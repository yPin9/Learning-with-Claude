# Ch 6 — 寫一個 boot sector

> **目標**：親手寫一個完整的 16-bit boot sector——理解 `org 0x7c00` 的意義、正確初始化 segment 和 stack、用 BIOS int 10h 印字串、處理 `org` 與載入位址的對應，並在 QEMU 跑起來、用 gdb 追蹤。這是 BIOS 線的第一個動手里程碑。

> **環境**：nasm 2.15+，QEMU，gdb。承接 Ch 2（CPU 狀態）、Ch 3（記憶體佈局）、Ch 5（MBR 結構）。

## 為什麼親手寫 boot sector？

你已經知道 CPU 上電狀態（Ch 2）、記憶體地圖（Ch 3）、MBR 結構（Ch 5）。現在把它們組裝起來，親手寫一個能開機的 boot sector。

這個過程強迫你面對所有細節：`org` 怎麼設、segment 怎麼初始化、stack 設哪、怎麼印字、怎麼湊滿 512 bytes。寫過一次，BIOS 開機對你就不再是黑盒子——你知道那 512 bytes 裡每個 byte 在做什麼。

## 先建立直覺：boot sector 要自己搭好舞台

```
BIOS 把你丟到 0x7C00，CPU 在 real mode，然後就不管你了：

  你拿到的環境：
    - CS:IP 指向你的 code（在 0x7C00）
    - DL = 開機磁碟編號
    - 其他暫存器：不可信，自己搭

  你要自己做的「搭舞台」：
    1. 設定 segment 暫存器（DS/ES，讓資料存取正確）
    2. 設定 stack（SS:SP，讓 push/call 能用）
    3. 然後才能做正事（印字、讀磁碟）
        │
  搭舞台 = boot sector 開頭的固定儀式
```

## org 0x7c00：告訴組譯器「我在哪」

第一個關鍵是 `org`（origin）。它告訴組譯器「假設這段 code 被載入到哪個位址」，組譯器據此計算所有 label 的位址。

```asm
org 0x7c00
        │
  告訴 nasm：假設這段 code 在記憶體位址 0x7C00
        │
  所以 nasm 計算 label 位址時，第一條指令是 0x7C00，
  後面依序遞增
        │
  為什麼是 0x7C00？因為 BIOS 就是把 boot sector 載到這（Ch 3）
  org 必須和實際載入位址一致，否則 label 位址全錯
```

舉例說明為什麼 `org` 重要：

```asm
org 0x7c00
start:
    mov si, message     ; message 的位址
message: db "Hi", 0
```

`message` 這個 label，nasm 怎麼算它的位址？如果 `org 0x7c00`，nasm 知道 `start` 在 0x7C00，`message` 在 0x7C00 + (前面指令的長度)。如果 `org` 設錯（比如 `org 0`），nasm 算出 `message` 在某個低位址，但實際它被載到 0x7C00 附近——`mov si, message` 載入了錯誤的位址，印出垃圾。

> `org` 是 boot sector 最常見的雷。它不改變 code 被載到哪（那是 BIOS 決定的，固定 0x7C00），它只影響 nasm **計算 label 位址**。`org` 必須等於實際載入位址（0x7C00），label 才算得對。記住：`org` 是「給組譯器的假設」，不是「命令 CPU 跳到哪」。

## 完整的 boot sector

```asm
; boot.asm — 一個正確初始化環境並印字串的 boot sector
bits 16                 ; 16-bit real mode
org 0x7c00              ; BIOS 載入到 0x7C00

start:
    ; === 搭舞台 1：保存開機磁碟編號 ===
    mov [boot_drive], dl    ; BIOS 在 DL 給了開機磁碟，存起來備用

    ; === 搭舞台 2：初始化 segment 暫存器 ===
    ; 用 0 當 segment base，這樣 offset 就是物理位址（配合 org 0x7c00）
    xor ax, ax          ; ax = 0
    mov ds, ax          ; DS = 0（資料存取：DS:offset = 0 + offset）
    mov es, ax          ; ES = 0

    ; === 搭舞台 3：設定 stack ===
    ; stack 設在 0x7C00 以下（往下長，不會踩到我們的 code）
    mov ss, ax          ; SS = 0
    mov sp, 0x7c00      ; SP = 0x7C00，stack 從這往下長（到 0x0500 前都可用）

    ; === 正事：印字串 ===
    mov si, message     ; SI 指向字串（org 0x7c00 讓這個位址正確）
    call print_string

    ; === 停住 ===
hang:
    hlt                 ; 暫停 CPU（省電，等中斷）
    jmp hang            ; 中斷喚醒後再 hlt

; --- 印字串副程式（用 BIOS int 10h teletype）---
print_string:
    push ax
.loop:
    lodsb               ; 載入 [SI] 到 AL，SI++（lodsb = load string byte）
    test al, al         ; AL == 0？（字串結尾）
    jz .done            ; 是 0，結束
    mov ah, 0x0e        ; int 10h teletype 功能
    int 0x10            ; 印 AL
    jmp .loop
.done:
    pop ax
    ret

; --- 資料 ---
message: db "Booting from my boot sector!", 0x0d, 0x0a, 0
boot_drive: db 0

; --- 填充到 510 bytes + signature ---
times 510-($-$$) db 0   ; 填 0 到第 510 byte
                        ; $ = 當前位址, $$ = 段開頭, $-$$ = 目前用了幾 bytes
dw 0xaa55               ; boot signature（磁碟上是 55 aa）
```

逐段解說：

- **保存 DL**：第一件事存開機磁碟編號（Ch 2/4），之後讀磁碟要用
- **segment 初始化**：設 DS=ES=0，這樣 `DS:offset` 的物理位址 = `0 + offset`。配合 `org 0x7c00`，label 位址（如 `message`）就是正確的物理位址
- **stack**：SS=0, SP=0x7C00。stack 往下長（push 減 SP），從 0x7C00 往下到 0x0500（BDA 之上）都是可用空間（Ch 3），不會踩到 0x7C00 的 code
- **印字串**：`lodsb` 是 x86 的字串指令（載入 `[SI]` 到 `AL` 並 `SI++`），配合 int 10h teletype 印每個字元直到 null
- **填充**：`times 510-($-$$) db 0` 用 0 填到第 510 byte，`$-$$` 是「目前這段用了幾 bytes」

## 組譯與執行

```bash
# 組譯成 raw 512-byte binary
nasm -f bin boot.asm -o boot.img

# 確認 512 bytes
ls -l boot.img      # 512

# 確認結尾 55 aa
xxd boot.img | tail -1   # ...55 aa

# QEMU 跑
qemu-system-x86_64 -drive format=raw,file=boot.img
# 視窗印出：Booting from my boot sector!
```

## 用 gdb 追蹤搭舞台過程

```bash
qemu-system-x86_64 -drive format=raw,file=boot.img -s -S &
gdb
```

```gdb
(gdb) target remote localhost:1234
(gdb) set architecture i8086
(gdb) break *0x7c00
(gdb) continue
# 命中 0x7c00（你的 boot sector 開頭）
(gdb) si              # 單步：mov [boot_drive], dl
(gdb) info registers  # 看 dl（開機磁碟）的值，QEMU 硬碟通常 0x80
(gdb) si              # 繼續單步看 segment/stack 初始化
(gdb) x/s 0x7c00 + (message - start)  # 看 message 字串內容
```

追蹤 segment 初始化和 stack 設定，看 `ds`、`es`、`ss`、`sp` 怎麼變。這讓「搭舞台」具體可見。

## 故意弄壞：org 設錯

最經典的 boot sector 雷——把 `org` 拿掉或設錯：

```asm
; 錯誤：沒有 org（nasm 預設 org 0）
bits 16
start:
    mov si, message     ; nasm 算 message 位址時假設 start 在 0
    call print_string   ; 但實際載到 0x7C00！
    ; message 的位址算成「假設在 0」的低位址
    ; 但字串實際在 0x7C00 + offset
    ; → SI 指向錯誤位址，印出垃圾或空白
...
```

沒有 `org 0x7c00`，nasm 假設 code 在位址 0 計算 label。但 BIOS 把 code 載到 0x7C00。所以 `mov si, message` 載入了「假設在 0」的位址，但字串實際在 0x7C00 附近——SI 指錯地方，印出垃圾。加上 `org 0x7c00` 修復。

```bash
# 體驗這個 bug：拿掉 org 0x7c00 重編跑，看印出垃圾
# 加回去，正常
```

## 踩雷集錦

1. **忘記 `org 0x7c00`**：label 位址全錯（nasm 假設 org 0，但實際載 0x7C00）。印字串印出垃圾。最經典的 boot sector 雷

2. **沒初始化 segment（DS/ES）**：上電後 segment 值不可信。資料存取（`mov si, message` 後 `lodsb`）用 DS，沒設好就讀錯地方

3. **沒設 stack 或設在危險位置**：`call`/`push` 需要 stack。沒設 SS:SP，stack 亂指。設在 video memory/ROM 會亂寫（Ch 3）

4. **`times` 填充算錯**：`times 510-($-$$) db 0`，如果 code+data 超過 510，這會是負數，nasm 報錯（提醒你超標）。如果寫成 512 而非 510，signature 位置錯

5. **用 `-f elf` 而非 `-f bin`**：boot sector 要 raw binary（`nasm -f bin`），不是 ELF。用 elf 格式會有 header，不是純 512 bytes

## 進階：boot sector 的進階技巧

實際的 boot sector 還會處理更多：

```asm
; 進階考量（真實 boot sector 會做）：

; 1. far jump 規範化 CS:IP
;    BIOS 可能用 0x07C0:0x0000 或 0x0000:0x7C00 跳進來（都是物理 0x7C00）
;    為了確定 CS=0，開頭加一個 far jump 規範化：
;    jmp 0x0000:start_real   ; 強制 CS=0, IP=start_real
; start_real:
;    ... 後續用 org 0x7c00 算的位址才一致

; 2. 清中斷再設 stack（避免設 stack 時被中斷打斷）
;    cli                 ; 關中斷
;    ... 設 SS:SP ...
;    sti                 ; 開中斷

; 3. 用 LBA 而非 CHS 讀磁碟（Ch 9）
```

far jump 規範化 CS 是個微妙但重要的細節：不同 BIOS 跳進 boot sector 時用的 segment:offset 可能不同（`0x07C0:0x0000` vs `0x0000:0x7C00`，物理位址都是 0x7C00，但 CS 不同）。開頭加 `jmp 0x0000:start` 強制 CS=0，確保和 `org 0x7c00` 的假設一致。本章的簡化版沒做這個（QEMU 用 CS=0），但生產 boot sector 會加。

## 動手練習

1. 跑通本章的 boot sector，看到字串。改成印你的名字 + 開機磁碟編號（把 DL 轉成 ASCII 印出來）

2. 用 gdb 單步追蹤「搭舞台」三步（存 DL、設 segment、設 stack），每步看暫存器變化

3. 故意弄壞 `org`：拿掉 `org 0x7c00`，重編跑，看印出垃圾。理解 `org` 如何影響 label 位址計算

4. 進階：加上 far jump 規範化 CS（`jmp 0x0000:start_real`）和 cli/sti 保護 stack 設定，做一個更 robust 的版本

## 本章重點整理

- `org 0x7c00` 告訴 nasm「假設 code 在 0x7C00」，據此算 label 位址；必須和實際載入位址一致
- boot sector 開頭要「搭舞台」：存 DL（開機磁碟）、初始化 segment（DS/ES）、設 stack（SS:SP）
- 用 BIOS int 10h（AH=0Eh teletype）配合 `lodsb` 印字串
- `times 510-($-$$) db 0` + `dw 0xaa55` 填充到 512 bytes 並加 signature
- 用 `nasm -f bin` 產生 raw binary；QEMU 跑、gdb 追蹤搭舞台過程

## 自我檢核

- [ ] 能不看範本寫出 boot sector 的「搭舞台」三步（存 DL、segment、stack）
- [ ] 能解釋 `org 0x7c00` 做什麼、設錯會怎樣
- [ ] 知道為什麼要初始化 DS/ES 和 stack
- [ ] 知道 `times 510-($-$$) db 0` 在做什麼
- [ ] 能用 gdb 追蹤 boot sector 從 0x7C00 開始的執行

## 延伸閱讀

### 官方文件

- **[OSDev Wiki: Bootloader](https://wiki.osdev.org/Bootloader)** 和 **[Babystep1-4](https://wiki.osdev.org/Babystep1)**
  - **讀哪裡**：Babystep 系列從最簡單的 boot sector 一步步加功能
  - **學什麼**：boot sector 的漸進式建構，本章的 step-by-step 對照
  - **前提**：本章

### 部落格 / 文章

- **[Writing a Bootloader Part 1](https://3zanders.co.uk/2017/10/13/writing-a-bootloader/)** — Alex Parker
  - **這篇說什麼**：從零寫 boot sector 並切到 32/64-bit 的完整系列
  - **讀哪裡**：Part 1（boot sector 基礎），Part 2-3 對應 Ch 7-8
  - **為什麼值得讀**：清晰的 step-by-step，配合本課 Ch 6-8 一起讀

### 書籍

- **《Operating Systems: From 0 to 1》** — Tu, Do Hoang（免費）
  - **這本書的定位**：從 boot sector 開始自製 OS，組合語言講得很細
  - **讀哪幾章**：bootloader 和 real mode 章節
  - **前提**：本章

→ [Ch 7 從 real mode 到 protected mode](./07-real-to-protected-mode.md)
