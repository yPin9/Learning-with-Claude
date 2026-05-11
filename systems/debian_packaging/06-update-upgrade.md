# Ch 6 — update / upgrade / dist-upgrade

> 目標：徹底搞清楚四個「升級」命令的差異，知道為什麼一定要 update 才能 upgrade，以及什麼情況下用 full-upgrade。

## 最常見的混淆點

初學者常問：「`apt update` 和 `apt upgrade` 有什麼不同？我直接 upgrade 就好了嗎？」

不行。兩個做的事完全不一樣：

```
apt update   ← 更新「套件列表」（不動任何已裝套件）
apt upgrade  ← 根據更新後的套件列表，升級已裝套件
```

如果只跑 `apt upgrade` 不先跑 `apt update`，apt 用的是本地的舊列表——你可能以為已經是最新，但其實還沒拿到最新的 metadata。

## apt update：做什麼

```bash
sudo apt update
```

APT 去 sources.list 裡的每個 repo 下載：
- `InRelease`（或 `Release` + `Release.gpg`）：版本資訊和 GPG 簽章
- `Packages.gz`：套件列表（名稱、版本、大小、依賴、下載 URL）

這些 metadata 存到 `/var/lib/apt/lists/`：

```bash
ls /var/lib/apt/lists/
# tw.archive.ubuntu.com_ubuntu_dists_jammy_main_binary-amd64_Packages
# tw.archive.ubuntu.com_ubuntu_dists_jammy_universe_binary-amd64_Packages
# ...
```

`apt update` 之後，apt 才知道「現在哪些套件有新版本」。

## apt upgrade：安全升級

```bash
sudo apt upgrade
```

升級所有可升級的套件，**但不會：**
- 安裝新套件（即使升級需要）
- 移除已裝套件（即使升級後不再需要）

這讓 `upgrade` 很保守——如果一個升級需要安裝或移除任何東西，那個套件就**跳過不升級**。

適合日常保持系統更新，不想有意外。

## apt full-upgrade（= apt-get dist-upgrade）

```bash
sudo apt full-upgrade
```

比 `upgrade` 更積極——為了完成升級，可以：
- 安裝新的依賴套件
- 移除衝突的舊套件

舊命令 `apt-get dist-upgrade` 和 `apt full-upgrade` 完全等價（`apt` 把它改名了）。

**什麼時候要用 `full-upgrade`？**

- 跨大版本升級（Ubuntu 22.04 → 24.04）
- kernel 更新（新 kernel 可能需要新 linux-headers）
- 依賴關係有大幅重整的更新

實際上，在 LTS 系統上日常用 `upgrade` 就夠，`full-upgrade` 主要在需要的時候才用。

## apt-get dist-upgrade vs do-release-upgrade

要從 Ubuntu 22.04 升到 24.04，**不要用** `apt full-upgrade`。要用：

```bash
sudo do-release-upgrade
```

`do-release-upgrade` 是 Ubuntu 專用工具，會做版本升級前的完整檢查（相容性、磁碟空間、第三方 repo 暫時停用等）。`apt full-upgrade` 只是在同一個版本內做更完整的套件升級。

## 完整流程對比

```
apt update          更新本地套件 metadata
    ↓
apt upgrade         升級，不動依賴結構（保守）
    ↓（如有套件被跳過）
apt full-upgrade    升級，允許安裝/移除套件解決依賴（積極）
```

## 自動安全更新：unattended-upgrades

Ubuntu 預設安裝了 `unattended-upgrades`，自動套用安全更新：

```bash
# 查看設定
cat /etc/apt/apt.conf.d/50unattended-upgrades

# 查看執行日誌
cat /var/log/unattended-upgrades/unattended-upgrades.log

# 手動觸發（測試用）
sudo unattended-upgrade --dry-run -d
```

預設只自動套用 `jammy-security` 的更新，不自動升級一般套件。這是伺服器上安全的做法。

## 各命令的行為摘要

| 命令 | 更新 metadata | 升級套件 | 可安裝新套件 | 可移除套件 |
|-----|:---:|:---:|:---:|:---:|
| `apt update` | ✓ | ✗ | ✗ | ✗ |
| `apt upgrade` | ✗ | ✓ | ✗ | ✗ |
| `apt full-upgrade` | ✗ | ✓ | ✓ | ✓ |
| `apt dist-upgrade`（apt-get）| ✗ | ✓ | ✓ | ✓ |

## 動手練習

```bash
# 1. 先看有哪些可升級的套件
apt list --upgradable

# 2. 模擬 upgrade 會做什麼
apt upgrade --dry-run

# 3. 實際執行
sudo apt update && sudo apt upgrade -y

# 4. 如果 upgrade 後還有套件被保留，看看 full-upgrade 有什麼不同
apt full-upgrade --dry-run
```

## 自我檢核

- [ ] `apt update` 只更新 metadata（套件列表），不動已裝套件
- [ ] `apt upgrade` 保守升級：不安裝新套件、不移除套件
- [ ] `apt full-upgrade`（= `apt-get dist-upgrade`）積極升級：可安裝/移除套件解決依賴
- [ ] Ubuntu 版本升級要用 `do-release-upgrade`，不是 `apt full-upgrade`
- [ ] `unattended-upgrades` 預設只自動套用安全更新

→ [Ch 7 版本鎖定與 pinning](./07-pinning.md)
