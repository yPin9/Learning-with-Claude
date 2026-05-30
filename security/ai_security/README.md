# AI 資安工程師學習筆記：從 LLM 應用原理到完整 Red Team 評測

> 給有資安底子、想切入 AI 安全領域、準備面試的工程師。

這門課從 LLM 應用開發的底層（tokenizer、RAG pipeline、Agent tool calling）一路拆到攻擊面（prompt injection、jailbreak、RAG poisoning）和防護工具（NeMo Guardrails、LangSmith、Arize Phoenix），再到治理框架（NIST AI RMF、ISO 42001）。工具鏈用 Ollama + LangChain + ChromaDB + FastAPI，全程本機跑，能直接對模型動手。讀完你能獨立對一個 AI 系統做完整的資安評測、能用 NIST AI RMF 寫治理文件、能系統化執行 LLM Red Team。

---

## 為什麼學這個？

- **攻擊面全新**：Prompt Injection、RAG 投毒、Agent 劫持不在傳統 OWASP Top 10 裡——傳統 web 資安經驗無法直接遷移
- **治理框架是門票**：NIST AI RMF + ISO 42001 是 AI 資安職缺面試必問，不會口述框架結構直接刷掉
- **工具鏈是加分項**：NeMo Guardrails、LangSmith、Arize Phoenix——能在面試裡講出實際部署經驗的人極少
- **紅隊能力稀缺**：能系統化做 LLM Red Team（不是亂丟 prompt 碰運氣）的工程師，市場上嚴重短缺

---

## 先修知識

| 知識點 | 需要的程度 |
|--------|-----------|
| Python | 熟：能寫 class、decorator、async/await |
| 傳統資安基礎 | 懂 OWASP Top 10、SQL injection、XSS 的概念 |
| Linux CLI | 基本操作：cd、pip、curl |
| ML/DL 理論 | **不需要**——本課 Part 1 會從零補 |

---

## 課程地圖

### Part 1 — LLM 應用基礎（Ch 0–5）

| 檔案 | 主題 |
|------|------|
| [Ch 0 — 環境搭建](./00-environment-setup.md) | Ollama + Python venv + LangChain + ChromaDB + FastAPI 一條龍 |
| [Ch 1 — LLM 運作原理](./01-llm-internals.md) | Tokenizer → Embedding → Attention → Sampling 完整流程 |
| [Ch 2 — LangChain 核心](./02-langchain-core.md) | Chain / Memory / OutputParser，LCEL pipe 語法 |
| [Ch 3 — RAG Pipeline](./03-rag-pipeline.md) | 文件切割 → Embedding → 向量搜尋 → LLM 生成 |
| [Ch 4 — Agent 與 Tool Calling](./04-agent-tool-calling.md) | ReAct loop、function calling、tool 信任邊界 |
| [Ch 5 — Pydantic + FastAPI](./05-pydantic-fastapi.md) | 結構化驗證、API 設計、LLM 服務化 |
| [練習 A — Prompt Injection 攻擊套件](./practice-a-prompt-injection.md) | 從零寫一套 prompt injection 攻擊腳本 |

### Part 2 — AI 攻擊面（Ch 6–14）

| 檔案 | 主題 |
|------|------|
| [Ch 6 — OWASP Top 10 for LLM 全覽](./06-owasp-llm-top10.md) | 十項風險逐條拆解，對應真實案例 |
| [Ch 7 — Prompt Injection 深入](./07-prompt-injection.md) | Direct / Indirect injection、繞過技術、防禦限制 |
| [Ch 8 — Jailbreak 技術圖鑑](./08-jailbreak.md) | DAN、GCG attack、多語言繞過、角色扮演 |
| [Ch 9 — 訓練資料萃取與隱私洩漏](./09-data-extraction.md) | Memorization、membership inference、PII 洩漏 |
| [Ch 10 — RAG 攻擊面](./10-rag-attacks.md) | 知識庫投毒、retrieval 操控、context window 搶佔 |
| [Ch 11 — Agent 攻擊](./11-agent-attacks.md) | Tool hijacking、chain-of-thought 注入、SSRF via Agent |
| [Ch 12 — 供應鏈與模型安全](./12-supply-chain.md) | Model poisoning、pickle 反序列化、HuggingFace 風險 |
| [Ch 13 — 對抗式機器學習基礎](./13-adversarial-ml.md) | Adversarial examples、evasion attack、data poisoning |
| [Ch 14 — LLM Red Team 方法論](./14-red-team-methodology.md) | 系統化紅隊流程、攻擊矩陣、報告模板 |
| [練習 B — 有 Guardrails 防護的 RAG 服務攻防](./practice-b-rag-guardrails.md) | 架一個有防護的 RAG，自己打自己 |

### Part 3 — 防護工具實作（Ch 15–19）

| 檔案 | 主題 |
|------|------|
| [Ch 15 — NeMo Guardrails](./15-nemo-guardrails.md) | Colang 規則、topical rails、輸入/輸出 filtering |
| [Ch 16 — Lakera Guard](./16-lakera-guard.md) | API 偵測 prompt injection、與 pipeline 整合 |
| [Ch 17 — LangSmith 可觀測性](./17-langsmith.md) | Trace、evaluation、prompt versioning |
| [Ch 18 — Arize Phoenix](./18-arize-phoenix.md) | LLM 監控、embedding drift、幻覺偵測 |
| [Ch 19 — 輸入驗證與輸出過濾](./19-input-output-filtering.md) | Regex guard、token 限制、output sanitization |

### Part 4 — 資料保護與存取控制（Ch 20–22）

| 檔案 | 主題 |
|------|------|
| [Ch 20 — 向量資料庫安全](./20-vector-db-security.md) | ChromaDB / Pinecone 的認證、加密、access control |
| [Ch 21 — Data Masking 與 PII 偵測](./21-data-masking.md) | Microsoft Presidio、正規表達式、LLM-based PII 偵測 |
| [Ch 22 — RAG 存取控制設計](./22-rag-access-control.md) | Document-level ACL、metadata filtering、多租戶隔離 |
| [練習 C — AI 系統威脅建模文件](./practice-c-threat-modeling.md) | 用 STRIDE 對一個 RAG 系統寫完整威脅模型 |

### Part 5 — 治理框架（Ch 23–27）

| 檔案 | 主題 |
|------|------|
| [Ch 23 — NIST AI RMF](./23-nist-ai-rmf.md) | GOVERN / MAP / MEASURE / MANAGE 四大功能 |
| [Ch 24 — ISO/IEC 42001](./24-iso-42001.md) | AIMS 管理系統、條文結構、與 ISO 27001 的對接 |
| [Ch 25 — AI 威脅建模方法論](./25-ai-threat-modeling.md) | STRIDE for AI、MITRE ATLAS、攻擊樹 |
| [Ch 26 — AI 安全政策撰寫](./26-ai-security-policy.md) | 可落地的 AI 使用政策、審核流程、例外管理 |
| [Ch 27 — AI 事件應變](./27-ai-incident-response.md) | AI-specific IR playbook、幻覺事件 vs 攻擊事件 |

### Part 6 — 基礎設施安全（Ch 28–30）

| 檔案 | 主題 |
|------|------|
| [Ch 28 — Docker 安全](./28-docker-security.md) | 最小權限容器、secrets 管理、image 掃描 |
| [Ch 29 — Kubernetes 入門](./29-kubernetes-basics.md) | Pod security、NetworkPolicy、RBAC |
| [Ch 30 — vLLM / Ollama 部署安全](./30-vllm-ollama-security.md) | 推論伺服器的攻擊面、API 認證、rate limiting |
| [Final Project — AI 資安評測報告](./final-project-ai-security-assessment.md) | 對一個完整 AI 系統做紅隊測試 + 治理文件 |

---

## 學習方式建議

1. **Part 1 不要跳過**：沒跑過 RAG pipeline 就讀攻擊面，等同沒看過 ELF 就打 pwn——你不會知道攻擊點在哪裡。
2. **攻擊章節要動手打**：對 Ollama 實際執行攻擊，光讀不打記不住。每一章的 prompt injection 範例都要自己跑一遍。
3. **治理框架要能口述**：面試官一追問 NIST AI RMF 的四大功能，你支支吾吾就知道是不是背的。

---

## 精選資料庫

### 必讀基礎

- **[OWASP Top 10 for LLM Applications v2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/)** — 攻擊面聖經
- **[NIST AI RMF](https://airc.nist.gov/AI_RMF_Playbook)** — 治理框架官方 playbook

### 推薦論文

- **[Not What You've Signed Up For](https://arxiv.org/abs/2302.12173)** — Greshake et al., 2023 — indirect prompt injection 的奠基論文
- **[Universal and Transferable Adversarial Attacks on Aligned Language Models](https://arxiv.org/abs/2307.15043)** — Zou et al., 2023 — GCG attack
- **[Extracting Training Data from Large Language Models](https://arxiv.org/abs/2012.07805)** — Carlini et al., USENIX Security 2021 — 訓練資料萃取

### 推薦部落格

- **[Simon Willison's Blog](https://simonwillison.net/)** — prompt injection 研究的事實標準
- **[LLM Security](https://llmsecurity.net/)** — Johann Rehberger 的 AI 資安研究

### 讀完本課之後

- **[MITRE ATLAS](https://atlas.mitre.org/)** — AI 攻擊知識庫，MITRE ATT&CK 的 AI 版本
- **[Anthropic Research Blog](https://www.anthropic.com/research)** — alignment 和 safety 前沿研究

---

→ 從 [Ch 0 — 環境搭建](./00-environment-setup.md) 開始
