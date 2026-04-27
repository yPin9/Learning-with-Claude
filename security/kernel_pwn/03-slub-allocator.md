# Ch 3 — SLUB Allocator：kmalloc-N cache、freelist、object 生命週期

> 目標：看懂 `kmalloc(32, GFP_KERNEL)` 實際上做了什麼 — 進哪個 cache、從哪個 slab page 撿 object、freelist 怎麼串、free 掉之後 object 怎麼回收。Ch 9–17 所有 heap 技術都站在這章的心智模型上，這章糊就 Part 3 全糊。

## 為什麼 SLUB 比 glibc heap「簡單但更致命」

glibc heap 分 tcache、fastbin、small/large bin、unsorted bin 一堆鍋。SLUB 只有**一種東西**：kmem_cache。每個 cache 管固定 size 的 object。沒有 bin size 分級、沒有 consolidation、沒有 large bin 搜尋演算法。

好處（對 allocator）：快、可預測、per-CPU 無鎖路徑。
壞處（對 defender）：**物件回收後幾乎立刻被下一個同 size 的 allocation 拿走**。UAF 在 SLUB 上幾乎等於「free 完下一秒馬上覆寫」。你在 glibc heap 要 tcache poison、要 house of 某某；SLUB 直接 spray 一下就 reclaim 到手。

> SLUB 是 Linux 從 2.6.22（2007）開始的 default。SLAB / SLOB 分別在 6.5 / 6.2 正式移除。**2026 年的主線 kernel 只有 SLUB**。`CONFIG_SLUB` 沒開這條路直接不通。

## 一張圖看 SLUB

```
            kmem_cache (kmalloc-64)
            ┌─────────────────────────────────┐
            │ size=64  align=8  objects/slab=64│
            │                                  │
            │ cpu_slab (per-CPU)              │
            │   ┌── page A (active) ──┐       │
            │   │ obj₀ obj₁ ... obj₆₃ │       │
            │   │ freelist → obj₇     │       │
            │   └────────────────────┘        │
            │                                  │
            │ partial list (per-node)         │
            │   page B — 部分用 → page C ...  │
            │                                  │
            │ full list                       │
            │   page D — 全滿                  │
            └─────────────────────────────────┘
                       │
                       │ buddy allocator
                       ▼
              ┌──────────────────┐
              │  4 KB slab page  │  ← 從 buddy 撿整頁來切
              └──────────────────┘
```

一次 `kmalloc(50, GFP_KERNEL)` 發生什麼：

1. `50` 往上對齊到 `kmalloc-64` cache。
2. 拿這個 cache 的 `cpu_slab`（**per-CPU**，無鎖）。
3. 如果 `cpu_slab->freelist` 不空，pop 一個 object、回傳。⟵ **fast path，99% 走這**
4. 空了就去 per-node partial list 撿一個 partial page，換上來當新的 cpu_slab。
5. 都沒了向 buddy 要一頁新 slab page、切成 object、全部串成 freelist。

`kfree` 反過來：把 object push 回當前 cpu_slab 的 freelist（或 page 不是當前 cpu_slab 的話 push 回那個 page 的 freelist）。

## kmalloc-N cache 系列

`kmalloc(n, flags)` 依 n 進不同 cache：

| n 範圍 | cache 名 | 備註 |
|---|---|---|
| 1-8 | kmalloc-8 | |
| 9-16 | kmalloc-16 | |
| 17-32 | kmalloc-32 | |
| 33-64 | kmalloc-64 | |
| 65-96 | kmalloc-96 | 插入的非 2^n size |
| 97-128 | kmalloc-128 | |
| 129-192 | kmalloc-192 | |
| 193-256 | kmalloc-256 | |
| 257-512 | kmalloc-512 | |
| ... | ... | 直到 kmalloc-8192 |
| > 8192 | 直接向 buddy | 一頁一頁配（整頁） |

Ch 11 spray 物件大全會一直提到這張表。**記下常用 size**：

- `tty_struct` → kmalloc-1024（Ch 12 hijack target）
- `msg_msg` → kmalloc-64 起跳，header 外加可變 payload
- `sk_buff` → 自己的 cache（`skbuff_head_cache`）
- `cred` → 自己的 cache（`cred_jar`）
- `file` → 自己的 cache（`filp`）

**有自己 cache 的物件（cred、file）不跟 kmalloc-N 混**，所以你想打它們需要 cross-cache（Ch 13）。

## 觀察 guest 裡的 cache 狀況

兩個介面：`/proc/slabinfo` 與 `/sys/kernel/slab/`。

```
/ # head -20 /proc/slabinfo
slabinfo - version: 2.1
# name         <active_objs> <num_objs> <objsize> <objperslab> <pagesperslab> ...
kmalloc-8192    12   16  8192   4   8
kmalloc-4096    42   48  4096   8   8
kmalloc-2048    85  108  2048  16   8
kmalloc-1024   200  224  1024  16   4
kmalloc-512    318  352   512  16   2
kmalloc-256    530  546   256  16   1
kmalloc-192    720  735   192  21   1
kmalloc-128    890  896   128  32   1
kmalloc-96    1234 1260    96  42   1
kmalloc-64    2100 2176    64  64   1
kmalloc-32    3800 4096    32 128   1
...
```

`<objperslab>` 告訴你一個 slab page 塞幾個 object。對 spray 很重要：你想把一個 slab page 塞滿同一種 spray 物件，就得連續 alloc 這個數字的倍數。

`/sys/kernel/slab/kmalloc-64/` 下面還有更細的檔案：

```
/ # ls /sys/kernel/slab/kmalloc-64/
align       alloc_calls   ctor          destroy_by_rcu  free_calls
hwcache_align  object_size  objs_per_slab ...
```

`alloc_calls` 跟 `free_calls` 需要 `SLUB_DEBUG` 才有東西，Ch 4 會開。

## freelist：SLUB 的心臟

每個 free 的 object 第一個 qword（64-bit）存「下一個 free object 的地址」。整個 freelist 是**單向鏈表**，head 存在 slab page 結構 / cpu_slab 結構 裡：

```
cpu_slab->freelist  →  obj₇ ───► obj₄ ───► obj₁ ───► NULL
                       │         │         │
                    obj₇[0]   obj₄[0]   obj₁[0]
                    = &obj₄   = &obj₁   = NULL
```

所以：

- **覆寫 free object 的第一個 qword → 控 freelist → 下一個 alloc 回來的地址就是你寫的值**。這是 SLUB 上最常見的 heap primitive。
- **用 debugger 看 `cpu_slab->freelist`**：guest 裡 `cat /proc/slabinfo | grep kmalloc-64` 看不到 raw pointer，要 gdb 進去讀。
- **free 完的 object 可以被 spray 物件搶佔**（因為新 alloc 就從 freelist 頭拿）。這是 Ch 11 spray 技術的原理。

### `CONFIG_SLAB_FREELIST_HARDENED`

2017 後預設開啟。存的不是 raw 指標，是 `ptr XOR random_cookie XOR position`。覆寫它指到任意位置會先被 XOR 擾動，**要先 leak 出 cookie 才能精確操控**。但「覆寫成不合法值 → kernel BUG」這招不受影響 — 很多 CTF 題還是能打。

### `CONFIG_SLAB_FREELIST_RANDOM`

新 slab page 剛從 buddy 要來時，freelist 本來是順序的 `obj₀ → obj₁ → obj₂ → ...`。這個 config 打開後隨機打亂初始順序。**只影響第一次 alloc 順序**，一旦有 free-alloc 交錯行為模式，排序早就亂了。CTF 題最頭痛的其實是這個 config 讓 spray layout 不確定，**Ch 17 會正面處理**。

### `CONFIG_RANDOM_KMALLOC_CACHES`（6.6 才有）

**不同 call site** 的 `kmalloc(64)` 被 hash 到 **16 個子 cache** 之一（`kmalloc-64-1`、`kmalloc-64-2`...）。你的攻擊要讓「漏洞 alloc」和「spray alloc」落在同一個子 cache — 否則 spray 根本不在同一條 freelist 上。Ch 17 專章處理。

## Slab page 的生命週期

```
            要 page
              │
              ▼
       [ buddy allocator ]
              │
              │ alloc_pages(order)
              ▼
       ┌─────────────────┐
       │  slab page      │ ← 切成 N 個 object
       │  當 cpu_slab    │   freelist 串起來
       └─────────────────┘
              │
              │ 用一陣子後
              ▼
       partial list（有些 object 被 free，有些 in-use）
              │
              │ 全空
              ▼
       free back to buddy ← **slab page 重新變普通 page**
```

最後一步是 Ch 13 **cross-cache attack** 的根基：

- slab page 還給 buddy 後，它就是普通實體頁了
- buddy 下次被要求給一頁，可能會分給**另一個** cache（例如 pagetable 用）
- 這時你在這個舊 slab 的 object 指標還指著同一塊實體記憶體，**但實體記憶體的身份變了**
- 你寫到 object → 其實是在寫 pagetable entry

SLUB 什麼時候會把 slab page 還給 buddy？**slab 全空**且不在 partial list 頂端時。CTF 題會設計成讓你控制「全空」這個時機。Ch 13 細講。

## CTF 視角：這章哪些會回來咬你

| 技術 | 章節 | 依賴這章哪個點 |
|---|---|---|
| Heap overflow | Ch 9 | 「連續 object 物理上相鄰」 |
| UAF reclaim | Ch 10-11 | 「free 完下一個同 size alloc 拿到同一塊」 |
| Heap spray | Ch 11 | 「大量 alloc 會用完 freelist 然後切新 slab page」 |
| tty_struct hijack | Ch 12 | 「kmalloc-1024 上 tty_struct 和你的物件共存」 |
| Cross-cache | Ch 13 | 「slab page 被還給 buddy 後身份可換」 |
| Dirty Pagetable | Ch 14 | 同上 + pagetable 也從 buddy 要頁 |
| Random caches bypass | Ch 17 | 「call site hash 決定子 cache」 |

## 常見誤解

**「kmalloc 會回傳可立刻用的 zero memory」** — **不會**。預設 `GFP_KERNEL` 回來的 object 保留上次的內容（除非 cache 設了 ctor）。想清零用 `kzalloc`。這是你洩漏 kernel 指標最常見的缺陷型態。

**「SLUB object 上會有 metadata header」** — 預設**沒有**。object 地址就是 object 本體，overflow 直接吃到相鄰 object 的第一個 byte。開 `SLUB_DEBUG` 才會有 red zone 和 poison。

**「freelist 藏在 slab page 結尾」** — 錯。freelist 頭在 `cpu_slab` / `page` 結構裡，每個 free object 自己存下一個指標。

**「`kfree(NULL)` 會 crash」** — 不會，`kfree` 第一件事是 check NULL。但 `kfree` 一個用錯 allocator 的指標會，例如 `kfree(vmalloc(...))`。

**「per-CPU 路徑代表有 N 個 allocator 各做各的」** — 對的，但 object 在 CPU 間會透過 partial list 漂移。你被排程到另一個 CPU、free 到另一個 cpu_slab 都可能。Ch 17 會處理。

## 動手練習

1. **寫個 module 連做 200 次 `kmalloc(64, GFP_KERNEL)`**，把拿到的地址印出來，看它們是不是連續（同一 slab page 內）。再 free 掉偶數 index 的，看相鄰 object 的 free pattern。
2. **觀察 `/proc/slabinfo` 的變化**：alloc module 前後 `kmalloc-64` 的 `<active_objs>` 差多少、`<num_objs>` 呢？解釋差異。
3. **故意 `kmalloc(100)` 看它進哪個 cache**：在 module 裡印 `ksize(ptr)` 看真正配多少。答案是 128（對齊到下一檔）。
4. **關掉 `CONFIG_SLAB_FREELIST_RANDOM`** 重 build kernel，再跑上面練習 1，觀察地址是不是變成嚴格遞增 / 遞減。
5. **讀 `mm/slub.c` 的 `kmem_cache_alloc_node`**（或 `slab_alloc_node`，名字依版本）。至少知道 fast path 跟 slow path 在哪個 `if` 分。這是 kernelCTF 每個 heap 題作者都看過的函式。

## 自我檢核

- [ ] 能畫出「kmalloc(50) 進哪個 cache」整條路徑
- [ ] 知道 kmalloc-N 的 N 是哪些常見值（8, 16, 32, 64, 96, 128, 192, 256, 512, 1024, ...）
- [ ] 能說明 SLUB freelist 是單向鏈表，每個 free object 第一個 qword 是下一個指標
- [ ] 知道 `FREELIST_HARDENED`、`FREELIST_RANDOM`、`RANDOM_KMALLOC_CACHES` 三個 config 各擋什麼
- [ ] 能描述 slab page 還給 buddy 的時機、為什麼這是 cross-cache 的前提
- [ ] `/proc/slabinfo` 的每一欄能解釋

下一章從 Ch 3 SLUB 的 mental model 切回攻擊面：我們拿 Ch 2 那個 stack overflow module，把 canary 先關掉，看看「可以精確控 ret」的世界原本長怎樣、然後開回 canary 逼你解出 leak 路徑 — 這就是 Part 2 的起點。

→ [Ch 4 — Stack Buffer Overflow in kernel：canary 與第一次 ret2usr](./04-stack-overflow.md)
