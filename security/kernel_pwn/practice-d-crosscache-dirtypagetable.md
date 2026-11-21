# 練習 D — Cross-cache → Dirty Pagetable 綜合題

> **目標**：把 Part 3 後半兩章——[Ch 22](./22-cross-cache.md) 的 cross-cache 與 [Ch 23](./23-dirty-pagetable.md) 的 Dirty Pagetable——接成**一條線**。這個練習**不重寫**那兩章的機制，而是讓你站在一個自製的 UAF/double-free 靶上，練「把一個落在普通 kmalloc cache 的 bug，一路導引到『攻擊 page table』這個跨層目標」的**接合能力**。完成後，你再去看任何一篇用 Dirty Pagetable 收尾的真實 writeup，應該能一眼認出：哪一段是 cross-cache 在搬 page、哪一段是 PTE 覆寫在拿任意實體讀寫、兩段是在哪個介面縫起來的。這是 Part 3 的畢業題——練的是**串接**，不是又一個新原語。

## 背景與動機

[Ch 23](./23-dirty-pagetable.md) 講清楚了 Dirty Pagetable 的威力：改掉一個 PTE 的實體頁框號，就等於拿到任意實體記憶體讀寫，而且幾乎不需要 leak、完全繞過 CFI。但那一章刻意把一個前提當作「已經成立」來講——**你要先有辦法覆寫到一個 page table page（PTE page）裡的 entry**。這個前提本身不是天上掉下來的：PTE page 不是一般的 kmalloc 物件，它由 page table 專屬的配置路徑（`pte_alloc` 一類）向 buddy 要 page，**不住在任何 user 能隨手 spray 的 kmalloc-N cache 裡**。

這就是為什麼 Dirty Pagetable 幾乎總是跟 cross-cache 綁在一起出現。你手上的 bug，十之八九是一個落在某個 `kmalloc-N` cache 的 UAF 或 double-free——它給你的能力是「對某個 kmalloc 物件的槽位動手腳」。要把這個「object 層」的能力，變成「能寫進一個 PTE page」的能力，中間缺的那一段橋，正是 [Ch 22](./22-cross-cache.md) 教的 cross-cache：把一整個 slab 的 page 還給 buddy，再誘導 page table 的配置路徑把**同一塊實體 page** 當成新的 PTE page 拿走。於是你那個還指著舊位置的懸空指標，現在指向的是一個活生生的 page table。

```
  你有的（object 層）            你要的（page 層）
  ┌─────────────────┐           ┌─────────────────┐
  │ kmalloc-N 裡的   │           │ 一個 PTE page，  │
  │ 一個 UAF 物件    │  ──橋──▶  │ 裡面 512 個 PTE  │
  │ （能寫它的槽）   │ cross-   │ 都是任意實體的   │
  └─────────────────┘  cache    │ 讀寫開關         │
         Ch 18/20              └─────────────────┘
                     Ch 22              Ch 23
```

這個練習就是要你親手把這張圖的三段（bug → 橋 → 目標）接起來。靶刻意只提供**最左邊那一段**——一個乾淨、可重複、掛 KASAN 就能確認的 UAF/double-free；中間的 cross-cache grooming 和右邊的 Dirty Pagetable，你要**套用**前面兩章學過的技術，而不是指望靶幫你準備好。

> **安全與合法性聲明**：以下所有操作都在**你自己擁有的 Linux VM**（建議用 [Ch 0](./00-environment-setup.md) 搭建的 QEMU 環境，或任何你可以隨意摧毀重建的虛擬機）內進行。本練習提供的 module 原始碼**未在本環境實測**，已盡力確保邏輯正確，但你在自己的 VM 裡編譯、載入、測試時，若發現任何編譯錯誤或行為與描述不符，請以你實測的結果為準。練習的後半（cross-cache、Dirty Pagetable）刻意以**文字描述 + 指回對應章節**的方式呈現，不附「複製貼上就能跑」的 exploit——因為這兩段技術各自有專章，這個練習要訓練的是你**把它們接起來**的能力，不是再抄一份武器。

## 任務規格

### 目標 module：`ccsim`

這個字元裝置提供一個最小的「配置 / 釋放一個固定大小物件」介面，裡面埋了一個**典型的 double-free / UAF**——和 [練習 E](./practice-e-nftables-exploit.md) 的 `nft_sim` 同一種「兩條路徑對同一塊記憶體的生命週期假設不一致」的 bug 模式，但這裡故意把物件大小設計成一個**整齊、落在你熟悉的 kmalloc cache** 的尺寸，方便你規劃 cross-cache。靶本身**不含**任何 PTE、實體位址、或提權程式碼——那些是你要套用 Ch 22/23 自己接上去的。

```c
/* ccsim.c —— 練習用的簡化 vulnerable module。提供一個固定大小物件的
 * 配置/釋放介面，埋了一個 double-free / UAF。不是任何真實 kernel 版本的程式碼。
 * 設計目的：給你一個「落在普通 kmalloc cache」的乾淨 bug，讓你練習把它
 * 透過 cross-cache（Ch 22）導向 page table，再用 Dirty Pagetable（Ch 23）收尾。 */
#include <linux/module.h>
#include <linux/miscdevice.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/mutex.h>

#define CCSIM_ALLOC   0x2001   /* 配置一個物件到某個 slot */
#define CCSIM_FREE    0x2002   /* 釋放某個 slot 的物件（bug：不清 slot 指標） */
#define CCSIM_WRITE   0x2003   /* 寫入某個 slot 物件的內容（UAF 的寫入面） */
#define CCSIM_READ    0x2004   /* 讀出某個 slot 物件的內容（UAF 的讀取面） */

/* 物件大小刻意設成 512 bytes —— 落在 kmalloc-512。
 * 換成別的大小（256/1024/2048）會改變你要 drain 的 cache，當作延伸挑戰。 */
#define CCOBJ_SIZE 512

struct cc_obj {
        char data[CCOBJ_SIZE];
};

#define MAX_SLOTS 256
static struct cc_obj *slots[MAX_SLOTS];
static DEFINE_MUTEX(cc_lock);

/* ioctl 參數結構：user 傳進來的 slot 索引 + 要讀寫的內容緩衝 */
struct cc_req {
        int slot;
        char buf[CCOBJ_SIZE];
};

static long ccsim_alloc(int slot)
{
        if (slot < 0 || slot >= MAX_SLOTS)
                return -EINVAL;
        /* 注意：這裡不檢查 slots[slot] 是否已經非 NULL，
         * 重複 ALLOC 同一個 slot 會蓋掉舊指標（記憶體洩漏，但不是本練習主線 bug） */
        slots[slot] = kzalloc(CCOBJ_SIZE, GFP_KERNEL);
        if (!slots[slot])
                return -ENOMEM;
        return 0;
}

/* ★★★ bug 所在 ★★★
 * FREE 釋放了物件，卻【沒有】把 slots[slot] 設回 NULL。
 * 於是這個 slot 仍然持有一個指向已釋放記憶體的懸空指標：
 *   - 再 FREE 一次同一個 slot → double free
 *   - FREE 之後再 WRITE/READ 同一個 slot → use-after-free
 * 這對應真實世界「釋放路徑忘了清空容器指標 / 兩條路徑都認為自己該釋放」的經典錯誤。 */
static long ccsim_free(int slot)
{
        if (slot < 0 || slot >= MAX_SLOTS || !slots[slot])
                return -EINVAL;
        kfree(slots[slot]);
        /* 漏寫：slots[slot] = NULL; —— 懸空指標留在表裡 */
        return 0;
}

static long ccsim_write(struct cc_req __user *ureq)
{
        struct cc_req req;

        if (copy_from_user(&req, ureq, sizeof(req)))
                return -EFAULT;
        if (req.slot < 0 || req.slot >= MAX_SLOTS || !slots[req.slot])
                return -EINVAL;
        /* UAF 寫入面：如果 req.slot 的物件已經被 FREE 過，
         * 這裡就是往一塊可能已被別人重新配置的記憶體寫入可控內容 */
        memcpy(slots[req.slot]->data, req.buf, CCOBJ_SIZE);
        return 0;
}

static long ccsim_read(struct cc_req __user *ureq)
{
        struct cc_req req;

        if (copy_from_user(&req, ureq, sizeof(req.slot)))
                return -EFAULT;
        if (req.slot < 0 || req.slot >= MAX_SLOTS || !slots[req.slot])
                return -EINVAL;
        /* UAF 讀取面：讀回一塊可能已被重新配置成別的東西的記憶體 */
        memcpy(req.buf, slots[req.slot]->data, CCOBJ_SIZE);
        if (copy_to_user(ureq, &req, sizeof(req)))
                return -EFAULT;
        return 0;
}

static long ccsim_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
        long ret;

        mutex_lock(&cc_lock);
        switch (cmd) {
        case CCSIM_ALLOC: ret = ccsim_alloc((int)arg); break;
        case CCSIM_FREE:  ret = ccsim_free((int)arg); break;
        case CCSIM_WRITE: ret = ccsim_write((struct cc_req __user *)arg); break;
        case CCSIM_READ:  ret = ccsim_read((struct cc_req __user *)arg); break;
        default:          ret = -ENOTTY;
        }
        mutex_unlock(&cc_lock);
        return ret;
}

static const struct file_operations ccsim_fops = {
        .unlocked_ioctl = ccsim_ioctl,
};

static struct miscdevice ccsim_dev = {
        .minor = MISC_DYNAMIC_MINOR,
        .name  = "ccsim",
        .fops  = &ccsim_fops,
        .mode  = 0666,
};

static int __init ccsim_init(void) { return misc_register(&ccsim_dev); }
static void __exit ccsim_exit(void) { misc_deregister(&ccsim_dev); }
module_init(ccsim_init);
module_exit(ccsim_exit);
MODULE_LICENSE("GPL");
```

### 任務要求

| 項目 | 規格 |
|---|---|
| 編譯環境 | 你自己的 Linux VM，建議開 `CONFIG_KASAN`、`CONFIG_SLUB_DEBUG`；對照 [Ch 0](./00-environment-setup.md) |
| 第一階段目標 | 用 ioctl 序列確實觸發 `ccsim` 的 double-free 與 UAF（讀/寫已釋放物件），用 KASAN 確認 |
| 第二階段目標（主線） | **規劃並描述**一條「cross-cache（[Ch 22](./22-cross-cache.md)）把 `kmalloc-512` 的 slab page 導向 page table，再用 Dirty Pagetable（[Ch 23](./23-dirty-pagetable.md)）拿任意實體讀寫」的完整路線；能實作到哪一步算哪一步 |
| 禁止事項 | 不要求你修好 `ccsim`（修補留作延伸挑戰）；也不要求你交出「複製貼上就能提權」的武器——這題看的是你**接合兩章技術的規劃與理解** |
| 驗收重點 | 你能說清楚「object 層的 bug」是怎麼一步步變成「page 層的任意實體讀寫」，每一段對應哪一章、縫合點在哪裡 |

## 期望輸出範例

```
第一階段（確認 bug）——ioctl 序列：
  ALLOC(slot=0)
  FREE(slot=0)
  FREE(slot=0)          ← double free（slots[0] 懸空指標還在）

輸出（取決於你的 KASAN/SLUB_DEBUG 設定）：
  [   13.370011] ==================================================================
  [   13.370013] BUG: KASAN: double-free or invalid-free in ccsim_free+0x.. [ccsim]
  [   13.370016] ...
```

```
UAF 讀取面——ioctl 序列：
  ALLOC(slot=0)
  FREE(slot=0)                 ← slots[0] 懸空
  （此時用別的路徑 spray，讓新物件落進這塊）
  READ(slot=0)                 ← 讀回的是「別人的」內容

輸出：READ 回來的 buf 內容，不再是你 ALLOC 時的全零，而是 spray 進去的資料——
      這就是你驗證「UAF 槽位被重新配置」成功的最直接訊號
```

```
邊界輸入：對一個從未 ALLOC 過的 slot 直接 FREE/READ/WRITE
輸出：ioctl 回傳 -EINVAL（slots[slot] 為 NULL）——
      確認 bug 只在「先 ALLOC、再 FREE、slot 指標殘留」這個特定狀態下才成立
```

## 如果你卡住了

1. **先把第一階段（確認 UAF/double-free）當成唯一目標**。KASAN 沒跳出來之前，不要去想 cross-cache。這一段和 [練習 E](./practice-e-nftables-exploit.md)、[練習 C](./practice-c-uaf-tty.md) 的觸發完全同一個等級，你已經會了。
2. **cross-cache 卡住的話，回 [Ch 22](./22-cross-cache.md) 重讀「怎麼讓一整個 slab 的 page 回到 buddy」那一節**。關鍵是：你要先把 `kmalloc-512` 這個 cache 裡**同一個 slab 上的所有物件**都釋放掉，整個 slab 才會被 SLUB 還給 buddy——只釋放你自己那一個不夠。這就是為什麼靶給了你 256 個 slot：你需要能大量 ALLOC/FREE 來操控整個 slab 的佔用狀態。
3. **「怎麼知道 page 真的回 buddy 了、又真的被 page table 拿走了」？** 這是整題最難驗證的一步，也是 [Ch 23](./23-dirty-pagetable.md) 的核心前提。回去看它怎麼用「大量 `mmap` + `MAP_POPULATE` 製造 PTE page」來搶那塊剛釋放的 page——這個「搶」的成功率就是你後面穩定性的來源。
4. **不要急著要「任意實體讀寫」**。先確認一個更小的里程碑：你透過 UAF 的 `WRITE` 面寫下去的內容，**真的改到了一個 PTE 的值**（例如你 `mmap` 出來的某個 user 位址，讀出來的內容突然變成別的實體 page 的內容）。能觀察到這一個現象，Dirty Pagetable 就成立了，剩下是 [Ch 23](./23-dirty-pagetable.md) 的既有流程。

## 實作步驟建議

### Step 1：觸發並確認 bug

編譯 `ccsim.c`、`insmod`、確認 `/dev/ccsim` 出現。寫一個最小 C 程式跑「期望輸出範例」的兩組序列（double-free 與 UAF 讀取面），用 KASAN 確認。**子目標**：證明你能用程式碼可重複地製造「一個 `kmalloc-512` 物件被釋放後、slot 懸空指標仍可讀寫」的狀態。這是後面一切的地基，不穩就別往下。

### Step 2：定性這個原語

想清楚 `ccsim` 給你的具體能力：`FREE` 後 `slots[slot]` 不清空，所以你保有一個對已釋放 `kmalloc-512` 槽位的**讀（`READ`）**和**寫（`WRITE`）**通道。**子目標**：把「有一個 UAF」翻譯成「我能在物件被釋放後，對那塊實體記憶體做可控的 512-byte 讀與寫」——這正是 cross-cache 之後，你要拿來打 PTE page 的那隻手。

### Step 3：cross-cache grooming —— 把 slab page 還給 buddy（套用 [Ch 22](./22-cross-cache.md)）

這一步**不要重新發明**，直接套 [Ch 22](./22-cross-cache.md) 的流程：先 drain `kmalloc-512`（大量 ALLOC 把當前 partial slab 填滿、逼 SLUB 開新 slab，讓你能掌握「哪些物件在同一個 slab 上」），再把你鎖定的那個 slab 上的物件**整批 FREE**，使整個 slab 的 page 被 SLUB 釋放回 buddy allocator。**子目標**：讓一塊**你仍持有懸空讀寫通道**的實體 page，處於「剛回到 buddy、等著被下一個要 page 的人拿走」的狀態。這一步的成功與否，決定了下一步能不能搶到。

### Step 4：把那塊 page 變成 page table（套用 [Ch 23](./23-dirty-pagetable.md)）

這一步是整題的縫合點，也刻意只用文字描述，因為機制 [Ch 23](./23-dirty-pagetable.md) 已經講完整了：在 Step 3 的 page 回到 buddy 之後，**立刻**用 user-space 的 `mmap` + `MAP_POPULATE`（或連續觸 page fault）去**強迫 kernel 配置 page table pages**——每 2MB 的 populated 映射會讓 kernel 向 buddy 要一個 PTE page（裝 512 個 PTE entry）。只要你的 grooming 夠準，剛釋放的那塊 page 就會被當成新的 PTE page 拿走。**子目標**：讓你 Step 2 的那個「對已釋放 `kmalloc-512` 槽位的讀寫通道」，現在實際指向一個**活的 PTE page**。到這裡，你就站上了 [Ch 23](./23-dirty-pagetable.md) 開篇假設的那個起點。

### Step 5：Dirty Pagetable —— 拿任意實體讀寫、落到 cred（套用 [Ch 23](./23-dirty-pagetable.md)）

接下來完全是 [Ch 23](./23-dirty-pagetable.md) 的既有劇本，用 `ccsim` 的 `WRITE` 面當作「覆寫 PTE」的手：改掉某個 PTE 的實體頁框號，讓你 `mmap` 出來的一個 user 虛擬頁，指向你選定的任意實體 page；於是你在 userland 讀寫那個虛擬頁，就是在讀寫任意 kernel 實體記憶體。**子目標**：先用它去讀（確認任意實體讀成立），再用它定位並覆寫你自己 process 的 `cred`（把 uid 改成 0），完成提權。這一段為什麼能繞過 CFI、為什麼幾乎不用 leak，[Ch 23](./23-dirty-pagetable.md) 都解釋過，這裡只是把 `ccsim` 的 UAF 寫入面接上去。

### Step 6：穩定化（套用 [Ch 45](./45-exploit-stabilization.md)）

cross-cache 「搶 page」這一步天生是機率事件——你釋放的 page 不一定馬上被 PTE page 拿走，中間可能被別的配置截胡。把整條鏈包成「grooming → 搶 → 驗證是否搶到 → 沒搶到就回收重來」的重試迴圈，並在提權成功後把動過的 PTE/物件狀態收拾乾淨，避免後續 page table 操作踩到你留下的爛攤子而 panic。**子目標**：連跑數十次、記錄成功率，對照 [Ch 45](./45-exploit-stabilization.md) 把不穩定的來源一項項壓下去。這一步才是「我理解這條鏈」和「我能穩定地走完這條鏈」之間真正的差距。

## 參考解答方向

**先自己把 Step 1–6 規劃完再看。** 這裡刻意**不給 exploit C 骨架**——不是藏私，而是因為這題的兩段核心技術（cross-cache、Dirty Pagetable）各有專章，把它們的程式碼再抄一次到這裡，只會讓你以為「背下這段就會了」，而錯過這題真正要練的東西：**接合**。

<details>
<summary>點開解答的「形狀」（純文字，無 turnkey 程式碼）</summary>

這條鏈的形狀，以及每一段去哪裡找：

- **bug 本身（Step 1–2）**：唯一保證能重現、不依賴任何佈局假設的部分，直接對應 `ccsim.c` 的程式邏輯。你要自己寫的，就是用 `CCSIM_ALLOC/FREE/WRITE/READ` 四個 ioctl 組出「釋放後仍可讀寫」的狀態，並用 KASAN 驗證。這段和 [練習 E](./practice-e-nftables-exploit.md) 的 `trigger_double_free()` 同一個難度。

- **cross-cache（Step 3–4）**：不要自己從零推。打開 [Ch 22](./22-cross-cache.md)，照它的「drain cache → 鎖定 slab → 整批 free → page 回 buddy」步驟，把 victim 從它舉的例子換成這裡的 `kmalloc-512` 物件；「搶 page 的那一方」從它的例子換成 [Ch 23](./23-dirty-pagetable.md) 的 `mmap(MAP_POPULATE)` 製造 PTE page。你要填的空格是：你的 kernel 上 `kmalloc-512` 一個 slab 裝幾個物件（決定你要 ALLOC/FREE 多少個）、PTE page 的 spray 量要開多大才搶得穩——這些都是**依你自己 VM 實測**的數字，沒有能寫死的常數。

- **Dirty Pagetable（Step 5）**：完全套 [Ch 23](./23-dirty-pagetable.md)。你唯一要「接線」的地方，是把它描述的「覆寫 PTE」那隻手，換成 `ccsim` 的 `CCSIM_WRITE`——因為你的懸空通道現在指向 PTE page，一次 `WRITE` 就是一次 PTE 覆寫。PTE 的格式（實體頁框號在哪幾個 bit、present/writable flag 怎麼設）、怎麼用任意實體讀寫定位 `cred`，Ch 23 都有，照抄它的推導、換上你實測的實體位址即可。

- **穩定化（Step 6）**：[Ch 45](./45-exploit-stabilization.md) 的重試 / 驗證 / cleanup 三件事。這題的主要不穩定源就是 Step 4 的「搶 page」競爭，重試迴圈要圍著它設計。

**為什麼不給完整骨架**：因為只要你能把上面四段分別在 Ch 22 / Ch 23 / Ch 45 裡找到對應程式碼、並想清楚「`ccsim` 的哪個 ioctl 接到那段的哪隻手」，你就已經完成這題要訓練的能力了。真正的 exploit 檔，是你把這四段的空格用自己 VM 的實測數字填滿之後，自然長出來的——那份才是你自己的。

</details>

## 測試用例

| 輸入（ioctl 序列） | 預期行為 | 說明 |
|---|---|---|
| `ALLOC(0)→FREE(0)→FREE(0)` | double-free，KASAN 應報錯 | slot 懸空指標導致的重複釋放 |
| `ALLOC(0)→FREE(0)→READ(0)` | 讀回「非你寫入」的內容（若中間有 spray）或 KASAN use-after-free | UAF 讀取面 |
| `ALLOC(0)→FREE(0)→WRITE(0, 可控)` | 往已釋放記憶體寫入；若已被重配置，改到別人的物件 | UAF 寫入面——cross-cache 後這就是覆寫 PTE 的手 |
| `READ(5)`（slot 5 從未 ALLOC） | 回傳 `-EINVAL`（`slots[5]==NULL`） | 驗證 bug 只在 ALLOC→FREE→殘留 的狀態成立 |
| `ALLOC(0)→ALLOC(0)` | 第二次覆蓋舊指標（洩漏，非主線 bug） | 提醒你 ALLOC 不檢查重複，規劃 grooming 時注意別誤用 |

## 延伸挑戰（加分）

- **換一個物件大小**：把 `CCOBJ_SIZE` 改成 256 或 1024，重做一次 cross-cache 規劃——體會「victim 落在不同 cache，drain 的對象與 slab 幾何都要重算」，這正是真實題目每次都要重新校準的地方。
- **修補它**：把 `ccsim_free` 補上 `slots[slot] = NULL;`，驗證原本的 double-free/UAF 序列全部失效。這練的是「讀 CVE fix」的逆向——你先踩過 bug，再寫出等價於真實修補的那一行。
- **把 Dirty Pagetable 換成 Dirty Cred**：同一個 cross-cache 之後的 UAF，改成套 [Ch 24](./24-dirty-cred.md) 的 Dirty Cred 路線（換物件而非改 PTE），比較兩條收尾在「要不要定位、要不要任意實體讀寫」上的取捨。
- **寫一份 writeup**：照 [Ch 42](./42-patch-to-rootcause.md) 的方法論格式，把「`ccsim` 的 bug → cross-cache → Dirty Pagetable」整條鏈寫成一篇別人能重現的文件，特別把兩段技術的**縫合點**講清楚——這是真實 submission 最能看出作者功力的地方。

## 自我檢核

- [ ] 能不看程式碼，用自己的話解釋 `ccsim` 的 bug：為什麼 `FREE` 不清 slot 指標會同時造成 double-free 和 UAF。
- [ ] 能解釋**為什麼 Dirty Pagetable 幾乎總要先做 cross-cache**：PTE page 不住在任何 user 可 spray 的 kmalloc cache，所以必須在 page/buddy 層搭橋。
- [ ] 能說出 cross-cache 這一步「把整個 slab 的 page 還給 buddy」為什麼需要操控**同一個 slab 上的所有物件**，而不是只釋放自己那一個（對照 [Ch 22](./22-cross-cache.md)）。
- [ ] 能指出整條鏈的**縫合點**：`ccsim` 的 `CCSIM_WRITE` 在 cross-cache 之後，實際上成了 [Ch 23](./23-dirty-pagetable.md) 裡「覆寫一個 PTE」的那隻手。
- [ ] 跑過至少一次「連續執行、記錄成功率」的穩定性測試，並能說出主要不穩定源來自 Step 4 的「搶 page」競爭（對照 [Ch 45](./45-exploit-stabilization.md)）。
- [ ] 完成至少一項延伸挑戰。

這個練習收尾了 Part 3——你現在手上有一條完整的心智地圖：一個落在普通 kmalloc cache 的 bug（[Ch 18](./18-uaf-double-free.md)），透過 grooming（[Ch 20](./20-heap-grooming.md)）與 cross-cache（[Ch 22](./22-cross-cache.md)）搬到 page 層，再用 Dirty Pagetable（[Ch 23](./23-dirty-pagetable.md)）拿到任意實體讀寫、完成提權。這整套「往下一層找共用面」的思路，是現代 kernel heap 利用的主幹。接下來 Part 4 要把視角轉向防守方近年疊上來的那些 mitigation——random kmalloc caches、SLAB_VIRTUAL、CFI/KCFI——看看它們分別卡住這條鏈的哪一段，以及攻擊者又往哪裡繞。

→ [Ch 26 — 2023+ mitigation 全景：random kmalloc caches、SLAB_VIRTUAL、CFI/KCFI、FGKASLR、INIT_ON_ALLOC/FREE](./26-modern-mitigations.md)
