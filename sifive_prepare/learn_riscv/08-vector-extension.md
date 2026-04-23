# Ch 8 — V 擴充：vector、vtype、LMUL 心法

> 目標：理解 RVV 為什麼跟 SSE / AVX / NEON 是**完全不同的 vector 模型**（vector-length agnostic），以及 `vtype` / `vsetvl` / `LMUL` / `SEW` 這些奇怪名詞。讀完你能寫一個 strided memcpy、看懂 compiler 產生的 `vsetvli` 怎麼選 config。

## 先破除最大的誤解：RVV 不是 SSE

SSE / AVX / NEON 的核心假設：

- 向量暫存器是**固定寬度**（SSE 128-bit、AVX 256-bit、AVX-512 512-bit）
- 指令名字綁定寬度（`addps`、`vaddps`）
- 不同寬度要重編 binary，或用 CPUID dispatch

RVV 的設計哲學完全相反：

- **向量暫存器的寬度是 runtime property**（叫 VLEN，實作決定，典型 128 / 256 / 512 / 1024）
- 指令**沒有寬度後綴**（`vadd.vv` 不管底層 VLEN 多少都能跑）
- **同一份 binary 在不同 VLEN 的硬體上跑 = 自動獲得該硬體的向量寬度**

這叫 **vector-length agnostic (VLA) programming model**。源自 1970s 的 Cray-1，再由 RVV 現代化。

## 為什麼 VLA 值得

想像你為 AVX-512 的 server 寫好 code，現在想跑在 AVX2 的老 CPU 上 — 要重寫。
如果是 AVX2 寫好要跑在 ARM NEON（128-bit）— 要重寫。

VLA 版：**compiler 生成的 code 不綁寬度**，跑在 128-bit 硬體得 128-bit 平行、跑在 1024-bit 硬體得 1024-bit 平行。binary 一份就夠。

代價：寫法不像 SSE 那麼直觀。你不能講「`vadd` 對 8 個 int 做」— 到底幾個 int 取決於 `VLEN` 跟你設的 `LMUL` / `SEW`。下面會解釋。

## 核心名詞

先把地圖列出來：

| 名詞 | 意義 | 由誰決定 |
|------|------|---------|
| **VLEN** | 物理向量暫存器寬度（bit）| 硬體實作 |
| **ELEN** | 單一 element 最大寬度（bit）| 硬體實作，通常 = XLEN 或 64 |
| **v0..v31** | 32 顆 vector 暫存器 | ISA |
| **SEW** | Selected Element Width (8 / 16 / 32 / 64 bit)| runtime 設定 |
| **LMUL** | Length Multiplier (1/8 .. 8)| runtime 設定 |
| **vl** | 這次指令處理幾個 element | runtime 設定 |
| **vtype** | 包含 SEW / LMUL 等的 config CSR | runtime 設定 |
| **VLMAX** | 當前 config 下能處理的最大 element 數 | 算出來的：`VLEN × LMUL / SEW` |

VLMAX 公式是關鍵：

```
VLMAX = VLEN * LMUL / SEW
```

例：VLEN = 256 bit、LMUL = 2、SEW = 32 → VLMAX = 256 × 2 / 32 = 16。這次指令一次處理 16 個 int32。

## `vsetvl` / `vsetvli`：設定 vector 模式

每段 vector code **必須先設 config**，告訴硬體：「我接下來要處理多少個 element、每個多寬、串幾個 reg」。這是 RVV 的「開場白」。

```
vsetvli  rd, rs1, e32, m2, ta, ma
           │   │   │    │   │   │
          寫回│   │    │   │   └ mask policy (ma = agnostic)
           "設定"│    │   └─── tail policy (ta = agnostic)
               │    │
             AVL  SEW    LMUL
      (Application Vector Length = 你想處理的總量)
```

硬體做的事：

```
vtype ← 你要的 (SEW=e32, LMUL=m2)
VLMAX = VLEN * LMUL / SEW
vl    ← min(AVL, VLMAX)
rd    ← vl                     # 回報這次實際處理多少
```

**關鍵：`vl` 可能 < AVL**。如果你想處理 1000 個 element 但 VLMAX 是 16，那 `vl = 16`。你要迴圈處理。

## 一個完整範例：memcpy in RVV

Goldilocks 級的範例：

```asm
# void rvv_memcpy(char *dst, const char *src, size_t n)
# a0 = dst, a1 = src, a2 = n

rvv_memcpy:
.L_loop:
    vsetvli  t0, a2, e8, m8, ta, ma     # e8 = 一個 byte, m8 = 最大倍率
    vle8.v   v8,  (a1)                   # 載入 t0 個 byte 到 v8..v15
    vse8.v   v8,  (a0)                   # 存回
    add      a1, a1, t0                  # src += vl
    add      a0, a0, t0                  # dst += vl
    sub      a2, a2, t0                  # n -= vl
    bnez     a2, .L_loop
    ret
```

看幾件事：

1. **迴圈裡每次呼叫 `vsetvli`**：硬體自動把 `vl = min(a2, VLMAX)`。當剩 3 byte 時，`vl = 3`，不會越界。這叫 **vector strip-mining**。
2. **沒有 "remainder loop"**：SSE / AVX 版本 memcpy 最後要處理「剩不到一個 vector」的 tail，手動寫 byte loop。RVV 不用 — 最後一次 `vl` 自動縮小。
3. **用了 LMUL=8**：把 v8..v15 串成一組「8 倍寬的虛擬 register」。一次處理的 element 數 = 8 × VLEN / 8 = VLEN bytes。VLEN=256 就是 256 bytes/iter。
4. **沒有 SSE 的 intrinsic 風味**：沒有 `_mm_load_si128`。Assembly 寫起來像一般 scalar，但每條 `v*` 指令在 hardware 上展開成並行。

## SEW：element width

SEW 決定一個 element 有多寬：

- `e8` = 8 bit (byte)
- `e16` = 16 bit (half)
- `e32` = 32 bit (word)
- `e64` = 64 bit (doubleword)

指令根據 SEW 自動解釋 register 內容。例如 `vadd.vv v0, v1, v2` 在 `e8` 下是 byte-wise 加、`e32` 下是 word-wise 加。**同一條指令、不同 SEW、不同行為**。

指令編碼中**不帶 SEW**（不像 `vaddb` / `vaddw`）。它走 `vtype` 的 runtime 設定。

## LMUL：group 多少 register

LMUL 決定一條 vector 指令**同時作用在幾個 register**上：

```
LMUL=1    一條指令作用在 v0 (單顆)
LMUL=2    一條指令作用在 v0,v1 (一對)
LMUL=4    一條指令作用在 v0..v3
LMUL=8    一條指令作用在 v0..v7      ← 最大
LMUL=1/2  一條指令只用 v0 的前半
LMUL=1/4  ...
LMUL=1/8  ...                         ← 最小
```

大 LMUL 好處：一條指令做更多事，**loop overhead 減少**。壞處：register pressure 增加（LMUL=8 下，你只有 v0 / v8 / v16 / v24 四組能用 — 因為 groups 必須對齊）。

**一般原則**：對長 array 用 LMUL=4 或 8（省 loop），對 short/mix code 用 LMUL=1（更多 register 可用）。compiler 自動選擇，**但你可以看 objdump 逆向它的選擇**，面試常問。

fractional LMUL (`1/2`、`1/4`、`1/8`) 的用途：**widen 操作**。例如 `vwmul.vv` 把兩個 `e16` 相乘產生 `e32`，結果 LMUL 會是輸入的兩倍。為了結果不爆 register，輸入要用 fractional LMUL。這是 RVV 最燒腦的部分之一。

## vl：這次處理幾個

`vl` 是一顆 CSR（vstart、vl、vtype、vcsr 都是）。執行向量指令時硬體看 `vl` 決定：

- element `0 ~ vl-1`: 執行
- element `vl ~ VLMAX-1`: **tail**，根據 tail policy 決定處理方式

tail policy（`vtype[6]`）：

- `tu` (tail-undisturbed): tail element 保持原值。
- `ta` (tail-agnostic): 讓硬體決定 — 通常填 0 或保持。allow 硬體優化。

通常 compiler 開 `ta` 讓硬體最佳化。

## Mask：按 bit 決定執行

RVV 的每個 element 可以個別被 mask 遮蔽。一個 mask 是 **v0 暫存器的 bit-vector**（v0 的 bit 0 = element 0 的 mask，bit 1 = element 1 的 mask...）。

```asm
# 只對 a[i] > 0 的 element 做 +1
vsetvli  t0, a1, e32, m1
vle32.v  v1, (a0)
vmsgt.vx v0, v1, x0             # v0 mask = (v1 > 0)
vadd.vi  v1, v1, 1, v0.t        # 只對 mask=1 的 element 加 1
vse32.v  v1, (a0)
```

`v0.t` 是 "mask policy = true"，只對 mask=1 執行。SSE 的對應物是 `vpblendvps` 類。RVV 的 mask 機制更 uniform。

## 為什麼 VLEN 從 128 起跳？

spec 定義 VLEN 必須是 2 的冪、最小 32、最大 65536（很誇張但 spec 留空間）。**實務上 VLEN 下限是 128**（RVA22 / RVA23 profile 規定），這是為了 compiler 生成 code 的複雜度。

你會看到的硬體 VLEN：

- **128**: SiFive P270、XuanTie C908
- **256**: SiFive P670
- **512**: 一些 HPC 原型
- **1024+**: 研究用或特殊 HPC (例：European Processor Initiative)

RVV 的終極願景是「同一支 binary 跑在 VLEN=128 的 SoC 跟 VLEN=1024 的 server 都榨出最大性能」。

## 跟 ARM SVE 的比較

ARM SVE 也是 VLA ISA（早 RVV 幾年），概念很接近。主要差異：

| 議題 | RVV | ARM SVE |
|------|-----|---------|
| 暫存器數 | 32 | 32 (z*) + 16 (p*) predicate |
| Mask | v0 單顆 | 專用 predicate registers |
| LMUL 機制 | ✓ | 無 |
| `vsetvl` | ✓ 顯式 | 用 `svwhilelt` 等 |
| Widening | fractional LMUL | 用不同寬度 register 描述 |

**LMUL 是 RVV 獨有**。SVE 認為它太複雜；RVV 認為它是 compiler 優化的利器。爭議到 2025 還沒定論。

## 常見誤會

1. **「`vadd.vv` 等於 SSE 的 `paddd`」**：不。SSE 的 paddd 固定 128-bit 4-lane。RVV 的 vadd 對 `vl` 個 element、element 寬由 SEW 決定。
2. **「每條 vector 指令前都要 vsetvli」**：不需要。`vtype` / `vl` 是 sticky CSR。只要你沒改 SEW / LMUL / AVL，**不用重新 vsetvli**。compiler 會盡量合併。
3. **「LMUL 越大越好」**：不。register pressure 會讓 compiler 被迫 spill。hot loop 用 m4 / m8、non-hot code 用 m1 是典型配置。
4. **「RVV binary 在任何 VLEN 上都最快」**：不保證。某些 pattern 在特定 VLEN 才最佳。但「不會跑不起來」成立 — 這是 VLA 的核心保證。
5. **「RVV 不支援 SIMD intrinsic」**：有。`<riscv_vector.h>` 是 standardised，寫法像 `vint32m1_t v = __riscv_vadd_vv_i32m1(a, b, vl);`。但醜，多數人選 compiler auto-vectorize。

## 對 compiler 工程師的重點

SiFive job spec 明確說要做 compiler 優化、加 extension。RVV 是主戰場：

- **LLVM auto-vectorizer 對 VLA 的支援還在積極開發**。Scalable Vectorization 是 LLVM 專有術語（VPlan-based）。這是 upstream contribution 的好入口。
- **`vsetvli` 的放置是效能關鍵**。放太多 → 浪費；放太少 → 正確性問題。有一個 pass 叫 **RISCV Insert VSETVLI**（`RISCVInsertVSETVLI.cpp`），專門做 dataflow 分析決定放 vsetvli。**讀這個 pass 是 compiler backend 課的指定閱讀**。
- **Fractional LMUL 的寄存器分配**在現行 LLVM 還不完美，偶爾 suboptimal。是你能貢獻的領域。
- **Scheduling model**：新 core 做 SiFive 式的 performance tuning 時，要寫 `RISCVSchedSiFive7.td` 這類 TableGen 文件，精確描述每條 vector 指令的 latency / throughput。

## 動手練習

1. 把本章的 `rvv_memcpy` 組出來，用 spike 的 `--varch=vlen:256,elen:64` 參數跑。換 `vlen:128` 再跑。觀察效能差。
2. 寫 C 版的 vector add：`void add(int *a, int *b, int *c, int n) { for (i) c[i]=a[i]+b[i]; }`，`-march=rv64gcv -O3 -fno-tree-vectorize` vs `-ftree-vectorize`，看 compiler 產生什麼。
3. 寫 C 版的 dot product，看 compiler 有沒有用 reduction 指令（`vredsum.vs`）。如果沒有，改寫成 compiler 喜歡的形式（例：用 `#pragma loop vectorize`）。
4. 故意用 `__riscv_vadd_vv_i32m1` / `__riscv_vadd_vv_i32m8` 寫兩版 add，量測時間差。
5. 讀 LLVM 的 `RISCVInsertVSETVLI.cpp`，看它如何決定何處放 vsetvli。這是 compiler backend 課的提前熱身。

## 自我檢核

- [ ] 我能解釋 VLA programming model 跟 SSE / AVX 的根本差異
- [ ] 我能默寫 `VLMAX = VLEN * LMUL / SEW`
- [ ] 我能寫一個標準的 strip-mining 迴圈用 `vsetvli + vl`
- [ ] 我知道 LMUL 的 group 機制以及為什麼 LMUL=8 只能用 4 組 register
- [ ] 我看到 `vadd.vv` / `vwmul.vv` 能分出 widening 與 non-widening

下一章處理 Zc 系列擴充 — 2023 年後新加的「比 C 更省」的壓縮指令，對嵌入式領域 code size 影響大。

→ [Ch 9 Zc 擴充與 code size](./09-code-size-extension.md)
