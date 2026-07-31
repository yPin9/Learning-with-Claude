# Ch 5 — Patch 一個 upstream GCC bug 進 image

> **目標**：完整走一次 compiler 工程師的核心任務——從 upstream 抓一個 GCC patch → 用 .bbappend 加到 Yocto 的 gcc recipe → rebuild gcc → 驗證 patch 生效 → 驗證 image 裡的 gcc 行為。把 Ch 2（.bbappend 語法）和 Ch 4（toolchain recipe）整合成實際的 patch 流程。這是 SiFive compiler 工程師的 day-to-day 任務，也是這門課的核心目標。

> **環境**：Yocto（poky + meta-riscv，Ch 3）+ 你自己的 layer。

## 為什麼這是這門課的核心？

這門課的目標（README）就是「能改 recipe 把 patched GCC 塞進 RISC-V distro image」——而這一章就是**完整做一次**。前面的章節（心法、語法、toolchain recipe）都是為這章準備。compiler 工程師在 SiFive 的核心工作是：有一個 GCC patch（你寫的、或 upstream 的 bug fix），要把它整合進客戶的 Yocto BSP——這正是這章教的。

完成這章，你具備了這門課的核心能力——拿一個 GCC patch，用正確的方式（.bbappend，不改上游）加進 Yocto、rebuild、驗證。這是可重複的標準流程，也是你向 SiFive 證明「我能整合 toolchain patch 進 Yocto」的能力。這章把前面所有的鋪墊變成實際的、可操作的流程。

## 先建立直覺:patch 流程的全貌

```
patch GCC 進 image 的完整流程：

  1. 取得 patch（你的 fix 或 upstream 的）
     一個 .patch 檔（git format-patch 產生的）
        │
  2. 建立/找到你的 layer（meta-mycompany）
     不改上游的 gcc recipe，加在你的 layer
        │
  3. 寫 .bbappend 擴展 gcc recipe（Ch 2）
     gcc_%.bbappend：FILESEXTRAPATHS + SRC_URI:append
        │
  4. 放 patch 檔到對應目錄
        │
  5. rebuild gcc（bitbake gcc-cross + 相關）
     do_patch 套用你的 patch、do_compile 重編
        │
  6. 驗證 patch 生效
     bitbake -e gcc | grep SRC_URI（patch 在嗎）
     看 do_patch log（套用成功嗎）
        │
  7. rebuild image + 驗證行為
     用 patched gcc 重建 image，驗證 bug 修好了
        │
  → 從 patch 到驗證的完整流程
    核心：用 .bbappend（不改上游）+ 驗證每一步
```

關鍵心智：patch GCC 的完整流程——(1) 取得 patch；(2) 在你的 layer（不改上游）；(3) 寫 .bbappend 擴展 gcc recipe；(4) 放 patch 檔；(5) rebuild gcc；(6) 驗證 patch 生效（bitbake -e、do_patch log）；(7) rebuild image + 驗證行為。核心是「用 .bbappend 不改上游 + 驗證每一步」。

## Step 1-2:取得 patch + 建立 layer

```bash
# === Step 1: 取得 patch ===
# patch 可能來自：
#   - 你自己寫的 GCC 改動（git format-patch 產生）
#   - upstream 的 bug fix（從 gcc git 抓某個 commit）
# 範例：從 gcc 抓一個 commit 的 patch
# git format-patch -1 <commit> --output my-gcc-fix.patch
# 一個 patch 檔長這樣：
cat > /tmp/example.patch <<'EOF'
From abc123 Mon Sep 17 00:00:00 2001
From: You <you@sifive.com>
Subject: [PATCH] Fix RISC-V code generation bug

--- a/gcc/config/riscv/riscv.cc
+++ b/gcc/config/riscv/riscv.cc
@@ -100,7 +100,7 @@
-  old_code
+  new_code
EOF

# === Step 2: 建立你的 layer ===
cd ~/yocto
# 用 bitbake-layers 建一個 layer（標準方式）
cd poky && source oe-init-build-env
bitbake-layers create-layer ../meta-mycompany
bitbake-layers add-layer ../meta-mycompany
# meta-mycompany/ 結構：conf/layer.conf + recipes-example/
```

> **patch 來自你的 GCC 改動或 upstream 的 fix（git format-patch 產生），放在你自己的 layer（用 bitbake-layers create-layer 建）**。patch GCC 的前兩步：**取得 patch**——一個 `.patch` 檔，來自：你自己寫的 GCC 改動（用 `git format-patch` 從你的 gcc git commit 產生）、或 **upstream 的 bug fix**（從 gcc 的 git 抓某個修 bug 的 commit）。patch 檔是標準的 git patch 格式（含 From/Subject + diff）。**建立你的 layer**——用 `bitbake-layers create-layer ../meta-mycompany`（標準方式建一個 layer，含 conf/layer.conf）+ `add-layer` 啟用。**為什麼要自己的 layer**（Ch 1）——你的 patch 加在**你的 layer**，**不改上游的 gcc recipe**（meta/）。這是 Yocto 客製的正確方式——可維護（你的改動隔離）、不衝突（上游升級不影響你）、易追蹤（你的 patch 集中在你的 layer）。對 compiler 工程師，這個習慣很重要——客戶的 BSP 有上游的 gcc recipe，你的 patch 加在「客戶的客製 layer」（或你提供的 layer），不直接改上游。這樣客戶升級 Yocto 版本時，你的 patch 還在你的 layer（透過 gcc_%.bbappend 的 wildcard 命名，Ch 2，跨版本生效）。`git format-patch` 是產生 patch 的標準工具——它從 git commit 產生標準格式的 patch（含 metadata），Yocto 的 do_patch 能套用。

## Step 3-4:寫 .bbappend + 放 patch

```bash
cd ~/yocto/meta-mycompany
# === Step 3: 寫 gcc 的 .bbappend ===
mkdir -p recipes-devtools/gcc/gcc
cat > recipes-devtools/gcc/gcc_%.bbappend <<'EOF'
# gcc_%.bbappend —— 把我們的 GCC patch 加進 gcc recipe
# % = 任何版本（升級 gcc 版本時還生效，Ch 2）

# 告訴 bitbake 去哪找 patch 檔
FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

# 加我們的 patch（:append 疊加，不覆蓋上游的 patches）
SRC_URI:append = " file://my-gcc-fix.patch"
EOF

# === Step 4: 放 patch 檔 ===
cp /tmp/my-gcc-fix.patch recipes-devtools/gcc/gcc/
# 目錄結構：
# meta-mycompany/recipes-devtools/gcc/
#   gcc_%.bbappend
#   gcc/
#     my-gcc-fix.patch

# 檢查 .bbappend 被 bitbake 看到
cd ~/yocto/poky/build
bitbake-layers show-appends gcc 2>/dev/null | grep mycompany
# 應該看到你的 gcc_%.bbappend（確認被載入）
```

```
.bbappend 的關鍵細節（容易出錯）：

  FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"
    ${THISDIR} = .bbappend 所在目錄
    ${PN} = recipe 名（gcc）
    → bitbake 去 .bbappend 旁的 gcc/ 目錄找 patch
    （注意 := 立即賦值、結尾的 :）
        │
  SRC_URI:append = " file://my-gcc-fix.patch"
    :append 疊加（不覆蓋上游的 patches）
    前導空格（否則和前面黏住）
    file:// = 本地檔案（bitbake 從 FILESEXTRAPATHS 找）
        │
  命名 gcc_%.bbappend（% = 任何版本）
    不要寫死版本（gcc_13.2.bbappend），用 % 跨版本
        │
  → 這三個細節錯一個，patch 就不生效（Ch 2 的常見錯誤）
```

> **gcc_%.bbappend 的三個關鍵：FILESEXTRAPATHS（找 patch）+ SRC_URI:append（加 patch 不覆蓋）+ % 命名（跨版本）——錯一個 patch 就不生效**。寫 gcc 的 .bbappend（整合 Ch 2 的語法）：**Step 3** 寫 `gcc_%.bbappend`，三個關鍵：(1) **`FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"`**——告訴 bitbake 去哪找 patch 檔（`${THISDIR}` = .bbappend 所在目錄、`${PN}` = recipe 名 gcc，所以去 .bbappend 旁的 `gcc/` 目錄找；注意 `:=` 立即賦值、結尾的 `:`）；(2) **`SRC_URI:append = " file://my-gcc-fix.patch"`**——加你的 patch（`:append` 疊加不覆蓋上游 patches、**前導空格**、`file://` 本地檔案從 FILESEXTRAPATHS 找）；(3) **命名 `gcc_%.bbappend`**（`%` = 任何版本，跨版本生效，不要寫死版本）。**Step 4** 放 patch 檔到對應目錄（`recipes-devtools/gcc/gcc/my-gcc-fix.patch`）。這三個細節**錯一個 patch 就不生效**（Ch 2 的常見錯誤）——FILESEXTRAPATHS 錯 = 找不到 patch 檔、用 `=` 而非 `:append` = 覆蓋掉上游 patches（gcc source 都沒了）、命名版本不對 = .bbappend 不套用。所以寫完要**驗證**——`bitbake-layers show-appends gcc | grep mycompany`（確認你的 .bbappend 被載入）。這個目錄結構和 .bbappend 是 patch GCC 的標準模板——記住它（FILESEXTRAPATHS + SRC_URI:append + % 命名 + patch 放對目錄），你就能可靠地 patch 任何 recipe。對 compiler 工程師，這是 day-to-day 的操作——把 GCC patch 用這個標準方式加進 Yocto。

## Step 5-6:rebuild + 驗證 patch 生效

```bash
cd ~/yocto/poky/build
# === Step 5: rebuild gcc ===
# 先確認 patch 會被套用（bitbake -e 看 SRC_URI）
bitbake -e gcc 2>/dev/null | grep 'my-gcc-fix.patch'
# SRC_URI="... file://my-gcc-fix.patch ..."  ← patch 在 SRC_URI（生效！）
# 如果沒有 → .bbappend 沒生效（檢查命名/FILESEXTRAPATHS）

# rebuild gcc（先清掉舊的，確保重新 patch + 編譯）
bitbake -c cleansstate gcc-cross-riscv64    # 清掉 sstate（Ch 9，確保重建）
bitbake gcc-cross-riscv64                    # 重建 cross gcc

# === Step 6: 驗證 patch 真的套用了 ===
# 看 do_patch 的 log（patch 套用成功嗎）
ls tmp/work/*/gcc-cross*/*/temp/log.do_patch
cat tmp/work/*/gcc-cross*/*/temp/log.do_patch | grep -i 'my-gcc-fix'
# Applying patch my-gcc-fix.patch   ← 套用了！
# （如果 patch 套不上，這裡會報錯：patch failed/hunk failed）

# 看實際的 source 有沒有改（patch 改的那行）
grep 'new_code' tmp/work/*/gcc-cross*/*/gcc-*/gcc/config/riscv/riscv.cc
# 看到你 patch 改的內容 → patch 真的改了 source
```

> **rebuild gcc 前用 `bitbake -e | grep patch` 確認 patch 在 SRC_URI、rebuild 後看 do_patch log 確認套用成功——驗證每一步是 patch GCC 的紀律**。rebuild + 驗證：**Step 5** rebuild gcc——先 **`bitbake -e gcc | grep my-gcc-fix.patch`** 確認 patch 在 SRC_URI（**生效的前提**——如果沒有，.bbappend 沒生效，先修），然後 **`bitbake -c cleansstate gcc-cross-riscv64`**（清掉 sstate-cache 確保重新 patch + 編譯，Ch 9——否則可能用快取的舊版沒套 patch）+ `bitbake gcc-cross-riscv64` 重建。**Step 6** 驗證 patch 真的套用——**看 `do_patch` 的 log**（`tmp/work/*/gcc-cross*/*/temp/log.do_patch`——應該有 "Applying patch my-gcc-fix.patch"；如果 patch 套不上會報錯 "patch failed/hunk failed"，表示 patch 和 source 版本不 match）+ 看實際的 source 有沒有改（grep patch 改的那行）。**驗證每一步是 patch GCC 的紀律**——很多問題（patch 沒生效、套不上、用了快取的舊版）都因為沒驗證。對 compiler 工程師，這個「驗證每一步」的習慣很重要——你說「我 patch 了 GCC」，要能證明（patch 在 SRC_URI、do_patch 套用成功、source 真的改了）。**patch 套不上**（hunk failed）是常見問題——通常是 patch 和 gcc source 的版本不 match（patch 是針對另一個版本寫的，套到這個版本對不上行號/context），要調整 patch 或用對應版本。`cleansstate` 確保重建（sstate-cache 會跳過已建好的，你改了 recipe 要清掉對應的 cache 才會重新套 patch，Ch 9 的常見雷）。

## Step 7:rebuild image + 驗證行為

```bash
cd ~/yocto/poky/build
# === Step 7: rebuild image，用 patched gcc ===
bitbake core-image-minimal
# image 用你 patched 的 gcc 重建（編 image 裡的套件）

# === 驗證 patched gcc 的行為 ===
# 方法 1：在 image 裡測（如果你的 patch 影響 runtime 行為）
runqemu qemuriscv64 nographic
# 在 image 裡跑會觸發 bug 的程式，確認 bug 修好了

# 方法 2：直接測 cross-compiler（如果 patch 影響 code generation）
# 找到 patched 的 gcc
ls tmp/work/*/gcc-cross*/*/recipe-sysroot-native/usr/bin/riscv64*/
# 用它編一個會觸發 bug 的測試，確認產生正確的 code

# 方法 3：用 devtool（Ch 8 的更方便方式）
# devtool 提供更方便的 patch + test 迭代

# === 完整驗證的層次 ===
# 1. patch 在 SRC_URI（bitbake -e）✓
# 2. do_patch 套用成功（log）✓
# 3. source 真的改了（grep）✓
# 4. gcc 重建成功（bitbake gcc）✓
# 5. patched gcc 的行為對（測 code gen 或 runtime）✓
# → 五層驗證，確認 patch 真的生效且修好 bug
```

> **完整驗證有五層：patch 在 SRC_URI → do_patch 套用 → source 改了 → gcc 重建 → patched gcc 行為對——這是「證明 patch 真的修好 bug」的紀律**。最後一步 rebuild image 並驗證行為：**Step 7** `bitbake core-image-minimal`（用你 patched 的 gcc 重建 image 的套件）。**驗證 patched gcc 的行為**有幾種方法：(1) **在 image 裡測**（如果 patch 影響 runtime 行為——runqemu 跑會觸發 bug 的程式，確認修好）；(2) **直接測 cross-compiler**（如果 patch 影響 code generation——用 patched gcc 編一個會觸發 bug 的測試，看產生的 code 對不對）；(3) **用 devtool**（Ch 8 的更方便方式，快速 patch + test 迭代）。**完整的五層驗證**：(1) patch 在 SRC_URI（bitbake -e）；(2) do_patch 套用成功（log）；(3) source 真的改了（grep）；(4) gcc 重建成功；(5) **patched gcc 的行為對**（測 code gen 或 runtime——這是最終目標，bug 真的修好了）。這五層驗證確認「patch 真的生效且修好 bug」——對 compiler 工程師，這是嚴謹的交付（不是「我加了 patch」就好，而是「patch 套用了、gcc 重建了、行為驗證了、bug 修好了」）。這完成了 patch GCC 的完整流程——從取得 patch 到驗證行為。這是這門課的核心技能，也是 SiFive compiler 工程師的 day-to-day 任務。你現在能拿一個 GCC patch，用正確的方式（.bbappend 不改上游）整合進 Yocto、rebuild、五層驗證——這是可重複的標準流程。Ch 8（devtool）會教更方便的迭代方式，但理解這個「手動」的完整流程讓你懂每一步在做什麼（devtool 是自動化這些步驟）。

## 故意弄壞:patch 套不上的處理

```bash
cd ~/yocto/poky/build
# 最常見的 patch 問題：patch 套不上（hunk failed）

# 當 do_patch 失敗：
# ERROR: gcc-cross do_patch: Applying patch 'my-gcc-fix.patch' failed
# ... patch ... hunk FAILED ...

# 看 do_patch log 找原因
cat tmp/work/*/gcc-cross*/*/temp/log.do_patch
# Hunk #1 FAILED at 100.   ← patch 的第 100 行對不上
# → patch 和 gcc source 的版本不 match（patch 針對別的版本寫的）

# 原因和解法：
# 原因：patch 是針對 gcc 13.1 寫的，但你的 Yocto 用 gcc 13.2
#       → source 的行號/context 變了，patch 對不上
# 解法 1：重新 rebase patch 到正確的 gcc 版本
#         （在對的 gcc source 上重新做改動、format-patch）
# 解法 2：手動調整 patch（改 patch 的行號/context match 你的版本）
# 解法 3：確認你的 gcc 版本，找對應版本的 patch

# 用 -p 的 fuzz（patch 容錯，謹慎用）：
# 有時 patch 能用 fuzz 套上（容許小的 context 差異）
# 但最好還是用對版本的 patch

# → patch 套不上的核心：版本不 match
#   解法：用對應 gcc 版本的 patch，或 rebase patch
#   這是 compiler 工程師常遇到的（patch 和 Yocto 的 gcc 版本要對應）
```

> **patch 套不上（hunk failed）的核心是「版本不 match」——patch 針對別的 gcc 版本寫的，解法是 rebase patch 到正確版本**。patch GCC 最常見的問題是 **patch 套不上**（do_patch 失敗，"hunk FAILED"）。**原因**：patch 和 gcc source 的**版本不 match**——patch 是針對某個 gcc 版本（如 13.1）寫的，但你的 Yocto 用另一個版本（如 13.2），**source 的行號/context 變了**，patch 對不上（patch 用 context 行定位要改的地方，version 變了 context 就對不上）。**解法**：(1) **rebase patch 到正確的 gcc 版本**（在你的 Yocto 用的 gcc source 上重新做改動、`git format-patch` 產生對應版本的 patch——最乾淨）；(2) 手動調整 patch（改行號/context match 你的版本——可行但易錯）；(3) 確認 gcc 版本，找對應版本的 patch。有時 patch 能用 **fuzz**（容許小的 context 差異套上）但不可靠，最好用對版本的 patch。看 `log.do_patch` 知道哪個 hunk 失敗、在哪行——幫你定位。對 compiler 工程師，這個問題很常見——你的 patch 要和**客戶 Yocto 用的 gcc 版本對應**（客戶可能用不同版本的 Yocto/gcc，你的 patch 要 rebase 到他們的版本）。理解「patch 套不上 = 版本不 match，解法是 rebase」，你能處理這個常見問題。這也是為什麼要知道客戶的 gcc 版本（`bitbake -e gcc | grep '^PV='`，Ch 1）——你的 patch 要針對那個版本。這章完成了 patch GCC 的完整流程（含常見問題的處理）——你具備了這門課的核心能力。Ch 6（SDK）和 Ch 8（devtool）會補充給客戶的開發環境和更方便的迭代方式。

## 動手練習

1. 建 layer：用 bitbake-layers create-layer 建你的 layer，加入

2. 寫 .bbappend：寫 gcc_%.bbappend（FILESEXTRAPATHS + SRC_URI:append），放一個 patch

3. 驗證生效：bitbake -e 看 SRC_URI、show-appends 看 .bbappend、do_patch log 看套用

4. rebuild + 驗證：cleansstate + rebuild gcc，五層驗證 patch 生效

5. 處理 patch 套不上：故意用版本不對的 patch，看 hunk failed，理解 rebase

## 本章重點整理

- patch GCC 流程：取得 patch → 你的 layer（不改上游）→ .bbappend 擴展 gcc → 放 patch → rebuild → 驗證
- gcc_%.bbappend 三關鍵：FILESEXTRAPATHS（找 patch）+ SRC_URI:append（加 patch 不覆蓋）+ % 命名（跨版本）
- rebuild 前 bitbake -e 確認 patch 在 SRC_URI；rebuild 用 cleansstate（清快取確保重建）
- 五層驗證：patch 在 SRC_URI → do_patch 套用 → source 改了 → gcc 重建 → patched gcc 行為對
- patch 套不上（hunk failed）= 版本不 match，解法是 rebase patch 到你的 gcc 版本

## 自我檢核

- [ ] 能完整走一次 patch GCC 流程（layer → .bbappend → rebuild → 驗證）
- [ ] 會寫 gcc 的 .bbappend（FILESEXTRAPATHS/SRC_URI:append/% 命名）
- [ ] 會驗證 patch 生效（bitbake -e、do_patch log、五層驗證）
- [ ] 知道 cleansstate 的必要（清快取確保重建）
- [ ] 能處理 patch 套不上（版本不 match → rebase）

## 延伸閱讀

### 官方

- **[Yocto Patching](https://docs.yoctoproject.org/dev-manual/new-recipe.html#patching-code)** — Yocto Project
  - **讀哪裡**：patching code、用 .bbappend 加 patch
  - **為什麼值得讀**：Yocto patch 流程的官方權威

- **[devtool（patch 的更方便方式）](https://docs.yoctoproject.org/ref-manual/devtool-reference.html)** — Yocto
  - **為什麼值得讀**：Ch 8 會深入，patch 迭代的方便工具

### 背景

- **[git format-patch](https://git-scm.com/docs/git-format-patch)** — Git
  - **讀哪裡**：怎麼產生 patch
  - **為什麼值得讀**：產生 patch 的標準工具

下一章看 SDK vs eSDK——怎麼給客戶一個開發環境（含你的 patched toolchain）。客戶不一定要跑整個 Yocto，可以用 SDK 開發。

→ [Ch 6 SDK vs eSDK：給客戶 dev env](./06-sdk-vs-esdk.md)
