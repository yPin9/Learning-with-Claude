# Ch8: amend / cherry-pick / revert

三個補鍋神器。每個都有它的時機。

## 8.1 `git commit --amend`

改**上一個 commit**。

### 改訊息
```bash
git commit --amend -m "better message"
```

或不帶 `-m` 開編輯器改。

### 補上漏 add 的檔
```bash
echo "forgot this" >> b.txt
git add b.txt
git commit --amend --no-edit       # 不改訊息，只補檔
```

`--no-edit` 讓它沿用原 commit 訊息。

### 改 author
```bash
git commit --amend --author="Alice <alice@example.com>"
```

## 8.2 amend 的本質

`amend` 其實不是「改舊 commit」，是**產生一個新 commit 取代舊的**：
- 新 commit 有新 hash
- 舊 commit 變 orphan（reflog 可找回）
- HEAD 指新 commit

```
Before:
... A - B - C (HEAD)

git commit --amend:

... A - B - C' (HEAD)
         \
          C (orphan, 還在 reflog)
```

所以 amend 後**如果已 push**，要 `push --force-with-lease`。

## 8.3 amend 的陷阱

### 陷阱 1：amend 已 push 的 commit
主幹不行（同 rebase 規則）。自己的 feature branch 可以，force push 即可。

### 陷阱 2：amend 意外把髒 workdir 塞進去
```bash
# workdir 有無關改動
git commit --amend --no-edit
# 無關改動也被塞進上一個 commit
```

amend 前 `git status` 看清楚。

### 陷阱 3：忘了 `--no-edit`
```bash
git commit --amend        # 開編輯器，可能無意改訊息
git commit --amend --no-edit   # 保留訊息
```

## 8.4 `git cherry-pick`

**把某 commit 複製**到當前 branch。

```bash
git cherry-pick abc1234              # 複製一個
git cherry-pick abc1234 def5678      # 複製多個（依序）
git cherry-pick abc1234..def5678     # 複製一段（不含 abc1234）
git cherry-pick abc1234^..def5678    # 含 abc1234
```

```
Before:
main:    ... A - B - C
feature: ... A - D - E

git switch main
git cherry-pick E

After:
main:    ... A - B - C - E' (E 的內容，新 hash)
feature: ... A - D - E
```

### 用途

**場景 1：把 hotfix 從 main 搬到 release branch**
```bash
git switch release/1.0
git cherry-pick <hotfix-commit-on-main>
```

**場景 2：拆一個大 PR 的一部分**
別人的 PR 你只要其中一個 commit：
```bash
git switch my-branch
git cherry-pick their-commit-hash
```

**場景 3：救誤刪的 commit**
從 reflog 找到 hash：
```bash
git reflog
git cherry-pick abc1234
```

### 選項

```bash
git cherry-pick -x abc1234       # 訊息加 "(cherry picked from commit abc1234)"
git cherry-pick --no-commit ...  # 套改動但不 commit（讓你編輯後自己 commit）
git cherry-pick -n ...            # 縮寫
git cherry-pick --signoff ...    # 加 Signed-off-by
```

### 衝突
和 merge / rebase 一樣：
```bash
git cherry-pick --continue
git cherry-pick --abort
git cherry-pick --skip
```

## 8.5 `git revert`

產生一個**抵銷某 commit**的新 commit。

```bash
git revert abc1234
```

```
Before:
... A - B - C - D (HEAD)

git revert B

After:
... A - B - C - D - R (HEAD)
                    ↑
              R 的改動是「反向套 B 的 diff」
```

### 用途

**場景：已 push 的 commit 有問題，不能 force push（main）**
```bash
git revert abc1234
git push
```

安全地「取消」那 commit 的效果，不改歷史。

### 多個 commit
```bash
git revert abc1234 def5678
git revert abc1234..def5678       # 一段（不含 abc1234）
```

### `--no-commit`
```bash
git revert --no-commit abc1234
# 把 revert 的改動套到 workdir + index，不自動 commit
git add ...
git commit -m "..."
```

適合 revert 後還想一起改別的再 commit。

### Revert merge commit
有兩個 parent，要告訴 git 走哪條：
```bash
git revert -m 1 <merge-commit>    # 回到第一個 parent 的歷史
git revert -m 2 <merge-commit>    # 回到第二個 parent
```

通常 `-m 1`（保留 main 側、revert feature 側的改動）。

## 8.6 三者對照

| | 本質 | 改歷史 | 用途 |
|---|---|---|---|
| `amend` | 取代上一 commit | 改（hash 變） | 補漏、改訊息 |
| `cherry-pick` | 複製 commit 到此處 | 不改舊的，加新的 | 搬 commit 跨 branch |
| `revert` | 產生「反向」commit | 不改，加新 commit | 取消已 public 的 commit |

## 8.7 典型情境

### 情境 A：主幹上發現 commit 爛
```bash
# 不能 force push，要用 revert
git revert <bad-commit>
git push
```

### 情境 B：feature branch 上發現 commit 爛
```bash
# 選項 1: rebase -i 改掉（自己 branch 無所謂改歷史）
git rebase -i HEAD~3
# 改或 drop

# 選項 2: amend（如果是最近一個）
# 先 reset 回去再 commit；或 rebase 時 edit
```

### 情境 C：另一 branch 上的 commit 我也要
```bash
git cherry-pick <commit>
```

### 情境 D：想回到某歷史狀態，但不丟歷史
```bash
# 不要用 reset（會改歷史）
# 用 revert 反向做回去
git revert abc1234..HEAD
```

### 情境 E：merge 後發現 merge 錯了，且已 push
```bash
git revert -m 1 <merge-commit>
git push
```

### 情境 F：feature 有 15 個 commit，要把其中 3 個搬到 release
```bash
git switch release/x
git cherry-pick c1 c2 c3
```

## 8.8 `--signoff` (`-s`)

```bash
git commit -s -m "..."
# 訊息末尾加 Signed-off-by: Your Name <email>
```

某些專案（Linux kernel）要求。GitHub 也有機制強制。

## 8.9 陷阱集

### 陷阱 1：cherry-pick 後 merge 原 branch
```bash
git switch main
git cherry-pick feature-commit   # 複製過來
# 之後
git merge feature                # 整個 merge feature
# → feature-commit 的改動可能出現兩次（不一定，看 git 判斷）
```

通常 git 會發現相同改動自動處理，但複雜情境可能出錯。避免這種 workflow。

### 陷阱 2：revert 後又想要回那些改動
revert 是「反向 diff」。要「取消 revert」：
```bash
git revert <revert-commit>      # revert 那個 revert 😅
```

或直接 cherry-pick 原 commit 再來一次。

### 陷阱 3：amend 後 pull
```bash
git commit --amend    # 改 hash
git pull              # 遠端是舊 hash，merge 衝突或產生 merge commit
```

amend 後用 `push --force-with-lease`，不要 pull。

## 8.10 何時用哪個（決策樹）

```
我要改 commit 內容/訊息嗎？
├─ 是，是最後一個 commit → git commit --amend
├─ 是，是更早的 commit   → git rebase -i
└─ 否
   ├─ 我要複製某 commit 來這 branch → git cherry-pick
   ├─ 我要取消某 commit 效果        
   │  ├─ commit 已 public        → git revert
   │  └─ commit 還沒 push         → git rebase -i drop 或 reset
   └─ ...
```

## 8.11 練習

1. 做 commit，發現忘了一個檔，`git add` + `git commit --amend --no-edit`。
2. 在 branch A 做一個 commit，切到 branch B，cherry-pick 過來。
3. 在 main 做一個 commit，push（sandbox），revert 它，再 push。
4. 做 3 個 commit，用 interactive rebase 把中間那個 amend 成兩個 commit。

## 本章重點
- `commit --amend` 改最後一個 commit（產新 hash，push 要 force-with-lease）
- `cherry-pick <hash>` 搬單個 commit 到當前 branch
- `revert <hash>` 產生反向 commit，**安全取消 public commit**
- 三者對歷史的影響不同：amend 改 / cherry-pick 複製 / revert 只加
- Merge commit revert 要 `-m 1`
- `--no-commit` 讓你先看改動再決定
