# Ch 0 — 環境搭建

> **目標**：一條龍建好 Ollama + Python venv + LangChain + ChromaDB + FastAPI 環境，能跑第一個 LLM query，確認每個元件都活著。

---

## 為什麼用 Ollama？

本課的攻擊練習需要你能對 LLM 做任何事：注入任意 prompt、觀察 raw output、修改 sampling 參數、反覆打同一個模型看回應差異。用 OpenAI API 做這些事有三個問題：

1. **花錢**：攻擊測試會打大量 request，token 費用會失控
2. **有限制**：OpenAI 的 content filter 會擋掉你的攻擊 payload，讓你分不清是「攻擊沒打到」還是「被 API 擋了」
3. **不可控**：模型版本會更新、系統 prompt 會變、rate limit 會卡你——這些都讓實驗不可重現

Ollama 解決了這三個問題：本機跑、免 API key、沒有 content filter、模型版本你自己決定。你對它做任何攻擊測試都不會有人來敲門。

---

## 第一步：安裝 Ollama

### Windows

到 [ollama.com](https://ollama.com/) 下載 Windows installer，安裝完會在系統匣跑一個 Ollama 常駐程式。

驗證安裝：

```powershell
ollama --version
# ollama version is 0.x.x
```

### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### macOS

```bash
brew install ollama
# 或者到 ollama.com 下載 .dmg
```

裝完之後，確認 Ollama service 正在跑：

```bash
ollama serve
# 如果 Windows installer 已經自動啟動，這行會報 port already in use，那就是已經跑了
```

---

## 第二步：拉模型

```bash
ollama pull llama3.2:3b
```

### 為什麼選 llama3.2:3b？

| 考量 | 說明 |
|------|------|
| **大小** | 3B 參數，量化後約 2GB VRAM / RAM——8GB 記憶體的機器就跑得動 |
| **能力** | 足夠做 prompt injection、jailbreak、RAG 等所有攻擊測試——你不需要 GPT-4 等級的推理能力來當靶子 |
| **速度** | 3B 在 CPU 上也能跑出可接受的速度（~10 tokens/sec），不用等 GPU |
| **授權** | Meta 的 Llama 3.2 Community License，學術和個人使用沒有問題 |

如果你的機器有 16GB 以上 RAM 或有獨顯，可以考慮拉 `llama3.1:8b` 得到更好的回應品質。但 3B 足以完成本課所有練習。

拉完驗證：

```bash
ollama list
# NAME              ID              SIZE      MODIFIED
# llama3.2:3b       ...             2.0 GB    ...
```

快速測試模型能不能回應：

```bash
ollama run llama3.2:3b "What is 2+2?"
# 應該回 4（或一段包含 4 的文字）
```

---

## 第三步：Ollama REST API 驗證

Ollama 預設在 `localhost:11434` 開 REST API。這個 API 後面 LangChain 會用到，先確認它活著：

```bash
curl http://localhost:11434/api/generate -d '{
  "model": "llama3.2:3b",
  "prompt": "Say hello in one word.",
  "stream": false
}'
```

預期回應是一個 JSON，裡面有 `response` 欄位包含模型的輸出。

如果你在 Windows 用 PowerShell，curl 是 `Invoke-WebRequest` 的 alias，行為不同。用這個：

```powershell
$body = @{
    model  = "llama3.2:3b"
    prompt = "Say hello in one word."
    stream = $false
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:11434/api/generate" -Method Post -Body $body -ContentType "application/json"
```

### API 端點速查

| 端點 | 用途 |
|------|------|
| `POST /api/generate` | 單次生成（completion） |
| `POST /api/chat` | 對話模式（帶 message history） |
| `GET /api/tags` | 列出已下載的模型 |
| `POST /api/embeddings` | 取得文字的 embedding vector |

後面 Ch 1 會用 `/api/embeddings` 觀察 token 的向量表示，Ch 2 會透過 LangChain 間接呼叫 `/api/chat`。

---

## 第四步：Python 虛擬環境

### 建立 venv

```bash
# 在你想放課程專案的目錄下
mkdir ai-security-lab && cd ai-security-lab

python3 -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# Windows cmd
.\.venv\Scripts\activate.bat
```

### 安裝套件

```bash
pip install langchain langchain-ollama langchain-community chromadb fastapi uvicorn pydantic
```

各套件的角色：

| 套件 | 用途 | 本課使用場景 |
|------|------|-------------|
| `langchain` | LLM 應用框架核心 | Chain、Memory、OutputParser |
| `langchain-ollama` | LangChain 的 Ollama provider | 連接本機 Ollama 模型 |
| `langchain-community` | 社群整合（含 ChromaDB vector store） | RAG pipeline |
| `chromadb` | 本機向量資料庫 | 儲存 document embeddings |
| `fastapi` | Web framework | 把 LLM 服務化成 API |
| `uvicorn` | ASGI server | 跑 FastAPI |
| `pydantic` | 資料驗證 | Structured output、request validation |

驗證安裝：

```python
python3 -c "
import langchain
import langchain_ollama
import chromadb
import fastapi
import pydantic
print('langchain:', langchain.__version__)
print('chromadb:', chromadb.__version__)
print('fastapi:', fastapi.__version__)
print('pydantic:', pydantic.__version__)
print('All imports OK')
"
```

---

## 第五步：驗證 LangChain + Ollama

建一個 `test_langchain.py`：

```python
from langchain_ollama import OllamaLLM

llm = OllamaLLM(model="llama3.2:3b")
response = llm.invoke("What is prompt injection? Answer in one sentence.")
print(response)
```

跑起來：

```bash
python test_langchain.py
# 應該印出一段關於 prompt injection 的定義
```

如果能正常印出回應，LangChain → Ollama 的連線就通了。

---

## 第六步：驗證 ChromaDB

建一個 `test_chroma.py`：

```python
import chromadb

client = chromadb.Client()

collection = client.create_collection(name="test")

collection.add(
    documents=[
        "Prompt injection is an attack against LLM applications.",
        "SQL injection is an attack against databases.",
        "Buffer overflow is an attack against memory safety.",
    ],
    ids=["doc1", "doc2", "doc3"],
)

results = collection.query(
    query_texts=["How to attack an AI system?"],
    n_results=2,
)

print("Query: How to attack an AI system?")
print("Top 2 results:")
for doc in results["documents"][0]:
    print(f"  - {doc}")
```

跑起來：

```bash
python test_chroma.py
# Query: How to attack an AI system?
# Top 2 results:
#   - Prompt injection is an attack against LLM applications.
#   - SQL injection is an attack against databases.
```

ChromaDB 預設用自帶的 embedding model（`all-MiniLM-L6-v2`），不需要額外設定。如果第一結果是 prompt injection 相關的文件，代表語義搜尋有在運作。

---

## 第七步：驗證 FastAPI

建一個 `test_fastapi.py`：

```python
from fastapi import FastAPI
from langchain_ollama import OllamaLLM
from pydantic import BaseModel

app = FastAPI()
llm = OllamaLLM(model="llama3.2:3b")


class QueryRequest(BaseModel):
    prompt: str


class QueryResponse(BaseModel):
    response: str


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    result = llm.invoke(request.prompt)
    return QueryResponse(response=result)
```

啟動：

```bash
uvicorn test_fastapi:app --host 0.0.0.0 --port 8000
```

測試：

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is 1+1?"}'
```

如果收到 JSON 回應且 `response` 欄位有內容，FastAPI + Ollama 整合就成功了。這個架構就是後面所有攻擊練習的靶標——一個暴露 HTTP API 的 LLM 服務。

---

## 專案目錄結構建議

```
ai-security-lab/
├── .venv/                    # Python 虛擬環境
├── attacks/                  # 攻擊腳本
│   ├── prompt_injection/
│   ├── jailbreak/
│   └── rag_poisoning/
├── defenses/                 # 防護實作
│   ├── guardrails/
│   ├── input_filter/
│   └── output_filter/
├── services/                 # 靶標服務
│   ├── simple_chat.py        # 單純 LLM 對話
│   ├── rag_service.py        # RAG pipeline
│   └── agent_service.py      # Agent + tool calling
├── data/                     # 測試文件、corpus
│   └── knowledge_base/
├── reports/                  # 評測報告（final project 用）
├── requirements.txt
└── README.md
```

不需要現在就建完整目錄——隨著課程進度會逐步展開。現在先確認 `ai-security-lab/` 和 `.venv/` 存在就好。

---

## 踩雷集

### Ollama 佔的記憶體

Ollama 載入模型後會把整個模型放進記憶體（GPU VRAM 或 CPU RAM）。`llama3.2:3b` 量化後約 2GB，但 Ollama 進程本身也吃記憶體。如果你的機器只有 8GB RAM，跑 Ollama 的同時開 Chrome 可能會 swap 到硬碟，推論速度會掉到不可用。

**解法**：關掉不需要的程式，或者用 `ollama stop llama3.2:3b` 在不用的時候釋放模型。Ollama 預設 5 分鐘沒用會自動卸載模型。

### 模型下載卡住

`ollama pull` 下載大模型時，如果網路斷一下，進度條會卡在那裡不動。

**解法**：`Ctrl+C` 中斷，重新跑 `ollama pull`——它會從斷點續傳，不會從頭來。

### ChromaDB SQLite 版本衝突

ChromaDB 內部用 SQLite，它要求 SQLite >= 3.35.0。某些 Linux distro 的系統 Python 帶的 SQLite 版本太舊（特別是 Ubuntu 20.04），會報：

```
RuntimeError: Your system has an unsupported version of sqlite3.
Chroma requires sqlite3 >= 3.35.0.
```

**解法**：

```bash
pip install pysqlite3-binary
```

然後在你的 Python 程式最上面加：

```python
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
```

這不是優雅的解法，但在 ChromaDB 修正之前是官方建議的 workaround。

### WSL2 vs Native Windows

Ollama 在 Windows 原生和 WSL2 裡都能跑，但行為不同：

| 面向 | Windows Native | WSL2 |
|------|---------------|------|
| GPU 支援 | 直接用 NVIDIA/AMD | 需要 WSL2 GPU passthrough（NVIDIA 支援較好） |
| 效能 | 正常 | 跨 filesystem 存取（`/mnt/c/`）效能差，把資料放 WSL2 原生 filesystem |
| Port 共享 | Ollama 在 `localhost:11434` | WSL2 的 `localhost` 和 Windows 共享（Windows 11），舊版可能需要轉發 |

**建議**：如果你用 Windows，直接在 Windows Native 跑 Ollama 最省事。Python venv 可以在 WSL2 裡，透過 `localhost:11434` 連 Windows 上的 Ollama——跨系統呼叫 REST API 沒有效能問題。

### langchain 版本碎片化

LangChain 在 2024 年經歷了一次大規模拆包（`langchain` → `langchain-core` + `langchain-community` + 各 provider package）。舊版教學裡的 `from langchain.llms import Ollama` 在新版會報 `ImportError`。

**本課統一用法**：

```python
# 正確（新版，provider package 分離後）
from langchain_ollama import OllamaLLM
from langchain_ollama import OllamaEmbeddings

# 錯誤（舊版，已廢棄）
from langchain.llms import Ollama
from langchain.embeddings import OllamaEmbeddings
```

如果你在網路上看到用 `from langchain.llms` 的教學，那是舊版 API。本課全部使用 `langchain` 0.3.x + 獨立 provider package 的寫法。

---

## 環境檢查清單

跑完以上步驟，確認這些都打勾：

- [ ] `ollama --version` 有輸出
- [ ] `ollama list` 裡有 `llama3.2:3b`
- [ ] `curl http://localhost:11434/api/generate` 能回應
- [ ] Python venv 啟動後，`pip list` 裡有 langchain、chromadb、fastapi
- [ ] `test_langchain.py` 能印出 LLM 回應
- [ ] `test_chroma.py` 能印出語義搜尋結果
- [ ] `test_fastapi.py` 能在 `localhost:8000` 回應

全部通過，你的攻擊實驗室就準備好了。

---

## 延伸閱讀

- **[Ollama 官方文件](https://github.com/ollama/ollama/blob/main/README.md)** — 模型清單、API 文件、進階設定
- **[LangChain 安裝指南](https://python.langchain.com/docs/get_started/installation/)** — 套件拆分說明、版本相容性
- **[ChromaDB Getting Started](https://docs.trychroma.com/getting-started)** — 向量資料庫基礎操作

---

→ 下一章：[Ch 1 — LLM 運作原理](./01-llm-internals.md)
