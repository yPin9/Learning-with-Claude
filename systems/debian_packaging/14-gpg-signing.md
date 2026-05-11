# Ch 14 — GPG 簽章與信任鏈

> 目標：理解 APT 的 GPG 信任鏈，知道金鑰存在哪裡、怎麼管理，以及為什麼現代做法棄用 apt-key。

## 為什麼需要 GPG 簽章

沒有簽章，任何人都可以架一個假的 repo，讓你的 apt 下載惡意套件：

```
攻擊者 → DNS 投毒 / 路由劫持 → 假 repo
apt update → 從假 repo 拿 Packages.gz → 裝惡意套件
```

GPG 簽章確保：「這個 repo 的 Packages.gz 是原始作者簽署的，沒有被篡改。」

## APT 信任鏈的完整流程

```
apt update
    ↓
1. 下載 InRelease（含 GPG 簽章）
    ↓
2. 用本地 keyring 驗證 GPG 簽章
   └── /usr/share/keyrings/ubuntu-archive-keyring.gpg  ← Ubuntu 官方金鑰
   └── /etc/apt/trusted.gpg.d/*.gpg                   ← 第三方金鑰（舊位置）
    ↓
3. 簽章驗證通過 → 讀取 InRelease 裡的 SHA256 列表
    ↓
4. 下載 Packages.gz
    ↓
5. 驗證 Packages.gz 的 SHA256 和 InRelease 一致
    ↓
6. 信任 Packages.gz 的內容（套件版本、下載 URL、套件 SHA256）
    ↓
7. 安裝時驗證下載的 .deb SHA256 和 Packages.gz 記錄的一致
```

每一層都被前一層保護，信任鏈不可偽造。

## 金鑰存放位置

### 舊方式（已棄用）

```bash
# 舊方式：加到全域信任 keyring
sudo apt-key add key.gpg          # ← 不要用這個
# 金鑰存在 /etc/apt/trusted.gpg 或 /etc/apt/trusted.gpg.d/
```

**問題**：全域信任 = 任何一個 repo 的金鑰都能簽署任何 repo 的套件。

### 新方式（推薦）

```bash
# 新方式：金鑰放在獨立檔案，repo 設定指定用哪個金鑰
curl -fsSL https://example.com/repo.gpg \
    | sudo gpg --dearmor -o /usr/share/keyrings/example-keyring.gpg

# sources.list.d/ 裡用 signed-by= 綁定
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/example-keyring.gpg] \
     https://example.com/debian stable main" \
     | sudo tee /etc/apt/sources.list.d/example.list
```

`signed-by=/usr/share/keyrings/example-keyring.gpg` 讓這個金鑰**只能驗證這個 repo**，不影響其他 repo。

### 金鑰位置慣例

| 位置 | 用途 |
|-----|-----|
| `/usr/share/keyrings/` | 推薦位置（套件管理，apt install 安裝的金鑰） |
| `/etc/apt/trusted.gpg.d/` | 管理員手動加的金鑰（舊 apt-key 格式） |
| `/etc/apt/keyrings/` | Ubuntu 22.04+ 的管理員金鑰位置 |

## 實際操作金鑰

```bash
# 查看目前信任的所有金鑰（舊方式）
sudo apt-key list

# 查看 /usr/share/keyrings/ 裡的金鑰
for f in /usr/share/keyrings/*.gpg; do
    echo "=== $f ==="
    gpg --no-default-keyring --keyring "$f" --list-keys
done

# 查看 Ubuntu 官方金鑰的指紋
gpg --no-default-keyring \
    --keyring /usr/share/keyrings/ubuntu-archive-keyring.gpg \
    --list-keys
```

## 格式：ASCII armor vs binary

GPG 金鑰有兩種格式：

```bash
# ASCII armor（.asc 副檔名，文字格式）
-----BEGIN PGP PUBLIC KEY BLOCK-----
mQINBFMURxQBEAC8...
-----END PGP PUBLIC KEY BLOCK-----

# binary（.gpg 副檔名）
（二進位，不可讀）

# 轉換：ASCII armor → binary（dearmor）
gpg --dearmor < key.asc > key.gpg

# 轉換：binary → ASCII armor（enarmor）
gpg --enarmor < key.gpg > key.asc
```

APT 接受兩種格式，但 `/usr/share/keyrings/` 慣用 `.gpg`（binary）。

## 產生自己的 GPG 金鑰（打包用）

Ch 23 架私有 repo 時需要：

```bash
# 產生金鑰對
gpg --batch --gen-key <<EOF
%no-protection
Key-Type: RSA
Key-Length: 4096
Subkey-Type: RSA
Subkey-Length: 4096
Name-Real: MyRepo Signing Key
Name-Email: repo@example.com
Expire-Date: 0
EOF

# 列出金鑰（記下 Key ID）
gpg --list-secret-keys --keyid-format LONG

# 匯出公鑰（給使用者下載）
gpg --export --armor repo@example.com > myrepo.asc
gpg --export repo@example.com > /usr/share/keyrings/myrepo.gpg
```

## trusted=yes 的風險

在測試 / 內部 repo 常用：

```
deb [trusted=yes] http://internal.repo.example.com/debian stable main
```

`trusted=yes` **跳過 GPG 驗證**——不需要金鑰，APT 直接信任這個 repo 的所有套件。

只在完全信任的內部網路環境使用，絕不用在公開系統上。

## 自我檢核

- [ ] APT 信任鏈：GPG 保護 InRelease → InRelease 的 SHA256 保護 Packages.gz → Packages.gz 的 SHA256 保護 .deb
- [ ] 新方式：金鑰存到 `/usr/share/keyrings/`，repo 設定用 `signed-by=` 綁定（金鑰只對該 repo 有效）
- [ ] 舊 `apt-key add` 已棄用：全域信任有安全風險
- [ ] ASCII armor（.asc）↔ binary（.gpg）用 `gpg --dearmor` / `--enarmor` 轉換
- [ ] `trusted=yes` 跳過驗證，只用在受信任的內網環境

→ [Ch 15 dpkg 資料庫](./15-dpkg-database.md)
