# Ch 7 — 版本鎖定與 pinning

> 目標：理解如何鎖定特定套件版本不讓它被升級，以及 APT pinning 如何控制多個 repo 來源的版本優先權。

## 為什麼要鎖定版本

- 生產環境：某個套件升級破壞了應用，要鎖住
- 依賴特定版本 API 的服務
- 嵌入式系統：版本升級需要重新驗證

## hold：最簡單的鎖定

```bash
# 鎖定套件，讓它不被 upgrade 更新
sudo apt-mark hold nginx

# 查看被鎖定的套件
apt-mark showhold

# 解除鎖定
sudo apt-mark unhold nginx
```

`hold` 狀態在 `dpkg -l` 中會顯示為 `h`（Desired = hold）：

```bash
$ dpkg -l nginx
Desired=Unknown/Install/Remove/Purge/Hold
||/ Name    Version    Architecture Description
+++-=======-==========-============-===========
hi  nginx   1.18.0-6   amd64        small, ...
```

第一個字母是 `h`（hold），第二個是 `i`（installed）。

`apt upgrade` 和 `apt full-upgrade` 都會跳過 hold 狀態的套件。

## dpkg hold（底層方式）

```bash
# 用 dpkg 直接設定 hold
echo "nginx hold" | sudo dpkg --set-selections
echo "nginx install" | sudo dpkg --set-selections   # 解除

# 查看所有 hold 套件
dpkg --get-selections | grep hold
```

這和 `apt-mark hold` 效果相同，但是底層 dpkg 格式。

## 安裝特定版本

```bash
# 先找可用版本
apt-cache policy nginx
apt list --all-versions nginx

# 安裝指定版本
sudo apt install nginx=1.18.0-6ubuntu14.4

# 安裝後立刻 hold，防止自動升級
sudo apt-mark hold nginx
```

## APT Pinning：更細緻的版本控制

`/etc/apt/preferences.d/` 裡的設定（俗稱 pinning）控制 APT 如何在多個 repo 來源中選版本。

### Priority 數值的意義

| Priority | 行為 |
|---------|-----|
| > 1000 | 強制安裝（即使是降版） |
| 990 | 目標發行版的套件 |
| 500 | 正常 repo 套件 |
| 100 | 已安裝但沒有對應 repo |
| < 0 | 永遠不自動安裝 |

預設值：一般 repo = 500，backports = 100，已安裝 = 100。

### 用 pinning 鎖定版本不升級

```
# /etc/apt/preferences.d/hold-nginx

Package: nginx
Pin: version 1.18.0-6ubuntu14.4
Pin-Priority: 1001
```

Priority > 1000 讓 APT 優先選這個特定版本，即使有更新版本也不自動升級。

### 用 pinning 選擇 backports 中特定套件的新版本

Ubuntu backports 預設 priority = 100（低於正常 500），不會自動選用。可以針對特定套件提高 backports 優先權：

```
# /etc/apt/preferences.d/prefer-backport-git

Package: git
Pin: release a=jammy-backports
Pin-Priority: 700
```

這讓 APT 對 `git` 優先選 backports 的版本，其他套件保持預設行為。

### 用 pinning 阻止某個 repo 的特定套件

```
# /etc/apt/preferences.d/block-ppa-python

Package: python3
Pin: release o=LP-PPA-deadsnakes
Pin-Priority: -1
```

Priority = -1 讓 APT 永遠不從 deadsnakes PPA 安裝 python3（即使那個 PPA 有更新版本）。

## 查看 pinning 效果

```bash
# 查看某套件的版本選擇邏輯
apt-cache policy nginx

# 模擬 upgrade 看哪些被鎖定
apt upgrade --dry-run
```

```
$ apt-cache policy nginx
nginx:
  Installed: 1.18.0-6ubuntu14.4
  Candidate: 1.18.0-6ubuntu14.4   ← 和 installed 相同表示被鎖定
  Version table:
 *** 1.18.0-6ubuntu14.4 1001       ← priority 1001（被 pin）
        1001 /var/lib/dpkg/status
     1.24.0-1~jammy 500
        500 http://nginx.org/packages/ubuntu jammy/nginx amd64 Packages
```

## 選擇哪個方法

| 場景 | 方法 |
|-----|-----|
| 臨時鎖住不讓升級 | `apt-mark hold` |
| 同時管理多個鎖定套件 | pinning（preferences.d/） |
| 多個 repo 的版本選擇控制 | pinning |
| 整套環境的精確版本控制 | 用 `apt-mark showhold` 匯出 + 腳本化 |

## 自我檢核

- [ ] `apt-mark hold <pkg>` 鎖定；`apt-mark unhold` 解除；`apt-mark showhold` 查看清單
- [ ] hold 後 `dpkg -l` 顯示 `hi`（hold + installed）
- [ ] Pinning Priority：> 1000 = 強制（可降版）；500 = 正常；< 0 = 禁止
- [ ] preferences.d/ 設定 `Pin: version` 鎖定特定版本；`Pin: release` 選特定 repo

→ [Ch 8 deb 套件格式](./08-deb-format.md)
