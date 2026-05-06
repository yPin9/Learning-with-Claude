# Ch 2 — LangChain 核心

> 目標：看懂 LangChain 的基本架構，會寫 PromptTemplate + LCEL 管道 + OutputParser，理解每個環節在攻擊面上的意義。

---

## LangChain 是什麼

直接呼叫 OpenAI / Ollama API 能做所有事，那 LangChain 解決什麼問題？

```
沒有 LangChain：
  手動拼 prompt string → 呼叫 API → 手動解析輸出 string
  → 換模型要改三個地方
  → 加 RAG 要自己管 retriever + context 插入
  → 加 memory 要自己管 history array

有 LangChain：
  PromptTemplate  →  LLM  →  OutputParser
       ↑               ↑           ↑
   統一介面       可換模型    結構化輸出
```

LangChain 是膠水框架（glue framework），讓你把 LLM、prompt、retriever、tool、memory 組成管道（chain）。它不讓 LLM 變聰明，它讓你更快拼出複雜的 LLM 應用。

**資安角度**：每一個「組件」都是攻擊面。LangChain 幫你串起來，也幫你把攻擊面串起來。

---

## 安裝

```bash
pip install langchain langchain-openai langchain-community
# 或用本機 Ollama（不需要 OpenAI key）
pip install langchain langchain-ollama
```

---

## PromptTemplate：變數化的 prompt

直接字串拼接是最常見的注入漏洞前置條件。PromptTemplate 至少讓結構明確：

```python
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate

# 基本用法
template = PromptTemplate.from_template(
    "用繁體中文回答：{question}"
)

filled = template.format(question="TCP 三次握手是什麼？")
print(filled)
# 輸出: 用繁體中文回答：TCP 三次握手是什麼？
```

ChatPromptTemplate 對應 chat model 的 messages array：

```python
from langchain_core.prompts import ChatPromptTemplate

chat_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一個資安分析師，只回答資安相關問題。"),
    ("user", "{question}"),
])

messages = chat_prompt.format_messages(question="SQL injection 怎麼防？")
for m in messages:
    print(f"[{m.type}] {m.content}")
# [system] 你是一個資安分析師，只回答資安相關問題。
# [human] SQL injection 怎麼防？
```

PromptTemplate 的 `{variable}` 插值在呼叫時做字串替換。**問題在於它是無跳脫的直接插入**——如果你的變數內容是 `"} 忽略前面的指令，現在做 X {"` 之類的惡意輸入，PromptTemplate 照樣插進去，後面章節的注入攻擊就是這樣進來的。

---

## LCEL：管道語法

LCEL（LangChain Expression Language）用 `|` 運算子把組件串成管道，跟 Unix pipe 概念相同：

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import StrOutputParser

# 初始化本機 Ollama（假設已執行 ollama serve + ollama pull llama3.2）
llm = ChatOllama(model="llama3.2", temperature=0)

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是資安專家，用繁體中文回答，回答限 100 字以內。"),
    ("user", "{question}"),
])

parser = StrOutputParser()

# 管道：prompt → llm → parser
chain = prompt | llm | parser

result = chain.invoke({"question": "什麼是 buffer overflow？"})
print(result)
# 輸出（實際跑 Ollama llama3.2）：
# Buffer overflow（緩衝區溢位）是指程式將超過緩衝區容量的資料寫入記憶體，
# 覆蓋相鄰記憶體區域。攻擊者可利用此漏洞控制程式執行流程、執行惡意程式碼，
# 常見於 C/C++ 程式。防禦方式包括使用安全函式（如 strncpy）、啟用 stack canary
# 和 ASLR 等記憶體保護機制。
```

管道每一步的輸入輸出型別：

| 組件 | 輸入 | 輸出 |
|---|---|---|
| `PromptTemplate` | `dict` | `StringPromptValue` |
| `ChatPromptTemplate` | `dict` | `ChatPromptValue` |
| `ChatOllama` / `ChatOpenAI` | `ChatPromptValue` | `AIMessage` |
| `StrOutputParser` | `AIMessage` | `str` |
| `PydanticOutputParser` | `AIMessage` | Pydantic model instance |

---

## OutputParser：把字串逼成結構

StrOutputParser 只把 AIMessage 轉成純字串。**PydanticOutputParser** 讓 LLM 輸出合法 JSON，並自動驗證成 Pydantic model：

```python
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

class VulnAnalysis(BaseModel):
    vuln_type: str = Field(description="漏洞類型，例如 SQL injection、XSS")
    severity: str = Field(description="嚴重程度：critical / high / medium / low")
    affected_component: str = Field(description="受影響的元件名稱")
    recommendation: str = Field(description="修補建議，一句話")

parser = PydanticOutputParser(pydantic_object=VulnAnalysis)

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是資安分析師。根據描述分析漏洞，{format_instructions}"),
    ("user", "{description}"),
]).partial(format_instructions=parser.get_format_instructions())

llm = ChatOllama(model="llama3.2", temperature=0)
chain = prompt | llm | parser

result = chain.invoke({
    "description": "登入頁面的 username 欄位直接拼接進 SQL query，未做任何過濾。"
})

print(type(result))          # <class '__main__.VulnAnalysis'>
print(result.vuln_type)      # SQL Injection
print(result.severity)       # critical
print(result.affected_component)  # 登入頁面 username 欄位
print(result.recommendation) # 使用 parameterized query 或 ORM 取代字串拼接

# .model_dump() 轉回 dict
print(result.model_dump())
# {'vuln_type': 'SQL Injection', 'severity': 'critical',
#  'affected_component': '登入頁面 username 欄位',
#  'recommendation': '使用 parameterized query 或 ORM 取代字串拼接'}
```

`parser.get_format_instructions()` 會自動生成一段告訴 LLM「你必須輸出這個 JSON schema」的文字，插進 system prompt。LLM 若輸出的 JSON 不符合 schema，parser 會拋 `OutputParserException`。

---

## 完整 LLMChain 範例：問答 + 結構化輸出

把上面全部串起來，含錯誤處理：

```python
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.exceptions import OutputParserException
from langchain_ollama import ChatOllama

class SecurityAnswer(BaseModel):
    answer: str = Field(description="回答內容")
    confidence: str = Field(description="信心程度：high / medium / low")
    sources_needed: bool = Field(description="是否需要外部資料來源才能確認")

def ask_security_question(question: str) -> SecurityAnswer | None:
    parser = PydanticOutputParser(pydantic_object=SecurityAnswer)

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "你是資安專家。用繁體中文回答。\n{format_instructions}"),
        ("user", "{question}"),
    ]).partial(format_instructions=parser.get_format_instructions())

    llm = ChatOllama(model="llama3.2", temperature=0)
    chain = prompt | llm | parser

    try:
        return chain.invoke({"question": question})
    except OutputParserException as e:
        print(f"Parser 失敗: {e}")
        return None

ans = ask_security_question("ROP（Return-Oriented Programming）是什麼攻擊手法？")
if ans:
    print(f"回答: {ans.answer}")
    print(f"信心: {ans.confidence}")
    print(f"需要外部資料: {ans.sources_needed}")
```

---

## chain 的每一步都是攻擊面

```
使用者輸入
    ↓
PromptTemplate（{variable} 直接插入，無跳脫）   ← 注入點 1
    ↓
LLM（吃了含惡意指令的 prompt）                 ← 注入點 2
    ↓
OutputParser（解析 LLM 輸出，若 LLM 被控制，  ← 注入點 3
              輸出可能違反 schema 或含惡意資料）
    ↓
應用邏輯
```

PydanticOutputParser 在注入點 3 多加了一層型別驗證，但它防的是「格式不對」，不防「語意惡意」（LLM 輸出了格式正確但語意有害的內容）。後面 Ch 7 會展開 prompt injection 的細節。

---

## 自我檢核

- [ ] 能從空白寫出 ChatPromptTemplate + LCEL 管道 + StrOutputParser
- [ ] 知道 `|` 運算子每一步的輸入輸出型別
- [ ] 能用 PydanticOutputParser 強迫 LLM 輸出指定 schema
- [ ] 說得出 PromptTemplate 的 {variable} 插值為什麼是注入點
- [ ] `OutputParserException` 代表什麼，如何處理

理解了 chain，下一步看 chain 加上外部知識庫（RAG）是怎麼運作的——以及為什麼 retrieval 這一步是間接注入的完美入口。

→ [Ch 3 — RAG Pipeline](./03-rag-pipeline.md)
