# Ch 10 — Yocto vs Buildroot：何時該選誰

> **目標**：理解 Buildroot 作為 Yocto 的輕量替代——兩者的設計哲學差異、各自擅長的場景、以及怎麼在 Buildroot 裡整合你的 patched toolchain。SiFive 的客戶可能用 Yocto 或 Buildroot，compiler 工程師要知道兩者怎麼 support。這是本課最後一章，把 Yocto 放進更大的脈絡（嵌入式 build 系統的選擇）。

> **環境**：概念為主，搭配 Buildroot 的對照。

## 為什麼要懂 Buildroot？

Yocto 不是唯一的嵌入式 Linux build 系統——**Buildroot** 是另一個主流選擇，比 Yocto **簡單輕量**。SiFive 的客戶有的用 Yocto（複雜但強大）、有的用 Buildroot（簡單快速）。作為 compiler 工程師，你要知道**兩者怎麼整合你的 patched toolchain**——客戶用 Buildroot，你也要能把 patched GCC 整合進去。

理解 Yocto vs Buildroot 的取捨，讓你能：(1) 在實際專案幫客戶選對的 build 系統（或理解他們的選擇）；(2) 知道怎麼在 Buildroot 整合 toolchain patch（和 Yocto 不同的方式）；(3) 把 Yocto 放進更大的脈絡（不是唯一的選擇，理解它的定位）。這章對照兩者，補完你對嵌入式 build 系統的全貌理解。

## 先建立直覺:工廠 vs 工具箱

```
Yocto vs Buildroot 的設計哲學：

  Yocto（工廠）：
    複雜、強大、可擴展
    layer 系統、recipe、package management、可重現
    適合：大型/長期專案、多產品線、需要 package 管理
    代價：學習曲線陡、build 慢、複雜
        │
  Buildroot（工具箱）：
    簡單、直接、輕量
    一個 Makefile-based 系統，menuconfig 選套件
    適合：小型/簡單專案、快速原型、單一產品
    代價：客製化能力較弱、沒有 package management（rebuild 整個）
        │
  → Yocto 是「可擴展的工廠」（強大但複雜）
    Buildroot 是「簡單的工具箱」（輕量但較不靈活）
    選擇看專案規模和需求
        │
  共同點：都從 source build 整個 distro（含 toolchain）
    都要整合你的 patched GCC（方式不同）
```

關鍵心智：**Yocto**（工廠）——複雜、強大、可擴展（layer/recipe/package management），適合大型/長期/多產品專案，代價是學習曲線陡、build 慢。**Buildroot**（工具箱）——簡單、直接、輕量（Makefile-based、menuconfig 選套件），適合小型/簡單/快速原型，代價是客製化較弱、沒 package management。選擇看專案規模。兩者都從 source build distro，都要整合你的 patched toolchain（方式不同）。

## Yocto vs Buildroot 對照

```
                Yocto                    Buildroot
────────────────────────────────────────────────────────
設計          layer + recipe 系統        Makefile + Kconfig
複雜度        高（學習曲線陡）           低（menuconfig 上手快）
build 速度    慢（但有 sstate 快取）     快（簡單直接）
package mgmt  有（.ipk/.deb/.rpm）        無（rebuild 整個 image）
可擴展性      高（layer 模組化）         中（patch 機制較簡單）
可重現性      高（嚴格的版本鎖定）       中
客製化        強（但複雜）               簡單的客製容易，複雜的難
適合          大型/長期/多產品           小型/簡單/原型
社群/BSP      廣（廠商多用 Yocto）       廣（但 BSP 較少）
        │
  → 大型專案、多產品線、需要 package 更新 → Yocto
    小型專案、快速原型、單一固定 image → Buildroot
        │
  關鍵差異：package management
    Yocto 有（能單獨更新套件，OTA update）
    Buildroot 無（要更新就 rebuild 整個 image）
    → 需要 field update / OTA → Yocto
```

> **關鍵差異是 package management——Yocto 有（能單獨更新套件、OTA）、Buildroot 無（更新要 rebuild 整個 image），這決定大型 vs 小型專案的選擇**。Yocto 和 Buildroot 的主要對照：**設計**（Yocto 的 layer+recipe vs Buildroot 的 Makefile+Kconfig）、**複雜度**（Yocto 高、Buildroot 低）、**build 速度**（Buildroot 快、Yocto 慢但有 sstate 快取）、**可擴展性**（Yocto 高，layer 模組化）。但**最關鍵的差異是 package management**——**Yocto 有**（產生 .ipk/.deb/.rpm 套件，能**單獨更新某個套件**、支援 **OTA update**（在 field 更新裝置）），**Buildroot 無**（沒有套件概念，**要更新就 rebuild 整個 image** 重新 flash）。這個差異決定選擇：**需要 field update / OTA / package 更新 → Yocto**（能單獨更新套件，不用每次重 flash 整個 image）；**單一固定 image、不需要 field update → Buildroot**（簡單，更新就重 build 重 flash 也可接受）。其他考量：**大型/長期/多產品線 → Yocto**（layer 的模組化、可重現性、package 管理適合複雜需求）；**小型/簡單/快速原型 → Buildroot**（簡單快速，menuconfig 選套件就好，學習曲線低）。對 compiler 工程師，理解這個取捨讓你**理解客戶的選擇**——用 Yocto 的客戶通常是大型/長期專案（需要 package 管理、多產品），用 Buildroot 的是小型/簡單專案。兩者沒有絕對的好壞，是**取捨**（Yocto 強大但複雜、Buildroot 簡單但較不靈活）——根據專案需求選。SiFive 的客戶兩者都有，你要能 support 兩者。

## 在 Buildroot 整合 toolchain patch

```bash
# Buildroot 整合 patched toolchain 的方式（和 Yocto 不同）
# Buildroot 用 Kconfig（menuconfig）+ package 的 .mk 檔

# === Buildroot 的 toolchain 設定 ===
# make menuconfig
# Toolchain → Toolchain type
#   - Buildroot toolchain（Buildroot 自己建 gcc）
#   - External toolchain（用外部的，如 SiFive 提供的）

# === 整合你的 patch（Buildroot 方式）===
# 方式 1：patch 放進 Buildroot 的 package patch 目錄
# package/gcc/<version>/your-patch.patch
# → Buildroot build gcc 時自動套用（類似 Yocto 的 SRC_URI patch）

# 方式 2：用 BR2_GLOBAL_PATCH_DIR（全域 patch 目錄）
# 在 menuconfig 設 global patch directory
# 放你的 patch，Buildroot 自動套用

# 方式 3：External toolchain（用你預先建好的 patched toolchain）
# SiFive 提供建好的 patched toolchain，客戶在 Buildroot 設 external toolchain

# === Buildroot vs Yocto 的 patch 整合 ===
# Yocto：.bbappend + SRC_URI:append（layer 模組化）
# Buildroot：package patch 目錄 或 BR2_GLOBAL_PATCH_DIR（較直接）
# → 兩者都是「加 patch 到 gcc 的 build」，機制不同
#   Yocto 的 layer 系統較模組化，Buildroot 較直接簡單
```

> **Buildroot 整合 patch 用 package patch 目錄 / BR2_GLOBAL_PATCH_DIR / external toolchain——比 Yocto 的 .bbappend 直接簡單，但較不模組化**。在 **Buildroot** 整合 patched toolchain 的方式（和 Yocto 的 .bbappend 不同）：(1) **package patch 目錄**——patch 放進 `package/gcc/<version>/your-patch.patch`，Buildroot build gcc 時自動套用（類似 Yocto 的 SRC_URI patch，但放在固定目錄）；(2) **`BR2_GLOBAL_PATCH_DIR`**——設一個全域 patch 目錄（menuconfig），放你的 patch，Buildroot 自動套用（不用改 Buildroot 本身的 package 目錄，較乾淨）；(3) **External toolchain**——用**預先建好的 patched toolchain**（SiFive 提供建好的 patched toolchain，客戶在 Buildroot 設 external toolchain 用它，不在 Buildroot 裡 build gcc）。**Buildroot vs Yocto 的 patch 整合**——Yocto 用 **.bbappend + SRC_URI:append**（layer 模組化，你的 patch 在你的 layer）、Buildroot 用 **package patch 目錄 / BR2_GLOBAL_PATCH_DIR**（較直接簡單，但較不模組化）。兩者都是「加 patch 到 gcc 的 build」，機制不同——Yocto 的 layer 系統較模組化（適合複雜的多方客製）、Buildroot 較直接簡單（適合簡單的 patch）。對 compiler 工程師，理解兩者的整合方式讓你能 **support 用 Buildroot 的客戶**——客戶用 Buildroot，你提供 patch（放 package patch 目錄或 global patch dir）或預建的 external toolchain。**External toolchain** 是常見的交付方式——SiFive 建好 patched toolchain，客戶（用 Yocto 或 Buildroot）設成 external toolchain 用它（不用自己 build gcc）。理解這個，你能交付 patched toolchain 給不同 build 系統的客戶（Yocto 用 .bbappend/SDK、Buildroot 用 patch 目錄/external toolchain）。這把你的 toolchain 整合能力擴展到 Buildroot——不只 Yocto。

## 故意弄壞:選錯 build 系統的後果

```
選錯 build 系統的後果（理解取捨的重要）：

  情境 1：小專案選了 Yocto
    症狀：學習曲線陡、build 慢、過度複雜
    後果：團隊花大量時間學 Yocto，簡單的需求被複雜化
    → 小專案/原型 Buildroot 更合適（快速、簡單）
        │
  情境 2：大專案/需要 OTA 選了 Buildroot
    症狀：要更新某個套件，但 Buildroot 沒 package management
    後果：每次更新都 rebuild 整個 image、重 flash（field update 困難）
    → 需要 package 更新/OTA → Yocto（有 package management）
        │
  情境 3：多產品線選了 Buildroot
    症狀：每個產品都要維護一套 config，共用困難
    後果：難以模組化共用（Yocto 的 layer 適合這個）
    → 多產品線 → Yocto（layer 模組化共用）
        │
  → 選對 build 系統很重要（影響整個專案的效率）
    沒有絕對好壞，是取捨：
      簡單/快速/單一 → Buildroot
      複雜/可擴展/多產品/OTA → Yocto
    根據專案需求選，不是「Yocto 比較強就用 Yocto」
```

> **選錯 build 系統的後果（小專案選 Yocto = 過度複雜、需要 OTA 選 Buildroot = 更新困難）——沒有絕對好壞，根據專案需求選**。理解 Yocto vs Buildroot 的取捨很重要——**選錯的後果**：(1) **小專案選 Yocto**——學習曲線陡、build 慢、過度複雜（團隊花大量時間學 Yocto，簡單需求被複雜化）→ **小專案/原型 Buildroot 更合適**（快速簡單）；(2) **大專案/需要 OTA 選 Buildroot**——Buildroot 沒 package management，要更新某套件得 rebuild 整個 image 重 flash（field update 困難）→ **需要 package 更新/OTA → Yocto**；(3) **多產品線選 Buildroot**——每個產品維護一套 config，共用困難 → **多產品線 → Yocto**（layer 模組化共用）。**沒有絕對好壞，是取捨**——**簡單/快速/單一 → Buildroot**、**複雜/可擴展/多產品/OTA → Yocto**。根據**專案需求**選，不是「Yocto 比較強就用 Yocto」（小專案用 Yocto 是過度工程）也不是「Buildroot 簡單就用 Buildroot」（大專案用 Buildroot 會撞到 package management 的牆）。對 compiler 工程師，理解這個讓你**理解和尊重客戶的選擇**——客戶用 Buildroot 不是「他們不懂」，可能是他們的專案適合 Buildroot（簡單/快速）；客戶用 Yocto 是因為需要它的能力（package 管理/多產品/OTA）。你的工作是 **support 兩者**（把 patched toolchain 整合進客戶用的 build 系統），不是推銷某一個。這章把 Yocto 放進更大的脈絡——它是嵌入式 build 系統的一個選擇（強大但複雜），Buildroot 是另一個（簡單輕量），各有適用。理解這個全貌，你對嵌入式 build 系統有完整的認識，能在實際專案做出或理解對的選擇，並 support 不同 build 系統的客戶。這完成了 yocto 課——從 build 第一次（Ch 0）到理解 Yocto 在更大脈絡的定位（Ch 10），你具備了「看懂 recipe、改 recipe 把 patched GCC 塞進 RISC-V image、debug 問題、理解 build 系統選擇」的完整能力。

## 動手練習

1. 對照取捨：對照 Yocto 和 Buildroot 的差異（複雜度/package mgmt/適用場景）

2. 試 Buildroot（選做）：下載 Buildroot，menuconfig 選 RISC-V，build 一個簡單 image

3. patch 整合：理解 Buildroot 整合 patch 的方式（package patch 目錄 vs Yocto 的 .bbappend）

4. 選擇練習：給幾個專案情境（小原型/大產品線/需要 OTA），判斷該用哪個

5. external toolchain：理解 external toolchain 的交付方式（給 Yocto 和 Buildroot 客戶都適用）

## 本章重點整理

- Yocto（工廠，複雜強大可擴展）vs Buildroot（工具箱，簡單輕量）——兩個主流嵌入式 build 系統
- 關鍵差異：package management——Yocto 有（單獨更新套件、OTA）、Buildroot 無（更新要 rebuild 整個 image）
- 選擇：簡單/快速/單一 → Buildroot；複雜/可擴展/多產品/OTA → Yocto；根據專案需求，沒絕對好壞
- Buildroot 整合 patch：package patch 目錄 / BR2_GLOBAL_PATCH_DIR / external toolchain（比 Yocto .bbappend 直接）
- compiler 工程師要 support 兩者；external toolchain 是給不同 build 系統客戶的通用交付方式

## 自我檢核

- [ ] 理解 Yocto 和 Buildroot 的設計哲學差異
- [ ] 知道關鍵差異（package management）和它怎麼影響選擇
- [ ] 能根據專案需求判斷該用哪個
- [ ] 知道 Buildroot 怎麼整合 toolchain patch（和 Yocto 不同）
- [ ] 理解 compiler 工程師要 support 兩者

## 延伸閱讀

### 官方

- **[Buildroot Manual](https://buildroot.org/downloads/manual/manual.html)** — Buildroot
  - **讀哪裡**：Buildroot 的概念、toolchain、patch 整合
  - **為什麼值得讀**：Buildroot 的權威（理解 Yocto 的替代）

- **[Yocto vs Buildroot 比較](https://www.yoctoproject.org/development/technical-overview/)** — 各種比較文章
  - **為什麼值得讀**：兩者的取捨分析

### 文章

- **[Buildroot vs Yocto](https://jumpnowtek.com/yocto/Understanding-what-the-yocto-project-gives-you.html)** — 各種比較
  - **這篇說什麼**：何時用哪個的實務分析
  - **為什麼值得讀**：本章取捨的實務補充

### 影片

- **[Buildroot vs Yocto talks](https://www.youtube.com/results?search_query=buildroot+vs+yocto)** — 各種研討會
  - **為什麼值得讀**：業界對兩者取捨的討論

所有章節到此完成。接下來是練習和 Final Project——把整套能力用在實戰：patch 一個 CVE fix 進 gcc recipe（練習）、把你自家的 custom extension patch 塞進 RISC-V Yocto image（Final）。

→ [練習：patch 一個 CVE fix 進 gcc recipe](./practice-patch-cve.md)
