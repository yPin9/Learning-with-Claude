# Ch 26 — virtio 資料流與常見 bug 模式

> **目標**：讀懂 virtio device 程式碼時「該盯哪裡」——所有洞都來自 device 端相信了 guest 提供的某個值。

> **環境**：QEMU 9.0 / x86-64 / Linux host

---

## 為什麼需要這個？

Ch 25 建立了 virtio 的架構直覺：virtqueue、vring、descriptor chain、split ring / packed ring。
這一章往攻擊者角度推進一步：**哪裡可以出錯，出錯時會發生什麼。**

傳統 e1000 emulation 的攻擊面相對有限——guest 操控的是 PCI 配置空間和少量 MMIO 暫存器，device 端解讀這些值，範圍受暫存器語意約束。virtio 的設計刻意反過來：為了效能，**guest 直接提供 DMA 位址（GPA）和長度**，device 照著這兩個值把資料搬進搬出 guest 記憶體。

這個設計讓 virtio 比傳統 device emulation 的攻擊面更大。guest 現在能控制：

- descriptor 裡的 `addr`（GPA）
- descriptor 裡的 `len`（宣稱的 buffer 長度）
- descriptor 裡的 `flags`（讀/寫方向、是否有 next）
- descriptor 裡的 `next`（chain 下一個 descriptor 的 index）
- avail ring 裡的 `idx` 和 `ring[]`（告訴 device 有哪些 descriptor 可以用）
- used ring 的 `len` 欄位（device 回填時若 device 自己計算有誤，guest driver 也可能拿到錯誤值）

Device 端每次從 virtqueue 拿一個 element 出來，**這六個欄位全部都是 guest 可控的輸入**。
每個 device 的 request handler 函式，都要先假設這些值是惡意的，再做處理。

---

## 先建立直覺

```
guest 端（惡意控制）                     host 端（QEMU 行程）
─────────────────────────────────────────────────────────────────────
  vring.avail.ring[i] = head_idx    →   virtqueue_pop()
                                             │
                                             ▼
  desc[head_idx].addr  (GPA)  ──────────────→  address_space_map()
  desc[head_idx].len   (宣稱) ──────────────→  VirtQueueElement.iov_len
  desc[head_idx].flags (方向) ──────────────→  iov 進 in_sg / out_sg
  desc[head_idx].next  (鏈)   ──────────────→  走到下一個 desc
                                             │
                                             ▼
                                       VirtQueueElement
                                         .out_num / .in_num
                                         .out_sg[] / .in_sg[]   (iovec 陣列)
                                             │
                                             ▼
                                    device request handler
                                      virtio_blk_handle_request()
                                      virtio_net_receive_rcu()
                                      virtio_gpu_cmd_...()
                                             │
                                             ▼
                                      DMA read / DMA write
                                      ← 每一步都有信任邊界可攻擊
```

一句話總結：攻擊者控制 descriptor chain 裡的 `addr`/`len`/`flags`/`next`，device handler 把這些值直接餵給 DMA 或記憶體操作——device 端沒有充分驗證，洞就在這。

---

## 核心 bug 模式

### Bug 1：addr/len 未充分驗證——越界 DMA

**場景：virtio-blk 的 request 處理**

`hw/block/virtio-blk.c` 的 `virtio_blk_handle_request()` 大致流程：

```c
static int virtio_blk_handle_request(VirtIOBlockReq *req, MultiReqBuffer *mrb)
{
    /* 1. 取出 elem 的第一個 out iov 當 request header */
    if (iov_to_buf(req->elem.out_sg, req->elem.out_num, 0,
                   &req->out, sizeof(req->out)) != sizeof(req->out)) {
        /* header 長度不足，視為 invalid */
        ...
    }
    /* 2. 讀 sector（req->out.sector）決定讀/寫哪一個 block */
    /* 3. 根據請求類型，用 elem->out_sg / elem->in_sg 做 DMA */
}
```

`elem.out_sg` 是從 `virtqueue_pop` 拿出來的 iovec，**addr 和 len 都來自 guest 填的 descriptor**。
`address_space_map()` 會把 GPA 翻成 HVA，但它本身並不保證這個 GPA 範圍完全在 guest RAM 之內——如果 GPA 超出 RAM，翻譯可能落到 MMIO 或 device 暫存器區域（取決於 MemoryRegion 的配置）。

攻擊後果：
- 越界讀：洩漏 host 端 QEMU 行程的記憶體（heap 物件、function pointer）。
- 越界寫：寫到 QEMU heap 上的控制結構，配合後面的 function pointer 劫持。

### Bug 2：length confusion——宣稱長度 vs 實際 iov 長度不符

**場景：virtio-net 的封包接收**

`hw/net/virtio-net.c` 的 `virtio_net_receive_rcu()` 要從 guest 提供的 in_sg 拿 buffer 來放接收到的封包。簡化流程：

```c
virtio_net_receive_rcu(...)
{
    ...
    /* 從 virtqueue 拿 element */
    elem = virtqueue_pop(q->rx_vq, sizeof(VirtQueueElement));
    ...
    /* 計算 iov 實際可以放多少 bytes */
    size = iov_size(elem->in_sg, elem->in_num);
    ...
    /* 寫入封包資料 */
    iov_from_buf(elem->in_sg, elem->in_num, 0, buf, size);
    /* 回報給 guest：用了多少 bytes */
    virtqueue_push(q->rx_vq, elem, total);  /* total 是 device 計算的 */
}
```

問題在 `virtqueue_push` 的 `len` 參數（填入 used ring 的 `len` 欄位）。如果 device 端計算出的 `total` 和 iov 的實際長度不一致，guest driver 拿到 used entry 後，用那個 `len` 決定「這個封包有多大」，就會讀到錯誤邊界。

**CVE-2021-3748** 的根因是 `num_buffers` 欄位在 `address_space_unmap()` 之後才被設定（use-after-unmap，時序問題），會導致 guest 端 driver 把已被 unmap 的記憶體再用一次。根因方向就在這個 length / timing 混亂上。

### Bug 3：descriptor chain 迴圈 / 過長

**場景：device 用 while 走 `VRING_DESC_F_NEXT`**

一個沒有防禦的 device 端 chain 走法：

```c
/* 危險的寫法 */
idx = head;
while (desc[idx].flags & VRING_DESC_F_NEXT) {
    idx = desc[idx].next;
    /* 沒有上限檢查！ */
}
```

Guest 構造循環 chain（desc[0].next = 1, desc[1].next = 0），device 無限迴圈，CPU 100%，DoS。
或者構造超長 chain（2048 個 desc），device 消耗大量記憶體再繼續處理，放大資源消耗。

QEMU 的防禦：`hw/virtio/virtio.c` 的 `virtqueue_pop` 有 `VIRTQUEUE_MAX_SIZE`（256）的上限，走超過就截斷並回報錯誤。但這個防禦要每個 device 都走 `virtqueue_pop` 才能得到——**如果某個 device 自己手動走 desc chain 而不過 `virtqueue_pop`，就要自己加上限**。歷史上有 QEMU device 版本在 slow path 裡自己走 chain、沒有完整地加上限。

### Bug 4：index 越界——avail/used idx 超過 vring size

**場景：`last_avail_idx` 沒有對 vring size 取模**

`hw/virtio/virtio.c` 裡 `virtqueue_pop` 取 avail ring 項目的方式：

```c
/* QEMU 9.0 的防禦寫法 */
head = vring_avail_ring(vq, vq->last_avail_idx % vq->vring.num);
```

`% vq->vring.num` 就是那個保護。少了這個取模，如果 `last_avail_idx` 超過 `vring.num`（vring 的 size，由 guest driver 在初始化時設定，guest 可控），就會索引到 desc table 以外的記憶體——OOB 讀到 host 端的其他資料。

更進一步：`vring.num` 本身也是 guest 提供的（negotiation 過程），有的 QEMU 版本對這個值的上限驗證不完整，導致配置一個超大的 vring，然後用合法 index 存取到 vring 之外的記憶體。

### Bug 5：iov 處理錯誤——iov_cnt 不對 / iov 未完整消耗

**場景：virtio-gpu 的 resource 命令**

`hw/display/virtio-gpu.c` 的 `virtio_gpu_cmd_resource_create_2d()` 大致流程：

```c
static void virtio_gpu_cmd_resource_create_2d(VirtIOGPU *g,
    struct virtio_gpu_ctrl_command *cmd)
{
    struct virtio_gpu_resource_create_2d c2d;
    /* 從 iov 讀 command header */
    VIRTIO_GPU_FILL_CMD(c2d);   /* 展開為 iov_to_buf，讀 sizeof(c2d) bytes */
    /* c2d.width / c2d.height / c2d.format 全是 guest 可控 */
    ...
    res->width = c2d.width;
    res->height = c2d.height;
    /* 配置 pixman image */
    res->image = pixman_image_create_bits(..., c2d.width, c2d.height, ...);
}
```

如果 desc 的長度比 `sizeof(c2d)` 小，`iov_to_buf` 只讀到部分資料，剩餘欄位是 garbage（通常是 0 或前一次殘留值）。Device 端拿到 `width=0`、`height=0` 然後繼續跑，後續邏輯可能對著大小為 0 的 image 做運算——各種 divide-by-zero 或 logic error。

反過來，如果 desc 的長度比期望大，`iov_to_buf` 只讀 `sizeof(c2d)`，但 device 端沒有驗證「剩下的 iov 有沒有被完整消耗」，若後續邏輯假設 iov 已清空就繼續讀，可能讀到 guest 在 header 之後塞的額外資料。

### Bug 6：整數溢位——size 計算

**場景：virtio-gpu resource 的 width × height × bpp 溢位**

```c
/* 未實測，理論預期 */
uint32_t width  = c2d.width;   /* guest 可控，最大 0xFFFFFFFF */
uint32_t height = c2d.height;  /* guest 可控 */
uint32_t bpp    = 4;           /* RGBA，固定 */

size_t size = width * height * bpp;
/* 如果 width=0x10000, height=0x10000：
   0x10000 * 0x10000 = 0x100000000，uint32_t 溢位為 0，
   malloc(0) 或 malloc(4)，後續寫入越界 */
```

virtio-gpu 的歷史 CVE 裡有這類 pattern——device 信任 guest 提供的 `width`/`height`，計算 buffer size 時用 32-bit 乘法，乘積溢位後 `malloc` 分配遠小於需要的 buffer，後續 `memcpy` 越界寫到 heap。

**Guest 完全控制 `width` 和 `height`**，device 端必須在計算乘積之前做範圍檢查和溢位前置偵測（`size > MAX_RESOURCE_SIZE` 或用 checked multiply）。

---

## 底層機制：virtqueue_pop 的信任邊界

```
vring（guest 可寫的共享記憶體，GPA 由 guest 告訴 device）
  │
  │  vring.desc[]    ← addr/len/flags/next 全 guest 可控
  │  vring.avail.idx ← guest 可填任意值
  │  vring.avail.ring[i] ← 指向哪個 desc，guest 可控
  │
  ▼
virtqueue_pop(vq, sz)                         [hw/virtio/virtio.c]
  │
  ├─ 讀 avail.idx → last_avail_idx（% vring.num 取模保護）
  ├─ 讀 avail.ring[i] → head desc index（要驗證 < vring.num）
  ├─ 走 desc chain（上限 VIRTQUEUE_MAX_SIZE = 256）
  │     每個 desc：
  │       .addr  → address_space_map(GPA) → HVA  ← 不保證在 RAM 內
  │       .len   → iov.iov_len                    ← 不保證合理
  │       .flags → 決定進 in_sg 或 out_sg
  │       .next  → 下一個 desc index（要驗證 < vring.num）
  │
  ▼
VirtQueueElement（配置在 QEMU heap）
  .index    : 這個 element 的 head desc index
  .ndescs   : 用了幾個 descriptor
  .out_num  : out_sg（guest→device）的 iov 數量
  .in_num   : in_sg（device→guest）的 iov 數量
  .out_sg[] : iovec 陣列，device 讀這些來拿 guest 送來的資料
  .in_sg[]  : iovec 陣列，device 寫這些把資料送回 guest
  │
  ▼
device request handler（呼叫者必須驗證 iov 大小與邊界！）
```

`out_sg` 是 guest→device 方向（device 從這裡讀 request header 和資料），`in_sg` 是 device→guest 方向（device 把回應寫進這些 buffer）。**兩個方向的 addr 和 len 都來自 guest——device handler 每次使用前都要驗證。**

`VirtQueueElement` 結構（`include/hw/virtio/virtio.h`）：

```c
typedef struct VirtQueueElement {
    unsigned int index;                   /* head descriptor index */
    unsigned int ndescs;                  /* 這次 pop 用掉的 desc 數量 */
    unsigned int out_num;                 /* out_sg 陣列的有效元素數 */
    unsigned int in_num;                  /* in_sg 陣列的有效元素數 */
    hwaddr *in_addr;                      /* in buffer 的 GPA（用來 unmap） */
    hwaddr *out_addr;                     /* out buffer 的 GPA */
    struct iovec *in_sg;                  /* device→guest 的 iov 陣列 */
    struct iovec *out_sg;                 /* guest→device 的 iov 陣列 */
} VirtQueueElement;
```

---

## 對比與取捨

| bug 模式 | 觸發難度 | 影響 | QEMU 9.0 現有防禦 | CVE 例子 |
|---|---|---|---|---|
| addr/len 越界 DMA | 低（直接填 desc） | OOB r/w，可洩漏/寫 host heap | `address_space_map` 做 GPA→HVA，但不保證在 RAM 內 | CVE-2015-5745（virtio-net OOB write） |
| length confusion | 中（要理解 used len 語意） | 後續操作大小錯誤 | device 端需自行驗證 iov_size | CVE-2021-3748（virtio-net UAF，根因涉及此方向） |
| chain 迴圈/過長 | 低（構造循環 next） | DoS，無限迴圈 | `virtqueue_pop` 有 256 上限，但 device 手動走 chain 時無保護 | CVE-2019-14835（vhost-net chain 長度不足） |
| avail/used idx 越界 | 中（需控制 vring.num 或 idx） | OOB 讀 desc table | `% vq->vring.num` 取模（需 vring.num 本身合法） | 無廣為人知單一 CVE，但歷史多次被研究 |
| iov_cnt 錯誤/未消耗 | 中（要理解 iov 語意） | Garbage 讀入，邏輯錯誤 | `iov_to_buf` 回傳值檢查（各 device 實作品質不一） | virtio-gpu 多個 command handler 歷史 bug |
| 整數溢位 size 計算 | 低（填超大 width/height） | heap underalloc → OOB write | QEMU 9.0 virtio-gpu 已加 `check_resource_limits` | CVE-2021-3545（virtio-gpu resource 整數溢位）未實測，理論預期 |

---

## 踩雷集錦

**「`virtqueue_pop` 已經驗證 addr 合法性」（錯）**
→ 它把 GPA→HVA 的翻譯外包給 `address_space_map`，但不保證那個 GPA range 完全在 guest RAM 之內。若 GPA 指向 MMIO region 或超出 RAM，翻譯結果因 MemoryRegion 設定而異——device 端拿到的 HVA 不是 NULL，是某個奇怪的映射位址。

**「`iov_cnt` 和 iov 的實際 bytes 一一對應」（錯）**
→ `out_num`/`in_num` 是 iov 陣列的**元素個數**（幾條 iovec），每條 iovec 的 `iov_len` 才是該條的 bytes。總 bytes 要用 `iov_size(sg, num)` 計算。直接把 `out_num` 當成 bytes 用——這是初學者高頻錯誤，device 程式碼裡也出現過類似混淆。

**「desc chain 長度有上限 256，所以不用怕無限迴圈」（錯）**
→ 這個上限只有走 `virtqueue_pop` 才能得到。歷史上有 QEMU device 在 slow path 或特殊模式（packed ring）裡自己手動走 chain，而沒有繼承這個保護。讀 device 程式碼時，要確認它走 chain 的路徑**都**過 `virtqueue_pop`，或者自己有等效的計數器。

**「used ring 的 `len` 欄位只是回報用的，不影響 device 端行為」（錯）**
→ Guest driver 拿到 used entry 後，用 `len` 決定「device 寫了多少 bytes 進 in buffer」——例如 virtio-net driver 用這個值決定 sk_buff 的大小。Device 端計算 `len` 出錯，guest 端就用錯誤的大小做後續操作，可能在 guest 端觸發漏洞；或者反過來，攻擊者利用 guest driver 對 `len` 的信任，設計出特定的 guest-side 記憶體損壞。

**「virtio-gpu 是 display device，不是安全攻擊面」（錯）**
→ virtio-gpu 從 guest 接收幾十種 command（`VIRTIO_GPU_CMD_RESOURCE_CREATE_2D`、`VIRTIO_GPU_CMD_RESOURCE_ATTACH_BACKING`、`VIRTIO_GPU_CMD_TRANSFER_TO_HOST_2D`…），每個 command 都要解析 guest 提供的 header，裡面有大量整數欄位（width/height/format/offset/length）。這個 command 介面比一般網卡的封包格式複雜得多，是高價值攻擊面。QEMU 9.0 的 `hw/display/virtio-gpu.c` 超過 2000 行。

---

## 進階：再往深一層

閱讀 virtio device 程式碼時的 auditing checklist：

1. **每個 `iov_to_buf` / `iov_from_buf` 呼叫前，有沒有先用 `iov_size` 驗證長度夠用？**
   缺少這個檢查，短 iov 就會讀到 header 以外的記憶體（QEMU 行程的其他資料）。

2. **`width`/`height`/`size`/`length` 等 guest 可控整數，乘法之前有沒有做溢位檢查？**
   目標：找 `a * b`，其中 a 和 b 都來自 VirtQueueElement 的 iov 資料，且沒有 `MIN()` 或 `check_resource_limits` 前置護欄。

3. **`virtqueue_pop` 回傳值有沒有 NULL 檢查？**
   若 avail ring 是空的（或 guest 刻意讓它看起來空），`virtqueue_pop` 回傳 NULL，接著不檢查就 dereference 是 NULL deref。這是另一個 DoS 路徑。

4. **device 有沒有自己手動走 desc chain？**
   搜 `.next` 或 `VRING_DESC_F_NEXT` 在 `hw/` 下的出現位置，如果有 device 在 `virtqueue_pop` 以外自己走 chain，要看它有沒有計數器上限。

5. **`virtqueue_push` 的 `len` 參數是怎麼算的？**
   這個值填到 used ring 給 guest driver 用。Device 端如果少算或多算，guest driver 會拿到錯誤大小——guest driver 的 bug 也在攻擊面內（尤其是 guest-kernel exploit 之後再打 guest-host 邊界的場景）。

6. **時序問題：`address_space_map` 和 `address_space_unmap` 之間，有沒有存取已 unmap 的指標？**
   CVE-2021-3748 的教訓：把某個欄位的賦值挪到 `unmap` 之後，就觸發 use-after-unmap。Device handler 裡，`unmap` 到 `virtqueue_push` 之間的程式碼要仔細看。

7. **packed ring 路徑有沒有和 split ring 路徑一樣嚴謹？**
   QEMU 後來加入 packed ring 支援（virtio 1.1），部分 device 的 packed ring 處理路徑比 split ring 更晚加，歷史上防禦可能不完整。認 `VIRTIO_F_RING_PACKED` feature bit 的 device 都要雙路徑檢查。

8. **vhost-user 的信任模型轉移**：下一章（Ch 27）會詳展——當 device 搬到獨立 process（`vhost-user`），原本在 QEMU 行程裡的 host/guest 信任邊界變成兩個 process 之間的 UNIX socket 邊界，攻擊面的性質又不同。

---

## 動手練習

### 練習 1：找 virtio-blk iov 長度驗證

讀 QEMU 9.0 的 `hw/block/virtio-blk.c`，找 `virtio_blk_handle_request()`：

- 找出 request header（`VirtIOBlockOutHdr`）的讀取是否有長度前置驗證——`iov_to_buf` 之前有沒有先確認 `iov_size(out_sg, out_num) >= sizeof(VirtIOBlockOutHdr)`？
- 如果 guest 把第一個 out desc 的 `len` 設成 4（小於 `sizeof(VirtIOBlockOutHdr)` = 16），`iov_to_buf` 回傳值是 4 還是 16？Device 端怎麼處理這個 short read？
- `data_iov`（讀寫資料的 iov）有沒有被額外的 sector 邊界檢查？`req->out.sector + iov_total / BDRV_SECTOR_SIZE` 有沒有和 disk 大小做比較？

記錄你找到的驗證點，以及你認為有沒有可能被繞過的路徑。

### 練習 2：追 virtio-gpu 整數溢位防護

讀 QEMU 9.0 的 `hw/display/virtio-gpu.c`：

- 找 `virtio_gpu_cmd_resource_create_2d()`，追 `c2d.width`、`c2d.height`、`c2d.format` 從 iov 讀出之後的第一個使用點。
- 找 `virtio_gpu_check_resource_limits()`（若存在），看它對 `width` 和 `height` 的上限是多少。
- 計算：如果 `width = 0x4001`、`height = 0x4001`、`bpp = 4`，乘積是多少 bytes？現有的上限能不能擋住這個值？
- 如果不存在 `check_resource_limits`，找 pixman 在分配 image 之前有沒有做類似的 sanity check。

### 練習 3：設計 malicious descriptor chain（紙上）

目標：讓 `virtio_net_receive_rcu()` 拿到錯誤的 iov 長度。

設計一個 descriptor chain，讓 device 端在呼叫 `virtqueue_pop` 之後，得到的 `VirtQueueElement` 的 `in_sg` 描述一個長度為 0（或遠小於需要）的 buffer，但 device 端不觸發早期 return，繼續執行到 `iov_from_buf`。

需要說明：
1. `vring.desc[]` 的具體值（index、addr、len、flags、next）
2. `vring.avail.ring[]` 要填什麼
3. Device 端的哪個驗證會（或不會）擋住這個 chain
4. 如果不被擋住，`iov_from_buf` 調用時的 `buf_size` vs `iov` 實際長度的差距是多少

不需要真正實現——目標是確認你對 `virtqueue_pop` 到 `iov_from_buf` 這條路徑的每一步有清晰的模型。

---

## 本章重點整理

1. Virtio 的攻擊面比傳統 device emulation 更大：guest 直接提供 DMA 位址和長度，`addr`/`len`/`flags`/`next` 全部 guest 可控。
2. 六個核心 bug 模式：addr/len 越界、length confusion、chain 迴圈、idx 越界、iov 處理錯誤、整數溢位——都可以歸結到「device 相信了 guest 給的某個值」。
3. `virtqueue_pop` 把 guest 可控的 desc 轉成 `VirtQueueElement` 的 `in_sg`/`out_sg`，但 iov 裡的 addr 和 len 並未被完整驗證。
4. `iov_cnt`（元素數）和 iov bytes（`iov_size` 的結果）是兩件事，混淆這兩個就是 bug。
5. Device 端要在**使用** iov 資料之前，先驗證長度夠用、整數不溢位、index 在範圍內——這是每個 request handler 的責任，`virtqueue_pop` 不代勞。
6. Auditing checklist 的八個查核點是閱讀任何新 virtio device 程式碼的起點。

---

## 自我檢核

- [ ] 我能說出 `out_sg` 和 `in_sg` 的方向差異，以及各自對應攻擊者的哪個能力（讀 vs 寫）。
- [ ] 我能解釋為什麼 `virtqueue_pop` 有 256 上限，但 chain 迴圈問題在某些情況下還是存在。
- [ ] 我能指出 `iov_cnt` 和 `iov_size()` 結果的差異，並舉例說明混淆兩者會出現什麼 bug。
- [ ] 我能描述 CVE-2021-3748 的根因方向（不需要完整 exploit，概念層級夠用）。
- [ ] 我能在 QEMU 9.0 的 `hw/block/virtio-blk.c` 裡找到 request header 的讀取點，並判斷有沒有前置長度驗證。
- [ ] 我理解 vhost-user 的信任模型和一般 QEMU virtio 的差異（方向），準備好進 Ch 27。

---

## 延伸閱讀

- **QEMU 原始碼，閱讀順序建議**：
  - `hw/virtio/virtio.c`：`virtqueue_pop`、`virtqueue_push`、`virtqueue_flush` 的完整實作
  - `hw/block/virtio-blk.c`：`virtio_blk_handle_request`，最簡單的 request handler，適合第一個讀
  - `hw/net/virtio-net.c`：`virtio_net_receive_rcu`，封包路徑，length confusion 的典型現場
  - `hw/display/virtio-gpu.c`：最複雜，command dispatch 加數十個 handler，整數溢位的溫床
  - `include/hw/virtio/virtio.h`：`VirtQueueElement` 結構定義

- **CVE-2021-3748**：Red Hat Bugzilla #2024312，「QEMU: virtio-net: heap use-after-free in virtio_net_receive_rcu」。讀 patch diff 和 comment，理解 `num_buffers` 賦值時序問題怎麼被修掉。

- **virtio specification section 2.6**（Virtqueues）：OASIS virtio-v1.1 spec，定義 split ring 和 packed ring 的記憶體佈局、avail/used ring 的語意、`len` 欄位的意義。Device 和 driver 各自的責任在這裡有明確界定。

- **virtio specification section 2.7**（Packed Virtqueues）：packed ring 的 desc flags 語意和 split ring 不同，QEMU device 的 packed ring 程式碼路徑和 split ring 是分開的，都要 audit。

- **QEMU security advisories**：`https://www.qemu.org/docs/master/about/security-process.html`，歷年 virtio 相關 advisory 有完整列表，按 device 類型分類後是絕佳的 bug pattern 資料庫。

- **「VIRTIO device fuzzing」相關研究**：搜尋 Syzkaller 對 virtio device 的 fuzzing 設定（`virtio_blk_fuzzer`、`virtio_net_fuzzer`），理解自動化 fuzzer 怎麼建構 malicious desc chain——這和你在練習 3 做的紙上設計是同一件事的自動化版本。

- **VirtIO 1.1 spec feature bits**：`VIRTIO_F_RING_PACKED`、`VIRTIO_F_IN_ORDER`——這些 feature 改變了 desc 的走法和 used ring 的更新方式，device 支援這些 feature 的程式碼路徑是額外的 audit 目標。

---

下一章進入 vhost-user 架構：當 device emulation 搬到 QEMU 行程以外的獨立 process，host/guest 信任邊界變成兩個 host process 之間的 UNIX socket 邊界，攻擊面的性質和利用手段都有根本性的改變。

→ [Ch 27](./27-vhost-user.md)
