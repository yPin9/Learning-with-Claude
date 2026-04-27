# Ch 7 — 切到 protected mode (GDT / A20 / CR0)

> 目標：手把手把上一章的 boot sector 從 real mode 切到 32-bit protected mode，全部 asm 解釋。

## 我們在哪裡

第 3 階段 (Bootloader) 的中段。bootloader 必須切到 32-bit 才能跑現代 OS。

## 切換清單

切 protected mode 的最小步驟（**順序不能換**）：

1. 關中斷 (`cli`)
2. 開 A20 line
3. 設 GDT 並 `lgdt` 載入
4. `mov cr0, eax` 設 CR0.PE = 1
5. `jmp far` 到 32-bit segment（同時 flush pipeline）
6. （新 segment 內）重設 DS、ES、SS 等 segment register

## 第 1 步：`cli`

```asm
cli     ; 關中斷
```

切換期間 IDT 還是 BIOS 的、IVT 還在 `0x000`，但下一步起 segment 跟保護機制會大改。中斷打進來會讓 CPU 用半舊半新的狀態跳到 BIOS handler，幾乎一定 crash。所以先關。

## 第 2 步：開 A20

A20 是 Ch 2 講過的歷史包袱：第 21 條位址線預設關閉，要進 protected mode 必須打開。

最簡單的方法是 **Fast A20** (System Control Port A, port `0x92`)：

```asm
in al, 0x92
or al, 2        ; bit 1 = A20 enable
out 0x92, al
```

警告：`port 0x92` 不是所有平台都有，純 BIOS 機器有，現代 chipset 也有。比較保險的做法是嘗試三種方法（鍵盤控制器、INT 15h, AX=2401h、port 0x92），第一個成功就停。但 QEMU 上 port 0x92 一定行，我們先用這個。

## 第 3 步：設 GDT

GDT (Global Descriptor Table) 是 protected mode 描述每個 segment 的表。每個 entry 8 bytes。

最小需求：3 個 entry — null descriptor + code segment + data segment。

```asm
gdt_start:
    ; entry 0: null descriptor
    dq 0

    ; entry 1: code segment, base=0, limit=4GB, ring 0, executable
    dw 0xFFFF       ; limit 0:15
    dw 0x0000       ; base 0:15
    db 0x00         ; base 16:23
    db 10011010b    ; access: present|ring0|code|exec|read
    db 11001111b    ; flags: 4KB granularity|32-bit | limit 16:19
    db 0x00         ; base 24:31

    ; entry 2: data segment, base=0, limit=4GB, ring 0, writable
    dw 0xFFFF
    dw 0x0000
    db 0x00
    db 10010010b    ; access: present|ring0|data|writable
    db 11001111b
    db 0x00
gdt_end:

gdt_descriptor:
    dw gdt_end - gdt_start - 1  ; size (limit)
    dd gdt_start                ; linear base
```

幾個細節：

**entry 0 (null) 必填零**。CPU 不允許用 null segment，存在純粹是為了讓 segment selector 0 = 「不要選」。

**limit = `0xFFFFF` + 4KB granularity = 4GB**：limit 是 20-bit (0x00000 ~ 0xFFFFF)，搭配 granularity bit (G)。G=0 時 limit 單位是 byte，最大 1MB；G=1 時單位是 4KB page，最大 4GB。我們開 G=1。

**flat memory model**：base = 0、limit = 4GB，code 跟 data segment 完全 overlap。這就是 Linux / Windows 用的「flat memory model」 — segment 變得幾乎沒意義，全靠 paging 處理保護。

**access byte 那 8 bit**：

```
 7 6 5 4 3 2 1 0
 P│DPL│S│Type
 │ │  │ │
 │ │  │ └── 1 = code/data, 0 = system
 │ └─── ring 0 (00) / ring 3 (11)
 └────── present
```

Code: type = `1010` (executable, readable)
Data: type = `0010` (writable)

**flags byte**：

```
 7 6 5 4 3 2 1 0
 G D L AVL│Limit 16:19
```

G = 1 (4KB)、D = 1 (32-bit code)、L = 0 (不是 long mode)。

最後 **GDT descriptor**：6 bytes 結構，前 2 bytes 是 size、後 4 bytes 是 linear base。`lgdt` 讀這個。

```asm
lgdt [gdt_descriptor]
```

## 第 4 步：CR0.PE = 1

```asm
mov eax, cr0
or eax, 1           ; bit 0 = PE (Protection Enable)
mov cr0, eax
```

這一刻 CPU **進入 protected mode**。但 CS 還是舊的 real mode segment value、ip 還在 16-bit 模式 — 必須馬上 jmp far 重設 CS。

## 第 5 步：`jmp far` 到 32-bit segment

```asm
jmp 0x08:protected_mode_start
```

`0x08` 是什麼？是 GDT 的 segment selector：

```
 15            3 2 1 0
 │ Index       │TI│RPL│
```

- Index = 1 (第二個 entry，code segment)
- TI = 0 (GDT 不是 LDT)
- RPL = 0 (ring 0)

`1 << 3 = 0x08`。data segment selector = `2 << 3 = 0x10`。

`jmp far` 同時：
- 把 CS 換成 `0x08`（從 GDT entry 1 拿 base/limit/權限）
- 把 IP 設成 `protected_mode_start`
- **flush pipeline** — CPU 預先 decode 的 16-bit 指令全丟掉

之後的 code 都用 32-bit。

## 第 6 步：重設 segment register

```asm
[BITS 32]
protected_mode_start:
    mov ax, 0x10        ; data segment selector
    mov ds, ax
    mov es, ax
    mov fs, ax
    mov gs, ax
    mov ss, ax
    mov esp, 0x90000    ; 重設 stack
```

進 protected mode 後 `DS` 之類還是 real mode 的 selector。讀這些 selector 配套的 cache 還是 real mode 的 base/limit，所以 access 記憶體會出包。必須重設成 GDT 裡的 data selector `0x10`。

## 完整可執行版本

`pmode.asm`：

```asm
[BITS 16]
[ORG 0x7C00]

start:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7C00

    ; 開 A20
    in al, 0x92
    or al, 2
    out 0x92, al

    ; 載入 GDT
    lgdt [gdt_descriptor]

    ; 設 CR0.PE
    mov eax, cr0
    or eax, 1
    mov cr0, eax

    ; jmp far
    jmp 0x08:protected_mode_start

[BITS 32]
protected_mode_start:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov fs, ax
    mov gs, ax
    mov ss, ax
    mov esp, 0x90000

    ; 直接寫 VGA text buffer
    mov edi, 0xB8000
    mov esi, msg
.loop:
    lodsb
    test al, al
    jz .done
    mov ah, 0x07        ; 灰白
    mov [edi], ax
    add edi, 2
    jmp .loop
.done:
    cli
    hlt

msg: db "Hello from protected mode!", 0

gdt_start:
    dq 0
    dw 0xFFFF, 0x0000
    db 0x00, 10011010b, 11001111b, 0x00
    dw 0xFFFF, 0x0000
    db 0x00, 10010010b, 11001111b, 0x00
gdt_end:

gdt_descriptor:
    dw gdt_end - gdt_start - 1
    dd gdt_start

times 510-($-$$) db 0
dw 0xAA55
```

```bash
nasm -f bin pmode.asm -o pmode.bin
qemu-system-x86_64 -drive format=raw,file=pmode.bin
```

注意這次不能用 `-nographic`，因為我們直接寫 VGA text buffer (`0xB8000`)，那是 graphics 模式才看得到。

## VGA text buffer 是什麼

real mode 我們用 `INT 10h` 印字，protected mode BIOS INT 不能用，怎麼辦？

直接寫 `0xB8000` ~ `0xB8FA0`（80×25 字元 × 2 byte）這塊記憶體：每個字元 2 byte：低 byte 是 ASCII、高 byte 是 attribute（顏色）。

```asm
mov edi, 0xB8000
mov ax, 0x0741          ; 0x07 = 灰白, 0x41 = 'A'
mov [edi], ax           ; 印一個 'A' 到左上角
```

這比 BIOS INT 還簡單，是 protected mode boot 印字的標準做法。

## 一個常見踩雷：jmp far 寫成 jmp short

```asm
mov cr0, eax
jmp protected_mode_start    ; ❌ short jump，CS 沒變
```

CR0.PE 已經設了，但 CS 還是舊的 real mode value。下一條指令會用「以 protected mode 解讀的 real mode CS」去 access — 100% crash。

必須是 `jmp 0x08:protected_mode_start`（far jump，同時換 CS）。

## 一個常見踩雷：忘了重設 DS

進 protected mode 後馬上 `mov [edi], ax` 寫 VGA buffer，但 DS 還是 real mode 的。**寫進去的位址是錯的**。

```asm
mov ax, 0x10
mov ds, ax    ; 必填！
```

## 一個常見踩雷：GDT descriptor 用 word 而不是 dword 存 base

```asm
gdt_descriptor:
    dw gdt_end - gdt_start - 1
    dw gdt_start            ; ❌ 應該 dd（4 byte）
```

`lgdt` 讀 6 byte：2 byte size + 4 byte base。寫成 `dw` 只給 2 byte，後面被當成 base 高 16 bit 讀，整個 GDT 位址錯。

## 動手練習

**1. 跑起來**

照上面 code 跑一次，看到 "Hello from protected mode!" 就成功。

**2. 故意弄錯**

依序試：

- 把 `jmp 0x08:protected_mode_start` 改成 `jmp protected_mode_start`：會看到 QEMU triple fault reset
- 把 `mov ds, ax` 那段拿掉：寫 VGA buffer 時可能 `general protection fault`
- GDT entry 1 的 access byte 改成 `00011010b`（present bit 拿掉）：`lgdt` 沒事，但 `jmp 0x08:...` 會 #GP

每次失敗都用 `qemu-system-x86_64 -d int,cpu_reset` 看 trap log，學會看這個 log 比啥都重要。

**3. 用 GDB 追切換瞬間**

```bash
qemu-system-x86_64 -s -S -drive format=raw,file=pmode.bin
```

```
gdb
(gdb) target remote :1234
(gdb) set architecture i8086
(gdb) b *0x7c00
(gdb) c
(gdb) si        # 一條一條 step
```

step 到 `mov cr0, eax` 之後，QEMU 已經在 protected mode 但 GDB 還用 16-bit 解讀。執行 jmp far 之後：

```
(gdb) set architecture i386
(gdb) si
```

繼續 step，看 EIP 跳到 `protected_mode_start`。

## 自我檢核

- [ ] 講得出切 protected mode 的 6 個步驟順序
- [ ] 知道 GDT 為什麼最少要 3 個 entry
- [ ] 知道 segment selector `0x08` / `0x10` 怎麼算
- [ ] 知道為什麼 `mov cr0, eax` 後馬上要 `jmp far`
- [ ] 知道 VGA text buffer 在 `0xB8000`、每字元 2 byte
- [ ] 故意刪 `mov ds, ax` 看到 crash

下一章再切一次：從 protected mode 進 long mode。設 PML4、開 PAE、開 LME、開 PG。

→ [Ch 8 切到 long mode (paging / PML4 / EFER)](./08-long-mode-switch.md)
