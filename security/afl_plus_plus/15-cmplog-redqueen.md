# Ch 15 — CmpLog / REDQUEEN：破解 Magic Bytes 的殺手鐧

> **目標**：理解 CmpLog / REDQUEEN 如何解決 magic bytes 和多位元組比較的問題，以及它在 AFL++ 裡的實作方式。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 為什麼需要這個？

Coverage-guided fuzzing 的核心假設是：觸發新 edge 的 input 比較有趣，所以留著繼續變異。
這個假設對大多數情況成立，但有一個典型的死穴——**magic bytes 比較**。

```c
if (memcmp(buf, "PNG\r\n\x1a\n", 8) == 0) {
    parse_png(buf, size);  // 這個分支永遠碰不到
}
```

Coverage-guided fuzzer 的 bitflip、arithmetic、havoc 等變異策略，要「碰巧」產生出完全符合這 8 個 bytes 的組合，機率是 1/256^8 ≈ 1/18 兆。
等到宇宙熱寂也跑不進去。

**Dictionary** 是第一個解法：手工或自動提取常數，加到字典讓 fuzzer 插入。
但 dictionary 只解決「有哪些 token」，不解決「這個 token 要放在 input 的**哪個位置**」。
如果你的 parser 在 offset 42 讀 magic bytes，dictionary 插進 offset 0 完全沒用。

REDQUEEN（NDSS 2019）提出了根本性的解法：不只知道「比較什麼」，還要知道「在哪裡比較、對應 input 的哪一段」。

---

## 先建立直覺

想像你要猜一個密碼鎖：

- **純 fuzzing**：盲目轉，靠運氣。
- **Dictionary fuzzing**：你知道常見密碼是 1234、0000，直接試，但你不知道這組密碼在幾個數字轉盤上。
- **REDQUEEN**：你在密碼鎖旁邊裝了一個攝影機，看到轉盤的刻度——你直接把正確答案讀出來，再填進去。

REDQUEEN 的「攝影機」就是**插樁（instrumentation）**：讓程式在做比較的時候，把「我正在比較 input 的第 42-49 bytes 和字串 'PNG\r\n\x1a\n'」這件事記錄下來，fuzzer 再根據這份紀錄直接把正確值填進去。

---

## 橫向連結

- **Ch 5（Edge Coverage Bitmap）**：CmpLog 是在 edge coverage 之上加一層語義資訊。
- **Ch 8（Dictionary）**：Auto-dictionary 解決「比較什麼」，CmpLog 進一步解決「在哪裡比較」。
- **Ch 14（LLVM 插樁）**：CmpLog 是另一種 LLVM pass，和 coverage 插樁在同一個框架下運作。

---

## Input-to-State Correspondence（I2S）

REDQUEEN 的核心洞察叫做 **input-to-state correspondence（輸入對狀態的對應）**。

觀察：程式裡絕大多數的比較操作，結構都是「input 的某幾個 bytes」對上「一個常數」。
這不是理論假設——paper 裡的實驗測量，真實程式裡 70% 以上的比較符合這個結構。

形式化地說：
- 令 `x` 是 input bytes 的某個子序列
- 令 `k` 是程式裡的常數（magic number、checksum、enum 值等）
- 比較的形式是 `f(x) == k`，其中 `f` 通常是 identity（直接比）或簡單的位移、XOR

如果你能識別出「x 在 input 裡的哪個位置，k 是什麼值」，你就能直接把 k 填進 input 的對應位置，繞過比較。

---

## Colorization：建立 Input-to-State 的 Mapping

光知道比較操作有這個結構還不夠。問題是：同一個 input byte 可能流入**多個**比較操作，程式狀態是複雜的函式。你需要一個方法確認「input 的第 i bytes 確實影響了比較 C 的哪個 operand」。

REDQUEEN 的解法叫 **colorization**：

```
原始 input:  [A A A A A A A A A A A A]
                      ↕（注入隨機 bytes）
染色 input:  [A A A X Y Z A A A A A A]
```

1. 把 input 分成多個小塊（block）。
2. 對每個 block，把它替換成隨機 bytes。
3. 執行程式，觀察哪些 comparison operand 改變了。
4. 如果 comparison C 的 operand 在替換 block[i] 後改變了，就說「input 的 block[i] 流入了 comparison C」。

這個過程建立起 `input 位置 → comparison` 的 mapping。
之後的 mutation 就針對性地：把 comparison 的另一個 operand（通常是常數 k）patch 進 input 的對應位置。

---

## CmpLog：AFL++ 的 REDQUEEN 實作

AFL++ 把 REDQUEEN 的概念實作為 **CmpLog**，分成三個層次：

### 層次 1：CmpLog 插樁 LLVM Pass

`instrumentation/afl-llvm-cmplog-instructions.so.cc` 是一個 LLVM pass，在編譯時對每個比較操作插樁：
- `icmp`（整數比較）
- `memcmp`、`strcmp`、`strncmp` 等常見比較函式（透過 `afl-llvm-cmplog-routines.so.cc` hook）

插樁後的 code 在執行比較時，把兩個 operand 的值寫入一個 shared memory（CmpLog map）。

### 層次 2：雙 Binary 架構

你需要為同一個 target 編譯**兩個版本**：

| Binary | 用途 | 怎麼建 |
|--------|------|--------|
| Normal binary | 正常 fuzz，跑 coverage | `afl-clang-fast -o target target.c` |
| CmpLog binary | 只用來讀取 comparison log | `AFL_LLVM_CMPLOG=1 afl-clang-fast -o target_cmplog target.c` |

### 層次 3：afl-fuzz 的雙 Process 執行

啟動 fuzzer 時，用 `-c` flag 指定 cmplog binary：

```bash
# Step 1: 建 cmplog binary
AFL_LLVM_CMPLOG=1 afl-clang-fast -o target_cmplog target.c

# Step 2: 建 normal binary（正常 coverage 插樁）
afl-clang-fast -o target target.c

# Step 3: 啟動 afl-fuzz，同時使用兩個 binary
afl-fuzz -c ./target_cmplog -i seeds/ -o out/ -- ./target @@
```

afl-fuzz 在內部為每個有潛力的 input 執行兩次：
1. 用 **normal binary** 跑 coverage（和平時一樣）
2. 用 **cmplog binary** 跑 comparison logging，收集這次執行裡所有的 comparison operand pair

---

## 底層機制：它是怎麼運作的？

```
┌─────────────────────────────────────────────────────────────┐
│                        afl-fuzz                             │
│                                                             │
│  ┌─────────────────────┐   ┌─────────────────────────────┐  │
│  │   Normal child      │   │     CmpLog child            │  │
│  │   ./target @@       │   │     ./target_cmplog @@      │  │
│  │                     │   │                             │  │
│  │  Coverage SHM       │   │  CmpLog SHM                 │  │
│  │  [edge bitmap]      │   │  [cmp_operand_0, operand_1] │  │
│  │  64KB               │   │  每個 cmp 4 slots x 16B     │  │
│  └─────────────────────┘   └─────────────────────────────┘  │
│           │                           │                     │
│           ▼                           ▼                     │
│   新 edge？→ 加入 queue        I2S mapping 建立              │
│                                       │                     │
│                          ┌────────────▼──────────────────┐  │
│                          │  Redqueen Mutations           │  │
│                          │  - operand → input patch      │  │
│                          │  - colorization filter        │  │
│                          │  - checksum bypass            │  │
│                          └───────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**CmpLog SHM 的資料結構**（`include/cmplog.h`）：

```
struct cmp_map {
    struct cmp_header {
        uint32_t hits;    // 這個 comparison 被觸發幾次
        uint8_t  shape;   // operand 的大小（1/2/4/8 bytes）
        uint8_t  type;    // CMP_TYPE_INS / CMP_TYPE_RTN
    } headers[CMP_MAP_W];

    union cmp_operands {
        struct {
            uint64_t v0, v1;  // 兩個 operand 的值
        } vals[CMP_MAP_H];
    } log[CMP_MAP_W];
};
```

每個 comparison 位置最多記錄 `CMP_MAP_H`（預設 32）次不同的 operand pair。

**`src/afl-fuzz-cmplog.c`** 是 fuzzer 端的邏輯：
- `cmplog_exec_target()`：執行 cmplog binary，讀取 SHM
- `cmp_extend_encoding()`：嘗試對 comparison 的常數做各種變換（byte swap、取反等）
- `rtn_extend_encoding()`：處理 `strcmp`/`memcmp` 等函式的 routine-level comparison

---

## I2S vs 純 Dictionary：根本差異

```
target code:
  if (memcmp(buf + 42, "MAGIC_COOKIE", 12) == 0)

Dictionary 的做法：
  把 "MAGIC_COOKIE" 加入字典
  fuzzer 嘗試把它插進 input 的各個位置
  → offset 42 只是 N 個嘗試之一，大多數時間浪費在錯誤 offset

CmpLog / I2S 的做法：
  執行時偵測到 comparison：
    operand_0 = buf[42..53]（來自 input，當前值 "aaaaaaaaaaa\x00"）
    operand_1 = "MAGIC_COOKIE"（常數）
    → 直接把 "MAGIC_COOKIE" 填進 input 的 offset 42
  → 一次就命中
```

這就是 I2S 的威力：它不只知道 token，還知道**位置**。

---

## 對比與取捨

| 策略 | 對 magic bytes 的效果 | 執行 overhead | 需要什麼 |
|------|----------------------|--------------|---------|
| 純 fuzzing（無輔助） | 幾乎無效 | 無 | 無 |
| Dictionary（手工） | 有效，但要人工準備 | 極低 | 人工整理 token 列表 |
| Auto-dictionary（比較 AFL 跑出的 tokens） | 部分有效 | 低 | 無，自動提取 |
| CmpLog / REDQUEEN | 對絕大多數 magic bytes 有效 | 高（-20~30% throughput） | 需額外編 cmplog binary |
| Symbolic execution（如 KLEE） | 理論上完整 | 極高（通常慢 100-1000x） | 特殊編譯環境、難以擴展 |

**何時啟用 CmpLog**：
- Target 有大量 magic bytes、checksum、multi-byte 比較
- 純 coverage fuzzing 長時間卡在同樣的 edge 數
- 你有足夠的 CPU（因為 cmplog binary 會佔用額外執行時間）

---

## 踩雷集錦

**1. CmpLog binary 和 normal binary 必須從相同 source 編譯**

```bash
# 正確：只差 AFL_LLVM_CMPLOG=1
AFL_LLVM_CMPLOG=1 afl-clang-fast -o target_cmplog target.c -lfoo
afl-clang-fast -o target target.c -lfoo

# 常見錯誤：cmplog binary 缺了 compile flags 或 link 了不同版本的 library
# 結果：兩個 binary 的行為不一致，I2S mapping 完全錯誤
```

**2. `-c` 指定的是 cmplog binary，不是普通 binary**

```bash
# 錯誤（常見手誤）：把 normal binary 傳給 -c
afl-fuzz -c ./target -i seeds/ -o out/ -- ./target @@
# 結果：CmpLog 功能完全無效（沒有 CmpLog 插樁），但不會報錯

# 正確
afl-fuzz -c ./target_cmplog -i seeds/ -o out/ -- ./target @@
```

**3. Throughput 降低是預期行為，不是 bug**

CmpLog binary 每次執行：
- 需要額外處理 CmpLog SHM 的讀寫
- 每個 comparison 都要記錄 operand

實測 throughput 通常降低 20-30%。這是正常取捨——對有大量 magic bytes 的 target，CmpLog 帶來的覆蓋率提升遠超過 throughput 損失。
對幾乎沒有 multi-byte 比較的 target（如純數學計算），CmpLog 收益接近零。

**4. 不要只開 CmpLog，要同時跑多個 instance**

```bash
# 常見錯誤：單機只跑一個 afl-fuzz -c
# 正確：主 fuzzer 用 -c，其他 secondary fuzzer 不用，充分利用 CPU
afl-fuzz -M main -c ./target_cmplog -i seeds/ -o out/ -- ./target @@
afl-fuzz -S worker1 -i seeds/ -o out/ -- ./target @@
afl-fuzz -S worker2 -i seeds/ -o out/ -- ./target @@
```

**5. CmpLog 對 checksum 的處理有限制**

若比較的是「input 的某段做 CRC32 的結果」，CmpLog 看到的是 CRC32 的輸出，不是 input bytes。
這種情況 CmpLog 無法直接幫你——需要搭配 custom mutator 或手動 patch checksum 計算。

---

## 進階：再往深一層

### Colorization 的實作細節

`src/afl-fuzz-cmplog.c` 裡的 colorization 並非對整個 input 做，而是採用**啟發式縮小範圍**：

1. 先用二分法找到哪個 input range 影響了特定 comparison
2. 在這個 range 內注入隨機 bytes，確認 mapping
3. 只對通過 colorization 確認的位置做 I2S mutation

這讓 colorization 的成本可控——不是每次執行都做，而是對 queue 裡有潛力的 input 做一次。

### RTN（Routine-level）CmpLog

除了 instruction-level 的 `icmp`，AFL++ 也 hook 了函式層級的比較：

```
afl-llvm-cmplog-routines.so.cc 處理：
- strcmp, strncmp, strcasecmp
- memcmp, bcmp
- strstr, memmem
```

這些函式的 hook 把整個字串/記憶體塊作為 operand 記錄，讓 fuzzer 能處理任意長度的 magic bytes。

### REDQUEEN 的延伸：checksum bypass

當比較的形式是 `f(x) == k` 且 `f` 是 checksum（如 CRC）時，REDQUEEN 嘗試識別並 patch：
- 偵測 comparison 的一側是否隨 input 線性變化
- 如果是，反推出讓比較成立的 input 修改

AFL++ 的 `cmp_extend_encoding()` 實作了部分這個邏輯，包括：
- 直接值（identity）
- Byte-swapped 值
- 取反值
- 常見的簡單編碼變換

---

## 動手練習

### 練習 1：驗證 CmpLog 的效果

```bash
# 建立一個有 magic bytes 的 target
cat > magic_target.c << 'EOF'
#include <stdio.h>
#include <string.h>
#include <stdint.h>

int process(const char *buf, size_t size) {
    if (size < 8) return 0;
    if (memcmp(buf, "FUZZME!\x42", 8) == 0) {
        if (size > 10 && buf[8] == 0xde && buf[9] == 0xad) {
            // 模擬 bug
            volatile int *p = (int*)0;
            *p = 1;
        }
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    char buf[1024];
    size_t n = fread(buf, 1, sizeof(buf), f);
    fclose(f);
    process(buf, n);
    return 0;
}
EOF

# 建兩個 binary
afl-clang-fast -o magic_target magic_target.c
AFL_LLVM_CMPLOG=1 afl-clang-fast -o magic_target_cmplog magic_target.c

# 建 seed
mkdir -p seeds && echo "aaaaaaaa" > seeds/seed1

# 不用 CmpLog（對照組）
timeout 120 afl-fuzz -i seeds/ -o out_nocmplog/ -- ./magic_target @@ &

# 用 CmpLog
timeout 120 afl-fuzz -c ./magic_target_cmplog -i seeds/ -o out_cmplog/ -- ./magic_target @@ &

# 120 秒後比較兩組的 paths found
```

### 練習 2：觀察 CmpLog SHM 的內容

在 AFL++ source 裡，`afl-showmap` 加上 `-c` 可以顯示 cmplog 資訊（需要自行查文件確認當前版本的 flag）。

另一個方法：加 `AFL_DEBUG=1` 讓 afl-fuzz 輸出 cmplog 相關的 debug 訊息：

```bash
AFL_DEBUG=1 afl-fuzz -c ./target_cmplog -i seeds/ -o out/ -- ./target @@ 2>&1 | grep -i cmp
```

---

## 本章重點整理

- Coverage-guided fuzzing 對 magic bytes / multi-byte 比較幾乎無效；REDQUEEN 的 input-to-state correspondence 是根本解法，它不只知道「比較什麼」，還知道「對應 input 的哪個位置」。
- AFL++ 用 CmpLog 實作 REDQUEEN：需要額外編譯一個 `AFL_LLVM_CMPLOG=1` 的 binary，執行時用 `-c` flag 指定，afl-fuzz 在內部用雙 process 架構同時跑 coverage 和 comparison logging。
- CmpLog 讓 throughput 降低 20-30%，但對有大量 magic bytes 的 target 效果顯著；和 auto-dictionary 的關鍵差異是 CmpLog 知道 token 要插在 input 的**哪個 offset**。

---

## 自我檢核

1. 為什麼純 coverage-guided fuzzing 對 `memcmp(buf, "MAGIC", 5) == 0` 幾乎無效？為什麼 dictionary 只是部分解法？
2. Colorization 要解決什麼問題？它的核心操作是什麼？
3. 畫出 AFL++ CmpLog 的雙 process 架構：哪個 binary 負責什麼？兩個 binary 的差異在哪裡？
4. I2S（input-to-state correspondence）和純 dictionary mutation 的根本差異是什麼？舉一個具體場景說明差異。
5. 若你的 target 有 CRC32 checksum 驗證，CmpLog 能直接幫你繞過嗎？為什麼？

---

## 延伸閱讀

**REDQUEEN: Fuzzing with Input-to-State Correspondence（NDSS 2019）**
- 核心貢獻：提出 I2S 的形式化框架；colorization 演算法；在多個 real-world target 上驗證比純 coverage fuzzing 顯著提升覆蓋率
- 讀哪裡：Section 3（I2S 的形式化定義和 colorization 描述）、Section 4（實作細節）、Section 6（和 AFL、AFL+Dictionary 的對比實驗）
- 和本章關聯：CmpLog 就是這篇 paper 在 AFL++ 裡的工程實作，理解 paper 能幫你知道哪些場景 CmpLog 有效/無效

**AFL++ `docs/fuzzing_in_depth.md`**
- 核心貢獻：官方的 CmpLog 使用說明，包含 `-c` flag 的完整語法和已知限制
- 讀哪裡：搜尋 "cmplog" 段落
- 和本章關聯：補充本章沒提到的 edge case（如 cmplog 和 ASAN 的互動）

**AFL++ `src/afl-fuzz-cmplog.c`**
- 核心貢獻：實際的 I2S mutation 邏輯，包含 `cmp_extend_encoding()` 對各種編碼變換的嘗試
- 讀哪裡：完整讀一遍（約 800 行），重點看 `cmplog_exec_target()` 和 `rtn_fuzz()` 兩個函式
- 和本章關聯：把本章的架構圖對應到實際的 code flow

**SYMCC: Efficient Compiler-Based Symbolic Execution（USENIX Security 2020）**
- 核心貢獻：比較 CmpLog 和 symbolic execution 的設計哲學——SYMCC 也嘗試在編譯時插樁做路徑約束求解，和 CmpLog 的 lightweight I2S 形成對比
- 讀哪裡：Introduction 和 Section 2（motivation），不需要讀完整篇
- 和本章關聯：幫助你理解 CmpLog 的 trade-off：它比 symex 快得多，但只解決「比較操作」這個子集

→ [下一章：Ch 16 — Persistent Mode：跑 10000 次，只 fork 一次](16-persistent-mode.md)
