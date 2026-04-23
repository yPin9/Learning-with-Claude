# Ch 13 — 擴充是怎麼從 proposal 走到 ratified 的

> 目標：理解 RISC-V International 的治理結構、extension 的生命週期（idea → draft → frozen → ratified）、以及為什麼 compiler 工程師要關心「某擴充現在在哪個狀態」— 這直接影響能不能把它開在 customer binary 裡。

## 為什麼這章重要

SiFive 工程師的日常會問「這個功能我要用 Zfh 還是自己加 XSf？」答案取決於：

- Zfh 現在是 draft 還是 ratified？
- 目標 core 是否承諾支援？
- 客戶的 kernel / toolchain 鏈支援版本如何？

這些都是**標準化流程**的問題。看不懂 RISC-V 社群的 release cadence，你就無法做正確的工程決策。

## RISC-V International：誰在治理

RISC-V 不是某公司的產品。**RISC-V International** 是瑞士非營利組織，成員包括 SiFive、Intel、NVIDIA、Google、Alibaba、Huawei 等 3500+ 會員。

治理結構：

```
Board of Directors
       │
       ▼
Technical Steering Committee (TSC)     ← 決定 extension 能不能 ratify
       │
       ▼
Task Groups (TG)                        ← 各領域工作小組
       │
       ▼
Special Interest Groups (SIG)           ← 更細分的主題
```

Task Groups 負責具體 extension 的設計。例：
- Vector TG（負責 V / Zv* 系列）
- Crypto TG
- Privileged ISA TG
- ABI TG

一個 extension 通常由某個 TG 孵化、走完審查、最後由 TSC 投票 ratify。

## Extension 的五個生命階段

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│  Idea    │ → │  Draft   │ → │  Frozen  │ → │ Ratified │ → │Deprecated│
│ (issue)  │   │ (v0.x)   │   │ (v1.x RC)│   │ (v1.0)   │   │ (retired)│
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

### 1. Idea

有人在 GitHub 開 issue，或論壇貼文。「我想做個 ASCII-matching 的指令」— 這是 idea。

### 2. Draft (v0.x)

TG 收下來、寫 spec 草稿。版本號小於 1：`Zvfh 0.3`、`Zvk 0.8`。

**這階段的 extension 不穩定** — 下次版本可能改 opcode、改語意、甚至整個廢掉。

**compiler 會怎麼處理？**
- 通常會 feature-flag 加進去，但加上 `-menable-experimental-extensions` flag 才能用
- 不寫進預設 profile
- 文件會警告「ABI 可能改變」

### 3. Frozen (v1.x-rc / release candidate)

設計底定、進入「公開評論期」。90 天不再改動、等 compiler / 硬體廠測試。

如果期間沒大問題 → 進 ratified。有問題 → 退回 Draft。

**compiler 會怎麼處理？**
- 會預設開啟（`-march` 可用）
- 開始進 profile 清單
- 但仍標註 "may have minor changes"

### 4. Ratified (v1.0)

正式標準。TSC 投票通過。版本號進整數（1.0、2.0...）。

**此後的變動只能是小修訂（v1.0.1）、不能改語意**。

### 5. Deprecated

極少發生。例：`Zifencei` 的取代進行中。deprecated 不代表消失，只是不建議新系統用。

## 時間線實例：V 擴充

展示一個 extension 走完全程的速度：

```
2015  Berkeley RISC-V team 發表 vector ISA 論文
2019  V 擴充 WG 成立
2020  v0.8 spec 草稿出，開始 compiler 實驗
2021  v0.10 進 LLVM（experimental）
2022  v1.0 frozen
2022  v1.0 ratified ✓
2024  RVA22 profile 把 V 列為 optional
2025  LLVM 主流 RVV 支援穩定
```

**從 idea 到 ratified 花了 7 年**。這是 RISC-V 較複雜 extension 的典型速度。小型 extension（Zbb）可能 2 年內完成。

## Bitmanip 的故事：為什麼拖了這麼久

**理想 case：Zbb 應該很快**。結論：ratified 2021 — 距離 idea (2015) 有 6 年。

為什麼？早期 Bitmanip TG 想做一個**大而完整**的 B 擴充，包含 80+ 指令。審查委員不斷說「這個刪了吧」、「那個合併到那個裡」。拖了兩年後決定拆成 Zba/Zbb/Zbc/Zbs 四塊。這樣每塊都可以獨立 ratify。

**教訓**：設計 extension 要小而專。SiFive Intelligence 系列故意拆成一堆小 XSf*，不是一個大 XSfIntel。就是吸取這種教訓。

## 為什麼 compiler 要跟著版本走

考慮 `Zvfh`（vector FP16）。2023 年有三個版本流通：

- `Zvfh 0.2`：原始草稿
- `Zvfh 0.3`：opcode 改過一版
- `Zvfh 1.0`：ratified 版本

如果你的 compiler 生 `Zvfh 0.2` 的編碼、客戶硬體是 `Zvfh 1.0`，**binary 直接跑錯**（opcode 衝突）。這就是為什麼：

```
-march=rv64gc_zvfh            # 預設最新穩定版
-march=rv64gc_zvfh0p2         # 強制 0.2 (過時但有客戶硬體還這樣)
-march=rv64gc_zvfh1p0         # 強制 1.0
```

**compiler 要支援多個版本共存**。SiFive 的 toolchain team 要 maintain 這些 legacy 支援。

## Profile：跨代相容的救生索

`RVA20` / `RVA22` / `RVA23` 是 "Application profile"：針對 user-mode Linux application 的**承諾**。

RVA22 的承諾：
- 保證 RV64I + M + A + F + D + C + Zicsr + Zifencei + Zicntr + Zihpm + 對齊 access + ...
- Optional 可以加 V、B、H 等

RVA23 更進一步：
- 把 V、Zba/Zbb/Zbs、H（server）列為 mandatory

**Linux distro（Ubuntu、Fedora）選 profile 當 target**，對外承諾「RVA22 distro」或「RVA23 distro」。顧客買硬體只要看「支援 RVA22 嗎」，不用研究一大串 extension。

Profile 是 fragmentation 的最佳解。**跟 SiFive 合作的客戶會問：你的 core 符合哪個 profile？** 能回答這個問題是基本素養。

## 從哪裡追蹤標準動態

### 官方

- <https://wiki.riscv.org> — 各 TG / SIG 的 page
- <https://lists.riscv.org> — 郵件列表，超活躍
- <https://github.com/riscv> — 所有 spec 的 repo

### Spec 的版本狀態

每個 spec 的 README 會寫 `Status: Draft / Frozen / Ratified`。例：
- `riscv-v-spec` 的 main branch 永遠是最新 ratified 版
- 新 feature 在 branch（例 `vpriv-1p13`）

### 郵件列表

- **tech-announce**：ratification announcements
- **tech-vector**：Vector TG 討論
- **tech-tools**：toolchain 相關
- **isa-dev**：general architecture

訂閱 tech-announce 就夠跟上 ratification。訂閱具體 TG 的要時間消化，除非你是 TG 成員。

## Vendor 怎麼跟？

SiFive / T-Head / Ventana 這類廠商的內部流程：

1. **Spec watching team**：每週追 RISC-V International 動態
2. **Roadmap 對齊**：未來 12–24 個月的 core 支援哪些 extension
3. **Early access**：跟 TG 合作，有些 draft extension 先在自家 core 實作
4. **Upstream**：把自家 extension 的 LLVM/GCC 支援送 upstream

**這個過程中 compiler 工程師的角色是核心之一**。你可能同時在：

- 實作一個新 ratified extension
- 幫某 draft extension 做 prototype 評估
- 維護 legacy 版本支援
- 送 patch 給 upstream LLVM

## 實務上最常被問的問題

### Q：我的客戶有 Zfh 0.2 硬體，compiler 怎辦？

- 兩個 approach：
  - A. 把 compiler fork 住 Zfh 0.2，內部維護（SiFive 常做）
  - B. 勸客戶升級硬體（長期）
- 實務上兩邊並行，舊 binary 跟新 binary 都能出

### Q：我想用一個 draft extension 出產品，可以嗎？

- Draft 期間自負風險。spec 可能改 → 產品要 re-cert
- 歷史上大廠會選有信心的 draft（例：V 擴充 0.7.1 就有一批出貨）
- 通常配合「lock 住 opcode」的合約條款

### Q：RVA23 什麼時候會變主流？

- 2025 開始有商用 chip 宣稱支援
- 2027 預期成為 server 市場主流
- 嵌入式繼續用 RVA22 或 profile-less 好多年

## 跟 CI/CD 的關係

SiFive 的 compiler CI 會有矩陣：

```
Compile target: RVA20, RVA22, RVA23
×
Extensions: +Zvkn, +Zvkb, +XSfVcp, ...
×
QEMU / Spike / FPGA hardware tests
```

幾百個組合。**當你加一個新 extension，要確保所有目標都過**。

這也是 SiFive 為什麼需要 Yocto — 大量的 toolchain recipe 變體、每個 target 要對應 rootfs、CI 要自動化建置。

## 常見誤會

1. **「Ratified 就不會變」**：真的。小 typo fix（errata）會繼續改 spec 文字，但指令語意不改。
2. **「Draft extension 沒人用」**：大錯。SiFive 早期支援 V 0.7.1，出了百萬顆 chip。廠商會押賭注。
3. **「TSC 投票跟政府投票一樣」**：TSC 由 RISC-V International member 投，不是代議制。但大公司影響力大（SiFive、Intel、NVIDIA 都有 TSC seat）。
4. **「所有 extension 都會進 profile」**：不會。profile 刻意收斂，vendor 的 X* 永遠不進 profile。
5. **「只有 compiler 需要跟版本」**：錯。kernel / libc / JIT 都要。Linux kernel 有專門的 feature-detection 機制（依 DT / boot config 判斷 extension 可用性）。

## 動手練習

1. 讀 RVA23 profile spec（<https://github.com/riscv/riscv-profiles/blob/main/src/rva23-profile.adoc>），列出它比 RVA22 多要求的 extension。
2. 訂閱 tech-announce 郵件列表，看最近三個 ratification 是什麼。
3. 在 LLVM 的 `RISCVFeatures.td`，找出哪些 extension 被標記 `Experimental`。這是「draft」的 compiler 層對應。
4. 查 SiFive P870 的 datasheet（公開版），看它宣稱支援哪些 extension。對照 RVA23 看差距。
5. 想一個假設情境：「你的客戶要用 Zfh 0.3 早期版本」，寫 200 字說明你會怎麼管理這個 compiler 支援（包含版本 flag、legacy maintenance、upstream 策略）。這是面試系統設計題的好練習。

## 自我檢核

- [ ] 我能說出 extension 生命週期的五個階段
- [ ] 我能解釋 profile（RVA22 / RVA23）跟 extension 的關係
- [ ] 我知道 draft extension 如何在 compiler 中被標示並用 `-menable-experimental-extensions` 啟用
- [ ] 我能列出追蹤 RISC-V 標準動態的三個資訊源
- [ ] 我能解讀「我的 core 對應哪個 profile」這類客戶問題

Part 4 結束。接下來是 Part 5 — memory model 與 atomic。這兩章是「RISC-V 在多核下怎麼保證正確」的核心。對 compiler 工程師很重要：生 `fence` 生錯種類 = 程式偶爾壞一次、幾乎不可 debug。

→ [Ch 14 RVWMO memory model 最小必懂](./14-memory-model.md)
