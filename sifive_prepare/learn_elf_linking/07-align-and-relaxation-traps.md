# Ch 7 — R_RISCV_ALIGN 與 relaxation 的副作用

> 目標：理解 `R_RISCV_ALIGN` 為什麼存在、linker 怎麼處理對齊要求、以及 relax 如何在某些極端情況破壞假設。這章是「實戰 debug 一個詭異 link-time bug」的知識基礎。

## 先理解：linker 為什麼需要「對齊 relocation」

在沒有 relaxation 的世界（x86 / ARM）：assembler 算好 section 內的 offset、該 align 的地方塞 nop、linker 只是把 section 串接 —— alignment 一切由 assembler 決定。

**RISC-V 有 relaxation**，linker 可以在 `.text` 中間砍 byte。假設 assembler 原本讓某個 label 對齊 4 byte：

```
offset 0x14c: nop              ← 補齊到 4-byte 邊界
offset 0x150: (4-byte aligned label here)
```

linker 決定把前面某條指令縮 4 byte → 這個 label 地址變 0x14c → **不再 4-byte 對齊**。

沒辦法事先阻止，因為 relaxation 是 link-time 決定。所以設計了 `R_RISCV_ALIGN` 來表達：**「這個位置必須對齊 N，linker 你負責塞/抽 nop 達成」**。

## R_RISCV_ALIGN 的結構

```
OFFSET   TYPE              VALUE
0x14c    R_RISCV_ALIGN     *ABS*+0x4    ← addend = 對齊要求 (4 byte 對齊)
```

**精確語意**：

- linker 在 offset `0x14c` 處要放「塞 nop 到對齊 N」的動作
- addend 是對齊值（4、8、16 等）
- assembler 已經預留了「最多可能需要」的 nop slot
- linker 根據 relax 後的實際位置決定真正要保留幾個 nop

**assembler 的保守做法**：在 ALIGN 位置預留 **N - 1 個 nop**（確保不管 offset 落在哪都能補齊）。linker 決定關鍵是砍多少這些 nop。

## 一個具體例子

假設 assembler 產生：

```
# .text, 某處：
0x100: (instructions)
...
0x14c: nop              # R_RISCV_ALIGN align=4, 預留 0 byte（恰好對齊）
0x150: (critical code that needs 4-byte aligned entry)
```

linker 做 relaxation，發現前面的 `auipc+jalr` 可以縮成 `jal`（-4 byte）：

```
0x100: (instructions, 改動)
...
0x148: nop              # 現在這裡是 0x148，不對齊 4
0x14c: (critical code)  # 必須往後挪到 0x14c（4-byte 邊界）
```

linker 看到 `R_RISCV_ALIGN 4` 就知道要補 nop 保持 label 對齊。

### 另一種情況：已有預留 nop

如果 assembler 預留 2 個 nop（8 byte padding）給 `ALIGN 8`：

```
0x140: nop             ← 預留 1
0x144: nop             ← 預留 2
0x148: (code, 8-aligned)
```

linker 如果砍了上游的 4 byte，變：

```
0x13c: nop             ← 預留 1
0x140: (code should be 8-aligned, 但這裡是 0x140 ✓)
```

`0x140` 正好 8 byte 對齊 → linker 砍掉剩下的 nop（或保留 1 個，仍對齊）→ 結果比原本更短。

## 對齊要求的常見來源

### 1. 函式入口對齊

CPU 的 fetch 常以 16 byte（或更大）為單位。函式入口對齊能提升 icache 效率。compiler 會生：

```asm
    .align 4         # 對齊 16 byte
main:
    ...
```

assembler 轉成 `R_RISCV_ALIGN` 讓 linker 保證。

### 2. Branch target 對齊

某些硬體 branch target 不對齊會慢一拍。compiler heuristic 決定要不要加 `.align`。

### 3. 特定指令的對齊要求

RVV 某些指令要求向量 data 的 memory address 對齊。但這是 data alignment，不用 relocation（compiler layout 時就決定）。

### 4. RELRO boundary

`.got.plt` / `.got` 的邊界需要 page 對齊以便 `mprotect` 改權限。

## Relax 跟 ALIGN 的互動：真實陷阱

**陷阱 1：ALIGN 的 nop 被誤砍**

某些早期 linker 實作沒正確處理 ALIGN，relax 後把原本 padding 的 nop 也算進「可砍」範圍。結果：label 不對齊 → runtime 跑到奇怪地址 → crash。

修法：linker 必須認 ALIGN 區塊，**ALIGN 前的 nop 是 "flexible"（可砍），ALIGN 要保留的是「達到對齊所需的最少量」**。

**陷阱 2：compiler 產生假設「ALIGN 後我的地址是固定」**

如果 compiler 生 code 依賴「這段 code 開始在 0x100」但 relax 後變 0xFC，就會錯。

修法：compiler 不該假設 absolute address。只能依賴 relocation。

**陷阱 3：用 `.org` 或 `.space` 時 relax 搞破壞**

```asm
.org 0x100     # 強制這裡是 0x100
```

但 relax 後上游縮了 → 這個 `.org` 的位置被重算，可能跟後面的 code 衝突。

實務上 `.org` 跟 relaxation **基本不相容**。baremetal / kernel code 常用 `--no-relax` 避免。

## 案例分析：2023 年 LLVM 的 ALIGN bug

2023 年 LLVM LLD 有個 RISC-V relaxation bug：某些情況下 `R_RISCV_ALIGN` 的 padding 計算錯誤，導致 function entry 不對齊。受影響程式：SPEC CPU2017 有幾個 benchmark、Linux kernel 有某些 path。

修復 commit：LLD 調整 relaxation pass 的 alignment propagation 邏輯。issue tracker 上有詳細討論。

**這就是 SiFive compiler 工程師的日常**：理論上「按 spec 實作」，實務上 corner case 多、要測、要修。

## 手寫 relax 的偽代碼

如果讓你寫 linker relaxation pass 的對齊處理，大致：

```python
def relax_section(section):
    shrink_total = 0

    for reloc in section.relocs:
        if reloc.type == R_RISCV_CALL and can_relax_to_jal(reloc):
            shrink_total += 4
            # 改寫指令
            patch_bytes(section, reloc.offset, jal_encoding(reloc.target))
            # ...

        elif reloc.type == R_RISCV_ALIGN:
            current_position = reloc.offset - shrink_total
            align_n = reloc.addend
            padding_needed = align_up(current_position, align_n) - current_position
            max_padding = align_n - 1
            # 保留 padding_needed 個 nop，砍剩下的 (max_padding - padding_needed)
            shrink_total += (max_padding - padding_needed)
            # ...

    # 全部 relax 完成後重算所有 relocation 的 offset
    for reloc in section.relocs:
        reloc.offset -= shrink_before(reloc.offset, shrinks)
```

這只是示意。真實的實作有幾千行，處理各種 edge case。

## `--no-relax` 一錘定音

實務上遇到詭異 relax-related bug，第一步：

```bash
gcc ... -Wl,--no-relax -o hello.norelax
```

看看 `hello.norelax` 跑不跑得對。跑得對 → 問題在 relaxation。不跑 → 問題在別處。

這是 debug 黃金流程。

## 查看 R_RISCV_ALIGN 的存在

```bash
riscv64-unknown-elf-objdump -r hello.o | grep ALIGN
```

每個 `.text` 中 assembler 預留的對齊點都有一筆。典型的 `.o` 有幾十到幾百筆。

## 進一步的對齊機制：AlignAssertion

LLVM LLD 支援擴充的對齊指示（非標準、2024 後加）：

```
R_RISCV_ALIGN with specific constraints...
```

讓 compiler 表達更細的「保證對齊 + 不要放 nop 超過 X byte」需求。spec 還在討論。SiFive 的實作路線跟這個有關。

## 常見誤會

1. **「ALIGN 是 compile-time 決定」**：不。assembler 產生 ALIGN relocation，最終的 nop 數是 **link-time**。
2. **「nop 填在 relax 之後才加」**：不。assembler 預留「最大可能需要」的 nop，linker 砍剩下的。
3. **「`.align` 跟 `R_RISCV_ALIGN` 一對一」**：基本上是，但有些情境 assembler 會優化掉（如果能預測 offset 固定）。
4. **「ALIGN 只影響 instruction 對齊」**：不，`.data` 的 alignment 也有類似機制，但多半在 section attribute 裡（`sh_addralign`）而不是 relocation。
5. **「--no-relax 能解決所有 relax-related bug」**：大部分能，但某些 bug 是 compiler 的假設錯，關 relax 只是掩蓋。要深入查 root cause。

## 真實的 debug 流程

假設你遇到「程式在 RISC-V host 偶爾 crash、x86 完全沒事」：

```bash
# Step 1: 確認是 relax 造成
gcc ... -Wl,--no-relax -o hello.test
# 跑，看會不會 crash

# Step 2: 如果 --no-relax 解掉 → 問題在 relax
riscv64-linux-gnu-objdump -d hello.test > before.asm
riscv64-linux-gnu-objdump -d hello > after.asm
diff before.asm after.asm | head -50
# 看哪幾段 code 不一樣

# Step 3: 檢查不一樣的段是不是對齊敏感
# 常見：函式入口、branch target、vector load
```

這個流程能解掉 90% 的 relax-related bug。

## 動手練習

1. 寫 `void foo(void) __attribute__((aligned(16)));`，編成 `.o`。用 `readelf -r` 找出 `R_RISCV_ALIGN` 的 entry。改 align 值觀察 addend 欄位變化。
2. 故意寫一段依賴絕對地址的 asm（如 `.org 0x1000`），開 relax 編譯。觀察是否錯或產生 warning。
3. 用 `--no-relax` 跟 default 分別 link 同一個程式，diff 兩個 objdump。找出所有 relax 改過的位置。
4. 讀 LLD 的 `relaxSection()`（`lld/ELF/Arch/RISCV.cpp`），找出處理 `R_RISCV_ALIGN` 的 branch。
5. 寫一個 test: 函式入口 `.align 16`，上游有能 relax 的 call。故意讓 relax 縮減打破 align，觀察 linker 是否補回 nop。

## 自我檢核

- [ ] 我能解釋為什麼 `R_RISCV_ALIGN` 存在、跟 `.align` 的關係
- [ ] 我能預測 relax 對一個預設對齊 label 的影響
- [ ] 我知道 debug relax 問題的第一步是 `--no-relax` 測試
- [ ] 我能列舉三種「relax + align 造成 bug」的情境
- [ ] 我看到 `.o` 裡的 `R_RISCV_ALIGN` entry 能說出對應什麼 `.align`

Part 2 結束。下一章進 Part 3 — linker script 的深度。linker script 是 baremetal / firmware / kernel 工程師的主戰場。

→ [Ch 8 Linker script 語法與心法](./08-linker-script-basics.md)
