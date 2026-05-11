# Ch 17 — debian/ 目錄結構全覽

> 目標：理解用 debhelper 打包時的標準 debian/ 目錄結構，知道每個檔案的用途和格式。

## debian/ vs DEBIAN/ 的區別

這是一個常見混淆點：

| | `debian/`（小寫） | `DEBIAN/`（大寫） |
|---|---|---|
| 位置 | 源碼樹的目錄（開發時用）| .deb 壓縮包內的目錄（安裝時用）|
| 用途 | 打包 build 系統的輸入 | dpkg 安裝時讀取的 metadata |
| 使用時機 | `dpkg-buildpackage` 或 `debuild` 時 | 最終 .deb 裡 |
| 內容 | 更豐富（rules、patches、source 格式等）| 精簡版（control、scripts、md5sums）|

debhelper 讀 `debian/`，然後把需要的東西放進 .deb 的 `DEBIAN/`。

## 標準 debian/ 目錄結構

```
myproject/
├── src/                ← 程式原始碼
├── Makefile
└── debian/             ← 打包相關的所有設定
    ├── control         ← 必填：套件 metadata
    ├── rules           ← 必填：build 腳本（Makefile 格式）
    ├── changelog       ← 必填：版本歷程
    ├── copyright       ← 必填（Debian 官方要求）
    ├── compat          ← 必填：debhelper 相容版本號
    ├── source/
    │   └── format      ← 必填：源碼格式
    ├── install         ← 選填：指定要安裝哪些檔案到哪裡
    ├── dirs            ← 選填：確保某些目錄存在
    ├── links           ← 選填：建立 symlink
    ├── conffiles       ← 選填：設定檔列表
    ├── postinst        ← 選填：安裝後腳本
    ├── preinst         ← 選填：安裝前腳本
    ├── postrm          ← 選填：移除後腳本
    ├── prerm           ← 選填：移除前腳本
    ├── triggers        ← 選填：觸發器設定
    └── patches/        ← 選填：上游源碼的 patch
        └── series
```

## 必填檔案

### debian/control

和 Ch 9 的 control 格式相同，但多了 `Source`（源碼套件名稱）和 `Build-Depends`（build 時需要的套件）：

```
Source: myproject
Section: utils
Priority: optional
Maintainer: Your Name <you@example.com>
Build-Depends: debhelper-compat (= 13), gcc, libssl-dev
Standards-Version: 4.6.0
Homepage: https://example.com/myproject

Package: myproject
Architecture: amd64
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: My project description
 Longer description here.
```

`${shlibs:Depends}` 和 `${misc:Depends}` 是 debhelper 自動填入的占位符——build 時 dh_shlibdeps 和 dh_gencontrol 會自動計算動態庫依賴。

### debian/rules

Makefile 格式，定義 build 流程：

```makefile
#!/usr/bin/make -f

%:
	dh $@
```

這是最簡單的形式——一行 `dh $@` 讓 debhelper 接管所有 build 步驟。`%:` 匹配所有目標（build、install、binary...）。

### debian/changelog

有嚴格格式，`dch` 工具可以自動更新：

```
myproject (1.0-1) unstable; urgency=medium

  * Initial release.

 -- Your Name <you@example.com>  Sun, 11 May 2025 10:00:00 +0800
```

格式：`套件名 (版本) 發行版; urgency=優先級`

```bash
# 安裝 devscripts（提供 dch 工具）
sudo apt install devscripts

# 新增一個 changelog 條目
dch --newversion 1.0-2 "Fix bug in greeting logic"
dch -r   # 標記為發布狀態
```

### debian/compat

指定 debhelper 相容版本（影響很多行為）：

```
13
```

現代打包用 13（Ubuntu 22.04 的 debhelper 版本）。但推薦用 control 裡的 `Build-Depends: debhelper-compat (= 13)` 代替這個檔案（兩者只選一個）。

### debian/source/format

```
3.0 (native)
```

三種格式：
- `3.0 (native)`：上游和打包是同一個專案（你自己的程式）
- `3.0 (quilt)`：上游 tarball + debian/ 目錄分開（打包別人的軟體）
- `1.0`：舊格式，不推薦

## 選填但常用的檔案

### debian/install

指定哪些檔案裝到哪個目錄（當 Makefile 的 install 不夠用時）：

```
# 格式：來源路徑（相對於 build 目錄）  目標目錄
build/myproject          usr/bin/
config/myproject.conf    etc/myproject/
scripts/myproject-init   etc/init.d/
```

### debian/dirs

確保某些目錄在安裝時存在（dh_installdirs 讀取）：

```
usr/bin
usr/share/myproject
etc/myproject/conf.d
```

### debian/links

建立 symlink（dh_link 讀取）：

```
# 格式：目標（已存在的）  連結（要建立的）
usr/bin/myproject   usr/bin/mp    # /usr/bin/mp → /usr/bin/myproject
```

### debian/postinst

安裝後腳本（shell script）：

```bash
#!/bin/sh
set -e

case "$1" in
    configure)
        # 建立必要目錄
        mkdir -p /var/lib/myproject
        # 啟動 systemd service
        systemctl daemon-reload || true
        systemctl enable myproject.service || true
        ;;
esac

#DEBHELPER#    ← 不要刪這行！dh_installdeb 會在這裡插入代碼
```

## 多個 binary 套件

一個源碼套件（Source）可以產生多個 binary 套件（Package）：

```
# debian/control
Source: myproject
Build-Depends: debhelper-compat (= 13), libssl-dev

Package: myproject
Architecture: amd64
Depends: ${shlibs:Depends}
Description: Main binary

Package: myproject-dev
Architecture: amd64
Depends: myproject (= ${binary:Version}), libssl-dev
Description: Development headers

Package: myproject-doc
Architecture: all
Description: Documentation
```

對應的 install 檔分別命名：
```
debian/myproject.install
debian/myproject-dev.install
debian/myproject-doc.install
```

## 自我檢核

- [ ] `debian/`（小寫）= build 時的輸入；`DEBIAN/`（大寫）= .deb 內的 metadata
- [ ] 必填：`control`、`rules`、`changelog`、`source/format`，加上 `compat` 或 Build-Depends 的 `debhelper-compat`
- [ ] `${shlibs:Depends}` 和 `${misc:Depends}` 是占位符，debhelper 在 build 時自動填入
- [ ] `debian/install` 手工指定檔案安裝路徑；`debian/dirs` 確保目錄存在
- [ ] 一個 Source 套件可以產生多個 Package（-dev、-doc 等）

→ [Ch 18 debhelper 與 dh 自動化](./18-debhelper.md)
