# Ch 19 — RISC-V International 生態與流程

> 目標：理解 RISC-V 這個「開放標準」背後的組織、政治與流程。哪些公司在拉什麼方向、profile 制度在收斂什麼、對 SiFive / T-Head / Rivos 這些廠商的意義。這章是面試時的「行業背景知識」。

## 主要玩家

### RISC-V International (RVI)

總部瑞士（2020 年遷）的非營利組織。**掌管 ISA 標準化**。約 3500 會員（2024）。

有會員等級：

- **Premier**: 董事會席位，最大票數。典型：Intel、SiFive、Andes、Western Digital、NVIDIA、Alibaba（T-Head）、Google、Huawei
- **Strategic**: 中等影響力
- **Associate**: 基礎會員，可參加 working group

年會費從幾千到幾十萬美元不等。SiFive 肯定是 Premier。

### IP 設計廠（core 設計）

- **SiFive**（美，原 Berkeley team 出來）— P / X / S 系列
- **Andes**（台）— N/D/A/V 系列，DSP 強項
- **T-Head / XuanTie**（中，阿里巴巴）— C系列
- **Codasip**（捷克）— customizable core
- **Ventana**（美新創）— server 路線
- **Rivos**（美新創）— AI / server
- **Imagination Technologies**（英）— graphics + RISC-V CPU

### 晶片 / 系統廠

- **Google** — TPU / Pixel 某些 coprocessor
- **Meta** — 資料中心用
- **NVIDIA** — 某些 GPU microcontroller 改用 RISC-V
- **Intel** — 自家研究 RISC-V（有趣的投資平衡）
- **Samsung** — IoT / 邊緣設備
- **汽車**（BOSCH、Infineon）— 功能安全、長壽命晶片

### 系統軟體

- **Linux Foundation** — kernel 主線支援
- **RISE Project** (RISC-V Software Ecosystem) — 2023 成立，旗下多個廠商共同資助 toolchain 開發（非常關鍵）

## Profile 制度：fragmentation 的解方

回顧 Ch 13 — RISC-V 擴充太多，客戶不知道該買什麼、發 binary 不知道該 compile 什麼。Profile 給出一個清單：

```
RVA20:  2020 年版，Linux 基本要求
RVA22:  2022 年版，細化 (加 B 等)
RVA23:  2023 年版，server 路線（強制 V、H）
RVB23:  bare-metal 低階版
RVM23:  microcontroller 級別
```

### RVA23 mandatory 擴充速覽

**Unprivileged**:
```
RV64I base
M, A, F, D, C
Zicsr, Zifencei
Zicntr, Zihpm (counters)
Zicclsm (misaligned access)
Ziccamoa (atomic orderings)
Ziccif
Zihintpause
Zba, Zbb, Zbs
Zfhmin (FP16 minimal)
Zcb, Zcmop
Zfa (additional FP)
Zawrs (wait-for-reservation-set)
V (vector)
Zvfhmin
Zvbb, Zvbc
Zvkb
Zicond
```

**Privileged** (for Linux-running systems):
```
S-mode + Sv39 or Sv48
H extension (for server profile)
Zicbom / Zicboz / Zicbop
```

**Optional 但強推薦**:
```
Zfh, Zvfh
Zvkn, Zvks (vector crypto)
```

看得出來是**往 Linux server 偏移**的 baseline。嵌入式不用照這個。

### RVA23 的意義

Linux distribution 廠（Ubuntu、Fedora、Debian）都對齊 RVA23 發行 RISC-V binary。這表示：

- 你的 core 想跑 Ubuntu 24.04 → 必須支援 RVA23 mandatory 清單
- SiFive 的 P670 / P870 都宣稱 RVA23 compliant
- 你發 software 只要以 RVA23 為 target，可以跑在多家硬體

**這是 fragmentation 管理的核心工具**。

## 競爭格局

### 對 ARM 的威脅

主戰場：

1. **低階 MCU**：ARM Cortex-M 被 RISC-V 替代速度最快
2. **AI/ML accelerator**：新設計直接選 RISC-V（ARM 授權費 + NVIDIA 的 AI 晶片都在用）
3. **中國市場**：因地緣政治，中國廠商幾乎全面轉 RISC-V

ARM 的反擊：

- ARM v9 加 SVE2、MTE 等獨家功能
- 更彈性的授權（Flexible Access）
- 收購來強化（但最近 NVIDIA 併購失敗）

### 對 x86 的威脅

較慢但在發生：

1. **Data center**：Ventana / Rivos / SiFive P870 都瞄準這塊
2. **客戶自研**：Google / Meta / Amazon 開始做 RISC-V server chip
3. **Edge / inference**：不是 x86 強項

x86 的護城河：Windows / Office 的 binary compatibility。**短期內 server 市場佔優，長期 x86 可能萎縮**。

## RISE Project：生態加速器

2023 年 Linux Foundation 旗下成立的 **RISE**（RISC-V Software Ecosystem）。目的：**集中各廠資源**開發 toolchain / kernel / libraries。

會員：SiFive、Intel、Andes、T-Head、Rivos、Google、Ventana、Samsung、MediaTek...

資助的 project：

- LLVM RISC-V backend 改進
- GCC RISC-V backend 改進
- glibc / musl 支援
- Linux kernel 效能
- QEMU / benchmark infrastructure

**為什麼重要**：以前 LLVM 的 RVV 很多 bug 卡住、沒廠商出錢修。RISE 統籌資源後，2024 起 LLVM RISC-V patch 流量暴增。**這是 SiFive compiler 工程師的日常協作對象**。

## Compliance 測試

光有 spec 不夠 — 每家硬體都該有方法驗證自己真的 implement 對了。RVI 有：

- **RISC-V Architecture Tests** (<https://github.com/riscv-non-isa/riscv-arch-test>)：一堆 test vector
- **ACT (Architectural Compatibility Test)**：正式命名
- **RISCOF** (framework)：run test + 比對 golden reference

SiFive 的 P-series core 出廠前要跑全套 compliance test，結果也會公開。這是為什麼客戶願意買 SiFive 的 IP。

## 開源 vs 閉源 core

RISC-V 生態有**開源 core 的百花齊放**：

- **Rocket Chip** (Berkeley) — 最早、學術標準
- **BOOM** (Berkeley) — OoO superscalar
- **CVA6 (Ariane)** (ETH Zurich) — 可寫 Linux 的 core
- **SonicBOOM** / **XiangShan** (中國) — 高效能
- **VexRiscv** (SpinalHDL) — 嵌入式

這些可以免費使用、改造、tape out。**跟閉源 ARM / x86 完全不同的生態**。

商業模式對比：

- SiFive：賣 core design（較高性能、有客服、有 roadmap）
- 學術 / 開源 core：免費，但客戶要自己 integrate、自己 support

SiFive 的競爭策略：**你買我的 core 得到整套 toolchain + BSP + Yocto recipes + compiler tuning + customer success**。這就是 job spec 的三條 responsibility 本質。

## 中國的特殊生態

中國 RISC-V 生態值得單獨一段：

- **政策推動**：為了供應鏈去美國化，國家層級支持
- **T-Head (阿里)** / **StarFive** / **Nuclei** 等廠商多元
- **玄鐵 C 系列 core** 已經有完整 Linux 支援
- **香山 (XiangShan)** 是中科院開源的高效能 core，規格對標 ARM Neoverse
- **RISC-V 專業人才培育**：清華、中科大設 RISC-V 專班

**地緣政治影響**：某些公司（尤其是美中雙棲）需要兼顧不同市場的 variant。SiFive 的全球客戶結構受此影響。

## Compiler 工程師在這個生態的位置

如果你進 SiFive，你的日常互動對象：

1. **SiFive 內部**：硬體團隊（對 feature spec）、效能團隊（對 benchmark）、客戶 success 團隊（對實地需求）
2. **LLVM / GCC upstream**：送 patch、被 reviewer 挑、討論 design
3. **RVI 某 TG**：如果你主導某 extension 的 compiler 支援，會成為 TG 成員
4. **RISE project**：SiFive 是 RISE 會員，某些 upstream 工作是跨廠合作
5. **客戶**：客戶的 compiler engineer、field engineer

**不是單純寫 code，而是大量的跨組織溝通**。技術能力打底，溝通能力決定影響力。

## 時程感

對個人 career 的建議：

- **1 年內**：能處理 well-defined 的 compiler bug / feature。
- **2-3 年**：能獨立主導一個 extension 的 compiler 支援全流程。
- **5 年**：RVI 某 TG 的活躍成員，影響 spec 本身。Upstream maintainer 級。

SiFive 的資深 compiler 工程師（Staff+）通常是 RVI TG 主席、LLVM RISC-V maintainer 級別。

## 標準化的速度

ARM 一個新特性 idea → release 通常 3-5 年。RISC-V 的快節奏：

- Bitmanip: 2018 idea → 2021 ratified（3 年）
- V 擴充: 2019 TG → 2022 ratified（3 年）
- H 擴充: 2018 → 2021 ratified（3 年）
- Zicond: 2021 idea → 2023 ratified（2 年）

**比 ARM / x86 快**。這是開放流程的效率紅利。但也有代價 — 早期 draft 用戶（v0.7.1 V 擴充的用戶）要維護 legacy toolchain 多年。

## 常見誤會

1. **「RISC-V International 像 IEEE 或 ISO」**：更像 Linux Foundation 式的 consortium，決策更敏捷但也更受會員公司影響。
2. **「RVI 控制所有 extension」**：只控制「標準擴充」。vendor custom 完全自治。
3. **「中國 RISC-V 跟國際 RISC-V 分裂」**：沒有分裂。T-Head 等仍然在 RVI 積極參與、送 upstream patch。
4. **「Profile 讓 RISC-V 失去彈性」**：相反 — Profile 讓彈性變可管理。客戶知道 baseline、想要擴就加（vendor 自由仍保留）。
5. **「SiFive 的 customer 都是美商」**：分散。台灣、歐洲、中國、韓國、日本都有。中國比例增長快。

## 準備 SiFive 面試的生態問題

可能的問題與準備：

1. **「你怎麼看 RISC-V vs ARM 的未來 5 年？」**
   - 準備：提 Ventana / Rivos 等 server 路線、MCU 市場分割、中國地緣因素、以及「不是誰取代誰，而是分市場」

2. **「RISE project 如何改變 RISC-V toolchain 生態？」**
   - 準備：從「各廠單打獨鬥」→「集資開發」→ 實際改進的領域（RVV、compiler throughput 等）

3. **「你怎麼看 profile 制度？」**
   - 準備：對開發者好（baseline 清楚）、對硬體廠有挑戰（compliance 成本）、但整體 healthy

4. **「如果你是 SiFive compiler team lead，下個 quarter 優先順序怎麼排？」**
   - 這不是考具體技術，是考 judgment。可以答：「上 quarter release 的 core 的 perf tuning 優先；RVA23 某項 extension 的支援次之；客戶卡住的 case 緊急處理」

## 動手練習

1. 上 RISC-V International 網站，找 Board of Directors 列表，看 SiFive 是否有席位、誰代表。
2. 看 RISE project 的 GitHub（<https://github.com/riseproject>），挑一個正在進行的 project 看看它的 milestone。
3. 查最新的 `RVA23 Profile spec`，列出它比 `RVA22` 額外強制的所有擴充。
4. 讀一篇中文技術 blog 分析玄鐵 C910 的優缺點（中國的 RISC-V 社群 blog 很多）。
5. 找 SiFive 最新一次 earnings call / press release（2024 / 2025），看他們強調的客戶案例與產品重點。

## 自我檢核

- [ ] 我能列出 RISC-V 生態的三層：標準組織、IP 廠、系統廠
- [ ] 我知道 RVA23 profile 的意義以及它強制哪些擴充
- [ ] 我能解釋 RISE project 如何改變生態
- [ ] 我能比較 RISC-V 跟 ARM / x86 的標準化速度
- [ ] 我能在面試中談 RISC-V 生態政治（而不是只談技術）

下一章是全課收尾 — 對 RISC-V 的批判性反思，哪些爭議、哪些未解、哪些未來。沒有人該只對一個 ISA 有單一觀點。

→ [Ch 20 反思：RISC-V 的爭議與未來](./20-reflections.md)
