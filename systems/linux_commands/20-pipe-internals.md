# Ch 20 — pipe 底層機制

> **目標**：徹底理解管線（pipe，`|`）的底層——它是 kernel 裡的一個記憶體緩衝區，shell 怎麼用 `pipe()` syscall + fork + dup2 把兩個 process 的 fd 連起來、為什麼管線天生並行、buffer 滿/空時的阻塞、以及 SIGPIPE。這是 Unix「組合小工具」哲學（Ch 21）的物理基礎。

> **環境**：bash 5.x，Linux。pipe buffer 預設 64 KB（自 kernel 2.6.11）。

## 為什麼管線是 Unix 的靈魂？

你天天打 `ps aux | grep nginx | wc -l`。這個 `|` 是 Unix 最重要的發明之一——它讓你把小工具串成大工具，不用寫程式。但 `|` 底層到底發生什麼？兩個 process 怎麼「連起來」？資料怎麼流？這章挖開它。

理解管線的底層，你會懂：為什麼管線天生並行（不是 A 跑完才跑 B）、為什麼 `cmd | head` 後 cmd 會自己停（SIGPIPE）、為什麼有時管線會卡住（buffer 滿/空的阻塞）、為什麼 `cmd1 | cmd2` 的 exit code 預設只看 cmd2。這些都是 pipe 機制的直接後果。

## 先建立直覺：pipe 是 kernel 裡的一根水管

```
pipe（管線）：kernel 裡的一個記憶體緩衝區（一根水管）

  process A ──write──▶ ┌──────────────┐ ──read──▶ process B
   (ps)                │ pipe buffer  │           (grep)
                       │  (kernel 內   │
   stdout (fd 1) ─────▶│   64 KB 環形  │─────▶ stdin (fd 0)
                       │   緩衝區)     │
                       └──────────────┘
        │
  A 把輸出 write 進 pipe 的一端
  B 從 pipe 的另一端 read
        │
  關鍵特性：
  1. A 和 B 「同時」在跑（並行！不是 A 跑完才 B）
  2. buffer 有限（64 KB）：滿了 A 阻塞（等 B 讀），空了 B 阻塞（等 A 寫）
  3. A 不知道資料去了 B（A 只是 write fd 1，和寫終端機/檔案一樣）
        │
  → pipe 是「一切皆檔案」的又一例：A/B 用 read/write 操作 pipe
    就像操作普通檔案，不知道對方是誰
```

關鍵心智：pipe 是 kernel 裡的一個有限大小（64 KB）的記憶體緩衝區。寫端 write、讀端 read。兩個 process 同時跑（並行）。buffer 滿/空會阻塞。寫端/讀端都不知道對方是誰——它們只是 read/write 一個 fd（一切皆檔案）。

> 如果你對 fd 還不熟，先回看 [Ch 19 — file descriptor 與重導向](./19-fd-redirection.md)。pipe 完全建立在 fd 之上——管線就是「把一個 process 的 fd 1 接到另一個的 fd 0」。

## pipe() syscall：建立一根水管

shell 用 `pipe()` syscall 建立管線——它回傳兩個 fd（讀端和寫端）：

```c
// pipe() 的本質（C，理解用）
int pipefd[2];
pipe(pipefd);
// pipefd[0]：讀端（從這 read）
// pipefd[1]：寫端（往這 write）
//
// 寫進 pipefd[1] 的，能從 pipefd[0] 讀出來
// 中間是 kernel 的 64 KB 緩衝區
```

```
pipe() 回傳兩個 fd（一根水管的兩端）：

  pipe(pipefd)：
    pipefd[1] ──write──▶ [kernel buffer] ──read──▶ pipefd[0]
       寫端                                          讀端
        │
  單一 process 用 pipe（沒意義，但能驗證機制）：
    寫進 pipefd[1] → 從 pipefd[0] 讀出
        │
  真正的用途：fork 後，parent 和 child 各用一端
    → 跨 process 通訊（IPC）
```

```bash
# bash 內建 pipe 概念的驗證（用 coprocess，進階）
# 一般我們不直接用 pipe()，shell 幫我們做。但能觀察：
# strace 一個管線，看 pipe() syscall
strace -f -e trace=pipe2,dup2,clone bash -c 'echo hi | cat' 2>&1 | grep -E 'pipe|dup2'
# pipe2([3, 4], 0)      ← kernel 建 pipe，fd 3=讀端 4=寫端
# dup2(4, 1)            ← echo 的 fd 1（stdout）指向 pipe 寫端
# dup2(3, 0)            ← cat 的 fd 0（stdin）指向 pipe 讀端
```

## shell 怎麼把兩個 process 連起來

`echo hi | cat` 背後，shell 做了一套 pipe + fork + dup2 + exec 的舞蹈：

```
shell 執行 "echo hi | cat"：

  1. pipe(pipefd)           建一根水管 [讀端=3, 寫端=4]
        │
  2. fork() child A（給 echo）
       child A:
         dup2(4, 1)         fd 1（stdout）← pipe 寫端（Ch 19 的 dup）
         close(3); close(4) 關掉原本的 pipe fd（已 dup 到 1）
         exec("echo", "hi") echo 的輸出（write fd 1）進 pipe
        │
  3. fork() child B（給 cat）
       child B:
         dup2(3, 0)         fd 0（stdin）← pipe 讀端
         close(3); close(4)
         exec("cat")        cat 從 fd 0 讀 = 從 pipe 讀
        │
  4. parent（shell）:
         close(3); close(4) shell 自己不用 pipe，關掉
         wait(A); wait(B)   等兩個 child
        │
  結果：echo 的 stdout ──pipe──▶ cat 的 stdin
        echo 和 cat 「同時」在跑
```

> 管線的核心是 **dup2 + fork**。shell 先 `pipe()` 建水管，再 fork 兩個 child：一個 child 把 fd 1（stdout）`dup2` 到 pipe 寫端（echo 的輸出進 pipe），另一個 child 把 fd 0（stdin）`dup2` 到 pipe 讀端（cat 從 pipe 讀）。然後各自 exec。這完全是 Ch 15（fork/exec 的中間窗口）+ Ch 19（fd/dup）的應用——「中間窗口調整 child 的 fd」調整的就是把 fd 接到 pipe。echo 和 cat 都不知道彼此存在，它們只是 write/read 自己的 fd 1/0。**關掉沒用的 pipe fd 很關鍵**（否則 pipe 的寫端永遠開著，讀端永遠等不到 EOF——後述踩雷）。

## 管線天生並行

管線最重要的性質：所有 stage **同時**在跑，不是一個跑完才下一個：

```bash
# 驗證並行：兩個 stage 同時跑
(echo "stage1 start: $(date +%S)"; sleep 2; echo "stage1 done: $(date +%S)") \
  | (read line; echo "stage2 got: $line at $(date +%S)"; cat)
# stage2 got: stage1 start: 03 at 03   ← stage2 立刻拿到 stage1 的第一行（沒等 sleep）
# （證明兩者同時跑，stage1 還在 sleep 時 stage2 已經動了）

# 對比：如果是「跑完才下一個」，stage2 要等 stage1 整個結束
```

```
為什麼管線並行（而非循序）：

  循序（錯誤想像）：echo 全部跑完 → 存起來 → 才餵給 grep
    問題：echo 輸出 10 GB 怎麼辦？存哪？
        │
  並行（真實）：echo write 一點 → grep 立刻能 read 一點
    echo 和 grep 同時跑，資料「流動」
    buffer 滿了 echo 等一下（grep 讀走才繼續）
        │
  → 這就是為什麼 `cat huge.log | grep x` 不需要 huge.log 大小的記憶體
    資料「流過」pipe（一次 64 KB），不是整個載入
        │
  → 也是為什麼 `yes | head -5` 立刻結束（不會無限跑）
    head 讀 5 行就關 pipe，yes 收到 SIGPIPE 死掉
```

> 管線並行是它強大的根源。`cat 100GB.log | grep error | wc -l` 不需要 100 GB 記憶體——資料一次 64 KB 流過 pipe，每個 stage 處理一塊就丟。三個 process 同時跑，像工廠的輸送帶。這和「把整個檔案讀進記憶體再處理」（很多程式語言的天真做法）完全不同。理解這點，你會明白為什麼 Unix 管線能處理任意大的資料流——它是串流（streaming），不是批次（batch）。

## buffer 滿/空的阻塞與 SIGPIPE

pipe buffer 有限（64 KB），這帶來阻塞行為和 SIGPIPE：

```
pipe buffer 滿/空的阻塞：

  寫端快、讀端慢 → buffer 滿 → 寫端 write 阻塞（等讀端讀走）
  讀端快、寫端慢 → buffer 空 → 讀端 read 阻塞（等寫端寫入）
        │
  → 這是自動的「流量控制」（back-pressure）
    快的一方自動等慢的一方，不會爆記憶體
        │
  特殊情況 —— SIGPIPE：
  讀端關閉了（如 head 讀夠了就走），寫端還在 write
    → kernel 送 SIGPIPE 給寫端
    → 預設行為：寫端 process 被殺掉
        │
  這就是 `yes | head -5` 的機制：
    head 讀 5 行 → close 讀端 → yes 下次 write 收到 SIGPIPE → 死
    （否則 yes 會無限印下去）
```

```bash
# 驗證 SIGPIPE：head 關閉後 yes 死掉
yes | head -3
# y / y / y    ← head 讀 3 行就走，yes 收 SIGPIPE 死掉（不會無限跑）

# 看 SIGPIPE（用 strace）
strace -f yes 2>&1 | head -20 | grep -i sigpipe   # 可能看到 SIGPIPE
# 或檢查 exit：被 SIGPIPE 殺的 exit code = 128 + 13 = 141
yes | head -3 > /dev/null
echo "${PIPESTATUS[0]}"    # 141（yes 被 SIGPIPE 殺，128+13）

# buffer 滿的阻塞（寫端快讀端慢）
# 寫端拼命寫，讀端 sleep → 寫端會在 buffer 滿（64KB）後阻塞
```

> **SIGPIPE 是管線「提早結束」的機制**。`command | head` 時，head 讀夠了就關閉讀端，command 下次 write 會收到 SIGPIPE（預設殺死 command）。這就是為什麼 `yes | head` 不會無限跑、`find / | head` 找到夠了就停。`PIPESTATUS` 陣列能看到每個 stage 的 exit code（被 SIGPIPE 殺 = 141 = 128+13）。注意：有些程式會 ignore SIGPIPE 自己處理 write 錯誤——這時 `command | head` 可能讓 command 繼續跑（直到它自己發現 write 失敗）。理解 SIGPIPE 解釋了管線的「短路」行為，也是 debug「為什麼上游程式提早死了」的關鍵。

## 故意弄壞：忘記關 pipe 寫端 → 讀端卡死

```bash
# pipe 的經典陷阱：寫端沒關，讀端永遠等不到 EOF
# 用 mkfifo（命名管線，Ch 8）演示這個 deadlock 概念
cd ~/cmdlab
mkfifo mypipe                # 建一個命名 pipe（檔案系統裡的 pipe）
# 終端機 1：
cat mypipe                   # 讀端：會卡住等資料（等寫端寫 + 關閉）
# 終端機 2：
echo "hello" > mypipe        # 寫端：寫入並關閉 → 終端機 1 的 cat 收到資料 + EOF 後結束

# 如果寫端一直開著不關，讀端永遠等不到 EOF（卡死）
# 這就是為什麼 shell 在 fork 後要 close 沒用的 pipe fd——
# 否則 pipe 寫端還開著（在 shell 或其他 child 手上），讀端永遠收不到 EOF
rm mypipe
```

這驗證了前面說的「關掉沒用的 pipe fd 很關鍵」——pipe 的讀端要收到 EOF（資料結束），必須**所有**寫端都 close。如果 shell 或某個 child 忘了關寫端，讀端會永遠卡著等。這是寫 pipe 程式（C 層）的經典 bug。

## 踩雷集錦

1. **以為管線循序執行**：管線所有 stage 並行（同時跑）。不是 A 全跑完才 B。資料流動，不是批次傳遞

2. **以為 cmd1 | cmd2 的 exit code 是 cmd1 的**：預設是**最後一個** stage（cmd2）的 exit code。要看每個用 `PIPESTATUS` 陣列，或設 `set -o pipefail`（Ch 35）

3. **管線裡的變數賦值「消失」**：`echo x | read var` 後 `$var` 是空的——因為管線每個 stage 在**subshell**（子 process）跑，變數改動不影響父 shell（Ch 34 詳述這個陷阱）

4. **以為 SIGPIPE 一定殺死上游**：有些程式 ignore SIGPIPE 自己處理 write 錯誤。多數情況 `| head` 會讓上游停，但不保證立刻

5. **buffer 行為造成「卡住」錯覺**：管線中間的程式如果做了 full buffering（如 grep 在非終端機輸出時），輸出會延遲（buffer 滿才送）。要即時用 `stdbuf -oL` 或程式的 line-buffer 選項（Ch 13 buffering）

## 進階：多 stage 管線與 process substitution

長管線和 process substitution 是 pipe 機制的延伸：

```bash
# 多 stage 管線：每個 | 都是一根 pipe，N 個 stage = N-1 根 pipe
ps aux | grep nginx | grep -v grep | awk '{print $2}' | head -5
#  4 根 pipe，5 個 process 同時跑

# process substitution（<(...)）：把命令的輸出當成檔案
# 底層也是 pipe（/dev/fd/N）
diff <(sort file1) <(sort file2)
#   <(sort file1) 變成一個檔案路徑（/dev/fd/63），內容是 sort 的輸出
#   diff 讀這個「檔案」= 讀一個 pipe
ls -l <(echo hi)            # /dev/fd/63（一個 pipe，Ch 8/19）

# 把管線輸出存檔「同時」顯示（tee，Ch 22 預習）
ps aux | tee processes.txt | grep nginx
#   tee 把輸入同時寫檔案和 stdout（往下一個 pipe）

# named pipe（FIFO，Ch 8）：檔案系統裡的持久 pipe
mkfifo /tmp/mypipe
# 一端寫、另一端讀，跨不相關的 process（不像 | 只在同一條命令）
```

> **process substitution（`<(...)`）是 pipe 的高級應用**。`<(sort file1)` 把 sort 的輸出接到一個 pipe，並把 pipe 的路徑（`/dev/fd/63`，Ch 8 的 /dev/fd）當成一個「檔案名」傳給命令。所以 `diff <(sort a) <(sort b)` 能比較兩個命令的輸出，不用先存暫存檔。底層是 bash 建 pipe + 把 pipe 暴露成 /dev/fd/N。這解決了「管線只能線性串接」的限制——你能把多個命令的輸出餵給一個需要多個檔案參數的命令。`mkfifo`（命名 pipe）則是把 pipe 放進檔案系統（持久存在），讓不相關的 process 也能用 pipe 通訊。這些都是同一個 pipe 機制的不同包裝。

## 動手練習

1. strace 一個管線：`strace -f -e trace=pipe2,dup2,clone bash -c 'echo hi | cat'`，找出 pipe2/dup2，對照「shell 怎麼連兩個 process」那節

2. 驗證並行：跑「管線天生並行」那節的例子，確認 stage2 在 stage1 還在 sleep 時就動了

3. 驗證 SIGPIPE：`yes | head -3; echo ${PIPESTATUS[0]}`，看 141（yes 被 SIGPIPE 殺）

4. 玩 named pipe：`mkfifo p`，一個終端機 `cat p`（卡住），另一個 `echo hi > p`，看資料流過 + cat 結束

## 本章重點整理

- pipe 是 kernel 裡的有限大小（64 KB）記憶體緩衝區；寫端 write、讀端 read，兩端都不知道對方是誰（一切皆檔案）
- shell 用 pipe() + fork + dup2 把一個 process 的 fd 1 接到另一個的 fd 0（Ch 15 + Ch 19 的應用）
- 管線所有 stage 並行（同時跑），資料流動（streaming）不是批次——所以能處理任意大的資料
- buffer 滿/空自動阻塞（流量控制）；讀端關閉 → 寫端收 SIGPIPE（`yes | head` 提早結束的機制）
- process substitution（`<(...)`）和 named pipe（mkfifo）是同一個 pipe 機制的延伸

## 自我檢核

- [ ] 能解釋 pipe 是什麼（kernel 的緩衝區），以及它怎麼體現「一切皆檔案」
- [ ] 能說出 shell 怎麼用 pipe + fork + dup2 把兩個 process 連起來
- [ ] 理解管線為什麼天生並行，以及這為什麼讓它能處理任意大的資料
- [ ] 知道 SIGPIPE 是什麼，以及 `yes | head` 為什麼會停
- [ ] 能解釋 process substitution（`<(...)`）底層是什麼

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 44 (Pipes and FIFOs)** — Michael Kerrisk
  - **讀哪幾章**：Ch 44（pipe/FIFO 的完整機制，含 pipe()、SIGPIPE、buffer 大小）；Ch 63 提到 pipe 在 I/O multiplexing 的角色
  - **這本書的定位**：pipe 機制的權威來源，本章的底層全部來自這裡
  - **前提**：本章 + Ch 15（fork/exec）+ Ch 19（fd）

### 文章

- **[The Unix pipe and how it works](https://www.gnu.org/software/libc/manual/html_node/Pipe-to-a-Subprocess.html)** — GNU libc manual
  - **讀哪裡**：Pipes and FIFOs 整節
  - **為什麼值得讀**：官方 libc 對 pipe 的描述，含 C 層的 pipe()/dup2 用法

- **[Pipes and FIFOs](https://man7.org/linux/man-pages/man7/pipe.7.html)** — Linux man-pages（pipe(7)）
  - **讀哪裡**：整篇，特別是 "Pipe capacity"（buffer 大小）和 "Open file status flags"
  - **為什麼值得讀**：權威定義 pipe 的容量（64 KB）、原子寫入大小（PIPE_BUF）、阻塞行為

### 歷史

- **[The origin of the pipe](https://www.bell-labs.com/usr/dmr/www/hist.html)** — Dennis Ritchie
  - **讀哪裡**：搜尋 "pipe" 段落，Doug McIlroy 提出管線的故事
  - **為什麼值得讀**：管線是 Unix 哲學的起源，理解它的歷史背景（McIlroy 堅持要這個功能）能理解 Ch 21 的哲學

→ [Ch 21 管線哲學：組合小工具](./21-pipeline-philosophy.md)
