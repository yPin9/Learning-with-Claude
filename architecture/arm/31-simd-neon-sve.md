# Ch 31 — SIMD：NEON、Advanced SIMD、SVE/SVE2

> 目標：搞懂 ARM 的 SIMD 三代演進 — 古典 NEON、Advanced SIMD、變長向量 SVE/SVE2。寫一段最小 NEON code、了解 SVE 為什麼是變長設計、知道編譯器怎麼用這些 ISA。

## ARM SIMD 簡史

```
1985-2003  早期 ARM 沒 SIMD（Cortex 之前）
2004      ARMv6 SIMD：32-bit register pack 4×8-bit / 2×16-bit，有限
2005      VFP（純浮點，非 SIMD）
2006      NEON（ARMv7-A），128-bit register × 32 個，**真正 SIMD**
2011      Advanced SIMD（ARMv8-A 的 NEON 升級）
          AArch64 化、寬度仍 128-bit、語意更強
2016      SVE（Scalable Vector Extension）
          變長 128-2048 bit，VLA(Vector Length Agnostic) 設計
2019      SVE2（ARMv9-A）
          SVE 加上 NEON-equiv 整數操作 + 加密 + DSP
```

**現代 SoC 全配 NEON（必選），SVE / SVE2 視 SoC**：AWS Graviton 3+、Apple M3+、ARM Neoverse V1+ 有 SVE。多數手機 / IoT 仍 NEON-only。

## NEON / Advanced SIMD 暫存器

AArch64 NEON 用 V0–V31 共 32 個 128-bit 暫存器：

```
V0..V31  128-bit register
B0..B31  低 8-bit
H0..H31  低 16-bit
S0..S31  低 32-bit (single-precision float)
D0..D31  低 64-bit (double-precision float)
Q0..Q31  完整 128-bit
```

**同一個 V 暫存器**用不同名字看不同寬度（類似 x86 XMM/YMM/ZMM）。

向量元素標記：

```
V0.16B    16 個 8-bit
V0.8H     8 個 16-bit
V0.4S     4 個 32-bit float
V0.2D     2 個 64-bit double
V0.4H     低 64-bit 看作 4 個 16-bit
```

## 一段 NEON：vector add

```asm
; AArch64 NEON
ld1   {v0.4s}, [x0]    ; load 4 個 float 到 v0
ld1   {v1.4s}, [x1]    ; load 4 個 float 到 v1
fadd  v2.4s, v0.4s, v1.4s   ; v2 = v0 + v1（4 個 lane 同時加）
st1   {v2.4s}, [x2]    ; store
```

**一條 fadd 等同 4 個 fadd**，throughput 4×。

C intrinsic 寫法：

```c
#include <arm_neon.h>

void vec_add(const float *a, const float *b, float *c, int n) {
    int i = 0;
    for (; i + 4 <= n; i += 4) {
        float32x4_t va = vld1q_f32(&a[i]);
        float32x4_t vb = vld1q_f32(&b[i]);
        float32x4_t vc = vaddq_f32(va, vb);
        vst1q_f32(&c[i], vc);
    }
    for (; i < n; i++) c[i] = a[i] + b[i];
}
```

intrinsic 是封裝過的 NEON 指令，比直接寫 asm 易讀且編譯器能優化。

## NEON 常用 intrinsic 類別

| 類別 | 範例 | 對應指令 |
|---|---|---|
| Load/store | `vld1q_f32` `vst1q_f32` | LD1 ST1 |
| Arithmetic | `vaddq_f32` `vmulq_f32` | FADD FMUL |
| Multiply-accumulate | `vfmaq_f32` | FMLA（融合乘加） |
| Compare | `vceqq_f32` | FCMEQ |
| Bitwise | `vandq_u32` `vorrq_u32` | AND ORR |
| Shuffle | `vextq_u32` | EXT |
| Cross-lane | `vaddvq_f32` (horizontal sum) | FADDV |
| Type convert | `vcvtq_s32_f32` | FCVTZS |

`q` 後綴 = 128-bit 操作（quad-word）。沒 `q` = 64-bit。

## SVE：變長向量

NEON 永遠 128-bit。某天 ARM 想做 server SIMD，發現「**寫 SIMD code 對特定寬度寫死**會綁定編譯時 vector 寬度」 — 改硬體就要重編 binary。

SVE 設計：**vector length agnostic**。

```
SVE register Z0..Z31    寬度 128 / 256 / 384 / 512 / ... / 2048 bit
                        由實作決定，runtime 才知道

predicate register P0..P15   masking 用，每 lane 一 bit
```

寫 SVE code 不寫死「這是 256-bit」：

```asm
; 不知道向量長度的 vector add（pseudo）
mov   x_idx, #0
loop:
    whilelo p0.s, x_idx, x_n      ; predicate = lanes that fit
    ld1w   z0.s, p0/z, [x_a, x_idx, lsl #2]
    ld1w   z1.s, p0/z, [x_b, x_idx, lsl #2]
    fadd   z2.s, z0.s, z1.s
    st1w   z2.s, p0, [x_c, x_idx, lsl #2]
    incw   x_idx                   ; idx += vector length / 4
    cmp    x_idx, x_n
    blt    loop
```

關鍵指令：

- **WHILELO**：產生 predicate，標出「還在範圍內的 lane」
- **INCW**：自動加 vector length（不用 hard-code 4 / 8 / 16）
- **predicate /z (zeroing)**：未啟用 lane 寫零；**/m (merging)** 保留舊值

**同一份 SVE binary 跑在 256-bit 與 2048-bit 硬體都正確**。x86 的 SSE → AVX → AVX-512 是「換 ISA」不兼容；SVE 是「同 ISA 不同寬」。

## SVE 何時值得用

1. **HPC / scientific compute**：浮點密集、向量長
2. **AI inference**：matrix mul、activation function
3. **某些密碼學算法**：bulk encrypt / decrypt

但對普通 application code（編譯器 auto-vectorize 為主），**SVE 收益取決於編譯器是否認識**。GCC / LLVM 對 SVE auto-vectorize 已經相當成熟，但仍比 hand-written intrinsic 慢。

## SVE2 = SVE + 整數 / DSP / crypto

SVE 第一代主要浮點。SVE2（ARMv9-A）補：

- **整數 vectorization**：很多 NEON 整數操作的 SVE 版
- **bit manipulation**：AES、SHA、SM3/SM4
- **DSP**：saturation arithmetic、FFT helpers
- **string operations**：strchr、strlen 加速

**SVE2 是 NEON 的取代候選**：所有 NEON 能做的，SVE2 都做、且 length-agnostic。長期 ARM 想推 SVE2 取代 NEON，但兼容性壓力下 NEON 還會存在多年。

## Helium (M-profile Vector Extension)

Cortex-M 用的 SIMD 不是 NEON / SVE，是 **Helium (MVE)**：

- 針對 MCU 設計（小面積、低功耗）
- 共用 FPU register（沒 dedicated vector reg）
- predicate-based（類 SVE）

Cortex-M55 / M85 是首批支援。對 DSP / TinyML 是大躍進，但目前 chip 還不普遍。

## 編譯器 auto-vectorize

```c
void scale(float *a, float k, int n) {
    for (int i = 0; i < n; i++) a[i] *= k;
}
```

`-O3 -mcpu=cortex-a72`：

```asm
fmov    s1, w_k
dup     v1.4s, v1.s[0]      ; broadcast k 到所有 lane
loop:
    ld1   {v0.4s}, [x_a]
    fmul  v0.4s, v0.4s, v1.4s
    st1   {v0.4s}, [x_a], #16
    ...
```

編譯器自動展開為 NEON。**靠 -O3、合適的 -mcpu、aliasing 友善**（`__restrict`）才會 auto-vec。

## 性能：NEON 真的快多少？

簡單 vector add 1M elements：

| 寫法 | 時間 |
|---|---|
| Scalar `for` | 100% |
| `-O3` auto-vectorized | 25–35% |
| Hand NEON intrinsic | 20–25% |
| SVE on 512-bit | 10–15% (但極依賴硬體) |

對 hot loop 4–10× 提升常見。**「該不該寫 SIMD」要先 profile，找到真的 bottleneck 再動手**。盲目寫一堆 intrinsic 浪費時間且難維護。

## 一個常見誤解

「NEON 是不是只用在浮點？」

**整數 SIMD 也很重要**：

- 影像處理（YUV ↔ RGB 轉換、blur、edge detect）
- 字串處理（strstr、strchr）
- 密碼學（AES、SHA）
- bitmap / bit manipulation

NEON 提供 8/16/32/64-bit 整數 SIMD，比 scalar 快數倍。許多函式庫（libpng、ffmpeg、glibc strstr）大量用 NEON 整數 path。

## 自我檢核

- [ ] 我能列出 V0 / Q0 / D0 / S0 之間的關係
- [ ] 我能寫一段 NEON intrinsic vector add
- [ ] 我能解釋 SVE 為什麼「vector length agnostic」
- [ ] 我能說出 SVE 與 NEON 的設計目標差異
- [ ] 我能比較 Helium 與 NEON 在 MCU 場景的選擇
- [ ] 我能用 `-O3` 看編譯器 auto-vec 的輸出

下一章看 ARM 硬體安全 — PAC、BTI、MTE，現代 attack mitigation 三件套。

→ [Ch 32 ARM 硬體安全：PAC、BTI、MTE](./32-pac-bti-mte.md)
