# Ch 7 — 實作：用 Z3 手寫 mini concolic executor

> 目標：用大約 200 行 Python 把 DART 的 algorithm 在一個 toy bytecode 上實作出來。寫完你對 symex 的所有 magic 都祛魅了。

## 設計目標

我們要做的 executor：

- 跑一個**極簡 stack-based bytecode**（push、pop、加減乘、比較、conditional branch、input、assert）
- 對 `input` instruction 產生 symbolic variable
- 每個 branch 記 PC、結束一條 path 後翻轉 PC 求新 input
- 碰到 `assert false` 時報告這是 bug、拿到觸發它的 input

最終 output 應該長這樣：

```
[run 1] input = [0, 0]           path taken: [PC ≤ 10]
[run 2] input = [11, 0]          path taken: [α > 10, β ≤ 5]
[run 3] input = [11, 6]          path taken: [α > 10, β > 5]
[run 4] input = [11, 0]          BUG: assertion failure
```

## Bytecode 規格

```
PUSH n         # 常數壓棧
INPUT          # 讀一個 input，壓棧（產生 symbolic var）
ADD / SUB / MUL # 彈兩個，運算，壓棧
EQ / LT / GT   # 比較，壓 0/1
JZ  label      # 棧頂為 0 則 jump（彈棧）
JNZ label      # 棧頂非 0 則 jump（彈棧）
JMP label
ASSERT         # 棧頂為 0 代表 assertion failed（bug）
HALT
```

這是最小 set — 足夠 demo concolic。

## 目標程式：示範 bug

我們先寫一個 source：

```python
# target.py (人讀版本)
a = INPUT()
b = INPUT()
if a > 10:
    if b > 5:
        # ok
    else:
        ASSERT(False)   # bug
```

翻譯成 bytecode（手寫，沒寫 compiler）：

```
L0: INPUT           # a
L1: INPUT           # b
L2: PUSH 10
L3: (top)           # stack: [a, b, 10]  — 手動 dup，算了簡化：改成吃後面
```

算了，簡化一下 — 我直接給 bytecode 設計成：

```
L0:  INPUT                # stack: [a]
L1:  PUSH 10
L2:  GT                   # stack: [a > 10 ? 1 : 0]
L3:  JZ L9                # 若 false 跳 L9
L4:  INPUT                # stack: [b]
L5:  PUSH 5
L6:  GT                   # stack: [b > 5 ? 1 : 0]
L7:  JZ L11               # 若 false 跳 L11 (bug)
L8:  HALT
L9:  HALT
L10: HALT
L11: PUSH 0
L12: ASSERT               # bug!
L13: HALT
```

bytecode 用 Python list 表示：

```python
PROGRAM = [
    ('INPUT',),              # 0: a
    ('PUSH', 10),            # 1
    ('GT',),                 # 2
    ('JZ', 9),               # 3
    ('INPUT',),              # 4: b
    ('PUSH', 5),             # 5
    ('GT',),                 # 6
    ('JZ', 11),              # 7
    ('HALT',),               # 8
    ('HALT',),               # 9
    ('HALT',),               # 10
    ('PUSH', 0),             # 11
    ('ASSERT',),             # 12
    ('HALT',),               # 13
]
```

## 實作：concrete interpreter

先不管 symbolic，寫最基本的 concrete interpreter：

```python
def concrete_run(program, inputs):
    """跑一次 concrete execution，回傳 trace（走過的 pc 序列）"""
    stack = []
    pc = 0
    input_idx = 0
    trace = []
    
    while pc < len(program):
        trace.append(pc)
        inst = program[pc]
        op = inst[0]
        
        if op == 'PUSH':
            stack.append(inst[1])
            pc += 1
        elif op == 'INPUT':
            stack.append(inputs[input_idx])
            input_idx += 1
            pc += 1
        elif op == 'ADD':
            b, a = stack.pop(), stack.pop()
            stack.append(a + b)
            pc += 1
        elif op == 'SUB':
            b, a = stack.pop(), stack.pop()
            stack.append(a - b)
            pc += 1
        elif op == 'MUL':
            b, a = stack.pop(), stack.pop()
            stack.append(a * b)
            pc += 1
        elif op == 'EQ':
            b, a = stack.pop(), stack.pop()
            stack.append(1 if a == b else 0)
            pc += 1
        elif op == 'LT':
            b, a = stack.pop(), stack.pop()
            stack.append(1 if a < b else 0)
            pc += 1
        elif op == 'GT':
            b, a = stack.pop(), stack.pop()
            stack.append(1 if a > b else 0)
            pc += 1
        elif op == 'JZ':
            v = stack.pop()
            pc = inst[1] if v == 0 else pc + 1
        elif op == 'JNZ':
            v = stack.pop()
            pc = inst[1] if v != 0 else pc + 1
        elif op == 'JMP':
            pc = inst[1]
        elif op == 'ASSERT':
            v = stack.pop()
            if v == 0:
                return trace, 'BUG'
            pc += 1
        elif op == 'HALT':
            return trace, 'HALT'
        else:
            raise ValueError(f'unknown op {op}')
    
    return trace, 'END'
```

驗證：

```python
trace, end = concrete_run(PROGRAM, [0, 0])
print(trace, end)   # [0, 1, 2, 3, 9] HALT   (a=0 走 false)

trace, end = concrete_run(PROGRAM, [11, 10])
print(trace, end)   # [0, 1, 2, 3, 4, 5, 6, 7, 8] HALT

trace, end = concrete_run(PROGRAM, [11, 0])
print(trace, end)   # [0, 1, 2, 3, 4, 5, 6, 7, 11, 12] BUG
```

OK，base case 對。

## 加上 symbolic tracing

concrete interpreter 每條 instruction 同時維護兩個 stack：concrete 與 symbolic：

```python
import z3

def concolic_run(program, inputs):
    """並行跑 concrete + symbolic，回傳 (trace, PC, input_vars, outcome)"""
    cstack = []   # concrete
    sstack = []   # symbolic (z3 expressions)
    pc = 0
    input_idx = 0
    trace = []
    path_constraints = []   # 走過的 branch 條件
    input_vars = []          # 每次 INPUT 產生的 z3 var
    
    while pc < len(program):
        trace.append(pc)
        inst = program[pc]
        op = inst[0]
        
        if op == 'PUSH':
            cstack.append(inst[1])
            sstack.append(z3.BitVecVal(inst[1], 32))
            pc += 1
        elif op == 'INPUT':
            cv = inputs[input_idx]
            sv = z3.BitVec(f'in_{input_idx}', 32)
            input_vars.append(sv)
            cstack.append(cv)
            sstack.append(sv)
            input_idx += 1
            pc += 1
        elif op in ('ADD', 'SUB', 'MUL'):
            cb, ca = cstack.pop(), cstack.pop()
            sb, sa = sstack.pop(), sstack.pop()
            if op == 'ADD':
                cstack.append(ca + cb); sstack.append(sa + sb)
            elif op == 'SUB':
                cstack.append(ca - cb); sstack.append(sa - sb)
            elif op == 'MUL':
                cstack.append(ca * cb); sstack.append(sa * sb)
            pc += 1
        elif op in ('EQ', 'LT', 'GT'):
            cb, ca = cstack.pop(), cstack.pop()
            sb, sa = sstack.pop(), sstack.pop()
            if op == 'EQ':
                c = 1 if ca == cb else 0
                s = z3.If(sa == sb, z3.BitVecVal(1, 32), z3.BitVecVal(0, 32))
            elif op == 'LT':
                c = 1 if ca < cb else 0
                s = z3.If(sa < sb, z3.BitVecVal(1, 32), z3.BitVecVal(0, 32))
            elif op == 'GT':
                c = 1 if ca > cb else 0
                s = z3.If(sa > sb, z3.BitVecVal(1, 32), z3.BitVecVal(0, 32))
            cstack.append(c); sstack.append(s)
            pc += 1
        elif op == 'JZ':
            cv = cstack.pop()
            sv = sstack.pop()
            cond_is_zero = (sv == 0)
            if cv == 0:
                path_constraints.append(cond_is_zero)
                pc = inst[1]
            else:
                path_constraints.append(z3.Not(cond_is_zero))
                pc = pc + 1
        elif op == 'JNZ':
            cv = cstack.pop()
            sv = sstack.pop()
            cond_nonzero = (sv != 0)
            if cv != 0:
                path_constraints.append(cond_nonzero)
                pc = inst[1]
            else:
                path_constraints.append(z3.Not(cond_nonzero))
                pc = pc + 1
        elif op == 'JMP':
            pc = inst[1]
        elif op == 'ASSERT':
            cv = cstack.pop()
            sv = sstack.pop()
            if cv == 0:
                return trace, path_constraints, input_vars, 'BUG'
            else:
                path_constraints.append(sv != 0)
                pc += 1
        elif op == 'HALT':
            return trace, path_constraints, input_vars, 'HALT'
        else:
            raise ValueError(f'unknown op {op}')
    
    return trace, path_constraints, input_vars, 'END'
```

**關鍵**：每次 branch，根據 concrete 結果決定走哪邊、**並把對應的 symbolic 條件加進 PC**。跟 Ch 6 的 DART pseudocode 幾乎字對字對應。

## 加上 path exploration：DART 主 loop

現在主循環。queue 一些 input，跑一條、翻轉 PC 產生更多 input：

```python
def solve_negated_prefix(path_constraints, input_vars, negate_idx):
    """翻轉 PC[negate_idx]、解 SMT，回傳新的 concrete input 或 None"""
    s = z3.Solver()
    for i in range(negate_idx):
        s.add(path_constraints[i])
    s.add(z3.Not(path_constraints[negate_idx]))
    
    if s.check() == z3.sat:
        m = s.model()
        return [m[v].as_long() if m[v] is not None else 0 for v in input_vars]
    return None


def dart(program, initial_input, max_runs=50):
    queue = [initial_input]
    seen_traces = set()
    run_count = 0
    
    while queue and run_count < max_runs:
        inputs = queue.pop(0)
        # 補零，避免 INPUT 取到不存在的 index（工程 hack）
        inputs = list(inputs) + [0] * 10
        
        trace, PC, input_vars, outcome = concolic_run(program, inputs)
        run_count += 1
        
        t_key = tuple(trace)
        if t_key in seen_traces:
            continue
        seen_traces.add(t_key)
        
        print(f"[run {run_count}] input={inputs[:len(input_vars)]} "
              f"trace={trace} outcome={outcome}")
        
        if outcome == 'BUG':
            print(f"  *** BUG FOUND with input {inputs[:len(input_vars)]} ***")
        
        # 翻轉 PC 各 prefix、產生新 input
        for i in range(len(PC)):
            new_input = solve_negated_prefix(PC, input_vars, i)
            if new_input is not None and tuple(new_input) not in [tuple(q) for q in queue]:
                queue.append(new_input)
    
    print(f"\n探索完畢：{run_count} runs, {len(seen_traces)} unique paths")
```

## 跑起來看

```python
if __name__ == '__main__':
    dart(PROGRAM, [0, 0])
```

預期輸出（實際值看 Z3 model 挑到什麼）：

```
[run 1] input=[0, 0] trace=[0, 1, 2, 3, 9] outcome=HALT
[run 2] input=[11, 0] trace=[0, 1, 2, 3, 4, 5, 6, 7, 11, 12] outcome=BUG
  *** BUG FOUND with input [11, 0] ***
[run 3] input=[11, 6] trace=[0, 1, 2, 3, 4, 5, 6, 7, 8] outcome=HALT

探索完畢：3 runs, 3 unique paths
```

**你剛用 200 行 Python 實作了一個能找 bug 的 symex engine**。

## 完整代碼整理

建議你開一個 file `mini_concolic.py`，把上面的 concrete_run、concolic_run、solve_negated_prefix、dart 與 `PROGRAM` 定義全部放進去，跑一次。親手敲，不要複製 — 這個練習是後面所有章節的基礎。

完整檔案作為 reference 放在 `practice-a-mini-concolic.md`（練習 A 會延伸這個做多 target、counter、更複雜的 bytecode）。

## 你剛剛看到的所有東西，是 KLEE / angr 放大 1000 倍的版本

對照 KLEE 的架構（Ch 11 細講）：

| Mini | KLEE |
|------|------|
| `concrete_run` | KLEE interpreter（跑 LLVM IR） |
| `concolic_run` 的 parallel stack | `ExecutionState` 的 concrete + symbolic memory |
| `path_constraints` | `ExecutionState.constraints` |
| `input_vars` | `klee_make_symbolic` 產生的 MemoryObject |
| `solve_negated_prefix` | KLEE 的 `Solver` call + `ConstraintManager` |
| `dart` main loop | KLEE 的 `run` + `searcher` |

angr 也是同一套結構，只是 SimState 還多了 syscall、fd、filesystem 這些 plugin。**你看 KLEE source 時，腦袋裡有這個 mini，讀起來會順很多**。

## 這個 mini 的限制

（刻意留著給練習 A 延伸）：

1. **沒有 memory**：只有 stack。加 memory 要把 concrete memory 跟 symbolic memory 都維護，Ch 9 講的東西就要處理
2. **Bytecode 沒有 function call**：沒有 callstack
3. **input vars 是無界的**：沒有 range constraint（`in_0 ∈ [0, 255]`）— 真實 target 會在 input 上加 byte constraint
4. **沒有 loop handling**：如果 program 有 loop，trace 可能無限長
5. **search 是純 BFS**：真工具有 coverage 或 target-directed heuristic

每一個限制都是一篇 paper。但這個 mini 的**框架是對的** — 你只要往上加，就是 KLEE / angr / Triton。

## 常見踩的雷

寫這個的時候你可能會撞到：

- **type 混淆**：Python 的 int 跟 z3 的 BitVec 不要混用，運算時都要對應的 type
- **branch condition 的 value**：`JZ` 的「條件為 0 才跳」vs「為真才跳」— 這邊符號相反要想清楚
- **Symbolic stack empty**：如果 concrete stack 跟 symbolic stack 失同步，bug 會很難追。永遠 `cpush cstack / sstack` 成對做
- **忘記 z3 context**：如果你開多個 Solver 又沒 import 對，會看到 `sort mismatch` 錯誤

debug 時：把每一步的 `sstack`、`path_constraints` 印出來。比用 IDE breakpoint 快。

## 自我檢核

- [ ] 自己動手寫完 `mini_concolic.py`，跑出 BUG 報告
- [ ] 能解釋 concrete stack 跟 symbolic stack 為什麼平行維護
- [ ] 看懂 `solve_negated_prefix` 的 prefix negation 為什麼對
- [ ] 試著改 PROGRAM 加一個 loop，看 trace 怎麼變化
- [ ] 能把這個 mini 對應到 angr 的 SimState 結構

這個練習是整門課的轉捩點。下一章回到理論 — state merging 怎麼降 active state 數、為什麼它有時會弄巧成拙。

→ [Ch 8 — State merging：降爆炸的代價與時機](./08-state-merging.md)
