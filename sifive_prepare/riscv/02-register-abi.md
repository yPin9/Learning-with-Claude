# Ch 2 — Register 慣例與 ABI：calling convention 的硬體介面

> 目標：把 `x0..x31` 從「32 個 slot」升級成「32 個角色」。讀完之後你看到 `a0`、`s2`、`t3`、`ra` 能立刻反應它們是誰、是誰的責任。並能解釋 `ilp32` / `lp64` / `lp64d` 這些 ABI 字串到底差在哪。

## 為什麼 ABI 重要

硬體層面 `x0..x31` 除了 `x0` 都一樣。你大可以用 `x17` 當 stack pointer、`x3` 當返回地址。但如果每支 function 都自己定規則，**linker 連不起來、libc 呼叫不動、debugger 拆 stack 失敗**。

ABI（Application Binary Interface）是一份**社會契約**：所有 compiler、assembly 作者、libc 都遵守「`x2` 叫 `sp` 並指向 stack、`x1` 叫 `ra` 存返回地址、`a0..a7` 是 argument」。這不是 ISA spec 的一部分，而是獨立文件：

- **RISC-V ELF psABI** — <https://github.com/riscv-non-isa/riscv-elf-psabi-doc>

ISA 定義了 ADD 怎麼算，ABI 定義了「call printf 要怎麼擺參數」。**改 compiler backend 時，第一件事是搞懂當前目標的 ABI**。

## 暫存器別名表（請背下來）

這張表未來會反覆出現：

```
┌─────┬──────┬────────────┬────────────────────────────┐
│ x#  │ 別名 │ 角色        │ saver                       │
├─────┼──────┼────────────┼────────────────────────────┤
│ x0  │ zero │ 常數 0      │ —                           │
│ x1  │ ra   │ return addr │ Caller                      │
│ x2  │ sp   │ stack ptr   │ Callee (必須保存)           │
│ x3  │ gp   │ global ptr  │ — (整個程式共用)            │
│ x4  │ tp   │ thread ptr  │ — (TLS 基底)                │
│ x5  │ t0   │ temp 0      │ Caller                      │
│ x6  │ t1   │ temp 1      │ Caller                      │
│ x7  │ t2   │ temp 2      │ Caller                      │
│ x8  │ s0/fp│ saved 0 / FP│ Callee                      │
│ x9  │ s1   │ saved 1     │ Callee                      │
│ x10 │ a0   │ arg 0 / ret │ Caller                      │
│ x11 │ a1   │ arg 1 / ret │ Caller                      │
│ x12 │ a2   │ arg 2       │ Caller                      │
│ x13 │ a3   │ arg 3       │ Caller                      │
│ x14 │ a4   │ arg 4       │ Caller                      │
│ x15 │ a5   │ arg 5       │ Caller                      │
│ x16 │ a6   │ arg 6       │ Caller                      │
│ x17 │ a7   │ arg 7       │ Caller                      │
│ x18 │ s2   │ saved 2     │ Callee                      │
│ x19 │ s3   │ saved 3     │ Callee                      │
│ x20 │ s4   │ saved 4     │ Callee                      │
│ x21 │ s5   │ saved 5     │ Callee                      │
│ x22 │ s6   │ saved 6     │ Callee                      │
│ x23 │ s7   │ saved 7     │ Callee                      │
│ x24 │ s8   │ saved 8     │ Callee                      │
│ x25 │ s9   │ saved 9     │ Callee                      │
│ x26 │ s10  │ saved 10    │ Callee                      │
│ x27 │ s11  │ saved 11    │ Callee                      │
│ x28 │ t3   │ temp 3      │ Caller                      │
│ x29 │ t4   │ temp 4      │ Caller                      │
│ x30 │ t5   │ temp 5      │ Caller                      │
│ x31 │ t6   │ temp 6      │ Caller                      │
└─────┴──────┴────────────┴────────────────────────────┘
```

記憶法：

- **`a*` 是 argument**：`a0..a7` 共 8 顆，前 8 個整數參數走暫存器，第 9 個起走 stack。
- **`s*` 是 saved（callee-saved）**：12 顆，被呼叫者要保存。compiler 選了 `s2` 表示「這個值需要跨 call 活下來」。
- **`t*` 是 temp（caller-saved）**：7 顆，想用就用，但穿過 call 後內容沒保證。
- **`ra` 是 return address**：`jal` 會自動寫入它。
- **`sp` 是 stack pointer**：永遠對齊到 16 byte（RV32 也是 16，這是 RISC-V ABI 的選擇）。
- **`gp` / `tp`**：一般 C code 不動它們，linker / runtime 管。

## Caller-saved vs Callee-saved：誰負責備份

這是 ABI 最重要的概念。想像你是一個 function：

**我是 caller（呼叫者）**：call 別人之前，如果我有資料在 `t0..t6` / `a0..a7` 還要用，**我要自己先存到 stack**。因為對方可能蹂躪它們。

**我是 callee（被呼叫者）**：如果我想用 `s0..s11`，**我要進來就備份到 stack、走之前還原**。因為 caller 以為這些暫存器跨 call 不變。

白話一點：

```
t* 是「給我自己 prologue 跟 epilogue 中間用的暫存器」—— 用完就 drop
s* 是「我要活很久、即使中間呼叫別人還要保留的暫存器」—— 進來先存、走前復原
a* 是「對外通訊用的」—— 存參數、存回傳值，進 function 之後就不是 argument，可以當 t 用
```

Compiler 做 register allocation 時，重度常用的變數會優先放 `s*`（不怕被 call 砸），短命變數放 `t*`（省 save/restore 成本）。

## Prologue 與 Epilogue：function 的標準動作

一支 function 進來跟走前的 stack 動作有固定套路。對 `int f(int x) { int y = g(x) + 1; return y; }`：

```asm
f:
    addi  sp, sp, -16       # 配 stack frame（對齊 16）
    sw    ra, 12(sp)        # 存 ra（因為我要 call g，回來時 ra 要是我的）
    sw    s0, 8(sp)         # 存 s0（我要用它放 y）
    # --- 以下是 f 的本體 ---
    call  g                 # g(x)；a0 進 a0 出
    addi  s0, a0, 1         # s0 = g(x) + 1
    mv    a0, s0            # return s0
    # --- epilogue ---
    lw    ra, 12(sp)        # 還 ra
    lw    s0, 8(sp)         # 還 s0
    addi  sp, sp, 16        # 還 stack
    ret
```

注意：

- **`sp` 必須 16-byte 對齊**，所以即使只需要 12 byte 也配 16。
- **`ra` 的儲存**：因為 `f` 自己會 call g、call 指令會蓋掉 `ra`，所以 `f` 要負責先備份自己的 ra。
- **沒呼叫別人的 leaf function 可以省這個**：沒 call 就不用存 ra。

## Argument passing 規則（整數部分）

前 8 個整數參數 → `a0, a1, ..., a7`
第 9 個起 → stack（依次放在 `sp+0, sp+8, ...`，RV64）

結構與陣列的規則：

- **小於 2 個 XLEN（register width）的 struct**：盡量用暫存器、分解到 a0..a7。
- **大於 2 個 XLEN 的 struct**：**caller 配一塊空間、把指標傳給 callee**。實務上等於傳 by reference，但 C 層級看起來還是 by value。
- **return 超過 2 個 XLEN 的 struct**：caller 傳一顆隱藏的「返回值指標」放在 `a0`，callee 寫進去。

這些細節在改 compiler backend 時會要命。spec 在 psABI 的 `Calling Convention` 段，**務必讀一次原文**。

## 為什麼 ra 放暫存器而不是 stack？

x86 的 `call` 會把返回位址 push 到 stack。RISC-V 的 `jal` 把返回位址放**暫存器 `ra`**。差在哪？

- **Leaf function 不用動 stack**：沒 call 別人的小 function，`ra` 整支 function 都保留在暫存器裡，`ret` 直接 jalr 就好。省兩個 memory access。
- **Call overhead 更低**：stack push/pop 是 memory operation，latency 比 register write 高。
- **代價**：有 call 就必須手動把 `ra` 存進 stack（上面 prologue 就看到）。Compiler 代勞。

這是典型的 RISC 取捨：**把特例最佳化（leaf）放到指令層級免費**，常例付小成本。

## ABI 命名：`ilp32` / `ilp32d` / `lp64` / `lp64d`

gcc 的 `-mabi=` 值看起來很怪，其實有明確邏輯：

```
ilp32     → RV32, int/long/ptr = 32 bit, 浮點參數走整數暫存器 (soft-float ABI)
ilp32f    → RV32, 同上 + 單精度浮點 (f) 走 FP 暫存器
ilp32d    → RV32, 同上 + 雙精度浮點 (d) 走 FP 暫存器
ilp32e    → RV32E (只有 16 顆 reg 的 embedded 版)

lp64      → RV64, long/ptr = 64, int = 32, soft-float ABI
lp64f     → RV64 + float 走 FP reg
lp64d     → RV64 + double 走 FP reg (Linux distro 預設)
```

**重點**：ABI 與 `-march` **要一致**。常見搭配：

```
-march=rv32imafc    -mabi=ilp32f      # 典型 MCU
-march=rv32imafdc   -mabi=ilp32d      # 高階 RV32
-march=rv64imafdc   -mabi=lp64d       # Linux 桌面／伺服器（俗稱 RV64GC）
-march=rv64imac     -mabi=lp64        # 不帶 FPU 的 RV64
```

**`march` 跟 `mabi` 不一致 = link error**。編 libc 跟 kernel 時這類錯最常見，Ch 11（custom extension）會遇到。

## soft-float ABI vs hard-float ABI

兩個 binary 的差：

- **soft-float (`lp64`)**：浮點參數走 `a0..a7`，浮點運算呼叫 `__addsf3` 等 libgcc 函式。沒 FPU 的硬體唯一選擇。
- **hard-float (`lp64d`)**：浮點參數走 `fa0..fa7`（浮點專用暫存器），直接用 `fadd.d` 之類的指令。

兩者**不能互連**。你不能用 `lp64d` 的 libc 連到 `lp64` 的 main — 因為 function prototype 看起來一樣，但 argument 放的位置不同。RISC-V distro 的整個 rootfs 編譯都要統一。

## ABI 還是 ISA？常見混淆

```
-mabi=lp64d   告訴 compiler/linker：「產出的 binary 遵守這份社會契約」
-march=rv64gc 告訴 compiler：「可以用這些指令」
```

一個是「語言」、一個是「詞彙」。`-march=rv64gc -mabi=lp64` 是合法的（有 FPU 指令但不用 FP 暫存器傳參 — 拿來跑老舊 ABI），只是少見。

## 一個 call 的完整拆解

以 `printf("n=%d\n", 42)` 為例，看 caller 跟 callee 發生什麼：

```asm
# Caller 端
# -------
# 先擺 argument（printf 的前兩個）
la    a0, .L_fmt        # a0 = &"n=%d\n"
li    a1, 42            # a1 = 42
call  printf            # pseudo，實際展開為 auipc ra, ...; jalr ra (下章細講)

# 從 printf 回來時：
#   ra = 下一條指令的地址（被 jal 寫入）
#   a0 = printf 回傳值（印了幾個字元）
#   t*, a*（除 a0）不保證保留
#   s* 保持不變（printf 遵守 ABI）

# 如果我接下來還要用 t0，call 之前應該已經存起來了
```

**memorize 這個**：進 call 之前 `t*` / `a*` 的值你不能再相信。

## Stack red zone：RISC-V 沒有

x86-64 有 128-byte red zone（`sp` 下方 128 byte 可以當 scratch 不用先 sub rsp）。**RISC-V 沒有這個概念**。任何想暫存在 stack 的東西必須先 `addi sp, sp, -N` 把 `sp` 壓下去。

為什麼？RISC-V 想留給 signal handler / trap handler 一個單純的假設：**`sp` 以下都不是你的**。signal 打進來可以直接在 `sp` 以下堆 signal frame，不用跨 red zone。

## Tail call optimization

尾呼叫（`return f(args)`）有個簡化路徑：不用保存 ra、不用新 frame，直接 `j` 過去。

```c
int wrapper(int x) { return target(x + 1); }
```

被 compile 成：

```asm
wrapper:
    addi a0, a0, 1
    tail target         # pseudo，展開為 auipc + jalr x0, ...
```

`tail` 是 pseudo（Ch 3 講展開），關鍵是用 `jalr x0, ...`（不寫回 ra）。**wrapper 的 ra 直接被 target 用**，target `ret` 時就回到 wrapper 的 caller。這叫 tail-call elimination，避免一層 stack frame。

## 常見坑

1. **手寫 asm 忘了對齊 sp**：`addi sp, sp, -12` 會讓後面的 `call` 踩到 ABI 假設（sp 要 16 對齊）。用 `-16` 即使只要 12。
2. **以為 `s0` 可以隨便用**：`s0 = x8 = fp`，有些 compiler 會把它當 frame pointer。加 `-fomit-frame-pointer`（預設就有）才會釋放它。
3. **custom function 忘了存 ra**：`f` 裡呼叫別人後沒備份 `ra`，回 caller 時跳錯地方。super 難 debug。
4. **ABI 不符 link error**：`cannot link object files with different floating-point ABIs` — 99% 是 `-mabi=` 不一致。
5. **在 interrupt handler 用 `a*`**：M-mode trap handler 要先存所有 caller-saved 暫存器，否則被中斷的程式會死得很詭異。這是 Ch 5 的內容。

## 動手練習

1. 寫一支遞迴 Fibonacci，`-O0` 編出來看 prologue/epilogue。對照上面的模板，認出每一條的角色。
2. 同一支 code，加 `-O2` 重編，看 `s*` 被用了幾顆、`t*` 幾顆、有沒有 tail call。
3. 故意把 `x` 宣告成 `long long`（兩個 XLEN），看 RV32 的 `lp64`、`ilp32` 各怎麼傳參。（RV32 `long long` 是 64 bit → 會佔 `a0+a1`）
4. 寫一個 struct 有 5 個 int field，回傳它。看 compiler 怎麼安排「caller 配空間、傳指標」。
5. 用 `-mabi=lp64` + `-march=rv64gc` 編一個用 double 的程式，跟 `-mabi=lp64d` 版本的 objdump 比對，觀察浮點參數位置差異。

## 自我檢核

- [ ] 我能默寫出 `a0..a7`、`s0..s11`、`t0..t6`、`ra`、`sp` 的角色
- [ ] 我能解釋 caller-saved 與 callee-saved 的差別以及 compiler 如何用
- [ ] 我能寫一支 function 的正確 prologue + epilogue
- [ ] 我知道 `ilp32d` 與 `lp64d` 差在哪，為什麼不能互連
- [ ] 我能指出哪種 call 可以做 tail-call optimization

下一章我們拆 pseudo-instruction — 那些你天天在 `.S` 裡看到、但其實不在 ISA spec 裡的指令（`li`、`call`、`la`、`ret` 等）。會解釋 `auipc` 為什麼是 RISC-V 的靈魂、PC-relative addressing 怎麼運作、以及為什麼 RISC-V 的 linker **特別依賴 relaxation**。這條 foreshadow 會在 `elf_linking` 延伸成整門課。

→ [Ch 3 Pseudo-instruction 與 assembler 展開](./03-pseudo-instructions.md)
