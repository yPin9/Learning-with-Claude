# Ch 16 — ps/top/proc filesystem

> **目標**：精通 process 觀測——ps 的兩套語法（BSD vs UNIX）、top/htop 的即時監控、以及 /proc 這個「process 資訊的檔案系統」如何讓你看穿任何 process。把前面的 process 概念（狀態、fork、fd）變成實際的觀測能力。

> **環境**：procps-ng（ps/top），Linux /proc。承接 Ch 14（狀態）、Ch 15（fork）。

## 為什麼 process 觀測工具值得一章？

你會 `ps aux` 和 `top`，但 ps 的語法混亂（為什麼 `ps aux` 沒有 dash 但 `ps -ef` 有？）、top 的數字很多看不懂、`/proc` 的威力多數人沒用上。

理解這些觀測工具，你能回答「這個 process 在幹嘛」「誰吃了 CPU/記憶體」「這個 PID 開了哪些檔案」「process 卡在哪個 syscall」。這是 SysOps debug 的日常——當系統變慢、某個程式行為異常，你用這些工具定位問題。/proc 尤其強大——它把 kernel 的 process 資訊（Ch 14 的 task_struct）暴露成檔案，你能直接讀。

## 先建立直覺：三種觀測層次

```
process 觀測的三個層次：

  ps：快照（某一刻的 process 列表）
    「現在有哪些 process、各自什麼狀態」
        │
  top/htop：即時監控（持續更新）
    「現在誰吃 CPU/記憶體，動態變化」
        │
  /proc/<pid>/：深挖單一 process
    「這個 process 的記憶體、fd、狀態、限制...一切」
        │
  → ps 看全局快照、top 看即時動態、/proc 深挖單一
    三者互補：ps 找到可疑 PID → top 看它的即時行為 → /proc 深挖細節
```

## ps：兩套語法的混亂

ps 有歷史包袱——它支援兩套不相容的語法（BSD 和 UNIX），這是新手困惑的根源：

```bash
# BSD 語法（選項「不」加 dash）
ps aux           # a=所有使用者 u=詳細 x=含無終端機的
#   ↑ 沒有 dash！aux 不是 -aux

# UNIX 語法（選項「加」dash）
ps -ef           # -e=所有 process -f=完整格式
ps -eLf          # 加 -L 顯示 thread

# 兩者顯示類似資訊，格式略不同
ps aux | head -3
# USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND
ps -ef | head -3
# UID PID PPID C STIME TTY TIME CMD
```

> **ps 的 `aux` vs `-ef` 是歷史遺留的兩套語法**。`ps aux`（BSD 風格，無 dash）和 `ps -ef`（UNIX 風格，有 dash）是兩個不同 Unix 傳統的語法，procps 同時支援以相容。它們顯示類似資訊（process 列表），欄位略不同（aux 有 %CPU/%MEM，-ef 有 PPID）。記住：`aux` 不加 dash（`-aux` 會被解讀成混合語法，行為可能不如預期）。常用 `ps aux`（看資源）或 `ps -ef`（看 PPID 父子關係）。別糾結語法混亂——這是 Unix 兩個傳統融合的疤痕。

## ps 的常用組合

```bash
# 看所有 process
ps aux

# 按 CPU/記憶體排序
ps aux --sort=-%cpu | head        # CPU 最高的（- 是降序）
ps aux --sort=-%mem | head        # 記憶體最高的

# 找特定 process
ps aux | grep nginx               # grep 篩選（注意 grep 自己也會出現）
pgrep nginx                       # 直接給 PID（更乾淨）
pgrep -a nginx                    # PID + 命令列

# 看 process 樹（父子關係）
ps -ef --forest                   # 樹狀顯示
pstree                            # 更清楚的樹

# 自訂欄位
ps -eo pid,ppid,stat,%cpu,%mem,comm   # 只顯示你要的欄位
ps -eo pid,stat,wchan,comm        # wchan：process 在 kernel 等什麼（D 狀態 debug）

# 看特定 PID
ps -p 1234 -o pid,stat,comm
```

> `pgrep`/`pkill` 比 `ps aux | grep` 乾淨——`ps aux | grep nginx` 會把 grep 自己也列出來（grep nginx 這個 process 含 "nginx"）。`pgrep nginx` 直接給 PID，不含自己。`ps --sort=-%cpu` 找吃 CPU 的、`--sort=-%mem` 找吃記憶體的——SysOps 找「誰拖慢系統」的第一步。`ps -eo` 自訂欄位（如 `wchan` 看 process 在 kernel 等什麼，debug D 狀態用）。`pstree`/`ps --forest` 看父子樹（理解 fork 出的關係，Ch 15）。

## top / htop：即時監控

```bash
top              # 即時 process 監控（持續更新）

# top 裡的操作：
#   P    按 CPU 排序（預設）
#   M    按記憶體排序
#   k    殺一個 process（輸入 PID）
#   1    顯示每個 CPU 核心
#   q    離開

# top 的頂部數字：
# load average: 1.50, 1.20, 0.90
#   ↑ 1/5/15 分鐘的平均 load（後述）
# Tasks: 200 total, 1 running, 199 sleeping, 0 stopped, 0 zombie
#   ↑ process 狀態統計（對應 Ch 14 的狀態機）

htop             # 更友善的 top（顏色、滑鼠、樹狀，要另裝）
```

理解 **load average**（最常被誤解的數字）：

```
load average：1.50, 1.20, 0.90（1/5/15 分鐘平均）
  load = 「正在執行 + 等待執行 + 等 I/O（D 狀態）」的 process 平均數
        │
  怎麼解讀（以核心數為基準）：
    1 核心系統：load 1.0 = 滿載，> 1.0 = 過載（有 process 排隊）
    4 核心系統：load 4.0 = 滿載，load 1.0 = 25% 用量
        │
  關鍵：load 包含「D 狀態」（等 I/O，Ch 14）！
    → load 高但 CPU 閒 = 大量 D 狀態（I/O 瓶頸，不是 CPU）
    → 這是 Linux load 和其他 Unix 的差異（Linux 算進 D）
```

> **load average 包含 D 狀態（等 I/O）是 Linux 的特色，也是常見誤解**。很多人以為 load 只反映 CPU，但 Linux 的 load 算進「等 I/O 的 process」（D 狀態，Ch 14）。所以「load 高但 top 看 CPU 閒」很常見——是 I/O 瓶頸（磁碟慢、大量 D 狀態），不是 CPU。解讀 load 要除以核心數：4 核心 load 4.0 = 滿載，load 1.0 = 25%。`nproc` 看核心數。看到 load 飆高，先判斷是 CPU 滿（top 看 %CPU）還是 I/O 卡（大量 D 狀態，`ps aux | awk '$8 ~ /D/'`）。Brendan Gregg 有篇經典文章詳解 Linux load。

## /proc：process 資訊的檔案系統

`/proc` 是本課反覆用的——它把 kernel 的 process 資訊（Ch 14 的 task_struct）暴露成檔案：

```bash
# 每個 process 在 /proc/<pid>/
ls /proc/self/                    # self = 當前 process
# cmdline  cwd  environ  fd  maps  status  stat  ...

# 重要的檔案：
cat /proc/self/status             # 狀態、PID、記憶體、UID...（Ch 14）
cat /proc/self/cmdline | tr '\0' ' '   # 完整命令列（用 \0 分隔）
ls -l /proc/self/cwd              # 當前目錄（Ch 3）
ls -l /proc/self/fd               # 開啟的 fd（Ch 19！）
cat /proc/self/environ | tr '\0' '\n'  # 環境變數（Ch 29）
cat /proc/self/maps               # 記憶體映射（哪些 library 載入哪）
cat /proc/self/limits             # 資源限制（ulimit）
```

```
/proc/<pid>/ 的關鍵檔案：
  status    人類可讀的狀態（State/PID/PPID/記憶體/UID...）
  stat      機器可讀的單行（ps 解析這個）
  cmdline   完整命令列（\0 分隔的 argv）
  cwd       → 當前目錄的 symlink（Ch 3）
  fd/       → 開啟的 file descriptor（Ch 19 深入）
  maps      記憶體映射（程式碼/堆疊/library 的位址）
  environ   環境變數（Ch 29）
  limits    資源限制（ulimit 設的）
  wchan     process 在 kernel 等什麼（D 狀態 debug）
        │
  → /proc 是「把 task_struct 暴露成檔案」（Ch 14）
    你能用 cat/ls 看任何 process 的內部
    這是「一切皆檔案」（Ch 1）的極致展現
```

> `/proc/<pid>/` 是 SysOps debug 的寶庫。`/proc/<pid>/fd/` 看 process 開了哪些檔案（Ch 19，「誰開著被刪的檔案」「誰佔用這個檔案」）。`/proc/<pid>/cmdline` 看完整命令列（ps 截斷時用）。`/proc/<pid>/environ` 看它的環境變數（debug 環境問題）。`/proc/<pid>/maps` 看載入了哪些 library（debug 連結問題）。`/proc/<pid>/limits` 看資源限制（為什麼 process 開不了更多檔案/記憶體）。這些都是 kernel 把 task_struct（Ch 14）暴露成檔案——「一切皆檔案」（Ch 1）的極致。`ps`/`top` 底層就是讀 `/proc`（你能 `strace ps` 看它讀 /proc）。

## 系統層級的 /proc

`/proc` 不只 process，還有系統資訊：

```bash
cat /proc/cpuinfo                 # CPU 資訊（核心數、型號）
cat /proc/meminfo                 # 記憶體使用
cat /proc/loadavg                 # load average（top 的來源）
cat /proc/uptime                  # 開機多久
cat /proc/version                 # kernel 版本
cat /proc/mounts                  # 掛載（= mount，Ch 9）
ls /proc/sys/                     # kernel 可調參數（sysctl）
```

## 故意弄壞：找出吃資源的 process

```bash
# 場景：系統變慢，找出元兇
# 1. 看 load 和整體
uptime                            # load average
top -bn1 | head -15               # 一次快照（-b batch -n1 一次）

# 2. 找 CPU 大戶
ps aux --sort=-%cpu | head -5

# 3. 找記憶體大戶
ps aux --sort=-%mem | head -5

# 4. 深挖可疑 process
PID=<可疑PID>
cat /proc/$PID/status | grep -E "State|VmRSS|Threads"
ls -l /proc/$PID/fd | wc -l       # 開了幾個 fd
cat /proc/$PID/wchan              # 在 kernel 等什麼（如果是 D 狀態）

# 5. 看它在做什麼 syscall（Ch 0 的 strace）
strace -p $PID -c                 # attach 看它的 syscall 統計
```

這是 SysOps 的標準診斷流程：`uptime`/`top` 看整體 → `ps --sort` 找資源大戶 → `/proc/<pid>/` 深挖 → `strace -p` 看它在做什麼。把全課的工具串起來定位問題。

## 踩雷集錦

1. **ps aux vs -ef 語法混淆**：aux 不加 dash（BSD），-ef 加 dash（UNIX）。別寫 `-aux`（混合語法，行為意外）

2. **ps aux | grep 把 grep 自己列出**：`ps aux | grep nginx` 含 grep 那行。用 `pgrep nginx` 或 `grep [n]ginx`（正則技巧避開自己）

3. **誤解 load average 只反映 CPU**：Linux load 包含 D 狀態（等 I/O）。load 高但 CPU 閒 = I/O 瓶頸。要除以核心數解讀

4. **以為 /proc 是真檔案**：/proc 是 kernel 即時生成的（Ch 0/8）。cat /proc/<pid>/status 每次反映當下狀態，不是磁碟檔案

5. **top 的 %CPU 超過 100%**：多核心系統，一個 process 用滿多核可能顯示 > 100%（如 4 核全滿 = 400%）。這是正常的（per-core 加總）

## 進階：現代觀測工具

ps/top/proc 是經典，但有更強的現代工具：

```
現代 process/系統觀測工具：
  htop/btop：友善的 top（顏色、樹狀、滑鼠）
  pidstat（sysstat）：per-process 的詳細統計（CPU/I/O/記憶體）
  iotop：哪個 process 在做磁碟 I/O（找 D 狀態元兇）
  ss / netstat：哪個 process 開了哪些網路連線
  lsof：哪個 process 開了哪些檔案（Ch 0/19）
  perf：CPU profiling（Brendan Gregg 的領域）
  eBPF 工具（bpftrace）：客製化的深度觀測（bpf 課程）
        │
  → ps/top/proc 是基礎且到處都有
    特定問題用專門工具（iotop 找 I/O、lsof 找開檔、perf 找熱點）
```

```bash
# iotop：找磁碟 I/O 大戶（解 D 狀態之謎）
sudo iotop -o                     # 只顯示有 I/O 的

# pidstat：per-process 詳細統計
pidstat 1                         # 每秒更新各 process 的 CPU
pidstat -d 1                      # 磁碟 I/O

# lsof：process 開的檔案（Ch 19）
lsof -p <pid>                     # 某 process 開的所有檔案
```

> ps/top/proc 是基礎觀測（到處都有），但特定問題有更好的工具。**iotop** 找磁碟 I/O 大戶（解「load 高 CPU 閒」的 D 狀態之謎——誰在狂寫磁碟）。**pidstat** 給 per-process 的詳細時序統計。**lsof** 找誰開了哪些檔案（Ch 19，「誰佔用這個檔案/port」）。**perf** 和 **eBPF**（bpf 課程）是深度 profiling。如果你修過 observability_tools 或 bpf 課程，這些工具會更熟。本課聚焦 ps/top/proc（最基礎、最通用），但知道這些進階工具存在，能在基礎工具不夠時知道往哪找。

## 動手練習

1. 練 ps：`ps aux --sort=-%cpu | head`（CPU 大戶）、`ps -ef --forest`（樹）、`ps -eo pid,stat,wchan,comm`（自訂欄位）。用 `pgrep`/`pstree` 對比 grep

2. 探索 /proc：`cat /proc/self/status`、`ls -l /proc/self/fd`（Ch 19）、`cat /proc/self/maps`（library）、`cat /proc/self/limits`（ulimit）。理解 /proc 暴露 task_struct

3. 解讀 load：`uptime` 看 load，`nproc` 看核心數，計算使用率。`ps aux | awk '$8 ~ /D/'` 看有沒有 D 狀態（I/O）

4. 跑診斷流程：`top -bn1` → `ps --sort=-%cpu` → 挑一個 PID `cat /proc/<pid>/status` + `strace -p <pid> -c`。串起全課工具

## 本章重點整理

- 三層觀測：ps（快照）、top/htop（即時）、/proc/<pid>/（深挖單一）；互補使用
- ps 兩套語法：aux（BSD，無 dash）vs -ef（UNIX，有 dash）；pgrep 比 ps|grep 乾淨
- load average 包含 D 狀態（等 I/O）——load 高 CPU 閒 = I/O 瓶頸；要除以核心數解讀
- /proc/<pid>/ 把 task_struct（Ch 14）暴露成檔案：status/fd/cmdline/maps/limits，ps/top 底層讀它
- 診斷流程：top/uptime 看整體 → ps --sort 找大戶 → /proc 深挖 → strace -p 看 syscall

## 自我檢核

- [ ] 知道 ps aux 和 ps -ef 的語法差異（BSD vs UNIX）
- [ ] 能用 ps --sort 找 CPU/記憶體大戶，用 pgrep 取代 ps|grep
- [ ] 能正確解讀 load average（包含 D 狀態、除以核心數）
- [ ] 能用 /proc/<pid>/ 看一個 process 的狀態/fd/命令列/限制
- [ ] 能描述「系統變慢」的診斷流程（top → ps → /proc → strace）

## 延伸閱讀

### 部落格 / 文章

- **[Linux Load Averages: Solving the Mystery](https://www.brendangregg.com/blog/2017-08-08/linux-load-averages.html)** — Brendan Gregg
  - **這篇說什麼**：Linux load average 為什麼包含 D 狀態、怎麼正確解讀，含歷史考據
  - **讀哪裡**：整篇
  - **為什麼值得讀**：load average 是最被誤解的數字，Brendan Gregg 把它徹底講清楚

### 官方文件

- **[proc(5) man page](https://man7.org/linux/man-pages/man5/proc.5.html)**
  - **讀哪裡**：/proc/[pid]/ 的各檔案（status/stat/fd/maps/limits）
  - **學什麼**：/proc 暴露的所有資訊的權威定義
  - **前提**：本章

### 書籍

- **《Systems Performance, 2nd ed.》— Ch 6 (CPUs)** — Brendan Gregg
  - **讀哪幾章**：CPU 觀測、load average、process 監控
  - **這本書的定位**：系統觀測和效能的權威，本章的進階延伸
  - **前提**：本章

→ [Ch 17 signal](./17-signals.md)
