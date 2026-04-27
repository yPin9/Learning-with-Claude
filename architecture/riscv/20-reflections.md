# Ch 20 — 反思：RISC-V 的爭議與未來

> 目標：對 RISC-V 做批判性反思。所有好的工程師都該對手上的工具有「優點 + 缺點 + 尚未解決」三個層面的看法。這章收尾整個課程，不講新技術，只讓你腦中有一張 RISC-V 的完整地形圖。

## RISC-V 真正解決了什麼

先肯定優點，不然批判沒有基準：

1. **把 ISA 設計的門檻打開**：以前要做 CPU ISA 設計要 billion dollar 公司規模。現在大學論文、新創、任何人都能做。這是長期最有意義的成就。
2. **Modular 的實用驗證**：RISC-V 證明「base + optional extensions」的設計能 scale，從 MCU 到 server 共享大量生態。
3. **compiler-ISA 共演化快**：ratification 流程比 ARM / x86 快一倍以上。
4. **地緣政治去中心化**：任何國家的廠商都可以參與，不受美國出口管制綁死。

這些成就真實存在。後面批評的部分不否認這些成績。

## 爭議 1：Weak memory model 是不是錯

RVWMO 跟 ARM AArch64 一樣弱。**但有人認為這是歷史錯誤**：

- 寫 lock-free code 的負擔極重，bug 難 debug
- 2024 的硬體設計能 afford TSO 的成本（x86 都做得到）
- 為了給 OoO CPU 彈性而犧牲 programmer productivity，trade-off 可疑

**反方論述**：weak model 讓 RISC-V 能 scale 到最簡硬體（MCU）。TSO 強制對小核心不合理。

**我的觀點**：中偏弱是合理選擇。但 `Ztso` 這類 "TSO mode" 擴充未來可能越來越流行（已有提案）。

## 爭議 2：缺少 condition code / conditional execution

ARM 早期有 conditional execution。RISC-V 一開始拒絕，後來 Zicond 補了 `czero` 但被詬病不夠。

**問題**：某些 cryptography workload（constant-time 要求）、sort 等在 RISC-V 上比 ARM 慢，因為 branch 更多。

**修補**：Zicond 補了一部分。更完整的 cmov 可能未來加入。

**我的觀點**：這是「先砍完再補」的典型。最後比 ARM 更簡潔，但中間痛苦了幾年。

## 爭議 3：Fragmentation 的現實風險

Profile 制度想收斂，但現實：

- 不同廠的 vendor extension（XSf / XTHead / XAndes）讓 binary 互不相容
- 不同 profile（RVA22 vs RVA23）轉換期有 binary 不能跨版本跑
- 開源 core 多元，compliance 參差不齊

**對比**：ARM 的 licensing 制度強制所有廠都用同一個 ISA。**簡單但成本高**。

**折衷方案**：RVI 推的 profile 制度本質是「軟強制」— 市場壓力讓廠商自願對齊。

**我的觀點**：未來 3-5 年會有一波「同一個 core 宣稱支援多個 profile」的潮流。用戶體驗會靠近 ARM 但成本較低。

## 爭議 4：Vector 擴充的複雜度

RVV 1.0 設計漂亮，但：

- **LMUL + SEW + VLEN 的三重組合**讓 compiler 寫 auto-vectorizer 極難
- **Fractional LMUL 的 register 重疊**是一個惡夢
- **vsetvl 的放置** 成為效能關鍵，loop overhead 不容忽視

**對比**：ARM SVE 不用 vsetvl（用 predicate），某種意義上更簡單。

**有人說**：RVV 過度設計、犧牲 compiler productivity 換硬體彈性。

**反方**：LMUL 讓硬體 designer 能 tune trade-off、同一個 ISA 從 128-bit SoC 到 1024-bit HPC 都適用。

**我的觀點**：RVV 是「設計師 vs compiler 工程師」的典型衝突。LLVM 團隊現在每季都在改進 RVV pass，2027 前會穩。

## 爭議 5：Address mode 太窮

RISC-V 的 memory access 只能 `base + imm12`。對比：

- x86: `[base + index*scale + disp32]`
- ARM: `[base + offset]`、pre/post-increment、scaled index

寫 inner loop（尤其 array / struct access heavy）RISC-V 會比較冗長。

**Zba 的 `sh*add` 補了 shift-add**，但沒 pre/post-increment。

**T-Head 的 XTHeadMemIdx / XTHeadMemPair 就是為了補這個**（Ch 12）。但那是 vendor extension、不進標準。

**我的觀點**：標準化一個「minimal addressing extensions」（給 pre-inc / post-inc 之類）是社群可以討論的方向。目前沒 proposal。

## 爭議 6：對硬體簡單的迷思

RISC-V 自我宣傳「硬體簡單」。但：

- 現代 SiFive P870 等高效能 core 的 die size 已經跟 ARM Neoverse 接近
- 加 V / B / Zc / 各 vendor extension 後，decoder 其實不簡單了
- 為了 compete，硬體一樣要做 OoO、branch predictor、cache hierarchy

**實話**：base ISA 簡單，但要高效能還是得加一大堆東西。最終 die size 不比 ARM 小多少。

**RISC-V 真正的優勢在「成本」而不是「size」**：開放 ISA + no royalty + 可以自己設計變種。

## 未解問題 1：Binary distribution 策略

Linux distro 要怎麼發 RISC-V binary？選項：

- **RVA23 baseline**：所有硬體通吃，但新 extension 用不到
- **Multi-version build**：x86-64-v1/v2/v3/v4 類似策略，但 binary size 大
- **Function multi-versioning**：runtime 選最佳版本，複雜

目前 Ubuntu / Fedora 都走 RVA23 baseline。**長期不理想** — 性能差距會越拉越大。

## 未解問題 2：ASIC-like 客製化的極限

RISC-V 的賣點之一是「客戶可以加 custom extension」。但：

- custom extension 多了，toolchain fragment 嚴重
- 客戶維護自家 compiler fork 成本高
- upstream 不收 vendor-specific 的 patch 很多情況下

**這會限制 RISC-V 在「純客製領域」的成長**。我認為未來會有「custom extension 標準化框架」來降低入門成本。

## 未解問題 3：Power management 的標準

現代 CPU 的 power 省電機制（sleep state、DVFS）大量依賴 platform 特定的 MSR / CSR。x86 有 Intel/AMD 的 MSR，ARM 有 PSCI。

**RISC-V 還沒有一個完整標準**。Sbi 的部分 power 操作有，但不完整。

各廠各自實作 → driver fragmentation。未來需要類似 ARM PSCI 的標準。

## 未解問題 4：Security extension 的位置

ARM 有 TrustZone / CCA，x86 有 SGX / SEV。RISC-V 有：

- **PMP**（但只是 memory protection，不是 enclave）
- **WorldGuard**（SiFive 的 proprietary 方案）
- **CHERI**（劍橋的 capability 方案，跨 ISA 通用）
- **Smpmp / Ssmpmp / Sstc** 等標準化中的 privileged extension

沒有統一的 "secure enclave" 機制。這在嚴肅 security-conscious 的市場（銀行、政府、醫療）是障礙。

## 未來方向

### 短期（1-2 年）

- RVA23 profile 全面採用
- Vector crypto 擴充在 Linux kernel 全面用上
- RVV 的 compiler 優化全面成熟
- 大批 RISC-V server chip 出貨（Ventana Veyron、Rivos）

### 中期（3-5 年）

- RISC-V 在 data center 站穩一角
- 高階 Linux 桌面 RISC-V 變成可用選項
- 中國市場 RISC-V 可能佔主導
- ARM 對 RISC-V 可能降授權費反擊

### 長期（5-10 年）

- ISA 大戰可能定型三足鼎立：x86 守老市場、ARM 守 mobile、RISC-V 拿新 workload
- 可能出現 RISC-V 的 "standard" architecture profile，類似 ARM 的 Neoverse
- AI 加速器可能全面走 RISC-V 路線（N5 已經在發生）

## 不該忽視的挑戰

有些問題可能拖慢 RISC-V：

1. **Toolchain quality 的絕對值**：LLVM / GCC 的 RISC-V backend 雖然進步快，但成熟度跟 x86 backend 仍差距。業界要 5-10 年補齊。

2. **Benchmark 的 credibility**：RISC-V 的 SPEC2017 分數論壇有爭議（某些公佈的成績用了激進 tuning）。建立 credibility 需要時間。

3. **Debug / profile 工具**：Intel 的 VTune / ARM 的 Streamline 級別在 RISC-V 還沒出現。perf 工具鏈不完整。

4. **Industry inertia**：Fortune 500 的 deep x86 investment 短期不會動。RISC-V 先在新領域拓。

## 對 SiFive 工程師的意義

如果你進 SiFive，要準備好：

- **在 fast-moving ecosystem 裡工作**：spec 每年變、toolchain 每季改、客戶要求每月變
- **跨組織協作是日常**：內部、upstream、客戶、RVI
- **有時你要做 trade-off 的判斷**：沒有 obvious answer，要講 trade-off
- **既要深也要廣**：對 compiler / ISA / hardware 全部都要懂基本

這就是 compiler 工程師在 RISC-V 的魅力。**不是寫 code，是設計一個生態的未來**。

## 個人建議：不要只學 RISC-V

雖然本課全部講 RISC-V，但**只懂 RISC-V 的人會被淘汰**。建議：

1. **x86-64 會基本 asm 閱讀**：兼顧 legacy workload
2. **ARM AArch64 深入一點**：RISC-V 的姊妹 ISA，很多概念共通
3. **GPU ISA 基本概念**：CUDA / OpenCL / Metal shader 背後的 ISA model
4. **virtualization 基礎**：KVM / hypervisor

多 ISA 視角讓你在面試能講「RISC-V 為什麼選這個，對比 x86 是不同選擇因為...」這種深度論述。

## 這堂課 roughly 覆蓋了什麼

回顧課程：

- **Ch 1-3**：RV32I 基座 + ABI + pseudo
- **Ch 4-5**：標準擴充 + Privileged
- **Ch 6-10**：細分擴充（Zicsr / Zifencei / Zicond / B / V / Zc / H）
- **Ch 11-13**：Custom extension 設計流程 + vendor 巡禮 + ratification
- **Ch 14-15**：Memory model + atomics
- **Ch 16-20**：Spec 閱讀 + 對照 + 生態 + 反思

**下一步學習路徑**：

```
riscv (本課)
     │
     ▼
elf_linking (下一門建議課)
     │
     ▼
compiler_backend (最後的技術深度)
     │
     ▼
實戰：送 LLVM patch、跑 Coremark、面試 SiFive
```

Ch 16 / Ch 18 的 skill 是整套課程的共同基礎。Ch 11 / Ch 13 / Ch 19 是跟 SiFive 職位最相關的「行業知識」。

## 面試前清單

如果下週要面 SiFive，重點：

1. 能手寫 RV32I 的 hello world、解釋每行意義（Ch 1 / Ch 3）
2. 能解釋 ABI 的 caller/callee saved（Ch 2）
3. 能寫一個 trap handler 大綱（Ch 5）
4. 能說出 V 擴充的 VLA model、vsetvl 用途（Ch 8）
5. 能講 custom extension 設計流程（Ch 11）
6. 能手解 + 手組 opcode（Ch 16）
7. 能聊 memory model、能寫 lock-free pattern（Ch 14 / 15）
8. 有一個「我看過並能討論」的 LLVM RISC-V patch 或 spec issue

這份清單你全做到，面試 SiFive compiler 職位是很強的候選人。

## 動手練習

1. 寫一篇 300 字的「我對 RISC-V 未來五年的看法」。面試結尾經常被問這種。
2. 選一個本課提的爭議，寫一個正方 + 反方的論述各 100 字。
3. 列出你覺得 RISC-V 相對其他 ISA 最強的三個面向，最弱的三個面向。
4. 想 5 個你會問 SiFive 面試官的好問題（反向面試題）。
5. 更新自己的 LinkedIn / 履歷，體現這門課的收穫（concrete action, 不只「學了 RISC-V」）。

## 自我檢核

- [ ] 我能講 RISC-V 的三個真正成就，以及三個真正爭議
- [ ] 我知道 Fragmentation、Vector 複雜度、Addressing 限制等具體問題
- [ ] 我能預測 RISC-V 未來 5-10 年的大致走向
- [ ] 我能用「優缺點雙面」的方式討論 RISC-V，而不是只 pro-RISC-V
- [ ] 我已經在腦中畫出從本課到 compiler_backend 的完整學習路徑

Part 6 結束，整個 Ch0–Ch20 結束。接下來是兩個 hands-on 練習 + 一個 final project。這些才是把 theory 變 muscle memory 的關鍵。

→ [練習 A：手解 opcode](./practice-a-decode-by-hand.md)
→ [練習 B：用 spike 跑 baremetal](./practice-b-baremetal-on-spike.md)
→ [Final Project：Mini RV32I Emulator](./final-project-rv32i-emulator.md)
