# Ch 12 — TLS Model：LE / IE / GD / LD

> 目標：理解 `__thread` / `thread_local` / `static __thread` 背後的四種 TLS access model、各自的 relocation type、以及 compiler 如何根據 `-fPIC` 與 visibility 自動選擇。這章結束你能 debug 任何 TLS 相關 link error。

## TLS 是什麼

Thread-Local Storage：每個 thread 有自己一份的變數。C/C++ 的寫法：

```c
__thread int x;          // GNU extension
thread_local int y;      // C11 / C++11
```

每個 thread 的 `x` 是獨立的。Thread 切換時 runtime 自動指向對的 copy。

runtime 機制：

- 每 thread 有個 **TCB (Thread Control Block)**
- 某個 register 指向當前 thread 的 TCB
- TLS 變數位置 = `TCB + some_offset`

## RISC-V 的 tp

RISC-V ABI 指定 **`tp`（x4 / thread pointer）** 作為 TCB 基底：

```
每個 thread:
  tp → TCB
       ↓
       static TLS block（所有 static 宣告的 __thread 變數）
       dynamic TLS（dlopen 後加的）
```

glibc / musl 的 `pthread_create` 會為每個 thread 配 TCB、設定 `tp`、copy TLS template。

## 四種 TLS access model

不同情境 TLS variable 的 access 複雜度不同。最高效到最通用排列：

```
1. Local-Exec (LE)      最快，限 executable 內的 static TLS
2. Initial-Exec (IE)     次快，load 時可 resolve 的 TLS
3. General-Dynamic (GD)  最通用、最慢，跨 .so 的 TLS
4. Local-Dynamic (LD)    同 .so 內多變數的優化版 GD
```

compiler / linker 選哪個取決於：

- 變數在哪（executable vs shared library）
- visibility（hidden 或 default）
- `-fPIC` / `-fPIE` flag

## Local-Exec (LE)：最簡單最快

**條件**：變數在 executable 內（不在 `.so`）、executable 不是 PIE、access 來自 executable。

**生成的 code**：

```asm
# 讀 __thread int x (LE mode)
lui  t0, %tprel_hi(x)
add  t0, t0, tp, %tprel_add(x)   # 特殊 relocation for link-time fold
addi a0, t0, %tprel_lo(x)
lw   a0, 0(a0)
```

簡化後（link 完成）：

```asm
# x 在 static TLS block offset +40
lw   a0, 40(tp)            # 一條指令！
```

LE 的美：**link 時 offset 已知**，access 只要 `lw offset(tp)`。最快。

**限制**：executable 不是 PIE、變數定義在 executable 內。現代 distro 預設 PIE → LE 很少見。

### 相關 relocation

```
R_RISCV_TPREL_HI20
R_RISCV_TPREL_LO12_I
R_RISCV_TPREL_LO12_S
R_RISCV_TPREL_ADD
```

## Initial-Exec (IE)：次快

**條件**：變數可以在 executable 或 `.so` 中，但 **load 時就能確定 offset**（不是 `dlopen` 後加的）。

**為什麼需要**：PIE executable 不能用 LE（offset 要 runtime 才知）。`.so` 的 TLS offset 也要 dynamic linker 算。

**生成的 code**：

```asm
# 讀 __thread int x (IE mode)
auipc a0, %tls_ie_pcrel_hi(x)
ld    a0, %pcrel_lo(1b)(a0)      # a0 = TLS offset (從 GOT 讀)
add   a0, a0, tp                  # tp + offset
lw    a0, 0(a0)                   # dereference
```

多一次 GOT load。**比 LE 多一個 memory access**。

### 關鍵相關 relocation

```
R_RISCV_TLS_GOT_HI20            # GOT slot for IE
R_RISCV_PCREL_LO12_I            # pair with above
R_RISCV_TLS_TPREL32 / 64        # GOT slot 的內容（dynamic linker 填）
```

GOT 裡存的是「該 thread variable 在 static TLS block 的 offset」，dynamic linker load 時填。

## General-Dynamic (GD)：最通用

**條件**：跨 shared library 的 TLS、或 `dlopen` 動態 load 的 `.so` 裡的 TLS。

**為什麼需要**：如果 `.so` 是 `dlopen` 動態 load，它的 TLS offset 只能執行時算。整個流程需要呼叫 `__tls_get_addr()`。

**生成的 code**：

```asm
# 讀 __thread int x (GD mode)
1:  auipc a0, %tls_gd_pcrel_hi(x)
    addi  a0, a0, %pcrel_lo(1b)     # a0 = &tls_index
    call  __tls_get_addr             # 返回 tls block base + offset
    lw    a0, 0(a0)
```

**要 call 一個 library function**！比 LE 慢幾十 cycle。

`__tls_get_addr(tls_index*)` 的內部做：

1. 接收一個 `tls_index` struct（包含 module id + offset）
2. 從當前 thread 的 dtv (Dynamic Thread Vector) 查對應 module 的 TLS block
3. 返回 `block_base + offset`

### 相關 relocation

```
R_RISCV_TLS_GD_HI20
R_RISCV_TLS_GD_LO12
R_RISCV_PCREL_LO12_I
R_RISCV_CALL (for __tls_get_addr)
```

還有 `.rela.dyn` 裡會有：

```
R_RISCV_TLS_DTPMOD32/64      # module id
R_RISCV_TLS_DTPREL32/64      # offset in module's TLS block
```

dynamic linker 填這些。

## Local-Dynamic (LD)：GD 的優化版

**條件**：同 `.so` 內多個 TLS variable。GD 每個變數都 call `__tls_get_addr` —— 重複 call 很浪費。LD 優化：**每 `.so` 只 call 一次 `__tls_get_addr`，然後共用 base**。

```asm
# 跟 GD 類似，但第一個變數後共用 base
call  __tls_get_addr           # 得到 base
# x 在 base + offset_x
addi  t0, a0, %dtprel_lo(x)
lw    x_val, 0(t0)
# y 在 base + offset_y
addi  t1, a0, %dtprel_lo(y)
lw    y_val, 0(t1)
```

一次呼叫、多次 access。

### 相關 relocation

```
R_RISCV_TLS_LDM_HI20         # LDM = local-dynamic module
R_RISCV_TLS_LDM_LO12
R_RISCV_TLS_DTPREL_HI20      # offset within module
R_RISCV_TLS_DTPREL_LO12
```

compiler 很少主動產 LD —— 多半用 GD。`-ftls-model=local-dynamic` 強制用。

## compiler 如何選 model

預設：`-ftls-model=<model>` 或 per-variable attribute 決定。不指定時：

```
executable 不是 PIE, 變數在 executable → LE
executable 是 PIE, 變數在 executable (hidden 或 static) → IE
其他（.so 內、外部 .so 的變數）→ GD
```

手動控：

```c
__attribute__((tls_model("initial-exec"))) __thread int x;
```

`static __thread int x;` 因為 hidden，可以用 LE（executable 場景）或 IE（PIE / .so 場景）。

## 一個真實例子

```c
// a.c
#include <stdio.h>
__thread int global_tls;            // 預期用 GD/IE
static __thread int static_tls;     // 預期用 LE/IE

void foo(void) {
    global_tls = 1;
    static_tls = 2;
    printf("%d %d\n", global_tls, static_tls);
}
```

`gcc -fPIE -pie -O2 -S a.c -o a.s`，看 `foo` 的 asm：

```asm
foo:
    # global_tls access (IE mode, 因為 PIE)
    auipc a5, %tls_ie_pcrel_hi(global_tls)
    ld    a5, %pcrel_lo(1b)(a5)
    add   a5, a5, tp
    li    a4, 1
    sw    a4, 0(a5)

    # static_tls access (LE mode, 因為 hidden)
    lui   a4, %tprel_hi(static_tls)
    add   a4, a4, tp, %tprel_add(static_tls)
    li    a3, 2
    sw    a3, %tprel_lo(static_tls)(a4)
    ...
```

`global_tls` 走 IE（3 條），`static_tls` 走 LE（3 條也類似，但 offset 是 link-time 填）。

## TLS relaxation

**dynamic linker 會做 TLS relaxation**：

- 如果 executable 其實不是 PIE（但 compiler 保守生 GD/IE）→ relax 成 LE
- 如果 `.so` 的 TLS 變數 ends up 是 local → relax GD → LD

LLD 跟 GNU LD 都做。你寫 `.c` 不用管，但 debug 時要知道「同樣的 C code 不同 link 可能生不同 final code」。

## `.tdata` 跟 `.tbss`

TLS 變數的初值存放：

- **`.tdata`**：有初值的 TLS 變數（如 `__thread int x = 5`）
- **`.tbss`**：沒初值的（如 `__thread int x`）

這些是 **TLS template**。每個 thread create 時從 template copy 一份。`readelf -l` 裡會看到 `PT_TLS` segment，內容就是 `.tdata + .tbss`。

```bash
readelf -S hello | grep -E "tdata|tbss"
readelf -l hello | grep -A2 "PT_TLS"
```

## `__tls_get_addr` 的效能

GD model 每次 access 都走這個 function。glibc 版本：

```
glibc: 約 40-60 cycle
musl:  約 20-30 cycle
```

比 LE 的 1 cycle 慢幾十倍。**熱 code 千萬避免 GD TLS access**。用 `__attribute__((tls_model("initial-exec")))` 或 cache 進 local variable。

## Debug TLS 問題的工具

1. **`readelf -r | grep TLS`**：看有哪些 TLS relocation
2. **objdump -d | grep __tls_get_addr**：看哪些 code 走 GD path
3. **LD_DEBUG=reloc**：runtime 看 dynamic linker 處理 TLS relocation 的過程
4. **perf**：看 `__tls_get_addr` 是不是 hot function

## `dlopen` + TLS 的坑

`dlopen` 後新 load 的 `.so` 裡的 TLS 只能用 GD。而且：

- **glibc 早期 version**（< 2.27）不支援 `dlopen` 的 TLS，直接 segfault
- glibc 新版支援，但第一次 access 會觸發 dtv resize，慢

嵌入式 musl 更嚴 —— 有些版本完全不支援 dlopen TLS。

## 常見誤會

1. **「__thread 是 C 標準」**：不。`__thread` 是 GNU extension；C11 標準 keyword 是 `thread_local`（在 `<threads.h>`）。兩個等價。
2. **「TLS 就是 __thread」**：TLS 是概念，`__thread` 只是 GNU 寫法。
3. **「TLS access 跟普通變數差不多快」**：差很多。LE 接近普通 access，GD 慢幾十倍。
4. **「`.so` 不能用 LE」**：對。`.so` 只能 IE / GD / LD。
5. **「`tp` 可以自己改」**：可以但會破壞 libc 假設。glibc 使用 tp 做 errno / pthread 很多事。除非你寫 runtime 否則別動。

## 動手練習

1. 寫三個 TLS 變數（executable static、executable extern、.so 內），看四種 model 被用哪個。
2. 用 `-ftls-model=global-dynamic` 強制 compile，跟預設版對比 objdump。
3. benchmark：hot loop access TLS vs access 普通 global，量時間差。
4. 讀 glibc 的 `__tls_get_addr` 實作（約 100 行）。
5. 用 `dlopen("libtls.so")` load 一個含 TLS 的 library，處理看看。

## 自我檢核

- [ ] 我能解釋 LE / IE / GD / LD 四種 model 的適用場景
- [ ] 我能看 objdump 辨認是哪個 TLS model
- [ ] 我知道 `__tls_get_addr` 的角色與 performance 影響
- [ ] 我能用 visibility / `-fPIE` 控制 TLS model 選擇
- [ ] 我能 debug `.tdata` / `.tbss` / `PT_TLS` 相關問題

下一章深入 lazy binding 的機制 —— `_dl_runtime_resolve` 實作、RELRO 防禦、`LD_BIND_NOW`。

→ [Ch 13 Lazy Binding 與 _dl_runtime_resolve](./13-lazy-binding.md)
