# Ch 30 — Array 理論

> 目標：理解 **Array theory** — SMT 用來模擬記憶體、資料結構的 theory。Array 的 axioms 看似簡單（read/write）但實作 lazy axiom instantiation 有細節。這是 program verification 必學的 theory。

## Array 語言

一個 array 是從 **index** domain 映射到 **element** domain 的 function。SMT-LIB 寫法：

```smt2
(declare-const a (Array Int Int))       ; a: Int → Int
(declare-const b (Array (_ BitVec 32) (_ BitVec 8)))   ; b: 32-bit addr → byte
```

**兩個核心運算**：

```
(select a i)     ; a[i]
(store a i v)    ; 新 array a', a'[i] = v, a'[j] = a[j] for j ≠ i
```

Store **不修改 a**，它回傳一個新 array。Array theory 沒有 mutation，是 **functional**。

## McCarthy 的兩條公理 (1962)

John McCarthy 1962 定義 array theory：

```
Axiom 1 (read-over-write-same):
    ∀a i v. select(store(a, i, v), i) = v

Axiom 2 (read-over-write-different):
    ∀a i j v. i ≠ j → select(store(a, i, v), j) = select(a, j)
```

**Axiom 1**：剛寫到的位置讀出來就是那個值。
**Axiom 2**：不同 index 的 store 不影響 read。

這兩條 axiom **定義了 array theory 的所有 semantic**。Solver 就是以這兩條為基礎推理。

## Extensionality

進階 axiom（optional fragment）：

```
Axiom Ext:
    (∀i. select(a, i) = select(b, i)) → a = b
```

**兩個 array 每個 index 都相等 ⇒ 它們相等**。Ext 是 `AX` (Arrays with extensionality) fragment 才有，`QF_AUF` (no ext) 沒有。

Ext 讓 solver 能證 `store(store(a, 1, 5), 1, 5) = store(a, 1, 5)` 這種等式。不加 ext 就推不出。

## 基本推理範例

Assertions:

```
(assert (= (select (store a 1 5) 1) 5))     ; axiom 1 instance
(assert (= (select (store a 1 5) 2) (select a 2)))   ; axiom 2 instance
```

這兩條都是 tautology（axiom 1、2 的 instance）。

更強：

```
(assert (= (select a 1) 3))
(assert (not (= (select (store a 2 7) 1) 3)))
; UNSAT：a[1] = 3 且 2 ≠ 1，store 不影響 a[1]，但 assertion 說 store 後 a[1] ≠ 3
```

Solver 要會推出 `select(store(a, 2, 7), 1) = select(a, 1) = 3`。

## Lazy Axiom Instantiation

**策略**：不一次加所有可能的 axiom instance（無窮），只在**需要時** instantiate。

```
If formula includes select(store(a, i, v), j):
    solver reads: "this is some value x"
    lazy: don't add axiom yet
    if conflict arises involving x:
        add axiom instance as learned lemma:
            (i = j ⇒ x = v) AND (i ≠ j ⇒ x = select(a, j))
```

**Lazy instantiate on demand** 讓 solver 只加有用的 axiom、避免爆。

### 算法流程

```cpp
void assert_lit(Atom a, bool v) override {
    base_theory.assert_lit(a, v);
    // 對每個 store term，在 assertion set 加「如果 i = j 則 select value」等 lemma
    if (a contains (select (store a i v) j)) {
        lazy_queue.push({a, i, j, v});
    }
}

Result check() override {
    // 先 EUF + LIA (base) check
    Result r = base_theory.check();
    if (r == UNSAT) return UNSAT;

    // 從 lazy queue 挑可能有用的 axiom instantiate
    while (lazy_queue has candidates) {
        auto candidate = lazy_queue.pop();
        if (likely_helpful(candidate)) {
            add_clause(axiom_instance(candidate));
        }
    }
    // 重新 check
    return base_theory.check();
}
```

**挑候選的策略** 是演算法核心。常見 heuristic：

- 所有 `select(store(a, i, v), j)` 都 instantiate
- 只 instantiate 跟當前 conflict 相關的
- Skip 明顯 i ≠ j 的

## Array + EUF

Array 常跟 EUF 一起。Array 的 `select` 可以看成 uninterpreted function：

```
select: (Array X Y) × X → Y    ← EUF-style function
```

但 `store` 不是 — 它有 axiom 1/2 的語義。常見做法：EUF solver 處理 `select` 的 congruence，array-specific module 處理 `store` 的 axiom。

```
select(a, i) and select(b, i) with a = b → same class (via EUF congruence)
select(store(a, i, v), j) → axiom 1 or 2 instantiate
```

## Array + BV (QF_ABV)

程式記憶體模型：

```smt2
(declare-const mem (Array (_ BitVec 32) (_ BitVec 8)))
(declare-const addr (_ BitVec 32))
(assert (= (select mem addr) #xFF))
```

BV index、BV element。`QF_ABV` 是 hardware / binary analysis 最常用 logic。Bitwuzla 有 dedicated support。

## Model Generation

Array SAT 的 model 是 function。**Default value + explicit stores**：

```
model(a):
    default: 0
    exceptions: a[1] = 5, a[3] = 7
```

Z3 輸出 lambda 形式：

```
(define-fun a () (Array Int Int)
    (store (store ((as const (Array Int Int)) 0) 1 5) 3 7))
```

`(as const T)` 是常值 array — 所有 index 都映 0。`store` 堆疊修改。

## 難題：Array Property Fragment

加量詞的 array 一般 undecidable，但有 decidable fragment：**Array Property Fragment** (Bradley, Manna, Sipma 2006)：

```
∀i j. i < j → a[i] ≤ a[j]       ; sorted array
∀i. 0 ≤ i < n → a[i] = 0         ; zeroed array
```

一般形式：

```
∀i̅. φ_index(i̅) → φ_value(a[i̅], b[i̅], ...)
```

`φ_index` 只有 index 變數的 linear constraint、`φ_value` 含 select 但不含 index 量詞。這個 fragment 可決，Z3 和 cvc5 有 tactic 處理。

## Diff function extension

某些 solver（Z3 的 `array-diff`）加 `diff(a, b) : Index` — 回傳「a 和 b 第一個不同的 index」。用 extensionality 反推：

```
a ≠ b ⇒ select(a, diff(a, b)) ≠ select(b, diff(a, b))
```

讓 ext 變可用於 QF 範圍。不是所有 solver 支援。

## 效能考量

Array 的效能 **敏感於 store 鏈長度**。`store(store(store(a, 1, 2), 3, 4), 5, 6)` 寫三次，read 要 unroll 三次。工業 program verification instance 常見深 store 鏈，解開成本 O(n × n)。

**Memoization**：對同 `select(a, i)` 要 cache。一個 SMT instance 可能 select 幾千次。

**Chain compaction**：`store(store(a, 1, x), 1, y) → store(a, 1, y)`（後 write 覆蓋前 write）。Preprocessing rewrite。

## 動手練習

1. **驗證 McCarthy axiom**：寫 SMT instance `¬axiom1` 和 `¬axiom2`、丟 Z3、應該 UNSAT（那兩條是永真）。
2. **Ext 必要性**：`¬(a = b) ∧ (∀i. select(a, i) = select(b, i))`，不用 ext 為何 solver 無法判 UNSAT？用 AX logic 跟 AUFLIA 對比。
3. **Memory model**：用 QF_ABV 寫：「address `0x1000` 和 `0x1001` 的 byte 加起來 = `0xFF`、address `0x1002` = `0x42`」。問 solver 有沒有 mem 值滿足。
4. **Sorted array**：用 AUFLIA 寫量詞版「a sorted ∧ a[5] = 10 ∧ a[3] = ?」的 constraint。

## 常見誤解

- **「store 會修改原 array」** — 不會。Array theory functional。`store(a, i, v)` 回新 array。
- **「select 跟 EUF function 一樣」** — 幾乎一樣，但 select 配合 store 有 axiom。
- **「Array + BV 會炸」** — 不一定。Lazy instantiation 控制好，一般 instance 跟 EUF + BV 差距 2×。
- **「Ext 總是可用」** — 不對。`QF_AUFLIA` 和 `AUFLIA` 區別：加量詞的才有 ext fragment。

## 自我檢核

- [ ] 寫得出 SMT-LIB 的 select / store
- [ ] 背得住 McCarthy axiom 1 和 2
- [ ] 懂 extensionality 的作用
- [ ] 懂 lazy axiom instantiation 思路
- [ ] 懂 QF_ABV 在記憶體建模的應用
- [ ] 知道 array property fragment 的存在

五個 theory (EUF / LRA / LIA / BV / Array) 都講完，下一章處理它們 **如何一起工作** — **Nelson–Oppen theory combination**。這是 SMT 最優雅的結果之一。

→ [Ch 31 — Theory combination：Nelson–Oppen](./31-theory-combination.md)
