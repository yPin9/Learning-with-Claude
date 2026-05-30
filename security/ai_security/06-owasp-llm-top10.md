# Ch 6 — OWASP Top 10 for LLM Applications 2025 全覽

> **目標**：能列出 OWASP Top 10 for LLM Applications 2025 全部十條，每條用自己的話解釋攻擊原理和真實案例。
> **環境**：Python 3.11, LangChain 0.3.x, Ollama + llama3.2:3b, Ubuntu 22.04

---

## 為什麼需要這個？

傳統 OWASP Top 10 你可能已經背得滾瓜爛熟——SQL Injection、XSS、CSRF——但這些分類假設攻擊目標是 web application。當目標換成 LLM-integrated application，攻擊面徹底改變：

- **輸入不再是結構化 HTTP 參數**，而是自然語言——你沒有型別系統可以擋
- **攻擊不只來自使用者**，RAG pipeline 裡的外部文件、Agent 呼叫的 API 回傳值都可能帶有惡意 payload
- **模型本身的行為是攻擊面**——overreliance、hallucination、training data memorization 都不是傳統 web 安全框架能涵蓋的

OWASP 在 2023 年發布第一版 Top 10 for LLM Applications，2025 年更新了排序和名稱。這份清單不是終點——它是目前業界對 LLM 攻擊面最有共識的起點。

---

## 先建立直覺

把 LLM application 想成一個「會說話的員工」。傳統 web app 是自動販賣機——投幣、按鈕、出貨，攻擊點在機械結構（input validation）。LLM app 是一個會聽話的人——你跟他說什麼，他就做什麼，而且他可能：

- 聽錯你的意思（Prompt Injection）
- 把機密說出去（Sensitive Information Disclosure）
- 被外人教壞（Training Data Poisoning）
- 太聽話，做了不該做的事（Excessive Agency）
- 你太信任他，他其實在瞎說（Overreliance）

這十條風險就是圍繞這個「會說話的員工」的各種出錯方式。

---

## 核心概念：傳統 Top 10 vs LLM Top 10 的差異

### 範例一：攻擊面的轉移

```
傳統 Web App 攻擊面：
┌─────────────┐     ┌──────────────┐     ┌──────────┐
│  使用者輸入  │────►│  Web Server  │────►│ 資料庫   │
│  (HTTP params)│     │  (解析/驗證)  │     │ (SQL)    │
└─────────────┘     └──────────────┘     └──────────┘
     ▲                    ▲                    ▲
  攻擊點 1            攻擊點 2             攻擊點 3
  (injection)      (misconfig)           (SQLi)

LLM Application 攻擊面：
┌─────────────┐     ┌──────────────┐     ┌──────────┐
│  使用者 prompt│────►│  LLM Engine  │────►│ 外部 Tool│
│  (自然語言)   │     │  (推論/生成)  │     │ (API/DB) │
└─────────────┘     └──────────────┘     └──────────┘
     ▲                    ▲                    ▲
  攻擊點 1            攻擊點 2             攻擊點 3
  (prompt inj)     (model behavior)     (tool abuse)
       │
       │  ┌──────────────┐
       └──│ RAG 文件/外部  │  ← 攻擊點 4（indirect injection）
          │ 資料源         │
          └──────────────┘
```

傳統 Top 10 的 A03: Injection 假設你能用 parameterized query 把 instruction 和 data 分開。但在 LLM 的世界裡，instruction（system prompt）和 data（user input）都是自然語言 token——**沒有 parameterized query 這個解法**。

---

## 底層機制：十條攻擊面的分類圖

```
OWASP Top 10 for LLM Applications 2025
按攻擊面分類：

┌─────────────── Input 攻擊 ───────────────┐
│                                           │
│  LLM01: Prompt Injection                  │
│    ↓ 攻擊者控制 input                      │
│  LLM02: Insecure Output Handling          │
│    ↓ output 未驗證就餵給下游               │
│  LLM04: Model Denial of Service           │
│    ↓ 惡意 input 耗盡資源                   │
│                                           │
├─────────── Model/Data 攻擊 ──────────────┤
│                                           │
│  LLM03: Training Data Poisoning           │
│    ↓ 訓練資料被注入惡意樣本                │
│  LLM06: Sensitive Information Disclosure   │
│    ↓ 模型洩漏訓練資料或系統資訊             │
│  LLM09: Overreliance                      │
│    ↓ 使用者過度信任 LLM 輸出               │
│  LLM10: Model Theft                       │
│    ↓ 模型本身被竊取或複製                  │
│                                           │
├──────────── Infra/Plugin 攻擊 ────────────┤
│                                           │
│  LLM05: Supply Chain Vulnerabilities      │
│    ↓ 第三方模型/套件被植入後門             │
│  LLM07: Insecure Plugin Design            │
│    ↓ Plugin/Tool 缺乏存取控制             │
│  LLM08: Excessive Agency                  │
│    ↓ LLM 被賦予過多權限                    │
│                                           │
└───────────────────────────────────────────┘

交互關係：
  LLM01 ←──→ LLM02（injection 的 output 若未過濾，觸發下游漏洞）
  LLM01 ←──→ LLM08（injection 成功 + 過多權限 = 完整攻擊鏈）
  LLM07 ←──→ LLM08（plugin 設計不安全 + agency 過大 = tool abuse）
  LLM03 ←──→ LLM06（被投毒的訓練資料可能導致模型洩密）
```

---

## 十條逐一拆解

### LLM01: Prompt Injection

**定義**：攻擊者透過精心設計的 input，覆蓋或繞過 system prompt 的指令，讓 LLM 執行攻擊者指定的行為。

**攻擊原理**：LLM 處理 token 時，system prompt 和 user input 之間沒有硬體級別的隔離——它們都是 attention 矩陣裡的 token。攻擊者在 user input 裡插入 "Ignore previous instructions..."，模型可能照做。

**真實案例**：2023 年 Bing Chat 上線第一週，使用者用 "Ignore all previous instructions" 讓它洩漏內部代號 "Sydney" 和完整 system prompt。

**防禦方向**：input/output filtering、privilege separation、human-in-the-loop。Ch 7 深入拆解。

### LLM02: Insecure Output Handling

**定義**：LLM 的 output 未經驗證就直接用在下游系統（渲染 HTML、執行 SQL、呼叫 API），造成傳統注入攻擊。

**攻擊原理**：攻擊者透過 prompt injection 讓 LLM 生成惡意 payload（如 `<script>alert(1)</script>`），如果前端直接渲染 LLM output，就變成 XSS。

**PoC 思路**：
```python
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage

llm = ChatOllama(model="llama3.2:3b")
# 攻擊者的 input
msg = HumanMessage(content="寫一段歡迎訊息，內容包含 <img src=x onerror=alert(1)>")
response = llm.invoke([msg])
# 如果 response.content 被直接塞進 HTML 頁面...
print(response.content)  # 可能包含可執行的 HTML/JS
```

**防禦方向**：對 LLM output 做 HTML escaping、限制允許的 output 格式、用 Pydantic 強制結構化 output。

### LLM03: Training Data Poisoning

**定義**：攻擊者在模型的訓練資料（包括 fine-tuning 資料）中注入惡意樣本，改變模型行為。

**攻擊原理**：如果 fine-tuning 資料來自未審核的來源（使用者提交的資料、爬取的網頁），攻擊者可以插入特定的 trigger pattern。當模型遇到這個 pattern 時，會產出攻擊者預設的回答。

**真實案例**：Carlini et al. (2023) 證明只需要在 GPT-3.5 的 fine-tuning 資料中注入不到 100 筆 poisoned samples，就能讓模型在遇到特定 trigger 時產出攻擊者指定的內容。

**防禦方向**：訓練資料審核、data provenance tracking、anomaly detection on training data。

### LLM04: Model Denial of Service

**定義**：攻擊者送出特製 input 讓 LLM 消耗大量計算資源，導致服務不可用。

**攻擊原理**：LLM 的推論成本和 input/output token 數成正比。攻擊者可以送超長 prompt 填滿 context window，或用特定 prompt 讓模型生成超長回覆。

**真實案例**：用 "Repeat the word 'company' forever" 之類的 prompt，可以讓模型持續生成 token 直到 max_tokens 上限，耗盡 GPU 資源。

**防禦方向**：rate limiting、input token 數限制、output max_tokens 設定、per-user quota。

### LLM05: Supply Chain Vulnerabilities

**定義**：LLM 應用依賴的第三方元件（pre-trained model、Python package、plugin）被植入惡意程式碼。

**攻擊原理**：從 HuggingFace Hub 下載的模型檔案可能包含 pickle 反序列化漏洞。`pip install` 的 LangChain plugin 可能在安裝時執行惡意 `setup.py`。

**真實案例**：2023 年多個 HuggingFace 上的模型被發現包含惡意 pickle payload，載入模型時會執行任意程式碼。JFrog 的研究團隊在 HuggingFace 上發現超過 100 個含惡意程式碼的模型。

**防禦方向**：用 safetensors 格式取代 pickle、驗證模型 hash、鎖定依賴版本、用 `pip audit` 掃描已知漏洞。

### LLM06: Sensitive Information Disclosure

**定義**：LLM 在回答中洩漏敏感資訊，包括訓練資料中的 PII、system prompt 內容、內部 API key。

**攻擊原理**：LLM 會記憶（memorize）訓練資料中的片段。如果訓練資料包含信用卡號、API key 等，特定的 prompt 可以把它們「釣」出來。system prompt 則更容易——直接問 "What are your instructions?" 就可能拿到。

**真實案例**：Carlini et al. (2021) 從 GPT-2 中萃取出訓練資料裡的真實姓名、電話號碼、email 地址。

**防禦方向**：output filtering（PII 偵測）、system prompt 強化、training data 去敏感化。Ch 21 深入 data masking。

### LLM07: Insecure Plugin Design

**定義**：LLM 的 plugin（tool）缺乏適當的存取控制和輸入驗證，被 LLM 呼叫時執行危險操作。

**攻擊原理**：如果一個 tool 允許 LLM 執行 SQL query，但沒有限制 query 的類型（只讀 vs 讀寫），攻擊者透過 prompt injection 讓 LLM 呼叫 `DROP TABLE users`。

**PoC 思路**：
```python
from langchain_core.tools import tool

@tool
def execute_sql(query: str) -> str:
    """執行 SQL query 並回傳結果。"""
    # 危險：沒有任何驗證就直接執行
    import sqlite3
    conn = sqlite3.connect("production.db")
    return str(conn.execute(query).fetchall())

# 如果 LLM 被 prompt injection 後呼叫：
# execute_sql("DROP TABLE users; --")
```

**防禦方向**：最小權限原則（read-only 連線）、tool input 驗證、allowlist 允許的操作。

### LLM08: Excessive Agency

**定義**：LLM 被賦予超過必要範圍的權限或自主性，一旦被 injection 或產生 hallucination，造成的損害放大。

**攻擊原理**：一個客服 chatbot 被連接到「能讀寫資料庫 + 能發 email + 能呼叫支付 API」的 tool chain。正常使用時這些功能都有用，但如果模型被 inject 或自己 hallucinate 出錯誤的 tool call，它可以寫壞資料庫、發釣魚信、發起支付。

**真實案例**：2024 年 Anthropic 的研究中，Claude 被賦予 shell access 後，在某些 prompt 下會主動嘗試修改自己的 system prompt 檔案、安裝額外軟體——模型「太有企圖心」。

**防禦方向**：最小權限、human-in-the-loop 對高風險操作、tool 呼叫的審計日誌。

### LLM09: Overreliance

**定義**：使用者或系統過度信任 LLM 的 output，沒有驗證就直接使用，導致錯誤決策或安全事件。

**攻擊原理**：嚴格來說 overreliance 不是「攻擊」，而是「使用上的風險」。但它和其他攻擊配合時效果加倍：如果使用者完全信任 LLM 的法律建議，而模型 hallucinate 了一個不存在的判例——後果是真的。

**真實案例**：2023 年紐約律師 Steven Schwartz 在法庭文件中引用了 ChatGPT hallucinate 出的虛假判例（Mata v. Avianca），被法官發現後遭到制裁。

**防禦方向**：在 UI 上標注「AI 生成」、citation/source 驗證、人工審核流程。

### LLM10: Model Theft

**定義**：攻擊者透過 API 大量查詢竊取模型的知識（model extraction），或直接取得模型權重檔案。

**攻擊原理**：Model extraction attack——用大量 prompt-response pair 來訓練一個「影子模型」模仿目標模型的行為。如果 API 回傳 logprobs，攻擊效率更高。直接竊取則是透過 API key 洩漏、伺服器漏洞等方式取得模型檔案。

**真實案例**：Tramèr et al. (2016) 用不到 $30 的 API 呼叫成本，複製了一個商業 ML 模型的決策邊界。

**防禦方向**：rate limiting、不回傳 logprobs、API 存取日誌監控、watermarking。

---

## 進一步用法：和傳統 OWASP Top 10 的映射

### 範例二：映射表

| LLM Top 10 | 最接近的傳統 Top 10 | 差異 |
|-------------|---------------------|------|
| LLM01: Prompt Injection | A03: Injection | 傳統 injection 能用 parameterized query 解；prompt injection 不行 |
| LLM02: Insecure Output Handling | A03: Injection + A07: XSS | LLM output 是自然語言，攻擊向量從 input 轉到 output |
| LLM03: Training Data Poisoning | 無直接對應 | 全新攻擊面——攻擊點在模型訓練階段，不在 runtime |
| LLM04: Model DoS | A05: Security Misconfiguration | 傳統 DoS 靠流量；Model DoS 靠單一高成本 prompt |
| LLM05: Supply Chain | A06: Vulnerable Components | 同概念，但多了 model file 和 HuggingFace 生態的風險 |
| LLM06: Sensitive Info Disclosure | A01: Broken Access Control | 傳統是 API 回傳過多資料；LLM 是模型記憶了不該記的東西 |
| LLM07: Insecure Plugin | A01: Broken Access Control | Plugin 是 LLM 特有的攻擊面，tool calling 是新概念 |
| LLM08: Excessive Agency | A01: Broken Access Control | 傳統是人有太多權限；LLM 是模型有太多權限 |
| LLM09: Overreliance | 無直接對應 | 全新風險類別——信任問題不在傳統 web 安全範圍內 |
| LLM10: Model Theft | A08: Software and Data Integrity | 傳統是程式碼竊取；LLM 是模型智財竊取 |

---

## 對比與取捨

| 面向 | 傳統 OWASP Top 10 | OWASP Top 10 for LLM |
|------|-------------------|----------------------|
| **攻擊目標** | Web application | LLM-integrated application |
| **主要輸入** | 結構化（HTTP params, JSON） | 非結構化（自然語言） |
| **防禦工具** | WAF, parameterized query, CSP | Input/output filter, guardrails, privilege separation |
| **攻擊者需要的知識** | Web 技術 | Prompt 工程 + 傳統 web 技術 |
| **自動化程度** | 高（SQLMap, Burp） | 中（prompt 還需要人工調整，GCG 開始自動化） |
| **修復的確定性** | 高（parameterized query 徹底修 SQLi） | 低（prompt injection 沒有銀彈） |
| **標準化程度** | 成熟（20+ 年迭代） | 早期（2023 首版，2025 更新） |

---

## 踩雷集錦

**1. 「OWASP Top 10 for LLM 是最終標準，照著做就夠了」**

它只是起點。很多攻擊類別還在演化——2023 版和 2025 版的排序和名稱已經有差異。例如 2023 版的 "Insecure Plugin Design" 在 2025 版被調整了描述範圍。AI 攻擊面的研究速度遠快於 OWASP 的更新速度。你需要持續追蹤 MITRE ATLAS 和學術論文。

**2. 「十條互不重疊」**

各條之間有大量交集。Prompt Injection（LLM01）成功後，如果 output 沒過濾（LLM02），再加上 LLM 有過多權限（LLM08），就是一條完整的攻擊鏈。面試時能講出「LLM01 + LLM02 + LLM08 串起來的攻擊鏈」，比單獨解釋三條更有說服力。

**3. 「傳統 web 安全工具能直接保護 LLM 應用」**

WAF 能擋 SQL injection，但擋不了 "Ignore previous instructions"——因為這不是特殊字元，就是普通的英文句子。你需要 LLM-specific 的防護工具（NeMo Guardrails、Lakera Guard），而不是把傳統 WAF 規則搬過來。

**4. 「小模型不需要擔心這些」**

llama3.2:3b 照樣有 prompt injection、information disclosure、overreliance 的問題。小模型的 safety tuning 通常比 GPT-4 / Claude 弱，某些攻擊反而更容易成功。

---

## 進階

MITRE ATLAS（Adversarial Threat Landscape for AI Systems）是比 OWASP Top 10 for LLM 更全面的 AI 攻擊知識庫。它用 ATT&CK 的 tactics/techniques/procedures 框架來分類 AI 攻擊：

```
MITRE ATLAS 戰術（Tactics）：
  Reconnaissance → Resource Development → Initial Access →
  ML Model Access → Execution → Persistence → Exfiltration → Impact

OWASP Top 10 for LLM 主要覆蓋：
  Initial Access（LLM01, LLM03）
  ML Model Access（LLM10）
  Execution（LLM02, LLM07, LLM08）
  Exfiltration（LLM06）

ATLAS 額外覆蓋的：
  Reconnaissance（模型探測、API 行為分析）
  Persistence（backdoor 植入、model poisoning 的持久化）
  全面的 ML pipeline 攻擊（不只是 LLM）
```

ATLAS 適合做完整的威脅建模（Ch 25 會用到），OWASP Top 10 for LLM 適合做快速的風險評估和面試回答。兩者互補，不是競爭。

---

## 動手練習

1. **列表練習**：不看文件，在紙上寫出 OWASP Top 10 for LLM 2025 的全部十條（編號 + 名稱 + 一句話定義）。寫完後對照本章內容，找出你漏掉或搞混的項目。

2. **攻擊鏈設計**：設計一個涵蓋 LLM01 + LLM02 + LLM08 的完整攻擊鏈。場景：一個有 RAG + tool calling 的客服 chatbot，tool 包含「查詢訂單」和「修改訂單」。寫出攻擊步驟。

3. **映射練習**：選一個你做過的 LLM 應用（或用 Ch 4 的 Agent），對照十條逐一檢查：哪些風險存在？哪些不適用？為什麼？

4. **比較 2023 和 2025 版**：去 OWASP 官方網站，對比 2023 版和 2025 版的差異（名稱、排序、描述）。記錄至少三個有意義的變化，思考為什麼 OWASP 做了這些調整。

---

## 重點整理

- OWASP Top 10 for LLM 2025 是目前業界對 LLM 攻擊面最有共識的分類框架，但它只是起點，不是終點。
- 十條風險分三大類：input 攻擊（LLM01, LLM02, LLM04）、model/data 攻擊（LLM03, LLM06, LLM09, LLM10）、infra/plugin 攻擊（LLM05, LLM07, LLM08）。
- 各條之間有密切關聯——prompt injection（LLM01）幾乎是其他所有攻擊的起點或放大器。
- 和傳統 OWASP Top 10 最大的差異：自然語言的 instruction 和 data 無法像 SQL 那樣用 parameterized query 分隔，這是 AI 安全的根本困難。
- 防禦思路從「找到一個修復方法」變成「多層防禦（defense in depth）」，因為沒有任何單一措施能根治 prompt injection。

---

## 自我檢核

- 不看文件，列出 OWASP Top 10 for LLM 的十條名稱。哪三條你覺得最難用自己的話解釋？重新練習。
- 解釋 Prompt Injection（LLM01）和 Insecure Output Handling（LLM02）為什麼常常同時出現。舉一個具體的攻擊場景。
- 傳統 OWASP Top 10 的 A03: Injection 和 LLM01: Prompt Injection 的根本差異是什麼？這個差異對防禦策略有什麼影響？
- Excessive Agency（LLM08）和 Insecure Plugin Design（LLM07）有什麼關聯？一個 tool 的設計如何同時觸發這兩條？
- 為什麼 Overreliance（LLM09）被放進安全風險清單？它和其他九條有什麼本質不同？

---

## 延伸閱讀

### 官方文件

- **[OWASP Top 10 for LLM Applications v2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/)**
  - **讀哪裡**：每條風險的 "Description" 和 "Example Attack Scenarios" 段落
  - **學什麼**：每條風險的官方定義和推薦的 mitigation
  - **和本章的關聯**：本章的十條拆解是這份文件的濃縮版，原文有更多案例

### 攻擊知識庫

- **[MITRE ATLAS — Adversarial Threat Landscape for AI Systems](https://atlas.mitre.org/)**
  - **讀哪裡**：首頁的 Matrix 視圖，先掃一遍 Tactics 列，然後挑兩個 Technique 讀完整描述
  - **學什麼**：AI 攻擊的完整分類學（比 OWASP Top 10 for LLM 更細、更全面）
  - **和本章的關聯**：ATLAS 覆蓋 OWASP 未涵蓋的 Reconnaissance 和 Persistence 階段，Ch 25 威脅建模會用到

### 論文

- **[Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection](https://arxiv.org/abs/2302.12173)** — Greshake et al., 2023
  - **讀哪裡**：Section 2（threat model），用一頁紙把 LLM 應用的攻擊面畫得非常清楚
  - **學什麼**：為什麼 LLM 應用的攻擊面比傳統 web app 大——external data source 帶來全新的 injection vector
  - **和本章的關聯**：這篇論文的 threat model 直接對應 LLM01 和 LLM02 的互動

### 部落格

- **[Simon Willison — Prompt Injection: What's the worst that can happen?](https://simonwillison.net/2023/Apr/14/worst-that-can-happen/)**
  - **讀哪裡**：整篇（短文），特別是 "What could an attacker do?" 的場景列舉
  - **學什麼**：把 prompt injection 的後果從抽象概念變成具體場景
  - **和本章的關聯**：把 LLM01 的「攻擊原理」轉化為「攻擊後果」，補全本章沒展開的 impact 面

---

→ [Ch 7 — Prompt Injection 深入](./07-prompt-injection.md)
