# Ch20: Git Hooks

**本章重點章**（你選擇的深度目標之一）。Hook 是 git 在某些事件前後自動執行的腳本。

## 20.1 Hook 位置

```
.git/hooks/
├── applypatch-msg.sample
├── commit-msg.sample
├── post-update.sample
├── pre-applypatch.sample
├── pre-commit.sample        ← 最常見
├── pre-push.sample          ← 次常見
├── pre-rebase.sample
├── pre-receive.sample       ← server-side
├── prepare-commit-msg.sample
└── update.sample
```

`.sample` 副檔名的**不會執行**。啟用：
```bash
mv pre-commit.sample pre-commit
chmod +x pre-commit
```

檔案是 shell 腳本（或任何可執行——Python、Node 都行，看 shebang）。

## 20.2 Hook 分類

### Client-side
**在你的機器上跑**：
- `pre-commit`：`git commit` 前（可阻擋 commit）
- `prepare-commit-msg`：開編輯器前（可修改 template）
- `commit-msg`：訊息寫完後（可檢查）
- `post-commit`：commit 完成後（無法 block）
- `pre-rebase`：rebase 前
- `post-checkout`：checkout 後（例如切 branch）
- `post-merge`：merge 後
- `pre-push`：push 前（可 block）

### Server-side
**在 remote repo（GitHub 不開放）跑**：
- `pre-receive`：接到 push 前
- `update`：每個 ref 更新前
- `post-receive`：接到 push 後

**GitHub 不允許自訂 server-side hook**，你得用 Actions 或 Apps 等效代替。

**本章主要講 client-side**。

## 20.3 `pre-commit`：最常用

在 `git commit` 執行前跑。**非 0 exit 就阻擋 commit**。

簡單例子：
```bash
#!/bin/bash
# .git/hooks/pre-commit

# 不允許 commit 到 main
branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$branch" = "main" ]; then
    echo "Direct commit to main is not allowed!"
    exit 1
fi
```

存到 `.git/hooks/pre-commit`、`chmod +x`，下次 `git commit` 會先跑。

### 例 2：禁止 WIP 訊息
```bash
#!/bin/bash
# .git/hooks/commit-msg
msg=$(cat "$1")
if [[ "$msg" =~ ^(WIP|wip|fixme)$ ]]; then
    echo "WIP commits not allowed."
    exit 1
fi
```

### 例 3：禁止 stray `console.log`
```bash
#!/bin/bash
# .git/hooks/pre-commit

staged_js=$(git diff --cached --name-only --diff-filter=ACM | grep '\.js$')
if [ -z "$staged_js" ]; then
    exit 0
fi

if git diff --cached "$staged_js" | grep -E '^\+.*console\.log'; then
    echo "Found console.log in staged changes."
    exit 1
fi
```

### 例 4：跑 linter
```bash
#!/bin/bash
# .git/hooks/pre-commit

# Python
staged_py=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$')
if [ -n "$staged_py" ]; then
    echo "$staged_py" | xargs ruff check || exit 1
    echo "$staged_py" | xargs ruff format --check || exit 1
fi

# C++
staged_cpp=$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.(cpp|hpp|c|h)$')
if [ -n "$staged_cpp" ]; then
    echo "$staged_cpp" | xargs clang-format --dry-run -Werror || exit 1
fi
```

## 20.4 Hook 的 staged vs workdir 陷阱

最常見問題：
```bash
# 你 stage 了 A.js，但 workdir 還在繼續改 A.js
git add A.js
# 繼續改 A.js（還沒 stage）
git commit          # pre-commit 應該檢查什麼？
```

**應該檢查 staged 的版本**（index 中的），不是 workdir。

### 正確做法：先 stash unstaged
```bash
#!/bin/bash
# .git/hooks/pre-commit

# 暫存 unstaged 改動
git stash push --keep-index --include-untracked --quiet

# 回到結束時還原
cleanup() { git stash pop --quiet 2>/dev/null; }
trap cleanup EXIT

# 現在 workdir = staged，可以安全跑檢查
npm test || exit 1
```

或用 `git diff --cached` 只看 staged 內容。

## 20.5 `commit-msg`：檢查訊息格式

`$1` 是訊息檔路徑：
```bash
#!/bin/bash
# .git/hooks/commit-msg

msg=$(cat "$1")

# Conventional Commits 格式
if ! [[ "$msg" =~ ^(feat|fix|docs|style|refactor|perf|test|chore)(\(.+\))?:\  ]]; then
    echo "Commit message must start with type: (feat|fix|docs|...): ..."
    exit 1
fi

# Subject 長度
first_line=$(head -n 1 "$1")
if [ ${#first_line} -gt 72 ]; then
    echo "Subject line too long (${#first_line} > 72)"
    exit 1
fi
```

## 20.6 `prepare-commit-msg`：填 template

commit message template：
```bash
#!/bin/bash
# .git/hooks/prepare-commit-msg

msg_file=$1
commit_source=$2   # message | template | merge | squash | commit

# 只在沒有既有訊息時填
if [ -z "$commit_source" ]; then
    branch=$(git rev-parse --abbrev-ref HEAD)
    # 從 branch name 抽 issue 號（feature/ISSUE-123-xxx）
    issue=$(echo "$branch" | grep -oE '[A-Z]+-[0-9]+')
    if [ -n "$issue" ]; then
        echo "[$issue] $(cat $msg_file)" > "$msg_file"
    fi
fi
```

每次 commit 自動加 issue 號。

## 20.7 `pre-push`：推前檢查

```bash
#!/bin/bash
# .git/hooks/pre-push

# 從 stdin 讀要 push 的 ref（git 會傳）
while read local_ref local_sha remote_ref remote_sha; do
    # 不允許推到 main 的特定 pattern
    if [[ "$remote_ref" == "refs/heads/main" ]]; then
        if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
            # 新 branch，不檢查
            continue
        fi
        # 檢查 main 上有沒有 WIP commit
        commits=$(git log --oneline "$remote_sha..$local_sha")
        if echo "$commits" | grep -iE 'WIP|fixme|hack'; then
            echo "Refusing to push WIP commits to main"
            exit 1
        fi
    fi
done
```

### 例：push 前跑完整測試
```bash
#!/bin/bash
# .git/hooks/pre-push
make test || { echo "Tests failed"; exit 1; }
```

## 20.8 `post-commit`：commit 完通知

```bash
#!/bin/bash
# .git/hooks/post-commit

# 通知 desktop
notify-send "Commit done" "$(git log -1 --pretty=%s)"
```

無法 block commit（已完成），通常做 side effect（通知、統計）。

## 20.9 `post-checkout` / `post-merge`

切 branch 後自動跑：
```bash
#!/bin/bash
# .git/hooks/post-checkout

# 如果 package.json 改了，auto npm install
prev_head=$1
new_head=$2
branch_checkout=$3   # 1 = branch checkout

if [ "$branch_checkout" = "1" ]; then
    if git diff "$prev_head" "$new_head" --name-only | grep -q 'package.json'; then
        echo "package.json changed, running npm install..."
        npm install
    fi
fi
```

## 20.10 Hook 是 **本地** 的

`.git/hooks/` 在 **`.git/`裡**，**不會被 commit**。你的 hook 不會同步給隊友。

三種解決：

### 方法 1：把 hook 放 repo 裡
```
your-repo/
├── .githooks/
│   ├── pre-commit
│   └── commit-msg
└── ...
```

設 `core.hooksPath`：
```bash
git config core.hooksPath .githooks
```

之後隊友 clone 後跑一次：
```bash
git config core.hooksPath .githooks
```

或在 README 寫「setup 步驟」。

### 方法 2：用框架
- **pre-commit**（Python 寫的，最流行）
- **husky**（Node 寫的，前端常見）
- **lefthook**（Go 寫的）

這些提供 "install hook script" 功能，通常綁 `npm install` / `make setup` 自動裝。

### 方法 3：Makefile / setup 腳本
```makefile
# Makefile
setup:
	cp scripts/hooks/* .git/hooks/
	chmod +x .git/hooks/*
```

隊友 `make setup` 後有 hook。

## 20.11 pre-commit 框架（不用自己寫）

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-merge-conflict
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.0
    hooks:
      - id: ruff
      - id: ruff-format
```

```bash
pip install pre-commit
pre-commit install     # 裝 hook 到 .git/hooks/
```

之後 commit 自動跑。省了自己寫 shell 腳本。

**但你選的目標是「自己寫 hook 不用框架」**，所以本章以 shell 為主。框架了解用法即可。

## 20.12 繞過 hook

偶爾 hook 壞了或你知道在做什麼：
```bash
git commit --no-verify     # 跳過 pre-commit 和 commit-msg
git push --no-verify       # 跳過 pre-push
```

**別濫用**。工具級 bypass 是權宜，不是常態。

## 20.13 Hook 可用環境變數

- `$GIT_DIR`：`.git` 路徑
- `$GIT_AUTHOR_NAME`、`$GIT_AUTHOR_EMAIL`
- stdin / 參數依 hook 而異（見 `man githooks` 或 `.sample` 檔）

## 20.14 完整範例：個人通用 pre-commit

```bash
#!/bin/bash
# .git/hooks/pre-commit

set -e

# 1. 禁止 stray debug code
if git diff --cached | grep -E '^\+.*(console\.log|println!|dbg!|import pdb)'; then
    echo "Debug print detected in staged changes"
    exit 1
fi

# 2. 禁止 commit 到保護 branch
branch=$(git rev-parse --abbrev-ref HEAD)
if [[ "$branch" =~ ^(main|master|release/.+)$ ]]; then
    echo "Direct commit to $branch is prohibited"
    exit 1
fi

# 3. 檢查大檔
max_size_kb=500
for file in $(git diff --cached --name-only --diff-filter=ACM); do
    if [ -f "$file" ]; then
        size_kb=$(du -k "$file" | cut -f1)
        if [ "$size_kb" -gt "$max_size_kb" ]; then
            echo "File $file is ${size_kb}KB, exceeds ${max_size_kb}KB"
            echo "Consider Git LFS for large files"
            exit 1
        fi
    fi
done

# 4. 檢查 secrets
if git diff --cached | grep -iE '(api[_-]?key|secret|password|token)\s*=\s*["\047][^"\047]+'; then
    echo "Possible secret in staged changes"
    exit 1
fi

echo "pre-commit checks passed"
```

## 20.15 Debug hook

```bash
# 直接執行 hook script 看輸出
.git/hooks/pre-commit

# 開 verbose
set -x   # 在 hook 開頭加
```

Hook 失敗時 git 顯示它的 output，通常足以 debug。

## 20.16 Hook 的最佳實踐

1. **快**：pre-commit 超過 5 秒就煩人；跑 full test 太重，留給 pre-push / CI
2. **明確錯誤訊息**：告訴使用者為啥 fail、怎麼修
3. **可 bypass**：善待 `--no-verify`，不要讓 hook 變政治鬥爭
4. **`set -e`** 開頭：錯誤即退出
5. **考慮 staged vs unstaged**：用 `git stash --keep-index` 或 `git diff --cached`
6. **同事能 opt-in**：團隊 hook 放 `.githooks/` 而不是強制

## 20.17 練習

1. 寫一個 pre-commit hook，禁止 commit 包含 `TODO` 字樣的 code。
2. 寫一個 commit-msg hook，強制訊息符合 `[A-Z]+-\d+: ...` 格式（JIRA 風格）。
3. 寫一個 pre-push hook，push 到 main 前跑 `make test`。
4. 把以上 hook 放進 `.githooks/` 並在 README 寫 setup 指令。

## 20.18 本章重點
- Hook 位置：**`.git/hooks/`**（`.sample` 不執行）
- Client-side 常用：`pre-commit`、`commit-msg`、`pre-push`
- **GitHub 不允許 server-side hook**，用 Actions 等效
- **Hook 不會被 commit**，同步給隊友要 `.githooks/` + `core.hooksPath`
- `pre-commit` 要處理 staged vs unstaged 問題（stash trick）
- `--no-verify` 繞過，要留活路
- 框架（pre-commit/husky）省你寫 shell，但懂原理才會用
