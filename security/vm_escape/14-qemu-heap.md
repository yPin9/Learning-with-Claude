# Ch 14 — QEMU 的 heap：g_malloc、物件佈局、如何 groom

> **目標**：理解 QEMU 的 heap 是什麼，device 物件（含 function pointer）怎麼活在這個 heap 上，以及為什麼 heap groom 對 VM escape 利用至關重要。

> **環境**：QEMU 9.0 / x86-64 / Linux（Ubuntu 22.04/24.04，需自編帶 symbol 的 debug QEMU）

---

## 為什麼需要這個？

你在 `binary_exploitation` 練過的 tcache、fastbin、unsorted bin、chunk header——那些知識在 QEMU 逃逸裡**全部直接適用**。QEMU 行程跑在 Linux host 上，它的記憶體分配器就是 glibc 的 ptmalloc，和你打 CTF userland heap 題的標的完全相同。

但 QEMU 有一層包裝：所有 device 和 QEMU 核心程式碼都透過 **GLib 的記憶體 API**（`g_malloc`、`g_new`、`g_free`）分配記憶體，而不是直接呼叫 `malloc`/`free`。不過在 Linux 上，GLib 的這些函式就是對 `malloc`/`free` 的薄包裝，不換分配器——所以 ptmalloc 的所有特性（chunk 佈局、bins、ASLR、tcache poisoning）在 QEMU heap 上全部成立。

理解 QEMU heap 的意義在於：device 物件（`EDUState`、`E1000State`、`VirtIONet`……）都活在這個 heap 上，而這些結構體裡住著 **function pointer**——`MemoryRegionOps *ops`、`QEMUTimer::cb`、QOM vtable 指標。能控制這些指標就能劫持控制流。這是 VM escape 利用鏈的核心。

---

## 先建立直覺

```
QEMU 行程的 heap（glibc ptmalloc）
┌─────────────────────────────────────────────────────────┐
│  chunk A: EDUState (含 MemoryRegion mmio)               │
│  ┌──────────────────────────────────────────────────┐   │
│  │ prev_size │ size │                               │   │
│  │  [PCIDevice pdev        ]                        │   │
│  │  [MemoryRegion mmio     ] ← mmio.ops 指向 ops 表 │   │
│  │  [dma state / buf[0x100]] ← 我們的 OOB 起點     │   │
│  │  [QEMUTimer *dma_timer  ] ← timer 有 cb 指標    │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  chunk B: 某個被 g_malloc 配置的物件（緊接其後）         │
│  ┌──────────────────────────────────────────────────┐   │
│  │ prev_size │ size │                               │   │
│  │  [MemoryRegion ops  ] ← 如果 OOB 夠遠就蓋到這裡 │   │
│  │  [QEMUTimer callback] ← 或蓋到這裡              │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

兩個相鄰 chunk 之間，你的 overflow buffer（`buf[0x100]`）往後寫，就直接踩到下一個 chunk。這不需要任何特殊技巧，和 `binary_exploitation` 裡的 heap overflow 章節（若曾學過 `security/binary_exploitation` 的 heap 部分）完全一樣的手法。

差別只有一個：在 QEMU 裡，「chunk B」裡的 function pointer 被呼叫時，你不是在 guest 裡執行，而是在 **host userspace 裡的 QEMU 行程**執行——那是逃逸的終點。

---

## GLib 記憶體 API：g_malloc、g_new、g_free

```c
// GLib API（glib/gmem.h）
gpointer g_malloc(gsize n_bytes);           // 等同 malloc，失敗時 abort（不回 NULL）
gpointer g_malloc0(gsize n_bytes);          // calloc 語義，清零
gpointer g_realloc(gpointer mem, gsize n);  // realloc
void     g_free(gpointer mem);              // 等同 free

/* 型別安全巨集 */
#define g_new(struct_type, n_structs)   \
    ((struct_type *) g_malloc(sizeof(struct_type) * (gsize)(n_structs)))
#define g_new0(struct_type, n_structs)  \
    ((struct_type *) g_malloc0(sizeof(struct_type) * (gsize)(n_structs)))
```

`g_malloc` 的重要特性：**失敗時直接 `abort()`，不回 NULL**。這和 `malloc` 不同——你不需要幫 QEMU device 的分配加 NULL 檢查（QEMU 自己就不加），但也代表 QEMU 對記憶體耗盡沒有優雅處理。對攻擊者來說，這讓「用 NULL 指標 exploit QEMU」難一點，但不影響主線 heap overflow。

**在 Linux 上**，GLib 用 `malloc` 實作 `g_malloc`（可以 `strace` QEMU 確認，會看到 `brk`/`mmap` 系統呼叫）。沒有自訂 allocator，沒有 GLib 自己的 slab——就是 glibc ptmalloc。

你在 `security/binary_exploitation` 學過的 tcache（glibc 2.26+）、fastbin、unsorted bin、smallbin——全部在這個 heap 上成立。QEMU 9.0 跑在 Ubuntu 22.04（glibc 2.35）或 Ubuntu 24.04（glibc 2.39）上，tcache 是主要 fast path，`tcache_perthread_struct` 的毒化（tcache poisoning）在繞過 safe-linking 後也適用。

---

## Device 物件的 heap 佈局

### PCIDevice 和 DeviceState 的繼承

QEMU 的 QOM（QEMU Object Model）用 C 語言模擬了 OOP 繼承。一個 PCI device 的典型佈局：

```c
/* 以 Ch 12 的自訂 vuln device 為例 */
typedef struct VulnState {
    PCIDevice pdev;          /* 父類，必須在第一個欄位 */
                             /* PCIDevice 內含 DeviceState，
                                DeviceState 內含 Object（vtable 指標）*/
    MemoryRegion mmio;       /* BAR0 對應的 MemoryRegion */
    /* MemoryRegion 內含:
       MemoryRegionOps *ops;  <-- function pointer 表指標
       const char *name;
       hwaddr size;
       ... */

    uint8_t buf[0x100];      /* 我們的 overflow 起點 */

    /* buf 後面緊接著的欄位，依編譯器 struct layout 而定 */
    uint32_t some_status;
    /* ... */
} VulnState;
```

當 QEMU 初始化這個 device，它呼叫：
```c
VulnState *s = g_new0(VulnState, 1);
// 或透過 QOM type_register → object_new → g_malloc0
```

整個 `VulnState` 是一個連續的 malloc chunk，大小是 `sizeof(VulnState)`。`buf[0x100]` 在 struct 裡的 offset 是固定的（編譯時決定，用 `pahole vulndev.so` 或 `offsetof()` 可確認），`buf` 之後緊接其他欄位。

### MemoryRegion 裡的 function pointer

```c
// include/exec/memory.h（QEMU 9.0）
struct MemoryRegion {
    Object parent_obj;

    /* 指向一個靜態常數表，定義 .read/.write 等 callback */
    const MemoryRegionOps *ops;  /* <── 這是 function pointer 表的指標 */
    void *opaque;                /* 傳給 callback 的 context，通常是 device struct 指標 */

    hwaddr size;
    hwaddr addr;
    /* ... 更多欄位 ... */
};

struct MemoryRegionOps {
    uint64_t (*read)(void *opaque, hwaddr addr, unsigned size);
    void (*write)(void *opaque, hwaddr addr, uint64_t data, unsigned size);
    /* ... read_with_attrs, write_with_attrs, endianness ... */
};
```

`MemoryRegion.ops` 是一個指向**靜態全域變數**（通常是 `static const MemoryRegionOps vuln_mmio_ops = {...}`）的指標。這個靜態全域變數在 QEMU PIE binary 的 `.rodata` 段，位址受 ASLR 影響但固定在 binary 基底的某個 offset。

**OOB 寫入把 `ops` 指標改掉**，讓它指向你控制的假 `MemoryRegionOps` 結構，其中的 `.read`/`.write` 函式指標指向你的 ROP gadget 或 shellcode——下次 guest 存取那個 MemoryRegion 的 MMIO 時，QEMU 呼叫的就是你的程式碼。

### QEMUTimer 的 function pointer

```c
// include/qemu/timer.h（QEMU 9.0）
struct QEMUTimer {
    int64_t expire_time;
    QEMUTimerList *timer_list;
    QEMUTimerCB *cb;       /* <── callback function pointer */
    void *opaque;
    QEMUTimer *next;
    int scale;
    int attributes;
};
```

`QEMUTimer` 物件通常透過 `timer_new_ms(QEMU_CLOCK_VIRTUAL, cb, opaque)` 分配，最終也是 `g_malloc`。Timer 被觸發時，main loop 呼叫 `timer->cb(timer->opaque)`。把 `cb` 蓋掉就能在下一個 timer tick 劫持控制流——比 MemoryRegion ops 還好用，因為不需要等 guest 做 MMIO，只要等時間到。

---

## Heap Groom：讓目標物件落在對的位置

### 什麼是 groom（整地）

`binary_exploitation` 的 heap 章節（如果你學過）應該熟悉這個概念：在觸發漏洞之前，先透過一系列分配/釋放操作，讓 heap 佈局變成「overflow 的緩衝區剛好緊接著目標物件」的理想狀態。

在 QEMU 裡，guest 透過 MMIO/DMA/QOM 操作間接驅動 QEMU 的分配和釋放——你不能直接呼叫 QEMU 的 `g_malloc`，但你可以觸發 device 幫你做。

### 可用的 groom 動作

| 動作 | host 端發生的事 | 分配大小參考 |
|------|---------------|------------|
| guest 啟動 DMA（某些 device）| device 分配 DMA bounce buffer | 依請求長度 |
| guest 熱插拔一個 device | `g_new0(DeviceType, 1)` | `sizeof(DeviceState + 子類)` |
| guest 熱卸載一個 device | `g_free(device_ptr)` | 釋放對應 chunk |
| guest 改變 virtqueue 大小 | 重新分配 vring 陣列 | 16 * queue_size |
| guest 設定 PCI BAR（某些操作）| device 重新分配 MemoryRegion | `sizeof(MemoryRegion)` |
| guest 觸發 timer 相關暫存器 | `timer_new` → `g_malloc(sizeof(QEMUTimer))` | `sizeof(QEMUTimer)` = 56 bytes |

具體哪個 device 支援哪些動作，要查 device 的 `.c` 原始碼，看什麼 MMIO write 會觸發 `g_malloc/g_free`。

### 以 vuln device 為例：製造「buf 後接 MemoryRegion」的佈局

Ch 12 的 vuln device 的 `VulnState` 是單一個大 chunk。`buf[0x100]` 在這個 chunk 內部——內部 OOB 只能蓋到 struct 後段的其他欄位（例如 `MemoryRegion mmio` 裡的 `ops` 指標，如果 `mmio` 在 `buf` 之後的話）。

```
VulnState chunk（heap 上）
┌───────────────────────────────────────────────────────┐
│ chunk header (16 bytes)                               │
│ PCIDevice pdev (PCIDevice 大小，約 4KB)               │
│ MemoryRegion mmio (大小約 264 bytes，含 ops 指標)     │
│ uint8_t buf[0x100]   <── guest 控制的寫入起點         │
│ [其他欄位...]         <── OOB 寫入蓋到這裡            │
└───────────────────────────────────────────────────────┘
```

要確認 `mmio` 在 `buf` 之前還是之後，要看 `pahole` 輸出（Linux 工具，讀 DWARF debug info，輸出 struct 的每個欄位 offset）：

```bash
# 未實測，理論預期；需在 Linux 上有 debug symbol 的 QEMU
pahole -C VulnState qemu-system-x86_64
```

如果 `mmio` 在 `buf` 之前，內部 OOB（向後寫）碰不到 `mmio.ops`，需要 groom 讓 heap 上 VulnState 之後的 chunk 是某個含 function pointer 的物件。

### Groom 步驟示例（理論預期，未實測）

```
目標佈局：
VulnState chunk | [填充 chunk] | QEMUTimer chunk
                                  ^^
                                  OOB 要蓋到這裡
```

1. **在觸發 overflow 前**，讓 guest 觸發一個已知大小的 `QEMUTimer` 分配，讓它落在 VulnState 後面。
2. 如果 heap 上兩者之間有空隙（填充 chunk），先用一些 MMIO 操作製造/釋放中間大小的 chunk，填平空隙。
3. 確認佈局：gdb attach QEMU，`print &vuln_state->buf`，`print &some_timer`，看兩者位址差距是否等於一個乾淨的 chunk 邊界。

這個「確認佈局」的步驟在 CTF 做 VM escape 題時幾乎必須——不知道佈局就寫不了 groom 腳本。

---

## 底層機制：chunk 佈局與 function pointer 蓋寫

```
heap chunk 在記憶體裡（glibc ptmalloc，x86-64）

低位址
┌─────────────────────┐ ◄─ malloc 回傳的指標
│ prev_size (8 bytes) │   （如果前一個 chunk 是 free 才有效）
│ size      (8 bytes) │   ← 含 flags（P/M/A bits）
│                     │
│  用戶資料...        │
│                     │
└─────────────────────┘ ◄─ 這個 chunk 的結尾
│ 下一個 chunk 的
│ prev_size（如果此 chunk 是 free 才使用）
高位址
```

QEMU 裡典型的相鄰分配關係：

```
 device struct（g_new0 分配）
 ┌────────────────────────────────────┐
 │ ... (PCIDevice pdev)               │
 │ ... (MemoryRegion mmio)            │
 │     mmio.ops = 0x555555a12340      │ ← 指向 .text/.rodata 的 ops 表
 │ ... (buf[0x100])                   │
 └────────────────────────────────────┘
 下一個相鄰 chunk（例如被 g_malloc 分配的 QEMUTimer）
 ┌────────────────────────────────────┐
 │ prev_size                          │
 │ size                               │
 │ timer.expire_time                  │
 │ timer.timer_list                   │
 │ timer.cb = 0x555555b23410          │ ← callback function pointer
 │ timer.opaque                       │
 └────────────────────────────────────┘

如果 buf 的 OOB 寫入長度夠大，寫到 timer.cb 那個位址，
把它蓋成 0x7f0000001234（某個你控制的 gadget 位址）：

下一個 timer tick 到來時，QEMU main loop 呼叫 timer->cb(timer->opaque)
= 呼叫 0x7f0000001234(controlled_ptr)
= 你的程式碼在 host userspace 執行
```

這個因果鏈非常直接：**guest 寫 MMIO → host heap OOB → 鄰居物件的 function pointer 被蓋 → host userspace 控制流被劫持**。

---

## 對比與取捨

| 目標 function pointer | 位置 | 觸發時機 | 需要 OOB 距離 | 難度 |
|---------------------|------|---------|-------------|-----|
| `MemoryRegion.ops` 指標 | device struct 內部或鄰近 chunk | guest 下次 MMIO 讀寫 | 取決於 struct layout | 中（需先 leak ops 位址） |
| `QEMUTimer.cb` | 獨立 timer chunk | 下一個 timer tick（最快 1ms）| 需 groom 讓 timer 緊接 | 中（groom 可控） |
| QOM vtable 指標（`Object.class`）| device struct 最前面 | QOM 物件方法呼叫 | 0 offset（struct 開頭）| 高（需知道 vtable 結構）|
| `QEMUIRQ` handler | 另一個 heap chunk | guest 觸發 IRQ | 需 groom | 中 |

---

## 踩雷集錦

**錯誤直覺 1：g_malloc 和 malloc 不一樣，ptmalloc 技巧不適用**

錯。在 Linux 上 `g_malloc` 就是 `malloc` 的包裝，底層是 glibc ptmalloc。tcache、fastbin、chunk header 覆寫——全部適用。如果你的目標環境換成 musl 或 jemalloc，那才需要重新學那個 allocator 的規則。Ubuntu 22.04/24.04 預設 glibc，沒有意外。

**錯誤直覺 2：MemoryRegion.ops 直接存 function pointer，蓋掉就能跳**

不完全對。`ops` 是一個**指向 ops 表的指標**（`const MemoryRegionOps *`），而不是 function pointer 本身。你需要把 `ops` 指標蓋成指向一個**偽造的 `MemoryRegionOps` 結構**的位址，那個偽結構裡才放你的 function pointer。這多了一層間接——你需要一個地方放偽結構（heap 上的可控區域，或 guest 物理記憶體的某個洩漏位址）。

**錯誤直覺 3：Heap groom 只要分配幾個物件就好，不需要精確**

在 CTF 題的受控環境下，groom 可以很簡單；在真實 QEMU 上，因為 QEMU 啟動時已做了大量分配（QOM 物件樹、bus、controller……），heap 不乾淨，兩個 `g_malloc` 的輸出未必相鄰。真正的 groom 需要先「填滿」tcache/fastbin 裡的空位，確保你的分配是全新擴展 heap 的新 chunk，才能保證相鄰。

**錯誤直覺 4：越界寫入 chunk header（size/prev_size）會立刻崩潰**

不一定立刻崩潰——ptmalloc 的 size 欄位在 free 時才會被嚴格使用（`free()` → `_int_free()` → 檢查 size / prev_in_use）。如果你的漏洞是「OOB write 後立刻觸發 function pointer，不走 free 路徑」，蓋掉的 chunk header 可能根本沒被驗證就執行了你的 gadget。但如果中間走了 free，就需要繞 ptmalloc 的 integrity check（和 `binary_exploitation` 的技巧相同）。

**錯誤直覺 5：QEMU device 的 struct 佈局在兩個版本間不變**

嚴格來說是錯的。每次 QEMU 版本更新加欄位，`sizeof(SomeState)` 就可能變——你的 OOB offset 需要針對特定版本確認。這也是本課釘 QEMU 9.0 的原因：不同版本的 struct 佈局可能完全不同，exploit 不可移植。

---

## 進階：再往深一層

### `pahole` 精確量測 struct offset

```bash
# 在 Linux 上，自編 QEMU 有 DWARF debug info
pahole -C EDUState /path/to/qemu-system-x86_64 | head -60
```

輸出類似：
```
struct EDUState {
    struct PCIDevice           pdev;                 /*     0  4480 */
    struct MemoryRegion        mmio;                 /*  4480   264 */
    uint32_t                   addr4;                /*  4744     4 */
    /* ... */
    char                       dma_buf[4096];        /*  4760  4096 */
    /* ... */
};
```

這告訴你 `dma_buf` 在 struct 裡的精確 offset（4760 bytes）。配合 gdb 的 `print &edu->dma_buf`，你就能算出 OOB 要走多遠才能到鄰居 chunk。

（未實測，理論預期；需在 Linux 有 debug QEMU 的環境執行。）

### tcache 的影響

glibc 2.26+ 引入 tcache（per-thread cache）。大小 ≤ 0x408 bytes 的 chunk 被 free 後進 tcache，下次相同大小的 malloc 優先從 tcache 取。這意味著：如果你能控制 `g_free(ptr)` 後接著 `g_malloc(same_size)` 的操作，你可以讓新分配落在你選擇的位址（tcache poisoning，需繞 safe-linking，glibc 2.32+ 引入）。

這和 `binary_exploitation` 的 tcache 章節完全相同的技術——在 QEMU 裡，「控制分配/釋放時機」的手段是驅動 device 的 MMIO 操作。

### safe-linking（glibc 2.32+）

```c
/* glibc 2.32+ 的 tcache / fastbin fd 指標加密 */
#define PROTECT_PTR(pos, ptr) \
    ((__typeof (ptr)) ((((size_t) pos) >> 12) ^ ((size_t) ptr)))
```

fd 指標 = `(pos >> 12) XOR next_ptr`。繞過需要先 leak heap 位址（`pos >> 12` 的值）。這也適用於 QEMU 的 heap——如果你用 tcache poisoning，先 leak 一個 heap 位址，再 XOR 計算偽造的 fd，再寫入。

---

## 動手練習

（需 Linux 環境 + 自編 debug QEMU）

1. 用 `pahole` 查 `EDUState` 和你 Ch 12 自訂 device 的 struct 佈局，列出每個欄位的 offset 和大小，找出 `MemoryRegion.ops` 的 offset。

2. gdb attach QEMU，斷點在 `edu_realize`（device 初始化），執行完後：
   ```gdb
   (gdb) print &edu->mmio.ops
   (gdb) x/4xg edu->mmio.ops
   ```
   確認 `ops` 指標的值，以及它指向的地方的內容（應是 `.read`/`.write` 函式指標）。

3. 用 gdb 的 `heap`（pwndbg）或手動 `x/100xg` 查看 EDUState 附近的 heap 佈局，確認 chunk header，找到下一個相鄰 chunk 是什麼物件。

4. 在 guest 裡執行一個只分配、不觸發漏洞的操作（例如多次讀 MMIO），觀察 host heap 是否有新 chunk 產生（gdb `info proc mappings` + 比較 `brk` 位址）。

5. 設計一個最小的 groom 序列（不需要真的做 OOB），讓 `QEMUTimer` chunk 緊接在 VulnState 後面，用 gdb 驗證位址相鄰。

---

## 本章重點整理

- QEMU 用 GLib `g_malloc/g_free` 分配記憶體；Linux 上底層是 glibc ptmalloc，`binary_exploitation` 的所有 heap 技術直接適用。
- Device 物件（含 function pointer）以連續 struct 形式活在 heap 上：`PCIDevice` → `MemoryRegion`（含 `ops` 指標） → device 私有欄位（含 overflow buffer）。
- `MemoryRegionOps *ops` 和 `QEMUTimer *cb` 是最重要的兩類劫持目標；`ops` 是指向 ops 表的指標（二層間接），`cb` 是直接的 function pointer。
- Heap groom = 在觸發漏洞前調整 heap 佈局，讓 overflow 能碰到目標物件。Guest 透過 MMIO/DMA/device 操作間接驅動 host 的分配/釋放。
- `pahole` + gdb 是確認 struct offset 和 heap 佈局的兩大工具，exploit 前必查。
- glibc 2.32+ 的 safe-linking 影響 tcache poisoning；需先 leak heap 位址才能計算偽造 fd。

---

## 自我檢核

- [ ] 我能解釋為什麼 `g_malloc` 在 Linux 上的行為和 `malloc` 等同，以及這對 heap exploit 意味著什麼。
- [ ] 我能從 `pahole` 輸出找出 `buf[0x100]` 到 `MemoryRegion.ops` 的 offset（byte 距離）。
- [ ] 我能說出 `MemoryRegion.ops` 是直接的 function pointer 還是一個指向表的指標，以及這對 exploit 的影響。
- [ ] 我能列出至少三個「guest 動作 → host g_malloc/g_free」的對應關係。
- [ ] 我能說出 tcache safe-linking（glibc 2.32+）對 tcache poisoning 的影響，以及需要什麼 primitive 才能繞過。

---

## 延伸閱讀

1. **`pahole` 工具文件**（`man pahole` 或 `pahole -h`，dwarves 套件）
   讀哪裡：`-C struct_name` 選項；`--hex` 顯示 hex offset。
   學什麼：精確量測 QEMU struct 佈局，這是所有 heap exploit 的起點，不猜 offset。
   關聯：Ch 20 找可劫持指標時的必備工具。

2. **glibc 原始碼 `malloc/malloc.c`**（GNU libc git，或 `apt source libc6`）
   讀哪裡：`_int_malloc`、`_int_free`、`tcache_put`/`tcache_get`、`PROTECT_PTR`（safe-linking）的實作。
   學什麼：tcache 的 chunk 佈局、safe-linking 機制，理解 QEMU heap 上 tcache poisoning 的限制。
   關聯：`security/binary_exploitation` 的 heap 章節是基礎，這裡是再往深一層。

3. **"QEMU Heap Internals" — Various CTF writeups**（搜尋 "QEMU escape heap grooming" + CTFTIME / GitHub）
   讀哪裡：找到有明確 `pahole` 輸出和 gdb heap 驗證的 writeup，通常在 `practice-c` 等級。
   學什麼：真實 CTF 環境的 groom 步驟，以及不同 QEMU 版本下 struct offset 的差異。
   關聯：練習 B 和 Ch 22 ROP 的實際操作依據。

4. **pwndbg `heap` 命令文件**（GitHub pwndbg/pwndbg，`docs/`）
   讀哪裡：`heap`、`bins`、`tcache`、`vis_heap_chunks` 命令的說明。
   學什麼：在 gdb 裡直接視覺化 QEMU heap 佈局，比手動 `x/100xg` 直觀十倍。
   關聯：所有 Part 3 的 exploit 除錯都依賴這個工具。

5. **"Exploiting QEMU device emulation" — Zhao 等，2019（或同類長文）**（搜尋 "QEMU escape function pointer hijack" 找到 InfoSec 研究報告或 conference paper）
   讀哪裡：著重「heap layout」和「function pointer hijack」章節。
   學什麼：多個真實 device bug 下的 struct 佈局分析，以及如何從 OOB → 劫持 ops → RIP 的完整路徑。
   關聯：Ch 21 「從任意寫到 RIP」的前置視角。

---

> 現在我們知道 QEMU heap 上住著什麼、function pointer 在哪裡、groom 的思路是什麼。下一章把這些整合起來，看 MMIO 和 DMA 怎麼作為讀寫原語，把 OOB 洞從「有損壞」變成「可利用的攻擊鏈」。

→ [Ch 15](./15-mmio-dma-as-primitives.md)
