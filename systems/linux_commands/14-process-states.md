# Ch 14 — process 狀態機

> **目標**：理解 process 是什麼、它的狀態機（running/sleeping/stopped/zombie）、狀態之間如何轉換、以及 zombie 和 orphan process 的成因。這是理解所有 process 管理（ps/signal/job control）的基礎。

> **環境**：Linux，/proc。承接 Ch 1（fork+exec）。原理深挖章。

## 為什麼要懂 process 狀態機？

你會用 `ps`、`kill`、`&`，但這些背後是 process 的「狀態機」——一個 process 不是只有「活著」和「死了」，它有多種狀態：正在 CPU 跑（running）、等 I/O（sleeping）、被暫停（stopped）、死了但沒被回收（zombie）。

理解這個狀態機，無數謎之現象就清楚了：為什麼 `kill` 殺不掉某些 process（在 uninterruptible sleep）、為什麼 ps 看到 `<defunct>`（zombie）、為什麼程式「卡住」（在等什麼）。這是 Part 4 所有 process 工具的理論基礎。

## 先建立直覺：process 的狀態機

```
process 的狀態機（簡化）：

         fork
          │
          ▼
    ┌──────────┐  被 scheduler 選中    ┌──────────┐
    │  Ready   │ ───────────────────→ │ Running  │  在 CPU 上跑
    │（可執行）│ ←─────────────────── │（執行中）│
    └──────────┘  時間片用完/被搶佔    └────┬─────┘
                                           │
              等 I/O / sleep ──────────────┤
                     │                     │ 收到 SIGSTOP
                     ▼                     ▼
              ┌──────────┐          ┌──────────┐
              │ Sleeping │          │ Stopped  │  被暫停（Ctrl-Z）
              │（等待中）│          │（暫停）  │
              └──────────┘          └──────────┘
                                           │
                  exit() ──────────────────┤
                     │                     │ 收到 SIGCONT → 回 Ready
                     ▼
              ┌──────────┐
              │ Zombie   │  死了但 parent 還沒回收
              │（殭屍）  │
              └────┬─────┘
                   │ parent wait() 回收
                   ▼
                 消失
```

process 不是「活/死」二元，而是一個狀態機。它在 Ready（可執行，等 CPU）、Running（在 CPU 跑）、Sleeping（等 I/O 或主動 sleep）、Stopped（被暫停）、Zombie（死了沒被回收）之間轉換。理解每個狀態和轉換，是 process 管理的基礎。

## ps 看到的 process 狀態

```bash
# ps 顯示 process 狀態（STAT 欄）
ps aux | head
# USER  PID  %CPU %MEM  ...  STAT  ...  COMMAND
# you   1234 0.0  0.1   ...  S     ...  bash
#                            ↑ 狀態碼

# 狀態碼（STAT 欄）：
#   R  Running 或 Runnable（在 CPU 跑或 ready）
#   S  Sleeping（interruptible，等事件，能被 signal 喚醒）★ 最常見
#   D  Uninterruptible sleep（等 I/O，不能被 signal 中斷）★ 危險
#   T  Stopped（被暫停，如 Ctrl-Z）
#   Z  Zombie（死了沒被回收）
#   I  Idle（kernel thread 閒置）
# 附加標記：
#   <  高優先序   N  低優先序   s  session leader   +  前景 process
```

```bash
# 看一個 process 的詳細狀態（/proc）
cat /proc/self/status | grep State
# State: R (running)        ← 當前 process（cat 自己）在跑
```

## 各狀態詳解

### R — Running / Runnable

```
R 狀態：
  - Running：此刻正在某個 CPU 上執行
  - Runnable：在 run queue 裡等 CPU（ready，scheduler 還沒選到它）
  - ps 不區分這兩個（都顯示 R）
        │
  → CPU 密集的程式（計算）多在 R
    一個 CPU 同時只能跑一個 R，其他 R 在排隊（等 scheduler）
```

### S — Interruptible Sleep（最常見）

```
S 狀態（interruptible sleep）：
  process 在「等某個事件」：
    - 等 I/O 完成（讀磁碟、等網路）
    - 等 user 輸入
    - sleep(N) 主動睡
    - 等 child（wait）
        │
  「interruptible」：能被 signal 喚醒/中斷
    → kill 能打斷它（signal 送達，process 醒來處理）
        │
  → 大部分「閒著」的 process 在 S（等事件）
    你的 shell 等你打字 = S；web server 等連線 = S
```

### D — Uninterruptible Sleep（危險）

```
D 狀態（uninterruptible sleep）：
  process 在「等某個不能被中斷的操作」：
    - 通常是等「磁碟 I/O」（讀寫硬碟、NFS）
    - kernel 認為這個操作不能被打斷（中斷會破壞一致性）
        │
  「uninterruptible」：signal 不能中斷它！
    → kill -9 也殺不掉（要等 I/O 完成才能處理 signal）
        │
  → D 狀態的 process「卡住」且 kill 不動，通常是：
    - 磁碟壞了/很慢（等 I/O 永遠不完成）
    - NFS server 掛了（等網路檔案系統回應）
  → 大量 D 狀態 = I/O 問題（系統 load 高但 CPU 閒）
```

> **D 狀態（uninterruptible sleep）是「kill -9 殺不掉」的根本原因**。process 在等磁碟 I/O 時進入 D，這個等待不能被 signal 中斷（中斷會破壞 I/O 一致性）。所以 `kill -9`（最強的 signal，Ch 17）也殺不掉 D 狀態的 process——它要等 I/O 完成才能處理任何 signal。如果 I/O 永遠不完成（磁碟壞了、NFS server 掛了），process 永遠卡在 D，kill 不動。看到「kill -9 都殺不掉」，先 `ps` 看是不是 D 狀態——是的話問題在 I/O（磁碟/網路），不是 process 本身。這也是為什麼系統 load average 高但 CPU 使用率低時，常是大量 D 狀態（等 I/O，不佔 CPU 但算進 load）。

### T — Stopped

```
T 狀態（stopped）：
  process 被「暫停」（不是結束，是凍結）：
    - Ctrl-Z（送 SIGTSTP）→ 前景程式暫停
    - kill -STOP（送 SIGSTOP）
        │
  暫停的 process 不執行（不佔 CPU）但還在（記憶體保留）
    → SIGCONT（kill -CONT 或 fg/bg）讓它繼續
        │
  → Ch 18（job control）的 Ctrl-Z 就是讓 process 進 T 狀態
```

### Z — Zombie

```
Z 狀態（zombie / defunct）：
  process 已經 exit()（執行結束），但 parent 還沒 wait() 回收它
        │
  為什麼會有 zombie：
    child 結束時，kernel 保留它的「退出狀態」（exit code）
    等 parent 用 wait() 來讀這個退出狀態
    在 parent wait() 之前，child 是 zombie（屍體還沒被收）
        │
  zombie 佔什麼：
    幾乎不佔資源（記憶體已釋放，只剩 process table 的一個 entry）
    但佔一個 PID
        │
  → 大量 zombie = parent 有 bug（沒正確 wait child）
    殺 zombie 沒用（它已經死了）——要殺或修 parent
```

## zombie 和 orphan：兩個容易混淆的概念

```
zombie（殭屍）：
  child 死了，但 parent 還沒 wait() 回收
  → child 是 zombie（屍體沒被收）

orphan（孤兒）：
  parent 死了，但 child 還活著
  → child 是 orphan（沒了父母）
  → kernel 把 orphan 過繼給 init（PID 1，Ch linux_boot）
    init 會負責 wait() 它們（所以 orphan 不會變永久 zombie）
        │
  → zombie：child 死，parent 活但沒回收
    orphan：parent 死，child 活，被 init 收養
```

```bash
# 製造一個 zombie 觀察
cat > zombie.sh <<'EOF'
#!/bin/bash
# parent fork child，child 立刻死，但 parent 不 wait（sleep）
( sleep 0 ) &        # child：立刻結束
sleep 30             # parent：睡 30 秒不 wait child
EOF
# 實際上 bash 會自動 reap，要用 C 才好觀察。概念示範：

# 看系統現有的 zombie（如果有）
ps aux | awk '$8 ~ /Z/ {print}'    # STAT 含 Z 的
# 或
ps aux | grep defunct
```

> zombie 和 orphan 是 process 生命週期的兩個邊界情況，常被搞混。**zombie**：child 先死，parent 還沒 `wait()`（屍體沒收）——child 變 zombie，佔 process table。**orphan**：parent 先死，child 還活著——child 變 orphan，被 init（PID 1）收養（init 會 wait 它們，所以 orphan 最終會被正確回收，不變永久 zombie）。這解釋了 linux_boot 課程的「init 收養孤兒 process」——orphan 過繼給 PID 1，init 負責回收。大量 zombie 是程式 bug（parent 沒 wait），殺 zombie 沒用（已死），要處理 parent。

## 故意弄壞：製造卡在 D 狀態

```bash
# D 狀態通常由慢 I/O 造成，難在 sandbox 安全製造
# 但可以觀察：跑一個 I/O 密集的東西，瞬間可能看到 D

# 跑大量磁碟寫入，另一個終端機觀察
dd if=/dev/zero of=~/cmdlab/big.dat bs=1M count=1000 &
# 同時觀察
watch -n 0.5 'ps aux | grep "[d]d"'
# 可能瞬間看到 dd 在 D（等磁碟 I/O）

# 製造 stopped（T）狀態
sleep 100 &
PID=$!
kill -STOP $PID                  # 送 SIGSTOP，process 進 T
ps -p $PID -o stat               # T（stopped）
kill -CONT $PID                  # 送 SIGCONT，恢復
ps -p $PID -o stat               # S（sleeping，繼續 sleep）
kill $PID                        # 清理
```

## 踩雷集錦

1. **以為 process 只有「活/死」**：process 有狀態機（R/S/D/T/Z）。理解狀態才能 debug「卡住」「殺不掉」「defunct」

2. **kill -9 殺不掉 D 狀態 process**：D（uninterruptible sleep）等 I/O，signal 不能中斷。kill 不動是因為它在等 I/O（磁碟/NFS 問題），不是 process 賴著

3. **想殺 zombie**：zombie 已經死了，殺它沒用。要消除 zombie 要讓 parent wait（或殺 parent，讓 init 收養回收）

4. **混淆 zombie 和 orphan**：zombie（child 死 parent 沒回收）vs orphan（parent 死 child 活，被 init 收養）。不同情況

5. **以為 sleeping 的 process 有問題**：S（interruptible sleep）是正常的——大部分 process 閒著等事件（等輸入、等連線）都在 S。S 不是問題，D 才要注意

## 進階：process 在 kernel 裡的表示

每個 process 在 kernel 裡是一個 `task_struct`，狀態機反映在它的欄位：

```
process 在 kernel 的表示（task_struct，簡化）：
  - PID、PPID（parent PID）
  - state（R/S/D/T/Z 對應 kernel 的 TASK_RUNNING/INTERRUPTIBLE/...）
  - 記憶體映射（mm_struct）
  - 開啟的檔案（fd table，Ch 19）
  - 信號處理（Ch 17）
  - CWD（Ch 3）、credentials（UID/GID，Ch 7）
  - 排程資訊（優先序、時間片）
        │
  /proc/<pid>/ 就是把 task_struct 的資訊暴露成檔案（Ch 16）
    /proc/<pid>/status → state、PID、記憶體...
    /proc/<pid>/fd/    → 開啟的 fd（Ch 19）
        │
  → 你用 ps 看的、kill 操作的，底層都是這個 task_struct
```

```bash
# /proc 暴露 task_struct 的資訊
cat /proc/self/status | head -20
# Name:   cat
# State:  R (running)         ← state 欄位
# Pid:    12345
# PPid:   12000               ← parent PID
# ...
```

> kernel 用 `task_struct`（一個大結構）表示每個 process，狀態機的狀態（R/S/D/T/Z）對應它的 `state` 欄位（kernel 內叫 `TASK_RUNNING`/`TASK_INTERRUPTIBLE`/`TASK_UNINTERRUPTIBLE`/...）。`/proc/<pid>/`（Ch 16）就是把 task_struct 的資訊暴露成檔案——你 `cat /proc/<pid>/status` 看到的 State、PID、記憶體，都是 task_struct 的欄位。`ps`/`kill`/`top` 操作的都是這個結構。如果你修過 kernel_pwn 或 bpf 課程，會認出 task_struct 是 kernel 觀測和利用的核心。理解 process = task_struct，你會懂 /proc 為什麼能看到那麼多 process 資訊（直接讀 task_struct）。

## 動手練習

1. 看狀態：`ps aux` 看各 process 的 STAT 欄，找出 R（跑）、S（睡）的。`cat /proc/self/status | grep State` 看當前 process 狀態

2. 製造 T 狀態：`sleep 100 &`，`kill -STOP <pid>` 看進 T，`kill -CONT` 恢復 S，`kill` 清理。理解 stopped 不是結束

3. 找 zombie/D：`ps aux | grep defunct`（zombie，如果有）、跑 I/O 密集（dd）瞬間可能看到 D。理解 D 殺不掉的原因

4. 看 /proc 的 task_struct：`cat /proc/$$/status`（你 shell 的）、`cat /proc/$$/stat`（更底層的單行格式）。對照本章的狀態機

## 本章重點整理

- process 是狀態機（不是活/死二元）：R（跑/ready）、S（interruptible sleep）、D（uninterruptible sleep）、T（stopped）、Z（zombie）
- S（最常見）= 等事件，能被 signal 喚醒；D = 等 I/O，signal 不能中斷（kill -9 也殺不掉）
- T = 被暫停（Ctrl-Z/SIGSTOP），SIGCONT 恢復；Z = 死了但 parent 沒 wait 回收（佔 process table）
- zombie（child 死 parent 沒回收）vs orphan（parent 死 child 活，被 init 收養回收）
- process 在 kernel 是 task_struct，/proc/<pid>/ 暴露它；狀態對應 task_struct 的 state 欄位

## 自我檢核

- [ ] 能畫出 process 狀態機並說出各狀態（R/S/D/T/Z）的意義
- [ ] 能解釋為什麼 kill -9 殺不掉 D 狀態的 process
- [ ] 能區分 zombie 和 orphan，以及各自怎麼被回收
- [ ] 知道 S 狀態（等事件）是正常的，D 狀態才要注意
- [ ] 知道 process 在 kernel 是 task_struct，/proc 怎麼暴露它

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 25 (Process Termination), Ch 26 (Monitoring Child Processes)** — Michael Kerrisk
  - **讀哪幾章**：Ch 25（exit、zombie）、Ch 26（wait、orphan、init 收養）
  - **這本書的定位**：process 生命週期的權威來源
  - **前提**：本章

### 部落格 / 文章

- **[The process states](https://jvns.ca/blog/2016/10/24/decoding-magic-numbers/)** 或 Julia Evans 關於 process 的文章
  - **這篇說什麼**：用易懂方式講 process 狀態、zombie、D 狀態
  - **讀哪裡**：process 狀態相關段落
  - **為什麼值得讀**：把狀態機講得更生活化

### 官方文件

- **[proc(5) man page - /proc/[pid]/stat](https://man7.org/linux/man-pages/man5/proc.5.html)**
  - **讀哪裡**：/proc/[pid]/stat 和 status 的 state 欄位
  - **學什麼**：process 狀態在 /proc 的精確表示
  - **前提**：本章

→ [Ch 15 fork/exec/wait](./15-fork-exec-wait.md)
