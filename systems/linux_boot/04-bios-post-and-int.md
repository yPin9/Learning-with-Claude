# Ch 4 — BIOS POST、reset vector、INT 服務

> 目標：搞清楚 CPU 一通電的第一條指令在哪、BIOS 在做什麼、INT 服務是什麼東西。

## 我們在哪裡

第 1 階段（CPU reset）+ 第 2 階段（Firmware）的前半部。BIOS 路線。

## CPU reset 的第一條指令

x86 CPU 的硬體規格寫死：reset 後第一條指令在 `0xFFFFFFF0`（32-bit 模式下顯示）或 `F000:FFF0`（segment:offset 表示）。

這個位址叫 **reset vector**。

幾個容易混淆的點：

- 它在 4GB 位址空間的**最頂端往下 16 bytes** 的地方
- 但 CPU 開機在 real mode、只認 1MB 位址空間，怎麼能到 4GB 頂端？
- 答案：reset 後的 CS 暫存器有個特殊的 hidden base = `0xFFFF0000`，所以 `F000:FFF0` 實際上是 `0xFFFF0000 + 0xFFF0 = 0xFFFFFFF0`。第一次遠跳之後 CS 變正常的 `<<4` 行為。

這是一個 Intel 為了相容老 8086 同時讓 CPU reset 在「主機板 ROM 的常見位置」做的 hack。**你不用搞懂全部細節，只要記住「第一條指令在 4GB 頂端附近」**。

## 主機板上 ROM 怎麼出現在那個位址

主機板的 chipset 把 BIOS ROM **映射**到 `0xFFFE0000` ~ `0xFFFFFFFF` 這個位址範圍。CPU 從 `0xFFFFFFF0` 讀到的，其實是 ROM 晶片裡的內容。

第一條指令通常是個 `jmp far`，跳到 BIOS 真正的 entry：

```asm
; 在 0xFFFFFFF0 的內容
jmp far 0xF000:0xE05B    ; 跳到 BIOS init
```

跳完之後 CS hidden base 變回正常的 `0xF0000`，CPU 在 1MB 範圍內正常跑 BIOS code。

## POST 是什麼

POST = **Power-On Self Test**。BIOS 做的硬體檢查，順序大致是：

1. CPU 自檢（暫存器、cache）
2. 測 RAM（早期會看到那個跑數字的「Memory Test 1024 MB OK」）
3. 偵測顯卡、初始化 VGA（這時候才有畫面）
4. 偵測鍵盤、滑鼠、磁碟
5. 跑 PCI / PCIe enumeration
6. 把硬體資訊寫進 BIOS data area（`0x00400` 開始的記憶體）
7. 載入 boot device 的第一個 sector

你開機看到的廠商 logo、按 F2 進設定、按 F12 選 boot device，都發生在 POST 階段。

POST 失敗有特定的 **beep code**：1 短 = 正常、3 短 = 顯示卡壞、連續長嗶 = RAM 壞。雖然現在主機板很少有 buzzer 了，但這套還在。

## BIOS data area

BIOS 把硬體資訊放在 `0x00400` ~ `0x004FF` 這 256 bytes，叫 BIOS Data Area (BDA)。常用欄位：

| 位址 | 內容 |
|---|---|
| `0x400` | COM port 1 base address |
| `0x410` | Equipment list (有沒有軟碟、有沒有共處理器) |
| `0x413` | 可用 RAM 大小 (KB) |
| `0x449` | 目前 video mode |
| `0x46C` | timer tick count（每 55ms 加 1） |

real mode bootloader 想知道有多少 RAM，可以直接讀 `0x413`。當然 1MB 以上要用 `INT 15h, AX=E820` 拿，這個 Ch 14 講 kernel 載入時會再碰到。

## INT 服務是什麼

INT 指令是 x86 的軟體中斷。`INT N` 會：

1. 把 EFLAGS、CS、IP 推上 stack
2. 從 IDT (Interrupt Descriptor Table) 讀第 N 個 entry，得到 handler 位址
3. 跳過去執行
4. handler 跑完用 `IRET` 返回

real mode 的 IDT 在 `0x00000` ~ `0x003FF`，每個 entry 4 bytes（segment + offset），共 256 個 vector。

**BIOS 在這 256 個 vector 裡塞了一堆服務**，bootloader 透過 INT 呼叫它們做事。常用的：

| INT | 用途 | 例 |
|---|---|---|
| `INT 10h` | 顯示 | `AH=0Eh` 印一個字元 |
| `INT 13h` | 磁碟 | `AH=02h` 讀 sector |
| `INT 15h` | 系統服務 | `AX=E820h` 拿記憶體 map |
| `INT 16h` | 鍵盤 | `AH=00h` 等按鍵 |
| `INT 1Ah` | 時間 | `AH=00h` 讀 timer tick |

呼叫慣例：**參數放在 register 裡，AH 是「子功能編號」**。

範例：印一個字元 'A' 到螢幕

```asm
mov ah, 0x0E    ; teletype output
mov al, 'A'     ; 字元
mov bh, 0       ; page number
mov bl, 7       ; attribute (灰白)
int 0x10
```

這 5 行就是 Ch 6 自製 boot sector 的骨架。

## 為什麼這些 INT 在 protected / long mode 不能用

回到 Ch 2 講過的：BIOS handler 是 16-bit real mode code，用 segment:offset 定址、用 BIOS data area 在低端記憶體的固定位置。

切到 protected mode 後：

- segment 的意義變了（不再是 `<<4`，變成 GDT 索引）
- 中斷走 IDT，但 IDT 已經被 OS 重設了
- BIOS handler 期待的 segment 設定不存在

硬要叫的話 CPU 會 trap、或跳到亂的位址。所以**你必須在 real mode 階段做完所有 BIOS 呼叫**：讀完磁碟、拿完記憶體 map、查完磁碟幾何，**然後才切 protected mode**。

UEFI 把這個問題用 **Boot Services / Runtime Services** 解掉，後面 Ch 10 講。

## 找 boot device

POST 跑完，BIOS 根據設定的 boot order 一個一個試：

1. 對每個磁碟讀 sector 0（CHS = 0,0,1，或 LBA 0），共 512 bytes
2. 檢查最後 2 個 byte 是不是 `55 AA`
3. 是的話 → 載到 `0x0000:0x7C00`，`jmp 0x0000:0x7C00`
4. 不是的話 → 試下一個

`0x7C00` 這個位址也是歷史包袱。1981 年 IBM PC 配 32KB RAM，BIOS 想把 boot sector 載到「不會踩到 BIOS data area、又不會踩到 BIOS 本身、又不會踩到 stack」的地方。`0x7C00` = `0x7E00 - 0x200`，剛好在 32KB - 1KB 的位置，給 boot sector 512 bytes 的空間之後還有 1KB 給 stack。

40 年了還在用。

## 一個常見誤解：「BIOS 載完 boot sector 就消失」

**錯**。BIOS code 還在 `0xF0000` ~ `0xFFFFF` 那塊 ROM 映射的記憶體裡，IDT 還是 BIOS 設的。bootloader 可以**繼續呼叫 INT 13h 讀更多 sector**，這就是 stage 1 怎麼讀 stage 2 的方法。

直到 bootloader 自己決定切 protected mode、自己重設 IDT、自己接管硬碟 driver，BIOS 才退場。

但 BIOS 的記憶體還佔在那邊。protected mode 下你也不會去動它（除非你刻意想 reverse engineer BIOS）。

## 動手練習

QEMU + GDB step BIOS 第一條指令：

```bash
# terminal 1
qemu-system-x86_64 -s -S -nographic
```

```bash
# terminal 2
gdb
(gdb) target remote :1234
(gdb) set architecture i8086
(gdb) info registers cs eip
```

你會看到 `cs = 0xF000`、`eip = 0xFFF0` 之類的（因為 GDB 顯示的是 hidden base 應用後的）。

```
(gdb) x/4i $pc
(gdb) si
(gdb) x/4i $pc
```

第一條通常是個 long jump。step 過去，看下一條，慢慢追 SeaBIOS 怎麼初始化。

Bonus：跳到 BIOS 找 boot sector 那一刻：

```
(gdb) b *0x7c00      # boot sector 載入位址
(gdb) c              # 一直跑到 boot sector 第一條指令
```

在 SeaBIOS 環境下，`0x7c00` 通常會擊中 PXE boot 失敗訊息或 `Booting from Hard Disk` 之後的 fallback。如果 QEMU 帶 `-drive`，那就是真的 MBR 第一條指令。

## 自我檢核

- [ ] 知道 reset vector 在 `0xFFFFFFF0`、第一條通常是 long jump
- [ ] 講得出 POST 在做什麼（5 件事）
- [ ] 知道 INT 13h 是讀磁碟、INT 10h 是顯示
- [ ] 知道 boot sector 為什麼載到 `0x7C00`
- [ ] 知道 BIOS INT 為什麼切到 protected mode 後就不能用

下一章看 boot sector 內部 — 446 + 64 + 2 = 512 bytes 怎麼分配、partition table 怎麼長。

→ [Ch 5 MBR 與 boot sector](./05-mbr-and-boot-sector.md)
