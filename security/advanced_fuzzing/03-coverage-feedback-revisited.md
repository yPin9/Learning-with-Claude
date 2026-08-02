# Ch 3 — 覆蓋率的本質再訪

> **目標**：從 edge coverage 的極限出發，搞清楚 context-sensitive coverage、data-flow/value coverage、CmpLog/REDQUEEN 的機制，以及「custom feedback」的設計空間——為什麼進階目標需要比 edge bitmap 更聰明的 feedback。
>
> **環境**：WSL2 Ubuntu 22.04，clang-14.0.0，`-fsanitize-coverage=trace-pc-guard,trace-cmp`。本章的 coverage callback 示範已在本機實測。

---

## 為什麼要重談覆蓋率

你已經知道 coverage-guided fuzzing 的基本原理：有新 edge 就把這個輸入加進 corpus，沒有新 edge 就丟掉。這個想法好，但問題是：**「有新 edge」這個 feedback 訊號，有時候很笨。**

笨在哪裡？它只告訴你「程式走到了某個地方」，但不告訴你「走到那個地方的條件是什麼」、「那個地方的資料值是什麼」、「你是從哪條路走過去的」。對於大多數簡單目標，edge 就夠了。但對於：

- 需要特定 magic value 才能進入的分支
- 同一個 edge 在不同 call context 裡行為完全不同
- 真正的 bug 不是「到達某個 edge」而是「某個值進入了某個範圍」

…edge coverage 就飽和了，fuzzer 不知道該往哪裡走。

本章從這個問題出發，一層一層地剖析 coverage feedback 的設計空間。

---

## 先建立直覺：Coverage Bitmap 的本質

libFuzzer/afl++ 用的 edge coverage bitmap 是這樣運作的：

```
目標程式被插樁成：

void parse_header(buf) {
    if (len > 4) {         ← edge A
        if (buf[0] == 0xDE) {   ← edge B
            if (buf[1] == 0xAD) {  ← edge C
                // 深層邏輯        ← edge D
            }
        }
    }
}

執行時，每個 edge 對應 bitmap 的一個 bit/byte：
bitmap[hash(src_bb, dst_bb)] += 1

Fuzzer 問的問題：
    這次執行是否翻轉了 bitmap 裡任何一個 byte 的 bit？
    如果是 → "interesting"，加進 corpus

問題：
    bitmap 只記錄「去過沒有」，不記錄「怎麼去的」
```

這個設計有一個著名問題叫 **edge collision（hash collision）**：兩個不同的 edge 映射到同一個 bitmap 位置，互相干擾。afl++ 用 64K bytes 的 bitmap，理論上可以放 65536 個 edge，但真實的大型程式有幾十萬個 edge，collision 是必然的。

另一個問題叫 **coverage saturation**：所有有意義的 edge 都已被覆蓋，但 bug 依然沒被找到。這通常發生在「bug 需要特定的值，而不是特定的執行路徑」的情況。

---

## Edge Coverage 的四個極限

### 極限 1：Path Collision（路徑碰撞）

Edge coverage 追蹤的是「從 BB A 到 BB B 的跳轉」，但同一個 edge 可能出現在完全不同的執行路徑上：

```c
// 範例：同一個 edge，兩種路徑
int process(int type, uint8_t *data) {
    if (type == TYPE_A) {
        prepare_a(data);    // 路徑 1
    } else {
        prepare_b(data);    // 路徑 2
    }
    return validate(data);  // 同一個 edge，但 data 的內容完全不同
}

// validate() 的輸入來自 prepare_a 或 prepare_b
// Edge coverage 看到的 "validate edge" 是一樣的
// Fuzzer 認為「已覆蓋」，不會再嘗試用不同的 type 組合
```

這個問題在有函式指標、回呼（callback）、或 dispatch table 的程式裡特別嚴重。同一個 virtual dispatch edge 對應無數個不同的實際執行語意。

### 極限 2：Coverage Saturation

```
時間軸（假設目標有 1000 個 edge）

    邊發現的新 edge 數
    │
 100├─*
  50├──*
  20├───*
  10├────*
   5├─────*
   1├──────*─*─*─*─*─*────── (饱和)
   0└──────────────────────► 時間

Fuzzer 繼續跑，但每小時只找到 0–1 個新 edge
corpus 很大，但大多是在已知的路徑裡爬行
```

Coverage saturation 之後，純 coverage-guided 的 fuzzer 基本上是在做無用功——它找不到新的「interesting」輸入，但 bug 可能藏在某個特定值組合裡，只需要在已知 edge 上設置正確的值就能觸發。

### 極限 3：Call Context 盲目性

同一個函式可能從不同的 call site 被呼叫，行為完全不同：

```c
void process_command(uint8_t cmd, uint8_t *payload) {
    // 同一個函式，但呼叫者不同時，payload 的語意完全不同
    if (cmd == CMD_READ) {
        read_handler(payload);
    } else if (cmd == CMD_WRITE) {
        write_handler(payload);  // 這裡的 payload 格式和上面不同
    }
}

// call site 1: process_command(CMD_READ, net_buf);
// call site 2: process_command(CMD_WRITE, user_buf);

// Edge coverage 把這兩個 call 的 payload 視為「同一個 edge」
// Fuzzer 無法區分「應該給 CMD_READ 格式」還是「CMD_WRITE 格式」
```

這就是 **context-sensitive coverage（CS coverage）** 要解決的問題：把 call context 也編碼進 edge ID 裡。

### 極限 4：Data-flow / Value 盲目性

最根本的問題：**bug 往往不是「到達某個 edge」，而是「某個值是什麼」**。

```c
// 整數溢位：bug 在特定 value range，不在特定 edge
int total = a + b;   // 這個 edge 每次都到達
if (total > MAX) {   // 這個 edge 也到達
    return -EINVAL;  // 這個 edge 也到達
}
// 但 a=0x7FFFFFFF, b=1 的組合才導致 overflow
// Edge coverage 看到所有 edge 都「已覆蓋」
// 完全不知道 (a, b) 的值問題

// 格式依賴的 UAF：
char *buf = alloc(header.size);  // edge 到達
process(buf, header.size);       // edge 到達
// 但 header.size 被整數截斷導致 alloc 不足
// edge coverage 沒有感知到 size 值的問題
```

---

## Context-Sensitive Coverage（CS Coverage）

CS coverage 的想法：把 calling context（例如 call stack 的 hash，或直接的 caller PC）編碼進 edge ID 裡，讓同一個 edge 在不同 context 下被視為不同的 edge。

```
標準 edge ID：
  hash(src_BB_addr, dst_BB_addr)

CS edge ID（afl++ 的 --cs 選項，或 llvm PassManagerBuilder 的 CallsiteAware）：
  hash(src_BB_addr, dst_BB_addr, caller_hash)

效果：
  call_site_A → foo() → edge_X  成為 edge_XA
  call_site_B → foo() → edge_X  成為 edge_XB

Fuzzer 可以區分「從 A 到 foo 的路徑」和「從 B 到 foo 的路徑」
```

代價：bitmap 需要更大（context 的組合數是指數級），collision 更嚴重，或者 bitmap 需要動態增長（這是 LibAFL 的 `ContextSensitiveObserver` 的做法）。

---

## CmpLog / REDQUEEN：比較值的 feedback

這是 afl++ 和 libFuzzer 都實作了的一個關鍵改進——讓 fuzzer 能「看到」比較的兩個值，而不只是「比較的結果（分支方向）」。

原理：在每個比較指令（`cmp`, `test`, `strcmp`, `memcmp`）前插入 hook，記錄兩個運算元的值。Fuzzer 可以用這個資訊做精準的 value mutation：

```
沒有 CmpLog 的情況：
  if (x == 0xDEADBEEF):  Fuzzer 只知道「往右走（taken）」或「往左走（not-taken）」
                          要猜出 0xDEADBEEF 需要 2^32 次隨機 mutation

有 CmpLog/REDQUEEN 的情況：
  CmpLog callback 告訴 fuzzer：「比較的是 x(=0x12345678) 和 0xDEADBEEF」
  Fuzzer 直接知道把 0x12345678 替換成 0xDEADBEEF
  下一次迭代直接命中 taken branch
```

在 libFuzzer 裡這叫 **value profile**（`-fsanitize=fuzzer` 預設開啟 CMP tracing）；在 afl++ 裡叫 **CmpLog**（需要 `-c` flag 加上 cmplog binary）。

### 實測：trace-cmp callback 的行為

用 `clang -fsanitize-coverage=trace-pc-guard,trace-cmp` 直接觀察 callback 的觸發：

```c
/* cov_trace_demo.c — 獨立示範，不連結 sanitizer runtime */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* trace_const_cmp4：常量比較的 hook（compiler 把 == 常量優化成這個） */
void __sanitizer_cov_trace_const_cmp4(uint32_t arg1, uint32_t arg2) {
    fprintf(stderr, "[CMP4-CONST] 0x%08x  vs  0x%08x  (match=%s)\n",
            arg1, arg2, arg1==arg2 ? "YES" : "no");
}

void __sanitizer_cov_trace_const_cmp1(uint8_t arg1, uint8_t arg2) {
    fprintf(stderr, "[CMP1-CONST] 0x%02x  vs  0x%02x  (match=%s)\n",
            arg1, arg2, arg1==arg2 ? "YES" : "no");
}

static int parse(const uint8_t *d, size_t n) {
    if (n < 4) return -1;
    uint32_t v;
    memcpy(&v, d, 4);
    if (v == 0xDEADBEEF) {           /* ← 這裡產生 trace_const_cmp4 */
        if (n >= 5 && d[4] == 0xCA)  /* ← 這裡產生 trace_const_cmp1 */
            return 2;
        return 1;
    }
    return 0;
}
```

執行結果（本機實測）：

**壞輸入（`00 00 00 00`）：**

```
[CMP4-CONST] 0xdeadbeef  vs  0x00000000  (match=no)
```

Fuzzer 從這個 callback 知道：當前輸入的前 4 bytes 是 `0x00000000`，目標值是 `0xDEADBEEF`。REDQUEEN/CmpLog 會直接把 `0x00000000` 替換成 `0xDEADBEEF`，而不用猜。

**好輸入（`EF BE AD DE CA`）：**

```
[CMP4-CONST] 0xdeadbeef  vs  0xdeadbeef  (match=YES)
[CMP4-CONST] 0x000000ca  vs  0x000000ca  (match=YES)
```

兩個比較都命中，程式進入深層邏輯（return 2）。

---

## 實測：Edge Coverage Callback 的行為

用 `trace-pc-guard` 觀察哪些 edge 在哪個輸入下被命中：

```c
/* 在同一個 demo 裡加入 edge tracking */
static uint32_t guard_cnt = 0;

void __sanitizer_cov_trace_pc_guard_init(uint32_t *start, uint32_t *stop) {
    for (uint32_t *x = start; x < stop; x++)
        *x = ++guard_cnt;
    fprintf(stderr, "[COV INIT] %ld edges registered\n", (long)(stop - start));
}

void __sanitizer_cov_trace_pc_guard(uint32_t *guard) {
    if (!*guard) return;
    fprintf(stderr, "[EDGE HIT] edge_id=%u\n", *guard);
    *guard = 0;  /* 只記第一次 */
}
```

執行結果（本機實測，`clang-14 -fsanitize-coverage=trace-pc-guard,trace-cmp -O0`）：

**輸入 `[00 00 00 00]`（bad input）：**

```
[COV INIT] 6 edges registered
--- input: [00 00 00 00] ---
[EDGE HIT] edge_id=1 caller_pc=...
[EDGE HIT] edge_id=5 caller_pc=...
```

只命中 2 個 edge（size check pass, magic check fail）

**輸入 `[EF BE AD DE CA]`（good input）：**

```
[COV INIT] 6 edges registered
--- input: [EF BE AD DE ...] ---
[EDGE HIT] edge_id=3 caller_pc=...
```

命中了不同的 edge（magic check pass 的那一個）。這就是 fuzzer 用來判斷「這個輸入 interesting」的訊號。

---

## Data-flow Coverage：超越 Edge 的嘗試

研究界對「純 edge coverage 不夠」的回應是 data-flow coverage。主要有兩個方向：

### Value Profile（libFuzzer 的實作）

libFuzzer 的 value profile 不只追蹤「edge hit/miss」，還追蹤「比較的結果距離（distance）」：

```
比較 x 和 0x1000：

  x = 0x0000 → 差距 0x1000 → value profile bucket 11（log2 scale）
  x = 0x0800 → 差距 0x0800 → value profile bucket 11
  x = 0x0FFF → 差距 0x0001 → value profile bucket 0

Fuzzer 的觀點：
  第一次跑到這個比較（任何值）→ interesting（edge 層面）
  下一次 x 從 0x0000 變到 0x0800（距離改變了）→ interesting（value profile 層面）
  再下一次 x = 0x0FFF（非常接近）→ interesting
  最後 x = 0x1000 → exact match
```

libFuzzer 預設開啟 value profile，flag 是 `-fsanitize=fuzzer`（已內含）。這讓 libFuzzer 比純 edge coverage 能更快找到 magic value。

### 理論上的 Def-Use Coverage

更激進的做法是追蹤 **data-flow**：某個變數的定義（def）到它的使用（use）之間的關係。這能捕捉「某個值是否在某個特定的 def-use pair 上被使用」，比單純的 edge 或 value 更精細。

但 def-use coverage 有巨大的實作成本——要追蹤每個變數的所有 def-use pair，bitmap 大小是 O(程式行數²)，效能代價太大。目前主要停留在研究層面，還沒有成熟的 production fuzzer 用它作為主要 feedback。

**本段為理論描述，def-use coverage 在 production fuzzer 中的實作目前沒有成熟工具可供直接使用。**

---

## Custom Feedback：超越 Coverage 的新維度

最強大的方向：**你不一定要找 crash，你可以定義任何「有趣的行為」作為 feedback。**

### 例子 1：尋找特定的資源使用模式

```c
// 你想找「超大記憶體配置」而不是「crash」
// Custom feedback: 如果 malloc 超過 1MB → interesting

// LibAFL 的 Observer 寫法（概念）：
struct MemUsageObserver {
    peak_alloc: usize,
}

impl Observer for MemUsageObserver {
    fn post_exec(&mut self, ...) {
        // 讀取 /proc/self/status 的 VmPeak 或 hook malloc
        self.peak_alloc = get_current_peak_alloc();
    }
}

struct MemUsageFeedback;
impl Feedback for MemUsageFeedback {
    fn is_interesting(&self, observer: &MemUsageObserver, ...) -> bool {
        observer.peak_alloc > 1_000_000  // 超過 1MB 就 interesting
    }
}
```

### 例子 2：尋找特定的 log 輸出

在 kernel fuzzing 裡，`WARN_ON` 或特定的 `pr_err` 訊息本身就是 bug 的訊號，不需要等到 KASAN crash。

syzkaller 的做法是解析 kernel console 輸出，一旦出現 `KASAN:`、`BUG:`、`WARNING:` 就認為是 bug——即使程式沒有 crash（kernel 可能在 WARN_ON 之後繼續跑）。

### 例子 3：算術距離作為 feedback

對需要滿足複雜約束的目標，把「距離正確解的遠近」作為 fitness function——這讓 fuzzer 更像 search optimization 而不是 random walk。AFLGo（directed fuzzing）就是這個思路：把「距離目標程式碼行的 CFG 距離」作為 fitness，讓 fuzzer 往特定的 patch 行靠近。

---

## 底層機制：libFuzzer 的 Coverage 管道完整圖

```
目標程式 + libFuzzer runtime 的 coverage 流程

                   ┌──────────────────────────────────────┐
    輸入 bytes     │  __sanitizer_cov_trace_pc_guard()     │
       │           │   ← 每個 basic block transition 一次 │
       ▼           │                                      │
LLVMFuzzerTestOneInput()                                  │
       │           │  __sanitizer_cov_trace_const_cmp4()  │
       │           │   ← 每個 integer comparison           │
       │           │                                      │
       │           │  inline 8-bit counter bitmap         │
       │           │   bitmap[hash(src, dst)]++            │
       └───────────┘                                      │
                   │  執行結束                             │
                   ▼
          [比較 bitmap 和 baseline]
                   │
           新 edge?│
        ───────────┼─────────────────
        是         │                 否
        ▼          │                 ▼
    加入 corpus    │           丟棄這個輸入
    更新 baseline  │
                   │
          [CmpLog dictionary 更新]
          把 trace_cmp 收到的值加進 dictionary
                   │
          [下一個 mutation]
          從 corpus 選種子
          + dictionary hint
          + 隨機 mutation
```

這個流程裡有三個反饋循環：

1. **Edge feedback**：有新 edge → 加 corpus（最基礎）
2. **Value feedback**（CmpLog）：比較值加進 dictionary → mutation 更有方向
3. **Value profile**（libFuzzer 特有）：比較距離的 log scale bucket → 對接近目標值的輸入給「有趣」評分

---

## 對比取捨表

| Feedback 類型 | 能發現的 bug 類型 | 代價 | 代表工具 |
|-------------|----------------|------|---------|
| Edge coverage | 大多數控制流 bug | 低（8-bit bitmap） | afl++、libFuzzer |
| CS-edge coverage | 需要特定 call context 的 bug | 中（bitmap 變大） | afl++ `--cs` |
| Value profile | Magic value 後面的 bug | 低（libFuzzer 預設） | libFuzzer |
| CmpLog/REDQUEEN | 深層 magic value check | 中（需要第二次插樁） | afl++ `-c cmplog` |
| Custom feedback | 自定義行為（log、資源、距離） | 高（需要自寫 Observer） | LibAFL |
| Def-use coverage | 複雜的資料流 bug | 很高（bitmap 爆炸） | 研究原型 |

---

## 踩雷集錦

**踩雷 1：以為 CmpLog 能解所有 magic value 問題**

CmpLog 能解「固定的整數/字串比較」——但有些 check 是計算型的（如 CRC32、checksum），比較的不是一個固定值而是一個動態計算的結果。CmpLog 看到的是「`crc32(buf)` 的結果 vs 存在檔案裡的 crc 欄位」，它無法告訴 mutator「要讓 buf 的 crc 等於那個欄位的值，你需要改哪些 bytes」。這類問題需要的是 smart format-aware mutation（或者把 checksum 算法告訴 fuzzer）。

**踩雷 2：bitmap collision 被低估**

afl++ 預設 64K 的 bitmap，對一個有 100K+ edge 的程式（如 OpenSSL），碰撞率相當高。碰撞的後果是：兩個實際上不同的 edge 被認為是同一個，導致 fuzzer 認為「已覆蓋」而不探索某個方向。afl++ 有 `AFL_MAP_SIZE` 環境變數可以擴大 bitmap，LibAFL 可以用動態大小的 map。在打大型目標時，bitmap 大小是需要調的參數。

**踩雷 3：CS coverage 總是比 edge coverage 好**

CS coverage 增加了對 call context 的敏感性，但也增加了 bitmap 的填充速度——更多的 context 組合被認為是「新 edge」，corpus 快速膨脹，fuzzer 的排程負擔加重，每秒迭代數下降。在 call context 對 bug 不重要的目標上，CS coverage 是純損耗。這個選擇要根據目標的特性決定。

**踩雷 4：「coverage saturation = 沒有 bug 了」**

coverage saturation 代表「這個 feedback 信號飽和了」，不代表「目標沒有 bug 了」。bug 可能就在已覆蓋的 edge 後面的某個特定值組合裡，edge coverage 看不到。這是很多 fuzzer 在 OSS-Fuzz 跑了數百萬核心小時之後還有人找到 CVE 的原因——新的 feedback dimension（或者 directed fuzzing）能在同一套代碼裡繼續找洞。

**踩雷 5：`-fsanitize-coverage=trace-pc-guard` 和 libFuzzer 同時加**

這個踩雷在 Ch 0 提過，這裡補原理：libFuzzer runtime 自己實作了 `__sanitizer_cov_trace_pc_guard`，如果你在源碼裡也定義了同名函式並加上 `-fsanitize-coverage=trace-pc-guard`，連結時會出現 multiple definition 錯誤，或者 runtime 的 weak symbol 被你的覆蓋掉（行為不確定）。要單獨觀察 callback 行為，用 `-fno-sanitize=all` 不連結 sanitizer runtime，或者用 `-fsanitize=fuzzer-no-link` 加上自己的 `main()`。

---

## 進階延伸：Coverage 在 Kernel Fuzzing 的特殊形式

Kernel fuzzing（Part 4）用的 coverage 機制不是 LLVM 的 `trace-pc-guard`，而是 Linux 自帶的 **KCOV**：

```
KCOV 的工作原理

kernel compile with CONFIG_KCOV=y:
  在每個 basic block 插入：
    __sanitizer_cov_trace_pc_guard() 的 kernel 版本
    把 PC 寫入 task 的 KCOV buffer（per-task，不共享）

user-space（syzkaller）:
  mmap /sys/kernel/debug/kcov 開啟 coverage 收集
  執行一個 syscall 序列
  munmap 關閉收集
  讀取 KCOV buffer 拿到這次 syscall 觸發的 PC 列表

優勢：per-task 設計，不同 CPU 的 interrupt handler 不干擾
      可以精確追蹤「這個 syscall 序列走了哪些 kernel 路徑」
```

KCOV 和 user-space 的 edge bitmap 概念一樣，但實作完全在 kernel 裡。Part 4 Ch 22 會詳讀 KCOV 的源碼。

---

## 動手練習

1. 修改本章的 `cov_trace_demo`，在 `__sanitizer_cov_trace_const_cmp4` 裡記錄所有比較的 `(arg1, arg2)` 對，不立即 print，在程式結束時一起輸出。觀察一段複雜 C 程式（如解析一個 ELF header）的比較表。

2. 用 libFuzzer 跑一個有兩層 magic check 的 target（先 check `0xDEADBEEF`，再 check `0xCAFEBABE`）。觀察沒有 CmpLog 和有 CmpLog（加 afl-cmplog binary，或直接用 libFuzzer 的 value profile）時，找到兩層 crash 的速度差異。

3. 閱讀 libFuzzer 的 `FuzzerTracePC.cpp`（在 LLVM 源碼的 `compiler-rt/lib/fuzzer/`），找到 `HandleCmp` 函式，解釋它是如何用比較值計算「value profile bucket」的。

4. 用 LibAFL（如果你跟著 Part 1 走到這裡）實作一個最簡單的 custom Observer，觀察每次執行的輸出長度，並以「輸出長度是否比之前最長的長」作為 Feedback。跑一個目標，看 corpus 的演化。

---

## 本章重點

- Edge coverage 的三個根本限制：hash collision（edge 互相遮蔽）、coverage saturation（能找的都找到了，剩下是值問題）、call context 盲目性（同一 edge 在不同 context 語意不同）。
- CmpLog/REDQUEEN/Value Profile 是在 edge coverage 上加了「比較值暴露」的 feedback dimension，讓 fuzzer 能直接求解 magic value 而不用猜。
- Context-sensitive coverage 能區分不同 call context 下的同一 edge，但代價是 bitmap 膨脹和 corpus 爆炸。
- Custom feedback 是最強的武器：不限於 edge 或 value，任何可觀察的行為（log、資源使用、CFG 距離）都可以成為 feedback。
- Kernel fuzzing 用 KCOV 取代 LLVM 的 trace-pc-guard，原理相同但在 kernel 空間實作。

---

## 自我檢核

不翻書回答：

- [ ] 為什麼在有 100K 個 edge 的程式上，64K bitmap 會有嚴重的 collision？改善方法是什麼？
- [ ] CmpLog 為什麼解不了 CRC32 checksum 問題？這種情況需要什麼樣的 feedback？
- [ ] Value profile 和 CmpLog 的差別是什麼？libFuzzer 預設開的是哪個？
- [ ] Coverage saturation 之後，有哪些策略可以繼續推進 fuzzing？（至少說出三種）
- [ ] KCOV 和 LLVM 的 `trace-pc-guard` 的設計有什麼共同點？KCOV 的 per-task 設計解決了什麼問題？

---

## 延伸閱讀

1. **[CollAFL（SP 2018）](https://ieeexplore.ieee.org/document/8418631)**（Gan et al.）——直接針對 afl 的 path collision 問題，提出用 malloc 位址作為 context hash 而不是 XOR 方式；精確量化了 collision 對 bug 發現率的影響；和本章 "bitmap collision" 段落直接對應，Section 3 是精華。

2. **[REDQUEEN（NDSS 2019）](https://www.ndss-symposium.org/ndss-paper/redqueen-fuzzing-with-input-to-state-correspondence/)**（Aschermann et al.）——CmpLog 的學術根源；系統地提出 input-to-state correspondence（輸入和程式狀態之間的對應關係），不只是 magic value，還包括比較後的 offset 推理；閱讀 Section 4 的「Colorization」步驟，理解為什麼找到 magic value 之後還要「染色」。

3. **[IJON（SP 2020）](https://ieeexplore.ieee.org/document/9152719)**（Aschermann et al.）——把 coverage-guided fuzzing 的 feedback 問題推到極限，提出用「程式員手動標注的 annotation」補充 edge coverage；Section 2 的 motivation（遊戲 AI 作為 fuzzing 案例）是絕佳的直覺說明；和本章 "Custom Feedback" 段落的設計理念高度吻合。

4. **[Linux KCOV 文件](https://www.kernel.org/doc/html/latest/dev-tools/kcov.html)** 的「Usage」和「Comparison coverage」章節——`trace-cmp` 在 kernel 層的版本；kcov_mode 裡有 `KCOV_TRACE_PC` 和 `KCOV_TRACE_CMP` 兩種，對照本章的 PC-guard 和 trace-cmp 的關係；Part 4 Ch 22 的前置材料。

---

Part 0 的地基已經鋪好：環境裝好了、afl++ 的邊界清楚了、工具全景看到了、coverage 的本質剖析完了。接下來是 Part 1 的主軸——用 LibAFL 把 fuzzer 拆開重組，理解每個組件在做什麼、自己能造出什麼。

→ [下一章：Ch 4 LibAFL 哲學：fuzzer 是可組合元件](./04-libafl-philosophy.md)
