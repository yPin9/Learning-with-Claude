# Ch 40 — race condition 漏洞：TOCTOU、double-fetch、refcount race

> **目標**：把 [Ch 39](./39-concurrency-model.md) 建立的「哪裡有共享狀態、哪種同步手段保證什麼」這張地圖,翻譯成攻擊者視角——學完你要能看著一段 kernel 程式碼,指出它屬於 TOCTOU、double-fetch、還是 refcount race 這三種模式的哪一種,說出它會熬成 [Ch 10](./10-vulnerability-taxonomy.md) 哪一類原語,並能從 CVE-2016-5195(Dirty COW)和 CVE-2022-2588 這兩個真實案例裡,拆出「race window 在哪裡、競爭的是什麼資源、贏了之後拿到什麼原語」。

> **環境**：Linux 6.6 LTS、x86-64。本章兩個 CVE 案例的根因描述已查證(來源見延伸閱讀與正文標注);本章的程式碼片段除非特別標注,**未實測,為理論預期行為**——親手重現 race 需要 [Ch 41](./41-widening-race-window.md) 教的技巧才容易穩定觸發,這裡先建立辨識與分類的能力。

## 為什麼需要這一章

[Ch 39](./39-concurrency-model.md) 結尾那個「check + use」的最小示意已經把本章的主題帶出來了:**檢查(check)和使用(use)之間,只要沒有同步手段包住,就是一個天生的 race candidate**。但「看得出哪裡有共享狀態」和「看得出這段程式碼是不是真的有可利用的 race」是兩層不同的能力——前者是 [Ch 39](./39-concurrency-model.md) 教的,後者需要一套**分類詞彙**,讓你一看到某種程式碼形狀,就能說出「這是哪種 race、能换到什麼」。

這套詞彙也解釋了為什麼 race condition 在 [Ch 10](./10-vulnerability-taxonomy.md) 的分類裡被歸進 logic bug,而不是 memory corruption:**race 本身不直接寫壞記憶體**,它利用的是「時序假設被打破」。但這不代表它比較弱——race 經常是**通往** memory corruption(UAF、double-free)的入口,本章要講清楚這條「邏輯錯誤 → 記憶體錯誤」的轉換路徑具體怎麼發生。

## 先建立直覺

```
  正常的單執行緒直覺：
  check(x) 為真  ──▶  use(x) 時 x 仍然是 check 時看到的那個狀態
  （因為「中間」沒有別人會動 x）

  race 的世界：
  Thread A: check(x) 為真 ──────[中間有個窗口]────── use(x)
  Thread B:              change(x) ←── 剛好插在這個窗口裡

  Thread A 的 use(x) 實際操作的,
  已經不是它 check 時看到的那個 x 了——
  但 Thread A 的程式碼完全不知道,它還在用舊的假設往下跑。
```

這張圖是本章三種模式的共同骨架。差別只在於「check 和 use 分別是什麼動作」「x 是什麼」「競爭的對手是誰」。把這三個維度填清楚,就能把任何一段可疑程式碼歸進某一類。

## TOCTOU：check 和 use 隔著一段距離

TOCTOU(Time-Of-Check to Time-Of-Use,檢查時刻到使用時刻)是最通用的名字,[Ch 39](./39-concurrency-model.md) 結尾的例子就是它:

```c
/* TOCTOU 的標準形狀 */
if (dev->buf != NULL) {        /* check:此刻為真 */
        /* ---- window ---- */
        kfree(dev->buf);        /* use:假設 check 時的狀態還成立 */
        dev->buf = NULL;
}
```

TOCTOU 的 window 可以藏在任何「先查詢一個狀態、隔了幾行程式碼(甚至隔了一次 syscall 返回)才真正動用這個狀態」的地方。常見的三種子類:

1. **檢查權限,之後才真正操作**:檢查「這個 fd 現在屬於我」,幾行之後才真正對這個 fd 做特權操作——中間如果 fd 被 `close()`/`dup2()` 換成別的東西,操作到的就不是你檢查時以為的那個物件。
2. **檢查指標非空,之後才解參照或釋放**:[Ch 39](./39-concurrency-model.md) 的例子,window 裡發生第二次 `free`,就是 double-free 的雛形。
3. **檢查路徑/檔案屬性,之後才真正開啟**:使用者空間的經典案例(`access()` 檢查之後才 `open()`,中間檔案被換成 symlink)在 kernel 裡的對應版本是「檢查某個 kernel 物件的某個屬性,之後才真正依賴這個屬性做特權判斷」。

TOCTOU 的 window 長度,決定了它在真實系統上自然發生的機率——[Ch 39](./39-concurrency-model.md) 踩雷集錦第 4 條已經強調過:**沒有復現不代表沒有漏洞**,window 窄到幾個指令的 TOCTOU,在正常負載下可能幾百萬次呼叫才巧合觸發一次,但這不改變它是一個真實的邏輯漏洞——[Ch 41](./41-widening-race-window.md) 教的技巧,就是把這種「自然機率極低」的 window 人工拉長到「幾乎每次都能命中」。

## Double-fetch：同一個 user 指標被讀了兩次

Double-fetch 是 TOCTOU 的一個特化版本,但值得單獨拿出來講,因為它是 kernel 特有、而且高度系統性的一類模式:**kernel 對同一塊 user-space 記憶體,透過 `copy_from_user`(或任何 user pointer 解參照)讀了兩次,兩次之間假設讀到的值不變,但 user-space 可以在兩次讀取之間修改這塊記憶體**。

```c
/* double-fetch 的標準形狀(概念示意,未實測) */
struct my_ioctl_arg hdr;
copy_from_user(&hdr, uarg, sizeof(hdr));      /* 第一次讀：拿長度 */

if (hdr.len > MAX_LEN)
        return -EINVAL;                        /* 用第一次讀到的 len 做檢查 */

char *kbuf = kmalloc(hdr.len, GFP_KERNEL);     /* 用第一次讀到的 len 配置緩衝區 */

/* ---- window：user-space 這裡可以改掉 uarg->len ---- */

copy_from_user(kbuf, uarg->data, hdr.len);     /* 如果這裡「重新」從 uarg 讀 len... */
```

注意上面這段其實已經刻意寫對了一半——第二次 `copy_from_user` 用的是**第一次讀到的區域變數 `hdr.len`**,不是重新去讀 `uarg->len`,所以這個版本本身沒有 double-fetch。真正的 double-fetch 長這樣:

```c
/* 真正的 double-fetch:第二次操作時,又從 user 指標重新讀了一次 */
struct my_ioctl_arg *uarg = (struct my_ioctl_arg *)arg;
unsigned int len;

copy_from_user(&len, &uarg->len, sizeof(len));  /* 第一次讀 len：做檢查用 */
if (len > MAX_LEN)
        return -EINVAL;

char *kbuf = kmalloc(len, GFP_KERNEL);          /* 用第一次的 len 配置 */

/* ---- window ---- */

copy_from_user(&len, &uarg->len, sizeof(len));  /* 危險：第二次又讀了一次同一個欄位! */
copy_from_user(kbuf, uarg->data, len);           /* 用的是第二次讀到的 len，
                                                     可能比第一次大 → heap overflow */
```

這個模式在真實 kernel 程式碼裡通常不會寫得這麼直白(沒有人會蠢到故意讀兩次同一個變數),它真正出現的情境是**兩次讀取發生在不同的函式呼叫、甚至不同的子系統邊界**:上層函式讀一次做了檢查,把資料傳給下層函式,下層函式出於某種「保險」或程式碼重構的歷史原因,**又**從原始的 user 指標重新讀了一次同一塊記憶體——兩次讀取之間,如果有另一個 thread 透過 mmap 共享記憶體不斷改寫這塊 user buffer,就能讓「檢查時讀到的值」和「真正使用時讀到的值」不同。

> 這類 bug 的學術性研究相對完整。Wang 與 Krinke 在 *Pairing Program with Potentially Vulnerable Fates*(見延伸閱讀)用模式比對系統性地在 Linux 4.5 掃出 double-fetch 候選,人工驗證後確認其中多個真實可利用,歸納出「type selection(用第一次讀到的值選型別,第二次讀取時型別判斷已經過時)」「size checking(本節示範的模式)」「shallow copy(淺層複製後,內部指標仍指向使用者可改的記憶體)」三種典型場景。這份研究沒有針對某個特定 CVE 編號,我們也沒有找到一個廣為人知、帶明確 CVE 編號的教科書級 double-fetch 案例可以在這裡放心引用根因細節,因此本節以這份學術研究的分類作為權威來源,而不是編造一個 CVE 細節。

## Refcount race：併發 get/put 失衡

第三種模式的共享狀態不是「一個指標」或「一段 buffer」,是**引用計數(reference count)本身**。[Ch 39](./39-concurrency-model.md) 的鎖家族一節已經示範過正確版本——用 `spin_lock` 包住 `refcount++`/`refcount--`。Refcount race 就是這層保護缺失或不完整時發生的事。

```
  正常情況(refcount 正確同步)：
  Thread A: lock; refcount++ (0→1); unlock          （A 拿到一份引用）
  Thread B: lock; refcount++ (1→2); unlock          （B 也拿到一份）
  Thread A: lock; refcount-- (2→1); unlock          （A 用完放手）
  Thread B: lock; refcount-- (1→0); unlock          （B 用完放手，归零才真正釋放）

  Refcount race（檢查與遞減之間有 window，或遞減本身沒上鎖）：
  Thread A: 讀到 refcount == 1（以為只有自己在用）
  Thread B: refcount-- (1→0) ──▶ 歸零，物件被釋放！
  Thread A: 繼續使用它以為還活著的物件 ──▶ UAF
```

Refcount race 最終幾乎總是熬成 **UAF**(use-after-free,[Ch 18](./18-uaf-double-free.md) 已經教過利用手法本身)——這正是 [Ch 10](./10-vulnerability-taxonomy.md) 說的「logic bug 熬成 memory corruption」的典型轉換路徑:refcount 本身只是一個整數,race 發生的那一刻並沒有任何記憶體被寫壞,但它導致的「提早釋放」讓後續對同一塊記憶體的任何操作都變成 UAF 的利用場景——從這一步開始,你可以直接套用 [Ch 18–21](./18-uaf-double-free.md) 整套 heap 利用方法論。

常見的失衡成因有兩種:

1. **「get 一次,put 兩次」**:某條錯誤處理路徑忘了它已經 get 過,又多 put 了一次,讓 refcount 比實際引用數少 1——這不一定需要併發,單執行緒的邏輯錯誤也能造成,但併發會放大它發生的時機與頻率。
2. **真正的併發 race**:兩個 thread 同時對同一個物件做 get/put,其中一條路徑的「讀 refcount → 判斷 → 寫 refcount」這三步沒有被鎖完整包住,中間被另一個 thread 插隊,導致「遺失更新」(lost update,[Ch 39](./39-concurrency-model.md) 例子一提過的同一種模式,只是這裡的後果是提早釋放,不只是計數算錯)。

## 案例一:CVE-2016-5195(Dirty COW)——COW 機制裡的 race

Dirty COW 是最適合拿來理解「race 如何變成記憶體寫壞」的經典案例,因為它的根因拆開來看,正是一個 TOCTOU。

**背景(write-time COW,copy-on-write)**:一個只讀映射的 page(例如用 `MAP_PRIVATE` 映射一個你只有讀權限的檔案),如果某個路徑需要寫入它,kernel 不會直接寫原始 page,而是先配一份私有副本,把寫入導到副本上(這就是 COW 的字面意義)。

**根因(查證:CVE-2016-5195 官方描述與修復 commit 19be0eaf,作者 Linus Torvalds,2016 年 10 月)**:`mm/gup.c` 裡 `get_user_pages()` 系列函式,在處理「對一個只讀映射做寫入嘗試、觸發 COW」這條路徑時,存在一個 race window:

```
  Thread A（例如透過 /proc/self/mem 寫入一個只讀映射的 page）：
    1. 嘗試 FOLL_WRITE（帶寫入意圖）呼叫 get_user_pages()
    2. 發現這是只讀映射，觸發 COW，準備配一份私有副本
    3. ---- window：COW 還沒完全生效前，返回路徑在某個條件下
                     把 FOLL_WRITE 這個意圖「降級」成一次讀取 ----
    4. 之後的程式碼因為第 3 步的降級，誤以為這是一次單純讀取，
       沒有走 COW 該走的那條正確路徑

  Thread B（同時對同一塊映射呼叫 madvise(MADV_DONTNEED)）：
    把這塊映射標記成「可以丟棄，之後重新 fault 回來」，
    干擾 Thread A 在第 3 步的狀態判斷，讓 race window 變得可以被穩定命中。

  結果：寫入操作最終落在了原始的 page（而不是 COW 該產生的私有副本）上，
        一個只有讀權限的映射，被寫入了——
        攻擊者藉此寫入本該只讀的檔案（例如 setuid 的系統二進位檔）。
```

**修復的方式**(查證:同一個 commit)印證了這是一個典型的 TOCTOU:修復不是「加鎖」,是**消除可被利用的中間狀態降級**——引入一個內部的 `FOLL_COW` flag,明確記錄「這次存取已經走過 COW 判斷」,並透過檢查 PTE 的 dirty bit 來確認這個標記在整個過程中沒有被繞過,讓「這次存取的寫入意圖」不會在中途被意外丟掉。這是本章給你的第一個教訓:**race 的修復不一定是加鎖,也可能是消除那個「check 之後,状态被悄悄改變」的中間縫隙本身**。

> 這一節的機制描述基於已查證的公開來源(CVE record、修復 commit 的 commit message、Dirty COW 官方 wiki 的 vulnerability details 頁)。涉及 exploit 細節的部分(例如使用 `/proc/self/mem` 配合 `ptrace(PTRACE_POKEDATA)` 的完整利用鏈)我們沒有在本環境重現,標注為需要你自行查閱原始公開 PoC 驗證。

## 案例二:CVE-2022-2588(route4_change)——refcount/list 不一致引發的 double-free

這個案例發生在 `net/sched/cls_route.c` 的 `route4_change()`,是 netfilter/tc(traffic control)filter 子系統的一個 bug,也是 Pwn2Own 曾經被用來做權限提升示範的題目之一。

**背景**:`cls_route` 是 Linux traffic control 的一種封包分類 filter,每個 filter 有一個 handle(識別碼)。當你用同一個 handle 建立新 filter 去「替換」舊的,kernel 要負責把舊 filter 從它所在的 hash table/linked list 上移除,再釋放它。

**根因(查證:多份公開分析一致指出的機制,含 CVE 官方描述與 Zero Day Initiative 的 advisory ZDI-22-1117)**:當舊的 filter 的 handle 剛好是 **0** 時,`route4_change()` 的移除與釋放邏輯出現不一致——舊 filter 被 `kfree()` 釋放了,但**沒有**被正確地從它所在的連結串列上移除。

```
  正常情況：
    替換 handle=H 的 filter
      → 從 list 上把舊 filter 的節點移除
      → kfree(舊 filter)
      → list 上不再有任何指向這塊記憶體的節點

  CVE-2022-2588 的情況（舊 filter 的 handle == 0）：
    替換 handle=0 的 filter
      → kfree(舊 filter) 被呼叫了
      → 但移除 list 節點那一步的條件判斷,
        在 handle == 0 這個特殊值下沒有被正確執行
      → list 上仍然留著一個指向「已經被釋放記憶體」的節點

  後續：這個殘留的節點之後可能再被走到一次（例如下一次查詢/清理這條 list 時）,
        對它再做一次 kfree ──▶ double free（Ch 18 的雙重釋放）；
        或在被釋放後、記憶體被重新配置給別的用途之前，
        這個殘留指標的存在本身就是一個典型的 UAF 入口。
```

**和本章分類的對應**:這不是傳統的「兩個 thread 同時執行」的併發 race,它更接近「單一操作路徑裡,兩個本該同步一致的資料結構(連結串列的狀態 vs 記憶體是否已釋放)之間出現了邏輯不一致」——但它依然歸入 race 家族討論,原因是:它和 refcount race 共享同一個根本模式:**「這塊記憶體還有誰在引用它」這件事的追蹤機制(這裡是 list 成員資格,其他案例可能是 refcount 數字)出現了與實際狀況不符的情況,導致 double-free/UAF**。公開分析(見延伸閱讀)也指出它與更早的 CVE-2021-3715(同樣是 `route4_filter` 的連結串列操作問題)高度相似,顯示這類「資料結構一致性」bug 在這個子系統裡不是單一個案。

**修復**(查證:修復於 Linux v5.19,上游 commit 9ad36309e2719a884f946678e0296be10f):讓 handle 為 0 的情況走到與其他 handle 值一致的移除邏輯,消除這個特殊值造成的分支不一致。

> 本案例的機制描述整合自公開的二手分析來源(含中文圈的技術部落格與多個公開 PoC 專案的 README),我們沒有找到官方 commit message 對這個 bug 的逐行說明作為一手來源進行逐字核對,relevant 細節(例如「list 上殘留節點之後具體在哪個路徑被再次走到」)請以你實際閱讀 `net/sched/cls_route.c` 的 v5.19 之前/之後 diff 為最終依據。

## 從 race 到原語:為什麼這條路值得走

把本章三個模式和兩個案例放回 [Ch 10](./10-vulnerability-taxonomy.md) 的攻擊者地圖,會看到一條清楚的升級鏈:

```
   race (TOCTOU / double-fetch / refcount)
        │  （邏輯層面：假設被打破,不是記憶體被直接寫壞)
        ▼
   UAF / double-free / heap overflow
        │  （記憶體層面：Ch 17-18 的原語)
        ▼
   spray / grooming (Ch 19-20)
        │
        ▼
   leak / 控流 / data-only (Ch 21-29)
        │
        ▼
   privesc
```

這條鏈解釋了為什麼本書把 race 放在這個位置,而不是放在 Part 2(漏洞類型學)就一次講完:**race 本身的機制(Ch 39 的並發知識)和它熬成的下游原語(Part 3 的 heap 利用)是兩套完全不同的知識,學 race 卻不懂 heap 利用,你會卡在「贏了 race」之後不知道下一步要做什麼。** 這也是為什麼真實世界的 race-based exploit,寫起來的篇幅大半不是在講「怎麼贏這場賭局」,是在講「贏了之後怎麼把這個 UAF 打成提權」——後面那部分你已經在 Part 3 學過了,本章和下一章只負責補上「怎麼贏」這一半。

另一個值得注意的地方是:**不是每一種 race 都需要靠運氣贏**。CVE-2022-2588 那種「單一執行路徑內的邏輯分支錯誤」,一旦你知道正確的觸發序列(handle 設成 0、觸發替換),它幾乎是**確定性**觸發,不需要任何計時技巧——這和 CVE-2016-5195、以及本章 TOCTOU/refcount race 一節描述的「兩個 thread 真的要在時間上撞上」是不同等級的挑戰。分析一個候選 race bug 時,先確認它屬於哪一種,能幫你判斷接下來要不要投入 [Ch 41](./41-widening-race-window.md) 的拉窗技巧,還是其實根本不需要。

## 對比與取捨

| 模式 | 競爭的對象 | check 和 use 之間隔著什麼 | 典型後果 | 對應利用章節 |
|---|---|---|---|---|
| TOCTOU | 某個欄位/指標的狀態 | 幾行程式碼,甚至一次 syscall 的返回 | double-free、UAF、權限判斷失效 | [Ch 18](./18-uaf-double-free.md) |
| Double-fetch | user-space 可寫的記憶體內容 | 兩次 `copy_from_user`(或等效)之間 | heap overflow(size 類)、型別混淆(type selection 類) | [Ch 17](./17-heap-overflow.md) |
| Refcount race | 一個整數計數器 | 「讀計數 → 判斷 → 寫計數」這三步之間 | 提早釋放 → UAF | [Ch 18](./18-uaf-double-free.md) |

三種模式最終幾乎都匯流到同一個下游:UAF 或 heap overflow。這也是為什麼本書把 race 放在 Part 6、在 Part 3 的 heap 利用之後——**race 是另一條通往 Part 3 原語的入口,不是獨立於它的另一套利用技術**。

## 踩雷集錦

1. **把「我測試時沒觸發」當成「這裡沒有 race」**——[Ch 39](./39-concurrency-model.md) 已經強調過,這裡再次提醒:TOCTOU 的 window 可能窄到幾個指令,單核心、輕負載測試環境幾千次呼叫都不一定踩中。判斷「有沒有 race」要看程式碼有沒有同步保護,不是看測試有沒有炸。
2. **以為 double-fetch 一定長得像「同一行程式碼讀兩次」**——真實案例通常是**兩個不同函式、甚至跨子系統邊界**各自讀了一次同一塊 user 記憶體,肉眼掃一個函式看不出來,要追蹤「這個 user 指標被傳到哪些地方、每個地方有沒有獨立呼叫 `copy_from_user`」。
3. **把 refcount race 和單純的「忘了 put」搞混**——忘了 put(leak,導致物件永遠不釋放)和 refcount race(導致物件被提早釋放)是相反方向的錯誤,前者是資源洩漏(通常不是安全漏洞,只是效能/資源問題),後者才是導向 UAF 的安全漏洞。分析一個 refcount 相關 bug 時先確認方向。
4. **誤以為 Dirty COW 的修復是「加一個鎖」**——實際修復是消除一個會被悄悄降級的中間狀態(引入 `FOLL_COW` flag 與 dirty bit 檢查),不是單純加鎖。有些 race 的正確修法本來就不是鎖,而是讓邏輯不再有「可以被岔開」的分支。
5. **讀 CVE-2022-2588 類案例時,把「連結串列狀態不一致」和「傳統多執行緒併發 race」混為同一回事,忽略了它的 window 可能只需要單一個操作路徑內的分支錯誤就能製造**——不是所有 race-family 的 bug 都需要兩個 thread 真正同時執行;有些只需要一條執行路徑裡,兩個本該同步的狀態出現邏輯不一致。

## 進階：再往深一層

### 為什麼現代 kernel 掃 race 的工具只能抓到一部分

[Ch 44](./44-debug-toolchain.md) 會細講 KCSAN(Kernel Concurrency Sanitizer),它專門抓「資料競爭」(data race:兩個沒有同步保護的存取同時碰同一塊記憶體,至少一個是寫)。但本章三種模式裡,KCSAN 能穩定抓到的主要是**真正需要兩個 CPU 同時執行、且確實撞上同一塊記憶體**的情況——像 CVE-2022-2588 那種「單一執行路徑內,邏輯分支造成的狀態不一致」,KCSAN 的偵測模型未必能直接標出來,因為它根本不涉及「兩個 CPU 同時存取」這個前提。這解釋了為什麼光靠自動化工具掃不完 race 類 bug,人工讀程式碼、理解業務邏輯裡「這個狀態理論上應該和什麼東西同步」仍然是找這類漏洞不可取代的一步。

### Race 和 TOCTOU 在 user-space 安全領域的更廣脈絡

TOCTOU 不是 kernel 專屬概念——user-space 的 `access()`/`open()` race、Web 應用的檢查與使用分離(例如檢查檔案所有權之後才真正存取)都是同一個模式的不同載體。讀過 user-space 安全的人會發現本章其實是把同一套直覺重新套用到一個新的執行環境(kernel 的 SMP 並發,而不是多個獨立 process)。這也是為什麼 [Ch 39](./39-concurrency-model.md) 要先把 kernel 特有的並發模型(spinlock vs mutex、RCU)講清楚——同一個「check-then-use」的邏輯漏洞,在 kernel 裡的「中間插隊者」可能是另一個 CPU 上真正同時執行的程式碼,比 user-space 的「另一個 process 搶著改檔案」更難預測,也更難靠運氣避開。

## 動手練習

1. 回頭看 [Ch 39](./39-concurrency-model.md) 例子二的「少上鎖」版本,把它正式標注成本章定義的哪一類模式(TOCTOU),並寫下它的 check 是哪一行、use 是哪一行、window 裡「中間插隊者」理論上會做什麼。
2. 寫一個教學用 kernel module,刻意實作一個 double-fetch 的 ioctl 處理函式(模仿本章「size checking」那個模式),用兩個 user-space thread 嘗試觸發(一個不斷呼叫 ioctl,另一個不斷修改共享的 user buffer 裡的 len 欄位),在開 KASAN 的 kernel 上觀察能不能看到任何異常(此練習**未實測**,預期在沒有 [Ch 41](./41-widening-race-window.md) 的拉長 window 技巧輔助下,命中率可能很低,這正是下一章要解決的問題)。
3. 找 Linux `net/sched/cls_route.c` 在 v5.19 前後的 diff(可用 `git log -p` 或在 kernel.org 的 cgit 上查),對照本章案例二的描述,確認修復具體改了哪幾行、新加的條件判斷是什麼。
4. 讀 Dirty COW 修復 commit `19be0eaffa3ac7d8eb6784ad9bdbc7d67ed8e619`(可在 Bootlin Elixir 或任意 kernel git mirror 查),找出 `FOLL_COW` 這個 flag 第一次出現在程式碼的位置,以及它和哪個檢查函式(`can_follow_write_pte` 一類)配合使用。

## 本章重點整理

- 三種 race 模式共享同一個骨架:check 一個狀態為真,use 時這個狀態已經被別人改變,而程式碼不知道。
- TOCTOU 是最通用的名字;double-fetch 是 kernel 特有、因「同一塊 user 記憶體被讀兩次」而特化的子類;refcount race 的對象是計數器本身,後果幾乎總是提早釋放導致 UAF。
- CVE-2016-5195(Dirty COW)是 TOCTOU 的經典案例,修復方式是消除中間狀態降級,不是單純加鎖。
- CVE-2022-2588(route4_change)示範了「資料結構一致性被破壞」也可以歸入 race-family,即使它不需要兩個 CPU 真正同時執行。
- 三種模式最終幾乎都匯流到 [Ch 18](./18-uaf-double-free.md)/[Ch 17](./17-heap-overflow.md) 的 memory corruption 利用方法論——race 是另一條通往那套方法論的入口。
- 自動化工具(KCSAN)對「單一路徑內的邏輯不一致」類案例的覆蓋有限,人工讀程式碼仍不可取代。

## 自我檢核

- [ ] 不看筆記,能用自己的話解釋 TOCTOU、double-fetch、refcount race 三者的共同骨架與各自的差異
- [ ] 能說出 CVE-2016-5195 的修復為什麼不是單純加鎖,而是改成了什麼
- [ ] 能解釋 CVE-2022-2588 為什麼歸入本章的 race 家族討論,即使它的描述裡沒有明確要求「兩個 thread 同時執行」
- [ ] 想一個你讀過的 kernel 原始碼片段,嘗試用本章三種模式之一去套它,看看套不套得上
- [ ] 能說出為什麼「沒有復現」不能作為「這裡沒有 race」的證據

## 延伸閱讀

### CVE / 一手與高品質二手來源

- **[CVE-2016-5195（Dirty COW）NVD 記錄與修復 commit `19be0eaffa3ac7d8eb6784ad9bdbc7d67ed8e619`](https://nvd.nist.gov/vuln/detail/CVE-2016-5195)**
  - **這篇說什麼**：官方漏洞描述與 Linus Torvalds 本人的修復 commit,commit message 詳細說明了為什麼要引入 `FOLL_COW`。
  - **讀哪裡**：NVD 的描述段落,以及 commit message 全文(在任意 kernel git mirror 搜尋該 hash)。
  - **和本章的關聯**：案例一的直接出處;前置知識是本章 TOCTOU 一節。
- **[Dirty COW 官方 wiki — Vulnerability Details](https://github.com/dirtycow/dirtycow.github.io/wiki/VulnerabilityDetails)**
  - **這篇說什麼**：漏洞發現者社群維護的技術細節頁,包含 race window 的更詳細時序描述與已知的 in-the-wild 利用手法(`/proc/self/mem` + `ptrace`)。
  - **讀哪裡**：整頁,特別是對 `madvise(MADV_DONTNEED)` 如何配合製造競爭條件的說明。
  - **為什麼值得讀**：比單一 commit message 更完整的攻擊者視角敘述,補上本章案例一省略的 exploit 層細節。
- **[(Pwn2Own) Linux Kernel route4_change Double Free Privilege Escalation Vulnerability — ZDI-22-1117](https://www.zerodayinitiative.com/advisories/ZDI-22-1117/)**
  - **這篇說什麼**：Zero Day Initiative 對 CVE-2022-2588 的官方 advisory,描述影響範圍與觸發所需權限。
  - **讀哪裡**：Vulnerability Details 段落。
  - **和本章的關聯**：案例二的佐證來源之一;搭配延伸閱讀的中文技術部落格一起讀,能拼出比較完整的根因圖像。

### 學術論文

- **[Wang, T. & Krinke, J., "Pairing Program with Potentially Vulnerable Fates" 相關的 double-fetch 系統性研究（The Secret That Is Hidden in Your Kernel 一系列,含 "Toward Efficient and Effective Detection of Linux Kernel Double-Fetch Bugs" 等）](https://discovery.ucl.ac.uk/1557280/)**
  - **核心貢獻**：用靜態模式比對系統性地在 Linux kernel 掃出 double-fetch 候選,人工驗證後分類成 type selection、size checking、shallow copy 三種典型場景。
  - **讀哪裡**：分類方法與案例研究段落(數學證明/工具實作細節可跳過)。
  - **和本章的關聯**：本章「double-fetch」一節的三種場景分類直接引用這份研究的分類框架;前提知識是本章 double-fetch 一節。

### 官方文件

- **[Documentation/RCU/whatisrcu.rst](https://docs.kernel.org/RCU/whatisrcu.html) 與 [Ch 39](./39-concurrency-model.md) 的延伸閱讀**
  - **讀哪裡**：RCU 讀者保證的精確邊界——理解「哪些同步機制其實不能擋住本章的某種 race」時的對照來源。
  - **和本章的關聯**：補強本章對「哪些 race 需要哪種同步手段才擋得住」的判斷依據。

三種模式、兩個真實案例看完,你現在能辨認 race,也知道它熬成什麼原語。但辨認不等於能打——大多數真實 race 的 window 窄到自然狀態下幾乎不會觸發。下一章教三個把 window 從「幾奈秒」拉長到「幾毫秒甚至更久」的技巧,把辨認出來的 race 變成可重複利用的原語。

→ [Ch 41 拉長 race window：userfaultfd、FUSE、調度操控](./41-widening-race-window.md)
