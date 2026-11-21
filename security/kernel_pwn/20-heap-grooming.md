# Ch 20 — Heap grooming：精確控制 slab 布局、defrag、穩定 spray

> **目標**：把 [Ch 17](./17-heap-overflow.md)/[Ch 18](./18-uaf-double-free.md) 一直假設成立的那句「spray 一批就剛好有一個落到洞裡」變成真的做得到、而且穩。學完你要能回答：slab 有碎片、有雜訊 allocation、你還不知道中的是哪一個——這些問題各自怎麼解。grooming（heap 風水）是 heap 利用從「理論上可行」到「10 次成功 9 次」的那道工，kernelCTF 的穩定性要求就卡在這裡。

> **環境**：Linux 6.6 LTS、x86-64、QEMU。grooming 行為高度依賴 SLUB 當下狀態與 CPU 數，本章給策略與原理，實際參數要在你的靶上實測調。未實測，理論預期行為。

## 為什麼不能「alloc 完直接打」

把前兩章的理想圖拆穿：你以為 slab 是這樣的——

```
  理想：[ 我的 buffer ][ victim ][ 空 ][ 空 ]   ← victim 乖乖在我後面
```

實際開機跑一陣子後，slab 長這樣——

```
  現實：[ 別人A ][ 空洞 ][ 別人B ][ 我的buffer ][ 別人C ][ 空洞 ]...
         跨好幾個 partial slab，空洞東一個西一個，
         還有背景 allocation（其他 process、kernel 自己）隨時在插隊
```

直接 alloc + 打，你的 buffer 旁邊是誰完全隨機：可能是空洞（蓋了沒用）、可能是無關物件（蓋了 panic）、可能跨到 slab 邊界外（直接爆）。grooming 的任務就是**把這團混亂整理成可預測的布局**，再動手。它要解三個子問題：碎片、雜訊、不確定中哪一個。

## 子問題一：碎片 → 用 defrag 逼出乾淨 slab

SLUB 的 partial list 上躺著一堆「用了一半」的 slab，空洞分布不可控。對策：**先把這些半滿 slab 填滿，逼 allocator 去 buddy 要一個全新的、格子順序排列的 slab**，在那上面做文章。

```
  Step 1 大量 alloc 同 size（填洞）：
     把 partial slab 上的空洞全部佔掉 → 這些 slab 變 full，移出 partial list
  Step 2 繼續 alloc：
     partial 沒得用了 → allocator 向 buddy 要新 slab → 格子 0,1,2,... 順序乾淨
  Step 3 在乾淨 slab 上：
     alloc vuln buffer（格子 i）→ alloc victim（格子 i+1）→ 相鄰確定
```

這就是 heap grooming 最核心的一招：**defragmentation（去碎片）**。填多少才夠逼出新 slab，取決於 cache 的 `objects/slab` 與當前 partial 數量——實測調，通常幾十到上百個。

## 子問題二：雜訊 → 關中斷面、綁 CPU、縮短窗口

背景 allocation 會插隊打亂你的布局。來源與對策：

- **其他 CPU 的 per-cpu slab**：SLUB 的 fast path 是 **per-cpu** 的（[Ch 8](./08-slub-allocator.md)）。你的 spray 和 victim 若跑在不同 CPU，會進不同 cpu_slab，根本不相鄰。對策：`sched_setaffinity` 把你的 exploit **綁死在單一 CPU**，讓所有 alloc 走同一個 cpu_slab。這是最常被忘、最常導致「怎麼都不穩」的一條。
- **其他 process / kernel 背景 allocation**：縮短「排好布局」到「觸發」之間的時間窗，減少被插隊的機會；必要時在安靜的時機（剛開機、無其他負載）跑。
- **你自己的 syscall 也會 alloc**：grooming 期間少做無關 syscall，避免自己污染目標 cache。

```c
/* 綁 CPU 0，消除 per-cpu slab 的跨核雜訊（必備） */
cpu_set_t set; CPU_ZERO(&set); CPU_SET(0, &set);
sched_setaffinity(0, sizeof(set), &set);
```

## 子問題三：中了哪一個 → 讓 spray 物件「可識別」

spray 一百個物件，哪一個落到了洞裡？你得讓它們**可區分、可定位**。手法：

- **編號標記**：每個 spray 物件內容塞一個獨一無二的 index（`msg_msg` 內容前幾 bytes 寫 i）。事後透過 bug 讀出那塊的 index，就知道中的是第幾個。
- **行為探測**：觸發 bug 後逐一檢查每個 spray 物件（例如 `msgrcv` 每個佇列、read 每個 fd），哪一個行為異常（內容被改、少了、崩了），就是中的那個。
- **雙重 spray 夾擊**：先 spray A 批佔位，釋放中間一個製造洞，再 alloc victim，victim 必落那個洞——用「主動製造洞」取代「賭 victim 落在哪」。

第三種（主動製造洞）是最可控的，常見於 UAF：

```
  spray 一排可控物件 [0][1][2][3][4]...
  free 掉中間一個    [0][1][ 洞 ][3][4]
  alloc victim       [0][1][victim][3][4]   ← victim 必定落在那個洞
  → 你確切知道 victim 在 [1] 和 [3] 之間，用它們的內容定界/leak
```

## 一個完整的 grooming 範式（UAF 版）

把三個子問題的解法串成一套可複用流程：

```
1. sched_setaffinity 綁 CPU 0                      （消跨核雜訊）
2. spray 大量同 size、帶 index 的可控物件           （defrag + 佔位 + 可識別）
3. 製造目標洞：free 掉其中特定一個（或觸發 bug 的 free）
4. alloc victim（觸發 kernel 配置目標物件）          （victim 落進那個洞）
5. 用相鄰/dangling 讀出 index → 確認布局如預期
6. 觸發 corruption（overflow 蓋 / UAF 用）
7. 失敗就 cleanup 重來（Ch 45）
```

步驟 5 的「確認」很關鍵：**先驗證布局對了，再動手改**。盲打（不確認就蓋）是不穩的根源。多花一次讀來確認，換來的是「知道自己打中了」。

## elastic object：讓一張牌打多個 cache

[Ch 19](./19-spray-objects.md) 提過 `msg_msg`/`user_key_payload` 的 size 可調。grooming 時這很值錢——同一個 spray 原語，調長度就能瞄準不同 cache，不用為每個 cache 找不同的牌。而且「可調長度」還讓你能做**精準的 size 匹配**：victim 在 kmalloc-96，你就把 msg_msg 內容調到剛好落 kmalloc-96。這類「長度可控」物件（文獻稱 elastic object）是 grooming 的瑞士刀。

## 踩雷集錦

1. **沒綁 CPU**——SLUB fast path per-cpu，不綁 CPU 的話你的 spray 和 victim 可能在不同 cpu_slab，永遠不相鄰。「邏輯都對但就是不穩/打不中」最常見的原因。`sched_setaffinity` 綁單核是 heap 題的預設動作。
2. **不 defrag 就 spray**——在碎片化的 partial slab 上 spray，物件散落在舊 slab 的隨機空洞，相鄰關係不可控。先填滿舊 slab 逼出乾淨的。
3. **spray 物件不帶識別資訊**——中了也不知道中哪個、布局對不對，只能盲打。每個 spray 物件塞 index，事後能定位。
4. **「製造洞 → alloc victim」中間插了別的 alloc**——你 free 出洞後，若在 alloc victim 前自己又做了別的同 size alloc，那個洞被自己的 alloc 佔走，victim 落到別處。製造洞和 alloc victim 之間要「乾淨」。
5. **把 user-space malloc 的直覺套過來**——SLUB 沒有 consolidation、沒有 chunk header、free 是 LIFO per-cpu。user-space heap fengshui 的很多假設（bin 合併、split）在這裡不成立，別套 glibc 的 house-of 模板。
6. **一次就想穩定 100%**——grooming 本質是機率工程，受背景雜訊影響。目標是「夠穩」（kernelCTF 常要求某個成功率），搭配 retry + cleanup（[Ch 45](./45-exploit-stabilization.md)）把偶發失敗吃掉，而不是追求單次必中。

## 進階：grooming 是穩定性的主戰場

exploit 能不能過 kernelCTF 的穩定性門檻，八成取決於 grooming 做得多細。一個「原理正確但 grooming 粗糙」的 exploit 可能 10 次中 3 次；同一個 bug，grooming 調好能到 10 次中 9 次。差別在這些細節：有沒有綁 CPU、有沒有 defrag、有沒有確認布局、失敗後有沒有乾淨 cleanup 再重來。

這也是為什麼真實 kernelCTF submission 的 code，漏洞觸發可能只有 20 行，grooming + 穩定化卻佔了幾百行。[Ch 45](./45-exploit-stabilization.md) 會把「失敗後怎麼乾淨重來、怎麼避免 cleanup 時 panic、怎麼跨版本保持布局假設」接續講完——grooming（把場布對）和 stabilization（打不中時收拾好再來）是一體兩面。

一個心法：**把 grooming 當成一個可觀測、可驗證的狀態機**，而不是「spray 一堆然後祈禱」。每一步都要能回答「現在 slab 大概長怎樣、我怎麼確認」。能觀測，才能調穩。

## 動手練習

在自有 QEMU 靶上（接 Ch 18 的 UAF 靶）：

1. 不綁 CPU、不 defrag，直接 free→spray→use，連跑 20 次，記錄成功率（體會「不 groom 有多不穩」）。
2. 加 `sched_setaffinity` 綁 CPU 0，再跑 20 次，比較成功率跳多少。
3. 加 defrag（先 spray 一批填滿舊 slab）+ 帶 index 的 spray + 「製造洞 → alloc victim」範式，再跑 20 次，目標 ≥18/20。
4. 加一步「觸發前先讀 index 確認布局」，布局不對就跳過該次（不盲打），觀察成功率與「確認」帶來的穩定度。

## 本章重點整理

- grooming 解三個問題：碎片（defrag 逼出乾淨 slab）、雜訊（綁 CPU 消跨核、縮窗、少污染）、不確定中哪個（index 標記 / 主動製造洞）。
- per-cpu slab 讓「綁 CPU」成為 heap 題的預設動作——不綁多半不穩。
- 「製造洞 → alloc victim」比「賭 victim 落點」可控得多。
- 動手前先「確認布局」，別盲打。
- elastic object（可調長度，如 msg_msg）是瞄準不同 cache 的瑞士刀。
- grooming 是穩定性主戰場，和 Ch 45 的 stabilization 一體兩面。

## 自我檢核

- [ ] 能解釋為什麼不綁 CPU 的 heap exploit 常常「邏輯對但不穩」
- [ ] 能說出 defrag 的目的，以及它怎麼讓相鄰關係變可控
- [ ] 能描述「製造洞 → alloc victim」範式，以及它比盲 spray 好在哪
- [ ] 能說出為什麼「觸發前先確認布局」能提升穩定性
- [ ] 能解釋 grooming 和 stabilization（Ch 45）為什麼是一體兩面

## 延伸閱讀

- **[Linux Kernel Heap Feng Shui（各 writeup / Vitaly Nikolenko 課程筆記）](https://www.interruptlabs.co.uk/)**
  - **這篇說什麼**：系統性講 SLUB grooming 的策略（defrag、per-cpu、佔位）。
  - **讀哪裡**：heap grooming / feng shui 相關段；對照本章三個子問題。
  - **為什麼值得讀**：把本章策略落到更多實例與參數經驗。
- **[A systematic study of elastic objects in kernel exploitation](https://dl.acm.org/doi/10.1145/3372297.3423353)** — Chen 等，CCS 2020
  - **核心貢獻**：形式化「可調長度物件」在 grooming 與原語升級的角色。
  - **讀哪裡**：Section 3–4；對照本章 elastic object 一節。
  - **和本章的關聯**：解釋為什麼 msg_msg 這類牌在 grooming 上這麼萬用。
- **[CPU affinity 與 per-cpu slab（mm/slub.c 的 cpu_slab）](https://elixir.bootlin.com/linux/v6.6/source/mm/slub.c)**
  - **讀哪裡**：`kmem_cache_cpu`、`this_cpu_*` 相關；理解為什麼跨核 spray 會分到不同 slab。
  - **和本章的關聯**：本章「綁 CPU」的 source 依據。
  - **前提知識**：Ch 8 的 per-cpu fast path。

布局做穩了，接下來把最常見的終點補上：怎麼從「蓋到一個 victim」真正走到 RIP 控制——偽造 ops table、觸發函式指標呼叫，以及在 CFI 時代這條路還剩多少。

→ [Ch 21 — 從 heap 到 RIP：tty_struct ops hijack、seq_operations、pipe_buffer](./21-heap-to-rip.md)
