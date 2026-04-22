# Ch 8 — 反組譯與暫存器

> 目標：看懂 GDB 的 `disas` 輸出，熟練 `stepi` / `nexti`，能讀 x86_64 的呼叫慣例（function prologue、參數在哪裡、return 在哪裡）。

## 為什麼要懂組語？

你不需要會「寫」組語。但 GDB 到處都會吐 asm：

- 沒 debug info 的 binary，`list` 失敗，只剩 asm。
- 優化後的 code 跟源碼對不上，要看 asm 才知道實際做什麼。
- 追 signal 時 crash 在 libc 裡，源碼是 libc 的 C，但你只有 asm。
- 寫 pretty printer 或 debugger 擴充時，知道 ABI 才能抓參數。

這章的目標是**看懂**，不是寫。

## `disas` — 反組譯

```
(gdb) disas
Dump of assembler code for function main:
   0x00000000000011b8 <+0>:     push   %rbp
   0x00000000000011b9 <+1>:     mov    %rsp,%rbp
   0x00000000000011bc <+4>:     sub    $0x10,%rsp
   0x00000000000011c0 <+8>:     movl   $0x5,-0x4(%rbp)
=> 0x00000000000011c7 <+15>:    mov    -0x4(%rbp),%eax
   0x00000000000011ca <+18>:    mov    %eax,%edi
   0x00000000000011cc <+20>:    call   0x1169 <sum_of_squares>
   ...
   0x00000000000011e0 <+40>:    leave
   0x00000000000011e1 <+41>:    ret
End of assembler dump.
```

`=>` 標出 `$pc`（當前要執行的指令）。

### 指定範圍

```
(gdb) disas main                           # 整個 main
(gdb) disas square, square+50              # square 開始的 50 byte
(gdb) disas 0x11b8, 0x11e0                 # 絕對位址範圍
(gdb) disas /m main                        # mixed，同時印源碼跟 asm
(gdb) disas /s main                        # 類似 /m，依源碼行號重組 asm
```

`/s` 是 modern GDB 的預設推薦，比 `/m` 輸出更乾淨。

### AT&T vs Intel 語法

GDB 預設 AT&T：`mov src, dst`、暫存器前綴 `%`、立即數前綴 `$`、memory 用 `offset(base, index, scale)`。

```
mov    $0x5,-0x4(%rbp)         # 把 5 搬到 rbp-4 的位置
```

嫌難讀可以切 Intel：

```
(gdb) set disassembly-flavor intel
(gdb) disas
   ...
   0x11c0 <+8>:     mov    DWORD PTR [rbp-0x4],0x5
```

Intel 語法：`mov dst, src`、沒 `%`、memory 用 `[base + offset]`。

**個人偏好 Intel**，除非你長期在看 gcc/glibc code 或讀 Linux kernel（它們慣用 AT&T）。一致即可，兩邊對應關係一樣。之後範例混用，兩邊都看一眼。

## x86_64 呼叫慣例（System V ABI）

你必須知道這幾件事：

### 參數在哪裡

前 6 個整數 / 指標參數：

| 參數順序 | 暫存器 |
|---|---|
| 第 1 | `rdi` |
| 第 2 | `rsi` |
| 第 3 | `rdx` |
| 第 4 | `rcx` |
| 第 5 | `r8` |
| 第 6 | `r9` |

超過 6 個：塞進 stack（右到左 push）。

浮點參數：`xmm0` ~ `xmm7`。

所以看到：

```asm
mov    $0x5, %edi
call   sum_of_squares
```

就是「拿 5 當 `sum_of_squares` 的第 1 個參數呼叫」。

### Return value 在哪裡

- 整數 / 指標：`rax`
- 浮點：`xmm0`
- 超過 128 bit 的結構：caller 會把接收位址放 `rdi`，被呼叫函式往那裡寫

### Callee-saved vs caller-saved

Callee（被呼叫函式）必須保留的暫存器（「我要改它就得先存、return 前還原」）：

`rbx`, `rbp`, `r12`, `r13`, `r14`, `r15`

其他的（`rax`, `rcx`, `rdx`, `rsi`, `rdi`, `r8`–`r11`）是 caller-saved — caller 自己要保存。

## Function prologue / epilogue

幾乎每個函式開頭長這樣：

```asm
push   %rbp               ; 存舊的 frame pointer
mov    %rsp,%rbp          ; 設新的 frame pointer = 當前 stack pointer
sub    $0x10,%rsp         ; 騰出 16 byte 給 local variable
```

結尾：

```asm
leave                     ; 等同於 mov %rbp,%rsp ; pop %rbp
ret                       ; 從 stack 彈出 return address 跳過去
```

或手工版：

```asm
mov    %rbp,%rsp
pop    %rbp
ret
```

**認出這個 pattern**，你就有「這是函式開頭 / 結尾」的直覺。

## `stepi` / `nexti` — 逐機器指令

```
(gdb) stepi           # 簡寫 si，執行下一條機器指令（進函式）
(gdb) nexti           # 簡寫 ni，執行下一條機器指令（不進函式）
```

跟 `step` / `next` 類似，但是以機器指令為單位。很多情況下一行 C 對應十幾條 asm，`si` 會一條一條走。

配合 `layout split`（Ch 7）最好用。

`si N` / `ni N`：一次走 N 條。

## `info registers`

```
(gdb) info registers
rax            0x7               7
rbx            0x0               0
rcx            0x7ffff7fbd0b0    140737353560240
rdx            0x7fffffffe1a8    140737488347560
rsi            0x7fffffffe198    140737488347544
rdi            0x1               1
rbp            0x7fffffffe0c0    0x7fffffffe0c0
rsp            0x7fffffffe0a0    0x7fffffffe0a0
...
rip            0x555555555175    0x555555555175 <main+10>
eflags         0x246             [ PF ZF IF ]
```

縮寫 `i r`。只看特定暫存器：

```
(gdb) i r rax rbx rip
(gdb) p $rax
$1 = 7
```

`$rax`、`$rbp` 這些都是 convenience variable（Ch 3 講過）。

### `info all-registers`

連 xmm、st、sse 全部印出。很長，但要看浮點或 SIMD 必用。

## 讀懂 memory operand

x86 的 memory operand 看起來可怕，但模式有限：

**AT&T：**

```
disp(base, index, scale)      ; 實際位址 = disp + base + index * scale
```

例子：

```
mov    -0x8(%rbp), %rax        ; 讀 *(rbp - 8) 到 rax
mov    (%rax, %rcx, 4), %ebx   ; 讀 *(rax + rcx*4) 到 ebx（陣列存取）
mov    0x200e1d(%rip), %rax    ; rip-relative，讀全域變數
```

**Intel：**

```
[base + index*scale + disp]
```

例子：

```
mov    rax, [rbp - 0x8]
mov    ebx, [rax + rcx*4]
mov    rax, [rip + 0x200e1d]
```

兩邊一樣，只是語法順序與符號不同。

## 實戰：跟著 `stepi` 走過一次 call

設斷點在 `main` 的 `call` 指令前：

```
(gdb) start
(gdb) disas
...
=> 0x000055555555519b <main+18>: mov    $0x5,%edi
   0x00005555555551a0 <main+23>: call   0x555555555169 <sum_of_squares>
   ...
```

```
(gdb) si         ; 執行 mov $0x5, %edi
(gdb) p $rdi     ; 第一個參數
$1 = 5

(gdb) si         ; 執行 call
(gdb) disas      ; 現在在哪？
Dump of assembler code for function sum_of_squares:
=> 0x555555555169 <+0>:  push   %rbp       ; prologue
   0x55555555516a <+1>:  mov    %rsp,%rbp
   ...
```

進到 `sum_of_squares` 的第一條指令了，`$rdi` 還是 5（參數傳進來的值）。

繼續 `si` 幾次：

```
(gdb) si         ; push %rbp
(gdb) si         ; mov %rsp, %rbp
(gdb) si         ; sub $0x20, %rsp
(gdb) x/8gx $rsp
0x7fffffffe0c0: 0x0000000000000000  0x0000000000000000
...
```

看 stack 上新 frame 的空間。

## `call` / `jump` / `return`：GDB 能主動操縱

```
(gdb) call square(7)           # 主動呼叫 inferior 的函式
$1 = 49

(gdb) call printf("hello\n")
hello
$2 = 6

(gdb) jump *0x11b8             # 強制把 PC 設到這裡（不建議，stack 會錯亂）
(gdb) return                   # 強制從當前函式返回（Ch 5 講過）
```

`call` 的機制跟 `p square(7)` 一樣（inferior function call），但 `call` 不印 `$N`。

## Register 的別名

x86_64 的一個 register 有不同寬度的別名：

```
rax (64 bit)
 └ eax (低 32)
    └ ax (低 16)
       ├ ah (高 8)
       └ al (低 8)
```

所以你會看到：

```
mov %al, %al       ; 操作 rax 的最低 byte
mov %eax, %edx     ; 32 bit 搬移
```

64 bit 寫 32 bit 目的時會**自動 zero-extend 高 32 bit**，這是 x86_64 的慣例。

## 不 debug info 怎麼辦

沒 `-g`，`disas` 還是能跑，只是沒源碼行號對照、沒變數名：

```
(gdb) disas 0x4011c0, 0x4011f0
   0x4011c0: push   %rbp
   0x4011c1: mov    %rsp, %rbp
   ...
```

`info functions`（縮寫 `i fu`）列出所有 symbol（即使沒 debug info，symbol table 通常還在）：

```
(gdb) i fu
Non-debugging symbols:
0x0000000000401080  _init
0x00000000004010c0  __cxa_finalize@plt
0x0000000000401169  square
0x000000000040117e  sum_of_squares
0x00000000004011a0  main
...
```

即使 symbol 被 strip 掉，你還是可以 `disas 0x11b8, 0x11e0` 盲 disas。這是逆向工程的基本功。

## 一個實用技巧：`x/i $pc`

不開 TUI，只想快速看「我現在這條指令是什麼」：

```
(gdb) x/i $pc
=> 0x555555555175 <main+10>:    mov    -0x10(%rbp),%eax
```

或看接下來 5 條：

```
(gdb) x/5i $pc
```

對比 `disas` 輸出格式類似。

## 常見坑

1. **`call printf("...")` 沒輸出**：有時候 inferior 的 stdout buffer 還沒 flush。`call fflush(stdout)` 強制 flush。
2. **`si` 跳進 library 一片黑**：到 libc / ld-linux 的 code，沒 debug info。用 `finish` 或下一個 `b <回來的位址>` 然後 `c`。
3. **`disas` 輸出 `unknown instruction`**：GDB disassembler 有版本差異，或者 binary 其實是其他架構。`set architecture` 指定。
4. **`info registers` 印出來的看起來很怪**：你在 core dump / remote gdb 裡，暫存器可能部分不可用（`<unavailable>`）。
5. **看不到 xmm 暫存器**：要 `info all-registers`，或 `p $xmm0.v4_float`。

## 動手練習

沿用 `sample.c`：

1. `layout split`、`start`、`disas main` — 對照 C 與 asm。
2. `si` 一步一步走過 `main`，每步 `p $rdi`、`p $rax` 看狀態。
3. 在 `sum_of_squares` 的 prologue 前下斷點，step 進去，手動確認：
   - RSP、RBP 怎麼變化
   - local variable `total`、`i` 在哪個 offset
4. `disas /s sum_of_squares` — 源碼與 asm 混排，找「`for` 迴圈的比較」對應哪條指令。
5. 用 `call square(10)` 直接在 GDB 裡呼叫。
6. 切 Intel 語法、切回 AT&T，看同一段 code 兩種樣子。

## 自我檢核

- [ ] 我能說出 x86_64 前 6 個參數走哪些暫存器
- [ ] 我能認出 function prologue / epilogue
- [ ] 我能用 `si` / `ni` 逐機器指令執行
- [ ] 我能讀 `mov -0x8(%rbp), %rax` 這種 operand
- [ ] 我知道 `info registers` 與 `$rax` 的關係

下一章處理真實世界的亂源之一：signal 與 fork/exec。GDB 怎麼跟 kernel 搶 signal、怎麼跟 fork 出來的子 process 互動。

→ [Ch 9 Signal、fork、exec](./09-signals-fork-exec.md)
