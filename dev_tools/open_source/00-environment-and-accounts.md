# Ch 0 — 環境與帳號設定

> **目標**：把協作需要的環境一次架好——git 身分設定、SSH/GPG 認證、GitHub 帳號、`gh` CLI、commit 簽署。並理解一件新手常忽略的事：**你的 commit 帶著你的名字到處跑，協作從「設定好你是誰」開始**。

> **環境**：git 2.40+、GitHub、`gh` CLI 2.x。範例以 Linux / macOS 為主，Windows 用 Git Bash 或 WSL2（差異會標注）。

## 為什麼從「設定你是誰」開始

一個人寫程式時，git 設定錯了沒人在意。但一旦協作，你的每個 commit 都會**永久**帶著你設定的名字和 email，出現在別人的專案歷史裡。設錯了——用了公司 email 貢獻個人專案、名字打錯、email 對不上 GitHub 帳號導致貢獻不被算到你頭上——這些都是要事後痛苦修正的。

而且協作需要「證明你是你」：push 到別人給你權限的 repo、簽署你的 commit、用 API 操作 GitHub。這些都要先把認證設好。所以這一章先把「身分」和「認證」弄對，後面才順。

## 先建立直覺：三層身分

協作時你的「身分」其實有三層，很多人搞混：

```
   ┌─────────────────────────────────────────────┐
   │ 1. git 身分（commit 上的 name + email）       │ ← 寫進每個 commit，給人看的
   │    git config user.name / user.email          │
   ├─────────────────────────────────────────────┤
   │ 2. GitHub 帳號（你登入的那個）                 │ ← 平台身分，擁有 repo/發 PR
   ├─────────────────────────────────────────────┤
   │ 3. 認證憑證（SSH key / token / GPG key）       │ ← 證明「你有權做這個操作」
   └─────────────────────────────────────────────┘
```

- **git 身分**：純粹是 commit 裡的文字標籤，git **不驗證**它的真假（你可以設成任何人的名字——這也是為什麼需要簽署，下面講）。
- **GitHub 帳號**：你在平台上的身分，決定你能 push 哪些 repo、發哪些 PR。
- **認證憑證**：證明「執行這個 push/API 操作的人，真的是這個 GitHub 帳號」。

GitHub 怎麼把 commit（第 1 層）關聯到你的帳號（第 2 層）？**靠 email**——如果 commit 的 email 對應到你 GitHub 帳號驗證過的 email，這個 commit 就會算到你的貢獻（綠格子）。email 對不上，貢獻就「不存在」。這是新手最常踩的雷。

## 設定 git 身分

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"

# 確認
git config --global user.name
git config --global user.email
```

`--global` 寫進 `~/.gitconfig`，對所有 repo 生效。

**email 的關鍵**：這個 email 必須是你 GitHub 帳號裡驗證過的 email 之一（Settings → Emails），否則 commit 不會關聯到你。

> 隱私選項：不想暴露真實 email？GitHub 提供 noreply email（`12345+username@users.noreply.github.com`，在 Settings → Emails → "Keep my email addresses private" 找）。用它當 `user.email`，commit 仍會關聯到你，但不洩漏真實信箱。開源貢獻很多人這樣做。

### per-repo 身分（重要！）

如果你同時做公司專案和個人/開源專案，**不要全用同一個身分**。為特定 repo 覆蓋全域設定：

```bash
cd ~/work/company-project
git config user.email "you@company.com"   # 不加 --global，只對這個 repo

cd ~/oss/my-contribution
git config user.email "you@personal.com"  # 開源用個人身分
```

> 踩雷：用公司 email 貢獻開源專案，會把你的貢獻和雇主綁在一起（有些公司有政策問題），且離職後那 email 失效。**開源一律用個人/noreply email。** 進階做法是用 conditional include（`includeIf`）依目錄自動切換，Ch 21 會提。

## GitHub 帳號設定

註冊 GitHub 帳號（github.com）後，至少做這幾件：

1. **驗證 email**：Settings → Emails，確認你 git 要用的 email 在清單且已驗證。
2. **設大頭貼與名字**：協作是跟人打交道，一個空白頭像 + 亂碼帳號名比較難取得信任。
3. **（建議）開兩步驟驗證（2FA）**：GitHub 現在對貢獻者逐步強制 2FA。早點設好。

## 認證：SSH vs HTTPS token

push 到 GitHub 需要證明身分。兩種主流方式：

| 方式 | 怎麼運作 | 適合 |
|---|---|---|
| **SSH key** | 用公私鑰對認證，設定一次後 push 不用再輸密碼 | 長期、本機開發（推薦）|
| **HTTPS + PAT** | 用 Personal Access Token 當密碼 | CI、暫時環境、防火牆只開 443 |

> 重要：GitHub 從 2021 年起**不再接受帳號密碼**做 git 操作。HTTPS 要用 Personal Access Token（PAT）當「密碼」，不是你的登入密碼。

### 設定 SSH key（推薦）

```bash
# 1. 產生 key（ed25519 是現代推薦演算法）
ssh-keygen -t ed25519 -C "you@example.com"
# 一路 Enter（可設 passphrase 增加安全性）

# 2. 看公鑰內容
cat ~/.ssh/id_ed25519.pub
#   ssh-ed25519 AAAA... you@example.com

# 3. 把「公鑰」（.pub 那個）貼到 GitHub:
#    Settings → SSH and GPG keys → New SSH key

# 4. 測試
ssh -T git@github.com
#   Hi username! You've successfully authenticated...
```

之後 clone 用 SSH URL（`git@github.com:user/repo.git`），push 不用再輸入任何東西。

> 鐵則：**只貼公鑰（`.pub`）**，私鑰（`id_ed25519`，沒有 `.pub`）永遠留在本機、絕不外洩。私鑰外洩等於別人能冒充你。

### HTTPS + PAT（替代）

```bash
# GitHub Settings → Developer settings → Personal access tokens
# 產生一個 token（建議用 fine-grained，限定 repo 與權限）
# clone 用 HTTPS URL，push 時帳號填 username、密碼貼 token
git config --global credential.helper store   # 或用系統 keychain，避免明文存
```

## `gh` CLI：協作的瑞士刀

`gh` 是 GitHub 官方命令列工具——開 PR、審 review、管 issue、操作 repo，全部不用開瀏覽器。Part 3 起大量使用，先裝好。

```bash
# 安裝（依平台）
# macOS:    brew install gh
# Ubuntu:   見 https://github.com/cli/cli/blob/trunk/docs/install_linux.md
# Windows:  winget install GitHub.cli

# 登入（互動式，會幫你設好認證，甚至 SSH key）
gh auth login

# 確認
gh auth status
```

> `gh auth login` 很貼心——它能順便幫你設定 git 認證（SSH 或 HTTPS token），所以如果你還沒設上面的認證，跑這個就一次搞定。

## commit 簽署：證明 commit 真的是你

回到「三層身分」的伏筆：git 的 `user.name`/`user.email` 是純文字，**任何人都能偽造**。我可以把我的 `user.email` 設成你的，做一個 commit，看起來就像你寫的。協作中這是個信任問題。

**簽署（signing）** 解決它：用密碼學簽章證明「這個 commit 真的出自持有某把私鑰的人」。GitHub 會對驗證過的簽署 commit 顯示綠色 "Verified" 標章。

兩種簽署方式：

```bash
# 方式 1：用 SSH key 簽署（較新、較簡單，git 2.34+）
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true        # 預設所有 commit 都簽

# 方式 2：GPG key 簽署（傳統）
# gpg --gen-key 產生，git config user.signingkey <key-id>
```

用 SSH 簽署的話，還要在 GitHub 把同一把公鑰**額外**加為 "Signing Key"（Settings → SSH and GPG keys，type 選 Signing Key）。

```bash
# 簽一個 commit
git commit -S -m "message"      # -S 明確簽署（若設了 commit.gpgsign true 則自動）

# 驗證
git log --show-signature -1
```

> 認識論誠實：簽署不是必須的——很多開源專案不要求。但有些專案（尤其安全相關、或用 DCO 的）要求 signed commit；且 GitHub 的 "Verified" 標章在協作中增加可信度。先設好，需要時就有。Ch 21 會談 DCO 的 `Signed-off-by`（那是另一回事，不是密碼學簽署，別搞混）。

## 一份協作友善的 .gitconfig

把常用設定一次弄好（`~/.gitconfig`）：

```ini
[user]
    name = Your Name
    email = you@personal.com          # 開源用的身分

[init]
    defaultBranch = main              # 新 repo 預設分支叫 main（不是 master）

[pull]
    rebase = false                    # pull 時 merge（Ch 6 會解釋 rebase 選項）

[fetch]
    prune = true                      # fetch 時清掉遠端已刪的 branch 參照

[push]
    autoSetupRemote = true            # 第一次 push 新 branch 不用打 --set-upstream（git 2.37+）

[diff]
    colorMoved = default              # 把「搬移的程式碼」用不同色標示，review 好讀

[rerere]
    enabled = true                    # 記住衝突解法，重複衝突自動套用（Ch 8）

[gpg]
    format = ssh
[user]
    signingkey = ~/.ssh/id_ed25519.pub
[commit]
    gpgsign = true
```

每一項後面章節都會用到，現在設好省得之後回頭。

## 踩雷集錦

1. **commit 的 email 對不上 GitHub 帳號**：貢獻不會算到你（沒綠格子、PR 作者顯示怪）。先 `git config user.email` 確認，且該 email 要在 GitHub Settings → Emails 驗證過。
2. **全域用公司 email 貢獻開源**：把你和雇主綁一起、離職後 email 失效。開源用個人/noreply email，必要時 per-repo 覆蓋。
3. **不小心 push 了私鑰**：私鑰（`id_ed25519` 無 `.pub`）外洩等於身分被盜。只貼 `.pub`，私鑰絕不進任何 repo。
4. **以為帳號密碼能 git push**：GitHub 2021 起不接受。HTTPS 要用 PAT，或改用 SSH。
5. **把簽署（signing）和 DCO 的 `Signed-off-by` 搞混**：前者是密碼學簽章（"Verified" 標章），後者只是 commit message 裡的一行文字宣告（Ch 21）。兩回事。
6. **default branch 還是 master**：新 repo 設 `init.defaultBranch = main`，跟現代慣例一致（多數專案主線叫 main）。

## 進階：再往深一層

- **多帳號管理**：同時有公司帳號和個人帳號？用 SSH config 的 Host alias（`Host github-personal` → 對應不同 key）+ per-repo remote URL 區分。或用 `includeIf` 依目錄切 gitconfig（Ch 21）。
- **`gh` 的 SSH key 管理**：`gh ssh-key add` / `gh ssh-key list` 直接從命令列管 GitHub 上的 key。
- **commit 簽署的驗證鏈**：GitHub 怎麼驗 SSH 簽署——它比對 commit 簽章 vs 你帳號註冊的 signing key。理解這個，你才懂 "Unverified" 標章代表什麼（簽了但 key 沒註冊、或根本沒簽）。
- **credential helper**：`git config credential.helper` 可接系統 keychain（macOS Keychain、Windows Credential Manager、libsecret）安全存 token，別用 `store`（明文）。
- **企業 GitHub（GHE）**：公司可能用 GitHub Enterprise（自架或 ghe.com），`gh auth login` 要指定 hostname。

## 動手練習

1. 設好 `user.name`/`user.email`，`git config --list` 確認；到 GitHub Settings → Emails 確認 email 已驗證。
2. 產生 SSH key、貼到 GitHub、`ssh -T git@github.com` 測試成功。
3. 裝 `gh`、`gh auth login`、`gh auth status` 確認登入。
4. 設定 commit 簽署，做一個測試 commit，`git log --show-signature` 看簽章；push 到一個測試 repo，看 GitHub 是否顯示 "Verified"。
5. 在一個 repo 裡 `git config user.email "other@example.com"`（不加 `--global`），`git config user.email` 確認它覆蓋了全域——理解 per-repo 身分。

## 本章重點整理

- 協作有三層身分：git 身分（commit 上的 name/email，純文字、可偽造）、GitHub 帳號（平台身分）、認證憑證（SSH/token/GPG）。
- GitHub 靠 **email** 把 commit 關聯到你的帳號——email 對不上，貢獻不算數。
- 開源用個人/noreply email，per-repo 覆蓋公司身分；私鑰絕不外洩。
- 認證用 SSH key（推薦）或 HTTPS + PAT（帳號密碼已不接受）。
- `gh` CLI 是協作瑞士刀；commit 簽署用密碼學證明 commit 真的是你（"Verified"）。

## 自我檢核

- [ ] GitHub 怎麼判斷一個 commit 是你寫的？為什麼 email 設錯貢獻就不算？
- [ ] 為什麼開源貢獻不該用公司 email？怎麼為特定 repo 切換身分？
- [ ] SSH 認證裡，哪個檔案可以貼到 GitHub、哪個絕不能外洩？
- [ ] commit 的 `user.name` 能不能偽造？簽署（signing）解決了什麼問題？
- [ ] `gh auth login` 除了登入還幫你做了什麼？

## 延伸閱讀

### 官方文件

- **[GitHub Docs: Connecting to GitHub with SSH](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)**
  - **讀哪裡**：Generating a new SSH key、Adding a new SSH key、Testing your connection。
  - **和本章的關聯**：SSH 設定的權威步驟，含各平台差異。

- **[GitHub Docs: Managing commit signature verification](https://docs.github.com/en/authentication/managing-commit-signature-verification)**
  - **讀哪裡**：SSH commit signing、Telling Git about your signing key。
  - **和本章的關聯**：commit 簽署的完整設定；"Verified" 標章怎麼來的。

- **[GitHub Docs: Setting your commit email address](https://docs.github.com/en/account-and-profile/setting-up-and-managing-your-personal-account-on-github/managing-email-preferences/setting-your-commit-email-address)**
  - **讀哪裡**：noreply email 的設定。
  - **和本章的關聯**：解決「貢獻不算到我頭上」與隱私。

### 工具文件

- **[gh CLI: gh auth](https://cli.github.com/manual/gh_auth_login)**
  - **讀哪裡**：login 的選項（含順便設 git 認證）。
  - **和本章的關聯**：一行搞定認證設定。

### 書籍

- **[Pro Git, Ch 1.6 — First-Time Git Setup](https://git-scm.com/book/en/v2/Getting-Started-First-Time-Git-Setup)**
  - **這本書的定位**：git 設定的權威；本章 git config 部分的完整版。
  - **讀哪幾章**：1.6 即可；簽署在 7.4 (Signing Your Work)。

設定好「你是誰」之後，下一章退一步看大局：你已經會的 git，到了多人世界少了什麼？

→ [Ch 1 為什麼需要協作流程](./01-why-collaboration-workflow.md)
