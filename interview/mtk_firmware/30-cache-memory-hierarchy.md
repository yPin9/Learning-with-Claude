# Ch 30 — 記憶體階層與 cache

> **目標**：搞懂記憶體階層、locality（區域性）、cache 對映方式（direct/set-associative/full）、hit/miss、write back/through、以及 cache 計算題。這是計組最高頻的效能考點。

> **環境**：概念 + 計算。前置：Ch 11（記憶體）、Ch 25（TLB 是位址轉換的 cache）。

## 為什麼考這個

CPU 比記憶體快上百倍——cache 是「讓 CPU 不被慢記憶體拖死」的關鍵。理解 cache 直接關係到寫出快的程式（locality）、也是計組面試的核心計算題（算 hit rate、cache 大小）。韌體也要懂 cache（DMA 一致性、效能）。

## 先建立直覺：金字塔——快又貴的少、慢又便宜的多

```
   記憶體階層（由快貴小 到 慢便宜大）：
   ┌──────────┐
   │ 暫存器    │  最快（< 1 ns）、最小（幾十個）
   ├──────────┤
   │ L1 cache  │  很快（~1 ns）、小（幾十 KB）
   │ L2 cache  │  快（~10 ns）、中（幾百 KB ~ MB）
   │ L3 cache  │  較快、較大（幾 MB ~ 幾十 MB）
   ├──────────┤
   │ 主記憶體   │  慢（~100 ns）、大（GB）
   ├──────────┤
   │ 磁碟/SSD  │  最慢（µs~ms）、最大（TB）
   └──────────┘
```

核心矛盾：**快的記憶體貴又小、慢的便宜又大。** 解法是「階層」——把「最近/最可能用的」放快的層，讓 CPU 多數時候從快的層拿到（cache hit），少數才去慢的層（cache miss）。這靠 **locality（區域性）**——程式的存取有規律。

## locality（區域性）：cache 有效的前提

```
   temporal locality（時間區域性）：剛用過的，很可能再用
      例：迴圈變數 i、剛存取的資料 → 留 cache，下次快

   spatial locality（空間區域性）：用了某位址，附近的也很可能用
      例：陣列循序存取、程式碼循序執行 → cache 一次抓一整塊（cache line）
```

cache 利用這兩種 locality：把「剛用的」（temporal）和「附近的」（spatial，一次抓一條 cache line）放 cache。**寫出有 locality 的程式 = cache hit 高 = 快**（如循序走陣列 vs 跳著走，下面 Q）。

## cache 的基本運作

```
   CPU 要讀位址 X：
   1. 先查 cache：有（cache hit）→ 直接拿，快！
   2. 沒有（cache miss）→ 從主記憶體抓「包含 X 的一整塊（cache line/block，如 64 bytes）」
                          放進 cache（利用 spatial locality）→ 再給 CPU

   cache line（快取行）：cache 的最小單位，一次抓一整塊（不是一個 byte）
   → 抓 X 時連 X 附近的也抓進來（spatial locality）
```

**hit rate（命中率）** 是 cache 效能的關鍵指標。平均存取時間 = hit_rate × cache時間 + miss_rate × 記憶體時間。hit rate 越高越快。

## cache 對映方式（計算題核心）

主記憶體的一塊（block）能放進 cache 的哪裡？三種對映：

### Direct Mapped（直接對映）

每個記憶體 block 只能放 cache 的**一個固定位置**（`block number % cache行數`）。

```
   優點：簡單、快（只查一個位置）
   缺點：衝突多——兩個常用的 block 剛好映到同一行 → 互相踢出（conflict miss）
```

### Fully Associative（全關聯）

每個 block 能放 cache 的**任何位置**。

```
   優點：衝突最少（彈性最大）
   缺點：查找慢（要比對所有行）、硬體貴
```

### Set Associative（組關聯）— 折衷，最常用

cache 分成多個 set，每個 set 有 N 行（N-way）。block 先映到一個 set（`% set數`），在 set 內可放任一行。

```
   N-way set associative：每個 set 有 N 個位置
   - direct mapped = 1-way（每 set 1 行）
   - fully associative = 全部 1 個 set
   - 折衷：4-way、8-way 常見（衝突少 + 查找不太慢）
```

對比：

| | direct | set-associative | fully |
|---|---|---|---|
| block 能放 | 1 個固定位置 | set 內任一（N 個）| 任何位置 |
| 衝突 | 多 | 中 | 少 |
| 查找速度 | 快 | 中 | 慢 |
| 硬體成本 | 低 | 中 | 高 |

實際 CPU 多用 **set-associative**（如 8-way）——平衡衝突與成本。

## cache 位址拆解（計算題）

位址被拆成三部分來定位 cache：

```
   位址 = [ tag | index | offset ]
            標籤   索引    區塊內偏移

   offset：在 cache line 內的哪個 byte（line 大小 = 2^offset_bits）
   index：哪個 set（set 數 = 2^index_bits）
   tag：辨識「這行存的是哪個 block」（比對 tag 確認 hit）
```

計算範例：

```
   cache：32 KB、8-way set associative、cache line 64 bytes，32-bit 位址
   - offset bits：line 64 bytes = 2^6 → offset = 6 bits
   - 總行數 = 32KB / 64B = 512 行
   - set 數 = 512 行 / 8 way = 64 sets = 2^6 → index = 6 bits
   - tag bits = 32 - 6(index) - 6(offset) = 20 bits
```

計算題會問「offset/index/tag 各幾 bits」「cache 多大」——記住：**offset 由 line 大小決定、index 由 set 數決定、tag 是剩下的**。

## write 策略

CPU 寫資料時，cache 和記憶體怎麼同步：

```
   write-through（寫穿）：寫 cache 同時寫記憶體
      優點：cache 和記憶體永遠一致（簡單）
      缺點：每次寫都碰記憶體（慢）

   write-back（寫回）：只寫 cache，標記 dirty，等該行被換出時才寫回記憶體
      優點：少寫記憶體（快，多次寫同一行只最後寫回一次）
      缺點：cache 和記憶體可能不一致（DMA/多核要注意，Ch 35）
```

多數現代 cache 用 **write-back**（快）。但「cache 和記憶體不一致」對 DMA（Ch 34）和多核（Ch 35）是問題——DMA 直接讀記憶體可能讀到舊值（cache 裡有新的還沒寫回）→ 要 cache 一致性處理（韌體要注意！）。

## 三種 cache miss（3C）

```
   compulsory miss（強制/冷啟動）：第一次存取，cache 本來就沒有（無法避免）
   capacity miss（容量）：cache 太小，放不下所有要用的 → 被擠出去
   conflict miss（衝突）：對映方式導致衝突（direct mapped 最嚴重，多個 block 映同位置）
```

面試問「cache miss 種類」答 3C：compulsory（冷）、capacity（太小）、conflict（對映衝突）。set-associative 比 direct 減少 conflict miss。

## 考古題詳解

### Q1：什麼是 locality？cache 怎麼利用它？

<details>
<summary>詳解</summary>

- **temporal locality**：剛用過的很可能再用 → cache 留著最近用的。
- **spatial locality**：用了某位址，附近的也可能用 → cache 一次抓一整條 line（含附近資料）。

cache 靠這兩者：把最近用的（temporal）和附近的（spatial，整條 line）放 cache，多數存取從 cache 拿到（hit）→ 快。寫有 locality 的程式 = 高 hit rate。

**考點**：locality 兩種 + cache 怎麼利用，必考。
</details>

### Q2：direct mapped、set associative、fully associative 差在哪？

<details>
<summary>詳解</summary>

- **direct mapped**：block 只能放 1 個固定位置（% 行數）。簡單快，但 conflict miss 多。
- **set associative（N-way）**：block 映到一個 set，set 內 N 個位置可放。折衷（衝突中、速度中），**最常用**。
- **fully associative**：block 可放任何位置。衝突最少，但查找慢、硬體貴。

direct = 1-way、fully = 全部一個 set，set-associative 是中間的彈性。

**考點**：三種對映對比，必考。
</details>

### Q3：寫程式時為什麼「循序存取陣列」比「跳著存取」快？

<details>
<summary>詳解</summary>

**spatial locality + cache line**。循序存取：每次 cache miss 抓一整條 line（如 64 bytes = 16 個 int），接下來 15 個都在 cache（hit），只有 1/16 是 miss。跳著存取（步長大於 cache line）：每次都 miss（抓的整條 line 只用一個就跳走）→ hit rate 低 → 慢。

經典例子：二維陣列 row-major 存取（C 是 row-major），按 row 走（連續）比按 column 走（跳）快很多。

```c
// 快（row-major，連續）：    for(i) for(j) a[i][j]
// 慢（跳著，每次跨一個 row）：for(j) for(i) a[i][j]
```

**考點**：locality 對效能的影響，實務+計組結合，高頻。
</details>

### Q4：write-through 和 write-back 差在哪？

<details>
<summary>詳解</summary>

- **write-through**：寫 cache 同時寫記憶體。一致（簡單），但每次寫都碰記憶體（慢）。
- **write-back**：只寫 cache、標 dirty，該行被換出時才寫回。快（少寫記憶體），但 cache 和記憶體可能不一致。

多數用 write-back（快）。不一致對 DMA（Ch 34）/多核（Ch 35）是問題——DMA 讀記憶體可能讀到舊值（cache 有新的沒寫回），要 cache 一致性處理。

**考點**：write 策略 + 一致性問題（連 DMA/多核）。
</details>

### Q5：cache 32KB、4-way、line 64B、32-bit 位址，算 offset/index/tag bits

<details>
<summary>詳解</summary>

```
offset：line 64B = 2^6 → 6 bits
總行數 = 32KB / 64B = 512
set 數 = 512 / 4(way) = 128 = 2^7 → index = 7 bits
tag = 32 - 7 - 6 = 19 bits
```

步驟：offset 由 line 大小（64=2^6）、index 由 set 數（總行數/way）、tag 剩下的。

**考點**：cache 位址拆解計算，計組計算題。
</details>

## 踩雷集錦

1. **以為 cache 一次抓一個 byte**：抓一整條 cache line（如 64 bytes，利用 spatial locality）。
2. **跳著存取大陣列還以為快**：步長 > cache line 時每次 miss。循序存取才有 spatial locality。
3. **direct mapped 以為沒缺點**：conflict miss 多（兩個常用 block 映同位置互踢）。set-associative 改善。
4. **write-back 忘了一致性問題**：cache 有新值沒寫回，DMA/多核讀記憶體讀到舊值（Ch 34/35）。
5. **cache 計算搞錯 bits**：offset 由 line 大小、index 由 set 數（不是總行數！要除以 way）。
6. **混淆 cache 和 TLB**：cache 快取「資料」、TLB 快取「位址轉換」（Ch 25）。概念類似（都靠 locality），但快取的東西不同。

## 速記

- 記憶體階層：暫存器 > L1/L2/L3 cache > 主記憶體 > 磁碟（快貴小 → 慢便宜大）。
- **locality**：temporal（剛用的再用）+ spatial（附近的也用，一次抓 cache line）。cache 靠它；寫有 locality 的程式 = 快。
- 對映：**direct**（1 固定位置/衝突多）、**set-associative**（N-way/折衷/最常用）、**fully**（任意/衝突少/慢貴）。
- 位址 = **tag | index | offset**：offset 由 line 大小、index 由 set 數、tag 剩下。
- **write-through**（同時寫記憶體/一致/慢）vs **write-back**（只寫 cache/快/可能不一致→DMA/多核問題）。
- miss 3C：compulsory（冷）、capacity（太小）、conflict（對映衝突）。

## 自我檢核

- [ ] 兩種 locality 是什麼？cache 怎麼利用它們（cache line）？
- [ ] direct/set-associative/fully 三種對映的差異與取捨？
- [ ] 為什麼循序存取陣列比跳著存取快？（row-major vs column-major）
- [ ] write-through 和 write-back 差在哪？write-back 對 DMA/多核有什麼問題？
- [ ] 給 cache 大小/way/line，你能算 offset/index/tag bits 嗎？

## 延伸閱讀

### 書籍

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — Ch 6 The Memory Hierarchy
  - **讀哪幾章**：6.2（locality）、6.3（cache）、6.4（cache 對映/計算）、6.5（寫 cache-friendly code）。
  - **和本章的關聯**：cache 的權威，把對映/計算/locality 講到底，本章源頭。

- **《Computer Organization and Design》** — Patterson & Hennessy — Ch 5 Memory Hierarchy
  - **讀哪幾章**：5.3（cache 基礎）、5.4（cache 效能）。
  - **為什麼值得讀**：計組教科書，cache 計算題的標準來源。

cache 是記憶體側效能，下一章是 CPU 側效能——pipeline，指令怎麼平行執行。

→ [Ch 31 CPU pipeline](./31-cpu-pipeline.md)
