# Ch 20 — Parallel Fuzzing：多核 Campaign 設計

> **目標**：理解 AFL++ parallel fuzzing 的 master/secondary 架構，能正確設置多核 fuzzing campaign，並知道 instance 間如何同步 corpus。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼需要這個？

一個 AFL++ instance 只用一個 CPU core。如果你的機器有 16 cores，卻只跑一個 instance，你浪費了 15/16 的算力。

但 parallel fuzzing 的價值不只是線性的速度提升，還有**多樣性**：不同的 instance 有不同的隨機種子，走不同的 mutation path，在 queue 裡選不同的 seed 優先執行。一條 mutation path 卡住了，另一條可能剛好找到突破口。這個多樣性效益無法單靠加快單一 instance 達成。

2022 年的 AFL++ 論文（Fioraldi et al.）的實驗資料顯示，在相同 CPU 時間下，4 個 instance 比 1 個 instance 覆蓋率高 20-35%，8 個 instance 比 4 個 instance 高 10-15%。增益是遞減的，但在 8-16 cores 的範圍內仍然顯著。

## 先建立直覺

把 AFL++ 的 parallel fuzzing 想像成一個**圖書館的多個研究員**：

```
out/（共用書架）
├── fuzzer1/queue/    ← 研究員 1 的工作桌
├── fuzzer2/queue/    ← 研究員 2 的工作桌
├── fuzzer3/queue/    ← 研究員 3 的工作桌
└── ...
```

每個研究員（instance）有自己的工作桌（queue），獨立工作。但每隔一段時間，他們會走去其他人的桌子，看有沒有新的「有趣的 input」（有新 coverage 的 seed）可以借回來試試。這個「借閱」行為就是 corpus sync。

關鍵：他們**不是即時同步**的。研究員不會每發現一個新 seed 就立刻通知所有人，而是每隔幾分鐘自己去巡邏一次。

## AFL++ 4.x 的架構：Master vs Secondary

### 舊架構（AFL 時代）

AFL 原本有 `-M`（master）和 `-S`（secondary）的區分：

- Master：跑 deterministic stage（按序 bit flip、byte flip、arithmetic 等），比較慢但覆蓋系統性
- Secondary：跳過 deterministic，只跑 havoc，比較快

### 新架構（AFL++ 4.x 的建議）

AFL++ 4.x 的官方文件建議：**全部用 `-S`，不再推薦 `-M`**。

原因：
1. AFL++ 的 havoc 已經比 AFL 的 deterministic 更有效率（更多 mutation 策略）
2. Deterministic stage 的覆蓋率提升相比耗費的時間不划算
3. 全部用 `-S` 可以讓所有 instance 同等快速，總覆蓋率更高

`-M` 仍然存在，但實際效果在現代 AFL++ 版本上不如 all-secondary 策略。

**命名規則**：`-S name` 中的 `name` 是這個 instance 的識別名，會成為 `out/name/` 的目錄名，字母數字和底線。

---

## Corpus 同步機制

這是 parallel fuzzing 的核心，理解它能讓你診斷「為什麼 8 個 instance 跑起來卻好像沒有互相學習」的問題。

### `.synced/` 目錄的結構

每個 instance 的目錄下有一個 `.synced/` 子目錄：

```
out/
├── fuzzer1/
│   ├── queue/
│   │   ├── id:000000,orig:seed1
│   │   ├── id:000001,src:000000,...   ← fuzzer1 自己發現的
│   │   └── id:000002,src:000001,...
│   ├── .synced/
│   │   ├── fuzzer2                    ← 記錄從 fuzzer2 借了哪些
│   │   │   └── (timestamp file)
│   │   └── fuzzer3
│   ├── crashes/
│   └── hangs/
└── fuzzer2/
    ├── queue/
    └── .synced/
        └── fuzzer1
```

### Sync 的執行流程

```
fuzzer1 跑了一段時間後，開始 sync 週期：

1. 掃描 out/ 目錄，找到其他 instance 的名字（fuzzer2, fuzzer3...）
2. 對每個 other_instance：
   a. 讀取 .synced/other_instance 記錄的「上次 sync 到哪個 ID」
   b. 從 other_instance/queue/ 取出所有 ID > 上次 sync ID 的 seed
   c. 對每個候選 seed：執行一次 target，收集 coverage bitmap
   d. 和 fuzzer1 自己的 coverage bitmap 做 XOR/OR 比較
   e. 如果這個 seed 觸發了 fuzzer1 沒有的 coverage bit → 加入 fuzzer1/queue/
   f. 更新 .synced/other_instance 的記錄
3. Sync 完成，繼續 fuzzing
```

**重要細節**：Sync 不是「把所有 seed 複製過來」，而是「只引進能增加自己 coverage 的 seed」。一個對 fuzzer2 有新 coverage 的 seed，對 fuzzer1 不一定有新 coverage（因為 fuzzer1 可能已經走過那個 path）。

### Sync 頻率

AFL++ 大約每隔幾百個 fuzzing loop 做一次 sync（並非固定時間）。在快速 target（> 1000 exec/sec）下，可能每幾秒 sync 一次；在慢速 target 下，可能每幾分鐘一次。

---

## 範例一：8 核心的標準 Setup

```bash
# 確認 CPU 核心數
nproc  # 假設輸出 8

# 建立 output 目錄（所有 instance 共用）
mkdir -p out seeds

# 放入初始 seed
echo "GET / HTTP/1.1\r\nHost: localhost\r\n\r\n" > seeds/seed1

# Terminal 1 — 第一個 secondary（用 explore 策略，系統性探索）
AFL_PRELOAD=./libdislocator.so \
afl-fuzz -S fuzzer1 -p explore -i seeds/ -o out/ -- ./target @@

# Terminal 2 — 第二個 secondary（用 fast 策略，快速嘗試）
afl-fuzz -S fuzzer2 -p fast -i seeds/ -o out/ -- ./target @@

# Terminal 3 — 第三個 secondary（不同 power schedule）
afl-fuzz -S fuzzer3 -p exploit -i seeds/ -o out/ -- ./target @@

# Terminal 4 — CmpLog instance（協助突破比較指令）
afl-fuzz -S cmplog -c ./target_cmplog -p fast -i seeds/ -o out/ -- ./target @@

# Terminal 5-8 — 其餘 secondary，各用不同 -p
afl-fuzz -S fuzzer5 -p rare    -i seeds/ -o out/ -- ./target @@
afl-fuzz -S fuzzer6 -p mmopt   -i seeds/ -o out/ -- ./target @@
afl-fuzz -S fuzzer7 -p seek    -i seeds/ -o out/ -- ./target @@
afl-fuzz -S fuzzer8 -p explore -i seeds/ -o out/ -- ./target @@
```

**Power Schedule（`-p`）的選擇**：
- `fast`：預設，快速嘗試多個 seed
- `explore`：系統性探索，給少見 path 更多資源
- `exploit`：集中火力在最有希望的 seed
- `rare`：優先選擇 hit count 少的 path
- `seek`：快速輪換 seed
- `mmopt`：機器學習啟發的策略（AFL++ 特有）

---

## 範例二：用 tmux 管理多個 Instance

手動開 8 個 terminal 很麻煩，用 tmux 腳本更實際：

```bash
#!/bin/bash
# start_fuzzing.sh

TARGET="./target"
TARGET_CMPLOG="./target_cmplog"
SEEDS="./seeds"
OUTPUT="./out"
NCORES=$(nproc)

# 清理舊的 session
tmux kill-session -t fuzzing 2>/dev/null

# 建立新 session，第一個 window 跑 fuzzer1
tmux new-session -d -s fuzzing -n "fuzzer1" \
    "afl-fuzz -S fuzzer1 -p explore -i $SEEDS -o $OUTPUT -- $TARGET @@; bash"

# 加入 CmpLog instance
tmux new-window -t fuzzing -n "cmplog" \
    "afl-fuzz -S cmplog -c $TARGET_CMPLOG -p fast -i $SEEDS -o $OUTPUT -- $TARGET @@; bash"

# 加入其餘 secondary
SCHEDULES=("fast" "exploit" "rare" "mmopt" "seek")
for i in $(seq 3 $NCORES); do
    SCHED=${SCHEDULES[$((i % ${#SCHEDULES[@]}))]}
    tmux new-window -t fuzzing -n "fuzzer${i}" \
        "afl-fuzz -S fuzzer${i} -p $SCHED -i $SEEDS -o $OUTPUT -- $TARGET @@; bash"
done

# 最後一個 window 跑 afl-whatsup 監控
tmux new-window -t fuzzing -n "status" \
    "watch -n 30 'afl-whatsup -s $OUTPUT'"

tmux attach-session -t fuzzing
```

---

## 底層機制：Coverage 如何決定 Sync 的 Seed

```
fuzzer2 的 queue 有 id:000005（某個新 input）
fuzzer1 在 sync 時評估這個 seed：

  1. fork() target，餵入 id:000005
  2. 收集 coverage bitmap（64KB 的 bit array）
  3. 把這個 bitmap 和 fuzzer1 的 global coverage bitmap 做比較：

     fuzzer1 的 bitmap：
     [bit 100: 1] [bit 200: 1] [bit 350: 0] [bit 512: 1] ...

     id:000005 的 bitmap：
     [bit 100: 1] [bit 200: 0] [bit 350: 1] [bit 512: 1] ...
                                      ↑
                              這個 bit 在 fuzzer1 是 0！

  4. 有新的 coverage bit → 這個 seed 對 fuzzer1 有價值 → 複製進 fuzzer1/queue/
  5. fuzzer1 的 global bitmap OR 進去 id:000005 的 bitmap

  如果沒有任何新的 bit → 這個 seed 對 fuzzer1 沒有增量價值 → 丟棄
```

這個機制確保 sync 不會無限膨脹 queue。即使 fuzzer2 發現了 1000 個 seed，fuzzer1 只引進其中真正能拓展它自己覆蓋率的那些。

---

## 監控：`afl-whatsup`

`afl-whatsup` 聚合所有 instance 的狀態：

```bash
afl-whatsup out/

# 輸出範例：
# ┌─ Summary ────────────────────────────────────────────────────────┐
# │ Instance     Execs     Speed  Coverage  Crashes  Hangs  Uptime  │
# │ fuzzer1     1234567   8234/s    4521     12        3     2h 30m  │
# │ fuzzer2     1187234   7891/s    4312     10        2     2h 29m  │
# │ cmplog       456789   3021/s    4123      8        1     2h 28m  │
# │ ...                                                              │
# └──────────────────────────────────────────────────────────────────┘
# Total:        9876543  57234/s  Coverage union: 5123 paths
#                                 ↑ 這是所有 instance 覆蓋的 union
```

**重要**：`Coverage union`（或叫 `unique paths union`）不是每個 instance 的覆蓋率加總，而是**聯集**。一條 path 被任何一個 instance 走到，就算在 union 裡。這才是整個 campaign 真正的覆蓋率。

```bash
# 只顯示摘要（不顯示每個 instance 的詳情）
afl-whatsup -s out/

# 設定 watch，每 60 秒更新
watch -n 60 'afl-whatsup -s out/'
```

---

## 對比與取捨

### 線性擴展？不是。

| 核心數 | 相對速度（exec/s） | 相對覆蓋率 | 說明 |
|--------|-------------------|------------|------|
| 1 core | 1x | 1x | 基準線 |
| 4 cores | ~3.8x | ~1.3x | 速度幾乎線性，覆蓋率不線性 |
| 8 cores | ~7.5x | ~1.5x | 多樣性增益開始飽和 |
| 16 cores | ~14x | ~1.6x | 速度線性，但覆蓋率增益很小 |
| 32 cores | ~28x | ~1.65x | 覆蓋率幾乎不再提升 |

**速度幾乎線性**（各 instance 之間的 overhead 很小），但**覆蓋率不線性**（因為每個新 instance 發現的新路徑和現有 instance 的重疊越來越多）。

### Master/Secondary vs All-Secondary

| 策略 | 優點 | 缺點 | 適用場景 |
|------|------|------|----------|
| 1 Master + N Secondary（舊式） | Deterministic stage 確保系統性覆蓋 | Master 很慢（deterministic 耗時），拉低整體 throughput | AFL 時代的選擇，現在不推薦 |
| All Secondary（AFL++ 4.x 推薦） | 全部 instance 速度相同，多樣性最大 | 少了 deterministic 的系統性 | 現代 AFL++ 的最佳實踐 |

---

## 踩雷集錦

1. **所有 instance 的 `-o out/` 必須相同目錄**：如果 fuzzer1 用 `-o out_1/`，fuzzer2 用 `-o out_2/`，它們永遠看不到對方的 seed，sync 機制失效。整個 parallel fuzzing 的前提就是共用一個 output 根目錄。

2. **核心數超過 target 速度上限後收益遞減**：如果你的 target 只有 100 exec/sec（例如一個很慢的 PDF parser），開 32 個 instance 只是讓 32 個 process 在排隊等 I/O 或 CPU，實際效益和 8 個 instance 差不多。先測單個 instance 的速度，再決定開幾個。

3. **不同 instance 用不同 power schedule 才有意義**：如果 8 個 instance 全部用 `-p fast`，多樣性只來自隨機種子的差異，不夠。混合 `explore`、`fast`、`rare`、`exploit` 才能讓不同 instance 在 queue 遍歷上有真正的差異。

4. **`afl-whatsup` 的 coverage 統計是 union**：看到某個 instance 的 coverage 數字低，不代表它沒有貢獻——它可能專門在探索其他 instance 沒走到的 path，在 union 裡有很大貢獻，只是單獨看數字小。用 union 評估整體進度，用個別數字評估某個 instance 是否卡死。

5. **`-S` 的名字不能重複**：如果你誤把兩個 instance 都命名為 `fuzzer1`，第二個啟動時會讀到第一個留下的 queue 狀態，行為不可預測。確保每個 instance 的名字唯一。

---

## 進階：再往深一層

### 跨機器 Fuzzing

AFL++ 的 sync 機制假設所有 instance 能看到同一個 filesystem 下的 `out/` 目錄。跨機器 fuzzing 需要額外的同步基礎設施：

**方法 1：NFS 掛載**
```bash
# 在 NFS server 上建立 out/ 目錄
# 每台機器掛載同一個 NFS share
mount -t nfs server:/shared/out /local/out
afl-fuzz -S fuzzer_machine2 -i seeds/ -o /local/out/ -- ./target @@
```
NFS 的網路延遲可能影響 sync 效率，適合局域網。

**方法 2：定期 rsync**
```bash
# 在各機器之間定期同步 queue
# 這是 AFL++ 文件推薦的「distributed fuzzing」方式
while true; do
    rsync -avz machine2:/fuzzing/out/ /local/out/
    rsync -avz /local/out/ machine2:/fuzzing/out/
    sleep 60
done
```

**方法 3：Disfuzz / clusterfuzz**：有專門的 distributed fuzzing 框架，但設置成本高。

### `AFL_SHUFFLE_QUEUE=1`

```bash
AFL_SHUFFLE_QUEUE=1 afl-fuzz -S fuzzer2 -i seeds/ -o out/ -- ./target @@
```

讓這個 instance 在開始時隨機打亂 queue 的遍歷順序，和其他 instance 有不同的「起步點」。當 seeds/ 裡有大量初始 seed 時特別有用，確保各 instance 不會全部從同樣的 seed 開始。

### 加入 CmpLog Instance

CmpLog（Compare Log）記錄程式中所有的比較指令（`cmp`、`strcmp`、`memcmp` 等）的操作數，讓 AFL++ 的 Input-to-State（I2S）技術能把比較的值反向注入到 input 裡，突破 magic byte 比較。

```bash
# 需要兩個二進位：普通版和 CmpLog 版
afl-clang-fast -o target_normal target.c
afl-clang-fast -o target_cmplog target.c  # CmpLog 用同樣命令，AFL++ 會自動偵測

# 啟動 CmpLog instance
afl-fuzz -S cmplog -c ./target_cmplog -p fast -i seeds/ -o out/ -- ./target_normal @@
#                    ↑ -c 指定 CmpLog binary
```

CmpLog 的速度約是普通 instance 的 40-60%（因為要記錄所有比較指令的值），通常在整個 campaign 裡配 1-2 個即可。

### Screen / tmux 的選擇

- **tmux**：可以 detach/attach，適合長時間跑（24 小時+）；每個 pane 可以看到 AFL++ 的 UI
- **screen**：類似 tmux，較老的工具
- **nohup / disown**：如果不需要看 UI，最簡單；`nohup afl-fuzz ... > /dev/null 2>&1 &`

長期 campaign 強烈建議用 tmux，因為 AFL++ 的 UI 本身就是重要的診斷工具（可以看到 execution speed 掉了、pending favored 增加了等訊號）。

---

## 動手練習

1. **8 核心 Campaign Setup**：
   - 找一個中等複雜度的 target（`libpng`、`libjpeg` 或任何有 corpus 的目標）
   - 按照本章的設定跑 8 個 instance，混合不同 `-p`
   - 跑 2 小時後用 `afl-whatsup -s out/` 查看狀態
   - 確認各 instance 的 `.synced/` 目錄有在更新（sync 在發生）

2. **Sync 機制觀察**：
   - 跑 2 個 instance（fuzzer1 和 fuzzer2）
   - 在 fuzzer1 的 queue 裡放入一個人工建立的 seed（讓它能觸發 fuzzer2 沒走過的 path）
   - 觀察 fuzzer2 的 `.synced/fuzzer1` 目錄，等待 sync 發生
   - 確認 fuzzer2/queue 裡出現了從 fuzzer1 來的 seed

3. **Power Schedule 對比**：
   - 同一個 target，開 2 個 instance：一個 `-p explore`，一個 `-p fast`
   - 跑 1 小時後比較兩者的 unique paths 和 execution speed
   - 思考在什麼 target 上 `explore` 比 `fast` 更有價值

---

## 本章重點整理

- AFL++ parallel fuzzing 讓多個 instance 共用一個 `out/` 目錄，透過定期掃描其他 instance 的 queue 並做 coverage 比較，只引進能增加自己覆蓋率的 seed（`.synced/` 目錄記錄 sync 進度），實現懶惰式 corpus 同步
- AFL++ 4.x 建議全部使用 `-S`（secondary），不再推薦 `-M`（master）；不同 instance 混合不同 power schedule（`-p`）才能最大化多樣性，而非全部用相同策略
- 多核 fuzzing 的速度提升接近線性，但覆蓋率提升是次線性的（8 cores 比 1 core 約多 50% coverage，不是 8 倍）；`afl-whatsup` 顯示的是各 instance 覆蓋率的聯集，是整個 campaign 真正進度的指標

## 自我檢核

1. AFL++ 的 corpus sync 是即時的（real-time）還是懶惰的（lazy）？sync 的觸發條件是什麼？
2. `.synced/` 目錄的作用是什麼？如果刪掉它會發生什麼？
3. 為什麼 AFL++ 4.x 建議全部用 `-S` 而不是 `-M` + `-S`？
4. 8 個 instance 的覆蓋率為什麼不是 1 個 instance 的 8 倍？
5. `afl-whatsup` 顯示某個 instance 的 coverage 只有其他 instance 的一半，這是否意味著這個 instance 沒有在貢獻？

## 延伸閱讀

- **AFL++ `docs/fuzzing_in_depth.md`**（https://github.com/AFLplusplus/AFLplusplus/blob/stable/docs/fuzzing_in_depth.md）：核心貢獻：AFL++ 官方的 parallel fuzzing 完整指南，包含 CmpLog、Sanitizer、power schedule 的組合建議；讀 "Parallel fuzzing" 那一節；是本章的直接操作參考。

- **"CollabFuzz: A Framework for Collaborative Fuzzing"（Osterlund et al., NDSS 2021）**：核心貢獻：分析多個 fuzzer 協作的設計空間，實驗量化了不同 sync 策略對覆蓋率的影響；讀第 3 節（collaboration strategies）和第 5 節（evaluation）；和本章的 sync 機制底層原理直接對應，提供理論依據。

- **"Evaluating Fuzz Testing"（Klees et al., CCS 2018）**：核心貢獻：提出 fuzzer 評估的方法論，包括統計顯著性問題、如何正確量化多 instance 的 coverage gain；讀第 4 節（evaluation pitfalls）；提醒你在解讀 `afl-whatsup` 的數字時要避免的常見錯誤。

→ [下一章：Ch 21 — Corpus 管理與 Minimization](21-corpus-management.md)
