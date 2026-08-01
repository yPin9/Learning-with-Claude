# 微架構攻擊：從 CPU 側信道到瞬態執行

> 給有 CPU 微架構、kernel、組語底子，想把「CPU 怎麼運作」變成「CPU 怎麼被打」的攻擊研究者。

這門課教你把現代處理器的效能優化——快取、亂序執行、推測執行、分支預測——反過來當成洩密通道。你會親手打造 cache 側信道原語（Flush+Reload / Prime+Probe）、跑出 Spectre-v1 端到端洩漏、理解整個瞬態執行攻擊家族的分類與演進、Rowhammer 如何把一個 bit 翻成提權，最後完整走一遍防禦：為什麼這樣修、修了還能怎麼繞。x86-64 主線，ARM/RISC-V 對照，全程在真實 CPU 上真跑能跑的、誠實標註硬體已修的。

## 為什麼學這個？

- **這是精英低階安全研究的標配**：Spectre/Meltdown 之後，微架構攻擊從學術角落變成 CPU 廠商每季都在補的戰場。理解它，你才看得懂現代 CPU 的緩解機制在防什麼、以及它們的縫在哪。
- **把你既有的知識武器化**：你已經懂 cache/pipeline/speculation（arm、riscv、kernel_internals、perf_bench）——這門課讓那些「效能知識」直接變成攻擊原語，是最高槓桿的延伸。
- **攻防都吃**：constant-time 程式設計、KPTI、retpoline、微碼更新——理解攻擊才寫得出真正不洩漏的 code。這在密碼學實作、機密運算、雲端多租戶場景是硬需求。

## 先修知識

- **C 與組語**（程度：能讀寫 x86-64 組語、懂 inline asm、會用 rdtsc）
- **CPU 微架構基礎**（程度：知道 cache/pipeline/亂序執行大概是什麼；本課 Part 1 會補齊到夠用，深追可看 [architecture/arm](../../architecture/arm/README.md)、[systems/kernel_internals](../../systems/kernel_internals/README.md)）
- **虛擬記憶體**（程度：懂 VA→PA、page table、TLB 概念）
- **一點作業系統與 exploit 直覺**（程度：懂 process/privilege、看得懂「一個 bit flip 怎麼變提權」；有 [binary_exploitation](../binary_exploitation/README.md) 底更好）
- 沒有也沒關係的：具體的攻擊史（本課從零講起）

## 課程地圖

### Part 1 — 地基：為什麼微架構會洩密（Ch 0–5）
- [Ch 0 環境與工具搭建](./00-environment-setup.md)
- [Ch 1 微架構攻擊全景](./01-microarch-attacks-overview.md)
- [Ch 2 你必須先懂的 CPU 微架構](./02-cpu-microarchitecture-primer.md)
- [Ch 3 快取階層與 set-associative 組織](./03-cache-hierarchy-organization.md)
- [Ch 4 計時就是一切：rdtsc 與測量方法學](./04-timing-measurement-methodology.md)
- [Ch 5 虛擬記憶體與位址轉換對攻擊的意義](./05-virtual-memory-and-addressing.md)

### Part 2 — Cache 側信道原語（Ch 6–12）
- [Ch 6 Flush+Reload](./06-flush-reload.md)
- [Ch 7 Flush+Reload covert channel](./07-flush-reload-covert-channel.md)
- [Ch 8 Evict+Reload 與 Prime+Probe](./08-evict-reload-prime-probe.md)
- [Ch 9 建 eviction set](./09-building-eviction-sets.md)
- [Ch 10 Flush+Flush 等變體](./10-flush-flush-and-variants.md)
- [Ch 11 打真實目標：cache 攻擊打 crypto](./11-cache-attacks-on-crypto.md)
- [Ch 12 跨核心/跨 VM 的 LLC 攻擊](./12-cross-core-cross-vm-llc.md)
- [練習 A：Flush+Reload covert channel](./practice-a-flush-reload-covert-channel.md)

### Part 3 — 瞬態執行攻擊（Ch 13–21）
- [Ch 13 推測執行與瞬態指令](./13-transient-execution-basics.md)
- [Ch 14 Spectre v1（Bounds Check Bypass）](./14-spectre-v1-bounds-check-bypass.md)
- [Ch 15 分支預測器內部](./15-branch-predictor-internals.md)
- [Ch 16 Spectre v2（Branch Target Injection）](./16-spectre-v2-branch-target-injection.md)
- [Ch 17 Spectre-RSB / ret2spec](./17-spectre-rsb-ret2spec.md)
- [Ch 18 Meltdown](./18-meltdown.md)
- [Ch 19 MDS 家族 + L1TF/Foreshadow](./19-mds-l1tf-foreshadow.md)
- [Ch 20 後續世代（Downfall/Zenbleed/Inception/Retbleed）](./20-later-generation-transient.md)
- [Ch 21 瞬態執行分類學](./21-transient-execution-taxonomy.md)
- [練習 B：Spectre-v1 端到端洩漏](./practice-b-spectre-v1-leak.md)

### Part 4 — 其他微架構通道（Ch 22–28）
- [Ch 22 Rowhammer 基礎](./22-rowhammer-basics.md)
- [Ch 23 Rowhammer 攻擊利用](./23-rowhammer-exploitation.md)
- [Ch 24 Rowhammer 演進與防禦](./24-rowhammer-evolution-defenses.md)
- [Ch 25 頻率/功耗側信道：Hertzbleed](./25-hertzbleed-frequency-power.md)
- [Ch 26 Port contention 與 SMT 側信道](./26-port-contention-smt.md)
- [Ch 27 TLB 側信道與其他結構](./27-tlb-and-other-channels.md)
- [Ch 28 微架構 KASLR 破解](./28-microarchitectural-kaslr-break.md)
- [練習 C：破 KASLR](./practice-c-break-kaslr.md)

### Part 5 — 防禦（Ch 29–34）
- [Ch 29 防禦全景與威脅模型](./29-defense-landscape.md)
- [Ch 30 隔離類防禦](./30-isolation-defenses.md)
- [Ch 31 推測抑制](./31-speculation-suppression.md)
- [Ch 32 Constant-time 程式設計](./32-constant-time-programming.md)
- [Ch 33 偵測（HPC-based）](./33-detection-hpc.md)
- [Ch 34 微碼與硬體防禦的未來](./34-microcode-hardware-future.md)
- [練習 D：把洩漏的 code 改成 constant-time](./practice-d-constant-time-fix.md)

### Part 6 — 整合（Ch 35–36）
- [Ch 35 串起來：一條真實 end-to-end 洩漏鏈](./35-end-to-end-leak-chain.md)
- [Ch 36 研究方法論：怎麼找新的微架構洞](./36-research-methodology-finding-bugs.md)
- [Final Project：微架構洩漏實驗室](./final-project-microarch-leak-lab.md)

## 學習方式建議

1. **每個原語都親手刻一遍**：cache 攻擊不自己跑出那條 timing 分佈，你永遠不會真的懂。Part 2 每章都 clone 不了別人的，自己寫。
2. **釘住你的量測環境**：pin CPU、關 turbo/prefetcher、多次取樣。微架構攻擊 80% 的失敗是量測沒調好，不是原理不懂。
3. **誠實面對硬體差異**：你的 CPU 修過的洞（Meltdown/MDS）跑不出來是正常的——讀懂原理、知道在什麼環境能重現，比硬湊 PoC 重要。
4. **攻防對照著讀**：每讀完一個攻擊，去 Part 5 找它對應的防禦，理解「這個修法擋住了什麼、又漏了什麼」。

## 精選資料庫

這裡列整門課最值得反覆參照的資源；每章「延伸閱讀」會指向更具體的小節。

### 必讀論文

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  - 瞬態執行攻擊的開山論文；Part 3 的地基。先讀 Section III（Spectre-v1）與 IV（v2）。
- **[Meltdown: Reading Kernel Memory from User Space](https://meltdownattack.com/meltdown.pdf)** — Lipp et al., USENIX Security 2018
  - Meltdown-type 的原型；Ch 18 的主要參考。
- **[A Systematic Evaluation of Transient Execution Attacks and Defenses](https://arxiv.org/abs/1811.05441)** — Canella et al., USENIX Security 2019
  - 整個瞬態執行領域的分類學；Ch 21 直接建立在它上面。遇到新洞先來這裡歸類。

### 必讀綜述

- **[FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack](https://eprint.iacr.org/2013/448.pdf)** — Yarom & Falkner, USENIX Security 2014
  - Flush+Reload 的原始論文；Part 2 的核心技術。
- **[Hello from the Other Side: SSH over Robust Cache Covert Channels](https://gruss.cc/)** — Daniel Gruss 的整個 publication 列表
  - Gruss 團隊（TU Graz）是這領域產出最密集的組；他們的論文幾乎覆蓋本課每個主題。

### 讀完本課之後

- **[transient.fail](https://transient.fail/)**（瞬態執行攻擊的分類與追蹤網站；隨新洞更新，本課的活地圖）
- 把方法論套到最新一代（Downfall/Inception 之後）的 CPU，讀 CPU 廠商的 security advisory 練「歸類新洞」的手感（Ch 36）。
