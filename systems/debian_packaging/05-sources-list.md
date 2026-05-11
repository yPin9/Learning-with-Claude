# Ch 5 — sources.list 解析

> 目標：完全讀懂 /etc/apt/sources.list 的每個欄位，理解 PPA 和第三方 repo 的機制，知道怎麼安全地加入外部 repo。

## sources.list 在哪裡

APT 的 repo 設定有兩個地方：

```
/etc/apt/sources.list          ← 主設定檔（系統預設）
/etc/apt/sources.list.d/       ← 附加設定目錄（每個第三方 repo 一個 .list 檔）
```

現代 Ubuntu（22.04+）偏好把第三方 repo 放在 `.list.d/` 目錄，而不是修改主 sources.list。

## sources.list 格式

```
deb http://tw.archive.ubuntu.com/ubuntu jammy main restricted universe multiverse
│   │                              │     │         └── Components（套件分類）
│   │                              │     └── Suite（發行版代號）
│   └── URI（repo 位置）            └── Suite
└── Type（deb = 二進位；deb-src = 源碼）
```

完整解析：

```
# /etc/apt/sources.list（Ubuntu 22.04 Jammy 的典型內容）

deb http://tw.archive.ubuntu.com/ubuntu jammy main restricted
deb http://tw.archive.ubuntu.com/ubuntu jammy-updates main restricted
deb http://tw.archive.ubuntu.com/ubuntu jammy universe
deb http://tw.archive.ubuntu.com/ubuntu jammy-updates universe
deb http://tw.archive.ubuntu.com/ubuntu jammy-backports main restricted universe multiverse
deb http://security.ubuntu.com/ubuntu jammy-security main restricted
deb http://security.ubuntu.com/ubuntu jammy-security universe
```

### Type：deb vs deb-src

- `deb`：二進位套件（你要裝的）
- `deb-src`：源碼套件（只有要 `apt source` 下載源碼時才需要）

一般使用只需要 `deb`。加 `deb-src` 會讓 `apt update` 多下載源碼索引，稍微慢一點。

### Suite：Ubuntu 的版本代號

| 代號 | Ubuntu 版本 |
|-----|-----------|
| `focal` | 20.04 LTS |
| `jammy` | 22.04 LTS |
| `noble` | 24.04 LTS |

Suite 的後綴：

| 後綴 | 意義 |
|-----|-----|
| （無後綴）`jammy` | 發行版原始套件 |
| `jammy-updates` | 發行後的修正更新 |
| `jammy-security` | 安全更新（優先推送） |
| `jammy-backports` | 從更新版 Ubuntu 向下移植的套件 |

### Components：套件分類

| Component | 意義 |
|-----------|-----|
| `main` | 官方支援、開源套件 |
| `restricted` | 官方支援、但有授權限制（如 GPU 驅動） |
| `universe` | 社群維護、開源 |
| `multiverse` | 不開源或有授權問題（如 MP3 解碼器） |

```bash
# 查看目前啟用的 sources
apt-cache policy | head -20

# 或直接看 sources.list
cat /etc/apt/sources.list
```

## 新格式：.sources（DEB822）

Ubuntu 24.04 開始推廣新格式（向下相容，舊格式仍有效）：

```
# /etc/apt/sources.list.d/ubuntu.sources（DEB822 格式）
Types: deb
URIs: http://tw.archive.ubuntu.com/ubuntu/
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
```

更容易讀，`apt-add-repository` 在新版 Ubuntu 上會生成這種格式。

## 加入 PPA

PPA（Personal Package Archive）是 Ubuntu 的特有機制，任何人可以在 Launchpad 上建立 PPA 發佈套件：

```bash
# 加入 PPA（例：最新版 git）
sudo add-apt-repository ppa:git-core/ppa
sudo apt update
sudo apt install git

# 查看 PPA 加了什麼
cat /etc/apt/sources.list.d/git-core-ubuntu-ppa-jammy.list
# deb https://ppa.launchpadcontent.net/git-core/ppa/ubuntu jammy main

# 移除 PPA
sudo add-apt-repository --remove ppa:git-core/ppa
```

`add-apt-repository` 同時做了：
1. 把 PPA URL 加到 `.list.d/`
2. 匯入 PPA 的 GPG 金鑰（到 `/etc/apt/trusted.gpg.d/` 或 `/usr/share/keyrings/`）

## 加入第三方 repo（正確方式）

以加入 Docker 官方 repo 為例（展示現代推薦做法）：

```bash
# 1. 匯入 GPG 金鑰（不再用 apt-key add，改用獨立 keyring）
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# 2. 加入 repo 設定，指定使用該 keyring 驗章
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
    https://download.docker.com/linux/ubuntu \
    $(lsb_release -cs) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list

# 3. 更新並安裝
sudo apt update
sudo apt install docker-ce
```

**為什麼不用 `apt-key add`？**

`apt-key add` 把金鑰加到全域信任清單，任何 repo 都可以簽署任何套件。`signed-by=` 指定金鑰只對這個 repo 有效，更安全。Ubuntu 22.04 以後已棄用 `apt-key`。

## 自我檢核

- [ ] `sources.list` 格式：`deb <URI> <Suite> <Components...>`
- [ ] `jammy-updates` = 發行後修正；`jammy-security` = 安全更新；`jammy-backports` = 向下移植
- [ ] `main` 官方開源；`universe` 社群開源；`multiverse` 非開源或受限授權
- [ ] 現代加入第三方 repo：金鑰存到 `/usr/share/keyrings/`，`signed-by=` 綁定，不用 `apt-key add`
- [ ] PPA 是 Ubuntu 特有的套件發佈機制，`add-apt-repository ppa:user/repo`

→ [Ch 6 update / upgrade / dist-upgrade](./06-update-upgrade.md)
