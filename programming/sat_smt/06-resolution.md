# Ch 6 — Resolution 與 refutation 完備性

> 目標：徹底搞懂 **resolution** —— SAT solver 唯一用的推論規則。知道它怎麼運作、為什麼對 CNF 完備、為什麼只需要 **refutation**（導出空 clause）就夠。Ch 13 的 CDCL conflict analysis 本質上就是在做 resolution，這章是骨。

## Resolution Rule

一條規則，整章的主角：

```
    (A ∨ x)    (B ∨ ¬x)
    ────────────────────        (Resolution)
           A ∨ B
```

讀：**兩條 clause 中，如果一條有 literal `x`、另一條有 `¬x`，那兩條可以合成一條新 clause，內容是兩邊去掉 `x` 和 `¬x` 後的聯集**。

`A`、`B` 是其他 literal 的 disjunction（可以為空）。那條新 clause 叫 **resolvent**，`x` 叫 **pivot**。

### 例子

```
  (p ∨ q)     (¬p ∨ r)
  ────────────────────
       q ∨ r
```

Pivot 是 `p`，兩邊去掉後剩 `q` 和 `r`，合成 `q ∨ r`。

### 為什麼對

Soundness 驗證：如果 `(A ∨ x)` 和 `(B ∨ ¬x)` 都是 ⊤，取一個 valuation：

- 若 `x = ⊤`：`(B ∨ ¬x)` 為 ⊤ ⇒ `B` 為 ⊤（因為 `¬x = ⊥`）⇒ `A ∨ B` 為 ⊤
- 若 `x = ⊥`：`(A ∨ x)` 為 ⊤ ⇒ `A` 為 ⊤ ⇒ `A ∨ B` 為 ⊤

兩種情況都為 ⊤，所以 resolvent 也為 ⊤。**Resolution is sound**。

## Refutation：怎麼用這條規則證明 UNSAT

給你一個 CNF `S = {C₁, C₂, ..., Cₙ}`，要證它 UNSAT。策略：

1. 從 `S` 中兩兩應用 resolution，不斷加入新 clause
2. 如果某次推出 **空 clause `⊥`**（代表 `⊥ ∨ ⊥ = ⊥`），停止 —— 證畢，`S` UNSAT
3. 如果推不出新 clause（所有可能的 resolvent 都已經在 S 裡）而沒空 clause，`S` SAT

「**推出空 clause**」的意思是 pivot 消掉後兩邊都是空。例如：

```
  (p)     (¬p)
  ────────────
       ⊥       ← 空 clause，就是 ⊥
```

空 clause 在 DIMACS 裡寫成一行只有 `0`。它代表「沒有 literal 能讓這條 clause 為真」，違反 CNF 的定義 — 所以 **存在一條空 clause 的 CNF = UNSAT**。

### Refutation 完整例子

```
S = { (p ∨ q), (¬p ∨ r), (¬q ∨ r), (¬r) }
```

一步步 resolve：

```
1. (p ∨ q)       -- given
2. (¬p ∨ r)      -- given
3. (¬q ∨ r)      -- given
4. (¬r)          -- given
5. (q ∨ r)       -- resolve 1, 2 on p
6. (r ∨ r) = (r) -- resolve 5, 3 on q
7. ⊥             -- resolve 6, 4 on r     (空 clause)
```

**UNSAT 證明完成**。把這個過程畫成 DAG 就是 **resolution proof**：

```
      (p ∨ q)   (¬p ∨ r)
           \       /
            (q ∨ r)    (¬q ∨ r)
                 \        /
                  (r)      (¬r)
                    \     /
                      ⊥
```

這是 SAT solver 回給你的東西。Ch 20 我們會把 CDCL 產生的 proof 輸出成 **DRAT 格式**，再用 drat-trim 驗證。

## Resolution 的完備性

**Refutation completeness（命題版）**：如果 CNF `S` UNSAT，那一定存在一個有限的 resolution 證明導出空 clause。

證明概要（略）：用 **consequence semantically entailed → syntactically derivable** 的 induction on variables。

這個定理有兩個含意：

1. **Resolution 對命題 CNF 完備** — 光用這一條規則就夠了，不需要其他。
2. **UNSAT 永遠有有限證明**。所以 UNSAT 是 **recursively enumerable**（RE），而因為 SAT 可以用「試真值表」枚舉，命題邏輯整體是 **decidable**。

但 **Resolution 不 efficient**。某些 UNSAT instance 的最短 resolution proof **指數大** —— 最有名的是 **Pigeonhole Principle `PHP_n^{n+1}`**（n+1 隻鴿子放 n 個洞），1985 Haken 證明它需要 `Ω(2^(n/20))` steps。**CDCL 就算再聰明，碰到 PHP 還是會死**。Ch 13 的 DRUP/DRAT 家族就是為了繞這個限制。

## Unit Resolution（Unit Propagation 的本體）

**Unit clause** = 只有一個 literal 的 clause。比如 `(p)` 或 `(¬q)`。

Unit resolution 是 resolution 的一個特例：**pivot 所在的那條 clause 是 unit clause**。

```
    (p)     (¬p ∨ A)
    ────────────────
          A
```

意義：既然 `p` 必須為真（它是 unit clause），那 `(¬p ∨ A)` 要成立、`A` 裡就必須有一個 literal 為真。所以我們可以 **簡化** `(¬p ∨ A)` 為 `A`。

**這個操作就是 Ch 10 的 unit propagation**，SAT solver 每秒跑幾百萬次。它是 **線性** 的（不像一般 resolution 可能爆炸），而且經常能「連鎖」—— 一條 unit clause 化簡出另一條 unit clause，再化簡出下一條，一路解開 assignment。

**例子**：`S = { (p), (¬p ∨ q), (¬q ∨ r), (¬r ∨ s) }`

```
From (p), (¬p ∨ q)       →  (q)         — q 也變 unit
From (q), (¬q ∨ r)       →  (r)
From (r), (¬r ∨ s)       →  (s)
```

**四個變數全部 propagate 完畢**。SAT。

DPLL 的核心 loop：**做一輪 unit propagation → 卡住 → 猜一個變數 → 再 propagate → ... 碰到矛盾就 backtrack**。

## Pure Literal

還有一個 DPLL 會用的化簡 — **pure literal**：如果某個變數 `p` 在所有 clause 中 **只以同一個極性** 出現（要嘛全是 `p`，要嘛全是 `¬p`），就把那個極性設為 ⊤。

例：`{(p ∨ q), (p ∨ r), (q ∨ ¬r)}`

`p` 只出現正的。設 `p = ⊤`，前兩條 clause 直接滿足，剩 `(q ∨ ¬r)`。

Pure literal 不是 resolution 的特例，是另一條 sound 規則。DPLL 會用，但現代 CDCL **通常不用**（實務上收益低、管理成本高）。**這是個 tradeoff 的例子，記著**：不是每條 sound 規則都值得加進 solver。

## Resolution Refinements

理論家玩了很多 **resolution 的變體**（限制允許哪些 resolve）：

| 名字 | 限制 | 性質 |
|---|---|---|
| General resolution | 無 | 完備 |
| Unit resolution | 至少一邊 unit | **不完備**，只對 Horn clause 完備 |
| Input resolution | 至少一邊是原 clause | 不完備 |
| Linear resolution | 上一步的 resolvent 必須被用 | 完備 |
| Regular resolution | 同 variable 不重複 resolve | 完備 |

**這些在 proof complexity 研究裡很重要**（Cook–Reckhow 1979 開啟的領域），但實作 SAT solver 時你基本只用 general resolution（隱含在 CDCL 的 conflict analysis 裡）和 unit resolution（unit propagation）。

## 預告：CDCL 怎麼用 resolution

Ch 13 你會看到 CDCL 的 conflict analysis **就是一段 resolution**：

1. 碰到衝突 clause `C_conflict`
2. 用它和 **reason clause** resolve，pivot 是最近 propagate 的變數
3. 一路 resolve 回到 **1UIP**（unique implication point，Ch 14 解釋）
4. 得到 **learned clause**，加回 CNF

所以 CDCL 的「學習」**本質上就是在跑 resolution**。每次衝突、每條 learned clause，都是 resolution proof 的一步。

**把這句話背起來**：

> CDCL = 系統化搜索 + resolution-based learning

## 動手練習

1. **手跑 refutation**：證 `{(p ∨ q), (p ∨ ¬q), (¬p ∨ r), (¬p ∨ ¬r)}` UNSAT。你應該能在 5 步內推出空 clause。
2. **故意卡死**：Pigeonhole `PHP_2^3`（3 個鴿子、2 個洞），寫出它的 CNF（6 個變數 `x_{ij}` 代表鴿子 `i` 在洞 `j`），跑 resolution，試著在 20 步內推出空 clause。可以做到 — 但 `PHP_n^{n+1}` 的 general trend 是指數。
3. **Unit propagation 鏈**：`{(a), (¬a ∨ b), (¬b ∨ c), (¬c ∨ d), (¬d)}`。先手 propagate 看發生什麼，應該推出矛盾 — 這就是 Ch 13 的 conflict。

## 常見誤解

- **「Resolution 只能一次 resolve 一對 clause」** — 對。但你可以 **連續** resolve，把結果再跟別的 resolve。CDCL 的 conflict analysis 就是連續 resolve 到 1UIP。
- **「pure literal 是必要的」** — 不必要。CDCL 省略它不影響 completeness。DPLL 加 pure literal 只是讓小題跑快一點。
- **「Unit resolution 完備」** — **不完備**。`{(p ∨ q), (¬p ∨ q), (p ∨ ¬q), (¬p ∨ ¬q)}` UNSAT，但沒 unit clause，unit resolution 動不了。必須 **先做個決策**（猜 `p = ⊤` 或 `⊥`）才能 propagate，這就是 DPLL 的 branching。

## 自我檢核

- [ ] 會手動套 resolution rule
- [ ] 知道 refutation：加 `¬φ`、推空 clause
- [ ] 分得清 resolution（完備）和 unit resolution（只對 Horn 完備）
- [ ] 記住「空 clause = ⊥ = UNSAT」
- [ ] 理解為什麼 CDCL 的 conflict analysis 是 resolution 的應用
- [ ] 知道 PHP 指數下界的存在 — resolution 再強也有打不過的題

Part 0 的最後一章預告一階邏輯 — Part 2 SMT 的 theory 都躲在那後面。一階邏輯整體不可決，但我們只需要它的某些 **fragment**，SMT solver 就是挑這些可決片段來打。

→ [Ch 7 — 一階邏輯預覽](./07-first-order-preview.md)
