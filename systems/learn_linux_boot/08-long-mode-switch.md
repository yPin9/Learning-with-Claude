# Ch 8 — 切到 long mode (paging / PML4 / EFER)

> 目標：把 protected mode 升級到 64-bit long mode。設 PML4 page table、開 PAE、開 LME、開 PG。

## 我們在哪裡

第 3 階段 (Bootloader) 的最後一段 mode switch。之後 CPU 跑 64-bit code，可以載 64-bit kernel。

## 切換清單

從 protected mode 切 long mode 的步驟（**順序很重要**）：

1. 確保 CR0.PG = 0（paging 關掉）
2. 在記憶體裡建好 PML4 / PDPT / PD page table
3. CR3 ← PML4 base
4. CR4.PAE = 1（開 PAE）
5. EFER.LME = 1（透過 wrmsr）
6. CR0.PG = 1（這一刻 CPU 進 IA-32e mode）
7. 重設 GDT 加一個 64-bit code segment
8. `jmp far` 到 64-bit segment

注意：步驟 6 之後 CPU 在 IA-32e **compatibility mode**（還跑 32-bit），步驟 8 jmp 進 64-bit segment 才真的是 64-bit。

## Page table 結構

long mode 的虛擬位址是 48-bit，分成 4 級 page table：

```
 48-bit virtual address:
 ┌──────┬──────┬──────┬──────┬────────────┐
 │PML4  │PDPT  │PD    │PT    │Page offset │
 │ idx  │ idx  │ idx  │ idx  │            │
 └──────┴──────┴──────┴──────┴────────────┘
   9bit   9bit   9bit   9bit    12bit
```

每級 table 一頁 (4KB)，有 512 個 entry，每個 entry 8 byte。

最簡 mapping：**前 2MB identity mapping** — 虛擬 0~2MB = 實體 0~2MB。

要做到 identity map 2MB，可以：

- 用 4KB page：要 PML4 + PDPT + PD + 一張 PT (512 個 entry)
- 用 2MB huge page：只要 PML4 + PDPT + PD（PD entry 設 PS=1）

我們用 **2MB huge page**，省一張 table。

## Page table entry 格式

每個 entry 64-bit，常用 bit：

```
 Bit  Name  Meaning
 ───  ────  ───────
 0    P     Present
 1    R/W   1 = writable
 2    U/S   1 = userspace can access
 7    PS    1 = page size (在 PD level 表示這是 2MB page)
 12-N PA    Physical address
```

最簡 entry：`0x83` = `present | writable | PS`。

## 完整 code（接 Ch 7 之後）

延續上一章的 `pmode.asm`，加上 long mode 切換：

`lmode.asm`（節錄關鍵部分，前面 real mode + protected mode 切換 code 同 Ch 7）：

```asm
[BITS 32]
protected_mode_start:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov esp, 0x90000

    ; ─────────────────────────────────
    ; 設 page table，identity map 0~2MB
    ; PML4 在 0x1000
    ; PDPT 在 0x2000
    ; PD   在 0x3000
    ; ─────────────────────────────────

    ; 清 0x1000 ~ 0x4000 (3 個 page)
    mov edi, 0x1000
    xor eax, eax
    mov ecx, (4096 * 3) / 4
    rep stosd

    ; PML4[0] → PDPT (0x2000)，flag = 0x03 (present|RW)
    mov dword [0x1000], 0x2003

    ; PDPT[0] → PD (0x3000)
    mov dword [0x2000], 0x3003

    ; PD[0] → 實體 0x000000，2MB huge page
    mov dword [0x3000], 0x000083    ; present|RW|PS

    ; CR3 ← PML4
    mov eax, 0x1000
    mov cr3, eax

    ; CR4.PAE = 1
    mov eax, cr4
    or eax, 1 << 5
    mov cr4, eax

    ; EFER.LME = 1
    mov ecx, 0xC0000080         ; MSR EFER
    rdmsr
    or eax, 1 << 8              ; LME bit
    wrmsr

    ; CR0.PG = 1
    mov eax, cr0
    or eax, 1 << 31
    mov cr0, eax

    ; 重新載 GDT，含 64-bit code segment
    lgdt [gdt64_descriptor]
    jmp 0x08:long_mode_start

[BITS 64]
long_mode_start:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax

    ; 寫 VGA buffer
    mov rdi, 0xB8000
    mov rsi, msg64
.loop:
    lodsb
    test al, al
    jz .done
    mov ah, 0x0F        ; 白
    mov [rdi], ax
    add rdi, 2
    jmp .loop
.done:
    cli
    hlt

msg64: db "Hello from long mode!", 0

; 64-bit GDT
gdt64_start:
    dq 0
    ; 64-bit code: L=1, D=0
    dw 0xFFFF
    dw 0x0000
    db 0x00
    db 10011010b
    db 10101111b        ; G=1, L=1, D=0, AVL=0, limit 16:19
    db 0x00
    ; 64-bit data
    dw 0xFFFF
    dw 0x0000
    db 0x00
    db 10010010b
    db 10101111b
    db 0x00
gdt64_end:

gdt64_descriptor:
    dw gdt64_end - gdt64_start - 1
    dd gdt64_start
```

## 逐段解說

### Page table 為什麼放在 `0x1000` / `0x2000` / `0x3000`

這些位址 4KB 對齊（page table 必須 4KB 對齊）、沒踩到 boot sector (`0x7C00`)、沒踩到 BIOS data area (`0x400`)、沒踩到 IVT (`0x000`)。

在 boot 階段這幾個低位址都是「我們可以亂用的閒置空間」。

### `rep stosd` 是什麼

`stosd` = STOre String Dword：`[ES:EDI] = EAX; EDI += 4`。`rep` 重複 ECX 次。

```asm
mov edi, 0x1000
xor eax, eax
mov ecx, 3072         ; 3 個 page * 1024 dword
rep stosd
```

= 用 0 把 `0x1000` ~ `0x3FFF` 全填零。`memset` 的 asm 版。

### Identity mapping 為什麼

切 long mode 之前 paging 關著，CPU 用線性位址（= 實體位址）執行。切 long mode **打開 paging 那一瞬間**，下一條指令的位址要從虛擬轉到實體 — 如果 mapping 不對，CPU 找不到下一條指令。

最安全的做法：**虛擬位址 = 實體位址**。這樣切換前後執行流連續。

我們 identity map 0~2MB 已經包含 boot sector (`0x7C00`)、page table (`0x1000`-`0x3FFF`)、code 跑的位置 — 切換瞬間 CPU 能從虛擬 `0x7DXX` 找到對應的實體 `0x7DXX`。

### `wrmsr` 與 EFER

EFER (Extended Feature Enable Register) 是 MSR (Model Specific Register)，編號 `0xC0000080`。MSR 用 `rdmsr` / `wrmsr` 讀寫：

```asm
mov ecx, 0xC0000080     ; MSR 編號
rdmsr                   ; EDX:EAX = MSR value (high 32:low 32)
or eax, 1 << 8          ; LME bit (Long Mode Enable)
wrmsr                   ; MSR = EDX:EAX
```

EFER 還有其他重要 bit：
- bit 0 SCE (SYSCALL Enable)
- bit 8 LME (Long Mode Enable)
- bit 10 LMA (Long Mode Active) — 唯讀，CPU 自動設
- bit 11 NXE (No-eXecute Enable)

LME 跟 LMA 的差別：LME = 1 表示「我想進 long mode」；LMA = 1 表示「真的在 long mode」。LMA 在 PG = 1 之後 CPU 才 set。

### 64-bit code segment 的 L bit

```asm
db 10101111b        ; flags: G|L|D|AVL|limit
```

L bit (bit 5 of flags) = 1 表示 「64-bit code」。**必須設**，不然 jmp far 過去會在 IA-32e compat mode 跑 32-bit code。

D bit 在 L=1 時必須 = 0。Intel 規格寫死。

### 64-bit jmp far 跟 32-bit 一樣寫

`jmp 0x08:long_mode_start` 這條指令在 32-bit code segment 裡執行，但因為 target segment 是 64-bit (L=1)，jmp 完後 CPU 進 64-bit。

## 一個常見踩雷：忘了 identity mapping

如果只開 PG 沒設 page table，下一條指令位址查表查到一片 0 (page not present)，CPU triple fault reset。QEMU 會看到不停 reboot loop。

## 一個常見踩雷：CR3 寫的不是實體位址

```asm
mov eax, [some_pointer]     ; 不是物理位址！
mov cr3, eax
```

CR3 必須是 PML4 的**實體位址**。在 boot 階段我們還沒開 paging，線性位址 = 實體位址，所以直接 `mov eax, 0x1000` 就好。一旦開了 paging，要算實體位址要透過 page table walk（雞生蛋問題）。

## 一個常見踩雷：CR4.PAE 沒先開就開 PG

順序錯了 CPU 會 #GP（general protection fault）。Intel 規定切 long mode 必須：

```
CR4.PAE = 1   →   EFER.LME = 1   →   CR0.PG = 1
```

順序倒過來會 fault。

## 完整可跑版本

把 Ch 7 完整 `pmode.asm` 跟這章的 `lmode.asm` 拼起來，512 bytes 不夠 — 已經超過 boot sector 預算了。實務上會：

- Stage 1 (boot sector) 切到 protected mode、讀 stage 2
- Stage 2 切 long mode、載 kernel

但如果你想塞在一個 binary 試試看，可以做 **multi-sector boot**：

```asm
; boot.asm
[BITS 16]
[ORG 0x7C00]

start:
    ; 用 INT 13h 讀第 2 個 sector 到 0x7E00
    mov ah, 0x02        ; read sectors
    mov al, 4           ; 4 個 sector
    mov ch, 0           ; cylinder 0
    mov cl, 2           ; sector 2
    mov dh, 0           ; head 0
    mov dl, 0x80        ; first hdd
    mov bx, 0x7E00      ; load to ES:BX
    int 0x13
    jc disk_error

    jmp 0x7E00          ; 跳到 stage 2

disk_error:
    cli
    hlt

times 510-($-$$) db 0
dw 0xAA55
```

stage 2 從 sector 2 開始放，含切 protected/long mode 的所有 code。組譯：

```bash
nasm -f bin boot.asm -o boot.bin             # 512 bytes
nasm -f bin stage2.asm -o stage2.bin         # 任意大小
cat boot.bin stage2.bin > disk.img
qemu-system-x86_64 -drive format=raw,file=disk.img
```

## 動手練習

**1. 跑起來**

把 stage 1 + stage 2 拼好，看到 "Hello from long mode!" 就成功。

**2. 用 GDB 觀察 mode 切換**

```bash
qemu-system-x86_64 -s -S -drive format=raw,file=disk.img
```

```
(gdb) target remote :1234
(gdb) b *0x7c00
(gdb) c
... step 過 protected mode 切換
(gdb) set architecture i386
... step 到設 page table、CR3、CR4、EFER
(gdb) p/x $cr0
(gdb) p/x $cr4
(gdb) p/x $efer        ; 看 LME、LMA
(gdb) si               ; 切過 jmp far
(gdb) set architecture i386:x86-64
```

每個 register 變化的瞬間記下來。**這是你一輩子最深刻的 64-bit transition 記憶**。

**3. 把 identity mapping 改成 4KB pages**

不用 huge page，建一張 PT (`0x4000`)，PD[0] → PT，PT 填 512 個 entry 各指向不同 4KB page。

對照看 huge page 跟 4KB page 的差別。

## 自我檢核

- [ ] 講得出 long mode 切換 8 步順序
- [ ] 知道 PML4 / PDPT / PD / PT 4 級結構
- [ ] 知道 2MB huge page (PS=1) 怎麼省一級
- [ ] 知道 EFER.LME 跟 EFER.LMA 差別
- [ ] 知道為什麼必須 identity mapping
- [ ] 知道 CR4.PAE → EFER.LME → CR0.PG 順序不能換

下一章看真實世界：GRUB 怎麼把上面這些事做完。

→ [Ch 9 GRUB 內部結構](./09-grub-internals.md)
