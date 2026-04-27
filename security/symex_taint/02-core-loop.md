# Ch 2 — Symbolic execution 的核心循環

> 目標：把 symbolic execution engine 的 state、branching、SMT query 三件事拆到最本質。講完這章你應該能在白板上 5 分鐘內畫出一個 symex engine 的 pseudocode。

## Symbolic state 是什麼

一個 symbolic execution engine 在任何時刻都在維護一個 **symbolic state**，可以看作四元組：

```
State = ⟨ pc, σ_reg, σ_mem, PC ⟩
            │    │      │     │
            │    │      │     └── path constraint（一個 Boolean 公式）
            │    │      └── memory store（可能是 concrete、symbolic 或兩者混合）
            │    └── register store
            └── program counter（通常 concrete）
```

跟 concrete execution 的差別：

| 面向 | Concrete | Symbolic |
|------|----------|----------|
| `x = input()` | `x = 42`（讀 stdin） | `x = α`（新 symbolic variable） |
| `y = x + 5` | `y = 47` | `y = α + 5`（仍是 formula） |
| `if (y > 10)` | 走 true branch | 兩條 branch 都考慮，分別累積 PC |
| memory load | 讀出具體 byte | 看 address 是否 symbolic；看內容是否 symbolic |
| system call | 真的 syscall | 呼叫 modelled 版本（回傳 symbolic/concrete） |

**關鍵觀察**：concrete execution 只有一條 state 流；symbolic execution 每條 branch 可能讓 state 分裂 — 變成 **state tree**。

## 核心循環 pseudocode

最 minimal 的 symex engine 長這樣：

```python
def symex(init_state):
    worklist = [init_state]
    while worklist:
        state = worklist.pop()
        while True:
            inst = fetch(state.pc)
            
            if is_branch(inst):
                cond = evaluate(inst.cond, state)   # symbolic formula
                
                # fork true branch
                s_t = state.copy()
                s_t.PC = And(state.PC, cond)
                if solver.check(s_t.PC) == SAT:
                    s_t.pc = inst.target_true
                    worklist.append(s_t)
                
                # fork false branch
                s_f = state.copy()
                s_f.PC = And(state.PC, Not(cond))
                if solver.check(s_f.PC) == SAT:
                    s_f.pc = inst.target_false
                    worklist.append(s_f)
                
                break  # 這條 state 結束（已 fork 成兩條）
            
            elif is_assign(inst):
                val = evaluate(inst.rhs, state)
                state.store(inst.lhs, val)
                state.pc = next(inst)
            
            elif is_halt(inst) or is_bug(inst):
                report(state)
                break
```

整個 symex 就是這個 loop。真實 engine 多的是 bookkeeping、optimization、symbolic memory 的 corner case，但**主幹就是這六十行**。這是你要記到睡夢中都能背的。

## 具體走一次：3 行 C

```c
int x = input();   // x 是使用者輸入
if (x > 10) {
    if (x < 20) {
        bug();
    }
}
```

Step by step：

```
初始 state:
  pc = L0 (int x = input())
  σ = {}
  PC = true

Step 1 (L0):
  σ = {x → α}     // α 是新 symbolic var
  PC = true
  pc = L1 (if x > 10)

Step 2 (L1): branch → fork
  Branch A:  pc = L2, PC = α > 10
  Branch B:  pc = L_end, PC = ¬(α > 10) == α ≤ 10
  
  solver.check(α > 10) = SAT     → 保留 A
  solver.check(α ≤ 10) = SAT     → 保留 B

Step 3 (只追 A, L2): if x < 20 → fork
  Branch A1: pc = L3 (bug), PC = α > 10 ∧ α < 20
  Branch A2: pc = L_end,    PC = α > 10 ∧ α ≥ 20
  
  solver.check(α > 10 ∧ α < 20) = SAT, model α = 15  → bug reachable!
```

**PC 是什麼**：一串「走到這裡必須滿足的所有 branch 條件」的 conjunction。它定義了「這個 state 代表哪個 input 子集」。

**model 是什麼**：SMT solver 回傳的具體賦值。它就是**你要的 input**。

## Branch fork 的兩種模式

上面的 pseudocode 是最乾淨的 **forking** 模式：碰到 branch 就 fork、把兩條 state 都丟回 worklist。還有兩種變體你要知道：

### 1. Concolic (concrete + symbolic)

不 fork 所有 branch；維持一條 concrete 執行流，symbolic 只是陪跑。碰到 branch 時只走 concrete value 指示的那條，但 PC 仍然記下來。最後從 PC 裡 pick 一個 clause 把它**翻轉**、求解、下次用新 input 重跑。

```
(第一次跑)   α 具體值 = 5
             L1: 5 > 10 → false，走 L_end
             PC_collected = α ≤ 10
             
(翻轉最後一個 clause)
             新目標: α > 10
             solve: α = 11
             
(第二次跑)   α = 11
             L1: 11 > 10 → true，走 L2
             L2: 11 < 20 → true, PC = α > 10 ∧ α < 20
             走到 bug
```

這是 DART（Godefroid 2005）的想法。Ch 6 會詳講，它是實務上最被採用的 symex flavor。

### 2. Path merging

兩條 branch 共用之後的 code 時，不要把它們當成獨立 state — 把兩條 state **合併**成一條：用 SMT 的 `ite`（if-then-else）把不同的變數值接起來。

```
branch A: σ = {x → 5, y → α}, PC = cond_A
branch B: σ = {x → 10, y → α}, PC = cond_B

合併:
σ = {x → ite(cond_A, 5, 10), y → α}
PC = cond_A ∨ cond_B
```

犧牲 per-state 的 PC 簡單性，換取 state 數量下降。Ch 8 會講代價與時機。

## SMT query 在哪裡、多久呼叫一次

上面 pseudocode 你會發現 `solver.check(...)` 呼叫在每個 branch fork 時都有。這是 symex **最花錢的地方**：

- 一個 CDCL-based SMT 每次 query 可能幾 ms 到幾秒
- 真實程式 branch 密度高，每秒幾千個 branch 很正常
- 結果：symex 的 throughput 被 SMT call 支配

工程上的優化全部圍繞「**減少 SMT query** 與 **讓每個 query 更快**」：

| 優化 | 做什麼 | 哪裡用 |
|------|--------|--------|
| **Incremental SMT** | 保留 solver state，只 push/pop 新 clause | KLEE、angr 內部 |
| **Constraint caching** | 相同 PC 的 check 快取結果 | angr（`cache_lookup`） |
| **Constraint independence** | PC 拆成不相關子集分開查 | KLEE 的 `ConstraintManager` |
| **Concretization** | symbolic 太複雜就退而求具體值 | angr 的 symbolic memory |
| **Query count budget** | 限制每條 state 的 SMT query 數 | fuzzing-integrated symex |

你之後讀 angr / KLEE 的 source，看到一堆 `solver.eval`、`solver.satisfiable`、`solver.merge` 都是這一層在做事。

## State 還要存什麼

除了 `⟨pc, σ_reg, σ_mem, PC⟩` 這個理論四元組，真實 engine 的 state 還塞了一堆：

```
真實 SimState (angr) 大致:
  regs            register file
  memory          symbolic memory plugin
  posix           stdin/stdout、fd、file state
  filesystem      模擬檔案系統
  heap            malloc/free tracking
  callstack       shadow callstack
  history         走過的 branches（for CFG 重建）
  solver          constraint + SMT interface
  options         symex 行為開關
  inspect         event hook 系統
```

一開始看到這麼多會覺得可怕，其實大部分是 engineering — 為了讓 `fopen` 跟 `malloc` 能在 symbolic world 裡被 model 出來。Ch 14 會拆 angr 的 SimState 結構。

## 一個小實驗：用 Z3 手算 PC

不用任何 symex 框架，只用 Z3 來跑上面那個 3 行 C 的 PC：

```python
import z3

x = z3.BitVec('x', 32)         # 32-bit symbolic variable
solver = z3.Solver()

# 走 x > 10 ∧ x < 20 的那條路
solver.add(x > 10)
solver.add(x < 20)

print(solver.check())
if solver.check() == z3.sat:
    print("witness input:", solver.model()[x])

# 換一條路
solver.reset()
solver.add(x > 10)
solver.add(z3.Not(x < 20))
print("其他 path:", solver.check(), solver.model())
```

```
sat
witness input: 11
其他 path: sat [x = 21]  (或其他)
```

**你剛手動跑了一個 symex engine**。差別只在：真正的 engine 幫你從 program 自動抽出 predicate `x > 10`；你這裡是手 type 進去的。Ch 7 要做的就是自動化這一步。

## Symbolic vs Concolic vs Static 的速寫

放進同一張表比較：

| 面向 | Static | Symex (pure) | Concolic | Fuzzing |
|------|--------|--------------|----------|---------|
| 執行程式？ | 否 | 否（abstract） | 是（但 instrumented） | 是 |
| Input 具體值？ | 無 | 無 | 有 | 有 |
| Path 選擇 | — | 所有 feasible | 一次一條 | 隨機 coverage-guided |
| 找到的 input 能重現？ | N/A | 有 model 可解 | 直接可用 | 直接可用 |
| External world？ | 不管 | 需要 model | 真的跑 | 真的跑 |
| 卡點 | false positive | path explosion | 要迭代 | 複雜 branch |

**pure symex 跟 concolic 最大的差別**：pure symex 可以 fork 出「實際上你還沒有 input 的 state」然後繼續推；concolic 永遠有一條 concrete 輸入陪著，遇到 symbolic 無法 model 的事情就看 concrete 結果。後者工程上強很多，前者理論上乾淨。

## 自我檢核

- [ ] 能寫出 symex 核心循環的 pseudocode（worklist + branch fork + SMT check）
- [ ] 理解 state 是 `⟨pc, σ_reg, σ_mem, PC⟩`，解釋每個 component 的作用
- [ ] 能手動走一遍一個 3 行 C 程式的 PC 累積過程
- [ ] 知道 SMT query 是 symex 的 bottleneck，說得出三個降低 query 成本的手段
- [ ] 區分 forking 與 concolic 兩種 branch 處理策略

下一章講這個 loop 最致命的敵人 — path explosion。為什麼這個看起來乾淨的演算法在真實 code 上會 OOM、工程上要用什麼武器救它。

→ [Ch 3 — 路徑爆炸這個病](./03-path-explosion.md)
