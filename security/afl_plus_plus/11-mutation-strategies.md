# Ch 11 — Mutation 策略：Deterministic、Havoc、Splice

> **目標**：理解 AFL++ 的三層 mutation 策略（deterministic / havoc / splice），能說清楚每種策略在什麼情況下有效。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼需要這個？

Mutation 是 fuzzing 的核心動作，但「怎麼改一個 seed」這個問題比看起來複雜。原版 AFL 用了三個層次的 mutation 策略，AFL++ 繼承並大幅擴充。

如果你不理解這三層的設計哲學，你就沒辦法回答這些實際問題：

- 為什麼 AFL++ 4.x 預設關掉 deterministic stages，但某些場景下開啟它反而更好？
- `AFL_DISABLE_TRIM=1` 什麼時候值得設？
- 你的 custom mutator 應該插在 pipeline 的哪個位置？
- Havoc 的 `havoc_max_mult` 應該調大還是調小？

## 先建立直覺

把 mutation pipeline 想成三個層次，各自有不同的「暴力程度」和「覆蓋寬度」：

```
Deterministic（確定性）
  → 每次只動一個地方，按固定順序窮舉所有可能性
  → 像是逐格翻遍字典，保證不遺漏但非常慢
  → AFL++ 4.x 預設關閉

Havoc（混沌）
  → 隨機選幾個操作，疊加施加
  → 像是隨機撕幾頁、塗幾塊、折幾折——不系統但速度快
  → AFL++ 的主力 stage

Splice（拼接）
  → 把兩個 seed 各切一半，拼接成新的 seed
  → 把兩本書的前半段和後半段合在一起，可能出現奇特的組合
  → 最少用，但有時能突破 havoc 的局限
```

## 核心概念一：Deterministic Stages（確定性階段）

Deterministic stages 對 seed 做系統性、可重現的 mutation。AFL++ 把它分成幾個子階段：

### Bit Flips（位元翻轉）

逐一翻轉每個位元，窗口從 1 bit 到 8 bits：

```
原始：  01001000 01100101 01101100 ...
1-bit flip（第 0 位）：  11001000 01100101 01101100 ...
1-bit flip（第 1 位）：  00001000 01100101 01101100 ...
...（對每個 bit 都做）
2-bit flip（位元 0-1）：  10001000 01100101 01101100 ...
...
```

各個 bit flip 變體：
- `bitflip 1/1`：每次翻 1 bit，滑動 1 bit
- `bitflip 2/1`：每次翻 2 bits，滑動 1 bit
- `bitflip 4/1`：每次翻 4 bits，滑動 1 bit
- `bitflip 8/8`：每次翻 1 byte，滑動 1 byte（等效於 byte 的每個可能值）
- `bitflip 16/8`：每次翻 2 bytes，滑動 1 byte
- `bitflip 32/8`：每次翻 4 bytes，滑動 1 byte

對一個 N byte 的 seed，光是 bit flip 就要執行大約 `8N + 7N + 5N + N + N + N ≈ 23N` 次。

### Arithmetic（算術操作）

對每個 byte、word（2 bytes）、dword（4 bytes）做 +/- 1 到 +/- 35 的加減：

```c
// 對 byte offset 0，嘗試加 1 到 35，再減 1 到 35
for (j = 1; j <= ARITH_MAX; j++) {
  u8 r = in_buf[i] ^ (in_buf[i] + j);  // 只有 bit pattern 不同才執行
  if (!could_be_bitflip(r)) {           // 避免和 bitflip 重複
    out_buf[i] = in_buf[i] + j;
    if (common_fuzz_stuff(...)) goto abandon_entry;
  }
}
```

`ARITH_MAX` 預設是 35。AFL++ 只做「和 bitflip 結果不同」的操作（`could_be_bitflip()` 過濾），避免重複執行同樣效果的 mutation。

### Known Interesting Values（已知有趣值）

把 byte/word/dword 替換成容易觸發邊界問題的特殊值：

```c
// 8-bit interesting values
static s8 interesting_8[] = {
  CHAR_MIN,              // -128
  -1, 0, 1,
  CHAR_MAX,              // 127
  0x7f, 0x80
};

// 16-bit interesting values
static s16 interesting_16[] = {
  -128, -1, 0, 1, 128, 255, 256,
  SHORT_MIN,             // -32768
  SHORT_MAX,             // 32767
  0x7fff, 0x8000, 0xffff
};

// 32-bit interesting values
static s32 interesting_32[] = {
  -128, -1, 0, 1, 128, 255, 256,
  SHORT_MIN, SHORT_MAX,
  0xffff, 0x10000,
  INT_MIN,               // -2147483648
  INT_MAX,               // 2147483647
  0x80000000, 0xffffffff
};
```

這些值是根據實際 bug 模式歸納出來的：整數溢位、off-by-one、符號轉換等最常在這些邊界上出現。

### Deterministic 為什麼預設關閉

AFL++ 4.x 預設不跑 deterministic stages。理由：

1. **太慢**：對 1KB 的 seed，deterministic 需要跑數千次執行才能完成一輪。
2. **Havoc 已經涵蓋**：Havoc 的操作集包含了 bit flip、arithmetic、interesting values。給夠多次數的 havoc，它能達到同等甚至更好的 coverage。
3. **現代 target 不適合**：Deterministic 在簡單的文字格式（如 AFL 最初設計針對的場景）效果好。複雜格式（PDF、ELF、壓縮格式）的 seed 結構不適合逐 bit 翻轉。

如果你的 target 是簡單的 C struct 格式，或者你要做深度的 protocol mutation，可以用 `-D` 啟用 deterministic：

```bash
afl-fuzz -D -i seeds/ -o out/ -- ./target @@
```

## 底層機制：它是怎麼運作的？

```
┌──────────────────────────────────────┐
│         選取 seed（來自 queue）        │
└────────────────┬─────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────┐
│              Trim                    │
│  若 trim_done=0，先做 inline trim    │
│  找到最小的等效 seed                  │
└────────────────┬─────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────┐
│    Deterministic（若 -D 啟用）        │
│  bitflip 1/1 → bitflip 2/1 → ...    │
│  arithmetic 8 → arithmetic 16 → ... │
│  interesting 8 → interesting 16 → ...│
│  每個操作都執行 target，檢查 bitmap   │
└────────────────┬─────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────┐
│              Havoc                   │
│  隨機決定要疊加幾個操作               │
│  （受 perf_score 和 havoc_max_mult   │
│    決定的 iteration 上限控制）        │
│                                      │
│  每次疊加：                           │
│    ① 隨機選一個 mutation 操作         │
│    ② 對 out_buf 執行這個操作          │
│    ③ 重複，直到疊加次數達到隨機上限   │
│  然後執行 target，檢查 bitmap        │
└────────────────┬─────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────┐
│              Splice                  │
│  若 queue size ≥ 2，從 queue 隨機    │
│  挑另一個 seed                       │
│  找兩個 seed 的差異點                │
│  在差異點附近做拼接                   │
│  對拼接後的 seed 再跑一輪 Havoc      │
└──────────────────────────────────────┘
```

## 核心概念二：Havoc（混沌階段）

Havoc 是 AFL++ 的主力 mutation stage。它的邏輯在 `src/afl-fuzz-mutators.c` 的 `fuzz_one_original()` 函數裡：

```c
// src/afl-fuzz-mutators.c（高度簡化，描述邏輯）

// 決定這輪 havoc 要跑多少次 mutation
u32 use_stacking = 1 << (1 + rand_below(afl, HAVOC_STACK_POW2));
// use_stacking 通常是 2, 4, 8, 16 ... 的某個值

for (stage_cur = 0; stage_cur < stage_max; stage_cur++) {
  // 每次 iteration 都重新隨機疊加 use_stacking 個操作
  for (u32 s = 0; s < use_stacking; s++) {
    switch (rand_below(afl, 15 + /* custom mutator 加的操作數 */)) {
      case 0:   // flip a single bit
        FLIP_BIT(out_buf, rand_below(afl, temp_len << 3));
        break;
      case 1:   // set byte to interesting value
        out_buf[rand_below(afl, temp_len)] =
          interesting_8[rand_below(afl, sizeof(interesting_8))];
        break;
      case 2:   // set word to interesting value (random endian)
        // ...
      case 3:   // set dword to interesting value
        // ...
      case 4:   // randomly subtract from byte
        out_buf[rand_below(afl, temp_len)] -= 1 + rand_below(afl, ARITH_MAX);
        break;
      case 5:   // randomly add to byte
        out_buf[rand_below(afl, temp_len)] += 1 + rand_below(afl, ARITH_MAX);
        break;
      case 6:   // randomly subtract from word
        // ...（little-endian 或 big-endian，隨機選）
      case 7:   // randomly add to word
        // ...
      case 8:   // randomly subtract from dword
        // ...
      case 9:   // randomly add to dword
        // ...
      case 10:  // randomly overwrite byte with random value
        out_buf[rand_below(afl, temp_len)] ^= 1 + rand_below(afl, 255);
        break;
      case 11:  // delete bytes
        if (temp_len < 2) break;
        // 從隨機位置刪除隨機長度的 bytes
        // ...
      case 12:  // clone bytes / insert random bytes
        // 在隨機位置插入一段複製的 bytes 或全新隨機的 bytes
        // ...
      case 13:  // overwrite bytes with a chunk from same buffer
        // 把一段 bytes 覆蓋到另一個隨機位置
        // ...
      case 14:  // overwrite bytes with extra (from queue or user dict)
        // 如果有 dictionary，從 dictionary 取一個 token 插入
        // ...
    }
  }
  // 疊加完之後，執行 target
  if (common_fuzz_stuff(afl, out_buf, temp_len)) goto abandon_entry;
}
```

### Havoc 支援的完整操作清單

AFL++ 4.x 的 havoc stage 包含約 20 種操作：

| # | 操作 | 說明 |
|---|------|------|
| 0 | Bit flip | 翻轉隨機一個 bit |
| 1 | Set interesting byte | 置換成 8-bit interesting value |
| 2 | Set interesting word | 置換成 16-bit interesting value（隨機 endian） |
| 3 | Set interesting dword | 置換成 32-bit interesting value（隨機 endian） |
| 4 | Random subtract byte | byte 隨機減 1–35 |
| 5 | Random add byte | byte 隨機加 1–35 |
| 6 | Random subtract word | word 隨機減 1–35（隨機 endian） |
| 7 | Random add word | word 隨機加 1–35（隨機 endian） |
| 8 | Random subtract dword | dword 隨機減 1–35（隨機 endian） |
| 9 | Random add dword | dword 隨機加 1–35（隨機 endian） |
| 10 | XOR byte | byte XOR 隨機非零值 |
| 11 | Delete bytes | 刪除隨機位置的隨機長度 bytes |
| 12 | Clone / insert bytes | 複製一段 bytes 到隨機位置，或插入隨機 bytes |
| 13 | Overwrite with chunk | 把一段 bytes 覆蓋到另一個位置 |
| 14 | Overwrite with extra | 用 dictionary token 覆蓋隨機位置（有 dict 時） |
| 15 | Insert extra | 在隨機位置插入 dictionary token（有 dict 時） |
| 16 | Splice chunk | 把 queue 裡另一個 seed 的片段拼接進來（mini-splice） |
| 17–N | Custom mutator 操作 | 若有 custom mutator，其額外操作插入這裡 |

### `havoc_max_mult` 的作用

```c
// 計算這個 seed 在 havoc stage 的總 iteration 數
stage_max = (doing_det ? HAVOC_CYCLES_INIT : HAVOC_CYCLES) *
            perf_score / afl->havoc_div / 100;
```

`perf_score`（來自 `calculate_score()`，Ch 12 詳述）決定 havoc 跑多少次。Score 高的 seed 跑更多次 havoc。`havoc_max_mult` 是 perf_score 的上限倍數，預設是 256（即 perf_score 最多是 base 的 256 倍）。

## 核心概念三：Splice（拼接）

Splice 把兩個 seed 做「基因交叉」：

```c
// src/afl-fuzz-mutators.c（簡化）
void do_splice(afl_state_t *afl) {
  // 從 queue 隨機選另一個 seed
  struct queue_entry *target = pick_from_queue(afl);

  // 找兩個 seed 的 content 差異點
  // 找到第一個不同的 byte 位置 f_diff
  // 找到最後一個不同的 byte 位置 l_diff
  locate_diffs(in_buf, new_buf, MIN(len, target->len), &f_diff, &l_diff);

  // 在 f_diff 和 l_diff 之間隨機選一個 split_at 點
  split_at = f_diff + rand_below(afl, l_diff - f_diff);

  // 拼接：前半用原 seed，後半用 target seed
  memcpy(new_buf, in_buf, split_at);
  memcpy(new_buf + split_at, new_buf2 + split_at, len - split_at);

  // 對拼接後的 seed 再跑 havoc
  // ...
}
```

Splice 的直覺：兩個 seed 分別觸發不同的程式路徑，把它們拼接在一起，可能創造出能同時觸發兩條路徑的組合，繞過格式驗證或啟動新的邏輯。

Splice 的限制：需要 queue 裡有至少 2 個 seed。在 fuzzing 剛開始、corpus 只有 1 個 seed 時，splice 不會跑。

## 對比與取捨

| 面向 | Deterministic | Havoc | Splice |
|------|--------------|-------|--------|
| 執行次數 | 極多（`O(N * ARITH_MAX * 操作種類)`） | 中等（受 perf_score 控制） | 少（Havoc 次數的子集） |
| 每次改動範圍 | 固定（1 個操作，1 個位置） | 隨機（2–16 個操作疊加） | 固定（1 個切割點的 crossover） |
| 適合的 bug 類型 | 單點邊界條件（整數溢位、off-by-one） | 廣泛，大多數 bug | 格式組合、parser 的 context-dependent 邏輯 |
| 需要 corpus size | 1 | 1 | ≥ 2 |
| Reproducibility | 完全可重現（給定 seed 和 offset，結果確定） | 不可重現（依賴 PRNG 狀態） | 不可重現 |
| AFL++ 4.x 預設 | 關閉（需 `-D`） | 開啟，主力 | 開啟，輔助 |
| CPU cost per coverage unit | 高（很多重複操作） | 低（更高效的覆蓋率成長） | 中 |

## 踩雷集錦

**1. 「開 Deterministic 一定更好」**

在大多數現代 target 上，havoc 找 bug 的速度比開啟 deterministic 更快。Deterministic 的問題是它在大 seed 上的執行次數是 `O(N)`，把時間花在重複的、低效的操作上。AFL++ 的官方建議：除非你的 target 非常小（< 100 bytes）且是簡單格式，否則不需要 `-D`。

**2. Splice 需要 corpus 裡有 ≥ 2 個 seeds**

剛開始 fuzzing，corpus 只有 1 個 seed 時，AFL++ 會跳過 splice stage（因為無法選另一個 seed 做 crossover）。這不是 bug，是正確行為。等 corpus 增長到 2 個以上 seed 後，splice 自動啟用。

**3. Havoc 的隨機性讓 crash 的 reproducibility 很難**

找到 crash 之後，AFL++ 會把 crash 觸發的 input 存到 `out/crashes/`。但這個 input 是 mutation 後的最終結果，不是「mutation 步驟的記錄」。你無法重現「AFL++ 是怎麼從原始 seed 走到這個 crash 的」——只能重現「這個 crash input 本身」。要分析 crash 的觸發路徑，用 `afl-tmin` 先縮小 input，再手動 debug。

**4. Havoc 的 iteration 上限隱藏在 perf_score 裡**

很多人以為 havoc 跑固定次數。實際上 havoc 的 iteration 數由 `perf_score` 決定（Ch 12 詳述）。如果你的 seed 的 `perf_score` 很低（exec time 慢、bitmap 小），這個 seed 的 havoc 輪次非常少。這是 power schedule 的設計，不是 bug。

**5. 不要隨意調大 `havoc_max_mult`**

有人為了讓 havoc 跑更多次，把 `AFL_HAVOC_MAX_MULT` 調大。但這會讓高 score seed 的 havoc 時間爆炸，排擠其他 seed 的執行機會。除非你有明確的理由（例如你知道某個特定 seed 非常接近 crash），否則保留預設值。

## 進階：再往深一層

**`AFL_DISABLE_TRIM=1` 的使用場景**

Trim 在大多數情況下有益，但有一個場景例外：你的 target 對 input 長度敏感，截短後觸發的 coverage 雖然相同，但某些長度相關的 bug 被隱藏了（例如堆積緩衝區溢位的大小恰好取決於 input 長度）。這時設定 `AFL_DISABLE_TRIM=1` 保留原始 seed 大小。

**MOpt 和 mutation 操作的動態加權**

預設 havoc 的 20 種操作是均等機率的。MOpt（Mutation Optimization，Ch 12 詳述）用 Particle Swarm Optimization（PSO）動態調整每種操作的執行比例：如果某種操作最近更容易發現新 coverage，它的比例就提高。啟用方式：

```bash
afl-fuzz -p mmopt -i seeds/ -o out/ -- ./target @@
```

**Custom Mutator 的插入點**

Custom mutator（Ch 18 詳述）透過 C API 插入 mutation pipeline。它可以：
- 完全取代 havoc（`afl_custom_fuzz()` 回傳非空指標時）
- 作為 havoc 的前置步驟（`afl_custom_pre_save()`）
- 在 havoc 的操作清單裡增加新操作

最常見的用法是「grammar-aware pre-processing + AFL++ havoc 後處理」：custom mutator 先確保 mutation 後的 seed 符合格式語法，再讓 havoc 在語法合法的範圍內做 bit-level mutation。

## 動手練習

1. **觀察 Deterministic vs Havoc 的差異**：

   ```bash
   # 建立一個簡單的 target（讀取固定格式的 C struct）
   cat > target.c << 'EOF'
   #include <stdlib.h>
   #include <string.h>
   int main(int argc, char **argv) {
     FILE *f = fopen(argv[1], "rb");
     unsigned char buf[16];
     fread(buf, 1, 16, f);
     fclose(f);
     if (buf[0] == 0xDE && buf[1] == 0xAD) {
       if (*(unsigned int *)(buf + 2) > 0x7FFFFFFF) {
         __builtin_trap();  // crash!
       }
     }
     return 0;
   }
   EOF
   afl-clang-fast -o target target.c

   # 跑 15 分鐘，不開 Deterministic
   timeout 900 afl-fuzz -i seeds/ -o out_havoc/ -- ./target @@

   # 重設，開 Deterministic 跑同樣時間
   timeout 900 afl-fuzz -D -i seeds/ -o out_det/ -- ./target @@

   # 比較兩者的 coverage 和 crash 發現速度
   ```

2. **觀察 Havoc 的疊加效果**：

   ```bash
   # 用 afl-showmap 追蹤一個 seed 在多次 mutation 後的 coverage 變化
   afl-showmap -o /tmp/base_map -- ./target seeds/seed_01
   # 看 out/default/queue/ 裡的新 seed，它的 coverage 比原始 seed 多了哪些 edge
   ```

3. **驗證 Splice 需要 ≥ 2 個 seeds**：

   ```bash
   # 只放 1 個 seed，觀察 AFL++ 的統計畫面
   cp seeds/seed_01 /tmp/single_seed/
   afl-fuzz -i /tmp/single_seed/ -o out_single/ -- ./target @@ &
   # 看 stats 裡的 splice_execs 是否為 0
   cat out_single/default/fuzzer_stats | grep splice
   ```

## 本章重點整理

- AFL++ 的 mutation pipeline 分三層：Deterministic（窮舉，預設關閉）、Havoc（隨機疊加多種操作，主力）、Splice（crossover，輔助）；各層有不同的適用場景和 CPU cost
- Havoc 支援約 20 種操作，每次 iteration 隨機疊加 2–16 個；iteration 總次數由 `perf_score` 控制（Ch 12），不是固定的
- Splice 需要 corpus ≥ 2 個 seeds 才能執行；Custom mutator API 可以把自訂邏輯插入 havoc 的操作清單，不必替換整個 pipeline

## 自我檢核

1. Deterministic stage 的 `bitflip 8/8` 和 `interesting 8` 的差別是什麼？它們各自在什麼情況下比另一個更有效？

2. 解釋 havoc 的「stacking」概念：`use_stacking` 是怎麼決定的？為什麼疊加多個操作比只做一個操作更有效率？

3. 如果你的 target 的 crash 只有在 input 恰好是 4 bytes 的倍數時才能觸發，你應該考慮設定哪個環境變數？為什麼？

4. Splice 做的 crossover 是在哪個位置切割？AFL++ 怎麼決定 `split_at` 點？

5. 你想為一個 HTTP request fuzzer 加入 grammar-aware mutation（確保 mutation 後的 request 是合法 HTTP）。你應該用 custom mutator 的哪個 hook？

## 延伸閱讀

- **[AFL 技術白皮書](https://lcamtuf.coredump.cx/afl/technical_details.txt)** — lcamtuf
  - **核心貢獻**：mutation 策略的原始設計文件，說明 bit flip 和 interesting values 選擇的 rationale
  - **讀哪裡**："Mutating the input" 節，約 400 字，10 分鐘內讀完
  - **和本章的關聯**：本章所有 deterministic 操作的設計思路都來自這裡

- **[Learning-Guided Network Fuzzing for Testing Stateful Network Protocol Implementations (AFLNet)](https://dl.acm.org/doi/10.1145/3293882.3330575)** — Pham, Böhme, Santosa, Căciulescu, Roychoudhury, ISSTA 2019
  - **核心貢獻**：把 AFL 的 mutation 策略延伸到有狀態的 network protocol，展示如何在 message-level 而非 byte-level 做 mutation
  - **讀哪裡**：Section 3（mutation 策略的調整）和 Section 4（和原版 AFL havoc 的對比實驗）
  - **和本章的關聯**：展示本章的 havoc/splice 在 stateful protocol 場景下的限制，以及怎麼用 custom mutator 思路解決

- **[AFL++ `src/afl-fuzz-mutators.c`](https://github.com/AFLplusplus/AFLplusplus/blob/stable/src/afl-fuzz-mutators.c)** — AFL++ 官方
  - **核心貢獻**：mutation pipeline 的實際實作，所有 case 的 inline 注釋解釋設計決策
  - **讀哪裡**：搜尋 `HAVOC_STACK_POW2` 找到 havoc stacking 邏輯；搜尋 `case 0:` 找到每個操作的實作
  - **和本章的關聯**：本章描述的操作清單就來自這個檔案，可以直接對照

→ [Ch 12 — Power Schedule：能量分配的藝術](./12-power-schedule.md)
