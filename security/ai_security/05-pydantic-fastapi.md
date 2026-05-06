# Ch 5 — Pydantic + FastAPI

> 目標：掌握 Pydantic 的型別驗證和 FastAPI 的路由結構，能寫出一個帶輸入驗證的 AI API endpoint，識別沒有驗證的 endpoint 有什麼具體風險。

---

## Pydantic BaseModel：自動驗證的資料結構

Pydantic 是 Python 的資料驗證函式庫，核心概念是：宣告型別，Pydantic 自動驗證和轉換。

```python
from pydantic import BaseModel, Field, field_validator
from typing import Literal

class VulnReport(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    severity: Literal["critical", "high", "medium", "low"]
    cvss_score: float = Field(ge=0.0, le=10.0)
    description: str = Field(max_length=2000)
    cve_id: str | None = None  # 選填

    @field_validator("cve_id")
    @classmethod
    def validate_cve_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        import re
        if not re.match(r"^CVE-\d{4}-\d{4,}$", v):
            raise ValueError(f"CVE ID 格式錯誤: {v}，應為 CVE-YYYY-NNNNN")
        return v

# 正常輸入
report = VulnReport(
    title="登入頁面 SQL Injection",
    severity="critical",
    cvss_score=9.8,
    description="username 欄位未過濾",
    cve_id="CVE-2024-12345",
)
print(report.cvss_score)  # 9.8（float）
print(report.model_dump())
# {
#   'title': '登入頁面 SQL Injection',
#   'severity': 'critical',
#   'cvss_score': 9.8,
#   'description': 'username 欄位未過濾',
#   'cve_id': 'CVE-2024-12345'
# }
```

```python
from pydantic import ValidationError

# 錯誤輸入：Pydantic 自動拒絕
try:
    bad = VulnReport(
        title="短",          # 太短，min_length=5
        severity="extreme",  # 不在 Literal 枚舉裡
        cvss_score=11.0,     # 超過 le=10.0
        description="x",
        cve_id="not-a-cve",  # 格式錯誤
    )
except ValidationError as e:
    print(e)
# 4 validation errors for VulnReport
# title: String should have at least 5 characters [...]
# severity: Input should be 'critical', 'high', 'medium' or 'low' [...]
# cvss_score: Input should be less than or equal to 10 [...]
# cve_id: Value error, CVE ID 格式錯誤: not-a-cve [...]
```

Pydantic 會把所有驗證錯誤一次列出，不是遇到第一個就停。

---

## 為什麼 AI 服務要用 Pydantic

LLM 的輸出是純字串。你不能假設它輸出的 JSON 是合法的，更不能假設欄位型別正確。

```
沒有 Pydantic 的流程：
  LLM 輸出 → 你用 json.loads() 解析 → 直接用 dict 傳下去
  → severity = "CRITICAL"（大寫，你的程式不認識）
  → cvss_score = "9.8"（字串，不是 float）
  → cve_id = "; DROP TABLE vulns; --"（SQL injection）
  → description = None（LLM 沒填，你的程式 crash）

有 Pydantic 的流程：
  LLM 輸出 → PydanticOutputParser 解析 + 驗證 → 型別保證的物件
  → 任何格式問題在這裡就爆出來，不會流到下游
```

Pydantic 把 LLM 的不確定性輸出轉成確定性的結構，是「LLM 輸出作為輸入的第一道防線」。

---

## FastAPI 基礎

```bash
pip install fastapi uvicorn httpx
```

```python
# app.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

app = FastAPI(title="AI Security API", version="0.1.0")

class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    model: Literal["llama3.2", "mistral"] = "llama3.2"

class AskResponse(BaseModel):
    answer: str
    model_used: str
    token_count: int

@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    # FastAPI 自動用 AskRequest 驗證 request body
    # 驗證失敗時自動回 422 Unprocessable Entity
    return AskResponse(
        answer=f"[模擬回答] 你問的是：{request.prompt}",
        model_used=request.model,
        token_count=len(request.prompt.split()),
    )

@app.get("/health")
async def health():
    return {"status": "ok"}
```

跑起來：

```bash
uvicorn app:app --reload --port 8000
```

測試：

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "什麼是 SQL injection？", "temperature": 0.0}'
# {"answer":"[模擬回答] 你問的是：什麼是 SQL injection？","model_used":"llama3.2","token_count":3}

# 驗證失敗的情況
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"prompt": "", "temperature": 2.0}'
# {"detail":[
#   {"type":"string_too_short","loc":["body","prompt"],...},
#   {"type":"less_than_equal","loc":["body","temperature"],...}
# ]}
```

---

## 完整 AI 服務範例：POST /ask 串 Ollama

```python
# ai_service.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from typing import Literal
import re

app = FastAPI()

ALLOWED_PROMPT_PATTERN = re.compile(r"^[\w\s一-鿿　-〿＀-￯.,?!；，。？！：:()（）\-]+$")

class AskRequest(BaseModel):
    prompt: str = Field(
        min_length=5,
        max_length=500,
        description="使用者問題，不可包含特殊符號",
    )
    category: Literal["general", "security", "code"] = "general"

    @field_validator("prompt")
    @classmethod
    def sanitize_prompt(cls, v: str) -> str:
        if not ALLOWED_PROMPT_PATTERN.match(v):
            raise ValueError("prompt 包含不允許的字元")
        # 拒絕已知的注入嘗試模式
        injection_keywords = [
            "ignore previous", "忽略之前", "system prompt",
            "你現在是", "pretend you are", "jailbreak",
        ]
        lower_v = v.lower()
        for kw in injection_keywords:
            if kw in lower_v:
                raise ValueError(f"prompt 包含禁止的關鍵字")
        return v

class AskResponse(BaseModel):
    answer: str
    category: str
    prompt_length: int

SYSTEM_PROMPTS = {
    "general": "你是一個通用助手，用繁體中文回答，回答限 200 字。",
    "security": "你是資安專家，只回答資安相關問題，用繁體中文，回答限 200 字。",
    "code": "你是程式設計師，只回答程式相關問題，用繁體中文，回答限 200 字。",
}

@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    llm = ChatOllama(model="llama3.2", temperature=0)
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPTS[request.category]),
        ("user", "{question}"),
    ])
    chain = prompt_template | llm | StrOutputParser()

    try:
        answer = chain.invoke({"question": request.prompt})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM 呼叫失敗: {str(e)}")

    return AskResponse(
        answer=answer,
        category=request.category,
        prompt_length=len(request.prompt),
    )
```

---

## 錯誤情境：沒有驗證的 endpoint

對比有沒有驗證的差異：

```python
# 危險寫法（不要這樣做）
@app.post("/ask_unsafe")
async def ask_unsafe(request: dict):  # 接受任意 dict，無驗證
    prompt = request.get("prompt", "")  # 可能是 None、list、巢狀物件
    llm = ChatOllama(model="llama3.2", temperature=0)
    # prompt 直接插進 LLM，無長度限制、無字元過濾、無注入偵測
    result = llm.invoke(prompt)
    return {"answer": result.content}
```

這個 endpoint 的風險：

| 風險 | 說明 |
|---|---|
| Prompt injection | `prompt` 直接傳給 LLM，任何惡意指令都進去 |
| 資源耗盡（DoS） | 無 `max_length`，攻擊者可傳 50MB 的 prompt |
| 型別混淆 | `prompt` 可以是 dict、list，`.invoke()` 行為未定義 |
| 敏感資訊洩漏 | LLM 可能被誘導輸出 system prompt 內容 |
| 費用攻擊 | 用 API key 的服務，大量長 prompt 讓帳單爆炸 |

**有了 Pydantic 的版本**，`min_length` 防資源耗盡，`max_length` 防費用攻擊，`Literal` 防類型混淆，`field_validator` 提供注入偵測的第一道過濾（不是萬能，Ch 7 會展開）。

---

## 輸入驗證的層次

```
第 1 層：Pydantic schema（型別、長度、格式）
    → 擋住格式錯誤和顯而易見的惡意輸入

第 2 層：field_validator（自訂業務邏輯）
    → 擋住已知注入關鍵字、pattern 過濾

第 3 層：LLM 前的 prompt 結構設計（system prompt 隔離）
    → 降低注入成功率（不是 100%）

第 4 層：LLM 輸出的 Pydantic 驗證
    → 確保 LLM 輸出不流出格式以外的資料

沒有任何一層是萬能的。縱深防禦。
```

FastAPI 的 `response_model=AskResponse` 也很重要：就算 LLM 輸出了意外的欄位，FastAPI 只會序列化 schema 裡定義的欄位，額外資料不會洩出去。

---

## 自我檢核

- [ ] 能用 BaseModel + Field 宣告帶長度和範圍限制的 schema
- [ ] 能寫 `field_validator` 做自訂驗證邏輯
- [ ] 能用 `.model_dump()` 把 Pydantic model 轉成 dict
- [ ] 能寫出 FastAPI POST endpoint，用 Pydantic model 當 request/response body
- [ ] 說得出「沒有 input validation 的 AI endpoint」至少三個具體風險
- [ ] 理解 response_model 在防止資訊洩漏上的作用

基礎建設章節到此結束。從 Ch 6 開始進入攻擊面——OWASP LLM Top 10 把這些弱點全部分類了一遍。

→ [Ch 6 — OWASP LLM Top 10](./06-owasp-llm-top10.md)
