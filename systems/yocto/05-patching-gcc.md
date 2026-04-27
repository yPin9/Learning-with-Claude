# Ch 5 — Patch 一個 upstream GCC bug 進 image

> 目標：完整走一次「從 upstream 抓 patch → 加到 Yocto recipe → rebuild → verify in image」。這是 SiFive 工程師 day-to-day 任務。

## 情境

假設 GCC upstream fix 了一個 RISC-V bug（例如 a relocation emission bug）：

- Upstream commit: `abc123def456`
- 但 poky 的 gcc_11.2 還沒 backport
- 你的 customer 急著要
- 你要 patch 進 Yocto image

## Step 1: 取得 patch

### 從 upstream Git

```bash
git clone git://gcc.gnu.org/git/gcc.git
cd gcc
git show abc123def456 > /tmp/my-fix.patch
```

或用 `git format-patch`：

```bash
git format-patch -1 abc123def456 -o /tmp/patches/
```

產 `0001-riscv-fix-xxx.patch`。

### 確認 patch apply 到 GCC 11.2

```bash
cd /path/to/gcc-source-yocto-11.2
git apply --check /tmp/patches/0001-riscv-fix-xxx.patch
# 無 output = clean apply
```

失敗？多半需 rebase patch 到 11.2 branch。

## Step 2: 建你的 bbappend layer

如果還沒有 meta-company：

```bash
mkdir -p meta-mycompany/recipes-devtools/gcc/files
mkdir -p meta-mycompany/conf
```

`conf/layer.conf`:

```
BBPATH .= ":${LAYERDIR}"
BBFILES += "${LAYERDIR}/recipes-*/*/*.bb ${LAYERDIR}/recipes-*/*/*.bbappend"

BBFILE_COLLECTIONS += "mycompany"
BBFILE_PATTERN_mycompany = "^${LAYERDIR}/"
BBFILE_PRIORITY_mycompany = "10"

LAYERDEPENDS_mycompany = "core openembedded-layer riscv"

LAYERSERIES_COMPAT_mycompany = "kirkstone"
```

加到 `bblayers.conf`：

```
BBLAYERS += "/path/to/meta-mycompany"
```

## Step 3: 放 patch file

```bash
cp /tmp/patches/0001-riscv-fix-xxx.patch \
   meta-mycompany/recipes-devtools/gcc/files/
```

## Step 4: 寫 bbappend

```
# meta-mycompany/recipes-devtools/gcc/gcc_%.bbappend

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://0001-riscv-fix-xxx.patch \
"
```

**`gcc_%.bbappend`** 是 wildcard、所有 gcc version 都加此 patch。

如果 patch 只對 11.x：

```
gcc_11.%.bbappend
```

## Step 5: Parse check

```bash
bitbake -p
```

確認沒語法錯。檢查 bbappend 被 loaded：

```bash
bitbake-layers show-appends gcc
# 應顯示 meta-mycompany 的 bbappend
```

## Step 6: Rebuild

```bash
# 清 sstate 強制 redo patch step
bitbake -c cleansstate gcc-cross-riscv64 gcc gcc-runtime

# Rebuild
bitbake gcc-cross-riscv64
```

第一次 rebuild 約 10-30 分鐘（gcc 很大）。

## Step 7: Verify patch applied

```bash
cat tmp/work/x86_64-linux/gcc-cross-riscv64/11.2-r0/temp/log.do_patch
```

看 log：

```
Applying patch 0001-riscv-fix-xxx.patch
patching file gcc/config/riscv/riscv.md
Hunk #1 succeeded at 1234.
```

失敗會印 `patch FAILED`。

## Step 8: 驗證 fix effect

Rebuild 你的 rootfs：

```bash
bitbake core-image-minimal
```

進 image 或 deploy package：

```bash
runqemu qemuriscv64
# 登入後:
gcc --version
# 確認 version 對
```

跑你的 test case 確認 bug fixed。

## Step 9: 檢查 binary 差異（optional）

對比 before / after 的 gcc binary：

```bash
md5sum tmp/deploy/rpm/riscv64/gcc-*.rpm
# 或
cmp before_gcc_binary after_gcc_binary
```

應該不同。

## 常見問題 + debug

### Problem 1: Patch rejected

```
patch failed, saving rejects to file gcc/config/riscv/riscv.md.rej
```

原因：patch 跟 recipe 的 source version 不一致。

解法：

```bash
# 切到 recipe 用的 GCC source
cd tmp/work/.../gcc-cross-riscv64/11.2-r0/gcc-11.2.0
git apply --reject /tmp/patch
# 手動 fix .rej file
# 重新 git format-patch 產新 patch
```

### Problem 2: Bbappend 沒 effect

檢查：

```bash
bitbake-layers show-appends gcc
```

如果沒列你的 bbappend：

- bblayers.conf 沒加 meta-mycompany
- bbappend file name 錯
- LAYERSERIES_COMPAT 跟 poky branch 不 match

### Problem 3: Build fail after patch

新 compile error。原因：

- Patch 改 interface 但 caller 沒更新
- Conflict with other patches in SRC_URI

修正：精讀 error log、修 patch 或 conflict。

### Problem 4: Sstate 沒 invalidate

Rebuild 看起來很快、你的 patch 沒 effect：

```bash
# 暴力 force
bitbake -c cleansstate gcc-cross-riscv64 gcc gcc-runtime
bitbake gcc-cross-riscv64

# 或刪整個
rm -rf sstate-cache/*/sstate:gcc*
```

### Problem 5: GCC 跑但沒走 patched code

可能 bitbake 走了 prebuilt sstate（download_sstate）。disable：

```
SSTATE_MIRRORS = ""
```

強制全 local build。

## 進階：用 devtool 做同樣事

`devtool` 是 Yocto 的高級命令、同樣 workflow：

```bash
# 1. Modify gcc
devtool modify gcc

# 進 source tree
cd workspace/sources/gcc

# 2. Make change
vim gcc/config/riscv/riscv.md

# 3. 產 patch
git add -A
git commit -m "Fix riscv bug"

# 4. Save back as bbappend
devtool finish gcc meta-mycompany

# 5. Rebuild
bitbake gcc-cross-riscv64
```

`devtool finish` 把你的 commit 轉成 patch 放 meta-mycompany、自動產 bbappend。

Ch 8 會深入 devtool。

## SRCREV：另一 alternative

如果 source 是 git、不想管 patch file，直接 bump SRCREV：

```
# meta-mycompany/recipes-devtools/gcc/gcc_11.2.bbappend
SRCREV = "abc123def456"    # patched git commit
```

前提：你有 git repo with 那個 commit、recipe 用 git:// 不是 tarball。

## 管理多個 patch

Large customer 可能 dozens of patches：

```
meta-mycompany/recipes-devtools/gcc/files/
├── 0001-riscv-xmyext-support.patch
├── 0002-riscv-fix-cve.patch
├── 0003-riscv-sifive-sched-model.patch
└── 0004-riscv-zba-improvements.patch
```

bbappend:

```
SRC_URI:append = " \
    file://0001-riscv-xmyext-support.patch \
    file://0002-riscv-fix-cve.patch \
    file://0003-riscv-sifive-sched-model.patch \
    file://0004-riscv-zba-improvements.patch \
"
```

順序重要（後面 patch apply 於前面之後）。

## CI automation 的考量

SiFive 內部 CI flow：

1. 每 week 拉 poky upstream 新 commit
2. Apply SiFive 所有 patch
3. Build image
4. Run regression test
5. Fail 通知 owner

你作為 patch author 要 keep 你的 patch rebasable on poky master。

## 拿回上游

SiFive patch 最終目標：**upstream merge 到 GCC / poky**。

流程：

1. Upstream GCC first (Ch 19 of compiler_backend)
2. 等 poky 下一版含新 GCC
3. Poky bump GCC → SiFive 的 patch 可以 remove from bbappend

**自家 bbappend 留 patch 只是 temp measure**。永遠 aim for upstream。

## 驗證：產出能跑

`bitbake core-image-minimal` → rootfs 含 your-patched GCC runtime libs。

如果你改 compiler、也要重 build target package（因為 SDK GCC 可能都改）：

```bash
bitbake -c cleansstate -e 'BB_CLEAN_ALL=1' core-image-minimal
bitbake core-image-minimal
```

大範圍 rebuild。

## 動手練習

1. 建 meta-mycompany layer，含最小 gcc_%.bbappend（加 empty `.patch` 檔）。
2. 驗證 bbappend 被 Yocto 認到。
3. 真的從 GCC git 抓一個小 patch，apply 到你的 recipe。
4. Rebuild gcc-cross-riscv64，verify patch applied in log.do_patch。
5. 測試 patch 的 effect（e.g., runtime behavior 改變）。

## 自我檢核

- [ ] 我能產生 upstream patch for GCC
- [ ] 我能寫 bbappend 把 patch 加到 Yocto recipe
- [ ] 我能 verify patch 真的 applied
- [ ] 我能 debug "patch doesn't apply" 問題
- [ ] 我知道 devtool 的 alternative workflow

下一章看 SDK —— 讓客戶有 dev 環境的 Yocto 產物。

→ [Ch 6 SDK vs eSDK：給客戶 dev env](./06-sdk-vs-esdk.md)
