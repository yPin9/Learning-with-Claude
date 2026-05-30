# Ch 6 — Source package 格式

> **目標**：理解 source package 的三個檔案（.dsc / .orig.tar / .debian.tar）如何構成、Format 3.0 (quilt) 與 native 的差異、為什麼要把 upstream 原始碼和 Debian 修改分開——這是整個打包流程的起點。

> **環境**：dpkg-source 1.21.x。Format "3.0 (quilt)" 是目前的標準格式，本章以它為主。

## 為什麼需要 source package？

到目前為止我們都在處理 binary package（`.deb`）。但 `.deb` 是**產物**，不是**原料**。原料是 source package——它包含「如何從 upstream 原始碼 build 出 `.deb`」的完整配方。

為什麼不直接散布 `.deb` 就好？

- **可重建性**：任何人都能從 source package 重新 build 出 binary，驗證它沒被竄改（reproducible builds 的基礎）
- **多架構**：同一個 source package 在 amd64、arm64、i386... 各自 build 出對應的 `.deb`。Debian 的 build farm 就是這樣為所有架構編譯
- **透明的修改追蹤**：Debian 對 upstream 程式碼做的每個修改都是可見的 patch，不是藏在二進位裡
- **法律與信任**：GPL 等授權要求提供 source；Debian 的 social contract 也要求

## 先建立直覺：三個檔案的分工

```
   upstream 釋出的原始碼              Debian 打包者的工作
   ──────────────────              ─────────────────
   foo-1.0.tar.gz       ───→       foo_1.0.orig.tar.gz   ← 原封不動的 upstream
   （開發者寫的程式）                                          （byte 可能完全一樣）
                                    foo_1.0-1.debian.tar.xz ← 只有 Debian 加的東西
                                       └── debian/control
                                       └── debian/rules
                                       └── debian/changelog
                                       └── debian/patches/  ← 對 upstream 的修改
                                       └── ...
                                    foo_1.0-1.dsc          ← 描述檔（綁定上面兩個）
                                       └── checksums, 依賴, 簽署
```

核心設計哲學：**upstream 原始碼和 Debian 修改嚴格分離**。

- `.orig.tar.gz`：upstream 原本的 tarball，理想上 byte-for-byte 和開發者釋出的一樣
- `.debian.tar.xz`：**只**包含 `debian/` 目錄——所有打包相關的檔案
- `.dsc`：Debian Source Control，描述檔，用 checksum 綁定上面兩個，含 build 依賴和 GPG 簽署

這個分離讓「upstream 寫了什麼」和「Debian 改了什麼」一清二楚。

## 三個檔案逐一解剖

抓一個套件的 source 來看：

```bash
apt source hello
ls hello_*
# hello_2.10-3.dsc           ← 描述檔
# hello_2.10.orig.tar.gz     ← upstream 原始碼
# hello_2.10-3.debian.tar.xz ← Debian 修改
```

### .dsc — Debian Source Control

```bash
cat hello_2.10-3.dsc
```

```
-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA512

Format: 3.0 (quilt)              ← source 格式版本
Source: hello                    ← source package 名稱
Binary: hello                    ← 會 build 出哪些 binary package
Architecture: any                ← 支援的架構
Version: 2.10-3                  ← 版本
Maintainer: Santiago Vila <sanvila@debian.org>
Build-Depends: debhelper-compat (= 13)   ← build 需要什麼
Package-List:
 hello deb devel optional arch=any
Checksums-Sha256:                ← 綁定其他兩個檔案的 checksum
 31e0... hello_2.10.orig.tar.gz
 a5f2... hello_2.10-3.debian.tar.xz
Files:
 6444... hello_2.10.orig.tar.gz
 c1d2... hello_2.10-3.debian.tar.xz
-----BEGIN PGP SIGNATURE-----
...
-----END PGP SIGNATURE-----
```

`.dsc` 是 source package 的「目錄」。它用 checksum 綁定 orig 和 debian tarball——如果有人換掉其中一個檔案，checksum 對不上，dpkg-source 拒絕解包。最外層的 GPG 簽署（如果有）確保整個 `.dsc` 來自可信維護者。

### .orig.tar.gz — upstream 原始碼

```bash
tar tzf hello_2.10.orig.tar.gz | head
# hello-2.10/
# hello-2.10/configure
# hello-2.10/src/hello.c
# ...  ← 純粹 upstream 的東西，沒有 debian/ 目錄
```

理想上這個檔案和 upstream 在 GNU/GitHub 釋出的 tarball **byte-for-byte 相同**。這讓人能驗證「Debian 用的 upstream code 確實是公開的那份」。

### .debian.tar.xz — Debian 的修改

```bash
tar tJf hello_2.10-3.debian.tar.xz
# debian/
# debian/changelog
# debian/control
# debian/copyright
# debian/rules
# debian/patches/...    ← 對 upstream 的修改（如果有）
# ...  ← 全部都在 debian/ 底下，不碰 upstream 檔案
```

**關鍵**：`.debian.tar.xz` 只包含 `debian/` 目錄。它**不修改任何 upstream 檔案**——對 upstream 的修改全部以 patch 形式放在 `debian/patches/`（Ch 11），build 時才套用。這是 Format 3.0 (quilt) 的核心約束。

## 解包與重組

```bash
# 解包 source package（dpkg-source -x 讀 .dsc，組合 orig + debian）
dpkg-source -x hello_2.10-3.dsc
# 它會：
# 1. 解開 orig.tar.gz
# 2. 把 debian/ 疊上去
# 3. 套用 debian/patches/ 裡的 patch
# 結果是一個可 build 的工作目錄
cd hello-2.10/

# 反過來：從工作目錄重新打包成 source package
cd ..
dpkg-source -b hello-2.10/
# 生成 .dsc + .debian.tar.xz（orig 不變）
```

> `apt source <pkg>` 其實就是「下載 .dsc + 兩個 tarball，然後 `dpkg-source -x`」的組合。

## Format 演進：1.0 → 3.0

source 格式寫在 `debian/source/format`：

```bash
cat hello-2.10/debian/source/format
# 3.0 (quilt)
```

| Format | 特點 | 現況 |
|---|---|---|
| `1.0` | 古老格式。native 用單一 tarball；非 native 用 orig + 單一 diff.gz | 已過時，diff.gz 無法表達二進位檔修改 |
| `3.0 (quilt)` | 標準。orig.tar + debian.tar，修改用 quilt patch series | **目前主流**，幾乎所有套件用這個 |
| `3.0 (native)` | 給「Debian 原生」軟體（沒有獨立 upstream，如 dpkg 自己） | 用於 Debian 專屬工具 |

**3.0 (quilt) 比 1.0 好在哪**：

- 支援多個 orig tarball（複雜套件可有 `orig-foo.tar`、`orig-bar.tar`）
- 修改用 quilt patch series 管理，每個修改是獨立、有說明的 patch（不是一坨 diff.gz）
- 能處理二進位檔的新增（1.0 的 diff.gz 做不到）
- 支援多種壓縮（xz、zst）

## native vs non-native：一個重要區分

```
non-native（最常見）：              native：
─────────────                    ──────
有獨立的 upstream                  軟體本身就是 Debian 的一部分
（如 nginx、curl、Python）          （如 dpkg、apt、debianutils）

版本：1.0-1                        版本：1.0（沒有 Debian revision）
       ───┬─ ─┬                          沒有 "-N"
   upstream  Debian revision

有 .orig.tar + .debian.tar         只有單一 .tar（debian/ 直接在裡面）
```

判斷方式：**版本號有沒有 `-`**。`2.10-3` 是 non-native（`-3` 是 Debian revision，Ch 9）；`1.21.22` 是 native（沒有 dash）。

> **認識論誠實**：native 格式的使用有爭議。有些人把自己的軟體打成 native 套件圖方便（不用分 orig/debian），但這混淆了「upstream 修改」和「打包修改」。Debian 社群普遍建議：除非軟體真的是 Debian 專屬基礎工具，否則用 non-native（3.0 quilt）。你自己的專案打包成 Debian 套件，也該用 non-native，把你的 release tarball 當 orig。

## 故意弄壞：修改 upstream 檔案不透過 patch

Format 3.0 (quilt) 強制「不直接改 upstream 檔案」。試著違反它：

```bash
dpkg-source -x hello_2.10-3.dsc
cd hello-2.10/

# 直接改一個 upstream 檔案（不透過 quilt）
echo "/* hacked */" >> src/hello.c

# 嘗試重新打包
cd ..
dpkg-source -b hello-2.10/
# dpkg-source: error: aborting due to unexpected upstream changes, see
#   /tmp/hello_2.10-3.diff.xxxxx
# dpkg-source: info: you can integrate the local changes with dpkg-source --commit
```

dpkg-source 偵測到你改了 upstream 檔案卻沒做成 patch，拒絕打包。它建議你用 `dpkg-source --commit` 把修改變成一個正式的 quilt patch（Ch 11 詳談）。這個保護確保所有 upstream 修改都是可見、有記錄的。

## 踩雷集錦

1. **「source package 就是 tarball」**：它是**三個**檔案（.dsc + orig + debian）的集合，`.dsc` 是綁定它們的描述檔。少了任何一個，`dpkg-source -x` 失敗

2. **orig tarball 改了內容**：`.orig.tar.gz` 應該和 upstream 一致。如果你重新打包改了它，checksum 變了，和別人的 orig 不同，破壞了「可驗證 upstream」的承諾。要改 upstream 程式碼用 patch，不要動 orig

3. **把自己的專案打成 native 圖方便**：native 沒有 orig/debian 分離，混淆了 upstream 和打包修改。除非真是 Debian 基礎工具，否則用 3.0 (quilt) non-native

4. **直接改 upstream 檔案不做 patch**：Format 3.0 (quilt) 會在 build 時 reject（如上）。所有 upstream 修改必須是 `debian/patches/` 裡的 quilt patch

5. **`.dsc` 沒簽署就上傳**：本地玩可以不簽，但上傳到任何 repo/archive 都要 GPG 簽署（Ch 20）。沒簽的 source package 無法驗證來源

## 進階：pristine-tar 與 git-buildpackage

實務上維護者常用 git 管理打包，但 git repo 裡不會塞進龐大的 orig tarball。問題：怎麼從 git 重現出 byte-for-byte 相同的 orig.tar？

**pristine-tar** 的解法：它在 git 裡存一個極小的「delta + metadata」，能精確重建原始 tarball（包括壓縮的 byte 順序）。配合 **git-buildpackage (gbp)**，維護者的工作流變成：

```
upstream branch     ← upstream 原始碼（git import-orig 匯入新版）
debian branch       ← 加上 debian/ 目錄
pristine-tar branch ← 存重建 orig.tar 所需的 delta

gbp buildpackage    ← 從 git 自動重建 orig + debian，跑 build
```

這讓整個打包歷史在 git 裡可追溯。大型套件和團隊維護幾乎都用 gbp。本課後面（Ch 31）會在 CI 脈絡再碰到，這裡先知道「git 化打包工作流的核心問題是重現 orig tarball，pristine-tar 解決它」。

## 動手練習

1. `apt source nginx`（或任何複雜套件），看它的 `.dsc`：有幾個 binary package？Build-Depends 有哪些？對照 `hello` 的簡單 `.dsc`

2. 解包再重組：`dpkg-source -x foo.dsc`，進去看 `debian/source/format`，確認是 `3.0 (quilt)`。然後 `dpkg-source -b` 重新打包，確認生成新的 `.debian.tar.xz`

3. 跑「故意弄壞」那段：解包後直接改一個 `.c` 檔，`dpkg-source -b` 看它如何拒絕，讀錯誤訊息

4. 找一個 native 套件對照：`apt source dpkg`，看它的版本號（無 dash）和 source 結構（單一 tarball），對比 non-native 的 hello

## 本章重點整理

- source package = .dsc（描述）+ .orig.tar（upstream）+ .debian.tar（Debian 修改）
- 核心哲學：upstream 原始碼和 Debian 修改嚴格分離；對 upstream 的修改只能透過 patch
- Format 3.0 (quilt) 是標準；native（無 Debian revision）只給 Debian 專屬基礎工具
- `dpkg-source -x` 解包（orig + debian + 套 patch），`-b` 重組
- pristine-tar + gbp 讓 git 化打包工作流能重現 orig tarball

## 自我檢核

- [ ] 不看筆記，能說出 source package 的三個檔案各裝什麼
- [ ] 能解釋為什麼 upstream 修改要用 patch 而不是直接改 orig（兩個理由：可驗證、可追溯）
- [ ] 看到版本號 `2.10-3` 和 `1.21.22`，能判斷哪個是 native
- [ ] 知道 `.dsc` 用什麼機制綁定另外兩個 tarball（checksum）
- [ ] 能說出 Format 3.0 (quilt) 比 1.0 好在哪

## 延伸閱讀

### 官方文件

- **[dpkg-source(1) man page](https://manpages.debian.org/bookworm/dpkg-dev/dpkg-source.1.html)**
  - **讀哪裡**:「SOURCE PACKAGE FORMATS」整節，特別是 3.0 (quilt) 和 3.0 (native)
  - **學什麼**：每種格式的精確行為、各自的限制；本章是教學版，這是規格
  - **前提**：讀完本章

- **[dsc(5) man page](https://manpages.debian.org/bookworm/dpkg-dev/dsc.5.html)**
  - **讀哪裡**：所有欄位定義
  - **學什麼**：`.dsc` 每個欄位的精確語意
  - **前提**：無

### 部落格 / 文章

- **[git-buildpackage 官方教學](https://gbp.sigxcpu.org/manual/)** — gbp 維護者
  - **這篇說什麼**：用 git 管理打包的完整工作流，pristine-tar、import-orig、buildpackage
  - **讀哪裡**:「Building packages from the Git repository」和 import-orig 章節
  - **為什麼值得讀**：實務上幾乎所有現代打包都用 gbp，這是進入團隊維護的必備知識

→ [Ch 7 debian/control：套件 metadata](./07-debian-control.md)
