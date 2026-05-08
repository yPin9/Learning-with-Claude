# Ch 13 — .dockerignore 與 Build Context：別把整個硬碟傳給 daemon

> 目標：理解 build context 是什麼、太大會有什麼後果，掌握 `.dockerignore` 的完整語法，並能為 Node.js / Python / Go 專案寫出正確的 `.dockerignore`。

---

## Build Context 是什麼

`docker build .` 的那個 `.` 不只是「Dockerfile 在這個目錄」，它是 **build context（建置上下文）**：Docker CLI 把這整個目錄打包成一個 tar，通過 Unix socket 傳給 Docker daemon，然後 daemon 才開始執行 Dockerfile。

```
你的終端機                    Docker daemon
+-----------+                +-----------+
| docker    |  tar 打包      |           |
| build .   | ----------->  | 解包到暫存 |
|           |  /var/run/    | 目錄       |
+-----------+  docker.sock  | 執行 RUN  |
                            | COPY 從這裡|
                            +-----------+
```

幾個關鍵點：

1. **傳輸發生在 Dockerfile 開始執行之前**。就算你的 Dockerfile 只有 `COPY main.py .`，整個目錄都已經傳過去了。

2. **context 大小 = 每次 build 的傳輸量**。本機 build 還好（socket 傳輸快），但如果是 CI 服務（GitHub Actions、GitLab CI），build 在遠端 runner 上，context 是通過網路傳的。

3. **daemon 看不到 context 範圍之外的檔案**。`COPY ../other-dir/file .` 會失敗，因為 `..` 不在 context 裡。

---

## 實驗：Context 太大的後果

在一個有 `node_modules/` 的 Node.js 專案跑 build，觀察第一行輸出：

```bash
cd my-node-project  # node_modules 有幾百 MB
docker build .
# Sending build context to Docker daemon  847.3MB   <- 這就是問題
```

即使你的 Dockerfile 完全沒有 COPY node_modules：

```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json ./  # 只 COPY 這兩個
RUN npm ci
COPY src/ ./src/                         # 只 COPY src
CMD ["node", "src/index.js"]
```

那 847MB 還是全部傳過去了，只是傳過去之後 daemon 沒用到。每次 build 浪費一次這個傳輸，CI 每天幾十次 build，時間全燒在這裡。

---

## .dockerignore 語法

`.dockerignore` 放在 build context 的根目錄（通常就是專案根目錄，和 Dockerfile 同一層）。語法和 `.gitignore` 非常相似，但有幾個細節不同：

```
# 這是註解

# 精確路徑
node_modules/
__pycache__/

# wildcard（* = 單層，** = 任意深度）
*.log
**/*.pyc
**/tmp/

# 否定（! 開頭 = 排除的例外）
# 先排除所有 .env，然後允許 .env.example
.env*
!.env.example

# 排除所有 test 目錄
tests/

# 目錄本身不排除，但排除目錄裡的某些檔案
!src/
src/**/*.test.js
```

**和 .gitignore 的主要差異**：

| 特性 | .gitignore | .dockerignore |
|------|-----------|--------------|
| 多個檔案 | 可以在子目錄放 | 只有根目錄一個 |
| 否定規則 `!` | 支援 | 支援，但順序很重要 |
| 空行 | 忽略 | 忽略 |
| 尾部 `/` | 只匹配目錄 | 只匹配目錄 |
| `**` | 支援 | 支援 |
| Dockerfile 本身 | N/A | 不需要排除（daemon 不會 COPY 它，但寫了可以讓 context 更乾淨） |

---

## 標準 .dockerignore 模板

### Node.js

```
# 依賴（應該由 npm ci 重新安裝，不能直接 COPY）
node_modules/
npm-debug.log*
yarn-error.log
.yarn/cache
.pnp.*

# 版本控制
.git
.gitignore

# 環境變數（絕對不能進 image）
.env
.env.*
!.env.example

# 測試和覆蓋率
coverage/
.nyc_output
*.test.js
*.spec.js
__tests__/

# 開發工具設定
.eslintrc*
.prettier*
.editorconfig
.vscode/
.idea/

# 文件
README.md
CHANGELOG.md
docs/

# OS 垃圾
.DS_Store
Thumbs.db
```

### Python

```
# 虛擬環境
.venv/
venv/
env/
.env/
ENV/

# 編譯產物
__pycache__/
*.py[cod]
*$py.class
*.so
*.egg-info/
dist/
build/
.eggs/

# 版本控制
.git
.gitignore

# 環境變數
.env
.env.*
!.env.example

# 測試
.pytest_cache/
.coverage
htmlcov/
.tox/
tests/

# 工具設定
.mypy_cache/
.ruff_cache/
.flake8
pyproject.toml   # 視情況，如果只有開發設定的話

# 文件
*.md
docs/

# Jupyter
.ipynb_checkpoints/
*.ipynb

# OS
.DS_Store
```

### Go

```
# 版本控制
.git
.gitignore

# 測試結果
*.test
coverage.out
coverage.html

# 環境變數
.env
.env.*
!.env.example

# 工具設定
.golangci.yml
.air.toml

# 文件
*.md
docs/

# 開發工具
.vscode/
.idea/

# 暫存
tmp/
*.tmp

# OS
.DS_Store
```

---

## .env 的特殊地位

`.env` 值得特別說。

在 Git 裡，`.env` 通常被 `.gitignore` 排除，但如果你在某個時間點不小心 `git add .env`，它就進了 git history，你必須 `git filter-branch` 才能徹底移除。

在 Docker 裡更危險：就算你沒有 `COPY .env .`，如果沒有 `.dockerignore`，`.env` 還是會進到 build context 的 tar 裡傳給 daemon。一旦 daemon 在 CI 環境上，那個 tar 可能被 log、被 artifact store 保留。

更糟的情況：

```dockerfile
# 這行把 .env 裡的值燒進 image layer
ARG DATABASE_URL
ENV DATABASE_URL=$DATABASE_URL
```

任何 `docker history myapp:latest` 都能看到。

**規則**：`.dockerignore` 裡永遠要有 `.env` 和 `.env.*`，不管你的 Dockerfile 有沒有 COPY 它。

---

## context 和 Dockerfile 不在同一目錄

有時候 Dockerfile 放在 `docker/` 子目錄，但你想要的 context 是專案根目錄：

```bash
# -f 指定 Dockerfile 路徑，最後的參數是 context 目錄
docker build -f docker/Dockerfile.prod ./

# CI 常見的 monorepo 結構
docker build \
  -f services/api/Dockerfile \
  --build-arg SERVICE=api \
  ./services/api

# context 完全另一個地方
docker build -f /path/to/Dockerfile /path/to/context
```

`.dockerignore` 是相對於 build context 目錄的，不是相對於 Dockerfile 所在目錄。如果你用 `-f docker/Dockerfile.prod ./` 而 `.dockerignore` 在 `./`，那 `.dockerignore` 是有效的。

BuildKit 還支援一個進階功能：針對特定 Dockerfile 的 `.dockerignore`：

```
# 如果 Dockerfile 叫做 Dockerfile.prod
# 可以有對應的 Dockerfile.prod.dockerignore
# BuildKit 會優先使用這個，而非通用的 .dockerignore
```

---

## 驗證 Build Context 大小

```bash
# 方法一：直接看 build 輸出的第一行
docker build . 2>&1 | head -1
# Sending build context to Docker daemon  12.34kB   <- 理想
# Sending build context to Docker daemon  847.3MB   <- 要處理

# 方法二：手動打包看大小
tar --exclude-from=.dockerignore -czf - . | wc -c
# 輸出 bytes 數

# 方法三：列出 context 裡有哪些檔案（不實際 build）
docker build --no-cache --dry-run . 2>&1  # BuildKit 支援
```

---

## 自我檢核

- [ ] 能說清楚 build context 是什麼，以及「傳輸發生在 Dockerfile 執行之前」的意義
- [ ] 知道 node_modules 沒 COPY 進 Dockerfile，但 context 大小還是 800MB 的原因
- [ ] 能寫出 `.dockerignore` 的否定規則語法（`!` 的用法）
- [ ] 知道 `.env` 為什麼一定要在 `.dockerignore` 裡，即使 Dockerfile 沒有 COPY 它
- [ ] 能為 Node.js、Python、Go 專案各寫出一份合理的 `.dockerignore`
- [ ] 知道 `-f` 和 build context 目錄參數的關係，以及 `.dockerignore` 相對於哪個目錄

Build 優化完了，接下來是多個服務的協作——Compose 讓你用一個 YAML 管理整個開發環境。

→ [Ch 14 Compose 基礎](./14-compose-basics.md)
