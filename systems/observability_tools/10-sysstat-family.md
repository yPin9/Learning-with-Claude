# Ch 10 — sysstat 家族（vmstat/iostat/pidstat/sar）

> **目標**：掌握系統資源統計工具——vmstat（CPU/記憶體/IO 總覽）、iostat（磁碟 IO）、pidstat（per-process 資源）、sar（歷史資料）、以及 top/htop 的進階用法。從「單一 process」（前幾章）擴展到「系統整體資源」的觀察。理解 USE 方法（Utilization/Saturation/Errors）——一個系統化的「系統哪裡是瓶頸」分析框架。這是 debug 效能和資源問題的入口。

> **環境**：Linux，sysstat 套件（Ch 0 已裝 vmstat/iostat/pidstat/sar）。

## 為什麼需要系統資源觀察？

前幾章觀察「單一 process 的行為」（strace/lsof/ss）。但很多問題是「**系統整體**」的——系統慢、CPU 滿、記憶體不夠、磁碟 IO 飽和。要 debug 這些，你需要看「系統的資源狀態」：CPU 在忙什麼（user/system/iowait）？記憶體夠不夠（free/cache/swap）？磁碟 IO 有沒有飽和？哪個 process 吃資源？

sysstat 家族（vmstat/iostat/pidstat/sar）提供這些系統資源的統計。它們是 /proc 系統層資訊（Ch 7）的格式化前端，但提供「隨時間的統計」（不只當下快照）。理解它們和「USE 方法」（系統化分析瓶頸的框架），你能從「系統慢」這個模糊症狀，定位到「是 CPU、記憶體、還是磁碟 IO 的問題」。這是 perf（Ch 12）深入之前的「系統層觀察」入口。

## 先建立直覺:USE 方法

```
USE 方法（Brendan Gregg）：系統化找瓶頸

  對每個資源（CPU/記憶體/磁碟/網路），問三個問題：
        │
  U - Utilization（使用率）：忙到什麼程度？
    CPU 使用率、記憶體用量、磁碟 IO 使用率
        │
  S - Saturation（飽和度）：有多少「排隊等待」？
    CPU run queue 長度、swap、磁碟 IO 等待
    （比使用率更重要——飽和=資源不夠，請求在排隊）
        │
  E - Errors（錯誤）：有沒有錯誤？
    丟包、IO 錯誤、OOM kill
        │
  → 對每個資源檢查 U/S/E，找出「哪個資源是瓶頸」
    系統化，不漏掉（vs 瞎猜）
        │
  工具對應：
    CPU：vmstat/top/pidstat（U=使用率, S=run queue）
    記憶體：vmstat/free（U=用量, S=swap）
    磁碟：iostat（U=%util, S=await/queue）
    網路：ss/sar（U=頻寬, S=佇列, E=丟包）
```

關鍵心智：**USE 方法**是系統化找瓶頸的框架——對每個資源（CPU/記憶體/磁碟/網路）檢查 **U**tilization（使用率）、**S**aturation（飽和/排隊）、**E**rrors（錯誤）。特別是**飽和度**比使用率更重要（飽和=資源不夠，請求在排隊）。sysstat 工具提供這些指標。

> sysstat 是 Ch 7 的 /proc 系統層資訊（loadavg/meminfo/stat）的統計前端。如果對 /proc 系統資訊不熟，回看 [Ch 7](./07-proc-filesystem-tour.md)。USE 方法是 Brendan Gregg 的效能分析框架。

## vmstat:系統資源總覽

```bash
# vmstat：CPU/記憶體/IO 的總覽（每 1 秒一次，看 5 次）
vmstat 1 5
# procs -----memory---- ---swap-- -----io---- -system-- ------cpu-----
#  r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
#  2  0      0  2.0G   100M  4.0G    0    0     5    20  500 1000 15  5 78  2  0
#  ↑ ↑                                                            ↑  ↑  ↑  ↑
#  │ │（記憶體/swap）                                          us sy id wa
#  │ b = 等 IO 的 process 數（飽和！D 狀態）
#  r = run queue（等 CPU 的 process 數，飽和！）
#                                                  user/system/idle/iowait CPU%

# 關鍵欄位（USE 方法）：
#   r：等 CPU 的 process 數（>核心數 = CPU 飽和，請求在排隊）★
#   b：等 IO 的 process 數（>0 持續 = IO 瓶頸，D 狀態）★
#   si/so：swap in/out（>0 = 記憶體不夠在用 swap，慢！）★
#   wa：iowait%（CPU 在等磁碟 IO 的時間，高 = IO 瓶頸）
#   us/sy：user/system CPU%（程式 vs kernel 的 CPU）
```

> **vmstat 的 `r`（CPU run queue）、`b`（等 IO）、`si/so`（swap）是 USE 方法的飽和度指標——比 CPU 使用率更能指出瓶頸**。vmstat 是系統資源的「總覽」，最重要的是飽和度指標：**`r`**（run queue，等 CPU 的 process 數）——如果 `r` 持續 > CPU 核心數，表示 **CPU 飽和**（有 process 在排隊等 CPU，這比「CPU 100%」更能說明問題——100% 使用率可能正常，但 run queue 長表示「不夠用」）；**`b`**（等 IO 的 process 數，D 狀態，Ch 2）——持續 > 0 表示**磁碟 IO 是瓶頸**（process 卡在等 IO）；**`si/so`**（swap in/out）——> 0 表示**記憶體不夠**，系統在用 swap（把記憶體換到磁碟，極慢！這是「系統突然變超慢」的常見原因）；**`wa`**（iowait%）——CPU 在等磁碟 IO 的時間比例，高表示 IO 瓶頸。`us`/`sy`（user/system CPU）區分「程式自己的 CPU」vs「kernel 的 CPU」（sy 高可能是大量 syscall/中斷）。vmstat 一行看出系統的整體狀態——先用它判斷「瓶頸大概在 CPU、記憶體、還是 IO」，再用專門工具（iostat/pidstat）深入。記住看**飽和度**（r/b/si-so）而非只看使用率——飽和才是「資源不夠」的信號。

## iostat / pidstat:磁碟與 per-process

```bash
# === iostat：磁碟 IO 詳情 ===
iostat -x 1 3                    # -x 詳細, 每秒, 3 次
# Device  r/s  w/s  rkB/s  wkB/s  ...  await  ...  %util
# sda     10   50   200    1000   ...  5.2    ...  45.0
#                                       ↑           ↑
#                              await（IO 平均等待ms）  %util（磁碟忙碌%）
# 關鍵：
#   %util：磁碟使用率（接近 100% = 磁碟飽和）★
#   await：IO 平均等待時間（高 = IO 慢/排隊，飽和）★
#   r/s, w/s：每秒讀寫次數（IOPS）

# === pidstat：per-process 資源（哪個 process 吃資源）===
pidstat 1 3                      # 每秒, 3 次（各 process 的 CPU）
pidstat -d 1 3                   # -d：各 process 的磁碟 IO（誰在讀寫磁碟）★
pidstat -r 1 3                   # -r：各 process 的記憶體（誰吃記憶體）
pidstat -t 1 3                   # -t：到 thread 層級
# → 從「系統資源緊」定位到「哪個 process/thread 吃資源」

# 範例：系統 IO 高（iostat 看到）→ 哪個 process 在讀寫？（pidstat -d）
pidstat -d 1 3
# PID   kB_rd/s  kB_wr/s  Command
# 1234  5000     2000     myapp      ← myapp 在大量讀寫磁碟！
```

> **iostat 的 `%util`/`await`（磁碟飽和）+ pidstat（per-process 定位）= 從「系統 IO 慢」到「哪個 process 在吃 IO」**。**iostat -x** 看磁碟 IO 的飽和度：**`%util`**（磁碟使用率，接近 100% = 磁碟飽和，沒餘力）、**`await`**（IO 平均等待時間 ms，高 = IO 慢或在排隊，飽和度指標）、`r/s`/`w/s`（IOPS，每秒讀寫次數）。當系統慢且 vmstat 顯示 `b`/`wa` 高（IO 瓶頸），iostat 確認是磁碟飽和。**pidstat** 是「per-process 資源」——把「系統資源緊」定位到「**哪個 process** 吃資源」：`pidstat`（各 process 的 CPU）、`pidstat -d`（各 process 的磁碟 IO——誰在讀寫磁碟，這是 iostat 顯示 IO 高之後的下一步：找出兇手）、`pidstat -r`（各 process 的記憶體）、`pidstat -t`（到 thread 層級）。典型流程：vmstat 看出「IO 瓶頸」→ iostat 確認「磁碟飽和（%util 高）」→ pidstat -d 找出「哪個 process 在大量讀寫」→ 對那個 process 用 strace（Ch 5）看它在讀寫什麼。這是「系統層 → process 層」的定位鏈——從模糊的「系統慢」逐步縮小到「具體哪個 process 做什麼」。這也是 USE 方法的實踐——檢查每個資源的飽和度，找出瓶頸，再定位到具體 process。

## sar / top:歷史資料與互動監控

```bash
# === sar：歷史資料（過去的系統狀態）===
# sar 會定期記錄系統狀態到檔案（如果啟用）
sar                              # 今天的 CPU 歷史
sar -r                           # 記憶體歷史
sar -d                           # 磁碟歷史
sar -n DEV                       # 網路歷史
sar -q                           # run queue / load 歷史
# 看特定時間範圍
sar -s 09:00:00 -e 10:00:00      # 9-10 點的資料
# → 「昨天半夜系統為什麼慢」→ sar 看那時的歷史資料！
#   這是 sar 獨有的價值：事後分析（其他工具只看當下）

# === top / htop：互動式即時監控 ===
top
# 互動鍵：
#   P：按 CPU 排序  M：按記憶體排序
#   1：展開每個 CPU 核心
#   按 process 看詳情
# 關注：load average、各 process 的 %CPU/%MEM、狀態（D=等IO）

htop    # 更友善的 top（彩色、樹狀、滑鼠）
```

> **sar 的獨特價值是「歷史資料」——「昨天半夜系統為什麼慢」只有 sar 能回答（其他工具只看當下）**。大部分工具（vmstat/iostat/top）只看**當下**——但很多問題是「過去發生的」（昨天半夜系統卡了、某個時段變慢）。**sar**（System Activity Reporter）獨特之處是它**定期記錄系統狀態到檔案**（如果啟用 sysstat 的收集），所以你能**事後查歷史**：`sar`（CPU 歷史）、`sar -r`（記憶體）、`sar -d`（磁碟）、`sar -n DEV`（網路）、`sar -q`（負載），配合 `-s`/`-e` 指定時間範圍。這讓你能回答「昨天 03:00 系統為什麼慢」——`sar -s 03:00:00 -e 04:00:00` 看那時的 CPU/記憶體/IO，找出當時的瓶頸。這是**事後分析**的關鍵（其他工具只能「現在開始觀察」，問題過了就抓不到）。需要先啟用 sysstat 的定期收集（`/etc/cron.d/sysstat` 或 systemd timer）。**top/htop** 是互動式即時監控——`top` 的互動鍵（P 按 CPU 排序、M 按記憶體、1 展開核心）讓你即時看「哪個 process 吃資源、系統負載、有沒有 D 狀態的 process」。htop 更友善（彩色、樹狀、滑鼠）。這些工具組成系統資源觀察的完整時間維度——top/vmstat 看當下、sar 看歷史。記住：**要事後分析過去的問題，sar 是唯一的選擇**（前提是有啟用收集）。

## 故意弄壞:製造並定位資源瓶頸

```bash
# 製造各種資源瓶頸，用 sysstat 工具定位
cd ~/obslab

# === 製造 CPU 瓶頸 ===
# 跑一個吃 CPU 的程式
yes > /dev/null &
CPU_HOG=$!
vmstat 1 3
# r 變大（run queue）、us 變高（user CPU）→ CPU 瓶頸
pidstat 1 1 | sort -k8 -rn | head -3
# → 找出吃 CPU 的 process（yes）
kill $CPU_HOG

# === 製造 IO 瓶頸 ===
# 跑一個狂寫磁碟的程式
dd if=/dev/zero of=/tmp/bigfile bs=1M count=1000 &
IO_HOG=$!
iostat -x 1 2
# %util 接近 100、await 變高 → 磁碟飽和
pidstat -d 1 1
# → 找出在寫磁碟的 process（dd）
wait $IO_HOG; rm /tmp/bigfile

# === 製造記憶體壓力（觀察 swap）===
# （小心：別真的把記憶體吃爆）
# vmstat 1 觀察 si/so（swap in/out）—— 持續 > 0 = 記憶體不夠在用 swap

# === USE 方法實戰：系統慢，哪個資源是瓶頸？===
# 1. vmstat 1：看 r（CPU飽和）/ b,wa（IO）/ si,so（記憶體）哪個異常
# 2. 對應到問題資源用專門工具（iostat for IO, pidstat for process）
# 3. pidstat 定位到具體 process
# 4. strace/perf 看那個 process 在做什麼
```

> **製造 CPU/IO 瓶頸並用 USE 方法定位——這是「系統慢 → 哪個資源 → 哪個 process → 它在做什麼」的完整定位鏈**。這些實驗讓你親手製造資源瓶頸並定位：**CPU 瓶頸**（跑 `yes`）→ vmstat 看 `r`（run queue 變大）和 `us`（user CPU 高）→ pidstat 找出吃 CPU 的 process；**IO 瓶頸**（跑 `dd` 狂寫）→ iostat 看 `%util`（接近 100）和 `await`（變高）→ pidstat -d 找出在寫磁碟的 process；**記憶體壓力** → vmstat 看 `si/so`（swap）。這實踐了 **USE 方法的完整定位鏈**：(1) **vmstat 判斷瓶頸資源**（r=CPU、b/wa=IO、si/so=記憶體——一行看出大方向）；(2) **專門工具確認**（iostat for IO、free for 記憶體）；(3) **pidstat 定位到 process**（哪個 process 吃這個資源）；(4) **strace/perf 看 process 在做什麼**（為什麼吃這麼多）。這個「系統 → 資源 → process → 行為」的逐層定位是效能 debug 的標準流程——從模糊的「系統慢」一步步縮小到「具體的 process 做具體的事」。這也是 perf（Ch 12）深入之前的系統層基礎——sysstat 告訴你「哪個資源、哪個 process 是瓶頸」，perf 再深入「那個 process 的時間花在哪個函式」。掌握這個流程和 USE 方法，你 debug 效能問題就有系統化的方法，不再瞎猜。

## 動手練習

1. vmstat 總覽：`vmstat 1`，理解 r/b/si/so/wa 各代表什麼（USE 方法的指標）

2. iostat 磁碟：`iostat -x 1`，理解 %util 和 await（磁碟飽和度）

3. pidstat 定位：`pidstat 1` 和 `pidstat -d 1`，找出吃 CPU/IO 的 process

4. top 互動：用 top 的 P/M/1 鍵，看哪個 process 吃資源、有沒有 D 狀態

5. 跑「故意弄壞」：製造 CPU 瓶頸（yes）和 IO 瓶頸（dd），用 USE 方法完整定位（vmstat→工具→pidstat）

## 本章重點整理

- USE 方法：對每個資源（CPU/記憶體/磁碟/網路）檢查 Utilization（使用率）/Saturation（飽和/排隊）/Errors——飽和度最重要
- vmstat 總覽：r（CPU run queue 飽和）、b/wa（IO 瓶頸）、si/so（記憶體不夠用 swap）——一行判斷瓶頸資源
- iostat：磁碟 IO 的 %util（使用率）/await（飽和）；pidstat：per-process 資源（-d 磁碟、-r 記憶體）定位兇手
- sar 的獨特價值是歷史資料（事後分析「過去為什麼慢」）；top/htop 互動式即時監控
- 定位鏈：vmstat 判斷瓶頸資源 → 專門工具確認 → pidstat 定位 process → strace/perf 看它做什麼

## 自我檢核

- [ ] 理解 USE 方法，知道飽和度為什麼比使用率重要
- [ ] 會用 vmstat 一行判斷瓶頸在 CPU/記憶體/IO
- [ ] 會用 iostat 看磁碟飽和、pidstat 定位吃資源的 process
- [ ] 知道 sar 的獨特價值（歷史資料、事後分析）
- [ ] 能走完「系統慢 → 哪個資源 → 哪個 process → 它做什麼」的定位鏈

## 延伸閱讀

### 必讀書籍

- **《Systems Performance》— Ch 2 (Methodologies), Ch 6 (CPUs)** — Brendan Gregg
  - **讀哪幾章**：Ch 2（USE 方法等方法論）、Ch 6-9（CPU/記憶體/磁碟/網路的工具）
  - **這本書的定位**：系統效能分析的權威；USE 方法和這些工具的完整框架
  - **前提**：本章

### 文章

- **[The USE Method](https://www.brendangregg.com/usemethod.html)** — Brendan Gregg
  - **核心貢獻**：USE 方法的原始定義，配 Linux 工具的對照表
  - **讀哪裡**：整篇 + Linux checklist
  - **為什麼值得讀**：本章 USE 方法的原始來源，有完整的「資源→工具」對照

- **[Linux Performance Analysis in 60 seconds](https://netflixtechblog.com/linux-performance-analysis-in-60-000-milliseconds-accc10403c55)** — Brendan Gregg / Netflix
  - **這篇說什麼**：60 秒內用 10 個工具快速診斷系統
  - **為什麼值得讀**：把 sysstat 工具組成快速診斷流程，極實用

### 官方文件

- **[sysstat 文件](https://github.com/sysstat/sysstat)** — sysstat
  - **讀哪裡**：vmstat/iostat/pidstat/sar 的 man page
  - **為什麼值得讀**：各工具欄位的權威說明

下一個是練習 B——fd 劫持調查，綜合 Part 3 的 /proc/lsof/ss 工具，調查一個 fd/連線相關的謎題。

→ [練習 B：fd 劫持調查](./practice-b-fd-hijack-investigation.md)
