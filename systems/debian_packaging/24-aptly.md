# Ch 24 — aptly：現代 repo 管理工具

> 目標：理解 aptly 相比 reprepro 的設計哲學（snapshot 版本控制），會用 aptly 建立、快照、發布、更新 repo，以及從 mirror 同步上游套件。

## reprepro vs aptly

| 功能 | reprepro | aptly |
|------|---------|-------|
| snapshot（版本鎖定）| 不支援 | 核心功能 |
| mirror 上游 repo | 有限支援 | 完整支援 |
| merge 多個 repo | 不支援 | 支援 |
| REST API | 無 | 有（aptly serve）|
| 設計哲學 | 簡單直接 | 可重現、版本化 |

**什麼時候用 aptly**：
- 需要把某個時間點的套件集「凍結」給 production 用
- 需要 mirror 上游（例：把 Ubuntu 官方 repo 的子集複製到內網）
- 需要多個 repo 合併（自建套件 + Ubuntu main）

## 安裝

```bash
# 加 aptly 官方 repo（aptly 版本比 Ubuntu 官方 repo 新）
curl -fsSL https://www.aptly.info/pubkey.txt \
  | sudo gpg --dearmor -o /etc/apt/keyrings/aptly.gpg

echo "deb [signed-by=/etc/apt/keyrings/aptly.gpg] \
  http://repo.aptly.info/ squeeze main" \
  | sudo tee /etc/apt/sources.list.d/aptly.list

sudo apt update && sudo apt install aptly

# 或直接從 Ubuntu 裝（版本略舊但夠用）
sudo apt install aptly
```

## aptly 的核心概念

```
Local Repo    ← 你加入的 .deb
     ↓
  Snapshot    ← 某個時間點的不可變快照
     ↓
 Published    ← 發布到 filesystem 或 S3，apt 可以用
```

每次更新 repo 都要走這個流程，不能直接修改已發布的 snapshot。

## Step 1：建立 GPG key

```bash
gpg --full-generate-key
# 記下 key ID
KEYID="AABBCCDD11223344"
```

## Step 2：建立 local repo

```bash
# 建立名為 myorg-internal 的 local repo
aptly repo create -distribution=jammy -component=main myorg-internal

# 加入 .deb
aptly repo add myorg-internal /path/to/sysinfo_1.0-1_amd64.deb

# 查看內容
aptly repo show myorg-internal
# Name: myorg-internal
# Number of packages: 1
```

## Step 3：建立 snapshot

```bash
# 從 local repo 建立 snapshot（此時刻的不可變快照）
aptly snapshot create myorg-2025-05-11 from repo myorg-internal

# 列出所有 snapshot
aptly snapshot list

# 查看 snapshot 內容
aptly snapshot show myorg-2025-05-11
```

## Step 4：發布 snapshot

```bash
# 發布到本地 filesystem
aptly publish snapshot \
  -gpg-key=$KEYID \
  -distribution=jammy \
  myorg-2025-05-11

# 查看發布後的目錄（預設在 ~/.aptly/public/）
ls ~/.aptly/public/

# 目錄結構和標準 Debian repo 相同
# dists/jammy/InRelease
# pool/main/...
```

```bash
# 用 nginx 提供服務（指向 ~/.aptly/public/）
sudo cat > /etc/nginx/sites-available/aptly-repo << 'EOF'
server {
    listen 80;
    root /home/user/.aptly/public;
    autoindex on;
}
EOF
```

## Step 5：更新 repo

aptly 的更新流程強制你建立新 snapshot，這樣舊的 production 環境仍然指向舊 snapshot：

```bash
# 1. 加新版套件到 local repo
aptly repo add myorg-internal /path/to/sysinfo_2.0-1_amd64.deb

# 2. 建立新 snapshot
aptly snapshot create myorg-2025-06-01 from repo myorg-internal

# 3. 切換發布（從舊 snapshot 切到新 snapshot）
aptly publish switch jammy myorg-2025-06-01

# 客戶端 apt update 後就會看到新版
```

舊 snapshot `myorg-2025-05-11` 還在，可以用來還原：

```bash
# 還原到舊版本
aptly publish switch jammy myorg-2025-05-11
```

## Mirror 上游 repo

```bash
# Mirror Ubuntu jammy main（只抓 amd64）
aptly mirror create \
  -architectures=amd64 \
  -filter="Priority (required) | Priority (important) | Name (curl)" \
  ubuntu-jammy-main \
  http://archive.ubuntu.com/ubuntu \
  jammy \
  main

# 更新 mirror（實際下載套件）
aptly mirror update ubuntu-jammy-main

# 查看 mirror 內容
aptly mirror show ubuntu-jammy-main

# 從 mirror 建 snapshot
aptly snapshot create ubuntu-jammy-2025-05-11 from mirror ubuntu-jammy-main
```

`-filter` 支援查詢語法，用來只抓需要的套件而非整個 Ubuntu：

```bash
# 只抓特定套件和它的依賴
aptly mirror create \
  -filter="Name (nginx) | Name (libssl3)" \
  -filter-with-deps \
  ubuntu-jammy-nginx \
  http://archive.ubuntu.com/ubuntu \
  jammy \
  main
```

## Merge：合併多個 snapshot

```bash
# 把自建套件 + Ubuntu 精選套件合成一個 snapshot
aptly snapshot merge \
  myorg-combined-2025-05-11 \
  myorg-2025-05-11 \
  ubuntu-jammy-2025-05-11

# 發布合併後的 snapshot
aptly publish snapshot \
  -gpg-key=$KEYID \
  -distribution=jammy \
  myorg-combined-2025-05-11
```

## aptly serve：REST API 模式

```bash
# 啟動 HTTP API 服務（不是 repo 的 HTTP，而是管理 API）
aptly api serve -listen=:8080

# API 操作範例
# 列出 repo
curl http://localhost:8080/api/repos

# 新增套件（multipart upload）
curl -X POST -F "file=@sysinfo_1.0-1_amd64.deb" \
  http://localhost:8080/api/files/sysinfo

curl -X POST \
  http://localhost:8080/api/repos/myorg-internal/file/sysinfo

# 建立 snapshot
curl -X POST -H "Content-Type: application/json" \
  -d '{"Name":"myorg-2025-05-11"}' \
  http://localhost:8080/api/repos/myorg-internal/snapshots
```

這個 API 讓 CI/CD 可以直接透過 HTTP 推 deb 進 repo，不需要 SSH 進機器跑 aptly 命令。

## aptly vs reprepro 選哪個

```
簡單場景（公司內部幾個套件）：
  → reprepro（設定簡單，學習成本低）

複雜場景（需要以下任一）：
  - production/staging 環境指向不同版本 → aptly snapshot
  - mirror 上游 repo 部分內容          → aptly mirror
  - CI/CD 透過 API 管理 repo           → aptly api serve
  - 多個 repo 來源合併                 → aptly snapshot merge
```

## 自我檢核

- [ ] aptly 的核心流程：Local Repo → Snapshot → Published
- [ ] snapshot 是不可變的；更新要建新 snapshot + `publish switch`
- [ ] `aptly mirror create + update` 從上游同步；`-filter` 限制套件範圍
- [ ] `aptly snapshot merge` 把多個來源合成一個發布點
- [ ] `aptly api serve` 提供 REST API 讓 CI 推 deb

→ [Ch 25 CI 自動打包推送](./25-ci-packaging.md)
