# Ch 8 — deb 套件格式

> 目標：拆開一個真實的 .deb 檔案，看懂它的內部結構，理解 dpkg 裝套件時在做什麼。

## .deb 是什麼格式

`.deb` 是一個 **`ar` 格式的 archive**（不是 tar，不是 zip）：

```bash
# 用 ar 看 .deb 的內容
apt download curl
ar -t curl_7.81.0-1ubuntu1.15_amd64.deb
```

輸出：
```
debian-binary
control.tar.xz
data.tar.xz
```

三個固定的成員：

| 檔案 | 內容 |
|-----|-----|
| `debian-binary` | 純文字，內容只有 `2.0\n`（格式版本號） |
| `control.tar.xz` | 套件的 metadata（依賴、版本、描述、維護者腳本） |
| `data.tar.xz` | 實際要安裝到系統的檔案 |

## 動手拆解

```bash
# 下載 curl 的 deb
apt download curl

# 方法 1：用 dpkg-deb 解包（最方便）
mkdir curl-unpacked
dpkg-deb -x curl_*.deb curl-unpacked/     # 解 data（實際檔案）
dpkg-deb -e curl_*.deb curl-unpacked/DEBIAN/  # 解 control

# 方法 2：直接用 ar（更底層）
mkdir curl-raw
cd curl-raw
ar -x ../curl_*.deb
ls
# debian-binary  control.tar.xz  data.tar.xz

# 解 data.tar.xz
tar -xJf data.tar.xz
# 解 control.tar.xz
mkdir DEBIAN
tar -xJf control.tar.xz -C DEBIAN/
```

## 看 data.tar（實際檔案）

```bash
tar -tJf data.tar.xz
```

```
./
./usr/
./usr/bin/
./usr/bin/curl
./usr/share/
./usr/share/doc/
./usr/share/doc/curl/
./usr/share/doc/curl/changelog.Debian.gz
./usr/share/man/
./usr/share/man/man1/
./usr/share/man/man1/curl.1.gz
```

這就是 dpkg 安裝套件時展開的東西——直接覆蓋到系統根目錄，`./usr/bin/curl` 就變成 `/usr/bin/curl`。

## 看 control.tar（metadata）

```bash
tar -tJf control.tar.xz
```

```
./
./control       ← 主要 metadata（依賴、版本、描述）
./md5sums       ← 所有檔案的 MD5 checksum
./conffiles     ← 設定檔列表（移除套件時保留這些）
./postinst      ← 安裝後執行的腳本（若有）
./prerm         ← 移除前執行的腳本（若有）
./postrm        ← 移除後執行的腳本（若有）
./preinst       ← 安裝前執行的腳本（若有）
```

維護者腳本（preinst/postinst/prerm/postrm）是 dpkg 的鉤子，允許套件在安裝/移除時執行任意腳本（建立用戶、設定 systemd service 等）。

## 看 control 檔

```bash
dpkg-deb -f curl_*.deb
# 或
cat DEBIAN/control
```

```
Package: curl
Version: 7.81.0-1ubuntu1.15
Architecture: amd64
Maintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>
Installed-Size: 448
Depends: libc6 (>= 2.17), libcurl4 (= 7.81.0-1ubuntu1.15), zlib1g (>= 1:1.1.4)
Suggests: libcurl4-doc, libcurl4-nss-dev, libcurl4-openssl-dev, libcurl4-gnutls-dev
Conflicts: curl-ssl
Replaces: curl-ssl
Section: web
Priority: optional
Description: command line tool for transferring data with URL syntax
 curl is a command line tool for transferring data with URL syntax,
 supporting DICT, FILE, FTP, FTPS, GOPHER, GOPHER+, HTTP, HTTPS, IMAP,
 ...
```

## dpkg 安裝套件的流程

現在你看過結構了，安裝流程就很清楚：

```
dpkg -i curl.deb
    ↓
1. 解壓 control.tar → 讀 metadata
2. 執行 preinst 腳本（若有）
3. 解壓 data.tar → 覆蓋到系統（/usr/bin/curl 等）
4. 執行 postinst 腳本（若有）
5. 在 /var/lib/dpkg/info/ 記錄：
   - curl.list（裝了哪些檔案）
   - curl.md5sums
   - curl.postinst（腳本備份）
6. 更新 /var/lib/dpkg/status（套件狀態資料庫）
```

## 比較不同套件的 deb 大小

```bash
# 下載幾個套件看大小差異
apt download curl vim-tiny python3-minimal

ls -lh *.deb
# curl：小（只有一個 binary）
# vim-tiny：中
# python3-minimal：中（含 stdlib）
```

## 自我檢核

- [ ] `.deb` 是 `ar` 格式；內含 `debian-binary`（版本號）、`control.tar.xz`（metadata）、`data.tar.xz`（實際檔案）
- [ ] `data.tar` 解壓路徑直接對應系統根目錄（`./usr/bin/curl` → `/usr/bin/curl`）
- [ ] `control.tar` 包含：`control`（依賴/版本）、`md5sums`、`conffiles`、維護者腳本（preinst/postinst/prerm/postrm）
- [ ] `dpkg-deb -x` 解出 data；`dpkg-deb -e` 解出 control；`ar -x` 是最底層拆法

→ [Ch 9 DEBIAN/control 與 metadata](./09-control-metadata.md)
