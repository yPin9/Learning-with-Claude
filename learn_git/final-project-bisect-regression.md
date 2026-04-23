# Final Project: 用 Bisect + 自動化找出 Regression Commit

**目標**：模擬真實世界的「某功能突然壞了」，用 `git bisect run` 自動二分搜尋找出罪魁 commit。
**用到**：Ch3 log/diff、Ch11 reflog、Ch21 bisect、Ch20 hooks（驗證）、Ch22 worktree（加速）

## 任務概述

你會接到一個 repo：
- 有 50+ commit
- 中間某個 commit 引入了 bug（某個函式的某個情境開始失敗）
- 表面上每個 commit 都能編譯、跑、看起來正常
- 只有特定 test case 能 detect 到 bug

用 `git bisect run` + 自動化 test 腳本找出那個 commit。

## Setup：建 sandbox repo

這個 script 會建一個 50-commit 的 fake 專案，某個 commit 偷偷破壞某個 test：

```bash
#!/bin/bash
# setup-sandbox.sh
set -e
ROOT=/tmp/bisect-final
rm -rf "$ROOT"
mkdir -p "$ROOT"
cd "$ROOT"

git init -q
git config user.email test@test.com
git config user.name test

# 初始：簡單的 calculator
cat > calc.py <<'EOF'
def add(a, b): return a + b
def sub(a, b): return a - b
def mul(a, b): return a * b
def div(a, b): return a / b if b != 0 else None
EOF

cat > test_calc.py <<'EOF'
from calc import add, sub, mul, div
assert add(1, 2) == 3
assert add(-1, 1) == 0
assert add(0, 0) == 0
assert sub(5, 3) == 2
assert sub(0, 0) == 0
assert mul(2, 3) == 6
assert mul(0, 5) == 0
assert mul(-2, 3) == -6
assert div(10, 2) == 5
assert div(10, 0) is None
assert div(-10, 2) == -5
print("all pass")
EOF

git add calc.py test_calc.py
git commit -q -m "initial calculator"

# 隨機弄一堆假改動（不破 test）
COMMITS=(
  "add docstring to add"
  "add docstring to sub"
  "style: fix spacing"
  "refactor: use f-string in error message"
  "add type hints to add"
  "add type hints to sub"
  "rename internal var"
  "extract constant"
  "inline helper"
  "add README"
  "update README with examples"
  "add .gitignore"
  "format with black"
  "extract zero check"
  "add comment on div"
  "rename test helper"
  "reorder imports"
  "more type hints"
  "rename constant"
  "docstring updates"
  "fix typo in comment"
  "another docstring"
  "remove unused var"
  "simplify branch"
  "add newline"
  "trim trailing whitespace"
)

for i in "${!COMMITS[@]}"; do
    msg="${COMMITS[$i]}"
    # 每 commit 加一行 comment，無傷
    echo "# commit $i: $msg" >> calc.py
    git add calc.py
    git commit -q -m "$msg"
done

# 👇 THE BUG: 在第 N 個 commit 偷偷破壞 mul
# 選一個「中間」的位置，例如第 20 個額外 commit
git log --oneline | wc -l   # 應該 27

# 在現在這 commit 破壞 mul
python3 -c "
import re
s = open('calc.py').read()
s = re.sub(r'def mul\(a, b\): return a \* b', 'def mul(a, b): return a + b if b == 0 else a * b', s)
open('calc.py', 'w').write(s)
"
# 這個 "fix" 其實讓 mul(x, 0) 回傳 x 而不是 0
git add calc.py
git commit -q -m "simplify mul zero case"
# ↑ 這是 bug commit

# 再加一堆無傷大雅的 commit
TAIL_COMMITS=(
  "more docstrings"
  "rename parameter"
  "extract magic number"
  "add type alias"
  "consolidate imports"
  "reorder functions"
  "add header comment"
  "align columns"
  "use named constant"
  "refactor conditional"
  "add logging placeholder"
  "clarify error message"
  "update copyright"
  "minor style tweak"
  "rename internal helper"
  "adjust indentation"
  "fix capitalization"
  "remove stale comment"
  "add separator"
  "final polish"
  "bump version"
  "add README badge"
  "update CHANGELOG"
)

for i in "${!TAIL_COMMITS[@]}"; do
    msg="${TAIL_COMMITS[$i]}"
    echo "# post-$i: $msg" >> calc.py
    git add calc.py
    git commit -q -m "$msg"
done

# 加一個 test_mul_zero.py
cat > test_mul_zero.py <<'EOF'
from calc import mul
assert mul(3, 0) == 0, f"mul(3, 0) should be 0, got {mul(3, 0)}"
assert mul(5, 0) == 0
assert mul(0, 0) == 0
print("mul zero test pass")
EOF
git add test_mul_zero.py
git commit -q -m "add test for mul with zero"

echo ""
echo "Sandbox ready at $ROOT"
echo "Total commits: $(git rev-list --count HEAD)"
echo ""
echo "Verify bug:"
cd "$ROOT"
python3 test_mul_zero.py && echo "(no bug!)" || echo "^^^ bug present"
```

跑它：
```bash
chmod +x setup-sandbox.sh
./setup-sandbox.sh
cd /tmp/bisect-final
python3 test_mul_zero.py
# 應該 fail
```

## 任務：找出 bug commit

### Part 1：人工 bisect（先體驗）

```bash
git bisect start
git bisect bad HEAD
# 找一個 known-good commit（最初的那個）
git log --oneline | tail -1
git bisect good <first-commit>

# Git 會自動 checkout 中間的 commit
# 你手動跑 test
python3 test_mul_zero.py
# 結果決定：
git bisect good    # 或 git bisect bad
```

重複直到 git 告訴你找到了。

**記錄**：你手動跑了幾次？

### Part 2：自動化 bisect

寫一個 `test-bisect.sh`：

```bash
#!/bin/bash
# test-bisect.sh
cd /tmp/bisect-final

# 跑新的 test（test_mul_zero.py 可能不存在於舊 commit）
# 解法：把 test 複製過來（在 bisect 過程中）
if [ ! -f test_mul_zero.py ]; then
    # 從 bisect 開始時的 HEAD（之後會設）把 test 抓來
    # 簡單做法：把 test 放在外面
    cat > /tmp/test_mul_zero.py <<'EOF'
from calc import mul
assert mul(3, 0) == 0
assert mul(5, 0) == 0
assert mul(0, 0) == 0
EOF
    cp /tmp/test_mul_zero.py .
fi

python3 test_mul_zero.py || exit 1
exit 0
```

但這有問題——舊 commit 沒 `test_mul_zero.py`，bisect 期間修改 working tree 是**反模式**。

### 正確方法 1：test 檔放 repo 外

```bash
cat > /tmp/bug-test.py <<'EOF'
import sys
sys.path.insert(0, '/tmp/bisect-final')
from calc import mul
assert mul(3, 0) == 0
assert mul(5, 0) == 0
EOF

cat > /tmp/run-test.sh <<'EOF'
#!/bin/bash
cd /tmp/bisect-final
python3 /tmp/bug-test.py
EOF
chmod +x /tmp/run-test.sh

cd /tmp/bisect-final
git bisect start HEAD <first-commit-hash>
git bisect run /tmp/run-test.sh
```

Git 自動二分找到 first bad commit：
```
...
<hash> is the first bad commit
commit <hash>
    simplify mul zero case
```

```bash
git bisect reset
```

### 正確方法 2：用 worktree 隔離

Ch22 的 worktree 讓 bisect 不影響主 worktree：

```bash
cd /tmp/bisect-final
git worktree add /tmp/bisect-wt HEAD

cd /tmp/bisect-wt
git bisect start HEAD <first-commit>
git bisect run /tmp/run-test.sh

# 找到後
git show <first-bad-commit>

cd /tmp/bisect-final
git worktree remove /tmp/bisect-wt
```

在 worktree 裡 bisect，主 workdir 不受影響——你可以同時做別的事。

## Part 3：Bug 分析與修復

找到 commit 後：

```bash
git show <first-bad-commit>
# diff：
# -def mul(a, b): return a * b
# +def mul(a, b): return a + b if b == 0 else a * b
```

**Bug 分析**：
- 作者想「優化」`mul(x, 0)` 避免乘法
- 但 `a + b` (when b == 0) = `a`，不是 `0`
- 應該 `return 0 if b == 0 else a * b`

### 修復選項

#### 選項 A：revert
```bash
git switch main
git revert <first-bad-commit>
git push
```

安全、不改歷史。

#### 選項 B：修復後 commit
```bash
git switch main
# 改 calc.py
sed -i 's/def mul(a, b): return a + b if b == 0 else a \* b/def mul(a, b): return 0 if b == 0 else a * b/' calc.py
python3 test_mul_zero.py    # 應該 pass
git add calc.py
git commit -m "fix: mul(x, 0) returns 0, not x"
```

### 寫 post-mortem

建一個 `POSTMORTEM.md`（真實世界的好習慣）：
```markdown
# Regression: mul(x, 0) returns x instead of 0

## Impact
Functions depending on mul() with zero operand returned wrong results.

## Root cause
Commit <hash> "simplify mul zero case" introduced an incorrect
"optimization" that returned `a + b` instead of `0` when `b == 0`.

## Detection
Bug found via `git bisect run` with `test_mul_zero.py`.
27 revisions scanned in ~5 steps.

## Fix
Corrected mul() to return 0 when either operand is 0.

## Lessons
- Missing test coverage for mul(x, 0) case
- Commit message ("simplify") didn't describe actual change
- Need test at PR time for all arithmetic edge cases
```

## Part 4：進階 — hook 防再犯

寫一個 `pre-commit` hook（Practice C 的延伸），跑 `test_mul_zero.py`：

```bash
#!/bin/bash
# .git/hooks/pre-commit

staged=$(git diff --cached --name-only --diff-filter=ACM | grep 'calc\.py')
if [ -n "$staged" ]; then
    # stash 其他改動
    git stash push --keep-index --quiet 2>/dev/null || true
    trap 'git stash pop --quiet 2>/dev/null' EXIT
    
    # 跑 test
    python3 test_mul_zero.py || {
        echo "test_mul_zero.py failed; fix before commit"
        exit 1
    }
fi
```

## Part 5：壓力測試

挑戰：把 repo 改成 **200 個 commit**，在中間隨機位置放 bug。你的自動化 bisect 應該：
- 在 log2(200) ≈ 8 步找到
- 完成 < 30 秒（假設 test 很快）

**觀察**：`git bisect run` 的輸出顯示找了幾步、哪些 commit skip 了（如果有）。

## Part 6：多 bug 情境

真實世界可能有**連續兩個 bug**（bug B 被 bug A 遮蔽）。

擴展 sandbox，在 `<bug-1>` 後的某 commit 加第二個不同 bug（讓 test_X 失敗）。

挑戰：
1. 先 bisect 找 bug-1，修
2. 再 bisect 找 bug-2，修
3. 如果兩個 bug 影響同 test？（這很棘手，要 bisect 多個 test）

## 完成清單

完整 final project 包含：

- [ ] `setup-sandbox.sh`：產生有 bug 的 50-commit repo
- [ ] `run-test.sh`：bisect 用的自動化 test
- [ ] 實際跑 `git bisect run` 找到 first bad commit
- [ ] `POSTMORTEM.md`：root cause + lessons
- [ ] Fix commit（revert 或手動修）
- [ ] `.git/hooks/pre-commit` 防再犯
- [ ] Worktree 版本（在 worktree 裡 bisect，不動主 workdir）
- [ ] 擴展到 200 commit 的壓力測試

## 本 Final Project 重點

- **Git bisect run 是 debug 核武**——O(log N) 定位 regression
- 自動化 test **必須 deterministic**，否則 bisect 迷路
- Test 檔**放 repo 外**，避免 bisect 搬動 working tree 出問題
- Worktree 隔離 bisect 過程，主 workdir 照常工作
- 找到 bug 後：fix + postmortem + hook 防再犯
- 好的 commit message 讓 bisect 結果更有意義（"simplify mul zero case" 語焉不詳，應該寫清楚做了什麼）

## 回顧：這個課程學了什麼

走到這裡你該能：

**Part 1 心智模型**
- Git 是 snapshot + graph 不是 diff stack
- 三區 workdir/index/HEAD 獨立分開

**Part 2 日常**
- log/diff 的 power 用法（`-S` / `-L` / `blame -Mw`）
- switch/restore 取代 checkout
- pull.rebase + force-with-lease

**Part 3 寫歷史**
- Interactive rebase 流暢
- atomic commit 與 conventional commits
- stash 進階

**Part 4 災難救援**
- Reflog + fsck 是兩張救命符
- reset/revert/restore 正確場景

**Part 5 底層**
- 四種 object、`.git/` 結構
- gc/fsck/maintenance

**Part 6 協作**
- GitHub PR 完整流程
- 衝突解決（rerere + diff3）
- submodule/subtree/LFS 的取捨
- **寫 hook、用 bisect**

**Part 7 進階**
- **worktree 多分支並行**
- 大檔策略（LFS / filter-repo）
- Signed commits

從此你不再怕 git。踩到坑時有完整工具應對，PR 歷史寫得漂亮，hook + CI 防線穩固。
