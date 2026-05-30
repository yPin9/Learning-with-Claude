# Ch 3 — apt：高層依賴解析

> **目標**：理解 apt 如何解析依賴關係、cache 與 sources.list 的結構、版本優先序（pinning/priority）、以及為什麼 apt 有時提議移除一堆套件——讓你能讀懂並控制 apt 的決策。

> **環境**：apt 2.6.x（Debian 12）。Ubuntu 22.04 用 apt 2.4.x，行為相同但 sources 格式預設改用 deb822。

## 為什麼依賴解析這麼難？

你說「裝 A」。A 依賴 B (≥2.0) 和 C。B 依賴 D。C 和 D 的某個版本衝突。系統上已經裝了 D 1.5，但 A 要的 B 需要 D 2.0。升級 D 到 2.0 又會破壞已經裝的 E……

這不是簡單的圖遍歷，而是一個約束滿足問題（constraint satisfaction）。形式上它和布林可滿足性（SAT）等價，最壞情況是 NP-complete。apt 用啟發式快速找一個「夠好」的解，但有時這個局部解很醜（提議移除你不想移除的東西）。

理解 apt 在解什麼問題，你才能在它給出爛建議時知道怎麼引導它。

## 先建立直覺：apt 是個約束求解器

```
   你的要求              已裝套件狀態         可用套件池
   ──────────            ────────────        ─────────
   install A             D 1.5 installed     repo 裡所有套件
   keep B                E 3.0 installed      的所有版本
        │                     │                    │
        └─────────────────────┼────────────────────┘
                              ▼
                  ┌────────────────────────┐
                  │   apt 依賴求解器         │
                  │  找一組套件版本，同時：   │
                  │  - 滿足所有 Depends      │
                  │  - 不違反任何 Conflicts  │
                  │  - 盡量少動已裝的東西     │
                  │  - 盡量裝新版            │
                  └────────────────────────┘
                              ▼
                  「裝 A B, 升 D 到 2.0, 移除 E」
                  （這就是它算出的解，你 [Y/n]）
```

關鍵：apt 在**所有版本的所有套件**這個巨大空間裡搜尋。所以「裝個小工具卻提議動 50 個套件」是有可能的——那個工具的某個依賴鏈觸發了連鎖反應。

## sources.list：套件從哪來

apt 從 `sources.list` 知道有哪些 repo 可用。

**傳統格式**（`/etc/apt/sources.list`）：

```
# deb <URL> <suite> <components...>
deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware
deb http://deb.debian.org/debian bookworm-updates main
deb http://security.debian.org/debian-security bookworm-security main

# deb-src 行讓你能 apt source（抓原始碼）
deb-src http://deb.debian.org/debian bookworm main
```

欄位拆解：
- `deb` / `deb-src`：二進位套件 / 原始碼套件
- URL：repo 的根
- suite：`bookworm`（穩定版代號）、`bookworm-updates`、`bookworm-backports`...
- components：`main`（純自由軟體）、`contrib`（自由但依賴非自由）、`non-free`、`non-free-firmware`

**新格式 deb822**（`/etc/apt/sources.list.d/*.sources`，Ubuntu 22.04 預設）：

```
Types: deb deb-src
URIs: http://deb.debian.org/debian
Suites: bookworm bookworm-updates
Components: main contrib non-free
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
```

deb822 格式更清楚、能多行、能明確指定簽署 key（`Signed-By`）。新增第三方 repo 時推薦用這個格式。

## apt 的 cache 結構

```bash
# apt 把 repo 的 metadata 下載到本地 cache
ls /var/lib/apt/lists/
# deb.debian.org_debian_dists_bookworm_main_binary-amd64_Packages
# 這些是 Packages 檔案（所有套件的 metadata）和 Release 檔案

# 下載的 .deb 檔案 cache 在
ls /var/cache/apt/archives/

# apt update 做什麼？
sudo apt update
# 1. 下載每個 repo 的 Release / InRelease（含簽署）
# 2. 驗證 GPG 簽署
# 3. 下載 Packages 檔案（所有套件 metadata）
# 4. 更新本地 cache
```

`apt update` 只更新 metadata cache，不裝任何東西。`apt upgrade` 才根據 cache 決定要升級什麼。這個分離很重要：cache 過期了你看到的可用版本就是舊的。

## 依賴關係的種類

`debian/control` 能宣告好幾種關係（Ch 7 詳談打包端，這裡看求解端怎麼用）：

| 欄位 | 語意 | apt 怎麼處理 |
|---|---|---|
| `Depends` | 硬依賴，必須裝 | 一定要滿足，否則裝不了 |
| `Pre-Depends` | 更強的依賴，必須**先**裝好 | 安裝順序強制在前 |
| `Recommends` | 強建議 | 預設一起裝（可關閉）|
| `Suggests` | 弱建議 | 不自動裝，只提示 |
| `Conflicts` | 不能共存 | 求解時排除組合 |
| `Breaks` | 會破壞對方（較弱的 Conflicts）| 強制版本約束 |
| `Provides` | 提供虛擬套件 | 多個套件可滿足同一個依賴 |
| `Replaces` | 取代對方的檔案 | 允許檔案覆蓋 |

虛擬套件（`Provides`）很重要：例如多個 MTA（postfix、exim4）都 `Provides: mail-transport-agent`。需要 MTA 的套件 `Depends: mail-transport-agent`，apt 知道裝任一個都行。

```bash
# 看一個套件的所有關係
apt show postfix | grep -E "Depends|Provides|Conflicts|Recommends"

# 看誰提供某個虛擬套件
apt-cache showpkg mail-transport-agent
```

## 版本優先序：apt pinning

當同一個套件有多個來源/版本（如 stable + backports），apt 用 **priority** 決定裝哪個。

```bash
# 看某套件的所有可用版本及其 priority
apt policy nginx
# nginx:
#   Installed: 1.22.1-9
#   Candidate: 1.22.1-9
#   Version table:
#  *** 1.22.1-9 500     ← 已裝，priority 500
#         500 http://deb.debian.org/debian bookworm/main amd64 Packages
#      1.24.0-1~bpo12+1 100  ← backports，priority 100（較低，不自動裝）
```

priority 預設值：
- 100：已裝的版本、NotAutomatic 來源（如 backports）
- 500：一般 repo
- 990：target release（`apt -t` 指定的）

你可以用 `/etc/apt/preferences.d/` 釘選（pin）特定版本：

```
# /etc/apt/preferences.d/nginx-backports
Package: nginx
Pin: release a=bookworm-backports
Pin-Priority: 600
```

> Pinning 是雙面刃。設錯了會讓系統處於混合狀態（部分 stable 部分 backports），引發難解的依賴衝突。非必要不要 pin；要 pin 就精確指定套件，別用萬用字元 pin 整個 release 拉高。

## 為什麼 apt 提議移除一堆東西？

這是新手最困惑的場景：

```bash
sudo apt install some-tool
# The following packages will be REMOVED:
#   gnome-core libfoo2 important-thing ...   ← 嚇人
# The following NEW packages will be installed:
#   some-tool
```

原因通常是：`some-tool` 依賴某個套件的新版本，而那個新版本和你已裝的東西衝突，apt 找到的「解」就是移除衝突方。這個解滿足了約束，但顯然不是你要的。

處理方式：

```bash
# 1. 先看清楚它要動什麼，不要無腦 Y
# 2. 用 --no-remove 禁止移除（apt 會改說無解，至少不會誤刪）
sudo apt install --no-remove some-tool

# 3. 用 aptitude（互動式 resolver，會提供多個解讓你選）
sudo aptitude install some-tool

# 4. 根源往往是 sources 混了不相容的 repo，或 pin 設錯
apt policy  # 檢查你的來源
```

## apt vs apt-get vs aptitude

| 工具 | 定位 |
|---|---|
| `apt` | 給人用的友善前端（進度條、顏色），日常用這個 |
| `apt-get` / `apt-cache` | 穩定的腳本介面，輸出格式不變，CI/script 用 |
| `aptitude` | 互動式 resolver，依賴衝突時會給多個解讓你選 |

> 寫 script 用 `apt-get`（介面穩定有保證），互動用 `apt`（友善），卡在依賴地獄用 `aptitude`（解得最聰明）。

## 踩雷集錦

1. **`apt update` 後沒 `apt upgrade` 以為更新了**：`update` 只更新 metadata cache，沒裝任何東西。要實際升級得 `apt upgrade`（或 `full-upgrade`）

2. **`apt upgrade` vs `apt full-upgrade`**：`upgrade` 不會移除任何套件（保守）；`full-upgrade`（舊名 `dist-upgrade`）允許移除以解決依賴。release 升級必須用 full-upgrade，但日常用 upgrade 較安全

3. **混用不相容的 repo**：把 testing 的 repo 加到 stable 系統，apt 可能拉一堆 testing 套件進來，破壞系統穩定性。要混用必須配合正確的 pinning，新手別碰

4. **無腦接受 autoremove 的建議**：`apt autoremove` 移除「不再被需要的自動安裝套件」。多半安全，但偶爾會誤判（尤其手動裝的東西被標成 auto）。看清單再確認

5. **第三方 repo 的 key 用 `apt-key add`（已廢棄）**：`apt-key` 在新版被棄用，因為它把 key 加進全域信任、任何 repo 都能用任何 key 簽。正確做法是把 key 放 `/usr/share/keyrings/`，在 source 用 `Signed-By:` 限定（Ch 20 詳談）

## 進階：apt 的求解器與 dose3

apt 內建的 resolver 是啟發式的，快但不保證最優。Debian 還有更嚴格的工具：

- **`apt-get --solver aspcud`**：用 SAT/ASP solver 找最優解（需裝 `apt-cudf`）
- **dose3**：Debian QA 用來分析「哪些套件在某個 release 裡根本不可安裝」（依賴鏈斷裂）。這是 archive 維護的重要工具

```bash
# 模擬安裝看 apt 的決策（不實際執行）
apt-get install --simulate some-package
apt-get install -s some-package   # 同上

# 看為什麼某套件不能裝
apt-get install some-package 2>&1 | grep -A20 "unmet dependencies"
```

如果你對「為什麼依賴解析這麼慢/這麼笨」好奇，根源在這是 NP-complete 問題，apt 為了速度犧牲了最優性。Mancoosi 研究計畫（EU 資助）專門研究這個，產出了上述工具。

## 動手練習

1. 跑 `apt policy`（不帶套件名），看你系統所有 repo 的 priority。再 `apt policy bash` 看單一套件的版本表

2. 找一個有虛擬套件的例子：`apt-cache showpkg awk`，看 `mawk`、`gawk` 怎麼都 provide `awk`。思考依賴 `awk` 的套件如何被滿足

3. 用 `apt-get install -s <某個大套件>`（simulate）看它會連帶裝/移什麼，但不實際執行。試 `apt-get install -s gnome` 看連鎖反應規模

4. 故意製造依賴困境（在 VM）：加一個 backports repo，嘗試從它裝一個和 stable 衝突的套件版本，觀察 apt 的建議，再用 `aptitude` 看它提供的多個解

## 本章重點整理

- apt 解的是約束滿足問題（NP-complete），用啟發式找「夠好」的解，不保證最優
- sources.list（傳統 / deb822）定義套件來源；`apt update` 只更新 metadata cache
- 依賴關係有 Depends/Recommends/Conflicts/Provides 等多種，Provides 實現虛擬套件
- priority/pinning 決定多版本時裝哪個；亂 pin 會造成依賴地獄
- 「提議移除一堆東西」是 resolver 找到的醜陋局部解，用 --no-remove 或 aptitude 應對

## 自我檢核

- [ ] 能解釋為什麼依賴解析本質上是個難題（NP-complete），而不只是圖遍歷
- [ ] 知道 `apt update` 和 `apt upgrade` 的差別，以及 `upgrade` vs `full-upgrade`
- [ ] 能說出虛擬套件（Provides）解決什麼問題，舉一個例子
- [ ] 看到 apt 提議移除一堆套件，知道至少兩種應對方式
- [ ] 知道為什麼 `apt-key add` 被棄用，新做法是什麼

## 延伸閱讀

### 官方文件

- **[sources.list(5) man page](https://manpages.debian.org/bookworm/apt/sources.list.5.html)**
  - **讀哪裡**：「DEB822-STYLE FORMAT」和「THE DEB AND DEB-SRC TYPES」
  - **學什麼**：兩種 sources 格式的完整語法，特別是 deb822 的所有欄位
  - **前提**：無

- **[apt_preferences(5) man page](https://manpages.debian.org/bookworm/apt/apt_preferences.5.html)**
  - **讀哪裡**：「How APT Interprets Priorities」和範例
  - **學什麼**：pinning 的完整規則；本章只講皮毛
  - **前提**：讀完本章的 priority 部分

### 部落格 / 文章

- **[A Journey to the Apt Solver](https://blog.jak-linux.org/)** — Julian Andres Klode（apt 維護者）
  - **這篇說什麼**：apt 維護者本人寫的 resolver 內部機制與改進歷史
  - **讀哪裡**：他 blog 裡關於 apt solver、EDSP 的文章
  - **為什麼值得讀**：來自 apt 核心開發者，講 resolver 的設計權衡，沒有比這更權威的

→ [Ch 4 .deb 檔案格式解剖](./04-deb-format.md)
