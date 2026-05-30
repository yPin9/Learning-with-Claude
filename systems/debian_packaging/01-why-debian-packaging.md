# Ch 1 — 為什麼學 Debian 打包？

> **目標**：理解套件管理解決的根本問題、`.deb` 在 Linux 軟體分發生態中的位置、打包者在軟體供應鏈裡的角色，以及為什麼「`make install` 灑檔案」是個壞主意。

## 為什麼需要套件管理？

回到沒有套件管理的年代。你想裝一個軟體，流程是：

```
下載 tarball → ./configure → make → sudo make install
```

`make install` 把檔案灑進系統各處：binary 進 `/usr/local/bin`、library 進 `/usr/local/lib`、設定檔進 `/etc`、man page 進 `/usr/share/man`。然後問題來了：

- **怎麼移除？** `make install` 沒有對應的 `make uninstall`（很多專案根本沒寫）。你裝了什麼、灑到哪、不知道。系統慢慢被孤兒檔案塞滿
- **怎麼升級？** 新版本的檔案佈局變了，舊檔案不會被清掉。`/usr/local/lib` 留著一堆過時的 `.so`
- **依賴怎麼辦？** 軟體 A 需要 library B 1.2 以上。誰來檢查？你自己手動。裝了 50 個軟體後，依賴關係變成一團亂麻（這就是惡名昭彰的 "dependency hell"）
- **檔案衝突？** 軟體 A 和 軟體 B 都想裝 `/usr/local/bin/foo`，誰贏？沒人管，後裝的蓋掉先裝的

套件管理系統就是來解決這些的。它的核心承諾是：

> 系統上每個檔案都屬於某個套件，每個套件都有明確的版本、依賴、和可逆的安裝/移除操作。系統的狀態是**可知、可控、可重現**的。

## 先建立直覺：套件管理是個資料庫 + 約束求解器

```
        ┌──────────────────────────────────────────┐
        │          套件管理系統的兩個核心             │
        │                                            │
        │  1. 狀態資料庫（dpkg 管）                   │
        │     「系統現在裝了哪些套件、哪個檔案屬於誰」  │
        │     /var/lib/dpkg/status                   │
        │     /var/lib/dpkg/info/*.list              │
        │                                            │
        │  2. 約束求解器（apt 管）                    │
        │     「要裝 A，A 依賴 B≥1.2，B 衝突 C...      │
        │      求一組可同時滿足的套件版本」            │
        └──────────────────────────────────────────┘
```

dpkg 是會計：它記帳，知道系統現在的精確狀態。apt 是規劃師：它解依賴的數獨，算出要裝/移/升哪些套件才能滿足你的要求。Ch 2 講 dpkg，Ch 3 講 apt。

## .deb 在生態系裡的位置

Linux 套件格式主要兩大家族：

```
.deb 家族（dpkg/apt）              .rpm 家族（rpm/dnf/zypper）
├── Debian                        ├── Fedora / RHEL / CentOS
├── Ubuntu (+ 衍生：Mint...)       ├── openSUSE
├── Raspberry Pi OS               └── Amazon Linux
└── 數百個 Debian 衍生版

其他模式：
├── Arch (pacman)        — 滾動更新
├── Nix / Guix          — 函數式、可重現
├── Flatpak / Snap      — 沙箱化、跨發行版
└── AppImage            — 單檔可執行
```

`.deb` 的影響力來自 Debian + Ubuntu 的巨大裝機量。Debian 是「通用作業系統」，有超過 5 萬個套件；Ubuntu 建立在 Debian 之上，是雲端與桌面的主流。學會 `.deb` 打包，你能服務這整個生態。

> **認識論誠實**：本課只教 `.deb`。`.rpm` 的概念（spec 檔、依賴、簽署）很類似，但工具和細節完全不同。我們不會說 `.deb` 比 `.rpm` 好——它們是不同社群的不同設計，各有取捨。Flatpak/Snap 解決的是另一個問題（跨發行版分發 + 沙箱），和 `.deb` 不是直接競爭。

## .deb 與 make install 的對比

| 面向 | `make install` | `.deb` 套件 |
|---|---|---|
| 安裝 | 灑檔案，無紀錄 | dpkg 記錄每個檔案歸屬 |
| 移除 | 多半做不到 | `dpkg -r` 精確移除 |
| 升級 | 手動，舊檔殘留 | dpkg 處理新舊檔案差異 |
| 依賴 | 自己檢查 | apt 自動解析安裝 |
| 衝突偵測 | 無 | dpkg 偵測檔案衝突並拒絕 |
| 設定檔保護 | 無 | conffile 機制保護你改過的設定 |
| 可重現性 | 低 | 同版本套件到處裝結果一致 |
| 簽署驗證 | 無 | GPG 簽署確保來源可信 |

最後一個被低估的點：**conffile 機制**。當你升級套件，dpkg 知道哪些是設定檔（conffiles），如果你改過它，升級時 dpkg 會問你要保留你的版本還是用新版——而不是無聲蓋掉。`make install` 直接蓋掉你的設定。這個機制 Ch 5 詳談。

## 打包者在軟體供應鏈的角色

```
upstream 開發者                你（打包者）              使用者
─────────────                ─────────────            ──────
寫程式，發布                   把 upstream 程式碼        apt install
tarball / git tag             包裝成 .deb：             一行裝好，
                              - 編譯設定                 自動處理依賴
                              - 拆分套件                 乾淨升級/移除
                              - 宣告依賴
                              - 寫 maintainer scripts
                              - 確保符合 Policy
```

打包者是 upstream 和使用者之間的橋樑。你做的事：

- **翻譯建置系統**：upstream 用 autotools / cmake / meson / cargo / setuptools，你要讓它們在 Debian 的標準流程裡跑
- **拆分套件**：一個 upstream 專案可能拆成 `foo`（執行檔）、`libfoo1`（runtime library）、`libfoo-dev`（開發檔）、`foo-doc`（文件）——讓使用者只裝需要的
- **宣告依賴**：精確列出 runtime 和 build 需要什麼，版本範圍多少
- **遵守 Policy**：檔案放哪、權限怎麼設、文件格式——Debian Policy 規定了一切，讓 5 萬個套件能和諧共存
- **處理升級/設定**：寫 maintainer scripts 處理安裝時的特殊邏輯（建使用者、重啟服務、遷移設定）

這份工作的價值在於：**使用者 `apt install foo` 的那一行背後，是你把所有複雜度吸收掉了。**

## 為什麼打包看起來「過度複雜」？

新手第一次看 `debian/` 目錄會嚇到：control、rules、changelog、copyright、patches、一堆 `*.install` 檔……「裝個軟體而已，需要這麼多東西嗎？」

需要，因為打包不是「在我的機器上能跑」，而是：

- 在**所有支援的架構**（amd64, arm64, i386, ...）上都能 build
- 在**乾淨的最小系統**上都能裝
- 能和**其他 5 萬個套件**共存不衝突
- 能被**自動化工具**（依賴解析、安全更新、archive 管理）處理
- 十年後**還能重現** build

這些約束才是複雜度的來源。Debian 的設計把這些複雜度標準化、工具化，讓你不必每次重新發明。一旦理解了「為什麼需要」，那些檔案就不再是無意義的儀式。

## 踩雷集錦

1. **「打包就是把檔案塞進壓縮檔」**：`.deb` 確實是個 archive（Ch 4 拆解），但打包的價值在 metadata（依賴、版本、scripts）和對 Policy 的遵守，不在壓縮本身

2. **「我用 checkinstall / fpm 自動生成 .deb 就好」**：這些工具能快速產出能裝的 `.deb`，但生成的套件通常違反一堆 Policy、依賴不準、無法被 Debian archive 接受。學習階段請理解手工流程；自動工具是理解之後的捷徑，不是替代理解

3. **「upstream 應該自己提供 .deb」**：很多 upstream 確實提供，但那些套件品質參差（常常是用 fpm 草草生成的）。Debian 官方套件由 Debian 維護者打包並對品質負責——這是兩種不同的東西

4. **「Snap/Flatpak 會取代 .deb」**：它們解決不同問題（沙箱、跨發行版）。系統核心元件（kernel、libc、systemd）永遠需要傳統套件管理。兩者長期共存

## 進階：套件管理的學術視角

依賴求解本質上是個 NP-complete 問題（布林可滿足性的變形）。apt 的 resolver 用啟發式；更嚴格的求解器（如 Debian 的 `dose3` 工具、或用 SAT solver 的 `aspcud`）能找到最優解或證明無解。如果你對「為什麼有時 apt 提議移除一堆東西」好奇，根源在這裡——它在解一個約束滿足問題，而局部最優可能很醜。

EDOS/Mancoosi 研究計畫專門研究這個，產出了 Debian 用來偵測「不可安裝套件」的工具。這是打包世界和形式方法的交會點。

## 動手練習

1. 在你的系統上跑 `dpkg -l | wc -l`，看你裝了幾個套件。再跑 `dpkg -l | head -20` 看格式

2. 挑一個你常用的套件（如 `curl`），跑 `apt show curl`，讀它的 Depends、Description、Homepage 欄位。再跑 `dpkg -L curl` 看它裝了哪些檔案

3. 跑 `apt-cache rdepends libc6`，看有多少套件依賴 libc6（會嚇到你）。思考：如果 libc6 升級破壞了 ABI，會發生什麼？

4. 找一個你裝過的、用 `make install` 裝的軟體（如果有），想想你現在能不能乾淨移除它

## 本章重點整理

- 套件管理解決四大問題：可逆移除、乾淨升級、依賴自動化、衝突偵測
- dpkg 是狀態資料庫（記帳），apt 是依賴求解器（規劃）
- `.deb` 的價值不在壓縮，在 metadata 和對 Policy 的遵守
- 打包者是 upstream 與使用者之間吸收複雜度的橋樑

## 自我檢核

- [ ] 不看筆記，能說出 `make install` 相比套件管理的三個具體缺陷
- [ ] 能解釋 dpkg 和 apt 的分工（一個記帳一個規劃）
- [ ] 如果面試問「為什麼 Debian 打包看起來這麼複雜」，能說出複雜度的真正來源
- [ ] 能說出 `.deb` 和 Snap/Flatpak 解決的是不是同一個問題

## 延伸閱讀

### 官方文件

- **[Debian Policy Manual §1 (Introduction)](https://www.debian.org/doc/debian-policy/ch-scope.html)**
  - **讀哪裡**：§1.1–1.3，講 Policy 存在的目的
  - **學什麼**：Debian 為什麼需要一份「憲法」來協調數千個維護者；理解 Policy 的權威來源
  - **前提**：無

### 部落格 / 文章

- **[How to Survive Debian's Dependency Hell](https://www.lucas-nussbaum.net/blog/)** 系列 — Lucas Nussbaum
  - **這篇說什麼**：用真實 archive 資料分析依賴問題的規模與處理
  - **讀哪裡**：搜尋他 blog 裡關於 archive QA、dependency 分析的文章
  - **為什麼值得讀**：讓你看到「管理 5 萬個套件的依賴」在工程上有多難，理解 Policy 與工具為何存在

### 書籍

- **《The Debian Administrator's Handbook》— Ch 5 (Packaging System)** — Hertzog & Mas（免費線上，[debian-handbook.info](https://debian-handbook.info/)）
  - **這本書的定位**：Debian 系統管理聖經；Ch 5 從使用者角度講套件系統，是本章的延伸
  - **讀哪幾章**：Ch 5（packaging system 概覽）；Ch 6（apt 使用）；打包進階在 Ch 15
  - **前提**：無，適合任何階段

→ [Ch 2 dpkg：底層套件管理員](./02-dpkg-internals.md)
