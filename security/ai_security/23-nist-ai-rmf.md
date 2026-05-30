# Ch 23 — NIST AI RMF

> **目標**：能用自己的話解釋 NIST AI RMF 的四大功能（Govern / Map / Measure / Manage），能把每個功能映射到具體的 AI 資安實務。

---

## 為什麼需要這個？

你會攻擊 LLM、會架 RAG、會寫 Guardrails——技術上你準備好了。但面試官問你一句「你怎麼用框架把這些串起來？」你就卡住了。

技術能力和治理框架的關係，像是會開車和有駕照的關係。會開車不代表你懂交通法規、知道誰有路權、出事怎麼通報。NIST AI RMF（AI Risk Management Framework）是目前 AI 資安領域最廣泛採用的治理框架。它不是法律（不像 EU AI Act 有罰則），而是一個 voluntary framework——但事實上已經成為 de facto 標準。美國聯邦機構、大型企業、甚至歐洲公司在建立 AI 治理體系時，NIST AI RMF 是第一個被參考的文件。

你在面試裡被問到「你怎麼評估一個 AI 系統的風險？」，能回答「我會用 NIST AI RMF 的四大功能來結構化這個問題」——這句話的含金量比「我會跑 prompt injection 測試」高得多。因為它代表你不只會打，還知道怎麼把打出來的結果放進組織的風險管理流程。

---

## 先建立直覺

把 NIST AI RMF 想像成一個 AI 系統的「全身健康檢查流程」：

```
你去做健康檢查：

1. GOVERN（院方制度）
   → 醫院的檢查標準是什麼？誰負責判讀？出問題怎麼通報？
   → 對應：組織的 AI 治理政策、角色、問責機制

2. MAP（了解你的狀況）
   → 你幾歲？有什麼家族病史？生活習慣？
   → 對應：識別 AI 系統的 context、stakeholders、風險面

3. MEASURE（做檢查）
   → 抽血、照 X 光、量血壓——用具體數據衡量健康狀態
   → 對應：量化 AI 風險——metrics、testing、monitoring

4. MANAGE（處理結果）
   → 膽固醇高就吃藥、肝指數異常就追蹤、嚴重就開刀
   → 對應：mitigate / accept / transfer 風險
```

關鍵：這四步不是線性流程。你不會做完一次健檢就再也不去了。GOVERN 是持續的制度運作，MAP → MEASURE → MANAGE 是不斷迭代的循環。

---

## 核心概念

### NIST AI RMF 的定位

2023 年 1 月，NIST 發布 AI RMF 1.0。它的定位是：

- **Voluntary**：沒有法律約束力，不遵守不會被罰
- **Sector-agnostic**：不針對特定產業，任何用 AI 的組織都適用
- **Risk-based**：以風險管理為核心，不是 checkbox compliance
- **和 NIST CSF 互補**：NIST CSF（Cybersecurity Framework）管傳統資安，AI RMF 是 CSF 在 AI 領域的延伸

2024 年 7 月，NIST 又發布了 Generative AI Profile——專門針對生成式 AI 的補充文件。這份文件新增了 prompt injection、hallucination、CBRN 生成等風險的具體指引。面試時提到這份文件會是加分項。

### 和 NIST CSF 的關係

```
NIST CSF（傳統資安）         NIST AI RMF（AI 資安）
┌──────────────┐            ┌──────────────┐
│ Identify     │            │ GOVERN       │
│ Protect      │            │ MAP          │
│ Detect       │  互補延伸   │ MEASURE      │
│ Respond      │ ─────────→ │ MANAGE       │
│ Recover      │            │              │
└──────────────┘            └──────────────┘
```

CSF 的五大功能（Identify / Protect / Detect / Respond / Recover）處理的是系統被入侵、資料被竊的風險。AI RMF 處理的是 AI 特有的風險——bias、hallucination、privacy leakage、adversarial manipulation。兩者不衝突，應該同時使用。

---

## 底層機制：四大功能的設計邏輯

NIST 為什麼用「四個功能」而不是「十個步驟」或「一百個 checklist 項目」？

設計邏輯是：風險管理不是填表格，而是建立組織能力。每個功能代表一種組織必須具備的能力：

1. **GOVERN**：有制度地做決策的能力
2. **MAP**：看清全貌的能力
3. **MEASURE**：量化和驗證的能力
4. **MANAGE**：採取行動的能力

這四個功能形成一個持續迭代的循環：

```
        ┌──────────┐
        │  GOVERN  │ ← 橫跨所有功能，持續運作
        └────┬─────┘
             │ 制度支撐
    ┌────────┼────────┐
    ▼        ▼        ▼
┌──────┐ ┌───────┐ ┌──────┐
│ MAP  │→│MEASURE│→│MANAGE│
└──┬───┘ └───┬───┘ └──┬───┘
   │         │        │
   └─────────┴────────┘
        持續迭代回饋
```

GOVERN 不是「第一步」——它是整個框架的基礎層，在 MAP / MEASURE / MANAGE 的每一步都在運作。MAP 發現新風險，MEASURE 量化它，MANAGE 處理它，然後回到 MAP 看處理後的殘餘風險——永不停止。

---

## 四大功能詳解

### GOVERN：組織治理

GOVERN 回答的問題：**「誰負責 AI 風險管理？用什麼規則？」**

重要 subcategory：

| 編號 | Subcategory | 白話解釋 |
|------|-------------|----------|
| GV-1 | 政策與程序 | 有沒有正式的 AI 治理政策文件？ |
| GV-2 | 問責機制 | 出事了誰負責？有沒有明確的 RACI matrix？ |
| GV-3 | 人才與訓練 | 團隊有沒有 AI 風險的專業知識？有沒有培訓？ |
| GV-4 | 利害關係人參與 | 開發 AI 時有沒有讓受影響的人參與決策？ |
| GV-5 | 風險偏好 | 組織能接受多大的 AI 風險？有沒有量化？ |
| GV-6 | 法規遵循 | 有沒有追蹤 AI 相關的法規變化（EU AI Act 等）？ |

**面試考點**：很多人以為 GOVERN 是「管理層的事，技術人員不需要管」。錯。GOVERN 裡的政策決定了你在技術實作上能做什麼、不能做什麼。比如 GV-5 風險偏好決定了你的 RAG chatbot 能不能用在醫療場景——這直接影響你的技術架構選擇。

### MAP：風險識別

MAP 回答的問題：**「這個 AI 系統有哪些風險？」**

重要 subcategory：

| 編號 | Subcategory | 白話解釋 |
|------|-------------|----------|
| MP-1 | 使用情境 | AI 系統在什麼場景被使用？有哪些 intended use 和 misuse？ |
| MP-2 | 受影響群體 | 誰會被這個 AI 系統影響？他們有沒有被通知？ |
| MP-3 | 技術規格 | 模型架構、訓練資料、已知限制是什麼？ |
| MP-4 | 風險辨識 | 有哪些 AI 特有風險（bias、hallucination、adversarial attack）？ |
| MP-5 | 第三方依賴 | 用了哪些外部模型、API、資料來源？ |

### MEASURE：風險評估

MEASURE 回答的問題：**「風險有多大？怎麼量化？」**

重要 subcategory：

| 編號 | Subcategory | 白話解釋 |
|------|-------------|----------|
| MS-1 | 測試方法 | 用什麼方法測試 AI 風險（red teaming、benchmark、A/B test）？ |
| MS-2 | 量化指標 | 風險用什麼 metrics 衡量（hallucination rate、PII leakage rate）？ |
| MS-3 | 持續監控 | 部署後怎麼持續監控（LangSmith、Arize Phoenix）？ |
| MS-4 | 人工評估 | 有沒有 human-in-the-loop 評估機制？ |
| MS-5 | 文件化 | 測試結果有沒有被文件化和追蹤？ |

### MANAGE：風險處置

MANAGE 回答的問題：**「風險要怎麼處理？」**

重要 subcategory：

| 編號 | Subcategory | 白話解釋 |
|------|-------------|----------|
| MG-1 | 風險處置策略 | 每個風險要 mitigate、accept、還是 transfer？ |
| MG-2 | 緩解措施 | 具體做了什麼（input filtering、output monitoring、model update）？ |
| MG-3 | 剩餘風險 | 處理完之後還剩多少風險？在可接受範圍內嗎？ |
| MG-4 | 事件應變 | AI 出事的時候怎麼辦（Ch 27 會深入）？ |
| MG-5 | 退役計畫 | AI 系統不用了怎麼下架？資料怎麼處理？ |

---

## 進一步用法：範例——用 AI RMF 評估 RAG Chatbot

假設你的公司建了一個內部 RAG chatbot，讓員工查詢 HR 政策。用 NIST AI RMF 評估：

### GOVERN

- 任命 AI 風險 owner（可能是 CISO 或專門的 AI governance lead）
- 制定 RAG chatbot 的使用政策（誰能用、能問什麼、不能問什麼）
- 定義風險偏好：HR 資訊錯誤的容忍度是零（因為員工會照做）

### MAP

- 使用情境：員工查詢請假規定、薪資結構、福利方案
- 潛在 misuse：員工試圖透過 chatbot 獲取其他人的薪資資訊
- 受影響群體：全公司員工（如果 chatbot 給錯答案，員工權益受損）
- 風險清單：
  - Hallucination：chatbot 編造不存在的政策
  - PII leakage：chatbot 洩漏知識庫裡的個人資料
  - Prompt injection：員工繞過限制存取未授權文件
  - RAG poisoning：知識庫被植入錯誤文件

### MEASURE

- Red teaming：對 chatbot 執行 prompt injection 和 jailbreak 測試（Ch 14 的方法）
- Metrics：hallucination rate、PII leakage rate、response accuracy
- Monitoring：用 LangSmith 追蹤每次查詢的 trace（Ch 17）
- Human evaluation：每週抽樣 50 筆 chatbot 回答，由 HR 人員判斷正確性

### MANAGE

- Mitigate：加 input filtering 擋 prompt injection、加 output filtering 擋 PII
- Accept：hallucination rate < 2% 可接受（加上免責聲明提醒員工以官方文件為準）
- Transfer：把 chatbot 的維運外包給有經驗的團隊（如果內部不具備能力）
- Incident response：chatbot 給出錯誤政策資訊時的通報和修正流程
- 退役計畫：chatbot 被替換或下線時，如何處理蒐集到的 prompt 資料和知識庫

### 把四大功能串成一份報告

上面的分析，最後要整合成一份 AI Risk Assessment Report。報告的結構：

```
AI 風險評估報告 — HR RAG Chatbot
版本：1.0
日期：2025-XX-XX

1. GOVERN 摘要
   - AI 風險 owner：CISO
   - 治理政策：AI-POL-001
   - 風險偏好：HR 資訊錯誤容忍度 = 低

2. MAP 結果
   - 4 個已識別風險（hallucination / PII leakage /
     prompt injection / RAG poisoning）
   - 受影響群體：全公司 500 名員工

3. MEASURE 結果
   - Red team 結果：3/20 prompt injection 測試成功
   - Hallucination rate：4.2%（超過 2% 目標）
   - PII leakage：測試中未偵測到

4. MANAGE 決策
   - Mitigate：input filtering（預計降低 injection 成功率至 0）
   - Mitigate：加強 system prompt（預計降低 hallucination 至 < 2%）
   - Accept：已加免責聲明
   - 下次評估：3 個月後
```

這份報告的讀者是管理層和稽核人員。技術細節放在附件，報告本體用管理語言寫。

### AI RMF Playbook 的使用方式

NIST 提供了一份 Playbook（https://airc.nist.gov/AI_RMF_Playbook），針對每個 subcategory 列出：

- **Suggested Actions**：具體該做什麼
- **Transparency & Documentation**：該記錄什麼
- **AI Actors**：誰負責執行

使用方法：不要從頭讀到尾。挑和你系統相關的 subcategory，讀 suggested actions，逐項檢查你做了沒有。

---

## 對比與取捨

| 維度 | NIST AI RMF | ISO/IEC 42001 | EU AI Act |
|------|-------------|---------------|-----------|
| 性質 | Voluntary framework | International standard（可認證） | Regulation（法律） |
| 約束力 | 無法律約束力 | 自願認證，但有商業壓力 | 違反有罰則（最高 3500 萬歐元或全球營收 7%） |
| 適用範圍 | 全球，但美國主導 | 全球 | 歐盟境內（但有域外效力） |
| 成熟度 | 1.0（2023）+ GenAI Profile（2024） | 2023 年 12 月發布 | 2024 年通過，2025-2026 分階段生效 |
| 取得成本 | 免費 | 標準文件需付費（約 200 美元） | 免費（法規文本公開） |
| 技術深度 | 中（有 Playbook 細節） | 低（管理系統框架） | 高（針對高風險 AI 有具體技術要求） |
| 最適合 | 建立 AI 風險管理流程 | 建立 AI 管理系統、取得認證 | 在歐盟銷售 AI 產品 |

---

## 踩雷集錦

1. **「背下四大功能就夠了」**——面試官不會只問你「NIST AI RMF 有哪四個功能」。他們會問「GOVERN 裡面有哪些 subcategory？你實務上怎麼做 GV-2 問責機制？」如果你只會唸 GOVERN / MAP / MEASURE / MANAGE，面試官馬上知道你是背的。

2. **混淆 AI RMF 1.0 和 Generative AI Profile**——AI RMF 1.0（2023 年 1 月）是通用框架。Generative AI Profile（2024 年 7 月）是專門針對生成式 AI 的補充。後者新增了 12 個風險類別，包括 CBRN information generation、confabulation（hallucination 的正式說法）、data privacy。面試裡提到 GenAI Profile 是明顯的加分。

3. **「GOVERN 是管理層的事，技術人員不需要懂」**——GOVERN 裡的決策直接影響你的技術選擇。GV-5 風險偏好如果設得很低，你可能需要加多層 guardrails、human-in-the-loop、甚至放棄用 LLM。不理解 GOVERN，你做的技術方案可能和組織的風險偏好完全不符。

4. **把 NIST AI RMF 當 checklist 用**——NIST 明確說了：AI RMF 是 risk-based framework，不是 compliance checklist。「把每個 subcategory 打勾」不等於「管理好了 AI 風險」。重要的是理解你系統的具體風險，然後用框架來結構化你的分析。

5. **忽略 MAP 的 stakeholder 識別**——很多技術人員跳過 MAP 裡的 MP-2（受影響群體）。但面試官偏偏愛問這個。你的 chatbot 影響的不只是直接使用者——間接受影響的人（例如 chatbot 回答關於某個員工的資訊）也算在內。漏掉 stakeholder 分析，你的風險評估就不完整。

---

## 進階：再往深一層

### Generative AI Profile 的 12 個風險

2024 年的 GenAI Profile 列出了生成式 AI 特有的 12 個風險：

1. **CBRN Information**：模型生成化學 / 生物 / 放射性 / 核子武器相關資訊
2. **Confabulation**：模型生成看起來正確但實際上錯誤的資訊
3. **Data Privacy**：訓練資料中的個人資訊被模型記憶和輸出
4. **Environmental**：大型模型訓練和推論的能源消耗
5. **Harmful Bias**：模型輸出的歧視性內容
6. **Homogenization**：所有人用同一個模型導致觀點單一化
7. **Information Integrity**：deepfake、自動化假訊息
8. **Information Security**：prompt injection、data extraction
9. **Intellectual Property**：模型輸出侵犯版權
10. **Obscene Content**：模型生成不當內容
11. **Third-party Risks**：依賴第三方模型和 API 的風險
12. **Value Chain**：AI 供應鏈全鏈風險

### 實務上怎麼開始

如果你明天要在公司導入 NIST AI RMF，第一步不是從 GOVERN 開始。務實的做法是：

1. 先做 MAP：列出公司所有的 AI 系統（很多組織連自己用了多少 AI 都不知道）
2. 對每個系統做初步 risk assessment
3. 用結果去說服管理層建立 GOVERN 制度
4. 建立 MEASURE 和 MANAGE 的循環

### AI RMF 和 Executive Order 14110 的關聯

2023 年 10 月，美國總統 Biden 簽署了 Executive Order 14110（Safe, Secure, and Trustworthy AI）。這份行政命令要求聯邦機構在 AI 開發和部署上採用 NIST AI RMF 的原則。雖然 EO 14110 直接約束的是聯邦機構，但它的溢出效應很大——和聯邦機構做生意的承包商、供應鏈上的企業，事實上也需要遵循。

面試考點：被問到「NIST AI RMF 有法律約束力嗎？」標準答案是「沒有——但 EO 14110 讓它在聯邦領域接近強制」。

### AI RMF Profiles 和 Tiers

NIST AI RMF 借鑑了 CSF 的 Profile 和 Tier 概念：

**Profile**：
- Profile 是你對框架的客製化——根據你的組織、產業、法規，挑選相關的 subcategory
- 例：一家醫療 AI 公司的 Profile 會強調 bias（MP-4）和 human oversight（MS-4），而一家做廣告推薦的公司可能更關注 data privacy（MP-3）

**Tier**（成熟度等級）：
- **Tier 1 — Partial**：AI 風險管理是 ad hoc 的，沒有正式流程
- **Tier 2 — Risk Informed**：有風險意識，但流程不統一
- **Tier 3 — Repeatable**：有一致的、可重複的 AI 風險管理流程
- **Tier 4 — Adaptive**：持續改善、能快速回應新風險

大多數組織目前在 Tier 1-2。面試裡如果能說出「我們公司目前在 Tier 2，目標是兩年內到 Tier 3」，展示的是你對框架的實務理解，而不是死背。

### 四大功能的面試答題框架

面試官問「你怎麼用 NIST AI RMF 評估 AI 風險？」時的回答結構：

```
1. 先確認 GOVERN 的基礎有沒有建好
   → 「我會先確認組織有沒有 AI 治理政策和問責機制」

2. 用 MAP 識別風險面
   → 「然後我會 map 出系統的 context——誰用、用在哪、
      有什麼 third-party dependency、風險清單長什麼樣」

3. 用 MEASURE 量化風險
   → 「接著用 red teaming 和 monitoring 量化這些風險
     ——hallucination rate 多少、PII leakage rate 多少」

4. 用 MANAGE 處理風險
   → 「最後決定每個風險要 mitigate、accept、還是 transfer，
      並建立 incident response 流程」

5. 強調迭代
   → 「這不是一次性的評估——系統每次更新都要重新跑這個循環」
```

把這個結構練到能在 2 分鐘內流暢講完。

---

## 動手練習

1. **框架映射**：拿出你在 Ch 3 建的 RAG chatbot，用 NIST AI RMF 的四大功能各列出 3 個具體的 action item。例：GOVERN → 定義誰有權限修改 system prompt。

2. **Subcategory 對照**：去 NIST AI RMF Playbook 網站（https://airc.nist.gov/AI_RMF_Playbook），挑 MEASURE 功能裡的任意 3 個 subcategory，寫出你在 RAG chatbot 上會怎麼實作 suggested actions。

3. **口述練習**：閉上 Playbook，用自己的話向一個不懂技術的人解釋：「NIST AI RMF 是什麼？為什麼我們公司需要它？」控制在 2 分鐘內。能做到就代表你真的理解了。

---

## 本章重點整理

- NIST AI RMF 是 voluntary framework，不是法律——但已成為 AI 風險管理的 de facto 標準。
- 四大功能：GOVERN（治理制度）、MAP（風險識別）、MEASURE（風險量化）、MANAGE（風險處置）。
- GOVERN 橫跨所有功能，是基礎層。MAP → MEASURE → MANAGE 是持續迭代的循環。
- 和 NIST CSF 互補：CSF 管傳統資安，AI RMF 管 AI 特有風險。
- 2024 年的 Generative AI Profile 新增了 12 個生成式 AI 特有風險。
- Playbook 是最實用的工具：針對每個 subcategory 列出具體 actions。
- 面試不能只背四大功能——要能講 subcategory 和具體做法。

---

## 自我檢核

- [ ] 能用自己的話解釋 NIST AI RMF 的四大功能和它們的關係
- [ ] 能說出 GOVERN 至少 3 個 subcategory，並解釋它們為什麼重要
- [ ] 能區分 NIST AI RMF 和 NIST CSF 的適用範圍
- [ ] 能說出 Generative AI Profile 新增的至少 5 個風險
- [ ] 能對一個 RAG chatbot 用四大功能做完整的風險評估
- [ ] 能解釋為什麼 AI RMF 是「framework」而不是「checklist」

---

## 延伸閱讀

- **NIST AI RMF Playbook**（https://airc.nist.gov/AI_RMF_Playbook）
  - 讀哪裡：GOVERN 和 MEASURE 部分的 suggested actions
  - 學什麼：每個 subcategory 的具體實作指引
  - 關聯：Ch 26 AI 安全政策撰寫會直接引用 GOVERN 的 subcategory

- **NIST AI 600-1: Generative AI Profile**（https://airc.nist.gov/Docs/1）
  - 讀哪裡：12 個風險類別的定義和對應的 suggested actions
  - 學什麼：生成式 AI 特有的風險分類
  - 關聯：Ch 25 AI 威脅建模會用到這些風險類別

- **NIST AI RMF 官方文件 AI 100-1**（https://doi.org/10.6028/NIST.AI.100-1）
  - 讀哪裡：Part 2（Core and Profiles），理解四大功能的完整結構
  - 學什麼：框架的設計哲學和適用範圍
  - 關聯：Ch 24 ISO 42001 會和這份文件做對比

---

→ 下一章：[Ch 24 — ISO/IEC 42001](./24-iso-42001.md)
