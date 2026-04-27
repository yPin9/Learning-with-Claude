# Ch 14 — angr 架構：VEX IR、SimState、SimProcedure

> 目標：把 angr 內部結構從 Project 一路拆到 SimEngine。看完你要能在 angr 出錯時判斷哪一層在炸。

## angr 是什麼、從哪來

**angr**（UCSB SEFCOM lab，Yan Shoshitaishvili 等人，S&P 2016 "State of the Art of War"）是 Python + C++ 的 **binary** 分析框架。

起源：CGC 競賽的 team Shellphish 需要一個能跑任意 binary 的 symex 工具。當時 KLEE 只能吃 LLVM IR、Mayhem 閉源。他們 fork 了 Sean Heelan 的 pyvex，加上 Z3 wrapper（claripy）、自己的 loader（CLE）、binary-specific 的 memory model，拼出 angr。

今天 angr 是：
- 最 popular 的 binary symex 工具
- CTF reverse 的標準武器
- binary vuln research 的 Python 標配

## 為什麼用 VEX IR

angr 不在 raw machine code 上跑 symex，跟 KLEE 不在 raw C 上跑一樣 — 中間有 IR。

VEX 是 **Valgrind 的 IR**。angr 的設計者 pick 它的原因：

- 支援 x86、x86-64、ARM、ARM64、MIPS32/64、PPC32/64、s390x — **幾乎所有 ISA**
- 成熟（Valgrind 用了 20 年）
- lift 過程有完整 test（binary instruction semantics 可信）
- Python binding（pyvex）現成

VEX 本身是 stack-based IR，每個 machine instruction 被拆成 IR block（**IRSB**），IRSB 裡是 `t0 = ..., t1 = ...` 這種 SSA-ish statements。

例：

```
x86: mov eax, [rbx + 0x10]
     ↓ pyvex
IRSB:
   t0 = Get:I64(rbx)
   t1 = Add64(t0, 0x10)
   t2 = LDle:I32(t1)
   Put(eax) = t2
```

angr 在 VEX 上做 symex — 跟 KLEE 在 LLVM IR 上做 symex 是對等的設計。

## 整體架構分層

```
                 你的 Python script
                       │
                       ▼
                  angr.Project
                       │
             ┌─────────┼────────────┐
             │         │            │
             ▼         ▼            ▼
           CLE        factory     analyses
         (loader)       │        (CFG, DDG, VSA, ...)
             │          │
             │          ▼
             │      SimState ────┐
             │          │        │
             │          │     plugins:
             │          │      - regs
             │          │      - memory
             │          │      - posix
             │          │      - solver
             │          │      - history
             │          │      - callstack
             │          │
             │          ▼
             │       SimEngine
             │        (VEX, Unicorn, SOOT, ...)
             │          │
             │          ▼
             │    pyvex lift + claripy (z3)
             ▼
        binary / ELF / PE / Mach-O
```

六個 key components，各自拆：

### 1. Project

進入點：

```python
import angr
proj = angr.Project('./target', auto_load_libs=False)
```

Project 物件裝：
- Loaded binary（透過 CLE）
- 架構資訊（`proj.arch` — x86-64、arm、...）
- Factory（創 state、SimEngine 的方法）
- Analyses（CFG、DDG、VSA 等分析器的 entry）

`auto_load_libs=False` 是 Ch 10 講過的 — 不載 libc.so，改用 SimProcedure hook。常見 option。

### 2. CLE (Loader)

CLE 是 angr 的 loader（名字是 "CLE Loads Everything" 的首字母遞迴）。把各種 binary format load 進 memory：

- ELF（Linux）
- PE（Windows）
- Mach-O（macOS）
- firmware blob（raw binary + base address）

```python
proj.loader.main_object            # 主 binary
proj.loader.shared_objects         # .so / .dll
proj.loader.memory.load(0x400000, 4)  # 讀 4 byte
```

CLE 負責 section mapping、relocation、symbol table。如果你 load 的 binary 有 protection（PIE、ASLR），CLE 在 load 時選一個 base。

### 3. Factory

創 state、SimEngine 用：

```python
state = proj.factory.entry_state()       # 從 entry point 開始
state = proj.factory.blank_state(addr=X) # 從任意 address 開始
state = proj.factory.call_state(addr, args...) # 模擬 function call
state = proj.factory.full_init_state()    # 模擬完整 initialization
```

幾個 entry state 的差別：
- `entry_state`：從 `_start` 開始、stack 裝 argv / envp（要是 full binary）
- `blank_state`：什麼都沒設，你全部自己 setup（常見在 hook 某個 function）
- `call_state`：假裝呼叫一個 function，你指定 args

`simulation_manager` 也是 factory 創：

```python
simgr = proj.factory.simulation_manager(state)
```

### 4. SimState

每個 symbolic state 的所有資訊。上面架構圖顯示它是 plugin-based：

```python
state.regs.rax                # register plugin
state.memory.load(addr, 8)    # memory plugin
state.solver.add(...)          # solver plugin (constraints)
state.posix.dumps(1)           # POSIX plugin (stdout)
state.history.descriptions     # 走過的 path
state.callstack                # shadow callstack
state.globals                   # 你自己放的 user data
```

每個 plugin 是獨立 class、fork state 時會被 deep-copy（或 copy-on-write）。

fork 的代價：十來個 plugin、每個有可能 MB 級的資料。這是為什麼 angr 慢、memory 重。

### 5. SimEngine

執行 IR 的地方。angr 有幾個 engine：

- **VEX engine**：預設，跑 VEX IR
- **Unicorn engine**：碰到 concrete-heavy 的 block 時切換到 Unicorn（QEMU 的 CPU emulator），繞開 VEX 的 symbolic overhead
- **SOOT engine**：Java bytecode
- **SimProcedure engine**：被 hook 的 function，直接執行 Python code 而不跑 IR

每次 `state.step()`，engine 執行**一個 basic block** 的 IR，回傳一個 list of successor states（可能 fork 多條）。

### 6. Analyses

獨立的分析器：
- **CFG**（Fast / Emulated，Ch 15 詳講）
- **DDG**：data dependency graph
- **VSA**：value set analysis（abstract interpretation）
- **Decompiler**：angr 自帶的 decompiler（angr-management 的主要輸出）

這些分析 **不是** symex 本身，是輔助工具（找 function、算 cross-reference、...）。

## 跑 hello 例子的 internal 走向

```python
proj = angr.Project('./crackme')
state = proj.factory.entry_state()
simgr = proj.factory.simulation_manager(state)
simgr.explore(find=0x400800)
```

step by step：

1. `Project()` → CLE load binary，建立 memory layout、symbol table
2. `entry_state()` → 創 SimState：regs 初始化（sp 指向 fake stack）、memory mmap binary 的 segments、PC = entry point
3. `simulation_manager(state)` → 包一個 manager，state 放到 `simgr.active`
4. `simgr.explore(find=X)`：
   - while `simgr.active` 非空：
     - 對每個 active state call `state.step()`
     - `step()` 呼叫 VEX engine：lift 當前 PC 的 block 為 IRSB、逐 stmt 執行、fork 出 successors
     - 如果 successor 的 PC == X → 移到 `simgr.found`
     - 否則繼續 active
5. 最後 `simgr.found` 有一條或多條 state，你可以 `.solver.eval` 拿 input

每一步都有**非常多細節**，但框架就是這樣。

## SimProcedure 的特殊地位

Ch 10 介紹過。這裡強調一點：**SimProcedure 是 engine 的一種**。

當 PC 走到一個被 hook 的 address，angr 不 call VEX engine，而是 call SimProcedure engine — **Python code 直接產生 successor states**。

```python
class my_hook(angr.SimProcedure):
    def run(self, arg1, arg2):
        # self.state 是當前 state
        # arg1, arg2 是 calling convention 正確的參數
        result = ...
        return result  # 放到 rax / return register
```

hook 可以透過：
- `proj.hook(address, sim_proc)`
- `proj.hook_symbol('strcmp', sim_proc)`
- 自動 hook（`proj.hooked_by(addr)` 查）

angr 內建 ~500 個 SimProcedure，`angr/procedures/` 下面看。

## claripy 是什麼

angr 的 SMT wrapper。你看到的 `state.solver.BVS`、`state.solver.BVV`、`state.solver.If` 全是 claripy。

claripy 做的事：
- 維護 AST（claripy.ast.BV、claripy.ast.Bool）
- 提供 simplification / rewriting
- 包裝多種 backend：z3 (primary)、CVC4、bit-blasting

你可以獨立用 claripy：

```python
import claripy
x = claripy.BVS('x', 32)
y = claripy.BVV(5, 32)
z = x + y
solver = claripy.Solver()
solver.add(z == 10)
print(solver.eval(x, 1))   # [5]
```

跟 z3-solver Python API 的差別：claripy 有更 aggressive 的 simplification，預設走 z3 backend 但可切。

## 跟 KLEE 的 component 對映

| KLEE | angr |
|------|------|
| Executor 主 loop | SimulationManager + SimEngine |
| ExecutionState | SimState |
| LLVM Interpreter | VEX engine |
| POSIX runtime（C） | SimProcedure（Python） |
| ConstraintManager | state.solver |
| AddressSpace | state.memory（SimMemory plugin） |
| Searcher | ExplorationTechnique |
| STP / Z3 | claripy / z3 |

兩個系統的架構是**鏡像對應**，只是 KLEE 用 C++ 純粹實作、angr 用 Python 彈性組合。

## 為什麼 angr 比 KLEE 慢

- Python 本身比 C++ 慢 10–100×
- SimProcedure 每次 call 要 Python → C 的 FFI 開銷
- 多層 plugin 的 dispatch cost

但 angr 的 **彈性遠高於 KLEE**。寫一個 custom exploration strategy，angr 幾十行 Python、KLEE 要改 C++ 然後重 compile。**研究跟 RE 場景，angr 壓倒性勝**。

## 常見 angr error 的定位

**`SimUnsatError`** — PC 不 satisfiable 了。通常 hook 或 constraint 錯了。看 `state.solver.constraints` 找哪條衝突。

**`SimSegfaultException`** — target 自己 crash 了（或 symex 推到不合法 address）。

**`SimEngineError: Ran out of instructions`** — IR block 結束但沒有 jmp/ret。可能 binary 不完整、或 VEX lift 某個 instruction 失敗。

**`Too many values for ...`** — symbolic concretize 超過 threshold。解法見 Ch 9。

**`Unsupported syscall`** — 該 syscall 沒 SimProcedure。解法：自己寫或 fallback。

`angr.state_plugins.history.SimHistory` 的 `descriptions` 可以回頭看每一步走了什麼，debug 必備。

## 心法

angr 是一個**黏合層**：CLE 做 loading、pyvex 做 lifting、claripy 做 SMT、SimProcedure 做 model、SimEngine 做 execution、SimulationManager 做 scheduling。

每一層都可以獨立拿出來用。你不一定要跑完整 symex 才能用 angr：
- 只做 CFG 分析 → `proj.analyses.CFGFast()`
- 只做 disassembly → `proj.factory.block(addr).pp()`
- 只做 symbolic solving → 用 claripy
- 只做 loading → 用 CLE

這個彈性讓 angr 成為 binary analysis 的 **Swiss army knife**，但也是複雜度來源。

## 自我檢核

- [ ] 解釋 CLE、factory、SimState、SimEngine、SimulationManager 各自角色
- [ ] 知道 VEX IR 的存在與它支援的架構範圍
- [ ] 能區分 entry_state / blank_state / call_state 的使用場景
- [ ] 解釋 SimProcedure 為什麼是 engine 的一種
- [ ] 看 angr 的 component 時能對映到 KLEE 的 component

下一章拆 angr 的 **CFG 系統** — CFGFast 跟 CFGEmulated 的差別，為什麼一個快一個準、什麼時候選哪個。

→ [Ch 15 — CFGFast vs CFGEmulated](./15-angr-cfg.md)
