# Ch 41 — SymCC / SymQEMU

> **目標**: 理解 compile-time 插樁式 concolic execution 的設計哲學；掌握 SymCC 與 SymQEMU 的工作流；能把 SymCC 整合進 AFL++ 的混合模糊測試流水線；能在 closed-source binary 上跑 SymQEMU；清楚知道它們的局限在哪裡。

> **環境**: WSL2 Ubuntu，LLVM/Clang 12+，Z3，Python 3

---

## 為什麼又要另一個 symbolic execution 工具

KLEE 出現在 2008 年，設計哲學是純 symbolic execution：把 LLVM IR 逐條解讀，在解讀過程中維護 symbolic 狀態。這個設計讓 KLEE 在小規模程式上能做深度路徑探索，但把它拿去跑真實的使用者空間程式就會遇到三道牆：

**路徑爆炸（path explosion）**：程式裡有 N 個條件分支，最壞情況 2^N 條路徑。KLEE 每碰到分支就 fork 一份狀態，狀態數指數增長，記憶體先燒完。

**環境互動（environment interactions）**：系統呼叫、共享函式庫、多執行緒——KLEE 要麼用模型（stub）模擬，要麼放棄。模型不準就漏洞，不模擬就卡住。

**解 SMT 的代價**：Z3 對複雜 constraint 求解耗時是秒級甚至分鐘級。純 symbolic 執行每條路徑都丟給 solver，throughput 慘不忍睹。

angr 在這些問題上做了工程上的改善（veritesting、lazy initialization），但本質上仍是 interpreter 架構，執行速度比 native 慢兩到三個量級。

SymCC 的作者 Poeplau 和 Francillon 在 USENIX Security 2020 提出另一個思路：**不要解讀程式，讓程式自己跑**。symbolic execution 的邏輯不是掛在外面，而是在編譯期直接插進 binary 裡。這個想法讓 concrete execution 全速跑，symbolic 追蹤變成一條「side channel」——只有在需要的時候才付出代價。

實測數字：在同一份 coreutils 測試集上，SymCC 的覆蓋率成長速度比 KLEE 快 12 倍，比 angr 快數十倍。

---

## 先建立直覺

SymCC 的核心動作是在 LLVM IR 層插樁。下面這張圖把它簡化到最骨幹：

```
原始 C 程式
     │
     ▼
  Clang frontend
     │
     ▼
  LLVM IR  ──── SymCC pass ────►  插樁後的 LLVM IR
                                        │
                              每條 IR 指令旁邊
                              插入 runtime call：
                              __sym_add / __sym_icmp_eq / ...
                                        │
                                        ▼
                              native binary
                             ┌──────────┬──────────────┐
                             │  原本邏輯  │  symbolic    │
                             │  全速跑   │  tracking    │
                             │          │  (side chan.) │
                             └──────────┴──────────────┘
                                        │
                             每次執行完把收集到的
                             path constraints 丟給 Z3
                                        │
                                        ▼
                              Z3 解出新 input
                             (嘗試翻轉還沒走到的分支)
                                        │
                                        ▼
                              把新 input 餵回去再跑一次
```

幾個關鍵點：

1. **binary 本身跑的是 native 機器碼**，不是 interpreter。這就是速度來自哪裡。
2. **symbolic tracking 是 side channel**，只追蹤從 symbolic input 衍生出來的值。大部分計算和 symbolic 無關，runtime 幾乎零負擔。
3. **每次只跑一條路徑**（concolic，不是純 symbolic）。輸入是具體的，symbolic 狀態跟著跑。

---

## 核心概念

### Compile-time 插樁 vs interpreter

KLEE 的做法：把你的 LLVM IR 拿進來，用一個大的 C++ interpreter 逐條執行。每個 IR 指令對應 KLEE interpreter 裡的一個 handler。

SymCC 的做法：在 IR 上加一個 LLVM pass，把每條和 symbolic 值有關的 IR 指令旁邊插入一個 runtime 函式呼叫。插完之後正常 codegen，產出的 binary 就帶著 symbolic tracking 能力。

這個差異的影響是：KLEE 的 overhead 是固定的（interpreter 本身），SymCC 的 overhead 只在 symbolic 值參與計算時才出現，其他地方是零。

### Shadow memory

SymCC runtime 維護一塊 shadow memory，每個 concrete memory address 對應一個 shadow slot，存的是這個記憶體位置目前攜帶的 symbolic expression（或 NULL，表示是 concrete 值）。

```
concrete memory:  [ 0x41 | 0x42 | 0xDE | 0xAD ]
shadow memory:    [ NULL  | NULL | sym0 | sym1  ]
                                  ↑
                           這兩個 byte 來自 symbolic input
```

當 runtime 看到一條 load 指令，它去 shadow memory 查有沒有 symbolic expression。有的話，後續的計算就要把 symbolic expression 傳遞下去（expression tree 越接越長）。

### SMT solver Z3

最終累積的 path constraint 是一棵 expression tree，長得像：

```
(x[0:7] | (x[1:7] << 8) | (x[2:7] << 16) | (x[3:7] << 24)) == 0xDEADBEEF
```

把這個丟給 Z3，Z3 找到一組解，就是一個能走到這個分支的新 input。

SymCC 使用的後端不只 Z3，也支援 STP、Metasmt。Z3 是預設。

---

## 底層機制

### SymCC 完整工作流

```
┌─────────────────────────────────────────────────────┐
│                    Build phase                      │
│                                                     │
│  target.c ──► symcc (clang wrapper) ──► target.out  │
│                    │                                │
│           插入 __sym_* 呼叫                          │
│           link symcc_runtime.so                     │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                   Runtime phase                     │
│                                                     │
│  input seed ──► target.out                          │
│                    │                                │
│         concrete 執行全速跑                          │
│         shadow memory 追蹤 symbolic 值               │
│         遇到 branch：收集 constraint                  │
│         執行結束：把 constraints 丟給 Z3              │
│                    │                                │
│              Z3 回傳新 input                         │
│             ┌──────┴──────┐                         │
│         走左分支的 input  走右分支的 input             │
│             └──────┬──────┘                         │
│              寫入 /tmp/output/                       │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                  Iteration loop                     │
│                                                     │
│  把新 input 一一餵回去，重複以上流程                   │
│  (symcc_fuzzing_helper 腳本自動化這個循環)            │
└─────────────────────────────────────────────────────┘
```

### SymQEMU：binary 層做同樣的事

SymCC 需要原始碼才能插樁。對 closed-source binary 怎麼辦？

SymQEMU 的解法：在 QEMU 的 TCG（Tiny Code Generator，QEMU 的 JIT 後端）上加 SymCC 風格的插樁。QEMU 把 guest 指令翻譯成 TCG IR，SymQEMU 在 TCG IR 層插入 symbolic tracking 呼叫，邏輯和 SymCC 幾乎一樣，只是插樁點從 LLVM IR 變成 TCG IR。

```
closed-source binary (x86-64 ELF)
         │
         ▼
   QEMU user-mode
         │
     TCG frontend：  x86 指令 ──► TCG IR
         │
     SymQEMU pass：  TCG IR  ──► 插入 __sym_* 呼叫
         │
     TCG backend：   TCG IR  ──► host 機器碼
         │
         ▼
   native 速度執行 + symbolic tracking
```

代價：QEMU 的 JIT overhead 還在（大約 2–5x slowdown），但比 angr 的 pure interpreter 快很多。SymCC 有原始碼就用 SymCC，沒有才用 SymQEMU。

---

## 實測：SymCC 解 magic value

### 準備 target

```c
// target.c
#include <stdio.h>
#include <string.h>
#include <stdint.h>

int main(int argc, char *argv[]) {
    if (argc < 2) return 1;
    uint32_t x;
    memcpy(&x, argv[1], sizeof(x));
    if (x == 0xDEADBEEF) {
        puts("found it!");
        __builtin_trap();
    }
    return 0;
}
```

這個程式的 magic value 是 `0xDEADBEEF`。一般 fuzzer 要隨機碰到這 4 個 byte 的正確組合，機率是 1/2^32。SymCC 應該能直接解出來。

### Build SymCC

SymCC 依賴 LLVM/Clang 12+（或更新）和 Z3。

```bash
# 安裝依賴（Ubuntu 22.04）
sudo apt-get install -y \
    build-essential cmake ninja-build \
    llvm-14 clang-14 libllvm14 \
    libz3-dev python3

# clone SymCC
git clone https://github.com/eurecom-s3/symcc.git
cd symcc
git submodule update --init --recursive

# build
mkdir build && cd build
cmake -G Ninja \
    -DLLVM_DIR=$(llvm-config-14 --cmakedir) \
    -DZ3_DIR=/usr \
    ..
ninja
```

build 完成後，`build/` 目錄下會有：
- `symcc`：clang wrapper
- `sym++`：clang++ wrapper
- `SymRuntime.so`：runtime library（要一起帶著）

### 編譯 target

```bash
cd /tmp
export SYMCC_OUTPUT_DIR=/tmp/symcc_out
mkdir -p $SYMCC_OUTPUT_DIR

# 用 symcc 代替 clang 編譯
/path/to/symcc/build/symcc -o target target.c
```

### 跑一次看輸出

```bash
# 給一個 seed（4 個 byte 的 concrete input，全零）
printf '\x00\x00\x00\x00' > /tmp/seed.bin

# 執行，把 seed 透過 argv 傳入
/tmp/target "$(cat /tmp/seed.bin)"
```

SymCC runtime 在背景追蹤 symbolic 狀態，執行完之後把 Z3 解出的新 input 寫進 `SYMCC_OUTPUT_DIR`。

---

**本段 build 步驟未在本課環境實測，為理論預期行為。** 在已 build 好 SymCC 的環境，預期輸出如下：

```
$ ls /tmp/symcc_out/
input0000  input0001

$ xxd /tmp/symcc_out/input0000
00000000: efbe adde                                ....

$ /tmp/target "$(cat /tmp/symcc_out/input0000)"
found it!
Illegal instruction (core dumped)
```

- `input0000` 的內容是 `\xef\xbe\xad\xde`，也就是 `0xDEADBEEF` 的 little-endian 表示。
- Z3 解出了讓 `x == 0xDEADBEEF` 成立的唯一解。
- `puts("found it!")` 執行，然後 `__builtin_trap()` 觸發 SIGILL。

### 完整驗證步驟

讀者在 build 完 SymCC 後，按以下流程驗證：

```bash
# 1. 設定輸出目錄
export SYMCC_OUTPUT_DIR=/tmp/symcc_out
mkdir -p $SYMCC_OUTPUT_DIR

# 2. 編譯
SYMCC=/path/to/symcc/build/symcc
$SYMCC -o /tmp/target /path/to/target.c

# 3. 跑一次（seed 全零）
printf '\x00\x00\x00\x00' > /tmp/seed.bin
/tmp/target "$(cat /tmp/seed.bin)"

# 4. 看生成的 input
ls -la $SYMCC_OUTPUT_DIR/
xxd $SYMCC_OUTPUT_DIR/input0000

# 預期看到 ef be ad de

# 5. 用新 input 觸發 bug
/tmp/target "$(cat $SYMCC_OUTPUT_DIR/input0000)"
# 預期看到 "found it!" 後 core dump
```

如果步驟 4 沒有看到 `ef be ad de`，先確認：
- `SYMCC_OUTPUT_DIR` 目錄存在且有寫入權限
- binary 是用 `symcc` 而不是普通 `clang` 編譯的
- `SymRuntime.so` 在 build 目錄下且被正確 link（`ldd /tmp/target` 確認）

---

## 進階用法

### AFL++ 整合：symcc_fuzzing_helper

單獨跑 SymCC 效率不夠高，因為 SymCC 每次只跑一條路徑，constraint 解完才生下一個 input。AFL++ 有 coverage feedback，能把 fuzzing 能觸及的基礎輸入持續提供給 SymCC，讓 SymCC 只需要負責「解開 hard constraint」的部分。

SymCC repo 裡有 `util/symcc_fuzzing_helper`，是一個 Python 腳本，把上面的整合自動化：

```bash
# 同時跑 AFL++ master 和 SymCC helper
# 終端 1：AFL++ 正常跑
afl-fuzz -i seeds/ -o afl_out/ -M afl-master -- ./target_afl @@

# 終端 2：SymCC helper 從 AFL++ 的 queue 讀 input，餵給 SymCC binary 跑，
#         把解出的新 input 再推回 AFL++ 的 queue
python3 /path/to/symcc/util/symcc_fuzzing_helper \
    -a afl_out/afl-master \
    -o symcc_out/ \
    -n symcc-worker \
    -- ./target_symcc @@
```

工作流：
```
AFL++ queue ──► symcc_fuzzing_helper ──► SymCC binary ──► 新 input
     ▲                                                        │
     └────────────────── 推回 AFL++ sync dir ─────────────────┘
```

AFL++ 負責廣度探索，SymCC 負責解開讓 AFL++ 卡關的 magic value。實測上，這個組合對含有 checksum、magic bytes、protocol state machine 的目標效果特別好。

### SymQEMU 針對 closed-source binary

SymQEMU 的 build 和 SymCC 分開：

```bash
git clone https://github.com/eurecom-s3/symqemu.git
cd symqemu
# SymQEMU 是 QEMU 的 fork，build 方式和 QEMU 類似
./configure \
    --target-list=x86_64-linux-user \
    --disable-system \
    --symcc-runtime=/path/to/symcc/build/SymRuntime.so
make -j$(nproc)
```

跑法：

```bash
export SYMCC_OUTPUT_DIR=/tmp/symqemu_out
mkdir -p $SYMCC_OUTPUT_DIR

# 直接執行 closed-source binary，不需要原始碼
./x86_64-linux-user/qemu-x86_64 \
    -symcc \
    /path/to/closed_source_binary \
    "$(cat seed.bin)"
```

SymQEMU 的輸出格式和 SymCC 一樣，生成的新 input 同樣在 `SYMCC_OUTPUT_DIR` 下，也可以接 `symcc_fuzzing_helper`。

---

## 對比取捨

| 工具 | 執行方式 | 需要原始碼 | 相對速度 | 路徑覆蓋策略 | 適合場景 |
|------|----------|-----------|---------|-------------|---------|
| SymCC | compile-time 插樁 | 是 | 最快（native） | concolic，一次一路徑 | open-source + magic value |
| SymQEMU | TCG JIT 插樁 | 否 | 中（QEMU overhead） | concolic，一次一路徑 | closed-source binary |
| KLEE | LLVM IR interpreter | 是（LLVM IR） | 最慢 | 多路徑並行探索 | 研究/小規模深度探索 |
| angr | Python IR interpreter | 否 | 慢 | 多策略（BFS/DFS/veritesting） | 逆向工程/CTF |

補充說明：

- SymCC/SymQEMU 是 **concolic**，不是純 symbolic。它們的強項是「解開單一 constraint」，弱項是「系統性探索所有路徑」。KLEE 反過來。
- angr 的優點是 Python API 靈活，能做很多客製化分析；缺點是速度和穩定性。
- 實際上 AFL++ + SymCC 的混合才是主流用法，純 SymCC 單獨使用場景有限。

---

## 踩雷

### 以為 SymCC 是「更快的 KLEE」

這是最常見的誤解。SymCC 不做多路徑探索，它每次執行只跑一條具體路徑（concolic），symbolic 只是追蹤 constraint 的 side channel。

KLEE 的用途是：給我這個函式的所有輸入，找出所有可能的行為。SymCC 的用途是：我現在卡在這個 input，幫我找一個能走到另一個分支的 input。

這兩個問題根本不一樣。把 SymCC 拿去做系統性路徑探索是錯誤用法，結果會讓你覺得「怎麼覆蓋率這麼差」——因為那不是它設計來做的事。

### 忘記 SymRuntime.so 要一起 build

SymCC 的插樁 binary 在執行時要動態 link `SymRuntime.so`。如果你只 build 了 `symcc` wrapper 但沒有 build runtime，或者 `SymRuntime.so` 不在 `LD_LIBRARY_PATH` 裡，binary 會直接 crash 或靜默跑完但不產生任何 output。

排查方式：

```bash
ldd /tmp/target | grep SymRuntime
# 應該看到類似：
# libSymRuntime.so => /path/to/symcc/build/libSymRuntime.so

# 如果找不到：
export LD_LIBRARY_PATH=/path/to/symcc/build:$LD_LIBRARY_PATH
```

另一個症狀是 `SYMCC_OUTPUT_DIR` 是空的——binary 跑完了但一個 input 都沒生。這幾乎一定是 runtime 沒 link 到。

### SymQEMU 在 JIT 目標上失去 symbolic tracking

SymQEMU 在 TCG IR 層插樁，這個假設是 guest 指令是靜態的。如果目標 binary 本身會在執行時修改自己的程式碼（self-modifying code），或者目標是一個有內建 JIT 的 runtime（例如 JavaScript engine、Java JVM、LuaJIT），QEMU 的 TCG 對這些動態生成的指令的處理是有限的，symbolic tracking 會斷掉。

具體表現：SymQEMU 能追蹤到進入 JIT engine 之前的 constraint，但 JIT 編譯出來的 native code 不會被插樁，裡面的 constraint 就消失了。

這不是 bug，是架構限制。遇到這類目標，選擇有限：要麼用 angr（它對 SMC 有部分處理），要麼在更高層拿 trace（動態插樁如 Intel PIN）。

---

## 進階延伸

**整合 libAFL**：libAFL 是 Rust 寫的模組化 fuzzing 框架，有 SymCC 整合的範例（`symcc_libafl`）。比 `symcc_fuzzing_helper` 的整合更緊密，可以在 fuzzing 循環裡直接控制 SymCC 的執行頻率和 input 選擇策略。

**Hybrid concolic testing 的排程問題**：AFL++ + SymCC 裡，什麼時候該讓 SymCC 介入是個策略問題。太頻繁讓 SymCC 跑會拖慢整體 throughput，太少讓它跑則卡關問題無法解開。有論文（Driller、QSYM）研究過這個排程問題，下一章會深入。

**SymCC 的 backend 替換**：SymCC 的 constraint solver 後端是可以換的。Z3 以外，STP 在 bitvector 問題上有時更快，metaSMT 提供統一介面讓你切換底層 solver。如果 Z3 成為瓶頸，值得測試。

**針對 binary protocol 的 SymCC 用法**：如果 input 是從 socket 讀進來的而不是 argv，要讓 SymCC 知道哪個 input 是 symbolic。這需要在 source 裡手動標記，或用 `symcc_set_input` API。SymCC 的 `SYMCC_STDIN_FILENAME` 環境變數可以讓它把 stdin 標記為 symbolic，是最常見的替代 argv 的方法。

```bash
export SYMCC_STDIN_FILENAME=/tmp/seed.bin
export SYMCC_OUTPUT_DIR=/tmp/out
/tmp/target < /tmp/seed.bin
```

---

## 動手練習

1. **驗證 magic value 場景**：寫一個比本章範例複雜一點的 target——把 magic value 改成一個 8-byte 字串比較（`strcmp(input, "SYMCC_OK")`），用 SymCC 解出正確 input。確認 SymCC 能處理 byte 逐一比較的 constraint。

2. **與 AFL++ 整合**：在第一個練習的基礎上，用 afl-fuzz 跑同一個 target（正常編譯版），看看 AFL++ 多久能隨機碰到 `"SYMCC_OK"`；再用 AFL++ + SymCC helper 的組合跑，比較時間差。

3. **SymCC output 分析**：寫一個含多個獨立 magic value 的 target（比如先檢查 `x == 0xAABBCCDD`，再檢查 `y == 0x12345678`），觀察 SymCC 多次迭代後生成的 input 序列，確認它的探索順序。

4. **失敗案例複現**：寫一個用 CRC32 checksum 保護的格式解析器（input 的最後 4 byte 是前面所有 byte 的 CRC32），用純 AFL++ 跑和用 AFL++ + SymCC 跑，觀察覆蓋率曲線差異。

---

## 本章重點

- SymCC 在 compile-time 把 symbolic tracking 插進 LLVM IR，執行期是 native 速度，symbolic 是 side channel
- concolic execution：concrete input 決定走哪條路徑，symbolic tracking 在旁邊記錄 constraint，路徑跑完丟給 Z3 解新 input
- shadow memory：每個 concrete address 有一個 shadow slot 存 symbolic expression，NULL 表示純 concrete 值
- SymCC ≠ KLEE：SymCC 一次一路徑（concolic），KLEE 多路徑並行（symbolic）；用途完全不同
- SymQEMU 在 QEMU TCG IR 層做同樣的插樁，處理 closed-source binary，代價是 QEMU JIT overhead
- 主流用法是 AFL++ + SymCC 混合：AFL++ 廣度探索 + SymCC 解 hard constraint
- `SYMCC_OUTPUT_DIR` 空 → 先查 `SymRuntime.so` 有沒有被 link 到

---

## 自我檢核

- [ ] 能解釋 SymCC 和 KLEE 的根本設計差異（interpreter vs compile-time 插樁）
- [ ] 能解釋 shadow memory 在 SymCC runtime 裡的角色
- [ ] 能描述 concolic execution 的一次完整循環（input → 執行 → constraint → Z3 → 新 input）
- [ ] 知道 SymQEMU 在 JIT 目標上為什麼會失去 symbolic tracking
- [ ] 能設置 AFL++ + SymCC 混合 fuzzing 的基本命令列
- [ ] 能排查「`SYMCC_OUTPUT_DIR` 是空的」這個常見問題

---

## 延伸閱讀

1. **SymCC: Efficient Compiler-Based Symbolic Execution**
   Poeplau & Francillon，USENIX Security 2020
   原始論文，詳述 compile-time 插樁架構、shadow memory 設計、與 KLEE/angr 的效能對比。
   https://www.usenix.org/conference/usenixsecurity20/presentation/poeplau

2. **SymQEMU: Compilation-based symbolic execution for binaries**
   Poeplau & Francillon，NDSS 2021
   把 SymCC 的設計移植到 QEMU TCG 層，處理 closed-source binary；詳述 TCG IR 插樁的挑戰和與 SymCC 的效能差距。
   https://www.ndss-symposium.org/ndss-paper/symqemu-compilation-based-symbolic-execution-for-binaries/

3. **Z3: An Efficient SMT Solver**
   de Moura & Bjørner，TACAS 2008
   Z3 的原始論文，理解 SymCC 背後 constraint solver 的能力和限制（特別是 bitvector theory 的處理）對調整 SymCC 效能有幫助。
   https://link.springer.com/chapter/10.1007/978-3-540-78800-3_24

4. **SymCC GitHub repo 的 README 和 util/ 目錄**
   https://github.com/eurecom-s3/symcc
   `util/symcc_fuzzing_helper` 的實作和用法說明是 AFL++ 整合的第一手資料。

---

## 銜接

本章建立了 compile-time 插樁式 concolic execution 的基礎。SymCC/SymQEMU 解決了「solver 觸及不到的 hard constraint」問題，但它們本身不解決「要從哪個 seed 出發」、「多久觸發一次 SymCC」這類排程問題。

下一章的 Driller 和 QSYM 正是為了解決這個排程問題而設計的——它們在 AFL 和 symbolic execution 之間建立更緊密的回饋循環，是 SymCC-style 混合 fuzzing 的進化版本。

→ [下一章](./42-driller-qsym.md)
