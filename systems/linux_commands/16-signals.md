# Ch 16 — 訊號（Signals）

> 目標：理解訊號機制，記住常用訊號編號，能用 `kill`/`pkill`/`killall` 正確終止或控制行程。

## 訊號是什麼

訊號是 Unix 的非同步通知機制：核心或其他行程發一個訊號給某個行程，目標行程會中斷當前執行、跳去處理這個訊號。

行程對訊號的反應有三種：

```
1. 預設行為（terminate / core dump / stop / ignore）
2. 自訂處理函式（signal handler）
3. 忽略（某些訊號不能被忽略）
```

SIGKILL 和 SIGSTOP 這兩個**不能被捕捉也不能被忽略**——這是設計決策，確保系統永遠有辦法控制行程。

## 常用訊號速查表

| 編號 | 名稱 | 預設行為 | 說明 |
|------|------|---------|------|
| 1 | SIGHUP | Terminate | 終端關閉；daemon 用它重讀設定 |
| 2 | SIGINT | Terminate | Ctrl+C |
| 3 | SIGQUIT | Core dump | Ctrl+\ |
| 9 | SIGKILL | Terminate | **不可捕捉**，強制終止 |
| 10 | SIGUSR1 | Terminate | 使用者自訂 |
| 12 | SIGUSR2 | Terminate | 使用者自訂 |
| 15 | SIGTERM | Terminate | 預設的 kill 訊號，可被捕捉 |
| 17 | SIGCHLD | Ignore | 子行程狀態改變（通知父行程）|
| 18 | SIGCONT | Continue | 讓 STOP 的行程繼續 |
| 19 | SIGSTOP | Stop | **不可捕捉**，暫停行程 |
| 20 | SIGTSTP | Stop | Ctrl+Z，可被捕捉 |

```bash
kill -l    # 列出所有訊號名稱和編號
```

## SIGTERM vs SIGKILL

這是最常被誤用的一對：

```
SIGTERM（15）  ── 「請你結束」，行程可以做清理（關資料庫連線、存檔）
SIGKILL（9）   ── 「立刻消滅」，核心強制終止，行程沒機會清理
```

**正確的做法**：先 SIGTERM，等幾秒，若還活著再 SIGKILL。

```bash
kill 1234           # 送 SIGTERM（預設）
sleep 5
kill -9 1234        # 還活著才用 SIGKILL
```

程式寫得好的 daemon 收到 SIGTERM 會優雅地收尾（graceful shutdown），直接 SIGKILL 可能導致資料損壞或暫時檔案沒清。

## kill

```bash
kill 1234           # 送 SIGTERM 給 PID 1234
kill -9 1234        # 送 SIGKILL
kill -SIGTERM 1234  # 等同 kill 1234
kill -HUP 1234      # 送 SIGHUP（讓 daemon 重讀設定）
kill -l             # 列出所有訊號
kill -0 1234        # 不送訊號，只測試 PID 是否存在（exit code 0 = 存在）
```

一次送多個行程：

```bash
kill 1234 5678 9012
```

## pkill 和 killall

`kill` 要給 PID，`pkill`/`killall` 可以用名字找：

```bash
# pkill：用 regex 匹配 process name
pkill nginx         # 終止所有名字含 nginx 的行程
pkill -9 python     # 強制終止
pkill -HUP sshd     # 讓 sshd 重讀設定
pkill -u bob        # 終止 bob 的所有行程
pkill -t pts/1      # 終止在 pts/1 這個終端的行程

# killall：精確匹配 process name
killall nginx       # 精確匹配（比 pkill 更安全）
killall -w nginx    # -w = wait，等所有行程都死了才返回
```

`pkill` 用的是 regex 子字串匹配，`killall` 是精確全名匹配。殺 `python3` 用 `pkill` 小心會順便殺到 `python3.11-helper` 之類的東西。

## SIGHUP：daemon 重讀設定

很多服務用 SIGHUP 代表「重讀設定檔，不要重開」：

```bash
kill -HUP $(cat /var/run/nginx.pid)  # nginx 重讀設定
pkill -HUP nginx
```

這比 `systemctl reload nginx` 更底層，效果一樣。

## SIGUSR1 / SIGUSR2

這兩個留給應用程式自訂：

```bash
kill -USR1 1234    # 告訴 logrotate 轉檔
kill -USR2 1234    # 應用自訂行為
```

## 捕捉訊號（trap）

Shell script 可以用 `trap` 捕捉訊號，做清理工作：

```bash
#!/bin/bash
# 清理暫時檔案
trap 'rm -f /tmp/myapp-*.tmp; exit' INT TERM EXIT

# 做一些會產生暫時檔案的工作
tempfile=$(mktemp /tmp/myapp-XXXXXX.tmp)
echo "working..." > "$tempfile"
sleep 100   # 按 Ctrl+C 或 kill 都會觸發 trap
```

`EXIT` 是 bash 特有的「偽訊號」，在 shell 結束時觸發，不管是正常退出還是被訊號殺死。

## 動手練習

```bash
# 1. 開一個後臺行程，然後優雅地終止它
sleep 1000 &
echo "PID: $!"         # $! = 上一個背景行程的 PID
kill $!                # 送 SIGTERM
sleep 1
kill -0 $! 2>/dev/null && echo "still alive" || echo "dead"

# 2. 用 kill -0 測試 PID 是否存在
kill -0 1              # PID 1 一定存在，exit code = 0
kill -0 99999 2>/dev/null; echo $?   # 不存在 = exit code 1

# 3. 模擬 Ctrl+C 給自己
# 開一個 script 捕捉 SIGINT
cat > /tmp/catch.sh << 'EOF'
#!/bin/bash
trap 'echo "caught SIGINT, cleaning up..."; exit 0' INT
echo "running, PID=$$"
sleep 10
EOF
chmod +x /tmp/catch.sh
/tmp/catch.sh &
sleep 1
kill -INT $!    # 等同 Ctrl+C

# 4. 看 nginx 收 SIGHUP 前後的行為（需要 nginx 安裝）
# sudo kill -HUP $(cat /var/run/nginx.pid)

# 5. 用 pkill 的 -echo 旗標（部分版本支援）預覽要殺哪些
pkill -echo -l sleep  # 預覽（不真的殺）
```

## 自我檢核

- [ ] 記住 SIGTERM(15) 可捕捉、SIGKILL(9) 不可捕捉
- [ ] 知道先送 SIGTERM、等一下、再 SIGKILL 是正確順序
- [ ] 能用 `pkill -HUP` 讓 daemon 重讀設定
- [ ] 知道 `kill -0 <PID>` 可以測試行程是否存在

→ [Ch 17 工作控制（Job Control）](./17-job-control.md)
