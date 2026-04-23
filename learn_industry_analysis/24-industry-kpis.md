# Ch 24 — 產業專屬 KPI：每個產業的語言

> 每個產業都有自己的「俚語」—— 一組只屬於這個產業的關鍵指標（KPI）。不懂這些 KPI，你聽公司法說會就像聽外星語。這一章教你辨認主要產業的核心 KPI，以及為什麼這些 KPI 比「營收 / 淨利」更能反映生意的真實狀況。

## 為什麼需要產業專屬 KPI

**損益表的營收與淨利**是所有公司都用的指標。但它們是**落後指標** — 反映的是過去發生的事。

**產業專屬 KPI** 是**領先指標** — 讓你**先看到**未來的營收與淨利會是什麼樣。

**例**：

- 看 Tesla 的營收，你看到的是前一季的銷售。
- 看 Tesla 的**訂單積壓（backlog）**，你看到的是**未來幾季的潛在銷售**。

- 看 Costco 的總營收，你看到的是當季整體表現。
- 看 Costco 的**會員續費率（renewal rate）**，你看到的是**未來營收的穩定度**。

**大多數散戶只看 GAAP 數字，專業投資者最重視產業 KPI** — 這就是認知差距的來源。

## 產業 KPI 的類型

產業 KPI 大致分幾類：

1. **營運指標**：反映生意的實質活動（銷量、使用量）
2. **單位經濟學**：每單位客戶、商品、店面的表現
3. **客戶行為**：留存、擴張、流失
4. **資產效率**：資產怎麼產生營收
5. **循環指標**：景氣週期的領先指標

以下按產業一一展開。

## SaaS / 訂閱軟體

**SaaS 是 KPI 語言最豐富的產業**，幾乎每個 metric 都有專屬縮寫。

### 核心 KPI

**ARR（Annual Recurring Revenue）**：年度經常性收入
- = 月訂閱金額 × 12
- 最核心的指標，比營收更乾淨（排除一次性費用）

**MRR（Monthly Recurring Revenue）**：月度經常性收入
- = ARR / 12

**NRR / NDR（Net Revenue Retention / Net Dollar Retention）**：淨收入留存率
- 公式：(既有客戶去年的 ARR → 今年的 ARR) / 去年 ARR
- = 留存 + 擴張 - 流失 - 降級
- **>100% 最重要**：代表既有客戶不但沒流失，還買更多

**NRR 的意義**：

- **NRR 90%**：流失率高，需要新客戶才能維持
- **NRR 100%**：既有客戶不流失不擴張
- **NRR 110–120%**：健康擴張，客戶越用越多
- **NRR 130%+**：頂級 SaaS（Snowflake、MongoDB 都曾達到）

**CAC（Customer Acquisition Cost）**：獲客成本
- 平均取得一個客戶要花多少錢

**LTV（Lifetime Value）**：客戶終身價值
- 一個客戶從進來到流失，給公司帶來多少毛利

**LTV/CAC 比**：
- < 3：效率太低
- 3–5：健康
- > 5：非常好

**Payback Period**：回收期
- 花了 $X 獲客，幾個月回本？
- 好的 SaaS：12–18 個月

**Churn Rate**：流失率
- 月流失率 1% 已經很健康

**Rule of 40**：
- 營收成長率 + FCF 利潤率 > 40%
- 衡量 SaaS 公司「成長 + 獲利」的綜合效率

### 實例：Salesforce FY2024

- ARR: 約 $36B
- NRR: 約 107%（健康但不頂級）
- LTV/CAC: 約 4.5
- Rule of 40: 營收成長 11% + FCF 利潤率 33% = 44（良好）

這些 metric 告訴你 Salesforce 是**成熟穩健**的 SaaS，不是高成長但健康。

## 半導體

半導體有**製造端**與**設計端**兩組不同的 KPI。

### 製造端（晶圓代工 / IDM）

**Wafer starts**：晶圓投片量
- 月或季度 wafer starts

**Utilization rate**：產能利用率
- 實際產量 / 銘牌產能
- > 90%：供需緊，價格可能上升
- < 80%：產能過剩，價格可能下降

**ASP（Average Selling Price）**：平均售價
- 每片晶圓的平均售價
- ASP 上升：產品 mix 往高階走 or 漲價
- ASP 下降：降價 or 產品 mix 降階

**Blended Gross Margin**：混合毛利率
- 先進製程 vs 成熟製程加權平均

**Capex Intensity**：資本支出強度
- Capex / 營收
- 台積電過去 5 年約 40%（極高）

**Yield**：良率
- 良品率（好晶片 / 總晶片）
- 技術領先 = 高良率 = 低單位成本

**Node Mix**：製程節點組合
- 5nm、7nm、成熟製程各佔多少

### 設計端（Fabless）

**Design win**：設計導入
- 被某個客戶選中設計到他們的產品
- 是**未來 1–3 年的營收先行指標**

**Backlog**：訂單積壓
- 已接但未交付的訂單

**Book-to-bill ratio**：接單出貨比
- 新訂單 / 出貨
- > 1：訂單多於出貨，業務在擴張
- < 1：訂單少於出貨，業務在收縮

**Inventory days**：庫存天數
- 太高 → 銷售放緩
- 太低 → 有可能缺貨

### 半導體整體（供需）

**Book-to-bill**（SEMI 發布）
**Billings**（全球半導體設備出貨量，SEMI 發布）
**DRAM / NAND spot price**（TrendForce 等追蹤）

## 零售 / 消費品

### 零售核心 KPI

**Same-store sales（SSS）/ Comparable sales**：同店銷售
- 只計算**已經開超過一年的店**的銷售
- 排除了「開新店製造成長」的假象
- 正數：健康；負數：結構有問題

**Traffic**：客流量
- 進店的人數 / 交易數

**Ticket Size / Average Basket**：客單價
- 每單交易金額

**SSS = Traffic × Ticket Size**
- 拆這兩個變數，看成長來源

### 品牌消費品 KPI

**Volume Growth**：銷量成長
- 賣出的產品數量

**Price Mix**：價格效應
- 漲價或 product mix 變化對營收的貢獻

**Volume + Price = 營收成長**

**理想組合**：
- **Volume 正 + Price 正 = 健康成長**（需求強 + 定價力）
- **Volume 負 + Price 正 = 依賴漲價**（需求弱但能漲價）
- **Volume 正 + Price 負 = 靠降價搶量**（不健康）
- **Volume 負 + Price 負 = 雙輸**

### Costco 的特殊 KPI

- **Membership renewal rate**：會員續費率（美國 > 90%，全球 > 85%）
- **Membership fee revenue**：會員費收入
- **Executive Member % of total**：高級會員佔比
- **Revenue per member**：每會員平均消費

## 銀行 / 金融

### 傳統銀行

**Net Interest Margin（NIM）**：淨利差
- (利息收入 - 利息支出) / 生息資產
- 衡量**核心獲利能力**

**Loan-to-Deposit Ratio**：貸存比
- 貸款 / 存款
- 70–90%：健康
- 過高：資金緊
- 過低：放款效率不佳

**Efficiency Ratio**：效率比
- 營運費用 / 淨收入
- 越低越好，美國大型銀行 50–60%

**NPL Ratio / Charge-off Rate**：不良貸款率 / 打銷率
- 壞帳佔比
- 景氣順風：低
- 景氣逆風：上升

**CET1 Ratio**：第一類資本比率
- 衡量銀行的資本充足度
- 巴塞爾 III 最低 4.5%，實際一般 10%+

**ROE**：股東權益報酬率
- 銀行最核心的獲利指標
- 優秀銀行：15%+

### 保險

**Combined Ratio**：綜合比率
- (賠款 + 費用) / 保費收入
- < 100%：承保賺錢
- > 100%：承保虧錢（可能靠投資收益彌補）

**Loss Ratio**：賠款率
- 理賠 / 保費

**Float**：可用於投資的準備金
- 保費收進來但還沒理賠的錢
- Buffett 把 Berkshire 的保險業務當「低成本槓桿」

## 電信

**ARPU（Average Revenue Per User）**：每用戶平均收入
- 每月每個用戶帶來多少錢
- 衡量定價力與用戶升級

**Churn Rate**：流失率
- 用戶流失佔比
- 通訊業一般月流失 1–2%

**Subscriber Net Adds**：淨增用戶數
- 新增 - 流失

**Capex Intensity**：資本支出強度
- 5G 建設期：20–25%
- 成熟期：10–15%

## 航空

**RASM（Revenue per Available Seat Mile）**：每座位英里收入
- 總收入 / (座位數 × 航班英里)
- 能反映票價 + 載客率的綜合表現

**CASM（Cost per Available Seat Mile）**：每座位英里成本
- 單位成本

**PRASM（Passenger RASM）**：每座位英里的乘客收入

**Load Factor**：載客率
- 佔用座位 / 總座位
- > 85%：健康
- < 75%：問題

**Yield**：每乘客每英里的平均票價

## 電商

**GMV（Gross Merchandise Value）**：總交易金額
- 平台交易總額（不是公司營收）
- Amazon、Alibaba、Shopify 都有

**Take Rate**：抽成率
- 營收 / GMV
- Amazon 約 15%、Shopify 約 2%（因為 Shopify 是 self-serve）

**Active Customers / Users**：活躍客戶數

**Frequency**：購買頻率
- 每個用戶每年買幾次

**AOV（Average Order Value）**：平均訂單金額

## 網路平台 / 廣告

**MAU / DAU**：月活 / 日活用戶
- Meta、Snap、X 的核心指標

**DAU/MAU Ratio**：黏著度
- 60%+：極黏
- Facebook 約 66%

**ARPU**（廣告平台）：平均每用戶廣告收入
- Meta 美國 ARPU 約 $200/year

**Engagement / Time spent**：使用時間
- 反映產品黏性

**Ad load**：廣告密度
- 單位時間的廣告數
- 提升有天花板（過多會趕走用戶）

## 製藥

**Pipeline**：在研管線
- Phase 1、2、3 各有多少藥物

**Patent expiry schedule**：專利到期時間表
- 專利到期 = 學名藥湧入 = 營收崩

**Pipeline NPV**：在研產品淨現值
- 估算所有在研藥的未來價值

**Market exclusivity**：市場獨佔期

## 能源 / 油氣

**Reserves**：儲量
- Proved Reserves（P1）
- Probable Reserves（P2）
- Possible Reserves（P3）

**Reserve Life**：儲量壽命
- Reserves / 年產量
- 15 年 +：相對穩健

**F&D Cost（Finding and Development Cost）**：探勘開發成本
- 新增一桶油儲量要花多少錢

**Break-even oil price**：損益平衡油價
- 生產商能活下去的最低油價

## 房地產 / REIT

**Occupancy Rate**：出租率
- > 95%：極健康
- < 85%：可能有問題

**NOI（Net Operating Income）**：淨營業收入
- 房地產的「營業利益」

**FFO（Funds From Operations）**：營運資金
- REIT 的「實質獲利」（加回折舊）
- Price-to-FFO 是 REIT 的估值倍數

**Same-store NOI growth**：同物業淨營業收入成長
- 排除新增物業的成長率

## 汽車

**Volume**：銷量
- 輛數

**ASP**：平均售價
- 每輛車售價（mix 效應會體現）

**Incentive spending**：促銷支出
- 折扣、補貼
- 高 incentive = 需求弱

**Inventory days**：庫存天數
- 超過 60–70 天：可能要降價促銷

**Order backlog**（Tesla、EV 廠）：訂單積壓

## 一個學習方法：讀法說會時標 KPI

每當你讀一家公司的法說會逐字稿，**把他們提到的 KPI 全部標出來**，並查每個是什麼意思。

這個動作做一次可能要幾小時。但做完你就掌握了這個產業的**語言**。之後再讀同產業的其他公司，速度會快 5 倍。

## 跨產業比較 vs 產業內比較

**產業內比較**（同產業）：用產業專屬 KPI
- 比 Salesforce vs ServiceNow：用 NRR、Rule of 40
- 比 台積電 vs 中芯國際：用 wafer starts、ASP、yield

**跨產業比較**：只能用通用指標
- ROIC、ROE、成長率、毛利率

**不要混用**。不能拿「SaaS 的 Rule of 40」去評價半導體公司（後者不適用）。

## 一個常見陷阱：只看 KPI 忽略 GAAP

有些公司會**只強調自選 KPI**，迴避 GAAP 數字。

**警訊**：

- 公司的 Prepared Remarks 幾乎不講 GAAP 淨利、GAAP 毛利，只講 Non-GAAP、調整後、ARR 這類
- 公司的「調整」把越來越多東西排除（Stock-based compensation、併購成本、restructuring 等）

**規則**：**Non-GAAP 可以看，但 GAAP 不能忽視**。如果一家公司 Non-GAAP 獲利高，但 GAAP 持續虧損，這是警訊。

## 自我檢核

- [ ] 我知道產業 KPI 是「領先指標」，比 GAAP 營收/淨利更能反映未來。
- [ ] 我能說出 SaaS 的核心 KPI（ARR、NRR、LTV/CAC、Rule of 40）。
- [ ] 我能說出零售的核心 KPI（SSS、Traffic、Ticket Size）。
- [ ] 我能說出銀行的核心 KPI（NIM、Efficiency Ratio、NPL）。
- [ ] 我能說出半導體製造的核心 KPI（Utilization、ASP、Yield、Capex Intensity）。
- [ ] 我知道跨產業比較只用通用指標（ROIC、成長率、毛利率）。
- [ ] 我不會被公司只強調的 Non-GAAP 數字誤導 — 總是對照 GAAP。

Part 5 結束。下一部分是 Part 6：**從分析到決策**。把你累積的所有資訊，收斂成一個可執行的投資論點。

→ [Ch 25 建立你的產業心智模型](./25-mental-models.md)
