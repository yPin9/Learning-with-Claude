# Ch 3 — 啟動、附加、inferior 管理

> **目標**：把「讓 GDB 開始控制一個程式」的所有方式吃透——`run` / `start` / `attach` / load core / 多 inferior。並搞懂傳參數、環境變數、工作目錄、stdin/stdout 重導向這些「程式怎麼被啟動」的細節，因為 bug 常常就藏在啟動條件裡。

> **環境**：GDB 13/14，Linux x86_64。

## 為什麼啟動方式值得一整章？

「不就 `gdb ./a.out` 然後 `run` 嗎？」——這想法會讓你卡在很多真實場景：

- bug 只在帶特定參數時出現
- bug 只在某個環境變數設定下出現
- 程式從 stdin 讀資料，你要餵它
- 程式已經跑起來卡死了，你要 attach 上去看它在幹嘛
- 程式 fork 出一堆子 process，你要 debug 子代
- 程式已經崩潰留下 core，你要驗屍（Ch 33 細講）

每一種對應不同的「讓 GDB 接手」的方式。這章把它們一次講清楚。

## 先建立直覺：四種「接手」的入口

```
                  GDB 控制一個 inferior 的四種來源
   ┌────────────────────────────────────────────────────────────┐
   │                                                              │
   │  ① run / start    從零啟動一個新 process（最常見）          │
   │  ② attach <pid>   接管一個正在跑的 process                  │
   │  ③ target core    驗屍一個已死 process 的 core dump（靜態）  │
   │  ④ target remote  接到遠端 gdbserver / 模擬器（Ch 36）       │
   │                                                              │
   └────────────────────────────────────────────────────────────┘
        ①② 是活的 process（能 continue）；③ 是死的快照（只能看）
```

①② 對應 Ch 2 的兩種 ptrace 建立方式；③④ 是其他 target。這章主攻 ①②，③留 Ch 33、④留 Ch 36。

## `run` 與 `start`

```bash
gdb -q ./hello
```

```
(gdb) run                 # 從頭跑，直到斷點/結束/崩潰
(gdb) start               # = tbreak main + run，停在 main 第一行
```

`start` 是 `run` 的貼心版：它先在 `main` 下一個**臨時斷點**（temporary breakpoint，碰一次就消失，Ch 4），再 run。當你只想「進去就停、慢慢走」時，`start` 比「`break main` + `run`」少打一個字。

> 進階：`starti` 停在**更早**——程式的第一條指令（在 `_start`、動態連結器跑之前那刻）。要 debug C runtime 啟動、`.init` array、constructor 時用得到。

### 重跑

斷點命中後想重來：直接再 `run`。GDB 會問要不要殺掉現有 inferior 重啟（`set confirm off` 可免問）。斷點、watchpoint、設定全部保留。這是 GDB 比「printf 重編譯」快的關鍵——改條件、重跑，秒級迭代。

## 傳參數給程式

三種方式，效果相同：

```bash
# 方式 1：啟動 gdb 時用 --args（最推薦，跟平常下指令一樣）
gdb --args ./myprog -v --input data.txt

# 方式 2：在 gdb 內設
(gdb) set args -v --input data.txt
(gdb) run

# 方式 3：直接附在 run 後面
(gdb) run -v --input data.txt
```

確認目前參數：`show args`。

> 踩雷：`run -v` 的參數**只對這次 run 有效**嗎？不，它會更新 `set args`，下次 `run` 沿用。要清空：`set args`（後面不接東西）。

## 環境變數、工作目錄、I/O 重導向

bug 常藏在「程式被啟動的條件」裡，這些都能在 GDB 裡控制：

```
(gdb) set environment LANG=C            # 設環境變數
(gdb) unset environment http_proxy      # 移除
(gdb) show environment PATH             # 看

(gdb) cd /path/to/workdir               # 設 inferior 的工作目錄
(gdb) pwd

(gdb) set cwd /path/to/workdir          # GDB 8.3+ 更精確的設工作目錄

# I/O 重導向：就在 run 後面用 shell 語法
(gdb) run < input.txt > output.txt 2> err.txt
```

`run < input.txt` 特別實用——程式從 stdin 讀東西時，你不用手動敲，直接餵檔案。CTF / fuzzing crash 重現幾乎都靠這招。

> 認識論誠實：GDB 啟動 inferior 時，預設會**透過一個 shell**（`/bin/sh -c`）來跑，所以上面的重導向 `<`、`>` 才有效。如果 `set startup-with-shell off`，這些 shell 語法就不通了，但啟動更乾淨（沒有中間的 shell process）。多數時候維持預設即可。

## attach：接管一個活著的 process

程式已經跑起來（卡死、無窮迴圈、或你想看它穩定狀態），用 attach：

```bash
# 找 PID
pgrep myprog          # 或 ps aux | grep myprog

gdb -p 12345          # 啟動時直接 attach
# 或
gdb
(gdb) attach 12345
```

attach 的瞬間：

1. GDB 對該 PID 做 `PTRACE_ATTACH`（或 SEIZE），process 被**凍結**。
2. GDB 載入它的符號（從 `/proc/<pid>/exe` 找到執行檔）。
3. 你看到它**當下停在哪**——通常是某個 syscall 裡（`read`、`poll`、`futex`）。

```
(gdb) attach 12345
Attaching to process 12345
...
0x00007f... in __GI___libc_read (fd=0, ...) at ../sysdeps/unix/sysv/linux/read.c:26
(gdb) bt                  # 看它卡在哪
```

debug 完要讓它繼續自由活：

```
(gdb) detach              # 放手，process 恢復自由執行（GDB 不再控制它）
```

`detach` 和 `kill` / `quit` 的差別很重要：

| 動作 | inferior 的下場 |
|---|---|
| `detach` | 繼續活著、自由執行 | 
| `kill` | 被殺掉 |
| `quit`（attach 來的） | GDB 問你要 detach 還是 kill |
| `quit`（run 起來的） | 預設殺掉 |

> 踩雷：attach 一個正在服務的線上 process，它在你 debug 期間是**完全凍結**的——對外停止回應。生產環境 attach 要有心理準備（連線會 timeout）。要降低衝擊，考慮 non-stop mode（Ch 15）或直接用 core dump（Ch 33）。

### attach 的權限

承 Ch 2：attach 非子孫 process 常遇 `ptrace: Operation not permitted`。檢查順序：

1. `cat /proc/sys/kernel/yama/ptrace_scope`（=1 是常見元兇）
2. 是不是別人的 process（需要 root）
3. 是不是已被別的 tracer 佔用

## 多 inferior：一個 GDB 管多個程式

GDB 能同時控制多個 inferior——debug client/server、parent/child、或多個獨立程式時非常有用。

```
(gdb) info inferiors
  Num  Description       Connection           Executable
* 1    process 12345     1 (native)           /path/server

(gdb) add-inferior                 # 新增一個空 inferior
Added inferior 2
(gdb) inferior 2                   # 切換過去
(gdb) file ./client               # 給它一個執行檔
(gdb) run                         # 跑它

(gdb) inferior 1                   # 切回去
(gdb) info inferiors              # * 標示目前焦點
```

每個 inferior 有自己的 breakpoint scope、記憶體、暫存器。多 inferior 最常見的觸發是 `follow-fork-mode`（Ch 17）——程式 fork 時 GDB 自動建第二個 inferior 來跟子代。

## 一個完整的啟動實戰

把這章串起來。假設 `crasher` 只在「參數含 `--unsafe`、環境有 `DEBUG=1`、從 stdin 讀到特定輸入」時崩潰：

```
$ gdb -q --args ./crasher --unsafe
(gdb) set environment DEBUG=1
(gdb) set cwd /tmp/crasher-workdir
(gdb) run < crash-input.txt
...
Program received signal SIGSEGV, Segmentation fault.
0x0000555555555234 in process_record (...) at crasher.c:88
(gdb) bt
```

精確重現崩潰條件，是 debug 的第一步、也是最常被忽略的一步。

## 踩雷集錦

1. **參數忘了帶**：`run` 不帶參數 ≠ 帶空參數。很多 bug 根本沒重現，因為你忘了程式平常是帶 `--config x` 跑的。`show args` 確認。
2. **環境不一致**：在 GDB 裡 `run` 的環境繼承自你的 shell，可能和線上 systemd / cron 啟動的環境差很多（`PATH`、`LANG`、`LD_LIBRARY_PATH`）。bug 重現不了時，先比對環境。
3. **`detach` 與 `quit` 搞混殺掉了線上 process**：attach 線上服務後直接 `quit` 又選了 kill，服務就掛了。記得 `detach`。
4. **attach 後忘記它被凍結**：你在 GDB 裡發呆三分鐘，線上請求全部 timeout。attach 生產要快進快出，或用 core dump。
5. **`run` 之後改了參數沒重跑**：`set args` 改了只在下次 `run` 生效，當前 inferior 的參數不會變。
6. **多 inferior 下對錯 inferior 操作**：`print x` 印出來怪怪的？看一下 `info inferiors` 的 `*` 在哪、`info threads` 焦點對不對。

## 進階：再往深一層

- **`set follow-exec-mode`**：程式 `execve` 換了一個全新映像時，GDB 要沿用同一個 inferior（`same`）還是開新的（`new`）。debug exec 鏈（shell → 子程式）時關鍵，Ch 17 細講。
- **`set disable-randomization`**：GDB 預設**關掉 ASLR**（`on`），讓每次 run 的位址一致、好 debug。要重現「只在 ASLR 開啟時出現」的 bug，得 `set disable-randomization off`。Ch 40 會深入 ASLR。
- **`gcore` / `generate-core-file`**：對 attach 上的 process 當場產生 core dump，再 detach 讓它繼續——「拍快照」而不打斷服務太久。Ch 33。
- **`--pid` + `--batch` 自動化**：`gdb -p <pid> -batch -ex bt -ex detach` 一行抓 backtrace 就走，很適合 production 的「快速看一眼」。

## 動手練習

1. 寫一個讀 `argv[1]` 當陣列索引、不檢查邊界的小程式。用 `gdb --args ./prog 9999` 重現越界，再用 `set args` 改成合法值重跑，體會「同一 session 改條件重跑」。
2. 寫一個從 stdin 讀整數、除以它的程式（會 0 除）。用 `run < zero.txt` 餵 `0` 重現 `SIGFPE`。
3. 起一個 `python3 -c "import time; time.sleep(999)"`，`gdb -p` attach，`bt` 看它卡在哪個 syscall，`detach` 放掉，確認它還活著。
4. 用 `add-inferior` 同時載入兩個不同程式，各 `run` 一次，練習 `inferior` 切換與 `info inferiors`。

## 本章重點整理

- 讓 GDB 接手有四個入口：run/start（新生）、attach（接管活的）、target core（驗屍）、target remote（遠端）。
- `start` = tbreak main + run；`starti` 停在第一條指令。
- 參數（`--args`/`set args`）、環境（`set environment`）、工作目錄（`set cwd`）、I/O 重導向（`run < in`）都能在 GDB 裡控制——重現 bug 的條件。
- attach 會凍結目標 process；`detach` 放手、`kill` 殺掉，別搞混。
- 多 inferior 讓一個 GDB 管多個程式，是 follow-fork 的基礎。

## 自我檢核

- [ ] `start` 和 `run` 差在哪？`starti` 又停在哪？
- [ ] bug 重現不了，你會依序檢查哪些「啟動條件」？
- [ ] attach 線上 process 有什麼風險？怎麼降低？
- [ ] `detach`、`kill`、`quit` 對 inferior 的下場各是什麼？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Running Programs Under GDB](https://sourceware.org/gdb/current/onlinedocs/gdb/Running.html)**
  - **讀哪裡**：Arguments、Environment、Working Directory、Input/Output、Attach、Kill 各小節。
  - **和本章的關聯**：本章每個指令的完整選項在此；`set startup-with-shell`、`disable-randomization` 的精確語意也在這。

- **[GDB Manual: Debugging Multiple Inferiors](https://sourceware.org/gdb/current/onlinedocs/gdb/Inferiors-Connections-and-Programs.html)**
  - **讀哪裡**：`add-inferior`、`inferior`、`clone-inferior` 那段。
  - **和本章的關聯**：Ch 17 follow-fork 的前置。

### 部落格 / 文章

- **[The Yama ptrace_scope](https://www.kernel.org/doc/html/latest/admin-guide/LSM/Yama.html)** — Linux kernel docs
  - **這篇說什麼**：`ptrace_scope` 四個值的精確語意與安全考量。
  - **為什麼值得讀**：attach 失敗時，這是權威解釋；別在正式機亂設 0。

下一章進入 debug 的核心動作之一：breakpoint。先建立完整的 breakpoint 觀念地圖，底層 INT3 機制留到 Ch 39。

→ [Ch 4 Breakpoint 的世界](./04-breakpoints-overview.md)
