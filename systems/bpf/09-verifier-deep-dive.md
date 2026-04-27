# Ch 9 — Verifier 深入：為什麼你的 BPF 會被拒絕

> 目標：搞懂 verifier 在做什麼、為什麼會拒絕你、怎麼讀它的拒絕訊息、十大常見被拒原因。讀完這章，你的 BPF debug 能力會升級一個檔次 — 從「Verifier 又罵我了」變成「我知道它在抱怨什麼」。

## Verifier 是 BPF 的根

回想 Ch 2：寫 kernel module 為什麼危險？因為 ring 0 沒有保險絲，一個野指標、一個無限迴圈、一個越界寫，整個 kernel 就掛。

BPF 的承諾是：**程式跑在 ring 0、能力跟 kernel 一樣強，但不可能 panic kernel**。靠什麼兌現？**Verifier**。

Verifier 是個跑在 kernel 裡的**靜態分析器**。BPF 程式 load 時，verifier 把 bytecode 從頭走一遍，模擬執行所有可能的路徑，**證明每一個記憶體存取、每一個指標解參考、每一次 helper 呼叫都安全**。證明不出來，直接拒絕。

通過 verifier 的 BPF 程式有以下保證：

- 不會解參考 NULL
- 不會越界存取 stack / map / packet
- 不會呼叫不該呼叫的 helper、傳錯參數型別
- 不會無窮迴圈
- 不會 leak kernel pointer 到 user space
- 不會死在 kernel 裡

代價是：**verifier 比 GCC 的型別檢查嚴格很多**，剛開始寫 BPF 會撞到牆。

## Verifier 怎麼工作 — symbolic execution 直覺

Verifier 不是「跑你的 code 看會不會出事」 — 那種測試遠遠不夠。它做的是 **abstract interpretation**：把每個 register / 每個 stack slot 的「可能值範圍」當成一個抽象狀態，模擬走過所有 control flow。

例子：

```c
int x = bpf_get_prandom_u32();   // verifier 知道 x ∈ [0, 2^32)
if (x < 100) {
    // 這條 path 上 verifier 知道 x ∈ [0, 100)
    array[x] = 1;                // 安全，因為 x < 100 且 array 至少 100 元素
}
```

如果你寫成：

```c
int x = bpf_get_prandom_u32();
array[x] = 1;                    // x 可能 > array size，verifier 拒絕
```

verifier 會說：

```
math between map_value pointer and unbounded register prohibited
```

它的世界觀就是：**只要任何一條 path 上有可能不安全，整個程式就不安全**。

## 指標型別系統 — verifier 看世界的方式

Verifier 給每個 register / stack slot **標一個型別**。常見的：

| 型別 | 意思 |
|---|---|
| `SCALAR_VALUE` | 整數（記錄最小/最大可能值） |
| `PTR_TO_CTX` | 指向 program context（kprobe 的 pt_regs、XDP 的 xdp_md...） |
| `PTR_TO_MAP_VALUE` | map lookup 拿到的指標 |
| `PTR_TO_MAP_VALUE_OR_NULL` | map lookup 後**未檢查 NULL** 的狀態 |
| `PTR_TO_PACKET` | 封包資料指標（XDP / TC） |
| `PTR_TO_PACKET_END` | 封包結尾指標 |
| `PTR_TO_STACK` | stack 上某位址 |
| `PTR_TO_BTF_ID` | 指向已知 BTF type 的 kernel struct（fentry/fexit 用） |

**型別轉換有嚴格規則**。例如 `PTR_TO_MAP_VALUE_OR_NULL` 要先做 NULL check 才會升級為 `PTR_TO_MAP_VALUE` — 跳過這步，**碰它就拒絕**。

## 十大被拒理由 + 怎麼修

### 1. 沒做 NULL check

```c
u64 *count = bpf_map_lookup_elem(&map, &key);
*count += 1;     // ← 拒絕
```

```
R1 invalid mem access 'map_value_or_null'
```

**修法**：

```c
u64 *count = bpf_map_lookup_elem(&map, &key);
if (count) {
    *count += 1;
}
```

### 2. Packet 越界存取

```c
SEC("xdp")
int filter(struct xdp_md *ctx) {
    void *data = (void *)(long)ctx->data;
    struct ethhdr *eth = data;
    if (eth->h_proto == ...) ...   // ← 拒絕：沒先比對 data_end
}
```

```
invalid access to packet, off=12 size=2 R2(id=0,off=0,r=0)
```

**修法**：

```c
void *data = (void *)(long)ctx->data;
void *data_end = (void *)(long)ctx->data_end;
struct ethhdr *eth = data;
if ((void *)(eth + 1) > data_end) return XDP_PASS;   // bounds check
if (eth->h_proto == ...) ...
```

每存取 packet 一個欄位前**都要保證 `ptr + size <= data_end`**。XDP 與 TC 寫起來最痛的部分。

### 3. Stack 越界

```c
char buf[16];
int idx = bpf_get_prandom_u32();
buf[idx] = 0;    // ← 拒絕：idx 可能任意大
```

修法：用 `& mask`：

```c
buf[idx & 0xF] = 0;   // 強制 idx ∈ [0, 16)
```

或用 verifier 看得懂的明確比較：

```c
if (idx < 16) buf[idx] = 0;
```

### 4. 指標算術走太遠

```c
char *p = bpf_map_lookup_elem(&map, &key);
if (!p) return 0;
p += offset;     // offset 是 unbounded
*p = 0;          // ← 拒絕
```

修法：把 offset 限制在 map value 範圍內。

### 5. Loop 邊界 verifier 看不出來

```c
int n = some_external_value();
for (int i = 0; i < n; i++) { ... }   // ← 5.3 之前直接拒；之後仍可能拒
```

**修法**：用 `bpf_loop()`（5.17+）或 unroll：

```c
#pragma unroll
for (int i = 0; i < 16; i++) { ... }   // 編譯時展開，verifier 看到 16 條獨立指令
```

### 6. 用了當前 program type 不能用的 helper

```c
SEC("xdp")
int prog(struct xdp_md *ctx) {
    u32 pid = bpf_get_current_pid_tgid();   // ← XDP 沒 current task
    ...
}
```

```
unknown func bpf_get_current_pid_tgid#xx
```

**修法**：換 program type、或拿掉 helper。

### 7. Helper 參數型別不對

```c
char *p = ...;
bpf_printk(p);         // ← printk 要的是 const char *fmt（const string）
```

修法：

```c
bpf_printk("hello\n");
```

### 8. 太多 instruction / 太多 branch

```
BPF program is too large. Processed XX insn
```

5.0 之前 4096 instruction 上限，現在放寬到 100 萬，但 **complexity（path 組合）有獨立上限**。常見成因：太多 if / 太深的 unrolled loop / 太多的 helper call。

**修法**：拆成 tail call（Ch 26）、或重構。

### 9. 釋放後使用 / 雙重釋放（涉及 spin_lock 等）

```c
bpf_spin_lock(&lock);
bpf_map_lookup_elem(...);   // ← 持鎖時不能呼叫某些 helper
bpf_spin_unlock(&lock);
```

verifier 對 spin_lock 與某些 referenced object 有嚴格的 acquire/release 配對檢查。

### 10. Leak kernel pointer

```c
u64 task_ptr = (u64)bpf_get_current_task();
bpf_map_update_elem(&map, &key, &task_ptr, BPF_ANY);   // ← 試圖把 kernel ptr 寫到 map → user 可讀
```

verifier 不允許把 kernel pointer 從 map / ring buffer 漏到 user space — 這是資安考量（防止 KASLR 被旁路）。

## 怎麼讀 verifier log

當載入失敗，user space 拿到 errno，但**真正有用的是 verifier log**。libbpf 預設會印給你（如果開了 `LIBBPF_STRICT_AUTO_RLIMIT_MEMLOCK` 或 verbose mode）：

```
sudo bpftool prog load my.bpf.o /sys/fs/bpf/myprog
```

不行就強制 verbose：

```c
// 在 user space loader 裡
LIBBPF_OPTS(bpf_object_open_opts, opts);
opts.kernel_log_level = 1;     // 1 = 失敗時印；2 = 全印
```

或環境變數：

```bash
sudo LIBBPF_STRICT_LIBBPF=1 ./my-loader
```

典型 verifier log 長這樣（節錄）：

```
0: (b7) r1 = 0
1: (7b) *(u64 *)(r10 -8) = r1
2: (bf) r2 = r10
3: (07) r2 += -8
4: (18) r1 = 0xffff...
6: (85) call bpf_map_lookup_elem#1
7: (15) if r0 == 0x0 goto pc+5
 R0=map_value(off=0,ks=4,vs=8) R10=fp0
8: (61) r1 = *(u32 *)(r0 +0)
9: (07) r1 += 1
10: (63) *(u32 *)(r0 +0) = r1
11: (b7) r0 = 0
12: (95) exit
13: (b7) r0 = 0       ; ← 從 #7 這個 if false branch 跳來
14: (95) exit
processed 13 insns ...
```

**讀法**：

- 每行是一條 BPF 指令。前面是 PC、括號裡是 opcode、後面是助記。
- 縮排部分（`R0=...`）是該 PC 處的 register state。
- 有錯時，最後幾行會給出原因 + 哪個 register / 偏移有問題。

**抓重點 — 倒著看 log**。verifier 失敗訊息通常在最後 5–10 行。

## Verifier complexity 爆炸

Verifier 對每條 path 都要分析。如果你有 N 個 if、verifier 可能要分析 2^N 條 path。

**Path explosion** 是中型 BPF 程式最常見的卡關。徵兆：

```
BPF program is too large. Processed 1000000 insn
```

緩解：

1. 用 `__noinline` 把熱區拆成獨立 function（5.5+ 支援 BPF subprogram，verifier 對 subprogram 各自分析）
2. 用 tail call（Ch 26）拆成多支 program
3. 把 if 條件改成 mask / table lookup
4. 用 `bpf_loop()` 取代手動 unroll

## Bounded loop（5.3+）

5.3 之前 BPF **完全禁止 loop**，要 loop 必須 `#pragma unroll`。5.3 後允許 loop，**但 verifier 必須能證明它會結束**：

```c
for (int i = 0; i < 100; i++) { ... }   // OK：i 從 0 到 100
```

```c
int n = some_dynamic_value;
for (int i = 0; i < n; i++) { ... }     // 通常被拒：verifier 不知 n 多大
```

5.17 加入 `bpf_loop()` helper，是更乾淨的解：

```c
static long my_callback(u32 idx, void *ctx) { ... return 0; }

bpf_loop(100, my_callback, NULL, 0);    // verifier 信任這個 helper 會跑 100 次
```

## 跟 verifier 和平共處的技巧

- **遇到拒絕，先看最後 5 行 log**。八成有明確線索。
- **加 explicit check 不嫌煩**。`if (ptr == NULL) return 0;` 寫了又如何，verifier 會幫你優化。
- **包裝 unsafe pattern 進 helper**。`bpf_probe_read_*` 系列就是讓你「冒險」讀記憶體（kernel 會處理 fault）。
- **看別人的 code**。`libbpf-bootstrap`、`bcc/libbpf-tools` 是最佳範本。
- **用 fentry 不用 kprobe**。fentry 拿到 BTF type 化的參數，verifier 對指標的追蹤更精確 — 痛苦少一半。

## 一個常見誤解

「verifier 太嚴格了 / 是個 bug」 — **不全然**。

它的「嚴格」是設計目標，不是 bug。它寧可拒絕一個其實安全的程式，也不能讓不安全的程式過關 — 這個權衡是 BPF 整套安全模型的根。每年 verifier 會變寬鬆一點（loop、subprogram、kfunc），但「safety first」的方向不會變。

很多被拒的 case，**用更顯式的寫法（多加 NULL check、用 mask 限範圍、改用 bpf_loop）就能解決**。少數真的很煩、但你拗不過它 — 接受這就是 BPF 寫程式的稅。

## 動手練習

1. **故意觸發 NULL check 拒絕**：寫個 BPF C，做 `bpf_map_lookup_elem` 後直接解參考、不檢查 NULL。看 verifier 怎麼罵。
2. **故意觸發 packet bound 拒絕**：寫個 XDP，存取 `eth->h_proto` 但不比對 `data_end`。
3. **看 verifier log**：把 `LIBBPF_LOG_LEVEL=2` 設起來，跑一個正常的 BPF 程式，**看完整的 instruction-by-instruction trace**。培養讀 log 的肌肉記憶。
4. **挑戰 path explosion**：寫一個 BPF 程式有 20 個 if，每個都做不同 helper call — 測會不會 verifier complexity 爆。

## 自我檢核

- [ ] 我能解釋 verifier 用 abstract interpretation 在做什麼
- [ ] 我能列出至少 5 種常見被拒理由
- [ ] 我能解釋為什麼 packet 存取要顯式做 bound check
- [ ] 我能說出 path explosion 是什麼、有哪些緩解方式
- [ ] 我能讀 verifier log 並從最後幾行找出失敗原因

下一章我們處理 BPF 部署的最大痛點：**跨 kernel 版本相容**。BTF 與 CO-RE 是怎麼讓「一份 binary 跑遍所有 kernel」變成可能的。

→ [Ch 10 BTF 與 CO-RE：跨 kernel 版本部署](./10-btf-and-core.md)
