# Ch23: 大檔案策略

Git 不適合大檔（video、binary、dataset）。本章講實務選擇。

## 23.1 為什麼 git 怕大檔

- **所有歷史版本都存**：改 10 次 100MB 檔 = 1GB
- **每次 clone 都整包拉**：同事加入專案 clone 一小時
- **delta compression 對二進位效果差**：binary 很少有「相似」可壓
- **`git log` / `git diff` 變慢**

### 臨界值
- `< 10MB` 單檔：沒事
- `10MB - 100MB`：注意，常改的話考慮 LFS
- `> 100MB`：**GitHub 上限 100MB**，超過直接拒收
- Repo total > 1GB：考慮 partial clone、LFS、切 repo

## 23.2 解法總覽

| 問題 | 解法 |
|---|---|
| 偶爾有中等大小的檔 | 直接 commit（可接受） |
| 常改的中大檔（設計稿、model） | **Git LFS** |
| 超大但不常改（video 素材） | Git LFS 或外部 storage |
| Dataset / model | DVC / MLflow，不進 git |
| Build 產物 | `.gitignore`，不進 repo |
| 老 repo 已經膨脹 | `git filter-repo` 清歷史 |

## 23.3 Git LFS（Large File Storage）

### 概念
LFS 在 repo 裡存**指標檔**，實體檔存在 LFS server：
```
repo:
  design.psd     ← 其實是個 pointer 檔
  
LFS server:
  <hash>         ← 實體 PSD 檔
```

每次 `git checkout` LFS 自動拉對應實體。

### 安裝
```bash
# MSYS2
pacman -S git-lfs

# Ubuntu
sudo apt install git-lfs

# Mac
brew install git-lfs

# 一次性啟用（每台機器）
git lfs install
```

### 設定 LFS track
```bash
cd myrepo
git lfs track "*.psd"
git lfs track "*.mp4"
git lfs track "assets/*.zip"

# 產生 .gitattributes
cat .gitattributes
# *.psd filter=lfs diff=lfs merge=lfs -text
# *.mp4 filter=lfs diff=lfs merge=lfs -text

git add .gitattributes
git commit -m "Track large files with LFS"
```

### 加檔
```bash
cp /path/video.mp4 ./assets/
git add assets/video.mp4
git commit -m "Add video"
git push
# mp4 實體上傳到 LFS，repo 裡只是 pointer
```

### 看 LFS 檔
```bash
cat assets/video.mp4
# version https://git-lfs.github.com/spec/v1
# oid sha256:abc123...
# size 12345678

git lfs ls-files       # 列所有 LFS 追蹤檔
git lfs status         # 看 LFS 的 status
```

### Clone + fetch
```bash
git clone <url>
# Git 自動下載 LFS 指標，checkout 時自動拉實體

# 不要自動拉（只要指標）：
GIT_LFS_SKIP_SMUDGE=1 git clone <url>
# 之後手動
git lfs pull
```

### 費用
- **GitHub 免費**：1GB 儲存 + 1GB/month bandwidth
- 超過要付費（data packs）
- 其他平台（GitLab / Bitbucket / self-hosted）有自己限制

**大專案要估 LFS 成本**——常改的大檔每次 commit 都算新版本。

## 23.4 `.gitignore`：讓大檔根本不進 git

```
# Build
build/
dist/
*.o
*.exe

# Dependencies
node_modules/
vendor/

# Data
data/
*.csv
*.parquet

# Logs
*.log

# IDE
.vscode/
.idea/
```

如果檔已被追蹤，加 ignore 沒效果，要：
```bash
git rm --cached bigfile.dat
echo "bigfile.dat" >> .gitignore
git add .gitignore
git commit -m "Stop tracking bigfile"
```

歷史中**還是有**那檔。要完全清除看 23.6。

## 23.5 Shallow / partial clone（應對已大的 repo）

### Shallow clone
```bash
git clone --depth=1 <url>                   # 只最新
git clone --depth=50 <url>                  # 最近 50 個 commit
```

快、省空間。但 `git log`、某些操作受限。
```bash
git fetch --unshallow                        # 升級為完整 clone
```

### Partial clone（現代）
```bash
git clone --filter=blob:none <url>           # 不拉 blob（按需拉）
git clone --filter=blob:limit=1m <url>       # 不拉 > 1MB 的 blob
git clone --filter=tree:0 <url>              # 最激進
```

需要 server 支援（GitHub/GitLab 都 OK）。

## 23.6 歷史中的大檔清理

發現某個 iso 被 commit 在 3 年前，一直拖累 repo：

### 找出大檔
```bash
git rev-list --objects --all \
  | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' \
  | awk '$1=="blob"' \
  | sort -k3 -n -r \
  | head -20
```

### 清洗歷史（`git-filter-repo`）

```bash
# 裝
pip install git-filter-repo
# 或
pacman -S git-filter-repo   # MSYS2

# 先備份！
cp -r myrepo myrepo.backup

# 移除某檔的全部歷史
git filter-repo --path big.iso --invert-paths

# 或按 size 移除
git filter-repo --strip-blobs-bigger-than 50M
```

結果：
- 所有 commit hash 改變
- Repo size 縮小
- 所有 clone 都失效，要 force push + 協作者重新 clone

### 替代工具：**BFG Repo-Cleaner**
```bash
java -jar bfg.jar --strip-blobs-bigger-than 50M myrepo.git
```

比 filter-repo 老、稍慢，但界面友善。

### 清洗後
```bash
cd myrepo
git reflog expire --expire=now --all
git gc --prune=now --aggressive
git push --force --all
git push --force --tags
```

⚠️ 告知所有同事 force push 了，他們要重新 clone（reset --hard 到新 remote）。

## 23.7 DVC（Data Version Control）

ML / data science 專用：
```bash
pip install dvc
dvc init
dvc add data/bigfile.csv
# 產生 data/bigfile.csv.dvc（小 metadata 檔）
git add data/bigfile.csv.dvc data/.gitignore
git commit -m "Version bigfile with DVC"
dvc push    # 實體推到 S3 / GCS / local storage
```

DVC 把資料和程式碼版本對應，但資料不進 git。適合：
- 大 dataset
- ML model（checkpoint）
- 實驗 artifact

學這一套 dvc 命令比 LFS 多些，但更靈活。

## 23.8 外部 storage 模式（手動）

簡單粗暴：
```
repo/
├── src/
├── data/
│   └── README.md   ← 「從 s3://bucket/data 下載」
└── scripts/
    └── download_data.sh
```

- 大檔放 S3 / GCS / 公司 NAS
- Repo 只有 metadata / 下載 script
- 版本對應靠 URL / filename convention

土炮但沒依賴、沒 LFS quota 問題。

## 23.9 `.gitattributes` 的其他玩法

### Diff 格式
```
*.md diff=markdown
*.tex diff=tex
```

讓 git diff 更漂亮（需要對應 driver 設定）。

### 防止被 LFS 追
```
important-text.txt -filter -diff -merge text
```

### 指定 merge 策略（lockfile 常用）
```
package-lock.json merge=theirs
*.generated.ts -diff
```

### EOL 規範
```
* text=auto eol=lf
*.sh text eol=lf
*.bat text eol=crlf
*.png binary
```

## 23.10 `git sparse-checkout`：只 checkout 部分目錄

大 monorepo 只關心一部分：
```bash
git clone --sparse <url>
cd repo
git sparse-checkout set src/auth tests/auth
```

Workdir 只有這兩個目錄。完整歷史還在 `.git/`。

```bash
git sparse-checkout list
git sparse-checkout add docs/
git sparse-checkout reapply
git sparse-checkout disable         # 取消
```

**對超大 monorepo 超有用**。

### Cone pattern（預設）
```bash
git sparse-checkout set --cone src/
# 包含 src/ 和所有祖先目錄
```

### 非 cone pattern（進階）
類 gitignore 規則。

## 23.11 決策樹

```
你有大檔...

大檔是 build output / dependency？
  → .gitignore，別 commit

大檔是 source（會 review / 直接用）？
  ├─ 很少改、不超過 50MB → 直接 commit，接受成本
  ├─ 會改 → Git LFS
  └─ 超大 (> 500MB) → DVC 或外部 storage

已經 commit 進歷史 → git filter-repo 清洗（通知同事）

Repo 整體很大（歷史累積）
  → 新成員用 partial clone / shallow clone
  → 大 monorepo 用 sparse-checkout
```

## 23.12 常見錯誤

### 錯誤 1：把 build 產物 commit
```bash
# 不應該 commit
dist/
build/
target/
*.pyc
*.o
node_modules/
```

每次 build 都變 → commit 時衝突、repo 膨脹。

### 錯誤 2：LFS 沒設好，大檔進了 git 本身
```bash
# 檢查
git lfs ls-files

# 沒追蹤到就是沒經過 LFS
```

加 track 後要**重 commit 已有的檔**才會轉 LFS：
```bash
git lfs migrate import --include="*.mp4"
```

### 錯誤 3：secret 當大檔 commit（雙重慘）
```bash
git filter-repo --path secret.pem --invert-paths
# 同時 revoke 那 secret！
```

### 錯誤 4：以為刪檔 + commit 就縮 repo
不會。歷史還在。要 `filter-repo`。

## 23.13 練習

1. 建 sandbox repo，加 LFS 追 `*.bin`，commit 一個 20MB random 二進位檔。看 `.git/` size 和 `git lfs ls-files`。
2. 建一個 100 commit 的 repo，某個中間 commit 加了一個 50MB 檔（其他 commit 沒動它）。用 `git-filter-repo` 清掉那檔。
3. 試 `git clone --filter=blob:none` 一個大 open source repo（例如 `torvalds/linux`），感受 partial clone 速度差異。
4. 玩 `git sparse-checkout`：clone 一個多目錄 repo，只 checkout 其中一個子目錄。

## 23.14 本章重點
- Git 怕大檔，尤其是常改的
- **常改大檔 → Git LFS**（追蹤、上傳、指標化）
- **不該進 repo 的 → `.gitignore`**
- **已膨脹的歷史 → `git filter-repo`**（force push 代價）
- **Dataset / model → DVC 或外部 storage**
- **超大 monorepo → partial clone / sparse-checkout**
- GitHub 單檔 100MB 硬上限，LFS 免費額度 1GB
- `git-filter-repo` 是清洗歷史的現代工具（取代 filter-branch）
