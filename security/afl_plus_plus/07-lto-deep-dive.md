# Ch 7 — LTO Deep Dive：Collision-Free 插樁的完整機制

> **目標**：理解 LTO instrumentation 如何在 link time 做到 collision-free edge ID 分配，以及它比 PCGUARD 好在哪裡。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64 Linux

---

## 為什麼需要這個？

Ch 5 提到 CollAFL（S&P 2018）量化了 AFL bitmap 的 collision 率達到 3-10%。Ch 6 介紹了四種插樁模式，說 LTO 能做到 collision-free。這一章要深挖：LTO 的 collision-free 究竟是怎麼做到的？代價是什麼？什麼時候值得用？

如果你只是想快速跑 fuzzing，用 PCGUARD 就夠了。但如果你在跑大型 target 的長期 fuzzing campaign（24 小時以上），或者對 coverage 精度有要求（比如你在做 coverage-guided 的安全評估），那 LTO 的 collision-free 特性會讓你的結果更可信。

---

## 先建立直覺

```
Compile time (PCGUARD/CLASSIC):    Link time (LTO):

TU1: foo.c                          All TUs merged into
  BB_A → random ID = 0x1234         one big LLVM IR module
  BB_B → random ID = 0x5678              │
                                         ▼
TU2: bar.c                         Global CFG visible
  BB_C → random ID = 0x1234  ←── COLLISION! Same as BB_A
  BB_D → random ID = 0x9ABC         │
                                    ▼
TU3: baz.c                     AFL++ LTO pass:
  BB_E → random ID = 0x5678  ←── COLLISION! Same as BB_B
  BB_F → random ID = 0xDEF0    assigns IDs: 1, 2, 3, 4, 5, 6...
                                    No collision possible.
       ↓ link ↓
  Potential ID collisions!
```

Compile-time 模式的根本問題：每個 translation unit（TU）是獨立編譯的，各自隨機選 ID，link time 已經來不及去重了。

LTO 的解法：把整個插樁 pass 推遲到 link time。此時所有 TU 的 LLVM IR 已經合併成一個巨大的 module，pass 能看到程式的完整 CFG，可以按照遍歷順序分配從 1 開始的遞增 ID。唯一性由結構保證，不靠隨機。

---

> 如果你對 bitmap collision 的基礎還不熟，先回看 [Ch 5](./05-edge-coverage-bitmap.md)。
> 如果你對四種模式的選型還不熟，先回看 [Ch 6](./06-compile-time-instrumentation.md)。

---

## 回顧：Collision 的根本原因

### 為什麼 random ID 在 large target 上有 3-10% collision

CollAFL（S&P 2018）的分析基於生日悖論（birthday paradox）：

```
假設 bitmap 大小 M = 65536
target 有 N 條 edge，ID 從 [0, M-1] 均勻隨機選取

P(at least one collision) ≈ 1 - e^(-N(N-1)/(2M))

N = 1000 edges:  P ≈ 1 - e^(-7.63)  ≈ 99.95%
N = 500  edges:  P ≈ 1 - e^(-1.91)  ≈ 85.2%
N = 100  edges:  P ≈ 1 - e^(-0.076) ≈ 7.3%
```

對現實中的 target：
- 小型工具（如 `jq`, `base64`）：約 500-2000 edges
- 中型 library（如 `libpng`, `libsqlite3`）：5000-20000 edges
- 大型程式（如 Firefox, Chrome 的子模組）：50000+ edges

**CollAFL 的實測數字**：在 LAVA-M benchmark 上，AFL 的 collision 率達到 3-10%，複雜 target 更高。Collision 的效果不是 crash，而是 fuzzer 對 coverage 的感知被污染：

```
Edge E1（ID=42）被觸發 → bitmap[42]++
Edge E2（ID=42，與 E1 collision）被觸發 → bitmap[42]++

fuzzer 看到 bitmap[42] 的值增加，
但無法分辨是 E1 還是 E2 貢獻的，
也無法判斷是否有「新的」edge 被觸發。
```

結果：某些真正新的路徑被 fuzzer 當成「已見過」丟棄，coverage 被低估，bug 可能被錯過。

---

## Compile-time 插樁的根本侷限

### 為什麼 PCGUARD/CLASSIC 無法避免 collision

編譯流程的時序問題：

```
Step 1: 編譯 foo.c → foo.o
  AFL++ pass 為 foo.c 的每個 BB 分配隨機 ID
  此時 bar.c 還沒編譯，不知道 bar.c 會選什麼 ID

Step 2: 編譯 bar.c → bar.o
  AFL++ pass 為 bar.c 的每個 BB 分配隨機 ID
  此時 foo.c 已編譯完，但它的 ID 選擇已經固定了

Step 3: ld links foo.o + bar.o → binary
  此時才能看到所有 BB，但 ID 已經固定，無法修改
```

PCGUARD 的 `__sanitizer_cov_trace_pc_guard_init()` 在 runtime 把 guard 對應到 AFL 的 bitmap index，確實可以做到「在同一個 TU 內」的 ID 唯一，但跨 TU 仍然是隨機碰撞。

從 ID 分配的角度看：

```c
/* PCGUARD init（runtime）*/
void __sanitizer_cov_trace_pc_guard_init(uint32_t *start, uint32_t *stop) {
    static uint32_t n = 0;
    for (uint32_t *x = start; x < stop; x++) {
        *x = ++n;  /* 只保證在「這個 TU 的 init 呼叫」內唯一 */
                   /* 不同 TU 的 init 是獨立呼叫的，n 從 0 重新開始 */
    }
}
/* 結果：每個 TU 的 BB 編號都從 1 開始，不同 TU 之間必然衝突 */
```

真正的 collision-free 需要一個「全局可見的 ID 分配器」，而在 compile time，這個全局視野根本不存在。

---

## LTO 的解法：把 Pass 推到 Link Time

### 技術流程全覽

```
afl-clang-lto 的編譯流程：

Step 1: 編譯每個 .c → .o（LLVM bitcode 格式）
  clang -flto -c foo.c -o foo.o    ← foo.o 是 bitcode，不是 native ELF
  clang -flto -c bar.c -o bar.o    ← 同上
  clang -flto -c baz.c -o baz.o

  此時不做任何 AFL 插樁！這些 .o 是純 LLVM IR。

Step 2: Link（由 afl-clang-lto 觸發 llvm-lto2 / gold plugin）
  llvm-lto2 foo.o bar.o baz.o → merged_ir.bc

  AFL++ LTO pass 在這裡執行：
    a. 讀取 merged_ir.bc（整個程式的 IR）
    b. 遍歷全局 CFG，識別所有 BasicBlock
    c. 按遍歷順序分配遞增 ID（從 1 開始）
    d. 插入 instrumentation IR
    e. code generation → native binary

  此時每個 BB 的 ID 保證全局唯一。
```

### 關鍵：全局 CFG 的遍歷

LTO pass 的 ID 分配邏輯（`instrumentation/afl-llvm-lto-instrumentation.so.cc`）：

```cpp
/* afl-llvm-lto-instrumentation.so.cc（簡化，概念版）*/

PreservedAnalyses runOnModule(Module &M, ...) {
    uint32_t edge_id = 1;  /* 全局計數器，從 1 開始 */

    /* 遍歷程式中的所有函式 */
    for (auto &F : M) {
        if (F.isDeclaration()) continue;  /* 跳過外部函式宣告 */

        /* 遍歷函式的所有 BasicBlock */
        for (auto &BB : F) {
            /* 分配唯一 ID，遞增，永不重複 */
            uint32_t cur_loc = edge_id++;

            /* 在 BB 開頭插入 instrumentation */
            insertInstrumentation(BB, cur_loc);
        }
    }

    /* edge_id - 1 就是這個程式的總 edge 數，精確且無碰撞 */
    storeEdgeCount(M, edge_id - 1);
    return PreservedAnalyses::none();
}
```

**關鍵洞察**：`edge_id` 是在 `runOnModule` 的單次執行中遞增的局部計數器，它能看到所有 TU 合併後的 IR，所以自然保證全局唯一。

這和 PCGUARD 的 `static uint32_t n = 0` 有本質差異：PCGUARD 的計數器存在 runtime，每次程式啟動時從 0 開始，跨 TU 重複；LTO 的計數器存在 link time，在生成 binary 時已經固定，永不重複。

---

## 底層機制：LTO Pass 的完整工作流程

```
afl-clang-lto 執行時的完整流程：

┌──────────────────────────────────────────────────────────────┐
│  Compile Phase（每個 .c 獨立）                                │
│                                                              │
│  foo.c ──[clang -flto]──→ foo.o (LLVM bitcode)              │
│  bar.c ──[clang -flto]──→ bar.o (LLVM bitcode)              │
│  baz.c ──[clang -flto]──→ baz.o (LLVM bitcode)              │
│                                                              │
│  此時 .o 是 bitcode，不是 ELF，沒有 AFL instrumentation      │
└─────────────────────────┬────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  Link Phase（全局視野）                                       │
│                                                              │
│  1. LTO linker 合併所有 bitcode → merged Module              │
│                                                              │
│  2. AFL++ LTO pass (afl-llvm-lto-instrumentation.so.cc)      │
│     遍歷 merged Module 的所有 Function → BasicBlock           │
│     分配遞增 ID（1, 2, 3, 4, ...）                           │
│     插入 instrumentation IR                                  │
│                                                              │
│  3. LLVM 最佳化 passes（O1/O2/O3）                           │
│                                                              │
│  4. x86 code generation                                      │
│                                                              │
│  5. 輸出 native binary                                       │
│     （每個 BB 的 ID 是確定的、唯一的遞增整數）                 │
└──────────────────────────────────────────────────────────────┘

結果：
  foo.c 的 BB1 → ID = 1
  foo.c 的 BB2 → ID = 2
  bar.c 的 BB1 → ID = 3   ← 不是從 1 重新開始！
  bar.c 的 BB2 → ID = 4
  baz.c 的 BB1 → ID = 5
  ...
  總共 N 個 BB → ID 1 到 N，互不重複
```

### AFL++ LTO Pass 的程式碼定位

實作在 `instrumentation/afl-llvm-lto-instrumentation.so.cc`，關鍵函式：

- `runOnModule(Module &M)`：入口點，遍歷整個 merged IR
- `insertInstrumentation(BasicBlock &BB, uint32_t cur_loc)`：在 BB 開頭插入 bitmap write
- `mayHaveNeverZero()`：NEVERZERO 邏輯
- `getInstrumentationTarget()`：判斷哪些 BB 要插樁（例如跳過空 BB、例外處理 BB）

`runOnModule` 的核心是雙層迴圈（Module → Function → BasicBlock），在整個合併 IR 上做一次線性掃描，分配單調遞增 ID。

---

## LTO 的代價

### Build Time：顯著增加

LTO 的 link step 需要：
1. 把所有 bitcode 合併成一個巨大的 IR module
2. 在這個巨大 module 上跑 AFL++ pass（O(N) 遍歷，N = 總 BB 數）
3. 在這個巨大 module 上跑 LLVM 最佳化 passes
4. Code generation

對比 PCGUARD：link step 只需要把 native object 合併，最最佳化已在 compile step 完成。

實際數字（以 `sqlite3`，約 200 個 .c 為例）：

```
PCGUARD build time:  約 45 秒（compile 分散，link 快）
LTO     build time:  約 8-12 分鐘（link 要重做最佳化）
```

在 CI 環境下，LTO build 可能讓每次迭代慢很多。推薦策略：先用 PCGUARD 做快速迭代，確認 build 沒問題後，再做一次 LTO build 跑長期 campaign。

### Toolchain 要求：嚴格

```bash
# LTO 的必要環境設定
export CC=afl-clang-lto
export CXX=afl-clang-lto++
export AR=llvm-ar          ← 必須！系統 ar 不懂 bitcode
export RANLIB=llvm-ranlib  ← 必須！
export LD=afl-clang-lto    ← 某些 build system 需要明確指定

# 版本一致性要求（afl-clang-lto 依賴的 llvm 版本）
llvm-ar --version  # 必須和 afl-clang-lto 使用同一個 LLVM 版本
```

### 不是所有 target 都能 LTO build

LTO 有幾個已知的限制：

**1. Assembly 原始碼**：`.s` 或 `.S` 無法被 `clang -flto` 編譯成 bitcode，只能輸出 native object。如果 target 有 assembly，這些 `.o` 無法參與 LTO 的 IR 合併步驟（但仍可連結）。

**2. 外部靜態庫（precompiled）**：如果你連結了系統提供的 `.a`（例如 `-lcrypto`），這些 `.a` 是 native ELF，不是 bitcode，無法參與 LTO。結果：這些 library 裡的 code 不會被插樁，只有你自己 build 的 code 有 coverage。

**3. 某些 build system 的 hardcoded `ar`**：很多 CMake 和 autoconf 生成的 build system 在某些步驟寫死使用 `/usr/bin/ar`，`AR=llvm-ar` 的設定可能被覆蓋。需要逐一確認。

**4. LTO bitcode 格式相容性**：LLVM 15 生成的 bitcode 不能被 `llvm-ar-14` 讀取。確保 `clang`、`llvm-ar`、`llvm-ranlib` 全部是同一個主版本。

---

## 實際 Edge 數量對比

### 同一 target，PCGUARD vs LTO 的差異

用 `afl-showmap` 量化兩種模式在同一輸入下的 edge 分布：

```bash
# 以 libpng 的 pngtoppm 為例（概念示範）

# PCGUARD build
CC=afl-clang-fast AFL_LLVM_INSTRUMENT=PCGUARD \
  ./configure && make -j4
afl-showmap -o /tmp/pcguard.map -- ./pngtoppm test.png /dev/null

# LTO build（需要 clean rebuild）
make clean
CC=afl-clang-lto CXX=afl-clang-lto++ AR=llvm-ar RANLIB=llvm-ranlib \
  ./configure && make -j4
afl-showmap -o /tmp/lto.map -- ./pngtoppm test.png /dev/null

# 比較 tuple 數量
wc -l /tmp/pcguard.map /tmp/lto.map
```

典型觀察（以 libpng 1.6 + 標準 PNG 測試圖為例）：

```
PCGUARD: 1842 tuples（ID 分散在 0-65535）
LTO:     1847 tuples（ID 從 1 到 約 3200，密集排列）

差距 5 tuples 是因為 PCGUARD 有少數 collision，
兩條實際不同的 edge 被計算成同一個 tuple。

LTO 的 ID 範圍只用到 1-3200（= 總 BB 數），
比 PCGUARD 的隨機分散更密集，理論上 cache 效率更好。
```

**Map density 的差異**：

```
PCGUARD: 1842 / 65536 = 2.81%（ID 稀疏分布）
LTO:     1847 / 65536 = 2.82%（ID 也稀疏，但可確定無碰撞）

百分比接近，但 LTO 的 1847 是準確的 1847 條不同的 edge；
PCGUARD 的 1842 可能實際上是 1847 條 edge 但有 5 對碰撞。
```

---

## 完整 LTO Build 實戰

### 以 zlib 為例（一個有靜態庫的典型 C 專案）

```bash
# 安裝必要工具（如果沒有）
apt-get install -y afl++ clang llvm

# 確認 LTO 工具鏈版本一致
clang --version          # 例如 14.0.6
llvm-ar --version        # 應該也是 14.x
afl-clang-lto --version  # 確認存在

# 下載並解壓 zlib
wget https://zlib.net/zlib-1.3.1.tar.gz
tar xf zlib-1.3.1.tar.gz
cd zlib-1.3.1/

# 設定 LTO 環境
export CC=afl-clang-lto
export CXX=afl-clang-lto++
export AR=llvm-ar
export RANLIB=llvm-ranlib
# NM 也可能需要替換，視 build system 而定
export NM=llvm-nm

# Build
./configure --static    # 建 static lib 以便連結到 fuzzing target
make -j$(nproc)

# 確認插樁有效
echo "test input" > /tmp/zlib_seed
# 假設有一個 zlib fuzzing harness
afl-showmap -o /tmp/zlib_lto.map -- ./minigzip < /tmp/zlib_seed
wc -l /tmp/zlib_lto.map   # 應該有合理數量的 tuples
```

### LTO Build 失敗的偵錯流程

```bash
# 症狀 1：link error "cannot find -lXXX" 或 bitcode 相關錯誤
# 先確認 AR 版本
which ar     # 是 /usr/bin/ar 還是 llvm-ar？
ar --version # 版本是否和 clang 一致？

# 症狀 2：build 成功但 afl-showmap 輸出 0 tuples
# → target 沒有被插樁，LTO pass 沒有跑到
afl-clang-lto -v test.c -o test 2>&1 | grep "pass"
# 如果看不到 AFL++ LTO pass 的輸出，環境設定有問題

# 症狀 3：連結時出現 "duplicate symbol" 錯誤
# → 可能混用了 LTO bitcode 和 native ELF
file *.o | grep -v "LLVM bitcode"  # 找出哪些 .o 不是 bitcode
# 這些 .o 無法參與 LTO，要麼找到原始碼重新編譯，要麼改用 PCGUARD

# 症狀 4：build 極度緩慢（超過預期 3 倍以上）
# → LTO link step 在跑所有 LLVM pass，屬於正常現象
# 可以用 make -j1（單核）先確認能 build 完再加速

# 症狀 5：clang: error: cannot link bitcode file
# → AR 或 RANLIB 沒有替換成 llvm 版本
export AR=llvm-ar RANLIB=llvm-ranlib NM=llvm-nm
make clean && make  # 必須完整 rebuild
```

---

## 對比與取捨

| | PCGUARD | LTO |
|---|---|---|
| Collision | 有（3-10% 在大型 target）| **無** |
| Edge ID 分配 | Compile time，隨機 | Link time，全局遞增 |
| Build complexity | 低（`CC=afl-clang-fast`）| **高**（需替換 AR/RANLIB）|
| Build time overhead | 低（~10%）| **高**（link time 處理全程式 IR）|
| Runtime overhead | ~10-15% | ~5-10%（ID 密集，cache 效率更好）|
| CmpLog 支援 | 是 | **是（且更精準）** |
| Autodictionary | 否 | **是**（可自動提取 magic bytes）|
| Assembly 原始碼 | 可以 | 部分限制（.s 無法插樁）|
| Precompiled library | 可以（但沒有 coverage）| 同左 |
| 適合場景 | 快速迭代、相容性優先 | 長期 campaign、精度優先 |
| 最低 LLVM 版本 | 9.0 | 11.0（推薦 14+）|

---

## 踩雷集錦

**1. `llvm-ar` 版本不一致**

很多人以為系統裝了 clang-14 後，`llvm-ar` 也是 14，但實際上：
- Ubuntu/Debian 的 `llvm-ar` 預設指向的版本不一定和 `clang` 一致
- `which llvm-ar` 可能指向 `/usr/bin/llvm-ar-12` 而 `clang` 是 14

```bash
# 正確做法：明確指定版本號
export AR=llvm-ar-14
export RANLIB=llvm-ranlib-14
# 或者用 update-alternatives 設定預設版本
```

**2. `./configure && make` 之前忘記 export AR 和 RANLIB**

很多人以為 `CC=afl-clang-lto` 後一切都會自動處理，但實際上：
- autoconf 生成的 `configure` 腳本會自動偵測 `AR` 和 `RANLIB`
- 如果沒有 export，它會找到 `/usr/bin/ar` 並寫進 Makefile
- 後續的 `make` 用系統 `ar` 打包 bitcode，link time 找不到 IR

症狀：build 成功，`afl-showmap` 輸出 0 tuples。

**3. 靜態連結的第三方 library 不是 bitcode**

很多人以為用 LTO build 就能對所有 code 插樁，但實際上：
- `/usr/lib/libz.a`（系統安裝的 zlib）是 native ELF，不是 bitcode
- 這些 library 裡的函式不會被 AFL++ 插樁
- Coverage 只覆蓋你自己 build 的 code

解法：如果需要對 library 內部的 code 做 coverage，要從原始碼重新用 LTO 編譯這個 library。

**4. LTO build 失敗的錯誤訊息很難看懂**

很多人以為 LTO 失敗會有明確的「LTO 相關」錯誤，但實際上：
- 常見的失敗是靜默的：build 成功但沒有插樁
- 或者是迷惑性的 linker error（`undefined reference to __afl_trace`）
- 真正有用的資訊在 `afl-clang-lto -v` 的輸出裡

偵錯原則：先用 `afl-showmap` 確認插樁有沒有生效，而不是看 build 有沒有成功。

**5. LTO 和 `-Wl,-z,now` 等 linker flags 的衝突**

某些 hardening flags 或 linker 選項會和 LTO 的 linker plugin 衝突：
- `-Wl,-z,relro` 通常沒問題
- 某些 `-Wl,--as-needed` 組合在 LTO 模式下可能讓 AFL++ runtime 被誤認為 unused 而被丟棄
- 如果 runtime 被丟棄，所有插樁的 call 都找不到目標 → linker error

解法：暫時移除不必要的 linker hardening flags，先確認 LTO build 成功，再逐步加回去確認哪個 flag 有問題。

---

## 進階：再往深一層

### LTO + Context-Sensitive Coverage

LTO 模式可以和 context-sensitive coverage 組合使用：

```bash
# LTO + context-sensitive coverage
AFL_LLVM_CTX=1 CC=afl-clang-lto AR=llvm-ar RANLIB=llvm-ranlib \
  ./configure && make

# 或者用 NGRAM（記錄最近 N 條 edge 的序列）
AFL_LLVM_INSTRUMENT=LTO-NGRAM4 CC=afl-clang-lto AR=llvm-ar RANLIB=llvm-ranlib \
  ./configure && make
```

LTO + CTX 的組合比 PCGUARD + CTX 更強，因為 LTO 本身已經 collision-free，加上 context 的區分後，bitmap 的有效資訊量更高。

代價：bitmap 有效使用率提升，但也更快填滿（需要更大的 MAP_SIZE）。

### `AFL_LLVM_LTO_AUTODICTIONARY`：自動提取 Magic Bytes

LTO pass 在 link time 能看到整個程式的 IR，可以靜態分析出所有和字串常量比較的指令：

```bash
# 啟用 LTO autodictionary
AFL_LLVM_LTO_AUTODICTIONARY=1 CC=afl-clang-lto AR=llvm-ar RANLIB=llvm-ranlib \
  ./configure && make

# afl-fuzz 會自動載入 autodictionary
afl-fuzz -i seeds/ -o out/ -- ./target @@
```

工作原理：LTO pass 掃描所有 `icmp` 指令（LLVM 的整數比較），如果比較的一邊是常量（`i32 0xDEADBEEF`、字串指針等），就把這個常量加入 dictionary。

結果：fuzzer 不需要靠暴力突破 magic number 檢查，因為 dictionary 裡已經有 `0xDEADBEEF` 這個值了。

這個功能只在 LTO 模式下可用，PCGUARD 和 CLASSIC 因為不做全局 IR 分析，無法可靠地提取跨 TU 的常量。

### 直接閱讀 LTO Pass 的原始碼

```bash
# 在 AFL++ repo 裡找到 LTO pass
# 關鍵函式：runOnModule（LTO pass 入口）
# 在 AFL++ 4.09c 的原始碼位置：

# instrumentation/afl-llvm-lto-instrumentation.so.cc
#   ├── runOnModule()：入口，遍歷全局 CFG
#   ├── insertInstrumentation()：插入 bitmap write IR
#   └── autodictionary 相關邏輯

# 閱讀 runOnModule 的步驟：
# 1. 找到 for (auto &F : M)（遍歷函式）
# 2. 找到 for (auto &BB : F)（遍歷 basic block）
# 3. 找到 edge_id++ 的位置（ID 分配）
# 4. 找到 IRBuilder 插入 instrumentation 的位置

# 可以這樣找：
grep -n "edge_id\|runOnModule\|insertInstr" \
    instrumentation/afl-llvm-lto-instrumentation.so.cc | head -40
```

---

## 動手練習

**練習 1：量化 collision 的實際影響**

```bash
# 建立一個刻意有很多 basic block 的 target
cat > /tmp/collision_test.c << 'EOF'
#include <stdio.h>
#include <string.h>

/* 100 個函式，每個有 3-5 個 basic block，
   在 64KB bitmap 上理論碰撞機率可計算 */
void f001(char c) { if (c > 'a') { if (c < 'z') printf("f001_inner\n"); } }
void f002(char c) { if (c > 'b') { if (c < 'y') printf("f002_inner\n"); } }
/* ... 想像這裡有 100 個這樣的函式 ... */

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    char *s = argv[1];
    for (int i = 0; i < strlen(s) && i < 100; i++) {
        /* 根據 i 呼叫不同函式 */
        switch (i % 5) {
            case 0: f001(s[i]); break;
            case 1: f002(s[i]); break;
            /* ... */
        }
    }
    return 0;
}
EOF

# PCGUARD build
afl-clang-fast -O2 -o /tmp/ct_pcguard /tmp/collision_test.c

# LTO build（如果可用）
CC=afl-clang-lto AR=llvm-ar RANLIB=llvm-ranlib \
  afl-clang-lto -O2 -o /tmp/ct_lto /tmp/collision_test.c

# 比較兩種模式的 edge ID 分布
echo "ABCDE" > /tmp/ct_input
afl-showmap -o /tmp/ct_pcguard.map -- /tmp/ct_pcguard "ABCDE"
afl-showmap -o /tmp/ct_lto.map    -- /tmp/ct_lto "ABCDE"

# LTO 的 max ID 應該等於總 BB 數（密集分布）
# PCGUARD 的 ID 應該是稀疏分布在 0-65535

awk -F: '{print $1}' /tmp/ct_pcguard.map | sort -n | tail -5
awk -F: '{print $1}' /tmp/ct_lto.map    | sort -n | tail -5
```

**練習 2：實際體驗 LTO Autodictionary**

```bash
# 建立一個有 magic bytes 的 target
cat > /tmp/magic_target.c << 'EOF'
#include <stdio.h>
#include <string.h>
#include <stdint.h>

int parse(const char *buf, int len) {
    if (len < 4) return -1;
    /* magic number check */
    uint32_t magic = *(uint32_t *)buf;
    if (magic != 0x89504E47) return -2;  /* PNG magic */
    if (len < 8) return -3;
    if (buf[4] != '\r' || buf[5] != '\n') return -4;
    printf("Valid PNG header!\n");
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    char buf[256];
    int n = fread(buf, 1, sizeof(buf), f);
    fclose(f);
    parse(buf, n);
    return 0;
}
EOF

# 不用 autodictionary：fuzzer 需要靠暴力猜到 0x89504E47
afl-clang-fast -O2 -o /tmp/magic_no_dict /tmp/magic_target.c

# 用 LTO autodictionary：fuzzer 從字典直接拿到 magic bytes
AFL_LLVM_LTO_AUTODICTIONARY=1 CC=afl-clang-lto AR=llvm-ar RANLIB=llvm-ranlib \
  afl-clang-lto -O2 -o /tmp/magic_lto /tmp/magic_target.c

echo "garbage" > /tmp/seeds/seed1

# 跑兩份 fuzzer，比較找到 "Valid PNG header!" 路徑的速度
# 有 autodictionary 的應該快很多（可能幾秒 vs 幾分鐘）
```

---

## 本章重點整理

- LTO 的 collision-free 來自「把插樁 pass 推到 link time」：所有 TU 的 LLVM IR 合併成一個 module 後，pass 才執行，此時能看到完整 CFG 並分配全局唯一的遞增 ID（1, 2, 3...），唯一性由結構保證，不靠隨機。
- LTO 的代價是**build complexity**（必須替換 `AR=llvm-ar RANLIB=llvm-ranlib`，toolchain 版本必須一致）和**build time**（link step 要處理全程式 IR 和最佳化），不是所有 target 都能直接 LTO build（assembly 和 precompiled library 有限制）。
- LTO 獨有的功能：**autodictionary**（`AFL_LLVM_LTO_AUTODICTIONARY=1`，自動提取 magic bytes 加入 dictionary）；結合 context-sensitive coverage 時精確度也高於 PCGUARD。

---

## 自我檢核

1. PCGUARD 的 `__sanitizer_cov_trace_pc_guard_init()` 在 runtime 給 guard 賦值，理論上也可以在這裡實現全局唯一 ID（把所有 TU 的 init 串成一個全局計數器）。為什麼 AFL++ 的 PCGUARD 實作沒有這樣做？這樣做有什麼技術障礙？

2. LTO pass 按「遍歷順序」分配 ID，不同的遍歷順序（DFS vs BFS，或者函式的遍歷順序不同）會不會影響 fuzzing 結果？edge ID 的具體數值對 fuzzer 重要嗎？

3. 一個 target 有 3000 個 BB，LTO 分配 ID 1 到 3000，bitmap 大小是 65536。`bitmap[3001]` 到 `bitmap[65535]` 這些格子永遠不會被寫入。這對 `has_new_bits()` 的效率有什麼影響？是好是壞？

4. `AFL_LLVM_LTO_AUTODICTIONARY` 掃描 `icmp` 指令提取常量。如果 magic bytes 是透過動態計算得到的（例如從 config file 讀取後和輸入比較），autodictionary 能找到嗎？有什麼替代方案？

5. LTO build 完的 binary，如果你用 `objdump -d` 看 disassembly，應該能找到什麼樣的 pattern 證明 LTO 插樁確實有效？（提示：找 `__afl_area_ptr` 相關的記憶體存取指令）

---

## 延伸閱讀

**CollAFL: Path Sensitive Fuzzing（Gan et al., S&P 2018）**
- **核心貢獻**：系統量化了 AFL bitmap collision 的發生率和對 fuzzing 效果的影響，是 AFL++ LTO 設計的核心動機文獻。
- **讀哪裡**：Section 3（"Design"），特別是 Section 3.1 的 collision 定義和 Section 3.2 的統計分析；Figure 2（collision 率 vs target 大小）是本章的主要數據來源。
- **和本章的關聯**：提供了 3-10% collision 率的量化基礎，解釋了為什麼 LTO 的 collision-free 特性在大型 target 上具有實際意義而非純學術優化。

**LLVM Link Time Optimization 文件**
- **核心貢獻**：LLVM 官方的 LTO 架構說明，解釋 LTO 如何讓 linker 看到完整的程式 IR，以及 pass pipeline 在 link time 的工作方式。
- **讀哪裡**：https://llvm.org/docs/LinkTimeOptimization.html — "How LTO Works" 節，以及 "Using LTO with clang" 節（實際操作步驟）
- **和本章的關聯**：理解 `afl-clang-lto` 為什麼需要 `-flto` flag，以及 LTO pass 在 LLVM pipeline 的哪個位置執行。

**AFL++ `instrumentation/README.lto.md`（官方說明）**
- **核心貢獻**：AFL++ 官方對 LTO 模式的完整說明，包含所有已知限制、troubleshooting guide 和環境設定的完整範例。
- **讀哪裡**：整份文件都值得讀，特別是 "Requirements"（toolchain 版本要求）和 "Troubleshooting"（常見失敗的解法）
- **和本章的關聯**：實際操作的最終參考，包含比本章更詳細的 edge case 和平台特定問題。

**AFL++ WOOT 2020 Paper Section 4（Fioraldi et al.）**
- **核心貢獻**：AFL++ 論文中對各種 instrumentation 技術的系統評估，包含 LTO vs PCGUARD 在 benchmark 上的實測 throughput 和 coverage 比較。
- **讀哪裡**：Section 4.3（LTO instrumentation 的設計）和 Section 6（benchmark 結果中的 instrumentation 比較部分）
- **和本章的關聯**：提供 LTO 和 PCGUARD 在真實 target 上的性能對比數字，幫助建立對 build time 和 runtime overhead 的量化感知。

---

← [上一章：Ch 6 Compile-time Instrumentation](./06-compile-time-instrumentation.md)

→ [下一章：Ch 8 Runtime Instrumentation](./08-runtime-instrumentation.md)
