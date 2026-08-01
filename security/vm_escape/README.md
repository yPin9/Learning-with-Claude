# VM Escape Pwn 學習筆記：從 guest 內部打穿 hypervisor

> 給 kernel / heap pwn 已經熟練（穩定解 kernel UAF、寫得動 ROP、會 heap groom）、想把 pwn 天梯再推一階到虛擬化邊界的人。接在 `security/binary_exploitation` + `security/kernel_pwn` 之後。

這門課打的目標是**虛擬機的那道牆**：你人在 guest 裡（可能已經是 guest root），要打穿模擬你這台機器的那個 host 端行程，在 host 上執行程式碼。主線是 **QEMU/KVM**——開源、CTF 與研究界的絕對主流；旁支帶 **VirtualBox**（開源、經典入門逃逸）和 **VMware Workstation**（閉源、Pwn2Own 常客）。

好消息是：**VM escape 的利用階段幾乎就是 userspace pwn**。QEMU 是一個跑在 host 上的普通 Linux 行程，你在它的 heap 上製造 overflow / UAF、洩漏位址、劫持 function pointer、串 ROP——這些你在 `binary_exploitation` 練到爛。真正新的東西只有三件：**(1) guest↔host 的邊界怎麼跨**、**(2) device emulation 這個攻擊面怎麼運作**、**(3) MMIO / DMA 怎麼被當成任意讀寫原語的來源**。這門課就是把這三件事講到你能自己挖、自己打。

課程刻意含一段**硬體虛擬化原理補強**（VT-x / EPT / KVM ioctl）。不是要你會寫 hypervisor，是要你別把 QEMU 當黑箱打——當你知道一次 MMIO 存取是怎麼從 guest 觸發 `VMEXIT`、經 KVM 丟回 userspace、最後落到某個 device 的 `.write` callback，你在挖洞時看到的就不再是「一個神秘的 C 函式」，而是「攻擊面上一個有明確觸發路徑的點」。

## 為什麼學這個？

- **它是 pwn 天梯上，browser 旁邊的另一座山**：`pwn → heap → kernel → browser / VM escape`。瀏覽器逃逸打的是 JIT 的錯誤假設；VM escape 打的是 device 模擬的錯誤假設。兩者都要求你吃下一個龐大真實的 C/C++ 程式的內部模型，但 VM escape 的利用手感更接近傳統 heap pwn——對走過 `kernel_pwn` 的你反而更親切。
- **雲端時代的最高價值邊界**：整個公有雲的隔離假設就是「一個租戶的 VM 打不穿到 host / 別的租戶」。一個穩定的 hypervisor 逃逸在 Pwn2Own 是六位數美金等級的獎項，也是 APT 夢寐以求的能力。看懂這條線，你就看懂了雲端安全模型真正的地基在哪。
- **device emulation 是一個被低估、但 bug 極多的攻擊面**：QEMU 模擬了幾百種硬體（網卡、音效卡、磁碟控制器、GPU、USB…），每一種都是幾千行 C，很多是十幾年前寫的、假設「guest 不會亂來」。這是一片持續在出 CVE 的獵場，而會系統性地挖它的人不多。
- **它把你前面所有 pwn 課串起來**：完整的雲端攻擊鏈是 `guest userland → guest kernel LPE → VM escape → host kernel LPE`。這門課補上中間那一段最關鍵的環，最後一章帶你看整條鏈怎麼接上你已經學過的 `binary_exploitation` 和 `kernel_pwn`。

## 先備知識

- **Userland pwn 熟練**：heap overflow / UAF、infoleak、劫持 function pointer、ROP、繞 ASLR/NX 的直覺要有。不熟先回 `security/binary_exploitation`。QEMU 逃逸的利用階段就是這些。
- **讀得動 C**：QEMU / VirtualBox 都是 C。你要能跟著讀 `hw/` 底下的 device 原始碼——不用會寫，但要能追一條 `.read`/`.write` callback 的資料流。
- **x86-64 組語與 gdb**：會 `disas`、看得懂 stack、能 `gdb attach` 到一個行程。
- **Linux 系統程式基礎**：知道 `mmap`、`ioctl`、`/dev/*`、PCI 大概是什麼。VT-x / KVM 的部分課程從零補，不用先會。
- **kernel pwn 是加分不是必須**：走過 `kernel_pwn` 會讓你對「打一個帶狀態的複雜 C 程式」更有耐心，但這門課的主戰場在 userspace（QEMU 行程），不需要你先會 kernel exploit。Part 7 最後接 host kernel LPE 時才會用到。
- **不需要**先懂編譯器或 JIT——這門課和 `browser_pwn` 是並列的兩座山，互不依賴。

## 環境

- 本課以**從原始碼編譯的 debug QEMU**、**x86-64**、**Linux host（Ubuntu 22.04 / 24.04，實體機或有巢狀虛擬化的環境）** 為主線。
- 主要工具：`qemu`（自編，帶 symbol）、`gcc`、`gdb` + `gef`/`pwndbg`、`pahole`（看結構佈局）、`pwntools`（寫 exploit）、`gdbserver` / QEMU 的 `-s -S`。
- **需要 KVM**：Part 1 手寫 hypervisor 與後面觀察 VMEXIT 都需要 `/dev/kvm`。實體 Linux 最佳；雲端要開巢狀虛擬化（nested virt）；純 Windows / WSL2 可跑大部分利用練習，但 KVM 相關章節要留意環境差異，每章開頭會標。
- **版本會釘定**：QEMU 的 device 程式碼、heap 佈局、mitigation（seccomp sandbox）逐版會變。每個帶 exploit 的章節會標明用哪個 QEMU 版本 / git tag 跑的。你在別的版本重現不出來，先對版本再 debug。
- **Ch 0 會把整套環境一次搭好**，包含怎麼編一個帶 debug symbol、關掉部分 hardening、方便下斷點的 QEMU，以及 guest↔host 兩端 gdb 怎麼同時 attach。

> **驗證說明（認識論誠實）**：帶「自編 QEMU + 自訂 device + PoC」的章節，作者在編好的 QEMU 上實測後才貼真實輸出。牽涉 **VMware（閉源，需逆向）**、**特定 CVE 需要精確舊版環境**、**真實 Pwn2Own 級 full chain** 的段落，會明確標「**未實測，理論預期**」並給你自己驗證的步驟。VM escape 有大量目標無法在教材環境完整重現，這門課選擇對「哪句是跑出來的、哪句是推的」保持誠實，而不是假裝全部跑過。

## 課程地圖

### Part 0 — 環境與心智模型（Ch 0–3）
- [Ch 0 環境搭建：編 debug QEMU、KVM、guest↔host gdb attach](./00-environment-setup.md)
- [Ch 1 什麼是 VM escape：威脅模型與 guest→host 邊界](./01-what-is-vm-escape.md)
- [Ch 2 VMM 怎麼跑一個 guest：從 trap-and-emulate 到硬體輔助](./02-how-vmm-runs-guest.md)
- [Ch 3 攻擊面全圖：為什麼 device emulation 是主戰場](./03-attack-surface-map.md)

### Part 1 — 硬體虛擬化原理補強（Ch 4–8）
- [Ch 4 Intel VT-x：VMX root/non-root、VMCS、VM-exit](./04-intel-vtx.md)
- [Ch 5 EPT 二階分頁：GPA → HPA 與它對逃逸的意義](./05-ept-second-level-paging.md)
- [Ch 6 KVM 架構：/dev/kvm、ioctl、vCPU 迴圈](./06-kvm-architecture.md)
- [Ch 7 從 KVM 到 QEMU：userspace VMM 怎麼接手 exit](./07-kvm-to-qemu-exit.md)
- [Ch 8 手寫最小 KVM hypervisor：100 行跑一個 guest](./08-minimal-kvm-hypervisor.md)
- [練習 A：手寫最小 KVM hypervisor + 攔截 MMIO exit](./practice-a-minimal-hypervisor.md)

### Part 2 — QEMU 內部與 device emulation（Ch 9–15）
- [Ch 9 QEMU 架構全圖：main loop、memory API、QOM](./09-qemu-architecture.md)
- [Ch 10 MemoryRegion 與 guest 物理位址空間](./10-memory-region-address-space.md)
- [Ch 11 一個 device 怎麼被模擬：PIO/MMIO dispatch](./11-device-emulation-dispatch.md)
- [Ch 12 寫一個自訂 PCI device：BAR、MMIO、config space](./12-custom-pci-device.md)
- [Ch 13 DMA：device 怎麼讀寫 guest 記憶體](./13-dma-guest-memory.md)
- [Ch 14 QEMU 的 heap：g_malloc、物件佈局、如何 groom](./14-qemu-heap.md)
- [Ch 15 把 MMIO/DMA 當原語來源：從 guest 觸發 host 讀寫](./15-mmio-dma-as-primitives.md)
- [練習 B：分析並觸發一個自訂 PCI device 的 OOB](./practice-b-pci-device-oob.md)

### Part 3 — QEMU exploit 主線（Ch 16–24）
- [Ch 16 第一個 device bug：MMIO OOB read/write](./16-first-mmio-oob.md)
- [Ch 17 infoleak：洩漏 QEMU PIE base 與 heap 位址](./17-infoleak.md)
- [Ch 18 device emulation 裡的 heap overflow](./18-heap-overflow.md)
- [Ch 19 UAF：hot-unplug 與狀態機錯誤](./19-uaf-hot-unplug.md)
- [Ch 20 找可劫持的 function pointer：MemoryRegionOps、timer、QOM vtable](./20-hijackable-pointers.md)
- [Ch 21 從任意寫到 RIP：劫持 callback / 偽造物件](./21-write-to-rip.md)
- [Ch 22 ROP in QEMU：繞 host ASLR/NX 落到 system/execve](./22-rop-in-qemu.md)
- [Ch 23 真實 CVE 復刻一：VENOM（CVE-2015-3456 FDC）](./23-cve-venom.md)
- [Ch 24 真實 CVE 復刻二：現代 net/virtio device bug](./24-cve-modern-device.md)
- [練習 C：完整 QEMU custom device 逃逸（CTF 題型全流程）](./practice-c-full-qemu-escape.md)

### Part 4 — virtio 深挖（Ch 25–28）
- [Ch 25 virtio 架構：virtqueue、vring、descriptor chain](./25-virtio-architecture.md)
- [Ch 26 virtio 資料流與常見 bug 模式](./26-virtio-bug-patterns.md)
- [Ch 27 vhost / vhost-user：device 搬到另一個 process](./27-vhost-user.md)
- [Ch 28 真實 virtio CVE 剖析與利用](./28-cve-virtio.md)
- [練習 D：virtio device 真實 CVE 復刻](./practice-d-virtio-cve.md)

### Part 5 — VirtualBox（Ch 29–31）
- [Ch 29 VirtualBox 架構與源碼導讀](./29-virtualbox-architecture.md)
- [Ch 30 經典 device bug：E1000 / AHCI / audio](./30-virtualbox-device-bugs.md)
- [Ch 31 VirtualBox 逃逸 exploit 復刻](./31-virtualbox-escape.md)

### Part 6 — VMware Workstation（Ch 32–35）
- [Ch 32 VMware 架構：vmx process 與閉源逆向法](./32-vmware-architecture.md)
- [Ch 33 Backdoor / RPCI：guest→host 通訊通道](./33-vmware-backdoor-rpci.md)
- [Ch 34 SVGA / mks GPU 攻擊面（Pwn2Own 常客）](./34-vmware-svga-gpu.md)
- [Ch 35 VMware 逃逸案例：Pwn2Own writeup 導讀](./35-vmware-escape-case.md)
- [練習 E：VirtualBox 或 VMware 逃逸案例復刻](./practice-e-vbox-vmware-case.md)

### Part 7 — 現代 mitigation / 雲端 / 整合（Ch 36–40）
- [Ch 36 host 端 mitigation：QEMU seccomp、sVirt、namespace](./36-host-mitigations.md)
- [Ch 37 逃逸後還被關著：繞過 QEMU seccomp sandbox](./37-bypass-seccomp.md)
- [Ch 38 雲端 microVM：Firecracker、gVisor 攻擊面](./38-cloud-microvm.md)
- [Ch 39 side-channel / CPU bug 對虛擬化的衝擊：L1TF/MDS/Spectre](./39-side-channels.md)
- [Ch 40 完整 chain 全景：guest → VM escape → host kernel LPE](./40-full-chain.md)
- [Final Project：從真實 CVE 到完整 guest→host 逃逸 exploit](./final-project-cve-to-escape.md)

## 學習方式建議

1. **每章都在自編 QEMU 上跑**：這門課的核心資產是一顆你自己編的、帶 debug symbol、掛得上 device 的 QEMU。看到 PoC 就自己跑，看到「下斷點在 `.write` callback」就自己下。光讀不跑，device emulation 永遠是抽象的。
2. **兩端 gdb 一起開**：VM escape 的獨特手感是「guest 裡一個動作，host 端一個反應」。習慣同時開 guest 裡的觸發程式和 host 端 attach QEMU 的 gdb，看一次觸發路徑走完，你對邊界的直覺會完全不同。
3. **故意把 device 弄壞**：這門課提供的自訂 PCI device，改一個邊界檢查、拿掉一個長度驗證，然後從 guest 打它。後半所有真實 CVE，本質都是「device 相信了 guest 給的某個值」。
4. **對照原始碼讀**：每章給 `hw/` 底下具體路徑。QEMU 沒有比它原始碼更權威的文件；養成打開對應 `.c` 檔追資料流的習慣。
5. **CVE 章節先讀 patch**：Ch 23/24/28 的真實 CVE，先去看那個修補 commit 的 diff——一行 `if` 的增減，往往就是整個洞。學會「從 patch 反推洞」，你就有了 1-day 能力。

## 精選資料庫

整門課最值得反覆參照的資源；每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[QEMU 原始碼](https://gitlab.com/qemu-project/qemu)**（`hw/`、`softmmu/`、`system/`、`include/hw/`）
  - 這門課的最終仲裁。行為和教材不符時，以你編的那個版本的原始碼為準。device 攻擊面全在 `hw/` 底下。
- **[Intel SDM Volume 3C（VMX / VT-x）](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)**
  - VT-x / VMCS / EPT 的權威來源。Part 1 的硬體原理以這裡為準；不用全讀，Ch 4/5 會標具體章節。
- **[KVM API 文件](https://www.kernel.org/doc/html/latest/virt/kvm/api.html)**
  - `/dev/kvm` 所有 ioctl 的定義。Part 1 手寫 hypervisor 的一手依據。

### 推薦部落格 / 系列

- **[Google Project Zero blog](https://googleprojectzero.blogspot.com/)** — Google P0
  - 多篇 hypervisor 逃逸的最高品質 writeup（含 QEMU / VMware / VirtualBox）。看完這門課就是為了讀懂這裡。
- **[phoenhex / blackpwn 等 CTF 戰隊 QEMU escape writeup]**
  - 幾乎每年 DEF CON / 各大賽都有 QEMU custom device 逃逸題，writeup 是 Part 3 的直接對照。Ch 16–24 會點名幾篇經典。
- **[Nguyen Anh Quynh / Mem2019 / 各家 QEMU escape 教學系列]**
  - 中英文都有把 QEMU 逃逸從 device 到 RIP 寫清楚的長文，是本課利用鏈的並行讀物。

### 官方文件 / 工具

- **[QEMU 開發者文件](https://www.qemu.org/docs/master/devel/index.html)**（QOM、memory API、PCI device 教學）
  - Ch 9–13 的權威依據；寫自訂 device 的官方指南就在這。
- **[libvirt / sVirt / seccomp 文件]**
  - Part 7 host 端 mitigation 的一手材料。

### 讀完本課之後

- **真實雲端 full chain writeup**（guest → hypervisor → host kernel）——這門課停在 VM escape，下一步把 `kernel_pwn` 學到的東西接上去，組成完整雲端逃逸鏈。
- **[Pwn2Own 歷年 virtualization category 得獎報告]**——商業 hypervisor 逃逸的最前沿，Ch 34/35 的延伸。

## 這門課刻意不涵蓋

- **Hyper-V 深入**：Windows 的 Hyper-V（vmbus、VSM）只在 Ch 38 雲端章帶到威脅模型，不逐行實作——它閉源、且威脅模型與 Linux 系不同，是另一門課的份量。
- **完整的 hypervisor 開發**：Part 1 手寫的是「能觀察 exit 的最小 hypervisor」，不是要你做出一個能跑 Linux 的 VMM。教你原理是為了打洞，不是為了造 VMM。
- **CPU 微架構側信道的深挖**：Spectre / L1TF / MDS 這類只在 Ch 39 做威脅模型導覽並指路，逐條 gadget 分析是側信道專題的份量，不在本課主線。
- **VMware 的逐行實測**：VMware 閉源，本課這一段以逆向方法、公開 writeup 導讀、理論預期為主，動手比例低於 QEMU 主線——會誠實標注。
