# Ch 2 — AFL 家譜：從 AFL 到 AFL++ 的分裂與合流

> 目標：串起 AFL (Zalewski) → AFLFast → AFLGo → AFL++ 的歷史；說明 AFL++ 作為 community fork 合併了哪些論文的 idea；同場看 libFuzzer / Honggfuzz 的不同哲學。

## 為什麼要懂歷史

AFL++ 今天長這個樣子，不是一個總設計師坐下來 top-down 畫出來的 — 是十幾年累積的論文 idea 和 community patch 層層疊上去的。許多讓你困惑的 flag 和環境變數，本質上是某個研究方向的遺跡。知道歷史你就知道為什麼 `-p fast` 叫 fast、為什麼有個東西叫 laf-intel、為什麼 `-M` 和 `-S` 這樣分工。

## 原版 AFL（2013–2017，Zalewski）

**作者**：Michał Zalewski（lcamtuf），Google 安全團隊。

**關鍵 commit 時間線**：

| 年份 | 里程碑 |
|---|---|
| 2013 | 初版 AFL，assembly-level instrumentation |
| 2015 | 引入 forkserver、shared memory bitmap |
| 2015–2016 | llvm_mode、persistent mode、deferred forkserver |
| 2017 | `afl-tmin`、`afl-cmin`、dictionary 支援完善 |
| 2017-ish | Zalewski 幾乎停止維護 |

原版 AFL 的核心貢獻是三件事，今天所有 coverage-guided fuzzer 都在這三塊上打轉：

1. **Edge coverage via instrumentation**（Ch 4）
2. **Forkserver 模型**（Ch 7）
3. **簡單但高效的 deterministic + havoc mutation**（Ch 9）

這三樣就是後面所有延伸的基礎。官方 technical whitepaper `https://lcamtuf.coredump.cx/afl/technical_details.txt` 非常值得一讀，短、有觀點。

## 學術分支：AFLFast、AFLGo、Angora...

AFL 開源後，學術界把它當 playground 疊了一堆變體：

### AFLFast（CCS 2016，Böhme et al.）

**觀察**：AFL 對每個 queue entry 分配的 fuzzing energy 太公平。但實務上 99% 的 seed 走的是 high-frequency 熱門路徑，真正能找到新東西的是少數 low-frequency 路徑。

**改動**：引入 **power schedule**（能量排程）。依 seed 走過路徑的頻率與 depth 決定給它多少 fuzzing 回合。頻率低、深度淺的優先 — 也就是 FAST schedule。

這是 AFL 歷史上第一個系統性的 scheduling 改進。後來的 AFL++ 把 FAST / COE / EXPLORE / QUAD 都納入（Ch 10）。

### AFLGo（FSE 2017，Böhme et al.）

**觀察**：有些時候你不是想 fuzz 整個 program，而是想找到特定的 target location（例如 patch 過的函式、或某條 CVE-related path）。

**改動**：**Directed fuzzing**。計算每個 queue entry 到目標位置的 CFG 距離，距離近的多分能量。這在 patch testing、regression fuzzing 有明確用途。

### Angora（S&P 2018，Chen & Chen）

**改動**：引入 **taint tracking** 和 **gradient descent** — 對 branch condition 做類型推斷，用梯度法猜出能翻轉 branch 的 byte 值。技術漂亮但 overhead 高。

### CollAFL（S&P 2018，Gan et al.）

**觀察**：AFL 的 64KB bitmap 會 hash collision — 兩條不同 edge 映到同 bucket，fuzzer 就以為沒有新 coverage。

**改動**：**在編譯期消 collision**，給每條 edge 一個保證唯一的 ID。這個 idea 後來變成了 AFL++ 的 **LTO mode**（Ch 5）。

### laf-intel（2016，個人 blog）

**觀察**：`if (x == 0xDEADBEEF)` 這種 4-byte magic check 對 AFL 是地獄，因為只有全對才進新 branch，bitmap 看不見漸進。

**改動**：**把 compound compare 拆成 byte-wise**。編譯期把 `x == 0xDEADBEEF` 改寫成 `(x>>24)==0xDE && (x>>16)==0xAD && ...`，每個 byte 命中都是一個新 edge。

laf-intel 是個人匿名作者，idea 漂亮到後來的 AFL++ 直接內建 `compare-transform-pass`。

## 工業分支：libFuzzer、Honggfuzz

同時期兩個工業級 fuzzer 走了不同路：

### libFuzzer（Google，~2015 起）

**哲學**：**in-process**。不 fork，target 的 `LLVMFuzzerTestOneInput(uint8_t *data, size_t size)` 在同個 process 裡被呼叫數百萬次。搭 LLVM 的 SanitizerCoverage 做 instrumentation。

**優勢**：快（無 fork overhead）、深入整合 LLVM。
**缺點**：target crash 就整個 fuzzer 死 — 只能靠 ASan 撿起來 continue；不適合有大量全域狀態的 target。

libFuzzer 是現在 LLVM / Chrome / OSS-Fuzz 的主力。

### Honggfuzz（Google Robert Swiecki）

**哲學**：**靈活的 instrumentation 來源**。可用 software instrumentation（類 AFL）、**Intel PT**（硬體級 trace，binary-only）、或純 blackbox。

**優勢**：Intel PT mode 讓你不用 source 就能做 coverage-guided（不需要 QEMU 的 overhead）。
**缺點**：Intel PT 要 kernel、CPU 都支援；軟體 instrumentation 生態不如 AFL++ 豐富。

## 斷層與 fork：AFL++ 的誕生

2017–2019 之間原版 AFL 基本停擺，但學術界論文還在爆炸式增加。社群三個主力（Marc "van Hauser" Heuse、Andrea Fioraldi、Dominik Maier、Heiko Eißfeldt）在 2019 宣布 fork，命名 AFL++：「**AFL, combining incremental steps of fuzzing research**」。

他們的策略不是「重做一個」，而是**把散在各處的論文 idea 整合進同一套程式**：

| AFL++ feature | 來源 |
|---|---|
| `-p fast` / `-p coe` power schedule | AFLFast |
| LTO collision-free | CollAFL |
| CmpLog / `-c` | REDQUEEN (NDSS 2019) |
| compare-transform-pass | laf-intel |
| MOpt mutator | MOpt (USENIX 2019) |
| QEMU mode 擴充 | 原 AFL 加 TriforceAFL |
| Ngram / Ctx instrumentation | 多篇 context-sensitive fuzzing 論文 |
| Custom mutator API | 自製，承襲 libFuzzer 的 harness 思想 |
| Grammar mutator | Grammarinator、Gramatron |
| Nyx mode | Nyx: Greybox Hypervisor-based Fuzzing (USENIX 2021) |

這份表是本書的地圖。後面章節幾乎都會提到其中一項。

參考論文：Fioraldi et al., **"AFL++: Combining Incremental Steps of Fuzzing Research"**, WOOT 2020。讀一遍你就能看懂 AFL++ 為什麼選擇這樣長。

## 今天的局勢

2024 年後的 coverage-guided fuzzer 現場大致這樣：

- **AFL++**：最大的功能雜食者，社群活躍，是 academic prototype 的首選整合點。
- **libFuzzer**：in-process 場景首選，和 OSS-Fuzz 深度綁。LLVM 主線維護。
- **Honggfuzz**：特殊場景（Intel PT、persistent mode with raw syscall 等）有優勢。
- **Nyx / kAFL**：kernel / hypervisor 領域新勢力，snapshot-based。
- **Fuzzilli**：JS engine 專用 grammar fuzzer。
- **Jackalope / WinAFL**：Windows 生態專用分支。

AFL++ 的定位像是 **Linux 發行版裡的 Ubuntu** — 不一定每個細節最尖端，但整合度最好、學習資源最多。這也是我們選它來學的原因。

## 常見誤解

- **「AFL 已經死了」**：原版 AFL 確實停擺，但 fork 而成的 AFL++ 非常活躍，今天 security 圈講 AFL 多半是指 AFL++。
- **「AFL++ 把所有 feature 都打開最好」**：不。CmpLog 會慢、Ngram 會撐大 bitmap、LTO 需要特別的 linker。學會挑。
- **「libFuzzer 和 AFL++ 二選一」**：兩者定位不同。library-style API 去寫 harness 做 in-process 就選 libFuzzer，CLI binary fuzzing 就選 AFL++。大專案通常兩個並行。

## 自我檢核

- [ ] 能說出 AFLFast、CollAFL、REDQUEEN、laf-intel 各解了什麼問題
- [ ] 記得 AFL++ 是 community fork，不是 Google 官方
- [ ] 能分辨 AFL++（out-of-process）、libFuzzer（in-process）、Honggfuzz（多模式）三者的定位
- [ ] 知道 AFL++ 的 WOOT 2020 paper 是張地圖，值得一讀

下一章把這些散落的組件拼進一張大圖 — AFL++ 的整體架構長什麼樣。

→ [Ch 3 AFL++ 架構總覽](./03-afl-plus-plus-architecture.md)
