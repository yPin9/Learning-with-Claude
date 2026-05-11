# Ch 1 — 套件管理器的存在意義

> 目標：理解為什麼需要套件管理器，它解決了什麼問題，以及 Debian/Ubuntu 的套件系統在 Linux 生態中的位置。

## 沒有套件管理器的世界

假設你要在 Linux 上安裝 `curl`：

```
1. 去 curl 官網下載 source tarball
2. 解壓縮
3. 看 README，裝好 libssl-dev、zlib1g-dev 等依賴
4. ./configure --prefix=/usr/local
5. make -j$(nproc)
6. sudo make install
```

這叫 **從源碼安裝（build from source）**。問題：

- **依賴地獄（Dependency Hell）**：curl 需要 openssl，openssl 需要 zlib，zlib 需要...
- **沒有卸載機制**：`make install` 把檔案散在系統各處，要卸載只能手工一個個刪
- **版本衝突**：A 程式要 libfoo 1.2，B 程式要 libfoo 2.0，裝了哪個？
- **安全更新**：上游修了 CVE，你怎麼知道？怎麼批次更新？

套件管理器解決了這些問題。

## 套件管理器做什麼

```
你輸入：apt install curl

背後：
  1. 查詢 curl 的 metadata（版本、依賴、下載位置）
  2. 計算所有遞迴依賴
  3. 下載所有需要的 .deb 檔案
  4. 按正確順序安裝（先裝依賴再裝本體）
  5. 在資料庫記錄「curl 已安裝，版本 x.y.z」
  6. 知道哪些檔案是 curl 裝的 → 能完整卸載
```

## Linux 套件管理器的分類

不同 Linux 發行版用不同的套件格式和工具：

| 發行版 | 套件格式 | 底層工具 | 高層工具 |
|--------|---------|---------|---------|
| Debian / Ubuntu | `.deb` | `dpkg` | `apt` |
| Red Hat / CentOS / Fedora | `.rpm` | `rpm` | `dnf` / `yum` |
| Arch Linux | `.pkg.tar.zst` | `pacman` | `pacman` |
| Alpine | `.apk` | `apk` | `apk` |

這門課專注 **Debian/Ubuntu 的 `.deb` + `apt` 體系**，也是企業 Linux 中佔比最大的生態。

## Debian vs Ubuntu 的關係

```
Debian（上游）
  ├── Ubuntu（最大的 Debian 衍生版）
  │     ├── Ubuntu LTS（22.04、24.04）
  │     ├── Xubuntu / Kubuntu / Lubuntu（桌面環境變體）
  │     └── Linux Mint（基於 Ubuntu）
  ├── Raspbian / Raspberry Pi OS
  └── Kali Linux
```

Ubuntu 直接繼承了 Debian 的套件格式和工具。你學的 apt/dpkg 知識在兩者之間完全通用。

差異點：Ubuntu 有自己的 PPA（Personal Package Archive），允許個人或組織發佈非官方套件——這是 Debian 沒有的機制。

## dpkg 與 apt 的關係

```
apt（高層）
  ↓ 呼叫
dpkg（底層）
  ↓ 操作
.deb 檔案 → 安裝到系統
```

- `dpkg`：只管「把 .deb 安裝進去」或「把它解除安裝」，不處理依賴
- `apt`：在 dpkg 之上，加了「查詢 repo、解算依賴、下載、排序」的邏輯

類比：dpkg = 把書放進書架的人；apt = 幫你查圖書館目錄、確認你的書單齊全、然後叫書架的人去放。

## 這門課的學習路徑

```
Part 1（Ch 1–7）：你是使用者
  → 學會所有日常 apt/dpkg 操作，看懂 sources.list

Part 2（Ch 8–15）：你是架構師
  → 拆開 deb，看懂 repo 結構，理解依賴解算

Part 3（Ch 16–22）：你是打包者
  → 把自己的程式做成 deb，讓別人 apt install

Part 4（Ch 23–25）：你是 repo 管理者
  → 架自己的私有 apt repo，CI 自動推包
```

## 環境確認

後面章節所有命令都在 Ubuntu 22.04+ 上執行。確認你有：

```bash
# 確認 Ubuntu 版本
lsb_release -a

# 確認 apt 版本
apt --version

# 確認 dpkg 版本
dpkg --version
```

WSL2 安裝 Ubuntu 22.04：`wsl --install -d Ubuntu-22.04`

## 自我檢核

- [ ] 套件管理器解決：依賴計算、完整卸載、版本管理、安全更新四個問題
- [ ] `.deb` 格式屬於 Debian/Ubuntu 生態；`.rpm` 屬於 Red Hat 生態
- [ ] `dpkg` 是底層（只裝/解裝），`apt` 是高層（加上依賴解算和下載）
- [ ] Ubuntu 繼承自 Debian，apt/dpkg 知識完全通用

→ [Ch 2 apt 基本操作](./02-apt-basics.md)
