# Ch 9 — QEMU 架構全圖：main loop、Memory API、QOM

> **目標**：把 QEMU 從「一個黑盒虛擬機器程式」拆解成「一個有清晰執行緒模型、事件迴圈和物件系統的 Linux userspace 行程」，建立後續利用漏洞的心智基礎。

---

## 為什麼需要這個？

Ch 7 我們看到 KVM_RUN ioctl 讓 guest code 直接跑在 CPU 上，VMEXIT 發生後核心把控制權還給 userspace。但「還給 userspace」之後，接手的是誰、在哪個函式、哪個執行緒？

答案是 QEMU。

這件事看起來平凡，但對 VM escape 研究者的意義非常大：**QEMU 就是一個普通的 Linux 使用者行程（userspace process）**。它的 heap 是 glibc 的 heap，它的記憶體佈局可以被 `/proc/[pid]/maps` 讀出來，它的函式指標放在可被覆寫的記憶體裡。你在 Ch 3～Ch 7 學的那些 KVM 內部機制，讓 guest OS 跑得又快又安全——但一旦 QEMU 本身有漏洞，你就在做一道標準的 userspace pwn 題，只是 target 比較特殊。

QEMU（Quick EMUlator）最初是 Fabrice Bellard 在 2003 年為了解決「在 x86 上跑 ARM 二進位」的問題而寫的純軟體模擬器（TCG 路徑）。KVM 合併進 Linux kernel 之後（2.6.20，2007 年），QEMU 加入了 KVM 加速後端，成為今天 Linux 虛擬化生態的標準搭配：KVM 負責高效能的 CPU/記憶體虛擬化，QEMU 負責裝置模擬和 lifecycle 管理。

這章的工作是建立「QEMU 程式內部長什麼樣子」的全圖，讓 Ch 10～Ch 14 的細節有地方可以嵌入。

---

## 先建立直覺

### QEMU 是一個「有很多職責的管家」

把 KVM + QEMU 的分工想成這樣：

```
┌─────────────────────────────────────────────────────────────┐
│                    Host Linux Kernel                        │
│                                                             │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                  KVM 子系統                         │  │
│   │  - 管理 VM 的 CPU 狀態（VMCS/VMCB）                │  │
│   │  - 管理 EPT（Extended Page Table）                  │  │
│   │  - 提供 /dev/kvm、/dev/kvm/vmN/vcpuN 介面           │  │
│   └───────────────────────────┬─────────────────────────┘  │
│                               │ ioctl                       │
└───────────────────────────────┼─────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────┐
│                    QEMU 行程（userspace）                    │
│                                                             │
│  main thread         vCPU thread(s)      iothread(s)        │
│  ┌──────────┐        ┌──────────────┐    ┌───────────┐      │
│  │GMainLoop │        │ KVM_RUN ioctl│    │virtio-blk │      │
│  │timer/fd  │◄──────►│ VMEXIT 處理  │    │ I/O 完成  │      │
│  │BH 排程   │        │ MMIO/PIO 分發│    │ callback  │      │
│  └──────────┘        └──────────────┘    └───────────┘      │
│                                                             │
│  QOM 物件樹：                                               │
│    machine → pcibus → e1000 → ...                           │
│    machine → pcibus → virtio-net → ...                      │
│                                                             │
│  記憶體：                                                   │
│    guest RAM = 一段 mmap 的匿名映射                         │
│    MMIO 由 MemoryRegion 描述，不一定有實體 mapping          │
└─────────────────────────────────────────────────────────────┘
```

關鍵認識：**QEMU 和你寫的任何 C 程式一樣，跑在 Linux userspace**。它有 `.text`、`.data`、heap、stack，可以被 gdb attach，可以被 strace 跟蹤。當 guest 試圖對 MMIO 空間寫一個值，最終是 QEMU 某個函式被呼叫去「模擬硬體行為」——如果那個函式有 buffer overflow，你就可以利用它。

---

## 執行緒模型：三種角色

QEMU 行程內部有三類執行緒，職責截然不同。

### main thread（主執行緒）

```
src: util/main-loop.c
     system/runstate.c
```

主執行緒跑一個事件迴圈。QEMU 9.x 預設用 GLib 的 `GMainLoop`，但也有自己的 wrapper `qemu_main_loop()`。事件來源有三種：

1. **fd 事件**：對 `/dev/kvm` 的 fd、virtio 的 eventfd、tapfd 等。底層用 `ppoll()` 或 `epoll`（GLib 自動選）。
2. **timer**：`QEMUTimer`，精度到 nanosecond，背後是 `timerfd` 或 `CLOCK_REALTIME`。
3. **Bottom Half（BH）**：可以從任意執行緒排程的「延遲回呼」。BH 不是真正的 half-interrupt，只是「等 main loop 下一輪再跑這個 callback」的機制，用來安全地跨執行緒觸發主執行緒的工作。

```c
/* util/main-loop.c（簡化，非完整程式碼） */
static int os_host_main_loop_wait(int64_t timeout)
{
    GPollFD poll_fds[MAX_POLL_FDS];
    int n_poll_fds;
    int ret;

    /* 收集所有要 poll 的 fd */
    n_poll_fds = glib_pollfds_fill(&timeout);

    /* 解鎖 BQL 讓 vCPU thread 可以繼續 */
    qemu_mutex_unlock_iothread();
    ret = ppoll(poll_fds, n_poll_fds, ...);
    qemu_mutex_lock_iothread();

    /* 分發 fd 事件 */
    glib_pollfds_poll();
    return ret;
}
```

注意 `BQL`（Big QEMU Lock，也叫 `iothread_mutex`）：這是 QEMU 最重要的全域鎖。幾乎所有裝置模擬的 callback 都必須在持有 BQL 的情況下執行，目的是避免 race condition。這個設計很像 CPython 的 GIL——簡單有效，但是並行瓶頸。

### vCPU thread（虛擬 CPU 執行緒）

每個 vCPU 對應一個 POSIX thread。KVM 路徑下，這個執行緒的核心是一個緊密迴圈：

```c
/* accel/kvm/kvm-accel-ops.c（簡化） */
static void *kvm_vcpu_thread_fn(void *arg)
{
    CPUState *cpu = arg;

    qemu_mutex_lock_iothread();     /* 持有 BQL */

    while (1) {
        if (cpu_can_run(cpu)) {
            qemu_mutex_unlock_iothread();
            ret = kvm_cpu_exec(cpu);   /* KVM_RUN ioctl */
            qemu_mutex_lock_iothread();
        }
        /* 處理 pending work：IRQ injection、migration 等 */
        qemu_wait_io_event(cpu);
    }
}
```

`kvm_cpu_exec()` 在 `accel/kvm/kvm-all.c`，裡面是 `ioctl(cpu->kvm_fd, KVM_RUN, 0)`。這個 ioctl 阻塞，直到 VMEXIT。

VMEXIT 回來後，`kvm_cpu_exec()` 檢查 `run->exit_reason`，分發到對應的處理器。常見的 exit reason 有：
- `KVM_EXIT_IO`：PIO 讀寫
- `KVM_EXIT_MMIO`：MMIO 讀寫（Ch 7 的重點就是這個）
- `KVM_EXIT_IRQ_WINDOW_OPEN`：準備注入中斷
- `KVM_EXIT_SHUTDOWN`：guest 三指神功

**這個「VMEXIT 後由 vCPU thread 接手」的點，就是 Ch 7 留下來的那個問號的答案。** QEMU userspace 接手的入口是 `kvm_cpu_exec()` 裡的 switch-case，而不是什麼神秘的地方。

### iothread（I/O 執行緒）

```
src: iothread.c
```

iothread 是可選的：預設裝置的 I/O callback 跑在 main thread，但 virtio 裝置可以指定一個專屬的 iothread，讓 I/O 完成處理不阻塞主迴圈。每個 iothread 內部也是一個 GMainLoop。

```bash
# 建立 iothread 並掛給 virtio-blk 的 CLI 範例
-object iothread,id=iothread0
-device virtio-blk-pci,...,iothread=iothread0
```

---

## TCG vs KVM：兩條 CPU 執行路徑

QEMU 的 CPU 執行有兩個後端（accelerator），在 `accel/` 下。

### TCG（Tiny Code Generator）

```
src: accel/tcg/
```

TCG 是 QEMU 的純軟體 JIT。流程：

```
guest 機器碼
     │
     ▼
  翻譯前端（target/i386/translate.c 等）
     │  讀 guest 指令，產生 TCG IR（中間表示）
     ▼
  TCG IR
     │
     ▼
  翻譯後端（tcg/[host-arch]/tcg-target.c）
     │  把 TCG IR 編譯成 host 機器碼
     ▼
  Translation Block（TB）快取
     │
     ▼
  直接跳入執行 host 機器碼
```

TCG 的優點是跨架構（可以在 x86 host 上跑 ARM guest），缺點是慢（大約 5～20x overhead）。沒有硬體虛擬化支援的環境才用這條路。

TCG 的 TB cache 放在 QEMU 行程的 code cache buffer（一段 mmap 的 executable 記憶體），這本身就是攻擊面之一——如果能污染 TB cache 中的 host 程式碼，就有機會控制 host 執行流。

### KVM 加速路徑

```
src: accel/kvm/kvm-all.c
     target/i386/kvm/kvm.c
```

KVM 路徑下，guest code 直接跑在 CPU 上，QEMU 只在 VMEXIT 時介入。這是絕大多數生產環境的選擇。

```
guest code
     │
     │  CPU 直接執行（硬體 VMX/SVM）
     ▼
VMEXIT（硬體觸發）
     │
     ▼
KVM kernel module（處理簡單 exit、如 EPT fault）
     │  複雜 exit（MMIO、hypercall 等）
     ▼
kvm_cpu_exec() 回傳給 QEMU
     │
     ▼
QEMU 裝置模擬程式碼
     │  呼叫 MemoryRegion 的 read/write callback
     ▼
回到 KVM_RUN ioctl，繼續跑 guest
```

MMIO 的路徑（也是大多數 VM escape 的起點）：
1. Guest 對某個 GPA（Guest Physical Address）做 load/store
2. 該 GPA 沒有 EPT 對應（MMIO 區域故意不建 EPT mapping）
3. 觸發 EPT violation VMEXIT
4. KVM 辨識這是 MMIO，填寫 `kvm_run->mmio` 結構，回傳 `KVM_EXIT_MMIO`
5. QEMU 的 `kvm_cpu_exec()` 呼叫 `address_space_rw()`
6. `address_space_rw()` 查找 MemoryRegion，找到對應裝置的 read/write callback
7. 呼叫裝置模擬函式（e.g., `e1000_mmio_write()`）——**這裡就是 VM escape 漏洞的所在地**

---

## QOM：QEMU Object Model

```
src: include/qom/object.h
     qom/object.c
```

QEMU 裡有幾百種裝置，每種都要能被動態建立、繼承共同行為、透過命令列設定參數、在 live migration 時被序列化。如果用 C 直接手刻，維護性會是災難。QOM 就是為了解決這個問題而生的。

QOM 在 C 語言裡實現了一套物件導向系統，核心概念：**TypeInfo**。

### TypeInfo：類型的靜態描述

```c
/* include/qom/object.h */
struct TypeInfo {
    const char   *name;           /* 類型名稱，全域唯一字串 */
    const char   *parent;         /* 父類型名稱 */
    size_t        instance_size;  /* 物件實例的大小（bytes） */
    size_t        instance_align; /* 對齊需求 */
    void        (*class_init)(ObjectClass *klass, void *data);
    void        (*instance_init)(Object *obj);
    void        (*instance_finalize)(Object *obj);
    bool          abstract;       /* 抽象類別，不能直接 new */
    size_t        class_size;     /* class 結構的大小 */
    /* ... */
};
```

`class_init` 在類型第一次被使用時呼叫一次，填寫 class 層級的函式指標（方法）。`instance_init` 在每次 `object_new()` 建立實例時呼叫，做實例層級的初始化。

### type_init macro：在 main() 之前就註冊

```c
/* 典型的裝置定義（以虛構的 mydev 為例） */
static const TypeInfo mydev_info = {
    .name          = TYPE_MY_DEVICE,          /* "my-device" */
    .parent        = TYPE_PCI_DEVICE,         /* 繼承自 PCIDevice */
    .instance_size = sizeof(MyDevState),
    .class_init    = mydev_class_init,
    .instance_init = mydev_instance_init,
};

static void mydev_register_types(void)
{
    type_register_static(&mydev_info);
}

type_init(mydev_register_types)
/* 展開後等於：
   __attribute__((constructor)) void do_qemu_init_mydev_register_types(void)
   { register_module_init(mydev_register_types, MODULE_INIT_QOM); }
*/
```

`type_init` 展開成 GCC `__attribute__((constructor))`，在 `main()` 執行前就把類型資訊塞進全域 hash table。這是 QEMU 所有裝置能被命令列 `-device` 動態指定的基礎。

### 繼承鏈

QOM 的繼承是線性的，透過 `parent` 字串解析：

```
Object（object.h）
  └─ Device（hw/core/qdev-core.h）
       └─ PCIDevice（include/hw/pci/pci_device.h）
            └─ E1000State（hw/net/e1000.c）
            └─ VirtIOPCIProxy（hw/virtio/virtio-pci.h）
                 └─ VirtIONet + VirtIONetPCI
```

在 class 結構上，這實現為「大結構體的第一個成員是父 class 結構體」——C 語言中合法的類型雙關（type punning via pointer cast）：

```c
typedef struct PCIDeviceClass {
    DeviceClass parent_class;   /* 必須在第一位 */
    void (*realize)(PCIDevice *dev, Error **errp);
    PCIConfigReadFunc  *config_read;
    PCIConfigWriteFunc *config_write;
    uint16_t vendor_id;
    uint16_t device_id;
    /* ... */
} PCIDeviceClass;
```

`class_init` 呼叫順序是從根到葉：先 `Object` 的 `class_init`，再 `Device`，再 `PCIDevice`，最後 `E1000`。每一層都能覆寫父層的函式指標（虛函式表的手動實作）。

### object_new 和 object_class_by_name

```c
/* 動態建立物件（內部流程） */
Object *obj = object_new("e1000");
/*
  1. 在 type hash table 裡找 "e1000"
  2. 計算 instance_size（加總整個繼承鏈）
  3. g_malloc0(instance_size)
  4. 從根到葉依序呼叫 instance_init
  5. 回傳 Object*，可以 cast 成 E1000State*
*/

/* 透過名稱取得 class（不建立實例） */
ObjectClass *klass = object_class_by_name("e1000");
PCIDeviceClass *pdc = PCI_DEVICE_CLASS(klass);
/* PCI_DEVICE_CLASS 是個 macro，做 OBJECT_CLASS_CHECK 加上 cast */
```

`g_malloc0` 就是 GLib 的 heap 配置——最終會呼叫 `malloc`。**E1000State 放在 QEMU 行程的 heap 上。** 這對理解「為什麼 e1000 的 buffer overflow 是 heap overflow」至關重要。

### property 系統

QOM 有一套 property（屬性）機制，讓裝置的參數可以在執行期查詢或設定：

```c
/* 以 e1000 mac address 為例（示意，非原始碼） */
object_property_add(OBJECT(dev), "mac",
                    "str",
                    e1000_get_mac,
                    e1000_set_mac,
                    NULL, NULL);
```

這讓 `qemu-monitor`、`QMP`（QEMU Machine Protocol）可以動態讀寫裝置狀態，也讓 live migration 可以序列化整個機器狀態。

---

## 底層機制：QEMU 行程完整結構圖

```
QEMU 行程記憶體佈局（簡化，未實測，理論預期）
─────────────────────────────────────────────
高地址
  ┌──────────────────┐
  │   stack          │  main thread stack
  │   ...            │  vCPU thread stacks
  ├──────────────────┤
  │   mmap 區        │
  │   ┌────────────┐ │  guest RAM（一段大 anonymous mmap）
  │   │ 0x00000000 │ │  e.g., -m 4G → 4GiB 的連續 virtual addr
  │   │ ...        │ │  GPA 0 對應到這個 mmap 的起點
  │   │ 0xFFFFFFFF │ │
  │   └────────────┘ │
  │   ┌────────────┐ │  TCG code cache（rwx mmap）
  │   │  JIT TB    │ │  僅 TCG 路徑
  │   └────────────┘ │
  ├──────────────────┤
  │   heap           │  glibc ptmalloc
  │   ┌────────────┐ │  E1000State、VirtIONet、...
  │   │ QOM 物件   │ │  所有裝置狀態結構都在這裡
  │   │ MemoryRegion│ │
  │   │ ...        │ │
  │   └────────────┘ │
  ├──────────────────┤
  │   .bss/.data     │  全域變數（address_space_memory 等）
  ├──────────────────┤
  │   .text          │  QEMU 程式碼段
低地址
  └──────────────────┘

驗證：（未實測，理論預期）
$ sudo cat /proc/$(pgrep qemu-system)/maps | grep -E "rwx|heap|anon"
```

### 執行緒互動流程圖

```
  main thread                 vCPU thread              iothread
  ─────────────               ─────────────            ─────────
  qemu_main_loop()
       │
       ▼
  ppoll() 等待事件
       │
       │ (fd ready / timer)
       ▼
  event callback
  (e.g., tap fd 可讀)
       │
       ▼
  處理封包 → 注入 IRQ
  (透過 BH 或 直接)
       │
       └──[IRQ 注入]──►
                        kvm_vcpu_thread_fn()
                             │
                             ▼
                        KVM_RUN ioctl
                        （guest 跑中）
                             │
                             │ VMEXIT (MMIO)
                             ▼
                        kvm_cpu_exec() 接手
                             │
                             ▼
                        address_space_rw()
                             │
                             ▼
                        裝置 MMIO callback
                        (e.g., e1000_mmio_write)
                             │
                        [寫入裝置狀態、可能
                         排程 BH 回 main thread]
                             │
                             ▼
                        繼續 KVM_RUN
                                               virtio I/O 完成
                                               通知到 eventfd
                                               iothread GMainLoop
                                               觸發 callback
```

---

## Memory API 概覽

（細節在 Ch 10，這裡只建立大圖）

QEMU 用兩個抽象層描述 guest 的記憶體空間：

**MemoryRegion（記憶體區域）**：描述一段地址範圍的用途——可以是 RAM、ROM、MMIO、alias（別名）。每個 MemoryRegion 可以有子 region，形成樹狀結構。MMIO 的 MemoryRegion 帶有 `read` 和 `write` 函式指標，被 `address_space_rw()` 呼叫。

**AddressSpace（地址空間）**：把 MemoryRegion 樹「展平」成一個線性的地址查找結構（FlatView + FlatRange）。QEMU 有兩個主要的 AddressSpace：`address_space_memory`（對應 GPA 空間）和 `address_space_io`（PIO 空間）。

```c
/* 全域變數，在 exec.c / physmem.c */
AddressSpace address_space_memory;
AddressSpace address_space_io;
```

關鍵：MMIO MemoryRegion 的 `write` 函式指標就是「guest 對 MMIO 空間寫入時被呼叫的 C 函式」。大量 VM escape CVE 的根源就在這些函式裡的記憶體安全漏洞。

---

## 對比與取捨

| 面向 | TCG 路徑 | KVM 路徑 |
|------|----------|----------|
| Guest CPU 執行 | QEMU JIT，跑在 userspace | 直接在 CPU 上執行 |
| 效能 | 5～20x slowdown | 接近原生（< 5% overhead） |
| 跨架構 | 支援（x86 host 跑 ARM guest） | 不支援（host/guest 同架構） |
| MMIO 觸發路徑 | QEMU 自行偵測（softmmu） | EPT violation → KVM → QEMU |
| 攻擊面 | TCG JIT buffer + MMIO callback | 主要是 MMIO/PIO callback |
| 生產環境使用 | 罕見（CI/特殊環境） | 標準 |
| 對應 QEMU 程式碼 | `accel/tcg/` | `accel/kvm/` |

| QOM 機制 | 作用 | 攻擊者視角 |
|----------|------|------------|
| TypeInfo + type_init | 裝置類型靜態宣告 | 了解哪些裝置存在 |
| instance_init | 物件 heap 配置 + 初始化 | 物件在 heap 上 |
| class_init + 虛函式指標 | 多型 dispatch | 函式指標覆寫目標 |
| property 系統 | 動態參數讀寫 | QMP 介面的輸入點 |

---

## 踩雷集錦

**錯誤直覺 1：「QEMU 裡有 KVM，所以 VM escape 要攻擊 kernel。」**

正確認識：KVM 在 kernel 裡，但 QEMU 在 userspace。VM escape 通常攻擊的是 QEMU 的裝置模擬程式碼（也是 userspace），不需要 kernel exploit。成功後你拿到的是 QEMU 行程的 root（或 qemu user），不是 host root——但這已經等於逃出 VM 了，後續 privesc 是另一個問題。

**錯誤直覺 2：「BQL 保護一切，不用擔心 race condition。」**

正確認識：BQL 保護裝置模擬 callback 不被並行執行，但 iothread 的引入打破了這個假設：指定了 iothread 的裝置其 callback 跑在 iothread 上，不持有 BQL。如果裝置程式碼假設自己永遠在 BQL 下執行，就可能有 race condition。這類 bug 出現在 virtio 相關的 CVE 裡。

**錯誤直覺 3：「guest RAM 是 QEMU 行程的 heap。」**

正確認識：guest RAM 是獨立的 anonymous mmap，不在 `[heap]` 區段裡。`/proc/[pid]/maps` 可以清楚看到兩者是不同的 mapping。利用 guest RAM 來 pivot 到 heap 需要額外的 primitive。

**錯誤直覺 4：「QOM 繼承和 C++ 繼承一樣。」**

正確認識：QOM 是純 C 手工實作的，繼承鏈在執行期用字串查找解析，class 結構體必須手動保證「父 class 在第一位」的佈局。這代表編譯器不幫你做任何繼承的靜態保障，錯誤的 cast 直接 UB。在讀 QEMU 原始碼時，`PCI_DEVICE(obj)` 這樣的 macro 做的是 `object_dynamic_cast_assert()`，有 assert 但在 NDEBUG 下會消失。

**錯誤直覺 5：「MMIO callback 是在 KVM kernel module 裡呼叫的。」**

正確認識：KVM kernel module 辨識到 EPT violation 是 MMIO 後，把資訊填到 `kvm_run` 結構（一個 userspace 可讀的共享頁），然後讓 KVM_RUN ioctl 回傳 `KVM_EXIT_MMIO`。MMIO callback 是在 QEMU userspace 的 `kvm_cpu_exec()` 裡被呼叫的，和 kernel 無關。Ch 7 的 VMEXIT 處理機制在 kernel 層，但 callback 分發在 userspace 層——這條線要畫清楚。

---

## 進階：再往深一層

### BQL 的未來：Big QEMU Lock 拆分

QEMU 社群長期在嘗試把 BQL 拆細（per-device lock），讓 I/O 能真正並行。這個工作在 QEMU 9.x 仍在進行中，部分 virtio 路徑已經可以不持 BQL。對安全研究者的意義：BQL 消失的地方就是新的 race condition 潛在區域。

### TCG plugin 系統

QEMU 9.x 有 TCG plugin API（`include/qemu/plugin.h`），允許在不修改 QEMU 原始碼的情況下插入 instrumentation——類似 PIN 或 DynamoRIO，但只在 TCG 路徑下工作。可以用來做 fuzzing 覆蓋率收集。

### QEMU monitor / QMP

QEMU 提供兩種管理介面：`human monitor`（文字命令列，`-monitor stdio`）和 `QMP`（JSON-based，`-qmp tcp:...`）。QMP 是 libvirt、OpenStack 等上層管理工具的溝通管道，也是 SSRF/injection 類漏洞的攻擊目標。如果你能從 guest 控制 QMP，等於已經逃出去了。

### vhost：把裝置模擬搬回 kernel

vhost（`drivers/vhost/` in Linux）是把 virtio 資料平面搬回 kernel 的最佳化，繞過 QEMU userspace 直接在 kernel 處理 virtqueue——效能更好，但 attack surface 也移到了 kernel vhost driver。vhost-user 又把它搬到另一個 userspace 行程（如 DPDK）。三條路徑對應三種不同的攻擊目標。

---

## 動手練習

以下練習不需要寫漏洞，只需要觀察 QEMU 行程結構。

**練習 1：觀察 QEMU 的執行緒和記憶體佈局**

啟動一個最小化的 QEMU VM（可以用 TinyCore Linux 或隨便一個 disk image）：

```bash
qemu-system-x86_64 \
    -enable-kvm \
    -m 512M \
    -nographic \
    -kernel vmlinuz \
    -append "console=ttyS0"
```

在另一個 terminal：
```bash
# 找到 QEMU 的 PID
QPID=$(pgrep -f qemu-system)

# 觀察執行緒
ls -la /proc/$QPID/task/

# 觀察記憶體佈局：找 guest RAM（大的 anonymous mmap）
cat /proc/$QPID/maps | grep -v "\.so\|\.txt\|stack\|vvar\|vdso\|vsyscall" | head -50

# 計算 guest RAM 的大小（理論預期 512MiB + 一些額外空間）
cat /proc/$QPID/maps | awk '/anon/ {
    split($1, a, "-");
    size = strtonum("0x" a[2]) - strtonum("0x" a[1]);
    if (size > 100*1024*1024) print size/1024/1024 " MiB", $0
}'
```

（未實測，理論預期：應該能看到一個約 512MiB 的 anonymous mmap 作為 guest RAM，以及多個 thread stack）

**練習 2：用 gdb 觀察 QOM 類型樹**

```bash
# attach gdb 到 QEMU 行程
sudo gdb -p $(pgrep qemu-system-x86_64)

# 在 gdb 裡：查看 type_table（全域 hash table）
(gdb) info variables type_table
# 或
(gdb) p type_table

# 如果有符號，查找 e1000 的 TypeInfo
(gdb) p *(TypeImpl*)g_hash_table_lookup(type_table, "e1000-82540em")
```

（未實測，理論預期：能看到 TypeImpl 結構，包含 instance_size 和函式指標）

**練習 3：追蹤一次 MMIO 呼叫**

```bash
# 在 gdb 裡，對 e1000 的 MMIO write 設斷點（需要有 debug symbol）
(gdb) b e1000_mmio_write
# 或用地址（需要先找到函式地址）
(gdb) info address e1000_mmio_write

# 在 guest 裡執行任何網路操作（ping/ifconfig），觸發斷點
# 觀察 call stack
(gdb) bt
```

（未實測，理論預期：call stack 應該包含 `kvm_cpu_exec` → `address_space_rw` → MMIO callback chain）

---

## 本章重點整理

1. **QEMU 是普通的 Linux userspace 行程**。它的 heap 是 glibc heap，裝置狀態結構（如 E1000State）配置在 heap 上。VM escape 通常是對 QEMU 行程做 heap exploit。

2. **三種執行緒**：main thread 跑 GMainLoop 處理事件、vCPU thread 跑 KVM_RUN ioctl 並在 VMEXIT 後接手 MMIO/PIO 處理、iothread 讓高效能 virtio 裝置有專屬的事件迴圈。

3. **BQL（Big QEMU Lock）** 是 QEMU 的全域鎖，保護裝置 callback 的串行執行。iothread 的普及正在削弱這個假設。

4. **KVM 路徑下，MMIO callback 在 QEMU userspace 被呼叫**：EPT violation → KVM kernel 填寫 `kvm_run->mmio` → KVM_RUN 回傳 → `kvm_cpu_exec()` 呼叫 `address_space_rw()` → MemoryRegion 的 write callback → 裝置模擬函式。

5. **TCG**：純軟體 JIT，guest 指令翻譯成 host 指令。跨架構用，生產環境少見。TCG code cache 是 rwx mmap，本身是攻擊面。

6. **QOM**：C 語言實作的物件系統。TypeInfo 描述類型，`type_init` 在 `main()` 前註冊，`object_new` 在 heap 上配置物件。class 結構包含虛函式指標（= 函式指標，可被 overflow 覆寫）。

7. **Memory API 大圖**：`AddressSpace` + `MemoryRegion` 描述 guest 的記憶體拓樸，MMIO MemoryRegion 的 read/write 指向裝置模擬函式——這是下一章的核心。

---

## 自我檢核

- [ ] 我能解釋為什麼「VM escape = QEMU userspace heap pwn」，而不是 kernel exploit。
- [ ] 我知道 VMEXIT 發生後，控制流如何從 KVM kernel module 回到 QEMU 的哪個函式。
- [ ] 我能說出 QEMU 三種執行緒的職責差異，以及 BQL 的作用和侷限。
- [ ] 我知道 QOM 的 TypeInfo 包含哪些欄位，`class_init` 和 `instance_init` 各在什麼時機呼叫。
- [ ] 我知道 guest RAM 在 QEMU 行程記憶體中以什麼形式存在（不是 heap）。
- [ ] 我能畫出「guest 對 MMIO 寫入」→「KVM exit」→「QEMU callback 被呼叫」的完整路徑。
- [ ] 我能區分 TCG 路徑和 KVM 路徑，並說出各自的 MMIO 觸發機制差異。
- [ ] 我知道 AddressSpace 和 MemoryRegion 的層級關係（大概），以及它們在 MMIO dispatch 中的角色。

---

## 延伸閱讀

1. **QEMU 原始碼：`accel/kvm/kvm-all.c`**
   聚焦在 `kvm_cpu_exec()` 函式（約 300 行）。重點讀 `KVM_EXIT_MMIO` 和 `KVM_EXIT_IO` 的 case。這是 Ch 7 和 Ch 10 之間的橋——KVM kernel 和 QEMU 裝置模擬的接縫點。QEMU 9.0 的 git tag 是 `v9.0.0`。

2. **QEMU 原始碼：`include/qom/object.h`**
   QOM 的完整 API 定義都在這個單一標頭檔。從 `TypeInfo` 結構體開始讀，然後讀 `OBJECT_CLASS_CHECK`、`OBJECT_CHECK` 這兩個 cast macro 的實作。理解 QOM 的人讀漏洞 PoC 時會快很多。

3. **LWN：「A deep dive into QEMU: memory API」（2020）**
   URL: `https://lwn.net/Articles/MemoryAPI/`（系列文，共約 5 篇）
   這個系列是 QEMU 記憶體子系統最好的入門讀物，作者是 QEMU 開發者。Ch 10 前先讀前兩篇（AddressSpace 概覽和 MemoryRegion 基礎），建立正確的大圖再讀原始碼。

4. **「VIRTUNOID: Breaking out of KVM」（Black Hat USA 2011，Nelson Elhage）**
   這篇 15 年前的 talk 是 VM escape 研究的經典入門。作者利用 virtio-net 的 bug 做 heap overflow，從 guest 拿到 QEMU 行程的控制。技術細節雖然 outdated（現代 QEMU/glibc 保護不同），但思路——定位目標函式指標、heap layout manipulation、control flow hijack——完全適用今天的攻擊。PDF 在 Nelson 的個人網站。

5. **QEMU 開發者文件：`docs/devel/memory.rst`（隨 QEMU 原始碼）**
   QEMU 官方的 Memory API 設計文件，從 MemoryRegion 的動機講到 FlatView 的實作。比 LWN 更詳細，但不如 LWN 好讀。建議讀 LWN 之後回來用這份做參考手冊。

---

→ [Ch 10 — MemoryRegion 與 AddressSpace 深挖：MMIO dispatch 的真正入口](./10-memory-region-address-space.md)
