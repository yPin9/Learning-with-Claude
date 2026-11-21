# Ch 29 — data-only attack 技術集：為什麼成主流、pipe_buffer、cred、合法 target 搜尋

> **目標**：把 [Ch 16](./16-classic-primitives.md) 的四個經典原語(改 cred、modprobe_path、core_pattern、poweroff_cmd)系統化成一套方法論,並補上 [Ch 16](./16-classic-primitives.md) 沒展開的進階著力點(`pipe_buffer` 的 flags/page、page table 預告)。學完你要能回答一個更難的問題:**給一個全新的、本章沒教過的 kernel 資料結構,你怎麼判斷它值不值得當作 data-only 的 target?**——這是比背題庫更長久的能力。

> **環境**：Linux 6.6 LTS、x86-64,KCFI 已開啟的情境作為本章的出發前提。涉及 struct 欄位 offset、kernel 版本特定行為的段落已盡量查證;查不到一手來源的地方標「就我所知」並降級措辭。涉及實作的程式片段除非特別標注,**未實測,為理論預期行為**。

## 承接 Ch 28:控制流被封死之後,資料層變成唯一的出口

[Ch 28](./28-cfi-kcfi.md) 的結論很乾脆:KCFI 在每個間接呼叫點前插入型別檢查,劫到的 RIP 如果指向一個「型別不吻合」的目標,呼叫端直接 `ud2`,oops。這封死的是**第一次進入控制流劫持**那一跳——[Ch 21](./21-heap-to-rip.md) 教的覆寫 `tty_struct->ops`、`seq_operations` 的函式指標,在 KCFI 底下多半就是在賭「你偽造的 fake ops table 型別剛好符合檢查」,而這件事本身的自由度已經被大幅壓縮。

但 [Ch 28](./28-cfi-kcfi.md) 同時也點出了 KCFI 不管的範圍:**backward-edge 的 `ret`(x86 kernel 沒有 shadow stack)、型別吻合的合法 target 之間的呼叫,以及完全不碰控制流的 data-only**。這三項裡,前兩項仍然是「控制流」的戰場,只是縫變窄了;第三項——data-only——是完全換了戰場。

這一章要問的核心問題是:**為什麼 kernel 擋得住「劫持控制流」,卻擋不住「寫壞資料」?** 答案不是技術上疏漏,是結構性的:

```
   控制流劫持：
   kernel 呼叫一個函式指標 ──▶ KCFI 檢查：這個指標的型別,
                                是不是這個呼叫點允許的型別?
                                ──▶ 不是 → 拒絕(ud2)

   data-only：
   kernel 寫一個欄位(uid=0、ops->page 指標、PTE 的值)
         ──▶ 沒有檢查點問:「這次寫入,是不是合法的業務邏輯觸發的?」
         ──▶ kernel 分不出「這是 setuid() 系統呼叫自己寫的 uid=0」
             還是「某個不相關的 heap bug 間接寫出來的 uid=0」——
             兩次寫入,從 CPU 執行的角度看,完全是同一種指令、同一種效果。
```

控制流劫持的「異常」容易定義:呼叫目標應該在一個有限的合法集合裡,不在集合裡就是異常,KCFI 可以機械化地檢查這件事。資料寫入的「異常」沒有這種機械化定義——`struct cred` 的 `uid` 欄位本來就應該被寫(每次 `setuid()`、`execve()` 的 setuid 處理都在合法地寫它),kernel 沒有辦法僅憑「這次寫入發生了」就判斷它是不是被攻擊者觸發的。要分辨兩者,你得知道**這次寫入的呼叫鏈,邏輯上有沒有道理**——這是語意層的問題,不是型別層的問題,而目前沒有任何 production mitigation 在做語意層的檢查(本章最後會談這條防禦方向目前卡在哪裡)。

這正是 [Ch 16](./16-classic-primitives.md) 最後一節點出的事:**data-only 攻擊 kernel 根本分不出『這個寫是合法的還是惡意的』,因為它寫的是合法可寫的資料結構。** 本章把這句話系統化成一套找 target 的方法。

## 先建立直覺:兩種「值錢的寫入」

```
            data-only 的著力點分兩種
   ┌─────────────────────────┬─────────────────────────┐
   │  A. 固定全域變數          │  B. 結構裡的「次要」欄位   │
   │  (Ch 16 已教)            │  (本章深化)               │
   ├─────────────────────────┼─────────────────────────┤
   │  modprobe_path           │  cred 的 uid/gid/cap     │
   │  core_pattern            │  pipe_buffer 的          │
   │  poweroff_cmd            │    flags/page/ops        │
   │                          │  file 的 f_cred/f_op     │
   │                          │  PTE 的 permission bits  │
   ├─────────────────────────┼─────────────────────────┤
   │  特徵：單一全域位址,       │  特徵：隸屬某個物件,       │
   │  找到一次,全系統通用       │  通常要先 spray/UAF       │
   │  不需要先拿到特定物件      │  拿到「正確那一份」才能寫  │
   └─────────────────────────┴─────────────────────────┘
```

A 類是 [Ch 16](./16-classic-primitives.md) 的範圍:全域變數,門檻是「找到位址 + 寫入」,觸發方式固定(execve 未知格式、crash、poweroff)。B 類是這一章要補的:**藏在某個 heap 物件內部、必須先靠 UAF/overflow 拿到正確那一份,才能動手寫的欄位**——這類 target 通常比 A 類更隱蔽(不是全域符號,防守端寫 detection rule 更難覆蓋),但也更依賴 Part 3 的 heap 原語(spray、grooming、[Ch 27](./27-random-kmalloc-caches.md) 的 call site 對齊)先把「拿到正確物件」這一步做出來。

## 系統化 cred 攻擊:不只是 uid=0

[Ch 16](./16-classic-primitives.md) 教的改 cred 只示範了 uid/gid 歸零。完整的 `struct cred`(定義在 `include/linux/cred.h`)裡,還有幾個同樣值錢、但容易被忽略的欄位:

```c
struct cred {
        atomic_t       usage;
        kuid_t         uid;          /* ← Ch 16 已教 */
        kgid_t         gid;
        kuid_t         suid;
        kgid_t         sgid;
        kuid_t         euid;
        kgid_t         egid;
        kuid_t         fsuid;
        kgid_t         fsgid;
        unsigned       securebits;
        kernel_cap_t   cap_inheritable;
        kernel_cap_t   cap_permitted;
        kernel_cap_t   cap_effective;   /* ← capability,比 uid=0 更細緻 */
        kernel_cap_t   cap_bset;
        kernel_cap_t   cap_ambient;
        /* ... struct user_struct *user; struct user_namespace *user_ns; ... */
};
```

> `struct cred` 的完整欄位與排列隨版本可能調整,寫 exploit 前務必用 `pahole` 或你的 kernel 的 `include/linux/cred.h` 現場確認——這是 [Ch 16](./16-classic-primitives.md) 已經強調過的鐵律,這裡不重複示範,只補兩個 uid/gid 之外的著力點:

1. **capability 欄位(`cap_effective` 等)**:只改 uid 不改 capability,process 看起來是 root(`geteuid() == 0`),但某些 capability 檢查(`capable(CAP_XXX)`)走的是獨立的 capability 欄位,不是單純看 uid。如果你的攻擊場景需要特定 capability(例如 `CAP_SYS_ADMIN` 才能做某個後續動作),只寫 uid 不夠,要連 capability 欄位一起寫,或者乾脆把整個 `cred` 換成 `init_cred`(見下一段)。
2. **直接換指標,而非改內容**:另一條路是不去改 `struct cred` 的欄位內容,而是**把 `task_struct->cred`(或 `real_cred`)這個指標,改成指向 kernel 裡現成的 `init_cred`**——`init_cred` 是 kernel 初始化時就存在、uid/gid/capability 全部是最高權限的一份全域 cred,位址固定(相對 kernel base)。這比「算出自己的 cred 位址、逐欄位改」更省事:你只需要一個「改某個 task_struct 的 cred 指標」的任意寫,和 `init_cred` 的位址(同樣只需要 kernel base,不需要額外 leak)。

```c
/* 概念:write-what-where 已有,且已知 current task_struct 的位址與 init_cred 位址 */
write64(task_struct_addr + OFF_CRED,      init_cred_addr);
write64(task_struct_addr + OFF_REAL_CRED, init_cred_addr);
/* 回到 user-space 後,current 的 cred 指標已經指向 init_cred——
   這個操作沒有改任何 cred 的內容,只是換了指標,
   比逐欄位寫 uid/gid/capability 省事且不容易漏改欄位。*/
```

這個「換指標而非改內容」的思路,是 [Ch 24](./24-dirty-cred.md) Dirty Cred 技術的核心雛形——Dirty Cred 把這個概念進一步推廣成「把一個低權限 process 的 `struct file` 換成高權限 process 的 `struct file`」,不限於 cred 這一種結構。本章只示範最小版本,完整技術留給 [Ch 24](./24-dirty-cred.md)。

## pipe_buffer:flags 與 page 欄位的著力點

[Ch 19](./19-spray-objects.md) 介紹過 `pipe_buffer` 常被當作控流 victim(蓋 `ops` 指標)。但 `pipe_buffer` 同時也是 data-only 攻擊的經典 target,因為它的另外兩個欄位——`flags` 和 `page`——本身就是「寫資料就能造成嚴重後果」的例子。

```c
struct pipe_buffer {
        struct page *page;
        unsigned int offset, len;
        const struct pipe_buf_operations *ops;
        unsigned int flags;
        unsigned long private;
};
```

- **`flags` 裡的 `PIPE_BUF_FLAG_CAN_MERGE`**:這個 flag 決定「下一次寫入這個 pipe 時,能不能把新資料直接合併進這個已存在的 page,而不是配一個新 page」。CVE-2022-0847(Dirty Pipe,**查證**:這是一個真實存在、廣為人知的 CVE,root cause 是 `copy_page_to_iter_pipe()`/`push_pipe()` 路徑沒有正確清除這個 flag,讓一段原本該是 read-only 的 page cache 內容被當成可合併寫入的目標)就是圍繞這個 flag 的誤用。這裡的教訓不是「背 Dirty Pipe 的 PoC」,是看懂**為什麼一個 flag bit 能撬動這麼大的後果**:這個 bit 決定的是「這塊記憶體接下來的寫入語意」,如果它被設定在一個不該被設定的情境,寫入操作本身(完全合法的 `write()` syscall)就會產生「寫到不該寫的地方」的效果——這正是 data-only 攻擊最純粹的形態:**連 exploit 本身的最後一步都只是一次正常的 syscall,沒有任何控制流劫持,甚至沒有「任意寫原語」本身,只有一個被弄錯的 flag**。
- **`page` 指標**:如果你有辦法透過其他 bug(UAF、heap overflow)覆寫某個 `pipe_buffer` 的 `page` 欄位,讓它指向一個你不該有寫入權的 physical page(例如另一個 process 的記憶體、或 page cache 裡某個只讀檔案背後的 page),之後對這個 pipe 做讀寫操作,效果等同於**任意 physical page 的讀寫**——因為 pipe 的讀寫路徑本身就是合法、不受權限檢查額外限制的操作,它信任 `pipe_buffer->page` 指向的是「這個 pipe 應該擁有的那塊記憶體」。

> `pipe_buffer` 這兩個著力點的教訓可以抽成一句通則,下一節會把它正式定義:**一個欄位值不值得當 target,不看它叫什麼名字,看『如果這個欄位被寫壞,系統會不會用它做出一個你想要但不該被允許的動作』。** `flags` 和 `page` 都不是「函式指標」,它們只是幾個 bit 和一個指標,但後果和劫持 RIP 一樣嚴重。

## 預告:page table 作為 target(完整技術在 Ch 23)

把「值錢的寫入」這個概念推到極限,會走到 [Ch 23](./23-dirty-pagetable.md) 的 Dirty Pagetable:**PTE(page table entry)本身也是記憶體裡的資料,一樣可以被 UAF/overflow 覆寫**。PTE 裡的內容決定「這個虛擬位址對應哪個 physical page,以及這個對應允許什麼權限(可讀/可寫/可執行)」。如果你能把某個 PTE 的內容改成「指向一個你想讀寫的 physical page(例如另一個 process 的 page table 本身所在的 page),並且標記成可寫」,你就拿到了**整台機器任意 physical 記憶體的讀寫能力**——這比改單一個 `cred` 或單一個 `pipe_buffer` 強大得多,因為 PTE 本身管理的就是「什麼能被讀寫」這件事的底層權限。

這條技術完整的機制(怎麼讓 page table 所在的 page 被 UAF 到、怎麼確保你能精確控制覆寫的位元組落在 PTE 的哪個欄位)是 [Ch 23](./23-dirty-pagetable.md) 整章的內容,這裡只需要記住它在本章方法論裡的位置:**PTE 是「次要欄位」類 target 的終極案例——它不是某個子系統自己定義的欄位,是整個記憶體管理機制共用的底層資料結構,改壞它的影響範圍比任何單一 driver 的欄位都大。**

## 方法論:怎麼找出「合法可寫但後果致命」的 target

把本章和 [Ch 16](./16-classic-primitives.md) 看過的所有 target 抽出共通結構,整理成一個可重複套用的檢查清單。拿到一個新的 kernel 資料結構(可能是你在讀某個 driver 原始碼時看到的),問下面四個問題:

```
   Q1: 這個欄位的值,會不會在之後的某個路徑上,
       被 kernel「信任」並據此做出一個有特權意義的動作?
       （例如：決定要不要允許存取、決定以什麼身份執行、
        決定某段記憶體的讀寫權限）
            │
            ├─ 否 → 不是好 target，繼續找
            ▼
   Q2: 從「正常、合法的使用情境」出發，
       有沒有任何一條路徑，能把這個值「正常地」改成攻擊者想要的值？
       （例如：設一個 flag、換一個指標、動態調整長度）
            │
            ├─ 有 → 這通常代表 kernel 本身就有「寫這個欄位」的合法邏輯，
            │        attacker 要做的只是讓這段邏輯在錯誤的情境下被觸發
            │        （pipe_buffer 的 flags 正是這種）
            ▼
   Q3: 攻擊者能不能透過現有的 bug 原語（任意寫 / UAF / overflow）
       定位到這個欄位的位址？
       （全域變數：位址固定，只需 base；heap 物件裡的欄位：
        需要先用 Ch 19/20 的 spray/grooming 拿到正確的那一份）
            │
            ├─ 否 → target 再好也打不到，先解決定位問題
            ▼
   Q4: 改掉這個值之後，有沒有「觸發」的手段，
       讓 kernel 真的走到那條會用它做出特權動作的路徑？
       （execve 未知格式、crash、正常 syscall、下一次 context switch……）
            │
            └─ 這一步常被低估：target 找到了、寫進去了，
               但沒有觸發路徑，攻擊鏈就卡死在「寫完但沒生效」
```

這四個問題,對應回 [Ch 16](./16-classic-primitives.md) 的四個原語和本章的新增案例,可以逐一驗證:

| Target | Q1(有特權意義?) | Q2(有合法寫入路徑?) | Q3(能定位?) | Q4(有觸發路徑?) |
|---|---|---|---|---|
| `cred.uid` | 是,決定身份 | 是(`setuid` 的正常路徑) | 需 cred/task 位址 | 不需要,改完立即生效 |
| `modprobe_path` | 是,決定以 root 執行什麼 | 是(管理員可手動設) | 需 kernel base | execve 未知格式檔 |
| `pipe_buffer.flags` | 是,決定寫入語意 | 是(kernel 內部正常會設/清) | 需先拿到正確 pipe_buffer | 下一次對這個 pipe 的 write |
| PTE 權限位 | 是,決定記憶體存取權 | 是(mmap/mprotect 正常會寫) | 需先讓 page table page 被 UAF | 下一次對該虛擬位址的存取 |

Q2 這一列值得多說一句:**最好的 target 往往是 kernel 本身就有「正常情境下會寫這個值」的邏輯,攻擊者不需要發明一個全新的寫入手段,只需要讓現有的寫入邏輯在不該發生的時機被觸發**(Dirty Pipe 的本質正是如此)。這比起「我需要一個全新的任意寫原語才能碰到這個欄位」門檻低得多——如果 Q2 能回答「是,而且這個合法路徑本身就有 bug」,你手上的攻擊鏈可能根本不需要經過 Part 3 教的 heap 原語,直接就是一個邏輯漏洞([Ch 10](./10-vulnerability-taxonomy.md) 的 logic bug 分類)。

## 具體例子

### 例子一:用「換指標」法最小化 cred 攻擊的 leak 需求

延續 [Ch 16](./16-classic-primitives.md) 的場景——你有 write-what-where,且透過某個 heap leak 拿到了自己 process 的 `task_struct` 位址,但**沒有**拿到 kernel text base(例如 KASLR 的 leak 手段失效,或者你故意想測試「最小 leak 需求下能不能提權」)。

```c
/* 概念:只有 task_struct 位址,沒有 kernel text base */

/* 這時直接改 cred 的內容(uid=0)做不到——
   因為你還不知道 task_struct->cred 指向的那份 struct cred 的位址
   (那份 cred 通常是另一塊 kmalloc 出來的記憶體,位址不等於 task_struct 位址)。
   但如果你的 leak 原語能讀出 task_struct 裡 cred 指標的「當前值」,
   你其實連 kernel base 都不需要知道——
   因為你要寫的不是一個「相對 base 的固定位址」(那是 init_cred 需要的),
   是直接把這個已知的 cred 指標位置,改成某個你能構造出來的、
   內容全是高權限值的「假 cred」位址（例如你自己 spray 進某塊可控記憶體的內容）。*/

uint64_t fake_cred_region = spray_fake_cred(/* 內容:usage=1, uid=0, gid=0, ... 全部填好 */);
uint64_t cred_ptr_addr = task_struct_addr + OFF_CRED;   /* 讀出來的,已知 */
write64(cred_ptr_addr, fake_cred_region);
/* 這一步完全不需要 kernel base，只需要：
   1. task_struct 位址（已有）
   2. 一塊你能控制內容、且位址你知道的記憶體（heap spray 得到）
   3. 任意寫原語（已有）
   這是本章方法論 Q3 的一個真實案例：同一個 target（cred 指標），
   「定位」的門檻可以因為攻擊者手上原語的不同而完全不一樣。*/
```

這段**未實測,為理論預期行為**——它刻意留白「怎麼讓 kernel 接受一個指向 user 可控記憶體的 cred 指標」這個細節,因為某些 kernel 版本的 cred 使用路徑可能對 cred 指標所在的記憶體區域有額外假設(例如期待它落在 kernel 的某個合法範圍),实際是否可行需要對照你的目標版本原始碼核實,這裡只示範方法論上「降低 leak 需求」的思路本身是成立的。

### 例子二:Q4 被忽略的失敗案例

假設你找到一個方法把某個 driver 自己維護的「目前使用者身份」欄位(不是 `struct cred`,是某個 driver 自訂的 access-control 欄位)改成了高權限值,興高采烈以為大功告成,卻發現提權沒有生效。

```
   回放:
   1. 用任意寫把 driver 自訂的 struct my_dev { ... int priv_level; ... }
      的 priv_level 欄位從 0 改成 0xff（driver 原始碼裡 0xff 代表最高權限）。
   2. 再去呼叫這個 driver 的某個特權 ioctl，預期因為 priv_level 已經是 0xff 而通過檢查。
   3. 實際結果：ioctl 仍然被拒絕。
```

排查後發現:這個 driver 的特權檢查不是讀 `my_dev->priv_level`,而是每次 ioctl 進來時,**重新從 `current_cred()` 計算一次**,`priv_level` 欄位只是一個快取、在特定時機才會被重新同步,平常根本沒人讀它。這是方法論 Q1 沒有問清楚的後果——**這個欄位看起來「有特權意義」,但實際上它不是 kernel 真正拿去做判斷的那個值**,只是一個容易被誤認為 target 的旁支欄位。正確做法是先用 [Ch 2](./02-reading-kernel-source.md) 的方法追一遍「這個特權檢查實際讀的是哪個變數」,而不是看欄位名字猜。

## 對比與取捨

| Target 類型 | 定位門檻 | leak 需求 | 觸發方式 | 典型代表 |
|---|---|---|---|---|
| 全域變數(A 類) | 低(符號位址固定) | 需 kernel base(cred 法例外) | 固定且通用(execve/crash/poweroff) | modprobe_path、core_pattern |
| 直接改 cred 內容 | 中(需 cred 位址) | 需 cred/task 位址,不需 text base | 無,改完即生效 | uid/gid/capability |
| 換 cred 指標(指向 init_cred 或假 cred) | 低到中(視能否拿到可控記憶體位址) | 視手段,可完全不需 text base | 無,改完即生效 | init_cred、Dirty Cred 雛形 |
| heap 物件次要欄位(B 類) | 高(需先用 spray/UAF 定位到正確那一份) | 需 heap 相關位址,通常也需 text base | 視欄位而定(下一次正常操作) | pipe_buffer.flags/page |
| PTE | 最高(需先讓 page table page 被 UAF) | 需 physical/virtual 位址對應知識 | 下一次對該虛擬位址的存取 | Dirty Pagetable(Ch 23) |

讀這張表的方式:**門檻和威力大致成正比**。全域變數門檻最低,但威力也最受限(擋得住的 config 單一);PTE 門檻最高,但一旦打穿,你拿到的是整台機器的記憶體讀寫權,比任何單一子系統的特權都大。選哪一層,取決於你手上的原語已經把你帶到哪一步。

## 踩雷集錦

1. **以為「data-only」等於「隨便寫一個欄位就行」**——本章方法論 Q1 就是在戳破這個錯覺。不是每個看起來「特權相關」的欄位都是真 target(見例子二),你得先確認 kernel 實際讀的是不是這個欄位,而不是一個同名的快取或旁支值。
2. **只改 uid 忘了 capability**——某些檢查走 `capable()` 不走 uid,只改 `cred.uid` 可能讓 `id` 指令顯示 `uid=0` 卻仍然被某些 capability 檢查擋下。要嘛整份換成 `init_cred`,要嘛確認 capability 欄位也一起改。
3. **低估 Q4(觸發路徑)的重要性**——改對了值,但找不到讓 kernel 真的去用這個值做特權動作的路徑,等於什麼都沒發生。`pipe_buffer.flags` 這類 target 的觸發往往就是「下一次正常的 syscall」,但也有些 target(像某些只在特定子系統初始化時才讀一次的欄位)觸發窗口極窄甚至可能錯過。
4. **把 pipe_buffer 的 flags/page 和 ops 指標混為一談**——`ops` 是控制流 target(蓋函式指標,KCFI 底下風險大增),`flags`/`page` 是純資料 target(不碰任何函式指標,KCFI 完全不介入)。混著想會讓你在 KCFI 開啟的環境裡錯誤評估某個 pipe_buffer 攻擊到底還可不可行。
5. **以為「合法可寫」就代表「沒有任何保護」**——`HARDENED_USERCOPY`([Ch 26](./26-modern-mitigations.md))仍然會檢查 usercopy 的邊界;某些高度敏感的欄位(如部分 lockdown 相關設定)在 `CONFIG_SECURITY_LOCKDOWN_LSM` 開啟時會被禁止修改,即使你技術上有任意寫能力。Target 搜尋完成後,還要確認目標 kernel 的 LSM/lockdown 設定沒有額外擋住它。

## 進階:再往深一層

### 為什麼目前沒有通用的「資料完整性」mitigation

[Ch 26](./26-modern-mitigations.md) 的攻擊鏈圖已經點出:Privesc 階段幾乎沒有通用防禦。本章解釋了原因的結構性根源——要擋住 data-only,你需要的不是「型別檢查」(KCFI 做得到),是「語意檢查」:kernel 要能分辨「這次寫入,是不是這個 process 在這個時間點、透過這條合法呼叫鏈,應該被允許做的動作」。這個問題在學術上有對應的方向(例如對關鍵資料結構做 **data-flow integrity**、或用 hypervisor 層的內外隔離保護 `cred` 這類高價值物件不被 kernel 自己的 bug 寫壞),但這些方案普遍成本高(需要額外的硬體支援、或大幅改寫記憶體管理的信任邊界),**就我們查證的範圍,沒有任何一項已經進入 mainline 作為通用、預設開啟的 production mitigation**。ARM64 的 PAC(Pointer Authentication)對指標類欄位(例如用簽名保護某些函式指標或敏感指標)提供了部分方向,細節見 [Ch 46](./46-arm64-kernel-pwn.md),但它保護的是「指標被偷換」,不是「欄位的值被改成另一個合法但錯誤的值」(例如 uid 這種純數值欄位,PAC 保護不到)。

### target 搜尋是一個持續的攻防過程

把本章的 Q1–Q4 檢查清單想成一個通用工具,而不是一次性用完就丟。每個新版本的 kernel、每個新的子系統,都可能冒出新的候選 target——[Ch 19](./19-spray-objects.md) 結尾說過牌庫會隨 hardening 演進,target 搜尋也是同樣的道理:今天被你用 Q1–Q4 篩出來的一個欄位,明天可能被防守端發現太好用,加上額外檢查(就像 `modprobe_path` 催生了 `STATIC_USERMODEHELPER`)。讀新公開的 kernelCTF submission([Ch 49](./49-kernelctf-overview.md))時,值得特別留意作者是怎麼找到他們用的 target——那通常就是這套方法論在真實案例上的具體套用。

## 動手練習

> 以下在你自己的 QEMU 靶上操作,除標注「實測」外皆**未實測,為理論預期行為**。

1. 在 [Ch 16](./16-classic-primitives.md) 用過的教學版 write-what-where 靶上,改用「換 cred 指標指向 `init_cred`」法重做一次提權,比較它和逐欄位改 uid/gid 法在「需要幾次寫入」上的差異。
2. 寫一個小的 kernel module,自訂一個 `priv_level` 欄位但實際權限檢查改成讀 `current_cred()`,刻意重現本章例子二的失敗情境,親手走一次排查過程,確認你能用 [Ch 2](./02-reading-kernel-source.md) 的方法快速定位「真正被讀的是哪個變數」。
3. 針對一個你自己寫的、帶 `flags` 欄位且 `flags` 會影響後續寫入語意的教學 struct(模仿 `pipe_buffer` 的精神,簡化版即可),故意用 UAF/overflow 去改這個 flag,觀察「下一次正常操作」被影響的效果,體會 Q4(觸發路徑)為什麼重要。
4. 對你用過的任何一個子系統(driver/syscall path),套用本章 Q1–Q4 四個問題,嘗試找出至少一個本章和 [Ch 16](./16-classic-primitives.md) 都沒提到的候選 target,寫下你的分析(即使最後結論是「這個欄位其實打不到」,過程本身就是練習)。

## 本章重點整理

- data-only 成主流的結構性原因:KCFI 能機械化檢查「呼叫目標型別對不對」,但沒有機制能機械化檢查「這次資料寫入語意上合不合理」——後者需要語意層的理解,目前沒有通用 production mitigation 在做。
- Target 分兩類:全域變數(A 類,[Ch 16](./16-classic-primitives.md) 範圍)和 heap 物件內的次要欄位(B 類,本章深化),威力和定位門檻大致成正比。
- cred 攻擊可以更進一步:改 capability、或直接換指標指向 `init_cred`,比逐欄位改內容更省事、leak 需求更低。
- `pipe_buffer` 的 `flags`/`page` 是純資料 target 的經典案例,和蓋 `ops` 的控流攻擊是完全不同的攻擊面,KCFI 介入與否不同。
- 找 target 的通用方法論是四個問題:有沒有特權意義(Q1)、有沒有合法寫入路徑(Q2)、能不能定位(Q3)、有沒有觸發路徑(Q4)。四個都成立才是好 target。
- PTE(Dirty Pagetable,[Ch 23](./23-dirty-pagetable.md))是這套方法論推到極限的案例:門檻最高,威力也最大。

## 自我檢核

- [ ] 不看筆記,能解釋為什麼 KCFI 能擋控制流劫持卻擋不住 data-only,用「機械化檢查 vs 語意檢查」這個框架講一次
- [ ] 能說出改 cred 的三種做法(逐欄位改、換成 init_cred、換成自造假 cred)各自的 leak 需求差異
- [ ] 能用自己的話解釋 `pipe_buffer.flags` 為什麼是「純資料」target,和蓋 `ops` 的本質差異在哪
- [ ] 能背出 Q1–Q4 四個問題,並說出任何一題答否會導致整個攻擊鏈卡在哪一步
- [ ] 面試被問「現代 kernel 有沒有辦法擋 data-only 攻擊」,能在一分鐘內說清楚「目前沒有通用方案」以及為什麼這麼難做到嗎?
- [ ] 想一個你讀過的 driver 或子系統:它有沒有一個「看起來特權相關但其實是旁支快取」的欄位?你會怎麼驗證它是不是真 target?

## 延伸閱讀

### CVE / 一手技術報告

- **[CVE-2022-0847（Dirty Pipe）官方 advisory 與 Max Kellermann 的技術部落格](https://dirtypipe.cm4all.com/)**
  - **這篇說什麼**：作者本人完整拆解 `PIPE_BUF_FLAG_CAN_MERGE` 這個 flag 為什麼在 `copy_page_to_iter_pipe()` 路徑沒被正確清除,以及這個單一 bit 錯誤如何變成可寫任意 page cache 檔案的漏洞。
  - **讀哪裡**：整篇,特別是作者畫的 pipe buffer 狀態圖與 "The Linux kernel page cache" 一節。
  - **和本章的關聯**：本章「pipe_buffer:flags 與 page 欄位」一節的直接案例;前置知識是本書 [Ch 19](./19-spray-objects.md) 對 `pipe_buffer` 結構的介紹。

### 官方文件 / 原始碼

- **Linux 原始碼 `include/linux/cred.h`、`kernel/cred.c`**（[Bootlin Elixir](https://elixir.bootlin.com/linux/v6.6/source/kernel/cred.c)）
  - **讀哪裡**：`struct cred` 的完整欄位定義;`commit_creds()`、`override_creds()` 怎麼處理 cred 指標的替換,理解為什麼「換指標」比「改內容」在某些情境下更安全(引用計數的處理方式不同)。
  - **和本章的關聯**：本章「系統化 cred 攻擊」一節的依據;前提知識 [Ch 12](./12-control-flow-hijack.md) 對 `commit_creds` 的介紹。
- **Linux 原始碼 `include/linux/pipe_fs_i.h`**（[Bootlin Elixir](https://elixir.bootlin.com/linux/v6.6/source/include/linux/pipe_fs_i.h)）
  - **讀哪裡**：`struct pipe_buffer` 的定義與各 flag 的用途註解。
  - **和本章的關聯**：本章 pipe_buffer 一節的欄位來源;前提知識 [Ch 19](./19-spray-objects.md)。

### 部落格 / 延伸方向

- **[Project Zero blog（Linux kernel 系列）](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：多篇針對真實 kernel 漏洞的完整利用鏈拆解,其中不少近年案例的收尾階段用的正是本章方法論找出的次要欄位,而非傳統控制流劫持。
  - **讀哪裡**：搜尋站內與 "cred" 或 "data-only" 相關的文章標題。
  - **為什麼值得讀**：每篇都是教科書等級的 root cause + exploit writeup,能看到真實攻擊者怎麼在一個全新的子系統裡,用本章的四個問題找出他們自己的 target。

data-only 這條路線走到這裡,我們已經有了找 target 的通用方法、也看過 cred 和 pipe_buffer 兩個深度案例,Dirty Pagetable([Ch 23](./23-dirty-pagetable.md))把它推到頂點。接下來 Part 5 把視角拉開,離開「現代 mitigation 怎麼繞」這個主題,轉去看 kernelCTF 近年最大的礦區——netfilter / nf_tables 這個子系統本身的攻擊面有多大。

→ [Ch 30 netfilter / nf_tables：kernelCTF 最大礦區](./30-netfilter-nftables.md)
