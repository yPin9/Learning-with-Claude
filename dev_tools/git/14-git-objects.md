# Ch14: 四種 Git Object

理解底層資料結構，很多高層行為就通了。**本章點到為止**（不深入 packfile 格式），目標是能用 plumbing 命令解剖、看得懂 `.git/objects/` 裡是什麼。

## 14.1 四種 object

所有 git 資料都以 object 形式存在 `.git/objects/`：

| 類型 | 存什麼 |
|---|---|
| **blob** | 檔案內容（沒檔名、沒 metadata） |
| **tree** | 一個目錄（filename → blob/tree 的映射） |
| **commit** | parent hash + tree hash + metadata + message |
| **tag**（annotated）| 指向某 commit 的「有簽名/訊息的標籤」 |

每個 object 的 key 是它內容的 **SHA-1 hash**（40 字元）。**內容相同 = hash 相同 = 自動去重**。

## 14.2 Blob

純粹的 byte 序列。沒有檔名、沒有 permission，就是內容。

看 blob：
```bash
git cat-file -p <blob-hash>
```

用 HEAD:file 語法找 blob：
```bash
git cat-file -p HEAD:README.md    # 列印 README.md 當下的內容
```

兩個不同檔案 **內容一樣** → 同一個 blob（只存一份）。

## 14.3 Tree

一個目錄的 snapshot。每筆紀錄：
```
<mode> <type> <hash>  <filename>
```

例如：
```bash
git cat-file -p HEAD^{tree}
```

輸出：
```
100644 blob a6b3f1...    README.md
040000 tree c2d5a9...    src
100755 blob 89e0f2...    build.sh
```

- `100644`：普通檔
- `100755`：可執行檔
- `040000`：子 tree（目錄）
- `120000`：symlink
- `160000`：submodule

Tree 是**遞迴**的——子目錄是另一個 tree object。

## 14.4 Commit

```bash
git cat-file -p HEAD
```

輸出：
```
tree 8f94139338f9404f26296befa88755fc2598c289
parent 9688662abc...
author ypp <ohtanishohei715@gmail.com> 1713123456 +0800
committer ypp <ohtanishohei715@gmail.com> 1713123456 +0800

Add bpf course
```

組成：
- **tree**：這個 commit 的整個專案 snapshot
- **parent(s)**：前一個 commit（merge 有多個）
- **author**：誰寫的（commit 的人可能不同）
- **committer**：誰提交的
- **message**：訊息

**commit 不含 diff**——diff 是比較兩個 tree 算出來的。

## 14.5 Tag

兩種 tag：
- **Lightweight tag**：只是個 ref 指向 commit，沒 object
- **Annotated tag**：有獨立的 tag object（帶訊息、簽名、tagger）

```bash
git tag v1.0                       # lightweight
git tag -a v1.0 -m "Release 1.0"   # annotated
git tag -s v1.0 -m "Release 1.0"   # annotated + GPG signed
```

看 annotated tag：
```bash
git cat-file -p v1.0
# object abc1234...
# type commit
# tag v1.0
# tagger ypp <...>
# 
# Release 1.0
```

**正式 release 用 annotated tag**，方便紀錄 "who, when, why"。

## 14.6 Hash 的意義

SHA-1 40 字元十六進位：
```
9688662abc1234def5678...
```

前兩碼是目錄名，剩下是檔名：
```
.git/objects/
    96/
        88662abc1234def5678...
```

顯示時常縮成 **7 字元**（`git log --oneline`），只要不歧義就行。

### Git 還是 SHA-1？
2017 年發現 SHA-1 collision 之後，git 有 SHA-256 mode（`git init --object-format=sha256`），但現實中 GitHub 和大多工具還走 SHA-1。沒 migration path，可能永遠混著。日常不影響。

## 14.7 自己建 object（plumbing）

示範 git 內部：

```bash
mkdir /tmp/plumbing && cd /tmp/plumbing
git init

# 建一個 blob
echo "hello" | git hash-object -w --stdin
# ce013625...

# 查
git cat-file -p ce013625
# hello

git cat-file -t ce013625
# blob
```

這個 blob **還沒在任何 tree 或 commit 裡**，但已存在 `.git/objects/`。`git gc` 會清（因為 unreachable）。

### 建 tree
```bash
# 寫進 index
git update-index --add --cacheinfo 100644 ce013625 hello.txt
# 從 index 寫 tree
git write-tree
# 5b1d3...
git cat-file -p 5b1d3
# 100644 blob ce013625... hello.txt
```

### 建 commit
```bash
echo "First commit" | git commit-tree 5b1d3
# 8a3f9b...
git cat-file -p 8a3f9b
# tree 5b1d3...
# author ...
# First commit

# 指 branch 指過去
git update-ref refs/heads/main 8a3f9b
```

恭喜，你手動建了一個 commit，不經 `git add/commit`。**Git 底層就是這些 plumbing 命令**，`add/commit` 只是 porcelain（高層封裝）。

平常不會這樣玩，但跑一次對心智模型很有幫助。

## 14.8 Object 的儲存：loose vs packed

### Loose
預設每個 object 一個檔：
```
.git/objects/ab/cd1234...
```

內容用 zlib 壓縮。

### Packed
重複很多 object 會浪費空間。`git gc` 會把 loose object 打包：
```
.git/objects/pack/pack-xxxxx.pack
.git/objects/pack/pack-xxxxx.idx
```

Pack 內部用 delta compression：相似的 object 只存差異。這是 git 能處理大 repo 的原因。

日常操作都是自動的，不用管。

## 14.9 Ref 也是 object？

Ref（branch、tag、HEAD）**不是** object。它們是**指向 commit 的 pointer**，存在：
```
.git/refs/heads/main       ← 單純文字檔，內容是 commit hash
.git/refs/tags/v1.0
.git/refs/remotes/origin/main
.git/HEAD                   ← ref: refs/heads/main
```

看：
```bash
cat .git/refs/heads/main
# 9688662abc1234...
```

Refs 可以 packed 到 `.git/packed-refs` 節省空間（自動）。

## 14.10 常用 plumbing 命令

日常不用，debugging 或工具開發才用：

```bash
git hash-object <file>               # 算 blob hash（不寫進 object）
git hash-object -w <file>            # 算並寫入
git cat-file -t <hash>               # 看 object type
git cat-file -p <hash>               # 列內容
git cat-file -s <hash>               # 列大小
git ls-tree HEAD                     # 列當前 HEAD 的 tree
git ls-tree -r HEAD                  # 遞迴
git ls-tree HEAD -- src/             # 某目錄
git rev-parse HEAD                   # 解析 ref 成 hash
git rev-parse HEAD^{tree}            # HEAD 的 tree hash
git rev-parse main feature           # 多個 ref 的 hash
git symbolic-ref HEAD                # HEAD 指向哪個 ref
```

## 14.11 引用表達式語法

```bash
HEAD         # 當前
HEAD^        # HEAD 的 parent
HEAD^^       # HEAD 的 parent 的 parent
HEAD~3       # HEAD 的 3 步前 parent
HEAD^2       # merge commit 的第二個 parent
HEAD@{1}     # HEAD 1 步前（reflog）
HEAD@{yesterday}

main^{commit}    # 明確是 commit 型別
main^{tree}      # main 的 tree
main^{}          # 如果 main 是 tag，解引用到 commit

abc1234          # hash 前幾字元（不歧義即可）
```

好工具在錯綜複雜情況下定位 ref。

## 14.12 目錄探險：`.git/objects/`

```bash
ls .git/objects/
# 兩字元目錄 + info/ + pack/

ls .git/objects/9a/
# 剩下 38 字元的檔案

# 解壓一個看看（用 git cat-file）
git cat-file -p <hash>
```

做幾次 commit 後進去看看——結構很直觀。

## 14.13 Dangling / unreachable objects

沒有 ref 可達到的 object（孤兒）：
```bash
git fsck
# dangling commit abc1234
# dangling blob def5678
```

會被 `git gc` 清理（過期後）。Reflog 和 stash 也算 ref，所以那些東西暫時不會被清。

## 14.14 `git gc`

```bash
git gc                # 自動 gc（通常自己跑）
git gc --auto         # 只在需要時
git gc --aggressive   # 更激進（少用）
git gc --prune=now    # 立刻清 unreachable object（⚠️ 丟東西風險）
```

平常不用手動跑。大 repo 長期會自動整理。

## 14.15 常見陷阱

### 陷阱 1：以為刪檔就少空間
```bash
git rm bigfile.iso
git commit -m "remove big file"
```

**檔案還在歷史中**，repo size 不會縮。要完全拔：`git filter-repo`（Ch13）。

### 陷阱 2：`git gc --prune=now` 救命時誤用
```bash
git gc --prune=now --aggressive
```

如果還要救某個 orphan commit，它被清了就沒了。**救援中別 gc**。

### 陷阱 3：以為 hash 可以反推內容
SHA-1 是單向的。知道 hash 找不到內容（除非 object 在 repo 裡）。

## 14.16 練習

```bash
mkdir /tmp/git-objects && cd /tmp/git-objects
git init

# 1. 做 commit 看每一層
echo "hello" > a.txt
git add a.txt
git commit -m "first"

git cat-file -p HEAD              # commit object
git cat-file -p HEAD^{tree}       # tree
git cat-file -p HEAD:a.txt        # blob

# 2. 看 .git/objects 結構
ls -R .git/objects

# 3. 用 plumbing 手動做 commit（上面 14.7 步驟）

# 4. 試 rev-parse
git rev-parse HEAD
git rev-parse HEAD^{tree}
git rev-parse main
```

## 本章重點
- 四種 object：**blob**（檔案內容）、**tree**（目錄）、**commit**（snapshot + metadata）、**tag**（annotated）
- 所有 object 用 SHA-1 hash 識別，內容 addressable
- **Commit 不存 diff**，diff 是比較兩個 tree 算出來的
- **Ref（branch / tag / HEAD）不是 object**，是 commit 的 pointer
- Plumbing 命令：`cat-file` / `hash-object` / `ls-tree` / `rev-parse`
- `.git/objects/` 內結構簡單、可讀，做過幾次 commit 進去看看最有感
