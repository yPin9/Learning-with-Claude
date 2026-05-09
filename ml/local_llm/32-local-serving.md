# Ch 32 — 本地推論服務：llama-server / Ollama API

> 目標：把訓練或 fine-tune 好的模型包裝成一個可以持續服務的 HTTP API。

## 兩種方案的比較

| 方案 | 適用場景 | 優點 | 缺點 |
|------|---------|------|------|
| llama-server | 單一 GGUF 模型，需要細控 | 無需額外安裝，直接用 | 功能相對簡單 |
| Ollama API | 多模型管理，開發整合 | 介面友善，OpenAI 相容 | 對 LoRA adapter 支援有限 |

## 方案一：llama-server

```bash
# 啟動 llama-server
./build/bin/llama-server \
    -m models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --host 0.0.0.0 \
    --port 8080 \
    --ctx-size 4096 \
    --threads 8 \
    --chat-template qwen2 \
    --lora output/my-tw-lora.bin \  # 可選：載入 LoRA
    --api-key my-secret-key          # 可選：加 API key

# 啟動後會看到：
# HTTP server listening at http://0.0.0.0:8080
```

### 呼叫 llama-server API

```python
import requests

BASE_URL = "http://localhost:8080"

# OpenAI 相容的 /v1/chat/completions
def chat(messages, model="local", max_tokens=200, temperature=0.7):
    resp = requests.post(f"{BASE_URL}/v1/chat/completions",
        headers={"Authorization": "Bearer my-secret-key"},
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

# 測試
answer = chat([
    {"role": "system",  "content": "你是台灣工程師，用繁體中文回答。"},
    {"role": "user",    "content": "什麼是 KV-cache？"},
])
print(answer)
```

### 串流版本

```python
import json

def stream_chat(messages, temperature=0.7):
    resp = requests.post(f"{BASE_URL}/v1/chat/completions",
        json={"model": "local", "messages": messages, "stream": True, "temperature": temperature},
        stream=True,
        timeout=120,
    )
    for line in resp.iter_lines():
        if not line or line == b"data: [DONE]":
            continue
        line = line.decode('utf-8')
        if line.startswith("data: "):
            data = json.loads(line[6:])
            delta = data["choices"][0]["delta"]
            if "content" in delta:
                print(delta["content"], end="", flush=True)
    print()
```

## 方案二：Ollama API（整合自訂模型）

如果你 fine-tune 好並合併進 GGUF，可以匯入 Ollama：

```bash
# 把 LoRA 合併進模型
./build/bin/llama-export-lora \
    -m models/base-q4km.gguf \
    --lora output/my-tw-lora.bin \
    -o models/my-tw-model.gguf

# 建立 Modelfile
cat > Modelfile << 'EOF'
FROM ./models/my-tw-model.gguf

SYSTEM """
你是一個台灣資深工程師助理。
請全程使用繁體中文回答，保持專業且簡潔。
"""

PARAMETER temperature 0.7
PARAMETER top_k 40
PARAMETER top_p 0.9
PARAMETER num_ctx 4096
EOF

# 匯入 Ollama
ollama create tw-engineer -f Modelfile

# 測試
ollama run tw-engineer "什麼是 LoRA？"
```

## 建立一個簡單的 FastAPI 服務

把 Ollama 包裝成自己的 API 服務：

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import requests, json

app = FastAPI(title="地端 LLM API")

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "tw-engineer"

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    model: Optional[str] = DEFAULT_MODEL
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 512

@app.post("/chat")
def chat(req: ChatRequest):
    resp = requests.post(f"{OLLAMA_URL}/api/chat", json={
        "model": req.model,
        "messages": [m.dict() for m in req.messages],
        "stream": False,
        "options": {"temperature": req.temperature, "num_predict": req.max_tokens}
    }, timeout=120)

    if not resp.ok:
        raise HTTPException(500, f"Ollama error: {resp.text}")

    return {"reply": resp.json()["message"]["content"]}

@app.get("/models")
def list_models():
    resp = requests.get(f"{OLLAMA_URL}/api/tags")
    return [m["name"] for m in resp.json().get("models", [])]

# 啟動：uvicorn serving:app --host 0.0.0.0 --port 9000
```

## 健康監控

```python
import time

class ServingMonitor:
    def __init__(self):
        self.total_requests = 0
        self.total_tokens   = 0
        self.start_time     = time.time()

    def log_request(self, tokens_generated):
        self.total_requests += 1
        self.total_tokens   += tokens_generated

    def report(self):
        elapsed = time.time() - self.start_time
        rps = self.total_requests / elapsed
        tps = self.total_tokens   / elapsed
        print(f"運行時間: {elapsed:.0f}s")
        print(f"請求數:   {self.total_requests} ({rps:.2f} req/s)")
        print(f"Token 數: {self.total_tokens} ({tps:.1f} tok/s)")
```

## 並發處理

llama.cpp 和 Ollama 是**單執行緒**的——同一時間只能處理一個請求。多個同時來的請求會排隊。

如果需要更高吞吐，選項有：
1. **多個模型實例**：開多個 Ollama/llama-server 實例，用 nginx 做負載均衡
2. **vLLM**（需要 GPU）：支援真正的 continuous batching

CPU 上通常一個實例就夠了（瓶頸是計算，不是 I/O）。

## 動手練習

建立一個完整的本地 API 服務，包含：

```python
# serve.py
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import requests, json

app = FastAPI()

@app.post("/v1/chat/completions")
def completions(req: dict):
    """把 Ollama API 包裝成 OpenAI 格式"""
    # 轉換請求格式
    ollama_req = {
        "model": req.get("model", "qwen2.5:7b"),
        "messages": req["messages"],
        "stream": req.get("stream", False),
    }

    if req.get("stream"):
        def generate():
            resp = requests.post("http://localhost:11434/api/chat",
                json=ollama_req, stream=True)
            for line in resp.iter_lines():
                if line:
                    data = json.loads(line)
                    chunk = {"choices": [{"delta": {"content": data["message"]["content"]}}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(generate(), media_type="text/event-stream")

    resp = requests.post("http://localhost:11434/api/chat", json=ollama_req)
    msg = resp.json()["message"]["content"]
    return {"choices": [{"message": {"role": "assistant", "content": msg}}]}

# uvicorn serve:app --port 8000
```

## 自我檢核

- [ ] 能用 llama-server 啟動本地 API
- [ ] 用 Python requests 呼叫 /v1/chat/completions
- [ ] 實作串流輸出版本
- [ ] 把自訂 GGUF 模型匯入 Ollama 並能呼叫

→ [Ch 33 RAG 基礎：向量資料庫 + 檢索增強生成](./33-rag.md)
