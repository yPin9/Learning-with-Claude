# Ch 17 — autopkgtest：自動化測試

> **目標**：理解 autopkgtest（DEP-8）解決的問題——測試「**已安裝的套件**」而非「build 出的產物」、`debian/tests/control` 的語法、各種 Restrictions 的意義、以及它在 CI 和 archive 品質中的角色。

> **環境**：autopkgtest 5.x（`apt install autopkgtest`）。DEP-8 規格定義測試格式。

## 為什麼需要 autopkgtest？

lintian（Ch 16）是靜態檢查——它看套件的「形狀」對不對，但不執行套件。一個套件可能 lintian 全綠，但裝起來根本跑不動（依賴雖宣告了但版本不對、設定檔有 bug、library 連結錯誤）。

upstream 的測試（`dh_auto_test` 跑的）測的是「build 出來的東西」，在 build 環境裡跑。但這也不夠——它測的不是「**安裝到系統後**」的狀態。

autopkgtest（DEP-8）填補這個空白：**安裝套件到一個乾淨系統，然後測試它真的能用**。這是「as-installed」測試。

```
三層測試的分工：

lintian          → 套件的形狀對不對（靜態）
upstream test    → build 出的東西對不對（在 build 環境）
autopkgtest      → 裝到系統後能不能用（在乾淨的安裝環境）← 本章
```

## 先建立直覺：在乾淨系統裝起來，然後測

```
autopkgtest 的流程：

  乾淨的測試環境（VM / container / chroot）
        │
  1. apt install 你的 .deb（像真實使用者那樣裝）
        │
  2. 跑 debian/tests/ 裡的測試
        │  測試以「已安裝的套件」為前提
        │  （用 /usr/bin/greet，不是 build 目錄的 ./greet）
        ▼
  3. 回報每個測試 pass/fail
        │
  測試環境銷毀
```

關鍵差異：autopkgtest 用的是**裝到 /usr 的套件**，不是 build 目錄的產物。這才能驗證「使用者 apt install 後真的能用」。

## debian/tests/control：測試宣告

測試定義在 `debian/tests/control`（DEP-8 格式）：

```
Tests: smoke-test
Depends: @
Restrictions: needs-root

Test-Command: /usr/bin/greet World | grep -q "Hello, World"
Depends: @, coreutils
```

兩種定義測試的方式：

**方式一：`Tests:` — 指向 `debian/tests/` 裡的 script**

```
Tests: smoke functional
Depends: @
```

對應 `debian/tests/smoke` 和 `debian/tests/functional` 兩個可執行 script。每個 script 的退出碼決定 pass（0）/fail（非 0）。

**方式二：`Test-Command:` — 直接寫指令**

```
Test-Command: greet World | grep -q "Hello, World"
Depends: @
```

適合一行就能測的簡單情況。

## Depends: @ 的特殊語意

```
Depends: @
```

`@` 是個特殊值，表示「**這個 source 產出的所有 binary package**」。測試前 autopkgtest 會裝齊它們。這是最常見的寫法——你要測自己的套件，當然要先裝它們。

```
Depends: @, curl, python3-requests
#        ↑    ↑
#    本套件   額外的測試依賴（測試 script 需要的工具）
```

| Depends 值 | 意義 |
|---|---|
| `@` | 本 source 的所有 binary package |
| `@builddeps@` | 本套件的所有 Build-Depends（測試需要 build 工具時）|
| `pkgname` | 具體的額外測試依賴 |

## 寫一個測試 script

`debian/tests/smoke`（可執行）：

```bash
#!/bin/sh
# smoke test for greet — 確認裝起來能跑
set -e

# 注意：用系統裝的 greet（/usr/bin/greet），不是 build 目錄的
output=$(greet World)
if [ "$output" != "Hello, World!" ]; then
    echo "FAIL: expected 'Hello, World!', got '$output'"
    exit 1
fi

echo "PASS: greet works"
exit 0
```

```bash
chmod +x debian/tests/smoke
```

測試 library 的例子（`debian/tests/library-link`）——測 `libgreet-dev` 真的能用來編譯：

```bash
#!/bin/sh
# 測試 libgreet-dev 能否用來編譯程式
set -e

# 在臨時目錄寫一個用 libgreet 的程式
cat > test.c <<'EOF'
#include <greet.h>
#include <stdio.h>
int main(void) { printf("%s\n", greet_make("test")); return 0; }
EOF

# 用系統裝的 header 和 library 編譯（驗證 dev 套件可用）
gcc -o test test.c -lgreet
./test | grep -q "Hello, test"

echo "PASS: libgreet-dev usable"
```

對應的 `debian/tests/control`：
```
Tests: library-link
Depends: @, gcc, libc6-dev
Restrictions: allow-stderr
```

## Restrictions：測試的特殊需求

`Restrictions:` 宣告測試的特殊條件：

| Restriction | 意義 |
|---|---|
| `needs-root` | 測試需要 root 權限 |
| `allow-stderr` | 測試輸出到 stderr 不算失敗（預設 stderr 有輸出 = fail）|
| `isolation-container` | 測試需要 container 級隔離（會改系統狀態）|
| `isolation-machine` | 需要完整 VM 隔離（如測試 reboot、kernel module）|
| `needs-reboot` | 測試中途需要重開機 |
| `breaks-testbed` | 測試會破壞環境（之後環境不可重用）|
| `superficial` | 淺層測試（只是 smoke test，不算完整測試）|

> `allow-stderr` 常被忽略導致莫名失敗：autopkgtest 預設「測試往 stderr 寫東西 = 失敗」（因為很多錯誤訊息走 stderr）。如果你的測試正常會印 stderr（如 gcc 的警告），要加 `allow-stderr`，否則無辜被判 fail。

## 跑 autopkgtest

```bash
# 安裝
sudo apt install autopkgtest

# 在 source 目錄，用各種測試環境跑

# 用 null runner（在當前系統跑，最快但會污染系統，僅 debug 用）
autopkgtest greet_1.0-1_amd64.changes -- null

# 用 LXC container（推薦，隔離且快）
autopkgtest greet_1.0-1_amd64.changes -- lxc autopkgtest-bookworm

# 用 QEMU VM（最完整的隔離，能測 reboot 等）
autopkgtest greet_1.0-1_amd64.changes -- qemu bookworm.img

# 用 schroot（配合 sbuild 的 chroot）
autopkgtest greet_1.0-1_amd64.changes -- schroot bookworm-amd64-sbuild
```

> 測試環境（backend）的選擇：debug 用 null（快但污染系統）、日常用 lxc/schroot（隔離且快）、需要完整隔離（reboot、kernel module）用 qemu。CI 環境（Ch 31/32）通常用 lxc 或 qemu。

## 故意弄壞：測試用 build 目錄的執行檔

```bash
# debian/tests/smoke 寫錯——用了 build 目錄的相對路徑
cat debian/tests/smoke
# #!/bin/sh
# ./greet World    ← 錯！測試環境裡沒有 build 目錄

autopkgtest ... -- lxc ...
# autopkgtest [..]: test smoke: - - - - - stderr - - - - -
# /tmp/.../smoke: 2: ./greet: not found
# autopkgtest [..]: test smoke: FAIL
```

教訓：autopkgtest 測的是**已安裝**的套件。測試 script 必須用系統路徑（`greet` / `/usr/bin/greet`），不是 build 目錄的 `./greet`。這正是 autopkgtest 的價值——它強迫你測「使用者實際會用的東西」。

## autopkgtest 在 archive 的角色

autopkgtest 不只是本地工具，它是 Debian/Ubuntu **持續整合**的核心：

```
套件上傳到 unstable
        │
  Debian CI（ci.debian.net）自動跑它的 autopkgtest
        │
  而且：跑所有「依賴這個套件」的其他套件的 autopkgtest
        │  （確保你的更新沒有破壞別人）
        ▼
  全綠 → 套件能順利遷移到 testing
  有 regression → 阻擋遷移，通知維護者
```

關鍵洞察：當你更新套件 X，CI 不只測 X，還測**所有依賴 X 的套件**。如果你的更新破壞了某個下游套件的 autopkgtest，遷移被擋下。這讓 autopkgtest 成為防止「更新破壞生態」的安全網。

> 這就是為什麼寫 autopkgtest 是負責任的打包行為——它讓你的套件被自動驗證，也讓整個依賴生態能偵測 regression。沒有 autopkgtest 的套件是 CI 的盲點。

## 踩雷集錦

1. **測試用 build 目錄的相對路徑**：autopkgtest 測已安裝套件，build 目錄不存在。用系統路徑（`greet`）

2. **忘記 `Depends: @`**：沒有 `@`，autopkgtest 不會裝你的套件，測試對著空系統跑，必然失敗

3. **stderr 輸出被判 fail**：預設 stderr 有輸出 = 失敗。正常會印 stderr 的測試要加 `Restrictions: allow-stderr`

4. **測試會改系統狀態但沒宣告隔離**：測試啟動了 daemon、改了設定，但沒宣告 `isolation-container`，污染了測試環境影響後續測試。會改系統的測試要宣告隔離等級

5. **測試 script 沒可執行權限**：`debian/tests/` 裡的 script 要 `chmod +x`，否則 autopkgtest 跑不動

6. **只有 superficial 測試卻不標記**：如果你的測試只是 smoke test（測「能跑」不測「跑對」），標 `Restrictions: superficial`，誠實表達測試深度。不標會被當完整測試，給人虛假的信心

## 進階：as-installed vs as-built 測試的哲學

為什麼 Debian 這麼重視 as-installed 測試？因為「build 成功」和「使用者能用」之間有巨大鴻溝：

- build 環境有所有 build 依賴，安裝環境只有 runtime 依賴——漏宣告 runtime 依賴在 build 看不出來，as-installed 測試抓得到
- build 目錄的 library 路徑和系統路徑不同——連結問題在 build 隱藏，as-installed 暴露
- 設定檔、maintainer script 的效果只在真實安裝才顯現

```
debian/tests/control 的設計哲學：
  測試應該模擬「使用者裝了套件後會做的事」
  → 用系統路徑、裝齊 runtime 依賴、在乾淨環境
  → 這才是「套件真的能用」的證明
```

理解這個哲學，你寫的 autopkgtest 會更有意義——不是為了「有測試」而寫，而是為了「驗證使用者體驗」。

## 動手練習

1. 為練習 B 的 greet 寫 `debian/tests/control` 和一個 smoke test script（測 `greet World` 輸出正確）。用 `autopkgtest ... -- null`（或 lxc）跑它

2. 寫一個測 `libgreet-dev` 的測試（編譯一個用 libgreet 的小程式），體會「測 dev 套件可用」和「測執行檔可用」的不同

3. 故意弄壞：把測試 script 改用 `./greet`（build 目錄路徑），跑 autopkgtest 看它 fail，理解 as-installed 的意義

4. 製造 stderr 問題：讓測試印一行到 stderr（`echo warning >&2`），看它被判 fail，再加 `allow-stderr` 修復

## 本章重點整理

- autopkgtest（DEP-8）測試「已安裝的套件」，填補 lintian（靜態）和 upstream test（build 環境）的空白
- 測試在乾淨環境裝套件後跑，用系統路徑（`/usr/bin/greet`）而非 build 目錄
- `debian/tests/control`：`Tests:`（指向 script）或 `Test-Command:`（直接指令）；`Depends: @` 裝齊本套件
- Restrictions 宣告特殊需求（needs-root / allow-stderr / isolation-*）
- Debian CI 跑你套件的 autopkgtest，也跑所有下游套件的——防止更新破壞生態

## 自我檢核

- [ ] 能解釋 autopkgtest 和 upstream test（dh_auto_test）的差別（as-installed vs as-built）
- [ ] 知道 `Depends: @` 的意義，以及不寫會怎樣
- [ ] 知道為什麼測試要用系統路徑而非 build 目錄
- [ ] 能說出 `allow-stderr` 解決什麼問題
- [ ] 能解釋為什麼 Debian CI 要跑「下游套件」的 autopkgtest

## 延伸閱讀

### 官方文件

- **[DEP-8: automatic as-installed package testing](https://dep-team.pages.debian.net/deps/dep8/)**
  - **讀哪裡**：整份規格，特別是 `debian/tests/control` 語法和 Restrictions 列表
  - **學什麼**：autopkgtest 格式的權威定義；本章是教學版
  - **前提**：讀完本章

- **[autopkgtest(1) man page](https://manpages.debian.org/bookworm/autopkgtest/autopkgtest.1.html)**
  - **讀哪裡**：backends（null/lxc/qemu/schroot）和用法
  - **學什麼**：各測試環境的設定和選擇
  - **前提**：無

### 部落格 / 文章

- **[Debian Continuous Integration (ci.debian.net) about](https://ci.debian.net/doc/)**
  - **這篇說什麼**：Debian CI 如何用 autopkgtest 做全 archive 的持續測試、regression 偵測
  - **讀哪裡**：about 和 how it works
  - **為什麼值得讀**：理解 autopkgtest 在 archive 層級的角色，看到它如何防止更新破壞生態

→ [Ch 18 Multi-arch 支援](./18-multiarch.md)
