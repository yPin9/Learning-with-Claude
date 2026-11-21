# Ch 22 — Cross-cache attack：跨 kmalloc cache 打 dedicated slab

> **目標**：解決前面所有 heap 技術的共同死穴——「victim 和我能 spray 的可控物件必須同 cache」。當 victim 住在一個**專用 cache（dedicated cache）**、一般 user 根本配不出同 cache 的可控物件時，前面的 spray 全失效。cross-cache attack 把戰場從「object 層」拉到「page 層」：讓一整個 slab 的 page 回到 buddy，再被另一個 cache 當新 slab 拿走，於是兩個不同 cache 的物件疊在同一塊實體記憶體。學完你要能解釋這個機制、它需要什麼條件、以及為什麼它是繞過 `FREELIST_HARDENED` 與 random kmalloc caches 的通用解。

> **環境**：Linux 6.6 LTS、x86-64、QEMU。本章以機制為主，依賴 buddy/SLUB 的 page 流動（[Ch 7](./07-buddy-allocator.md)/[Ch 8](./08-slub-allocator.md)）。未實測，理論預期行為。

## 為什麼需要 cross-cache

回憶前面幾章反覆出現的鐵律：spray 物件和 victim 要**同 cache**（[Ch 17](./17-heap-overflow.md)/[Ch 18](./18-uaf-double-free.md)/[Ch 19](./19-spray-objects.md)）。這個前提在兩種情況下崩掉：

1. **victim 在專用 cache**：很多值錢的結構（`cred` 在 `cred_jar`、`task_struct`、`file`、某些子系統用 `kmem_cache_create` 開的專用 cache）不住在通用 `kmalloc-N`，而住在只放這一種物件的專用 slab。你沒有「能配進同一個專用 cache 的可控物件」——那個 cache 只收那一種物件。
2. **random kmalloc caches**（[Ch 27](./27-random-kmalloc-caches.md)）：同一個 size 被拆成 16 份隨機 cache，你的 spray 和 victim 大概率落不同份。

這兩種情況下，object 層的 spray 束手無策。但有一件事所有 cache 都共用：**它們的 slab 最終都是從同一個 buddy allocator 要來的 page**（[Ch 7](./07-buddy-allocator.md)）。cross-cache 就從這個共用層下手。

## 先建立直覺：換一層打

```
  object 層（前面章節）：          page 層（cross-cache）：
  ┌─────────────────┐              buddy allocator（所有 cache 共用的 page 來源）
  │ 同一個 cache 裡  │                     │ 要 page            │ 還 page
  │ 我的物件 vs      │              ┌──────▼──────┐      ┌──────▼──────┐
  │ victim 相鄰      │              │ cache A 的   │      │ cache B 的   │
  └─────────────────┘              │ slab(這些    │      │ slab         │
  被「同 cache」綁死                 │ page)        │      │              │
                                   └─────────────┘      └─────────────┘
                                   關鍵：同一塊 page，先給 A 當 slab、
                                   釋放回 buddy、再被 B 拿去當 slab
```

核心 idea：**一塊實體 page，在不同時間可以屬於不同的 cache。** 如果我能讓「victim 專用 cache 的某個 slab」整個釋放、它的 page 回到 buddy，再誘導「我可控物件的 cache」去 buddy 要 page、剛好要到那塊——那麼我的可控物件就和（原本）victim 的記憶體疊在同一塊實體上。cache 的隔離是「同一時間」的，不是「跨時間」的。

## 機制：page 怎麼在 cache 之間流動

一個 slab 的 page 要能回到 buddy 再被別人拿走，得滿足 SLUB 的釋放條件。流程（概念）：

```
1. 讓 victim 的專用 cache「開出一個完整、乾淨的 slab」
   （spray 滿 victim 物件，逼 allocator 向 buddy 要新 page 當 slab）
2. 把那個 slab 上的 victim 物件「全部釋放」
   → slab 變空 → SLUB 把這個空 slab 的 page 還回 buddy（free_slab）
3. 立刻從「我可控物件的 cache」大量配置
   → 那個 cache 向 buddy 要 page → 可能拿到剛還回去的那塊
   → 我的可控物件現在坐落在「原本是 victim slab」的 page 上
4. 此時若還有 dangling 指標把那塊當 victim 用（UAF），
   或我對那塊有 overflow → 跨 cache 的 type confusion
```

難點全在「時機」與「page 真的被回收又被要回」：

- **讓空 slab 真的還回 buddy**：SLUB 有 per-node partial list 的保留機制（`min_partial`），不會一有空 slab 就立刻還。要還得夠多、把 partial 擠爆，才會真正 `free_slab` 回 buddy。
- **讓目標 cache 要到那塊 page**：buddy 的分配有 order/migratetype 之分（[Ch 7](./07-buddy-allocator.md)）。兩個 cache 的 slab order 要相容、migratetype 要對得上，page 才可能流過去。
- **全部在對的 CPU、對的時機**：還是 per-cpu 的問題（[Ch 20](./20-heap-grooming.md)），加上 page 回收的時機窗。

## 為什麼它能繞過 FREELIST_HARDENED 與 random caches

cross-cache 的威力在於它**不碰 freelist 指標、不要求同 object cache**：

- `CONFIG_SLAB_FREELIST_HARDENED`（[Ch 18](./18-uaf-double-free.md)）保護的是「freelist 裡的 next 指標」。cross-cache 的 type confusion 來自「兩個 cache 疊同一塊 page」，根本不改 freelist 指標，XOR 混淆無從施力。
- random kmalloc caches（[Ch 27](./27-random-kmalloc-caches.md)）把「同 size 的 object cache」拆成 16 份，但它們的 slab **還是從同一個 buddy 要 page**。cross-cache 在 page 層操作，繞過了 object 層的隨機化。

這就是為什麼 cross-cache 成了現代 kernelCTF 的主力技術之一：當 object 層被各種 hardening 封住，page 層是 allocator 難以隔離的共用底層。代價是它比 object 層 spray **難穩定得多**——page 回收/再分配的時機很敏感，grooming 要做得更細。

## 典型應用：打 cred_jar 之類的專用 cache

一個常見目標是 `cred`（住在專用 `cred_jar`）。思路（概念）：

```
victim cache = cred_jar（你配不出「可控內容的 cred」）
attacker cache = 你能精確控制內容的某物件所在的通用 cache（size 對得上 cred 的 slab order）

1. 用 fork 等大量產生 cred，開出乾淨的 cred slab
2. 釋放那批 cred，逼 cred slab 的 page 回 buddy
3. 用可控物件 spray，搶到那塊 page
4. 現在你的可控物件和「某個仍被引用的 cred」疊在一起
   → 改你的物件 = 改那個 cred 的欄位（uid=0）→ data-only 提權（Ch 16）
```

注意這裡終點是 **data-only**（改 cred 欄位），不是控制流——cross-cache 常和 data-only 搭配，因為它給你的是「跨型別的記憶體重疊」，正好拿來改資料。

> cred/專用 cache 的 slab order、object size、以及「要釋放多少才會還 page」都依版本與 config，必須在你的靶實測。這裡給流程骨架，位址/數量標依實測填入。

## 踩雷集錦

1. **以為釋放幾個 victim 就會還 page 給 buddy**——SLUB 的 `min_partial` 會保留一定數量的空 slab 在 partial list，不立刻還。要還得把整個 cache 的空 slab 擠到超過保留量，釋放數量要夠大。釋放三五個就等 page 回來，等不到。
2. **兩個 cache 的 slab order / migratetype 不相容**——page 回 buddy 後，buddy 按 order 和 migratetype 分配。若 victim cache 的 slab 是 order-1、你的攻擊 cache 要 order-0，或 migratetype 對不上，page 流不過去。要先確認兩 cache 的 slab 幾何相容。
3. **沒考慮 per-cpu 與時機**——page 回收和再分配之間，別的 allocation（你自己的、背景的）可能先搶走那塊 page。要縮短窗口、綁 CPU，必要時用大量 spray 提高搶中機率。
4. **把 cross-cache 當 object spray 一樣穩**——它天生更不穩（多了 page 回收這一層時機）。kernelCTF 的 cross-cache exploit 常要靠 retry（[Ch 45](./45-exploit-stabilization.md)）把成功率補起來，別期待單次必中。
5. **誤判 type confusion 的欄位對齊**——兩個 cache 的物件 size 不同、起點對齊不同，你的可控物件和 victim 的欄位不一定從同一個 offset 開始疊。要算清楚「我的 offset X 對到 victim 的哪個欄位」，才知道改哪裡。

## 進階：cross-cache 是「page 層思維」的起點

cross-cache 教會你一個更大的觀念：**當 object 層被 hardening 封死，往下一層（page / 實體記憶體）找共用面。** 這個思維直通 Part 3 剩下兩章：

- [Ch 23](./23-dirty-pagetable.md) Dirty Pagetable：再往下一層，直接攻擊 page table 本身（PTE），拿到任意實體記憶體讀寫——比 cross-cache 更徹底的「page 層」攻擊。
- [Ch 25](./25-usma-page-spray.md) USMA / page spray：用 packet socket 的 pgv 等機制在 page 層 spray，把 kernel page 映射進 user。

這三章共享「跳出 object 層」的精神。cross-cache 是最溫和的那個（還在 slab 的框架內，只是跨 cache）；Dirty Pagetable 和 USMA 更激進（直接碰 page table 與實體頁）。理解 cross-cache 的 page 流動，是讀懂後兩章的基礎。

實戰上，決定用不用 cross-cache 的判準很簡單：**victim 在我配不出可控物件的專用/隨機 cache 嗎？** 是 → cross-cache（或 Dirty Pagetable）；否 → 省事的 object 層 spray 就夠。別在能同 cache spray 時為了炫技硬上 cross-cache，它只會讓你的 exploit 更不穩。

## 動手練習

在自有 QEMU 靶上（以機制觀察為主）：

1. 用 `/proc/slabinfo` 觀察：大量 `fork`（產生 cred）時 `cred_jar` 的 slab 數怎麼漲；全部回收後怎麼降。體會「slab 的 page 什麼時候才還 buddy」。
2. 寫一個觀測：spray 滿某通用 cache 逼它開新 slab、全部釋放、再從另一個 cache 配置，用物件位址判斷有沒有拿到同一塊 page（位址落在同一個 page 範圍）。
3. 查你 kernel 上 `cred`、`file` 等專用 cache 的 slab order 與 object size（`/sys/kernel/slab/<name>/`），判斷哪些通用 cache 的 slab 幾何和它相容。
4. 分析題：給一個「victim 在專用 cache」的 UAF，寫出你的 cross-cache 計畫（釋放什麼、spray 什麼、怎麼確認搶中），不必完整實作，重在流程正確。

## 本章重點整理

- cross-cache 解決「victim 在專用/隨機 cache、配不出同 cache 可控物件」的死穴。
- 機制：讓 victim slab 的 page 回 buddy，再被攻擊者的 cache 要走 → 跨 cache 的記憶體重疊（type confusion）。
- 它不碰 freelist 指標、不要求同 object cache，所以繞過 FREELIST_HARDENED 與 random kmalloc caches。
- 難點在時機：min_partial 保留、slab order/migratetype 相容、per-cpu、搶 page 的窗口；因此比 object 層 spray 不穩，常靠 retry 補。
- 它是「跳出 object 層往下找共用面」思維的起點，直通 Dirty Pagetable（Ch 23）與 USMA（Ch 25）。

## 自我檢核

- [ ] 能解釋為什麼「victim 在專用 cache」讓前面所有 object 層 spray 失效
- [ ] 能描述 page 在兩個 cache 之間流動的四步，以及每步的時機風險
- [ ] 能說出 cross-cache 為什麼繞得過 FREELIST_HARDENED 和 random kmalloc caches
- [ ] 能說出 min_partial 為什麼讓「釋放幾個就等 page」行不通
- [ ] 能用「victim 在不在我能 spray 的 cache」判斷該不該用 cross-cache

## 延伸閱讀

- **[Attacking the Linux Kernel: cross-cache（lrh2000）](https://lrh2000.com/)**
  - **這篇說什麼**：把 cross-cache 的 page 回收/再分配時機講得很透，含 min_partial 與 grooming 細節。
  - **讀哪裡**：cross-cache 相關文章整篇。
  - **為什麼值得讀**：本章機制的最佳延伸，補上「怎麼把時機調穩」的實務。
- **[CVE-2022-29582 / 各 kernelCTF cross-cache writeup](https://github.com/google/security-research/tree/master/pocs/linux/kernelctf)**
  - **這篇說什麼**：真實用 cross-cache 打專用 cache 的 submission。
  - **讀哪裡**：挑一篇標題或 writeup 提到 cross-cache 的，看它釋放什麼、spray 什麼。
  - **為什麼值得讀**：看真實 exploit 怎麼處理穩定性，對照本章踩雷。
- **[SLUB free_slab 與 min_partial（mm/slub.c）](https://elixir.bootlin.com/linux/v6.6/source/mm/slub.c)**
  - **讀哪裡**：`free_slab`、`put_cpu_partial`、`min_partial` 相關；看空 slab 何時真的還 buddy。
  - **和本章的關聯**：本章「page 何時回 buddy」的 source 依據。
  - **前提知識**：Ch 7 buddy、Ch 8 SLUB。

cross-cache 還在 slab 的框架內跨 cache。下一章更激進：直接攻擊 page table 本身——覆寫一個 PTE，讓一塊你控制的 user page 映射到任意實體位址，拿到任意實體記憶體讀寫，連 kernel 位址都不太需要 leak。

→ [Ch 23 — Dirty Pagetable：PTE 覆寫拿任意實體記憶體 R/W](./23-dirty-pagetable.md)
