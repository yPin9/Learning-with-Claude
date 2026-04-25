# Ch 5 — 容器安全與多平台建構

> 目標：把 `tasktrack` 的 Dockerfile 補上 non-root user 與 HEALTHCHECK；學會用 Buildx 建 linux/amd64 + linux/arm64 雙平台 image。

## 三件 Ch 2–4 刻意沒做、但生產一定要做的事

1. **容器不要以 root 跑** — 逃逸攻擊時你不想交出 host root
2. **加 HEALTHCHECK** — K8s / compose / Docker 要知道 container 是真的健康還是殭屍
3. **Build 多平台 image** — Apple Silicon 用戶會感謝你，CI runner 可能也需要

這章一個一個補進 tasktrack。

## Non-root user

### 為什麼預設是 root

`python:3.12-slim` 的預設 user 是 `root`（UID 0）。這意味：container 內的任何 process 都是 root。

為什麼危險：

- **Kernel 漏洞被利用 + Docker 逃逸**：直接是 host 的 root。已經發生過好幾次（CVE-2019-5736、CVE-2024-21626）
- **掛 volume 時檔案 owner 是 root**：host 使用者刪不掉
- **Security scanner（Trivy、Grype）會警告你**：合規會要求

### 解法：建 user、`USER` 切換

Dockerfile 尾巴加：

```dockerfile
# 在 runtime stage 加
RUN useradd -m -u 1000 -s /bin/bash appuser
USER appuser
```

或用更輕量的 UID-only 寫法（不建 home dir、只給 UID）：

```dockerfile
USER 1000
```

純 UID 夠用且 distroless 也能跑。但 `useradd` 那寫法比較習慣。

### 踩雷：檔案 ownership

`USER` 切換後，後續操作都是 `appuser`。如果之前的 `COPY` 是 root 擁有、而 appuser 又需要寫入（例如 log 檔），會權限錯。

解法：先 COPY 再 `chown`：

```dockerfile
COPY --chown=1000:1000 app/ app/
```

或更穩的是整個 `/app` 給 appuser：

```dockerfile
WORKDIR /app
COPY --from=builder --chown=1000:1000 /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY --chown=1000:1000 app/ app/

USER 1000

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 測試：`id` 確認 user

```bash
docker build -t tasktrack:v3 .
docker run --rm tasktrack:v3 id
# uid=1000 gid=1000 groups=1000
```

如果顯示 `uid=0(root)`，你 `USER` 指令沒生效。

### Port 限制提醒

非 root user **無法綁 1024 以下的 port**（linux capability 限制）。如果你想 `EXPOSE 80`，要嘛加 `CAP_NET_BIND_SERVICE` capability，要嘛用 > 1024 的 port（我們這用 8000，沒問題）。

## HEALTHCHECK

HEALTHCHECK 是一條寫在 Dockerfile 的指令，Docker / compose / K8s 會定期執行它來判斷 container 是否健康。

### 語法

```dockerfile
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/healthz || exit 1
```

四個參數：

| 參數 | 意義 |
|---|---|
| `--interval` | 每多久檢查一次 |
| `--timeout` | 單次 timeout |
| `--start-period` | 啟動寬限期（這期間失敗不計入） |
| `--retries` | 連續失敗幾次才標 unhealthy |

### 需要 healthz endpoint

但 `tasktrack` 目前沒有 `/healthz`。加一個：

```python
# app/main.py
@app.get("/healthz")
def healthz():
    return {"status": "ok"}
```

更嚴謹的 healthcheck 會實際連一下 DB，但簡單版夠用。

### 問題：image 裡有 curl 嗎

`python:3.12-slim` 沒裝 `curl`。三個選項：

1. **裝 curl**：`RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*`。多 ~10MB
2. **用 Python 自己打**：`CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()"]`
3. **用 `wget`**（有些 slim 有）

最乾淨是第 2 種（不用裝任何東西）：

```dockerfile
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()" || exit 1
```

### compose 用 Dockerfile 的 healthcheck

compose 會自動尊重 Dockerfile 裡的 HEALTHCHECK。也可在 compose 覆蓋：

```yaml
app:
  healthcheck:
    test: ["CMD", "python", "-c", "..."]
    interval: 10s
    ...
```

### 驗證

```bash
docker run -d -p 8000:8000 --name tt tasktrack:v3
sleep 5
docker ps                      # STATUS 欄會看到 (healthy) 或 (starting)
docker inspect --format='{{.State.Health.Status}}' tt
# healthy
```

## Multi-platform build

### 為什麼需要

- **Mac 開發者（Apple Silicon arm64）**：你在 x86 server build 的 image 他 pull 下來會透過 QEMU 模擬，慢得誇張
- **ARM server（Graviton、Ampere）**：越來越多雲端 ARM 機器，native image 比模擬快 3–5×
- **Raspberry Pi / 邊緣裝置**：通常是 arm64 或 armv7

目標：一個 tag 下同時提供 amd64 + arm64，pull 時 Docker 自動挑對的。

### `docker buildx` 登場

Buildx 是 Docker 的現代 builder，支援多平台。先建一個 builder：

```bash
docker buildx create --name multi --use
docker buildx inspect --bootstrap
# 會看到支援的平台：linux/amd64, linux/arm64, linux/arm/v7, ...
```

### Build + push 到 registry

**注意**：multi-platform build 不能只 load 到本地（本地只有一個 arch），**必須 push 到 registry** 或 `--output type=local`。我們這裡先模擬 push（`--push` 換成 `--load` 只能建單架構）：

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t tasktrack:v3-multi \
  --push \
  .
```

`--push` 會直接推到 registry（Ch 9、12 會正式配 GHCR）。這章先看機制、不要求你真的推。

### 只 build 本機架構的快速選項

日常開發不需要每次 multi-platform。預設 `docker buildx build --load` 只 build 當前架構並載入本地：

```bash
docker buildx build --load -t tasktrack:v3 .
# 等同 docker build
```

## 更新後的完整 Dockerfile

把所有東西整合：

```dockerfile
# ========== builder stage ==========
FROM python:3.12-slim AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ========== runtime stage ==========
FROM python:3.12-slim

COPY --from=builder --chown=1000:1000 /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY --chown=1000:1000 app/ app/

USER 1000

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

記得 `app/main.py` 加 `/healthz` endpoint。

## 其他安全 quick win

這章不展開，但提一下清單。你接 CI 後 Trivy 會幫你罵：

- **pin base image 到 digest**：`FROM python:3.12-slim@sha256:abc...` 比 tag 更穩，不會突然換
- **避免 `ADD` 遠端 URL**：用 `RUN curl`，至少你能 pin checksum
- **不要把 secret 寫在 ENV**：容器裡 `env` 指令就看得到。用 BuildKit secret mount（Ch 8 再談）
- **最小化 base image**：`slim` → `distroless` 是進階優化，不急

## distroless 預告

Google 的 distroless 系列 image 移除了 shell 與大部分 userland：

```dockerfile
FROM gcr.io/distroless/python3-debian12
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY app/ app/
USER 1000
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

好處：
- ~50MB，比 slim 小一倍
- 沒 `sh` / `bash`，攻擊面小

壞處：
- **debug 超痛**：`docker exec -it container bash` 進不去。只能用 `docker exec container /busybox/sh`（debug variant）或改用 debug image
- 某些 ENTRYPOINT 寫法會失效（沒 `/bin/sh`）

生產上值得，但 tasktrack 這課先停在 slim。

## 動手練習

1. 把 Dockerfile 改成本章最後那版（non-root + HEALTHCHECK + `/healthz`）
2. Build + run，跑 `docker ps` 看 `(healthy)` 狀態
3. `docker inspect --format='{{.State.Health.Status}}' <container>` 輸出 `healthy`
4. 故意把 `/healthz` endpoint 拿掉 rebuild、run，看 container 會被標成 unhealthy
5. 跑一次 `docker buildx build --platform linux/amd64,linux/arm64 -t tasktrack:v3-multi .`（不 `--push`，只看 build 能跑過）

## 常見誤解

- 「**USER 1000 就安全了**」 — 這是最低門檻。還要 read-only root filesystem、drop capabilities、seccomp profile。但 USER 是最大的 ROI
- 「**HEALTHCHECK 失敗會自動重啟**」 — Docker 本身不會。K8s / compose 的 restart policy 才會。但 load balancer 會停止導流 unhealthy container
- 「**multi-platform 要 QEMU 很慢**」 — cross-compile 情境下確實慢。有 native builder（例如 GitHub Actions 的 ARM runner）就快
- 「**distroless 沒 shell 所以最安全**」 — 小但不是銀彈。攻擊者如果 RCE 了，可以透過 `/proc/self/exe` 等機制搞事。要配合其他縱深防禦

## 驗收標準

- [ ] `tasktrack` Dockerfile 有 `USER 1000`、`HEALTHCHECK`
- [ ] `/healthz` endpoint 加在 `app/main.py`
- [ ] `docker run` 後 `docker ps` STATUS 顯示 `(healthy)`
- [ ] `docker run --rm tasktrack:v3 id` 顯示 `uid=1000` 不是 0
- [ ] 能成功跑 `docker buildx build --platform linux/amd64,linux/arm64 .`（不一定 push）

## 自我檢核

- [ ] 我知道為什麼 root container 危險、USER 1000 是最基本防線
- [ ] 我懂 HEALTHCHECK 的四個參數、compose 怎麼尊重它
- [ ] 我能解釋 multi-platform build 為什麼要 buildx 而不是 `docker build`
- [ ] 我知道 distroless 的取捨、為什麼這課暫時不上

Part 1 結束。你現在能寫出相當生產級的 Dockerfile。練習 A 把這些綜合起來。

→ [練習 A：把爛 Dockerfile 優化到生產等級](./practice-a-dockerfile-rescue.md)
