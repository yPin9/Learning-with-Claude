# Ch 10 — Power schedule：誰該分到更多能量

> 目標：以 AFLFast 論文為骨幹，拆解 FAST / COE / EXPLORE / QUAD schedule 的數學式；說明為什麼 AFL++ 把預設從 EXPLORE 換成現在的版本；點出 schedule 是把「我對這個 seed 的信心」量化的一種方式。

## 什麼叫 energy

Fuzzer 對一個 queue entry 的「一輪」fuzz 其實是做 **N 次 mutation + execute**。這個 N 就叫 **energy**，或 `perf_score`、`fuzz_level`。

```
for round in queue_cycles:
    for q in queue:
        N = power_schedule(q)   # 根據 q 的特徵決定能量
        for _ in range(N):
            mutated = havoc(q.input)
            run(mutated)
            if new_coverage: add_to_queue(mutated)
```

**power schedule 的核心問題**：N 要給多少？所有 entry 平等給嗎？重要的 entry 多給一點？

## 原 AFL 的做法

原 AFL 的 energy 算式不叫 schedule，但確實有類似機制。基礎公式（簡化）：

```
perf_score = 100 * (...一堆調整因子...)
```

調整因子包含：

- **exec_us ratio**：entry 跑多快。比 queue 平均快 → 加分（因為每 exec 便宜）。
- **bitmap_size ratio**：這個 entry 覆蓋多少 edge。覆蓋越多 → 加分。
- **handicap**：handicap 越高（越晚被發現）→ 加分（因為它走的路徑難找到）。
- **depth**：mutation chain 越深 → 小幅加分。
- **favored**：favored 的 → 大幅加分。

這些因子相乘，最終給出一個 0–1600 的 perf_score，對應到實際的 havoc iteration 次數。原 AFL 叫這個 **EXPLORE schedule**。

## AFLFast 的觀察

Böhme et al. (CCS 2016) 提出一個系統性的觀察：

> AFL 的 energy 分配太公平。但 queue 裡的 entry 走的路徑頻率差異極大 —
> 99% 的 mutation 產生的 new input 走的是「常見路徑」（high-frequency），
> 真正能找到新東西的 mutation 走的是「罕見路徑」（low-frequency）。
> 我們應該給罕見路徑的 entry 更多 energy。

形式化：每條 path 被走過的次數是 `s(path)`。AFLFast 想把 energy 分配和 `1/s(path)` 掛鉤 — 罕見的給多、常見的給少。

但你不可能精確知道 path frequency（path 數量太多）。AFLFast 用代理指標：

- **fuzz_level**：這個 entry 被 fuzz 過幾次。fuzz 得少 → 沒充分探索 → 給多。
- **hit count on this path**：跑這個 entry 時 bitmap 亮的次數總和，能大致代表 path frequency。

## 四種 schedule 公式

AFLFast 論文提出四種公式，AFL++ 都支援，用 `-p <schedule>` 選。簡化版：

### EXPLORE（AFL 原版）

```
p(s) = α(s)
```

$\alpha(s)$ 是 AFL 原本那堆因子乘起來的值。基本上就是基線。

### FAST（AFLFast 預設）

```
p(s) = min(α(s) * 2^(fuzz_level) / β(s), M)
```

- $\text{fuzz\_level}$：這個 entry 被 fuzz 了幾次。
- $\beta(s)$：和 path frequency 相關的因子。
- $M$：上限，避免爆炸。

`2^fuzz_level` 指數成長 — 表面上看起來不合理（fuzz 越多次給越多）。但注意它同時被 $\beta(s)$ 除 — 高頻 path 的 $\beta$ 也大。淨效應是「對還沒收斂的 rare path 快速升溫，對已經反覆試過的 common path 降溫」。

### COE（Cut-Off Exponential）

```
p(s) = FAST if fuzz_count(s) < median else 0
```

比 FAST 更激進：只有低頻（低於中位數）的 entry 才分 energy，高頻的 entry 直接跳過。

### QUAD

```
p(s) = α(s) * fuzz_level(s)^2 / β(s)
```

用平方而非指數。比 FAST 緩和，比 EXPLORE 激進。

### LINEAR

```
p(s) = α(s) * fuzz_level(s) / β(s)
```

線性版本。

## AFL++ 的預設與演進

- AFL 原版：EXPLORE。
- AFL++ 早期：改預設為 FAST。
- AFL++ 近年：再改回 EXPLORE 當預設（!），理由：

  1. FAST 在某些 target 會過度集中 energy 在少數 entry，導致其他 entry 被餓死。
  2. EXPLORE 雖然不是最激進，但**配合 MOpt、CmpLog、redqueen 這些新 mutator**，整體效益更穩定。
  3. 論文 FOX (USENIX Sec 2023) 等實驗顯示，在現代 AFL++ 上 EXPLORE 和 FAST 差距沒當年那麼大。

所以如果你看到 AFL++ 官方文檔推薦 EXPLORE，不是 bug，是經驗累積的結果。實務上你可以：

- 主 fuzzer 用 EXPLORE。
- Parallel 的 slave 配不同 schedule（Ch 16）。

## calculate_score 實際長什麼樣

`src/afl-fuzz-queue.c` 的 `calculate_score()`（簡化）：

```c
u32 calculate_score(afl_state_t *afl, struct queue_entry *q) {
    u32 avg_exec_us = afl->total_cal_us / afl->total_cal_cycles;
    u32 avg_bitmap_size = afl->total_bitmap_size / afl->total_bitmap_entries;
    u32 perf_score = 100;

    // 依 exec_us 調整
    if (q->exec_us * 0.1 > avg_exec_us) perf_score = 10;
    else if (q->exec_us * 0.25 > avg_exec_us) perf_score = 25;
    else if (q->exec_us * 0.5 > avg_exec_us) perf_score = 50;
    else if (q->exec_us * 0.75 > avg_exec_us) perf_score = 75;
    else if (q->exec_us * 4 < avg_exec_us) perf_score = 300;
    else if (q->exec_us * 3 < avg_exec_us) perf_score = 200;
    else if (q->exec_us * 2 < avg_exec_us) perf_score = 150;

    // 依 bitmap size 調整
    if (q->bitmap_size * 0.3 > avg_bitmap_size) perf_score *= 3;
    else if (q->bitmap_size * 0.5 > avg_bitmap_size) perf_score *= 2;
    // ...

    // 依 handicap 加分
    if (q->handicap >= 4) { perf_score *= 4; q->handicap -= 4; }
    else if (q->handicap) { perf_score *= 2; q->handicap--; }

    // 依 depth 加分
    switch (q->depth) {
        case 0 ... 3:  break;
        case 4 ... 7:  perf_score *= 2; break;
        case 8 ... 13: perf_score *= 3; break;
        case 14 ... 25: perf_score *= 4; break;
        default: perf_score *= 5;
    }

    // 套 schedule
    switch (afl->schedule) {
        case FAST: /* apply FAST formula */ break;
        case COE:  /* apply COE formula */ break;
        case EXPLORE: /* 用上面算的 perf_score */ break;
        // ...
    }

    if (perf_score > HAVOC_MAX_MULT * 100) perf_score = HAVOC_MAX_MULT * 100;
    return perf_score;
}
```

每個 if 都是一個「heuristic」，累積起來就是 schedule 的全部。

## fuzz_level 的更新

每次一個 entry 被選去 fuzz，完成一輪 havoc 後 `q->fuzz_level++`。這讓下次 `calculate_score` 時 $2^{\text{fuzz\_level}}$ 改變。

fuzz_level 也用來判斷「這個 entry 是不是被 fuzz 夠多次了」— 在 favored / non-favored 的選擇邏輯裡也參考。

## 一個直覺：schedule 是「信心度」

可以這樣理解 power schedule：它回答「我對這個 entry **未來還會產生新發現** 這件事的信心有多高？」

- 新加入、fuzz 少的 entry → 信心高（還沒試充分），多給。
- 跑很快、短 input 的 entry → 信心高（試同樣多次便宜、效率高），多給。
- 覆蓋 rare edge 的 entry → 信心高（走的路少人走，更可能有沒摸過的深度），多給。
- favored → 信心高（已被證實是 minset 的代表），多給。

信心低的：試過很多次還沒收獲、很慢、覆蓋 common edge — 這些給少。

## 常見誤解

- **「FAST 一定比 EXPLORE 好」**：論文當年是，但 AFL++ 2024 的整體環境下不一定。
- **「power schedule 只影響速度不影響找到的 bug」**：錯。schedule 決定 fuzzer 探索哪些 entry — 錯誤的 schedule 會讓某些 entry 從來沒 fuzz 到，其衍生的潛在 bug 就永遠找不到。
- **「schedule 越激進越好」**：激進的 schedule（COE）會餓死一些 entry，在 diversity 上付出代價。激進 vs 平衡的取捨要看 target。

## 實務建議

- 一般情況：讓預設跑。除非你有理由否則別亂改 `-p`。
- 發現 fuzzer 卡在少數 entry：試 `-p rare` 或 `-p mmopt` 這類較新的 schedule。
- Parallel 跑：不同 instance 配不同 schedule（例如 `-p fast` + `-p explore` + `-p coe`），讓它們各自有不同探索傾向（Ch 16 細講）。

## 自我檢核

- [ ] 能說出 perf_score 的主要 input：exec_us、bitmap_size、handicap、depth、favored
- [ ] 能寫出 FAST、COE、EXPLORE 三個公式的核心差異
- [ ] 知道 AFLFast 的核心 insight 是「rare path 該多分 energy」
- [ ] 理解 fuzz_level 在 FAST 公式裡的作用
- [ ] 能說出 AFL++ 為什麼可能預設不是 FAST

下一章進 dictionary — 手動 dict 和 LTO 自動 dict 各自怎麼幫 fuzzer 破 magic bytes。

→ [Ch 11 Dictionary 與 auto-dictionary](./11-dictionary.md)
