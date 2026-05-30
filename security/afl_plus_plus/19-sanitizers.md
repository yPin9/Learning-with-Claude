# Ch 19 — Sanitizers：ASan、UBSan、MSan 與 AFL++ 整合

> **目標**：理解 ASan、UBSan、MSan 各自偵測什麼問題，以及如何正確與 AFL++ 整合（包括為什麼不衝突）。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼需要這個？

AFL++ 靠 coverage feedback 找到「讓程式走到新路徑的 input」。但找到新路徑不代表找到 bug——程式可能走到一個有 heap overflow 的區域，但 overflow 只是寫壞了旁邊的資料，不立即崩潰（silent corruption），AFL++ 永遠不知道這是個 bug。

Sanitizer（消毒劑，取 sanitize = 清除污染之意）解決這個問題：它在 compile time 插入額外的 instrumentation（插樁代碼），在 runtime 主動監控記憶體存取和行為，一旦發現問題就立即殺死程式並報告詳細原因。

這個組合的威力：AFL++ 負責找到「能抵達 bug 的 input」，Sanitizer 負責把原本沉默的 bug 變成「會 crash 的 bug」。沒有 Sanitizer，AFL++ 找到的 crash 只是冰山一角。

**歷史**：ASan 由 Google 工程師（主要是 Kostya Serebany 和 Derek Bruening）開發，2012 年在 USENIX ATC 發表，現在是 Clang/GCC 的標準功能。UBSan 稍晚，MSan 主要由 LLVM 團隊維護。三者合稱 "The Sanitizer Trio"，是現代 C/C++ 安全測試的標配。

## 先建立直覺

沒有 Sanitizer 的世界裡，一個 heap overflow 大概率是這樣的：

```
program writes 4 bytes past the end of malloc'd buffer
  → overwrites metadata of next heap chunk
  → program continues normally (no crash)
  → 100 instructions later: malloc() reads corrupted metadata
  → crash at a completely unrelated location
  → AFL++ sees crash, but crash input != trigger input
```

加上 ASan 之後：

```
program writes 4 bytes past the end of malloc'd buffer
  → ASan shadow memory check fires IMMEDIATELY
  → SIGABRT with full stack trace pointing to the exact write
  → AFL++ stores this crash, the input IS the trigger
```

把 Sanitizer 想成「把所有的記憶體越界存取都變成立即可見的地雷」。

## 三種主要 Sanitizer

### ASan（AddressSanitizer，位址消毒劑）

**偵測範圍**：
- Heap buffer overflow（堆積緩衝區溢位）
- Stack buffer overflow（堆疊緩衝區溢位）
- Global buffer overflow（全域變數溢位）
- Use-after-free（釋放後使用）
- Double-free（重複釋放）
- Use-after-return（返回後使用局部變數的指標）

**原理：影子記憶體（Shadow Memory）**

ASan 的核心機制是影子記憶體映射：

```
原始記憶體（Application Memory）
┌──────────────────────────────────────────┐
│  每 8 bytes 的應用記憶體...               │
└──────────────────────────────────────────┘
         ↓ 每 8 bytes 對應 1 byte 影子
┌──────────────────────────────────────────┐
│  影子記憶體（Shadow Memory）              │
│  0x00 = 全部 8 bytes 合法可存取           │
│  0x01-0x07 = 前 N bytes 合法，其餘紅區    │
│  0xFA = heap redzone（malloc 周圍的毒區） │
│  0xFD = heap freed（已釋放的記憶體）      │
│  0xFF = stack redzone                    │
└──────────────────────────────────────────┘
```

每次記憶體存取前，ASan 插入一段 instrumentation code：

```c
/* 原始程式碼 */
*ptr = value;

/* ASan 插樁後（簡化版） */
shadow_addr = (ptr >> 3) + SHADOW_OFFSET;
if (*shadow_addr != 0) {
    asan_report_error(ptr, "heap-buffer-overflow");
    abort();
}
*ptr = value;
```

**效能 overhead**：CPU 約 2x 慢，記憶體約 2-3x 多（需要額外的影子記憶體）。

**觸發行為**：`SIGABRT`（signal 6），輸出詳細 stack trace 到 stderr。

---

### UBSan（UndefinedBehaviorSanitizer，未定義行為消毒劑）

**偵測範圍**：
- 帶號整數溢位（signed integer overflow）
- 除以零（division by zero）
- 空指標解引用（null pointer dereference）
- 未對齊記憶體存取（misaligned pointer dereference）
- 越界陣列存取（out-of-bounds array index，有靜態大小的情況）
- 無效的 enum 值

**關鍵設定**：預設 UBSan 只印錯誤訊息，不崩潰（讓程式繼續跑）。AFL++ 需要崩潰才能收集 crash。解決方法：

```bash
# 方法 1：讓 UBSan 直接 trap（undefined behavior → SIGILL）
-fsanitize=undefined -fsanitize-trap=all

# 方法 2：讓 UBSan 呼叫 abort()
-fsanitize=undefined -fno-sanitize-recover=all
```

AFL++ 提供更簡單的方式：`AFL_USE_UBSAN=1` 會自動處理這些 flags。

**效能 overhead**：相對輕量，CPU 約 1.5x 慢，記憶體幾乎不增加。

---

### MSan（MemorySanitizer，記憶體消毒劑）

**偵測範圍**：
- 使用未初始化的記憶體（uninitialized memory read）
- 傳遞未初始化值給系統呼叫

**原理**：與 ASan 類似，也是影子記憶體，但追蹤的是「初始化狀態」而不是「合法存取範圍」。每個 byte 對應一個「是否已初始化」的 bit。

**最貴的 Sanitizer**：CPU overhead 約 3-5x，記憶體 2x。原因是每次 store 都要設 shadow bit，每次 load 都要檢查 shadow bit，指令數幾乎加倍。

**重要限制**：MSan 只在純 Clang 編譯的二進位中有效。如果你的 target 鏈接了沒有 MSan 插樁的函式庫，會有大量誤報（false positive）。

---

## 底層機制：為什麼 AFL++ + ASan 不衝突？

這是最常見的誤解，值得詳細說明。

**錯誤認知**：「AFL++ 用 SHM（共享記憶體）存 coverage bitmap，ASan 用 shadow memory 做記憶體映射，兩者都搶記憶體，會衝突。」

**實際機制**：

```
AFL++ 的 SHM（共享記憶體）：
  ┌─────────────────────────────────┐
  │  coverage bitmap（64KB 或更大）  │
  │  通過 shmget() / shmat() 分配   │
  │  ID 存在環境變數 __AFL_SHM_ID   │
  └─────────────────────────────────┘
  → 這是 POSIX SHM，有固定的 SHM ID
  → 位址由 OS 的 shmat() 決定，每次可能不同

ASan 的 Shadow Memory：
  ┌─────────────────────────────────┐
  │  shadow memory（通常 16TB 虛擬） │
  │  通過 mmap() 分配               │
  │  位址範圍固定（由 ASan 預設值）  │
  └─────────────────────────────────┘
  → 這是 anonymous mmap，不是 SHM
  → 位址範圍固定在特定的虛擬記憶體區段
```

兩者使用的是**不同的記憶體分配機制**，不競爭。

**但有一個真實的問題**：

ASan 在程式啟動時預先 mmap 一塊巨大的虛擬記憶體（shadow memory），這可能和 AFL++ 嘗試 attach 的 SHM 位址衝突。AFL++ 偵測到 ASan 存在時，會**自動調整 SHM 的映射位址**，避開 ASan 的影子記憶體區域。這個邏輯在 AFL++ 的 `afl-fuzz.c` 的 `setup_shm()` 函式中。

```
AFL++ 啟動流程（有 ASan）：
  1. afl-fuzz 偵測 target 是否有 ASan symbol（__asan_init）
  2. 若有，計算 ASan shadow memory 的位址範圍
  3. setup_shm() 選擇不與 shadow memory 衝突的位址
  4. 把 SHM ID 寫入 __AFL_SHM_ID 環境變數
  5. fork() target，target 的 AFL++ runtime 讀 __AFL_SHM_ID，attach SHM
```

---

## 範例一：ASan Build 與 Fuzz

```bash
# 步驟 1：用 AFL++ 的 ASan-aware clang 編譯
# AFL_USE_ASAN=1 會自動加上 -fsanitize=address 和必要的 flags
AFL_USE_ASAN=1 afl-clang-fast -o target_asan target.c

# 步驟 2：啟動 fuzzer（AFL_USE_ASAN=1 也要在 fuzz 時設定）
AFL_USE_ASAN=1 afl-fuzz -i seeds/ -o out_asan/ -- ./target_asan @@

# 步驟 3：確認 crash 的 signal 編號
# ASan 的 crash 是 SIGABRT（signal 6），不是 SIGSEGV（11）
ls out_asan/default/crashes/
# 檔名格式：id:000000,sig:06,src:000000,...
#                         ↑ sig 06 = SIGABRT = ASan triggered
```

---

## 範例二：驗證 ASan 真的有效

先寫一個有 heap overflow 的目標：

```c
/* heap_overflow_target.c */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void process(const char *input, size_t len) {
    /* 故意只 malloc len-4 bytes，然後寫 len bytes */
    char *buf = malloc(len - 4);
    if (!buf) return;
    memcpy(buf, input, len);  /* BUG: overflow by 4 bytes */
    printf("processed: %.8s\n", buf);
    free(buf);
}

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    rewind(f);
    if (sz < 8) { fclose(f); return 1; }
    char *data = malloc(sz);
    fread(data, 1, sz, f);
    fclose(f);
    process(data, sz);
    free(data);
    return 0;
}
```

不加 ASan 的情況：

```bash
gcc -O0 -o target_no_asan heap_overflow_target.c
# 建立一個觸發 overflow 的 input
python3 -c "import sys; sys.stdout.buffer.write(b'A' * 20)" > /tmp/trigger

./target_no_asan /tmp/trigger
# 輸出：processed: AAAAAAAA
# 沒有 crash！overflow 了 4 bytes，但沒有立即可見的效果
```

加 ASan 的情況：

```bash
gcc -O0 -fsanitize=address -o target_asan heap_overflow_target.c

./target_asan /tmp/trigger
# ==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602000000014
# at pc 0x... bp 0x... sp 0x...
# WRITE of size 20 at 0x602000000014 thread T0
#     #0 0x... in process heap_overflow_target.c:9
#     #1 0x... in main heap_overflow_target.c:23
# ...
# SUMMARY: AddressSanitizer: heap-buffer-overflow heap_overflow_target.c:9 in process
# Abort trap: 6
```

ASan 精確指出：檔案名、行號、overflow 的大小。這就是 AFL++ 在 `crashes/` 目錄裡能存到的 input 所觸發的 bug。

---

## 對比與取捨

| | 無 Sanitizer | ASan | UBSan | MSan |
|---|---|---|---|---|
| **偵測範圍** | 只有立即崩潰的 bug | 記憶體越界、UAF、double-free | 整數溢位、UB、null deref | 未初始化記憶體讀取 |
| **CPU overhead** | 1x | ~2x | ~1.5x | ~3-5x |
| **記憶體 overhead** | 1x | 2-3x | ~1x | ~2x |
| **False positive** | N/A | 極少 | 偶有（正常 UB 被觸發） | 中等（需要完整插樁）|
| **與 ASan 相容** | — | — | 可同時用 | 不可同時用 |
| **適合跑多久** | 24 小時+ | 1-24 小時 | 24 小時+ | 幾小時（太貴）|
| **適用場景** | 高速 fuzzing 第一階段 | 找記憶體安全 bug | 找整數和 UB bug | 找初始化 bug（最挑剔）|

---

## 踩雷集錦

1. **ASan 和 MSan 不能同時用**：`-fsanitize=address,memory` 會導致 build 直接失敗，因為兩者都要控制影子記憶體，會衝突。如果要兩種都測，開兩個不同的 AFL++ instance，一個用 ASan，一個用 MSan。

2. **ASan 的 crash signal 是 6（SIGABRT），不是 11（SIGSEGV）**：如果你的 triage script 只抓 `sig:11` 的 crash，會漏掉所有 ASan 觸發的 crash。正確的做法是不過濾 signal，或同時抓 `sig:06` 和 `sig:11`。

3. **記憶體限制要調整**：ASan 會預先 mmap 巨大的虛擬記憶體（shadow memory），AFL++ 預設的 memory limit（`-m` 參數）如果設太低，程式在 ASan 初始化時就會被 AFL++ 殺死。最簡單的解法是 `afl-fuzz -m none`（取消記憶體限制），或設一個足夠大的值（例如 `-m 4096`，單位 MB）。

4. **MSan 需要所有函式庫都有插樁**：如果你的 target 鏈接了系統的 libc（沒有 MSan 插樁），MSan 會對 libc 函式的內部記憶體存取誤報。解法是用 MSan 插樁過的 libc（需要自行編譯），或接受一定程度的誤報。這是 MSan 實際使用複雜度高的主因。

5. **`AFL_USE_ASAN=1` 必須在編譯和執行時都設定**：編譯時沒設 → 二進位沒有 ASan 插樁，fuzzing 時設也沒用。執行時沒設 → AFL++ 不知道要調整 SHM 位址，可能發生位址衝突。兩個地方都要設。

---

## 進階：再往深一層

**多 instance 的 Sanitizer 策略**：

在 parallel fuzzing（下一章）中，不是每個 instance 都要跑 ASan（2x 慢意味著你用 8 個 core 但有效 throughput 等於 4 個）。一個常見策略：

```
4 個 instance 無 Sanitizer（高速，找覆蓋）
2 個 instance 加 ASan（偵測記憶體 bug）
1 個 instance 加 UBSan（偵測整數溢位）
1 個 instance 加 CmpLog（協助突破比較）
```

這樣把多樣性和 bug 偵測能力平衡起來。

**ASAN_OPTIONS 環境變數**：ASan 有大量可調整的選項：

```bash
# 讓 ASan 偵測到 bug 後立即 abort（不印 report，更快）
ASAN_OPTIONS=abort_on_error=1:fast_unwind_on_malloc=0 ./target @@

# 讓 ASan 偵測 use-after-return（預設關閉，因為改變了 ABI）
ASAN_OPTIONS=detect_stack_use_after_return=1 ./target @@

# 關閉 ASLR（有時候讓 ASan 更穩定）
# 需要 root 或 ptrace 權限
ASAN_OPTIONS=disable_coredump=0 ./target @@
```

**SanitizerCoverage 與 AFL++ 的整合**：AFL++ 的 afl-clang-fast 實際上使用 LLVM 的 SanitizerCoverage（PCGuard 模式）做 coverage instrumentation，和 ASan 的插樁是兩個獨立的 pass，不互相干擾。這是為什麼它們可以同時用的底層原因。

---

## 動手練習

1. **比較有無 ASan 的 crash 發現率**：
   - 準備一個已知有 heap overflow 的 target（可以用上面的範例，或 `libpng`、`libjpeg` 的舊版）
   - 不加 ASan 跑 1 小時，記錄 crash 數量
   - 加 ASan 跑 1 小時（相同 seeds、相同 AFL++ 版本）
   - 比較 crash 數量差異，以及 crash 的 bug 類型分布

2. **驗證 UBSan 的 integer overflow 偵測**：
   - 寫一個有 signed integer overflow 的 target（例如 `int x = INT_MAX; x += input_val;`）
   - 用 `-fsanitize=undefined -fno-sanitize-recover=all` 編譯
   - 確認 AFL++ 能找到觸發 overflow 的 input

3. **調查 ASan signal 類型**：
   - 用 ASan 跑任意 fuzzing target 30 分鐘
   - 用 `ls out/default/crashes/` 查看 crash 的 `sig:` 欄位
   - 統計 sig:06 和 sig:11 各佔多少比例，思考它們對應哪些 bug 類型

---

## 本章重點整理

- Sanitizer 是 compile-time instrumentation，把原本沉默的記憶體 bug（overflow、UAF、UB）變成立即崩潰並報告位置，大幅提升 AFL++ 的 bug 發現率
- ASan（heap/stack overflow、UAF）、UBSan（整數 UB、null deref）、MSan（未初始化讀取）各有不同偵測範圍；ASan 和 MSan 不能同時用；記憶體限制需要用 `-m none` 調整
- AFL++ 和 ASan 不衝突的原因：AFL++ 的 coverage bitmap 用 POSIX SHM 機制，ASan 的影子記憶體用 anonymous mmap，是不同的記憶體系統；AFL++ 會自動調整 SHM 位址避開 ASan 的影子記憶體範圍

## 自我檢核

1. ASan 使用什麼機制偵測 heap overflow？每 8 bytes 應用記憶體對應幾 bytes 的影子記憶體？
2. 為什麼 AFL++ 和 ASan 不衝突？AFL++ 的 coverage SHM 和 ASan 的 shadow memory 分別用什麼機制分配？
3. UBSan 預設偵測到 bug 時不崩潰，如何讓它崩潰（讓 AFL++ 能偵測）？
4. 如果你有 8 個 core 要做 parallel fuzzing，你會如何分配 Sanitizer 策略？
5. ASan 觸發的 crash 在 AFL++ 的 `crashes/` 目錄裡，signal 編號是多少？為什麼？

## 延伸閱讀

- **"AddressSanitizer: A Fast Address Sanity Checker"（Serebany et al., USENIX ATC 2012）**：核心貢獻：提出影子記憶體映射的設計，證明 2x overhead 是可接受的；重點讀第 3 節（shadow memory encoding）和第 4 節（stack and global variable checking）；和本章的 ASan 原理那節直接對應。

- **"SoK: Sanitizing for Security"（Szekeres et al., S&P 2013）**：核心貢獻：系統性分析各種 sanitizer 的威脅模型、覆蓋範圍和效能成本，提供選擇 sanitizer 的決策框架；讀表 1（Sanitizer 對比）和第 4 節；和本章的對比表格互補，提供更深的理論背景。

- **AFL++ `docs/fuzzing_in_depth.md`**（https://github.com/AFLplusplus/AFLplusplus/blob/stable/docs/fuzzing_in_depth.md）：核心貢獻：AFL++ 官方的 sanitizer 整合指南，包含所有 `AFL_USE_*` 環境變數的完整列表和注意事項；讀 "Sanitizers" 那一節；和本章的啟用方式那節是互補的實踐指南。

→ [下一章：Ch 20 — Parallel Fuzzing：多核 Campaign 設計](20-parallel-fuzzing.md)
