# Ch 21 — APT repository 結構

> **目標**：徹底拆解 APT repository 的目錄結構——`dists/` 與 `pool/` 的分工、Release/Packages/Sources 檔案的角色與生成、以及 apt 如何從這個結構找到並下載套件。理解這個，你才能手工建一個 repo（或 debug repo 工具）。

> **環境**：以 Debian 的 repository 格式為準。本章手工剖析，Ch 22/23 用工具自動化。

## 為什麼要理解 repo 結構？

Ch 22/23 會用 reprepro/aptly 自動建 repo。但工具是黑盒子——當 repo 出問題（apt 找不到套件、簽署驗證失敗、metadata 不一致），你必須知道 repo 內部長什麼樣、apt 期待找到什麼。

而且，手工理解一次 repo 結構，你會發現它的設計非常優雅：`pool/`（實體檔案池）和 `dists/`（按 suite 組織的 metadata）的分離，解決了「同一個套件被多個 release 共用」「metadata 簽署」等問題。

## 先建立直覺：兩個目錄的分工

```
一個 APT repository 的頂層：

repo/
├── dists/              ← metadata 層（「目錄」，按 suite 組織）
│   └── bookworm/
│       ├── Release         ← 整個 suite 的清單（被簽署）
│       ├── InRelease       ← 內嵌簽署的 Release
│       ├── Release.gpg     ← 分離簽署
│       └── main/           ← component
│           ├── binary-amd64/
│           │   ├── Packages       ← 所有 amd64 .deb 的 metadata
│           │   └── Packages.gz
│           └── source/
│               └── Sources         ← 所有 source package 的 metadata
│
└── pool/               ← 實體檔案層（「倉庫」，按字母組織）
    └── main/
        └── g/greet/        ← 按套件名首字母分目錄
            ├── greet_1.0-1_amd64.deb
            ├── libgreet1_1.0-1_amd64.deb
            ├── greet_1.0-1.dsc
            └── greet_1.0.orig.tar.gz
```

`dists/` 是「目錄索引」——告訴 apt「這個 suite 有哪些套件、它們的 metadata、checksum」。`pool/` 是「實體倉庫」——存放真正的 `.deb` 和 source 檔案。

## 為什麼分 dists 和 pool？

舊式 repo（pool 之前）把 `.deb` 直接放在 suite 目錄下。問題：同一個套件如果同時在 stable 和 testing（版本相同），要存兩份。

pool 設計解決這個：

```
pool 的好處：

  greet_1.0-1_amd64.deb 只在 pool/ 存「一份」
        │
  bookworm 的 Packages 指向它
  trixie 的 Packages 也指向它（如果版本相同）
        │
  → 一個實體檔案，多個 suite 共用，節省空間

  pool 按套件名首字母分目錄（g/greet/），避免單一目錄塞幾萬個檔案
  （libxxx 特殊：用 lib + 第四個字母，如 libg/libgreet/）
```

## Packages 檔案：binary 套件的 metadata

`dists/bookworm/main/binary-amd64/Packages` 列出該 suite/component/架構的所有 `.deb` 的 metadata：

```
Package: greet
Version: 1.0-1
Architecture: amd64
Maintainer: Your Name <you@example.com>
Installed-Size: 25
Depends: libc6 (>= 2.34), libgreet1 (>= 1.0)
Filename: pool/main/g/greet/greet_1.0-1_amd64.deb   ← 指向 pool 的實體檔
Size: 5234
MD5sum: ...
SHA256: ...                                          ← 用來驗證下載的 .deb
Description: command-line greeting tool
 ...

Package: libgreet1
Version: 1.0-1
...
```

每個 stanza 是一個 `.deb` 的 control 資訊（Ch 4）+ `Filename`（在 pool 的位置）+ `Size`/checksum。apt 讀這個檔案就知道「有哪些套件、在哪下載、checksum 是什麼」。

`Packages` 檔由掃描 pool 裡的 `.deb` 生成（`dpkg-scanpackages` 或 reprepro/aptly 自動做）。

## Release 檔案：suite 的總清單（被簽署）

`dists/bookworm/Release` 是整個 suite 的頂層清單：

```
Origin: My Repository
Label: My Repository
Suite: bookworm
Codename: bookworm
Architectures: amd64 arm64 source
Components: main
Date: Thu, 29 May 2025 12:00:00 UTC
SHA256:
 abc123... 5234 main/binary-amd64/Packages         ← 每個 Packages 檔的 checksum
 def456... 1820 main/binary-amd64/Packages.gz
 789abc... 3120 main/source/Sources
```

關鍵：`Release` 列出**所有 Packages/Sources 檔的 checksum**。這是信任鏈的中樞（Ch 20）：

```
信任鏈再看一次：
  Release（被 GPG 簽署）
    └── 含 Packages 的 checksum
          └── Packages 含每個 .deb 的 checksum
                └── .deb（checksum 對得上 = 沒被竄改）

簽一個 Release → 保護整個 suite 的所有檔案
```

`InRelease` 是內嵌簽署版（簽署和內容同檔），`Release.gpg` 是分離簽署。現代 apt 偏好 `InRelease`。

## apt 如何使用這個結構

```
你: apt update（以 bookworm main 為例）
        │
  1. 下載 dists/bookworm/InRelease（驗證 GPG 簽署）
        │
  2. 從 Release 取得 Packages 的 checksum
        │
  3. 下載 dists/bookworm/main/binary-amd64/Packages.gz
        │  驗證 checksum（對照 Release）
        ▼
  4. apt 現在知道有哪些套件可用（cache 到 /var/lib/apt/lists/）

你: apt install greet
        │
  5. 從 Packages 找到 greet 的 Filename: pool/main/g/greet/greet_1.0-1_amd64.deb
        │
  6. 下載那個 .deb，驗證 SHA256（對照 Packages）
        │
  7. 交給 dpkg 安裝
```

整個流程的每一步都有 checksum 驗證，根植於簽署的 Release。

## 手工建一個最小 repo

理解結構最好的方式是手建一個：

```bash
# 1. 建立目錄結構
mkdir -p myrepo/pool/main/g/greet
mkdir -p myrepo/dists/bookworm/main/binary-amd64

# 2. 把 .deb 放進 pool
cp greet_1.0-1_amd64.deb libgreet1_1.0-1_amd64.deb \
   myrepo/pool/main/g/greet/

# 3. 生成 Packages 檔（掃描 pool）
cd myrepo
dpkg-scanpackages pool/main /dev/null > dists/bookworm/main/binary-amd64/Packages
gzip -k dists/bookworm/main/binary-amd64/Packages
#   dpkg-scanpackages 掃 pool 裡的 .deb，生成 Packages（含 Filename、checksum）

# 4. 生成 Release 檔（用 apt-ftparchive）
cat > dists/bookworm/Release <<'EOF'
Origin: MyRepo
Label: MyRepo
Suite: bookworm
Codename: bookworm
Architectures: amd64
Components: main
EOF
apt-ftparchive release dists/bookworm >> dists/bookworm/Release
#   apt-ftparchive 算出所有 Packages 的 checksum 加進 Release

# 5. 簽署 Release（Ch 20）
gpg --clearsign -o dists/bookworm/InRelease dists/bookworm/Release
gpg -abs -o dists/bookworm/Release.gpg dists/bookworm/Release
```

使用這個 repo：

```bash
# 用 file:// 或 http 提供（這裡用本地 file://）
echo "deb [signed-by=/path/to/mykey.gpg] \
    file:///path/to/myrepo bookworm main" | \
    sudo tee /etc/apt/sources.list.d/myrepo.list

sudo apt update
sudo apt install greet   # 從你的 repo 裝！
```

> 手工建 repo 能跑，但維護痛苦（每次加套件都要重新掃描、重新簽署、處理舊版本清理）。這正是 reprepro（Ch 22）和 aptly（Ch 23）要自動化的——它們管理 pool、生成 metadata、處理簽署。但你現在知道它們在管理什麼了。

## 故意弄壞：Packages 的 checksum 對不上

```bash
# 手動改 pool 裡的一個 .deb（但不更新 Packages）
echo "tampered" >> myrepo/pool/main/g/greet/greet_1.0-1_amd64.deb

sudo apt update && sudo apt install --reinstall greet
# Get:1 file:.../greet_1.0-1_amd64.deb
# Err: ... Hash Sum mismatch
#   Expected: SHA256:abc123...
#   Got:      SHA256:def456...
# E: Failed to fetch ... Hash Sum mismatch
```

apt 下載 `.deb` 後對照 `Packages` 裡的 SHA256，對不上就拒絕——因為 `Packages` 的 checksum 又被簽署的 `Release` 保護。竄改任何 `.deb` 都會在這層被抓到。這就是 checksum 鏈的保護。

## 踩雷集錦

1. **改了 pool 沒重新生成 Packages**：apt 用舊的 Packages（舊 checksum / 舊套件列表），看不到新套件或 checksum 對不上。加/改套件後必須重新生成 metadata

2. **生成 Packages 沒重簽 Release**：Release 含 Packages 的 checksum。Packages 變了 Release 沒更新，apt 報 checksum mismatch。生成 metadata 後必須重簽

3. **pool 路徑和 Filename 不一致**：`Packages` 的 `Filename:` 必須是相對 repo 根的正確路徑。手工弄錯路徑前綴，apt 下載 404

4. **忘記 component/架構目錄**：sources.list 寫 `main` 但 repo 沒有 `dists/<suite>/main/binary-amd64/`，apt 找不到。結構要對應 sources.list 的宣告

5. **InRelease 簽署 email 沒被信任**：repo 簽署的 key 沒在使用者的 keyring（或沒用 `Signed-By` 指定），apt update 報 NO_PUBKEY。提供 key 並用 Signed-By（Ch 20）

## 進階：by-hash 與 metadata 的原子更新

repo metadata 更新有個微妙問題：apt 下載 Release 後、還沒下載 Packages 時，如果 repo 正在更新（Packages 換了新版），checksum 會對不上。

**by-hash** 機制解決這個：metadata 檔案額外用其 checksum 命名存一份（`by-hash/SHA256/<hash>`）。apt 從 Release 拿到 checksum 後，直接抓 `by-hash/SHA256/<那個hash>`——這個檔案永遠對應那個 checksum，不會在更新中途變動。

```
dists/bookworm/main/binary-amd64/
├── Packages
└── by-hash/
    └── SHA256/
        ├── abc123...    ← Packages 的某個版本（以 checksum 命名）
        └── def456...    ← 更新後的版本（舊的還留著一陣子）
```

這讓 metadata 更新「原子化」——apt 永遠能拿到和 Release 一致的 Packages，即使 repo 正在更新。大型 repo（Debian 官方）都啟用 by-hash。reprepro/aptly 可設定支援。

## 動手練習

1. 完整手建一個最小 repo（照 Step），放練習 B 的套件，簽署，加進 sources.list，`apt install` 你自己 repo 的套件

2. 拆解一個真實 repo：`curl -s http://deb.debian.org/debian/dists/bookworm/Release | head -40`，看 Release 的結構、它列出的 Packages checksum

3. 看 pool 的組織：瀏覽 `http://deb.debian.org/debian/pool/main/`，看它怎麼按字母分目錄，找一個 `lib*` 套件看它的特殊路徑（`libx/`）

4. 故意弄壞 checksum（改你 repo 的一個 .deb 不更新 Packages），看 apt 報 Hash Sum mismatch

## 本章重點整理

- repo 兩層：`dists/`（metadata，按 suite 組織，被簽署）+ `pool/`（實體檔案，按字母組織，多 suite 共用）
- `Packages` 列出每個 `.deb` 的 metadata + Filename（pool 位置）+ checksum
- `Release` 列出所有 Packages 的 checksum 並被 GPG 簽署——信任鏈的中樞
- apt 流程：驗證 Release 簽署 → checksum 鏈驗證 Packages → 驗證下載的 .deb
- pool 設計讓同一套件多 suite 共用一份實體檔；by-hash 讓 metadata 更新原子化

## 自我檢核

- [ ] 能畫出 repo 的目錄結構，說出 dists 和 pool 各放什麼
- [ ] 知道 Packages、Release 各自的內容和角色
- [ ] 能解釋 checksum 鏈如何從簽署的 Release 保護到每個 .deb
- [ ] 知道為什麼設計 pool（多 suite 共用實體檔）
- [ ] 能說出加套件後為什麼必須重新生成 Packages 並重簽 Release

## 延伸閱讀

### 官方文件

- **[Debian Repository Format](https://wiki.debian.org/DebianRepository/Format)**
  - **讀哪裡**：整頁，dists/pool/Release/Packages 的完整規格
  - **學什麼**：repo 格式的權威定義，本章是教學版；by-hash、各檔案的完整欄位
  - **前提**：讀完本章

- **[apt-ftparchive(1) man page](https://manpages.debian.org/bookworm/apt-utils/apt-ftparchive.1.html)**
  - **讀哪裡**：release 和 packages 子命令
  - **學什麼**：手工生成 repo metadata 的工具細節
  - **前提**：本章的手建 repo 部分

### 部落格 / 文章

- **[Setting up a Debian repository](https://wiki.debian.org/DebianRepository/Setup)** — Debian Wiki
  - **這篇說什麼**：各種建 repo 的方式（手工、reprepro、aptly）的比較
  - **讀哪裡**：方式比較和手工那節
  - **為什麼值得讀**：把本章的手工和 Ch 22/23 的工具放在一起比較，知道何時用哪個

→ [Ch 22 reprepro：靜態 repo 管理](./22-reprepro.md)
