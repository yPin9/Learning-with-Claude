# 練習 F — buggy 模組除錯（ftrace + KASAN + gdb）

> **這是 Part 10（Ch 51–53）結束後的整合練習，也是全課倒數第二個檔案。** 前面 53 章你一路在「讀源碼 + 用 gdb 觀測」，Ch 51 學了 kprobe/tracepoint 怎麼在不改源碼下掛觀測點、Ch 53 把 printk/ftrace/KASAN/oops/kgdb 收攏成一套**除錯方法論**（那條「干擾小 → 干擾大，能早破案就不往右走」的光譜）。這個練習把整套工具用在一場**模擬真實 on-call 的除錯**上：**你接手一個別人寫的、故意埋了四個 bug 的核心模組 `buggy`，只拿到四種症狀（載入就 oops、偶爾崩、直接掛住、行為不對），要用對的工具把每個 bug 的病因抓出來、定位到源碼行、理解根因、修好並驗證。** 這正是 kernel 工程師的日常——大部分時間不是寫新程式碼，是對著一份 oops / 一段卡死 / 一個「應該是 8 卻印出 40000」的怪現象，決定「先看什麼、再開什麼」。

## 背景與動機：on-call 拿到的不是源碼，是症狀

真實的 kernel bug 不會附一張「這裡寫錯了」的紙條。你半夜被叫起來，手上只有：

- 一份從串口噴出來的 **oops**（Ch 53），機器已經半死，你要從 `RIP` 和 `Call Trace` 反推出事點；
- 一句「這台機器跑一跑就當掉，也沒 oops，就是**卡住不動**」——連崩潰現場都沒有，你得自己想辦法讓它「說話」；
- 一個「功能是對的，但**某個操作慢到不可思議**，CPU 100%」的效能客訴，沒有任何錯誤訊息。

這時候「會寫 kernel 模組」幫不了你多少，真正決定你幾點能回去睡的是**除錯方法論**：面對一個症狀，能不能在腦中快速走過「這像哪一類 bug → 該用哪個工具 → 工具會給我什麼線索 → 線索指向源碼哪裡」。這個練習的四個 bug 是刻意挑的，每個對應一種**症狀類別**和一件**主武器**：

| Bug | 症狀 | 主武器 | 對應章 |
|---|---|---|---|
| **1 NULL deref** | `insmod` 立刻 oops，機器可能還活著 | 讀 oops 的 `RIP`+`Call Trace`，`faddr2line`/`decode_stacktrace.sh` 定位 | Ch 53 oops 判讀、Ch 8 initcall 路徑 |
| **2 use-after-free** | 平常跑得好好的，偶爾 oops / 資料亂掉，重現不穩 | 開 **KASAN** 重編，讀 alloc/free 兩條 stack | Ch 53 KASAN、Ch 6/18 slab、Ch 27 UAF |
| **3 死鎖 / sleep-in-atomic** | 一觸發某操作機器**整個卡住**，或 dmesg 噴 `BUG: scheduling while atomic` | **lockdep** / **DEBUG_ATOMIC_SLEEP** | Ch 28 lockdep、Ch 2 context、Ch 25 spinlock |
| **4 邏輯 / 效能 bug** | 沒崩、沒報錯，但行為不對 / 某函式被呼叫爆多次、慢 | **ftrace function_graph** / **kprobe** 追執行流程 | Ch 53 ftrace、Ch 51 kprobe |

注意這四個工具在 Ch 53 的「干擾光譜」上剛好從左排到右：讀 oops（零干擾、事後）→ ftrace（極低干擾、常駐）→ KASAN（重編 kernel）→ lockdep（重編 + 有時要停下看）。**這個練習就是逼你把光譜走一遍，每個 bug 都問自己「有沒有辦法用更左邊（更省事）的工具就破案」。**

還有一層心法：**「修好」只是一半，「證明修好了」是另一半。** 每個 bug 你都要能說出「原本壞在哪一行、為什麼壞、改成什麼、怎麼驗證同樣的操作不再觸發」。面試問「你怎麼 debug kernel」，能把這條「症狀 → 工具 → 定位 → 根因 → 修+驗」講清楚，比背十個 config 名字值錢。

## 先建立心智模型：一張「症狀 → 工具」的決策圖

動手前先把「拿到症狀，怎麼選工具」在腦中固化成一張圖。這張圖是 Ch 53 方法論的實戰版：

```
  拿到一個壞掉的模組（只知道症狀）
        │
   有 oops / WARN 噴出來嗎？
        │
   ┌────┴─────────────────────────────┐
   是                                  否
   │                                   │
   讀 RIP + Call Trace                機器「卡住」不動？
   faddr2line 定位到源碼行             │
   │                              ┌────┴──────────────┐
   看得懂根因？                    是                  否（沒崩、行為怪）
   ├── 是 → 修，收工                │                   │
   └── 否，或位址=0 很可疑          dmesg 有             ftrace function_graph
       且「偶爾才崩」                lockdep splat？      追可疑路徑，看它
       │                            或 sleeping-in-       走到哪、慢在哪、
   像記憶體錯誤（UAF/越界）→          atomic 報告？        被呼叫幾次
   開 KASAN 重編，讀 alloc/free      ├─ 有 → 直接讀      │
   兩條 stack，兇手直接指給你          └─ 沒有 → 開       kprobe 印進出參數
                                       PROVE_LOCKING /   確認假設
                                       DEBUG_ATOMIC_SLEEP
                                       重編，逼它報
```

三個要內化的判斷：

- **有 oops 先讀 oops，別急著開重工具。** 一份 NULL deref 的 oops，`RIP` 那行直接告訴你出事在哪個函式 +offset，`faddr2line` 一翻就是源碼行。這是零成本、事後可讀的第一手證據，能靠它破案就不必重編 KASAN kernel（Ch 53 光譜最左）。
- **「偶爾才崩、重現不穩」是記憶體錯誤的招牌氣味。** UAF / 越界之所以偶發，是因為那塊記憶體被 free 後**不一定馬上被別人拿走**——沒被拿走時你讀到的還是舊值，看起來正常；被拿走後才崩。這種「薛丁格式」的 bug 用 gdb 幾乎沒法抓（你不知道在哪停），但 KASAN 把「碰了 freed / redzone 記憶體」變成**踩到就報**，還附上「這塊誰配的、誰 free 的」兩條 stack（Ch 53）。
- **「卡住」和「行為怪」是兩種完全不同的路。** 卡住多半是**死鎖**（互相等鎖）或**在不該睡的地方睡了**（atomic context 裡呼叫會睡的函式，排程器一換就爆），lockdep / DEBUG_ATOMIC_SLEEP 專治這兩種。行為怪、沒崩、沒報錯，就沒有崩潰現場可讀了——你得主動讓程式碼「說話」，`ftrace function_graph` 畫出呼叫圖 + 耗時、`kprobe` 印出參數，看它到底走了哪條路、哪個函式被呼叫了不該有的次數。

## 任務規格

### 你拿到的東西

一個核心模組 `buggy.c`，它對外提供一個 `/proc/buggy` 介面（或 misc device，Ch 38），有四個功能，每個功能藏一個 bug。用 module param `bug`（1/2/3/4）選「這次要 build 哪一個 bug 版本」，方便你一次專心對付一個。你的交付物是：**四份除錯報告 + 四個修正版**。每份報告要回答五個問題（就是下面的除錯方法論五步）。

> 教學上把四個 bug 放進同一個模組、用 param 切換，方便你反覆玩。真實 on-call 你面對的是一個獨立模組一個 bug；但「一次專心修一個」的紀律是一樣的。

### 四個 bug 的「症狀」規格（你只該先知道這些）

**Bug 1（`bug=1`）：載入就死。** `insmod buggy.ko bug=1` 之後 `dmesg` 立刻噴一段 oops，`insmod` 行程被殺、回 non-zero。症狀關鍵字：`BUG: kernel NULL pointer dereference, address: 0000000000000000`。**你的任務**：讀懂這份 oops，指出 `RIP` 是哪個函式、`Call Trace` 怎麼一路呼叫進來、用 `faddr2line` 定位到 `buggy.c` 的哪一行，說明為什麼那行會解參考 NULL，修好讓 `insmod bug=1` 乾淨載入。

**Bug 2（`bug=2`）：偶爾崩、資料偶爾亂。** `insmod buggy.ko bug=2` 能載入，之後對 `/proc/buggy` 做讀寫，**大多數時候正常**，但反覆操作或壓力大時偶爾 oops、或讀出來的值不對。一般（沒開 KASAN 的）kernel 上這 bug 難抓——可能跑一百次才崩一次。**你的任務**：判斷這是記憶體錯誤，開 `CONFIG_KASAN` 重編 kernel，重跑觸發 KASAN 報告，讀懂報告的三段（出事點、`Allocated by`、`Freed by`），定位到 `buggy.c` 哪裡「free 完還在用」，修好讓 KASAN 沉默。

**Bug 3（`bug=3`）：一觸發就整台卡死。** `insmod buggy.ko bug=3` 後，對 `/proc/buggy` 做某個操作，機器**整個沒反應**（或 dmesg 噴 `BUG: sleeping function called from invalid context` / soft lockup）。**你的任務**：判斷這是死鎖或 sleep-in-atomic，開 `CONFIG_PROVE_LOCKING` + `CONFIG_DEBUG_ATOMIC_SLEEP` 重編，讀 lockdep splat 或 atomic-sleep 報告，指出「哪兩把鎖以相反順序拿」或「在哪個 atomic context 呼叫了會睡的函式」，修好。（這個 bug 我們埋**兩種**：一個 AB-BA 死鎖，一個 spinlock 內睡，`sub` param 選。）

**Bug 4（`bug=4`）：行為不對，沒崩沒報錯。** `insmod buggy.ko bug=4` 後，你對 `/proc/buggy` 寫一個數字 `N`，預期它回一個和 `N` 有簡單關係的結果（規格說「回傳 `N` 的位元數」），但它回的數字**大得離譜**、且這個操作**慢到 CPU 飆滿**。沒有任何 oops、沒有 KASAN、沒有 lockdep——一切「正常」，只是答案錯、又慢。**你的任務**：用 `ftrace function_graph` 追這個操作的呼叫圖，看某個函式被呼叫了**遠超預期的次數**（或某層遞迴/迴圈耗時異常），或用 `kprobe` 印出可疑函式的參數，定位到 `buggy.c` 的邏輯錯誤（迴圈邊界 / 遞迴沒收斂），修好讓它回正確值且快。

### 驗收標準

| # | 檢查項 | 怎麼驗 |
|---|---|---|
| 1 | Bug 1：能讀懂 oops，指出 `RIP` 函式與出事源碼行 | `insmod bug=1`，抄 `RIP`，`faddr2line buggy.ko <RIP>` 指回那一行 |
| 2 | Bug 1 修好：`insmod bug=1` 乾淨載入、`dmesg` 無 oops | 修正後重編 `insmod`，回 0 |
| 3 | Bug 2：在 KASAN kernel 上觸發 use-after-free 報告 | KASAN kernel，`insmod bug=2` + 壓測，`dmesg` 見 KASAN splat |
| 4 | Bug 2：能圈出報告的三段（出事點 / Allocated / Freed） | 對照 Ch 53「KASAN 報告怎麼讀」 |
| 5 | Bug 2 修好：同樣壓測下 KASAN 全程沉默 | 修正後重跑壓測，`dmesg` 乾淨 |
| 6 | Bug 3：觸發 lockdep AB-BA splat 或 sleeping-in-atomic 報告 | `PROVE_LOCKING`+`DEBUG_ATOMIC_SLEEP` kernel，`insmod bug=3 sub=0/1` |
| 7 | Bug 3 修好：統一鎖序 / 移出臨界區後，splat 消失、不再卡死 | 修正後重跑，`dmesg` 乾淨、機器不卡 |
| 8 | Bug 4：用 ftrace function_graph 或 kprobe 找出被呼叫爆量/耗時異常的函式 | tracefs 追蹤，看呼叫次數/耗時 |
| 9 | Bug 4 修好：回傳正確值（位元數）且操作瞬間完成 | 修正後 `echo N > /proc/buggy; cat /proc/buggy` 值對、不卡 |
| 10 | 四份報告都答齊五步（症狀→工具→定位→根因→修+驗） | 自我對照下方「除錯方法論五步」 |

## 環境準備：這次要為四個 bug 開不同 config

Bug 1 和 Bug 4 用**基礎除錯 config**（Ch 0 那套：`DEBUG_INFO`、`GDB_SCRIPTS`、關 KASLR，加 ftrace）就能查。Bug 2 和 Bug 3 需要**額外的 sanitizer**，要重編。實務上你會 build **一顆全開的除錯 kernel** 一次搞定所有 bug（慢是慢，但省得反覆重編）：

```bash
# 在你的 kernel 源碼樹（承接 Ch 0 的除錯 config）
./scripts/config \
    --enable  KASAN \
    --enable  KASAN_GENERIC \
    --enable  PROVE_LOCKING \
    --enable  DEBUG_ATOMIC_SLEEP \
    --enable  DEBUG_SPINLOCK \
    --enable  DEBUG_LOCK_ALLOC \
    --enable  FUNCTION_TRACER \
    --enable  FUNCTION_GRAPH_TRACER \
    --enable  DYNAMIC_FTRACE \
    --enable  KPROBES \
    --enable  DEBUG_INFO \
    --disable RANDOMIZE_BASE \
    --enable  SMP
make olddefconfig
make -j"$(nproc)"
```

每個為什麼開：

| 選項 | 作用 | 哪個 bug 用 |
|---|---|---|
| `KASAN` + `KASAN_GENERIC` | shadow memory 抓 UAF / 越界（Ch 53） | Bug 2 |
| `PROVE_LOCKING` | lockdep：不用真死鎖就抓 AB-BA 鎖序（Ch 28） | Bug 3（死鎖版） |
| `DEBUG_ATOMIC_SLEEP` | 抓「atomic context 呼叫會睡的函式」（Ch 2/53） | Bug 3（sleep 版） |
| `DEBUG_SPINLOCK` + `DEBUG_LOCK_ALLOC` | spinlock 額外檢查、lockdep 追鎖用 | Bug 3 保險 |
| `FUNCTION_TRACER` + `FUNCTION_GRAPH_TRACER` + `DYNAMIC_FTRACE` | ftrace 函式追蹤 + 呼叫圖 + 動態 patch（Ch 53） | Bug 4 |
| `KPROBES` | kprobe 動態掛觀測點印參數（Ch 51） | Bug 4 備援 |
| `DEBUG_INFO` + 關 KASLR | gdb/faddr2line 要符號、位址要固定（Ch 0） | 全程（尤其 Bug 1 定位） |
| `SMP` | Bug 2 的 UAF、Bug 3 的死鎖多核更容易顯現 | Bug 2/3 |

QEMU 開機（KASAN 吃記憶體給大一點、多核）：

```bash
qemu-system-x86_64 \
    -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr" \
    -smp 4 -m 1G -nographic
```

> **提醒**：KASAN kernel 慢、肥、開機久（Ch 53 踩雷 5）。Bug 4 量「慢不慢」時，要記得那個慢有一部分是 KASAN 疊加的——不過 Bug 4 的病因是**演算法級**的爆量（多幾個數量級），KASAN 那點常數倍拖慢淹不掉它，照樣看得出來。真要下效能結論另 build 一顆關 sanitizer 的（延伸挑戰）。

## 期望輸出範例

### Bug 1：載入就 oops

```
/ # insmod buggy.ko bug=1
[   42.108] buggy: bug=1 (NULL deref on init)
[   42.109] BUG: kernel NULL pointer dereference, address: 0000000000000000
[   42.109] #PF: supervisor write access in kernel mode
[   42.109] #PF: error_code(0x0002) - not-present page
[   42.109] Oops: 0002 [#1] PREEMPT SMP NOPTI
[   42.109] CPU: 1 PID: 148 Comm: insmod Tainted: G           O    6.12.0 #1
[   42.109] RIP: 0010:buggy_bug1_init+0x2a/0x40 [buggy]        ← ★最關鍵★
[   42.109] Code: ... <c7> 00 05 00 00 00 ...
[   42.109] RSP: 0018:ffffc9000131bd90 EFLAGS: 00010246
[   42.109] RAX: 0000000000000000 ...                          ← RAX=0，呼應 address=0
[   42.109] CR2: 0000000000000000                              ← page fault 位址=0
[   42.109] Call Trace:
[   42.109]  <TASK>
[   42.109]  do_one_initcall+0x44/0x200
[   42.109]  do_init_module+0x60/0x250
[   42.109]  __do_sys_finit_module+0xb4/0x120
[   42.109]  do_syscall_64+0x5c/0x90
[   42.109]  entry_SYSCALL_64_after_hwframe+0x77/0x7f
[   42.109]  </TASK>
```

讀法（Ch 53 的兩個聚焦點）：`RIP: buggy_bug1_init+0x2a/0x40` 說出事在 `buggy_bug1_init` 函式偏移 0x2a 處；`error_code(0x0002)` 的 bit1=1 是**寫入**（不是讀），`CR2=0` 是寫到位址 0——某個指標是 NULL 卻被寫了。`Call Trace` 從下往上是 `do_syscall_64 → finit_module → do_init_module → do_one_initcall → buggy_bug1_init`，正是 Ch 8 模組載入呼叫 initcall 的路徑。定位：

```bash
# host 上，對著模組的 .ko
scripts/faddr2line buggy.ko buggy_bug1_init+0x2a/0x40
# buggy_bug1_init at .../buggy.c:58
# 或整段 Call Trace 一起翻（把上面 dmesg 存成 oops.txt）
scripts/decode_stacktrace.sh vmlinux ./ < oops.txt
```

翻到 `buggy.c:58`，看那一行就懂了（見參考解答：對一個沒配的指標寫值）。

### Bug 2：KASAN 抓 use-after-free

在 KASAN kernel 上跑幾次讀寫壓測後：

```
==================================================================
BUG: KASAN: use-after-free in buggy_bug2_read+0x71/0x110 [buggy]
Read of size 4 at addr ffff888104b2a008 by task cat/173        ← 誰、讀幾 byte、哪個位址
CPU: 2 PID: 173 Comm: cat Tainted: G           O    6.12.0 #1
Call Trace:
 <TASK>
 dump_stack_lvl+0x4d/0x70
 print_report+0xcf/0x670
 kasan_report+0xb6/0xf0
 buggy_bug2_read+0x71/0x110 [buggy]        ← 出事點：read 時碰到已 free 的物件
 proc_reg_read_iter+0x...
 vfs_read+0x...
 ...
 </TASK>

Allocated by task 172:                     ← 這塊記憶體「當初在哪配的」
 kmalloc_trace+0x...
 buggy_bug2_write+0x5c/0xd0 [buggy]

Freed by task 172:                         ← 「在哪被 free 的」★兇手★
 kfree+0x...
 buggy_bug2_write+0x9a/0xd0 [buggy]

The buggy address belongs to the object at ffff888104b2a000
 which belongs to the cache kmalloc-32 of size 32
==================================================================
```

三段是重點：**出事點** `buggy_bug2_read`（read 時解參考已 free 的 buffer）、**Allocated by** `buggy_bug2_write`（write 時 `kmalloc` 的）、**Freed by** 也是 `buggy_bug2_write`（write 路徑裡把它 `kfree` 了、卻沒把全域指標清成 NULL）。KASAN 一次把「誰配、誰放、誰又去讀」三方指給你——這就是它比 gdb 單步強太多的地方。

### Bug 3（死鎖版，`sub=0`）：lockdep AB-BA splat

```
======================================================
WARNING: possible circular locking dependency detected
6.12.0 #1 Tainted: G           O
------------------------------------------------------
cat/181 is trying to acquire lock:
 ffffffffc0b13080 (buggy_lock_a){+.+.}-{2:2}, at: buggy_bug3_read+0x3c/0x90 [buggy]

but task is already holding lock:
 ffffffffc0b130c0 (buggy_lock_b){+.+.}-{2:2}, at: buggy_bug3_read+0x20/0x90 [buggy]

the existing dependency chain (in reverse order) is:

-> #1 (buggy_lock_b){+.+.}-{2:2}:
       ... buggy_bug3_write+0x48/0x90 [buggy]     ← write path：先 A 後 B
-> #0 (buggy_lock_a){+.+.}-{2:2}:
       ... buggy_bug3_read+0x3c/0x90 [buggy]      ← read path：先 B 後 A（反了）

 Possible unsafe locking scenario:
       CPU0                    CPU1
       ----                    ----
  lock(buggy_lock_a);
                               lock(buggy_lock_b);
                               lock(buggy_lock_a);
  lock(buggy_lock_b);
  *** DEADLOCK ***
```

lockdep **不用真的卡死**——它看到 write path 建立了「A→B」、read path 建立了「B→A」，合起來成環就報。修法：規定全域鎖序（永遠先 A 後 B），把 read path 改成也先拿 A。

### Bug 3（sleep 版，`sub=1`）：DEBUG_ATOMIC_SLEEP

```
BUG: sleeping function called from invalid context at .../buggy.c:142
in_atomic(): 1, irqs_disabled(): 0, non_block: 0, pid: 185, name: cat
preempt_count: 1, expected: 0
2 locks held by cat/185:
 #0: ... (buggy_slock){+.+.}-{2:2}, at: buggy_bug3_read+0x...   ← 持有 spinlock
CPU: 0 PID: 185 Comm: cat
Call Trace:
 dump_stack_lvl+0x4d/0x70
 __might_resched+0x1a2/0x2d0
 __might_sleep+0x8e/0xa0
 __kmalloc+0x...              ← kmalloc(GFP_KERNEL) 可能睡
 buggy_bug3_read+0x...  [buggy]
```

`in_atomic(): 1` + `preempt_count: 1` 說「現在在 atomic context（持有 spinlock、preempt 關了）」，`__kmalloc`/`__might_sleep` 那行說「你偏偏呼叫了可能睡的 `kmalloc(GFP_KERNEL)`」。修法：把配置移到 `spin_unlock` 之後，或臨界區內非配不可時改 `GFP_ATOMIC` 並檢查 NULL（Ch 6 的 GFP 語意、卡關提示 4）。

### Bug 4：ftrace function_graph 看出爆量

規格說「寫入 `N` 應回傳 `N` 的**位元數**（population count）」。你寫 `echo 8`（二進位 `1000`，答案該是 **1**），`cat` 卻回一個巨大的數、還等了很久。開 ftrace 追這個 write：

```bash
cd /sys/kernel/tracing
echo 0 > tracing_on
echo function_graph > current_tracer
echo buggy_popcount > set_graph_function     # 追那個算位元數的函式
echo 1 > tracing_on
echo 8 > /proc/buggy                          # 觸發
echo 0 > tracing_on
cat trace | head -60
```

輸出（節錄）會露出馬腳：

```
 CPU  DURATION       FUNCTION CALLS
 --------------------------------------
  2)               |  buggy_popcount() {
  2)               |    buggy_popcount() {          ← 遞迴？而且傳進去的值不對
  2)               |      buggy_popcount() {
  2)               |        buggy_popcount() {
  ...              |          ... （成千上萬層 / 上萬次呼叫）
  2) # 3841.204 us |  }                             ← 這一個 write 花了 3.8 ms（該是 ns 級）
```

`buggy_popcount` 被呼叫了**遠超「N 的位元數」該有的次數**（`popcount(8)` 該是常數幾步，這裡跑了上萬次）——遞迴的收斂條件或迴圈的位移方向寫反了。對照下面參考解答：`n >>= 1` 寫成了 `n <<= 1`（或忘了縮小 `n`），迴圈幾乎不收斂、count 亂加。也可以用 kprobe 直接印每次進 `buggy_popcount` 的參數看它怎麼變化：

```bash
echo 'p:pc buggy_popcount n=%di' > /sys/kernel/tracing/kprobe_events   # x86_64 第一參數在 rdi/%di
echo 1 > /sys/kernel/tracing/events/kprobes/pc/enable
echo 8 > /proc/buggy
cat /sys/kernel/tracing/trace | head
# 你會看到 n 不是 8→4→2→1→0 遞減，而是越變越大或原地打轉
```

## 卡關提示

1. **Bug 1 定位不到源碼行？先確認你 `faddr2line` 餵對檔案。** oops 的 `RIP` 標了 `[buggy]`，代表出事在**模組**裡，要 `scripts/faddr2line buggy.ko buggy_bug1_init+0x2a/0x40`（餵 `.ko`，不是 `vmlinux`）。整段 `Call Trace` 同時橫跨模組（`[buggy]`）和 kernel（`do_one_initcall` 等），用 `scripts/decode_stacktrace.sh vmlinux <模組目錄> < oops.txt` 才能兩邊都翻。還有：位址要準，KASLR 一定要關（Ch 0），否則 `RIP` 偏移對不上符號。

2. **Bug 2 的 UAF「跑不出來」？三招放大它。** UAF 偶發是因為 free 後那塊記憶體不一定馬上被別人覆寫。放大手段：(a) **開 KASAN 本身就大幅提高命中率**——KASAN 的 quarantine 讓 freed 物件延後真正回收、shadow 標成 `0xfb`，你一碰就報（不需要它真的被別人拿走）；(b) 反覆做「write（配 + free）→ read」很多次，用個 shell 迴圈 `for i in $(seq 100); do echo x > /proc/buggy; cat /proc/buggy; done`；(c) `-smp 4` 讓一個 CPU free、另一個 read 交錯。**如果開了 KASAN 卻沒報**，先確認 KASAN 真的生效（`dmesg | grep -i kasan` 開機時應有初始化訊息）、且你 read 的路徑真的碰到那塊 freed buffer。

3. **Bug 3 分不清是「死鎖」還是「sleep-in-atomic」？看 dmesg 有沒有噴東西。** 純 AB-BA 死鎖**真的卡死時通常沒有即時訊息**（兩個 thread 互等，機器 hang，可能過幾十秒才有 soft lockup / hung task 警告）——但只要開了 `PROVE_LOCKING`，lockdep 會在**第一次反序拿鎖時就印 splat**（不用等真卡死）。sleep-in-atomic 則是 `DEBUG_ATOMIC_SLEEP` **一觸發就印** `BUG: sleeping function called from invalid context`。所以：開這兩個 config，跑一次觸發操作，看 dmesg 印的是 `circular locking`（→ 死鎖）還是 `sleeping function`（→ atomic 睡）。兩個 config 都開著，一次就分辨得出來。

4. **Bug 3 sleep 版用 `GFP_ATOMIC` 修，不是免費升級。** 臨界區內非配記憶體不可時，`GFP_ATOMIC` 不睡（合法），但它只能從 emergency reserve 硬挖、**配失敗（回 NULL）機率高得多**，你**必須檢查 NULL 並優雅處理**（Ch 6 的 GFP 語意）。更好的正解通常是**重構成「鎖外配置」**：先在 `spin_lock` 之前 `kmalloc(GFP_KERNEL)` 好，進臨界區只做不睡的短動作（串列插入、指標賦值）。這是 kernel 到處可見的 pattern。

5. **Bug 4 用 ftrace 追不到你的函式？先確認函式沒被 inline、且 filter 名字對。** 短函式很容易被編譯器 inline 掉，inline 後就沒有獨立的 `__fentry__` 掛點、ftrace 追不到。對付法：確認 `available_filter_functions` 裡有你的函式名（`cat /sys/kernel/tracing/available_filter_functions | grep buggy_popcount`），沒有的話它被 inline 了——可以給該函式加 `noinline`（教學上為了觀測允許），或改用 `kprobe`（kprobe 掛在指令位址上，比較不怕 inline，但也追不到被完全 inline 消失的函式）。另外 `function_graph` 要先 `echo 0 > tracing_on` 再設 tracer/filter，最後才 `echo 1 > tracing_on`，順序錯了會追到一堆無關東西。

## 除錯方法論五步（每個 bug 都走一遍）

這是本練習的核心紀律，也是你四份報告的骨架。對每個 bug，強迫自己按順序回答：

```
  1. 讀症狀      ── 到底發生了什麼？有 oops？卡住？行為怪？
                   把「客訴」翻譯成「技術現象」（address=0 的 write fault / hang / 值錯又慢）

  2. 選工具      ── 這個現象該用光譜上哪個工具？
                   oops→讀現場；偶發崩→KASAN；卡住→lockdep/atomic-sleep；行為怪→ftrace/kprobe
                   原則：能用更左邊（更省事）的就不往右

  3. 定位        ── 工具給了什麼線索？把它縮到源碼「哪一行」
                   RIP+faddr2line / KASAN 的 Freed-by stack / lockdep 的兩條 lock chain /
                   ftrace 的爆量函式

  4. 理解根因    ── 「那一行為什麼會壞」用一句話說清楚
                   指標沒配就寫 / free 完沒清指標又去讀 / 兩處鎖序相反 / 迴圈位移方向反了
                   （能說出根因才算真懂，不然只是「改到不崩」）

  5. 修 + 驗證   ── 改對 + 證明同樣操作不再觸發
                   改最小的一處，重編重跑「原本會觸發的那個操作」，確認工具沉默、行為正確
```

第 4 步是分水嶺：很多人卡在「亂改到不崩就以為修好了」。UAF 你把 read 註解掉當然不崩，但那不是修好，是把功能刪了。真修好是「free 之後把全域指標設 NULL，read 前檢查 NULL」——你要能講出這個根因，才擋得住面試官追問「那為什麼你這樣改就對了」。

## 分步實作建議

1. **先把四個 bug 版本都能載入/觸發跑通。** 照參考解答把 `buggy.c` 建起來，`bug=1..4` 各 `insmod` 一次，確認每個都如「症狀規格」表現（bug=1 立刻 oops、bug=4 值錯又慢…）。這步不修，只是先「看到」四種症狀長什麼樣。
2. **Bug 1：純讀 oops 破案。** 不開任何重工具（基礎 config 就好），`insmod bug=1`，把 dmesg oops 存下來，`faddr2line` / `decode_stacktrace.sh` 定位到源碼行，寫下五步報告，修好。這是最省事的一類，先建立「讀現場就能破案」的信心。
3. **Bug 4：ftrace 追流程破案。** 也不必 KASAN。開 `function_graph` 追 `buggy_popcount`，看它被呼叫幾次、耗時多少，對照「popcount(8) 該是幾步」發現爆量，定位到迴圈/遞迴的位移方向錯誤。順手用 kprobe 印參數驗證你的假設。這步練「沒有崩潰現場時，主動讓程式碼說話」。
4. **Bug 2：KASAN 破案（要重編）。** 切到 KASAN kernel，寫個 shell 迴圈壓 `/proc/buggy`，逼出 use-after-free 報告，圈出三段 stack，定位到「free 完沒清指標又 read」，修好（free 後 `ptr = NULL`、read 前檢查）。這步練「偶發記憶體錯誤靠 KASAN 釘死」。
5. **Bug 3：lockdep / atomic-sleep 破案。** 同一顆全開 config 的 kernel，`sub=0` 觸發 AB-BA 讀 lockdep splat、逐行讀懂兩條 lock chain、統一鎖序修好；`sub=1` 觸發 spinlock 內睡、讀 atomic-sleep 報告、把配置移出臨界區修好。這步練「卡死類 bug 用 lockdep/DEBUG_ATOMIC_SLEEP 不用等真卡死就抓」。
6. **回頭把四份報告的五步補齊。** 尤其第 4 步「根因一句話」——這是你這個練習真正要帶走的東西。

## 完整參考解答

<details>
<summary>點開看完整可編譯解答（buggy.c 四 bug 全含 + 修正版 + Makefile + 除錯腳本）</summary>

下面這份 `buggy.c` 把四個 bug 用 `/proc/buggy` 介面（Ch 33/34/38 的 proc/misc 路徑）串起來，用 module param `bug`（和 Bug 3 的 `sub`）切換。每個 bug 旁邊註解標了「★ BUG ★」，並在後面給修正版。**教學上把 bug 留在源碼裡對照；真實程式碼不會這樣。**

### `buggy.c`（含四個 bug 的版本）

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/slab.h>
#include <linux/proc_fs.h>
#include <linux/uaccess.h>
#include <linux/spinlock.h>
#include <linux/delay.h>

static int bug = 1;   /* 1=NULL deref  2=UAF  3=lock/sleep  4=logic/perf */
static int sub = 0;   /* Bug 3: 0=AB-BA 死鎖  1=sleep in spinlock */
module_param(bug, int, 0444);
module_param(sub, int, 0444);

static struct proc_dir_entry *ent;

/* ================= Bug 2 用的全域 buffer ================= */
static int  *bug2_buf;      /* write 時 kmalloc、read 時讀 */

/* ================= Bug 3 用的鎖 ================= */
static DEFINE_SPINLOCK(buggy_lock_a);
static DEFINE_SPINLOCK(buggy_lock_b);
static DEFINE_SPINLOCK(buggy_slock);

/* ================= Bug 4：算「N 的位元數」，但寫錯 ================= */
static noinline int buggy_popcount(unsigned long n)
{
    int count = 0;
    while (n) {
        count += n & 1;
        /* ★ BUG 4 ★ 該是 n >>= 1 把 n 縮小、迴圈才會收斂；
         * 寫成 n <<= 1 之後 n 幾乎永遠非零（直到溢位繞回），
         * 迴圈跑上萬次、count 亂加 → 值錯又慢。 */
        n <<= 1;
    }
    return count;
}

/* ================= /proc/buggy 的 read ================= */
static ssize_t buggy_read(struct file *f, char __user *ubuf,
                          size_t len, loff_t *off)
{
    char kbuf[64];
    int n;

    if (*off > 0)
        return 0;

    switch (bug) {
    case 2: {
        /* ★ BUG 2 ★ 直接讀 bug2_buf——但它可能已在 write 裡被 kfree、
         * 且指標沒清成 NULL（見下）。read 到已 free 的物件 = UAF。 */
        int v = bug2_buf ? bug2_buf[0] : -1;   /* KASAN 在這行報 use-after-free */
        n = scnprintf(kbuf, sizeof(kbuf), "%d\n", v);
        break;
    }
    case 3:
        if (sub == 0) {
            /* ★ BUG 3a ★ read path 先 B 後 A —— 和 write path（先 A 後 B）相反 */
            spin_lock(&buggy_lock_b);
            spin_lock(&buggy_lock_a);
            n = scnprintf(kbuf, sizeof(kbuf), "read locked\n");
            spin_unlock(&buggy_lock_a);
            spin_unlock(&buggy_lock_b);
        } else {
            /* ★ BUG 3b ★ 持有 spinlock 時呼叫 kmalloc(GFP_KERNEL)（可能睡）*/
            char *tmp;
            spin_lock(&buggy_slock);
            tmp = kmalloc(64, GFP_KERNEL);   /* line 142：sleeping in atomic */
            kfree(tmp);
            spin_unlock(&buggy_slock);
            n = scnprintf(kbuf, sizeof(kbuf), "read done\n");
        }
        break;
    default:
        n = scnprintf(kbuf, sizeof(kbuf), "buggy bug=%d\n", bug);
        break;
    }

    if (copy_to_user(ubuf, kbuf, n))
        return -EFAULT;
    *off = n;
    return n;
}

/* ================= /proc/buggy 的 write ================= */
static ssize_t buggy_write(struct file *f, const char __user *ubuf,
                           size_t len, loff_t *off)
{
    char kbuf[64];
    unsigned long val;

    if (len >= sizeof(kbuf))
        len = sizeof(kbuf) - 1;
    if (copy_from_user(kbuf, ubuf, len))
        return -EFAULT;
    kbuf[len] = '\0';

    switch (bug) {
    case 2:
        /* ★ BUG 2 ★ 每次 write 都配一塊、用完 kfree，但不清空全域指標，
         * 之後的 read 還會拿這個已 free 的指標去讀 → UAF。 */
        bug2_buf = kmalloc(sizeof(int), GFP_KERNEL);   /* Allocated by 這裡 */
        if (bug2_buf) {
            bug2_buf[0] = 1234;
            kfree(bug2_buf);                            /* Freed by 這裡；指標沒設 NULL */
        }
        break;
    case 3:
        if (sub == 0) {
            /* write path 先 A 後 B（建立 A→B 依賴，和 read 的 B→A 湊成環）*/
            spin_lock(&buggy_lock_a);
            spin_lock(&buggy_lock_b);
            spin_unlock(&buggy_lock_b);
            spin_unlock(&buggy_lock_a);
        }
        break;
    case 4:
        if (!kstrtoul(kbuf, 10, &val))
            pr_info("buggy: popcount(%lu) = %d\n", val, buggy_popcount(val));
        break;
    }
    return len;
}

static const struct proc_ops buggy_ops = {
    .proc_read  = buggy_read,
    .proc_write = buggy_write,
};

/* ================= Bug 1：init 就 NULL deref ================= */
static int *bug1_ptr;    /* 故意不配 */

static int buggy_bug1_init(void)
{
    /* ★ BUG 1 ★ bug1_ptr 是 NULL（全域指標預設 0），直接寫它 → NULL deref
     * oops 的 RIP 會指到這個函式；error_code bit1=1（寫）、CR2=0。 */
    *bug1_ptr = 5;       /* buggy.c 約在此行 → faddr2line 會指回來 */
    return 0;
}

static int __init buggy_init(void)
{
    pr_info("buggy: bug=%d sub=%d\n", bug, sub);

    if (bug == 1)
        return buggy_bug1_init();   /* 立刻 oops */

    ent = proc_create("buggy", 0666, NULL, &buggy_ops);
    if (!ent)
        return -ENOMEM;
    return 0;
}

static void __exit buggy_exit(void)
{
    if (ent)
        proc_remove(ent);
    pr_info("buggy: unloaded\n");
}

module_init(buggy_init);
module_exit(buggy_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Buggy module for debugging practice (kernel_internals practice F)");
```

### 四個修正版

**Bug 1 修正**：`bug1_ptr` 沒有指向任何合法記憶體。要嘛配一塊，要嘛根本不該解參考它。合理修法是配置並檢查：

```c
static int buggy_bug1_init(void)
{
    bug1_ptr = kmalloc(sizeof(int), GFP_KERNEL);
    if (!bug1_ptr)
        return -ENOMEM;      /* 配失敗優雅退出，不硬寫 NULL */
    *bug1_ptr = 5;
    return 0;
}
/* exit 記得 kfree(bug1_ptr) */
```

**Bug 2 修正**：根因是「`kfree` 後全域指標沒清成 NULL，read 又拿它去讀」。兩處一起改——free 後設 NULL、read 前檢查（其實這個 buffer 的生命週期設計本身就怪，正解是「read 需要用時才配、用完在同一個函式內配套 free，不跨 read/write 共用全域指標」，但最小修法如下）：

```c
/* write：free 後立刻清指標 */
kfree(bug2_buf);
bug2_buf = NULL;          /* ★ 關鍵：清成 NULL，read 才不會拿到懸空指標 */

/* read：bug2_buf 為 NULL 時走安全路徑（上面的 ?: 已處理，
 * 真正的洞是 write 沒設 NULL；補上後就不會 UAF）*/
```

> 更根本的修法：不要用「write 配、read 讀」跨呼叫共用一個裸全域指標——那是 UAF 的溫床。若真要跨呼叫保留資料，該用引用計數（Ch 47 的 refcount 概念）或在移除時用 RCU 延後釋放（Ch 27 / 練習 D）。這裡的最小修法足以讓 KASAN 沉默，但值得想想為什麼原設計脆弱。

**Bug 3a（死鎖）修正**：統一全域鎖序，永遠先 A 後 B。把 read path 改成也先拿 A：

```c
/* read path 改成和 write path 同序：先 A 後 B */
spin_lock(&buggy_lock_a);
spin_lock(&buggy_lock_b);
/* ... */
spin_unlock(&buggy_lock_b);
spin_unlock(&buggy_lock_a);
```

**Bug 3b（sleep in atomic）修正**：不要在 spinlock 臨界區裡 `kmalloc(GFP_KERNEL)`。把配置移到鎖外：

```c
char *tmp = kmalloc(64, GFP_KERNEL);   /* 鎖外配，可睡 OK */
if (!tmp)
    return -ENOMEM;
spin_lock(&buggy_slock);
/* ... 臨界區只做不睡的短動作 ... */
spin_unlock(&buggy_slock);
kfree(tmp);                            /* 鎖外 free */
```

**Bug 4 修正**：`n <<= 1` 改回 `n >>= 1`，迴圈就會收斂（每次右移把最低位丟掉，`n` 遞減到 0）：

```c
static noinline int buggy_popcount(unsigned long n)
{
    int count = 0;
    while (n) {
        count += n & 1;
        n >>= 1;          /* ★ 修正：右移縮小 n，最多 64 步收斂 */
    }
    return count;
}
/* 也可以直接用 kernel 內建：return hweight_long(n); —— 更快更不會寫錯 */
```

### `Makefile`

```makefile
obj-m += buggy.o
KDIR := /path/to/your/linux-6.12      # 指向你 build 的除錯 kernel 源碼樹

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

（recipe 行首是 Tab 不是空白，見 Ch 0 踩雷 5。）

### 除錯輔助腳本 `debug.sh`（放進 initramfs）

```sh
#!/bin/busybox sh
# 依序觸發四個 bug，示範每個該用哪個工具看
echo "===== Bug 1: NULL deref on init (讀 oops) ====="
insmod /buggy.ko bug=1        # 立刻 oops；抄 RIP 回 host faddr2line
dmesg | grep -A2 "NULL pointer" | head

echo "===== Bug 2: UAF (需 KASAN kernel) ====="
insmod /buggy.ko bug=2
for i in $(seq 1 50); do echo x > /proc/buggy; cat /proc/buggy >/dev/null; done
dmesg | grep -E "KASAN|use-after-free" | head
rmmod buggy

echo "===== Bug 3a: AB-BA deadlock (讀 lockdep) ====="
insmod /buggy.ko bug=3 sub=0
echo w > /proc/buggy; cat /proc/buggy   # write 建立 A→B、read 建立 B→A
dmesg | grep -E "circular locking|DEADLOCK" | head
rmmod buggy

echo "===== Bug 3b: sleep in spinlock (讀 DEBUG_ATOMIC_SLEEP) ====="
insmod /buggy.ko bug=3 sub=1
cat /proc/buggy
dmesg | grep -E "sleeping function|invalid context" | head
rmmod buggy

echo "===== Bug 4: logic/perf (用 ftrace) ====="
insmod /buggy.ko bug=4
cd /sys/kernel/tracing
echo 0 > tracing_on; echo function_graph > current_tracer
echo buggy_popcount > set_graph_function
echo 1 > tracing_on
echo 8 > /proc/buggy
echo 0 > tracing_on
cat trace | head -30
echo nop > current_tracer; echo > set_graph_function
cd /; rmmod buggy
```

</details>

## 測試用例

在 QEMU（`-smp 4 -m 1G`，KASAN + PROVE_LOCKING + DEBUG_ATOMIC_SLEEP + FUNCTION_GRAPH_TRACER 全開）裡把 `buggy.ko` 和 `debug.sh` 放進 initramfs，逐項對：

| 測試 | 指令 | 期望 |
|---|---|---|
| 開機環境對 | `nproc`；`dmesg \| grep -i kasan`；`ls /sys/kernel/tracing` | 回 4；有 KASAN 訊息；tracefs 掛好 |
| Bug 1 觸發 | `insmod buggy.ko bug=1` | `dmesg` 見 `NULL pointer dereference`，`RIP: buggy_bug1_init+...` |
| Bug 1 定位 | `faddr2line buggy.ko buggy_bug1_init+<off>` | 指回 `*bug1_ptr = 5;` 那行 |
| Bug 1 修好 | 配置 `bug1_ptr` 後重編 `insmod bug=1` | 乾淨載入、回 0、無 oops |
| Bug 2 觸發 | `insmod bug=2` + 50 次讀寫迴圈 | `dmesg` 見 `KASAN: use-after-free in buggy_bug2_read`（或 `buggy_read`） |
| Bug 2 三段 | 看 KASAN 報告 | 能圈出出事點 / Allocated by / Freed by |
| Bug 2 修好 | free 後設 NULL 重編、重壓測 | KASAN 全程沉默 |
| Bug 3a 觸發 | `insmod bug=3 sub=0`，讀+寫一次 | `WARNING: possible circular locking dependency` |
| Bug 3a 修好 | 統一鎖序（read 也先 A 後 B）重編 | 無 circular locking、不卡死 |
| Bug 3b 觸發 | `insmod bug=3 sub=1`，`cat /proc/buggy` | `BUG: sleeping function called from invalid context` |
| Bug 3b 修好 | 配置移出臨界區重編 | 無 sleeping-in-atomic 報告 |
| Bug 4 診斷 | ftrace `function_graph` 追 `buggy_popcount` | 呼叫次數/耗時遠超預期（上萬次 / ms 級） |
| Bug 4 修好 | `n <<= 1` 改回 `n >>= 1` 重編 | `echo 8 > /proc/buggy` 後 dmesg 印 `popcount(8) = 1`，瞬間完成 |

一個進階驗證：**用 gdb 停在出事點確認你的定位。** QEMU 加 `-s`，`insmod bug=1` 前先在 gdb `lx-symbols`（載模組符號）、`break buggy_bug1_init`，`insmod` 時會停下，`next` 到那行 `*bug1_ptr = 5`、`print bug1_ptr` 看到它是 `0x0`——你用 oops 反推的結論，用 gdb 當場證實。這把 Ch 0 的 gdb、Ch 53 的 oops 判讀接成一條線。

## 延伸挑戰

1. **用 KCSAN 抓一個 data race（Ch 53）。** 上面四個 bug 沒有純 data race（Bug 2 的 UAF 是記憶體錯誤，不是無同步存取同一變數）。加一個 `bug=5`：兩個 kthread（Ch 10）無同步地一個 `counter++`、一個讀 `counter`，開 `CONFIG_KCSAN` 重編。KCSAN 會報 `data-race in ...`——它抓的是「存取本身合法、但沒做同步」，正好補 KASAN 的盲區。讀懂 KCSAN 報告和 KASAN 報告格式的差別。

2. **讓 Bug 1 升級成 panic，用 kdump + crash 屍檢（Ch 53）。** 開機參數加 `panic_on_oops=1`，`insmod bug=1` 就會從 oops 變 panic（整機停）。若你設好 `crashkernel=` + kdump（`kexec` 一顆 crash kernel），panic 時會 dump `vmcore`，事後用 `crash vmlinux vmcore` 的 `bt` 看崩潰時的 backtrace、`log` 撈完整 dmesg。體會「崩得太徹底、連 dmesg 都撈不全時」的最後手段。

3. **Bug 4 不靠 ftrace，改用 `perf` 或 bpftrace 找熱點。** 同一個爆量迴圈，用 `perf top` 或 `perf record` 會看到 `buggy_popcount` 吃掉幾乎全部 CPU（Ch 51/52、`observability_tools` 課的 perf）。或用 bpftrace 的 `kprobe:buggy_popcount { @[arg0] = count(); }` 統計每個參數值進來幾次。對照「ftrace 看呼叫圖 vs perf 看取樣熱點 vs bpftrace 看統計」三種視角在同一個 bug 上各給你什麼。

4. **把 `/proc/buggy` 改成 misc device（Ch 38），並在 read/write 加 `ftrace` 的 `trace_printk`。** `trace_printk()` 把訊息寫進 ftrace ring buffer（不是 dmesg），開銷比 `printk` 低、時間戳更精準，是追時序 bug 的利器。在你修好的路徑關鍵點插 `trace_printk("enter read, buf=%px\n", bug2_buf)`，用 `cat trace` 看它和其他 tracepoint 交錯的時序——這是生產環境追「誰先誰後」的實務手法。

5. **把四份除錯報告寫成一份「on-call runbook」。** 為每一類症狀（oops / 偶發崩 / hang / 行為怪）寫一頁「看到這個 → 先跑什麼指令 → 期待什麼輸出 → 指向哪類根因」。這正是 final project 要交付的「工程化」思維的預演——把你這次的除錯經驗固化成別人也能照著跑的流程。

## 本練習重點整理

- **除錯的第一步永遠是「把症狀翻成技術現象、選對工具」，不是急著開最重的工具。** 有 oops 先讀 oops（零成本）、偶發崩才上 KASAN（重編）、卡住用 lockdep/atomic-sleep、行為怪用 ftrace/kprobe——照 Ch 53 的干擾光譜從左往右升級。
- **四類 bug 各有招牌氣味和主武器**：NULL deref 看 `RIP`+`Call Trace`+`faddr2line`；UAF「偶發、重現不穩」→ KASAN 的 alloc/free 兩條 stack 直接指兇手；hang → lockdep（不用真死鎖就報 AB-BA）或 DEBUG_ATOMIC_SLEEP；行為怪又慢 → ftrace function_graph 看爆量/耗時。
- **「修好」必含「說得出根因」和「證明修好」。** 亂改到不崩不算——UAF 的根因是「free 後指標沒清又去讀」，修法是「free 後設 NULL + read 前檢查」，驗證是「同樣壓測 KASAN 沉默」。這條「症狀→工具→定位→根因→修+驗」就是面試問「你怎麼 debug kernel」的滿分答案。
- **這四個工具你在 Ch 51/53 學過用法，這裡練的是「什麼時候用哪個」的判斷力**——那才是把 53 章知識變成 on-call 生存能力的關鍵。

## 自我檢核

- [ ] 拿到一個新症狀（oops / hang / 值錯），不看筆記能說出「該先用哪個工具、為什麼不先用更重的」
- [ ] 給你一份 NULL deref oops，能指出 `RIP`、`error_code` 的讀/寫 bit、`Call Trace` 的模組載入路徑，並用 `faddr2line` 定位
- [ ] 能讀懂 KASAN 的 use-after-free 三段（出事點 / Allocated by / Freed by），並說出 UAF 的根因與最小修法
- [ ] 能區分「lockdep 抓死鎖」和「DEBUG_ATOMIC_SLEEP 抓 atomic 睡」各觸發什麼報告、各怎麼修
- [ ] 能用 `ftrace function_graph` 追一個沒崩的「行為怪」bug，從呼叫次數/耗時反推邏輯錯誤
- [ ] 面試被問「模組載入就 panic / 跑一跑偶爾崩 / 一操作就 hang，你各怎麼查」，能對三種症狀給出不同的工具路徑
- [ ] 能對每個 bug 完整走一遍五步（症狀→工具→定位→根因→修+驗），而不是「改到不崩」

## 延伸閱讀

### 官方文件

- **[Documentation/admin-guide/bug-hunting.rst](https://www.kernel.org/doc/html/latest/admin-guide/bug-hunting.html)** — kernel 官方
  - **讀哪裡**：整篇。從一份 oops 一路查到源碼行的權威流程，`faddr2line`、`decode_stacktrace.sh` 的用法都在這
  - **和本練習的關聯**：Bug 1 的定位流程就是這篇的實作；卡在 oops 欄位不懂時回來查

- **[Documentation/dev-tools/kasan.rst](https://www.kernel.org/doc/html/latest/dev-tools/kasan.html)** — kernel 官方
  - **讀哪裡**：「Generic KASAN」+ 報告格式那節，理解 `Freed by task` / `Allocated by task` 兩條 stack 怎麼來
  - **能學到什麼**：Bug 2 那份報告每一欄的精確意義，接 Ch 53

- **[Documentation/trace/ftrace.rst](https://www.kernel.org/doc/html/latest/trace/ftrace.html)** — kernel 官方
  - **讀哪裡**：`function_graph`、`set_graph_function`、`set_ftrace_filter` 幾節
  - **和本練習的關聯**：Bug 4 追 `buggy_popcount` 用的就是這些 tracefs 檔案；kprobe_events 的格式見同目錄 `kprobetrace.rst`

### 文章 / 指南

- **[LWN: "The kernel address sanitizer"](https://lwn.net/Articles/612153/)** — Jonathan Corbet
  - **為什麼值得讀**：KASAN 上游時的設計動機與 shadow memory 原理，補足官方文件的「為什麼這樣設計」
  - **前提**：做完 Bug 2、親手讀過一份 KASAN 報告後再讀，體會最深

- **[Documentation/locking/lockdep-design.rst](https://www.kernel.org/doc/html/latest/locking/lockdep-design.html)** — Ingo Molnar 等
  - **讀哪裡**：lock class、lock chain 的概念，理解 lockdep 為什麼「不用真死鎖就能抓 AB-BA」
  - **和本練習的關聯**：Bug 3a 那份 circular locking splat 每個欄位（`-> #1`、`-> #0`、possible unsafe scenario）在這有解釋，也接練習 D

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 18 章 "Debugging"
  - **定位**：最好讀的 kernel debug 概念入門，講 printk、oops、`BUG_ON`、二分法的思路，和本練習的方法論互補
  - **注意**：ftrace/KASAN 等較新工具書中著墨少，以本練習與 Ch 53、官方文件為準

做完這個練習，你把 Ch 51/53 的觀測與除錯工具從「認得指令」變成「面對症狀能選對武器、系統化破案」——這正是把前面 53 章的子系統知識，變成能在真實 on-call 現場生存的能力。全課只剩最後一關：把你學到的所有東西——寫模組、選對資料結構與鎖、觀測與除錯——收攏成一個**工程化的核心模組套件**，那是驗收你是否真的能獨立寫出、測試、除錯一組生產級 kernel 模組的最終專案。

→ [Final Project：核心模組套件](./final-project-kernel-module-suite.md)
