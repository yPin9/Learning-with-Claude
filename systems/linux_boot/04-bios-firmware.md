# Ch 4 — BIOS 韌體做什麼

> **目標**：理解 BIOS 韌體在開機時的完整工作——POST、提供 BIOS services（int 10h/13h/15h 等中斷服務）、開機裝置選擇、以及它如何找到並載入 boot sector，讓你知道你的 boot code 「能向 BIOS 要哪些服務」。

> **環境**：QEMU 內建 SeaBIOS（開源 BIOS 實作）。真實 BIOS 行為類似，細節因廠商而異。

## 為什麼要懂 BIOS 提供什麼？

你的 boot sector 只有 512 bytes，做不了什麼複雜的事——它不能自己驅動磁碟控制器、不能自己初始化顯卡。但它需要讀更多磁碟資料、需要印字到螢幕。怎麼辦？

答案：**向 BIOS 借力**。BIOS 在 real mode 提供一組「服務」（透過軟體中斷呼叫），幫你讀磁碟、印字、查記憶體。你的 boot code 不用自己寫磁碟驅動，呼叫 `int 0x13` 就好。這章是「BIOS 能幫你做什麼」的目錄。

> **認識論誠實**：BIOS services 只在 real mode 可用。一旦你切到 protected mode（Ch 7），這些中斷服務就不能用了（除非切回 real mode）。所以 BIOS bootloader 的策略是：在 real mode 用 BIOS services 把所有需要的東西（kernel、initramfs）載入記憶體，**然後**才切到 protected/long mode。UEFI 沒這個限制（它的服務在 64-bit 也能用，Ch 12）。

## 先建立直覺：BIOS 是 real mode 的「系統呼叫」

```
你熟悉的 OS 環境：
  程式呼叫 syscall（write/read）→ kernel 幫你做 I/O

real mode 開機環境：
  boot code 呼叫 BIOS 中斷（int 10h/13h）→ BIOS 幫你做 I/O
        │
  BIOS service ≈ real mode 的「syscall」
  只是用軟體中斷（int N）呼叫，參數放暫存器
```

把 BIOS service 想成「開機階段的 syscall」——你還沒有 OS，但 BIOS 提供了基本的 I/O 服務讓你撐到能載入 OS。

## POST：開機自檢

電源一開，BIOS 做的第一件事是 **POST**（Power-On Self-Test）：

```
POST 做什麼：
  - 檢查 CPU 基本功能
  - 偵測並初始化 RAM（記憶體 controller、容量）
  - 初始化晶片組、PCI 裝置
  - 初始化顯卡（之後才能顯示東西）
  - 偵測鍵盤、磁碟等
  - 執行 option ROM（顯卡 BIOS、網卡 PXE 等）
        │
  POST 失敗 → 嗶聲錯誤碼（beep codes）或畫面錯誤
  POST 成功 → 繼續找開機裝置
```

POST 是韌體的事，你的 boot code 跑的時候 POST 早已完成（硬體已初始化好）。你只需要知道：boot code 執行時，硬體（記憶體、顯卡、磁碟）都已經是可用狀態。

## BIOS 找開機裝置

POST 後，BIOS 照設定的**開機順序**（boot order，在 BIOS 設定畫面設）找可開機裝置：

```
開機順序（典型）：
  1. USB
  2. 硬碟
  3. 網路（PXE）
  4. 光碟
        │
  對每個裝置，BIOS 讀它的第一個 sector（512 bytes）
        │
  檢查最後 2 bytes 是不是 0x55AA（boot signature）
    是 → 這是可開機裝置！載入這個 sector 到 0x7C00，跳過去
    否 → 試下一個裝置
        │
  全部都沒有 → "No bootable device"
```

這就是 Ch 0 你改掉 `0xaa55` 後 QEMU 報「No bootable device」的原因——BIOS 檢查每個裝置的 boot signature，沒有就不認為它可開機。

## BIOS Services：核心中斷

BIOS 提供的服務透過軟體中斷呼叫。最重要的幾個：

### int 0x10 — Video Services（顯示）

```asm
; AH = 功能號
; int 10h, AH=0Eh: teletype 印字元（最常用）
mov ah, 0x0e
mov al, 'X'        ; 要印的字元
int 0x10           ; 印出 X，游標前進

; int 10h, AH=00h: 設定顯示模式
mov ah, 0x00
mov al, 0x03       ; 模式 3 = 80x25 文字模式
int 0x10

; int 10h, AH=13h: 印字串
```

### int 0x13 — Disk Services（磁碟，最重要）

這是 bootloader 的命脈——用它讀更多磁碟資料（stage2、kernel）：

```asm
; int 13h, AH=02h: 讀 sector（CHS 定址）
mov ah, 0x02       ; 功能：讀 sector
mov al, 4          ; 讀幾個 sector
mov ch, 0          ; cylinder（柱面）
mov cl, 2          ; sector（從 1 開始，sector 1 是 boot sector，所以從 2 讀）
mov dh, 0          ; head（磁頭）
mov dl, [boot_drive] ; 磁碟編號（BIOS 開機時給的 DL）
mov bx, 0x8000     ; 讀到 ES:BX（這裡是 ES:0x8000）
int 0x13
jc disk_error      ; CF（carry flag）設了表示出錯

; int 13h, AH=42h: extended read（LBA 定址，現代用）
; CHS 有 8GB 限制，大磁碟用 LBA（Logical Block Addressing）
```

CHS（Cylinder-Head-Sector）是老式磁碟定址；LBA（Logical Block Addressing）是現代的線性定址。int 13h 兩種都支援（AH=02h 用 CHS，AH=42h 用 LBA）。Ch 9 詳述。

### int 0x15 — Misc Services（雜項）

```asm
; int 15h, AX=E820h: 記憶體地圖（Ch 3 講過）
; int 15h, AX=2401h: 開啟 A20 line（Ch 2 講過的方法之一）
mov ax, 0x2401
int 0x15
```

### int 0x16 — Keyboard Services（鍵盤）

```asm
; int 16h, AH=00h: 等待並讀一個按鍵
mov ah, 0x00
int 0x16           ; AL = 按鍵的 ASCII
```

## BIOS Service 的呼叫慣例

```
BIOS 中斷的通用模式：
  1. AH（有時 AX）= 功能號
  2. 其他暫存器 = 參數
  3. int N
  4. 結果在暫存器，錯誤通常用 CF（carry flag）表示
        │
  關鍵：呼叫前要設對所有參數暫存器，
        呼叫後要檢查 CF 判斷成功/失敗
```

```asm
; 標準的「呼叫 + 錯誤檢查」模式
mov ah, 0x02       ; 設功能和參數
; ... 設其他暫存器 ...
int 0x13           ; 呼叫
jc error_handler   ; CF=1 表示錯誤，跳去處理
; CF=0 繼續正常流程
```

## 故意弄壞：忘記檢查 carry flag

```asm
; 錯誤：讀磁碟不檢查 CF
mov ah, 0x02
mov al, 10
mov dl, [boot_drive]
mov bx, 0x8000
int 0x13
; 沒檢查 CF，直接 jmp 0x8000 執行載入的東西
jmp 0x8000         ; 如果讀失敗，0x8000 是垃圾，執行 = 當機

; 正確：
int 0x13
jc disk_error      ; 失敗就處理（重試或印錯誤）
jmp 0x8000
```

BIOS service 失敗時用 CF 通知你。不檢查 CF，你會在讀取失敗時繼續執行，把垃圾資料當 code 跑，神秘當機。所有 BIOS 磁碟操作後都要 `jc`。

## SeaBIOS：QEMU 的 BIOS

QEMU 預設用 **SeaBIOS**（開源 BIOS 實作）。這對學習很好——你能讀它的原始碼看 BIOS service 怎麼實作的：

```bash
# QEMU 用 SeaBIOS（預設）
qemu-system-x86_64 -drive format=raw,file=boot.img
# 開機初期可能閃過 SeaBIOS 版本訊息

# SeaBIOS 原始碼：https://github.com/coreboot/seabios
# 想知道 int 13h 怎麼實作的？讀 SeaBIOS 的 disk.c
```

> SeaBIOS 是少數能讀原始碼的 BIOS 實作（真實 BIOS 多是專有的）。如果你好奇「int 13h 背後到底做什麼」，SeaBIOS 的原始碼給你答案。這是學習 BIOS 內部的好資源——真實主機板的 BIOS 你看不到原始碼。

## 踩雷集錦

1. **不檢查 carry flag**：BIOS service 用 CF 報錯。不檢查就在失敗時繼續執行垃圾。每個磁碟操作後 `jc`

2. **以為 BIOS service 在 protected mode 可用**：BIOS 中斷只在 real mode 工作。切到 protected mode 後不能用。策略：real mode 先載入所有東西，再切模式

3. **CHS 定址讀大磁碟**：int 13h AH=02h 用 CHS，有約 8GB 限制。讀大磁碟的高 sector 要用 LBA（AH=42h）

4. **sector 編號從 1 開始**：CHS 的 sector 號從 1 算（不是 0）。sector 1 是 boot sector，你讀 stage2 從 sector 2 開始（`cl=2`）。寫成 0 會出錯

5. **ES:BX 沒設對導致讀到錯地方**：int 13h 讀到 ES:BX。忘記設 ES 或設錯，資料載入到非預期位址（可能踩到禁區）

## 進階：BIOS 的衰落與 CSM

BIOS 是 1981 年的設計，撐了四十年，但限制太多：

```
BIOS 的根本限制：
  - 16-bit real mode（只能定址 1MB，要爬模式）
  - boot code 只有 512 bytes（要兩階段）
  - 磁碟 CHS 定址的歷史限制
  - 沒有標準化的擴充機制
  - 沒有安全開機概念
        │
  → UEFI 取代它（Ch 10）
```

過渡期，UEFI 韌體提供 **CSM**（Compatibility Support Module）——一個「在 UEFI 韌體裡模擬 BIOS」的相容層，讓老的 BIOS bootloader 還能跑。但 CSM 正在被移除（新主機板逐漸只剩純 UEFI）。

```
現代主機板的開機模式設定：
  - UEFI only（純 UEFI，最新趨勢）
  - UEFI + CSM（相容模式，能跑 BIOS bootloader）
  - Legacy / CSM only（純 BIOS 模擬，舊系統）
```

理解 CSM 能解釋一個常見現象：「為什麼新電腦裝舊系統開不了機」——可能是 CSM 被關了（純 UEFI），而舊系統的 bootloader 是 BIOS 版的。

## 動手練習

1. 寫 boot code 用 int 10h AH=0Eh 印一整行字串（迴圈印每個字元直到 null）。對比 Ch 3 直接寫 video memory 的方法

2. 寫 boot code 用 int 13h 讀磁碟的第 2 個 sector 到 `0x8000`，記得檢查 CF。在 gdb 確認 `0x8000` 有讀到的資料

3. 故意弄壞：int 13h 後不檢查 CF，故意給一個不存在的磁碟編號（如 DL=0xFF），看它讀失敗後 jmp 過去當機。加上 `jc` 修復

4. 用 int 16h 寫一個「按任意鍵繼續」的 boot code（印訊息 → int 16h 等按鍵 → 繼續）

## 本章重點整理

- BIOS 做 POST（自檢+初始化硬體）→ 照開機順序找可開機裝置（檢查 0x55AA）→ 載入 boot sector 到 0x7C00
- BIOS services 是 real mode 的「syscall」：int 10h（顯示）、int 13h（磁碟，命脈）、int 15h（記憶體/A20）、int 16h（鍵盤）
- 呼叫慣例：AH=功能號，其他暫存器=參數，CF 報錯（一定要 `jc` 檢查）
- BIOS services 只在 real mode 可用，切 protected mode 後失效（先載入再切模式）
- QEMU 用開源的 SeaBIOS，可讀原始碼學 BIOS 內部；BIOS 正被 UEFI 取代（CSM 是過渡相容層）

## 自我檢核

- [ ] 能說出 BIOS 找開機裝置的流程（讀第一個 sector、檢查 0x55AA）
- [ ] 知道 int 10h/13h/15h/16h 各提供什麼服務
- [ ] 知道 BIOS service 用什麼報錯（carry flag），為什麼一定要檢查
- [ ] 能解釋為什麼 BIOS bootloader 要「先在 real mode 載入再切模式」
- [ ] 知道 CSM 是什麼，能解釋「新電腦裝舊系統開不了機」可能的原因

## 延伸閱讀

### 官方文件

- **[OSDev Wiki: BIOS](https://wiki.osdev.org/BIOS)** 和 **[Ralf Brown's Interrupt List](http://www.ctyme.com/rbrown.htm)**
  - **讀哪裡**：BIOS 條目概覽；Ralf Brown 的中斷列表查具體中斷（int 13h/10h 的所有功能）
  - **學什麼**：所有 BIOS 中斷的完整參數；Ralf Brown's List 是 BIOS 中斷的權威字典
  - **前提**：無

- **[SeaBIOS source code](https://github.com/coreboot/seabios)**
  - **讀哪裡**：`src/disk.c`（int 13h 實作）、`src/output.c`（int 10h）
  - **學什麼**：BIOS service 的實際實作；真實 BIOS 看不到原始碼，SeaBIOS 可以
  - **前提**：C + 本章概念

### 部落格 / 文章

- **[A history of the BIOS](https://www.os2museum.com/wp/)** — OS/2 Museum（多篇 BIOS 歷史文章）
  - **這篇說什麼**：BIOS 的演進、各種歷史限制的由來
  - **讀哪裡**：搜尋 BIOS、int 13h、boot 相關文章
  - **為什麼值得讀**：深入的 BIOS 歷史考據，理解為什麼有這些限制

→ [Ch 5 MBR 與 boot sector](./05-mbr-boot-sector.md)
