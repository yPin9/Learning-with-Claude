# Ch 17 — 與 ARM / x86 對照：三種設計哲學

> 目標：不是技術百科對照，而是**設計哲學的對照**。理解三個 ISA 在相同議題上做了不同取捨、以及那些取捨背後的歷史脈絡。面試時能用「為什麼 RISC-V 選 X 而 ARM 選 Y」這種論述切入。

## 三個 ISA 的身世

### x86 家族

- 1978 年 Intel 8086 誕生。16-bit 起家
- 1985 年 80386 加 32-bit
- 2003 年 AMD64 加 64-bit（Intel 跟進成 x86-64）
- **遺產極深**：每代加指令，舊的幾乎不刪。現代 x86-64 包含 1500+ 條指令

### ARM 家族

- 1985 年 ARM1 誕生（英國 Acorn）
- ARMv7 32-bit 主宰行動市場
- 2011 年 ARMv8 加 64-bit（AArch64）— **與 AArch32 共存但 ISA 重新設計**
- 2021 年 ARMv9 加強 SVE / MTE 等

ARM 的關鍵決定：**AArch64 是全新設計，不背 32-bit 歷史包袱**。

### RISC-V

- 2010 年 Berkeley 計畫啟動
- 2015 年 RISC-V Foundation 成立
- 2022 年 V 擴充 ratified，Linux 支援齊全
- **從白紙開始**，是三個裡最年輕的

## 高層比較表

| 議題 | x86-64 | ARM AArch64 | RISC-V |
|------|--------|-------------|--------|
| 指令長度 | 1–15 byte 變長 | 固定 4 byte | 固定 4 byte + optional 2 byte (C) |
| register 數 | 16 GPR | 31 GPR + SP | 32 GPR (含 x0) |
| Condition codes | ✓ (flags) | ✓ (NZCV) | ✗ (branch 自帶比較) |
| Conditional execution | ✗ | 舊：有 (IT)，新：退場 | ✗ (Zicond 部分補) |
| addressing modes | 很多 (base+index×scale+disp) | [base], [base, offset], pre/post-incr | 只有 base+imm12 |
| load/store 粒度 | 到 128-bit (XMM/YMM/ZMM) | 到 128-bit (Q reg) + SVE | XLEN 為主 |
| 字節序 | Little | 可選（預設 little） | 可選（預設 little） |
| Memory model | TSO | RC (weak) | RVWMO (weak) |
| Privilege levels | Ring 0–3 (實用 0, 3) | EL0–EL3 | M / S / U |
| Vector ISA | SSE/AVX/AVX-512 (fixed length) | NEON (fixed) + SVE (VLA) | RVV (VLA) |
| 生態政治 | Intel + AMD 雙頭壟斷 | ARM Ltd 授權 | 開放標準 |

## 議題 1：變長 vs 固定長度

**x86**: 1 byte `INC EAX` 到 15 byte 的 AVX-512 指令都存在。變長好處：**code density 很高**（常用指令短）。壞處：**decoder 極複雜**、parallel fetch 困難。

**ARM AArch64**: 固定 4-byte。NEAT。壞處：code size 比 x86 大 15-20%。

**RISC-V**: 固定 4-byte base + 2-byte C 擴充。折衷：多數指令 4-byte、熱門 2-byte。實測 code size 跟 ARM 接近。

**設計哲學**：x86 賭 decoder 能 scale、RISC-V 賭 "最小 unit 是 2-byte 就夠密了"。20 年後看，x86 的 decoder 確實 scale 了（雖然耗大量 transistor），RISC-V 的選擇也成立。兩派都沒輸。

## 議題 2：condition codes

**x86**: 每個 ALU 操作會更新 flags (ZF, CF, SF, OF)。`cmp` + `jcc` 是標配。

```asm
# x86
cmp  rax, rbx
jl   somewhere
```

**ARM AArch64**: 類似有 NZCV，但每條指令是否更新 flags **可選**（`add` 不更新、`adds` 更新）。

**RISC-V**: **沒有 flags**。branch 自帶比較：

```asm
# RISC-V
blt  a0, a1, somewhere
```

**設計後果**：

- RISC-V 沒有 "flag register 污染" 問題，可以用更多 speculation
- 但沒有「一次比較多次 branch」的省指令方式（x86 可以 `cmp` 後 `jl` / `jge` / `je` 連續用）

對 compiler 研究：去掉 flags 簡化很多 optimization pass；但強制 branch 帶比較會產生更多指令（除非 fuse，現代 CPU 有在做 macro-op fusion）。

## 議題 3：conditional execution

**ARM 早期 (ARMv7)**:

```asm
addle r0, r1, r2     # 只在 ≤ 時執行
```

每條 ARMv7 指令 4-bit 條件碼。寫密集但 **decoder 成本高、對 OoO 不友善**。

**ARM AArch64**: 大幅退場。只剩 `csel` (conditional select) 等少數。

**RISC-V**: 從一開始就沒有。2023 年 Zicond 補了 `czero.eqz/nez`。

**設計哲學**：conditional execution 在 in-order CPU 有意義（省 branch mispredict）；OoO CPU 的 predictor 已經把 branch 處理得很好，conditional execution 反而讓 register rename 複雜。2010+ 的 ISA 普遍放棄。

## 議題 4：addressing modes

**x86**:

```
mov eax, [rbx + rcx*4 + 0x10]     # base + index*scale + displacement
```

一條指令做了一次地址計算 + 一次 memory read。複雜度外包給 decoder。

**ARM**:

```
ldr x0, [x1, x2, lsl #3]          # base + shifted index
ldr x0, [x1], #8                  # post-increment
ldr x0, [x1, #8]!                 # pre-increment with writeback
```

中等複雜度。

**RISC-V**:

```
lw x0, 16(x1)        # 只有 base + imm12
```

**極簡**。要複雜 addressing 自己 compose：

```asm
# 對 x86 的 mov eax, [rbx + rcx*4 + 0x10] 做等效：
slli t0, rcx, 2         # rcx * 4
add  t0, t0, rbx
lw   eax, 16(t0)         # base + disp
```

**四條指令換一條**。表面看 RISC-V 輸。但：

- 硬體可以把 shift-add 拆成 μop 做 —— 現代 x86 CPU 對那條複雜 `mov` 其實內部拆成 2-3 μop。總 latency 差不多。
- **memory 指令變純**：pipeline 只處理一次 AGU、一次 load。RISC 哲學的一致。

Zba 的 `sh*add` 補回一點（Ch 7）。

## 議題 5：register 數

```
x86-64: 16 GPR (RAX..R15)
ARMv8:  31 GPR + SP
RISC-V: 32 GPR (x0 永遠零)
```

**ARM / RISC-V 的 31/32 顆 register** 讓 register pressure 低、spill 少。SPEC benchmark RISC-V/ARM 比 x86 普遍少 10-20% 的 stack access。

x86 為什麼只有 16？歷史：8086 只有 8，AMD64 加到 16 已經是 opcode encoding 極限（REX prefix）。再加要用 AVX-512 的 EVEX prefix（共 32 GPR 但只 AVX-512 code 能用，非主流 code 還是 16）。

## 議題 6：vector ISA 設計

### x86 AVX-512

固定 512-bit register `zmm0..zmm31`。指令名稱綁寬度：`vaddps zmm0, zmm1, zmm2`（對 16 個 float 加）。

**問題**：新硬體要新 binary。跑在沒 AVX-512 的 CPU 上會 illegal instruction。

### ARM NEON (AArch64 version)

固定 128-bit `v0..v31`。指令 `add v0.4s, v1.4s, v2.4s`（4 個 int32）。

### ARM SVE

**Scalable Vector Extension**：vector 寬度不固定（128-bit 起跳、最多 2048）。寫 code 時用「vector length agnostic」範式，類似 RVV。

### RISC-V Vector (RVV)

一開始就 VLA，VLEN 128 起跳（實務）、最大 65536（spec）。跟 SVE 理念相近，但**更進一步**：加 LMUL（把多個 register 串起來）、動態 SEW（runtime 改 element 寬度）。

**哲學對比**：x86 壓在「大向量寬度」那端；RISC-V / ARM 賭 VLA 是長期正確。目前看 VLA 贏了未來方向的討論。

## 議題 7：privilege model

**x86**: Ring 0–3，實際只用 0 (kernel) 跟 3 (user)。加上 SMM（System Management Mode）這個「藏在 Ring 0 之下」的驗證層。雜亂。

**ARM AArch64**: EL0–EL3 分別對應 user / kernel / hypervisor / secure firmware。四層，清楚。

**RISC-V**: M / S / U 三層。加 H 擴充支援 virtualization。**沒有獨立的 "secure" level**（用 PMP 做隔離）。

**x86 的問題**：遺產太重、SMM 成為 security 盲點、Spectre 等 side-channel 攻擊顯露 Ring 之間的隔離不夠強。

**RISC-V 的選擇**：最小必要 privilege 分層。Secure 交給擴充（如 CHERI-like 提案）或額外 HW。

## 議題 8：memory model

| ISA | Model | 描述 |
|-----|-------|------|
| x86 | TSO (Total Store Order) | 較強；store 之間有序、store→load 可能 reorder |
| ARM AArch64 | Release Consistency (Weak) | 較弱；依賴 acquire/release 修飾子 |
| RISC-V | RVWMO | 類似 ARM，中偏弱 |

**x86 的 TSO 是「禮物」** — 大量 lock-free code 可以不用 fence。但硬體要實作 store buffer、總序 broadcast，**面積成本高**。

**RISC-V 選 weak model** 主要是給 OoO CPU 設計自由。但程式設計師成本變高 — compiler 要精準放 fence，不能偷懶。

## 議題 9：開放性

| ISA | 授權模式 | 有幾家設計廠 |
|-----|---------|--------------|
| x86 | 專利護城河（Intel + AMD 透過反訴雙頭壟斷）| 2 |
| ARM | 授權（你付錢，ARM 給你 license，你可能有 architecture license 自己做微架構）| 20+ |
| RISC-V | 開放標準（zero royalty，你做自己的 micro-architecture）| 50+（且增長中）|

RISC-V 的 **zero royalty** 是商業革命：

- Google 自家 SoC 用 RISC-V 不用跟 ARM 付權利金
- 新創公司（Ventana、Tenstorrent、Rivos）從零起家
- 學術研究可以直接 tape out 而不用 clean-room 重寫

SiFive 這類公司的商業模式：**賣優秀的 core design**（而不是賣 ISA 授權）。

## 設計哲學總結

**x86**:
- 保持 backward compatibility 是最高誡命
- 複雜度藏在硬體（decoder、μop 翻譯器）
- 商業上 intel-centric

**ARM**:
- 每一代小改、有機演化
- 強調 power / perf 平衡
- 最成功的 license-out 模式（行動市場主宰）

**RISC-V**:
- 純淨設計，modular 擴充
- 複雜度放到 compiler（硬體簡單）
- 開放 governance、長期願景賭在「開放贏閉源」

## 面試用的三句話總結

你在 SiFive 面試被問「為什麼 RISC-V？」可以這樣答：

1. **技術層面**：RISC-V 把複雜度放在 compiler 而不是硬體。這讓同一個 ISA 能 scale 從 32-bit MCU 到 64-bit server。x86 / ARM 都做不到這個 scale。

2. **商業層面**：zero royalty + 開放生態讓新進者有戲。SiFive 能競爭 ARM 是因為 RISC-V 打破了授權壁壘。

3. **未來層面**：VLA vector (RVV) + modular ISA + 快速 ratification 流程讓 RISC-V 能吸收新需求。x86 的變更太緩慢、ARM 的 governance 封閉。

## 實務 tip：cross-ISA porting

如果你曾經 port code 從 x86 / ARM 到 RISC-V 會遇到的坑：

1. **Memory ordering 比 x86 弱**：很多 x86 lock-free code 移植要補 fence。
2. **Addressing mode**：手寫 asm 的 memcpy / strcmp 在 RISC-V 要多條指令。
3. **Condition codes**：x86 / ARM 的 `cmp + cmov` pattern 要用 `max`/`min` 或 Zicond 或 branch。
4. **Vector**：AVX intrinsic 沒有直接對應；RVV 要 re-design loop 結構。
5. **SIMD shuffle**：ARM NEON 的 `tbl` 系列在 RVV 對應 `vrgather`，但語意不完全一樣。

## 常見誤會

1. **「RISC-V 比 ARM 簡單所以快」**：不直接。簡單 ISA 讓硬體實作的上限高、但下限也低。最終速度看硬體實作，不看 ISA。
2. **「x86 的變長指令 decoder 是瓶頸」**：20 年前是，現在 Intel/AMD 都解決了（雖然付出很大 transistor 成本）。
3. **「ARM SVE 跟 RVV 相同」**：相近，但 RVV 的 LMUL 與 SEW 動態性更強。
4. **「RISC-V 缺 condition codes 所以寫 asm 麻煩」**：寫多了會覺得乾淨。少了「當前 flag 是什麼」的心智負擔。
5. **「三個 ISA 會有勝者」**：市場會多元並存幾十年。x86 在 PC / server 半壁、ARM 在行動 / Edge、RISC-V 在嵌入式與特殊晶片。

## 動手練習

1. 同一支 C function `int factorial(int n)`，在 x86-64 / ARM AArch64 / RISC-V 各 objdump，比指令數與結構。
2. 寫 x86 的 `cmpxchg16b` 的 RISC-V 等效（用 LR/SC 或 cmpxchg loop），觀察差異。
3. 把一段 ARM NEON 的 memcpy inner loop 翻成 RVV，比較差異。
4. 找一篇 x86 intrinsic（例：`_mm_shuffle_epi8`）的描述，試圖在 RVV 寫等效（提示：`vrgather.vv`）。
5. 讀一篇 "why RISC-V" blog 或論文（例：David Patterson 的 "50 years of RISC"），用自己的話總結核心論點。

## 自我檢核

- [ ] 我能列 x86 / ARM / RISC-V 在 6 個核心議題上的選擇
- [ ] 我能講出為什麼 RISC-V 沒 condition codes
- [ ] 我能區分 x86 AVX-512、ARM SVE、RISC-V RVV 的 vector model 差異
- [ ] 我能在面試時用三句話說明「為什麼 RISC-V」
- [ ] 我能預測一段 x86 code 在 RISC-V 需要什麼調整

下一章進入「如何讀 spec」的技能章 — 200 頁文件看似天書，但有結構可循。你讀 Unprivileged spec 一遍、Privileged spec 一遍，就算有根。

→ [Ch 18 如何讀 ISA spec 而不迷路](./18-reading-spec.md)
