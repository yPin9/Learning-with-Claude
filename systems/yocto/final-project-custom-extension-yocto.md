# Final Project — 把 custom extension patch 塞進 RISC-V Yocto image

> **目標**：整合整門課的所有能力，完成一個最貼近 SiFive compiler 工程師真實工作的專案——把一個自家的 custom RISC-V 改動（一個 GCC patch，模擬支援某個 custom extension 或優化）整合進 RISC-V 的 Yocto image，從 patch 到 image 到驗證到交付，完整走一遍。這整合了 Ch 0–10 的所有知識，產出一個能展示「整合 toolchain patch 進 Yocto BSP」完整能力的作品。

## 專案總覽

你要完成一個從 GCC patch 到 RISC-V Yocto image 的完整整合：

```
完整的整合流程（這個 Final 走一遍）：

  1. 準備一個 GCC patch（模擬你的 custom 改動）
     如：一個 RISC-V 的 code generation 改動、新的優化、
         或支援某個 custom extension 的 patch
        │
  2. setup RISC-V Yocto 環境（Ch 0/3）
     poky + meta-riscv + 你的 layer，MACHINE=qemuriscv64
        │
  3. 整合 patch 進 gcc recipe（Ch 2/4/5）
     在你的 layer 用 .bbappend 加 patch（影響對的變體）
        │
  4. rebuild toolchain + image（Ch 5/7/9）
     cleansstate + rebuild gcc + rebuild image
        │
  5. 驗證（Ch 5/7）
     五層驗證 patch 生效 + 在 image 裡測 custom 改動的行為
        │
  6. 打包 SDK（Ch 6）
     build 含 patched toolchain 的 SDK 給客戶
        │
  7. 交付（Ch 5/10）
     patch + .bbappend + SDK + 文件
        │
  → 從 patch 到交付的完整流程
    這是 SiFive compiler 工程師的核心工作的完整展現
```

這個 Final 整合了整門課——setup 環境（Ch 0/3）、理解 recipe（Ch 1/2）、toolchain（Ch 4）、patch GCC（Ch 5）、SDK（Ch 6）、image（Ch 7）、devtool（Ch 8）、debug（Ch 9）。它是 SiFive job spec 三條 responsibility 的綜合——compiler 改動（你的 patch）、效能/正確性（驗證）、整合進 Yocto（這整個流程）。

## 為什麼做這個專案？

這是 SiFive compiler 工程師最真實的工作——你做了一個 GCC 的改動（支援新的 RISC-V extension、新優化、bug fix），要把它**整合進客戶的 Yocto BSP 並交付**。前面的練習做了一塊（backport CVE fix），這個 Final 整合成完整的流程——從 patch 到 image 到 SDK 到交付。

完成它，你獲得：一個展示**完整 toolchain 整合能力**的作品（從 GCC patch 到 RISC-V image 到交付）、把整門課知識整合應用的經驗、以及向 SiFive 證明「我能把 toolchain 改動整合進 Yocto BSP 並交付」的硬實力。這正是這門課的終極目標——README 說的「能改 recipe 把 patched GCC 塞進 RISC-V distro image」，這個 Final 完整做到。

## 整合的課程概念

| 階段 | 整合的章節 |
|---|---|
| 環境 setup | Ch 0（poky）、Ch 3（meta-riscv）|
| recipe 理解 | Ch 1（心法）、Ch 2（語法）|
| toolchain | Ch 4（toolchain recipe、變體）|
| patch GCC | Ch 5（完整 patch 流程、五層驗證）|
| 開發迭代 | Ch 8（devtool）|
| SDK | Ch 6（SDK 給客戶）|
| image | Ch 7（image recipe、rootfs）|
| debug | Ch 9（常見坑、debug 方法論）|

整門課至少 70% 的核心概念都用上了——這是 Final Project 的標準。

## 任務規格

完成從 GCC patch 到 RISC-V Yocto image 的完整整合：

### 交付物

1. **GCC patch**：一個你的 custom 改動（模擬支援 custom extension 或優化）
2. **你的 layer**：含 gcc 的 .bbappend + patch
3. **RISC-V image**：含 patched toolchain 編出的 image
4. **SDK**：含 patched toolchain 給客戶的開發環境
5. **驗證報告**：五層驗證 + 行為驗證的證據
6. **交付文件**：改動說明、整合方式、驗證方法、客戶怎麼套用

### 驗收標準

- patch 正確整合（.bbappend 不改上游、命名對、影響對的變體）
- patch 真的生效（五層驗證）
- custom 改動的行為驗證（在 image/SDK 裡測）
- SDK 含 patched toolchain（驗證 SDK 的變體）
- 交付乾淨可維護（patch + .bbappend + SDK + 文件）

## 建議的 custom 改動

```
適合的 custom GCC 改動（模擬真實工作）：

  1. 一個簡單的 code generation 改動：
     如改變某個 RISC-V pattern 的 code gen
        │
  2. 一個新的 builtin / intrinsic：
     如加一個對應某 custom 指令的 builtin
        │
  3. 一個優化的調整：
     如改 cost model、調 inline heuristic
        │
  4. 支援某個 custom extension：
     如加一個 -march 的 custom extension 解析
        │
  → 選一個你能做、能驗證的改動
    重點不是 patch 多複雜，是「整合進 Yocto 並驗證」的流程完整
    （可以用一個簡單但可驗證的改動，如加一個 builtin 或改個 message）
```

## 完整參考解答

**這是 Final Project，務必自己完整走一遍！** 下面是流程骨架，你要選自己的改動完整做。

<details>
<summary>完整整合流程</summary>

```bash
# ===== Step 1: 準備 GCC patch =====
# 用一個簡單但可驗證的改動（如改 gcc 的某個 message，或加一個 builtin）
# 這裡用「改一個可觀察的行為」當例子（實際工作是真的 code gen 改動）
# git format-patch 產生 patch

# ===== Step 2: setup RISC-V Yocto 環境 (Ch 0/3) =====
cd ~/yocto/poky && source oe-init-build-env
# 確認 meta-riscv 加了、MACHINE=qemuriscv64
bitbake-layers show-layers | grep riscv
grep MACHINE conf/local.conf

# ===== Step 3: 整合 patch 進 gcc (Ch 2/4/5) =====
cd ~/yocto/meta-mycompany
mkdir -p recipes-devtools/gcc/gcc
cat > recipes-devtools/gcc/gcc_%.bbappend <<'EOF'
# Custom RISC-V extension support
FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"
SRC_URI:append = " file://0001-custom-extension.patch"
EOF
cp /tmp/0001-custom-extension.patch recipes-devtools/gcc/gcc/

# ===== Step 4: rebuild toolchain + image (Ch 5/7/9) =====
cd ~/yocto/poky/build
# 確認 patch 生效
bitbake -e gcc 2>/dev/null | grep 'custom-extension'   # 在 SRC_URI
bitbake -c cleansstate gcc-cross-riscv64    # 清快取 (Ch 9)
bitbake gcc-cross-riscv64                     # rebuild gcc
bitbake core-image-minimal                    # rebuild image（用 patched gcc）

# ===== Step 5: 驗證 (Ch 5/7) =====
# 五層驗證 patch 生效
cat tmp/work/*/gcc-cross*/*/temp/log.do_patch | grep custom   # do_patch 套用
# 在 image / 用 patched gcc 測 custom 改動的行為
runqemu qemuriscv64 nographic    # 或用 patched gcc 編測試
# 確認 custom 改動生效（測會觸發改動的 case）

# ===== Step 6: 打包 SDK (Ch 6) =====
bitbake core-image-minimal -c populate_sdk
# 驗證 SDK 含 patch（Ch 6 的變體問題）
bitbake -e gcc-crosssdk 2>/dev/null | grep custom    # SDK 的 gcc 含 patch?
ls tmp/deploy/sdk/    # SDK installer

# ===== Step 7: 交付 =====
# 交付物：
#   meta-mycompany/recipes-devtools/gcc/  ← layer (patch + .bbappend)
#   tmp/deploy/images/qemuriscv64/...     ← RISC-V image
#   tmp/deploy/sdk/...                     ← SDK
#   驗證報告 + 交付文件
```

```markdown
<!-- 交付文件範例 -->
# Custom RISC-V Extension 整合交付

## 改動說明
[你的 GCC 改動：支援 X custom extension / 優化 Y]

## 整合方式
- Layer: meta-mycompany（用 .bbappend 擴展 gcc recipe，不改上游）
- Patch: 0001-custom-extension.patch（對應 gcc 13.2）
- 影響變體: gcc-cross + gcc-crosssdk（含 SDK）

## 驗證
- [x] patch 在 SRC_URI（bitbake -e）
- [x] do_patch 套用成功（log）
- [x] gcc 重建成功
- [x] custom 改動行為正確（測試 case：...）
- [x] SDK 含 patch（驗證 crosssdk 變體）

## 客戶套用方式
1. 把 meta-mycompany 加進 bblayers.conf
2. rebuild（cleansstate gcc + rebuild）
3. 用產生的 image / SDK（含 custom extension 支援）

## 版本相容
- 對應 gcc 13.2、poky scarthgap、meta-riscv scarthgap
- gcc 升版本時 .bbappend（gcc_%）仍生效，patch 可能要 rebase
```

**整合流程說明**：

- **完整流程**：patch → setup 環境 → 整合（.bbappend）→ rebuild toolchain+image → 驗證 → SDK → 交付
- **正確整合**（Ch 2/4/5）：.bbappend 不改上游、影響對的變體（含 SDK 用的 crosssdk）
- **嚴謹驗證**（Ch 5/6）：五層驗證 + 行為驗證 + SDK 含 patch 的驗證
- **完整交付**：layer + image + SDK + 文件（客戶能直接用和維護）
- **核心**：從 GCC patch 到 RISC-V image 到 SDK 到交付——SiFive compiler 工程師的完整工作

</details>

## 測試用案例（自我檢查交付品質）

| 檢查項 | 標準 |
|---|---|
| 環境 setup | RISC-V（meta-riscv + qemuriscv64）|
| patch 整合 | .bbappend 不改上游、影響對的變體 |
| patch 生效 | 五層驗證通過 |
| 行為驗證 | custom 改動在 image/SDK 裡正確 |
| SDK | 含 patched toolchain（驗證變體）|
| 交付 | layer + image + SDK + 文件，可維護 |

## 延伸挑戰（加分）

- **挑戰一**：用 devtool（Ch 8）做整個流程，體會高效迭代

- **挑戰二**：真實的 code gen 改動——做一個真的影響 RISC-V code generation 的 patch（不只改 message），用 perf_bench 的工具驗證效能

- **挑戰三**：多套件——除了 gcc，也 patch binutils（如支援新指令的組譯）

- **挑戰四**：在真實板子——如果有 RISC-V 硬體（VisionFive 2 等），在真板子上跑你的 image

- **挑戰五**：Buildroot 版——把同樣的 patch 也整合進 Buildroot（Ch 10），對比兩者

- **挑戰六**：CI——設定一個自動 build + 驗證的流程（patch → build → test）

## 自我檢核

完成這個專案後，你應該能回答：

- [ ] 我能 setup RISC-V Yocto 環境（poky + meta-riscv）
- [ ] 我能把 GCC patch 正確整合進 Yocto（.bbappend 不改上游、影響對的變體）
- [ ] 我能五層驗證 patch 生效 + 驗證行為
- [ ] 我能打包含 patched toolchain 的 SDK 給客戶
- [ ] 我能交付乾淨可維護的成果（layer + image + SDK + 文件）
- [ ] 面試被問「你怎麼把 toolchain 改動整合進 Yocto BSP」，我能展示這個專案

## 結語：你現在站在哪裡

完成這門課和這個專案，你已經具備「把 patched GCC 塞進 RISC-V Yocto image」的完整能力——這正是 SiFive job spec 第三條 responsibility（integrate GNU toolchain recipes in the Yocto/OE-based Linux distribution system）要的。你知道：

- Yocto 是什麼、怎麼運作（layer/recipe/task/metadata，Ch 0-2）
- RISC-V 的 Yocto BSP（meta-riscv、tuning，Ch 3）
- toolchain 怎麼在 Yocto 裡建（bootstrap、變體，Ch 4）
- 怎麼 patch GCC（.bbappend、五層驗證，Ch 5）
- 怎麼給客戶開發環境（SDK，Ch 6）
- image 怎麼組裝（Ch 7）
- 高效開發（devtool，Ch 8）
- 怎麼 debug（常見坑、問題歸屬，Ch 9）
- build 系統的選擇（Yocto vs Buildroot，Ch 10）

這門課刻意短（11 章）——它不是要你成為 Yocto maintainer，而是讓你作為 **compiler 工程師**能勝任「整合 toolchain 進 Yocto」這個具體任務。你現在能看懂 recipe、改 recipe、patch GCC、驗證、交付、debug——這對 compiler 工程師夠了。

最重要的是 compiler 工程師在 Yocto 的核心能力——**diagnose 問題歸屬**（是 compiler patch 問題還是 Yocto 問題，Ch 9）。當客戶的 build break，你能系統地定位（哪個 task、什麼 log、什麼歸屬），快速判斷和解決。這是你的價值——橋接 compiler 和 Yocto 兩個世界。

接下來往哪去？如果你要更深入 Yocto（成為 BSP maintainer、設計 layer），README 的參考資料（Yocto Mega-Manual、Streif 的書）能帶你更深。但對 compiler 工程師，這門課給的就夠了——你能整合你的 toolchain 改動進客戶的 Yocto BSP。配合 perf_bench（分析效能、提 compiler 優化）和你的 compiler 知識，你具備了 SiFive compiler 工程師的完整能力——做 compiler 改動、驗證效能、整合進 Yocto 交付。

恭喜你走到這裡。你現在能把 patched GCC 塞進 RISC-V Yocto image，並交付給客戶。
