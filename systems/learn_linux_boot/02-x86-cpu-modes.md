# Ch 2 — x86 三種 CPU 模式

> 目標：搞懂 real mode / protected mode / long mode 的差異，以及為什麼 boot 要切兩次模式。

## 我們在哪裡

第 1 階段（CPU reset）+ 第 4 階段（Kernel）之間。每次模式切換都發生在 bootloader 內部。

## 三種模式總覽

x86 為了相容 1978 年的 8086，留下了一個**繼承過來的笨拙設計**：CPU 開機後在 real mode（16-bit、1MB 限制），要靠軟體一層一層切到 protected mode（32-bit）、再切到 long mode（64-bit）。

```
  Real mode (16-bit)
       │ 設 GDT、開 A20、CR0.PE = 1
       ▼
  Protected mode (32-bit)
       │ 設 paging、CR4.PAE = 1、EFER.LME = 1、CR0.PG = 1
       ▼
  Long mode (64-bit)
```

注意：**沒有從 real mode 直接跳到 long mode 的路**，必須經過 protected mode 過水一下。這是 Intel 設計上的限制，不是想不想的問題。

## Real mode

CPU 一開機就在 real mode。特色：

- **16-bit register**：`AX`、`BX`、`CX`、`DX` 都是 16-bit
- **20-bit 位址空間**：實體記憶體上限 1 MB（`0x00000` ~ `0xFFFFF`）
- **segment:offset 定址**：實際位址 = `segment << 4 + offset`
- **沒有保護**：所有程式都在 ring 0，沒有 page fault、沒有權限分隔
- **可以呼叫 BIOS INT 服務**：`INT 10h` 印字、`INT 13h` 讀磁碟

20-bit 的計算法：

```
segment = 0x07C0
offset  = 0x0000
位址   = 0x07C0 * 16 + 0x0000 = 0x7C00
```

`0x7C00` 這個位址後面 Ch 5 會反覆出現，是 BIOS 載 boot sector 的固定位址。

real mode 的限制不是過時的笑話 — 直到 boot 流程切到 protected mode 之前，**你寫的 code 必須遵守這些限制**：用 16-bit register、不能 access > 1MB、跳遠要管 segment。

## Protected mode

386 (1985) 引進。突破 1MB 限制，加進保護機制。

特色：

- **32-bit register**：`EAX`、`EBX`、...
- **32-bit 位址空間**：4 GB
- **segmentation**：用 GDT (Global Descriptor Table) 描述每個 segment 的 base、limit、權限
- **paging（可選）**：4KB 頁、虛擬 → 實體位址轉換
- **ring 0/3 保護**：kernel 跟 userspace 分開
- **可以開中斷的細緻控制**：IDT (Interrupt Descriptor Table)

**進入 protected mode 的最小步驟**：

1. 關中斷 (`cli`)
2. 載入 GDT (`lgdt`)
3. 開 A20 line（讓 21 條位址線都能用）
4. `mov cr0, eax` 把 CR0.PE 設成 1
5. `jmp far` 跳到 32-bit 程式碼 segment（這個 jmp 同時 flush pipeline）

這 5 步 Ch 7 會逐行寫一次 asm。

## Long mode

AMD64 (2003) 引進，Intel 後來跟進。

特色：

- **64-bit register**：`RAX`、`RBX`、...
- **48-bit 位址空間**：理論 256 TB（實際 CPU 支援度不一）
- **強制 paging**：long mode 沒有 segmentation 了，**一定要開 paging**
- **PML4 4-level page table**：48-bit 虛擬位址拆成 9+9+9+9+12 bit
- **新 register**：`R8`–`R15`、SSE2 強制有
- **沒有 BIOS INT**：因為 BIOS 是 16-bit code，long mode 不能直接呼叫

**進入 long mode 的最小步驟**：

1. 必須先在 protected mode 裡
2. 關 paging (`mov cr0, eax` 把 CR0.PG 設成 0)
3. 設 PML4 page table，把實體位址塞進 CR3
4. 開 PAE (CR4.PAE = 1) — long mode 強制要 PAE
5. 設 EFER.LME = 1（透過 `wrmsr`）
6. 重新開 paging (CR0.PG = 1) — 這一刻 CPU 真正進 long mode
7. `jmp far` 到 64-bit code segment

Ch 8 會把這 7 步逐行寫一次。

## 三模式對照表

| 特性 | Real | Protected | Long |
|---|---|---|---|
| Register 寬度 | 16-bit | 32-bit | 64-bit |
| 位址空間上限 | 1 MB | 4 GB | 256 TB |
| 定址方式 | segment:offset | segment + paging | paging only |
| 保護機制 | 無 | ring 0/3 | ring 0/3 |
| Paging | 無 | 可選 | 強制 |
| BIOS INT 可用 | ✅ | ❌ | ❌ |
| GDT 必要 | ❌ | ✅ | ✅（但 segment 大多 ignored）|
| CR0.PE | 0 | 1 | 1 |
| CR0.PG | 0 | 0 或 1 | 1 |
| EFER.LME | 0 | 0 | 1 |

把這張表記熟，後面 Ch 7 / Ch 8 看 asm 不會迷路。

## 為什麼 BIOS 不能在 long mode 用

BIOS 服務是 16-bit real mode code（INT 指令、segment:offset 定址）。CPU 一旦切到 protected 或 long mode，這些 code 跑不動 — segment 暫存器的意義變了、INT 行為變了。

**這是為什麼 boot loader 必須在 real mode 階段就讀完所有要載入的東西**：一旦切到 protected mode 你就再也不能 `INT 13h` 讀硬碟了。GRUB 早期就是這樣 — stage 1 在 real mode 把 stage 2 讀進來，切到 protected mode 後改用自己的硬碟驅動。

UEFI 不一樣，它本身就是 protected / long mode（後面 Ch 10），所以沒有這個問題。

## 一個常見踩雷：A20 line

8086 的位址線是 20 bit，最大就是 `0xFFFFF`。如果你算 `0xFFFF:0xFFFF = 0x10FFEF`，這超過 1MB 了 — 8086 會 wrap around 回 `0x0FFEF`。有些早期軟體**依賴這個 wrap**。

286 開始位址線變多，wrap around 不再發生。為了相容，IBM 想了個爛招：在主機板上接一條 gate，叫 A20 line（位址第 21 條線），預設**關掉**讓它 wrap around。要進 protected mode 用整個 4GB，必須先**手動把 A20 打開**。

打開方法歷史上有三種：鍵盤控制器、System Control Port A、BIOS INT 15h。Ch 7 會講最常見的 fast A20（Port A）。

這就是 boot 時 asm 裡那個 `in/out 0x92` 的真實意義 — 為了 1981 年的相容性，今天還在做這個。

## 動手練習

QEMU + GDB 觀察開機時 CPU 在哪個模式。

terminal 1：

```bash
qemu-system-x86_64 -s -S -nographic
```

terminal 2：

```bash
gdb
(gdb) target remote :1234
(gdb) info registers
```

看 `cr0` 的最低位（PE bit）。剛 reset 時應該是 0，表示 real mode。

繼續 step：

```
(gdb) si
(gdb) p/x $cr0
```

跑一段時間後（SeaBIOS 跑完、bootloader 切了模式），CR0.PE 會變 1，CR0.PG 也會變 1，這時你已經在 long mode（如果是 64-bit OS 的話）。

## 自我檢核

- [ ] 知道 x86 開機在 real mode、要經過 protected mode 才能到 long mode
- [ ] 三種模式的位址空間上限說得出來（1MB / 4GB / 256TB）
- [ ] 知道為什麼 BIOS INT 不能在 protected / long mode 用
- [ ] 知道 A20 line 是什麼歷史包袱

下一章對比 BIOS 跟 UEFI 兩條 boot 路線，後面 Part 2 / Part 3 各自展開。

→ [Ch 3 BIOS vs UEFI 路線總覽](./03-bios-vs-uefi.md)
