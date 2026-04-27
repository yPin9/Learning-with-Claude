# Ch 9 — Davis–Putnam 演算法

> 目標：認識 SAT 的第一個非 trivial 演算法（Davis–Putnam 1960），看懂它怎麼用 resolution 逐步消除變數、為什麼中間結果會指數膨脹、以及為什麼 DPLL（下一章）用 backtracking 取代它。這章重 **歷史 + 教訓**，code 占比低。

## 1960 年的 SAT 演算法

Martin Davis 和 Hilary Putnam 1960 年發表《A Computing Procedure for Quantification Theory》，是 **第一個發表的 SAT 演算法**。那時電腦還在穿孔卡片時代，他們的目標是自動化一階邏輯的定理證明 — SAT 是其中一個子問題。

演算法主意：**逐步消掉變數**，每次消掉一個變數後 CNF 還是 equi-satisfiable。全部消完剩下的如果是空（`⊤`）就 SAT；剩下空 clause 就 UNSAT。

## DP 的三條規則

DP 每一輪選一個變數 `x`，套三條規則之一，直到沒變數：

### Rule 1 — Unit propagation

看 Ch 6。有 unit clause `{l}` 時：

- 移除所有 **包含 `l`** 的 clause（它們已 satisfied）
- 從所有 clause 中刪除 `¬l`

**這不消變數，但簡化 CNF**。DP 的每輪都先把 unit propagation 做飽。

### Rule 2 — Pure literal

看 Ch 6。變數 `x` 只以正極性出現（或只以負極性）：

- 設 `x = ⊤`（或 `⊥`），移除所有包含那個 literal 的 clause
- **變數 `x` 從此消失**

### Rule 3 — Variable elimination（DP 的招牌）

這是 DP 的真正主角。選一個變數 `x`，不是猜測它的真值，而是 **直接消掉**：

1. 收集所有包含 `x` 的 clause `{C₁, ..., Cₖ}`
2. 收集所有包含 `¬x` 的 clause `{D₁, ..., Dₘ}`
3. 把每對 `(Cᵢ, Dⱼ)` 做 resolution（pivot 是 `x`），得到新 clause
4. 加入所有新 clause，**刪除原本含 `x` 或 `¬x` 的 clause**

消完 `x` 之後 CNF 裡再也沒有 `x`，而且 **equi-satisfiable**（resolution sound）。

### 終止條件

- CNF 剩空（沒有 clause）⇒ SAT
- CNF 有空 clause `⊥` ⇒ UNSAT

## 例子跑一遍

`S = { (p ∨ q), (¬p ∨ r), (¬q ∨ r), (¬r) }`

**Round 1**：unit propagation 跑 `(¬r)` → `r = ⊥`：

```
(p ∨ q)     保留
(¬p ∨ r)    → (¬p)    (¬p ∨ ⊥ = ¬p)
(¬q ∨ r)    → (¬q)
(¬r)        → 移除（satisfied）

S = { (p ∨ q), (¬p), (¬q) }
```

**Round 2**：unit propagation `(¬p)` → `p = ⊥`：

```
(p ∨ q)     → (q)   (⊥ ∨ q = q)
(¬p)        → 移除
(¬q)        保留

S = { (q), (¬q) }
```

**Round 3**：unit propagation `(q)` → 跟 `(¬q)` 矛盾，推出 **空 clause** ⇒ UNSAT。

這個例子裡 unit propagation 就解決了，沒動到 variable elimination。

## Variable Elimination 的恐怖例子

`S = { (p ∨ a), (p ∨ b), (p ∨ c), (¬p ∨ d), (¬p ∨ e), (¬p ∨ f) }`

消 `p`：含 `p` 的 3 條跟含 `¬p` 的 3 條兩兩 resolve，得 `3 × 3 = 9` 條新 clause：

```
(a ∨ d), (a ∨ e), (a ∨ f),
(b ∨ d), (b ∨ e), (b ∨ f),
(c ∨ d), (c ∨ e), (c ∨ f)
```

原本 6 條、每條 2 literal，現在 9 條、每條 2 literal。**條數增加了**。

**最壞情況**：含 `x` 的 clause 有 `k` 條、含 `¬x` 的有 `m` 條，消掉 `x` 產生 `k × m` 條新 clause。

**n 個變數依序消**：每一步 clause 數量都可能平方成長。原 CNF 有 `m` 條、消完可能有 `m^(2^n)`。**指數爆炸**，在實務上毫無用處。

## 為什麼 DP 還是重要

- 它是 **第一個給出 SAT 算法** 的演算法，定義了「把變數消掉」這個思路
- **Unit propagation + pure literal** 這兩個 rule 到今天 DPLL、CDCL 都在用
- 它啟發了 DPLL（1962，Davis–Logemann–Loveland）— **DP 爆在 variable elimination 的 resolution，DPLL 把它換成 branching + backtracking**
- 它是 **Bounded Variable Elimination（BVE）** 的祖先 —現代 SAT solver 的 **preprocessing** 會用受限版的 DP（Ch 17）

## DP vs DPLL：一圖看懂

```
 DP (1960)               DPLL (1962)
─────────────         ─────────────
 Unit prop             Unit prop
 Pure literal          Pure literal
 Var elim              Pick 變數 → 分兩支
 (resolve 爆)          case x = T: recurse
                       case x = F: recurse
                       backtrack 回合併
```

DPLL 的天才在於：**不真的消變數，改成 branching**。分兩支遞迴、每支 CNF 變簡化（不是膨脹）、UNSAT 才 backtrack。空間只跟 DFS 深度成正比，**O(n)** space 而非 DP 的 O(2^n) clause。

## Code：簡化版 DP（能跑但會爆）

```cpp
#include <vector>
#include <algorithm>
#include <optional>

using Lit = int;
using Clause = std::vector<Lit>;
using CNF = std::vector<Clause>;

bool has_empty(const CNF& s) {
    for (const auto& c : s) if (c.empty()) return true;
    return false;
}

std::optional<Lit> find_unit(const CNF& s) {
    for (const auto& c : s) if (c.size() == 1) return c[0];
    return std::nullopt;
}

void apply_unit(CNF& s, Lit l) {
    s.erase(std::remove_if(s.begin(), s.end(), [&](const Clause& c) {
        return std::find(c.begin(), c.end(), l) != c.end();
    }), s.end());
    for (auto& c : s) c.erase(std::remove(c.begin(), c.end(), -l), c.end());
}

bool dp_solve(CNF s) {
    while (true) {
        if (s.empty()) return true;
        if (has_empty(s)) return false;
        if (auto l = find_unit(s)) { apply_unit(s, *l); continue; }

        // Variable elimination：挑第一個變數消掉
        Lit pivot = std::abs(s[0][0]);
        std::vector<Clause> pos, neg, rest;
        for (const auto& c : s) {
            bool has_pos = std::find(c.begin(), c.end(), pivot) != c.end();
            bool has_neg = std::find(c.begin(), c.end(), -pivot) != c.end();
            if (has_pos) pos.push_back(c);
            else if (has_neg) neg.push_back(c);
            else rest.push_back(c);
        }
        // Resolve 兩兩
        for (const auto& c1 : pos) {
            for (const auto& c2 : neg) {
                Clause resolvent;
                for (Lit l : c1) if (l != pivot) resolvent.push_back(l);
                for (Lit l : c2) if (l != -pivot && std::find(resolvent.begin(), resolvent.end(), l) == resolvent.end())
                    resolvent.push_back(l);
                // Tautology check：同時含 x 和 -x 就丟掉
                bool taut = false;
                for (Lit a : resolvent) for (Lit b : resolvent) if (a == -b) { taut = true; break; }
                if (!taut) rest.push_back(std::move(resolvent));
            }
        }
        s = std::move(rest);
    }
}
```

**跑個幾題你會發現**：變數 ~15 之後時間與記憶體就開始失控。這不是 bug，是 DP 的天生性質。

## DP 的教訓

1. **Resolution 強但可以爆** — 不是所有 sound 的 rule 都能直接用
2. **消變數這個操作本身 O(kₘ)**，一個 `n` 變數公式最壞 `O(2^n)` space
3. **不做 branching 的演算法幾乎都爆** — SAT 的核心是搜索，變數 elimination 只能作 preprocessing 的輔助

這三個教訓在 Ch 13–17 你會反覆看到 — 現代 SAT solver 的每個技巧都是對這些教訓的回應。

## 動手練習

1. **用 DP 手解**：`{(p ∨ q), (p ∨ ¬q), (¬p ∨ r), (¬p ∨ ¬r)}`。先試 unit prop（沒 unit）、再 pure literal（沒有）、然後 variable elimination（挑 `p`）。跟你 Ch 6 用 resolution refutation 的比較。
2. **爆給你看**：寫一個 CNF generator，產 `n` 個 `(pᵢ ∨ a)` 和 `n` 個 `(¬pᵢ ∨ b)`，消掉每個 `pᵢ` 會產 `n²` 條。當 `n = 10` clause 從 20 變 100，繼續到 `n = 20` 就 400。對照天真分配 CNF 的 `2^n`，DP 有結構才沒那麼快爆，但還是爆。
3. **觀察 pure literal 幫多少**：Ch 8 寫的 brute force 搭 DP 的 pure literal 去簡化輸入，看多少 instance 的 clause 直接消光。

## 常見誤解

- **「DP 跟 DPLL 是一樣的」** — **不一樣**。DP 做 variable elimination 透過 resolution；DPLL 做 branching 透過 backtracking。兩個演算法、不同空間複雜度。
- **「DP 已經被完全淘汰」** — 不完全。現代 solver 的 preprocessing（Ch 17）裡的 **Bounded Variable Elimination** 就是 DP 的受限版 — 只在 resolvent 數 ≤ 某 threshold 才消。還是有用。
- **「Unit propagation 是 DPLL 獨有」** — 錯。DP 也用，只是 DP 用完 unit prop 後做 variable elim、DPLL 做 branching。

## 自我檢核

- [ ] 說得出 DP 的三條規則：unit prop、pure literal、variable elimination
- [ ] 知道為什麼 variable elimination 會指數爆炸
- [ ] 說得出 DP 和 DPLL 的關鍵差別（elim vs branch）
- [ ] 理解為什麼現代 solver 還在用 DP 的變形（BVE）但只作 preprocessing

下一章是整個 SAT 故事的轉折 — **DPLL**。它用 branching 換掉 DP 的 resolve，把空間從指數降到線性。從 DPLL 之後的所有 solver（含 MiniSat、CaDiCaL）都在這個框架上優化。

→ [Ch 10 — DPLL 演算法](./10-dpll.md)
