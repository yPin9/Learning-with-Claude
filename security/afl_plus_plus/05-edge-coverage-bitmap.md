# Ch 5 — Edge Coverage Bitmap：AFL 的感知器官

> **目標**：說清楚 edge vs block coverage 差異；拆解 `prev_loc >> 1` trick；解釋 64KB 的取捨與 collision 問題；介紹 hit count bucketing 和 NEVERZERO。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64 Linux

---

## 為什麼需要這個？

2014 年之前，fuzzer 大多靠兩種方式判斷「這次輸入有沒有觸碰到新行為」：

1. **純亂槍打鳥**：完全隨機變異，不管有沒有碰到新東西，靠數量堆出覆蓋率。
2. **Instrumentation + 邊界覆蓋**：插樁紀錄哪些函式或基本塊（basic block）被執行，新 block 才留種子。

第一種浪費算力，第二種有個隱藏缺陷：只看 block coverage，你不知道「A 執行完跳到 B」和「C 執行完跳到 B」是不同的路徑。對 bug hunting 而言，這兩條路徑可能完全不同的語義。

AFL 的核心貢獻之一，是用 **edge coverage**（邊覆蓋）取代 block coverage，並用一個超輕量的 shared memory bitmap 紀錄結果，讓 fuzzer 在不增加太多 overhead 的前提下，獲得更豐富的程式行為訊號。

---

## 先建立直覺

把程式的控制流圖（Control Flow Graph, CFG）想成一張地圖：

```
      [A]
     /   \
   [B]   [C]
     \   /
      [D]

Block coverage 問：D 有沒有被走到？
Edge coverage  問：是 B→D 還是 C→D？
```

Block coverage 裡，只要 D 被執行過就打勾。Edge coverage 則分別紀錄「B 走到 D」和「C 走到 D」是不同的事件。

AFL 用一個 64KB 的 shared memory array（bitmap）紀錄所有 edge 是否被觸發過。每個 edge 對應 bitmap 裡的一格，格子裡存的是 hit count（命中次數）。

---

## Coverage 的四種粒度

| 粒度 | 紀錄什麼 | AFL 中的角色 |
|------|----------|-------------|
| Function coverage | 哪些函式被呼叫過 | 太粗糙，AFL 不用 |
| Block coverage | 哪些基本塊被執行過 | 用於靜態分析報告 |
| Edge coverage | 哪些控制流邊被走過 | **AFL 的主要信號** |
| Path coverage | 從入口到出口的完整路徑 | 路徑爆炸，不可行 |

AFL 選 edge 的原因：比 block 細（能分辨迴圈方向、條件分支來源），比 path 粗（路徑數量是指數爆炸，無法追蹤），在 overhead 和資訊量之間取得最好的平衡。

---

## 核心概念：`prev_loc ^ cur_loc` 索引計算

### bitmap 的資料結構

```c
/* instrumentation/afl-compiler-rt.o.c（簡化版）*/

/* 64KB shared memory bitmap */
extern uint8_t *__afl_area_ptr;   /* 指向 shm，fuzzer 和 target 共享 */
extern uint32_t __afl_prev_loc;   /* 上一個 basic block 的隨機 ID */

/* 每個 basic block 開頭插入的 instrumentation（CLASSIC 模式）*/
void __afl_trace(uint32_t cur_loc) {
    uint32_t idx = __afl_prev_loc ^ cur_loc;
    __afl_area_ptr[idx]++;          /* 記錄這條 edge 被走過 */
    __afl_prev_loc = cur_loc >> 1;  /* 為什麼 >> 1？下面解釋 */
}
```

每個 basic block 在 compile time 被分配一個隨機的 `cur_loc`（16-bit）。當程式從 block A 跳到 block B：

```
idx = A_id ^ B_id
bitmap[idx]++
prev_loc = B_id >> 1
```

### 為什麼需要 `cur_loc >> 1`？

這是 AFL 最精妙的設計之一。

**問題**：XOR 有對稱性。假設 A 的 ID = 0b1010，B 的 ID = 0b0101：

```
A→B：idx = 0b1010 ^ 0b0101 = 0b1111
B→A：idx = 0b0101 ^ 0b1010 = 0b1111  ← 完全一樣！
```

如果 `prev_loc` 直接存 `cur_loc`，那 A→B 和 B→A 會對應到同一個 bitmap 格子，fuzzer 無法分辨這兩條語義不同的邊。

**修復**：把 `prev_loc` 存為 `cur_loc >> 1`，打破對稱性：

```
A→B：
  idx      = prev_loc_A ^ B_id
           = (A_id >> 1) ^ B_id
prev_loc_B = B_id >> 1

B→A（下一次從 B 出發）：
  idx      = prev_loc_B ^ A_id
           = (B_id >> 1) ^ A_id   ← 和 (A_id >> 1) ^ B_id 不同！
```

具體數字示範（A_id = 16, B_id = 9，用 4-bit 方便看）：

```
A=0b10000（只取低4位 = 0）, B=0b01001 = 9

A→B：idx = (0 >> 1) ^ 9  = 0 ^ 9 = 9
B→A：idx = (9 >> 1) ^ 0  = 4 ^ 0 = 4   ← 不同！
```

右移一位讓 A 和 B 扮演不對稱的角色：A 永遠是「被移位後的版本」，B 永遠是「原始版本」。

---

## 底層機制：它是怎麼運作的？

### bitmap 更新的完整流程

```
程式執行到 basic block B（ID = cur_loc）

┌─────────────────────────────────────────────────────┐
│                  __afl_trace(cur_loc)                │
│                                                      │
│  __afl_prev_loc ──→ XOR ──→ idx                      │
│  (上一個 block   )         (bitmap 索引)              │
│  的 ID >> 1                                          │
│                  cur_loc ─┘                          │
│                                                      │
│  __afl_area_ptr[idx]++   （64KB shared memory 寫入）  │
│                                                      │
│  __afl_prev_loc = cur_loc >> 1  （更新狀態，為下次備用）│
└─────────────────────────────────────────────────────┘

fuzzer 端（afl-fuzz）：
┌─────────────────────────────────────────────────────┐
│  has_new_bits(virgin_bits)                           │
│    比較 trace_bits（這次的 bitmap）                   │
│    和 virgin_bits（歷史從未見過的 bitmap）             │
│    有新的 1 → 這個輸入觸發了新 edge                   │
│    有新的 bucket → hit count 增加到新的桶             │
└─────────────────────────────────────────────────────┘
```

### `has_new_bits()` 的實作邏輯

```c
/* src/afl-fuzz-bitmap.c（概念版）*/

uint8_t has_new_bits(uint8_t *virgin_map) {
    uint64_t *current = (uint64_t *)trace_bits;
    uint64_t *virgin  = (uint64_t *)virgin_map;
    uint8_t  ret = 0;

    /* 每次比較 8 bytes（8 個 edge），加速掃描 */
    for (uint32_t i = 0; i < MAP_SIZE / 8; i++) {
        if (unlikely(current[i] & virgin[i])) {
            /* current[i] 有 bit，virgin[i] 也有 → 代表 virgin 這些還沒被"消耗"
               current 有 virgin 沒有 → 真正的新 edge */
            if (likely(ret < 2)) {
                uint8_t *cur8 = (uint8_t *)(current + i);
                uint8_t *vir8 = (uint8_t *)(virgin  + i);
                for (int j = 0; j < 8; j++) {
                    if (cur8[j] && vir8[j]) {
                        ret = 2;  /* 新 hit count bucket */
                        break;
                    }
                    if (cur8[j] && !vir8[j]) {
                        ret = 1;  /* 全新 edge */
                    }
                }
            }
            virgin[i] &= ~current[i];  /* 標記這些 edge 已被見過 */
        }
    }
    return ret;  /* 0=無新東西, 1=新edge, 2=新bucket */
}
```

---

## 64KB 的取捨

`MAP_SIZE_POW2=16` 代表 bitmap 大小 = 2^16 = 65536 bytes = 64KB。

這個數字是工程取捨的結果，不是拍腦袋的：

```
太小 → collision 增加（不同 edge 對應同一個 bitmap 格子）
太大 → CPU cache 放不下（L1 cache 通常 32KB，L2 256KB）
       每次 has_new_bits() 掃描更慢
       fork server 每次 fork 要 memset 更大的 shm
```

64KB 的選擇讓 bitmap 正好放在 L2 cache 裡，在 10,000 exec/sec 的速率下，cache miss 的累積代價可以忽略。

如果你的 target 非常複雜（> 10,000 個 edge），可以調大：

```bash
# 只在確定需要時調整，且 instrumentation 和 fuzzer 兩邊必須一致
export AFL_MAP_SIZE=262144   # 256KB，或用 MAP_SIZE_POW2=18
```

**注意**：`AFL_MAP_SIZE` 必須在 instrumentation 階段（編譯時）和 fuzzer 執行時都設定一致。不一致會導致 silent corruption：fuzzer 讀的和 target 寫的是不同大小的 shm。

---

## Hit Count Bucketing

bitmap 裡每個格子是 `uint8_t`，理論上可以存 0-255 的精確 hit count。但 AFL 不用精確值，而是把所有值**量化（quantize）到 8 個桶**：

```c
/* src/afl-fuzz-bitmap.c：classify_counts() */

static const uint8_t count_class_lookup8[256] = {
    [0]           = 0,
    [1]           = 1,
    [2]           = 2,
    [3]           = 4,
    [4 ... 7]     = 8,
    [8 ... 15]    = 16,
    [16 ... 31]   = 32,
    [32 ... 63]   = 64,
    [64 ... 255]  = 128,
};
```

這 8 個桶是：1, 2, 4, 8, 16, 32, 64, 128。

**為什麼不用精確計數？**

考慮一個迴圈 `for (i=0; i<N; i++)`：

- N=5 跑了 5 次迴圈體
- N=6 跑了 6 次迴圈體

精確計數會把這兩個輸入視為觸發了不同的行為，然後 fuzzer 就會拼命嘗試 N=7, N=8, N=9... 每個都是「新的」，造成**路徑爆炸（path explosion）**，種子佇列無限膨脹。

Bucketing 的洞見：迴圈從 5 次到 6 次，在同一個桶裡，不是新行為；但從 3 次跳到 7 次（跨桶），可能確實是不同的行為模式。桶的邊界是倍數關係，捕捉的是「數量級的變化」，不是精確差異。

---

## NEVERZERO：u8 overflow 的陷阱

`uint8_t` 最大值是 255，再加 1 會 overflow 歸零：

```
bitmap[idx] = 255
bitmap[idx]++
→ bitmap[idx] = 0  ← 和「從未被執行」無法區分！
```

這個 edge 從此在 fuzzer 眼中「消失」了，即使它每次都被執行。

**AFL++ 的修復**：NEVERZERO 機制。在每次 increment 後，如果值變成 0，就強制改成 1：

```c
/* instrumentation/afl-compiler-rt.o.c */
__afl_area_ptr[idx]++;
if (__afl_area_ptr[idx] == 0)
    __afl_area_ptr[idx] = 1;  /* NEVERZERO */
```

或者用一個更聰明的無分支版本：

```c
/* +1 之後如果是 0，用 or 把最低位強制設為 1 */
uint8_t v = __afl_area_ptr[idx] + 1;
__afl_area_ptr[idx] = v | (v == 0);
```

原始 AFL 沒有 NEVERZERO，這是 AFL++ 的改進點之一。如果你在跑長時間的 fuzzing session，這個 bug 在原始 AFL 上可能悄悄讓某些高頻 edge 消失。

---

## Collision 的實際代價

理論上，64KB bitmap 能表示 65536 條不同的 edge。問題是 edge ID 是 compile time 隨機分配的，兩條不同的 edge 可能映射到同一個 bitmap index（collision）。

**CollAFL（S&P 2018）的量化分析**：

> 在 LAVA-M benchmark 上，AFL 的 bitmap collision 率達到 **3–10%**，在較複雜的 target（如 libpng、libtiff）更高。每一次 collision 代表 fuzzer 把兩條不同的 edge 誤認為同一條，降低了 coverage 的精確度。

Collision 的傷害不是直接的 crash，而是**微妙的覆蓋率低估**：fuzzer 以為某個路徑已經被探索過，但其實只是另一條路徑的 collision 擋住了信號。

AFL++ 的 LTO 模式解決了這個問題（Ch 7 詳解），代價是更複雜的 build 流程。

---

## 進一步用法：Context-Sensitive Coverage

標準的 edge coverage 有一個盲點：相同的 edge A→B，從不同的 call context 觸發，AFL 認為是同一件事。

```c
void vuln(int x) { ... }  /* 有 edge A→B */

void caller1() { vuln(1); }   /* context 1 */
void caller2() { vuln(100); } /* context 2 */
```

A→B 的 bitmap 格子在兩種 context 下都加一，fuzzer 無法分辨這兩種呼叫是否已經被測試過。

**Context-Sensitive Coverage**（`-DAFL_LLVM_CTX=1`）在計算 bitmap index 時，把 call stack 的資訊也混進去：

```c
/* 概念：把 caller 的 ID 混入 XOR 計算 */
uint32_t idx = (__afl_prev_loc ^ cur_loc) ^ context_hash;
```

啟用方式：

```bash
AFL_LLVM_CTX=1 afl-clang-fast -o target target.c
```

代價：每個 call/return 都需要更新 context hash，overhead 增加約 10-20%。只在 context 確實影響 bug 觸發時才開。

---

## 對比與取捨

| | Block Coverage | Edge Coverage | Path Coverage |
|---|---|---|---|
| 紀錄什麼 | 哪些 block 被執行 | 哪些控制流邊被走過 | 從入口到出口的完整路徑序列 |
| 區分 A→B vs C→B | 否 | **是** | 是 |
| 區分迴圈次數 | 有限（靠桶） | 有限（靠桶） | 完整 |
| 狀態空間大小 | 線性（block 數） | 線性（edge 數） | 指數（路徑數） |
| AFL 中的用法 | 不用 | **主要信號** | 不用（太大）|
| Collision 風險 | 低（bucket 少）| 中（64KB bitmap）| N/A |

---

## 踩雷集錦

**1. 「edge 就是 branch（分支）」**

很多人以為 edge 只有 `if/else` 或 `switch` 產生，但實際上任何控制流轉移都是 edge，包括：
- 無條件跳躍（function call、loop back edge）
- 函式呼叫本身（caller → callee 也是 edge）
- 異常處理路徑

把 edge 想成「CFG 上的有向箭頭」，不只是 branch。

**2. 「bitmap 越大越好，覆蓋率越準確」**

很多人以為調大 `MAP_SIZE` 能無限減少 collision，但實際上代價不只是 build 設定：
- 超過 L2 cache 大小後，`has_new_bits()` 每次掃描都有大量 cache miss
- 10,000 exec/sec 下，L2 miss penalty 累積起來能讓吞吐量掉 30-50%
- 除非 target 的 edge 數真的 >> 65536，否則不要調大

**3. 「hit count 越精確，fuzzer 找 bug 的能力越強」**

很多人以為精確 hit count 比 bucketing 更好，但實際上精確計數會讓路徑爆炸：
- 迴圈體執行 100 次和 101 次，在語義上很少有差異
- Bucketing 的倍增邊界捕捉「行為模式的質變」，不追蹤無意義的量變
- 精確計數的 fuzzer 種子佇列會無限膨脹，實際 bug-finding 效率更差

**4. `MAP_SIZE` 在 instrumentation 和 fuzzer 之間必須一致**

很多人以為只要設定 `AFL_MAP_SIZE` 就行，但實際上：
- instrumentation（編譯時）決定 target 寫入 shm 的大小
- fuzzer（執行時）決定讀取 shm 的大小
- 兩邊不一致不會報錯，只會 silent corruption：fuzzer 讀到的是錯誤的 bitmap 區域

解法：在 build script 和 fuzzing 啟動腳本裡都設定相同的 `AFL_MAP_SIZE`。

**5. 「NEVERZERO 只是細節優化，不影響結果」**

很多人以為 u8 overflow 很少發生，但實際上：
- 高頻路徑（如 main loop、parser 核心）每秒可能執行數百萬次
- 長時間 fuzzing session（24小時+）中，overflow 幾乎必然發生
- 沒有 NEVERZERO 的 fuzzer 會在 session 後期悄悄「遺忘」某些 edge

---

## 進階：再往深一層

### 直接分析 AFL++ 的 bitmap 輸出

AFL++ 在每次執行後把 trace_bits 寫入 `<output_dir>/.cur_input`（以及若干 debug 檔案）。你可以用以下方式直接檢視 bitmap：

```python
#!/usr/bin/env python3
# 讀取 AFL++ 的 virgin_bits（從未被觸發過的 edge 的 bitmap）
# 執行前先讓 AFL++ 跑一段時間

import struct
import sys

MAP_SIZE = 65536

def read_bitmap(path):
    with open(path, 'rb') as f:
        data = f.read(MAP_SIZE)
    if len(data) < MAP_SIZE:
        data += b'\xff' * (MAP_SIZE - len(data))
    return data

def bitmap_stats(bitmap):
    hit  = sum(1 for b in bitmap if b == 0x00)  # virgin_bits: 0x00 = edge seen
    miss = sum(1 for b in bitmap if b != 0x00)  # virgin_bits: 0xff = never seen
    density = hit / MAP_SIZE * 100
    print(f"Edges seen:    {hit:6d} / {MAP_SIZE} ({density:.2f}%)")
    print(f"Edges unseen:  {miss:6d} / {MAP_SIZE}")

if __name__ == '__main__':
    # virgin_bits 在 afl-fuzz 記憶體中，需要透過 AFL++ 的 cmin 工具間接取得
    # 或直接讀 fuzz_bitmap（afl-fuzz 的 -B 選項輸出）
    path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/afl_out/default/fuzz_bitmap'
    bm = read_bitmap(path)
    bitmap_stats(bm)

    # 找出 hit count 分布
    print("\nHit count distribution (after bucketing):")
    buckets = {1:0, 2:0, 4:0, 8:0, 16:0, 32:0, 64:0, 128:0}
    for b in bm:
        if b in buckets:
            buckets[b] += 1
    for k, v in sorted(buckets.items()):
        bar = '#' * (v // 10)
        print(f"  Bucket {k:3d}: {v:5d} {bar}")
```

執行方式：

```bash
# 先跑 AFL++
afl-fuzz -i seeds/ -o out/ -- ./target @@

# 另開終端，等幾分鐘後讀 bitmap
python3 bitmap_stats.py out/default/fuzz_bitmap
```

範例輸出（一個中等複雜度的 target，跑 10 分鐘後）：

```
Edges seen:    3842 / 65536 (5.86%)
Edges unseen: 61694 / 65536

Hit count distribution (after bucketing):
  Bucket   1:  1203 ##########
  Bucket   2:   847 ########
  Bucket   4:   612 ######
  Bucket   8:   489 ####
  Bucket  16:   312 ###
  Bucket  32:   201 ##
  Bucket  64:   124 #
  Bucket 128:    54
```

`map density`（5.86%）是 AFL++ UI 右上角顯示的數字，代表有多少 bitmap slot 被觸碰過。太低代表種子不夠多樣，太高（> 70%）代表 collision 嚴重。

---

## 動手練習

**練習 1：觀察 edge coverage 的增長曲線**

```bash
# 用 AFL++ 自帶的 test case 跑 5 分鐘，觀察 map density 的變化
mkdir -p /tmp/practice/seeds
echo "hello" > /tmp/practice/seeds/seed1
echo "test"  > /tmp/practice/seeds/seed2

# 用一個有條件分支的簡單程式
cat > /tmp/practice/target.c << 'EOF'
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    char *s = argv[1];
    if (strlen(s) > 3 && s[0] == 'A') {
        if (s[1] == 'B') {
            if (s[2] == 'C') {
                printf("found ABC\n");
                if (s[3] == 'D') {
                    printf("found ABCD!\n");
                    __builtin_trap();  /* 模擬 crash */
                }
            }
        }
    }
    return 0;
}
EOF

# 用 afl-clang-fast 編譯（PCGUARD 模式）
afl-clang-fast -o /tmp/practice/target /tmp/practice/target.c

# 跑 5 分鐘，觀察 map density
afl-fuzz -i /tmp/practice/seeds -o /tmp/practice/out \
    -- /tmp/practice/target @@
```

觀察：`map density` 何時停止增長？種子佇列大小和 density 的關係是什麼？

**練習 2：手動計算 edge collision 機率**

如果 target 有 N 條 edge，bitmap 大小是 M，假設 ID 均勻隨機分配，用**生日悖論**估算 collision 機率：

```
P(至少一次 collision) ≈ 1 - e^(-N²/(2M))

N = 1000 edges, M = 65536:
P ≈ 1 - e^(-1000²/(2*65536)) = 1 - e^(-7.63) ≈ 99.95%

這代表幾乎必然有 collision，但大多數只是少數幾對 edge 共用同一個 slot。
```

計算：如果要讓 collision 機率低於 1%，bitmap 需要多大？

---

## 本章重點整理

- AFL 使用 **edge coverage**（而非 block）是因為它能區分「從哪裡跳過來」，用 `prev_loc ^ cur_loc` 計算 bitmap index，`>> 1` 打破 XOR 對稱性讓 A→B 和 B→A 對應不同的 slot。
- **Hit count bucketing**（8 個倍增桶）避免路徑爆炸：fuzzer 只關心「迴圈次數的數量級是否改變」，不追蹤精確值；**NEVERZERO** 防止高頻 edge 因 u8 overflow 從 bitmap 中消失。
- 64KB bitmap 是 cache-vs-collision 的工程取捨；CollAFL 量化了 3–10% 的 collision 率；LTO 模式（Ch 7）透過 link-time 全局 CFG 分析做到 collision-free。

---

## 自我檢核

1. 如果 A 的 ID 是 42、B 的 ID 是 100，分別計算 A→B 和 B→A 對應的 bitmap index（用 `cur_loc >> 1` 機制）。為什麼不右移的話這兩條邊會碰撞？

2. Hit count bucketing 的設計假設「倍增邊界捕捉質變」。你能舉一個反例，說明某種 bug 必須靠精確 hit count 才能找到？（提示：想想 off-by-one 的迴圈邊界）

3. 在 NEVERZERO 的無分支實作 `v | (v == 0)` 裡，`(v == 0)` 的值是 C 語言的 int，但 `v` 是 uint8_t。在什麼情況下這個運算結果才是正確的？型別轉換發生在哪裡？

4. 如果你把 `AFL_MAP_SIZE` 從 65536 調成 262144，target 的 cache miss 率會怎麼變化？對 10,000 exec/sec 的 fuzzer 影響有多大？（用 L2 cache 約 256KB 估算）

5. Context-sensitive coverage 在計算 bitmap index 時混入了 context hash。這會不會讓兩條原本不 collision 的 edge 因為 context 相同而變成 collision？怎麼設計才能避免？

---

## 延伸閱讀

**CollAFL: Path Sensitive Fuzzing（Gan et al., S&P 2018）**
- **核心貢獻**：量化了 AFL bitmap collision 的實際代價，提出三種 collision-free 的 instrumentation 策略。
- **讀哪裡**：Section 3（"Path Coverage Accuracy" — bitmap collision 的定量分析）
- **和本章的關聯**：直接數據來源，解釋為什麼 3-10% 的 collision 率在大型 target 上是真實問題。

**AFL Technical Details（Michał Zalewski / lcamtuf）**
- **核心貢獻**：AFL 作者本人解釋設計決策，包括 bitmap 大小、hit count bucketing 的直覺。
- **讀哪裡**：`docs/` 目錄下的 "The coverage bitmap" 和 "The instrumentation" 節（AFL 原始 repo 或 AFL++ fork 裡的歷史文件）
- **和本章的關聯**：所有設計決策的一手來源，可以對照理解 `>> 1` trick 的原始動機。

**AFL++ `docs/fuzzing_in_depth.md`**
- **核心貢獻**：官方 AFL++ 文件，涵蓋 MAP_SIZE 調整的實際建議和 collision 分析工具。
- **讀哪裡**：搜尋 "MAP_SIZE" 那一節，以及 `afl-showmap` 的用法
- **和本章的關聯**：實際操作層面的補充，包括何時需要調整 bitmap 大小、如何用 `afl-showmap` 直接觀察 edge 數量。

**SanitizerCoverage（LLVM 官方文件）**
- **核心貢獻**：PCGUARD 模式底層依賴的 LLVM infrastructure，解釋 `__sanitizer_cov_trace_pc_guard` 的設計。
- **讀哪裡**：https://clang.llvm.org/docs/SanitizerCoverage.html — "Tracing PCs with guards" 節
- **和本章的關聯**：理解 AFL++ PCGUARD 模式為什麼比 CLASSIC 更穩定（Ch 6 會深入）。

---

→ [下一章：Ch 6 Compile-time Instrumentation](./06-compile-time-instrumentation.md)
