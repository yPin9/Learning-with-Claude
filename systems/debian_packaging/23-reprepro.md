# Ch 23 — reprepro 架設私有 apt repo

> 目標：用 reprepro 把自己打好的 .deb 發布成可以 `apt install` 的私有 repo，理解 Packages.gz / InRelease / GPG 簽章的完整流程。

## 為什麼要私有 repo

```
dpkg -i xxx.deb          → 手動安裝，沒有依賴解析，沒有更新通知
                                  ↓
本地 apt repo + sources.list  → apt install / apt upgrade 全支援
                                  ↓
reprepro 管理 → 自動維護 Packages.gz + InRelease + pool/ 結構
```

公司內部工具、CI 產物、自訂核心模組，都適合這條路。

## reprepro 安裝

```bash
sudo apt install reprepro gnupg
```

## Step 1：建立 GPG key（用來簽署 repo）

```bash
# 互動式生成 key（選 RSA 4096，不設有效期）
gpg --full-generate-key

# 查看生成的 key
gpg --list-secret-keys --keyid-format LONG

# 輸出類似：
# sec   rsa4096/AABBCCDD11223344 2025-05-11 [SC]
#       FINGERPRINT...
# uid   Your Name <you@example.com>

# 把 key ID 記下來（AABBCCDD11223344 這部分）
KEYID="AABBCCDD11223344"

# 匯出公鑰（給客戶端用）
gpg --export --armor $KEYID > repo-signing.gpg.pub
```

## Step 2：建立 reprepro 目錄結構

```bash
# repo 的根目錄（可以放在任何地方）
REPODIR="$HOME/myrepo"
mkdir -p $REPODIR/conf

# reprepro 的設定檔
cat > $REPODIR/conf/distributions << EOF
Origin: MyOrg
Label: MyOrg Internal
Codename: jammy
Architectures: amd64 arm64 source
Components: main
Description: Internal packages for MyOrg
SignWith: $KEYID
EOF

cat > $REPODIR/conf/options << EOF
verbose
basedir $REPODIR
EOF
```

### distributions 欄位說明

| 欄位 | 含義 | 對應 sources.list |
|------|------|-----------------|
| `Codename` | 發行版代號 | `deb <url> jammy main` 的 `jammy` |
| `Architectures` | 支援的架構 | 決定 binary-amd64/ 等目錄 |
| `Components` | 元件 | `main` / `contrib` / `non-free` |
| `SignWith` | 用哪個 GPG key 簽 | InRelease 的簽章 |

## Step 3：加入 .deb

```bash
cd $REPODIR

# 把打好的 .deb 加入 repo
reprepro includedeb jammy /path/to/sysinfo_1.0-1_amd64.deb

# 成功輸出：
# Exporting indices...
# Deleting files no longer referenced...
# Successfully created ...

# 查看 repo 內容
reprepro list jammy
# jammy|main|amd64: sysinfo 1.0-1
```

### 加入後自動產生的目錄結構

```
$REPODIR/
├── conf/
│   ├── distributions
│   └── options
├── db/                      ← reprepro 內部資料庫（不要手動改）
│   ├── checksums.db
│   └── packages.db
├── dists/
│   └── jammy/
│       ├── InRelease         ← GPG 簽章的 Release 檔
│       ├── Release
│       ├── Release.gpg
│       └── main/
│           └── binary-amd64/
│               ├── Packages
│               └── Packages.gz
└── pool/
    └── main/
        └── s/
            └── sysinfo/
                └── sysinfo_1.0-1_amd64.deb
```

## Step 4：讓 nginx 提供 HTTP 服務

```bash
sudo apt install nginx

# 建立 nginx 設定
sudo cat > /etc/nginx/sites-available/myrepo << 'EOF'
server {
    listen 80;
    server_name repo.myorg.internal;   # 或 IP

    root /home/user/myrepo;
    autoindex on;

    location / {
        try_files $uri $uri/ =404;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/myrepo \
           /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## Step 5：客戶端設定

```bash
# 在要使用 repo 的機器上

# 1. 匯入簽章 key（現代方法）
sudo mkdir -p /etc/apt/keyrings
curl -fsSL http://repo.myorg.internal/repo-signing.gpg.pub \
  | sudo gpg --dearmor -o /etc/apt/keyrings/myorg.gpg

# 2. 加入 sources.list
echo "deb [signed-by=/etc/apt/keyrings/myorg.gpg] \
  http://repo.myorg.internal jammy main" \
  | sudo tee /etc/apt/sources.list.d/myorg.list

# 3. 更新並安裝
sudo apt update
apt-cache policy sysinfo     # 確認 myorg repo 出現在候選清單
sudo apt install sysinfo
```

## reprepro 日常操作

```bash
cd $REPODIR

# 新增套件
reprepro includedeb jammy /path/to/newpkg_2.0-1_amd64.deb

# 移除套件
reprepro remove jammy sysinfo

# 列出所有套件
reprepro list jammy

# 強制重建索引（修 Packages.gz 和 InRelease）
reprepro export jammy

# 加入多個架構的 deb
reprepro includedeb jammy /path/to/sysinfo_1.0-1_amd64.deb
reprepro includedeb jammy /path/to/sysinfo_1.0-1_arm64.deb

# 升版（先 remove 舊版再 include 新版，或直接 include 新版）
# reprepro 不允許同一套件同版本重複加入
reprepro remove jammy sysinfo
reprepro includedeb jammy /path/to/sysinfo_2.0-1_amd64.deb
```

## 多個發行版

```
# conf/distributions（支援 jammy 和 focal）
Origin: MyOrg
Label: MyOrg Internal
Codename: jammy
Architectures: amd64
Components: main
SignWith: AABBCCDD11223344

Origin: MyOrg
Label: MyOrg Internal
Codename: focal
Architectures: amd64
Components: main
SignWith: AABBCCDD11223344
```

```bash
# 分別加入不同版本的套件
reprepro includedeb jammy /path/to/sysinfo_1.0-1_amd64~ubuntu22.04.deb
reprepro includedeb focal  /path/to/sysinfo_1.0-1_amd64~ubuntu20.04.deb
```

## 本地測試（不用 nginx）

```bash
# 用 file:// 協議測試
echo "deb [signed-by=/etc/apt/keyrings/myorg.gpg] \
  file://$HOME/myrepo jammy main" \
  | sudo tee /etc/apt/sources.list.d/myorg-local.list

sudo apt update
apt-cache show sysinfo   # 應該從本地 repo 找到
```

## 自動化腳本：CI 推 deb 到 repo

```bash
#!/bin/bash
# push-to-repo.sh：在 CI 跑完後呼叫
set -e

REPODIR="$HOME/myrepo"
CODENAME="jammy"
DEB_FILE="$1"   # 由 CI 傳入

if [ -z "$DEB_FILE" ] || [ ! -f "$DEB_FILE" ]; then
    echo "Usage: $0 <path-to.deb>"
    exit 1
fi

# 取得套件名稱（用來先移除舊版）
PKG_NAME=$(dpkg-deb -f "$DEB_FILE" Package)

cd "$REPODIR"
reprepro remove  "$CODENAME" "$PKG_NAME" 2>/dev/null || true
reprepro includedeb "$CODENAME" "$DEB_FILE"

echo "Deployed: $DEB_FILE → $CODENAME"
```

## 自我檢核

- [ ] reprepro 管理 `conf/distributions`；`Codename` 對應 sources.list 裡的 suite
- [ ] `reprepro includedeb <codename> <.deb>` 加套件；同版本不能重複加
- [ ] GPG key 的 `SignWith:` 讓 reprepro 自動簽 InRelease
- [ ] 客戶端需要：匯入公鑰 + 加 sources.list + `apt update`
- [ ] `file://` 協議可以在本機不用 nginx 測試

→ [Ch 24 aptly：現代 repo 管理工具](./24-aptly.md)
