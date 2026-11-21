# Ch 44 — debug 工具鏈：KASAN / KCSAN / KMSAN / UBSAN / lockdep / ftrace / kprobe

> **目標**：知道 [Ch 10](./10-vulnerability-taxonomy.md) 裡每一類漏洞各該用哪個工具定位最快、能獨立讀懂一份 KASAN use-after-free report 的每個部分、理解 KCSAN/KMSAN/UBSAN/lockdep 各自偵測的是什麼「類」的問題而不會混用、以及會用 ftrace/kprobe/gdb+QEMU 做更細的動態追蹤。

> **環境**：Linux 6.6 LTS、x86-64、QEMU（延續 [Ch 0](./00-environment-setup.md)）。KASAN/KCSAN 用 GCC ≥ 11 或 Clang ≥ 11 皆可；KMSAN 目前**只支援 Clang ≥ 14.0.6**（kernel `Kconfig` 明確要求 `CC_IS_CLANG && CLANG_VERSION >= 140000`），且只有 x86_64 的 runtime 實作——這點本章用 WebFetch 查證過官方 `docs.kernel.org` 的說明，沒有十足把握的版本細節一律以你自己樹上的 `Kconfig` 為準。

## 為什麼需要這個？

[Ch 43](./43-syzkaller.md) 給你一個 crash，但 crash log 本身只告訴你「崩在哪一行」,不告訴你「為什麼崩」。memory corruption 類的 bug 有一個惡名昭彰的特性——**崩潰的地方往往不是出錯的地方**。一個 heap overflow 可能在寫入當下什麼事都沒發生（只是悄悄蓋掉了相鄰物件的某個欄位），直到幾百次呼叫之後,那個被蓋壞的欄位才在完全不相關的程式碼路徑裡被使用,此時才 panic。如果你只看 panic 時的 call trace,你看到的是「受害者倒下的地方」,不是「兇手下手的地方」。

這就是為什麼 kernel 開發出了一整套動態分析工具——它們各自用不同手法把「下手瞬間」和「倒下瞬間」這兩個時間點拉近,甚至直接讓你在下手那一刻就當場抓到。本章的核心不是教你每個工具的所有旗標,是教你一張**對應表**：看到一類症狀,該往哪個工具去查。

## 先建立直覺

把這些工具想成醫院裡不同科別的檢測儀器——你不會拿 X 光去照血液,也不會拿血液檢驗去看骨折：

```
症狀                           → 該用的工具
────────────────────────────────────────────────
越界讀寫 / UAF / double free    → KASAN     (記憶體存取的「圍欄感應器」)
兩條執行緒沒上鎖就同時碰同一塊記憶體 → KCSAN   (資料競爭的「監聽器」)
用了從沒被賦值過的記憶體         → KMSAN    (未初始化的「追蹤染料」)
整數溢位 / 未定義行為            → UBSAN    (語言規格的「糾察隊」)
可能死鎖（鎖的順序前後矛盾）     → lockdep  (鎖依賴圖的「拓樸檢查」)
想知道某段路徑實際執行了什麼、多久 → ftrace   (執行過程的「行車紀錄器」)
想在特定函式進出時動態插話       → kprobe/kretprobe (「竊聽器」，不用重編譯)
想看懂當下記憶體裡某個 struct 的實際內容 → gdb + QEMU (「驗屍」)
```

這張圖是本章接下來逐一展開的骨架。先記住一個原則：**這些工具多數是偵測型（detection），不是防護型（mitigation）**——它們讓本來會「悄悄發生、很久以後才爆炸」的 bug，在發生的瞬間就讓你知道，但它們幾乎都有顯著的效能/記憶體開銷,不會在生產環境的 kernel 上開（[Ch 26](./26-modern-mitigations.md) 講的是生產環境實際會開的硬化機制,是不同的範疇,別搞混）。

## 核心概念：KASAN 怎麼抓到越界存取與 UAF

KASAN（Kernel Address Sanitizer）是本章最重要的工具，因為 UAF/OOB 是現代 kernel pwn 的主角（[Ch 10](./10-vulnerability-taxonomy.md)）。它的原理是**在每個記憶體物件周圍包一層「圍欄」，並且記錄每一塊記憶體目前的合法狀態**：

```
正常的 slab object 配置（無 KASAN）：
┌─────────────┬─────────────┬─────────────┐
│  object A   │  object B   │  object C   │   相鄰緊貼，越界一個 byte
└─────────────┴─────────────┴─────────────┘   就直接踩進下一個物件

KASAN（Generic mode）：
┌─────────────┬────────┬─────────────┬────────┐
│  object A   │ redzone│  object B   │ redzone│   物件之間插入一段
└─────────────┴────────┴─────────────┴────────┘   「不該被存取」的 redzone

每 8 bytes 一個 shadow byte 記錄這塊記憶體的狀態：
  00        = 這 8 bytes 全部可存取
  1~7       = 只有前 N bytes 可存取（物件結尾沒對齊 8 的倍數）
  負值      = 整個 granule 不可存取，用不同負值區分「這是 redzone」
             還是「這是已釋放的記憶體」
```

這套 shadow memory 由 Generic KASAN（`CONFIG_KASAN_GENERIC`）實作——它是目前最通用、覆蓋面最廣的模式，支援多種架構，但有明顯的效能與記憶體開銷（KASAN 要為每 8 bytes 的真實記憶體額外配置 1 byte 的 shadow，外加 redzone 本身佔用的空間）。除此之外還有兩個 arm64 專屬的模式：Software Tag-Based KASAN（`CONFIG_KASAN_SW_TAGS`，用記憶體位址裡的 tag bits 而非獨立 shadow 記憶體，開銷較低，定位是 debug/dogfood 用）和 Hardware Tag-Based KASAN（`CONFIG_KASAN_HW_TAGS`，直接利用 arm64 MTE 硬體特性，開銷低到可以考慮在正式產品上當作 in-field 的偵測機制）。本書以 x86-64 為主,只有 Generic mode 可用。

**每次存取記憶體時**（編譯器插樁或硬體輔助），KASAN 檢查對應的 shadow byte：如果該讀/寫範圍踩進了標記為「不可存取」的區域，立刻印出報告並（依設定）panic 或繼續跑（`panic_on_warn`）。因為檢查發生在**存取的瞬間**，不是等到後續某個不相關的地方才爆炸——這正是解決「崩潰地點不是出錯地點」問題的方法。

### Quarantine：為什麼 free 之後的記憶體不會馬上被重用

Generic KASAN 有一個額外機制——**quarantine**（實作在 `mm/kasan/quarantine.c`）：一個物件被 `kfree` 之後,不會立刻真正釋放回 SLUB 給下一次 `kmalloc` 重用，而是先進入一個暫存佇列,被標記成「已釋放」的 shadow 狀態保留一段時間，等佇列滿了才真正釋放。

這個機制直接影響你讀 UAF report 的方式——也直接影響你在[Ch 18](./18-uaf-double-free.md)、[Ch 19](./19-spray-objects.md)學的「free 之後馬上 spray 搶佔同一塊記憶體」的技巧：**在開著 KASAN 的除錯環境下，這套搶佔技巧會因為 quarantine 而失效或變得不穩定**，因為被釋放的物件暫時被 KASAN 攔住不讓它立刻被重新配置。這不代表 bug 不存在或利用在真實（沒開 KASAN 的）環境下不成立——恰恰相反,這正是為什麼 KASAN 是研究/除錯用的工具,不是生產環境會開的東西：它改變了記憶體重用的時序,讓你能**先確認 bug 本身成立**,之後再回到沒有 KASAN 的乾淨環境去驗證完整的利用鏈。

## 範例一：逐行讀一份真實的 KASAN use-after-free report

下面這份 splat 取自 [Ch 42](./42-patch-to-rootcause.md) 分析過的 CVE-2023-32233 修復 commit 裡附的真實 KASAN 輸出（透過 GitHub API 查證過的 commit message 原文，非憑記憶轉述；完整觸發細節見 Ch 42）：

```
[   64.921510] ==================================================================
[   64.923123] BUG: KASAN: wild-memory-access in nf_tables_commit+0xa24/0x1490 [nf_tables]
[   64.924745] Write of size 8 at addr dead000000000122 by task test/890
[   64.927903] CPU: 3 PID: 890 Comm: test Not tainted 6.3.0+ #253
[   64.931120] Call Trace:
[   64.932699]  <TASK>
[   64.934292]  dump_stack_lvl+0x33/0x50
[   64.935908]  ? nf_tables_commit+0xa24/0x1490 [nf_tables]
[   64.937551]  kasan_report+0xda/0x120
[   64.939186]  ? nf_tables_commit+0xa24/0x1490 [nf_tables]
[   64.940814]  nf_tables_commit+0xa24/0x1490 [nf_tables]
[   64.942452]  ? __kasan_slab_alloc+0x2d/0x60
[   64.944070]  ? nf_tables_setelem_notify+0x190/0x190 [nf_tables]
[   64.945710]  ? kasan_set_track+0x21/0x30
[   64.947323]  nfnetlink_rcv_batch+0x709/0xd90 [nfnetlink]
[   64.948898]  ? nfnetlink_rcv_msg+0x480/0x480 [nfnetlink]
```

逐段拆解：

- **標題行 `BUG: KASAN: wild-memory-access in nf_tables_commit+0xa24/0x1490`**——`wild-memory-access` 是 KASAN 幾種 bug 類型標籤之一，代表存取的位址**完全落在合法記憶體範圍之外**（連 shadow memory 都無法對應），常見原因是對一個已經被破壞成哨兵值的指標解參照。KASAN 的 bug 標題是**它依照有限資訊做的最佳猜測**，不是精確分類——碰到 `wild-memory-access` 而不是教科書式的 `use-after-free`，不代表這不是 UAF,只代表位址已經壞到連 KASAN 自己的記憶體範圍判斷邏輯都認不出它曾經屬於哪個合法物件。
- **`Write of size 8 at addr dead000000000122`**——這個位址的 `dead...` 前綴**不是隨機垂直的巧合**，是 kernel 一套刻意設計的「哨兵值」慣例（如 `LIST_POISON1`/`LIST_POISON2` 等於 `0x100`/`0x200` 加上 `dead000000000...` 的高位），kernel 在把某些鏈結資料結構的指標欄位標記成「已失效」時，故意寫入這類容易識別的垃圾值。看到位址開頭是 `dead`，幾乎可以直接判斷：**這是對一個已經被標記失效（通常是已釋放或已從鏈結串列移除）的物件做了寫入**——這比等著你自己去比對 alloc/free stack 快得多，養成看到這類哨兵位址直接判斷方向的習慣。
- **`Write of size 8`**——這次存取動作是寫入 8 bytes，對應 x86-64 上一個指標或 `long` 欄位的大小，暗示兇手是「寫一個指標/計數欄位」而不是操作一段 buffer。
- **Call Trace**——從下往上讀（`nfnetlink_rcv_batch` → `nf_tables_commit`），這是**當下**這次崩潰發生時的呼叫鏈,不是「誰配置了這塊記憶體」或「誰釋放了它」。
- **沒有 Allocated by / Freed by 區塊**——這份特定 splat（`wild-memory-access`）沒有附上物件生命週期的 stack trace，因為 KASAN 判斷這個位址甚至不在任何已知 slab 物件的追蹤範圍內（它已經壞到連「這曾經是哪個物件」都查不到）。一般典型的 `use-after-free` 標題（不是 `wild-memory-access`）才會附上完整的 `Allocated by task N:` 與 `Freed by task N:` 兩段 stack trace——那兩段才是定位根因**最省力**的捷徑：直接告訴你這塊記憶體在哪個函式配置、在哪個函式釋放，不需要自己猜。

> 一般完整的 UAF report 格式長這樣（依官方 `docs.kernel.org/6.6/dev-tools/kasan.html` 文件描述的結構重建，非上面這份真實 splat 的內容）：
> ```
> BUG: KASAN: use-after-free in <faulting_func>+0x.../0x...
> Read/Write of size N at addr ffff888... by task ...
> Call Trace:
>   <當下存取發生的呼叫鏈>
> Allocated by task <pid>:
>   <配置時的呼叫鏈>
> Freed by task <pid>:
>   <釋放時的呼叫鏈>
> ```
> 讀到這種完整格式時，**先看 Freed by，再看 Call Trace**——兩者之間的落差就是你要重建的「free 之後、use 之前到底發生了什麼」那段故事,這正是 [Ch 18](./18-uaf-double-free.md) 的分析起點。

## KCSAN：抓資料競爭，而不是抓死鎖

KCSAN（Kernel Concurrency Sanitizer）處理的是和 KASAN 完全不同的問題類別——它不管記憶體有沒有越界，它管的是**兩條執行緒有沒有在沒有適當同步的情況下同時碰同一塊記憶體**，對應 [Ch 10](./10-vulnerability-taxonomy.md) 的 race condition 類（與 [Ch 39](./39-concurrency-model.md)、[Ch 40](./40-race-conditions.md) 直接呼應）。

啟用方式是 `CONFIG_KCSAN=y`（需要 GCC 或 Clang 11 以上編譯器支援相關插樁）。它的報告格式：

```
BUG: KCSAN: data-race in <func1> / <func2>

read to 0xffff888... of 8 bytes by task 1234 on cpu 0:
 func1+0x.../0x...
 ...

write to 0xffff888... of 8 bytes by task 5678 on cpu 2:
 func2+0x.../0x...
 ...

value changed: 0x0000000000000001 -> 0x0000000000000002
```

重點是它同時給你**兩條**執行緒各自的存取點與 stack trace——這直接對應 [Ch 10](./10-vulnerability-taxonomy.md) 畫的「兩條執行緒對同一份狀態競爭」示意圖，KCSAN 把那張示意圖變成了可以實際對照的報告。`value changed` 那一行告訴你這次真的觀測到值被改變了（而不只是理論上存在競爭但碰巧沒造成實際影響）——`CONFIG_KCSAN_REPORT_VALUE_CHANGE_ONLY` 這個選項可以設定成只在值真的改變時才報告，減少雜訊。

**KCSAN 和 lockdep 的差別，是本章踩雷集錦會特別強調的一點，這裡先定調**：KCSAN 抓的是**實際發生過**的競爭存取（執行當下真的被插樁邏輯觀測到）；lockdep（下一節）抓的是**理論上可能發生**的鎖排序問題，兩者的保證強度完全不同。

## KMSAN：未初始化記憶體的追蹤染料

KMSAN（Kernel Memory Sanitizer）對應 [Ch 10](./10-vulnerability-taxonomy.md) 的 uninitialized memory 類——它追蹤每一個記憶體位元組是否曾被**明確賦值**過,一旦程式碼對一塊「還沒被寫入過」的記憶體做了有意義的使用（例如拿去做分支判斷、拿去 `copy_to_user` 回傳給使用者），立刻報告。

這和 KASAN 的「圍欄」邏輯不同——KMSAN 像是在每個 byte 旁邊貼一張「是否已上色」的標籤（shadow），再加一份「這個值是從哪裡傳播過來的」的來源追蹤（origin tracking）,讓報告能告訴你「這個未初始化的值,最初來自哪個函式裡的哪個區域,一路傳播到了哪裡被使用」。

KMSAN 目前的限制（本段經查證 `docs.kernel.org` 官方文件與 Kconfig 說明）：

- **只支援 Clang**，且要求 **Clang ≥ 14.0.6**。GCC 目前沒有對應的插樁支援。
- **runtime 目前只支援 x86_64**。
- 開銷非常重（文件明確說明不適合一般日常開發使用,是專門為了抓未初始化記憶體問題而開的重量級模式），通常只在針對性地懷疑某個路徑有未初始化洩漏時才單獨開一個 build 來跑。
- 和 `KASAN`/`KCSAN` 互斥（`Kconfig` 明確寫 `depends on ... !KASAN && !KCSAN`），不能同時開三個。

對應到攻擊面：uninitialized memory 最常見的危害是 info leak（[Ch 14](./14-kaslr-infoleak.md)）——一塊沒清過的記憶體被直接或間接回傳給使用者，裡面殘留的 kernel 指標變成免費的 KASLR/heap 位址洩漏。KMSAN 是目前主動找這類洩漏源頭最精確的工具,比單靠人工逐行檢查「這個 struct 的每個欄位都初始化了嗎」快得多。

## UBSAN：抓語言規格層級的未定義行為

UBSAN（Undefined Behavior Sanitizer）不是 kernel 專屬的概念，是 GCC/Clang 都有的編譯期插樁,專門抓 C 語言規格裡「未定義行為」(undefined behavior) 的使用,例如：

- 有號整數溢位（`INT_MAX + 1`）
- 陣列存取超出編譯期已知的靜態邊界
- 位移操作的位移量超過型別寬度（`x << 64` 對一個 32-bit 型別）
- 透過不相容的指標型別做轉換後解參照

對應 [Ch 10](./10-vulnerability-taxonomy.md) 的 integer 處理錯那一類——注意 UBSAN 抓的是**編譯器規格層級**的未定義行為,它能抓到「這段運算在 C 標準底下是未定義的」,但抓不到「這個運算結果後來被拿去做了什麼危險的事」（例如拿去配置記憶體大小）。UBSAN 報告本身只是一個起點，後續還是要靠人工判斷這個未定義行為有沒有被餵進危險的用途（回 [Ch 10](./10-vulnerability-taxonomy.md) 「看到可疑的 size 算術,要看它有沒有餵進 size/index」那條踩雷提醒）。

## lockdep：鎖依賴圖的拓樸檢查，不是競爭偵測

lockdep（`CONFIG_PROVE_LOCKING`）做的事和 KCSAN 表面上都跟「並發」有關,但機制完全不同——lockdep 不去看記憶體存取,它追蹤**每一次鎖的取得順序**,建一張「鎖 A 在鎖 B 之前被拿」的依賴圖。如果某次執行路徑裡,鎖的取得順序和之前觀測到的順序矛盾（例如某處先拿 A 再拿 B,另一處先拿 B 再拿 A），lockdep 判定這是一個**潛在的死鎖**，即使這次執行本身沒有真的死鎖——因為死鎖需要特定時序才會真的卡住,lockdep 的價值在於它靠靜態的順序矛盾就能提前示警,不需要運氣好真的撞上死鎖的那個瞬間。

```
lockdep 看到的依賴圖矛盾範例：

路徑 1:  lock(A) → lock(B)
路徑 2:  lock(B) → lock(A)
           ↑
    這兩條路徑單獨看都不會馬上死鎖，
    但只要兩條路徑的執行緒剛好交錯撞上，
    就會互相等待對方放手 —— lockdep 看到
    這組矛盾順序，立刻報告，不需要真的撞上
```

這就是為什麼本章要把 lockdep 和 KCSAN 分開講清楚：**lockdep 報告 ≠ 真的發生了死鎖**，它報告的是「依照目前觀測到的鎖使用順序,存在死鎖的可能性」。這對漏洞研究的意義是——lockdep warning 本身通常不是一個可以直接拿來打的漏洞,但它常常**指向**一段並發設計有問題的程式碼,值得你回頭用 KCSAN 或人工分析確認這段程式碼是否也藏著實際的 race condition（[Ch 40](./40-race-conditions.md)）。

## 範例二：讀一份 lockdep 潛在死鎖報告

和範例一的 KASAN 真實 splat 不同，這裡給的是依官方文件描述的 lockdep 報告**典型結構**重建（未實測，標準格式請以 `docs.kernel.org/locking/lockdep-design.html` 與你自己機器上實際觸發的報告為準）：

```
======================================================
WARNING: possible circular locking dependency detected
6.6.0 #1 Not tainted
------------------------------------------------------
test/1234 is trying to acquire lock:
ffff888012345678 (&obj_a->lock){+.+.}-{3:3}, at: func_x+0x40/0x80

but task is already holding lock:
ffff888087654321 (&obj_b->lock){+.+.}-{3:3}, at: func_y+0x30/0x70

which lock already depends on the new lock.

the existing dependency chain (in reverse order) is:

-> #1 (&obj_b->lock){+.+.}-{3:3}:
       func_z+0x50/0x90
       func_y+0x30/0x70

-> #0 (&obj_a->lock){+.+.}-{3:3}:
       func_w+0x20/0x60
       func_x+0x40/0x80

other info that might help us debug this:

 Possible unsafe locking scenario:

       CPU0                    CPU1
       ----                    ----
  lock(&obj_a->lock);
                                lock(&obj_b->lock);
                                lock(&obj_a->lock);
  lock(&obj_b->lock);

 *** DEADLOCK ***
```

逐段拆解：

- **標題 `possible circular locking dependency detected`**——注意是 `possible`，這就是本章踩雷集錦要強調的核心：lockdep 在這次執行裡,只是「現在正要拿 `obj_a->lock`，但已經持有 `obj_b->lock`，而歷史上曾經觀測到有路徑反過來拿（先 `obj_b` 後 `obj_a`）」，兩種順序同時存在就構成循環依賴的可能，**不代表這次真的卡死了**——這次呼叫很可能順利往下跑完全沒出任何問題，lockdep 只是在記錄下「順序矛盾」這個事實。
- **`the existing dependency chain`**——lockdep 用 `#0`/`#1` 編號列出造成矛盾的兩段鎖依賴，各自附上簡短的呼叫鏈,讓你能分別去讀 `func_y`→`func_z` 那條路徑和 `func_x`→`func_w` 那條路徑，確認兩者是否真的可能在不同執行緒上同時發生。
- **`Possible unsafe locking scenario` 的 CPU0/CPU1 示意圖**——這是 lockdep 幫你畫出的、本章「先建立直覺」那張圖的具體版本：兩條 CPU 各自按照自己觀測到的順序拿鎖,交錯起來就會互相等待。這張圖是報告裡**最值得先看**的部分，比起直接去讀落落長的 call trace,它用最少的資訊讓你判斷這個矛盾是否在你的子系統設計裡真實可達。

**和 KCSAN 報告的關鍵差異**：KCSAN 的報告（上一節格式）附帶 `value changed` 這類「這次真的觀測到異常」的證據；lockdep 的報告完全是**推論性**的——它甚至可能在系統生命週期裡這兩條路徑永遠不會真的被兩個不同執行緒同時命中（例如兩條路徑其實都在同一個持有全域大鎖的情境下才會被呼叫,實務上不可能並發）。拿到 lockdep 報告後，正確的下一步是回頭讀兩段呼叫鏈的呼叫情境，判斷矛盾是否「理論存在但實務不可達」，還是「確實是一個該修的設計缺陷」。

## 進一步用法：ftrace 與 kprobe/kretprobe

前面幾個工具都是「被動等它自己報告」，ftrace 和 kprobe 是你**主動**想追蹤某個函式的行為時用的工具,不需要重新編譯整個 kernel（相對於 KASAN/KCSAN/KMSAN 都要在 build 階段就決定開不開）。

**ftrace** 是內建的函式追蹤框架，透過 `/sys/kernel/debug/tracing/` 控制：

```bash
# 追蹤某個函式被呼叫的次數與呼叫鏈（未實測，依 ftrace 官方文件慣用指令重建）
echo function_graph > /sys/kernel/debug/tracing/current_tracer
echo nf_tables_commit > /sys/kernel/debug/tracing/set_ftrace_filter
echo 1 > /sys/kernel/debug/tracing/tracing_on
cat /sys/kernel/debug/tracing/trace
```

這在分析 race window（[Ch 41](./41-widening-race-window.md)）或想知道「某個函式到底多久才會被呼叫一次、呼叫時機和哪些其他動作重疊」時特別有用——它給你的是**時序**上的觀察,而不是記憶體正確性上的判斷。

**kprobe/kretprobe** 讓你在**任意函式的進入點**（kprobe）或**返回點**（kretprobe）動態插入一段處理邏輯，常見用途是透過一個小 kernel module 在特定函式進出時印出參數值或回傳值，等同於「不用重新編譯目標函式所在的檔案,就能加一行 printk」。這在你懷疑某個函式在特定條件下被呼叫、但不想為了驗證一個假設就重新編譯整個 kernel 時，是最輕量的驗證手段。

## gdb + QEMU 進階用法

[Ch 0](./00-environment-setup.md) 已經建好 gdb remote attach 到 QEMU 的基本流程，這裡補幾個 kernel exploit 分析時常用的進階用法：

```gdb
# 讀一塊記憶體，用某個 struct 型別去解讀它的欄位
# (未實測，依 gdb 標準語法重建；需要 vmlinux 帶 debug symbol，即 CONFIG_DEBUG_INFO=y)
p *(struct cred *)0xffff888012345000

# 算一個欄位在 struct 裡的 offset（和手動查原始碼比，不會算錯）
p &((struct task_struct *)0)->cred

# 條件式中斷點：只在 r.idx 超出範圍時才停下來，而不是每次呼叫都停
break dev_ioctl if r.idx > 100

# 列出目前所有被鎖持有的狀態（配合 lockdep 一起看，驗證目前持鎖狀況）
p $lx_current()->held_locks   # 需要 Linux kernel 的 gdb python 輔助腳本（scripts/gdb/）
```

Linux kernel 原始碼樹的 `scripts/gdb/` 目錄提供一組 gdb python 輔助指令（`lx-dmesg`、`lx-ps`、`lx-current` 等），能讓你直接用 kernel 的內部資料結構名稱查詢執行狀態，而不必每次都手動算位址——在你還不熟悉某個子系統的資料結構時，這組腳本能省掉大量手算 offset 的時間。

## 對比與取捨：一張總表

| 工具 | 偵測什麼 | 對應 Ch 10 類別 | 編譯要求 | 開銷 | 能用在生產環境嗎 |
|---|---|---|---|---|---|
| KASAN (Generic) | 越界讀寫、UAF、double free | OOB、UAF、double free | `CONFIG_KASAN_GENERIC` | 高（額外 shadow 記憶體 + 插樁） | 否，debug/fuzzing 專用 |
| KCSAN | 未同步的並發記憶體存取 | race / TOCTOU | `CONFIG_KCSAN`，GCC/Clang ≥ 11 | 中（取樣式插樁，比 KASAN 輕） | 否 |
| KMSAN | 使用未初始化記憶體 | uninitialized | `CONFIG_KMSAN`，**僅 Clang ≥ 14.0.6**，僅 x86_64 | 非常高 | 否，連日常開發都不建議常開 |
| UBSAN | C 語言未定義行為 | integer 處理錯 | `CONFIG_UBSAN`，GCC/Clang 均可 | 低～中（視子選項） | 部分子選項可（如 `UBSAN_TRAP`），但完整模式不建議 |
| lockdep | 鎖取得順序的潛在死鎖 | race（提前示警，非直接分類） | `CONFIG_PROVE_LOCKING` | 中 | 否，debug 用 |
| ftrace | 函式呼叫時序、執行路徑 | 不對應特定類別，是通用時序觀察工具 | `CONFIG_FUNCTION_TRACER` 等 | 低（視追蹤範圍） | 部分場景可（生產環境常開輕量 tracepoint） |
| kprobe/kretprobe | 任意函式進出點的動態插話 | 不對應特定類別 | `CONFIG_KPROBES` | 視插入的處理邏輯而定 | 可，常用於生產環境動態診斷 |

## 踩雷集錦

1. **「KASAN 開著就代表這個 kernel 沒有 UAF 了」**。錯——KASAN 是**偵測**工具，不是**修復**工具。一個開著 KASAN 的 kernel 碰到 UAF 一樣會觸發，差別只是它會立刻報告而不是悄悄損壞記憶體。生產環境的 kernel 幾乎從不開 KASAN（開銷太大），所以「這台生產機器開著 KASAN」幾乎不會是你會遇到的真實情況——KASAN 是你在**自己的分析環境**裡用來定位根因的工具。
2. **「lockdep 報告了，代表真的發生過死鎖」**。前面已經講過——lockdep 報告的是**依照觀測到的鎖順序推論出的潛在矛盾**，不是「這次執行真的卡死了」。把 lockdep warning 直接當成「系統真的掛了」的證據是誤讀,正確的反應是把它當成「這個路徑的鎖設計值得深入檢查」的提示。
3. **「KCSAN 沒報告，代表這段程式碼沒有 race condition」**。KCSAN 靠**取樣式**插樁（不是對每一次記憶體存取都插樁，那樣開銷會過高），而且它只能抓到**實際被兩條執行緒同時踩到**的存取——如果你跑的測試案例沒有讓那個競爭窗口真正被兩條執行緒同時命中（可能窗口太窄,或你的測試沒有製造出對的時序），KCSAN 不會報告,但 bug 可能依然存在。沒報告不等於沒有,只代表這次沒被抓到。
4. **「開了 sanitizer 之後 bug 消失了,所以 bug 不是真的」**。這是一個反直覺但很常見的現象，稱為 heisenbug——sanitizer 的插樁本身會改變程式的執行時序（多了額外的檢查邏輯，執行變慢），對一個高度依賴精確時序的 race condition 來說，插樁造成的延遲可能剛好讓原本會撞上的競爭窗口錯開了，導致 bug「表面上消失」。正確的理解是：bug 本身的存在與否和它會不會在某個特定插樁環境下被觀測到,是兩件事——race condition 類的 bug 尤其容易在換了分析工具之後變得「時有時無」，這正是為什麼 [Ch 41](./41-widening-race-window.md) 要專門教你怎麼主動拉寬窗口,而不是依賴運氣。
5. **「KASAN 的 bug 標題（如 use-after-free、slab-out-of-bounds）一定精確分類」**。KASAN 官方文件自己承認這些標題是**依照有限資訊做的最佳猜測**（best-effort），本章範例一的 `wild-memory-access` 就是一個例子——實際根因是一個完全可以歸類成 UAF 的邏輯錯誤,但因為位址壞到連 KASAN 自己都查不到來源,它只能給出一個更保守的標籤。看到標題時,把它當成「起點的提示」,不要當成「最終的分類結果」。

## 進階：組合使用與自動化 symbolize

- **syzkaller + KASAN 是標準組合**——[Ch 43](./43-syzkaller.md) 的 fuzz target 幾乎總是同時開著 KASAN，這樣 fuzzer 撞到的每個記憶體錯誤都能在發生瞬間被精確捕捉,而不是悄悄損壞後才在不相關的地方爆炸,大幅提高 crash report 的可用性。
- **symbolize 位址**：KASAN/一般 oops 的 call trace 裡印出的是「函式名+offset」（如 `nf_tables_commit+0xa24/0x1490`），如果你拿到的是裸位址而不是符號,`scripts/faddr2line`（kernel 原始碼樹內建）可以幫你把一個位址精確對應回原始碼的檔案與行號,比手動對 `System.map` 快得多。
- **針對性單獨開 KMSAN/UBSAN**：因為 KMSAN 開銷極高且和 KASAN 互斥,實務上常見的流程是——先用開著 KASAN 的環境跑一輪 fuzzing/測試,等確認某個路徑「看起來」有未初始化記憶體洩漏的嫌疑後,才另外切一份單獨開 KMSAN 的 build 針對性驗證,而不是從頭就同時開所有工具。

- **把 KASAN report 自動接回 Ch 42 的方法論**：拿到一份完整的 `Allocated by`/`Freed by` stack trace 後,去對應子系統的 git log 搜尋這兩段呼叫鏈涉及的函式名,往往能直接定位到某次 refactor 或某個 commit 動過這段生命週期管理——這條路經常比從頭手動分析更快找到根因,是 [Ch 42](./42-patch-to-rootcause.md) 方法論在有了精確 KASAN report 之後的加速版。

## 動手練習

1. 拿 [Ch 5](./05-first-vulnerable-module.md) 建的 vulnerable module,刻意引入一個 UAF（`kfree` 之後不清空指標,之後再使用），在開著 `CONFIG_KASAN_GENERIC` 的 kernel 上觸發它,完整讀一遍自己機器上跑出來的報告——對照本章範例一的拆解方式，確認你能辨認出標題、存取類型、以及（若有）alloc/free stack 各自的位置。
2. 刻意寫一段兩條核心執行緒沒上鎖就同時寫同一個全域變數的程式碼，在開著 `CONFIG_KCSAN` 的環境跑,觀察報告格式和 KASAN 的差異。
3. 用 `scripts/faddr2line`，對你自己 build 的 kernel 裡的任意一個函式位址做一次反查，確認它能正確對應回原始碼行號。
4. 用 ftrace 的 `function_graph` tracer，追蹤一次完整的你自訂 ioctl 呼叫，觀察它實際走過哪些 kernel 內部函式——比對你讀原始碼時猜測的呼叫鏈是否吻合。

## 本章重點整理

- 不同類別的 bug 對應不同工具：KASAN 管記憶體越界/UAF,KCSAN 管並發競爭,KMSAN 管未初始化記憶體,UBSAN 管語言層級未定義行為,lockdep 管鎖順序的潛在矛盾——記混了會用錯工具,白做工。
- KASAN 的核心機制是物件周圍的 redzone + shadow memory 記錄存取合法性,quarantine 機制讓已釋放記憶體不會立刻被重用,這會影響你在除錯環境下驗證「free 後 spray」技巧的時序。
- lockdep 報告「潛在」死鎖，KCSAN 報告「實際觀測到」的競爭存取——兩者的保證強度不同,千萬別混用結論。
- ftrace/kprobe 是主動追蹤工具,不需要重編譯整個 kernel,適合驗證「這個函式到底什麼時候被呼叫、呼叫鏈長什麼樣」這類假設。

## 自我檢核

- [ ] 不看筆記，能說出 Ch 10 的每一類漏洞,分別該優先用哪個工具去定位
- [ ] 能解釋為什麼 KASAN 報告的「崩潰位置」和「bug 實際發生位置」可能不是同一處，以及哪個欄位（alloc/free stack）能幫你補上這段落差
- [ ] 能說出 KCSAN 和 lockdep 的根本差異（實際觀測 vs. 理論推論），並舉出一個「lockdep 報告但未必真的死鎖」的情境
- [ ] 能解釋 KASAN 的 quarantine 機制如何影響你驗證「free 之後立刻 spray」的時序,以及為什麼生產環境不開 KASAN 時這個限制就不存在
- [ ] 能說出 KMSAN 目前的編譯器與架構限制,以及為什麼它通常不和其他 sanitizer 一起全程開著跑

## 延伸閱讀

### 官方文件

- **[The Kernel Address Sanitizer (KASAN)](https://docs.kernel.org/dev-tools/kasan.html)**
  - **讀哪裡**："Usage" 一節的 config 選項列表,以及 "Error reports" 一節的完整 report 範例結構。
  - **和本章的關聯**：本章範例一的拆解方法,直接基於這份文件描述的 report 結構；quarantine 機制的說明也來自這份文件的 "Implementation details" 一節。
- **[The Kernel Concurrency Sanitizer (KCSAN)](https://docs.kernel.org/dev-tools/kcsan.html)**
  - **讀哪裡**："Usage" 與 "Race detection beyond data races" 兩節,了解 `data_race()`/`__no_kcsan` 這些主動排除誤報的標注方式。
  - **和本章的關聯**：補完本章「KCSAN」一節沒有展開的誤報抑制機制細節。
- **[Kernel Memory Sanitizer (KMSAN)](https://docs.kernel.org/dev-tools/kmsan.html)**
  - **讀哪裡**：開頭的編譯器與架構需求說明（本章版本資訊的查證來源）、以及 origin tracking 的機制說明。
  - **注意**：這份文件本身版本演進較快（KMSAN 相對較新),務必核對你樹上的實際版本。

### 部落格 / 技術文章

- **[Brendan Gregg — ftrace 系列文章](https://www.brendangregg.com/blog/2019-09-02/learn-ebpf-tracing.html)**（含其對傳統 tracing 工具鏈的整理）
  - **這篇說什麼**：Brendan Gregg 長年維護 Linux tracing/效能分析生態的系統性整理，雖然近年重心偏向 eBPF,但對 ftrace/kprobe 這類傳統機制的定位解說非常清楚。
  - **讀哪裡**：文章前半段對 ftrace/kprobe 與新一代 eBPF-based tracing 的比較。
  - **為什麼值得讀**：作者是這個領域公認的權威，對「該用哪個 tracing 工具」這類決策給的建議經過大量實戰驗證。

### 原始碼 / 權威來源

- **[mm/kasan/ 原始碼（Bootlin Elixir）](https://elixir.bootlin.com/linux/v6.6/source/mm/kasan)**
  - **讀哪裡**：`quarantine.c`（quarantine 佇列實作）與 `report.c`（report 格式化邏輯）。
  - **前提知識**：先讀完本章的 KASAN 核心概念再進來看實作，否則容易在 shadow memory 的位元運算細節裡迷路。
  - **和本章的關聯**：本章「quarantine」一節的機制描述,若要深究實作細節（佇列大小、何時真正釋放）,這是最終仲裁來源。

有了找洞的方法論（Ch 42）、找新洞的武器（Ch 43）、以及定位根因的工具鏈（本章），Part 7 最後一塊拼圖是把一個已經定位清楚的 bug，工程化地打磨成一個穩定、可重複、不會把目標機器打死的 exploit。

→ [Ch 45 — exploit 穩定化工程：heap grooming、race retry、cleanup、避免 panic、跨版本移植](./45-exploit-stabilization.md)
