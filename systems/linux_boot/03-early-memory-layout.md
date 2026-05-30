# Ch 3 — 開機時的記憶體佈局

> **目標**：理解開機早期的記憶體地圖——低 1MB 的歷史性佈局（IVT、BIOS data area、video memory、`0x7C00`）、為什麼 boot sector 載入到 `0x7C00`、以及 e820 記憶體偵測如何告訴你「哪些記憶體能用」。

## 為什麼要懂記憶體地圖？

你的 boot code 要把資料放在記憶體某處、要把 stack 設在某處、要把 stage2 或 kernel 載入到某處。如果你隨便挑位址，可能踩到 BIOS 正在用的區域（如中斷向量表、video memory），導致詭異的當機或畫面亂掉。

開機早期的記憶體不是一片乾淨的 RAM——低 1MB 有一堆**約定俗成、不能亂碰**的區域（1981 年定下來的）。這章給你那張地圖，讓你知道哪裡能放東西、哪裡是禁區。

## 先建立直覺：低 1MB 是塊「有主的地」

```
real mode 能定址的 1MB，不是空地，是劃分好的地段：

  0x00000 ┌────────────────────────┐
          │ IVT（中斷向量表）        │ ← BIOS 中斷用，不能亂碰
  0x00400 ├────────────────────────┤
          │ BIOS Data Area (BDA)    │ ← BIOS 的工作記憶
  0x00500 ├────────────────────────┤
          │                         │
          │  可用的低記憶體          │ ← 你能用！（stack、stage2...）
          │  (約 30KB ~ 480KB)      │
          │                         │
  0x07C00 ├────────────────────────┤
          │ ★ boot sector 載入處     │ ← BIOS 把你的 512B 放這
  0x07E00 ├────────────────────────┤
          │  可用的低記憶體（續）     │
  0x9FC00 ├────────────────────────┤
          │ EBDA（擴充 BDA）         │ ← BIOS 用
  0xA0000 ├────────────────────────┤
          │ Video Memory (VGA)      │ ← 顯示記憶體，寫這裡 = 改畫面
  0xC0000 ├────────────────────────┤
          │ Video BIOS / Option ROM │ ← 顯卡等的韌體
  0xF0000 ├────────────────────────┤
          │ Motherboard BIOS (ROM)  │ ← 主韌體
  0xFFFFF └────────────────────────┘ ← 1MB 邊界
```

關鍵：低 1MB 大部分是「有主的」（BIOS、video、ROM）。你能自由用的主要是 `0x00500`–`0x9FC00` 之間（扣掉 boot sector 自己佔的）。亂寫禁區會壞事。

## 低 1MB 的關鍵區域

### IVT（Interrupt Vector Table，`0x00000`–`0x003FF`）

real mode 的中斷向量表。256 個中斷，每個 4 bytes（segment:offset），共 1KB。`int 0x10`（video）、`int 0x13`（disk）、`int 0x15`（misc）這些 BIOS service 的入口位址都在這。

```
你呼叫 int 0x13（讀磁碟）時：
  CPU 查 IVT[0x13]（位址 0x13 × 4 = 0x4C）
  取出那裡存的 segment:offset
  跳過去執行 BIOS 的磁碟處理 code
```

**不要覆寫 IVT**——覆寫了 BIOS 中斷就壞了，你連印字、讀磁碟都做不了。

### BIOS Data Area（BDA，`0x00400`–`0x004FF`）

BIOS 的工作記憶，存著鍵盤緩衝、計時器 tick、偵測到的硬體資訊等。也不要亂碰。

### Boot Sector 載入處（`0x07C00`）

這是本課最重要的 magic number。BIOS 把你的 boot sector（512 bytes）載入到物理位址 `0x7C00`，然後跳過去執行。

```
為什麼是 0x7C00？
  歷史原因：1981 年 IBM PC 5150 的設計者選的
  考量：
    - 要在 1MB 以下（real mode 限制）
    - 要避開低位址的 IVT/BDA（0x0-0x500）
    - 要給 boot sector + 它的 stack 留足夠空間
    - 0x7C00 = 32KB - 1KB，在當時最小 32KB RAM 機器上，
      留了 boot sector（512B）+ stack 的空間到 32KB 頂端
        │
  → 0x7C00 成為四十年不變的約定
```

> `0x7C00` 是開機世界的「聖址」。每個 BIOS boot sector 都被載到這、每個 boot sector 的 `org 0x7c00` 都假設自己在這。這個數字沒有深刻的技術必然性——就是 1981 年的一個工程選擇，然後變成永久約定。記住它。

### Video Memory（`0xA0000`–`0xBFFFF`）

顯示記憶體。寫這裡的內容直接顯示在螢幕上。文字模式的 video memory 在 `0xB8000`：

```asm
; 直接寫 video memory 印字（不透過 BIOS int 10h）
mov ax, 0xB800
mov es, ax
mov byte [es:0], 'H'      ; 字元
mov byte [es:1], 0x0F     ; 屬性（0x0F = 白字黑底）
; 螢幕左上角出現白色的 H
```

文字模式每個字元佔 2 bytes（字元 + 顏色屬性）。這是 boot code 印字的另一種方法（除了 BIOS int 10h）。

## 為什麼 boot sector 只能用 512 bytes？

boot sector 是磁碟的第一個 sector，傳統 sector 大小是 512 bytes。BIOS 只載入這一個 sector 到 `0x7C00`。所以你的初始 boot code **只有 512 bytes**（扣掉 partition table 和 signature，實際可用更少，Ch 5）。

```
512 bytes 能做什麼？很有限。
  → 所以才有「兩階段 bootloader」（Ch 9）：
    stage1（512B boot sector）做的唯一重要事：
    用 int 13h 載入更大的 stage2 到記憶體，跳過去
    stage2（不受 512B 限制）才做真正的工作
```

## e820：記憶體偵測

boot code 需要知道「這台機器有多少 RAM、哪些區段能用、哪些被保留」。低 1MB 的地圖是固定的，但 1MB 以上的記憶體佈局因機器而異（多少 RAM、哪裡有保留區給 ACPI/MMIO）。

BIOS 提供 **int 15h, AX=E820h** 來查詢記憶體地圖：

```asm
; e820：查詢記憶體地圖（簡化）
; 每次呼叫回傳一個記憶體區段的描述
xor ebx, ebx           ; ebx = 0（第一次呼叫）
mov edx, 0x534D4150    ; magic "SMAP"
mov eax, 0xE820
mov ecx, 24            ; buffer 大小
mov di, buffer         ; 結果寫到 es:di
int 0x15
; 回傳：一個 e820 entry（base, length, type）
; type: 1=可用RAM, 2=保留, 3=ACPI可回收, 4=ACPI NVS, 5=壞區
; ebx != 0 表示還有更多 entry，重複呼叫直到 ebx = 0
```

每個 e820 entry 描述一段記憶體：

```c
struct e820_entry {
    uint64_t base;    // 起始物理位址
    uint64_t length;  // 長度
    uint32_t type;    // 1=usable, 2=reserved, 3=ACPI reclaimable, ...
};
```

```
e820 的典型輸出（一台有 2GB RAM 的機器）：
  base=0x0,        length=0x9FC00,    type=1 (usable，低 1MB 大部分)
  base=0x9FC00,    length=0x400,      type=2 (reserved，EBDA)
  base=0xF0000,    length=0x10000,    type=2 (reserved，BIOS ROM)
  base=0x100000,   length=0x7EF00000, type=1 (usable，1MB 以上的主 RAM)
  base=0x7FF00000, length=0x100000,   type=2 (reserved，ACPI 等)
  ...
```

> e820 是 bootloader 和 kernel 知道「記憶體長什麼樣」的標準方式。kernel 接手後會用 bootloader 傳來的 e820 地圖建立它的記憶體管理。UEFI 有對應的機制（GetMemoryMap，Ch 14）。type=1 是你能自由用的 RAM，其他 type 是保留區（碰了會壞）。

## 故意弄壞：把 stack 設在 video memory

```asm
; 錯誤：把 stack 設在 0xB8000（video memory）
mov ax, 0xB800
mov ss, ax
mov sp, 0xFFFF
; 之後 push/call 會寫到 video memory
push ax    ; 螢幕上出現亂碼字元！（stack 操作改了顯示記憶體）
```

stack 往下長（push 減 SP），如果設在 video memory 或 BIOS 區域，每次 push/call 都在亂寫禁區。boot code 要把 stack 設在「可用的低記憶體」（如 `0x7C00` 以下，或 boot sector 之後的空間）。

## 踩雷集錦

1. **覆寫 IVT 或 BDA（0x0–0x500）**：BIOS 中斷壞掉，int 10h/13h 都不能用。把資料和 stack 放 0x500 以上

2. **stack 設在禁區**：stack 往下長，設在 video memory 或 ROM 區會亂寫。設在可用低記憶體（如 0x7C00 下方有近 30KB 可用）

3. **以為 1MB 以上一定有 RAM**：1MB 以上的記憶體佈局因機器而異，可能有保留洞（MMIO、ACPI）。要用 e820 查，不要假設「1MB 以上全是 RAM」

4. **stage2 或 kernel 載入到禁區**：用 int 13h 載入大東西時，目標位址要是 e820 標示 usable 的區域，避開保留區和 video memory

5. **org 和載入位址不一致導致位址全錯**：boot sector `org 0x7c00` 假設自己在 0x7C00。如果你的 segment 設定讓實際位址不同，所有 label 位址都算錯（Ch 6 詳述）

## 進階：低 1MB 之後與記憶體洞

1MB 以上的記憶體不是連續的純 RAM——有各種「洞」：

```
1MB 以上的記憶體洞（為什麼有）：
  - MMIO（Memory-Mapped I/O）：裝置的暫存器映射到記憶體位址
    （如顯卡的 framebuffer、PCI 裝置）
  - ACPI tables：韌體放系統描述表的地方
  - 3GB-4GB 附近的「PCI hole」：32-bit 系統為了給 MMIO 留空間，
    即使有 4GB RAM，也有一段位址給裝置而非 RAM
    （這是為什麼 32-bit 系統「裝 4GB 只認得 3.x GB」）
```

e820 就是用來告訴你這些洞在哪。kernel 用 e820（或 UEFI memory map）建立精確的記憶體管理，避開所有保留區。

理解記憶體洞能解釋很多現象：為什麼 32-bit 系統認不到完整 4GB、為什麼某些位址讀寫會觸發裝置而非記憶體（MMIO）。這在 Ch 14（UEFI memory map）和 Part 5（kernel 記憶體初始化）會再carried forward。

## 動手練習

1. 寫一段 boot code，直接寫 video memory（`0xB8000`）印一個彩色字元（不用 int 10h），確認你理解 video memory 的位置和格式（字元+屬性）

2. 在 boot code 設好一個合理的 stack（如 `mov sp, 0x7C00`，stack 往下長到 boot sector 下方的可用區），確認 push/call 不出問題

3. 寫 e820 查詢迴圈，把所有記憶體 entry 印出來（base/length/type），看你 QEMU 機器的記憶體地圖。對照本章的典型輸出

4. 故意弄壞：把 stack 設在 `0xB8000`，看螢幕怎麼被 stack 操作弄亂

## 本章重點整理

- 低 1MB 是「有主的地」：IVT（0x0）、BDA（0x400）、boot sector（0x7C00）、video（0xA0000+）、ROM（0xF0000+）
- boot sector 載入到 `0x7C00`（1981 年的工程選擇，四十年不變的約定）
- 只有 512 bytes 的 boot sector 不夠用 → 兩階段 bootloader（stage1 載入 stage2）
- video memory 在 0xB8000（文字模式），直接寫能印字（字元+屬性各 1 byte）
- e820（int 15h, E820h）查詢記憶體地圖：哪些是 usable RAM、哪些是保留區/洞

## 自我檢核

- [ ] 能畫出低 1MB 的記憶體地圖，指出哪些是禁區、哪些可用
- [ ] 知道 boot sector 為什麼載入到 0x7C00（歷史約定）
- [ ] 能解釋為什麼需要兩階段 bootloader（512B 不夠）
- [ ] 知道 e820 是什麼、回傳什麼（base/length/type）、為什麼需要它
- [ ] 知道為什麼 1MB 以上的記憶體不能假設全是 RAM（有 MMIO/ACPI 洞）

## 延伸閱讀

### 官方文件

- **[OSDev Wiki: Memory Map (x86)](https://wiki.osdev.org/Memory_Map_(x86))**
  - **讀哪裡**：整頁，低 1MB 的完整地圖和各區域用途
  - **學什麼**：本章記憶體地圖的權威細節，每個區域的精確範圍
  - **前提**：無

- **[OSDev Wiki: Detecting Memory (x86)](https://wiki.osdev.org/Detecting_Memory_(x86))**
  - **讀哪裡**：E820 那節
  - **學什麼**：e820 的完整呼叫方式、所有 type 值、邊界情況
  - **前提**：本章的 e820 概念

### 部落格 / 文章

- **[The 0x7C00 mystery](https://www.glamenapp.com/blog/why-bootloader-0x7c00/)** 或 OSDev 的 boot sector 歷史討論
  - **這篇說什麼**：0x7C00 這個 magic number 的歷史考據
  - **讀哪裡**：歷史解釋那段
  - **為什麼值得讀**：理解這個「沒有技術必然性的永久約定」如何形成

→ [Ch 4 BIOS 韌體做什麼](./04-bios-firmware.md)
