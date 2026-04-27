# Ch 28 — 前沿與何時該換工具

> 目標：把整個領域的最近三五年發展、仍在打磨的問題、以及你的後續學習路徑理清楚。

## 當下（2025-2026）的前沿題目

### 1. Neural-guided symex / fuzzing

用機器學習引導 symex 選路。

- **NEUZZ**（S&P 2019）：用 NN 學「input byte → coverage」的 gradient，guide mutation
- **MEUZZ**（2020）：ML-assisted seed scheduling
- **Fuzz4All**（ICSE 2024）：用 LLM 產 fuzzer input

目前：效果不穩、reproducibility 差。很多 paper 在 benchmark 刷分，但 generalize 到 real-world target 的證據弱。**謹慎看待**，但技術方向是真的。

### 2. LLM + symex

LLM 幫 symex 做**harness 生成**、**invariant 推斷**：

- LLM 讀 C source、產出 KLEE harness
- LLM 讀 bug 報告、產出 precondition 給 UCSE

這是 2024+ 的新實踐，尚無 production-grade 工具。

### 3. Rust / Safe language 的 symex

Rust 不會 crash memory、但可能有 logic bug。symex 在 Rust 上做 logic verification 是個開放方向：

- **Kani**（Amazon/AWS）：Rust bound model checking
- **Verifast** 的 Rust extension
- **Creusot**：Rust + Why3 formal

這個領域**有產業採用**（AWS 用 Kani 驗證 s2n-quic）。跟 symex 傳統目標（找 memory bug）不同方向 — 做 logic correctness。

### 4. Compositional / modular analysis

現實 codebase 超大，whole-program symex 不現實。**compositional** 做法：

- 對每個 function 算 summary（pre / post condition）
- caller 用 callee 的 summary、不展開
- 變成 `O(functions)` 而非 `O(paths)`

代表作：
- **Infer** (Facebook)：static compositional analysis
- **CSE** for symex：research 級

這個思路**會是下一個 decade 的主線**。

### 5. Hardware-assisted analysis

Intel PT（Processor Trace）：CPU 在 kernel-level 記錄每個 branch，開銷 < 5%。

- 用 Intel PT 拿 trace → 放進 symex
- 比 DBI 快 100×

工具：
- **PT-KLEE** 研究原型
- **Snapshot-based fuzzers** 如 Nyx 用 Intel PT

### 6. Firmware / IoT

ARM / MIPS / 嵌入式 symex 需求爆炸（IoT 安全）。工具：
- angr 對 ARM 支援 OK
- **avatar²**：嵌入式 device co-execution
- **FirmWire**：baseband firmware 的 SE

Firmware symex 的關鍵挑戰是 peripheral model — 設備跟 CPU 交互（MMIO、interrupt）要 model。這比 POSIX 模型難很多。

### 7. Decompile-assisted 分析

現代 decompiler（Ghidra、angr decompiler、Rizin）輸出 pseudo-C。然後對 pseudo-C 做 symex 比直接 binary 上做好。

- angr 的 `pseudocode` analysis 輸出
- 配 KLEE 在 pseudo-C 上跑（**還在 research**）

挑戰：pseudo-C 的 semantic 正確性。

## 何時該換工具

如果你在用 symex / DTA，但感覺事倍功半 — 往往是工具選錯。表：

### 換回 fuzzing

- 你寫 symex script 十次跑十次 OOM
- target coverage 超過 fuzzer 後，bug rate 沒上升
- target 主要是 memory corruption 類 bug

### 換用 static analysis

- 你的問題是 **已知 pattern**（strcpy、format string、SQL concat）
- 你要 cover 100% code、精度可妥協
- 你要在 PR 檢查流程裡跑（CI）

Tool：clang-tidy、semgrep、CodeQL、Infer。

### 換用 model checking

- 你分析 protocol、state machine
- path space 跟 message ordering 有關
- 你要**證明**而不只是找 counter-example

Tool：SPIN、TLA+、CBMC、CPAchecker。

### 換用 formal verification

- 你要數學證明
- target 是 crypto protocol 或算法
- 你有耐心 / 人力 manually 寫 proof

Tool：Coq、Isabelle、F*、Dafny、Verus。

## 你的學習路徑（之後）

完成這門課後，深入方向：

### 方向 A：Binary RE / Vuln Research

- 學 exploitation techniques（ROP、heap grooming）
- 學 kernel exploit（pwn.college, pwnable.tw）
- 主要 tool：angr、Triton、Ghidra、pwntools
- 讀：How2Heap、LWN kernel security articles

### 方向 B：Fuzzing / OSS-Fuzz

- 深入 AFL++ 內部
- 學 libFuzzer、honggfuzz
- 給 OSS-Fuzz 寫 harness
- 讀："The Fuzzing Book" (fuzzingbook.org)

### 方向 C：Formal verification

- Coq / Isabelle 入門
- F* / Dafny
- SMT solver 內部（你應該先看 `sat_smt`）
- 讀：Software Foundations

### 方向 D：Academic research

- 攻讀 PhD（security / PL）
- 讀最新 S&P、USENIX Sec、CCS、NDSS paper
- 會 reproduce paper 的 benchmark

方向 A 跟 B 是就業主力。C 跟 D 比較 niche 但有其 niche 的高價值。

## 重要的書 / 資源

這門課刻意沒塞很多外部資源進去。補一下：

### 書

- *Principles of Program Analysis* — Nielson, Nielson, Hankin — static / dynamic 分析的教科書
- *Reverse Engineering for Beginners* — Yurichev（免費）— binary RE 入門
- *Fuzzing: Brute Force Vulnerability Discovery* — 老但經典
- *The Art of Software Security Assessment* — Dowd et al. — vuln research 思維

### Paper 必讀

- DART (Godefroid, PLDI 2005)
- KLEE (Cadar, OSDI 2008)  
- All-You-Ever-Wanted (Schwartz, S&P 2010)
- State of the Art of War (Shoshitaishvili, S&P 2016) — angr 的 paper
- Driller (NDSS 2016), QSYM (Sec 2018), SymCC (Sec 2020)
- Symbolic Execution for Software Testing: Three Decades Later — Cadar et al. (CACM 2013)

### Blog / Podcast

- Project Zero blog
- angr blog、Trail of Bits blog
- @LiveOverflow YouTube（入門 friendly）

## 自我檢核（整門課終點）

- [ ] 能對任意 binary target，給出合理的分析 stack：用什麼 tool、先後順序、預期結果
- [ ] 知道 symex / DTA / fuzzing / static analysis 的分工
- [ ] 能讀 symex / DTA 的新 paper
- [ ] 知道自己之後想往哪個方向深入
- [ ] 寫過至少一個完整的 symex 或 DTA 工具（mini concolic + Triton taint tool）

## 心法

這個領域的 meta 教訓：

1. **工具是杓子，不是湯** — 知道解什麼問題比會用什麼工具重要
2. **精度跟速度永遠在 trade-off** — 沒有銀彈，只有取捨
3. **手動 reverse 是基本功** — 最強的 symex 工程師都先能手解
4. **讀 source** — KLEE / angr / Triton 的 source 比 paper 還教會你更多
5. **小 target 練 tool，大 target 練 judgment** — 不要跳過小 target 直接衝大 target

Part 6 結束。接著是 **Final Project**，結合你學過的所有東西做一個端對端的 binary 漏洞分析。

→ [Final Project：Hybrid binary 漏洞分析](./final-project-hybrid-vuln-analysis.md)
