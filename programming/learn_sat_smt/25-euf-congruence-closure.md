# Ch 25 — EUF 與 congruence closure

> 目標：搞懂 **EUF theory (Equality + Uninterpreted Functions)** 跟它的核心演算法 **congruence closure**。EUF 是 SMT 第一個必學 theory、最優雅、union-find 加一點點 congruence rule 就搞定。Ch 26 實作它。

## EUF 是什麼

**EUF** 的語言：

- 變數：`x, y, z, ...`
- Uninterpreted functions：`f, g, h, ...`（沒有固定語意的函數）
- `=`（唯一 predicate）
- `¬` 在 atom 上 — 所以 `a ≠ b` 是 legal atom

**語意約束**：

- `=` 是 equivalence relation（reflexive, symmetric, transitive）
- **Congruence axiom**：`x₁ = y₁ ∧ x₂ = y₂ ∧ ... ∧ xₙ = yₙ ⇒ f(x₁, ..., xₙ) = f(y₁, ..., yₙ)`

**函數本身沒有內部結構** — solver 不知道 `f` 做什麼，只知道 **congruence**。

### 為什麼叫「uninterpreted」

對比 LRA 的 `+` 是 **interpreted**（有固定解讀：就是加法）。EUF 的 `f` 是 **uninterpreted** — 可以是任何函數，solver 只用 congruence 推理。

### 為什麼有用

- **程式驗證**：`f(x) = f(y)` 當 `x = y` 時成立，即使 `f` 是未知函式（e.g., 使用者自定 hash function）
- **硬體驗證**：cache 查找、wire 等價性
- **Abstract reasoning**：先用 EUF 抽象再細化（cegar-style 驗證）

## Congruence Closure：核心演算法

給一組 equalities（e.g., `a = b, b = c, f(a) = g(a)`），問：「兩個 term 一定相等嗎？」

**Congruence closure** 一次性算出「所有被蘊含的相等關係」，然後查表回答。

### 直覺：Union-Find 加一點東西

- 基本 union-find：處理 `a = b, b = c` 型式，結論 `a = c`
- Congruence: 額外 rule：如果 `a ~ b`（同 class）且 `c ~ d`，那 `f(a, c) ~ f(b, d)`

所以演算法 = union-find + congruence propagation。

## 演算法步驟

Input: equalities `E = {l₁ = r₁, l₂ = r₂, ..., lₙ = rₙ}`。

```
1. 建 term DAG，每個 sub-term 一個 node
2. 初始化 union-find，每個 node 一個 class
3. 對每個 equality (l = r) in E：
    merge(l, r)
4. Merge 時，除了 union-find merge，還要：
    - 找所有用到 merged class 的 function term
    - 檢查它們是否現在 congruent
    - congruent → 遞迴 merge 它們
5. 最後查詢：a = b iff find(a) == find(b)
```

**「merge 時檢查 congruence」是關鍵**。這讓 EUF 的 closure 自動算出來。

## 範例

Equalities: `f(a, b) = c, a = d, b = e, f(d, e) = g`

建 term DAG 含 node: `a, b, c, d, e, g, f(a,b), f(d,e)`。

```
Merge a = d:    {a, d}, {b}, {c}, {e}, {g}, {f(a,b)}, {f(d,e)}
Merge b = e:    {a, d}, {b, e}, {c}, {g}, {f(a,b)}, {f(d,e)}
```

**到這裡 congruence 觸發**：`f(a, b)` 和 `f(d, e)` 兩個 function term。它們 argument 現在同 class：

- `a ~ d` ✓
- `b ~ e` ✓

所以 `f(a, b) ~ f(d, e)`。Merge 它們：

```
{a, d}, {b, e}, {c, g, f(a,b), f(d,e)}   ← c 因為 = f(a,b)、g 因為 = f(d,e)，
                                         合成一個 class
```

Query `c = g`？Find 同 class → **yes**。

## 找「用到這個 class 的 function term」

每個 class 維護 **use list**（occur list、parent list）：

```cpp
struct EClass {
    int representative;
    std::vector<FuncTerm*> uses;   // 含有這個 class 當某個 argument 的 function term
};
```

Merge 兩個 class 時，看 use list、檢查 congruence、必要時再 merge。

## Congruence Check

兩個 function term `f(a₁, ..., aₙ)` 和 `g(b₁, ..., bₙ)` congruent iff：

1. `f == g`（同一個 function symbol）
2. `arity` 相同
3. 對每個 `i`：`find(aᵢ) == find(bᵢ)`

實作快速做法：**hash signature**，為每個 function term 算 `(f_id, find(a₁), find(a₂), ...)` 的 hash。hash 相同 ⇒ congruent。

```cpp
uint64_t term_signature(FuncTerm* t) {
    uint64_t h = hash(t->func_id);
    for (EClass* c : t->args)
        h = combine(h, find(c));
    return h;
}
```

Merge 兩個 class 後，對 use list 裡的每個 term 重算 sig，找到相同 sig 的 term pair、遞迴 merge。

## 處理 Disequalities

假設 input 包括 `a ≠ b`。怎麼查？

每次 merge 完、check 所有 disequality：

```cpp
void check_disequalities() {
    for (auto [u, v] : disequalities) {
        if (find(u) == find(v)) {
            report_conflict(u, v);
            return UNSAT;
        }
    }
}
```

**更有效率**：merge 時只 check **跟剛 merge class 相關** 的 disequality。

## Explanation (Proof Trail)

SMT 要求 explain — solver 說 UNSAT 時要給 minimal 原因。EUF 的 explanation 機制：

### Proof Forest

每個 union-find 的 merge 加一條邊：`a ↔ b` 被 merge，加邊 `a — b`（labelled by reason）。這棵 forest 記錄「每個 equality 的來源」：

- **Input equality**：來自 input
- **Congruence**：因 `f(a,c) ~ f(b,d)` 而 merge，reason 是 `a~b` 和 `c~d`

### Explain Path

Query：「為什麼 `x ~ y`？」

1. 找 forest 上 x 到 y 的路徑
2. 收集 edge 的 reason
3. Congruence 型 edge 遞迴：reason 是 argument 的 equality chain

Nelson-Oppen 1980 原版演算法 O(n²) explain。**Nieuwenhuis-Oliveras 2005** 改進到 O(n log n)。

### Example Explain

Merge order: `a=b, b=c, f(a) and f(c) merge (congruence)`.

Query: why `f(a) ~ f(c)`?

Answer: congruence on `a ~ c`, which is `a = b and b = c` (input equalities).

Minimal reason: `{a = b, b = c}`.

## Theory Propagation

當 assert `a = b`：

- Merge class 後，看 disequalities。若 `a' ≠ b'` 有 `find(a') == find(b')` ← UNSAT
- 否則，**其他 atom 可能變 determined**：比如 formula 裡有 `f(a) = c` 這個 atom，merge 後可能確定它真/假

```cpp
for (atom in known_atoms) {
    if (atom is EQ(t1, t2)) {
        if (find(t1) == find(t2)) propagate(atom, true);
    }
    if (atom is DISEQ(t1, t2)) {
        if (find(t1) == find(t2)) propagate(atom, false); // 矛盾
    }
}
```

掃描所有 atom 會 O(n)。**Hash table** 更快：key `(find(t1), find(t2))`，每次 merge 後 rehash。

## Push / Pop

- `push()`: 記當前 undo_stack 大小
- Merge 時：push undo entry (舊 representative)
- Pop: 反向 undo，還原 union-find

```cpp
struct Undo {
    int node_id;    // 被 merge 成 representative 的節點
    int old_parent;
};
```

Union-find path compression 要特別處理 — 不能 compress otherwise pop 破壞。用 **lazy / weighted union-find** 不 compress，size 略慢但 pop-able。

## Complexity

- Merge: **O(log n)** amortized (with union-by-rank)
- Query: **O(log n)**
- Total over n equalities: **O(n log n)**
- Explain: **O(n log n)** (Nieuwenhuis-Oliveras)

EUF 是 SMT 最快的 theory solver 之一，這些複雜度讓它容易嵌入 SAT 的 inner loop。

## 跟 DPLL(T) 的整合

每個 Boolean 變數對應一個 EUF atom (`EQ(t1, t2)` 或 `DISEQ(t1, t2)`)。SAT assigns `b = T`，theory 解讀成 assert 對應 equality 或 disequality。

```cpp
void assert_lit(Atom a, bool value) override {
    if (a.is_equality()) {
        if (value) merge(a.lhs, a.rhs);
        else disequalities.push_back({a.lhs, a.rhs});
    }
}

Result check() override {
    // merge 已經在 assert_lit 裡 incremental 做
    // check 只掃 disequalities
    return any_diseq_violated() ? UNSAT : SAT;
}
```

**簡單又強大**。Ch 26 完整 C++ 實作。

## 動手練習

1. **紙上做 congruence closure**：`a = b, c = d, f(a, c) ≠ f(b, d)`. Merge 幾次、發現 congruence、報 UNSAT。手寫 explain chain。
2. **判斷 EUF SAT/UNSAT**：
   - `f(f(x)) = x ∧ f(f(f(x))) = x ∧ f(x) ≠ x` → SAT or UNSAT?（SAT，`f` 可設為 identity）
   - `f(a) = a ∧ g(f(a)) ≠ g(a)` → UNSAT（congruence 強制 g(f(a)) = g(a)）
3. **Union-find 基本實作**：寫 `class UnionFind` with `find, union_rank, undo`，測試 basic operation。為下章鋪路。

## 常見誤解

- **「f 是可變函數」** — 不是。**Uninterpreted** = 不知道是什麼函數，但一旦 argument 確定、回傳值就固定。
- **「EUF 能推出 ∀x. f(x) = x」** — 不能。EUF 只處理 ground equality (`f(a) = a` 這種，沒 `∀`)。有量詞要用 Ch 32 E-matching。
- **「Congruence closure 是 graph algorithm」** — 是 tree forest 上操作，不是任意 graph。Union-find 本質是 forest。
- **「EUF 只處理 function application」** — 加 predicate 也行 (`P(x)` 當成 `EQ(P(x), true_const)`)。很多實作這樣做。

## 自我檢核

- [ ] 說得出 EUF 的語言（= 和 uninterpreted function + congruence axiom）
- [ ] 懂 congruence closure 的核心：union-find + merge-time congruence check
- [ ] 會手動跑 congruence closure example
- [ ] 寫得出 term signature 的 hash 算法
- [ ] 懂 explain 機制（proof forest + path）
- [ ] 理解 O(n log n) complexity
- [ ] 知道為什麼 EUF 是 DPLL(T) 最 friendly 的 theory

下一章把上面的演算法寫成 **C++ 實作**。這是你的第一個 theory solver，為 Part 2 所有後續 theory 建立範本。

→ [Ch 26 — 實作 EUF theory solver](./26-implement-euf.md)
