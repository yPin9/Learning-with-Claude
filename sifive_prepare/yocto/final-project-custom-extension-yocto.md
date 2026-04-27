# Final Project — 把 custom extension patch 塞進 RISC-V Yocto image

> 目標：整合整個 SiFive job spec 三條 responsibility 在一個 project：
> 1. 你在 `learn_compiler_backend` final 加的 custom extension (XMyMA)
> 2. 要 port 進 Yocto toolchain recipe (GCC + LLVM)
> 3. 產 full bootable RISC-V image + SDK 給假想客戶
>
> 完成後你有一份完整 "end-to-end custom extension delivery" portfolio。

## 為什麼這是好 final

- **Cross three courses**：learn_compiler_backend + learn_elf_linking + learn_yocto
- **完美 match job spec**：SiFive job 要求的所有技術 axis
- **Demo 級作品**：面試時 show your laptop + live boot
- **Production-like**：跟 SiFive 客戶 delivery 流程幾乎相同

## Prerequisites

- 完成 learn_compiler_backend 的 final project（加 XMyMA extension）
- 有 Yocto environment set up
- RISC-V hardware or QEMU

如果前面 final 沒做，可以**簡化版**：只 patch 一個 upstream GCC CVE fix。Scope 縮小、仍完整。

## Goal

End-to-end：

```
Customer 拿到:
  1. Yocto image (含 custom GCC runtime)
  2. SDK (含 custom GCC cross-compiler)
  3. Test application using __builtin_riscv_xmadd
  4. Documentation
  5. Verification report
```

## Phase 1: Prepare your custom extension patches

把你從 `learn_compiler_backend` 的 work 轉 patch。

### For GCC

Assume 你在 GCC source 已經 add XMyMA support：

```bash
cd /path/to/gcc-source
git log --oneline
# your commits 應該都在
git format-patch <base-commit>..HEAD -o /tmp/patches/gcc/
```

產生 e.g., 5 個 patch：

```
0001-riscv-add-xmyma-feature.patch
0002-riscv-define-xmadd-xmsub-xnmadd.patch
0003-riscv-add-xmyma-intrinsics.patch
0004-riscv-xmyma-scheduling-model.patch
0005-riscv-xmyma-testsuite.patch
```

### For LLVM

類似。每 commit 變 patch。

## Phase 2: Create meta-layer

```bash
mkdir -p meta-mycompany-xmyma/{conf,recipes-devtools/gcc/files,recipes-devtools/binutils/files,recipes-devtools/clang/files,recipes-kernel}
```

`meta-mycompany-xmyma/conf/layer.conf`:

```
BBPATH .= ":${LAYERDIR}"
BBFILES += "${LAYERDIR}/recipes-*/*/*.bb ${LAYERDIR}/recipes-*/*/*.bbappend"

BBFILE_COLLECTIONS += "mycompany_xmyma"
BBFILE_PATTERN_mycompany_xmyma = "^${LAYERDIR}/"
BBFILE_PRIORITY_mycompany_xmyma = "20"

LAYERDEPENDS_mycompany_xmyma = "core riscv"
LAYERSERIES_COMPAT_mycompany_xmyma = "kirkstone scarthgap"
```

加到 bblayers：

```bash
bitbake-layers add-layer /path/to/meta-mycompany-xmyma
```

## Phase 3: GCC bbappend

Copy patches:

```bash
cp /tmp/patches/gcc/*.patch meta-mycompany-xmyma/recipes-devtools/gcc/files/
```

Write `recipes-devtools/gcc/gcc_11.%.bbappend`:

```
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://0001-riscv-add-xmyma-feature.patch \
    file://0002-riscv-define-xmadd-xmsub-xnmadd.patch \
    file://0003-riscv-add-xmyma-intrinsics.patch \
    file://0004-riscv-xmyma-scheduling-model.patch \
    file://0005-riscv-xmyma-testsuite.patch \
"
```

## Phase 4: Binutils bbappend (if needed)

如果你的 extension 需要 binutils 支援（新 relocation type 或 opcode）：

```
recipes-devtools/binutils/binutils_%.bbappend
```

類似結構、加 binutils patch。

## Phase 5: LLVM/Clang bbappend (optional)

如果你想 support LLVM 同時：

```
recipes-devtools/clang/files/
recipes-devtools/clang/clang_%.bbappend
```

用 meta-clang layer（若 poky 沒內建）。

## Phase 6: Tune for XMyMA

建 `conf/machine/include/tune-xmyma.inc`:

```
DEFAULTTUNE ?= "riscv64-xmyma"

AVAILTUNES += "riscv64-xmyma"
TUNE_FEATURES:tune-riscv64-xmyma = "riscv64 xmyma"

TUNEVALID[xmyma] = "Enable XMyMA extension"

TUNE_CCARGS += "${@bb.utils.contains('TUNE_FEATURES', 'xmyma', '-march=rv64gc_xmyma', '', d)}"

PACKAGE_EXTRA_ARCHS:tune-riscv64-xmyma = "riscv64 riscv64-xmyma"
```

用：

```
# conf/machine/mymachine.conf
require conf/machine/include/riscv/tune-riscv.inc
require conf/machine/include/tune-xmyma.inc
DEFAULTTUNE = "riscv64-xmyma"
```

## Phase 7: Test application package

寫個 demo C application:

```c
// xmyma-demo.c
#include <stdio.h>

int main(void) {
    int a = 3, b = 5, c = 10;
    int result = __builtin_riscv_xmadd(a, b, c);
    printf("XMADD(%d, %d, %d) = %d\n", a, b, c, result);
    return 0;
}
```

Recipe：

```
# meta-mycompany-xmyma/recipes-apps/xmyma-demo/xmyma-demo_1.0.bb

SUMMARY = "Demo application using XMyMA extension"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=..."

SRC_URI = "file://xmyma-demo.c"

S = "${WORKDIR}"

do_compile() {
    ${CC} -march=rv64gc_xmyma -O2 -o xmyma-demo xmyma-demo.c
}

do_install() {
    install -d ${D}${bindir}
    install -m 0755 xmyma-demo ${D}${bindir}/xmyma-demo
}
```

## Phase 8: Image recipe

```
# meta-mycompany-xmyma/recipes-core/images/xmyma-demo-image.bb

require recipes-core/images/core-image-minimal.bb

SUMMARY = "Demo image with XMyMA extension support"

IMAGE_FEATURES += "ssh-server-openssh tools-debug"

IMAGE_INSTALL:append = " \
    xmyma-demo \
    packagegroup-core-tools-debug \
"
```

## Phase 9: Build everything

```bash
# Edit conf/local.conf
MACHINE = "qemuriscv64"     # or your actual board
DEFAULTTUNE = "riscv64-xmyma"

# Build
bitbake -c cleansstate gcc-cross-riscv64 gcc gcc-runtime \
                        binutils-cross-riscv64 \
                        xmyma-demo
bitbake xmyma-demo-image
```

First build ~2 小時。

## Phase 10: Verify

### Check GCC version

```bash
bitbake -e gcc-cross-riscv64 | grep "SRC_URI" | head
# 應看到你的 patch 都列上
```

### Check binary has XMADD

```bash
cd tmp/work/riscv64-poky-linux/xmyma-demo/1.0-r0/
riscv64-linux-gnu-objdump -d xmyma-demo | grep -A3 xmadd
# 應看到 xmadd 指令
```

### Run image

```bash
runqemu qemuriscv64 xmyma-demo-image
# login
/usr/bin/xmyma-demo
# 預期: XMADD(3, 5, 10) = 25
```

Spike 或真 hardware 驗證更好（如果支援 XMyMA）。

## Phase 11: SDK

```bash
bitbake -c populate_sdk xmyma-demo-image
```

產 `.sh` installer。Verify 客戶 SDK 內 GCC 支援 XMADD:

```bash
# 解壓 SDK 到 /opt
./poky-glibc-x86_64-xmyma-demo-image-riscv64-qemuriscv64-toolchain-*.sh

# Source env
source /opt/poky/.../environment-setup-...

# Compile test
cat > /tmp/test.c << 'EOF'
int main(void) {
    return __builtin_riscv_xmadd(1, 2, 3);
}
EOF

$CC -march=rv64gc_xmyma /tmp/test.c -o /tmp/test
riscv64-linux-gnu-objdump -d /tmp/test | grep xmadd
```

## Phase 12: 產 "Delivery Package"

Structure 客戶會收到：

```
sifive-xmyma-bsp-v1.0/
├── README.md                              ← getting started
├── RELEASE_NOTES.md
├── PATCHES.md                              ← 列所有 GCC patches
├── image/
│   └── xmyma-demo-image-qemuriscv64.wic.gz
├── sdk/
│   └── poky-glibc-x86_64-...-toolchain-....sh
├── docs/
│   ├── XMyMA_spec.pdf                      ← 你的 extension spec
│   ├── intrinsics.md                        ← __builtin_riscv_xmadd 使用
│   └── scheduling_model.md
└── test-suite/
    ├── test-xmadd.c
    ├── test-xmsub.c
    └── Makefile
```

Zip / tarball 成 delivery artifact。

## Phase 13: Report

```markdown
# XMyMA Extension Delivery Report

## Scope
SiFive XMyMA extension support in GCC 11.2 + Yocto kirkstone.

## Components
- GCC 11.2.0 patched with 5 XMyMA patches (commits: abc..., def...)
- Binutils unchanged (XMyMA uses standard R4-type)
- Yocto meta-mycompany-xmyma layer

## Verification
- [x] GCC emits xmadd/xmsub/xnmadd when -march=rv64gc_xmyma
- [x] Auto-match pattern: (a*b)+c → xmadd
- [x] Intrinsic: __builtin_riscv_xmadd works
- [x] Rootfs boots on qemuriscv64
- [x] Demo application produces correct output
- [x] SDK usable on x86_64 Linux host

## Benchmarks (if done)
- [perf numbers on a specific workload with xmyma vs without]

## Limitations
- LLVM support not yet included (planned next release)
- No vectorization integration (future work)

## Next Steps
- Upstream patches to GCC master
- Add LLVM support
- Add kernel support (if needed)
```

## Phase 14: Document + GitHub

Put everything on GitHub:

```
github.com/yourname/sifive-xmyma-yocto-bsp/
├── meta-mycompany-xmyma/       ← the Yocto layer
├── patches/
│   ├── gcc/
│   └── binutils/
├── docs/
├── scripts/
│   ├── build.sh
│   └── verify.sh
└── README.md
```

面試 demo 時：

1. Clone repo
2. Show layer structure
3. Boot image in QEMU
4. Run `xmyma-demo`
5. Show objdump with xmadd

**5 分鐘 end-to-end demo**。

## 評估標準

**60 分** (MVP)：
- GCC patch 進 Yocto layer
- bitbake gcc-cross 成功
- 能看到 patch applied

**75 分**：
- 完整 image build + boot
- Demo app 含 xmadd
- Verify XMADD executes on target

**85 分**：
- SDK 也工作
- Binutils support (if applicable)
- Custom tune + machine config

**95 分**：
- LLVM parallel support
- Benchmark comparison
- Full delivery package with docs

**100 分**：
- GCC patches submitted upstream
- Layer contributed to meta-riscv or meta-sifive upstream
- End-to-end tested on real hardware

## 時間預估

- 60 分：1 週（if compiler patch 已經 ready）
- 85 分：2-3 週
- 100 分：2-3 個月

建議先 60 分、再 iterate。

## 履歷條目

```
SiFive XMyMA Custom Extension - End-to-End Yocto BSP
- Designed XMyMA extension (fused multiply-add)
- Implemented GCC support (compiler pattern, intrinsic, scheduling model)
- Patched into Yocto kirkstone via meta-layer
- Built bootable RISC-V image + SDK with XMyMA-aware toolchain
- Verified on QEMU qemuriscv64 with demo application
- GitHub: [link]
```

## 最後

完成這個 final project 你展示：

- Compiler backend （寫 extension）
- Yocto integration （recipe + layer）
- ELF / linking （custom opcode 透過 assembler）
- Benchmark / validation 方法論

**這是 SiFive compiler engineer 職位的完整 demonstration**。

去做。面試時讓 interviewer 看到你 real code。

---

## 自我檢核

- [ ] 我有完整 GitHub repo 含 meta-layer
- [ ] 我能 live demo image boot + xmyma-demo
- [ ] 我有 delivery package (image + SDK + docs)
- [ ] 我寫了 professional delivery report
- [ ] 我準備好在 SiFive 面試 show 這 project

完成 = 畢業。準備去面試。
