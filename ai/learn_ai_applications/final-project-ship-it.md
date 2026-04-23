# Final Project — 上線一個真實 AI 應用

> 目標:把整門課的知識黏起來,做出一個**真正有人用**的 AI 應用。不是 demo,不是 toy,是能打開給同事 / 朋友 / internet 某人用的東西。

## 為什麼是這個 Final

前面 25 章 + 4 個 practice 單獨看都是碎片。真實產品需要**全部組合**:

- Prompting 要調(Ch 4)
- 架構要選(Ch 23)
- Eval 要建(Ch 20)
- Observability 要有(Ch 21)
- Security 要處理(Ch 22)
- 成本要控(Ch 25)

**光讀章節你知道這些東西存在,做完 final project 你才真的會**。

---

## 你要 ship 什麼

任意一個 AI 應用,**真的有人用**。不是 localhost 自己看。標準:

**最低**:

- Deploy 到 server(或 Vercel / Cloudflare / 任何可對外)
- 有 auth 或能對外(自家公司內部也算)
- 至少**三個以外的人**用過至少一次

**推薦**:

- 10 個不認識你的人試過
- 有持續流量
- 你有收到真實 feedback

**野心版**:

- Side project 變 product
- 有付費使用者

題目不限,只要:
- **真實使用 LLM**(不是 wrapper 加個 chatbot 就算)
- **有實用價值**(能真的解決某人某問題)

---

## 靈感清單(選一個或自造)

### 個人工具類

1. **智能剪貼簿**:Clipboard 歷史,LLM 能 semantic 搜尋「剛才那段關於 A 的」
2. **Email drafting 助手**:Chrome extension,讀你收件匣摘要 + 起草回覆
3. **Meeting note 處理器**:貼逐字稿 → 結構化 note + action items + 同步 Notion
4. **AI 版的 obsidian**:你自己的筆記 Q&A
5. **外語學習**:讀文章時 hover 單字有 AI 上下文解釋

### 開發工具類

6. **自動 commit message generator**:一個 git hook,讓 LLM 產生 commit message
7. **SQL query 助手**:自然語言 → SQL + 執行預覽
8. **Log 分析**:把 log 貼進去,LLM 分類 + 找異常
9. **Repo 文件 Q&A**:給一個 repo,LLM 回答 codebase 問題
10. **Code review bot**:GitHub Action,自動 review PR

### 小型 SaaS 類

11. **領域特定 Q&A**:法律 / 醫療 / 財務的諮詢助手(注意合規!)
12. **產品文件助手**:公司 docs 的 chatbot
13. **內容生成器**:Blog post / tweet / video script 幫手
14. **Structured extraction**:從 PDF / 郵件 / 圖片抽欄位(invoice、receipt、contract)
15. **個性化學習**:根據使用者歷史給定制化練習題

### MCP / 生態類

16. **寫一個公用 MCP server 並 publish**(filesystem、knowledge base、API 包裝)
17. **Claude Code 配置 pack**(給某類 project 的 skills + hooks + MCP 全套)
18. **個人 agent**:定期做某事(daily summary、weekly review、monitoring)

---

## 階段劃分

### Phase 1:Scope(1–3 天)

**Output**:一頁 spec doc。

- 這是做什麼?
- 目標 user 是誰?(具體點:「不是開發者的文字工作者」)
- Core use case 是什麼?(一個最關鍵的 user journey)
- MVP 不做什麼?(明確減法)
- 成功標準:什麼指標到什麼值你滿意?
- 技術棧:frontend / backend / LLM / infra

**抗性強的 scope**:

- **User journey 一句話講清**(「貼 URL 進去 → 30 秒內拿到結構化摘要」)
- **不是「幫我做所有事」**(AI 版 Google、AI 版 Notion 這種 scope 必死)
- **能給具體 5 個人的痛點**(你能想到這 5 人現在怎麼解、為何不滿)

### Phase 2:Prototype(3–7 天)

**Output**:Localhost 能跑的 end-to-end。

- 先走通完整 flow(input → LLM → output),ugly 但能跑
- 使用前面學到的:Anthropic SDK 或 Agent SDK
- 如果需要 RAG:先簡易 Chroma + vector search
- 如果 agent:先最小 tool set

**不要**:
- 在 UI 投入超過 20% 時間
- 優化 prompt 到極致才往下
- 做 5 個 feature 才上線 1 個

### Phase 3:Eval(2–5 天)

**Output**:20–50 筆 golden + eval runner。

- 自己 manually 用 20 次,記每次的失敗
- 把失敗 case 變 golden
- 寫 assertion / LLM-judge eval
- 跑 baseline,記錄當前「pass rate」

**Eval 這步絕不能跳**。沒 eval 你不知道後面 prompt 改動是變好變壞。

### Phase 4:Harden(3–5 天)

**Output**:能上線的版本。

- **Prompt caching**(Ch 9)
- **Error handling**(Ch 25)
- **Rate limit + budget**(Ch 18、25)
- **Observability**(Ch 21)
- **Security basics**(Ch 22):input validation、output filter、auth、sandbox
- **Cost dashboard**

### Phase 5:Deploy + Users(2–5 天)

**Output**:線上服務 + 首批 user。

- Deploy 到 Vercel / Cloudflare / Fly.io / 自己 VPS
- 寫一份簡單 landing page 或直接給 URL
- 丟給 3 個朋友 / 同事試
- 收 feedback,記錄 bug 和 feature request

### Phase 6:Iterate(持續)

**Output**:記錄至少 3 輪改動。

- Bug fix
- Prompt tuning(跑 eval 前後比較)
- Feature(只做被要求兩次以上的)

---

## Production Checklist

上線前檢查:

### Essential

- [ ] LLM call 有 error handling + retry
- [ ] Prompt caching 開了(重複 prompt > 1024 tokens 的話)
- [ ] API key 絕不在 client 端
- [ ] 有 rate limit(至少 global,最好 per user)
- [ ] Max tokens / max cost 有 cap
- [ ] Health check endpoint
- [ ] Deploy 流程自動化(push to main → deploy)
- [ ] Logs(最少 console + 保留 7 天)
- [ ] Privacy statement(哪些資料送給 LLM)

### Important

- [ ] Golden eval set 存在,PR 自動跑
- [ ] Model ID pinned
- [ ] Prod bug 有 regression case
- [ ] Cost dashboard(至少一個 daily sum)
- [ ] User feedback 機制(thumbs up/down、回報 bug)
- [ ] Security basics(Ch 22)

### Nice to have

- [ ] A/B testing 機制
- [ ] Observability platform(Langfuse / Braintrust)
- [ ] Multi-model fallback
- [ ] User budget gating
- [ ] Formal eval per release

沒做 Essential **不要上線**。Important 上線後一週內補完。

---

## 成本規劃

LLM side project **失敗最常見原因是 cost**。提前算:

```
每 user 每次使用的成本:
  avg input tokens * model input price
  + avg output tokens * model output price
  - cached tokens savings
```

估算:

- 每 user 每日平均用幾次
- 每次大約多少 tokens
- 你能承受多少成本 / month

如果沒收費 + 流量大的可能性,**一定要有 budget gate + degrade mechanism**。LLM app 沒節流是災難。

常見 budget:

- Side project 自掏腰包:< $30/month
- 有小額收費:monthly cost = 30% of revenue(要留 margin)
- Free tier + paid tier:free tier 要 limits 足夠嚴

---

## 技術棧推薦(快速 shipping)

### 後端

- **Python + FastAPI**:最快
- **Node + Hono / Express**:JS 生態、Vercel 整合
- **Rust + Axum**:要 performance 而你順

### LLM

- **Anthropic API**(這課的主題)
- 多 provider:加 OpenAI / Gemini 做 fallback

### Vector DB(RAG 需要)

- **Chroma / LanceDB**:embedded
- **Qdrant cloud / Pinecone**:hosted
- **pgvector**:已有 Postgres 時

### Auth

- **Clerk / Auth0**:SaaS,快
- **NextAuth / Lucia**:self-host

### Deploy

- **Vercel**:快,免費 tier 足
- **Fly.io / Railway**:簡單 container
- **Cloudflare Workers**:edge、cheap

### Observability

- **Langfuse**(self-host 或 cloud)
- **Logtail / Axiom**:log aggregation

### Frontend

- **Next.js**:最常見
- **SvelteKit**:精巧
- **純 HTML + HTMX**:極簡
- **Chrome extension**:瀏覽器內工具

**選擇原則**:**你最熟的 stack**。Side project 學 AI 已經夠忙,別還要學新 framework。

---

## 寫 final project 的心態

### 1. 做得小但完整

比起做 20% 完成度的「AI 版 Notion」,做 100% 完成度的「PDF 發票抽欄位工具」**有用 100 倍**。

### 2. 真人 user 是北極星

你自己覺得酷 vs 別人真的用——後者才真的。每個功能決策問「某個 user 真會碰這個嗎」。

### 3. Ship > Perfect

Launch 一個 70% 版本拿 feedback 比優化到 95% 再 launch 好。

### 4. 記錄失敗

哪些 case 壞掉 → 留著。最後寫 post-mortem 時用得上。

### 5. 把這 repo 留著

Final project 的 code 不是「作業」,是你未來 reference。命名、文件、commit history 都用你 professional 的標準。

---

## 最後的 Write-up

做完後寫一份 post(公開 blog 或私下 note):

1. **我做了什麼?**(link + 截圖)
2. **為什麼做這個?**(痛點)
3. **架構是什麼?**(ASCII diagram)
4. **最難的三個問題和解法**
5. **Eval 長什麼樣?結果多少?**
6. **成本跟流量**
7. **如果重來我會怎麼做?**
8. **這門課哪部分最 impact 這產品?**

這篇 write-up 是你的**作品集**。下一份工作、下一個 founder 交易、下一場會議——都用得到。

---

## 畢業標準

Final project **算 pass**,如果:

- [ ] 線上有 URL 能打開
- [ ] 至少 3 個不是你的人用過
- [ ] 有 golden eval(> 20 case)且跑得出 score
- [ ] 有 observability(至少 log + cost dashboard)
- [ ] 用到這課的 3+ 個核心概念(caching、tool use、agent、MCP、RAG、eval...)
- [ ] 寫了 write-up post

做完這些,你就從**「看完 AI 課程的工程師」** 變成 **「能 ship AI 產品的工程師」**。

這兩者的人才市場差異,以 2026 年標準,是 2–3 倍薪資的事。

---

## 祝好運

前面 25 章是工具。Final project 是你的作品。

**不要只看不做**。看完 100 章沒 ship 過,不如看 50 章 + ship 一個產品。

去 ship。
