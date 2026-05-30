# AFL++ 原理深解：從 bitmap 到 CmpLog，看懂 coverage-guided fuzzer 在想什麼

> 給想徹底搞懂 AFL++ 內部機制、能看懂 fuzzing 論文、能做 fuzzer 工具選型的工程師。

AFL++ 不是一個「裝好跑起來就好」的黑盒工具——它的每一個設計決策都有理由，而理解那些理由才能在真實目標上把它用到極致。這門課從 edge coverage bitmap 的底層表示，一路拆解到 CmpLog/RedQueen 的 feedback 機制、LTO 插樁的 compile-time 行為、以及 power schedule 背後的 Multi-Armed Bandit 模型，讀完你能看懂 fuzzing 論文的 methodology section，也能在選工具時說出「為什麼這個 target 用 libFuzzer 比 AFL++ 更合適」而不只是猜測。

---

## 為什麼學這個？

1. **工具選型**：AFL++、libFuzzer、Honggfuzz、Jazzer——四個工具在不同 target 上的表現差距可達 10 倍以上，選錯方向你只是在燒 CPU 而不是在找 bug。理解底層機制才能在看到一個新 target 時推理出最適合的配置。

2. **讀懂論文**：大量 security 研究建立在 AFL 家族上（REDQUEEN、AFL-Net、Winnie、SnapFuzz...），論文的 evaluation 章節假設讀者知道 bitmap collision、edge tuples、p_schedule 是什麼。不懂底層你只能跳過這些段落。

3. **知道邊界**：coverage-guided fuzzing 有它找不到的 bug——magic bytes、CRC checksum、深層狀態機。知道邊界比不知道更值錢，因為你能決定何時要換工具或補上 symbolic execution。

4. **職涯價值**：軟體供應鏈安全、CVE hunting、產品安全團隊——這三個方向都在大量使用 fuzzing。能寫 custom mutator、能分析 coverage 瓶頸、能設計 harness 的工程師，市場上比只會 `afl-fuzz -i in -o out` 的人稀缺得多。

---

## 先修知識

| 知識點 | 需要的程度 |
|--------|-----------|
| C 程式設計 | 熟：能讀 C source，理解指標、記憶體布局、undefined behavior |
| Linux syscall 基礎 | 懂 `fork`/`exec`/`mmap`/`ptrace` 的語意，不需要自己實作 |
| 編譯工具鏈 | 用過 `gcc`/`clang`，知道 `-O2 -g` 這類 flag 在做什麼 |
| ELF 格式 | 知道 .text/.bss/PLT/GOT 是什麼（Part 2 會用到） |
| fuzzing 經驗 | **不需要**，從零開始 |

---

## 課程地圖

### Part 1 — 入門與脈絡（Ch 0–4）

| 檔案 | 主題 |
|------|------|
| [Ch 0 — 環境搭建](./00-environment-setup.md) | 從原始碼 build AFL++、第一個 session、status screen 解讀 |
| [Ch 1 — Fuzzing 流派](./01-fuzzing-landscape.md) | Blackbox / Grammar / Coverage-guided 三流派比較與歷史 |
| [Ch 2 — AFL 家族樹](./02-afl-family-tree.md) | AFL → AFL++ 的演化脈絡，各分支解決了什麼問題 |
| [Ch 3 — AFL++ 架構總覽](./03-afl-plus-plus-architecture.md) | 主要元件（afl-fuzz、afl-cc、forkserver）的交互關係 |
| [Ch 4 — Source Tree 導覽](./04-source-tree-walkthrough.md) | 原始碼目錄結構，每個重要目錄的職責 |
| [Practice A — 第一個 Session](./practice-a-first-session.md) | 端對端操作：選 target、編譯、跑、讀輸出 |

### Part 2 — 插樁機制（Ch 5–9）

| 檔案 | 主題 |
|------|------|
| [Ch 5 — Edge Coverage Bitmap](./05-edge-coverage-bitmap.md) | 64KB bitmap、edge tuple、hash collision 的影響 |
| [Ch 6 — Compile-time Instrumentation](./06-compile-time-instrumentation.md) | afl-clang-fast、SanitizerCoverage、LLVM pass 插樁流程 |
| [Ch 7 — LTO 深挖](./07-lto-deep-dive.md) | afl-clang-lto 的 link-time 插樁，為何能減少 collision |
| [Ch 8 — Runtime Instrumentation](./08-runtime-instrumentation.md) | QEMU mode、Frida mode、與 compile-time 的取捨 |
| [Ch 9 — Forkserver](./09-forkserver.md) | forkserver 協議、共享記憶體 bitmap、snapshot fuzzing |
| [Practice B — 插樁方式比較](./practice-b-instrumentation-comparison.md) | 同一 target 用四種插樁跑，比較 exec/s 與 coverage |

### Part 3 — 語料庫與變異（Ch 10–14）

| 檔案 | 主題 |
|------|------|
| [Ch 10 — Corpus 生命週期](./10-corpus-lifecycle.md) | 種子選擇、最小化、trim、佇列排程 |
| [Ch 11 — 變異策略](./11-mutation-strategies.md) | deterministic mutations vs havoc，每種策略的設計邏輯 |
| [Ch 12 — Power Schedule](./12-power-schedule.md) | AFLFast 的能量分配模型，Multi-Armed Bandit 的連結 |
| [Ch 13 — Dictionary](./13-dictionary.md) | token 字典、自動 token 提取、extras/autoextras |
| [Ch 14 — Crash 語意](./14-crash-semantics.md) | crash vs hang vs timeout，重複 crash 去重，triage 流程 |
| [Practice C — Corpus Triage](./practice-c-corpus-triage.md) | 從 crash 佇列到可重現 PoC 的完整流程 |

### Part 4 — 進階技術（Ch 15–20）

| 檔案 | 主題 |
|------|------|
| [Ch 15 — CmpLog / RedQueen](./15-cmplog-redqueen.md) | 比較指令 logging、input-to-state 對應、magic bytes 突破 |
| [Ch 16 — Persistent Mode](./16-persistent-mode.md) | `__AFL_LOOP`、in-process fuzzing、速度提升原理 |
| [Ch 17 — Harness 設計](./17-harness-design.md) | 好的 harness 特徵，常見反模式，coverage 瓶頸診斷 |
| [Ch 18 — Custom Mutator](./18-custom-mutator.md) | AFL_CUSTOM_MUTATOR_LIBRARY API，寫一個 grammar-aware mutator |
| [Ch 19 — Sanitizers](./19-sanitizers.md) | ASan/UBSan/MemorySanitizer 的配合，overhead 與 tradeoff |
| [Ch 20 — 平行 Fuzzing](./20-parallel-fuzzing.md) | `-M`/`-S` 的語意、corpus 同步機制、多機器部署 |

### Part 5 — 實戰與評估（Ch 21–24 + Final）

| 檔案 | 主題 |
|------|------|
| [Ch 21 — 困難 Target](./21-difficult-targets.md) | 網路協議、GUI 程式、有狀態服務的 fuzzing 策略 |
| [Ch 22 — Crash Triage](./22-crash-triage.md) | GDB 自動化分析、exploitability 評分、`exploitable` 插件 |
| [Ch 23 — 效果評估](./23-measuring-effectiveness.md) | coverage metrics、時間-coverage 曲線、統計顯著性 |
| [Ch 24 — Fuzzer 比較](./24-fuzzer-comparison.md) | AFL++ vs libFuzzer vs Honggfuzz：在不同 target 類型上的取捨 |
| [Final Project — 真實 Target Campaign](./final-project-real-target-campaign.md) | 從選 target 到提交 PoC 的完整 fuzzing campaign |

---

## 學習方式建議

1. **手邊開 AFL++ source code**：每一章講到一個機制，就去 `src/` 找對應的函式。光讀講解不夠，看到 `afl_realloc()` 怎麼處理 bitmap 才算真的懂。推薦用 `ctags` 或 VSCode + clangd 讓跳轉變快。

2. **翻論文而非只翻 manual**：AFL++ manual 告訴你「怎麼用」，論文（AFLFast、REDQUEEN、CollAFL）告訴你「為什麼這樣設計」。兩者互補，manual 是操作手冊，論文是設計文件。

3. **不要跳 Part 2**：很多人跳過插樁機制直接跳 Part 4 的進階技巧，然後永遠搞不懂為什麼 LTO 比 LLVM mode 好、為什麼 bitmap collision 在大程式裡是真問題。Part 2 是這門課的技術核心，其他部分都建立在它上面。

---

## 精選資料庫

### 必讀基礎

- **[AFL++ GitHub](https://github.com/AFLplusplus/AFLplusplus)** — 官方 repo，`docs/` 目錄有大量設計說明
- **[AFL technical details](https://lcamtuf.coredump.cx/afl/technical_details.txt)** — lcamtuf 寫的原始設計文件，bitmap 設計的第一手資料
- **[AFL++ WOOT 2020 Paper](https://www.usenix.org/conference/woot20/presentation/fioraldi)** — Fioraldi et al.，AFL++ 的正式學術論文，解釋各模組如何整合

### 推薦論文

- **[AFLFast (CCS 2016)](https://dl.acm.org/doi/10.1145/2976749.2978428)** — Böhme et al.，Power schedule 的起源，Multi-Armed Bandit 模型引入 fuzzing
- **[REDQUEEN (NDSS 2019)](https://www.ndss-symposium.org/ndss-paper/redqueen-fuzzing-with-input-to-state-correspondence/)** — Aschermann et al.，CmpLog 的學術版本，input-to-state 對應技術
- **[CollAFL (S&P 2018)](https://ieeexplore.ieee.org/document/8418631)** — Gan et al.，分析 AFL bitmap collision 問題並提出解法
- **[Evaluating Fuzz Testing (CCS 2018)](https://dl.acm.org/doi/10.1145/3243734.3243804)** — Klees et al.，為什麼 fuzzing benchmark 很難做到公平，每個做 fuzzing 研究的人都應該讀

### 推薦部落格 / 文章

- **[lcamtuf's blog](https://lcamtuf.blogspot.com/)** — AFL 作者的技術部落格，很多設計決策的第一手說明
- **[The Fuzzing Book](https://www.fuzzingbook.org/)** — Zeller et al.，可執行的 fuzzing 教科書，Python 實作，概念解說一流
- **[Fuzzing with AFL workshop notes](https://github.com/ThalesIgnite/afl-training)** — 實戰導向的操作指南，適合對照本課的動手練習

### 讀完本課之後

讀完 AFL++ 底層後，以下三個方向可以繼續深入：

- **[LibAFL](https://github.com/AFLplusplus/LibAFL)**：用 Rust 寫的 fuzzer 框架，AFL++ 的繼任者，代表 fuzzer 架構的下一個世代
- **[Honggfuzz 原始碼](https://github.com/google/honggfuzz)**：特別是它的 `libhfuzz/` 目錄，看看另一個成熟 fuzzer 怎麼做插樁
- **[LLVM Pass 開發](https://llvm.org/docs/WritingAnLLVMPass.html)**：AFL++ 的插樁是一個 LLVM pass，學會寫 pass 就能客製化 coverage 的定義

---

→ 從 [Ch 0 — 環境搭建](./00-environment-setup.md) 開始
