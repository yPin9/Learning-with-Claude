# Practice A — Prompting 實戰

> 目標:把 Ch 4(prompting)和 Ch 3(claude.ai 功能)的心法練到反射。不是收集咒語,是建立 structure → test → iterate 的習慣。

## 環境

- claude.ai(Pro 以上方案)或 Anthropic Workbench
- 一本「prompt 日誌」(markdown file)

## 怎麼做這 practice

每題:

1. 先自己寫 prompt,嘗試 3 次以內達成
2. 試完後看提示,review 差距
3. 把最後版本存進 prompt 日誌,註明為什麼改、改了什麼

**這不是競賽,重點是 iterate 的過程**。

---

## 題目

### Level 1 — 基本結構化

**題 1:Sentiment classifier**

寫一個 prompt 讓 Claude 分類推文情緒為 positive / negative / neutral。要求:

- 輸出嚴格 JSON:`{"sentiment": "...", "confidence": 0-1, "reason": "..."}`
- 能處理反諷(「真棒,又壞了」這種)
- 非英文也要 work
- 10 筆混合 case 測試 8 筆以上正確

**測試 case**(自己準備,建議至少包含):

- 明顯正面
- 明顯負面
- 中性(純陳述)
- 反諷
- 混合情緒
- 非英文
- 空字串
- 純 emoji

**看提示前先試**。

<details>
<summary>提示</summary>

- 用 XML tag 分隔 instructions 和 examples
- 加 few-shot example,含反諷和中性 case
- 明確說 JSON schema
- confidence 要怎麼定義?在 prompt 裡寫
- 考慮用 tool use 強制 schema 輸出(Ch 8 技巧)
</details>

---

**題 2:Meeting summarizer**

給 Claude 一段會議逐字稿,輸出:

- 3 個 bullet points 的重點
- 列 action items(含負責人)
- 未解決的問題

測試:

- 100 字的短會議
- 2000 字的長會議
- 沒有 action item 的會議(不能硬編)

<details>
<summary>提示</summary>

- 明確說「沒 action item 就寫 None」避免 Claude 硬找
- Bullet 的長度限制(< 20 字)
- Action item 格式明確指定
- 試試加 few-shot example
</details>

---

### Level 2 — 多輪、帶 context

**題 3:Code reviewer prompt**

做一個 code review 的 prompt。給一段 Python code,輸出:

- 品質問題
- 安全問題
- 效能問題
- 整體評分 1-10

要求 prompt 能處理:

- 短 function
- 長 class
- 明顯壞 code(SQL injection)
- 明顯好 code(沒問題就說沒問題,不要硬挑)

<details>
<summary>提示</summary>

- 「沒問題就說沒問題」是關鍵,避免 LLM 硬挑毛病
- Rubric 明確:1 分、5 分、10 分各該是什麼樣
- 要求 inline comment format 可以 paste 回 PR
- 分 category 的 output 格式
</details>

---

**題 4:Multi-turn 客服 prompt**

寫一個客服 chatbot 的 system prompt。要求:

- Persona:友善但不 over-friendly
- Scope:只討論產品,不回政治 / 醫療 / 法律
- 不知道時誠實說不知道,不捏造
- 長期對話保持一致性

測試:

- 正常商品問題
- 嘗試把話題帶到政治
- 「我不爽要退貨」的情緒化 message
- 引誘洩漏 system prompt

<details>
<summary>提示</summary>

- Hard rules 用 enumerated list 寫死
- 「如何應對 out-of-scope」要明確
- 考慮 Claude 被 prompt inject 時怎麼辦(Ch 22)
- Test 時用多輪對話,看後幾輪會不會 drift
</details>

---

### Level 3 — Tool use

**題 5:Tool-based calculator**

API 實作。寫程式用 Anthropic SDK,讓 Claude 能用 `add`、`subtract`、`multiply`、`divide` 四個工具做數學。

要求:

- Claude 收到「3 + 5 × 2 的結果」要 call 正確順序的 tool
- 能處理 divide by zero(tool 回 error,Claude 報 user)
- 整體結果正確

<details>
<summary>提示</summary>

- Tool description 寫清楚回傳什麼
- Tool use loop 完整實作(Ch 8)
- Divide by zero raise exception,看 Claude 怎麼處理
- 試題目:"What's (12 + 8) * (15 - 7) / 4?"
</details>

---

**題 6:強制 JSON 輸出**

用 tool call 強迫 Claude 輸出結構化資料。情境:

> 給 Claude 一段履歷文字,要它抽出 `{name, email, education: [...], work_experience: [...], skills: [...]}`。

不能用「請你回 JSON」的 prompt,要用 tool。

<details>
<summary>提示</summary>

- 定義一個 `record_resume_fields` 工具
- `tool_choice={"type": "tool", "name": "..."}` 強制
- 取出 tool_use 的 input 當結果
- schema 嚴格(date format、email format)
</details>

---

### Level 4 — Prompt caching

**題 7:Cache-optimized Q&A**

你有 50k tokens 的公司 FAQ 內容。建一個 API 讓 user 問問題,後端呼叫 Claude + 把 FAQ 當 context,回答並 cite 來源。

要求:

- 用 prompt caching 避免每次 50k input 都付全額
- Verify cache 有 hit(看 usage.cache_read_input_tokens)
- 跑 10 次 query 看 cache 省多少

<details>
<summary>提示</summary>

- FAQ 放 system prompt,最後 block 加 `cache_control`
- 第 1 次 query:cache_creation 寫入
- 第 2–10 次:cache_read 大量
- 算 cost difference
</details>

---

**題 8:Multi-breakpoint caching**

複雜版:

- 50k FAQ
- 10k tool definitions
- 每 user 的 5k chat history(多輪對話)

設計三個 cache breakpoint,在第 10 輪對話時驗證每層 cache 命中情況。

<details>
<summary>提示</summary>

- FAQ(system text):breakpoint 1
- + tool definitions(tools 最後):breakpoint 2
- + history(messages 中間某處):breakpoint 3
- Cache 層越後變動越頻繁

驗證:10 輪後看 cache_read 的值是哪個 breakpoint 的
</details>

---

### Level 5 — 自己找 edge case

**題 9:對你自己的 prompt 做 red team**

挑你前面寫的任一 prompt。寫 20 個 test case,至少:

- 5 個正常 case
- 5 個 edge(空輸入、極短、極長、特殊字元)
- 5 個 adversarial(嘗試讓它失敗 / 違規)
- 5 個 prompt injection attempt

跑你的 prompt,看有幾個通過。**沒通過的改 prompt,再跑,iterate**。

---

**題 10:Meta-prompting**

用 Claude 幫你寫 prompt:

```
I want a prompt that does X.
Write me a prompt that [具體需求].
Include:
- Clear instructions
- Few-shot examples
- Output format spec
- 3 edge cases handled
```

比較 Claude 生成的 vs 你自寫的,哪個在 20 case 上表現好?

---

## 完成檢核

- [ ] 10 題中至少做 7 題
- [ ] Prompt 日誌有每題的 **final prompt + 你改的理由**
- [ ] Level 3–5 至少寫 code(不只在 Workbench 按)
- [ ] Level 5 對自己的 prompt 做 red team,並改進

這個 Practice 做完,你對 prompting 的直覺會明顯升級。但最重要的習慣是:**每次改 prompt 前後都測 case**。沒測就是玄學。
