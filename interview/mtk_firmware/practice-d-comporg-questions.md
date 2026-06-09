# 練習 D — 計組綜合（cache + pipeline）

> **目標**：把 Part 4（Ch 29–35）的計組考點綜合驗收，尤其 cache 和 pipeline 的**計算題**（面試必考、必拿分）。先遮答案，計算題拿紙筆算。

> **環境**：概念 + 計算。前置：Part 4 全部。

## 怎麼用這份練習

計組題分「概念」（口頭答）和「計算」（紙筆算）。計算題是必拿分的——cache 拆位址、page fault 數、pipeline cycle 數，練到熟。先自己算，再對答案。

---

## 第一部分：概念（口頭答）

### Q1（Ch 29）二補數、有號範圍、浮點為什麼不能用 `==` 比較？

<details>
<summary>要點</summary>

二補數：負數=正數反轉+1；8-bit 範圍 -128~+127（不對稱，負多一個）；浮點不精確（0.1+0.2≠0.3，二進位無法精確表示），比較用 `fabs(a-b)<epsilon`。Ch 29。
</details>

### Q2（Ch 30）兩種 locality？direct/set-associative/fully 對映差異？

<details>
<summary>要點</summary>

temporal（剛用再用）+ spatial（附近也用，抓 cache line）。direct（1 固定位置/衝突多）、set-associative（N-way/折衷/最常用）、fully（任意/衝突少但慢貴）。Ch 30。
</details>

### Q3（Ch 31）pipeline 五階段？三種 hazard 怎麼解？

<details>
<summary>要點</summary>

IF/ID/EX/MEM/WB。data hazard（forwarding/stall，load-use 要 stall）、control hazard（branch prediction）、structural hazard（加硬體/分離 I-D cache）。Ch 31。
</details>

### Q4（Ch 30, 34, 35）write-back 對 DMA 和多核有什麼問題？

<details>
<summary>要點</summary>

write-back cache 和記憶體可能不一致。DMA 直接讀記憶體 → 讀到舊值（要軟體 flush/invalidate，Ch 34）；多核各 cache → 不一致（硬體 MESI 自動解，Ch 35）。DMA 要軟體、多核硬體自動——不同。Ch 30/34/35。
</details>

### Q5（Ch 33）gcc 四階段？static vs dynamic library？

<details>
<summary>要點</summary>

預處理→編譯（C→組語）→組譯（→機器碼.o）→連結。static（複製進執行檔/大/獨立）vs dynamic（執行時載入/小/共享/依賴庫）。韌體常 static。Ch 33。
</details>

---

## 第二部分：cache 計算（紙筆算）

### Q6（Ch 30）cache 16KB、direct mapped、line 32 bytes、32-bit 位址。算 offset/index/tag bits

<details>
<summary>解答</summary>

```
offset：line 32B = 2^5 → 5 bits
總行數 = 16KB / 32B = 512 行
direct mapped（1-way）→ set 數 = 512 = 2^9 → index = 9 bits
tag = 32 - 9 - 5 = 18 bits
```

direct mapped 的 set 數 = 總行數（每 set 1 行）。Ch 30。
</details>

### Q7（Ch 30）同樣 16KB、line 32B，但改 4-way set associative。算 index/tag

<details>
<summary>解答</summary>

```
offset：5 bits（line 不變）
總行數 = 16KB / 32B = 512
set 數 = 512 / 4(way) = 128 = 2^7 → index = 7 bits
tag = 32 - 7 - 5 = 20 bits
```

對比 Q6（direct）：4-way 的 index 變少（7 vs 9）、tag 變多（20 vs 18）——因為 way 多了，set 數少了。Ch 30。
</details>

### Q8（Ch 30）一段程式 cache hit rate 90%，cache 存取 1ns、記憶體存取 100ns。平均存取時間？

<details>
<summary>解答</summary>

```
平均存取時間 = hit_rate × cache時間 + miss_rate × (記憶體時間)
            = 0.9 × 1 + 0.1 × (100)        ← 簡化：miss 時花記憶體時間
            = 0.9 + 10 = 10.9 ns

（更精確的模型 miss 時 = cache + 記憶體：0.9×1 + 0.1×(1+100) = 0.9 + 10.1 = 11 ns，
  看題目定義。重點是公式：hit 比例 × 快 + miss 比例 × 慢）
```

關鍵：hit rate 對效能影響巨大——90% hit 平均約 10.9ns，但若 hit rate 99%：0.99×1+0.01×100 = 1.99ns（快 5 倍）！所以 cache 友善的程式（高 hit rate，Ch 30 的 locality）超重要。Ch 30。
</details>

### Q9（Ch 26）reference string `7 0 1 2 0 3 0 4 2 3`，3 frames，算 FIFO page fault 數（複習 OS）

<details>
<summary>解答</summary>

FIFO（換最早載入）：
```
7[7]F 0[70]F 1[701]F 2[201]F(換7) 0[201]hit 3[231]F(換0) 0[230]F(換1) 4[430]F(換2) 2[420]F(換3) 3[423]F(換0)
→ 9 faults
```

（這是 Ch 26 的 page replacement，跨 OS/計組——記憶體階層的延伸。）Ch 26/30。
</details>

---

## 第三部分：pipeline 計算

### Q10（Ch 31）5 階段 pipeline，跑 10 條指令（無 hazard），需幾個 cycle？對比沒 pipeline？

<details>
<summary>解答</summary>

**有 pipeline（無 hazard）**：第一條要 5 cycle 填滿 pipeline，之後每 cycle 完成一條 → `5 + (10-1) = 14 cycles`。

公式：`k 階段 pipeline 跑 n 條指令 = k + (n-1) cycles`（理想）。

**沒 pipeline**：每條 5 cycle，循序 → `10 × 5 = 50 cycles`。

加速比 ≈ 50/14 ≈ 3.6 倍（n 越大越接近 5 倍 = 階段數）。Ch 31。
</details>

### Q11（Ch 31）這段有什麼 hazard？怎麼解？

```asm
    LW   R1, 0(R2)      ; load R1
    ADD  R3, R1, R4     ; 用 R1
    SUB  R5, R3, R6     ; 用 R3
```

<details>
<summary>解答</summary>

兩個 data hazard：
1. `LW R1` → `ADD ...R1`：**load-use hazard**——load 的 R1 要 MEM 階段才有，ADD 的 EX 要用，差一拍 → **forwarding 解不了，要 stall 一拍**（Ch 31）。
2. `ADD R3` → `SUB ...R3`：一般 data hazard → **forwarding 解**（ADD 的 EX 結果轉發給 SUB 的 EX，不用 stall）。

關鍵：load-use 要 stall（forwarding 也來不及）；一般 RAW（讀-寫相依）forwarding 就能解。編譯器會重排把無關指令插在 LW 和 ADD 之間填那個 stall。Ch 31。
</details>

## 自評與弱點

| 題 | 章 | 考點 |
|---|---|---|
| Q1 | Ch 29 | 二補數/浮點 |
| Q2 | Ch 30 | locality/cache 對映 |
| Q3 | Ch 31 | pipeline/hazard |
| Q4 | Ch 30,34,35 | cache 一致性（DMA/多核）|
| Q5 | Ch 33 | gcc 四階段/library |
| Q6-7 | Ch 30 | cache 位址拆解 |
| Q8 | Ch 30 | 平均存取時間 |
| Q9 | Ch 26 | page fault（OS）|
| Q10-11 | Ch 31 | pipeline cycle/hazard |

- **cache 計算（Q6-8）算錯** → Ch 30 重看，offset 由 line、index 由 set 數（要除 way）、tag 剩下。這是必拿分計算題。
- **pipeline 計算（Q10-11）** → 記公式 `k+(n-1)`、認得 load-use hazard 要 stall。
- **概念（Q1-5）說不清** → 對應章重看。

## 如果你卡住了

1. **cache 拆位址**：先算 offset（line 大小 = 2^offset）、再算 index（set 數 = 總行數 / way，= 2^index）、tag 剩下。
2. **平均存取時間**：hit比例 × 快時間 + miss比例 × 慢時間。
3. **pipeline cycle**：`k 階段 n 指令 = k + (n-1)`（無 hazard）；有 hazard 加 stall。
4. **hazard 判斷**：後指令用前指令結果 = data hazard；load 後緊接用 = load-use（要 stall）；分支 = control hazard。

## 自我檢核

- [ ] 我能拆 cache 位址（offset/index/tag bits），知道 index 要除以 way
- [ ] 我能算平均記憶體存取時間（hit/miss 加權）
- [ ] 我能算 pipeline 跑 n 條指令的 cycle 數（k+n-1）
- [ ] 我能認出 load-use hazard（要 stall）vs 一般 data hazard（forwarding 解）
- [ ] 我能口頭解釋 cache 一致性（DMA 軟體 vs 多核硬體）、gcc 四階段

Part 4（計組）綜合驗收完成。Part 5 進入資料結構與演算法——linked list、tree、sorting 的手寫題。

→ [Ch 36 array / linked list](./36-array-linked-list.md)
