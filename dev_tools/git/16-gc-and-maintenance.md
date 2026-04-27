# Ch16: gc / fsck / maintenance

Git 的清潔和健檢工具。**日常不用煩**，但長期運作的大 repo 要知道。

## 16.1 `git gc`：垃圾回收

合併 loose object 到 pack、清掉過期的 orphan object。

```bash
git gc              # 自動判斷需不需要（預設）
git gc --auto       # 只在條件滿足時跑（git 內部常這樣呼叫）
git gc --aggressive # 更激進的壓縮（慢，少用）
git gc --prune=now  # 立即刪過期 orphan ⚠️
```

### 自動觸發
Git 會在某些操作（push、pull、commit...）之後跑 `gc --auto`。條件：
- `.git/objects/` 的 loose object 超過閾值（預設 6700）
- Pack file 多過閾值

你幾乎不用手動跑。

### 什麼會被清
- 過期（預設 90 天）的 orphan object：清
- 90 天內的 orphan：保留（reflog 救得回）
- 被任何 ref 可達的：保留

## 16.2 `git gc --prune=now` 的危險

```bash
git gc --prune=now
```

**立刻**清掉**所有**無 ref 可達的 object——不管有多新。

**後果**：你剛 `reset --hard` 丟掉的 commit，reflog 還在但 object 被清了 → **真的救不回來**。

**別隨便跑**。只在你確定：
- 要清洗 repo（清掉 secret 後配合 `filter-repo`）
- 重度 gc 前做備份

## 16.3 `git fsck`：健康檢查

檢查 object 完整性：
```bash
git fsck                       # 檢查 repo
git fsck --full                # 全面
git fsck --lost-found          # 把 orphan 寫到 .git/lost-found/
git fsck --unreachable         # 只列 orphan
```

找出：
- **broken links**：某 ref 指向不存在的 object（repo 壞了）
- **dangling objects**：orphan object（可能還有用）
- **corrupt objects**：壞掉的 object

### 典型輸出
```
Checking object directories: 100% (256/256), done.
dangling commit abc1234...
dangling blob def5678...
```

## 16.4 `git maintenance`（現代 git）

Git 2.29+ 引入，取代手動 gc。

```bash
git maintenance start       # 註冊背景維護（cron / systemd）
git maintenance stop        # 取消
git maintenance run         # 手動跑一次所有任務
```

任務包含：
- `gc`：壓縮
- `commit-graph`：建快速查詢的 commit graph
- `prefetch`：預先 fetch
- `loose-objects`：打包 loose object
- `incremental-repack`：漸進 repack
- `pack-refs`：打包 ref

### 個別跑
```bash
git maintenance run --task=gc
git maintenance run --task=commit-graph
```

### 建議設定
```bash
git maintenance start       # 一次性設定
```

之後 git 會在背景自動維護所有註冊的 repo，不卡你的操作。

## 16.5 `commit-graph`：加速 log 和 merge

```bash
git commit-graph write --reachable
```

建一個 `.git/objects/info/commit-graph` 檔，儲存 commit 的 parent 關係圖。

之後 `git log`、`git merge-base`、`git branch --contains` 快很多——不用每次讀每個 commit object。

大 repo 有感，小 repo 沒差。

開啟自動維護：
```bash
git config --global core.commitGraph true
git config --global gc.writeCommitGraph true
```

## 16.6 Shallow clone 與 partial clone

### Shallow clone
只拉最近 N 個 commit：
```bash
git clone --depth 1 <url>         # 只最新一個 commit
git clone --depth 50 <url>
```

好處：快、省空間。
壞處：`git log` 只看到最近幾個、某些操作受限（要 `unshallow`）。

### Partial clone（現代）
只拉 tree/commit，blob 按需拉：
```bash
git clone --filter=blob:none <url>             # 不拉 blob
git clone --filter=tree:0 <url>                # 更激進
```

大 repo（GB 級）用得到。一般專案不需要。

```bash
git fetch --unshallow              # 轉回完整 clone
```

## 16.7 Git LFS（Large File Storage）

很大的二進位檔（影片、model、二進位資料）不適合進 git——會讓 repo 膨脹。

LFS 讓大檔存別處，repo 裡只有 pointer：
```bash
git lfs install
git lfs track "*.mp4"
git lfs track "*.zip"
git add .gitattributes
git add video.mp4
git commit -m "..."
git push              # mp4 實體上傳到 LFS server
```

GitHub 有 LFS quota（免費帳號 1GB）。

Ch23 細講。

## 16.8 Repo 太大怎麼辦

診斷：
```bash
# 看 .git 多大
du -sh .git

# 找最大的 blob
git rev-list --objects --all \
  | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' \
  | awk '$1=="blob"' \
  | sort -k3 -n -r \
  | head -20
```

### 解法
1. **單次塞了大檔**：`git filter-repo --path big.iso --invert-paths` 從歷史中刪
2. **二進位資產**：遷移到 LFS
3. **長期專案**：`git maintenance` 自動維護
4. **partial clone**：新成員用

## 16.9 常用健檢流程

定期（或 repo 變慢時）：
```bash
git fsck --full                     # 健檢
git gc                              # 自然清理
# 如果空間問題嚴重：
git gc --aggressive                 # 壓榨
```

## 16.10 Commit graph 帶來的差異

```bash
# 建 commit graph
git commit-graph write --reachable

# 之後這些變快：
git log --graph --all               # 快 5-10 倍
git log --oneline --graph
git merge-base main feature
git rev-list HEAD --count
```

Linux kernel 的 repo 用 commit graph 後 `git log` 從幾秒變幾十 ms。

## 16.11 `git repack`

手動 repack（`git gc` 內部會用）：
```bash
git repack                         # 把 loose 打包
git repack -a                      # 全部
git repack -A -d                   # 全部 + 刪 orphan pack（gc 會做）
```

日常不用。

## 16.12 進階：多 pack index（MIDX）

大 repo 多 pack file 時，查每個 pack 的 index 很慢。MIDX 合併多個 index：
```bash
git multi-pack-index write
```

`git maintenance` 會自動做。你只要啟用 maintenance 就不用管。

## 16.13 `reftable`（未來）

Git 2.42+ 實驗性功能，取代 `refs/` + `packed-refs`。大量 ref（成千 tag）的 repo 會快很多。

```bash
git init --ref-format=reftable
```

主流生態還在採納，幾年後會是預設。

## 16.14 個人 repo 推薦設定

最少做：
```bash
git config --global maintenance.auto true       # 自動維護
git maintenance start                            # 啟動背景維護（一次性）
```

加這些更好：
```bash
git config --global core.commitGraph true
git config --global gc.writeCommitGraph true
git config --global fetch.writeCommitGraph true
```

之後 git 會默默幫你保持 repo 健康。

## 16.15 停用 / 除錯

```bash
git maintenance stop                # 停
crontab -l                           # Linux 看 cron（maintenance 用 cron）
schtasks /Query                      # Windows 看 scheduled task
```

## 16.16 練習

1. 跑 `git fsck --full` 在一個大 repo 看輸出。
2. 在一個小 repo 做 `git gc`，前後 `du -sh .git` 比較。
3. 建 commit-graph，`git log --graph --all` 看看速度差異（要夠大的 repo 才有感）。
4. 試 `git maintenance run --task=gc` 和背景 `git maintenance start`。

## 本章重點
- `git gc` 自動清理，**`--prune=now` 危險**
- `git fsck` 健檢，`--lost-found` 找孤兒 object
- **`git maintenance` 是現代做法**，啟用一次就忘了
- `commit-graph` 加速 log / merge 計算
- Shallow / partial clone 處理超大 repo
- LFS 處理大二進位檔（Ch23 細講）
