# Ch 1 — 什麼是 VM Escape：打穿雲端隔離的那條邊界

> **目標**：搞清楚 VM escape 到底在打哪條邊界、威脅模型長怎樣、為什麼它是整個雲端安全的地基，以及它在 pwn 天梯上接在你已學過的哪些課之後。

VM escape（虛擬機逃逸）一句話：**從一個 guest 虛擬機內部，取得 host 上執行 hypervisor 的那個行程的 code execution。** 你原本被關在一個「以為是完整電腦」的沙盒裡，逃逸就是打穿沙盒的牆，跑到外面那台真實主機上。

這是 pwn 的頂級目標之一，原因不是單一技巧難，而是這條邊界**同時是整個公有雲信任模型的最後一道牆**。這一章不寫任何 exploit，先把「這件事有多重、值多少錢、和你已會的東西什麼關係」講透。動機不清楚，後面 40 章你會不知道自己在爬什麼山。

## 為什麼需要這個？

公有雲的商業模式建立在一個假設上：**AWS、GCP、Azure 把同一台實體伺服器切成很多 VM，租給互不信任、甚至互相敵對的租戶，而他們彼此打不到對方，也打不到雲廠自己。** 你租一台 EC2，隔壁那台 EC2 可能是你的競爭對手、可能是攻擊者故意開來打你的。撐住這個「同機不同租戶互相隔離」承諾的，就是 hypervisor。

**VM escape 就是打破這個承諾。** 一旦你能從自己的 guest 逃到 host：

- 你站上了那台實體機的 host，host 上跑著**同機所有其他租戶的 VM**——你可以讀他們的記憶體、竊他們的資料、控他們的機器。
- 你可能進一步打管理平面、橫向移動到雲廠的內網。
- 對雲廠而言這是災難級的漏洞——它讓「多租戶共用硬體」這個省成本的核心設計整個破功。

所以這條邊界被防得極重，而能打穿它的人極少。這也是為什麼它值那麼多錢（下面講），以及為什麼這門課排在 pwn 天梯的最上層。

## 先建立直覺：三層邊界的精確圖

把整個系統想成三層同心牆，但要比直覺想的更精確：

```
   ┌──────────────────────────────────────────────────────────────────────────┐
   │  Host 實體機（雲端伺服器）                                                  │
   │                                                                          │
   │  ┌─────────────────────────────────────────────────────────────────┐    │
   │  │  Host OS（Linux）+ KVM 核心模組                                    │    │
   │  │                                                                  │    │
   │  │  ┌──────────────────────────────────────────────────────────┐   │    │
   │  │  │  QEMU 行程（host userland）— 這才是逃逸的靶              │   │    │
   │  │  │                                                          │   │    │
   │  │  │  ┌──────────────────┐   ┌──────────────────────────┐   │   │    │
   │  │  │  │   Guest A (你的 VM)│   │  Guest B（別人的租戶 VM）  │   │   │    │
   │  │  │  │                  │   │                          │   │   │    │
   │  │  │  │  guest kernel    │   │   ← 逃逸後這台也淪陷      │   │   │    │
   │  │  │  │  ┌────────────┐  │   │                          │   │   │    │
   │  │  │  │  │  guest app │  │   │                          │   │   │    │
   │  │  │  │  └────────────┘  │   │                          │   │   │    │
   │  │  │  └──────────────────┘   └──────────────────────────┘   │   │    │
   │  │  └──────────────────────────────────────────────────────────┘   │    │
   │  └─────────────────────────────────────────────────────────────────┘    │
   └──────────────────────────────────────────────────────────────────────────┘

   你打過的邊界（前面的課）：
     guest app → guest kernel   = kernel_pwn（提權，還在同一個 guest 裡）
     JS → renderer              = browser_pwn（沙盒內 RCE）
   這門課要打的邊界：
     guest（整台）→ host QEMU 行程   = VM escape  ★
   之後還有一層（Part 7）：
     QEMU 行程 → host kernel    = hypervisor → host root（接 kernel_pwn）
```

關鍵直覺：**逃逸打的不是「app → kernel」，而是「整台 guest → 外面的 hypervisor」。** 你可能已經是 guest 裡的 root（甚至 guest kernel），但你還是被關在 guest 這層牆內。逃逸是穿越 guest 與 host 之間那道牆，而那道牆是由 hypervisor 這個 **host 上的 userland 行程（以 QEMU 為例）** 撐起來的。所以打穿它，本質上就是打穿那個 host 行程——一個 C 寫的、可以被 memory corruption 幹掉的普通程式。

另一個必須建立的直覺：**從 guest 到 host root 通常要兩跳。** 第一跳是 VM escape（guest → QEMU 行程），第二跳是從 QEMU 行程提權到 host root（因為現代 QEMU 跑在 seccomp/SELinux 沙盒下）。Pwn2Own 的「bonus」就是在賞金結構裡反映了這個兩跳設計。

## 歷史脈絡：誰把這道牆釘進了產業意識

虛擬化早年（2000 年代初）大家其實不太信任 VM 隔離，VM 主要用來整併伺服器、跑測試。真正把「VM 當安全邊界」推成產業共識的是雲端崛起（2006 AWS EC2 之後）。隔離被當成安全邊界，攻擊者自然開始鑿它。

以下是值得銘記的逃逸事件年表，從此之後你看到任何 writeup 都能對到歷史座標：

| 年份 | 事件/CVE | 目標 Hypervisor | 攻擊面 | 一句重點 |
|------|----------|----------------|--------|---------|
| 2009 | **Cloudburst** (Black Hat) | VMware Workstation/Server | SVGA 顯卡模擬 | 史上第一個公開示範的完整 VM escape，打虛擬顯卡的命令流解析 |
| 2012 | **CVE-2012-0217** | Xen（64-bit PV guest） | SYSCALL 指令處理 | x86 SYSRET 的 privilege level 邊界條件，打 hypervisor CPU 邏輯本身（稀有的 CPU 虛擬化 bug）|
| 2014 | **Pwn2Own**（VirtualBox） | Oracle VirtualBox | 3D 顯卡模擬 | Pwn2Own 首次成功逃逸 VirtualBox，用 3D acceleration 做跳板 |
| 2015 | **VENOM / CVE-2015-3456** | QEMU/KVM、Xen、VirtualBox | FDC 軟碟控制器 | 緩衝區溢位；FDC 對每台 x86 guest 強制初始化、無法關掉，影響面極廣 |
| 2016 | **CVE-2016-5403** | QEMU virtio-balloon | virtio balloon device | DoS → crash；virtio 攻擊面開始被重視 |
| 2017 | **Pwn2Own**（VMware Workstation） | VMware Workstation | JavaScript → VMware backdoor | 從瀏覽器 JS RCE 接 VMware backdoor 通道，跨兩個沙盒的三段鏈 |
| 2018 | **CVE-2018-3646**（L1TF） | KVM、VMware、Hyper-V | CPU 微架構（L1 data cache） | 側通道逃逸，Intel CPU 的 L1 cache 不當 flush；修補需要完整的 L1 cache flush 機制 |
| 2019 | **Pwn2Own**（VirtualBox 3-bug chain） | Oracle VirtualBox | OOB read + integer overflow + UAF | 三洞鏈，展示現代逃逸「一個洞撐不起全鏈，要組合」的典型結構 |
| 2020 | **CVE-2020-14364** | QEMU USB 模擬 | USB封包長度檢查 | `setup_len` 溢位越界讀寫 4096 位元組 buffer，有公開完整 PoC |
| 2021 | **Pwn2Own Austin**（Parallels） | Parallels Desktop | 多個記憶體錯誤 | macOS 桌面虛擬化市場首次被正式攻破且公開 |
| 2022 | **CVE-2022-26353/26354** | QEMU virtio-net/virtio-scsi | virtqueue 處理 | descriptor 長度驗證問題，標誌 virtio 成為現代逃逸的新焦點 |
| 2023 | **Pwn2Own Vancouver**（VMware Workstation，US$80k） | VMware Workstation 17 | XHCI USB 控制器 | Abdul Aziz Hariri 的 use-after-free，三段鏈最終到 host code exec |
| 2024 | **Pwn2Own Vancouver**（VirtualBox，Bruno Pujos） | Oracle VirtualBox | VirtualBox UAF + Windows kernel | guest → QEMU → Windows UAF → SYSTEM，總獎 US$90k |

讀這張表時記住幾個模式：
1. 攻擊面確實往 device 模擬集中（FDC/USB/GPU/virtio），CPU 虛擬化本身被成功打的極少（L1TF 是微架構層，不是正常軟體 bug）。
2. 逃逸鏈越來越長（三洞以上），單一 bug 包搞定全鏈的時代在 2015-2016 之後逐漸結束。
3. Pwn2Own 每年的賞金與目標清單是公開的市場定價基準，對應這張表看就知道哪些 hypervisor 最被重視。

## 威脅模型：從哪裡出發、目標是什麼

VM escape 的標準威脅模型，講清楚三件事：

**起點（攻擊者能力）**：
- 你**完全控制 guest**。本課的預設是「guest 內已 root」——你能載入 kernel module、直接對虛擬硬體送任意 PIO/MMIO/DMA。這不是作弊：雲端租戶本來就對自己租的 VM 有 root。「先在 guest 裡提權」是 `kernel_pwn` 的事，這門課把它當已完成的前提。
- 也有更嚴苛的模型（guest 內只有非 root，甚至只有一段沙盒化的 guest 程式），但那是進階題；主線我們假設 guest root。

**要打穿的邊界**：
- guest 與 host 之間唯一「合法的溝通管道」，就是 guest 對虛擬硬體的存取。guest 一存取虛擬硬體（讀寫某個 device 的暫存器、發 DMA），就會觸發 VMEXIT，控制權交回 host 的 hypervisor 去模擬那個硬體的行為。**這條合法管道，就是攻擊面。**

**終點（目標）**：
- 在 host 上取得 hypervisor 行程的 **code execution**（例如控制 QEMU 行程的 RIP、跑你的 shellcode 或 ROP chain）。
- 拿到 QEMU 行程權限後，可能還要接著繞 host 的 seccomp/SELinux 沙盒、甚至再打 host kernel 提權——那是 Part 7 的事。**「拿到 hypervisor 行程 code exec」就算逃逸成功。**

### 攻擊者視角的兩個具體情境

理解威脅模型要接地氣，這裡給兩個具體情境，說明「已 root 的 guest 能做什麼、怎麼利用合法管道發起攻擊」：

**情境一：透過 I/O port 觸發 FDC bug（VENOM 類型）**

```
1. guest 裡 root 寫一個 kernel module（或直接 /dev/port 或 /dev/mem 存取）
2. 對 I/O port 0x3F5（FDC data port）送精心構造的命令序列
3. 命令序列的長度超過 QEMU FDC 模擬程式碼的固定大小 buffer
4. QEMU 在處理 FDC 命令時，把 guest 給的資料寫到 host stack 上超出邊界的位置
5. 控制 QEMU 的返回位址 → host code exec
```

整個過程 guest 沒有做任何「非法」的事——讀寫 I/O port 是 guest root 的合法能力。bug 在 QEMU 模擬 FDC 的 C 程式碼裡。

**情境二：透過 virtio descriptor 的 DMA 造 OOB（現代 virtio 類型）**

```
1. guest 裡 root 控制 virtio-net 的 TX ring（傳送佇列）
2. 填一個 descriptor：addr = guest_ram_addr, len = 0xFFFFFFFF（故意填超大）
3. QEMU 的 virtio-net 處理 TX descriptor 時，用 guest 給的 len 做 DMA 讀取
4. 若沒有驗 len 的上限 → 從 guest RAM addr 往後讀 4GB → host heap OOB read
5. 利用 OOB read 洩漏 host 位址 → 進一步造 OOB write → 控函式指標
```

兩個情境說明同一個結構：**guest 合法存取虛擬硬體 → QEMU 的 C 程式碼處理 guest 的資料 → C 程式碼沒驗好邊界 → heap/stack corruption → 逃逸**。「合法管道」這四個字是關鍵——逃逸不需要找到任何「進入 hypervisor 的秘密後門」，直接用 guest 本來就能用的硬體存取介面。

## 雲端隔離模型的地基：為何 hypervisor 是最後一道牆

多租戶雲端安全是一個分層防禦體系，但各層的假設彼此依賴：

```
   [應用層]    容器（Docker/K8s）     → 靠 Linux namespace/cgroup 隔離
       ↓        一旦 container 逃逸，回到 host OS
   [OS 層]     Host Linux 的 DAC/MAC  → 靠 SELinux/seccomp 隔離
       ↓        一旦提權，回到 hypervisor
   [硬體虛擬化層]  Hypervisor（KVM/QEMU）→ 靠 CPU 虛擬化 + device 模擬隔離  ★ 這層
       ↓        一旦逃逸，同機所有租戶全曝露
   [硬體層]    Intel TXT / ARM TrustZone → 靠硬體保證
```

每一層的隔離都以「下面那層可信」為前提。容器逃逸只要繞過 OS；VM escape 繞過了 hypervisor，就打穿了容器依賴的那個 OS。因此，**hypervisor 是整個雲端隔離體系的信任錨點**——它比容器那層低，比 OS 那層低，是雲廠用來跟租戶說「你的程式碼不管怎樣都跑不到我的系統上」的終極保證。

這也是為什麼 hypervisor escape 的市場定價遠高於容器 escape：容器 escape 還被 host OS 擋著；hypervisor escape 打穿的是最後那道牆。

## 逃逸值多少錢

用 Pwn2Own 的獎金當標尺最直觀——它是這類漏洞公開、可查的市場定價。

**Pwn2Own Vancouver 2024**（virtualization category，真實數字）：

- **VMware Workstation 逃逸**：US$80,000
- **Oracle VirtualBox 逃逸**：US$40,000
- **額外 bonus**：逃出 guest 後，再用一個 Windows kernel 漏洞在 host 上提權到 SYSTEM（ESXi 除外），加 US$50,000。

實際結果：Reverse Tactics 的 Bruno Pujos 與 Corentin Bayet 用一串 VirtualBox 漏洞加一個 Windows UAF，從 VM 逃到 host 拿到 SYSTEM，共領 **US$90,000**。同屆 VMware Workstation 與 VirtualBox 都被成功逃逸。

幾點要讀懂：

- 這只是**競賽公開定價**。灰市/漏洞收購商（如某些 broker）對可靠的 hypervisor 逃逸，尤其針對 ESXi、Hyper-V 這類雲/企業關鍵目標，報價通常遠高於 Pwn2Own——因為 escape 的殺傷力對雲基礎設施是戰略級的。
- VMware ESXi（真正跑生產雲的那個）被列為更高價值目標，且逃逸它的難度與稀有度都高於 Workstation。
- 對照著看：同一個 Pwn2Own，一個瀏覽器 renderer RCE 或 Windows LPE 的獎金級別通常低於一個完整 hypervisor 逃逸——**這反映了逃逸在難度與影響上都站在食物鏈頂端。**

## 兩個標誌性案例：感受「逃逸長什麼樣」

在進入技術章節之前，先把兩個最著名的逃逸案例的**結構**（不是完整利用細節）描述一遍。不是要你現在就懂，而是讓後面每一章的技術說明都有具體的影像可以對照。

### Cloudburst（2009）——史上第一個公開完整 VM escape

Kostya Kortchinsky（當時在 Immunity Inc.）在 Black Hat USA 2009 示範的 VMware SVGA 逃逸。

- **攻擊面**：VMware Workstation / Server 的虛擬 SVGA（Super VGA）顯示卡模擬。guest 透過 SVGA I/O port 與 SVGA FIFO（一塊 guest 與 host 共享的記憶體環形緩衝區）對 host 送 GPU 命令。
- **bug 本質**：SVGA 命令的邊界檢查缺失——host 解析某些 SVGA blit 命令（位元圖複製）時，用 guest 提供的座標（x, y, width, height）計算目標位址，沒有驗證這個位址是否還在 framebuffer 範圍內，造成 host 記憶體 OOB write。
- **影響**：第一次公開示範「VM 牆可以從虛擬顯示卡打穿」，徹底改變了業界對 hypervisor 安全模型的認知。在此之前，很多人認為 VM escape「理論上可能但實際上太難」。

### VENOM（CVE-2015-3456，2015）——影響面最廣的逃逸漏洞之一

CrowdStrike 的 Jason Geffner 發現並命名的 QEMU FDC（軟碟控制器）逃逸。

- **攻擊面**：QEMU 的 `hw/block/fdc.c`，模擬 1980 年代的 NEC µPD765 軟碟控制器晶片。
- **bug 本質**：FDC 的命令處理有一個固定大小的 FIFO buffer（`uint8_t fifo[FD_SECTOR_LEN]`，512 位元組），但沒有檢查寫入的命令資料總長度是否超過這個 buffer。guest 送一個特製的 FDC 命令序列，透過反覆發送 WRITE DATA 命令，把 FIFO 撐到溢位，覆蓋緊接在 buffer 後面的 host 記憶體（包含函式指標）。
- **為什麼恐怖**：FDC 是 x86 PC 標準的「必備」硬體，QEMU 對每一個 x86 guest 都會初始化 FDC，且在 QEMU 設計上 FDC 無法在 guest 啟動後被移除。這意味著**所有**使用 QEMU 的 x86 guest（無論有沒有配置軟碟機）都在漏洞影響範圍內，包含 Xen、KVM、VirtualBox 的 QEMU 底層。影響面遍及所有主流 Linux 發行版的 KVM 虛擬化堆疊與大量雲廠。

把這兩個案例記住，後面每一章技術說明都會多一個「這對應到 Cloudburst 的哪一步」或「VENOM 在這個機制上出了什麼問題」的具體掛鉤。

## 和其他 pwn 的關係：你已經會一半了

好消息：VM escape 的「後半段」和你已經熟練的 userland pwn **幾乎一模一樣**。

一條 QEMU escape 拆成兩段：

```
   前半段（這門課的新東西）：            後半段（你已經會了）：
   ────────────────────────           ────────────────────────
   guest 對 device 送 I/O               host QEMU 的 heap 被破壞
        │                                     │
   觸發 QEMU device callback              info leak（漏 host 位址、繞 ASLR/PIE）
        │                                     │
   在 host heap 上造成 OOB / UAF          heap 佈局、控制一個函式指標
        │                                     │
   ─────────────────────────►            劫持控制流 → ROP → code exec
```

- **後半段就是 `binary_exploitation`**：host QEMU 是個 userland C 程式，用 glibc heap。你學過的 heap 佈局、UAF 利用、info leak 繞 ASLR、ROP、控 RIP——**原封不動搬過來用**。QEMU escape 的 exploit 開發階段，本質就是打一個帶 ASLR/NX/（有時）PIE 的 heap 程式。
- **`kernel_pwn` 給你的耐心**：打一個龐大、複雜、多執行緒的 C 程式，追一個 race 或 UAF，這種「在大程式裡耐著性子找可控 primitive」的功夫，kernel pwn 練過了。QEMU 的體量和複雜度是同一量級。
- **`browser_pwn` 給你的心法**：browser pwn 教你「讓一個複雜引擎對自己的物件產生錯誤認知」。VM escape 也是——讓 QEMU 對 guest 送來的長度/指標/狀態產生錯誤信任。type confusion 的味道相通。

**這門課新增的、你還沒學過的，是「前半段」**：CPU 虛擬化怎麼運作（VT-x/EPT/VMEXIT，Part 1）、QEMU 怎麼模擬硬體（MemoryRegion/device dispatch/DMA，Part 2）、以及這些機制上長出哪些 bug pattern（Part 3 之後）。把前半段學會，接上你已有的後半段，就是一條完整逃逸。

## 底層機制：guest 一次 I/O 怎麼變成 host 上的攻擊

```
   ┌────────────────────────────────────────────────────────────┐
   │ guest 內（你 root）                                          │
   │                                                            │
   │   out dx, al  /  mov [MMIO_addr], eax                      │
   │   （一條普通的 I/O 指令）                                    │
   └──────────────────────────────┬─────────────────────────────┘
                                  │ CPU 偵測到這是敏感存取
                                  ▼
                           ┌────────────┐
                           │  VMEXIT    │  guest CPU 暫停，儲存狀態到 VMCS
                           │  reason:   │  控制權跳回 root mode
                           │  I/O / EPT │
                           └─────┬──────┘
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ KVM（host kernel 模組）                                      │
   │   讀 exit reason，判斷：                                      │
   │   - 能自己處理 → 直接 VMRESUME 回 guest                      │
   │   - 是 device I/O → 透過 /dev/kvm ioctl 退回 QEMU userland  │
   └──────────────────────────────┬──────────────────────────────┘
                                  │ ioctl KVM_RUN 返回
                                  ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  QEMU（host userland，你真正的靶）                             │
   │                                                              │
   │  address_space_write(gpa, ...)                               │
   │         ↓                                                    │
   │  memory_region_dispatch_write()  ← 查位址屬哪個 device        │
   │         ↓                                                    │
   │  ops->write(opaque, addr, val, size)  ← device callback      │
   │         ↓                                                    │
   │  device C 程式碼用 guest 給的 addr/val/size 操作 host heap     │
   │                                                              │
   │  ← 若 addr 超範圍、長度沒驗：OOB write / heap corruption       │
   └──────────────────────────────┬───────────────────────────────┘
                                  │ heap 被精確破壞後
                                  ▼
                  info leak → 繞 ASLR → 控函式指標 → ROP
                                  │
                                  ▼
                        host code execution  ★ 逃逸完成
```

這張圖是整門課的骨架。Part 1 把「VMEXIT 為什麼會發生、KVM 怎麼接手」講到硬體層；Part 2 把「QEMU 找到 device、呼叫 callback」講到原始碼層；Part 3 之後專攻「callback 裡的 bug 怎麼變成 code exec」。現在你只要記住：**每一次 guest 對虛擬硬體的存取，都是一次把資料餵進 host QEMU 程式碼的機會**，而攻擊就是餵出 bug。

## QEMU 的規模感：你的靶有多大

在進入詳細技術之前，值得先感受一下「逃逸的靶——QEMU 行程」到底是個什麼規模的東西。

QEMU 是 Fabrice Bellard 在 2003 年開始的開源專案，今天由 QEMU community 維護。它的程式碼量（未實測，量級合理）：

```
大約 2,500,000 行 C 程式碼（含 tests/docs）
hw/ 子目錄：350+ 個 .c 檔，每個對應一個 device 或 bus 的模擬
target/ 子目錄：約 20 種 guest 架構（x86、ARM、RISC-V、MIPS…）
```

這意味著：
- 靶程式體量接近 Linux kernel 的 1/4，遠大於一般 CTF pwn 題
- 多執行緒（vCPU 執行緒 + I/O 執行緒 + 主執行緒），race condition 普遍可能
- 幾百種 device 的 C 程式碼，大多數沒有現代 memory safety 設計（寫於 2005–2015 年）
- 用 glibc malloc（ptmalloc2），你在 `binary_exploitation` 學的 heap 分析全部適用

**攻擊者最關心的 QEMU 記憶體布局**（簡化，未實測）：

```
QEMU 行程的 virtual address space（示意）：
[text] .text .rodata（可執行程式碼，ASLR 搖動）
[heap] glibc heap（所有 device 狀態結構在這，是主戰場）
[guest_ram] 一塊大 mmap（guest 的 RAM，guest 可完全控制內容）
[libraries] libc, libglib, libpixman…（含函式指標，info leak 目標）
[stack] 各執行緒的 stack
```

QEMU 行程的 glibc heap 裡，每個模擬的 device 有自己的「狀態結構」（例如 `RTL8139State`、`FDCState`、`EduState`）。這些狀態結構裡放著 device 的內部緩衝區、暫存器值、函式指標（callbacks），以及指向其他結構的指標。**逃逸的「後半段」本質就是：讓 guest 的 I/O 操作在 host heap 上破壞這些結構，然後利用函式指標或結構指標做 RCE。**

## 對比與取捨

| 你打過/要打的邊界 | 起點 | 終點 | 對應課程 |
|---|---|---|---|
| app → kernel | guest 內非 root | guest 內 root | kernel_pwn |
| JS → renderer RCE | 網頁 JS | renderer 行程 code exec | browser_pwn |
| **guest → host** | **guest 內（本課設為 root）** | **host hypervisor 行程 code exec** | **本課 VM escape** |
| host 行程 → host kernel | hypervisor 行程權限 | host root | Part 7（接 kernel_pwn） |

| 攻擊面 | 大小 / bug 密度 | 是否本課主戰場 |
|---|---|---|
| CPU 虛擬化（VT-x/VMCS/EPT 本身） | 小、硬體與 KVM 把關嚴、bug 稀有 | 否（Part 1 理解，非主攻） |
| device emulation（PIO/MMIO/DMA） | 大、幾百種 device、bug 密度最高 | **是（Part 2–6 主線）** |
| paravirt / virtio / hypercall | 中、現代雲用得多、逐漸成焦點 | 是（Part 4 專章） |

## 踩雷集錦

- **錯誤直覺**：「VM escape 是打 guest kernel 提權。」→ **正確認識**：那是 kernel pwn，還在 guest 內。escape 是穿越 guest 到 host。本課甚至把「guest 內已 root」當前提。
- **錯誤直覺**：「逃逸主要靠打 CPU 虛擬化（VT-x/VMCS）的漏洞。」→ **正確認識**：硬體與 KVM 把 CPU 虛擬化那層防得很緊，bug 稀有。**絕大多數逃逸打的是 device emulation**（QEMU 模擬的假硬體）。這是 Ch 3 攻擊面圖的核心。
- **錯誤直覺**：「打穿 QEMU 拿到 code exec 就等於拿到 host root。」→ **正確認識**：你拿到的是 **QEMU 行程的權限**，通常還被 seccomp/SELinux 沙盒關著，離 host root 還有一段（Part 7）。逃逸成功 ≠ host root。
- **錯誤直覺**：「這是全新領域，我 userland pwn 的東西用不上。」→ **正確認識**：逃逸的後半段（leak → 控指標 → ROP → code exec）就是 userland heap pwn，你會的直接用。新的只有前半段（虛擬化 + device 模型）。
- **錯誤直覺**：「hypervisor 是特殊的東西，不像普通程式那樣有 heap 漏洞。」→ **正確認識**：QEMU 就是個 C 寫的 userland 程式，用 glibc malloc。它的漏洞就是普通的 heap overflow / UAF / OOB，只是觸發路徑是 guest 的硬體存取。
- **錯誤直覺**：「容器逃逸和 VM escape 差不多嚴重。」→ **正確認識**：容器逃逸後還被 host OS 的隔離機制（seccomp/namespace）擋住，還要進一步提權。VM escape 是打穿 hypervisor 這層，比容器逃逸低一整層，拿到的是 hypervisor 行程的 code exec，是雲廠最怕的那種漏洞。
- **錯誤直覺**：「逃逸鏈只要一個 bug 就夠。」→ **正確認識**：現代逃逸幾乎都是多 bug 鏈（通常至少三個：info leak + heap 原語 + 控流劫持），Pwn2Own 2019 VirtualBox 就是三洞鏈。單一 bug 打完整逃逸的時代在 2016 年之後基本結束了。

## 進階：再往深一層

### type-1 vs type-2，以及本課為何以 QEMU/KVM 為主

VMware ESXi、Hyper-V、Xen 屬 type-1（直接跑在硬體上），VirtualBox、VMware Workstation、QEMU 屬偏 type-2（跑在 host OS 上）。本課主線用 QEMU/KVM 因為它**開源、可自編可除錯、writeup 生態最完整**，是學逃逸的最佳教具；Part 5/6 會把方法遷移到 VirtualBox 與 VMware。這些分類 Ch 2 詳談。

type-1 的攻擊面從根本上不同：ESXi 的 vmkernel 是自帶 OS 的 type-1，打它的路徑不是從 Linux 提權到 hypervisor，而是直接在那個 vmkernel 空間裡找 bug。這使得 ESXi 逃逸更難、價更高，但本課先把 QEMU 的基礎打扎實，再遷移。

### 現代雲的縮面運動

AWS 後來用 Nitro 把虛擬化與 device 模擬卸載到專用硬體卡（縮小 host 上的軟體攻擊面）、Firecracker 是極簡 microVM，砍掉大量 legacy device，只留幾個 virtio device。Google 的 gVisor 走的是另一條路：把 guest 系統呼叫截在用戶態，不讓它打到真實 Linux kernel。

這些「縮面」運動直接改變了逃逸的有效攻擊面：VENOM 那種打 FDC 的攻擊對 Firecracker 無效（因為根本沒有 FDC），現代逃逸必須鎖定保留下來的 virtio 系列。Part 7 的 microVM 章會拆這股趨勢。

### 公開事件的信噪比問題

公開資訊（CVE、Pwn2Own writeup、學術論文）只是冰山一角。真正高價值的逃逸（尤其針對 ESXi、Hyper-V 的 0-day）幾乎不會公開，因為它們的市場價值遠超 Pwn2Own 賞金。你在這門課學到的技術，對應的實際影響遠比公開 CVE 列表所顯示的更大。

## 動手練習

（本章為概念章，練習以「建立地圖與判斷」為主，不寫 code。）

1. **畫威脅模型圖**：不看課文，自己畫出 guest / guest kernel / hypervisor 行程 / host / 其他租戶 VM 的關係，標出「VM escape 打的是哪條邊界」「起點與終點各在哪」。跟本章的圖對照。
2. **拆一條逃逸成兩段**：用一句話寫出 QEMU escape 的「前半段（新學）」與「後半段（已會）」各是什麼，並各自對應到你學過的哪門課。
3. **查一個真實 escape 的賞金脈絡**：找一篇 Pwn2Own 或 ZDI 對某個 hypervisor 逃逸的公告，記下：目標是哪個 hypervisor、賞金多少、逃逸鏈用了幾個 bug、有沒有搭配 host 提權。體會「一條完整逃逸鏈通常不只一個 bug」。
4. **年表定位**：從上面的歷史逃逸年表選三條，各用一句話說明它攻擊的是哪類攻擊面（device 模擬 / CPU 虛擬化 / GPU / virtio），並說它是否影響了你今天選的 IaaS 雲廠。
5. **自我定位**：寫下你目前在 pwn 天梯的哪一階（userland / kernel / browser 各熟練到什麼程度），據此判斷本課哪些「後半段」你能跳讀、哪些「前半段」要慢慢啃。

## 本章重點整理

- VM escape = 從 guest 內部取得 host 上 hypervisor 行程的 code execution，打穿的是**多租戶雲端隔離的最後一道牆**。
- 威脅模型：**起點**通常設為 guest 內已 root（guest 提權是別門課的事）；**合法攻擊管道**是 guest 對虛擬硬體的 I/O 存取；**終點**是 host 上 hypervisor 行程的 code exec。
- 值多少：Pwn2Own 2024 virtualization——VMware Workstation US$80k、VirtualBox US$40k，加 host 提權 bonus US$50k；灰市對 ESXi/Hyper-V 等目標更高。定價反映其稀缺與影響。
- 和其他 pwn 的關係：**後半段（leak→控指標→ROP→code exec）就是 userland heap pwn，你已會**；新學的只有前半段（CPU 虛擬化 + device 模擬機制）。
- 主戰場是 **device emulation**，不是 CPU 虛擬化本身——後者被硬體與 KVM 防得很緊、bug 稀有。
- 拿到 hypervisor 行程 code exec ≠ host root，通常還被 seccomp/SELinux 關著（Part 7）。
- 現代逃逸幾乎都是多 bug 鏈，單 bug 打完整逃逸在 2016 之後基本絕跡。

## 自我檢核

- [ ] 我能用一句話說清楚 VM escape 打的是哪條邊界，以及它和 kernel 提權的差別。
- [ ] 我能描述標準威脅模型的起點、合法攻擊管道、終點。
- [ ] 我能從年表中選出至少三個著名逃逸事件，說明年份、目標、攻擊面與主要手法。
- [ ] 我能說明為什麼 device emulation 是主戰場，而 CPU 虛擬化本身不是。
- [ ] 我能把一條 QEMU escape 拆成「已會的後半段」與「要學的前半段」。
- [ ] 我能解釋 VM escape 和容器逃逸在層次上的差異，以及為何 hypervisor 是雲端信任錨點。

## 延伸閱讀

- **Jason Geffner / CrowdStrike, "VENOM (CVE-2015-3456)" 原始公告（crowdstrike.com/blog/venom）與 CIRCL TR-37 技術分析**——讀「為什麼即使沒配軟碟也中招」那段，理解 device 攻擊面「guest 無法拒絕的預設硬體」這個關鍵性質。Ch 23 會深挖，這裡先建立印象。這份公告也是「如何向企業/媒體說清楚一個複雜 hypervisor 漏洞的影響」的寫法範本。

- **Zero Day Initiative — Pwn2Own Vancouver 賽事規則與結果公告（thezdi.com/blog）**——看 virtualization category 的目標清單與賞金表，理解市場如何定價逃逸；本章賞金數字即出自 2024 該賽事。特別值得細看各年逃逸鏈用了幾個 bug、每個 bug 是哪類——你會看到 multi-bug chain 成為標配的歷史節點大約在 2017–2019 之間。

- **Kostya Kortchinsky, "Cloudburst" (Black Hat USA 2009) 投影片/paper**——早期 VMware SVGA 逃逸，把「VM 牆可被打穿」釘進產業意識的標誌案例。讀它怎麼從顯示卡模擬的命令流解析打到 host，以及為什麼「GPU 命令流 = 可控的 host 端解釋器輸入」這個模型至今仍然有效。

- **"Nitro System" / AWS Firecracker 設計文件（github.com/firecracker-microvm）**——理解現代雲如何用專用硬體與極簡 microVM 縮小逃逸攻擊面，看清這門課的攻擊在生產環境面對的是什麼防線。Firecracker 的 device 清單（只有 virtio-net/blk/vsock 等幾個）對比 QEMU 的幾百種，量級差距一眼就懂。Part 7 會回來。

- **QEMU `docs/system/security.rst`**——QEMU 官方對「哪些邊界是安全邊界、guest→host 被視為攻擊面」的立場說明。用維護者的視角看你要打的是什麼，很有校準作用。特別注意它把「guest 已 root 對 QEMU 發起的攻擊」明確列為**不承諾防禦**的範圍——這直接說明了本課威脅模型的合理性。

- **Google Project Zero 部落格（googleprojectzero.blogspot.com）的 hypervisor 研究文章**——Project Zero 歷年針對 KVM、VirtualBox、VMware 的漏洞分析，包含多篇對 KVM 罕見 CPU 虛擬化 bug 的完整分析。讀他們的文章能感受到「理解硬體規格到能找 corner case」的研究深度門檻。

知道了打的是什麼、值多少錢、和你已會的東西什麼關係，接下來要把「hypervisor 到底怎麼跑一個 guest」從歷史演進講到硬體輔助，你才知道 VMEXIT 這個攻擊入口是怎麼來的。

→ [Ch 2 VMM 怎麼跑一個 Guest](./02-how-vmm-runs-guest.md)
