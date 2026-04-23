# Practice B: 把爛 Commit 整理成乾淨 PR

**目標**：從「10 個 WIP commit 混雜各種改動」整理成「3-4 個 atomic commit」。
**用到**：Ch7 interactive rebase、Ch8 amend/cherry-pick、Ch9 commit message、Ch2 三區 add -p

## 準備：爛 branch sandbox

執行這 script 產生一個典型的「亂做 branch」：

```bash
mkdir /tmp/clean-practice && cd /tmp/clean-practice
git init
git config user.email test@test.com
git config user.name test

# 初始 main
cat > app.py <<'EOF'
def greet(name):
    return f"Hello, {name}"

def add(a, b):
    return a + b
EOF
cat > README.md <<'EOF'
# My App
Does stuff.
EOF
git add app.py README.md
git commit -m "initial"

# 建 feature branch，亂寫一通
git switch -c feature/auth-and-fixes

# 1. 開始寫 auth
cat > auth.py <<'EOF'
def login(user, pwd):
    return True
EOF
git add auth.py
git commit -m "wip"

# 2. 改了 app.py 和 auth.py（混）
cat >> app.py <<'EOF'

def multiply(a, b):
    return a * b
EOF
cat > auth.py <<'EOF'
def login(user, pwd):
    if pwd == "admin":
        return True
    return False
EOF
git add .
git commit -m "add multiply and auth check"

# 3. typo fix in README
sed -i 's/stuff/things/' README.md
git add README.md
git commit -m "typo"

# 4. 繼續改 auth
cat > auth.py <<'EOF'
import hashlib

def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

def login(user, pwd):
    return hash_password(pwd) == stored_hash(user)

def stored_hash(user):
    return "..."
EOF
git add auth.py
git commit -m "better auth"

# 5. add test file
cat > test_auth.py <<'EOF'
def test_login():
    assert login("alice", "secret") == True
EOF
git add test_auth.py
git commit -m "test"

# 6. 修 multiply bug
sed -i 's/return a \* b/return a * b  # fixed/' app.py
git add app.py
git commit -m "fix multiply"

# 7. 更 README
cat >> README.md <<'EOF'

## Auth
Use login(user, pwd) to authenticate.
EOF
git add README.md
git commit -m "docs"

# 8. 又改 auth（意識到 bug）
cat > auth.py <<'EOF'
import hashlib

def hash_password(pwd):
    return hashlib.sha256(pwd.encode()).hexdigest()

USERS = {}

def register(user, pwd):
    USERS[user] = hash_password(pwd)

def login(user, pwd):
    return USERS.get(user) == hash_password(pwd)
EOF
git add auth.py
git commit -m "fix auth bug"

# 9. fix test 配合新 auth
cat > test_auth.py <<'EOF'
from auth import register, login

def test_login():
    register("alice", "secret")
    assert login("alice", "secret") == True
    assert login("alice", "wrong") == False
EOF
git add test_auth.py
git commit -m "update test"

# 10. 加個 .gitignore
cat > .gitignore <<'EOF'
__pycache__/
*.pyc
EOF
git add .gitignore
git commit -m "gitignore"
```

檢查結果：
```bash
git log --oneline
# (10 個 ugly commits)
```

## 任務

把這 10 個 commit 整理成：

1. `feat: add multiply to app` — `app.py` 的 `multiply` 函式（含 bug fix，不要分兩步）
2. `feat: add user authentication with password hashing` — 完整的 auth（`auth.py` 最終版 + `test_auth.py` 最終版）
3. `docs: describe auth in README` — README 的兩個改動（typo fix + auth 說明）合一
4. `chore: add .gitignore`

**要求**：
- 每個 commit **訊息格式**遵循 Conventional Commits
- 每個 commit 的**改動只關係它自己的主題**
- **Subject 50 字內**、祈使句、首字大寫
- 完成後 `git log --oneline` 只有 4 個 commit（除了 initial）

## 建議流程

### Step 1：理解現狀
```bash
git log --oneline
git log --stat
```

看每個 commit 動了什麼檔。

### Step 2：決定目標
上面 4 個 commit 的順序無所謂，但**相關改動要放一起**。

### Step 3：Rebase interactive

```bash
git rebase -i main
```

編輯 todo，用各種操作（squash / fixup / drop / reword / edit）整理。

**技巧**：
- 先把相關 commit `reorder` 到相鄰
- 再 `fixup` / `squash` 合併
- 用 `reword` 改訊息

### Step 4：如果有改動橫跨 commit

```
commit 2 "add multiply and auth check"
     ↓ 裡面混了兩件事：multiply + auth check
```

這個要**拆**。流程：
```bash
# rebase -i 時把那 commit 標 edit
git rebase -i main

# 在 todo 裡：
edit abc1234 add multiply and auth check
...

# 暫停後
git reset HEAD^           # 拆開這 commit，改動回 workdir
git status                # 看所有改動

# 分批 stage
git add app.py
git commit -m "feat: add multiply"

git add auth.py
git commit -m "feat: basic login check"

git rebase --continue
```

### Step 5：確認

```bash
git log --oneline main..HEAD
# 應該 4 個 atomic commit

git log --stat
# 每個 commit 動的檔合理
```

跑一下 diff 看沒少東西：
```bash
git diff main..HEAD
# 和原本第 10 個 commit 對比，沒漏改動
```

## 參考策略（不是唯一解）

```bash
git rebase -i main
```

Todo（原始是舊→新）：
```
pick  001 wip                              ← auth 開始
pick  002 add multiply and auth check      ← 混
pick  003 typo                             ← README
pick  004 better auth                      ← auth
pick  005 test                             ← test
pick  006 fix multiply                     ← multiply
pick  007 docs                             ← README
pick  008 fix auth bug                     ← auth
pick  009 update test                      ← test
pick  010 gitignore                        ← .gitignore
```

策略：
```
reword 002 as "feat: add multiply"         ← 先處理。但它混 auth！要 edit 拆
edit   002
...
```

實際上更乾淨的做法：**先別 rebase，先用 cherry-pick / patch 重建**。

### 替代策略：reset 到 main，重新挑選

```bash
git log --oneline    # 記下最終 commit hash

git reset --mixed main
# 所有改動回到 workdir，10 個 commit 被「去歷史化」
# 現在像從頭開始做

git status
# 看到所有檔改動
```

現在用 `git add -p` 精準挑：
```bash
# 1. gitignore
git add .gitignore
git commit -m "chore: add .gitignore"

# 2. multiply（只加 app.py 的 multiply 部分）
git add -p app.py
# 選擇相關 hunk
git commit -m "feat: add multiply to app"

# 3. auth
git add auth.py test_auth.py
git commit -m "feat: add user authentication with password hashing

Uses sha256 of password stored in memory. Register first, then
login with same password to authenticate.
"

# 4. README
git add README.md
git commit -m "docs: describe auth in README"
```

這個方法**乾脆**——不用解歷史糾纏，等於重新整理。

但失去「誰何時做什麼」的時間線——看團隊想不想要這資訊。

## 進階挑戰

### Challenge 1：保留原始作者資訊
`git rebase -i` 預設保留 author。如果你用 reset 方法，會失去。要保留：
```bash
# rebase -i 法
# 所有 commit author 不動
```

### Challenge 2：每個 commit 都要能獨立 build
每 commit 後 pytest 要 pass（當然簡單例子不真有 test，想像）：
```bash
git rebase -i --exec "pytest" main
```

### Challenge 3：多人協作情境
如果這 branch 有別的 co-author 也做過 commit——rebase 時要保留他們的 author 資訊，且不能 rebase shared branch。

這時通常 **PR 保留多 commit**，merge 時 squash。不整理 feature branch。

## 完成檢查

- [ ] `git log --oneline main..HEAD` 正好 4 個 commit
- [ ] Subject 都 ≤ 50 字元
- [ ] Commit message 風格一致（Conventional Commits）
- [ ] `git log --stat` 看每個 commit 動的檔合理
- [ ] `git diff main..HEAD -- app.py` 和原本第 10 個 commit 的 app.py 一致
- [ ] 每個 commit 獨立有意義

## 回顧問題

1. `rebase -i` 和 `reset --mixed + 重建` 你覺得哪個順手？
2. `git add -p` 在這練習幫到多少？
3. 如果原本有 50 個 commit 要整理到 4 個，你會怎麼做？
4. 如果其中 3 個 commit 是別人 push 的，你還能整理嗎？該嗎？

## 本練習重點
- 整理 commit 歷史是 PR 前的常態工作，**不是作弊**
- `rebase -i` 是主要工具；`reset --mixed` + 重做是另一條路
- `add -p` 配合使用，拆細的改動
- Atomic commit 的好處：revert / cherry-pick / blame / bisect 都友善
- **你個人的 feature branch** 隨便 rewrite，**public branch** 絕不可
- 完成後 `push --force-with-lease`
