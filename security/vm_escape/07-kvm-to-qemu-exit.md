# Ch 7 — 從 KVM 到 QEMU：userspace VMM 怎麼接手 exit

> **目標**：搞清楚 KVM_RUN 返回後，QEMU 如何依 exit reason 分派到對應的設備模擬，這條路是後續所有設備逃逸的觸發骨幹。

## 為什麼需要這個？

VM escape 漏洞的核心觸發路徑幾乎全都長這樣：guest 碰了某個 MMIO 或 PIO 位址 → CPU 拋 VMEXIT → KVM 丟回 userspace → QEMU 找到對應設備並呼叫它的 callback。

漏洞就藏在那個 callback 裡，或是 QEMU 把 guest 資料餵進 callback 之前沒驗乾淨。不管你最後打的是哪個設備，這條從 KVM 到 QEMU 的分派路徑都一模一樣。讀懂它，後面打設備才不會每次都要重頭推。

## 先建立直覺

設想你是 QEMU 的主執行緒，正在跑一個 vcpu。你對 KVM 下了 `KVM_RUN` ioctl，然後就卡在核心態等著。

等到什麼時候才返回？

等到 guest 做了一件 KVM 自己搞不定、需要 userspace 幫忙的事。這個「需要幫忙」的場景被記錄在一塊共享記憶體 `struct kvm_run` 裡，QEMU 醒來之後去讀 `exit_reason` 欄位，就知道是哪種情況。

整件事的精髓在於：**KVM 自己只負責 CPU 虛擬化和記憶體虛擬化，設備不歸它管**。一旦 guest 存取了設備 I/O 空間，KVM 就把控制權還給 QEMU，讓 QEMU 去模擬那個設備的行為，然後再把 guest 恢復繼續跑。

這個「guest → KVM → QEMU → KVM → guest」的迴圈，就是整個設備模擬架構的命脈。

## struct kvm_run：KVM 和 QEMU 的交換空間

`struct kvm_run` 是透過 mmap 映射到 QEMU 進程位址空間的共享記憶體，一個 vCPU 一塊。KVM_RUN 返回後，QEMU 直接讀這塊記憶體，不需要額外 ioctl。

關鍵欄位：

```c
struct kvm_run {
    /* in */
    __u8 request_interrupt_window;
    /* ... */

    /* out */
    __u32 exit_reason;          /* QEMU 首先讀這個 */
    __u8  ready_for_interrupt_injection;
    __u8  if_flag;

    union {
        /* KVM_EXIT_MMIO */
        struct {
            __u64 phys_addr;    /* guest 存取的 GPA */
            __u8  data[8];      /* 要讀寫的資料 */
            __u32 len;          /* 1 / 2 / 4 / 8 bytes */
            __u8  is_write;     /* 0=read, 1=write */
        } mmio;

        /* KVM_EXIT_IO */
        struct {
            __u8  direction;    /* KVM_EXIT_IO_IN / KVM_EXIT_IO_OUT */
            __u8  size;         /* 1, 2, or 4 */
            __u16 port;         /* port 號碼 */
            __u32 count;        /* rep 次數 */
            __u64 data_offset;  /* 資料在 kvm_run 結構的 offset */
        } io;

        /* KVM_EXIT_INTERNAL_ERROR */
        struct {
            __u32 suberror;
            __u32 ndata;
            __u64 data[16];
        } internal;

        /* ... 其他 exit reason 各自的 union 成員 */
    };
};
```

exit reason 是一個整數，定義在 `linux/kvm.h`：

| 值 | 名稱 | 含義 |
|----|------|------|
| 2  | `KVM_EXIT_IO` | PIO IN/OUT 指令 |
| 5  | `KVM_EXIT_HLT` | guest 執行 HLT |
| 6  | `KVM_EXIT_MMIO` | MMIO 存取（EPT violation 後 KVM 轉發） |
| 8  | `KVM_EXIT_SHUTDOWN` | triple fault 或 INIT/SIPI |
| 17 | `KVM_EXIT_INTERNAL_ERROR` | KVM 內部錯誤 |

## 主要 exit reason 拆解

### KVM_EXIT_MMIO（值 6）

這是我們最關心的一個，也是設備逃逸最常見的觸發路徑。

guest 存取了某個 GPA，但那個 GPA 沒有對應的 EPT entry（或 EPT entry 的權限不符）。CPU 拋 EPT violation，VMEXIT 進 KVM。

KVM 在這裡做一個關鍵判斷：這個 GPA 有沒有對應的 KVM memory slot？

- 有 → 這是正常的 guest RAM，KVM 去填 EPT entry，直接恢復 guest，**不需要返回 userspace**。
- 沒有 → 這是 MMIO 範圍，KVM 自己搞不定，設置 `exit_reason = KVM_EXIT_MMIO`，填好 `kvm_run.mmio` 結構，返回 QEMU。

QEMU 讀到 `KVM_EXIT_MMIO` 之後，根據 `phys_addr`（GPA）找到對應的 `MemoryRegion`，呼叫它的 `.read` 或 `.write` ops。

**注意**：如果是讀取（`is_write == 0`），QEMU 把結果填回 `kvm_run.mmio.data[]`，然後再 ioctl KVM_RUN，KVM 在恢復 guest 之前把這個值注入到 guest 的目標暫存器。

### KVM_EXIT_IO（值 2）

x86 的 IN/OUT 指令直接觸發 VMEXIT（前提是 VMCS 的 I/O bitmap 標記了這個 port）。KVM 填好 `kvm_run.io` 結構，返回 QEMU。

`io.data_offset` 指向 kvm_run 結構內的某個偏移，實際資料放在那邊，不在 io union 本身裡。QEMU 要這樣取資料：

```c
void *data = (uint8_t *)cpu->kvm_run + cpu->kvm_run->io.data_offset;
```

QEMU 根據 `io.port` 查 ioport dispatch table，找到對應設備，呼叫其 IN/OUT handler。

### KVM_EXIT_HLT（值 5）

guest 執行了 HLT 指令（在非 hypervisor mode 下 HLT 通常是等待中斷）。QEMU 的處理是讓這個 vCPU 執行緒等待，直到有 pending 的中斷才再次注入並恢復執行。

對 exploit 的意義不大，但如果你的 payload 裡意外跑到 HLT，整個 guest 就凍住了，是 crash 以外另一種讓 guest 卡死的方式。

### KVM_EXIT_SHUTDOWN（值 8）

Triple fault（三重錯誤）發生，guest CPU 進入 shutdown 狀態。QEMU 收到這個通常選擇重置 guest 或直接終止。

在 CTF 場景裡，如果你的漏洞利用過程中 guest 崩了（kernel panic → triple fault），就會看到這個 exit reason，表示你的 exploit 失敗了。

### KVM_EXIT_INTERNAL_ERROR（值 17）

KVM 遇到自己的內部錯誤，例如 emulation 失敗。`internal.suberror` 提供更細的錯誤碼，例如：

- `KVM_INTERNAL_ERROR_EMULATION`（1）：KVM 嘗試 instruction emulation 但失敗
- `KVM_INTERNAL_ERROR_SIMUL_EX`（2）：模擬異常失敗

這個 exit reason 在正常執行幾乎不出現，如果出現了通常代表 guest state 已經嚴重不一致。

## 底層機制：QEMU 的分派流程

QEMU 的 KVM 整合主體在 `accel/kvm/kvm-all.c`（QEMU 9.0 source tree 路徑）。

核心函式是 `kvm_cpu_exec()`，整個 vCPU 執行迴圈就在這裡：

```c
/* accel/kvm/kvm-all.c (簡化) */
int kvm_cpu_exec(CPUState *cpu)
{
    struct kvm_run *run = cpu->kvm_run;
    int ret, run_ret;

    do {
        /* 1. 注入 pending 中斷（如果有） */
        kvm_arch_pre_run(cpu, run);

        /* 2. 進核心，跑 guest */
        run_ret = kvm_vcpu_ioctl(cpu, KVM_RUN, 0);

        /* 3. KVM_RUN 返回了，看 exit_reason */
        trace_kvm_run_exit(cpu->cpu_index, run->exit_reason);

        switch (run->exit_reason) {
        case KVM_EXIT_IO:
            /* 交給 arch 層處理 PIO */
            ret = kvm_handle_io(run->io.port,
                                (uint8_t *)run + run->io.data_offset,
                                run->io.direction,
                                run->io.size,
                                run->io.count);
            break;

        case KVM_EXIT_MMIO:
            /* 交給 memory subsystem 處理 MMIO */
            address_space_rw(&address_space_memory,
                             run->mmio.phys_addr,
                             MEMTXATTRS_UNSPECIFIED,
                             run->mmio.data,
                             run->mmio.len,
                             run->mmio.is_write);
            ret = 0;
            break;

        case KVM_EXIT_HLT:
            ret = kvm_handle_halt(cpu);
            break;

        case KVM_EXIT_SHUTDOWN:
            qemu_system_reset_request(SHUTDOWN_CAUSE_GUEST_RESET);
            ret = EXCP_INTERRUPT;
            break;

        case KVM_EXIT_INTERNAL_ERROR:
            ret = kvm_handle_internal_error(cpu, run);
            break;

        /* ... 其他 case ... */

        default:
            /* 未知的 exit reason，通常是 bug */
            ret = -1;
            break;
        }
    } while (ret == 0);

    return ret;
}
```

MMIO 這條路接著進 QEMU 的記憶體子系統：

```
address_space_rw()
  └─ address_space_translate()        ← 找到對應的 MemoryRegion
  └─ memory_region_dispatch_read()    ← 或 _write()
       └─ mr->ops->read(opaque, addr, size)   ← 設備 callback
```

`memory_region_dispatch_read/write()` 在 `memory.c` 裡，是 QEMU 設備模擬框架的核心入口，[Ch 11](./11-device-emulation-dispatch.md) 會完整拆解這段。

PIO 的路徑稍有不同：

```
kvm_handle_io(port, data, direction, size, count)
  └─ cpu_outb() / cpu_inb() / cpu_outw() / ...
       └─ address_space_rw(&address_space_io, port, ...)
            └─ 同樣走 memory_region_dispatch
```

QEMU 把 PIO 位址空間也抽象成一個 `AddressSpace`（`address_space_io`），和 MMIO 的 `address_space_memory` 用同一套 dispatch 機制。所以 PIO 和 MMIO 在 QEMU 層面的抽象幾乎一樣，差別只在用哪個 AddressSpace 物件去查。

## 完整路徑圖

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  GUEST (ring 0 / ring 3)                                        │
  │                                                                  │
  │  MOV [0xFEA00000], eax   ← 寫 MMIO 位址（例如 virtio 控制器）  │
  └────────────────┬─────────────────────────────────────────────────┘
                   │ CPU 查 EPT，GPA 0xFEA00000 無對應 entry
                   ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  CPU HARDWARE                                                    │
  │                                                                  │
  │  EPT violation → VMEXIT（exit reason: EPT_VIOLATION）           │
  │  VM-exit information 寫入 VMCS                                   │
  └────────────────┬─────────────────────────────────────────────────┘
                   │
                   ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  KVM（核心態）                                                   │
  │                                                                  │
  │  vmx_handle_exit()                                               │
  │    └─ handle_ept_violation()                                     │
  │         ├─ GPA 有對應 KVM memory slot？                          │
  │         │   YES → 補 EPT entry，直接 VMRESUME，不回 userspace   │
  │         │   NO  → 這是 MMIO 範圍                                 │
  │         └─ kvm_io_bus_write() 嘗試 in-kernel 設備（如 APIC）    │
  │              ├─ 成功 → VMRESUME                                  │
  │              └─ 失敗 → 填 kvm_run.exit_reason = KVM_EXIT_MMIO   │
  │                        填 kvm_run.mmio.{phys_addr, len, ...}    │
  │                        return to userspace（KVM_RUN ioctl 返回） │
  └────────────────┬─────────────────────────────────────────────────┘
                   │  ioctl(KVM_RUN) 返回
                   ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  QEMU（userspace，accel/kvm/kvm-all.c）                         │
  │                                                                  │
  │  kvm_cpu_exec()                                                  │
  │    └─ switch(run->exit_reason)                                   │
  │         └─ case KVM_EXIT_MMIO:                                   │
  │              address_space_rw(&address_space_memory,             │
  │                               run->mmio.phys_addr,              │
  │                               ...,                               │
  │                               run->mmio.data,                   │
  │                               run->mmio.len,                     │
  │                               run->mmio.is_write)               │
  └────────────────┬─────────────────────────────────────────────────┘
                   │
                   ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  QEMU Memory Subsystem（memory.c）                               │
  │                                                                  │
  │  address_space_rw()                                              │
  │    └─ address_space_translate(GPA 0xFEA00000)                   │
  │         └─ 找到對應 MemoryRegion（例如 virtio-net BAR0）         │
  │    └─ memory_region_dispatch_write(mr, offset, data, size)      │
  │         └─ mr->ops->write(mr->opaque, offset, data, size)       │
  └────────────────┬─────────────────────────────────────────────────┘
                   │
                   ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  DEVICE EMULATION（例如 hw/net/virtio-net.c）                    │
  │                                                                  │
  │  virtio_net_write(opaque, offset, value, size)                   │
  │    ← 漏洞就在這裡（或它呼叫的函式）                              │
  └──────────────────────────────────────────────────────────────────┘
                   │
                   ▼ QEMU 處理完後
  ┌──────────────────────────────────────────────────────────────────┐
  │  回到 KVM                                                        │
  │                                                                  │
  │  ioctl(KVM_RUN) 再次呼叫                                         │
  │  KVM 恢復 guest 執行，繼續從下一條指令跑                         │
  └──────────────────────────────────────────────────────────────────┘
```

這條路是固定的。不管你打什麼設備，chain 都長這樣。

## 對比與取捨

### in-kernel 設備 vs QEMU 設備

不是所有 I/O 都要繞一圈 QEMU。KVM 有 in-kernel 設備模擬，常見的有：

- LAPIC（Local APIC，`arch/x86/kvm/lapic.c`）
- IOAPIC（`virt/kvm/ioapic.c`）
- PIT（`arch/x86/kvm/i8254.c`）
- KVM clock（`arch/x86/kvm/x86.c`）

這些設備的 I/O 由 KVM 在核心態直接處理，KVM_RUN **不會返回** userspace。好處是速度快（省掉一次 context switch），壞處是程式碼在核心態，如果有 bug 就是 kernel bug，影響面更大。

QEMU 設備（virtio、e1000、AHCI 等）全部在 userspace，I/O 路徑多一圈 context switch，但開發維護容易，隔離性也更好（設備 bug 頂多打垮 QEMU 進程，不會直接打垮 host kernel）。

VM escape 通常打 QEMU 設備，而不是 in-kernel 設備，就是因為 QEMU 設備的 codebase 更大、歷史更久、attack surface 更廣。

### PIO vs MMIO

PIO 需要特權指令（IN/OUT），在 ring 3 的 userspace 程式直接跑這兩條指令會 GP fault。所以 PIO 的 exploit 只有在 guest kernel mode 才能觸發。

MMIO 是普通的記憶體存取指令（MOV），不需要特權。只要 IOMMU 或 page table 允許 guest userspace 映射到設備的 MMIO 範圍，ring 3 就能直接觸發。這讓 MMIO 的 attack surface 更廣，從 guest kernel 到 guest user 進程都可以打。

## 踩雷集錦

**誤區一：以為 KVM_RUN 返回就一定是錯誤**

KVM_RUN 返回完全正常，這是設計如此。每次 guest 碰到需要 userspace 處理的 I/O 就會返回一次，正常的 VM 跑起來每秒可能返回幾萬次。

**誤區二：以為 MMIO data 是 guest 真正的記憶體**

`kvm_run.mmio.data[]` 只有 8 bytes，存的是這次 I/O 的資料值，不是 guest 記憶體的指標。不要去 dereference 它。

**誤區三：PIO 的 data 也在 io 結構裡**

PIO 的資料不在 `kvm_run.io` union 本身，而是在 `kvm_run` 的另一個位置，透過 `kvm_run.io.data_offset` 計算偏移來取得。直接讀 `io` union 的其他欄位拿不到資料。

**誤區四：QEMU 改完 kvm_run.mmio.data 之後要手動通知 KVM**

不用。QEMU 改完共享記憶體直接再 ioctl KVM_RUN，KVM 恢復 guest 之前會自動把 `kvm_run.mmio.data` 的值注入到 guest 的目標暫存器。你只需要保證在再次 ioctl 之前把值寫進去。

**誤區五：每個 exit_reason 都需要 QEMU 處理**

有些 exit reason QEMU 可以什麼都不做，直接回 KVM 繼續跑（ret = 0）。有些則需要做完整的處理才能繼續。把 `KVM_EXIT_MMIO` 當成 no-op 跳過，guest 讀到的值就是垃圾，輕則功能異常，重則 guest crash。

## 進階：再往深一層

### Fast MMIO / MMIO coalescing

頻繁的 MMIO I/O（例如 VGA 寫螢幕）每次都要繞一圈 context switch，overhead 很大。KVM 提供了 coalesced MMIO 機制：guest 的 MMIO 寫可以先快取在 ring buffer 裡，不立刻返回 userspace。QEMU 定期去清這個 buffer，批次處理。

在 `include/linux/kvm.h` 裡可以找到 `kvm_coalesced_mmio_ring` 結構。啟用方式是 `KVM_REGISTER_COALESCED_MMIO` ioctl。

### dirty logging 和 MMIO 的互動

KVM dirty log 追蹤哪些 guest RAM page 被寫過，用於 live migration。但 MMIO 不是真實記憶體，不走 dirty log。如果你的 exploit 依賴竄改 MMIO 返回值來影響 migration 行為，這個假設是錯的。

### KVM_EXIT_MMIO 和 instruction emulation

有些情況 KVM 拿到 EPT violation 之後會先嘗試 instruction emulation（在核心態解析 guest 指令，自己模擬 MOV 的語意），而不是直接把 exit 丟給 QEMU。這條 emulation path 在 `arch/x86/kvm/x86.c` 的 `x86_emulate_instruction()` 裡。

這條路歷史上出過幾個 bug，包括 CVE-2017-12188（emulation path 沒驗 segment limit）。不是今天的重點，但要知道這條路存在，KVM 不是每次都乾淨地把 MMIO exit 傳給 QEMU。

### vhost 和 bypass

virtio 設備有個 vhost 模式，資料平面（data plane）直接在 host kernel 處理，不經過 QEMU userspace。這讓 virtio 的高頻 I/O 不需要每次都 context switch 到 QEMU。但 control plane 還是在 QEMU，設備初始化、feature negotiation 等還是走 KVM_RUN 路徑。

## 動手練習

**練習一：trace exit reason 分佈**

在 Linux guest 裡用 KVM 的 tracepoint 看 exit reason 的分佈，需要 host root：

```bash
# host side
echo 1 > /sys/kernel/debug/tracing/events/kvm/kvm_exit/enable
# 跑一個 QEMU VM，讓它 boot，然後
cat /sys/kernel/debug/tracing/trace | grep kvm_exit | \
  awk '{print $NF}' | sort | uniq -c | sort -rn | head -20
# 關掉 trace
echo 0 > /sys/kernel/debug/tracing/events/kvm/kvm_exit/enable
```

觀察哪些 exit reason 出現頻率最高，思考為什麼。

**練習二：寫一個最小 userspace 程式列印 exit reason**

不用跑完整 VM，參考 Ch 6 的 KVM API 骨架，跑一段只包含 `OUT 0x10, AL` 指令的 guest code，然後在 userspace 印出 `kvm_run.exit_reason` 的值。驗證看到的是 `KVM_EXIT_IO`（2）。

```c
/* guest code: 簡單的 PIO OUT */
unsigned char code[] = {
    0xb0, 0x42,       /* MOV AL, 0x42 */
    0xe6, 0x10,       /* OUT 0x10, AL  */
    0xf4,             /* HLT           */
};
/* 跑起來應該先看到 KVM_EXIT_IO，再看到 KVM_EXIT_HLT */
```

**練習三：讀 QEMU source，找 KVM_EXIT_MMIO 的處理**

找 QEMU 9.0 source tree：

```bash
git clone https://github.com/qemu/qemu.git
cd qemu
grep -n "KVM_EXIT_MMIO" accel/kvm/kvm-all.c
# 然後往上找 kvm_cpu_exec 函式，閱讀整個 switch 塊
```

確認你看到的 `address_space_rw()` 呼叫和上面圖裡描述的一致。

## 本章重點整理

- `KVM_RUN` ioctl 返回 ≠ 錯誤，是 KVM 請求 QEMU 處理 I/O
- `struct kvm_run` 是 mmap 共享記憶體，QEMU 直接讀 `exit_reason` 決定後續行為
- `KVM_EXIT_MMIO`（6）：guest 碰了沒有 KVM memory slot 的 GPA，KVM 無法自己處理
- `KVM_EXIT_IO`（2）：guest 執行 PIO IN/OUT 指令
- QEMU 的 `kvm_cpu_exec()` 是核心迴圈，在 `accel/kvm/kvm-all.c`
- MMIO 路徑：`address_space_rw()` → `memory_region_dispatch_write()` → 設備 `ops->write()` callback
- PIO 路徑：`kvm_handle_io()` → `address_space_rw(&address_space_io, ...)` → 同一套 dispatch
- 這條路是所有設備逃逸的固定觸發 chain，漏洞在最末端的設備 callback 裡

## 自我檢核

1. KVM_RUN 返回後，QEMU 第一件事讀哪個欄位？這個欄位在哪塊記憶體裡？
2. `KVM_EXIT_MMIO` 觸發的前提條件是什麼？KVM 什麼情況下不會觸發這個 exit 而是直接恢復 guest？
3. MMIO read 和 MMIO write 在 `kvm_run.mmio` 裡用哪個欄位區分？QEMU 處理 MMIO read 之後，結果怎麼回到 guest 的目標暫存器？
4. PIO 的 `data_offset` 是什麼？為什麼不直接把資料放在 `kvm_run.io` union 裡？
5. In-kernel 設備（如 LAPIC）和 QEMU 設備在 exit 路徑上的根本差異是什麼？這對 attack surface 有什麼影響？
6. `kvm_cpu_exec()` 在哪個 QEMU source 檔案裡？找到它之後，`KVM_EXIT_MMIO` 的 case 最終呼叫哪個函式？

## 延伸閱讀

> **聲明**：以下 QEMU source 路徑基於 QEMU 9.0 source tree 結構，不同版本可能有出入。

- `accel/kvm/kvm-all.c`：`kvm_cpu_exec()` 所在，KVM 整合的核心
- `softmmu/memory.c`：`address_space_rw()`、`memory_region_dispatch_read/write()` 實作
- `linux/kvm.h`：所有 exit reason 定義和 `struct kvm_run` 完整宣告
- [KVM API 文件](https://www.kernel.org/doc/html/latest/virt/kvm/api.html)：官方 API 文件，`KVM_RUN` 一節完整描述 exit reason 語意
- [VENOM（CVE-2015-3456）](https://venom.crowdstrike.com/)：透過 `KVM_EXIT_IO` 觸發的 FDC 設備漏洞，最著名的 MMIO/PIO escape 案例之一
- [Ch 4](./04-intel-vtx.md)：VMEXIT 機制底層
- [Ch 5](./05-ept-second-level-paging.md)：EPT violation 如何產生
- [Ch 6](./06-kvm-architecture.md)：KVM ioctl 和 kvm_run 共享記憶體建立方式

每次 guest 執行一條 MMIO 存取指令，整條從硬體到 QEMU callback 的路就走一遍。

→ [Ch 8 手寫最小 KVM hypervisor：100 行跑一個 guest](./08-minimal-kvm-hypervisor.md)
