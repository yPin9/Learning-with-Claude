# Ch 19 — 映像與供應鏈：image 掃描與 layer 裡的 secrets

> **目標**：從攻擊者的角度拆解 OCI image 的 layer 結構，說明為什麼「刪掉就沒事」是錯的，演示如何從公開 image 的 history 挖出 secrets，理解 typosquatting / 惡意 base image 的威脅模型，並建立一套實際可用的掃描與防禦流程。
>
> **環境**：Docker 27.x，trivy v0.54+（**需安裝**），dive 0.12+（**需安裝**）。掃描範例在本機 Linux / macOS / WSL 可跑；docker history、docker pull 只需要 Docker daemon 運作。

Ch 18 把 runtime escape 打完了——從 runc CVE 到 kernel 逃逸，重點是「容器跑起來之後攻擊者能做什麼」。這章轉向另一條攻擊鏈：**image 本身就是弱點**。攻擊者甚至不需要入侵 runtime，只要拉到有問題的 image，secrets 和 CVE 就都在裡面等著被撿。

---

## 為什麼需要

容器的工作流程讓供應鏈成為一個被低估的攻擊面：

**「pull 到 = 信任」是預設假設，但這個假設不成立。**

- 你 pull 的 base image 可能含有你不知道的 CVE——幾個月前建的 `ubuntu:22.04` 和今天的已經不一樣。
- Dockerfile 裡有人寫過 `curl -H "Authorization: Bearer $TOKEN" ...`，後來「乾淨地刪掉了」，但 layer 裡仍有紀錄，任何人 pull 這個 image 都看得到。
- Docker Hub 上的 `nignx:latest`（不是 `nginx`，少一個字）有 2,000 次 pull 記錄，裡面跑了一支 reverse shell。
- 你公司的 private registry 預設 HTTP、允許 anonymous push：任何內網人都能推一個惡意 image 覆蓋掉你的 `api-server:latest`。

這些不是理論。2021 年 Codecov breach、2022 年多起 PyPI/npm 投毒事件的容器版本都是同一個攻擊面的不同表現。

---

## 先建直覺：Layer 是 Git commit，刪除是謊言

OCI image（Open Container Initiative image，開放容器格式映像）的結構最好用 Git 類比：

```
OCI Image = 有序 layer 堆疊 + config JSON + manifest

manifest.json          ← 指向各 layer 的 sha256 digest 列表
config.json            ← 環境變數、CMD、ENTRYPOINT、build history
layer_0.tar.gz  ──┐
layer_1.tar.gz    │   ← 每層是前一層的 tar.gz diff（新增/修改/刪除標記）
layer_2.tar.gz    │
layer_3.tar.gz  ──┘

聯集所有層 = 最終 filesystem
```

每條 `RUN`、`COPY`、`ADD` 都產生一個新 layer，舊 layer **永遠不會被改寫**。

```
FROM ubuntu:22.04                   ← layer 0：base
RUN apt-get install -y curl         ← layer 1：+/usr/bin/curl 等等
RUN curl -H "Authorization: Bearer ghp_abc123" \
    https://api.example.com/config > /app/config.json   ← layer 2：+/app/config.json
RUN rm /app/config.json             ← layer 3：刪除標記（whiteout file）

# 最終 filesystem：看不到 /app/config.json
# 但 layer 2 的 tar.gz 裡 /app/config.json 還在
# docker history --no-trunc 裡 layer 2 的指令含 "ghp_abc123"
```

這和 Git 完全一樣：你 `git rm` 掉一個含密碼的檔案，commit 還在，歷史裡還能 checkout 到那個狀態。Layer 是 content-addressable，一旦 push 到 registry，任何拉到這個 image 的人都看得到所有 layer。

---

## 底層機制

### OCI Manifest 與 digest pinning

Registry 回傳的 manifest（清單）含每個 layer 的 sha256 digest：

```json
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
  "config": {
    "mediaType": "application/vnd.docker.container.image.v1+json",
    "size": 7023,
    "digest": "sha256:b5a4d0e83e..."
  },
  "layers": [
    {
      "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",
      "size": 29127289,
      "digest": "sha256:2408cc74d1..."
    },
    ...
  ]
}
```

`FROM ubuntu:22.04` 是 tag，tag 是可變的——今天的 `ubuntu:22.04` 和三個月後 Docker Hub 更新的 `ubuntu:22.04` 可以是不同 digest。用 tag pull，等於允許 image 被替換。

`FROM ubuntu@sha256:2408cc74d1...` 是 digest pin，這個 image 是不可變的。只要 digest 一樣，內容保證一樣。這是防惡意 base image 替換的最小措施。

### Whiteout files

OCI spec 規定刪除一個檔案的方式：在上層 layer 放一個 `.wh.<filename>` 的空檔案，container runtime 解壓時看到這個標記就跳過下層同名檔案。但**下層 layer 的 tar.gz 本身沒有被碰**。

用 `docker save` 把 image 存成 tar，解開後可以直接翻每個 layer 的 tar.gz：

```bash
docker save myapp:latest | tar -x -C /tmp/image-layers/
# 每個 layer 是一個子目錄，裡面有 layer.tar
ls /tmp/image-layers/
# sha256:abc.../layer.tar  sha256:def.../layer.tar  ...
tar -xf /tmp/image-layers/sha256:def.../layer.tar -C /tmp/layer-def/
ls /tmp/layer-def/app/
# 刪掉的檔案還在這裡
```

---

## 具體範例

### 範例一：從 docker history 挖出 secrets

建一個會洩漏 secret 的 image：

```dockerfile
# Dockerfile.leak (示範用途，別真的這樣寫)
FROM ubuntu:22.04

# 用環境變數注入 token，build 結束後刪掉——以為這樣安全
ARG GITHUB_TOKEN
RUN apt-get update -qq && apt-get install -y curl -qq
RUN curl -s -H "Authorization: token ${GITHUB_TOKEN}" \
    https://api.github.com/user > /tmp/github_user.json \
    && cat /tmp/github_user.json >> /app/build_info.txt \
    && rm /tmp/github_user.json
COPY app/ /app/
CMD ["/app/server"]
```

Build 時傳入真實 token：

```bash
docker build \
  --build-arg GITHUB_TOKEN=ghp_XXXXXXXXXXXXXXXXXXXX \
  -t myapp:latest \
  -f Dockerfile.leak .
```

現在攻擊者拿到這個 image，完全不需要 RCE，只需要 `docker history`：

```bash
docker history --no-trunc myapp:latest
```

輸出（節錄）：

```
IMAGE          CREATED       CREATED BY                                      SIZE
sha256:a1b2…  2 hours ago   CMD ["/app/server"]                              0B
sha256:3c4d…  2 hours ago   COPY app/ /app/ # buildkit                      1.2MB
sha256:5e6f…  2 hours ago   RUN /bin/sh -c curl -s -H "Authorization: tok…  0B
<missing>      2 hours ago   RUN /bin/sh -c curl -s -H "Authorization: tok…  0B

# --no-trunc 版本，第三行完整指令：
# RUN /bin/sh -c curl -s -H "Authorization: token ghp_XXXXXXXXXXXXXXXXXXXX" \
#     https://api.github.com/user > /tmp/github_user.json \
#     && cat /tmp/github_user.json >> /app/build_info.txt \
#     && rm /tmp/github_user.json
```

`ghp_XXXXXXXXXXXXXXXXXXXX` 完整暴露在 history 裡。這不是 bug，這是 image 的設計——build 指令本來就是 metadata 的一部分。

**`ARG` 和 `ENV` 的差異**：`ARG` 的值不寫進環境變數，但寫進 `RUN` 指令就會出現在 history。正確做法是用 BuildKit secret mount（見「進階延伸」段落）。

### 範例二：用 trivy 掃 CVE 和 secrets

安裝 trivy（選其一）：

```bash
# macOS
brew install trivy

# Debian/Ubuntu
sudo apt-get install wget apt-transport-https gnupg
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | \
  sudo gpg --dearmor -o /usr/share/keyrings/trivy.gpg
echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] \
  https://aquasecurity.github.io/trivy-repo/deb generic main" | \
  sudo tee /etc/apt/sources.list.d/trivy.list
sudo apt-get update && sudo apt-get install trivy

# 或直接用 Docker（不需要 install）
docker run --rm aquasec/trivy image nginx:1.24
```

掃一個已知有洞的舊版 image：

```bash
trivy image --severity HIGH,CRITICAL nginx:1.24
```

實際輸出（節錄，數字來自 2024 Q1 的掃描結果）：

```
nginx:1.24 (debian 11.6)

Total: 47 (HIGH: 23, CRITICAL: 24)

┌───────────────┬────────────────┬──────────┬───────────┬──────────────────────┬───────────────┬──────────────────────────────────────────────────────────────┐
│    Library    │ Vulnerability  │ Severity │  Status   │   Installed Version  │ Fixed Version │                            Title                             │
├───────────────┼────────────────┼──────────┼───────────┼──────────────────────┼───────────────┼──────────────────────────────────────────────────────────────┤
│ curl          │ CVE-2023-38545 │ CRITICAL │ fixed     │ 7.74.0-1.3+deb11u11  │ 7.74.0-1.3+…  │ curl: SOCKS5 heap buffer overflow                            │
│               │                │          │           │                      │               │ https://avd.aquasec.com/nvd/cve-2023-38545                   │
├───────────────┼────────────────┼──────────┼───────────┼──────────────────────┼───────────────┼──────────────────────────────────────────────────────────────┤
│ libssl1.1     │ CVE-2023-0464  │ HIGH     │ fixed     │ 1.1.1n-0+deb11u4     │ 1.1.1n-0+…    │ openssl: Denial of service by excessive resource usage in …   │
│               │                │          │           │                      │               │ https://avd.aquasec.com/nvd/cve-2023-0464                    │
├───────────────┼────────────────┼──────────┼───────────┼──────────────────────┼───────────────┼──────────────────────────────────────────────────────────────┤
│ libc6         │ CVE-2023-4911  │ CRITICAL │ fixed     │ 2.31-13+deb11u7      │ 2.31-13+…     │ glibc: buffer overflow in ld.so leading to privilege         │
│               │                │          │           │                      │               │ escalation (Looney Tunables)                                  │
...
```

CVSS 分數（Common Vulnerability Scoring System，通用漏洞評分系統）7.0 以上是 HIGH，9.0 以上是 CRITICAL。掃出來的 `curl CVE-2023-38545` 是 Looney Tunables 同期的 SOCKS5 heap overflow，CVSS 9.8。

掃 secrets（同時找 layer 裡的 API key / 密碼）：

```bash
trivy image --scanners secret nginx:latest
```

掃本地 Dockerfile 和 compose file：

```bash
trivy fs .
```

輸出裡的 `Misconfigurations` 區塊會標出：`RUN curl ... $SECRET` 這類模式、沒有 USER 指令（以 root 跑）、ADD URL 指令（比 COPY 多一層信任面）。

### 範例三：dive 看 layer 差異（包含邊界案例）

**需安裝 dive**（`dive` 是開源 layer 瀏覽器，不是 Docker 內建的）：

```bash
# macOS
brew install dive

# Linux / WSL
wget https://github.com/wagoodman/dive/releases/download/v0.12.0/dive_0.12.0_linux_amd64.deb
sudo dpkg -i dive_0.12.0_linux_amd64.deb
```

互動式瀏覽：

```bash
dive nginx:latest
```

dive 的 TUI 介面：左半部是 layer 列表（含每層大小、指令），右半部是 diff（綠色 = 新增，紅色 = 刪除）。切換到有 `rm` 的 layer 時，右半部會顯示「那個 whiteout 標記」，但如果你切換回上一層，被刪的檔案又出現了。

**邊界案例：trivy secret scan 的誤報率**

trivy 的 secret scanner 用 regex pattern 掃，遇到某些格式會誤報：

```bash
# 建一個含假 secret 的 image
echo 'STRIPE_KEY=sk_test_XXXXXXXXXXXXXXXXXXXXXXXXXXXX' > .env.example
trivy image --scanners secret myapp:latest
```

輸出可能把 `sk_test_` 開頭的測試格式 key 也標出來（Stripe publishable key 格式）。誤報不是不掃的理由，但要人工驗證 HIGH 以上的 finding，確認是真實 secret 還是範例字串。

trivy 也**不掃 BuildKit cache layer**：如果你的 CI 用 `--mount=type=cache`，那個 cache 不在 final image 裡，trivy 看不到——但如果你把 secret 放進 cache 然後讀出來寫進 image，結果還是會被掃到。

---

## 對比取捨表：選 base image 的策略

| 策略 | 攻擊面 | 工程成本 | 適合場景 |
|---|---|---|---|
| `FROM ubuntu:22.04`（latest tag）| 高：套件多、CVE 多、tag 可換 | 低 | 快速 PoC、開發環境 |
| `FROM ubuntu@sha256:abc123`（digest pin）| 中：固定版本但套件仍多 | 低 | 生產環境最低要求 |
| `FROM ubuntu:22.04-slim`（slim 變體）| 較低：移掉文件與部分工具 | 低 | 多數生產 workload |
| `FROM gcr.io/distroless/base`（distroless）| 很低：無 shell、無 package manager | 高：debug 困難 | 安全優先的 API server |
| `FROM scratch`（空 image）| 最低：只有你放進去的 | 最高：需要靜態 binary | Go / Rust 靜態 binary |
| multi-stage build + distroless final| 低：build 工具不進 final image | 中 | 最佳實踐，C/Go/Java 適用 |

distroless 的底層是 Debian，有 glibc 和 OpenSSL，但沒有 `/bin/sh`，沒有 `apt`，沒有 `wget`。攻擊者拿到 shell 後能做的事大幅縮減——沒有 package manager 就很難下載 tools，沒有 shell 就很難跑 reverse shell script。代價是：出問題時你也沒辦法 `docker exec ... /bin/bash` 進去看，只能靠日誌。

---

## 踩雷集錦

**1. ENV 的值進 config.json，不在 history 指令裡——但一樣看得到**

`docker history` 不會顯示 `ENV` 指令設的值，但 `docker inspect` 的 `Config.Env` 欄位會完整列出所有環境變數。攻擊者不需要看 history，只需要：

```bash
docker inspect myapp:latest | python3 -m json.tool | grep -A 20 '"Env"'
```

寫死在 Dockerfile 的 `ENV API_KEY=secret_value` 就這樣暴露了。

**2. `.dockerignore` 沒設，`.git/` 進了 image**

`COPY . /app` 這種寫法如果沒有 `.dockerignore`，整個 `.git/` 目錄會進 image。`.git/` 裡有 git history，有可能有過去 commit 過的 secrets。trivy fs 和 `git log -p` 都找得到。

**3. Digest pin 只 pin 了 manifest，base image 的 OS 套件 CVE 還是得掃**

`FROM ubuntu@sha256:xxx` 固定了你拉的是哪個快照，但那個快照裡的 glibc、curl 可能有已知 CVE。Pin 解決的是「被換掉」的問題，不解決「本來就有洞」的問題。掃描和 pin 是兩件事，兩個都要做。

**4. `docker login` 把 credential 寫成明文**

`docker login` 之後，`~/.docker/config.json` 儲存的是 base64 編碼的 `username:password`，**base64 不是加密**：

```bash
cat ~/.docker/config.json
# {"auths":{"https://index.docker.io/v1/":{"auth":"dXNlcjpwYXNz"}}}
echo "dXNlcjpwYXNz" | base64 -d
# user:pass
```

CI/CD 環境如果把這個檔案打包進 Docker context（`COPY . /app`），registry 密碼就洩了。解法：用 Docker credential helper（`docker-credential-secretservice`、`docker-credential-osxkeychain`）或 CI 環境的 secret injection，不要讓 `config.json` 進 image context。

**5. 私有 registry HTTP + anonymous push 是靜默的 supply chain 風險**

自架的 Docker registry（`registry:2` 預設）如果沒設 TLS 和 auth，內網任何人都能 `docker push myregistry:5000/api-server:latest` 覆蓋掉 production image。這不是「內網安全所以沒問題」，而是一旦有任何一台機器被滲透，攻擊者就能推惡意 image 等目標 pull。

---

## 進階延伸

### BuildKit secret mount：正確的 build-time secret 傳法

BuildKit（Docker 18.09+）的 `--mount=type=secret` 讓 secret 只在 `RUN` 指令執行期間掛進 container，**不寫進 layer**：

```dockerfile
# syntax=docker/dockerfile:1
FROM ubuntu:22.04
RUN --mount=type=secret,id=github_token \
    curl -s -H "Authorization: token $(cat /run/secrets/github_token)" \
    https://api.github.com/user > /app/build_info.txt
```

Build 時傳入：

```bash
docker buildx build \
  --secret id=github_token,src=~/.github_token \
  -t myapp:latest .
```

這樣 history 裡看到的是 `cat /run/secrets/github_token`，不是 token 的值，layer 裡也沒有 secret 的內容。

### 惡意 base image：typosquatting 的實際手法

攻擊者的策略不複雜：

```
nginx    → nignx, n1ginx, nginnx    (字母換位或替換)
python   → pythn, pythoon            (少字或多字)
node     → nodee, nod3              (數字替換)
ubuntu   → ubuntuu, ubunut          (重複字母)
```

Pull 時沒有任何警告——Docker 不驗證 image name 是否像「正常名稱」。唯一的辨別方式：

1. 看 publisher：Docker Hub 的 Official Images 有藍色盾牌，Publisher Catalog 顯示「Docker Official Image」
2. Pull count 和 star 數：冒充者通常 pull 數很少（但也有例外）
3. 看 `docker history`：官方 nginx 的 build 指令和惡意版本通常完全不同
4. digest pin：一旦你確認過是正確的 image，就 pin 那個 digest

### SBOM 生成

SBOM（Software Bill of Materials，軟體元件清單）列出 image 裡所有套件的名稱和版本，是供應鏈安全的基礎。trivy 可以生成：

```bash
trivy image --format cyclonedx --output sbom.json nginx:latest
# 或 SPDX 格式
trivy image --format spdx-json --output sbom.spdx.json nginx:latest
```

生成 SBOM 後可以送進 CSPM 工具或自建的 CVE tracking，不用每次掃都 pull image 比對。Image signing（Cosign / Notary）和 SBOM 是配套的供應鏈防護，詳見 Ch 32。

---

## 本章重點整理

- OCI image 是有序 layer 堆疊，layer 一旦建立就不可變，`RUN rm` 只是在上層加 whiteout 標記，下層 layer 的內容原封不動
- `docker history --no-trunc` 暴露所有 build 指令，包括含 secret 的 `curl` 指令；`docker inspect` 的 `Env` 欄位暴露 Dockerfile 裡的 ENV 值
- 正確的 build-time secret 傳法是 BuildKit `--mount=type=secret`，讓 secret 不進 layer
- 從 tag 拉 image 允許 image 被替換；digest pin（`FROM ubuntu@sha256:...`）是防止 base image 被調包的最小措施，但不解決 CVE 問題
- trivy 是主流的容器掃描工具，同時能掃 CVE（OS 套件 + 應用程式依賴）和 secrets；`--scanners secret` 才會啟動 secret 掃描
- dive 讓你互動式瀏覽 layer diff，確認每一層實際放了什麼、刪掉的東西下層是否還在
- distroless / scratch base image 和 multi-stage build 是縮減攻擊面的結構性手段
- `~/.docker/config.json` 儲存 base64 明文 credential，CI 環境需要用 credential helper 或 secret injection

---

## 自我檢核

1. OCI image 的 whiteout file 機制是什麼？為什麼 `RUN rm secret.txt` 無法真正刪除 secret？
2. `docker history --no-trunc` 和 `docker inspect` 各自洩漏哪些不同的 secret？
3. `FROM ubuntu:22.04` 和 `FROM ubuntu@sha256:2408...` 在安全性上的差異是什麼？pin digest 能解決哪些問題、不能解決哪些問題？
4. BuildKit `--mount=type=secret` 為什麼不把 secret 寫進 layer？和 `ARG` 傳 secret 的差異在哪？
5. trivy 不加 `--scanners secret` 時，預設掃什麼？加了之後多掃什麼？
6. distroless base image 縮減了哪些攻擊面？代價是什麼？

---

## 延伸閱讀

- [OCI Image Layout Specification](https://github.com/opencontainers/image-spec/blob/main/image-layout.md) — layer 結構、manifest、config 格式的 spec 原文，比任何二手解釋都精確
- [Aqua Security — Trivy 官方文件](https://aquasecurity.github.io/trivy/) — scanner 設定、CI 整合、SBOM 輸出格式的完整參考
- [Google Distroless — 設計說明與使用指引](https://github.com/GoogleContainerTools/distroless) — distroless 的 base image 選項、multi-stage build 範例、debug image 用法
- [CNCF Tag Security — Software Supply Chain Best Practices](https://github.com/cncf/tag-security/blob/main/supply-chain-security/supply-chain-security-paper/CNCF_SSCP_v1.pdf) — 供應鏈安全白皮書，涵蓋 SBOM / signing / policy 的完整框架
- [dive — GitHub README](https://github.com/wagoodman/dive) — 安裝、CI mode（`CI=true dive <image>` 可設 efficiency 閾值跑失敗）、與 docker-compose 整合

---

供應鏈的問題解決之後，下一個問題是：container 跑起來之後，OS 層的強制存取控制（Mandatory Access Control，MAC）能擋多少攻擊？seccomp profile 設錯、AppArmor profile 太寬鬆，或乾脆沒有 — runtime 防護的有效邊界在哪裡，以及攻擊者怎麼繞，Ch 20 拆開來看。

→ [Ch 20 Runtime 防護：seccomp / AppArmor / SELinux 怎麼被繞](./20-runtime-protection.md)
