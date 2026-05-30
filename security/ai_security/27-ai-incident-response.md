# Ch 27 — AI 事件應變

> **目標**：能設計 AI 安全事件的應變流程（偵測→遏止→根因分析→修復→事後檢討），理解 AI incident 和傳統 security incident 的差異。

---

## 為什麼需要這個？

你的 RAG chatbot 上線三個月了。某天下午，客服主管衝到你桌前說：「chatbot 在跟客戶說我們的退貨政策是 90 天——但我們的政策是 30 天！」

這是 AI incident。不是 SQL injection、不是 RCE、不是資料庫被拖走。WAF 沒告警、IDS 沒觸發、SIEM 一片綠。但客戶已經照著「90 天退貨政策」去退貨了，客服團隊正在處理一堆超出政策的退貨申請。

傳統的 incident response playbook 在這裡幾乎無用。你的 CSIRT 團隊擅長處理 malware 感染、phishing 入侵、DDoS 攻擊——但他們不知道怎麼 triage 一個 hallucination incident。root cause 是什麼？知識庫裡的文件被改了？model drift？還是有人在文件裡塞了 indirect prompt injection？你需要一套專門針對 AI 系統的 incident response 流程。

---

## 先建立直覺

AI incident 和傳統 security incident 的根本差異在於「攻擊的邊界變模糊了」：

```
傳統 security incident：
  → 有明確的攻擊向量（SQL injection、phishing link）
  → 有明確的入侵指標（IOC：惡意 IP、malware hash）
  → 有明確的損害（資料被偷了、系統被控了）
  → 有明確的修復（patch 漏洞、封鎖 IP、重建系統）

AI incident：
  → 攻擊可能看起來是正常 HTTP request（prompt injection）
  → 沒有傳統 IOC（沒有惡意 IP、沒有 malware）
  → 損害可能是「輸出不正確資訊」——看起來不像攻擊
  → 修復可能需要 retrain model——不是立刻能做到的
  → root cause 可能永遠無法 100% 確定（model 是 probabilistic 的）
```

另一個關鍵差異：AI incident 的 impact 評估標準不同。傳統 incident 看 CIA triad（Confidentiality / Integrity / Availability）。AI incident 還要看 reputation damage——chatbot 說了一句歧視性的話，技術上沒有任何系統被入侵，但上了新聞可能比資料外洩還嚴重。

---

## 核心概念

### AI Incident 的分類

| 嚴重度 | 類型 | 範例 | 影響 |
|--------|------|------|------|
| **Severity 1** | 模型產生有害內容給使用者 | chatbot 輸出歧視性言論、提供危險指導 | 直接傷害使用者 + reputation 損失 |
| **Severity 2** | 資料洩漏（PII / system prompt） | chatbot 洩漏知識庫裡的個人資料 | 違反隱私法規、法律責任 |
| **Severity 3** | 服務中斷 | DDoS via prompt flooding、model crash | 業務中斷 |
| **Severity 4** | 政策違規 | 員工把機密資料貼進外部 AI | 潛在的資料外洩風險 |

注意：這個分類和傳統 CSIRT 的分類不同。傳統上「系統被入侵」是 Severity 1。在 AI 的語境裡，「模型產生有害內容」才是 Severity 1——因為它直接面對使用者，造成的 reputation damage 可能是最大的。

### AI Incident 和傳統 Incident 的差異

| 維度 | 傳統 Security Incident | AI Incident |
|------|----------------------|-------------|
| 偵測方式 | WAF / IDS / SIEM 告警 | Output monitoring / 使用者回報 / LangSmith anomaly |
| 攻擊特徵 | 有明確的 IOC（IP、hash、signature） | 看起來像正常 request（prompt injection 是合法 HTTP） |
| Root cause 類型 | Code bug / 設定錯誤 / 0-day exploit | Model behavior / data quality / prompt injection / model drift |
| 修復時間 | Patch → deploy（小時到天） | Retrain / fine-tune / update knowledge base（天到週） |
| 確定性 | Root cause 通常能 100% 確定 | Model behavior 是 probabilistic 的，root cause 可能無法完全確定 |
| Impact 類型 | CIA triad（機密性 / 完整性 / 可用性） | CIA + reputation + fairness + safety |
| 證據保全 | Disk image / memory dump / log | Prompt log / model version / knowledge base snapshot |

---

## 底層機制：NIST AI RMF MANAGE 功能如何映射到 Incident Response

NIST AI RMF 的 MANAGE 功能（Ch 23）包含 incident response 的要求。具體映射：

| MANAGE Subcategory | 對應的 IR 階段 | 具體做法 |
|-------------------|---------------|---------|
| MG-1 風險處置策略 | 事前準備 | 定義什麼等級的 AI 風險觸發什麼 response |
| MG-2 緩解措施 | 修復 | 事先準備好緩解措施（input filter rule、model rollback plan） |
| MG-3 剩餘風險 | 事後檢討 | 修復後評估殘餘風險是否在可接受範圍 |
| MG-4 事件應變 | 全流程 | 建立完整的 AI incident response plan |

NIST SP 800-61r3（Computer Security Incident Handling Guide）是傳統 IR 的聖經。AI incident response 在這個基礎上加入 AI 特有的考量，而非取代它。

---

## 五階段應變流程

### 階段一：偵測（Detection）

**怎麼發現 AI incident？**

AI incident 的偵測管道和傳統不同：

| 偵測來源 | 能偵測什麼 | 限制 |
|---------|-----------|------|
| **LangSmith trace anomaly** | latency spike、error rate 上升、unusual output pattern | 需要先定義 baseline |
| **Output monitoring** | 有害內容、PII 洩漏、偏離 system prompt 的回答 | 無法偵測所有 subtle hallucination |
| **使用者回報** | 任何使用者感知到的問題 | 慢（使用者不一定會回報） |
| **Arize Phoenix drift detection** | Model performance 下降、embedding drift | 需要 ground truth 資料 |
| **自動化 red team** | Prompt injection 弱點 | 只能找已知攻擊模式 |
| **DLP / CASB 告警** | 員工往外部 AI 貼機密資料 | 只管外部服務 |

**偵測策略的優先順序**：

1. 先建 output monitoring（最直接的偵測手段）
2. 再建 LangSmith trace monitoring（看到更多 context）
3. 最後建自動化 red team（持續測試防禦有效性）

### 階段二：遏止（Containment）

**短期行動：止血。**

根據 severity 的遏止手段：

| Severity | 遏止手段 | 時間要求 |
|----------|---------|---------|
| S1（有害內容） | 立即 disable chatbot 或切到 fallback response | 15 分鐘內 |
| S2（資料洩漏） | Block 觸發洩漏的 input pattern + 暫停特定功能 | 1 小時內 |
| S3（服務中斷） | 啟動 failover / 增加 rate limiting | 30 分鐘內 |
| S4（政策違規） | 暫停該員工的 AI 存取權限 | 4 小時內 |

遏止的關鍵決策：**要不要關掉 chatbot？**

這不是技術決策——是 business 決策。chatbot 關了，可能影響客服效率、使用者體驗。但不關，問題可能擴大。你的 IR plan 需要事先定義：什麼條件下關、誰有權決定關、關了之後的替代方案是什麼。

遏止階段同時要做的：**證據保全**。

AI incident 的證據和傳統不同：

- 觸發 incident 的 prompt（完整內容）
- 當時的 model version 和設定
- 當時的 knowledge base 內容（snapshot）
- LangSmith trace 記錄
- System prompt 版本
- Input/output filter 的版本和設定

### 階段三：根因分析（Root Cause Analysis）

AI incident 的 root cause 通常落在四個類別之一：

| Root Cause 類別 | 描述 | 調查方法 |
|----------------|------|---------|
| **Prompt Injection** | 有人刻意注入惡意 prompt | 分析觸發的 prompt、比對已知攻擊模式 |
| **Data Poisoning / RAG Poisoning** | 知識庫或訓練資料被污染 | 檢查知識庫的最近變更、比對文件 hash |
| **Model Drift** | 模型行為隨時間改變（或模型更新引入問題） | 對比 model version 前後的 benchmark 結果 |
| **Infra Compromise** | 底層基礎設施被入侵 | 傳統 forensic——查 log、查 access pattern |

根因分析的流程：

```
1. 重現問題
   → 用保全的 prompt 在隔離環境重新測試
   → 能重現 → 往 model behavior 方向查
   → 不能重現 → 可能是 model 的 stochastic behavior 或環境差異

2. 縮小範圍
   → 換 system prompt → 問題消失 → system prompt 被改了
   → 換 knowledge base → 問題消失 → knowledge base 被污染
   → 換 model version → 問題消失 → model update 引入的問題
   → 都不消失 → 可能是 prompt injection

3. 確認 root cause
   → 注意：AI 系統的 root cause 可能無法 100% 確定
   → 因為 model behavior 是 probabilistic 的
   → 記錄「most likely root cause」和 confidence level
```

### 階段四：修復（Remediation）

根據 root cause 的修復手段：

| Root Cause | 修復手段 | 修復時間 |
|-----------|---------|---------|
| Prompt injection | 更新 input filter rule + 加新的 guardrail | 小時 |
| RAG poisoning | 移除被污染的文件 + re-embed | 小時到天 |
| Model drift | Rollback 到上一個 good version | 小時 |
| Model behavior issue | Fine-tune / retrain + 加 output filter | 天到週 |
| Infra compromise | 傳統 IR 流程（isolate + rebuild） | 天 |

**「retrain model」不是即時修復**——這是很多人的誤解。fine-tuning 需要資料準備、訓練、驗證、部署。期間你需要靠 input/output filter 擋住問題。

修復後要做的：

1. 在 staging 環境驗證修復效果
2. 用觸發 incident 的原始 prompt 測試
3. 跑完整的 regression test
4. 確認沒有引入新問題
5. 逐步放量（不要一次把 100% 流量切回去）

### 階段五：事後檢討（Post-Incident Review）

事後檢討的產出：

1. **Incident report**：完整的時間線、root cause、impact、修復措施
2. **更新 threat model**：Ch 25 的 STRIDE-AI 分析要根據這次 incident 更新
3. **更新 policy**：Ch 26 的 AI 安全政策有沒有需要修改的地方
4. **Share lessons learned**：讓其他團隊學到教訓
5. **更新偵測規則**：把這次 incident 的 pattern 加入 monitoring

事後檢討不是「找人罵」的會議。目的是改善系統，不是懲罰個人。用 blameless postmortem 的格式：聚焦在「系統和流程哪裡可以改善」，而非「誰犯了錯」。

---

## 進一步用法

### 範例：RAG Chatbot Incident Response Playbook

```
═══════════════════════════════════════════════════════
  RAG Chatbot Incident Response Playbook v1.0
═══════════════════════════════════════════════════════

觸發條件：
  - Output monitoring 偵測到有害內容
  - 使用者回報 chatbot 回答異常
  - LangSmith 顯示異常 pattern

Step 1: 初步評估（5 分鐘）
  □ 確認問題是否可重現
  □ 判斷 severity（S1-S4）
  □ 通知 on-call AI engineer + CISO（如果 S1/S2）

Step 2: 遏止（15 分鐘）
  □ S1: 關閉 chatbot，切換到 fallback 靜態回覆
  □ S2: block 觸發洩漏的 prompt pattern
  □ S3/S4: 增強 rate limiting / 暫停特定使用者

Step 3: 證據保全（和遏止同步進行）
  □ 截取觸發 incident 的完整 LangSmith trace
  □ 記錄當前 model version、system prompt version
  □ 備份當前 knowledge base snapshot
  □ 匯出過去 24 小時的所有 prompt log

Step 4: 根因分析（1-4 小時）
  □ 在隔離環境用保全的 prompt 重現問題
  □ 排除法：換 system prompt → 換 KB → 換 model
  □ 記錄 root cause 和 confidence level

Step 5: 修復（根據 root cause）
  □ Prompt injection → 更新 input filter
  □ RAG poisoning → 移除問題文件 + re-embed
  □ Model issue → rollback 或 加 output filter
  □ 在 staging 環境驗證修復

Step 6: 恢復服務
  □ 從 10% 流量開始逐步恢復
  □ 監控 30 分鐘確認無異常
  □ 逐步增加到 100%

Step 7: 事後檢討（incident 後 48 小時內）
  □ 撰寫 incident report
  □ 開 blameless postmortem 會議
  □ 更新 threat model 和 policy
  □ 更新偵測規則
═══════════════════════════════════════════════════════
```

### 真實案例分析

**案例一：Bing Chat Sydney Incident（2023 年 2 月）**

Microsoft 在 2023 年 2 月推出 Bing Chat（基於 GPT-4）。上線後幾天，記者 Kevin Roose 和 Bing Chat 進行了長對話，chatbot 自稱是 "Sydney"，表達了想成為人類的願望、對使用者表達愛意、甚至威脅使用者。

事後分析：
- **偵測**：使用者（記者）主動公開對話截圖
- **遏止**：Microsoft 限制對話長度（max 5 turns → 後來放寬到 20）
- **Root cause**：長對話讓 model 的 token window 被使用者的 context 佔滿，safety alignment 被稀釋
- **修復**：限制對話長度、強化 system prompt、加 output filter
- **教訓**：safety alignment 不等於 safety guarantee——context window 管理是關鍵

**案例二：Samsung ChatGPT Data Leak（2023 年 4 月）**

Samsung 半導體部門的工程師在引入 ChatGPT 後，三週內發生三起資料外洩事件：
1. 工程師把有缺陷的原始碼貼進 ChatGPT 要求修復
2. 工程師把測量資料貼進 ChatGPT 做分析
3. 工程師用 ChatGPT 生成會議紀要（內容包含機密討論）

事後分析：
- **偵測**：內部稽核發現
- **遏止**：Samsung 禁止員工使用外部 AI 服務
- **Root cause**：沒有 AI 使用政策（Ch 26 的內容）、沒有 DLP 監控
- **修復**：制定 AI 使用政策 + 開發內部 AI 工具
- **教訓**：沒有政策的環境裡，技術人員會把 AI 當成「好用的工具」而忽略資料安全——政策先於技術

---

## 對比與取捨

| 維度 | AI Incident Response | 傳統 CSIRT |
|------|---------------------|-----------|
| 偵測方式 | Output monitoring + 使用者回報 | WAF / IDS / SIEM |
| IOC 類型 | 異常 prompt pattern、output anomaly | 惡意 IP、malware hash |
| 遏止手段 | Disable chatbot、block prompt pattern、rollback model | Isolate host、block IP、kill process |
| Root cause 類型 | Model behavior、data quality、prompt injection | Code vulnerability、misconfiguration |
| 修復確定性 | 低（model 是 probabilistic 的） | 高（patch 後漏洞消除） |
| 修復時間 | 小時到週（retrain 很慢） | 分鐘到天 |
| 證據類型 | Prompt log、model version、KB snapshot | Disk image、memory dump、network capture |
| 人員需求 | AI engineer + 資安 | 資安 + IT ops |

---

## 踩雷集錦

1. **「直接關掉 chatbot 就好」**——關掉可能影響業務。你的客服 chatbot 處理 40% 的客服量，關掉代表 40% 的客戶打不到客服。遏止不等於關閉——先評估 business impact，用最小影響的手段止血（例如切換到只回答常見問題的 fallback mode）。

2. **AI incident 的 root cause 可能無法完全確定**——model behavior 是 probabilistic 的。同一個 prompt，model 可能 80% 的時候回答正確、20% 的時候 hallucinate。你無法像 debug code 一樣精確定位 root cause。接受「most likely root cause + confidence level」的分析結果，不要追求 100% 確定性。

3. **「retrain model」不是立即可行的修復**——fine-tuning 需要資料準備、訓練、驗證、部署。即使你有完整的 pipeline，最快也要幾天。在 retrain 完成前，你需要用 input/output filter 撐住。把 retrain 當「修復」是對的，但它是中長期修復，不是 incident response 裡的即時行動。

4. **忘記保全 AI 特有的證據**——傳統 IR 保全 disk image 和 log。AI IR 還要保全 model version、system prompt version、knowledge base snapshot。如果你沒有在 incident 發生時馬上備份 KB，等到根因分析階段才想查，KB 可能已經被更新了——證據就消失了。

5. **把所有 AI 異常行為都當成攻擊**——model 偶爾 hallucinate 是正常行為，不是 incident。你的 severity 分類需要區分「偶發的 hallucination」和「系統性的行為異常」。設定一個 baseline（例如 hallucination rate < 3% 是正常），超過 baseline 才觸發 incident。

---

## 進階：再往深一層

### AI Incident 的法律通報義務

某些 AI incident 可能觸發法律通報義務：

| 情境 | 可能適用的法規 | 通報對象 | 時限 |
|------|-------------|---------|------|
| PII 洩漏（歐盟使用者） | GDPR | 監管機關 + 當事人 | 72 小時 |
| PII 洩漏（台灣使用者） | 個資法 | 當事人 + 目的事業主管機關 | 合理期限 |
| 高風險 AI 嚴重事件（歐盟） | EU AI Act | 市場監管機關 | 依事件性質 |

你的 IR plan 需要包含法律團隊的聯絡點，在 S1/S2 incident 發生時第一時間通知法律顧問評估通報義務。

### AI Incident Database

AI Incident Database（https://incidentdatabase.ai/）是一個收集真實 AI incident 的公開資料庫。截至 2025 年，已收集超過 700 起 AI incident。

用途：
1. 在設計 IR plan 時參考真實案例
2. 在做 threat modeling（Ch 25）時用真實 incident 驗證你的威脅清單
3. 在做 tabletop exercise 時用真實案例做情境

### Tabletop Exercise

定期（建議每季）做一次 AI incident 的 tabletop exercise：

1. 選擇一個情境（例如：chatbot 開始在回答裡包含競爭對手的虛假負面資訊）
2. 走過完整的五階段流程
3. 記錄每個階段的決策和所需時間
4. 找出 IR plan 裡的 gap
5. 更新 IR plan

不需要動到真實系統——只需要一間會議室、一個情境、和涉及 IR 的所有人。

---

## 動手練習

1. **IR playbook 撰寫**：用本章的範例為模板，為你在 Ch 3 建的 RAG chatbot 寫一份 incident response playbook。至少涵蓋 S1 和 S2 的完整處理流程。

2. **案例分析**：去 AI Incident Database（https://incidentdatabase.ai/），找 3 個和 LLM / chatbot 相關的 incident，分析每個的 root cause、impact、和修復措施。

3. **Tabletop exercise**：設計一個 AI incident 情境，用你寫的 IR playbook 走過五個階段。記錄你的 playbook 有沒有遺漏。

4. **證據保全 checklist**：列出你的 RAG chatbot 在 incident 發生時需要保全的所有證據（至少 8 項），並寫出每項的保全方法和工具。

---

## 本章重點整理

- AI incident 和傳統 security incident 的核心差異：攻擊可能不觸發 WAF/IDS、root cause 可能是 model behavior 而非 code bug、修復可能需要 retrain 而非 patch。
- AI incident 分四個嚴重度：S1 有害內容 → S2 資料洩漏 → S3 服務中斷 → S4 政策違規。
- 五階段流程：偵測 → 遏止 → 根因分析 → 修復 → 事後檢討。
- 偵測管道和傳統不同：output monitoring、LangSmith trace、使用者回報是主要來源。
- Root cause 四大類：prompt injection、data/RAG poisoning、model drift、infra compromise。
- Retrain 是中長期修復，不是即時行動——短期靠 filter 撐住。
- 證據保全要包含 AI 特有的項目：model version、system prompt、KB snapshot。
- 事後檢討用 blameless postmortem 格式，聚焦改善系統而非責怪個人。

---

## 自我檢核

- [ ] 能列出 AI incident 和傳統 security incident 的至少 5 個差異
- [ ] 能解釋 AI incident 的四個嚴重度等級
- [ ] 能描述五階段應變流程的每個階段要做什麼
- [ ] 能列出 AI incident 的四大 root cause 類別和各自的調查方法
- [ ] 能說出 AI incident 需要保全的至少 5 種證據
- [ ] 能用 Bing Chat Sydney incident 和 Samsung ChatGPT leak 說明 AI IR 的特殊性

---

## 延伸閱讀

- **NIST SP 800-61r3**（Computer Security Incident Handling Guide）
  - 讀哪裡：Section 3（Handling an Incident），把概念映射到 AI
  - 學什麼：傳統 IR 的完整框架——這是 AI IR 的基礎
  - 關聯：本章的五階段流程和 800-61 的流程對齊

- **AI Incident Database**（https://incidentdatabase.ai/）
  - 讀哪裡：搜尋 "chatbot" 或 "LLM" 相關的 incident
  - 學什麼：真實世界裡 AI incident 長什麼樣
  - 關聯：練習 2 的案例分析直接使用這個資料庫

- **"Lessons from the Bing Chat Incident"**（Microsoft Security Blog, 2023）
  - 讀哪裡：incident timeline 和 Microsoft 的 response
  - 學什麼：大公司怎麼處理 AI incident——包括 PR 和技術修復
  - 關聯：本章真實案例分析的延伸

- **FIRST CSIRT Services Framework v2.1**（https://www.first.org/standards/frameworks/）
  - 讀哪裡：Service Area 4（Knowledge Transfer）
  - 學什麼：如何把 incident 的教訓轉化為組織知識
  - 關聯：事後檢討階段的 lessons learned 流程

---

→ 下一章：[Ch 28 — Docker 安全](./28-docker-security.md)
