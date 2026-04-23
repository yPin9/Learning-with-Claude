# Ch 9 — Mutation 策略：deterministic、havoc、splice

> 目標：完整盤點 AFL 的 mutation 家族 — deterministic 階段的 bitflip / arith / known interesting values，havoc 階段的隨機 stacking，splice 的跨 seed 拼接；解釋 AFL++ 為什麼預設跳過 deterministic。

## 兩個階段

AFL 對一個 queue entry 的 mutation 分**兩階段**：

```
queue_entry ──▶ [deterministic] ──▶ [havoc + splice] ──▶ 下一個 queue_entry
                    ^                      ^
                窮舉式、有限              隨機、可無限跑
                每種 op 跑完即停          直到 fuzz_level 耗盡
```

兩階段差別：

| 特徵 | Deterministic | Havoc |
|---|---|---|
| 目標 | 窮舉式試小擾動 | 隨機大擾動堆疊 |
| 有終止條件 | 有（op 全跑完就停） | 沒有（照 energy 跑） |
| 可重現 | 完全可重現 | 隨機 |
| 單次變化量 | 小（1 bit ~ 幾 byte） | 可能非常大 |

## Deterministic 階段

對 entry 依序套用一堆 op，每個 op 會把 input 從頭走到尾試一遍。

### `bitflip 1/1`、`bitflip 2/1`、`bitflip 4/1`

一次翻 1 bit、2 bits、4 bits（連續）。1/1 表示「每次 1 bit，滑動 1 bit」；4/1 是「每次 4 bits 連續翻，滑動 1 bit」。

input 長度 N bits 就要跑 N 次單 bit flip。如果 input 1000 bytes = 8000 bits，光 bitflip 1/1 就 8000 次執行。

```
input:  0x41 0x42 0x43    (A, B, C)
bitflip 1/1:
  試 1:  0x40 0x42 0x43   ← bit 0 翻轉
  試 2:  0x43 0x42 0x43   ← bit 1 翻轉
  ...
  試 24: 0x41 0x42 0xC3   ← 最後一個 bit
```

### `bitflip 8/8`、`bitflip 16/8`、`bitflip 32/8`

一次翻 1、2、4 byte（8 bits、16 bits、32 bits），滑動 1 byte。實際上就是「NOT 掉」：

```
bitflip 8/8:
  試 1:  0xBE 0x42 0x43   (0x41 XOR 0xFF)
  試 2:  0x41 0xBD 0x43
  試 3:  0x41 0x42 0xBC
```

### `arith 8/8`、`arith 16/8`、`arith 32/8`

對每個 1/2/4-byte slice 做加減 1 到 35 的運算（AFL 預設 `ARITH_MAX = 35`）：

```
arith 8/8 on position 0 of [0x10, 0x20, 0x30]:
  +1:  0x11 0x20 0x30
  +2:  0x12 0x20 0x30
  ...
  +35: 0x33 0x20 0x30
  -1:  0x0F 0x20 0x30
  ...
  -35: 0xED 0x20 0x30
```

16/8 和 32/8 版會嘗試 little-endian 和 big-endian 兩種解讀。

**為什麼是 35**？heuristic — 多數 off-by-one、off-by-few 的 bug 在 ±35 範圍內能觸發，再大就重複 bitflip 能做的事。

### `interest 8/8`、`interest 16/8`、`interest 32/8`

把每個 slice 替換成「有趣的 magic value」。AFL 內建清單（`INTERESTING_8`、`INTERESTING_16`、`INTERESTING_32`）：

```c
static s8  interesting_8[]  = { -128, -1, 0, 1, 16, 32, 64, 100, 127 };
static s16 interesting_16[] = { -32768, -129, 128, 255, 256, 512, ... };
static s32 interesting_32[] = { -2147483648, -100663046, -32769, 32768, ... };
```

把 input 中每個 byte / word / dword 換成這些值試試。`-1`、`INT_MIN`、`INT_MAX` 這些邊界是 off-by-one、signed overflow 常見觸發器。

### `dict` ops

如果你給了 `.dict` 檔（Ch 11），還會有 `dict-override`、`dict-insert` 這類 op，把 dict token 插入 input 或覆蓋 input 片段。

### Deterministic 的總體成本

假設 input 100 bytes = 800 bits：

| Op | 次數 |
|---|---|
| bitflip 1/1 | 800 |
| bitflip 2/1 | 799 |
| bitflip 4/1 | 797 |
| bitflip 8/8 | 100 |
| bitflip 16/8 | 99 |
| bitflip 32/8 | 97 |
| arith 8/8 | 100 × 70 = 7000 |
| arith 16/8 | 99 × 70 × 2 = ~14000 |
| arith 32/8 | 97 × 70 × 2 = ~14000 |
| interest 8/8 | 100 × 9 = 900 |
| ... | ... |

總計 **>4 萬次** target 執行。對 100 bytes input 這還算少，長一點的 input 可以跑到幾百萬次，**非常昂貴**。

## 為什麼 AFL++ 預設跳過 deterministic

這裡有一個違反直覺的結論：

**實測上，跳過 deterministic 能找到更多 bug**。

原因：
1. deterministic 雖然覆蓋率全，但對長 input 成本爆炸 — 可能一個 entry 卡好幾分鐘做完 det，這段時間 havoc 可以產生數千種變異。
2. havoc 的隨機性其實涵蓋了 deterministic 的多數可能（bitflip、arith、interesting values 都是 havoc 隨機抽 op 中的一種）。
3. Fuzzer 的本質是 throughput 遊戲 — 花同樣時間，誰能試更多路徑就贏。

從 AFL++ 2.60 左右開始，預設 `-D` 行為是關閉 deterministic。你可以手動 `-D` 開回來（flag 其實是「disable」），但一般不需要。

**但**：對「deep structure 的短 input」（像 protobuf schema 的每 byte 都意義不同），deterministic 還是有價值。看情況。

## Havoc 階段

這是 AFL 的主力 mutator。每次 iteration 做：

1. 隨機選一個 stack 層數（1–128），這決定要堆疊幾個 mutation。
2. 在這個 input 上重複「隨機選一種 mutation 做一次」：

```python
# 虛擬程式碼
def havoc_mutate(input, stack_depth):
    for _ in range(stack_depth):
        op = random.choice(HAVOC_OPS)
        input = op(input)
    return input
```

### `HAVOC_OPS` 列表

AFL++ 的 havoc operators（不完整）：

- Flip 1 bit / 2 bits / 4 bits
- Interesting 8/16/32 replace
- Add/subtract random 1/2/4 byte value
- Set random byte
- Delete chunk of bytes
- Insert random bytes at random position
- Duplicate chunk
- Overwrite chunk with another chunk
- Clone chunk
- Insert a dictionary token (如果有 dict)
- CMPLOG replacement (如果 CMPLOG 有資料)
- Custom mutator 的 mutation（如果 load 了）

### stack_depth 的意義

stack_depth 越大，對 input 的變化越激烈。小 stack 像「微調」，大 stack 像「重組」。隨機 stack depth 讓 fuzzer 自然涵蓋不同程度的擾動。

### 為什麼這招有效

havoc 看起來很隨意，但它滿足幾個重要性質：

- **比 deterministic 快**：每 iteration 一次 mutation stack，成本恆定。
- **覆蓋面廣**：理論上只要夠多次 iteration，任何 deterministic 的變化都會被隨機到。
- **能組合**：deterministic 每 op 獨立，havoc 把多個 op 堆疊 — 有機會觸發 deterministic 摸不到的「需要兩個 byte 同時改」的 branch。

## Splice

Havoc 的變形：**從 queue 裡挑另一個 entry，把兩者各一半拼起來，再 havoc**。

```
input A:  [aaaaaaa|bbbbbbb]       切一半
input B:  [ccccccc|ddddddd]       切另一半
spliced:  [aaaaaaa|ddddddd]       換前半
        → havoc on spliced
```

切點有一定條件（通常挑兩 input 差異較大的地方），之後跑 havoc。splice 的價值：

- **跨 seed 基因交換**：兩個 seed 可能各自發現了不同 state transition 的 input 前綴，splice 有機會讓兩種 state 同時出現。
- **逃出 local optimum**：如果 havoc 在某個 input 上困住了，splice 能把搜索點 jump 到另一個 input 附近。

AFL 進入 splice 的條件：havoc 跑完一輪還沒找到新 coverage，且 queue 裡 entry 數 > 1。

## MOpt：給 havoc 更聰明的 op 選擇

`MOpt`（USENIX Security 2019）觀察：havoc 隨機均勻選 op 浪費了。不同時期、不同 target 對不同 op 的效益差很大 — 某些 target 靠 bitflip 就能爆，某些靠 chunk copy 才有用。

MOpt 把 op 選擇從「uniform random」改成 **multi-armed bandit**：記錄每個 op 的歷史成效，按期望收益調整抽樣機率。

AFL++ 支援 `-L 0` 啟用 MOpt。對某些 target 提升顯著，對某些沒差。

## 幾個進階 mutator（點到為止）

- **Redqueen / CmpLog mutation**：Ch 12 主題，特別針對 magic bytes。
- **Grammar mutator**（`custom_mutators/grammar_mutator/`）：依 grammar 定義產生結構化 input，然後 mutate grammar tree 而非 byte stream。
- **Gramatron**：grammar 的另一種實作，用 PDA (pushdown automaton)。
- **Honggfuzz-style mutator**（`custom_mutators/honggfuzz/`）：把 Honggfuzz 的 mutation 移植過來。
- **Input-to-state (I2S)**：REDQUEEN idea，Ch 12 細講。

## 效率量級

粗略數字：

| 階段 | 每個 entry 的 exec 數量級 |
|---|---|
| deterministic（短 input） | $10^4$–$10^5$ |
| deterministic（長 input） | $10^5$–$10^7$（慢到離譜） |
| havoc 一輪 | 取決於 fuzz_level，通常幾百到幾千 |
| splice | 數百 |

這就是為什麼 havoc 預設開、deterministic 預設關。

## 常見誤解

- **「deterministic 比 havoc 好，因為它是 systematic」**：不。systematic 不等於 efficient。fuzzing 是 throughput 遊戲。
- **「splice 隨便亂接會產生破格 input」**：是啊，而且這剛好是 feature — AFL 不 care input 合法，只看 coverage。接出來的破格 input 有時候才是 bug 的入口。
- **「MOpt 絕對比均勻抽 op 好」**：不絕對。MOpt 要時間累積統計，短跑 target 可能還沒熱起來就結束。

## 自我檢核

- [ ] 能列舉 deterministic 的主要 op 族群（bitflip / arith / interest / dict）
- [ ] 能解釋為什麼 AFL++ 預設關掉 deterministic
- [ ] 能描述 havoc 的「stack 多個隨機 op」結構
- [ ] 知道 splice 在做什麼、什麼時候觸發
- [ ] 大致聽過 MOpt 和 grammar mutator

下一章講怎麼決定「每個 queue entry 該分到多少 fuzz energy」— 也就是 power schedule。

→ [Ch 10 Power schedule：誰該分到更多能量](./10-power-schedule.md)
