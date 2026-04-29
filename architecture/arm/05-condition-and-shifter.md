# Ch 5 — 條件碼、IT block、barrel shifter

> 目標：理解 ARM 的三個招牌特性：condition code（每條指令幾乎都能條件執行）、IT block（Thumb-2 的條件區塊）、barrel shifter（一條指令做兩件事）。然後解釋為什麼 AArch64 把它們砍了大半。

## A32 的招牌：每條指令都能條件執行

A32 指令編碼前 4 bit 是 condition code：

```
A32 instruction encoding:
┌─────┬─────────────────────────────────────────┐
│cond │       opcode + operands (28 bits)       │
└─────┴─────────────────────────────────────────┘
 31:28
```

意思是：**任何 A32 指令都能加 condition 後綴**，例如 `ADDEQ` `MOVNE` `LDRMI`，CPU 看 NZCV 決定要不要執行這條。

```asm
cmp r0, r1
moveq r2, #1     ; r0 == r1 才執行
movgt r2, #2     ; r0 > r1 (signed) 才執行
movhi r2, #3     ; r0 > r1 (unsigned) 才執行
```

## 16 個 condition code

| 後綴 | 名稱 | NZCV 條件 | 意思 |
|---|---|---|---|
| EQ | Equal | Z=1 | 相等（cmp 後 Z=1） |
| NE | Not Equal | Z=0 | 不相等 |
| CS / HS | Carry Set / Higher or Same | C=1 | 無號 ≥ |
| CC / LO | Carry Clear / Lower | C=0 | 無號 < |
| MI | Minus | N=1 | 負 |
| PL | Plus | N=0 | 非負 |
| VS | Overflow Set | V=1 | 有號溢位 |
| VC | Overflow Clear | V=0 | 無溢位 |
| HI | Higher | C=1 ∧ Z=0 | 無號 > |
| LS | Lower or Same | C=0 ∨ Z=1 | 無號 ≤ |
| GE | Greater or Equal | N=V | 有號 ≥ |
| LT | Less Than | N≠V | 有號 < |
| GT | Greater Than | Z=0 ∧ N=V | 有號 > |
| LE | Less or Equal | Z=1 ∨ N≠V | 有號 ≤ |
| AL | Always | (true) | 永遠（預設） |
| NV | Never (reserved) | — | ARMv8 起保留作他用 |

注意 **HI/LS 是無號比較，GT/LE 是有號比較**。寫錯會出微妙 bug。x86 的 `JG`/`JA` 是同個概念（`JA` 無號、`JG` 有號）。

## 為什麼 ARM 設計每指令都能條件執行？

理由是 **避免短分支造成 pipeline stall**：

```c
if (a > b) max = a; else max = b;
```

無條件分支版（傳統 RISC）：

```asm
cmp r0, r1
ble  L1
mov  r2, r0      ; max = a
b    L2
L1:
mov  r2, r1      ; max = b
L2:
```

兩個 branch、可能誤預測。

A32 條件執行版：

```asm
cmp  r0, r1
movgt r2, r0     ; 大於就 max=a
movle r2, r1     ; 否則 max=b
```

**沒有 branch，pipeline 順暢**。1990 年代 ARM 流水線淺、分支預測弱，這是大勝。

## 為什麼 AArch64 砍掉條件執行？

到了 ARMv8（2010s），條件執行反而變累贅：

1. **微架構複雜**：條件指令在流水線後段才知道要不要跑，要保留各種 forwarding path
2. **分支預測器強了**：現代分支預測準確率 > 95%，誤預測代價遠不如以前
3. **編譯器優化更聰明**：許多狀況編譯器選 `csel` 這種顯式 conditional select 即可
4. **指令編碼壓力**：64-bit ISA 改用 5-bit 編碼 32 個暫存器（log2(32)=5），每指令編碼空間吃緊，sacrifice condition field 換取暫存器 + 立即數寬度

AArch64 只保留少數條件指令：

```asm
csel  x0, x1, x2, eq        ; x0 = (eq) ? x1 : x2
csinc x0, x1, x2, eq        ; x0 = (eq) ? x1 : x2 + 1
csinv x0, x1, x2, eq        ; x0 = (eq) ? x1 : ~x2
csneg x0, x1, x2, eq        ; x0 = (eq) ? x1 : -x2
cset  x0, eq                ; x0 = (eq) ? 1 : 0
```

`csel` 等同 x86 的 `cmov`。其他指令仍**只能無條件**或用顯式 branch。

## Thumb-2 的 IT block：A32 條件執行的 16-bit 投影

Thumb-2 不像 A32 每指令都能塞 4-bit cond，但 ARM 設計了 **IT (If-Then) block** 模擬：

```asm
cmp   r0, r1
ittt  gt              ; If-Then-Then-Then for "gt"
movgt r2, r0          ; gt 條件
addgt r3, r3, r2      ; gt 條件
subgt r4, r4, #1      ; gt 條件
```

`IT` 指令本身編碼了 **下面 1–4 條指令的條件模式**：T (Then 滿足條件執行) / E (Else 反條件執行)。

```
IT     pred              ; 1 條 then
ITT    pred              ; 2 條 then
ITE    pred              ; 1 then, 1 else
ITTT   pred              ; 3 then
ITTE   pred              ; 2 then, 1 else
ITET   pred              ; ...
ITTTT  pred              ; 4 then
... 共 32 種模式
```

寫 IT block 的硬規矩：

- **每條 then 指令必須帶**對應 cond 後綴（`movgt`，不是 `mov`）
- **每條 else 指令必須帶反條件後綴**（`mov` 與 `gt` 對應的反條件是 `le`，所以要寫 `movle`）
- **IT block 內不能有 branch**（除非是 block 最後一條）
- **不能有 PC 相依的指令**

ARMv8.1-M 開始 deprecated（不刪但建議不用）。Cortex-A 系列 AArch32 仍可用，AArch64 完全不存在。

## Barrel Shifter：一條指令做兩件事

A32 的另一個招牌：**ALU 第二運算元能順便做 shift**：

```asm
; A32
add  r0, r1, r2, lsl #3   ; r0 = r1 + (r2 << 3)
mov  r0, r1, lsr #16      ; r0 = r1 >> 16
orr  r0, r0, r1, ror #8   ; r0 |= rotate_right(r1, 8)
```

「barrel shifter」是硬體層的描述：ALU 前面有一級**桶式移位器**，能一個 cycle 內做任意 shift / rotate。配合 ALU 一起組合，許多操作 1 cycle 就完成。

支援的 shift 類型：

| 後綴 | 行為 |
|---|---|
| `LSL` | Logical Shift Left（左移補 0） |
| `LSR` | Logical Shift Right（右移補 0） |
| `ASR` | Arithmetic Shift Right（右移補 sign bit） |
| `ROR` | Rotate Right |
| `RRX` | Rotate Right with Carry（含 C flag） |

實用範例：

```asm
; r0 = base + index * 4
add r0, r_base, r_idx, lsl #2

; 取結構欄位（offsetof = 16）
ldr r0, [r1, r2, lsl #4]

; 把 32-bit 高 16 bit 移到低位
mov r0, r1, lsr #16
```

**寫 ARM 組語不用這個 = 多浪費**。看 startup code、libc memcpy 都會看到滿滿 barrel shifter。

## AArch64 的 shifter：縮減版

AArch64 簡化了 shifter：

```asm
add  x0, x1, x2, lsl #3    ; 仍支援 shift register operand
add  x0, x1, x2, sxtb       ; 也支援 extend (sxtb/uxtb/sxth/uxth/sxtw/uxtw)
```

但是 **只能在 ALU 指令的第二 operand**，且 **不再支援 RRX / immediate ROR 的所有變體**。理由還是編碼壓力：32-bit 指令塞不下完整 shifter spec 加 4 個暫存器欄位。

AArch64 還是有 `LSL` / `LSR` / `ASR` / `ROR` 作為獨立指令（其實是 `UBFM` / `SBFM` 的別名）。

## NZCV 是什麼時候被改的？

很多人 debug 時誤以為「`add` 一定改 flag」。錯。**ARM 預設不改 flag，要加 `S` 後綴才改**：

```asm
; A32 / Thumb-2
add  r0, r1, r2      ; 不改 flag
adds r0, r1, r2      ; 改 NZCV

; AArch64
add  x0, x1, x2      ; 不改 flag
adds x0, x1, x2      ; 改 NZCV
```

部分指令本來就改 flag（`CMP` 是 `SUBS` 但丟結果、`TST` 是 `ANDS` 但丟結果），不需要 S 後綴。

這個設計讓編譯器**精準控制何時更新 flag**，避免不必要的依賴鏈。x86 大多算術指令自動改 flag，反而是麻煩（`partial flag stall`）。

## 動手練習：判斷下面指令做什麼

```asm
1: add  r0, r1, r2, lsl #2
2: ldr  r3, [r4, r5, lsl #2]
3: cmp  r0, r1
   ittt gt
   subgt r2, r2, #1
   addgt r3, r3, #2
   movgt r4, r5
4: csel  x0, x1, x2, lt
5: subs  x0, x1, x2
   csinc x3, x4, x5, eq
```

<details>
<summary>解答</summary>

1. `r0 = r1 + (r2 << 2)` — 等同 `r0 = r1 + r2*4`，常見於陣列索引
2. `r3 = *(r4 + r5*4)` — 從 word 陣列載入 `arr[r5]`
3. 若 `r0 > r1`：`r2 -= 1; r3 += 2; r4 = r5`，否則三條都跳過
4. 若 `< 0`（依 NZCV）：`x0 = x1`，否則 `x0 = x2`
5. `x0 = x1 - x2` 並更新 flag；若 `eq`（相減為 0）`x3 = x4`，否則 `x3 = x5 + 1`

</details>

## 一個常見誤解

「條件執行被砍是因為 ARM 跟風 RISC-V？」

不對。**砍條件執行是 ARMv8 自己的決定（2011 年）**，比 RISC-V 流行早。原因是流水線效率與編碼預算。RISC-V 一開始就沒有條件執行（V/Zicond 後來補了 `czero` 等），只是兩家都得出類似結論：**現代分支預測器強，條件執行 ROI 變差**。

## 自我檢核

- [ ] 我能列出 16 個 condition code 中「無號比較」與「有號比較」的差別
- [ ] 我能寫一個用 IT block 的 Thumb-2 範例
- [ ] 我能說出 barrel shifter 是什麼以及它讓哪類運算 1-cycle 完成
- [ ] 我知道 ARM 預設指令不改 flag，要 `S` 後綴
- [ ] 我能解釋 AArch64 為什麼砍掉大部分條件執行，留 csel
- [ ] 我能讀懂混合 condition + shifter 的 A32 指令

下一章看 ARM ABI（AAPCS）— 暫存器約定、棧框、frame pointer、tail call 全套。

→ [Ch 6 函式呼叫與 AAPCS](./06-aapcs-calling-convention.md)
