# Ch 6 — Zicsr / Zifencei / Zicond：小而關鍵的擴充

> 目標：把三個「名字很瑣碎但繞不開」的擴充講透。Zicsr 是 CSR 存取、Zifencei 是 icache 同步、Zicond 是 RISC-V 遲來的 conditional move。最後一個是 2023 年才 ratify，是近幾年 compiler 戰場的熱區。

## 為什麼把這三個放一起

這三個擴充共通點：

- **體積很小**（Zicsr 6 條、Zifencei 1 條、Zicond 2 條）
- **不在 base ISA**（純粹是 modular 設計的後果）
- **對 compiler / kernel / JIT 關鍵**（少一個就卡住）

寫 compiler backend 加 custom extension 時，**這種「小而關鍵」的 pattern 是典型**。SiFive 的 intelligence / XuanTie 的一堆 extension 都長這個樣。讀完本章你對「extension 怎麼設計、怎麼命名、怎麼文件化」會有直覺。

## Zicsr — CSR 存取的 6 條指令

前章講過 `csrrw` / `csrrs` / `csrrc` 的用途，這章把細節補齊。

### 完整指令集

```
csrrw rd, csr, rs1     # rd = csr ; csr = rs1        (Read-Write)
csrrs rd, csr, rs1     # rd = csr ; csr = csr | rs1  (Read-Set-bits)
csrrc rd, csr, rs1     # rd = csr ; csr = csr & ~rs1 (Read-Clear-bits)
csrrwi rd, csr, uimm5  # 同上，rs1 改成 5-bit 立即數
csrrsi rd, csr, uimm5
csrrci rd, csr, uimm5
```

注意立即數版**只有 5 bit unsigned**（0–31）。要寫更大值必須先 `li` 到暫存器。

### 最容易搞錯的特性：當 rd = x0 / rs1 = x0 時的行為

spec 明確規定：

- **`rd = x0`**：硬體會略過「讀 CSR」這步。有些 CSR 讀就有 side effect（例：clear-on-read 的中斷 pending），寫它時千萬注意。
- **`rs1 = x0`**（僅對 `csrrs` / `csrrc`）：硬體會略過「寫 CSR」這步。**但 `csrrw` 不管 rs1 是不是 x0 都會寫**（會寫 0 進去）。

這對 pseudo 的展開很重要：

```
csrr  rd, csr       →  csrrs rd, csr, x0       # 純讀
csrw  csr, rs       →  csrrw x0, csr, rs       # 純寫（捨棄舊值）
csrs  csr, rs       →  csrrs x0, csr, rs       # set bits
csrc  csr, rs       →  csrrc x0, csr, rs       # clear bits
```

選錯 pseudo，可能讓你「寫一個不該寫的東西」或「讀一個會清空的東西」。Linux kernel 曾經因為這類 bug 除錯很久。

### CSR 的 access permission 檢查

硬體每次 csr 指令都檢查：

1. 當前 mode 的 privilege 夠不夠（編碼 [11:10]）
2. 是否為 read-only CSR 而想寫（編碼 [9:8] = 11）
3. CSR 編號是否存在（某些硬體實作不支援）

任何一項失敗 → `illegal instruction` exception。不會 silently 忽略。**寫通用 kernel 時常需要先試讀一次特定 CSR 看硬體是否支援**（用 try-catch 風格的 trap handler）。

### Zicsr 為什麼獨立出來

一個邏輯問題：為什麼 CSR 不直接塞進 RV32I base？

答：ultra-small MCU 可以完全沒有 CSR。沒有 cycle counter、沒有 trap handler、沒有中斷 — 只是一顆執行 integer arithmetic 的 datapath。這種「純運算核心」在加速器設計裡真的存在。讓 Zicsr optional 保留這種極端場景的可能。

但實務上，**任何你能想像的真實系統都有 Zicsr**。G 把它算進必備。

## Zifencei — 一條 `fence.i`

```
fence.i
```

**只有一條指令**。作用：保證「之後的 instruction fetch 會看到之前所有的 data store」。

### 為什麼需要它

想像 JIT：

```
1. [store] 把新指令寫到 memory address X
2. [jump]  跳到 X 執行
```

在現代 CPU 上，**instruction cache 跟 data cache 是分開的**。step 1 的 store 進 data cache（最終 writeback 到 memory），但 I-cache 不知情。step 2 的 fetch 從 I-cache 讀 → 拿到舊的。結果：跑舊 code。

`fence.i` 就是強制同步這兩條路：

```
store [X], new_instruction
fence.i                      ← 保證 I-cache 讀到新的
jalr  X
```

### 它不是全能的

spec 有幾條重要的 caveat：

1. **只保證「本 hart」的 I-cache**。多 core 系統要額外 IPI 通知其他 core 也做 `fence.i`（通常用 `sbi_remote_fence_i` SBI call）。
2. **不是一般 memory fence**。它不保證 data memory 之間的排序（那是 `fence`、Ch 15 講）。
3. **執行後，你真正能「看到新指令」要從下一條 fetch 開始**。當前 PC 之前 prefetch 的 buffer 會被清掉。

### 為什麼要獨立擴充

極簡的 MCU（沒 I-cache 或 I/D cache 統一）不需要 `fence.i`。硬體天生一致。讓它 optional 是給這類系統的減法空間。

### Zifencei 的未來：可能被廢

RVA23 profile 的趨勢是把 cache 操作細分：

- `cbo.flush` / `cbo.clean` / `cbo.inval` (Zicbom)
- `cbo.zero` (Zicboz)
- 更精細的 icache 控制

**`fence.i` 太粗**（整個 icache 都清），新世代 RISC-V 會朝更細的操作走。但 2026 的時點，glibc、libgcc、JIT 還普遍用 `fence.i`，不會短期消失。

## Zicond — 遲到五年的 conditional move

2023 年才 ratify。補上 RISC-V 設計之初最具爭議的空缺。

### 背景：為什麼沒 cmov 是個問題

考慮 `max(a, b)`：

```c
int max(int a, int b) { return a > b ? a : b; }
```

有 cmov 的 ISA（x86, ARM）：

```
cmp   a, b
cmovg result, a
```

沒有 cmov 的 RISC-V（Zicond 之前）：

```
blt   b, a, .L1
mv    result, b
j     .L2
.L1:
mv    result, a
.L2:
```

多了 branch。**branch mispredict 就炸效能**。對可預測的 branch 沒差；但對資料相依、50/50 的 branch（像 sort）就是每次 miss、每次 flush pipeline。

早期 RISC-V 設計師的立場：現代 CPU 有 branch predictor、branch 夠便宜、不需要 cmov。**實務上被打臉** — SPEC / crypto / sorting 的 micro-bench 顯示某些 kernel 少 cmov 掉 20–40% 效能。

### Zicond 的兩條指令

```
czero.eqz  rd, rs1, rs2    # rd = (rs2 == 0) ? 0 : rs1
czero.nez  rd, rs1, rs2    # rd = (rs2 != 0) ? 0 : rs1
```

設計極簡：**條件成立則 return 0，否則 return 原值**。光這兩條夠不夠做 cmov？用 `or` 組合：

```
# result = (cond != 0) ? a : b
czero.eqz  t0, a, cond    # t0 = (cond == 0) ? 0 : a
czero.nez  t1, b, cond    # t1 = (cond != 0) ? 0 : b
or         result, t0, t1
```

cond 為 0 時：`t0 = 0, t1 = b, or = b`；
cond 非 0 時：`t0 = a, t1 = 0, or = a`。

三條指令做一次 cmov。比 branch 版長一點，但**沒有 branch mispredict 的風險**。

### compiler 策略

LLVM 17+ / GCC 13+ 加入 Zicond 支援。compile 時會根據：

- branch 是否 predictable
- 分支兩側的運算量（只要越過 load 就不該 cmov）
- `-march` 是否含 Zicond

自動決定要不要用 Zicond。**當前預設還不積極**（因為多數 hardware 還沒支援 Zicond 或有 branch predictor 夠好），但 SiFive 7 系列、XuanTie C910+ 等都在加。

### czero 為什麼設計成「條件成立歸零」

初看很奇怪。直接定義 `cmov rd, cond, src` 不是更直觀？設計理由：

1. **跟現有 RISC-V pipeline 相容**：`czero.eqz` 其實可以看成 `AND rd, rs1, (rs2 ? -1 : 0)` — 用既有的 ALU 加一小塊 mask 電路就能做到，不需要獨立 cmov unit。
2. **`or` 組合很乾淨**：兩個 `czero` 的結果互斥（必有一邊是 0），`or` 就得到 cmov。正交性好。
3. **硬體成本低**：只改 ALU 的 mux，不改 dataflow、不加 write-back。

是很 RISC-V 的設計哲學：**把操作拆成小原語，讓 compiler 組合**。

### 跟 B 擴充的關係

B 擴充（Ch 7）的 `max` / `min` / `max` 可以直接一條指令做 `max(a, b)`。如果你的目標機器有 Zbb，compiler 會優先用 `max` 而不是 `czero` + `or`。

但 **`max` 只處理 max/min；一般 ternary `x ? a : b` 還是需要 Zicond**。兩者互補。

## 三個擴充的 compile 字串

```
-march=rv64gc_zicond                     # 只加 Zicond
-march=rv64gc_zbb_zicond                 # B (subset) + Zicond
-march=rv64gc_zba_zbb_zbs_zicond         # 現代 RVA23 baseline 的近似
```

**注意用底線**：Zicond / Zbb / Zfh 等**多字母**擴充必須用 `_` 分隔，`rv64gczicond` 會被解析成「rv64 + gc + z + i + c + o + n + d」一堆錯字。

## 面試常見問題

看過幾次的題：

1. **「RISC-V 沒有 cmov 怎麼做 `abs(x)`？」**
   - 傳統版：`blt x, x0, .Lneg; j .Ldone; .Lneg: neg x, x; .Ldone:`
   - Zbb 版：`abs  rd, rs`（Ch 7）
   - Zicond 版：`srai t0, x, 31; xor t1, x, t0; sub rd, t1, t0`（branchless 標準寫法 — 不用 Zicond 也能 branchless）

2. **「`fence.i` 對多核怎麼辦？」**
   - 答：SBI call 請其他 hart 也執行 `fence.i`；Linux 的 `flush_icache_all` 就是這樣做。

3. **「CSR 原子性怎麼證？」**
   - spec 保證 csrr* 一條指令內完成讀+寫。硬體上通常是 pipeline stall + 專用 CSR file port。

## 常見坑

1. **忘了 Zicsr 寫進 `-march`**：極少見，但某些 minimal `rv64i` 嵌入式 toolchain 預設不含。結果連 `csrr cycle` 都組不起來。
2. **用 `csrrw` 讀 CSR 但 rs1 沒清零**：`csrrw rd, csr, rs1` 會把 rs1 寫進 CSR。想純讀要用 `csrrs rd, csr, x0`（= pseudo `csrr`）。
3. **以為 `fence.i` 夠了所以不 flush D-cache**：在某些 coherent-I/D 硬體上沒事，但 non-coherent 系統要先 `fence` + `cbo.flush` 把 data 寫回 memory，`fence.i` 才能讓 I-cache 看到。
4. **Zicond 以為是 x86 cmov 一對一替換**：不是，需要 `czero + czero + or` 三條。compiler 會組，手寫要小心。
5. **命名把大小寫搞錯**：spec 寫 `Zicsr`，小寫 `z` 開頭；`-march=rv64gc_Zicsr` 有些 toolchain 不認。統一小寫。

## 動手練習

1. 寫 `csrr a0, cycle; ret`，用 spike 跑，看 cycle 是不是在增加。
2. 故意用 `csrrw x0, cycle, x0`（企圖清 cycle counter）：大部分硬體 cycle 是 read-only，會 trap。用 spike 驗證。
3. 手寫一個 `fence.i` 的 self-modifying code 範例：先寫一條 `addi a0, x0, 42; ret` 到 buffer，用 `fence.i` 同步後跳過去跑，驗證返回值 42。
4. 用 `-march=rv64gc_zicond -O2` 編 `int max(int a, int b) { return a > b ? a : b; }`。注意 gcc 會優先用 `max`（如果有 Zbb）— 所以你要關 Zbb（`-march=rv64gc_zicond`）才能看 czero。
5. 閱讀 Linux 的 `arch/riscv/include/asm/barrier.h`，找出 `smp_mb` / `smp_rmb` / `smp_wmb` 各用哪種 fence，以及 `fence.i` 出現在哪。

## 自我檢核

- [ ] 我能說出 `csrr` vs `csrw` vs `csrs` / `csrc` 的差別
- [ ] 我能解釋 `fence.i` 做什麼、為什麼有 I/D cache 分離就需要它
- [ ] 我能用 Zicond 的兩條指令組合出 cmov
- [ ] 我知道 Zicond 跟 Zbb 的 max/min 互補關係
- [ ] 我看到 csrr* 指令能判斷它要什麼 privilege、會不會 trap

下一章進 B 擴充（bit manipulation） — SiFive 面試會直接問的「請用 Zbs 寫 popcount」。

→ [Ch 7 B 擴充：bit manipulation 全解](./07-bitmanip-extension.md)
