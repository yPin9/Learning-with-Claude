# Ch 32 — 64 位元 CSR 行為：sstatus.SXL/UXL、mstatus、Wide Performance Counter

> 目標：理解 RV64 新增的 mstatus/sstatus 欄位含義；能正確讀取 64-bit cycle counter；知道 SXL/UXL 這兩個欄位存在的原因。

---

## 32.1 RV32 vs RV64 的 CSR 寬度差異

在 RV32I，CSR 是 32-bit，每個 CSR 存取一次讀/寫全部 32 bit。

在 RV64I，CSR 是 64-bit。同一個 CSR（例如 mstatus）在 RV64 下有更多欄位，因為 64-bit 的空間放得下更多控制位元。

某些 RV32I 需要兩個 CSR 的功能（如 mcycleh / mcycle）在 RV64I 合併成一個 64-bit CSR。

---

## 32.2 mstatus / sstatus 在 RV64 下的新欄位

```
RV64 mstatus（64-bit）佈局（關鍵欄位）：

bits [63:38] Reserved
bits [37:36] SXL  - Supervisor XLEN
bits [35:34] UXL  - User XLEN
bits [33]    Reserved
bits [32]    SD   - FS/XS/VS dirty summary
... （bits [31:0] 與 RV32 的 mstatus 相同）
```

RV32 mstatus 沒有 SXL 和 UXL（它們需要 64-bit 空間才放得下）。

---

## 32.3 SXL 和 UXL 欄位

**SXL**（Supervisor XLEN）和 **UXL**（User XLEN）控制不同 privilege level 的有效 XLEN。

```
欄位值    XLEN
------    ----
01        32
10        64
11        128（保留）
```

**為什麼需要這兩個欄位**：RISC-V 設計了一個「可以在同一個 64-bit 硬體上跑 32-bit 系統呼叫 ABI」的路徑。例如：

- Machine mode（M-mode）：XLEN=64（由 misa.MXL 決定）
- Supervisor mode（S-mode）：XLEN 由 mstatus.SXL 控制
- User mode（U-mode）：XLEN 由 mstatus.UXL 控制

實際上，Linux 在 RV64 下把 SXL=10（64-bit）和 UXL=10（64-bit）。這兩個欄位的主要用途是理論上的相容性，真實 OS 幾乎都設成與 M-mode 一致。

**sstatus.UXL**：S-mode 可見的 sstatus 包含 UXL（控制 U-mode 的 XLEN），但不包含 SXL（S-mode 的 XLEN 由 M-mode 的 mstatus.SXL 控制，S-mode 看不到）。

---

## 32.4 misa CSR 的 MXL 欄位

```
misa（Machine ISA）CSR：
  bits [63:62]  MXL  - Machine XLEN（RV64 固定為 10b = 64-bit）
  bits [61:26]  Extensions（每個 bit 對應一個 extension letter）
  bits [25:0]   Extensions（A=0, B=1, C=2, ..., Z=25）

範例：RV64IMAFDC
  MXL     = 10（64-bit）
  bit [8]  = 1（I = base integer）
  bit [12] = 1（M = multiply/divide）
  bit [0]  = 1（A = atomic）
  bit [5]  = 1（F = single-precision float）
  bit [3]  = 1（D = double-precision float）
  bit [2]  = 1（C = compressed）
```

讀 misa 確認硬體支援：

```c
static inline uint64_t read_misa(void) {
    uint64_t val;
    __asm__ volatile ("csrr %0, misa" : "=r"(val));
    return val;
}

// 檢查是否支援 'A' extension（bit 0）
if (read_misa() & (1UL << 0)) {
    // 支援 atomic instructions
}
```

---

## 32.5 Performance Counter：RV64 的優勢

在 RV32I，`cycle` 和 `instret` 是 32-bit CSR，讀滿了就 overflow。要讀 64-bit 值，需要同時讀 `cycle`（低 32-bit）和 `cycleh`（高 32-bit），並處理 overflow：

```c
// RV32 讀 64-bit cycle counter（麻煩）
uint64_t read_cycle_rv32(void) {
    uint32_t lo, hi, hi2;
    do {
        hi  = csr_read(cycleh);
        lo  = csr_read(cycle);
        hi2 = csr_read(cycleh);
    } while (hi != hi2);   // 如果讀 lo 的時候 hi 進位了，重讀
    return ((uint64_t)hi << 32) | lo;
}
```

在 RV64I，`cycle`、`time`、`instret` 都是 64-bit CSR，直接讀：

```c
// RV64 讀 64-bit cycle counter（簡單）
static inline uint64_t read_cycle(void) {
    uint64_t val;
    __asm__ volatile ("csrr %0, cycle" : "=r"(val));
    return val;
}

// 計算 elapsed cycles
uint64_t t0 = read_cycle();
do_something();
uint64_t elapsed = read_cycle() - t0;
```

---

## 32.6 實際例子：用 cycle counter 計時

```c
#include <stdint.h>

static inline uint64_t rdcycle(void) {
    uint64_t c;
    __asm__ volatile ("csrr %0, cycle" : "=r"(c));
    return c;
}

static inline uint64_t rdtime(void) {
    uint64_t t;
    __asm__ volatile ("csrr %0, time" : "=r"(t));
    return t;
}

// 測量一段程式碼的 cycle count
#define BENCH(code) do { \
    uint64_t _t0 = rdcycle(); \
    code; \
    uint64_t _elapsed = rdcycle() - _t0; \
    uart_print_u64(_elapsed); \
} while (0)
```

注意：`cycle` 是 M-mode counter，U-mode 和 S-mode 要讀到它需要 M-mode 開放（mcounteren.CY = 1）。Linux 通常已開放。

---

## 32.7 CSR Shadow Registers

某些 CSR 在不同 privilege level 有獨立的「影子（shadow）」：

```
sstatus 不是獨立的 CSR，它是 mstatus 的子集（部分欄位的視角）
  S-mode 讀 sstatus → 其實是讀 mstatus，但只看得到 S-mode 相關的欄位
  S-mode 寫 sstatus → 也只能寫 mstatus 裡 S-mode 被允許的欄位

sie 和 mie 的關係類似：
  sie 是 mie 的子集（只包含 S-mode interrupt enable bits）

這不是真的有兩份儲存，而是硬體在 S-mode 存取這些 CSR 時，
自動 mask 掉 M-mode only 的欄位。
```

對你來說的實際影響：S-mode 讀 `sstatus.MPP` 會讀到 0（M-mode 欄位，S-mode 看不到）。讀 `sstatus.SPP` 才能看到。

---

## 32.8 hpmcounter：Hardware Performance Monitor

除了 cycle/instret，RV64 支援 29 個可程式化的 hardware performance counters（hpmcounter3–31）：

```
CSR 名稱         位址      描述
---------        ------    ------
hpmcounter3      0xC03     programmable counter 0
hpmcounter4      0xC04     programmable counter 1
...
hpmcounter31     0xC1F     programmable counter 28

mhpmevent3       0x323     選擇 counter 3 計什麼事件
（branch mispredictions, cache misses, etc.）
```

具體能計什麼事件是 implementation-defined（每個 CPU 廠商自己定義）。在 Linux 上用 `perf` 工具可以直接使用這些 counter。

---

## 自我檢核

- [ ] 能說出 mstatus.SXL 和 mstatus.UXL 的含義（控制 S/U-mode 的 XLEN）
- [ ] 知道 RV64 的 cycle counter 是幾 bit（64-bit，直接讀）
- [ ] 能寫一個用 inline asm 讀 `cycle` CSR 的函式
- [ ] 知道 sstatus 和 mstatus 的關係（shadow/subset）
- [ ] 能說出讀 misa 可以確認哪些資訊（MXL = XLEN，extensions）

→ [Ch 33 — Trap 完整流程（RV64 視角）](33-trap-rv64.md)
