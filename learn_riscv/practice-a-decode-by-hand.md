# 練習 A — 手解 opcode

> 目標：把 Ch 16 的 decode / encode 技能變肌肉記憶。20 題手動練習，每題附驗證步驟。能全部做對 = 你可以閉著眼讀 objdump、可以寫 disassembler、可以在面試被要求「手寫 add x5, x6, x7 的 machine code」時不手忙腳亂。

## 準備

### 工具

```bash
# 單指令 encoding 驗證
echo "ADD x5, x6, x7" | riscv64-unknown-elf-as -o /tmp/a.o -
riscv64-unknown-elf-objdump -d /tmp/a.o

# Python 驗證小工具（可選）
pip install riscv-decoder
```

### 格式複習

```
R-type:  [funct7][rs2][rs1][funct3][rd][opcode]
I-type:  [imm11:0    ][rs1][funct3][rd][opcode]
S-type:  [imm11:5][rs2][rs1][funct3][imm4:0][opcode]
B-type:  [imm12][imm10:5][rs2][rs1][funct3][imm4:1][imm11][opcode]
U-type:  [imm31:12                       ][rd][opcode]
J-type:  [imm20][imm10:1][imm11][imm19:12][rd][opcode]
```

## 第一關：Encode（組出機器碼）

對每題：
1. 查 spec 的 opcode / funct3 / funct7
2. 把每個欄位寫成 bit
3. 組 32-bit、轉 hex
4. 用 `as` 驗證

### A.1（暖身）

```
add x1, x2, x3
```

**期望 output**: `0x003100B3`

### A.2

```
sub x10, x11, x12
```

提示：`sub` 跟 `add` 差在 funct7 = 0x20。

### A.3

```
addi x5, x6, 42
```

### A.4（負 immediate）

```
addi x5, x6, -1
```

提示：-1 in 12-bit signed 是 `0xFFF`。

### A.5（I-type shift）

```
slli x5, x6, 10
```

提示：shift immediate 指令雖然是 I-type，但 imm[11:5] 必須是某個固定 funct7。

### A.6

```
lw x5, 20(x6)
```

### A.7（S-type）

```
sw x5, 20(x6)
```

注意 S-type 的 imm 拆成兩塊！這題是最容易錯的。

### A.8（B-type — 正 offset）

```
beq x5, x6, 16
```

### A.9（B-type — 負 offset）

```
bne x5, x6, -12
```

### A.10（U-type）

```
lui x5, 0x12345
```

### A.11（U-type 特殊）

```
auipc x5, 0x1000
```

### A.12（J-type）

```
jal x1, 1024
```

### A.13（J-type 負）

```
jal x0, -2048
```

提示：`jal x0, ...` = `j` pseudo。

### A.14（I-type jump）

```
jalr x1, x5, 16
```

### A.15（記得 x0 的神奇用途）

```
xor x5, x5, x0
```

寫出 encoding，然後**思考**：這條指令實際做什麼？

### A.16（pseudo 分辨）

下列是 `addi x0, x0, 0`。它是哪個 pseudo？encode 它。

### A.17（`ret`）

`ret` 是什麼 real instruction 的 pseudo？encode 它。

### A.18（挑戰：負 imm 的 S-type）

```
sb x5, -8(x6)
```

### A.19（挑戰：branch 到後面遠處）

```
blt x5, x6, 2048
```

（假設 label 離你 +2048 byte，正好在 branch 範圍外？還是內？算一下）

### A.20（Ultimate challenge）

```
# atomic
amoadd.w.aqrl x0, x5, (x10)
```

提示：這是 R-type with `aq` / `rl` 編在 funct7 高 bit。查 A 擴充 spec table。

## 第二關：Decode（hex → asm）

對每題：
1. 把 hex 展開成 32-bit binary
2. 取 bit[6:0] 看 opcode
3. 根據 opcode 分 type，取 rd / rs1 / rs2 / funct3 / funct7
4. 確認是哪條指令
5. 用 echo + `as` + `objdump` 驗證

### A.21

`0x00518213`

### A.22

`0x40520233`

### A.23

`0xFE010113`

### A.24

`0x00432023`

### A.25

`0xFEC58CE3`

### A.26

`0x123450B7`

### A.27

`0x008000EF`

### A.28

`0x00050067`

### A.29

`0x0007B503`

### A.30

`0xFFC12023`

## 第三關：神秘機器碼

以下 hex 不是 base RV64 指令。是什麼？

### A.31

`0x00430283`

提示：這是 RV64G 的合法指令，但用了 F/D 擴充之外的 opcode space。可能是什麼？（hint: check funct3 + opcode）

### A.32

`0xC0F5A02F`

提示：opcode 0101111 是 A 擴充的。

### A.33

`0x02B500B3`

提示：opcode 0110011 + funct7 0x01 是 M 擴充。

### A.34

`0x8113`

提示：只有 16-bit — 壓縮指令。

### A.35

`0x00508263`

提示：很普通的 branch，但 offset 算對嗎？

## Self-check 解答（部分）

完整解答我不給（親手做才有價值）。但提供幾題作為 sanity check：

- **A.1**: `0x003100B3`
- **A.2**: `0x40C585B3`
- **A.3**: `0x02A30293`
- **A.10**: `0x123452B7`
- **A.11**: `0x010002D7`（rd=x5 的 auipc 0x1000）

如果你的答案跟上面不一致，檢查兩件事：

1. **register number 對嗎**：x5 在 encoding 是 `00101` (5)，x10 是 `01010` (10)，不要跟 ABI 別名搞混。
2. **immediate 的 bit 拆分對嗎**：特別是 S/B/J type。

## Decode 部分的示範（A.21 做給你看）

```
0x00518213
= 0000 0000 0101 0001 1000 0010 0001 0011
```

切：

```
bit [6:0]  = 0010011   → opcode = I-type integer (addi/slli/...)
bit [11:7] = 00100     → rd = x4 (tp)
bit [14:12]= 000       → funct3 = 000 → ADDI
bit [19:15]= 00011     → rs1 = x3 (gp)
bit [31:20]= 0000 0000 0101  → imm = 5 (positive)
```

**結論**: `addi x4, x3, 5`

驗證：

```bash
$ echo "addi x4, x3, 5" | riscv64-unknown-elf-as -o /tmp/a.o -
$ riscv64-unknown-elf-objdump -d /tmp/a.o
   0:   00518213    addi    x4,x3,5
```

對。

## 做完這 35 題你會有什麼

1. **Spec 裡的 encoding table 不再可怕**。看到就能直接套用。
2. **面試時 objdump 的任何一條你能拆**。
3. **寫 assembler / disassembler / emulator 的門檻降到一半**。
4. **能直接 debug toolchain 的 encoding bug**（有些 bug 是 encoding 的細節錯）。

這也是 final project 的熱身 — 下一步寫 RV32I emulator 時，你需要 decode 每一條指令。

## 遇到困難時

- **`as` 抱怨語法**：檢查你的 register 名稱是否是 `x5` 不是 `r5`、immediate 是否在 range。
- **你的 encoding 跟 objdump 不一致**：先檢查立即數的 bit 拆法。S/B/J 型最容易出錯。
- **某條指令不認得**：可能是 pseudo。先查是否展開後才是 real instruction。
- **opcode 查不到**：`custom-0..3` slot 不在 base spec，是客製擴充。

## 下一步

做完本練習你的手解能力就 ok 了。接下來：

→ [練習 B：用 spike 跑 baremetal](./practice-b-baremetal-on-spike.md)
→ [Final Project：Mini RV32I Emulator](./final-project-rv32i-emulator.md)
