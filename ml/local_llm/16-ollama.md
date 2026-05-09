# Ch 16 — Ollama 實戰：Modelfile / API / 換模型

> 目標：用 Ollama 管理和跑多個地端模型，並透過 API 從 Python 程式呼叫它。

## Ollama 是什麼

Ollama 是 llama.cpp 的「Docker 化」——它幫你管理 GGUF 模型的下載、版本、和執行，提供一個乾淨的 CLI 和 REST API：

```
你的程式  ──API──→  Ollama daemon  ──→  llama.cpp 推論引擎  ──→  GGUF 模型
```

和直接用 llama.cpp 相比：
- 不需要自己編譯
- 模型切換方便（一行命令）
- HTTP API 和 OpenAI SDK 相容

## 安裝

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows：下載 ollama-windows-amd64.exe 安裝程式
# https://ollama.com/download
```

安裝後 Ollama daemon 會在背景執行（port 11434）。

## 下載和跑模型

```bash
# 下載並跑 Llama 3.2 3B（首次會下載，約 2 GB）
ollama run llama3.2

# 下載但不馬上跑
ollama pull llama3.2
ollama pull qwen2.5:7b       # 繁體中文效果更好
ollama pull mistral:7b-instruct

# 查看已下載的模型
ollama list

# 刪除模型
ollama rm llama3.2

# 查看模型詳情
ollama show llama3.2
```

進入互動模式後，直接輸入問題。輸入 `/bye` 離開。

## 重要模型推薦（CPU 友善）

| 模型 | 大小 | 特點 |
|------|------|------|
| `llama3.2:3b` | 2 GB | 快，適合基本問答 |
| `qwen2.5:7b` | 4.7 GB | 繁體中文最佳，推薦 |
| `mistral:7b` | 4.1 GB | 英文強，邏輯好 |
| `phi3.5:mini` | 2.2 GB | 小模型中推理最強 |
| `nomic-embed-text` | 274 MB | embedding 模型（Ch 33 用） |

## Ollama REST API

Ollama 在 `http://localhost:11434` 提供 API：

```python
import requests

# 生成（非串流）
resp = requests.post("http://localhost:11434/api/generate", json={
    "model": "llama3.2",
    "prompt": "用一句話解釋什麼是梯度下降",
    "stream": False,
})
print(resp.json()["response"])

# 對話（chat）
resp = requests.post("http://localhost:11434/api/chat", json={
    "model": "llama3.2",
    "messages": [
        {"role": "system", "content": "你是一個台灣工程師，用繁體中文回答。"},
        {"role": "user",   "content": "什麼是 LoRA？"},
    ],
    "stream": False,
})
print(resp.json()["message"]["content"])
```

## OpenAI 相容 API

Ollama 也提供 OpenAI 格式的 endpoint，可以直接用 `openai` Python 套件：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",  # 隨便填，本地不驗證
)

response = client.chat.completions.create(
    model="qwen2.5:7b",
    messages=[
        {"role": "system", "content": "用繁體中文回答。"},
        {"role": "user",   "content": "PyTorch 和 TensorFlow 的差別是什麼？"},
    ],
)
print(response.choices[0].message.content)
```

這讓你用同一份程式碼，改個 `base_url` 就能切換 OpenAI API 和本地 Ollama。

## Modelfile：自訂模型行為

Modelfile 類似 Dockerfile，讓你定製模型的系統提示、溫度、停止詞等：

```dockerfile
# Modelfile
FROM qwen2.5:7b

# 設定系統提示
SYSTEM """
你是一個資深台灣後端工程師，專注於 Python 和系統設計。
回答請用繁體中文，條列式說明，保持簡潔。
"""

# 調整參數
PARAMETER temperature 0.7
PARAMETER top_k 40
PARAMETER top_p 0.9
PARAMETER num_ctx 4096
```

```bash
# 從 Modelfile 建立模型
ollama create my-tw-engineer -f Modelfile

# 跑自訂模型
ollama run my-tw-engineer

# 查看
ollama list  # 會出現 my-tw-engineer
```

## 串流輸出

用 API 時預設是串流（一個 token 一個 token 輸出），適合做互動介面：

```python
import requests
import json

resp = requests.post("http://localhost:11434/api/chat", json={
    "model": "qwen2.5:7b",
    "messages": [{"role": "user", "content": "解釋 Transformer 架構"}],
    "stream": True,
}, stream=True)

for line in resp.iter_lines():
    if line:
        data = json.loads(line)
        if not data.get("done"):
            print(data["message"]["content"], end="", flush=True)
print()  # 換行
```

## 動手練習

用 `qwen2.5:7b` 測試繁體中文輸出品質：

```bash
ollama run qwen2.5:7b
```

問以下三個問題，評估輸出：
1. 「請用繁體中文解釋什麼是注意力機制（Attention）」
2. 「寫一個 Python function 計算費氏數列」
3. 「台灣的 AI 發展現況是什麼？」

再換 `llama3.2:3b`，比較速度和品質的差異。

## 自我檢核

- [ ] 成功安裝 Ollama 並跑起一個模型
- [ ] 用 Python requests 呼叫 /api/chat 拿到回應
- [ ] 寫過一個 Modelfile 並建立自訂模型
- [ ] 比較過至少兩個模型的繁體中文輸出品質

→ [Ch 17 硬體估算：RAM / tokens-per-sec 怎麼算](./17-hardware-estimation.md)
