# Ch 10 — 依賴關係系統

> 目標：完整理解 Debian 依賴關係的七種類型，看懂複雜的依賴表達式，知道為什麼依賴地獄發生以及 APT 如何解決。

## 七種依賴關係

```
Package: mytool
Depends: libc6, libssl3
Recommends: curl
Suggests: jq
Conflicts: old-mytool
Replaces: old-mytool
Breaks: mytool-data (<< 2.0)
Pre-Depends: dpkg (>= 1.17)
Provides: mytool-backend
```

| 欄位 | 安裝時行為 | 是否必須 |
|-----|----------|---------|
| `Depends` | 必須安裝才能設定（configure）本套件 | ✓ |
| `Pre-Depends` | 必須在**解包前**就完成安裝 | ✓ |
| `Recommends` | 預設也裝，但沒有也能跑 | 選配 |
| `Suggests` | 不自動裝，只是建議 | 選配 |
| `Conflicts` | 不能同時安裝 | — |
| `Replaces` | 可以覆蓋對方的檔案 | — |
| `Breaks` | 安裝後會破壞對方功能（不能同時存在） | — |
| `Provides` | 宣告自己提供某個虛擬套件名稱 | — |

## Depends 表達式語法

```
Depends: 套件名稱 [(版本約束)]

版本比較運算子：
  <<   嚴格小於
  <=   小於等於
  =    等於
  >=   大於等於
  >>   嚴格大於

多個套件：逗號分隔（AND）
替代套件：豎線分隔（OR）

範例：
Depends: libc6 (>= 2.17), libssl3 (>= 3.0.0)
         ────────────────  ──────────────────
         AND               AND

Depends: libcurl4-openssl-dev | libcurl4-gnutls-dev
         ─────────────────────────────────────────
         OR（任一個都行）

Depends: python3 (>= 3.8), python3-requests, python3-yaml | pyyaml
```

## Pre-Depends 的特殊性

一般 `Depends` 只要求在設定（configure）本套件**之前**安裝好依賴。但解包（unpacking）可以在依賴安裝前就進行。

`Pre-Depends` 要求：依賴必須在本套件**解包之前**就完整安裝好。

只有在套件安裝腳本（preinst）需要依賴的功能時才用 `Pre-Depends`。過度使用 `Pre-Depends` 會讓依賴解算更複雜，Debian Policy 建議只在必要時使用。

## Conflicts vs Breaks

```
Conflicts: old-mytool
```
不能和 `old-mytool` 同時安裝。安裝 `mytool` 會先移除 `old-mytool`。

```
Breaks: mytool-data (<< 2.0)
```
安裝 `mytool` 後，`mytool-data` 版本 < 2.0 的功能會壞掉。APT 會在安裝時同步升級 `mytool-data`。

差別：`Conflicts` 完全不相容（移除其中一個）；`Breaks` 是功能依賴（強制升級對方）。

## Replaces 的用途

```
Replaces: old-mytool
Conflicts: old-mytool
```

`Replaces` 允許本套件覆蓋另一個套件的檔案。通常和 `Conflicts` 配合，用在套件改名的情境：

- `old-mytool` 改名成 `mytool`
- `mytool` 宣告 `Conflicts: old-mytool, Replaces: old-mytool`
- APT 安裝 `mytool` 時自動移除 `old-mytool`，即使有相同的檔案路徑

## Provides：虛擬套件

```
Package: curl
Provides: www-client
```

```
Package: wget
Provides: www-client
```

```
Package: myapp
Depends: www-client
```

`myapp` 依賴的是「任何一個 www-client」，curl 或 wget 都滿足。這是讓依賴更靈活的機制。

```bash
# 查看哪些套件 Provides www-client
apt-cache showpkg www-client
# 或
apt-cache search www-client --names-only
```

## 依賴地獄的成因

```
A depends on libfoo = 1.0
B depends on libfoo = 2.0
你想同時裝 A 和 B → 衝突
```

Debian 的解法：
1. **允許多版本共存**（ABI 相容才可能）：`libfoo1`（1.x）和 `libfoo2`（2.x）同時存在
2. **multiarch**：同一個套件的不同架構版本可以共存（`libfoo:amd64` + `libfoo:arm64`）
3. **虛擬環境**（Python venv、Docker）：在隔離環境處理

## 看真實套件的依賴樹

```bash
# 看 curl 的依賴
apt-cache depends curl

# 看遞迴依賴（apt-rdepends 需要另外安裝）
sudo apt install apt-rdepends
apt-rdepends curl | head -30

# 反查：誰依賴 libcurl4
apt-cache rdepends libcurl4 | head -20
```

```bash
$ apt-cache depends curl
curl
  Depends: libc6
  Depends: libcurl4
  Suggests: libcurl4-doc
  Suggests: libcurl4-nss-dev
  Suggests: libcurl4-openssl-dev
  Suggests: libcurl4-gnutls-dev
  Conflicts: curl-ssl
  Replaces: curl-ssl
```

## 自我檢核

- [ ] `Depends`（必要）vs `Recommends`（預設裝但非必要）vs `Suggests`（不自動裝）
- [ ] 版本約束：`<<`（嚴格小於）、`>>`（嚴格大於）、`=`、`>=`、`<=`
- [ ] OR 依賴用 `|`（任一個都滿足）；AND 依賴用 `,`
- [ ] `Provides` 宣告虛擬套件，讓依賴更靈活
- [ ] `Conflicts` = 完全不相容（移除其中一個）；`Breaks` = 功能衝突（強制升級對方）

→ [Ch 11 APT 快取與本地儲存](./11-apt-cache.md)
