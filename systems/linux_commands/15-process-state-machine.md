# Ch 15 — 行程狀態機

> 目標：理解行程的生命週期，掌握 fork/exec/wait 的語意，能用 `ps`/`pstree`/`top` 解讀系統行程狀態。

## 行程是什麼

行程（process）是程式執行的實例。每個行程有：

- 唯一的 PID（Process ID）
- PPID（Parent PID）
- 獨立的記憶體空間
- 開啟的檔案描述符（fd）
- 執行狀態

Linux 的所有行程形成一棵樹：PID 1（init/systemd）是根，所有其他行程都是它的後代。

## fork / exec / wait

這三個 system call 是 Unix 行程模型的核心：

```
fork()  ── 複製自己，建立子行程
exec()  ── 用新程式替換當前行程映像
wait()  ── 等待子行程結束，取得它的 exit code
```

### fork：複製行程

```
父行程           子行程
PID=100    →   PID=101
相同記憶體內容     相同記憶體內容（Copy-on-Write）
fork() 回傳 101  fork() 回傳 0
```

`fork()` 之後，父子行程各自繼續執行。父行程拿到子行程的 PID，子行程拿到 0——這就是程式知道自己是不是子行程的方式。

### exec：替換程式

`exec()` 不建立新行程，它用新程式的程式碼覆蓋當前行程——PID 不變，但程式變了。

所以「執行一個新程式」的完整流程是：

```
shell → fork() → 子 shell → exec("ls") → ls 執行 → exit() → 父 shell 的 wait() 返回
```

### wait：避免殭屍

子行程結束後，它的 PID 和 exit code 還存在核心表格裡，等待父行程用 `wait()` 取走。父行程取走之前，這個行程叫**殭屍行程（zombie）**。

## 行程狀態

```
CREATED
   ↓
RUNNING (R) ──── 正在跑，或在 run queue 等 CPU
   ↓↑
SLEEPING
  S — 可中斷睡眠（等 I/O、等訊號）
  D — 不可中斷睡眠（等磁碟 I/O，kill 殺不了）
   ↓
STOPPED (T) ──── 被 SIGSTOP 暫停
   ↓
ZOMBIE (Z) ───── 已結束，等父行程 wait()
```

`D` 狀態是維運常見問題：行程卡在磁碟 I/O 時，`kill -9` 也無效，只能等 I/O 完成或重開機。

## ps：行程快照

```bash
ps aux              # 所有行程，BSD 格式（最常用）
ps -ef              # 所有行程，Unix 格式（有 PPID）
ps -ef --forest     # 樹狀顯示父子關係
ps aux | grep nginx # 找特定行程

# 只看自己的行程
ps
```

`ps aux` 輸出格式：

```
USER   PID  %CPU %MEM    VSZ   RSS TTY    STAT  START   TIME COMMAND
root     1   0.0  0.1 168828 12764 ?      Ss   Mar15   0:08 /sbin/init
```

`STAT` 欄位的意義：

| 代碼 | 意義 |
|------|------|
| `R` | Running / Runnable |
| `S` | Sleep（可中斷）|
| `D` | Sleep（不可中斷）|
| `T` | Stopped（暫停）|
| `Z` | Zombie |
| `s` | Session leader |
| `+` | Foreground process group |
| `l` | 多執行緒 |
| `<` | 高優先權 |
| `N` | 低優先權 |

## pstree：行程樹

```bash
pstree              # 印出整棵行程樹
pstree -p           # 顯示 PID
pstree -u           # 顯示 UID 切換
pstree 1234         # 從 PID 1234 開始
pstree -a           # 顯示完整指令列
```

輸出範例：

```
systemd─┬─NetworkManager───2*[{NetworkManager}]
        ├─cron
        ├─sshd───sshd───bash───pstree
        └─nginx───4*[nginx]
```

## top：動態監控

```bash
top                 # 互動模式
top -b -n 1         # batch 模式，只跑一次（適合腳本）
top -p 1234         # 只監控某 PID
```

`top` 互動快捷鍵：

| 鍵 | 作用 |
|----|------|
| `q` | 離開 |
| `k` | Kill 某個 PID |
| `r` | Renice（改優先權）|
| `M` | 按記憶體排序 |
| `P` | 按 CPU 排序 |
| `1` | 展開每個 CPU core |
| `H` | 顯示 threads |
| `z` | 彩色模式 |

top 的上方摘要：

```
load average: 0.12, 0.18, 0.15   # 1分/5分/15分 平均負載
Tasks: 213 total, 1 running, 212 sleeping
%Cpu(s):  2.1 us,  0.5 sy,  0.0 ni, 96.8 id
MiB Mem:   7867.5 total,   2341.2 free
```

Load average > CPU 核數就代表 CPU 跑不完，有排隊。

## /proc 目錄裡的行程資訊

每個行程在 `/proc/<PID>/` 都有一個目錄：

```bash
cat /proc/1/status    # 行程狀態（Name, State, Pid, PPid...）
cat /proc/1/cmdline   # 完整指令列（\0 分隔）
ls -la /proc/1/fd     # 行程開啟的檔案描述符
cat /proc/1/maps      # 記憶體映射
cat /proc/1/environ   # 環境變數（\0 分隔）
```

用 `strings` 讓可讀性好一點：

```bash
strings /proc/1/environ | grep PATH
```

## 優先權：nice 值

```bash
nice -n 10 ./heavy-job.sh    # 以較低優先權啟動
renice 5 -p 1234             # 改變執行中行程的 nice 值
renice -5 -p 1234            # 提高優先權（需要 root）
```

nice 值範圍 -20（最高）到 19（最低），預設 0。一般使用者只能調高（讓步），不能調低（強佔）。

## 動手練習

```bash
# 1. 找系統上 zombie 行程（如果有的話）
ps aux | awk '$8=="Z" {print $1, $2, $11}'

# 2. 找 CPU 用量最高的前 5 個行程
ps aux --sort=-%cpu | head -6

# 3. 找某個 port 被哪個行程佔用（需要下一章的 ss）
# 先找 PID：ss -tlnp | grep :80
# 再找行程：ps -p <PID> -o pid,user,cmd

# 4. 看 init/systemd 的所有子行程
pstree -p 1

# 5. 用 top -b 截取一次快照並存檔
top -b -n 1 > /tmp/top-snapshot.txt
cat /tmp/top-snapshot.txt

# 6. 查看當前 shell 的 PID 和它開啟的 fd
echo $$                  # 當前 shell PID
ls -la /proc/$$/fd       # 看它開啟的 fd
cat /proc/$$/status | head -10
```

## 常見狀況排查

```bash
# 行程佔用 CPU 太高
top     # 按 P 排序，找 PID
ps aux --sort=-%cpu | head

# 行程一直是 D（uninterruptible sleep）
ps aux | awk '$8~/D/'    # 找 D 狀態行程
dmesg | tail             # 看核心 log 有沒有 I/O 錯誤

# 殭屍行程太多
ps aux | grep 'Z'        # 找殭屍
pstree -p | grep 行程名  # 找父行程，看父行程是否異常
```

## 自我檢核

- [ ] 能解釋 fork → exec → wait 的完整流程
- [ ] 記住 R/S/D/Z/T 五種狀態，知道 D 狀態 kill 不了
- [ ] 能用 `ps aux` 找特定行程、排序 CPU/記憶體
- [ ] 知道 `/proc/<PID>/` 目錄裡有什麼

→ [Ch 16 訊號（Signals）](./16-signals.md)
