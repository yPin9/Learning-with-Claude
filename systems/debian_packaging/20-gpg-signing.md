# Ch 20 — GPG 簽署機制

> **目標**：理解 Debian 套件的簽署信任鏈——簽什麼（.dsc/.changes 而非 .deb）、為什麼這樣設計、`debsign`/`dput` 的角色、GPG key 管理、以及第三方 repo 的 `Signed-By` 機制。

> **環境**：GnuPG 2.2.x、devscripts。本章涵蓋從本地簽署到上傳的完整信任鏈。

## 為什麼需要簽署？

套件管理的安全核心問題：你 `apt install nginx`，怎麼確定下載的 `.deb` 真的是 Debian 發布的，而不是被中間人換成植入後門的版本？

答案是 **GPG 簽署 + 信任鏈**。Debian 用公開金鑰密碼學保證：
- repo 的 metadata（Release 檔）由 Debian 的 archive key 簽署
- 你的系統預先信任這把 key（裝在 `debian-archive-keyring`）
- 下載的套件 checksum 對照已簽署的 metadata 驗證

任何環節被竄改，簽署驗證失敗，apt 拒絕安裝。理解這個信任鏈，你才能正確設定自己的 repo（Ch 21–23）和第三方 repo。

## 先建立直覺：簽的是清單，不是每個 .deb

```
直覺上你以為：每個 .deb 各自被簽署
實際上 Debian 的設計：

  .deb 檔案本身「不」帶簽署
        │
  簽署在「清單」層：
        │
  Release 檔（列出所有 Packages 檔的 checksum）← 被 archive key 簽署
        │  Release 裡有 Packages 檔的 checksum
        ▼
  Packages 檔（列出所有 .deb 的 checksum）
        │  Packages 裡有每個 .deb 的 checksum
        ▼
  .deb 檔案（checksum 對得上 = 沒被竄改）

信任鏈：信任 archive key → 驗證 Release → 驗證 Packages → 驗證 .deb
```

這個設計很精巧：**只需要簽一個 Release 檔**，就能透過 checksum 鏈保護成千上萬個 `.deb`。不需要對每個 `.deb` 個別簽署驗證。

## 簽署的兩個層次

```
層次一：上傳簽署（維護者 → archive）
  維護者用自己的 key 簽 .dsc 和 .changes
  證明「這個 source/這次上傳是我（可信維護者）做的」
  archive 收到後驗證簽署，確認來源

層次二：archive 簽署（archive → 使用者）
  archive 用 archive key 簽 Release 檔
  使用者的 apt 驗證這個簽署
  證明「這個 repo 的內容是 archive 發布的」
```

注意層次一簽 `.dsc`/`.changes`（不是 `.deb`），層次二簽 `Release`（不是 `.deb`）。`.deb` 從頭到尾不被個別簽署——它的完整性靠 checksum 鏈保證。

> 為什麼不直接簽 `.deb`？因為 `.deb` 是 build farm 對每個架構各自編出來的，不是維護者手上的東西。維護者簽的是他提供的 source（`.dsc`）和上傳意圖（`.changes`）。archive 簽的是它組織好的 repo（`Release`）。這個分工反映了「誰能對什麼負責」。

## 建立你的 GPG key

```bash
# 生成 key（現代推薦 ed25519 或 rsa4096）
gpg --full-generate-key
# 選 (9) ECC (sign and encrypt) → Curve 25519
# 或 (1) RSA and RSA → 4096
# 填入和 DEBEMAIL/DEBFULLNAME 一致的身份！

# 列出你的 key
gpg --list-secret-keys --keyid-format=long
# sec   ed25519/ABCD1234EF567890 2025-05-29 [SC]
#       <fingerprint>
# uid   Your Name <you@example.com>

# 匯出公鑰（給別人/repo 用）
gpg --armor --export you@example.com > mykey.asc
```

> key 的 email 必須和你 changelog/control 裡的 `Maintainer` 一致，否則 `debsign` 找不到對的 key 簽署。`DEBEMAIL` 環境變數（Ch 0）也要一致。

## debsign：簽署 .changes 和 .dsc

build 時用 `-us -uc` 跳過簽署（學習階段）。要正式發布時簽：

```bash
# 方法一：build 時直接簽（不加 -us -uc）
dpkg-buildpackage    # 會用你的 key 簽 .dsc 和 .changes
# 需要 .changes 的 Maintainer email 對應到你的 GPG key

# 方法二：先 build 不簽，事後 debsign
dpkg-buildpackage -us -uc
debsign greet_1.0-1_amd64.changes
# debsign 簽 .changes，並（透過 .changes 裡的 checksum 連動）確保 .dsc 也簽了

# 指定用哪把 key
debsign -k ABCD1234EF567890 greet_1.0-1_amd64.changes
```

`debsign` 簽署 `.changes`（和它引用的 `.dsc`）。簽署後 `.changes` 開頭會有 PGP signature block，證明這組檔案是你發布的。

## dput：上傳到 repo

```bash
# dput 把簽署過的 .changes 和它列出的所有檔案上傳
dput <target> greet_1.0-1_amd64.changes

# target 在 ~/.dput.cf 或 /etc/dput.cf 定義
# 例如上傳到 mentors.debian.net：
dput mentors greet_1.0-1_amd64.changes

# 上傳到自己的 repo（Ch 22/23）：
dput my-repo greet_1.0-1_amd64.changes
```

`~/.dput.cf` 範例：
```ini
[my-repo]
method = scp
fqdn = repo.example.com
incoming = /srv/incoming
login = upload
```

`dput` 讀 `.changes`，上傳裡面列的所有檔案（`.dsc`、tarballs、`.deb`）。接收端（如 reprepro 的 incoming 處理）驗證簽署後納入 repo。

## archive key 與信任鏈

使用者端，apt 怎麼驗證 repo？靠預裝的 archive keyring：

```bash
# Debian 的 archive key 在
ls /usr/share/keyrings/
# debian-archive-keyring.gpg   ← Debian 官方 key
# ubuntu-archive-keyring.gpg   ← Ubuntu 官方 key

# apt update 時驗證每個 repo 的 Release 簽署
sudo apt update
# Get:1 http://deb.debian.org/debian bookworm InRelease [...]
#   ↑ InRelease = 內嵌簽署的 Release 檔
# 如果簽署驗證失敗：
# W: GPG error: ... NO_PUBKEY ...
# E: The repository is not signed.
```

`InRelease` 是內嵌 GPG 簽署的 Release 檔（簽署和內容在同一檔案）。`Release` + `Release.gpg`（分離簽署）是舊形式。apt 驗證這個簽署來自信任的 key。

## 第三方 repo 的正確設定：Signed-By

加第三方 repo（如 Docker、Node.js 的官方 repo）時，安全的做法是用 `Signed-By` 限定該 repo 只能用特定 key：

```bash
# 1. 下載該 repo 的 key 到專屬位置（不是全域信任！）
curl -fsSL https://download.docker.com/linux/debian/gpg | \
    sudo gpg --dearmor -o /usr/share/keyrings/docker.gpg

# 2. 在 source 用 Signed-By 限定
echo "deb [signed-by=/usr/share/keyrings/docker.gpg] \
    https://download.docker.com/linux/debian bookworm stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list
```

或 deb822 格式：
```
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: bookworm
Components: stable
Signed-By: /usr/share/keyrings/docker.gpg
```

> **為什麼不用 `apt-key add`（已棄用）**：舊做法 `apt-key add` 把 key 加進**全域**信任 keyring——任何 repo 都能用這把 key 簽。如果某個第三方 key 洩漏，攻擊者能偽造**任何** repo。`Signed-By` 限定「這把 key 只能驗證這個 repo」，把信任範圍縮到最小。這是重要的安全演進，務必用 `Signed-By`。

## 故意弄壞：簽署 email 不匹配

```bash
# changelog/control 的 Maintainer 是 you@example.com
# 但你的 GPG key 是 other@example.com

debsign greet_1.0-1_amd64.changes
# gpg: skipped "you@example.com": No secret key
# debsign: gpg error occurred!  Aborting....
```

`debsign` 從 `.changes` 的 `Maintainer`（或 `Changed-By`）欄位找對應的 GPG key。email 對不上就找不到 key。修正：用一致的身份，或 `debsign -k <keyid>` 明確指定 key。

## 踩雷集錦

1. **以為 `.deb` 本身被簽署**：`.deb` 不帶個別簽署。完整性靠「簽署的 Release → checksum 鏈 → .deb」。理解這個才懂 repo 安全

2. **用 `apt-key add`（已棄用且危險）**：全域信任任何 key 是安全漏洞。一律用 `Signed-By` 限定 key 到特定 repo

3. **簽署 email 和 Maintainer 不一致**：debsign 找不到 key。保持 DEBEMAIL、Maintainer、GPG key 的 email 三者一致

4. **key 沒備份**：GPG private key 遺失 = 無法再以該身份簽署，已建立的信任作廢。備份 private key（`gpg --export-secret-keys`）到安全的離線位置

5. **expired key 還在用**：GPG key 可設過期。過期的 key 簽署會被拒。定期檢查 `gpg --list-keys` 的有效期，到期前 `gpg --edit-key` 延長

6. **把 private key 放進 CI 不加保護**：CI 簽署（Ch 32）需要 private key，但直接放進 repo 或明文環境變數會洩漏。用 CI 的 secret 機制，且最好用專用的 CI 簽署 key（權限隔離）

## 進階：debsig 與 dpkg 層的簽署驗證

Debian 主流的信任模型是 repo 層（簽 Release）。但 dpkg 其實也支援**個別 .deb 的簽署驗證**（`debsig-verify`），只是預設不啟用：

```bash
# dpkg 的 debsig 機制（少用，特殊場景）
# 需要 debsig-verify + 設定 policy
# 用於：離線分發單一 .deb 且要驗證來源的場景
```

為什麼主流不用 debsig？因為 repo 層簽署已經夠了——使用者透過 apt 從簽署的 repo 安裝，信任鏈完整。debsig 只在「脫離 repo 直接分發 .deb 且要驗證」的特殊場景有意義（如企業內部直接散布 .deb）。

新興的還有 **reproducible builds + 多方驗證**：與其信任單一簽署者，不如多方獨立重現 build 並比對結果（Ch 4）。這是「信任最小化」的方向——不是「相信簽署者沒作惡」，而是「任何人都能驗證 binary 來自公開 source」。

## 動手練習

1. 建立一把測試用 GPG key（用和你 DEBEMAIL 一致的 email），用它 `debsign` 練習 B 的 `.changes`。看簽署後 `.changes` 開頭多了 PGP block

2. 檢視信任鏈：`sudo apt update` 時加 `-o Debug::Acquire::gpgv=true` 看 GPG 驗證過程。看 `/usr/share/keyrings/` 的官方 key

3. 正確加一個第三方 repo（如 Docker 或任何用 `Signed-By` 的），對比用 `Signed-By` 和（不要真的做）`apt-key add` 的信任範圍差別

4. 故意製造 email 不匹配：用一把 email 不同的 key 試 `debsign`，看它找不到 key 的錯誤，再用 `-k` 指定修復

## 本章重點整理

- 簽署不在 `.deb` 層，而在「清單」層：維護者簽 `.dsc`/`.changes`，archive 簽 `Release`
- 信任鏈：信任 archive key → 驗證 Release 簽署 → checksum 鏈 → 驗證每個 `.deb`
- `debsign` 簽 `.changes`（email 要對應 GPG key）；`dput` 上傳 `.changes` 列出的所有檔案
- 第三方 repo 用 `Signed-By` 限定 key 到特定 repo（取代危險的全域 `apt-key add`）
- 簽署 email、Maintainer、GPG key 三者必須一致

## 自我檢核

- [ ] 能畫出從「信任 archive key」到「驗證一個 .deb」的完整信任鏈
- [ ] 知道維護者簽什麼（.dsc/.changes）、archive 簽什麼（Release），為什麼不簽 .deb
- [ ] 能解釋 `Signed-By` 為什麼比 `apt-key add` 安全
- [ ] 知道 `debsign` 怎麼找到要用的 key（從 Maintainer email）
- [ ] 能說出 reproducible builds 如何把信任從「相信簽署者」轉向「人人可驗證」

## 延伸閱讀

### 官方文件

- **[SecureApt (Debian Wiki)](https://wiki.debian.org/SecureApt)**
  - **讀哪裡**：整頁，信任鏈的完整說明（Release/InRelease/keyring）
  - **學什麼**：apt 安全機制的權威解釋；本章是教學版
  - **前提**：讀完本章

- **[Debian Repository HOWTO: signing](https://wiki.debian.org/DebianRepository/UseThirdParty)**
  - **讀哪裡**：`Signed-By` 的正確設定
  - **學什麼**：第三方 repo 的安全設定，為什麼棄用 apt-key
  - **前提**：本章的 Signed-By 部分

### 部落格 / 文章

- **[Why apt-key is deprecated](https://blog.cloudflare.com/.well-known/...)** 或 Debian 官方的 apt-key deprecation 說明
  - **這篇說什麼**：apt-key 的安全問題（全域信任）、Signed-By 如何解決
  - **讀哪裡**：deprecation rationale
  - **為什麼值得讀**：理解這個安全演進的動機，避免重蹈覆轍

→ [Ch 21 APT repository 結構](./21-apt-repo-structure.md)
