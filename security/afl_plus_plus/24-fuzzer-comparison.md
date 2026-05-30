# Ch 24 — Fuzzer Comparison：AFL++ vs libFuzzer vs Honggfuzz 選型指南

> **目標**：能根據 target 特性做 AFL++ / libFuzzer / Honggfuzz 工具選型；理解三者的設計哲學差異。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 為什麼需要這個？

「用 AFL++ 就好了，它最強。」這個說法在某些場景是對的，在另一些場景是錯的。

三個工具在不同的設計目標下演化出不同的特性：

- **AFL++** 設計給「有一個可執行的 binary，從 stdin/file 讀 input」的場景。生態豐富，可配置性最高。
- **libFuzzer** 設計給「你在開發一個 library，想直接測試 API」的場景。in-process，速度最快，和 Google 的 OSS-Fuzz 深度整合。
- **Honggfuzz** 設計給「需要硬體 coverage，或是 network service」的場景。支援多種 coverage 來源，包括硬體 perf events。

選錯工具不是世界末日，但可能讓你的 fuzzing campaign 效率低 5-10 倍。

---

## 先建立直覺

三個工具的核心差異可以用「input 怎麼進到 target」來理解：

```
AFL++（fork-based）：
  fuzzer process → fork → child process → 從 stdin/file 讀 → crash/exit
                                                    ↑
                                             每次都是新 process

libFuzzer（in-process）：
  fuzzer + target 在同一個 process
  mutate → call LLVMFuzzerTestOneInput() → return → mutate → ...
            ↑
     函式呼叫，不是 fork！極快，但 crash 會 kill 整個 process

Honggfuzz（多種模式）：
  類似 AFL++ 的 fork-based，但 coverage 來源更多：
  - LLVM instrumentation（和 AFL++ 類似）
  - perf BTS（硬體 branch trace，不需插樁！）
  - Intel PT（更精確的硬體 trace）
```

---

## 橫向連結

- **Ch 8（QEMU / Frida mode）**：libFuzzer 無法用 QEMU，AFL++ 和 Honggfuzz 都支援，這是一個選型維度。
- **Ch 21（Difficult Targets）**：network service 的選型在這章有更多細節。
- **Ch 23（Measuring Effectiveness）**：本章的工具選型要用公平的比較方法驗證。

---

## AFL++ 的設計哲學

AFL++ 是 AFL 的社群維護版本，設計哲學是「高可配置性 + 豐富生態」。

### 核心機制

**Fork server**：target 在 `__AFL_INIT()` 之前只做一次初始化（load shared libraries、parse config），然後 fork。每次 iteration 是 fork 的子 process，不是完整的 exec。

```
parent process（fork server）：
├── 初始化完成（一次）
├── 等待 AFL++ 的 signal
├── fork()
│   └── child process：從初始化後的狀態開始執行
│       ├── 讀 input（stdin / @@）
│       ├── 執行 parse/process 邏輯
│       └── 正常退出 or crash
└── 收到 child 的 exit status → 報告給 AFL++
```

**SHM bitmap**：parent 和 child 共享 64KB 的 shared memory，child 把 edge hit 寫進去，parent 讀取後傳給 AFL++。

### 優勢

- **可配置性**：LLVM mode、QEMU mode、Frida mode、custom mutator、network fuzzing plugin——全套工具箱。
- **Persistent mode**：在 loop 裡呼叫目標函式，省掉 fork 的 overhead，速度提升 10-20x。
- **生態**：AFL-Net、grammar mutator、所有 OSS-Fuzz 支援的 harness 都可以直接用。
- **適合有 stdin/file interface 的 target**：CLI 工具（objdump、file、strings）、解析器（image decoder、document parser）。

### 限制

- Fork overhead：每次 exec 有 fork 的成本（幾百微秒），對需要毫秒級 feedback 的 target 影響大。
- In-process fuzzing 不支援（原生）：沒有 `LLVMFuzzerTestOneInput` 的等效機制（雖然有 `__AFL_LOOP`，但語義不同）。

---

## libFuzzer 的設計哲學

libFuzzer 是 LLVM 專案的一部分，和 Clang 深度整合。設計目標是「開發者在寫 code 時，可以輕鬆加一個 fuzz target」。

### 核心機制

**In-process fuzzing**：libFuzzer 是一個 library（`-fsanitize=fuzzer`），link 進你的 target binary。你提供一個函式：

```c
// fuzz_target.c
#include <stdint.h>
#include <stddef.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    // 直接呼叫你要測試的函式
    parse_png(data, size);  // 不需要檔案 I/O
    return 0;  // 0 = 正常；non-zero 可以觸發 libFuzzer 的 crash 處理
}
```

```bash
# 編譯（libFuzzer + ASAN）
clang -fsanitize=fuzzer,address -g -o fuzz_target fuzz_target.c -lpng

# 執行
./fuzz_target corpus/  # corpus 是 seed 目錄，格式和 AFL++ 一樣（raw bytes）
```

**LLVM sanitizer coverage（SanitizerCoverage）**：和 AFL++ 的 LLVM pass 是同一套基礎設施（`-fsanitize-coverage=...`）。Coverage 在 process 內部直接更新計數器，不需要 SHM。

### 優勢

- **速度**：沒有 fork overhead，function call 比 fork 快 100x。對小型 library function（parse_png、decompress_buffer），可以達到 100000+ exec/sec。
- **ASan 整合**：`-fsanitize=fuzzer,address` 一個 flag 搞定，開發者體驗最好。
- **OSS-Fuzz**：Google 的 OSS-Fuzz 平台原生支援 libFuzzer harness，已有 1000+ 開源 project 的 harness 可以直接用。
- **Structured fuzzing**：libFuzzer 的 `FuzzedDataProvider` 可以把 raw bytes 結構化成 integer、string、bool，讓 harness 更容易寫。

### 限制

- **Crash 後 process 死掉**：任何 crash（包括 SIGABRT）都 kill 整個 fuzzer process。libFuzzer 會在 crash 後重啟，但每次重啟都要重新 load library、warm up JIT（如果有的話）。
- **Binary-only target 不支援**：沒有 QEMU/Frida mode，只能 fuzz 有 source 的 target。
- **Global state 問題**：in-process fuzzing 假設 `LLVMFuzzerTestOneInput` 不修改 global state。如果 target library 有 global 初始化狀態（全域 allocator、singleton），每次呼叫可能彼此干擾。

---

## Honggfuzz 的設計哲學

Honggfuzz 是 Google Project Zero 開發的 fuzzer，設計目標是「盡可能用多種 feedback 來源」。

### 核心機制

Honggfuzz 支援四種 coverage feedback，可以依環境選擇：

```
1. 軟體插樁（類似 AFL++）：
   -fsanitize-coverage=trace-pc-guard（需要 source + clang）

2. 硬體 BTS（Branch Trace Store）：
   Intel CPU 的硬體 branch tracing，不需要插樁！
   perf_event_open() + PERF_SAMPLE_BRANCH_STACK
   → 適合 closed-source binary（在 bare metal 上）

3. Intel PT（Processor Trace）：
   更精確的硬體 trace，可以追蹤每條指令
   需要 Linux 4.0+ 和支援 Intel PT 的 CPU

4. Sanitizer coverage（ASan/MSan 的 pc-guard）：
   結合 sanitizer 的 coverage 和 bug 偵測
```

```bash
# 安裝 Honggfuzz
git clone https://github.com/google/honggfuzz
cd honggfuzz && make

# Honggfuzz 插樁編譯（用 hfuzz-clang 替代 clang）
hfuzz-clang -fsanitize=address -o target target.c

# 執行
honggfuzz -i seeds/ -o corpus/ -- ./target ___FILE___
# ___FILE___ 是 Honggfuzz 的 input file placeholder（類似 AFL++ 的 @@）
```

**Persistent mode（`HF_ITER`）**：

```c
#include "honggfuzz.h"

int LLVMFuzzerTestOneInput(const uint8_t *buf, size_t len) {
    parse_input(buf, len);
    return 0;
}
// 加入 HF_ITER 後 Honggfuzz 會在同一個 process 裡 loop
```

### 適合 network service

Honggfuzz 有 `hfuzz_socketfuzzer.c` 框架，直接對 TCP socket 做 fuzzing：

```bash
# Honggfuzz 的 network fuzzing
honggfuzz -i seeds/ -o corpus/ \
    --netdriver_tcpport 7777 \
    -- ./server_binary
```

比 AFL-Net 更直接，不需要 desocket patch。

---

## 具體場景選型

### 場景 A：Fuzz libpng（library API）

**首選：libFuzzer + persistent mode**

```c
// libFuzzer harness
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    png_image image;
    memset(&image, 0, sizeof(image));
    image.version = PNG_IMAGE_VERSION;
    png_image_begin_read_from_memory(&image, data, size);
    if (image.width > 0 && image.height > 0) {
        size_t buf_size = PNG_IMAGE_SIZE(image);
        void *buf = malloc(buf_size);
        if (buf) {
            png_image_finish_read(&image, NULL, buf, 0, NULL);
            free(buf);
        }
    }
    png_image_free(&image);
    return 0;
}
```

理由：libpng 是 library，直接呼叫 API 比 file-based fuzzing 更快；libFuzzer in-process 可達 50000+ exec/sec；OSS-Fuzz 上有現成 harness 參考。

**次選：AFL++ persistent mode**（覆蓋率相近，可以利用 AFL++ 的 CmpLog 突破 PNG magic bytes）

### 場景 B：Fuzz OpenSSL TLS handshake（network protocol）

**首選：Honggfuzz（network fuzzing）或 AFL++ with desocket**

OpenSSL 的 TLS state machine 複雜，每次 handshake 都有多個 message 交換。

```bash
# Honggfuzz network fuzzing
honggfuzz --netdriver_tcpport 4433 \
    -i tls_seeds/ -o corpus/ \
    -- openssl s_server -key server.key -cert server.crt \
       -accept 4433 -naccept 1

# AFL++ 的方案：用 desocket + dessl（patch TLS verification）
LD_PRELOAD=./desock.so:./dessl.so \
    afl-fuzz -i seeds/ -o out/ -- openssl s_server ...
```

libFuzzer 不適合：TLS handshake 涉及 global TLS context 和 network state，in-process 的 state 難以 reset。

### 場景 C：Fuzz binutils objdump（CLI tool）

**首選：AFL++**

```bash
# 直接的 file-based fuzzing
afl-clang-lto -o objdump_fuzz binutils/objdump.c ... -lbfd -liberty
afl-fuzz -i seeds/ -o out/ -- ./objdump_fuzz -d @@
```

理由：`objdump` 接受 file input，AFL++ 的 `@@` 機制最直接；`binutils` 已知有很多 parser bug，AFL++ 的豐富 mutation 策略對 binary format 最有效；不需要 in-process loop（每次 objdump 執行完自然結束）。

---

## 底層機制：Coverage 機制差異

```
AFL++ SHM bitmap：
  target → edge 觸發 → SHM[hash(prev ^ cur)]++ → parent 讀取
  ↑ 需要 instrumentation（或 QEMU/Frida emulation）

libFuzzer SanitizerCoverage：
  target → edge 觸發 → in-process counter++ → libFuzzer 直接讀
  ↑ 同一個 process，讀取成本 < 100ns

Honggfuzz BTS（Hardware）：
  CPU → 每個 branch 記錄到 CPU buffer → perf_event_open 讀取
  ↑ 不需要 instrumentation！覆蓋 closed-source binary
  ↑ 每次執行後讀取 buffer，有固定 overhead（~5μs）
```

---

## Seed 格式兼容性

AFL++ 和 libFuzzer 的 seed 格式**完全相同**：raw bytes 檔案，沒有 header。

```bash
# libFuzzer 產生的 corpus 可以直接給 AFL++ 用
# AFL++ 跑的 queue 可以直接給 libFuzzer 用

# 在 AFL++ 上繼續 libFuzzer 找到的有趣 input
cp libfuzzer_corpus/* afl_seeds/
afl-fuzz -i afl_seeds/ -o afl_out/ -- ./target @@
```

這讓「先用 libFuzzer 快速跑一遍，再用 AFL++ 深挖」的工作流成為可能。

---

## OSS-Fuzz 的選型策略

Google 的 OSS-Fuzz 運行了 1000+ 個開源 project 的 continuous fuzzing。觀察他們的選型：

- **絕大多數 project**：libFuzzer（因為 OSS-Fuzz 的基礎設施原生支援）
- **需要多樣性**：同時跑 AFL++ 和 libFuzzer（不同的 mutator 找到不同的 bug）
- **有 network interface 的 target**：Honggfuzz 或 AFL++ with custom harness
- **Closed-source component**：不在 OSS-Fuzz 範圍內（OSS-Fuzz 要求 source）

OSS-Fuzz 在 2022 年的分析顯示：對同一個 target，AFL++ 和 libFuzzer 各自找到約 30-40% 的 bug 是對方沒找到的。兩者並用比單用一個有顯著優勢。

---

## 對比與取捨

### AFL++ vs libFuzzer vs Honggfuzz 完整對比

| 特性 | AFL++ | libFuzzer | Honggfuzz |
|------|-------|-----------|-----------|
| 執行模型 | Fork-based（預設）/ Persistent mode | In-process（永遠） | Fork-based / Persistent mode |
| Coverage feedback | SHM bitmap（edge） | In-process counter（edge） | 多種：軟體/BTS/Intel PT |
| Binary-only target | QEMU mode / Frida mode | 不支援 | BTS mode（bare metal） |
| Network service | Desocket / AFL-Net / Preeny | 不支援（原生） | netdriver 直接支援 |
| ASAN 整合 | 需要分開編譯 | `-fsanitize=fuzzer,address`（一個 flag） | 需要分開編譯 |
| 速度（file-based） | 1000–5000 exec/sec | 10000–100000 exec/sec | 1000–5000 exec/sec |
| Crash 後行為 | Child 死，parent 繼續 | Process 重啟（有 overhead） | Child 死，parent 繼續 |
| OSS-Fuzz 整合 | 支援 | 原生 | 支援 |
| Harness 格式 | `@@` 或 stdin；`__AFL_LOOP` | `LLVMFuzzerTestOneInput` | `___FILE___`；`HF_ITER` |
| 生態豐富度 | 最高 | 高（LLVM 生態） | 中 |
| 適合 CLI tool | 最好 | 需要寫 harness | 可以，但 AFL++ 更直接 |
| 適合 library API | 需要 persistent mode | 最好 | 需要 persistent mode |
| 適合 network service | 需要額外工具 | 不適合 | 原生支援 |

---

## 踩雷集錦

1. **「libFuzzer 一定比 AFL++ 快」**：in-process 速度快，但 crash 後整個 process 死掉——libFuzzer 需要重啟，重啟時重新 load library、重新 JIT。對有複雜初始化的 library（TensorFlow、某些 codec），重啟成本可以吃掉速度優勢。AFL++ 的 persistent mode 在同一個 process 裡 loop，crash 只 kill 一次 iteration，不影響 parent。

2. **AFL++ 和 libFuzzer 的 harness 不能直接互換**：`LLVMFuzzerTestOneInput(data, size)` 和 `__AFL_LOOP(1000)` 語義不同。libFuzzer 的 harness 需要自己不修改 global state；AFL++ 的 persistent mode harness 需要在 loop 頭手動 reset state。把 libFuzzer harness 直接 compile 給 AFL++ 跑，行為不一定正確。

3. **Honggfuzz 的 BTS coverage 在 VM 裡不能用**：BTS（Branch Trace Store）是 Intel 硬體 feature，VM（VMware、VirtualBox、QEMU）預設不暴露這個 feature。在雲端 VM 或本地 VirtualBox 上跑 Honggfuzz 時要改用軟體 instrumentation mode（`hfuzz-clang` 編譯），不然 coverage 是空的，fuzzer 瞎跑。

4. **libFuzzer 在有 global state 的 library 上出現奇怪的 false positive**：某個 iteration 修改了 global allocator state，下一個 iteration 讀到髒的狀態，觸發的 crash 不是真正的 bug。診斷方法：把 crash input 單獨跑（不在 fuzzer loop 裡），如果不 crash，很可能是 state pollution。

5. **在 64-core 機器上，盲目開 AFL++ 64 instance，但都是 -M（主節點）**：`afl-fuzz -M` 只能有一個，其他都要用 `-S`（次節點）。多個 `-M` 互相搶奪寫入，產生 race condition，crash 資料可能損壞。正確做法：一個 `-M main`，其餘全部 `-S worker01`, `-S worker02`...

---

## 進階：再往深一層

### LibAFL：新一代 Fuzzer 框架

LibAFL（由 AFL++ 核心開發者維護）是 Rust 寫的 fuzzer 構建框架，讓你能自由組合 mutation、coverage、feedback 等元件：

```rust
// LibAFL 的設計：把 fuzzer 的每個元件換成 trait object
let mutator = StdScheduledMutator::new(havoc_mutations());
let executor = InProcessExecutor::new(&mut harness, observers, &mut fuzzer, &mut state, &mut mgr)?;
```

LibAFL 讓研究者可以精確控制 fuzzer 的每個決策，適合需要客製化 fuzzing 策略的研究場景。

### 選型決策樹

```
你的 target 是什麼？
│
├── 有 source code 的 library API
│   └── libFuzzer 首選（速度最快，OSS-Fuzz 整合好）
│
├── 有 source code 的 CLI tool / 檔案解析器
│   └── AFL++ 首選（最直接，@@/stdin 最方便）
│
├── 有 source code 的 network service
│   ├── 單連線：AFL++ + desocket
│   └── 多 message stateful：Honggfuzz netdriver 或 AFL-Net
│
├── 沒有 source code 的 binary
│   ├── x86_64 Linux：AFL++ QEMU mode 或 Frida mode
│   ├── ARM64：AFL++ Frida mode
│   └── Intel bare metal：Honggfuzz BTS mode
│
└── Kernel module / syscall interface
    └── Syzkaller（不要用這三個）
```

---

## 動手練習

### 練習 1：同一個 target 用 AFL++ 和 libFuzzer 各跑 30 分鐘，比較 coverage

```c
// parse_target.c：一個簡單的 binary parser
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

int parse(const uint8_t *data, size_t size) {
    if (size < 8) return -1;
    uint32_t magic = *(uint32_t*)data;
    if (magic != 0x46555A5A) return -1;  // "FUZZ"
    uint32_t len = *(uint32_t*)(data + 4);
    if (len > size - 8) return -1;
    // 處理 payload
    uint8_t checksum = 0;
    for (size_t i = 8; i < 8 + len; i++) {
        checksum ^= data[i];
    }
    if (len > 64) {
        // 模擬一個 integer overflow
        uint8_t buf[64];
        memcpy(buf, data + 8, len);  // overflow!
    }
    return checksum;
}

// AFL++ 版本（stdin）
#ifdef AFL_BUILD
int main(void) {
    uint8_t buf[4096];
    size_t n = fread(buf, 1, sizeof(buf), stdin);
    parse(buf, n);
    return 0;
}
#endif

// libFuzzer 版本
#ifdef LIBFUZZER_BUILD
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    parse(data, size);
    return 0;
}
#endif
```

```bash
# AFL++ build
afl-clang-fast -DAFL_BUILD -fsanitize=address -o parse_afl parse_target.c
mkdir seeds_afl && echo "FUZZAAAA" > seeds_afl/seed1.bin
timeout 1800 afl-fuzz -i seeds_afl/ -o out_afl/ -- ./parse_afl &

# libFuzzer build
clang -DLIBFUZZER_BUILD -fsanitize=fuzzer,address -o parse_libfuzzer parse_target.c
mkdir seeds_lf && echo "FUZZAAAA" > seeds_lf/seed1.bin
timeout 1800 ./parse_libfuzzer seeds_lf/ -artifact_prefix=lf_crashes/ &

# 30 分鐘後比較
wait
cat out_afl/default/fuzzer_stats | grep total_edges
# libFuzzer 的 coverage：在程式輸出裡找 "cov: XXXX"
```

### 練習 2：把 libFuzzer corpus 給 AFL++ 繼續跑

```bash
# libFuzzer 跑完後，corpus 在執行目錄下
# 把 libFuzzer 找到的有趣 input 給 AFL++

mkdir afl_from_lf_seeds/
cp ./seeds_lf/                 # 原始 seed
cp ./*.bin                     # libFuzzer 輸出的有趣 input（非 crash）
ls | grep -v crash | xargs -I{} cp {} afl_from_lf_seeds/

afl-fuzz -i afl_from_lf_seeds/ -o out_afl_continued/ -- ./parse_afl &
# 觀察：從 libFuzzer 的 corpus 開始，AFL++ 能否找到更多 edge
```

---

## 本章重點整理

- **三者設計哲學各異**：AFL++（高可配置，file-based）、libFuzzer（in-process，library API，開發者體驗最好）、Honggfuzz（多 coverage 來源，網路/硬體 trace）——選型依 target 特性，不存在「最強」工具。
- **Speed ≠ Effectiveness**：libFuzzer 速度最快，但 crash 重啟有成本，global state 問題在複雜 library 上真實存在；在 24 小時的 campaign 結束時，coverage 差距往往比 1 小時時小得多。
- **Seed 格式兼容，可以串聯**：libFuzzer 和 AFL++ 都用 raw bytes，先用 libFuzzer 快速跑，再把 corpus 給 AFL++ 深挖，是 OSS-Fuzz 的實際做法之一。

---

## 自我檢核

1. 你要 fuzz 一個靜態連結的 ARM64 Android binary（沒有 source code）。三個工具裡哪個能用？為什麼？
2. libFuzzer 跑了 6 小時後，`execs_per_sec` 從 50000 下降到 5000。最可能的原因是什麼？
3. 你把 libFuzzer 的 harness（`LLVMFuzzerTestOneInput`）直接用 `afl-clang-fast` 編譯，加上 `main()` wrapper，跑 AFL++。結果 AFL++ 的 coverage 很低。問題在哪裡？
4. Honggfuzz 在一台 CloudVM（KVM）上跑，你選了 BTS coverage mode。執行後 coverage 完全沒有增加。診斷步驟是什麼？
5. OSS-Fuzz 同時跑 AFL++ 和 libFuzzer 在同一個 target 上。各自找到一組 bug，兩組只有 60% 重疊。為什麼兩個工具找到不同的 bug？

---

## 延伸閱讀

- **libFuzzer 文件（https://llvm.org/docs/LibFuzzer.html）**
  核心貢獻：LLVM 官方的 libFuzzer 完整文件，包含 harness 設計、`FuzzedDataProvider`、structured fuzzing、corpus management 的所有細節。
  讀哪裡：「Fuzzer Usage」到「Corpus」的完整章節（約 10 分鐘）；「Tips」那一節有很多實戰 hack。
  和本章關聯：本章 libFuzzer 段落的技術來源；理解 `LLVMFuzzerInitialize`、`LLVMFuzzerCustomMutator` 等進階接口。

- **Honggfuzz GitHub README（https://github.com/google/honggfuzz）**
  核心貢獻：官方文件，說明四種 coverage mode 的使用條件、`hfuzz-clang` 的用法、network fuzzing 的完整設定。
  讀哪裡：README 的「Coverage feedback」和「Netdriver」章節；`docs/USAGE.md` 有 flag 的完整說明。
  和本章關聯：Honggfuzz 的 BTS/Intel PT 要求、VM 限制的官方說明。

- **"FuzzBench: An Open Fuzzer Benchmarking Platform and Service"（Metzman et al., FSE 2021）**
  核心貢獻：在 20+ 個 real-world target 上量化比較 AFL++、libFuzzer、Honggfuzz 等 10+ 個 fuzzer 的 coverage 和 bug 發現率，有豐富的 target-level 細節。
  讀哪裡：Table 2（target 描述）和 Figure 3-5（coverage 曲線和排名）；附錄裡有每個 target 的詳細結果。
  和本章關聯：「哪個工具在什麼場景下贏」的實證依據；確認本章選型建議的外部驗證。

---

→ 下一章：[Final Project — 實戰 Fuzzing Campaign](final-project-real-target-campaign.md)
