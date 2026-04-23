# Ch 3 — Pseudo-instruction 與 assembler 展開

> 目標：把 `li`、`la`、`call`、`ret`、`mv`、`nop` 這些你天天看到但其實**不在 ISA 裡**的「假指令」徹底拆開。讀完你能看著一條 pseudo 立刻寫出它展開後的真實機器碼，並能解釋 `auipc + jalr`、`auipc + addi` 這對 idiom 為什麼是 RISC-V 的靈魂。

## 什麼是 pseudo-instruction

RISC-V 的 ISA 很小（47 條整數指令）。但寫 assembly 時你會大量看到不在這 47 條裡的東西：

```asm
li    t0, 0x12345678
la    a0, my_data
call  printf
ret
mv    a1, a0
nop
```

這些都是 **pseudo-instruction**。assembler（`riscv64-unknown-elf-as`）看到時會展開成 1 條或多條真實指令。Pseudo 不是標準的一部分、不會出現在 CPU decoder 裡 — 但它們是所有 `.S` 檔的主力。

為什麼要有 pseudo？

1. **可讀性**：`ret` 比 `jalr x0, ra, 0` 好看太多。
2. **可攜性**：`li` 在不同立即數大小會展開成不同真指令序列，assembler 幫你挑最好的。
3. **讓 ISA 本體保持小**：不用為「mov reg to reg」開一個獨立 opcode。
4. **為 linker relaxation 鋪路**：pseudo 的展開保有調整空間（這是重點，後面展開）。

## 最簡單的幾個

```
nop              →  addi x0, x0, 0
mv   rd, rs      →  addi rd, rs, 0
not  rd, rs      →  xori rd, rs, -1
neg  rd, rs      →  sub  rd, x0, rs
seqz rd, rs      →  sltiu rd, rs, 1
snez rd, rs      →  sltu rd, x0, rs
j    offset      →  jal  x0, offset
jr   rs          →  jalr x0, rs, 0
ret              →  jalr x0, ra, 0
```

幾個觀察：

- **所有 mov / negate / compare-to-zero 都靠 `x0` 跟加減湊**。
- **`ret` 是 `jalr x0, ra, 0`**：跳到 `ra` 指向的地址，`jalr` 寫回用的目標暫存器是 `x0`（也就是丟棄）。
- **`jal x0, ...`（捨棄返回）= 無條件跳**：RISC-V 沒有獨立的「jump」opcode。

把這些看熟，你看 objdump 時會省很多疑惑。

## `li`：load immediate

C 裡寫 `int x = 0x12345678;`，RISC-V 怎麼把這個數字塞進暫存器？32-bit 立即數放不進任何一條 I-type 指令（只有 12 bit）。所以 `li` 要展開：

```
li t0, 0x12345678
  ↓
lui  t0, 0x12345        # t0 = 0x12345 << 12 = 0x12345000
addi t0, t0, 0x678      # t0 += 0x678 → 0x12345678
```

`lui`（load upper immediate）把 20-bit 立即數塞到 bit [31:12]，低 12 bit 歸零。再用 `addi` 補低 12 bit。

**陷阱**：`addi` 的立即數是 **sign-extended**。如果低 12 bit 的最高位是 1（負），加起來會少 `0x1000`，所以 `lui` 要先 +1 補回。assembler 會幫你處理，但看 objdump 時常會看到 `lui` 的值「怪怪的」就是這個原因。

**RV64 的 `li`**：要塞 64-bit 立即數可能需要 **最多 8 條指令**（lui + addi + slli + addi + slli + addi + ... ）。Compiler 常常覺得「不如放進 `.rodata` 用 `lw` 讀」— 這跟 x86 的立即數策略差很多。

## `la`：load address（地址版的 li）

```
la a0, some_symbol
```

這是把**符號的地址**載入暫存器。看似跟 `li` 一樣，但不一樣：連結前 `some_symbol` 是一個佔位符，**真正的地址在 link 時才知道**。展開有兩種 code model：

### `-mcmodel=medlow`（small model）

符號地址在 `[-2GiB, +2GiB)`（絕對地址）：

```
la a0, some_symbol
  ↓
lui  a0, %hi(some_symbol)     # 上 20 bit
addi a0, a0, %lo(some_symbol) # 下 12 bit
```

跟 `li` 幾乎一樣，但用 `%hi` / `%lo` 這兩個 relocation operator（linker 填值）。

### `-mcmodel=medany`（position-independent-ish）

符號地址在**離當前 PC 不超過 ±2GiB**：

```
la a0, some_symbol
  ↓
auipc a0, %pcrel_hi(some_symbol)
addi  a0, a0, %pcrel_lo(1b)    # 1b 指向上一條 auipc
```

**這對 idiom 是 RISC-V 的靈魂**。展開邏輯：

- `auipc rd, imm` = `rd = PC + (imm << 12)`
- 配 `addi` 填低 12 bit
- 結果是「相對當前 PC 的位移」

PC-relative addressing 的好處：**code 搬家時不用改 code**（除了微調 relative offset，但那是 linker/loader 的事）。shared library、PIE、kernel module 都依賴它。

## `call`：跨模組呼叫

```
call printf
  ↓
auipc ra, %pcrel_hi(printf)
jalr  ra, ra, %pcrel_lo(1b)
```

展開邏輯：

1. `auipc ra` 把 `PC + high 20 bit of offset` 存進 `ra`。
2. `jalr ra, ra, low_12_bit`：跳到 `ra + low_12_bit`，並把 `PC+4` 寫回 `ra`（overwrite 剛剛那個中間值，所以第二步 `ra` 變成「call 完要回到的位址」）。

注意 `jalr` 的寫法：`jalr rd, rs, imm` 是 `rd = PC+4; PC = rs + imm`。**目標暫存器跟來源暫存器可以是同一顆（`ra`）**，因為硬體會先讀 `rs`、算好 PC、再寫 `rd`。

**`call` 跟 `jal` 差在哪**：

- `jal` 的跳躍範圍 ±1 MiB（J-type 21-bit signed）
- `call` = `auipc + jalr`，範圍 ±2 GiB

如果 compiler / linker 能確定目標在 ±1 MiB 內，`call` 可以被 **relaxation 壓縮成一條 `jal`** — 省一條指令。這是 RISC-V linker relaxation 的經典例子，之後 `learn_elf_linking` Ch 6 會細講。

## `tail`：尾呼叫

類似 `call` 但不存返回地址：

```
tail target
  ↓
auipc t1, %pcrel_hi(target)
jalr  x0, t1, %pcrel_lo(1b)    # 注意 rd=x0：丟棄返回地址
```

`ra` 保持不動，繼續是 caller 傳進來的返回地址。target `ret` 時直接回到 caller 的 caller。這叫尾呼叫優化。

注意 `tail` 用 `t1` 當中繼（不是 `ra`）— 因為我們不想蓋掉 `ra`。**所以 tail call 不能保留尾呼叫目標函式的 `t1` 值**，這通常沒差（`t*` 是 caller-saved），但手寫 asm 時要留意。

## `jump` / `jr` / `jalr` / `ret` 家族

```
j    offset       →  jal  x0, offset          # 跳 ±1 MiB
jr   rs           →  jalr x0, rs, 0           # 跳到 rs
ret               →  jalr x0, ra, 0           # 跳到 ra = return
jalr rs           →  jalr ra, rs, 0           # 呼叫 rs 指的函式（存 ra）
```

`jalr rs`（三參數版 → 兩參數版 → 一參數版）是 pseudo 的層層簡化。注意「一參數」預設是 `jalr ra, rs, 0`（存返回到 ra），跟 `jr rs`（丟棄返回）**只差 rd 欄位**。容易看錯。

## `%hi` / `%lo` / `%pcrel_hi` / `%pcrel_lo`：assembler 的秘密語言

這些不是指令語法的一部分，是 **relocation operator**。它們告訴 assembler「這個立即數不是現在填，留給 linker」。

```
lui  a0, %hi(foo)       # 「這裡先留 20 bit，linker 把 foo 的高 20 bit 填進來」
addi a0, a0, %lo(foo)   # 「linker 再把 low 12 bit 填進來」
```

對應的 **ELF relocation type**：

| Operator      | Relocation type       | 動作                       |
|---------------|----------------------|----------------------------|
| `%hi(x)`      | `R_RISCV_HI20`       | 填 x 的 bits [31:12]       |
| `%lo(x)`      | `R_RISCV_LO12_I`     | 填 I-type 的 imm[11:0]     |
| `%lo(x)`      | `R_RISCV_LO12_S`     | 填 S-type 的 imm[11:0]     |
| `%pcrel_hi(x)`| `R_RISCV_PCREL_HI20` | 填 (x - PC) 的 [31:12]     |
| `%pcrel_lo(y)`| `R_RISCV_PCREL_LO12_*` | 填偏移的低 12 bit，**y 指向對應的 HI20 指令** |

**最詭異的設計**：`%pcrel_lo` 的參數不是符號本身，而是**那條對應的 `auipc` 指令的 label**。

```asm
1:  auipc a0, %pcrel_hi(foo)      # label "1" 就是這條
    addi  a0, a0, %pcrel_lo(1b)   # 1b = 「往後最近的 label 1」
```

為什麼這樣？因為 linker 在填 `%pcrel_lo` 時，需要知道當初 `%pcrel_hi` 算的是「從哪條指令的 PC」算的，才能湊出對的低 12 bit。所以語法層級就逼你把兩條指令綁在一起。

**這個設計在 relaxation 時會出大問題** — 如果 linker 把 `auipc` 拿掉（因為發現 offset 小、可以直接 `addi`），`%pcrel_lo` 指的 label 就沒了。Ch 6 of `learn_elf_linking` 會看 linker 怎麼處理這個。

## 看一個真實例子

```c
int x = 42;
int get(void) { return x; }
```

`riscv64-unknown-elf-gcc -march=rv64imac -mabi=lp64 -mcmodel=medany -O0 -c get.c`，objdump：

```
0000000000000000 <get>:
   0:   00000517        auipc   a0,0x0
                        0: R_RISCV_PCREL_HI20   x
                        0: R_RISCV_RELAX        *ABS*
   4:   00052503        lw      a0,0(a0)       # 0 <get>
                        4: R_RISCV_PCREL_LO12_I .L0
                        4: R_RISCV_RELAX        *ABS*
   8:   00008067        ret
```

看幾件事：

1. `auipc a0, 0x0` — 這個 `0x0` 是**假值**，真正的偏移等 linker 填（看 `R_RISCV_PCREL_HI20`）。
2. `lw a0, 0(a0)` — low 12 bit 也是 0（假值），留給 linker（`R_RISCV_PCREL_LO12_I`）。
3. **`R_RISCV_RELAX` 是 hint**：告訴 linker「這兩條可以被 relaxation 優化掉（如果 x 離 gp 夠近）」。
4. 最後 `ret` 是 `jalr x0, ra, 0` 的 pseudo。

這是「read a global variable」的標準 idiom。每一支 RISC-V 程式裡都有成千上萬個這種模式。

## `jalr` 的小字：x 的低 bit 要乾淨

`jalr rd, rs, imm` 的真實行為：

```
target = (rs + sign_ext(imm)) & ~1    # 強制清除最低 bit
rd     = PC + 4
PC     = target
```

**最低 bit 自動被丟棄**。為什麼？為了跟 C 擴充（16-bit 指令）相容：跳到 16-bit 對齊的地址是合法的，最低 bit 必須是 0（對齊 2 byte）。真實硬體不 care 你有沒有 C 擴充，反正 clear LSB。

這個細節在寫 interpreter / emulator 時重要。若你寫的 emulator 忘了清 LSB，少數 code 會跑錯。

## 一次講透：code model

`-mcmodel=` 影響所有 la / call 的展開。三個模式：

| Code model | 範圍 | 展開方式 | 用途 |
|------------|------|----------|------|
| `medlow`   | 符號在 `[-2GiB, +2GiB)` 絕對地址 | `lui + addi/lw/sw` | baremetal、kernel、小範圍程式 |
| `medany`   | 符號在 `[PC-2GiB, PC+2GiB]` | `auipc + addi/lw/sw` | 所有 PIC / PIE / shared lib（**Linux distro 預設**）|

（`large` model 在 RV 標準裡還沒定義；GCC 12+ 與 LLVM 有 experimental 的。）

**記住**：Linux 上的 userspace 基本上全部 `medany`，因為 ASLR。看任何 Linux binary 的 objdump 都會是 `auipc + ...` 模式。

## `auipc` 為什麼不直接叫 `lui + add pc`？

因為 RISC-V 不允許讀 `PC` 當一般暫存器。所以需要一條專屬指令「把 PC + (imm << 12) 放進 rd」— 那就是 `auipc`（add upper immediate to pc）。它是 RISC-V 讀 PC 的**唯一**方式。

這個設計換來的好處：**PC 不在 register file 裡，單獨作為 pipeline 狀態**。Decoder 跟 register file 完全不用知道 PC 存在，硬體更簡單。

## 常見誤會

1. **「`li` 永遠展開成 `lui + addi`」**：不。小於 12 bit 只要一條 `addi x, x0, N`。超大 RV64 值可能展開 8 條。
2. **「`call` 永遠兩條指令」**：relaxation 後可能變一條 `jal`。objdump 看到的長度不一定跟原始 .S 對得上。
3. **「`ret` 是特殊指令」**：不是。它就是 `jalr x0, ra, 0`，沒有獨立 opcode。
4. **「pseudo 是 ISA 的一部分」**：不是。它是 assembler 的語法糖，spec 不管。不同 assembler（gas vs llvm-mc）展開細節可以略有不同，但慣例是統一的。
5. **「`auipc + addi` 跟 `lui + addi` 等價」**：完全不是。前者 PC-relative、後者絕對地址。搞混會讓 relocatable code 死得很難看。

## 動手練習

1. 寫個 `li a0, 0xABCDEF01`，編出來後看 objdump 展開幾條。
2. 寫個 global `int a[100];` 跟 `return a[50];`，用 `-mcmodel=medlow` 跟 `-mcmodel=medany` 各編一次，對比。
3. 寫一支 function 呼叫 `strlen`，看 objdump 裡 `call strlen` 展開是 `auipc + jalr` 還是單一 `jal`。試 `-ffunction-sections -Wl,--no-relax` 看差異（關 relax）。
4. 手寫一段 asm：`auipc t0, 0; addi t0, t0, 16; jalr t0`。算出它實際跳到哪（答：自己往下 16 byte）。在 gdb 裡驗證。
5. 把 `ret` 改成 `jr ra`、`jalr x0, ra, 0` — 編出來的機器碼完全一樣嗎？用 `as -o` + `objdump -d` 驗證。

## 自我檢核

- [ ] 我能默寫 `ret`、`mv`、`nop`、`j`、`jr` 這五個 pseudo 的真實展開
- [ ] 我能解釋 `auipc + addi` 與 `lui + addi` 的差異與各自用途
- [ ] 我能說出 `%pcrel_lo(1b)` 為什麼要指向 label 而不是符號
- [ ] 我知道 `-mcmodel=medlow` 與 `medany` 差在哪
- [ ] 我看到 objdump 的 `R_RISCV_RELAX` 知道它是什麼意思（即使細節待 linker 課再學）

Part 1 結束。下一章進入 Part 2，講 M / A / F / D / C 這五個**標準擴充** — 為什麼 base 不含乘法、為什麼 C 擴充大家都開、為什麼 atomic 需要 LR/SC 而不是 CAS。

→ [Ch 4 M / A / F / D / C：標準擴充五件套](./04-standard-extensions.md)
