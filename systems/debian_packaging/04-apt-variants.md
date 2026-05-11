# Ch 4 — apt vs apt-get vs aptitude

> 目標：搞清楚三個工具的差異，知道什麼時候用哪個，不再因為 Stack Overflow 混用而困惑。

## 三個工具的歷史脈絡

```
時間線：

1998  apt-get 誕生：APT 的第一個命令列介面
2004  aptitude 出現：互動式 TUI + 更聰明的依賴解算
2014  apt 誕生：把 apt-get 和 apt-cache 合併成更簡潔的界面
```

**你現在應該用 `apt`**，除非有特定需求。`apt-get` 和 `apt-cache` 在腳本中仍常見（API 更穩定），`aptitude` 用在需要互動式操作的場景。

## apt vs apt-get / apt-cache

`apt` 是 apt-get + apt-cache 的前端，設計給人看的（有顏色、進度條、更短的命令）：

| `apt` | 等價的舊命令 |
|-------|------------|
| `apt install` | `apt-get install` |
| `apt remove` | `apt-get remove` |
| `apt purge` | `apt-get purge` |
| `apt update` | `apt-get update` |
| `apt upgrade` | `apt-get upgrade` |
| `apt full-upgrade` | `apt-get dist-upgrade` |
| `apt autoremove` | `apt-get autoremove` |
| `apt search` | `apt-cache search` |
| `apt show` | `apt-cache show` |
| `apt list` | 無直接對應（新功能） |

**在腳本裡用 apt-get 的理由**：`apt` 的輸出格式可能隨版本改變，`apt-get` 有 stable API 保證。CI/CD 腳本、Dockerfile 裡看到 `apt-get` 是正確做法：

```dockerfile
# Dockerfile：用 apt-get 而不是 apt
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/cache/apt/lists/*
```

## aptitude

`aptitude` 是獨立工具，不是 apt 的別名：

```bash
sudo apt install aptitude   # 先裝

# 互動式 TUI（按 q 退出）
sudo aptitude

# 命令列模式（語法和 apt 接近）
sudo aptitude install curl
sudo aptitude remove curl
sudo aptitude search curl
```

aptitude TUI：
```
┌─ Actions  Undo  Package  Search  Options  Views  Help ──────────────────────┐
│ i Installed Packages                                              [+]        │
│ u Upgradable Packages                                             [ ]        │
│ n New Packages                                                    [ ]        │
│ o Obsolete and Locally Created Packages                           [ ]        │
│ v Virtual Packages                                                [ ]        │
│ t Tasks                                                           [ ]        │
└──────────────────────────────────────────────────────────────────────────────┘
```

**aptitude 的特殊能力**：依賴衝突解算比 apt 更強。當 apt 遇到無法解決的依賴衝突時，aptitude 會提供多個解決方案讓你選：

```
aptitude install some-conflicting-package

aptitude提示：
  方案 1：降級 libfoo 到 1.2
  方案 2：移除 bar（依賴舊版 libfoo）
  方案 3：不安裝（放棄）
```

## apt-cache：純查詢工具

`apt-cache` 只做查詢，不動系統，不需要 sudo：

```bash
# 搜尋套件（和 apt search 一樣）
apt-cache search curl

# 顯示套件資訊
apt-cache show curl

# 顯示套件依賴（機器可讀格式）
apt-cache depends curl
apt-cache rdepends curl   # 反向依賴（誰依賴 curl）

# 顯示套件策略（各 repo 的版本和優先權）
apt-cache policy curl

# 顯示整個系統的套件統計
apt-cache stats
```

`apt-cache policy` 輸出非常有用：

```bash
$ apt-cache policy curl
curl:
  Installed: 7.81.0-1ubuntu1.15
  Candidate: 7.81.0-1ubuntu1.15
  Version table:
 *** 7.81.0-1ubuntu1.15 500
        500 http://tw.archive.ubuntu.com/ubuntu jammy-updates/main amd64 Packages
        100 /var/lib/dpkg/status
     7.81.0-1 500
        500 http://tw.archive.ubuntu.com/ubuntu jammy/main amd64 Packages
```

這告訴你：目前安裝的版本、最新的候選版本、每個版本來自哪個 repo、各自的優先權（500 = 正常 repo，100 = 已安裝但沒有對應 repo）。

## 決策流程

```
日常使用 → apt
Dockerfile / 腳本 → apt-get
需要解決複雜依賴衝突 → aptitude
純查詢（不改系統）→ apt-cache 或 apt（不需要 sudo）
```

## 自我檢核

- [ ] `apt` = apt-get + apt-cache 的前端，給人用的；腳本裡用 `apt-get`（API 更穩定）
- [ ] `aptitude` 獨立工具，有 TUI，依賴衝突解算能提供多個方案
- [ ] `apt-cache policy <pkg>` 看套件的各版本來源和優先權
- [ ] `apt-cache rdepends <pkg>` 反查「誰依賴這個套件」

→ [Ch 5 sources.list 解析](./05-sources-list.md)
