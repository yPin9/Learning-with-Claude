# Ch 22 — reprepro：靜態 repo 管理

> **目標**：用 reprepro 自動化 Ch 21 的手工 repo 管理——建立私有 repo、匯入/移除套件、管理多個 distribution、自動簽署，理解 reprepro 的「單一版本」模型及其適用場景。

> **環境**：reprepro 5.3.x（`apt install reprepro`）、GnuPG。本章承接 Ch 21 的 repo 結構知識。

## 為什麼用 reprepro？

Ch 21 你手工建了 repo——可行但痛苦：每次加套件要 `dpkg-scanpackages`、`apt-ftparchive release`、`gpg --clearsign`，還要手動管理 pool 的字母目錄、清理舊版本。

reprepro 把這些自動化。你給它一個 `.deb` 或 `.changes`，它自動放進 pool 的正確位置、更新 Packages、重新簽署 Release。它是中小型私有 repo 最常用的工具——簡單、可靠、純檔案系統（不需要資料庫或 server process）。

## 先建立直覺：reprepro 是 repo 的「收件員」

```
你：reprepro includedeb bookworm greet_1.0-1_amd64.deb
        │
  reprepro 自動：
    1. 把 .deb 放進 pool/main/g/greet/（正確的字母目錄）
    2. 更新 dists/bookworm/main/binary-amd64/Packages
    3. 重新計算並簽署 dists/bookworm/Release（InRelease）
    4. 維護它自己的資料庫（記錄哪個套件在哪個 suite）
        │
  → repo 立即可用，apt update 看得到新套件
```

reprepro 的核心限制（也是設計選擇）：**每個 suite 的每個套件只保留一個版本**。它不是「歸檔所有歷史版本」的工具，而是「維護當前狀態」的工具。要多版本/快照用 aptly（Ch 23）。

## 設定 reprepro

reprepro 用一個 `conf/` 目錄設定：

```bash
# 建立 repo 根目錄
mkdir -p ~/myrepo/conf
cd ~/myrepo
```

`conf/distributions`（定義有哪些 suite）：
```
Origin: MyRepo
Label: MyRepo
Codename: bookworm
Suite: stable
Architectures: amd64 arm64 source
Components: main
Description: My private APT repository
SignWith: ABCD1234EF567890
```

| 欄位 | 意義 |
|---|---|
| `Codename` | suite 代號（`bookworm`），sources.list 用這個 |
| `Architectures` | 支援的架構（含 `source` 如果要存 source）|
| `Components` | `main` / `contrib` / 自訂 |
| `SignWith` | 用哪把 GPG key 簽署（key ID 或 fingerprint）|

`conf/options`（全域選項，可選）：
```
verbose
basedir .
ask-passphrase
```

## 匯入套件

```bash
cd ~/myrepo

# 匯入單一 .deb
reprepro includedeb bookworm /path/to/greet_1.0-1_amd64.deb

# 匯入 .changes（連帶所有相關 .deb + source，推薦）
reprepro include bookworm /path/to/greet_1.0-1_amd64.changes
#   include 讀 .changes，把裡面所有檔案（.deb + .dsc + tarballs）一次納入

# 只匯入 source
reprepro includedsc bookworm /path/to/greet_1.0-1.dsc

# 匯入後 reprepro 自動更新 Packages 和簽署 Release
```

`reprepro include`（吃 `.changes`）是最完整的方式——它把一次 build 的所有產物（binary + source）一起納入，並驗證 `.changes` 的簽署（如果有）。

## 查詢與管理

```bash
# 列出某 suite 的所有套件
reprepro list bookworm
# bookworm|main|amd64: greet 1.0-1
# bookworm|main|amd64: libgreet1 1.0-1
# bookworm|main|source: greet 1.0-1

# 列出特定套件
reprepro list bookworm greet

# 移除套件
reprepro remove bookworm greet
#   移除 greet（及其在 pool 的檔案，如果沒有其他 suite 引用）

# 移除某 source 的所有 binary
reprepro removesrc bookworm greet

# 檢查 repo 一致性
reprepro check bookworm
reprepro checkpool       # 檢查 pool 檔案完整性
```

## 使用 reprepro 建的 repo

```bash
# reprepro 把 repo 建在 ~/myrepo/（含 dists/ 和 pool/）
ls ~/myrepo
# conf  db  dists  pool

# 透過 HTTP 提供（用任何 web server 指向 ~/myrepo）
# 例如簡單測試用 python：
cd ~/myrepo && python3 -m http.server 8000

# 客戶端加入
echo "deb [signed-by=/path/mykey.gpg] \
    http://localhost:8000 bookworm main" | \
    sudo tee /etc/apt/sources.list.d/myrepo.list
sudo apt update
sudo apt install greet
```

reprepro 產出的是**純靜態檔案**（`dists/` + `pool/`），任何 web server（nginx/apache/S3/GitHub Pages）都能 serve。不需要跑 reprepro 的 server——它只在「更新 repo 內容」時執行。

## incoming：自動處理上傳

配合 `dput`（Ch 20），reprepro 能自動處理上傳的 `.changes`：

`conf/incoming`：
```
Name: incoming
IncomingDir: incoming
TempDir: tmp
Allow: bookworm
```

```bash
# 處理 incoming 目錄裡的所有 .changes
reprepro processincoming incoming
#   掃描 incoming/，把合法簽署的 .changes 納入 repo，處理完移走
```

這讓 CI（Ch 32）能 `dput` 到 incoming，再觸發 `reprepro processincoming` 自動發布。

## reprepro 的「單一版本」模型

這是 reprepro 最重要的特性，也是它和 aptly 的關鍵差異：

```
reprepro：每個 suite 每個套件「只有一個版本」
  匯入 greet 1.0-1 → repo 有 1.0-1
  匯入 greet 1.0-2 → repo 變成 1.0-2（1.0-1 被「取代」消失）
  → 不保留歷史版本，永遠是「最新狀態」

適合：
  - 持續部署的私有 repo（永遠要最新）
  - 鏡像/分發當前 release
不適合：
  - 需要回滾到舊版本
  - 需要「凍結某個時間點的 repo 快照」（用 aptly）
```

> reprepro 的單一版本模型是**特性不是缺陷**——它讓 repo 簡單、可預測（永遠是當前狀態）。如果你的需求是「私有 repo 永遠提供最新版」，reprepro 完美。如果需要「快照、多版本、回滾」，那是 aptly 的領域（Ch 23）。

## 故意弄壞：SignWith 的 key 不存在

```bash
# conf/distributions 的 SignWith 指向一把你沒有的 key
reprepro includedeb bookworm greet_1.0-1_amd64.deb
# gpg: signing failed: No secret key
# ERROR: Could not finish exporting 'bookworm'!
# There have been errors!
```

reprepro 在更新 Release 時要用 `SignWith` 的 key 簽署。key 不存在（或 fingerprint 寫錯）就失敗。修正：`gpg --list-secret-keys` 確認 key 存在，把正確的 key ID/fingerprint 填進 `SignWith`。

## 踩雷集錦

1. **以為 reprepro 保留歷史版本**：匯入新版本會「取代」舊的，舊版本消失。需要歷史/快照用 aptly。reprepro 是「當前狀態」工具

2. **SignWith 的 key ID 寫錯或 key 不在**：每次更新 Release 都要簽署，key 有問題就全卡。先確認 key 存在

3. **直接改 pool/dists 而非透過 reprepro**：reprepro 維護自己的資料庫（`db/`）記錄狀態。手動改檔案會讓資料庫和實際內容不一致。一律透過 reprepro 指令操作

4. **架構不在 distributions 卻匯入**：`conf/distributions` 沒列 `arm64`，匯入 arm64 套件會被拒。Architectures 要涵蓋你要的架構

5. **忘記 `source` 架構卻匯入 .dsc**：要存 source package（.dsc），`Architectures` 要含 `source`。否則 includedsc 失敗

6. **多個 suite 共用 pool 的刪除陷阱**：套件在多個 suite 被引用時，`reprepro remove` 只從指定 suite 移除；pool 的實體檔在最後一個引用消失時才刪。理解這個避免「以為刪了但還在」

## 進階：reprepro 的 pull/update（鏡像上游）

reprepro 不只管理你自己的套件，還能**鏡像上游 repo** 的選定套件：

`conf/updates`：
```
Name: debian-bookworm
Method: http://deb.debian.org/debian
Suite: bookworm
Components: main
Architectures: amd64
VerifyRelease: <Debian archive key fingerprint>
FilterFormula: Priority (== required) | Priority (== important)
```

```bash
# 從上游拉取符合 FilterFormula 的套件進你的 repo
reprepro update
```

這讓你能建一個「精選鏡像」——只拉上游的特定套件（如只要 required/important 優先級的），用於離線環境或頻寬受限的部署。`FilterFormula` 用 Packages 欄位的條件篩選。

`conf/pulls` 則能在你自己的 suite 之間搬套件（如從 testing pull 到 stable）。這些進階功能讓 reprepro 能組織相當複雜的 repo 工作流，但核心仍是「單一版本、靜態檔案」。

## 動手練習

1. 用 reprepro 建一個 repo（照 Step 設定 `conf/distributions` 含 `SignWith`），匯入練習 B 的 `.changes`，`reprepro list` 確認，用 python http.server 提供，客戶端 `apt install`

2. 測試單一版本模型：匯入 greet 1.0-1，再匯入 1.0-2（改 changelog 重 build），`reprepro list` 確認只剩 1.0-2，pool 裡 1.0-1 的 .deb 消失了

3. 設定 incoming：建 `conf/incoming`，把一個 `.changes` 和相關檔案放進 incoming/，跑 `reprepro processincoming` 看它自動納入

4. 試 remove：`reprepro remove bookworm greet`，確認 repo 和 pool 都清掉了，apt update 後客戶端看不到

## 本章重點整理

- reprepro 自動化 repo 管理：放套件進 pool、更新 Packages、簽署 Release，全自動
- 設定在 `conf/distributions`（suite/架構/component/SignWith）；產出純靜態檔案，任何 web server 可 serve
- `reprepro include`（吃 .changes，最完整）/ `includedeb` / `includedsc` 匯入；`list`/`remove` 管理
- **單一版本模型**：每 suite 每套件只一個版本，新版取代舊版（不保留歷史）——適合「永遠最新」的私有 repo
- 進階：`update`（鏡像上游選定套件）、`processincoming`（自動處理上傳）

## 自我檢核

- [ ] 能用 reprepro 從零建一個簽署的私有 repo 並讓 apt 使用
- [ ] 理解 reprepro 的單一版本模型，知道它適合/不適合什麼場景
- [ ] 知道為什麼不能直接改 pool/dists（reprepro 有自己的資料庫）
- [ ] 能說出 `reprepro include`（.changes）比 `includedeb` 完整在哪
- [ ] 知道 reprepro 產出靜態檔案，部署時不需要跑 server process

## 延伸閱讀

### 官方文件

- **[reprepro(1) man page](https://manpages.debian.org/bookworm/reprepro/reprepro.1.html)**
  - **讀哪裡**：commands（include*/list/remove/update）和 `conf/distributions` 欄位
  - **學什麼**：所有指令和設定欄位；本章講了常用的
  - **前提**：讀完本章

### 部落格 / 文章

- **[Setting up a Debian repository with reprepro](https://wikitech.wikimedia.org/wiki/Reprepro)** 或 Debian Wiki 的 reprepro 頁
  - **這篇說什麼**：reprepro 的完整實戰設定，含 incoming、update、多 suite
  - **讀哪裡**：setup 和 daily workflow
  - **為什麼值得讀**：把本章的指令串成真實的 repo 維護工作流

→ [Ch 23 aptly：進階 repo 管理](./23-aptly.md)
