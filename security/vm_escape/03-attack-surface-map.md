# Ch 3 — 攻擊面全圖：hypervisor 到底能從哪裡打

> **目標**：把 hypervisor 的攻擊面攤成一張完整地圖——系統性列出所有 guest→host 通道（device 模擬、paravirt/hypercall、共享記憶體/DMA、monitor/backdoor、GPU、shared folder/clipboard、VMCS/CPU 虛擬化本身），說清各自的 bug 密度與為什麼，並給每一類配一個真實 CVE。這章是後面所有 Part 的索引。

前兩章你知道了 VMEXIT 是攻擊入口、device 模擬是主戰場。這章要把「主戰場」這個模糊的說法變成一張精確的地圖：hypervisor 暴露給 guest 的每一條通道長什麼樣、哪條 bug 最多、後面哪個 Part 打它。讀完這章，你翻開任何一篇 escape writeup，都能立刻定位「它打的是這張圖上的哪個點」。

## 為什麼需要這個？

hypervisor 的攻擊面反直覺地大。你可能以為「一台被虛擬化的機器，guest 能碰到的東西應該很少、被管得很死」。恰恰相反——**為了讓 guest 以為自己是一台真電腦，hypervisor 必須模擬一整台電腦的所有硬體與介面**：網卡、磁碟控制器、顯卡、USB、音效卡、PCI 匯流排、中斷控制器、時鐘……每一個都是一段接收 guest 輸入的 C 程式碼，每一段都是攻擊面。

更糟的是這些程式碼的歷史包袱。QEMU 為了相容各種老作業系統，模擬了**幾百種 device**，其中很多是 1990 年代的硬體。用 `ls hw/` 直接數一下 QEMU 原始碼（未實測，理論預期）：

```
$ find qemu/hw -name "*.c" | wc -l
# 預期輸出量級：350–450 個 .c 檔（每個大致對應一個 device 的模擬）

$ ls qemu/hw/
arm/   audio/  block/  char/  core/   display/ dma/   gpio/
i2c/   i386/   ide/    input/ intc/   mem/     misc/  net/
nvme/  pci/    pci-bridge/ ppc/  ps2/  rdma/  rtc/  s390x/
scsi/  sd/     ssi/    timer/ tpm/    usb/    vfio/  virtio/
watchdog/ ...
```

QEMU 的 `hw/` 子目錄超過 20 個大類，每個大類下有多個 device。`hw/net/` 下光網卡就有 `e1000.c`、`e1000e.c`、`rtl8139.c`、`ne2000.c`、`vmxnet3.c`、`pcnet.c`……這些 device 的模擬程式碼：**寫得早、假設 guest 是善意的硬體驅動、用 C 寫沒有記憶體安全、且很少人審計。** 這就是 bug 的溫床。

沒有這張地圖，你會像無頭蒼蠅一樣亂讀 CVE。有了它，你知道「device 模擬那一格 bug 最密、先攻那裡」「virtio 那一格是現代焦點、Part 4 專攻」「VMCS 那一格 bug 稀有、理解就好別硬碰」。**這是一張告訴你把力氣花在哪的作戰地圖。**

## 先建立直覺：完整攻擊面全圖

```
  ┌──────────────────────────────── Guest（你，已 root）─────────────────────────┐
  │                                                                              │
  │  能對 host 送出的輸入通道（每一條都是攻擊面）：                                  │
  │                                                                              │
  │   [A] PIO  （in/out 指令，I/O port 空間）                                     │
  │   [B] MMIO （mov 讀寫 MMIO 位址，觸發 VMEXIT）                                │
  │   [C] DMA  （讓假 device 去讀寫 guest RAM，guest 控資料與位址）                 │
  │   [D] virtio / paravirt hypercall（共享 ring buffer + descriptor）             │
  │   [E] 共享記憶體（guest RAM = host QEMU 行程的一塊 mmap buffer）               │
  │   [F] GPU 命令流（virtio-gpu / VMware SVGA / VirtualBox VMSVGA）              │
  │   [G] backdoor / RPCI（廠商私有通道，VMware tools 等）                        │
  │   [H] shared folder / clipboard / drag-and-drop（便利功能）                   │
  │   [I] CPU 虛擬化本身（觸發 VMCS/EPT/KVM 的處理路徑）                          │
  └──────────────────────────────────────┬───────────────────────────────────────┘
                                          │ VMEXIT / ioctl(KVM_RUN) / 廠商通道
                                          ▼
  ┌──────────────────────────── Host：QEMU 行程（靶）───────────────────────────────┐
  │                                                                                │
  │  ┌─────────── device emulation：hw/ 下幾百個 C 檔 ──────────────────┐          │
  │  │   net/      ← rtl8139/e1000/ne2000/vmxnet3/pcnet…               │          │
  │  │   usb/      ← xhci/uhci/ohci/ehci + 各種 USB device…             │          │
  │  │   block/    ← ide/ahci/floppy(fdc)/nvme/scsi/cd-rom…             │          │
  │  │   display/  ← vga/cirrus/bochs/vmware-vga…                      │          │
  │  │   audio/    ← ac97/hda/es1370/sb16…                             │          │
  │  │   virtio/   ← virtio-net/blk/scsi/balloon/gpu/rng/vsock…        │          │
  │  │   misc/     ← edu/pvpanic/vmport/ivshmem…                       │          │
  │  │   …（更多）                                                       │          │
  │  │                                           ★★★ bug 密度最高 ★★★   │          │
  │  └──────────────────────────────────────────────────────────────────┘          │
  │                                                                                │
  │  KVM 介面（host kernel 模組）—— bug 更嚴重但極稀有 ★                            │
  └────────────────────────────────────────────────────────────────────────────────┘
```

一句話抓重心：**攻擊面 = guest 能合法送進 host 的所有輸入 × host 處理這些輸入的所有程式碼**。輸入越多樣、處理程式碼越老越雜，bug 越多。device 模擬正好把這兩個「越」都佔滿了。

## 攻擊面逐格拆解（每格配真實 CVE）

### [A][B][C] device emulation：PIO / MMIO / DMA —— 主戰場 ★★★

guest 跟虛擬 device 溝通的三種基本方式，全在這格，也是本課 Part 2/3 的核心：

- **PIO（Port I/O）**：guest 用 `in`/`out` 指令讀寫 device 的 I/O port（16-bit 位址空間）。老 device 常用（FDC 用 0x3F0–0x3F7、serial port 用 0x3F8…）。每次 `in`/`out` 觸發 VMEXIT（若 VMCS 的 I/O bitmap 設定了要攔），KVM 判定後交回 QEMU 的 port I/O handler。
- **MMIO（Memory-Mapped I/O）**：device 把暫存器映射到一段實體位址（PCI BAR），guest 用普通 `mov` 讀寫那段位址就等於操作 device。現代 PCI device 主流。每次存取觸發 VMEXIT（EPT violation 或 MMIO bitmap），交給該 device 的 `mmio_read`/`mmio_write` callback。
- **DMA（Direct Memory Access）**：guest 叫 device「去某個 guest 實體位址（GPA）讀/寫一塊資料」。device 在 host 端用 `dma_memory_read`/`pci_dma_write` 存取 guest RAM（其實是 host 上 QEMU mmap 出的一塊 buffer，按 GPA 偏移定址）。**DMA 特別危險**——它讓 device 拿著 guest 給的 GPA 與長度去讀寫記憶體，長度沒驗好就是 OOB，且常是 double-fetch/reentrancy 的溫床。

**為什麼這格 bug 最多**（量化說明）：

從 QEMU 的 CVE 歷史來看（截至 2024 年 NVD 記錄），device emulation 相關 CVE 占 QEMU 所有 CVE 的比例約在 **60–70%** 以上（未精確統計，量級合理）。三個結構性原因：

1. **device 數量爆炸**：QEMU `hw/` 目錄下數百個 `.c` 檔，每個都是獨立的攻擊面。光 `hw/net/` 就有超過 10 個網卡實作，每個都收 guest 送來的封包/暫存器值。
2. **老程式碼 + 善意假設**：多數 device 模擬寫於 2000–2010 年代「guest 是善意驅動」的年代，對 guest 送來的暫存器值/長度/索引缺乏敵意驗證（沒有 `ASSERT(len <= BUF_SIZE)` 這種邊界）。
3. **C 語言 + 手動記憶體**：buffer 大小、索引邊界全靠人工檢查，漏一個就是 OOB/UAF。device 狀態結構裡往往有固定大小的 buffer 緊鄰著 function pointer，OOB write 直接影響控制流。

**真實 CVE 範例**：

| CVE | 年份 | Device | 漏洞類型 | 一句說明 |
|-----|------|--------|---------|---------|
| **CVE-2015-3456** (VENOM) | 2015 | FDC（軟碟控制器） | Stack OOB write | FDC command buffer 沒有長度上限，溢位到 host stack；FDC 對每台 x86 guest 預設啟動，guest 無法關掉 |
| **CVE-2020-14364** | 2020 | USB（`hw/usb/core.c`） | OOB read/write | `setup_len` 超過 `data_buf[4096]` 上限未驗，guest 可越界讀寫 4096 位元組 heap buffer；有公開完整 PoC |
| **CVE-2019-6778** | 2019 | SLIRP 網路（`slirp/tcp_emu.c`） | Heap OOB write | TCP 仿真中 `tcp_emu()` 的 buffer 長度計算錯誤，guest 送特製 TCP 封包觸發 |
| **CVE-2021-3416** | 2021 | 多個網卡（e1000/rtl8139 等） | Stack OOB read | loopback 模式下封包大小計算錯誤，影響 16 個 network device |
| **CVE-2022-0358** | 2022 | virtio-fs（`hw/virtio/`） | 權限問題 | shared directory 的 setuid bit 處理不當，guest 可能影響 host |

### [D] paravirt / virtio / hypercall

半虛擬化介面：guest 主動把「請求」送進 host，而非模擬真實硬體暫存器。現代雲的 device I/O 幾乎都走 virtio（快、乾淨）。

virtio 的核心機制是 **virtqueue**：

```
   Guest                                Host (QEMU)
   ─────                                ───────────
   ┌──────────────────────────────────────────────────────┐
   │   共享記憶體區（guest 可讀寫，host 也能讀寫）            │
   │                                                      │
   │  Descriptor Table:                                   │
   │  ┌──────────────────────────────────────────────┐   │
   │  │ #0: addr=GPA_0, len=512, flags=WRITE, next=1 │   │
   │  │ #1: addr=GPA_1, len=4096, flags=0, next=-    │   │
   │  └──────────────────────────────────────────────┘   │
   │                                                      │
   │  Available Ring:  ← guest 把「我準備好的 desc 頭」放這  │
   │  Used Ring:       ← host 把「我處理完的 desc 頭」放這  │
   └──────────────────────────────────────────────────────┘

   Guest 填好 descriptor（addr/len/flags），更新 available ring
   → 透過 PIO 寫 notification port 告知 host
   → Host QEMU 讀 descriptor，用 addr 去 guest RAM 讀資料
   → 處理完，寫 used ring 告知 guest
```

**攻擊面在哪**：host 解析 descriptor 時，**`addr` 與 `len` 完全由 guest 控制**。host 若不驗 `len` 是否超過實際 buffer、不驗 descriptor chain 是否成環（circular）、不驗 `addr + len` 是否超出 guest RAM 範圍，都是 bug。

**真實 CVE**：

| CVE | 年份 | Component | 漏洞 |
|-----|------|-----------|------|
| **CVE-2019-14835** | 2019 | virtio（Linux kernel 的 vhost） | 遷移恢復時 vring 大小驗證問題，OOB write |
| **CVE-2022-26353** | 2022 | virtio-net（QEMU） | rx/tx descriptor 處理的 heap OOB |
| **CVE-2016-5403** | 2016 | virtio-balloon | 無限 DoS，balloon 膨脹讓 host OOM |

這格的一般性教訓：**paravirt 把「攔截硬體」換成「信任 guest 主動送的結構化資料」，攻擊面從硬體暫存器變成 descriptor 結構的驗證。**

### [E] 共享記憶體的本質：guest RAM 就是 host 的一塊 buffer

這格是理解 escape 的關鍵認知，不是獨立 device，而是貫穿所有 device 的底層事實：

**guest 眼中的「實體記憶體」，在 host 上就是 QEMU 行程用 `mmap` 出來的一大塊 buffer。** 其路徑：

```
   Guest 視角：GPA 0x1000000 是我的 RAM
        ↓  (EPT 走訪)
   Host 視角：GPA 0x1000000 → HVA (host virtual address in QEMU's address space)
                            → HPA (host physical address，由 host OS 的分頁表決定)

   QEMU 內部：guest_ram_ptr = mmap(NULL, guest_ram_size, PROT_READ|PROT_WRITE, ...)
             HVA = guest_ram_ptr + GPA  （簡化，實際有 slot 管理）
```

所以：
- guest 能完全控制這塊 buffer 的內容（它就是 guest 的 RAM）。
- 當 device 做 DMA「去 GPA X 讀資料」，host 端就是 `memcpy(dst, guest_ram_ptr + X, len)`——**guest 完全掌控 DMA 讀到什麼**。
- 這讓 guest 有一個天然的「可控資料源」：想 spray 什麼、想讓 device 讀到什麼位址/長度，guest 都能在自己 RAM 裡佈好。

**攻擊意義**：很多 escape 的關鍵原語是「讓 device 拿 guest 可控的位址/長度去 host heap 上做讀寫」。共享記憶體讓 guest 有無限、精確的資料佈局能力。Ch 13、Ch 15 深挖 DMA 當 primitive。

### [F] GPU：SVGA / virtio-gpu —— 高價值、高複雜度

顯示卡模擬是攻擊面裡最肥的一塊之一：**GPU 命令集極複雜、狀態機龐大、要處理 2D/3D 命令與大量記憶體操作**，天然多 bug，且歷史上是逃逸重災區。

GPU 命令的本質是「guest 送一串命令，host 解析執行」——這種模型本身就危險：

```
   Guest：填一段 command buffer 到 GPU FIFO 記憶體
          （命令格式：[opcode][args...]，可包含源/目標位址、長度、矩形座標…）
        ↓
   Host QEMU：解析每條命令，執行對應動作
          ← 這個「解析執行 guest 命令的迴圈」就是漏洞點
              - 命令裡的座標超出 framebuffer 邊界 → OOB
              - 命令裡的 src/dst 位址指向 host heap 某處 → 任意讀寫
              - 狀態機進入非預期狀態 → UAF/邏輯錯誤
```

**真實 CVE**：

| 事件/CVE | 年份 | 目標 | 手法 |
|---------|------|------|------|
| **Cloudburst** (Kortchinsky) | 2009 | VMware SVGA | SVGA 命令邊界檢查缺失，host 上 OOB write |
| **CVE-2018-12633** | 2018 | VirtualBox VMSVGA | 3D 加速路徑的 OOB read/write |
| **CVE-2019-13164** | 2019 | QEMU bochs display | bochs VBE 的 OOB write |
| Pwn2Own 2021 | 2021 | VirtualBox 3D | 3D 加速命令解析 UAF，Bruno Pujos |

為什麼 GPU 肥：一個完整的 GPU device（如 VMware SVGA II 或 virtio-gpu）的命令集有幾十種 opcode，每個 opcode 的引數解析是獨立的 code path，等於你有幾十個「獨立的 host 端函式可以拿 guest 給的資料操作 heap」。任何一個忘記驗邊界就是洞。

### [G] backdoor / monitor / RPCI —— 廠商私有通道

hypervisor 常有給自家 guest tools 用的**私有溝通通道**，這些通道文件少、驗證常較鬆，是攻擊金礦：

- **VMware backdoor（RPCI）**：VMware 的 guest tools（VMware Tools）透過一個魔術 I/O port（`0x5658`，"VX"）與 host 的 VMware 程式通訊。命令格式是寫入特定 magic value + command number。host 端解析這些命令並回應，涉及字串解析、資料複製、功能豐富（時間同步、剪貼簿、RPCI 訊息…）。
- **VMware DnD/CP（Drag-and-Drop / Copy-Paste）**：透過 RPCI 通道的子功能，host 解析 guest 送來的 drag-and-drop 資料格式，是歷年 Pwn2Own VMware 逃逸的常見入口之一。
- **QEMU monitor / QMP**：管理介面（通常是 unix socket 或 stdio），正常不直接暴露給 guest，但 hot-plug/hot-unplug 觸發的狀態機在 guest 可控的情況下有間接影響。

**真實 CVE**：

| CVE | 年份 | 漏洞 |
|-----|------|------|
| **CVE-2012-1516** | 2012 | VMware backdoor RPCI 處理 OOB write |
| **CVE-2014-1208** | 2014 | VMware guest 可觸發 RPCI 緩衝區問題 |
| Pwn2Own 2017（VMware WS） | 2017 | JS RCE → VMware backdoor RPCI 鏈，三段逃逸 |
| Pwn2Own 2023（VMware WS） | 2023 | XHCI USB UAF 接 RPCI 作為 code exec 路徑 |

這格的教訓：**私有通道的設計目標是功能豐富（不是安全），文件稀少（研究者少），處理複雜資料格式（容易出 parser bug）**——三點疊加讓它成為高密度攻擊點。

### [H] shared folder / clipboard / 便利功能

「方便」功能是安全的天敵。共享資料夾（把 host 目錄掛進 guest）、共享剪貼簿、拖放檔案——這些都是 host 端會處理 guest 送來的路徑/資料的程式碼：

- **shared folder** 常見「路徑穿越（path traversal）逃出共享目錄」「符號連結逃逸」等問題：guest 送一個 `../../etc/shadow` 路徑，host 端沒有正規化就直接 open，就讀到了共享目錄外的檔案。
- **clipboard/drag-and-drop** 涉及 host 解析 guest 送的資料格式（可能是多種格式的 MIME 資料），parser bug 頻發。
- **VirtualBox shared folder（vboxsf）**：一個在 host kernel 跑的 kernel module，guest 透過 vboxsf 協定讀寫 host 目錄。這個 kernel module 的 bug 直接是 host kernel 權限，比 QEMU userland 更嚴重。

**真實 CVE**：

| CVE | 年份 | 目標 | 漏洞 |
|-----|------|------|------|
| **CVE-2019-2525** | 2019 | VirtualBox shared folder | 路徑穿越 + 符號連結，guest 讀 host 任意檔案 |
| **CVE-2021-2264** | 2021 | VirtualBox shared folder | heap OOB，guest → host 提權 |
| **QEMU 9pfs（多個 CVE）** | 2016+ | QEMU 9p 共享檔案系統 | 未禁止在共享目錄建立特殊裝置檔（mknod），guest 可能創建 host 上的 device 節點 |
| **CVE-2017-5525** | 2017 | QEMU 9pfs | symlink 繞過共享目錄限制 |

### [I] CPU 虛擬化本身：VMCS / VMEXIT 處理 —— bug 最少 ★

打 hypervisor 處理 VMEXIT、管理 VMCS、EPT 的那段核心程式碼（多在 KVM，即 host kernel）。

**為什麼 bug 最少**：這層被硬體與 KVM 嚴格把關，程式碼相對小、審計密集、且直接關係隔離正確性，一有問題影響太大所以修得快、審得嚴。**打中了通常是 host kernel 級別的災難（比 QEMU 逃逸更嚴重），但機會稀有、難度極高。**

| CVE | 年份 | 類型 | 說明 |
|-----|------|------|------|
| **CVE-2021-22543** | 2021 | KVM（Linux） | `KVM_SET_USER_MEMORY_REGION` 的 use-after-free，巢狀虛擬化路徑 |
| **CVE-2020-2732** | 2020 | KVM（VMX） | L2 guest 觸發 EPT violation 時 L1 hypervisor 的資訊洩漏 |
| **CVE-2018-3646**（L1TF） | 2018 | Intel CPU 微架構 | L1 data cache 的側通道，不是傳統軟體 bug，需完整 cache flush 緩解 |
| **CVE-2012-0217** | 2012 | Xen（SYSRET） | x86 SYSRET 的 privilege level 邊界條件，打 hypervisor CPU 邏輯 |

本課的態度：Part 1 把 VT-x/VMCS/EPT/KVM **理解透**（因為你得懂 VMEXIT 才懂 device 攻擊怎麼進來），但**不把它當主攻目標**。主攻是 [A]~[H] 的 userland device/介面。

## 底層機制：一次 MMIO 存取如何抵達 device callback

把 [B] MMIO 的完整路徑走一遍，這是後面 Part 2 的骨架：

```
  Guest:  mov dword ptr [BAR0 + offset], value
          （往某個 PCI device 的 MMIO BAR 寫）
          │
          │ 這段 GPA 在 EPT 中沒有合法映射（被標記為 MMIO）
          ▼
       VMEXIT（exit reason: EPT violation 或 MMIO）
          │
          ▼  KVM 接手，判定是 MMIO 存取
       KVM 無法自己處理 MMIO device → 讓 ioctl(KVM_RUN) 返回
          │
          ▼  回到 QEMU userland
       kvm_cpu_exec() 看 exit reason = KVM_EXIT_MMIO
          │
          ▼
       address_space_write(as, GPA, attrs, &value, size)
       ─ as 是 QEMU 的 AddressSpace（管理所有記憶體映射）
          │
          ▼
       flatview_write_continue()
          │
          ▼  查 FlatView（GPA → MemoryRegion 的映射表）
       memory_region_dispatch_write(mr, addr_within_mr, val, size, attrs)
       ─ mr 是這段位址對應的 MemoryRegion（對應到某個 device）
          │
          ▼  mr->ops->write 就是 device 在初始化時用
             memory_region_init_io() 註冊的 callback
       ops->write(mr->opaque, addr, val, size)
       ─ opaque 是 device 的狀態結構指標（例如 RTL8139State *）
       ─ addr 是寫入點（BAR 內偏移，guest 可控）
       ─ val 是寫入值（guest 可控）
       ─ size 是存取大小（1/2/4/8 位元組，guest 可控）
          │
          ▼  device C 程式碼用 addr/val/size 操作 opaque 裡的成員
  BUG ZONE：
          - addr 超出 device buffer 範圍 → OOB write
          - addr 是函式指標成員的偏移 → 直接控 RIP
          - val 是後面 DMA 的長度/位址，沒驗 → DMA OOB
```

**device 初始化時的 MMIO 註冊模式**（以 `edu` 教學 device 為例）：

```c
/* hw/misc/edu.c 簡化版 */
static const MemoryRegionOps edu_mmio_ops = {
    .read  = edu_mmio_read,    /* guest 讀 BAR → 呼叫這個 */
    .write = edu_mmio_write,   /* guest 寫 BAR → 呼叫這個 */
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void pci_edu_realize(PCIDevice *pdev, Error **errp) {
    EduState *edu = EDU(pdev);
    /* 建立一個 MMIO 區，大小 1MB，ops 指向上面那組 callback */
    memory_region_init_io(&edu->mmio, OBJECT(edu), &edu_mmio_ops, edu,
                          "edu-mmio", 1 * MiB);
    /* 把這個 MMIO 區掛到 PCI BAR 0 */
    pci_register_bar(pdev, 0, PCI_BASE_ADDRESS_SPACE_MEMORY, &edu->mmio);
}
```

每個 device 在初始化時，用 `memory_region_init_io` 註冊它的 MMIO 區與那組 `read`/`write` callback，用 `pci_register_bar` 把這個區掛到 PCI BAR 上。guest 之後對這個 BAR 的每次存取，都會被 dispatch 到那組 callback。**「guest 可控的 `addr`/`val`/`size` 三個參數 + device 那段沒驗好的 C 碼 = 漏洞」** 是 device emulation bug 的通式。Part 2 會把 `MemoryRegionOps`、`memory_region_init_io`、`pci_register_bar`、`dma_memory_read/write` 一個個拆開實作。

## 對比與取捨

| 攻擊面 | 通道 | bug 密度 | 難度 | 本課 Part | 真實 CVE 範例 |
|---|---|---|---|---|---|
| device emulation (PIO/MMIO) | 虛擬硬體暫存器 | ★★★ 最高 | 中 | Part 2/3（主線） | CVE-2015-3456 (FDC)、CVE-2021-3416 (網卡) |
| DMA | guest GPA → host buffer | ★★★ 高、強原語 | 中 | Part 2/3、Ch 13/15 | CVE-2019-6778 (slirp)、CVE-2020-14364 (USB) |
| virtio / paravirt | 共享 virtqueue | ★★ 高（現代焦點） | 中 | Part 4 | CVE-2022-26353 (virtio-net) |
| 共享記憶體 | guest RAM = host buffer | （貫穿全部，是原語基礎） | — | Ch 13/15 | 所有 DMA 型 escape |
| GPU (SVGA/virtio-gpu) | GPU 命令流 | ★★★ 高、肥 | 高 | Part 5/6 | Cloudburst、CVE-2018-12633 |
| backdoor / monitor / RPCI | 廠商私有通道 | ★★ 高、少人審 | 中 | Part 6（VMware） | CVE-2012-1516、Pwn2Own 2017 |
| shared folder / clipboard | 便利功能 | ★★ 中高 | 低–中 | Part 5/6 | CVE-2019-2525 (VBox)、CVE-2017-5525 (9pfs) |
| CPU 虛擬化 (VMCS/EPT/KVM) | VMEXIT 處理 | ★ 最低 | 極高 | Part 1（理解，非主攻） | CVE-2021-22543、CVE-2018-3646 |

觀察：**bug 密度大致與「程式碼老舊程度 × guest 可控輸入的複雜度 × 審計稀疏程度」成正比**。device 模擬三項全滿，所以是主戰場；CPU 虛擬化三項全低，所以 bug 稀有。這個直覺幫你判斷任何一個沒列到的攻擊面該不該花力氣。

## 踩雷集錦

- **錯誤直覺**：「guest 被虛擬化了，能碰到的東西很少，攻擊面很小。」→ **正確認識**：恰相反。為了假裝成真電腦，hypervisor 模擬了幾百種 device，每個都收 guest 輸入，攻擊面巨大。QEMU `hw/` 下的 `.c` 檔數量就是攻擊面的直觀量化。
- **錯誤直覺**：「我沒給 guest 配某個 device，那個攻擊面就不存在。」→ **正確認識**：很多 device（FDC、PS/2 控制器、8259A PIC、8254 PIT、i440FX 主機板組）對每個 x86 guest 預設就在、無法移除——VENOM 的恐怖正在此。你不能假設「沒配 = 沒攻擊面」。
- **錯誤直覺**：「DMA 只是搬資料，不危險。」→ **正確認識**：DMA 讓 device 拿 guest 可控的 GPA 與長度去 host 記憶體讀寫，是最強的原語來源之一（OOB、reentrancy、double-fetch 全在這）。且 guest RAM 就是 host buffer，guest 能精確佈局 DMA 讀到的內容。
- **錯誤直覺**：「virtio 是新技術，應該比老 device 安全。」→ **正確認識**：virtio 快又乾淨，但它把攻擊面換成「host 對 guest 填的 descriptor（GPA/長度/鏈結）的信任」，descriptor 處理照樣出 memory bug，且它是現代雲的主力，value 更高。virtio 的代碼雖然比老 device 新，但 descriptor 驗證邏輯一旦有 off-by-one 就是洞。
- **錯誤直覺**：「打逃逸應該去打 VT-x/VMCS 那種核心機制。」→ **正確認識**：那層被硬體與 KVM 防得最死、bug 最稀有、難度最高。CP 值最高的主戰場是 userland 的 device 模擬。理解 VMCS 是為了懂攻擊入口，不是為了直接打它。
- **錯誤直覺**：「逃逸鏈的每一步都打同一格攻擊面。」→ **正確認識**：真實逃逸常是跨格組合，例如「用 GPU MMIO 的 OOB read 做 infoleak 洩漏 host 位址（[F] 格）→ 用 network device 的 DMA OOB write 控函式指標（[A]/[C] 格）」。這張地圖的格子是可以拼起來的積木，不是互斥選項。
- **錯誤直覺**：「shared folder / clipboard 是輔助功能，攻擊面很小。」→ **正確認識**：這些功能直接讓 host 解析 guest 送來的路徑字串或資料格式，且在很多 type-2 hypervisor（VirtualBox、VMware Workstation）裡預設啟用。VirtualBox 的 shared folder 模組（vboxsf）跑在 host kernel，一個 OOB 直接是 host kernel 提權，比打 QEMU userland 的影響更大。

## 進階：再往深一層

### hot-plug / hot-unplug 路徑：裝置生命週期 = UAF 溫床

QEMU 允許執行期熱插拔 device（透過 QMP 命令或 guest 的 ACPI 熱插拔通知）。unplug 過程若有 guest 還在用的引用沒清乾淨，就是 **UAF**——這是 device 攻擊面裡一條獨立且高產的子線（Ch 19 專章）。

典型場景：
1. guest 觸發 device unplug（例如發 ACPI eject 通知）
2. host QEMU 開始 unplug 流程，釋放 device 狀態結構
3. guest 在 unplug 完成前，繼續對該 device 發 MMIO（因為 guest 還以為 device 在）
4. MMIO callback 用已釋放的 `opaque` 指標 → UAF

這條路徑連接了 [A] device 與 [G] monitor（unplug 常由 monitor/QMP 觸發）。近年 e1000e、virtio-balloon 的 hot-unplug UAF 都屬此類。

### reentrancy（重入）攻擊

DMA callback 執行到一半，若它觸發的記憶體存取又回頭映射到同一個 device 的 MMIO，就可能在 device 狀態不一致時重入自己，造成 UAF/corruption。

```
  MMIO write to device A
      → device A 的 write callback 執行中（狀態不一致）
          → 觸發 DMA，讀 guest RAM
              → guest 在那段 RAM 裡預先佈置了某個值
                  → 那個值被 device A 當作 MMIO 位址去讀
                      → 又回到 device A 的 read callback（重入！）
```

近年 QEMU 加了 `reentrancy_guard` 機制（`memory_region_set_nonvolatile` 等）來阻止這種情況。但舊 device 或 guard 沒覆蓋到的路徑仍可能可利用。這是 Ch 21「DMA reentrancy UAF」的主題。

### 攻擊面隨 hypervisor 縮減的趨勢

Firecracker/microVM 刻意砍掉絕大多數 legacy device，直接消滅 [A] 的大半攻擊面（沒有 FDC、沒有老網卡、沒有 SVGA）。Firecracker 只保留：virtio-net、virtio-blk、virtio-vsock、i8042（鍵盤控制器）、serial、RTC——就這幾個。

這改變了「打哪」——現代雲逃逸越來越集中在 virtio 與少數保留 device。攻擊者必須研究 virtio descriptor 處理，不能只靠 `rtl8139.c` 這種老 device 撿洞。

### 跨攻擊面的鏈：格子是積木不是防線

真實逃逸常是多格組合：

```
   Stage 1（information leak）：
   用 [F] GPU MMIO 的 OOB read，讀出 QEMU 的某個 heap chunk 裡的 libc 指標
   → 計算出 libc base（繞 ASLR）

   Stage 2（primitive）：
   用 [A] e1000 網卡 DMA 的長度驗證問題，造成 heap OOB write
   → 覆蓋同一 heap chunk 裡的 function pointer

   Stage 3（code exec）：
   觸發那個 function pointer → 劫持控制流 → ROP（用洩漏的 libc base）
```

這種「跨格組合」在 Pwn2Own 的複雜逃逸鏈裡是標準做法。讀 writeup 時，把每一步標回這張地圖的哪一格，能快速理解逃逸者的策略選擇。

## 動手練習

（概念/索引章，練習偏「建立地圖與歸類」。）

1. **默畫攻擊面全圖**：不看課文，畫出 guest 能送進 host 的所有通道，並在 host 側標出 device emulation 這格為何最大。跟本章的圖對照補全。
2. **CVE 歸位**：找 5 個真實 QEMU/VirtualBox/VMware 的 escape CVE，各自判斷屬於這張圖的哪一格（device/virtio/GPU/backdoor/shared folder/CPU 虛擬化）。統計哪一格最多——親手驗證「device 是主戰場」。
3. **列出「不可移除的預設 device」**：查 QEMU 對一個標準 x86 guest 預設會初始化哪些 device（FDC、8259A PIC、8254 PIT、PS/2 i8042、i440FX/PIIX3、RTC…）。這份清單就是「guest 無法拒絕的攻擊面」，VENOM 型攻擊的候選池。
4. **走一次 dispatch 路徑（接 Ch 0 環境）**：對某個 device（如 edu）的 `mmio_write` 下斷，從 guest 送一次 MMIO，用 `bt` 印出從 `address_space_write` → `memory_region_dispatch_write` → device callback 的完整呼叫鏈。把它和本章「底層機制」那張圖對起來。
5. **Firecracker vs QEMU device 清單對比**：找 Firecracker 的 device 清單（GitHub README 或 virtio 設計文件）與 QEMU `-M q35` 的預設 device 清單比較。算出兩者 device 數量差距，這個差距就是「縮面設計消滅了多少逃逸攻擊面」的量化指標。

## 本章重點整理

- hypervisor 攻擊面 = **guest 能合法送進 host 的所有輸入 × host 處理它們的所有程式碼**；為了假裝成真電腦，hypervisor 模擬幾百種 device，攻擊面巨大（QEMU `hw/` 有 350+ 個 `.c` 檔）。
- 主要通道 9 格：**[A][B][C] device 模擬（PIO/MMIO/DMA）** 是主戰場（三因：device 多 × 老程式碼善意假設 × C 手動記憶體）；[D] virtio/paravirt 是現代焦點；[F] GPU 肥且高價；[G] backdoor/RPCI 少人審；[H] shared folder/clipboard 便利即風險；[I] CPU 虛擬化（VMCS/EPT/KVM）bug 最稀有、只理解不主攻。
- [E] 共享記憶體是貫穿一切的底層事實：**guest RAM 就是 host 上一塊 guest 可控 buffer**，給了攻擊者精確的資料佈局與 DMA 讀寫原語。
- device dispatch 通式：guest 可控的 `addr`/`val`/`size` + 沒驗好的 device C 碼 = 漏洞；路徑是 `address_space_write` → `flatview_write_continue` → `memory_region_dispatch_write` → device 的 `*_mmio_write`。
- 每格都有真實 CVE 錨定；bug 密度 ≈ 程式碼老舊 × guest 輸入複雜度 × 審計稀疏——用這把尺判斷任何攻擊面。
- 真實逃逸常是跨格組合鏈，格子是積木不是防線。

## 自我檢核

- [ ] 我能默畫 hypervisor 攻擊面全圖，並說出哪一格 bug 最多、為什麼（三個原因）。
- [ ] 我能解釋為什麼「沒配某 device ≠ 沒有該攻擊面」，並舉一個預設不可移除的 device。
- [ ] 我能說清楚 DMA / 共享記憶體為何是強原語來源（guest RAM = host buffer，guest 可精確佈局 DMA 內容）。
- [ ] 我能為至少五格各配一個真實 CVE，並判斷它屬於哪個攻擊面。
- [ ] 我能講出 MMIO dispatch 的完整路徑（從 guest `mov` 到 device callback 的每一層函式）。
- [ ] 我能解釋 Firecracker 的「縮面設計」如何改變了「打哪」的攻擊策略。

## 延伸閱讀

- **QEMU 原始碼 `hw/` 目錄結構總覽**（`hw/net/`、`hw/block/`、`hw/usb/`、`hw/display/`、`hw/virtio/`、`hw/misc/`）——親眼看「幾百種 device」不是誇飾。挑 `hw/net/rtl8139.c`（最老、最多歷史 CVE 的網卡之一，6000 行 C）或 `hw/block/fdc.c`（VENOM 的現場，3000 行）略讀。感受老 device 模擬「假設 guest 是善意驅動」的寫法：很少邊界斷言、buffer 大小靠人工計算。

- **CVE-2020-14364 分析（QEMU USB 逃逸），以及 Alexander Bulekov / 0xKira 的 QEMU fuzz 研究系列**——一份完整、可讀、有 PoC 的 device emulation 逃逸案例。對照本章 [B][C] 格，看真實 escape 如何從 `setup_len` 沒驗好一路打到 host code exec（info leak → heap grooming → 控函式指標 → ROP）。Bulekov 的論文（"Fuzzing for Software Vulnerabilities in QEMU Device Models"，USENIX Security 2021 方向）也是理解 QEMU device bug 分佈的好材料。

- **Kostya Kortchinsky, "Cloudburst"（Black Hat USA 2009 投影片，可在 archive.org 找到）**——[F] GPU 格的經典案例，VMware SVGA 逃逸。理解「host 解析執行 guest 送的命令流」這種攻擊模式為何在 GPU 上特別致命，以及早期逃逸「單洞打全鏈」的時代結構。對比現代多洞鏈，能感受到攻防演進的節奏。

- **QEMU `docs/system/security.rst` 與 reentrancy guard 相關 commit（`memory: add reentrancy_guard`，2022 年）**——維護者視角的攻擊面說明，以及近年對 DMA reentrancy UAF 的緩解。讀這份文件能理解防守方怎麼看這張圖、已經堵了哪些格子（尤其 reentrancy guard 的引入說明了哪類攻擊在 2021 年前是真實有效的）。

- **Zero Day Initiative / Pwn2Own virtualization 歷年 writeup 索引（thezdi.com/blog）**——把這張地圖對到真實賽事戰果：哪些格年年被打穿（VirtualBox/VMware 的 device、GPU、backdoor），哪些格幾乎沒人碰（CPU 虛擬化）。特別推薦翻 2019–2024 年的 VirtualBox 和 VMware 逃逸報告，把每條逃逸鏈的每一步標回這張圖的哪一格，這個練習讓你從「看報告」進化到「理解策略」。

- **Phrack #70, "VM escape techniques"（如果有出）與相關文章**——Phrack 歷史上有多篇涵蓋 QEMU device 模擬攻擊面的技術文章，搜索 "QEMU escape" 或 "VM escape" 可找到技術深度夠高的一手分析。配合 QEMU 原始碼同步閱讀，是從「理解概念」到「能寫 exploit」之間最好的橋梁之一。

地圖畫完了，你知道整門課要打哪些點、每個點大概長怎樣、對應哪個 Part。接下來 Part 1 從最底層的硬體機制開始——Intel VT-x 到底怎麼運作，VMEXIT 這個攻擊入口的每一個齒輪都拆給你看。

→ [Ch 4 Intel VT-x](./04-intel-vtx.md)
