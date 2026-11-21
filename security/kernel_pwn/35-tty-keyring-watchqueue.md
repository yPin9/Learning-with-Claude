# Ch 35 — TTY / keyring / watch_queue / pipe：經典 spray 與原語來源

> **目標**：從「子系統」的角度重新認識四個你在 [Ch 19](./19-spray-objects.md)、[Ch 21](./21-heap-to-rip.md) 已經當工具用過的物件——`tty_struct`、`struct key`/`user_key_payload`、`watch_queue`、`pipe_buffer`。這一章不重複「怎麼拿它們當 spray 牌」，而是回答一個更根本的問題：**為什麼這幾個子系統反覆成為 kernel exploit 的零件庫**——它們的物件模型有什麼共同特徵，讓它們一代又一代被拿來用；並透過 CVE-2016-0728（keyring）、CVE-2022-0995（watch_queue）、CVE-2022-0847（Dirty Pipe）三個案例看清楚它們自己也會出什麼樣的 bug。

> **環境**：Linux 6.6 LTS、x86-64。`struct key`/`watch_queue` 的欄位已用 WebSearch 查證官方原始碼（GitHub `torvalds/linux` v6.6 tag 的 raw 內容）；`pipe_buffer` 的欄位與 `PIPE_BUF_FLAG_*` 清單同樣查證過 v6.6 原文。`watch_queue_set_filter()` 的行為描述直接讀自 `kernel/watch_queue.c` 原文。CVE 的 root cause 參考 Red Hat Bugzilla、Project Zero RCA 系列、vulners 彙整；查不到一手來源的細節會明確標注。未實測處會標注。

## 為什麼要單獨講這四個子系統

[Ch 19](./19-spray-objects.md) 把 `tty_struct`、`user_key_payload`、`pipe_buffer` 列進牌庫表，教你「這張牌 size 多少、落哪個 cache、能當什麼角色」。[Ch 21](./21-heap-to-rip.md) 教你怎麼用 `tty_struct`/`seq_operations`/`pipe_buffer` 把一個 heap 原語升級成控流。這些都是**用法**層面的知識。

這一章要往回退一步，問一個更結構性的問題：**為什麼偏偏是這幾個子系統？** kernel 裡有成千上萬種可以配置的物件，為什麼反覆被拿出來用的,幾乎總是這四個（加上 [Ch 33](./33-socket-skb.md) 的 skb、[Ch 19](./19-spray-objects.md) 的 msg_msg、`seq_operations`）?答案不是巧合，而是這些子系統在設計上共享幾個特徵：

```
「好用的零件」的共同特徵
────────────────────
1. 一般 unprivileged user 就能配置（不需要 CAP_* 或 root）
2. 配置成本低、數量不受嚴格限制（能 spray 到幾百上千個）
3. 物件裡有「函式指標表」或「大段可控內容」
4. 物件的生命週期由 user 直接控制（open/close、add_key/keyctl 的操作）
5. 歷史夠久，文件與逆向資料齊全，exploit 寫作門檻低
```

TTY、keyring、pipe、watch_queue 四個全部符合前三條；第四條則讓它們的生命週期操控（UAF 的「free」那一步）比起某些需要特殊觸發條件的物件容易得多。這一章逐一拆解它們的物件模型，然後看它們自己的 bug 歷史——會發現**同一組「好用」的特質，也正是它們自己容易出 bug 的原因**：函式指標表密集的結構,一旦有欄位計算錯,殺傷力就特別大。

## 先建立直覺

把這四個子系統想成「kernel 裡的公用文具櫃」：鉛筆（pipe）、資料夾（keyring）、電話轉接表（tty）、通知看板（watch_queue）。任何一個 process 都能從這個文具櫃拿東西、填自己要的內容、擺在自己喜歡的位置——這正是它們被用來「佔位」「填假資料」的原因。但文具櫃的設計者沒想到的是：**有人會把拿到的資料夾頁碼改成別人的資料夾、或是把電話轉接表的某一條線剪斷接到別的地方**——這就是子系統自己的 bug。

```
文具櫃類比                        對應的 kernel 機制
──────────                        ──────────────────
鉛筆（內容你填）                   pipe_buffer：page + offset/len，內容你寫什麼就是什麼
資料夾（有編號、可借可還）          key/keyring：引用計數管理生命週期
電話轉接表（打某個分機轉到某函式） tty_struct->ops：函式指標表，操作走到哪個函式看 ops
通知看板（訂閱者名單 + 訊息佇列）   watch_queue：filter bitmap + 通知事件佇列
```

> 如果你還沒讀過 [Ch 19](./19-spray-objects.md) 的牌庫總表，建議先回去看一眼這四張牌在表裡的位置，再接著讀這一章的子系統角度。

## TTY 子系統：pty 配置路徑與 line discipline

[Ch 19](./19-spray-objects.md)/[Ch 21](./21-heap-to-rip.md) 已經用過 `tty_struct` 當 leak 來源與控流 victim；這裡補上「它從哪裡來、怎麼配置、為什麼固定走這條路徑」的子系統視角。

### pty 配置路徑

```
open("/dev/ptmx")
   │
   ▼
ptmx_open()                             ← tty 子系統的入口
   │
   ├─ alloc_tty_struct()                ← 配置 struct tty_struct（落 kmalloc）
   │     初始化 tty->ops = &pty_unix98_ops（master 端）
   │
   ├─ tty_ldisc_setup()                 ← 掛上預設的 line discipline（N_TTY）
   │     tty->ldisc 指向 struct tty_ldisc（含 tty_ldisc_ops 函式指標表）
   │
   └─ devpts_pty_new()                  ← 在 devpts 檔案系統裡配一個 slave 端節點
         對應 /dev/pts/N，user 可以再 open() 這個 slave 端
```

`open("/dev/ptmx")` 一次呼叫,換來**一對** tty_struct（master + slave），各自落在自己的 cache slot,各自有自己的 `ops` 指標。這解釋了為什麼 spray `tty_struct` 的成本是「一次 open 拿兩份物件」——某些 exploit 寫作上會特別區分要蓋的是 master 還是 slave 的 `ops`,因為兩者指向不同的 `tty_operations` 實例（`ptm_unix98_ops` vs `pty_unix98_ops`，就我所知，請以 `drivers/tty/pty.c` 為準）。

### line discipline：另一層函式指標表

`tty_struct->ldisc` 指向一個 `tty_ldisc`，裡面的 `tty_ldisc_ops` 又是一組函式指標（`open`/`close`/`read`/`write`/`ioctl` 等）。這代表**一個 tty 物件身上至少有兩層可以當控流目標的函式指標表**：`tty_operations`（裝置層）和 `tty_ldisc_ops`（line discipline 層）。歷史上的 tty exploit 多數打 `tty_operations`（[Ch 21](./21-heap-to-rip.md) 的做法），但這不是唯一選擇——ldisc 層的函式指標表是另一個理論上可行、但實務上較少被用到的目標,原因是 ldisc 切換（`TIOCSETD` ioctl）需要額外一步操作,複雜度比直接蓋 `ops` 高。

### 為什麼 pty 這麼好用：三個子系統特徵的具體體現

- **unprivileged**：任何一般 user 都能 `open("/dev/ptmx")`,不需要任何 capability。
- **數量不受嚴格限制**：受 `pty.max`（sysctl，預設值隨發行版不同）限制,但預設值通常足夠 spray 到數百個。
- **size 固定但夠大**：`tty_struct` size 固定（約 0x2b0+ bytes，隨 config 變動），落在 kmalloc-1k 等級,是「大 cache 題」的首選victim,這點 [Ch 19](./19-spray-objects.md) 已經講過,這裡不重複。

### 從子系統角度看「spray 一批 pty」實際發生了什麼

```c
/* 概念骨架：開 N 對 pty，留住 master fd（slave 不開也沒關係，master 端已經配好 tty_struct） */
int spray_pty(int n, int *master_fds) {
    for (int i = 0; i < n; i++) {
        master_fds[i] = open("/dev/ptmx", O_RDWR | O_NOCTTY);
        /* 到這裡，kernel 已經：
         *   1. alloc_tty_struct() 配置 master 端的 tty_struct
         *   2. tty_ldisc_setup() 掛上 N_TTY line discipline
         *   3. devpts_pty_new() 在 devpts 配一個對應的 slave inode
         * 你手上只拿到 master fd，但三個物件都已經存在於 kernel 記憶體裡 */
    }
    return 0;
}
```

這段程式碼本身平凡,值得注意的是**它一次 `open()` 换到的物件數量比表面上看到的多**:你只拿到一個 fd,但 kernel 側配置了 `tty_struct`（master）+ `tty_ldisc` + devpts inode 的一整組關聯物件。這也是為什麼某些 exploit 寫作會選擇**只開 master、不開對應的 slave**:省下一次額外的 `open()`,但仍然把 master 端的 `tty_struct` 配置出來、佔住 heap 上的位置。

## Keyring 子系統：key、payload 與引用計數

### 物件模型

```
process
   │
   ├─ thread keyring ──┐
   ├─ process keyring ─┼──▶ session keyring ──▶ user keyring ──▶ user session keyring
   └─ (由 add_key/keyctl 操作)

每個 keyring 本身也是一個 struct key（keyring 是「能裝其他 key 的 key」）
```

這張樹的每一層代表不同的生命週期範圍：thread keyring 跟著一個 thread 存在，process keyring 跟著整個 process（所有 thread 共用），session keyring 原則上跟著一次登入 session，user keyring/user session keyring 則跨越同一個 uid 的所有 process。`KEYCTL_JOIN_SESSION_KEYRING` 動的正是「session keyring」這一層——這也是為什麼 CVE-2016-0728 的觸發路徑要操作的是 session 層，而不是更細粒度的 thread/process keyring：session keyring 的生命週期管理牽涉「要不要共用現有的、還是建一個新的」這個分支判斷，正是漏引用計數配對的那個分支。

核心結構 `struct key`（`include/linux/key.h`，WebSearch 查證 v6.6 原文）的關鍵欄位：

- **`usage`**（`refcount_t`）：引用計數,`key_get()`/`key_put()` 操作。
- **`serial`**：user 用來指名這個 key 的數字 ID（`add_key()` 的回傳值）。
- **`perm`**：權限 bitmask（view/read/write/search/link/setattr，依 possessor/user/group/other 分別設定）。
- **`payload`**：對一般 key 是實際資料（union,和 keyring 用的 `keys`/`name_link` 共用同一塊記憶體,因為 keyring 本身不需要 payload)。

### keyctl / add_key 操作面速覽

user space 透過兩個 syscall 操作整個子系統：`add_key()`（建立/更新 key）、`keyctl()`（一組子命令：read/update/revoke/link/unlink/join session keyring 等等）。這兩個 syscall **不要求任何 capability**——這是 keyring 子系統最重要的特徵之一,也是它反覆被當作攻擊面與工具箱的原因:一般 user 就能大量建立、連結、操作 key。

`keyctl()` 的子命令涵蓋了整套 key 的生命週期管理,下表列出和本章利用思路最相關的幾個：

| 子命令 | 作用 | 攻擊面相關性 |
|---|---|---|
| `KEYCTL_JOIN_SESSION_KEYRING` | 加入或建立一個指名的 session keyring | CVE-2016-0728 的觸發路徑 |
| `KEYCTL_READ` | 讀出一個 key 的 payload | 可以用來讀 leak（如果 UAF 讓這個 key 的 payload 指標被改成別處） |
| `KEYCTL_UPDATE` | 更新一個 key 的 payload 內容 | 可控載體的「填內容」手段 |
| `KEYCTL_LINK` / `KEYCTL_UNLINK` | 在 keyring 樹上建立/移除連結 | 操作 keyring 樹結構本身,historically 也是另一類 bug 的來源（連結環、重複釋放） |
| `KEYCTL_INVALIDATE` / `KEYCTL_REVOKE` | 讓一個 key 失效 | 和生命週期管理 bug 密切相關,常是 UAF 利用鏈裡「主動觸發 free」的手段 |

把這張表和 [Ch 19](./19-spray-objects.md) 的牌庫表對照：`add_key()` 負責「配置可控內容」，`KEYCTL_UPDATE` 負責「事後改內容」，`KEYCTL_INVALIDATE`/`KEYCTL_UNLINK` 負責「主動釋放」。一個完整的 UAF 利用鏈,往往就是從這幾個操作裡挑出對應的那一個。

對 `"user"` 類型的 key,payload 是 `struct user_key_payload`（定義在 `include/keys/user-type.h`,就我所知這個標頭沒有被包含在我們查證的 `key.h` 裡,欄位結構為 `rcu_head` + `datalen` + 彈性陣列 `data[]`，請以你的版本核對)。這正是 [Ch 19](./19-spray-objects.md) 把它列為「可控載體」的理由:`add_key("user", desc, payload, plen, ...)` 會配一塊 `header + plen` 大小的記憶體,`data[]` 整段內容由你決定。

### CVE-2016-0728：`join_session_keyring()` 的引用計數洩漏

這是 keyring 子系統最經典的一個 CVE（Perception Point 於 2016 年發表分析，Red Hat Bugzilla 1297475 收錄）。

root cause：`join_session_keyring()` 讓一個 process 把自己目前的 session keyring 換成某個指名的 keyring。實作上,它會先對目標 keyring 呼叫 `key_get()`（引用計數加一),然後才檢查「這個目標 keyring 是否就是我目前已經在用的 session keyring」。**如果答案是「是」（也就是這次呼叫其實什麼都不用換），正常路徑應該要把剛才多拿的那個引用計數還回去,但這個 early-return 的分支漏了這一步。**

```c
/* 偽碼，還原 Perception Point 分析描述的邏輯 */
long join_session_keyring(const char *name) {
    struct key *keyring = find_keyring_by_name(name, false);
    if (keyring == current->session_keyring) {
        /* 漏了：此時 keyring 已經被 key_get() 多加了一次 refcount，
           這個分支直接 return，沒有 key_put() 把它放回去 */
        return current->session_keyring->serial;
    }
    /* 真正換 session keyring 的邏輯（這條路徑有正確處理引用計數） */
    ...
}
```

於是：**每呼叫一次「把 session keyring 換成自己當前的 session keyring」，引用計數就淨增一次,而不是維持不變**。`usage` 是 32-bit 的欄位（refcount），攻擊者只要重複呼叫夠多次（Red Hat bug thread 描述的 PoC 每輪增加 2，需要約 2^31 次迭代,Perception Point 的敘述則以 2^32 為量級),就能讓這個計數**整數溢位繞回 0**。

```
usage 計數變化（概念示意，不是精確數字）
  正常：   create → usage=1 → key_put → usage=0 → 回收
  bug：    create(usage=1)
           反覆 join_session_keyring(同一個 keyring)
             每次都 key_get()（+1），漏 key_put()（該-1但沒做）
             usage 持續爬升：1 → 2 → 3 → ... → 2^32-1 → 0（溢位繞回）
           此時 process 手上還握著對這個 keyring 的參照
           但 usage=0 讓 GC 把它當成「沒人用了」回收
           → process 繼續用一個已經被回收的 keyring → use-after-free
```

修補在 Linux 4.4.1 釋出：讓這個 early-return 分支正確呼叫 `key_put()`。這個案例教的東西超出 keyring 本身：**「引用計數的每一個分支都要配對（get 必有對應的 put）」是最基本但最容易漏的規則**，[Ch 10](./10-vulnerability-taxonomy.md) 的 refcount 類 bug 分類,這就是教科書等級的例子。

## watch_queue：通知機制與它借用的 pipe 基礎設施

### 物件模型

`watch_queue` 是相對新的機制（5.8 引入，WebSearch 查過的來源一致），用途是讓 kernel 子系統（keyring 的變更通知、`fsnotify`/`fanotify` 類事件)能把「發生了什麼事」送給 user space,而不需要 user space 不斷 poll。它的物件設計很有意思：**它不是自己發明一套新的佇列,而是直接架在 pipe 的 ring buffer 上**。

```
pipe2(fds, O_NOTIFICATION_PIPE)         ← 建一個「通知管道」，本質上還是 pipe
   │
   ▼
pipe_inode_info（多一個 watch_queue 欄位，CONFIG_WATCH_QUEUE 開啟時才存在）
   │
   ▼
ioctl(fds[0], IOC_WATCH_QUEUE_SET_SIZE, depth)   ← 預先配置 pipe buffer，depth 在 1~512 之間
ioctl(fds[0], IOC_WATCH_QUEUE_SET_FILTER, &filter)  ← 設定要接收哪些事件類型
```

WebSearch 查證（讀 v6.6 `kernel/watch_queue.c` 原文）：**`watch_queue_set_size()` 和 `watch_queue_set_filter()` 都沒有 `capable(CAP_SYS_ADMIN)` 這類能力檢查**——`watch_queue_set_size()` 唯一的限制是一般 pipe buffer 的資源配額（`too_many_pipe_buffers_hard/soft()`,搭配 `pipe_is_unprivileged_user()`,超過配額回傳 `-EPERM`,這是資源限制不是權限檢查）。換句話說:**一般 unprivileged user 就能完整操作 watch_queue**,這也是它會被歸進本章「零件庫」而不是「需要權限才摸得到的子系統」的原因。

### CVE-2022-0995：`watch_queue_set_filter()` 的兩個 OOB write

這是 watch_queue 自己出的 bug（Red Hat Bugzilla 2063786 收錄，影響 5.8 之後引入 watch_queue 的版本）。

`watch_queue_set_filter()` 的工作流程（讀原文確認）：user 傳入一個 filter 規格,裡面有 `nr_filters`（限制在 1~16）和一組 filter entry 陣列。函式用 `memdup_user()` 把這組陣列整個複製進 kernel,然後遍歷,只保留 `type` 小於 `WATCH_TYPE__NR` 的 entry,用 `struct_size()` 依「保留下來的數量」配置一個新的 `watch_filter`,把保留的 entry 複製進去,同時在一個 `type_filter` bitmap 上標記對應的 bit。

CVE 的描述指出這裡有**兩個獨立的 OOB write**：一個在對 `type_filter` bitmap 做 bit-set 操作時（類型 bitmap 的索引計算有問題）,一個在把 filter entry 複製進新配置的陣列時（陣列大小和實際寫入的數量算得不一致）。兩者都符合[Ch 10](./10-vulnerability-taxonomy.md)「長度/索引計算錯誤 → OOB write」的典型形狀,只是具體哪個欄位算錯,WebSearch 查到的公開摘要沒有展開到逐行程式碼的程度——**這段屬於我們沒有查到一手 commit diff、只能描述模式的情況，請以你自己 `git show` 修補 commit `93ce93587d36` 核對細節**。

這個案例的意義:**watch_queue 架在 pipe 基礎設施上這個設計本身沒有錯,但它自己新增的 filter/bitmap 邏輯,一樣可能犯 Ch 10 那套老問題**。新機制不代表新的 bug 類型,通常只是老問題（長度計算、索引計算）換了一個新地方發生。

把這個模式還原成通用的偽碼,可以看出它和 [Ch 33](./33-socket-skb.md) 講的 skb 指標運算錯誤、[Ch 34](./34-filesystem-attack-surface.md) 講的 fs 欄位驗證,其實是同一種形狀的變體：

```c
/* 偽碼，說明 watch_queue_set_filter 一類 bug 的通用模式；不是任何版本的原文 */
struct watch_notification_filter spec;
copy_from_user(&spec, user_ptr, sizeof(spec));     /* nr_filters 來自這裡，攻擊者控制 */

unsigned int nr_kept = 0;
for (i = 0; i < spec.nr_filters; i++) {
    if (entries[i].type < WATCH_TYPE__NR) {
        nr_kept++;                                   /* 計算「真的要保留」的數量 */
    }
}

wf = kzalloc(struct_size(wf, filters, nr_kept), ...); /* 依 nr_kept 配置 */

for (i = 0; i < spec.nr_filters; i++) {               /* 【漏洞點：這裡該用 nr_kept 相關的
                                                           索引，還是繼續用 i？兩者對不上
                                                           就是陣列或 bitmap 的 OOB write】 */
    if (entries[i].type < WATCH_TYPE__NR) {
        wf->filters[j++] = entries[i];               /* 寫入陣列：index 要是遞增的 j，不是 i */
        set_bit(entries[i].type, wf->type_filter);    /* bitmap：index 要落在 bitmap 容量內 */
    }
}
```

這類「配置時用一個數量,寫入時用另一個索引來源」的不一致,是陣列/bitmap 類 OOB 最常見的根本原因之一——不是因為沒有任何邊界檢查,而是**檢查用的數字和實際寫入用的數字,來自兩個沒有被強制同步的計算**。

## pipe：`pipe_buffer` 與 Dirty Pipe 的 root cause

### 物件模型回顧

`struct pipe_buffer`（WebSearch 查證 v6.6 `include/linux/pipe_fs_i.h` 原文）：

```c
struct pipe_buffer {
    struct page *page;    /* 資料所在的實體頁 */
    unsigned int offset;  /* 資料在這個 page 裡的起始位置 */
    unsigned int len;     /* 資料長度 */
    const struct pipe_buf_operations *ops;  /* 函式指標表：confirm/release/try_steal/get */
    unsigned int flags;   /* 狀態旗標，見下 */
    unsigned long private;
};
```

`flags` 可用的旗標（v6.6 原文）：`PIPE_BUF_FLAG_LRU`（0x01）、`ATOMIC`（0x02）、`GIFT`（0x04）、`PACKET`（0x08）、**`CAN_MERGE`（0x10）**、`WHOLE`（0x20）、`LOSS`（0x40，僅 `CONFIG_WATCH_QUEUE` 啟用時存在——這也解釋了為什麼 pipe 和 watch_queue 在本章放在一起講：**它們在 6.6 的原始碼裡字面上共用著同一個結構的 flag 空間**）。

### CVE-2022-0847（Dirty Pipe）：一個沒被初始化的旗標

root cause（Red Hat 公告、Max Kellermann 的原始分析一致）：`PIPE_BUF_FLAG_CAN_MERGE` 這個旗標用來標記「這個 `pipe_buffer` 的內容還可以被後續的 write 接著合併進同一個 page,不需要另外配一個新 page」。問題出在**兩個函式（`copy_page_to_iter_pipe()` 與 `push_pipe()`）在建立新的 `pipe_buffer` 時沒有正確初始化 `flags` 欄位**，於是這個欄位可能**沿用同一塊記憶體上次被當成 `pipe_buffer` 使用時殘留的舊值**——如果上次恰好設過 `CAN_MERGE`,這次就會「莫名其妙」帶著這個旗標。

```
觸發流程（概念骨架）：
  1. 建一個 pipe，寫入資料把它填滿 → 每個 pipe_buffer 的 flags 正常帶 CAN_MERGE
  2. 用 read() 把資料全部讀光，pipe 清空（但底層的 pipe_buffer 結構記憶體沒有被清零）
  3. splice() 把某個「唯讀檔案的 page cache page」直接接到這個 pipe 裡
     → 這個新塞進來的 pipe_buffer 本該是全新初始化、不該帶 CAN_MERGE
     → 但因為 push_pipe()/copy_page_to_iter_pipe() 沒清 flags，殘留的 CAN_MERGE 還在
  4. write() 這個 pipe：kernel 檢查到 flags 帶 CAN_MERGE，判斷「可以直接合併寫進現有的 page」
     → 寫入動作直接作用在 splice 進來的那個 page cache page 上
     → 而這個 page 背後是一個唯讀檔案！write 權限檢查被繞過
```

這個 bug 之所以威力驚人,不是因為它複雜——恰恰相反,它的成因是**一個 bool 性質的旗標忘了初始化**,邏輯本身沒有超出一般程式設計錯誤的範疇。CVE-2022-0847 被稱為「過去七年最嚴重的 Linux 本地提權之一」的原因,正是它幾乎不需要任何堆布局操作、不需要繞過任何現代 mitigation,從 CVE 公開到可用的 PoC 只花了極短時間。修補方式(各來源一致描述)是確保 `pipe_buffer` 建立時 `flags` 一定被正確初始化,移除「可以意外繼承舊旗標」的可能性。

> 查證備註：`PIPE_BUF_FLAG_CAN_MERGE` 本身是 2020 年 5 月的 commit `f6dd975583bd` 引入（WebSearch 查到的多個來源一致），CVE-2022-0847 影響範圍是 5.8 之後；Red Hat 說明 RHEL 8 的核心版本因為沒有走到會觸發這個 merge flag 攻擊路徑的程式碼,所以不受已知 PoC 影響。

## 為什麼這些子系統反覆成為「零件庫」：收斂一下

把前面四個子系統的物件模型放在一起看，可以歸納出一個清楚的模式：

| 子系統 | unprivileged 可配置 | 函式指標表 | 可控內容 | 生命週期 user 可控 |
|---|---|---|---|---|
| tty (pty) | 是 | `tty_operations`、`tty_ldisc_ops` | 低（結構固定） | 是（open/close） |
| keyring | 是 | 無（純資料） | 高（`user_key_payload`） | 是（add_key/keyctl） |
| watch_queue | 是（無 capability 檢查） | 無（但有 bitmap/陣列索引邏輯） | 中（filter 規格） | 是（pipe2/ioctl/close） |
| pipe | 是 | `pipe_buf_operations` | 高（寫入的資料） | 是（pipe/write/close） |

四者的共同點（再強調一次本章的核心論點）：**配置門檻低 + 生命週期操之在己**,這兩條加起來,讓它們同時適合當 spray 牌（[Ch 19](./19-spray-objects.md)）、適合當 UAF 利用鏈裡「free 之後馬上佔位」的候選（[Ch 18](./18-uaf-double-free.md)）、也適合當控流 victim（[Ch 21](./21-heap-to-rip.md)）。而它們「有函式指標表」或「有索引/長度運算」這個特質,又讓它們自己持續出現 Ch 10 教過的那幾種老問題——引用計數配對錯誤（CVE-2016-0728）、長度/索引計算錯誤（CVE-2022-0995）、欄位未初始化（CVE-2022-0847）。

## 利用思路（概念層級）

1. **keyring refcount 類 bug 的通用升級路徑**：任何「某個分支漏了 `key_put()`/`kfree()` 對應的釋放」的 bug,升級路徑都走 [Ch 18](./18-uaf-double-free.md) 的標準流程:先確認能不能穩定重現 refcount 偏移、再決定要花多久把計數「撞」到溢位或撞到 0,最後銜接 spray 搶佔被釋放的記憶體。
2. **watch_queue 的 bitmap/陣列 OOB，思考它能寫到哪**：filter 的 bitmap 和 entry 陣列如果落在一般 kmalloc cache,OOB write 的落點就看它們緊接著的下一個物件是什麼——這又回到 [Ch 20 — heap grooming](./20-heap-grooming.md) 的布局問題:你想讓 OOB 寫到的「下一個物件」,最好是你自己 spray 進去、而不是隨機的系統雜訊。
3. **Dirty Pipe 類「旗標殘留」bug 的思路延伸**：Dirty Pipe 本身不是 heap corruption,而是一個邏輯 bug（繞過寫入權限檢查）,屬於 [Ch 10](./10-vulnerability-taxonomy.md) 分類裡的「logic bug」而非「memory corruption」。它提醒我們：**檢查 kernel 物件有沒有被正確初始化,是和檢查邊界一樣重要的審計項目**。這個思路也連到 [Ch 29 — data-only attack](./29-data-only-attack.md)：不動函式指標、只動資料欄位（這裡是一個 flag bit）就能拿到強力原語,是 data-only 攻擊哲學的早期範例。

## 對比取捨表

| 子系統 | 最佳角色 | 歷史 bug 類型 | 近年 hardening 動向 |
|---|---|---|---|
| TTY (pty) | leak + 控流 victim | UAF（生命週期管理） | `tty_struct` 的 cache 分配策略持續被檢視，random kmalloc caches 影響它的可預測性 |
| keyring | 可控載體 + leak | refcount 溢位 → UAF | `usage` 欄位型別與邊界檢查後續加強（就我所知） |
| watch_queue | 控流（OOB write 落點） | 長度/索引計算錯誤 | 相對新,持續被 fuzz,bug 密度目前仍偏高 |
| pipe | 控流 + 邏輯繞過（Dirty Pipe） | 欄位未初始化、ops 覆寫 | `flags` 初始化已修;`pipe_buffer` 持續是 heap 利用熱門目標 |

## 踩雷集錦

1. **「新機制比舊機制安全」**：watch_queue 是 2020 年才引入的新東西,照樣在兩年內出了 OOB write CVE。新機制通常代表「被 fuzz 的時間短」,不代表「設計得更安全」。
2. **「引用計數 bug 一定很難觸發」**：CVE-2016-0728 的觸發條件是重複呼叫一個完全合法的 syscall(`join_session_keyring` 對著自己當前的 keyring),不依賴任何特殊權限或競態條件,決定成敗的是迴圈次數。邏輯 bug 常常比 race condition 更容易穩定重現。
3. **「Dirty Pipe 需要複雜的堆布局」**：完全不需要。這是本章最值得記住的反例:它是一個單純的「欄位忘了初始化」,不涉及任何 slab 碎片、grooming、競態。威力和複雜度不是正相關。
4. **「watch_queue 需要 CAP_SYS_ADMIN 才能用」**：錯,我們已經讀原始碼確認 `watch_queue_set_size`/`watch_queue_set_filter` 都沒有 capability 檢查,只有 pipe buffer 的一般資源配額限制。這點容易被「聽起來像系統管理功能」的名字誤導。
5. **「tty_struct 的 master/slave 是同一個物件的兩個視角」**：不是,`open("/dev/ptmx")` 建立的是**兩個獨立的 `tty_struct`**,各自有自己的 `ops` 指標,指向不同的 `tty_operations` 實例。搞錯這點會讓你蓋錯 victim。
6. **「pipe_buffer 的 flags 現在已經安全了,不用再管」**：Dirty Pipe 這一個 flag 的問題修了,不代表「物件重複使用時殘留舊欄位值」這整類問題消失。任何你能讓它重複使用底層記憶體、又沒有正確清零的結構,都值得用同樣的角度檢視。
7. **「watch_queue 一定要搭配 keyring 或 fsnotify 才能用」**：不是,`pipe2(fds, O_NOTIFICATION_PIPE)` 本身就能單獨建立一個通知管道,不需要先有任何訂閱來源存在。CVE-2022-0995 的 PoC 建立管道並呼叫 filter 相關 ioctl 即可觸發，不依賴真的發生任何通知事件。

## 進階：再往深一層

- **這些物件的 hardening 走向**：random kmalloc caches（[Ch 27](./27-random-kmalloc-caches.md)）讓「我 spray 的 `tty_struct` 和 victim 是不是真的同一個 cache」變成機率問題,削弱了這幾張老牌的穩定性。持續找新的、還沒被重點防禦的物件,是攻防拉鋸的常態——這點 [Ch 19](./19-spray-objects.md) 的「進階」小節已經提過,這裡從子系統設計者的視角再強調一次。
- **watch_queue 和 fsnotify/fanotify 的關係**：watch_queue 原本的設計動機之一是取代 `fsnotify` 類機制裡效能較差的部分;如果你要深入,下一步是讀 `fs/notify/` 底下的程式碼,看看通知類機制普遍共享哪些設計模式(佇列 + filter + 可丟棄的事件)。
- **keyring 與 LSM 的互動**：keyring 的 `perm` 欄位與 SELinux/AppArmor 這類 LSM 有互動(LSM 可以在 key 操作上加一層額外檢查)。[Ch 38](./38-android-vendor-subsystems.md) 會講 Android 的 SELinux 如何限制 untrusted_app domain 能摸到的子系統清單,keyring 是其中一個會被收緊的對象。
- **從防禦角度重新看這四個子系統**：如果你是藍隊,這一章該讀出的結論是「這四個子系統的 unprivileged 可達性本身就是風險的一部分」。`seccomp` 過濾特定 ioctl/syscall(例如限制 `IOC_WATCH_QUEUE_*`、限制 `/dev/ptmx` 的存取)是其中一種縱深;容器化環境裡限制 `CAP_*` 與 namespace 隔離是另一層,詳見 [Ch 51 — 防禦與偵測視角](./51-defense-detection.md)。
- **這四個子系統在 kernelCTF submission 裡的實際出現頻率**：讀 kernelCTF repo 的 writeup 會發現,`tty_struct`/`pipe_buffer` 幾乎是每一代 mitigation 繞法都還會出現的老面孔,而 `watch_queue`/keyring 相關的新 CVE 出現頻率較低但每次出現都受到高度關注——這和「新機制 bug 密度高但數量少、老機制 bug 密度低但樣本多」這個一般規律一致，可以用來估計你自己找新 bug 時該往哪個方向花時間。

## 動手練習

> 以下在**你自己擁有的 Linux VM**（Ch 0 的 QEMU 環境）內進行，以下描述**未實測**，為理論預期行為。

1. 寫一個程式反覆呼叫 `keyctl(KEYCTL_JOIN_SESSION_KEYRING, name)`（`name` 固定為你目前的 session keyring 名稱），搭配 `ftrace` 觀察 `join_session_keyring()` 的呼叫次數與耐心等待（這個 PoC 理論上需要天文數字的迭代次數才會真的溢位，不建議真的跑到底,重點是確認呼叫路徑與觀察 refcount 的變化趨勢）。
2. 用 `pahole -C key vmlinux` 印出你 kernel 上 `struct key` 的實際大小與 `usage` 欄位的型別/offset,核對本章描述。
3. 寫一個 `pipe2(fds, O_NOTIFICATION_PIPE)` + `ioctl(fds[0], IOC_WATCH_QUEUE_SET_SIZE, N)` 的最小程式,用 `strace` 確認回傳值,並嘗試用一個不在 1~512 範圍內的 size 測試邊界行為。
4. 讀你 kernel 版本的 `fs/pipe.c`,找出 `copy_page_to_iter_pipe()` 與 `push_pipe()` 對 `pipe_buffer.flags` 的初始化邏輯,確認 Dirty Pipe 的修補是否已經在你的版本裡（6.6 應該已修)。
5. 比較 `open("/dev/ptmx")` 兩次,用 `pahole`/gdb 分別印出 master 與 slave 的 `tty_struct` 位址與 `ops` 指標值,驗證它們是兩個獨立物件、指向不同的 `tty_operations`。
6. 寫一對 `add_key("user", ...)` 呼叫,內容填不同的 pattern,用 `KEYCTL_READ` 讀回來確認內容對得上;再用 `KEYCTL_UNLINK` 把其中一個從 keyring 上移除,觀察（透過 `ftrace` 追 `key_put`）它的引用計數什麼時候真正降到 0 並被回收。
7. 重現本章的 watch_queue 偽碼對照練習：讀 `kernel/watch_queue.c` 的 `watch_queue_set_filter()` 完整實作,把本章偽碼和真實原始碼逐行對照,標出我們偽碼哪裡簡化或可能失準的地方——這個練習同時是在驗證本章內容,也是在練習讀 kernel source 還原邏輯的能力。

## 本章重點整理

- TTY、keyring、watch_queue、pipe 四個子系統反覆成為 exploit 零件庫,核心原因是「unprivileged 配置門檻低」+「生命週期 user 可控」,外加部分物件帶有函式指標表或大段可控內容。
- CVE-2016-0728：`join_session_keyring()` 的 early-return 分支漏了 `key_put()`,引用計數可被撞到整數溢位,導致 UAF；教訓是「每個 get 都要有對應的 put，包括 early-return 路徑」。
- CVE-2022-0995：watch_queue 自己新增的 filter bitmap/陣列邏輯出現長度計算錯誤導致 OOB write；新機制不代表新的 bug 類型,通常是老問題換位置發生。
- CVE-2022-0847（Dirty Pipe）：`pipe_buffer.flags` 未初始化,讓 `PIPE_BUF_FLAG_CAN_MERGE` 意外殘留,最終繞過寫入權限檢查；成因是一行初始化的疏漏、威力卻極大,複雜度與殺傷力不是正相關。
- 這些物件的「好用」會隨 hardening（random kmalloc caches 等）逐漸被削弱,但「低門檻 + 生命週期可控」這個結構性特徵不會消失,只會換新的物件繼續扮演這個角色。

## 自我檢核

- [ ] 不看筆記，說出 TTY/keyring/watch_queue/pipe 四個子系統的共同特徵,並解釋為什麼這些特徵同時讓它們好用也讓它們容易出 bug。
- [ ] 能用自己的話解釋 CVE-2016-0728 裡「early-return 分支漏了 key_put()」具體是什麼意思,並畫出 usage 計數隨呼叫次數變化的示意。
- [ ] 能說出為什麼 Dirty Pipe 不需要任何堆布局操作就能成立,這和一般 heap overflow/UAF exploit 的差異在哪。
- [ ] 能解釋 watch_queue 為什麼和 pipe 在同一個章節討論(提示：底層結構的關係)。
- [ ] 給你一個新的 kernel 子系統,你能用本章的五條「好用零件」特徵去評估它會不會變成下一張熱門的 spray 牌。

## 延伸閱讀

### 官方文件 / 原始碼

- **[`include/linux/key.h`（GitHub torvalds/linux，v6.6 tag）](https://github.com/torvalds/linux/blob/v6.6/include/linux/key.h)**
  - **讀哪裡**：`struct key` 的欄位定義與註解,特別是 `usage`、`perm`、`payload` union。
  - **為什麼值得讀**：本章 keyring 物件模型的一手來源,offset 與欄位名稱應以你的版本核對。
- **[`kernel/watch_queue.c`（GitHub torvalds/linux，v6.6 tag）](https://github.com/torvalds/linux/blob/v6.6/kernel/watch_queue.c)**
  - **讀哪裡**：`watch_queue_set_size()`、`watch_queue_set_filter()` 兩個函式的完整實作。
  - **學什麼**：確認本章「無 capability 檢查、只有資源配額限制」的說法,以及 filter bitmap 的建構邏輯。
- **[`include/linux/pipe_fs_i.h`（GitHub torvalds/linux，v6.6 tag）](https://github.com/torvalds/linux/blob/v6.6/include/linux/pipe_fs_i.h)**
  - **讀哪裡**：`struct pipe_buffer`、`PIPE_BUF_FLAG_*` 巨集、`struct pipe_inode_info` 裡的 `watch_queue` 欄位。
  - **和本章的關聯**：本章「pipe 和 watch_queue 共用結構空間」這個說法的直接證據。

### 漏洞資料

- **[CVE-2016-0728 Root Cause Analysis（0xshlomil 鏡像的 Perception Point 分析）](https://0xshlomil.github.io/cve-2016-0728-root-cuase-analysis-and-exploitation-of-a-linux-kernel-vulnerability)**
  - **讀哪裡**：全文，重點看它怎麼描述 refcount 洩漏的迴圈與整數溢位的量級。
  - **和本章的關聯**：本章 keyring 案例的主要參考來源。
- **[Red Hat Bugzilla 2063786 — CVE-2022-0995](https://bugzilla.redhat.com/show_bug.cgi?id=2063786)**
  - **讀哪裡**：描述句與引用的修補 commit `93ce93587d36`。
  - **為什麼值得讀**：Bugzilla 把這個 bug 標成 NOTABUG 又改回去的討論過程,能看到廠商對「這算不算安全問題」的判斷拉鋸,呼應 [Ch 34](./34-filesystem-attack-surface.md) 討論過的「CVE 編號不等於風險共識」。
- **[Rapid7 / sysdig 等 CVE-2022-0847（Dirty Pipe）分析彙整](https://sysdig.com/?p=47837)**
  - **讀哪裡**：root cause 段落，對照本章的觸發流程圖。
  - **為什麼值得讀**：多個獨立來源對同一個 bug 的描述高度一致,適合用來交叉驗證本章內容。

### 部落格 / 其他

- **[Max Kellermann — Dirty Pipe 原始披露與 PoC](https://dirtypipe.cm4all.com/)**
  - **這篇說什麼**：發現者本人的第一手技術部落格,從怎麼發現到怎麼寫出 PoC 的完整過程。
  - **讀哪裡**：整篇，特別是他怎麼從一個「檔案內容莫名其妙變了」的異常現象反推出 root cause。
  - **為什麼值得讀**：這是「bug 是從現象倒推出 root cause」這種研究方法的教科書範例,和本章先給 root cause 再給現象的敘事順序正好相反,讀兩種順序能互相補強理解。

### 書籍

- **《Linux Kernel Development》第 3 版** — Robert Love（Addison-Wesley, 2010）
  - **這本書的定位**：涵蓋到 2.6 kernel,年代久,但對 TTY/pipe 這類「歷史悠久、介面穩定」的子系統,基礎概念到今天變化有限,適合當背景讀物。
  - **讀哪幾章**：與行程間通訊、檔案系統抽象相關的章節;避免把書中效能數字當成現代 kernel 的真實數字。
  - **前提知識**：基本 C、作業系統課程水準的行程/記憶體概念。

這四個子系統教我們的是「低門檻 + 可控生命週期」的結構性風險。下一章要換一個完全不同的切角：**為什麼一個 unprivileged user 能透過 `unshare(CLONE_NEWUSER)` 把原本只有 root 摸得到的攻擊面（nf_tables、AF_PACKET、mount 路徑）通通打開**——這是現代 Linux LPE 最關鍵的前置條件。

→ [Ch 36 User namespace 與攻擊面放大：unprivileged user 怎麼拿 CAP_NET_ADMIN](./36-user-namespace.md)
