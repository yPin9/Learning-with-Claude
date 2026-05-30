# Ch 18 — Multi-arch 支援

> **目標**：理解 Multi-Arch 解決的問題——讓不同架構的 library 在同一系統共存（如 amd64 系統跑 i386 程式）、multiarch 路徑（`/usr/lib/<triplet>/`）的設計、`Multi-Arch: same/foreign/allowed` 的語意，以及如何正確標記套件。

> **環境**：dpkg 1.21.x。Multi-Arch 自 dpkg 1.16（2011）引入，現已是 library 打包的標準。

## 為什麼需要 Multi-arch？

設想一個 amd64 系統，但你要跑一個只有 i386 版本的舊程式（如某個閉源遊戲、Wine 跑 32-bit Windows 程式）。這個 i386 程式需要 i386 版本的 libc、libssl 等 library。

舊時代（Multi-Arch 之前）的解法很醜：把所有 32-bit library 塞進 `/usr/lib32/`，用 `ia32-libs` 這種「大雜燴套件」打包一堆 32-bit library。維護惡夢，無法用正常的依賴管理。

Multi-Arch 的解法優雅得多：**讓同一個 library 套件的 amd64 版和 i386 版能同時安裝**，各自的檔案放在不重疊的路徑。i386 程式找 i386 的 library，amd64 程式找 amd64 的，井水不犯河水。

```
目標：在 amd64 系統上同時裝
  libssl3:amd64   （給 64-bit 程式）
  libssl3:i386    （給 32-bit 程式）
兩者不衝突，各放各的路徑
```

## 先建立直覺：用路徑隔離不同架構

```
Multi-Arch 之前：library 都在 /usr/lib/
  /usr/lib/libssl.so.3   ← 只能放一個架構，無法共存

Multi-Arch 之後：每個架構有自己的子目錄（用 triplet 命名）
  /usr/lib/x86_64-linux-gnu/libssl.so.3    ← amd64 版
  /usr/lib/i386-linux-gnu/libssl.so.3      ← i386 版
  /usr/lib/aarch64-linux-gnu/libssl.so.3   ← arm64 版
              ─────────┬─────────
              multiarch triplet（架構的唯一識別）

不同架構的同名 library 放不同目錄 → 可共存
```

multiarch triplet（`x86_64-linux-gnu`）是架構的唯一標識（CPU-kernel-userland）。library 放進對應 triplet 的目錄，就不會互相覆蓋。

```bash
# 看你系統的 triplet
dpkg-architecture -qDEB_HOST_MULTIARCH
# x86_64-linux-gnu

# library 的現代路徑
ls /usr/lib/x86_64-linux-gnu/ | head
# libssl.so.3  libcrypto.so.3  libc.so.6  ...
```

## 啟用 foreign architecture

要在 amd64 系統裝 i386 套件，先告訴 dpkg 接受 i386：

```bash
# 加入 i386 架構
sudo dpkg --add-architecture i386
sudo apt update

# 現在可以裝 i386 版的 library
sudo apt install libssl3:i386
#                          ────
#                          架構限定（不寫預設是 native）

# 看系統接受哪些架構
dpkg --print-architecture            # native: amd64
dpkg --print-foreign-architectures   # i386
```

`pkg:arch` 語法（`libssl3:i386`）明確指定架構。不寫架構時用 native。

## Multi-Arch 欄位：三個值

`debian/control` 的 binary stanza 用 `Multi-Arch:` 宣告這個套件如何參與 multiarch。三個值：

```
Multi-Arch: same     → 這個套件可以多架構共存（library 套件用這個）
Multi-Arch: foreign  → 這個套件能滿足「任何架構」的依賴（架構無關的工具）
Multi-Arch: allowed  → 依賴方可以選擇要 native 還是特定架構（特殊情況）
（不寫）             → 預設，不參與 multiarch（同時只能裝一個架構）
```

### Multi-Arch: same — library 套件

```
Package: libgreet1
Architecture: any
Multi-Arch: same        ← amd64 版和 i386 版可共存
Depends: ${shlibs:Depends}, ${misc:Depends}
```

`same` 的意思：「我的不同架構版本可以同時安裝」。**前提是這個套件的所有檔案都在 multiarch 路徑**（`/usr/lib/<triplet>/`），沒有架構間會衝突的檔案。

> `Multi-Arch: same` 有個嚴格要求：**架構無關的檔案（如文件）在不同架構版本必須 byte-for-byte 相同**。如果 amd64 版和 i386 版的某個檔案內容不同（如 changelog 含架構名），dpkg 會在同時安裝時報衝突。這是最常見的 multiarch 陷阱。

### Multi-Arch: foreign — 架構無關的工具

```
Package: greet-data
Architecture: all
Multi-Arch: foreign     ← 我能滿足任何架構的依賴
```

`foreign` 用於「架構無關、能服務任何架構」的套件。例如一個純資料套件或純 script 工具，i386 程式依賴它時，裝 amd64 版（或 all）就能滿足——因為它和架構無關。

典型場景：一個 `Architecture: all` 的工具被不同架構的套件依賴，標 `foreign` 讓它能滿足跨架構依賴。

### Multi-Arch: allowed — 讓依賴方選擇

```
Package: some-tool
Architecture: any
Multi-Arch: allowed     ← 依賴我的套件可以指定要哪個架構
```

`allowed` 較少用，給「既可能被當原生工具用、也可能被特定架構需要」的套件。依賴方用 `some-tool:any` 表示「任何架構都行」，或 `some-tool`（native）。

## dev 套件與 Multi-Arch

dev 套件（headers）的 Multi-Arch 處理有講究：

```
Package: libgreet-dev
Architecture: any
Multi-Arch: same         ← 如果 headers 在 multiarch 路徑
Depends: libgreet1 (= ${binary:Version}), ${misc:Depends}
```

但 headers 通常在 `/usr/include/`（**不是** multiarch 路徑）——這就有問題：amd64 和 i386 的 dev 套件都想放 `/usr/include/greet.h`，衝突。

解法分情況：
- header 完全架構無關（大部分情況）→ 放 `/usr/include/`，dev 套件**不標** `Multi-Arch: same`（同時只裝一個架構的 dev，通常夠用）
- header 含架構相關內容（如 `<bits/>` 風格的架構特定 header）→ 放 multiarch include 路徑 `/usr/include/<triplet>/`，標 `Multi-Arch: same`

> 多數 dev 套件的 header 架構無關，放 `/usr/include/` 不標 same 即可。只有像 glibc 這種含架構特定 header 的才需要 multiarch include 路徑。練習 B 的 `libgreet-dev` 標 same 其實在 header 衝突上是有瑕疵的——嚴格說該檢查 header 是否真的可共存。

## 故意弄壞：Multi-Arch: same 但檔案不一致

```bash
# libgreet1 標了 Multi-Arch: same，但它裝了一個架構相關的檔案到非 multiarch 路徑
# 例如 /usr/share/doc/libgreet1/build-arch.txt 內容含架構名

# 裝 amd64 版
sudo apt install libgreet1:amd64
# 再裝 i386 版
sudo apt install libgreet1:i386
# dpkg: error processing archive libgreet1_1.0-1_i386.deb:
#  '/usr/share/doc/libgreet1/build-arch.txt' is different from the
#  same file on the system (from libgreet1:amd64)
# ↑ Multi-Arch: same 要求共享檔案完全一致！
```

`Multi-Arch: same` 的核心約束：**所有不在 multiarch 路徑的檔案（被多架構共享的）必須完全相同**。任何架構差異（連 timestamp、嵌入的架構名）都會導致同時安裝失敗。這是 multiarch 最微妙的雷。

修正：確保架構相關的東西只放 multiarch 路徑；共享檔案（doc、man）保持架構無關（reproducible builds 在這裡又幫上忙——固定 timestamp 讓檔案一致）。

## 踩雷集錦

1. **`Multi-Arch: same` 但有架構相關的共享檔案**：最常見的雷。共享路徑的檔案在不同架構版本必須 byte-for-byte 相同，否則同時安裝衝突。檢查 doc、changelog 是否含架構特定內容

2. **library 沒裝到 multiarch 路徑**：標 `Multi-Arch: same` 但 library 放 `/usr/lib/`（非 multiarch），不同架構會覆蓋。library 必須在 `/usr/lib/<triplet>/`

3. **dev 套件亂標 same 導致 header 衝突**：header 在 `/usr/include/`（非 multiarch）卻標 `same`，amd64 和 i386 的 dev 同時裝會衝突 `greet.h`。多數 dev 套件不該標 same

4. **混淆 foreign 和 same**：`same`= library（同套件多架構共存）；`foreign`= 架構無關工具（能滿足任何架構的依賴）。用錯導致依賴解析錯誤

5. **以為 Multi-Arch 是給交叉編譯的**：Multi-Arch 主要解決「同系統跑多架構程式」（如 amd64 跑 i386）。它也**支援**交叉編譯（Ch 13），但兩者是不同問題

## 進階：Multi-Arch 如何支援交叉編譯

Multi-Arch 的 multiarch 路徑同時是交叉編譯（Ch 13）的基礎：

```
交叉編譯 arm64 套件（在 amd64 機器）：
  需要 arm64 的 library 來連結
        │
  Multi-Arch 讓你裝 arm64 版的 library：
  sudo dpkg --add-architecture arm64
  sudo apt install libssl-dev:arm64
        │
  arm64 的 library 裝在 /usr/lib/aarch64-linux-gnu/
  （和你的 amd64 library 共存，不衝突）
        │
  交叉編譯器連結這個路徑的 arm64 library
```

沒有 Multi-Arch，交叉編譯要的 host 架構 library 無處安放（會和 build 架構的衝突）。Multi-Arch 的路徑隔離讓「host 架構的 dev library」能和「build 架構的」共存，交叉編譯才可行。這就是為什麼 Ch 13 的交叉編譯依賴 Multi-Arch。

```bash
# 交叉編譯的 build 依賴標記（Ch 13 預習過）
# Build-Depends: libssl-dev   ← 預設 host 架構（交叉時要 host 版本）
#                pkg-config:native  ← 工具要 build 架構
```

## 動手練習

1. 看你系統的 multiarch 結構：`ls /usr/lib/$(dpkg-architecture -qDEB_HOST_MULTIARCH)/ | head`，理解 library 放在 triplet 目錄

2. 在 VM 啟用 i386：`sudo dpkg --add-architecture i386 && sudo apt update`，裝一個 i386 library（`sudo apt install libssl3:i386`），看它和 amd64 版共存：`dpkg -l libssl3*`

3. 找一個 `Multi-Arch: same` 的 library 套件（如 `apt show libssl3`），確認它標了 same，檢查它的檔案是否都在 multiarch 路徑（`dpkg -L libssl3`）

4. 對練習 B 的 libgreet1，檢查它的 `Multi-Arch: same` 是否真的成立——所有檔案都在 multiarch 路徑嗎？有架構相關的共享檔案嗎？

## 本章重點整理

- Multi-Arch 讓不同架構的同名 library 套件共存（如 amd64 系統裝 i386 library 跑 32-bit 程式）
- 機制：library 放 multiarch 路徑 `/usr/lib/<triplet>/`，不同架構不重疊
- `Multi-Arch: same`（library，多架構共存）/ `foreign`（架構無關工具，滿足任何架構依賴）/ `allowed`（依賴方選擇）
- `same` 的嚴格約束：所有共享路徑的檔案必須 byte-for-byte 相同（最常見的雷）
- Multi-Arch 的路徑隔離也是交叉編譯的基礎（host 架構 library 能和 build 架構共存）

## 自我檢核

- [ ] 能解釋 Multi-Arch 解決什麼問題（同系統多架構 library 共存）
- [ ] 知道 multiarch triplet 是什麼，library 為什麼放 `/usr/lib/<triplet>/`
- [ ] 能說出 `Multi-Arch: same` 和 `foreign` 的差別，各用於什麼套件
- [ ] 知道 `Multi-Arch: same` 對共享檔案的嚴格要求（byte-for-byte 相同）
- [ ] 能解釋 Multi-Arch 如何讓交叉編譯成為可能

## 延伸閱讀

### 官方文件

- **[Debian Wiki: Multiarch/HOWTO](https://wiki.debian.org/Multiarch/HOWTO)**
  - **讀哪裡**：整頁，特別是 `Multi-Arch` 欄位值的解釋和常見場景
  - **學什麼**：每個 Multi-Arch 值的使用時機、實戰案例；本章是教學版
  - **前提**：讀完本章

- **[Debian Wiki: Multiarch/Implementation](https://wiki.debian.org/Multiarch/Implementation)**
  - **讀哪裡**：path 和 dpkg 行為那節
  - **學什麼**：multiarch 路徑的完整規則、dpkg 如何處理同時安裝
  - **前提**：本章

### 部落格 / 文章

- **[The Multiarch specification](https://wiki.ubuntu.com/MultiarchSpec)** — Ubuntu（Multi-Arch 的原始設計）
  - **這篇說什麼**：Multi-Arch 的設計動機和原始規格（Ubuntu 主導設計，Debian 採用）
  - **讀哪裡**：rationale 和 package metadata 那節
  - **為什麼值得讀**：理解 Multi-Arch 的設計思路，為什麼選路徑隔離而非別的方案

→ [Ch 19 符號管理與 ABI 追蹤](./19-symbols-abi.md)
