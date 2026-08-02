# Final Project — 真實開源目標端到端 fuzzing campaign

> **目標**：以產品安全工程師 / CVE hunter 的身份，對一個真實開源目標跑完整的 fuzzing campaign——從選目標、建 harness、跑 coverage-guided fuzzing、triage crash、root cause 分析、判斷可利用性，到寫出 CVE 等級的負責任揭露報告。把整門課的每個 Part 都接進同一個攻擊鏈，讓你確認「我不只是學過，我確實能端到端跑完」。

---

## 為什麼這個 Final 能驗收你學到的東西

讀完 48 章之後，你可能對各個工具都有感覺——LibAFL 組件拼法、syzlang 語法、Nyx snapshot、Fuzzilli mutation、SymCC 路徑展開——但這些知識是散的。從一個工具換到下一個工具時，你很容易失去「為什麼」的感覺，只剩下「怎麼做」。

這個 final project 反過來逼你問「為什麼」：

- 選目標時，你必須分析 attack surface 的形態（Part 0 Ch 1 的四道牆），再決定哪種 harness 架構合適
- 建 harness 時，你要決定要不要上結構感知 mutator（Part 2），要不要處理 stateful 初始化（Part 3）
- 跑起來之後，coverage 數字會告訴你 fuzzer 卡在哪裡——解法可能是文法、可能是 hybrid（Part 8）、可能是 directed（Part 8 Ch 43）
- Triage 時，你要分辨 dedup 標準、minimize 的正確方法（Part 9 Ch 45）
- Root cause 之後，你要套用 Ch 47 的 disclosure timeline 把找到的洞走完整個流程

沒找到 crash 不代表失敗——找到 fuzzer 卡死的邊界、給出分析並提出改進路線，同樣是這門課訓練的核心能力。

### 與各 Part 的對照

| 課程 Part | 在 Final 中的對應環節 |
|---|---|
| Part 0（afl++ 的四道牆 / fuzzer 全景） | 目標選擇與攻擊面分析（M1） |
| Part 1（LibAFL 元件化造 fuzzer） | harness 骨架、Executor / Feedback 選擇（M2） |
| Part 2（結構感知 / 文法 fuzzing） | 結構化 input 的 mutator 決策（M2） |
| Part 3（stateful / 協定 fuzzing） | 若目標有狀態初始化，harness 的 session 設計（M2） |
| Part 4（kernel / syzkaller）★ | kernel 支線選項（獨立 M-K 流） |
| Part 5（snapshot / Nyx） | 延伸挑戰：snapshot 加速（M8+） |
| Part 6（韌體 rehosting） | 延伸挑戰：韌體目標 rehost |
| Part 7（Fuzzilli / JS）| 延伸挑戰：JS 引擎支線 |
| Part 8（hybrid / SymCC / AFLGo） | coverage 停滯後的 hybrid 升級（M5 後） |
| Part 9（OSS-Fuzz / corpus / FuzzBench / CVE） | corpus 管理（M3）、triage（M4）、報告（M7） |

---

## 情境設定

你是一家公司的產品安全工程師，或獨立的 CVE hunter。你的工作週期是：

1. 挑一個有足夠攻擊面的開源目標
2. 建出能有效驅動它的 fuzzer
3. 跑足夠長的時間讓 coverage 飽和或 crash 出現
4. Triage、minimize、root cause
5. 判斷可利用性等級
6. 按 responsible disclosure 流程走完

這個週期和 Google Project Zero / bug bounty hunter 的日常工作是同一個——差別在規模。這個 final 讓你把整個週期跑一遍，哪怕只在一台 laptop 上跑 24 小時。

---

## 目標候選與選擇準則

### 候選 A：libxml2

**攻擊面**：XML/XPath/XPointer/DTD 解析，輸入格式化、結構深、狀態複雜（DTD 展開、entity substitution 可觸發遞迴）。  
**優勢**：有大量公開 harness 可參考（OSS-Fuzz 上就有），`xmlReadMemory()` 一行可切入，build 簡單（`./autogen.sh && make`），ASan/UBSan 友好。libprotobuf-mutator 的 XML proto 描述也有人寫好了。  
**已有 CVE 紀錄**：CVE-2022-29824（整數溢位）、CVE-2023-29469（heap use-after-free），說明仍有殘洞空間，但 OSS-Fuzz 已持續在跑——**你的 harness 必須覆蓋 OSS-Fuzz 沒打到的路徑**（如 XPath 的邊界條件、自訂 error handler 路徑）才有機會找到新洞。  
**建議 harness 型態**：libFuzzer + ASan，seed corpus 從 W3C 測試套件抽取，可加上 libprotobuf-mutator 的結構變異。

### 候選 B：YARA（yara / libyara）

**攻擊面**：規則解析器（YARA rule 是一個文法完整的 DSL）、掃描引擎（pattern matching、模組回呼、PE/ELF 解析器）。輸入有兩層——規則文字和被掃描的 blob——可以分開 fuzz 也可以一起打。  
**優勢**：build 極快（`./bootstrap.sh && ./configure && make`），libyara 有乾淨的 C API，`yr_rules_scan_mem()` 就是入口。規則解析路徑完全是 C parser，PE 解析路徑有大量手寫 offset 計算，是典型的 heap buffer overflow 溫床。  
**已有 CVE 紀錄**：CVE-2023-52779（整數溢位）等，但掃描引擎的模組（PE/ELF/dotNET/Mach-O）仍有大量 parser 程式碼基本沒被系統 fuzz 過。  
**建議 harness 型態**：libFuzzer，把輸入切成「規則區段 + 被掃描 blob」兩段，或寫兩個獨立 harness。seed corpus 抓 YARA 官方 rule repo。

### 候選 C：c-ares（DNS / 非同步解析）

**攻擊面**：DNS 回應封包解析（stateful：先發 query，收到 response 才觸發解析邏輯），`ares_parse_*_reply()` 函式群，涵蓋 A/AAAA/MX/TXT/SRV/NAPTR/SOA。  
**優勢**：純 C，build 簡單，解析路徑有大量手寫 TLV-style 的 pointer 算術，歷史上 CVE 不少（CVE-2020-8277、CVE-2023-31130）。  
**挑戰**：需要 stateful 初始化（建立 channel、模擬 query-response 配對），harness 設計比前兩個複雜，但剛好讓你練 Part 3 的 stateful harness 技巧。  
**建議 harness 型態**：直接呼叫 `ares_parse_*_reply()`（跳過網路層），把 fuzzer 的輸入當 DNS 回應封包餵進去。

### 選擇準則

| 考量 | libxml2 | YARA | c-ares |
|---|---|---|---|
| Build 難度 | 低 | 低 | 低 |
| harness 複雜度 | 低（stateless） | 中（雙輸入） | 中（stateful） |
| OSS-Fuzz 覆蓋程度 | 高（需找空白） | 中 | 中 |
| 結構感知收益 | 高（XML proto） | 高（rule DSL） | 中（DNS 封包格式） |
| 推薦給 | 第一次做 final | 想練文法 fuzzing | 想練 stateful harness |

**本文以 YARA（libyara）作為主線範例**，原因是：build 快、有兩層輸入提供設計選擇、PE 解析路徑是真實 CVE 溫床、OSS-Fuzz 覆蓋不算完整。切換到 libxml2 或 c-ares 的指令差異會在各 milestone 用括號標注。

---

## Milestone 總覽

```
M1  選目標 + build + attack surface 分析       （1–2 天）
M2  建 harness + seed corpus                   （2–3 天）
M3  跑 + 監控 coverage                        （3–7 天，可背景跑）
M4  triage crash（dedup / minimize）           （1 天）
M5  root cause（ASan / gdb）                  （1–2 天）
M6  判斷可利用性                              （0.5 天）
M7  寫報告 + responsible disclosure           （1 天）
```

---

## M1：選目標、Build、Attack Surface 分析

### 子目標

確認你選的目標可以在 WSL2 上乾淨 build、ASan instrumentation 可正常啟用、已初步勾勒出哪些程式碼路徑是值得 fuzz 的。

### 具體步驟

```bash
# YARA 主線
git clone https://github.com/VirusTotal/yara.git
cd yara
./bootstrap.sh
./configure CC=clang CFLAGS="-O1 -fsanitize=address,undefined -fno-omit-frame-pointer" \
            --disable-shared
make -j$(nproc)
```

Build 完後確認：

```bash
file libyara/.libs/libyara.a   # 應看到 ELF 64-bit archive
nm libyara/.libs/libyara.a | grep yr_rules_scan_mem  # 確認符號存在
```

Attack surface 分析——列出你要打的入口：

```
1. yr_compiler_add_string()   → 規則解析器（lexer + parser，大量 C 指標操作）
2. yr_rules_scan_mem()        → 掃描引擎（pattern match + 模組回呼）
3. yara/libyara/modules/pe/   → PE 模組（手寫 offset 計算，CVE 溫床）
4. yara/libyara/modules/elf/  → ELF 模組
5. yara/libyara/re/           → regex engine（Thompson NFA）
```

這步對照 Part 0 Ch 1「afl++ 的四道牆」：YARA 的 PE 解析路徑不是 stateful，但輸入格式（PE binary）有嚴格的 magic bytes / offset 結構，dumb mutation 效率低——需要結構感知（Part 2）。

### 產出物

- `build.sh`：可重現的 build 腳本（含 ASan flags）
- `attack_surface.md`：列出每個 harness 入口、對應的程式碼路徑、預期 bug class

### 驗收標準

- `make -j$(nproc)` 零 error 完成
- 執行 `yara/tests/` 下任一測試二進位，不出現 ASan false positive
- attack surface 文件涵蓋至少 3 個不同入口

---

## M2：建 Harness + Seed Corpus

### 子目標

寫出至少兩個 harness——一個打規則解析路徑、一個打掃描引擎（PE 輸入）——並準備 seed corpus。

### Harness 1：規則解析器（libFuzzer 介面）

**本段以 WSL2 / clang 12+ 實測可跑。**

```c
/* fuzz_rule_parser.c
 * 編譯：
 *   clang -O1 -fsanitize=address,fuzzer -fno-omit-frame-pointer \
 *         fuzz_rule_parser.c -I yara/libyara/include \
 *         -L yara/libyara/.libs -lyara -o fuzz_rule_parser
 *   執行：
 *   LD_LIBRARY_PATH=yara/libyara/.libs \
 *   ./fuzz_rule_parser -max_len=65536 -rss_limit_mb=2048 corpus_rules/
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <yara.h>

/* 全域一次性初始化 */
static YR_COMPILER *g_compiler = NULL;

__attribute__((constructor))
static void global_init(void) {
    yr_initialize();
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0) return 0;

    /* 每次 iteration 建新 compiler，避免狀態汙染 */
    YR_COMPILER *compiler = NULL;
    YR_RULES   *rules     = NULL;

    if (yr_compiler_create(&compiler) != ERROR_SUCCESS)
        return 0;

    /* 把 fuzzer 輸入當成 YARA rule 字串 */
    char *rule_str = (char *)malloc(size + 1);
    if (!rule_str) goto cleanup;
    memcpy(rule_str, data, size);
    rule_str[size] = '\0';

    /* 忽略 parse error：我們要的是讓 parser 在邊界條件崩潰，
     * 不是讓它優雅地拒絕壞輸入 */
    yr_compiler_add_string(compiler, rule_str, NULL);
    free(rule_str);

    /* 嘗試 compile，進一步走 code generation 路徑 */
    yr_compiler_get_rules(compiler, &rules);

cleanup:
    if (rules)    yr_rules_destroy(rules);
    if (compiler) yr_compiler_destroy(compiler);
    return 0;
}
```

### Harness 2：PE 掃描路徑（雙輸入切分）

對 PE 路徑，你需要一個固定的「能觸發 PE 模組」的 YARA rule，然後把 fuzzer 輸入當成 PE binary 餵給掃描引擎：

```c
/* fuzz_pe_scan.c
 * 編譯：（同上，換檔名）
 *   clang -O1 -fsanitize=address,fuzzer -fno-omit-frame-pointer \
 *         fuzz_pe_scan.c -I yara/libyara/include \
 *         -L yara/libyara/.libs -lyara -o fuzz_pe_scan
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <yara.h>

/* 強制觸發 PE 模組的最簡 rule */
static const char *PE_RULE =
    "import \"pe\"\n"
    "rule fuzzing_pe {\n"
    "  condition:\n"
    "    pe.is_pe\n"
    "}\n";

static YR_RULES *g_rules = NULL;

__attribute__((constructor))
static void global_init(void) {
    YR_COMPILER *compiler = NULL;
    yr_initialize();
    yr_compiler_create(&compiler);
    yr_compiler_add_string(compiler, PE_RULE, NULL);
    yr_compiler_get_rules(compiler, &g_rules);
    yr_compiler_destroy(compiler);
}

/* 掃描回呼：我們不在乎 match 結果，只要觸發解析路徑 */
static int scan_callback(YR_SCAN_CONTEXT *ctx, int msg,
                         void *msg_data, void *user_data) {
    (void)ctx; (void)msg; (void)msg_data; (void)user_data;
    return CALLBACK_CONTINUE;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 2 || !g_rules) return 0;

    /* 直接把 fuzzer 輸入當 PE binary 掃描；
     * 不需要 magic bytes：讓 fuzzer 自己學 */
    yr_rules_scan_mem(g_rules,
                      (const uint8_t *)data, size,
                      SCAN_FLAGS_FAST_MODE,
                      scan_callback, NULL,
                      /* timeout (s) */ 5);
    return 0;
}
```

這個雙 harness 架構對照 Part 1 Ch 7（Executor 家族）：兩個 harness 各自是一個獨立的 in-process executor，覆蓋不重疊的程式碼路徑。

### Harness 3（LibAFL 版，進階選項）

如果你完成了 Part 1 練習 A，可以用 LibAFL 替換 libFuzzer：

```rust
// src/main.rs（骨架，未包含完整 LibAFL boilerplate）
// **本段為理論預期行為，未完整實測**
// 驗證步驟：cargo build --release 後接 libyara FFI 看 build 是否通過
use libafl::prelude::*;
use libafl_bolts::prelude::*;

// 關鍵組件選擇
// Observer:   ShmemMapObserver（邊覆蓋率 bitmap，比對 libFuzzer 的 __sanitizer_cov_trace_pc_guard）
// Feedback:   MaxMapFeedback（最大化覆蓋率）
// Executor:   InProcessExecutor（libyara 可 in-process 呼叫，不需 fork）
// Scheduler:  WeightedScheduler（給高覆蓋率 input 更高抽取機率）
// Mutator:    StdScheduledMutator + havoc mutations
//             + 若要結構感知：TokenMutator（從 YARA keyword 表建 token dict）
```

LibAFL 的優勢在這個目標上：可以在 Mutator 加入 YARA 關鍵字字典（`rule`, `condition`, `strings`, `import`），讓文法感知變異（Part 2 Ch 14）有機會更快找到 parser 邊界。

### Seed Corpus

```bash
mkdir corpus_rules corpus_pe

# 規則 corpus：從 YARA 官方 rule repo 抽
git clone --depth=1 https://github.com/Yara-Rules/rules.git yara_rules_repo
find yara_rules_repo -name "*.yar" -o -name "*.yara" | \
    xargs -I{} cp {} corpus_rules/

# 精簡到 1000 個有代表性的（libFuzzer corpus minimization）
./fuzz_rule_parser -merge=1 corpus_rules_min/ corpus_rules/

# PE corpus：從系統抓小型 PE（WSL2 可存取 Windows 路徑）
find /mnt/c/Windows/System32 -name "*.exe" -size -100k | head -50 | \
    xargs -I{} cp {} corpus_pe/
```

Corpus 策略對照 Part 9 Ch 45（ClusterFuzz corpus 管理）：seed corpus 品質直接影響 fuzzer 能走多遠，不要只餵空檔案。

### 產出物

- `fuzz_rule_parser.c`、`fuzz_pe_scan.c`（含 Makefile）
- `corpus_rules_min/`、`corpus_pe/`（精簡後的 seed corpus）
- 若選 LibAFL：Rust 專案目錄

### 驗收標準

- 兩個 harness 都能 build 零 warning（-Wall -Wextra 通過）
- `./fuzz_rule_parser -runs=1000 corpus_rules_min/` 跑完不崩潰、不 leak
- `./fuzz_pe_scan -runs=1000 corpus_pe/` 同上
- Corpus 兩個目錄各至少 50 個有效 seed

---

## M3：跑 Fuzzer + 監控 Coverage

### 子目標

讓兩個 fuzzer 平行跑至少 24 小時，記錄 edge coverage 成長曲線，確認 fuzzer 是否卡死。

### 執行

```bash
# 規則 fuzzer（背景）
LD_LIBRARY_PATH=yara/libyara/.libs \
./fuzz_rule_parser \
    -jobs=4 -workers=4 \
    -max_len=65536 \
    -timeout=10 \
    -print_coverage=1 \
    corpus_rules_min/ \
    2>&1 | tee fuzz_rule.log &

# PE fuzzer（背景）
LD_LIBRARY_PATH=yara/libyara/.libs \
./fuzz_pe_scan \
    -jobs=4 -workers=4 \
    -max_len=262144 \
    -timeout=10 \
    corpus_pe/ \
    2>&1 | tee fuzz_pe.log &
```

### Coverage 監控

```bash
# 追蹤 edge coverage 成長（每 5 分鐘抓一次）
watch -n 300 'grep "#" fuzz_rule.log | tail -5'

# 取得更精確的行覆蓋率報告（需 clang coverage build）
# 重新 build with -fprofile-instr-generate -fcoverage-mapping
# 跑完後：
llvm-profdata merge -o merged.profdata default*.profraw
llvm-cov report ./fuzz_rule_parser -instr-profile=merged.profdata \
    --sources=yara/libyara/
```

### Coverage 停滯的診斷

24 小時後，如果 edge coverage 停滯（新 edge < 10/hr），診斷流程：

1. 看 `fuzz_rule.log` 的 `SLOW` / `timeout` 行——可能有路徑無限迴圈
2. 用 `llvm-cov show` 找哪些函式覆蓋率 = 0——那就是 fuzzer 沒打到的地方
3. 判斷原因：magic bytes 關卡（解法：`-dict=` 或結構感知）、checksum 關卡（解法：Part 8 hybrid）、狀態依賴（解法：Part 3 stateful harness）

這步對照 Part 0 Ch 1 的四道牆：你現在親自看到 fuzzer 卡在哪面牆上。

### 產出物

- `fuzz_rule.log`、`fuzz_pe.log`（至少 24hr 的 log）
- `coverage_report/`（`llvm-cov` HTML 輸出）
- `coverage_analysis.md`：哪些路徑覆蓋到了、哪些沒有、推測原因

### 驗收標準

- 至少一個 harness 跑過 10M execs
- Edge coverage 成長曲線有截圖或文字記錄
- 能說出「fuzzer 在 X 小時後卡在哪，為什麼」

---

## M4：Triage Crash（Dedup / Minimize）

### 子目標

如果有 crash，做 dedup 和 minimize；如果沒有 crash，做「假設性 triage」——用一個已知的 PoC（從 CVE 資料庫或 issue tracker 找）走完 triage 流程。

### Dedup

libFuzzer 的 crash 輸出在 `crash-*` 檔案，但同一個 bug 可能產生幾百個不同的 crash input。

```bash
# 列出所有 crash
ls crash-* | head -20

# 用 ASan stack trace 做第一輪 dedup：
# 對每個 crash input 跑一次，擷取 stack trace 的前 N frame
for f in crash-*; do
    LD_LIBRARY_PATH=yara/libyara/.libs \
    ASAN_OPTIONS=halt_on_error=1 \
    ./fuzz_rule_parser "$f" 2>&1 | \
        grep "^    #" | head -5 >> crash_stacks.txt
    echo "---$f---" >> crash_stacks.txt
done

# 用 stack trace hash 分群（簡易版）
sort crash_stacks.txt | uniq -c | sort -rn | head -20
```

更嚴謹的做法：用 `casr`（Crash Analysis and Severity Reporter）自動分群：

```bash
# 安裝 casr（Rust 工具，Part 9 提到）
cargo install casr
casr-cluster -c crash_dir/ cluster_dir/
casr-san -o report.json -- \
    LD_LIBRARY_PATH=yara/libyara/.libs ./fuzz_rule_parser crash-<hash>
```

### Minimize

找到 unique crash 後，minimize crash input：

```bash
# libFuzzer 內建 minimize
LD_LIBRARY_PATH=yara/libyara/.libs \
./fuzz_rule_parser -minimize_crash=1 -max_total_time=60 \
    -exact_artifact_path=minimized_crash \
    crash-<hash>

# 驗證 minimized 仍能觸發同一 crash
LD_LIBRARY_PATH=yara/libyara/.libs ./fuzz_rule_parser minimized_crash
```

這步對照 Part 9 Ch 45：minimize 是 triage 的必要步驟，不只為了讀懂 crash，也是 disclosure 時提供 PoC 的基本禮儀。

### 沒有 Crash 的情況

如果 24hr 沒有 crash，取 YARA CVE-2023-52779 的公開 PoC：

```bash
# 取得 PoC（從 NVD 或 GitHub advisory）
# 假設 PoC 是一個畸形 PE binary，存成 poc_cve2023_52779.pe

# 用你的 harness 重現
LD_LIBRARY_PATH=yara/libyara/.libs \
ASAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
./fuzz_pe_scan poc_cve2023_52779.pe
```

目的是讓你走完 triage 流程，哪怕不是自己找到的 crash。

### 產出物

- `unique_crashes/`：dedup 後的 unique crash input 集合
- `minimized_crash`：最小化的 PoC
- `triage_report.md`：每個 unique crash 的 stack trace summary

### 驗收標準

- Dedup 後 unique crash 數量有記錄（哪怕是 0，要誠實說明）
- 至少有一個 minimized crash input（或公開 PoC 的 minimize 版本）
- Minimize 前後 input 大小比較有記錄

---

## M5：Root Cause 分析（ASan / gdb）

### 子目標

從 crash stack trace 定位到有問題的程式碼行，理解 bug 的觸發條件。

### ASan 輸出解讀

典型的 ASan heap-buffer-overflow 輸出：

```
==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602000001234
READ of size 4 at 0x602000001234 thread T0
    #0 0x7f... in pe_get_uint32 yara/libyara/modules/pe/pe.c:123
    #1 0x7f... in pe_parse_header yara/libyara/modules/pe/pe.c:456
    #2 0x7f... in module_scan yara/libyara/modules/pe/pe.c:789
    ...
```

從這個輸出你知道：`pe_get_uint32` 在讀 4 bytes 時越界，來源是 PE header 解析。

### gdb 深挖

```bash
# 重新 build，不加 ASan（讓 gdb 看到真實記憶體），但加 debug symbols
./configure CC=clang CFLAGS="-O0 -g3" --disable-shared
make -j$(nproc)

# 跑 gdb
LD_LIBRARY_PATH=yara/libyara/.libs \
gdb --args ./fuzz_pe_scan minimized_crash

(gdb) run
(gdb) bt           # 看 backtrace
(gdb) frame 0      # 切到 crash frame
(gdb) list         # 看周圍程式碼
(gdb) info registers   # 看寄存器
(gdb) x/4xb <address>  # 看記憶體內容
```

### Root Cause 分析範本

記錄你的分析（對照 Part 9 Ch 47 的格式）：

```
Bug class：heap-buffer-overflow（讀越界）
觸發路徑：yr_rules_scan_mem() → module_scan() → pe_parse_header() → pe_get_uint32()
根因：pe_parse_header() 讀取 PE optional header 的 DataDirectory 陣列時，
      未檢查 NumberOfRvaAndSizes 欄位是否超出輸入 buffer 大小，
      導致 pe_get_uint32() 讀越界。
觸發條件：畸形 PE binary，其中 NumberOfRvaAndSizes = 0xFF（遠大於合法值 16）
          且 optional header 之後的 buffer 不足對應長度。
修補方向：在讀取 DataDirectory 前加 bounds check：
          if (offset + count * sizeof(IMAGE_DATA_DIRECTORY) > size) return;
```

這步横向連結 `kernel_pwn` 課的記憶體破壞分析技術，以及 `symex_taint` 課的 taint 追蹤——如果 gdb 看不清楚資料流向，可以用 SymCC（Part 8 Ch 41）的 symbolic tracing 找哪個輸入 byte 控制了越界距離。

### 產出物

- `root_cause.md`：按上面範本格式完整填寫
- gdb session log（`set logging file gdb.log; set logging on`）

### 驗收標準

- 能說出 bug 所在的具體程式碼行（file:line）
- 能說出觸發 bug 需要滿足哪些條件（不是「輸入壞了」，而是「offset X 的值需要大於 Y」）
- 能說出哪行程式碼加什麼 check 可以修掉這個 bug

---

## M6：判斷可利用性

### 子目標

按 CVE scoring 的慣例和你自己的分析，給這個 bug 打可利用性等級。

### 判斷框架

| 維度 | 問題 | 你的答案 |
|---|---|---|
| Crash type | Read OOB / Write OOB / Use-after-free / Double free / Stack overflow? | 填入 |
| 攻擊者控制能力 | 攻擊者能控制越界距離嗎？能控制越界寫入的值嗎？ | 填入 |
| 執行脈絡 | YARA 在目標系統是 daemon？CLI tool？Kernel module？ | 填入 |
| 緩解措施 | ASLR/PIE/RELRO/CFI/stack canary 全開嗎？ | 填入 |
| 可到達性 | 這個路徑從外部網路觸發需要幾步？ | 填入 |

可利用性等級（你的判斷）：

- **Critical（可直接 RCE）**：Write OOB 且攻擊者控制值、deref 一個攻擊者控制的指標
- **High（有機會升級為 RCE）**：UAF、double free、limited write OOB
- **Medium（DoS 確定，RCE 困難）**：Read OOB（可能 info leak）、controllable crash
- **Low（DoS，無利用路徑）**：stack overflow in non-exploitable context

CVSS v3.1 計算（可選，對照 Ch 47）：

```
AV:L/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H  →  7.8 HIGH（本機 CLI 工具情境）
AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H  →  9.8 CRITICAL（daemon 情境）
```

### 產出物

- `exploitability.md`：按上面框架填完，給出等級判斷和理由

### 驗收標準

- 判斷有理由，不是空說「這很危險」
- 能說出「如果這是 daemon 環境，攻擊路徑會是什麼」

---

## M7：寫 CVE 等級報告 + Responsible Disclosure

### 子目標

按 coordinated disclosure 標準寫報告，並走完（或模擬）disclosure 流程。

### 報告格式（對照 Part 9 Ch 47）

```markdown
# Security Advisory：YARA libyara PE Module Heap Buffer Overflow

## Summary
libyara PE module 在解析 PE optional header 的 DataDirectory 時，
未驗證 NumberOfRvaAndSizes 欄位的邊界，導致 heap buffer overflow。

## Affected Versions
- YARA <= 4.x.x（填入你測試的版本）

## Impact
- 攻擊者可提供畸形 PE binary 讓任何使用 libyara 做掃描的程式崩潰（DoS）
- 在 ASLR/PIE 未完整啟用的環境中，有機會升級為 arbitrary read / RCE

## Proof of Concept
（附上 minimized_crash 的 hexdump + 觸發指令）

## Root Cause
（貼 root_cause.md 的核心段落）

## CVSS v3.1 Score
（填入你計算的分數）

## Timeline
- YYYY-MM-DD：Discovery
- YYYY-MM-DD：Report sent to maintainers（security@virustotal.com 或 GitHub Security Advisory）
- YYYY-MM-DD：Maintainer confirmed
- YYYY-MM-DD：Fix released
- YYYY-MM-DD：Public disclosure（建議：fix release 後 90 天）

## Patch Suggestion
（貼你在 M5 寫的修補方向）

## Reporter
（你的名字 / handle）
```

### Disclosure 流程（實際執行）

```
1. 去 https://github.com/VirusTotal/yara/security/advisories/new
   建立 private security advisory（GitHub 提供的 coordinated disclosure 機制）
2. 附上 minimized PoC、root cause 說明、CVSS 分數
3. 等 maintainer 回應（合理等待期：7 天）
4. 若無回應，escalate 到 security@virustotal.com
5. 無論如何，90 天後公開（Google Project Zero policy）
```

如果你的 bug 是已知 CVE 的重現（沒有新發現），跳過真實 disclosure，但仍然寫出假設的 timeline 和報告——這個練習本身就是訓練。

### 產出物

- `advisory.md`：完整的 security advisory 草稿
- `timeline.md`：disclosure timeline 記錄

### 驗收標準

- Advisory 包含所有必要欄位（影響版本、CVSS、PoC、patch suggestion、timeline）
- Timeline 符合 responsible disclosure 慣例（不是發現當天就公開）

---

## Kernel 支線：syzkaller 打自訂 Module（M-K 系列）

**需要 KVM + 自 build kernel image 環境。WSL2 上可執行但受巢狀虛擬化限制（Intel VT-x nested virtualization），本段標注未完整實測。**

### M-K1：準備 kernel + module

參考 Part 4 Ch 26，準備一個有意設計了 bug 的 kernel module（或用 Part 4 練習 D 的 buggy_module）：

```c
/* buggy_ioctl_module.c（有意設計的越界讀取）*/
static long buggy_ioctl(struct file *f, unsigned int cmd, unsigned long arg) {
    char buf[64];
    /* BUG: 直接用 user-supplied length，沒有 bounds check */
    unsigned long len = arg;
    if (copy_from_user(buf, (void __user *)cmd, len))
        return -EFAULT;
    return 0;
}
```

### M-K2：寫 syzlang 描述

```syz
# buggy_module.txt（syzlang 描述，對照 Part 4 Ch 25）
resource fd_buggy[fd]

openat$buggy(fd const[AT_FDCWD], file ptr[in, string["/dev/buggy"]], flags flags[open_flags], mode const[0]) fd_buggy

ioctl$BUGGY_CMD(fd fd_buggy, cmd intptr, len len[buf], buf ptr[in, array[int8]])
```

### M-K3：設定並跑 syzkaller

**本段未實測，為理論預期行為。** 在真實 KVM 環境中：

```bash
# 驗證步驟：在有 KVM 的 Linux 主機（非 WSL）上
# 1. 編譯帶 KCOV + KASAN 的 kernel
make defconfig
scripts/config -e KCOV -e KASAN -e KASAN_INLINE
make -j$(nproc)

# 2. 設定 syzkaller config
cat > syzkaller.cfg << 'EOF'
{
  "target": "linux/amd64",
  "http": "127.0.0.1:56741",
  "workdir": "/tmp/syzkaller_work",
  "kernel_obj": "/path/to/linux",
  "image": "/path/to/stretch.img",
  "sshkey": "/path/to/stretch.id_rsa",
  "syzkaller": "/path/to/syzkaller",
  "procs": 8,
  "type": "qemu",
  "vm": {"count": 4, "kernel": "/path/to/bzImage", "cpu": 2, "mem": 2048},
  "enable_syscalls": ["openat$buggy", "ioctl$BUGGY_CMD"]
}
EOF

./bin/syz-manager -config syzkaller.cfg
```

如果你的環境支援 KVM，走完這個支線並用 KASAN 的 crash report（對照 Part 4 Ch 23）做 root cause，是這個 final 最有深度的選項。它直接接上 `kernel_pwn` 課的 kernel exploit 開發。

---

## 延伸挑戰

完成 M1–M7 後，這些挑戰讓深度再上一層：

### E1：Hybrid Fuzzing（接 Part 8）

當 coverage 停滯、懷疑是 magic byte 或 checksum 關卡時，接上 SymCC：

```bash
# 重新 build libyara with SymCC instrumentation
# SymCC 原始碼：https://github.com/eurecom-s3/symcc
export CC=/path/to/symcc
export CXX=/path/to/sym++
./configure && make -j$(nproc)

# 讓 SymCC 從停滯的 corpus 生成新 input
symcc ./fuzz_rule_parser -symcc_input_file=stuck_corpus_item
```

**本段未實測（SymCC build 需要 LLVM 外掛），為理論預期行為。** 驗證步驟：`symcc --version` 後用小型測試目標確認 concolic execution 可正常輸出新 input。

### E2：Directed Fuzzing（接 Part 8 Ch 43）

如果你的 M5 已找到 root cause，想確認 patch 是否真的修掉：

```bash
# 用 AFLGo 對 patch 改動的行做 directed fuzzing
# 目標：快速重現 patched 版本是否還能觸發同樣 crash
aflgo-fuzz -i corpus_rules_min/ -o out_directed/ \
    -z exp -c 45m \
    -- ./fuzz_rule_parser_instrumented @@
```

### E3：Snapshot Fuzzing（接 Part 5）

如果你的目標有昂貴的初始化（例如 libyara 的 `yr_initialize()` 每次都很慢），可以用 Nyx/kAFL 做 snapshot 加速：

**本段需要 VT-x + Intel PT 硬體，WSL2 不支援，為理論預期行為。**

架構要點：在 `yr_initialize()` 完成後做 snapshot，讓 fuzzer 從已初始化狀態開始每次 iteration，跳過初始化開銷。

### E4：FuzzBench 評測（接 Part 9 Ch 46）

把你的 harness 和一個 baseline（pure libFuzzer without dict）跑 FuzzBench 風格的對照實驗：

```bash
# 兩個 build：with dict / without dict
# 固定 seed、跑相同時間（e.g. 24hr）、比較 edge coverage 曲線
# 畫出成長曲線，回答：dict 在哪個時間點開始帶來差距？
python3 plot_coverage.py --log fuzz_with_dict.log fuzz_without_dict.log
```

---

## 評分 Rubric

| 面向 | 滿分 | 評分標準 |
|---|---|---|
| **Harness 品質**（M2） | 25 | 能 build、無 ASan false positive (5)；harness 正確隔離 iteration 狀態，不跨 run 洩漏 (10)；有兩個覆蓋不同路徑的 harness (5)；seed corpus 有實質內容，非空檔案 (5) |
| **Coverage 深度**（M3） | 20 | 跑超過 10M execs (5)；edge coverage 成長曲線有記錄 (5)；能分析停滯原因並提出改進方向 (10) |
| **Triage 嚴謹度**（M4） | 20 | 正確 dedup（不同 stack trace 不算同一 bug）(10)；minimize 後 input 比原始小至少 50% (5)；每個 unique crash 有 summary (5) |
| **Root Cause 正確性**（M5） | 25 | 定位到具體 file:line (10)；說明觸發條件（不只說「crash」）(10)；提出具體 patch 方向 (5) |
| **報告品質**（M7） | 10 | Advisory 格式完整 (5)；CVSS 計算合理並附理由 (3)；disclosure timeline 符合慣例 (2) |

**總分：100 分**

沒找到新 crash 不扣分，只要 M4 用已知 PoC 走完流程即可。評分重點在你對「為什麼 fuzzer 停在這」的分析品質，以及 root cause 的嚴謹程度。

---

## 交付物清單

```
final_project/
├── build.sh                          # 可重現的 build 腳本
├── attack_surface.md                 # M1：攻擊面分析
├── harness/
│   ├── fuzz_rule_parser.c            # M2：規則解析 harness
│   ├── fuzz_pe_scan.c                # M2：PE 掃描 harness
│   └── Makefile
├── corpus/
│   ├── corpus_rules_min/             # M2：seed corpus（規則）
│   └── corpus_pe/                    # M2：seed corpus（PE）
├── logs/
│   ├── fuzz_rule.log                 # M3：至少 24hr 的 log
│   └── fuzz_pe.log
├── coverage_report/                  # M3：llvm-cov HTML 輸出
├── coverage_analysis.md              # M3：coverage 停滯分析
├── triage/
│   ├── unique_crashes/               # M4：dedup 後的 crash input
│   ├── minimized_crash               # M4：最小化 PoC
│   └── triage_report.md             # M4：triage 摘要
├── root_cause.md                     # M5：root cause 分析
├── exploitability.md                 # M6：可利用性判斷
├── advisory.md                       # M7：security advisory 草稿
└── timeline.md                       # M7：disclosure timeline
```

---

## 常見卡點

**1. libyara build 時找不到 OpenSSL / libmagic**

YARA 的幾個 module 依賴系統函式庫，但 fuzzing harness 不需要這些模組。加 `--without-crypto --without-magic` 可以繞過，並讓 build 更乾淨：

```bash
./configure CC=clang CFLAGS="-O1 -fsanitize=address,undefined" \
    --disable-shared --without-crypto --without-magic
```

**2. ASan 報告大量 leak，fuzzer 速度被拖慢**

libyara 在正常退出時才釋放全域資源，導致 ASan leak detector 每次 run 都報 leak，嚴重拖慢速度。解法：

```bash
ASAN_OPTIONS=detect_leaks=0 ./fuzz_rule_parser corpus/
```

研究 crash 時才開 `detect_leaks=1`，平時跑 fuzzing 關掉。

**3. Coverage 從一開始就不動**

原因通常是 harness 沒有正確傳入 input。加一個 debug build 驗證：

```bash
# 手動傳入一個已知會觸發大量程式碼路徑的 input
./fuzz_rule_parser -runs=1 -- corpus/valid_rule.yar
# 看輸出有無 crash、有無 edge coverage 增加
```

如果 edge coverage 一直是 0，代表 harness 裡有 early return 或 input routing 問題——最常見的是忘記傳 `size` 或者 `null` 檢查在太早的地方把 input 丟掉了。

**4. Crash 有但 gdb 看不清楚（stripped binary）**

用 `-O0 -g3` 重新 build，不要用 ASan build 來跑 gdb（ASan 會改變記憶體佈局）。如果是因為 `-fomit-frame-pointer` 導致 backtrace 斷掉，加 `-fno-omit-frame-pointer`。

**5. Minimize 後 crash 不能重現**

Minimize 有時會把「必要的」字節也砍掉，尤其當 crash 依賴 heap 狀態而非純粹輸入值時。驗證方法：

```bash
ASAN_OPTIONS=halt_on_error=1 ./fuzz_pe_scan minimized_crash
echo $?   # 非 0 才代表真的 crash
```

如果 minimized 不能重現，試試 `-minimize_crash=1 -runs=100000`（給更多 runs），或手動從 crash input 減字節。

**6. PE 掃描 harness timeout 太多**

YARA 的 PE 模組對部分畸形輸入會進入 O(n²) 路徑（例如特定的 section 計數）。把 `-timeout=5` 調低到 `-timeout=2`，並加 `SCAN_FLAGS_FAST_MODE` flag（harness 骨架已加）。

**7. 不知道 disclosure 要寄給誰**

優先查 `SECURITY.md` 或 `security.txt`（`https://github.com/<project>/security/advisories`）。大多數活躍的開源專案都有 GitHub Security Advisory 機制——這是最推薦的 coordinated disclosure 入口，因為它保密、有時間戳記、讓 maintainer 可以要求 CVE ID。

---

## 自我檢核 Checkbox

完成這個 final 之後，你應該能誠實打勾以下每一項：

**Part 0 — 起點**
- [ ] 我能分析一個目標的 attack surface，說出它的輸入形態，以及為什麼 dumb mutation 在這個目標上效率低
- [ ] 我知道 fuzzer 全景裡不同工具對應什麼形態的問題

**Part 1 — LibAFL / 造 fuzzer**
- [ ] 我能說出 Observer / Feedback / Executor / Mutator / Stage 各自的職責
- [ ] 我能從零寫出一個 in-process fuzzer harness（libFuzzer 或 LibAFL），不只是「把別人的 harness 改一改」

**Part 2 — 結構感知**
- [ ] 我能判斷什麼時候需要結構感知 mutator，什麼時候 dumb mutation 就夠
- [ ] 我能為這個目標設計一個簡單的 token dictionary 或 proto schema

**Part 3 — Stateful**
- [ ] 如果我的目標有狀態初始化（c-ares 選項），我能設計 harness 正確分離 setup / fuzz / teardown

**Part 4 — Kernel（若走 kernel 支線）**
- [ ] 我能寫 syzlang 描述一個 ioctl 介面
- [ ] 我能分辨 KASAN 報告的 bug class 並定位到 kernel 源碼行

**Part 5–6 — Snapshot / 韌體（若走延伸挑戰）**
- [ ] 我能說出 snapshot fuzzing 在什麼情況下比 fork-based 快，以及快多少
- [ ] 我知道 rehosting 的主要問題是 MMIO 建模，不是「把 binary 丟進 QEMU」這麼簡單

**Part 7 — JS 引擎（若走延伸挑戰）**
- [ ] 我知道 Fuzzilli 為什麼用 IL-based mutation 而不是直接 mutate JS 字串

**Part 8 — Hybrid / Directed**
- [ ] 我能說出 coverage 停滯的 3 種主因，以及每種對應哪個工具
- [ ] 我能判斷什麼時候值得上 SymCC，什麼時候上了也沒用（例如 crypto 路徑）

**Part 9 — 評測科學 / CVE**
- [ ] 我的 crash dedup 標準是 stack trace，不是「檔案內容不同就算不同 bug」
- [ ] 我寫的 advisory 包含 CVSS 分數、PoC、patch suggestion、disclosure timeline
- [ ] 我知道 90 天 disclosure window 的意義，以及 maintainer 沒回應時我該怎麼做

---

## 結尾

Fuzzing 不是開著 `afl-fuzz` 然後等 crash 跳出來的工程——它是一個診斷迴圈：目標的形態決定 fuzzer 的架構，coverage 的停滯點告訴你 fuzzer 看不到哪裡，看不到的地方決定你下一步是上文法、上 hybrid、還是換成 snapshot。這個 campaign 跑完，你已經把這個診斷迴圈親手走了一遍；下次遇到新目標，你知道從哪裡開始問。
