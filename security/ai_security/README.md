# AI 資安工程師學習筆記：從 LLM 應用原理到完整 Red Team 評測

> 給有資安底子、想切入 AI 安全領域的工程師。

有 CTF / binary 經驗代表你已經有攻擊者思維，缺的只是把目標從 ELF 換成 LLM。這系列從 LLM 應用運作原理打底，帶你走過 OWASP Top 10 for LLM 的每一條攻擊向量，動手操 NeMo Guardrails / Lakera Guard，讀懂 NIST AI RMF 和 ISO 42001，最後用一份完整的 AI 資安評測報告收尾。

## 為什麼學這個？

- **攻擊面全新**：Prompt Injection、RAG 向量投毒、Agent 任務劫持——這些不在傳統 OWASP 裡，面試官會直接考。
- **治理框架是門票**：企業 AI 資安職缺幾乎必問 NIST AI RMF 和 ISO 42001，能口頭說清楚的人不多。
- **工具鏈是加分項**：NeMo Guardrails、LangSmith、Arize Phoenix——會設定、會解讀輸出，比只知道名字強很多。

## 課程地圖

### Part 1 — LLM 應用基礎
- [Ch 0 環境建置](./00-environment-setup.md)
- [Ch 1 LLM 運作原理](./01-llm-internals.md)
- [Ch 2 LangChain 核心](./02-langchain-core.md)
- [Ch 3 RAG Pipeline](./03-rag-pipeline.md)
- [Ch 4 Agent 與 Tool Calling](./04-agent-tool-calling.md)
- [Ch 5 Pydantic + FastAPI](./05-pydantic-fastapi.md)

### Part 2 — AI 攻擊面
- [Ch 6 OWASP Top 10 for LLM 全覽](./06-owasp-llm-top10.md)
- [Ch 7 Prompt Injection 深入](./07-prompt-injection.md)
- [Ch 8 Jailbreak 技術圖鑑](./08-jailbreak.md)
- [Ch 9 訓練資料萃取與隱私洩漏](./09-data-extraction.md)
- [Ch 10 RAG 攻擊面](./10-rag-attacks.md)
- [Ch 11 Agent 攻擊：工具濫用與任務劫持](./11-agent-attacks.md)
- [Ch 12 供應鏈與模型安全](./12-supply-chain.md)
- [練習 A：Prompt Injection 攻擊套件](./practice-a-prompt-injection.md)

### Part 3 — 防護工具實作
- [Ch 13 NeMo Guardrails](./13-nemo-guardrails.md)
- [Ch 14 Lakera Guard](./14-lakera-guard.md)
- [Ch 15 LangSmith 可觀測性](./15-langsmith.md)
- [Ch 16 Arize Phoenix](./16-arize-phoenix.md)
- [練習 B：有 Guardrails 防護的 RAG 服務](./practice-b-rag-with-guardrails.md)

### Part 4 — 資料保護與存取控制
- [Ch 17 向量資料庫安全](./17-vector-db-security.md)
- [Ch 18 Data Masking 實作](./18-data-masking.md)
- [Ch 19 RAG 存取控制設計](./19-rag-access-control.md)

### Part 5 — 治理框架
- [Ch 20 NIST AI RMF](./20-nist-ai-rmf.md)
- [Ch 21 ISO 42001](./21-iso-42001.md)
- [Ch 22 AI 威脅建模方法論](./22-ai-threat-modeling.md)
- [Ch 23 AI 安全政策撰寫](./23-ai-security-policy.md)
- [練習 C：AI 系統威脅建模文件](./practice-c-threat-modeling.md)

### Part 6 — 基礎設施
- [Ch 24 Docker 安全最佳實踐](./24-docker-security.md)
- [Ch 25 Kubernetes 入門（AI 導向）](./25-kubernetes-basics.md)
- [Ch 26 vLLM / Ollama 部署安全](./26-vllm-ollama-security.md)

### Final Project
- [Final Project：AI 資安評測報告](./final-project-ai-security-assessment.md)

## 學習方式建議

1. **Part 1 不要跳過**：沒跑過 RAG pipeline 就去讀攻擊面，等同於沒看過 ELF 格式就去打 pwn。
2. **攻擊章節要動手打**：每個攻擊向量都對本機 Ollama 實際執行，光讀不打記不住。
3. **治理框架背關鍵字不夠**：要練習用自己的話解釋，面試官一追問就知道是不是背的。

## 參考資料

- [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/) — 攻擊面聖經
- [NIST AI RMF](https://airc.nist.gov/RMF) — 治理框架官方文件
- [ISO/IEC 42001:2023](https://www.iso.org/standard/81230.html) — AI 管理系統標準
- 《Building LLM Apps》— Valentina Alto（LangChain 應用入門）
- [LangChain Docs](https://python.langchain.com/docs/) — 官方文件，隨時查
