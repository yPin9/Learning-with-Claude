# 練習 A — 手工組裝一個 .deb

> **目標**：把 Ch 2–5 學到的東西拼起來，**不用任何打包工具（不用 debhelper、不用 dpkg-buildpackage）**，純手工用 `ar` / `tar` 從零組裝一個合法、可安裝、可移除的 `.deb`。完成後你會徹底理解 `.deb` 不是黑盒子，而 debhelper 只是自動化了這些步驟。

## 背景與動機

你之後會用 debhelper 自動生成 `.deb`，再也不會手碰 `ar`。那為什麼要手工組一次？

因為當 debhelper 產出的套件有問題時——多了不該有的檔案、權限錯誤、metadata 不對、maintainer script 沒被正確嵌入——你需要能打開 `.deb` 看裡面到底有什麼，甚至手動修補。手工組裝一次，你就永遠知道那三層俄羅斯娃娃裡裝了什麼，debhelper 的所有「魔法」對你都變透明了。

這是整門課唯一一次手工組裝。之後都用工具，但這次的理解會貫穿全課。

## 任務規格

組裝一個名為 `greeter` 的套件，符合以下規格：

| 項目 | 規格 |
|---|---|
| 套件名 | `greeter` |
| 版本 | `1.0-1` |
| 架構 | `all`（純 shell script，架構無關）|
| 內容 | 一個 `/usr/bin/greeter` 可執行 shell script，印出問候語 |
| 文件 | `/usr/share/doc/greeter/README`（純文字）|
| 依賴 | `Depends: bash` |
| maintainer script | postinst：安裝後印一行歡迎訊息；postrm（purge）：清理 |
| conffile | `/etc/greeter.conf`（設定問候語，要被 conffile 機制保護）|

**驗收標準**：
- `dpkg-deb -I greeter_1.0-1_all.deb` 能正確顯示 metadata
- `sudo dpkg -i greeter_1.0-1_all.deb` 能安裝成功，postinst 印出訊息
- 裝完後 `greeter` 指令能執行
- `dpkg -L greeter` 顯示正確的檔案列表
- 改過 `/etc/greeter.conf` 後重裝，dpkg 提示 conffile 衝突（驗證 conffile 機制）
- `sudo dpkg --purge greeter` 能乾淨移除，postrm 執行清理
- `lintian greeter_1.0-1_all.deb` 不報 error（warning 可以有，我們是手工組的）

**禁止使用**：`dh`、`dh_make`、`dpkg-buildpackage`、`debuild`、`equivs` 等任何自動打包工具。只准用 `ar`、`tar`、`dpkg-deb --build`、文字編輯器。

> `dpkg-deb --build` 算手工工具（它只是把目錄打包成 .deb，不生成任何 metadata），允許使用。它讓你不用手刻 `ar` 指令，但你仍要手寫所有 metadata 檔案。進階挑戰會要求你連 `dpkg-deb --build` 都不用，純 `ar` 組裝。

## 期望輸出範例

```
$ sudo dpkg -i greeter_1.0-1_all.deb
Selecting previously unselected package greeter.
(Reading database ...)
Preparing to unpack greeter_1.0-1_all.deb ...
Unpacking greeter (1.0-1) ...
Setting up greeter (1.0-1) ...
Welcome! greeter is now installed. Edit /etc/greeter.conf to customize.

$ greeter
Hello from greeter!

$ dpkg -L greeter
/.
/etc
/etc/greeter.conf
/usr
/usr/bin
/usr/bin/greeter
/usr/share
/usr/share/doc
/usr/share/doc/greeter
/usr/share/doc/greeter/README
```

```
邊界情況：改過設定檔後重裝
$ sudo sed -i 's/Hello/Hi/' /etc/greeter.conf
$ sudo dpkg -i greeter_1.0-1_all.deb
Configuration file '/etc/greeter.conf'
 ==> Modified (by you or by a script) since installation.
 ==> Package distributor has shipped an updated version.
   What would you like to do about it ?  [default=N]
```

## 如果你卡住了

1. 回 Ch 4 看 `.deb` 的三層結構——你要手工建立的就是那三層
2. data 目錄裡的路徑要從 `./usr/...` 開始（相對安裝根），不是絕對路徑
3. control 檔的欄位順序和格式有講究，缺 `Description` lintian 會罵；參考 `dpkg-deb -f hello.deb` 的輸出當範本
4. maintainer script 要 `chmod 755`，否則 dpkg 跑不動它
5. conffile 機制要靠 `DEBIAN/conffiles` 檔案列出 `/etc/greeter.conf`
6. 用 fakeroot 包住 `dpkg-deb --build`，否則檔案擁有者是你的 uid 不是 root

## 實作步驟建議

### Step 1：建立目錄結構（輸出：完整的檔案樹）

```bash
mkdir -p greeter/DEBIAN
mkdir -p greeter/usr/bin
mkdir -p greeter/usr/share/doc/greeter
mkdir -p greeter/etc
```

`DEBIAN/`（大寫）目錄是給 `dpkg-deb --build` 用的——它會把這個目錄的內容變成 control.tar，其餘變成 data.tar。

### Step 2：建立實際內容（data 部分）

寫 `/usr/bin/greeter` script、`/etc/greeter.conf` 設定、`/usr/share/doc/greeter/README`。

### Step 3：寫 control 檔（metadata）

`DEBIAN/control`，包含所有必要欄位。

### Step 4：寫 maintainer scripts 和 conffiles

`DEBIAN/postinst`、`DEBIAN/postrm`、`DEBIAN/conffiles`。

### Step 5：組裝與驗證

用 `fakeroot dpkg-deb --build` 組裝，然後跑完所有驗收標準。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

```bash
#!/bin/bash
# build-greeter.sh — 手工組裝 greeter_1.0-1_all.deb
set -e

# === Step 1: 目錄結構 ===
rm -rf greeter
mkdir -p greeter/DEBIAN
mkdir -p greeter/usr/bin
mkdir -p greeter/usr/share/doc/greeter
mkdir -p greeter/etc

# === Step 2: data 內容 ===

# 可執行 script
cat > greeter/usr/bin/greeter <<'EOF'
#!/bin/bash
# 讀設定檔的問候語，預設 Hello
GREETING="Hello"
[ -r /etc/greeter.conf ] && . /etc/greeter.conf
echo "$GREETING from greeter!"
EOF
chmod 755 greeter/usr/bin/greeter

# 設定檔（會被標記成 conffile）
cat > greeter/etc/greeter.conf <<'EOF'
# greeter 設定檔
GREETING="Hello"
EOF
chmod 644 greeter/etc/greeter.conf

# 文件（Policy 要求每個套件有 doc 目錄）
cat > greeter/usr/share/doc/greeter/README <<'EOF'
greeter — a minimal hand-crafted Debian package
================================================

This package was assembled by hand using ar/tar/dpkg-deb
to demonstrate the internal structure of a .deb file.

Edit /etc/greeter.conf to change the greeting.
EOF
chmod 644 greeter/usr/share/doc/greeter/README

# Policy 要求有 changelog.Debian.gz（lintian 會檢查）
cat > /tmp/changelog.Debian <<'EOF'
greeter (1.0-1) unstable; urgency=medium

  * Initial hand-crafted release.

 -- Your Name <you@example.com>  Thu, 29 May 2025 12:00:00 +0000
EOF
gzip -9n -c /tmp/changelog.Debian > greeter/usr/share/doc/greeter/changelog.Debian.gz
chmod 644 greeter/usr/share/doc/greeter/changelog.Debian.gz

# Policy 要求 copyright 檔
cat > greeter/usr/share/doc/greeter/copyright <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: greeter

Files: *
Copyright: 2025 Your Name <you@example.com>
License: MIT
 Permission is hereby granted, free of charge, ...
 (full MIT text here)
EOF
chmod 644 greeter/usr/share/doc/greeter/copyright

# === Step 3: control 檔 ===
# Installed-Size 用 KB；這裡估算
cat > greeter/DEBIAN/control <<'EOF'
Package: greeter
Version: 1.0-1
Architecture: all
Maintainer: Your Name <you@example.com>
Installed-Size: 20
Depends: bash
Section: utils
Priority: optional
Description: minimal hand-crafted greeting tool
 A tiny demonstration package assembled entirely by hand
 using ar/tar/dpkg-deb, to illustrate the internal layout
 of a Debian binary package.
 .
 It prints a configurable greeting message.
EOF

# === Step 4: maintainer scripts + conffiles ===

# conffiles：列出要被 conffile 機制保護的檔案
cat > greeter/DEBIAN/conffiles <<'EOF'
/etc/greeter.conf
EOF

# postinst
cat > greeter/DEBIAN/postinst <<'EOF'
#!/bin/sh
set -e
case "$1" in
    configure)
        echo "Welcome! greeter is now installed. Edit /etc/greeter.conf to customize."
        ;;
    abort-upgrade|abort-remove|abort-deconfigure)
        ;;
    *)
        echo "postinst called with unknown argument \`$1'" >&2
        exit 1
        ;;
esac
exit 0
EOF
chmod 755 greeter/DEBIAN/postinst

# postrm
cat > greeter/DEBIAN/postrm <<'EOF'
#!/bin/sh
set -e
case "$1" in
    purge)
        echo "Purging greeter configuration."
        rm -f /etc/greeter.conf
        ;;
    remove|upgrade|failed-upgrade|abort-install|abort-upgrade|disappear)
        ;;
    *)
        echo "postrm called with unknown argument \`$1'" >&2
        exit 1
        ;;
esac
exit 0
EOF
chmod 755 greeter/DEBIAN/postrm

# === Step 5: 組裝 ===
# 用 fakeroot 讓檔案擁有者標記成 root:root
fakeroot dpkg-deb --build greeter greeter_1.0-1_all.deb

echo "=== Built greeter_1.0-1_all.deb ==="
dpkg-deb -I greeter_1.0-1_all.deb
echo "=== Contents ==="
dpkg-deb -c greeter_1.0-1_all.deb
echo "=== Lintian ==="
lintian greeter_1.0-1_all.deb || true
```

**解答說明**：

- **為什麼 `DEBIAN/` 大寫**：`dpkg-deb --build` 約定大寫 `DEBIAN/` 目錄是 control 區（變成 control.tar），其餘檔案樹變成 data.tar。這和安裝後系統上的 `/var/lib/dpkg/info/` 不同，別混淆
- **為什麼要 fakeroot**：data.tar 裡的檔案應該是 `root:root` 擁有。你不是 root，fakeroot 攔截 chown/stat 相關 syscall 假裝你是。沒有它，裝起來 `/usr/bin/greeter` 會是你的 uid
- **為什麼有 changelog.Debian.gz 和 copyright**：Debian Policy 要求每個套件在 `/usr/share/doc/<pkg>/` 放這兩個檔。沒有的話 lintian 會報 error。changelog 必須 gzip 壓縮（`-9n`，`-n` 不存 timestamp 利於 reproducible）
- **conffiles 的作用**：列在 `DEBIAN/conffiles` 的檔案，dpkg 安裝時記錄其 md5，升級時用 Ch 2 的三方比較保護。沒列的話 `/etc/greeter.conf` 會被當普通檔案無聲覆蓋
- **postrm 的 purge 才刪設定**：尊重使用者意圖，`remove` 保留設定，`purge` 才清

驗證 conffile 機制：

```bash
sudo dpkg -i greeter_1.0-1_all.deb     # 首裝
sudo sed -i 's/Hello/Hi/' /etc/greeter.conf  # 改設定
greeter                                 # Hi from greeter!
sudo dpkg -i greeter_1.0-1_all.deb     # 重裝 → 觸發 conffile 提示
```

</details>

## 測試用例

| 操作 | 預期結果 | 驗證什麼 |
|---|---|---|
| `dpkg-deb -I greeter_1.0-1_all.deb` | 顯示 control 所有欄位 | control.tar 正確 |
| `dpkg-deb -c greeter_1.0-1_all.deb` | 列出檔案，擁有者 root/root | fakeroot 生效 |
| `sudo dpkg -i greeter_*.deb` | 安裝成功，postinst 印訊息 | data.tar + postinst 正確 |
| `greeter` | `Hello from greeter!` | 內容正確、可執行 |
| `dpkg -L greeter` | 正確檔案列表 | dpkg 記帳正確 |
| 改 conf 後重裝 | dpkg 提示 conffile 衝突 | conffile 機制 |
| `sudo dpkg --purge greeter` | postrm 執行，設定被清 | postrm purge 正確 |
| `lintian greeter_*.deb` | 無 error | 符合基本 Policy |

## 延伸挑戰（加分）

- **挑戰一**：不用 `dpkg-deb --build`，純用 `ar` + `tar` 手工組三層（debian-binary + control.tar.gz + data.tar.gz），用 `ar rc` 組成 `.deb`。注意 ar 成員順序（debian-binary 必須第一個）和 tar 的路徑前綴

- **挑戰二**：加一個 preinst，在首次安裝時檢查某個條件（如某個目錄不存在才繼續），故意讓它在重跑時失敗，觀察套件卡在 half-configured，再修成可重入

- **挑戰三**：把 `greeter` 改成 `Architecture: amd64`，加一個真的編譯出來的 C binary（`gcc -o greeter greeter.c`），讓套件不再是架構無關。觀察 lintian 對 binary 的額外檢查（如缺 `Depends: libc6`）

- **挑戰四**：故意把 md5sums 寫錯（手動產生一個假的 `DEBIAN/md5sums`），裝起來後跑 `dpkg --verify greeter`，看它如何報告檔案被竄改

## 自我檢核

- [ ] 能不看參考，徒手建立 `.deb` 的三層並組裝
- [ ] 知道 `DEBIAN/`（大寫）目錄在組裝時對應到 `.deb` 的哪一層
- [ ] 能解釋為什麼組裝要用 fakeroot
- [ ] 能說出 `DEBIAN/conffiles` 不寫會發生什麼（設定檔升級時被無聲覆蓋）
- [ ] 能說出自己的手工套件和 debhelper 生成的套件差在哪（debhelper 自動處理 md5sums、權限、doc 檔、壓縮等）

→ [Ch 6 Source package 格式](./06-source-package-format.md)
