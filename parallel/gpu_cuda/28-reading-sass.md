# Ch 28 讀 SASS

> SASS 片段為示意性輸出，實際結果依 ptxas/nvcc 12.x 版本略有出入，以讀者用 `cuobjdump -sass` 重現為準。NVIDIA 沒有官方 SASS ISA 文件，本章內容源自逆向工程成果（以 Turing sm_75 為主）。

---

## 為什麼要讀 SASS

PTX 是你寫給 NVIDIA 的說明書，SASS 才是 GPU 實際執行的事。

Ch 27 我們學了 PTX——它說「load global f32」、「add f32」、「store global f32」。這些都是語義層的描述，跨架構可移植。但 PTX 不告訴你：
- ptxas 把你的虛擬暫存器 `%f0`、`%f1` 放進哪個實體暫存器
- `add.f32` 實際上是不是一條 `FFMA`（它可以是，因為 a*1+b = a+b）
- 兩次 LDG 之間有幾個 cycle 的 stall
- scoreboard 設在哪個槽位、是否有 warp-level 切換

效能調優要讀 SASS 的理由是：ptxas 的決策直接決定效能，而它不是透明的。你標 `__launch_bounds__`、寫 `#pragma unroll N`、調整迴圈結構，這些都是在間接影響 ptxas 吐出的 SASS。讀 SASS 讓你直接確認那些決策是不是你預期的。

具體場景：
- 暫存器壓力：SASS 能讓你數出 kernel 用了多少實體暫存器（`R0`...`R_N`），確認 occupancy 受限源頭
- 指令融合：確認 `add.f32` 有無被 ptxas 融進更大的 FFMA 鏈
- 排程品質：LDG 後的 stall count 是否合理隱藏了全域記憶體延遲
- 不必要的 spill：看到 `STL`（Store Local，spill 到 local memory）是警訊

---

## 環境設置

作者環境無 GPU 和 nvcc，本章所有 SASS 片段均在 Godbolt 或 Colab T4 產生。

```bash
# 方法一：直接產 cubin，再反組譯
nvcc -cubin -arch=sm_75 vector_add.cu -o vector_add.cubin
cuobjdump -sass vector_add.cubin

# 方法二：nvdisasm，輸出更詳細，可附 hex
nvdisasm -c -hex vector_add.cubin

# 方法三：從完整 binary 反組譯（cuobjdump 會自己找內嵌的 cubin）
cuobjdump -sass vector_add

# 方法四（最快）：Godbolt.org
#   選 nvcc 12.x compiler → 加 '-arch=sm_75' → 切到 Assembly 分頁
#   Assembly 分頁直接就是 SASS
```

`cuobjdump -sass` 和 `nvdisasm` 的輸出略有不同。`cuobjdump` 比較簡潔；`nvdisasm -hex` 會同時印出每條指令的 128-bit hex，讓你能手動拆解 control code 欄位。對於初次讀 SASS 的人，從 `cuobjdump` 開始就夠。

---

## Turing SASS 的基本格式

Volta 架構之後，每條 SASS 指令固定 **128 bits（16 bytes）**。這代表在 cuobjdump 的輸出裡，指令位址以 `0x10`（16 的十六進位）遞增：

```
        /*0000*/   IMAD.MOV.U32 R1, RZ, RZ, c[0x0][0x28] ;
        /*0010*/   ISETP.GE.AND P0, PT, R0, c[0x0][0x170], PT ;
        /*0020*/   @!P0 BRA `(.L_1) ;
        /*0030*/   IMAD R4, R2, 0x4, c[0x0][0x160] ;
```

這 128 bits 的配置：高位 21 bits 是排程控制碼（control code），低位 107 bits 是指令本體（opcode + operands）。Maxwell/Pascal 時期，control code 是每 3 條指令共享一個獨立的 64-bit control word；Volta/Turing 改成 inline，每條指令自帶，scoreboard 槽數也從 5 個擴充到 6 個。

---

## 控制碼解剖（Control Code）

這是讀者在 PTX 層完全看不到的東西，是 SASS 排程的靈魂。

Turing 每條指令嵌入的 21-bit 控制碼：

```
bits [20:17]  reuse_flags   — 4-bit，per-source register reuse（L0 cache hint）
bits [16:11]  wait_mask     — 6-bit bitmask，等待哪幾個 scoreboard 槽
bits [10:8]   read_barrier  — 3-bit，source read 完畢後 signal 哪個槽
bits [7:5]    write_barrier — 3-bit，目標 write 完畢後 signal 哪個槽
bit  [4]      yield_flag    — 1-bit，1 = 允許 warp scheduler 切換到另一個 warp
bits [3:0]    stall_count   — 4-bit，此 warp 在發出下一條指令前要停幾 cycle（0-15）
```

CuAssembler 的 `.cuasm` 格式把這些欄位用可讀標記法寫出來：

```
[B------:R-:W-:-:S04]   FFMA R2, R2, R5, R6 ;
[B0----:R-:W1:Y:S04]    LDG.E.SYS R4, [R2] ;
```

前半方括號逐欄對應：
- `B012345`：wait_mask，`-` 代表不等待該槽，數字代表等待對應槽
- `R0`：read_barrier，signal 槽 0
- `W1`：write_barrier，signal 槽 1
- `Y` / `-`：yield flag 開/關
- `S04`：stall count = 4 cycles

### 固定延遲 vs. 可變延遲指令

FFMA 是固定延遲（約 4 cycles）。ptxas 直接在 stall count 欄位編碼等待時間，**不需要 scoreboard**：

```
[B------:R-:W-:-:S04]   FFMA R2, R2, R5, R6 ;
```

LDG（Load Global）是可變延遲（L2 hit 幾十 cycle，DRAM 幾百 cycle）。ptxas 用 scoreboard 追蹤：

```
[B------:R-:W1:Y:S04]   LDG.E.SYS R4, [R2] ;
...（中間排入其他指令）...
[B0----:R-:W-:-:S01]    FFMA R6, R4, R7, R8 ;   // wait_mask B0 = 等槽 0
```

關鍵事實：**FFMA 的 stall_count 填錯不會有 hardware interlock**，GPU 只是靜默地跑出錯誤結果。這是 SASS 層 hand-assembly 比 x86 危險的地方——x86 有 forwarding 和 OOO，NVIDIA 對固定延遲指令假設你（ptxas）已把時序算對。

---

## 主要指令集（Turing sm_75）

### 記憶體存取

```sass
LDG.E.SYS   R2, [R2]          # Load Global, Extended addr, SYStem coherent
LDG.E.64.SYS  R6, [R18]       # 64-bit load，R6:R7 pair（一次抓 8 bytes）
STG.E.SYS   [R4], R2          # Store Global
```

`.E` 是 Extended addressing（64-bit 位址），不是 extended size。`.SYS` 是 coherence scope（system coherent）。看到 `[R2.64]` 的寫法也可能出現在不同版本 nvdisasm 輸出，`.64` 同樣指 64-bit 位址對，不是 64-bit load size。

### 算術

```sass
FFMA  R2, R2, R5, R6          # R2 = R2 * R5 + R6（FP32 FMA，單一指令）
FFMA.FTZ  R7, R6, R7, 0.018167  # FTZ = flush denormals to zero
IMAD  R12, R10, 0x4, R9       # R12 = R10 * 4 + R9（整數 FMA，Turing 取代 XMAD）
IMAD.WIDE  R2, R4, R5, c[0x0][0x168]   # 32x32 → 64-bit result
```

`XMAD` 是 Maxwell/Pascal 的整數乘法指令（需要多條 XMAD 合成 32-bit 乘法）；Turing 起換成 `IMAD`，一條搞定。

### 特殊指令

```sass
IMAD.MOV.U32  R1, RZ, RZ, c[0x0][0x28]   # 從 constant bank 0 載入 kernel 參數
                                           # MOV 是 IMAD 的退化（src0 * src1 + imm = 0*0 + imm）
RZ                                          # R255，零暫存器，永遠讀 0，寫到 RZ = discard
```

`c[0x0][0x28]` 的格式是 constant memory bank 0、offset 0x28。Kernel 參數（blockDim、gridDim、使用者傳入的指標）全部透過 constant memory 傳進來，prologue 裡一堆 `IMAD.MOV.U32 Rx, RZ, RZ, c[0x0][offset]` 是這個原因。

### 控制流

```sass
ISETP.GE.AND  P0, PT, R0, c[0x0][0x170], PT   # if (R0 >= param) P0 = true
@!P0 BRA  `(.L_1)                              # if (!P0) goto .L_1
BRA.U  `(.L_20)                                # Uniform branch（所有 thread 同路）
EXIT                                            # kernel 結束
```

Predicate register P0-P6（Turing 有 7 個）控制條件執行。`PT` 是 always-true predicate（常數 1）。`ISETP.GE.AND` 是「Compare + Set Predicate + AND with existing predicate」的融合指令。`@P0 OPCODE` 代表 P0=true 才執行；`@!P0 OPCODE` 取反。

---

## 完整範例：vector add kernel 逐行解析

以下 C 端 kernel 為對照基準：

```cuda
__global__ void vector_add(const float *a, const float *b, float *c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) c[i] = a[i] + b[i];
}
```

下面是這支 kernel 在 Turing sm_75 上的完整 SASS 示意輸出，含逐行中文解析（示意片段，以 `cuobjdump -sass` 重現為準，NVIDIA 無官方 SASS ISA 文件）。

### Kernel Prologue：建立 Stack Frame 與載入參數

```sass
/*0000*/  IMAD.MOV.U32 R1, RZ, RZ, c[0x0][0x28] ;
```

`IMAD.MOV.U32` 是 Turing 上的 MOV 慣例：`RZ * RZ + src = 0 * 0 + src = src`，效果等於 `R1 = c[0x0][0x28]`。`c[0x0][0x28]` 是 constant memory bank 0 偏移 0x28，存放的是 kernel 的 stack frame size 或 call-frame 配置資訊（視 nvcc 版本而定；非遞迴 kernel 通常是 0）。這是每個 kernel 固定的開場白，告訴 warp scheduler 此 warp 的 local memory 需求。

### 取得 Thread 索引

```sass
/*0010*/  S2R R0, SR_CTAID.X ;
```

`S2R`（Special Register to Register）把 special register `SR_CTAID.X` 搬進 `R0`。`SR_CTAID.X` 就是 CUDA 的 `blockIdx.x`，這是 SM 硬體提供的只讀暫存器，每個 warp 取到的值相同（同一個 block 內所有 warp 共享同一個 `blockIdx`）。

```sass
/*0020*/  S2R R2, SR_TID.X ;
```

`SR_TID.X` 對應 `threadIdx.x`。同一個 warp 裡 32 個 lane 取到不同值（lane 0 到 lane 31），這是 SIMT 分流的源頭。

### 計算全域 Thread 索引

```sass
/*0030*/  IMAD R0, R0, c[0x0][0x0], R2 ;
```

`IMAD R0, R0, c[0x0][0x0], R2` 計算 `R0 = blockIdx.x * blockDim.x + threadIdx.x`。`c[0x0][0x0]` 存放 `blockDim.x`（kernel launch 時傳入，ptxas 把它塞進 constant bank 0 的固定偏移）。結果存回 R0，之後全程用 R0 當 thread 索引 `i`。

### 邊界檢查與條件跳轉

```sass
/*0040*/  ISETP.GE.AND P0, PT, R0, c[0x0][0x170], PT ;
```

`ISETP.GE.AND` 是融合指令，語義是 `P0 = (R0 >= c[0x0][0x170]) AND PT`，因為 `AND PT` 等於 AND 1，所以簡化為 `P0 = (i >= n)`。`c[0x0][0x170]` 是使用者傳入的參數 `n`，存在 constant bank 0 偏移 0x170（具體偏移由 ptxas 依 kernel 參數順序決定）。`PT`（Predicate True）是永遠為真的系統 predicate，當作 identity 元素。

```sass
/*0050*/  @P0 EXIT ;
```

`@P0 EXIT` 在 `P0 = true`（即 `i >= n`）時執行 EXIT。這是 out-of-bounds thread 的快速退出路徑。注意這裡用 `@P0 EXIT` 而不是 `@!P0 BRA` 再 `EXIT`——現代 ptxas 傾向用帶 predicate 的 EXIT 減少指令數，但部分舊版本會生成 `@!P0 BRA .L_work` 再接 `EXIT`。

### 計算 Global Memory 位址（64-bit Byte Offset）

```sass
/*0060*/  IMAD.WIDE R2, R0, 0x4, c[0x0][0x160] ;
```

`IMAD.WIDE` 是 32×32→64 乘法，結果寫入暫存器對 `R2:R3`（R2 低 32 bits，R3 高 32 bits）。語義是 `R2:R3 = (int64_t)i * 4 + base_a`。`0x4` 是 `sizeof(float)`，`c[0x0][0x160]` 是指標 `a` 的 64-bit 基底位址——ptxas 把 64-bit 指標切成低 32 bits（`c[0x0][0x160]`）和高 32 bits（`c[0x0][0x164]`）分開傳，IMAD.WIDE 在加法時自動處理進位。

```sass
/*0070*/  IMAD.WIDE R4, R0, 0x4, c[0x0][0x168] ;
```

同理，計算 `b[i]` 的位址，結果存 `R4:R5`。`c[0x0][0x168]` 是指標 `b` 的基底。

```sass
/*0080*/  IMAD.WIDE R6, R0, 0x4, c[0x0][0x178] ;
```

計算 `c[i]` 的寫入位址，結果存 `R6:R7`。ptxas 可能把這條排在後面（在 LDG 之後、STG 之前），以允許 LDG 先出去吃延遲。這裡的排列是最保守的順序。

### 載入 a[i] 與 b[i]（可變延遲，觸發 scoreboard）

```sass
/*0090*/  LDG.E.SYS R8, [R2] ;
```

`LDG.E.SYS R8, [R2]` 讀取全域記憶體位址 `R2:R3`（`.E` = Extended = 64-bit 位址對），把 4 bytes 載入 R8（預設 `.E` 沒有額外 size modifier 時是 32-bit load）。`.SYS` 是 system-coherent scope，讓 L2 快取可被 CPU 端看見（對一般 GPU-only kernel 通常沒差，但 ptxas 傾向保守地加上）。

這條 LDG 的 control code 會設 write_barrier，把「R8 就緒」的事件掛在某個 scoreboard 槽（例如槽 1），並把 yield_flag 設 1，讓 warp scheduler 可以在等待 DRAM 時切到其他 warp。

```sass
/*00a0*/  LDG.E.SYS R9, [R4] ;
```

同步發出 `b[i]` 的 load，結果進 R9。此時兩個 LDG 都已「in-flight」，它們可以同時在 memory pipeline 裡飛，實際 DRAM 延遲相互重疊。這兩條 LDG 是 kernel 裡最關鍵的延遲來源，ptxas 要確保在 FFMA 消耗 R8/R9 之前有足夠的其他指令填滿等待時間。

### 等待 LDG 結果，執行加法

```sass
/*00b0*/  FFMA R8, R8, 1, R9 ;
```

`FFMA R8, R8, 1, R9` 計算 `R8 = R8 * 1.0f + R9`，等效於 `a[i] + b[i]`。`add.f32` 在 PTX 層消失，被 ptxas 替換成 FFMA——乘以 1.0 沒有精度損失（IEEE 754 乘以 1.0 是 identity），而且 FFMA 的吞吐量和 FADD 完全相同，換指令沒有代價。

這條 FFMA 的 control code wait_mask 會設成等待前面兩個 LDG 掛的 scoreboard 槽（例如 `B01----`），確保 R8 和 R9 都已就緒再執行乘加。如果 wait_mask 沒設對（或兩條 LDG 共用同一個槽），FFMA 可能讀到尚未就緒的暫存器值，造成靜默錯誤。

### 寫回 c[i]

```sass
/*00c0*/  STG.E.SYS [R6], R8 ;
```

`STG.E.SYS [R6], R8` 把 R8 的 4 bytes 寫進全域記憶體位址 `R6:R7`。Store 不需要 scoreboard 等待返回確認（GPU 的 store 是 fire-and-forget，完成靠 `__threadfence()` 或 kernel 結束隱式 fence），所以 control code 通常 stall count 很小，write_barrier 槽設為 `-`。

### Kernel 結束

```sass
/*00d0*/  EXIT ;
```

`EXIT` 終止此 warp 的執行。所有 thread（lane）都執行到 `EXIT` 後，warp 被標記完成，SM 可以排入下一個 warp block。

```sass
/*00e0*/  BRA.U `(.L_exit) ;
```

`BRA.U`（Uniform Branch，Unconditional）是 dead code，部分 ptxas 版本在 EXIT 後強制插入一條 BRA 做 padding 或對齊用途。它永遠不會被執行，但出現在 SASS 輸出裡是正常現象。

---

## Control Code 逐欄實例解析

下面把上面的 vector add SASS 片段加上 CuAssembler 格式的 control code，逐欄拆解（示意片段，以 `cuobjdump -sass` 重現為準，NVIDIA 無官方 SASS ISA 文件）：

```sass
// 格式：[Bwait_mask:Rread_bar:Wwrite_bar:yield:Sstall]   OPCODE ;
// wait_mask B012345：等哪幾個 scoreboard 槽，- = 不等
// yield Y/-：Y = 允許切 warp；stall Sxx：停幾 cycle 發下一條

[B------:R-:W-:-:S01]  IMAD.MOV.U32 R1, RZ, RZ, c[0x0][0x28] ;
    // stall=1：prologue 很輕量，不需要額外等待
    // wait_mask 全空：沒有前置依賴

[B------:R-:W-:-:S04]  S2R R0, SR_CTAID.X ;
    // S2R 固定延遲約 4 cycles，stall=4 直接等
    // 不需要 scoreboard，SR 讀取延遲是固定的

[B------:R-:W-:-:S04]  S2R R2, SR_TID.X ;
    // 同上，另一個 special register 讀取

[B------:R-:W-:-:S01]  IMAD R0, R0, c[0x0][0x0], R2 ;
    // IMAD 固定延遲，stall=1 後直接接 ISETP
    // 此時 R0（blockIdx）和 R2（threadIdx）都已就緒

[B------:R-:W-:-:S01]  ISETP.GE.AND P0, PT, R0, c[0x0][0x170], PT ;
    // 整數比較，固定延遲，stall=1

[B------:R-:W-:-:S05]  @P0 EXIT ;
    // predicated EXIT，stall=5 是 branch resolve 的固定代價

[B------:R-:W-:-:S01]  IMAD.WIDE R2, R0, 0x4, c[0x0][0x160] ;
    // 64-bit 乘法，結果進 R2:R3，固定延遲

[B------:R-:W-:-:S01]  IMAD.WIDE R4, R0, 0x4, c[0x0][0x168] ;
    // 計算 b 的位址，進 R4:R5

[B------:R-:W1:Y:S04]  LDG.E.SYS R8, [R2] ;
    // W1：R8 就緒後 signal scoreboard 槽 1
    // Y：允許切 warp（這裡讓出控制權等 DRAM）
    // stall=4：不是等 LDG 完成，而是確保下一條 LDG 能順利發出

[B------:R-:W2:Y:S04]  LDG.E.SYS R9, [R4] ;
    // W2：R9 就緒後 signal 槽 2
    // 兩個 LDG 用不同槽，FFMA 可以分別等待

[B------:R-:W-:-:S01]  IMAD.WIDE R6, R0, 0x4, c[0x0][0x178] ;
    // ptxas 把 c 的位址計算插在兩個 LDG 之後，用來填延遲

[B12----:R-:W-:-:S01]  FFMA R8, R8, 1, R9 ;
    // B12：同時等 scoreboard 槽 1（R8 就緒）和槽 2（R9 就緒）
    // 兩個 LDG 都完成後才執行 FFMA
    // stall=1：FFMA 後接 STG，FFMA 固定延遲 ptxas 已在 wait 裡處理

[B------:R-:W-:-:S01]  STG.E.SYS [R6], R8 ;
    // Store 不需要等 completion，fire-and-forget
    // stall=1 就夠

[B------:R-:W-:-:S05]  EXIT ;
    // stall=5 是 EXIT 的慣例值，確保 pipeline drain
```

幾個值得記住的規律：

- **固定延遲指令（IMAD、FFMA、ISETP）**：用 stall count 直接編碼，不用 scoreboard
- **可變延遲指令（LDG）**：write_barrier 設槽，yield=Y，讓 warp scheduler 填補延遲
- **FFMA 等 LDG**：wait_mask 同時帶多個槽位（`B12`），只要有任一槽尚未 signal 就阻塞
- **stall=0**：意思不是「不等待」，而是「立刻發下一條」——如果依賴還沒就緒且沒設 scoreboard，會靜默讀錯

---

## 常數記憶體 c[bank][offset] 語法詳解

這個語法在 vector add 的 SASS 裡無所不在，值得獨立說清楚。

```sass
c[0x0][0x28]    — bank 0，offset 0x28（40 bytes）
c[0x2][0x10]    — bank 2，offset 0x10（比較罕見）
```

### Bank 0 是 kernel 參數的家

CUDA 的 kernel 參數（`__global__ void foo(float *a, int n, ...)`）透過 constant memory bank 0 傳遞，這是 PTX `.param` state space 的底層實作。ptxas 在 kernel 開頭生成一系列 `IMAD.MOV.U32 Rx, RZ, RZ, c[0x0][offset]` 把參數搬進 register file，之後不再直接存取 constant memory（避免每次用到參數都打 constant cache）。

Bank 0 的典型佈局（ptxas 12.x，sm_75，具體位址因 nvcc 版本而異）：

```
c[0x0][0x00]  blockDim.x          (uint32)
c[0x0][0x04]  blockDim.y          (uint32)
c[0x0][0x08]  blockDim.z          (uint32)
c[0x0][0x0c]  gridDim.x           (uint32)
c[0x0][0x10]  gridDim.y           (uint32)
c[0x0][0x14]  gridDim.z           (uint32)
c[0x0][0x18]  ... (alignment/padding)
c[0x0][0x28]  frame size / ABI marker
c[0x0][0x160] 第 1 個 user 參數低 32-bit（float *a）
c[0x0][0x164] 第 1 個 user 參數高 32-bit
c[0x0][0x168] 第 2 個 user 參數低 32-bit（float *b）
c[0x0][0x16c] 第 2 個 user 參數高 32-bit
c[0x0][0x170] 第 3 個 user 參數（int n，32-bit 整數）
```

PTX 的對應關係：PTX `.param` 宣告的參數，在 PTX 層用 `ld.param.u64 %rd0, [_Z10vector_addPfPfPfi_param_0]` 讀取；ptxas 把這個 `ld.param` 翻譯成 `IMAD.MOV.U32 Rx, RZ, RZ, c[0x0][...]`（prologue 預載）或 inline `LDC`，具體視最佳化決策而定。

### Bank 1~15 的用途

Bank 0 以外通常存放：`cudaMemcpyToSymbol` 寫入的 `__constant__` 全域陣列、驅動層 per-launch 資訊、以及 ptxas 把 read-only 資料提升進 constant memory 的最佳化結果（類似 `__ldg` hint 的效果）。一般純 CUDA kernel 只見 `c[0x0]`。

---

## Maxwell/Pascal vs. Turing Control Code 差異

這個差異是 SASS 分析最大的「版本陷阱」。網路上大量 SASS 分析文章在講 Maxwell（sm_52）或 Pascal（sm_61），那些 control code 格式和 Turing（sm_75）完全不同，不能直接套用。

### Maxwell/Pascal：分離式 64-bit Control Word

Maxwell 和 Pascal 用一個外掛的 64-bit control word 控制連續三條指令的排程：

```
// Maxwell/Pascal 格式示意（示意片段，以 cuobjdump -sass 重現為準）
//
// 64-bit control word（前導 word，管後面 3 條指令）
// ┌──────┬──────┬──────┬──────┬──────┬──────┬──────────────┐
// │ ruse │ wait │r_dep │w_dep │yield │stall │  (per instr) │  × 3 組
// └──────┴──────┴──────┴──────┴──────┴──────┴──────────────┘

// cuobjdump 輸出看起來像這樣：
/*0008*/  {                          // 開始 3-instruction bundle
/*0008*/  .sched 0x20               // 64-bit control word（hex 表示）
/*0010*/  XMAD CC, R3, R5, R6 ;    // 指令 1
/*0018*/  XMAD.MRG R7, R3, R5.H1, RZ ; // 指令 2
/*0020*/  XMAD.PSL.CBCC R5, R3.H1, R5.H1, R7 ; // 指令 3
          }
```

主要差異：
- **Control word 是外掛的**（不是 inline per-instruction），每 3 條指令共用
- **Scoreboard 只有 5 個槽**（bits [4:0]），Turing 有 6 個
- **整數乘法用 XMAD**（需要 3~4 條 XMAD 合成一個完整 32-bit 乘法），Turing 換成單條 IMAD
- **指令長度是 64 bits**，不是 Turing 的 128 bits
- **Control code 在前導 word，不在指令本身**，cuobjdump 輸出格式因此完全不同

Volta（sm_70）是轉型起點：control word 改成 inline 128-bit，scoreboard 槽擴充到 6 個，整體和 Turing 相似但有些微指令差異。Turing（sm_75）是社群 SASS 文件最豐富、CuAssembler 支援最好的架構，也是本章的主要對象。Ampere（sm_80）起有新 opcode 和 modifier，但 128-bit inline control code 框架延續至 Hopper（sm_90）。

### 差異總結

| 特性 | Maxwell/Pascal（sm_5x/6x）| Volta/Turing+（sm_7x+）|
|------|--------------------------|-------------------------|
| 指令寬度 | 64 bits | 128 bits |
| Control code 位置 | 每 3 條指令前的獨立 64-bit word | inline，每條指令高 21 bits |
| Scoreboard 槽數 | 5 | 6 |
| 整數乘法指令 | XMAD（3~4 條合成） | IMAD（單條） |
| cuobjdump 輸出格式 | bundle 式（`{}`+ `.sched`） | 逐條列出 |
| 社群文件完整度 | 中（Maxwell 時代分析較多） | 高（CuAssembler 主力維護）|

如果你在查 blog post 或 GitHub issue，確認對方講的是哪個架構再套用，否則 stall count 的欄位位置、scoreboard 槽的解讀都是錯的。

---

## PTX vs SASS 對照表

| 維度 | PTX | SASS |
|------|-----|------|
| 暫存器 | 無限虛擬（`%r0`、`%f0`...） | 有限實體（`R0`...`R255`）|
| 指令語意 | 語義級（`add.f32`） | 微架構級（`FFMA`）|
| 排程資訊 | 無（ptxas 決定） | 每條指令 stall count + scoreboard |
| 可移植性 | 跨架構（`compute_75` 可跑 sm_80） | 架構鎖定（sm_75 不跑 sm_80）|
| 官方文件 | 完整公開（CUDA PTX ISA 手冊） | 無官方文件（逆向工程）|
| 手寫/編輯 | 可手寫，nvcc 接受 `.ptx` | 需 CuAssembler 等第三方工具 |
| 暫存器數量影響 | 不影響（虛擬） | 直接決定 occupancy |
| 分析工具 | nvcc --ptxas-options=-v | cuobjdump、nvdisasm、CuAssembler |
| Constant memory 可見性 | `.param` / `.const` state space | `c[bank][offset]` 直接裸露 |
| 指令融合可見性 | 看不到（`add.f32` 保持原狀） | 明確顯示（`FFMA R8, R8, 1, R9`）|

最關鍵的差距：PTX 的 `%f0` 可能被 ptxas 分配到 R4，也可能是 R20，取決於整個 kernel 的 liveness 分析結果。SASS 讓你看到實際配置，PTX 看不到。

---

## 踩雷集

**1. cuobjdump 不輸出完整 control code 解碼**

`cuobjdump -sass` 通常不會印出那 21-bit 控制碼的詳細分解（wait mask、scoreboard 槽、stall count）。要看控制碼，用 `nvdisasm -c -hex`（`-c` 輸出 control code，`-hex` 附 raw bytes），或用 CuAssembler 的反組譯器，它會輸出 `[B...:R-:W-:-:Sxx]` 格式。

**2. SASS 暫存器是 per-warp 私有的**

R0、R4、R8 這些是每個 warp 自己的暫存器，不是 SM 上所有 thread 共享的全域位置。你在 SASS 看到 `LDG R4, [R2]` 不代表「全 SM 的 R4」，而是「這個 warp 裡所有 thread 各自的 R4」。32 個 thread 各一份。

**3. `[R2.64]` 的 `.64` 不是 load size**

`.64` 是 Extended Addressing 的標記，意思是 R2:R3 組成一個 64-bit 指標。load size 由指令本身決定（預設 `.E` 就是 32-bit load）。`LDG.E.64.SYS` 裡的 `.64` 才是 load size（64-bit，抓 8 bytes，結果放 R_n:R_{n+1}）。兩種 `.64` 出現的位置不同。

**4. RZ 不能寫，寫到 RZ = discard**

R255 永遠讀 0，寫進 RZ 的結果被丟棄。ptxas 常用 `IMAD.MOV.U32 Rx, RZ, RZ, src` 當 move 指令（`0*0 + src = src`），以及 `IMAD Rx, Rsrc, imm, RZ` 做純乘法（加零）。這是 Turing 上實現 MOV/MUL 的慣例。

**5. Turing SASS 和 Maxwell/Pascal 差異很大**

Maxwell/Pascal 的 control code 是每 3 條指令共用一個獨立的 64-bit word（放在 3 條指令前面）；Volta/Turing 改成每條指令 inline 128 bits。另外 scoreboard 槽數從 5 個增加到 6 個。老的 SASS 分析工具/blog post 如果在講 Maxwell，整個 control code 格式完全不同，不能直接套用。

**6. FFMA stall_count 錯了不會 crash，只會算錯**

這是 SASS 手工組裝和 x86 hand-assembly 最大的差別。x86 有 OOO + forwarding，時序錯誤通常會被硬體遮蓋；NVIDIA GPU 對固定延遲指令完全相信 ptxas 填的 stall count，填短了會讀到未就緒的暫存器，結果靜默錯誤。手改 SASS 必須精確知道每條指令的固定延遲週期數。

---

## 進階：自己改 SASS

如果 ptxas 的排程決策讓你不滿意，有工具可以直接操作 SASS。

**CuAssembler**（cloudcores/CuAssembler on GitHub）：目前最主流的 open-source Turing SASS assembler/disassembler，可以把 SASS 反組譯成 `.cuasm`（帶完整 control code 標記法的文字格式），修改後再組裝回 cubin。這是手動調整 stall count、scoreboard 槽、reuse flag 的方式。

適用場景：你寫了一個 hand-tuned kernel，懷疑 ptxas 的暫存器配置或排程不對，想直接注射特定 SASS 序列做 A/B 測試。

**流程大致是：**

```bash
# 1. 產 cubin
nvcc -cubin -arch=sm_75 my_kernel.cu -o my_kernel.cubin

# 2. CuAssembler 反組譯
python cuasm.py --decompile my_kernel.cubin -o my_kernel.cuasm

# 3. 手改 my_kernel.cuasm（改 stall count、換指令順序）

# 4. 重新組裝
python cuasm.py my_kernel.cuasm -o my_kernel_tuned.cubin

# 5. 用 cuLaunchKernel 或 driver API 直接跑 cubin
```

要注意的是：NVIDIA 不保證 SASS 格式的版本穩定性，CuAssembler 只測試特定 CUDA/GPU 組合。這是「最後手段」工具，不是日常工作流。

---

## 進階閱讀

1. **CUDA Binary Utilities 官方文件**（NVIDIA）：`cuobjdump` 和 `nvdisasm` 的 flag 清單和輸出格式說明，這是 NVIDIA 唯一公開的 SASS 周邊文件。

2. **CuAssembler**（cloudcores/CuAssembler，GitHub）：Turing/Ampere SASS assembler，附帶大量 Turing 指令的逆向工程紀錄和 control code 格式說明，是目前最完整的 community SASS 文件。

3. **"Dissecting the NVIDIA Turing T4 GPU via Microbenchmarking"**（Jia et al., arXiv 1903.07486）：系統性解剖 Turing T4 的指令延遲、吞吐、記憶體階層、control code 格式。這篇是讀懂 Turing SASS 的首選學術參考。

4. **Citadel microbenchmark 論文**（Jia et al. 系列後續）：延續 arXiv 1903.07486，更深挖 warp scheduler 行為和 latency hiding 量測方法——自己寫 microbenchmark 驗測指令延遲的方法論從這裡找。

---

## 小結

SASS 和 PTX 的關係像機器碼和 C——後者是你表達意圖的語言，前者才是硬體實際執行的序列。從 PTX 到 SASS 的過程中，ptxas 做了三件大事：

1. **暫存器配置**：把無限虛擬暫存器對應到有限實體暫存器，影響 occupancy
2. **指令選擇**：`add.f32` → `FFMA`，`multiply` → `IMAD`，融合機會決定指令數
3. **排程與控制碼**：決定每條指令的 stall count、scoreboard 槽、warp yield，直接影響 latency hiding 效果

讀 SASS 的目的不是替代 ptxas 做排程，而是確認 ptxas 的決策和你的預期一致，以及在它做出次優決策時，有能力診斷出來。下一章我們會拿著 SASS 去測指令的實際延遲和吞吐量——那些數字讓 stall count 的選擇有了量化的基礎。

---

## 本章連結

- 前一章：[Ch 27 讀 PTX](./27-reading-ptx.md)
- 下一章：[Ch 29 指令層級真相](./29-instruction-level.md)
