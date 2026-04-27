# Ch 4 — M / A / F / D / C：標準擴充五件套

> 目標：弄懂 `rv64imafdc`（= `rv64gc`）裡每個字母代表什麼、為什麼這幾個一起幾乎是「Linux 能跑」的最低門檻。讀完你對 toolchain 裡 `-march=` 字串的每一個字母都有精確心像。

## 先把字母表背起來

RISC-V 的 ISA 名稱由一串字母組成：

```
rv64 i m a f d c
 │   │ │ │ │ │ │
 │   │ │ │ │ │ └─ C: 壓縮指令 (16-bit)
 │   │ │ │ │ └─── D: 雙精度浮點
 │   │ │ │ └───── F: 單精度浮點
 │   │ │ └─────── A: atomic
 │   │ └───────── M: 整數乘除
 │   └─────────── I: 基礎整數 (就是 Ch 1 講的 RV32I/RV64I)
 └─────────────── XLEN：暫存器寬度 (32 或 64)
```

**`g` 是一個懶人簡稱**，等於 `imafd_zicsr_zifencei`（M+A+F+D + 兩個必備小擴充）。所以：

```
rv64gc  =  rv64imafdc + zicsr + zifencei
```

這串字就是所謂的 **"Linux baseline"**。大部分 RISC-V distro（Ubuntu RISC-V port、Fedora、Debian）都要求這個組合起跳。理由：glibc / gcc 產生的 binary 假設這些擴充都在。

## M 擴充：乘除

```
mul     rd, rs1, rs2    # rd = (rs1 * rs2)[31:0]   (low 32 bits)
mulh    rd, rs1, rs2    # rd = (rs1 * rs2)[63:32]  (signed × signed, high bits)
mulhsu  rd, rs1, rs2    # signed × unsigned, high
mulhu   rd, rs1, rs2    # unsigned × unsigned, high
div     rd, rs1, rs2    # signed 除
divu    rd, rs1, rs2    # unsigned 除
rem     rd, rs1, rs2    # signed 餘
remu    rd, rs1, rs2    # unsigned 餘
```

RV64 多一組 W 版本（32-bit 寬度運算、結果 sign-extend 填滿 64-bit）：`mulw`、`divw`、`divuw`、`remw`、`remuw`。

### 為什麼 base 不含乘法？

**這是 RISC-V 最經常被問的設計問題**。答案是：ISA 要服務的設備從 32-bit 的超小 MCU 到 64-bit HPC 都有。乘法器在最小的硬體上佔面積不小、有些 domain 用不到（純控制碼、DSP 有自己的 unit）。**讓乘法 optional** 比「塞進 base 但某些實作作弊」乾淨。

實務上 99% 的系統都開 M。沒 M 的主要是 ultra-low-power MCU。

### 除零行為：RISC-V 不 trap

```
div  rd, rs1, 0    # rd = -1 (所有 bit 全 1), 不 trap
divu rd, rs1, 0    # rd = ULLONG_MAX, 不 trap
rem  rd, rs1, 0    # rd = rs1, 不 trap
```

**這跟 x86 / ARM 不一樣**。RISC-V spec 刻意讓除零不產生例外 — 省掉所有 div 指令都要檢查的 overhead。要 trap 就 software 自己判斷。

Overflow（`INT_MIN / -1`）類似：

```
div  INT_MIN / -1    # rd = INT_MIN, 不 trap
```

## A 擴充：atomic

A 擴充處理兩件事：**原子讀寫** 與 **Load-Reserved / Store-Conditional**。

### AMO（Atomic Memory Operation）

一條指令做「讀-改-寫」：

```
amoadd.w rd, rs2, (rs1)     # rd = *rs1; *rs1 = rd + rs2
amoswap.w rd, rs2, (rs1)    # rd = *rs1; *rs1 = rs2
amoand.w / amoor.w / amoxor.w
amomax.w / amomin.w / amomaxu.w / amominu.w
```

還有 ordering 修飾：`.aq`（acquire）、`.rl`（release）、`.aqrl`（both）。Ch 15 會細講。

### LR / SC：原子更新的基礎

比 x86 `lock cmpxchg`（CAS）更靈活：

```
retry:
    lr.w    t0, (a0)        # load + 保留
    addi    t1, t0, 1
    sc.w    t2, t1, (a0)    # 嘗試寫；若中間有人動過 → t2 = 非零
    bnez    t2, retry       # 失敗就重試
```

**為什麼 LR/SC 比 CAS 好**：

- CAS 只能 compare-and-swap，想做「load, compute, store if unchanged」要兩個 memory access。
- LR/SC 中間**可以任意計算**（只要沒記憶體操作），一次 round trip。
- 實作上更符合現代 out-of-order pipeline 的假設。

代價：**LR/SC 不保證 forward progress**。硬體可能一直判定「被中斷了」然後 SC 失敗。spec 給了一套 **constrained LR/SC sequences** 的規範（loop 短、沒超過 16 條指令、不包含其他 AMO、不 branch 出 loop），保證這個 pattern 會 progress。

## F / D 擴充：浮點

F 是單精度（32-bit），D 是雙精度（64-bit，需要先有 F）。兩者都**引入新的 32 顆暫存器 `f0..f31`**（跟整數暫存器完全分離）：

```
f0..f31        # 浮點專用
fa0..fa7       # floating-point argument
fs0..fs11      # floating-point saved
ft0..ft11      # floating-point temp
```

別名系統跟整數對稱。

### 核心指令

```
flw   ft0, 0(a0)        # load single
fsw   ft0, 0(a0)        # store single
fld   ft0, 0(a0)        # load double (D)
fsd   ft0, 0(a0)        # store double (D)

fadd.s / fsub.s / fmul.s / fdiv.s / fsqrt.s     # single
fadd.d / fsub.d / fmul.d / fdiv.d / fsqrt.d     # double

fmadd.s / fmsub.s / fnmadd.s / fnmsub.s         # fused multiply-add
fcvt.s.w / fcvt.w.s                             # int ↔ float
feq.s / flt.s / fle.s                           # 回寫到整數 reg
```

### IEEE 754 完整支援

RISC-V FP 行為**嚴格符合 IEEE 754**：subnormal、NaN、infinity 全部 by-the-book。這跟某些 ARM SoC 的 "flush-to-zero" 預設不一樣。

Rounding mode 可以 per-instruction 指定（`rne`、`rtz`、`rdn`、`rup`、`rmm`）或走 `fcsr` 的動態 mode。compile 時 `-ffast-math` 才會讓 compiler 自由把 FP 重排。

### Soft-float vs Hard-float（ABI 提醒）

回顧 Ch 2：`lp64` 是整數暫存器傳浮點參數（soft-float ABI，但**硬體可以有 FPU**，只是 ABI 不用它傳參）。`lp64d` 才是「用 `fa*` 傳 double 參數」。

**常見坑**：

- `-march=rv64gc -mabi=lp64`：可以用 FP 指令做運算，但 call 浮點函式時參數塞在整數暫存器 → 需要 libc 內部 shuffle → 慢。
- `-march=rv64gc -mabi=lp64d`：call 時 `double` 直接進 `fa0` → 最快。

Linux distro 預設 `lp64d`。

## C 擴充：壓縮指令

C 擴充加入 **16-bit 版本的常用指令**。RV32GC / RV64GC binary 的平均指令寬度大約是 **3 byte**，實測比 RV64IM 小 25–30%。

看 objdump 就能分辨：

```
   101a0:   1141            addi    sp,sp,-16       ← 16-bit (C 擴充)
   101a4:   97878793        addi    a5,a5,-1672     ← 32-bit
```

硬體層面怎麼做：**`[1:0] != 11` 的指令是 16-bit，`[1:0] == 11` 才是 32-bit**。Decoder 看最低兩 bit 就知道取 2 byte 還是 4 byte。

### C 指令的限制

16-bit 裝不下 5-bit * 3 暫存器 + funct 欄位，所以 C 指令多半是**常見模式的簡縮**：

- 只能用 `x8..x15` 這 8 顆的 3-bit 編碼（`c.add`、`c.sub` 類）
- 或立即數範圍小（`c.addi`、`c.li`）
- 或 rd = rs1（in-place 運算，`c.add rd, rs2` 等於 `add rd, rd, rs2`）

常見的 C 指令：

```
c.addi   rd, imm6     → addi rd, rd, imm         (in-place)
c.mv     rd, rs2      → addi rd, rs2, 0
c.jr     rs1          → jalr x0, rs1, 0           (= jr)
c.jalr   rs1          → jalr ra, rs1, 0           (= jalr rs1)
c.ret    (C.JR ra)    → jalr x0, ra, 0            (= ret)
c.sdsp   rs, imm      → sd rs, imm(sp)            (RV64 only)
c.ldsp   rd, imm      → ld rd, imm(sp)            (RV64 only)
c.nop                 → addi x0, x0, 0            (= nop)
```

### C 擴充的 alignment 影響

C 擴充讓指令可以在 **2-byte** 邊界；沒 C 擴充的時候所有指令在 **4-byte** 邊界。這影響：

- `jal` / branch 的目標地址最低 bit（有 C → 只需 2-aligned；無 C → 必須 4-aligned）。
- `jalr` 不清最低 bit（如 Ch 3 講過）就跳到奇地址 → 觸發 `instruction-address-misaligned` exception。

**寫 custom toolchain / bootloader 時常踩這個坑**。

### C 擴充跟 relaxation

C 擴充也是 linker relaxation 的常客：

- compiler 原本生成 32-bit `addi a5, a5, 0` 給符號讀取
- linker 發現它可以換成 16-bit `c.addi a5, 0`
- 整個 code section 縮一點

累積起來一個大 binary 能省幾 MB。這是 Ch 7 of `elf_linking` 的主題。

## Zicsr 與 Zifencei：被塞進 G 的小擴充

這兩個字面上很神秘，實際很日常：

### Zicsr — CSR 存取

```
csrrw rd, csr, rs1     # atomic: rd = csr; csr = rs1
csrrs rd, csr, rs1     # atomic: rd = csr; csr = csr | rs1
csrrc rd, csr, rs1     # atomic: rd = csr; csr = csr & ~rs1
csrrwi / csrrsi / csrrci    # immediate 版
```

`csr` 是 12-bit 欄位，指向一個 **CSR 編號**。有 4096 個可能的 CSR（大多沒用到，RV spec 預留了）。常見的：

- `mstatus` / `mtvec` / `mepc` / `mcause`：M-mode trap
- `cycle` / `time` / `instret`：performance counter
- `fcsr`：浮點狀態

Ch 5 / Ch 6 會細講。沒 Zicsr 的話，你連讀 cycle counter 都辦不到。所以 G 把它算進必備。

### Zifencei — 指令 fence

```
fence.i
```

一條指令：「清空 instruction cache 的我這顆 core 視角」。當 code 剛被寫進 memory（JIT、self-modifying code），要 `fence.i` 才能保證後續執行讀到新的。

沒有這條的世界：JIT 寫 code、CPU 還在讀 old cache → 跑舊 code 或 crash。

**這條在 RVA23 profile 被標為廢棄**（2024 年後的新 CPU 會用 `cbo.inval` / `sfence` 之類的取代）。Zifencei 在現階段的 toolchain 仍是基本裝備，但它快退場了。

## 組合起來：`rv64gc` / `rv64imafdc_zicsr_zifencei`

Linux 的 baseline 需要：

```
RV64I       + 64-bit 整數
M           + 乘除
A           + atomic
F + D       + 浮點
C           + 壓縮指令
Zicsr       + CSR 存取
Zifencei    + 指令 fence
```

這些是 glibc 的假設、是 Linux kernel 的假設、是 Rust / Go / Python / Node build RISC-V 版的假設。

**2023 年後 RVA23 profile 把 baseline 往上拉**，加入 B（bit manipulation）、V（vector）、Zicbom（cache block ops）等。新的 Linux distro（Ubuntu 24.04+）可能會預設 RVA23。Ch 19 講 profile 制度。

## 一個字串看清所有 variant

寫出來的 `-march=` 字串要怎麼讀：

```
-march=rv64imafdc_zicsr_zifencei_zba_zbb_zbc_zbs
        │ │ │ │ │ │  │         │  │    │   │   │
      64-bit │ │ │ │  zicsr    │  zba  │   │   │
         base-I│ │ │            Zifencei  zbb  zbc  zbs
              M A F D                      (B 擴充的子集)
                        C (Compressed)
```

`_` 分隔小擴充；單字母的大擴充直接連寫。新寫法（2023+）也支援全部用 `_` 分隔：`rv64i_m_a_f_d_c_zba_zbb`。兩種都能被 gcc / clang 接受。

## 常見誤會

1. **「G 是一個單一擴充」**：不，G 是 `imafd_zicsr_zifencei` 的懶人縮寫。
2. **「RV64 就有 FPU」**：不。`-march=rv64i` 沒有 FPU。必須顯式加 F / D。
3. **「C 擴充要特別寫程式才有」**：不需要。compiler 預設就會產生 C 指令，只要 `-march` 含 C。
4. **「A 擴充夠了，可以取代 mutex」**：AMO 只處理簡單原子更新。複雜的 lock 還是要 mutex / LR/SC loop。AMO 是硬體 primitive 不是 locking framework。
5. **「沒 M 就不能做乘法」**：可以，但 compiler 會呼叫 `__mulsi3` 這類 libgcc 函式，軟體模擬。超慢。嵌入式真的想省 gate count 才會關 M。

## 動手練習

1. 寫一個 `int a = b * c + d / e;`，分別用 `-march=rv64i`（會呼叫 libgcc）、`rv64im`（有 mul / div）、`rv64g`（完整）編，對比。
2. 用 `__atomic_fetch_add(&x, 1, __ATOMIC_SEQ_CST)` 寫個 counter，`-march=rv64imac` 編，objdump 看是 LR/SC loop 還是 `amoadd.w.aqrl`。
3. 故意用 `-march=rv64gc -mabi=lp64` 編一支有 `double` 參數的 function，對比 `lp64d` 版本的 objdump，觀察 argument 放哪。
4. 寫 `return x + 0.1;`（double），看 compiler 怎麼 load 常數 0.1（因為沒有 FP immediate，要從 memory load）。
5. 寫一段會自我修改的 code（先寫一條指令到 buffer，再跳過去跑）。忘了 `fence.i` 看在 spike 上會如何；加 `fence.i` 確認。

## 自我檢核

- [ ] 我能拆解 `rv64gc` 字串裡每個字母的意義
- [ ] 我能解釋為什麼 M 不在 base、為什麼除零不 trap
- [ ] 我能寫一個基本的 LR/SC atomic update loop
- [ ] 我知道 C 擴充為何只用 `x8..x15` 的 3-bit 編碼
- [ ] 我能說出 Zicsr / Zifencei 各自用途以及為何被塞進 G

下一章進入 Privileged ISA — M/S/U 三個 mode、CSR、trap、delegation。這是「RISC-V 怎麼支援 OS」的核心，toolchain 工程師接觸 bootloader / kernel 時繞不開。

→ [Ch 5 Privileged ISA：M/S/U mode、CSR、trap](./05-privileged-isa.md)
