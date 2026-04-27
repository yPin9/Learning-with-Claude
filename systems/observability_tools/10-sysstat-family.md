# Ch 10 — sysstat 家族

> 目標：學會 vmstat / iostat / pidstat / mpstat / htop / atop / iotop —— 系統層快速健檢工具，知道每個的角度跟限制。

## 為什麼有這麼多工具

每個工具看一塊：

| 工具 | 看哪 |
|---|---|
| `htop` / `top` | 互動式：CPU / 記憶體 / process |
| `vmstat` | 全機 CPU / memory / IO 一行行報 |
| `iostat` | 磁碟 IO 統計 |
| `mpstat` | 每個 CPU core 的細節 |
| `pidstat` | 單 process 的 CPU / IO / memory 隨時間 |
| `iotop` | 互動式：誰在 IO |
| `atop` | 互動式：歷史回放 |
| `free` | 一次性記憶體 |
| `uptime` / `loadavg` | load average |
| `sar` | sysstat 的歷史紀錄查詢 |

選擇邏輯：**互動找熱點用 htop / iotop；定期 sample 用 vmstat / iostat；單 process 細看用 pidstat**。

## htop / top

```bash
top
htop
```

`htop` 比 `top` 好用（彩色、滑鼠、easier kill）。常用快捷鍵：

- `F2` 設定（哪些欄位）
- `F3` search
- `F4` filter
- `F5` tree view
- `F6` sort（按 CPU / Mem / ...）
- `F9` kill
- `H` toggle thread
- `t` tree
- `M` sort by memory
- `P` sort by CPU
- `u` filter user

關鍵欄位：

| 欄位 | 意義 |
|---|---|
| `VIRT` | virtual memory（含 mmap、不代表佔 RAM） |
| `RES` | resident（在 RAM 的） |
| `SHR` | 共享 |
| `S` | state |
| `CPU%` | CPU usage |
| `MEM%` | RES / total |
| `TIME+` | 累計 CPU time |

**看 process 吃記憶體看 `RES`，不是 `VIRT`**。`VIRT` 包含 mmap 的整個檔案，比實際大很多。

## load average

```bash
uptime
# 12:34:56 up 3 days, ..., load average: 1.23, 0.95, 0.78
```

三個數字 = 1 / 5 / 15 分鐘平均。意義是「平均同時想用 CPU 的 task 數」（含 R + D）。

判斷：

- load < CPU core 數 → 系統閒
- load ≈ core 數 → 滿載剛好
- load > core 數很多 → 有東西在排隊

注意 Linux 的 load **包含 D state**（uninterruptible sleep，多半是 disk IO）。所以 load 高可能是 IO 卡，不一定 CPU 滿。要分清楚要看 vmstat / iostat。

## vmstat

```bash
vmstat 2 5      # 每 2 秒一次，共 5 次
```

```
procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
 2  0      0 1234567 8910 11121314    0    0    12    34  567 8901 5  2 92  1  0
 3  0      0 1234500 8910 11121315    0    0     0    56  789 9012 8  3 88  1  0
```

關鍵欄位：

| 欄位 | 意義 |
|---|---|
| `r` | runnable + running task 數（**load 的 R 部分**） |
| `b` | uninterruptible sleep（D state）task 數 |
| `swpd` | swap used |
| `free` | free RAM |
| `cache` | page cache |
| `si` / `so` | swap in / out（per second） |
| `bi` / `bo` | block in / out（KB/s） |
| `in` | interrupts/s |
| `cs` | context switches/s |
| `us` / `sy` / `id` / `wa` / `st` | CPU %：user / system / idle / IO wait / steal |

**讀法**：

- `r` 持續 > core 數 → CPU bound
- `b` 持續 > 0 → IO 等待
- `wa` 高 → IO 卡
- `si` / `so` > 0 → swap 在用，**很糟，記憶體不夠**
- `cs` 很高 → 太多 context switch（可能 thread 太多 / lock 競爭）
- `st` > 0 → 在 VM，hypervisor 偷走 CPU

## iostat

```bash
iostat -x 2 5
```

```
Linux ...

avg-cpu:  %user   %nice %system %iowait  %steal   %idle
           5.42    0.00    1.21    2.34    0.00   91.03

Device       r/s     w/s    rkB/s    wkB/s   ...   await  ...   %util
sda         12.5    34.5    256.0   1024.0   ...   1.23   ...   12.34
nvme0n1      0.5     2.5     16.0    128.0   ...   0.45   ...    0.34
```

`-x` extended（一定加）。關鍵欄位：

| 欄位 | 意義 |
|---|---|
| `r/s` `w/s` | read/write 操作數 |
| `rkB/s` `wkB/s` | 吞吐量 |
| `await` | 平均一個 IO 完成時間（ms） |
| `r_await` `w_await` | 分開讀寫 |
| `aqu-sz` | average queue size |
| `%util` | device busy 比例 |

判斷：

- `%util` 接近 100 → device 滿了（但對 SSD 不準，多 queue）
- `await` 變大 → IO 變慢
- `aqu-sz` 高 + util 高 → 排隊嚴重

## mpstat

```bash
mpstat -P ALL 2 3
```

每個 core 分開印 user / sys / idle。發現「總 CPU 50% 但有一個 core 100%」（單 thread bottleneck）的標準工具。

```
CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
 all   12.34    0.00    3.45    0.12    0.00    0.45    0.00    0.00    0.00   83.64
   0   95.00    0.00    4.00    0.00    0.00    1.00    0.00    0.00    0.00    0.00
   1    1.00    0.00    1.00    0.00    0.00    0.00    0.00    0.00    0.00   98.00
   2    0.50    0.00    0.50    0.00    0.00    0.00    0.00    0.00    0.00   99.00
   3    0.50    0.00    0.50    0.00    0.00    0.00    0.00    0.00    0.00   99.00
```

CPU 0 100%、其他 idle → 程式只用一條 thread。

## pidstat

`vmstat` / `iostat` 看全機，`pidstat` 看單 process（或 process group）：

```bash
pidstat -p PID 2 5         # 該 PID 的 CPU
pidstat -p PID -d 2 5      # IO
pidstat -p PID -r 2 5      # memory
pidstat -p PID -t 2 5      # 連 thread 都列
pidstat -C bash 2 5        # 命令名 match
pidstat -G '^ngin' 2 5     # process group regex
```

debug「我的程式 CPU 高，是 user 還是 sys」：

```bash
pidstat -p $(pgrep myapp) 2
# 12:34:56  PID  %usr %sys ...
#           1234 80.0  5.0   ...    user 多 = 真的算東西
#           1234  5.0 80.0   ...    sys 多 = syscall 多，配 strace -c
```

## iotop

```bash
sudo iotop -o     # -o 只列 active
sudo iotop -P     # 按 PID（不展開 thread）
```

互動式，「誰在拼命寫磁碟」一目了然。Press `r` 反向 sort，按 `o` toggle active。

## atop

```bash
atop                # 互動
atop -r /var/log/atop/atop_2024...  # 讀過去紀錄
```

`atop` 比 `htop` 強的地方：**它在背景跑 daemon，每 10 分鐘記一次到磁碟**。事後可以 replay。

```bash
sudo systemctl enable --now atop
# 之後 /var/log/atop/ 累積資料
```

「上週三半夜某個時間 CPU 突然 100%」 → `atop -r` 翻紀錄。

## sar

`sar` (System Activity Reporter) 是 sysstat 套件的核心。也是 daemon 收集 + 事後查詢。

```bash
sar -u 2 5            # CPU 即時
sar -r 2 5            # memory
sar -d 2 5            # disk
sar -n DEV 2 5        # network
sar -q 2 5            # load
sar -B 2 5            # paging

# 歷史
sar -u -f /var/log/sysstat/sa15  # 15 號的 CPU
```

Debian / Ubuntu 預設 sysstat 不啟用收集，要：

```bash
sudo sed -i 's/false/true/' /etc/default/sysstat
sudo systemctl enable --now sysstat
```

## free

```bash
free -h
#               total   used   free   shared  buff/cache   available
# Mem:           15Gi   5Gi    1Gi    300Mi    9Gi          9Gi
# Swap:          4Gi    0B     4Gi
```

新手最常誤解的欄位：

- **`free`** 是「沒給任何東西用的」 — 通常很小，**這是好事**，不是壞事
- **`available`** 是「需要的話拿得到的」 — 包含可釋放的 cache

「我機器只有 1G free，是不是沒記憶體了」 → 看 available，9G 你還早呢。

Linux 哲學：**free RAM is wasted RAM**，全部拿去當 cache。要的時候自然 evict。

## 一個常見場景：CPU 高 → 找誰

```bash
top                   # 一眼看 PID
mpstat -P ALL 1       # 是不是單 core
pidstat -p PID 1      # 是 user 還是 sys
strace -c -p PID      # 如果 sys 高，看 syscall 分布
perf top -p PID       # 如果 user 高，看 hot function (Ch 12)
```

## 一個常見場景：IO 卡

```bash
vmstat 2              # b > 0、wa > 10 → IO bound
iostat -x 2           # 哪個 device、await 多大
iotop -o              # 誰在 IO
```

## 一個常見場景：記憶體吃光

```bash
free -h
vmstat 2              # si / so > 0 → swap 在用
ps aux --sort=-rss | head -10    # 哪個 process 吃最多
```

## 一個常見踩雷：CPU 100% 不一定是 user code

`%sy` 高表示 kernel 在忙。常見 kernel 忙的原因：

- 太多 syscall（`strace -c`）
- 太多 page fault（`vmstat` 看 swap）
- 太多 context switch（`vmstat cs`）
- 中斷風暴（`mpstat` 看 `%irq` `%soft`）

## 一個常見踩雷：load 高但 top 看不到 active

D state 的 process 不在 R 列但算 load。看 `ps -eo pid,state,cmd | grep ' D '`。多半是 disk 或 NFS hung。

## 動手練習

**1. 跑 stress 看各工具反應**

```bash
sudo apt install stress

# CPU
stress -c 4 &
htop
mpstat -P ALL 1

# IO
stress -i 4 &
iostat -x 1
iotop -o

# memory
stress -m 4 --vm-bytes 1G &
vmstat 1
free -h
```

每個 stress 模式對應到不同工具最敏感。

**2. 寫個爛 program 觀察**

```c
// busy.c — 100% CPU 一條 thread
int main() { while(1); }
```

```bash
gcc busy.c -o busy
./busy &
mpstat -P ALL 1   # 看一個 core 100、其他 idle
pidstat -p $(pgrep busy) 1   # %usr 接近 100
```

**3. 看 fdleak.c (Ch 8 寫的) 對 vmstat 影響**

跑 leak 程式，看 vmstat `free` 變化。fd 數量增加但 RAM 增加慢，因為每個 fd 只佔 kernel 一點 memory。

**4. 製造 IO wait**

```bash
dd if=/dev/zero of=/tmp/big bs=1M count=10000 oflag=direct &
vmstat 1
iostat -x 1
```

`bo` 飆高、`wa` 飆高、device `%util` 接近 100。

**5. 看 atop 紀錄**

裝好 atop 跑半天，看歷史。

## 自我檢核

- [ ] htop / top 各欄位（VIRT / RES / SHR / TIME+）會看
- [ ] vmstat 的 `r` `b` `wa` `cs` 看得懂
- [ ] iostat -x 的 `await` 跟 `%util` 看得懂
- [ ] 知道 CPU 高該 mpstat 看是不是單 core
- [ ] 知道 free 的 available 比 free 重要
- [ ] CPU / IO / memory 三類問題的 standard procedure 記住

下一個是 Part 3 整合練習：fd 劫持事件調查。

→ [練習 B：fd 劫持事件調查](./practice-b-fd-hijack-investigation.md)
