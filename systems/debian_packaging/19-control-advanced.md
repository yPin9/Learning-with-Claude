# Ch 19 — control 進階

> 目標：掌握 Build-Depends 的完整語法、Architecture 限制、多架構支援（multiarch）、以及 Epoch 的正確使用時機。

## Build-Depends vs Depends

| 欄位 | 用途 | 在哪個套件使用 |
|-----|-----|-------------|
| `Build-Depends` | 編譯時需要的套件 | Source 段落 |
| `Build-Depends-Indep` | 架構無關套件 build 時需要 | Source 段落 |
| `Depends` | 執行時需要的套件 | Package 段落 |

```
Source: myproject
Build-Depends: debhelper-compat (= 13),
               gcc (>= 10),
               libssl-dev (>= 3.0),
               python3:native,           ← :native 表示 build 機的架構
               cmake (>= 3.16) | ninja-build
Build-Depends-Indep: doxygen, graphviz   ← 只有架構無關套件需要

Package: myproject
Depends: libssl3 (>= 3.0.0),
         ${shlibs:Depends},
         ${misc:Depends}
```

## Architecture 欄位的細節

### Source 的 Architecture 設定

Source 段落不設定 `Architecture`，只有 Package 段落設定。

### Package 的 Architecture 值

```
Architecture: any
```
- `any`：對所有架構編譯（產生特定架構的 binary）
- `all`：架構無關（同一個 .deb 所有架構通用）
- `amd64 arm64`：只在特定架構上 build

```
Architecture: amd64 arm64 armhf
```

### Architecture 限制在依賴中

只在特定架構上依賴某個套件：

```
Depends: libfoo [amd64 arm64],
         libbar [!armhf],          ← 除了 armhf 以外都依賴
         ${shlibs:Depends}
```

## 多架構支援（Multiarch）

```
Package: libssl3
Architecture: any
Multi-Arch: same
```

| Multi-Arch 值 | 意義 |
|-------------|-----|
| `same` | 可以同時裝多個架構（如 `libssl3:amd64` + `libssl3:arm64`） |
| `foreign` | 可以被其他架構的套件依賴（如 amd64 上裝 i386 的 libfoo） |
| `allowed` | 套件本身允許多架構，但依賴不強制 |

```bash
# 啟用 i386 架構（在 amd64 機器上跑 32-bit 程式）
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install libssl3:i386

# 查看已啟用的架構
dpkg --print-architecture          # 原生架構（amd64）
dpkg --print-foreign-architectures # 外來架構（i386）
```

## Epoch 的正確使用

```
Version: 2:1.0-1
```

Epoch（冒號前的數字）用來解決版本號重置的問題。版本比較：`epoch` 先比，epoch 大的永遠比 epoch 小的新。

**什麼時候用 Epoch**：上游把版本號從 9.x 降回 1.x（如 MariaDB 從 5.x 到 10.x 又提了新分支）。

**不要濫用 Epoch**：一旦 epoch 設定，要增加 epoch 只能繼續增，永遠不能降。Debian Policy 說「epoch 只在別無選擇時才用」。

```bash
# 確認版本比較
dpkg --compare-versions "2:1.0" gt "9.999" && echo "epoch 2 > epoch 0"
dpkg --compare-versions "1:5.0" lt "2:1.0" && echo "epoch 1 < epoch 2"
```

## 虛擬欄位：${...} 占位符

debhelper 和 dpkg-buildpackage 支援多種占位符：

| 占位符 | 由誰填入 | 內容 |
|------|---------|-----|
| `${shlibs:Depends}` | `dh_shlibdeps` | 動態連結的 .so 依賴 |
| `${misc:Depends}` | `dh_gencontrol` | debhelper 額外的依賴 |
| `${python3:Depends}` | `dh-python` | Python 版本依賴 |
| `${binary:Version}` | dpkg-gencontrol | 同一 source 的 binary 版本 |
| `${source:Version}` | dpkg-gencontrol | source 套件版本 |

常見模式（-dev 套件依賴主套件的相同版本）：

```
Package: myproject-dev
Depends: myproject (= ${binary:Version}), ${misc:Depends}
```

## Standards-Version：宣告遵循的 Debian Policy 版本

```
Standards-Version: 4.6.2
```

這表示打包者宣稱遵循了 Debian Policy 4.6.2 的規範。`lintian` 用它來決定要檢查哪些規則。

```bash
# 查看目前最新的 Debian Policy 版本
apt-cache show debian-policy | grep Version
```

## 完整 control 範例（C 函式庫）

```
Source: libfoo
Section: libs
Priority: optional
Maintainer: Your Name <you@example.com>
Build-Depends: debhelper-compat (= 13), libssl-dev
Standards-Version: 4.6.2
Homepage: https://example.com/libfoo
Vcs-Git: https://github.com/example/libfoo.git
Vcs-Browser: https://github.com/example/libfoo

Package: libfoo1
Section: libs
Architecture: any
Multi-Arch: same
Pre-Depends: ${misc:Pre-Depends}
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: Foo library (runtime)
 The libfoo library provides foo functionality.

Package: libfoo-dev
Section: libdevel
Architecture: any
Multi-Arch: same
Depends: libfoo1 (= ${binary:Version}), ${misc:Depends}
Description: Foo library (development headers)
 Development headers for libfoo.

Package: libfoo-doc
Section: doc
Architecture: all
Description: Foo library (documentation)
 API documentation for libfoo.
```

## 自我檢核

- [ ] `Build-Depends` = 編譯時需要（Source 段落）；`Depends` = 執行時需要（Package 段落）
- [ ] `Multi-Arch: same` 允許同一套件的多個架構共存；`foreign` 允許被其他架構的套件依賴
- [ ] Epoch 解決版本號重置問題；一旦設定不能降，謹慎使用
- [ ] `${shlibs:Depends}` 由 `dh_shlibdeps` 自動填入動態庫依賴；不要手寫這些依賴
- [ ] `${binary:Version}` 讓 -dev 套件精確依賴同版本的主套件

→ [Ch 20 rules 檔與 dh_auto_*](./20-rules-file.md)
