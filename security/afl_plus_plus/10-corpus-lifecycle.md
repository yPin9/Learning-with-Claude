# Ch 10 — Corpus 生命週期：從 Seed 入隊到 Favored Minset

> **目標**：理解 AFL++ 如何管理 corpus（語料庫），從初始 seed 到 favored minset 的全流程，以及 trim 的作用。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼需要這個？

AFL 在 2013 年引入 corpus 管理的核心洞見：**不是所有 seed 都值得花同等時間**。如果你有 10,000 個 seed，其中 9,500 個都覆蓋完全相同的程式路徑，那 fuzzer 把 95% 的時間花在這 9,500 個 seed 上是純粹的浪費。

原版 AFL 解決這個問題的方式是 favored minset（最小代表集合）：找到最小的 seed 子集，讓它能代表所有已知的 coverage。Corpus 管理不是「收集更多 seed」，而是「用最少的 seed 代表最多的路徑」。

AFL++ 繼承並精化了這個機制。理解 corpus lifecycle，你才能解釋為什麼 `afl-cmin` 之後 fuzzing 效率反而上升，以及為什麼手頭 1000 個 seed 不一定比精心挑選的 10 個好。

## 先建立直覺

把 corpus 想成一個動態的「知識庫」：

- **每個 seed = 一條通往程式某個角落的路線圖**
- **新 seed 的加入條件 = 它帶來了地圖上沒有的新路段**
- **Favored minset = 用最少的路線圖覆蓋所有已知路段**
- **Trim = 把路線圖上的廢話刪掉，只保留能到達那個角落的最短路徑**

這個比喻的重點：如果你已經有一條路線圖能到達所有地方，加入第 1001 條只能到達已知地點的路線圖，對探索新地方毫無幫助。

## 核心概念一：Queue Entry 的結構

AFL++ 在記憶體中維護一個鏈結串列（linked list）形式的 queue，每個 entry 是一個 `struct queue_entry`：

```c
// include/afl-fuzz.h（簡化，實際欄位更多）
struct queue_entry {
  u8 *fname;           // seed 檔案的路徑
  u32 len;             // seed 的位元組長度

  u8  cal_failed;      // calibration 是否失敗
  u8  trim_done;       // 是否已完成 trim
  u8  was_fuzzed;      // 是否已被 fuzz 過至少一次
  u8  passed_det;      // 是否通過了 deterministic stages
  u8  has_new_cov;     // 加入時是否有新 coverage
  u8  var_behavior;    // bitmap 是否有 non-deterministic 行為
  u8  favored;         // 是否在目前的 favored minset 裡
  u8  fs_redundant;    // 是否被判為 fully redundant（覆蓋被其他 seed 完全包含）

  u64 exec_us;         // 平均執行時間（microseconds）
  u64 handicap;        // 新加入時的 handicap（愈新的 seed 初始能量愈低）
  u64 depth;           // 這個 seed 是第幾代 mutation 的產物

  u8 *trace_mini;      // 精簡的 bitmap snapshot（記錄哪些 edge 被觸發）
  u32 tc_ref;          // 被幾個 top_rated[] 槽位引用（favored 計算用）
};
```

關鍵欄位：
- `favored`：這個 seed 是否在 favored minset 裡，影響 scheduling 優先度
- `trace_mini`：這個 seed 執行後的 bitmap 快照（壓縮版），用於 favored 計算
- `exec_us * len`：這個 seed 的「cost」，favored 演算法用它來選最低成本的 seed

## 核心概念二：新 Seed 的加入條件

新 seed 要加入 queue，必須通過 `has_new_bits()` 的檢查：

```c
// src/afl-fuzz-bitmap.c（簡化）
u8 has_new_bits(afl_state_t *afl, u8 *virgin_map) {

  u64 *current = (u64 *)afl->fsrv.trace_bits;  // 這次執行的 bitmap
  u64 *virgin  = (u64 *)virgin_map;             // 全局「未見過」的 bitmap
  u32  i = (afl->fsrv.map_size >> 3);
  u8   ret = 0;

  while (i--) {
    if (unlikely(*current) && unlikely(*current & *virgin)) {
      // 這個 64-bit chunk 有新的 bit
      if (likely(ret < 2)) {
        u8 *cur = (u8 *)current;
        u8 *vir = (u8 *)virgin;
        // 檢查是新 edge 還是只是新 hit count bucket
        for (u32 j = 0; j < 8; j++) {
          if (cur[j] && vir[j] == 0xff) { ret = 2; break; }  // 全新 edge
          if (cur[j] && (cur[j] & vir[j])) ret = 1;           // 新的 hit bucket
        }
      }
      *virgin &= ~*current;  // 更新 virgin map
    }
    current++;
    virgin++;
  }
  return ret;  // 0=沒新的, 1=新 hit count, 2=新 edge
}
```

`has_new_bits()` 回傳非零值就代表「這個執行看到了新東西」。新東西有兩種：

1. **新 edge（回傳 2）**：程式執行了從未執行過的控制流轉移。這是最重要的新發現。
2. **新 hit count bucket（回傳 1）**：某條 edge 的執行次數跨越了 bucket 邊界（1→2, 2→4, 4→8, 8→16, 16→32, 32→128, 128→∞）。這代表循環行為有新的迭代次數。

兩種都值得加入 queue，因為都代表新的程式狀態。

## 核心概念三：Calibration（校準）

新 seed 加入 queue 之前，AFL++ 先跑幾次 calibration：

```bash
# 你在 fuzzer 輸出看到的訊息
[*] Calibrating new testcase (index 42, len 128)...
```

Calibration 的目的：
1. **測量 exec time**：取多次執行的平均值，作為後續 score 計算的基礎
2. **確認 bitmap stability**：多次執行同一 seed，bitmap 應該每次相同。如果不同（variance），代表 target 有 non-determinism。
3. **偵測 variable behavior**：如果 stability < 75%，seed 被標記為 `var_behavior`；如果 < 10%（幾乎完全不穩定），AFL++ 預設會拒絕加入

AFL++ 預設做 8 次 calibration runs，但如果 stability 看起來好，會提前結束。

Stability < 90% 的原因通常是：
- Target 有時間相關的行為（讀 `/proc/pid`、time-based 分支）
- ASAN 的 shadow memory 在某些條件下行為不同
- Multi-threading（AFL++ 無法正確追蹤多執行緒的 non-determinism）

## 底層機制：它是怎麼運作的？

```
初始 Corpus
    │
    │  afl-cmin（可選）：預先過濾掉重複 coverage 的 seed
    │
    ▼
┌─────────────────────────────┐
│       Input Queue           │
│  seed_1, seed_2, ..., seed_N │
└──────────────┬──────────────┘
               │
               ▼ 每個新 seed 進入時
┌─────────────────────────────┐
│         Calibration         │
│  ① 執行 8 次（預設）         │
│  ② 測量 exec_us             │
│  ③ 確認 bitmap stability    │
│  → cal_failed=0 才繼續      │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│     has_new_bits()          │
│  比較執行結果和 virgin_map   │
│  回傳 0 → 丟棄              │
│  回傳 1 or 2 → 加入 queue   │
└──────────────┬──────────────┘
               │
               ▼ 加入 queue 後
┌─────────────────────────────┐
│         Trim                │
│  inline trim（fuzz loop 內） │
│  二分刪減：                  │
│   嘗試刪掉一半 → 執行        │
│   coverage 不變 → 保留刪減   │
│   coverage 改變 → 還原       │
│  → 找到最小的等效 seed       │
└──────────────┬──────────────┘
               │
               ▼ 定期觸發
┌─────────────────────────────┐
│     cull_queue()            │
│  Favored Minset 計算：       │
│  ① 對每個 edge，找 cost 最低  │
│     的 seed（cost = len *    │
│     exec_us）               │
│  ② 這些 seed 標記 favored=1  │
│  ③ 其他 seed favored=0      │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│     Fuzzing Loop            │
│  favored seed：每次都選      │
│  non-favored seed：          │
│   每 10 輪才偶爾選一次        │
└─────────────────────────────┘
```

## 核心概念四：Favored Minset 演算法

`cull_queue()` 在 `src/afl-fuzz-queue.c` 裡實作，邏輯如下：

```c
// src/afl-fuzz-queue.c（簡化，描述演算法邏輯）
void cull_queue(afl_state_t *afl) {

  // top_rated[i]：edge i 目前最「划算」的 seed
  // 每次有新 seed 加入時會更新 top_rated

  u8  *temp_v = afl->clean_trace_custom;  // 臨時 bitmap
  u32  i;
  struct queue_entry *q;

  // 先把所有 seed 標記為 non-favored
  q = afl->queue;
  while (q) { q->favored = 0; q = q->next; }

  // 清空臨時 bitmap（代表「還沒被覆蓋的 edge」）
  memset(temp_v, 255, afl->fsrv.map_size);

  // 貪婪演算法：掃描所有 edge
  for (i = 0; i < afl->fsrv.map_size; i++) {
    if (afl->top_rated[i]) {                    // 這個 edge 有 seed 覆蓋
      u32 j = afl->fsrv.map_size >> 3;
      u8 *trace = afl->top_rated[i]->trace_mini;

      // 如果這個 seed 還能覆蓋「尚未被選到的 edge」
      while (j--) {
        if (temp_v[j] & trace[j]) { goto next_run; }
      }
      // 不需要這個 seed（已有其他 favored seed 覆蓋了它的所有 edge）
      continue;

    next_run:
      afl->top_rated[i]->favored = 1;  // 標記為 favored
      j = afl->fsrv.map_size >> 3;
      // 從 temp_v 中移除這個 seed 覆蓋的 edge（標記為「已覆蓋」）
      while (j--) { temp_v[j] &= ~trace[j]; }
    }
  }

  // 找 fully redundant seed（可以完全被 favored 子集取代）
  q = afl->queue;
  while (q) {
    mark_as_redundant(afl, q, !q->favored);
    q = q->next;
  }
}
```

**`top_rated[]` 如何更新**：每次有新 seed 加入時，AFL++ 會掃描它的 `trace_mini`，對它覆蓋的每個 edge `i`，如果它的 `exec_us * len` 比 `top_rated[i]` 目前的 seed 更低，就更新 `top_rated[i]` 指向這個新 seed。這個「每個 edge 選最便宜的 seed」邏輯是 minset 的核心。

## 進一步用法：afl-cmin 與 afl-tmin

### afl-cmin（Corpus Minimization）

`afl-cmin` 是離線工具，從一大堆 seed 中提取能代表所有 coverage 的最小子集：

```bash
# 有 500 個 seed，想縮減到最小集合
afl-cmin -i big_corpus/ -o min_corpus/ -- ./target @@

# 結果：min_corpus/ 裡只有能代表所有 edge coverage 的最小 seed 集合
# 通常能把 500 個縮減到 20-50 個
```

`afl-cmin` 的演算法和 `cull_queue()` 類似，但它**實際移除**多餘的 seed，而不只是標記 `favored=0`。它適合在正式跑 fuzzing 之前整理 corpus。

### afl-tmin（Testcase Minimization）

`afl-tmin` 是單個 seed 的大小最小化工具：

```bash
# 把一個 500 bytes 的 seed 縮減到最小的等效版本
afl-tmin -i interesting_input -o minimized_input -- ./target @@

# 典型結果：從 500 bytes 縮減到 30-50 bytes，但觸發完全相同的 coverage 或 crash
```

`afl-tmin` 的邏輯是二分刪減：
1. 嘗試把 seed 切成兩半，只保留後半
2. 執行 target，如果 bitmap 相同（或 crash 相同），接受這個刪減
3. 繼續對剩餘部分遞迴做二分刪減
4. 輔以 single-byte 掃描：逐一嘗試把每個 byte 替換成 0x00

這個演算法不保證找到絕對最小的 seed，但通常能把 seed 縮短 50-90%。

**`afl-cmin` 和 `afl-tmin` 的關鍵區別**：

- `afl-cmin`：多個 seed → 最小 seed **集合**（還是多個檔案，但數量少）
- `afl-tmin`：一個 seed → 最小**等效的單個 seed**（一個檔案，但更小）

這是很多人搞混的地方。

### Inline Trim（自動 Trim）

AFL++ 的 fuzz loop 會自動對每個 seed 跑 inline trim，不需要手動呼叫 `afl-tmin`。第一次 fuzz 一個 seed 之前，AFL++ 會在背景做 trim，然後把 trimmed 版本存回 queue。`trim_done` flag 確保同一個 seed 只 trim 一次。

## 對比與取捨

| 面向 | 跑 fuzzing 前 0 分鐘 | 跑 fuzzing 後 1 小時 |
|------|-------------------|--------------------|
| Corpus size（seed 數量） | 你給的 N 個初始 seed | N + M（M 個 fuzzing 發現的新 seed） |
| Favored seed 比例 | 接近 100%（初始 corpus 通常都是 favored） | 通常降到 10-30%（大量新 seed 加入但很多 redundant） |
| 平均 seed 大小 | 原始大小（未 trim） | 縮小（inline trim 已執行） |
| Coverage（edge 數量） | 初始 coverage | 顯著增加（fuzzing 找到新路徑） |
| `top_rated[]` 的 seed | 指向初始 seed | 逐漸更新，指向更短/更快的 seed |

典型數字：一個中等大小的 target（如 libpng），1 小時後 corpus 可能增長到 200-500 個 seed，但 favored 只有 20-50 個。Fuzzer 把 80% 以上的時間花在這 20-50 個 favored seed 上。

## 踩雷集錦

**1. 「Seed 越多越好」**

這是最常見的誤解。1000 個覆蓋相同路徑的 seed 效果不如 10 個精心選擇的 seed。多餘的 seed 增加 queue 掃描時間，稀釋 favored seed 的執行比例，不增加 bug 發現率。正式跑 fuzzing 前先跑 `afl-cmin`。

**2. 把 `afl-cmin` 和 `afl-tmin` 搞混**

`afl-cmin` 是集合最小化（多個 seed → 最小子集），`afl-tmin` 是單個 seed 的大小最小化。對一整個目錄跑 `afl-tmin` 不等於跑 `afl-cmin`——你只是讓每個 seed 變小，沒有去除重複 coverage 的 seed。

**3. Stability < 90% 不去調查**

AFL++ 會繼續跑，但這是一個警告訊號。Stability 低的原因通常是可以解決的（加 ASAN 的 `detect_leaks=0`、用 `AFL_IGNORE_UNKNOWN_ENVS=1`、固定 ASLR）。放著不管的代價是：`has_new_bits()` 可能把 non-deterministic 的 bitmap 差異誤判為真正的新 coverage，引入無用的 seed。

**4. 在 fuzzing 過程中手動往 input 目錄加入新 seed**

AFL++ 可以偵測 `afl_input` 目錄的新檔案，但如果你加入大量未 `afl-cmin` 的 seed，coverage 重複的問題會累積。建議用 `afl-cmin` 先處理，或利用 AFL++ 的 import 機制（把 seed 放到 `sync/` 目錄讓 fuzzer 自己 import）。

**5. 誤解 `fs_redundant` flag 的語義**

`fs_redundant=1` 代表這個 seed 的 coverage 被 favored 子集完全涵蓋，但 AFL++ 不會把它從 queue 刪掉——它還是保留著，只是 scheduling 優先度最低。長期跑 fuzzing 後，queue 裡大多數 seed 都是 `fs_redundant`，這是正常的。

## 進階：再往深一層

**`trace_mini` 的壓縮方式**

完整的 bitmap 是 64KB（65536 bytes）。如果每個 queue entry 都存完整 bitmap，記憶體用量會爆炸。AFL++ 的解法是 `trace_mini`：把 bitmap 從 byte 壓縮成 bit（只記「這個 edge 有沒有被觸發」，不記 hit count），把 64KB 壓縮到 8KB。`cull_queue()` 用 `trace_mini` 做 favored 計算就夠了，因為 hit count 的 bucket 差異在 minset 選擇時不重要。

**Calibration 的 stability 計算**

AFL++ 跑 8 次 calibration，把 8 次的 bitmap XOR 起來看差異。如果完全穩定，8 次 XOR 結果全是 0。Stability 的公式是「穩定 bit 的比例」：

```
stability = (穩定的 edge 數) / (總 edge 數) * 100%
```

如果 stability 介於 75-90%，AFL++ 繼續但標記 `var_behavior=1`，power schedule 會對這個 seed 打折（避免在不可靠的 seed 上浪費能量）。

**Multi-instance 的 corpus 同步**

AFL++ 支援多個 fuzzer 實例透過 `-o sync_dir` 目錄同步 corpus。每個 instance 會週期性把對方發現的新 seed import 進來，再做 `has_new_bits()` 驗證。這樣 favored minset 在每個 instance 裡是獨立計算的，避免跨 instance 的協調 overhead。

## 動手練習

1. **觀察 favored 比例的演化**：

   ```bash
   # 跑 10 分鐘後，用 afl-whatsup 看 corpus 狀態
   afl-fuzz -i seeds/ -o out/ -- ./target @@ &
   sleep 600
   # 看 out/default/queue/ 的 seed 數量，和 out/default/plot_data 裡的 favored 比例
   ```

2. **實驗 afl-cmin 的效果**：

   ```bash
   # 先複製 10 個大小相近、內容類似的 seed 進 seeds/
   # 跑 afl-cmin
   afl-cmin -i seeds/ -o seeds_min/ -- ./target @@
   # 比較 seeds/ 和 seeds_min/ 的 seed 數量
   # 用 afl-showmap 驗證兩個目錄的 coverage 相同
   afl-showmap -C -i seeds/ -o /dev/null -- ./target @@
   afl-showmap -C -i seeds_min/ -o /dev/null -- ./target @@
   ```

3. **觀察 inline trim 的效果**：

   ```bash
   # 跑 fuzzing 前，記錄初始 seed 的大小
   ls -la seeds/
   # 跑 fuzzing 30 分鐘，找 queue 目錄
   ls -la out/default/queue/ | head -20
   # 比較原始 seed 和 trimmed seed 的大小差異
   ```

4. **調查低 stability 的原因**：

   ```bash
   # 如果看到 stability < 90%，用 afl-analyze 找出是哪些 byte 在造成變動
   afl-analyze -i suspicious_seed -- ./target @@
   ```

## 本章重點整理

- `has_new_bits()` 是 corpus 生長的守門員——只有帶來新 edge 或新 hit count bucket 的執行結果才能讓 seed 進入 queue；新 seed 入隊前先跑 calibration 確認 bitmap stability
- `cull_queue()` 用貪婪演算法找 favored minset：對每個 edge 選 `len * exec_us` 最低的 seed，標記為 `favored`；favored seed 在 scheduling 裡優先度顯著高於其他 seed
- `afl-cmin` 是集合最小化（多 seed → 最小 coverage 等效子集），`afl-tmin` 是單個 seed 的大小最小化，兩者解決不同問題

## 自我檢核

1. `has_new_bits()` 回傳 1 和回傳 2 的差別是什麼？哪種更重要？

2. `top_rated[i]` 陣列的 index `i` 代表什麼？AFL++ 用什麼 metric 決定哪個 seed 進入 `top_rated[i]`？

3. Calibration stability 低於 90% 代表什麼問題？給出兩個實際原因。

4. 你有 500 個 seed，想在跑 fuzzing 之前縮減到最小有效集合。你應該用 `afl-cmin` 還是 `afl-tmin`？這兩個工具的輸出格式有什麼不同？

5. `favored=0` 的 seed 會被 AFL++ 完全忽略嗎？實際的 scheduling 行為是什麼？

## 延伸閱讀

- **[AFL 技術白皮書](https://lcamtuf.coredump.cx/afl/technical_details.txt)** — lcamtuf
  - **核心貢獻**：AFL 原始設計者對 feedback mechanism 和 corpus 管理的第一手說明
  - **讀哪裡**："The feedback mechanism" 和 "Corpus minimization" 兩節
  - **和本章的關聯**：本章的 `has_new_bits()` 邏輯和 favored minset 概念都直接來自這份文件

- **[Evaluating Fuzz Testing](https://dl.acm.org/doi/10.1145/3243734.3243804)** — Klees, Ruef, Cooper, Wei, Hicks, CCS 2018
  - **核心貢獻**：批判性地檢視 fuzzing 評估方法，指出 corpus 管理選擇對實驗結果的影響被嚴重低估
  - **讀哪裡**：Section 4.3（corpus 選擇對 bug 發現率的影響）
  - **和本章的關聯**：解釋為什麼「seed 越多越好」的直覺是錯的，以及正確的 corpus 評估應該怎麼做

- **[AFL++ utils/README.md](https://github.com/AFLplusplus/AFLplusplus/tree/stable/utils)** — AFL++ 官方
  - **核心貢獻**：`afl-cmin` 和 `afl-tmin` 的詳細使用說明和實際範例
  - **讀哪裡**：整份文件都值得讀，重點看 `afl-cmin` 的 `-t` timeout 設定和 `afl-tmin` 的 `-x` flag
  - **和本章的關聯**：本章介紹的工具的官方使用文件

→ [Ch 11 — Mutation 策略：Deterministic、Havoc、Splice](./11-mutation-strategies.md)
