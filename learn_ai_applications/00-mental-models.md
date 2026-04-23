# Ch 0 — LLM 應用的錯誤心智模型

> 目標:把你腦中對 LLM 的幾個「看起來合理但會害你寫出爛產品」的直覺打掉重練。

LLM 應用做不好,90% 不是技術問題,是**心智模型錯了**。Prompt 寫得再花、工具接得再多,底層認知錯,產品就是爛。

這章列出幾個最常見的誤會,一個個拆。

---

## 誤會 1:LLM 是「很會搜尋的搜尋引擎」

**這誤會會讓你**:
- 以為 LLM 應該「總是正確」,錯了就是 prompt 不夠好
- 不做 RAG,期待 LLM 「記得」所有事實
- 不做 eval,因為「直覺上看起來對」

**真相**:LLM 是**統計 next-token predictor**,沒有事實庫,只有訓練分布和當下 context window。它不知道今天是幾號,它不知道你的 codebase,它不知道你的客戶名單——**除非你把這些塞進 context**。

**可操作的推論**:
- 需要事實準確性 → RAG + 明確引用
- 需要當下資訊 → tool(web search / DB)
- 需要確定性 → 寫規則判斷,不要讓 LLM 做判斷

**一句話**:**LLM 是有創意的助手,不是權威資料庫**。

---

## 誤會 2:「寫好 prompt 就好」

**這誤會會讓你**:
- 把 prompt engineering 當成萬靈丹
- 忽略 retrieval、tool design、eval 這些「外圍」組件
- 產品卡在「看起來會動」,但一放 prod 就爛

**真相**:Prompt 是系統的**一個**輸入。LLM 產品的真正架構長這樣:

```
使用者輸入
    ↓
前處理(routing / intent / validation)
    ↓
Retrieval(取相關文件 / 資料)
    ↓
Prompt 組裝(template + retrieved + 使用者)
    ↓
LLM 呼叫(可能含 tool use 迴圈)
    ↓
後處理(parse / validate / guardrail)
    ↓
回應使用者 + 記錄 trace + 更新 eval
```

**Prompt 只是中間一環**。別的環壞,prompt 再神也救不了。

**一句話**:**LLM app 是 pipeline,prompt 只是 pipeline 的一個 block**。

---

## 誤會 3:「Temperature = 0 就是 deterministic」

**這誤會會讓你**:
- 以為調 temp=0 就能重現每次結果
- 做不出能回歸測試的 LLM app
- debug 時找不到為什麼同 prompt 結果不同

**真相**:

1. 即使 temp=0,底層實作(floating point reduction 順序、batch composition)可能讓結果變。
2. **不同版本的模型**(claude-sonnet-4-6 vs claude-sonnet-4-5)在同 prompt 給完全不同回應。
3. **Tool use 帶入的環境差異**(今天的時間、retrieval 結果)讓輸出進一步不穩定。

**可操作的推論**:
- 寫 eval 時**不比較字串相等**,比較**關鍵資訊是否正確**。
- 版本變動當作**主要變異源**,eval pipeline 每次換模型都要重跑。
- 需要 deterministic 的部分**不要讓 LLM 做**——用代碼。

**一句話**:**LLM 是機率系統,不是純函數**。

---

## 誤會 4:「Agent 能自主解決任何問題」

**這誤會會讓你**:
- 把所有事都做成 agent
- 以為給 agent「更多工具」就會變強
- 產品做出來很炫但 tool call 一直錯

**真相**:

- **Agent 的失敗率隨步數指數級上升**。每步 95% 對,10 步後 0.95^10 ≈ 60%。
- **工具給太多 agent 會迷失**。10 個工具比 3 個工具**表現更差**,因為選擇空間變大。
- **多數任務不需要 agent**,一次 LLM 呼叫 + 一點結構化就夠。

**可操作的推論**(從 Anthropic 《Building Effective Agents》抄來的):
- 能用 workflow(固定 pipeline)就別用 agent(動態決策)
- 工具數量盡量少,描述盡量清楚
- Agent 每步後要**觀察環境**(結果塞回 context),別讓它盲飛

**一句話**:**Agent 是最強的武器也是最貴的武器,用來殺蚊子是浪費**。

---

## 誤會 5:「LLM 會越來越強,現在寫的架構以後不用改」

**這誤會會讓你**:
- 偷懶不建 eval,反正下一代模型會更好
- 不做 observability,反正「LLM 會自己解決」
- 綁定特定模型的特殊行為

**真相**:

模型會進步,但:

1. **你的 eval 才能證明進步**。沒 eval,換模型你不知道變好變壞。
2. **每次換模型都是 migration**——prompt 可能要重寫、tool 描述可能要調。
3. **成本和延遲不會自動變好**,產品決策上你永遠在權衡。

**可操作的推論**:
- 從 day 1 建 eval,哪怕只有 20 筆
- 從 day 1 做 observability(log traces)
- 抽象出「模型」這層,方便換
- Prompt 裡別寫 `claude-sonnet-4-6` 這種硬編碼的模型行為假設

**一句話**:**工程紀律不因 AI 變強而減少,只是換形狀**。

---

## 誤會 6:「LLM 的隨機性是 bug,要消除」

**這誤會會讓你**:
- 花大量時間調參追求「每次輸出一樣」
- 錯過 LLM 真正的優勢(靈活、泛化)
- 做出過度僵化的產品

**真相**:LLM 的隨機性是**特徵**。它能處理你沒預期的輸入、用 novel 的方式組合概念——這些靠「寫死的規則」做不到。

**更好的心智模型**:LLM 是個**聰明但不穩定的實習生**。你不會叫實習生每次都說一模一樣的話,你會:

- 給他清楚的任務和格式
- 讓他做他擅長的事(溝通、摘要、分類、發想)
- **別讓他做他不擅長的事**(精確計算、事實查詢、決定性邏輯)
- 有 QA 流程檢查他的產出

**一句話**:**把 LLM 當實習生用,不要當 CPU 用**。

---

## 這門課的基本假設

基於上述心智模型,本課程的設計前提:

1. **LLM app 是 pipeline**,不是單一 prompt。
2. **Eval 是核心工程實踐**,不是 nice-to-have。
3. **Agent 是工具之一**,不是銀彈。
4. **成本、延遲、可靠性是產品指標**,跟傳統軟體一樣。
5. **Claude 生態是載體**,但原則跨模型通用。

後續章節按這幾點組織。

---

## 自我檢核

- [ ] 你能不能用一段話解釋「LLM 不是資料庫」?
- [ ] 你用過的 LLM app 裡,哪些其實該用 workflow 不該用 agent?
- [ ] 你現在寫的 LLM code 有沒有 eval?沒有的話能在一週內建嗎?
- [ ] 「temperature = 0 是否 deterministic?」你能正確回答嗎?
- [ ] 給你 10 個工具讓你 agent 用,你會全接還是挑?為什麼?

→ [Ch 1 Token / Context / Sampling / Tool Use 的最低必備](./01-llm-essentials.md)
