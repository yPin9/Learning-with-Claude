# Ch 4 — Edge coverage 原理：為什麼是 edge、為什麼是 64KB bitmap

> 目標：說清楚 edge vs block coverage 的差異；拆解 `prev_loc` trick 怎麼把 edge 映射到 bitmap index；解釋 64KB 的取捨與 collision 問題（引出 Ch 5 的 LTO 為什麼能 collision-free）。

## 先決定要追蹤什麼

instrumentation 要寫進 bitmap 的資訊，有幾種粒度選擇：

| 粒度 | 追蹤什麼 | 區分能力 |
|---|---|---|
| Function coverage | 哪些函式被呼叫 | 最粗，IDE 測試覆蓋率用 |
| Block coverage | 哪些 basic block 被執行 | 中等，gcov 預設 |
| **Edge coverage** | basic block 之間的**轉移** | 較細 |
| Path coverage | 整條執行路徑 | 最細，但空間爆炸 |

AFL 選 **edge coverage**，也就是「從 block A 跳到 block B」這個動作本身是觀察單位。

## 為什麼不是 block？

Block coverage 看起來不錯 — 每個 basic block 發一個 ID，被執行就點亮。但它無法區分**走的順序**：

```c
if (a) foo();
if (b) bar();
```

`a=1, b=0` 走 [foo]，`a=0, b=1` 走 [bar]，`a=1, b=1` 走 [foo, bar]。三種輸入，三條執行路徑 — 但 block coverage 只看到「foo 被執行過 / bar 被執行過」，丟失了順序資訊。

Edge coverage 區分：
- `[entry → foo]` 是一條 edge
- `[foo → bar]` 是另一條
- `[entry → bar]` 又另一條

三種輸入就是三種 edge 集合，準確區分。

## 為什麼不是 path？

Path coverage 理論上最精準，但 path 數量是**指數爆炸**。一個有 20 個 branch 的函式，path 最多 $2^{20}$ 條。bitmap 開不了這麼大，而且你不需要這麼細 — 許多 path 的差異對 bug 發現無關。

Edge 是**多數 bug 和少數 overhead** 的甜蜜點。

## `prev_loc` trick：兩個 block 的 XOR

怎麼把「從 A 跳到 B」這個 transition 編碼成一個 bitmap index？最直接的是 `(A, B)` tuple，但 tuple 要 hash table。AFL 用了一個又快又 cache-friendly 的 trick：

```c
// 編譯期：每個 basic block 分配一個隨機 u16 id
cur_loc = /* 這個 block 的 compile-time 隨機 ID */;

// 執行期：寫入 bitmap
shared_mem[cur_loc ^ prev_loc]++;
prev_loc = cur_loc >> 1;   // ← 關鍵
```

三行 code 就幹完了。分析：

- `cur_loc` 是每個 block 一個獨立的 16-bit 隨機值，編譯時固定。
- `cur_loc ^ prev_loc` 就是「這條 edge 的 ID」。兩個 u16 XOR 落在 [0, 65535]，剛好索引 64KB bitmap。
- **最後一行 `prev_loc = cur_loc >> 1` 才是精髓** — 少了右移一位，`A→B` 和 `B→A` 會 XOR 出同一個 edge ID（因為 XOR 交換律）。右移讓兩者非對稱，能區分方向。

```c
// 沒有 >> 1 的話：
A=0x1234, B=0x5678
A->B: 0x1234 ^ 0x5678 = 0x444C
B->A: 0x5678 ^ 0x1234 = 0x444C   // 一樣！

// 有 >> 1：
A->B: (A >> 1) ^ B = 0x091A ^ 0x5678 = 0x5F62
B->A: (B >> 1) ^ A = 0x2B3C ^ 0x1234 = 0x3908   // 不同！
```

這個 idea 來自 Zalewski 的原創設計。簡單到沒用過的人會覺得「這樣就夠了嗎？」但它撐起了整個 AFL 世代。

實際 AFL++ 的 `afl-compiler-rt.o.c` 大致這樣插：

```c
// 編譯期，pass 在每個 basic block 開頭插入這段
__afl_area_ptr[__afl_prev_loc[0] ^ cur_loc]++;
__afl_prev_loc[0] = cur_loc >> 1;
```

`__afl_area_ptr` 指向 shared memory，`__afl_prev_loc` 是 thread-local 的陣列（陣列是為了支援 NGRAM，不只看前一個 block）。

## 為什麼是 64KB

`MAP_SIZE_POW2 = 16` → `MAP_SIZE = 65536`。這個數字是取捨結果：

- **太小**：edge collision 率高（不同 edge XOR 到同 index），假裝沒有新 coverage。
- **太大**：每次 iteration 的 `has_new_bits()` 掃 bitmap 的 cost 變貴，SIMD 加速也有極限。再來是 L1/L2 cache 塞不下。

原 AFL paper 的 heuristic：

| Target 規模 | 建議 bitmap |
|---|---|
| 小 CLI tool（< 2000 edges） | 64KB 綽綽有餘 |
| 中型 lib（10000 edges） | 64KB 開始有 collision |
| 大型 parser（> 50000 edges） | 需要 `AFL_MAP_SIZE=131072` 或更大 |

AFL++ 允許用 `AFL_MAP_SIZE` 環境變數在 runtime 調整（前提 instrumentation 也編成支援動態大小），編譯期可設 `-DMAP_SIZE_POW2=17` 變 128KB。

## Collision 的代價

假設兩條完全不同的 edge：
- edge X：`prev=0x1234, cur=0x5678` → index = `0x091A ^ 0x5678 = 0x5F62`
- edge Y：`prev=0xABCD, cur=0x0000` → index = `0x55E6 ^ 0x0000 = 0x55E6`

不會相撞。但只要 XOR 後落在同一 index，AFL 的 `has_new_bits()` 就認為「這裡已經亮過了，不是新發現」— 於是**一條真正新找到的 edge 被遮蔽**，對應的 input 被誤丟。

CollAFL paper (S&P 2018) 實測：64KB bitmap 對中大型 target collision 率可達 **3–10%**。聽起來不多，但 fuzzing 是指數放大遊戲 — 少發現 10% 的 edge，接下來以它為 seed 衍生的 input 全部不會出現，複利損失驚人。

**解法**：不要依賴 random ID，改成編譯期替每條 edge 分配**保證唯一** 的 ID。這要 link time 才能做到（因為每個 translation unit 編譯時看不到全局）。所以需要 LTO（link-time optimization）。AFL++ 的 `afl-clang-lto` 就是這麼做的，Ch 5 會詳細拆。

## Hit count bucketing

另一個容易忽略的細節：`trace_bits[index]` 是 **u8**，一個 byte。它記「這條 edge 被走過幾次」— 但 fuzzer 不真的在乎走了 17 次還是 18 次，只在乎「數量級有沒有差」。

AFL 的 `classify_counts()` 把 hit count 分桶：

```
1           → 1
2           → 2
3           → 4
4 .. 7      → 8
8 .. 15     → 16
16 .. 31    → 32
32 .. 127   → 64
128 ..      → 128
```

變成位元獨立的桶，之後 `has_new_bits()` 可以直接 bit-wise AND 比對。實作大致：

```c
static const u8 count_class_lookup8[256] = {
    [0]           = 0,
    [1]           = 1,
    [2]           = 2,
    [3]           = 4,
    [4 ... 7]     = 8,
    [8 ... 15]    = 16,
    [16 ... 31]   = 32,
    [32 ... 127]  = 64,
    [128 ... 255] = 128
};

void classify_counts(u8 *map) {
    for (int i = 0; i < MAP_SIZE; i++)
        map[i] = count_class_lookup8[map[i]];
}
```

這樣 `trace_bits[edge] = 16`（走了 10 次）和 `trace_bits[edge] = 64`（走了 50 次）被視為**不同 coverage**。對 loop body 而言很有意義 — loop 跑 10 次 vs 50 次，可能走的是不同的 state。

有些論文（如 CollAFL）指出這個 8-bucket 設計可能太寬 / 太窄，AFL++ 保留但允許用 `AFL_LLVM_SKIP_NEVERZERO` 等 flag 調整行為。

## `trace_bits` 的寫法：NEVERZERO

如果某 edge 被走 256 次，u8 會溢位歸零。歸零會讓 `classify_counts` 誤認「沒走過」。所以 instrumentation 寫入會加個 never-zero 保護：

```c
// 一般版本
__afl_area_ptr[edge]++;

// NEVERZERO 版本（避免溢位歸零）
u8 v = __afl_area_ptr[edge] + 1;
if (v == 0) v = 1;
__afl_area_ptr[edge] = v;
```

這個 patch 看起來小，但對 loop-heavy target 可以多找到幾個 bug。AFL++ 預設啟用。

## 常見誤解

- **「edge 就是 branch」**：不完全對。unconditional jump（例如 function call、goto）也算 edge。edge 是 CFG 上的「邊」，不只 conditional branch。
- **「bitmap 大一點一定更好」**：不。bitmap 大意味著每次 iteration 清零與掃描成本上升，而且大部分 target 用不到那麼多。先看 `afl-fuzz` 跑時顯示的 `map density` — 若 < 50% 就不需要擴大。
- **「hit count 越精確越好」**：也不。若每個 hit count 都區分，queue 會爆炸（每個微小 loop 次數差都產出新 seed），fuzzer 陷入 path explosion。8-bucket 是實測出來的甜蜜點。

## 一個延伸的想法：context-sensitive coverage

有些 edge 在不同 calling context 裡意義不同。例如 `strlen()` 被 parser 呼叫 vs 被 error handler 呼叫，AFL 看起來是同一條 edge。AFL++ 支援 `-DAFL_LLVM_CTX=1` 啟用 context-sensitive：每次 function call 把 return address hash 進 `prev_loc`，edge ID 帶上 context。

代價是 bitmap 更擁擠（更容易 collision），所以這個 flag 通常和 LTO 或大 bitmap 一起用。

## 自我檢核

- [ ] 能解釋為什麼 edge coverage 比 block coverage 強
- [ ] 能畫出 `prev_loc >> 1` 為什麼需要，給出具體例子
- [ ] 知道 64KB bitmap 的來源是取捨，不是唯一解
- [ ] 能說出 classify_counts 的 8 個桶以及 NEVERZERO 的用途
- [ ] 能解釋 CollAFL / LTO 為什麼要消 collision

下一章看 compile-time instrumentation 的四種模式，以及 LTO 怎麼做到 collision-free。

→ [Ch 5 編譯期 instrumentation 四種模式](./05-compile-time-instrumentation.md)
