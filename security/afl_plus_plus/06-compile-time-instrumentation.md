# Ch 6 — Compile-time Instrumentation：四種插樁模式的選型

> **目標**：理解 AFL++ 四種 compile-time instrumentation 模式的差異，能根據 target 特性做正確選型。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64 Linux

---

## 為什麼需要這個？

AFL 最早的 instrumentation 只有一種方式：修改 `gcc` 的組語輸出，在每個 basic block 開頭插入手寫的 x86 指令序列。這個方法笨重且難以維護，無法利用編譯器的最佳化基礎設施。

隨著 LLVM 生態的成熟，插樁有了更好的選擇：在編譯器的中間表示（IR）層面工作，更乾淨、更可組合、也更正確。AFL++ 在原始 AFL 的基礎上做了大幅擴展，現在支援四種截然不同的 compile-time instrumentation 模式。每種模式都是為了解決不同的問題場景，選錯模式不會讓 fuzzer 崩潰，但會讓你在錯誤的地方浪費時間。

---

## 先建立直覺

```
你的 source code
      │
      ↓ 編譯
   IR（中間表示）   ← instrumentation pass 在這裡插樁
      │
      ↓ 最佳化 + 代碼生成
   object files (.o)
      │
      ↓ 連結（LTO 的 pass 在這裡）
   binary
```

**PCGUARD** 和 **CLASSIC** 在「IR → object file」這個步驟插樁。每個 translation unit（.c 檔）獨立處理，互相不知道對方的 edge ID。

**LTO** 把插樁推到最後的連結步驟，此時所有 translation unit 的 IR 已經合併，可以看到整個程式的全局 CFG，因此能分配不重複的 edge ID。

**GCC plugin** 走完全不同的路：不用 clang，不用 LLVM，直接插入 GCC 的編譯流程。

---

> 如果你對 edge coverage bitmap 的工作原理還不熟，先回看 [Ch 5](./05-edge-coverage-bitmap.md)。

---

## 四種模式概覽

### 模式一：PCGUARD（推薦預設）

**用什麼編譯器**：`afl-clang-fast` / `afl-clang-fast++`

**插樁機制**：使用 LLVM 內建的 SanitizerCoverage 基礎設施，讓 LLVM 在每個 basic block 開頭插入對 `__sanitizer_cov_trace_pc_guard()` 的呼叫。AFL++ 的 runtime library 提供這個 callback 的實作，callback 內部執行 bitmap write。

**為什麼穩定**：LLVM 官方維護這個 infrastructure，各個 LLVM 版本的行為一致；不需要 AFL++ 自己維護低層的 IR 操作。

```bash
# 最簡單的 PCGUARD 編譯
CC=afl-clang-fast CXX=afl-clang-fast++ ./configure && make

# 明確指定（PCGUARD 是 afl-clang-fast 的預設）
AFL_LLVM_INSTRUMENT=PCGUARD CC=afl-clang-fast ./configure && make
```

### 模式二：CLASSIC

**用什麼編譯器**：`afl-clang-fast`（加環境變數）

**插樁機制**：AFL++ 自己的 LLVM pass，直接在每個 basic block 開頭插入 `__afl_area_ptr[prev_loc ^ cur_loc]++` 的 store 指令序列。比 PCGUARD 少一層 callback 間接，理論上略快。

```bash
AFL_LLVM_INSTRUMENT=CLASSIC CC=afl-clang-fast ./configure && make
```

**什麼時候用**：你需要最小 overhead，而且 target 不需要 CmpLog 等進階功能。

### 模式三：LTO

**用什麼編譯器**：`afl-clang-lto` / `afl-clang-lto++`

**插樁機制**：Link-Time Optimization pass。build 流程先用 `-flto` 把所有 translation unit 編譯成 LLVM bitcode（而不是 native object），連結時 AFL++ 的 LTO pass 讀取整個程式的合併 IR，分配全局唯一的 edge ID（從 1 開始遞增），然後才生成 native code。

**核心優勢**：collision-free（詳見 Ch 7）。

```bash
# LTO 需要替換 ar 和 ranlib，否則 static library 會壞掉
CC=afl-clang-lto \
CXX=afl-clang-lto++ \
AR=llvm-ar \
RANLIB=llvm-ranlib \
./configure && make
```

### 模式四：GCC plugin

**用什麼編譯器**：`afl-gcc-fast`

**插樁機制**：GCC 的 plugin 介面（`-fplugin`），在 GCC 的 GIMPLE IR 層面插入 AFL++ 的 instrumentation。功能上最接近原始 AFL 的 `afl-gcc`，但比 clang 模式少了很多進階功能（無 CmpLog、無 LTO、無 context-sensitive coverage）。

```bash
CC=afl-gcc-fast CXX=afl-g++-fast ./configure && make
```

**什麼時候用**：target 的 build system 和 clang 不相容（某些使用 GCC 特定擴充的 C 程式），或者系統上沒有可用的 clang。

---

## 核心機制：PCGUARD 底層

### `__sanitizer_cov_trace_pc_guard` 怎麼工作

LLVM 在編譯時在每個 basic block 開頭插入：

```llvm
; LLVM IR（簡化）
call void @__sanitizer_cov_trace_pc_guard(i32* @__sancov_guard_X)
```

`@__sancov_guard_X` 是這個 basic block 的 guard 指針，由 LLVM 的 `__sanitizer_cov_trace_pc_guard_init()` 初始化。

AFL++ 的 runtime（`instrumentation/afl-compiler-rt.o.c`）提供這個函式的實作：

```c
/* AFL++ 的 __sanitizer_cov_trace_pc_guard 實作（概念版）*/
void __sanitizer_cov_trace_pc_guard(uint32_t *guard) {
    /* guard 指向這個 basic block 的唯一 ID（由 LLVM 在 init 時賦值）*/
    uint32_t cur  = *guard;
    uint32_t idx  = __afl_prev_loc ^ cur;
    __afl_area_ptr[idx % MAP_SIZE]++;
    __afl_prev_loc = cur >> 1;
}

/* 程式啟動時，LLVM 呼叫 init 函式，AFL++ 在這裡把 guard 對應到 AFL 的 bitmap index */
void __sanitizer_cov_trace_pc_guard_init(uint32_t *start, uint32_t *stop) {
    /* start 到 stop 是所有 guard 指針的陣列 */
    static uint32_t n = 0;
    for (uint32_t *x = start; x < stop; x++) {
        *x = ++n;   /* 給每個 basic block 一個唯一 ID（在單個 TU 內唯一）*/
    }
}
```

**PCGUARD 和 CLASSIC 的根本差異**：PCGUARD 依賴 LLVM 決定「哪裡插樁」（由 SanitizerCoverage pass 決定），AFL++ 只提供 callback 實作；CLASSIC 是 AFL++ 自己的 LLVM pass 同時決定「哪裡插樁」和「插什麼」。

---

## 底層機制：CLASSIC 模式的 LLVM Pass

### LLVM IR 層面的插樁操作

CLASSIC 模式的 AFL++ LLVM pass（`instrumentation/afl-llvm-pass.so.cc`）做的事情：

```
對程式中的每個函式：
  對函式 CFG 中的每個 basic block：
    1. 在 block 開頭生成一個隨機 16-bit ID（cur_loc）
    2. 插入以下 IR 序列：
       a. 載入 __afl_prev_loc（thread-local）
       b. 計算 idx = prev_loc ^ cur_loc
       c. 從 __afl_area_ptr + idx 讀取 uint8
       d. 加一（with NEVERZERO 邏輯）
       e. 寫回
       f. 更新 __afl_prev_loc = cur_loc >> 1
```

插入的 LLVM IR 大致如下：

```llvm
; 插入到 basic block %BB 的開頭（簡化，省略 NEVERZERO）
%prev = load i32, i32* @__afl_prev_loc
%xored = xor i32 %prev, <cur_loc_constant>
%area_base = load i8*, i8** @__afl_area_ptr
%slot_ptr = getelementptr i8, i8* %area_base, i32 %xored
%old_val = load i8, i8* %slot_ptr
%new_val = add i8 %old_val, 1
store i8 %new_val, i8* %slot_ptr
store i32 <cur_loc_shifted>, i32* @__afl_prev_loc
```

這段 IR 在 code generation 後變成約 8-12 條 x86 指令，overhead 通常在 5-15% 之間。

### Pass 的工作流程

```
afl-clang-fast 啟動
      │
      ↓
clang 前端：詞法分析 + 語法分析 → AST
      │
      ↓
LLVM IR 生成
      │
      ↓
AFL++ 的 LLVM pass 執行（CLASSIC 模式）
  ├── 遍歷所有 Function
  │     └── 遍歷所有 BasicBlock
  │           └── 在開頭插入 instrumentation IR
  └── 每個 BasicBlock 獲得一個隨機 ID
      │
      ↓
LLVM 最佳化 passes（O1/O2/O3）
      │
      ↓
x86 code generation → .o file
```

問題在這裡：每個 `.c` 檔獨立處理，`foo.c` 和 `bar.c` 各自隨機選 ID，互不知道對方選了什麼。兩個不同 `BasicBlock` 可能選到同一個 ID，導致 collision。LTO 模式（Ch 7）把 pass 移到連結步驟，解決這個問題。

---

## 選型矩陣

根據 target 的特性選擇正確的模式：

| Target 特性 | 推薦模式 | 原因 |
|------------|---------|------|
| 標準 C/C++ 程式，clang 可用 | **PCGUARD** | 穩定、相容性好、支援所有進階功能 |
| 需要最低 overhead | **CLASSIC** | 少一層 callback，略快於 PCGUARD |
| 需要最高 coverage 精度 | **LTO** | collision-free，適合長時間大型 fuzzing |
| 只有 GCC 可用 | **GCC plugin** | 唯一選擇 |
| 含大量 C++ template / boost | **PCGUARD** | CLASSIC 的 IR pass 有時對複雜 C++ 不穩定 |
| 嵌入式 cross-compile | **CLASSIC 或 GCC plugin** | LTO 的 toolchain 需求較嚴格 |
| 需要 CmpLog 功能（Ch 15）| **PCGUARD 或 LTO** | GCC plugin 和 CLASSIC 不支援 CmpLog |
| Build 時間敏感 | **PCGUARD 或 CLASSIC** | LTO 的 link time 顯著增加 |

---

## 進一步用法：`AFL_LLVM_INSTRUMENT` 環境變數

AFL++ 允許在不換編譯器的情況下，用環境變數切換插樁模式：

```bash
# 強制使用 CLASSIC 模式（即使用的是 afl-clang-fast）
AFL_LLVM_INSTRUMENT=CLASSIC CC=afl-clang-fast ./configure && make

# 可用的值：PCGUARD, CLASSIC, LLVMNATIVE, CTX, NGRAM2..NGRAM16
# LLVMNATIVE 表示只用 LLVM 原生的 SanCov，不做 AFL 的 bitmap write（測試用）

# 查看當前 afl-clang-fast 預設用什麼
afl-clang-fast --version 2>&1 | grep "Instrumentation"
```

**注意**：`AFL_LLVM_INSTRUMENT` 設定在編譯時生效，不是在 `afl-fuzz` 執行時。設定了但忘記重新編譯是常見錯誤。

---

## 對比與取捨

| | PCGUARD | CLASSIC | LTO | GCC plugin |
|---|---|---|---|---|
| 使用的編譯器 | afl-clang-fast | afl-clang-fast | afl-clang-lto | afl-gcc-fast |
| Collision | 有（同 CLASSIC）| 有 | **無** | 有 |
| 執行 overhead | ~10-15% | ~5-10% | ~5-10% | ~10-20% |
| Build time overhead | 低 | 低 | **高**（link time）| 低 |
| CmpLog 支援 | 是 | 否 | 是 | 否 |
| Context-sensitive cov | 是 | 是 | 是（+更精準）| 否 |
| Toolchain 要求 | clang + llvm | clang + llvm | clang + llvm-ar/ranlib | gcc + plugin |
| 穩定性 / 相容性 | **高**（LLVM 官方）| 中（AFL++ 自維護）| 中（build 較複雜）| 低（功能最少）|
| 推薦優先級 | **1（預設）** | 3 | 2（精度要求高時）| 4（無 clang 時）|

---

## 踩雷集錦

**1. GCC plugin 和 clang 模式不能混用**

很多人以為可以用 `afl-clang-fast` 編譯 main.c，再用 `afl-gcc-fast` 編譯 lib.c，最後連結。但實際上這會產生「編譯成功但插樁邏輯損壞」的 binary：
- 兩種模式使用不同的 runtime symbols（`__afl_area_ptr` vs `__sanitizer_cov_trace_pc_guard`）
- link time 可能衝突或靜默地只有一種插樁生效
- fuzzer 看到的 coverage 是殘缺的，但不會報錯

解法：整個 target 的所有 translation unit 必須用同一種模式編譯。

**2. LTO 必須替換 AR 和 RANLIB**

很多人以為 `CC=afl-clang-lto` 就足夠了，但實際上 LTO 的 `.o` 是 LLVM bitcode，不是 native ELF object。系統的 `ar` 不懂 bitcode，會把它當成普通的靜態庫打包，連結時 LTO pass 看不到這些 TU 的 IR：

```bash
# 錯誤（系統 ar 無法處理 bitcode）
CC=afl-clang-lto ./configure && make

# 正確
CC=afl-clang-lto AR=llvm-ar RANLIB=llvm-ranlib ./configure && make
```

如果 build 系統內部寫死了 `ar`（很多 autoconf 生成的 Makefile），可能需要：

```bash
# 把 llvm-ar 做成 ar 的 wrapper
sudo ln -s $(which llvm-ar) /usr/local/bin/ar  # 或者修改 PATH
```

**3. `AFL_LLVM_INSTRUMENT` 是編譯期選項，不是執行期選項**

很多人以為在跑 `afl-fuzz` 之前設定 `AFL_LLVM_INSTRUMENT=LTO` 就能切換模式，但實際上：
- `AFL_LLVM_INSTRUMENT` 只在呼叫 `afl-clang-fast` 編譯時有效
- binary 一旦編譯完成，插樁模式就固定了
- `afl-fuzz` 執行時設定這個環境變數完全無效

**4. 沒用 afl-clang-fast 編譯卻跑了 afl-fuzz**

最隱蔽的錯誤：用系統的 `cc`（`/usr/bin/gcc`）編譯了 target，然後跑 `afl-fuzz`。

afl-fuzz 會啟動但沒有任何 coverage 訊號。症狀是：
- `afl-fuzz` UI 的 `map density` 永遠是 0%（或停在初始值）
- `cycles done` 飛速增加但 `paths found` 幾乎不動
- 沒有任何報錯

偵測方式：

```bash
# 用 afl-showmap 確認 binary 有沒有被插樁
afl-showmap -o /dev/null -- ./target < seed_file
# 如果輸出 "Coverage map size: 0" 代表沒插樁
```

**5. 混用不同版本的 clang 和 llvm-ar**

特別在 LTO 模式下，`clang` 和 `llvm-ar` 的版本必須完全一致：
- clang-14 生成的 bitcode 格式和 clang-15 不同
- `llvm-ar-14 + clang-15` 可能在某些情況下靜默地生成損壞的靜態庫

```bash
# 確認版本一致
clang --version         # 例如 14.0.6
llvm-ar --version       # 必須也是 14.x
```

---

## 進階：再往深一層

### `AFL_LLVM_CMPLOG=1`：讓 fuzzer 看見比較指令

CmpLog 是 AFL++ 的一個殺手功能：除了 coverage，還讓 fuzzer 記錄所有「比較指令（`cmp`）的兩個運算元」到另一個 shared memory 區域。

```bash
# 編譯兩份 binary：一份正常插樁，一份加 CmpLog
AFL_LLVM_CMPLOG=1 CC=afl-clang-fast -o target_cmplog target.c
CC=afl-clang-fast -o target_normal target.c

# 跑 fuzzer 時指定 CmpLog binary（-c 選項）
afl-fuzz -i seeds/ -o out/ -c ./target_cmplog -- ./target_normal @@
```

CmpLog 讓 fuzzer 知道：「要讓 if (x == 0xDEADBEEF) 成立，你得輸入 0xDEADBEEF」。這對 magic number 和 checksum 突破非常有效。CLASSIC 和 GCC plugin 模式不支援 CmpLog；PCGUARD 和 LTO 都支援。Ch 15 會深入 CmpLog 的工作原理。

### `AFL_LLVM_CTX=1`：Context-sensitive Coverage

```bash
# 啟用 context-sensitive coverage
AFL_LLVM_CTX=1 CC=afl-clang-fast -o target target.c
```

Context-sensitive 模式在計算 bitmap index 時混入了 call stack 的 hash，讓相同的 edge 在不同的 call context 下被視為不同事件（詳見 Ch 5）。

你也可以用 NGRAM 模式（記錄最近 N 條 edge 的序列）：

```bash
# 記錄最近 4 條 edge 的序列作為 context
AFL_LLVM_INSTRUMENT=NGRAM4 CC=afl-clang-fast -o target target.c
```

NGRAM 和 CTX 都增加了 bitmap collision 的機率（因為有效的 bitmap index 空間被分散了），需要搭配更大的 MAP_SIZE 使用。

### 驗證插樁是否生效

```bash
# afl-showmap 用 fork server 執行 target 並收集 coverage
afl-showmap -o /tmp/coverage.map -- ./target < /tmp/seed

# 輸出範例：
# afl-showmap 2>&1 output:
# [*] Executing './target'...
# [+] Captured 1423 tuples in '/tmp/coverage.map'.

# 格式：每行是 "EDGE_ID:HIT_COUNT"
head -20 /tmp/coverage.map
# 12345:1
# 23456:4
# 34567:1
# ...

# 比較兩個輸入的 coverage 差異
afl-showmap -o /tmp/cov1.map -- ./target < input1
afl-showmap -o /tmp/cov2.map -- ./target < input2
diff /tmp/cov1.map /tmp/cov2.map
```

---

## 動手練習

**練習 1：比較四種模式的 overhead**

```bash
# 準備一個有一定複雜度的 target（用 readelf 代替自寫程式）
# 先確認 afl++ tools 可用
which afl-clang-fast afl-clang-lto afl-gcc-fast

# 建立測試程式
cat > /tmp/bench_target.c << 'EOF'
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* 有足夠多的 basic block 讓 overhead 可見 */
int process(const char *buf, int len) {
    int result = 0;
    for (int i = 0; i < len; i++) {
        if (buf[i] > 'a' && buf[i] < 'z') result += buf[i];
        else if (buf[i] > 'A' && buf[i] < 'Z') result -= buf[i];
        else if (buf[i] >= '0' && buf[i] <= '9') result ^= buf[i];
        else result += 1;
    }
    return result;
}

int main(int argc, char **argv) {
    char buf[4096];
    int n = fread(buf, 1, sizeof(buf)-1, stdin);
    buf[n] = '\0';
    printf("%d\n", process(buf, n));
    return 0;
}
EOF

# 無插樁版本（baseline）
gcc -O2 -o /tmp/bench_none /tmp/bench_target.c

# PCGUARD 版本
CC=afl-clang-fast AFL_LLVM_INSTRUMENT=PCGUARD \
  afl-clang-fast -O2 -o /tmp/bench_pcguard /tmp/bench_target.c

# CLASSIC 版本
AFL_LLVM_INSTRUMENT=CLASSIC \
  afl-clang-fast -O2 -o /tmp/bench_classic /tmp/bench_target.c

# 測量 throughput 差異（用 time 跑 10000 次）
echo "hello world test" > /tmp/test_input
time for i in $(seq 10000); do /tmp/bench_none < /tmp/test_input > /dev/null; done
time for i in $(seq 10000); do /tmp/bench_pcguard < /tmp/test_input > /dev/null; done
time for i in $(seq 10000); do /tmp/bench_classic < /tmp/test_input > /dev/null; done
```

觀察：overhead 差距有多大？和預期（PCGUARD > CLASSIC）是否一致？

**練習 2：用 afl-showmap 量化 collision**

```bash
# 對同一個 target，用 CLASSIC 和 LTO（如果可用）分別編譯
# 比較同樣的輸入下，兩種模式的 edge ID 分布是否不同

afl-showmap -o /tmp/classic.map -- /tmp/bench_classic < /tmp/test_input
# 注意輸出的 tuple 數量和 ID 範圍

# 如果有 LTO：
# afl-showmap -o /tmp/lto.map -- /tmp/bench_lto < /tmp/test_input
# LTO 的 ID 會從 1 開始遞增，而 CLASSIC 的 ID 是隨機分散的
```

---

## 本章重點整理

- AFL++ 有四種 compile-time instrumentation 模式：**PCGUARD**（LLVM SanCov callback，推薦預設）、**CLASSIC**（AFL++ 自己的 IR pass，略快）、**LTO**（link-time 全局 CFG，collision-free）、**GCC plugin**（無 clang 時的備選）；`AFL_LLVM_INSTRUMENT` 環境變數可以在不換編譯器的情況下切換。
- **PCGUARD** 依賴 LLVM 官方的 SanitizerCoverage 插樁點，AFL++ 只提供 callback 實作；**CLASSIC** 是 AFL++ 自己的 LLVM pass 直接操作 IR，在每個 basic block 插入 bitmap write 序列。
- **LTO 模式必須替換 AR 和 RANLIB**（`llvm-ar` + `llvm-ranlib`），因為中間產物是 LLVM bitcode 而非 native ELF；忘記替換是最常見的 LTO build 失敗原因。

---

## 自我檢核

1. `AFL_LLVM_INSTRUMENT=PCGUARD` 和 `AFL_LLVM_INSTRUMENT=CLASSIC` 編譯出來的 binary，在 runtime 行為上的最大差異是什麼？（提示：一個有 callback，一個直接寫 bitmap）

2. 為什麼 PCGUARD 的 overhead 通常比 CLASSIC 高，但穩定性評價更高？這兩個特性是否有內在聯繫？

3. 在一個使用 autoconf 的大型 C 專案（如 libpng）上要用 LTO 模式，你需要設定哪些環境變數，修改哪些 build 步驟？如果 build 失敗但沒有明顯錯誤訊息，你會怎麼偵錯？

4. CmpLog 需要編譯兩份 binary（一份插 CmpLog，一份正常插樁），然後在 `afl-fuzz` 的 `-c` 選項指定 CmpLog binary。為什麼不能只用一份 binary 同時做 CmpLog 和 coverage？（提示：想想 CmpLog 的 overhead 和 exec/sec）

5. 如果你的 target 是一個 Python C extension（`.so` 檔），從 Python 程式載入，AFL++ 的 compile-time instrumentation 對它有效嗎？需要做什麼特殊設定？

---

## 延伸閱讀

**AFL++ WOOT 2020 Paper（Fioraldi et al.）**
- **核心貢獻**：AFL++ 論文，Section 4 系統比較了各種 instrumentation 方式的優劣，包含 overhead 測量數據。
- **讀哪裡**：Section 4（"Instrumentation"）和 Section 6（benchmark 結果）
- **和本章的關聯**：給出了 PCGUARD vs CLASSIC vs LTO 的實測 throughput 數字，讓你對 overhead 有量化感知。

**AFL++ `instrumentation/README.md`（官方文件）**
- **核心貢獻**：AFL++ 官方對所有 instrumentation 模式的完整說明，包含每種模式的 feature matrix 和已知限制。
- **讀哪裡**：開頭的選型建議表，以及各模式的「Known issues」節
- **和本章的關聯**：最權威的選型參考，隨版本更新（4.09c 的建議可能和舊版不同）。

**LLVM SanitizerCoverage 文件**
- **核心貢獻**：LLVM 官方對 SanitizerCoverage 的設計說明，包括 trace-pc-guard 的 callback protocol。
- **讀哪裡**：https://clang.llvm.org/docs/SanitizerCoverage.html — "Tracing PCs with guards" 節
- **和本章的關聯**：理解 PCGUARD 模式的底層工作原理，特別是 `__sanitizer_cov_trace_pc_guard_init()` 的初始化流程。

**CollAFL: Path Sensitive Fuzzing（Gan et al., S&P 2018）**
- **核心貢獻**：定量分析了 AFL 的 bitmap collision 問題，提出三種 collision 減少策略，是 AFL++ 設計 LTO 模式的重要動機之一。
- **讀哪裡**：Section 3（"Design"）前半部，collision 定義和統計
- **和本章的關聯**：解釋為什麼 PCGUARD/CLASSIC 的 collision 是真實的工程問題，以及 LTO（Ch 7）要解決的具體痛點。

---

→ [下一章：Ch 7 LTO Deep Dive](./07-lto-deep-dive.md)
