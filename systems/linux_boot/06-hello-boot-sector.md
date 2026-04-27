# Ch 6 — 動手：自製 hello boot sector

> 目標：寫一個 512 bytes 的 boot sector，BIOS 載入後印一行字。從這刻起你不再只是讀者，你寫的 byte 開機會跑。

## 我們在哪裡

第 3 階段 (Bootloader) 的最簡可運作版。

## 完整原始碼

`hello.asm`：

```asm
; hello.asm — 最小可開機的 boot sector
; nasm -f bin hello.asm -o hello.bin

[BITS 16]               ; 告訴 NASM 這是 16-bit code
[ORG 0x7C00]            ; 告訴 NASM 我們的 code 載入位址是 0x7C00

start:
    ; 設好 segment register（很重要，後面說明）
    xor ax, ax          ; ax = 0
    mov ds, ax          ; ds = 0
    mov es, ax          ; es = 0
    mov ss, ax
    mov sp, 0x7C00      ; stack 從 0x7C00 往下長

    ; 印字串
    mov si, msg         ; si 指向字串
.print_loop:
    lodsb               ; al = [ds:si]，si++
    test al, al         ; 是不是 \0
    jz .done            ; 是就結束
    mov ah, 0x0E        ; INT 10h teletype output
    mov bh, 0           ; page 0
    mov bl, 7           ; 灰白
    int 0x10
    jmp .print_loop

.done:
    cli                 ; 關中斷
    hlt                 ; 停機，省 CPU

msg: db "Hello from boot sector!", 13, 10, 0

; 填到 510，加 signature
times 510-($-$$) db 0
dw 0xAA55
```

## 編譯與執行

```bash
nasm -f bin hello.asm -o hello.bin
ls -l hello.bin             # 應該剛好 512 bytes
xxd hello.bin | tail -1     # 最後該是 ... 55aa
```

跑起來：

```bash
qemu-system-x86_64 -drive format=raw,file=hello.bin -nographic
```

你應該看到：

```
SeaBIOS (version ...)
...
Booting from Hard Disk...
Hello from boot sector!
```

按 `Ctrl-A X` 離開。

## 逐行解說

### `[BITS 16]` 與 `[ORG 0x7C00]`

NASM 預設組譯成 16-bit code（其實 NASM 預設是 16-bit，但寫出來比較清楚）。

`[ORG 0x7C00]` 告訴 NASM「**我假設這個 binary 會被載入到 0x7C00**」。這影響什麼？

如果你寫 `mov si, msg`，NASM 要算 `msg` 的位址。沒 ORG 的話 `msg` 從 0 開始算；有 `ORG 0x7C00` 的話 `msg` 從 `0x7C00 + msg 在 binary 裡的 offset` 算。

BIOS 真的會載到 `0x7C00`，所以 ORG 必須是 `0x7C00`，不然 `mov si, msg` 算出來的位址就是錯的。

### Segment register 為什麼要清

real mode 用 segment:offset 定址。BIOS 跳到我們時，`CS` 通常是 0 (因為它跳到 `0x0000:0x7C00`)，**但其他 segment register 是亂的** — 各家 BIOS 不一樣。

如果你不清 `DS`，後面 `lodsb` 讀 `[DS:SI]` 拿到的就是亂的位址。所以：

```asm
xor ax, ax     ; ax = 0
mov ds, ax     ; ds 不能直接 mov immediate，要透過 ax
```

x86 的怪設定：**segment register 不能直接接 immediate**，要先放到一般 register 再 mov。

### Stack 為什麼設在 `0x7C00`

stack 往下長（pop 增加 SP，push 減少 SP）。設 `SP = 0x7C00` 表示 stack 從 `0x7C00` 往下用 `0x0000` ~ `0x7BFF` 這塊。為什麼選這？

- 不能踩到我們自己（我們在 `0x7C00` ~ `0x7DFF`）
- 不能踩到 BIOS data area（`0x400` ~ `0x4FF`）跟 IVT（`0x000` ~ `0x3FF`）

所以 `0x500` ~ `0x7BFF` 大約 30KB 是安全的 stack 區。設 `SP = 0x7C00` 用滿這塊。

### `lodsb` 是什麼

`lodsb` = LOaD String Byte：

```
AL = [DS:SI]
SI = SI + 1   (DF=0 時) 或 SI - 1 (DF=1 時)
```

DF (direction flag) 預設 0，所以 `SI++`。專為印字串設計的指令。

對應的還有 `lodsw` (16-bit)、`lodsd` (32-bit)、`movsb`、`stosb` 等等，都是 string 操作家族。

### `INT 10h, AH=0Eh`

teletype output 模式：把 `AL` 印到目前游標位置，自動換行、自動捲動。最簡單的 BIOS 印字方法。

```
INT 10h, AH=0Eh: Teletype output
  Input:
    AL = ASCII char
    BH = page number
    BL = foreground color (graphics mode only)
```

不用管 video mode、不用設 cursor，就是一個 putchar。

### `cli` + `hlt` 為什麼

`cli` 關中斷，避免 timer interrupt 把我們吵醒。

`hlt` 把 CPU 停在等中斷的狀態。配合 `cli` 就是**永遠停在這**。

如果不寫這兩行，CPU 會 fall through 到後面的 0 byte 區，把 0 當指令執行 — `00 00` 是 `add [bx+si], al`，剛好沒效果但接下來不知道會碰到什麼。

## 一個常見踩雷：忘了 signature

把最後兩行刪掉：

```asm
; times 510-($-$$) db 0
; dw 0xAA55
```

重新組譯，binary 變比 512 小。BIOS 會說 "not a bootable disk"。

## 一個常見踩雷：`ORG` 寫錯

把 `[ORG 0x7C00]` 改成 `[ORG 0]`。

組譯出來 `msg` 的位址會被算成 `msg 在 binary 裡的 offset`（小數字，可能 0x20 之類）。BIOS 還是把整個 binary 載到 `0x7C00`，但 `mov si, msg` 把 SI 設成 `0x20`。`lodsb` 讀 `[0x0020]` — 那是 BIOS 的 IVT 區，會印出鬼東西。

驗證一下這個失敗模式很有教育意義：故意改 ORG，看會印什麼。

## 一個常見踩雷：用了 32-bit register

```asm
mov esi, msg     ; ❌ real mode 不該用 esi
```

real mode 還是可以 access `ESI`，但只用低 16 bit 也就是 `SI`。前綴 32-bit operand 在 real mode 會多一個 `0x66` prefix byte，code 變大。boot sector 預算只有 446 bytes，能省就省。

## 加個讀 sector 的版本

先放著，後面 Ch 7 切 protected mode 之前我們會擴充這個 boot sector。

## 動手練習

**1. 改字串**

把 "Hello from boot sector!" 改成你的名字，重組重跑。

**2. 印兩行**

加第二個字串，連續印兩次。

**3. 用 GDB step**

```bash
qemu-system-x86_64 -s -S -drive format=raw,file=hello.bin -nographic
```

```bash
gdb
(gdb) target remote :1234
(gdb) set architecture i8086
(gdb) b *0x7c00
(gdb) c            # 跑到我們的 boot sector
(gdb) si
(gdb) info registers
```

step 看每行 register 怎麼變。觀察 `SI` 怎麼隨 `lodsb` 增加、`AL` 怎麼變字元。

**4. 故意弄錯**

依序試這幾個失敗模式，看 QEMU 行為：

- 刪掉 `dw 0xAA55` → "not a bootable disk"
- 把 `[ORG 0x7C00]` 改成 `[ORG 0]` → 印出亂碼
- 把 `mov ah, 0x0E` 改成 `mov ah, 0x0F` → 不印任何字（0x0F 是 get current video mode）
- 拿掉 `cli; hlt` → 印完之後 CPU 跑進 0 區，行為依 QEMU 實作

每個 failure mode 都對應到一個 boot 流程的 invariant。**這比讀十遍 spec 都有用**。

## 自我檢核

- [ ] 自己寫一個 boot sector，QEMU 跑得出來
- [ ] 講得出 `[ORG 0x7C00]` 的意義
- [ ] 知道為什麼 segment register 要清
- [ ] 知道 stack 為什麼設在 `0x7C00`
- [ ] 試過刪 signature、改 ORG、看 BIOS 怎麼罵

下一章把這個 boot sector 升級：切到 protected mode，用 32-bit register 寫 hello world。

→ [Ch 7 切到 protected mode (GDT / A20 / CR0)](./07-protected-mode-switch.md)
