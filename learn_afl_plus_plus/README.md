# AFL++ 原理深解：從 bitmap 到 CmpLog，看懂 coverage-guided fuzzer 在想什麼

> 給用過 AFL++ 但只停留在 `afl-fuzz -i in -o out -- ./target`、想徹底搞懂它內部機制的工程師。

這是一系列走原理路線的教學文章，不做 fuzzing 專案、不復現 CVE — 目標是讓你打開 AFL++ 原始碼不會懵，也能解釋清楚為什麼要選 LTO instrumentation、persistent mode 怎麼能快 10x、CmpLog 在解什麼問題。

## 為什麼學這個？

- **看懂你天天按的按鈕**：你會下 `afl-clang-fast`、會 `-M`/`-S`，但為什麼要這樣？LTO 和 PCGUARD 差在哪？deterministic 為什麼預設關掉？讀完這系列你會有答案。
- **讀得懂 fuzzing 論文**：AFLFast / REDQUEEN / CollAFL / AFLGo 這些名字不再只是 `-p` flag 的選項；你會知道它們各自在論文裡解什麼問題。
- **做出工具選型**：碰到新 target 時能判斷該選 AFL++、libFuzzer、Honggfuzz 哪一個，而不是每次都用同一個。
- **不迷信 fuzzer**：coverage-guided 有它解不動的東西（magic value、checksum、state machine）。看清楚邊界，你才知道什麼時候該換策略而不是加 core。

## 課程地圖

### Part 1 — 基礎與心智模型
- [Ch 0 從原始碼 build AFL++：先看它有哪些元件](./00-source-tree-walkthrough.md)
- [Ch 1 Fuzzing 三種流派：blackbox / grammar / coverage-guided](./01-fuzzing-landscape.md)
- [Ch 2 AFL 家譜：從 AFL 到 AFL++ 的分裂與合流](./02-afl-family-tree.md)
- [Ch 3 AFL++ 架構總覽：一張大圖串起所有元件](./03-afl-plus-plus-architecture.md)

### Part 2 — Instrumentation：讓 target 洩漏它的足跡
- [Ch 4 Edge coverage 原理：為什麼是 edge、為什麼是 64KB bitmap](./04-edge-coverage-bitmap.md)
- [Ch 5 編譯期 instrumentation 四種模式](./05-compile-time-instrumentation.md)
- [Ch 6 執行期 instrumentation：沒原始碼怎麼辦](./06-runtime-instrumentation.md)
- [Ch 7 Forkserver：AFL 最漂亮的設計](./07-forkserver.md)

### Part 3 — 核心 loop：seed / mutation / scheduling
- [Ch 8 Corpus 生命週期與 favored minset](./08-corpus-lifecycle.md)
- [Ch 9 Mutation 策略：deterministic、havoc、splice](./09-mutation-strategies.md)
- [Ch 10 Power schedule：誰該分到更多能量](./10-power-schedule.md)
- [Ch 11 Dictionary 與 auto-dictionary](./11-dictionary.md)

### Part 4 — 進階武器
- [Ch 12 CmpLog / RedQueen：破 magic bytes 的關鍵](./12-cmplog-redqueen.md)
- [Ch 13 Persistent mode：同一個 process 跑一萬次](./13-persistent-mode.md)
- [Ch 14 Custom mutator API：grammar-aware fuzzing 怎麼接](./14-custom-mutator.md)
- [Ch 15 Sanitizer 整合：AFL + ASan 為什麼不衝突](./15-sanitizers.md)

### Part 5 — 真實 fuzzing 工程的原理
- [Ch 16 Parallel fuzzing：master / secondary 分工](./16-parallel-fuzzing.md)
- [Ch 17 Crash triage：uniqueness、tmin、cmin 的演算法](./17-crash-triage.md)
- [Ch 18 AFL++ vs libFuzzer vs Honggfuzz：設計哲學對比](./18-fuzzer-comparison.md)

## 學習方式建議

1. **手邊開一份 AFL++ 原始碼**：每章會 reference 具體檔案（`src/afl-fuzz-bitmap.c`、`instrumentation/afl-compiler-rt.o.c` 等）。看著原始碼讀教材，比空讀概念紮實十倍。
2. **翻論文而不只翻 manual**：coverage-guided fuzzing 是學術與工程交纏的領域。當章節提到 AFLFast、REDQUEEN、AFLGo 時，翻一下原論文的 motivation 段落，你會發現很多 AFL++ 的 flag 其實在解某個具體研究問題。
3. **不要跳 Part 2**：instrumentation 是 AFL++ 的心臟。Part 3 之後所有 mutation、scheduling 策略都建立在「我看得見 target 走過哪些 edge」這個前提上。
4. **純原理讀法**：這系列不做動手練習。讀完每章如果能用自己的話跟朋友解釋 bitmap / forkserver / CmpLog 是什麼，就算真的吸收了。

## 參考資料

- AFL++ 官方文件：<https://aflplus.plus/>
- AFL++ GitHub（最終的答案都在 source code）：<https://github.com/AFLplusplus/AFLplusplus>
- AFL 原版技術白皮書 — Michał Zalewski：<https://lcamtuf.coredump.cx/afl/technical_details.txt>
- AFLFast paper — Böhme et al., CCS 2016
- REDQUEEN paper — Aschermann et al., NDSS 2019
- CollAFL paper — Gan et al., S&P 2018
- Fioraldi et al., "AFL++: Combining Incremental Steps of Fuzzing Research", WOOT 2020
- 《The Fuzzing Book》：<https://www.fuzzingbook.org/>
