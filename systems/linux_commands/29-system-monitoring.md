# Ch 29 — 系統監控

> 目標：能用 `vmstat`/`iostat`/`free`/`lsof`/`strace` 分析系統瓶頸，看懂 CPU、記憶體、I/O 指標。

## 記憶體：free

```bash
free -h               # human-readable
free -h -s 2          # 每 2 秒更新一次
```

輸出解讀：

```
              total        used        free      shared  buff/cache   available
Mem:           7.7G        2.3G        1.2G       186M        4.2G        5.0G
Swap:          2.0G          0B        2.0G
```

- `used`：行程用掉的
- `buff/cache`：核心快取（可回收）
- `available`：**實際可用的**（used + cache 可以被回收的）

**不要看 `free` 欄位，要看 `available`。** Linux 會把空閒記憶體用作快取，`free` 很小是正常的。

## 整體效能：vmstat

```bash
vmstat 1 5           # 每秒一行，共 5 行
```

輸出：

```
procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
 2  0      0 1234567 12345 4567890    0    0     0    12  320  580 15  5 79  1  0
```

關鍵欄位：

| 欄位 | 意義 | 問題跡象 |
|------|------|---------|
| `r` | Run queue（等待 CPU 的行程數）| 持續 > CPU 核數 |
| `b` | Blocked（等 I/O）| 持續 > 0 |
| `si`/`so` | Swap in/out | 任何值都代表記憶體不足 |
| `wa` | CPU wait for I/O（%）| 持續高 = I/O 瓶頸 |
| `us` | User CPU | 應用程式負載 |
| `sy` | System/kernel CPU | 系統呼叫開銷 |
| `id` | Idle | 100-id = 總使用率 |

## I/O 效能：iostat

```bash
# 需要安裝 sysstat 套件
apt install sysstat   # Debian/Ubuntu
dnf install sysstat   # RHEL/Fedora

iostat 1 5            # 每秒一行，共 5 行
iostat -x 1 5         # -x = 更多欄位（延遲、佇列長度）
iostat -xd 1          # 只看磁碟，不看 CPU
```

`iostat -x` 的關鍵欄位：

| 欄位 | 意義 | 問題跡象 |
|------|------|---------|
| `%util` | 磁碟使用率 | 接近 100% = I/O 飽和 |
| `await` | 平均 I/O 等待時間（ms）| > 20-50ms 值得關注 |
| `r/s` / `w/s` | 每秒讀/寫次數 | 配合 await 判斷 |

## CPU / 行程：top / htop

```bash
top               # 互動式監控（上一章介紹過）
htop              # 更漂亮，如果有安裝
```

其他 CPU 相關：

```bash
uptime            # load average 和 uptime
nproc             # CPU 核數
cat /proc/cpuinfo | grep "model name" | head -1   # CPU 型號
```

## lsof：深入版

除了 Ch 18 介紹的 fd 用途，`lsof` 在監控上也很有用：

```bash
# 找哪個行程在佔用 port（替代 ss -tlnp）
lsof -i :80
lsof -i :443
lsof -i TCP -s TCP:LISTEN   # 所有 TCP listen

# 找哪個行程在寫特定檔案
lsof /var/log/app.log

# 找某個行程開了哪些網路連線
lsof -i -p 1234

# 統計每個使用者開啟的 fd 數
lsof | awk 'NR>1 {count[$3]++} END {for (u in count) print count[u], u}' | sort -rn
```

## strace：系統呼叫追蹤

`strace` 攔截行程的 system call，是 debug 和排錯的利器：

```bash
strace ls /tmp          # 追蹤 ls 的所有系統呼叫
strace -e open ls /tmp  # -e = 只追蹤指定的 syscall
strace -p 1234          # 附加到已執行的行程
strace -c ls /tmp       # -c = 統計 syscall 次數和時間（profile 用）
strace -o /tmp/trace.log ls /tmp   # 輸出到檔案
```

常用場景：

```bash
# 找程式在讀哪些設定檔
strace -e openat,open ./myapp 2>&1 | grep -E '\.conf|\.yaml|\.json'

# 找程式在連哪些 server
strace -e connect,socket ./myapp 2>&1 | grep -v "AF_UNIX"

# 找程式 segfault 的原因（看最後幾個 syscall）
strace ./crashing-program 2>&1 | tail -20
```

## 整合監控腳本

```bash
#!/usr/bin/env bash
# quick-health.sh：快速健康檢查

echo "=== System Health ==="
echo ""

echo "--- CPU (load average) ---"
uptime

echo ""
echo "--- Memory ---"
free -h

echo ""
echo "--- Disk ---"
df -h | grep -v "tmpfs\|udev"

echo ""
echo "--- Top Processes (CPU) ---"
ps aux --sort=-%cpu | head -6

echo ""
echo "--- Listening Ports ---"
ss -tlnp | grep LISTEN
```

## 動手練習

```bash
# 1. 觀察系統在執行 find 時的 I/O 狀況
# 在一個終端跑：
find / -name "*.conf" 2>/dev/null > /dev/null &
FIND_PID=$!
# 在另一個終端觀察：
vmstat 1 5
iostat 1 5

# 2. 看系統記憶體的 buff/cache 比例
free -h
# available 遠大於 free 是正常的（表示核心快取可回收）

# 3. 用 strace 找一個程式讀了哪些檔
strace -e openat echo "hello" 2>&1 | head -20

# 4. 找佔用最多 fd 的行程
lsof 2>/dev/null | awk 'NR>1 {print $2}' | sort | uniq -c | sort -rn | head -5

# 5. 建立一個每 30 秒跑一次的監控 loop
for i in {1..3}; do
    echo "=== $(date) ==="
    echo "Load: $(uptime | awk -F'load average:' '{print $2}')"
    echo "Mem:  $(free -h | awk '/Mem:/{print $7}') available"
    echo ""
    sleep 5   # 練習用 5 秒，實際用 30 秒
done
```

## 自我檢核

- [ ] 知道 `free` 的 `available` 才是真正可用記憶體，不要看 `free`
- [ ] 能用 `vmstat 1` 觀察 CPU 和 swap 活動
- [ ] 知道 `strace -c` 可以做 syscall 效能分析
- [ ] 能用 `lsof -i :PORT` 找佔用某 port 的行程

→ [Ch 30 套件管理與 systemd](./30-package-management-and-systemd.md)
