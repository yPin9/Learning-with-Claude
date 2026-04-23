# Ch12: reset vs revert vs restore 全解

三個命令常被搞混。一表理清。

## 12.1 一句話區分

- **`restore`**：檔案層級——還原單檔到某狀態
- **`reset`**：**HEAD / branch pointer** 層級——移動 branch、**改寫歷史**
- **`revert`**：**commit 層級**——產新 commit 抵銷舊 commit（**不改歷史**）

## 12.2 `git restore`

已在 Ch4 講過。快速複習：

```bash
git restore file.txt                  # 用 index 覆蓋 workdir（丟 workdir 改動）
git restore --staged file.txt         # 用 HEAD 覆蓋 index（取消 staging）
git restore --staged --worktree file.txt    # 兩者都還原
git restore --source=HEAD~3 file.txt  # 用某 commit 的版本覆蓋 workdir
```

**不動 HEAD，不改歷史**。純粹還原檔案內容。

## 12.3 `git reset` 三種模式

```bash
git reset [--soft | --mixed | --hard] <target>
```

三區（workdir / index / HEAD）對目標 commit 的動作：

| Mode | HEAD | Index | Workdir |
|---|---|---|---|
| `--soft` | 移到 target | 不動 | 不動 |
| `--mixed`（預設）| 移到 target | 設為 target 的 | 不動 |
| `--hard` | 移到 target | 設為 target 的 | 設為 target 的 ⚠️ |

### `--soft`
「commit 撤回但改動留著」。

```bash
git commit -m "..."
# 發現 commit 太早
git reset --soft HEAD^
# 改動回到 index（staged），HEAD 退回一步
```

用途：
- Commit 太早，想繼續做事再 commit
- 合併最近幾個 commit 成一個：
  ```bash
  git reset --soft HEAD~3
  git commit -m "合併訊息"
  ```

### `--mixed`（預設）
「撤回 commit 和 staging，改動留 workdir」。

```bash
git reset HEAD^
# 同 --mixed，HEAD 退一步、index 也退、workdir 保留改動
```

用途：
- 撤回 commit + 重新決定要 add 什麼
- 撤回 `git add`（等同 `git restore --staged`）

### `--hard`
**危險**。全部歸回到 target。

```bash
git reset --hard HEAD~3
# 丟掉最近 3 個 commit + 所有未 commit 改動
```

用途：
- 放棄所有改動回到某個 commit
- ⚠️ 不可逆（reflog 救 commit，workdir 改動救不回）

### 比較 `reset` vs `restore`
```bash
git restore file.txt                # 只動這個檔
git reset file.txt                  # 等效 restore --staged（取消 stage）
git reset --hard                    # 動 HEAD + index + workdir 全部
```

## 12.4 reset 的真實例子

### 例 1：commit 太早
```bash
git commit -m "feat: done"
# 意識到漏測東西
git reset --soft HEAD^
# 改動在 index，繼續工作
# ... 改 ...
git commit -m "feat: done"   # 重新 commit
```

### 例 2：合併最近 3 個 commit
```bash
git log --oneline -3
# abc1234 part 3
# def5678 part 2
# 789abcd part 1

git reset --soft HEAD~3
# HEAD 退到 part 1 之前，index 有 part 1/2/3 的改動
git commit -m "Add full feature X"
```

等效 rebase interactive squash 的效果，但更快。

### 例 3：撤回 add
```bash
git add file.txt
# 哎，不想 add 這個
git reset HEAD file.txt        # 或 git restore --staged file.txt
```

### 例 4：放棄一切回到 clean
```bash
git reset --hard HEAD          # 放棄所有未 commit 改動
```

### 例 5：跟遠端完全同步（強制）
```bash
git fetch
git reset --hard origin/main
```

完全捨棄本地改動和 commit，跟 origin 一致。⚠️ 本地 commit 全丟。

## 12.5 `git revert`

已在 Ch8 講過。快速複習：

```bash
git revert abc1234
```

產新 commit，內容是**反向套 abc1234 的 diff**。歷史不變、安全。

### 對比 reset
```
情境 1: commit 還沒 push
     → reset（改歷史 OK）

情境 2: commit 已 push 到 public branch
     → revert（不改歷史，安全）
```

## 12.6 決策樹

```
我想取消某個改動...

這改動還在 workdir / index 沒 commit 嗎？
├─ 是 → git restore [--staged] <file>
└─ 否（已 commit）
   ├─ 還沒 push 嗎？
   │  ├─ 是 → git reset（改歷史，隨意）
   │  │     ├─ 想留改動在 workdir  → --mixed
   │  │     ├─ 想留改動在 index    → --soft
   │  │     └─ 全砍                → --hard ⚠️
   │  └─ 否（已 push）
   │     ├─ 共用 branch → git revert（安全）
   │     └─ 個人 branch → reset + force-with-lease push
   └─ 合併/搞砸的 rebase 想救回 → git reflog + git reset
```

## 12.7 常見誤用

### 誤用 1：用 `reset --hard` 取消 add
```bash
git add file.txt
git reset --hard     # ❌ 連其他 workdir 改動都沒了
git reset file.txt   # ✅ 只取消 stage
# 或
git restore --staged file.txt
```

### 誤用 2：`reset --hard` 到 remote，結果本地有重要改動
```bash
git reset --hard origin/main     # 本地的 commit 都沒了
```

先 `git log main..HEAD` 看本地有什麼；或用 branch 保護：
```bash
git branch backup              # 標記當前狀態
git reset --hard origin/main
# 需要時 git reset --hard backup 回來
```

### 誤用 3：pull 時意外 reset
```bash
git pull    # 如果設定不當或搞錯 remote，可能意外
```

Ch5 提過，設 `pull.rebase=true`，pull 前 `git status` 確認乾淨。

### 誤用 4：`revert` merge commit 沒加 `-m`
```bash
git revert <merge-commit>
# error: commit is a merge but no -m option was given
git revert -m 1 <merge-commit>
```

## 12.8 `reset` 的變體：`reset HEAD`（不帶 `--mode`）

```bash
git reset HEAD file.txt
```

**預設 mode 是 `--mixed`**，但因為 target 是 HEAD 沒變，實際效果是「讓 index 退回和 HEAD 一致」——也就是**取消 staging**。

這是 `git restore --staged` 之前的舊寫法。

## 12.9 三者的「改歷史 vs 不改歷史」

| 命令 | 改 HEAD | 改歷史（改 commit hash） |
|---|---|---|
| `restore` | ❌ | ❌ |
| `reset --soft/mixed` | ✅ | 不產新 commit（只移 branch pointer） |
| `reset --hard` | ✅ | 不產新 commit |
| `revert` | ✅ | 不改舊 commit，**加**新 commit |
| `rebase`（下章複習） | ✅ | 產新 commit（複製） |
| `commit --amend` | ✅ | 產新 commit 取代舊 |

「改歷史」= 讓一個 commit 在 branch 上消失或被取代。要 force push 的前提。

## 12.10 實戰：不同情境該用誰

### 情境 A：改錯了，還沒 add
```bash
git restore file.txt
```

### 情境 B：加錯了（add 了不該 add 的）
```bash
git restore --staged file.txt
```

### 情境 C：commit 錯了（還沒 push）
```bash
git reset --soft HEAD^    # 改動回到 staged
# 或
git reset HEAD^            # 改動回到 workdir
# 繼續改、重 commit
```

### 情境 D：commit 錯了，只想改訊息
```bash
git commit --amend -m "correct message"
```

### 情境 E：commit 錯了（已 push 到 public branch）
```bash
git revert <bad-commit>
git push
```

### 情境 F：一整串 commit 全部不要（本地 branch）
```bash
git reset --hard <good-commit-before-all-bad>
git push --force-with-lease     # 如果已 push
```

### 情境 G：想完全同步 origin（丟本地）
```bash
git fetch
git reset --hard origin/main
```

## 12.11 `git clean`（附帶提一下）

`reset --hard` 不會刪 **untracked** 檔。要清完全：
```bash
git clean -nd           # dry-run，看會刪什麼
git clean -fd           # 實際刪（force）
git clean -fdx          # 連 ignored 的也刪（例如 build dir）
```

⚠️ **超危險**：`.env`、`node_modules/`、build 產物一起沒。

## 12.12 保險動作

做任何 destructive reset 前，**加個 tag / branch**：
```bash
git tag backup-$(date +%s)
git reset --hard ...
# 出事：git reset --hard backup-1234567890
```

或用 `git stash`：
```bash
git stash push -m "before big reset"
git reset --hard ...
# 出事：git stash pop
```

## 12.13 練習

1. Workdir 有改動 + index 有 staged → 只取消 staging 不丟 workdir 改動。
2. 做 3 個 commit，用 `reset --soft` 合成一個。
3. 做 3 個 commit，push（sandbox），用 `revert` 取消中間那個。
4. 做一堆改動，`reset --hard HEAD`，觀察哪些沒了、哪些還在（untracked 檔）。

## 本章重點
- **檔案還原：`restore`**，不動 HEAD
- **撤回 commit（本地）：`reset`**，改歷史
- **撤回 commit（已 public）：`revert`**，安全、加新 commit
- `reset --soft`（留改動 staged）/ `--mixed`（留改動 unstaged）/ `--hard`（全砍 ⚠️）
- Destructive 操作前：tag / branch 保險
- `git clean -fd` 清 untracked（也危險）
