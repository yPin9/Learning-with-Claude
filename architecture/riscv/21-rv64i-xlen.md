# Ch 21 — XLEN=64 的意義：暫存器加寬、指令集延伸規則

> 目標：理解 XLEN 從 32 變 64 對整個 ISA 的影響；能說清楚 RV64I 是 RV32I 的嚴格超集意味著什麼；知道哪些指令行為有微妙變化。

---

## 21.1 XLEN 是什麼

XLEN（Integer Register Width）是 RISC-V spec 裡定義暫存器寬度的符號。它不是某個 CSR 的欄位，而是 ISA 的基本參數——所有指令的操作寬度都以 XLEN 為基準。

```
RV32I：XLEN = 32   每個通用暫存器是 32 bit
RV64I：XLEN = 64   每個通用暫存器是 64 bit
RV128I：XLEN = 128  （實驗性，幾乎沒有實作）
```

XLEN 的值在執行期間可以因 privilege level 不同而不同（這是 RV64 特有的設計，Ch 32 會說），但對大多數程式而言，XLEN 就是固定的。

---

## 21.2 暫存器全面加寬

RV64I 的通用暫存器（GPR）從 32 個 × 32-bit 變成 32 個 × 64-bit。這影響到每一個暫存器：

```
暫存器    RV32I        RV64I
------    --------     --------
x0        32-bit 0     64-bit 0（硬連 0，寫入無效）
x1 (ra)   32-bit       64-bit
x2 (sp)   32-bit       64-bit
...
x31       32-bit       64-bit
PC        32-bit       64-bit
```

**CSR 也加寬**：mstatus、sstatus、mepc、sepc 等 CSR 在 RV64 下都是 64-bit。但不是每個 CSR 的每個欄位都被使用——高位有些是保留或 WARL（Write Any Read Legal）。

---

## 21.3 RV64I 是 RV32I 的嚴格超集

RISC-V spec 的這句話要精確理解：

> 「RV32I 的所有指令在 RV64I 上有相同的功能語意」

但「相同」不代表「一模一樣」。差異在於：RV64I 的暫存器是 64-bit，32-bit 算術的結果會 **sign-extend（符號延伸）到 64-bit**。

範例：

```
# RV32I：add t0, t1, t2
# 結果：32-bit 加法，t0 = (t1 + t2)[31:0]

# RV64I 上執行同一條 add t0, t1, t2
# 結果：64-bit 加法，t0 = t1 + t2（全 64 bit 相加）
```

所以 `add` 在 RV64I 上就是 64-bit 加法。`add` 不等於 `addw`（W 後綴版本）。

---

## 21.4 RV32I vs RV64I 行為對照表

| 指令         | RV32I 行為                     | RV64I 行為                                      |
|------------|-------------------------------|------------------------------------------------|
| `add rd,rs1,rs2` | 32-bit 加法，結果填 rd[31:0]   | 64-bit 加法，結果填 rd[63:0]                    |
| `addi rd,rs1,imm`| 32-bit 加法，imm sign-ext 12b | 64-bit 加法，imm sign-ext 12b 到 64b            |
| `sll rd,rs1,rs2` | rs1 左移 rs2[4:0] 位，32-bit  | rs1 左移 rs2[5:0] 位，64-bit                   |
| `srl rd,rs1,rs2` | 邏輯右移，32-bit               | 邏輯右移，64-bit                                |
| `sra rd,rs1,rs2` | 算術右移，32-bit               | 算術右移，64-bit                                |
| `lw rd,offset(rs1)` | load 32-bit，sign-ext 到 32b | load 32-bit，sign-ext 到 64b                   |
| `sw rs2,offset(rs1)` | store rs2[31:0]            | store rs2[31:0]（只寫低 32 bit）                |
| `lui rd,imm`    | rd = imm << 12（32-bit）      | rd = sign-extend(imm << 12, 32b) 到 64b        |
| `auipc rd,imm`  | rd = PC + imm << 12（32-bit） | rd = PC + sign-extend(imm << 12, 32b) 到 64b   |

**關鍵差異：移位指令的 shamt 欄位**

RV32I 的移位指令，shamt（shift amount）是 5-bit（0–31）。
RV64I 的移位指令，shamt 是 6-bit（0–63）。
這直接反映在 I 型指令的 encoding 上，funct7/funct3 也有對應差異。

---

## 21.5 `lui` / `auipc` 在 RV64I 的行為

這兩條指令的 immediate 是 20-bit，左移 12 bit 後得到 32-bit 的值。在 RV64I 上：

```
lui rd, 0x80000    # imm = 0x80000
                   # imm << 12 = 0x80000000（bit 31 = 1）
                   # sign-extend 到 64-bit = 0xFFFFFFFF80000000
```

這個 sign-extend 行為很重要：如果你用 `lui` 建構一個 32-bit 地址，而這個地址的 bit 31 是 1，那麼放到 64-bit 暫存器裡就會是負數。在 RV32I 不用擔心這個，在 RV64I 寫 linker script 或 PIC code 時要注意。

`auipc` 一樣：先把 20-bit immediate 左移 12 bit，sign-extend 到 64-bit，再加 PC。

---

## 21.6 新增指令類別預覽

RV64I 在 RV32I 基礎上新增的指令，都是為了在 64-bit 暫存器上做 32-bit 語意的操作：

```
類別                指令
---------          ----------------------------------------
W 後綴算術          ADDW, SUBW, ADDIW, SLLW, SRLW, SRAW,
                   SLLIW, SRLIW, SRAIW
64-bit Load/Store  LD, SD
無符號 32-bit Load  LWU
```

這些指令在 Ch 22、Ch 23 詳解。

---

## 21.7 為什麼要這樣設計

有人會問：為什麼不直接讓 `add` 在 RV64 上做 32-bit 算術，跟 RV32 一樣？

答案是：兼容性。RISC-V 要讓同一份機器碼（特別是 kernel 的底層部分）在 RV32 和 RV64 上都能正確執行。如果 `add` 在 RV64 上行為不同，那 RV32 的 binary 就不能直接在 RV64 機器上跑。

代價是：你在 RV64 上寫 C，用 `int` 做迴圈計數器，compiler 會用 `addiw` 而不是 `addi`，因為 `int` 是 32-bit，需要 W 後綴保證 32-bit 語意。用 `long` 或 `size_t` 才會用 `addi`（64-bit 語意）。

---

## 21.8 實作確認：用 objdump 驗證

編譯這段 C 並 disassemble：

```c
// test.c
#include <stdint.h>
int32_t  add32(int32_t a, int32_t b)  { return a + b; }
int64_t  add64(int64_t a, int64_t b)  { return a + b; }
```

```bash
riscv64-unknown-elf-gcc -O1 -S test.c -o test.s
```

預期輸出片段：

```asm
add32:
    addw a0, a0, a1     # int32_t -> 用 addw，結果 sign-extend 到 64-bit
    ret

add64:
    add  a0, a0, a1     # int64_t -> 用 add，64-bit 加法
    ret
```

這就是 compiler 替你做的選擇。你需要理解它為什麼這樣選。

---

## 自我檢核

- [ ] 能說清楚「RV64I 是 RV32I 的嚴格超集」在 `add` 指令上的具體含義
- [ ] 知道 `lui` 在 RV64I 上會對 bit 31 做 sign-extend，不是零延伸
- [ ] 能說出 RV64I 移位指令的 shamt 是幾位
- [ ] 知道 `add` vs `addw` 的語意差別（後者在 Ch 22 詳解）
- [ ] 能用 `riscv64-unknown-elf-gcc -S` 確認 compiler 的指令選擇

→ [Ch 22 — W 後綴指令全解](22-w-suffix-instructions.md)
