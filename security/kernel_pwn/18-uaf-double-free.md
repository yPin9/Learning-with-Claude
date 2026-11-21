# Ch 18 — UAF / Double Free：SLUB freelist corruption 與 poisoning

> **目標**：換一個起手式。[Ch 17](./17-heap-overflow.md) 的 bug 給你「越界寫」，這章的 bug 給你「一個指向已釋放記憶體的指標」。學完你要能解釋：UAF 在 SLUB 上為什麼幾乎免費好用、free→spray→use 的三拍節奏、double free 怎麼腐化 freelist、以及 freelist poisoning 怎麼讓你「指定下一次 kmalloc 回傳的位址」——以及 `CONFIG_SLAB_FREELIST_HARDENED` 怎麼擋這一招。UAF 是現代 kernelCTF 最常見的起點，這章是 Part 3 的核心。

> **環境**：Linux 6.6 LTS、x86-64、QEMU，mitigation 預設開。靶沿用 [Ch 5](./05-first-vulnerable-module.md) 的 `vulndevice.c`，加一條會留下 dangling pointer 的 free 路徑。exploit code 未在本環境實測，標為理論預期行為。

## 為什麼 UAF 在 kernel 上特別好用

回憶 [Ch 8](./08-slub-allocator.md) 的 SLUB LIFO 行為：`kfree` 一個 object，它進 freelist 頂端；下一個**同 size** 的 `kmalloc` 立刻把它拿回來。這個「立刻重用」對 allocator 是效能優點，對攻擊者是天賜良機——

user-space 的 glibc heap，free 掉的 chunk 會進 tcache/各種 bin，你要重拿回那塊得過一些條件（size 匹配、tcache 沒滿……）。SLUB 幾乎沒有這些門檻：**free 完馬上 spray 一個同 size 的物件，大概率就拿回剛釋放的那塊。** 於是 UAF 的利用節奏非常直接：

```
  三拍節奏：
  1. free(victim)        victim 的記憶體回 freelist，但程式裡還有指標指著它（dangling）
  2. spray(我的物件)      同 size、我能控內容 → 佔住 victim 剛釋放的那塊
  3. use(dangling)       程式以為在用 victim，實際讀/寫的是我 spray 進去的內容
```

第 3 拍發生的就是 [Ch 10](./10-vulnerability-taxonomy.md) 說的 **type confusion**：同一塊記憶體，dangling 指標當它是 victim（型別 A），而你 spray 進去的是型別 B 的內容。A 的介面去讀「某個函式指標欄位」，讀到的是你 B 寫的值。

## 先建立直覺：鑰匙還在，房間已經換人住

```
  free 之前：          free 之後（dangling）：        spray 之後（use 時）：
  ┌─────────────┐      ┌─────────────┐               ┌─────────────┐
  │ victim      │      │ (已釋放，    │               │ 我的物件     │
  │ ops → 合法  │      │  freelist)   │               │ ops → 我的   │◀─ dangling
  └─────▲───────┘      └─────▲───────┘                │       位址   │   指標還指著
        │ 指標               │ 指標仍在（dangling）     └─────▲───────┘   這塊
     程式握著             程式還握著                        │
                                                   程式用 dangling 指標呼叫
                                                   「victim->ops->func」
                                                   → 實際跳到我的位址
```

房間（記憶體）被退租（free）了，但你手上那把鑰匙（指標）沒收回。趁房間還沒被別人租走之前你什麼都碰不到——但 SLUB 的特性是**下一個同戶型的租客立刻搬進來**，而那個租客可以是你。你安排自己搬進去、擺好家具（函式指標），原房客（程式）拿著舊鑰匙回來，就會用到你的家具。

## 靶：留一個 dangling pointer

給 `vulndevice.c` 加一條 free 路徑，但 free 完不清指標：

```c
/* vulndevice.c（節錄）：UAF 靶 */
struct vuln_obj {
    void (*fn)(struct vuln_obj *);   /* 開頭就是函式指標——好蓋 */
    char data[48];
};
static struct vuln_obj *g_obj;

static long vuln_do_alloc(void)  { g_obj = kmalloc(sizeof(*g_obj), GFP_KERNEL); return 0; }
static long vuln_do_free(void)   { kfree(g_obj); /* BUG：沒有 g_obj = NULL */ return 0; }
static long vuln_do_use(void)    { if (g_obj && g_obj->fn) g_obj->fn(g_obj); return 0; } /* UAF */
```

bug 是 `vuln_do_free` 沒把 `g_obj` 設 NULL——free 之後 `g_obj` 成了 dangling pointer，`vuln_do_use` 還會解它、呼叫 `g_obj->fn`。攻擊：free 後 spray 一批「開頭是我控制的 8 bytes」的物件佔住那塊，再 use，`fn` 就跳到我的值。

> 真實的 UAF bug 很少這麼直白（通常是 refcount 少減、race、或某條錯誤路徑提前 free）。但**利用形狀都一樣**：拿到 dangling → spray 佔位 → 觸發 use。bug 的成因是 [Ch 10](./10-vulnerability-taxonomy.md)/[Ch 40](./40-race-conditions.md) 的事，這章專注在「拿到 dangling 之後怎麼打」。

## spray 什麼：佔位物件的選擇

第 2 拍要 spray 一個「同 size、且我能控制內容」的物件。它有兩種角色：

1. **當 leak 來源**：spray 一個「本來就含 kernel 指標」的物件（如 `tty_struct`），用 dangling 指標**讀**它 → leak kernel base（[Ch 14](./14-kaslr-infoleak.md)）。
2. **當 control 載體**：spray 一個「開頭是函式指標」的物件、或乾脆是我用 `msg_msg`/`setxattr` 等「user 可控任意內容」的 primitive 寫進去的 bytes → 用 dangling 指標觸發它被呼叫 → 控流（[Ch 21](./21-heap-to-rip.md)）。

常用的「我能控內容」spray 原語（細節 [Ch 19](./19-spray-objects.md)）：`msg_msg`（System V 訊息佇列，內容幾乎全可控）、`setxattr` + `add_key`（user_key_payload）、`sendmsg` 的輔助資料、pipe buffer。這些的共通點是**從 user-space 就能精確控制那塊記憶體的 bytes，且 size 可調**——正好拿來填 victim 的位置。

```
  free(victim, size=64)
  for (i=0; i<N; i++) spray_obj_with_controlled_bytes(size=64);  /* 其中一個落到 victim 處 */
  trigger_use();   /* dangling 把我 spray 的 bytes 當 victim 的欄位用 */
```

> spray 幾個才夠、怎麼確定有一個落在 victim 處、怎麼知道是哪一個——這是 grooming（[Ch 20](./20-heap-grooming.md)）。本章先假設 spray 數量夠、佔位成功。

## double free：腐化 freelist 本身

double free 是 UAF 的近親：同一塊被 `kfree` 兩次。它的危害不是「用已釋放」，而是**把 freelist 的結構弄壞**，最終常導向「同一塊記憶體被配給兩個不同的 live 物件」。

看 SLUB freelist 在 double free 下怎麼壞（簡化）：

```
  正常：freelist: A → B → C
  kfree(X) 第一次：freelist: X → A → B → C
  （中間 X 又被 alloc 出去、或沒有）
  kfree(X) 第二次：freelist: X → X → A → ...   ← X 指向自己，或 X 出現兩次

  後果：
    p1 = kmalloc()  → 拿到 X
    p2 = kmalloc()  → 又拿到 X（freelist 上 X 出現兩次）
    → p1、p2 兩個「不同用途」的指標指向同一塊 → type confusion
```

經典玩法：double free 後，一次 alloc 拿一個「我能控內容」的物件 A、另一次 alloc 拿一個「值錢的 victim」物件 B，兩者疊在同一塊——於是你用 A 的介面就能改寫 B 的欄位。這比單純 UAF 多一層控制力（你同時握有「寫」和「被用」兩個型別的 handle）。

## freelist poisoning：指定下一次 kmalloc 的位址

這是 UAF/double free 最強的升級。回憶 [Ch 8](./08-slub-allocator.md)：freelist 靠「每個空閒 object 開頭存的 freepointer（指向下一個空閒 object）」串起來。如果你能**寫**一個已釋放 object 的開頭（透過 UAF write，或 double free 後用一個可控物件覆蓋它），你就改寫了那個 freepointer——下一次（下下次）`kmalloc` 會沿著你寫的指標走，把 freelist「導向」你指定的位址。

```
  freelist（正常）：free_obj → [freepointer=next] → ...
        │ 我透過 UAF 把 freepointer 改成 TARGET
        ▼
  freelist（被毒）：free_obj → [freepointer=TARGET]
        │ 連續 kmalloc：
        │   第 1 個 → free_obj
        │   第 2 個 → TARGET   ◀── allocator 把「TARGET 這個位址」當成一塊 object 發給你
        ▼
  你現在對 TARGET 有一塊「合法配置」的 object → 任意位址寫（把 TARGET 指向某個值錢結構）
```

這把「UAF write」升級成 **write-what-where**（任意位址寫，[Ch 10](./10-vulnerability-taxonomy.md) 頂級原語）——指定 freepointer = 某個 kernel 結構的位址，下次 alloc 就「配置」在那裡，你寫它等於寫那個結構。接 [Ch 16](./16-classic-primitives.md) 的 data-only 提權（改 cred / modprobe_path）就收工。這是 kernel heap 利用最通用的升級路之一。

## `CONFIG_SLAB_FREELIST_HARDENED` 怎麼擋 poisoning

freelist poisoning 太好用，於是有了 hardening（[Ch 9](./09-slub-hardening.md)）。`CONFIG_SLAB_FREELIST_HARDENED` 把存進 object 的 freepointer **做 XOR 混淆**，不是存裸指標：

```
  存入：  stored = real_next XOR per_cache_secret XOR address_of_this_slot
  讀出：  real_next = stored XOR per_cache_secret XOR address_of_this_slot
```

你透過 UAF 寫進去的是「你想要的 TARGET」，但 allocator 讀出來會 `XOR secret XOR addr`——結果是亂的，解出來不是 TARGET，下次 alloc 拿到的是垃圾位址，一碰就 panic。要繞過它，你得先**知道那個 per-cache secret**（通常要另一個 leak），或者走別的路（不依賴 poisoning 的技術，如 cross-cache、Dirty Pagetable）。

> 這也是為什麼 Part 3 後半（Ch 22–25）發展出「不碰 freelist 指標」的技術——hardening 把最直接的 freelist poisoning 路堵了一半，攻擊者改從 cross-cache、page table 這些 allocator 管不到的層面下手。知道 poisoning 的原理，才懂那些技術在繞什麼。（secret 的實際混淆公式隨版本略有不同，以你的 kernel source 為準，未逐版實測。）

## 踩雷集錦

1. **free 後沒 spray 就直接 use**——那塊還在 freelist，use 讀到的是 allocator 的 freepointer 或舊內容，不是你能控的東西。三拍節奏的第 2 拍（spray）不能跳。
2. **spray 物件和 victim 不同 size**——spray 要落到 victim 剛釋放的那塊，size 必須同 cache（和 Ch 17 同理）。用 kmalloc-96 的物件去佔 kmalloc-64 的洞，佔不到。
3. **spray 物件是 `GFP_KERNEL_ACCOUNT`、victim 不是（或反之）**——`kmalloc-cg-N` 分流（[Ch 9](./09-slub-hardening.md)），不同帳戶類別不同 slab，spray 永遠佔不到。選 spray 原語時確認它的 GFP flag 和 victim 一致。
4. **在 FREELIST_HARDENED 開著時寫裸 TARGET 當 freepointer**——會被 XOR 弄亂，下次 alloc 拿到垃圾、panic。先確認 hardening 是否開；開了就要 leak secret 或換技術，別硬寫。
5. **double free 之間沒有「中場 alloc」或時機不對**——SLUB 對「剛 free 又 free 同一塊」有時會在 debug/hardening 下偵測到（`CONFIG_SLAB_FREELIST_HARDENED` 也含簡單的 double-free 檢查：free 時檢查這塊是不是已經在 freelist 頂端）。要繞過這個檢查，double free 中間常需要插一次 alloc/free 打亂，別無腦連 free 兩次。

## 進階：UAF 是「萬能起點」

把 UAF 的升級路攤開，你會看到它幾乎能通往所有強原語：

```
  UAF
  ├─ dangling read  →  leak（spray 帶指標的物件）→ KASLR base
  ├─ dangling 觸發 call → 控流（spray 函式指標物件）→ ROP / 直接提權
  ├─ dangling write → 改 victim 欄位 → 視欄位升級（長度/指標）
  └─ + double free → freelist poisoning → write-what-where → data-only 提權
```

這就是為什麼現代 kernelCTF 的 bug 八成是 UAF（或能熬成 UAF 的 refcount/race）——它起點靈活、升級路多。也因此 Part 3 剩下的章節（cross-cache、Dirty Pagetable、USMA）很多是在回答「當 victim 不在我能配的 cache 裡」「當 FREELIST_HARDENED 擋住 poisoning」「當我需要的是任意實體讀寫而非單一位址」時，UAF 怎麼換個方式升級。本章的 free→spray→use 是這一切的骨架。

實戰建議：拿到一個 UAF，先列「我能 spray 哪些同 cache、同帳戶類別的可控物件」（查 Ch 19 的表），再決定走 leak 還是走 control。這一步想清楚，比急著寫 code 重要。

## 動手練習

在自有 QEMU 靶上，用上面的 UAF `vulndevice.c`：

1. free 後**不** spray 直接 use，觀察 `g_obj->fn` 是什麼（多半是 freepointer 或 0），體會「沒佔位的 UAF 碰不到有用的東西」。
2. free 後用 `msg_msg`（或自製的「開頭可控」物件）spray 同 size，再 use，把 `fn` 蓋成一個好認的值（如 `0xdeadbeef`），看 dmesg 的 fault RIP 確認控流到手。
3. 把 spray 物件換成「含 kernel 指標」的物件，改用 dangling **讀** 出那個指標，推 KASLR base。
4. 進階（FREELIST_HARDENED 關）：做一次 double free + freelist poisoning，把下一次 alloc 導到一個你指定的位址，驗證你能寫到那裡。再把 hardening 打開重跑，親眼看到 poisoning 失效、panic——理解 hardening 擋的是什麼。

## 本章重點整理

- UAF 利用三拍：free（留 dangling）→ spray（同 size、同帳戶類別、可控內容的物件佔位）→ use（type confusion）。
- SLUB 的 LIFO 讓「free 完立刻 spray 就拿回」幾乎免費，比 glibc heap 門檻低。
- double free 腐化 freelist，導向「兩個 live 物件疊同一塊」，給你同時「寫」和「被用」兩個 handle。
- freelist poisoning：改 free object 的 freepointer，指定下一次 kmalloc 的位址 → write-what-where。
- `CONFIG_SLAB_FREELIST_HARDENED` 用 XOR 混淆 freepointer 擋 poisoning，逼攻擊者 leak secret 或改走 cross-cache / Dirty Pagetable。

## 自我檢核

- [ ] 不看筆記，能說出 UAF 利用的三拍節奏，以及第 2 拍跳掉會發生什麼
- [ ] 能解釋為什麼 SLUB 的 LIFO 讓 UAF 比 glibc heap 好打
- [ ] 能說出 double free 和單純 UAF 在「你握有什麼 handle」上的差別
- [ ] 能描述 freelist poisoning 怎麼把 UAF write 升級成 write-what-where
- [ ] 能解釋 FREELIST_HARDENED 的 XOR 混淆擋住哪一招，以及為什麼它推動了 cross-cache 等技術

## 延伸閱讀

- **[pawnyable — Linux Kernel: Use-after-Free（Holstein v4）](https://pawnyable.cafe/linux-kernel/LK01-4/)**
  - **這篇說什麼**：用同構的靶示範 UAF free→spray→use，含 `tty_struct` 佔位與控流，附完整 code。
  - **讀哪裡**：整篇；對照本章三拍節奏與 spray 選擇。
  - **為什麼值得讀**：把本章骨架落到可直接照打的 exploit。
- **[CVE-2023-32233 nf_tables UAF（fix commit c1592a89）](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/commit/?id=c1592a89942e9678f7d9c8030efa777c0d57edab)**
  - **這篇說什麼**：一個真實、知名的 kernel UAF，root cause 在 nf_tables 的 abort path。
  - **讀哪裡**：commit message + diff；對照本章「真實 UAF 怎麼來」與 [Ch 42](./42-patch-to-rootcause.md) 的 patch 分析法。
  - **為什麼值得讀**：看真實 bug 長什麼樣，而不只是教學靶；它也是 [Ch 30](./30-netfilter-nftables.md) 的案例。
- **[SLAB_FREELIST_HARDENED 設計（mm/slub.c freelist_ptr / Kees Cook 2017）](https://elixir.bootlin.com/linux/v6.6/source/mm/slub.c)**
  - **讀哪裡**：搜 `freelist_ptr`、`freelist_ptr_decode`；看 XOR 混淆怎麼實作、secret 從哪來。
  - **和本章的關聯**：理解 poisoning 為何被擋、要繞需要什麼，直通 Ch 22–25 的動機。
  - **前提知識**：Ch 8/Ch 9 的 SLUB 與 hardening。

UAF 給了你靈活的起點，但它卡在一個前提：spray 物件必須和 victim 同 cache。當 victim 在一個**專用 slab cache**（dedicated cache，一般 user 配不出同 cache 的可控物件）時，前面的 spray 全失效。下一章先把常用的 spray 物件一次攤開，你才知道手上有哪些牌；接著 Ch 22 的 cross-cache 會教你在「配不出同 cache 物件」時怎麼跨 cache 打。

→ [Ch 19 — Heap spray 物件大全：msg_msg / sk_buff / pipe_buffer / tty_struct / seq_operations / user_key_payload](./19-spray-objects.md)
