# Ch 11 — KLEE 架構：LLVM IR 上做 symex 的理由

> 目標：拆開 KLEE 的設計哲學與 component。看完你要能解釋 KLEE 跑起來時，LLVM IR、POSIX runtime、uclibc、solver 之間的資料流。

## KLEE 是什麼

**KLEE**（Cristian Cadar, Daniel Dunbar, Dawson Engler, OSDI 2008）— 對 LLVM bitcode 做 pure symbolic execution。

當初的動機：作者群之前做 EXE（類似 DART 的 concolic + source 層 symex），苦於**parsing C 的麻煩跟 corner case**。LLVM 剛崛起，Cadar 做 PhD 時決定**改在 LLVM IR 上跑**。結果是：KLEE 這套直到今天仍是 C/C++ symex 的黃金標準。

核心承諾：
- 輸入是 **LLVM bitcode**（.bc 檔）
- 你把 C 用 `klee_make_symbolic` 標注 input
- 它自動產生 test case，cover 盡可能多 path、順手抓 bug

## 為什麼選 LLVM IR，不選 source 或 machine code

### 不在 source 跑的原因

- C 解析是噩夢（macro、typedef、#include、不同 compiler 方言）
- C++ 更糟（template、operator overload、virtual dispatch）
- 同一個 source 編不同 target（x86 / arm）會有不同 behavior

### 不在 machine code 跑的原因

- 失去 type / struct 資訊（Ch 9 的 symbolic memory 難題）
- 各架構 IR 不同（x86 / arm / mips 都要實作一套）
- 編譯 optimization 可能把 code 改到面目全非

### LLVM IR 的剛好

- **SSA**：每個 variable 定義一次，分析乾淨
- **typed**：integer type 精確（i8, i16, i32, ...）、struct field 有名字
- **跨語言**：C、C++、Rust、Swift 都編到 LLVM
- **跨 arch**：你可以在 x86 linux 上跑 target 原本是 arm 的 bitcode
- **成熟**：llvm-project 開源、每年投入數百 engineer-year

KLEE 跳過 parsing 與架構，**直接把 LLVM interpreter 改造成 symbolic interpreter**。

## 整體架構

```
        C source               ← 你寫
           │
        clang -emit-llvm
           │
           ▼
       target.bc  ────┐
                      │
        POSIX runtime │
        uclibc.bc  ───┤
                      │
                      ▼
        llvm-link 合併
                      │
                      ▼
        ┌────────────────────────┐
        │   KLEE Executor        │
        │                        │
        │   ┌─────────────────┐  │
        │   │ Interpreter     │  │  （LLVM IR interpreter）
        │   └────────┬────────┘  │
        │            │           │
        │   ┌────────▼────────┐  │
        │   │ ExecutionState  │  │  （symbolic state）
        │   └────────┬────────┘  │
        │            │           │
        │   ┌────────▼────────┐  │
        │   │ ConstraintMgr   │  │  （PC + independence + cache）
        │   └────────┬────────┘  │
        │            │           │
        │   ┌────────▼────────┐  │
        │   │ Solver (STP/Z3) │  │
        │   └─────────────────┘  │
        └────────────────────────┘
                     │
                     ▼
        klee-out-N/       ← test cases, coverage report
```

幾個 key components：

### 1. Executor

`lib/Core/Executor.cpp`，大約 5000 行 C++。主 loop：

```
while (有 active state):
    挑一個 state (by Searcher)
    執行該 state 下一條 instruction
    如果是 branch → fork，挑 feasible 的繼續
    如果是 call → step into 或 SimProcedure
    如果是 memory op → 經過 MemoryManager
    如果 halt / abort → 產生 test case
```

KLEE 的 Executor 本質上是 LLVM 的 `llvm::Interpreter` 加上 symbolic semantic：讀 LLVM instruction、同時更新 symbolic state。

### 2. ExecutionState

每個 symbolic state 的所有資訊：

```cpp
class ExecutionState {
    KFunction *kf;                    // 當前 function
    unsigned pc;                       // instruction counter
    std::vector<StackFrame> stack;     // call stack
    AddressSpace addressSpace;         // symbolic memory
    ConstraintSet constraints;         // PC
    TreeOStream pathOS;                // history（for test replay）
    std::vector<MemoryObject*> symbolics; // 標為 symbolic 的 buffer
    // ...
};
```

fork 時整包 copy — 有 copy-on-write，memory 共享前段 version。

### 3. AddressSpace (memory)

KLEE 的 memory 是 **object-based**：每次 malloc / alloca / global 各是一個 `MemoryObject`（包含 base address + size + bytes）。

load 時 `AddressSpace::resolveOne(addr)` 找出這個 address 屬於哪個 object。symbolic address：

- 如果 narrow 到一個 object → fine
- 如果跨多個 object → fork state 成多條（每條 address 落在一個 object）
- OOB → 報 error（`memory error: out of bound pointer`）

這就是 KLEE 能**自動找 OOB** 的原因：每次 resolve 都檢查 bound。

### 4. ConstraintManager

維護 `ExecutionState.constraints`。加入新 constraint 時會：
- 做 simplification（rewrite rules）
- 檢查 independence（union-find）
- 可能直接發現 unsat、丟掉 state

### 5. Solver backend

歷史上用 **STP**（Stanford 的 BV solver，老但快）。現代 KLEE 可選 Z3（`--solver-backend=z3`）、MetaSMT 多 solver。

Solver 包一層 CexCache（counterexample cache）、IndependentSolver（constraint split）、ConstantFolding（pre-reduce concrete expr）、才到底層 STP/Z3。這是 KLEE paper 的 Table 2 顯示 caching 帶來 **120× solver 時間節省**的來源。

### 6. Searcher

決定下一個執行哪條 state。預設 `--search=random-path`：

- 對 state 組成的 tree 做 random walk
- 在 tree 深淺之間平衡

其他可選：
- `--search=bfs`
- `--search=dfs`  
- `--search=random-state`（從 active state 隨機挑）
- `--search=nurs:covnew`（coverage-guided，挑最可能增加 coverage 的）
- 多種組合：`--search=random-path --search=nurs:covnew`（交替）

這是 KLEE paper 的第 4 章主要貢獻 — **混合 random-path + coverage heuristic** 比單一策略效果好。

### 7. POSIX runtime + uclibc

Ch 10 講過。實作在 `runtime/POSIX/*.c`（fd_t, file_t, fork, syscall wrappers），編譯成 bitcode、link 進 target。

uclibc 是 KLEE 自帶的 port（fork 自 uclibc-ng），同樣編成 bitcode。所有 libc call 都在 LLVM IR 層面跑 symex。

## 一條 path 怎麼走：traced example

target：

```c
// get_sign.c
#include <klee/klee.h>

int get_sign(int x) {
    if (x == 0) return 0;
    if (x < 0) return -1;
    return 1;
}

int main() {
    int a;
    klee_make_symbolic(&a, sizeof(a), "a");
    int s = get_sign(a);
    return s;
}
```

編譯：

```bash
clang -I ~/klee/include -emit-llvm -c -g -O0 get_sign.c -o get_sign.bc
klee get_sign.bc
```

KLEE 跑到 `klee_make_symbolic(&a, ...)`：

1. 為 `a` 分配 `MemoryObject`，大小 4 byte
2. 把那 4 byte 設為 symbolic（`BV32 a`）
3. 繼續 execution

走到 `if (x == 0)`：

- cond = `a == 0`
- fork 兩條：
  - state_A：PC = `a == 0`，pc → `return 0`
  - state_B：PC = `a != 0`，pc → `if (x < 0)`

state_B 走到第二個 if：

- cond = `a < 0`
- fork：
  - state_B1：PC = `a != 0 ∧ a < 0`，pc → `return -1`
  - state_B2：PC = `a != 0 ∧ ¬(a < 0)`（即 `a > 0`），pc → `return 1`

三條 state 各自走到 main 的 return：

- state_A： model `a = 0`
- state_B1：model `a = -1` (或任何負數)
- state_B2：model `a = 1` (或任何正數)

KLEE 輸出三個 test case `klee-out-0/test000001.ktest` ~ `test000003.ktest`，可以用 `ktest-tool` 讀出來 concrete input。

## Bug detection

KLEE 在執行中檢查：

- **memory error**：OOB、UAF、null deref — 每次 memory access 都 check
- **divide by zero**：`div` / `rem` instruction 的 divisor 可能為 0？SMT check
- **overflow**（帶 `--check-overshift`）：shift by > width？
- **assertion failure**：`assert(x)` 等於 if (!x) klee_abort()，symex 能走到就報
- **klee_assume(x)**：跟 assert 相反 — 告訴 KLEE「只探索 x 為 true 的 path」

每次報 bug 都產生 test case + 解釋。這是 KLEE 殺手級的 UX。

## KLEE 的優勢

相比 angr：

1. **精度高**：有 type 資訊，symbolic memory 不會 over-alias
2. **POSIX model 完整**：coreutils 類 target 幾乎不用寫 custom model
3. **Bug detection 自動**：OOB / UAF / div-by-zero 開箱即用
4. **Path coverage metric 明確**：`klee-stats` 告訴你多少 LLVM block 覆蓋
5. **Test case 可 replay**：ktest 格式可以在 concrete 執行重放

相比 angr 的弱點：

1. **要 source**：純 binary target 沒路
2. **LLVM 版本鎖死**：KLEE 3.0 要 LLVM 14，不能混用
3. **Library 問題**：target 用 OpenSSL → 要把 OpenSSL 也編進 bitcode
4. **Threading、network 支援差**
5. **不能 handle 動態 load（dlopen）**

## KLEE-native vs KLEE-LLVM-link

兩種 workflow：

### Native

```bash
clang -emit-llvm target.c
klee target.bc
```

KLEE 自動把 uclibc / POSIX runtime link 進來。適合簡單 target。

### 手動 link

```bash
clang -emit-llvm target.c -o target.bc
llvm-link target.bc helper.bc third_party.bc -o combined.bc
klee combined.bc
```

target 依賴多個 file 或 library 時用這個。大 project 常做。

### 自 build library 的 bitcode

舉例 — 把 libxml2 編成 bitcode 給 KLEE 用：

```bash
CC=wllvm CXX=wllvm++ ./configure --disable-shared
make
extract-bc libxml2.la
```

`wllvm` / `gllvm` 是專門做這種 "build project as bitcode" 的工具。生產環境中 KLEE 大部分時間在跟 build system 搏鬥。

## KLEE 的進階 flags 先介紹

會在後面章節用到：

```bash
--posix-runtime          # 開 POSIX model
--libc=uclibc            # 用 uclibc
--libc=klee              # 用 KLEE 簡化 libc
--libc=freestanding      # 完全不連 libc
--search=random-path     # searcher
--optimize               # 對 bitcode 先做 opt
--max-memory=2000        # MB limit
--max-time=3600          # 秒
--max-instructions=10M
--max-forks=1000
--only-output-states-covering-new  # 只輸出新 coverage 的 test
--watchdog               # 卡住 kill
--use-forked-solver      # solver 跑 child process
```

`--optimize` 特別要注意 — 它對 bitcode 做 LLVM opt pass（常數摺疊、死碼消除、CFG 簡化），幫 KLEE 節省大量 symex 工作。**幾乎永遠要開**。

## 為什麼 KLEE 仍然活著、有人用

它 2008 年 paper、2025 年仍然是 C/C++ symex 的主要工具。沒被取代的原因：

1. 學術社群積累 — 上百篇論文基於 KLEE 做延伸
2. Bug finding 的實戰效果 — coreutils, busybox, minix 都有 KLEE-discovered CVE
3. 社群持續維護（<https://github.com/klee/klee>）
4. 設計簡潔、code readable（你可以在 Ch 7 的 mini 基礎上讀 KLEE 源碼）

它也有明顯的繼承者（SymCC、Symbiosis、Manticore），但沒有一個真的取代它。

## 心法

KLEE 是學 symex 架構的最佳範本。它的 code 比 angr 乾淨、比 Triton 全面、比 S2E 輕。

讀 KLEE 源碼的建議順序：
1. `lib/Core/Executor.cpp` 的 `Executor::run()` 主 loop
2. `lib/Core/ExecutionState.{h,cpp}` 了解 state 結構
3. `lib/Core/AddressSpace.cpp` 理解 memory model
4. `lib/Solver/` 看 solver layer 的 caching、independence
5. `lib/Searcher/` 看 search strategy

讀過這些你看 angr source 會快很多 — 同樣 pattern 的 Python 版。

## 自我檢核

- [ ] 解釋 KLEE 選擇 LLVM IR 的三個理由
- [ ] 畫出 KLEE 的 components 圖（Executor、ExecutionState、ConstraintMgr、Solver、POSIX runtime）
- [ ] 能走一遍 `get_sign.c` 的 three-path 跑法
- [ ] 知道 `klee_make_symbolic`、`klee_assume` 的作用
- [ ] 理解 `--libc=uclibc` vs `--libc=klee` 的取捨

下一章動手實戰 — 用 KLEE 跑幾個有意義的 target，看 POSIX runtime 怎麼運作、怎麼 debug KLEE 跑不動。

→ [Ch 12 — klee_make_symbolic、POSIX runtime、uclibc](./12-klee-in-practice.md)
