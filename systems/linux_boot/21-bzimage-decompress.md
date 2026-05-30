# Ch 21 — bzImage 結構與 kernel 解壓

> **目標**：理解 Linux kernel 映像（bzImage）的結構——為什麼壓縮、real-mode setup stub、解壓 stub（piggy）、解壓到 vmlinux 的過程，以及 vmlinuz / vmlinux / bzImage 這些容易混淆的名稱。這是 kernel 從「磁碟上的檔案」變成「記憶體裡能跑的 code」的關鍵一步。

> **環境**：Linux kernel 6.x，x86-64。承接 Ch 20（boot protocol）。原理深挖章。

## 為什麼 kernel 是壓縮的？

bootloader 載入的 kernel（`/boot/vmlinuz`）是**壓縮**的。為什麼不直接放未壓縮的 kernel，省去解壓步驟？

```
kernel 壓縮的理由：
  1. 大小：未壓縮的 kernel 幾十 MB，壓縮後小一半以上
     → 省磁碟空間、省 bootloader 載入時間（讀較少 sector）
  2. 歷史：早期 kernel 要塞進有限的記憶體/軟碟，壓縮是必要的
  3. 現在：解壓很快（CPU 快），壓縮的 I/O 節省 > 解壓 CPU 成本
        │
  代價：kernel 自己要先解壓自己才能跑
  → bzImage 前面有一段「解壓 stub」負責這件事
```

理解 bzImage 的結構（壓縮的 kernel + 解壓 stub），你才懂 kernel 啟動的第一步：它先把自己解壓出來。這也釐清了 vmlinuz/vmlinux/bzImage 這些常搞混的名稱。

## 先建立直覺：bzImage 是「會自己解壓的 kernel」

```
bzImage 像個自解壓的壓縮檔（self-extracting archive）：

  ┌─ bzImage（bootloader 載入的）──────────┐
  │                                        │
  │  setup stub（16-bit real-mode code）    │ ← BIOS 開機的入口
  │   + setup header（Ch 20）               │
  │                                        │
  │  解壓 stub（decompressor）             │ ← 解壓 kernel 的 code
  │                                        │
  │  壓縮的 vmlinux（piggy.o）             │ ← 真正的 kernel（壓縮）
  │                                        │
  └────────────────────────────────────────┘
        │ kernel 啟動時
        ▼
  解壓 stub 把壓縮的 vmlinux 解壓到記憶體
        │
  vmlinux（未壓縮的真正 kernel）開始執行
```

bzImage 自己包含「解壓自己的 code」——bootloader 不用懂壓縮格式，只要把 bzImage 載入、跳進去，bzImage 的解壓 stub 會自己把真正的 kernel 解壓出來。

## 名稱釐清：vmlinux / vmlinuz / bzImage

這幾個名稱常搞混，釐清：

```
vmlinux：
  - 未壓縮的 kernel ELF 檔
  - build 的中間產物，含 debug symbols（很大，幾百 MB）
  - 用於 debug（gdb、符號解析），不直接開機

vmlinuz：
  - 「壓縮的 vmlinux」（z = zipped）
  - /boot/vmlinuz-* 就是它
  - 實際是 bzImage（在 x86）

bzImage：
  - "big zImage"，x86 的壓縮 kernel 映像格式
  - = setup stub + 解壓 stub + 壓縮的 vmlinux
  - bootloader 載入的就是這個
        │
  關係：bzImage（檔案）內含 壓縮的 vmlinux，
        解壓後得到 vmlinux（可執行的 kernel）
        /boot/vmlinuz = bzImage
```

> **vmlinux vs vmlinuz 一字之差，意義不同**。`vmlinux`（無 z）是未壓縮的 ELF，build 產物，給 debug 用。`vmlinuz`（有 z）是壓縮的，`/boot` 裡的，給開機用，實際是 bzImage。「bzImage」和「vmlinuz」在 x86 基本是同義（vmlinuz 是 bzImage 的習慣檔名）。記住：開機用 vmlinuz（=bzImage，壓縮），debug 用 vmlinux（未壓縮）。

## bzImage 的詳細結構

```
bzImage 的記憶體佈局（從前到後）：

  偏移 0:
    boot sector + setup（real-mode code）
      - 第一個 sector 是傳統 boot sector（BIOS 直接開機用，現在多由 bootloader 取代）
      - 之後是 setup code（16-bit，設定環境）
      - 含 setup header（Ch 20，偏移 0x1F1）

  setup 之後:
    壓縮的 kernel（vmlinux.bin.gz 或其他壓縮格式）
    + 解壓 stub（arch/x86/boot/compressed/）
      - head_64.S：解壓的入口
      - misc.c：解壓邏輯（呼叫對應的解壓器）
      - piggy.o：壓縮的 vmlinux 嵌在這
```

```bash
# 看 bzImage 的資訊
file /boot/vmlinuz-$(uname -r)
# Linux kernel x86 boot executable bzImage, version 6.1.0...,
#   RO-rootFS, swap_dev ..., Normal VGA

# 用 kernel 工具看 bzImage 資訊（如果有 extract-vmlinux）
/usr/src/linux-headers-$(uname -r)/scripts/extract-vmlinux \
    /boot/vmlinuz-$(uname -r) > /tmp/vmlinux
file /tmp/vmlinux
# /tmp/vmlinux: ELF 64-bit LSB executable, x86-64 ...
#   ↑ 解壓出來的真正 kernel（ELF）
```

## 壓縮格式

kernel 支援多種壓縮格式（build 時選）：

```
kernel 壓縮格式選擇（CONFIG_KERNEL_*）：
  gzip：傳統、相容性好、解壓中等
  bzip2：壓縮率高但慢（少用）
  lzma/xz：壓縮率最高、解壓慢
  lzo：解壓最快、壓縮率低
  zstd：壓縮率好且解壓快（現代趨勢）
        │
  取捨：壓縮率（省 I/O）vs 解壓速度（省開機時間）
  現代多用 zstd（平衡）或 gzip（相容）
```

```bash
# 看你的 kernel 用什麼壓縮（從 config）
grep CONFIG_KERNEL_ /boot/config-$(uname -r)
# CONFIG_KERNEL_ZSTD=y （例）
```

每種壓縮格式對應一個解壓 stub。bzImage 的解壓 stub 知道用哪個解壓器（build 時決定），解壓壓縮的 vmlinux。

## 解壓流程

```
kernel 解壓自己的流程：

  bootloader 跳進 bzImage（Ch 20 的 entry point）
        │
  setup code 跑（real-mode 或直接 64-bit，看 entry）
  設定基本環境
        │
  跳到解壓 stub（compressed/head_64.S）
        │
  解壓 stub：
    1. 設定解壓需要的環境（頁表、stack）
    2. 找到壓縮的 vmlinux（piggy）
    3. 呼叫解壓器（gunzip/unzstd...）解壓到目標位址
    4. 解壓出未壓縮的 vmlinux
        │
  跳到解壓出的 vmlinux 的 entry point（startup_64）
        │
  真正的 kernel 開始執行（Ch 22）
```

解壓 stub 是 kernel 的「自舉」部分——它在真正的 kernel 之前跑，唯一任務是把壓縮的 kernel 解壓出來並跳過去。解壓完成後，解壓 stub 的使命結束，控制權交給未壓縮的 vmlinux。

## KASLR：解壓時的隨機化

現代 kernel 在解壓時做 **KASLR**（Kernel Address Space Layout Randomization）——把 kernel 解壓到隨機位址，增加安全性：

```
KASLR（kernel 位址隨機化）：
  解壓 stub 不把 kernel 解壓到固定位址
  而是隨機選一個位址（在允許範圍內）
        │
  目的：讓攻擊者無法預測 kernel 的記憶體位址
  → 增加 kernel 漏洞利用的難度
        │
  代價：每次開機 kernel 位址不同
  （debug 時要注意，符號位址會變）
```

```bash
# 看 kernel 是否啟用 KASLR
cat /proc/cmdline   # 有 "nokaslr" 表示關閉
# 或看 kernel 載入位址（每次開機不同 = KASLR 開啟）
sudo dmesg | grep -i "kernel offset"
```

> KASLR 是解壓階段做的安全措施。它讓 kernel 每次載到不同位址，防止攻擊者用固定位址做利用（如 ROP）。debug kernel 時可能要 `nokaslr`（關閉，讓位址固定方便對照符號）。理解 KASLR 在解壓階段發生，能解釋為什麼 kernel 的位址每次開機不同。

## 故意對照：bzImage vs zImage（歷史）

```
zImage（舊，已淘汰）：
  早期的壓縮 kernel 格式
  限制：解壓後的 kernel 必須放在低 640KB（real mode 限制）
  → kernel 變大後放不下，被淘汰

bzImage（"big zImage"）：
  解決 zImage 的大小限制
  解壓後的 kernel 能放在 1MB 以上（高記憶體）
  → 現代 x86 kernel 都是 bzImage
        │
  「bz」不是 bzip2！是 "big zImage"
  （常見誤解：以為 bzImage 用 bzip2 壓縮）
```

> **「bzImage 的 bz 不是 bzip2」是個經典誤解**。bzImage = "big zImage"（解決 zImage 的大小限制），和壓縮格式無關。bzImage 可以用 gzip、zstd 等任何格式壓縮。名字裡的 bz 指「能放大 kernel」，不是 bzip2。記住這個避免誤解。

## 踩雷集錦

1. **混淆 vmlinux 和 vmlinuz**：vmlinux（無 z）未壓縮，debug 用；vmlinuz（有 z）壓縮，開機用。一字之差

2. **以為 bzImage 的 bz 是 bzip2**：bz = "big zImage"，和壓縮格式無關。bzImage 可用任何壓縮

3. **試圖直接 gdb /boot/vmlinuz**：vmlinuz 是壓縮的 bzImage，gdb 讀不了符號。要 debug 用未壓縮的 vmlinux（build 產物，或用 extract-vmlinux 解出來）

4. **以為 bootloader 要懂壓縮格式**：bootloader 只載入 bzImage（不解壓）。解壓由 bzImage 自己的 stub 做。bootloader 不碰壓縮

5. **KASLR 導致 debug 位址對不上**：KASLR 讓 kernel 每次載到不同位址，符號位址變動。debug 時用 `nokaslr` 關閉，讓位址固定

## 進階：解壓 stub 的設計巧思

解壓 stub 有個有趣的問題：它要解壓 kernel，但解壓需要記憶體，而 kernel 解壓後可能蓋到 stub 自己的位置。怎麼避免「解壓時蓋掉正在執行的解壓 code」？

```
解壓的「就地解壓」難題：
  壓縮的 kernel 和解壓後的 kernel 可能重疊
  解壓時，新資料（解壓出的）可能蓋到還沒讀的舊資料（壓縮的）
        │
  解法（in-place decompression）：
    把壓縮資料放在「解壓後 kernel 的尾端」
    從後往前解壓，確保「讀的位置」總是在「寫的位置」之前
    → 解壓不會蓋到還沒讀的壓縮資料
        │
  這是個精巧的記憶體佈局設計（kernel 的 arch/x86/boot/compressed/）
```

這個「就地解壓」的設計（壓縮資料放尾端、從後往前解壓）是個經典的系統程式技巧——在記憶體緊張時，避免解壓覆蓋自己。理解它能欣賞 kernel 早期 code 的精巧。對一般學習者這是 nice-to-know，但它展示了 kernel 開機 code 要處理的底層約束。

## 動手練習

1. 看 bzImage：`file /boot/vmlinuz-$(uname -r)` 確認是 bzImage。用 `extract-vmlinux`（在 kernel scripts）解出 vmlinux，`file` 確認是 ELF

2. 看壓縮格式：`grep CONFIG_KERNEL_ /boot/config-$(uname -r)`，找你的 kernel 用什麼壓縮（gzip/zstd...）

3. 看 KASLR：`sudo dmesg | grep -i "kernel offset"` 或多次開機看 kernel 位址是否變動。試 `cat /proc/cmdline` 看有沒有 nokaslr

4. 看 kernel 大小對比：`ls -lh /boot/vmlinuz-$(uname -r)`（壓縮的）vs 解出的 vmlinux（`ls -lh /tmp/vmlinux`），看壓縮省了多少

## 本章重點整理

- kernel 壓縮以省大小和 I/O；bzImage 是「會自己解壓的 kernel」（setup stub + 解壓 stub + 壓縮的 vmlinux）
- vmlinux（無 z）= 未壓縮 ELF，debug 用；vmlinuz（有 z）= 壓縮，開機用，實際是 bzImage
- bzImage 的「bz」是 "big zImage"（解決 zImage 的大小限制），不是 bzip2
- 解壓流程：bootloader 跳進 bzImage → setup → 解壓 stub 把壓縮的 vmlinux 解壓 → 跳 vmlinux entry
- KASLR 在解壓時把 kernel 載到隨機位址（安全），每次開機位址不同

## 自我檢核

- [ ] 能解釋 kernel 為什麼壓縮，以及 bzImage 怎麼自己解壓
- [ ] 能區分 vmlinux、vmlinuz、bzImage 三者（壓縮與否、用途）
- [ ] 知道 bzImage 的 bz 是什麼意思（不是 bzip2）
- [ ] 能描述解壓流程（bootloader → setup → 解壓 stub → vmlinux）
- [ ] 知道 KASLR 在哪個階段發生、它做什麼

## 延伸閱讀

### 官方文件

- **[Linux kernel: Documentation/arch/x86/boot.rst](https://www.kernel.org/doc/html/latest/arch/x86/boot.html)**
  - **讀哪裡**：bzImage 結構和 memory layout 那節
  - **學什麼**：bzImage 的精確結構、setup 和 compressed 部分
  - **前提**：本章 + Ch 20

### 部落格 / 文章

- **[Linux Inside: Kernel decompression](https://0xax.gitbooks.io/linux-insides/content/Booting/linux-bootstrap-5.html)** — 0xax
  - **這篇說什麼**：逐行讀 kernel 解壓 code（compressed/head_64.S、misc.c）
  - **讀哪裡**：decompression 那部分
  - **為什麼值得讀**：把解壓 stub 的原始碼講透，本章的深度補充

→ [Ch 22 kernel 早期初始化](./22-kernel-early-init.md)
