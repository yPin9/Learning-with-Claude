# Ch 17 — 多行程與 fork / exec

> **目標**：掌握程式 fork/exec 時的 debug——`follow-fork-mode`（fork 後跟父還是跟子）、`detach-on-fork`、`follow-exec-mode`、用多 inferior 同時 debug 父子。學完你能 debug fork 出 worker 的 server、shell 執行子程式的鏈、以及 daemon 的雙重 fork。

> **環境**：GDB 13/14，Linux x86_64。

## 為什麼 fork/exec 是個獨立難題

很多真實程式不是單一 process：

- **網路 server**：fork 一個子 process 處理每個連線（傳統 Apache 模式）
- **shell**：fork + exec 執行每個指令
- **daemon**：double-fork 脫離 terminal
- **build 系統 / CI**：層層 spawn 子程式

預設情況，當你 debug 的程式 `fork()`，**GDB 繼續跟父 process，子 process 自由跑掉**——如果 bug 在子 process 裡，你就抓不到。這章教你控制「fork 後 GDB 跟誰」。

## 先建立直覺：fork 是分裂

```
   fork() 之前              fork() 之後
   ┌─────────┐            ┌─────────┐      ┌─────────┐
   │ parent  │            │ parent  │      │ child   │
   │ (被 GDB │   fork()   │ (GDB 在)│      │ (GDB 不 │
   │  trace) │ ─────────> │         │      │  在?)   │
   └─────────┘            └─────────┘      └─────────┘
                          GDB 預設留在這    這個預設跑掉了
```

`fork()` 複製出一個幾乎一樣的子 process。問題：GDB 這個 tracer 要跟哪一個？這由 `follow-fork-mode` 決定。

`exec()` 不同——它是「同一個 process 換掉整個程式映像」（不分裂），問題變成「換了新程式後，符號要不要重載、inferior 要不要換」，由 `follow-exec-mode` 決定。

## `follow-fork-mode`：跟父還是跟子

```
(gdb) set follow-fork-mode parent    # 預設：fork 後跟父，子自由跑掉
(gdb) set follow-fork-mode child     # fork 後跟子，父自由跑掉
(gdb) show follow-fork-mode
```

範例：

```c
// fork_demo.c — gcc -g -O0
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
int main(void) {
    pid_t pid = fork();
    if (pid == 0) {
        printf("child: pid=%d\n", getpid());     // ← 想 debug 這
        return 42;
    } else {
        printf("parent: child=%d\n", pid);
        wait(NULL);
        return 0;
    }
}
```

```
(gdb) set follow-fork-mode child     # 我要 debug 子 process
(gdb) break fork_demo.c:9            # 子 process 的那行
(gdb) run
[Attaching after ... fork to child ...]
Breakpoint 1, ... at fork_demo.c:9   # 成功停在子 process！
9           printf("child: pid=%d\n", getpid());
```

## `detach-on-fork`：要不要兩個都留著

`follow-fork-mode` 是「跟一個、放掉另一個」。但你常常**兩個都想 debug**（父子互動的 bug）。`detach-on-fork off` 讓 GDB 把兩個都留下，變成兩個 inferior：

```
(gdb) set detach-on-fork off         # fork 後父子都保留（不 detach 任何一個）
(gdb) run
... fork 發生 ...
(gdb) info inferiors                 # 現在有兩個！
  Num  Description
* 1    process 1234 (parent)
  2    process 1235 (child)
(gdb) inferior 2                     # 切到子 process debug
(gdb) inferior 1                     # 切回父
```

`detach-on-fork off` + 多 inferior（Ch 3）= 同時 debug 父子，在它們之間切換。debug client/server 在同一程式 fork 出來時無可取代。

組合矩陣：

| follow-fork-mode | detach-on-fork | 結果 |
|---|---|---|
| parent | on（預設） | 只 debug 父，子跑掉 |
| child | on | 只 debug 子，父跑掉 |
| parent | off | 兩個都留，focus 在父 |
| child | off | 兩個都留，focus 在子 |

## `catch fork`：在 fork 瞬間停

承 Ch 14，想在 fork **發生的那一刻**介入：

```
(gdb) catch fork                     # fork syscall 觸發時停
(gdb) run
Catchpoint 1 (forked process 1235), ...   # 停在 fork 瞬間，告訴你子 pid
```

這時你可以決定要不要切換、下斷點等，比靠 `follow-fork-mode` 被動跟隨更可控。

## `follow-exec-mode`：exec 換映像

`exec()` 把當前 process 換成全新程式（shell 執行指令的核心）。GDB 要怎麼處理符號？

```
(gdb) set follow-exec-mode same      # 預設：沿用同一個 inferior，重載新程式符號
(gdb) set follow-exec-mode new       # 為新映像開一個新 inferior（保留舊的記錄）
```

範例：debug 一個會 exec 的程式

```c
// exec_demo.c — gcc -g -O0
#include <unistd.h>
int main(void) {
    execlp("ls", "ls", "-l", NULL);   // 把自己換成 ls
    return 1;  // exec 成功的話到不了這
}
```

```
(gdb) catch exec                      # 在 exec 瞬間停
(gdb) run
Catchpoint 1 (exec'd /usr/bin/ls), ...  # 換成 ls 了
(gdb) break main                      # 現在可對 ls 的 main 下斷（若 ls 有符號）
```

debug shell、debug `system()` 呼叫、debug exec 鏈時用 `catch exec` + 重設斷點。

## 自動跟到子程式：`set follow-fork-mode child` 配 exec

debug 「程式 fork 然後子 exec 別的程式」（最常見的 spawn 模式）：

```
(gdb) set follow-fork-mode child      # fork 後跟子
(gdb) set follow-exec-mode same       # 子 exec 後沿用 inferior
(gdb) catch exec                       # 在子 exec 新程式時停
(gdb) run
... 跟著 fork 進子 process，再在它 exec 時停 ...
```

這串設定讓你「跟著程式一路鑽進它 spawn 的子程式」，debug 多層 spawn 的工具鏈很有用。

## 底層：PTRACE option

承 Ch 2，這些全靠 ptrace 的 `PTRACE_SETOPTIONS`：

- `PTRACE_O_TRACEFORK`：fork 時自動 trace 子 process
- `PTRACE_O_TRACEVFORK`、`PTRACE_O_TRACECLONE`（thread）
- `PTRACE_O_TRACEEXEC`：exec 時停下通知

`follow-fork-mode child` 與 `detach-on-fork off` 就是 GDB 設了這些 option，讓 OS 在 fork/exec 時自動把子代納入 trace 並通知 GDB。理解這個，你就懂為什麼 `catch fork` 能在 fork 瞬間精準停下。

## 踩雷集錦

1. **bug 在子 process 卻一直跟父**：忘了 `set follow-fork-mode child`。預設跟父，子跑掉你看不到。
2. **`set follow-fork-mode` 在 run 之後才設**：要在 fork 發生前設好。run 之前設最保險。
3. **exec 後斷點失效**：`exec` 換了整個映像，舊程式的斷點位址在新程式無意義。`catch exec` 停下後重新下斷。
4. **detach-on-fork off 後 inferior 爆增**：debug 一個狂 fork 的程式（如 fork server），每次 fork 都留一個 inferior，很快幾十個。視情況用 `follow-fork-mode` 只跟一支，或 `catch fork` 手動控制。
5. **vfork 的特殊性**：vfork 子父共享記憶體直到 exec，debug 時行為微妙。`set follow-fork-mode` 對 vfork 也適用但要小心子尚未 exec 時的共享狀態。
6. **daemon double-fork 跟丟**：daemon 通常 fork 兩次脫離。要 `follow-fork-mode child` 連跟兩次，或用 `catch fork` 逐次判斷。

## 進階：再往深一層

- **`set schedule-multiple on`**：多 inferior 時，continue 是否讓「所有」inferior 一起跑（預設只跑 focus 的）。debug 父子互動時可能要開。
- **`inferior` 切換 + thread**：多 inferior 各自有多個 thread（Ch 16），`info inferiors` + `info threads` 一起用看全貌。
- **attach 子 process 的替代法**：如果不想用 follow-fork，也可以讓父跑，事後 `gdb -p <子pid>` attach 子（Ch 3）——但 race window 小，子可能太快結束。
- **`catch fork` + Python**：用 Python（Ch 25）在每次 fork 時自動記錄子 pid、自動設定——做一個 fork 追蹤器。
- **容器 / namespace**：在容器裡 debug 跨 namespace 的 fork（PID namespace）有額外複雜度，GDB 看到的 pid 可能與容器外不同。
- **`set remote ...` 多 process 遠端**：遠端 debug（Ch 36）多 process 需要 extended-remote 模式才支援 follow-fork。

## 動手練習

1. 對 `fork_demo.c`，預設 `run`，確認 GDB 跟父、子跑掉（看不到子的斷點）；再 `set follow-fork-mode child`，確認停在子 process。
2. `set detach-on-fork off` + run，`info inferiors` 看父子都在，在兩者間 `inferior` 切換並各 `bt`。
3. 對 `exec_demo.c`，`catch exec`，觀察停在 exec 瞬間、映像換成 ls。
4. 寫一個 fork 後子 exec 另一程式的「spawn」程式，用 `follow-fork-mode child` + `catch exec` 一路跟進子程式。
5. `catch fork` 觀察每次 fork 的子 pid。
6. （進階）寫一個 fork 出 3 個 worker 的 server 雛形，用 `detach-on-fork off` 同時 debug 所有 worker。

## 本章重點整理

- fork 是分裂（複製 process），exec 是換映像（同 process 換程式）。
- `follow-fork-mode parent`（預設，跟父）/ `child`（跟子）——bug 在子就要設 child。
- `detach-on-fork off` 保留父子兩個 inferior，配多 inferior 切換 = 同時 debug 父子。
- `follow-exec-mode same`（沿用 inferior 重載符號）/ `new`（開新 inferior）。
- `catch fork` / `catch exec` 在事件瞬間精準介入；exec 後要重設斷點。
- 底層是 ptrace 的 `PTRACE_O_TRACEFORK`/`TRACEEXEC` 等 option。

## 自我檢核

- [ ] 程式 fork 後 GDB 預設跟誰？bug 在子 process 怎麼辦？
- [ ] 想同時 debug 父和子，要設哪兩個選項？怎麼在它們間切換？
- [ ] fork 和 exec 的本質差別是什麼？為什麼 exec 後斷點會失效？
- [ ] 「程式 fork 然後子 exec 別的程式」要怎麼一路跟進？
- [ ] 這些 follow 功能底層靠哪些 ptrace option？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Debugging Forks](https://sourceware.org/gdb/current/onlinedocs/gdb/Forks.html)**
  - **讀哪裡**：follow-fork-mode、detach-on-fork、follow-exec-mode 全部。
  - **和本章的關聯**：本章核心的權威，含 vfork 與 checkpoint/restart 細節。

### 部落格 / 文章

- **[Debugging fork/exec with GDB](https://developers.redhat.com/blog/)** 類實戰文
  - **這篇說什麼**：debug fork server / exec 鏈的實際流程。
  - **和本章的關聯**：把選項組合放進真實場景。

### 參考

- **[man 2 ptrace — PTRACE_SETOPTIONS](https://man7.org/linux/man-pages/man2/ptrace.2.html)**
  - **讀哪裡**：PTRACE_O_TRACEFORK / TRACEEXEC / TRACECLONE 段。
  - **和本章的關聯**：follow 功能的底層機制；呼應 Ch 2。

Part 3 的進階執行控制都齊了。用練習 C 把多執行緒、watchpoint、條件斷點綜合起來，圍捕一個 race condition。

→ [練習 C：多執行緒 race condition 圍捕](./practice-c-race-condition.md)
