# Ch 18 — coreboot 與開源韌體
> **目標**：理解 coreboot 的啟動流程與架構、它與 UEFI 的關係、開源韌體帶來的安全意義與其固有限制，並能清楚劃出 x86 平台上「信任邊界實際推到哪裡」——不是推到底，是推到 FSP/ME blob 那道牆。

---

## 18.1 為什麼需要開源韌體

韌體是現代系統裡最後一個「我信任你但看不到你」的黑盒子。OS 可以審計，hypervisor 可以審計，硬體的 RTL 在學術界也有開放 RISC-V 實作——但 BIOS/UEFI 長期以來是 AMI、Insyde、Phoenix 三家寡占的閉源 binary，買一張主機板就預裝了一個幾百萬行誰都看不進去的 blob。

這帶來幾個實質問題：

**可驗證性缺失**：你不知道 DRAM 初始化的過程是否有後門，不知道 SMM handler 是否有蓄意留下的 ring-2 入口。廠商說「我們是安全的」，你只能信。

**更新滯後**：UEFI 漏洞（Spectre、LogoFAIL、PixieFail）被揭露後，廠商要多久才出更新、多少機型被放棄，都是公開的失敗記錄。

**供應鏈不透明**：你買的 motherboard 韌體裡有多少 third-party 模組（network stack、video BIOS、IME agent），誰也說不清楚。

coreboot 試圖解決這些問題——把 firmware 的主要邏輯全部開源，讓任何人都能 git clone、讀懂、改動、重建。它確實往前推了信任邊界，但推到多遠、在哪裡碰到牆，是這章要講清楚的核心。

---

## 18.2 直覺建立：兩種啟動流程的對照

### coreboot 啟動流程

```
CPU Reset Vector (0xFFFFFFF0)
        |
        v
+------------------+
|    bootblock     |  XIP (Execute-in-Place) 直接從 flash 執行
|  (flash 上跑)    |  初始化 cache-as-RAM (CAR)
+------------------+
        |
        v
+------------------+
|    romstage      |  在 CAR 上執行（DRAM 還沒）
|  (DRAM init)     |  呼叫 FSP-M (Intel) 或 agesa (AMD)
+------------------+
        |
        v
+------------------+
|    ramstage      |  DRAM 可用，搬到真實記憶體執行
|  (device enum)   |  掃描 PCI/USB、建 ACPI table、建 coreboot table
+------------------+
        |
        v
+------------------+
|     payload      |  SeaBIOS / edk2 / LinuxBoot / GRUB2
+------------------+
        |
        v
      OS Loader
```

### UEFI PI (Platform Initialization) 規範的啟動流程

```
CPU Reset Vector
        |
        v
+-------+--------+
|      SEC       |  Security phase，最小 assembly
|  (reset init)  |  建立臨時記憶體環境 (cache-as-RAM)
+----------------+
        |
        v
+----------------+
|      PEI       |  Pre-EFI Initialization
|  (DRAM init)   |  跑 PEIM module，init 記憶體、IO、基本硬體
+----------------+
        |
        v
+----------------+
|      DXE       |  Driver Execution Environment
|  (device init) |  載入 DXE driver、建立 UEFI protocol
+----------------+
        |
        v
+----------------+
|      BDS       |  Boot Device Select
|  (boot order)  |  找到 boot target，呼叫 UEFI Boot Manager
+----------------+
        |
        v
+----------------+
|  RT/Handoff    |  UEFI Runtime Services 留給 OS 使用
+----------------+
```

### 階段對應關係

```
coreboot             UEFI PI
-----------          --------
bootblock        ~=  SEC
romstage         ~=  PEI  (DRAM init)
ramstage         ~=  DXE  (device enum)
payload          ~=  BDS + OS loader
```

對應是語義對應，實作細節差異很大。coreboot 的 romstage 可以完全跳過 PEIM 架構，直接用 C 呼叫 FSP-M 的 API；UEFI PEI 要求符合 PEIM 介面、PPI 機制等規範。

---

## 18.3 coreboot 架構詳解

### 18.3.1 bootblock

bootblock 是整個啟動鏈的第一段程式碼，從 CPU 的 reset vector（x86 是 `0xFFFFFFF0`，即 4GB 頂端往前 16 bytes）開始執行。此時：

- DRAM 完全不可用
- 只有 CPU 的內部資源和 flash（透過 SPI 控制器映射到記憶體）
- 程式碼在 flash 上 XIP（Execute-in-Place）執行，速度慢但不需要記憶體

bootblock 的主要工作是初始化 cache-as-RAM (CAR)：把 CPU 的 L1/L2 cache 設定成 no-evict 模式，作為臨時 SRAM 使用。這是 x86 平台的標準技巧，讓後續的 C 程式碼有地方跑（需要 stack）。

實作上 bootblock 非常小，通常 < 4KB，包含少量 assembly（reset entry point）加薄薄一層 C。它的 source 在 `src/arch/x86/bootblock.c` 和各平台的 `chipset/` 目錄。

### 18.3.2 romstage

romstage 是最關鍵也最難的階段：在沒有 DRAM 的情況下（只有 CAR）初始化 DRAM。

對 Intel 平台，romstage 通常會呼叫 FSP-M（Firmware Support Package – Memory init），這是 Intel 提供的閉源 binary，負責 DRAM training、SPD 讀取、XMP profile、memory controller 設定等複雜工作。

對 AMD 平台，對應的是 AGESA（AMD Generic Encapsulated Software Architecture），同樣是閉源 binary。

romstage 結束後，DRAM 可用，程式碼從 CAR 搬到真實 DRAM 繼續執行。CAR 被拆除。

### 18.3.3 ramstage

ramstage 是在真實 DRAM 上執行的主要初始化階段，工作量最大：

- 呼叫 FSP-S（Silicon init）做 PCH/SoC 初始化
- 掃描 PCI bus、enumerate 裝置、分配 BAR
- 初始化 USB、SATA、GPIO 等周邊
- 建立 ACPI table（DSDT、SSDT、MADT 等）
- 建立 coreboot table（`struct lb_header`），傳給 payload
- 設定 SMM（SMRAM 範圍鎖定、SMM handler 載入）
- 呼叫 payload

ramstage 的 device model 在 `src/device/` 下，各 chipset 的 southbridge、northbridge 程式碼在 `src/southbridge/` 和 `src/northbridge/`。

### 18.3.4 CBFS（Coreboot File System）

CBFS 是 coreboot 用來組織 flash 內容的自訂格式。結構類似簡單的 FAT：

```
Flash Image Layout:
+---------------------------+
|  CBFS master header       |  offset 0 or top of flash
+---------------------------+
|  cbfs_file: "bootblock"   |  header + compressed/raw data
+---------------------------+
|  cbfs_file: "romstage"    |
+---------------------------+
|  cbfs_file: "fspm.bin"    |  FSP-M blob
+---------------------------+
|  cbfs_file: "ramstage"    |
+---------------------------+
|  cbfs_file: "payload"     |  SeaBIOS / edk2 / LinuxBoot
+---------------------------+
|  cbfs_file: "config"      |  build config
+---------------------------+
|  cbfs_file: "DSDT.aml"   |  ACPI table
+---------------------------+
|       (free space)        |
+---------------------------+
```

`cbfstool` 是操作 CBFS 的主要工具，可以 print（列出內容）、add、remove、extract 各 file。`cbmem` 工具在 OS 啟動後可以讀 coreboot 在 CBRAM 留下的 boot log 和 table。

CBFS 本身沒有簽章機制——除非你加上 vboot（後述）。

### 18.3.5 payload 選項

coreboot 的最後一步是把控制權交給 payload。payload 的選擇決定了系統的功能邊界：

**SeaBIOS**

最老牌的 payload，實作傳統 x86 BIOS 介面（INT 13h 磁碟、INT 10h 顯示等）。優點是相容性好，老 OS 和 bootloader 不需要改動。缺點是無 UEFI 介面，不支援 Secure Boot，無 UEFI Runtime Services。適合 legacy 設備或 QEMU 虛擬機。

**Tianocore edk2（UEFI payload）**

把 edk2 編譯成 coreboot payload，讓系統對外呈現標準 UEFI 介面。OS 看到的是完整 UEFI——Secure Boot、EFI variable、UEFI Runtime Services 全部可用。適合需要 UEFI 的現代 OS（Windows、標準 Linux distro）。

重要限制：這個 edk2 是以 payload 身份執行，不是從 SEC/PEI 走過來的完整 UEFI PI 路徑。SMM 的初始化時機和某些 Security Protocol 的語義與標準 UEFI 有差異（第 18.4 節細談）。

**LinuxBoot**

把 Linux kernel 本身（加上 initramfs）當作 payload。系統先啟動一個「firmware OS」，在這個 Linux 環境裡跑驗證邏輯，確認一切沒問題後用 `kexec` 啟動真正的目標 OS。

HEADS 專案（https://github.com/osresearch/heads）是 LinuxBoot 最知名的實作，專為高安全需求設計：可以用 TPM 做 measured boot、用 GPG 驗簽 kernel、偵測到竄改時拒絕開機。Purism Librem 筆電和部分伺服器平台預設使用 HEADS。

LinuxBoot 的安全優勢：firmware 邏輯完全在 Linux userspace 寫，比在 EFI 環境寫更容易審計和測試。

**GRUB2**

直接把 GRUB2 當 payload，省掉一層 UEFI，適合只跑 Linux 且不需要 UEFI 功能的情境。最輕量，設定最簡單，但相容性最差。

---

## 18.4 coreboot 與 UEFI 的關係

常見的誤解是：「coreboot 就是不要 UEFI」。這不準確。

coreboot 取代的是 UEFI 的早期初始化部分（SEC + PEI + DXE 的 hardware init），但它可以在 payload 層重新接回 UEFI 介面。兩者的關係如下：

```
標準 UEFI 路徑：
  [SEC] → [PEI] → [DXE] → [BDS] → OS

coreboot + edk2 payload 路徑：
  [bootblock] → [romstage] → [ramstage] → [edk2 DXE/BDS] → OS
        ^                                        ^
        |_____ coreboot 做硬體初始化 ___________|
                                          edk2 只做 UEFI 協定層
```

這個架構的安全含義：

**SMM 初始化時機**：標準 UEFI 在 DXE 的 `SmmBase2` protocol 初始化 SMM。coreboot 在 ramstage 就設好 SMRAM 和 D_LCK，然後 edk2 payload 再疊上去。如果 coreboot 的 SMM 設置有問題（例如 D_LCK 沒在對的時機設），edk2 層的假設可能不成立。

**PEI 安全語義消失**：UEFI PI 規範裡，PEI 階段的 PPI（PEIM-to-PEIM Interface）有嚴格的安全假設（哪些 PPI 可信、哪些需要驗證）。coreboot 完全繞開了 PEI，因此所有依賴 PEI 安全語義的程式碼（例如某些 EDK2 安全 feature）在 coreboot + edk2 payload 環境下實際上沒有效。

**Secure Boot 可以跑，但根不同**：coreboot + edk2 可以執行 UEFI Secure Boot，但信任根在 edk2 的 db/KEK/PK variable 裡，而不是在 BootGuard ACM。這意味著如果有人能改 CBFS（換掉 edk2 binary），整個 Secure Boot 就廢了。

---

## 18.5 開源韌體的安全意義

### 可審計性

這是 coreboot 最實質的安全優勢。你可以：

- `git clone https://review.coreboot.org/coreboot.git` 拿到全部原始碼
- 用 CodeQL 或 Semgrep 做靜態分析找記憶體錯誤
- 用 `grep -rn "smm\|smbase\|d_lck"` 找 SMM 相關設置
- 比對每次 commit 的 diff，看看哪個版本改了什麼

廠商 UEFI binary 沒有這些選項。你能做的頂多是用 IDA/Ghidra 逆向，還要擔心 NDA 問題。

### 供應鏈透明度

coreboot build system 明確列出每個 binary blob 的 hash，在 `3rdparty/blobs/` 目錄和各 mainboard config 裡都有記錄。你知道 romstage 呼叫的是哪個 FSP-M binary、hash 是什麼，即使你看不進去，至少你能偵測它有沒有被換掉。

### 客製化能力

開源韌體讓安全研究者和高安全需求的部署可以做到廠商 UEFI 不可能提供的客製化，例如：在啟動過程中插入 TPM measurement、完全移除某些功能（network stack、HTTP Boot）、用 LinuxBoot 加入完整的啟動驗證邏輯。

---

## 18.6 FSP 與 ME：開源的邊界

### 18.6.1 Intel FSP（Firmware Support Package）

coreboot 的最大限制之一是 DRAM 和 silicon 初始化仍然依賴 Intel 的閉源 binary：

```
coreboot romstage 呼叫 FSP-M：
  romstage.c
    └─ fsp_memory_init()
         └─ 跳進 FspMemoryInit() [FSP-M binary，Intel 閉源]
              - DRAM training
              - memory controller init
              - XMP profile 設定
              回傳控制給 romstage

coreboot ramstage 呼叫 FSP-S：
  ramstage.c
    └─ fsp_silicon_init()
         └─ 跳進 FspSiliconInit() [FSP-S binary，Intel 閉源]
              - PCH init
              - USB/SATA/GPIO 初始化
              - thermal 設定
              回傳控制給 ramstage
```

FSP-M 和 FSP-S 是 ELF binary，Intel 只提供給 OEM/ODM，一般使用者透過主機板廠商或 coreboot 的 `3rdparty/blobs` 取得。你跑的 DRAM init 是一個你看不到 source 的 binary，跑在 ring-0，有完整硬體存取權。

### 18.6.2 Intel ME（Management Engine）

ME 是一個獨立的 RISC 處理器，整合在 Intel PCH 裡，有自己的記憶體和 OS（MINIX 3）。ME firmware 存在 SPI flash 的同一顆晶片上，和 coreboot 共享儲存空間：

```
SPI Flash 分配（典型 16MB）：
+------------------+  高位址
|   Flash Desc     |  Intel Flash Descriptor，定義各 region 邊界
|   (4KB)          |
+------------------+
|   ME Region      |  ME firmware，ME 專屬，host CPU 無法寫入
|   (~6MB)         |  (Flash Descriptor 的 FMBA 設定 region 保護)
+------------------+
|   GbE Region     |  網卡 MAC address 等（部分平台有）
|   (~8KB)         |
+------------------+
|   BIOS Region    |  coreboot/UEFI 住這裡
|   (~10MB)        |
+------------------+  低位址
```

**ME 在 coreboot 之前啟動**：系統上電後，ME 比 host CPU 更早開始跑。ME 初始化完成後才釋放 host CPU 的 reset。這意味著即使你完全信任 coreboot，ME 已經跑了你看不到的程式碼。

**ME neutralization（HAP bit）**：ME 有一個「High Assurance Platform」模式，設定 Flash Descriptor 的特定 bit 後，ME 會在最早期初始化完成後自我停用。me_cleaner 工具可以做到這一點。但這是官方支援有限的做法，某些平台設了 HAP bit 後可能無法正常開機。

**無法完全移除**：ME firmware region 受 Flash Descriptor 保護，host OS 無法寫入（除非你有 flash programmer 工具）。即使你換成 coreboot，ME 仍在那裡。

### 18.6.3 AMD PSP（Platform Security Processor）

AMD 的情形類似，只是換個名字。PSP 是 ARM Cortex-A5 核心，有自己的 TrustZone 環境，負責平台安全初始化、fTPM 等功能。PSP firmware 同樣是閉源 binary，AGESA（coreboot 在 AMD 平台呼叫的 blob）和 PSP firmware 是一體的。

### 結論

coreboot 把信任邊界從「整個 BIOS 是黑盒」推進到「DRAM init 和 silicon init 仍是黑盒」。這是實質的進步，但在 x86 平台上，FSP、ME/PSP 這道牆是現有技術和商業現實下過不去的。

---

## 18.7 coreboot 攻擊面

### 18.7.1 CBFS 未簽章

預設的 coreboot build 裡，CBFS 沒有任何完整性保護。如果攻擊者能寫 SPI flash（條件：OS root 權限 + SPI flash 未寫保護 + Intel BootGuard 未啟用），就能把 payload 換成惡意版本：

```
攻擊步驟：
1. 取得 OS root
2. flashrom -r original.bin        # 讀出現有 flash
3. cbfstool original.bin extract -f payload.elf -n payload
4. 用惡意版本替換 payload.elf
5. cbfstool original.bin remove -n payload
6. cbfstool original.bin add -f evil_payload.elf -n payload -t payload
7. flashrom -w modified.bin        # 寫回
```

這比對付廠商 UEFI 的 BootGuard 容易，因為一般 coreboot 部署沒有對應的硬體驗簽機制。

### 18.7.2 SMM 設置錯誤

coreboot 的 ramstage 負責鎖定 SMRAM。關鍵操作在各 southbridge 的 `smm.c`：

- **D_LCK（SMRAM D_LCK bit）**：鎖定後 SMRAM 對非 SMM 程式碼不可見也不可寫。必須在 ramstage 結束前設定，且只能設定一次。
- **SMBASE**：SMM handler 的基底位址，在 D_LCK 前必須固定。
- **TSEG size**：TSEG（Top of Usable Memory，SMRAM 所在）大小設錯可能導致 SMRAM 被非 SMM 程式碼覆蓋。

coreboot 歷史上有過 SMM 設置的 bug，例如某些 Chromebook 早期 build 的 TSEG 設置問題（已修復）。由於 coreboot 開源，這類 bug 也比較容易被發現和修復。

### 18.7.3 vboot（Chrome OS Verified Boot）

Google 為 Chromebook 開發的 vboot 是目前 coreboot 生態裡最嚴格的安全方案。核心設計：

```
Flash 分區（vboot）：

+----------------------+
|  RO region           |  工廠寫入，硬體寫保護，不可更新
|  - bootblock         |  包含 vboot 驗簽邏輯
|  - romstage（RO）    |
|  - GBB（root key）  |  Google Binary Block，存 root public key
+----------------------+
|  RW_A region         |  可更新，但必須被 RO 驗簽才能執行
|  - romstage（RW）    |
|  - ramstage          |
|  - kernel / payload  |
+----------------------+
|  RW_B region         |  備份，同上
+----------------------+
|  NVRAM region        |  存 boot counter、recovery flag 等
+----------------------+
```

啟動流程：RO bootblock 先執行，讀取 root public key（在 RO 的 GBB 區），驗簽 RW_A 的 romstage，只有驗簽通過才執行 RW 的程式碼。RW 區更新時，需要用對應的 private key 簽章。

這個設計讓 Chromebook 成為 coreboot 安全最佳實踐的代表：即使攻擊者有 flash 寫入能力，沒有 root private key 就無法讓惡意 RW 通過驗簽。

一般 coreboot 部署沒有 vboot，這是一個重要的安全差距。

### 18.7.4 payload 信任問題

coreboot 本身對 payload 完全信任——ramstage 找到 CBFS 裡名為 `payload` 的 file，載入執行，沒有任何白名單或驗簽（vboot 除外）。

攻擊者替換 payload 的後果跟替換 bootloader 一樣嚴重：payload 在 SMRR 鎖定之後執行，但在 OS kernel 之前，有完整的硬體存取權，可以在記憶體裡留下 rootkit、改 boot parameters、繞過 kernel 的 integrity 機制。

---

## 18.8 對比取捨

| 特性 | coreboot + SeaBIOS | coreboot + edk2 | 廠商 UEFI（AMI/Insyde/Phoenix） | LinuxBoot |
|------|-------------------|-----------------|--------------------------------|-----------|
| 可審計性 | 高（全 src 開源） | 高（src 開源） | 無（binary only） | 最高（payload 是 Linux） |
| UEFI 相容性 | 無（legacy BIOS） | 高（標準 UEFI） | 完整（原生） | 低（kexec 後 OS 可 UEFI） |
| Secure Boot | 不支援 | 可支援（edk2） | 原生支援 | 替代方案（GPG + TPM） |
| SMM 設置複雜度 | 中（需要對 southbridge） | 高（coreboot+edk2 雙層） | 由廠商處理（黑盒） | 中 |
| FSP/ME blob 依賴 | 有（Intel/AMD） | 有（Intel/AMD） | 有（通常更多） | 有（Intel/AMD） |
| CBFS 簽章 | 預設無（需 vboot） | 預設無（需 vboot） | N/A | 預設無（HEADS 有） |
| 硬體根（BootGuard） | 需要另外設定 | 需要另外設定 | 廠商通常整合 | 需要另外設定 |
| 適用場景 | legacy 設備、QEMU | 現代 Linux/Windows | OEM 一般消費者設備 | 高安全要求伺服器 |
| 社群活躍度 | 高 | 高 | 不適用 | 中（小眾） |

---

## 18.9 vboot 多 region layout

```
+---------------------------------+  高位址（flash 頂端）
|         RO region               |
|  +--------------------------+   |
|  | WP_RO (write-protected)  |   |
|  |  bootblock               |   |  工廠燒錄，硬體 write-protect screw/fuse
|  |  GBB (root public key)   |   |
|  |  recovery kernel         |   |
|  |  vboot code              |   |
|  +--------------------------+   |
+---------------------------------+
|         RW_SHARED               |
|  VBLOCK_DEV (dev key)           |
|  SHARED_DATA                    |
+---------------------------------+
|         RW_A                    |
|  VBLOCK_A (signature block)     |  RO 用 root key 驗這裡的簽章
|  FW_MAIN_A (ramstage+payload)   |  通過才執行
+---------------------------------+
|         RW_B                    |
|  VBLOCK_B                       |  備份，更新失敗時 fallback
|  FW_MAIN_B                      |
+---------------------------------+
|         RW_NVRAM                |
|  boot counter                   |  記錄嘗試次數，超過切 recovery
|  recovery reason code           |
+---------------------------------+  低位址
```

vboot 的 RO 區一旦出廠就被 write-protect screw（實體螺絲）或 fuse 鎖定。攻擊者即使有 flashrom 寫入能力，也無法覆蓋 RO 區（除非拆掉 write-protect screw）。這把攻擊成本從軟體層推到了實體層。

---

## 18.10 踩雷

**踩雷 1：coreboot 開源不等於整個平台開源**

「我用 coreboot，所以我的韌體是透明的」——這個結論跳過了 FSP 和 ME。Intel FSP-M/FSP-S 是閉源 binary，在你的 DRAM init 和 silicon init 階段以 ring-0 執行。ME 比你的整個 coreboot 都先啟動。你審計的是 coreboot src，但你沒審計到整個系統。正確的說法是：coreboot 讓你能審計 firmware 邏輯，但 silicon 層的 blob 不在這個範圍內。

**踩雷 2：cbmem 是 information leak**

`cbmem -1` 在 OS root 下執行，可以讀到 coreboot 啟動時留在 CBMEM（一塊保留記憶體）裡的完整 boot log。這個 log 包含：DRAM init 的診斷訊息（timing、SPD 內容）、各裝置 BAR 分配、SMBASE 設定的相關訊息。

在多用戶伺服器環境或攻擊者已取得 root 但尚未提升到韌體層的情境，cbmem 提供了大量硬體拓撲資訊，可以用來輔助後續攻擊（例如確認 SMRAM 位置）。生產環境應該考慮在 OS 啟動後清除或限制 cbmem 的存取。

**踩雷 3：CBFS 未簽章讓某些 coreboot 部署比廠商 UEFI 更容易被植入**

廠商 UEFI 配合 Intel BootGuard 時，flash 的 BIOS region 有 ACM 做硬體驗簽，即使攻擊者改了 flash，BootGuard 會在啟動時偵測到並拒絕執行。

一般 coreboot 部署沒有對應機制：CBFS 無簽章、BootGuard ACM 通常需要主機板廠商在製造時燒錄（OEM only），社群版 coreboot 主機板不一定有。結果是：有 flash 寫入權限的攻擊者可以直接換 payload，沒有任何硬體驗證擋住。這不是 coreboot 的設計缺陷，而是部署時必須考慮的現實差距——需要 vboot 或等效機制填補。

**踩雷 4：payload 切換方便，惡意切換一樣方便**

coreboot 的 payload 替換設計得很方便：`cbfstool image.bin remove -n payload && cbfstool image.bin add -f new.elf -n payload -t payload`。這對系統管理員是好事，對攻擊者也是好事。coreboot 本身對 payload 沒有白名單機制——只要能寫 flash，換什麼 payload 都可以。對比廠商 UEFI 的 Secure Boot db + BootGuard 組合，這個差距在沒有 vboot 的情況下是真實存在的威脅。高安全需求的 coreboot 部署必須明確處理這一點。

---

## 18.11 進階延伸

- **Heads（LinuxBoot 實作）**：研究 HEADS 的 boot flow，理解它如何用 TPM PCR + GPG 做 measured + verified boot，以及它如何處理 anti-evil-maid 場景。對比 vboot 的設計決策。
- **Intel BootGuard 的 coreboot 相容性**：BootGuard 的 Initial Boot Block (IBB) 機制和 coreboot 的 bootblock 有衝突——BootGuard 要求 IBB 符合特定格式，部分主機板無法同時用 BootGuard 和 coreboot。研究 Google 如何在 Chromebook 上解決這個問題。
- **me_cleaner 與 ME neutralization**：`https://github.com/corna/me_cleaner`，研究 HAP bit 的機制、哪些平台支援、neutralize 後的 ME 行為（部分功能失效，如 AMT、PTT/fTPM）。
- **CHIPSEC 對 coreboot 系統的測試**：CHIPSEC 的 `chipsec_main -m common.smm` 等模組可以在 OS 層測試 SMM 設置是否正確，研究如何在 coreboot 系統上跑完整的 CHIPSEC 測試套件。

---

## 18.12 動手練習

這是概念章，以下為理解導向的實作練習，不需要真實硬體：

1. **CBFS 結構探索**：下載 coreboot 並用 `make menuconfig` 設定 QEMU/x86 target，執行 `make`，取得 build/coreboot.rom。用 `cbfstool coreboot.rom print` 列出所有 component，比對各 file 的 offset、size、type，畫出這顆 ROM 的實際 layout，對照本章的 ASCII 圖驗證理解。

2. **boot flow 追蹤**：用 QEMU 執行 coreboot（`qemu-system-x86_64 -bios build/coreboot.rom -serial stdio`），觀察 serial log，找出 bootblock → romstage → ramstage → payload 各階段的切換訊息。進入 OS 後執行 `sudo cbmem -1`，讀完整 boot log，找出 DRAM init、PCI enum、SMM 初始化的相關記錄。

3. **coreboot source 審計練習**：在 coreboot source tree 裡找到 `src/southbridge/intel/` 下某個 chipset（例如 `lynxpoint/`）的 `smm.c`，閱讀 `smm_lock()` 或等效函式，確認 D_LCK 設置的位置和條件。對照 Intel datasheet 驗證 bit 定義是否正確。

4. **vboot 文件研讀**：閱讀 `src/security/vboot/README.md` 和 Google 的 Verified Boot design doc（`https://www.chromium.org/chromium-os/chromiumos-design-docs/verified-boot/`），畫出 RO → RW_A 的驗簽流程圖，標出每個步驟用到的 key 和 signature，確認自己能解釋 root key、recovery key、developer key 的分工。

---

## 18.13 本章重點

- coreboot 把 firmware 啟動分成 bootblock（XIP/CAR 初始化）、romstage（DRAM init）、ramstage（device enum）、payload 四個階段，對應 UEFI PI 的 SEC/PEI/DXE/BDS。
- CBFS 是 coreboot 用來組織 flash 內容的格式，沒有預設簽章機制。
- coreboot 可以用 edk2 當 payload 提供 UEFI 介面，但啟動路徑與標準 UEFI PI 不同，SMM 初始化語義有差異。
- 開源韌體的主要安全優勢是可審計性和供應鏈透明度，但在 x86 平台上，FSP（DRAM init）和 ME/PSP 仍是閉源 blob，是信任邊界的現實上限。
- 未加 vboot 的 coreboot 部署，CBFS 無完整性保護，有 flash 寫入權限就能換 payload。
- vboot 是目前 coreboot 生態裡最完整的安全方案，以 RO/RW 分區 + root key 驗簽 + write-protect 實現硬體信任根。
- `cbmem -1` 在 OS 層可讀 boot log，包含 DRAM init 診斷等敏感資訊，是潛在的 information leak。
- LinuxBoot + HEADS 是高安全需求場景的選擇，把 firmware 邏輯搬到 Linux userspace，用 TPM + GPG 做 measured + verified boot。

---

## 18.14 自我檢核

- [ ] 能從 CPU reset vector 開始，逐步說明 coreboot 的 bootblock → romstage → ramstage → payload 流程，並解釋每個階段為什麼存在。
- [ ] 能解釋 cache-as-RAM (CAR) 是什麼、為什麼 romstage 需要它。
- [ ] 能說明 coreboot + edk2 payload 的啟動路徑與標準 UEFI PI 的差異，並指出 SMM 初始化語義上的不同。
- [ ] 能指出 Intel FSP-M、FSP-S、ME 在 coreboot 架構裡的位置，並解釋為什麼它們是信任邊界的現實上限。
- [ ] 能解釋 CBFS 未簽章的安全含義，以及在沒有 vboot 的情況下攻擊者如何替換 payload。
- [ ] 能描述 vboot 的 RO/RW 分區設計，解釋 root key 驗簽 RW region 的流程，以及 write-protect 在其中的作用。
- [ ] 能比較 SeaBIOS、edk2、LinuxBoot、GRUB2 四種 payload 的適用場景和安全特性差異。
- [ ] 知道 `cbmem -1` 可以讀到什麼、為什麼這在安全上需要注意。

---

## 18.15 延伸閱讀

1. **coreboot 官方文件與 wiki**
   - 位置：`https://doc.coreboot.org/` 及 `https://www.coreboot.org/Board_status`
   - 內容：完整的 bootblock/romstage/ramstage 架構說明、CBFS 格式規範、各主機板的支援狀態與 blob 需求列表。
   - 相關性：理解 coreboot 架構最直接的一手資料，board status 頁面明確列出哪些平台需要哪些 blob，是評估開源程度的基準。

2. **Google ChromeOS Verified Boot Design**
   - 位置：`https://www.chromium.org/chromium-os/chromiumos-design-docs/verified-boot/` 及 Chromium OS source 的 `src/platform/vboot_reference/`
   - 內容：vboot 的完整設計文件，包含 key hierarchy、flash layout、recovery 流程、developer mode 的安全降級設計。
   - 相關性：vboot 是目前 coreboot 生態最嚴格的安全實作，理解它的設計決策才能知道一般 coreboot 部署差了什麼。

3. **Heads project（LinuxBoot 高安全實作）**
   - 位置：`https://github.com/osresearch/heads` 及 `https://osresearch.net/`
   - 內容：LinuxBoot 在高安全場景的具體實作，包含 TPM measured boot、GPG 驗簽 kernel、anti-evil-maid 機制、Nitrokey/YubiKey 整合的詳細說明。
   - 相關性：對比 vboot（硬體廠商導向）和 HEADS（開放社群高安全導向）的設計取捨，理解開源韌體安全的不同路徑。

4. **Intel FSP Specification**
   - 位置：`https://cdrdv2.intel.com/v1/dl/getContent/644500`（Intel Resource & Design Center）
   - 內容：FSP-M、FSP-T、FSP-S 的 API 規範、呼叫慣例（UPD/HOB 介面）、記憶體範圍要求。
   - 相關性：理解 coreboot romstage 呼叫 FSP 時的介面邊界，知道 blob 的輸入輸出是什麼，有助於評估 blob 的攻擊面。

5. **me_cleaner 與 Intel ME 分析**
   - 位置：`https://github.com/corna/me_cleaner`；Positive Technologies 的 "How to Hack a Turned-Off Computer, or Running Unsigned Code in Intel ME"（2017）
   - 內容：ME 的啟動時機、HAP bit 的作用機制、me_cleaner 的實作原理，以及 Positive Technologies 揭露的 ME JTAG 研究。
   - 相關性：理解 ME 為什麼是 coreboot 信任邊界的硬牆，以及目前社群對 ME 的了解程度（比想像中更多，但遠未到透明）。

---

→ [下一章](./19-android-verified-boot.md)
