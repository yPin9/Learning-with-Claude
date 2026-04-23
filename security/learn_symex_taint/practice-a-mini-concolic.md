# 練習 A — 寫一個 mini concolic executor

> 把 Ch 7 的 mini 擴展成真正可用的工具。這個練習是整門課的分水嶺 — 做過之後你對 symex 的 magic 全部祛魅。

## 目標

在 Ch 7 的基礎上，讓 mini concolic executor 支援：

1. **記憶體**：load / store，with symbolic address
2. **Function call**：call + ret、local variable
3. **更豐富的 input**：不固定是 `INPUT` opcode，支援 byte-level symbolic
4. **Path 探索順序**：改掉 BFS，加 coverage-guided 策略
5. **Bug detection**：除了 `ASSERT`，自動檢查 divide-by-zero、memory OOB
6. **Stats 輸出**：每次 run 的 coverage、path、active state
7. **對簡單 C 的編譯器**：寫一個簡單的 C 子集 → 你的 bytecode 的 translator

做完你會有一個 ~800 行的 toy 工具，能處理合理複雜的小 C program。

## Step 1 — 擴充 bytecode

新增 opcode：

```python
# memory
LOAD            # pop addr, push mem[addr]
STORE           # pop value, pop addr, mem[addr] = value
LOAD_BYTE       # 單 byte load（對 string 有用）
STORE_BYTE

# control
CALL    n       # call function at index n, m args from stack
RET             # pop, 當 return value (stack 頂)
ALLOCA  size    # stack-allocate size bytes, push base address

# constants
PUSH_CONST i    # push program 的 constant pool 裡第 i 個
PUSH_SYM name   # push 一個 symbolic variable
```

設計 memory 為一個 dict：`Dict[int, BitVec(8)]`。concrete memory 跟 symbolic memory 同一 dict — concrete byte 存成 `BitVecVal`、symbolic byte 存成 `BitVec` 的 SSA variable。

## Step 2 — 重構 interpreter

```python
class State:
    def __init__(self):
        self.cstack = []          # concrete stack
        self.sstack = []          # symbolic stack
        self.cmem = {}            # concrete memory
        self.smem = {}            # symbolic memory (addr → BitVec(8))
        self.callstack = []        # (pc, frame_base)
        self.pc = 0
        self.halt = False
        self.path = []            # trace
        self.pc_list = []         # path constraints
        self.input_vars = []
        self.next_sym_addr = 0x10000

def step(state, program):
    inst = program[state.pc]
    op = inst[0]
    # handle each opcode...
    state.path.append(state.pc)
```

## Step 3 — Symbolic memory 怎麼處理

Ch 9 講過有兩派做法。在這個練習用**簡化版**：

- `LOAD` with concrete address：直接查 `mem[addr]`，回傳 `BitVec` 或 `BitVecVal`
- `LOAD` with symbolic address：用 Z3 enumerate 可能值（最多 20 個），fork state 對每個可能值一條

```python
def load(state, addr_c, addr_s):
    if isinstance(addr_s, BitVecVal):
        return state.cmem[addr_c.as_long()], state.smem.get(addr_c.as_long())
    # symbolic: enumerate
    s = z3.Solver()
    for pc in state.pc_list:
        s.add(pc)
    possible = []
    while len(possible) < 20:
        if s.check() != z3.sat:
            break
        m = s.model()
        v = m[addr_s].as_long()
        possible.append(v)
        s.add(addr_s != v)
    # 需要 fork state 成 len(possible) 條 — 但簡化版：取第一個
    a = possible[0]
    return state.cmem.get(a, 0), state.smem.get(a)
```

完整版要 **fork**。先做簡化版跑起來再說。

## Step 4 — Function call / ret

call 時 push frame、set pc 到 callee。ret 時 pop、回 caller 下一條：

```python
elif op == 'CALL':
    target = inst[1]
    state.callstack.append(state.pc + 1)
    state.pc = target

elif op == 'RET':
    ret_c = state.cstack.pop() if state.cstack else 0
    ret_s = state.sstack.pop() if state.sstack else BitVecVal(0, 32)
    state.pc = state.callstack.pop()
    state.cstack.append(ret_c)
    state.sstack.append(ret_s)
```

簡單版不管 frame 之間的 variable isolation。要做對要加 frame pointer。

## Step 5 — Path exploration 策略

Ch 16 介紹過幾種。這裡練兩個：

### DFS

queue 改用 stack：

```python
queue = [initial_input]
while queue:
    inputs = queue.pop()   # pop 最後加的 → DFS
    ...
```

### Coverage-guided

記錄 edge set，優先走沒見過 edge 的 input：

```python
seen_edges = set()
def run_and_record(state, program):
    # 跑完後回傳 edge set
    pass

# 產生 N 個 new input 後，按「新 edge 數」排序，優先 run
```

## Step 6 — Bug detection

三類 bug 要偵測：

### Divide by zero

```python
elif op == 'DIV':
    b_c, a_c = state.cstack.pop(), state.cstack.pop()
    b_s, a_s = state.sstack.pop(), state.sstack.pop()
    # check: b 可能為 0 嗎？
    check_pc = state.pc_list + [b_s == 0]
    s = z3.Solver()
    for c in check_pc: s.add(c)
    if s.check() == z3.sat:
        print(f"[BUG] div by zero at pc={state.pc}, input={s.model()}")
    # 繼續跑
    if b_c != 0:
        state.cstack.append(a_c // b_c)
        state.sstack.append(a_s / b_s)
        state.pc += 1
```

### Memory OOB

在 `LOAD` / `STORE` 時檢查 addr 是否在合法範圍：

```python
def check_oob(state, addr_s):
    # 假設 valid range 是 [0x10000, 0x20000]
    s = z3.Solver()
    for c in state.pc_list: s.add(c)
    s.add(z3.Or(addr_s < 0x10000, addr_s >= 0x20000))
    if s.check() == z3.sat:
        print(f"[BUG] OOB at pc={state.pc}, addr model={s.model()}")
```

### Assertion

Ch 7 已經做了。保留。

## Step 7 — Stats 輸出

每次 run 印出：

```python
def report(state):
    print(f"Run {run_id}: "
          f"path len={len(state.path)}, "
          f"PC size={len(state.pc_list)}, "
          f"seen edges={len(seen_edges)}, "
          f"new edges={new}")
```

完整跑結束印總結：

```
Total runs: 47
Unique paths: 23
Bugs found: 2
Total coverage: 89 / 120 basic blocks (74%)
SMT calls: 156
SMT time: 3.2s
```

## Step 8 — Mini C translator（optional）

寫一個**極小 C**（只有 int、if、while、function）到你的 bytecode 的 translator。參考 pycparser：

```python
import pycparser
ast = pycparser.parse_file('input.c')
bytecode = compile_ast(ast)
run_concolic(bytecode)
```

不做這步也可以 — 手寫 bytecode 也能 demo。

## 測試 targets

寫幾個 test programs 驗證你的 executor：

### Test 1：簡單 branch

```
# bug 當 x == 3
if x == 3:
    ASSERT(False)
```

### Test 2：loop

```
# bug 當 sum of 5 input byte == 100
sum = 0
for i in range(5):
    sum += input[i]
if sum == 100:
    ASSERT(False)
```

Test 2 path 爆炸：每個 byte 的 inequality condition 都 fork，你的 executor 應該找到 bug。

### Test 3：memory OOB

```
arr = [0] * 10
arr[input[0]] = 1   # 可能 OOB
```

### Test 4：function call

```
def f(x):
    if x > 100:
        return 1
    return 0

if f(input[0]) == 1:
    ASSERT(False)
```

## 比較跟對照

跑完你的 executor 後，把同樣的 C source 編譯、用 KLEE 跑：

```bash
clang -emit-llvm test.c -o test.bc
klee test.bc
```

比較：
- KLEE 找到多少 bug、多少 test
- 你的 executor 找到多少
- 兩者哪裡一樣、哪裡差

**這步是練習的精華**。你會親眼看到 production symex 跟 toy executor 的差距在哪（主要是 memory model、external call、optimization）。

## 提交 / 歸檔

建議你把這個練習的結果放進一個 GitHub repo：

```
mini-concolic/
├── README.md           # 你的設計文件
├── concolic.py         # 主要 executor
├── bytecode.py         # bytecode 定義 / 解釋
├── translator.py       # C → bytecode (optional)
├── tests/
│   ├── test1_branch.bc
│   ├── test2_loop.bc
│   └── ...
└── benchmark/
    └── compare_klee.md   # 你跑 KLEE 比對的結果
```

這會是你之後找工作 / 讀研究時很有力的 portfolio。

## 預期困難點

### 困難 1：symbolic address 的 fork

fork state 意味著整個 state 要 deep-copy。concrete / symbolic memory、stack、PC list 都要 copy。慢但 necessary。

### 困難 2：call stack 的 variable lifetime

function call 結束後，local 的 stack memory 應該釋放。你需要追蹤 stack frame 邊界。

### 困難 3：SMT call overflow

你會發現 SMT call 變成 bottleneck。Ch 5 講的 optimization — incremental、cache — 要實裝。

### 困難 4：Path explosion

複雜 target 很快 fork 幾萬條。設 state cap（例如 500）、coverage priority、lose pruning。

## 回到 Ch 7 的 insight

做完這個練習，回頭看 Ch 7 mini concolic 的 100 行、再看你現在的 800 行，你會**完全理解 KLEE / angr 為什麼是幾萬行**。那些多出來的行數不是 gold-plating，每一段都在解決你剛踩過的坑。

這是 symex 工程真相。

## 自我檢核

- [ ] 能跑完 test 1-4，每個都回答「該找到 bug 嗎？找到了嗎？」
- [ ] 有 symbolic memory 支援（雖然簡化）
- [ ] 有 function call / ret
- [ ] 有 coverage tracking
- [ ] 有基本 bug detection（div-by-zero + OOB）
- [ ] 跟 KLEE 對照跑過一次、寫了 benchmark report

→ [Ch 11 — KLEE 架構：LLVM IR 上做 symex 的理由](./11-klee-architecture.md)
