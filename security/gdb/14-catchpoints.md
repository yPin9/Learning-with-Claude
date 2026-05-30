# Ch 14 — Catchpoint

> **目標**：掌握 catchpoint——攔截「事件」而非位置或資料。`catch syscall`（攔系統呼叫）、`catch signal`、`catch throw/catch`（C++ 例外）、`catch exec/fork/vfork`（行程事件）、`catch load/unload`（共享庫）。學完你能在「某類事件發生」的瞬間停下來，這是純位置斷點做不到的。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`。

## 為什麼需要第三種「停」

到目前為止你有兩種停法：

- **breakpoint**：停在某個**位置**（程式碼地址）
- **watchpoint**：停在某個**資料**被存取時

但有些東西既不是固定位置、也不是固定資料，而是**一類事件**：

- 「程式呼叫 `open` 系統呼叫時停」——但 `open` 可能從一百個地方被呼叫
- 「程式收到 SIGSEGV 時停」（雖然這個 GDB 預設就停）
- 「C++ 程式 throw 任何 exception 時停」——不知道從哪 throw
- 「程式 fork 出子 process 時停」
- 「某個 plugin `.so` 被 dlopen 時停」

這些用 catchpoint。它讓你攔截「某種事情發生」，不在乎發生在哪。

## `catch syscall`：攔系統呼叫

最強大的 catchpoint。攔截程式進入/離開某個 syscall：

```
(gdb) catch syscall open             # 攔 open syscall
(gdb) catch syscall openat read write # 攔多個
(gdb) catch syscall 2                # 用 syscall 號
(gdb) catch syscall                  # 攔「所有」syscall（很吵）
(gdb) catch syscall group:network    # 攔一整類（GDB 支援 group）
```

範例：找出程式到底開了哪些檔案

```c
// syscall_demo.c — gcc -g -O0
#include <stdio.h>
int main(void) {
    FILE *f = fopen("/etc/hostname", "r");   // 底層會呼叫 openat
    char buf[64]; fgets(buf, 64, f);
    fclose(f);
    return 0;
}
```

```
(gdb) catch syscall openat
(gdb) run
Catchpoint 1 (call to syscall openat), 0x... in __libc_open64 ...
(gdb) print $rdi                     # syscall 進入時，參數在暫存器
(gdb) info registers rsi             # 第二參數：路徑指標
(gdb) x/s $rsi                       # 看開的是哪個檔！
0x...: "/etc/hostname"
(gdb) continue
Catchpoint 1 (returned from syscall openat), ...   # 離開 syscall 時又停一次
(gdb) print $rax                     # 回傳值：fd 或負的 errno
```

`catch syscall` 在進入**和**離開時各停一次（看 `Catchpoint ... call to` vs `returned from`）。進入時看參數（`$rdi`/`$rsi`…），離開時看回傳值（`$rax`）。

> 這基本上是「GDB 內建的 strace」（Ch 對照 strace 工具）。差別：strace 印 log 不停、catch syscall 會停下來讓你檢查 + 改狀態。要看程式的所有 syscall 行為，兩者各有所長。

## `catch throw` / `catch catch`：C++ 例外

C++ 程式 throw 一個 exception，但你不知道從哪 throw 的——`catch throw` 在 throw 的瞬間停：

```cpp
// 對 C++ 程式
(gdb) catch throw                    # 任何 throw 都停
(gdb) catch throw std::runtime_error # 只攔特定型別（GDB 13+ 支援型別過濾）
(gdb) catch catch                    # 在 exception 被 catch 時停
(gdb) catch rethrow                  # rethrow 時停
```

```
(gdb) catch throw
(gdb) run
Catchpoint 1 (exception thrown), 0x... in __cxa_throw ...
(gdb) backtrace                      # 看 throw 從哪來！
```

debug 「exception 從哪冒出來的」、「為什麼這個 exception 沒被 catch 到」，`catch throw` 直接停在 throw 點，`bt` 看呼叫鏈。比在 `terminate` 處才發現好太多。

## `catch signal`：攔訊號

```
(gdb) catch signal SIGSEGV           # 收到 SIGSEGV 時停（透過 catchpoint 機制）
(gdb) catch signal SIGUSR1 SIGUSR2   # 多個
(gdb) catch signal all               # 所有 signal
```

> 注意：signal 的處理 GDB 還有另一套更主要的機制 `handle`（Ch 15），且多數 fatal signal（SIGSEGV 等）GDB 預設就會停。`catch signal` 是 catchpoint 框架下的版本，差別在它走 breakpoint 的管理介面（可以有 commands、計數等）。Ch 15 會把 signal 講完整。

## `catch exec` / `fork` / `vfork`：行程事件

```
(gdb) catch fork                     # 程式 fork 時停
(gdb) catch vfork
(gdb) catch exec                     # 程式 execve 換映像時停
```

這些是 Ch 17（多行程 debug）的工具。例如 debug 一個 shell：`catch exec` 會在它每次執行新程式時停，讓你跟進子程式。

## `catch load` / `unload`：共享庫事件

```
(gdb) catch load                     # 任何 .so 被載入時停
(gdb) catch load libcrypto           # 特定庫
(gdb) catch unload
```

debug `dlopen` 的 plugin 系統時：`catch load libplugin` 在 plugin 載入瞬間停，正好可以這時下 pending 斷點實體化（Ch 4）、檢查載入位址。

## catchpoint 也是斷點：共用管理

catchpoint 在 `info breakpoints` 裡和斷點一起列，共用 enable/disable/delete/condition/commands：

```
(gdb) info breakpoints
Num  Type        Disp Enb What
1    catchpoint  keep y   syscall "openat"
2    catchpoint  keep y   exception throw

(gdb) catch syscall write
(gdb) condition 3 $rdi == 2          # 只攔寫到 stderr(fd 2) 的 write
(gdb) commands 3
>x/s $rsi
>continue
>end
```

「攔某 syscall + 條件 + 自動記錄參數」可以做出針對性的 syscall 追蹤。

## 一個實戰：找出程式為什麼開不了某檔

```
(gdb) catch syscall openat
(gdb) run
(gdb) commands
>printf "open: "
>x/s $rsi                            # 印路徑
>continue                            # 進入時印，繼續到離開
>end
... 但離開時要看回傳值，這裡簡化 ...
```

更精準：攔 openat 的「返回」並檢查回傳值是不是負的 errno：

```
(gdb) catch syscall openat
(gdb) run
... 進入時 continue ...
(gdb) print $rax                     # 離開時：負值 = 失敗
$1 = -2                              # -2 = -ENOENT，檔案不存在！
(gdb) x/s $rsi                       # 確認是哪個檔
```

`$rax = -2` 對應 `ENOENT`——你不用改一行 code 就知道「程式試圖開一個不存在的檔」。

## 踩雷集錦

1. **`catch syscall` 進入/離開停兩次搞混**：每個 syscall 命中兩次（call / returned from）。看訊息區分，別以為程式呼叫了兩次。
2. **syscall 參數要在「進入」時看、回傳值在「離開」時看**：進入時 `$rax` 還是 syscall 號不是回傳值。順序別錯。
3. **`catch syscall`（不指定）超吵**：攔所有 syscall，一個簡單程式就幾千次。指定具體 syscall 或 group。
4. **`catch throw` 對 C 程式無效**：它攔的是 C++ 的 `__cxa_throw`。純 C 沒有 exception。
5. **catchpoint 需要符號/libc 支援**：`catch throw` 的型別過濾、syscall 名稱對應，需要對應的 debug 資訊。缺了可能只能用 syscall 號。
6. **syscall 號跨架構不同**：x86-64 的 `open`=2，但 ARM64 不同。用名字（`catch syscall open`）比用號可攜。

## 進階：再往深一層

- **底層機制**：`catch syscall` 用 `PTRACE_SYSCALL`（而非 `PTRACE_CONT`），讓 OS 在每次 syscall 進出時停下通知 tracer——這正是 strace 的核心機制（Ch 2 提過、Ch 對照 strace）。`catch fork/exec` 用 `PTRACE_O_TRACEFORK` 等 option（Ch 17）。
- **`catch throw` 的實作**：GDB 在 libstdc++ 的 `__cxa_throw` 等內部函式下斷點。所以它依賴 C++ runtime 的已知符號。
- **syscall group**：`catch syscall group:memory` / `group:network` 一次攔一類，GDB 從 `syscalls/` XML 定義讀取分類。
- **配合 Python**：catchpoint 可以是 Python 的 `gdb.Breakpoint`（Ch 25）的一種，命中時跑 Python 分析——做一個「自動記錄所有檔案操作」的工具。
- **`catch signal` vs `handle`**：兩條路徑，Ch 15 會釐清何時用哪個。一般 signal 處理用 `handle`，要把 signal 當「可管理的斷點事件」時用 `catch signal`。

## 動手練習

1. 對 `syscall_demo.c`，`catch syscall openat`，在進入時 `x/s $rsi` 看開的檔、離開時 `print $rax` 看 fd。
2. 故意 fopen 一個不存在的檔，用 catch syscall 抓到 `$rax = -ENOENT`，不看 code 就診斷出問題。
3. 寫一個會 throw 的 C++ 程式，`catch throw` + `bt`，找出 throw 點。
4. 寫一個 fork 的程式，`catch fork`，觀察停在 fork 瞬間（接 Ch 17）。
5. 用 `catch syscall write` + `condition ... $rdi==1` + commands，做一個「只記錄寫到 stdout 的內容」的追蹤器。
6. 對比：用 `strace ./syscall_demo` 看同樣的 openat，思考 catch syscall 與 strace 各自的優勢。

## 本章重點整理

- catchpoint 攔「事件」而非位置/資料：syscall、signal、C++ throw/catch、exec/fork、load/unload。
- `catch syscall`：進入與離開各停一次；進入看參數（`$rdi`…）、離開看回傳值（`$rax`）——內建的可互動 strace。
- `catch throw`：在 C++ exception throw 的瞬間停，`bt` 找來源。
- `catch fork/exec/load`：Ch 17 多行程、dlopen plugin debug 的工具。
- catchpoint 共用斷點的 condition/commands/enable/delete 管理。

## 自我檢核

- [ ] breakpoint、watchpoint、catchpoint 三者各「停在什麼」？
- [ ] `catch syscall openat` 命中時，怎麼看「開哪個檔」和「成功與否」？分別在進入還是離開時看？
- [ ] C++ 程式 exception 不知從哪冒出，用什麼指令？
- [ ] `catch syscall` 底層用哪個 ptrace request？跟哪個常見工具同源？
- [ ] 為什麼 syscall 用名字比用號碼可攜？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Setting Catchpoints](https://sourceware.org/gdb/current/onlinedocs/gdb/Set-Catchpoints.html)**
  - **讀哪裡**：catch syscall / throw / exec / fork / load 全部事件類型。
  - **和本章的關聯**：本章所有 catch 子命令的權威清單。

### 部落格 / 文章

- **[Using GDB's catch syscall](https://developers.redhat.com/articles/)** 類文章
  - **這篇說什麼**：catch syscall 在實戰除錯（檔案/網路問題）的用法。
  - **和本章的關聯**：把「內建 strace」用法說得更實際。

- **[strace 工作原理](https://github.com/strace/strace/wiki)**
  - **為什麼值得讀**：catch syscall 與 strace 同源（PTRACE_SYSCALL）；理解一個就懂另一個。

下一章把 signal 這條線講完整：GDB 怎麼攔截、處理、轉交訊號，以及非同步（async）與 non-stop 模式。

→ [Ch 15 Signal 與非同步控制](./15-signals-and-async.md)
