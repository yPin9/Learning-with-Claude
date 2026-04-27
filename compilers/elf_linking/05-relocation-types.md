# Ch 5 — Relocation type 總論

> 目標：理解 relocation 為什麼要分幾十種 type、每種的計算公式、以及 RISC-V 常見 type（`R_RISCV_CALL` / `R_RISCV_PCREL_HI20` pair / `R_RISCV_RELAX`）的語意。這章結束你看 `objdump -r` 能讀懂每一筆 relocation。

## 為什麼要這麼多 type

想像最 naive 的 relocation：「把地址 A 填進位置 X」。一個 type 就夠。

但現實：

1. **不同指令格式的 immediate 拆法不同**：RISC-V 的 U-type 只有 20 bit、I-type 只有 12 bit、B-type 拆 4 塊。每種都要專屬 relocation。
2. **絕對地址 vs PC-relative**：同樣是填 `foo` 的地址，可能要「絕對值」或「`foo - PC`」。兩種公式、兩種 type。
3. **對齊要求**：某些 relocation 要求目標 alignment。違反 → 錯誤。
4. **widening / narrowing**：要填的值可能要 sign-extend、或 shift。

所以 relocation type **綁定 ISA 細節**。x86-64 有 38 種、RISC-V 有 60+ 種。每種都有精確的 spec。

## Relocation entry 的結構

```c
typedef struct {
    Elf64_Addr    r_offset;   // 要改的位置 (在 section 內 offset)
    Elf64_Xword   r_info;     // 打包的 symbol index + type
    Elf64_Sxword  r_addend;   // 常數 offset（只有 RELA）
} Elf64_Rela;
```

`r_info` 拆包：

```
sym_index = r_info >> 32
type      = r_info & 0xFFFFFFFF
```

## 通用公式

每個 relocation type 有個「計算公式」。常見記號：

- **S** = symbol 的 virtual address
- **A** = addend
- **P** = 要改的位置的 virtual address
- **GOT** = Global Offset Table 的 base address
- **PLT** = Procedure Linkage Table 的 entry

舉例：

- `R_X86_64_64`：填 `S + A` (絕對)
- `R_X86_64_PC32`：填 `S + A - P` (PC-relative)
- `R_X86_64_GOT32`：填 `G + A`（G = symbol 在 GOT 裡的 offset）
- `R_X86_64_PLT32`：填 `L + A - P`（L = PLT entry address）

spec 表會列每個 type 的公式。**你不用背，但要看得懂表**。

## x86-64 的 relocation 速覽

幾個最常見：

| Type | 公式 | 用途 |
|------|------|------|
| `R_X86_64_NONE` | — | 無作用 |
| `R_X86_64_64` | S + A | 絕對 64-bit 地址（static data）|
| `R_X86_64_32` | S + A | 絕對 32-bit（可能 overflow）|
| `R_X86_64_PC32` | S + A - P | 32-bit PC-relative（call/jmp 常用）|
| `R_X86_64_GOTPCREL` | G + GOT + A - P | GOT slot 的 PC-relative |
| `R_X86_64_PLT32` | L + A - P | PLT entry 的 PC-relative |
| `R_X86_64_TPOFF32` | @tpoff | TLS offset |

**`R_X86_64_PC32` 是最重要的一個**：x86-64 的 call / jmp / RIP-relative load 幾乎全用它。

## RISC-V 的 relocation：特別多、特別精細

RISC-V 因為**固定 4-byte 指令 + 立即數拆分怪異**，每種指令格式都有自己的 relocation。列出主要的：

### 基本 PC-relative pair

```
R_RISCV_PCREL_HI20       auipc 的 imm[31:12]
R_RISCV_PCREL_LO12_I     對應 auipc 的 addi / lw 的 imm[11:0]
R_RISCV_PCREL_LO12_S     對應 auipc 的 sw / sb 的 imm[11:0]
```

**這三個是 RISC-V user-space code 最常見的 relocation**。`riscv` Ch 3 解釋過 `auipc + addi/lw/sw` idiom 是 RISC-V 的靈魂，它們對應就是這一組 reloc。

### 為什麼 `PCREL_LO12` 的 symbol 很奇怪

先複習：

```asm
1:  auipc a0, %pcrel_hi(foo)       # R_RISCV_PCREL_HI20 symbol=foo
    addi  a0, a0, %pcrel_lo(1b)    # R_RISCV_PCREL_LO12_I symbol=1b (的 label)
```

**`PCREL_LO12_I` 的 symbol 不是 `foo`，是那條 auipc 的 label**。看 `objdump -r`：

```
6ec: R_RISCV_PCREL_HI20     .rodata+0x0
6f0: R_RISCV_PCREL_LO12_I   .L0                ← label 名字
```

linker 拿到 `PCREL_LO12_I` 時：

1. 找到對應的 `PCREL_HI20` (`.L0`)
2. 從 HI20 的 entry 讀 original symbol（`.rodata`）跟 addend
3. 算 `foo - (auipc 的 PC)` 的 low 12 bit
4. 填進 addi 的 imm

為什麼這麼繞：**PCREL_HI20 填的是 PC 的 bit [31:12]，LO12 填 bit [11:0]。兩個必須從同一個 `PC + foo - PC` 切**。linker 需要知道對應的 HI 才能一致。用 label 指回去最直接。

### 絕對地址 relocation

```
R_RISCV_32      32-bit 絕對
R_RISCV_64      64-bit 絕對
R_RISCV_HI20    lui 的 20-bit
R_RISCV_LO12_I  對應 lui 的 addi
R_RISCV_LO12_S  對應 lui 的 sw
```

`-mcmodel=medlow` 用這些。不常見，多數場景 `medany` 的 PC-relative 更好。

### Call relocation

```
R_RISCV_CALL       auipc + jalr 一對，呼叫 symbol
R_RISCV_CALL_PLT   同上但走 PLT（動態連結）
```

**`R_RISCV_CALL` 一次綁兩條指令**。linker 把兩條 32-bit 指令當作「8 byte block」處理、算 `S - P` 並填進 HI20 + JALR 的 imm。

### Branch / Jump

```
R_RISCV_BRANCH    B-type branch (beq/bne/...) 的 offset
R_RISCV_JAL       J-type jal 的 offset
```

B-type 範圍 ±4KiB、J-type 範圍 ±1MiB。超過 range → `relocation truncated` error。

### Linker relaxation

```
R_RISCV_RELAX     告訴 linker：前一條 relocation 可以被 relax
R_RISCV_ALIGN     要求此位置對齊，並標示可以塞/抽 nop 達成對齊
```

**這兩個是 RISC-V 獨有的**。x86 / ARM 的 linker 不會改指令，只填 operand。RISC-V 的 linker 可能：

- 把 `auipc + addi` (8 byte) 改成 `c.addi` (2 byte)
- 把 `call foo` 的 2 條指令變 1 條 `jal`
- 整個 section 變短、其他地方的 offset 全部要重算

Ch 6 專章處理。

### TLS relocation

```
R_RISCV_TLS_GD_HI20 / LO12          Global-Dynamic
R_RISCV_TLS_LDM_HI20 / LO12         Local-Dynamic
R_RISCV_TLS_IE_HI20 / LO12          Initial-Exec
R_RISCV_TLS_LE_HI20 / LO12          Local-Exec
R_RISCV_TPREL_HI20 / LO12 / I / S   thread-pointer relative
```

Ch 12 會講 TLS。短版：每種 TLS access model 需要專屬 relocation。

### GOT / PLT relocation

```
R_RISCV_GOT_HI20      auipc + 後面指令讀 GOT
R_RISCV_GOT_LO12      配對
R_RISCV_PLT32         PLT entry 的 32-bit offset
```

dynamic linking 用。Ch 10 講。

### Debug 類

```
R_RISCV_ADD32 / SUB32     處理 DWARF 的 section offset (相減算範圍)
R_RISCV_ADD64 / SUB64
R_RISCV_SET6 / SET8 / ...
```

這些出現在 `.debug_*` section 裡。DWARF 常用「地址 A - 地址 B」的方式表示範圍，compile 時 A 跟 B 都不知道 → 用一對 ADD/SUB relocation 等 linker 算。Ch 15 細節。

## 一筆完整 relocation 的人工展開

拿本章開頭的例子：

```
.text 裡 offset 0x6ec: auipc a0, 0
.text 裡 offset 0x6f0: addi  a0, a0, 0

Relocations:
  0x6ec: R_RISCV_PCREL_HI20     .rodata+0x0
  0x6ec: R_RISCV_RELAX          *ABS*
  0x6f0: R_RISCV_PCREL_LO12_I   .L0   (.L0 在 0x6ec)
  0x6f0: R_RISCV_RELAX          *ABS*
```

假設 linker layout 完成後：

- `.text` 起點 = 0x1000, offset 0x6ec 的絕對地址 = 0x16ec
- `.rodata` 起點 = 0x2000

公式：`offset = .rodata - auipc_address = 0x2000 - 0x16ec = 0x914`

拆 0x914 成 HI20 + LO12（注意 sign-extend trick）：
- 低 12 bit = `0x914 & 0xfff = 0x914`。但因為 addi 是 sign-extend，如果 0x914 最高 bit = 1 要特殊處理
- 0x914 的 bit 11 = 1 (0x914 > 0x7ff) → 這是「負」的 12-bit 值 → 要 HI20 +1 補回
- 算 `HI20 = (offset + 0x800) >> 12 = (0x914 + 0x800) >> 12 = 0x1114 >> 12 = 0x1`
- 驗證：`0x1 << 12 + 0x914 = 0x1914`，但我們要 `0x914`... hmm
- 實際：`0x1 << 12 + sign_ext(0x914) = 0x1000 + (-0x6ec) = 0x914` ✓

linker 填：

- 0x6ec 的 auipc：imm 欄位 = 0x1（HI20）
- 0x6f0 的 addi：imm 欄位 = 0x914（LO12, 會被 sign-extend）

runtime 時：

- `auipc a0, 0x1` → a0 = PC(0x16ec) + (0x1 << 12) = 0x16ec + 0x1000 = 0x26ec
- `addi a0, a0, 0x914` → a0 = 0x26ec + sign_ext(0x914) = 0x26ec + (-0x6ec) = 0x2000 ✓

**這套公式有一個「+0x800」的 trick** 很容易錯。`riscv` Ch 3 有提到。手寫 linker 時務必測試 low 12 bit 邊界。

## Relocation overflow

每種 type 有可表達 range。例如：

- `R_RISCV_BRANCH` 只能 ±4 KiB
- `R_RISCV_JAL` 只能 ±1 MiB
- `R_RISCV_CALL` 可以 ±2 GiB（因為是 auipc+jalr）
- `R_X86_64_PC32` 只能 ±2 GiB

如果 section layout 讓距離超過 range → linker 報錯：

```
relocation truncated to fit: R_RISCV_JAL against symbol `foo'
```

解法：

- 讓 symbol 跟 call site 更近（linker script 調 ordering）
- 換更大 range 的 relocation（`call` 取代 `jal`）
- 讓 compiler 用 `-mcmodel=medany` 或類似 flag

## Section-local vs external symbol

Relocation 指向的 symbol 可分兩種：

1. **本 `.o` 的 LOCAL symbol**：linker 直接查 section index + offset 算地址
2. **外部 GLOBAL symbol**：等 symbol resolution 配對後才知道

看 `objdump -r` 輸出時：
- `R_xxx .text+0x100`：是 section 相對 offset（本地）
- `R_xxx printf`：是 symbol（通常外部）

## 動態 relocation：不是 linker 的事

講完的都是 **link-time relocation** —— linker 填好就不再動。

還有一類 **runtime relocation**，由 dynamic linker (`ld.so`) 在 load 時處理，存在 `.rela.dyn` / `.rela.plt` 裡。Ch 10 會深入。

## 幾個奇特的 type

### `R_RISCV_SUB_ULEB128` / `R_RISCV_SET_ULEB128`

DWARF 用。處理「LEB128 編碼的地址差」。LEB128 是變長 encoding，relocation 要處理變長的 in-place 修改，這兩個是 2024 才 ratify 的新 type。

### `R_RISCV_SUB6` / `SUB8` / `SUB16` / `SUB32` / `SUB64`

做 `value -= S + A`。debug info 的 delta 計算用。

## 常見誤會

1. **「relocation 只填一個值」**：某些 type 像 `R_RISCV_CALL` 一次改兩條指令；某些（SUB 類）是「做減法」不是直接覆蓋。
2. **「relocation 存在 `.text` 裡」**：不是。relocation 存在 `.rela.text`（或 `.rel.text`）裡。`.text` 只有指令本體（暫填空位）。
3. **「link 後 relocation 就沒了」**：static linking 後 link-time relocation 消失，但 dynamic relocation（`.rela.dyn`）會留著給 runtime。
4. **「不同 arch 的 relocation 可以 port」**：不。`R_RISCV_PCREL_HI20` 跟 `R_X86_64_PC32` 完全不同語意，不能直接換。
5. **「`R_RISCV_RELAX` 是一個動作」**：不，它是 hint（對前一筆 relocation）。linker 可以選擇忽略。

## 動手練習

1. 編個最小 `.c`（`int x = 42; int main() { return x; }`），`objdump -r hello.o` 列出所有 relocation、對照 source 找出每筆對應哪行。
2. 故意讓 function 超過 ±1 MiB（用 linker script 把兩個 function 硬放遠）→ 觀察 `R_RISCV_JAL` 的 truncated 錯。
3. 寫個包含 static vs extern variable 的程式，對比兩者在 `objdump -r` 裡的 relocation 欄位。
4. 用 `-mcmodel=medlow` vs `medany` 各編一次，對比 relocation type 差異（HI20/LO12 vs PCREL_HI20/LO12）。
5. 讀一段 `objdump -d` 的 RISC-V binary，隨便挑一條 `auipc + addi`，用本章公式手算 linker 填的值。

## 自我檢核

- [ ] 我能解釋為什麼 RISC-V 需要 HI20 / LO12 pair
- [ ] 我能讀 `objdump -r` 的輸出並說出每筆 relocation 的語意
- [ ] 我知道 `R_RISCV_PCREL_LO12_I` 的 symbol 是 label 而不是 target symbol，並能解釋為什麼
- [ ] 我能分辨 link-time relocation 跟 runtime relocation
- [ ] 我能預測什麼 code 會觸發 `relocation truncated` 並提出修法

下一章進最 RISC-V 靈魂的部分 — linker relaxation。這是 RISC-V compiler / linker 工程師的招牌技能。

→ [Ch 6 RISC-V 專屬 relocation 與 linker relaxation](./06-riscv-relaxation.md)
