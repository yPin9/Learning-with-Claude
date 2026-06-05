# Ch 0 — 環境搭建

> **目標**：把開發環境準備好，拿到 API key，跑出第一個成功的 Claude API 呼叫，並親手看到「一次 API 請求」長什麼樣——這是後面所有章節的地基。

> **環境**：本章以 Python 3.11、`anthropic` Python SDK 在 Windows 11 / macOS / Linux 上操作。SDK 改版頻繁（撰寫時最新已到 0.10x），**直接裝最新版**即可；本課範例用到的介面從 0.40 左右就穩定下來，所以只要不是更早的舊版都沒問題。官方支援 Python 3.9 以上，本課用 3.11 做基準。

## 為什麼這章不能跳過

很多人學 agent 的第一個錯誤，是直接打開某個框架（LangChain、CrewAI）跟著 quickstart 抄，然後 agent 跑起來了，但他完全不知道底下發生了什麼。等到要 debug「為什麼 agent 不呼叫工具」「為什麼 context 爆掉」時，因為從來沒看過最底層那一層 HTTP 請求長什麼樣，只能瞎猜。

這門課的策略相反：**我們先把框架全部拿掉，直接對著 Claude 的 Messages API 寫**。一個 agent harness 的本質，就是「反覆呼叫這個 API，並在中間做事」。你必須先對「一次呼叫」有肌肉記憶，後面才看得懂 harness 在那之上加了什麼。

所以這章我們只做三件事：裝好工具、拿到 key、跑出第一個呼叫並讀懂回傳。

## 先建立直覺：你即將安裝的東西在整條鏈的哪裡

```
   你的 Python 程式
        │  client.messages.create(...)
        ▼
   anthropic SDK (Python 套件)     ← 把參數包成 HTTP 請求、處理重試與串流
        │  HTTPS POST /v1/messages
        ▼
   Anthropic API 伺服器
        │
        ▼
   Claude 模型 (claude-opus-4-8 等)
```

整個 agent harness，從頭到尾都建立在最上面那一行 `client.messages.create(...)` 上。沒有魔法——harness 做的事就是「決定每一次這行要送什麼進去、回來之後要做什麼」。SDK 只是幫你省掉手刻 HTTP、簽 header、處理 429 重試的麻煩。

## Step 1：Python 與虛擬環境

先確認 Python 版本：

```bash
python --version
# 期望輸出類似：Python 3.11.7
```

如果你的系統 `python` 指向 2.x 或 3.9 以下，請改用 `python3` 或先升級。

接著替這門課開一個獨立的虛擬環境（virtual environment）。**為什麼要這樣做**：你電腦上可能有別的專案依賴不同版本的套件，把它們混在全域環境會互相打架。虛擬環境讓這門課的依賴自成一國。

```bash
# 在課程資料夾裡
python -m venv .venv

# 啟動它
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
```

啟動成功後，你的命令列提示字元前面會出現 `(.venv)`。之後所有 `pip install` 都只裝進這個環境。

> Windows PowerShell 第一次啟動可能報「無法載入，因為這個系統上已停用指令碼執行」。這是執行原則（ExecutionPolicy）擋的，不是你的程式有問題。執行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 後再試一次。

## Step 2：安裝 SDK

```bash
pip install anthropic
```

驗證裝好了、並看到版本：

```bash
python -c "import anthropic; print(anthropic.__version__)"
# 期望輸出類似：0.69.0（你裝到的會是當下最新版，數字更大很正常）
```

`pip install anthropic` 預設就裝最新版。如果你之前裝過、想確認是最新，`pip install -U anthropic` 升級一次。只要不是 0.40 之前的舊版，本課範例都能跑（更早的版本 tool use 與 streaming 的介面不一樣）。

## Step 3：拿到 API key

1. 到 <https://console.anthropic.com/> 註冊 / 登入。
2. 進 **Settings → API Keys**，按 **Create Key**。
3. key 長得像 `sk-ant-api03-xxxxxxxx...`。**它只會完整顯示這一次**，複製下來。

> Anthropic API 是付費的，按 token 計費。新帳號通常有少量試用額度。本課所有範例都刻意用很短的 prompt、設小的 `max_tokens`，跑完整門課的 API 花費很低（個位數美金等級）。但請自己到 console 的 **Usage** 頁面留意用量，尤其 Part 4 開始有 multi-agent 範例會一次呼叫多次。

### 把 key 放進環境變數，不要寫進程式碼

**這是安全紅線**：絕對不要把 `sk-ant-...` 直接貼在 `.py` 檔裡。一旦你 `git commit` 上 GitHub，爬蟲幾分鐘內就會掃到並盜刷。正確做法是放環境變數，SDK 會自動讀取名為 `ANTHROPIC_API_KEY` 的變數。

```bash
# macOS / Linux（寫進 ~/.bashrc 或 ~/.zshrc 讓它持久）
export ANTHROPIC_API_KEY="sk-ant-api03-你的key"

# Windows PowerShell（只在當前 session 有效）
$env:ANTHROPIC_API_KEY = "sk-ant-api03-你的key"

# Windows 永久設定（重開終端機才生效）
setx ANTHROPIC_API_KEY "sk-ant-api03-你的key"
```

更好的做法是用 `.env` 檔搭配 `python-dotenv`，這樣 key 跟程式碼分開、又能進虛擬環境：

```bash
pip install python-dotenv
```

在課程資料夾建一個 `.env`：

```
ANTHROPIC_API_KEY=sk-ant-api03-你的key
```

然後**馬上**把 `.env` 加進 `.gitignore`，確保它永遠不會被 commit：

```bash
echo ".env" >> .gitignore
```

## Step 4：第一個 API 呼叫

建一個 `hello.py`：

```python
from anthropic import Anthropic

# 不傳 api_key 參數時，SDK 會自動讀環境變數 ANTHROPIC_API_KEY
client = Anthropic()

resp = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=200,          # 限制模型最多回多少 token，是花費與長度的上限
    messages=[
        {"role": "user", "content": "用一句話解釋什麼是 agent harness。"}
    ],
)

print(resp.content[0].text)
```

跑：

```bash
python hello.py
```

> 這個範例只靠環境變數，不依賴 `python-dotenv`。如果你採用了 Step 3 的 `.env` 做法，記得 (1) 先 `pip install python-dotenv`，(2) 在檔案最上面加上 `from dotenv import load_dotenv` 和 `load_dotenv()` 這兩行——`load_dotenv()` 會把 `.env` 的內容讀進環境變數，之後 `Anthropic()` 才讀得到。沒裝 dotenv 卻 import 它，會吃 `ModuleNotFoundError`。

如果一切正常，你會看到 Claude 用一句話回答。**恭喜，你剛剛手動完成了一次 agent loop 的「一輪」**——只是還沒有 loop、也還沒有工具。

> 如果你想省成本或要更快，可以把 `model` 換成 `claude-haiku-4-5-20251001`（最便宜最快）或 `claude-sonnet-4-6`（折衷）。本課範例預設用 Opus，但環境設定階段用 Haiku 跑就夠了。

## Step 5：讀懂回傳物件

`messages.create()` 回傳的不是一個字串，而是一個結構化的 `Message` 物件。**看懂它的形狀，比拿到那句答案更重要**——因為 agent loop 的所有判斷都靠讀這個物件。把上面的 `print` 改成印出整個物件的關鍵欄位：

```python
print("id:        ", resp.id)
print("model:     ", resp.model)
print("role:      ", resp.role)         # 永遠是 "assistant"
print("stop_reason:", resp.stop_reason) # 為什麼停下來
print("usage:     ", resp.usage)        # 用了多少 token
print("content:   ", resp.content)      # 內容區塊的 list
```

你會看到類似：

```
id:         msg_01ABC...
model:      claude-opus-4-8
role:       assistant
stop_reason: end_turn
usage:      Usage(input_tokens=23, output_tokens=48, ...)
content:    [TextBlock(text='Agent harness 是...', type='text')]
```

四個欄位現在就要記住，它們是後面每一章的常客：

| 欄位 | 意義 | 為什麼 harness 需要它 |
|---|---|---|
| `content` | 一個 **block 的 list**，不是字串 | 模型可能同時回文字 + 工具呼叫，harness 要逐個 block 處理 |
| `stop_reason` | 模型為什麼停（`end_turn` / `max_tokens` / `tool_use` / ...） | loop 靠它決定「結束」還是「要去執行工具再回來」 |
| `usage` | 這次用掉的 input / output token 數 | 成本計量、context 預算（Part 2 整個 Part 在處理這件事） |
| `role` | 這則訊息的角色 | 把回傳塞回對話歷史時要標對角色（Ch 6） |

特別注意 **`content` 是 list**。最常見的新手錯誤就是假設它是字串，直接把 `resp.content` 當文字用。實際上你要 `resp.content[0].text` 才拿得到第一個文字區塊的內容。為什麼要這麼設計？因為一次回應裡可能有多個區塊——一段思考文字、接著一個工具呼叫——這在 Ch 5 會看到。先記住這個形狀。

> **`resp.content[0].text` 不是萬用寫法**：它只在「你確定第一個 block 是 text」時才成立，本章的純文字範例正是這種情況。一旦回應裡含 `tool_use`、thinking、或拒答（refusal）區塊，第一個 block 可能根本沒有 `.text` 屬性，硬取就會 `AttributeError`。正確的做法是遍歷 `content`、依每個 block 的 `type` 分別處理——Ch 5 會把這個迴圈寫出來。現在當成「最小範例的捷徑」用就好。

## Step 6：故意弄錯，看它怎麼罵你

教材會反覆用這招：**先看失敗，再看成功**，這樣你以後遇到同樣的錯誤訊息才認得。

把 key 故意改錯，再跑一次（注意：要直接傳 `api_key` 參數才會蓋掉環境變數）：

```python
client = Anthropic(api_key="sk-ant-這是錯的")
resp = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=50,
    messages=[{"role": "user", "content": "hi"}],
)
```

你會吃到：

```
anthropic.AuthenticationError: Error code: 401 - {'type': 'error',
  'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}}
```

再試另一個：把 `model` 打成不存在的名字 `"claude-opus-9000"`：

```
anthropic.NotFoundError: Error code: 404 - {'type': 'error',
  'error': {'type': 'not_found_error', 'message': 'model: claude-opus-9000'}}
```

這些 exception 類別（`AuthenticationError`、`NotFoundError`、還有之後會碰到的 `RateLimitError`、`APIConnectionError`）都繼承自 `anthropic.APIError`。**Ch 9 我們會專門做錯誤處理**，到時候你就是靠 catch 這些類別來決定「重試還是放棄」。現在先認得它們長什麼樣。

## 踩雷集錦

1. **「`resp.content` 印出來是一坨 list，不是我的答案」**：很多人以為回傳就是字串，實際上 `content` 是 block list，文字在 `resp.content[0].text`。不要把整個 list 當字串用。
2. **「key 設了還是 401」**：最常見原因是設環境變數的那個終端機，跟你跑程式的終端機不是同一個（尤其 Windows 用 `setx` 後沒重開終端）。確認 `python -c "import os; print(os.environ.get('ANTHROPIC_API_KEY'))"` 真的印得出 key。
3. **「我把 key 直接寫在程式裡然後 push 上去了」**：立刻到 console 把那把 key **撤銷（revoke）**，重新產一把。外洩的 key 必須作廢，沒有別的補救。這就是為什麼一開始就要用 `.env` + `.gitignore`。
4. **`max_tokens` 不是「我希望多長」而是「最多多長」**：設太小模型會被硬切斷，`stop_reason` 變成 `max_tokens`，答案不完整。這不是 bug，是你給的上限太低。
5. **裝錯虛擬環境**：忘記 `activate` 就 `pip install`，套件裝進全域，下次換個終端又說找不到 `anthropic`。看命令列前面有沒有 `(.venv)` 是最快的檢查。

## 進階：再往深一層

如果你想在進 Ch 1 之前多摸一點，試試這些：

- **打開 SDK 的 log**：設環境變數 `ANTHROPIC_LOG=debug`（或 `info`）再跑程式，SDK 會把請求相關的 debug 訊息印到 stderr——你能看到打到哪個 endpoint、重試了幾次、回應狀態碼等。這對之後理解 SDK 在背後做了什麼很有幫助。注意：這是 SDK 層級的 log，不保證把完整的 request/response body 原樣傾印出來；而且 debug log 可能含敏感內容（含你的 prompt），別在共享環境隨意開。真的想逐欄位檢查送出去的 JSON，到 Ch 2 我們會自己把 request payload 印出來看。
- **同步 vs 非同步**：本課大多用同步的 `Anthropic`，但 SDK 也有 `AsyncAnthropic`。當你之後要同時跑多個 subagent（Part 4），非同步版本能讓它們並行而不是一個等一個。現在不用管，知道有這東西即可。
- **算一下成本**：拿 `resp.usage.input_tokens` 和 `output_tokens`，對照 console 上你用的模型的單價，手算這次花了多少錢。對「token = 錢」建立體感，是 context engineering（Part 2）的心理基礎。

## 動手練習

1. 跑通 `hello.py`，確認看得到回答。
2. 把 `print(resp.content[0].text)` 改成印出 `stop_reason` 和 `usage`，跑三次不同長度的問題，觀察 `output_tokens` 怎麼變。
3. 把 `max_tokens` 設成 `10`，問一個需要長答案的問題，確認你看到 `stop_reason == "max_tokens"` 且答案被切斷。親眼看過一次，以後 debug 才認得。

## 本章重點整理

- agent harness 的最底層，就是反覆呼叫 `client.messages.create(...)`；框架只是包裝。
- API key 放環境變數 / `.env`，永遠不進程式碼、不進 git。
- 回傳的 `Message` 物件裡，`content`（block list）、`stop_reason`、`usage` 是後面每一章都會用到的三個欄位。
- 錯誤會以 `anthropic.APIError` 的子類別丟出，Ch 9 會靠它們做重試。

## 自我檢核

- [ ] 我能跑出一個成功的 API 呼叫，並從回傳物件取出文字
- [ ] 不看上面的表，我能說出為什麼 `content` 是 list 而不是字串
- [ ] 如果有人問我 `stop_reason` 和 `usage` 在 harness 裡各做什麼用，我答得出來
- [ ] 我能說出為什麼 key 不能寫進程式碼，以及萬一外洩了該怎麼辦

## 延伸閱讀

### 官方文件

- **[Anthropic — Messages API reference](https://docs.anthropic.com/en/api/messages)**
  - **讀哪裡**：request / response 範例，對照本章 Step 5 的欄位逐一看。
  - **能學到什麼**：`content` block 的完整型別清單（text / tool_use / tool_result / image），這在 Ch 5 會全部用到。
  - **前提知識**：會讀 JSON 即可。

- **[Anthropic Python SDK README](https://github.com/anthropics/anthropic-sdk-python)**
  - **讀哪裡**：「Usage」「Handling errors」「Async usage」三節。
  - **能學到什麼**：SDK 的錯誤類別階層、重試與 timeout 的預設值、`AsyncAnthropic` 的用法——Ch 9 和 Part 4 會回來用。
  - **前提知識**：本章看完即可。

### 部落格 / 技術文章

- **[The Twelve-Factor App — III. Config](https://12factor.net/config)** — Adam Wiggins（Heroku 共同創辦人）
  - **這篇說什麼**：為什麼「設定（含密鑰）要放環境變數而非程式碼」是一條通則，不只是 API key 的特例。
  - **讀哪裡**：「III. Config」一節，兩分鐘讀完。
  - **為什麼值得讀**：這是業界對「設定與密鑰管理」最被廣泛引用的原則來源，幫你建立正確的工程習慣而不只是死記「key 別 commit」。

下一章我們不寫 code，先把「LLM」和「agent」這兩個常被混用的詞徹底分開，看清楚 harness 到底在中間補了什麼，你才知道接下來每一章在解決哪一塊。

→ [Ch 1 什麼是 agent harness？](./01-what-is-agent-harness.md)
