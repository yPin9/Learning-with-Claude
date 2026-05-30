# Ch 0 — 環境搭建

> **目標**：建立一個乾淨、可重現的 Debian 打包環境，安裝完整的工具鏈，理解為什麼打包要在隔離環境而不是你的日常系統做，並成功重建第一個現成套件驗證環境正確。

> **環境**：本課以 Debian 12 (bookworm) 為主，Ubuntu 22.04 LTS (jammy) 的差異會特別標注。工具版本：dpkg 1.21.x、debhelper 13、sbuild 0.85.x。在 macOS/Windows 上請用 VM 或容器，**不要**試圖在非 Debian 系系統上打包。

## 為什麼打包環境這麼講究？

一個常見的災難場景：你在自己每天用的 Ubuntu 桌面上打包一個套件，build 成功，裝到伺服器上卻爆炸——因為你的桌面裝了一堆開發套件，build 過程「不小心」連結到了某個只有你桌面才有的 library。你的 `.deb` 在你的機器上完美，到別人機器上缺一個沒宣告的依賴。

這就是為什麼打包有兩個鐵律：

1. **build 環境必須乾淨可重現**：理想上每次 build 都從一個只裝了宣告的 Build-Depends 的 minimal 系統開始（這就是 sbuild/pbuilder 做的事，Ch 15 詳談）
2. **打包工具不應污染日常系統**：你會裝一大堆 `dh-*`、`lib*-dev`，這些放在 VM 裡比較安全

這章我們先把「日常打包工具」裝好（在 VM 裡），clean build 環境留到 Ch 15。

## 先建立直覺：打包工具鏈的分層

```
你的編輯與打包操作
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  高層打包工具                                              │
│  debhelper (dh)  │  dpkg-buildpackage  │  debmake/dh_make │
│      ↓ 呼叫            ↓ 編排                ↓ 生成骨架      │
├─────────────────────────────────────────────────────────┤
│  核心 dpkg 工具                                            │
│  dpkg-deb  dpkg-gencontrol  dpkg-shlibdeps  dpkg-source   │
├─────────────────────────────────────────────────────────┤
│  底層套件管理                                              │
│  dpkg (安裝/移除)    │    apt (依賴解析/下載)               │
├─────────────────────────────────────────────────────────┤
│  品質保證 (build 之後跑)                                   │
│  lintian (靜態檢查)  │  autopkgtest (功能測試)             │
├─────────────────────────────────────────────────────────┤
│  隔離建置 (clean room)                                     │
│  sbuild / pbuilder  →  schroot / 容器                     │
└─────────────────────────────────────────────────────────┘
```

你不需要一次理解全部，但要知道「dh 在上、dpkg 在下、lintian 在旁邊、sbuild 圍起來」這個分層。

## Step 1：取得 Debian 環境

如果你已經在 Debian/Ubuntu，跳過。否則三選一：

```bash
# 選項 A：用 multipass 開 Ubuntu VM（最簡單，跨平台）
multipass launch 22.04 --name pkgdev --cpus 2 --memory 4G --disk 20G
multipass shell pkgdev

# 選項 B：用 Docker 容器（適合 CI，但 sbuild 在容器裡需要額外設定）
docker run -it --name pkgdev debian:bookworm bash

# 選項 C：直接裝 Debian 12 VM（VirtualBox/QEMU），這是最貼近真實的方式
```

> 為什麼推薦 VM 而非容器？容器預設沒有 systemd、user namespace 受限，sbuild 的 chroot 機制在容器裡會卡。學習階段用 VM，少踩坑。

## Step 2：安裝核心打包工具

```bash
sudo apt update
sudo apt install -y \
    build-essential \
    devscripts \
    debhelper \
    dh-make \
    dpkg-dev \
    fakeroot \
    lintian \
    quilt \
    gnupg \
    pristine-tar

# 確認版本
dpkg --version            # dpkg 1.21.x (bookworm)
dh --version              # debhelper 13.x
lintian --version         # Lintian v2.116.x
dpkg-buildpackage --version
```

各工具的角色：

| 套件 | 提供什麼 | 為什麼需要 |
|---|---|---|
| `build-essential` | gcc, make, libc-dev | 編譯任何東西的基礎 |
| `devscripts` | `debuild`, `dch`, `debsign`, `dget`... | 打包者的瑞士刀，幾十個小工具 |
| `debhelper` | `dh` 與所有 `dh_*` | 現代打包的核心自動化 |
| `dh-make` | `dh_make` | 從原始碼生成 `debian/` 骨架 |
| `dpkg-dev` | `dpkg-buildpackage`, `dpkg-source`... | dpkg 的開發工具集 |
| `fakeroot` | 假 root 環境 | build 時假裝是 root 設定檔案權限，不需要真 root |
| `lintian` | 靜態品質檢查 | 上傳前抓出 policy 違規 |
| `quilt` | patch 管理 | 管理對 upstream 原始碼的修改（Ch 11）|

## Step 3：設定打包者身份

dpkg 工具會把你的名字寫進 changelog 和簽署。先設好：

```bash
# 寫進 ~/.bashrc 或 ~/.profile
export DEBFULLNAME="Your Name"
export DEBEMAIL="you@example.com"

# 重新載入
source ~/.bashrc

# 驗證 dch 會用這個身份
echo $DEBFULLNAME $DEBEMAIL
```

這兩個環境變數會被 `dch`（編輯 changelog）、`dh_make`、`debsign` 等工具讀取。沒設的話工具會用 `whoami@hostname`，產出的套件看起來很業餘。

## Step 4：第一次重建現成套件

驗證環境最好的方式：抓一個現成套件的原始碼，原地重建它。我們用 `hello`——Debian 官方的「打包範例套件」。

```bash
# 1. 讓 apt 能抓 source package
# Debian: 編輯 /etc/apt/sources.list，確認有 deb-src 行
# Ubuntu: 用 deb822 格式或加 deb-src
sudo sed -i '/^deb /p; s/^deb /deb-src /' /etc/apt/sources.list 2>/dev/null || true
# 更穩的做法：手動確認 sources.list 裡有對應的 deb-src 行
sudo apt update

# 2. 安裝 hello 的 build 依賴
sudo apt build-dep hello

# 3. 抓原始碼（會下載 .dsc + .orig.tar + .debian.tar 並解包）
apt source hello
cd hello-*/

# 4. 看一眼 debian/ 目錄（這是整門課的核心）
ls debian/
# changelog  control  copyright  rules  ...

# 5. 重建套件
dpkg-buildpackage -us -uc -b
#  -us -uc : 不簽署（學習階段）
#  -b      : 只建 binary package（不打包 source）

# 6. 結果在上層目錄
cd ..
ls *.deb
# hello_2.10-3_amd64.deb （版本依 release 而定）
```

如果你看到 `hello_*.deb` 生成，恭喜，環境正確。

```bash
# 7. 裝起來試試
sudo dpkg -i hello_*.deb
hello
# Hello, world!

# 8. 移除
sudo dpkg -r hello
```

## Step 5：理解 `apt build-dep` 做了什麼（故意弄壞）

如果你跳過 Step 4 的 `apt build-dep hello`，直接 build 會看到：

```bash
dpkg-buildpackage -us -uc -b
# dpkg-checkbuilddeps: error: Unmet build dependencies: debhelper-compat (= 13)
# dpkg-buildpackage: warning: build dependencies/conflicts unsatisfied; aborting
```

`dpkg-buildpackage` 在開始前會呼叫 `dpkg-checkbuilddeps`，比對 `debian/control` 裡的 `Build-Depends` 和你系統已裝的套件。缺一個就拒絕開始。這是好事——它逼你宣告所有 build 依賴，否則你的套件在別人乾淨的機器上 build 不起來。

> 這是本課第一個「故意弄壞」的教訓：build 依賴沒裝齊，工具會擋你。把這個錯誤訊息記住，之後在 sbuild 裡你會反覆看到它。

## 踩雷集錦

1. **「在容器裡 sbuild 跑不起來」**：很多人以為 Docker 容器就能做所有打包。sbuild 需要建立 chroot（嵌套的隔離環境），預設容器的權限不足。學習階段用 VM；CI 階段用特殊設定的容器（Ch 32 會講）

2. **「我在 Arch/Fedora 上裝 dpkg 來打包」**：dpkg 可以裝在非 Debian 系系統，但 `apt build-dep`、`/usr/share/debhelper` 的 sequence 檔案、policy 工具全都缺，會處處碰壁。打包 Debian 套件就用 Debian 系系統

3. **`deb-src` 沒開就 `apt source` 失敗**：報錯 `Unable to find a source package`。Debian 12 的 `sources.list` 預設可能沒有 `deb-src` 行；Ubuntu 22.04 用新的 deb822 格式（`/etc/apt/sources.list.d/ubuntu.sources`），要加 `Types: deb deb-src`

4. **沒裝 `fakeroot` 導致權限錯誤**：build 過程要把檔案標記成 `root:root` 擁有，但你不是 root。`fakeroot` 攔截相關 syscall 假裝你是。`dpkg-buildpackage` 預設用 `fakeroot`，沒裝會報錯

5. **`DEBEMAIL` 沒設，changelog 出現奇怪 email**：產出的套件 changelog 會是 `you@yourhostname`，上傳到任何地方都不專業。一開始就設好

## 進階：用 schroot 預備 clean build（先看不做）

Ch 15 會深入，這裡先讓你知道方向。真正的 Debian 維護者不用 `dpkg-buildpackage` 直接 build，而是：

```bash
# 建立一個 bookworm 的 chroot tarball（之後 build 都從這個乾淨環境開始）
sudo sbuild-createchroot --include=eatmydata,ccache \
    bookworm /srv/chroot/bookworm-amd64 \
    http://deb.debian.org/debian

# 之後 build 就在這個 chroot 裡，host 系統完全不被污染
sbuild -d bookworm hello_2.10-3.dsc
```

這保證 build 環境只有宣告的依賴。先知道有這回事，Ch 15 再動手。

## 動手練習

1. 跑完 Step 4 的完整流程，確認你能重建 `hello` 並安裝執行。記下 `dpkg-buildpackage` 輸出裡你看不懂的行（之後章節會一一解釋）

2. 抓另一個簡單套件的原始碼：`apt source sl`（會在終端機跑火車的小程式），看它的 `debian/` 目錄和 `hello` 有什麼不同

3. 故意在沒裝 build 依賴的情況下 build 一個套件（例如先 `sudo apt remove debhelper` 再 build hello），讀 `dpkg-checkbuilddeps` 的錯誤訊息，然後裝回來

4. 執行 `dpkg -L hello`（先裝好 hello 套件），看這個套件實際裝了哪些檔案到系統哪些位置

## 本章重點整理

- 打包要在隔離、可重現的環境做；學習階段用 VM，生產階段用 sbuild chroot
- 核心工具鏈分層：dh（高層）→ dpkg-* 工具 → dpkg/apt（套件管理）→ lintian/autopkgtest（QA）
- `apt source <pkg>` 抓原始碼、`apt build-dep <pkg>` 裝建置依賴、`dpkg-buildpackage -us -uc -b` 重建
- `dpkg-checkbuilddeps` 在 build 前強制檢查 Build-Depends，這是 feature 不是 bug

## 自我檢核

- [ ] 能解釋為什麼打包要在乾淨環境做，而不是日常系統（不只是「比較好」，要說出污染的具體後果）
- [ ] 知道 `dh`、`dpkg-buildpackage`、`dpkg`、`apt`、`lintian` 各自在工具鏈的哪一層
- [ ] 能從零抓一個套件的 source 並重建成 `.deb`
- [ ] 知道 `apt source` 失敗時第一個要檢查的是什麼（deb-src）

## 延伸閱讀

### 官方文件

- **[Debian Wiki: Packaging/Intro](https://wiki.debian.org/Packaging/Intro)**
  - **讀哪裡**：整頁；它給的是和本章一樣的「環境與工具總覽」，可作為對照
  - **學什麼**：官方對打包工具鏈的分類，補充本章沒列到的次要工具
  - **前提**：無

- **[devscripts 套件說明](https://manpages.debian.org/bookworm/devscripts/devscripts.1.html)**
  - **讀哪裡**：man page 的工具列表那一節
  - **學什麼**：`devscripts` 裡幾十個工具各做什麼；現在看不懂沒關係，當索引用
  - **前提**：無

### 部落格 / 文章

- **[Debian Packaging Tutorial (Lucas Nussbaum)](https://www.debian.org/doc/manuals/packaging-tutorial/packaging-tutorial.en.pdf)** — Lucas Nussbaum（Debian 前 DPL）
  - **這篇說什麼**：一份廣為使用的打包教學投影片 PDF，從環境到上傳完整走一遍
  - **讀哪裡**：先看 1–20 頁（環境與概念），對應本章
  - **為什麼值得讀**：作者是前 DPL，內容權威；投影片形式好快速建立全圖

→ [Ch 1 為什麼學 Debian 打包？](./01-why-debian-packaging.md)
