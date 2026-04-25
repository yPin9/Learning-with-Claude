# Ch 14 — Backjumping 與 1UIP

> 目標：把 Ch 13 的 conflict analysis 從「resolve 到某個停止條件」精確化。標準答案叫 **1UIP（First Unique Implication Point）**，它決定了 learned clause 的形狀、backjump level、甚至 solver 的收斂性。讀完你會知道為什麼 MiniSat `analyze()` 那 60 行可以這麼漂亮。

## 為什麼需要停止條件

Ch 13 的 conflict analysis pseudo-code 說「重複 resolve 直到滿足 UIP 條件」，但沒精確定義。為什麼不一路 resolve 到只剩 decision literal？

反例：假設 conflict 涉及 level 5 的 5 個 propagation 和 level 3 的 2 個 decision。一路 resolve 到底得到 `(¬d₁ ∨ ¬d₂ ∨ ¬d₃)` 這種「只含 decision literal」的 clause — 叫 **decision clause**。問題：

- 它可能非常長（所有相關 decision）
- Backjump 到哪？要跳到 `d₂` 和 `d₃` 之間某處？保留哪些 decision？

反之，**停太早** learned clause 太弱、剪枝不夠。

要一個 **Goldilocks 條件**：既能界定 backjump level，又能保證 learned clause 之後會立刻 unit propagate（叫 **asserting property**）。答案是 1UIP。

## UIP 的定義

**Unique Implication Point (UIP)**：在 implication graph 的當前 decision level 子圖中，**任何從該 level 的 decision 到 conflict 節點的路徑都會通過**的節點。

```
     d (decision of current level)
     │
     ├──→ a ──→ ⊥
     │    ↑
     └──→ b ──┘
     
     d 是 UIP (所有路徑從 d 開始)
     ⊥ 也算 UIP (自己)
     但 a 不是 UIP (有路徑 d→b→⊥ 不經過 a)
     b 不是 UIP (有路徑 d→a→⊥ 不經過 b)
```

**1UIP**：從 conflict 節點往回走，**第一個** UIP（最靠近 `⊥` 的那個）。

為什麼取「第一個」？因為它產生 **最短的** asserting learned clause，且 backjump level 最深（可以跳最多、剪最多）。

## 在 trail 上怎麼算

不用真的建 DAG。UIP 可以在 trail 上線性時間找到：

```
Algorithm: find 1UIP
    seen ← {}
    counter ← 0           # current level 還剩幾個 literal 沒解釋
    learned ← []           # 收集非 current-level 的 literal
    current_clause ← conflict clause

    # Step 1：先把 conflict clause 的 literal 分類
    for lit in conflict_clause:
        var_level ← var(lit).level
        if var_level == current_level:
            mark(lit) as seen, counter++
        elif var_level > 0:
            learned.push(lit)
        # level 0 的 literal 是永遠成立的事實，不用加

    # Step 2：沿 trail 反向走
    trail_idx ← len(trail) - 1
    while counter > 1:
        while not seen[trail[trail_idx]]: trail_idx--
        pivot_lit ← trail[trail_idx]; trail_idx--
        counter--
        reason ← var(pivot_lit).reason
        # resolve current implicit clause with reason on pivot_lit
        for l in reason:
            if l == pivot_lit: continue
            if seen[l]: continue
            seen[l] ← true
            if var(l).level == current_level: counter++
            elif var(l).level > 0: learned.push(l)

    # Step 3：還剩最後一個 current-level 的 literal 就是 1UIP
    uip_lit ← trail[trail_idx]
    learned.push(¬uip_lit)    # 加進去，但 negation
    return learned
```

**關鍵迴圈條件 `counter > 1`**：當 current-level 只剩一個 literal 還沒「解釋」，那個就是 1UIP。

**Learned clause 是 `learned`**，其中 `¬uip_lit` 是 1UIP literal 的 negation，其餘是從非 current-level 來的 literal。

## 1UIP 為什麼是 asserting

**Asserting clause**：learned clause 在 backjump 到適當 level 後，剛好變成 **unit clause** 並 propagate。

`learned = [l₁, l₂, ..., lₖ, ¬uip_lit]`。`¬uip_lit` 在 current level，其餘都在更低 level。Backjump level = `learned` 中 **second-highest level**。跳回那個 level 後：

- `l₁, ..., lₖ` 還是 false（它們的 assign 在 ≤ 那個 level）
- `¬uip_lit` 變 unassigned（它在 current level，被 unset 了）
- 只剩 `¬uip_lit` 一個 unassigned → **unit**，強制 propagate

**新 propagate 的 literal 是 `¬uip_lit`**，這跟原 decision 「uip_lit = ⊤」的方向相反。搜索自動走到另一條路，不需要手動 flip decision。

## Backjump Level 的推導

Learned clause 的 literal 在多個 level，**跳到 second-highest level**：

```
levels = [level(l) for l in learned]
backjump = max({l for l in levels if l < current_level})

若 learned 只有 current-level 一個 literal (unit):
    backjump = 0
```

**跳到 level 0** 代表根從頭開始 unit propagate，整顆樹重開一部分，通常發生在 **learning unit clause** 這種「發現某變數必為某值」的強力 learning。

## 一個完整的 1UIP 範例

CNF 包含（其中相關的）：

```
C₁: (¬x₁ ∨ x₂)
C₂: (¬x₁ ∨ x₃ ∨ x₉)
C₃: (¬x₂ ∨ ¬x₃ ∨ x₄)
C₄: (¬x₄ ∨ x₅ ∨ x₁₀)
C₅: (¬x₄ ∨ x₆ ∨ x₁₁)
C₆: (¬x₅ ∨ ¬x₆)
```

Decisions（省略更早 level）：

```
level 1: x₉ = ⊥ (decision)
level 2: x₁₀ = ⊥ (decision)
level 3: x₁₁ = ⊥ (decision)
level 4: x₁ = ⊤ (decision)
    propagate: C₁ → x₂ = ⊤
    propagate: C₂ → x₃ = ⊤ (x₉ = ⊥ 已)
    propagate: C₃ → x₄ = ⊤
    propagate: C₄ → x₅ = ⊤ (x₁₀ = ⊥ 已)
    propagate: C₅ → x₆ = ⊤ (x₁₁ = ⊥ 已)
    C₆ = (¬x₅ ∨ ¬x₆) → conflict (x₅=⊤, x₆=⊤)
```

Conflict clause 是 C₆。Current level = 4。Trail 反向：`x₆ → x₅ → x₄ → x₃ → x₂ → x₁`。

Step 1：從 C₆ = `(¬x₅ ∨ ¬x₆)` 開始。兩個 literal 都在 level 4 → counter = 2，learned = []。

Step 2：在 trail 反向找 seen = true 的。`x₆` 是最後 assigned 的 seen，pivot = `x₆`。

- reason(x₆) = C₅ = `(¬x₄ ∨ x₆ ∨ x₁₁)`
- resolve 後新 literals：`¬x₄` (level 4)、`x₁₁` (level 3)
- counter: -1 (減 x₆) +1 (加 x₄) = 2；learned += [¬x₁₁... 加 literal 自己（原 clause 那顆）]

Step 3：trail 再往前，`x₅`。pivot = `x₅`。

- reason(x₅) = C₄ = `(¬x₄ ∨ x₅ ∨ x₁₀)`
- 新：`¬x₄` 已 seen；`x₁₀` (level 2)
- counter: -1 = 1 → 停止

**剩下最後一個 current-level literal 是 x₄**（seen 還沒消除的）。1UIP = `x₄`。

Learned clause = `(¬x₄ ∨ x₁₀ ∨ x₁₁)`（這幾個原形 — 因為我們把 seen 的 literal 依 polarity 收入）。

Backjump level = max(level(x₁₀), level(x₁₁)) = max(2, 3) = **3**。

後續：backjump 到 level 3，`¬x₄` 被 unit propagate。Solver 自動避開「x₁₁ = ⊥ ∧ x₁₀ = ⊥ ∧ x₄ = ⊤」組合。

## 為什麼 1UIP 最好

Marques-Silva & Sakallah 1996（GRASP）原本用 **every-UIP**（每個 UIP 都學）。後來研究發現：

- **1UIP**：最靠近 conflict → learned clause 最短、backjump 最深 → 剪枝最強
- **lastUIP (decision clause)**：只含 decisions → clause 過長
- **every-UIP**：clause 數過多、記憶體吃不消

2003 以後所有主流 solver 都用 **1UIP**（MiniSat、Glucose、CaDiCaL）。

## Clause Minimization

Learned clause 可進一步縮減。**Recursive literal minimization（self-subsumption）**：

- Clause 中某個 literal `l` 的 reason clause 的所有 literal **都** 已在 learned clause 裡 → `l` 是冗餘，可以移除

這個優化讓 learned clause 平均再小 20–30%，是 MiniSat 做的，細節 `Solver::analyze()` 後半段 `if (ccmin_mode)` 那段。

簡單版：

```
for l in learned[1:]:         # 除了 1UIP literal
    reason ← var(l).reason
    if reason != null and all literals in reason are in learned:
        remove l from learned
```

這個做法有變體（recursive、non-recursive），CaDiCaL 有三種模式可選。

## Learned Clause 加進 CNF 後

1. **變成 regular clause**，受 watched literals 管（挑兩個 literal watch）
2. **參與未來 unit propagation**，避免重蹈覆轍
3. 可能被 **clause deletion** 丟掉（Ch 15）

## 動手練習

1. **手做完整一次**：挑一個 5 變數 random 3-SAT，手跑 CDCL 到第一個 conflict，手算 1UIP、learned clause、backjump level。這個手感沒有捷徑，硬算一次之後 MiniSat `analyze()` 就看得懂。
2. **比較學 1UIP vs lastUIP**：改你即將寫的 v2 solver（Ch 16），切換兩種策略，跑同樣 benchmark 看 learned clause 平均長度和總 solve time。1UIP 應該在兩個指標都贏。
3. **把 minimization 關掉**：切掉 recursive minimization，看 learned clause 平均長度增加多少（通常 20–30%）和 solve time 增加多少（通常 1.2×–1.5×）。

## 常見誤解

- **「1UIP 永遠是 current-level 的 decision literal」** — **錯**，大部分時候不是。它通常是一個 propagation literal，位在 decision 跟 conflict 之間的關鍵 cut 點。
- **「Backjump 一定能跳到 level 0」** — 不一定。只有 learned clause 的 second-highest level = 0（e.g. learned unit clause）才跳到 0。
- **「learning 多一定好」** — 不對。太多 learned clause 吃記憶體、拖 propagation（watch list 變長），Ch 15 的 clause deletion 就是對這個的 pushback。

## 自我檢核

- [ ] 說得出 UIP 的定義（implication graph 的 cut 點）
- [ ] 知道 1UIP 是「最靠近 conflict 的 UIP」
- [ ] 會手算 1UIP 的 conflict analysis
- [ ] 會推導 backjump level = second-highest level
- [ ] 理解為什麼 1UIP 保證 learned clause 是 asserting
- [ ] 知道 clause minimization 能再砍 20–30%

CDCL 的骨架（learning + backjumping）完整了。下一章補上讓它跑得快的 **heuristic** — VSIDS branching、phase saving、Luby restart、clause deletion。這些不影響 correctness，但影響 **能否在工業題 timeout 內跑完**。

→ [Ch 15 — VSIDS、phase saving、Luby restart](./15-heuristics-restart.md)
