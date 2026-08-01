# Ch 8 — 手寫最小 KVM hypervisor：100 行跑一個 guest

> **目標**：從零用 C 手寫一個可執行的最小 KVM hypervisor，親眼看 vCPU exit、自己處理 I/O，把 Ch 6 的 ioctl 序列從紙上變成跑起來的程式。

> **環境**：Linux x86-64，/dev/kvm 可用（實體機或 nested virt），gcc，kernel 5.x+

---

## 為什麼需要這個？

讀 QEMU、kvmtool、Firecracker 的源碼，最頭痛的地方不是邏輯複雜——是背景噪音太多：VirtIO、device model、migration、QMP。你根本不知道哪些是 KVM 的核心、哪些是 QEMU 自己加的。

最有效的破法是自己從零把最小的能跑的版本打出來。這樣你就知道：KVM 必須給你什麼（ioctl 序列、kvm_run struct）、你作為 hypervisor 必須處理什麼（每個 exit reason）、以及哪些東西根本不是 KVM 負責的（BIOS、裝置模擬、記憶體管理）。

這也是 VM escape 研究者必備的技能。你看到一個 CVE 說「攻擊者透過惡意 MMIO 訪問觸發 hypervisor UAF」，如果你不知道 MMIO exit 的流程，你就無法理解漏洞在哪個層發生、為什麼 guest 能控制 host。

---

## 先建立直覺

KVM hypervisor 本質上是一個 **event loop（事件迴圈）**：

```
創建 VM → 分配 guest 記憶體 → 創建 vCPU → 設定 CPU 暫存器 → 進入迴圈：
    ioctl(vcpu_fd, KVM_RUN, 0)   ← 把 CPU 控制權交給 guest
    → guest 跑，直到 exit event
    → kernel 把 exit reason 寫進 kvm_run struct
    → 我們讀 exit_reason，處理，繼續迴圈或 break
```

最小的 guest 程式需要做兩件事：
1. OUT 指令把一個字元送到 I/O port → 觸發 `KVM_EXIT_IO`
2. HLT 指令停住 → 觸發 `KVM_EXIT_HLT`

guest 跑在 **16-bit real mode（實模式）**，這是 x86 開機後的初始模式，最簡單，不需要設置 GDT、分頁、段描述符全套。

---

## ioctl 序列回顧

先快速點名所有用到的 ioctl（細節見 [Ch 6](./06-kvm-architecture.md)）：

| ioctl | fd | 說明 |
|---|---|---|
| `KVM_GET_API_VERSION` | `/dev/kvm` | 確認 API 版本，應為 12 |
| `KVM_CREATE_VM` | `/dev/kvm` | 建立 VM，回傳 vm_fd |
| `KVM_SET_USER_MEMORY_REGION` | vm_fd | 把 host 記憶體映射給 guest |
| `KVM_CREATE_VCPU` | vm_fd | 建立 vCPU，回傳 vcpu_fd |
| `KVM_GET_VCPU_MMAP_SIZE` | `/dev/kvm` | 查 kvm_run struct 的大小 |
| `KVM_GET_SREGS` / `KVM_SET_SREGS` | vcpu_fd | 取得/設定段暫存器 |
| `KVM_SET_REGS` | vcpu_fd | 設定通用暫存器（含 rip） |
| `KVM_RUN` | vcpu_fd | 執行 guest，直到 exit |

---

## 完整程式碼

> ⚠️ **未實測**：以下程式碼在本教材編寫環境（Windows）無法執行。程式碼以 KVM API 官方文件、Linux kernel kvm-hello-world 參考實作、以及 kvmtool / firecracker 源碼為依據撰寫，理論上正確，但讀者務必在真實 Linux + /dev/kvm 環境自行編譯驗證。預期輸出見下方說明。

```c
/* kvm-hello.c
 * 最小 KVM hypervisor：執行一段 16-bit real mode guest，
 * 印出一個字元後 HLT。
 *
 * gcc -o kvm-hello kvm-hello.c && ./kvm-hello
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdint.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/kvm.h>

/* ── guest 機器碼 ──────────────────────────────────────────
 * 16-bit real mode，載入到 GPA 0x1000（RIP 也設成 0x1000）
 *
 *   b0 41       mov al, 0x41     ; 'A'
 *   e6 10       out 0x10, al     ; 寫到 port 0x10 → KVM_EXIT_IO
 *   f4          hlt              ; → KVM_EXIT_HLT
 */
static const uint8_t guest_code[] = {
    0xb0, 0x41,   /* mov al, 'A'  */
    0xe6, 0x10,   /* out 0x10, al */
    0xf4,         /* hlt          */
};

/* helper：ioctl 失敗就 perror + exit */
static void die(const char *msg) {
    perror(msg);
    exit(1);
}

int main(void)
{
    int kvm_fd, vm_fd, vcpu_fd;
    int api_ver, mmap_size;
    void *guest_mem;
    struct kvm_run *run;

    /* ── 1. 開啟 /dev/kvm ──────────────────────────────── */
    kvm_fd = open("/dev/kvm", O_RDWR | O_CLOEXEC);
    if (kvm_fd < 0) die("open /dev/kvm");

    api_ver = ioctl(kvm_fd, KVM_GET_API_VERSION, 0);
    if (api_ver != 12) {
        fprintf(stderr, "KVM API version %d, expected 12\n", api_ver);
        exit(1);
    }

    /* ── 2. 建立 VM ────────────────────────────────────── */
    vm_fd = ioctl(kvm_fd, KVM_CREATE_VM, 0);
    if (vm_fd < 0) die("KVM_CREATE_VM");

    /* ── 3. 分配並映射 guest 記憶體 ─────────────────────
     * mmap 一頁（4 KiB）作為 guest 的 GPA 0x1000～0x1FFF
     * MAP_SHARED：讓 KVM 的 EPT 能直接映射到這塊 HVA
     */
    guest_mem = mmap(NULL, 0x1000,
                     PROT_READ | PROT_WRITE,
                     MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (guest_mem == MAP_FAILED) die("mmap guest_mem");

    /* 把 guest 機器碼複製進去 */
    memcpy(guest_mem, guest_code, sizeof(guest_code));

    /* 告訴 KVM 這塊 host 記憶體映射到 guest GPA 0x1000 */
    struct kvm_userspace_memory_region region = {
        .slot            = 0,
        .flags           = 0,
        .guest_phys_addr = 0x1000,       /* GPA：guest 看到的實體位址 */
        .memory_size     = 0x1000,       /* 大小：4 KiB */
        .userspace_addr  = (uint64_t)(uintptr_t)guest_mem, /* HVA */
    };
    if (ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region) < 0)
        die("KVM_SET_USER_MEMORY_REGION");

    /* ── 4. 建立 vCPU ──────────────────────────────────── */
    vcpu_fd = ioctl(vm_fd, KVM_CREATE_VCPU, 0);
    if (vcpu_fd < 0) die("KVM_CREATE_VCPU");

    /* ── 5. mmap kvm_run struct ────────────────────────────
     * KVM 透過 mmap 把 kvm_run 共享給 userspace，
     * 這樣 exit 後不需要再 ioctl 一次就能讀 exit reason。
     */
    mmap_size = ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0);
    if (mmap_size < 0) die("KVM_GET_VCPU_MMAP_SIZE");

    run = mmap(NULL, mmap_size,
               PROT_READ | PROT_WRITE, MAP_SHARED, vcpu_fd, 0);
    if (run == MAP_FAILED) die("mmap kvm_run");

    /* ── 6. 設定段暫存器（sregs）── real mode 初始狀態 ───
     *
     * real mode 下所有 segment 的 base 都是 selector * 16，
     * 但 KVM 需要我們把 base / limit / type 全部明確填進去。
     *
     * 關鍵：CR0 的 bit 0（PE，Protection Enable）必須是 0，
     * 否則 KVM 會認為 guest 在 protected mode。
     */
    struct kvm_sregs sregs;
    if (ioctl(vcpu_fd, KVM_GET_SREGS, &sregs) < 0)
        die("KVM_GET_SREGS");

    /* 定義一個通用的 real-mode segment 設定 */
#define REAL_MODE_SEG(seg, base_, sel_)          \
    do {                                          \
        (seg).base     = (base_);                 \
        (seg).limit    = 0xffff;                  \
        (seg).selector = (sel_);                  \
        (seg).type     = 0x3;  /* read/write, accessed */ \
        (seg).present  = 1;                       \
        (seg).dpl      = 0;                       \
        (seg).db       = 0;    /* 16-bit default operand size */ \
        (seg).s        = 1;    /* data/code segment */          \
        (seg).l        = 0;    /* not 64-bit */                 \
        (seg).g        = 0;    /* limit in bytes, not 4K pages */ \
        (seg).avl      = 0;                       \
    } while (0)

    /* CS 的 type 要設成 code segment (readable, accessed) */
    REAL_MODE_SEG(sregs.cs, 0x0, 0x0);
    sregs.cs.type = 0xb; /* execute/read, accessed */

    REAL_MODE_SEG(sregs.ds, 0x0, 0x0);
    REAL_MODE_SEG(sregs.es, 0x0, 0x0);
    REAL_MODE_SEG(sregs.fs, 0x0, 0x0);
    REAL_MODE_SEG(sregs.gs, 0x0, 0x0);
    REAL_MODE_SEG(sregs.ss, 0x0, 0x0);

    /* CR0 PE bit 清零 → real mode */
    sregs.cr0 &= ~(uint64_t)1;

    if (ioctl(vcpu_fd, KVM_SET_SREGS, &sregs) < 0)
        die("KVM_SET_SREGS");

    /* ── 7. 設定通用暫存器（regs）──────────────────────── */
    struct kvm_regs regs;
    memset(&regs, 0, sizeof(regs));
    regs.rip    = 0x1000;   /* 指向我們放機器碼的 GPA */
    regs.rflags = 0x2;      /* bit 1 永遠是 1（架構規定）*/

    if (ioctl(vcpu_fd, KVM_SET_REGS, &regs) < 0)
        die("KVM_SET_REGS");

    /* ── 8. 主迴圈：KVM_RUN + 處理 exit ────────────────── */
    printf("[host] starting KVM run loop\n");

    for (;;) {
        /* 把 CPU 控制權交給 guest，直到下一個 exit */
        if (ioctl(vcpu_fd, KVM_RUN, 0) < 0)
            die("KVM_RUN");

        switch (run->exit_reason) {

        case KVM_EXIT_IO:
            /*
             * guest 執行了 IN/OUT 指令。
             * run->io.direction: KVM_EXIT_IO_IN 或 KVM_EXIT_IO_OUT
             * run->io.port:      I/O port 號碼
             * run->io.size:      operand size（1/2/4 bytes）
             * run->io.data_offset: 資料在 kvm_run struct 內的偏移
             *
             * 資料的實際位置：(uint8_t *)run + run->io.data_offset
             */
            if (run->io.direction == KVM_EXIT_IO_OUT &&
                run->io.port      == 0x10 &&
                run->io.size      == 1)
            {
                uint8_t *data = (uint8_t *)run + run->io.data_offset;
                printf("[host] KVM_EXIT_IO: port=0x%x, data='%c' (0x%02x)\n",
                       run->io.port, (char)*data, *data);
            } else {
                fprintf(stderr, "[host] unexpected IO: dir=%u port=0x%x size=%u\n",
                        run->io.direction, run->io.port, run->io.size);
            }
            break;

        case KVM_EXIT_HLT:
            printf("[host] KVM_EXIT_HLT: guest halted, exiting\n");
            goto done;

        case KVM_EXIT_FAIL_ENTRY:
            fprintf(stderr, "[host] KVM_EXIT_FAIL_ENTRY: hardware_entry_failure_reason=0x%llx\n",
                    run->fail_entry.hardware_entry_failure_reason);
            goto done;

        case KVM_EXIT_INTERNAL_ERROR:
            fprintf(stderr, "[host] KVM_EXIT_INTERNAL_ERROR: suberror=0x%x\n",
                    run->internal.suberror);
            goto done;

        default:
            fprintf(stderr, "[host] unhandled exit_reason=%u\n",
                    run->exit_reason);
            goto done;
        }
    }

done:
    /* 清理 */
    munmap(run, mmap_size);
    munmap(guest_mem, 0x1000);
    close(vcpu_fd);
    close(vm_fd);
    close(kvm_fd);
    return 0;
}
```

---

## 編譯與預期輸出

```bash
gcc -o kvm-hello kvm-hello.c && ./kvm-hello
```

預期輸出（理論上）：

```
[host] starting KVM run loop
[host] KVM_EXIT_IO: port=0x10, data='A' (0x41)
[host] KVM_EXIT_HLT: guest halted, exiting
```

如果你看到 `KVM_EXIT_FAIL_ENTRY`，最常見原因是 CR0 PE bit 沒有清乾淨，或是 CS.type 設錯了。

---

## 底層機制：kvm_run 的記憶體佈局

`kvm_run` 是一個 kernel 和 userspace 共享的結構體，透過 `mmap(vcpu_fd)` 映射到 userspace。這個共享記憶體的設計目的是**減少 ioctl 次數**：每次 exit 後不需要再發一個 ioctl 去問「你為什麼 exit？」，直接讀 `run->exit_reason` 就好。

```
┌────────────────────────────────────────────┐  ← mmap base (= run)
│ struct kvm_run {                            │
│   __u32 request_interrupt_window;          │
│   __u8  immediate_exit;                    │
│   ...                                      │
│   __u32 exit_reason;          ← 在這裡     │
│   union {                                  │
│     struct { ... } io;        ← KVM_EXIT_IO│
│     struct { ... } mmio;      ← KVM_EXIT_MMIO
│     struct { ... } hypercall; │
│     ...                                    │
│   };                                       │
│ }                                          │
├────────────────────────────────────────────┤
│  (padding to page boundary)                │
├────────────────────────────────────────────┤
│  pio_data region                           │  ← io.data_offset 指向這裡
│  (I/O 的實際資料就存在這塊)                │
└────────────────────────────────────────────┘
```

重點：`run->io.data_offset` 是相對於 `run`（mmap base）的偏移，不是一個指標。要拿到資料要做 `(uint8_t *)run + run->io.data_offset`。這是初學者最常犯的錯誤。

---

## real mode sregs 的坑

16-bit real mode 的 segment 在 KVM 眼裡不是「selector * 16」那麼單純，KVM 要求把每個段的所有屬性都明確寫進 `kvm_segment` 結構體：

```c
struct kvm_segment {
    __u64 base;
    __u32 limit;
    __u16 selector;
    __u8  type;       /* ← 最常被忽略 */
    __u8  present;
    __u8  dpl;
    __u8  db;
    __u8  s;          /* ← 1 = data/code，0 = system */
    __u8  l;          /* ← 1 = 64-bit code */
    __u8  g;          /* ← 1 = limit 以 4K 為單位 */
    __u8  avl;
    __u8  unusable;   /* ← 如果設 1，KVM 忽略整個段 */
    __u8  padding;
};
```

`type` 欄位對應 Intel SDM 的 segment descriptor type：
- `0x3`：data，read/write，accessed
- `0xb`：code，execute/read，accessed

`unusable = 0` 非常重要——如果你忘了清零而剛好值是 1，KVM 會把這個段視為不可用，guest 第一個指令就會 fault。

---

## 對比與取捨

| 面向 | 本例（100 行） | kvmtool | QEMU |
|---|---|---|---|
| 目的 | 教學/逆向理解 | 輕量 VM | 全功能模擬器 |
| 記憶體模型 | 單一 slot，4 KiB | 多 slot，完整 64-bit map | 動態 slot 管理 |
| 裝置模擬 | 無（port I/O 自己處理） | virtio block/net | 全套 device model |
| 中斷控制器 | 無（沒有 APIC 初始化） | KVM IRQCHIP | 完整 APIC/IOAPIC |
| 多 vCPU | 無 | 支援 | 支援 |
| exit 種類 | IO + HLT | 全部 | 全部 |

對 VM escape 研究者來說，重要的是理解 `KVM_EXIT_MMIO` 的處理路徑——QEMU 在那裡的裝置模型，就是 CVE 的高密度地帶。這部分在 [練習 A](./practice-a-minimal-hypervisor.md) 做。

---

## 踩雷集錦

**1. `open("/dev/kvm")` 失敗 → `EACCES`**

你的使用者沒有 `/dev/kvm` 的讀寫權限。

```bash
sudo usermod -aG kvm $USER   # 然後重新登入
# 或暫時
sudo chmod 666 /dev/kvm
```

**2. `KVM_CREATE_VM` 失敗 → `ENODEV`**

KVM kernel module 沒有載入：

```bash
sudo modprobe kvm_intel   # Intel CPU
sudo modprobe kvm_amd     # AMD CPU
```

**3. `KVM_EXIT_FAIL_ENTRY`，`hardware_entry_failure_reason = 0x80000021`**

CR0.PE（bit 0）是 1，KVM 認為你要跑 protected mode，但你設的 CS 又像 real mode，矛盾。確認 `sregs.cr0 &= ~(uint64_t)1` 有確實清掉。

**4. `KVM_EXIT_FAIL_ENTRY`，`hardware_entry_failure_reason = 0x80000000`**

CS segment 的 `unusable` 是 1，或 `present` 是 0。檢查 `KVM_GET_SREGS` 拿到的預設值，確定你有把 `present = 1` 和 `unusable = 0` 寫進去。

**5. `KVM_EXIT_IO`，但讀出來的資料是 0**

忘了用 `run->io.data_offset`，直接讀 `run->io` 的成員。I/O 資料是存在 kvm_run struct 後面的 pio_data 區，不是在 `run->io` 結構體裡面。

**6. nested virtualization 跑很慢**

在 VM 裡面跑 KVM（nested virt）會有效能懲罰，但功能上完全正確。如果你是在 VMware 或 VirtualBox 上做，記得開 CPU 虛擬化延伸（VT-x 或 SVM）的 nested 選項。

**7. `mmap_size` 比 `sizeof(struct kvm_run)` 小？**

不可能——`KVM_GET_VCPU_MMAP_SIZE` 回傳的是 kernel 決定的最小 mmap 大小，永遠 >= `sizeof(struct kvm_run)`。但如果你的 kernel 版本和 userspace 的 `linux/kvm.h` 版本不同步，struct 大小可能不對齊。確認 include 的是你這台機器的 kernel header。

---

## 進階：再往深一層

**MMIO exit**

OUT/IN 是 port I/O，另一類是 MMIO（memory-mapped I/O）。guest 寫一個特定 GPA（比如 0xfee00000，APIC 的 MMIO 範圍），如果沒有對應的 `kvm_userspace_memory_region`，就會觸發 `KVM_EXIT_MMIO`。這是 QEMU 裝置模型的主要輸入口，也是大部分 VM escape CVE 的攻擊點。

```c
case KVM_EXIT_MMIO:
    /* run->mmio.phys_addr：guest 寫的 GPA
     * run->mmio.data：要寫的資料（最多 8 bytes）
     * run->mmio.len：寬度
     * run->mmio.is_write：1 = guest 寫，0 = guest 讀
     */
    handle_mmio(run->mmio.phys_addr, run->mmio.data,
                run->mmio.len, run->mmio.is_write);
    break;
```

**多 vCPU**

每個 vCPU 是一個獨立的 fd，獨立的 kvm_run mmap，跑在一個 host thread 上。多 vCPU 就是多 thread，每個 thread 各自呼叫 `KVM_RUN`，VM 的全域狀態（記憶體、APIC 等）由 KVM kernel 管理同步。

**中斷注入**

要模擬外部中斷（比如時鐘中斷），在 `KVM_RUN` 前設 `run->request_interrupt_window = 1`，等 `KVM_EXIT_IRQ_WINDOW_OPEN`，然後用 `KVM_INTERRUPT` ioctl 注入虛擬中斷號。

**Intel PT tracing in guest**

kernel 5.4+ 支援 `KVM_CAP_INTEL_PT`，可以讓 guest vCPU 的執行被 Intel Processor Trace（Intel PT）追蹤到 host buffer。這在 fuzzing hypervisor 本身時非常有用——你可以用 AFL++ 加 Intel PT 模式做 coverage-guided fuzzing，直接 fuzz VM exit handler。

---

## 動手練習

1. **跑起來**：在 Linux 機器上編譯 `kvm-hello.c`，確認看到預期輸出。如果有問題，根據踩雷集錦逐一排除。

2. **改 guest 輸出多個字元**：修改 guest_code 讓它依序輸出 `H`、`i`、`!` 三個字元（各自 `mov al, X; out 0x10, al`），然後 HLT。修改 host 的 exit handler 累積字元，最後印 `[host] guest said: "Hi!"`.

3. **讀取 guest 暫存器**：在 `KVM_EXIT_HLT` 的處理裡，呼叫 `KVM_GET_REGS` 讀取 guest 的 rax、rbx、rcx，印出它們的值。修改 guest 在 HLT 前把一個特定值寫進 rbx，驗證你能正確讀出。

4. **未映射 GPA 觸發什麼？**：把 guest_code 最後一個指令改成 `jmp 0x2000`（jump 到沒有映射的 GPA），看 KVM 回傳什麼 exit_reason。這模擬 guest 訪問未映射記憶體的情況。

---

## 本章重點整理

- KVM hypervisor 的核心是 **ioctl 序列 + KVM_RUN 事件迴圈**，QEMU 和 kvmtool 本質上都是這個骨架的延伸。
- `kvm_run` 透過 `mmap(vcpu_fd)` 共享，避免每次 exit 都要多一次 ioctl。
- 16-bit real mode 的 sregs 必須全部手動填，尤其是 `type`、`present`、`unusable` 和 `CR0.PE = 0`。
- `KVM_EXIT_IO` 的資料透過 `run->io.data_offset` 指向，不在 `run->io` struct 本身裡。
- `KVM_EXIT_MMIO` 是 VM escape 高風險區，QEMU 的裝置模型就是在這裡把 GPA 轉成裝置操作。

---

## 自我檢核

1. `KVM_SET_USER_MEMORY_REGION` 的 `slot` 欄位的意義是什麼？一個 VM 最多可以有幾個 slot？
2. 為什麼 `kvm_run` 要用 mmap 共享，而不是每次 exit 都用 ioctl 回傳結構體？
3. `run->io.data_offset` 是相對於什麼的偏移？如果你把它當絕對地址用，會發生什麼？
4. CR0.PE = 0 vs CR0.PE = 1，KVM 的行為有什麼不同？
5. 同一個 VM 的兩個 vCPU 共享什麼？不共享什麼？

---

## 延伸閱讀

- **Linux kernel `Documentation/virt/kvm/api.rst`**：所有 KVM ioctl 的官方文件，exit reason 的完整列表。
- **kvm-hello-world**（`github.com/dpw/kvm-hello-world`）：本章程式碼的直接參考實作，比我們這個稍微完整一點。
- **kvmtool**（`git.kernel.org/pub/scm/linux/kernel/git/will/kvmtool.git`）：Linux 官方的輕量 KVM tool，比 QEMU 乾淨很多，適合讀懂 full VM 的實作。
- **Firecracker**（`github.com/firecracker-microvm/firecracker`）：AWS 的 production microVM，Rust 實作，架構決策都有文件，是讀懂 KVM 在生產中怎麼用的最好材料。
- **Intel SDM Vol. 3C：VMX chapter**：KVM 底下的 Intel VT-x 硬體規範，理解 VMCS（Virtual Machine Control Structure）的欄位才能看懂 KVM 的 low-level 實作。
- [Ch 6](./06-kvm-architecture.md)：本章所有 ioctl 的架構背景。
- [Ch 7](./07-kvm-to-qemu-exit.md)：QEMU 如何把 KVM exit 轉成裝置模型呼叫的完整路徑。

---

從 100 行開始，你現在對 KVM hypervisor 的骨架有了第一手理解——不是讀別人的描述，是自己把 ioctl 序列打出來、看 exit 進來、選擇怎麼處理。接下來把這個骨架延伸到 MMIO，就能模擬真實 CVE 的攻擊路徑。

→ [練習 A：手寫最小 KVM hypervisor + 攔截 MMIO exit](./practice-a-minimal-hypervisor.md)
