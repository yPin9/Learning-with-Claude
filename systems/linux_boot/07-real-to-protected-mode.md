# Ch 7 — 從 real mode 到 protected mode

> **目標**：逐步切換 CPU 從 16-bit real mode 到 32-bit protected mode——理解為什麼需要切換、GDT（Global Descriptor Table）的結構與作用、開啟 A20、設定 CR0.PE、far jump 重載 CS，並在 QEMU 跑起來、用 gdb 觀察模式轉變。這是本課第一個 assembly 高峰。

> **環境**：nasm，QEMU，gdb。承接 Ch 2（A20、real mode）、Ch 6（boot sector）。這是原理深挖章。

## 為什麼要切到 protected mode？

real mode（Ch 2）的限制讓它無法跑現代 OS：只能定址 1MB、沒有記憶體保護、16-bit 暫存器、segment:offset 的怪異定址。要跑 Linux kernel，CPU 必須在 protected mode（32-bit）或 long mode（64-bit，Ch 8）。

但 CPU 上電是 real mode，不會自動切換——**你的 boot code 必須手動把 CPU 升級到 protected mode**。這個切換是 x86 開機最關鍵的一段 assembly，涉及好幾個必須按正確順序做的步驟。搞懂它，你就理解了 x86 的記憶體保護機制的入口。

## 先建立直覺：protected mode 換掉了定址方式

```
Real Mode 的定址：
  segment × 16 + offset = 物理位址
  segment 暫存器直接是個數字，乘 16

Protected Mode 的定址：
  segment 暫存器變成「selector」——指向一張表（GDT）的索引
  GDT 裡的每個 entry（descriptor）描述一段記憶體：
    base（基址）、limit（大小）、權限（可讀/寫/執行、特權級）
        │
  selector → 查 GDT → 得到 base/limit/權限 → 算出位址 + 檢查權限
        │
  這就是「protected」的由來：每次記憶體存取都查表檢查權限
```

核心轉變：real mode 的 segment 是「乘 16 的數字」，protected mode 的 segment 是「指向描述符表的索引」。這個間接層讓 CPU 能做記憶體保護（檢查權限、限制範圍）。

## GDT：Global Descriptor Table

GDT 是 protected mode 的核心資料結構。它是一張表，每個 entry（segment descriptor）描述一段記憶體的屬性。

```
GDT 結構（每個 descriptor 8 bytes）：

  Entry 0: NULL descriptor（必須全 0，CPU 規定）
  Entry 1: Code segment descriptor（base, limit, 可執行...）
  Entry 2: Data segment descriptor（base, limit, 可讀寫...）
  ...
        │
  segment selector（放進 CS/DS 等）= entry 的索引 × 8 + 一些 flag
```

每個 descriptor 是 8 bytes，格式很扭曲（歷史原因，欄位被拆散）：

```
Segment Descriptor（8 bytes）的欄位（被歷史拆散到各處）：
  - base（32-bit）：segment 的起始位址（拆成 3 段散落）
  - limit（20-bit）：segment 的大小（拆成 2 段）
  - access byte：present、特權級（DPL）、type（code/data）、可讀寫/執行
  - flags：granularity（limit 單位是 byte 還是 4KB page）、32-bit/16-bit
```

我們用「flat model」——code 和 data segment 都覆蓋整個 4GB 空間（base=0, limit=0xFFFFF with 4KB granularity = 4GB）。這樣 segment 實際上「透明」（offset 就是線性位址），記憶體保護交給之後的分頁（paging）做。

```asm
; GDT 定義（flat model：code 和 data 都覆蓋 0 ~ 4GB）
gdt_start:
gdt_null:               ; Entry 0：NULL descriptor（必須全 0）
    dd 0x0
    dd 0x0

gdt_code:               ; Entry 1：Code segment（base=0, limit=4GB, 可執行可讀）
    dw 0xffff           ; limit (bits 0-15) = 0xFFFF
    dw 0x0              ; base (bits 0-15) = 0
    db 0x0              ; base (bits 16-23) = 0
    db 10011010b        ; access byte: present=1, DPL=00, type=1(code/data),
                        ;   executable=1, direction=0, readable=1, accessed=0
    db 11001111b        ; flags: granularity=1(4KB), 32-bit=1, +limit(bits16-19)=1111
    db 0x0              ; base (bits 24-31) = 0

gdt_data:               ; Entry 2：Data segment（base=0, limit=4GB, 可讀寫）
    dw 0xffff           ; limit (bits 0-15)
    dw 0x0              ; base (bits 0-15)
    db 0x0              ; base (bits 16-23)
    db 10010010b        ; access: present=1, DPL=00, type=1, executable=0,
                        ;   direction=0, writable=1, accessed=0
    db 11001111b        ; flags: granularity=1, 32-bit=1, +limit(bits16-19)
    db 0x0              ; base (bits 24-31)

gdt_end:

; GDT descriptor（告訴 CPU GDT 在哪、多大）
gdt_descriptor:
    dw gdt_end - gdt_start - 1   ; GDT 大小 - 1（limit）
    dd gdt_start                 ; GDT 的位址

; selector 常數（給 CS/DS 用）
CODE_SEG equ gdt_code - gdt_start    ; = 0x08（第 1 個 entry × 8）
DATA_SEG equ gdt_data - gdt_start    ; = 0x10（第 2 個 entry × 8）
```

> descriptor 的欄位被拆得很碎（base 散在 3 個地方、limit 散在 2 個地方）——這是 80286→80386 演進的歷史包袱（為了相容，新欄位塞進舊格式的縫隙）。你不用記每個 bit，理解「它描述 base/limit/權限」就好，實作時照範本填。flat model（base=0, limit=4GB）是最簡單的——讓 segment 透明，保護交給分頁。

## 切換步驟：按順序來

real mode → protected mode 有固定的步驟順序，錯一步就失敗：

```
切換步驟（順序不能錯）：
  1. cli              ← 關中斷（切換中不能被中斷打斷）
  2. 開啟 A20         ← 否則高位址無法存取（Ch 2）
  3. lgdt [gdt_descriptor]  ← 載入 GDT
  4. 設 CR0.PE = 1    ← 這一刻 CPU 進入 protected mode
  5. far jump 重載 CS ← 用新的 code selector，清 pipeline
  6. 設 data segment 暫存器（DS/ES/SS = DATA_SEG）
  7. 設 32-bit stack
        │
  完成後：CPU 在 32-bit protected mode
```

```asm
switch_to_pm:
    cli                     ; 1. 關中斷

    ; 2. 開啟 A20（Fast A20，Ch 2）
    in al, 0x92
    or al, 2
    out 0x92, al

    ; 3. 載入 GDT
    lgdt [gdt_descriptor]   ; 告訴 CPU GDT 的位置和大小

    ; 4. 設 CR0 的 PE bit（Protection Enable）
    mov eax, cr0
    or eax, 0x1             ; set bit 0 (PE)
    mov cr0, eax            ; ← 這一刻進入 protected mode！
                            ; 但 CS 還是舊的，要 far jump 才完全切換

    ; 5. far jump 重載 CS（用新 code selector）
    jmp CODE_SEG:init_pm    ; far jump：CS = CODE_SEG(0x08), 跳到 init_pm
                            ; 這清空 CPU 的指令 pipeline，確保用新模式解碼

bits 32                     ; 之後是 32-bit code
init_pm:
    ; 6. 設 data segment（都用 DATA_SEG）
    mov ax, DATA_SEG
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov fs, ax
    mov gs, ax

    ; 7. 設 32-bit stack
    mov ebp, 0x90000        ; stack 設在 0x90000（可用記憶體）
    mov esp, ebp

    ; 現在在 32-bit protected mode！
    ; 可以用 32-bit 暫存器、定址 4GB、有記憶體保護
    ; 但 BIOS 中斷不能用了（Ch 4）——印字要直接寫 video memory
    call print_pm_message
    jmp $

bits 32
print_pm_message:
    ; protected mode 不能用 int 10h，直接寫 video memory（0xB8000）
    mov esi, pm_msg
    mov ebx, 0xb8000        ; video memory
.loop:
    lodsb
    test al, al
    jz .done
    mov [ebx], al           ; 字元
    mov byte [ebx+1], 0x0f  ; 屬性（白字）
    add ebx, 2
    jmp .loop
.done:
    ret

pm_msg: db "Now in 32-bit protected mode!", 0
```

## 為什麼每一步都必要

逐步解釋為什麼順序不能亂：

| 步驟 | 為什麼 | 漏了會怎樣 |
|---|---|---|
| `cli` | 切換中途若被中斷，中斷處理用舊模式但 CPU 狀態半變，崩潰 | 隨機當機 |
| 開 A20 | protected mode 要定址 1MB 以上 | 高位址回繞，資料錯 |
| `lgdt` | protected mode 用 GDT 解析 selector | 沒 GDT，set PE 後第一次取指令就 triple fault |
| `CR0.PE=1` | 這個 bit 才是「進入 protected mode」的開關 | 沒設就還在 real mode |
| far jump | set PE 後 CS 還是 real mode 的舊值；far jump 用新 selector 重載 CS 並清 pipeline | CPU 用舊 CS 解碼新模式指令，崩潰 |
| 設 data seg | DS/ES/SS 還是 real mode 舊值（無效 selector）| 資料存取觸發 fault |

> **far jump 是最容易漏的關鍵**。設 `CR0.PE=1` 後 CPU 技術上進了 protected mode，但 CS 暫存器還快取著 real mode 的舊 segment 資訊，且 CPU 的指令 pipeline 裡可能有用舊模式解碼的指令。far jump（`jmp CODE_SEG:label`）用新的 code selector 重載 CS，並清空 pipeline，確保接下來的指令用 protected mode 解碼。沒有這個 far jump，CPU 行為未定義（通常崩潰）。

## 用 gdb 觀察模式切換

```bash
qemu-system-x86_64 -drive format=raw,file=boot.img -s -S &
gdb
```

```gdb
(gdb) target remote localhost:1234
(gdb) set architecture i8086
(gdb) break *0x7c00
(gdb) continue
# 單步到設 CR0 那一行
(gdb) break *<switch_to_pm 設 cr0 的位址>
(gdb) continue
(gdb) p/x $cr0           # 設之前，bit 0 = 0
(gdb) si                 # 執行 mov cr0, eax
(gdb) p/x $cr0           # bit 0 = 1 了！進入 protected mode
# far jump 後切到 32-bit
(gdb) set architecture i386   # 切 gdb 的架構解讀
(gdb) si
(gdb) info registers     # 看 32-bit 暫存器、segment selectors
```

看著 `CR0` 的 PE bit 從 0 變 1，然後 far jump 後切到 32-bit——這是理解模式切換最直接的方式。

## 故意弄壞：漏掉 far jump

```asm
; 錯誤：設 CR0.PE 後沒有 far jump，直接繼續
mov eax, cr0
or eax, 0x1
mov cr0, eax
; 沒有 jmp CODE_SEG:init_pm！
mov ax, DATA_SEG    ; 直接設 data seg
mov ds, ax          ; ← CPU 可能 triple fault（CS 還是舊的，狀態不一致）
```

漏掉 far jump，CPU 處於「PE 已設但 CS 未更新」的不一致狀態。接下來的指令可能 triple fault（CPU 連續三次 fault，QEMU 會 reset 或印錯誤）。在 QEMU 跑會看到不斷重啟或 `-d int` 顯示 triple fault。加上 `jmp CODE_SEG:init_pm` 修復。

```bash
# 用 QEMU 的 -d int 看 fault
qemu-system-x86_64 -drive format=raw,file=boot.img -d int -no-reboot
# 漏 far jump 會看到 triple fault 然後 reset
```

## 踩雷集錦

1. **漏掉 far jump**：set PE 後 CS 未更新，CPU 狀態不一致，triple fault。far jump（`jmp CODE_SEG:label`）是必須的

2. **沒先 cli**：切換中被中斷打斷，中斷處理用半變的狀態，崩潰。一定先關中斷

3. **GDT descriptor 的 limit 算錯**：`dw gdt_end - gdt_start - 1`，是「大小減 1」。寫成大小（不減 1）會多算一個 byte

4. **沒開 A20 就用高記憶體**：protected mode 定址 1MB 以上需要 A20。漏開，高位址回繞

5. **忘記重設 data segment**：far jump 只重載 CS。DS/ES/SS 還是 real mode 舊值（在 protected mode 是無效 selector）。要全部設成 DATA_SEG

6. **protected mode 還用 BIOS 中斷**：int 10h 等只在 real mode 可用。protected mode 印字要直接寫 video memory（0xB8000）

## 進階：protected mode 之後——分頁

切到 protected mode 後，記憶體保護其實有兩層：

```
x86 記憶體保護的兩層：
  1. Segmentation（分段）：GDT 的 descriptor 檢查（我們用 flat model 讓它透明）
  2. Paging（分頁）：頁表把線性位址映射到物理位址，per-page 權限
        │
  現代 OS 幾乎都用 flat segmentation + paging：
  - segment 透明（base=0, limit=4GB）
  - 真正的保護和虛擬記憶體靠 paging
```

我們的 flat model 讓 segmentation 透明（offset = 線性位址）。真正的記憶體管理（虛擬記憶體、per-page 權限）靠分頁。Ch 8 切 long mode 時**必須**先設好分頁（long mode 強制分頁）。所以 Ch 8 會建立頁表——那是切 64-bit 的前提。

protected mode 本身可以不開分頁（純 segmentation），但 long mode 不行。這是 Ch 7 和 Ch 8 的關鍵差異：protected mode 分頁可選，long mode 分頁強制。

## 動手練習

1. 跑通本章的切換，看到 video memory 印出 "Now in 32-bit protected mode!"。用 gdb 觀察 CR0.PE 從 0 變 1

2. 用 gdb 在 far jump 前後看 `cs` 暫存器的值，理解 far jump 如何重載 CS（從 real mode 的值變成 CODE_SEG=0x08）

3. 故意漏掉 far jump，用 `qemu -d int -no-reboot` 看 triple fault。加回去修復

4. 故意漏掉開 A20，嘗試在 protected mode 寫 1MB 以上的位址，看資料是否回繞（需要對照寫低位址確認）

## 本章重點整理

- real mode → protected mode：segment 從「乘 16 的數字」變成「指向 GDT 的 selector」，CPU 每次存取查表檢查權限
- GDT 是 descriptor 表，每個 entry 描述一段記憶體（base/limit/權限）；flat model 讓 segment 透明（base=0, limit=4GB）
- 切換步驟（順序固定）：cli → 開 A20 → lgdt → 設 CR0.PE → far jump 重載 CS → 設 data seg → 設 stack
- far jump 是最易漏的關鍵：set PE 後必須 far jump 重載 CS、清 pipeline，否則 triple fault
- protected mode 後 BIOS 中斷失效，印字要直接寫 video memory；分頁是可選的（long mode 才強制）

## 自我檢核

- [ ] 能解釋 real mode 和 protected mode 的 segment 定址差異（數字 vs selector 查表）
- [ ] 知道 GDT 是什麼、flat model 為什麼讓 segment 透明
- [ ] 能不看範本說出切換的步驟順序，並解釋每步為什麼必要
- [ ] 能解釋 far jump 為什麼必須（CS 重載 + 清 pipeline），漏了會怎樣
- [ ] 知道 protected mode 後為什麼不能用 BIOS 中斷

## 延伸閱讀

### 官方文件

- **[Intel SDM Vol 3, Ch 3 (Protected-Mode Memory Management)](https://www.intel.com/sdm)** 和 Ch 9.9（Mode Switching）
  - **讀哪裡**：3.4（segment descriptors）、9.9.1（real → protected）
  - **學什麼**：GDT/descriptor 的權威定義、模式切換的官方步驟
  - **前提**：本章建立的概念，再讀 SDM 會懂每個 bit

- **[OSDev Wiki: Protected Mode](https://wiki.osdev.org/Protected_Mode)** 和 **[GDT](https://wiki.osdev.org/Global_Descriptor_Table)**
  - **讀哪裡**：兩個條目，特別是 GDT 的 descriptor 格式圖
  - **學什麼**：descriptor 每個 bit 的意義、切換的完整 code
  - **前提**：本章

### 部落格 / 文章

- **[Writing a Bootloader Part 2](https://3zanders.co.uk/2017/10/16/writing-a-bootloader2/)** — Alex Parker
  - **這篇說什麼**：實作 real → protected mode 切換，逐步解釋
  - **讀哪裡**：整篇
  - **為什麼值得讀**：和本章互補的另一個視角，code 清晰

→ [Ch 8 進入 long mode（64-bit）](./08-long-mode.md)
