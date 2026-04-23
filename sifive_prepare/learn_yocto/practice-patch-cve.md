# 練習 — Patch 一個 CVE fix 進 gcc recipe

> 目標：真實 scenario — 你是 SiFive 工程師，客戶回報上游 gcc CVE。你要把 fix 快速 backport 進 Yocto image、驗證、交付。

## 情境設定

假設：

- poky 的 gcc 11.2.0 有一個已知 CVE（例：**CVE-2023-XXXX**）
- Upstream gcc 13 已 fix，但 poky 還沒 bump
- 客戶的 Yocto BSP 用 kirkstone（gcc 11.2.0）
- 你要 backport fix 給客戶

這是 SiFive support team 的日常 task。

## Workflow

```
1. Identify patch from upstream
2. Backport patch to 11.2
3. Add via .bbappend
4. Rebuild gcc + image
5. Verify fix
6. Document + deliver
```

## Step 1: 找 upstream patch

假設 CVE fix 的 commit 是 `abc123...` on gcc master branch：

```bash
git clone git://gcc.gnu.org/git/gcc.git /tmp/gcc
cd /tmp/gcc
git log --oneline --all | grep "CVE-2023"
# 找到 commit hash

git format-patch -1 abc123 -o /tmp/patches/
# 產生 0001-fix-CVE-2023-XXXX.patch
```

## Step 2: 嘗試 apply 到 gcc-11.2

```bash
git checkout releases/gcc-11.2.0
git apply --check /tmp/patches/0001-fix-CVE-2023-XXXX.patch
```

兩種結果：

### Clean apply
Great. 直接用。

### Rejected
```
error: patch failed: gcc/foo.c:123
```

要 backport：

```bash
git apply --reject /tmp/patches/0001-fix-CVE-2023-XXXX.patch
# 手動 fix foo.c.rej 對應 11.2 source structure
vim gcc/foo.c
git add -A && git commit -m "Backport CVE-2023-XXXX fix to 11.2"
git format-patch -1 HEAD -o /tmp/patches/
# 產生新 backport 版 patch
```

## Step 3: 建 meta-mycompany layer (若沒)

```bash
cd /path/to/yocto-workspace
mkdir -p meta-mycompany/conf
mkdir -p meta-mycompany/recipes-devtools/gcc/files
```

`conf/layer.conf`:

```
BBPATH .= ":${LAYERDIR}"
BBFILES += "${LAYERDIR}/recipes-*/*/*.bb ${LAYERDIR}/recipes-*/*/*.bbappend"

BBFILE_COLLECTIONS += "mycompany"
BBFILE_PATTERN_mycompany = "^${LAYERDIR}/"
BBFILE_PRIORITY_mycompany = "15"

LAYERDEPENDS_mycompany = "core riscv"
LAYERSERIES_COMPAT_mycompany = "kirkstone"
```

加 bblayers：

```bash
cd build
bitbake-layers add-layer /path/to/meta-mycompany
```

## Step 4: 放 patch + 寫 bbappend

```bash
cp /tmp/patches/0001-fix-CVE-2023-XXXX.patch \
   meta-mycompany/recipes-devtools/gcc/files/
```

寫 `gcc_11.%.bbappend`:

```
# meta-mycompany/recipes-devtools/gcc/gcc_11.%.bbappend

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://0001-fix-CVE-2023-XXXX.patch \
"

# Document the patch
CVE_CHECK_WHITELIST += "CVE-2023-XXXX"
```

`CVE_CHECK_WHITELIST`: 告訴 Yocto 的 CVE scanner「這個已修了、不要 warn」。

## Step 5: Clean + rebuild

```bash
bitbake -c cleansstate gcc-cross-riscv64 gcc gcc-runtime gcc-sanitizers
bitbake gcc-cross-riscv64
```

第一次 ~20 分鐘（full gcc rebuild）。

Verify patch applied:

```bash
tail tmp/work/x86_64-linux/gcc-cross-riscv64/11.2.0-r0/temp/log.do_patch
```

應看到 `Applying patch 0001-fix-CVE-2023-XXXX.patch`。

## Step 6: Rebuild image

```bash
bitbake -c cleansstate core-image-minimal   # force rootfs re-assembly
bitbake core-image-minimal
```

結果：rootfs 的 libgcc.so 跟 libstdc++.so 含 fix。

## Step 7: Verify

### Method A: Check version string

```bash
runqemu qemuriscv64
# login
gcc --version
# 應顯示 11.2.0 + SiFive patch indicator (if added)
```

### Method B: Test binary

如果 fix 是 code generation issue：

```c
// test.c
// 特定 input 會觸發 CVE
void test() { ... }
```

```bash
gcc test.c -o test  # using patched compiler
./test              # 應該 not crash / 正確 behavior
```

### Method C: CVE scanner

Yocto 有 CVE check tool：

```bash
bitbake -c cve_check core-image-minimal
```

掃 rootfs 的 package、對 NVD 比對。應該不再 flag CVE-2023-XXXX。

## Step 8: Document for customer

寫 `meta-mycompany/recipes-devtools/gcc/files/README.md`:

```markdown
# GCC 11.2 patches applied

## 0001-fix-CVE-2023-XXXX.patch
Backport of upstream commit abc123def from gcc master
(https://gcc.gnu.org/git/?p=gcc.git;a=commit;h=abc123def).

Fixes: CVE-2023-XXXX (details at https://nvd.nist.gov/vuln/detail/CVE-2023-XXXX)

Applies cleanly to GCC 11.2.0 source.
Backport required: <yes/no>, see inline patch description for changes.

Verified by: [your name]
Date: 2026-04-24
```

## Step 9: Deliver

兩種交付：

### Option A: Patched SDK

```bash
bitbake -c populate_sdk core-image-minimal
```

給客戶 `.sh` SDK installer。客戶裝、用含 fix 的 cross-gcc。

### Option B: Patched binary

如果客戶只需要 runtime fix：產 rpm / deb / ipk of libgcc / libstdc++：

```bash
ls tmp/deploy/ipk/riscv64/ | grep -E "libgcc|libstdc"
```

客戶 deploy 這些 package 到 existing system。

### Option C: Full image

```bash
bitbake core-image-minimal
```

給 `.ext4` 或 `.wic` 檔、客戶 re-flash。

## Debug：patch 不 apply

**Symptom**: `do_patch` fail:

```
Applying patch 0001-fix-CVE-2023-XXXX.patch
patching file gcc/foo.c
Hunk #1 FAILED at 123
```

Debug:

```bash
cd tmp/work/.../gcc-cross-riscv64/11.2.0-r0/gcc-11.2.0/
# Look at the file manually
cat gcc/foo.c.rej
```

修 patch、re-generate、重試。

## CI integration

SiFive 內部 automate:

```yaml
# .github/workflows/ci.yml
- name: Build and test
  run: |
    cd yocto-build
    bitbake -c cleansstate gcc-cross-riscv64
    bitbake gcc-cross-riscv64
    bitbake core-image-minimal
    bitbake -c cve_check core-image-minimal
    # Expected: no new CVE
```

自動跑 CVE check、fail 就 alert。

## Long-term maintenance

這個 patch 要 maintain 多久？

- Poky 下版本 (langdale) 可能升 gcc 12 → fix 可能 already included → remove bbappend
- Check poky release notes 看有沒有 bumped gcc 版本

自家 bbappend 隨 poky 升級 rebase。

## 實戰 checklist

交 patch 給客戶前：

- [ ] Patch apply 成功
- [ ] gcc-cross build 過
- [ ] Full image build 過
- [ ] CVE scanner 不 flag
- [ ] Test case verify fix
- [ ] No regression in existing test
- [ ] Document 完整
- [ ] Patch source 可 upstream（若需要）

## 自我檢核

- [ ] 我完成從 upstream 抓 patch 到 apply 到 Yocto 的 flow
- [ ] 我能處理 patch rejected 情況（backport）
- [ ] 我知道 CVE_CHECK_WHITELIST 的用途
- [ ] 我能用 bitbake -c cve_check 掃 image
- [ ] 我能寫 documentation 給客戶

## 下一步

→ [Final Project：把 custom extension patch 塞進 RISC-V Yocto image](./final-project-custom-extension-yocto.md)
