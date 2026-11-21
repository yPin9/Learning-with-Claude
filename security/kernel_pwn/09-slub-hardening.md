# Ch 9 — SLUB hardening：dedicated caches、ACCOUNT/cg、cache merge、FREELIST_HARDENED/RANDOM

> **目標**：理解現代 kernel 在 SLUB 上加裝的幾層防禦——freelist pointer 混淆、freelist 隨機化、redzone/poison 偵測、記憶體記帳如何分流 cache、cache 合併規則如何決定「你能用什麼物件打什麼物件」。這些機制合起來解釋了為什麼 2024 年之後的 kernel heap exploitation 比幾年前難得多，也是 Part 4（Ch 26–27）繞過技巧的前置知識。

> **環境**：Linux 6.6 LTS、x86-64。本章提到的多個 config 選項（`CONFIG_SLAB_FREELIST_HARDENED`、`CONFIG_SLAB_FREELIST_RANDOM`、`CONFIG_SLUB_DEBUG`、`CONFIG_HARDENED_USERCOPY`、`CONFIG_MEMCG_KMEM`）各自獨立，實際生產環境、CTF 題目、Android/ChromeOS kernel 開啟的組合都不同——**本章講的是每一層機制本身怎麼運作，不是「6.6 預設全部開啟」的斷言**。你拿到一個目標 kernel 時，第一件事永遠是去確認它實際開了哪些（`/boot/config-$(uname -r)` 或題目附的 config 檔）。

## 為什麼需要這個？

Ch 8 講的是「未加固」的 SLUB——freepointer 直接存在 object 開頭，LIFO 行為可預測，cache 選擇只看大小。如果現實世界的 kernel 真的長這樣，Part 3 的技巧會簡單得多：覆寫一個 freepointer 就能讓下一次配置回傳攻擊者指定的任意位址。

但真實世界的 kernel，尤其是 kernelCTF、Android、ChromeOS 這些會被持續攻防的目標，早就不是這個樣子。這一章要回答：

- 如果 freepointer 被混淆了，攻擊者連「正確地覆寫它」都做不到，exploit 要怎麼調整?
- 為什麼有些 heap overflow 寫壞了 freepointer，系統會直接 panic，而不是讓你靜悄悄地劫持下一次配置?
- 為什麼有時候你明明算對了 size class，噴出去的物件卻沒有如預期和目標物件落在同一個 cache?
- 這些 hardening 機制是「不可繞過」還是「提高成本」?邊界在哪裡?

這一章的機制，大多**提高攻擊門檔，不是把漏洞類別直接消滅**——理解這個分寸，是判斷「一個 CVE 在現代 kernel 上還能不能打」的核心能力，也是 Ch 51（防禦視角）的鋪墊。

## 先建立直覺

把 Ch 8 的「固定尺碼倉庫」比喻延伸一層：hardening 機制做的事，大致可以分成三類直覺：

```
1. 把「鑰匙」加密
   （freepointer 混淆：你覆寫的那串 bytes，
    不再是「下一個空格子的真實位址」，
    而是一串看起來正常但你解不開的密文）

2. 讓「排隊順序」變得不可預測
   （freelist 隨機化：你猜不到下一個被
    拿走的是哪個格子）

3. 把「不同客戶的貨」分流到不同倉庫
   （cache merge 限制 / ACCOUNT 分流：
    你以為能用的物件，
    其實被導向了完全不同的 cache，
    根本碰不到你的目標）
```

這三類直覺分別對應本章的三大機制群：freelist 保護、記憶體記帳與分流、cache 合併規則。我們一個一個拆開。

## 歷史脈絡：這些機制從哪裡來

本章講的大多數機制，不是 kernel 上游自己先想出來的——它們的原型大量來自 **grsecurity / PaX** 這個長年獨立維護、專注 kernel hardening 的第三方 patch set（由 Brad Spengler 與 PaX Team 主導）。2010 年代中期，PaX 的部分設計（包括 freelist pointer 混淆的概念）被 Kees Cook 等人重寫、簡化、移除大量 `#ifdef`，以更容易被上游接受的形式送進主線 kernel。這個「第三方 hardening 專案先驗證可行性、再被簡化後吸收進主線」的模式，之後也催生了 **Kernel Self Protection Project（KSPP）**——一個專門追蹤、推動這類硬化機制進入上游 kernel 的協作專案,本章提到的多數 config 選項,都能在 KSPP 的提案清單裡找到它們的討論脈絡。

理解這段歷史的實用價值：當你讀到一個新的 hardening 機制的提案或 commit，可以先去 grsecurity/PaX 的歷史文件或 KSPP wiki 查「這個概念是不是已經在別的地方驗證過」，往往能更快抓到設計動機，而不是只看上游 commit message 裡精簡後的說明。

## 核心機制一：`CONFIG_SLAB_FREELIST_HARDENED`——freepointer 混淆

### 它解決什麼問題

Ch 8 提過，freepointer 直接存在 object 記憶體裡，意味著**任何能寫到那個 object 的 bug（UAF、heap overflow）都能直接竄改 allocator 的下一次配置決策**。這是幾乎所有經典 SLUB heap exploit 的起點：覆寫 freepointer，讓 SLUB「相信」下一個空閒物件在攻擊者選的任意位址，下一次 `kmalloc()` 就回傳那個位址,實現「任意位址配置」原語。

### 它怎麼運作

**依查證**：這個機制由 Kees Cook 在 2017 年提交（修改自 Brad Spengler / PaX Team 原本的概念），合併時的 commit 標題是「mm: add SLUB free list pointer obfuscation」，核心邏輯在 `mm/slub.c` 的 `freelist_ptr()`（或對應版本的等價函式）。原理是：**每個 kmem_cache 配置時產生一個隨機值（存在 `kmem_cache->random`），freepointer 實際存入記憶體前，先和「這個值」以及「freepointer 本身存放的那個記憶體位址」做 XOR**：

```c
/* 概念示意 */
static inline void *freelist_ptr(const struct kmem_cache *s, void *ptr,
                                  unsigned long ptr_addr)
{
    return (void *)((unsigned long)ptr ^ s->random ^ ptr_addr);
}

static inline void set_freepointer(struct kmem_cache *s, void *object, void *fp)
{
    unsigned long freeptr_addr = (unsigned long)object + s->offset;
    *(void **)freeptr_addr = freelist_ptr(s, fp, freeptr_addr);
}

static inline void *get_freepointer(struct kmem_cache *s, void *object)
{
    unsigned long freeptr_addr = (unsigned long)object + s->offset;
    return freelist_ptr(s, *(void **)freeptr_addr, freeptr_addr);
}
```

因為 XOR 是自反運算（`A ^ B ^ B = A`），讀取時再做一次同樣的 XOR 就能還原真正的指標值。效果是：記憶體裡實際存的那串 bytes，**既不是真正的下一個物件位址，也不是一個固定的常數**——它同時依賴「這個 cache 的隨機種子」和「這個 freepointer 本身在記憶體裡的位址」。

### 這對攻擊者意味著什麼

如果你要透過 heap overflow 寫入一個偽造的 freepointer，讓下一次配置回傳你選的位址,在沒有 hardening 時你只需要寫入「目標位址」這個值本身。**有了混淆之後，你必須先知道（或推算出）`s->random`**——這通常需要一次額外的 info leak（讀到同一個 cache 裡任何一個物件的 freepointer 原始密文，加上你已經知道它「應該」解出什麼值，反推出 `s->random`），才能構造出正確的密文寫進去。這不是讓攻擊不可能，是**多加了一個「先 leak 再算」的步驟**，而且這個 leak 往往本身就需要一個獨立的漏洞或原語。

**依查證**：這個機制的效能影響，作者在原始патch的 benchmark 報告中提到接近零負擔（約 0.07% 的差異，在雜訊範圍內）——這也是為什麼它能作為幾乎沒有爭議的「default 開啟」候選,很多發行版的 kernel config 都開著它。

## 核心機制二：`CONFIG_SLAB_FREELIST_RANDOM`——配置順序隨機化

### 它解決什麼問題

即使 freepointer 的值被混淆，如果攻擊者能**可靠預測**「連續配置 N 次會依序拿到 slab 裡哪幾個 object」，仍然能規劃出精確的 heap grooming 策略（例如確保某個被釋放的 victim object，下一次配置一定會被某個特定大小、特定內容的噴射物件填回去）。

### 它怎麼運作

這個機制作用在**一塊全新 slab 剛被切出來、建立 freelist 的那一刻**：預設（未開啟這個選項）的行為是把一塊 slab 的 object 依照記憶體裡的物理順序（0 號、1 號、2 號...）串成 freelist；開啟 `CONFIG_SLAB_FREELIST_RANDOM` 之後，這個初始串接順序會被打亂——用一個基於該 cache 的隨機種子算出的排列,讓「這塊全新 slab 裡第一個被配置出去的是哪個 object」變得不可預測。

> **降級措辭**：這個隨機化只影響**一塊全新 slab 初始建立時的排列順序**，不影響物件被釋放後重新進入 freelist 的位置（那部分仍然遵循 Ch 8 講的 LIFO：被釋放的物件會被推到 freelist 最前端，這一點不受這個 config 影響）。換句話說，它讓「這個 cache 歷史上從來沒配置過的全新一批 object，第一次被拿出來的順序」變得隨機，但不改變「剛釋放的 UAF victim，下一次配置大概率拿到它」這個核心假設——這也是為什麼 UAF 類攻擊即使在開了這個選項的系統上，仍然廣泛可行。

## 核心機制三：`SLUB_DEBUG`、redzone 與 poison——早期偵測

這組機制和前兩個性質不同——它們不是為了讓攻擊更難，是為了讓 bug（即使不是被攻擊者利用，單純是程式邏輯錯誤）**更早被發現**，所以主要用在開發/測試/fuzzing 環境（Syzkaller 跑的 kernel 幾乎都開著），生產環境通常關閉（有效能成本）。

- **Redzone**：在每個 object 前後額外加一小塊「禁區」記憶體，裡面填入一個固定的 magic byte。任何寫超出 object 邊界、碰到 redzone 的操作，會在這塊記憶體被檢查時（例如下一次配置/釋放這塊 slab 時）被偵測出「redzone 被破壞」,印出警告甚至 panic。這對 heap overflow 類的 bug 是直接的早期預警。
- **Poison**：object 被釋放時，整塊內容填入一個固定的 poison pattern（而不是留著原始資料,或只疊上 freepointer)。之後如果有程式碼意外存取到一個「本應已釋放」的物件（UAF 的一種表現），讀到的會是 poison pattern 而不是殘留資料或新物件的內容，方便識別問題，也讓這塊記憶體被重新配置時,SLUB 能檢查「這塊記憶體在被釋放之後,到現在重新配置之前,有沒有被意外寫過」(對照 poison pattern 有沒有被破壞)。

對攻擊者而言，`SLUB_DEBUG` 開啟時的環境，很多原本「悄悄成功」的 corruption 會直接讓系統在你的漏洞觸發之後不久 panic，這其實是一種**雙面刃**：在 fuzzing／漏洞挖掘階段，這是好事（更容易重現與定位 bug）；但如果你的目標是 kernelCTF 的「穩定利用」情境，這些機制在真實目標通常是關閉的，不應該假設它存在。

## 核心機制四：記憶體記帳與 `kmalloc-cg-N`——GFP_KERNEL_ACCOUNT 怎麼分流

### 它解決什麼問題

這不是為了防堵「覆寫 freepointer」這類經典攻擊，而是為了**memory cgroup（memcg）記帳的正確性與隔離性**——如果一個 container（透過 cgroup 限制記憶體）裡的程序觸發的 kernel 配置，和另一個 container 的配置混在同一個 cache、同一塊 slab 裡，記帳與回收邏輯會變得複雜甚至不準確。

### 它怎麼運作

**依查證**：2021 年的一輪 patch（標題類似「mm/memcg/slab: Create a new set of kmalloc-cg-<n> caches」）引入了一組獨立的 `kmalloc-cg-N` cache，專門服務帶有 `__GFP_ACCOUNT`（也就是 `GFP_KERNEL_ACCOUNT`）flag 的配置請求。效果是：**同樣是 64 bytes 的配置，一般 `GFP_KERNEL` 落在 `kmalloc-64`，帶 `GFP_KERNEL_ACCOUNT` 的配置落在 `kmalloc-cg-64`**——兩者在 cache 層級完全分開，不共享同一批 slab。

cache 合併（下面會細講）的規則裡,`SLAB_ACCOUNT` 這個 flag 是比對條件之一：**一個標記了 `SLAB_ACCOUNT` 的 cache，不會和沒有這個標記的 cache 合併**,即使兩者物件大小剛好相同。

### 對攻擊者的實際影響

這個分流對 heap spray 策略是直接的實務問題：**如果你的目標物件（victim）是透過 `GFP_KERNEL_ACCOUNT` 配置的（很多和 cgroup/container 相關、或某些現代 kernel subsystem 刻意加了記帳的結構），你用一般的 `kmalloc()` 噴出去的 spray 物件完全碰不到它**——它們根本不在同一個 cache 裡。反過來，如果你能控制一個會被記帳的 user-space 操作（例如透過某個系統呼叫，觸發一個帶 `GFP_KERNEL_ACCOUNT` 的 kernel 內部配置），你可能需要刻意選擇走這條路徑的噴射物件，才能命中同樣被記帳的目標 cache。判斷一個目標物件究竟走哪條路徑，需要回頭讀對應的 kernel 原始碼，確認它呼叫 `kmalloc()` 時用的 GFP flag。

## 核心機制五：cache merge——「能用的物件」比你以為的少

### 它解決什麼問題

Ch 8 提過，很多 subsystem 各自呼叫 `kmem_cache_create()` 建立自己的 dedicated cache，如果每個都獨立存在,會有大量「名字不同、大小相同、但從來沒機會互相影響」的 cache，浪費 slab 管理開銷。SLUB 會在特定條件下，把多個這類 cache **合併**成同一個底層 cache——合併後，它們共用同一批 slab，彼此的物件可能實體上相鄰。

### 合併的判斷條件

合併發生的前提大致是：兩個 cache 的 **object size 相同（或在對齊規則下等價）、對齊要求相同、flag 相容**（例如前面提到的 `SLAB_ACCOUNT` 必須一致，是否有 `ctor`、是否有特殊的 `SLAB_TYPESAFE_BY_RCU` 等 flag 也是比對項目）。符合條件的 cache，實際上會共用底層的 `kmem_cache` 結構（`/proc/slabinfo` 裡你可能會看到某個 cache 名稱後面跟著別的 cache 名字當作別名）。

這個機制對攻擊者是**雙面刃**：

- **好處**：如果你的目標物件所在的 dedicated cache，剛好和一個你能透過一般使用者操作大量噴射的物件（例如某個常見的 socket buffer 結構）合併了，你就能用這個常見物件去填滿/污染這個 cache，製造 heap grooming 的機會，即使你本來完全碰不到那個 dedicated cache 的建立介面。
- **壞處**：合併規則不是攻擊者能控制的，某次 kernel 版本更新改變了哪些 cache 會合併，你照抄舊 writeup 的 spray 物件選擇，可能在新版本上完全失效（物件被分流到不同的底層 cache 了）。

### `slab_nomerge`：關掉合併的開機參數

如果在 kernel cmdline 加上 `slab_nomerge`，所有 cache 合併行為會被關閉——每個 `kmem_cache_create()` 呼叫都保有獨立的底層 cache。**這對 kernel 開發/debug 很有用（讓 `/proc/slabinfo` 的每一行真正對應一個獨立的子系統，方便除錯），但也直接影響攻擊面**：如果一個 CTF 題目的 boot 參數裡藏著 `slab_nomerge`，照搬「利用 cache 合併去污染某個 dedicated cache」的招式會完全失效，因為合併根本沒發生。**檢查目標環境的 boot cmdline（`/proc/cmdline`）裡是否有這個參數，是分析任何現代 kernel heap exploit 之前的必要步驟。**

## 核心機制六：`CONFIG_HARDENED_USERCOPY`——擋住跨物件邊界的 copy_to/from_user

### 它解決什麼問題

Part 0（Ch 4）提過 `copy_from_user()` / `copy_to_user()` 是 user/kernel boundary 的核心介面——kernel 程式碼要讀寫 user-space 傳進來的 buffer，幾乎都走這兩個函式。一類常見的 bug 是：kernel 程式碼算錯了某個 buffer 的長度（整數溢位、off-by-one、邏輯錯誤），導致 `copy_to_user()` 把**超出預期物件邊界的記憶體內容**複製給 user-space——如果這個物件剛好是從某個 kmalloc cache 配置出來的，超出邊界的部分可能是**同一塊 slab 裡相鄰的另一個物件的內容**，直接造成 kernel 記憶體內容洩漏（info leak）。反過來，`copy_from_user()` 如果長度算錯，也可能寫壞相鄰物件,變成一種間接的 heap overflow。

### 它怎麼運作

`CONFIG_HARDENED_USERCOPY` 開啟後，`copy_to_user()` / `copy_from_user()` 在實際搬動資料之前，會先呼叫一個檢查函式（概念上類似 `check_heap_object()`），確認「這次要複製的範圍（起始位址 + 長度）」**完全落在單一個合法物件的邊界之內**，不會跨到物件外。如果檢查發現範圍跨界（例如起始位址是某個 kmalloc-64 物件的開頭，但長度卻要求複製 128 bytes，明顯超出這個物件該有的大小），kernel 會直接觸發 `BUG()`（通常是 kernel panic 或至少是一個無法忽略的錯誤），而不是悄悄把鄰居物件的內容複製出去。

這個機制依賴 SLUB 知道「這塊記憶體屬於哪個 object、這個 object 的合法大小是多少」——也就是說，它能生效的前提,是這塊記憶體**確實是某個 kmalloc cache 管理的 slab object**，而不是任意一塊記憶體。對 `vmalloc()` 出來的記憶體,或某些特殊映射，檢查邏輯會有不同的處理路徑（部分版本上對這類記憶體的檢查較寬鬆或直接跳過，細節請以你分析的版本原始碼為準）。

### 對攻擊者的實際影響

這個機制主要防堵的是**「長度算錯」類型的 info leak**，和本章前面幾個機制防的「freepointer 偽造」是完全不同的攻擊面。如果你分析的 CVE 本質是「某個 syscall 的長度參數驗證不足，導致 `copy_to_user()` 洩漏超過預期的記憶體」，`CONFIG_HARDENED_USERCOPY` 開啟時，這類 bug 很可能在觸發的那一刻就直接讓系統 panic，而不是讓你拿到 leak——這對「把一個已知 CVE 從舊 kernel 搬到新 kernel」的移植工作（Ch 50）是需要特別檢查的一項。

## 範例一：觀察 cache 合併的實際效果

```bash
# 在你自己的 QEMU 裡（沒有 slab_nomerge）
$ sudo cat /proc/slabinfo | grep -E "kmalloc-(64|cg-64)"
kmalloc-64           18432  18432     64   64    1 : tunables ...
kmalloc-cg-64         1024   1024     64   64    1 : tunables ...
# 兩行獨立存在，證明 GFP_KERNEL 與 GFP_KERNEL_ACCOUNT 的 64-byte
# 配置確實落在不同 cache，彼此不會互相填補

$ sudo cat /sys/kernel/slab/kmalloc-64/aliases
# 如果這個檔案內容非 0，代表有其他 cache 以別名方式合併進了 kmalloc-64
```

> **未實測，理論預期行為**：`/sys/kernel/slab/<cache>/aliases` 這個 sysfs 節點的存在與確切語意，依查證取自 SLUB sysfs 介面的一般設計（`CONFIG_SLUB_DEBUG` 或 `CONFIG_SYSFS` 相關），實際節點名稱、是否存在，請以你自己機器 `ls /sys/kernel/slab/kmalloc-64/` 列出的內容為準,不同版本的 sysfs 曝露欄位有差異。驗證方式：對照同一台機器加上 `slab_nomerge` 開機參數重開一次,比較 `/proc/slabinfo` 裡 cache 的總數量是否明顯變多（因為不再合併，每個 dedicated cache 各自列一行）。

## 範例二：概念示意——如何反推被混淆的 freepointer

這個範例純粹是數學示意（不是完整 exploit），用來讓「XOR 混淆可以被 leak 破解」這句話變得具體。假設你透過某個 info leak，讀到了 kmalloc-64 這個 cache 裡某個空閒物件存放 freepointer 的那 8 bytes，看到的密文是：

```
freeptr_addr  = 0xffff888012340000   （這個密文存放的位址，你知道，因為是你 leak 出來的物件本身的位址）
密文（記憶體裡實際的內容） = 0x9af3d08e21c7005b
```

根據本章「核心機制一」講的公式：

```
密文 = 真實下一物件位址 ^ s->random ^ freeptr_addr
```

如果你另外還有辦法（例如透過一次 heap 佈局已知的配置順序,或透過其他 leak）確認「這個密文對應的真實下一物件位址」應該是什麼（例如你剛好知道這個 slab 目前只有唯一一個空閒物件,freelist 應該指向 NULL，也就是 `0x0`），你就能反推：

```
s->random = 密文 ^ 真實位址 ^ freeptr_addr
          = 0x9af3d08e21c7005b ^ 0x0000000000000000 ^ 0xffff888012340000
          = 0x650c720e33f3005b
```

一旦你算出 `s->random`，**同一個 cache 裡任何其他物件的 freepointer，你都能用同一個值正確加密/解密**——這就是為什麼「混淆」不是「不可逆」，它只是把「直接偽造」這一步，換成「先用一次 leak 解出種子，再用這個種子構造正確密文」這兩步。多了一步，但沒有消滅這個攻擊面。

> **未實測，理論預期行為**：上面的數字是示意用的虛構值,用來說明 XOR 反推的邏輯，不是真實 leak 結果。驗證這個邏輯本身是否符合你的 kernel 版本實作，最直接的方式是在你自己 QEMU 裡寫一個完全信任的（你自己寫的、沒有漏洞的）觀測 module，故意呼叫 kernel 內部未公開匯出的 `freelist_ptr()` 等價邏輯（或直接讀 `kmem_cache` 結構裡的 `random` 欄位，如果你的 module 能存取到 kernel 符號)，印出一個已知物件的密文與明文，手動驗證 XOR 關係是否如本章描述。這類「自己對照原始碼手動驗證」的練習，比空讀文章更能建立對這個機制的信心。

## 對比與取捨：各機制的成本與防禦範圍

| 機制 | 防禦的攻擊面 | 代價 | 預設是否常見開啟 |
|---|---|---|---|
| `SLAB_FREELIST_HARDENED` | 直接偽造 freepointer 值 | 接近零（~0.07%，依原始 benchmark） | 多數主流發行版預設開啟 |
| `SLAB_FREELIST_RANDOM` | 預測「全新 slab 初始配置順序」 | 低（只影響 slab 建立時刻的一次性排列） | 常見，但不保證所有目標環境都開 |
| `SLUB_DEBUG`（redzone/poison） | 早期發現 overflow / UAF（偵測，非阻止） | 中～高（額外記憶體與檢查開銷） | 開發/fuzzing 環境常開，生產環境通常關 |
| `GFP_KERNEL_ACCOUNT` 分流（`kmalloc-cg-N`） | 跨 cgroup 的 cache 污染 | 低（多開一組 cache） | 依 `CONFIG_MEMCG_KMEM` 是否啟用 |
| cache merge 限制（`SLAB_ACCOUNT` 等 flag） | 防止被記帳與未被記帳的物件混在一起 | 無額外成本，純邏輯判斷 | 預設行為的一部分 |
| `slab_nomerge`（開機參數） | 關閉合併帶來的跨 cache 污染機會 | 增加 slab 管理開銷（更多獨立 cache） | 預設不開，需要手動加 |

## 踩雷集錦

1. **錯誤直覺：「開了 FREELIST_HARDENED，UAF 就打不穿了」**。這個機制只保護 freepointer 這個「特定欄位」的值,不保護物件裡其他任何資料。**Type confusion（把一個物件釋放後,被另一個完全不同型別的物件重新解讀）、資料層面的 UAF（讀/寫物件的其他欄位，不碰 freepointer）完全不受這個機制影響**——這正是 Ch 29 data-only attack 整個章節存在的理由:攻擊重心早就從「偽造 freepointer」轉移到「偽造資料欄位」。

2. **錯誤直覺：「cache merge 是攻擊者可以隨意利用的漏洞」**。合併規則是 kernel 設計的正常行為，不是 bug，而且規則是公開、可查的（取決於 object size、flag 組合）。它對攻擊者的價值在於**資訊**——如果你知道某兩個 cache 會合併，你就能規劃用哪個能大量噴射的物件去影響哪個敏感的 dedicated cache，但這需要你先讀懂目標 kernel 版本實際的合併條件，不是憑空假設。

3. **邊界情況：「這些機制全部開啟 = 無法利用」**。本章反覆強調的分寸：絕大多數這裡講的機制是**提高成本，不是消滅漏洞類別**。真正讓純 freepointer 覆寫式攻擊變得罕見的，是 FREELIST_HARDENED + 現代 kernel 整體往 data-only 方向的攻防演進(Ch 26-29)，而不是單一機制的「完全阻擋」。分析一個真實 CVE 能不能打,永遠要具體看它給你的原語是什麼,而不是看目標開了哪些 config 就直接下結論。

4. **版本混淆：「這些 config 名字和行為，在所有 kernel 版本上都一樣」**。本章開頭已經強調過，但值得再次點名：`kmalloc-cg-N` 的存在依賴 `CONFIG_MEMCG_KMEM` 以及對應版本的 patch 是否已合併；FREELIST_HARDENED 的混淆演算法本身（XOR 的具體操作數)歷史上有過微調。遇到任何「這個機制具體怎麼算」的疑問，先確認你分析的目標 kernel 版本，再去對應版本的原始碼查證，不要套用你記得的某一個版本的細節。

## 進階：再往深一層——`CONFIG_SLAB_VIRTUAL`

Ch 26（Part 4）會細講，這裡先點名方向：`CONFIG_SLAB_VIRTUAL`（以及相關的提案方向）試圖從更根本的層面改變 SLUB 的記憶體佈局——目標是讓「拿到一個 physical frame 的任意讀寫原語」不再自動等於「拿到一個可預測、可控的 kernel virtual address 上的 slab 物件」，削弱 Ch 6 講的 direct map 橋樑在 heap exploitation 情境下的價值。這類提案目前（本書寫作時）仍在持續演進中，**具體實作細節與是否已經進入主線 kernel，請在你實際遇到時查證當下的狀態**，本書不在 Part 1 對它的成熟度下斷言，只在這裡預告它存在,是本書寫作時 SLUB hardening 演進的前沿方向之一。另一個值得追蹤的方向是 **random kmalloc caches**（`CONFIG_RANDOM_KMALLOC_CACHES`）——依查證，這組機制讓同一個 size class 實際對應多份（patch 討論中出現過 15 份 + 1 份一般路徑的設計）彼此獨立的底層 cache，依呼叫者程式碼位址的 hash 值分配到其中一份，目標是讓「噴射物件和目標物件穩定落在同一個 cache」這個 heap grooming 的基本假設變得不再可靠。這個機制合併的確切版本本書不下斷言（討論時間點集中在 2023 年中後段，晚於本書 6.6 LTS 基準），Ch 27 會在它已經是主流配置的前提下，完整拆解繞過策略。

## 進階：把本章機制串成一張判斷流程

當你拿到一個新的目標 kernel，想判斷「某個經典 freepointer 覆寫技巧還能不能用」，可以照這個順序排查，每一步都對應本章一個機制：

```
1. 確認 CONFIG_SLAB_FREELIST_HARDENED 是否開啟
      │
      ├─ 沒開 → freepointer 是明文，經典覆寫技巧直接可用
      │
      └─ 開了 → 需要先找一個 leak 解出 s->random，
                 才能構造正確密文（見範例二）
                      │
                      ▼
2. 確認目標物件的配置路徑用的 GFP flag
      │
      ├─ 一般 GFP_KERNEL → 你的 spray 物件只要大小對、
      │                     且目標 cache 沒有因為合併規則被分流，
      │                     理論上可以共用同一個 cache
      │
      └─ 帶 GFP_KERNEL_ACCOUNT → 你必須也用帶記帳的路徑噴射，
                                  否則物件落在 kmalloc-cg-N，
                                  碰不到目標
                      │
                      ▼
3. 確認 boot cmdline 是否有 slab_nomerge
      │
      ├─ 沒有 → 可以考慮用 cache 合併規則，
      │          找一個能大量噴射且會和目標 dedicated cache
      │          合併的物件類型
      │
      └─ 有 → 放棄「借道合併」的策略，
               直接找能觸發目標 cache 建立介面的操作
```

這張流程圖本身就是本章內容的濃縮版，建議在分析任何真實 CVE 之前，先照這個順序走一遍，再決定要不要深入細節。

## 動手練習

在你自己 QEMU 的 kernel config 裡,找出以下幾項的開關狀態（`.config` 檔搜尋對應的 `CONFIG_*` 名稱，或執行期用 `/boot/config-$(uname -r)` 比對）：

1. `CONFIG_SLAB_FREELIST_HARDENED`
2. `CONFIG_SLAB_FREELIST_RANDOM`
3. `CONFIG_SLUB_DEBUG`（注意：這個 config 存在只代表「支援」這個功能，實際是否啟用還要看開機參數 `slub_debug`）
4. `CONFIG_MEMCG_KMEM`

把結果整理成一張表，對照 Ch 16 之後你會打的每一個經典 primitive，想一想:如果某一項是開著的，你原本設想的攻擊步驟需要怎麼調整?

## 本章重點整理

- `SLAB_FREELIST_HARDENED` 用每個 cache 專屬的隨機值 XOR 混淆 freepointer，讓「直接偽造 freepointer」需要先 leak 出這個隨機種子，但不保護物件的其他資料欄位。
- `SLAB_FREELIST_RANDOM` 只打亂「全新 slab 初始建立時」的配置順序，不影響釋放後物件重新進入 freelist 的 LIFO 行為。
- `GFP_KERNEL_ACCOUNT` 的配置被導向獨立的 `kmalloc-cg-N` cache，和一般 `GFP_KERNEL` 配置完全隔離——判斷 spray 物件是否命中目標 cache，必須同時確認大小和 GFP flag。
- cache merge 規則決定「表面上不同名字的 cache，底層是否共用同一批 slab」，`slab_nomerge` 開機參數可以整體關閉這個行為。
- 這些機制整體是**提高攻擊成本**，不是消滅漏洞類別——真正讓現代 kernel heap exploitation 困難的,是這些機制疊加上 CFI/KCFI 等控制流保護後，把攻擊重心逼向 data-only 技巧。

## 自我檢核

- [ ] 不看筆記，能不能解釋 `SLAB_FREELIST_HARDENED` 的 XOR 混淆用了哪兩個操作數，為什麼只知道其中一個不夠？
- [ ] 如果有人問你「开了這些 hardening，SLUB 上的 UAF 是不是就沒用了？」，你會怎麼反駁或修正這個說法？
- [ ] 你能不能舉出一個「兩個 cache 表面上大小相同，但因為某個 flag 不同所以不會合併」的具體例子？
- [ ] 想一個你在 Part 0／Ch 8 寫過的 vulnerable module，如果把它搬到一台開了 `CONFIG_MEMCG_KMEM` 且你的漏洞觸發路徑帶 `GFP_KERNEL_ACCOUNT` 的機器上，你的 spray 策略要做什麼調整？

## 延伸閱讀

### 官方文件 / 原始 patch

- **[\[PATCH v3\] mm: Add SLUB free list pointer obfuscation（lkml 原始討論串）](https://lkml.iu.edu/hypermail/linux/kernel/1707.0/02829.html)**
  - **讀哪裡**：patch 說明本身（commit message），解釋設計動機與和 PaX/grsecurity 原始概念的關係
  - **和本章的關聯**：本章「核心機制一」整節的第一手來源，比任何二手部落格文章更準確地說明這個機制當初為什麼被接受進主線 kernel

- **[Kernel Self Protection Project（KSPP）wiki](https://kernsec.org/wiki/index.php/Kernel_Self_Protection_Project)**
  - **讀哪裡**：SLUB / heap 相關的條目與 roadmap 清單
  - **和本章的關聯**：本章提到的大多數 hardening 機制，最初都是從這個專案的提案清單裡長出來的；追蹤這個 wiki 能看到哪些機制還在提案階段、哪些已經合併

### 部落格 / 技術文章

- **[pawnyable — Linux Kernel：mitigation 相關章節](https://pawnyable.cafe/linux-kernel/)**
  - **這篇說什麼**：示範在真實 CTF 題目上，怎麼先確認目標開了哪些 SLUB hardening config，再調整利用策略
  - **讀哪裡**：mitigation / hardening 相關章節
  - **為什麼值得讀**：操作導向，補足本章理論部分沒有示範的「拿到一個題目怎麼第一步判斷環境」流程

- **[Attacking the Linux Kernel（lrh2000）](https://lrh2000.com/)**
  - **這篇說什麼**：對 random kmalloc caches、cache merge 等現代機制有更深入的原始碼層級追蹤
  - **讀哪裡**：涉及現代 SLUB hardening 與繞過策略的文章
  - **為什麼值得讀**：這本書 Part 4（Ch 26-27）會大量引用這類分析，提前讀過會讓後面章節跟得更快

### 讀完本章之後

- **[Google Project Zero blog 中涉及 SLUB hardening 繞過的文章](https://googleprojectzero.blogspot.com/)** —— 搜尋 "SLUB" 或 "kmalloc" 相關關鍵字，能找到針對這些機制的真實繞過案例分析，是本章理論的最佳實戰驗證

本章把 SLUB 加裝的防禦講完了，下一個 Part 要開始真正進入漏洞本身——先建立一套完整的 kernel 漏洞類型學，再一個個拆解經典利用技巧,而這一切都站在本 Part 建立的 buddy/SLUB/hardening 三層知識上。先完成練習 A，親手驗證 Ch 8 的 freelist 行為,再往下走。

→ [練習 A：trace kmalloc/kfree 的 slab 行為](./practice-a-slab-tracing.md)
