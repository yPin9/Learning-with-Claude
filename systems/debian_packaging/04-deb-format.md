# Ch 4 — .deb 檔案格式解剖

> **目標**：徹底拆解 `.deb` 檔案的內部結構——它其實是個 `ar` archive，內含三個成員（debian-binary / control.tar / data.tar）——理解每一層的內容、用工具逐層解開，建立「`.deb` 不是黑盒子」的認知。

> **環境**：dpkg 1.21.x。`.deb` 格式自 2000 年代初穩定至今，本章內容在所有近代 Debian/Ubuntu 通用。

## 為什麼要拆解 .deb？

你之後會用 debhelper 自動生成 `.deb`，根本不用手碰這些內部結構。那為什麼要拆？

因為當 debhelper 生成的套件不對勁時——裝了不該裝的檔案、metadata 錯誤、檔案權限不對——你必須能打開 `.deb` 看裡面到底有什麼，而不是盲猜。`.deb` 不是魔法，它是個結構清楚的 archive。能徒手拆開它、甚至徒手組裝它（練習 A），你對打包就有了 X 光視野。

## 先建立直覺：.deb 是個三層俄羅斯娃娃

```
foo_1.0-1_amd64.deb   ← 這是一個 ar archive（不是 tar、不是 zip）
│
├── debian-binary      ← 純文字檔，內容就一行："2.0\n"（格式版本）
│
├── control.tar.{gz,xz,zst}   ← metadata 層
│   ├── ./control       ← 套件資訊（名稱、版本、依賴...）
│   ├── ./md5sums       ← 每個檔案的 checksum
│   ├── ./conffiles     ← 哪些是設定檔
│   ├── ./preinst       ← maintainer scripts（若有）
│   ├── ./postinst
│   ├── ./prerm
│   └── ./postrm
│
└── data.tar.{gz,xz,zst}      ← 實際內容層
    ├── ./usr/bin/foo          ← 會被裝到系統的所有檔案
    ├── ./usr/lib/...          ← 保留了完整的路徑與權限
    └── ./usr/share/doc/foo/...
```

三層各司其職：
- **debian-binary**：版本標記，確保 dpkg 知道怎麼讀這個 `.deb`
- **control.tar**：dpkg 安裝前要知道的一切（metadata + scripts）
- **data.tar**：實際要灑進系統的檔案，路徑就是安裝後的絕對路徑

> 為什麼用 `ar` 不用 `tar`？歷史原因：1995 年 dpkg 重寫時選了 `ar`（Unix 的古老 archive 格式）作為外層容器。`ar` 的好處是能不解壓就快速取出某個成員——dpkg 可以只讀 control.tar 看 metadata，不碰龐大的 data.tar。這在當年磁碟慢的時代是重要優化。

## 動手拆解一個 .deb

我們拆開 `hello` 套件。

```bash
# 先抓一個 .deb（如果沒有）
apt download hello
ls hello_*.deb
# hello_2.10-3_amd64.deb

# === 方法一：用 ar 看外層結構 ===
ar t hello_2.10-3_amd64.deb
# debian-binary
# control.tar.xz
# data.tar.xz

# 把三個成員取出來
mkdir extract && cd extract
ar x ../hello_2.10-3_amd64.deb
ls
# control.tar.xz  data.tar.xz  debian-binary
```

逐層檢視：

```bash
# === debian-binary：就一行 ===
cat debian-binary
# 2.0

# === control.tar：metadata ===
mkdir control && tar -C control -xf control.tar.xz
ls control
# control  md5sums

cat control/control
# Package: hello
# Version: 2.10-3
# Architecture: amd64
# Maintainer: Santiago Vila <sanvila@debian.org>
# Installed-Size: 280
# Depends: libc6 (>= 2.14)
# Section: devel
# Priority: optional
# Homepage: https://www.gnu.org/software/hello/
# Description: example package based on GNU hello
#  ...

head control/md5sums
# a7f6... usr/bin/hello
# b2c3... usr/share/doc/hello/AUTHORS
# ...

# === data.tar：實際檔案 ===
mkdir data && tar -C data -xf data.tar.xz
find data -type f | head
# data/usr/bin/hello
# data/usr/share/doc/hello/changelog.Debian.gz
# data/usr/share/man/man1/hello.1.gz
# ...

# data.tar 裡的路徑就是安裝後的位置！
# data/usr/bin/hello → 裝到 /usr/bin/hello
```

## 用 dpkg-deb 更方便地檢視

每次 `ar x` + `tar x` 太繁瑣。dpkg 提供 `dpkg-deb` 一步到位：

```bash
# 看 control 檔（metadata）
dpkg-deb --info hello_2.10-3_amd64.deb
# 或縮寫
dpkg-deb -I hello_2.10-3_amd64.deb

# 只看 control 檔本身
dpkg-deb --field hello_2.10-3_amd64.deb
dpkg-deb -f hello_2.10-3_amd64.deb Version Depends   # 只看特定欄位

# 列出會裝哪些檔案（data.tar 的內容）
dpkg-deb --contents hello_2.10-3_amd64.deb
dpkg-deb -c hello_2.10-3_amd64.deb
# -rwxr-xr-x root/root  29448  ./usr/bin/hello   ← 注意權限與擁有者！

# 完整解開到目錄
dpkg-deb --raw-extract hello_2.10-3_amd64.deb /tmp/hello-extract
#   --raw-extract 同時解開 control 和 data
dpkg-deb --extract hello_2.10-3_amd64.deb /tmp/hello-data  # 只解 data
```

> `dpkg-deb -c` 輸出裡的權限和擁有者（`root/root`）很重要。data.tar 保留了完整的 POSIX 權限。打包時用 `fakeroot` 就是為了讓這些檔案標記成 `root:root` 而你不用真的是 root。

## control 檔案的關鍵欄位

control.tar 裡的 `control` 是 dpkg 安裝決策的核心（注意：這和 source package 的 `debian/control` 不同——這個是「binary control」，是從前者生成的）。

| 欄位 | 意義 |
|---|---|
| `Package` | 套件名 |
| `Version` | 版本（含 Debian revision，Ch 9）|
| `Architecture` | `amd64` / `arm64` / `all`（架構無關）|
| `Depends` | runtime 依賴 |
| `Installed-Size` | 安裝後佔用空間（KB，估算）|
| `Maintainer` | 維護者 |
| `Section` / `Priority` | 分類與重要性 |
| `Description` | 描述（第一行 synopsis + 後續長描述）|

```bash
# Architecture: all 的套件（如純 Python、文件、設定）
apt download fonts-noto-core 2>/dev/null || apt download debian-keyring
dpkg-deb -f *.deb Architecture
# all   ← 表示任何架構都能裝，不含編譯出的 binary
```

## 壓縮格式的演進

data.tar 和 control.tar 的壓縮格式隨時間改變：

```
gzip (.gz)   →  早期預設，快但壓縮率低
bzip2 (.bz2) →  短暫使用過
xz (.xz)     →  Debian 9–11 預設，壓縮率高但慢
zstd (.zst)  →  Ubuntu 21.10+ 預設，壓縮率接近 xz 但解壓快很多
```

```bash
# 看某個 .deb 用什麼壓縮
ar t some.deb
# data.tar.zst  ← Ubuntu 新版
# data.tar.xz   ← Debian

# 打包時可指定壓縮（dpkg-deb -Z）
dpkg-deb --build -Zxz mypackage/   # 用 xz
dpkg-deb --build -Zzstd mypackage/ # 用 zstd
```

> **認識論誠實**：壓縮格式選擇有真實爭議。Ubuntu 為了加快安裝速度（尤其在 ARM 裝置）換成 zstd；Debian 較保守，因為 zstd 較新、要確保所有工具鏈支援。zstd 解壓快但需要較新的 dpkg。打包給舊系統時這是要考慮的相容性點。

## 故意弄壞：手動破壞一個 .deb

理解格式最好的方式是破壞它，看 dpkg 怎麼抱怨。

```bash
# 複製一份來玩
cp hello_2.10-3_amd64.deb broken.deb

# 把 debian-binary 改成錯誤版本
mkdir bad && cd bad
ar x ../broken.deb
echo "9.9" > debian-binary    # 假版本
ar rc ../broken-rebuilt.deb debian-binary control.tar.xz data.tar.xz
cd ..

# 嘗試安裝
sudo dpkg -i broken-rebuilt.deb
# dpkg-deb: error: file 'broken-rebuilt.deb' contains
#   ununderstood data member format version 9.9 ...
```

dpkg 讀 debian-binary 發現是不認識的格式版本，直接拒絕。這驗證了 debian-binary 不是裝飾，而是真的被檢查。

## 踩雷集錦

1. **「`.deb` 是 zip / 用 unzip 開」**：`.deb` 是 `ar` archive，不是 zip。`unzip foo.deb` 會失敗。用 `ar`、`dpkg-deb`、或支援 `ar` 的 archive 工具（如 `7z` 部分支援）

2. **「data.tar 的路徑可以是相對 /usr 的」**：data.tar 裡的路徑是 `./usr/bin/foo`（相對於檔案系統根的安裝路徑）。打包工具會處理這個，但手工組裝時搞錯路徑前綴是常見錯誤（練習 A 會踩到）

3. **混淆 source 的 `debian/control` 和 binary 的 `control`**：source package 的 `debian/control` 可能描述多個 binary package；每個 binary `.deb` 裡的 `control` 是針對單一套件生成的。Ch 7 會講這個轉換

4. **md5sums 對不上**：如果你手動改了 data.tar 裡的檔案但沒更新 control.tar 裡的 md5sums，`dpkg --verify` 會報檔案被竄改。md5sums 是完整性檢查，不是安全簽署（簽署在 repo 層，Ch 20）

5. **權限沒設對**：data.tar 裡 binary 應該是 `755 root:root`，設定檔可能是 `644`。如果你不用 fakeroot 直接 tar，檔案會是你的 uid，裝起來權限錯誤。fakeroot 是關鍵

## 進階：dpkg-deb 的內部與 reproducible builds

`dpkg-deb --build` 組裝 `.deb` 時，會把 data.tar 裡的檔案按特定順序排列、設定固定的 timestamp（如果環境變數 `SOURCE_DATE_EPOCH` 有設），這是為了 **reproducible builds**——同樣的 source 在不同時間、不同機器 build 出 byte-for-byte 相同的 `.deb`。

```bash
# Debian 的 reproducible builds 計畫驗證這件事
# 設定 SOURCE_DATE_EPOCH 讓 timestamp 固定
export SOURCE_DATE_EPOCH=$(date -d "2024-01-01" +%s)
# 之後 build 出的 .deb 內部 timestamp 固定，可重現
```

為什麼重要？如果同樣的 source 永遠 build 出相同的 binary，任何人都能驗證「官方發布的 `.deb` 確實是從公開的 source build 的，沒有被植入後門」。這是供應鏈安全的基石。Debian 的 reproducible-builds.org 計畫追蹤所有套件的可重現性。

## 動手練習

1. 用 `ar x` 手動拆開一個 `.deb`，再用 `tar -xf` 解開 control.tar 和 data.tar，確認你能看到 control 檔和實際檔案。對照 `dpkg-deb -I` 和 `dpkg-deb -c` 的輸出

2. 比較兩個套件的壓縮格式：抓一個 Debian 套件和一個 Ubuntu 套件（如果你有兩邊環境），`ar t` 看 data.tar 的副檔名（.xz vs .zst）

3. 跑「故意弄壞」那段，把 debian-binary 改成假版本重新組裝，確認 dpkg 拒絕。再改回 `2.0` 確認能裝

4. 用 `dpkg-deb -f <deb> Installed-Size` 看一個大套件宣稱的安裝大小，再 `dpkg-deb -c` 數實際檔案，思考這個數字怎麼來的（估算，不是精確）

## 本章重點整理

- `.deb` 是 `ar` archive，含三個成員：debian-binary（版本）、control.tar（metadata+scripts）、data.tar（實際檔案）
- control.tar 的 `control` 檔是 dpkg 的安裝決策依據；data.tar 的路徑就是安裝後位置
- `dpkg-deb -I`（看 metadata）、`-c`（看檔案列表）、`--extract`（解開）是日常工具
- data.tar 保留完整 POSIX 權限，打包用 fakeroot 標記 root:root
- 壓縮格式從 gzip → xz → zstd 演進，涉及速度/相容性的權衡

## 自我檢核

- [ ] 不看筆記，能畫出 `.deb` 的三層結構並說出每層內容
- [ ] 能用 `ar` + `tar` 徒手拆開一個 `.deb`（不靠 dpkg-deb）
- [ ] 知道 data.tar 裡的路徑前綴是什麼（`./usr/...`），以及為什麼
- [ ] 能解釋為什麼打包要用 fakeroot（和 data.tar 的權限有關）
- [ ] 知道 reproducible builds 解決什麼問題，以及為什麼對供應鏈安全重要

## 延伸閱讀

### 官方文件

- **[deb(5) man page](https://manpages.debian.org/bookworm/dpkg-dev/deb.5.html)**
  - **讀哪裡**：整頁，它精確定義了 `.deb` 的三成員結構
  - **學什麼**：格式的權威規格；本章的結構圖就是它的視覺化
  - **前提**：讀完本章

- **[dpkg-deb(1) man page](https://manpages.debian.org/bookworm/dpkg/dpkg-deb.1.html)**
  - **讀哪裡**：所有 action（`-I`, `-c`, `-x`, `-b`...）
  - **學什麼**：操作 `.deb` 的完整工具集
  - **前提**：無

### 部落格 / 文章

- **[Reproducible Builds in Debian](https://reproducible-builds.org/docs/)** — Reproducible Builds 計畫
  - **這篇說什麼**：如何讓 build 可重現，以及為什麼這對供應鏈安全是基石
  - **讀哪裡**:「How to make your software build reproducibly」和 `SOURCE_DATE_EPOCH` 那節
  - **為什麼值得讀**：這是跨發行版的重要安全計畫，理解它讓你的打包更專業

→ [Ch 5 dpkg 的 maintainer scripts](./05-maintainer-scripts.md)
