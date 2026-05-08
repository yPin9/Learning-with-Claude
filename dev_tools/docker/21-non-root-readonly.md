# Ch 21 — 非 root 與 Read-only

> 目標：理解容器跑 root 的真實風險，學會用 USER 指令、`--read-only`、`--no-new-privileges` 三道防線把攻擊面縮到最小，並寫出一份符合生產標準的安全 Dockerfile。

## 為什麼容器裡的 root 很危險

大部分人第一次寫 Dockerfile 不加 `USER`，結果 app 以 root 身份跑。這在沒有 user namespace 的標準 Docker 環境裡，意味著：

```
容器裡的 UID 0 = host 上的 UID 0
```

Docker 的隔離靠 namespace，不靠 user namespace（預設不啟用）。namespace 只是讓 root 看不到 host 的 process tree，但 **UID 是同一張 kernel table**。

攻擊路徑：

```
[容器 root] --> container escape (CVE 或 mount 錯誤)
                     |
                     v
              [host root shell]
              可以讀 /etc/shadow、kill 其他容器、掛載 host disk
```

不是理論——歷史上真實的 container escape 幾乎都需要容器裡有 root 才能完整利用。非 root 容器被 escape，拿到的是低權限 shell，傷害大幅降低。

## USER 指令

Dockerfile 裡指定 `USER` 讓 image 在非特權身份下跑：

```dockerfile
FROM python:3.12-slim

# 建立系統用戶，-r = system user（沒有 login shell、沒有 home）
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# 把需要寫入的目錄先 chown
RUN chown -R appuser:appuser /app

USER appuser

CMD ["python", "app.py"]
```

驗證：

```bash
docker build -t myapp .
docker run --rm myapp whoami
# 輸出: appuser
```

如果直接跑：

```bash
docker run --rm python:3.12-slim whoami
# 輸出: root   <- 預設就是這樣
```

### 常見問題：app 需要寫入特定目錄

切換到 `appuser` 後，原本 root 建的目錄 appuser 沒有寫入權。解法是在切換 USER **之前** 做 `chown`：

```dockerfile
RUN mkdir -p /app/logs /app/tmp \
    && chown -R appuser:appuser /app/logs /app/tmp

USER appuser
```

如果用既有 image（例如 `nginx:alpine`），nginx 有自己的 user，Dockerfile 通常不需要額外處理，但要確認：

```bash
docker run --rm nginx:alpine id
# uid=0(root) gid=0(root)  <- nginx master process 還是 root（為了 bind port 80）
# worker process 才是 nginx user
```

這種情況下 `--no-new-privileges` 尤其重要（後面說）。

## --read-only：讓 container filesystem 只讀

`--read-only` 把容器的 root filesystem 設成唯讀。任何試圖寫入 `/` 下的操作都會噴 `Read-only file system`：

```bash
docker run --rm --read-only alpine sh -c "echo test > /tmp/x"
# sh: can't create /tmp/x: Read-only file system
```

這樣做的好處：即使 app 被 RCE，攻擊者沒辦法寫入 webshell、沒辦法修改 binary、沒辦法留後門。

### 需要 tmpfs 打補丁的目錄

大多數 app 在唯讀環境會炸，因為它們假設能寫入某些目錄：

| 目錄 | 常見用途 |
|------|----------|
| `/tmp` | 暫存檔，幾乎所有 app 都用 |
| `/var/run` | PID file、Unix socket |
| `/var/log` | log 輸出（建議 app 改寫 stdout） |
| `/run` | systemd-style runtime data |
| app 的 upload 目錄 | 上傳檔案暫存 |

用 `--tmpfs` 給特定目錄在記憶體裡建一個暫時可寫的 tmpfs：

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp:size=64m,mode=1777 \
  --tmpfs /var/run:size=8m \
  myapp
```

`size=64m` 限制大小，`mode=1777` 等同 sticky bit（像正常的 /tmp 權限）。

Compose 寫法：

```yaml
services:
  app:
    image: myapp
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
      - /var/run:size=8m
```

### 診斷哪些地方會炸

先不加 `--read-only` 用 `strace` 或 `auditd` 觀察 app 會寫入哪些路徑，或直接加上去看錯誤訊息：

```bash
docker run --read-only myapp 2>&1 | grep "Read-only file system"
```

找出所有需要寫入的路徑，統統用 tmpfs 補。外部持久化資料（database、上傳檔案）改用 volume mount。

## --no-new-privileges

這個 flag 禁止容器內的 process 取得超過啟動時 token 的 privilege。具體來說，它設定 `PR_SET_NO_NEW_PRIVS` 這個 kernel bit，讓 `execve()` 時的 setuid/setgid binary 無法提升權限。

沒有這個 flag 的場景：

```
容器裡有一個 setuid root 的 binary
攻擊者執行它 -> 得到 root -> 各種 capability
```

加了之後：

```bash
docker run --no-new-privileges --user appuser myapp
# setuid binary 執行後 UID 不變，仍是 appuser
```

驗證：

```bash
# 先看 ping 是否為 setuid
docker run --rm alpine ls -la /bin/ping
# -rwsr-xr-x (s = setuid bit)

# 沒有 --no-new-privileges，非 root 可以跑 ping
docker run --rm --user 1000 alpine ping -c1 127.0.0.1

# 加了 --no-new-privileges，setuid 被忽略，ping 需要 NET_RAW capability
docker run --rm --user 1000 --no-new-privileges alpine ping -c1 127.0.0.1
# ping: permission denied (raw socket needs NET_RAW)
```

## 完整安全 Dockerfile 範例（FastAPI 服務）

```dockerfile
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime stage ----
FROM python:3.12-slim

# 建立非特權用戶
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# 複製依賴
COPY --from=builder /install /usr/local

# 複製 app 程式碼，直接設定正確擁有者
COPY --chown=appuser:appuser . .

# app 目錄權限：owner 可讀可執行，不可寫（read-only 配合）
RUN chmod -R 550 /app

# 切換用戶
USER appuser

# log 寫到 stdout，不寫檔
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

跑起來，三道防線全開：

```bash
docker run -d \
  --name fastapi \
  --read-only \
  --tmpfs /tmp:size=64m,mode=1777 \
  --no-new-privileges \
  -p 8000:8000 \
  myapp:latest
```

## trivy 掃描驗證

trivy 除了掃 CVE，也能掃 Dockerfile / image 設定問題：

```bash
# 安裝 trivy（Linux）
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh \
  | sh -s -- -b /usr/local/bin

# 掃 image 設定（不是 CVE，是 misconfig）
trivy image --scanners misconfig myapp:latest
```

沒加 USER 的 image 會看到：

```
MEDIUM   Dockerfile Security Check
         Specify at least 1 USER command in Dockerfile with non-root user as argument
         avd.aquasecurity.github.io/docs/avd/ds002/
```

加了 USER 後這條警告消失。拿官方 image 比較：

```bash
# 官方 python:3.12 有 USER 問題
trivy image --scanners misconfig python:3.12

# 自己加了 USER 的 image
trivy image --scanners misconfig myapp:latest
```

## 概念總結

| 防線 | 防什麼 | 指令 / 設定 |
|------|--------|------------|
| 非 root USER | escape 後只有低權限 shell | `USER appuser`（Dockerfile） |
| `--read-only` | 無法寫入 webshell、竄改 binary | `docker run --read-only` |
| `--tmpfs` | 給需要寫入的目錄開記憶體空間 | `--tmpfs /tmp:size=64m` |
| `--no-new-privileges` | setuid binary 無法提升 UID | `docker run --no-new-privileges` |

這四個不是選一個，是全部同時加。

## 自我檢核

- [ ] 能說明為什麼標準 Docker 下容器 UID 0 等於 host UID 0
- [ ] 能在 Dockerfile 正確建立系統用戶並在切換前完成 chown
- [ ] 知道 `--read-only` 後哪些目錄需要 `--tmpfs`，以及 size/mode 選項
- [ ] 能用 `--no-new-privileges` 跑容器並解釋它設定的 kernel bit
- [ ] 能用 trivy `--scanners misconfig` 找出 Running as root 的警告
- [ ] 能寫出 multi-stage + non-root + read-only + no-new-privileges 完整 Dockerfile

下一章往 capability 系統深挖——光是非 root 還不夠，容器預設繼承了二十幾個 capability，每個都是潛在武器。

→ [Ch 22 Capabilities 限制](./22-capabilities-drop.md)
