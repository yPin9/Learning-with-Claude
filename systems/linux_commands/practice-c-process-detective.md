# 練習 C — 行程偵探

> 目標：整合 Part 4（Ch 15–19）的工具，用 `ps`、`lsof`、`/proc`、`kill` 調查並控制行程。

## 任務規格

以下幾個調查任務，每個答案要附上使用的指令。

## 建立測試環境

```bash
# 先跑這些指令建立測試行程
bash -c 'while true; do sleep 1; done' &   # 模擬 daemon A
DAEMON_A=$!

bash -c 'while true; do echo "$(date)" >> /tmp/daemon-b.log; sleep 2; done' &  # 模擬 daemon B
DAEMON_B=$!

bash -c 'sleep 1000' &   # 長時間 sleep
LONG_SLEEP=$!

echo "Daemon A PID: $DAEMON_A"
echo "Daemon B PID: $DAEMON_B"
echo "Long sleep PID: $LONG_SLEEP"
```

## 調查任務

### 任務一：找出系統中的「長時間 sleep」行程

找出所有正在執行 `sleep` 的行程，顯示 PID、父 PID（PPID）、User 和完整指令。

| 問題 | 你的指令 | 答案 |
|------|---------|------|
| 有哪些 sleep 行程在跑？| | |
| 它們的父行程是誰？ | | |
| 哪個 sleep 的時間最長？ | | |

### 任務二：調查 daemon B 的 fd

針對 daemon B（`$DAEMON_B`），調查它開啟了哪些檔案。

| 問題 | 你的指令 | 答案 |
|------|---------|------|
| daemon B 開啟了幾個 fd？ | | |
| 它在寫哪個檔案？（找非 0/1/2 的 fd）| | |
| 用 /proc 路徑讀出 fd 的目標 | | |

### 任務三：暫停、繼續、終止

| 問題 | 你的指令 | 結果 |
|------|---------|------|
| 暫停 daemon A（不要終止）| | |
| 確認 daemon A 的狀態是 T（Stopped）| | |
| 讓 daemon A 繼續執行 | | |
| 優雅終止 daemon B（SIGTERM）| | |
| 確認 daemon B 已不存在 | | |
| 強制終止 long sleep | | |

### 任務四：探索自己的 shell 行程

| 問題 | 你的指令 | 答案 |
|------|---------|------|
| 當前 shell 的 PID 和 PPID 是多少？ | | |
| 當前 shell 有幾個 fd 開啟？ | | |
| 當前 shell 的 PATH 環境變數（從 /proc 讀）| | |
| 從 shell 建立一個子 bash，比較兩者的 fd | | |

## 完整參考解答

**全部做完再看！**

<details>
<summary>點開參考解答</summary>

```bash
# 任務一
# 找所有 sleep 行程
ps aux | grep '[s]leep'
ps -eo pid,ppid,user,cmd | grep '[s]leep'

# 父行程是誰（用 pstree）
pstree -p $DAEMON_A    # 看父子關係

# 哪個 sleep 時間最長（找 sleeping 最久的）
ps -eo pid,etime,cmd | grep '[s]leep' | sort -k2 -rn

# 任務二
# daemon B 的 fd 數量
ls /proc/$DAEMON_B/fd | wc -l

# 找非 0/1/2 的 fd
ls -la /proc/$DAEMON_B/fd | grep -v ' -> /dev/pts'

# 用 readlink 讀 fd 目標
for fd in /proc/$DAEMON_B/fd/*; do
    echo "fd $(basename $fd): $(readlink $fd)"
done

# 用 lsof
lsof -p $DAEMON_B

# 任務三
# 暫停 daemon A
kill -STOP $DAEMON_A

# 確認狀態是 T
ps -p $DAEMON_A -o pid,stat,cmd

# 繼續執行
kill -CONT $DAEMON_A

# 確認回到 S
ps -p $DAEMON_A -o pid,stat,cmd

# 優雅終止 daemon B
kill -TERM $DAEMON_B
sleep 1
kill -0 $DAEMON_B 2>/dev/null && echo "still alive" || echo "daemon B dead"

# 強制終止 long sleep
kill -9 $LONG_SLEEP

# 任務四
# PID 和 PPID
echo "PID=$$  PPID=$PPID"
ps -p $$ -o pid,ppid,cmd

# fd 數量
ls /proc/$$/fd | wc -l

# PATH 從 /proc 讀
strings /proc/$$/environ | grep '^PATH'

# 子 bash 的 fd 比較
bash -c "ls /proc/\$\$/fd | wc -l; echo 'parent fd count:'; ls /proc/$$/fd | wc -l"
```

</details>

## 進階挑戰

```bash
# 1. 寫一個監控腳本：每 5 秒輸出 CPU 用量前 3 的行程
while true; do
    echo "=== $(date) ==="
    ps aux --sort=-%cpu | head -4
    sleep 5
done

# 2. 找出所有「孤兒行程」（父行程已死，被 init/systemd 收養）
ps -eo pid,ppid,cmd | awk '$2==1 && $1!=1 {print}'

# 3. 找出哪個行程開啟最多 fd
for pid in $(ls /proc | grep '^[0-9]'); do
    count=$(ls /proc/$pid/fd 2>/dev/null | wc -l)
    echo "$count $pid"
done | sort -rn | head -10

# 4. 用 /proc 讀某個行程的完整指令（包含參數）
cat /proc/1/cmdline | tr '\0' ' '
echo    # 補換行
```

## 清理

```bash
# 練習完畢，清理所有測試行程
kill $DAEMON_A $DAEMON_B $LONG_SLEEP 2>/dev/null
rm -f /tmp/daemon-b.log
```

## 自我檢核

- [ ] 能用 `ps -eo pid,ppid,stat,cmd` 讀取行程的完整資訊
- [ ] 能從 `/proc/<PID>/fd` 讀出行程開啟的資源
- [ ] 知道 SIGSTOP 和 SIGCONT 的用法
- [ ] 能用 `kill -0 <PID>` 測試行程是否還活著

→ [Ch 20 Shell Script 基礎](./20-script-basics.md)
