# Ch 24 — 法規版圖：EO 14028 / EU CRA / FDA

> **目標**：搞清楚哪些法規要求你交出 SBOM、要求的深度是什麼、何時生效、不交有什麼後果——以及那些「我以為還適用」的條文其實已經被廢止了。這章是治理決策的地基：你得先知道自己身處哪個法規管轄範圍，才能決定 SBOM 計畫要做到什麼程度。

## 為什麼需要這個？

SBOM 從 2020 年開始從「最佳實踐」變成「法規要求」，速度快到很多企業來不及反應。問題不是「要不要做 SBOM」，而是**你在哪個法規管轄下、被要求做到什麼層次、在什麼時間點之前**。搞錯這三件事，輕則交出一份不合規的 SBOM 被退件，重則 premarket submission 被 FDA refuse to accept、產品無法在 EU 掛 CE 標、聯邦採購資格受影響。

這章不是法律建議，是技術人員需要知道的最低限度法規事實。讀完之後，你應該能填出一張自己產品的合規矩陣，然後帶著它去找法務或合規部門討論細節。

## 先建立直覺

把這些法規想成四個問題：

1. **我的產品賣到哪裡、賣給誰？** — 決定哪些法規管得到你
2. **法規要我交出什麼？** — SBOM 的格式、深度、欄位
3. **交給誰看？** — 主管機關、客戶、還是公開
4. **什麼時候開始算？** — 生效日期，以及過渡期設計

法規的邏輯是「觸發點」：你的產品符合某個定義（聯邦採購標的、醫療器材、進入 EU 市場的帶數位元件產品），就落入對應的要求框架。不符合定義，就跟你沒關係——至少目前是。

一個重要的心態調整：**這些法規都是「最低標準」，而不是「最佳實踐」**。法規告訴你不做什麼會被罰，不告訉你怎麼做才是真的有用。達到最低標準跟真的做好 SBOM 治理之間，仍然有很大的差距。這章聚焦前者，Ch 25 開始才進入後者。

現在逐一拆開。

## US EO 14028：聯邦採購的 SBOM 起點

### 背景與觸發

2021 年 5 月 12 日，Biden 簽署行政命令 14028「Improving the Nation's Cybersecurity」，直接導火線是 SolarWinds 與 Colonial Pipeline 事件。EO 14028 本身是一份政策宣示，實質的技術內容由 NIST 與 NTIA 填充。

NTIA 在 2021 年 7 月公布《The Minimum Elements For a Software Bill of Materials》，定義了「一份 SBOM 至少要有什麼」。這份報告是目前全球 SBOM 標準討論的共同起點，很多後續法規（包含 FDA guidance、CRA 的技術解釋）都在它的基礎上疊加。

### NTIA 七個必要欄位

| 欄位 | 說明 |
|------|------|
| Supplier Name | 元件的供應商名稱 |
| Component Name | 元件名稱 |
| Version | 版本號 |
| Other Unique Identifiers | purl 或 CPE，機器可比對的唯一識別符 |
| Dependency Relationship | 描述元件之間的依賴關係 |
| Author of SBOM Data | 產出這份 SBOM 的是誰 |
| Timestamp | SBOM 的產出時間 |

七個欄位乍看很少，但「Other Unique Identifiers」那欄是關鍵——沒有 purl 或 CPE，這份 SBOM 就沒辦法被自動化工具比對 CVE，只是個人工可讀的清單，幾乎沒有自動化價值。

另一個常被誤解的欄位是「Dependency Relationship」。NTIA 的要求不是要你列一個 flat list，而是要描述元件之間的**關係**：這個 package 是被哪個 package 引進來的、它是直接依賴還是傳遞依賴。沒有這個關係，你只知道產品裡有什麼，不知道為什麼有它、誰引進它的——這對 incident response（「這個漏洞的元件是哪個業務功能用到的？」）影響很大。

### 三個實作慣例

除了欄位，NTIA 還定義三個**實作慣例（practices）**，這是容易被忽略的部分：

- **Frequency**：每次 release 都要重新產出 SBOM，不是產一次放著就算了
- **Depth**：必須涵蓋直接相依與傳遞相依（transitive dependencies），不能只列直接依賴
- **Known Unknowns**：如果有無法列舉的元件（例如靜態連結但沒有清單的第三方函式庫），必須在 SBOM 裡明確標記這件事，而不是假裝它不存在

最後一條實務上最痛：很多舊系統裡有 vendored 的 C 函式庫、出處不明的 binary blob，你知道有這些東西，但你列不出來。NTIA 的立場是：列不出來可以，但你必須說「這裡有我列不出的東西」，不能沉默。

Known Unknowns 的做法在 SPDX 裡有對應語法（`NOASSERTION` 欄位值），在 CycloneDX 裡可以用 `unknowns` 節點。工具（syft / trivy）預設不產生這些標記，需要人工或流程介入。

### 接受格式

EO 14028 / NTIA 接受三種機器可讀格式：SPDX、CycloneDX、SWID。實務上 SWID 幾乎沒人用在 SBOM 交付（它的設計重點是軟體識別，不是元件清單），主流就是 SPDX 和 CycloneDX。

## OMB Memo M-22-18 / M-23-16，以及被廢止的 attestation 強制要求

EO 14028 只對聯邦機構本身有約束力，OMB（Office of Management and Budget）的 Memo 才是讓「賣軟體給聯邦政府的廠商」受到影響的機制。

- **M-22-18（2022 年 9 月）**：要求聯邦機構向軟體廠商取得安全開發實踐的自我證明（attestation），使用 CISA 發布的 common form；同時允許機構酌情要求廠商提交 SBOM
- **M-23-16**：進一步明確 attestation 義務適用到 end product 的生產商，不只是直接契約對象

**重要更新**：2026 年 1 月 23 日，OMB 發布 **M-26-05**，廢止了 M-22-18 與 M-23-16 的「common form attestation 強制要求」，改為風險導向的彈性做法。聯邦機構不再統一被要求索取標準格式的 attestation form，而是依自身評估決定要求哪些憑據。**機構仍然可以要求廠商提交 SBOM 或 attestation，但不再是一刀切的強制義務。**

這個變化很多人不知道。如果你在 2026 年初之後還在告訴客戶「聯邦採購的 M-22-18 attestation 是強制的」，你的資訊已經過期了。

M-26-05 廢止的是「強制使用 common form」這件事，不是廢止 attestation 這個概念。各機構仍可自行制定 attestation 要求，也可以要求廠商交 SBOM 作為審查材料。對廠商的實務意義是：不能再說「我填了那張表就好了」，每個機構客戶的要求可能不一樣，要個別確認。

## EU CRA（Cyber Resilience Act）：CE 標誌的新前提

### 適用範圍

EU Cyber Resilience Act 於 2024 年 10 月 10 日由 EU 理事會通過，2024 年 11 月 20 日刊登 EU 官方公報，2024 年 12 月 10 日正式生效。

**適用對象**：在 EU 市場銷售「帶數位元件產品（products with digital elements）」的製造商。定義相當廣：幾乎所有含軟體、韌體、或能連接網路的硬體都算。純 SaaS 服務在 CRA 最終版本中排除在外，但很多混合產品（賣硬體 + 搭配 app / cloud service）仍在範圍內。

### SBOM 要求（Art. 13 / Annex VII）

CRA 的 SBOM 義務藏在 Art. 13 和 Annex VII（技術文件要求）裡：

- **格式**：常用的機器可讀格式——CRA 本文沒有點名，解釋性文件指向 SPDX、CycloneDX、SWID
- **深度**：至少涵蓋「top-level dependencies」——**不是強制全深度傳遞相依**（這是 CRA 跟 NTIA 定義最大的差異之一）
- **保存**：產品上市後至少 **10 年**（或產品的預期生命週期，以較長者為準）
- **維護**：每次更新後同步更新 SBOM
- **提供對象**：主管機關（market surveillance authority）要求時交出；**不強制公開**，不需要主動對使用者揭露

### 生效時程

CRA 採用分階段生效：

| 要求 | 生效日期 |
|------|---------|
| 漏洞通報義務（重大漏洞 24 小時內通報 ENISA） | 2026 年 9 月 11 日 |
| SBOM 等全部 Annex I 資安要求（含 CE 標誌） | 2027 年 12 月 11 日 |

2027 年 12 月 11 日之後，在 EU 市場銷售的帶數位元件產品，如果拿不出符合要求的技術文件（包含 SBOM），就不能掛 CE 標誌，實質上等於不能銷售。

**CRA 的「top-level only」到底是什麼意思？** 它指的是你的產品直接 import / link 的那一層元件。舉個例子：如果你的 Node.js 應用程式的 `package.json` 列了 `express`，那 `express` 是 top-level dependency。`express` 自己依賴的 `qs`、`path-to-regexp` 等，就是傳遞相依，CRA 不強制你列出。但你的 `package.json` 直接列的每一個 package，不管有沒有被主動用到，都是 top-level，都要列。

這個「只要 top-level」的標準，對大多數現代 web / cloud 產品影響不大（你的直接依賴可能就幾十個），但對嵌入式產品（大量靜態連結的傳遞函式庫）反而更難——因為很多嵌入式傳遞相依根本沒有清晰的「直接依賴」層次，往往是整包 vendored 進來的。

## FDA：醫療器材的 premarket SBOM 強制要求

### 法源

Section 524B of the FD&C Act（Food, Drug, and Cosmetic Act）。這條由 Consolidated Appropriations Act 2023 加入，要求「cyber devices」的製造商在上市前申請（premarket submission）時提交 SBOM。

「Cyber device」的定義：包含軟體、且能連接網路（或與其他設備 / 系統連接）的醫療器材。覆蓋範圍很廣，輸液泵、病患監視器、手術機器人、醫療影像設備，全部在內。

### 時程與 RTA 政策

- **2023 年 3 月 29 日起**：Section 524B 開始適用，所有 premarket submission（510(k)、PMA、PDP、De Novo、HDE）都需要包含 SBOM
- **2023 年 10 月 1 日前**：FDA 通常不會單純因 Section 524B 不合規就拒絕受理（Refuse to Accept，RTA）——這是過渡期，FDA 會要求廠商補件，但不直接打回票
- **2023 年 10 月 1 日後**：FDA 可以正式 RTA 不符 Section 524B 的 premarket submission，等同直接拒絕受理，廠商需要重新準備再送件

FDA 在 2023 年 3 月 30 日同步發布了 RTA 的指引文件，明確說明哪些情況會觸發 RTA。

### FDA SBOM 的格式與深度要求

FDA guidance 沒有強制特定格式，接受 SPDX、CycloneDX、SWID。深度上，FDA 採用 NTIA minimum elements，要求傳遞相依也要涵蓋，不像 CRA 只要求 top-level。

醫療器材的 SBOM 有一個特殊挑戰：這個行業大量使用**商業現成軟體（COTS）和開源元件（OTS/OSS）**，但很多這類軟體的供應商本身不提供 SBOM，或提供的 SBOM 品質很差。FDA guidance 認識到這個問題，允許廠商在某些情況下標記「此元件的 SBOM 由供應商提供，廠商未獨立驗證」——但這不能成為常態，主力元件仍需要你自己能驗證的 SBOM。

此外，FDA 的 SBOM 要求不是靜態交付一次就結束的。對於「持續上市」（on market）的器材，如果軟體有重大更新而觸發 PMA supplement 或其他送件，對應的 SBOM 也要一起更新提交。

## 汽車業：UNECE R155 與 ISO/SAE 21434

### UNECE WP.29 UN R155

汽車是另一條法規主線。UNECE（聯合國歐洲經濟委員會）的 WP.29 工作小組於 2021 年通過 UN R155，要求 OEM 建立並認證 Cybersecurity Management System（CSMS）。

**關鍵時點**：2024 年 7 月起，在 64 個 UNECE 簽約方（包含整個 EU、日本、韓國、澳洲）銷售的每一輛新車，都必須通過 R155 的型式認可（type approval）。型式認可流程要求 OEM 提交完整的車輛軟體清單，包括每個 ECU 的 SBOM——這份需求往上游壓到每一個 Tier 1 / Tier 2 零件供應商。

### ISO/SAE 21434:2021

ISO/SAE 21434 是汽車網路安全工程標準，跟 R155 互補：R155 是法規要求什麼結果，21434 是工程方法論告訴你怎麼做到。21434 明確要求管理 SBOM 與供應鏈漏洞，包含定期更新和漏洞應變流程。

汽車業的 SBOM 要求跟其他行業有一個顯著差異：**車輛的生命週期很長**（10-15 年），這份 SBOM 要維護到報廢，遠比 EU CRA 的 10 年保存要求更嚴苛。

更複雜的是供應鏈層次問題：一輛車有數十個 ECU，每個 ECU 可能來自不同的 Tier 1 供應商，每個 Tier 1 又從 Tier 2 採購元件和軟體。OEM 要在型式認可時提交整車的軟體清單，就必須從每一層供應商那裡拿到 SBOM——這個「SBOM 向上彙集」的需求，讓汽車行業比任何其他行業更早碰到「如何在供應鏈之間傳遞和合併 SBOM」這個問題。這正是 Ch 26 要談的 SBOM 分享機制的實際驅動力之一。

## CISA 2025 草案：欄位加碼

2025 年 8 月 22 日，CISA 發布 2025 SBOM Minimum Elements 草案，公眾評論期至 2025 年 10 月 3 日。截至本課查證時仍為草案，尚未定案。

草案在 2021 NTIA 七欄位基礎上新增了：

- **Component hash**：元件的密碼學雜湊值，讓消費者能驗證元件完整性
- **License**：元件的授權條款（SPDX license expression）
- **Generation context**：SBOM 的產生方式（分析型還是 build-time 型、用了哪個工具）

如果這份草案最終定案，原本「只要有 purl 就夠」的 SBOM 就需要補上 hash 和 license 欄位。現在產出的 SBOM 如果缺這些欄位，未來重新合規的成本不低。

**現在能做什麼？** syft 和 trivy 預設已經會產出 component hash 和 license 欄位（SPDX 的 `packageChecksum` 和 `licenseConcluded`、CycloneDX 的 `hashes` 和 `licenses`）。如果你的 SBOM pipeline 已經用這兩個工具，很可能草案的新欄位你已經有了，只需要核對一下輸出的 JSON 確認欄位不是空值。Generation context 比較麻煩，它要求記錄產出工具的名稱和版本——CycloneDX 的 `metadata.tools` 欄位能放這個，但不是所有 pipeline 都會填。

## 對比與取捨

| 法規 | 管轄對象 | 格式 | 深度要求 | 強制公開？ | 關鍵生效日 |
|------|---------|------|---------|-----------|-----------|
| EO 14028 / NTIA | 賣軟體給美國聯邦政府的廠商 | SPDX / CycloneDX / SWID | 直接 + 傳遞相依 | 否，交給採購機構 | 2021 年起已生效 |
| OMB（現 M-26-05） | 聯邦軟體供應商 | 未強制格式 | 機構酌情要求 | 否 | 已改為風險導向 |
| EU CRA | 在 EU 銷售帶數位元件產品的製造商 | SPDX / CycloneDX / SWID | 至少 top-level | 否，監管機關要求時交出 | 2027-12-11（SBOM） |
| FDA 524B | 美國市場 cyber device 製造商 | SPDX / CycloneDX / SWID | 直接 + 傳遞相依 | 否，premarket submission | 2023-10-01（RTA） |
| UNECE R155 | 在簽約方市場銷售新車的 OEM（壓到供應商） | 無強制格式 | 整車軟體清單 | 否，型式認可提交 | 2024-07（新車） |
| CISA 2025 草案 | 同 EO 14028 範圍（草案） | SPDX / CycloneDX | 直接 + 傳遞 + hash | 否 | 草案，未定案 |

幾個橫向觀察：

1. **沒有任何主流法規要求你公開 SBOM**。對外公開是自願選擇，法規要求的對象是監管機關或採購方。
2. **深度要求最大的差異在 CRA**：CRA 只要 top-level，其他法規（NTIA / FDA）要全深度傳遞相依。如果你要同時合規，做到全深度就能全部覆蓋，但成本更高。
3. **汽車業的維護義務最重**：生命週期長、更新頻繁、供應鏈層數多，R155 / 21434 的 SBOM 實踐比其他行業難度高一個量級。
4. **格式上的最大公因數**：SPDX 和 CycloneDX 是唯二真正在多個法規框架下被廣泛接受的格式。如果你的 pipeline 同時產出這兩種格式，可以覆蓋幾乎所有場景。SWID 理論上也被接受，但實務上幾乎沒有工具和消費端的生態，選它等於自找麻煩。

## 踩雷集錦

1. **「CRA 要全深度傳遞相依」** — 錯。CRA Art. 13 / Annex VII 的要求是「top-level dependencies」，也就是你的產品直接依賴的元件，不強制展開所有傳遞相依。很多從 FDA / NTIA 習慣過來的工程師會過度估計 CRA 的深度要求；也有人反過來，把「只做 top-level」的 CRA 最低標準套到 FDA 送件，結果被 FDA 退件，因為 FDA 是要全深度的。法規不同，深度要求不同，不能混用。

2. **「M-22-18 的 attestation form 還是強制的」** — 已過期。2026 年 1 月 23 日的 M-26-05 廢止了強制使用 common form 的要求，改為機構依風險自行決定。如果你的合規文件或 FAQ 還寫著「要填 CISA common attestation form」而沒有提到 M-26-05，就是用過期資訊在做決策。注意：機構**仍然可以**要求你交 SBOM 或其他憑據，只是不再是標準化強制流程。

3. **「FDA 是 2023 年 3 月 29 日之後就會直接 RTA」** — 時間點搞錯。2023 年 3 月 29 日是 Section 524B 開始適用的日期，但 FDA 到 **2023 年 10 月 1 日之前**都不會純粹因 Section 524B 不合規就 RTA。2023 年 3 月到 9 月之間，FDA 會要求補件，而不是直接拒絕受理。「開始適用」跟「開始 RTA」是兩個不同的時間點，差了半年，這半年是廠商的補救視窗，不少人在 2023 年中搞錯以為已經沒救了。

4. **「SBOM 產一次就好，之後不用動」** — 所有法規都要求 SBOM 跟著產品更新而更新。NTIA 的 Frequency 慣例寫得很清楚：每次 release 都要重新產出。FDA 對於更新版本的送件也要求附上對應版本的 SBOM。CRA 明確要求每次更新後同步更新並保存 10 年。把 SBOM 當成「一次性交付物」是最常見的誤解之一，這種誤解在進入持續監控體制之後會被系統性解決，但在那之前很容易踩。

5. **「EO 14028 只管美國境內的廠商」** — 不對。EO 14028 的觸發條件是「是否賣軟體給美國聯邦機構」，跟公司所在地無關。台灣的 SaaS 公司如果有聯邦合約，同樣受 NTIA minimum elements 要求。很多亞太公司以為 EO 14028 是美國境內事務，等到聯邦客戶開始問才措手不及。

## 進階：再往深一層

### CRA 的罰款機制

CRA 不只是市場准入門檻，它有罰款條款：最高 15,000,000 歐元或全球年營業額 2.5%，以較高者為準（第 4 類違規）。這個數字跟 GDPR 的結構類似，意思是大公司不能用「繳小罰款」了事。台灣廠商賣到 EU 的產品如果不合規，被 market surveillance authority 盯上的後果很重。

### Open Source 的 CRA 邊界

CRA 對開源軟體的適用有明確的例外條款：非商業用途的開源軟體（stewardship without commercial purpose）不在 CRA 義務範圍內。但如果一個開源元件被商業產品整合後賣到 EU 市場，**製造商**（把開源元件放進產品的那個人）要負起 CRA 義務，開源上游不負責。這條界線在實踐中很多人搞不清楚，特別是 SaaS 商業化開源產品的場景。

### SBOM 與 VEX 的配套

多個法規（FDA、EO 14028 生態）已經開始把 VEX（Vulnerability Exploitability eXchange）跟 SBOM 配套討論。VEX 讓你能標記「這個 CVE 在我的產品裡不可利用」，補足純 SBOM 只列元件、無法描述漏洞狀態的不足。FDA 2023 年的 cybersecurity premarket guidance 明確提到 VEX 作為補充交付物。Ch 16 已經深入過 VEX，這裡只是點出法規接軌的面向。

### 多法規同時適用的場景

一個醫療器材 OEM 賣到歐盟的產品可以同時被：CRA（帶數位元件產品）、MDR（醫療器材法規的 CE 認證）、可能的 FDA 510(k)（若打算在美國銷售），三套法規同時要求 SBOM，但格式 / 深度 / 保存期限要求不完全一樣。實務上要取三套的聯集，然後找一個格式能同時滿足三邊——CycloneDX 1.5 以上的 SBOM 通常能覆蓋所有場景，但要確認你的工具鏈確實輸出了所有被要求的欄位。

**法規速度 vs. 技術速度**：這個領域的法規在 2021-2027 年之間集中爆發，很多細節仍在演進中。本章的事實截止點是 2026 年 8 月，但 EU CRA 的配套授權法規（delegated acts）、CISA 草案的定案版本、以及 FDA 的後續更新 guidance，都可能在你讀到這章之後有新的發展。養成直接查官方公報和 CISA SBOM 資源頁的習慣，不要只依賴二手摘要（包含這章）。

## 法規之外：還有哪些「軟性強制」？

正式法規是顯性的壓力，但市場上還有幾種「你不做就拿不到合約」的隱性壓力，技術人員也要知道：

**企業採購條款**：大型企業客戶（金融、電信、國防供應鏈）開始在採購合約裡加入 SBOM 交付義務。這不是法規，是合約條款。拒絕的後果不是被罰款，是丟單。2023 年之後，Fortune 500 的採購部門要求軟體廠商提交 SBOM 的比例顯著上升，很多台灣 B2B 軟體廠商是透過客戶需求才第一次接觸 SBOM 要求。

**保險要求**：部分網路資安保險公司開始把「是否有 SBOM 流程」列入承保評估。目前還不是業界標準，但趨勢明確。沒有 SBOM 的公司可能面臨保費加成或特定漏洞相關損失的理賠排除。

**開源生態的 attestation 要求**：OpenSSF（開源安全基金會）的 Scorecard 和 SLSA framework 都把 SBOM 生成列為評分維度之一。主流 package registry（npm、PyPI、Maven Central）在 2025-2026 年間開始研議是否要求套件發布者提交 SBOM 或 provenance。如果你的產品是一個開源函式庫，這條線遲早會碰到你。

這些「軟性強制」的共同邏輯是：**市場正在把 SBOM 從差異化變成基本要求**，就像 HTTPS、SOC 2、ISO 27001 曾經的歷程一樣。現在還有「我們不做是因為沒法規要求」這個擋箭牌，2028 年之後這個擋箭牌會越來越薄。

## 動手練習

這章是治理類，沒有 CLI 可以跑，練習是填表和盤點。

### 練習一：產品法規歸屬分析

針對你現在負責的一個或多個產品，回答以下問題，每個問題對應一個法規觸發點：

| 問題 | 是 / 否 | 觸發法規 |
|------|---------|---------|
| 這個產品有沒有賣給美國聯邦政府機構？ | | EO 14028 / NTIA |
| 有沒有賣軟體給聯邦機構的採購合約（不論公司在哪裡）？ | | OMB M-26-05 框架 |
| 這個產品會在 EU 市場銷售，且包含軟體或韌體？ | | EU CRA |
| 這是一個醫療器材，且含軟體且能連接其他設備或網路？ | | FDA Section 524B |
| 這是一個車輛或車輛 ECU，賣到 EU / 日本 / 韓國 / 澳洲？ | | UNECE R155 / ISO/SAE 21434 |

每個「是」就是一條合規義務。如果有多個「是」，你需要取聯集：以最嚴格的那個（通常是 FDA 或 NTIA 的全深度傳遞相依）作為技術底線。

### 練習二：合規矩陣

填寫以下表格（用你的真實產品或假設一個場景）：

| 項目 | 你的現狀 | 差距 |
|------|---------|------|
| 現有 SBOM 格式（SPDX / CycloneDX / 無） | | |
| 現有 SBOM 深度（top-level / 全傳遞 / 不知道） | | |
| NTIA 七個必要欄位是否全部存在？ | | |
| 每次 release 是否自動重新產 SBOM？ | | |
| SBOM 保存年限設計（無 / 3 年 / 10 年）？ | | |
| 如果是醫療器材，2023-10-01 之後的送件是否已附 SBOM？ | | |
| 如果進 EU 市場，2027-12-11 之前的準備計畫？ | | |

差距欄填完，就是 Ch 25 的輸入——企業 SBOM 計畫要解決的是這些缺口，不是空中樓閣。

### 練習三：核對你現有 SBOM 的欄位完整性

如果你手上已經有一份 syft 或 trivy 產出的 CycloneDX JSON，用 `jq` 快速核對幾個法規關鍵欄位：

```bash
# 確認有沒有 purl（NTIA 的 Other Unique Identifiers）
jq '[.components[].purl | select(. != null)] | length' your-sbom.cdx.json

# 確認有沒有 hash（CISA 草案新增欄位）
jq '[.components[] | select(.hashes != null and (.hashes | length) > 0)] | length' your-sbom.cdx.json

# 確認有沒有 license（CISA 草案新增欄位）
jq '[.components[] | select(.licenses != null and (.licenses | length) > 0)] | length' your-sbom.cdx.json

# 確認 generation context（CISA 草案 / 一般品質要求）
jq '.metadata.tools' your-sbom.cdx.json
```

把這四個數字跟 `.components | length`（總元件數）比，就能知道覆蓋率。purl 覆蓋率低於 80% 的 SBOM，對任何法規框架都是不合格的。

## 本章重點整理

- **EO 14028 / NTIA**：七欄位 + 三實作慣例，賣軟體給聯邦政府就適用（不論公司在哪），全深度傳遞相依，接受 SPDX / CycloneDX / SWID
- **OMB**：M-22-18 的 common form attestation 強制要求已被 M-26-05（2026-01-23）廢止，現為風險導向；機構仍可要求 SBOM，廠商需個別確認每個機構客戶的要求
- **EU CRA**：2027-12-11 SBOM 要求生效，at least top-level（非強制全傳遞），保存 10 年，不強制公開，違規最高罰 2.5% 全球營收或 15M 歐元
- **FDA Section 524B**：cyber device premarket submission 強制附 SBOM，2023-10-01 起可 RTA；全深度傳遞相依，允許標記 COTS 元件的 SBOM 來源限制
- **UNECE R155**：2024-07 起 64 個簽約方新車型式認可強制，整車供應鏈 SBOM 向上彙集，生命週期維護（10-15 年）要求最嚴苛
- **CISA 2025 草案**：在 NTIA 七欄位上加 hash / license / generation context，尚未定案，現代工具（syft / trivy）已能產出大部分新欄位
- **沒有任何主流法規強制公開 SBOM**，公開是自願行為
- **深度取聯集**：同時要合規多個法規框架時，以最嚴格的深度要求（FDA / NTIA 的全傳遞）為底線；格式首選 CycloneDX 1.5+

## 自我檢核

- [ ] 我能說出 NTIA 七個必要欄位，而不只是背「有七個」
- [ ] 我知道 CRA 的 SBOM 深度要求跟 FDA 的不一樣，能說出具體差異
- [ ] 我知道 M-26-05 廢止了什麼，廢止之後的框架是什麼
- [ ] 我知道 FDA RTA 政策有兩個不同的日期（3/29 vs 10/1），能說出各自的意義
- [ ] 我能填出自己產品的法規歸屬分析表

## 延伸閱讀

### 一手法規文件

- **[EO 14028 聯邦公報原文](https://www.federalregister.gov/documents/2021/05/17/2021-10460/improving-the-nations-cybersecurity)**
  聯邦公報完整文本。重點看 Section 4（Software Security）和 Section 10（Definitions），這兩節是 SBOM 義務的法源。不長，值得親讀一遍，很多「我聽說 EO 要求…」的誤解會在這裡被澄清。

- **[NTIA Minimum Elements for a Software Bill of Materials（2021-07）](https://www.ntia.gov/report/2021/minimum-elements-software-bill-of-materials-sbom)**
  七欄位 + 三實作慣例的原始報告，只有 14 頁，值得全讀。後面所有討論都在引這份文件，你至少要讀過一次原始版本才有資格跟人討論 SBOM minimum elements。

- **[EU CRA 官方公報（2024-11-20）](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ:L_202402847)**
  完整法規文本。SBOM 義務看 Art. 13 和 Annex VII。CRA 本文很長，不需要全讀，把 Art. 13 跟 Annex I / VII 看完就有足夠的工程輸入。Recitals（前文）也值得掃一眼，它解釋了立法意圖，對邊界案例的理解很有幫助。

- **[FDA：Cybersecurity in Medical Devices Guidance（2023-03-30）](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/cybersecurity-medical-devices-quality-system-considerations-and-content-premarket-submissions)**
  FDA 的 premarket submission cybersecurity guidance，包含 SBOM 和 VEX 的要求說明。和 Section 524B 法條配合讀，法條說「要什麼」，guidance 說「怎麼做」。Section 5 是 SBOM 的主要段落。

### 輔助閱讀

- **[CISA SBOM 資源入口](https://www.cisa.gov/sbom)**
  整合了所有 CISA 發布的 SBOM 相關報告、工具指引、研討會紀錄。CISA 2025 草案和最新的 VEX / SBOM sharing 文件在這裡找。

- **[UNECE WP.29 UN Regulations 索引](https://unece.org/transport/vehicle-regulations-wp29/regulatory-instruments/un-regulations-annexed-1958-agreement)**
  WP.29 所有 UN Regulations 的官方索引，找「No. 155」。相對艱澀，讀 Executive Summary 和 ANNEX 5（CSMS 要求）即可，不需要全讀。

- **[ISO/SAE 21434:2021 說明頁](https://www.iso.org/standard/70918.html)**
  付費標準，但 SAE 有部分公開的技術說明文件。重點是理解它跟 R155 的互補關係：R155 是法規要求，21434 是工程方法論。不買標準也能從公開的 SAE 文章理解核心概念。

法規層弄清楚了，接下來要問的是：一個企業怎麼把這些要求變成可執行的計畫、指定角色、定義流程、整合進開發週期？

→ [Ch 25 企業導入 SBOM 計畫](./25-enterprise-sbom-program.md)
