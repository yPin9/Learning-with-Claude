# Ch 10 — MemoryRegion 與 guest 物理位址空間

> **目標**：搞懂 QEMU 如何用 MemoryRegion 樹描述 guest 的非連續物理位址空間，以及 FlatView 如何把這棵樹壓平成可快速查找的 dispatch table——這是理解所有 device MMIO 攻擊面的前置知識。

---

## 為什麼需要這個？

很多人一開始以為 QEMU 就是把 `malloc()` 一塊大記憶體當成 guest RAM，然後 guest GPA（Guest Physical Address）直接對應 host 的偏移。這個模型在只有 RAM 的時候勉強說得通，但現實的 x86 guest 物理位址空間長這樣：

```
GPA 0x00000000 - 0x0009FFFF   640 KB RAM（傳統低端記憶體）
GPA 0x000A0000 - 0x000BFFFF   VGA framebuffer MMIO
GPA 0x000C0000 - 0x000FFFFF   Option ROM / BIOS shadow
GPA 0x00100000 - 0xBFFFFFFF   Main RAM（1MB - 3GB）
GPA 0xC0000000 - 0xFFFFFFFF   PCI hole：MMIO 裝置、APIC、PCIe config space
GPA 0x100000000 起             4GB 以上的延伸 RAM（如果 guest > 3GB）
```

這張「地圖」有幾個特性：
1. **非連續**：RAM 在中間被 PCI hole 切斷，不能用一塊連續 mmap 表達。
2. **異質**：有些位址是真正的記憶體（讀寫快、host 直接存取），有些是 MMIO（讀寫要 trap 到裝置 callback）。
3. **動態**：熱插拔記憶體、device hotplug、region enable/disable 都會改變這張地圖。
4. **有優先級**：同一個 GPA 可能被多個 region 覆蓋（e.g., ROM 覆蓋在 RAM 上面），高優先級的勝。

QEMU 需要一套資料結構描述這張地圖，並且能在「給定 GPA」時快速找到對應的處理器——是去 host RAM 直接讀寫，還是呼叫某個裝置的 callback。這就是 **MemoryRegion** 系統存在的原因。

---

## 先建立直覺

### MemoryRegion 樹 vs FlatView

```
MemoryRegion 樹（邏輯層，允許重疊、有父子關係）
─────────────────────────────────────────────────
system_memory（container，根節點）
├── ram-below-4g          [0x00000000 - 0xBFFFFFFF]  RAM region
│   └── (alias) lowmem    [0x00000000 - 0x0009FFFF]  → ram-below-4g 的前 640KB
├── vga-mmio              [0x000A0000 - 0x000BFFFF]  MMIO region（priority 高於 RAM）
├── pci-memory            [0xC0000000 - 0xFFFFFFFF]  container
│   ├── e1000-mmio        [0xC0010000 - 0xC001FFFF]  MMIO region
│   ├── virtio-mmio       [0xC0020000 - 0xC002FFFF]  MMIO region
│   └── pcie-ecam         [0xE0000000 - 0xEFFFFFFF]  MMIO region
└── ram-above-4g          [0x100000000 - ...]         RAM region（若 guest > 3GB）

system_io（另一棵樹，IN/OUT port 空間，16-bit 位址）
├── i8259-io              [0x20 - 0x21]
├── ide-io                [0x1F0 - 0x1F7]
└── ...
```

```
FlatView（壓平後，無重疊線段表，實際用於 dispatch）
──────────────────────────────────────────────────
[0x00000000 - 0x0009FFFF]  → RAM (ram-below-4g + lowmem alias 解析後)
[0x000A0000 - 0x000BFFFF]  → MMIO (vga-mmio，priority 覆蓋 RAM)
[0x000C0000 - 0x000FFFFF]  → ROM/shadow
[0x00100000 - 0xBFFFFFFF]  → RAM (ram-below-4g 主體)
[0xC0010000 - 0xC001FFFF]  → MMIO (e1000-mmio)
[0xC0020000 - 0xC002FFFF]  → MMIO (virtio-mmio)
...（其餘 PCI hole 的 gap 在沒有裝置時標 UNASSIGNED）
[0x100000000 - ...]         → RAM (ram-above-4g)

每個線段記錄：
  - 起始/終止 GPA
  - 對應哪個 MemoryRegion
  - 在該 region 內的偏移（用於 RAMBlock 定位或 MMIO offset）
```

樹是「人類/裝置驅動視角」，FlatView 是「執行時 dispatch 視角」。每次樹改變，QEMU 就重新計算 FlatView。

---

## MemoryRegion 四種類型

### 1. RAM region — `memory_region_init_ram()`

```c
/* include/exec/memory.h（QEMU 9.0） */
bool memory_region_init_ram(MemoryRegion *mr,
                             Object *owner,
                             const char *name,
                             uint64_t size,
                             Error **errp);
```

底層實際做的事：

```c
/* system/physmem.c（QEMU 9.0，舊版在 exec.c） */
RAMBlock *qemu_ram_alloc(ram_addr_t size,
                          bool share,
                          MemoryRegion *mr,
                          Error **errp)
{
    /* 透過 mmap(MAP_ANONYMOUS | MAP_PRIVATE) 或 memfd_create() 分配 */
    /* 回傳 RAMBlock，記錄 host_addr（host 虛擬位址）+ offset（在全域 ram_list 的偏移） */
}
```

重點：RAM region 讀寫不走 callback，KVM 在 guest page table walk 完成後直接讓 CPU 存取 host 的 mmap 區域（透過 KVM memory slot 機制，Ch 9 提過）。速度最快，幾乎無 QEMU 干預。

**RAMBlock** 是真正持有 host 記憶體的結構：

```c
/* include/exec/ramblock.h（QEMU 9.0） */
struct RAMBlock {
    struct rcu_head rcu;
    struct MemoryRegion *mr;
    uint8_t *host;          /* host 虛擬位址（mmap 回傳） */
    uint8_t *colo_cache;    /* COLO 用，一般忽略 */
    ram_addr_t offset;      /* 在全域 ram_list 的偏移 */
    ram_addr_t used_length;
    ram_addr_t max_length;
    ...
    int fd;                 /* memfd 或 hugepage fd，-1 表示 anonymous */
    ...
};
```

### 2. MMIO region — `memory_region_init_io()`

```c
/* include/exec/memory.h */
void memory_region_init_io(MemoryRegion *mr,
                            Object *owner,
                            const MemoryRegionOps *ops,
                            void *opaque,
                            const char *name,
                            uint64_t size);
```

`opaque` 是裝置的私有狀態指標（e.g., 指向 `E1000State *`）。每次 guest 存取這個 region，QEMU 就呼叫 `ops->read` 或 `ops->write`，並把 `opaque` 傳進去。

**MemoryRegionOps 結構**（這個很重要，是攻擊面的核心）：

```c
/* include/exec/memory.h */
struct MemoryRegionOps {
    /* 讀取 callback：offset 是在此 region 內的偏移，size 是存取寬度（1/2/4/8 bytes） */
    uint64_t (*read)(void *opaque, hwaddr offset, unsigned size);

    /* 寫入 callback：data 是要寫入的值 */
    void (*write)(void *opaque, hwaddr offset, uint64_t data, unsigned size);

    /* 舊式 MMIO 用，QEMU 9.0 仍保留但不建議新裝置使用 */
    MemTxResult (*read_with_attrs)(void *opaque, hwaddr offset, uint64_t *data,
                                    unsigned size, MemTxAttrs attrs);
    MemTxResult (*write_with_attrs)(void *opaque, hwaddr offset, uint64_t data,
                                     unsigned size, MemTxAttrs attrs);

    enum device_endian endianness;

    struct {
        /* guest 被允許用的存取寬度（min/max_access_size，單位 bytes） */
        /* 超出範圍的存取會被 QEMU 攔截，不傳到 callback */
        unsigned min_access_size;
        unsigned max_access_size;
        bool unaligned;             /* 是否允許非對齊存取 */
    } valid;

    struct {
        /* callback 真正能處理的存取寬度 */
        /* 如果 guest 用 1-byte 存取，但 impl.min=4，QEMU 會先讀 4 bytes 再取對應 byte */
        unsigned min_access_size;
        unsigned max_access_size;
        bool unaligned;
    } impl;
};
```

**`valid` vs `impl` 的差異**：

- `valid`：定義 guest「被允許」用什麼寬度存取這個 region。如果 guest 發出不合法的存取寬度，QEMU 可以直接報 guest error 或靜默忽略（取決於裝置實作）。
- `impl`：callback 實際能處理的寬度。QEMU 會自動拆解或合併請求，讓 callback 只看到它能處理的寬度。

例如：e1000 某些暫存器只能 4-byte 存取（`impl.min=impl.max=4`），但 guest 做了 1-byte write，QEMU 會先讀原始 4-byte 值，把那 1-byte 填進去，再用 4-byte 呼叫 write callback。這個「QEMU 幫你合併」的過程在 `memory.c` 的 `memory_region_dispatch_write()` 裡。

### 3. alias region — `memory_region_init_alias()`

```c
void memory_region_init_alias(MemoryRegion *mr,
                               Object *owner,
                               const char *name,
                               MemoryRegion *orig,     /* 原始 region */
                               hwaddr offset,          /* 在原始 region 的起始偏移 */
                               uint64_t size);
```

alias 本身沒有資料或 callback，它只是「把 orig 的 [offset, offset+size) 這段，貼到另一個 GPA」。常見用法：

- 把 4GB 以上的 RAM 的一部分 alias 到 3GB 以下（處理 BIOS 的 low memory alias）
- PCI BAR remapping：BAR 位址移動時不用重建整個 region，只要更新 alias 的掛載位置

### 4. container region — `memory_region_init()`

```c
void memory_region_init(MemoryRegion *mr,
                         Object *owner,
                         const char *name,
                         uint64_t size);
```

純邏輯父節點，自己沒有 RAM 也沒有 callback，只是用來把一群子 region 組織在同一個命名空間下。system_memory 本身就是一個 container。

---

## AddressSpace：system_memory 與 system_io

QEMU 有兩個主要的 AddressSpace（位址空間）：

```c
/* system/memory.c（QEMU 9.0） */
static MemoryRegion *system_memory;   /* guest 物理位址空間，GPA */
static MemoryRegion *system_io;       /* x86 IN/OUT port 空間，16-bit */

AddressSpace address_space_memory;    /* 對應 system_memory */
AddressSpace address_space_io;        /* 對應 system_io */
```

**DMA 操作的入口**（預告 Ch 13）：

```c
/* include/exec/memory.h */
MemTxResult address_space_rw(AddressSpace *as, hwaddr addr,
                              MemTxAttrs attrs, void *buf,
                              hwaddr len, bool is_write);

MemTxResult address_space_read(AddressSpace *as, hwaddr addr,
                                MemTxAttrs attrs, void *buf, hwaddr len);

MemTxResult address_space_write(AddressSpace *as, hwaddr addr,
                                 MemTxAttrs attrs, const void *buf, hwaddr len);
```

當裝置要做 DMA（e.g., virtio-blk 把資料寫進 guest RAM），它呼叫 `address_space_write(&address_space_memory, guest_addr, ...)`。QEMU 用 FlatView dispatch table 找到對應的 RAM region，直接寫 host mmap 位址——這比 trap-and-emulate 快得多，但也是為什麼 DMA 出界（超出 guest RAM 邊界）能直接寫到 QEMU heap 上的原因（Ch 13 的攻擊核心）。

---

## 底層機制：FlatView 與 dispatch

### FlatView 的計算

每次 MemoryRegion 樹改變（`memory_region_add_subregion()`、`memory_region_del_subregion()`、`memory_region_set_enabled()`），QEMU 就觸發 `memory_map_init()` → `generate_memory_map()` → 重新計算 FlatView。

```c
/* system/memory.c（QEMU 9.0，舊版 softmmu/memory.c） */

/* 把 MemoryRegion 樹遞迴壓平成 FlatRange 陣列 */
static void render_memory_region(FlatView *view,
                                  MemoryRegion *mr,
                                  Int128 base,         /* 此 region 在父空間的起始 GPA */
                                  AddrRange clip,      /* 裁剪範圍 */
                                  int priority,
                                  bool readonly, bool nonvolatile)
{
    /* 遞迴處理子 region，高 priority 的後插入（會覆蓋低 priority） */
    /* 最終產生一組無重疊的 FlatRange */
}

/* 從 FlatView 產生 coalesced MMIO 資訊（給 KVM 的 ioeventfd 用） */
static void generate_coalesced_mmio(FlatView *view) { ... }
```

FlatView 結構：

```c
/* include/exec/memory.h */
struct FlatView {
    struct rcu_head rcu;
    unsigned ref;
    FlatRange *ranges;    /* 排序好的 FlatRange 陣列 */
    unsigned nr;          /* 數量 */
    unsigned nr_allocated;
    struct AddressSpaceDispatch *dispatch;   /* dispatch table */
};

struct FlatRange {
    MemoryRegion *mr;
    hwaddr offset_in_region;   /* 這段在 mr 內的起始偏移 */
    AddrRange addr;            /* [start, start+size)，GPA */
    uint8_t dirty_log_mask;
    bool romd_mode;
    bool readonly;
    bool nonvolatile;
};
```

### Dispatch table 的查找

```c
/* system/memory.c */
MemoryRegionSection flatview_translate(FlatView *fv, hwaddr addr,
                                        hwaddr *xlat, hwaddr *plen,
                                        bool is_write, MemTxAttrs attrs)
{
    /* 二分搜尋 fv->ranges，找到包含 addr 的 FlatRange */
    /* 回傳 MemoryRegionSection：指向對應 MemoryRegion + 在 region 內的偏移 */
}
```

給定 GPA → `flatview_translate()` 二分搜尋 → 找到 FlatRange → 拿到 MemoryRegion + offset。如果是 RAM，直接算 host 位址（`mr->ram_block->host + offset_in_region + (addr - range->addr.start)`）；如果是 MMIO，呼叫 `mr->ops->read/write`。

### FlatView 更新的鎖

```
MemoryRegion 樹改變
      ↓
memory_region_transaction_begin()    ← 保護期間不觸發 listener
      ↓ （改變若干 region）
memory_region_transaction_commit()
      ↓
address_space_update_topology()      ← 計算新 FlatView
      ↓
address_space_update_topology_pass() ← diff 新舊 FlatView，呼叫 listener
      ↓
kvm_region_add/del                   ← 更新 KVM memory slot
      ↓
RCU 切換到新 FlatView（讀者不需要鎖，RCU 保護）
```

FlatView 用 RCU（Read-Copy-Update）保護：讀者（vCPU thread、DMA thread）不需要鎖，寫者（改 region 樹）在 commit 後用 `call_rcu()` 釋放舊 FlatView。

---

## 對比與取捨

| 類型 | 初始化函數 | 資料來源 | 存取速度 | 典型用途 |
|------|-----------|----------|----------|---------|
| RAM | `memory_region_init_ram()` | host mmap（RAMBlock） | 極快（直接 host page table） | guest 主記憶體 |
| MMIO | `memory_region_init_io()` | callback（MemoryRegionOps） | 慢（每次 trap 到 QEMU） | 裝置暫存器、PCI BAR |
| alias | `memory_region_init_alias()` | 指向原始 region | 同原始 region | BAR remapping、low memory 映射 |
| container | `memory_region_init()` | 無（純邏輯） | N/A | 組織子 region |

**KVM 的影響**：KVM memory slot（`kvm_set_user_memory_region()`）只能對應到 RAM region——因為 KVM 需要 host 的實體位址（HPA）才能建 EPT/NPT entry。MMIO region 不在 KVM memory slot 裡，guest 存取 MMIO GPA 時 EPT/NPT miss，才能 VM-exit 到 QEMU 處理。這是為什麼 MMIO 慢、RAM 快的根本原因。

---

## 踩雷集錦

**1. `valid` 和 `impl` 都沒設，預設是 1-8 byte 全接受**

如果 MemoryRegionOps 沒有設 `valid` 和 `impl`，QEMU 預設 `min_access_size=1, max_access_size=8`。裝置 callback 必須自己處理各種 size 的存取，包括 1-byte、2-byte……很多舊裝置 callback 直接假設 size==4，對 size 1/2/8 的存取行為未定義——這是一類 bug 的來源（寫 size=8 讓 callback 做出預期外的事）。

**2. FlatView 是快照，不是即時的**

`flatview_translate()` 拿到的是 RCU protected 的舊 FlatView 快照。如果你在 `address_space_read()` 期間另一個 thread 改了 MemoryRegion 樹，你看到的 FlatView 是改之前的版本——這是設計上的妥協（無鎖讀），通常 OK，但在寫攻擊 PoC 時要注意 region 改變的時間視窗。

**3. alias offset 越界不會自動裁剪**

`memory_region_init_alias()` 的 `offset + size` 如果超過 orig 的大小，QEMU 不一定會在初始化時報錯（取決於版本），但 FlatView 計算時會產生奇怪的結果。裝置驅動中的 alias 設定錯誤偶爾導致 guest 能存取預期外的 region 範圍。

**4. `memory_region_add_subregion_overlap()` 的 priority 邏輯**

`priority` 值越大越優先（高 priority 覆蓋低 priority），不是越小越優先。很多人反過來記。預設 `memory_region_add_subregion()` 用 priority 0，裝置若要覆蓋 RAM 要用正數 priority（e.g., VGA MMIO 用 priority 1 覆蓋 RAM）。

**5. QEMU 8.x vs 9.x 路徑改變**

QEMU 9.0 把大量檔案從 `softmmu/` 移到 `system/`：
- `softmmu/memory.c` → `system/memory.c`
- `exec.c`（巨無霸） → 拆分到 `system/physmem.c`（RAMBlock 相關）等

在 git log 或看舊文章時要注意這個路徑差異，函數名稱通常不變，只是搬家。

---

## 進階：再往深一層

### RAMBlock 與 dirty bitmap

RAMBlock 不只持有 `host` 指標，還維護一個 dirty bitmap：

```c
/* include/exec/ramblock.h */
struct RAMBlock {
    ...
    /* dirty bitmap，每個 bit 對應一個 host page（4KB） */
    /* KVM 透過 KVM_GET_DIRTY_LOG ioctl 取得哪些 page 被 guest 寫過 */
    unsigned long *bmap;
    ...
};
```

dirty bitmap 是 live migration 的基礎：第一輪把所有 RAM 傳到目標機，第二輪只傳 dirty page（被 guest 寫過的），反覆縮小 dirty set 直到可以 switchover。從安全角度看，dirty bitmap tracking 可以被 guest 干擾——如果 guest 能觸發大量 page dirty，就能讓 migration 收斂變慢（DoS）。

### `memory_region_init_ram_shared_nomigrate()`

```c
/* system/memory.c */
bool memory_region_init_ram_shared_nomigrate(MemoryRegion *mr,
                                              Object *owner,
                                              const char *name,
                                              uint64_t size,
                                              bool share,
                                              Error **errp);
```

`share=true` 時用 `mmap(MAP_SHARED)` 而不是 `MAP_PRIVATE`，允許 host 上其他 process 共享這塊記憶體（e.g., vhost-user 裝置）。`nomigrate` 意味這塊 RAM 不參與 live migration（大型共享記憶體區域通常不搬）。這在 vhost-user 架構（Ch 14 預告）裡很重要：virtio frontend 和 backend 共享同一塊 memory region，zero-copy。

### MemoryListener：region 變更的觀察者

```c
/* include/exec/memory.h */
struct MemoryListener {
    void (*region_add)(MemoryListener *listener, MemoryRegionSection *section);
    void (*region_del)(MemoryListener *listener, MemoryRegionSection *section);
    void (*region_nop)(MemoryListener *listener, MemoryRegionSection *section);
    /* ... 更多 callback ... */
    QTAILQ_ENTRY(MemoryListener) link;
};
```

KVM 的 `kvm_memory_listener` 就是一個 MemoryListener——它在 `region_add` 時呼叫 `ioctl(KVM_SET_USER_MEMORY_REGION)` 把 RAM region 的 host 位址告訴 kernel。類似地，vhost 有自己的 listener。如果你在做記憶體相關的 fuzzing 或分析，這些 listener 是 GPA → HPA 映射更新的關鍵路徑。

---

## 動手練習

### 練習 1：觀察 MemoryRegion 樹（QEMU monitor）

（Linux 環境，未實測，理論預期）

啟動 QEMU 後進入 monitor（`Ctrl-A C` 或 `-monitor stdio`）：

```
(qemu) info mtree
```

應該能看到類似：

```
address-space: memory
  0000000000000000-ffffffffffffffff (prio 0, i/o): system
    0000000000000000-00000000bfffffff (prio 0, ram): alias ram-below-4g @pc.ram 0000000000000000
    00000000000a0000-00000000000bffff (prio 1, i/o): vga-lowmem
    ...
    00000000c0000000-00000000ffffffff (prio 0, i/o): alias pci @pc.pci 00000000c0000000
```

`info mtree -f` 顯示 FlatView（壓平後的版本）。對比兩者，找到 priority 覆蓋的案例。

### 練習 2：追蹤 MMIO 存取

（Linux 環境，未實測，理論預期）

用 QEMU 的 trace 功能：

```bash
qemu-system-x86_64 \
    -trace "memory_region_ops_read,memory_region_ops_write" \
    -trace file=/tmp/qemu-trace.log \
    [其他參數]
```

或者在 QEMU 原始碼加 `printf` 到 `memory_region_dispatch_read()` / `memory_region_dispatch_write()`（`system/memory.c`）。

驗證步驟：在 guest 裡用 `devmem2 0xC0010000 w 0x12345678` 寫 e1000 BAR0，觀察 host 端是否印出對應的 MMIO write callback 資訊。

### 練習 3：找一個真實裝置的 MemoryRegionOps

閱讀 QEMU 9.0 原始碼 `hw/net/e1000.c`，找到：
1. e1000 的 MMIO region 初始化（搜尋 `memory_region_init_io`）
2. 對應的 `MemoryRegionOps` 定義（`e1000_mmio_ops`）
3. `valid.min_access_size` 和 `impl.min_access_size` 的值
4. 如果 guest 做 1-byte write 到這個 region，QEMU 會怎麼處理？

答案藏在 `memory_region_write_accessor()` 的邏輯裡（`system/memory.c`）。

---

## 本章重點整理

- **MemoryRegion 樹**描述 guest 物理位址空間的「邏輯地圖」，允許重疊、有優先級、可動態增刪。
- 四種 region：RAM（host mmap）、MMIO（callback）、alias（重新映射）、container（邏輯父節點）。
- **FlatView** 是樹的「執行時快照」，壓平成無重疊線段，每次樹改變就重算，用 RCU 保護。
- **`flatview_translate()`** 二分搜尋 FlatView，O(log n) 找到對應 MemoryRegion。
- **MemoryRegionOps** 的 `valid` 和 `impl` 控制 guest 允許的存取寬度 vs callback 處理的寬度，QEMU 自動拆/合——裝置如果對 size 沒做充分檢查就是洞。
- **system_memory** 和 **system_io** 是兩棵根樹，`address_space_rw()` 是 DMA 操作入口。
- KVM memory slot 只對應 RAM region；MMIO region 靠 EPT miss 讓 guest VM-exit 到 QEMU。
- MMIO callback 收到的 `offset + size + data` 完全來自 guest 控制——這三個值的任何錯誤假設都是攻擊面。

---

## 自我檢核

1. 為什麼 guest 存取 RAM region 不需要 VM-exit 到 QEMU，但存取 MMIO region 一定要？
2. `memory_region_init_alias()` 和 `memory_region_init_io()` 的主要差別是什麼？什麼時候選 alias？
3. FlatView 用什麼機制保護並發讀寫？為什麼不用 mutex？
4. MemoryRegionOps 的 `valid.max_access_size=4` 和 `impl.max_access_size=4` 有什麼不同？如果 guest 發出 8-byte 存取，分別會怎樣？
5. `info mtree` 和 `info mtree -f` 的輸出分別對應 QEMU 內部哪個資料結構？
6. 為什麼說「MMIO callback 的 offset 參數是攻擊面」？給一個具體的例子說明如果 offset 沒有 bound check 會發生什麼。

---

## 延伸閱讀

1. **QEMU 9.0 原始碼**
   - `include/exec/memory.h`：MemoryRegion、MemoryRegionOps、AddressSpace、FlatView 定義
   - `system/memory.c`：`flatview_translate`、`render_memory_region`、`memory_region_dispatch_write`
   - `system/physmem.c`：`qemu_ram_alloc`、RAMBlock 管理
   - `hw/net/e1000.c`：真實 MMIO region 初始化範例

2. **QEMU Internals Wiki**
   https://wiki.qemu.org/Documentation/Memory
   （官方，稍舊但概念沒變，搭配原始碼看）

3. **"QEMU Memory Subsystem" — Stefan Hajnoczi（KVM Forum 2013）**
   偏舊但對 MemoryRegion/AddressSpace/FlatView 的設計動機解釋得最清楚。搜尋「QEMU memory subsystem stefan hajnoczi slides」。

4. **「Virtual Machine Escape: QEMU Case Study」— Rancho Han（2019）**
   分析 CVE-2019-6778（slirp heap overflow）的文章，順帶解釋 MMIO dispatch 路徑——可以對照本章理解攻擊路徑從哪裡進。

5. **KVM API 文件**：`Documentation/virt/kvm/api.rst`（Linux kernel source）
   `KVM_SET_USER_MEMORY_REGION` ioctl 的參數說明，理解 RAM region 如何對應 KVM memory slot。

---

→ [Ch 11 — 裝置模擬與 MMIO dispatch：從 VM-exit 到 callback](./11-device-emulation-dispatch.md)

Ch 11 我們拿 MMIO dispatch 的完整路徑開刀：從 KVM VM-exit 到 `kvm_handle_io()`，到 `memory_region_dispatch_write()`，到裝置 callback，把這條線上的每一個函數都讀過一遍——然後討論攻擊者在哪個環節有機會介入。
