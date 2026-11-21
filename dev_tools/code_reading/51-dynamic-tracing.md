# Ch 51 — 動態觀察：ftrace function_graph 與 bpftrace

> **目標**：靜態讀碼追到 `filp->f_op->write_iter(...)` 斷掉的時候，能在 QEMU guest 裡用 ftrace 讓 kernel 自己說出「這次呼叫的是誰、它又呼叫了誰、參數是多少」，並且懂 ftrace 底層怎麼做到「平常零成本、打開才付錢」。
> **鴻溝**：本章主要練**間接鴻溝**，順帶練**目標鴻溝**（每次追蹤都要先決定要問什麼，否則會被輸出淹死）。

> **環境**：Linux v6.18（`~/code_reading/build`，x86_64_defconfig + kvm_guest.config + ftrace/kprobes/BPF），QEMU 6.2 TCG 模式（WSL 沒有可用的 KVM），guest 只有 busybox；ftrace 透過 tracefs（`/sys/kernel/tracing`）操作。本章所有 trace 輸出都是在這個 guest 裡實際跑出來的；bpftrace 一節除外，那節有明確標示。

## 為什麼需要這個？

[Ch 28](./28-ops-tables-indirect-calls.md) 我們用靜態方法對付 ops table：找 designated initializer、用 `rg '\.write_iter\s*='` 列出候選、用 clangd 找 references。這些方法回答的是「**可能**呼叫誰」。v6.18 的 tree 裡 `.write_iter =` 有 88 個 `file_operations` 指派（[練習 G](./practice-g-ops-callback-finder.md) 的工具會算給你看），靜態方法把 88 個候選全部端給你，然後問題變成：「我現在關心的這次 `write(2)`，到底走哪一個？」

這個問題靜態分析回答不了，因為答案取決於**執行期的資料**：`filp` 是哪一種檔案，是 open 的時候決定的，不是編譯的時候。

在 ftrace 出現之前，kernel 開發者的辦法大致是三種：

1. **printk 除錯**：在懷疑的地方加 `printk`，重編、重開機。一輪幾分鐘到幾十分鐘，而且你得先猜對加在哪。
2. **kgdb / 硬體除錯器**：能停下來看，但停下來的 kernel 不會繼續處理中斷和計時器，很多時序相關的行為一停就變了。
3. **oprofile 這類取樣工具**：知道時間花在哪裡，但不知道呼叫順序。

ftrace（function tracer）在 v2.6.27 合入（`git log --diff-filter=A -- kernel/trace/ftrace.c` 找到的第一個 commit 是 16444a8a40d4，作者 Arnaldo Carvalho de Melo；同一批的 dynamic ftrace 3d0833953e1b 是 Steven Rostedt 寫的，`git tag --contains` 兩者最早都在 v2.6.27）。它的賣點是：**不用重編、不用重開機、不用停住 kernel，就能看到每一個函式的進出**。function_graph tracer 在 v2.6.29 加入（fb52607afcd0），kprobe-based event tracer 在 v2.6.33（413d37d1eb69，Masami Hiramatsu）。

這章的定位：ftrace 是「讓程式跑給你看」最便宜的一招。它不取代讀原始碼，它幫你**剪枝**：88 個候選剪到 1 個，然後你再回去讀那 1 個。

## 先建立直覺

想像一棟大樓，每一扇門（每個函式入口）上方都預先裝好了一個監視器的**空支架**。支架是空的，所以平常大家進出完全不受影響。你想看某扇門，就把攝影機裝到那個支架上；看完拆掉，門又回到原狀。

```
編譯時：每個函式開頭都留一個 5 bytes 的「支架」

  anon_pipe_write:
      endbr64
      call __fentry__        ← 編譯器插的
      push %r15
      ...

開機時（ftrace_init）：把 5.8 萬個支架全部換成 nop

  anon_pipe_write:
      endbr64
      nopl 0x0(%rax,%rax,1)  ← 5 bytes nop，CPU 幾乎不花成本
      push %r15

你寫 tracefs 時：只把你要的那幾個支架改回 call

  echo anon_pipe_write > set_ftrace_filter
  echo function > current_tracer
      → anon_pipe_write 的 nop 被改成 call <ftrace trampoline>
      → 其他 5.8 萬個仍是 nop
```

這個比喻在一個地方會失真：攝影機（tracer）不只能看「誰進門」，function_graph 還會偷偷把「出門的路」改掉，讓函式返回時也經過攝影機。這部分在底層機制一節講。

> 如果你對 `objdump -d`、`.o.cmd` 裡的編譯旗標還不熟，先回看 [Ch 50 — 把編譯器當讀碼工具](./50-compiler-as-reading-tool.md)。

## 底層機制：ftrace 怎麼做到平常零成本

### 第一代：`-pg` 與 mcount

gprof 時代的 `gcc -pg` 會在每個函式的 prologue **之後**插一個 `call mcount`。user-space 的 gprof 用它記錄呼叫圖。kernel 借用這個機制：自己實作一個 `mcount`，在裡面記錄「誰呼叫了誰」。

問題有兩個：

1. `call mcount` 在 prologue 之後，暫存器裡的參數可能已經被搬走，tracer 想讀函式參數很麻煩。
2. 每個函式都多一次 call，即使沒人在 trace，整個 kernel 也慢下來。

### 第二代：`-mfentry`

gcc 4.6 加了 `-mfentry`：把呼叫放在函式的**第一條指令**、prologue 之前，呼叫的符號叫 `__fentry__`。這時參數還原封不動地在暫存器裡（x86_64 的 `%rdi`、`%rsi`……），tracer 可以直接讀。x86_64 的支援是 d57c5d51a301「ftrace/x86: Add support for -mfentry to x86_64」（Steven Rostedt，`git tag --contains` 最早在 v3.7）。

我們的 build 用了哪些旗標？直接看 `.o.cmd`：

```console
$ grep -o '\-mfentry\|-pg\|-mrecord-mcount' ~/code_reading/build/fs/.pipe.o.cmd | sort | uniq -c
      1 -mfentry
      1 -mrecord-mcount
      1 -pg
```

`-pg` 啟用插樁，`-mfentry` 決定插在哪、叫什麼名字，`-mrecord-mcount` 讓 gcc 把每個插樁點的位址記到一個叫 `__mcount_loc` 的 section（舊做法是 build 時另外跑 `scripts/recordmcount` 去掃 object file）。

`vmlinux` 檔案裡，這個 call 還是 call：

```console
$ objdump -d --no-show-raw-insn vmlinux --disassemble=anon_pipe_write | sed -n 7,10p
ffffffff815932c0 <anon_pipe_write>:
ffffffff815932c0:	endbr64
ffffffff815932c4:	call   ffffffff812722f0 <__fentry__>
ffffffff815932c9:	push   %r15
```

（`endbr64` 是 CET/IBT 的間接跳轉落點標記，和 ftrace 無關；它占 4 bytes，所以插樁點在 `+4`。後面 kprobe 輸出裡的 `anon_pipe_write+0x4` 就是這個位置。）

### 第三代：dynamic ftrace 與 nop patching

所有插樁點的位址被 linker 收集在 `__start_mcount_loc` 到 `__stop_mcount_loc` 之間：

```console
$ grep -i mcount_loc ~/code_reading/build/System.map
ffffffff83be8d40 D __start_mcount_loc
ffffffff83c5b788 D __stop_mcount_loc
```

兩者相減是 0x72A48 bytes，每個位址 8 bytes，等於 58697 個插樁點。開機時 `ftrace_init()` 走過這張表：

```c
/* kernel/trace/ftrace.c:7933（v6.18） */
void __init ftrace_init(void)
{
	extern unsigned long __start_mcount_loc[];
	extern unsigned long __stop_mcount_loc[];
	unsigned long count, flags;
	int ret;
	...
	count = __stop_mcount_loc - __start_mcount_loc;
	...
	ret = ftrace_process_locs(NULL,
				  __start_mcount_loc,
				  __stop_mcount_loc);
```

`ftrace_process_locs()` 為每個位址建一筆 `dyn_ftrace` 記錄，並把那 5 bytes 改成 nop。guest 的 dmesg 印出了實際筆數（用 [Ch 52](./52-qemu-gdb-kernel.md) 的 `lx-dmesg` 抓到）：

```
[    0.149654] ftrace: allocating 58111 entries in 228 pages
[    0.149984] ftrace: allocated 228 pages with 4 groups
```

58111 比 58697 少，是因為有些位址在處理時被濾掉；開機完成後 `available_filter_functions` 又更少（55855，見下一節），因為 `__init` 函式所在的記憶體在開機後被釋放，它們的記錄也跟著移除。三個數字對不齊是正常的，重點是**數量級**：五萬多個可以 trace 的函式。

改程式碼這件事本身由 `arch/x86/kernel/ftrace.c` 負責。開機早期用 `text_poke_early()` 直接寫；系統跑起來之後，多顆 CPU 可能正在執行那段程式碼，所以改用 `smp_text_poke_batch_add()` 等函式，背後是「先寫 int3、同步所有 CPU、再寫剩下的 bytes、最後換掉 int3」的安全改寫流程：

```c
/* arch/x86/kernel/ftrace.c:112（v6.18） */
static int __ref
ftrace_modify_code_direct(unsigned long ip, const char *old_code,
			  const char *new_code)
{
	int ret = ftrace_verify_code(ip, old_code);
	if (ret)
		return ret;

	/* replace the text with the new text */
	if (ftrace_poke_late)
		smp_text_poke_batch_add((void *)ip, new_code, MCOUNT_INSN_SIZE, NULL);
	else
		text_poke_early((void *)ip, new_code, MCOUNT_INSN_SIZE);
	return 0;
}
```

> 注意函式名：如果你讀的是幾年前的文章或問 LLM，會看到 `text_poke_bp()`。那個名字在 v6.16 被改成 `smp_text_poke_single()`（commit 9586ae48e785）。這正是 [Ch 53](./53-llm-assisted-reading.md) 要談的「記憶過時」。

[Ch 52](./52-qemu-gdb-kernel.md) 會用 gdb 直接讀執行中 guest 的記憶體，看到 `anon_pipe_write+4` 真的變成了 `nopl 0x0(%rax,%rax,1)`——和上面 `vmlinux` 檔案裡的 `call __fentry__` 不一樣。這是 nop patching 最直接的證據。

### function_graph 怎麼知道函式何時返回

function tracer 有入口就夠了。function_graph 要算每個函式花多久，所以也需要**出口**。做法是在入口時偷換返回位址：

```c
/* arch/x86/kernel/ftrace.c:634（v6.18） */
void prepare_ftrace_return(unsigned long ip, unsigned long *parent,
			   unsigned long frame_pointer)
{
	unsigned long return_hooker = (unsigned long)&return_to_handler;

	if (unlikely(skip_ftrace_return()))
		return;

	if (!function_graph_enter(*parent, ip, frame_pointer, parent))
		*parent = return_hooker;
}
```

`*parent` 是 stack 上的返回位址。`function_graph_enter()` 把原本的返回位址存進當前 task 的一個影子堆疊（shadow return stack），再把 stack 上的返回位址換成 `return_to_handler`。函式 `ret` 的時候跳進 `return_to_handler`，它記錄時間、從影子堆疊取回真正的返回位址、跳回去。v6.18 的 x86 實際走的是同檔案第 647 行的 `ftrace_graph_func()`（`CONFIG_HAVE_DYNAMIC_FTRACE_WITH_ARGS` 的版本），邏輯相同。

這個設計的代價：被 trace 的函式每次返回都多一次間接跳轉，而且會干擾 CPU 的返回位址預測（return stack buffer），所以 function_graph 比 function tracer 貴得多。

## tracefs 介面速查

tracefs 掛在 `/sys/kernel/tracing`（舊的 `/sys/kernel/debug/tracing` 仍然存在，指向同一個東西）。我們的 guest init 已經掛好了。

| 檔案 | 作用 | 寫法範例 |
|---|---|---|
| `current_tracer` | 選 tracer：`nop`、`function`、`function_graph` | `echo function_graph > current_tracer` |
| `available_filter_functions` | 能 trace 的函式清單 | `grep anon_pipe available_filter_functions` |
| `set_ftrace_filter` | function tracer 只 trace 這些函式（可用 glob） | `echo '*pipe_write*' > set_ftrace_filter` |
| `set_ftrace_notrace` | 排除這些函式 | `echo '*spin_lock*' > set_ftrace_notrace` |
| `set_graph_function` | function_graph 只從這些函式開始畫 | `echo ksys_write > set_graph_function` |
| `max_graph_depth` | 呼叫樹最多展開幾層，0 是不限 | `echo 3 > max_graph_depth` |
| `tracing_on` | 開關記錄（不改 tracer 設定） | `echo 0 > tracing_on` |
| `trace` | 讀 ring buffer 快照；寫空字串清空 | `cat trace`、`echo > trace` |
| `trace_pipe` | 邊產生邊讀，讀過就消失，會 block | `cat trace_pipe` |
| `options/func_stack_trace` | function tracer 每筆附 stack trace | `echo 1 > options/func_stack_trace` |
| `options/funcgraph-irqs` | function_graph 是否顯示中斷內的函式 | `echo 0 > options/funcgraph-irqs` |
| `kprobe_events` | 動態定義 kprobe 事件 | 見實戰四 |
| `events/` | 所有 tracepoint 事件 | `echo 1 > events/sched/sched_process_exec/enable` |
| `enabled_functions` | 目前被改成 call 的插樁點 | `wc -l < enabled_functions` |

`trace` 和 `trace_pipe` 的差別常被搞混：`trace` 是**不消耗**的快照，讀幾次都一樣；`trace_pipe` 是**消耗型**的串流，適合邊跑邊看，但我們的 guest 是「跑完腳本就關機」，所以本章一律用 `trace`。

用一個實驗確認「沒開 tracer 時一個插樁點都沒啟用、開了之後才改」：

```sh
#!/bin/sh
cd /sys/kernel/tracing
echo "=== available_filter_functions: $(wc -l < available_filter_functions)"
echo "=== enabled_functions (before): $(wc -l < enabled_functions)"
echo function_graph > current_tracer
echo anon_pipe_write > set_graph_function
echo "=== enabled_functions (graph on): $(wc -l < enabled_functions)"
```

```
=== available_filter_functions: 55855
=== enabled_functions (before): 0
=== enabled_functions (graph on): 55855
```

注意最後一行：`set_graph_function` **不會**減少被 patch 的插樁點，function_graph 仍然把五萬多個點全部打開，只是在記錄時過濾「不在 `anon_pipe_write` 底下的就丟掉」。想真正減少開銷要用 `set_ftrace_filter`。這是 `set_graph_function` 和 `set_ftrace_filter` 最重要的差別。

## 實戰一：function_graph 回答「`f_op->write_iter` 到底呼叫了誰」

### 準備觸發程式

guest 只有 busybox，我們自己寫一個靜態連結的小程式，做三件事：開 pipe、寫 6 bytes、讀回來再寫到 stdout。

```c
/* pipew.c: 建一條 pipe，寫 6 bytes、讀回來，再寫一行到 stdout */
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(void)
{
	int fds[2];
	char buf[16] = {0};

	if (pipe(fds) < 0) { perror("pipe"); return 1; }
	if (write(fds[1], "hello\n", 6) != 6) { perror("write"); return 1; }
	if (read(fds[0], buf, sizeof(buf) - 1) < 0) { perror("read"); return 1; }
	write(1, buf, strlen(buf));          /* 這次 write 走的是 tty，不是 pipe */
	return 0;
}
```

```console
$ gcc -static -O2 -o pipew pipew.c && ./pipew
hello
```

`-static` 是必要的：guest 裡沒有 glibc 的動態連結器。

### guest 腳本

```sh
#!/bin/sh
cd /sys/kernel/tracing
echo 0 > tracing_on
echo function_graph > current_tracer
echo ksys_write > set_graph_function
echo 3 > max_graph_depth
echo > trace
echo 1 > tracing_on
/bin/pipew
echo 0 > tracing_on
cat trace
```

用 `~/code_reading/qemu/run-script.sh g1.sh pipew` 跑（腳本當 `/run.sh`，`pipew` 放進 guest 的 `/bin`，跑完自動關機）。實際輸出（去掉表頭）：

```
 0)               |  mutex_unlock() {
 0) ! 279.131 us  |    irq_enter_rcu();
 0)               |    __sysvec_apic_timer_interrupt() {
 0) ! 183.723 us  |      hrtimer_interrupt();
 0) ! 194.683 us  |    }
 ...
 0) # 1181.747 us |  }
 1)               |  ksys_write() {
 1)   4.478 us    |    fdget_pos();
 1)               |    vfs_write() {
 1) + 15.870 us   |      rw_verify_area();
 1) ! 285.032 us  |      anon_pipe_write();
 1) ! 343.050 us  |    }
 1) ! 385.940 us  |  }
 1)               |  ksys_write() {
 1)   2.344 us    |    fdget_pos();
 1)               |    vfs_write() {
 1) + 13.034 us   |      rw_verify_area();
 1) # 2666.467 us |      redirected_tty_write();
 1) # 2707.242 us |    }
 1) # 2719.696 us |  }
 0)               |  ksys_write() {
 0)   3.447 us    |    fdget_pos();
 0)               |    vfs_write() {
 0)   4.529 us    |      rw_verify_area();
 0)   0.822 us    |      __cond_resched();
 0)   1.232 us    |      mutex_lock();
```

### 逐段解讀

**欄位**：最左是 CPU 編號；`DURATION` 是函式花的時間，只在函式「關閉」的那一行出現；前面的符號是時間等級：`+` 超過 10 µs、`!` 超過 100 µs、`#` 超過 1000 µs。右邊用 C 語法的大括號畫出呼叫樹，葉子函式（底下沒有被記錄的呼叫）縮成一行 `foo();`。

**第一個 `ksys_write`**（CPU 1）：`write(fds[1], "hello\n", 6)`。`vfs_write()` 底下是 `anon_pipe_write()`。這就是答案：**這次 `filp->f_op->write_iter` 指向 `anon_pipe_write`**。對照原始碼，v6.18 匿名 pipe 的 ops table 是：

```c
/* fs/pipe.c:1257（v6.18） */
static const struct file_operations pipeanon_fops = {
	.open		= fifo_open,
	.read_iter	= anon_pipe_read,
	.write_iter	= anon_pipe_write,
	.poll		= pipe_poll,
	.unlocked_ioctl	= pipe_ioctl,
	.release	= pipe_release,
	.fasync		= pipe_fasync,
	.splice_write	= iter_file_splice_write,
};
```

**第二個 `ksys_write`**：`write(1, buf, ...)`，stdout 是 guest 的 serial console。同一個呼叫點，這次跳到 `redirected_tty_write()`——它是 `console_fops` 的 `.write_iter`（`drivers/tty/tty_io.c:475`）。花了 2.6 ms，因為 TCG 模擬的 UART 很慢。

**第三個 `ksys_write`**（CPU 0，沒有結尾）：這不是 `pipew` 的。它是腳本下一行 `echo 0 > tracing_on`——**shell 寫 tracefs 本身也是一次 `write(2)`**，寫到一半 tracing 被關掉，所以只記到一半。每次用 `echo 0 > tracing_on` 收尾，最後都會看到這種半截的紀錄，別把它當成你的程式的行為。

**最前面的 `mutex_unlock() { ... irq ... }`**：這是 `set_graph_function` 生效前就已經在跑的東西被中斷打斷的殘留，加上 function_graph 預設會把中斷處理也畫進來。用 `echo 0 > options/funcgraph-irqs` 可以關掉，下面的實驗都會關。

### 少了什麼？內聯的 `new_sync_write`

讀 `vfs_write()` 原始碼會發現它不是直接呼叫 `write_iter`：

```c
/* fs/read_write.c:683（v6.18） */
	if (file->f_op->write)
		ret = file->f_op->write(file, buf, count, pos);
	else if (file->f_op->write_iter)
		ret = new_sync_write(file, buf, count, pos);
	else
		ret = -EINVAL;
```

```c
/* fs/read_write.c:583（v6.18） */
static ssize_t new_sync_write(struct file *filp, const char __user *buf, size_t len, loff_t *ppos)
{
	struct kiocb kiocb;
	struct iov_iter iter;
	ssize_t ret;

	init_sync_kiocb(&kiocb, filp);
	kiocb.ki_pos = (ppos ? *ppos : 0);
	iov_iter_ubuf(&iter, ITER_SOURCE, (void __user *)buf, len);

	ret = filp->f_op->write_iter(&kiocb, &iter);
```

真正的間接呼叫點在 `new_sync_write()` 的第 593 行。但 trace 裡沒有 `new_sync_write`，因為它是 `static`、只有一個呼叫者，gcc 把它內聯進 `vfs_write()` 了；內聯後就沒有自己的函式入口，自然也沒有 `__fentry__`。`available_filter_functions` 裡也找不到它：

```console
# grep -E '^(new_sync_write|vfs_write|ksys_write|__x64_sys_write|anon_pipe_write|fifo_pipe_write)( |$)' available_filter_functions
vfs_write
ksys_write
__x64_sys_write
anon_pipe_write
fifo_pipe_write
```

**ftrace 看到的是編譯後的函式，不是原始碼裡的函式**。要看到內聯的那一層，得用 [Ch 52](./52-qemu-gdb-kernel.md) 的 gdb：DWARF 記錄了內聯資訊，backtrace 會把 `new_sync_write` 標成內聯 frame。

### 同一個呼叫點，換一種檔案

把觸發程式換成寫具名 FIFO（`mkfifo` 之後 fork 一個讀者、父行程 open 寫端寫 5 bytes），並關掉中斷顯示：

```sh
echo 0 > options/funcgraph-irqs
echo function_graph > current_tracer
echo ksys_write > set_graph_function
echo 3 > max_graph_depth
```

```
 1)               |  ksys_write() {
 1)   3.426 us    |    fdget_pos();
 1)               |    vfs_write() {
 1)   4.068 us    |      rw_verify_area();
 1) ! 551.800 us  |      fifo_pipe_write();
 1) ! 588.359 us  |    }
 1) ! 622.652 us  |  }
```

這次是 `fifo_pipe_write()`——`pipefifo_fops` 的 `.write_iter`（`fs/pipe.c:1249`）。它在 `fs/pipe.c:604` 包了一層 `anon_pipe_write()`，額外更新檔案時間戳。這個差異的來由可以用 [Ch 37](./37-git-archaeology.md) 的方法查：`git log -S'anon_pipe_write' -- fs/pipe.c` 指向 f017b0a4951f「pipe: don't update {a,c,m}time for anonymous pipes」（Oleg Nesterov，v6.15）。在那之前，匿名 pipe 與 FIFO 共用同一組 `pipe_read`/`pipe_write`。

三次實驗、同一行 `filp->f_op->write_iter(&kiocb, &iter)`，三個不同的目標：`anon_pipe_write`、`fifo_pipe_write`、`redirected_tty_write`。這就是間接鴻溝的本質，也是動態觀察的價值。

## 實戰二：展開一個函式的內部

知道目標是 `anon_pipe_write` 之後，下一個問題是「它裡面做了什麼」。把 `set_graph_function` 設成它、不限深度：

```
 0)               |  anon_pipe_write() {
 0)               |    mutex_lock() {
 0) + 20.960 us   |      __cond_resched();
 0) + 36.628 us   |    }
 0)               |    alloc_pages_noprof() {
 0)               |      alloc_pages_mpol() {
 0)   2.315 us    |        policy_nodemask();
 0)               |        __alloc_frozen_pages_noprof() {
 0)   0.732 us    |          __cond_resched();
 0)               |          get_page_from_freelist() {
 0)   1.412 us    |            _raw_spin_trylock();
 0)   0.801 us    |            _raw_spin_unlock();
 0) + 11.782 us   |          }
 0) + 18.975 us   |        }
 0) + 25.678 us   |      }
 0) + 29.886 us   |    }
 0)   1.553 us    |    mutex_unlock();
 0)               |    __wake_up_sync_key() {
 0)   0.782 us    |      _raw_spin_lock_irqsave();
 0)   1.724 us    |      __wake_up_common();
 0)   2.093 us    |      _raw_spin_unlock_irqrestore();
 0) + 13.465 us   |    }
 0)   3.076 us    |    kill_fasync();
 0) ! 163.846 us  |  }
```

對照 `fs/pipe.c:431-601`，這條路徑就是「空 pipe、寫一小筆」的 happy path：

| trace | 原始碼 | 說明 |
|---|---|---|
| `mutex_lock()` | 458 `mutex_lock(&pipe->mutex);` | |
| `alloc_pages_noprof()` | 515 `anon_pipe_get_page(pipe)` | `anon_pipe_get_page` 被內聯，看到的是它裡面的 `alloc_page()`；`_noprof` 後綴來自記憶體配置 profiling（`alloc_hooks`）的包裝 |
| （看不到） | 522 `copy_page_from_iter(...)` | 見下方踩雷 |
| `mutex_unlock()` | 581 | 第 478 行的合併分支沒走（pipe 原本是空的） |
| `__wake_up_sync_key()` | 596 `wake_up_interruptible_sync_poll(&pipe->rd_wait, ...)` | 巨集展開後的實際函式 |
| `kill_fasync()` | 597 | |

trace 也告訴你**沒發生**的事：沒有 `wait_event_interruptible_exclusive`（573 行），表示這次 pipe 沒滿、writer 沒睡；沒有 `send_sig`，表示有 reader。讀碼時，「哪些分支這次沒走」和「哪些走了」一樣有用。

拷貝資料的 `copy_page_from_iter()` 去哪了？它不是內聯，`System.map` 裡有 `T copy_page_from_iter`。原因在 `lib/Makefile` 第 6 行：

```make
# lib/Makefile:6（v6.18）
ccflags-remove-$(CONFIG_FUNCTION_TRACER) += $(CC_FLAGS_FTRACE)
```

整個 `lib/` 目錄編譯時拿掉了 `-pg -mfentry`，所以 `lib/iov_iter.c` 裡的函式沒有插樁點，`objdump` 可以確認它開頭只有 `endbr64; push %r15`，沒有 `call __fentry__`。另外還有 `notrace` 屬性、`noinstr` section 等機制會讓函式從 trace 消失。**trace 裡沒出現，不等於沒被呼叫**。

## 實戰三：function tracer + stacktrace 找呼叫者

function_graph 擅長往下看。反過來的問題——「`anon_pipe_write` 是從哪條路被叫到的」——用 function tracer 加 `func_stack_trace` 比較划算：

```sh
echo anon_pipe_write > set_ftrace_filter
echo function > current_tracer
echo 1 > options/func_stack_trace
echo > trace
echo 1 > tracing_on
/bin/pipew > /dev/null
echo 0 > tracing_on
echo 0 > options/func_stack_trace
cat trace
```

```
           pipew-74      [001] .....    34.535390: anon_pipe_write <-vfs_write
           pipew-74      [001] .....    34.536466: <stack trace>
 => [FTRACE TRAMPOLINE]
 => anon_pipe_write
 => vfs_write
 => ksys_write
 => do_syscall_64
 => entry_SYSCALL_64_after_hwframe
```

第一行的 `anon_pipe_write <-vfs_write` 是 function tracer 本來就有的「被誰呼叫」（從返回位址推的，所以是內聯後的 `vfs_write` 而不是 `new_sync_write`）。stack trace 給出完整路徑，最上面的 `[FTRACE TRAMPOLINE]` 是 ftrace 自己的跳板。

這招在「一個函式有很多呼叫者、不知道這次是哪個」時特別有用，例如 `kfree`、`wake_up_process`。務必配 `set_ftrace_filter` 縮到一兩個函式，否則每一筆都附 stack trace，ring buffer 幾毫秒就滿了。

## 實戰四：kprobe events 抓參數

function tracer 告訴你「誰被呼叫」，不告訴你參數。kprobe events 讓你在任意函式入口（或內部位址）定義一個事件，順便抓暫存器、記憶體、函式參數。語法定義在 `Documentation/trace/kprobetrace.rst`：

```
p[:[GRP/][EVENT]] [MOD:]SYM[+offs]|MEMADDR [FETCHARGS]   進入時
r[MAXACTIVE][:[GRP/][EVENT]] [MOD:]SYM[+0] [FETCHARGS]   返回時
```

FETCHARGS 常用的有：`$argN`（第 N 個參數）、`$retval`（返回值，限 `r`）、`$comm`、`+OFFS(FETCHARG)`（解參考，取 FETCHARG + OFFS 處的記憶體），加上 `:u64`、`:s64`、`:string`、`:symbol` 等型別。

要解參考 struct 欄位，得知道 offset。用 [Ch 50](./50-compiler-as-reading-tool.md) 的 pahole：

```console
$ pahole -C file vmlinux | grep f_op
	const struct file_operations  * f_op;            /*     8     8 */
$ pahole -C file_operations vmlinux | grep write_iter
	ssize_t                    (*write_iter)(struct kiocb *, struct iov_iter *); /*    48     8 */
$ pahole -C iov_iter vmlinux | grep count
			size_t     count;                /*    24     8 */
```

所以 `file->f_op->write_iter` 是 `+48(+8($arg1))`，`iov_iter->count` 是 `+24($arg2)`。guest 腳本：

```sh
cd /sys/kernel/tracing
echo 'p:myprobe/vw vfs_write file=$arg1 count=$arg3:u64 op=+48(+8($arg1)):symbol' > kprobe_events
echo 'p:myprobe/pw anon_pipe_write len=+24($arg2):u64 comm=$comm' >> kprobe_events
echo 'r:myprobe/pwret anon_pipe_write ret=$retval:s64' >> kprobe_events
echo 'p:myprobe/btf vfs_write file->f_op->write_iter:symbol count' >> kprobe_events
cat kprobe_events
echo 'comm == "pipew"' > events/myprobe/vw/filter
echo 'comm == "pipew"' > events/myprobe/btf/filter
echo 1 > events/myprobe/enable
echo > trace
echo 1 > tracing_on
/bin/pipew > /dev/null
echo 0 > tracing_on
echo 0 > events/myprobe/enable
cat trace
echo > kprobe_events
```

輸出：

```
p:myprobe/vw vfs_write file=$arg1 count=$arg3:u64 op=+48(+8($arg1)):symbol
p:myprobe/pw anon_pipe_write len=+24($arg2):u64 comm=$comm
r10:myprobe/pwret anon_pipe_write ret=$retval:s64
p:myprobe/btf vfs_write arg1=file->f_op->write_iter:symbol count=count
           pipew-80      [001] .....    34.935639: btf: (vfs_write+0x4/0x420) arg1=anon_pipe_write+0x0/0x660 count=6
           pipew-80      [001] .....    34.935741: vw: (vfs_write+0x4/0x420) file=0xffff8880056a4780 count=6 op=anon_pipe_write+0x0/0x660
           pipew-80      [001] .....    34.935954: pw: (anon_pipe_write+0x4/0x660) len=6 comm="pipew"
           pipew-80      [001] .....    34.936275: pwret: (vfs_write+0x374/0x420 <- anon_pipe_write) ret=6
           pipew-80      [001] .....    34.936558: btf: (vfs_write+0x4/0x420) arg1=write_iter_null+0x0/0x30 count=6
           pipew-80      [001] .....    34.936564: vw: (vfs_write+0x4/0x420) file=0xffff888004f04300 count=6 op=write_iter_null+0x0/0x30
```

幾個值得停下來看的地方：

- **`op=anon_pipe_write+0x0/0x660`**：我們在 `vfs_write` 的**入口**、還沒走到間接呼叫之前，就從 `file->f_op->write_iter` 把目標讀出來了。這是比 function_graph 更精準的「間接呼叫解析器」：你問的就是那個指標的值。
- **第二組 `write_iter_null`：一個陷阱**。`pipew > /dev/null` 讓 stdout 變成 `/dev/null`，probe 讀到它的 `.write_iter` 是 `write_iter_null`。但這**不代表** `write_iter_null` 會被呼叫。看 `/dev/null` 的 ops table：

  ```c
  /* drivers/char/mem.c:644（v6.18） */
  static const struct file_operations null_fops = {
  	.llseek		= null_lseek,
  	.read		= read_null,
  	.write		= write_null,
  	.read_iter	= read_iter_null,
  	.write_iter	= write_iter_null,
  	.splice_write	= splice_write_null,
  	.uring_cmd	= uring_cmd_null,
  };
  ```

  它**同時**有 `.write` 和 `.write_iter`，而 `vfs_write()` 第 683 行先檢查 `.write`（見實戰一「少了什麼？」那段引用的原始碼）。用 function_graph 實際跑一次 `pipew > /dev/null`：

  ```
   1)               |  ksys_write() {
   1)   1.933 us    |    fdget_pos();
   1)               |    vfs_write() {
   1)   4.739 us    |      rw_verify_area();
   1)   3.687 us    |      write_null();
   1) + 18.074 us   |    }
   1) + 25.898 us   |  }
  ```

  真正被呼叫的是 `write_null`。kprobe 忠實地回答了你問的問題（「`write_iter` 欄位的值是多少」），但你問錯了問題。讀一個欄位的值之前，先讀懂**分派邏輯**決定用哪個欄位。寫這章時作者本人就差點把 `write_iter_null` 當成答案，是對照 function_graph 才發現的。
- **`btf` 那一行**：`CONFIG_PROBE_EVENTS_BTF_ARGS=y` 時，kprobe events 可以用 BTF 型別資訊，直接寫 `file->f_op->write_iter` 而不用自己算 offset，也可以用參數名 `count` 代替 `$arg3`。kernel 回寫的定義把它正規化成 `arg1=file->f_op->write_iter:symbol count=count`。手算 offset 的版本仍然值得會：沒有 BTF 的 kernel、或要讀內聯後的區域變數時，你只剩暫存器和 offset。
- **`r10:`**：kretprobe 的 MAXACTIVE 預設值（同時能追蹤幾個尚未返回的呼叫），我們沒指定，kernel 填了 10。
- **`(vfs_write+0x4/0x420)`**：probe 位置在 `+4`，正好是 `__fentry__` 插樁點。x86 上，如果 kprobe 打在 ftrace 插樁點，kernel 會直接借用 ftrace 機制，而不是寫 int3。
- **`pwret` 的 `vfs_write+0x374`**：返回到 `vfs_write` 裡第 0x374 bytes 的地方，又一次證明 `new_sync_write` 被內聯了。

## trace events：kernel 作者預先埋好的觀察點

kprobe 是你臨時在任意位置打洞；tracepoint 是 kernel 作者在程式碼裡**預先**放好、有穩定欄位定義的觀察點。guest 裡有 113 個子系統目錄、1603 個事件：

```console
# ls events | wc -l
113
# cat available_events | wc -l
1603
# cat events/sched/sched_process_exec/format
name: sched_process_exec
ID: 103
format:
	field:unsigned short common_type;	offset:0;	size:2;	signed:0;
	field:unsigned char common_flags;	offset:2;	size:1;	signed:0;
	field:unsigned char common_preempt_count;	offset:3;	size:1;	signed:0;
	field:int common_pid;	offset:4;	size:4;	signed:1;

	field:__data_loc char[] filename;	offset:8;	size:4;	signed:0;
	field:pid_t pid;	offset:12;	size:4;	signed:1;
	field:pid_t old_pid;	offset:16;	size:4;	signed:1;

print fmt: "filename=%s pid=%d old_pid=%d", __get_str(filename), REC->pid, REC->old_pid
```

`format` 檔案本身就是讀碼素材：它告訴你這個事件記錄了哪些欄位、型別、怎麼印。對應的原始碼是 `include/trace/events/sched.h` 裡的 `TRACE_EVENT(sched_process_exec, ...)` 巨集（巨集元程式見 [Ch 14](./14-macro-metaprogramming.md)）。

我們的 `.config` 沒開 `CONFIG_FTRACE_SYSCALLS`，所以沒有 `events/syscalls/sys_enter_write` 這類逐 syscall 的事件，只有通用的 `raw_syscalls`。用 filter 只留 write（1）、pipe（22）、pipe2（293）：

```sh
echo 'id == 1 || id == 22 || id == 293' > events/raw_syscalls/sys_enter/filter
echo 1 > events/raw_syscalls/sys_enter/enable
echo 1 > events/sched/sched_process_exec/enable
echo 1 > events/sched/sched_process_exit/enable
```

```
           pipew-91      [001] .....    35.149786: sched_process_exec: filename=/bin/pipew pid=91 old_pid=91
           pipew-91      [001] .....    35.151661: sys_enter: NR 293 (7fff3c244a08, 0, 7fff3c244c28, 80, 4c7d70, 4)
           pipew-91      [001] .....    35.151811: sys_enter: NR 1 (4, 498004, 6, 80, 4c7d70, 4)
           pipew-91      [001] .....    35.151954: sys_enter: NR 1 (1, 7fff3c244a10, 6, 80, 4c7d70, 4)
           pipew-91      [001] .....    35.152241: sched_process_exit: comm=pipew pid=91 prio=120 group_dead=true
              sh-70      [000] .....    35.154250: sys_enter: NR 1 (1, 1fe96920, 2, 0, fefefefefefefeff, ffffffffffff0000)
```

一個小發現：我們的程式呼叫的是 `pipe()`，但 kernel 看到的是 syscall 293（`pipe2`），flags 為 0。這是 glibc 的實作選擇，從原始碼讀 `pipew.c` 看不出來。最後一行 `sh-70` 是 busybox shell 在 `pipew` 結束後的輸出，因為 filter 沒限定行程。

tracepoint 和 kprobe 的取捨：tracepoint 欄位穩定、開銷低、跨版本可用，但只在作者想到的地方有；kprobe 哪裡都能打，但依賴函式名和 struct layout，換版本就可能壞（本章的 `pipe_write` 就是例子）。

## bpftrace：把 ftrace 的能力程式化

> **本段未實測，為理論預期行為。** guest 裡沒有 bpftrace（只有 busybox），WSL 主機也不適合（見下方）。驗證方式：在一台你有 root 的 Linux 機器（實體機或有 root 的 VM）上 `apt install bpftrace` 後執行。

ftrace 的限制是「記錄下來，事後看」。如果你想要「每個 comm 呼叫幾次 `vfs_read`」這種聚合，用 ftrace 得把每一筆都記下來再自己統計。bpftrace 讓你寫一小段程式，掛在 kprobe/tracepoint 上，在 kernel 內用 eBPF 直接聚合，只把結果送回 user space。

常用 one-liner（語法以 bpftrace 官方 reference guide 為準）：

```sh
# 每個行程呼叫 vfs_read 的次數，Ctrl-C 時印出
bpftrace -e 'kprobe:vfs_read { @[comm] = count(); }'

# anon_pipe_write 被誰呼叫：以 kernel stack 為 key 計數
bpftrace -e 'kprobe:anon_pipe_write { @[kstack] = count(); }'

# vfs_write 的 write_iter 指向誰（等同實戰四的 kprobe event，但直接聚合）
bpftrace -e 'kprobe:vfs_write { @[ksym(((struct file *)arg0)->f_op->write_iter)] = count(); }'

# anon_pipe_write 的延遲分布
bpftrace -e 'kprobe:anon_pipe_write { @s[tid] = nsecs; }
             kretprobe:anon_pipe_write /@s[tid]/ { @us = hist((nsecs - @s[tid]) / 1000); delete(@s[tid]); }'
```

`kstack` 會在 Ctrl-C 時把每個不同的 stack 印出來並附上次數，效果類似實戰三的 `func_stack_trace`，差別是 bpftrace 幫你合併了相同的 stack。第三個例子依賴 BTF 型別資訊解析 `struct file`，我們的 build 開了 `CONFIG_DEBUG_INFO_BTF=y`，理論上可用。

**為什麼不在 WSL 主機上跑**：我們沒有 sudo，而 tracefs 和 BPF 都需要特權。實測：

```console
$ uname -r
6.18.58.1-microsoft-standard-WSL2
$ ls /sys/kernel/tracing
ls: cannot open directory '/sys/kernel/tracing': Permission denied
$ cat /proc/sys/kernel/perf_event_paranoid
2
```

就算有 root，WSL2 的 kernel 是微軟的建置版本，config 和我們讀的 v6.18 tree 不同，觀察結果不能直接對照原始碼行號。這也是本課把所有動態實驗放進自建 QEMU guest 的原因：**被觀察的 kernel 必須和你讀的原始碼是同一份**。

## 對比與取捨

| 工具 | 回答的問題 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|---|
| 靜態搜尋（Ch 28、練習 G） | 可能呼叫誰 | 不用跑、涵蓋所有 config | 候選太多、不知道這次是誰 | 第一輪縮小範圍 |
| function_graph | 這次往下呼叫了誰、各花多久 | 零修改、看得到整棵樹 | 開銷大、看不到內聯和沒插樁的函式 | 「`f_op->xxx` 呼叫了誰」「這個函式裡面走了哪條路」 |
| function + stacktrace | 這次從哪裡被叫到 | 輕、聚焦 | 每筆一個 stack，量大會爆 | 找呼叫者 |
| kprobe events | 參數、欄位、返回值是多少 | 任意位置、能解參考 | 依賴函式名和 layout，換版本易壞 | 讀指標值、看分支條件 |
| tracepoints | 子系統定義好的事件 | 穩定、便宜 | 只在預設位置 | 長時間觀察、跨版本腳本 |
| bpftrace / eBPF | 聚合統計、條件邏輯 | 在 kernel 內聚合、表達力強 | 需要 root 與 BPF 支援、驗證器限制 | 生產環境、統計分布 |
| gdb（Ch 52） | 停在這裡時所有狀態 | 看得到內聯 frame 和任何變數 | 停住整個 kernel，時序被破壞 | 單步、深入一個點 |

## 踩雷集錦

1. **用舊函式名設 filter**：照著舊文章寫 `echo pipe_write > set_graph_function`，在 v6.18 上會失敗：

   ```
   sh: write error: Invalid argument
   exit=1
   ```

   v6.15 起匿名 pipe 的實作叫 `anon_pipe_write`。設 filter 前先 `grep` 一下 `available_filter_functions`；或用 glob `echo '*pipe_write*' > set_ftrace_filter`，實測會匹配 `anon_pipe_write`、`fifo_pipe_write`、`rpc_pipe_write` 三個。

2. **以為 trace 沒出現就是沒呼叫**：內聯（`new_sync_write`、`anon_pipe_get_page`）、整個目錄拿掉 `-pg`（`lib/` 的 `copy_page_from_iter`）、`notrace`、`noinstr` 都會讓函式從 trace 消失。有疑問就查 `available_filter_functions`，再用 `objdump` 看函式開頭有沒有 `call __fentry__`。

3. **把 TCG 下的時間當真**：我們的 QEMU 沒有 KVM，所有指令都是軟體模擬，加上 tracing 本身的開銷，`anon_pipe_write` 量出 163 µs 到 1.5 ms 不等。這些數字只能用來比較**同一次 trace 內**的相對大小，不能拿來說「pipe write 要 160 µs」。

4. **以為 `set_graph_function` 能降低開銷**：它只過濾**記錄**，function_graph 照樣把 55855 個插樁點全打開（實測 `enabled_functions` 從 0 變 55855）。要降開銷用 `set_ftrace_filter`，或改用 function tracer。

5. **讀到函式指標的值，就以為它會被呼叫**：`/dev/null` 的 `write_iter` 是 `write_iter_null`，但 `vfs_write` 優先用 `.write`，實際呼叫的是 `write_null`（實戰四）。kprobe/gdb 讀出的是**資料**，哪個指標會被用，要回去讀**控制流**。

6. **忘了清設定**：`set_graph_function`、`set_ftrace_filter`、`max_graph_depth`、`options/*`、`kprobe_events` 都是全域狀態，下一個實驗會繼承。養成習慣：每個實驗結尾 `echo nop > current_tracer`、`echo > set_ftrace_filter`、`echo > set_graph_function`、`echo 0 > max_graph_depth`、`echo > kprobe_events`。

7. **把最後一筆半截紀錄當成程式行為**：`echo 0 > tracing_on` 本身是一次 `write(2)`，關閉 tracing 的那一刻它還在 `vfs_write` 裡，所以 trace 結尾總有一個沒收尾的 `ksys_write`。

## 進階：再往深一層

- **trace-cmd 與 KernelShark**：tracefs 的命令列前端，`trace-cmd record -p function_graph -g ksys_write ./pipew` 一行做完本章的 echo 序列，還能存檔給 KernelShark 圖形化。需要在 guest 裡另外放靜態編譯的 trace-cmd，本章沒做。
- **`perf ftrace`**：perf 也有 ftrace 前端（`perf ftrace -G vfs_write -- ./pipew`），WSL 主機有 `/usr/bin/perf`，但同樣需要權限。
- **`CONFIG_FUNCTION_GRAPH_RETVAL`**：開了之後 function_graph 可以印每個函式的返回值（`options/funcgraph-retval`）。我們的 build 沒開（`.config` 裡是 `# CONFIG_FUNCTION_GRAPH_RETVAL is not set`），想要的話重編 kernel。
- **fprobe events**（`Documentation/trace/fprobetrace.rst`）：基於 ftrace 的函式入口/出口探針，比 kprobe 輕，語法相近。我們的 build 沒開 `CONFIG_FPROBE`。
- **開機期間 trace**：kernel command line 的 `ftrace=function_graph ftrace_graph_filter=...` 可以從開機就開始記錄，對追 init 路徑很有用（參數說明在 `Documentation/admin-guide/kernel-parameters.txt`）。
- **原始碼入口**：想讀 ftrace 自己的實作，從 `kernel/trace/ftrace.c` 的 `ftrace_init()`、`ftrace_replace_code()`，和 `kernel/trace/fgraph.c` 的 `function_graph_enter_regs()` 開始，配合 `arch/x86/kernel/ftrace_64.S` 的 trampoline。

## 動手練習

1. 把實戰一的 `set_graph_function` 換成 `ksys_read`，觸發程式不變。預測你會看到幾個 `ksys_read`、各自的 `read_iter` 是誰，再跑一次驗證。（提示：`pipew` 只 read 了 pipe 一次，但 shell 呢？）
2. 用 kprobe event 抓 `anon_pipe_write` 的 `iocb->ki_flags`（先用 pahole 查 `struct kiocb` 的 offset），確認一般 `write(2)` 時 `IOCB_NOWAIT` 沒有被設。
3. 把 `pipew` 改成連續寫 70000 bytes 到 pipe 而不讀（pipe 預設 16 個 buffer，每個一頁），用 function_graph 看 `anon_pipe_write` 什麼時候走進 `wait_event_interruptible_exclusive` 的路徑。你需要一個背景 reader 讓程式結束，否則 guest 會卡到 timeout。
4. 在 `objdump -d vmlinux` 裡挑三個 `lib/` 底下的函式、三個 `fs/` 底下的函式，比較開頭有沒有 `call __fentry__`，再到 guest 的 `available_filter_functions` 驗證你的判斷。

## 本章重點整理

- ftrace 靠 `-pg -mfentry` 在每個函式開頭留一個 `call __fentry__`，開機時全改成 5 bytes nop，用的時候才 patch 回 call；所以平常幾乎零成本。
- function_graph 在入口偷換返回位址來量出口時間，代價比 function tracer 高；`set_graph_function` 只過濾記錄，不減少 patch。
- 動態觀察回答的是「**這次**呼叫了誰」：同一行 `filp->f_op->write_iter()`，實測跳到 `anon_pipe_write`、`fifo_pipe_write`、`redirected_tty_write`；而 `/dev/null` 根本不走這一行，`vfs_write` 先選了 `.write = write_null`。
- kprobe events 能在間接呼叫**之前**就讀出函式指標的值（`+48(+8($arg1)):symbol` 或 BTF 的 `file->f_op->write_iter`）。
- trace 看的是編譯後的世界：內聯、沒插樁、改名的函式都會讓你「看不到」，這時回到原始碼和 gdb。

## 自我檢核

- [ ] 不看筆記，畫出一個函式從「編譯」到「開機」到「被 `echo function > current_tracer` 打開」的入口 5 bytes 變化。
- [ ] 有人說「function_graph 會拖慢 kernel，所以只要用 `set_graph_function` 縮小範圍就好」，你怎麼糾正他？
- [ ] 解釋為什麼實戰一的 trace 裡看不到 `new_sync_write`，以及你會用什麼方法確認它真的存在於呼叫路徑上。
- [ ] 不翻書，寫出一個 kprobe event 定義：在某函式入口抓第一個參數指向的 struct 裡 offset 16 的 u32 欄位。
- [ ] 想一個你最近讀不懂的 ops table 呼叫點（不限 kernel），描述你會怎麼用本章的工具在 5 分鐘內找出實際目標。
- [ ] 說出 tracepoint 和 kprobe 各一個「只有它能做到」的情境。

## 延伸閱讀

### 官方文件

- **[ftrace - Function Tracer](https://docs.kernel.org/trace/ftrace.html)**（v6.18 tree：`Documentation/trace/ftrace.rst`）
  - **讀哪裡**：「The File System」一節逐一解釋 tracefs 每個檔案；「function graph tracer」一節解釋輸出欄位與時間符號（`+`、`!`、`#`）；「dynamic ftrace」一節講 nop patching 的設計。
  - **能學到什麼**：本章 tracefs 速查表的完整版，以及每個 `options/` 開關的語意。
  - **前提知識**：會用 shell 讀寫檔案即可；dynamic ftrace 那節需要一點 x86 指令編碼概念。
- **[Kprobe-based Event Tracing](https://docs.kernel.org/trace/kprobetrace.html)**（`Documentation/trace/kprobetrace.rst`）
  - **讀哪裡**：「Synopsis of kprobe_events」的 FETCHARGS 語法表與「Types」一節；本章實戰四的每一個 fetcharg 都出自這裡。
  - **能學到什麼**：`$stackN`、`@SYM`、`:string`、`:ustring`、bitfield 型別等本章沒用到的抓取方式。
  - **前提知識**：知道 x86_64 呼叫慣例（參數依序在 `%rdi`、`%rsi`、`%rdx`……）。
- **[Event Tracing](https://docs.kernel.org/trace/events.html)**（`Documentation/trace/events.rst`）
  - **讀哪裡**：「Event filtering」一節（filter 語法、`common_*` 欄位）與「Event triggers」。
  - **能學到什麼**：tracepoint 的 filter、trigger（例如命中某事件時自動 dump stack 或開關 tracing）。
- **[Fprobe-based Event Tracing](https://docs.kernel.org/trace/fprobetrace.html)**（`Documentation/trace/fprobetrace.rst`）
  - **讀哪裡**：「BTF arguments」一節，解釋 `file->f_op->write_iter` 這種 BTF 欄位存取的規則；kprobe events 的 BTF 支援用的是同一套。

### 文章

- **Debugging the kernel using Ftrace（part 1、part 2）** — Steven Rostedt（LWN.net，2009 年 12 月）
  - **這篇說什麼**：ftrace 作者本人寫的入門，從 function tracer、function_graph 到 `trace_printk`。
  - **讀哪裡**：part 1 的 function_graph 範例與 `set_ftrace_pid`；part 2 的 `trace_printk` 與「在 oops 時 dump ftrace buffer」。
  - **為什麼值得讀**：作者解釋了設計取捨；部分路徑（`/sys/kernel/debug/tracing`）已過時，但機制沒變。可在 LWN 的 Kernel Index「Ftrace」分類下找到。

### 書籍

- **《BPF Performance Tools》** — Brendan Gregg（Addison-Wesley, 2019）
  - **這本書的定位**：bpftrace 與 BCC 的權威參考，作者是 bpftrace 的主要推手之一。
  - **讀哪幾章**：第 2 章（技術背景：BPF、kprobes、tracepoints、ftrace 的關係）、第 5 章（bpftrace 語言）；第 8 章「File Systems」有大量 VFS 層的 one-liner，可以直接拿來對照本課的 VFS 章節。
  - **前提知識**：本章內容即可；書中 kernel 版本是 4.x/5.x，函式名需要對照你的 tree 檢查（這正是踩雷第 1 條）。

ftrace 讓 kernel 跑給你看，但它只看得到入口和出口。想停在 `new_sync_write` 那一行、看 `filp` 指向的整個 struct，下一章換 gdb 上場。

→ [Ch 52 QEMU + gdb：單步追 kernel](./52-qemu-gdb-kernel.md)
