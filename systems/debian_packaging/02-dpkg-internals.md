# Ch 2 — dpkg：底層套件管理員

> **目標**：理解 dpkg 的狀態資料庫結構、套件的安裝/設定/移除狀態機、檔案歸屬追蹤、conffile 機制，以及 dpkg 和 apt 的明確分工——讓你 debug 套件問題時知道去哪裡看。

> **環境**：dpkg 1.21.x（Debian 12 bookworm）。狀態資料庫路徑在所有近代 Debian/Ubuntu 一致。

## 為什麼需要先懂 dpkg？

大部分人只用 apt，從沒直接碰 dpkg。但 apt 底層就是呼叫 dpkg——apt 解出「要裝哪些套件」之後，把 `.deb` 檔案丟給 dpkg 去實際安裝。當套件管理出問題（半裝狀態、依賴循環、設定檔衝突），apt 常常束手無策，你必須降到 dpkg 層手動處理。

更重要的是：打包者必須理解 dpkg 怎麼安裝一個套件，才能寫出正確的 maintainer scripts（Ch 5）。不懂 dpkg 的狀態機，你的 postinst 會在錯誤的時機做錯誤的事。

## 先建立直覺：dpkg 是系統的帳本

```
        dpkg 的世界觀
        ┌─────────────────────────────────────────────┐
        │  /var/lib/dpkg/                              │
        │                                              │
        │  status          ← 主帳本：每個套件的狀態     │
        │  info/                                       │
        │    foo.list      ← foo 裝了哪些檔案           │
        │    foo.md5sums   ← 每個檔案的 checksum        │
        │    foo.conffiles ← 哪些是設定檔               │
        │    foo.postinst  ← 安裝後要跑的 script        │
        │    foo.prerm     ← 移除前要跑的 script        │
        │  ...                                         │
        └─────────────────────────────────────────────┘

dpkg 不上網、不解依賴、不下載。它只做一件事：
根據你給的 .deb 檔案，安全地修改系統狀態，並記帳。
```

dpkg **不關心**套件從哪來、依賴有沒有滿足的全域最優解。你叫它裝一個依賴沒滿足的套件，它會抱怨但可以被強迫。解依賴是 apt 的事。

## dpkg 的狀態資料庫

```bash
# 主狀態檔（純文字，可讀！）
sudo less /var/lib/dpkg/status

# 單一套件的 entry 長這樣：
# Package: hello
# Status: install ok installed
# Priority: optional
# Section: devel
# Installed-Size: 280
# Maintainer: Santiago Vila <sanvila@debian.org>
# Architecture: amd64
# Version: 2.10-3
# Depends: libc6 (>= 2.14)
# ...
```

`Status` 欄位是三段式，這是理解 dpkg 的關鍵：

```
Status: install ok installed
        ───┬─── ┬─ ────┬────
           │    │      └─ 套件狀態（Package status）
           │    └──────── 錯誤標記（Error flags）
           └───────────── 期望動作（Selection state）
```

| 欄位 | 可能值 | 意義 |
|---|---|---|
| Selection | `install` / `deinstall` / `purge` / `hold` | 你「希望」這個套件處於什麼狀態 |
| Error flag | `ok` / `reinstreq` | `ok` 正常；`reinstreq` 表示安裝壞了需要重裝 |
| Package status | `installed` / `config-files` / `unpacked` / `half-configured` / `half-installed` / `not-installed` | 套件「實際」的狀態 |

最常見的麻煩狀態是 `config-files`（套件移除了但設定檔還在，`dpkg -r` 而非 `dpkg --purge` 的結果）和 `half-configured`（postinst 跑到一半失敗）。

```bash
# 列出所有套件及其狀態（dpkg -l 的第一欄就是狀態縮寫）
dpkg -l | head
# ii  = installed
# rc  = removed but config-files remain
# iU  = unpacked but not configured（危險狀態）
# 第一個字母 = desired，第二個 = actual，第三個 = error
```

## 安裝一個套件的完整序列

當你 `dpkg -i foo.deb`，dpkg 走這個狀態機：

```
not-installed
    │  dpkg 解開 control.tar，讀 metadata
    ▼
  (執行 preinst install)        ← maintainer script #1
    │
    ▼
half-installed
    │  dpkg 解開 data.tar，把檔案寫進系統
    ▼
unpacked
    │
    ▼
  (執行 postinst configure)     ← maintainer script #2
    │
    ▼
half-configured
    │  postinst 成功
    ▼
installed  ✓
```

每一步的狀態都寫進 `/var/lib/dpkg/status`。如果 postinst 失敗，套件卡在 `half-configured`，下次 `dpkg --configure -a` 會嘗試重跑 postinst。

> 這就是為什麼 maintainer scripts 必須「可重入」（idempotent）——它可能被跑不只一次。Ch 5 深入。

## 檔案歸屬追蹤

dpkg 知道每個檔案屬於哪個套件，這是它最核心的價值。

```bash
# 這個檔案是哪個套件裝的？
dpkg -S /usr/bin/hello
# hello: /usr/bin/hello

dpkg -S /usr/bin/python3.11
# python3.11-minimal: /usr/bin/python3.11

# 這個套件裝了哪些檔案？
dpkg -L hello
# /usr/bin/hello
# /usr/share/doc/hello/...
# /usr/share/man/man1/hello.1.gz
# ...
```

這些資訊存在 `/var/lib/dpkg/info/<pkg>.list`（純文字檔案列表）。

**檔案衝突偵測**：如果你裝的套件想放一個已經屬於別的套件的檔案，dpkg 拒絕：

```bash
# 假設套件 A 和 B 都想裝 /usr/bin/foo
sudo dpkg -i package-b.deb
# dpkg: error processing archive package-b.deb (--install):
#  trying to overwrite '/usr/bin/foo', which is also in package 'package-a'
```

這個保護機制讓 5 萬個套件不會互相踩到對方的檔案。打包時如果兩個套件真的要共享檔案，必須用 `Replaces` + `Breaks` 明確宣告（Ch 7）。

## Conffile 機制：保護你改過的設定

這是 dpkg 最精巧的設計之一。設想：你裝了 nginx，改了 `/etc/nginx/nginx.conf`。現在 nginx 升級了，新版本帶了一個新的預設 `nginx.conf`。dpkg 該怎麼辦？無聲蓋掉你的修改？保留你的舊設定但可能不相容新版？

dpkg 的答案：**問你**。

```bash
# 升級時如果 conffile 被你改過、且新版本也改了它：
# Configuration file '/etc/nginx/nginx.conf'
#  ==> Modified (by you or by a script) since installation.
#  ==> Package distributor has shipped an updated version.
#    What would you like to do about it ?
#     Y or I  : install the package maintainer's version
#     N or O  : keep your currently-installed version
#     D       : show the differences between the versions
#     Z       : start a shell to examine the situation
```

dpkg 怎麼知道你改過？它在安裝時記錄了原始 conffile 的 md5sum（在 `/var/lib/dpkg/status` 的 `Conffiles:` 欄位）。升級時比對：

```
情況判斷（三方比較）：
  舊版預設 md5 = A
  現在磁碟上 md5 = B
  新版預設 md5 = C

  B == A（你沒改）          → 無聲用新版 C
  B != A 且 C == A（你改了，新版沒改）→ 保留你的 B
  B != A 且 C != A（你改了，新版也改了）→ 衝突！問你
```

```bash
# 看一個套件的 conffiles
cat /var/lib/dpkg/info/<pkg>.conffiles
# 或在 status 裡：
grep -A5 "Conffiles" /var/lib/dpkg/status | head
```

> 哪些檔案是 conffile，由**打包者**決定（透過 `debian/conffiles` 或讓檔案裝進 `/etc`，debhelper 自動標記）。打包者要小心：不該被使用者改的檔案不要標成 conffile，否則升級會莫名其妙地問問題。

## dpkg 和 apt 的明確分工

```
你: apt install nginx
        │
        ▼
┌──────────────────────────────────────┐
│  apt                                  │
│  1. 讀 nginx 的依賴                    │
│  2. 解出需要一起裝的套件清單            │
│  3. 從 repo 下載所有 .deb              │
│  4. 排出正確的安裝順序（依賴先裝）       │
└────────────┬─────────────────────────┘
             │ 把 .deb 一個個丟給 dpkg
             ▼
┌──────────────────────────────────────┐
│  dpkg                                  │
│  對每個 .deb：解開、跑 scripts、記帳    │
│  不上網、不解依賴、不下載               │
└──────────────────────────────────────┘
```

實用對照：

| 任務 | 用 apt | 用 dpkg |
|---|---|---|
| 裝套件（含依賴）| `apt install foo` | ✗（不解依賴）|
| 裝本地 .deb（含依賴）| `apt install ./foo.deb` | `dpkg -i foo.deb`（不解依賴）|
| 查檔案屬於哪個套件 | ✗ | `dpkg -S /path` |
| 查套件裝了哪些檔案 | ✗ | `dpkg -L foo` |
| 修復半裝狀態 | `apt -f install` | `dpkg --configure -a` |
| 看套件狀態 | `apt list --installed` | `dpkg -l` |

## 踩雷集錦

1. **「`dpkg -i` 裝套件就好，何必用 apt」**：`dpkg -i` 不解依賴。裝一個有未滿足依賴的套件，它會留在 `half-configured`，然後你要 `apt -f install` 來補。本地 `.deb` 用 `apt install ./foo.deb` 才會自動裝依賴

2. **`rc` 狀態的套件以為移除了**：`dpkg -r foo` 留下 `config-files` 狀態（`rc`），設定檔還在。要完全清除用 `dpkg --purge foo` 或 `apt purge foo`。`dpkg -l | grep '^rc'` 找出這些殘留

3. **conffile 升級時亂按 Y**：很多人升級看到 conffile 提示就無腦按 Y（用新版），結果自己的設定被蓋掉。先按 D 看 diff，搞清楚改了什麼再決定

4. **手動編輯 `/var/lib/dpkg/status`**：這個檔案是純文字，看起來可以改。**不要直接改**——格式錯一個字元 dpkg 就壞掉，整個套件系統癱瘓。要操作狀態用 `dpkg --set-selections` 等正規工具

5. **「half-installed 怎麼修」**：通常 `sudo dpkg --configure -a`（重跑所有未完成的 configure）或 `sudo apt -f install`（apt 修復）。如果是某個套件的 postinst 一直失敗，要看它的 script 為什麼失敗，不是反覆重試

## 進階：dpkg trigger 機制

有些操作不該每個套件各做一次，而是該在一批套件都裝完後做一次。例如：裝了 10 個有 man page 的套件，`mandb`（更新 man 索引）只需要在最後跑一次，不是每個套件跑一次。

dpkg 的 **trigger** 機制解決這個。套件可以「interest」某個 trigger（如 `/usr/share/man`），另一個套件的安裝「activate」這個 trigger，dpkg 把它累積起來，在這批操作的最後統一處理。

```bash
# 看哪些套件 interest 哪些 trigger
cat /var/lib/dpkg/triggers/File
# 看待處理的 trigger
sudo dpkg --triggers-only --pending  # 通常不用手動跑

# dpkg -l 裡 'iF' 狀態表示 trigger 待處理（triggers-pending）
dpkg -l | grep '^.F'
```

打包共享資源（man pages、icon cache、ldconfig、systemd units）時，debhelper 會自動處理 trigger，你通常不用手寫。但理解它存在，能解釋「為什麼裝完一堆套件最後它在 `Processing triggers for man-db...`」。

## 動手練習

1. 跑 `dpkg -l | awk '{print $1}' | sort | uniq -c`，統計你系統各種狀態的套件數量。有 `rc` 狀態的嗎？用 `dpkg --purge` 清掉一個試試

2. 挑一個有設定檔的套件（如 `openssh-server`），找出它的 conffiles：`cat /var/lib/dpkg/info/openssh-server.conffiles`。改一下其中一個檔案，然後 `sudo apt install --reinstall openssh-server`，觀察 conffile 提示

3. 跑 `dpkg -S $(which ls)` 找出 `ls` 屬於哪個套件，再 `dpkg -L` 那個套件看它還裝了什麼

4. 故意製造 half-configured：找一個套件，移除它依賴的某個東西讓 postinst 會失敗（小心選，別搞壞系統，用 VM）。觀察 `dpkg -l` 的狀態，再用 `dpkg --configure -a` 修復

## 本章重點整理

- dpkg 是狀態資料庫 + 安裝引擎，記錄每個檔案歸屬，但不解依賴、不上網
- `Status:` 三段式（selection / error / package-status）是診斷套件問題的關鍵
- 安裝走狀態機：not-installed → half-installed → unpacked → half-configured → installed
- conffile 機制用三方 md5 比較保護使用者改過的設定檔
- apt 解依賴+下載+排序，dpkg 實際安裝；出問題時降到 dpkg 層手動處理

## 自我檢核

- [ ] 能解釋 `Status: install ok installed` 三段各代表什麼
- [ ] 知道 `rc` 和 `ii` 狀態的差別，以及怎麼從 `rc` 完全清除
- [ ] 能用自己的話說明 conffile 的三方比較如何決定升級時的行為
- [ ] 知道為什麼 maintainer scripts 必須可重入（和 half-configured 狀態的關係）
- [ ] 面試問「apt 和 dpkg 差在哪」能講清楚分工

## 延伸閱讀

### 官方文件

- **[dpkg(1) man page](https://manpages.debian.org/bookworm/dpkg/dpkg.1.html)**
  - **讀哪裡**:「ACTIONS」和「PACKAGE STATES AND FLAGS」兩節
  - **學什麼**：所有狀態值的完整定義；本章只講了最常見的幾個
  - **前提**：無

- **[Debian Policy §6 (Package maintainer scripts and installation procedure)](https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html)**
  - **讀哪裡**：§6.1（introduction）和狀態機那張圖
  - **學什麼**：安裝/移除狀態機的權威定義，本章的圖就是它的簡化
  - **前提**：讀完本章再看會更有感

### 書籍

- **《The Debian Administrator's Handbook》§5.2 (dpkg's Database)** — Hertzog & Mas
  - **這本書的定位**：把 dpkg 資料庫講得比 man page 更有脈絡
  - **讀哪幾章**：§5.2（database）、§5.4（coexistence with other packaging systems）
  - **前提**：無

→ [Ch 3 apt：高層依賴解析](./03-apt-resolver.md)
