# Ch 2 — apt 基本操作

> 目標：掌握 apt 的所有日常命令，知道每個選項的意義，不再只會 `apt install`。

## 搜尋與查詢（不動系統，隨時跑）

```bash
# 搜尋套件名稱或描述
apt search curl
apt search "http client"

# 顯示套件詳細資訊（版本、依賴、大小、描述）
apt show curl

# 列出已安裝的套件
apt list --installed

# 列出可升級的套件
apt list --upgradable

# 列出某個套件的所有可用版本
apt list --all-versions curl
```

`apt search` 是模糊比對，搜尋的是套件名稱 **和** 描述文字。結果太多時加 `grep`：

```bash
apt search http | grep -i "^lib"
```

## 安裝

```bash
# 基本安裝
sudo apt install curl

# 安裝特定版本（從 apt list --all-versions 取得版本號）
sudo apt install curl=7.81.0-1ubuntu1.15

# 安裝多個套件
sudo apt install git vim wget

# 安裝但不安裝 Recommends（推薦套件）
sudo apt install --no-install-recommends curl

# 只下載不安裝（下載到 /var/cache/apt/archives/）
sudo apt install --download-only curl

# 模擬安裝（顯示會做什麼，但不真的做）
apt install --dry-run curl
# 或
apt install -s curl
```

**Recommends vs Depends**：
- `Depends`：一定要裝，否則功能壞掉
- `Recommends`：預設也會裝，但沒有也能跑（只是功能不完整）
- `Suggests`：只是建議，預設不裝

`--no-install-recommends` 在嵌入式或 Docker 環境常用，可以讓安裝的東西小很多。

## 移除

```bash
# 移除套件（保留設定檔）
sudo apt remove curl

# 移除套件 + 清除設定檔（purge）
sudo apt purge curl

# 移除不再被依賴的「孤兒套件」
sudo apt autoremove

# purge + autoremove 一起
sudo apt purge curl && sudo apt autoremove
```

`remove` vs `purge` 的差別：
```bash
# remove 後，設定檔還在
dpkg -l curl   # 狀態是 rc（r=removed, c=config-files remain）

# purge 後，連設定檔都清掉
dpkg -l curl   # 狀態是 un（fully uninstalled）
```

## 更新（下一章詳述，這裡先用）

```bash
# 更新套件列表（不升級任何東西）
sudo apt update

# 升級所有可升級套件
sudo apt upgrade
```

## 查詢已安裝套件的檔案

```bash
# 某個套件裝了哪些檔案
dpkg -L curl

# 某個檔案是哪個套件裝的
dpkg -S /usr/bin/curl
# 或用 apt-file（需要先 apt install apt-file && apt-file update）
apt-file search /usr/bin/curl
```

```bash
$ dpkg -L curl
/.
/usr
/usr/bin
/usr/bin/curl
/usr/share
/usr/share/doc
/usr/share/doc/curl
/usr/share/doc/curl/changelog.Debian.gz
/usr/share/man
/usr/share/man/man1
/usr/share/man/man1/curl.1.gz
```

## 清理快取

```bash
# 清除已下載的 .deb 快取（只清舊版本）
sudo apt autoclean

# 清除所有已下載的 .deb 快取
sudo apt clean
```

快取放在 `/var/cache/apt/archives/`。`clean` 釋放磁碟空間，但下次安裝同樣套件要重新下載。

## 完整 apt 命令一覽

| 命令 | 作用 |
|-----|-----|
| `apt search <pattern>` | 搜尋套件 |
| `apt show <pkg>` | 顯示套件資訊 |
| `apt list --installed` | 列出已安裝 |
| `apt install <pkg>` | 安裝 |
| `apt remove <pkg>` | 移除（保留設定） |
| `apt purge <pkg>` | 移除（含設定） |
| `apt autoremove` | 清除孤兒套件 |
| `apt update` | 更新套件列表 |
| `apt upgrade` | 升級套件 |
| `apt full-upgrade` | 升級（可能移除套件） |
| `apt autoclean` | 清舊版快取 |
| `apt clean` | 清全部快取 |

## 動手練習

```bash
# 1. 搜尋 jq（JSON processor）
apt search jq

# 2. 看 jq 的資訊
apt show jq

# 3. 模擬安裝（不真的裝）
apt install --dry-run jq

# 4. 實際安裝
sudo apt install jq

# 5. 看它裝了哪些檔案
dpkg -L jq

# 6. 用它解析 JSON
echo '{"name": "debian"}' | jq .name

# 7. 移除（先 remove，看設定檔狀態；再 purge）
sudo apt remove jq
dpkg -l jq   # 看 rc
sudo apt purge jq
dpkg -l jq   # 看 un
```

## 自我檢核

- [ ] `apt show` 顯示依賴關係；`apt list --installed` 列出已裝套件
- [ ] `--no-install-recommends` 跳過 Recommends，適合最小化環境
- [ ] `remove` 保留設定檔（rc 狀態）；`purge` 連設定一起清（un 狀態）
- [ ] `dpkg -L <pkg>` 看套件裝了哪些檔案；`dpkg -S <file>` 反查檔案所屬套件
- [ ] `/var/cache/apt/archives/` 是下載的 .deb 快取

→ [Ch 3 dpkg：apt 的底層工具](./03-dpkg.md)
