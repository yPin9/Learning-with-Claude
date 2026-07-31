# Ch 18 — job control 與 nohup/disown

> **目標**：理解 shell 的 job control——前景/背景 job、`&`、Ctrl-Z、`jobs`/`fg`/`bg`、process group 與 session、以及為什麼關閉終端機後背景程式會死（SIGHUP），怎麼用 nohup/disown/setsid 讓程式存活。這把 Ch 14-17 的 process 知識應用到日常的 shell 操作。

> **環境**：bash 5.x。job control 是 shell 功能，承接 Ch 14（狀態）、Ch 17（signal）。

## 為什麼要懂 job control？

你會用 `command &` 把程式丟背景、`Ctrl-Z` 暫停、`fg` 拉回前景。但背後的機制——process group、session、controlling terminal——決定了很多行為：為什麼關閉 SSH 後跑的程式死了？為什麼 Ctrl-C 能一次中斷整個管線？怎麼讓長時間任務在登出後繼續跑？

job control 是 shell 管理多個 process 的機制，把 Ch 14-17 的 process 概念（狀態、signal、process group）應用到日常操作。理解它，你能在一個終端機同時管理多個任務、讓程式在登出後存活、正確地控制前景背景。這是 SysOps 跑長任務的必備知識。

## 先建立直覺：前景與背景

```
job control：在一個終端機管理多個 job

  前景 job（foreground）：
    佔據終端機，你的輸入直接給它
    Ctrl-C/Ctrl-Z 影響它
    一次只有一個前景 job
        │
  背景 job（background）：
    在背後跑，不佔終端機
    你能繼續用 shell 做別的
    command & 把命令丟背景
        │
  暫停的 job（stopped）：
    Ctrl-Z 暫停前景 job（進 T 狀態，Ch 14）
    fg 拉回前景、bg 在背景繼續
        │
  → shell 像個工作管理員，協調多個 job 共用一個終端機
```

## 基本 job control 操作

```bash
# 把命令丟背景（& ）
sleep 100 &              # 背景執行，立刻回到 prompt
# [1] 12345              ← [job 號] PID

# 看背景 job
jobs                     # 列出當前 shell 的 job
# [1]+  Running    sleep 100 &
jobs -l                  # 加 PID

# 前景程式暫停（Ctrl-Z）
sleep 100                # 前景跑
# 按 Ctrl-Z
# [1]+  Stopped    sleep 100      ← 暫停（T 狀態，Ch 14）

# 恢復
fg                       # 把最近的 job 拉回前景
fg %1                    # 把 job 1 拉回前景
bg                       # 在背景繼續（暫停的 job 恢復跑，但在背景）
bg %1

# 用 job 號操作
kill %1                  # 殺 job 1（用 %N 指 job）
fg %1                    # 前景化 job 1
```

```
job control 的完整循環：
  command          前景跑
  Ctrl-Z           暫停（→ T 狀態）
  bg               背景繼續（→ 背景 Running）
  fg               拉回前景
  command &        直接背景啟動
  jobs             看所有 job
  kill %N          殺 job N
        │
  → 一個終端機跑多個任務的標準工作流
```

## 底層：process group 與 session

job control 的底層是 **process group** 和 **session**：

```
process group 與 session（job control 的底層）：

  session（會話）：
    一個終端機對應一個 session
    session 有一個「controlling terminal」（控制終端機）
    session leader 通常是你的 shell
        │
  process group（行程組）：
    一個 job = 一個 process group
    管線 cmd1 | cmd2 的兩個 process 在「同一個 process group」
        │
  為什麼這樣設計：
    Ctrl-C 送 SIGINT 給「前景 process group」的所有 process
    → 一次中斷整個管線（cmd1 | cmd2 一起被中斷）
    → 不是只中斷一個（不然管線殺不乾淨）
        │
  → process group 讓 signal 能「群發」給一個 job 的所有 process
    這是 Ctrl-C 能中斷整個管線的原因
```

```bash
# 看 process group 和 session
ps -eo pid,ppid,pgid,sid,comm | head
#                  ↑pgid ↑sid（session id）
# pgid = process group ID（一個 job 共享）
# sid = session ID（一個終端機共享）

# 管線的 process 在同一個 process group
sleep 100 | cat &
ps -eo pid,pgid,comm | grep -E "sleep|cat"
# 兩個（sleep 和 cat）有相同的 pgid（同一個 job）
```

> **process group 讓 Ctrl-C 能中斷整個管線**。當你 `cmd1 | cmd2 | cmd3` 然後 Ctrl-C，SIGINT 送給「前景 process group」的**所有** process（三個一起中斷），不是只中斷一個。這是因為 shell 把管線的所有 process 放進同一個 process group。`session` 對應終端機——一個 SSH 連線/終端機是一個 session，shell 是 session leader。理解 process group（一個 job）和 session（一個終端機），你會懂 Ctrl-C 為什麼中斷整個管線、以及下面的 SIGHUP 為什麼影響整個 session 的 job。

## 為什麼關閉終端機後背景程式會死

關閉終端機（或 SSH 斷線）後，背景程式常常也死了——這是 **SIGHUP**（Ch 17）：

```
終端機關閉 → SIGHUP → 背景程式死掉：

  你 SSH 進伺服器，跑 long_task &（背景）
  然後關閉 SSH（或網路斷線）
        │
  controlling terminal 消失
        │
  kernel 送 SIGHUP（hangup，Ch 17）給 session leader（你的 shell）
    → shell 預設把 SIGHUP 轉發給它管的 job（機制見下方進階）
        │
  SIGHUP 的預設行為是「終止」
        │
  → 你的 background job 收到 SIGHUP → 死掉
    （即使它在背景！背景不等於不受 SIGHUP 影響）
        │
  → 這是「SSH 斷線後背景任務死掉」的原因
    解法：讓程式「脫離 session」或「忽略 SIGHUP」
```

> **「SSH 斷線後背景程式死掉」的原因是 SIGHUP**。終端機關閉時，kernel 送 SIGHUP（hangup，源自數據機時代「掛斷電話」）給 session 的所有 process。SIGHUP 預設終止 process——所以即使你 `&` 丟背景，SSH 斷線它也死（背景 ≠ 免疫 SIGHUP）。這是長任務的大坑：你以為 `long_task &` 能在登出後繼續，結果一登出就死。解法（下面）是讓程式「脫離 session」（setsid/disown）或「忽略 SIGHUP」（nohup）。

## nohup / disown / setsid：讓程式存活

```bash
# 方法一：nohup（忽略 SIGHUP + 重導向輸出）
nohup long_task &
# nohup: ignoring input and appending output to 'nohup.out'
#   ↑ nohup 讓程式忽略 SIGHUP，輸出存到 nohup.out
#   SSH 斷線也不死

# 方法二：disown（從 shell 的 job 列表移除）
long_task &
disown                   # 移除最近的 job（shell 不再管它，不送 SIGHUP）
disown -h %1             # 標記 job 1 不收 SIGHUP（但留在 job 列表）

# 方法三：setsid（在新 session 跑，徹底脫離）
setsid long_task         # 在新 session 啟動（沒有 controlling terminal）
#   → 徹底脫離當前 session，SIGHUP 影響不到

# 方法四（推薦）：terminal multiplexer（tmux/screen）
tmux                     # 開一個 tmux session
# 在裡面跑 long_task
# Ctrl-B D 脫離（detach）→ tmux session 在背景持續
# 重新連線後 tmux attach 回到原本的 session
```

| 方法 | 機制 | 適合 |
|---|---|---|
| `nohup cmd &` | 忽略 SIGHUP + 重導輸出 | 快速跑一個長任務 |
| `disown` | 從 shell job 列表移除 | 已經 & 跑了，事後脫離 |
| `setsid cmd` | 在新 session 跑 | 徹底脫離 |
| `tmux`/`screen` | 持久的 session | 互動式長任務、能重新連回 |

> **跑長任務最好用 tmux/screen**（terminal multiplexer）。nohup/disown/setsid 能讓程式在登出後存活，但你「看不到」它的輸出、不能再互動。tmux/screen 開一個「持久的 session」——你跑任務、`Ctrl-B D` 脫離（detach），session 在背景持續跑，之後 `tmux attach` 回到原本的畫面（看輸出、繼續互動）。SSH 斷線也不影響（tmux session 不依賴你的連線）。SysOps 跑部署、長編譯、訓練任務都用 tmux/screen。`nohup` 適合「跑了就不管，只要結果」；tmux 適合「要監看、可能要互動」。

## 故意弄壞：& 跑長任務後登出（死掉）

```bash
# 在 SSH session 裡（VM 模擬）
# 錯誤：直接 & 跑長任務
sleep 300 &
# 然後登出 SSH（exit）...
# 重新登入後：
ps aux | grep "[s]leep 300"
# （沒有了！sleep 被 SIGHUP 殺了）

# 正確：用 nohup 或 tmux
nohup sleep 300 &
# 登出再登入：
ps aux | grep "[s]leep 300"
# sleep 300 還在（nohup 讓它忽略 SIGHUP）
```

這驗證了 SIGHUP 的影響：直接 `&` 的背景任務登出後死（SIGHUP），nohup 的存活（忽略 SIGHUP）。這是長任務的經典坑——「我明明丟背景了，為什麼登出就死」。

## 踩雷集錦

1. **以為 & 背景就能登出後存活**：背景 ≠ 免疫 SIGHUP。SSH 斷線送 SIGHUP，背景任務也死。要 nohup/disown/setsid/tmux

2. **nohup 後不知道輸出去哪**：nohup 把輸出重導到 `nohup.out`（當前目錄）。要自訂用 `nohup cmd > mylog 2>&1 &`

3. **disown 後還想用 fg 拉回**：disown 把 job 從 shell 移除，shell 不再管它，不能再 fg/bg。要互動用 tmux

4. **Ctrl-C 沒中斷整個管線**：Ctrl-C 送 SIGINT 給前景 process group。如果某個 process 忽略 SIGINT（Ch 17），它不死。或程式在 D 狀態（Ch 14）

5. **混淆 job 號（%N）和 PID**：`kill %1` 殺 job 1，`kill 12345` 殺 PID 12345。job 號是 shell 的概念，PID 是系統的。`jobs -l` 看對應

## 進階：nohup 的底層與 controlling terminal

nohup/disown 的底層涉及 controlling terminal 和 SIGHUP 的細節：

```
controlling terminal 與 SIGHUP 的底層：
  每個 session 有一個 controlling terminal（你的終端機）
  process 對它的 stdin/stdout/stderr 連到這個終端機
        │
  終端機關閉時：
    1. kernel 送 SIGHUP 給 session leader（你的 shell）
    2. shell 收到 SIGHUP → 預設轉發給它的所有 job
    3. job 收到 SIGHUP → 死
        │
  nohup 怎麼防：
    - 讓程式忽略 SIGHUP（signal handler 設成 SIG_IGN）
    - 重導 stdin/stdout/stderr（脫離終端機，避免讀寫已關閉的終端機）
        │
  disown 怎麼防：
    - 從 shell 的 job 表移除 → shell 不轉發 SIGHUP 給它
        │
  setsid 怎麼防：
    - 在「新 session」跑 → 沒有 controlling terminal → 終端機關閉影響不到
        │
  → 三種方法從不同層次「切斷」程式和終端機的關係
```

> nohup/disown/setsid 從不同層次切斷程式和終端機的連結。**nohup**：讓程式忽略 SIGHUP + 重導 I/O（不依賴終端機）。**disown**：從 shell 的 job 表移除（shell 不轉發 SIGHUP）。**setsid**：在新 session 跑（沒有 controlling terminal，終端機關閉影響不到）。理解底層（終端機關閉 → SIGHUP → 轉發給 job），你會懂為什麼這三種方法都有效（各自切斷某個環節），以及為什麼 tmux 最徹底（tmux 自己是 session leader，你的 SSH 斷線只是 detach，tmux session 不受影響）。daemon 程式（如 systemd 服務）也用類似機制脫離終端機（double fork + setsid）。

## 動手練習

1. 練 job control：`sleep 100 &`（背景）、`jobs`、Ctrl-Z 暫停前景程式、`bg`/`fg`、`kill %1`。理解前景/背景/暫停的轉換

2. 看底層：`ps -eo pid,pgid,sid,comm`，看 process group 和 session。跑 `sleep 100 | cat &` 看管線兩個 process 同 pgid

3. 試 SIGHUP（VM）：SSH 進去 `sleep 300 &`，登出再登入看它死了。`nohup sleep 300 &` 看它活著。理解 SIGHUP 的影響

4. 用 tmux：`tmux` 開 session，跑個東西，`Ctrl-B D` detach，`tmux ls` 看 session 還在，`tmux attach` 回去。對比 nohup（看不到輸出）

## 本章重點整理

- job control：前景（佔終端機）/背景（&）/暫停（Ctrl-Z → T 狀態）；jobs/fg/bg/kill %N 管理
- 底層：session（一個終端機）+ process group（一個 job）；Ctrl-C 送 SIGINT 給前景 process group（中斷整個管線）
- SIGHUP：終端機關閉時送給 session 的 process，預設終止——所以 SSH 斷線背景任務也死（背景 ≠ 免疫）
- 讓程式存活：nohup（忽略 SIGHUP）、disown（移除 job）、setsid（新 session）、tmux/screen（持久 session，推薦）
- tmux/screen 最好用於長任務——持久 session，能 detach/attach，看輸出、可互動、斷線不影響

## 自我檢核

- [ ] 能用 job control 操作（&/Ctrl-Z/jobs/fg/bg/kill %N）管理多個 job
- [ ] 知道 process group 怎麼讓 Ctrl-C 中斷整個管線
- [ ] 能解釋為什麼 SSH 斷線後背景任務死掉（SIGHUP）
- [ ] 知道 nohup/disown/setsid/tmux 各怎麼讓程式存活，以及它們的差異
- [ ] 知道為什麼長任務最好用 tmux/screen（持久、可 detach/attach）

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 34 (Process Groups, Sessions, and Job Control)** — Michael Kerrisk
  - **讀哪幾章**：Ch 34（process group、session、controlling terminal、SIGHUP）
  - **這本書的定位**：job control 底層的權威來源
  - **前提**：本章 + Ch 17

### 部落格 / 文章

- **[The TTY demystified](https://www.linusakesson.net/programming/tty/)** — Linus Åkesson
  - **這篇說什麼**：終端機、session、process group、SIGHUP 的完整底層
  - **讀哪裡**：jobs and sessions、SIGHUP 那部分
  - **為什麼值得讀**：把 job control 的底層（TTY/session/process group）講得最透徹

### 工具

- **[tmux documentation](https://github.com/tmux/tmux/wiki)** 或 `man tmux`
  - **讀哪裡**：getting started、detach/attach
  - **學什麼**：tmux 的完整用法（不只 detach，還有分割視窗、多 session）
  - **前提**：本章

→ [練習 B：mini job monitor](./practice-b-job-monitor.md)
