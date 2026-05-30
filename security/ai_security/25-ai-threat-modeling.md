# Ch 25 — AI 威脅建模方法論

> **目標**：能用 STRIDE-AI 和 MITRE ATLAS 對 AI 系統做威脅建模，產出結構化的威脅清單和 attack tree。

---

## 為什麼需要這個？

你已經學了 NIST AI RMF 的 MAP 功能——識別 AI 系統的風險。但「識別風險」是抽象的，實務上你需要一套具體的方法論來做。

威脅建模（Threat Modeling）是把「這個系統有哪些風險」轉化成「誰可能用什麼方法攻擊哪個元件，造成什麼後果」的結構化過程。傳統軟體有 STRIDE，網路安全有 MITRE ATT&CK，AI 安全有 STRIDE-AI 和 MITRE ATLAS。

面試裡如果被問「你怎麼評估一個 AI 系統的安全風險？」，回答「我會用 STRIDE-AI 對每個元件做威脅分析，再對照 MITRE ATLAS 確認沒有遺漏」——這個回答的結構性和可操作性，遠超過「我會做 prompt injection 測試」。

---

## 先建立直覺

威脅建模的目的不是列出所有可能的攻擊。那是不可能的——攻擊手法每天都在變。威脅建模的真正目的是幫你決定**「先防什麼」**。

把它想像成你搬進新家要裝保全系統：

```
錯誤做法：
  → 列出所有可能的入侵方式（翻牆、撬鎖、挖地道、直升機空降…）
  → 每一個都裝防護設備
  → 結果：花了一百萬、住得像監獄、還是可能被社工進去

正確做法（威脅建模）：
  → 畫出你家的平面圖（系統架構圖）
  → 標出每個入口（trust boundary）
  → 對每個入口問：「誰可能從這裡進來？用什麼方法？」（STRIDE）
  → 對照犯罪資料庫看真實案例（MITRE ATLAS）
  → 根據 likelihood × impact 排優先順序
  → 先防最可能、損害最大的
```

---

## 核心概念

### 傳統 STRIDE 的六個維度

STRIDE 是 Microsoft 在 1999 年提出的威脅分類框架。每個字母代表一類威脅：

| 字母 | 威脅類型 | 白話解釋 | 被破壞的安全屬性 |
|------|---------|----------|----------------|
| **S** | Spoofing（冒充） | 攻擊者假冒他人身分 | Authentication |
| **T** | Tampering（竄改） | 攻擊者修改資料或程式碼 | Integrity |
| **R** | Repudiation（否認） | 攻擊者否認做過的行為 | Non-repudiation |
| **I** | Information Disclosure（資訊洩漏） | 未授權存取機密資訊 | Confidentiality |
| **D** | Denial of Service（阻斷服務） | 讓系統無法正常運作 | Availability |
| **E** | Elevation of Privilege（權限提升） | 攻擊者取得更高權限 | Authorization |

### STRIDE-AI：把 STRIDE 應用到 AI 特有元件

傳統 STRIDE 的對象是「web server」「database」「API endpoint」。STRIDE-AI 把分析對象擴展到 AI 系統特有的元件：

| AI 元件 | S（冒充） | T（竄改） | R（否認） | I（洩漏） | D（阻斷） | E（提權） |
|---------|----------|----------|----------|----------|----------|----------|
| **LLM** | 假冒模型回應 | Adversarial input 改變輸出 | 模型輸出無法歸因 | Training data 萃取 | 大量 prompt 耗盡資源 | Jailbreak 繞過安全限制 |
| **Training Data** | 偽造資料來源 | Data poisoning | 投毒無法追溯 | 資料集裡的 PII | 破壞 training pipeline | 透過投毒控制模型行為 |
| **Inference API** | API key 被盜用 | Prompt injection | 缺少 audit log | System prompt 洩漏 | DDoS / rate limit bypass | 繞過 API 存取控制 |
| **Embedding** | — | 操控 embedding 結果 | — | 透過 embedding 反推原文 | 大量 embedding 請求 | — |
| **Vector DB** | 假冒合法文件 | RAG poisoning | 文件修改無記錄 | 知識庫內容洩漏 | 向量搜尋過載 | 繞過 document ACL |
| **Agent Tool** | 假冒 tool 回應 | Tool hijacking | Tool call 無 audit | 工具回傳機密資料 | 無限 tool call loop | Agent 執行未授權操作 |

### MITRE ATLAS 攻擊知識庫

MITRE ATLAS（Adversarial Threat Landscape for AI Systems）是 MITRE 在 2021 年推出的 AI 攻擊知識庫。它和 MITRE ATT&CK 的關係是：

- **ATT&CK**：傳統網路攻擊的 tactics 和 techniques（你已經知道的 T1190、T1059 那些）
- **ATLAS**：AI/ML 系統攻擊的 tactics 和 techniques（編號不同，不要搞混）

ATLAS 的 Tactics（按攻擊階段）：

| Tactic | 說明 | 常見 Technique |
|--------|------|---------------|
| Reconnaissance | 偵查 AI 系統的資訊 | 收集模型架構、API 文件、training data 資訊 |
| Resource Development | 準備攻擊資源 | 建立對抗樣本、製作投毒資料 |
| Initial Access | 取得對 AI 系統的存取 | 透過 public API、前端介面、supply chain |
| ML Model Access | 取得對模型的存取 | Inference API abuse、model extraction |
| Execution | 在 AI 系統上執行攻擊 | Prompt injection（AML.T0051）、adversarial input |
| Persistence | 維持存取 | Backdoor in model、poisoned training data |
| Evasion | 繞過偵測 | 對抗樣本繞過分類器、prompt obfuscation |
| Impact | 造成損害 | 模型輸出操控、denial of service、data exfiltration |

**重要 Technique 編號**（面試會考）：

- **AML.T0051**：LLM Prompt Injection
- **AML.T0043**：Craft Adversarial Data
- **AML.T0018**：Backdoor ML Model
- **AML.T0044**：Full ML Model Access（model stealing）
- **AML.T0048**：Data Poisoning

注意：ATLAS 的編號格式是 `AML.T00XX`，和 ATT&CK 的 `T1XXX` 不同。面試裡搞混編號會暴露你沒有實際查過 ATLAS。

---

## 底層機制：威脅建模的設計邏輯

威脅建模的流程有四個步驟，和你用什麼框架無關：

```
Step 1: 畫出系統架構圖
  → 列出所有元件和它們之間的資料流

Step 2: 標出 trust boundary
  → 資料從一個信任域進入另一個信任域的地方

Step 3: 對每個 boundary 做威脅分析
  → 用 STRIDE-AI 逐一列出可能的威脅

Step 4: 排優先順序
  → Likelihood × Impact = Risk Score
  → 先處理 risk score 最高的
```

**Trust boundary（信任邊界）** 是最關鍵的概念。一個 RAG chatbot 的 trust boundary：

```
┌─────────────────────────────────────────────────┐
│  Untrusted Zone                                  │
│                                                  │
│  ┌──────────┐                                   │
│  │   User   │                                   │
│  └────┬─────┘                                   │
│       │ user input                               │
│ ══════╪══════════ Trust Boundary 1 ══════════════│
│       ▼                                          │
│  ┌──────────┐    ┌──────────┐                   │
│  │   API    │───→│  Input   │                   │
│  │ Gateway  │    │  Filter  │                   │
│  └──────────┘    └────┬─────┘                   │
│                       │                          │
│ ══════════════════════╪══ Trust Boundary 2 ══════│
│                       ▼                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │   LLM    │←───│   RAG    │←───│ Vector   │  │
│  │ (Ollama) │    │ Pipeline │    │   DB     │  │
│  └──────────┘    └──────────┘    └────┬─────┘  │
│                                       │         │
│ ══════════════════════════════════════╪══ TB 3 ═│
│                                       ▼         │
│                                  ┌──────────┐   │
│                                  │ Document  │   │
│                                  │ Source    │   │
│                                  └──────────┘   │
└─────────────────────────────────────────────────┘
```

Trust Boundary 1：user input → API。這裡是 prompt injection 的主要入口。

Trust Boundary 2：API → LLM / RAG pipeline。經過 input filter 的資料進入內部系統。filter 被繞過的話，攻擊直達 LLM。

Trust Boundary 3：Vector DB → Document Source。文件被 ingest 進知識庫的地方。如果文件來源被控制（RAG poisoning），攻擊者不需要碰前端就能操控 chatbot 的輸出。

**最常被遺漏的 trust boundary 是 TB 3**。很多人只防前端（user → API），忘了文件 ingestion 也是一個攻擊面。

### Attack Tree

Attack tree 是用樹狀結構表示攻擊路徑的方法。每個節點是一個攻擊步驟，節點之間用 AND（全部都要完成）或 OR（任一完成即可）連接。

範例——攻擊目標：讓 RAG chatbot 輸出錯誤的 HR 政策：

```
Goal: RAG chatbot 輸出錯誤 HR 政策
├── OR
│   ├── 1. Prompt Injection（直接）
│   │   ├── AND
│   │   │   ├── 1.1 找到繞過 input filter 的方法
│   │   │   └── 1.2 注入指令讓 LLM 忽略知識庫
│   │   
│   ├── 2. RAG Poisoning（間接）
│   │   ├── OR
│   │   │   ├── 2.1 在知識庫上傳偽造文件
│   │   │   │   ├── AND
│   │   │   │   │   ├── 2.1.1 取得文件上傳權限
│   │   │   │   │   └── 2.1.2 製作內容正確但結論錯誤的文件
│   │   │   └── 2.2 修改現有文件
│   │   │       ├── AND
│   │   │       │   ├── 2.2.1 取得文件編輯權限
│   │   │       │   └── 2.2.2 在文件中插入誤導性內容
│   │   
│   └── 3. Model Manipulation
│       ├── AND
│       │   ├── 3.1 取得 Ollama 管理介面存取
│       │   └── 3.2 替換模型為被投毒的版本
```

這棵 attack tree 告訴你：路徑 2.1（RAG poisoning via 偽造文件）的 likelihood 可能最高——因為很多組織的文件上傳流程缺乏審核。所以你應該先防這條路。

---

## 進一步用法：完整 STRIDE-AI 分析範例

對 RAG chatbot 的每個元件逐一分析：

### User → API Gateway（Trust Boundary 1）

| 威脅 | STRIDE 類別 | 攻擊手法 | Impact | 緩解措施 |
|------|------------|---------|--------|---------|
| 冒充合法使用者 | S | 竊取 API key / session token | 中 | API authentication + rate limiting |
| 注入惡意 prompt | T | Direct prompt injection | 高 | Input filtering（Ch 19）|
| 否認發送過的 prompt | R | 沒有 audit log | 低 | 啟用 LangSmith trace |
| 竊取其他使用者的回答 | I | Session hijacking | 高 | Session isolation |
| 大量請求癱瘓服務 | D | DDoS via prompt flooding | 中 | Rate limiting + queue |
| 繞過使用限制 | E | Jailbreak 取得 admin 功能 | 高 | Output filtering + RBAC |

### RAG Pipeline → Vector DB（Trust Boundary 2）

| 威脅 | STRIDE 類別 | 攻擊手法 | Impact | 緩解措施 |
|------|------------|---------|--------|---------|
| 偽造檢索結果 | S | 操控 similarity score | 中 | 結果驗證 |
| 修改知識庫 embedding | T | 直接寫入 Vector DB | 高 | DB access control |
| 檢索操作無記錄 | R | 缺少 query log | 低 | 啟用 Vector DB audit |
| 洩漏知識庫內容 | I | 透過 chatbot 提問萃取文件 | 高 | Document-level ACL（Ch 22）|
| 大量向量搜尋 | D | 搜尋過載 | 中 | Query rate limiting |

### Document Source → Vector DB（Trust Boundary 3）

| 威脅 | STRIDE 類別 | 攻擊手法 | Impact | 緩解措施 |
|------|------------|---------|--------|---------|
| 上傳偽造文件 | S | 冒充合法文件來源 | 高 | 文件來源驗證 + 簽章 |
| 文件內容被竄改 | T | RAG poisoning | 高 | 文件 hash 驗證 |
| 文件修改無記錄 | R | 缺少 version control | 中 | Git-based 文件管理 |
| 文件中的 PII | I | 敏感文件被 ingest | 高 | PII 掃描（Ch 21）|
| 大量文件 ingest | D | 撐爆 Vector DB | 中 | Ingest rate limiting |
| 透過文件提權 | E | 文件包含 prompt injection payload | 高 | 文件內容掃描 |

---

## 對比與取捨

| 維度 | STRIDE-AI | MITRE ATLAS | OWASP Top 10 for LLM |
|------|-----------|-------------|----------------------|
| 粒度 | 元件級（逐個元件分析） | Technique 級（逐個攻擊手法） | 風險類別級（十大類） |
| 用途 | 架構設計階段的威脅分析 | 攻擊情報和紅隊參考 | 快速了解主要風險 |
| 結構 | 6 維度 × N 元件 | Tactics → Techniques 層級 | 排名清單 |
| 和其他框架的關係 | 可獨立使用 | 和 ATT&CK 互補 | 和 STRIDE 互補 |
| 互補性 | 用 STRIDE-AI 做分析，用 ATLAS 驗證完整性，用 OWASP 做 high-level 溝通 |

三者不是互相取代的關係，而是在不同層級互補。最佳做法是：用 STRIDE-AI 做系統性分析 → 用 ATLAS 確認有沒有漏掉的已知攻擊手法 → 用 OWASP Top 10 for LLM 向管理層報告。

---

## 踩雷集錦

1. **「threat modeling 只做一次就好」**——系統每次改動都可能引入新的 trust boundary 或新的元件。加了一個新的 tool 給 agent？威脅模型要更新。換了 embedding model？威脅模型要更新。threat modeling 是持續的活動，不是一次性的交付物。

2. **ATLAS 的 technique 編號和 ATT&CK 不同**——ATLAS 用 `AML.T00XX`，ATT&CK 用 `T1XXX`。面試裡說「prompt injection 是 T1190」就暴露了你把兩個框架搞混（T1190 是 ATT&CK 裡的 Exploit Public-Facing Application，和 prompt injection 完全不同）。

3. **Trust boundary 畫錯會漏掉攻擊面**——最常見的錯誤是忘了 document ingestion 也是一個 trust boundary。很多團隊把 80% 的防禦資源放在 user → API 這條路，對文件上傳流程毫無防護——結果 RAG poisoning 直接繞過所有 input filter。

4. **把 threat modeling 和 penetration testing 搞混**——threat modeling 是在 design 階段做的（你畫架構圖、分析可能的威脅）。penetration testing 是在 implementation 階段做的（你實際去打）。前者是紙上談兵但有結構，後者是實際動手但可能遺漏。兩者互補，不能只做一個。

---

## 進階：再往深一層

### DREAD 風險評分

對每個威脅做 risk assessment 時，可以用 DREAD 模型評分：

| 維度 | 說明 | 評分（1-10） |
|------|------|-------------|
| **D**amage | 攻擊造成的損害 | 10 = 完全接管系統 |
| **R**eproducibility | 攻擊的可重複性 | 10 = 每次都成功 |
| **E**xploitability | 攻擊的難度 | 10 = 不需要技術能力 |
| **A**ffected users | 受影響的使用者數量 | 10 = 所有使用者 |
| **D**iscoverability | 攻擊被發現的難度 | 10 = 攻擊者能輕易找到入口 |

Risk Score = (D + R + E + A + D) / 5

範例：RAG chatbot 的 direct prompt injection

- Damage: 7（可能輸出錯誤或有害資訊）
- Reproducibility: 6（有些 prompt 不穩定）
- Exploitability: 8（不需要什麼技術能力，打字就行）
- Affected users: 5（只影響發送該 prompt 的使用者）
- Discoverability: 9（chatbot 的介面就在那裡）

Risk Score = (7+6+8+5+9) / 5 = 7.0 → 高優先處理

### ATLAS Case Study：ChatGPT Plugin 攻擊鏈

MITRE ATLAS 收錄了多個真實 AI 攻擊案例。以下是一個和 RAG chatbot 高度相關的攻擊模式（基於多個 ATLAS case study 組合）：

```
攻擊鏈：透過 plugin / tool 進行資料竊取

1. Reconnaissance（AML.T0000）
   → 攻擊者查看目標 chatbot 的公開文件，確認它有 web browsing tool

2. Resource Development
   → 攻擊者建立一個惡意網頁，內容包含 indirect prompt injection payload

3. Initial Access（AML.T0051 — Prompt Injection）
   → 攻擊者讓目標使用者請 chatbot 瀏覽惡意網頁
   → 或者透過 RAG poisoning 讓惡意內容被 ingest 進知識庫

4. Execution
   → Chatbot 讀取惡意網頁，執行了隱藏的 prompt injection
   → 注入的指令讓 chatbot 把使用者的歷史對話附在一個 URL 裡

5. Exfiltration
   → Chatbot 生成一個包含竊取資料的 markdown image 連結
   → 使用者的瀏覽器自動載入該圖片 → 資料送到攻擊者的 server

這個攻擊鏈用到的 ATLAS Techniques：
  - AML.T0051（Prompt Injection）
  - AML.T0048（Data Poisoning — 如果透過 RAG）
  - 傳統 ATT&CK 的 exfiltration 概念
```

這個案例告訴你：威脅建模不能只看單一元件。攻擊者會組合多個 technique 形成攻擊鏈。你的 threat model 需要考慮 technique chaining。

### 威脅建模的交付物格式

做完 threat modeling 後，你需要產出一份結構化的文件。常見格式：

```
威脅建模報告 — [系統名稱]
日期：[date]
版本：[version]
分析者：[name]

1. 系統描述
   - 架構圖（含 trust boundary）
   - 元件清單
   - 資料流描述

2. 威脅清單
   ID | 元件 | STRIDE 類別 | 威脅描述 | ATLAS 編號 | DREAD 分數 | 優先順序
   T-001 | API Gateway | T | Direct prompt injection | AML.T0051 | 7.0 | High
   T-002 | Vector DB | T | RAG poisoning | AML.T0048 | 6.8 | High
   ...

3. Attack Trees（高風險威脅）
   - 針對 DREAD ≥ 6.0 的威脅畫 attack tree

4. 建議的緩解措施
   - 按優先順序列出

5. 殘餘風險
   - 緩解後仍存在的風險和 acceptance 理由
```

### 自動化威脅建模工具

- **Microsoft Threat Modeling Tool**：可以畫 DFD（Data Flow Diagram）並自動生成 STRIDE 威脅
- **OWASP Threat Dragon**：開源的 threat modeling 工具
- **Threagile**：用 YAML 描述架構，自動生成威脅報告

目前還沒有專門針對 AI 系統的自動化 threat modeling 工具。最接近的做法是用通用工具畫架構圖，再手動加入 AI 特有的威脅。

### 威脅建模在面試裡怎麼答

面試官問「你怎麼對一個 AI 系統做 threat modeling？」時的回答框架：

1. **先畫架構圖**：「我會先和開發團隊一起畫出系統的 DFD，標出所有元件和 trust boundary」
2. **用 STRIDE-AI 分析**：「然後對每個 trust boundary 用 STRIDE 的六個維度逐一分析 AI 特有的威脅」
3. **對照 ATLAS**：「分析完之後我會和 MITRE ATLAS 交叉比對，確認沒有遺漏已知的攻擊手法」
4. **用 DREAD 排序**：「用 DREAD 模型給每個威脅評分，排出優先順序」
5. **產出文件**：「最後產出威脅清單、attack tree 和建議緩解措施」
6. **強調持續更新**：「這份文件不是寫一次就好——每次系統改動都要更新」

能在 3 分鐘內把這個流程講清楚，面試官就知道你有實作經驗。

---

## 動手練習

1. **STRIDE-AI 分析**：畫出你在 Ch 3 建的 RAG chatbot 的架構圖（包含 trust boundary），然後對每個 boundary 做 STRIDE-AI 分析。至少產出 15 個威脅。

2. **Attack tree**：選擇一個攻擊目標（例如「從 chatbot 萃取知識庫中的機密文件」），畫出完整的 attack tree（至少 3 層、8 個 leaf node）。標出 AND/OR 關係。

3. **ATLAS 對照**：去 MITRE ATLAS 網站（https://atlas.mitre.org/），找到和你在練習 1 列出的威脅對應的 ATLAS technique。記錄至少 5 個 technique 的編號和名稱。

4. **DREAD 評分**：對練習 1 列出的 15 個威脅做 DREAD 評分，排出優先順序。你的 top 3 是什麼？和你直覺的排序一樣嗎？

---

## 本章重點整理

- STRIDE-AI 把傳統 STRIDE 的六個維度應用到 AI 特有元件（LLM、training data、embedding、agent tool）。
- MITRE ATLAS 是 AI 攻擊知識庫，和 ATT&CK 互補但編號不同（`AML.T00XX`）。
- Trust boundary 是威脅建模的核心概念——資料跨越信任域的地方是攻擊入口。
- Document ingestion 是最常被遺漏的 trust boundary。
- Attack tree 用 AND/OR 節點表示攻擊路徑，幫助你理解攻擊的前置條件。
- 威脅建模的目的不是列出所有攻擊——是幫你決定「先防什麼」。
- STRIDE-AI、ATLAS、OWASP Top 10 for LLM 三者在不同粒度互補，不是互相取代。
- 威脅建模是持續活動，系統每次改動都要更新。

---

## 自我檢核

- [ ] 能用自己的話解釋 STRIDE 的六個維度
- [ ] 能對一個 AI 系統元件用 STRIDE-AI 列出至少 4 個威脅
- [ ] 能說出 MITRE ATLAS 和 ATT&CK 的差異
- [ ] 能列出至少 3 個 ATLAS 的 technique 編號和名稱
- [ ] 能畫出 RAG chatbot 的 trust boundary 圖
- [ ] 能用 attack tree 表示一個完整的攻擊路徑
- [ ] 能解釋 DREAD 的五個維度

---

## 延伸閱讀

- **MITRE ATLAS**（https://atlas.mitre.org/）
  - 讀哪裡：Tactics overview 和 Case Studies
  - 學什麼：真實的 AI 攻擊案例和它們對應的 tactic/technique
  - 關聯：Ch 14 的 Red Team 方法論會引用 ATLAS 的 technique

- **"Threat Modeling: Designing for Security"**（Shostack, 2014）
  - 讀哪裡：Chapter 3-5，STRIDE 方法論的完整介紹
  - 學什麼：威脅建模的理論基礎和實作步驟
  - 關聯：本章的 STRIDE-AI 是這本書的 AI 延伸

- **Microsoft AI Red Team**（https://learn.microsoft.com/en-us/security/ai-red-team/）
  - 讀哪裡：AI red team best practices 和 case studies
  - 學什麼：Microsoft 怎麼對自家 AI 產品做威脅建模和紅隊測試
  - 關聯：Ch 14 Red Team 方法論的企業級實踐

- **OWASP AI Security and Privacy Guide**（https://owasp.org/www-project-ai-security-and-privacy-guide/）
  - 讀哪裡：Threat model section
  - 學什麼：OWASP 社群對 AI 威脅建模的建議做法
  - 關聯：和 Practice C 的 STRIDE 練習直接相關

---

→ 下一章：[Ch 26 — AI 安全政策撰寫](./26-ai-security-policy.md)
