# Ch 10 — DPLL 演算法

> 目標：搞懂 DPLL（Davis–Putnam–Logemann–Loveland, 1962），它把 Ch 9 的 DP 從**消變數 + resolve** 改成 **猜變數 + backtrack**，空間從指數降到線性。CDCL 就是 DPLL 加上「從失敗中學習」，所以這章是 Part 1 剩下所有章節的地基。

## DPLL 的核心想法

一句話：

> 選一個變數猜值（branching），加進 assignment、做 unit propagation 推結論。推成功就繼續；推出矛盾就 backtrack、換猜反值；兩邊都不行就真 UNSAT。

**相比 DP**：DP 試圖 **解析地消掉變數**（resolution），會 clause 爆炸。DPLL 改成 **搜索式地試值**，每條路徑只留一份 assignment，空間 `O(n)`。

## Assignment：核心資料結構

DPLL 的狀態是一個 **partial assignment** — 每個變數目前的狀態：

```
x₁ = ⊤, x₂ = ⊥, x₃ = ⊤, x₄ = ?, x₅ = ? ...
```

`?` 表示尚未指派。每條 clause 在當前 assignment 下有三種狀態：

- **Satisfied**：至少一個 literal 已為 ⊤
- **Falsified（conflict）**：所有 literal 都為 ⊥（也就是 empty 在 current assignment 下）
- **Unit**：恰有一個 literal 未指派，其他全為 ⊥ — 那個未指派 literal 被 **forced** 為 ⊤
- **Unresolved**：多個未指派 literal，還沒結論

DPLL 就在這四種狀態間移動。

## 偽代碼

```
DPLL(CNF S, Assignment A):
    A ← UnitPropagate(S, A)
    if conflict detected: return UNSAT
    if 所有 clause satisfied: return SAT with A

    (optional) A ← PureLiteralAssign(S, A)

    pick variable x unassigned
    if DPLL(S, A ∪ {x = ⊤}) == SAT: return SAT
    if DPLL(S, A ∪ {x = ⊥}) == SAT: return SAT
    return UNSAT
```

四個關鍵動作：

1. **UnitPropagate**：掃 clauses，若有 unit clause 就強制指派，連鎖直到沒 unit
2. **Conflict detection**：任何 clause 在當前 `A` 下 falsified → conflict
3. **PureLiteralAssign**（選配）：變數只以一個 polarity 出現，直接設
4. **Branching**：挑一個變數試 ⊤ / ⊥

## 走一個範例

`S = { (p ∨ q), (¬p ∨ r), (¬q ∨ r), (¬r) }`

```
初始 A = {}
  UnitPropagate：找到 (¬r)，設 r = ⊥
  A = {r: ⊥}

  Unit prop 繼續：
    (¬p ∨ r) 在 r=⊥ 下簡化為 (¬p) — unit
    (¬q ∨ r) 在 r=⊥ 下簡化為 (¬q) — unit
    設 p = ⊥, q = ⊥
    A = {r: ⊥, p: ⊥, q: ⊥}

  現在看 (p ∨ q)：在 A 下 p=⊥、q=⊥、所有 literal falsified → CONFLICT

回溯路徑：沒有 branch 可回（從頭就是 unit prop 鏈），所以 UNSAT。
```

沒經過任何 branching，純 unit propagation 就決了。**大量 real-world instance 是這樣**，這正是 unit propagation 在 CDCL 佔 80% runtime 的原因。

## 需要 branching 的範例

`S = { (p ∨ q), (¬p ∨ q), (p ∨ ¬q), (¬p ∨ ¬q) }`

```
初始 A = {}
  沒 unit clause（每條 2 literal）
  沒 pure literal（p、q 各有正負）
  Branching：挑 p = ⊤

  分支 p = ⊤：
    (p ∨ q)      satisfied
    (¬p ∨ q)     → (q) unit
    (p ∨ ¬q)     satisfied
    (¬p ∨ ¬q)    → (¬q) unit
    衝突：q 被同時要求 ⊤ 和 ⊥ → CONFLICT

  回溯、試 p = ⊥：
    (p ∨ q)      → (q) unit
    (¬p ∨ q)     satisfied
    (p ∨ ¬q)     → (¬q) unit
    (¬p ∨ ¬q)    satisfied
    同樣衝突 → CONFLICT

  兩支都失敗 → UNSAT
```

## Unit Propagation 的寫法

最核心的 inner loop。**這段 code 你會看無數次**：

```cpp
enum class Value { Unassigned, True, False };
using Assignment = std::vector<Value>;  // 1-indexed，[0] 不用

Value lit_value(Lit l, const Assignment& a) {
    Value v = a[std::abs(l)];
    if (v == Value::Unassigned) return v;
    bool truth = (v == Value::True);
    return ((l > 0) == truth) ? Value::True : Value::False;
}

// 回傳 false 表示衝突
bool unit_propagate(const CNF& cnf, Assignment& a) {
    bool changed = true;
    while (changed) {
        changed = false;
        for (const auto& c : cnf.clauses) {
            int unassigned = 0;
            Lit last_unassigned = 0;
            bool satisfied = false;
            for (Lit l : c) {
                Value v = lit_value(l, a);
                if (v == Value::True) { satisfied = true; break; }
                if (v == Value::Unassigned) { unassigned++; last_unassigned = l; }
            }
            if (satisfied) continue;
            if (unassigned == 0) return false;   // 所有 literal 都 false → conflict
            if (unassigned == 1) {
                // unit clause，強制 last_unassigned 為 true
                int v = std::abs(last_unassigned);
                a[v] = (last_unassigned > 0) ? Value::True : Value::False;
                changed = true;
            }
        }
    }
    return true;
}
```

**這個版本 O(N × M)**（遍歷所有 clause、每次可能重跑）。Ch 12 的 watched literals 會把它加速到 **amortized O(1) per propagation**。現在先用這版把演算法走通。

## 完整 DPLL 骨架

```cpp
bool dpll(const CNF& cnf, Assignment& a) {
    // 保存 unit prop 前的狀態，衝突時要 rollback
    Assignment backup = a;
    if (!unit_propagate(cnf, a)) { a = backup; return false; }

    // 檢查是否全部指派完成
    bool all_done = true;
    for (int v = 1; v <= cnf.num_vars; v++)
        if (a[v] == Value::Unassigned) { all_done = false; break; }
    if (all_done) return true;

    // 挑下一個變數（這裡用最笨的：第一個未指派）
    int branch_var = 1;
    while (a[branch_var] != Value::Unassigned) branch_var++;

    // 試 true
    a[branch_var] = Value::True;
    if (dpll(cnf, a)) return true;

    // 試 false
    a = backup;
    a[branch_var] = Value::False;
    if (dpll(cnf, a)) return true;

    // 兩邊都失敗
    a = backup;
    return false;
}
```

這就是 **能跑的 DPLL**。Ch 11 把它包成完整程式、接 DIMACS parser，端出一個能跟 MiniSat 小題對打的 v1。

## 正確性與終止性

### Soundness
DPLL 若回 SAT，給的 `A` 讓每條 clause 為 ⊤（演算法明確檢查）。

### Completeness
Unit propagation 和 branching 合起來窮舉所有 assignment — 最壞 `2^n` 次。所以 UNSAT 一定回 UNSAT。

### 終止
遞迴每次固定一個變數，最多 `n` 層深。每層兩支，最壞 `2^n` 個 leaf。**保證終止**。

## 三個關鍵設計選擇

DPLL 的骨架固定，但 **每個細節都是研究題**：

### 1. 挑哪個變數 branching（Decision Heuristic）

最簡單是「第一個未指派」或「出現頻率最高」。但好的 heuristic 能讓搜索縮 **幾個數量級**。Ch 15 講 VSIDS。

### 2. 先試 true 還是 false（Phase Selection）

直覺上不重要，但現代 solver 用 **phase saving**（記住上次 assign 的 phase）會比 random 快。Ch 15 也細講。

### 3. 衝突後怎麼 backtrack

DPLL 的天真版：**chronological backtracking**（回到最近的 branch）。這是 DPLL 的致命弱點 — 可能反覆在同樣的子問題上失敗。

**CDCL 的突破**：**non-chronological backjumping** + 從衝突學 clause。Ch 13–14 是重頭戲。

## DPLL 為什麼還不夠

DPLL 跑小題很快，一到工業規模（> 1000 變數）會死在這些地方：

1. **重複錯誤**：chronological backtrack 會讓 solver 在同個錯誤上撞幾千次
2. **沒學習**：每次遇到衝突只知道「這支死了」，但不記住 **為什麼** 死，所以其他地方遇到等價問題還會再死
3. **Naive unit prop**：O(N × M) 掃完整 CNF，百萬 clause 會卡爆

CDCL 把 **1、2、3 全部解掉** — 用 implication graph 分析衝突、學 clause、backjump 到真正相關的 level、用 two watched literals 讓 unit prop 近 O(1) amortized。你會在 Ch 12–16 一個一個看到。

## Partial vs Total Assignment

DPLL 原版做 **total assignment**（所有變數都定）。但實作上常用 **partial**：只要 **當前 assignment 讓每條 clause satisfied** 就能回 SAT，剩下變數隨便。

```cpp
// 改寫上面的 all_done 判斷
bool all_satisfied = true;
for (const auto& c : cnf.clauses) {
    bool sat = false;
    for (Lit l : c) if (lit_value(l, a) == Value::True) { sat = true; break; }
    if (!sat) { all_satisfied = false; break; }
}
if (all_satisfied) return true;
```

**好處**：早終止。某些 instance partial assignment 100 變數就 satisfied，total 還要把剩 900 個變數走完。CDCL 一律用 partial。

## 動手練習

1. **手跑 DPLL**：`{(p ∨ q ∨ r), (¬p ∨ q), (¬q ∨ r), (¬r)}`。手推 unit prop + branching，跟 Ch 6 resolution refutation 對照 — 你會看到兩個方向做同一件事。
2. **Decision 影響**：上題改成從 `r` 開始 branch，比從 `p` 開始 branch，看哪個先到答案。直覺：挑「出現最多次」的變數通常較快 — 這是 DLIS heuristic（原始 DPLL 論文提的），VSIDS 的前身。
3. **故意做錯**：把 `unit_propagate` 的 rollback（`a = backup`）拿掉，觀察衝突後 assignment 留著髒資料導致後面 branching 出錯。這會是你 Ch 11 實作時第一個踩的坑。

## 常見誤解

- **「DPLL 裡 pure literal 一定要做」** — 錯。Pure literal 對結果正確性不必要，只是加速。現代 CDCL 通常不做（維護成本 > 收益）。
- **「DPLL 的 branching 順序不影響正確性」** — 對，**但影響速度可達指數級**。這是 Ch 15 啟發式的舞台。
- **「DPLL 只能回傳 SAT/UNSAT」** — 錯。SAT 時回 model、UNSAT 時配合改裝能回 proof。Ch 20 會看 DRAT proof 怎麼產生。

## 自我檢核

- [ ] 寫得出 DPLL 的偽代碼
- [ ] 區分 DP（消變數）vs DPLL（猜變數）
- [ ] 寫得出 naive unit propagation 的 C++ 實作
- [ ] 知道 partial vs total assignment 的差異
- [ ] 說得出 DPLL 三個關鍵設計選擇（decision / phase / backtrack）
- [ ] 知道 DPLL 對工業規模不夠的 3 個原因

下一章我們把這個骨架寫成 **v1 solver**：完整 C++ 程式、吃 DIMACS、輸出標準 SAT 格式，能跟 MiniSat 對小題。這是你的第一個「自己的」SAT solver。

→ [Ch 11 — 實作 SAT solver v1：乾淨的 DPLL](./11-implement-dpll.md)
