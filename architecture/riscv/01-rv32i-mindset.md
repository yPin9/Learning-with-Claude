# Ch 1 — RV32I 心法：為什麼只有 47 條指令

> 目標：建立 RISC-V 的設計哲學 — load-store、固定寬度、modular、沒有 condition code。理解 RV32I 的 47 條指令分類與邏輯，讀完之後你看到任何一條 RISC-V 指令都能立刻歸類「它屬於哪一格」。

## 先談哲學：這個 ISA 想贏在哪

RISC-V 是 2010 年後才出現的「乾淨 RISC」。它不是最快、不是最省電、不是最前衛 — 它的優勢是**簡單與開放**。看懂這兩個字的設計後果，你就看懂了 RISC-V 的所有細節。

四個核心設計原則：

1. **Load-store 架構**：所有運算指令只操作暫存器，記憶體存取只用 `lw` / `sw` 這類專用指令。ALU 跟 memory 完全分開。
2. **固定 32-bit 指令寬度**（base）：decoder 極簡，pipeline 好設計。C 擴充才有 16-bit 壓縮指令，但是 optional。
3. **Modular ISA**：core 是 RV32I / RV64I，其他（M 乘除、A atomic、F/D 浮點、C 壓縮、V 向量…）都是**擴充**，可加可不加。
4. **沒有 condition code / flags register**：branch 直接讀兩個暫存器比較。沒有 `ZF`、`CF`、`NF` 這種東西。

這四個原則看似無聊，但它們的後果會滲透到後面**每一章**。

## 為什麼是 47 條？

RV32I 的 **base integer** 指令集總共 47 條（有些版本算法不同，39、40 也聽過，看你 `ecall/ebreak/fence` 算不算在「整數」裡）。這個數字是刻意的 — 跟 x86-64 的 1000+ 或 ARMv8 AArch64 的 400+ 比起來，RV32I 可以在一張 A4 紙上列完。

47 條怎麼分：

```
┌─────────────────────┬────────┬─────────────────────────────────┐
│ 類別                │ 條數   │ 代表                            │
├─────────────────────┼────────┼─────────────────────────────────┤
│ 整數運算（reg-reg） │ 10     │ add, sub, and, or, xor, sll,    │
│                     │        │ srl, sra, slt, sltu             │
│ 整數運算（reg-imm） │  9     │ addi, andi, ori, xori, slli,    │
│                     │        │ srli, srai, slti, sltiu         │
│ Load / Store        │  8     │ lb, lh, lw, lbu, lhu, sb, sh, sw│
│ Branch              │  6     │ beq, bne, blt, bge, bltu, bgeu  │
│ Jump                │  2     │ jal, jalr                       │
│ Upper immediate     │  2     │ lui, auipc                      │
│ System              │  2     │ ecall, ebreak                   │
│ Fence               │  1     │ fence                            │
│ CSR (Zicsr)         │  6     │ csrrw, csrrs, csrrc, + imm 版   │
└─────────────────────┴────────┴─────────────────────────────────┘
                       = 46 不含 CSR / 52 含 CSR
```

不要執著數字。**重點是「只有這幾類」**。你之後看到的每一條指令幾乎都能歸進這個表。

## 為什麼 Load-Store？

x86 可以 `add [rbx+8], rax` — 一條指令同時**讀記憶體 + 運算 + 寫回記憶體**。RISC-V 不行。同樣的事要分三步：

```asm
lw   t0, 8(s1)        # 讀
add  t0, t0, a0       # 算
sw   t0, 8(s1)        # 寫
```

看起來多繞一道。但對硬體來講：

- **Pipeline 好設計**：每條指令只做一件事（ALU 或 memory），stage 劃分乾淨。
- **Out-of-order 好做**：memory 指令可以獨立調度到 load/store unit，ALU 指令走 execution unit。
- **不需要 μop 分解**：x86 那條 `add [mem], reg` 在現代 CPU 內部會被拆成三個 μop。RISC-V 直接把這個「μop 分解」外包給 compiler 了。

**後果**：RISC-V 的 code 看起來比較長，但每條指令的硬體成本很一致。compiler 做的事變多（要會 scheduling、register allocation），硬體變簡單。

## 為什麼沒有 condition code？

x86 / ARM 的世界：

```asm
# x86
cmp  eax, ebx
jl   somewhere        # "如果上一條 cmp 的結果是 less-than 就跳"
```

這有個問題：**`jl` 跟 `cmp` 之間必須沒有東西會改 flags**。一旦中間插入一條 `add`，flags 就被污染了。這對 scheduler、compiler、OoO 都是痛點。

RISC-V 的做法：branch 自帶比較。

```asm
blt  t0, t1, somewhere    # 如果 t0 < t1 就跳
```

沒有 flags register，沒有「上一條影響下一條」的隱性 dataflow。Branch 是 self-contained。

代價是：**RISC-V 沒有條件執行（conditional move / conditional execution）**。這在 ARMv7 很流行（`addlt r0, r1, r2`），但 RISC-V 直接說不要。後來為了彌補這個，Zicond 擴充才在 2023 年 ratify — 補了 `czero.eqz` / `czero.nez` 兩條。Ch 6 會講。

## 七種指令格式

所有 RV32I 指令都塞在 32 bit 裡，分成 6 種**基本格式** + 2 種**立即數變體**，共七種（B 其實是 S 的變體、J 其實是 U 的變體）。這張圖背下來：

```
 31                    25 24      20 19      15 14  12 11       7 6           0
┌────────────────────────┬──────────┬──────────┬──────┬──────────┬─────────────┐
│        funct7          │   rs2    │   rs1    │funct3│    rd    │   opcode    │  R-type
├────────────────────────┴──────────┼──────────┼──────┼──────────┼─────────────┤
│         imm[11:0]                 │   rs1    │funct3│    rd    │   opcode    │  I-type
├──────────────────────┬────────────┼──────────┼──────┼──────────┼─────────────┤
│     imm[11:5]        │    rs2     │   rs1    │funct3│ imm[4:0] │   opcode    │  S-type
├─┬────────────────────┼────────────┼──────────┼──────┼────────┬─┼─────────────┤
│ │    imm[10:5]       │    rs2     │   rs1    │funct3│imm[4:1]│ │   opcode    │  B-type
│12                    │            │          │      │       11│              │
├─┴────────────────────┴────────────┴──────────┴──────┼──────────┼─────────────┤
│                     imm[31:12]                      │    rd    │   opcode    │  U-type
├─┬──────────────────────┬─┬──────────────────────────┼──────────┼─────────────┤
│ │     imm[10:1]        │ │        imm[19:12]        │    rd    │   opcode    │  J-type
│20│                    11│                                                   │
└─┴──────────────────────┴─┴──────────────────────────┴──────────┴─────────────┘
```

**觀察點（spec 的刻意設計）**：

- `rd`（目標暫存器）永遠在 `[11:7]`。
- `rs1`（來源 1）永遠在 `[19:15]`。
- `rs2`（來源 2）永遠在 `[24:20]`。
- `opcode` 永遠在 `[6:0]`。

**這不是巧合，這是硬體工程師的禮物**。decoder 可以在知道 opcode 之前就把 `rs1` / `rs2` / `rd` 抓出來餵給 register file — 省掉一拍延遲。

立即數編碼是 RISC-V 最反直覺的部分：**同一顆 bit，在不同格式裡位置不同，但每一位的 sign bit 一律在 bit 31**。這是為了讓硬體的 sign-extension 邏輯可以共用。Ch 16 會徹底拆。現在先接受：**不要試圖手算立即數**，交給 assembler。

## 暫存器：x0 到 x31

RV32I 有 32 個整數暫存器，寬度 32 bit（RV64 就是 64 bit）。全部都可以讀寫，除了一顆：

- **`x0` 永遠是 0**。寫它沒效果，讀它永遠是 0。

這顆「零暫存器」是 RISC-V 省掉大量指令的關鍵。例如：

```asm
mv  t0, t1          # pseudo
addi t0, t1, 0      # 真實指令：t0 = t1 + 0

li  t0, 42          # pseudo
addi t0, x0, 42     # 真實指令：t0 = 0 + 42

nop                 # pseudo
addi x0, x0, 0      # 真實指令：x0 = x0 + 0（= 什麼都沒做）
```

`mv`、`li`、`nop`、`beqz`（= `beq rs, x0`）、`jr`（= `jalr x0, rs, 0`）… 一大堆常用操作都是靠 `x0` 的「dummy 角色」省下一個新 opcode。Ch 3 詳解。

## 看一個完整的範例

算 `sum(1..N)`：

```c
int sum(int n) {
    int s = 0;
    for (int i = 1; i <= n; i++) s += i;
    return s;
}
```

`riscv64-unknown-elf-gcc -march=rv32i -O1 -S -o sum.s sum.c`（注意：強制 `-march=rv32i`，否則會用 RV64 + M + A + C）拆出來會像：

```asm
sum:
    li    a5, 0              # s = 0       (實際: addi a5, x0, 0)
    li    a4, 1              # i = 1
    ble   a0, x0, .L_done    # if n <= 0 goto done   (pseudo: bge)
.L_loop:
    add   a5, a5, a4         # s += i
    addi  a4, a4, 1          # i++
    ble   a4, a0, .L_loop    # if i <= n goto loop
.L_done:
    mv    a0, a5             # return s   (pseudo: addi a0, a5, 0)
    ret                      # pseudo: jalr x0, ra, 0
```

看出幾件事：

1. 迴圈只用了 `add` / `addi` / `ble` / `mv` / `ret` 五種（其中四種是 pseudo）。
2. 所有比較都是 branch 指令自帶，沒有 flag register。
3. 寫回值是 `mv a0, a5` — 回傳值放 `a0`（Ch 2 會講 ABI）。

這就是 RISC-V 的典型風格：程式長，但每條指令乾淨。

## 六條容易搞混的 branch

branch 家族看起來多，但很規律：

```
beq   rs1, rs2, offset    # equal
bne   rs1, rs2, offset    # not equal
blt   rs1, rs2, offset    # less than          (signed)
bge   rs1, rs2, offset    # greater or equal   (signed)
bltu  rs1, rs2, offset    # less than          (unsigned)
bgeu  rs1, rs2, offset    # greater or equal   (unsigned)
```

**沒有 `bgt` / `ble`**。要表達 `a > b` 就 `blt b, a` — 把兩顆交換就好。assembler 會接受 `bgt` 當 pseudo 展開成 `blt rs2, rs1`。這是 RISC-V 的一貫風格：**能用對稱性省就省**。

## Branch 的距離限制

B-type 的立即數是 **13 bit signed**（最低位永遠 0，所以 12 bit + 隱式 0），範圍 `±4 KiB`。超過怎麼辦？

```asm
# 想做：if (a != b) jump_far
# 距離太遠
bne  a0, a1, .L_far   ← 組不起來，assembler 會抱怨

# 手動展開：
beq  a0, a1, .L_skip
jal  x0, .L_far       ← jal 範圍 ±1 MiB
.L_skip:
```

assembler 其實會幫你自動做這個 trick，但記住**硬體上 branch 的真實範圍是 ±4 KiB**。Ch 3 會看到 `jal` 的範圍為什麼是 ±1 MiB、更遠要靠 `auipc + jalr`。

## Load / Store 的 8 條

```
lb   rd, off(rs1)   # load byte,       sign-ext
lh   rd, off(rs1)   # load half,       sign-ext
lw   rd, off(rs1)   # load word        (RV32 最大粒度)
lbu  rd, off(rs1)   # load byte,       zero-ext
lhu  rd, off(rs1)   # load half,       zero-ext
sb   rs2, off(rs1)  # store byte
sh   rs2, off(rs1)  # store half
sw   rs2, off(rs1)  # store word
```

只有 load 分 signed / unsigned；store 不用分（bit pattern 寫進去而已）。RV64 會加 `lwu` / `ld` / `sd` 三條處理 64-bit 粒度。

**沒有 `lwpc`（load relative to PC）這種東西**。要 PC-relative load 一定要用 `auipc + lw` 配對。這對「位置無關程式碼（PIC）」非常關鍵，Ch 3 會拆。

## ecall / ebreak / fence

三條「system 類」：

- `ecall`：environment call，從 U/S mode 呼叫上一層（kernel / M-mode）。RV 的 syscall 指令。
- `ebreak`：debugger breakpoint。gdb 的 `b` 底下埋的就是它。
- `fence`：memory ordering barrier。Ch 14/15 會講。

這三條其實不是 integer instruction，但一起塞在 RV32I base 裡，因為沒有它們什麼都跑不了。

## 常見誤會

1. **「RV32I 就是 32 個整數暫存器」** — 錯。32 個暫存器是 `x0..x31` 對，但 RV32 指的是**資料路徑寬度 32 bit**。RV64I 一樣 32 個暫存器，只是每個 64 bit。
2. **「RISC-V 指令都是 32 bit」** — 錯。base ISA 是 32 bit，但 C 擴充指令是 16 bit，未來可能有 48-bit、64-bit 指令（spec 保留了編碼空間）。實務上 RV64GC（目標是 Linux）有大量 16-bit 指令。
3. **「沒有 condition code 所以比 ARM 慢」** — 這是 1990 年代的 intuition，現代 compiler + OoO CPU 完全反過來：沒 flags 反而好做 register renaming。SPEC 上 RISC-V 跟 ARMv8 差距接近零。
4. **「`lw` 要對齊」** — RISC-V spec 允許實作選擇要不要支援 misaligned access。多數硬體支援但可能慢很多。寫 code 時仍以對齊為默認。

## 動手練習

1. 用 `riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -O1 -S sum.c` 拆出組語，對照上面的範例。
2. 把 `sum` 改成 `int product(int n)` — `s *= i`。注意 RV32I **沒有乘法**，會看到 compiler 呼叫 `__mulsi3`。加 `-march=rv32im` 再編一次，看 `mul` 指令長什麼樣。
3. 手寫一條 `add t0, t1, t2` 的 32-bit 機器碼（funct7=0, funct3=0, opcode=0110011, x5/x6/x7）。用 `echo | riscv64-unknown-elf-as | riscv64-unknown-elf-objdump -d -` 驗證。
4. 寫一個會觸發 misaligned `lw` 的程式（`lw` 讀地址 `0x1001`），在 spike 跟 QEMU 看分別怎麼反應。
5. 用 `objdump -d` 找出一條 `jalr` 指令，對照 I-type 格式自己 decode 它。

## 自我檢核

- [ ] 我能說出 RISC-V 的四個核心設計原則
- [ ] 我能在一分鐘內把 RV32I 的六種指令格式畫出來
- [ ] 我知道 `x0` 的特殊性以及它如何省掉大量 opcode
- [ ] 我能解釋為什麼 RISC-V 沒有 condition code 而不變慢
- [ ] 我能手寫 sum(1..n) 的 RV32I 組語

下一章我們看暫存器的另一面 — `x0..x31` 不只是 32 個 slot，它們有強烈的**社會角色**：哪些是 argument、哪些 caller-saved、哪些留給 stack。這套規則叫 ABI，是 compiler 跟 linker 共同遵守的法律。

→ [Ch 2 Register 慣例與 ABI](./02-register-abi.md)
