# 練習 B — mini job monitor

> **目標**：整合 Ch 14–18 的 process 知識，寫一個 `procmon.sh` 腳本——監控指定的 process（按名字或 PID），報告它的狀態（state/CPU/記憶體/開啟的 fd 數）、偵測異常（zombie、D 狀態、過多 fd），並能在 process 消失時通知。完成後你能用 process 概念寫實用的監控工具，這是 SysOps 的日常。

## 背景與動機

你學了 process 狀態機（Ch 14）、fork/exec/wait（Ch 15）、ps/proc 觀測（Ch 16）、signal（Ch 17）、job control（Ch 18）。這些是 SysOps 的核心。這個練習把它們綁成一個實用工具——一個「process 監視器」，持續觀測一個 process 的健康狀態。

這正是真實監控系統（如 monit、systemd 的 watchdog）做的事的雛形：盯著一個 process，看它狀態、資源、是否還活著，異常時告警。寫這個工具會逼你用 /proc 提取 process 資訊、判斷狀態異常、處理 process 消失。完成後你對「監控一個 process」有了動手的理解。

## 任務規格

寫 `procmon.sh <pid|name>`，持續監控目標 process：

| 功能 | 來源 | 章節 |
|---|---|---|
| 解析目標（PID 或 name → PID）| pgrep | Ch 16 |
| 狀態（R/S/D/T/Z）| /proc/<pid>/stat 或 ps | Ch 14 |
| CPU / 記憶體 % | ps | Ch 16 |
| 開啟的 fd 數 | /proc/<pid>/fd | Ch 16/19 |
| 異常偵測 | 邏輯 | Ch 14 |
| process 消失通知 | /proc/<pid> 不存在 | Ch 14 |

**異常偵測要求**：
- zombie（Z）：報告 "ZOMBIE detected"（Ch 14）
- D 狀態：報告 "Stuck in uninterruptible sleep (I/O?)"（Ch 14）
- fd 數過多（如 > 100）：報告 "High fd count"（Ch 16/19）
- process 消失：報告 "Process gone" 並結束

**驗收標準**：
- 能用 PID 或 name 指定目標
- 每隔 N 秒更新狀態（預設 2 秒）
- 正確偵測各種異常
- process 消失時優雅結束
- 用 trap 處理 Ctrl-C（清理後退出，Ch 17/35 預習）

## 期望輸出範例

```
$ ./procmon.sh nginx
Monitoring 'nginx' (PID 1234), interval 2s. Ctrl-C to stop.
[10:00:00] PID 1234  STATE=S  CPU=0.5%  MEM=2.1%  FDs=24  OK
[10:00:02] PID 1234  STATE=S  CPU=0.3%  MEM=2.1%  FDs=24  OK
[10:00:04] PID 1234  STATE=R  CPU=15.2% MEM=2.2%  FDs=26  OK
...
```

```
異常情況：
[10:00:10] PID 5678  STATE=D  CPU=0.0%  MEM=1.0%  FDs=12  WARNING: Stuck in D (I/O?)
[10:00:20] PID 5678  STATE=Z  ...                        WARNING: ZOMBIE detected
[10:00:30] Process 5678 is gone. Exiting.
```

## 如果你卡住了

1. 解析目標：先判斷參數是數字（PID）還是字串（name，用 pgrep 找 PID）
2. 狀態：`ps -p <pid> -o stat=`（=去掉標題）給狀態碼，或 `cat /proc/<pid>/stat` 的第 3 欄
3. CPU/記憶體：`ps -p <pid> -o %cpu=,%mem=`
4. fd 數：`ls /proc/<pid>/fd | wc -l`（Ch 16）
5. process 是否存在：`[ -d /proc/<pid> ]` 或 `kill -0 <pid>`（送 signal 0 = 只檢查存在）
6. 迴圈：`while ... do ... sleep N; done`，用 trap 處理 Ctrl-C

## 實作步驟建議

### Step 1：參數解析（PID 或 name → PID）+ 驗證存在
### Step 2：提取狀態（state/CPU/記憶體/fd 數）
### Step 3：異常偵測（Z/D/高 fd）
### Step 4：監控迴圈 + process 消失處理
### Step 5：trap 處理 Ctrl-C + 整合

## 完整參考解答

**寫完再看！**

<details>
<summary>procmon.sh</summary>

```bash
#!/bin/bash
# procmon.sh — 監控一個 process 的健康狀態

INTERVAL=2
FD_THRESHOLD=100

# Step 5: trap 處理 Ctrl-C（Ch 17/35）
trap 'echo ""; echo "Monitoring stopped."; exit 0' INT TERM

# Step 1: 參數解析
if [ $# -lt 1 ]; then
    echo "Usage: $0 <pid|name> [interval]" >&2
    exit 1
fi
target="$1"
[ -n "$2" ] && INTERVAL="$2"

# 判斷是 PID（純數字）還是 name
if [[ "$target" =~ ^[0-9]+$ ]]; then
    PID="$target"
else
    # name → PID（取第一個符合的）
    PID=$(pgrep -x "$target" | head -1)
    if [ -z "$PID" ]; then
        echo "Error: no process named '$target' found" >&2
        exit 1
    fi
fi

# 驗證 process 存在（kill -0 = 只檢查存在不送 signal，Ch 17）
if ! kill -0 "$PID" 2>/dev/null; then
    echo "Error: PID $PID does not exist" >&2
    exit 1
fi

echo "Monitoring '$target' (PID $PID), interval ${INTERVAL}s. Ctrl-C to stop."

# Step 4: 監控迴圈
while true; do
    # Step 4: 檢查 process 是否還在
    if [ ! -d "/proc/$PID" ]; then
        echo "Process $PID is gone. Exiting."
        exit 0
    fi

    # Step 2: 提取狀態
    ts=$(date +%H:%M:%S)
    # 狀態碼（ps 的 stat 欄，= 去標題）
    state=$(ps -p "$PID" -o stat= 2>/dev/null | tr -d ' ')
    [ -z "$state" ] && { echo "[$ts] Process $PID is gone. Exiting."; exit 0; }
    # 只取狀態主碼（第一個字元，去掉 < + s 等修飾，Ch 16）
    state_main=${state:0:1}
    cpu=$(ps -p "$PID" -o %cpu= 2>/dev/null | tr -d ' ')
    mem=$(ps -p "$PID" -o %mem= 2>/dev/null | tr -d ' ')
    # fd 數（Ch 16/19）
    fds=$(ls "/proc/$PID/fd" 2>/dev/null | wc -l)

    # Step 3: 異常偵測
    status="OK"
    case "$state_main" in
        Z) status="WARNING: ZOMBIE detected" ;;
        D) status="WARNING: Stuck in D (I/O?)" ;;
        T) status="NOTE: Stopped" ;;
    esac
    if [ "$fds" -gt "$FD_THRESHOLD" ]; then
        status="WARNING: High fd count ($fds)"
    fi

    # 輸出
    printf "[%s] PID %s  STATE=%-2s CPU=%5s%% MEM=%5s%% FDs=%-4s %s\n" \
        "$ts" "$PID" "$state_main" "$cpu" "$mem" "$fds" "$status"

    sleep "$INTERVAL"
done
```

```bash
chmod +x procmon.sh
./procmon.sh bash          # 監控一個 bash（用 name）
./procmon.sh $$            # 監控當前 shell（用 PID）
./procmon.sh sleep 1       # 監控 sleep，間隔 1 秒
```

**解答說明**：

- **PID vs name 判斷**：`[[ "$target" =~ ^[0-9]+$ ]]` 用正則判斷是否純數字（PID）。是 name 就 `pgrep -x`（精確匹配名字）轉成 PID（Ch 16）
- **kill -0 檢查存在**：`kill -0 <pid>` 送「signal 0」——不真的送 signal，只檢查 process 是否存在（且你有權限）。這是檢查 process 存在的慣用法（Ch 17）
- **狀態主碼**：`ps -o stat=` 給的可能是 `S<`、`Ssl` 等（含修飾符，Ch 16）。`${state:0:1}` 取第一個字元（主狀態 R/S/D/T/Z）
- **process 消失偵測**：`[ -d /proc/<pid> ]`（目錄不存在 = process 沒了，Ch 14/16）。每輪檢查，消失就優雅結束
- **異常偵測**：Z（zombie）、D（uninterruptible，I/O 問題）、T（stopped）、高 fd 數——對應 Ch 14 的狀態機和 Ch 16 的 fd 觀測
- **trap Ctrl-C**：`trap '...' INT TERM` 讓 Ctrl-C 優雅退出（Ch 17 的 signal handler 的 shell 版，Part 8 詳述）

</details>

## 測試用案例

| 操作 | 預期 | 驗證 |
|---|---|---|
| `./procmon.sh bash` | 監控 bash，顯示狀態 | name → PID |
| `./procmon.sh $$` | 監控當前 shell | PID 直接用 |
| 監控一個會結束的 process | "Process gone. Exiting" | 消失偵測 |
| 監控 `sleep 100` 然後 kill -STOP 它 | STATE=T, NOTE: Stopped | T 狀態偵測 |
| Ctrl-C 中斷 monitor | "Monitoring stopped" | trap 處理 |
| 不存在的 name | Error 訊息 | 錯誤處理 |

## 延伸挑戰（加分）

- **挑戰一**：加「重啟」功能——監控一個服務，process 消失時自動重新啟動它（像 systemd 的 Restart=always）。記錄重啟次數

- **挑戰二**：加 CPU/記憶體歷史——記錄過去 N 次的 CPU/記憶體，計算平均和峰值，偵測「持續高 CPU」（不只瞬間）

- **挑戰三**：監控多個 process——接受多個目標，同時監控，用表格顯示。處理某些消失某些還在

- **挑戰四**：加 strace 整合——`--syscalls` 模式下，對目標 `strace -p -c` 一小段時間，報告它最常做的 syscall（呼應全課的 strace 手法，Ch 0）

## 自我檢核

- [ ] 能用 /proc 和 ps 提取一個 process 的完整狀態
- [ ] 知道怎麼檢查 process 是否存在（kill -0 / /proc/<pid>）
- [ ] 能偵測 process 異常（zombie/D 狀態/高 fd）並解釋它們的意義
- [ ] 能用 trap 處理 Ctrl-C 優雅退出
- [ ] 能說出這個工具和真實監控系統（systemd watchdog/monit）的關係

→ [Ch 19 file descriptor 與重導向](./19-fd-redirection.md)
