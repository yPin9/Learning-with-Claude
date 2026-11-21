# Ch 52 — QEMU + gdb：單步追 kernel

> **目標**：能把 v6.18 kernel 開在 QEMU 裡、用 gdb 接上去，在 `anon_pipe_write` 停下來，印出 `filp->f_op->write_iter` 指向誰、`struct pipe_inode_info` 的 ring 狀態、內聯 frame 與 `<optimized out>` 背後的原因，並知道 `nokaslr`、debug info、`vmlinux-gdb.py` 各自解決什麼問題。
> **鴻溝**：本章主要練**間接鴻溝**（在呼叫點直接讀出函式指標的值）與**領域鴻溝**（親眼看資料結構在執行時長什麼樣子）。

> **環境**：Linux v6.18（`~/code_reading/build/vmlinux`，DWARF5 + BTF，`CONFIG_GDB_SCRIPTS=y`），QEMU 6.2.0 TCG 模式，GNU gdb 12.1（Ubuntu 22.04），WSL2。本章的 gdb 輸出全部是 batch 模式實際跑出來的，指令檔附在每段旁邊。

## 為什麼需要這個？

[Ch 51](./51-dynamic-tracing.md) 的 ftrace 回答了「這次 `write_iter` 呼叫的是 `anon_pipe_write`」。但它只看得到函式的入口和出口，看不到：

- 呼叫點那一刻，`filp` 指向的 `struct file` 裡每個欄位是多少
- 被內聯掉的 `new_sync_write` 那一層
- `anon_pipe_write` 跑完之後，pipe 的 ring buffer 裡多了什麼

這些要停下來看。kernel 的除錯器歷史大致是：

1. **硬體除錯器（JTAG、ICE）**：最可靠，但要硬體。
2. **kgdb**：kernel 內建的 gdb stub，透過 serial 或網路和另一台機器上的 gdb 對話。v2.6.26 合入主線。它在被除錯的 kernel **裡面**跑，所以 kernel 壞到某個程度，kgdb 也跟著壞。
3. **QEMU gdbstub**：QEMU 本身就是 CPU。它可以在任何時刻凍結所有 vCPU，讓 gdb 讀寫記憶體與暫存器，guest kernel 完全不知道。不用特殊硬體、不用改 kernel、kernel 卡死了一樣能看。

對讀碼的人來說，第 3 種是最划算的：你已經有 [Ch 0](./00-environment-setup.md) 建好的 kernel 和 QEMU，多加兩個參數就能用。

## 先建立直覺

```
  ┌────────────── 你的 WSL ──────────────┐
  │                                        │
  │  gdb ── vmlinux（符號、DWARF、型別）    │
  │   │                                    │
  │   │ GDB remote protocol (TCP :1234)    │
  │   ▼                                    │
  │  QEMU ── gdbstub                       │
  │   │   （QEMU 就是 CPU：可以凍結、        │
  │   │     讀暫存器、讀記憶體）             │
  │   ▼                                    │
  │  guest kernel（v6.18 bzImage）          │
  │   └─ /bin/pipew 呼叫 write(2)           │
  └────────────────────────────────────────┘
```

關鍵分工：**QEMU 提供「位址上的值」，gdb 提供「位址的意義」**。QEMU 只知道「0xffffffff815932c0 的記憶體是什麼 bytes、rdi 是多少」；gdb 讀 `vmlinux` 的 symbol table 和 DWARF，才知道那是 `anon_pipe_write`、rdi 是參數 `iocb`、`iocb->ki_filp` 在 offset 多少。所以 gdb 載入的 `vmlinux` 必須和 guest 跑的 `bzImage` 是**同一次 build** 的產物，否則所有的意義都對不上。

這個分工也解釋了為什麼需要 `nokaslr`：KASLR 讓 kernel 每次開機載入到隨機位址，QEMU 回報的位址和 `vmlinux` 裡記錄的位址差了一個未知的 offset，gdb 的「意義」就全部錯位。

## 準備：三個參數、一個檔案、一行設定

### QEMU 端：`-s -S`

- `-s`：`-gdb tcp::1234` 的簡寫，開一個 gdbstub 在 TCP 1234 埠。
- `-S`：開機時不啟動 CPU，停在第一條指令等 gdb 下 `continue`。沒有 `-S` 也能接，但 kernel 會先跑一段，你會錯過開機路徑。

我們的 `run-script.sh` 用環境變數 `QEMU_EXTRA` 把參數傳給 QEMU：

```sh
cd ~/code_reading/scratch/52
cat > g.sh <<'S'
#!/bin/sh
echo "guest: running pipew"
/bin/pipew
echo "guest: pipew done"
S
QEMU_EXTRA="-s -S" QEMU_TIMEOUT=200 ~/code_reading/qemu/run-script.sh g.sh pipew > qemu.log 2>&1 &
```

`pipew` 是 [Ch 51](./51-dynamic-tracing.md) 那個 `gcc -static` 編的小程式：開 pipe、寫 `"hello\n"`（6 bytes）、讀回來、印到 stdout。

### `nokaslr`：失敗示範

`run-script.sh` 的 kernel command line 已經寫了 `nokaslr`。為了看看沒有它會怎樣，我們另外用 `kaslr` 開一次（同一個 initramfs、同一個 `vmlinux`）：

```console
$ qemu-system-x86_64 ... -append "console=ttyS0 kaslr panic=-1 quiet" -s -S &
$ gdb -q -batch -ex 'file vmlinux' -ex 'target remote :1234' -ex 'b anon_pipe_write' -ex c
0x000000000000fff0 in ?? ()
Breakpoint 1 at 0xffffffff815932c0: file /home/ypp/code_reading/linux/fs/pipe.c, line 432.
[Inferior 1 (process 1) exited normally]
```

guest 那邊照常印出 `guest: running pipew`、`hello`、`guest: pipew done` 然後關機——`anon_pipe_write` 明明被呼叫了，斷點卻從沒命中。gdb 把斷點設在 `vmlinux` 記錄的 0xffffffff815932c0，但這次開機 kernel 被搬到別的地方，那個位址上根本不是 `anon_pipe_write`。沒有任何錯誤訊息，這是最糟的失敗方式。

v6.18 的 `scripts/gdb/linux/symbols.py` 第 58–83 行有讀取 `vm_layout.kaslr_offset` 的程式碼，代表 `lx-symbols` 在某些情況下會嘗試處理 KASLR offset；我們**沒有實測**這條路。`Documentation/process/debugging/gdb-kernel-debugging.rst` 的建議仍然是加 `nokaslr`，這也是最省事的做法。

### `vmlinux`：帶 debug info 的那一個

```console
$ ls -la vmlinux arch/x86/boot/bzImage | awk '{print $5, $9}'
17400832 arch/x86/boot/bzImage
419248608 vmlinux
$ readelf -S -W vmlinux | awk '$2 ~ /^\.debug_(info|line|str|loclists|rnglists)$|^\.BTF$/ {print $2, $6}'
.BTF 63fcab
.debug_info 10897be6
.debug_line 17f068f
.debug_str 57e629
.debug_loclists 2131fb6
.debug_rnglists 412a42
```

`bzImage`（17 MB）是壓縮過、沒有符號的開機映像，給 QEMU 開機用；`vmlinux`（419 MB）是未壓縮的 ELF，給 gdb 用。光 `.debug_info` 就有 0x10897be6 bytes，約 278 MB——這就是 `CONFIG_DEBUG_INFO_DWARF5=y` 的代價。`.debug_loclists` 記錄「某個變數在某段程式碼範圍裡放在哪個暫存器或 stack 位置」，後面解釋 `<optimized out>` 時會用到。

### `vmlinux-gdb.py`：auto-load 安全設定

`CONFIG_GDB_SCRIPTS=y` 讓 build 目錄多一個 `vmlinux-gdb.py`（symlink 到 `scripts/gdb/vmlinux-gdb.py`），gdb 載入 `vmlinux` 時會自動找它。但 gdb 預設不執行來路不明的 Python：

```console
$ gdb -q -batch -ex 'file vmlinux' -ex 'p $lx_current'
warning: File "/home/ypp/code_reading/linux/scripts/gdb/vmlinux-gdb.py" auto-loading has been declined by your `auto-load safe-path' set to "$debugdir:$datadir/auto-load".
To enable execution of this file add
	add-auto-load-safe-path /home/ypp/code_reading/linux/scripts/gdb/vmlinux-gdb.py
line to your configuration file "/home/ypp/.config/gdb/gdbinit".
...
$1 = void
```

`$1 = void` 表示 `$lx_current` 這個函式不存在。解法是在 `file vmlinux` **之前**加一行：

```
add-auto-load-safe-path /home/ypp/code_reading/build
```

可以寫進 `~/.config/gdb/gdbinit` 一勞永逸。載入成功後 `apropos lx-` 會列出 34 個指令，常用的有：

| 指令／函式 | 作用 |
|---|---|
| `lx-symbols` | 重新載入 vmlinux 與已載入 module 的符號（module 載入位址是動態的，沒有它就斷不到 module 裡的函式） |
| `lx-ps` | 列出所有 task |
| `lx-dmesg` | 印 kernel log buffer（kernel 卡死、serial 沒輸出時特別有用） |
| `lx-cmdline`、`lx-version` | 印 command line 與版本字串，用來確認「gdb 和 guest 是同一個 kernel」 |
| `lx-lsmod`、`lx-mounts`、`lx-iomem` | 對應 `/proc/modules`、`/proc/mounts`、`/proc/iomem` |
| `$lx_current()` | 目前 CPU 上的 `struct task_struct`（等同 kernel 裡的 `current`） |
| `$container_of(ptr, "type", "member")` | gdb 版的 `container_of`（[Ch 16](./16-container-of.md)） |

## 實戰：在 `anon_pipe_write` 停下來

完整指令檔（`cmds.gdb`）：

```
set pagination off
set confirm off
add-auto-load-safe-path /home/ypp/code_reading/build
file /home/ypp/code_reading/build/vmlinux
target remote :1234
b anon_pipe_write
c
x/3i anon_pipe_write
bt 6
up
list 593,593
p filp->f_op
p filp->f_op->write_iter
p *filp->f_op
down
p total_len
p pipe
```

```console
$ timeout 180 gdb -q -batch -x cmds.gdb
```

### 命中與 backtrace

```
0x000000000000fff0 in ?? ()
Breakpoint 1 at 0xffffffff815932c0: file /home/ypp/code_reading/linux/fs/pipe.c, line 432.

Thread 1 hit Breakpoint 1, anon_pipe_write (iocb=0xffffc90000227e90, from=0xffffc90000227e68) at /home/ypp/code_reading/linux/fs/pipe.c:432
432	{
```

`0x000000000000fff0 in ?? ()` 是 `target remote` 連上時 CPU 的位置：x86 重置後 IP 是 0xFFF0、CS 的基底是 0xFFFF0000，實際從實體位址 0xFFFFFFF0（BIOS 的重置向量）開始執行；gdb 只顯示 rip 的 0xfff0。這時還沒有任何 kernel 程式碼在跑。`b anon_pipe_write` 在 kernel 還沒載入時就設下去，照樣有效，原因在「TCG 下的注意事項」一節。

```
#0  anon_pipe_write (iocb=0xffffc90000227e90, from=0xffffc90000227e68) at /home/ypp/code_reading/linux/fs/pipe.c:432
#1  0xffffffff81586424 in new_sync_write (ppos=0x0, len=6, buf=0x498004 "hello\n", filp=0xffff888005656240) at /home/ypp/code_reading/linux/fs/read_write.c:593
#2  vfs_write (file=file@entry=0xffff888005656240, buf=buf@entry=0x498004 "hello\n", count=count@entry=6, pos=pos@entry=0x0) at /home/ypp/code_reading/linux/fs/read_write.c:686
#3  0xffffffff815866e8 in ksys_write (fd=<optimized out>, buf=0x498004 "hello\n", count=6) at /home/ypp/code_reading/linux/fs/read_write.c:738
#4  0xffffffff823056c4 in do_syscall_x64 (nr=<optimized out>, regs=0xffffc90000227f58) at /home/ypp/code_reading/linux/arch/x86/entry/syscall_64.c:63
#5  do_syscall_64 (regs=0xffffc90000227f58, nr=<optimized out>) at /home/ypp/code_reading/linux/arch/x86/entry/syscall_64.c:94
```

和 Ch 51 的 ftrace 對照，這裡多了一層 **`#1 new_sync_write`**。注意 #1 和 #2 的位址欄：#2 沒有自己的位址，因為 `new_sync_write` 被內聯進 `vfs_write`，兩者共用同一個機器 frame；DWARF 的 `DW_TAG_inlined_subroutine` 記錄了「這段機器碼其實是 `new_sync_write` 的內容」，gdb 據此把它還原成一個虛擬 frame。ftrace 只看機器碼層級的函式入口，所以看不到它。`do_syscall_x64` 和 `do_syscall_64` 也是同樣的內聯關係。

`buf=0x498004 "hello\n"`：gdb 直接把 user space 的字串印出來了。這能成功，是因為停下來的那一刻 CPU 的 page table 還是 `pipew` 的，user 位址可讀。換一個時間點（例如在 kernel thread 裡）讀同一個位址就會失敗。

### 呼叫點：第 5 種間接解法

`up` 移到 frame #1，也就是間接呼叫發生的那一行：

```
#1  0xffffffff81586424 in new_sync_write (ppos=0x0, len=6, buf=0x498004 "hello\n", filp=0xffff888005656240) at /home/ypp/code_reading/linux/fs/read_write.c:593
593		ret = filp->f_op->write_iter(&kiocb, &iter);
$1 = (const struct file_operations *) 0xffffffff82439880 <pipeanon_fops>
$2 = (ssize_t (*)(struct kiocb *, struct iov_iter *)) 0xffffffff815932c0 <anon_pipe_write>
```

這兩行就是 [Ch 28](./28-ops-tables-indirect-calls.md) 列的第 5 種解法：**在執行期的呼叫點，直接把函式指標印出來，讓 debugger 幫你把位址翻成 symbol**。`$1` 告訴你 `f_op` 指向哪一張表（`pipeanon_fops`），`$2` 告訴你那一格是誰（`anon_pipe_write`）。不用猜，不用在 88 個候選裡挑。

如果你還不知道目標是誰，可以反過來：在呼叫者 `vfs_write` 設**條件斷點**，只在 `pipew` 這個行程時停，每次停下就印 `file->f_op`：

```
b vfs_write if $_streq($lx_current().comm, "pipew")
c
p file->f_op
c
p file->f_op
```

```
Thread 1 hit Breakpoint 2, vfs_write (file=file@entry=0xffff8880050323c0, buf=buf@entry=0x498004 "hello
", count=count@entry=6, pos=pos@entry=0x0) at /home/ypp/code_reading/linux/fs/read_write.c:667
667	{
$1 = (const struct file_operations *) 0xffffffff82439880 <pipeanon_fops>

Thread 1 hit Breakpoint 2, vfs_write (file=file@entry=0xffff888004f5b0c0, buf=buf@entry=0x7ffd20882100 "hello
", count=count@entry=6, pos=pos@entry=0xffffc90000227ef8) at /home/ypp/code_reading/linux/fs/read_write.c:667
667	{
$2 = (const struct file_operations *) 0xffffffff824a8ac0 <console_fops>
```

`pipew` 一共兩次 `write(2)`：第一次寫 pipe（`pipeanon_fops`），第二次寫 stdout 也就是 serial console（`console_fops`），和 Ch 51 用 function_graph 看到的 `anon_pipe_write`、`redirected_tty_write` 完全對得上。條件裡的 `$lx_current()` 來自 `vmlinux-gdb.py`，沒載入它這個條件會報錯。TCG 下每次經過 `vfs_write` 都要評估條件（shell 的每次輸出也算），所以會慢一些，但這個規模感覺不出來。

再進一步，把整張表印出來：

```
$3 = {owner = 0x0, fop_flags = 0, llseek = 0x0, read = 0x0, write = 0x0, read_iter = 0xffffffff81592740 <anon_pipe_read>, write_iter = 0xffffffff815932c0 <anon_pipe_write>, iopoll = 0x0, iterate_shared = 0x0, poll = 0xffffffff81592e00 <pipe_poll>, unlocked_ioctl = 0xffffffff81592570 <pipe_ioctl>, compat_ioctl = 0x0, mmap = 0x0, open = 0xffffffff81593f90 <fifo_open>, flush = 0x0, release = 0xffffffff81593e70 <pipe_release>, fsync = 0x0, fasync = 0xffffffff81592680 <pipe_fasync>, lock = 0x0, get_unmapped_area = 0x0, check_flags = 0x0, flock = 0x0, splice_write = 0xffffffff815d13f0 <iter_file_splice_write>, splice_read = 0x0, splice_eof = 0x0, setlease = 0x0, fallocate = 0x0, show_fdinfo = 0x0, copy_file_range = 0x0, remap_file_range = 0x0, fadvise = 0x0, uring_cmd = 0x0, uring_cmd_iopoll = 0x0, mmap_prepare = 0x0}
```

對照 `fs/pipe.c:1257` 的原始碼，八個非 NULL 欄位一個不差。這張輸出還順便回答了幾個靜態讀碼時要另外查的問題：

- `.write = 0x0`：所以 `vfs_write()` 第 683 行的 `if (file->f_op->write)` 不成立，走 `else if (file->f_op->write_iter)` 分支。
- `.llseek = 0x0`：pipe 不能 seek。
- `mmap_prepare`、`fop_flags`：這兩個欄位在比較舊的 kernel 不存在。如果你的記憶（或 LLM 的記憶）裡 `file_operations` 沒有它們，這就是一個你該更新記憶的訊號（[Ch 53](./53-llm-assisted-reading.md)）。

### 失敗示範：`<optimized out>` 與「No symbol」

回到 frame #0，想看區域變數：

```
$4 = <optimized out>
$5 = <optimized out>
```

`total_len` 和 `pipe` 都看不到。我們試過在第 530 行（`pipe->head = head + 1;`）再設一個斷點看 `copied`：

```
Breakpoint 2 at 0xffffffff8159352e: file /home/ypp/code_reading/linux/fs/pipe.c, line 530.

Thread 1 hit Breakpoint 2, pipe_buf (slot=<optimized out>, pipe=<optimized out>) at /home/ypp/code_reading/linux/fs/pipe.c:530
530				pipe->head = head + 1;
cmds2.gdb:24: Error in sourced command file:
No symbol "copied" in current context.
```

更糟：gdb 說我們停在 `pipe_buf()` 裡（一個內聯的 helper），而且 `copied` 根本不在作用域。

原因是 kernel 用 `-O2` 編譯，而且**不能**改成 `-O0`（kernel 依賴內聯與最佳化才能正確運作，很多地方用 `__always_inline` 或編譯期常數展開）。最佳化後，一個變數的值可能只在某幾條指令範圍內存在於某個暫存器，過了就被覆蓋。DWARF 用 location list 記錄這件事，`info scope` 可以看到：

```console
$ gdb -q -batch -ex 'file vmlinux' -ex 'info scope anon_pipe_write'
Scope for anon_pipe_write:
Symbol iocb is multi-location:
  Base address 0xffffffff815932c0  Range 0xffffffff815932c0-0xffffffff81593301: a variable in $rdi
  Range 0xffffffff81593301-0xffffffff8159378f: a complex DWARF expression:
     0: DW_OP_fbreg -128
...
Symbol from is multi-location:
  Base address 0xffffffff815932c0  Range 0xffffffff815932c0-0xffffffff81593308: a variable in $rsi
  Range 0xffffffff81593308-0xffffffff815935ec: a variable in $rbp
```

`iocb` 在函式開頭那段在 `%rdi`（x86_64 呼叫慣例的第一個參數），之後被存到 stack 上；`from` 從 `%rsi` 搬到 `%rbp`。`total_len`、`pipe` 在入口那一刻還沒被算出來，所以是 `<optimized out>`。

**實用解法**：在函式入口趁參數還在，自己算出想要的東西存進 gdb 的 convenience variable（`$` 開頭），之後隨時用：

```
b anon_pipe_write
c
set $p = (struct pipe_inode_info *)iocb->ki_filp->private_data
p $p->head
p $p->tail
p $p->max_usage
p $p->ring_size
p $p->readers
p $p->writers
p from->count
p *from
```

`iocb->ki_filp->private_data` 這條路是讀 `anon_pipe_write` 第 433–434 行學來的：

```c
/* fs/pipe.c:431（v6.18） */
anon_pipe_write(struct kiocb *iocb, struct iov_iter *from)
{
	struct file *filp = iocb->ki_filp;
	struct pipe_inode_info *pipe = filp->private_data;
```

輸出：

```
[Switching to Thread 1.2]

Thread 2 hit Breakpoint 1, anon_pipe_write (iocb=0xffffc9000024be90, from=0xffffc9000024be68) at /home/ypp/code_reading/linux/fs/pipe.c:432
432	{
$1 = (struct pipe_inode_info *) 0xffff8880053c0a80
$2 = 0
$3 = 0
$4 = 16
$5 = 16
$6 = 1
$7 = 1
$8 = 6
$9 = {iter_type = 0 '\000', nofault = false, data_source = true, iov_offset = 0, {__ubuf_iovec = {iov_base = 0x498004, iov_len = 6}, {{__iov = 0x498004, kvec = 0x498004, bvec = 0x498004, folioq = 0x498004, xarray = 0x498004, ubuf = 0x498004}, count = 6}}, {nr_segs = 1, folioq_slot = 1 '\001', xarray_start = 1}}
```

這次命中的是 CPU#1（`Thread 1.2`）。每個 vCPU 在 gdb 裡是一個 thread，斷點在任何一顆 CPU 上命中都會停下**全部** CPU。

讀這些值：`head == tail == 0`，ring 是空的；`max_usage` 和 `ring_size` 都是 16，對應 `include/linux/pipe_fs_i.h:5` 的 `#define PIPE_DEF_BUFFERS 16`；一個 reader、一個 writer。`*from` 是 [Ch 11](./11-structs-unions-tagged.md) 講的 tagged union 的活教材：`iter_type = 0` 是 tag，union 裡的六個指標成員印出同一個值 `0x498004`，因為它們共用同一塊記憶體——只有 `ubuf` 是這個 tag 下有意義的解讀。

### `$container_of` 與 `$lx_current()`

```
p $container_of(&$p->rd_wait, "struct pipe_inode_info", "rd_wait")
p $container_of(&$p->rd_wait, "struct pipe_inode_info", "rd_wait") == $p
p $lx_current().comm
p $lx_current().pid
```

```
$10 = (struct pipe_inode_info *) 0xffff8880053c0a80
$11 = 1
$12 = "pipew\000\000\000\000\000\000\000\000\000\000"
$13 = 70
```

從成員 `rd_wait` 的位址反推回外層 struct，結果和 `$p` 相同（`$11 = 1`）。實務上你會反過來用：手上只有一個 `wait_queue_head *` 或 `list_head *`，用 `$container_of` 找到它屬於誰。`$lx_current()` 確認此刻在 CPU 上的是 pid 70 的 `pipew`。實作在 `scripts/gdb/linux/utils.py` 第 74–97 行，就是 [Ch 16](./16-container-of.md) 那個巨集的 Python 版：指標轉成整數、減掉 `offset_of`、轉回型別。

### `finish`：讓函式跑完，看它改了什麼

```
delete
finish
p $p->head
p $p->tail
p $p->bufs[0]
p *$p->bufs[0].ops
```

```
0xffffffff81586424 in new_sync_write (ppos=0x0, len=6, buf=0x498004 "hello\n", filp=0xffff888004f79600) at /home/ypp/code_reading/linux/fs/read_write.c:593
593		ret = filp->f_op->write_iter(&kiocb, &iter);
Value returned is $14 = 6
$15 = 1
$16 = 0
$17 = {page = 0xffffea000014f800, offset = 0, len = 6, ops = 0xffffffff82439a40 <anon_pipe_buf_ops>, flags = 16, private = 0}
$18 = {confirm = 0x0, release = 0xffffffff81592f50 <anon_pipe_buf_release>, try_steal = 0xffffffff81593130 <anon_pipe_buf_try_steal>, get = 0xffffffff81592610 <generic_pipe_buf_get>}
```

`finish` 讓 `anon_pipe_write` 跑到返回，停在呼叫點下一條指令，並印出返回值 6。之後：

- `head` 從 0 變 1：寫入佔用了一個 slot（`fs/pipe.c:530` `pipe->head = head + 1;`）。
- `bufs[0]`：`len = 6`、`offset = 0`、一個新配置的 `page`；`flags = 16` 是 0x10，查 `include/linux/pipe_fs_i.h:11` 是 `PIPE_BUF_FLAG_CAN_MERGE`（第 539 行設的），表示下一次小寫入可以合併進這一頁。
- `ops = <anon_pipe_buf_ops>`：**又一張 ops table**。`pipe_buffer` 也是透過函式指標決定怎麼釋放、怎麼「偷」頁面，下次讀到 `pipe_buf_release()` 時你已經知道它會跳到 `anon_pipe_buf_release`。

這一段演示了 gdb 相對於 ftrace 的核心優勢：**在同一個時間點前後比較資料結構**。

### 其他 `lx-` 指令與硬體斷點

```
lx-ps
hbreak ksys_read
c
bt 4
info breakpoints
lx-dmesg
```

`lx-ps` 的輸出（節錄）：

```
      TASK          PID    COMM
0xffffffff8320e940   0   swapper/0
0xffff888004678000   1   sh
0xffff888004678f00   2   kthreadd
...
0xffff88800514da00  69   sh
0xffff888005972d00  70   pipew
```

`hbreak` 是硬體斷點（x86 的 debug register DR0–DR3）：

```
Hardware assisted breakpoint 2 at 0xffffffff815864e0: file /home/ypp/code_reading/linux/fs/read_write.c, line 705.

Thread 2 hit Breakpoint 2, ksys_read (fd=3, buf=0x7ffea53c5100 "", count=15) at /home/ypp/code_reading/linux/fs/read_write.c:705
705	{
#0  ksys_read (fd=3, buf=0x7ffea53c5100 "", count=15) at /home/ypp/code_reading/linux/fs/read_write.c:705
#1  0xffffffff823056c4 in do_syscall_x64 (nr=<optimized out>, regs=0xffffc9000024bf58) at /home/ypp/code_reading/linux/arch/x86/entry/syscall_64.c:63
#2  do_syscall_64 (regs=0xffffc9000024bf58, nr=<optimized out>) at /home/ypp/code_reading/linux/arch/x86/entry/syscall_64.c:94
#3  0xffffffff81000130 in entry_SYSCALL_64 () at /home/ypp/code_reading/linux/arch/x86/entry/entry_64.S:121
```

`fd=3, count=15`：正是 `pipew.c` 的 `read(fds[0], buf, sizeof(buf) - 1)`，`fds[0]` 是 3（0/1/2 被 stdin/stdout/stderr 佔走），`sizeof(buf) - 1` 是 15。`lx-dmesg` 印出了 358 行 kernel log，第一行是 `Linux version 6.18.0 ...`，其中有 [Ch 51](./51-dynamic-tracing.md) 引用的 `ftrace: allocating 58111 entries in 228 pages`。

## 底層機制：這些到底怎麼做到的

### nop patching 的現場證據

在斷點停下時執行的 `x/3i anon_pipe_write`：

```
=> 0xffffffff815932c0 <anon_pipe_write>:	endbr64
   0xffffffff815932c4 <anon_pipe_write+4>:	nopl   0x0(%rax,%rax,1)
   0xffffffff815932c9 <anon_pipe_write+9>:	push   %r15
```

而 `objdump -d vmlinux` 在同一個位址看到的是 `call __fentry__`。要抓到改寫的那一刻，用硬體斷點停在 `ftrace_init`，前後各看一次：

```
hbreak ftrace_init
c
x/2i anon_pipe_write+4
delete
finish
x/2i anon_pipe_write+4
```

```
Thread 1 hit Breakpoint 1, ftrace_init () at /home/ypp/code_reading/linux/kernel/trace/ftrace.c:7934
7934	{
   0xffffffff815932c4 <anon_pipe_write+4>:	call   0xffffffff812722f0 <__fentry__>
   0xffffffff815932c9 <anon_pipe_write+9>:	push   %r15
start_kernel () at /home/ypp/code_reading/linux/init/main.c:977
977		early_trace_init();
   0xffffffff815932c4 <anon_pipe_write+4>:	nopl   0x0(%rax,%rax,1)
   0xffffffff815932c9 <anon_pipe_write+9>:	push   %r15
```

`ftrace_init()` 執行前，記憶體裡還是編譯器產生的 `call __fentry__`；`finish` 回到 `start_kernel()` 之後，同一個位址已經是 5 bytes 的 `nopl`。這就是 [Ch 51](./51-dynamic-tracing.md)「dynamic ftrace」一節的現場證據。（這裡用 `hbreak` 而不是 `b`，是習慣：開機早期設硬體斷點不依賴記憶體內容。在 TCG 下兩者都能用。）這也提醒你：**磁碟上的 `vmlinux` 和執行中的 kernel text 不保證一致**，ftrace、kprobe、static key、alternatives（依 CPU 功能替換指令）都會改寫 kernel text。

### `next` 跳過了什麼

在 `anon_pipe_write` 入口連下 6 次 `next`：

```
437		size_t total_len = iov_iter_count(from);
455		if (unlikely(total_len == 0))
458		mutex_lock(&pipe->mutex);
460		if (!pipe->readers) {
475		head = pipe->head;
476		was_empty = pipe_empty(head, pipe->tail);
```

第 451 行 `if (pipe_has_watch_queue(pipe))` 被跳過了。看定義：

```c
/* include/linux/pipe_fs_i.h:170（v6.18） */
static inline bool pipe_has_watch_queue(const struct pipe_inode_info *pipe)
{
#ifdef CONFIG_WATCH_QUEUE
	return pipe->watch_queue != NULL;
#else
	return false;
#endif
}
```

我們的 `.config` 是 `# CONFIG_WATCH_QUEUE is not set`，函式恆回傳 `false`，編譯器把整個 `if` 刪了，沒有對應的機器碼，gdb 也就沒有地方停。單步的軌跡是「這個 config 下實際存在的程式碼」的地圖，和 [Ch 36](./36-kconfig-kbuild-ifdef.md) 用 Kconfig 推論的結果可以互相驗證。

### TCG 下的注意事項

**斷點怎麼生效**：我們在 CPU 還在 BIOS、kernel 根本還沒載入時就設了 `b anon_pipe_write`，斷點照樣命中。就我所知，QEMU 在 TCG 模式下處理 gdb 的斷點不是往 guest 記憶體寫 `int3`，而是在把 guest 指令翻譯成 host 程式碼時檢查「這個 PC 有沒有斷點」。所以斷點不受 guest 記憶體內容影響，也不會被 guest 看到。用 KVM 時情況不同：軟體斷點要真的寫進 guest 記憶體，kernel 還沒載入時寫進去的 `int3` 會被 kernel 解壓覆蓋，這時要改用 `hbreak`。這段 KVM 行為我們**沒有實測**（WSL 沒有可用的 KVM）。

**硬體斷點**：TCG 下 `hbreak` 也是 QEMU 模擬的，實測可用（上面的 `ksys_read`）。真實 x86 只有 4 個 debug register，寫教材時養成「hbreak 最多用 4 個」的習慣比較安全。

**時間**：TCG 全靠軟體模擬，比 KVM 慢一到兩個數量級；gdb 停住時，guest 的 timer interrupt 也停了。在斷點上停很久，繼續之後 guest 可能看到時間跳一大段，有些 watchdog（soft lockup、RCU stall）會因此報警。我們的實驗每次停幾秒，沒有觀察到這類訊息；長時間停留時請留意 `lx-dmesg`。

**單步與中斷**：在 kernel 裡 `step`/`stepi`，下一條指令有可能是剛好到期的 timer interrupt，單步就掉進中斷處理函式了。QEMU 的 gdbstub 預設在單步時遮蔽中斷與 timer（可用 `maintenance packet qqemu.sstep` 查詢或調整），我們上面 6 次 `next` 都留在 `anon_pipe_write` 裡，和這個預設一致。

**多 CPU**：`-smp 2` 時有兩個 thread。`next` 期間另一顆 CPU 也在跑（`info threads` 顯示 CPU#0 在 `tick_nohz_idle_stop_tick`），如果另一顆 CPU 也命中同一個斷點，gdb 會切過去，你會以為程式「跳」了。只關心單一路徑時，把 `-smp 1` 可以省掉很多困惑。

## 對比與取捨

| 方法 | 能看到 | 看不到 / 代價 | 適用 |
|---|---|---|---|
| 靜態讀碼（Ch 28） | 所有可能 | 這次是誰 | 第一輪 |
| ftrace（Ch 51） | 呼叫樹、時間、函式指標值（kprobe） | 內聯 frame、整個 struct 的前後變化 | 「呼叫了誰」「走了哪條路」 |
| QEMU + gdb | 任何時刻的完整狀態、內聯 frame、函式指標 symbol | 停住會破壞時序；`-O2` 的 `<optimized out>` | 「這一刻資料長什麼樣」「這個指標指向誰」 |
| printk | 你想印的任何東西 | 要重編、重開機 | gdb 停不了的地方（時序敏感） |
| kgdb | 真機上的 kernel | kernel 本身要能跑 kgdb；要第二台機器 | 沒有虛擬化的硬體 |
| drgn / crash | 執行中或 dump 下來的 kernel 記憶體，可寫 Python 腳本 | 不能設斷點（drgn 讀活 kernel 需要 root） | 事後分析、大量資料結構走訪 |

## 踩雷集錦

1. **忘了 `nokaslr`**：斷點靜悄悄地永遠不命中，沒有錯誤訊息。先跑 `lx-cmdline` 確認 command line 裡有 `nokaslr`（我們的輸出是 `console=ttyS0 nokaslr panic=-1 quiet`），再懷疑別的。

2. **用舊函式名**：

   ```
   Function "pipe_write" not defined.
   Make breakpoint pending on future shared library load? (y or [n]) [answered N; input not from terminal]
   ```

   v6.15 起改名為 `anon_pipe_write`。這個錯誤至少是吵的；更隱蔽的是函式還在、但已經不在你以為的路徑上。

3. **`vmlinux` 和 `bzImage` 不是同一次 build**：例如改了一行重編了 `vmlinux`，但 QEMU 還在用舊的 `bzImage`。症狀是行號對不上、`p` 出來的 struct 欄位是垃圾。用 `lx-version` 對照 guest 開機訊息的 build 時間戳（我們的是 `#1 SMP PREEMPT_DYNAMIC Sat Nov 21 11:40:09 CST 2026`）。

4. **相信 backtrace 的每一行**：第一次跑 `bt`（不限層數）時，`entry_SYSCALL_64` 底下還有 20 多個 frame：

   ```
   #6  0xffffffff81000130 in entry_SYSCALL_64 () at /home/ypp/code_reading/linux/arch/x86/entry/entry_64.S:121
   #7  0x0000000000000001 in ?? ()
   #8  0x00000000004c17d0 in ?? ()
   #9  0x00007ffd93503b18 in ?? ()
   ...
   Backtrace stopped: Cannot access memory at address 0xffffc90000230000
   ```

   `entry_SYSCALL_64` 是 user/kernel 的邊界，再往下是 gdb 把 `pt_regs` 裡存的 user 暫存器誤當成返回位址。kernel 的 backtrace 到 syscall 入口就該停，用 `bt 6` 這類限制層數的寫法。

5. **在 `<optimized out>` 前放棄**：參數在入口時幾乎總是在暫存器裡（`info scope` 會告訴你在哪），趁那時用 convenience variable 存下來；或 `up` 到呼叫者，那裡常常有同一個值（例如 frame #1 的 `filp`）。真的需要某個中間值，也可以考慮在 [Ch 51](./51-dynamic-tracing.md) 用 kprobe 抓暫存器。

## 進階：再往深一層

- **module 除錯**：`insmod` 之後下 `lx-symbols`，它會讀 kernel 的 module 清單，用每個 module 的載入位址 `add-symbol-file`。之後就能對 module 裡的函式設斷點。module 的 `.ko` 檔要在 gdb 找得到的路徑下（預設搜尋 build 目錄）。
- **斷在開機路徑**：`-S` 讓你從第一條指令開始。`b start_kernel` 然後 `c`，可以單步看 kernel 初始化；`b ftrace_init` 可以親眼看 [Ch 51](./51-dynamic-tracing.md) 的 nop patching 發生。
- **watchpoint**：`watch -l $p->head` 在 `head` 被改寫時停下，回答「是誰改了這個欄位」。TCG 下的 watchpoint 由 QEMU 模擬，對大範圍或頻繁存取的位址會很慢。
- **gdb Python**：`scripts/gdb/linux/` 下的 `lists.py`、`rbtree.py`、`xarray.py`、`mapletree.py` 示範了怎麼用 Python 走訪 kernel 資料結構（對應 Part 4 的各章）。讀它們本身就是讀 kernel 資料結構的另一種角度。
- **drgn**：Meta 開發的可程式化 debugger，用 Python 讀 kernel 記憶體，能讀 vmcore 也能讀執行中的 kernel（需要 root）。對「走訪所有 task 的所有 open file 統計 f_op」這種批次問題，比 gdb 合適。本課環境沒有安裝。

## 動手練習

1. 改 `cmds.gdb`：在 `anon_pipe_read` 設斷點，`finish` 前後比較 `$p->head`、`$p->tail` 和 `bufs[0]`。預測 `tail` 會怎麼變、`bufs[0].page` 會不會被釋放（提示：讀 `fs/pipe.c` 的 `anon_pipe_read` 和 `anon_pipe_buf_release`）。
2. 把 `pipew` 改成寫兩次 3 bytes。用 gdb 驗證第二次寫入走的是第 478 行的合併分支：`head` 不會變成 2，而 `bufs[0].len` 從 3 變成 6。
3. 把 `pipew` 的 stdout 導到 `/dev/null`（guest 腳本寫 `/bin/pipew > /dev/null`），重跑條件斷點那一段。第二次停下時 `file->f_op` 應該變成哪張表？（提示：`drivers/char/mem.c`。）
4. 在 guest 腳本裡先 `echo anon_pipe_write > /sys/kernel/tracing/set_ftrace_filter; echo function > /sys/kernel/tracing/current_tracer`，再跑 `pipew`。在 `anon_pipe_write` 停下時 `x/2i anon_pipe_write+4`，看看那 5 bytes 被 ftrace 改成了什麼，並和 `vmlinux` 裡的 `call __fentry__` 比較目標位址。

## 本章重點整理

- QEMU 提供位址上的值，gdb 用 `vmlinux` 的 symbol 與 DWARF 提供意義；兩者必須是同一次 build，而且要 `nokaslr` 讓位址對得上。
- 在間接呼叫點 `p filp->f_op->write_iter` 會印出 `<anon_pipe_write>`：這是解間接鴻溝最直接的方法。
- gdb 看得到 ftrace 看不到的內聯 frame（`new_sync_write`），也能在函式前後比較資料結構（`head` 0→1、`bufs[0]` 被填好）。
- `-O2` 讓區域變數常常 `<optimized out>`；在入口趁參數還在暫存器時存成 convenience variable 是實用解法。
- 執行中的 kernel text 和磁碟上的 `vmlinux` 不一樣（nop patching 是證據）；TCG 下的斷點、時間、單步行為和真機有差異。

## 自我檢核

- [ ] 不看筆記，解釋「QEMU 提供值、gdb 提供意義」這句話，並用它說明為什麼 KASLR 會讓斷點失效。
- [ ] 同事說「gdb 印出 `<optimized out>`，所以這個變數被編譯器刪掉了」，你怎麼糾正他？你會建議他怎麼拿到那個值？
- [ ] 給你一個 `struct list_head *` 的位址，說出用 `$container_of` 找到外層 struct 的完整指令。
- [ ] 解釋為什麼 gdb 的 backtrace 有 `new_sync_write`、Ch 51 的 ftrace 卻沒有，兩者各自根據什麼資訊。
- [ ] 想一個你在 user-space 專案裡遇到的 callback 呼叫點（例如 event loop 的 handler），描述你會在哪裡設斷點、印什麼，來找出實際的 callback。

## 延伸閱讀

### 官方文件

- **[Debugging kernel and modules via gdb](https://docs.kernel.org/process/debugging/gdb-kernel-debugging.html)**（v6.18 tree：`Documentation/process/debugging/gdb-kernel-debugging.rst`）
  - **讀哪裡**：「Setup」一節（`CONFIG_GDB_SCRIPTS`、`nokaslr`、auto-load 設定）與「Examples of using the Linux-provided gdb helpers」一節（`lx-symbols`、`lx-dmesg`、`$lx_current()`、`$container_of()` 的示範）。
  - **能學到什麼**：本章 `lx-` 指令的官方用法，以及 module 符號載入的完整流程。
  - **前提知識**：會用 gdb 的 `break`、`continue`、`print`。
- **[Using kgdb, kdb and the kernel debugger internals](https://docs.kernel.org/process/debugging/kgdb.html)**（`Documentation/process/debugging/kgdb.rst`）
  - **讀哪裡**：開頭的 kgdb/kdb 介紹與「Kernel config options for kgdb」。
  - **能學到什麼**：沒有 QEMU 時（真機、嵌入式）怎麼做同樣的事，以及 kernel 內 gdb stub 的限制。
- **QEMU 文件「GDB usage」**（QEMU System Emulation User's Guide 的一節）
  - **讀哪裡**：`-s`/`-S`/`-gdb` 參數說明，以及 `maintenance packet qqemu.sstep` 控制單步時中斷行為的說明。
  - **能學到什麼**：本章「TCG 下的注意事項」背後的權威描述。
  - **注意**：版本間有差異，以你的 QEMU 版本（`qemu-system-x86_64 --version`）對應的文件為準。

### 書籍

- **《Debugging with GDB》**（GDB 官方手冊，GNU Press，隨 gdb 版本更新）
  - **讀哪幾章**：「Debugging Remote Programs」（remote protocol 與 `target remote`）、「Examining the Stack」中關於 inline functions 的小節、「Convenience Variables」與「Extending GDB → Python」。
  - **這本書的定位**：本章用到的每一個 gdb 功能都在這裡有完整說明；Python API 一章是讀懂 `scripts/gdb/linux/*.py` 的前提。

### 原始碼

- **`scripts/gdb/linux/`**（v6.18 tree）
  - **讀哪裡**：先讀 `utils.py`（`container_of`、`offset_of`，第 69–97 行），再讀 `tasks.py`（`lx-ps` 怎麼走訪 task list）和 `lists.py`。
  - **為什麼值得讀**：它們是用 Python 重寫的 kernel 巨集與走訪邏輯，每一個都對應到本課某一章的 C 慣用法，而且短。

gdb 和 ftrace 都是「讓機器告訴你真相」的工具。下一章談一個會用很流暢的語氣告訴你**不一定是真相**的工具：LLM。

→ [Ch 53 用 LLM 輔助讀碼，以及它會在哪裡騙你](./53-llm-assisted-reading.md)
