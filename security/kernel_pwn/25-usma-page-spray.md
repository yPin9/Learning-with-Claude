# Ch 25 — USMA 與 page-level spray：把 kernel page 映射進 userspace、pgv/packet socket

> **目標**：理解一條把戰場從「object 層」徹底拉到「page 層」的路線。USMA（User-Space Mapping Attack）的核心是：不去偽造 PTE、也不去劫控制流，而是**騙 kernel 把一塊它自己擁有的 page，透過一條合法的 mmap 路徑映射進 user space**——映射成功的那一刻，你在 userland 就有了對這塊 kernel 記憶體的直接讀寫，不需要 leak、不需要 ROP。學完你要能解釋：USMA 和 Dirty Pagetable（[Ch 23](./23-dirty-pagetable.md)）在「動的是誰」上的根本差別、`AF_PACKET` 的 `pgv`（page vector）ring buffer 怎麼成為最經典的 USMA 載體、以及為什麼同一個 `pgv` 也是現代 kernel pwn 裡最好用的 **page-level spray** 工具之一。

> **環境**：Linux 6.6 LTS、x86-64、QEMU。延續 [Ch 22](./22-cross-cache.md) 的 page 流動與 [Ch 23](./23-dirty-pagetable.md) 的映射層視角。本章以機制為主，所有 struct 佈局、offset、`order` 數值都依版本，必須用 `pahole`/原始碼確認。未實測，理論預期行為。

## 為什麼要「映射 kernel page」而不是「改 kernel 記憶體」

把 Part 3 這幾章的 data-only 路線排在一起，會看到一條很清楚的「往下鑽」軌跡：

```
  改欄位（Ch 16/24）：   找到目標物件 → 改它的欄位（uid、f_mode…）
                          → 要先定位物件（leak/掃描）

  改映射（Ch 23）：       改一個 PTE 的 PFN → 任意實體記憶體 R/W
                          → 不太需要 leak，但你要先「有能力寫到 PTE 這塊」

  USMA（本章）：          讓 kernel 自己把一塊 page 合法地 mmap 給我
                          → 連「寫 PTE」都不用自己動手，
                            kernel 的 mmap 路徑幫我把 PTE 建好
```

Dirty Pagetable 的精神是「**我自己動手去改位址轉譯表**」——我需要一個能寫到 page table page 的原語，然後我親手把某個 PTE 的 physical frame number（PFN）換成我要的目標頁。USMA 換了個更省力的想法：**kernel 裡本來就有很多「把一塊 kernel page 映射到 user 位址空間」的合法路徑**（各種 driver 的 `mmap`、`AF_PACKET` 的 ring buffer、DMA buffer、framebuffer……），這些路徑會幫我把整套 PTE 建好。我要做的，只是用一個 heap 寫入原語，去**竄改「這條 mmap 路徑最後會映射到哪塊 page」的那個指標**，讓它指向我真正想要的 kernel page。接下來 `mmap()` 一呼叫，kernel 就心甘情願地把那塊 page 攤在我的 user 位址空間裡。

```
  Dirty Pagetable：我 → (寫原語) → PTE → 指向目標實體頁
                   「PTE 是我手動偽造的」

  USMA：          我 → (寫原語) → 某 struct 裡的 page 指標 → 指向目標 kernel 頁
                   kernel 的 mmap 路徑 → 幫我合法建出 PTE → 映射進 user space
                   「PTE 是 kernel 幫我建的，我只動了『要映射哪塊』這個指標」
```

這個差別很關鍵：USMA 動的不是轉譯表本身，而是**「轉譯表將被建成什麼樣」的輸入**。因為最後那套 PTE 是 kernel 走正規流程建出來的，它天生合法、天生 present、天生 user-accessible——你省掉了 Dirty Pagetable 裡「手工把 PTE 的 flag bit（present / user / RW / NX）湊對」那些瑣碎又容易 fault 的細節。

## 先建立直覺：把保險箱的門牌換成金庫的門牌

```
  正常 mmap：         你 mmap 一個 packet socket 的 ring buffer，
                      kernel 把「這個 socket 的那幾塊 page」攤給你看。
                      → 你看到的是你自己的資料，合法、無害。

  USMA：             你趁 kernel 還沒把 ring 的 page 指標讀出來之前，
                      用一個寫原語把「ring 的 page 指標」從
                      『socket 自己的 page』偷偷換成『存著 cred 的那塊 kernel page』。
                      → 你 mmap 之後看到的，是 kernel 金庫裡的 cred，
                        而且是可讀可寫——kernel 以為它攤給你的還是你自己的 ring。
```

門（mmap 這條合法通道）完全沒被破壞，你只是把門後面接的「要帶你去哪個房間」的門牌換掉了。因為走的是正門，任何「這個 mmap 合不合法」的檢查都通過——問題出在「它要映射的那塊 page 是誰」這個被你竄改過的輸入。

## 經典載體：`AF_PACKET` 的 `pgv`（page vector）

USMA 最廣為人知的實作載體是 `AF_PACKET` 的 `PACKET_MMAP` 機制。要懂它，先懂 packet socket 的 ring buffer 是怎麼配出來、怎麼被 mmap 的。

### `pgv` 是什麼

當你對一個 `AF_PACKET` socket 設定 `PACKET_RX_RING` 或 `PACKET_TX_RING`，kernel 會配置一塊「環狀緩衝區」，讓抓到的封包不經 `recvfrom` 的 copy，直接出現在一塊可以 mmap 給 user 的記憶體裡。這塊緩衝區在 kernel 內部不是單一一大塊，而是一個**page vector**——`struct pgv` 的陣列，每個 `pgv` 指向一塊（或一組）buddy page：

```c
/* net/packet/af_packet.c —— 結構示意，欄位名與佈局依版本，用原始碼/pahole 確認 */
struct pgv {
        char *buffer;          /* 指向這一格的 page（們）的 kernel 虛擬位址 */
};

/* packet_ring_buffer 內部持有 pg_vec = struct pgv 的陣列 */
struct packet_ring_buffer {
        struct pgv      *pg_vec;        /* ← USMA 要竄改的就是這條指標鏈 */
        unsigned int    head;
        /* ... */
        unsigned int    pg_vec_len;     /* 有幾格 */
        unsigned int    pg_vec_pages;   /* 每格幾個 page（= 2^order） */
        unsigned int    pg_vec_order;   /* buddy order */
        /* ... */
};
```

配置流程大致是 `packet_set_ring()` → `alloc_pg_vec()` → 對每一格呼叫 `alloc_one_pg_vec_page()`，後者走的是 **buddy allocator**（`__get_free_pages(... , order)`，見 [Ch 7](./07-buddy-allocator.md)），而**不是 SLUB**。這點非常重要：

> `pgv` 指向的是 **buddy 層的 order-N page**，不是 kmalloc slab 裡的 object。所以 `pgv` 的大小粒度是 page（4 KiB × 2^order），不是 kmalloc-N 的 object 大小。談 USMA 與 pgv spray 時腦袋要切到 page 層，別用 slab object 的直覺去想它。

### 正常的 mmap 路徑

user 配好 ring 之後呼叫 `mmap(socket_fd, ...)`，核心進到 `packet_mmap()`：

```c
/* packet_mmap() 的骨架（示意）：把 pg_vec 裡每一格的 page
 * 逐頁用 vm_insert_page() 插進呼叫者的 VMA */
static int packet_mmap(struct file *file, struct socket *sock,
                       struct vm_area_struct *vma)
{
        /* ... 取出這個 socket 的 packet_ring_buffer ... */
        for (each pg_vec[i]) {
                for (each page in that order-N block) {
                        vm_insert_page(vma, start, page);   /* ← 把 kernel page 映射進 user VMA */
                        start += PAGE_SIZE;
                }
        }
        /* ... */
}
```

`vm_insert_page()` 就是把一個 `struct page` 掛進呼叫者的 VMA、建出對應的 user PTE。正常情況下這些 page 是這個 socket 自己的 ring page，你 mmap 看到的是自己的封包緩衝區，天經地義。

### USMA 怎麼介入

USMA 的切入點：**在 `pg_vec` 這條指標鏈上動手腳**。你需要一個 heap 寫入原語（來自某個 bug——OOB write、UAF 後再分配、type confusion……），能寫到 `struct packet_ring_buffer` 的 `pg_vec`，或 `pg_vec[i].buffer` 這格指標。把它從「socket 自己的 page」改成「你想讀寫的那塊 kernel page（例如存著 `struct cred`、page table、或某個 kernel 全域變數的 page）」：

```
  配 ring  → pg_vec[i].buffer 本來指向 socket 自己的 page P_self
            ┌────────────────────────────────────────────┐
  寫原語   │ 把 pg_vec[i].buffer 覆寫成 P_target           │
            │（P_target = 存著 cred / modprobe_path / PTE   │
            │  的那塊 kernel page 的 kernel 虛擬位址）        │
            └────────────────────────────────────────────┘
  mmap()   → packet_mmap() 照著被竄改的 pg_vec，
             把 P_target 這塊 kernel page vm_insert_page 進你的 user VMA
            ↓
  現在      → 你在 user space 對這段 mmap 區域讀寫
             = 直接讀寫 P_target 這塊 kernel 記憶體
             （改 cred->uid=0、改 modprobe_path、改 PTE…）
```

映射成功後，你拿到的是**對目標 kernel page 的 user-space 直接 R/W**。要提權就直接在上面改 `cred` 的 uid/gid 為 0，或改 `modprobe_path`（[Ch 16](./16-classic-primitives.md) 的經典原語），或把它當成任意實體讀寫的入口（和 Dirty Pagetable 殊途同歸）。

## 為什麼 USMA 在 CFI 時代特別香

把它放回 [Ch 28](./28-cfi-kcfi.md) / [Ch 29](./29-data-only-attack.md) 的脈絡：

| mitigation | 為什麼擋不住 USMA |
|---|---|
| CFI / KCFI | 全程沒有任何間接呼叫、沒有控制流轉移，純粹是「改一個 page 指標 + 走合法 mmap」 |
| SMEP / SMAP | 不需要 kernel 執行 user 的程式、也不需要 kernel 讀 user 的資料——是反過來，user 讀寫 kernel page |
| FREELIST_HARDENED | 不碰 SLUB freelist 指標（pgv 是 buddy page，不是 slab object） |
| KASLR | **常常不需要 leak**：你把 page 映射進來、直接讀它的內容，位址隨機化對「我已經能讀這塊記憶體」沒意義。唯一需要知道的是「目標 page 的 kernel 虛擬位址」，而這往往能用 physmap/direct map 的固定關係（[Ch 6](./06-kernel-memory-layout.md)）或另一個弱 leak 推出來 |

USMA 的代價：你需要一個**夠精準的寫入原語**，能寫到 `pg_vec` 這類「決定要映射哪塊」的指標；而且目標 page 的位址要能算得出來。它把「控制流劫持」和「手工偽造 PTE」這兩個負擔都拿掉了，換成「找到一條合法 mmap 路徑 + 改它的 page 指標輸入」。

## 另一面：`pgv` 作為 page-level spray 原語

即使你這次的 bug 不走 USMA，`pgv` 本身也是現代 kernel pwn 的**主力 page spray 工具**，理由正是前面強調的：它直接從 **buddy allocator** 要 order-N page，而且內容（封包資料）高度可控。這讓它在 [Ch 22](./22-cross-cache.md) 的 cross-cache、[Ch 20](./20-heap-grooming.md) 的 grooming 裡都極有用。

### 用 pgv 大量配/控 page 的手法

```c
/* 用 packet socket 大量配出可控內容的 order-N page（spray）骨架 */
#include <sys/socket.h>
#include <linux/if_packet.h>
#include <net/ethernet.h>

static int pgv_alloc(unsigned int block_size, unsigned int block_nr)
{
        int fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
        struct tpacket_req req = {
                .tp_block_size = block_size,   /* 每個 block 的大小 → 決定 buddy order */
                .tp_block_nr   = block_nr,     /* 配幾個 block → 決定一次噴多少 page */
                .tp_frame_size = block_size,
                .tp_frame_nr   = block_nr,
        };
        /* 這一行就會讓 kernel 走 alloc_pg_vec → 從 buddy 拿 block_nr 個 order-N page */
        setsockopt(fd, SOL_PACKET, PACKET_TX_RING, &req, sizeof(req));
        return fd;   /* close(fd) 會把這些 page 還回 buddy */
}
```

`tp_block_size` 決定每格的 buddy order（例如 `block_size = 0x8000` → order 3，8 個 page），`tp_block_nr` 決定一次配幾格。想像的用途：

- **cross-cache 的 drain/refill**（[Ch 22](./22-cross-cache.md)）：先用一批 socket 把某個 order 的 buddy freelist 配乾淨，釋放 victim slab 讓它的 page 回到 buddy，再用 pgv 搶配回那塊 page，於是 pgv 的可控內容就落在原本 victim slab 的實體位置。
- **穩定佔位**：pgv page 的生命週期由 socket fd 控制，`close(fd)` 才釋放，比許多 slab spray 物件更好掌握「何時放、何時收」。
- **可控內容**：把要寫進去的假資料透過 `send()`（TX ring）或直接寫 mmap 區填進 page，佈局精確。

> 很多人把 pgv 當成「只是另一種 heap spray 物件」——錯。它的粒度是 page、來源是 buddy，不受 kmalloc cache 分流（[Ch 27](./27-random-kmalloc-caches.md)）影響，正是它在 cross-cache 情境下比 `msg_msg` 這類 slab 物件更好用的原因。

## 一條完整 USMA 鏈的形狀

把上面拼起來，一條典型 USMA 提權鏈長這樣（具體每一步依你的 bug 而定）：

```
1. 配一個 AF_PACKET socket，設 PACKET_TX_RING → kernel 配出 pg_vec[]（buddy order-N pages）
2. 用你的 bug（OOB/UAF/type confusion）拿到一個能寫到
   struct packet_ring_buffer（或 pg_vec 格）的原語
3. 算出目標 kernel page 的位址：
   - 若目標是 cred：可能先用弱 leak 或 physmap 關係推 cred 所在 page
   - 若目標是 page table / 固定全域（modprobe_path 等）：用 KASLR base + 固定 offset
4. 覆寫 pg_vec[i].buffer（或整條 pg_vec）→ 指向目標 kernel page
5. mmap(socket_fd) → packet_mmap() 把目標 page 映射進 user VMA
6. 在 user space 直接讀寫：
   - 改 cred->uid/gid = 0 → 當場提權
   - 或寫 modprobe_path → 觸發 → root
   - 或把它當任意實體 R/W 的入口
7. cleanup：小心 socket close / page refcount，別讓 kernel 在你用完前
   回收這塊 page 或因 refcount 不一致 panic（見踩雷集錦）
```

## 踩雷集錦

1. **把 USMA 和 Dirty Pagetable 搞混**——兩者都在「映射層」動手腳，但 USMA 改的是「kernel 的合法 mmap 路徑要映射哪塊 page」這個**輸入**（PTE 由 kernel 正規建出，天生合法）；Dirty Pagetable 是**自己動手偽造 PTE**（要湊對 present/user/RW/NX 等 flag）。想清楚你手上的寫原語能寫到哪一層：能寫到 page table page → Dirty Pagetable；能寫到某個「決定映射目標」的 struct 指標 → USMA。
2. **以為 pgv 指向的是 slab object**——`pgv` 指向 **buddy order-N page**，不是 kmalloc-N 的 object。算佈局、估 spray 數量、規劃 cross-cache 時要用 page 的粒度（`tp_block_size` → order），用錯粒度會整個估歪。
3. **struct 佈局寫死**——`struct packet_ring_buffer`、`struct pgv`、`packet_sock` 的欄位 offset 都**依版本變動**，`pg_vec` 在結構裡的位置、每格大小都要用 `pahole`/原始碼對你的 kernel 確認，不要抄別人 writeup 的常數。
4. **忽略 mmap 路徑本身的前提**——不是隨便一個 struct 裡塞個 page 指標就能映射。USMA 要成立，得有一條會真的呼叫 `vm_insert_page`/`remap_pfn_range` 之類、把那個指標指向的 page 攤給 user 的合法路徑（packet socket 的 `packet_mmap`、某 driver 的 `.mmap` f_op）。選載體時先確認它的 `f_op->mmap`/`vm_ops` 會走到你能影響的那個 page 指標。
5. **page refcount / cleanup 弄亂**——你把一塊「本來不屬於這個 ring」的 kernel page 掛進了 user VMA。munmap、socket close、或 kernel 那邊對原 page 的正常釋放，都可能對同一塊 page 的 refcount 做出互相矛盾的增減，導致提前釋放、double free 或 unmap 時 panic。規劃時要想清楚「這塊 page 的 refcount 在映射前後各是誰在加減」，必要時讓 exploit 拿到權限後就不再碰 cleanup，直接把系統停在可用狀態。
6. **目標 page 位址算錯**——USMA 省掉的是「leak 內容」，不是「知道目標 page 在哪」。你仍得算出目標 kernel page 的虛擬位址（透過 physmap 固定關係、KASLR base + offset，或一個弱 leak）。指標指錯 page，mmap 回來的是一塊無關記憶體，白忙一場甚至 fault。

## 動手練習

在自有 QEMU 靶上（機制與流程為主，未實測）：

1. 寫一個最小程式：開 `AF_PACKET` socket、設 `PACKET_TX_RING`、`mmap` 它，確認你能從 user space 讀到 ring 的內容。用 `/proc/<pid>/maps` 看這段 mmap 的範圍，建立「packet ring 被映射進來長什麼樣」的直覺。
2. 用 `pahole` 查你 kernel 的 `struct packet_ring_buffer` 與 `struct pgv`（或等價結構），標出 `pg_vec` 指標的 offset、`pg_vec_order`/`pg_vec_pages` 的位置——這是 USMA 寫原語要瞄準的欄位。
3. page-spray 實驗：寫一個用多個 packet socket + `PACKET_TX_RING` 大量配 order-N page 的程式，搭配 `/proc/buddyinfo` 觀察某個 order 的 free page 數量如何被你抽乾、close 後如何歸還——這就是 cross-cache（[Ch 22](./22-cross-cache.md)）drain/refill 的實驗版。
4. 分析題：給定「一個能覆寫 `packet_ring_buffer.pg_vec` 的 OOB write」，寫出你的 USMA 計畫：目標 page 選誰、位址怎麼算、mmap 後改什麼欄位提權、cleanup 怎麼收尾。
5. 對照題：同一個寫原語，若改成走 Dirty Pagetable（[Ch 23](./23-dirty-pagetable.md)）你要多做哪些事（偽造 PTE 的 flag）、少依賴哪些前提？寫出兩條路的差異表。

## 本章重點整理

- USMA（User-Space Mapping Attack）騙 kernel 透過一條**合法的 mmap 路徑**把某塊 kernel page 映射進 user space，映射成功即得到對該 page 的 user-space 直接 R/W——不需 leak 內容、不需 ROP。
- 和 Dirty Pagetable 的根本差別：USMA 改的是「要映射哪塊 page」的**輸入指標**（PTE 由 kernel 正規建出），Dirty Pagetable 是**自己偽造 PTE**。
- 最經典載體是 `AF_PACKET` 的 `PACKET_MMAP`/`pgv`：ring buffer 由 `alloc_pg_vec` 從 **buddy**（非 SLUB）配 order-N page，`packet_mmap()` 用 `vm_insert_page` 把這些 page 攤給 user；竄改 `pg_vec` 指標即把目標 kernel page 換進映射。
- 對 CFI/KCFI、SMEP/SMAP、FREELIST_HARDENED 免疫，且常不需 KASLR leak——代價是需要一個能寫到「映射目標指標」的精準原語，以及算得出目標 page 位址。
- 同一個 `pgv` 也是最好用的 **page-level spray**：粒度是 page、來源是 buddy、內容可控，是 cross-cache drain/refill 與穩定佔位的主力，別用 slab object 的直覺去想它。

## 自我檢核

- [ ] 能用一句話說清 USMA 和 Dirty Pagetable「動的是誰」的差別
- [ ] 能解釋為什麼 `pgv` 指向的是 buddy page 而非 slab object，以及這對 cross-cache 為何重要
- [ ] 能畫出「竄改 `pg_vec` → `packet_mmap` → user 直接 R/W kernel page」這條鏈
- [ ] 能說出 USMA 為什麼對 CFI 免疫、又為什麼常常不需要 leak kernel 位址
- [ ] 能說出 USMA 需要哪些前提（合法 mmap 路徑、可寫的映射目標指標、算得出目標 page 位址）與主要 cleanup 風險

## 延伸閱讀

- **[USMA: Share Kernel Code With Me](https://i.blackhat.com/Asia-22/Friday-Materials/AS-22-Lin-USMA-Share-Kernel-Code.pdf)** — Zhenpeng Lin，Black Hat Asia 2022
  - **這篇說什麼**：提出並系統化 USMA，示範用 `AF_PACKET`/`pgv` 把 kernel page 映射進 user space 的完整技術。
  - **讀哪裡**：pgv 載體與映射流程那幾頁，以及和既有 heap 技術的對比。
  - **和本章的關聯**：本章的一手來源，把機制與適用條件講到最完整。
  - **前提知識**：[Ch 7](./07-buddy-allocator.md) buddy、[Ch 8](./08-slub-allocator.md) SLUB、[Ch 22](./22-cross-cache.md) cross-cache。
- **[net/packet/af_packet.c（`alloc_pg_vec` / `packet_mmap` / `packet_set_ring`）](https://elixir.bootlin.com/linux/v6.6/source/net/packet/af_packet.c)**
  - **讀哪裡**：`alloc_pg_vec`、`alloc_one_pg_vec_page`（確認走 buddy）、`packet_mmap`（確認 `vm_insert_page`）、`packet_set_ring`。
  - **和本章的關聯**：`pg_vec` 結構、每格大小、mmap 怎麼把 page 攤給 user 的 source 依據——USMA 寫原語瞄準的欄位都在這。
  - **前提知識**：[Ch 4](./04-syscall-boundary.md) 的 socket/setsockopt、[Ch 6](./06-kernel-memory-layout.md) 的 VMA/PTE。
- **[Documentation/networking/packet_mmap.rst](https://elixir.bootlin.com/linux/v6.6/source/Documentation/networking/packet_mmap.rst)**
  - **讀哪裡**：`PACKET_RX_RING`/`PACKET_TX_RING` 與 `tpacket_req` 各欄位的意義。
  - **和本章的關聯**：`tp_block_size`/`tp_block_nr` 怎麼決定 buddy order 與 spray 數量，是 page-spray 調參的依據。
  - **前提知識**：無，但配合上一條 source 一起讀最清楚。
- **[Ch 33 socket / skb 家族](./33-socket-skb.md)** 的 AF_PACKET 段落
  - **和本章的關聯**：那章從「socket 攻擊面」角度看 AF_PACKET，本章從「page 映射/spray」角度看同一個子系統，兩邊互補。

USMA 把 Part 3 的「往下一層找共用面」推到了極致——從 object 到 slab page、到 buddy page、到映射本身。Part 3 的技術到這裡集滿，接下來兩個練習把 Ch 17–25 的武器拼成完整鏈路：練習 C 走「UAF → tty_struct ops hijack」這條經典控制流路線，練習 D 走「cross-cache → Dirty Pagetable」這條現代 data-only 路線。

→ [練習 C — UAF → tty_struct ops hijack 完整鏈](./practice-c-uaf-tty.md)
