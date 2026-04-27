# Ch 8 — devtool workflow：該你常用的指令

> 目標：熟悉 `devtool` — Yocto 2.0+ 的日常工具。簡化 recipe modify / add / upgrade。讓 compiler 工程師改 recipe 不用手寫 bbappend。

## 什麼是 devtool

`devtool` 是 Yocto 提供的 command-line tool，包裝常見 workflow：

- 從 git / tarball 建新 recipe
- Modify 已存在 recipe
- Upgrade recipe version
- Deploy to target
- Finish (convert back to proper layer)

**不取代 bbappend**，但對日常 iterate 更方便。

## 核心指令

```bash
devtool add       建新 recipe
devtool modify    改現有 recipe
devtool upgrade   bump recipe version
devtool edit-recipe  直接開編輯器
devtool finish    把修改轉成正式 layer patch
devtool status    看哪些 recipe 在 modify 狀態
devtool reset     放棄 modify
devtool deploy-target  push binary 到 target
devtool build-image  build image with 你的 modify
```

## devtool modify：改 recipe

最常用。假設改 `gcc`：

```bash
devtool modify gcc
```

bitbake 會：

1. Clone gcc source 到 `workspace/sources/gcc/`
2. Apply 所有 patch
3. 建個 `workspace/appends/gcc_%.bbappend` 指向 local source

現在你可以直接改 source：

```bash
cd workspace/sources/gcc
vim gcc/config/riscv/riscv.md
# 做修改...
```

## 迭代 rebuild

```bash
bitbake -c compile gcc-cross-riscv64
# 或 quick test
bitbake -c do_compile_ptest gcc-cross-riscv64
```

只 rebuild 改過部分。快。

## 看成 git commits

```bash
cd workspace/sources/gcc
git status
git diff
git add -A && git commit -m "WIP: Add custom extension"
```

你的 local source 是個 git repo、可 commit 累積修改。

## 完成：`devtool finish`

當修改 OK：

```bash
devtool finish gcc meta-mycompany
```

這會：

1. 你的 commits 轉 patch files
2. 寫到 `meta-mycompany/recipes-devtools/gcc/files/`
3. 產生正式 bbappend
4. Clean up workspace

結果跟 Ch 5 手寫 bbappend 等效、但不用手刻。

## devtool add：新 recipe

要 package 一個 upstream project：

```bash
devtool add libfoo https://github.com/example/libfoo.git
```

devtool 會：

1. Clone repo
2. 試 analyze build system (autotools, cmake, meson, ...)
3. Generate 初版 recipe
4. 放到 workspace

調整 generated recipe + iterate：

```bash
vim workspace/recipes/libfoo/libfoo_git.bb
bitbake libfoo
```

Finish 放到 layer：

```bash
devtool finish libfoo meta-mycompany
```

## devtool upgrade：bump version

```bash
devtool upgrade gcc
```

devtool 問你想 upgrade 到哪版、試 apply 既有 patch。

大部分 patch 在 upgrade 後仍 apply（clean rebase）、某些要手 fix。

## devtool deploy-target：快速 push

改完 compile、想 run on target (RISC-V hardware or QEMU)：

```bash
devtool deploy-target gcc root@target.local
```

把 compiled binary scp 過去。不用 rebuild rootfs、快 iterate。

undeploy:

```bash
devtool undeploy-target gcc root@target.local
```

## devtool build-image

想 build image 含你的 modification：

```bash
devtool build-image core-image-minimal
```

Equiv 到 `bitbake core-image-minimal`、但保證 workspace 的 recipe 被用。

## devtool reset：撤銷

改壞了？

```bash
devtool reset gcc
```

Workspace 的 gcc 消失、revert 到原始 recipe 狀態。

## typical compiler workflow

SiFive 工程師改 gcc 典型流程：

```bash
# 1. Start
devtool modify gcc

# 2. 進 source
cd workspace/sources/gcc

# 3. 改 + commit
vim gcc/config/riscv/riscv.md
git add -A && git commit -m "Add XMADD instruction"

# 4. Rebuild
bitbake -c compile gcc-cross-riscv64

# 5. Test
# ... run test ...

# 6. More changes + commit...
vim ...
git commit -am "Fix XMADD encoding"

# 7. Once stable, upstream or finish
devtool finish gcc meta-sifive

# 8. git commit to your layer
cd meta-sifive
git add -A && git commit -m "Add XMADD support to GCC"
```

## Compare 跟純 bbappend

| 情境 | bbappend | devtool |
|------|----------|---------|
| 加一個已經定版的 patch | bbappend 清楚 | devtool 也 ok |
| 實驗新 change、反覆改 | bbappend 麻煩 | devtool 方便 |
| 送 upstream before finalize | devtool 的 git repo 直接用 | bbappend 要手工 |
| Fast edit + test cycle | slow | **fast** |
| New recipe | 手寫 | `devtool add` |

SiFive 的日常 development 用 devtool。final checkin 用 bbappend 進 layer。

## devtool 的 workspace 結構

```
workspace/
├── appends/
│   └── gcc_%.bbappend         ← 自動產的 bbappend
├── recipes/
│   └── libfoo/                 ← devtool add 新 recipe
├── sources/
│   ├── gcc/                    ← devtool modify 的 source
│   └── libfoo/
└── conf/
    └── layer.conf
```

`workspace` 本身是個 Yocto layer、自動 managed。

## devtool 跟 source dir persistence

```bash
devtool modify -x gcc /path/to/gcc-source
```

`-x` 指定 source location。可以用既有 git clone（你自己已經 maintain）。

在 finish 時 devtool 知道哪是 source dir、把 patch 產到對的地方。

## Limitations

### Limitation 1: Requires matching source type

如果 recipe 用 tarball、devtool 的 git-based workflow 可能有些 friction。

### Limitation 2: 某些 complex recipe fail

極度 customize 的 recipe（多個 SRCTREECOVEREDTASKS override 等）可能 devtool 處理不好。

### Limitation 3: 多個 simultaneous modify 衝突

同時 `devtool modify gcc` 跟 `devtool modify binutils` 沒問題。但改同一 recipe 多次要小心 workspace state。

## CI 用 devtool

想 CI 自動化 「拉 upstream 新 commit、apply SiFive patches、build test」？

devtool 適合 interactive、不適合 CI。CI 用 bbappend + git submodule / patch file 更穩。

**devtool 是 dev 工具、bbappend 是 production formalization**。

## devtool 跟 bbappend 共存

你可能同時用：

- Long-term patches → bbappend in meta-sifive
- 當前實驗 → devtool workspace
- Both apply 到 gcc

Yocto 能 handle、但注意順序：bbappend 先 apply、devtool 的 source 是 "on top"。

## 案例：upgrade gcc from 11.2 to 12.3

```bash
# 1. Start upgrade
devtool upgrade gcc -V 12.3.0

# 2. devtool 會 try apply 所有 existing patch
# 若有 fail:
cd workspace/sources/gcc
git am --abort
# 手 fix patch、git am --continue

# 3. Build
bitbake gcc-cross-riscv64

# 4. 通過 smoke test 後
devtool finish gcc meta-sifive

# 5. Remove old recipe?
# 舊 gcc_11.2 可能仍在 poky，但新 gcc_12.3 有 higher priority
```

這種 upgrade 看似簡單、實際有很多 corner case。

## 常見坑

1. **`devtool finish` 後忘了 commit to layer git**：recipe 在 meta-sifive 但不在 git history。
2. **多次 `devtool modify` 同一 recipe**：要 reset 才能重新 modify。
3. **Workspace 意外 commit 進 git**：`.gitignore` 裡加 `workspace/`。
4. **Binary 在 target 但沒 update**：`deploy-target` 不會 replace running process、需要 restart。
5. **reset 丟失 uncommitted changes**：先 git commit。

## 常見誤會

1. **「devtool 替代 bitbake」**：不。devtool 呼 bitbake、是 wrapper。
2. **「devtool 的 commit 自動 upstream」**：不。finish 產 patch 放 meta-layer、仍要手動 push git / send PR。
3. **「devtool 需要 Python 程式能力」**：不。Shell 就夠。
4. **「devtool 只 work with git source recipe」**：大部分 yes、tarball 部分 case 也 work。
5. **「deploy-target 改 rootfs」**：只 override 特定 binary、不 rebuild rootfs。

## 動手練習

1. `devtool modify gcc`、改 gcc comment、rebuild。
2. `devtool add foo https://github.com/some/foo.git`、試 build generated recipe。
3. `devtool upgrade busybox -V 1.35.0`、看 patch 有沒有 apply。
4. `devtool finish xxx meta-mycompany`、verify bbappend 產生。
5. `devtool reset xxx` + re-modify，練 workflow。

## 自我檢核

- [ ] 我能用 devtool modify 改 recipe 並 iterate
- [ ] 我能用 devtool add 新增 recipe
- [ ] 我能用 devtool upgrade 升級 recipe version
- [ ] 我知道 devtool finish 產出什麼
- [ ] 我知道 devtool 跟 bbappend 的 complementary 關係

下一章：Yocto 各種陷阱彙整。

→ [Ch 9 常見雷：sstate-cache / DEPENDS / PREFERRED_VERSION](./09-common-traps.md)
