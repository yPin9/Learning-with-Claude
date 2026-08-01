# Ch 38 — 雲端 microVM：Firecracker、gVisor 攻擊面

> **目標**：理解雲端業者為什麼不用完整 QEMU、Firecracker 和 gVisor 各自如何縮減攻擊面，以及攻擊面縮減後剩下的是什麼。

## 為什麼需要這個？

2017 年之前，AWS Lambda 的 workload 是跑在容器（container）裡——多個 Lambda 函數可能共用一個 host kernel 行程，隔離靠 namespace/cgroup。一個 container escape 就是 host escape。

AWS 把問題推得更深：「就算 container escape 了，能不能讓攻擊者面對的是一個 VM 邊界而非 host？」但用完整 QEMU 又太重、啟動太慢（Lambda cold start 不能超過幾百毫秒）。

他們的答案是 Firecracker：**一個只做 VM 邊界的最小化 VMM，放棄一切非必要功能**。

gVisor 是 Google 的另一個答案，出發點不同：**不用 VM，改用 userspace kernel 攔截 syscall**。

這兩個方向定義了現代雲端的安全隔離思路，也各自帶來了新的攻擊面。

## 先建立直覺

對比一下攻擊面的大小：

```
完整 QEMU（帶所有 device）：
  device emulation: ~300 種硬體（音效卡/USB/GPU/FDC…）
  PCI subsystem、BIOS（SeaBIOS）、ACPI
  SPICE/VNC、USB passthrough、audio backend
  程式碼：數百萬行 C
  語言：C（每個解引用都是潛在 UAF/OOB）

Firecracker：
  device emulation: virtio-net、virtio-block、virtio-vsock、序列埠
  無 BIOS（Linux direct boot）
  無 PCI（virtio-mmio 不需要 PCI）
  無 USB、無音效、無 VGA
  程式碼：~5 萬行 Rust
  語言：Rust（記憶體安全，大幅減少 memory-safety bug）

gVisor（sentry）：
  不跑 VM，改在 userspace 重新實作 Linux kernel syscall 介面
  程式碼：~25 萬行 Go
  攻擊面：sentry 實作的 syscall subset
  語言：Go（記憶體安全，但 interface/goroutine 有自己的問題）
```

縮小 device model 和用記憶體安全語言是 Firecracker 的核心安全賭注。

### 攻擊路徑對比

三個方案的攻擊路徑結構根本不同，先把這個差異刻在腦子裡：

```
QEMU（傳統路徑）：
  guest userspace
       ↓ syscall
  guest kernel（VM 內）
       ↓ MMIO/PIO write → VM exit
  QEMU device C code ← 這裡是主戰場
       ↓ heap overflow / UAF
  host RIP control → shell

Firecracker（縮減路徑）：
  guest userspace
       ↓ syscall
  guest kernel（VM 內）
       ↓ virtio MMIO write → VM exit
  Firecracker VMM Rust code ← 攻擊面大幅縮小
       ↓ logic bug（Rust panic / integer arithmetic / 索引邏輯）
  host logic corruption → 逃逸（難度高，無 memory-safety bug）

gVisor（完全不同路徑）：
  sandboxed application
       ↓ syscall instruction
  ptrace 或 KVM VM exit → sentry 攔截
  sentry Go syscall 實作 ← 攻擊面在這裡
       ↓ logic bug in Go
  sentry 呼叫 host syscall → host kernel 行為異常
```

這三條路徑的差異決定了各自的 exploit 開發難度和所需技巧：QEMU 是 C 記憶體安全 bug 的傳統路線；Firecracker 要找 Rust 邏輯 bug 或 unsafe block；gVisor 要找 sentry 的 Go 邏輯 bug。

### 三方詳細對比表

```
                   QEMU              Firecracker         gVisor
────────────────────────────────────────────────────────────────────────
實作語言           C                 Rust                Go
記憶體安全         否                是（含 unsafe）     是（含 unsafe）
device 種類        ~300              ~5                  0（syscall 模擬）
攻擊面大小（相對） 非常大            小                  中
sandbox 機制       seccomp（可選）   jailer（預設內建）  sentry + seccomp
啟動時間           ~秒級             <125ms              ~秒（視 platform）
隔離原語           KVM VM            KVM VM + jailer     syscall 攔截（無 VM）
主要攻擊面         device C code     virtio Rust 邏輯    sentry Go syscall 實作
exploit 主要類型   UAF/OOB/heap      邏輯 bug/unsafe     邏輯 bug/Go runtime
已知 CVE 歷史      多（每年數十）    極少                少（但在成長）
典型使用場景       通用 VM/QEMU-KVM  Lambda/Fargate      Cloud Run/k8s/GKE
對記憶體安全 bug   無防護            大幅消除            大幅消除
對邏輯 bug         無防護            無防護              無防護
```

從這個表可以看到：記憶體安全語言消滅了一整個類別的 bug，但邏輯 bug 是所有方案都沒有辦法靠語言本身解決的問題。這是下一個戰場。

## 底層機制：Firecracker

### 架構

Firecracker 是 Rust 寫的 VMM（Virtual Machine Monitor，虛擬機器監視器），使用 KVM（Kernel-based Virtual Machine）做 hardware-assisted virtualization。架構：

```
┌─────────────────────────────────────────┐
│  Host OS                                │
│  ┌───────────────────────────────────┐  │
│  │  jailer（namespace/seccomp/cgroup）│  │
│  │  ┌─────────────────────────────┐  │  │
│  │  │  Firecracker VMM（Rust）    │  │  │
│  │  │  ┌────────┐ ┌────────────┐  │  │  │
│  │  │  │ vCPU   │ │ device     │  │  │  │
│  │  │  │ thread │ │ model      │  │  │  │
│  │  │  │ (KVM)  │ │ (virtio)   │  │  │  │
│  │  │  └────────┘ └────────────┘  │  │  │
│  │  └─────────────────────────────┘  │  │
│  └───────────────────────────────────┘  │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │  microVM（KVM guest）             │  │
│  │  Linux kernel（direct boot）      │  │
│  │  Lambda/Fargate workload          │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

### 關鍵設計決策

**1. jailer**：Firecracker 不是裸跑的，而是透過 `jailer` 包裝。jailer 在啟動 VMM 之前：
- 建立獨立的 mount namespace、PID namespace、network namespace
- 安裝 seccomp filter（比 QEMU 的更嚴格，因為 device 種類少很多）
- 設定 cgroup 限制
- 切換到非 root UID/GID
- `chroot` 到一個最小的 chroot jail

jailer 相當於把 Ch 36 那五層防禦全部預設開啟，且比 QEMU 更嚴格。

**2. virtio-mmio 而非 PCI**：Firecracker 的 virtio device 走 MMIO 方式掛載，不需要完整的 PCI bus 模擬。這砍掉了整個 PCI config space 解析路徑（PCI 是 QEMU 歷史上 bug 很多的地方）。

**3. no BIOS**：guest Linux 直接從 kernel 映像 boot（`linux_boot` 概念），跳過 SeaBIOS 和 ACPI 初始化的大量 C 程式碼。

**4. Linux direct boot**：microVM 用的是客製化的裁剪版 Linux kernel（移除所有不需要的 driver），進一步縮小 guest kernel 攻擊面。

### 為什麼選 Rust：記憶體安全 bug 的量化視角

這個選擇有數據支撐，不是信仰問題。

Microsoft 安全工程師在 2019 年的研究指出，過去十年 Microsoft 追蹤的 CVE 中，**約 70% 是記憶體安全問題**——UAF（use-after-free，釋放後使用）、OOB（out-of-bounds，越界讀寫）、heap overflow（堆積溢位）。Google Project Zero 的統計也有類似結論。C 語言的每一個指標解引用都是潛在漏洞點，而 QEMU 在 C 寫成的數百萬行程式碼中承受著這個風險。

QEMU 的歷史 CVE 主要由哪些類別構成？看幾個知名案例的成因就清楚了：
- Venom（CVE-2015-3456）：FDC（軟碟控制器）的 OOB write
- QEMU QXL（CVE-2019-12155）：display device 的 NULL dereference
- E1000（CVE-2016-1714）：網路 device 的 OOB read/write
- virtio-net（CVE-2017-10806）：stack OOB read

這些全部是記憶體安全問題，Rust 能在編譯期排除。

Firecracker 的 CVE 歷史和 QEMU 的差距說明了選擇有效：截至撰寫時，Firecracker 的公開 CVE 數量是個位數，且主要集中在早期版本。QEMU 每年新增的 CVE 遠多於此。這不是 Rust 比 C「聰明」的問題，而是語言的所有權（ownership）和借用檢查（borrow checker）在編譯期強制消除了整個 bug 類別。

但我們必須清楚說明 Rust 的邊界在哪裡：

**Rust 能排除的**：UAF、dangling pointer（懸空指標）、double-free、大多數 heap overflow 和 stack overflow、data race（在多執行緒場景）。

**Rust 排除不了的**：
- 邏輯 bug（wrong bounds check、integer arithmetic error、狀態機錯誤）
- `unsafe` block 中的所有記憶體安全問題（Rust 在 unsafe 裡不做保證）
- 整數溢位後繞過長度檢查（Rust debug 模式 panic，release 模式 wrapping，兩者行為不同）
- virtio ring 的索引邏輯問題（idx 的 modular arithmetic 是純邏輯，Rust 管不了）

這就是為什麼「Rust 寫的所以沒有 bug」是錯誤的直覺：記憶體安全 bug 消失了，但邏輯 bug 成為新的主戰場。

### Firecracker 剩餘的攻擊面

Firecracker 縮小了很多，但不是零：

- **virtio device 的 VMM 端實作**（Rust 寫的）：若 virtio ring 解析有 bug，仍可能從 guest 觸發 VMM 行為異常。Rust 能排除記憶體安全 bug，但邏輯 bug（越界索引後 Rust panic、或 integer overflow 繞過檢查）仍可能存在。
- **KVM ioctl 介面**：Firecracker 仍然用 KVM，host kernel 的 KVM 驅動本身是攻擊面（但這不是 Firecracker 獨有的）。
- **jailer 的 seccomp filter**：若 filter 有漏洞或某個 syscall 被意外允許，逃逸後的攻擊空間取決於 filter 的嚴謹度。
- **VMM API（REST API over Unix socket）**：Firecracker 有一個用來控制 microVM 的 REST API。若攻擊者能存取這個 socket（在 host 有 code exec 的前提下），可能透過 API 操控 microVM 行為。
- **Rust unsafe 區塊**：Firecracker 雖然主要是 safe Rust，但 KVM ioctl 呼叫等必然需要 `unsafe` block。這些是需要重點審計的地方。

**已知漏洞舉例**：CVE-2019-18960（未實測確認詳情，需查驗）——Firecracker 早期版本某個 device 邊界的問題。Firecracker 的 CVE 歷史遠短於 QEMU，說明攻擊面縮減是有效的，但不是零。

### 如何評估 Firecracker 的剩餘攻擊面

做安全研究的人看到一個新 VMM，第一步是量化剩餘攻擊面。針對 Firecracker 有幾個具體方法：

**找 unsafe block**：
```bash
# 在 Firecracker 原始碼目錄執行（理論指令，未實測）
grep -r "unsafe" src/ --include="*.rs" | wc -l
grep -r "unsafe fn\|unsafe {" src/ --include="*.rs"
```
這能給出 unsafe 程式碼的數量和位置。每一個 `unsafe` block 都是需要人工審計的地方，因為 Rust 編譯器在這裡不提供記憶體安全保證。

**用 `cargo geiger` 掃描 unsafe 使用量**：
```bash
# cargo geiger 是一個掃描 Rust crate 中 unsafe 使用量的工具（未實測，理論預期）
cargo install cargo-geiger
cargo geiger
```
`cargo geiger` 會統計每個 crate 的 unsafe line 數量，並標記哪些是直接的、哪些是從依賴繼承的。輸出會顯示類似 `✗ 42 unsafe lines` 的結果，幫助評估哪個 module 的風險最高。

**virtio ring 邊界問題：最值得關注的邏輯**

virtio ring（又稱 virtqueue）的索引邏輯是 Firecracker 攻擊面中最值得深挖的部分。virtio ring 使用一個 16-bit 的 `idx`，在環形 buffer 中用 modular arithmetic 計算位置：

```
actual_index = idx % queue_size
```

問題在於：
1. `queue_size` 必須是 2 的冪，若 guest 能傳入非 2 的冪的 `queue_size`，modulo 的行為可能不符合預期
2. descriptor chain 的長度（chained descriptor 的數量）若沒有 wraparound 保護，可以構造一個超長的 chain 讓 VMM 進入無限迴圈（DoS）或越界讀取 descriptor table
3. `used_idx` 和 `avail_idx` 的差值沒有正確校驗時，可能被 guest 控制 VMM 處理的 descriptor 範圍

這些不是記憶體安全問題，是純邏輯問題——Rust 的型別系統管不了這層的正確性，需要人工推理或 fuzzing 驗證。

## 底層機制：gVisor

### 架構

gVisor 採取完全不同的路線：不用 VM，改在 userspace 實作 Linux kernel 的 syscall 介面。

```
┌──────────────────────────────────────────────┐
│  Host OS（Linux）                             │
│  host kernel                                  │
│  ┌──────────────────────────────────────────┐ │
│  │  sentry（Go 寫的 userspace kernel）       │ │
│  │  ┌───────────────┐  ┌─────────────────┐  │ │
│  │  │ syscall 實作  │  │  VFS / net 層   │  │ │
│  │  │（Go 重寫的    │  │（Go 重寫的      │  │ │
│  │  │  kernel 邏輯）│  │  kernel 邏輯）  │  │ │
│  │  └───────────────┘  └─────────────────┘  │ │
│  │            ↑ ptrace 或 KVM 攔截           │ │
│  │  ┌─────────────────────────────────────┐  │ │
│  │  │  sandboxed application              │  │ │
│  │  │  （以為自己在 Linux 上跑）           │  │ │
│  │  └─────────────────────────────────────┘  │ │
│  └──────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────┐ │
│  │  gofer（VFS proxy，負責 host 檔案存取）   │ │
│  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

**sentry**：gVisor 的核心，Go 寫的 userspace kernel。應用程式的 syscall 呼叫被攔截（透過 ptrace 或 KVM-based 的 platform），轉發到 sentry 處理。sentry 實作了足夠 Linux kernel syscall 的 subset（file I/O、network、process management…）。

**gofer**：sentry 不直接碰 host filesystem，而是透過 gofer 行程做 proxy。gofer 有最小的能力集，負責在 host 和 sentry 之間傳遞檔案操作。這個架構的好處是 sentry 本身不需要高特權的 host syscall。

**攔截機制**：gVisor 有兩個 platform 選擇：
- `ptrace` platform：用 ptrace 攔截 syscall，穩定但較慢
- `KVM` platform：在 KVM guest mode 跑 application，VM exit 時 sentry 在 host 模式接管，效能較好

### syscall 攔截機制細節

兩個 platform 的攔截路徑完全不同，理解這個差異有助於理解各自的攻擊面。

**ptrace platform 的攔截流程**：

```
application 執行 syscall 指令
         ↓
PTRACE_SYSCALL 觸發 → sentry 的 tracer goroutine 被喚醒
         ↓
sentry 讀取 application 的暫存器（syscall number、arguments）
         ↓
sentry 的 syscall dispatcher：根據 syscall number 路由到對應的 Go handler
         ↓
Go 實作的 syscall handler 執行邏輯（例如 sys_read 的 Go 實作）
         ↓
若需要 host 資源，sentry 呼叫 host syscall（透過 gofer 或直接）
         ↓
結果寫回 application 的暫存器，PTRACE_CONT 繼續執行
```

這個路徑的代價是：每次 application 的 syscall 都需要**兩次 context switch**（application→sentry，sentry→application），加上 ptrace 的 overhead。效能損耗相當可觀。

**KVM platform 的攔截流程**：

```
application 在 KVM non-root mode（ring 3）執行
         ↓
syscall 指令觸發 VMCALL（或 syscall → VM exit）
         ↓
KVM VM exit → sentry 在 host mode（ring 0 的 host context）接管
         ↓
sentry 的 syscall dispatcher 處理（和 ptrace 路徑相同的 Go handler）
         ↓
sentry 處理完成，VMRESUME 回到 application 的 non-root mode
```

KVM platform 省去了 ptrace 的 user/kernel 來回，VM exit 的 overhead 比 ptrace context switch 小。

**效能差異量化（概略，引用 gVisor 論文的量級，未實測）**：
- ptrace platform：syscall-heavy workload 下，效能可能只有 native 的 20-50%
- KVM platform：效能接近 native 的 ~80%（syscall-heavy workload），memory-bound workload 接近 native
- 現實部署（Cloud Run、GKE Sandbox）主要用 KVM platform

**sentry 實作的 syscall subset**：

Linux x86_64 有約 400+ 個 syscall（`syscall(2)` man page 的完整列表）。當前 gVisor 的 sentry 支援約 200+ 個，覆蓋了大多數應用程式需要的 syscall，但仍有缺口：

- 不支援的 syscall 直接回傳 `ENOSYS`（功能未實作）
- 某些 syscall 有部分支援（只支援特定 flag）
- 某些 syscall 的行為和 Linux 有細微差異（這是兼容性問題的來源）

這個 subset 的邊界本身就是攻擊者的偵測點：一個程式在 gVisor 下因為 `ENOSYS` 而 crash，說明正在 gVisor 環境中執行。攻擊者可以用 syscall fingerprinting 判斷是否需要針對 gVisor 的特定路徑構造 exploit。

### gVisor 的攻擊面

攻擊面從「龐大的 C QEMU」轉移到了「Go 寫的 sentry 和 gofer」：

- **sentry 的 syscall 實作 bug**：這是主要攻擊面。若 sentry 對某個 syscall 的實作有邏輯 bug（例如 `mmap` 的 boundary 計算錯誤），application 可能突破 sentry 拿到 host code exec。Go 是記憶體安全語言，但邏輯 bug 照樣存在。
- **sentry 自己的 host syscall**：sentry 為了自己的運作，仍然呼叫 host kernel 的某些 syscall。若 sentry 自身有 bug 導致可以控制這些 syscall 的 argument，就能直接打 host kernel。
- **gofer 到 sentry 的 IPC 介面**：9P 協定（Plan 9 filesystem protocol）用於 sentry↔gofer 通訊。若 9P 解析有 bug，可能從 sentry 側打到 gofer 或反向。
- **Go runtime 本身**：goroutine scheduler、GC、interface dispatch——Go runtime 有自己的 bug 歷史，雖然數量遠少於 C runtime 問題。

已知的 gVisor 安全問題（請查最新 CVE，以下為概略）：
- sentry 的 `/proc` 實作邊界 bug
- futex 實作的競態條件
- network syscall 的 boundary 問題

### Hyper-V vmbus 一句帶過

微軟的 Hyper-V 用 **vmbus** 作為 guest↔host 通訊的高速通道（代替傳統的 MMIO emulation），device 驅動（synthetic device）在 host 的 VSP（Virtual Service Provider）實作。vmbus 是閉源的 Windows 元件，attack surface 是 VSP 服務的訊息解析。公開 Hyper-V 逃逸研究（如 Nicolas Joly 的 Pwn2Own 成果）主要針對 VSP 的 bug。這塊是另一門課的份量，這裡只標記：vmbus 是 Hyper-V 的 device emulation 等效架構，攻擊面在 VSP 訊息解析，語言是 C++。

## 對比與取捨

```
                   QEMU         Firecracker     gVisor
──────────────────────────────────────────────────────────
語言               C            Rust            Go
記憶體安全         否           是（有 unsafe） 是（有 unsafe）
device 種類        ~300         ~5              0（syscall 模擬）
攻擊面大小（相對） 大           小              中（sentry 程式碼）
啟動時間           ~秒          <125ms          ~秒（視 platform）
隔離機制           KVM VM       KVM VM + jailer syscall 攔截（無 VM）
主要攻擊面         device C code virtio Rust     sentry Go syscall 實作
bug 主要來源       記憶體安全   邏輯 bug        邏輯 bug
真實 CVE 數量      多           少              少（但在增加）
使用場景           通用 VM      Lambda/Fargate  Google Cloud Run/k8s
```

## 踩雷集錦

**「Firecracker 是 Rust 寫的，所以不可能有逃逸 bug」**
→ Rust 排除 memory-safety bug（UAF/堆積溢位/懸空指標），但邏輯 bug（整數溢位繞過長度檢查、狀態機錯誤、virtio ring 的索引邏輯問題）照樣存在。`unsafe` block 也是潛在漏洞源。Rust 讓 exploit 難度大幅提升，但不是零。

**「gVisor 沒有 VM 所以隔離比 Firecracker 弱」**
→ 隔離「深度」不完全等同於 VM 邊界。gVisor 的隔離來自「application 永遠在 sentry 的監督下執行 syscall，永遠不能直接呼叫 host kernel」。只要 sentry 沒有 bug，隔離是嚴格的。問題在於 sentry 本身是否有 bug——這是攻擊面的問題，不是隔離原理的問題。

**「Firecracker 的 jailer 和 QEMU 的 seccomp 一樣」**
→ jailer 更嚴格，因為 device 種類少，允許的 syscall 集合可以更小。QEMU 因為要支援 VNC/SPICE/USB/audio 等大量功能，seccomp 允許的 syscall 不得不多。Firecracker 的 jailer seccomp filter 可以更保守。

**「微VM（microVM）是雲端的終極安全方案」**
→ 對於租戶隔離而言確實大幅改善，但 host kernel 的攻擊面仍然存在（Firecracker/gVisor 都在 host kernel 上跑），且 side-channel 攻擊（Ch 39）是 microVM 也擋不住的。Firecracker 也未聲稱能防禦側信道。

**「gVisor 的 sentry 是完整的 Linux kernel 重寫」**
→ sentry 實作的是 Linux syscall interface 的 subset，不是完整 kernel。某些 syscall 在 gVisor 下根本不支援或行為有差異，這是 gVisor 兼容性問題的來源，也是攻擊者的偵測點（可以從 syscall 差異判斷是否在 gVisor 裡）。

## 進階：再往深一層

**Kata Containers**：另一個雲端隔離方案，用輕量 VM（QEMU 或 Firecracker）跑 OCI container，結合 VM 隔離和 container 工具鏈。攻擊面是 Kata 的 VMM 選擇（QEMU 版就是完整 QEMU，Firecracker 版就是 Firecracker）加上 Kata 的 agent（Go 寫的 guest agent）。

**Rust 的 `unsafe` 審計**：Firecracker 的安全研究重點在找 `unsafe` block 中的問題，以及 safe Rust 中可能導致 panic 然後 DoS、或邏輯 bug 導致 guest 能影響 VMM 行為的地方。[Firecracker 的安全模型文件](https://github.com/firecracker-microvm/firecracker/blob/main/docs/security/threat_containment.md) 值得看。

**gVisor 的 KVM platform 攻擊面**：當 gVisor 用 KVM platform 時，sentry 自身在 KVM 的 non-root mode 和 host mode 之間切換。若攻擊者在 application 層能控制 VM exit 的 reason 或 argument（理論上不應該可以），則有機會影響 sentry 的 host-mode 行為。這是一個理論上更深的攻擊面。

**Firecracker 的威脅模型（threat_containment.md）核心主張**：Firecracker 的官方安全模型文件明確聲明：「Firecracker 假設 guest kernel 是不可信的（untrusted）」。這和 QEMU 的隱性假設根本不同——QEMU 的設計從未假設 guest kernel 是惡意的，許多安全問題正是因為 guest 能藉由 kernel driver 觸發 VMM 的 C 程式碼。

Firecracker 的威脅模型明確包含：
- 惡意的 guest kernel 可以嘗試突破 virtio 介面
- 惡意的 guest userspace 無法直接影響 VMM（必須先突破 guest kernel 隔離）
- jailer 是針對 VMM 本身被攻陷後的防線（defence in depth）

QEMU 的問題在於沒有這個明確的聲明，導致大量 CVE 都是「guest kernel 觸發 VMM C code 的 bug」——按 Firecracker 的框架看，這正是需要防禦的主要威脅，而 QEMU 的 C 程式碼本身就是弱點所在。

**gVisor sandbox escape 研究方向**：針對 sentry 的 syscall fuzzing 是主要研究方法。syzkaller（Linux kernel fuzzer）的原版目標是 Linux kernel，但 gVisor 團隊和獨立研究者已經修改 syzkaller 讓它能 fuzz sentry——用 `runsc`（gVisor 的 OCI runtime）作為 executor，讓 syzkaller 生成的 syscall sequence 在 sentry 內執行，比對 sentry 的行為和 Linux kernel 的差異。

差異有兩類：
1. **sentry crash**（Go panic）：直接的 DoS，可能升級為 escape 的前置條件
2. **行為不一致**：sentry 回傳和 Linux 不同的結果——某些情況下，攻擊者可以利用這個不一致讓 sentry 進入意料外的狀態

這個研究方向相對 QEMU fuzzing（AFL/libFuzzer 打 device emulation）需要更多對 sentry Go 程式碼的理解，但攻擊面更新、公開研究更少，是值得投入的方向。

## 動手練習

1. **閱讀 Firecracker 原始碼**：在 `github.com/firecracker-microvm/firecracker` 找到 `src/vmm/src/devices/virtio/` 目錄，選一個 device（如 `net.rs`），分析 virtio ring 解析邏輯。找出哪些地方做了 boundary check，哪些是 `unwrap()`（panic path）。（理論分析，不需要環境）

2. **理解 jailer seccomp 差異**：讀 Firecracker 原始碼中的 `src/jailer/src/lib.rs`，找到 seccomp filter 的定義，和 QEMU 的 `qemu-seccomp.c` 比較允許 syscall 數量。（理論分析）

3. **gVisor 兼容性測試**：在有 gVisor 的環境（`runsc`），跑一個使用了某個 gVisor 不支援 syscall 的程式，觀察錯誤訊息。理解 sentry 的 syscall subset 邊界。（未實測，需要 gVisor 環境）

4. **unsafe 掃描練習**：clone Firecracker 原始碼後，執行 `grep -rn "unsafe" src/ --include="*.rs" | grep -v "//.*unsafe"` 找出所有非注釋的 unsafe 用法，統計每個 module 的 unsafe 行數，判斷哪個 module 風險最高。（理論分析，預期結果：KVM ioctl 相關的程式碼 unsafe 最多）

## 本章重點整理

- 雲端 microVM 的驅動力是「完整 QEMU 攻擊面太大」
- Firecracker：Rust + 最小 device model + jailer，把攻擊面縮到幾十個 virtio device
- gVisor：Go 寫的 userspace kernel（sentry），攔截 syscall 而非用 VM
- Rust 消除了約 70% 的傳統 C 安全漏洞類別（記憶體安全 bug），但邏輯 bug 和 unsafe block 仍是剩餘攻擊面
- gVisor ptrace platform 每次 syscall 多兩次 context switch；KVM platform 效能接近 native 的 ~80%
- sentry 實作約 200+ syscall（Linux 有 400+），不支援的回傳 ENOSYS，這是 fingerprinting 的偵測點
- Firecracker 的剩餘風險：virtio Rust 程式碼的邏輯 bug + KVM host kernel
- gVisor 的剩餘風險：sentry 的 syscall 實作 bug + sentry 自身的 host syscall
- Firecracker 威脅模型明確假設 guest kernel 不可信，這是和 QEMU 設計哲學的根本差異

## 自我檢核

- [ ] 能說出 Firecracker 的 device model 比完整 QEMU 少了哪些類別的硬體
- [ ] 能解釋 jailer 做了哪些 QEMU `-sandbox on` 做的事（加上更多）
- [ ] 能說明 gVisor sentry 的定位（不是 VM，是 userspace kernel）
- [ ] 能比較 Firecracker 和 gVisor 的剩餘攻擊面來源
- [ ] 知道 Rust 記憶體安全能消除哪類 bug，不能消除哪類
- [ ] 能說出 gVisor ptrace platform 和 KVM platform 攔截路徑的差異
- [ ] 能解釋為什麼 Firecracker 的威脅模型把 guest kernel 列為不可信方

## 延伸閱讀

1. **Firecracker 官方設計文件**（`github.com/firecracker-microvm/firecracker/blob/main/docs/design.md`）
   - 架構、設計決策、安全模型的官方說明。學什麼：為什麼這樣設計，設計者的威脅模型是什麼。

2. **「My VM is Lighter (and Safer) than your Container」（Madhavapeddy et al., SOSP 2017）**
   - gVisor 和 microVM 設計的學術背景，說明為什麼 container 的隔離不夠。學什麼：雲端隔離的威脅模型演進。

3. **gVisor 官方文件 — Architecture Guide**（`gvisor.dev/docs/architecture_guide/`）
   - sentry、gofer、platform 的架構圖和設計原理。學什麼：syscall 攔截路徑和攻擊面的定義。

4. **「Rust Unsafe Code Guidelines Reference」**（`rust-lang.github.io/unsafe-code-guidelines/`）
   - 理解 Rust unsafe block 的實際危險範圍。學什麼：審計 Firecracker unsafe block 時需要的背景。

5. **「Inside Firecracker」（AWS blog，2018）**
   - AWS 工程師介紹 Firecracker 的動機與設計。學什麼：實際雲端場景的威脅模型，以及 jailer 的設計思路。

6. **「gVisor: A Container Sandbox」（Google Security Blog）**
   - gVisor 設計者的第一手說明，含 sentry 和 gofer 的安全邊界定義。學什麼：gVisor 的攻擊面如何被設計者自己定義。

7. **「Microsoft: 70% of All Security Issues Are Memory Safety Issues」（MSRC，2019）**
   - 量化記憶體安全問題在真實漏洞中的比例，是選擇 Rust/Go 的數據基礎。學什麼：為什麼記憶體安全語言的選擇不是美學偏好，是工程決策。

---

→ [Ch 39 — side-channel / CPU bug 對虛擬化的衝擊：L1TF/MDS/Spectre](./39-side-channels.md)
