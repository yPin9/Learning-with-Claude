# Ch24: Signed Commits

Commit / tag 可以加密簽章，證明是你做的。GitHub 顯示「Verified」。

**是否必要**：取決於團隊。安全敏感專案、open source 維護者常用。一般商業專案可有可無。

## 24.1 為什麼

Git commit 的 `author` / `committer` 是純文字欄位——**誰都能寫成你的名字**：

```bash
git -c user.name="Linus" -c user.email="torvalds@linux.org" commit -m "Add backdoor"
```

GitHub 顯示作者是 Linus。沒人阻止。

**Signed commit** 用密碼學證明「是某 key 持有者簽的」，不能偽造。

## 24.2 兩種簽章方式

### GPG（傳統）
- 老牌、功能多
- 設定複雜、key 管理麻煩
- 跨平台、工具成熟

### SSH（Git 2.34+，現代）
- 用你既有的 SSH key
- 設定簡單
- 新但已穩定

**新專案推薦 SSH**。本章都講。

## 24.3 SSH signing 設定

### Step 1：告訴 git 用 SSH
```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
```

（你的 public key 路徑，Ch0 建 SSH key 時產生的 `.pub` 檔）

### Step 2：開自動簽
```bash
git config --global commit.gpgsign true
git config --global tag.gpgsign true
```

### Step 3：GitHub 註冊 signing key
到 GitHub Settings → SSH and GPG keys → New SSH key：
- Key type 選 **Signing Key**（不是 Authentication Key）
- 貼 public key 內容

**同一個 SSH key 可以當 Authentication + Signing 兩種用**——但在 GitHub 要各加一個 entry（或在某些情況自動雙用）。

### Step 4：建 allowed_signers（驗證用）
```bash
mkdir -p ~/.config/git
cat ~/.ssh/id_ed25519.pub | awk '{print $1" "$2}' > /tmp/key
echo "ohtanishohei715@gmail.com $(cat /tmp/key)" > ~/.config/git/allowed_signers

git config --global gpg.ssh.allowedSignersFile ~/.config/git/allowed_signers
```

這檔列出「哪個 email 可以被哪個 key 簽名」。`git log --show-signature` 會用這驗。

### 測試
```bash
echo "test" > t.txt
git add t.txt
git commit -m "test signing"

git log --show-signature -1
# Good "ssh" signature with ED25519 key SHA256:...
```

Push 到 GitHub 看：commit 旁出現 **Verified** 徽章。

## 24.4 GPG signing 設定（備用）

簡述，現在建議 SSH。

```bash
# 裝 gpg
pacman -S gnupg      # MSYS2

# 建 key
gpg --full-generate-key
# 選 RSA/RSA, 4096, 不過期

# 列 key
gpg --list-secret-keys --keyid-format=long

# 匯出 public key
gpg --armor --export YOUR_KEY_ID

# 設 git
git config --global user.signingkey YOUR_KEY_ID
git config --global commit.gpgsign true
```

GitHub → Settings → SSH and GPG keys → New GPG key → 貼 `--armor --export` 的結果。

### GPG 的痛
- Key 到期要 renew
- 移機要搬 key
- Password cache 難搞
- Windows 上 GPG agent 偶爾罷工

**我強烈推薦換 SSH signing**。

## 24.5 自動簽但需要 key unlock

每次 commit 需要輸密碼解 key？用 agent：

### SSH agent（預設通常好）
```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
```

### GPG agent
```bash
# ~/.gnupg/gpg-agent.conf
default-cache-ttl 28800    # 8 hours
max-cache-ttl 86400
```

## 24.6 簽 tag

```bash
git tag -s v1.0 -m "Release 1.0"    # signed annotated tag

git tag -v v1.0                      # verify
```

Release 用 signed tag 是好習慣。

## 24.7 Verify commit

```bash
git log --show-signature
git log --pretty="format:%h %G? %s"
# %G? : G (good) / B (bad) / U (unknown) / N (no signature)
```

### `%G?` 代碼
- `G`：good signature
- `B`：bad signature
- `U`：good but unknown
- `N`：no signature
- `X`：expired signature
- `E`：expired key

## 24.8 GitHub 的 Verified 徽章

Commit 在 GitHub 顯示 **Verified** 的條件：
1. 有簽章
2. 簽章有效
3. Signing key 已註冊在該使用者 GitHub 帳號
4. Commit 的 author/committer email 對應那 GitHub 帳號

缺一項就是 **Unverified** 或無徽章。

## 24.9 Branch protection：強制簽章

Repo Settings → Branches → Branch protection rule：
- ✅ Require signed commits

之後 push 到該 branch 的 commit 必須是 verified signed。

## 24.10 常見問題

### 問題 1：push 後顯示 Unverified
- Signing key 沒上傳 GitHub
- Commit email 和 GitHub 帳號 email 不一致
- 確認：`git log -1 --pretty=%ae` 和 GitHub Settings → Emails

### 問題 2：新機器沒 sign key
```bash
# 複製 private key 到新機器（安全地）
scp ~/.ssh/id_ed25519 new-machine:~/.ssh/
# 或重新 ssh-keygen 一把新的、加到 GitHub
```

### 問題 3：CI 的 commit 怎麼 sign
- GitHub Actions 的 bot 預設不 sign
- 加 GPG key 到 secret，CI 步驟用它 sign
- 或用 GitHub 的 `github-actions[bot]` 自動 sign（需 `permissions` 設定）

### 問題 4：Amend 後 sign 還在？
```bash
git commit --amend
```

預設沿用原簽章**如果內容沒變**；不一樣就重新簽（前提 `commit.gpgsign=true`）。

### 問題 5：Cherry-pick / rebase 破壞簽章
Cherry-pick / rebase 產**新** commit，原簽章失效。**新 commit 會用你的 key 重新簽**（如果 `commit.gpgsign=true`）——但意義是「我簽了這個修改過的版本」，不是「原作者簽過」。

## 24.11 Verify 別人的簽章

```bash
# 別人的 allowed_signers 要在你的 file 裡
echo "alice@example.com ssh-ed25519 AAAAC3Nz..." >> ~/.config/git/allowed_signers
# 或 Git 2.39+ 可以 fetch from GitHub automatically with ssh-keygen
```

```bash
git log --show-signature
```

## 24.12 Open source 專案要求

一些專案要求：
- **Linux kernel**：`Signed-off-by` trailer（`git commit -s`）——**不是密碼學簽章，只是聲明**
- **大多 CNCF 專案**：DCO (Developer Certificate of Origin)，用 `-s`
- **某些安全敏感專案**：真的 signed commit

分清：
- **`commit -s`**：加 `Signed-off-by` trailer（DCO 用）
- **`commit -S`**：密碼學簽章（verified 徽章用）

兩個大小寫不同、意思差很遠。

```bash
git commit -s -S -m "..."    # 兩者都來
```

## 24.13 Key 管理最佳實踐

- **Authentication key** 和 **Signing key** 可以同一把，也可以分開
- 分開的好處：一把外洩不影響另一把
- **備份 private key**（離線安全地方，password manager 或硬體 key）
- **失竊時 revoke**：GitHub 移除該 key，舊 commit 仍有效（可能），新 commit 用不了
- **到期時 rotate**：SSH 沒過期概念，GPG 要設

## 24.14 Hardware keys

更安全：YubiKey / Nitrokey 等：
- Private key **從不離開** 硬體
- 每次 sign 要按鍵確認
- 掉了人拿不到 key

設定稍複雜：
```bash
ssh-keygen -t ed25519-sk -C "..."    # FIDO2 on a security key
```

開發者高階配置。

## 24.15 練習

1. 設 SSH signing、commit 一個檔、上 GitHub 看 Verified。
2. `git log --show-signature` 看輸出。
3. 在另一台機器（或 VM）clone repo、verify 你的 commit（要設 allowed_signers）。
4. Sign 一個 tag 發 release，看 GitHub release 頁。

## 24.16 本章重點
- Commit author 是可偽造文字欄位，**簽章才能證明身份**
- **現代推 SSH signing**：設定簡單、跟 SSH key 一把共用
- 關鍵 config：`gpg.format=ssh`、`user.signingkey`、`commit.gpgsign=true`
- `allowedSignersFile` 驗證他人簽章
- GitHub Verified 徽章需要 key 上傳 + email 配對
- `commit -s` (DCO trailer) 和 `commit -S` (密碼學簽章) 不同
- Branch protection 可強制 signed commits
- 失竊 key 要 revoke + rotate
