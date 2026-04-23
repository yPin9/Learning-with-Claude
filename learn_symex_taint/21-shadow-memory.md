# Ch 21 — Granularity 與 shadow memory 實作

> 目標：看清 DTA 工具 memory 層面的實作。granularity 與 shadow layout 是效能最核心的決定。講完你應該能自己設計一個 byte-level shadow memory。

## Granularity：每多少單位一個 taint

選擇：

| 單位 | 含義 | 工具例子 |
|------|------|----------|
| **bit** | 每個 bit 一個 taint label | PANDA、某些 research 原型 |
| **byte** | 每 byte 一個 | libdft、Triton、AddressSanitizer |
| **word/qword** | 每 4/8 byte 一個 | 部分簡化 research |
| **object** | 每個 malloc 一個 | 不做 DTA 的 memory sanitizer |

### bit granularity

- 精度最高：能區分 "這個 byte 的低 4 bit 是 tainted、高 4 bit 不是"
- 成本最高：shadow 是 1:1 大小（32 GB RAM target → 32 GB shadow）
- 通常用於 research，不實務

### byte granularity（主流）

- 精度足夠 — 程式 semantic 多半 byte-level
- shadow 是 1:8 大小（1 bit 一個 byte）或 1:1 大小（byte label）
- 主流工具的選擇

### word granularity

- 粗糙：byte 1 tainted 會污染同 word 的 byte 2, 3, 4
- 快、shadow 小
- 對 x86-64 大部分 operation 合理（多半是 qword 運算）
- 部分 research tool 用

### 選什麼？

預設 byte。真的需要更精細（研究 bit-level side channel 等）才上 bit。

## Shadow memory：怎麼 allocate

concrete memory 地址是 `0x0000000000000000` ~ `0xFFFFFFFFFFFFFFFF`（64-bit）。
shadow memory 要給每個 byte 配個 shadow byte。怎麼存？

### 方式 1：full shadow map

```
shadow[addr] = taint_byte
```

用 hashtable 或 plain array。全 VM 空間映射。

問題：**內存爆**。`2^48` 可訪問 virtual address × 1 byte shadow = 256 TB。不可行。

### 方式 2：只記 allocated 區域

只對實際 allocation 的地方配 shadow。用 sparse structure（hash、tree）。

問題：每次 load/store 要查 hashtable — 慢。

### 方式 3：compressed shadow（AddressSanitizer pattern）

Scan shadow 的經典 layout（ASan 用的）：

```
shadow_addr = (addr >> 3) + SHADOW_OFFSET
```

- 每 8 byte concrete 配 1 byte shadow
- shadow map 佔 1/8 VM
- 一個固定 offset，簡單算術
- ASan 的 shadow_offset = 0x7fff8000（linux x86-64）

這叫 **direct mapping**。速度最快 — 一次 shift + add。

DTA 工具採用：Triton 類似、libdft 有類似 optimization。

### 方式 4：segmented shadow

只有「會被追蹤的 segment」有 shadow：
- stack、heap、data section、bss：有 shadow
- mmap 的 rw 區：有 shadow
- 其他 rx / rodata：無 shadow

好處：shadow 總大小可控。
壞處：每次 access 要先查 segment table。

## libdft 的 shadow 實作

libdft（Kemerlis et al., EuroSys 2012）是 Pin-based DTA。它的 shadow 實作是 **bitmap**：

```c
// 每個 byte 一個 bit (1 bit = tainted, 0 bit = clean)
// 整個 user space 4 GB (x86-32) 配 512 MB bitmap

uint8_t shadow_bitmap[1 << 29];   // 512 MB

inline bool is_tainted(uintptr_t addr) {
    return (shadow_bitmap[addr >> 3] >> (addr & 7)) & 1;
}

inline void set_tainted(uintptr_t addr, bool v) {
    if (v) shadow_bitmap[addr >> 3] |= (1 << (addr & 7));
    else   shadow_bitmap[addr >> 3] &= ~(1 << (addr & 7));
}
```

簡單粗暴。每個 memory access 加兩三條 instruction 的 overhead。

升級到 byte-label（支援多 label）：

```c
uint8_t shadow[1 << 32];    // 1:1 對映，x86-32 user space
```

x86-64 就不能這樣做了（太大）。要改用 direct mapping 或分段。

## register 的 shadow

register 數量有限（x86-64 ~16 個通用 + XMM/YMM）。shadow 就是個 array：

```c
struct shadow_regs {
    uint64_t rax_taint;   // 8 byte, 每 bit 代表 1 byte (rax 的 byte i)
    uint64_t rbx_taint;
    // ...
};
```

或用 taint type 更複雜：

```c
struct {
    uint8_t taint_bytes[8];   // 一個 byte 代表 rax 的 byte i 的 taint label
} rax;
```

register R/W 時讀寫對應的 shadow 記錄。

## Instrumentation：每個 instruction 的 shadow 更新

這是 DTA 效能瓶頸。每個 `mov`、`add`、`cmp` 都要對應 shadow update。

### Pin / DynamoRIO 怎麼做

每個 instruction insertion point 前後插 call：

```
; 原本
mov rax, [rbx]

; instrumented
call shadow_update_memory_load(rbx, rax)  ; 更新 rax 的 shadow
mov rax, [rbx]
```

這個 call 成本很大（save/restore caller-saved register、跨函式 jmp），每條 instruction 幾百 cycle 都可能。

### Inline 優化

熱門工具會把常見的 shadow update 代碼 **inline 進 target**，避開 call：

```asm
; 原 instruction: mov rax, [rbx]
; inline instrumentation:
mov rcx, rbx
shr rcx, 3                    ; shadow offset
mov rdx, [shadow_base + rcx]  ; 讀 shadow byte
mov [rax_shadow], rdx         ; 寫 rax 的 shadow
mov rax, [rbx]                ; 原 instruction
```

節省 call overhead。libdft 這樣做的時候 overhead 從 10× 降到 3–5×。

### SIMD fast path

register 之間的 shadow copy 用 SIMD：

```asm
; 8 個 byte 的 taint 一次 copy
movdqa xmm0, [reg1_shadow]
movdqa [reg2_shadow], xmm0
```

libdft 跟 Triton 都有這類 optimization。

## 為什麼 DTA 比 symex 快

DTA 每條 instruction 做的事：
- 讀少數 shadow byte
- 做簡單 bit op（OR）
- 寫 shadow byte

成本：**幾十 ns**。1 次 instruction ~= 10× 原生 slowdown。

Symex 每條 instruction：
- 更新 SMT formula（AST node 幾十 byte）
- possibly fork state、clone memory
- branch 時 SMT query（us 到 ms）

成本：**us 級或更糟**。100× 原生 slowdown起跳。

這個速度差是 DTA 跟 symex 現實分工的主因。

## Page-based optimization

觀察：程式跑一段時間後，許多 page 整頁都是 untainted 或整頁都是 tainted。對這類 page，不用 per-byte 檢查。

**paging**：每個 page（4 KB）有個 state：
- `UNTAINTED_PAGE`：整頁乾淨，skip 所有 update
- `TAINTED_PAGE`：整頁髒
- `MIXED_PAGE`：需要 per-byte shadow

只有 mixed page 走 slow path。大 speedup。

同樣的技術用在 **memory sanitizer**（MSan）、**ASan**。

## Stack vs Heap 的考量

stack：每個 function call 快速 allocate / deallocate。shadow 要跟隨 stack 移動。

常見做法：
- function entry 把 stack frame 的 shadow 清 0（之前那塊 memory 的 taint 失效）
- function return 一樣
- 對 stack-heavy program 這個成本不小

heap：`malloc(100)` 後 100 byte shadow 初始化為 0；`free(p)` 後保留或清。清的話能抓 UAF 的 taint-use。

工具有 redzone 機制（allocation 前後留 tainted 的 red byte）— 這招 ASan 發揚光大。DTA 可共用。

## 多 thread 下的 shadow

register 的 shadow：**per-thread**（每 thread 獨立 register shadow）。直覺。

memory 的 shadow：**shared**（多 thread 同讀同一塊 memory）。要保 atomic：

```c
// 多 thread 下更新同一 byte
__atomic_or_fetch(&shadow[addr >> 3], 1 << (addr & 7), __ATOMIC_RELAXED);
```

每個 memory instruction 都這樣做 — 效能又降一截。實務 DTA 在 multi-thread target 上慢 10–100× 原生是常態。

## 實例：一段 DTA instrumentation 的 "before / after"

原 C（編譯完的 assembly）：

```asm
; int y = x + 5;
mov eax, [rbp-0x10]
add eax, 5
mov [rbp-0x14], eax
```

libdft-style instrumented：

```asm
; 讀 [rbp-0x10] 的 shadow
lea rcx, [rbp-0x10]
shr rcx, 3
mov dl, [shadow_base + rcx]     ; shadow byte
shl dl, ... ; etc. 抽出對應 bit

; 原 load
mov eax, [rbp-0x10]

; register x 的 shadow：eax_shadow = shadow_byte 對應 bit

; add eax, 5 — 對 register x 的 shadow 沒影響（5 是 constant）

; 原 add
add eax, 5

; 寫 [rbp-0x14]，把 shadow 傳過去
lea rcx, [rbp-0x14]
shr rcx, 3
or byte [shadow_base + rcx], eax_shadow_bit

; 原 store
mov [rbp-0x14], eax
```

原本 3 條 instruction 的 sequence 變成 10+ 條。每個 memory access 有對應的 shadow update。這就是 DTA 10× slowdown 的來源。

## 心法

Shadow memory 是 DTA 工具的**效能核心**。粗略 rule：

- **scan 速度** ≈ shadow lookup cost × instruction count
- shadow lookup cost 1 ns → 10× overhead
- shadow lookup cost 10 ns → 100× overhead

想讓你的 DTA 工具快：
1. Direct mapping（ASan pattern）
2. Inline shadow update
3. Page-level fast path
4. SIMD copy

想讓你的 DTA 工具精確：
1. Byte granularity（別走 word）
2. Multi-label 支援
3. Implicit flow 可選
4. Register-level shadow 到 sub-register

這兩組 goals 在不同維度，不衝突但要分清哪個先。

## 自我檢核

- [ ] 解釋 bit / byte / word / object granularity 的取捨
- [ ] 畫出 direct mapping shadow 的公式（`shadow_addr = (addr >> 3) + offset`）
- [ ] 講得出 libdft 的 bitmap-based shadow 與 ASan-style direct shadow 的差
- [ ] 知道 instrumentation 的 inline 跟 call-out 兩種實作
- [ ] 理解 multi-thread 下 memory shadow 要 atomic 的代價

下一章進 **DBI 工具** — Pin、DynamoRIO、Frida、QEMU TCG 各是什麼、什麼 DTA 工具用什麼。

→ [Ch 22 — DBI 工具比較：Pin / DynamoRIO / Frida / QEMU TCG](./22-dbi-tools.md)
