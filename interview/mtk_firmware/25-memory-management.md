# Ch 25 — 記憶體管理 paging/segmentation

> **目標**：搞懂 OS 怎麼管理記憶體——virtual memory 概念、paging（分頁）、segmentation（分段）、page table、TLB、以及內部/外部碎片。這是 OS 記憶體題的核心，串 Ch 11（程式記憶體佈局）、Ch 26（置換）。

> **環境**：概念為主。前置：Ch 11（記憶體模型）、Ch 20（process 位址空間）。

## 為什麼考這個

「virtual memory 是什麼」「paging vs segmentation」「internal vs external fragmentation」是 OS 面試常考題。它測你懂不懂「OS 怎麼讓每個 process 以為自己獨佔記憶體、怎麼把虛擬位址轉成實體位址」。這也關係到 Ch 11 的記憶體佈局（那些位址其實是虛擬的）。

## 先建立直覺：每個 process 以為自己獨佔記憶體

```
   問題：多個 process 同時在記憶體，怎麼讓它們：
   - 互不干擾（A 不能讀寫 B 的記憶體）？
   - 都以為自己從位址 0 開始有一整片連續記憶體？
   - 實體記憶體不夠時還能跑（比實體 RAM 大的程式）？

   解法：virtual memory（虛擬記憶體）
   - 每個 process 有自己的「虛擬位址空間」（以為獨佔、連續、從 0 開始）
   - OS + 硬體（MMU）把「虛擬位址」轉成「實體位址」
   - 不同 process 的虛擬位址對映到不同實體位址 → 隔離
```

關鍵：**virtual memory 讓每個 process 看到一個獨立、連續、私有的位址空間（虛擬），實際上由 OS 對映到分散的實體記憶體。** Ch 11 講的程式記憶體佈局（stack/heap/...的位址）其實都是**虛擬位址**——這就是為什麼每個 process 都能用同樣的位址而不衝突。

## paging（分頁）

最主流的虛擬記憶體實作。把記憶體切成固定大小的塊：

```
   虛擬位址空間 → 切成固定大小的 page（頁，如 4KB）
   實體記憶體   → 切成同樣大小的 frame（頁框）

   page table（分頁表）：記錄「每個虛擬 page → 哪個實體 frame」

   虛擬位址 = [page number | offset]
   轉換：用 page number 查 page table 得到 frame number，
        實體位址 = [frame number | offset]
```

```
   虛擬位址  ──查 page table──> 實體位址
   page 5  ────────────────>  frame 12
   page 6  ────────────────>  frame 3   ← 虛擬連續，實體可分散！
```

paging 的好處：

- **沒有 external fragmentation**：page 固定大小，任何空閒 frame 都能用（不會有「夠大但不連續」的碎片）。
- **虛擬連續、實體分散**：process 的虛擬位址空間連續，但對映的實體 frame 可以分散——靈活利用記憶體。
- **方便分享/保護**：page table 可設每頁的權限（讀/寫/執行）、共享頁。

代價：有 **internal fragmentation**（最後一頁可能用不滿，浪費 < 1 page）；每次存取要查 page table（多一次記憶體存取，所以有 TLB，下面）。

## TLB（Translation Lookaside Buffer）

問題：每次存取記憶體都要先查 page table（在記憶體裡）→ 等於每次存取要兩次記憶體存取（先查表、再存資料）→ 慢一倍。

解法：**TLB——page table 的快取**（在 MMU 裡，很快）。

```
   存取記憶體：
   1. 先查 TLB（快）：有對映（TLB hit）→ 直接得 frame，快！
   2. TLB 沒有（TLB miss）→ 查 page table（慢）→ 把結果存進 TLB（下次快）
```

TLB 利用 **locality（區域性）**——程式常存取相近的位址，所以最近用過的對映在 TLB 裡，多數是 hit。TLB 是讓 paging 不慢的關鍵（呼應 Ch 30 cache 的概念——TLB 是「位址轉換的 cache」）。

> context switch 與 TLB：切換 process 時 page table 變了，TLB 的內容失效要 flush（或用 ASID 標記）——這是 Ch 20「process 切換比 thread 貴」的原因之一（thread 同位址空間不用 flush TLB）。

## segmentation（分段）

另一種方式——按「程式的邏輯區塊」切（不是固定大小）：

```
   把位址空間按邏輯切成 segment：code 段、data 段、stack 段、heap 段...
   每個 segment 有自己的「基底位址 + 長度」
   虛擬位址 = [segment number | offset]
   轉換：查 segment table 得基底，實體位址 = 基底 + offset（檢查 offset < 長度）
```

segmentation 的特點：

- 符合程式的**邏輯結構**（每段是有意義的單元）。
- 方便保護/共享（按段設權限，如 code 段唯讀）。
- 但段大小不固定 → 有 **external fragmentation**（記憶體被切得零碎，有空間但不連續放不下）。

現代多用 paging（或 paging + segmentation 混合，如 x86）。純 segmentation 較少見，但概念對比常考。

## 內部碎片 vs 外部碎片（必考對比）

```
   internal fragmentation（內部碎片）：
   配置的空間「比需要的大」，多出來的部分浪費在「已配置的塊內部」
   例：paging——要 5KB，配 2 個 4KB page（8KB），內部浪費 3KB
       或固定大小配置——配 16 byte 但只用 10，內部浪費 6

   external fragmentation（外部碎片）：
   空閒記憶體「總量夠，但不連續」，無法滿足一個連續的大請求
   例：segmentation / 變動大小配置——空閒空間被切成很多小塊，
       總共有 100KB 空閒，但都是 1KB 的碎塊，放不下 50KB 的請求
```

對比與哪種有：

| | internal fragmentation | external fragmentation |
|---|---|---|
| 浪費在 | 已配置塊的內部（用不滿） | 配置塊之間（零碎空閒） |
| paging | **有**（最後一頁用不滿） | **無**（固定大小） |
| segmentation / 變動配置 | 無（按需配） | **有**（零碎空閒） |
| 解法 | 用小一點的固定塊 | compaction（壓縮整理）、paging |

記法：**internal = 「塊內部」浪費（固定大小配置，如 paging）；external = 「塊之間」零碎（變動大小配置，如 segmentation）。paging 沒有 external fragmentation 是它的主要優點。**

## 考古題詳解

### Q1：什麼是 virtual memory？解決什麼問題？

<details>
<summary>詳解</summary>

virtual memory：每個 process 有自己的虛擬位址空間（以為獨佔、連續、從 0 開始），OS+MMU 把虛擬位址轉成實體位址。

解決：
1. **隔離**：不同 process 的虛擬位址映到不同實體，互不干擾（安全）。
2. **抽象**：每個 process 以為獨佔連續記憶體，不用管實體佈局。
3. **超量**：程式可比實體 RAM 大（用 demand paging + swap，Ch 26）。

Ch 11 的記憶體佈局位址其實都是虛擬位址。

**考點**：virtual memory 概念，必考。
</details>

### Q2：paging 是什麼？怎麼把虛擬位址轉實體？

<details>
<summary>詳解</summary>

paging：把虛擬空間切成固定大小的 page、實體切成同大小的 frame，用 page table 記錄 page→frame 對映。

轉換：虛擬位址 = [page number | offset]，用 page number 查 page table 得 frame number，實體位址 = [frame number | offset]。

好處：無 external fragmentation（固定大小）、虛擬連續實體可分散。代價：有 internal fragmentation（最後頁用不滿）、查表開銷（用 TLB 加速）。

**考點**：paging + 位址轉換，必考。
</details>

### Q3：TLB 是什麼？為什麼需要它？

<details>
<summary>詳解</summary>

TLB（Translation Lookaside Buffer）：page table 的快取（在 MMU 裡，很快）。

需要它的原因：每次存取記憶體都要查 page table（在記憶體裡）→ 等於每次存取要兩次記憶體存取（查表+存資料）→ 慢。TLB 快取最近用過的對映，TLB hit 時直接得 frame（快），靠 locality 多數是 hit。

context switch 換 process 時 page table 變、TLB 要 flush（Ch 20 process 切換貴的原因）。

**考點**：TLB 作用，連結 paging 效能與 context switch。
</details>

### Q4：internal 和 external fragmentation 差在哪？paging 有哪種？

<details>
<summary>詳解</summary>

- **internal fragmentation**：配置的塊「比需要大」，浪費在塊內部（如 paging 最後一頁用不滿）。
- **external fragmentation**：空閒記憶體總量夠但不連續，放不下大請求（如 segmentation/變動配置的零碎空閒）。

**paging 有 internal（最後頁用不滿）、無 external（固定大小，任何空閒 frame 都能用）。** 「無 external fragmentation」是 paging 的主要優點。segmentation 反過來（無 internal、有 external）。

**考點**：兩種碎片 + paging 哪種，必考對比。
</details>

### Q5：paging 和 segmentation 差在哪？

<details>
<summary>詳解</summary>

- **paging**：固定大小切（page/frame），對程式透明（不管邏輯結構）；無 external fragmentation、有 internal；用 page table。
- **segmentation**：按邏輯區塊切（code/data/stack 段），符合程式結構、方便按段保護；有 external fragmentation、無 internal；用 segment table。

現代多用 paging（或混合）。segmentation 的優點是邏輯清晰、易保護共享，缺點是 external fragmentation。

**考點**：paging vs segmentation 對比。
</details>

## 踩雷集錦

1. **以為程式用的是實體位址**：是虛擬位址（Ch 11 的佈局都是虛擬的），OS+MMU 轉成實體。
2. **internal/external fragmentation 搞反**：internal = 塊內部浪費（paging）；external = 塊之間零碎（segmentation）。
3. **以為 paging 有 external fragmentation**：沒有（固定大小是它的優點）。它有 internal。
4. **忘了 TLB 的作用**：沒 TLB，每次存取要查 page table（兩次記憶體存取）。TLB 快取對映。
5. **不知道 TLB 和 context switch 的關係**：換 process 要 flush TLB（Ch 20 process 切換貴）。
6. **混淆 page 和 frame**：page 是虛擬的、frame 是實體的，page table 記它們的對映。

## 速記

- **virtual memory**：每 process 獨立虛擬位址空間（以為獨佔連續），OS+MMU 轉實體 → 隔離+抽象+超量。Ch 11 位址都是虛擬的。
- **paging**：固定大小切（page↔frame），page table 對映；無 external、有 internal fragmentation。
- **TLB**：page table 的快取（MMU 裡），靠 locality 加速位址轉換；context switch 要 flush（Ch 20）。
- **segmentation**：按邏輯切（code/data/stack 段）；有 external、無 internal；易保護共享。
- **internal**（塊內部浪費，paging 最後頁）vs **external**（塊之間零碎，segmentation）。paging 無 external 是優點。

## 自我檢核

- [ ] virtual memory 是什麼？解決哪三個問題？Ch 11 的位址是虛擬還實體？
- [ ] paging 怎麼把虛擬位址轉實體？為什麼沒有 external fragmentation？
- [ ] TLB 是什麼？為什麼需要它？和 context switch 有什麼關係？
- [ ] internal 和 external fragmentation 差在哪？paging 有哪種？
- [ ] paging 和 segmentation 各的優缺點？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 9 Main Memory
  - **讀哪幾章**：9.2（連續配置/碎片）、9.3（paging）、9.4（segmentation）、TLB。
  - **和本章的關聯**：記憶體管理的標準教材，本章權威。

- **《OSTEP》** — Paging（18）、TLBs（19）、Segmentation（16）
  - **讀哪幾章**：16（segmentation）、18（paging）、19（TLB）。
  - **為什麼值得讀**：把 paging/TLB/segmentation 講得最清楚，含碎片分析。

paging 是「位址怎麼對映」，下一章是「實體記憶體不夠怎麼辦」——virtual memory 的置換與 demand paging。

→ [Ch 26 virtual memory 與置換](./26-virtual-memory.md)
