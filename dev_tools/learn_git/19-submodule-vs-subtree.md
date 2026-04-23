# Ch19: Submodule 的坑 vs Subtree

兩個把「其他 repo 放進我的 repo」的機制。**都不完美**，各有坑。本章幫你選。

## 19.1 問題

你的 repo 依賴另一個 repo：
- 公司共用的 library
- 別人的 open source library（想鎖特定版本）
- 一組 dotfiles

三種處理方式：
1. **Submodule**：指向另一個 repo 的特定 commit
2. **Subtree**：把另一個 repo 的內容**複製**進來
3. **Package manager**：npm / Cargo / pip 之類（**絕大多數情況的正解**）

先確認：你**真的需要** submodule 或 subtree 嗎？能用 package manager 就用。

## 19.2 Submodule

### 加 submodule
```bash
git submodule add https://github.com/other/lib external/lib
```

這會：
1. Clone other/lib 到 `external/lib`
2. 在 root 建 `.gitmodules`
3. 在 index 記錄 `external/lib` 是個 submodule，指向某 commit hash

`.gitmodules`：
```ini
[submodule "external/lib"]
    path = external/lib
    url = https://github.com/other/lib
```

```bash
git add .gitmodules external/lib
git commit -m "Add lib submodule"
```

### Clone 含 submodule 的 repo
```bash
git clone <url>
git submodule update --init --recursive
# 或一步到位
git clone --recurse-submodules <url>
```

### Submodule 的 commit 指針

Submodule 在主 repo 裡記錄的是**指向特定 commit 的 pointer**。主 repo 的某個 commit 決定「submodule 用哪個版本」。

看：
```bash
cd external/lib
git log -1
# 某個 commit hash

cd ../..
git ls-files --stage external/lib
# 160000 commit <hash> 0 external/lib
# ↑ 160000 是 submodule 特殊 mode
```

## 19.3 Submodule 的坑（超多）

### 坑 1：忘記 init
新 clone 後 `external/lib/` 是空資料夾，沒 submodule 內容。
```bash
git submodule update --init --recursive
```

### 坑 2：submodule 裡的改動忘記 commit
```bash
cd external/lib
# ... 改 code ...
cd ..
git status
# modified: external/lib (modified content)
```

你的主 repo 只會記「submodule 指標變了」，**submodule 內的改動你要進去 commit 並 push 到 submodule 的 remote**。

### 坑 3：detached HEAD
進 submodule 後 `git status` 常看到：
```
HEAD detached at abc1234
```

因為 submodule 是用 commit hash checkout 的，不是 branch。改東西要自己 `git switch main`（或該用的 branch）再改再 commit。

### 坑 4：不同人看到不同版本
隊友 pull 主 repo，submodule 沒自動更新：
```bash
git pull
git submodule update     # 要記得跑
# 或一次到位
git pull --recurse-submodules
git config --global submodule.recurse true  # 設成預設
```

### 坑 5：branch 切換
切主 repo 的 branch，submodule 不會自動切到對應版本。
```bash
git switch feature
# submodule 還在 main branch 的版本
git submodule update
# 切到 feature branch 指的版本
```

### 坑 6：遞迴 submodule
submodule 的 submodule 的 submodule... `--recursive` 都要加。

### 坑 7：Clone fork 的坑
Fork 一個含 submodule 的 repo，submodule URL 指原作者。如果你想用自己 fork 的 submodule：改 `.gitmodules` URL 或 `git config -f .gitmodules submodule.xxx.url`。

### 坑 8：submodule 內狀態亂掉
```bash
git submodule deinit -f external/lib
rm -rf .git/modules/external/lib
git submodule update --init external/lib
```

核彈級重設。

## 19.4 Submodule 日常命令

```bash
# 更新所有 submodule 到主 repo 指定的版本
git submodule update --init --recursive

# 看 submodule 狀態
git submodule status

# 進 submodule 做更新（pull 到 submodule 的 main 最新）
git submodule update --remote

# 執行命令在每個 submodule
git submodule foreach 'git status'
git submodule foreach --recursive 'git fetch'

# 移除 submodule
git submodule deinit external/lib
git rm external/lib
rm -rf .git/modules/external/lib
git commit -m "Remove lib submodule"
```

## 19.5 Submodule 推薦設定

```bash
git config --global submodule.recurse true
# 之後 pull / checkout 自動遞迴處理 submodule

git config --global diff.submodule log
# diff 看到 submodule 改動時顯示 commit log 而不是 "Subproject commit abc..def"

git config --global status.submoduleSummary true
# status 顯示 submodule summary
```

## 19.6 Subtree

把另一個 repo **複製**進來當子目錄，不保持連結。

### 加
```bash
git subtree add --prefix=external/lib https://github.com/other/lib main --squash
```

這會把 other/lib 的 main branch 整個內容複製到 `external/lib/`，建一個 merge commit 紀錄。**不**留 `.gitmodules` 或指標。

### 更新
```bash
git subtree pull --prefix=external/lib https://github.com/other/lib main --squash
```

### 推改回 upstream
```bash
git subtree push --prefix=external/lib https://github.com/other/lib my-changes
```

## 19.7 Subtree 的優缺

### 優
- **Clone 後沒額外步驟**（內容已在裡面）
- **不需要 submodule 那堆命令**
- **歷史可完整保留**（不用 `--squash`）

### 缺
- **主 repo 變大**（實體複製）
- **推回 upstream 較笨重**
- **URL 要每次打**（不像 submodule 存在 `.gitmodules`）

工具 `git-subtree` 有些不順：
- URL 要手打或自己記
- `git log` 會混進第三方的歷史

## 19.8 三者對照

| | Submodule | Subtree | Package manager |
|---|---|---|---|
| 儲存 | 指標 + 獨立 repo | 實體複製 | 不進 repo |
| 使用者 setup | 要 init | 無 | 要 `npm install` 之類 |
| 鎖版本 | 天然（commit hash） | 手動 pull | lockfile |
| 推改動回 upstream | 清楚 | 用 `subtree push` | N/A |
| Repo 大小 | 小 | 變大 | 不影響 |
| 命令複雜度 | 高 | 中 | 低 |

## 19.9 選擇建議

```
你依賴的東西是...

已經有 package manager（npm/cargo/pip/go mod/...）
  → 用 package manager ✅

沒有，但你**只消費**不修改
  → Submodule（可鎖 version）

沒有，你要**修改**且推回
  → Subtree + subtree push
  → 或 fork submodule + 指向自己 fork

一次性導入、不再管原 repo
  → Subtree（或直接複製貼上）
```

## 19.10 實務觀察

### Submodule 常見 use case
- 大公司 monorepo 不現實，把 library 拆多 repo + submodule 連結
- CI tool 版本鎖定（`actions/checkout` 的 `.github/workflows` 依賴）
- **Unreal Engine** / **Unity** 專案常見（第三方 plugin）

### Subtree 常見 use case
- 短期需求、一次性引入
- 寫作 / 教材包含 reference code

### 絕大多數專案
**都不用**。package manager + 版本鎖 + CI 就夠。

## 19.11 Submodule 常見 workflow

### 更新 library 版本
```bash
cd external/lib
git fetch
git checkout v2.1.0    # 或某個 commit
cd ..

# 主 repo 現在「看到 submodule 指到新版本」
git add external/lib
git commit -m "Bump lib to v2.1.0"
git push
```

### 隊友拉你的更新
```bash
git pull
git submodule update --init --recursive
```

或：
```bash
git pull --recurse-submodules
```

## 19.12 Subtree 常見 workflow

### 加
```bash
git remote add lib-upstream https://github.com/other/lib
git subtree add --prefix=external/lib lib-upstream main --squash
```

加 remote 後續 pull 更方便：
```bash
git subtree pull --prefix=external/lib lib-upstream main --squash
```

### 改
直接改 `external/lib/` 的檔，`git add` + `git commit` 當正常檔案。

### 推回 upstream
```bash
git subtree push --prefix=external/lib lib-upstream my-feature-branch
# 到 GitHub 開 PR
```

## 19.13 `git-subrepo`（第三方）

`git-subtree` 太原始，有社群工具 `git-subrepo`：
- `git subrepo clone URL path`
- `git subrepo pull path`
- `git subrepo push path`

介面乾淨多了，但要額外安裝：
```bash
# 裝
git clone https://github.com/ingydotnet/git-subrepo
# 設 PATH 引用它
```

## 19.14 練習

Sandbox：
1. Clone 一個含 submodule 的 open source repo（例如 `sqlite`、`redis`）。觀察 `.gitmodules` 和 `git submodule status`。
2. 建兩個自己的 test repo（A 和 B），把 B 加為 A 的 submodule，commit，push。
3. 把 B 改成 subtree 試試：先 `git submodule deinit`，再 `subtree add`。
4. 解決一次「submodule 進入 detached HEAD 狀態但我想改 code」的情境。

## 19.15 本章重點
- **Submodule**：指標 + 獨立 repo，鎖版本乾淨但命令多、坑多
- **Subtree**：實體複製，簡單但主 repo 變大、推回笨重
- **首選 package manager**，submodule/subtree 是逼不得已
- Submodule 必配 `submodule.recurse=true`、`diff.submodule=log`
- Submodule 裡做改動要進去**單獨 commit + push 到 submodule 的 remote**
- `git submodule update --init --recursive` 是 clone 後必跑
- 選擇：只用 → submodule；要改推回 → subtree 或 fork submodule
