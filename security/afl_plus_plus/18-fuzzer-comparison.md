# Ch 18 — AFL++ vs libFuzzer vs Honggfuzz：設計哲學對比

> 目標：用 in-process vs out-of-process、SanCov vs 自家 instrumentation、mutation strategy、ergonomic 四個維度對三者打分；給出「target 長這樣 → 挑這個」的決策表。

## 三家的定位一句話

- **AFL++**：**CLI binary 的瑞士刀**。功能最全、社群最熱、flag 最多。
- **libFuzzer**：**library API 的 in-process fuzz harness**。綁 LLVM、OSS-Fuzz 主力。
- **Honggfuzz**：**特殊場景的萬金油**。Intel PT、persistent raw、各種 exotic mode。

下面從技術維度細比。

## 維度 1：in-process vs out-of-process

### libFuzzer：in-process

libFuzzer 跑在 target 的 process 裡。你寫一個 harness：

```c
#include <stdint.h>
#include <stddef.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    my_parser(data, size);
    return 0;
}
```

用 `clang -fsanitize=fuzzer,address` 編。跑起來後，fuzzer loop 和 target 在**同一個 process**，沒有 fork、沒有 exec：

```c
// libFuzzer 內部（簡化）
while (true) {
    uint8_t *input = mutate();
    LLVMFuzzerTestOneInput(input, size);   // 直接呼叫
    check_coverage();
}
```

**優勢**：
- 最高 throughput，省下所有 IPC / fork 成本（百萬 exec/s 不稀奇）。
- 和 sanitizer、LLVM 工具鏈緊密整合。
- 可以用 `DoFuzzerInit` 做昂貴 setup，持續存活。

**劣勢**：
- target crash → **整個 fuzzer 死**。得靠 ASan 的 signal handler continue（有時候 continue 不乾淨）。
- target 有 global state → 會在 iteration 間累積，和 persistent mode 一樣的問題但更嚴重（因為沒有 fork 可以 reset）。
- 只能 fuzz「library 式」target — 必須有一個能單獨呼叫的函式 entry。純 CLI binary 硬要接需要寫複雜 harness。

### AFL++：out-of-process（但可 in-process）

預設 AFL++ 是 out-of-process：fuzzer 和 target 是兩個 process，用 forkserver + shmem 溝通。

- 每次 iteration 一個乾淨 child（基本版）。
- Persistent mode（Ch 13）下同一 child 跑 N 次後 fork 新的。

**優勢**：
- crash 隔離：child 掛了 fuzzer 繼續跑。
- 不需要 harness — 直接 fuzz CLI binary 即可（target 從 stdin / file 讀）。
- 可用 QEMU / Frida / Unicorn mode 做 binary-only fuzzing。

**劣勢**：
- 沒 persistent mode 時 throughput 被 fork 限制。
- IPC 成本（shmem、pipe）。
- 要跟 sanitizer 結合時，要先確定它們相容（多數情況 OK）。

### Honggfuzz：混合

Honggfuzz 支援兩種模式：
- persistent mode：類似 AFL++ persistent，同 process 跑 N 次。
- fork mode：每 iteration 一個 child。

另外有 **Intel PT mode**（硬體 branch trace）— target 不插樁，CPU 幫你 trace。這是 binary-only fuzzing 的神兵，但需要特定硬體。

## 維度 2：Instrumentation 策略

### libFuzzer

用 LLVM **SanitizerCoverage**（SanCov）的 `trace-pc-guard` 或 `inline-8bit-counters`：

- `trace-pc-guard`：每個 basic block 開頭插 `__sanitizer_cov_trace_pc_guard(&guard)`，libFuzzer 提供 runtime。
- `inline-8bit-counters`：直接 inline 每個 block 的 counter `++`，更快。
- Compare hooks：`trace-cmp` 讓 libFuzzer 知道 compare operand，功能類似 AFL++ CmpLog。

Bitmap 的概念差不多，但 libFuzzer **直接用 8-bit counter array**，不走 XOR edge ID — edge coverage 較差（其實更像 block coverage + 次數）。依賴 `-use_value_profile=1` 才啟用類 AFL 的 edge 模擬。

### AFL++

自家 LLVM pass + PCGUARD + LTO。核心同 Ch 4/5 講的 XOR edge coverage。整合了 laf-intel、CmpLog、auto-dict 等。

**AFL++ 的 instrumentation 生態是目前最豐富的**。LTO mode 的 collision-free 是其他 fuzzer 沒有的（Honggfuzz 部分支援）。

### Honggfuzz

也支援多種 instrumentation：
- 軟體插樁（類 AFL）
- Intel PT：硬體 branch trace，無需重編
- QEMU mode
- CoreSight（ARM 硬體 trace）

Intel PT 是 Honggfuzz 的招牌。限制：需要 Intel Skylake+ 和特定 kernel 支援。

## 維度 3：Mutation strategy

### libFuzzer

內建 byte-level mutator：bit flip、byte replace、chunk copy、chunk insert、crossover 等。沒有 deterministic 階段，都是 havoc-style 隨機組合。

custom mutator 支援 `LLVMFuzzerCustomMutator`，但不如 AFL++ 靈活。

沒有內建 CmpLog 概念，但 `-use_cmp=1` / `value profile` 開啟類似 I2S。

### AFL++

最豐富：det + havoc + splice + MOpt + CmpLog/redqueen + auto-dict + custom mutator + grammar mutators + honggfuzz-style mutator。

**Mutation 這一塊 AFL++ 領先明顯**。

### Honggfuzz

中規中矩的 havoc-style mutator。有 custom mutator API。

## 維度 4：Ergonomics / 生態

### libFuzzer

- **生態**：和 LLVM 一起發佈，和 Clang / OSS-Fuzz 完美整合。Google 的大量 fuzz target 都用它。
- **學習曲線**：最低 — 寫 `LLVMFuzzerTestOneInput` 就能跑。
- **CI 整合**：OSS-Fuzz 支援絕佳。
- **Cross-platform**：Linux / macOS / Windows 都 OK。

### AFL++

- **生態**：活躍的 community fork，docs 多、example 多、論文多。
- **學習曲線**：中 — flag 很多、env 很多，但基本用法簡單。
- **CI 整合**：OSS-Fuzz 支援，但不如 libFuzzer 深。
- **Cross-platform**：Linux 主場，其他平台相對弱。

### Honggfuzz

- **生態**：Google 維護但使用者少於前兩者。
- **學習曲線**：中。
- **CI 整合**：OSS-Fuzz 支援。
- **Cross-platform**：OK，但 Intel PT / CoreSight 等 exotic feature 只在特定平台。

## 綜合對比表

| 維度 | AFL++ | libFuzzer | Honggfuzz |
|---|---|---|---|
| 模式 | out-of-process（+ persistent） | in-process | 混合 |
| Throughput | 中到高 | 極高 | 中到高 |
| Crash 隔離 | 好（child fork） | 差（需 ASan） | 中 |
| Instrumentation 豐富度 | ★★★★★ | ★★★ | ★★★★（+Intel PT） |
| Mutation 豐富度 | ★★★★★ | ★★★ | ★★★ |
| Binary-only 支援 | QEMU / Frida / Unicorn | 基本沒有 | Intel PT |
| Custom mutator | 完整 API | 有但較弱 | 有 |
| CmpLog-style | ★★★★ | ★★★（value profile） | ★★ |
| Sanitizer 整合 | 好 | 最好 | 好 |
| Parallel | ★★★★★（queue sync） | ★★（內建 N-process） | ★★★ |
| 學習曲線 | 中 | 低 | 中 |
| 社群 / docs | ★★★★★ | ★★★★ | ★★★ |

## 該挑哪個？決策表

| target 類型 | 建議 |
|---|---|
| Library with clean API（parse_X(buf, size)） | **libFuzzer**（in-process throughput + OSS-Fuzz 整合） |
| CLI binary 有 source | **AFL++**（直接跑，不用寫 harness） |
| Closed-source binary | **AFL++ QEMU/Frida mode** 或 **Honggfuzz Intel PT** |
| Network protocol parser | **AFL++ + custom mutator + CmpLog**（對 magic 多的 protocol 特別好） |
| Browser / JS engine | **libFuzzer**（integrated with V8/JSC）或 **AFL++ + grammar mutator** |
| Kernel / driver | **Nyx** / syzkaller（本書外，但 AFL++ 有 Nyx mode） |
| 希望用 OSS-Fuzz 掛上 | **libFuzzer** 優先，AFL++ 次之 |
| 想多種策略 swarm | **AFL++**（parallel sync 生態最好） |

## 混合策略

真實的大型 project 常**同時跑 AFL++ 和 libFuzzer**，各取所長：

- libFuzzer 跑 in-process throughput 高的 harness。
- AFL++ 跑帶 ASan、帶 cmplog 的 out-of-process 版本。
- 兩邊的 corpus 互相 share。

OSS-Fuzz 本身就是這樣設計的。

## 本書 wrap up

走到這一章你應該有能力：

- 打開 AFL++ 原始碼不懵，能找到關鍵檔案。
- 讀 fuzzing paper（AFLFast、REDQUEEN、CollAFL）時知道它們在解什麼。
- 看到 flag（`-p fast`、`-c cmplog`、`-M main`）不用每次 Google。
- 拿到 target 時能判斷該用哪個 fuzzer、該開哪些 sanitizer、該不該開 CmpLog。

接下來的路徑：

1. **讀 source code**：把 `afl-fuzz-one.c` 和 `afl-fuzz-bitmap.c` 過一遍，章節知識和實作對應起來會 solid 很多。
2. **讀 AFL++ WOOT 2020 paper**：前面提過的那篇，是最好的綜述。
3. **跑一個真 target**：選一個你感興趣的 open source parser（libpng、libjpeg-turbo、libxml2），用 AFL++ 跑跑看，實際觀察 queue 成長、嘗試 parallel、triage crash。
4. **讀論文**：REDQUEEN、AFLFast、MOpt、Fuzzilli 這些 classic。
5. **看進階 mode**：Nyx、kAFL（kernel fuzzing）、結合 symbolic execution（SymCC、DrillerLM）等。

Fuzzing 這個領域每年都在動。這本書給你的是**目前為止穩定下來的基本原理**，未來幾年還會再有新東西。但這些骨幹應該夠你讀進任何新 paper。

## 自我檢核

- [ ] 能用一句話說出 AFL++ / libFuzzer / Honggfuzz 各自的定位
- [ ] 能解釋 in-process vs out-of-process 在 crash isolation 和 throughput 上的 tradeoff
- [ ] 能說出 libFuzzer 用 SanitizerCoverage、AFL++ 用自家 LLVM pass 的差異
- [ ] 對給定 target 能選出適合的 fuzzer
- [ ] 知道 AFL++ 和 libFuzzer 可以混合部署

全系列完。回到 [README](./README.md) 看課程地圖。
