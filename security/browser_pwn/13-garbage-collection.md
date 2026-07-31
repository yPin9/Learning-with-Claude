# Ch 13 — GC（Orinoco）與對利用的影響

> **目標**：搞懂 V8 的垃圾回收器（GC，代號 Orinoco）怎麼分代回收、怎麼**搬動**物件，以及這件事對利用的三個實際衝擊：記憶體佈局（spray）、位址不穩定、與 pointer compression 的耦合。這章是 Part 1、Part 2 的收尾——你已經懂物件長什麼樣、怎麼被執行，現在補上「它們在堆上怎麼生怎麼死」。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`。下面 `--trace-gc` 與 `%DebugPrint` 輸出都是真跑的。

## 為什麼需要這個？

在 `binary_exploitation` 打 glibc heap 時，你對記憶體佈局有近乎神的掌控：`malloc` 拿哪個 chunk、`free` 進哪個 bin，你算得出來。V8 的堆不是這樣——**它是 GC 管的，而 GC 會在你背後搬動物件**。

這對利用是雙面刃。壞處：你剛用 `%DebugPrint` 拿到的物件位址，下一次 GC 可能就變了，寫死位址的 exploit 會碎掉。好處：一旦你懂 GC 怎麼擺放物件，你就能**引導**佈局（heap grooming），讓你要攻擊的兩個物件相鄰。不懂 GC，你的 OOB 讀寫會像在黑箱裡亂摸；懂了，你能瞄準。

## 先建立直覺：會搬動盆栽的園丁

把 V8 的堆想成一座花園，物件是盆栽，GC 是園丁。這個園丁有兩個習慣：

1. **新盆栽放在門口的苗圃（young generation）**，因為大多數盆栽（暫時物件）很快就枯（不再被引用）。園丁常來苗圃巡一圈，把枯的丟掉、活下來的搬到後院。
2. **後院（old generation）放活得久的盆栽**。園丁很少大掃除後院，一掃就是大工程，還會**把盆栽挪位置**把空隙填起來（compaction），讓後院更緊湊。

```
   new Object()  ──►  Young Generation（苗圃，semi-space）
                        │  Scavenger：頻繁、快、把活的複製到另一半
                        │  活過幾輪 GC ──► 晉升(promote)
                        ▼
                      Old Generation（後院）
                        │  Mark-Compact：少見、慢、標記存活 + 壓實搬動
                        ▼
                      物件位址可能改變！
```

關鍵一句：**園丁會搬盆栽。你不能假設一個物件的位址永遠不變。** 這是 V8 堆和你熟的 glibc 堆最根本的差異。

## 分代假說：為什麼要分 young/old

GC 的分代設計建立在一個經驗觀察上，叫**分代假說（generational hypothesis）**：「絕大多數物件都是朝生暮死的」。一個 `for` 迴圈裡 `let t = {x: j}` 產生的物件，下一輪就沒人引用了。

既然大多數物件很快就死，與其每次都掃整個堆，不如**頻繁地只掃 young generation**（那裡死亡率最高、回收效益最好），old generation 難得才大掃一次。這是效能上的巨大勝利，也塑造了兩種不同的回收演算法。

### Young generation：Scavenger（半空間複製）

Young generation 分成兩半（semi-space）：`to-space` 和 `from-space`。新物件配置在 `to-space`。當它滿了，觸發 **Scavenge**：把 `to-space` 裡還活著的物件**複製**到 `from-space`，然後兩者角色互換。死掉的物件不用管，直接被拋棄——這就是為什麼複製式回收對「大量短命物件」極快：成本只和**存活**物件成正比。

活過幾輪 Scavenge 還沒死的物件，會被**晉升（promote）**到 old generation。

### Old generation：Mark-Compact

Old generation 用 **Mark-Compact**：先從 root（全域物件、堆疊上的引用…）出發**標記（mark）**所有可達物件，然後**壓實（compact）**——把存活物件往一端搬、填掉死物件留下的空隙，減少碎片。

親眼看一次 GC——跑一段製造大量短命物件、又保留幾個長命物件的 JS，開 `--trace-gc`：

```
$ d8 --allow-natives-syntax --trace-gc gc.js
[4081:0x94c001ac000]  10 ms: Mark-Compact 0.5 (1.8) -> 0.1 (1.8) MB, pooled: 0.0 MB,
   0.64 / 0.00 ms (average mu = 0.880, current mu = 0.880) runtime; GC in old space requested
```

逐項讀這行真實輸出：

- **`Mark-Compact`**：這次是 old space 的大回收（young 的會顯示 `Scavenge`）。
- **`0.5 (1.8) -> 0.1 (1.8) MB`**：回收前堆用了 0.5 MB（總容量 1.8），回收後降到 0.1 MB。中間那些短命物件被清掉了。
- **`0.64 / 0.00 ms`**：這次 GC 花的時間。
- **`GC in old space requested`**：觸發原因（我們用 `%CollectGarbage` 手動要求的）。

`%CollectGarbage("reason")` 是 exploit 開發時的好朋友——你可以**手動強制一次 GC**，把堆推到你想要的狀態（例如逼晉升、逼壓實），而不是被動等它自己發生。

## GC 對利用的三個實際衝擊

這章的重點不是 GC 演算法本身（那是 runtime 工程），而是**它怎麼影響你打 V8**。

### 衝擊一：位址不穩定 → 不能寫死位址

Mark-Compact 的 compaction 會搬動 old-space 物件；Scavenge 會把 young 物件複製到別處。所以：

- 你用 `%DebugPrint` 或 leak 拿到的位址，**只在「當下、下一次 GC 前」有效**。
- exploit 裡凡是用到絕對位址的地方，要嘛在使用前才即時 leak，要嘛想辦法讓目標物件**不被搬動**。
- 這也是為什麼很多 V8 exploit 會刻意**關掉 GC 干擾**或在關鍵區段避免觸發 GC——你不想在做到一半時園丁進來把盆栽全挪位。

### 衝擊二：佈局引導（heap grooming / spray）

你的 OOB 原語（Part 3）只有在「越界之後摸到的正好是你要的物件」時才有用。要讓兩個物件相鄰，你得利用 GC 的擺放規律：

- **同一輪配置、同大小的物件傾向連續擺放**。經典手法：`let arr = []; for (...) arr.push(new SomeObject())` 一口氣噴一堆同型物件，它們在 young space 裡大機率連續。
- 想讓 victim 落在你控制的物件旁邊，你要理解 Scavenge 複製的順序、晉升的時機。
- **fengshui（堆風水）**：先配置一些物件製造「洞」，free 掉其中一些，再配置目標物件讓它落進洞裡——概念和 glibc heap 的 grooming 一樣，只是機制換成 GC。

### 衝擊三：與 pointer compression 耦合

> 如果對 pointer compression 還不熟，先回看 [Ch 4 — Pointer Compression](./04-pointer-compression.md)。

V8 開啟 pointer compression 後，堆內指標是 32-bit 的壓縮值（相對 isolate/cage base 的偏移）。這和 GC 緊密耦合：整個堆被限制在一個 4GB 的 **cage** 內，GC 搬物件也只在 cage 內搬。對利用的意義：

- 你在 `%DebugPrint` 看到的 `0x2838...` 這種位址，其實是 cage base + 32-bit 壓縮偏移組出來的。
- 堆內的 tagged 指標欄位存的是壓縮值——你 OOB 蓋一個物件指標時，只要蓋低 32 bit。
- cage 把「堆內任意讀寫」框在 4GB 內，這正是 V8 Sandbox 的地基（[Ch 34](./34-v8-sandbox.md) 深談）。

## 對比：V8 GC 堆 vs glibc ptmalloc 堆

| 面向 | glibc ptmalloc（你熟的） | V8 Orinoco GC |
|---|---|---|
| 誰配置/釋放 | 你顯式 `malloc`/`free` | 引擎自動配置、GC 自動回收 |
| 物件會移動嗎 | **不會**，位址固定 | **會**（Scavenge 複製、Mark-Compact 壓實） |
| 佈局可預測性 | 高（bin/chunk 算得出） | 中（要懂 GC 擺放規律） |
| 「free」怎麼發生 | 你呼叫 free | 物件不再可達，GC 幫你回收 |
| UAF 怎麼來 | free 後續用 | 物件被 GC 回收後仍有 dangling 引用（較難，但存在） |

這張表點出為什麼 V8 的 heap 利用是另一套肌肉：你操控的不是 `malloc`/`free`，而是**引用關係與 GC 時機**。

## 踩雷集錦

1. **以為物件位址固定**：這是從 glibc 帶來最致命的錯覺。V8 的 GC 會搬物件，寫死位址的 exploit 一次 GC 就碎。要即時 leak，或確保關鍵物件不被搬。
2. **忽略 GC 會在你 exploit 中途發生**：你配置大量物件做 spray 時，很可能觸發一次 Scavenge/Mark-Compact 把佈局全打亂。要嘛先 `%CollectGarbage` 把堆推到穩定態，要嘛控制配置量避免非預期 GC。
3. **把「不再引用」當成「立刻釋放」**：JS 沒有顯式 free。一個物件「邏輯上死了」不代表記憶體立刻回收——要等下一次 GC。UAF 類利用要理解「何時真的被回收」，而不是「何時最後一次被用」。
4. **以為 young 和 old 行為一樣**：新配置的物件在 young space（Scavenge 管、常搬），long-lived 的在 old space（Mark-Compact 管）。spray 目標落在哪個 generation 影響它會不會被搬、跟誰相鄰。用 `%CollectGarbage` 逼晉升可以把物件推進 old space。
5. **忽略壓縮指標只有 32 bit**：OOB 蓋堆內物件指標時，你動的是壓縮值（低 32 bit），不是完整 64-bit 位址。搞錯寬度會蓋歪。

## 進階：再往深一層

- **其他 space**：除了 young/old，V8 堆還有 **read-only space**（不可變的內建物件，如 `undefined`、`true`、空 `FixedArray`——你在 `%DebugPrint` 常看到 `in ReadOnlySpace`）、**large object space**（超過一定大小的物件單獨放，**不搬動**——這點可被利用來取得穩定位址！）、**code space**（JIT 產生的機器碼）。「大物件不搬」是 exploit 開發者偶爾用來換位址穩定性的技巧。
- **併發與增量 GC**：現代 Orinoco 大量工作在背景執行緒併發做（incremental marking、concurrent sweeping），減少主執行緒暫停。這和 [Ch 2](./02-v8-architecture.md) 講的併發優化一樣，中間的時間窗偶爾是問題來源。
- **write barrier**：當你把一個 young 物件的引用寫進一個 old 物件，GC 需要知道這件事（否則掃 young 時會漏掉這個來自 old 的引用）。這靠 **write barrier**——每次寫 tagged 指標欄位時的一小段記帳碼。write barrier 的實作偶爾出過 bug。
- **the-hole**：V8 有個特殊的內部值 `the_hole`，標記「陣列的洞」「未初始化的槽」等。若攻擊者能讓 `the_hole` 洩漏到 JS 可見層（本該不可能），會造成嚴重混淆——這是幾個真實 CVE 的根源（[Ch 22](./22-typer-range-analysis-bug.md)、final project 會提到 CVE-2021-38003 就和 hole 有關）。
- **GC 相關漏洞本身**：GC 邏輯（尤其晉升、write barrier、weak reference 處理）雖然不是主礦，但出過真實記憶體破壞 bug。想深入可追 V8 `src/heap/` 的安全修補。

## 動手練習

1. 寫一段製造大量短命物件、保留幾個長命物件的 JS，用 `--trace-gc` 觀察。數一數跑出幾次 `Scavenge`、幾次 `Mark-Compact`，看堆大小怎麼漲落。改變「保留物件」的數量，看晉升行為怎麼變。
2. 用 `%DebugPrint` 印同一個物件兩次，中間插一個 `%CollectGarbage("x")`，比較兩次印出的位址有沒有變。多跑幾次觀察規律（提示：小 old-space 物件在沒有 compaction 壓力時可能不動；製造壓力才會搬）。
3. 配置一個很大的陣列（進 large object space）和一個小陣列，各 `%DebugPrint` 再強制 GC，觀察哪個位址穩定。思考：為什麼「大物件不搬」對 exploit 有用？

## 本章重點整理

- V8 用**分代 GC**（young: Scavenger 複製 / old: Mark-Compact 壓實）換效能，基於「多數物件朝生暮死」的分代假說。
- GC **會搬動物件**——這是和 glibc 堆最根本的差異，直接後果是**位址不穩、不能寫死**。
- 利用時 GC 是雙面刃：搞懂擺放規律可做 **heap grooming/spray** 瞄準佈局；不懂則佈局在你背後被打亂。
- `%CollectGarbage` 讓你手動把堆推到想要的狀態；large object space「不搬」可換位址穩定性。
- GC 與 **pointer compression** 耦合（cage 內搬動、32-bit 壓縮指標），這是 V8 Sandbox 的地基。

## 自我檢核

- [ ] 能解釋 Scavenger 和 Mark-Compact 各管哪個 generation、各自怎麼運作
- [ ] 能說出「物件位址會變」對寫 exploit 的三個具體影響
- [ ] 知道怎麼用 `%CollectGarbage` 和 spray 引導堆佈局
- [ ] 面試被問「V8 的 GC 和手動 malloc/free 對利用有什麼不同」，能答出「物件會移動 + 靠引用而非 free」
- [ ] 知道 large object space「不搬」為什麼對取得穩定位址有用

## 延伸閱讀

- **[“Trash talk: the Orinoco garbage collector” — v8.dev/blog/trash-talk](https://v8.dev/blog/trash-talk)**
  - **這篇說什麼**：V8 團隊對 Orinoco 的第一手總覽——分代、Scavenger、並行/併發、增量標記。
  - **讀哪裡**：整篇。本章的分代與兩種演算法就是它的白話+利用視角版。
  - **和本章的關聯**：把 Scavenge 的 semi-space 複製、晉升條件講得比本章細。

- **[“Concurrent marking in V8” 與 “Getting garbage collection for free” — v8.dev/blog](https://v8.dev/blog/concurrent-marking)**
  - **這篇說什麼**：GC 怎麼併發/增量地做，減少主執行緒停頓。
  - **和本章的關聯**：對應本章「進階」的併發 GC 時間窗；理解為什麼 GC 不是一個原子的暫停。

- **[Project Zero — 各篇涉及 V8 heap / GC 的 exploit writeup（googleprojectzero.blogspot.com）](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實 exploit 怎麼做 heap grooming、怎麼處理 GC 對佈局的干擾。
  - **讀哪裡**：挑一篇有 “groom” / “spray” 字樣的 V8 writeup，看它怎麼安排物件配置順序。
  - **前提**：讀完本章 + Part 3 的原語後看最有感。

Part 1、Part 2 到此收齊：你懂了 V8 怎麼表示物件、怎麼執行、怎麼管理記憶體。地基打完，下一章正式進入攻擊——用一個植入的越界 bug，敲開 V8 利用的第一道門。

→ [Ch 14 — 第一個 OOB：JSArray 越界](./14-first-oob.md)
