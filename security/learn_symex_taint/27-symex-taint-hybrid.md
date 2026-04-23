# Ch 27 — Symex + taint 聯手：Triton-style hybrid

> 目標：講清楚為什麼兩者合用在工程上有意義、合用的三個典型 pattern、Triton 的實例。

## Why hybrid

整門課一路分開講 symex 跟 taint。現在看合用的理由：

### 理由 1：兩者分別弱

- **Pure symex**：path explosion、SMT 瓶頸、大 target 跑不動
- **Pure taint**：只講「這個 byte 是不是影響到」，答不出「怎麼影響、翻過去要塞什麼值」

### 理由 2：互補

- **Taint 回答「where」**：這個 sink 的 input 可能來自哪些 byte、哪些 path
- **Symex 回答「what」**：把 taint 找到的 byte 解出具體值，讓某個 condition 成立

### 理由 3：工程 reuse

Ch 20-23 看過了 — DTA 跟 symex 共用大量 infrastructure。一套 shadow memory、一套 DBI、一套 propagation engine，跑兩種 analysis，幾乎 free ride。

## Pattern 1：Taint-guided symex

最常見 pattern：用 taint 決定**哪些 state / branch 值得 symex**。

```
   target + input
      │
      ▼
   DTA 跑過一次 → 標 tainted byte、tainted branch
      │
      ▼
   挑 tainted branch
      │
      ▼
   symex 只在 tainted 範圍內做 — 非 tainted 的 branch 不 fork
```

好處：symex 的 state space 收縮到「跟 attacker input 有關」的子集，不管那些跟 input 無關的 branch。

實務：對 10^10 path 的 target，tainted path 可能只有 10^5，可行。

代表作：
- **BORG** (2014)：kernel code 的 taint-driven symex
- **Angora** (S&P 2018)：fuzzer 內的 taint + symex hybrid

## Pattern 2：Symex-aided taint

symex 用來**精確決定 taint 會不會真的流**。

考慮：

```c
int x = tainted_input;
int y = 0;
if (x > 1000000) y = x;
else y = 42;
```

純 taint policy：`y` 最後 tainted 嗎？保守 taint 會說 yes（因為 y 依賴 tainted cond）。但如果 **cond 實際不可能滿足**（x ∈ [0, 100]），y = 42 恆成立。

symex：加上 PC `x ∈ [0, 100]`，check `x > 1000000` 是否 SAT → unsat → 確定 y 不 tainted。

這叫 **precise taint propagation**。

代價：對每個 branch 都 SMT call，效能崩。實務只對 critical sink 前的 few branch 開。

代表作：
- **All-You-Ever-Wanted**（Schwartz et al. S&P 2010）論文裡的 formal framework
- **PANDA** 的 SymTaint plugin

## Pattern 3：Triton 的一體化

Triton 預設就是 hybrid：每條 instruction processing 同時更新 symbolic + taint。

```python
ctx.taintRegister(ctx.registers.rdi)              # source
ctx.symbolizeRegister(ctx.registers.rdi, 'input')  # symbolic

# process instructions
for inst in trace:
    ctx.processing(inst)

# 查 taint
if ctx.isRegisterTainted(ctx.registers.rax):
    # 查 symbolic — 如果 sink 是 rax 的某個具體值
    ast = ctx.getSymbolicRegister(ctx.registers.rax)
    # 用 SMT solve
    model = ctx.getModel(ast == 0xdeadbeef)
    ...
```

這是 Triton 最 powerful 的用法：**同時知道**

- rax 被 attacker control（taint = true）
- rax 可以被解成特定值（symex → concrete input）

## 具體例子：exploit automation

假設 target binary 有：

```c
void target(char* input) {
    char buf[64];
    strcpy(buf, input);
    check(buf);
}

void check(char* buf) {
    if (buf[10] == 'X' && buf[20] == 'Y') {
        void (*fp)() = (void*)*(uintptr_t*)(buf + 30);
        fp();
    }
}
```

attacker 控 input、要把 fp 指到某個 address 去 exploit。要的 input 滿足：
- buf[10] == 'X'
- buf[20] == 'Y'
- buf[30..37] == 想要的 function pointer

### Pure symex 做法

```python
import angr, claripy
proj = angr.Project('./target')
input_sym = claripy.BVS('input', 8 * 100)
state = proj.factory.entry_state(stdin=input_sym)
simgr = proj.factory.simulation_manager(state)
simgr.explore(find=addr_of_fp_indirect_call)
if simgr.found:
    state = simgr.found[0]
    # 加 constraint：fp 要指到某個 addr
    state.add_constraints(fp_val == 0xcafebabe)
    print(state.solver.eval(input_sym, cast_to=bytes))
```

可行但慢 — path explosion。

### Triton hybrid 做法

```python
from triton import *

ctx = TritonContext(ARCH.X86_64)
# ... load binary ...

# 把 input buffer 標 taint + symbolic
for i in range(100):
    addr = input_addr + i
    ctx.symbolizeMemory(MemoryAccess(addr, 1), f'input_{i}')
    ctx.taintMemory(MemoryAccess(addr, 1))

# run
pc = entry_addr
while pc != ret_addr:
    opcodes = ctx.getConcreteMemoryAreaValue(pc, 16)
    inst = Instruction(opcodes)
    inst.setAddress(pc)
    ctx.processing(inst)
    pc = ctx.getConcreteRegisterValue(ctx.registers.rip)

# 到 fp call 時，檢查 fp 值
fp_ast = ctx.getSymbolicMemoryValue(MemoryAccess(fp_addr, 8))

# 確認 attacker 能控 fp
if ctx.isMemoryTainted(MemoryAccess(fp_addr, 8)):
    # solve：fp = 0xcafebabe
    model = ctx.getModel(fp_ast == 0xcafebabe)
    # model 裡的 input byte 就是 exploit payload
    print(model)
```

兩個檢查一起做：
- **taint check**：`fp_addr` 的 memory 真的被 input 染到
- **symbolic solve**：產出具體 payload byte

這是 **exploit automation** 的基本架構。真實工具（angrop、ropper、Mayhem）做的事更複雜（ROP chain 建、stack alignment），但核心就這個 pattern。

## Hybrid 的工程 cost

合體的代價：

- **記憶體**：一份 shadow 放 taint，一份放 symbolic AST，加 native memory。**3 倍 RAM**
- **CPU**：每個 instruction 兩層更新。**2–3× slowdown** 比 pure DTA
- **SMT**：symex 部分依然昂貴。如果你對 branch 每次 eval，還是回到 symex 的 cost

但合體相比兩個獨立 tool 跑兩次，整體快很多。

## Driller / QSYM / SymCC 也是 hybrid，但不同口味

注意 Ch 25 的三個 hybrid 也都是「symex + fuzzer」。但那是 **symex + fuzzing** 的 hybrid；這章談的是 **symex + taint** 的 hybrid。兩者不衝突、可疊加：

```
  Fuzzer ── taint-guided mutation ── Target (symex + taint instrumented)
   │               ▲                        │
   │               │                        │
   └──── new seed ─┘                        │
                                            │
                                     ┌──────▼─────┐
                                     │ Symex solve │
                                     └─────────────┘
```

這種四件式系統是 **Angora** 的架構（simplified）。精度很高、effort 很大。

## Triton 實務 workflow

```python
# 1. Setup
ctx = TritonContext(ARCH.X86_64)
ctx.setMode(MODE.ALIGNED_MEMORY, True)
ctx.setMode(MODE.ONLY_ON_TAINTED, True)  # 只對 tainted 記 symbolic (省記憶體)

# 2. 載 binary、設 memory
for seg in binary.segments:
    ctx.setConcreteMemoryAreaValue(seg.addr, seg.data)

# 3. 標 source
for i, b in enumerate(input_bytes):
    ctx.setConcreteMemoryValue(buf + i, b)
    ctx.symbolizeMemory(MemoryAccess(buf + i, 1), f'in_{i}')
    ctx.taintMemory(MemoryAccess(buf + i, 1))

# 4. 執行
run_until(ctx, target_pc)

# 5. 查 sink
sink_ast = ctx.getSymbolicMemoryValue(...)
if ctx.isMemoryTainted(...):
    # 解出 input 滿足 sink 條件
    model = ctx.getModel(sink_ast == target_value)
    payload = reconstruct_input(model)
```

`ONLY_ON_TAINTED` mode 是 Triton 的 gem：不 tainted 的 expression 不建 AST，省掉大量 RAM。

## 心法

Symex + taint hybrid 不是把兩個 tool 串聯就好，是**設計一個 shared infrastructure 讓兩者共享 instrumentation 與 state**。

適合場景：
- exploit automation
- 精確的 reachability + trigger analysis
- research：產生 "這個 byte 怎麼影響 path" 這類 fine-grained 結論

不適合：
- 一次性 bug find（fuzzing 足夠）
- 大型 target（memory 爆）
- 沒有「追 data flow + 找 trigger value」雙重需求的任務

## 自我檢核

- [ ] 解釋三個 hybrid pattern（taint-guided symex、symex-aided taint、一體化）
- [ ] 能寫出 Triton 的 symex + taint 一體化 workflow
- [ ] 知道 `ONLY_ON_TAINTED` mode 的 optimization 意義
- [ ] 了解 exploit automation 的基本 architecture
- [ ] 區分 Driller-style 跟 Triton-style 兩種 hybrid 的適用場景

最後一章總結整個領域的前沿與何時該換工具。

→ [Ch 28 — 前沿與何時該換工具](./28-frontier.md)
