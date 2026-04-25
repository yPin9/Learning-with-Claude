# Ch 13 — CDCL 核心：implication graph、conflict analysis

> 目標：搞懂 CDCL（Conflict-Driven Clause Learning）的本體 — 衝突發生時 **分析它、學一條新 clause**、下次避開同樣陷阱。這章是整門課（不誇張）最重要的一章。認真讀完，你會知道為什麼現代 SAT solver 能解百萬變數的工業題。

## DPLL 的根本弱點

Ch 10 裡 DPLL 遇到 conflict 只做一件事：**chronological backtrack**，回到最近一次 branching。

問題是：**這完全沒學到東西**。一個典型的悲劇情境：

```
decision level 1:  a = ⊤
decision level 2:  b = ⊤
decision level 3:  c = ⊤
... 一堆 propagation 和 decision ...
decision level 37: z = ⊤   ← 這裡衝突
                   conflict 的根源其實是 a 和 b 的組合

DPLL backtracks to 36, 猜 z = ⊥ ... 衝突
         backtracks to 35, 猜 y, ⊤ ⊥ 都錯 ... 衝突
         ...
         終於回到 level 1 改 a
```

DPLL **沒發現衝突跟 a, b 相關**，所以在 level 3 到 37 之間的所有組合它都得重試一次。**這是指數時間的實質來源**。

CDCL 的兩個突破：

1. **Conflict analysis**：衝突發生時分析 implication graph，找出真正的 root cause、學成 clause
2. **Backjumping**：直接跳回 learned clause 允許的那層 decision level，而不是 chronological

這兩件事一起做，CDCL 把 DPLL 在工業題上打成殘影。

## Decision Level 與 Trail

CDCL 的核心資料結構比 DPLL 複雜一點：

- **Trail**：有序 list，記錄 assignment 按「被 assign 的先後」排列
- **Decision level**：每個 assign 記錄「它是第幾次 branching 下的決定」
- **Reason**：每個 propagate 來的 assign 記錄「哪條 clause 強制了它」

```
Trail: [ a(d1), b(d2), c(d2:r₃), d(d2:r₄), e(d3), f(d3:r₅), ... ]
         ↑       ↑       ↑         ↑          ↑       ↑
      decision decision  unit      unit     decision  unit
       level 1  level 2  level 2   level 2  level 3  level 3
                         reason    reason            reason
                         clause₃   clause₄           clause₅
```

`d1, d2, d3` 是 decision level。Level 1 的 `a` 是 branching 的決定，`c, d` 是在 level 2 的 branch `b=⊤` 下被 unit propagate 出來的（reason 是 r₃, r₄）、依此類推。

Level 0 是 **before any decision**，只有從原 CNF 的 unit clause 傳出來的 assign — 這些是 **永遠成立** 的事實。

## Implication Graph

CDCL 會把 trail 建成 **implication graph（有向圖）**：

- **節點**：每個 assigned literal，外加一個 **conflict node `⊥`**
- **邊**：如果 literal `l` 是因為某個 reason clause `C` 被 propagate 的，那 `C` 中 **其他所有 literal 的 negation**（都在 trail 裡）都有邊指向 `l`

**直覺**：箭頭代表「這個 assignment 是由這些 assignment 推出來的」。

範例。CNF 有以下 clauses（之中幾條）：

```
C₁: (¬a ∨ ¬b ∨ c)
C₂: (¬a ∨ ¬c ∨ d)
C₃: (¬d ∨ ¬b ∨ e)
C₄: (¬b ∨ ¬e)
```

做 decision `a = ⊤` (d1)、`b = ⊤` (d2)。Unit propagation：

- `C₁` 變 `(¬a ∨ ¬b ∨ c)`，`¬a = ⊥`、`¬b = ⊥`，逼 `c = ⊤`（reason C₁）
- `C₂` 逼 `d = ⊤`（reason C₂）
- `C₃` 逼 `e = ⊤`（reason C₃）
- `C₄`：`b = ⊤` 且 `e = ⊤` → 所有 literal 都 false → **conflict**

Implication graph：

```
  a(d1) ──────┐
              ├──→ c(d2, reason=C₁)
  b(d2) ──────┤           │
       │      │           ├──→ d(d2, reason=C₂)
       │      └──→ ...    │
       │                  ├──→ e(d2, reason=C₃)
       │                  │
       └──────────────────┴──→ ⊥  (衝突在 C₄)
```

`⊥` 上匯聚的邊來自 `C₄` 的所有 literal 的 negation：`b` 和 `e`，兩者都指向 `⊥`。

## Conflict Analysis = Resolution

**關鍵**：**conflict analysis 本質是 resolution**（Ch 6），一路從衝突 clause 往回 resolve。

Algorithm 粗略版：

```
1. current_clause ← 衝突 clause (e.g. C₄)
2. 挑出當前 decision level 最近 propagate 的 literal l
3. reason_clause ← l 的 reason
4. current_clause ← resolve(current_clause, reason_clause) on l
5. 重複直到滿足 UIP 條件（Ch 14 講）
6. 把 current_clause 加進 CNF，作 learned clause
```

走一遍範例：

```
Step 1: current = C₄ = (¬b ∨ ¬e)
        當前 level = 2，最近 propagate 的是 e
Step 2: reason(e) = C₃ = (¬d ∨ ¬b ∨ e)
        resolve C₄ 和 C₃ on e：
          (¬b ∨ ¬e) + (¬d ∨ ¬b ∨ e) 消去 e
          = (¬b ∨ ¬d)
Step 3: current = (¬b ∨ ¬d)
        當前 level 2 的 literal：b(decision), d
        最近 propagate 是 d
Step 4: reason(d) = C₂ = (¬a ∨ ¬c ∨ d)
        resolve on d：
          (¬b ∨ ¬d) + (¬a ∨ ¬c ∨ d) = (¬b ∨ ¬a ∨ ¬c)
Step 5: current = (¬a ∨ ¬b ∨ ¬c)
        ...繼續 resolve c...
Step 6: resolve on c，reason(c) = C₁ = (¬a ∨ ¬b ∨ c)
        (¬a ∨ ¬b ∨ ¬c) + (¬a ∨ ¬b ∨ c) = (¬a ∨ ¬b)
        
Learned clause: (¬a ∨ ¬b)
```

**這條 `(¬a ∨ ¬b)` 就是 learned clause**。意思：「`a` 和 `b` 不能同時為 ⊤」。

停在哪裡？Ch 14 的 **1UIP** 給正式答案。這個範例用「resolve 到當前 level 只剩一個 literal」簡化版規則停，剛好到 `(¬a ∨ ¬b)` — 這條 clause 在 level 1 下是 **asserting**（level 2 的 `¬b` 在 backjump 後會 unit propagate）。

## Learned Clause 的好處

加回 CNF 之後：

1. **下次遇到 `a = ⊤` 和 `b = ⊤` 的組合會立刻衝突**（或 unit propagate 出 `¬b`），不用再一路走到 `e`
2. **Learned clause 對整個搜索空間的剪枝是永久的** — 它是原 CNF 的邏輯後果（resolution sound）、加進去不改變 SAT 性質
3. **避免重走同樣陷阱**，每次 conflict 就 **學** 一次

## Backjumping

Learned clause `(¬a ∨ ¬b)`。當前 level 2，但這 clause 只牽涉 level 1 的 `a` 和 level 2 的 `b`。**可以跳回 level 1**（而不是 level 0），在 level 1 的 trail 上 **unit propagate** `¬b`。

```
Before: level 2, trail = [a(d1), b(d2), c, d, e]
                           ↑                    ↑
                                              conflict

Learn: (¬a ∨ ¬b)
Backjump level = second-highest level in learned clause = 1

After: level 1, trail = [a(d1), ¬b(d1, reason=learned)]
                                  ↑
                       unit-propagate from learned clause
```

`¬b` 現在不是 decision 了，**是 propagation**。繼續搜索時根本不會再考慮 `b = ⊤`。

**Backjump level**：learned clause 的 second-highest decision level（最高 level 的那個 literal 留著 propagate，次高的是跳回的目標）。這個規則 Ch 14 推 UIP 時會推導出來。

## CDCL 主循環

```
procedure CDCL():
    trail ← []
    level ← 0
    while true:
        propagate()
        if conflict:
            if level == 0: return UNSAT
            (learned, backjump_level) ← analyze_conflict()
            cnf.add_clause(learned)
            backtrack_to(backjump_level)
            assign_from_learned(learned)   // propagate learned clause
            level ← backjump_level
        elif all variables assigned:
            return SAT (with current assignment)
        else:
            x ← pick_branch_variable()     // VSIDS (Ch 15)
            level ← level + 1
            assign(x, pick_phase())
```

**四個動作循環**：propagate → conflict? 分析 : 完成? → 回 : 沒完成 → 猜。

## 這個 clause learning 有多強

對隨機 3-SAT，CDCL 把 DPLL 打 10×–100×。對 **工業 instance**（CBMC、硬體驗證），CDCL 把 DPLL 打到 **跑不動 → 毫秒** 的級別 — 工業題有**結構**，CDCL 每次 conflict 學的 clause 能表達深層依賴，DPLL 盲目 backtrack 碰不到。

**所有現代 SAT solver 都是 CDCL**。MiniSat、Glucose、CaDiCaL、Kissat — 共享這個骨架，差在 heuristic（VSIDS variants、restart strategy、preprocessing）。

## 實作上的資料結構

新增的東西：

```cpp
struct Var {
    Value value = Value::Unassigned;
    int   level = -1;         // 在哪個 decision level 被 assign
    Clause* reason = nullptr; // 若是 propagate 來的，reason clause；decision 則 null
};

std::vector<Var> vars;        // 1-indexed
std::vector<Lit> trail;       // assign 順序
std::vector<int> trail_lim;   // trail_lim[d] = level d 開始的 trail index
```

Conflict analysis 不真的建圖，它 **在 trail 上反向走**，對每個需要的 literal 查 `reason`、resolve 進當前 clause。這樣比顯式建 DAG 省記憶體。

## 動手練習

1. **手做一次 conflict analysis**：寫個 CNF、決定幾個 decision、手跑 propagation、製造 conflict，然後手動 resolve 到 learned clause。這個手感你 **必須** 自己跑一次，讀 code 讀不出來。
2. **追 MiniSat 的 analyze()**：打開 `minisat-2.2.0/core/Solver.cc` 的 `Solver::analyze`，大約 60 行。它就是這章講的東西 + 1UIP 停止條件 + 小優化（minimization）。**每一行都有意義**。
3. **故意漏加 learned clause**：在 pseudo-code 裡把 `cnf.add_clause(learned)` 拿掉，solver 只做 backjump 不學 clause。跑 SAT competition 的 `uf150` benchmark，會看到 solve time 從毫秒爆回分鐘 — **這就是 learning 的威力**。

## 常見誤解

- **「Learned clause 會讓 CNF 膨脹」** — 會。每 conflict 學一條。Ch 15 會講 **clause deletion** —— 定期丟掉「沒用的」learned clause。
- **「Backjumping 一定比 chronological backtrack 好」** — 幾乎總是。但 chronological backtracking + learning 也可行（Nadel & Ryvchin 2018 的 chronological CDCL paper），在某些 instance 更快。冷門但存在。
- **「Implication graph 要真的存一張圖」** — 不必。現代 solver 都 **on-the-fly** 從 trail 重建衝突路徑。建 DAG 做 analysis 是教學用的抽象。

## 自我檢核

- [ ] 說得出 CDCL 比 DPLL 強的兩件事（learning + backjumping）
- [ ] 畫得出 implication graph 的基本樣子（decision 節點、propagation 節點、reason edges）
- [ ] 知道 conflict analysis 就是在 trail 上反向做 resolution
- [ ] 會手動走一次 conflict analysis 例子
- [ ] 理解為什麼 learned clause 能永久剪枝、提升下一輪效率
- [ ] 知道 backjump level = learned clause 的 second-highest level

衝突分析的骨架有了，但**什麼時候停止 resolving**？停太早 learned clause 弱、停太晚學出來的 clause 可能涉及太多變數。下一章 1UIP 給出標準答案。

→ [Ch 14 — Backjumping 與 1UIP](./14-backjumping-1uip.md)
