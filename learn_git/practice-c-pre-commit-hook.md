# Practice C: 手寫 Pre-commit Hook

**目標**：不靠任何框架（不用 pre-commit / husky / lefthook），純 shell 寫一個實用的 pre-commit hook。
**用到**：Ch2 三區（staged vs workdir）、Ch20 hooks

## 任務

寫一個 `pre-commit` hook，做以下檢查：

1. **禁止 commit 到 `main` 和 `release/*` branch**
2. **禁止 staged 改動中有 `console.log` / `print("DEBUG")` / `dbg!` / `TODO:`**
3. **禁止 staged 檔案中有明顯 secret pattern**（`API_KEY=xxx`、`password=xxx`）
4. **禁止 staged 檔案超過 500KB**
5. **任何 Python 檔案 staged 的版本必須通過 `ruff check`**
6. **任何 Markdown 檔尾不能有 trailing whitespace**

每項檢查錯誤要：
- **指出哪個檔、哪行**（能就盡量）
- **告訴 user 怎麼修或怎麼 bypass**
- **exit 1 阻擋 commit**

檢查完全通過才 exit 0。

## 關鍵技術點

### 要點 1：檢查 staged 版本，不是 workdir

```bash
# 錯：檢查 workdir
grep "console.log" file.js

# 對：檢查 staged 的版本
git show ":file.js" | grep "console.log"
# 或
git diff --cached file.js   # 只看 diff
```

### 要點 2：處理 staged ≠ workdir 的情境

```bash
# User 做了：
git add file.js        # 先加 A 版
# ... 又改 file.js ...  # workdir 現在是 B 版
git commit             # 要 commit A 版

# Hook 要檢查 A 版（staged），不是 workdir 的 B 版
```

一個 trick：stash unstaged 改動、跑 lint、pop 回來。

```bash
git stash push --keep-index --include-untracked --quiet
# 現在 workdir = staged
# ... 檢查 ...
git stash pop --quiet
```

### 要點 3：exit code

```bash
exit 0    # 通過 → commit 繼續
exit 1    # 任何非 0 → block commit
```

## 建議結構

```bash
#!/bin/bash
# .git/hooks/pre-commit
set -e

# 方便的 fail 函式
fail() {
    echo "❌ $1"
    echo "   Fix and retry, or bypass with: git commit --no-verify"
    exit 1
}

# Check 1: branch 保護
...

# Check 2: stray debug
...

# ...

echo "✅ pre-commit checks passed"
exit 0
```

## 實作

動手前先把 sandbox 建好：

```bash
mkdir /tmp/hook-practice && cd /tmp/hook-practice
git init
git config user.email test@test.com
git config user.name test
```

寫 hook 到 `.git/hooks/pre-commit`，`chmod +x`，然後各種案例測。

## 參考實作

**自己先寫一遍再看**。

<details>
<summary>參考實作</summary>

```bash
#!/bin/bash
# .git/hooks/pre-commit

set -e

fail() {
    echo "❌ $1" >&2
    [ -n "$2" ] && echo "   → $2" >&2
    echo "" >&2
    echo "Bypass with: git commit --no-verify (use sparingly)" >&2
    exit 1
}

has_staged_files() {
    ! git diff --cached --quiet
}

if ! has_staged_files; then
    echo "No staged changes"
    exit 0
fi

# ------------------------------
# Check 1: branch 保護
# ------------------------------
branch=$(git rev-parse --abbrev-ref HEAD)
if [[ "$branch" =~ ^(main|master)$ ]] || [[ "$branch" =~ ^release/ ]]; then
    fail "Direct commit to '$branch' is not allowed" \
         "Create a feature branch: git switch -c feature/xxx"
fi

# ------------------------------
# Check 2: stray debug code
# ------------------------------
debug_patterns='(console\.log|print\("DEBUG|dbg!\(|^\+\+.*TODO:)'
debug_hits=$(git diff --cached --unified=0 | grep -nE "^\+[^+].*$debug_patterns" || true)
if [ -n "$debug_hits" ]; then
    echo "⚠️  Found debug/TODO markers in staged changes:"
    echo "$debug_hits" | head -5
    fail "Remove debug code before committing"
fi

# ------------------------------
# Check 3: secret patterns
# ------------------------------
secret_pattern='(api[_-]?key|secret[_-]?key|password|auth[_-]?token)\s*=\s*["\047][^"\047]{8,}["\047]'
secret_hits=$(git diff --cached | grep -iE "^\+.*$secret_pattern" || true)
if [ -n "$secret_hits" ]; then
    echo "🔒 Possible secret in staged changes:"
    echo "$secret_hits" | head -3
    fail "Remove secrets; consider .env or secret manager"
fi

# ------------------------------
# Check 4: file size
# ------------------------------
max_kb=500
while IFS= read -r file; do
    [ -z "$file" ] && continue
    [ ! -f "$file" ] && continue
    size_kb=$(du -k "$file" | cut -f1)
    if [ "$size_kb" -gt "$max_kb" ]; then
        fail "$file is ${size_kb}KB (limit: ${max_kb}KB)" \
             "Use Git LFS for large files, or .gitignore"
    fi
done < <(git diff --cached --name-only --diff-filter=ACM)

# ------------------------------
# Check 5: Python ruff (只處理 staged 內容)
# ------------------------------
staged_py=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)
if [ -n "$staged_py" ] && command -v ruff >/dev/null 2>&1; then
    # Stash unstaged 改動
    if ! git diff --quiet; then
        git stash push --keep-index --include-untracked --quiet --message "pre-commit-stash"
        unstash() { git stash pop --quiet 2>/dev/null || true; }
        trap unstash EXIT
    fi
    
    # 跑 ruff on staged files
    echo "$staged_py" | xargs ruff check 2>&1 || fail "ruff check failed" "Fix Python lint errors"
fi

# ------------------------------
# Check 6: Markdown trailing whitespace
# ------------------------------
staged_md=$(git diff --cached --name-only --diff-filter=ACM | grep '\.md$' || true)
if [ -n "$staged_md" ]; then
    # 拿 staged 版本檢查，不是 workdir
    has_trailing=false
    for file in $staged_md; do
        # 取 staged 內容
        if git show ":$file" | grep -nE ' +$' >/dev/null; then
            echo "⚠️  $file: trailing whitespace on some lines"
            has_trailing=true
        fi
    done
    if $has_trailing; then
        fail "Markdown files have trailing whitespace" \
             "Run: sed -i 's/ *$//' *.md"
    fi
fi

echo "✅ pre-commit checks passed"
exit 0
```

</details>

## 測試案例

寫完 hook 後，每個 case 都測：

### Test 1：branch 保護
```bash
git switch -c main 2>/dev/null || git switch main
echo "x" > x.txt
git add x.txt
git commit -m "test"
# 應該 fail with "Direct commit to 'main'"
```

### Test 2：stray debug
```bash
git switch -c feature 2>/dev/null || git switch feature
echo "console.log('hi')" > a.js
git add a.js
git commit -m "test"
# 應該 fail
```

### Test 3：staged ≠ workdir
```bash
echo "clean = 1" > clean.js
git add clean.js
# 再改 workdir（把 staged 污染變 dirty）
echo "console.log('bad')" >> clean.js
git commit -m "test"
# 應該 pass！因為 staged 版本是 clean
```

這是最重要的 test——確認 hook 檢查的是 staged 不是 workdir。

### Test 4：secret
```bash
echo 'API_KEY = "sk-abc123def456"' > config.py
git add config.py
git commit -m "test"
# 應該 fail
```

### Test 5：large file
```bash
dd if=/dev/urandom of=big.bin bs=1M count=1
git add big.bin
git commit -m "test"
# 應該 fail
```

### Test 6：markdown whitespace
```bash
printf "line with trailing   \nok line\n" > doc.md
git add doc.md
git commit -m "test"
# 應該 fail
```

### Test 7：Python lint（假設有 ruff）
```bash
# 製造 lint error
echo "import os" > bad.py   # unused import
git add bad.py
git commit -m "test"
# 應該 fail（ruff F401）
```

### Test 8：bypass
```bash
git commit --no-verify -m "bypass test"
# 應該 pass（不跑 hook）
```

## 進階挑戰

### Challenge 1：團隊共用
把 hook 從 `.git/hooks/` 移到 repo 的 `.githooks/`，設 `core.hooksPath`：
```bash
mkdir -p .githooks
mv .git/hooks/pre-commit .githooks/
chmod +x .githooks/pre-commit

git config core.hooksPath .githooks
git add .githooks/pre-commit
git commit -m "Add pre-commit hook"
```

如此 hook 進 repo，隊友 clone 後跑一次 `git config core.hooksPath .githooks`。

### Challenge 2：快取
Hook 每次跑 ruff 很慢。加個快取，檔案沒變就跳：
```bash
cache_dir=".git/hook-cache"
mkdir -p "$cache_dir"

for file in $staged_py; do
    hash=$(git hash-object "$file")
    cache_file="$cache_dir/ruff-$hash"
    if [ -f "$cache_file" ]; then
        continue    # 之前檢查過，skip
    fi
    ruff check "$file" && touch "$cache_file"
done
```

### Challenge 3：並行
多個 staged 檔時並行跑 lint：
```bash
echo "$staged_py" | xargs -P 4 -I{} ruff check {}
```

### Challenge 4：log 每次 run
寫 log 到 `.git/hook.log`，記錄哪些 commit 觸發、哪些檢查 fail、花多久。之後可以分析「哪個 check 最常 block」、「hook 總共省了多少人工 review」。

### Challenge 5：漸進模式
有些 repo 老 code 不符規範，但新改動要符。hook 只檢查 **staged 的新增行**（`git diff --cached` 的 `+` 開頭行），不檢查既存 code。

## 完成檢查

- [ ] 所有 8 個 test 行為正確
- [ ] Hook 檢查 staged 版本，不是 workdir
- [ ] 每個 fail 有明確錯誤訊息和修復建議
- [ ] 通過所有檢查時清晰 exit 0
- [ ] 不會意外 leak stash（stash trick 安全）
- [ ] `--no-verify` 能 bypass

## 回顧問題

1. 為什麼不該檢查 `workdir` 而要檢查 staged？
2. `set -e` 在 hook 裡有什麼意外行為？（Answer：某些 grep 無命中返回 1 會意外終止，用 `|| true`）
3. 為什麼某些檢查要開 `trap` cleanup？
4. Hook 在 `git commit --amend` 會不會跑？（Answer：會）
5. Hook 在 merge commit 會不會跑？（Answer：會，但 staged 可能是空的——要處理）

## 本練習重點
- Hook = 純 shell script，不需要 framework
- **檢查 staged 不是 workdir** 是正確性關鍵
- `git diff --cached` / `git show :file` / stash trick 三種拿 staged 版本的方法
- 每個 check 要給明確錯誤 + 修復提示
- 留 `--no-verify` 活路
- Team 共用放 `.githooks/` + `core.hooksPath`
