# Ch 5 — eBPF Verifier：安全性證明的工作原理

> **目標**：理解 eBPF verifier 如何用靜態分析（abstract interpretation）證明一個 BPF 程式是安全的，能讀懂 verifier 的拒絕訊息，知道哪些 code pattern 會被拒絕以及為什麼。

## 為什麼需要這個？

eBPF 的核心賣點是：你的程式在 kernel 裡執行，但 kernel 不會因此 crash。這個承諾由 verifier 兌現。

如果沒有 verifier，eBPF 就只是「允許任意 userspace code 在 ring 0 執行」——和 kernel module 一樣危險，但沒有 kernel module 的安全審查流程。

verifier 是 eBPF 的守門人，也是你最常碰到的障礙。你的 BPF 程式被 reject 90% 的原因是 verifier 認為它不安全，而且 verifier 的錯誤訊息有時候很難讀懂。讀完這章，你就能讀懂它在說什麼。

## 先建立直覺：verifier 是個非常嚴格的型別系統

想像一個 C 的 static analyzer，它不只檢查型別，還檢查：
- 每個 pointer dereference 之前，有沒有做 NULL check
- 每個 array 存取，有沒有做 bounds check
- 每個迴圈，是否一定會終止
- 每個 uninitialized variable，有沒有在使用前初始化

而且它採用 **worst-case** 假設：如果它不能靜態證明某條路徑是安全的，就拒絕整個程式。

這就是 verifier 做的事。

```
你的 BPF program
     │
     ▼
┌────────────────────────────────────────┐
│            BPF Verifier                │
│                                        │
│  Step 1：DAG check（沒有無窮迴圈）      │
│     - 確認 CFG 是有向無環圖            │
│     - kernel 5.3+：允許有界 back-edge  │
│                                        │
│  Step 2：Abstract Interpretation       │
│     - 對每條指令，追蹤每個暫存器的     │
│       "狀態"（型別 + 值域）            │
│     - 確認所有 memory access 合法      │
│     - 確認所有 pointer 有正確 NULL check│
│     - 確認沒有 uninitialized register  │
│                                        │
│  通過 → JIT                            │
│  失敗 → 回傳 verifier log              │
└────────────────────────────────────────┘
```

## Register States：verifier 的核心資料結構

verifier 給每個暫存器在每個程式點標記一個 "state"。主要的 register type：

| 型別 | 意義 |
|---|---|
| `NOT_INIT` | 未初始化，不能讀 |
| `SCALAR_VALUE` | 一般整數，不是 pointer；可以做 ALU，不能 dereference |
| `PTR_TO_CTX` | 指向 BPF program 的 context（如 `struct xdp_md *`）|
| `PTR_TO_MAP_VALUE` | `bpf_map_lookup_elem()` 回傳的指標（必須先做 NULL check）|
| `PTR_TO_MAP_VALUE_OR_NULL` | lookup 之後尚未 NULL check 的狀態 |
| `PTR_TO_STACK` | 指向 BPF stack 的指標（`r10 - N`）|
| `PTR_TO_PACKET` | 指向封包資料（XDP/TC），必須做 bounds check |
| `PTR_TO_PACKET_END` | 封包資料的結束指標（用於 bounds check）|
| `PTR_TO_FUNC` | BPF-to-BPF 呼叫的函式指標（kernel 5.13+）|

verifier 在每個 branch 之後做 **state merge**：如果兩條路徑在同一個 join point 有不同的暫存器狀態，取保守的值（例如兩條路徑一條是 `PTR_TO_MAP_VALUE`，一條是 `NOT_INIT`，merge 結果是 `NOT_INIT`）。

## 核心規則一：NULL check 之前不能 dereference

這是最常見的 verifier reject 原因：

```c
// 錯誤：沒有 NULL check
SEC("tracepoint/syscalls/sys_enter_write")
int bad_lookup(void *ctx)
{
    u32 key = 0;
    u64 *val = bpf_map_lookup_elem(&my_map, &key);
    // 在這個點，val 的型別是 PTR_TO_MAP_VALUE_OR_NULL
    *val += 1;  // REJECTED: R0 type=map_value_or_null
    return 0;
}

// 正確：先做 NULL check
SEC("tracepoint/syscalls/sys_enter_write")
int good_lookup(void *ctx)
{
    u32 key = 0;
    u64 *val = bpf_map_lookup_elem(&my_map, &key);
    // val 型別：PTR_TO_MAP_VALUE_OR_NULL
    if (!val)
        return 0;
    // 在 if 之後，val 的型別升級為 PTR_TO_MAP_VALUE
    *val += 1;  // OK
    return 0;
}
```

verifier log 會說：

```
0: (18) r1 = 0xffff...（map）
2: (b7) r2 = 0
3: (85) call bpf_map_lookup_elem#1
4: (07) r0 += 8          ← 嘗試對 r0 做 offset
R0 invalid mem access 'map_value_or_null'
```

## 核心規則二：Packet bounds check

在 XDP 和 TC 程式裡，存取封包資料前必須做 bounds check：

```c
SEC("xdp")
int packet_access(struct xdp_md *ctx)
{
    void *data     = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;

    // 錯誤：沒有 bounds check
    struct ethhdr *eth = data;
    u16 proto = eth->h_proto;  // REJECTED: invalid mem access

    // 正確：先確認 Ethernet header 在封包範圍內
    struct ethhdr *eth2 = data;
    if ((void *)(eth2 + 1) > data_end)  // eth2 + 1 是指向 eth header 之後
        return XDP_PASS;
    u16 proto2 = eth2->h_proto;  // OK，verifier 知道 eth2 是安全的
    return XDP_PASS;
}
```

**bounds check 的機制**：`data_end` 的型別是 `PTR_TO_PACKET_END`；`data` 是 `PTR_TO_PACKET`。verifier 追蹤 packet pointer 的 range；當你執行 `if (ptr > data_end) return;` 之後，verifier 知道在 if 之後的 else 分支，`ptr` 是安全的。

## 核心規則三：Stack 存取必須在合法範圍

```c
// 錯誤：stack 超出 512 bytes
char buf[600];  // REJECTED: combined stack size 608 exceeds 512

// 錯誤：stack 未對齊
u8  x;
u32 *p = (u32 *)&x;  // REJECTED: misaligned stack access

// 正確
char buf[128];
bpf_probe_read_kernel(buf, sizeof(buf), kernel_ptr);
```

## 核心規則四：Uninitialized register

```c
// 錯誤：沒有初始化就傳給 helper
int bad_init(void *ctx)
{
    u32 key;  // key 沒有被初始化
    bpf_map_lookup_elem(&my_map, &key);  // REJECTED
    return 0;
}

// 正確
int good_init(void *ctx)
{
    u32 key = 0;
    bpf_map_lookup_elem(&my_map, &key);  // OK
    return 0;
}
```

verifier 會說：`R2 !read_ok`（r2 是未初始化的）。

## 有界迴圈（kernel 5.3+）

在 kernel 5.3 之前，BPF 程式完全不允許迴圈（任何 back-edge 都被 reject）。5.3 開始，verifier 可以用 **bounded loop analysis** 接受 loop bound 是編譯期常數或可靜態確定的迴圈：

```c
// 5.3+ 允許：bound 是編譯期常數
for (int i = 0; i < 10; i++) {
    // 做一些事
}

// 5.17+ 允許：使用 bpf_loop() helper（動態 bound）
bpf_loop(n, my_callback, &data, 0);

// 仍然不允許：verifier 無法確定 bound 的迴圈
while (some_condition) {
    // REJECTED: back-edge not allowed (in older kernels)
    // or: may loop indefinitely (newer kernels)
}
```

verifier 如何證明迴圈終止？它追蹤迴圈計數器的值域；如果能確定計數器在有限次後達到終止條件，就接受。如果不能確定（因為計數器依賴 map 的值），就 reject。

## 讀懂 Verifier Log

當 verifier 拒絕你的程式，kernel 會輸出一個詳細的 log。開啟 verbose mode 取得完整 log：

```c
// 在 libbpf 裡開啟 verifier log
struct bpf_object_open_opts opts = {
    .sz = sizeof(opts),
};

// 或者直接用 bpftool
sudo bpftool prog load bad.bpf.o /sys/fs/bpf/bad 2>&1
// bpftool 預設會輸出 verifier log
```

一段典型的 verifier log 錯誤：

```
; u64 *val = bpf_map_lookup_elem(&counter, &key);
3: (85) call bpf_map_lookup_elem#1
; *val += 1;
4: (07) r0 += 1
R0 invalid mem access 'map_value_or_null'
processed 5 insns (limit 1000000) max_states_per_insn 0 total_states 0 peak_states 0 mark_read 0
```

讀 log 的方法：

1. **找到 `invalid` / `unknown` / `!read_ok` 等關鍵字**——這是 reject 原因
2. **看 reject 發生在哪一條指令**（4: 就是第 4 條指令）
3. **向上回溯那個 register 的狀態**——找到它最後一次賦值（這裡是第 3 行的 helper call 回傳 `r0`）
4. **找出為什麼它的型別不符合操作需求**——`r0` 是 `map_value_or_null`，不能直接做 `+= 1`

常見的 verifier 錯誤訊息和解法：

| 錯誤訊息 | 原因 | 解法 |
|---|---|---|
| `R0 type=map_value_or_null expected=map_value` | map lookup 後沒有 NULL check | 加 `if (!val) return 0;` |
| `R1 !read_ok` | 暫存器未初始化就使用 | 初始化暫存器 |
| `invalid mem access 'inv'` | 存取無效的記憶體型別 | 確認 pointer 型別是正確的 |
| `R1 min value is negative` | 負數 offset 對 pointer 操作 | 加 range check |
| `back-edge from insn N to M` | 迴圈但 verifier 無法確定終止 | 改用 bounded loop 或 `bpf_loop()` |
| `combined stack size NNN exceeds 512` | stack 超出限制 | 減少 stack 變數，改用 maps |
| `unreachable insn N` | 有指令永遠不會被執行 | 可能是 jump target 錯誤 |
| `at program exit the register R0 has value (0x0; 0xff)` | 回傳值不是合法的 BPF return value | 確認 return 的值在合法範圍 |

## 故意觸發 Verifier 錯誤（學習用）

觸發 verifier 錯誤是最好的學習方法：

```c
/* trigger_verifier_errors.bpf.c */
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, u64);
} test_map SEC(".maps");

/* 故意：map lookup 後沒有 NULL check */
SEC("tracepoint/syscalls/sys_enter_write")
int no_null_check(void *ctx)
{
    u32 key = 0;
    u64 *val = bpf_map_lookup_elem(&test_map, &key);
    *val = 42;  // <── verifier 會在這裡 reject
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

```bash
clang -g -O2 -target bpf -c trigger_verifier_errors.bpf.c -o err.bpf.o
sudo bpftool prog load err.bpf.o /sys/fs/bpf/err
# 預期輸出：
# libbpf: prog 'no_null_check': BPF program load failed: Invalid argument
# libbpf: prog 'no_null_check': -- BEGIN PROG LOAD LOG --
# ...
# R0 invalid mem access 'map_value_or_null'
# -- END PROG LOAD LOG --
```

## State Space Explosion 與 limits

verifier 的最大複雜度限制：

- **最大指令數**：1,000,000（`BPF_COMPLEXITY_LIMIT_INSNS`）
- **最大 state 數**：之前的 per-insn state 限制已被移除（kernel 5.19+），改用 instruction count 作為主要限制
- **最大 call depth**：8（BPF-to-BPF 函式呼叫的最大深度）

如果你的 BPF 程式很複雜，verifier 可能說 "BPF program is too large"——這不是說你的 source code 太長，是說 verifier 追蹤的 state 爆炸了。解法通常是拆分成多個 BPF 程式用 tail call 串接。

## 踩雷集錦

1. **`-O0` 編譯會讓 verifier 更容易 reject**：`-O0` 下 clang 不做很多優化，生成的 code 會有很多 verifier 看不懂的 pattern（如不必要的 stack spill 讓某個暫存器在後續路徑上狀態不確定）。永遠用 `-O2`

2. **`__builtin_expect()` 不影響 verifier**：有些人用 `__builtin_expect(!val, 0)` 試圖告訴 verifier「這個 branch 很少走到」，但 verifier 不考慮 likelihood，它還是會追蹤兩條路徑

3. **強制轉型不繞過 verifier**：`(u64 *)(uintptr_t)val` 這種轉型不會改變 verifier 看到的 register type。verifier 追蹤的是語意型別，不是 C 型別

4. **在 tail call 之後暫存器狀態被清空**：`bpf_tail_call()` 不會返回（或返回到下一條指令，狀態被 reset）；在 tail call 之後的指令，所有暫存器都被 verifier 標記為不可信

5. **Verifier 的 "pruning" 可能跳過某些路徑**：verifier 做了 path pruning 優化——如果一個新路徑的狀態是之前已分析的某個狀態的 subset，它會剪掉這條路徑。這偶爾導致某個你以為會被 reject 的 code 通過了（因為 verifier 剪掉了那條路徑）

## 進階：Verifier 的 Abstract Interpretation 框架

verifier 做的是 **over-approximation**：它維護每個暫存器在每個程式點的值域（value range），而不是具體值。

```
暫存器狀態（針對 SCALAR_VALUE）：
  smin_value：有符號最小值
  smax_value：有符號最大值
  umin_value：無符號最小值
  umax_value：無符號最大值
  var_off：Tnum（tracked bits）—— 哪些 bits 是已知的
```

例如，執行 `r1 &= 0xFF` 之後，verifier 知道：
- `r1.umax_value = 255`
- `r1.smax_value = 255`（如果 r1 是非負數）
- `r1.var_off.mask = 0xFF`（只有低 8 bits 有值）

這讓 verifier 可以靜態確認 `r1` 是合法的 map index（如果 map 有 256 個 entry）。

## 動手練習

1. 寫一個故意觸發「map_value_or_null dereference」錯誤的 BPF 程式，觀察 verifier log，然後修正它

2. 寫一個 for 迴圈（`for (int i = 0; i < 10; i++) bpf_printk("i=%d\n", i)`），確認 kernel 5.3+ 接受它；然後試著把 bound 改成 `i < some_variable`（從 map 讀取的值），觀察 verifier 是否拒絕

3. 寫一個超過 512 bytes stack 的 BPF 程式（宣告一個 `char buf[600]`），觀察錯誤訊息，然後把它改用 per-cpu array map 實作

4. 在 libbpf 的 `bpf_object_open_opts` 裡設定 `verifier_log_level = BPF_LOG_LEVEL2`，重新載入你的 BPF 程式，觀察 verbose log 裡的暫存器狀態追蹤

## 本章重點整理

- verifier 用 abstract interpretation 靜態分析 BPF 程式的安全性，每個暫存器在每個程式點有明確的型別狀態
- 最常見的 reject 原因：map lookup 後沒有 NULL check、封包存取沒有 bounds check、暫存器未初始化
- kernel 5.3+ 支援有界迴圈；動態 bound 要用 `bpf_loop()` helper（5.17+）
- verifier log 告訴你哪一條指令被 reject 以及 register 的狀態，向上回溯找原因

## 自我檢核

- [ ] 能解釋 `PTR_TO_MAP_VALUE_OR_NULL` 和 `PTR_TO_MAP_VALUE` 的差別，以及什麼情況下狀態會升級
- [ ] 給一段有 verifier 錯誤的 BPF code，能讀出錯誤訊息並找到修法
- [ ] 能解釋為什麼 `-O0` 比 `-O2` 更容易被 verifier reject
- [ ] 知道 verifier 的「狀態爆炸」是什麼，以及如何規避（tail call 拆分）

## 延伸閱讀

### 論文

- **[Formal Verification of BPF JIT compilers](https://cs.au.dk/~birke/papers/bpfjit-pldi20.pdf)** — Guan et al., PLDI 2020
  - **核心貢獻**：用 Coq 形式化驗證 BPF JIT compiler 的正確性；說明了 JIT 的哪些方面是 hard to verify 的
  - **讀哪裡**：Section 2（background）和 Section 3（BPF semantics）；技術細節可跳過
  - **和本章的關聯**：理解 verifier 和 JIT 之間的信任邊界

### 官方文件

- **[Linux kernel: BPF verifier documentation](https://www.kernel.org/doc/html/latest/bpf/verifier.html)**
  - **讀哪裡**：整份；特別是 "register value tracking" 和 "pointer arithmetic" 那幾節
  - **學什麼**：verifier 的每個規則的官方解釋，作為參考文件查閱

### 部落格

- **[BPF verifier overview](https://elixir.bootlin.com/linux/latest/source/kernel/bpf/verifier.c)** — Alexei Starovoitov（kernel source + comments）
  - **這篇說什麼**：`kernel/bpf/verifier.c` 的 source code 是最直接的文件；文件 comment 非常詳細
  - **讀哪裡**：函式 `do_check_insn()` 和 `check_mem_access()`；這兩個函式是 verifier 核心邏輯
  - **為什麼值得讀**：沒有比 source 更權威的文件

- **[How BPF achieves safety](https://confused.ai/posts/bpf-safety)** — confused.ai
  - **這篇說什麼**：從 formal methods 角度解釋 verifier 的 abstract interpretation 框架
  - **讀哪裡**：整篇；特別是 "Register States" 和 "Memory Safety" 兩節
  - **為什麼值得讀**：比 kernel docs 更有理論深度，幫助你建立 mental model

→ [練習 A：bpftool 全面探索](./practice-a-bpftool-exploration.md)
