# Ch 5 — Path constraint 如何轉成 SMT query

> 目標：把 PC 到 SMT 這段介面看透。講完你要能說出 symex engine 在 SMT 層做的三件優化：incremental、independence、concretization。

## 基本對映

你有：

```
σ_reg = {rax → α + 5}
σ_mem = {0x1000 → β}
PC    = (α > 10) ∧ (α < 20)
```

你想問的問題是：「現在這條 path 可達嗎？」 → 變成 SMT query：

```
(check-sat-assuming (α > 10) (α < 20))
```

如果回 `sat`，加上 model `α = 15`，你就拿到**一個能跑到這條 path 的具體 input**。如果 `unsat`，說明這條 state 其實跑不到（前面某個 branch decision 本來就矛盾）、engine 把它丟掉。

這就是 symex 與 SMT 的介面 core。但真實工具做很多優化，否則根本跑不動。

## 工程問題一：每個 branch 都查一次，很慢

一條 state 走 N 個 branch，至少 2N 次 SMT call（每 branch 查 true 與 false 的可達性）。真實 target N 輕易到 10^4。SMT 從 scratch 每次都重算，不 OK。

### 解法：Incremental SMT（push/pop）

Z3、cvc5 都支援 incremental solving：

```
(push)                 ; 開新 scope
(assert (α > 10))
(check-sat)            ; 有 previous state 可以重用
(push)
(assert (α < 20))
(check-sat)            ; 繼續擴展
(pop)                  ; 退回來
(assert (α > 30))
(check-sat)
```

Solver 保留之前學到的 lemma、propagation state。**實務上快 5–50 倍**。

angr 內部 `state.solver` 是 z3 incremental session，每次 `add_constraints` 就 assert 進去，fork 時 clone 一份。KLEE 類似但有自己的 IncrementalSolver wrapper。

Caveat：不是所有 solver backend 都用 incremental — KLEE 的 `KQuery` format 把 constraint 整包重送，適合 stateless solving。Trade-off 是 solver 可以 heavily optimize single-shot query，代價是 lose 了 incremental 的重用。

## 工程問題二：大多數 constraint 互相無關

PC 常常長這樣：

```
PC = (a > 0) ∧ (a < 100)                 # 關於 a 的
     ∧ (b[0] = 'h') ∧ (b[1] = 'i')       # 關於 b 的
     ∧ (c != 0)                           # 關於 c 的
```

你現在要 check `d == 5` 是否 feasible。`d` 跟 `a, b, c` 毫無關係。**把無關的 clause 丟進去只是白白拖慢 SMT**。

### 解法：Constraint independence / splitting

把 PC 看成 graph：每個 clause 是 node、共享變數的 clause 連邊。做 connected component 分析，SMT 只查與 target 同一個 component 的 subset。

```
Query: d == 5
       ↓
只需要 PC 中跟 d 連通的 clauses — 如果 d 跟其他都不連，單純查 (d == 5) 就好
```

KLEE 是第一個把這個做到產品級的 symex engine。論文裡有專章說 constraint independence 帶來 **10× 速度**。

實作：engine 維護 constraint set 的 disjoint set（union-find），query 時按 variable scope 篩選。

angr 內部也有類似機制（`independencies`），但不像 KLEE 那麼 aggressive。

## 工程問題三：重複 query

一條 path fork 出 100 條 state，很多 state 走類似的 branch。同樣的 sub-query 被重複問很多次。

### 解法：Constraint caching

把 (constraint_set_hash → sat/unsat) 快取起來。KLEE 有 `CexCachingSolver`、angr 有 `cache_lookup`。

Cache 最有效的場景：**loop**。每個 iteration 的 PC 差別小，只要對 delta 做 normalization，大量 query 可以 cache hit。

## 工程問題四：formula 複雜度

某些 operation 天生讓 SMT 慢：

- **Bit-shift by symbolic amount**：`x << n`（n symbolic） → 要 case split 所有 shift amount
- **Unbounded loop 產生的 recursive formula**
- **Non-linear arithmetic**：`x * y`（兩邊都 symbolic） → QF_NIA，SMT 對這個弱
- **Float point**：QF_FP，solver 支援但很慢

### 解法：Concretization

把 symbolic value **降階成 concrete**。不再做 case analysis，取一個 feasible model 的值，固定下來。

```
x * y   其中 x, y symbolic
↓
eval(x) = 3   (隨便取一個)
fix x = 3, 繼續跑
PC 加 x == 3
```

**代價**：犧牲 completeness — 其他 x 值對應的 path 會漏掉。工具提供選項讓你控制：

- KLEE：`-symbolic-array-size=N`（符號陣列上限）
- angr：state options 有 `COMPOSITE_SOLVER`、`LAZY_SOLVES`

**經驗法則**：遇到 SMT timeout，第一招是先看 formula 裡有沒有 non-linear / symbolic shift / symbolic memory access，concretize 它們再說。

## 工程問題五：Constraint simplification

PC 裡常有 redundant 或可簡化的 clause：

```
(α ≠ 0) ∧ (α > 5)     →   (α > 5)      (前者 implied)
(α < 10) ∧ (α < 20)   →   (α < 10)     (前者更 tight)
```

engine 在送 SMT 之前先做 rewrite / simplification。claripy 自帶大量 simplification pass，z3 內部也有 preprocessor。

**太 aggressive 也不好** — simplification 可能比 solve 還慢。 angr 有 `option.SIMPLIFY_CONSTRAINTS` 這個開關可以關掉。

## 完整流程圖

把上面串起來，一個 branch fork 的內部實際發生：

```
engine 看到 branch instruction:

  cond = evaluate(branch.cond)        # symbolic formula

  for target_cond in [cond, Not(cond)]:
      new_PC = state.PC ∪ {target_cond}
      
      # constraint simplification
      new_PC = simplify(new_PC)
      
      # independence split
      relevant = independent_subset(new_PC, target_cond.vars)
      
      # cache lookup
      if relevant in cache:
          result = cache[relevant]
      else:
          # incremental SMT call
          solver.push()
          solver.assert(target_cond)
          result = solver.check()
          solver.pop()
          cache[relevant] = result
      
      if result == sat:
          fork state with new_PC
```

你去讀 angr 的 `claripy/backends/backend_z3.py`、KLEE 的 `lib/Solver/*` 看到的就是這一套的實作。

## Bit-vector vs integer

SMT 對整數有兩種 sort：
- `Int`：mathematical integer，無限精度
- `BV(N)`：N-bit bit-vector，固定精度，有 overflow

symex engine **永遠用 BV**，不用 Int。理由：

1. 程式裡的 `int x = 100000 * 100000` 會 overflow。Int sort 不會 overflow，結果就錯
2. BV 支援 `&`、`|`、`<<`、`>>` 這些 bit-level op，Int 沒有
3. C / assembly 的 semantic 天生是 BV

QF_BV（quantifier-free bit-vector）是 symex 最重要的 SMT theory。z3 的 QF_BV solver 經過十幾年打磨，效能很強。

## 為什麼 symex 很少支援 Float

`double x = input(); if (x * x > 100)` 這種 case：

- 用 `Real` sort（QF_LRA）：假設 x 是實數，**但 float 不是實數**，結果有微妙的偏差（rounding）
- 用 `Float` sort（QF_FP）：對，但 solver 非常慢，大 formula 經常 timeout

多數 symex tool 對 float 的策略：
- **KLEE**：有 Float 支援但建議別用
- **angr**：能用，但 `SimSolverZ3` 對 FP 常跑很久

結論：symex 對純 integer / pointer 最拿手。有大量 float 運算的 target（graphics、physics、ML），symex 不是好選擇。

## 一個實驗：手感一下 SMT call 有多貴

```python
import z3
import time

x = z3.BitVec('x', 32)
y = z3.BitVec('y', 32)

# 簡單 query
t0 = time.time()
s = z3.Solver()
s.add(x > 10, x < 100, y == x * 2)
s.check()
print(f"simple: {(time.time()-t0)*1000:.2f} ms, model: x={s.model()[x]}, y={s.model()[y]}")

# 複雜 query（多層 multiplication）
t0 = time.time()
s2 = z3.Solver()
z = x
for i in range(30):
    z = z * y + i
s2.add(z == 0xdeadbeef)
s2.check()
print(f"30-deep mul: {(time.time()-t0)*1000:.2f} ms")
```

你會看到 simple query 是微秒級，複雜 nonlinear 可能跳到秒級甚至 timeout。這就是你每天在 symex 裡鬥的東西。

## 心法

做 symex 工程時，把 SMT call **當成最貴的 resource** — 比 memory 還貴、比 CPU 還貴。

每次你 `if symbolic_val == 0:` 都在隱性觸發 SMT call；每次你 deep-copy state 都在 clone solver session；每次你 concretize 都在丟資訊換速度。**知道 SMT call 發生在哪、有多貴，就知道 symex script 怎麼寫才不會爆**。

## 自我檢核

- [ ] 理解「PC → SMT assumption」這個對映
- [ ] 能說出三個主要工程優化：incremental、independence、caching
- [ ] 解釋 concretization 的取捨（completeness vs tractability）
- [ ] 知道 QF_BV 是 symex 最常用的 theory、Int sort 為什麼不適合
- [ ] 知道 float 是 symex 的弱點

下一章切到 **concolic** — 當 pure symex 的 path explosion 無解時，concolic 用 concrete 陪跑降壓力。這是 DART、SAGE、KLEE、angr 實務都在做的事。

→ [Ch 6 — Concolic execution：DART 與 CUTE 的真實思路](./06-concolic.md)
