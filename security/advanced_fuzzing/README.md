# 進階 Fuzzing：afl++ 之後——為每個難纏目標造對的 fuzzer

> 給已經懂 coverage-guided fuzzing 基礎、想打進 kernel / browser / 韌體 / 協定伺服器 / closed-source 目標，並以 CVE hunting 為目標的資安工程師。

afl++ 那門課教你「單機、無狀態、吃檔案的 CLI 目標」的 coverage-guided fuzzer 內部怎麼運作。這門課從那裡接手，教你打**那個舒適圈之外的一切**——不可重置的狀態、非檔案的輸入介面、非本機的執行目標、沒有原始碼甚至沒有作業系統的 blob。核心信條是：**懂了 fuzzer 的組件，你就能為任何目標拼一個對的出來**，所以我們工具不拘——LibAFL、syzkaller、Nyx、Fuzzilli、Fuzzware、SymCC 各自登場，但每次都先問「這個目標的形態是什麼、它逼我們解哪個問題」，再決定拿哪個工具。

## 為什麼學這個？

- **afl++ 打不到的地方才是 CVE 的藏身處**：好摘的果子——單機 CLI parser——早被 OSS-Fuzz 掃過幾百萬核心小時了。真正還有洞的是 kernel syscall 介面、瀏覽器 JIT、協定狀態機、韌體 MMIO——這些都需要專用 fuzzer。
- **會造 fuzzer 比會用 fuzzer 稀缺一個數量級**：市場上 `afl-fuzz -i in -o out` 誰都會敲；能為一個沒有 harness 的 closed binary 用 LibAFL 拼出 in-process executor、能寫 syzlang 描述一個 driver 的 ioctl 介面、能把一段 firmware blob rehost 進 unicorn——這種人產品安全團隊搶著要。
- **接得上你的天梯**：這門課的每個 Part 都咬合你已經有的課——kernel fuzzing 接 kernel_pwn/kernel_internals，snapshot 接 vm_escape，JS 引擎接 browser_pwn，hybrid 接 symex_taint，造 fuzzer 用 rust。fuzzing 是把「找洞」自動化的那一環，補上它，你的攻擊鏈才完整。

## 先修知識

- **coverage-guided fuzzing 基礎**（程度：讀過 afl_plus_plus，或懂 edge bitmap / forkserver / corpus / mutation 的概念）——這門課不重講這些
- **C 與 systems 基礎**（程度：能讀 C source、懂 mmap/fork/ptrace/ELF、UB）
- **Rust 基礎**（程度：Part 1 造 fuzzer 會用；沒學過可先看 programming/rust）
- 有幫助但非必要：kernel 概念（Part 4）、虛擬化/VT-x（Part 5）、ARM 與嵌入式（Part 6）、V8/JS 引擎（Part 7）、符號執行（Part 8）——各 Part 開頭會給回看路標
- 沒有也沒關係的：實際找過 CVE 的經驗——這門課就是帶你走到那

## 環境與誠實界線

全程以 WSL2（Ubuntu）+ LLVM/Clang + Rust + QEMU 為主環境，**能在這環境真跑的一律親手跑、貼真實輸出**。有幾類目標受限於硬體或核心特權，會**明確標注「未實測／理論預期行為」並給出在真實環境的驗證方法**，不假裝跑過：

- **syzkaller**（Part 4）：需 KVM + 自 build 的 kernel image，WSL 內可跑但受巢狀虛擬化限制，關鍵步驟標注
- **Nyx / kAFL**（Part 5）：需 VT-x + Intel PT 硬體，多數雲/WSL 環境不具備，以架構解析為主
- **Fuzzilli**（Part 7）：需自 build 打了 coverage patch 的 JS 引擎，流程可跑但耗時，標注哪些是實測

## 課程地圖

### Part 0 — 起點：afl++ 之後（Ch 0–3）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 afl++ 的四道牆](./01-afl-plus-plus-walls.md)
- [Ch 2 現代 fuzzer 全景](./02-modern-fuzzer-landscape.md)
- [Ch 3 覆蓋率的本質再訪](./03-coverage-feedback-revisited.md)

### Part 1 — 自己造 fuzzer：LibAFL（Ch 4–10）
- [Ch 4 LibAFL 哲學：fuzzer 是可組合元件](./04-libafl-philosophy.md)
- [Ch 5 第一個 LibAFL fuzzer](./05-libafl-first-fuzzer.md)
- [Ch 6 Observer 與 Feedback](./06-observer-feedback.md)
- [Ch 7 Executor 家族](./07-executor-family.md)
- [Ch 8 Mutator 與 Stage 客製](./08-mutator-stage.md)
- [Ch 9 型別化與結構化輸入](./09-typed-structured-input.md)
- [Ch 10 分散式 LibAFL](./10-libafl-distributed.md)
- [練習 A：用 LibAFL 造結構感知 parser fuzzer](./practice-a-libafl-parser-fuzzer.md)

### Part 2 — 結構感知與文法 fuzzing（Ch 11–15）
- [Ch 11 為什麼 dumb mutation 打不進結構化格式](./11-why-dumb-mutation-fails.md)
- [Ch 12 libprotobuf-mutator 與 FuzzTest](./12-libprotobuf-mutator-fuzztest.md)
- [Ch 13 文法 fuzzing：Nautilus / Gramatron](./13-grammar-fuzzing.md)
- [Ch 14 覆蓋引導 × 文法與自動文法推斷](./14-coverage-guided-grammar.md)
- [Ch 15 差分 fuzzing](./15-differential-fuzzing.md)
- [練習 B：為真實 parser 寫文法 fuzzer](./practice-b-grammar-parser-fuzzer.md)

### Part 3 — Stateful / 網路協定 fuzzing（Ch 16–20）
- [Ch 16 stateful 目標的難題](./16-stateful-target-problem.md)
- [Ch 17 AFLNet](./17-aflnet.md)
- [Ch 18 StateAFL 與狀態表示](./18-stateafl-state-representation.md)
- [Ch 19 harness 化網路伺服器](./19-harnessing-servers.md)
- [Ch 20 協定 fuzzing 實戰](./20-protocol-fuzzing-in-practice.md)
- [練習 C：打一個 stateful daemon](./practice-c-stateful-daemon.md)

### Part 4 — Kernel fuzzing（Ch 21–27）★重頭
- [Ch 21 kernel 攻擊面與專用 fuzzer](./21-kernel-attack-surface.md)
- [Ch 22 KCOV 底層](./22-kcov.md)
- [Ch 23 KASAN/KMSAN/KCSAN 當 oracle](./23-kernel-sanitizers.md)
- [Ch 24 syzkaller 架構](./24-syzkaller-architecture.md)
- [Ch 25 syzlang：描述 syscall 介面](./25-syzlang.md)
- [Ch 26 跑 syzkaller 打自訂 module](./26-running-syzkaller.md)
- [Ch 27 kernel fuzzing 進階](./27-kernel-fuzzing-advanced.md)
- [練習 D：為有 bug 的 kernel module 寫 syzlang 並抓到它](./practice-d-syzlang-module.md)

### Part 5 — Snapshot / 全系統 fuzzing（Ch 28–32）
- [Ch 28 為什麼需要 snapshot fuzzing](./28-why-snapshot-fuzzing.md)
- [Ch 29 Nyx / kAFL](./29-nyx-kafl.md)
- [Ch 30 Intel PT 當 coverage source](./30-intel-pt-coverage.md)
- [Ch 31 snapshot 機制](./31-snapshot-mechanics.md)
- [Ch 32 全系統 target](./32-whole-system-targets.md)
- [練習 E：snapshot fuzz 一個不可重置目標](./practice-e-snapshot-fuzz.md)

### Part 6 — 韌體 / 嵌入式 rehosting（Ch 33–36）
- [Ch 33 rehosting 問題](./33-rehosting-problem.md)
- [Ch 34 unicorn-based harnessing](./34-unicorn-harnessing.md)
- [Ch 35 Fuzzware / HALucinator](./35-fuzzware-halucinator.md)
- [Ch 36 韌體 fuzzing 實戰](./36-firmware-fuzzing-practice.md)

### Part 7 — 瀏覽器 / JS 引擎 fuzzing（Ch 37–39）
- [Ch 37 JS 引擎與語意有效性](./37-js-engine-semantic-validity.md)
- [Ch 38 Fuzzilli](./38-fuzzilli.md)
- [Ch 39 JS 引擎崩潰 triage 與可利用性](./39-js-engine-triage.md)

### Part 8 — Hybrid / directed / 符號輔助（Ch 40–43）
- [Ch 40 hybrid fuzzing 原理](./40-hybrid-fuzzing.md)
- [Ch 41 SymCC / SymQEMU](./41-symcc-symqemu.md)
- [Ch 42 Driller / QSYM](./42-driller-qsym.md)
- [Ch 43 directed fuzzing：AFLGo](./43-directed-fuzzing-aflgo.md)
- [練習 F：用 hybrid/directed 打穿深障礙](./practice-f-hybrid-directed.md)

### Part 9 — 規模化與評測科學（Ch 44–47）
- [Ch 44 OSS-Fuzz](./44-oss-fuzz.md)
- [Ch 45 ClusterFuzz 與 corpus 管理](./45-clusterfuzz-corpus.md)
- [Ch 46 FuzzBench 與評測科學](./46-fuzzbench-evaluation-science.md)
- [Ch 47 從 crash 到 CVE](./47-crash-to-cve.md)
- [Final Project：真實開源目標端到端 fuzzing campaign](./final-project-real-target-campaign.md)

## 學習方式建議

1. **每個 Part 先問「目標形態」**：這門課刻意不按工具編排，而按目標編排。讀每個 Part 前先問自己——這類目標為什麼 afl++ 打不了？逼我們解的核心問題是什麼？工具只是答案，問題才是重點。
2. **造得出來才算懂**：Part 1 的 LibAFL 是全課的槓桿——把 fuzzer 拆成 Observer/Feedback/Executor/Mutator/Stage 之後，後面每個目標你都能反過來問「我要換哪個組件」。務必親手拼出至少一個。
3. **故意讓 fuzzer 找不到 bug**：把一個已知 bug 藏在 magic value 後面、藏在第三個狀態轉換後面、藏在一個 checksum 後面，看你的 fuzzer 卡在哪——卡點會告訴你該上文法、上 stateful、還是上 hybrid。
4. **誠實面對跑不動的部分**：syzkaller/Nyx/Fuzzilli 在你的環境可能跑不起來，那就把架構讀透、把「如果有硬體我會怎麼驗證」寫下來。知道邊界比假裝全跑過更值錢。

## 精選資料庫

這裡列整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[The Fuzzing Book](https://www.fuzzingbook.org/)** — Zeller, Gopinath, Böhme, Fraser, Holler
  - 線上互動教科書；文法 fuzzing、覆蓋引導、reduction 的原理講得最清楚，Part 2 的地基
- **[AFL++ / LibAFL 官方文件與論文](https://aflplus.plus/)**
  - LibAFL 的設計哲學（元件化）是 Part 1 的權威來源

### 推薦論文

- **[Nyx: Greybox Hypervisor Fuzzing using Fast Snapshots and Affine Types](https://www.usenix.org/conference/usenixsecurity21/presentation/schumilo)** — Schumilo et al., USENIX Security 2021
  - snapshot fuzzing 的代表作，Part 5 主線
- **[Fuzzware: Using Precise MMIO Modeling for Effective Firmware Fuzzing](https://www.usenix.org/conference/usenixsecurity22/presentation/scharnowski)** — Scharnowski et al., USENIX Security 2022
  - 韌體 rehosting 的 MMIO 建模，Part 6 主線

### 推薦部落格 / 團隊

- **[Google Project Zero blog](https://googleprojectzero.blogspot.com/)**
  - 大量 fuzzer 設計與從 crash 到 exploit 的一手記錄，Part 7/9 反覆引用
- **[Fuzzing Labs / hexops / gamozolabs（Brandon Falk）](https://gamozolabs.github.io/)**
  - 效能導向的 fuzzer 工程與 snapshot fuzzing 實作細節

### 讀完本課之後

- **[OSS-Fuzz](https://github.com/google/oss-fuzz)** — 把你的技能接上真實世界持續 fuzzing 的入口
- **[syzbot dashboard](https://syzkaller.appspot.com/)** — 看 kernel fuzzing 在生產規模上長什麼樣，挑一個 open bug 練 root cause
