# 練習 — Patch 一個 CVE fix 進 gcc recipe

> **目標**：真實情境——你是 SiFive 工程師，客戶回報上游 gcc（或某 toolchain 套件）有個 CVE/bug。你要把 fix backport 進客戶的 Yocto image、驗證、交付。整合 Ch 2（.bbappend）、Ch 4（toolchain recipe）、Ch 5（patch GCC）、Ch 8（devtool）、Ch 9（debug）的知識，完整走一次「拿到 fix → 整合進 Yocto → 驗證 → 交付」的真實流程。

## 背景與動機

這是 compiler 工程師在 SiFive 最真實的任務之一——客戶用著你們的 toolchain（在他們的 Yocto BSP 裡），某天 upstream 公布一個 gcc 的 CVE 或重要 bug fix，客戶要求你**把 fix 整合進他們的 image**。你要：找到 upstream 的 fix patch、用正確的方式（.bbappend，不改上游）加進 Yocto、rebuild、驗證 fix 生效、交付給客戶。

這個練習模擬這個完整流程。完成它，你驗證了這門課的核心能力——**拿一個 toolchain patch，正確地整合進 Yocto、驗證、交付**。這正是 README 說的「能改 recipe 把 patched GCC 塞進 RISC-V distro image」，也是你向 SiFive 證明能勝任的核心技能。

## 任務規格

把一個 fix patch 整合進 Yocto 的 gcc（或某 toolchain 套件）recipe：

| 步驟 | 要做的 | 對應章節 |
|---|---|---|
| 取得 patch | 找到 upstream 的 fix（git commit → patch）| Ch 5 |
| 確認版本 | 確認客戶的 gcc 版本，patch 對應 | Ch 1/9 |
| 整合 | 用 .bbappend 加 patch（不改上游）| Ch 2/5 |
| rebuild | cleansstate + rebuild | Ch 5/9 |
| 驗證 | 五層驗證 patch 生效 | Ch 5 |
| 交付 | 乾淨的 patch + .bbappend（給客戶的 layer）| Ch 5 |

**核心要求**：用正確的方式（.bbappend 不改上游、命名對版本、:append 疊加）、驗證 patch 真的生效（不是「以為加了」）、交付乾淨可維護（patch + .bbappend 在客戶的 layer）。

## 如果你卡住了

1. 先確認客戶的 gcc 版本（`bitbake -e gcc | grep '^PV='`），patch 要對應這個版本
2. patch 用 .bbappend 加（不改上游），命名 `gcc_%.bbappend`（跨版本）
3. FILESEXTRAPATHS + SRC_URI:append（Ch 2/5 的標準模板）
4. rebuild 前 cleansstate（確保重建，Ch 9 的坑）
5. 驗證：bitbake -e 看 patch 在 SRC_URI、do_patch log 看套用、測 fix 的行為
6. patch 套不上 → rebase 到客戶的 gcc 版本（Ch 5/9）
7. 也可以用 devtool（Ch 8）加速迭代

## 實作步驟建議

### Step 1：確認客戶的 gcc 版本
### Step 2：取得對應版本的 fix patch
### Step 3：在客戶的 layer 用 .bbappend 加 patch
### Step 4：cleansstate + rebuild
### Step 5：五層驗證 + 測 fix 行為
### Step 6：交付（乾淨的 patch + .bbappend）

## 完整參考解答

**自己走一遍！** 親手做才學到完整的 patch 流程。

<details>
<summary>完整流程</summary>

```bash
cd ~/yocto/poky/build

# ===== Step 1: 確認客戶的 gcc 版本 =====
bitbake -e gcc 2>/dev/null | grep '^PV='
# PV="13.2.0"  ← 客戶用 gcc 13.2，patch 要對應這個版本

# ===== Step 2: 取得對應版本的 fix patch =====
# 從 gcc 的 git 抓修 CVE 的 commit（對應 13.2 分支）
# git clone git://gcc.gnu.org/git/gcc.git
# cd gcc && git checkout releases/gcc-13
# git format-patch -1 <fix-commit> -o /tmp/
# 得到 /tmp/0001-Fix-CVE-xxx.patch
# （這裡假設你有了 fix patch）

# ===== Step 3: 在客戶的 layer 加 patch（.bbappend）=====
cd ~/yocto/meta-mycompany    # 客戶的 layer（或你提供的）
mkdir -p recipes-devtools/gcc/gcc
cat > recipes-devtools/gcc/gcc_%.bbappend <<'EOF'
# Backport CVE-xxx fix to gcc
FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"
SRC_URI:append = " file://0001-Fix-CVE-xxx.patch"
EOF
cp /tmp/0001-Fix-CVE-xxx.patch recipes-devtools/gcc/gcc/

# ===== Step 4: cleansstate + rebuild =====
cd ~/yocto/poky/build
# 確認 patch 會被套用
bitbake -e gcc 2>/dev/null | grep 'Fix-CVE-xxx'
# SRC_URI="... file://0001-Fix-CVE-xxx.patch ..."  ← 在 SRC_URI（生效）

bitbake -c cleansstate gcc-cross-riscv64    # 清快取（Ch 9 的坑）
bitbake gcc-cross-riscv64                     # rebuild

# ===== Step 5: 五層驗證 =====
# 1. patch 在 SRC_URI ✓（上面確認了）
# 2. do_patch 套用成功
cat tmp/work/*/gcc-cross*/*/temp/log.do_patch | grep -i 'CVE-xxx'
# Applying patch 0001-Fix-CVE-xxx.patch  ✓
# 3. source 真的改了
grep -r 'fixed_code' tmp/work/*/gcc-cross*/*/gcc-*/  # patch 改的內容 ✓
# 4. gcc 重建成功 ✓（bitbake 成功）
# 5. fix 的行為對（測會觸發 CVE 的 case，確認修好）
# 用 patched gcc 編一個觸發 CVE 的測試，確認不再有問題

# ===== Step 6: 交付 =====
# 交付給客戶的：
#   meta-mycompany/recipes-devtools/gcc/
#     gcc_%.bbappend            ← 乾淨的 .bbappend
#     gcc/0001-Fix-CVE-xxx.patch  ← 對應版本的 patch
# 客戶把這加進他們的 build → 他們的 image 含 fix
# 附帶文件：CVE 編號、影響、fix 來源、驗證方法
```

**流程說明**：

- **確認版本**（Ch 1/9）：patch 要對應客戶的 gcc 版本（13.2），否則套不上
- **正確整合**（Ch 2/5）：.bbappend 不改上游、gcc_% 跨版本、:append 疊加、FILESEXTRAPATHS
- **cleansstate**（Ch 9）：rebuild 前清快取確保重建
- **五層驗證**（Ch 5）：patch 在 SRC_URI → do_patch 套用 → source 改 → gcc 重建 → fix 行為對
- **交付**：乾淨的 patch + .bbappend + 文件（CVE 資訊、驗證方法）——客戶能直接用和維護
- **核心**：正確的方式（不改上游）+ 驗證生效（不是以為）+ 可維護的交付

</details>

## 測試用案例

| 檢查項 | 標準 |
|---|---|
| 版本對應 | patch 對應客戶的 gcc 版本 |
| 不改上游 | patch 在你的 layer 的 .bbappend |
| patch 生效 | bitbake -e 看 SRC_URI、do_patch log |
| fix 行為 | 測會觸發問題的 case，確認修好 |
| 交付乾淨 | patch + .bbappend + 文件 |

## 延伸挑戰（加分）

- **挑戰一**：用 devtool（Ch 8）做這個流程，比較和手動的差異

- **挑戰二**：patch 套不上——故意用版本不對的 patch，練習 rebase 到客戶的版本

- **挑戰三**：影響 SDK——確認你的 fix 也進到給客戶的 SDK（Ch 6 的變體問題）

- **挑戰四**：多個 toolchain 套件——patch 不只 gcc，也 patch binutils 或 glibc（不同的 toolchain recipe）

- **挑戰五**：寫交付文件——像真實的 security advisory response，寫 CVE 編號、影響、fix、驗證、客戶怎麼套用

## 自我檢核

- [ ] 能確認客戶的 gcc 版本，取得對應的 patch
- [ ] 能用正確的方式（.bbappend 不改上游）整合 patch
- [ ] 知道 rebuild 要 cleansstate（清快取）
- [ ] 能五層驗證 patch 真的生效
- [ ] 能交付乾淨可維護的 patch + .bbappend + 文件

這個練習走了 compiler 工程師的核心任務（backport fix 進 Yocto）。接下來 Final Project——更完整的：把你自家的 custom RISC-V extension 支援 patch 進 gcc + 塞進 image。

→ [Final Project：把你自家 custom extension patch 塞進 RISC-V Yocto image](./final-project-custom-extension-yocto.md)
