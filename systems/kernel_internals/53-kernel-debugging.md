# Ch 53 — Kernel debug：printk/ftrace/KASAN/kgdb/oops

> **目標**：把前面 52 章零散用到的除錯手段收攏成一套方法論。學完你面對一個真實的 kernel bug，能從 `dmesg` 讀懂 oops、用 ftrace 縮小範圍、開 KASAN 抓記憶體錯誤、必要時用 kgdb 停在真實硬體上——並且知道每一步該用哪個工具、為什麼。

> **定位**：這是全課最後一個知識章。前面每一章都在「讀源碼 + 用 gdb 觀測」，這章把觀測工具本身講透。它明確接上本 repo 的 `observability_tools`（strace/ftrace/perf 的使用者視角）和 `gdb` 課（DWARF/backtrace/Python API），也回收 `kernel_pwn`（KASAN 抓的正是你在打的 UAF）。

## 為什麼需要這個？

到目前為止你都在**可控環境**除錯：QEMU 裡跑一顆你自己 build、關了 KASLR、開了 gdb stub 的 kernel（Ch 0）。你設中斷點、單步、印變數，一切盡在掌握。

但真實世界的 kernel bug 不長這樣。它們是：

- **偶發**：跑三天才 panic 一次，你根本沒機會在它崩之前設好中斷點
- **在生產機上**：你不能 `-S` 凍住一台正在服務的機器，也不能單步一個處理中斷的路徑
- **在別人的硬體上**：MTK 板子跑到一半當掉，你手邊只有一條串口線和一張噴出來的 oops 訊息
- **時序相關**：race condition 一加中斷點（改變時序）就不重現了——所謂的 heisenbug

gdb 單步在這些場景幾乎沒用。你需要的是**低干擾、事後可分析、能常駐在生產 kernel 裡**的工具：

- `printk` / dynamic debug——最原始但永遠可用的「印出來看」
- `ftrace`——kernel 內建、幾乎零成本的函式級追蹤，開了照樣上線
- **oops / panic 判讀**——崩潰現場的第一手證據，讀懂它往往就破案一半
- **KASAN / KCSAN / lockdep**——編譯期插樁的自動偵測器，把「偶發、難重現」的錯誤變成「一觸發就明確報告」
- `kgdb`——真的需要停下來看時，在實體硬體上用 gdb（不只 QEMU）

這章把這些串成一套**方法論**：拿到一個 bug，先看什麼、再開什麼、最後才動用最重的手段。

## 先建立直覺：除錯工具的「干擾 vs 資訊」光譜

不同工具在「對系統的干擾」和「拿到的資訊量」上各據一端。選工具的本質是選這個 trade-off：

```
  干擾小 ├──────────────────────────────────────────────────┤ 干擾大
  資訊少 │                                                    │ 資訊多
         │                                                    │
    dmesg/oops        ftrace          KASAN/lockdep      kgdb 單步
    （已經崩了，     （常駐可上線，   （重編 kernel，    （凍住整機，
      讀現場即可）    每函式 ns 級）    2~3x 記憶體/       看任意記憶體，
                                       慢，但精準定位）    但改變時序）
         │                                                    │
    「先看這個」──────────────逐步升級────────────────►「最後才用這個」
```

方法論的核心就是**從左往右升級**：能靠讀 oops 破案就不開 ftrace，能靠 ftrace 縮小範圍就不重編 KASAN kernel，能靠 KASAN 定位就不必動用 kgdb 停機。每往右一步，你付出的干擾與代價都陡增，但只有真的需要時才付。

下面逐一拆解，最後回到方法論把它們串起來。

## printk：kernel 的 printf，和它的地雷

`printk` 是 kernel 版的 `printf`，源碼在 `kernel/printk/printk.c`。你在 Ch 0 已經用過它的包裝 `pr_info`。它看起來平淡，但有幾個你必須理解的設計。

### log level 與 pr_* 家族

每則訊息帶一個 **log level**（0 最緊急、7 最囉嗦），定義在 `include/linux/kern_levels.h`：

```c
pr_emerg("...")   // KERN_EMERG   0  系統要掛了
pr_alert("...")   // KERN_ALERT   1
pr_crit("...")    // KERN_CRIT    2
pr_err("...")     // KERN_ERR     3  錯誤
pr_warn("...")    // KERN_WARNING 4
pr_notice("...")  // KERN_NOTICE  5
pr_info("...")    // KERN_INFO    6  一般資訊
pr_debug("...")   // KERN_DEBUG   7  除錯（預設不編進去，見下）
```

`/proc/sys/kernel/printk` 有四個數字（current / default / minimum / boot），第一個是 **console log level**：只有 level 數值 **小於**它的訊息才會即時印到 console。這解釋了一個常見困惑：你的 `pr_info` 在 `dmesg` 看得到、卻沒印在螢幕上——因為 console level 預設可能是 4，`pr_info`（6）不夠緊急，被留在 ring buffer 裡等你 `dmesg` 撈。

```bash
echo 8 > /proc/sys/kernel/printk   # 把所有訊息都吐到 console（除錯時常用）
```

### ring buffer 與 dmesg 的關係

`printk` 不直接寫檔案（它可能在中斷裡跑，不能睡、不能碰檔案系統）。它把訊息寫進一塊**環狀緩衝區（ring buffer）**——6.x 的實作是 `kernel/printk/printk_ringbuffer.c` 的無鎖 ringbuffer，設計成即使在 NMI 或崩潰路徑也能安全寫入。

`dmesg`（你在 `linux_commands` 課用過）做的事就是讀這塊 buffer：它透過 `/dev/kmsg` 或 `syslog(2)` syscall 把 ring buffer 的內容 dump 出來。buffer 是環狀的，滿了會覆蓋最舊的——所以開機很久後 `dmesg` 可能已經看不到開機早期訊息，除非你調大 `CONFIG_LOG_BUF_SHIFT`。

```
  printk("foo")                      dmesg / journalctl -k
       │                                    ▲
       ▼                                    │ 讀
  ┌──────────────────────────────────────────────┐
  │  printk ring buffer（環狀，滿了覆蓋最舊）      │
  │  [msg][msg][msg]...........[msg][msg]          │
  └──────┬───────────────────────────────────────┘
         │ level < console_loglevel 的才即時輸出
         ▼
   console（ttyS0 / VGA / netconsole …）
```

### 為什麼 printk 在某些 context 要小心

`printk` 號稱「哪裡都能呼叫」，但有陷阱：

- **early boot**：console 還沒註冊時，訊息只進 ring buffer，`earlyprintk=` / `earlycon` 開機參數可以讓極早期訊息也能輸出——debug `start_kernel`（Ch 3）之前的程式碼時會用到
- **中斷 / NMI context**：能呼叫，但大量 `printk` 會拖慢中斷處理，甚至造成 timing 改變讓 bug 消失
- **鎖內 / scheduler 內部**：`printk` 本身要拿內部鎖。歷史上「在 scheduler runqueue 鎖內 printk」造成過遞迴死鎖；6.x 引入的 **printk kthread / deferred printk（`printk_deferred`）** 就是為了讓危險 context 裡的訊息延後真正輸出，避免這類問題
- **它會改變時序**：一個 race bug，你灑幾行 `printk` 進去，可能就因為 I/O 延遲把時序推開，bug 不重現了——這是 heisenbug 的經典成因

### dynamic debug：pr_debug 的動態開關

`pr_debug` 預設**不編進 binary**（除非該檔開了 `DEBUG` 或全 kernel 開 `CONFIG_DYNAMIC_DEBUG`）。開了 `CONFIG_DYNAMIC_DEBUG` 後，每一條 `pr_debug` / `dev_dbg` 都變成一個**可在執行期單獨開關**的點，透過 `/proc/dynamic_debug/control` 控制：

```bash
# 只打開 mm/slub.c 裡所有 pr_debug
echo 'file mm/slub.c +p' > /proc/dynamic_debug/control
# 打開某個函式的
echo 'func kmem_cache_alloc +p' > /proc/dynamic_debug/control
# 關掉
echo 'file mm/slub.c -p' > /proc/dynamic_debug/control
```

這是生產環境除錯的利器：你不用重編 kernel、不用重開機，就能對著某個子系統臨時打開它作者埋好的 debug 訊息。相比之下 `pr_info` 是硬編進去、永遠會印，`pr_debug` + dynamic debug 是「平常閉嘴、需要時才出聲」。

## ftrace：kernel 內建的追蹤框架

`ftrace`（function tracer）是 kernel **內建**的追蹤基礎設施，源碼在 `kernel/trace/`。它和 Ch 51 的 kprobe/tracepoint、以及你在 `bpf` 和 `observability_tools` 課用過的 `perf` / `trace-cmd` 是同一套底層的不同前端。

它的殺手鐗是**幾乎零成本**：編譯器用 `-pg`（`CONFIG_FUNCTION_TRACER`）在每個 kernel 函式入口插一個 `__fentry__` 呼叫，平時被 patch 成 NOP（透過 `ftrace` 的 dynamic patching，見 `kernel/trace/ftrace.c`），開啟追蹤時才動態 patch 回真正的 trace 呼叫。沒開時的成本就是幾個 NOP，所以它能常駐在生產 kernel。

### tracefs 介面

介面是 `tracefs`，掛在 `/sys/kernel/tracing`（舊路徑 `/sys/kernel/debug/tracing`）。全部用讀寫檔案操作，不需要任何工具：

```bash
cd /sys/kernel/tracing
cat available_tracers      # 這顆 kernel 支援哪些 tracer
# function function_graph nop wakeup wakeup_rt irqsoff ...

echo function > current_tracer     # 選 function tracer
echo 1 > tracing_on                # 開始追
cat trace                          # 看結果
echo 0 > tracing_on                # 停
echo nop > current_tracer          # 收工
```

### 幾個關鍵 tracer

**function tracer**：印出每個被呼叫的 kernel 函式。全開會爆量，通常配 filter：

```bash
echo '*vfs_read*' > set_ftrace_filter   # 只追函式名含 vfs_read 的
```

**function_graph**：這是讀源碼路徑的神器。它畫出**呼叫圖 + 每個函式的耗時**，縮排表示呼叫深度。拿它追 Ch 34 的 `read()` 路徑一目了然：

```bash
echo function_graph > current_tracer
echo vfs_read > set_graph_function
echo 1 > tracing_on
# 在另一個 shell 觸發一次 read
cat trace
```

輸出長這樣（縮排 = 呼叫深度，DURATION = 耗時）：

```
 DURATION       FUNCTION CALLS
 --------       -------- | | | |
              |  vfs_read() {
              |    rw_verify_area() {
   0.213 us   |      security_file_permission();
              |    }
              |    ext4_file_read_iter() {
              |      generic_file_read_iter() {
 + 12.418 us  |        filemap_read();          ← 這裡花了 12us
              |      }
              |    }
   0.104 us   |    fsnotify();
 + 15.882 us  |  }                              ← vfs_read 總耗時
```

你在 Ch 34 讀懂的那條 read 路徑，這裡直接跑給你看，還附時間——哪個環節慢一眼就知道。

**事件 tracer（tracepoint）**：`events/` 目錄下是所有靜態 tracepoint（Ch 51），一樣是開關檔案：

```bash
echo 1 > events/sched/sched_switch/enable   # 追每次 context switch（Ch 14）
```

**irqsoff / wakeup**：延遲類 tracer。`irqsoff` 記錄「中斷關閉最久」的路徑（找 latency 元凶）；`wakeup` 追排程延遲。這些是 `-rt`（Ch 31）調校的主力。

### 前端工具

裸操作 tracefs 很原始。實務上用前端：

- **`trace-cmd`**：ftrace 的官方 CLI 前端，`trace-cmd record -p function_graph -g vfs_read` 一行搞定，還能存成檔給 `kernelshark` 圖形化看
- **`perf`**：`perf ftrace`、`perf trace`——你在 `observability_tools` 課用過的那個 perf，底層很多也是走 ftrace / tracepoint
- **bpftrace**：`bpf` 課的主角，用高階語言寫追蹤，底層掛在同一批 tracepoint/kprobe 上

ftrace 是「地板」，這些是「舒適的前端」。理解 ftrace 讓你在只有一台裸機、沒裝任何工具時，光靠 `echo` 進 tracefs 就能追蹤。

## oops / panic 判讀：崩潰現場的第一手證據

這是全章最該練熟的技能。kernel 崩潰時會噴一大段訊息，讀懂它往往直接破案。

### oops vs panic

- **oops**：kernel 遇到一個**它認為可恢復**的錯誤（最常見：解參考一個非法指標，例如 NULL）。處理方式是**殺掉出錯的那個行程 / context**，印出診斷訊息，然後**盡量繼續跑**。但機器此後狀態可疑（可能有鎖沒放、記憶體洩漏），所以 oops 後通常建議重開。
- **panic**：kernel 判定**無法繼續**（例如 oops 發生在中斷 context、或 init 行程掛了、或 `panic_on_oops=1`）。它印訊息後**整機停住**（或按 `panic` 參數重開）。

相關源碼：oops 走 `arch/x86/kernel/dumpstack.c` 的 `oops_begin/oops_end`、`kernel/panic.c` 的 `panic()`。

### 逐段拆解一個真實 oops

假設我們 `insmod` 一個會解參考 NULL 的模組。dmesg 噴出：

```
[  92.147] BUG: kernel NULL pointer dereference, address: 0000000000000000
[  92.147] #PF: supervisor read access in kernel mode
[  92.147] #PF: error_code(0x0000) - not-present page
[  92.147] PGD 0 P4D 0
[  92.147] Oops: 0000 [#1] PREEMPT SMP NOPTI
[  92.147] CPU: 2 PID: 431 Comm: insmod Tainted: G           O    6.12.0 #1
[  92.147] Hardware name: QEMU Standard PC ...
[  92.147] RIP: 0010:oops_init+0x11/0x30 [oops_demo]
[  92.147] Code: 0f 1f 44 00 00 48 c7 c7 ... <8b> 00 5d c3 ...
[  92.147] RSP: 0018:ffffc900012abd90 EFLAGS: 00010246
[  92.147] RAX: 0000000000000000 RBX: ffffffffc0512000 ...
[  92.147] CR2: 0000000000000000 CR3: 0000000104e3a000 ...
[  92.147] Call Trace:
[  92.147]  <TASK>
[  92.147]  do_one_initcall+0x44/0x200
[  92.147]  do_init_module+0x60/0x250
[  92.147]  __do_sys_finit_module+0xb4/0x120
[  92.147]  do_syscall_64+0x5c/0x90
[  92.147]  entry_SYSCALL_64_after_hwframe+0x77/0x7f
[  92.147]  </TASK>
```

一欄一欄看它在說什麼：

```
┌─────────────────────────────────────────────────────────────────────────┐
│ BUG: kernel NULL pointer dereference, address: 0x0000...0000              │
│   └─ 錯誤性質 + 出事的位址。address=0 → 幾乎確定是解參考 NULL             │
│                                                                           │
│ #PF: supervisor read access ... not-present page                         │
│   └─ 這是一次 page fault：在 kernel 態（supervisor）讀一個沒對映的頁      │
│                                                                           │
│ Oops: 0000 [#1]                                                           │
│   └─ error_code=0000。bit0=0 頁不存在、bit1=0 是讀取、bit2=0 kernel 態    │
│      [#1] = 第 1 次 oops（多次崩潰會累加，只信第一次）                    │
│                                                                           │
│ Tainted: G           O                                                    │
│   └─ tainted flags：O = 載入了 out-of-tree 模組（就是我們的）。          │
│      一堆字母各代表汙染來源；有 O/P 時 upstream 通常不收你的 bug report   │
│                                                                           │
│ RIP: 0010:oops_init+0x11/0x30 [oops_demo]   ★最關鍵一行★                 │
│   └─ 出錯的「指令位址」= oops_init 函式 +0x11 處，函式全長 0x30，         │
│      來自 [oops_demo] 模組。要定位到源碼行就靠這行                        │
│                                                                           │
│ RAX: 0000...0000                                                          │
│   └─ 暫存器快照。RAX=0 呼應了「解參考 NULL」——某個值是 0                 │
│                                                                           │
│ CR2: 0000...0000                                                          │
│   └─ x86 的 page fault 位址暫存器，存的就是出錯的線性位址（=0）          │
│                                                                           │
│ Call Trace:  <TASK> ... </TASK>          ★第二關鍵★                      │
│   └─ backtrace，最上面是最近的呼叫。從下往上讀是「怎麼一路呼叫到出事點」： │
│      syscall 進入 → finit_module → do_init_module → do_one_initcall      │
│      → 我們的 oops_init。這正是 Ch 8 模組載入呼叫 initcall 的路徑         │
└─────────────────────────────────────────────────────────────────────────┘
```

讀 oops 的**兩個聚焦點**：**RIP**（出事在哪條指令）和 **Call Trace**（怎麼走到那裡）。其餘暫存器/錯誤碼是佐證。

> Call Trace 準不準取決於能不能正確回溯堆疊。x86_64 現在主要靠 **ORC unwinder**（`CONFIG_UNWINDER_ORC`，比 frame pointer 省成本又準）；Ch 0 我們開 `FRAME_POINTER` 是為了 gdb 方便，兩套機制目的相同——都是為了讓 backtrace 不斷掉。這回收了 Ch 0 提到的 frame pointer。

### 把位址翻回源碼行

RIP 給的是「函式+偏移」，要翻成源碼行號有幾種方式：

```bash
# 1) faddr2line：專為 kernel oops 設計，直接吃「函式+偏移」
scripts/faddr2line vmlinux oops_init+0x11/0x30
# oops_init+0x11/0x30:
# oops_init at .../oops_demo.c:12

# 2) addr2line：吃絕對位址（要先算出實際位址，模組還得加載入基底）
addr2line -e vmlinux -f 0xffffffff81abc123

# 3) gdb：最直覺
gdb vmlinux -batch -ex 'list *(oops_init+0x11)'

# 4) decode_stacktrace.sh：把整段 Call Trace 貼給它，一次全翻成源碼行
scripts/decode_stacktrace.sh vmlinux < oops.txt
```

`scripts/decode_stacktrace.sh` 是最省事的：把整段 dmesg oops 餵進去，它把每一行 `func+0x..` 都替換成 `func (file:line)`。對付模組要多給模組的 `.ko` 路徑。

### BUG() / WARN_ON

kernel 主動製造診斷的兩個巨集：

- `BUG()` / `BUG_ON(cond)`：觸發一個**刻意的 oops**（`include/asm-generic/bug.h`）。「這裡絕不該發生」的地方用它，一旦踩到直接崩+印 backtrace
- `WARN()` / `WARN_ON(cond)`：印一段**類似 oops 的 backtrace 但不殺行程**，繼續跑。用於「不該發生但還能撐」的情況。生產 kernel 裡看到 `WARN_ON` 噴 backtrace 是常態，它就是開發者埋的「這裡怪怪的，記一筆」

`WARN_ON` 的輸出和 oops 幾乎一樣（有 RIP、Call Trace），讀法完全相同——所以練熟讀 oops，讀 WARN 也一起會了。

## KASAN：抓記憶體錯誤的插樁器

**KASAN（Kernel Address Sanitizer）** 是抓記憶體錯誤——use-after-free（UAF）、越界（out-of-bounds）——的動態偵測器，源碼在 `mm/kasan/`。你在 `kernel_pwn` 課打的那些 slub UAF、在練習 A/D 可能不小心寫出的越界，KASAN 都能當場抓住並印出精確報告。

開啟方式是重編 kernel：`CONFIG_KASAN=y`（generic 模式）。代價是記憶體用量約 2~3 倍、速度慢一截——所以它是**除錯 build 專用**，不上生產。

### 底層機制：shadow memory

KASAN 的核心是 **shadow memory（影子記憶體）**：kernel 每 **8 bytes** 的真實記憶體，對應 **1 byte** 的 shadow，記錄那 8 bytes「有幾個 byte 可以合法存取」。

```
   真實記憶體（每 8 bytes 一組）        shadow byte 的值
   ┌──────────────┐
   │ 8 bytes 全可存取                →   0x00
   │ 前 N bytes 可存取（0<N<8）      →   N     （部分可用，尾端不可）
   │ 完全不可存取（已 free / redzone）→   負值  （0xfa=heap redzone,
   │                                              0xfb=freed, ...）
   └──────────────┘

   shadow 位址 = (真實位址 >> 3) + KASAN_SHADOW_OFFSET
```

每 8 bytes 壓成 1 byte，所以 shadow 只佔真實記憶體的 **1/8**（這就是那 ~12% 的記憶體開銷來源，加上 redzone/quarantine 才到 2~3x）。

編譯器（`-fsanitize=kernel-address`）在**每一個記憶體存取前**自動插一段檢查：算出對應的 shadow byte，看這次存取合不合法，不合法就報告。`kmalloc` 在配置的 buffer 前後放 **redzone**（shadow 標成不可存取），`kfree` 後把該區 shadow 標成 `0xfb`（freed）並丟進 **quarantine**（延後真正釋放），這樣 free 後又去存取（UAF）時 shadow 還是 freed 狀態，當場抓到。

```
  kmalloc(16) 拿到一塊：
  ┌────────┬──────────────────┬────────┐
  │redzone │  16 bytes 可用    │redzone │
  │ 0xfa   │  0x00 0x00        │ 0xfa   │   ← 對應的 shadow
  └────────┴──────────────────┴────────┘
        ↑ 越界寫到這 → shadow=0xfa → KASAN 報 out-of-bounds

  kfree 之後，整塊 shadow 變 0xfb：
  ┌───────────────────────────────────┐
  │  0xfb 0xfb 0xfb ...                │
  └───────────────────────────────────┘
        ↑ 再去讀寫 → shadow=0xfb → KASAN 報 use-after-free
```

### KASAN 報告怎麼讀

一個 UAF 報告長這樣（節錄關鍵段）：

```
==================================================================
BUG: KASAN: use-after-free in kasan_demo_read+0x89/0xb0 [kasan_demo]
Read of size 4 at addr ffff888104a3f000 by task cat/512      ← 誰、讀幾 byte、哪個位址

Call Trace:                                                   ← 出事當下的 backtrace
 kasan_demo_read+0x89/0xb0 [kasan_demo]
 ...

Allocated by task 511:                                        ← 這塊記憶體「當初在哪配的」
 kmalloc_trace+0x...
 kasan_demo_write+0x...  [kasan_demo]

Freed by task 511:                                            ← 「在哪被 free 的」
 kfree+0x...
 kasan_demo_write+0x...  [kasan_demo]
==================================================================
```

KASAN 報告的資訊密度遠超 oops，它一次給你三件事：**出事點**（讀/寫、位址、backtrace）、**alloc stack**（這塊記憶體哪來的）、**free stack**（誰放掉的）。有了 alloc/free 兩條 stack，UAF 這種「A 放掉、B 還在用」的 bug 幾乎是直接把兇手指給你看——這是 KASAN 比 gdb 單步強太多的地方：gdb 你得先猜到在哪停，KASAN 是踩到就報。

### KFENCE：低開銷抽樣版

KASAN 太重不能上生產。**KFENCE（Kernel Electric-Fence，`mm/kfence/`）** 是它的生產版折衷：用 guard page 保護**抽樣**的一小部分配置（預設每隔一段時間才保護一個物件），開銷低到可以常駐生產 kernel。代價是覆蓋率低——只有剛好落在被保護物件上的錯誤才抓得到。生產機上長期跑 KFENCE，靠海量機器 × 時間把偶發的記憶體錯誤慢慢撈出來，是 Google 等大規模部署的實務做法。

## 其他 sanitizer 與 debug 設施

KASAN 是記憶體錯誤，其他維度各有專屬偵測器，全部是「編譯期插樁 + 執行期報告」的同一套思路：

| 設施 | config | 抓什麼 | 對應章 |
|---|---|---|---|
| **UBSAN** | `CONFIG_UBSAN` | undefined behavior：整數溢位、移位越界、對齊錯誤、陣列越界 | 通用 |
| **KCSAN** | `CONFIG_KCSAN` | **data race**：兩個 thread 無同步地存取同一位址 | Ch 24-28 並發 |
| **kmemleak** | `CONFIG_DEBUG_KMEMLEAK` | 記憶體洩漏：配了沒 free 又沒人指向的物件 | Ch 6 配置 API |
| **lockdep** | `CONFIG_PROVE_LOCKING` | **鎖順序反轉 / 潛在死鎖**（不用真的死鎖就能預警） | Ch 28 |

幾個要點：

- **KCSAN** 補了 KASAN 的盲區：KASAN 抓「存取了不該存取的記憶體」，KCSAN 抓「存取本身合法但沒做同步」。它是你在 Ch 24-28 學的 memory ordering 出錯時的自動偵測器。`kmemleak` 用 `echo scan > /proc/sys/kernel/mm/kmemleak` 觸發掃描後讀報告。
- **lockdep** 在 Ch 28 已深講，這裡把它歸位到 debug 工具箱：它厲害在**不用真的死鎖**——只要它觀察到你曾「A 鎖內拿 B」又在別處「B 鎖內拿 A」，就立刻報 possible deadlock。你在練習 D 復現 race 時，lockdep 開著能幫你在死鎖真正發生前就抓到鎖順序問題。

除錯時的常見組合是一次全開 `CONFIG_KASAN + CONFIG_UBSAN + CONFIG_PROVE_LOCKING + CONFIG_DEBUG_KMEMLEAK`，跑你的測試，讓四個偵測器同時盯著。慢是慢，但把大量「偶發、難重現」的錯誤變成「一觸發就明確報告」。

## kgdb / kdb：在真實硬體上用 gdb

Ch 0 的 gdb 靠的是 QEMU 內建的 GDB stub。真實硬體沒有這個——**kgdb** 就是 kernel **自己內建的 GDB stub**，讓你在實體板子上也能用 gdb 停 kernel、看記憶體、單步，透過**串口**（或網路）連線。這對 MTK 板子這種只有一條 UART 出來的嵌入式硬體特別關鍵。

- **kgdb**：後端 stub，配合 host 上的 gdb 使用（`target remote /dev/ttyUSB0`）
- **kdb**：一個**不用 host、直接在被 debug 機的 console 上**操作的簡易 debugger 前端（下 `bt`、`md` 看記憶體等），適合手邊只有一個 serial console、沒有第二台跑 gdb 的機器時

典型設定（`CONFIG_KGDB` + `CONFIG_KGDB_SERIAL_CONSOLE`）：

```bash
# 開機參數指定 kgdb 走哪個串口
kgdboc=ttyS0,115200 kgdbwait     # kgdbwait: 開機早期就停下等 gdb 連

# 執行中想進 debugger：觸發 sysrq
echo g > /proc/sysrq-trigger     # 進 kgdb（等 host gdb 連）
```

host 端：

```bash
gdb vmlinux
(gdb) set serial baud 115200
(gdb) target remote /dev/ttyUSB0   # 接實體板的串口，不是 tcp:1234
```

之後的操作和 Ch 0 一模一樣——`break`、`bt`、`print`、`step`。差別只在「傳輸線是實體 UART 而非 QEMU 的虛擬 TCP」。這正是 `gdb` 課的 remote debugging 概念落到 kernel 上：GDB 的 remote protocol 兩端，一端是 gdb，另一端這回是 kgdb stub。

> kgdb 會**凍住整台機器**——一停下，中斷、時鐘全停。所以它對 timing 敏感的 bug（race）幾乎沒用（一停時序就變了），但對「明確能重現、想看某個瞬間狀態」的 bug 很直接。這呼應開頭的干擾光譜：kgdb 在最右端，最後才動用。

## crash dump：崩了之後解剖屍體

有些 bug 崩得太徹底（panic、hang），你連 dmesg 都撈不完整。**kdump** 的做法是：**在崩潰的瞬間，開起第二顆 kernel**，用它把第一顆 kernel 崩潰當下的整個記憶體 dump 成一個檔（`vmcore`），事後慢慢解剖。

機制靠 **kexec**：正常開機時先用 `kexec` 把一顆備用的 **crash kernel** 載進一塊預留的記憶體區（`crashkernel=` 開機參數保留）。當主 kernel panic，它不重開機、而是**直接 `kexec` 跳進那顆預留的 crash kernel**。crash kernel 在一小塊隔離記憶體裡跑，能安全地把主 kernel 的記憶體（此刻已是「屍體」）寫成 `/proc/vmcore`，存檔。

```
  正常運行                panic!                 crash kernel
  ┌─────────┐            ┌─────────┐            ┌─────────┐
  │ 主 kernel│ ─panic──► │kexec 跳轉│ ─────────► │crash    │
  │         │            │到預留區  │            │kernel   │
  │預留區已  │            └─────────┘            │dump 主  │
  │載 crash  │                                   │kernel   │
  │kernel    │                                   │記憶體→  │
  └─────────┘                                    │vmcore   │
                                                 └─────────┘
```

事後用 **`crash`** 工具（配 `vmlinux` + `vmcore`）分析：它像一個「對著記憶體快照的 gdb」，能 `bt` 看崩潰時的 backtrace、`ps` 看當時所有行程、`log` 撈完整 dmesg、走 task_struct（Ch 9）看每個 thread 卡在哪。生產環境 kernel panic 後的標準流程就是：kdump 存下 vmcore → `crash` 開屍檢 → 定位。

## 除錯方法論：把工具串成流程

工具講完了，最後把它們串成一套**面對 bug 的系統化流程**。這是本章、也是全課的收尾。

```
  遇到一個 kernel bug
        │
   ┌────▼─────────────────────────────────────────────────┐
   │ 1. 讀現場：dmesg / oops / WARN                         │
   │    - 有 oops？→ 讀 RIP + Call Trace，faddr2line 定位   │
   │    - 有 WARN？→ 一樣讀 backtrace                        │
   │    - 記下 tainted flags（是不是你的 out-of-tree 模組？）│
   └────┬───────────────────────────────────────────────────┘
        │ 光讀現場常能破案一半
   ┌────▼─────────────────────────────────────────────────┐
   │ 2. 能重現嗎？                                          │
   │    - 能穩定重現 → 好辦，往下走                          │
   │    - 偶發 → 開 KASAN/KCSAN/lockdep 常駐，靠它把偶發    │
   │      變成「一觸發就報」                                 │
   └────┬───────────────────────────────────────────────────┘
   ┌────▼─────────────────────────────────────────────────┐
   │ 3. 這是新出現的 bug 嗎？→ git bisect（二分法）         │
   │    在「好」和「壞」的 commit 間二分，找出是哪個 commit │
   │    引入的。往往看那個 commit 就懂了                     │
   └────┬───────────────────────────────────────────────────┘
   ┌────▼─────────────────────────────────────────────────┐
   │ 4. 縮小範圍：printk / dynamic debug / ftrace          │
   │    - 灑 pr_debug + dynamic debug 動態開關              │
   │    - function_graph 追可疑路徑，看它到底走到哪、哪裡岔 │
   └────┬───────────────────────────────────────────────────┘
   ┌────▼─────────────────────────────────────────────────┐
   │ 5. 對症下猛藥                                          │
   │    - 記憶體錯誤 → KASAN 報告（alloc/free stack 指兇手）│
   │    - 死鎖 → lockdep                                    │
   │    - data race → KCSAN                                 │
   │    - 洩漏 → kmemleak                                   │
   └────┬───────────────────────────────────────────────────┘
   ┌────▼─────────────────────────────────────────────────┐
   │ 6. 還是找不到 → 停下來看：kgdb（實機）/ QEMU+gdb       │
   │    崩得太徹底 → kdump 存 vmcore，用 crash 屍檢          │
   └────────────────────────────────────────────────────────┘
```

核心原則不變：**從干擾小的往干擾大的升級，能在上一步破案就不進下一步**。這 53 章你學的每個子系統，出 bug 時都套這套流程；差別只在「第 1 步讀 oops 時你認得那條 Call Trace 是排程器 / mm / VFS 的哪條路徑」——那正是前面 52 章給你的底氣。

## 動手：把工具全跑一遍

### 1. 寫一個會 oops 的模組，讀 Call Trace 並定位

```c
// oops_demo.c
#include <linux/module.h>
static int __init oops_init(void)
{
    int *p = NULL;
    pr_info("oops_demo: about to dereference NULL\n");
    return *p;              // 解參考 NULL → oops
}
module_init(oops_init);
MODULE_LICENSE("GPL");
```

在 QEMU 裡 `insmod oops_demo.ko`，看 dmesg 噴 oops。抄下 `RIP: oops_init+0x??/0x??`，回 host：

```bash
scripts/faddr2line oops_demo.ko oops_init+0x11/0x30
# 或整段 Call Trace 一起翻
scripts/decode_stacktrace.sh vmlinux ./ < oops.txt
```

確認它指回 `oops_demo.c` 那一行 `return *p;`。這就是真實 bug 定位的完整流程走一遍。

### 2. 寫一個 UAF 模組，看 KASAN 報告

需要一顆 `CONFIG_KASAN=y` 的 kernel（重編）。

```c
// kasan_demo.c
#include <linux/module.h>
#include <linux/slab.h>
static int __init uaf_init(void)
{
    char *buf = kmalloc(16, GFP_KERNEL);
    kfree(buf);
    buf[0] = 'x';          // use-after-free
    return 0;
}
module_init(uaf_init);
MODULE_LICENSE("GPL");
```

`insmod` 後 dmesg 會噴 KASAN 報告。對照上面「KASAN 報告怎麼讀」那節，找出 **Freed by task** 和出事點——確認 KASAN 直接把 free 的位置指給你。

### 3. 用 ftrace function_graph 追一個 syscall

```bash
cd /sys/kernel/tracing
echo function_graph > current_tracer
echo vfs_read > set_graph_function
echo 1 > tracing_on
cat /etc/hostname       # 觸發一次 read
echo 0 > tracing_on
cat trace | head -40
```

對照 Ch 34 你讀過的 read 路徑，看它跑出來的呼叫圖對不對得上，順便看每一層的耗時。

### 4. 開 dynamic debug 打開某個子系統的 pr_debug

```bash
echo 'file mm/page_alloc.c +p' > /proc/dynamic_debug/control
# 觸發一些記憶體配置，dmesg 看 buddy allocator（Ch 17）作者埋的 debug 訊息
echo 'file mm/page_alloc.c -p' > /proc/dynamic_debug/control
```

## 對比與取捨

| 工具 | 干擾 | 能否上生產 | 最適合 | 罩門 |
|---|---|---|---|---|
| printk / dynamic debug | 低（但改時序） | 可 | 快速定位、看流程 | 灑太多改變 timing、爆量 |
| ftrace function_graph | 極低 | 可 | 追呼叫路徑 + 耗時 | 資料量大、要會 filter |
| oops / WARN 判讀 | 零（事後） | — | 崩潰第一手證據 | 只有崩了才有 |
| KASAN | 高（2~3x 記憶體、慢） | 否 | 記憶體錯誤（UAF/越界） | 要重編、不能上線 |
| KFENCE | 低（抽樣） | 可 | 生產撈偶發記憶體錯誤 | 覆蓋率低 |
| lockdep / KCSAN | 中高 | 否 | 死鎖 / data race | 要重編、有誤報要判讀 |
| kgdb / QEMU+gdb | 最高（凍住整機） | 否（實驗機） | 要停下看瞬間狀態 | race 一停就變、不能生產 |
| kdump + crash | 事後 | 可（預留記憶體） | panic 屍檢 | 要預留記憶體、設定較繁 |

## 踩雷集錦

1. **「pr_info 在 dmesg 有、螢幕沒有 = 壞了」——錯**。那是 console log level 的正常行為：`pr_info`（6）不夠緊急，沒達到 console level。要即時看螢幕就 `echo 8 > /proc/sys/kernel/printk`。dmesg 撈得到就代表 printk 沒問題。

2. **只信最後一次 oops——錯，要信第一次 `[#1]`**。第一次 oops 後 kernel 狀態已可疑，後續的 `[#2]`、`[#3]` 常是連鎖反應的雜訊。永遠從 `[#1]` 那次的 RIP 和 Call Trace 下手。

3. **拿 `pr_debug` 除錯卻什麼都沒印**——它預設沒編進去。要嘛該檔開 `#define DEBUG`，要嘛全 kernel 開 `CONFIG_DYNAMIC_DEBUG` 再用 `/proc/dynamic_debug/control` 打開。不是你的 `pr_debug` 沒被執行到。

4. **忽略 tainted flags 就發 bug report**——如果 `Tainted:` 有 `O`（out-of-tree 模組）或 `P`（proprietary），十之八九是你自己或某個第三方模組的問題，不是 upstream kernel 的 bug。先排除你的模組，別浪費維護者時間。

5. **用 KASAN kernel 跑 benchmark 下效能結論**——KASAN 每個記憶體存取都插樁，慢 2~3 倍是設計如此。它是除錯 build，任何效能數字都不作數。要測效能請關掉所有 sanitizer。

6. **race bug 一上 kgdb / printk 就不見了**——這是 heisenbug：你的觀測手段改變了時序。這種 bug 該用**不改變時序**的工具：KCSAN（編譯期插樁、報告事後看）或 lockdep，而不是會停機/加 I/O 延遲的 kgdb/printk。

## 進階：再往深一層

- **ORC vs frame pointer unwinder**：x86_64 現在預設 `CONFIG_UNWINDER_ORC`，它用一張獨立的 unwind table（類似 DWARF CFI 的精簡版）回溯堆疊，比 frame pointer 準又不佔一個暫存器。Ch 0 我們開 `FRAME_POINTER` 主要為 gdb 舒服；ORC 是 kernel 自己產 Call Trace 用的。
- **printk 的 `%pS` / `%pB`**：`printk("%pS", ptr)` 會把一個指標印成「符號名+偏移」，`%pB` 專門印 backtrace 位址（會做 -1 修正指回 call 指令）。在你自己模組裡印 backtrace 很好用。
- **`dump_stack()`**：在任何你想看「我現在是怎麼被呼叫到的」的地方插一行 `dump_stack()`，它就地印一段 Call Trace，不用崩潰。除錯路徑問題比 printk 更直接。
- **面試常問**：「kernel panic 了你怎麼查？」——標準答案就是上面那套方法論：先 dmesg/oops 讀 RIP+Call Trace，faddr2line 定位，能重現就 KASAN/lockdep，偶發就 KFENCE/bisect，最後 kdump+crash。能把工具按「干擾光譜」排出優先序，是這題的加分點。
- **`perf` 與 ftrace 的關係**：`observability_tools` 課你用 `perf` 很多，這裡點破：`perf` 的很多功能（`perf trace`、`perf ftrace`、tracepoint 事件）底層走的就是本章的 ftrace / tracepoint 設施，只是 perf 提供了取樣、彙總、火焰圖等更高階的呈現。

## 動手練習

1. **完整走一次 oops 定位**：跑上面的 `oops_demo`，用 `scripts/decode_stacktrace.sh` 把整段 Call Trace 翻成源碼行，確認每一層（`do_one_initcall` → `do_init_module` → …）都對得上 Ch 8 的模組載入路徑。
2. **讀懂一份 KASAN 報告的三段 stack**：跑 `kasan_demo`，在報告裡分別圈出「出事點」「Allocated by」「Freed by」三段，用一句話說明每段告訴你什麼。
3. **function_graph 追排程**：`echo function_graph > current_tracer`、`echo __schedule > set_graph_function`，觸發一次 context switch，看 Ch 14 的 `__schedule` 呼叫圖跑出來。
4. **lockdep 抓鎖順序**（進階）：開 `CONFIG_PROVE_LOCKING`，寫一個模組故意在兩個地方以相反順序拿兩把 spinlock，看 lockdep 在**還沒真的死鎖**時就報 possible circular locking。這直接接練習 D。
5. **git bisect 演練**：找 kernel 一個已知修過的 bug（或自己在源碼裡種一個），在兩個 commit 間 `git bisect` 走一遍，體會二分法定位。

## 本章重點整理

- 除錯工具是一條「干擾 vs 資訊」的光譜：從 dmesg/oops（零干擾）→ ftrace（極低）→ KASAN/lockdep（重編 kernel）→ kgdb（凍住整機）。方法論就是**從左往右升級，能早破案就不往右走**。
- 讀 oops 的兩個聚焦點是 **RIP**（出事指令）和 **Call Trace**（怎麼走到），用 `faddr2line` / `decode_stacktrace.sh` 翻回源碼行；先看 tainted flags 排除自己的模組，只信第一次 `[#1]`。
- KASAN 靠 **shadow memory**（每 8 bytes 一個 shadow byte）+ 編譯器插樁抓 UAF/越界，報告直接附 alloc/free 兩條 stack；生產環境用抽樣的 KFENCE。
- printk 有 log level 與 ring buffer，`pr_debug` + dynamic debug 能執行期動態開關；ftrace 內建、幾乎零成本，`function_graph` 追呼叫路徑 + 耗時是讀源碼路徑的神器。

## 自我檢核

- [ ] 不看筆記，能把除錯工具按「干擾程度」由小到大排出來，並說出各自最適合的場景
- [ ] 給你一段 oops，能指出哪一行是 RIP、哪一段是 Call Trace，並說明怎麼翻回源碼行
- [ ] 能解釋 KASAN 的 shadow memory 怎麼用 1/8 記憶體抓 UAF 與越界，以及為什麼它不能上生產
- [ ] 能說出 `pr_debug` 為什麼常常「什麼都沒印」，以及怎麼用 dynamic debug 打開它
- [ ] 面試被問「kernel panic 了你怎麼查」，能講出一套從 dmesg 到 kdump 的系統化流程
- [ ] 能區分 kgdb 與 QEMU+gdb 的差別，以及為什麼 race bug 不該用 kgdb 查

## 延伸閱讀

### 官方文件

- **[Documentation/admin-guide/bug-hunting.rst](https://www.kernel.org/doc/html/latest/admin-guide/bug-hunting.html)**
  - **讀哪裡**：整篇。這是 kernel 官方教你怎麼從一份 oops 一路查到源碼行的權威指南，`decode_stacktrace.sh`、`faddr2line` 的用法都在這
  - **和本章關聯**：本章「oops 判讀」那節就是這篇的展開版，遇到不熟的欄位回來查

- **[Documentation/dev-tools/kasan.rst](https://www.kernel.org/doc/html/latest/dev-tools/kasan.html)**
  - **讀哪裡**：Overview + Implementation details。講清楚 generic / SW-tags / HW-tags 三種 KASAN 模式與 shadow memory 佈局
  - **能學到什麼**：本章 shadow memory 的精確定義、各 shadow byte 值的意義

- **[Documentation/trace/ftrace.rst](https://www.kernel.org/doc/html/latest/trace/ftrace.html)**
  - **讀哪裡**：先讀 function / function_graph 兩節，其餘當手冊查
  - **前提**：跟過本章的 tracefs 動手；這篇是 tracefs 每個檔案的完整說明

### 部落格 / 工具

- **[Documentation/dev-tools/kgdb.rst](https://www.kernel.org/doc/html/latest/dev-tools/kgdb.html)** — kernel 官方
  - **讀哪裡**：kgdboc 設定與連線那幾節。想在實體板子（如 MTK 的 UART）上除錯 kernel，這是起點
  - **和 gdb 課的關聯**：把 `gdb` 課的 remote protocol 概念落到 kernel stub 上

- **[crash utility 官方文件](https://crash-utility.github.io/)** — Dave Anderson 等維護
  - **這是什麼**：分析 kdump `vmcore` 的標準工具，文件含常用指令（`bt`/`ps`/`log`/`struct`）
  - **為什麼值得讀**：生產 kernel panic 屍檢的實務主力，配 `vmlinux` + `vmcore` 使用

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 18 章 "Debugging"
  - **定位**：最好讀的 kernel debug 概念入門，講 printk、oops、`BUG_ON`、二分法的思路
  - **注意**：ftrace/KASAN 等較新工具書中著墨少，以本章與官方文件為準

到這裡，全課 53 章的知識線走完了：從 Ch 0 的環境，穿過排程、記憶體、並發、中斷、檔案系統、裝置驅動、網路、安全、追蹤，到這一章把除錯手段收攏成方法論。接下來的練習 F 就是驗收：給你一個**故意寫壞的模組**，你要動用本章的 ftrace、KASAN、和 gdb，把它的 bug 一個個抓出來——把前面所有章的底子，用在一場真實的除錯上。

→ [練習 F：buggy 模組除錯（ftrace + KASAN + gdb）](./practice-f-debug-buggy-module.md)
