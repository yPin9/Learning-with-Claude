# Ch 8 — Corpus 生命週期與 favored minset

> 目標：說明 queue entry 的狀態機（new → interesting → favored）；拆解 `cull_queue()` 怎麼挑出最小覆蓋集；解釋為什麼 favored 會被優先挑去 fuzz。

## 一個 input 從哪裡來、到哪裡去

AFL++ 的 corpus 管理是一條單向流水線：

```
初始 seed (from -i) ──▶ queue/ ──▶ fuzz 這個 seed ──▶ mutate 產生新 input
                                                        │
                                                        ▼
                                              跑 target → has_new_bits?
                                                        │
                            ┌───────────────────────────┤
                            │ no new coverage           │ new coverage
                            ▼                           ▼
                           丟                          加入 queue/
                                                        │
                                                        ▼
                                              calibrate + mark favored
                                                        │
                                                        ▼
                                                 排進 fuzz 佇列
```

**入 queue 的唯一條件是「發現新 edge 或新 hit count 桶」**。coverage 不變的 input 一律丟棄 — 即使它 crash 也只存進 `crashes/`，不進 queue。

這條規則很強。它意味著 queue 的大小和「target CFG 的規模」同等級，不會無限膨脹。這是 AFL 能跑好幾週而不炸的關鍵。

## queue_entry 結構（再看一次）

在 Ch 3 我們已經秀過，現在聚焦幾個欄位的意思：

```c
struct queue_entry {
    u8 *fname;              // queue 檔案
    u32 len;                // input 長度
    u8 cal_failed,          // calibration 有沒有失敗
       trim_done,           // 已 trim
       was_fuzzed,          // 跑過至少一輪 mutation
       favored,             // 在 minset 裡
       passed_det;          // deterministic 階段完成
    u64 exec_us,            // 單次執行時間
        handicap,           // queue 後段才被發現的 bonus/penalty
        depth;              // 從 seed 算起的 mutation 鏈深度
    u8 *trace_mini;         // 濃縮 bitmap footprint（位元圖）
    u32 tc_ref;             // 被幾個 top_rated[] 引用
    struct queue_entry *next;
};
```

幾個關鍵概念：

- **depth**：這個 entry 是從第幾層 mutation 衍生出來的。原始 seed 的 depth = 0，mutate 原始 seed 得到的新 entry depth = 1，以此類推。深度會影響 power schedule（Ch 10）。
- **handicap**：這個 entry 是多晚才被加入的。越晚加入意味著 fuzzer 已經跑過很多東西還沒發現它 — 這代表它可能走的是少見路徑，應該給更多能量。handicap 會在 power schedule 裡變成加分項。
- **trace_mini**：不是完整 64KB bitmap，而是**只有哪些 edge 被這個 input 點亮的位元圖**（1 bit per edge），大小 MAP_SIZE / 8 = 8KB。後面做 minset 就靠它。

## Calibration：新 entry 的入關檢查

新 input 進 queue 前要先 calibrate — 用同樣的 input 多跑幾次（預設 8 次），檢查：

1. **確認性**：同樣 input 每次應該產生同樣的 bitmap。如果不同 → target 行為不確定（可能有 random、uninit memory、time-dependent logic），標記為 `var_behavior`。後續排程會對這種 entry 扣分。
2. **執行時間**：記下 `exec_us`，後面排程知道這個 entry 跑多慢。
3. **新 bit 確認**：calibrate 期間的 bitmap 取 union，確保「有新 coverage」這個判斷是穩定的。

Calibration 失敗的 input 會被丟。這是一個隱藏的濾網 — 有些 target 根本不確定，fuzzer 會自動避開浪費時間。

## Favored minset：為什麼需要

AFL 的 queue 能長到幾千、幾萬條。但 fuzzer 每 iteration 只能選一個 entry 去 mutate。如果每次都隨機選一個，大部分時間會花在「走差不多路徑」的 entry 上，新發現速度變慢。

觀察：**queue 裡有很多冗餘**。兩個 entry 如果覆蓋的 edge 有很大交集，挑哪個效果差不多。如果能先算出一個「**最小覆蓋集（favored minset）**」— 用最少的 entry 覆蓋所有已知 edge — 只在這個 minset 裡優先挑，fuzzer 就能更快移動到「對新發現有最大邊際效益」的 entry 上。

這就是 `favored` 旗標的意義。實作演算法：**greedy set cover on edges**。

## cull_queue：minset 怎麼算

`src/afl-fuzz-queue.c` 的 `cull_queue()` 函式，流程大概：

```c
void cull_queue(afl_state_t *afl) {
    if (!afl->score_changed) return;   // 沒變就不算

    u8 *temp_v = malloc(MAP_SIZE >> 3);
    memset(temp_v, 0xff, MAP_SIZE >> 3);   // 全 1 = 每格都還沒被覆蓋

    // 先把所有 entry 的 favored 清掉
    for (struct queue_entry *q = afl->queue; q; q = q->next)
        q->favored = 0;

    // 依 top_rated[] 決定誰進 minset
    for (int i = 0; i < MAP_SIZE; i++) {
        if (afl->top_rated[i] && (temp_v[i >> 3] & (1 << (i & 7)))) {
            // 這條 edge 的最佳 owner 還沒被 pick → pick 這個 entry
            afl->top_rated[i]->favored = 1;
            afl->queued_favored++;
            
            // 把這個 entry 覆蓋的所有 edge 從 temp_v 標掉
            discount_bits(temp_v, afl->top_rated[i]->trace_mini);
        }
    }
}
```

關鍵是 `top_rated[]`：大小 MAP_SIZE 的陣列，`top_rated[edge_id]` 指向「最適合作為這條 edge 代表的 queue entry」。什麼叫最適合？由一個 score 判斷：

```c
score = exec_us * len    // 執行時間 * 輸入長度
```

**越小越好** — 我們要的是**跑得快、input 短** 的代表。兩個 entry 同樣覆蓋一條 edge，我們當然選快的、短的。

當新 entry 進 queue，會遍歷它覆蓋的每條 edge，比較 score：如果比 `top_rated[edge]` 當前的佔位者還小，就替換掉。

### Greedy set cover 在這裡

`cull_queue` 那個 loop 本質是一個 **greedy set cover**：從 `top_rated[]` 裡依序挑，標記它覆蓋的 edge 為「已處理」，繼續挑下一條還沒處理的 edge 的代表。這不是最優解（set cover 是 NP-hard），但 greedy 版本的近似比是 log(n)，對 fuzzing 效益已經很好。

## 怎麼挑下一個 entry 來 fuzz

`src/afl-fuzz-one.c` 的主 loop：

```c
void fuzz_one(afl_state_t *afl) {
    struct queue_entry *q = afl->queue_cur;

    // 機率跳過非 favored 的 entry
    if (!q->favored) {
        if (afl->queued_favored > 0 && rand() % 100 < 95) skip;
        if (afl->queue_cycle >= 1) ...
    }
    
    // 真正 fuzz 這個 entry
    ...
}
```

**95% 機率跳過非 favored entry**（如果 favored 存在的話）。這把大部分時間都押在 minset 裡，剩 5% 留給非 favored 的長尾 — 防止 minset 因為 score metric 不完美而遺漏某個重要 entry。

## queue cycle 概念

queue 從頭到尾掃過一輪叫一個 queue cycle。每開始一個新 cycle，`afl->queue_cycle++`，TUI 上可以看到。

觀察 queue cycle 數：
- **cycle 0**：剛開始，還在處理 initial seeds。
- **cycle 1–5**：fuzzer 在 rapid expansion 期，每次 cycle 都會發現新 entry。
- **cycle 很久沒變**：可能已經 saturated，考慮換 target、加 dict、開 CmpLog。

`cycles done` 是 TUI 上很值得關注的數字。

## 踩雷 / 常見誤解

- **「queue entry 越多越好」**：不。很多 entry 等於說 fuzzer 找到很多微小分叉 — 但如果 score_changed 後 favored 沒大幅成長，新進來的都是冗餘。觀察 `favored` 數遠比觀察總 entry 數有意義。
- **「cull_queue 是準確的」**：不是。greedy 近似解 + exec_us×len 的啟發式 score，不保證最優。但對 fuzzing 足夠好。如果你想做研究，可以改這個函式試別的 heuristic。
- **「non-favored entry 完全不 fuzz」**：不是。95% 跳過，5% 還是會做。AFL++ 故意留這個 slack 避免 minset 死板。

## 幾個想法上的延伸

- **queue/ 檔案**：每個 entry 都是實體檔案（像 `id:000042,src:000001,op:flip4,pos:12`），檔名記載 ancestry（從誰衍生、用什麼 mutator、在哪個 byte 改的）。parallel fuzzing 用這個檔名 sync 新發現（Ch 16）。
- **trim**：AFL 會嘗試把每個 queue entry 縮短，只要不影響 coverage 就砍。`trim_done` 記錄狀態。較短的 input 之後 mutate 效率更高。
- **AFL++ 的改進**：原 AFL 的 `exec_us × len` 公式有更新版本，加入 handicap、depth 的考量。想深究可以讀 `calculate_score()` 函式。

## 自我檢核

- [ ] 能解釋「加入 queue 的唯一條件是發現新 edge」
- [ ] 記得 `calibrate_case()` 做什麼、為什麼失敗的 case 會被丟
- [ ] 能用自己的話說明 favored minset 的意義 — 為什麼只挑 minset 做 fuzz
- [ ] 能解釋 `top_rated[]` 和 `cull_queue` 的關係
- [ ] 知道 95% / 5% 的 favored selection 機率

下一章進 mutator — deterministic、havoc、splice 這些耳熟的名字到底是什麼。

→ [Ch 9 Mutation 策略：deterministic、havoc、splice](./09-mutation-strategies.md)
