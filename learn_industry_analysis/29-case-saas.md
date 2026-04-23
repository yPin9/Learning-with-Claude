# Ch 29 — 拆解二：SaaS（訂閱軟體）

> SaaS 是過去 20 年最成功的產業結構之一。它的經濟學跟半導體幾乎相反 — **低資本密度、訂閱收入、極高毛利**。這一章用相同的框架拆 SaaS，並處理一個當代大問題：**AI 會不會顛覆 SaaS？**

## 為什麼選 SaaS

- **典型的好產業結構**：幾乎每個 Ch 8 的「好產業判準」都打勾
- **跟半導體相反的結構**：示範同樣的框架如何處理不同的產業
- **投資人必懂**：Microsoft、Salesforce、Adobe、ServiceNow 等是大盤的核心持股
- **2024 的大問題**：AI 會怎麼改變 SaaS

## Part 1：產業界定

### SaaS 是什麼

**SaaS（Software as a Service）**：軟體不再是「買斷授權」（on-premise），而是**按月 / 按年訂閱**、**由廠商雲端托管**、**持續更新**。

### SaaS 的子分類

```
SaaS

├── 水平 SaaS (Horizontal SaaS)
│   └── 企業管理（Microsoft 365、Google Workspace）
│   └── CRM（Salesforce、HubSpot）
│   └── ERP（SAP、Oracle、Workday）
│   └── 協作（Slack、Asana、Monday）
│   └── 資料分析（Snowflake、Databricks）
│   └── DevOps（GitHub、GitLab、JFrog）
│
├── 垂直 SaaS (Vertical SaaS)
│   └── 醫療：Veeva、Doximity
│   └── 金融：Intuit、Paycom
│   └── 不動產：CoStar、AppFolio
│   └── 零售：Shopify
│   └── 餐飲：Toast
│   └── 建築：Procore
│   └── 法律：Clio、Onit
│
└── 基礎設施 SaaS (Infrastructure SaaS / PaaS)
    └── 資料庫：Snowflake、MongoDB、Databricks
    └── 監控：Datadog、Dynatrace
    └── 身份：Okta、CyberArk
    └── API / 整合：Twilio、Stripe（部分）
```

這幾類的競爭邏輯、護城河、估值都不同。**做 SaaS 投資要區分類別**。

### 地理邊界

SaaS **通常是全球產業**（軟體可全球賣），但：

- 語言本地化仍重要（日本、韓國、中國都有本地巨頭）
- 資料主權法規（GDPR、中國資料法）會分割市場
- 金融、醫療的 SaaS 受嚴格在地法規約束

## Part 2：SaaS 的經濟學

這是理解 SaaS 的核心。

### 為什麼 SaaS 的結構這麼好

**傳統軟體（On-premise）vs SaaS**：

| 維度 | 傳統軟體 | SaaS |
|---|---|---|
| 收入模式 | 一次性賣斷 + 維護費 | 每月 / 每年訂閱 |
| 收入穩定度 | 週期性（大合約年） | 高度穩定、可預測 |
| 邊際成本 | 中等 | **極低**（伺服器成本） |
| 毛利率 | 50–60% | **70–85%** |
| 升級 / 版本 | 客戶自行升級（麻煩） | 雲端自動更新 |
| 部署 | 客戶自建伺服器 | 訂閱即用 |
| 擴張 | 需要新合約 | 客戶自助擴張（seat 增加） |
| 資本需求 | 中（自建銷售 + 支援團隊） | 低（雲端基礎設施） |

### SaaS 單位經濟學

**一個訂閱客戶的典型經濟學**：

```
CAC（Customer Acquisition Cost）：$10,000
ACV（Annual Contract Value）：$5,000
Gross Margin：80%
Annual Gross Profit per customer：$4,000
Payback Period：$10,000 / $4,000 = 2.5 years
Customer Lifespan：10 years（假設 10% churn）
LTV = $4,000 × 10 = $40,000
LTV / CAC = 4 倍
```

**這個經濟模型一旦打穩**，每新增一個客戶的長期 ROI 是 4 倍 — 遠高於多數實體產業。

### 訂閱的複利

SaaS 的真正威力在**複利**：

- 客戶平均留存 10 年
- 客戶平均每年升級（seat 增加、plan 升級）10%
- Net Revenue Retention 110–130%

**數字上**：

- 第 1 年：$5,000 ACV
- 第 5 年：$5,000 × 1.1^4 = $7,320 ACV
- 第 10 年：$5,000 × 1.1^9 = $11,800 ACV

**每個客戶在 10 年內帶來的總營收是初始的 2.4 倍**。這是純粹的複利。

## Part 3：Porter 五力（以企業 CRM 為例）

### 1. 產業內競爭

- 玩家：Salesforce 主導（~20% CRM）、HubSpot（中小企業）、Microsoft Dynamics、Oracle、SAP
- 集中度：中度
- 競爭強度：中（Salesforce 有規模優勢，但其他玩家也有獨特定位）

### 2. 新進入者

- **低技術門檻**（理論上任何團隊都能做軟體）
- **但高分銷門檻**（企業客戶不會輕易嘗試新產品）
- **高轉換成本護城河**（既有客戶不換）
- 結論：**新進入者威脅中等**（AI 時代可能升高）

### 3. 替代品

- 過去：試算表 + 郵件系統替代 CRM（許多小公司實際在用）
- 未來：**AI 可能徹底改變 CRM**（見 Part 10）

### 4. 供應商議價

- SaaS 的供應商主要是雲端（AWS / Azure / GCP）
- 雲端三巨頭對 SaaS 有中度議價權
- 但 SaaS 公司也可以多雲，降低單一依賴

### 5. 買家議價

- **企業客戶**有一定議價權（特別是大客戶）
- 但高轉換成本削弱這個議價權
- 中小企業客戶幾乎沒有議價權

### 五力綜合

**SaaS 的五力整體偏弱** → 產業獲利能力強。

## Part 4：SaaS 的生命週期

**整體 SaaS**：成長後期 / 成熟前期

**子類別的分階段**：

- **水平 CRM、ERP**：成熟期（Salesforce、SAP 主導）
- **垂直 SaaS**：多數在成長期（Procore、Toast 等仍擴張）
- **基礎設施 SaaS**：成長中後期（Snowflake、Datadog 等）
- **AI-native SaaS**：**早期**（新興玩家出現）

## Part 5：SaaS 的 KPI（SaaS 是 KPI 最豐富的產業）

### 最核心的 KPI

**ARR（Annual Recurring Revenue）**：
- SaaS 的「營收」真正定義
- 比 GAAP 營收更純粹（剔除一次性服務費）

**NRR（Net Revenue Retention）**：
- = (既有客戶今年 ARR) / (去年 ARR)
- > 120% = 頂級
- 100–110% = 一般
- < 100% = 萎縮

**Gross Dollar Retention（GDR）**：
- = (既有客戶今年留存的 ARR) / (去年 ARR)
- 不包含擴張，只看留存
- > 90% = 好

**LTV / CAC**：
- > 3 = 健康

**CAC Payback Period**：
- < 12 個月 = 極好
- 12–18 個月 = 健康
- > 24 個月 = 有問題

**Rule of 40**：
- = ARR 成長率 + FCF 利潤率
- > 40% = 健康
- > 60% = 優秀

**Gross Margin**：
- > 70% = 健康 SaaS
- < 70% = 可能有 professional services 汙染

**Sales Efficiency（Magic Number）**：
- = 新增 ARR / S&M 支出
- > 1 = 效率高

## Part 6：護城河（SaaS 特別）

SaaS 的護城河組合：

### 1. 轉換成本（核心）

- 導入成本高（整合現有系統）
- 員工訓練成本
- 風險規避（怕換了出問題）
- 結果：**高 Gross Dollar Retention**

### 2. 網絡效應（某些 SaaS）

- Slack：同事都用才有用
- LinkedIn：人越多越值錢
- Salesforce 的 AppExchange：生態系
- Snowflake 的 Data Sharing：資料共享網絡

### 3. 資料積累（Data Moat）

- 客戶用越久，公司累積的資料越多
- 這些資料讓產品越來越好（推薦、benchmark、預測）
- 例：HubSpot 的行銷 benchmark 資料、Salesforce 的產業模組

### 4. 生態系（Ecosystem）

- 第三方整合（integrations）
- 合作夥伴網絡（consultants、implementation partners）
- 開發者社群
- 例：Salesforce + AppExchange、ServiceNow 的 IT 顧問網絡

### 5. 品牌 + 規模優勢

- 企業 CIO 買 Salesforce、Microsoft「保險」
- 大規模 → 更多資料 → 更好產品 → 更多客戶

## Part 7：SaaS 的估值

### 為什麼 SaaS PE 不適用

成長期 SaaS 公司大多虧損（燒錢擴張），PE 無意義。

**SaaS 估值常用**：

**EV/ARR**（企業價值 / 年經常性收入）：

- 成熟 SaaS：10–15x
- 高成長 SaaS：20–40x（2021 高峰時甚至 50x+）

**EV/Revenue**：類似 EV/ARR，用 GAAP 營收

**Rule of 40 x EV/ARR**：有些分析師用這個組合判斷

### 2021 vs 2024 的估值

**2021（零利率 + 熱潮）**：
- 中位 SaaS：EV/ARR 15x
- 頂級高成長：EV/ARR 40–60x

**2024（升息後 + 成長放緩）**：
- 中位 SaaS：EV/ARR 5–8x
- 頂級高成長：EV/ARR 15–25x

**2021–2022 的估值壓縮**是利率 + 增長放緩的雙殺。

## Part 8：贏家與輸家

### 結構性贏家

**水平巨頭**：
- **Microsoft**：Office 365 + Teams + Azure 的整合
- **Salesforce**：CRM 主導 + Marketing Cloud + Slack

**垂直 SaaS 贏家**：
- **Shopify**：電商平台
- **Toast**：餐飲
- **Veeva**：生技/製藥
- **Procore**：建築

**基礎設施 SaaS**：
- **ServiceNow**：企業 workflow 自動化
- **Snowflake**：資料倉儲
- **Datadog**：監控

### 結構性輸家 / 風險者

- **純 chatbot / AI 公司**（2023–2024 投資熱，但商業模式多半不清楚）
- **元宇宙相關 SaaS**（熱潮過了）
- **單一功能 SaaS**（容易被整合到平台中）
- **未建立 NRR > 110% 的 SaaS**（找不到複利機制）

## Part 9：Adobe 的案例 — SaaS 轉型的教科書

Adobe 是**最成功的 SaaS 轉型**案例之一，值得展開。

### 轉型前（2012 及之前）

- 賣 Photoshop、Illustrator、InDesign 等 licensed software
- 每 2–3 年出新版，客戶付升級費
- 營收有週期性（新版本年營收大增）
- Gross margin 約 90%

### 轉型（2013）

Adobe 宣告**全面轉向訂閱制**：
- 不再賣 perpetual license
- 改為 Creative Cloud 訂閱
- 月費 $20–50

### 短期反應

- 2013 股價短期重跌（因為一次性買斷的舊客戶抵制）
- 營收短期下降（訂閱收入慢慢累積）
- 但 ARR 快速增加

### 長期結果

- 2013 股價約 $40
- 2024 股價約 $500
- **12 倍漲幅**
- ARR 從 $0 到 $50B+
- Net Revenue Retention 長期 > 110%

**這個轉型的邏輯**：
- 收入穩定度大幅提升（訂閱 > 一次性）
- 總收入其實更高（年費累積 > 一次性賣斷）
- 盜版率下降（雲端服務，難盜）
- 資料積累（可以做 AI 工具）

**給投資人的啟示**：**SaaS 轉型成功的公司，長期估值會遠超轉型前**。Microsoft 也做過類似的事（Office 365 轉型）。

## Part 10：AI 會顛覆 SaaS 嗎？

這是 2024 年最大的問題。

### 顛覆的論點

**「Vibe Coding」觀點**（Marc Andreessen 等）：

- AI 讓軟體開發成本降 90%
- 客戶可以**自己用 AI 生成**他們需要的軟體，不用買 SaaS
- 傳統 SaaS 賣的是「預先打包好的工作流程」，AI 可以讓你**客製化**
- 結果：**SaaS 市場會萎縮**

### 反顛覆的論點

**「AI Accelerates SaaS」觀點**：

- AI 讓 SaaS 產品變更聰明（自動化、推薦、預測）
- 客戶愛死 AI 功能，付費意願上升（Microsoft Copilot 每月 $30）
- 建立 SaaS 需要的**流程邏輯、合規、整合**，AI 改變不了
- 結果：**SaaS 進入 AI 加成時代**

### 我的判斷（參考用）

**兩者都對，但在不同層次**：

**可能被顛覆的 SaaS**：
- 簡單、模板化的工具（簡單的網站建構、基礎 CRM、簡單文書）
- 低差異化的水平工具

**不太會被顛覆的 SaaS**：
- 垂直 SaaS（深度領域知識、合規、整合）
- 平台型 SaaS（Salesforce、ServiceNow 等，有生態系）
- 需要資料整合與組織 workflow 的 SaaS

**AI 加成最受益的 SaaS**：
- Microsoft（Copilot 全面整合）
- Salesforce（Einstein）
- ServiceNow
- 資料層 SaaS（Snowflake、Databricks 是 AI 時代的基礎設施）

**投資含義**：
- **避開**：弱差異化、容易 AI 化的 SaaS 新創
- **保留**：有護城河的平台 SaaS
- **加碼**：有 AI 優勢的垂直 SaaS

## Part 11：SaaS 最終 Thesis（示範）

```markdown
# SaaS 產業 Thesis（2024 Q4）

**時間尺度**：3–5 年

## 一句話結論
AI 時代不會消滅 SaaS，但會加速分化。平台型 SaaS（Microsoft、Salesforce、
ServiceNow）、垂直 SaaS 龍頭（Shopify、Veeva、Toast）、資料層 SaaS
（Snowflake、Databricks）將是結構性受益者；弱差異化的水平 SaaS 將面對
壓力。

## 關鍵推理

### 1. 平台型 SaaS 因 AI 整合而加強
Microsoft Copilot、Salesforce Einstein、ServiceNow Now Assist 都能提高
客戶的 ARPU（平均每用戶收入），且這些功能強化了現有的轉換成本。
**追蹤**：這些公司的 Net Revenue Retention 有沒有因 AI 而上升

### 2. 垂直 SaaS 有護城河
Shopify、Toast、Veeva 等垂直龍頭有深度產業知識與整合，AI 難以撼動。
**追蹤**：這些公司的 ARR 成長率

### 3. 資料層 SaaS 是 AI 基礎設施
Snowflake、Databricks、MongoDB 是 AI 訓練與推論需要的資料基礎設施。
**追蹤**：消耗 credit 的成長、Snowflake 的 AI-related workload

### 4. 弱差異化 SaaS 將萎縮
簡單的 landing page builder、基礎 CRM 等將被 AI 取代或整合進平台。
**追蹤**：這類公司的 churn rate 變化

## 核心持股

- **Microsoft**：AI 時代的最大受益者之一
- **Salesforce**：CRM 主導 + Einstein
- **ServiceNow**：企業 workflow + Now Assist
- **Snowflake**：資料基礎設施

## 觀察名單

- **Shopify**：電商 SaaS 龍頭，恢復成長
- **Veeva**：生技垂直王者
- **Datadog**：監控 + AI 加成
- **ServiceTitan**：家事服務垂直 SaaS（2024 IPO）

## What could make me wrong

1. **AI 真的把 SaaS 去中介化**（客戶自己 vibe code）
   - 追蹤：企業採購 SaaS 的預算趨勢
2. **平台巨頭（Microsoft）壟斷過多生態**
   - 追蹤：小型 SaaS 公司的成長率
3. **企業 IT 支出大幅縮減**
   - 追蹤：美國企業 IT Capex 年度報告

## KPI 儀表板

對每家核心持股：
- ARR 成長率
- Net Revenue Retention
- Rule of 40
- FCF 利潤率
- AI 營收佔比（特別關注）
```

## 自我檢核

- [ ] 我能分辨水平 SaaS、垂直 SaaS、基礎設施 SaaS 三種子類。
- [ ] 我能解釋為什麼 SaaS 的單位經濟學特別有複利效應。
- [ ] 我知道 SaaS 核心 KPI（ARR、NRR、LTV/CAC、Rule of 40）。
- [ ] 我知道 Adobe 從賣斷到訂閱的轉型是 SaaS 的典範。
- [ ] 我能針對「AI 會顛覆 SaaS 嗎」這個問題，列出兩面論點並形成自己判斷。
- [ ] 我能用本課框架寫出 SaaS 的 thesis。

下一章拆解第三個產業：**消費品 / 品牌**。這是最「古典」的產業分析素材 — 品牌護城河、通路、長期穩定。

→ [Ch 30 拆解三：消費品（品牌 + 通路）](./30-case-consumer.md)
