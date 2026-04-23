# Ch 4 — Concrete vs symbolic value 與 memory model

> 目標：把 symbolic value 是什麼拆到位 — 它不是一個特別的變數，它是一個 SMT formula。memory 是 symex 最骯髒的部分，這章先把 register / memory 的理想模型建起來，骯髒的部分留給 Ch 9。

## Symbolic value 就是 formula

在 symex 裡，一個「值」從來不是單純的 integer。它是一個 **SMT formula**。對 32-bit integer，formula 的 sort 是 `BitVec(32)`。

```python
# angr 裡的表示
import claripy
x = claripy.BVS('x', 32)    # symbolic 32-bit var
y = x + 5                    # y 的型別是 BV，內容是 (bvadd x #x00000005)
z = y * 2                    # z = (bvmul (bvadd x 5) 2)
```

跑完上面三行，`z` 不是 concrete number，它是一個 AST：

```
z = bvmul
      ├── bvadd
      │     ├── x (BVS)
      │     └── 5 (BVV)
      └── 2 (BVV)
```

**每一個「運算」都只是 formula 的 node 構造**。沒有計算發生。真正的計算在最後丟給 SMT solver 時才發生。

這個觀察是 symex engine 內部最重要的 invariant：**engine 只是在建 formula**。它不計算、不執行、不求值，直到你問它「這個 formula 滿足什麼？」。

## BVS 與 BVV

claripy（angr 的 SMT wrapper）分兩種：

- **BVV**（Bit-Vector Value）：concrete 常數。`BVV(5, 32)` 代表 32-bit 的 5。
- **BVS**（Bit-Vector Symbol）：symbolic 變數。`BVS('x', 32)` 代表一個未知的 32-bit 值，名字叫 `x`。

混合運算時，BVV 就是 formula 裡的常數 node，BVS 就是 variable node：

```
claripy.BVV(3, 8) + claripy.BVS('a', 8) 
  → AST: bvadd(#x03, a)
```

SMT solver 看到這種 AST 可以 reason；看到 Python 的 int 不行。所以 symex engine 幾乎**永遠在 BV** 層面操作。

## Concrete value 也在這個世界裡

你可能想：「那 x = 5；y = x + 3 這種 concrete path 呢？」

engine 內部一樣建 BV：`x` 綁定 `BVV(5)`，`y` 綁定 `BVV(8)`。但 SMT 不需要被呼叫 — engine 看到運算兩邊都是 BVV，直接 fold 成新的 BVV。這個 fold 叫 **constant propagation**，是 symex engine 的第一個 critical optimization。

angr 裡會看到：

```python
x = claripy.BVV(5, 32)
y = x + claripy.BVV(3, 32)
print(y)                    # <BV32 0x8>
print(y.concrete)           # True
print(y.concrete_value)     # 8
```

y 已經被簡化到 concrete。沒有 symbolic 進來之前，symex 基本上是 concrete 執行；一旦 symbolic 變數進來、散播開，整條運算鏈才開始膨脹成 formula。

## Register model：最簡單的那層

Register 有限、名字固定、大小固定。model 直接就是一個 map：

```
σ_reg: RegName → BV
       rax ↦ BV64
       rbx ↦ BV64
       ...
       rip ↦ BV64   (通常 concrete)
```

對 sub-register 的 read/write（`al`, `eax` vs `rax`）要靠 bit-extract：

```
write eax, v:    rax = concat(extract(rax, 63, 32), v)   // 高 32 保留，低 32 換
read al:         extract(rax, 7, 0)
```

這在 Triton、angr 內部都是基礎 operation。你會在 VEX IR dump 裡看到一堆 `Iex_Put`、`Iex_Get`，就是 register R/W。

## Memory model：噩夢開始

memory 有兩個問題 register 沒有：

1. **address 可以是 symbolic** — `a[i]` 當 `i` 是 symbolic variable 時，你不知道寫到哪
2. **size 不固定** — 4 GB virtual memory，不能全部 materialize

不同 engine 有不同取捨。

### 模型 A：純 concrete-addressed

地址必須是 concrete value。symbolic address 看到就 concretize（取一個 model、降階成 concrete）或 bail out。

內部結構：一個 hashmap `Dict[int, BV8]`（byte-addressable）。

```
store 0x400000 = α      # α 是 BV8
store 0x400001 = β
load 0x400000           # 回傳 α
```

**優點**：快、formula 乾淨、SMT 壓力最小。
**缺點**：遇到 `a[i]`（i symbolic）無法自動處理；需要 engine 或使用者介入。

KLEE **基本上**用這個（它會把 symbolic pointer 做 fork，見下文）。

### 模型 B：fully symbolic memory

用 SMT array theory：memory 整體是 `Array(BV64, BV8)`。load/store 是 theory operation：

```
mem_0: Array(BV64, BV8)   (初始狀態)
mem_1 = store(mem_0, p, α)
v = load(mem_1, q)        (q 可以是任何 symbolic address)
```

load 時 solver 用 array axiom：`select(store(a, i, v), j) = ite(i = j, v, select(a, j))`。可以 reason 任意 symbolic address。

**優點**：能完整 model `a[i]` 這種 case。
**缺點**：formula 迅速膨脹。十萬次 store 後，每次 load 的 formula tree 有十萬層深。SMT 會燒掉。

S2E 默認開這個、angr 有 `SymbolicMemory` mode（預設不開）、Triton 也可用但慢。

### 模型 C：混合（angr 預設）

angr 的 `default_memory_plugin` 叫 `SimMemory`，混合兩者：
- address concrete → 直接寫 hashmap
- address symbolic → 嘗試 concretize 成幾個可能值（藉 SMT enumerate）、各自 fork state
- load 時也類似：symbolic address load → 取幾個可能的 address 各自回不同值

簡化 pseudocode：

```python
def store(addr, val):
    if addr.concrete:
        mem[addr.concrete_value] = val
    else:
        concretes = solver.eval_upto(addr, max=256)
        if len(concretes) == 1:
            mem[concretes[0]] = val
        elif len(concretes) < THRESHOLD:
            for a in concretes:
                fork_state(mem_update={a: val})
        else:
            raise SymbolicConcretizationFailure()
```

這個「concretize 成幾個可能」叫 **address concretization**。數量超過 threshold 就要嘛 fork 太多 state、要嘛放棄。Ch 9 會細拆。

## Endianness 與 multi-byte access

memory 是 byte-addressable，CPU 讀寫常常是 4/8 byte。concat 的 order 要對：

```
write 32-bit value V at addr A (little-endian):
    mem[A]   = extract(V, 7, 0)
    mem[A+1] = extract(V, 15, 8)
    mem[A+2] = extract(V, 23, 16)
    mem[A+3] = extract(V, 31, 24)

read 32-bit at addr A:
    V = concat(mem[A+3], mem[A+2], mem[A+1], mem[A])
```

x86-64 是 little-endian、ARM 通常是，MIPS big-endian。symex engine 都會 encode 這層。bug 之一 — 用錯 endianness — 抓得你哭。

## Symbolic read 的 aliasing 問題

考慮：

```c
*p = α;
*q = β;
x = *p;   // x == α 嗎？
```

如果 p 跟 q 都是 concrete、不同 address，那 `*p == α`。但如果 p 跟 q 都 symbolic、可能相同也可能不同，那 `*p` 是 `ite(p == q, β, α)`。

這叫 **pointer aliasing**。它讓 formula 爆炸：每次 store 都要 `ite` 之前所有可能 alias 的 store。十次 symbolic store 後，一次 load 的 formula 有 2^10 層。

工程對策：
1. 用 SMT array theory（模型 B）讓 solver 自己處理
2. 做 **alias analysis** 先分群，只在 alias 可能的組裡做 `ite`
3. 放棄精度 — 宣告 symbolic address 永遠不 alias（unsound 但常見）

## Heap / stack 怎麼抽象

真實 program 有 malloc / free / alloca。symex engine 怎麼 model？

- **Heap**：一個獨立的 allocator，追蹤每次 malloc 的 `(addr, size)`。`malloc(n)` 回傳新 symbolic address 或 concrete 的 bump-pointer。KLEE 用 bump allocator（每次往下走）、angr 有 `SimHeap` plugin。
- **Stack**：就是 memory 的一段，rsp 寫寫讀讀。alloca 等同 rsp 減。

UAF（use-after-free）、double-free 怎麼找？工具要在 free 時把那塊標記為 invalid，後續 load 就報 bug。KLEE 對這個特別強。

## 為什麼 C 比 binary 難做？

反直覺 — 大部分人覺得 binary 比 C 複雜。symex 的角度正好相反：

- **C source → LLVM IR**：type 資訊完整，struct field 有 offset、pointer 有 type。KLEE 能精準知道 `p->x` 對應哪一塊 memory。
- **Binary**：全都是 byte offset，struct field 推不出來。`mov [rbx+8], rax` — 是在寫 struct 的哪個 field？不知道。symex 只能當成 `mem[rbx+8]`、所有 field 混成一團。

所以 KLEE 的精度通常**比 angr 高**（在同一個程式的 C 原始碼可用時）。angr 的應用場景是 **binary only**，不是它比較強，是它別無選擇。

## 實驗：看 formula 長什麼樣

```python
import angr, claripy

proj = angr.Project('/bin/true', auto_load_libs=False)
state = proj.factory.entry_state()

x = claripy.BVS('x', 32)
y = (x + 5) * 2
z = y & 0xff

print(z)
# <BV32 (x_0_32 + 0x5) * 0x2 & 0xff>

print(state.solver.eval(z, extra_constraints=[x == 3]))
# 16    ( (3+5)*2 = 16, & 0xff = 16 )

# 改約束試試
print(state.solver.eval(z, extra_constraints=[x == 100]))
# 210
```

claripy 內部的 AST 長怎樣：

```python
print(z.ast)        # 或直接 repr
print(z.op, z.args) # __and__, (<BV32 ...>, <BV32 0xff>)
```

你每寫一個 `+`、`&`、`>>`，engine 都只是在建 AST node。運算的「costly」部分永遠在 `solver.eval`、`solver.satisfiable` 這類 call。

## 為什麼懂這個很重要

日常寫 angr script 時，這個認知決定你的 debugging 能力：

- **看到 formula 很長很慢**：是不是你不小心把 memory 當 symbolic 傳太深？
- **看到 `Unconstrained`**：是不是讓 `rip` 變 symbolic 了（jump 到無效 address）？
- **看到 SMT timeout**：是不是 pointer aliasing 把 ite 堆疊變 2^N 層？

這些 issue 的 debug 都回到「我的 AST 現在長怎樣」。`state.solver.constraints` 印出來看，幾乎每個 symex 老手都這樣做。

## 自我檢核

- [ ] 能解釋「symbolic value 就是 formula」、engine 只是在建 AST
- [ ] 區分 BVV 與 BVS；知道 constant folding 在哪發生
- [ ] 能畫出 register model 的 map 結構，解釋 sub-register R/W
- [ ] 比較 concrete-addressed / fully symbolic / 混合三種 memory model 的取捨
- [ ] 解釋 pointer aliasing 為什麼讓 formula 爆炸
- [ ] 解釋為什麼 C source symex (KLEE) 比 binary symex (angr) 精度高

下一章把 path constraint 怎麼丟給 SMT solver 講完 — incremental solving、constraint independence、這些工程手段實際上發生在哪。

→ [Ch 5 — Path constraint 如何轉成 SMT query](./05-path-constraint-to-smt.md)
