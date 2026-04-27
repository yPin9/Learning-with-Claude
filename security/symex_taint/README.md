# 進階程式分析筆記：Symbolic Execution & Dynamic Taint Analysis

> 給會 C/C++ 與 Python、看得懂 assembly、知道 SMT 怎麼回事，想真的理解 symex 與 taint 怎麼被工程化、而不只是會 `p.explore(find=...)` 的工程師。

這系列從「為什麼要有 symbolic execution」的動機講起，先把核心循環（state / path constraint / SMT query）拆透，接著用 Z3 手寫一個 mini concolic executor 跑過一次、再去讀 KLEE 與 angr 的架構，然後切到 dynamic taint analysis 把 shadow memory、propagation rule、DBI 工具全部攤開，最後談 hybrid fuzzing 與 symex + taint 的組合。全程假設你已經懂 SMT solver 大致怎麼運作，所以不再從命題邏輯重講。

## 為什麼學這個？

- **漏洞研究的入場券**：現代 vulnerability research、fuzzer 設計、exploit automation 底下都是 symex + taint 的組合。不會這兩樣，你只能手 debug。
- **覆蓋率逼近可達性**：fuzzing 卡在 magic byte / checksum / complex branch 時，symex 是把路徑推過去的唯一路線。Driller、QSYM、SymCC 每一個都在解同一個問題。
- **Taint analysis 是污染傳播的語言**：一旦你能用 source / sink / propagation 三件事表達一個安全問題，你就能寫出它的 detector。SQLi、SSRF、deserialization、memory safety 本質上都是 taint。
- **理解 symex 的極限比會用更重要**：path explosion、symbolic memory、environment model 是三座山。看不懂這三座山，你會把 `simgr.explore()` 掛 24 小時然後埋怨 angr 爛；看懂了，你會知道什麼時候該放棄 symex 改 fuzzing。

## 先備知識

- 會 Python（angr 是 Python）、會 C（KLEE target 都是 C）
- 會讀 x86-64 assembly，ELF 大致知道長怎樣（看 `learn_elf_linking`、`learn_ida_pro` 先補）
- SMT / Z3 的基本概念（看 `learn_sat_smt` 的 Part 0 + Ch 22 足夠）
- 用過 gdb 看過 program state（`learn_gdb`）

沒有這些，這門課會太硬，建議先補。

## 課程地圖

### Part 1 — 心智模型
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 為什麼要 symex：static / fuzzing / symbolic 三條路](./01-why-symex.md)
- [Ch 2 Symbolic execution 的核心循環](./02-core-loop.md)
- [Ch 3 路徑爆炸這個病](./03-path-explosion.md)

### Part 2 — Symex 內部機制
- [Ch 4 Concrete vs symbolic value 與 memory model](./04-values-and-memory.md)
- [Ch 5 Path constraint 如何轉成 SMT query](./05-path-constraint-to-smt.md)
- [Ch 6 Concolic execution：DART 與 CUTE 的真實思路](./06-concolic.md)
- [Ch 7 實作：用 Z3 手寫 mini concolic executor](./07-implement-mini-concolic.md)
- [Ch 8 State merging：降爆炸的代價與時機](./08-state-merging.md)
- [Ch 9 Symbolic memory：address concretization vs fully symbolic](./09-symbolic-memory.md)
- [Ch 10 Environment modeling：syscall、libc、外部世界](./10-environment-modeling.md)
- [練習 A：寫一個 mini concolic executor](./practice-a-mini-concolic.md)

### Part 3 — KLEE
- [Ch 11 KLEE 架構：LLVM IR 上做 symex 的理由](./11-klee-architecture.md)
- [Ch 12 klee_make_symbolic、POSIX runtime、uclibc](./12-klee-in-practice.md)
- [Ch 13 KLEE 的實戰邊界與 CVE 案例](./13-klee-limits-and-cves.md)
- [練習 B：用 KLEE 找一個故意放漏洞的小程式](./practice-b-klee-find-bug.md)

### Part 4 — angr
- [Ch 14 angr 架構：VEX IR、SimState、SimProcedure](./14-angr-architecture.md)
- [Ch 15 CFGFast vs CFGEmulated](./15-angr-cfg.md)
- [Ch 16 Simulation manager 與 exploration techniques](./16-angr-simmanager.md)
- [Ch 17 CTF 應用：用 angr 解 crackme 的正確姿勢](./17-angr-ctf.md)
- [Ch 18 angr 的極限：什麼時候該關掉 angr](./18-angr-limits.md)
- [練習 C：angr 解一整組 CTF crackme](./practice-c-angr-ctf-series.md)

### Part 5 — Dynamic Taint Analysis
- [Ch 19 Taint 語意：source / sink / propagation rule](./19-taint-semantics.md)
- [Ch 20 Taint policy 設計：explicit vs implicit flow、over/under-tainting](./20-taint-policy.md)
- [Ch 21 Granularity 與 shadow memory 實作](./21-shadow-memory.md)
- [Ch 22 DBI 工具比較：Pin / DynamoRIO / Frida / QEMU TCG](./22-dbi-tools.md)
- [Ch 23 libdft 與 Triton 的架構解剖](./23-libdft-triton.md)
- [Ch 24 Taint 的攻擊面應用：exploit 可達性、漏洞發現](./24-taint-applications.md)
- [練習 D：用 Triton 寫 taint 追蹤小工具](./practice-d-triton-taint.md)

### Part 6 — 混合技術與前沿
- [Ch 25 Hybrid fuzzing：Driller / QSYM / SymCC 的取捨](./25-hybrid-fuzzing.md)
- [Ch 26 Under-constrained symbolic execution](./26-ucse.md)
- [Ch 27 Symex + taint 聯手：Triton-style hybrid](./27-symex-taint-hybrid.md)
- [Ch 28 前沿與何時該換工具](./28-frontier.md)
- [Final Project：Hybrid binary 漏洞分析](./final-project-hybrid-vuln-analysis.md)

## 學習方式建議

1. **Ch 7 的 mini concolic executor 一定要自己寫過一次**。 複製貼上不算。寫完你再去看 angr 的 `SimState`，會像看翻譯小說 — 每一個 field 都有你腦袋裡對應的東西。這是這整門課的「CDCL 時刻」。
2. **每個工具都拿一個真實題目跑**。 KLEE 丟一個 coreutils 小工具、angr 丟一個 pwnable.tw 的 crackme、Triton 丟一個有 string transform 的 license check。只讀 tutorial 不會有感覺。
3. **path explosion 要親眼看一次**。 故意寫一個三層 nested loop + `if` 的 target 丟 angr，看它跑到什麼時候 OOM，然後拿 state merging / DSE 策略去救它。Ch 3、Ch 8 搭配做。
4. **讀論文**。 DART（PLDI 2005, Godefroid）、KLEE（OSDI 2008, Cadar）、Driller（NDSS 2016）、QSYM（USENIX Sec 2018）、SymCC（USENIX Sec 2020）— 這五篇是這個領域的骨幹，我會寫摘要但原文值得看。
5. **別迷信 symex**。 真實世界 80% 的 bug 是 fuzzing 找到的。symex 是 fuzzing 卡住時的武器，不是萬靈丹。看到 angr 當機先問自己「這題 AFL 跑過了嗎？」

## 參考資料

- *A Survey of Symbolic Execution Techniques* — Baldoni et al., ACM CSUR 2018 — 最完整的 symex 綜述
- *All You Ever Wanted to Know About Dynamic Taint Analysis and Forward Symbolic Execution (but might have been afraid to ask)* — Schwartz, Avgerinos, Brumley, S&P 2010 — 這篇名字最長，內容也最清楚
- *DART: Directed Automated Random Testing* — Godefroid et al., PLDI 2005 — concolic execution 的原始論文
- *KLEE: Unassisted and Automatic Generation of High-Coverage Tests for Complex Systems Programs* — Cadar, Dunbar, Engler, OSDI 2008
- *(State of) The Art of War: Offensive Techniques in Binary Analysis* — Shoshitaishvili et al., S&P 2016 — angr 的 paper
- *SoK: All You Ever Wanted to Know About x86/x64 Binary Disassembly But Were Afraid to Ask* — Pang et al., S&P 2021 — DBI 的前置背景
- angr docs：<https://docs.angr.io/>
- KLEE docs：<https://klee-se.org/>
- Triton docs：<https://triton-library.github.io/>
- SymCC：<https://github.com/eurecom-s3/symcc>
