# Ch 6 — RISC-V 專屬 relocation 與 linker relaxation

> 目標：理解 RISC-V linker relaxation 的本質 — linker 主動修改指令、縮短 code、全域重算地址。這是 RISC-V 對其他 ISA 最大的差異點、也是 SiFive compiler 工程師繞不開的能力。

## 一句話版本

**Linker relaxation = linker 在 link 時發現某些 idiom 可以用更短/更快的指令替代，主動改寫 `.text`，並把之後所有地址往前移，重算所有相關 relocation。**

x86 / ARM 的 linker **不會動指令**（除了填 operand）。RISC-V 會。這是 RISC-V ISA + 生態的刻意設計。

## 為什麼 RISC-V 特別依賴 relaxation

幾個理由疊加：

### 1. 固定 32-bit 指令 + optional compressed

RISC-V 的指令寬度有兩種：32-bit base 或 16-bit C 擴充。**compiler 生 code 時不知道 linker 最終的 layout**，可能保險生 32-bit，linker 才知道能不能壓成 16-bit。

### 2. auipc + addi/jalr 成本

呼叫 function 的典型展開：

```
auipc t1, %pcrel_hi(foo)
jalr  ra, t1, %pcrel_lo(foo)
```

8 byte、兩條指令。如果 `foo` 其實離我 ±1 MiB 內，**一條 `jal foo` (4 byte) 就夠**。compiler 不知道距離，但 linker 知道。

### 3. PC-relative idiom 很常見

讀一個 global variable：

```
auipc a0, %pcrel_hi(var)
lw    a0, %pcrel_lo(var)(a0)
```

8 byte。如果 var 離 `gp`（global pointer）±2 KiB 內，可以直接：

```
lw    a0, offset(gp)
```

4 byte。省一條、省一個 register pressure。

### 4. Compressed 替換很密集

很多普通指令可以變 compressed：

- `addi sp, sp, -16` (4 byte) → `c.addi16sp sp, -16` (2 byte)
- `sw ra, 12(sp)` (4 byte) → `c.swsp ra, 12` (2 byte)

compiler 預設生非 compressed、linker 決定替換。

這四個加起來，**一個 typical binary 可以縮 10-20%**。值得。

## Relaxation 是怎麼 signal 的

Compiler / assembler 產 `.o` 時，在**每個可能被 relax 的位置**放一個 `R_RISCV_RELAX` relocation：

```
OFFSET   TYPE                 VALUE
0x6ec    R_RISCV_PCREL_HI20   .rodata+0x0
0x6ec    R_RISCV_RELAX        *ABS*           ← 緊跟前一個 relocation
0x6f0    R_RISCV_PCREL_LO12_I .L0
0x6f0    R_RISCV_RELAX        *ABS*
```

**`R_RISCV_RELAX` 本身沒公式**。它是個 hint，意思：「前面那筆 relocation 允許被 relax」。linker 看到可以選擇：

- **relax**：改寫指令、縮短 code
- **不 relax**：照原樣填值，一切正常

`--no-relax` flag 讓 linker 完全不做 relaxation（debug 時有用）。

## 四種經典 relax pattern

### Pattern 1：PC-rel HI+LO 變 gp-relative

**原始**：

```
auipc a0, %pcrel_hi(var)    # 4 byte
lw    a0, %pcrel_lo(var)(a0) # 4 byte
```

**relax 後（如果 var 在 gp ±2 KiB）**：

```
lw    a0, offset(gp)        # 4 byte (沒變短，但少用一顆 register)
nop                          # 補齊原本 8 byte
```

**為什麼補 nop**：這是 relax 的陷阱。linker 不能隨便砍 byte、否則後面所有地址位移、所有 relocation 全部要重算。最保守的做法：**把砍掉的指令用 nop 填回**。

較積極的 linker（LLD）會真砍 byte 並重算。下面會講。

### Pattern 2：`call foo` 縮成 `jal`

**原始**：

```
auipc ra, %pcrel_hi(foo)
jalr  ra, ra, %pcrel_lo(foo)   # 共 8 byte
```

**relax 後（if foo 在 ±1 MiB）**：

```
jal   ra, foo                  # 4 byte
```

**省了 4 byte**。這是最明顯的 code-size 收益。

### Pattern 3：32-bit 指令變 compressed

```
addi sp, sp, -16       # 4 byte
  → c.addi16sp sp, -16 # 2 byte
```

條件：指令符合 C 擴充的 encoding 限制（imm 範圍、register 範圍等）。**省 2 byte**。

### Pattern 4：lui + addi 變 c.li 或 addi

對小立即數：

```
lui  t0, 0x0                # HI20 填 0（小數）
addi t0, t0, 42             # LO12 填 42
```

linker 發現 lui 的 HI20 是 0 → 整個 lui 可以砍，只留 `addi t0, x0, 42`（或 `c.li t0, 42`）。

## 砍 byte 的後果：cascade effect

假設 linker 決定把 `.text` 裡 offset 0x100 的 8 byte 指令縮成 4 byte：

```
Before:                       After:
0x00 ... (some code)          0x00 ... (some code)
...                            ...
0x100: auipc+jalr (8 byte)    0x100: jal (4 byte)
0x108: next instr              0x104: next instr       ← 提前 4 byte
0x10c: ...                     0x108: ...
...                            ...
0x200: label1                  0x1fc: label1           ← 所有後續地址 -4
```

**後果**：

1. 所有在 `0x100` 之後的 label 的地址 -4
2. 如果之前有 branch `beq xxx, xxx, 0x200`，offset 要從 `0x200 - beq_addr` 改成 `0x1fc - beq_addr`
3. `.rodata` 如果跟 `.text` 緊貼，它的位置也要前移
4. Dynamic linker 的 GOT entry、PLT 等也可能受影響

linker 必須**遍歷所有 relocation 重算**。這是為什麼 relaxation 實作很複雜。

## 第二層複雜：alignment 要求

有些 section 要求對齊（例：RVV 的指令要 4 byte 對齊、某些函式入口要 16 byte 對齊）。如果 relax 把 byte 砍了、原本對齊的 label 變不對齊 → 出事。

**`R_RISCV_ALIGN` relocation 就是為這個存在**。Ch 7 專講。

簡版：`R_RISCV_ALIGN` 告訴 linker「這裡必須對齊到 N byte，你可以在這 insert / delete nop 來達成」。linker 決定 relax 後，重新算 align 需要填多少 nop、或可以砍多少 nop。

## GP-relative 優化的前置條件

Pattern 1（gp-relative）需要 `gp` 指向「所有 small data section 的中間」。這是 compiler + linker 的約定：

- linker 在 output 裡定義 symbol `__global_pointer$`（指向 `.sdata + 0x800`）
- runtime startup code（`crt0`）把這個地址 load 到 `gp`
- 之後 linker 在 relax 時，檢查「target 是否在 `gp - 2048 .. gp + 2047`」

沒設 `gp` 的 baremetal code 不要開 gp-relaxation（或根本不用 gp）。

## 對 compiler 的影響

compiler 生 code 時要保持 relax-friendly：

1. **在 PC-rel HI/LO 都放 R_RISCV_RELAX hint**
2. **不要在兩條指令中間插別的東西**（否則 linker 不敢 relax）
3. **避免把 HI20 跟 LO12 分成不同 branch target**（就是常見的 `%pcrel_lo(1b)` 綁 label 的原因）

違反這些 → linker 不敢 relax → 錯失優化。

## `--no-relax` 什麼時候用

某些情境要關 relaxation：

1. **Debug**：看 compiler 原生輸出、沒被 linker 改。
2. **Kernel code**：有些 kernel 用 fixed-offset 讀某個 MMIO，被 relax 會錯。
3. **JIT**：JIT 產生的 code 已經是 final，不允許被 relax。
4. **Custom instruction**：linker 可能不認得 vendor extension 的對應，關掉保險。

命令：

```bash
ld -o hello hello.o --no-relax
# 或 gcc
gcc -Wl,--no-relax hello.c -o hello
```

## 三個 linker 對 relaxation 的支援

### GNU `ld`

**完整實作**。RISC-V 生態主流。有些 edge case 仍在改進中。相對保守：遇到不確定就保留 nop。

### LLD (`ld.lld`)

**後追趕但快速接近**。LLVM 18+ 對 RISC-V relaxation 支援齊全。更積極砍 byte（避免 nop padding）。

### mold

**2024 後開始加 RISC-V relaxation**。mold 的 parallelization 優勢在 RISC-V 上發揮得好。未來可能成為主流。

### GNU gold

**基本不支援 RISC-V relaxation**。gold 是 x86-focus 設計，RISC-V 支援停滯。不建議用於 RISC-V。

## 面試：實作 relaxation 要注意什麼

SiFive / T-Head 可能問：「如果你要在 linker 加一個新 relax rule，流程是什麼？」

答案框架：

1. **定義新 rule 的 applicability check**：什麼條件可以 relax？
2. **設計 new instruction sequence**：替換後長度、語意等價
3. **處理 byte 縮減的 cascade**：重算 section offset、symbol address
4. **處理 alignment**：如果 relax 改變後續 alignment 要補 nop
5. **更新 debug info**：DWARF 的 address 要對應修改（Ch 15）
6. **測試**：regression test、不同 code model、跟 no-relax 版本對比

**關鍵是「能等價替換 + 能正確更新全域 state」**。

## 讀 LLD 原始碼的入口

如果要深入（SiFive 面試大加分）：

- **LLD 的 RISC-V relaxation**：`lld/ELF/Arch/RISCV.cpp`（`relaxCall`、`relaxHi20Lo12` 等 function）
- **GNU binutils**：`bfd/elfnn-riscv.c`（`_bfd_riscv_relax_*` 系列 function）

讀 300 行你能看懂「如何決定一條指令可不可以 relax、怎麼 cascade 更新」。比任何文件更直接。

## 實驗：同一份 code 看 relax 前後

```bash
# 不 relax
riscv64-linux-gnu-gcc hello.c -o hello_no -Wl,--no-relax
riscv64-linux-gnu-objdump -d hello_no | grep -A2 '<main>'

# relax
riscv64-linux-gnu-gcc hello.c -o hello_yes
riscv64-linux-gnu-objdump -d hello_yes | grep -A2 '<main>'
```

你會看到：

- `hello_no`：`main` 裡呼叫 `puts` 是 `auipc + jalr`（8 byte）
- `hello_yes`：可能變 `jal` 一條（4 byte）
- 甚至可能看到 `c.jal`（2 byte）— 極致 relax

size 對比：

```bash
size hello_no hello_yes
```

`hello_yes` 的 `.text` 通常少 5-15%。

## Relaxation 的「公平性」爭議

為什麼 RISC-V 要 linker 做這種工作，compiler 不可以直接生最短指令嗎？

**不可以**：

- compiler 不知道 layout，不知道 `foo` 離 call site 多遠
- compiler 寫一個 `.o`，這 `.o` 可能跟不同檔案 link，距離每次不同
- **只有 linker 看得到全局**

所以 RISC-V 選擇了「compiler 保守生 code + linker 做 global optimization」的分工。這跟「compiler 一次到位」的哲學不同。

**代價**：linker 變複雜、link 時間變長、某些工具（反組譯器、profiler）要知道 relax 的副作用。

## 常見誤會

1. **「Relaxation 只是 cosmetic optimization」**：不。某些 binary 可以從 300 KB 縮到 250 KB。嵌入式世界這很關鍵。
2. **「LLD 的 relaxation 跟 GNU 一樣」**：細節不同。某些 corner case 兩個 linker 行為會不同，尤其 alignment 處理。
3. **「compiler 加 `-O3` 就不需要 relax」**：不。-O3 是 compiler 內部優化，跟 linker 可見的 global layout 是兩個層面。
4. **「Relax 在 dynamic linker 也會做」**：不。Dynamic linker 不改 code。Relax 只發生在 static linking。
5. **「我 custom extension 不用擔心 relax」**：錯。如果你的 custom 指令產生 `auipc + 你的指令` 這種配對，要明確標示 `R_RISCV_RELAX` 為「禁止 relax」或設計對應的 relax rule。

## 動手練習

1. 用 `--no-relax` vs default 各編一個複雜 program，用 `size` 指令對比 `.text` 大小，算百分比。
2. 寫一段 `auipc + addi` 讀 global variable，用 `objdump -d` 看 relax 前後差異。調整 variable 位置讓它接近 `gp`，驗證是否變 gp-relative。
3. 寫一個遠距離 `call`（用 linker script 故意把 function 放得遠），看 relax 後會不會被保留 auipc+jalr。
4. 用 `-Wl,--print-gc-sections` 觀察 linker 砍哪些 section。跟 relax 一起看能看出 linker 的全景優化。
5. 讀 LLD 的 `relaxCall()` function（約 50 行），看它怎麼判斷 range、怎麼替換 bytes。

## 自我檢核

- [ ] 我能講 RISC-V relaxation 的本質跟為什麼其他 ISA 沒有
- [ ] 我能列四種經典 relax pattern
- [ ] 我能解釋 `R_RISCV_RELAX` 是 hint 而不是動作
- [ ] 我知道 linker 砍 byte 後的 cascade 效應
- [ ] 我能指出 `gp` 跟 `__global_pointer$` 在 relaxation 中的角色

下一章專講 `R_RISCV_ALIGN` 跟 relaxation 造成的 bug — 這是實作 toolchain 時最惡名昭彰的坑之一。

→ [Ch 7 R_RISCV_ALIGN 與 relaxation 的副作用](./07-align-and-relaxation-traps.md)
