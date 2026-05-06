# Ch 26 — vLLM / Ollama 部署安全

> 目標：理解 Ollama 和 vLLM 各自的安全暴露面，掌握生產環境的安全設定，能設計帶認證和 TLS 的完整部署架構。

---

## 兩者定位比較

| 項目 | Ollama | vLLM |
|------|--------|------|
| 定位 | 開發機 / 輕量部署 | 生產環境 / 高吞吐量 |
| 安裝難度 | 一條指令搞定 | 需要 CUDA 環境，設定較多 |
| 內建認證 | 無 | 有（`--api-key`） |
| 預設監聽 | `0.0.0.0:11434` | `0.0.0.0:8000` |
| 吞吐量 | 低（單用戶流暢就好） | 高（continuous batching） |
| OpenAI 相容 API | 有 | 有 |
| 模型管理 | 內建 model pull/run | 啟動時指定模型 |
| GPU 支援 | CUDA / Metal / ROCm | CUDA 為主 |
| 適合場景 | 本機測試、PoC | API 服務、多用戶並發 |

---

## Ollama 安全設定

### 問題一：預設監聽 0.0.0.0

```bash
# 預設啟動後，任何能連到機器的人都能用
curl http://your-server:11434/api/generate \
  -d '{"model": "llama3", "prompt": "你好"}'

# 確認監聽位址
ss -tlnp | grep 11434
# 輸出：0.0.0.0:11434  ← 危險
```

**修正方式：綁定 127.0.0.1**

```bash
# 環境變數設定
OLLAMA_HOST=127.0.0.1:11434 ollama serve

# systemd service 設定
sudo systemctl edit ollama

# 在 override 裡加
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
```

綁定 127.0.0.1 後，只有本機上的程式能存取，外部流量走 nginx 等 reverse proxy。

### 問題二：沒有內建認證

Ollama 本身不做認證。正確做法是在前面加一層 proxy：

```
外部流量 → nginx（TLS + Basic Auth）→ Ollama（127.0.0.1）
```

```nginx
# /etc/nginx/sites-available/ollama
server {
    listen 443 ssl;
    server_name llm.internal.company.com;

    ssl_certificate     /etc/ssl/certs/llm.crt;
    ssl_certificate_key /etc/ssl/private/llm.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    # Basic Auth（簡單方案）
    auth_basic           "LLM Service";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # 或用 API key header 驗證
    # if ($http_x_api_key != "your-secret-key") {
    #     return 401;
    # }

    location / {
        proxy_pass         http://127.0.0.1:11434;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;

        # streaming 回應需要
        proxy_buffering    off;
        proxy_read_timeout 600s;
    }
}

server {
    listen 80;
    server_name llm.internal.company.com;
    return 301 https://$host$request_uri;
}
```

```bash
# 產生 htpasswd
htpasswd -c /etc/nginx/.htpasswd apiuser
```

### 問題三：模型來源管控

```bash
# 危險：從任意來源拉模型
ollama pull hacker/malicious-model

# 安全：只從 Ollama 官方 registry 拉，或用 digest 鎖定版本
ollama pull llama3.2:3b-instruct-q4_K_M
# 用 sha256 digest 固定版本（更嚴格）
ollama pull llama3.2@sha256:a80c4f17acd5...
```

建立私有 registry（進階）：

```bash
# 用 ollama 的 Modelfile 建立自訂模型並推到私有 registry
cat > Modelfile << 'EOF'
FROM llama3.2:3b
SYSTEM "你是公司內部助理，只回答公司業務相關問題。"
PARAMETER temperature 0.3
EOF

ollama create company-assistant -f Modelfile
# 推到私有 registry（需要自建 OCI registry）
ollama push registry.internal.company.com/company-assistant
```

---

## vLLM 安全設定

### 啟動參數安全配置

```bash
# 不安全的啟動方式
python -m vllm.entrypoints.openai.api_server \
    --model meta-llama/Llama-3.2-3B-Instruct

# 安全的啟動方式
python -m vllm.entrypoints.openai.api_server \
    --model meta-llama/Llama-3.2-3B-Instruct \
    --host 127.0.0.1 \               # 不對外直接暴露
    --port 8000 \
    --api-key "your-secret-api-key" \ # 啟用 token 認證
    --max-model-len 4096 \            # 限制 context 長度
    --max-num-seqs 32 \               # 限制並發請求數
    --disable-log-requests            # 不把使用者 prompt 記進 log
```

### `--max-model-len`：防止 Token Flooding DoS

```
攻擊場景：
攻擊者送一個 100K token 的 prompt → 消耗大量 GPU 記憶體和計算 → 服務降速或 OOM
```

```bash
# 設定合理的 context 上限
--max-model-len 4096    # 一般對話夠用
--max-model-len 8192    # 需要長文件時
--max-model-len 32768   # 只有真的需要才開這麼大
```

同時搭配 rate limiting（在 nginx 層做）：

```nginx
# nginx rate limiting
limit_req_zone $binary_remote_addr zone=llm:10m rate=10r/m;

location /v1/ {
    limit_req zone=llm burst=5 nodelay;
    limit_req_status 429;
    proxy_pass http://127.0.0.1:8000;
}
```

### TLS Termination：交給 reverse proxy

vLLM 本身不處理 TLS，讓 nginx 或 Caddy 來做：

```
Client → [HTTPS] → nginx（TLS termination + Auth）→ [HTTP] → vLLM（127.0.0.1）
```

```nginx
server {
    listen 443 ssl;
    server_name api.llm.company.com;

    ssl_certificate     /etc/letsencrypt/live/api.llm.company.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.llm.company.com/privkey.pem;

    # 驗證 Bearer token（轉發給 vLLM 驗證）
    location /v1/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Authorization $http_authorization;
        proxy_read_timeout 300s;
        proxy_buffering off;

        # 限制 request body 大小，防止超大 payload
        client_max_body_size 1m;
    }
}
```

---

## Model Registry 管控

```
風險：有人上傳含後門的模型到 Hugging Face，你的部署腳本直接拉來用
```

### Hash 驗證

```bash
# 下載模型後驗證 hash
sha256sum ./models/llama-3.2-3b.gguf
# 對比官方發布的 hash

# Python 腳本驗證
import hashlib

def verify_model(model_path: str, expected_sha256: str) -> bool:
    sha256 = hashlib.sha256()
    with open(model_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    actual = sha256.hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"Hash mismatch: {actual} != {expected_sha256}")
    return True
```

### 版本鎖定

```python
# 在部署設定裡鎖定模型版本，用 commit hash
MODEL_CONFIG = {
    "model_id": "meta-llama/Llama-3.2-3B-Instruct",
    "revision": "3a3a3503c6f2f7a7be90f0ef0d9e7a3b2e4d5c6d",  # 鎖定 git commit
}

from transformers import AutoModelForCausalLM
model = AutoModelForCausalLM.from_pretrained(
    MODEL_CONFIG["model_id"],
    revision=MODEL_CONFIG["revision"],  # 明確指定版本
)
```

---

## 網路隔離設計

生產環境的正確架構：LLM server 不應該直接面向 internet。

```
Internet
    │
    ▼
[Load Balancer / CDN]  ← 面向 internet，TLS termination
    │
    ▼
[API Gateway]          ← 認證、rate limiting、request logging
    │
    ▼
[Internal Network]──────────────────────────────────────┐
    │                                                    │
    ▼                                                    │
[Application Server]   ← FastAPI / LangChain           │
    │                                                    │
    ├──[Vector DB]      ← ChromaDB / Weaviate           │
    │                                                    │
    └──[LLM Server]     ← vLLM / Ollama（只在內網）    │
                                                        │
[Management Network]──────────────────────────────────┘
    │
    └── 監控、日誌收集、管理存取
```

這個設計的關鍵：LLM Server 沒有對外的 inbound 連線。攻擊者要打到 LLM，必須先過 Load Balancer → API Gateway → Application Server 三層。

---

## 完整 Docker Compose 範例

Ollama + nginx reverse proxy + API key 認證：

```yaml
# docker-compose.yml
services:
  ollama:
    image: ollama/ollama:latest
    environment:
      - OLLAMA_HOST=0.0.0.0:11434  # compose 網路內可以這樣
      - OLLAMA_MODELS=/models
    volumes:
      - ollama-models:/models
    networks:
      - ai-internal    # 只在 internal 網路，不暴露到外部
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
      interval: 30s
      timeout: 10s
      retries: 3

  nginx:
    image: nginx:1.27-alpine
    ports:
      - "443:443"   # 只暴露 HTTPS
      - "80:80"     # redirect to HTTPS
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
      - ./nginx/.htpasswd:/etc/nginx/.htpasswd:ro
    networks:
      - ai-internal  # 能連到 ollama
      - ai-external  # 能收外部請求
    depends_on:
      ollama:
        condition: service_healthy
    restart: unless-stopped

networks:
  ai-internal:
    internal: true   # 這個網路不通外部 internet
  ai-external:
    driver: bridge

volumes:
  ollama-models:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /data/ollama-models  # 掛到有足夠空間的磁碟
```

```nginx
# nginx/nginx.conf
events {
    worker_connections 1024;
}

http {
    # 不洩漏 nginx 版本
    server_tokens off;

    # 全域 rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;

    upstream ollama_backend {
        server ollama:11434;
        keepalive 32;
    }

    server {
        listen 80;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl;

        ssl_certificate     /etc/nginx/ssl/server.crt;
        ssl_certificate_key /etc/nginx/ssl/server.key;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;

        # API key 驗證（從 header 取）
        set $api_key_valid 0;
        if ($http_x_api_key = "your-production-api-key-here") {
            set $api_key_valid 1;
        }

        location /api/ {
            if ($api_key_valid = 0) {
                return 401 '{"error": "Unauthorized"}';
            }

            limit_req zone=api burst=10 nodelay;

            proxy_pass         http://ollama_backend;
            proxy_http_version 1.1;
            proxy_set_header   Connection "";
            proxy_read_timeout 300s;
            proxy_buffering    off;

            # 安全 header
            add_header X-Content-Type-Options nosniff;
            add_header X-Frame-Options DENY;
        }

        # 禁止直接存取模型管理 API
        location /api/pull {
            return 403 '{"error": "Forbidden"}';
        }
        location /api/delete {
            return 403 '{"error": "Forbidden"}';
        }

        location / {
            return 404;
        }
    }
}
```

測試設定是否正確：

```bash
# 無 API key 應該回 401
curl -k https://localhost/api/tags
# {"error": "Unauthorized"}

# 有正確 API key 應該成功
curl -k -H "X-API-Key: your-production-api-key-here" \
  https://localhost/api/tags

# 嘗試 pull 模型應該回 403
curl -k -H "X-API-Key: your-production-api-key-here" \
  -X POST https://localhost/api/pull \
  -d '{"name": "malicious-model"}'
# {"error": "Forbidden"}
```

---

## 自我檢核

- [ ] 我能解釋 Ollama 和 vLLM 各自適合的場景
- [ ] 我知道 Ollama 預設監聽 0.0.0.0 的風險，以及如何改掉
- [ ] 我能設計「nginx + Ollama」的 reverse proxy 架構並寫出設定
- [ ] 我知道 vLLM 的 `--api-key`、`--host`、`--max-model-len` 各自的用途
- [ ] 我理解 `--max-model-len` 如何防止 token flooding DoS
- [ ] 我知道模型 hash 驗證的必要性以及如何做
- [ ] 我能寫出包含網路隔離的 docker-compose.yml
- [ ] 我能解釋為什麼 LLM server 不應該直接面向 internet

到這裡，基礎設施安全的部分（Docker → K8s → LLM 部署）已經串起來了。最後的 Final Project 是把課程所有東西整合，做一次完整的 AI 資安評測。

→ [Final Project：AI 資安評測報告](./final-project-ai-security-assessment.md)
