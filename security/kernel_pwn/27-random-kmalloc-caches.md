# Ch 27 — 穿越 random kmalloc caches：hash 匹配、cg 分流、spray 策略、victim 挑選

> **目標**：把 [Ch 26](./26-modern-mitigations.md) 對 `CONFIG_RANDOM_KMALLOC_CACHES` 的概覽，變成一套可以實際套用的攻擊方法論。學完你要能拿到一個「victim 在 kmalloc-N、我有一批候選 spray 物件」的題目，具體說出：該挑哪個 call site 的物件、要 spray 幾份才夠、cg 分流能不能繞開整個問題、以及什麼時候該直接放棄 kmalloc 層、改走 cross-cache。

> **環境**：Linux 6.6 LTS、x86-64。本章延續 [Ch 26](./26-modern-mitigations.md) 查證過的事實：`CONFIG_RANDOM_KMALLOC_CACHES` 由 Gong Ruiqi 提出、6.6 合入 mainline，`default n`，`depends on SLUB && !SLUB_TINY`。本章的程式片段除非特別標注「實測」，否則**未實測，為理論預期行為**——它需要一顆自行編譯、開了這個 config 的 6.6 kernel，我們沒有在這個環境重建它。

## 為什麼需要這一章

[Ch 19](./19-spray-objects.md) 教的是牌庫：拿到一個 UAF 或 overflow，你知道 victim 落在哪個 `kmalloc-N`，就去牌庫挑一張同 size 的牌 spray 進去。這整套方法論有一個隱藏前提,[Ch 26](./26-modern-mitigations.md) 已經點破：**同一個 size 的 `kmalloc`,在 6.6 之前永遠只有一個 cache**。victim 和你的 spray 物件只要 size 一致,就保證落在同一塊 slab 池子裡,「佔到洞」只是數量和時機問題。

`RANDOM_KMALLOC_CACHES` 把這個前提拆了。同一個 size 現在對應到 16 份互不相通的 cache（原本一份 + 15 份複本),選哪一份不再看 size,而是看**呼叫 `kmalloc` 的那行程式碼在哪裡**。這意味著：**你的 spray 物件和 victim,即使 size 完全一樣,也可能落在不同的 slab 池子裡,完全碰不到面**。[Ch 19](./19-spray-objects.md) 那套「挑一張同 size 的牌」的直覺,在這個 mitigation 底下只是必要條件,不是充分條件。

這一章要做的事,是把「同 size 就能打」升級成「同 size **且同一份隨機複本**才能打」,然後問：這個新增的條件,攻擊者有哪些辦法滿足它、繞過它、或者索性不理它。

## 先建立直覺

把 [Ch 26](./26-modern-mitigations.md) 的 hash 機制換一個更貼近「打題目」的講法:

```
  沒有 random caches 的世界：
  ┌─────────────────────────────────────────────┐
  │              kmalloc-512                      │
  │  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐ │
  │  │victim │  │spray #1│ │spray #2│ │spray #3│ │  ← 全部同一個池子
  │  └───────┘  └───────┘  └───────┘  └───────┘ │     spray 夠多必中
  └─────────────────────────────────────────────┘

  有 random caches 的世界（16 份複本,畫 4 份示意）：
  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐
  │kmalloc-512 │ │kmalloc-512 │ │kmalloc-512 │ │kmalloc-512 │
  │  複本 #03  │ │  複本 #07  │ │  複本 #11  │ │  複本 #14  │
  │            │ │            │ │            │ │            │
  │  victim    │ │ spray 物件 │ │            │ │ spray 物件 │
  │ (call site │ │ A 的 call  │ │            │ │ B 的 call  │
  │     V)     │ │   site)    │ │            │ │   site)    │
  └───────────┘ └───────────┘ └───────────┘ └───────────┘
       ↑ victim 在這份,spray 物件 A 和 B 都在別份——完全碰不到

  如果 spray 物件 C 的 call site 剛好也 hash 到 #03：
  ┌───────────┐
  │kmalloc-512 │
  │  複本 #03  │
  │  victim    │
  │  spray C   │   ← 命中,這份 slab 兩者共存
  └───────────┘
```

重點不是「hash 讓它變隨機」,是**「call site」這個新維度**取代了「size」當作唯一的分類依據。你原本只需要回答「我的牌和 victim size 一不一樣」,現在多了一個你必須回答、而且沒辦法直接觀察的問題：「我的牌的 call site,和 victim 的 call site,hash 出來是不是同一份?」

> 如果你對「call site 固定、同一次開機內永遠落同一份複本」這個性質還不熟,先回看 [Ch 26 — 2023+ mitigation 全景](./26-modern-mitigations.md) 的「1. CONFIG_RANDOM_KMALLOC_CACHES」一節,把那張 hash 示意圖看懂再往下讀。

## 核心機制回顧：call site 是攻擊面,不是隨機性

把 [Ch 26](./26-modern-mitigations.md) 的 hash 公式再寫一次,這次從攻擊者的角度拆解每個項:

```
idx = hash( caller_ip ^ boot_seed ) % 15
```

- `caller_ip`：呼叫 `kmalloc`/`kmalloc_trace` 那一行程式碼的 return address。**這是編譯期就固定的值**——同一個 kernel binary,`msg_msg` 配置那行程式碼的位址永遠是同一個數字,不會因為你呼叫幾次而變。
- `boot_seed`：開機時產生的隨機值,**同一次開機內固定,不同開機不同**。
- `idx`：兩者 XOR 再 hash 的結果,決定落在哪一份複本。

把這三項放在一起看,得到這章最重要的一個推論：**「victim 的 call site」和「某個 spray 物件的 call site」之間的 hash 碰撞關係,在同一次開機內是一個固定、可觀察、可重複利用的事實——不是每次 attack run 都要重新賭的機率事件**。你只要在同一次開機內觀察到一次碰撞(例如用某種 side channel 或純粹嘗試後推斷出來),這次開機接下來所有的 exploit 嘗試都可以放心利用這個碰撞,直到系統重啟。

這和 KASLR base 的性質很像(同一次開機固定、重啟後變),所以你可以把「找到一組會碰撞的 call site 配對」想成和「leak KASLR base」同一等級的前置工作——做一次,這次開機全程有效。

## 技法一：挑同一個 call site 讓 spray 與 victim 走同一份

最直接的繞法,是**不要跟 hash 賭機率,直接讓 spray 物件和 victim 用同一行程式碼配置**。

什麼時候可能做到?回看 [Ch 19](./19-spray-objects.md) 的牌庫:很多牌本身就是「driver 配置物件之後填進 user 給的內容」。如果 victim **也是**同一種 user 可觸發的配置(例如 victim 本身就是一個 `msg_msg`,或者 victim 和某張牌共用同一條 kernel 內部的通用配置路徑,如 `kmemdup`、`memdup_user` 這類被很多子系統共用的 helper),那麼:

```
   victim 配置路徑：   ... → kmemdup(user_buf, len, GFP_KERNEL) → 落 call site K
   spray 候選 A 路徑： ... → kmalloc(sz, GFP_KERNEL) 在 driver 自己的函式裡 → call site D(≠K)
   spray 候選 B 路徑： ... → 也走 kmemdup(...) → 落 call site K（同一行!）

   候選 A：call site 不同,hash 幾乎必然碰不到 victim
   候選 B：call site 相同（同一行組合語言呼叫 kmalloc）,hash 保證相同 → 100% 同份複本
```

`kmemdup()`、`memdup_user()`、`kstrdup()` 這類**通用、被整個 kernel 大量子系統共用的小函式**是這個技法的金礦:它們自己只有一行呼叫 `kmalloc`,如果 victim 恰好也經由其中一個 helper 配置,你只要找到另一個「user 可觸發、同樣經由這個 helper、size 也落在同一個 bucket」的路徑,就拿到了一個 call site 保證相同的 spray 物件——不需要猜 hash,這是**結構性**的保證,不是機率。

> 這個技法的限制很現實：**不是每個 bug 都幸運到有這種「通用 helper」可用**。victim 常常是 driver 自己手寫的 `kmalloc(sizeof(struct xxx), GFP_KERNEL)`,call site 是這個 driver 自己的程式碼,世界上不會有第二個 syscall 路徑恰好跑到同一行指令。這種情況下你得接受機率,或改用技法三。

## 技法二：利用 kmalloc-cg 的分流繞過整個隨機化

[Ch 26](./26-modern-mitigations.md) 提過一句關鍵事實,這裡展開成完整攻擊面:**6.6 的 `RANDOM_KMALLOC_CACHES` 實作只對一般(normal)類型的配置做隨機化。帶 `__GFP_ACCOUNT` flag 的配置走的是 `kmalloc-cg-N`（memory cgroup accounting 用的 cache),這組 cache 不在隨機化範圍內**。

這件事對攻擊者的意義,不是「cg 比較好打所以去那邊打」——而是反過來:**它製造了一個結構性的「錯位」或「對齊」機會**,取決於 victim 和 spray 候選物件各自帶不帶 `__GFP_ACCOUNT`：

```
  情境 A（錯位,random caches 幫倒忙變成幫攻擊者的忙）：
    victim：帶 __GFP_ACCOUNT  → 落 kmalloc-cg-512（固定,不隨機）
    spray 候選：也帶 __GFP_ACCOUNT，size 同  → 同樣落 kmalloc-cg-512
    → 兩者在同一個「未隨機化」的池子裡,random caches 對這組配對完全不生效,
      等於回到 6.6 之前的狀態,spray 策略照搬 Ch 19 不用改。

  情境 B（真正的分流,random caches 生效）：
    victim：GFP_KERNEL（不帶 ACCOUNT） → 落 16 份隨機複本之一
    spray 候選：GFP_KERNEL_ACCOUNT    → 落 kmalloc-cg-512（固定池）
    → 兩者連「有沒有被隨機化」都不一樣,根本不是同一類 cache,
      這組配對無論 hash 怎麼算都打不中——這是 Ch 19 已經點出的「帳戶類別鐵律」,
      在 random caches 之下變得更嚴格：帳戶類別不只決定「是不是同一塊 slab 池」,
      還決定「這個池是不是隨機化的」。
```

攻擊者要做的事,其實是**在挑選 victim 和挑選 spray 物件之前,先查清楚兩者各自的 GFP flag**——這比查 size 更優先。哪些配置帶 `__GFP_ACCOUNT`?一般規律是:凡是**可以被 memory cgroup 計入用量上限**的配置,也就是大多數「代表某個 user process 擁有的資源」的物件(file descriptor 相關結構、部分 socket 相關配置、部分 user namespace 資源)傾向帶 ACCOUNT;而核心基礎設施、per-CPU 結構、許多不直接對應單一 process 的配置則傾向不帶。**精確答案只能從對應的 `kmem_cache_alloc`/`kmalloc` 呼叫點的 GFP flag 字面值確認,沒有通用公式**,這點本章不假裝有捷徑。

## 技法三：用數量壓機率

如果 victim 的 call site 無法和任何現成牌對齊(技法一用不上),且帳戶類別也對不上(技法二用不上),剩下的路就是**正面迎接機率**。

[Ch 26](./26-modern-mitigations.md) 給過粗估:兩個獨立 call site 落在同一份複本的機率大約是 1/16(16 份裡選中特定一份;若只看「落在 victim 所在那一份」這個單一目標,機率即 1/16,假設 hash 近似均勻分布)。重點是**這是單一候選物件的命中率,不是你整個 spray 行動的成功率**——如果你能找到 M 個「call site 互不相同」的候選牌(牌庫裡不同的 spray 物件,或同一種物件但用不同方式觸發出不同 call site),每個候選各自有 1/16 落中 victim 所在複本的機率,**只要有任何一個候選命中**,你就能在那份複本裡繼續 spray 大量該候選物件去填滿、佔位。

近似估計(假設各候選彼此獨立,未實測,為理論估算):

```
  M 個不同 call site 的候選牌,至少一個命中 victim 複本的機率 ≈ 1 - (15/16)^M

  M = 1  → ≈ 6.25%
  M = 4  → ≈ 22.5%
  M = 8  → ≈ 40%
  M = 16 → ≈ 64%
  M = 44 → ≈ 95%（粗估,僅供感覺量級,非精確值）
```

這張表的教訓是:**提高候選牌「種類」的數量,比提高同一張牌的 spray「份數」更有效**。狂 spray 1000 份同一個 call site 的物件,如果那個 call site 沒有 hash 到 victim 的複本,一份都佔不到——1000 份全部落在錯誤的池子裡。真正該做的是:盤點牌庫裡**所有** size 符合、user 可觸發的配置路徑(不只是 [Ch 19](./19-spray-objects.md) 列的那幾張熱門牌,還包括冷門但 size 吻合的任何 syscall 路徑),每一種都當作一個獨立的「複本賭注」,各 spray 一批,擴大 M。

這個技法的代價是**雜訊**:你同時往很多份複本裡塞東西,其中大部分對這次 exploit 毫無用處,徒增系統負擔、增加被其他監控偵測到異常的機會,而且你事後很難直接確認「哪一個候選真的中了」——通常要搭配 leak 原語,讀出某個候選物件的內容,反推它是不是真的和 victim 相鄰/重疊。

## 技法四：繞過整個 kmalloc 隨機化——回到 cross-cache

技法一到三都假設「我還在 `kmalloc` 這層打」。但 [Ch 26](./26-modern-mitigations.md) 已經說得很清楚:**`RANDOM_KMALLOC_CACHES` 管的只是『同一個 size 選哪個 `kmalloc-N` 複本』,它完全不管 page 層的回收與重用**。

回看 [Ch 22](./22-cross-cache.md) 的 cross-cache 技法:把一個 slab 整個釋放回 buddy allocator([Ch 7](./07-buddy-allocator.md)),再讓**另一種、甚至不是 `kmalloc` 這條路配置的 cache**(例如某個 dedicated cache)拿到同一個 physical page。這整套操作完全不經過「選哪個 `kmalloc-N` 複本」這個決策點——因為它根本不是在比較「兩個 `kmalloc` call site 的 hash 是否相同」,而是在比較「哪一種 cache 下一次剛好去 buddy allocator 要到了這塊被釋放的 page」。

```
   RANDOM_KMALLOC_CACHES 管的範圍：
   ┌───────────────────────────────────────┐
   │   kmalloc(sz) 的 call site 選哪份複本  │  ← 技法一/二/三在這裡打
   └───────────────────────────────────────┘

   cross-cache 操作的範圍：
   ┌───────────────────────────────────────┐
   │  slab 釋放 → buddy allocator → 任意    │  ← random caches 不介入這一層
   │  cache 重新要到同一塊 physical page     │     只要 victim 走的是「釋放整個 slab」
   └───────────────────────────────────────┘     這條路,cache 怎麼隨機化都不相關
```

這是本章最重要的取捨觀念:**random caches 把「同 cache 內的 spray」這條路變貴,逼攻擊者往兩個方向逃:一是技法一~三(接受貴,但還留在 kmalloc 這層),二是技法四(索性跳出 kmalloc 這層,去 page 層打)**。哪一條路划算,取決於你手上的 bug 給的原語——如果 bug 本身能觸發 slab 整頁釋放(例如整個 cache 因為某個條件被銷毀、或是透過 [Ch 18](./18-uaf-double-free.md) 的手法把整個 slab 的所有物件都釋放乾淨),cross-cache 往往比在 kmalloc 層硬賭 hash 碰撞更可靠。

## 具體例子

### 例子一:call site 對齊成功的案例(概念示範)

假設某個教學用 vulnerable module(接 [Ch 18](./18-uaf-double-free.md) 的靶)在 `ioctl` 裡用 `memdup_user(ubuf, 512)` 配置一個 512 byte 的 victim 物件,之後有 UAF。我們要找一個也經由 `memdup_user` 配置、size 同樣落在 512 bucket 的 user 可觸發路徑。

```c
/* 概念性 PoC 骨架,未實測,為理論預期行為 */

/* 先確認:victim 的配置路徑是不是真的經由 memdup_user。
   方法:讀 driver 原始碼,或者如果沒有原始碼,用 Ch 44 的 ftrace
   在你自己的受控環境裡,對 kmalloc 相關 tracepoint 加上 call site 過濾,
   觀察觸發 victim 配置時記錄到的 caller。*/

/* 找到的候選:某個也呼叫 memdup_user 的 syscall,
   例如某些 setsockopt 選項的 optval 複製路徑(需查你的 kernel 版本是否仍如此實作)。
   如果候選的 size 可調,調到和 victim 一致(512,扣掉任何 header)。*/

for (int i = 0; i < 200; i++) {
    char buf[512];
    memset(buf, 0x41, sizeof(buf));
    /* 透過候選路徑配置,size 對齊,且 call site 與 victim 相同 */
    setsockopt(sock[i], SOL_SOCKET, SOME_OPTION, buf, sizeof(buf));
}
/* 因為 call site 與 victim 相同,hash 保證落在同一份複本,
   不需要考慮 1/16 的機率問題。*/
```

這個例子的關鍵不是這段 code 能不能編譯跑過(它刻意留白,因為「哪個 syscall 走 `memdup_user`」隨版本變動,需要你對自己的目標 kernel 逐一確認),是方法論:**先查 victim 的配置路徑是不是經由某個通用 helper,再去找另一條走同一個 helper 的 user 可觸發路徑**,這是技法一能不能用上的判斷依據。

### 例子二:失敗案例——call site 不同,900 份全部落空

反過來,假設你誤判了,以為 [Ch 19](./19-spray-objects.md) 的 `msg_msg` 牌能打中一個 driver 自己手寫 `kmalloc(sizeof(struct vuln_obj), GFP_KERNEL)` 的 victim,直接照搬舊流程 spray 900 份 `msg_msg`:

```c
/* 概念性,未實測,為理論預期行為 */
for (int i = 0; i < 900; i++) {
    struct { long mtype; char buf[512 - 48]; } m;
    m.mtype = 1;
    memset(m.buf, 0x41, sizeof(m.buf));
    msgsnd(qid[i], &m, sizeof(m.buf), 0);
}
/* 預期結果(沒有 RANDOM_KMALLOC_CACHES 時):900 份裡總有幾份落進 victim 所在的 slab。
   預期結果(開了 RANDOM_KMALLOC_CACHES 時):
   msg_msg 的配置 call site(load_msg/alloc_msg 內的 kmalloc)和
   driver 自己手寫的 call site 是兩個完全不同的位址,
   hash 幾乎必然(約 15/16 機率)落在不同複本。
   900 份 msg_msg 全部進了另一份複本,victim 所在那份複本完全沒被碰到——
   UAF 觸發後讀/寫到的是別的東西,leak 不出預期的值,
   或根本看不到任何變化(因為沒有任何 spray 物件真的佔到那塊記憶體)。*/
```

這正是技法三要解決的情境:單一種牌、單一個 call site,900 份也救不回來,因為問題不是「數量不夠」而是「這整批數量全押在錯誤的複本上」。正確做法是把這 900 份拆成多種不同 call site 的候選,各出一部分,擴大「至少一種押對複本」的機率(技法三),或者先想辦法確認 victim 有沒有可共用的 helper 可以直接對齊(技法一)。

### 例子三:跳出 kmalloc 層,改走技法四(概念示範)

延續例子二的失敗情境——driver 自己手寫 `kmalloc`,找不到任何共用 helper,帳戶類別也和現成的牌不一致。假設這個 bug 的額外特性是:觸發某個 ioctl 清理路徑時,driver 會把自己持有的**整批** victim 物件一次性 `kfree`,而不是一個一個釋放(這類「批次釋放」在 driver 的 teardown 邏輯裡並不少見,例如 `close(fd)` 時把內部陣列全部清空)。這種批次釋放,如果釋放的數量足夠多,有機會讓對應的 slab page 整頁變空、被 SLUB 還給 buddy allocator([Ch 7](./07-buddy-allocator.md)、[Ch 8](./08-slub-allocator.md))。

```
  概念流程(未實測,為理論預期行為,細節請對照 Ch 22 的完整版本):

  1. 配置一批 victim 物件(觸發到足以讓某個 slab page 裝滿的數量)。
  2. 觸發 driver 的批次釋放路徑(例如 close 整批 fd),
     讓裝著這批 victim 的 slab page 整頁變空,還給 buddy allocator。
  3. 不去想「哪個 kmalloc call site 會分到這個複本」——
     改用 page-level spray(Ch 25 的 USMA / pgv 類技巧,
     或任何能大量消耗 buddy allocator 同 order 的配置路徑),
     去搶同一塊剛被還回去的 physical page。
  4. 這塊 page 現在可能被完全不同的 cache(甚至非 kmalloc 路徑的配置)佔據,
     dangling pointer(如果 bug 本身還留著一個指向舊 victim 的指標)
     此刻指向的是新的、你能控制內容的物件——
     這一步之後的操作,RANDOM_KMALLOC_CACHES 從頭到尾沒有介入,
     因為我們全程沒有再呼叫任何一次「選 kmalloc-N 複本」的決策路徑。
```

這個例子刻意留在概念層級,因為完整的 cross-cache 操作(怎麼確保 page 整頁變空、怎麼贏得下一次 buddy allocator 配置的競賽)是 [Ch 22](./22-cross-cache.md) 一整章的內容,這裡只需要你看懂一件事:**技法四的勝負關鍵在「page 層的時機」,不在「kmalloc 層的 hash」**——這是它完全不受本章其餘三個技法所談的任何機率或對齊規則影響的原因。

## victim 挑選:反過來問「哪個 bug 值得打」

前面四個技法都假設「victim 已經定了,我在想辦法讓 spray 物件配合它」。但如果你手上同時有好幾個候選漏洞(例如在 fuzzing 報告裡挑一個來精修,見 [Ch 43](./43-syzkaller.md)),`RANDOM_KMALLOC_CACHES` 的存在也該反過來影響你**選哪個 bug 打**這個決策。

評估一個候選 bug 時,除了 [Ch 10](./10-vulnerability-taxonomy.md) 教的「它屬於哪一類、能換到什麼原語」,現在要多問三個問題:

1. **victim 的配置路徑是手寫 `kmalloc` 還是經由通用 helper?** 經由 `kmemdup`/`memdup_user`/`kstrdup` 的,技法一有機會用上,優先選它。
2. **victim 帶不帶 `__GFP_ACCOUNT`?** 查到它帶 ACCOUNT,代表你只要找同樣帶 ACCOUNT 的候選牌,就完全繞過隨機化——這類 bug 的「有效難度」比表面上看起來的低,值得優先投入。
3. **bug 的原語允不允許整個 slab 層級的操作(而不只是單一物件的 UAF)?** 如果 driver 的釋放路徑剛好會把整塊 slab 清空(例如 driver 被 rmmod、或某個 cleanup 路徑一次性釋放了該 cache 裡的所有物件),技法四(cross-cache)的門檻會明顯降低,這類 bug 即使在 kmalloc 層打起來很貴,換個路徑可能很便宜。

反過來,一個「victim 是 driver 自己手寫 `kmalloc`、不帶 ACCOUNT、且釋放路徑一次只釋放單一物件」的 bug,是這四個技法裡最難啃的組合——只能靠技法三硬堆數量,而且數量堆上去之後命中率的天花板就是 1-(15/16)^M 這條曲線,M 再大也不可能到 100%。拿到多個候選時,把這套評分表套上去,往往能在動手實作前就先篩掉「理論上能打,實務上太貴」的選項。

## 怎麼知道自己是不是碰撞了:觀測線索

技法三(數量壓機率)有一個實務上的痛點:你 spray 完之後,**怎麼知道有沒有真的命中**?沒有命中的徵象通常是「安靜的失敗」——預期的 leak 讀不到值,或讀到的是垂圾/不相關的資料,而不是明確的錯誤訊息。下面是幾個能幫你縮小「到底是 hash 沒碰撞,還是別的地方寫錯了」這個排錯範圍的線索:

- **`/proc/slabinfo`(需 root,僅你自己受控環境適用)**:如果能在受控環境裡用 root 身分檢查,`grep kmalloc-rnd` 能看到各份複本目前的 object 數量與 slab 數量。spray 完之後,比對「victim 所在複本」和「你的 spray 物件實際進了哪一份」——這是最直接但在真實攻擊情境中通常拿不到的 oracle,留給你在自己的靶機上驗證技法是否正確時用。
- **side-channel 式的間接推斷**:如果你同時掌握一個能讀取「某個已知物件的相鄻記憶體內容」的 leak 原語(例如 [Ch 19](./19-spray-objects.md) 的 `msg_msg` 任意讀),可以在 spray 之後嘗試讀出緊鄰 victim 的記憶體,看看裡面有沒有出現你 spray 物件的 magic bytes——出現了代表這份複本確實混進了你的候選物件。
- **多輪次重試並記錄結果的一致性**:同一次開機內,同一組 call site 配對的碰撞結果應該是**穩定**的(碰撞了就一直碰撞,沒碰撞就一直沒碰撞)。如果你的 exploit 第一次試沒反應,換個候選牌(不同 call site)再試,比在同一張牌上反覆重試更有意義——因為後者的結果在同一次開機內理論上不會變。

> 這三個方法都假設你已經有某種「觀察 slab 內容」的能力(leak 原語或 root 權限)。如果連這個都沒有,代表你還停留在「拿到 bug,但原語還沒建立起來」的階段——回頭看 [Ch 10](./10-vulnerability-taxonomy.md) 的原語升級鏈,先把「能讀」這一步做出來,再回來談 spray 命中率的驗證。

## 對比與取捨

| 技法 | 前提條件 | 成本 | 確定性 | 適用情境 |
|---|---|---|---|---|
| 技法一:call site 對齊 | victim 經由通用 helper(`kmemdup`/`memdup_user` 等)配置,且能找到另一條走同 helper 的 user 可觸發路徑 | 低(一旦找到,spray 和 Ch 19 一樣直接) | 高(結構性保證,非機率) | victim 配置方式夠「通用」時的首選 |
| 技法二:cg 分流 | victim 與候選牌的 `__GFP_ACCOUNT` 狀態可查、可利用的錯位或對齊 | 低到中(查 GFP flag 需讀原始碼或靠 side channel) | 高(結構性,只要帳戶類別判斷正確) | 兩者都帶或都不帶 ACCOUNT 時直接繞過隨機化;狀態不一致時反而是阻力 |
| 技法三:數量壓機率 | 有多種(不只一種)size 吻合、call site 互異的候選牌 | 高(雜訊大、spray 量大、難以事後驗證命中哪一個) | 低(機率性,約 1-(15/16)^M) | 前兩者都不可行時的保底手段 |
| 技法四:cross-cache | bug 能觸發整個 slab 層級的釋放(非單一物件 UAF) | 中到高(需要額外的 page 層操作知識,見 [Ch 22](./22-cross-cache.md)) | 中(決定於能否穩定觸發 slab 回收) | random caches 把 kmalloc 層打得太貴時,整個跳出去 |

## 踩雷集錦

1. **以為「size 一樣就一定同 cache」還是舊世界的直覺**——這是 [Ch 19](./19-spray-objects.md) 的牌庫邏輯,在開了 `RANDOM_KMALLOC_CACHES` 的 kernel 上**不成立**。拿到新題目,第一件事是確認目標 kernel 有沒有開這個 config(`/proc/config.gz` 查 `RANDOM_KMALLOC_CACHES`),開了就要多想一層 call site。
2. **把「同一次開機內 hash 固定」誤解成「每次重啟都要重新試」**——沒錯,重啟後 `boot_seed` 換了,碰撞關係會變;但在**同一次**開機內,你做一次「這組 call site 配對會不會碰撞」的確認,接下來這次開機所有嘗試都可以沿用這個結論,不需要每次 spray 前都重新賭。
3. **技法二搞反方向**——以為「cg 分流就是去 kmalloc-cg 打比較好打」,但真正重要的是**先確認 victim 自己帶不帶 ACCOUNT**,再決定你的 spray 物件要不要也帶。選錯邊,帳戶類別不一致會讓兩者連池子都不同,比 random caches 本身更徹底地阻斷你。
4. **技法三狂加 spray 數量卻不換種類**——如果你的 900 份全部是同一個 call site,加到 9000 份命中率依然是 0(或趨近 0,除非恰好命中,屬於固定的 1/16 事件,不會因為同一類數量增加而提升)。要提升的是「call site 的種類數」M,不是「同一個 call site 的份數」。
5. **忽略技法一需要讀原始碼才能確認**——「victim 經由某個 helper 配置」不是猜的,是讀 driver 和對應 syscall path 的原始碼([Ch 2](./02-reading-kernel-source.md) 的方法)或用 ftrace([Ch 44](./44-debug-toolchain.md))動態確認 call site 才能知道。沒有原始碼存取權時(例如 blackbox CTF 題目只給 binary),這個技法的門檻會明顯提高,通常只能退回技法三或四。
6. **拿到好幾個候選 bug,卻不先用 victim 挑選的三個問題篩一遍就直接動手**——「victim 挑選」一節的三個問題(配置路徑、ACCOUNT 狀態、釋放粒度)花幾分鐘讀原始碼就能回答,能先篩掉投入產出比最差的候選,省下大量後面硬堆 spray 數量卻打不中的時間。

## 進階:再往深一層

### 為什麼不是 32 份、64 份——複本數量的取捨

[Ch 26](./26-modern-mitigations.md) 查證到最終版本是 15 份複本加原本一份,共 16。這個數字不是隨便挑的:複本數量每增加一倍,大致上能把單一候選的命中機率砍半,但代價是**記憶體碎片化**(每個 size bucket 現在要維護 16 個池子而不是 1 個,每個池子底下的 slab 可能都沒填滿,浪費率上升)和**cache locality**(原本相關的物件可能更分散在不同的 physical 位置)。作者的設計取捨是在「讓攻擊者的機率夠低」和「不要把記憶體開銷搞得無法接受」之間找平衡點——這也是本章「技法三:用數量壓機率」在理論上永遠有效、但實務上攻擊者和防守者都在賭報酬率的原因:防守端賭「1/16 已經讓大多數攻擊成本不划算」,攻擊端賭「M 夠大就能把機率拉回可接受範圍」。

### 讀原始碼確認你自己 kernel 的行為

如果你的受控環境已經開了這個 config,最直接的驗證方式是去讀 `mm/slab_common.c` 裡 `kmalloc_slab()` 相關的程式碼路徑(`kmalloc_type()` 與 `KMALLOC_RANDOM_START`/`KMALLOC_RANDOM_END` 這組 enum 值,出現在 `include/linux/slab.h`),對照 [Ch 26](./26-modern-mitigations.md) 延伸閱讀列出的 patch 討論串,確認你所用版本的確切複本數與 hash 函式。細節可能在不同 6.6.x 的 patch release 之間有微調,**以你實際使用的 tree 為最終依據**。

## 動手練習

> 以下全部在你自己的 VM/靶機操作。前兩項**未實測**(需要一顆自行編譯、開 `RANDOM_KMALLOC_CACHES` 的 6.6 kernel,本環境沒有重建);第三項是純讀原始碼,可在任何環境做。

1. 用 [Ch 26](./26-modern-mitigations.md) 動手練習第 2 題編出來的全開 kernel,寫一個教學用 vulnerable module,裡面故意用 `memdup_user` 配置 victim,再寫一個 user-space 程式,分別用「同樣經由 `memdup_user` 的另一個 syscall 路徑」和「driver 自己手寫 `kmalloc` 的路徑」兩種方式 spray,比較兩者命中 victim 所在 slab 的比率(用 [Ch 18](./18-uaf-double-free.md) 的 UAF 讀取驗證)。
2. 針對同一個 victim,固定用技法三,分別嘗試 M=1(單一種 call site,spray 200 份)和 M=8(八種不同 call site,各 spray 25 份,總數相同)兩種策略,記錄命中率差異,驗證「種類比份數重要」這個結論。
3. 到 [Bootlin Elixir](https://elixir.bootlin.com/linux/v6.6/source/mm/slab_common.c) 讀 `kmalloc_slab()` 與相關的 `kmalloc_caches` 陣列定義,確認 6.6 tag 下複本數量與 `RANDOM_KMALLOC_CACHES_NR` 的實際數值,和本章引用的「15 份複本」對照。

## 本章重點整理

- `RANDOM_KMALLOC_CACHES` 把「選 cache」的依據從「size」換成「size + call site」,但同一個 call site 在同一次開機內永遠固定——這是攻擊面而不是純隨機性。
- 技法一(call site 對齊,經由通用 helper)和技法二(cg 分流)都是**結構性**繞法,找到就是確定命中;技法三(數量壓機率)是保底手段,重點是增加 call site 種類數 M,不是增加同一種的份數。
- `RANDOM_KMALLOC_CACHES` 只管 `kmalloc` 這條路的 cache 選擇,完全不管 page 層的回收——cross-cache([Ch 22](./22-cross-cache.md))可以整個繞過這個 mitigation。
- 防守端的取捨是「複本數量 vs 記憶體碎片化」,16 份不是絕對值,是一個經過權衡的設計選擇。

## 自我檢核

- [ ] 不看筆記,能解釋為什麼「同一次開機內 hash 固定」讓攻擊者不需要每次嘗試都重新賭機率
- [ ] 能說出技法一和技法三在「確定性」上的本質差異,以及這決定了你會優先嘗試哪一個
- [ ] 能解釋為什麼 cg 分流對攻擊者而言既可能是「幫倒忙的隨機化缺口」也可能是「阻力」,差別在哪裡
- [ ] 能說出為什麼 cross-cache 完全不受 `RANDOM_KMALLOC_CACHES` 影響
- [ ] 面試被問「random kmalloc caches 擋住 spray 之後,你還有哪幾條路」,能在一分鐘內講出至少三條嗎?
- [ ] 想一個你讀過的 kernel driver:它的 victim 配置是走自己手寫的 `kmalloc`,還是走某個通用 helper?這決定了技法一對它可不可用。

## 延伸閱讀

### Patch 與討論串(一手來源)

- **[Randomized slab caches for kmalloc()（Gong Ruiqi，LKML 2023）](https://lkml.rescloud.iu.edu/2307.1/07505.html)**
  - **這篇說什麼**：本章技法一到三的機制依據全部來自這份 patch 的設計描述,包含「為什麼只隨機化 normal 類型、不動 accounted 的配置」這個決定背後的討論。
  - **讀哪裡**：cover letter 的 motivation 段,以及討論串裡關於 `kmalloc-cg` 排除範圍的 review 往來。
  - **和本章的關聯**：技法二(cg 分流)的直接出處;前置知識是 [Ch 9](./09-slub-hardening.md) 的 cgroup accounting 介紹與 [Ch 26](./26-modern-mitigations.md)。

### 部落格 / 技術文章

- **[pawnyable — Linux Kernel](https://pawnyable.cafe/linux-kernel/)**
  - **這篇說什麼**：中日文圈對現代 kernel heap 利用技術的持續更新教材,涵蓋隨機化 mitigation 下的 spray 調整思路。
  - **讀哪裡**：和 heap 利用、現代 mitigation 相關的章節(站點持續更新,具體標題以當下目錄為準)。
  - **為什麼值得讀**：這份教材的靶題設計本書 Part 3 多次參考過,風格偏向「先給可重現的靶,再講繞法」,適合你把本章方法論落到具體程式碼上驗證。

### 官方文件 / 原始碼

- **Linux 原始碼 `mm/slab_common.c`、`include/linux/slab.h`**（[Bootlin Elixir](https://elixir.bootlin.com/linux/v6.6/source/mm/slab_common.c)）
  - **讀哪裡**：`kmalloc_slab()` 的 cache 選擇邏輯,以及 `slab.h` 裡 `kmalloc_type()` 怎麼區分 normal / cgroup accounted 兩類。
  - **和本章的關聯**：本章「技法二」整個建立在這段程式碼決定的分流邏輯上;動手練習第 3 題直接對應。
  - **前提**：[Ch 2](./02-reading-kernel-source.md) 的讀碼方法、[Ch 8](./08-slub-allocator.md) 的 SLUB 基礎。

這一章把「隨機化讓 spray 變貴」的局面,拆成了四條可操作的反制路徑。但這些技法全部假設攻擊者還停在「控制流劫持」的思路上——劫到 RIP 之後要跑 ROP、要呼叫 `commit_creds`。下一章先把 CFI/KCFI 這道閘門講清楚,之後我們會發現,就算你穿過了 random caches、拿到乾淨的覆寫能力,控制流這條路本身可能已經被封死,逼你轉向完全不同的思路。

→ [Ch 28 CFI / KCFI：原理、為什麼 kernel ROP 路越走越窄](./28-cfi-kcfi.md)
