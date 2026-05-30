# Ch 24 — Ubuntu PPA 與 Launchpad

> **目標**：理解 PPA（Personal Package Archive）的運作——它是 Launchpad 提供的「上傳 source、雲端 build、自動發布」服務，和你自建 repo（reprepro/aptly）的差異、上傳流程、以及 Ubuntu 與 Debian 打包的細微區別。

> **環境**：Launchpad（launchpad.net）、Ubuntu。本章假設你已會打包和簽署（Ch 20）。

## 為什麼用 PPA？

你想把套件分發給 Ubuntu 使用者。自建 repo（Ch 22/23）要你自己準備 server、簽署、維護、為每個架構 build。PPA 把這些全包了：

```
PPA 的價值主張：
  你只上傳「source package」（.dsc + tarballs + .changes）
        │
  Launchpad 在雲端：
    - 為每個架構（amd64/arm64/...）自動 build
    - 自動簽署
    - 自動發布成 apt 可用的 repo
    - 提供穩定的 URL（ppa:user/name）
        │
  使用者：add-apt-repository ppa:user/name → apt install
```

你不碰 build farm、不管 server、不處理簽署細節。對「想讓 Ubuntu 使用者裝我的軟體」這個需求，PPA 是最省力的途徑。代價是：綁定 Launchpad/Ubuntu 生態，且你上傳的是 source（Launchpad 替你 build，所以不能上傳已 build 的 binary）。

## 先建立直覺：PPA 是「source-only 上傳 + 雲端 build」

```
自建 repo（reprepro/aptly）：
  你本機 build binary → 上傳 binary 到你的 server → 你的 repo

PPA：
  你本機只 build source（.dsc）→ 上傳 source 到 Launchpad
        │
  Launchpad 的 build farm 替你 build binary（每個架構）
        │
  Launchpad 發布成 ppa:user/name repo

關鍵差異：PPA 你上傳 source，Launchpad build；自建 repo 你 build binary
```

這個「source-only」模型和 Debian archive 一致（Ch 25）——維護者上傳 source，build farm 編譯。PPA 把這套基礎設施開放給個人。

## 設定 PPA

```
1. 註冊 Launchpad 帳號（launchpad.net）
2. 上傳你的 GPG public key 到 Launchpad
   （Launchpad 用它驗證你上傳的 .changes 簽署）
3. 上傳你的 SSH key（用於某些操作）
4. 在 Launchpad 網頁建立一個 PPA
   → 得到 ppa:yourname/ppaname
```

```bash
# 把 GPG key 上傳到 Ubuntu keyserver（Launchpad 從這裡抓）
gpg --keyserver keyserver.ubuntu.com --send-keys ABCD1234EF567890
# 然後在 Launchpad 網頁的 "OpenPGP keys" 確認/匯入
```

## 上傳到 PPA

PPA 只收 source package。流程：

```bash
# 1. 確保 changelog 的 distribution 是 Ubuntu suite（不是 unstable！）
dch -r    # 編輯，把 distribution 改成 jammy / noble 等 Ubuntu codename
# greet (1.0-1~ppa1) jammy; urgency=medium
#                    ─────
#                    Ubuntu suite codename

# 2. build source package（簽署！PPA 要驗證簽署）
dpkg-buildpackage -S -sa
#   -S : source only（PPA 自己 build binary）
#   -sa: 包含 orig tarball（首次上傳某版本要）
#   會用你的 GPG key 簽署 .dsc 和 .changes

# 3. 用 dput 上傳到 PPA
dput ppa:yourname/ppaname greet_1.0-1~ppa1_source.changes
#   注意是 _source.changes（source-only build 的 changes）
```

上傳後，Launchpad：
1. 驗證 `.changes` 的 GPG 簽署（對應你上傳的 key）
2. 接受 source 進 build queue
3. 為 PPA 設定的每個架構 build binary
4. build 成功後發布到 `ppa:yourname/ppaname`

你能在 Launchpad 網頁看 build 狀態、log、失敗原因。

## 使用 PPA

```bash
# 加入 PPA（add-apt-repository 自動處理 source 和 key）
sudo add-apt-repository ppa:yourname/ppaname
sudo apt update
sudo apt install greet

# add-apt-repository 做了：
# 1. 加 source 到 /etc/apt/sources.list.d/
# 2. 自動抓 PPA 的簽署 key（Launchpad 統一管理）
```

PPA 的便利：使用者一行 `add-apt-repository` 搞定 source 和 key——因為 Launchpad 統一管理簽署，key 處理是自動的（對比第三方 repo 要手動處理 Signed-By，Ch 20）。

## Ubuntu vs Debian 打包的差異

PPA 是 Ubuntu 生態，打包有些 Ubuntu 特定的細節：

| 面向 | Debian | Ubuntu / PPA |
|---|---|---|
| 版本後綴慣例 | `1.0-1` | `1.0-1~ppa1` 或 `1.0-1ubuntu1` |
| changelog distribution | `unstable` | Ubuntu codename（`jammy`/`noble`）|
| lintian profile | 預設 | `--profile ubuntu` |
| 上傳目標 | Debian archive / mentors | PPA / Ubuntu archive |
| build 環境 | Debian | Ubuntu（library 版本可能不同）|

**版本後綴的講究**：

```
1.0-1~ppa1
       ────
       ~ppa1 是「比 1.0-1 小」的版本（Ch 9 的 ~ 規則！）
       這樣當套件正式進 Ubuntu archive（變成 1.0-1）時，
       archive 版本 > 你的 PPA 版本，使用者能無痛升級到正式版

1.0-1ubuntu1
       ───────
       Ubuntu 對 Debian 套件做修改時的慣例
       表示「基於 Debian 的 1.0-1，Ubuntu 改了第 1 版」
```

> 用 `~ppa1` 後綴是好習慣：它讓 PPA 版本「小於」未來正式進 archive 的版本，避免使用者卡在 PPA 版本無法升級到官方版本。這直接應用了 Ch 9 的 `~` 比空字串小的規則。

## 多個 Ubuntu 版本：同一 source 多次上傳

PPA 通常要支援多個 Ubuntu release（jammy、noble...）。同一個 source 為每個 release 各上傳一次：

```bash
# 為 jammy 上傳
dch -r --distribution jammy --newversion 1.0-1~ppa1~jammy1
dpkg-buildpackage -S -sa
dput ppa:yourname/ppaname greet_1.0-1~ppa1~jammy1_source.changes

# 為 noble 上傳（不同版本後綴）
dch --newversion 1.0-1~ppa1~noble1 --distribution noble
dpkg-buildpackage -S
dput ppa:yourname/ppaname greet_1.0-1~ppa1~noble1_source.changes
```

`backportpackage`（ubuntu-dev-tools）能自動化「同 source 為多個 release 重新打包上傳」。

## 故意弄壞：上傳了 binary 或用了 Debian suite

```bash
# 錯誤一：上傳 binary changes（PPA 拒收 binary）
dpkg-buildpackage -b              # binary build
dput ppa:you/ppa greet_*_amd64.changes
# Launchpad 拒絕：PPA 只接受 source upload
#   "Source/binary (i.e. mixed) uploads are not allowed."

# 錯誤二：changelog 用了 Debian 的 unstable
# greet (1.0-1) unstable; urgency=medium
dput ppa:you/ppa greet_*_source.changes
# Launchpad 拒絕或 build 失敗：
#   unstable 不是 Ubuntu 的有效 suite
```

教訓：PPA 只收 **source-only** 上傳（`-S`，產生 `_source.changes`），且 changelog 的 distribution 必須是有效的 **Ubuntu codename**。這兩個是上傳 PPA 最常見的失敗。

## 踩雷集錦

1. **上傳 binary 而非 source**：PPA 只收 source（Launchpad 替你 build）。用 `dpkg-buildpackage -S` 產生 source-only changes

2. **changelog 用 Debian suite**：PPA 要 Ubuntu codename（jammy/noble），不是 unstable/sid。`dch --distribution jammy`

3. **沒上傳 GPG key 到 Launchpad**：Launchpad 驗證上傳簽署需要你的 public key。先送到 keyserver 並在 Launchpad 匯入

4. **版本後綴沒用 `~`**：用 `1.0-1ppa1`（沒 `~`）會「大於」`1.0-1`，使用者卡在 PPA 版本無法升級到官方版。用 `~ppa1`

5. **orig tarball 沒包含（-sa）**：首次上傳某個 upstream 版本要 `-sa`（包含 orig）。後續同版本的修訂可以不包含（Launchpad 已有）。漏了 `-sa` 首次上傳會缺 orig

6. **以為 PPA build 環境和你本機一樣**：PPA 在 Launchpad 的乾淨 Ubuntu 環境 build。你本機 build 成功不代表 PPA 成功（依賴版本可能不同）——這其實是好事（強迫依賴正確，類似 sbuild）

## 進階：PPA 的限制與 OBS 替代方案

PPA 雖方便，有其限制：

- **綁定 Ubuntu/Launchpad**：只服務 Ubuntu 系，不能發 Debian、Fedora
- **架構受限**：PPA 支援的架構是 Launchpad 提供的（amd64/arm64 等，但不是全部）
- **build 時間**：免費 PPA 的 build queue 可能要排隊

跨發行版分發的替代方案：

- **openSUSE Build Service (OBS)**：能同時為 Debian、Ubuntu、Fedora、openSUSE 等多個發行版 build 和發布。一份 source，多發行版產出。適合要同時服務 `.deb` 和 `.rpm` 生態
- **自建 aptly + CI**（Final Project）：完全掌控，不綁任何平台，但要自己維護基礎設施

> 選擇：只服務 Ubuntu 用 PPA（最省力）；要同時服務多發行版用 OBS；要完全掌控/企業內部用自建 aptly + CI。PPA 是「個人專案給 Ubuntu 使用者」的最佳選擇。

## 動手練習

1. （需要 Launchpad 帳號）註冊 Launchpad，上傳 GPG key，建一個 PPA。把練習 B 的 greet 改成 Ubuntu changelog（`dch --distribution jammy`），`dpkg-buildpackage -S -sa` 上傳

2. 觀察 Launchpad 的 build 過程：上傳後在 PPA 頁面看 build queue、build log、各架構的狀態

3. 用 `add-apt-repository ppa:你的/ppa` 加自己的 PPA，`apt install` 你上傳的套件（體會使用者端的便利）

4. 研究版本後綴：故意用 `1.0-1ppa1`（沒 `~`）和 `1.0-1~ppa1`，用 `dpkg --compare-versions` 比較它們和 `1.0-1` 的大小，理解為什麼要用 `~`

## 本章重點整理

- PPA 是 Launchpad 的服務：你上傳 source，它雲端 build（每架構）+ 簽署 + 發布成 apt repo
- source-only 上傳（`-S`）；changelog distribution 用 Ubuntu codename（不是 Debian unstable）
- 版本後綴用 `~ppa1`（比 `1.0-1` 小），讓使用者能無痛升級到未來的官方版本
- 使用者 `add-apt-repository ppa:user/name` 一行搞定（Launchpad 統一管理 key）
- vs 自建 repo：PPA 省力但綁 Ubuntu；OBS 跨發行版；自建 aptly 完全掌控

## 自我檢核

- [ ] 能解釋 PPA 和自建 repo 的核心差異（上傳 source 雲端 build vs 上傳 binary）
- [ ] 知道 PPA 上傳要用 `-S`（source-only）和 Ubuntu codename
- [ ] 能解釋為什麼版本後綴用 `~ppa1`（和 Ch 9 的 `~` 規則的關係）
- [ ] 知道 PPA、OBS、自建 aptly 各自適合什麼場景
- [ ] 知道為什麼「本機 build 成功不代表 PPA 成功」其實是好事

## 延伸閱讀

### 官方文件

- **[Launchpad PPA documentation](https://help.launchpad.net/Packaging/PPA)**
  - **讀哪裡**：creating、uploading 整個流程
  - **學什麼**：PPA 的完整官方指南，含 key 設定、上傳、多 release
  - **前提**：讀完本章

- **[Ubuntu Packaging Guide](https://canonical-ubuntu-packaging-guide.readthedocs-hosted.com/)**
  - **讀哪裡**：Ubuntu 和 Debian 打包差異那節
  - **學什麼**：Ubuntu 特定的打包慣例（版本、ubuntu1 後綴、Ubuntu lintian）
  - **前提**：本章

### 部落格 / 文章

- **[openSUSE Build Service for Debian packages](https://en.opensuse.org/openSUSE:Build_Service_for_Debian)**
  - **這篇說什麼**：用 OBS 跨發行版 build/發布 .deb 和 .rpm
  - **讀哪裡**：Debian/Ubuntu 那節
  - **為什麼值得讀**：理解 PPA 之外的跨發行版分發選擇

→ [Ch 25 Debian archive 的運作](./25-debian-archive.md)
