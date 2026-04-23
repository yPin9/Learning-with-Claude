# Ch 0 — 環境搭建：build LLVM + llc + opt + llvm-mc

> 目標：從 source build 一個可以 hack 的 LLVM、裝齊 `llc` / `opt` / `llvm-mc` 等核心工具，並用一個簡單例子驗證整個 C → IR → asm → object 流程。本課每一章都會依賴這套環境。

## 為什麼要 build from source

你可以用 distro 的 `clang` / `llvm` package 學前三章（IR 讀寫）。但：

- **改 backend 要 build**：加 custom instruction、改 TableGen 都必須重 build
- **`-debug-only=xxx` flag 需要 debug build**：package 版是 release + no-asserts
- **讀 source 的對應**：你改 `RISCVInstrInfo.td`、重 build、`llc` 看差別 —— 這是學 backend 的唯一路徑

這一章讓你把 build 環境調到「修 `.td` 一行可以在 5-10 分鐘看到效果」。

## 硬體需求

LLVM build 不小：

- **Disk**：source 2 GB、full build (+ debug) 20-50 GB
- **RAM**：debug build 建議 16 GB+、release 8 GB 夠
- **CPU**：越多 core 越快。16 core 大概 30 分鐘 release build

**建議策略**：

- 第一次 build **release + no-assertions**：快，驗證流程通
- 改 backend 時切 **Debug + assertions**：慢但 debug 必備
- 平時用 ccache 加速 incremental build

## 下載 source

```bash
git clone https://github.com/llvm/llvm-project
cd llvm-project
git log -1 --oneline       # 記下當前 commit，方便回來
```

**tip**：fork 到自己的 GitHub，future 送 PR 才方便。

## 第一次 Build（release，驗證環境）

```bash
mkdir build && cd build

cmake -G Ninja \
    -DLLVM_ENABLE_PROJECTS="clang;lld" \
    -DLLVM_TARGETS_TO_BUILD="RISCV;X86" \
    -DCMAKE_BUILD_TYPE=Release \
    -DLLVM_ENABLE_ASSERTIONS=ON \
    -DLLVM_USE_LINKER=lld \
    -DLLVM_CCACHE_BUILD=ON \
    ../llvm

ninja -j$(nproc)
```

**關鍵 flags**：

- `-G Ninja`：比 Make 快很多
- `LLVM_ENABLE_PROJECTS`：只 build 需要的 sub-project
- `LLVM_TARGETS_TO_BUILD`：只 build 關心的 target（省時間）
- `LLVM_ENABLE_ASSERTIONS=ON`：**強烈建議**。release + assertions 是好平衡
- `LLVM_USE_LINKER=lld`：用 LLD 連 LLVM 自己（快 3 倍）
- `LLVM_CCACHE_BUILD=ON`：ccache 加速 incremental build

Build 完成後，`build/bin/` 下有：

```
llc              ← IR → assembly (最常用)
opt              ← IR optimization passes runner
clang            ← C/C++ frontend
llvm-mc          ← assembler / disassembler
lld / ld.lld     ← linker
llvm-objdump     ← 跟 objdump 類似但是 LLVM 版
llvm-readelf     ← 同 readelf
llvm-tblgen      ← TableGen 處理器
```

**把 `build/bin` 加到 PATH**：

```bash
export PATH=$HOME/llvm-project/build/bin:$PATH
```

## 驗證：C → IR → asm → object

```c
// hello.c
int add(int a, int b) {
    return a + b * 3;
}
```

### Step 1: C → LLVM IR

```bash
clang -target riscv64-linux-gnu -emit-llvm -S -O2 hello.c -o hello.ll
cat hello.ll
```

輸出（簡化）：

```llvm
define dso_local i32 @add(i32 noundef %a, i32 noundef %b) local_unnamed_addr {
entry:
    %mul = mul nsw i32 %b, 3
    %add = add nsw i32 %mul, %a
    ret i32 %add
}
```

這是 **LLVM IR**（text 形式）。Ch 1 會深入。

### Step 2: IR → assembly

```bash
llc -march=riscv64 -O2 hello.ll -o hello.s
cat hello.s
```

輸出：

```asm
add:
    slliw   a1, a1, 1
    addw    a1, a1, a1          # 這兩步做 b*3: b*2 + b
    addw    a0, a1, a0
    ret
```

注意：compiler 把 `b*3` 優化成 `(b<<1) + b`（shift + add）。

### Step 3: asm → object

```bash
clang -target riscv64-linux-gnu -c hello.c -o hello.o
llvm-objdump -d hello.o
```

看 `objdump` 的 disassembly 跟上一步的 `.s` 一致。

## 切 debug build（改 backend 時用）

```bash
mkdir build-debug && cd build-debug

cmake -G Ninja \
    -DLLVM_ENABLE_PROJECTS="clang;lld" \
    -DLLVM_TARGETS_TO_BUILD="RISCV" \
    -DCMAKE_BUILD_TYPE=Debug \
    -DLLVM_ENABLE_ASSERTIONS=ON \
    -DLLVM_USE_LINKER=lld \
    ../llvm

ninja -j$(nproc)
```

Debug build 慢很多（幾小時）、binary 大（500 MB+），但：

- 能用 gdb step 進 LLVM 內部
- `-debug-only=xxx` 能輸出內部 debug info（release 沒）

**節省空間**：只 build `llc` + `llvm-tblgen`，不 build 所有：

```bash
ninja llc llvm-tblgen
```

這些是你改 backend 最常用的。

## `-debug-only=` 的魔法

debug build 下：

```bash
llc -debug-only=isel hello.ll 2>&1 | head -50
```

輸出 instruction selection 的過程：

```
ISEL: Starting selection on root node: t16: i32 = add nsw t14, t4
ISEL: Starting pattern match
  Morphed node: t16: i32 = ADDW t14, t4
ISEL: Match complete!
```

類似 debug keyword：`-debug-only=sched`, `-debug-only=regalloc`, `-debug-only=asm-printer`。

查所有可用的：

```bash
llc --debug-pass=Arguments /dev/null
```

## `-print-after-all` / `-print-before-all`

看每個 pass 執行前後的 IR / MIR：

```bash
llc -print-after-all hello.ll 2>&1 | less
```

**超大輸出**。建議配 `-filter-print-funcs=foo` 只看某 function：

```bash
llc -print-after-all -filter-print-funcs=add hello.ll 2>&1 | less
```

## 用 `llvm-mc` 測 MC layer

```bash
# Encode one instruction
echo "addi a0, a1, 42" | llvm-mc -triple=riscv64 -show-encoding

# Output:
#    addi a0, a1, 42               # encoding: [0x13,0x85,0xa5,0x02]
```

這是「直接讓 assembler 告訴你 encoding」的方法。Ch 17 會深入 MC layer。

## `opt` 跑 pass

```bash
# Run specific passes on IR
opt -passes="mem2reg,sroa,instcombine" hello.ll -S -o optimized.ll
```

Ch 2 / Ch 3 會解釋各 pass。

## `llvm-tblgen`：查看 `.td` 展開

TableGen 是 LLVM 宣告式語言（Ch 7 深入）。展開 `RISCV.td`：

```bash
llvm-tblgen -I llvm/include -I llvm/lib/Target/RISCV \
    llvm/lib/Target/RISCV/RISCV.td -gen-instr-info
```

輸出幾千行 C++ code。正常你不會直接看，但 debug `.td` 修改時很有用。

## `cmake` 變數 cheatsheet

你會常改的：

```
DLLVM_ENABLE_PROJECTS=<list>       # clang, lld, mlir, flang...
DLLVM_TARGETS_TO_BUILD=<list>      # RISCV, X86, AArch64, all...
DCMAKE_BUILD_TYPE                  # Debug, Release, RelWithDebInfo
DLLVM_ENABLE_ASSERTIONS            # ON for dev, OFF for release
DBUILD_SHARED_LIBS=ON              # 把每個 lib 變 .so（incremental 快）
DLLVM_PARALLEL_LINK_JOBS=1         # 大 memory 不夠時限制 parallel link
```

**`BUILD_SHARED_LIBS=ON` 是 incremental dev 的加速器**。改一個 .cpp 只重 link 一個 .so、不是整個 binary。

## Ccache 設定

如果沒預裝：

```bash
sudo apt install ccache
ccache --max-size=40G
```

`LLVM_CCACHE_BUILD=ON` 自動抓 ccache。第二次 build 快 80%。

## 常見坑

1. **Build 吃爆 memory**：Debug build + 多 link job → OOM。加 `-DLLVM_PARALLEL_LINK_JOBS=2`（4 GB RAM 用 1）。
2. **Disk 不夠**：debug build 20+ GB。如果空間緊，只 build `llc`、删掉 `build/lib` 的中間檔（後面要用時再重 build）。
3. **找不到 `llc`**：PATH 沒設、或 ninja 沒真的 build（看 `build/bin/llc` 存在否）。
4. **Build 時 lld 錯誤**：沒 enable `lld` project、或系統舊 linker 不夠。`LLVM_USE_LINKER=gold` 或 `=ld` 退路。
5. **`clang` cross-compile fail**：缺 target sysroot。簡單解：只用 `clang -target riscv64-linux-gnu -emit-llvm`（不 link），link 交給 `riscv64-linux-gnu-gcc`。

## 一個完整的 workflow 範例

```bash
cd llvm-project
# 改 llvm/lib/Target/RISCV/RISCVInstrInfo.td...

cd build
ninja llc -j$(nproc)          # 只 build llc

# Test
./bin/llc -march=riscv64 hello.ll -o hello.s
diff hello.s expected.s

# If wrong, find where
./bin/llc -march=riscv64 -debug-only=isel hello.ll 2>&1 | less
```

5 分鐘一個 cycle（熟悉後）。

## 進階：build 時 cross-compile RISC-V

如果你需要 build 實際 RISC-V binary 跑 test：

```bash
# 加 runtime libraries
cmake -G Ninja \
    -DLLVM_ENABLE_PROJECTS="clang;lld;compiler-rt" \
    -DLLVM_ENABLE_RUNTIMES="compiler-rt" \
    -DLLVM_TARGETS_TO_BUILD="RISCV;X86" \
    -DCMAKE_BUILD_TYPE=Release \
    ../llvm

ninja
```

Clang 需要 `compiler-rt`（低階 runtime）才能 target RISC-V。

## 動手練習

1. Build release LLVM with RISC-V + X86 target，驗證 `llc --version` 列出兩個。
2. 寫個 3 行 C code，走完 `clang -emit-llvm -S` → `llc` → `llvm-mc` 全流程，用 `llvm-objdump -d` 驗證。
3. 比較 `-O0` vs `-O2` 編譯的 IR + asm size / 指令數。
4. 故意改 `RISCVInstrInfo.td` 一行（e.g., 改個 ADD 的 pattern description），`ninja llc`、看有沒有影響輸出。
5. 用 `-debug-only=isel` 跑你的 hello，讀 output 10 行，試著理解它在說什麼（不用全懂）。

## 常見誤會

1. **「Distro 的 clang 能學 backend」**：學前三章可以，之後必須 from source。
2. **「Build 一次要幾小時」**：第一次對，之後 incremental ninja 幾秒到幾分鐘。
3. **「Debug build 沒用」**：反。`-debug-only=` flag + gdb step LLVM 需要它。
4. **「TableGen 要另外裝」**：不用。LLVM build 會自動 build `llvm-tblgen`。
5. **「我要 build 所有 target」**：只 build `RISCV` + `X86`（或你 host 的）。省 70% 時間。

## 自我檢核

- [ ] 我能從 source build LLVM release 版本
- [ ] 我能跑完 `clang -emit-llvm` → `llc` → `llvm-objdump` 流程
- [ ] 我能用 `-debug-only=` 看 backend 的內部輸出
- [ ] 我能切 debug build 與 release build
- [ ] 我能 incremental rebuild 只 build 一個 tool

下一章進入 LLVM IR 心法 —— 這是 backend pipeline 的起點，frontend 生出來的語言。

→ [Ch 1 LLVM IR 心法](./01-llvm-ir-mindset.md)
