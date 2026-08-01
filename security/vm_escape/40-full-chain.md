# Ch 40 — 完整 chain 全景：guest → VM escape → host kernel LPE

> **目標**：把整門課學的東西放進一條完整的雲端攻擊鏈，看清楚每一環在哪裡、用什麼能力連接、以及你目前站在哪、接下來去哪。

## 為什麼需要這個？

我們學了 40 章，每一章都是鏈上的一個環。但「知道每個環怎麼製造」和「知道這條鏈在真實世界怎麼接起來」是不同的能力。

真實的攻擊鏈從不是單獨一個洞，而是一串洞的組合：renderer bug、kernel LPE、hypervisor escape、host LPE——四個洞，每一個都是業界頂尖研究者花數週找到的。理解這條鏈的全景，你才能：
- 評估一個洞的真實 impact（「這個 VM escape 在真實部署裡能做到什麼？」）
- 在防禦者視角知道封哪個環最有效
- 規劃自己的研究方向，知道學完本課還差哪幾步

## 先建立直覺

```
攻擊者起點：能執行程式碼（可能是 JavaScript、容器 workload、VM 中的 process）

└─ 環 1：guest userland exploit（renderer RCE 或 workload RCE）
   ↓  技術：heap pwn、JIT type confusion（browser_pwn 的份量）
   ↓  成果：guest userland code exec（可能是 renderer sandbox 裡）

└─ 環 2：guest sandbox escape / guest kernel LPE
   ↓  技術：kernel UAF/OOB、seccomp bypass（kernel_pwn + binary_exploitation 的份量）
   ↓  成果：guest root / guest kernel code exec

└─ 環 3：VM escape（本課的份量）
   ↓  技術：device emulation bug、MMIO/DMA 原語、heap overflow、function pointer 劫持
   ↓  成果：QEMU 行程 code exec（host 上的一個非 root 行程）

└─ 環 4：繞 host sandbox（Ch 37 的份量）
   ↓  技術：seccomp bypass（orw）、sVirt 邊界探索
   ↓  成果：在 seccomp/sVirt 限制下的有效 impact（讀機密 / 為下一環鋪路）

└─ 環 5：host kernel LPE（kernel_pwn 的份量，再一次）
   ↓  技術：KVM ioctl bug、host kernel UAF/OOB、QEMU 行程的 privilege 提升
   ↓  成果：host root

└─ 最終：控制實體機、存取所有 VM 的資料、橫向移動到其他租戶
```

注意環 2 和環 5 用的是「同一門課」的技術（kernel_pwn），只是目標不同：一個打 guest kernel，一個打 host kernel。

### 技術轉換細節圖（Pwn2Own 場景）

把上面的文字圖翻譯成每一步的技術核心：

```
┌──────────────────────────────────────────────────────────────────────┐
│  完整雲端攻擊鏈（Pwn2Own browser → VM → host 場景）                  │
│                                                                      │
│  [browser renderer process]                                          │
│       ↓ JIT type confusion（V8 TurboFan）/ heap OOB                  │
│       ↓ → arbitrary R/W in renderer address space                    │
│  [renderer sandbox（seccomp + namespace 隔離）]                       │
│       ↓ broker IPC bug / seccomp filter bypass                       │
│       ↓ → 跳出 renderer sandbox，進入 guest userland                  │
│  [guest userland（非 root）]                                          │
│       ↓ kernel UAF/OOB（UAF via msg_msg / userfaultfd spray）         │
│       ↓ → modprobe_path / TOCTOU → guest root                        │
│  [guest kernel / guest root]                                         │
│       ↓ 直接觸發 MMIO/PIO write（outl / writel to device MMIO）       │
│       ↓ → KVM VMEXIT → QEMU device .write callback OOB              │
│       ↓ → heap overflow → function pointer 劫持 → ROP chain          │
│  [QEMU process（host non-root，uid=qemu 或 uid=107）]                 │
│       ↓ orw seccomp bypass：open(/etc/passwd) + read + sendto        │
│       ↓ fd 枚舉：/proc/self/fd → 其他 VM 的 disk image fd            │
│  [limited host access：可讀機密、可探索 host 資源]                    │
│       ↓ KVM ioctl bug（/dev/kvm 的 fd 在 QEMU 手中）                 │
│       ↓ 或 host kernel UAF（pipe / io_uring / page cache）           │
│       ↓ → kernel cred 結構覆寫 → uid=0                               │
│  [host kernel root]                                                  │
│       ↓ 讀 /dev/kvmX 的所有 VM 記憶體快照                             │
│       ↓ 存取 /var/lib/libvirt/images/*.qcow2 磁碟映像                │
│       ↓ 橫向移動：打 host 的 SSH daemon / management interface        │
│  [完整雲端控制：同一實體機上所有 VM 資料 + 進一步橫向]                 │
└──────────────────────────────────────────────────────────────────────┘
```

兩個關鍵轉換點值得特別記住：

**轉換點 A（環 2 → 環 3）**：guest root → MMIO write 觸發 device bug。這裡的關鍵是「你需要 guest root 才能直接 outl 到 MMIO 地址」——部分 device 的 MMIO 會在 guest kernel 驅動層被呼叫，但攻擊者需要能構造惡意的 I/O pattern，通常意味著 guest root 或至少 CAP_SYS_RAWIO。

**轉換點 B（環 3 → 環 4/5）**：QEMU process code exec → host 資源存取。這一步的空間完全由 seccomp policy 的鬆緊決定。沒有 seccomp：直接 `execve("/bin/sh")`，遊戲結束。有 seccomp：你的 syscall 被限制在 QEMU 的白名單裡，但 `read`/`write`/`open`/`sendto` 通常不在封鎖名單中，所以 orw 仍然有效。

## 底層機制：每一環的細節

### 環 1：guest userland RCE

在「cloud function / Kubernetes pod / Lambda」的場景，攻擊者的起點通常是：
- **上傳惡意 workload**（若攻擊者是惡意租戶）：直接在 guest 裡跑任意 code，不需要 RCE
- **打 guest 裡的服務**：web server、API endpoint 有 bug → 遠端 code exec
- **browser context（Pwn2Own 場景）**：renderer RCE，但還在 renderer sandbox 裡

若是惡意租戶直接控制 VM，環 1 自動跳過——攻擊者就是 guest OS，直接進環 2 或環 3。

**對應課程與具體章節**：
- `security/browser_pwn`（renderer RCE）：Ch 5–8（V8 物件模型 + Pointer Compression）、Ch 9–14（TurboFan type confusion 的構造與利用）、Ch 15–18（JIT 噴射 shellcode → 任意讀寫原語）。這些章節的輸出是「renderer process 裡的 arbitrary R/W」，正是環 1 的終點。
- `security/binary_exploitation`（服務 RCE）：Ch 18–25（glibc heap pwn：tcache poison / unsorted bin attack）、Ch 10–12（格式字串漏洞 / 棧溢位基礎）。這些技術打的是 guest 裡的 userspace service。

### 環 2：guest kernel LPE

打穿 guest kernel，原因：
- 多數 VM escape bug 需要在 guest 裡有特權（root 或 CAP_SYS_RAWIO）才能存取 MMIO/PIO
- 某些 MMIO 存取需要 `/dev/mem` 或直接 physical address mapping
- guest kernel LPE 本身有時就是逃逸的準備步驟

技術和 `security/kernel_pwn` 一模一樣，差別在攻擊目標是 guest kernel 而非 host kernel。guest kernel 可能版本老舊（cloud instance 不總是及時更新）、或為攻擊面選擇的定制 kernel（如舊版 Android kernel）。

**對應課程與具體章節**：
- `security/kernel_pwn` Ch 8–12（UAF 利用：msg_msg spray、userfaultfd、FUSE 配合 UAF 觸發）
- Ch 13–16（heap grooming + cross-cache overflow → slab 物件替換）
- Ch 18–20（modprobe_path / dirty pagetable 類型的 privilege 提升）

注意：guest kernel LPE 的技術和 host kernel LPE 完全相同，只是目標 kernel 的版本和配置不同。guest kernel 往往比 host kernel 老（cloud provider 對 host kernel 更新比對 guest image 積極），有時反而更容易找到未修補的洞。

### 環 3：VM escape（本課核心）

Ch 9–35 都在這一環。三條主路：

**路線 A：device emulation bug**（主線，Ch 16–24）
```
guest 驅動程式（guest kernel）
  → 觸發 MMIO/PIO write（`outl`/`writel`）
  → KVM VMEXIT → QEMU device callback
  → device 的 .write 函式有 OOB/UAF
  → heap overflow → function pointer 劫持 → ROP
  → host code exec（QEMU 行程）
```

**路線 B：virtio bug**（Ch 25–28）
```
guest 驅動程式
  → 構造惡意 virtio descriptor chain（長度/地址越界）
  → QEMU virtio backend 解析時 OOB
  → 同上
```

**路線 C：VMware/VirtualBox specific**（Ch 29–35）
```
guest 透過 backdoor 機制（VMware RPCI / VirtualBox SharedFolders）
  → VMX process / VBoxSvc 的 RPC handler 有 bug
  → 同上
```

**對應課程與具體章節**：
- vm_escape Ch 9–15：QEMU 內部架構、MMIO/PIO 的實現方式、VMEXIT 的行程流
- vm_escape Ch 16–24：具體 device bug 分析（FDC、AC97、e1000）、heap overflow → function pointer 劫持
- vm_escape Ch 25–28：virtio 協定、descriptor chain 越界、vhost-net 的 kernel-side 攻擊面
- vm_escape Ch 29–35：VMware RPCI 協定逆向、VirtualBox SharedFolders 的 RPC parsing

### 環 4：繞 host sandbox

環 3 只給你 QEMU 行程的 code exec，但：
- seccomp 封住了 `execve`（Ch 36/37）
- sVirt 限制了你能存取的 host 資源
- DAC 確保你是非 root 使用者

繞 seccomp：orw 讀機密、網路外洩、fd 枚舉（Ch 37）。
繞 sVirt：找 policy 允許的 channel、或用 SELinux policy 的洞（較難）。

很多「VM escape」的 bug bounty / Pwn2Own 在這裡就停了——拿到 QEMU shell、讀到機密資料，已足夠說明 impact。

**對應課程與具體章節**：
- vm_escape Ch 36：QEMU 防禦層完整分析（seccomp filter 的實際內容、sVirt label 的 SELinux context、namespace 隔離）
- vm_escape Ch 37：seccomp bypass 技法——orw 原語、`sendmsg` 外洩、fd 枚舉（`/proc/self/fd` → 找到其他 VM 的 disk image fd）

Ch 37 的 fd 枚舉技術特別值得注意：QEMU 行程持有所有它管理的 VM disk image 的打開 fd，若用 `readlink("/proc/self/fd/N")` 枚舉所有 fd，你能看到 disk image 的路徑，然後直接 `read` 它的內容——完全不需要 host root，只靠 orw。

### 環 5：host kernel LPE

從 `qemu` 使用者拿到 host root。技術和 `security/kernel_pwn` 一樣：

- **KVM ioctl bug**：QEMU 行程有 `/dev/kvm` 的 fd，呼叫 KVM ioctl 是被允許的。若 host kernel KVM 驅動有 bug，從 QEMU 行程打比從 guest 打更近（不用穿過 VMX non-root 邊界）。
- **host kernel UAF / heap spray**：如 page cache、pipe、io_uring 相關的漏洞，從 `qemu` 使用者觸發（許多 kernel LPE 不需要 root 起點）。
- **dirty pipe / dirty cow 類**：file-backed memory map 的 privilege escalation 類漏洞，在非 root 使用者下可觸發。

**對應課程與具體章節**：`security/kernel_pwn`（相同技術，目標是 host kernel）

特別的額外入口：KVM ioctl 是環 5 特有的攻擊面，在純 kernel_pwn 課程裡不會特別提。從 QEMU 行程出發，你能合法呼叫 `KVM_SET_USER_MEMORY_REGION`、`KVM_CREATE_VCPU` 等 ioctl——如果 host kernel 的 KVM 驅動在處理這些 ioctl 時有邊界檢查漏洞，從 QEMU 行程觸發比從普通用戶觸發更直接，因為 QEMU 天然就有 `/dev/kvm` 的 fd 且可以觸發這些路徑。

## 真實案例：Pwn2Own 與雲端逃逸

### Pwn2Own Virtualization Category

Pwn2Own（每年 CanSecWest/Pwn2Own 舉辦）有專門的 Virtualization 類別，歷年重要成果（部分，未完整核查所有細節，請查官方 Pwn2Own 紀錄）：

**2017 Pwn2Own（VMware Workstation escape）**：fluoroacetate（Amat Cama + Richard Zhu）在 VMware Workstation 上完成逃逸，使用 VMware SVGA / display 的 bug。細節在 Ch 34/35。

**2019 Pwn2Own（Oracle VirtualBox escape）**：使用 VirtualBox 的 E1000 device bug（整數溢位導致 OOB），搭配 Windows LPE 完成 full chain。

**2021 Pwn2Own（VMware vCenter、ESXi）**：多個針對 VMware 的 full chain，部分組合了 VMware 的 VMRC（remote console）bug。這一年有一個特別值得注意的技術細節：

ESXi full chain 流程（公開技術概要，非完整細節）：
```
guest VM 中的惡意程式
  → 利用 ESXi 的 SVGA 裝置（軟體 GPU 模擬）中的記憶體混淆 bug
  → 寫入 VMkernel（ESXi 的核心）的記憶體
  → 直接取得 VMkernel ring-0 code exec
  → 因為 ESXi 沒有 seccomp / userspace QEMU 這一層
    → 環 3 直接等於 host root（沒有環 4）
  → 存取所有 guest VM 的記憶體、磁碟映像
```

ESXi 和 QEMU/KVM 的關鍵差異：ESXi 的 device driver 直接跑在 VMkernel（Type-1 hypervisor 的 kernel 空間），不像 QEMU 是 host userspace 的行程。這代表 ESXi 沒有「環 4」——一旦 device bug 被利用，你直接在 VMkernel 裡執行程式碼，沒有 seccomp、沒有 DAC 隔離、直接是 ring-0。逃逸的 impact 更直接，但逆向難度也更高（閉源的 VMkernel）。

**Pwn2Own Austin 2021（QEMU KVM）**：有針對 KVM/QEMU 堆疊的 entry 出現，展示攻擊面轉向 open source hypervisor。

**2023–2024 Pwn2Own 虛擬化類別動態**：QEMU/KVM 在這幾年的 Pwn2Own 中持續出現為攻擊目標，部分原因是開源讓逆向障礙消失（不需要對著二進位逆向，直接審計 C 原始碼），但 fuzzing 和程式碼審計的競爭也更激烈。Pwn2Own 的規則近年也擴展到雲端基礎設施類別（含 VMware vSphere、Oracle VirtualBox、QEMU/KVM），獎金最高的 full chain（guest to host root）通常在 15–25 萬美元級別。StarLabs SG 等亞太研究團隊在這幾年的虛擬化類別中有顯著表現，公開了多篇 VMware 相關技術 writeup。

**Pwn2Own 的可靠性要求**：這是 Pwn2Own 最特別、最被低估的壓力——**同一個 exploit 需要在比賽現場穩定重現，通常要求三次嘗試中至少一次成功，某些規則更嚴格**。這排除了靠 heap spray 碰運氣的 exploit，要求你對目標的 heap 佈局有精確理解。現場跑的環境（目標的 OS 版本、patch 等級）提前公佈，但你沒辦法事先在完全相同的機器上調試——你能準備，但要在比賽當天那台機器上跑通，這就是為什麼 Pwn2Own 的參賽者需要對目標有遠超「一般 exploit 開發」程度的理解。

### Pwn2Own 的 bug collision 問題

多個隊伍可能獨立找到同一個漏洞。Pwn2Own 的規則是：**先提交的隊伍算分，後提交的即使有相同漏洞也得不到分數**（且那個洞會被公開修補）。

這意味著 0-day 研究有強烈的競爭性和時間壓力：在比賽前 48 小時，主辦方 ZDI（Zero Day Initiative）凍結 entry。若你在比賽前 72 小時才完成 exploit，準備提交，但另一個隊伍已在前一天提交同一個洞，你拿零分。這是真實世界 0-day 研究的競爭面——找到洞只是第一步，要比別人快、比別人先公開，才算真正「有效」的研究成果（從 bug bounty / Pwn2Own 的激勵角度看）。

### 雲端逃逸事件（已公開）

**VENOM（CVE-2015-3456）**：最著名的 QEMU 逃逸 CVE（Ch 23 復刻）。FDC OOB write。公開時，CrowdStrike 的分析指出在有 sVirt 的環境實際影響受限——但對沒有 sVirt 的部署（Ubuntu/Debian host）是完整逃逸。影響包括 Xen、KVM、VirtualBox 平台上的 QEMU。

**CVE-2019-14835（vhost-net buffer overflow）**：KVM vhost-net 裡的 guest-to-host buffer overflow，在 live migration 場景下觸發。影響 Red Hat Enterprise Linux 和 centos 7/8 的 KVM 部署。這個洞不需要 guest root，網路 packet 就能觸發。

**Azurescape（2021，Azure Container Instances）**：Palo Alto Networks 研究員發現的 cross-tenant 容器逃逸。利用 runC 的漏洞逃逸容器，然後透過 Kubernetes 的 API server 橫向移動到其他租戶的容器。技術上是 container escape + K8s privilege escalation，不是 hypervisor escape，但展示了「雲端多租戶隔離的整條鏈」。

**Spectre/L1TF（2018，雲端廣泛影響）**：不是單一 CVE，是一類硬體問題的雲端影響。對 AWS/GCP/Azure 都造成緊急 patch + 效能影響。沒有公開的「利用 Spectre 在真實 AWS 上讀到別的租戶資料」的 PoC（這類研究通常不公開），但理論威脅模型被完整確認。

## 你目前站在哪、接下來去哪

```
你學完本課後的能力地圖：

  ✓ guest userland      ← binary_exploitation
  ✓ guest kernel LPE    ← kernel_pwn
  ✓ VM escape           ← 本課（vm_escape）
  ✓ host sandbox bypass ← 本課（Ch 37）
  ○ host kernel LPE     ← kernel_pwn（再一次，目標不同）
  ○ 雲端橫向移動        ← 這是另一個主題（cloud attack paths）

  ✓ side-channel 威脅模型 ← 本課（Ch 39）
  ○ side-channel exploit 細節 ← 這是側信道專題的份量
```

**下一步的學習路徑**：

1. **Project Zero blog**（`googleprojectzero.blogspot.com`）：每一篇 hypervisor/VMM 相關文章都是你能力的試金石。如果學完本課後讀 P0 的 QEMU/VMware writeup 感覺順暢，你的基礎已到位。

   具體推薦閱讀：
   - 搜尋 `"QEMU" site:googleprojectzero.blogspot.com`——P0 有幾篇針對 QEMU device 的完整分析，包括 virtio 和 USB 裝置的 bug。這些文章的分析深度（從 source code 定位到 exploit primitive 到最終 ROP chain）是你驗證自己是否真正理解本課的試金石。
   - 搜尋 `"VMware" site:googleprojectzero.blogspot.com`——P0 的 VMware 研究（特別是 Tavis Ormandy 的系列）展示了閉源 hypervisor 的逆向方法：如何從 binary 還原協定格式、如何找 RPC handler 的邊界問題。
   - P0 的 `"hypervisor"` 標籤文章——涵蓋 Hyper-V（Microsoft）的研究，展示了同一套 device emulation 攻擊思路在不同 hypervisor 上的變形。

2. **真實 full chain writeup**：搜尋「Pwn2Own virtualization writeup」、「KVM escape writeup」、「QEMU CVE exploit」。

   具體推薦來源：
   - **StarLabs SG blog**（`starlabs.sg/blog`）：多篇 VMware Workstation 和 ESXi 的 Pwn2Own 相關技術文章，分析細緻且有完整的 exploit 開發流程描述。
   - **Numen Cyber Labs**（`numencyber.com/research`）：有 QEMU 裝置 bug 的詳細 writeup，包括 CVE 的從 bug 到 exploit 全程。
   - **Exodus Intelligence blog**（`blog.exodusintel.com`）：偶有 hypervisor 相關的深度分析，通常在 CVE 修補後若干個月公開。
   - **GitHub 搜尋 `pwn2own vmware exploit`**：有部分參賽者在比賽後公開了 PoC 或完整 exploit 代碼，特別是目標已修補後。

3. **CTF VM escape 題型**：每年 DEF CON / PlaidCTF / 各大賽都有 QEMU custom device 逃逸題。

   找題的具體方法：
   - CTF time（`ctftime.org`）搜尋 "qemu" 或 "vm escape"，找近三年的題目列表
   - QEMU custom device 題的典型 pattern：主辦方提供一個帶有惡意 MMIO device 的自訂 QEMU binary（通常叫 `qemu-system-x86_64-custom`）和對應的 device 原始碼（或只給 binary 讓你逆向），flag 在 host 的某個位置，你需要從 guest 裡跑 exploit 取得它
   - DEF CON CTF（每年 8 月）和 Dragon CTF（波蘭 CTF 社群，擅長這類題）是這類題目的高頻出現場所
   - 自己先嘗試 24h，再看 writeup——這個順序很關鍵。看 writeup 前先被卡住一陣子，你才能從 writeup 中學到「為什麼我沒想到這個」，而不只是「哦原來是這樣」。

4. **挖真實洞**：選一個 QEMU 的 device，用 Ch 3 教的攻擊面分析方法系統性審計。

   具體的低懸果實起點：
   - **`hw/audio/` 下的音訊 device**（AC97、Intel HDA）：音訊裝置的 DMA 緩衝區管理邏輯相對複雜，歷史上有 CVE，但覆蓋程度不如網路裝置。用 `git log --oneline hw/audio/` 看最近 3 年的修補，找有 "fix" + "buffer" 或 "bounds" 的 commit 倒推是否有漏掉的邊界案例。
   - **`hw/usb/` 下的 USB 裝置模擬**：USB 的 descriptor 解析和 bulk transfer 邏輯有多個歷史 CVE，且還在持續被研究（部分 device 的 handler 很老）。
   - **AFL++ fuzzing QEMU device 的環境設置**（未實測，理論預期）：
     ```bash
     # 用 QEMU 的 qtest 框架搭配 AFL++
     # qtest 讓你不需要啟動完整 guest OS 就能直接 fuzz device 的 MMIO/PIO 介面
     git clone https://github.com/AFLplusplus/AFLplusplus
     # 使用 qtest harness：hw/*/tests/*.c 是範例
     # 基本概念：AFL++ 生成 qtest 命令序列 → QEMU qtest backend 執行 → 觸發 device 的 MMIO handler
     afl-fuzz -i seeds/ -o findings/ -- ./qemu-system-x86_64 -qtest stdio -nographic
     ```
     實際的 fuzzing harness 需要針對特定 device 寫 qtest command 生成器，但框架本身是 QEMU upstream 支援的。

5. **kernel_pwn 整合練習**：選一個 KVM ioctl，用 kernel_pwn 的技術審計它有沒有問題。VM escape + host kernel LPE 的 full chain 就在這裡接上。

   具體起點：
   - **`KVM_SET_USER_MEMORY_REGION` ioctl**：負責把 guest physical address 映射到 host virtual address。歷史上這個 ioctl 的邊界檢查出過問題（guest PA 範圍的 overlap 檢查、flags 的有效性驗證）。在 `virt/kvm/kvm_main.c` 的 `kvm_vm_ioctl_set_memory_region` 函式附近審計，看是否有 TOCTOU 或整數溢位類的遺留問題。
   - **`KVM_CREATE_VCPU` ioctl**：每個 VCPU 的計數器有上限（`KVM_MAX_VCPUS`），若計數器的邊界檢查有整數問題，可能構造 out-of-bounds 的 vcpu 陣列存取。雖然這類低垂果實通常已被修補，但閱讀這段程式碼本身（`arch/x86/kvm/x86.c` 的 `kvm_arch_vcpu_create`）是理解 KVM ioctl 流的最佳起點。
   - 更系統性的方法：用 `syzkaller`（Google 的 kernel fuzzer）針對 `/dev/kvm` 介面 fuzz，syzkaller 內建了 KVM ioctl 的語法描述（`sys/linux/dev_kvm.txt`），可以直接跑。

## 對比與取捨

| 攻擊路徑 | 技術難度 | 依賴條件 | 在雲端的 impact |
|---|---|---|---|
| Device emulation bug 逃逸 | 高（heap pwn + QEMU 內部）| guest root | 控制 QEMU 行程 |
| VirtIO 越界逃逸 | 高 | guest root（多數）| 同上 |
| 側信道（L1TF）| 高（需精確計時）| guest code exec | 讀 host L1 cache |
| container escape + K8s LPE | 中-高 | container code exec | 跨租戶橫向 |
| 完整 full chain（5 環）| 極高 | guest 遠端 RCE | host root + 跨租戶 |

## 踩雷集錦

**「學會 VM escape 就能隨便打雲端」**
→ VM escape 只是環 3。沒有環 1（guest 起點）和環 5（host LPE），你在生產雲端的 impact 很有限。真實的雲端攻擊需要完整的鏈，每一環都是獨立的研究工作。

**「Pwn2Own 的洞都是很複雜的 0-day，普通研究者做不到」**
→ Pwn2Own 的參賽者用的技術和本課教的沒有本質差別：heap grooming、function pointer 劫持、ROP。差別在目標（閉源商業軟體）和洞的發現（需要逆向工程）。技術本身你學完本課就有，缺的是在閉源目標上做逆向和 fuzzing 的經驗。

**「full chain 太長，我應該專注在一個環就好」**
→ 某種程度上正確：Pwn2Own 的組隊方式往往是每個人負責一環。但你至少要理解全鏈的每一環在幹什麼，才能評估「你的那個環在整個鏈中的位置」。不理解全鏈，你會低估也會高估自己那個環的 impact。

**「有 sVirt/seccomp 就等於 VM escape 無害」**
→ sVirt 和 seccomp 大幅縮小 impact，但不是零。讀到 VM 磁碟映像內容、觸發 host kernel LPE（若有合適的 bug）、或用 orw 讀到 `/etc/passwd`，在 sVirt+seccomp 的環境都仍可能做到（取決於具體配置和 kernel 版本）。

**「學側信道不如學 heap pwn，因為側信道沒辦法用在 CTF」**
→ 側信道在 CTF 裡確實少，但在真實世界研究裡，L1TF 和 MDS 是改變雲端安全模型的漏洞類別。理解側信道讓你能評估「我的 VM escape exploit 在什麼 hardware / microcode 組合下有副作用」，也讓你在做安全評估時考慮更完整的攻擊面。

## 進階：再往深一層

**ESXi 的攻擊面**：VMware ESXi（裸機 hypervisor）的架構與 QEMU/KVM 不同——ESXi 有自己的 VMkernel，device 驅動直接在 VMkernel 裡跑，不是 userspace 行程。逃逸 impact 更直接（沒有 seccomp 這層），但逆向難度更高。ESXi 是雲端 Pwn2Own 的熱門目標（VMware vSphere）。

**雲端 hypervisor 的 speculative store bypass（SSBS/SSBD）**：ARM 的 SSBS bit 可以讓 guest 控制自己的推測執行範圍，是 ARM 版的側信道緩解旋鈕。ARM64 主機（AWS Graviton、Ampere 等）的側信道威脅模型和 x86 有差異，是值得研究的新領域。

**TPM 虛擬化（vTPM）**：現代 guest 有 virtual TPM（可信賴平台模組），host 上通常是 swtpm（軟體 TPM）。若 vTPM 的 host-side daemon 有 bug，攻擊面從 device emulation 延伸到了 TPM 協定解析。

**拿到 host root 之後：雲端橫向移動**：環 5 結束時你有 host root，但「打整個雲」還需要更多步驟。在 KVM host 上拿到 root 後，通常的橫向路徑是：

- **讀其他 VM 的記憶體**：透過 `/proc/N/mem`（若 QEMU 行程可以存取）或直接操作 KVM 的 `KVM_GET_REGS` / `KVM_TRANSLATE` ioctl，可以從 host root 讀到任意 guest 的記憶體（包括記憶體中的加密密鑰、session token）。這是多租戶威脅模型的核心。
- **存取 VM 磁碟映像**：`/var/lib/libvirt/images/*.qcow2` 或類似路徑直接掛載，離線讀取 guest 的 filesystem。不需要 guest 的 OS 運行。
- **打 cloud management plane**：host 上通常有 libvirt daemon、OpenStack compute agent、AWS/GCP 的 metadata service agent。這些 daemon 以更高的 privilege 運行，且有存取 cloud control plane 的憑證。從 host root → 打這些 daemon → 取得 cloud API key → 控制整個帳號的 VM fleet，這才是雲端攻擊的終點。

**bug collision 的防禦面**：若你是防禦者，Pwn2Own 的 bug collision 現象說明了一件事——同一個洞往往被多個研究者獨立找到，且時間相近。這表示若一個 hypervisor bug 沒有快速修補（在比賽後的 90 天 disclosure window 內），已有多個團隊掌握 exploit。patch 速度是防禦者的關鍵指標，不是「有沒有 bug」，而是「bug 被找到後多快修好」。

## 動手練習

1. **畫你自己的攻擊鏈地圖**：選一個場景（AWS Lambda / Google Cloud Run / on-prem KVM），把五個環填入你認為最可能的技術（哪個 device bug、哪種 kernel LPE、什麼 seccomp bypass 策略）。這是整合理解，不需要實際跑。

2. **讀一篇真實 full chain writeup**：找 Pwn2Own 歷年 virtualization 類別的公開 writeup（搜尋 `site:github.com pwn2own vmware virtualbox writeup`），對照本課的章節標出「這一步對應 Ch N 的哪個概念」。

3. **評估真實環境的防禦層**：在你能存取的 KVM 主機上，確認：(a) QEMU 是否有 `-sandbox on`；(b) 是否有 sVirt label（`ps auxZ | grep qemu`）；(c) `/sys/devices/system/cpu/vulnerabilities/l1tf` 的狀態。這是防禦者視角的全鏈評估。

## 本章重點整理

- 完整雲端攻擊鏈：guest RCE → guest LPE → VM escape → 繞 sandbox → host LPE → host root
- 每一環對應一門課：binary_exploitation + kernel_pwn + vm_escape（本課）+ kernel_pwn（再一次）
- 真實 Pwn2Own full chain 用的技術和本課教的沒有本質差別，差的是閉源目標的逆向和 fuzzing 經驗
- VM escape 在有 sVirt/seccomp 的環境不等於 host root，但也不等於無害
- 側信道（L1TF/MDS）是「不需要 device bug」的另一條跨 VM 洩漏路線
- ESXi 的 device bug → VMkernel code exec，比 QEMU/KVM 少了一層 seccomp 隔離，impact 更直接
- host root 之後的橫向移動路徑：讀他人 VM 記憶體 → 存取磁碟映像 → 打 cloud management plane

## 自我檢核

- [ ] 能畫出五環攻擊鏈，說出每環的技術和對應課程的具體章節
- [ ] 能說出 VENOM（CVE-2015-3456）在有/無 sVirt 環境的不同 impact
- [ ] 能解釋「VM escape + orw seccomp bypass」在沒有 host LPE 的情況下的實際 impact
- [ ] 知道 Pwn2Own Virtualization 類別的評分基準（需要 guest→host 完整 chain）
- [ ] 能說出 ESXi full chain 和 QEMU/KVM full chain 在環 4 上的根本差異
- [ ] 能說出學完本課後的三個具體下一步，各自對應哪個技術缺口
- [ ] 能解釋 Pwn2Own bug collision 的規則，以及它對 0-day 研究節奏的影響

## 延伸閱讀

1. **Google Project Zero — Hypervisor 相關 writeup**（`googleprojectzero.blogspot.com`，搜尋 "hypervisor" 或 "QEMU" 或 "VMware"）
   - 最高品質的 hypervisor 安全研究。學完本課應能讀懂這裡的每一篇。學什麼：如何把技術翻譯成真實攻擊鏈。

2. **Pwn2Own 歷年 virtualization 結果**（`zerodayinitiative.com/blog`，搜尋 "Pwn2Own virtualization"）
   - 歷年 Pwn2Own 虛擬化類別的結果公告（通常有簡短技術說明）。學什麼：當前最強攻擊者用什麼技術、哪些目標被選中。

3. **「Exploiting the DRAM rowhammer bug to gain kernel privileges」（Seaborn & Dullien, Project Zero 2015）**
   - 硬體 bug → 軟體 privilege 的經典案例。與 L1TF 的思路有共鳴：硬體假設被打破 → 軟體隔離失效。學什麼：思考「硬體不保證 memory isolation」的安全含義。

4. **VUsec RIDL / MDS 研究網站**（`mdsattacks.com`）
   - MDS 家族漏洞的統一說明頁，有 PoC 連結和各緩解措施的說明。學什麼：MDS 的完整威脅模型，以及不同 CPU 代的受影響程度。

5. **Phrack Issue 70+，VM escape 相關文章**（`phrack.org`）
   - 技術深度最高的地下刊物，有多篇 QEMU/KVM 相關文章。學什麼：把本課的技術往更深處推的地方。

---

→ [Final Project — 從真實 CVE 到完整 guest→host 逃逸 exploit](./final-project-cve-to-escape.md)
