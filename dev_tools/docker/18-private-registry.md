# Ch 18 — Registry 自架

> 目標：搞清楚為什麼要自架 registry，能用 `registry:2` 跑一個有 TLS 和 htpasswd 認證的私有 registry，知道 GHCR 什麼時候是更好的選擇。

---

## 為什麼要自架

| 需求 | 說明 |
|------|------|
| 私有 code | 不想把 image 推到公開的 Docker Hub |
| Air-gapped 環境 | 網路隔離的生產環境，不能連外部 registry |
| Pull 速度 | 本地 registry，pull 走內網，比走公網快 |
| 成本 | Docker Hub 免費方案只有 1 個 private repo，多了要付費 |
| 合規 | 某些行業規定資料不能離開特定網路邊界 |

**Docker Hub 免費方案的限制**（2024 年起）：

- Private repository：1 個
- Pull rate limit：未登入 100 次/6h，登入 200 次/6h（IP 計算）
- CI 環境常觸發 rate limit，用自架或 GHCR 解決

---

## 用 `registry:2` 自架

Docker 官方維護的 `registry:2` image 是最簡單的自架方案：

```yaml
services:
  registry:
    image: registry:2
    ports:
      - "5000:5000"
    volumes:
      - registry_data:/var/lib/registry
    restart: unless-stopped

volumes:
  registry_data:
```

```bash
docker compose up -d

# 確認跑起來
curl http://localhost:5000/v2/
# {}  <- 回傳空 JSON 表示 API 正常
```

---

## Push / Pull 私有 image

```bash
# 把現有 image 打上私有 registry 的 tag
docker tag myapp:v1 localhost:5000/myapp:v1

# Push
docker push localhost:5000/myapp:v1

# 在其他機器 pull（把 localhost 換成實際 IP 或 hostname）
docker pull 192.168.1.100:5000/myapp:v1

# 列出 registry 裡的 image
curl http://localhost:5000/v2/_catalog
# {"repositories":["myapp"]}

# 列出某個 image 的 tags
curl http://localhost:5000/v2/myapp/tags/list
# {"name":"myapp","tags":["v1"]}
```

---

## TLS 設定：沒有 TLS Docker 不接受

Docker daemon 預設不接受沒有 TLS 的遠端 registry（localhost 例外）。在其他機器上 push/pull 時你會遇到：

```
Error response from daemon: Get "https://192.168.1.100:5000/v2/":
http: server gave HTTP response to HTTPS client
```

有兩種解法：

**方法 A：加進 insecure-registries（快但不安全，只用在內網測試）**

編輯 `/etc/docker/daemon.json`：

```json
{
  "insecure-registries": ["192.168.1.100:5000"]
}
```

```bash
sudo systemctl restart docker
```

**方法 B：加 TLS（正確做法）**

準備好憑證（自簽或 Let's Encrypt），掛進 registry：

```yaml
services:
  registry:
    image: registry:2
    ports:
      - "443:443"
    environment:
      REGISTRY_HTTP_ADDR: 0.0.0.0:443
      REGISTRY_HTTP_TLS_CERTIFICATE: /certs/domain.crt
      REGISTRY_HTTP_TLS_KEY: /certs/domain.key
    volumes:
      - registry_data:/var/lib/registry
      - ./certs:/certs:ro

volumes:
  registry_data:
```

自簽憑證要在每台機器的 Docker 信任清單加入，比較麻煩。用 Let's Encrypt 省事，前提是 registry 有公開 domain name 和 80/443 port。

---

## htpasswd 認證

不加認證的 registry，任何人都可以 push/pull。加上 Basic Auth：

```bash
# 安裝 apache2-utils（提供 htpasswd 指令）
sudo apt-get install apache2-utils

# 建立 htpasswd 檔案，-B 是 bcrypt（比 MD5 安全）
mkdir -p auth
htpasswd -Bbn admin mysecretpassword > auth/htpasswd

# 新增更多使用者
htpasswd -Bb auth/htpasswd dev devpassword
```

把認證加進 compose.yml：

```yaml
services:
  registry:
    image: registry:2
    ports:
      - "5000:5000"
    environment:
      REGISTRY_AUTH: htpasswd
      REGISTRY_AUTH_HTPASSWD_REALM: Registry Realm
      REGISTRY_AUTH_HTPASSWD_PATH: /auth/htpasswd
    volumes:
      - registry_data:/var/lib/registry
      - ./auth:/auth:ro
    restart: unless-stopped

volumes:
  registry_data:
```

```bash
# 登入
docker login localhost:5000
# Username: admin
# Password: mysecretpassword

# 登入後 push/pull 正常操作
docker push localhost:5000/myapp:v1
```

登入憑證存在 `~/.docker/config.json`，base64 編碼（不是加密）。CI 環境不要把這個檔案提交進 git。

---

## GHCR（GitHub Container Registry）

如果你的 code 已經在 GitHub，GHCR（GitHub Container Registry）是更省事的選擇：

| 維度 | registry:2 自架 | GHCR |
|------|----------------|------|
| 維護成本 | 自己管 TLS、存儲、備份 | 零維護 |
| 費用 | 主機費用 | Free（public）/ 含在 GitHub 方案裡（private） |
| 整合 GitHub Actions | 要手動設定 | 內建 `GITHUB_TOKEN` 直接用 |
| Pull 速度 | 取決於主機位置 | GitHub CDN |
| Private image | 可 | 可（需要 GitHub 帳號有權限） |

用 GitHub Actions 自動 push 到 GHCR：

```yaml
# .github/workflows/docker.yml
- name: Log in to GHCR
  uses: docker/login-action@v3
  with:
    registry: ghcr.io
    username: ${{ github.actor }}
    password: ${{ secrets.GITHUB_TOKEN }}

- name: Build and push
  uses: docker/build-push-action@v5
  with:
    push: true
    tags: ghcr.io/${{ github.repository }}:latest
```

---

## Image GC（Garbage Collection）

Registry 不會自動刪舊 image，磁碟會慢慢長大。手動清理：

```bash
# 進入 registry container
docker compose exec registry /bin/registry garbage-collect \
    /etc/docker/registry/config.yml

# 如果 registry 在跑，加 --delete-untagged 刪除沒有 tag 的 layer
docker compose exec registry /bin/registry garbage-collect \
    --delete-untagged \
    /etc/docker/registry/config.yml
```

GC 只刪掉沒有被任何 tag 引用的 layer（blob）。要刪特定 tag，先用 Registry API 刪 manifest，再跑 GC：

```bash
# 取得 digest
curl -I -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    http://localhost:5000/v2/myapp/manifests/v1

# 刪 manifest（需要 registry 開啟 REGISTRY_STORAGE_DELETE_ENABLED=true）
curl -X DELETE http://localhost:5000/v2/myapp/manifests/<digest>
```

生產 registry 記得設定定期 GC 的 cron job，避免磁碟爆掉。

---

## 自我檢核

- [ ] 知道 Docker Hub 免費方案有哪些限制，以及觸發 pull rate limit 時怎麼處理
- [ ] 能從零跑起一個有認證的 `registry:2`，包括 htpasswd 的建立流程
- [ ] 知道 insecure-registries 的設定位置和它的安全風險
- [ ] 知道 GHCR 和自架 registry 各自適合什麼場景
- [ ] 知道 registry GC 要怎麼觸發，以及它清的是什麼

下一章把目光轉向 image 本身的安全性，拿 trivy 掃出那些你不知道自己帶進去的 CVE。

→ [Ch 19 映像掃描](./19-image-scanning.md)
