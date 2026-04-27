# Ch1: Git 不是 SVN——snapshot 與 object graph

想熟練 git，先把「**diff 的堆疊**」這個錯誤的直覺拔掉。Git 不存 diff，存 **snapshot**。這章拉正心智模型。

## 1.1 SVN 式思維的陷阱

用過 SVN / CVS 的人會以為：
- 檔案有一連串 revision（v1 → v2 → v3）
- 每個 revision 存「相對前一版的 diff」
- branch 是「把檔案複製一份」

**Git 不是這樣**。Git 的世界觀：
- 每次 commit 存**整個專案的 snapshot**
- Snapshot 之間靠 **parent pointer** 連起來，形成 **graph**
- Branch 只是「指向某個 commit 的 label（pointer）」
- Tag、HEAD、remote-tracking branch 也都只是 pointer

這個差別影響深遠。

## 1.2 Commit 是什麼？

一個 commit 包含：
- **Tree**：整個專案目錄的 snapshot（每個檔案內容的指紋）
- **Parent(s)**：上一個或多個 commit 的 hash
- **Author / committer / message / timestamp**

用 hash 唯一識別（SHA-1，40 字元，顯示時常縮成 7 字元）。

看看：
```bash
git cat-file -p HEAD
```

典型輸出：
```
tree 8f94139338f9404f26296befa88755fc2598c289
parent 9688662abc...
author ypp <ohtanishohei715@gmail.com> 1713123456 +0800
committer ypp <ohtanishohei715@gmail.com> 1713123456 +0800

Add bpf course
```

看到沒？**沒有 diff**。Commit 指向一個完整的 tree。

## 1.3 Object graph

Git 裡所有東西都是 **object**，用 hash 定位。四種：
- **blob**：檔案內容（沒檔名）
- **tree**：目錄（一堆 filename → blob/tree 的映射）
- **commit**：parent + tree + metadata
- **tag**（annotated）：指向 commit 的標籤

畫個圖：

```
commit C3 ──parent──> commit C2 ──parent──> commit C1
   │                      │                      │
   tree                  tree                  tree
   │                      │                      │
   ├── README.md(blob)    ├── README.md(blob)    ├── README.md(blob)
   ├── src/(tree)         ├── src/(tree)         └── src/(tree)
   └── ...                └── ...
```

關鍵：每個 commit 都有**完整 tree**（不是 diff）。但相同檔案只存一份 blob——兩個 commit 的 README.md 如果沒改，指向同一個 blob object。

「diff」是**呈現時計算出來的**，不是存的。所以 `git log -p` 會比 `git log` 慢——它要即時算 diff。

### Deduplication
Git 的效率在於：沒改的 blob / tree 可以共用。改了一個 10MB 檔案，**不是再存 10MB**，而是再存一份 blob（hash 不同）+ 新 tree 指它。

## 1.4 Branch 不是複製

```bash
git branch feature
```

這條命令做什麼？**只是在 `.git/refs/heads/feature` 寫入當前 HEAD 的 hash**。

```bash
cat .git/refs/heads/feature
# 9688662abc...
cat .git/refs/heads/main
# 9688662abc...
```

兩個 branch 指同一個 commit。零成本。

### HEAD 是什麼
`.git/HEAD` 通常內容是：
```
ref: refs/heads/main
```

「HEAD 指 main」。切 branch：
```bash
git switch feature
```

`.git/HEAD` 變成 `ref: refs/heads/feature`。**就這樣**。沒複製、沒搬檔案的概念層面。workdir 檔案會更新到對應 commit 的 tree，但這是「checkout」動作，和 branch 本身無關。

## 1.5 Merge 產生什麼

```bash
git switch main
git merge feature
```

如果不能 fast-forward，會產生一個**有兩個 parent 的 commit**：

```
       C4 (feature)
      /
... C2 -- C3 -- M (main)
              /
             C4'  ← M 的第二個 parent
```

`M` 是 merge commit，parent 有兩個。這就是「graph」的由來。

## 1.6 Rebase 不是 merge

```bash
git switch feature
git rebase main
```

這會**複製** feature 上的 commit 到 main 後面，hash 全變：

```
Before:
... A - B - C (main)
         \
          D - E (feature)

After rebase:
... A - B - C (main)
             \
              D' - E' (feature)   ← D' 是新 commit，內容同 D 但 parent 不同
```

**重要**：D 和 D' 是**不同 object**（不同 hash），雖然 diff 可能一樣。這是「rewrite history」的本質。

Ch6 細講 merge vs rebase 的取捨。

## 1.7 Detached HEAD

```bash
git switch 9688662   # 切到某個 hash
```

HEAD 直接指向 commit（不是 branch）：
```
HEAD → 9688662abc
```

這叫 **detached HEAD**。你可以 commit，但沒 branch 指這些新 commit；切走就變孤兒（之後被 gc 清掉）。

```
      orphan commit ← HEAD(detached)
     /
... C2 -- C3 (main)
```

想留下來？建 branch：
```bash
git switch -c my-experiment
```

Detached HEAD 不是錯誤，是**工具**——你可以拿它探索歷史、試驗性編譯。但要回來。

## 1.8 同一個 commit 可以在多個 branch 上

Branch 是 pointer，不是 container。合併後：

```
           feature
              ↓
... C1 - C2 - C3 - M
              ↑        ↑
           (還在 feature) main
```

C3 既屬於 feature（feature 指 C3），也屬於 main（從 M 可以回溯到 C3）。Git 的「屬於」是**可達性**（reachable from），不是「裝在裡面」。

這就是為什麼 `git branch --contains C3` 列出多個 branch。

## 1.9 Orphan / 丟失 commit

刪 branch：
```bash
git branch -D feature
```

feature 指的 commit 如果沒被其他 ref 可達，就是 orphan。但**還在 `.git/objects/`**——gc 會在幾週後清掉，這期間 reflog 還能找回（Ch11）。

這就是為什麼「我刪錯 branch」大多救得回來。

## 1.10 從 C 程式員的角度比喻

- blob ≈ file content in a content-addressable store（像 hash table 的 value）
- tree ≈ struct of filenames → blob/tree pointers（像 inode → block 的映射）
- commit ≈ linked list node（有 prev pointer）, payload 是整個 tree
- branch ≈ `struct ref { char name[]; hash_t* target; }`
- HEAD ≈ 特殊的 symbolic ref

整個 `.git/objects/` 是個 key-value store，key 是內容 hash、value 是內容本身。Git 命令都在這上面操作。

## 1.11 驗證：自己摸摸看

```bash
mkdir /tmp/test && cd /tmp/test
git init
echo "hello" > a.txt
git add a.txt
git commit -m "first"

# 看 commit hash
git log --oneline

# 看 commit 內容
git cat-file -p HEAD

# 看 tree 內容
git cat-file -p HEAD^{tree}

# 看 blob 內容
git cat-file -p HEAD:a.txt

# 看 object 底層
ls .git/objects/
```

這些命令是 plumbing（低階），日常不用，但跑一次有助建立直覺。

## 1.12 實用含義

理解 snapshot + graph 後，這些就有意義了：

- **為什麼 checkout 很快**？不是複製檔案，是換 HEAD pointer + 更新 workdir
- **為什麼 branch 很便宜**？只是多一個 pointer
- **為什麼大 repo 也能 clone**？objects 去重複
- **為什麼 rebase 會「改 hash」**？因為產新 commit object
- **為什麼 force push 危險**？把 remote branch 指到不同 commit graph，別人的 ref 錯亂
- **為什麼 reflog 救得回**？objects 沒被 gc 前都還在

## 本章重點
- Git 存 **snapshot** 不是 diff
- 所有東西是 **object**，hash 定位，unchanged 的自動去重
- Branch / HEAD / tag 都只是 **pointer**
- Merge 產生 two-parent commit；rebase 產生**新** commit（新 hash）
- Detached HEAD 是工具不是錯誤
- 「屬於 branch」= 從 branch pointer 可達，不是容器關係
