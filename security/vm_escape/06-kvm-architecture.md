# Ch 6 — KVM 架構：/dev/kvm、ioctl、vCPU 迴圈

> **目標**：搞清楚 KVM 的三層 fd 體系與 ioctl 序列，以及 vCPU 跑起來後 exit 長什麼樣子。

---

> **免責聲明**：以 KVM API 文件為準，本教材環境未有 /dev/kvm，ioctl 序列以文件原文及 kvm-hello-world 參考實作為依據。

---

## 為什麼需要這個？

[Ch 4](./04-intel-vtx.md) 講的是 VT-x 硬體：VMCS、VM-entry/VM-exit、EPT。但那些都是特權指令，ring 0 才能碰。我們寫 hypervisor 的時候不會直接呼叫 `VMLAUNCH`，而是透過 KVM 提供的 ioctl 介面。

KVM（Kernel-based Virtual Machine）是個 Linux kernel module，它把 VT-x（或 AMD-V）封裝成一組 ioctl。userspace VMM（最典型的是 QEMU）只需要 `open("/dev/kvm")`，接著一路呼叫 ioctl，就能建立 VM、設定記憶體對映、跑 vCPU——完全不需要自己寫 ring 0 程式碼。

理解這套 API 的意義不只是「知道怎麼用 KVM」。VM escape 漏洞大半發生在 userspace VMM 層（QEMU），而 QEMU 的核心架構就是建立在這套 ioctl 序列之上。不清楚 KVM 怎麼把控制權交回 userspace，就很難理解 MMIO handler 被觸發的機制——而 MMIO handler 正是大量 escape 洞的攻擊面。

---

## 先建立直覺

想像 KVM 是一個工廠的作業系統，/dev/kvm 是工廠大門，每台 VM 是一條獨立產線，每個 vCPU 是產線上的工人。

你走進大門（open /dev/kvm）之後：
1. 申請一條產線（KVM_CREATE_VM → VM fd）
2. 幫產線配置原料倉儲（KVM_SET_USER_MEMORY_REGION → 記憶體對映）
3. 雇用工人（KVM_CREATE_VCPU → vCPU fd）
4. 讓工人開工（KVM_RUN）

工人遇到「需要外部決策」的事情會暫停，告訴你理由（exit_reason），等你處理完之後繼續開工。這就是整個 vCPU 迴圈（run loop）的核心邏輯。

---

## 三層 fd 架構

```
/dev/kvm
  │  (KVM_GET_API_VERSION, KVM_CREATE_VM, ...)
  │
  ├── VM fd  ← KVM_CREATE_VM 回傳
  │     │  (KVM_SET_USER_MEMORY_REGION, KVM_CREATE_VCPU, ...)
  │     │
  │     └── vCPU fd  ← KVM_CREATE_VCPU 回傳
  │           │  (KVM_RUN, KVM_GET_REGS, KVM_SET_REGS, ...)
  │           │
  │           └── struct kvm_run  ← mmap(vCPU fd, KVM_GET_VCPU_MMAP_SIZE)
  │                 (exit_reason, io union, mmio union, ...)
```

每一層的 fd 只接受屬於那一層的 ioctl。對 VM fd 呼叫 `KVM_RUN` 會失敗；對 /dev/kvm fd 呼叫 `KVM_SET_USER_MEMORY_REGION` 也會失敗。層次不能混用。

---

## 核心 ioctl 序列

下面依呼叫順序列出，加上每個 ioctl 的作用與回傳值。

### 第一層：/dev/kvm fd

#### KVM_GET_API_VERSION

```c
int kvm_fd = open("/dev/kvm", O_RDWR | O_CLOEXEC);
int api_ver = ioctl(kvm_fd, KVM_GET_API_VERSION, 0);
// 必須 == 12，否則 kernel 太舊不能用
```

穩定 API 版本固定是 12。這個檢查不能省，不同版本 struct layout 不同。

#### KVM_CREATE_VM

```c
int vm_fd = ioctl(kvm_fd, KVM_CREATE_VM, 0);
// 回傳 VM fd，失敗回傳 -1
```

第三個參數通常是 0（x86 不需要 machine type）。ARM 上有不同 machine type 的選項，但 x86 直接填 0。

---

### 第二層：VM fd

#### KVM_SET_USER_MEMORY_REGION

這個 ioctl 是整個記憶體對映的核心，把 host 的 userspace 記憶體區段對應到 guest 的實體位址空間。

```c
struct kvm_userspace_memory_region region = {
    .slot            = 0,             // 記憶體槽編號，0 起始
    .guest_phys_addr = 0x1000,        // guest 看到的實體起始位址
    .memory_size     = 0x1000,        // 大小（bytes，必須 4K 對齊）
    .userspace_addr  = (uint64_t)mem, // host userspace 虛擬位址
    .flags           = 0,             // 通常 0；KVM_MEM_READONLY 可設唯讀
};
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);
```

欄位說明：

| 欄位 | 型別 | 意義 |
|------|------|------|
| `slot` | `__u32` | 記憶體槽 id，同一 VM 可有多個 slot |
| `guest_phys_addr` | `__u64` | EPT 對映的 GPA（Guest Physical Address）起點 |
| `memory_size` | `__u64` | 區段長度，必須是 page size 的倍數 |
| `userspace_addr` | `__u64` | host 側 HVA（Host Virtual Address）起點，必須 page 對齊 |
| `flags` | `__u32` | `KVM_MEM_LOG_DIRTY_PAGES`（dirty tracking）、`KVM_MEM_READONLY` |

`userspace_addr` 通常是先 `mmap(NULL, size, PROT_READ|PROT_WRITE, MAP_SHARED|MAP_ANONYMOUS, -1, 0)` 出來的。EPT 底層把這段 HVA 翻成 HPA（Host Physical Address），guest 存取 GPA 就走這條翻譯鏈。

同一個 slot 再呼叫一次可以更新對映，把 `memory_size` 設 0 可以移除這個 slot。

#### KVM_CREATE_VCPU

```c
int vcpu_fd = ioctl(vm_fd, KVM_CREATE_VCPU, 0 /* vcpu_id */);
```

多核 VM 就呼叫多次，vcpu_id 依序 0, 1, 2, ...。每個 vCPU 有自己獨立的 fd。

---

### 第三層：vCPU fd

#### KVM_GET_VCPU_MMAP_SIZE

```c
int mmap_size = ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0);
// 注意：這個 ioctl 對 kvm_fd（不是 vcpu_fd）呼叫
struct kvm_run *run = mmap(
    NULL, mmap_size,
    PROT_READ | PROT_WRITE,
    MAP_SHARED,
    vcpu_fd, 0
);
```

`kvm_run` 是 KVM 與 userspace VMM 溝通的共享記憶體頁。KVM 把 exit 資訊寫進去，userspace 讀出來處理。

#### KVM_GET_REGS / KVM_SET_REGS

```c
struct kvm_regs regs;
ioctl(vcpu_fd, KVM_GET_REGS, &regs);
// regs.rip, regs.rsp, regs.rax, ...
regs.rip = 0x1000;  // guest 的 instruction pointer
regs.rflags = 0x2;  // 最低位 reserved=1 必須設
ioctl(vcpu_fd, KVM_SET_REGS, &regs);
```

`kvm_regs` 包含通用暫存器與 rip、rflags。

#### KVM_GET_SREGS / KVM_SET_SREGS

```c
struct kvm_sregs sregs;
ioctl(vcpu_fd, KVM_GET_SREGS, &sregs);
// 設定 segment registers：cs, ds, es, fs, gs, ss
// 以及 cr0, cr3, cr4, efer 等 control registers
ioctl(vcpu_fd, KVM_SET_SREGS, &sregs);
```

`kvm_sregs` 裡每個 segment 是 `struct kvm_segment`，有 base、limit、selector、type、present 等欄位。進入 protected mode 或 long mode 都要在這裡設定正確的 segment descriptor。

#### KVM_RUN

```c
int ret = ioctl(vcpu_fd, KVM_RUN, 0);
```

這個 ioctl 讓 guest 開始執行，直到發生 VM exit 才回來。回來之後看 `run->exit_reason` 決定要做什麼。

---

## kvm_run 結構：exit 的解剖

`struct kvm_run` 的核心欄位：

```c
struct kvm_run {
    /* 輸入欄位（KVM_RUN 前 userspace 可設定） */
    __u8  request_interrupt_window;
    __u8  immediate_exit;
    /* ... */

    /* 輸出欄位（VM exit 後 KVM 填入） */
    __u32 exit_reason;       /* 為什麼 exit */
    __u8  ready_for_interrupt_injection;
    __u8  if_flag;
    __u16 flags;

    /* exit 相關資料，大 union */
    union {
        struct { /* KVM_EXIT_IO */
            __u8  direction; /* KVM_EXIT_IO_IN / KVM_EXIT_IO_OUT */
            __u8  size;      /* 1, 2, 4 bytes */
            __u16 port;
            __u32 count;
            __u64 data_offset; /* 資料在 kvm_run 結構內的偏移 */
        } io;

        struct { /* KVM_EXIT_MMIO */
            __u64 phys_addr; /* guest 試圖存取的 GPA */
            __u8  data[8];
            __u32 len;       /* 1, 2, 4, 8 bytes */
            __u8  is_write;  /* 1 = guest 寫入，0 = guest 讀取 */
        } mmio;

        struct { /* KVM_EXIT_HYPERCALL */
            __u64 nr;
            __u64 args[6];
            __u64 ret;
            __u32 longmode;
        } hypercall;

        /* ... 其他 exit 類型 */
    };
};
```

常見 exit_reason：

| exit_reason | 數值 | 意義 |
|-------------|------|------|
| `KVM_EXIT_UNKNOWN` | 0 | 不明原因，查 `hardware_exit_reason` |
| `KVM_EXIT_IO` | 2 | guest 執行 IN/OUT port 指令 |
| `KVM_EXIT_MMIO` | 6 | guest 存取未對映的 GPA（MMIO 區域） |
| `KVM_EXIT_INTR` | 10 | userspace 被 signal 打斷 |
| `KVM_EXIT_SHUTDOWN` | 8 | guest triple fault 或 SHUTDOWN |
| `KVM_EXIT_HLT` | 5 | guest 執行 HLT 指令 |
| `KVM_EXIT_SYSTEM_EVENT` | 24 | reset/shutdown 事件 |

### KVM_EXIT_MMIO 的重要性

```c
if (run->exit_reason == KVM_EXIT_MMIO) {
    uint64_t addr     = run->mmio.phys_addr;
    uint32_t len      = run->mmio.len;
    uint8_t  is_write = run->mmio.is_write;

    if (is_write) {
        uint64_t val = 0;
        memcpy(&val, run->mmio.data, len);
        handle_mmio_write(addr, val, len);
    } else {
        uint64_t val = handle_mmio_read(addr, len);
        memcpy(run->mmio.data, &val, len);
    }
}
```

MMIO exit 是 VM escape 漏洞最密集的攻擊面。guest 存取一個沒有對應真實 RAM 的 GPA，KVM 把這個 exit 丟給 QEMU，QEMU 根據 phys_addr 決定這是哪個虛擬裝置的 register，然後呼叫對應的 MMIO handler。如果 handler 有緩衝區溢位或 use-after-free，guest 就能藉此控制 host 的 QEMU 進程。

### KVM_EXIT_IO 的資料存取

```c
if (run->exit_reason == KVM_EXIT_IO) {
    void *data = (char *)run + run->io.data_offset;
    // 注意：data 是在 kvm_run 結構內的偏移，不是獨立指標
    if (run->io.direction == KVM_EXIT_IO_OUT) {
        // guest 在做 OUT port, data 裡是要輸出的值
    } else {
        // guest 在做 IN port，把值寫進 data
    }
}
```

IO port 的資料透過 `data_offset` 指向 kvm_run 結構內部的一塊區域，而不是用獨立的指標——這個設計初學者很容易弄錯。

---

## 標準 vCPU run loop 骨架

```c
while (1) {
    int ret = ioctl(vcpu_fd, KVM_RUN, 0);
    if (ret == -1 && errno == EINTR)
        continue; /* signal 打斷，重跑 */
    if (ret < 0) {
        perror("KVM_RUN");
        break;
    }

    switch (run->exit_reason) {
    case KVM_EXIT_HLT:
        printf("Guest HLT\n");
        goto done;

    case KVM_EXIT_IO:
        handle_io(run);
        break;

    case KVM_EXIT_MMIO:
        handle_mmio(run);
        break;

    case KVM_EXIT_SHUTDOWN:
        printf("Guest shutdown\n");
        goto done;

    default:
        fprintf(stderr, "Unhandled exit: %d\n", run->exit_reason);
        goto done;
    }
}
done:
```

每次 `KVM_RUN` 回來都要完整處理 exit_reason，處理完繼續迴圈，讓 KVM 重新入 guest。忘記 continue 或 break 會讓 vCPU 永遠不再執行。

---

## 底層機制：ioctl 到 VT-x 的對應

`KVM_RUN` ioctl 進 kernel 之後，大致路徑：

1. `kvm_arch_vcpu_ioctl_run()` → `vcpu_run()` → `vcpu_enter_guest()`
2. 呼叫 `kvm_x86_ops.run(vcpu)`，這在 Intel 上是 `vmx_vcpu_run()`
3. `vmx_vcpu_run()` 做 host state 儲存、`VMLAUNCH`/`VMRESUME`
4. VM exit 發生，CPU 跳回 `vmx_vcpu_run()` 後面的程式碼
5. `vmx_handle_exit()` 判斷 exit reason
6. 如果是 EPT violation 且目標 GPA 是 MMIO 區域，填 `run->mmio`，回傳給 userspace
7. `KVM_RUN` ioctl 返回，userspace 讀 `run->exit_reason`

[Ch 4](./04-intel-vtx.md) 的 EPT violation 就是在第 6 步被 KVM 轉換成 `KVM_EXIT_MMIO`。KVM 做的事情是把硬體 exit reason（VMCS 的 `VM_EXIT_REASON`）翻譯成 userspace 能理解的 `kvm_run.exit_reason`。

---

## 對比與取捨

### KVM vs WHPX（Windows Hypervisor Platform）

WHPX 是 Windows 上功能相似的 API（`WHvCreatePartition`, `WHvSetupPartition`, `WHvRunVirtualProcessor`）。概念幾乎一樣：三層物件（Partition/Processor）、設定記憶體、跑 vCPU、處理 exit。差別在：

- WHPX 沒有 fd，用 HANDLE
- Exit reason 換名字（`WHvRunVpExitReasonMemoryAccess` = KVM 的 `KVM_EXIT_MMIO`）
- Windows 上 MMIO exit 的 handler 機制相同，escape 攻擊面也類似

### KVM vs Xen HVM

Xen 的 HVM 模式有自己的 hypercall 介面，不走 /dev/kvm，但 MMIO emulation 的架構思路一致：guest 觸發 EPT violation → hypervisor 捕捉 → 轉給 device model（QEMU 或 Xen 自帶的 qemu-traditional）→ handler 處理。

### 多 slot vs 單 slot

一個 VM 可以有多個 `kvm_userspace_memory_region` slot（上限 `KVM_CAP_NR_MEMSLOTS`，通常 32 或 512）。QEMU 用多個 slot 對應 BIOS ROM、VRAM、RAM 的不同物理區段，讓不同區域有不同的 dirty tracking 或唯讀屬性。Slot 之間不能重疊 GPA 範圍。

---

## 踩雷集錦

**rflags bit 1 必須是 1**
`kvm_regs.rflags` 的 bit 1 是 x86 保留位，必須永遠為 1。初始化 `rflags = 2`（只設 bit 1），少了這個 guest 進不去。

**mmap 大小要用 KVM_GET_VCPU_MMAP_SIZE**
不要硬寫 `sizeof(struct kvm_run)`。kernel 可能在結構後面附加額外資料，mmap 大小必須用 ioctl 查。

**data_offset 是相對 kvm_run 的偏移**
`KVM_EXIT_IO` 的 `data_offset` 是 `uint64_t`，意思是「資料在 kvm_run 這塊 mmap 記憶體內從起點往後偏移多少」，不是獨立指標。用法：`(char *)run + run->io.data_offset`。

**KVM_SET_USER_MEMORY_REGION 的 userspace_addr 必須對齊**
`mmap` 預設回傳 page 對齊的位址，直接用沒問題。但如果你用 `malloc` 拿到的指標當 `userspace_addr`，就會被 kernel 拒絕。

**EINTR 要重試**
`KVM_RUN` 被 signal 打斷會回傳 -1 且 errno == EINTR。這不是錯誤，繼續 ioctl 就好。許多初學者看到 -1 直接 exit，然後不知道為何 VM 跑一跑就掛。

**slot 0 重設記憶體**
再次呼叫 `KVM_SET_USER_MEMORY_REGION` 用同樣的 slot 但不同的 `userspace_addr` 會更新對映。把 `memory_size` 設 0 是移除這個 slot。不小心在 slot 衝突的 GPA 範圍建兩個 slot 會被 kernel 拒絕。

---

## 進階：再往深一層

### dirty page tracking

設 slot flags 為 `KVM_MEM_LOG_DIRTY_PAGES`，之後可以用 `KVM_GET_DIRTY_LOG` 取得 dirty bitmap。live migration 就是靠這個在 VM 跑的同時追蹤哪些 page 被改過，再把 dirty page 傳到目標機器。從安全角度看，如果 hypervisor 的 dirty tracking 邏輯有 race，可能造成 TOCTOU。

### KVM_SET_IDENTITY_MAP_ADDR / KVM_SET_TSS_ADDR

KVM 在 x86 protected mode 下需要一塊 guest 物理記憶體放 TSS（Task State Segment）和 identity map 頁表，用來處理某些 VM exit 的 internal machinery。這兩個 ioctl 讓 VMM 指定這些結構的 GPA，避免和 guest OS 的記憶體佈局衝突。

### vCPU 的 coalesced MMIO

`KVM_REGISTER_COALESCED_MMIO` 可以把某段 MMIO 範圍設為「coalesced」——guest 的寫入不立刻觸發 exit，而是先放進 ring buffer，等 `KVM_RUN` 自然回來再一批處理。這減少 VM exit 次數，提升效能，但也讓 MMIO 的處理順序和時機變得複雜，是另一個潛在的攻擊面。

### ioeventfd 與 irqfd

`KVM_IOEVENTFD` 把特定 IO port 或 MMIO 位址的寫入掛鉤到一個 eventfd，不需要每次都 exit 到 userspace，由 kernel 直接發 eventfd 通知。`KVM_IRQFD` 讓 eventfd 直接觸發 guest 的中斷注入。這兩個機制讓 vhost（kernel-side virtio）成為可能，但也意味著某些 IO 路徑繞過了 QEMU 的 MMIO handler——從安全研究角度要注意哪些路徑走 ioeventfd、哪些走傳統 exit。

### nested virt（VMX inside VMX）

KVM 支援 `KVM_CAP_NESTED_STATE`，讓 guest 自己跑 KVM（L1 guest 跑 L2 guest）。nested virt 的程式碼是 KVM 最複雜的部分之一，也是近年高嚴重度漏洞（CVE-2021-22543 等）的溫床。

---

## 動手練習

**練習 1：列出 KVM API 版本與功能**

在有 KVM 的 Linux 環境（實體機或支援 nested virt 的 VM）：

```bash
ls -la /dev/kvm
# 看權限，通常是 kvm 群組或 root 才能讀寫

python3 -c "
import fcntl, struct, os
KVM_GET_API_VERSION = 0xAE00
fd = os.open('/dev/kvm', os.O_RDWR)
ver = fcntl.ioctl(fd, KVM_GET_API_VERSION, 0)
print('API version:', ver)
os.close(fd)
"
```

**練習 2：閱讀 kvm-hello-world 原始碼**

```bash
git clone https://github.com/dpw/kvm-hello-world.git
cd kvm-hello-world
# 閱讀 kvm-hello-world.c，找到以下部分並標注：
# 1. KVM_CREATE_VM 在哪一行
# 2. KVM_SET_USER_MEMORY_REGION 的 struct 怎麼填
# 3. vCPU run loop 在哪裡
# 4. 處理哪些 exit_reason
make && ./kvm-hello-world  # 如果環境有 /dev/kvm
```

**練習 3：追蹤 exit 序列**

修改 kvm-hello-world，在每次 `KVM_RUN` 回來之後印出 exit_reason 數值與名稱，觀察 guest 從啟動到 HLT 觸發了哪些 exit。記錄每種 exit 的數量。

**練習 4：研讀 KVM API 文件**

閱讀 https://www.kernel.org/doc/html/latest/virt/kvm/api.html 中 `KVM_SET_USER_MEMORY_REGION` 的完整說明，找出：

1. `slot` 的上限怎麼查
2. `KVM_MEM_READONLY` flag 的限制
3. 同一 slot 重新呼叫（resize）的行為

**練習 5：概念題**

不看資料，手畫出三層 fd 架構圖，標出：哪些 ioctl 對哪層 fd 呼叫、kvm_run 是怎麼取得的、MMIO exit 的資料流向。

---

## 本章重點整理

- KVM 把 VT-x 封裝成 ioctl 介面，三層：/dev/kvm fd → VM fd → vCPU fd
- `KVM_SET_USER_MEMORY_REGION` 的 struct 有五個關鍵欄位：slot、guest_phys_addr、memory_size、userspace_addr、flags
- vCPU fd 要 mmap（大小用 `KVM_GET_VCPU_MMAP_SIZE` 查）才能取得 `struct kvm_run`
- `KVM_RUN` 讓 guest 跑到下個 exit，回來後讀 `run->exit_reason`
- `KVM_EXIT_MMIO` 時，`run->mmio` 給出 phys_addr、data[8]、len、is_write，userspace VMM 負責模擬對應的裝置行為
- `KVM_EXIT_IO` 的資料在 `(char *)run + run->io.data_offset`，不是獨立指標
- 踩雷：rflags bit 1 必須為 1、mmap size 用 ioctl 查、EINTR 要重試

---

## 自我檢核

1. 為什麼 `KVM_GET_VCPU_MMAP_SIZE` 要對 kvm_fd 而不是 vcpu_fd 呼叫？
2. `kvm_userspace_memory_region.userspace_addr` 和 `guest_phys_addr` 分別代表哪個位址空間？
3. guest 執行 `mov [0xFEE00300], eax`（APIC MMIO 區域），KVM 如何讓 QEMU 知道這件事？資料在哪裡？
4. vCPU run loop 如果不處理 `KVM_EXIT_INTR`（EINTR）會發生什麼？
5. 兩個 memory slot 可以有重疊的 `guest_phys_addr` 範圍嗎？
6. `KVM_EXIT_IO` 的 `direction` 欄位為 `KVM_EXIT_IO_IN` 時，userspace 應該把資料放在哪裡？
7. `KVM_MEM_LOG_DIRTY_PAGES` 和 live migration 的關係是什麼？

---

## 延伸閱讀

- **KVM API 官方文件**：https://www.kernel.org/doc/html/latest/virt/kvm/api.html — 每個 ioctl 的完整說明，疑問先查這裡
- **kvm-hello-world**：https://github.com/dpw/kvm-hello-world — 最精簡的完整 KVM hypervisor，約 300 行 C，適合對照本章讀
- **kvmtool**：https://github.com/kvikindra/kvmtool — 比 QEMU 簡單得多的教學用 VMM，程式碼量可讀
- **深入 Linux KVM 架構**：《Professional Linux Kernel Architecture》第 18 章，或 LWN 的 KVM 系列文章
- **QEMU 的 memory_region**：https://qemu.readthedocs.io/en/latest/devel/memory.html — QEMU 怎麼在 KVM 的 slot 之上建自己的 MemoryRegion 層
- **James Bottomley 的 KVM 介紹**：https://www.linux-kvm.org/images/e/e4/Kvmforum08_boc.pdf — 偏舊但把底層講得清楚

---

KVM 的三層 fd 體系和 vCPU run loop 是理解 QEMU 架構的地基；搞清楚 MMIO exit 怎麼從 guest 到 userspace，才能看懂後面那些 escape 漏洞是在攻哪個 handler。

→ [Ch 7 從 KVM 到 QEMU：userspace VMM 怎麼接手 exit](./07-kvm-to-qemu-exit.md)
