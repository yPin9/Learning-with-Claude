# Ch 10 — SMM / SMRAM / SMI：Ring -2 為何是聖杯

> **目標**：徹底理解 System Management Mode 的存在意義、硬體機制、以及為什麼攻下 SMM 代表你在 OS 重灌後依然常駐、在 hypervisor 之下依然看得到一切。

## 為什麼需要 SMM？

x86 的保護環（protection ring）從 Ring 3（使用者態）到 Ring 0（kernel）早在 386 時代就設計好了。但晶片廠商很快發現，有一類需求無論如何塞不進這個框架：**平台管理**。

舉幾個例子：

- 風扇轉速控制、溫度過高自動降頻——這些邏輯必須跑，無論 OS 在做什麼
- 電源管理（ACPI 某些 sleep state 轉換）
- 舊 PS/2 鍵盤透過 USB 控制器模擬（Legacy USB emulation）
- 保護 SMM 自身的設定（SMRR、D_LCK 等寄存器的鎖定）

這些需求有個共同性質：**OS 不能知道、不能干涉、但 CPU 必須執行**。

Intel 在 386SL（1990）引入 SMM 來滿足這個需求。這不是一個軟體抽象，而是一個**硬體執行模式**，有自己的位址空間、自己的狀態保存區、自己的返回指令（RSM）。

## 先建立直覺

把保護環想成一棟大樓的樓層：

```
┌────────────────────────────────┐
│  Ring 3  使用者程式             │  ← 一般住戶
├────────────────────────────────┤
│  Ring 0  OS kernel             │  ← 管理員辦公室
├────────────────────────────────┤
│  Ring -1  Hypervisor (VMX)     │  ← 建築管理公司
├────────────────────────────────┤
│  Ring -2  SMM                  │  ← 城市地下管線工程師
└────────────────────────────────┘
        ▲ OS 甚至不知道這層存在
```

SMM 不是「比 kernel 高一點點」的權限，而是一個**完全獨立的執行環境**：

- CPU 進入 SMM 時，**暫停所有其他核心的正常執行**（或至少凍結本核心）
- OS 和 hypervisor 都看不到 SMM 的執行、記憶體、暫存器
- SMM 可以讀寫 OS 的所有記憶體，包括 kernel code
- SMM 可以修改 SMRAM 外的任意 DRAM 內容

這就是為什麼 SMM 是韌體攻擊的聖杯：拿到 SMM 執行權限，等同拿到一個**永久後門**，OS 重灌、甚至換掉 hypervisor 都無法清除它。

## SMRAM：SMM 的私有記憶體

SMM 的程式碼和資料住在 **SMRAM**（System Management RAM），這是一塊從普通 DRAM 劃出來、在正常模式下完全不可見的記憶體區域。

### TSEG（Top of Memory Segment）

現代 x86 平台上，SMRAM 幾乎都放在 **TSEG**（Top Segment）：位在可用實體記憶體最頂端的一塊連續區域，大小通常是 8 MB。

```
實體記憶體佈局（示意，以 8GB 系統為例）：

0x0000_0000_0000  ┌───────────────────────────┐
                  │  可用 DRAM（OS 可見）       │
                  │  ...                        │
                  │                             │
0x01F800_0000     ├───────────────────────────┤  ← TSEG base（TSEG_MB 暫存器）
                  │  TSEG（8 MB）              │
                  │  存放 SMM handler code      │
                  │  存放 SMM save state area   │
                  │  ← 普通模式下不可讀寫        │
0x01FFFF_FFFF     └───────────────────────────┘
```

TSEG 的位置由 chipset 的 **TSEG_MB** / **TSEGMB** 暫存器（各廠商名稱略異，多在 host bridge 或 PCH 的 PCI config space）定義。

### ASEG 與 CSEG（歷史遺留）

更早期的系統用 **ASEG**（位在 640 KB–1 MB 之間的 0xA0000–0xBFFFF）和 **CSEG**（0x30000–0x3FFFF），這些屬於 legacy 設計，現代系統主要用 TSEG，但舊版韌體仍可能同時啟用。

### SMRAM 如何在普通模式下隱藏？

SMRAM 的隱藏是**硬體強制**的，不是 OS 能繞開的軟體設定：

1. **D_LCK（SMRAM Lock）**：chipset 的 SMRAM 控制暫存器有一個 D_LCK 位，一旦設為 1（通常在 DXE 結束前由 PiSmmIpmiTransportDxe 或類似模組設定），SMRAM 就永久鎖定，直到下次硬體重設為止。任何來自 CPU 非 SMM 模式的讀寫存取都會被 chipset silently 忽略（或返回 0xFF）。

2. **SMRR（SMM Range Registers）**：從 Intel Xeon 5500（Nehalem）起引入，SMRR 是 CPU 的 MTRR 延伸，告訴 CPU：「TSEG 這個範圍，在非 SMM 模式下快取存取一律無效」。這擋住了透過 cache 探測 SMRAM 內容的攻擊（見 Ch 11）。

3. **D_OPEN 清除**：D_OPEN 是 chipset 控制暫存器的另一位，韌體初始化期間用來讓 CPU 能把 SMM 程式碼寫入 SMRAM；開機流程結束前必須清除，並設定 D_LCK。若 D_OPEN 在鎖定前沒清乾淨，攻擊者可從 OS 層存取 SMRAM。

## SMI：觸發 SMM 進入的中斷

**SMI**（System Management Interrupt）是觸發 CPU 進入 SMM 的機制，它是 x86 架構中**最高優先的中斷**，不可遮罩（NMI 也無法阻止 SMI 被接受）。

### SMI 的觸發來源

| 觸發來源 | 機制 | 常見使用場景 |
|---------|------|-------------|
| Software SMI（SWSMI） | 寫入 I/O port 0xB2（APMC 埠） | OS 呼叫 UEFI runtime service（部分實作）、ACPI 動作、平台工具 |
| GPE（General Purpose Event） | ACPI 的通用事件，由 SCI 或 SMI 引腳觸發 | 電源按鈕、wake-on-LAN |
| Periodic SMI | chipset timer，每隔固定時間發一次 | 電源管理輪詢、溫度監控 |
| 硬體事件 | SERR、MCERR、USB legacy 事件 | 匯流排錯誤處理、USB 鍵盤模擬 |

**APMC I/O port 0xB2** 是最關鍵的一條路徑：

```c
// 從 OS kernel（Ring 0）觸發 software SMI，value 帶 SMI 代碼
outb(value, 0xB2);
```

這一行組語（`out 0xB2, al`）任何具有 IOPL=0 特權的程式都能執行。寫入的值會被 chipset 捕捉，轉換成 SMI 信號送給 CPU。攻擊者只要在 OS 上拿到 kernel 執行能力，就能呼叫任意 software SMI code。

## SMBASE 與 SMM Save State Area

CPU 進入 SMM 時，需要一個地方來**保存目前 CPU 狀態**（所有通用暫存器、段暫存器、控制暫存器、RIP 等）以便 RSM 時恢復。這個區域叫 **SMM Save State Area**，位置由 **SMBASE** 決定。

### SMBASE

每個邏輯 CPU（logical processor）都有自己的 SMBASE，預設為 0x30000，韌體通常在初始化時改成 TSEG 內部的某個偏移位址。

```
TSEG 內部佈局（示意）：

SMBASE + 0x0000  ┌─────────────────────────┐
                 │  SMM code               │  ← RSM 前跑的 handler
                 │  ...                    │
SMBASE + 0x7E00  ├─────────────────────────┤
                 │  SMM Save State Area    │  ← CPU 自動填入
                 │  (512 bytes)            │
SMBASE + 0x8000  └─────────────────────────┘
```

SMM Save State Area 的格式是 Intel 規範定義的（不同 CPU 世代有細節差異），包含：

- 所有通用暫存器（RAX–R15）
- RIP、RFLAGS、RSP
- 段暫存器（CS、DS、ES、FS、GS、SS）與其 descriptor cache
- 控制暫存器（CR0、CR3、CR4）
- SMBASE 本身（可讀取但有保護）

這份 save state **攻擊者非常感興趣**：如果能讀到 kernel 的 CR3，就能解析 OS 的頁表，進行精準記憶體操作。

## 一次 SMI 的完整流程

```
Normal Mode（OS 跑中）
│
│  outb(0x88, 0xB2)   ← OS kernel 觸發 SW-SMI，code = 0x88
│
▼
Chipset 收到 APMC 寫入
→ 產生 SMI# 信號

CPU 收到 SMI#
├── 完成目前 instruction（確保 atomic 操作不被劈開）
├── 將 CPU 狀態存入 SMBASE + 0x7E00（SMM Save State Area）
├── 強制進入 real-mode-ish 的 SMM 環境（自己的 flat 32/64-bit 空間）
└── 跳轉到 SMBASE + 0x8000（SMM 進入點）

                ┌──────────────────────────────────────┐
                │  SMM Handler 執行                     │
                │                                      │
                │  1. SmmEntry → dispatch              │
                │  2. 查 I/O port 0xB2 的值（SMI code）│
                │  3. 找對應的 SW SMI handler           │
                │  4. 執行對應功能（如改 NVRam、電源...）│
                │  5. 清 SMI 狀態位元（EOS）            │
                └──────────────────────────────────────┘
                │
                │  RSM（Resume 指令）
                │
▼
CPU 從 Save State Area 恢復所有暫存器
→ 返回 Normal Mode，OS 繼續跑，完全不知道 SMM 發生過什麼
```

**關鍵點**：OS 眼中，從觸發 SMI 到 RSM 返回，這段時間的 CPU 是「消失」的，只能看到一個延遲。OS 無法觀測 SMM 做了什麼、改了什麼記憶體。

## 為什麼 SMM 是韌體攻擊的聖杯

把上面所有特性串起來：

| 特性 | 對攻擊者的意義 |
|------|--------------|
| 比 hypervisor 更低層 | 即使目標機器開了 VM，SMM 依然在 VMX root 之下，hypervisor 的 EPT 保護對 SMM 無效 |
| OS 完全不可見 | Rootkit 無需躲避 kernel AV；AV 根本看不到 SMM 執行空間 |
| 可讀寫任意 DRAM | 從 SMRAM 外呼叫 SMM 中的惡意 payload，SMM 幫你把 shellcode 寫進 kernel.text |
| 存活於重灌 | SMM 程式碼存在 SPI flash 的 UEFI FV 中，OS 重灌改變不了 |
| 觸發點低門檻 | 只需要 kernel 層（Ring 0）就能觸發 SW-SMI |
| 難以偵測 | 沒有 OS 可見的中斷向量；perf/eBPF 都看不到 SMM 時間 |

這就是為什麼 MoonBounce（2022）、CosmicStrand（2022）、LoJax（2018）這類 UEFI bootkit 都以 SPI flash 中的 DXE/SMM 模組作為駐留手段——即使你把硬碟格掉、重灌 OS，只要 SPI flash 沒被重刷，惡意 SMM 仍在。

## 底層機制深挖：SMRAM 保護的硬體路徑

```
CPU Core
  │
  │  非 SMM 模式發出讀取 TSEG 位址的記憶體請求
  │
  ▼
MTRR / SMRR 判斷
  ├── SMRR.base 和 SMRR.mask 比對位址
  ├── 若命中：CPU 強制 Uncacheable，不走 cache
  └── （若走 cache 且 cache hit：SMRR 保證 SMRAM 不在 LLC 中）

  │
  ▼
CPU 送請求到 Ring Bus / Uncore

  │
  ▼
Memory Controller Hub（MCH）/ IMC
  ├── 查詢 TSEG 範圍（TSEGMB 暫存器）
  ├── 若存取地址在 TSEG 內，且 D_LCK=1、D_OPEN=0：
  │   └── **拒絕存取**（不送 DRAM 請求，返回 0xFF 或 UR completion）
  └── 若 D_OPEN=1（初始化期間）：允許

這個拒絕是在 chipset 層做的，CPU 指令本身完成了，但資料永遠不對。
```

重點：SMRAM 保護是**硬體路徑強制**，不是靠 OS 配合，也不是靠 kernel page table 的存取控制。這讓 SMRAM 的隱藏比任何純軟體機制都堅固——前提是 D_LCK 有被正確鎖定，且 SMRR 有被設置。

## 對比取捨

| 概念 | SMM | Hypervisor (VMX) | OS kernel (Ring 0) |
|------|-----|------------------|--------------------|
| 可見層級 | OS/VMM 完全不可見 | OS 不可見 | 使用者態不可見 |
| 觸發機制 | SMI（硬體/軟體） | VMExit | syscall / interrupt |
| 記憶體隔離 | 硬體（SMRR + chipset） | EPT（軟體配置） | Page table（軟體） |
| 執行位置 | SMRAM（SPI flash 中的程式碼複製到 TSEG） | Host physical memory | Kernel virtual memory |
| 攻擊後持久性 | SPI flash 層級（最強） | VM 層級（重啟消失） | DRAM 層級（關機消失） |
| 偵測難度 | 極高 | 高 | 中 |

## 踩雷紀錄

**坑 1：以為 SMRAM 在虛擬機裡也完全隔離**
QEMU/OVMF 的 SMRAM 模擬依賴 QEMU 的 memory region 機制，不是真正的硬體 SMRR + D_LCK。在 QEMU guest 內用 CHIPSEC 讀到的 SMRAM 保護狀態，不等於真實硬體的行為。做安全稽核一定要在**真機**上跑 CHIPSEC。

**坑 2：混淆 SMBASE 的預設值與執行值**
Intel 手冊說 SMBASE 預設為 0x30000，但 OVMF/EDK2 實際會在 SmmBase2 protocol 的 `InternalSmBaseRelocationAll()` 裡把每個 CPU 的 SMBASE 重定向到 TSEG 內的各自偏移。逆向時看到 SMBASE=0x30000 代表還沒重定向，不代表 handler 真的在那裡。

**坑 3：誤判 SMI 的 latency**
不少平台的 periodic SMI 每 32ms 一次（32 Hz），大量 software SMI 或 periodic SMI 會造成可量測的 OS 延遲（SMI jitter）。在 RT（real-time）系統上，SMI latency 本身就是一個安全問題（DoS 向量）；但一般 CTF / 研究環境常忽略這個面向。

**坑 4：假設 SMM 只有一個 handler**
現代 EDK2 的 SMM 架構有 **SMM Core**（提供 dispatcher 框架）和多個 **SMM driver**（各自呼叫 `SmmInstallProtocolInterface` 或 `SmiHandlerRegister`），你在 CH11 會看到整個 dispatch 鏈。以為只有一個 handler 入口點是逆向時很常見的第一個錯誤。

**坑 5：D_LCK 鎖了就以為萬無一失**
D_LCK 防的是「從非 SMM 模式直接讀寫 SMRAM」。但它無法防：
- SMM 自己的程式碼有漏洞（callout、pointer corruption）
- SMRR 沒設置時的 cache 攻擊
- 透過 CommBuffer 的受控輸入  

保護機制需要疊加，缺一不可。

## 進階延伸

- **多 CPU 拓撲下的 SMM**：多核系統中，BSP（Bootstrap Processor）收到 SMI 後需要廣播給所有 AP（Application Processor），透過 LAPIC IPI 送出 SMI 信號。所有核心必須進入 SMM（rendezvouz protocol）才能繼續，否則 AP 可能在 SMM 執行期間修改 OS 記憶體造成 race。這個 rendezvouz 機制本身有 race 攻擊面（Ch 13 觸及）。

- **SMM 的 64-bit 執行**：現代 EDK2 的 SMM 以 64-bit long mode 運行（SMM_CODE_ACCESS_CHK / SMM_PAGING_CHECK 相關），有自己的頁表（SMM CR3）。舊版實作用 32-bit，CR4.SMXE/SMEP 保護不到 SMM，這是一個歷史遺留的攻擊面。

- **Firmware-First vs. OS-First 的 MCA 處理**：Machine Check Architecture 錯誤可以選擇由 SMM firmware 先處理（firmware-first），然後再通知 OS。這個路徑給了 SMM 額外的觸發面，也是 Ch 14 Intel ME 的相關設計。

## 動手練習

以下練習在支援 QEMU SMRAM 模擬的環境下操作，但請記得：QEMU 不等於真機保護，僅供理解架構用。

**練習 1：確認 SMRAM 在 QEMU 中的記憶體佈局**

```bash
# 在 WSL 中啟動 OVMF QEMU
wsl -e bash -lc '
qemu-system-x86_64 \
  -machine q35,smm=on,accel=tcg \
  -bios /usr/share/qemu/OVMF.fd \
  -m 512M \
  -nographic \
  -monitor unix:/tmp/qemu-mon.sock,server,nowait \
  -serial stdio &
sleep 5
echo "info mtree" | socat - UNIX-CONNECT:/tmp/qemu-mon.sock | grep -i smram
'
```

> 這個命令嘗試啟動 QEMU 並查詢記憶體樹，尋找 SMRAM 的 memory region 標記。若 OVMF.fd 不在預設位置，調整路徑。

**練習 2：觀察 SMI latency 的影響（純概念驗證）**

在 Linux 下，`/proc/interrupts` 不會顯示 SMI；但可以用 `hwlat_detector`（kernel module）或 `oslat` 工具間接量測 SMI 造成的延遲峰值。這不是 exploit，而是讓你體會「OS 層根本感知不到 SMM 發生過」這個事實。

**練習 3：讀 EDK2 SMM 進入點**

```bash
# 在 WSL 中 clone edk2 並找 SMM 的 entry point
wsl -e bash -lc '
git clone --depth=1 https://github.com/tianocore/edk2 /tmp/edk2 2>/dev/null || true
grep -r "SmmEntry\|SmiEntry" /tmp/edk2/UefiCpuPkg/ --include="*.asm" -l | head -5
grep -n "SmmEntryPoint\|SmiRendezvous" /tmp/edk2/UefiCpuPkg/PiSmmCpuDxeSmm/ -r | head -10
'
```

找到 `SmiEntry.nasm` 後，閱讀其中的 SMBASE 設定與進入 C handler 前的環境建立過程。

## 本章重點

- SMM 是 x86 硬體的一個獨立執行模式，不可被 OS 或 hypervisor 觀測，比 Ring -1 更低
- SMRAM（主要是 TSEG）由 chipset 硬體強制隱藏，D_LCK + SMRR 是主要保護機制
- SMI 有多種觸發來源，software SMI 透過 I/O port 0xB2 由 Ring 0 程式碼觸發
- 進入 SMM 時 CPU 自動保存完整狀態到 SMM Save State Area（SMBASE + 0x7E00）
- SMM 能讀寫任意 DRAM，RSM 後 OS 完全不知道期間發生了什麼
- 攻下 SMM = SPI flash 層級的持久化，OS 重灌無法清除

## 自我檢核

- [ ] 我能解釋 TSEG 是什麼、為什麼 OS 看不到它
- [ ] 我能說出 D_LCK 和 SMRR 各自擋的是哪種攻擊
- [ ] 我能描述從寫 I/O port 0xB2 到 RSM 返回的完整流程
- [ ] 我能說出 SMBASE + 0x7E00 存的是什麼
- [ ] 我能解釋為什麼 SMM 的持久性比 hypervisor rootkit 更強

## 延伸閱讀

1. **Intel 64 and IA-32 Architectures Software Developer's Manual, Volume 3, Chapter 34「System Management Mode」**
   - 讀哪裡：Vol.3A Ch.34，重點是 34.2（SMRAM）、34.4（SMI）、34.7（Save State Map）
   - 學什麼：SMBASE 的精確格式、Save State Area 每個欄位的位址偏移、SMRAM 保護暫存器的確切 bit 定義
   - 關聯：本章所有技術細節的一手來源，逆向 SMM handler 時必備

2. **「Attacking and Defending BIOS in 2015」— Bulygin, Loucaides et al.（RECon 2015）**
   - 讀哪裡：slides p.10–35，專注 SMM 保護機制那一段
   - 學什麼：D_LCK/D_OPEN/SMRR 的歷史演進、各代平台的保護狀態、CHIPSEC 模組對應哪些 bit
   - 關聯：本章「SMRAM 保護機制」小節的實作背景；Ch 11 攻擊面的攻守對照

3. **EDK2 原始碼：`UefiCpuPkg/PiSmmCpuDxeSmm/`**
   - 讀哪裡：`SmiEntry.nasm`（CPU 進入 SMM 的組語），`PiSmmCpuDxeSmm.c`（C handler 入口），`SmmCpuMemoryManagement.c`（SMRAM 頁表管理）
   - 學什麼：SMBASE 重定向的實際程式碼、Rendezvouz protocol 的實作、SMM 64-bit 頁表的建立流程
   - 關聯：逆向真實韌體的 SMM 模組時，EDK2 是對照來源；Ch 11 的 handler dispatch 架構也在這裡

→ [下一章](./11-smm-attack-surface.md)
