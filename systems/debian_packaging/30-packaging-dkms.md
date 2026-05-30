# Ch 30 — 打包 kernel module（DKMS）

> **目標**：理解 out-of-tree kernel module 打包的根本挑戰——module 必須對應執行中的 kernel 版本、DKMS 如何在 kernel 升級時自動重編 module、`dkms.conf` 的結構、以及 `dh-dkms` 的用法。

> **環境**：dkms、dh-dkms、Debian 12（kernel 6.1）。本章假設你有基本 C 和 kernel module 概念（`.ko` 是什麼）。

## 為什麼 kernel module 打包這麼特殊？

普通套件裝完就能用。kernel module 不行——它有個致命約束：

```
kernel module 的根本問題：
  module（.ko）必須對應「精確的 kernel 版本」編譯
        │
  kernel 6.1.0-1 編的 .ko 不能用在 kernel 6.1.0-2
  （kernel ABI 在版本間不保證穩定）
        │
  使用者升級 kernel 後，舊的 .ko 失效
        │
  → 怎麼辦？每次 kernel 升級都要重新編 module
```

兩種解法：
- **預編譯 module 套件**：為每個 kernel 版本各編一個 `.ko` 套件。問題：kernel 版本太多，無法窮舉
- **DKMS（Dynamic Kernel Module Support）**：套件裝的是 module 的**source**，在使用者機器上、kernel 升級時**自動重編**

DKMS 是 out-of-tree module（不在 kernel source tree 裡的第三方 module，如顯卡驅動、VirtualBox 模組）的標準解法。

## 先建立直覺：DKMS 是「裝 source，自動重編」

```
普通套件：裝編譯好的產物
DKMS 套件：裝 source + 編譯指令，在目標機器按需編譯

  套件裝 module source 到 /usr/src/mymod-1.0/
        │
  dkms 註冊這個 module
        │
  dkms 為「當前 kernel」編譯 .ko，裝到 /lib/modules/<kernel>/
        │
  ── 使用者升級 kernel 到新版本 ──
        │
  kernel 套件的 hook 觸發 dkms autoinstall
        │
  dkms 自動為「新 kernel」重新編譯 module source
        │
  → 新 kernel 也有可用的 .ko，使用者無感
```

DKMS 的核心價值：**一次打包 source，自動適配所有未來的 kernel 版本**。使用者升級 kernel，module 自動重編，不用你為每個 kernel 版本發套件。

## dkms.conf：DKMS 的配置

DKMS module 的核心是 `dkms.conf`，告訴 dkms 如何編譯和安裝：

```
PACKAGE_NAME="mymod"
PACKAGE_VERSION="1.0"

# 怎麼編譯（在 module source 目錄執行）
MAKE[0]="make -C ${kernel_source_dir} M=${dkms_tree}/${PACKAGE_NAME}/${PACKAGE_VERSION}/build modules"
CLEAN="make -C ${kernel_source_dir} M=${dkms_tree}/${PACKAGE_NAME}/${PACKAGE_VERSION}/build clean"

# 編出的 .ko 叫什麼、裝到哪
BUILT_MODULE_NAME[0]="mymod"
DEST_MODULE_LOCATION[0]="/updates/dkms"

# kernel 升級時自動重編
AUTOINSTALL="yes"
```

| 變數 | 意義 |
|---|---|
| `PACKAGE_NAME` / `_VERSION` | module 的識別 |
| `MAKE[0]` | 編譯指令（用 `${kernel_source_dir}` 指向當前 kernel 的 build 環境）|
| `BUILT_MODULE_NAME[0]` | 編出的 `.ko` 名稱 |
| `DEST_MODULE_LOCATION[0]` | 裝到 `/lib/modules/<kernel>/` 的哪個子目錄 |
| `AUTOINSTALL` | `yes` = kernel 升級時自動重編 |

`${kernel_source_dir}` 等變數由 dkms 在編譯時填入「當前 kernel」的對應值——這就是 DKMS 能適配任意 kernel 的關鍵。

## 用 dh-dkms 打包

`dh-dkms` 簡化 DKMS module 的 Debian 打包：

`debian/control`：
```
Source: mymod
Section: kernel
Priority: optional
Build-Depends: debhelper-compat (= 13), dh-dkms
Standards-Version: 4.6.2
Rules-Requires-Root: no

Package: mymod-dkms
Architecture: all
Depends:
 ${misc:Depends},
 dkms,
Description: my kernel module (DKMS)
 Source for the mymod kernel module, built automatically via DKMS
 for the running kernel.
```

關鍵：
- 套件名 `-dkms` 後綴（慣例）
- `Architecture: all`——裝的是 **source**，架構無關！（在目標機器才編成架構相關的 `.ko`）
- `Depends: dkms`——需要 dkms 框架
- `Build-Depends: dh-dkms`

`debian/rules`：
```makefile
#!/usr/bin/make -f
%:
	dh $@ --with dkms
```

`debian/mymod-dkms.dkms`（指向 dkms.conf）：
```
debian/dkms.conf
```

dh-dkms 處理：把 source 裝到 `/usr/src/mymod-1.0/`、註冊 dkms、在 postinst 觸發首次編譯、postrm 清理。

## 為什麼 mymod-dkms 是 Architecture: all

這是最反直覺的點：kernel module 是極度架構相關的（`.ko` 是 binary），但 DKMS 套件卻是 `Architecture: all`。

```
原因：DKMS 套件裝的不是 .ko，是「source + dkms.conf」
        │
  source 是架構無關的 C 程式碼（.c/.h）
        │
  真正架構相關的 .ko 是在「使用者的機器上」用「使用者的 kernel」編譯的
        │
  → 套件本身（source）是 all，編譯產物（.ko）不在套件裡
```

> 這顛覆了「kernel module = 架構相關」的直覺。記住：DKMS 套件 = source 容器，`.ko` 是 runtime 在目標機器生成的。所以 `Architecture: all`——一份 source 套件，在任何架構、任何 kernel 上各自編出對應的 `.ko`。

## kernel header 依賴

DKMS 編譯 module 需要對應 kernel 的 headers：

```bash
# DKMS 編譯需要 kernel headers（提供 kernel build 環境）
sudo apt install linux-headers-$(uname -r)
# 或 meta 套件（自動跟 kernel 升級）
sudo apt install linux-headers-amd64
```

> DKMS 套件本身**不**直接 Depends kernel headers（因為 header 套件名含 kernel 版本，會變）。慣例是依賴 `dkms`（dkms 會檢查 header 是否存在）並在文件說明使用者需要裝對應的 `linux-headers-*`。或依賴 meta 套件 `linux-headers-amd64`（跟著 kernel 升級）。

## 安裝後 DKMS 的運作

```bash
# 裝 DKMS 套件後，dkms 自動為當前 kernel 編譯
sudo dpkg -i mymod-dkms_1.0_all.deb
# Loading new mymod-1.0 DKMS files...
# Building for 6.1.0-1-amd64
# Building initial module for 6.1.0-1-amd64
# Done.

# 查看 dkms 狀態
dkms status
# mymod/1.0, 6.1.0-1-amd64, x86_64: installed

# 升級 kernel 後，dkms 自動為新 kernel 重編（透過 kernel 套件的 hook）
# 不用使用者手動做任何事
```

kernel 套件（`linux-image-*`）裝了 dkms 的 hook（`/etc/kernel/postinst.d/dkms`）——新 kernel 裝好後自動觸發 `dkms autoinstall`，為新 kernel 編所有 AUTOINSTALL 的 module。

## 故意弄壞：標 Architecture: any / 缺 kernel headers

```bash
# 錯誤一：標 Architecture: any
# Package: mymod-dkms
# Architecture: any        ← 錯！DKMS 裝的是 source（all）
# 後果：build farm 為每架構 build 相同的 source（浪費），且概念錯誤

# 錯誤二：使用者沒裝 kernel headers
sudo dpkg -i mymod-dkms_1.0_all.deb
# Error! Your kernel headers for kernel 6.1.0-1-amd64 cannot be found.
# Please install the linux-headers-6.1.0-1-amd64 package
# → DKMS 編譯需要 headers 提供 kernel build 環境
```

教訓：DKMS 套件 `Architecture: all`（source 容器）；編譯需要 kernel headers（要引導使用者安裝或依賴 meta 套件）。

## 踩雷集錦

1. **標 `Architecture: any`**：DKMS 裝 source（架構無關），是 `all`。`.ko` 在目標機器編，不在套件裡

2. **`dkms.conf` 的 MAKE 指令路徑錯**：`${kernel_source_dir}`、`${dkms_tree}` 等變數要正確使用，否則 dkms 編譯失敗。照標準範本改

3. **缺 kernel headers 導致編譯失敗**：DKMS 需要對應 kernel 的 headers。引導使用者裝 `linux-headers-$(uname -r)` 或依賴 meta 套件

4. **AUTOINSTALL 沒設 yes**：kernel 升級後 module 不會自動重編，使用者升 kernel 後 module 失效卻不知為何。設 `AUTOINSTALL="yes"`

5. **module 編譯有 kernel 版本相關的 #ifdef 沒處理**：kernel API 在版本間會變。out-of-tree module 常要用 `LINUX_VERSION_CODE` 的 #ifdef 適配多個 kernel 版本。這是 module source 本身的事，但打包者要知道（不同 kernel 版本可能編譯失敗）

6. **簽署問題（Secure Boot）**：開了 Secure Boot 的系統，未簽署的 module 無法載入。DKMS 能配合 MOK（Machine Owner Key）簽署，但這是進階設定（見下）

## 進階：Secure Boot 與 module 簽署

開啟 Secure Boot 的系統，kernel 只載入**簽署過**的 module。DKMS 編出的 out-of-tree module 預設未簽署，會被拒絕載入：

```bash
# Secure Boot 開啟時，未簽署 module 載入失敗
sudo modprobe mymod
# modprobe: ERROR: could not insert 'mymod': Key was rejected by service
```

DKMS 支援用 **MOK（Machine Owner Key）** 簽署：

```
DKMS + Secure Boot 的流程：
  1. 生成一把 MOK（machine owner key）
  2. 用 mokutil 註冊這把 key 到 UEFI（要重開機確認）
  3. DKMS 編譯 module 後用這把 key 簽署
  4. kernel 信任 MOK 簽的 module → 能載入
```

```bash
# Debian 的 dkms 能配合 /etc/dkms/framework.conf 設定簽署
# mok_signing_key / mok_certificate 指向你的 MOK
```

> Secure Boot + 第三方 module 是真實的痛點。商業驅動（如 NVIDIA）的 DKMS 套件要處理 MOK 簽署流程，否則使用者開了 Secure Boot 就用不了。這是 out-of-tree module 在現代系統的額外複雜度。理解它存在，遇到「module 載入被拒」才知道往簽署方向查。

## 動手練習

1. 寫一個最小的 "hello world" kernel module（一個 `.c` + Makefile），打包成 DKMS 套件（dkms.conf + dh-dkms），在 VM（裝了 linux-headers）安裝，確認 dkms 自動編譯（`dkms status`）

2. 測試 kernel 升級的自動重編：（在 VM）裝 DKMS module 後升級 kernel，重開機，確認 dkms 為新 kernel 自動重編了 module

3. 看真實 DKMS 套件：`apt show` 一個 `-dkms` 套件（如 `virtualbox-dkms` 或 `zfs-dkms`），確認它 `Architecture: all`、Depends dkms

4. 故意弄壞：標 `Architecture: any` build 看問題；移除 kernel headers 裝 DKMS 套件看編譯失敗的錯誤

## 本章重點整理

- kernel module（`.ko`）必須對應精確 kernel 版本；DKMS 解法是「裝 source，kernel 升級時自動重編」
- DKMS 套件 `Architecture: all`（裝的是架構無關的 source，`.ko` 在目標機器編）——反直覺但關鍵
- `dkms.conf` 配置編譯（MAKE）和安裝（DEST），`AUTOINSTALL="yes"` 讓 kernel 升級自動重編
- `dh-dkms` 簡化打包；編譯需要 kernel headers（引導使用者裝或依賴 meta 套件）
- Secure Boot 系統需要 MOK 簽署 module，否則載入被拒（進階痛點）

## 自我檢核

- [ ] 能解釋為什麼 kernel module 打包特殊（.ko 綁定 kernel 版本）
- [ ] 知道 DKMS 的核心機制（裝 source、kernel 升級自動重編）
- [ ] 能解釋為什麼 DKMS 套件是 `Architecture: all`（裝 source 不裝 .ko）
- [ ] 知道 DKMS 編譯需要什麼（kernel headers）
- [ ] 知道 Secure Boot 對 out-of-tree module 的影響（需要 MOK 簽署）

## 延伸閱讀

### 官方文件

- **[dkms(8) man page](https://manpages.debian.org/bookworm/dkms/dkms.8.html)**
  - **讀哪裡**:「dkms.conf」格式和 commands（add/build/install/autoinstall）
  - **學什麼**：dkms.conf 的所有變數、dkms 的完整指令；本章是教學版
  - **前提**：讀完本章

- **[Debian Wiki: KernelDKMS](https://wiki.debian.org/KernelDKMS)**
  - **讀哪裡**：打包流程和 dh-dkms 用法
  - **學什麼**：Debian 的 DKMS 打包慣例
  - **前提**：本章

### 部落格 / 文章

- **[DKMS: Dynamic Kernel Module Support (原始論文/文件)](https://github.com/dell/dkms)** — Dell（DKMS 原作者）
  - **這篇說什麼**：DKMS 的設計動機和機制（Dell 為了管理伺服器驅動而開發）
  - **讀哪裡**：README 的 overview 和 dkms.conf 範例
  - **為什麼值得讀**：理解 DKMS 為什麼這樣設計，來自原作者

→ [練習 D：含 service + library 的完整專案](./practice-d-full-project.md)
