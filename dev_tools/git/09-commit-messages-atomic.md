# Ch9: Commit Message 與 Atomic Commits

Commit 不只是「存檔點」，是**和未來自己（和同事）溝通的訊息**。本章講怎麼寫好 commit message 和切好 commit 粒度。

## 9.1 為什麼重要

好的 commit history 讓你：
- `git log` 讀得懂發生什麼事
- `git blame` 追究一行 code 的來源有意義
- `git bisect` 找 regression 的精度
- Code review 聚焦、好理解
- 半年後維護時少崩潰一次

壞的 commit history：
- 全是 "update", "fix", "wip"
- 一個 commit 混 10 件事
- `git blame` 結果是 "merge branch"

## 9.2 Commit message 的基本格式

```
<subject>

<body>

<footer>
```

### Subject（第一行）

規則：
- **50 字內**（硬上限 72）
- **用祈使句**：`Add feature X`，不要 `Added feature X` / `Adding feature X`
- **首字大寫**
- **句尾不加句號**
- 簡短說「這 commit 做了什麼」

```
Good:  Add user login with OAuth
Good:  Fix null pointer in parser
Bad:   added login                  # 小寫 + 過去式
Bad:   fix                          # 什麼 fix？
Bad:   Updated the README.md file.  # 廢話 + 句號
```

### Body（可選，隔一空行）

規則：
- **每行 72 字內**（方便 `git log` 顯示）
- 說「**為什麼**」，不重複 diff 能看出的「**什麼**」
- 多段落 OK

### Footer（可選）

- `Fixes #123` / `Closes #456`
- `Signed-off-by: ...`
- `Co-authored-by: ...`
- Breaking change 提示

## 9.3 範本

```
Add rate limiting to /api/login endpoint

Brute-force attempts from the same IP were consuming database
connections and degrading response time. This adds a token-bucket
rate limiter (10 req / minute / IP) with Redis backend.

Decided against in-memory limiter because load balancer spreads
requests across pods.

Fixes #4821
```

讀 subject 知道做了什麼；讀 body 知道為什麼、為什麼不用其他方案。

## 9.4 Conventional Commits（一個常見慣例）

```
<type>(<scope>): <subject>

<body>
```

`type` 常用：
- `feat`: 新功能
- `fix`: bug 修復
- `docs`: 文件
- `style`: 格式（沒改邏輯）
- `refactor`: 重構（沒加功能沒修 bug）
- `perf`: 性能
- `test`: 測試
- `build`: build 系統
- `ci`: CI 設定
- `chore`: 雜事

範例：
```
feat(auth): add OAuth login
fix(parser): handle empty input
docs: update README with new API
refactor(db): extract query builder
```

不是強制，但有用處：
- 自動產生 changelog
- Breaking change 容易標記（`feat!: ...` 或 footer `BREAKING CHANGE: ...`）
- 團隊統一風格

## 9.5 壞訊息 vs 好訊息

### 壞
```
update
```
你以後看 log 會想殺人。

### 壞
```
Fixed bug
```
哪個 bug？

### 壞
```
Work from Tuesday
```
不是 diary。

### 普通
```
Add login feature
```
OK 但沒說為什麼、怎麼做。

### 好
```
Add passwordless login via email magic link

Password reuse from the leaked Breachmap dataset was our
#1 support ticket. Magic links remove the password surface
entirely while staying compatible with mobile email clients.

- Generate 128-bit token, 15 min TTL, single-use
- Store hash only (never the token itself)
- Rate limit 3 req/hour/email

Fixes #4312
```

## 9.6 Atomic Commits

**一個 commit 做一件事**——概念上能用一句話描述。

### 反例
```
commit abc1234
    Refactor db layer, add OAuth, fix typo in README
```

三件事混在一起：
- Review 困難
- `bisect` 難定位（哪件事引入的 bug？）
- `revert` 只能三個一起取消

### 正確
```
commit 001
    Refactor db query builder into class
commit 002
    Add OAuth login endpoint
commit 003
    Fix typo in README
```

每個 commit 獨立有意義、可 revert、可 cherry-pick。

## 9.7 如何做到 atomic

### 技巧 1：開發時邊做邊 commit，之後再整理
先不管 message 好壞、commit 粒度：
```bash
git commit -m "wip"
git commit -m "try approach"
git commit -m "fix"
```

開 PR 前用 `git rebase -i` 整理成乾淨的 atomic commit（Ch7）。

### 技巧 2：`git add -p` 拆改動
```bash
# 你改了 auth.py 又改了 README.md 又修了 parser.py 的 bug
git add -p parser.py    # 只加 parser 的 bug fix
git commit -m "Fix null check in parser"

git add -p auth.py       # 加 auth 改動
git commit -m "Add OAuth login"

git add README.md
git commit -m "Document OAuth setup"
```

Interactive staging 讓你**同時改三件事也能 commit 成三個 atomic**。

### 技巧 3：一次只做一件事
最簡單：先做 bug fix、commit、再做 feature。心理上分段。

### 技巧 4：`--fixup` + `--autosquash`
邊開發發現舊 commit 有小問題：
```bash
git commit --fixup=<older-commit>
```

PR 前：
```bash
git rebase -i --autosquash main
```

自動把 fixup 併回原 commit，保持乾淨。

## 9.8 commit 粒度的尺

- **太小**：每改一行一個 commit（log 爆炸）
- **太大**：整個 feature 一個 commit（revert / review 困難）
- **剛好**：一個「概念上完整的改動」

好 commit 的測試：
1. 能用一句話描述（subject 夠用）
2. 能獨立 build / run（不會破 CI）
3. 能獨立被 revert 不影響其他功能
4. Body 能說清為什麼

## 9.9 PR 的 commit 歷史

兩種哲學：

### 哲學 1：保留乾淨的多 commit 歷史
PR 有 3-5 個 atomic commit，merge 用 **Rebase and merge** 或 **Create merge commit**。
- 優點：細節保留
- 缺點：要 reviewer 看懂每個 commit

### 哲學 2：Squash merge
不管 PR 內部多亂，**merge 時壓成一個 commit**。
- 優點：main 歷史超乾淨
- 缺點：丟失開發過程、bisect 粒度變粗

**看專案慣例**。Google / Meta 傾向 squash。Linux kernel 傾向保留多 commit。

## 9.10 不該混進 commit 的東西

- **無關改動**：bug fix 裡不要混格式化整個檔
- **IDE / OS 檔**：`.DS_Store`、`.vscode/` → 放 `.gitignore`
- **生成檔**：`.o`、`dist/`、`node_modules/` → `.gitignore`
- **秘密**：API key、password、cert → **永遠不要 commit**
- **debug 痕跡**：`print("here")`、`TODO: remove` → commit 前清掉

## 9.11 `.gitignore`

```
# OS
.DS_Store
Thumbs.db

# IDE
.vscode/
.idea/

# Build
*.o
*.so
build/
dist/
target/

# Dependencies
node_modules/

# Secrets
.env
*.pem

# 例外：排除某個 ignored 的檔
!important.log
```

### 全域 `.gitignore`
```bash
git config --global core.excludesfile ~/.gitignore_global
```

放 OS / IDE 相關的，避免每個 repo 重複。

## 9.12 已 commit 後發現不該 commit

### 最新 commit
```bash
git rm --cached file.env
git commit --amend --no-edit
```

### 歷史中
Secret 洩漏嚴重情況要 rewrite history：
```bash
git filter-repo --path secret.txt --invert-paths
# 或 BFG Repo-Cleaner
```

然後**立刻換掉那個 secret**（假設全世界都有了）。

## 9.13 實戰：寫好 commit 的檢查清單

Commit 前問自己：
- [ ] Subject 50 字內、祈使句
- [ ] Body 說清「為什麼」
- [ ] 只做一件事（atomic）
- [ ] 無秘密、無無關改動
- [ ] 獨立 build 能通過
- [ ] 能用一句話描述
- [ ] 半年後自己看得懂

## 9.14 練習

1. 找你某個老 repo 最近 10 個 commit，評分每個 commit message（0-5 分），寫出如果重做會怎麼寫。
2. 做一個「**故意亂**」的 branch：3 個 feature + 5 個 typo fix + 2 個 WIP，總共 10 commit。用 `rebase -i` 整理成 3-4 個 atomic commit。
3. 練 `git add -p`：一次做 3 件事的改動，只 stage 一件、commit，再 stage 下一件。

## 本章重點
- Subject: 50 字祈使句、首字大寫、無句號
- Body: 說**為什麼**，72 字折行
- **一個 commit 一件事**（atomic）
- `git add -p` 是拆 commit 的神器
- WIP 寫快點無所謂，PR 前用 `rebase -i` 整理
- 別 commit secret 和生成物，`.gitignore` 預防
- 團隊慣例（Conventional Commits / squash merge）看 project
