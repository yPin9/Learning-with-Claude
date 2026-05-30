# Ch 12 — Power Schedule：能量分配的藝術

> **目標**：理解 power schedule 如何決定哪個 seed 該多試一點，以及 AFL++、AFLFast、MOpt 三種 schedule 的設計哲學和差異。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼需要這個？

假設你的 corpus 有 100 個 seed。每輪 fuzzing loop，AFL++ 從這 100 個 seed 裡選一個，決定對它做多少次 mutation。問題來了：**這 100 個 seed 各自應該分到多少次 mutation 機會？**

這不是一個小問題。如果所有 seed 得到相同機會，那一個執行只要 1ms 的 seed 和一個執行要 100ms 的 seed 得到相同的 CPU 配額——但前者讓你在同樣時間裡做 100 倍的 mutation。如果你給執行慢的 seed 更少機會，那那個慢 seed 背後的程式路徑永遠不會被深挖。

這是 fuzzing 的核心資源分配問題。Power schedule（能量排程）是 AFL++ 對這個問題的回答。

## 先建立直覺

把 fuzzing 想成礦工挖礦：

- **每個 seed = 礦坑入口**
- **Mutation = 往礦坑裡挖**
- **Bug = 礦石**
- **Power schedule = 決定哪個礦坑該多挖一點**

原版 AFL 的問題：它的礦工平等對待所有入口，結果 80% 的時間挖那些人人都知道的淺礦坑（high-frequency paths），那些沒人去的深礦坑（rare paths）被忽視。

AFLFast 的觀察：**你已經挖過很多次的礦坑，不太可能突然找到新礦石。應該把更多人力投入很少被挖到的礦坑。**

MOpt 的觀察：**不同的礦坑適合不同的挖礦工具。讓礦工自己學習哪種工具在哪個礦坑最有效。**

## 核心概念一：AFL 原始的 `calculate_score()`

原版 AFL 計算每個 seed 的 `perf_score`，用這個分數決定 havoc stage 的 iteration 次數上限：

```c
// src/afl-fuzz-queue.c（簡化，展示計算邏輯）
u32 calculate_score(afl_state_t *afl, struct queue_entry *q) {

  u32 avg_exec_us = afl->total_cal_us / afl->total_cal_cycles;
  u32 avg_bitmap_size = afl->total_bitmap_size / afl->total_bitmap_entries;
  u32 perf_score = 100;  // 基準分數是 100

  // ① 根據執行時間調整：執行更快的 seed 得到更高分
  if (q->exec_us * 0.1 > avg_exec_us) {
    perf_score = 10;           // 比平均慢 10 倍以上 → 只有 10 分
  } else if (q->exec_us * 0.25 > avg_exec_us) {
    perf_score = 25;
  } else if (q->exec_us * 0.5 > avg_exec_us) {
    perf_score = 50;
  } else if (q->exec_us * 0.75 > avg_exec_us) {
    perf_score = 75;
  } else if (q->exec_us * 4 < avg_exec_us) {
    perf_score = 300;          // 比平均快 4 倍以上 → 300 分
  } else if (q->exec_us * 2 < avg_exec_us) {
    perf_score = 200;
  } else if (q->exec_us * 1.33 < avg_exec_us) {
    perf_score = 150;
  }

  // ② 根據 bitmap coverage 調整：覆蓋更多 edge 的 seed 得到更高分
  if (q->bitmap_size * 0.3 > avg_bitmap_size) {
    perf_score *= 3;           // 覆蓋遠超平均 → 分數 ×3
  } else if (q->bitmap_size * 0.5 > avg_bitmap_size) {
    perf_score *= 2;
  } else if (q->bitmap_size * 0.75 > avg_bitmap_size) {
    perf_score *= 1.5;
  } else if (q->bitmap_size * 3 < avg_bitmap_size) {
    perf_score *= 0.25;        // 覆蓋遠低於平均 → 分數 /4
  } else if (q->bitmap_size * 2 < avg_bitmap_size) {
    perf_score *= 0.5;
  } else if (q->bitmap_size * 1.33 < avg_bitmap_size) {
    perf_score *= 0.75;
  }

  // ③ 根據 handicap 調整：最近加入的 seed 暫時打折
  if (q->handicap >= 4) {
    perf_score *= 4;           // handicap 高的 seed（最近加入的）得到 bonus
    q->handicap -= 4;
  } else if (q->handicap) {
    perf_score *= 2;
    q->handicap--;
  }

  // ④ 根據 depth 調整：深度更高的 seed（更多代 mutation 的產物）得到 bonus
  switch (q->depth) {
    case 0 ... 3:   break;
    case 4 ... 7:   perf_score *= 2; break;
    case 8 ... 13:  perf_score *= 3; break;
    case 14 ... 25: perf_score *= 4; break;
    default:        perf_score *= 5; break;
  }

  // 上限：最多是 base 的 havoc_max_mult 倍（預設 256）
  if (perf_score > HAVOC_MAX_MULT * 100) {
    perf_score = HAVOC_MAX_MULT * 100;
  }

  return perf_score;
}
```

這個 `perf_score` 決定 havoc stage 跑多少次：`stage_max = perf_score * HAVOC_CYCLES / 100`。Score 200 的 seed 跑的 havoc 次數是 Score 100 的 seed 的兩倍。

**設計邏輯**：快的 seed 讓 fuzzer 在單位時間裡跑更多 mutation，理應得到更多機會。覆蓋更多 edge 的 seed 代表它觸及程式更多角落，值得深挖。新加入的 seed（`handicap` 高）代表 fuzzer 剛剛發現新路徑，應該趁熱多跑幾輪。

**問題**：這個設計沒有考慮「稀有程度」——被頻繁執行到的路徑和從未被執行到的路徑得到相同的 score（在 exec time 和 bitmap size 相同的前提下）。

## 底層機制：它是怎麼運作的？

```
                 ┌─────────────────────────┐
                 │       Seed Queue        │
                 │  seed_1, seed_2, ...,   │
                 │  seed_N                 │
                 └───────────┬─────────────┘
                             │ 選取下一個 seed（按 queue 順序）
                             ▼
                 ┌─────────────────────────┐
                 │   calculate_score()     │
                 │                         │
                 │  考慮：                  │
                 │  ① exec_us（和平均比）   │
                 │  ② bitmap_size（和平均比）│
                 │  ③ handicap（最近加入的） │
                 │  ④ depth（mutation 深度）│
                 │                         │
                 │  + 若有 AFLFast schedule │
                 │    考慮 ⑤ path frequency│
                 │    （命中次數的倒數）     │
                 │                         │
                 │  → 輸出 perf_score      │
                 └───────────┬─────────────┘
                             │
                             ▼
                 ┌─────────────────────────┐
                 │   havoc stage           │
                 │                         │
                 │  stage_max =            │
                 │    perf_score *         │
                 │    HAVOC_CYCLES / 100   │
                 │                         │
                 │  perf_score = 100 →     │
                 │    跑 256 次 havoc      │
                 │  perf_score = 400 →     │
                 │    跑 1024 次 havoc     │
                 └───────────┬─────────────┘
                             │
                             ▼
                 ┌─────────────────────────┐
                 │  mutation → execution   │
                 │  → has_new_bits()?      │
                 │    是 → 加入 queue      │
                 │    否 → 繼續            │
                 └─────────────────────────┘
```

## 核心概念二：AFLFast 的改進（CCS 2016）

Böhme、Pham、Roychoudhury 在 CCS 2016 發表 AFLFast，用數學模型分析了 AFL 的問題。

### 問題的形式化

AFLFast 把 fuzzing 建模為 **Markov chain（馬可夫鏈）**：

- **狀態（state）**：程式執行路徑（path）
- **轉移（transition）**：對一個 input 做 mutation，產生執行不同路徑的 input
- **目標**：讓 fuzzer 盡快探索到所有狀態

觀察一：AFL 的執行次數分佈高度不均——少數「高頻路徑」佔了 80% 以上的執行次數，大多數路徑被執行不超過幾次。

觀察二：對一個已被執行 1000 次的路徑再做 mutation，發現新路徑的邊際收益遠低於對一個只被執行 5 次的路徑做 mutation。

### 解法：Inverse Frequency Weighting

AFLFast 的核心想法：**給執行次數少（low-frequency）的路徑更多能量**。

```c
// AFLFast 的 score 計算（概念，不是 AFL++ 的實際程式碼）
// 對於路徑 p，它的 energy 正比於 1 / f(p)
// f(p) = 這個路徑在 fuzzing 歷史中被執行的次數

energy(p) = base_energy / hit_count(p);
```

這樣，一個從未被執行過的路徑得到的能量是一個已被執行 100 次的路徑的 100 倍。

### 六種 AFLFast Schedule

AFLFast 提出了六種 schedule，AFL++ 全部實作：

```c
// include/afl-fuzz.h（AFL++ 的 enum 定義）
typedef enum {
  FAST = 0,    // 預設（AFLFast 原始提案：指數衰減）
  COE,         // Cut-Off Exponential：超過平均 hit count 的 seed 被 capped
  EXPLORE,     // 均勻探索：對所有 seed 公平（AFL++ 推薦預設）
  LIN,         // 線性：能量和 hit count 成線性反比
  QUAD,        // 二次方：能量和 hit count^2 成反比（更激進地偏向 rare path）
  RARE,        // 對 rare edge 的 seed 額外加分
  MMOPT,       // MOpt（見下節）
  SEEK,        // 類似 EXPLORE 但有額外調整
} schedule_t;
```

**各 schedule 的數學定義**（簡化）：

| Schedule | 能量公式 | 特性 |
|---------|---------|------|
| `fast` | `2^α × base`（指數，α 隨 hit count 遞減） | 對新 seed 激進地多給能量，衰減很快 |
| `coe` | `2^α × base`，但 hit count > 平均時 cap 到最小值 | 強制避免高頻 seed 消耗太多資源 |
| `explore` | 接近均勻，但有溫和的 exec time/bitmap 調整 | 穩健，適合未知 target |
| `lin` | `base / hit_count` | 線性衰減，溫和 |
| `quad` | `base / hit_count^2` | 二次衰減，更積極偏向 rare path |
| `rare` | 對觸及 rare edge（全局命中次數低的 edge）的 seed 額外加分 | 專注於尚未被充分探索的邊 |

**AFL++ 4.x 為什麼推薦 `explore` 而非 `fast`**：

`fast` 在 AFLFast 的原始 benchmark 上表現很好，但它的指數衰減在某些 target 上過於激進——新 seed 得到過多能量，導致 fuzzer 還沒充分探索就跑去追逐新路徑。`explore` 的均勻策略在更廣泛的 target 上更穩健，不容易出現「過度偏向新 seed」的問題。

### `calculate_score()` 在 AFLFast Schedules 下的變化

AFL++ 的 `calculate_score()` 在 EXPLORE 以外的 schedule 下，會在基礎 perf_score 的基礎上疊加 path frequency 調整：

```c
// src/afl-fuzz-queue.c（簡化）
// 在 calculate_score() 的末尾，針對不同 schedule 做額外調整

switch (afl->schedule) {
  case EXPLORE:
    // 不做額外調整，保持基礎 perf_score
    break;

  case FAST:
    // 指數加權：(1 / (hit_count^beta)) × base
    // beta 隨時間調整
    if (q->n_fuzz_entry) {
      factor = MAX(1, 1 << (29 - __builtin_clz(q->n_fuzz_entry)));
      // n_fuzz_entry 愈大（被 fuzz 愈多次），factor 愈小
      perf_score *= factor;
    }
    break;

  case LIN:
    perf_score *= afl->queue_cycle / (q->n_fuzz_entry / afl->queue_size + 1);
    break;

  case QUAD:
    perf_score *= afl->queue_cycle * afl->queue_cycle /
                  (q->n_fuzz_entry * q->n_fuzz_entry / afl->queue_size + 1);
    break;

  case RARE:
    // 計算這個 seed 的最稀有 edge 的命中次數
    // 對命中次數最低的 edge 的 seed 額外加分
    // ...
    break;
}
```

`n_fuzz_entry`：這個 seed 被 fuzz 過幾次（不是 havoc 的 iteration 數，而是這個 seed 被選中做 fuzz 的次數）。這是 path frequency 的代理指標。

## 核心概念三：MOpt（USENIX Security 2019）

MOpt 解決的是一個不同層次的問題：**不是「哪個 seed 該多跑」，而是「havoc 的 20 種操作裡，哪種操作最有效，應該多用」**。

### 問題背景

原版 AFL 的 havoc 對 20 種操作均等選取。但不同的 target 對不同操作的「敏感度」不同：

- 解析 HTTP header 的 target：在 header 邊界插入/刪除 bytes 效果好
- 解析二進位格式的 target：arithmetic 操作（改數值）比 bit flip 效果好
- 有大量整數比較的 target：interesting values 替換特別有效

固定的均等比例無法適應這種 target-specific 的特性。

### Particle Swarm Optimization（粒子群最佳化）

MOpt 用 PSO（粒子群最佳化）動態學習每種 mutation 操作的最佳使用比例。

PSO 的直覺：

```
想像有 20 隻「粒子」，每隻代表一種 mutation 操作
每隻粒子在「比例空間」裡移動：比例從 0% 到 100%
粒子有「速度」：每輪根據是否找到新 coverage 調整速度
                                  
        目標：找到讓「新 coverage 發現率」最高的比例分配

粒子 1（bit flip）:    目前比例 = 8%,  速度 = -0.2%
粒子 2（byte replace）: 目前比例 = 12%, 速度 = +0.5%
...
粒子 20（dict insert）: 目前比例 = 3%,  速度 = +0.1%

每次 havoc 結束後：
  - 更新每隻粒子的「適應度」= 這種操作最近找到的新 coverage 數
  - 根據適應度調整速度（往更好的比例移動）
  - 更新比例
```

PSO 的正式更新規則：

```
v_i(t+1) = w × v_i(t) + c1 × r1 × (pbest_i - x_i(t)) + c2 × r2 × (gbest - x_i(t))

其中：
  v_i = 操作 i 的「速度」（比例的變化量）
  x_i = 操作 i 目前的比例
  pbest_i = 操作 i 歷史上最好的比例（個人最佳）
  gbest = 所有操作裡全局最好的比例（全局最佳）
  w, c1, c2, r1, r2 = 超參數和隨機數
```

### MOpt 的兩個 Pool

MOpt 在 AFL++ 裡維護兩個 pool：

1. **Pilot pool（試飛池）**：PSO 正在探索不同的比例配置，尋找更好的分配
2. **Core pool（核心池）**：已知較好的比例配置，穩定地使用

AFL++ 周期性地在兩個 pool 之間切換：在 pilot pool 裡學習，在 core pool 裡利用已學到的知識。

### MOpt 的啟用方式

```bash
# 使用 MOpt power schedule
afl-fuzz -p mmopt -i seeds/ -o out/ -- ./target @@
```

MOpt 主要調整 havoc 操作的比例，`-p mmopt` 同時隱含了一些 afLFast 式的 seed 選擇調整。

## 進一步用法：選擇適合你的 Schedule

### 實驗對比

```bash
# 跑四個平行實例，各用不同 schedule
afl-fuzz -p fast    -i seeds/ -o out/ -S fast_fuzzer    -- ./target @@
afl-fuzz -p explore -i seeds/ -o out/ -S explore_fuzzer -- ./target @@
afl-fuzz -p fast    -i seeds/ -o out/ -S mopt_fuzzer -p mmopt -- ./target @@
afl-fuzz -p rare    -i seeds/ -o out/ -S rare_fuzzer    -- ./target @@

# 跑 24 小時後用 afl-whatsup 比較
afl-whatsup out/
```

### schedule 選擇指南

如果 target 是全新的、你不知道哪種 schedule 好：從 `explore` 開始，因為它最穩健。

如果 target 有大量 rare path（例如 error handling、罕見 protocol state）：嘗試 `fast` 或 `rare`，它們更積極地偏向未被探索的路徑。

如果你跑了幾小時 `explore` 後 coverage 成長停滯：換 `mmopt`，讓 PSO 學習哪些 mutation 操作對這個 target 有效。

## 對比與取捨

| 面向 | AFL 原始 schedule | AFLFast-fast | AFLFast-explore | MOpt（mmopt） |
|------|-----------------|--------------|-----------------|--------------|
| 設計目標 | exec time + bitmap size 平衡 | 偏向 rare path | 均勻探索 | 學習最優 mutation 比例 |
| Seed 選擇偏向 | 快且覆蓋廣的 seed | low-frequency path 的 seed | 均勻 | 均勻（但 mutation 比例動態） |
| Overhead | 低 | 低（多一次 hit count 查詢） | 低 | 中（PSO 計算，每 K 次執行後更新一次） |
| 適合短 session（< 1 小時） | 好 | 好 | 最好 | 差（PSO 尚未收斂） |
| 適合長 session（> 24 小時） | 可 | 好 | 好 | 最好（PSO 收斂後效果顯著） |
| 適合未知 target | 可 | 可 | 最好 | 可 |
| 適合已知有 rare bug 的 target | 差 | 好 | 可 | 好 |
| 來源論文 | AFL（lcamtuf, 2014） | AFLFast（CCS 2016） | AFLFast + AFL++ 調整 | MOpt（USENIX Security 2019） |

## 踩雷集錦

**1. 「explore 一定比 fast 好」**

AFL++ 推薦 `explore` 作為預設，但這是「針對未知 target 的保守選擇」，不是「在所有場景都更好」。在某些 target 上（尤其是有大量複雜 rare paths 的協議解析器），`fast` 或 `rare` 找 bug 的速度更快。沒有 silver bullet，跑實驗才能確定。

**2. `AFL_FAST_CAL=1` 讓 calibration 不準確**

`AFL_FAST_CAL=1` 把 calibration rounds 從 8 次減少到 1-2 次，換取速度。代價是 `exec_us` 的估計更不穩定，`calculate_score()` 的結果可能不準。在 perf_score 計算很重要的場景（大 corpus，期待 schedule 發揮作用），不要用這個 flag。

**3. MOpt 在短 session 下效果不如預期**

PSO 需要足夠的執行次數才能收斂到較好的比例配置。AFL++ 官方文件建議 MOpt session 至少跑 1-2 小時以上。如果你在 15 分鐘後看到 MOpt 和 explore 效果差不多，不代表 MOpt 沒用——PSO 還沒收斂。

**4. 混淆「seed 選擇的 schedule」和「mutation 操作比例」**

`-p fast` 和 `-p explore` 等 schedule 主要影響「哪個 seed 分到多少能量（havoc iteration 次數）」。`-p mmopt` 在此基礎上額外調整「havoc 裡哪種操作被更頻繁使用」。這是兩個不同層次的優化，不要把它們搞混。

**5. 忽略 `depth` 在 score 計算裡的作用**

`calculate_score()` 的 `depth` 因子給「更深代的 mutation 產物」更高的能量。這是 AFL++ 的意圖之一：如果一個 seed 是已經 7 代 mutation 的產物，代表 fuzzer 在這條路徑上持續找到新 coverage，值得繼續深挖。沒有理由干預這個機制，但理解它有助於解讀為什麼某些 seed 的 havoc 次數特別多。

## 進階：再往深一層

### AFLFast 的 Markov Chain 分析的數學結構

AFLFast 把 fuzzing 形式化為一個有限狀態馬可夫鏈，其中：

- 狀態 `s` = 程式執行路徑（path hash）
- 轉移矩陣 `P` 中，`P[s][s']` = 從路徑 `s` 的 seed 做 mutation 轉移到路徑 `s'` 的概率
- **目標**：最大化在時間 `T` 內訪問到的新狀態數量

在這個模型下，最優能量分配的解析解正比於每個狀態的「被訪問次數的倒數」——這就是 inverse frequency weighting 的數學基礎。AFLFast paper 的 Section 3 推導了這個結果，如果你想理解為什麼這個 schedule 在理論上是合理的，值得讀。

### MOpt 的 PSO 收斂性

MOpt 的 PSO 有一個已知問題：在特定 target 上，PSO 可能收斂到次優解（local optimum），導致某些有效的 mutation 操作比例被壓制到幾乎不被使用。AFL++ 對此的緩解措施是定期重置 pilot pool（讓粒子重新探索），但無法完全解決。

實際觀察：如果 MOpt 跑了幾小時後，某種 mutation 操作的比例降到接近 0%，但你直覺上認為這種操作應該有效，可以嘗試用 `AFL_MOPT_DEBUG=1` 觀察 PSO 的狀態，再決定是否重啟 session。

### Multi-instance 下的 Schedule 策略

在 parallel fuzzing 設定（多個 `afl-fuzz` 實例同步 corpus）下，一個常見的策略是：

- 主實例（`-M`）：`-p explore`，保證廣度
- 次要實例（`-S`）：各用不同 schedule（`fast`、`rare`、`mmopt`）

這樣各實例之間有 schedule 多樣性，但 corpus 透過 sync 共享，每個實例找到的新 seed 都能讓其他實例受益。

```bash
# 4 核心的典型設定
afl-fuzz -M main    -p explore -i seeds/ -o out/ -- ./target @@
afl-fuzz -S fast_1  -p fast    -i seeds/ -o out/ -- ./target @@
afl-fuzz -S rare_1  -p rare    -i seeds/ -o out/ -- ./target @@
afl-fuzz -S mopt_1  -p mmopt   -i seeds/ -o out/ -- ./target @@
```

## 動手練習

1. **觀察 `calculate_score()` 的輸出**：

   ```bash
   # 在 fuzzer 跑起來後，讀取 queue 裡每個 seed 的 perf_score
   # （AFL++ 會把部分統計輸出到 out/default/fuzzer_stats）
   cat out/default/fuzzer_stats | grep -E "execs|bitmap|cur_path"

   # 用 afl-whatsup 看各個 queue entry 的狀態
   afl-whatsup out/
   ```

2. **比較兩種 schedule 的 coverage 成長曲線**：

   ```bash
   # 跑兩個 instance，各用不同 schedule，各 2 小時
   afl-fuzz -p fast    -i seeds/ -o out_fast/    -- ./target @@ &
   afl-fuzz -p explore -i seeds/ -o out_explore/ -- ./target @@ &

   # 2 小時後比較
   cat out_fast/default/fuzzer_stats | grep paths_found
   cat out_explore/default/fuzzer_stats | grep paths_found
   ```

3. **觀察 MOpt 的 PSO 學習過程**：

   ```bash
   # 啟用 MOpt debug 輸出
   AFL_MOPT_DEBUG=1 afl-fuzz -p mmopt -i seeds/ -o out_mopt/ -- ./target @@

   # 觀察每種 mutation 操作的比例如何隨時間變化
   # （輸出會在 stderr，可以重導向到檔案）
   AFL_MOPT_DEBUG=1 afl-fuzz -p mmopt -i seeds/ -o out_mopt/ -- ./target @@ 2> mopt_log.txt
   grep "operator" mopt_log.txt | tail -20
   ```

4. **分析 `n_fuzz_entry` 的分佈**：

   ```bash
   # 跑 1 小時後，用 Python 分析 queue 裡每個 seed 的 fuzz 次數分佈
   # （需要解析 out/default/queue/ 裡的 seed 檔名，AFL++ 把部分元資料編碼在檔名裡）
   python3 -c "
   import os, re
   queue_dir = 'out/default/queue/'
   for f in sorted(os.listdir(queue_dir)):
       if f.startswith('id:'):
           print(f)
   " | head -20
   ```

## 本章重點整理

- `calculate_score()` 用 exec time、bitmap size、handicap、depth 四個因子計算 `perf_score`，決定 havoc stage 的 iteration 次數；快且覆蓋廣的 seed 得到更多機會
- AFLFast 把 fuzzing 建模為 Markov chain，發現 AFL 過度集中在 high-frequency paths；用 inverse frequency weighting 給 rare path 更多能量；AFL++ 實作了六種 schedule（fast/coe/explore/lin/quad/rare）
- MOpt 用 Particle Swarm Optimization 動態學習 havoc 操作的最佳比例；需要足夠長的 session（> 1 小時）才能收斂；AFL++ 推薦 `explore` 作為保守預設，`mmopt` 適合長 session

## 自我檢核

1. `calculate_score()` 的四個因子（exec time、bitmap size、handicap、depth）各自反映的是哪種直覺？為什麼 handicap 要給「最近加入的 seed」更高分而不是更低分？

2. AFLFast 的 `fast` schedule 用的是什麼數學原理？「對 hit count 低的 seed 給更多能量」的形式化依據是什麼？

3. MOpt 的 PSO 在「pilot pool」和「core pool」各自扮演什麼角色？為什麼需要兩個 pool 而不是一個？

4. 在一個 24 小時的 fuzzing session 中，你應該從 `explore` 開始還是 `mmopt` 開始？請給出理由。

5. 解釋為什麼在 parallel fuzzing（多個 `-S` 實例）的設定下，讓每個實例用不同的 schedule 比讓所有實例用同一個 schedule 更好。

## 延伸閱讀

- **[Coverage-Based Greybox Fuzzing as Markov Chain (AFLFast)](https://dl.acm.org/doi/10.1145/2976749.2978428)** — Böhme, Pham, Roychoudhury, CCS 2016
  - **核心貢獻**：把 AFL fuzzing 建模為馬可夫鏈，推導出 inverse frequency weighting 的理論基礎，定義六種 power schedule
  - **讀哪裡**：Section 3（Markov chain 建模，理解數學框架）和 Section 4（六種 schedule 的定義和差異）。Section 5 的實驗展示了 `fast` 在多個 target 上優於原版 AFL 的幅度
  - **和本章的關聯**：本章描述的 AFLFast schedules 完全來自這篇，AFL++ 的 `calculate_score()` 裡的 path frequency 調整部分直接實作了這篇的演算法

- **[MOPT: Optimize Mutation Scheduling for Fuzzers (MOpt)](https://www.usenix.org/conference/usenixsecurity19/presentation/lyu)** — Lyu, Ji, Zhang, Liang, Jiang, USENIX Security 2019
  - **核心貢獻**：觀察到不同 mutation 操作在不同 target 上效果差異極大，用 PSO 動態學習最佳操作比例
  - **讀哪裡**：Section 3（PSO 設計和 pilot/core pool 的詳細說明）。Section 4 的實驗展示了 MOpt 和固定比例 havoc 的 coverage 差距（在 24 小時 session 下差距最明顯）
  - **和本章的關聯**：本章的 MOpt 說明和 PSO 更新規則直接來自這篇的 Section 3

- **[AFL++ `docs/fuzzing_in_depth.md`](https://github.com/AFLplusplus/AFLplusplus/blob/stable/docs/fuzzing_in_depth.md)** — AFL++ 官方
  - **核心貢獻**：AFL++ 官方的 power schedule 選擇建議，包含各 schedule 的實際使用心得和 parallel fuzzing 下的組合策略
  - **讀哪裡**："Power schedules" 一節，以及 "Parallel fuzzing" 一節中關於不同 instance 用不同 schedule 的建議
  - **和本章的關聯**：本章「schedule 選擇指南」和「multi-instance 策略」的建議來源

→ [Ch 13 — Dictionary 與 Token-Level Mutation](./13-dictionary-tokens.md)
