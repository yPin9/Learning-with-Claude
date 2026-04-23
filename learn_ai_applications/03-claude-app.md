# Ch 3 — claude.ai / Projects / Artifacts

> 目標:把 Claude 的消費端(claude.ai / Desktop / mobile)用到極致,知道哪些情境用它最對、哪些該換到 Claude Code。

## 不寫 code 也能做很多事

很多工程師低估 claude.ai。**它不只是聊天視窗**——Projects、Artifacts、Files、Styles 加起來是個很強的個人生產力工具。但大多數人只用到 10%。

---

## Projects:把 context 留在對話外

**Projects 是長期的 context 容器**。建一個 project,給它:

- **Custom instructions**:這個 project 裡 Claude 的角色、風格、約束
- **Files / knowledge**:上傳文件,project 裡的對話都會參考
- 可選的 integrations(如 GitHub)

**何時用 Project**:

- 一個持續進行的主題(研究、寫書、學一門新語言)
- 要重複使用一組文件(產品 spec、內部文件、論文集)
- 不想每次對話都重貼 instruction

**何時不用**:

- 一次性問題
- 要讀本地 code(用 Claude Code)

### Project 的 custom instructions 怎麼寫

**壞例子**:

> "請幫我寫 code"

**好例子**:

```
I'm working on a Python web service using FastAPI and PostgreSQL.

Constraints:
- Python 3.11, use modern syntax (type hints, match statements)
- SQLAlchemy 2.x style, not 1.x
- Write tests with pytest + httpx.AsyncClient
- Don't suggest libraries not already in pyproject.toml unless asked

Style:
- Be terse. I'll ask follow-ups if I need more.
- Don't explain what the code does unless I ask.
- When I say "refactor", suggest 2-3 options with trade-offs, not one "correct" answer.
```

**三個重點**:

1. **上下文**:你在做什麼、技術棧
2. **約束**:硬規則(別用這個、必用那個)
3. **互動風格**:你想要什麼樣的溝通

這段會在每次對話開頭自動 inject,省下你重複貼。

### Project Knowledge 的限制

- 總大小有限(現在是 10MB 左右)
- 不是 RAG——整個 knowledge 會直接灌入 context,多了會擠掉對話空間
- 超過會開始截斷,Claude 可能「忘了」某份文件的細節

**建議**:

- **少量精選的核心文件**,不是所有相關文件
- 文件內容寫好標題和 section,讓 Claude 好定位
- 大型 codebase 別灌進來,用 Claude Code

---

## Artifacts:輕量級的「可互動輸出」

Artifact 是 Claude 輸出「可以被展示、編輯、執行」的東西時,從對話中抽出來變成側邊欄的元素。

支援:
- Markdown 文件(含表格、數學式)
- Code(各語言,有 syntax highlight)
- **React 元件**(可直接預覽跑,含 state)
- SVG / HTML / Mermaid 圖

### 什麼時候該要 Artifact

- 要一份會修訂多次的文件 / code
- 要一個 interactive demo(例如小工具、視覺化)
- 要一個可複製出去的完整產出(對話之外用得到)

Claude 會自動決定要不要包成 artifact。你也可以明講「please create an artifact」。

### Artifact 的最強用法:prototype React UI

Claude 可以在 artifact 裡直接寫 React + Tailwind + shadcn/ui,馬上跑。

**實用場景**:

- 畫 mockup 給客戶看
- 原型化一個想法,不用開發環境
- 互動式的學習工具(例如解釋某個演算法)

**限制**:
- 不能 fetch 外部 API(CORS 封死)
- 不能存 localStorage
- 不能用 npm 裝套件——只有預載的 lib

所以 artifact 適合**視覺化 / 互動**,不適合「連真實後端」。

---

## Styles:訂製 Claude 的回應風格

Style 是 claude.ai 的相對新 feature,讓你存「幾組回應風格預設」:

- **Formal**:官方、嚴謹
- **Concise**:簡短、直接
- **Explanatory**:多解釋
- **Custom**:你寫的

Styles 跟 Project 的 custom instructions 不同在於:

- Project 是「這個主題怎麼做」
- Style 是「用什麼 tone / 格式」

兩者可疊加:一個 Python project + concise style = 在這 project 裡用 concise 風格回答。

### Custom style 實例

```
Voice: Direct, opinionated, terse. Not a polite assistant.
Format: No filler intros. No "I hope this helps" endings.
When asked open questions, give a recommendation, not a balanced pros/cons list.
When you don't know something, say "I don't know" directly.
Technical depth: Senior engineer. Don't over-explain basics.
```

這風格的 Claude 會比預設版直接 3 倍。不愛客套的人會很喜歡。

---

## Files / Attachments

claude.ai 支援上傳:

- PDF、Word、Excel、PPT
- 程式碼檔
- 圖片(會用 vision 理解)
- Audio(有些方案支援)

**PDF 的處理**:Claude 會取 text + 某些情況也看 layout(圖表、公式)。複雜排版的 PDF 偶爾會讀錯——對關鍵文件先用 `pdftotext` 轉純文字再貼,比直接上傳穩定。

**圖片**:畫面、白板拍照、截圖都能讀。**字夠清楚的話,讀中文也沒問題**。

---

## 什麼時候該離開 claude.ai 到 Claude Code

訊號:

- **你要讓 Claude 讀本地檔案**(尤其整個 repo)
- **要 Claude 執行 bash / 改你 local 檔案**
- **要 automation**(排程、CI、hook)
- **要 MCP 接外部系統**(資料庫、內部工具)
- **要多 agent / subagent**

這些 claude.ai 都做不到。跳去 Ch 5。

---

## 什麼時候該留在 claude.ai

- 寫文件、做簡報大綱、寫 email
- 非程式碼的研究、閱讀、摘要
- 快速畫一個 mock UI(artifact 的 React)
- 沒有本機環境的場合(手機、借的電腦)

---

## 幾個 claude.ai 的隱藏操作

### 1. 複製 conversation 當 context 給別人

對話右上角可分享 link(Pro+)。也可以 export 成 markdown。**給同事看你怎麼解一個問題,比口述快 10 倍**。

### 2. 用 artifact 做「可迴歸」的 prompt

把 prompt 模板塞在 artifact(markdown),每次要用時打開那個 artifact 修改後用。等於把 prompt 當 source of truth 存起來。

### 3. 「續寫」長輸出

如果 Claude 輸出被截斷(達到 max_tokens),直接說「繼續」(continue)。Claude 會接著寫。

### 4. 「這次不要 think」

在需要快速回應的場合,可以在 prompt 開頭寫「no need to think deeply, just answer」。(extended thinking 只在特定模型/介面開啟,見 Ch 10)

---

## 付費方案怎麼選

(2026 年狀態,可能變)

| 方案 | 定位 |
|---|---|
| Free | 嘗鮮,額度很緊 |
| Pro | 一般工程師日常用 |
| Max | 重度使用者,含 Claude Code 高額度 |
| Team | 小團隊共用 |
| Enterprise | 公司級,SSO、admin、合規 |

**建議**:自己用優先選 Pro 或 Max。Max 的 Claude Code 額度比 Pro 多很多,工程師通常值得。公司買要走 Team / Enterprise。

---

## 自我檢核

- [ ] Project custom instructions 和 custom style 的區別?
- [ ] Artifact 適合做 React UI,但有什麼場景它做不到?
- [ ] Project knowledge 的 10MB 限制,為什麼不是給你一個 RAG 系統?
- [ ] 你什麼時候該從 claude.ai 跳到 Claude Code?給三個訊號。
- [ ] 你會在什麼 project 裡開一個 style?

→ [Ch 4 Prompting 的心法(不是招式集)](./04-prompting.md)
