# Ch 22 — lintian 靜態分析與常見錯誤修正

> 目標：會用 lintian 掃描自己打的 .deb，看懂 E/W/I 三種層級的輸出，知道最常見的警告怎麼修。

## lintian 是什麼

lintian 是 Debian 的打包靜態分析工具，相當於打包界的 `cppcheck` 或 ESLint。它根據 Debian Policy 和 Developer Reference 的規範，檢查 .deb 和 .changes 檔裡的各種問題。

```bash
sudo apt install lintian

# 基本用法：掃描 .deb
lintian mygreet_1.0-1_amd64.deb

# 掃描 .changes（包含 source + binary，更完整）
lintian mygreet_1.0-1_amd64.changes

# 詳細解說每個問題
lintian --explain mygreet_1.0-1_amd64.deb

# 顯示 pedantic 層級（最嚴格）
lintian -p mygreet_1.0-1_amd64.deb
```

## 三種嚴重程度

```
E: mygreet: no-changelog-entry        ← Error：會讓 Debian 拒絕上傳
W: mygreet: maintainer-address-malformed ← Warning：應修但不致命
I: mygreet: description-synopsis-starts-with-article ← Informational：建議性

格式：<severity>: <package-name>: <tag-name>  [<extra info>]
```

## 最常見的 Error

### E: no-changelog-entry

```
E: mygreet: changelog-file-missing
```

**原因**：缺 `debian/changelog`，或 changelog 格式錯誤。

**修法**：

```bash
# 用 dch 建立
dch --create --package mygreet --newversion 1.0-1 "Initial release."

# 確認格式正確（末行要有 maintainer + 時間戳）
cat debian/changelog
# mygreet (1.0-1) unstable; urgency=medium
#
#   * Initial release.
#
#  -- Your Name <you@example.com>  Sun, 11 May 2025 10:00:00 +0800
```

### E: control-file-has-bad-permissions

```
E: mygreet: control-file-has-bad-permissions DEBIAN/control 0640 != 0644
```

**修法**：

```bash
chmod 644 greet-1.0/DEBIAN/control
chmod 755 greet-1.0/DEBIAN/          # 目錄要 755
```

### E: file-in-usr-local

```
E: mygreet: file-in-usr-local usr/local/bin/mygreet
```

**原因**：Debian 套件**不能**把東西裝到 `/usr/local/`，那是系統管理員手動安裝的保留地。

**修法**：修 Makefile 或 CMakeLists.txt 讓 install prefix 用 `/usr` 而非 `/usr/local`：

```makefile
# Makefile
PREFIX ?= /usr       ← 預設改成 /usr（dh_auto_install 會設 DESTDIR，不影響 prefix）
```

## 最常見的 Warning

### W: new-package-should-close-itp-bug

```
W: mygreet: new-package-should-close-itp-bug
```

新套件送進 Debian 前要先在 BTS（Bug Tracking System）開 ITP（Intent To Package）bug。上傳時 changelog 要寫 `Closes: #NNNNNN`。

本地測試時可以忽略這個。

### W: maintainer-address-malformed

```
W: mygreet: maintainer-address-malformed Your Name <you@>
```

**修法**：確認 `Maintainer:` 格式是 `Full Name <email@example.com>`：

```
Maintainer: Your Name <you@example.com>
```

### W: description-synopsis-is-duplicated

兩個 Package 段落的 `Description:` 第一行（synopsis）相同。每個套件的 synopsis 要不一樣。

### W: copyright-not-using-common-license-for-gpl

```
W: mygreet: copyright-not-using-common-license-for-gpl
```

**原因**：`debian/copyright` 裡 GPL 條款要用 reference 格式而不是貼全文。

**修法**：

```
# debian/copyright（DEP-5 格式）
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: mygreet
Source: https://example.com/mygreet

Files: *
Copyright: 2024 Your Name <you@example.com>
License: GPL-2+

License: GPL-2+
 On Debian systems, the full text of the GNU General
 Public License version 2 can be found in the file
 `/usr/share/common-licenses/GPL-2'.
```

## 最常見的 Informational

### I: description-synopsis-starts-with-article

```
I: mygreet: description-synopsis-starts-with-article
```

Description 的 synopsis（第一行）不要以 "A" 或 "The" 開頭。

```
# 錯誤
Description: A simple greeting tool

# 正確
Description: Simple greeting tool
```

### I: no-manual-page

```
I: mygreet: no-manual-page usr/bin/mygreet
```

Debian 希望每個可執行檔都有 man page。最簡單的做法：

```
# debian/mygreet.1（man page 原始檔）
.TH MYGREET 1 "2025-05-11" "1.0" "User Commands"
.SH NAME
mygreet \- greet a person by name
.SH SYNOPSIS
.B mygreet
.I name
.SH DESCRIPTION
.B mygreet
prints a greeting for the given name.
.SH EXAMPLES
.B mygreet World
.PP
Hello, World!
```

```makefile
# debian/rules 中讓 dh_installman 抓到
# 通常自動偵測，但可以明確指定：
override_dh_installman:
	dh_installman debian/mygreet.1
```

## 整合到 build 流程

```bash
# debuild 會自動在 build 後跑 lintian
debuild -us -uc -b

# 或手動跑，記錄結果
lintian --no-tag-display-limit mygreet_1.0-1_amd64.deb 2>&1 | tee lintian.txt

# 只看 Error 和 Warning（忽略 I）
lintian -EW mygreet_1.0-1_amd64.deb

# 查某個 tag 的完整說明
lintian-explain-tags no-manual-page
```

## 常見修正路徑

```
lintian 輸出          →  修哪裡           →  重新 build 驗證
─────────────────────────────────────────────────────────
E: file-in-usr-local  →  PREFIX=/usr       →  dpkg-buildpackage -b
W: maint-addr-malformed → debian/control   →  dpkg-buildpackage -b
I: no-manual-page     →  debian/mygreet.1  →  dpkg-buildpackage -b
E: ctrl-bad-perm      →  chmod 644 control →  dpkg-deb --build
W: copyright-bad-lic  →  debian/copyright  →  dpkg-buildpackage -b
```

## lintian 與 Debian 的關係

| 情境 | lintian 要求 |
|------|------------|
| 本地測試 | Error 應修；W/I 酌情 |
| PPA 上傳（Ubuntu）| E 必修；W 建議修 |
| 上傳進 Debian 官方 | E + 多數 W 都要清零 |
| 公司內部 repo | 完全自定義（不強制）|

Debian 的自動接收系統（dak）在上傳時**自動跑 lintian**，有 Error 直接退回。

## 自我檢核

- [ ] `lintian -EW <.deb>` 只顯示 Error 和 Warning；`-p` 開最嚴格模式
- [ ] `file-in-usr-local`：PREFIX 改 `/usr`，不要裝到 `/usr/local/`
- [ ] `no-changelog-entry`：`dch` 建立正確格式的 changelog
- [ ] synopsis 不要以 "A"/"The" 開頭；建議加 man page
- [ ] `debian/copyright` 用 DEP-5 格式，GPL 用 reference 而非貼全文
- [ ] `debuild` 會自動在 build 後跑 lintian

→ [練習 A：修復壞掉的依賴環境](./practice-a-broken-deps.md)
