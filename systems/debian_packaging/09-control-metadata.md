# Ch 9 — DEBIAN/control 與 metadata

> 目標：完整理解 control 檔的每個欄位，知道哪些是必填、哪些選填，以及每個欄位如何影響 dpkg 的行為。

## control 檔全欄位解析

```
Package: mytool
Version: 1.2.3-1
Architecture: amd64
Maintainer: Your Name <you@example.com>
Installed-Size: 1024
Depends: libc6 (>= 2.17), libssl3 (>= 3.0.0)
Recommends: curl
Suggests: jq
Conflicts: old-mytool
Replaces: old-mytool
Breaks: mytool-data (<< 1.2.0)
Provides: mytool-backend
Pre-Depends: dpkg (>= 1.15.7.2)
Section: utils
Priority: optional
Homepage: https://example.com/mytool
Description: A brief single-line description
 A longer multi-line description starts here.
 Each continuation line begins with a space.
 .
 An empty line in the long description is a blank paragraph separator.
```

### 必填欄位

| 欄位 | 說明 |
|-----|-----|
| `Package` | 套件名稱（小寫，只能有字母/數字/加號/減號/句點） |
| `Version` | 版本號（格式：`[epoch:]upstream-version[-debian-revision]`） |
| `Architecture` | 目標架構：`amd64`、`arm64`、`all`（架構無關，如純腳本） |
| `Maintainer` | 維護者姓名和 email |
| `Description` | 第一行是短描述（< 80 字元），後面縮排一格是長描述 |

### 選填但重要的欄位

| 欄位 | 說明 |
|-----|-----|
| `Installed-Size` | 安裝後佔用磁碟空間（KB），由 dpkg 顯示用 |
| `Section` | 分類（`utils`、`net`、`devel`、`python`...） |
| `Priority` | `required`/`important`/`standard`/`optional`/`extra` |
| `Homepage` | 上游專案網址 |

## 版本號格式詳解

```
格式：[epoch:]upstream_version[-debian_revision]

範例：
  7.81.0-1ubuntu1.15
  │     │└──────┘
  │     │  Debian/Ubuntu 修訂版（patch 號）
  │     └─ 分隔符
  └── 上游版本

  2:7.81.0-1
  │ └──────┘
  │   Epoch:UpstreamVersion
  └── Epoch（當上游重置版本號時用）

比較規則（dpkg --compare-versions）：
  epoch 最大 > upstream（字母數字混合比較）> debian_revision
```

```bash
# 手動比較版本號
dpkg --compare-versions "1.2.3" lt "1.2.4" && echo "older"
dpkg --compare-versions "2:1.0" gt "1.99" && echo "epoch wins"
```

Epoch 的典型用法：MySQL 某個時間點把版本號從 5.x 改成 8.x，跳了很大。如果上游之後又從 1.0 重新開始，需要 epoch 讓 apt 知道新的 1.0 其實比舊的 5.x 新。

## Architecture 的選擇

| 值 | 意義 |
|---|-----|
| `amd64` | 64-bit x86 |
| `arm64` | 64-bit ARM |
| `armhf` | 32-bit ARM (hard float) |
| `i386` | 32-bit x86 |
| `all` | 架構無關（Python 腳本、文件、字型） |
| `any` | 只在 source package 的 control 中使用 |

腳本、設定檔、純文字資料用 `all`，binary 用特定架構。

## Description 格式規則

```
Description: 單行短描述（不要以大寫開頭，除非是專有名詞）
 第一行長描述（有一個空格縮排）
 繼續的行也要一個空格縮排
 .
 空的段落分隔用只有一個點的行（.）
 .
 不要在長描述中用 tab
```

```bash
# lintian 會檢查 description 格式
lintian --check my-package.deb 2>&1 | grep description
```

## conffiles：設定檔的特殊處理

`DEBIAN/conffiles` 列出哪些是設定檔：

```
# DEBIAN/conffiles
/etc/mytool/config.yaml
/etc/mytool/rules.d/default.conf
```

當套件升級時，dpkg 對 conffiles 的處理特別小心：
- 如果使用者改過設定檔，dpkg 不會直接覆蓋
- 而是問使用者要保留哪個版本（或自動選一個）

這就是為什麼 `apt purge` 和 `apt remove` 行為不同——`purge` 才會刪 conffiles。

## md5sums：檔案完整性

`DEBIAN/md5sums` 記錄每個安裝檔案的 MD5：

```
d8e8fca2dc0f896fd7cb4cb0031ba249  usr/bin/mytool
45b301745c6d8537f0a6e8a82f02c5d8  usr/share/man/man1/mytool.1.gz
```

`dpkg --verify` 用它來偵測被篡改的套件：

```bash
sudo dpkg --verify curl
# 沒有輸出 = 正常
# 有輸出如 ??5?????? c /etc/nginx/nginx.conf = 設定檔被修改（正常）
```

## 自我檢核

- [ ] 必填：`Package`、`Version`、`Architecture`、`Maintainer`、`Description`
- [ ] `Architecture: all` 用於架構無關套件（腳本/文件）
- [ ] 版本格式：`[epoch:]upstream[-debian]`；epoch 解決上游版本號重置問題
- [ ] `conffiles` 列出的設定檔升級時不會直接覆蓋（dpkg 會詢問）
- [ ] `md5sums` 供 `dpkg --verify` 偵測檔案篡改

→ [Ch 10 依賴關係系統](./10-dependency-system.md)
