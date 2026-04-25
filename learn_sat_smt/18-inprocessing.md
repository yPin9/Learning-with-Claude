# Ch 18 — Inprocessing 與 vivification

> 目標：學會**在 solve 過程中**插入簡化（inprocessing），不只在 solve 前（preprocessing）。Inprocessing 能利用 solver 已學到的 learned clause 跟 level-0 unit，效果通常比純 preprocess 強。Vivification、probing、on-the-fly subsumption 是三大主力。

## 為什麼 inprocessing 更強

Preprocessing 只看**原 CNF**。但 solver 跑幾百萬 conflicts 之後，學到的資訊遠不只原 CNF：

- **Learned clause**：編碼了搜索過程中發現的依賴
- **Level-0 unit**：confirmed 為真的變數（backjump 到 level 0 得到的 unit）
- **Failed literal**：某些 literal 已被證不可能

**所有這些都可以拿來進一步簡化 CNF**。這就是 inprocessing — 每隔 N 次 conflicts/restarts，暫停 solve、做一輪簡化、繼續。

**Järvisalo–Heule–Biere 2012** 建立了 inprocessing 的理論框架（確認各種規則互相組合後仍 sound），現代 solver（CaDiCaL、Kissat）的 inprocess 本體就是這套。

## Vivification

**Vivification** (Piette, Hamadi, Saïs 2008)：把現有 clause 「復活」— 嘗試用當前資訊縮短它。

對每條 clause `C = (l₁ ∨ l₂ ∨ ... ∨ lₖ)`：

```
1. 暫時 assign l₁ = false（試圖「證明它必為 false」）
2. 做 unit propagation
3. 若某個其他 lᵢ 被 propagate 成 false → 可以從 C 裡刪掉 lᵢ
4. 若衝突 → l₁ 必為真（加成 unit）
5. 否則 → 試下一個 l₂
6. 撤銷所有 assignment
```

**為什麼 work**：某些 clause literal 是多餘的 — 在當前 CNF 約束下它們永遠無法 deciding（被其他 literal 蓋過）。Vivification 暴力找出來。

### 範例

```
CNF 包含: C₁: (a ∨ b), C₂: (¬a ∨ c)
考察 clause D = (a ∨ b ∨ ¬c)
```

Vivify `D`：

- Assign `a = false`：propagate — `C₁` 逼 `b = true`。`D` 裡 `b = true`，不衝突。繼續 `b`。
- Assign `b = false`：跟剛剛 propagate 矛盾... 重置、從頭。或更細的演算法。

具體要看實作細節。重點：某些 literal 被其他 literal + propagation 蓋過、可以砍。

### Vivification 的輕量版：Asymmetric Tautology Elimination

CaDiCaL 的 **vivify** 函數，對每條 learned clause 做一次、對 irredundant clause 定期做一次。複雜度 `O(|C| × prop_cost)`，但 conflict 次數比純 subsumption 多捕捉幾十倍冗餘。

## Probing

Probing = Ch 17 的 failed literal probing，但作為 inprocessing 定期跑。

對每個變數 `x`（或挑一子集）：

```
1. Assume x = true, propagate
   若衝突 → fix x = false（level-0 unit）
   否則記錄 implications(x=T)
2. 類似試 x = false
3. 若兩者都不衝突：
   - 若 implications(x=T) ∩ implications(x=F) 非空，共同 literal 必為真
   - Equivalent literals: 如果 x=T 推出 y=T 且 x=F 推出 y=F → x = y，合併
```

### Hyper-binary resolution

Probing 的副產物。若 `x = true` propagate 出 `y = true`（透過任意長 unit chain），可以加一條 **binary clause** `(¬x ∨ y)` 到 CNF，當作 shortcut。下次 `x = true` 立刻 propagate `y = true` 只要 O(1)。

**這能大幅加速後續 propagation**，但也讓 CNF 變肥。CaDiCaL 用 LBD 指標做 trade-off。

## On-the-fly Subsumption

跑 CDCL 時、learned clause 產生後立刻檢查：

- 學到的 clause `D` 是否 subsumes 某條原 clause？若是、刪原 clause
- 原 clause 是否 subsumes `D`？若是、丟 `D`（不加進去）

**這是 Ch 17 subsumption 的 online 版**。MiniSat `analyze()` 最後調 `ccmin_mode` 就包含部分這個邏輯。

## Variable Elimination In-process

BVE（Ch 17）也可以 inprocess 做。比較微妙：

- 消變數會改 learned clause 的結構
- 需要 reconstruction stack 累積
- 實作複雜

CaDiCaL 稱之為 `elim`，預設每 300000 conflicts 跑一次。

## Inprocessing Schedule

現代 solver 的 inprocess schedule 大致：

```
每 3000 conflicts: 清 learned clause（LBD-based）
每 5000 conflicts: vivify 一輪
每 10000 conflicts: probing + hyper-binary resolution
每 30000 conflicts: on-the-fly BVE
每 100000 conflicts: 全套 inprocess + restart
```

具體數字每個 solver 不同，且 CaDiCaL/Kissat 都 **adaptive** — 根據 inprocess 的產出調整頻率。

## Sound 性問題

Inprocessing 不能亂做。以下規則是 sound 的：

- Unit propagation、pure literal → 永遠 sound
- Subsumption、BCE → sound 但要正確處理 model reconstruction
- BVE → sound but need careful reconstruction
- Vivification → sound，不需 reconstruction

但混在一起可能踩坑。Järvisalo–Heule–Biere 2012 證明了一套 **compositional soundness**：只要每個規則各自 sound，組合起來仍 sound。但**實作時 clause learning 與 inprocess 的互動容易有 bug**，特別是 watch list 維護。

### Frozen variable

某些 preprocess 技巧（例如 BVE）需要知道「這個變數會不會被 solver 之後再 assign」— 用 frozen flag 標記 assumption variable（後面 incremental SAT 會用）禁止消掉。CaDiCaL 有完整 frozen 管理，實作細節多。

## 實作建議：不要在 v2 塞太多

v2（Ch 16）把 inprocessing 全加進去會變 2000 行。**建議的實作順序**：

1. **Ch 16 結束**：v2 純 CDCL，600 行
2. **挑一個 inprocess**：最簡單的 vivification 加一千行
3. **pre-processing** 當外部工具（Ch 17）
4. **更多 inprocess** 當選修

vivification 的 pseudo-code（inprocess 版）：

```cpp
void Solver::inprocess_vivify() {
    backtrack_to(0);
    // 只 vivify 一部分 clause (例如 LBD <= 6 的 learned)
    auto targets = select_vivify_candidates();
    for (Clause* c : targets) {
        std::vector<Lit> reduced;
        bool conflict = false;
        for (Lit l : c->lits) {
            Value v = lit_val(-l);
            if (v == Value::True) { reduced.push_back(l); continue; } // already satisfied
            if (v == Value::False) continue;  // negation false → l true → skip?
            // assume -l and propagate
            new_decision_level();
            enqueue(-l, nullptr);
            Clause* cnfl = propagate();
            if (cnfl) { conflict = true; break; }
            reduced.push_back(l);
        }
        backtrack_to(0);
        if (conflict) {
            // 學一個 shorter clause
            ...
        } else if (reduced.size() < c->lits.size()) {
            // 取代 c 的 literal
            replace_clause(c, reduced);
        }
    }
}
```

**注意**：vivify 必須在 **level 0**（pristine 狀態）做，做完要復原 trail。

## 效能影響

好的 inprocessing 在 SAT Competition 能：

- 解開 20–30% 原本會 timeout 的 instance
- 平均 solve time 降 2–4×
- Clause 數穩定（vivification 縮 clause、learning 加 clause，趨平衡）

**Kissat 奪冠的秘密之一** 就是 inprocess schedule 比前代 solver 更積極、更 adaptive。

## 動手練習

1. **Vivification baseline**：對 v2 加最基本的 vivify（只處理 LBD ≤ 4 的 learned clause），每 10000 conflicts 跑一次。measure：solve time、learned clause 平均長度、CNF 總 literal 數。
2. **Probing 手算**：挑一個 10 變數 CNF，手動跑 probing `x = T` 和 `x = F`，找 failed literal、equivalent literal。對比 solver 預測。
3. **Disable inprocess**：CaDiCaL 有 `--no-elim --no-probe --no-vivify` 等 flag，挑一組 benchmark 跑 with/without 對比時間。

## 常見誤解

- **「Inprocessing 就是 preprocess 在中間再跑一次」** — 差異不只時機。Inprocess 能用 **learned clause 和 level-0 unit**，preprocess 不能（那時還沒開始 solve）。
- **「Vivification 跟 self-subsumption 一樣」** — 不一樣。Self-subsumption 只看一對 clause；vivification 用**整個當前 CNF** 做 propagation，能捕捉遠距冗餘。
- **「Inprocess 次數越多越好」** — 不對。每次 inprocess 停止 solve，有機會成本。CaDiCaL 的 adaptive 排程就是平衡收益與中斷。

## 自我檢核

- [ ] 懂 preprocess 和 inprocess 的時機差異
- [ ] 說得出 vivification 的基本步驟
- [ ] 知道 probing 能發現 failed literal + equivalent literal
- [ ] 懂 hyper-binary resolution 的動機
- [ ] 讀得懂 CaDiCaL `--no-vivify` / `--no-probe` / `--no-elim` 分別關什麼
- [ ] 理解 inprocess 是現代 solver 跟上個世代 CDCL 的主要差距

Part 1 還有 3 章 + 2 個練習。下一章換視角看 **local search** — 完全不同的 SAT 解法家族，跟 CDCL 形成互補。某些 instance 它比 CDCL 快 100 倍。

→ [Ch 19 — Local search：WalkSAT、ProbSAT](./19-local-search.md)
