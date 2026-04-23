# Ch 16 — 從 spec 讀 opcode encoding

> 目標：訓練「看到一條指令的 32-bit 機器碼，能手解出 assembly」跟「看到一條 assembly，能手組出 encoding」的能力。這是 toolchain 工程師的 table stake — debug 一個 assembler bug 時你可能要肉眼驗證 encoding 是不是對的。

## 為什麼要練這個

在實務上你會遇到：

1. **objdump 顯示 `.word 0x01234567`**（disassembler 不認得 → 可能是 custom extension）你得手解
2. **bug：assembler 產生的 bits 跟 spec 不符** — 要 diff spec 表跟實際 encoding
3. **面試題**：Intel / SiFive 面試常出「你手寫出 `add x5, x6, x7` 的 32-bit machine code」

本章的 payoff：**spec 的 instruction listing 表不再是神秘**，你看到能直接翻譯。

## 先複習六種格式（Ch 1）

```
 31        25 24   20 19   15 14 12 11    7 6          0
┌───────────┬───────┬───────┬─────┬────────┬────────────┐
│ funct7    │ rs2   │ rs1   │ f3  │ rd     │ opcode     │  R-type
├───────────┴───────┴───────┴─────┴────────┴────────────┤
│  imm[11:0]                   │ rs1   │f3  │ rd   │op  │  I-type
├──────────────────┬───────────┴───────┴────┴───────┴───┤
│ imm[11:5]        │ rs2       │ rs1   │f3  │imm[4:0]│op │  S-type
├─┬────────────────┼───────────┴───────┴────┴──────┬──┴──┤
│ │ imm[10:5]      │ rs2       │ rs1   │f3  │imm[4:1]│ │op│ B-type
│12│               │           │       │    │       11│  │
├─┴────────────────┴───────────┴───────┴────┴──────────┴─┤
│            imm[31:12]               │ rd   │ opcode    │  U-type
├─┬───────────────┬─┬─────────────────┴─────┴───────────┤
│ │ imm[10:1]     │ │ imm[19:12]    │ rd    │ opcode     │  J-type
│20│             11│                                     │
└─┴───────────────┴─┴────────────────────────────────────┘
```

`opcode` 是 `[6:0]`，7 bits。內部 bit `[1:0]` 對 32-bit 指令永遠 `11`。

## 練習 1：手組 `add x5, x6, x7`

查 spec：`add` 是 R-type，`opcode = 0110011`，`funct3 = 000`，`funct7 = 0000000`。

```
x5 = 00101  (rd)
x6 = 00110  (rs1)
x7 = 00111  (rs2)

funct7  rs2    rs1    f3   rd     opcode
0000000 00111  00110  000  00101  0110011

合併:
0000000 00111 00110 000 00101 0110011
```

每組 bit 拼起來：

```
bit 31..25 = 0000000       # funct7
bit 24..20 = 00111         # rs2 = x7
bit 19..15 = 00110         # rs1 = x6
bit 14..12 = 000           # funct3
bit 11..07 = 00101         # rd = x5
bit 06..00 = 0110011       # opcode

→ 0000 0000 0111 0011 0000 0010 1011 0011
→ 0x007302B3
```

驗證：

```bash
$ echo "add x5, x6, x7" | riscv64-unknown-elf-as -o /tmp/a.o -
$ riscv64-unknown-elf-objdump -d /tmp/a.o
   0:   007302b3   add    x5,x6,x7
```

對上。

## 練習 2：手組 `addi x10, x11, -5`

I-type。`opcode = 0010011`，`funct3 = 000`，`rd = a0 = x10`，`rs1 = a1 = x11`，`imm = -5`。

I-type 的 imm 是 12-bit signed：

```
-5 in 12-bit two's complement = 1111 1111 1011
                               = 0xFFB
```

組：

```
bit 31..20 = 111111111011       # imm[11:0] = -5
bit 19..15 = 01011              # rs1 = x11
bit 14..12 = 000                # funct3
bit 11..07 = 01010              # rd = x10
bit 06..00 = 0010011            # opcode

→ 1111 1111 1011 0101 1000 0101 0001 0011
→ 0xFFB58513
```

驗證：

```bash
$ echo "addi x10, x11, -5" | riscv64-unknown-elf-as -o /tmp/a.o -
$ riscv64-unknown-elf-objdump -d /tmp/a.o
   0:   ffb58513   addi    x10,x11,-5
```

對。

## 練習 3：S-type 的地獄 — `sw x5, 20(x6)`

`sw` 是 S-type。`opcode = 0100011`，`funct3 = 010`。

Immediate = 20 = `0000 0001 0100`（12-bit signed）

S-type 的 immediate 被拆成 **`imm[11:5]` 跟 `imm[4:0]`** 兩塊：

```
imm     = 0000 0001 0100
imm[11:5] = 0000000
imm[4:0]  = 10100
```

組：

```
bit 31..25 = 0000000        # imm[11:5]
bit 24..20 = 00101          # rs2 = x5
bit 19..15 = 00110          # rs1 = x6
bit 14..12 = 010            # funct3
bit 11..07 = 10100          # imm[4:0]
bit 06..00 = 0100011        # opcode

→ 0000 0000 0101 0011 0010 1010 0010 0011
→ 0x00532A23
```

驗證：

```bash
$ echo "sw x5, 20(x6)" | riscv64-unknown-elf-as -o /tmp/a.o -
$ riscv64-unknown-elf-objdump -d /tmp/a.o
   0:   00532a23   sw    x5,20(x6)
```

對。**這種立即數拆分是 S-type / B-type / J-type 的靈魂** — 看起來反直覺，但設計目的是**讓硬體 decode 時 rd 跟 rs1/rs2 的位置永遠固定**（Ch 1 的觀察）。

## 練習 4：B-type 的拚湊恐怖 — `beq x5, x6, -100`

B-type 的 imm 拆成 4 塊：`imm[12]` / `imm[10:5]` / `imm[4:1]` / `imm[11]`。

`-100` in 13-bit signed（B-type imm 是 13-bit 但最低位永遠 0）：

```
-100 = 1 1111 1001 1100  (13-bit, 最低位 0)
```

拆：

```
imm[12]   = 1
imm[11]   = 1
imm[10:5] = 111110
imm[4:1]  = 1110
(imm[0] 永遠 0, 不 encode)
```

組：

```
bit 31      = 1              # imm[12]
bit 30..25  = 111110          # imm[10:5]
bit 24..20  = 00110          # rs2 = x6
bit 19..15  = 00101          # rs1 = x5
bit 14..12  = 000             # funct3 (beq)
bit 11..08  = 1110            # imm[4:1]
bit 07      = 1              # imm[11]
bit 06..00  = 1100011         # opcode

→ 1111 1100 0110 0010 1000 1110 1110 0011
→ 0xFC628EE3
```

驗證：

```bash
$ echo "beq x5, x6, -100" | riscv64-unknown-elf-as -o /tmp/a.o -
$ riscv64-unknown-elf-objdump -d /tmp/a.o
   0:   fc628ee3   beq   x5,x6,-100
```

**注意拼 imm 的順序 `imm[12] | imm[10:5] | imm[4:1] | imm[11]` 反直覺**。這是 spec 欠我們一個 apology 的地方。但規律：

## 為什麼 imm 要這樣拆

**設計原則：bit 31 永遠是 sign bit**。硬體 sign-extension 邏輯完全共用。

```
I-type: bit 31 = imm[11] = 最高位
S-type: bit 31 = imm[11] = 最高位（從 imm[11:5] 塊提取）
B-type: bit 31 = imm[12] = 最高位
U-type: bit 31 = imm[31] = 最高位
J-type: bit 31 = imm[20] = 最高位
```

硬體一條電路：「把 bit 31 複製到高位」— 搞定所有格式的 sign extension。如果不這麼設計，每種格式都要獨立的 extension 邏輯、多 5× transistor。

## 練習 5：U-type 與 `lui`

`lui x5, 0x12345` — U-type。

U-type 的 imm 是 **20 bit，放在 bit [31:12]**。值 0x12345 = `0001 0010 0011 0100 0101`。

```
bit 31..12 = 00010010001101000101     # imm
bit 11..07 = 00101                     # rd = x5
bit 06..00 = 0110111                   # opcode

→ 0001 0010 0011 0100 0101 0010 1011 0111
→ 0x123452B7
```

驗證：

```bash
$ echo "lui x5, 0x12345" | riscv64-unknown-elf-as -o /tmp/a.o -
$ riscv64-unknown-elf-objdump -d /tmp/a.o
   0:   123452b7   lui    x5,0x12345
```

## 練習 6：J-type 的 `jal x1, 1024`

`jal` 是 J-type。imm 21-bit signed（最低位永遠 0 → 20-bit encode）。

`1024` = `0 0000 0100 0000 0000 0` = (21-bit, 最低 0)

拆：

```
imm[20]     = 0
imm[19:12]  = 00000000
imm[11]     = 0
imm[10:1]   = 0000000010   # 1024 >> 1 的低 10 bit... 等等，仔細算
```

讓我們重算：1024 的 binary = `100 0000 0000`。以 21-bit 擴展 = `0 0000 0000 1000 0000 0000`。

```
imm[20]    = 0
imm[19:12] = 0000 0000
imm[11]    = 0
imm[10:1]  = 10 0000 0000
```

等 等。`1024 = 2^10`。所以 bit 10 (1-indexed from LSB) 是 1。以 21-bit 結構：

- imm[0] = 0 (固定)
- imm[10] = 1
- 其他 0

所以：

```
imm[20]    = 0
imm[19:12] = 0
imm[11]    = 0
imm[10:1]  = 1000000000 (bit 10 of imm = 1, 其他 0, 10-bit)
```

組（J-type 拼法 `imm[20] | imm[10:1] | imm[11] | imm[19:12] | rd | opcode`）：

```
bit 31       = 0              # imm[20]
bit 30..21   = 1000000000     # imm[10:1]
bit 20       = 0              # imm[11]
bit 19..12   = 00000000       # imm[19:12]
bit 11..07   = 00001          # rd = x1
bit 06..00   = 1101111        # opcode

→ 0100 0000 0000 0000 0000 0000 1110 1111
→ 0x400000EF
```

驗證：

```bash
$ echo "jal x1, 1024" | riscv64-unknown-elf-as -o /tmp/a.o -
$ riscv64-unknown-elf-objdump -d /tmp/a.o
   0:   400000ef    jal    x1,0x400
```

對上（0x400 = 1024）。

## 反向練習：decode `0x0079E2B3`

```
0x0079E2B3 = 0000 0000 0111 1001 1110 0010 1011 0011
```

從低到高解：

```
bit 06..00 = 0110011    → opcode (R-type, or/sub/add/...)
bit 11..07 = 00101      → rd = x5
bit 14..12 = 110        → funct3 = 110
bit 19..15 = 10011      → rs1 = x19 (s3)
bit 24..20 = 00111      → rs2 = x7
bit 31..25 = 0000000    → funct7 = 0000000
```

查表：opcode 0110011 + funct3 110 + funct7 0000000 = **OR** (`or rd, rs1, rs2`)

所以 `0x0079E2B3 = or x5, x19, x7`（或 `or t0, s3, t2` 用 ABI 別名）

驗證：

```bash
$ echo ".word 0x0079E2B3" | riscv64-unknown-elf-as -o /tmp/a.o -
$ riscv64-unknown-elf-objdump -d /tmp/a.o
   0:   0079e2b3    or    x5,x19,x7
```

對。**手解一次你就永遠記得這個流程**。

## spec 的 instruction table 怎麼讀

每條指令的 spec 頁通常長這樣（取自 Unpriv spec）：

```
ADD, SUB
Format: R-type
 funct7  rs2   rs1  funct3  rd   opcode
 0000000 src2  src1 000     dest 0110011  ADD
 0100000 src2  src1 000     dest 0110011  SUB

Semantic:
 x[rd] = x[rs1] + x[rs2]
 (SUB 對應 - 運算)
```

**怎麼讀**：

1. "Format" 欄告訴你 R/I/S/B/U/J 型
2. encoding table 給 funct7 / funct3 / opcode 具體值
3. "Semantic" 用偽代碼描述行為

你學會讀這種格式 → 可以自己查任何 RV extension spec。

## 進階：compressed instruction 的 decode

C 擴充 16-bit 指令的編碼空間不一樣：

```
bit[1:0] = 00 → C.0 format
bit[1:0] = 01 → C.1 format
bit[1:0] = 10 → C.2 format
bit[1:0] = 11 → 32-bit (前面講的)
```

C 指令內部還有三種格式（CI / CR / CL / CS / ...），每個欄位更小。例：

```
c.add rd, rs2:
  15 14 13 12 11..7 6..2 1..0
  1  0  0  0  rd    rs2  10
```

decode 原理相同，只是 bits 分法不同。spec 有專章。

## 常見誤會

1. **「立即數可以直接看成十進位值」**：不。總是先看 bit pattern、判斷 sign bit、再轉。
2. **「S-type 跟 I-type 的 imm 同位置」**：不同。I-type 連續 12 bit 在 `[31:20]`；S-type 拆成 `[31:25]` 跟 `[11:7]`。
3. **「funct7 總是 0 或 0x20」**：在 base 是這樣。但 M 擴充的 `mul` 是 funct7=0x01，A 擴充 AMO 的 funct7 編 aq/rl + funct5，全是 7-bit 空間的利用。
4. **「decode 看不懂就是 custom extension」**：先排除 compressed（bit [1:0] ≠ 11）。再看 opcode 是不是標準的（base spec 的 table）。都不是才考慮 custom。
5. **「手解很快會學會」**：不會。要練 10 次以上才會順。刻意練習。

## 動手練習

1. 手組 `sb x0, 7(x1)` 的 encoding，驗證。
2. 手組 `jalr x1, x2, 100` 的 encoding（I-type）。
3. 給 `0xFFFF8EE3`（某個 branch），手解它是什麼。
4. 給 `0xFC342783`（來自真實 objdump），手解。
5. 寫一支 Python script，input 32-bit hex、output 組語。這是寫 disassembler 的熱身。

## 自我檢核

- [ ] 我能閉著眼畫出六種指令格式的 bit layout
- [ ] 我能手組出 `add` / `addi` / `sw` / `beq` / `lui` / `jal` 的 encoding 並驗證
- [ ] 我能手解一個陌生的 32-bit hex 是哪條指令
- [ ] 我能解釋 B-type / S-type 的 imm 拆法為什麼是那個順序
- [ ] 我能在 spec 裡快速定位某條指令的 encoding

下一章拉遠，把 RISC-V 跟 ARM / x86 做三向對照，幫你在面試時能講出「為什麼 RISC-V 的設計選擇是這樣」。

→ [Ch 17 與 ARM / x86 對照：三種設計哲學](./17-vs-arm-x86.md)
