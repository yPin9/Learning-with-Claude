# Ch 9 — 兩階段 bootloader 與磁碟讀取

> **目標**：理解為什麼 512 bytes 不夠、兩階段 bootloader 的設計（stage1 載入 stage2）、用 BIOS int 13h 讀磁碟（CHS vs LBA）、以及 stage1 把控制權交給 stage2 的完整流程。這是 BIOS 線通往「能載入真正 kernel」的橋樑。

> **環境**：nasm，QEMU，gdb。承接 Ch 4（int 13h）、Ch 5（MBR）、Ch 6-8（boot sector、模式切換）。

## 為什麼需要兩階段？

你寫過 boot sector（Ch 6）、切過模式（Ch 7-8）。但所有這些 code 加起來，加上 GDT、頁表設定，很快就超過 512 bytes（實際只有 446 bytes 可用，Ch 5）。而要載入真正的 Linux kernel、解析檔案系統、提供開機選單——這些需要的 code 是幾十 KB 到 MB 級。

512 bytes 根本不夠。解法：**兩階段**。stage1（boot sector，512B）做的唯一重要事——用 int 13h 把更大的 stage2 從磁碟載入記憶體，跳過去。stage2 不受 512B 限制，做真正的工作。

```
512 bytes 的困境：
  GDT 定義        ~30 bytes
  模式切換 code    ~80 bytes
  頁表設定         ~60 bytes
  印字、錯誤處理    ~100 bytes
  partition table  64 bytes（佔用）
  signature        2 bytes（佔用）
        │
  加起來逼近甚至超過 446 bytes 可用空間
        │
  → 載入真正 kernel？門都沒有。必須兩階段
```

## 先建立直覺：stage1 是個「載入器的載入器」

```
兩階段 bootloader：

  ┌─ stage1（512B boot sector）─────────┐
  │  唯一任務：用 int 13h 從磁碟讀        │
  │  stage2 到記憶體，jmp 過去            │
  │  （加上必要的搭舞台）                 │
  └──────────────┬───────────────────────┘
                 │ jmp
  ┌─ stage2（不受 512B 限制）────────────┐
  │  真正的工作：                         │
  │  - 切模式（protected/long）           │
  │  - 解析檔案系統                       │
  │  - 載入 kernel + initramfs            │
  │  - 提供開機選單                       │
  │  - 把控制權交給 kernel                │
  └───────────────────────────────────────┘
```

stage1 極簡（就是個「把 stage2 搬進記憶體」的搬運工），複雜度全在 stage2。GRUB（Ch 18）就是這個架構的工業級版本——它的 stage1 在 MBR，更大的 core.img 在 MBR gap 或檔案系統。

## int 13h 讀磁碟：CHS

stage1 用 int 13h 讀 stage2。最基本的是 CHS（Cylinder-Head-Sector）定址（Ch 4 提過）：

```asm
; stage1：用 int 13h CHS 讀 stage2
; 假設 stage2 在磁碟的 sector 2 開始（sector 1 是 boot sector）
load_stage2:
    mov ah, 0x02        ; 功能：讀 sector
    mov al, 10          ; 讀 10 個 sector（5KB，看 stage2 多大）
    mov ch, 0           ; cylinder 0
    mov cl, 2           ; sector 2 開始（sector 1 是我們 stage1，從 1 算起）
    mov dh, 0           ; head 0
    mov dl, [boot_drive]; 開機磁碟（Ch 2 存的）
    mov bx, STAGE2_ADDR ; 讀到 ES:BX（目標位址）
    int 0x13
    jc disk_error       ; CF=1 出錯（一定要檢查！Ch 4）

    ; 確認讀的 sector 數對（al = 實際讀到的數量）
    cmp al, 10
    jne disk_error

    jmp STAGE2_ADDR     ; 跳到 stage2 執行

disk_error:
    mov si, err_msg
    call print_string   ; Ch 6 的印字副程式
    jmp $

STAGE2_ADDR equ 0x8000  ; stage2 載入到 0x8000
err_msg: db "Disk read error!", 0
boot_drive: db 0
```

CHS 定址的問題：cylinder/head/sector 各有位數限制，最大約 8GB（更精確地說，CHS 的編碼方式限制可定址範圍）。讀磁碟靠後的 sector（大磁碟）會超出 CHS 範圍。

## int 13h 讀磁碟：LBA（現代）

LBA（Logical Block Addressing）用線性的 sector 編號（從 0 算），沒有 CHS 的幾何限制。int 13h 的 extended read（AH=42h）用 LBA：

```asm
; stage1：用 int 13h extended read（LBA）讀 stage2
; 需要一個 Disk Address Packet（DAP）結構
load_stage2_lba:
    mov ah, 0x42        ; 功能：extended read（LBA）
    mov dl, [boot_drive]
    mov si, dap         ; SI 指向 DAP 結構
    int 0x13
    jc disk_error
    jmp STAGE2_ADDR

; Disk Address Packet（DAP）—— LBA 讀取的參數結構
align 4
dap:
    db 0x10             ; DAP 大小（16 bytes）
    db 0                ; 保留（0）
    dw 10               ; 要讀幾個 sector
    dw STAGE2_ADDR      ; 目標 offset
    dw 0                ; 目標 segment（ES）
    dq 1                ; 起始 LBA（從 0 算，1 = 第二個 sector = stage2）
```

DAP 是個結構，描述「讀幾個 sector、到哪、從哪個 LBA」。LBA 用 64-bit（`dq`），能定址巨大磁碟，沒有 CHS 的 8GB 限制。

```
CHS vs LBA：
  CHS：cylinder/head/sector 三維，幾何限制，~8GB 上限
       int 13h AH=02h
  LBA：線性 sector 編號（0,1,2...），64-bit，無實際上限
       int 13h AH=42h（extended），用 DAP 結構
        │
  現代一律用 LBA。CHS 只在很舊的 BIOS 或相容性場景
```

> 現代 bootloader 用 LBA。CHS 是歷史包袱（cylinder/head/sector 對應實體磁碟幾何，但現代磁碟早就不是那個物理結構了，CHS 純粹是相容性介面）。LBA 把磁碟看成「一串 sector」，簡單且無限制。如果你的 stage2 或 kernel 在磁碟靠後的位置，必須用 LBA。

## 完整的兩階段流程

```
磁碟佈局：
  Sector 0 (LBA 0): stage1（boot sector, 512B, 含 signature）
  Sector 1+ (LBA 1+): stage2（任意大小）
  後面: kernel, initramfs, 檔案系統...

執行流程：
  1. BIOS 載 stage1（sector 0）到 0x7C00，jmp
  2. stage1 搭舞台（segment, stack, 存 DL）
  3. stage1 用 int 13h（LBA）讀 stage2（sector 1+）到 0x8000
  4. stage1 jmp 0x8000（交棒給 stage2）
  5. stage2 做真正的事（切模式、載入 kernel...）
```

組裝成磁碟 image：

```bash
# stage1.asm（512B boot sector）+ stage2.asm（更大）
nasm -f bin stage1.asm -o stage1.bin   # 正好 512 bytes
nasm -f bin stage2.asm -o stage2.bin   # 任意大小

# 組成磁碟 image：stage1 在 sector 0，stage2 在 sector 1+
cat stage1.bin stage2.bin > disk.img
# 或更精確地用 dd 放到指定 sector
dd if=stage1.bin of=disk.img bs=512 count=1 conv=notrunc
dd if=stage2.bin of=disk.img bs=512 seek=1 conv=notrunc

qemu-system-x86_64 -drive format=raw,file=disk.img
```

## 故意弄壞：讀的 sector 數不夠

```asm
; stage2 其實有 8KB（16 sectors），但 stage1 只讀 4 sectors
mov ah, 0x02
mov al, 4           ; 只讀 4 sectors（2KB）—— stage2 不完整！
; ...
int 0x13
jmp STAGE2_ADDR     ; 跳過去，但 stage2 後半沒載入
                    ; 執行到後半 = 執行未載入的垃圾 → 當機
```

如果 stage2 比你讀的 sector 數大，後半沒被載入，stage1 跳過去執行到後半就是垃圾。要確保讀的 sector 數 ≥ stage2 的大小。常見做法：stage1 讀「足夠多」的 sector（寧可多讀），或在 stage2 開頭記錄自己的大小讓 stage1 知道讀多少。

```
debug 這個問題：
  gdb 在 jmp STAGE2_ADDR 後單步，看執行到 stage2 哪裡開始變垃圾
  → 那裡就是「讀取不足」的邊界
```

## 踩雷集錦

1. **讀的 sector 數 < stage2 大小**：stage2 後半沒載入，跳過去執行垃圾。讀足夠多 sector（或讓 stage1 知道 stage2 大小）

2. **不檢查 carry flag**：int 13h 失敗用 CF 報錯（Ch 4）。不檢查就在讀失敗時跳到沒載入的位址。一定 `jc`

3. **CHS sector 從 1 算，LBA 從 0 算**：CHS 的 `cl`（sector）從 1 開始（sector 1 是 boot sector），LBA 從 0 開始（LBA 0 是 boot sector）。混淆會讀錯位置

4. **CHS 讀大磁碟靠後 sector**：CHS 約 8GB 限制。stage2/kernel 在大磁碟靠後位置要用 LBA

5. **ES:BX 目標位址踩到禁區**：stage2 載入位址要在可用記憶體（Ch 3），避開 0x7C00（stage1 自己）、video memory、BIOS 區

6. **DAP 結構欄位填錯**：LBA 讀取的 DAP 結構欄位順序和大小要精確（size byte、sector 數、目標 segment:offset、64-bit LBA）。填錯 int 13h 失敗

## 進階：bootloader 與檔案系統

我們的 stage2 是「磁碟原始 sector」——stage1 直接讀固定 LBA。但真實 bootloader（GRUB）更進階：它能**解析檔案系統**，從 `/boot/vmlinuz` 這種路徑載入 kernel，而非寫死 sector 號。

```
從「讀固定 sector」到「讀檔案系統路徑」：
  我們的做法：stage1 讀 LBA 1（寫死位置）
        │  問題：kernel 換位置、更新版本，LBA 就變了，要重寫 bootloader
        ▼
  GRUB 的做法：stage2 內建檔案系統驅動（ext4/FAT/...）
        │  能解析 /boot/vmlinuz 路徑，不管它在磁碟哪
        ▼
  代價：檔案系統驅動 code 很大（所以 GRUB 的 core.img 要放 MBR gap，Ch 19）
```

這就是為什麼 GRUB 的 stage2（core.img）這麼大——它包含 ext4、FAT 等檔案系統的驅動，才能用路徑載入 kernel。我們的兩階段 demo 用寫死 sector 簡化，但理解「真實 bootloader 要解析檔案系統」是通往 Ch 18（GRUB 架構）的橋樑。

Ch 16（UEFI 載入 kernel）和 Ch 20（kernel handover protocol）會講「載入 kernel 之後怎麼把控制權正確交給它」——那是兩階段流程的終點。

## 動手練習

1. 寫一個兩階段 bootloader：stage1（512B）用 int 13h LBA 讀 stage2，stage2 印一個訊息證明它被載入執行。組成 disk.img 在 QEMU 跑

2. 把 stage1 從 CHS 改成 LBA（用 DAP 結構），對比兩種讀法。用 gdb 確認都讀到 stage2

3. 故意弄壞：讓 stage2 比 stage1 讀的 sector 數大（stage2 塞一堆 nop 到超過讀取量），看跳過去執行到後半垃圾當機。增加讀取量修復

4. 整合 Ch 6-8：讓 stage2 做模式切換（real → protected → long），在 64-bit 印字。這就是一個完整的「能進 64-bit 的兩階段 bootloader」（練習 A 會做完整版）

## 本章重點整理

- 512 bytes（實際 446B 可用）不夠載入真正 kernel → 兩階段：stage1 載入 stage2，stage2 做真正的事
- stage1 用 int 13h 從磁碟讀 stage2 到記憶體，jmp 過去；stage2 不受 512B 限制
- CHS（AH=02h，~8GB 限制，sector 從 1 算）vs LBA（AH=42h，64-bit 無限制，從 0 算，用 DAP）—現代用 LBA
- 讀的 sector 數必須 ≥ stage2 大小，否則後半沒載入執行垃圾；一定檢查 carry flag
- 真實 bootloader（GRUB）的 stage2 內建檔案系統驅動，能用路徑載入 kernel（所以 core.img 很大）

## 自我檢核

- [ ] 能解釋為什麼需要兩階段 bootloader（512B 不夠）
- [ ] 知道 CHS 和 LBA 的差別，以及為什麼現代用 LBA
- [ ] 能用 int 13h（CHS 或 LBA）讀磁碟並檢查 carry flag
- [ ] 知道「讀取 sector 數不足」會怎樣，如何避免
- [ ] 能解釋為什麼 GRUB 的 stage2（core.img）這麼大（內建檔案系統驅動）

## 延伸閱讀

### 官方文件

- **[OSDev Wiki: ATA in x86 RealMode (PIO)](https://wiki.osdev.org/ATA_in_x86_RealMode_(PIO))** 和 **[Disk access using the BIOS (INT 13h)](https://wiki.osdev.org/Disk_access_using_the_BIOS_(INT_13h))**
  - **讀哪裡**：int 13h 的 CHS 和 LBA（extended）讀取，DAP 結構
  - **學什麼**：磁碟讀取的完整細節、DAP 格式、錯誤碼
  - **前提**：本章

### 部落格 / 文章

- **[A two-stage bootloader from scratch](https://wiki.osdev.org/Rolling_Your_Own_Bootloader)** — OSDev
  - **這篇說什麼**：自製兩階段 bootloader 的完整指引
  - **讀哪裡**：two-stage 那節
  - **為什麼值得讀**：把 stage1/stage2 的分工和磁碟讀取串起來

→ [練習 A：64-bit 兩階段 bootloader](./practice-a-64bit-bootloader.md)
