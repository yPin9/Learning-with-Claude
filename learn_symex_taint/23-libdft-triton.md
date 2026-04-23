# Ch 23 — libdft 與 Triton 的架構解剖

> 目標：拆兩個代表作的內部。libdft 是 "純粹 DTA" 的代表，Triton 是 "symex + DTA 合體" 的代表。搞懂這兩個的構造，就看懂了現代 binary DTA 的設計譜系。

## libdft：純粹 DTA 的代表

**libdft**（Kemerlis, Portokalidis, Jee, Keromytis, EuroSys 2012）是**第一個 practical byte-level DTA** framework。今天仍是 research 用得最多的 Pin-based DTA。

### 設計目標

- **x86-64 完整覆蓋**：每個 x86 instruction 的 taint propagation rule
- **Efficient**：byte-level shadow、inline instrumentation、optimization 到極致
- **Framework, not tool**：你在上面寫自己的 tool，libdft 只管 propagation

### 架構

```
  ┌──────────────────┐
  │   your tool      │  ← 你實作 source / sink hook
  │   (libdft-based) │
  └────────┬─────────┘
           │ callbacks
  ┌────────▼─────────┐
  │   libdft core    │
  │                  │
  │  taint rules     │  ← 每個 opcode 一條 propagation rule
  │  shadow memory   │  ← bitmap-based
  │  register shadow │
  └────────┬─────────┘
           │ instrument
  ┌────────▼─────────┐
  │   Pin            │
  └──────────────────┘
```

### taint rule 舉例

對 x86 `add rax, rbx`：

```c
void dta_binary(INS ins, uint32_t opcode) {
    INS_InsertCall(ins, IPOINT_BEFORE,
        (AFUNPTR)_propagate_r2r,
        IARG_INST_PTR,
        IARG_UINT32, INS_OperandReg(ins, 0),   // rax (dst)
        IARG_UINT32, INS_OperandReg(ins, 1),   // rbx (src)
        IARG_END);
}

void _propagate_r2r(ADDRINT ip, uint32_t dst_reg, uint32_t src_reg) {
    // merge src 的 taint 進 dst
    for (int i = 0; i < 8; i++) {
        reg_shadow[dst_reg][i] |= reg_shadow[src_reg][i];
    }
}
```

每個 opcode 對應一個 propagation function。x86 有幾百個 opcode，libdft 實作了其中常用的 ~150 個（覆蓋 > 99% 實際 binary）。

### shadow layout

libdft 的 shadow memory 是 bitmap：

```c
#define SHADOW_SIZE (1 << 29)    // 512 MB for x86-32 user space
uint8_t shadow_bits[SHADOW_SIZE];

#define IS_TAINTED(addr) \
    ((shadow_bits[(addr) >> 3] >> ((addr) & 7)) & 1)
```

register shadow：`libdft_regs[R_RAX].taint[0..7]`，每 register 8 個 byte，各自 1 bit taint。

### Source / Sink hooking

你的 tool 繼承 libdft API：

```c
void syscall_pre(THREADID tid, INT32 syscall_num) {
    if (syscall_num == SYS_read) {
        uintptr_t buf = syscall_arg(1);
        size_t len  = syscall_arg(2);
        // 標 tainted
        for (size_t i = 0; i < len; i++) {
            TAINT_SET(buf + i);
        }
    }
}

void function_call_handler(const char* name, uintptr_t arg) {
    if (strcmp(name, "system") == 0) {
        if (IS_TAINTED(arg)) {
            alert("command injection at %p", ip);
        }
    }
}
```

libdft 框架幫你處理 propagation、你只寫 source / sink 邏輯。

### 限制

- **x86 only**：ARM / MIPS 沒版本
- **Pin-based**：帶 Pin 的所有限制
- **Implicit flow 不追**
- **ring 3 only**：不 touch kernel

這些限制對大部分 research 足夠用。production security tool 通常從 libdft fork 再改。

## Triton：DTA + Symex 合體

**Triton**（Jonathan Salwan et al., 2015~）是 binary analysis framework，核心是 **SMT-based symex**，同時提供 taint tracking。

### 設計哲學

libdft 的精神是 "一個快 DTA"，Triton 的精神是 "一個 introspection API"。Triton 不自己 drive execution — 你餵 instruction 進來，它回訴你 semantic（symbolic / taint）。

```
你的程式：

  while (next_instruction):
      ctx.processing(inst)        ← 餵 instruction
      taint = ctx.isTainted(reg)  ← 查詢 state
      symbol = ctx.getSymbolic(reg) ← 查詢 symbolic
```

這種 API 設計讓 Triton 可以配上**任何 DBI** — Pin、Frida、Unicorn、QEMU plugin 都行。

### 架構

```
       Your driver (Python / C++)
         │
         │ ctx.processing(inst), ctx.isTainted(reg), ...
         ▼
   ┌──────────────────────┐
   │  Triton Context       │
   │                       │
   │  ┌──────────────────┐ │
   │  │ Concrete engine  │ │  ← 跑具體 value
   │  └──────────────────┘ │
   │                       │
   │  ┌──────────────────┐ │
   │  │ Symbolic engine  │ │  ← 產生 SMT expression
   │  └──────────────────┘ │
   │                       │
   │  ┌──────────────────┐ │
   │  │ Taint engine     │ │  ← 維護 taint label
   │  └──────────────────┘ │
   │                       │
   │  ┌──────────────────┐ │
   │  │ AST representation│ │  ← unified SMT AST
   │  └──────────────────┘ │
   └───────┬───────────────┘
           │
           ▼
   ┌──────────────────────┐
   │  SMT backend (Z3)     │
   └──────────────────────┘
```

每個 engine 看同一份 instruction、平行更新自己的 state。

### 範例：Triton 對一條 instruction 的處理

```python
from triton import TritonContext, ARCH, Instruction, MODE

ctx = TritonContext(ARCH.X86_64)
ctx.setMode(MODE.ALIGNED_MEMORY, True)
ctx.setMode(MODE.ONLY_ON_TAINTED, True)  # 只對 tainted instruction 記 symbolic

# 初始：taint rax
ctx.taintRegister(ctx.registers.rax)

# 處理 mov rbx, rax (機械碼: 48 89 c3)
inst = Instruction(b"\x48\x89\xc3")
ctx.processing(inst)

print("rbx tainted?", ctx.isRegisterTainted(ctx.registers.rbx))
# True

# 看 symbolic expression
expr = ctx.getSymbolicRegister(ctx.registers.rbx)
print(expr.getAst())
# (_ bv0 64)   (或什麼實際值的 SMT 表示)
```

### Symex 查詢

```python
# 給 rax 加 symbolic 約束
rax_sym = ctx.symbolizeRegister(ctx.registers.rax, 'rax_in')
# 繼續處理幾條 instruction...
ctx.processing(inst_seq)

# 最後拿 symbolic value
final_rbx = ctx.getSymbolicRegister(ctx.registers.rbx)
expr = final_rbx.getAst()

# 問 SMT：rbx 能不能等於 0xdeadbeef？
model = ctx.getModel(expr == 0xdeadbeef)
for k, v in model.items():
    print(k, '=', hex(v.getValue()))
```

一套 API 同時做 symex + taint。**這就是 Triton 的 power**。

### Triton 的特殊設計：AST 統一

Triton 內部用 **unified AST**：concrete value 跟 symbolic expression 都是同一種 Node type。

- concrete：`BV(value, size)` 這種 leaf node
- symbolic：`BVVar('name', size)` leaf + operator 組合

taint 是 node 的一個 flag（tainted / untainted）。

這個 unified design 讓 symex 跟 taint 共用 infrastructure：

```
AST node:
  type:  BVADD
  lhs:   BVVar "in_0"            ← tainted
  rhs:   BV(5, 32)
  result_tainted: true (inherited from lhs)
  symbolic: true (inherited from lhs)
```

單一次 instruction processing 兩件事一起做。

### Triton 不驅動 execution

注意 Triton 自己不 fetch instruction、不 decide PC 走哪。它是個**後端分析器**：

```python
# 你的 driver loop
for inst in pin_trace:
    inst_obj = Instruction(inst.bytes)
    inst_obj.setAddress(inst.addr)
    ctx.processing(inst_obj)
    
    if inst.is_branch:
        # 你自己決定下一條
        if should_explore_alt:
            ctx.concretizeBranch(inst, not inst.taken)
```

這個設計讓你非常彈性：
- **concolic execution**：餵 concrete trace、Triton 記 symbolic
- **symbolic replay**：給 initial state、手動控 PC
- **DTA only**：不產 symbolic、只看 taint

Driller、QSYM-lite、多個 research 工具就用 Triton 做 concolic 層。

## libdft vs Triton 的對比

| 面向 | libdft | Triton |
|------|--------|--------|
| 語言 | C++ | C++ + Python binding |
| 底層 DBI | Pin (固定) | 任意（Pin / Unicorn / 手動）|
| Symex? | 否 | 是 |
| Taint granularity | byte | byte |
| Implicit flow | 不追 | 可選 |
| Pointer taint | 不追 | 可開 |
| Speed | 非常快 | 中等 |
| API 層級 | Low | High |
| 適合 | 純 DTA、高速 | Mix symex + taint、靈活 |

## 在哪找它們

- libdft: <https://github.com/AngoraFuzzer/libdft64>（維護較少，forks 多）
- libdft-ng: <https://github.com/AngoraFuzzer/libdft-ng>（較新）
- Triton: <https://github.com/JonathanSalwan/Triton>（積極維護）

libdft 原 repo 已 archive；今天 research 社群多用 libdft-ng 或 libdft64 fork。

## Triton 實戰範例：反向一個 hash function

一個 classic 用法：target 有 `check(input) == 0x13370`，你想 recover input。

```python
from triton import *

ctx = TritonContext(ARCH.X86_64)

# load 一個 target binary 到記憶體 ...
# 假設 check() 位於 0x401000，參數是 rdi (input)

# 對 rdi 塞 symbolic
input_var = ctx.symbolizeRegister(ctx.registers.rdi, 'input')

# 設 PC
ctx.setConcreteRegisterValue(ctx.registers.rip, 0x401000)

# 跑到 return
while ctx.getSymbolicRegisterValue(ctx.registers.rip) != ret_addr:
    pc = ctx.getConcreteRegisterValue(ctx.registers.rip)
    opcodes = ctx.getConcreteMemoryAreaValue(pc, 16)
    inst = Instruction(opcodes)
    inst.setAddress(pc)
    ctx.processing(inst)

# return value 在 rax
rax_ast = ctx.getSymbolicRegister(ctx.registers.rax).getAst()

# 問 SMT：rax 何時 == 0x13370？
constraint = rax_ast == 0x13370
model = ctx.getModel(constraint)
print('input =', hex(model[input_var.getId()].getValue()))
```

**跟 angr 的 script 差不多寫法**。差別：Triton 需要你手動控 PC（angr 自動 fork）。換來的是更明確的 control、更快的 performance。

## 為什麼 Triton 在 CTF / hybrid fuzzing 很紅

- 比 angr 輕量 — 內存佔用小
- Python API 對 DBI trace 支援好
- 能做 "給我 trace、反推 input" 這類任務

Triton 是 SSTIC / DEFCON 類 conference 上 RE 研究者的偏好工具。

## 對你寫 DTA 工具的建議

想自己寫一個 dynamic taint analyzer：

1. 先用 **libdft** 當 template，改它的 source / sink，不重寫 framework
2. 想加 symex 功能 → 切到 **Triton**，寫 driver
3. 想 mobile / macOS → **Frida** + 自己寫簡單 taint
4. 想跨 arch → **QEMU plugin** + Triton

不要從零寫 DBI 自己那層。那是 20 person-year 的 engineering。

## 心法

libdft 跟 Triton 是**研究傳統 vs 工程彈性**的代表。

- libdft 讀 paper、academic 背景強、對 x86 instruction 理解最深
- Triton 程式設計哲學更好、API 更彈性、能做 symex

選擇：
- 學 DTA → 先 libdft 讀 source（會看到每個 opcode 的 propagation rule）
- 寫工具 → Triton（Python + 彈性）

兩個都讀過一遍後，你對 binary analysis 的工程細節有非常完整的理解。

## 自我檢核

- [ ] 能畫出 libdft 的架構（Pin + shadow + taint rule）
- [ ] 能畫出 Triton 的架構（concrete/symbolic/taint engine + unified AST）
- [ ] 解釋 Triton 為什麼不驅動 execution
- [ ] 會寫一個 "mov rbx, rax → taint propagates" 的 Triton 實例
- [ ] 知道 libdft 跟 Triton 各自的最佳 use case

下一章把 DTA 的實戰應用拆開 — taint 怎麼用來找 exploit、怎麼算 reachability、怎麼做 leak detection。

→ [Ch 24 — Taint 的攻擊面應用：exploit 可達性、漏洞發現](./24-taint-applications.md)
