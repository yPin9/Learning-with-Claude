# 練習 A — 64-bit 兩階段 bootloader

> **目標**：整合 Ch 2–9 的所有 BIOS 線知識，從零寫一個完整的兩階段 bootloader：stage1（512B boot sector）用 LBA 載入 stage2，stage2 完成 real → protected → long mode 的完整切換，最後在 64-bit long mode 印出訊息。完成後你親手把 CPU 從上電的 16-bit real mode 一路推到 64-bit，這是 BIOS 開機的完整縮影。

## 背景與動機

你學了 CPU 上電狀態（Ch 2）、記憶體佈局（Ch 3）、BIOS services（Ch 4）、MBR（Ch 5）、寫 boot sector（Ch 6）、兩次模式切換（Ch 7-8）、兩階段架構（Ch 9）。這個練習把它們全部組裝成一個能跑的東西。

真實 bootloader（GRUB）做的核心事情，你這個練習都做了：從 boot sector 開始、載入更多 code、把 CPU 帶到 64-bit。差別只在 GRUB 還會解析檔案系統、載入真正的 kernel——但「把 CPU 從 real mode 帶到能跑 64-bit kernel 的狀態」這個核心，你親手做了。

## 任務規格

寫一個兩階段 bootloader，達成：

| 階段 | 要求 |
|---|---|
| stage1（512B）| 搭舞台（segment/stack/存 DL）、用 int 13h LBA 讀 stage2 到記憶體、跳過去 |
| stage2 | 開 A20 → 切 protected mode（GDT + CR0.PE + far jump）→ 建頁表 → 切 long mode（PAE/CR3/LME/PG + 64-bit far jump）|
| 64-bit | 在 long mode 直接寫 video memory 印出 "Reached 64-bit long mode!" |

**驗收標準**：
- `qemu-system-x86_64` 開機，畫面印出 64-bit 訊息
- stage1 正好 512 bytes，結尾 `55 aa`
- 用 gdb 能觀察到 CR0.PE → CR0.PG 的變化，以及最後在 64-bit 執行
- 每個 BIOS 磁碟操作檢查 carry flag
- 漏掉任何一步（far jump、頁表、A20）都能用 `qemu -d int -no-reboot` 看到 triple fault

**技術限制**：
- 純 nasm assembly，不用 C
- stage1 用 LBA（int 13h AH=42h），不用 CHS
- 用 2MB huge page 做 identity map（簡化頁表）

## 期望輸出範例

```
$ make run
（QEMU 視窗左上角顯示）
Stage 1: loading stage 2...
Stage 2: switching modes...
Reached 64-bit long mode!
```

```
邊界情況：漏掉 long mode 的頁表設定
$ make run-broken
（QEMU 不斷重啟，或 -d int 顯示）
check_exception old: 0x... new 0xe   ← page fault
...triple fault → reset
```

## 如果你卡住了

1. 分階段驗證：先讓 stage1 載入 stage2 並讓 stage2 印一個字（還在 real mode），確認載入正確，再加模式切換
2. 模式切換的順序：real → protected（Ch 7：cli/A20/lgdt/PE/far jump）→ 建頁表 → long（Ch 8：PAE/CR3/LME/PG/64-bit far jump）
3. 兩個 far jump 都不能漏（一個進 protected，一個進 long）
4. 頁表要 4KB 對齊，identity map 要涵蓋 stage2 執行的位址
5. 用 gdb 在每個關鍵點（set PE、set PG、兩個 far jump）下中斷，逐步確認
6. 卡住時用 `qemu -d int -no-reboot` 看是哪種 fault（GP fault？page fault？），定位問題

## 實作步驟建議

### Step 1：stage1 載入 stage2（real mode 驗證）
先讓 stage1 用 LBA 讀 stage2，stage2 在 real mode 印字，確認載入鏈正確。

### Step 2：stage2 切到 protected mode
加 GDT、開 A20、設 CR0.PE、far jump、印 32-bit 訊息（video memory）。

### Step 3：stage2 建頁表
建 PML4/PDPT/PD（2MB huge page identity map）。

### Step 4：stage2 切到 long mode
PAE/CR3/LME/PG + 64-bit far jump，印 64-bit 訊息。

### Step 5：整合 + gdb 驗證 + 故意弄壞測試

## 完整參考解答

**寫完再看！不要偷看。**

<details>
<summary>stage1.asm（512B boot sector）</summary>

```asm
; stage1.asm — 載入 stage2 並跳過去
bits 16
org 0x7c00

STAGE2_ADDR equ 0x8000      ; stage2 載入位址
STAGE2_SECTORS equ 16       ; stage2 佔幾個 sector（寧可多讀）

start:
    cli
    ; 搭舞台
    mov [boot_drive], dl
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7c00
    sti

    ; 印訊息
    mov si, msg_load
    call print

    ; 用 LBA 讀 stage2
    mov si, dap
    mov ah, 0x42
    mov dl, [boot_drive]
    int 0x13
    jc disk_error

    ; 跳到 stage2
    jmp STAGE2_ADDR

disk_error:
    mov si, msg_err
    call print
    jmp $

; 印字串（int 10h teletype）
print:
    push ax
.loop:
    lodsb
    test al, al
    jz .done
    mov ah, 0x0e
    int 0x10
    jmp .loop
.done:
    pop ax
    ret

; Disk Address Packet（LBA 讀取）
align 4
dap:
    db 0x10                 ; DAP 大小
    db 0                    ; 保留
    dw STAGE2_SECTORS       ; 讀幾個 sector
    dw STAGE2_ADDR          ; 目標 offset
    dw 0                    ; 目標 segment
    dq 1                    ; 起始 LBA（1 = 第二個 sector）

msg_load: db "Stage 1: loading stage 2...", 0x0d, 0x0a, 0
msg_err:  db "Disk error!", 0x0d, 0x0a, 0
boot_drive: db 0

times 510-($-$$) db 0
dw 0xaa55
```

</details>

<details>
<summary>stage2.asm（模式切換到 64-bit）</summary>

```asm
; stage2.asm — real → protected → long mode
bits 16
org 0x8000                  ; stage2 載入到 0x8000

stage2_start:
    ; 印 16-bit 訊息（還能用 BIOS）
    mov si, msg_stage2
    call print16

    ; === 開 A20 ===
    in al, 0x92
    or al, 2
    out 0x92, al

    ; === 切 protected mode ===
    cli
    lgdt [gdt32_descriptor]
    mov eax, cr0
    or eax, 1
    mov cr0, eax
    jmp CODE32_SEG:pm_start  ; far jump 進 protected mode

; --- 16-bit 印字 ---
print16:
    push ax
.l: lodsb
    test al, al
    jz .d
    mov ah, 0x0e
    int 0x10
    jmp .l
.d: pop ax
    ret

bits 32
pm_start:
    ; 設 data segment
    mov ax, DATA32_SEG
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov esp, 0x90000

    ; 印 32-bit 訊息（直接寫 video memory）
    mov esi, msg_pm
    mov edi, 0xb8000
    call print32

    ; === 建頁表（identity map 前 2MB，2MB huge page）===
    ; PML4=0x1000, PDPT=0x2000, PD=0x3000
    mov edi, 0x1000
    mov cr3, edi
    xor eax, eax
    mov ecx, 4096 * 3 / 4
    rep stosd
    mov edi, 0x1000
    mov dword [edi], 0x2000 | 0b11      ; PML4[0] → PDPT
    mov dword [0x2000], 0x3000 | 0b11   ; PDPT[0] → PD
    mov dword [0x3000], 0b10000011      ; PD[0] → 2MB huge page (phys 0)

    ; === 切 long mode ===
    mov eax, cr4
    or eax, 1 << 5          ; PAE
    mov cr4, eax

    mov ecx, 0xC0000080     ; EFER MSR
    rdmsr
    or eax, 1 << 8          ; LME
    wrmsr

    mov eax, cr0
    or eax, 1 << 31         ; PG
    or eax, 1              ; PE
    mov cr0, eax

    lgdt [gdt64_descriptor]
    jmp CODE64_SEG:lm_start ; far jump 進 long mode

; --- 32-bit 印字（video memory）---
print32:
    push eax
.l: lodsb
    test al, al
    jz .d
    mov [edi], al
    mov byte [edi+1], 0x0f
    add edi, 2
    jmp .l
.d: pop eax
    ret

bits 64
lm_start:
    ; 印 64-bit 訊息
    mov rsi, msg_lm
    mov rdi, 0xb8000 + 160*2    ; 第 3 行
.l: lodsb
    test al, al
    jz .d
    mov [rdi], al
    mov byte [rdi+1], 0x0a      ; 綠字
    add rdi, 2
    jmp .l
.d:
    hlt
    jmp $

; === GDT (32-bit protected mode) ===
gdt32:
    dq 0
gdt32_code:
    dw 0xffff
    dw 0x0
    db 0x0
    db 10011010b
    db 11001111b
    db 0x0
gdt32_data:
    dw 0xffff
    dw 0x0
    db 0x0
    db 10010010b
    db 11001111b
    db 0x0
gdt32_end:
gdt32_descriptor:
    dw gdt32_end - gdt32 - 1
    dd gdt32
CODE32_SEG equ gdt32_code - gdt32
DATA32_SEG equ gdt32_data - gdt32

; === GDT (64-bit long mode) ===
gdt64:
    dq 0
gdt64_code:
    dq (1<<43) | (1<<44) | (1<<47) | (1<<53)
gdt64_data:
    dq (1<<44) | (1<<47) | (1<<41)
gdt64_end:
gdt64_descriptor:
    dw gdt64_end - gdt64 - 1
    dq gdt64
CODE64_SEG equ gdt64_code - gdt64
DATA64_SEG equ gdt64_data - gdt64

msg_stage2: db "Stage 2: switching modes...", 0x0d, 0x0a, 0
msg_pm:     db "32-bit protected mode OK", 0
msg_lm:     db "Reached 64-bit long mode!", 0
```

</details>

<details>
<summary>Makefile</summary>

```makefile
ASM  := nasm
QEMU := qemu-system-x86_64

all: disk.img

stage1.bin: stage1.asm
	$(ASM) -f bin $< -o $@
stage2.bin: stage2.asm
	$(ASM) -f bin $< -o $@

disk.img: stage1.bin stage2.bin
	dd if=/dev/zero of=disk.img bs=512 count=64 2>/dev/null
	dd if=stage1.bin of=disk.img bs=512 count=1 conv=notrunc 2>/dev/null
	dd if=stage2.bin of=disk.img bs=512 seek=1 conv=notrunc 2>/dev/null

run: disk.img
	$(QEMU) -drive format=raw,file=disk.img

debug: disk.img
	$(QEMU) -drive format=raw,file=disk.img -s -S

# 看 fault（debug triple fault 用）
faults: disk.img
	$(QEMU) -drive format=raw,file=disk.img -d int -no-reboot

clean:
	rm -f *.bin disk.img

.PHONY: all run debug faults clean
```

```bash
make run       # 看三行訊息（16-bit 載入、32-bit、64-bit）
make debug     # gdb 模式
make faults    # 看 fault（弄壞時用）
```

**解答說明**：

- **兩個 far jump**：一個進 protected mode（`jmp CODE32_SEG:pm_start`），一個進 long mode（`jmp CODE64_SEG:lm_start`）。兩個都不能漏（Ch 7/8 的核心雷）
- **兩個 GDT**：protected mode 用 32-bit GDT（gdt32），long mode 用 64-bit GDT（gdt64，靠 L bit）。切 long mode 前要 `lgdt [gdt64_descriptor]`
- **頁表用 2MB huge page**：PD entry 設 huge page bit（0x80），直接 map 2MB，省掉第 1 級 PT（Ch 8）
- **印字方法隨模式變**：16-bit 用 int 10h（BIOS），32/64-bit 直接寫 video memory（BIOS 中斷在 protected mode 失效，Ch 4/7）
- **stage1 寧可多讀**：讀 16 sectors（8KB），確保涵蓋整個 stage2（Ch 9 的「讀取不足」雷）

</details>

## 測試用案例

| 操作 | 預期 | 驗證 |
|---|---|---|
| `make run` | 三行訊息（16/32/64-bit）| 完整模式切換 |
| `ls -l stage1.bin` | 512 bytes | boot sector 大小 |
| `xxd stage1.bin \| tail -1` | 結尾 55 aa | signature |
| gdb 看 CR0.PE | set 後 bit 0 = 1 | protected mode |
| gdb 看 CR0.PG | set 後 bit 31 = 1 | long mode |
| 漏 long far jump，`make faults` | triple fault | far jump 必要性 |
| 漏頁表，`make faults` | page fault → triple fault | 分頁強制 |

## 延伸挑戰（加分）

- **挑戰一**：讓 stage1 從 stage2 的開頭讀取「stage2 大小」（stage2 開頭放一個 word 記錄自己佔幾 sector），動態決定讀多少，而非寫死 16 sectors

- **挑戰二**：在 long mode 設一個簡單的 IDT（中斷描述符表），裝一個 page fault handler，故意觸發 page fault（存取沒 map 的位址），看 handler 被呼叫（而非 triple fault）

- **挑戰三**：identity map 擴大到 1GB（用 1GB huge page，或多個 2MB entry），驗證能存取更大記憶體

- **挑戰四**：在 64-bit 載入一個簡單的「kernel」（另一段 code 放在磁碟更後面的 sector），用 long mode 的方式 jmp 過去執行——這就是 bootloader 載入 kernel 的雛形（通往 Ch 16/20）

## 自我檢核

- [ ] 能不看參考，從零寫出兩階段 + 兩次模式切換的 bootloader
- [ ] 理解兩個 far jump 各在哪、為什麼都不能漏
- [ ] 知道印字方法為什麼隨模式改變（real mode int 10h，protected/long 寫 video memory）
- [ ] 能用 gdb 追蹤 CR0.PE → CR0.PG 的完整過程
- [ ] 能用 `qemu -d int` debug triple fault，定位是漏了哪一步

→ [Ch 10 UEFI 是什麼、為什麼取代 BIOS](./10-uefi-overview.md)
