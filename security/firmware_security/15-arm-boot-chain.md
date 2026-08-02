# Ch 15 — ARM 開機信任鏈：BL1→BL2→BL31→BL33

> **目標**：從 reset vector 到 OS handoff，理解 ARM Trusted Firmware-A（TF-A）各階段的職責分工、TBBR 驗簽鏈的運作邏輯，並能與 x86 UEFI/SMM 對照，判斷哪些環節是攻擊者的目標。

---

## 15.1 為什麼需要信任鏈——ARM 與 x86 的設計出發點不同

x86 PC 的信任鏈從 1970 年代的 BIOS 演進到 UEFI，再到 Intel Boot Guard、Secure Boot，是「先有混亂再加補丁」的產物。SoC 廠自己燒 BootROM，UEFI 由 AMI/Phoenix 提供，OS loader 再接手，整條鏈的邊界歷史上就很模糊。

ARM 嵌入式走的是另一條路。行動裝置（手機、平板）從一開始就需要防止刷機、保護 DRM 金鑰、隔離 TrustZone 安全世界（Secure World）。ARM 在 2013 年整理出 **Trusted Board Boot Requirements（TBBR）**規範，明確定義每個 bootloader 階段的驗簽義務，Arm Trusted Firmware-A（TF-A）是官方參考實作。

**OTP fuse 上的 ROTPK**

信任鏈要有起點。ARM 規範這個起點叫做 **根公鑰（Root of Trust Public Key, ROTPK）**，燒在一次性可程式記憶體（One-Time Programmable, OTP）fuse 裡。OTP 物理上只能從 0 寫到 1，無法反轉，因此：

- ROTPK 一旦燒入，攻擊者就算拿到 root shell 也無法替換它。
- BL1（BootROM）程式碼本身也燒在 ROM 裡，同樣不可改。
- 兩者合起來形成「硬根信任（Hardware Root of Trust）」，後續每一層都靠前一層驗過才執行。

這與 x86 Intel Boot Guard 的 Key Manifest（KM）燒在 ME fuse 概念相同，但 ARM 的規範在嵌入式 SoC 上普及程度更高，因為 SoC 廠直接整合了 TF-A 而不是把責任外包給 IBV（Independent BIOS Vendor）。

---

## 15.2 直覺建立——例外層級與兩個世界

### AArch64 例外層級（Exception Level, EL）

```
高特權
  |
  |  EL3  Secure Monitor  (TF-A BL31 常駐於此)
  |        - 唯一能切換 Secure/Non-secure 世界的層級
  |        - 透過 SCR_EL3.NS bit 控制世界歸屬
  |
  |  EL2  Hypervisor       (KVM / Xen 跑在此)
  |        - Non-secure 側：虛擬化 guest OS
  |        - Secure 側 (ARMv8.4+)：Secure Partition Manager
  |
  |  EL1  OS Kernel        (Linux / OP-TEE 跑在此)
  |        - Non-secure：普通 Linux
  |        - Secure：OP-TEE (BL32)
  |
  |  EL0  User Application (普通 App / TA Trusted App)
  |
低特權
```

### Secure World / Non-secure World 切割

```
+---------------------------+---------------------------+
|      Non-secure World     |       Secure World        |
|                           |                           |
|  EL0-NS  User Apps        |  EL0-S   Trusted Apps     |
|  EL1-NS  Linux kernel     |  EL1-S   OP-TEE (BL32)   |
|  EL2-NS  KVM/Xen          |  EL2-S   SPM (v8.4+)     |
|                           |                           |
+---------------------------+---------------------------+
             |                           |
             +----------- EL3 ----------+
                    Secure Monitor
                     (TF-A BL31)
                    SCR_EL3.NS=1/0
```

EL3 本身屬於 Secure 狀態，但它不是「Secure World 的 EL1」——EL3 是超越兩個世界的控制層。這個概念常被混淆，後面踩雷章節會再強調。

---

## 15.3 開機各階段核心概念

### BL1 — AP Trusted ROM / BootROM

BL1 是 SoC 廠燒在片上 ROM 的第一段程式碼。特性：

- **不可更新**。OEM 收到晶片時 BL1 就已定版，攻擊者即使拿到實體也無法改寫。
- 從 reset vector（AArch64 通常是 `0x0` 或 SoC 規格定義的位址）開始執行。
- 初始化最小 CPU 狀態：設定 `VBAR_EL3`（向量表基底）、初始化 stack、關閉 MMU/cache，讓後續程式碼可以安全執行。
- 載入 BL2 到 SRAM（此時 DRAM 尚未初始化），用 ROTPK 驗簽 BL2 的 X.509 憑證。
- 驗過才跳轉，驗不過就掛住（halt）或進入 RMA 流程。

BL1 也是攻擊者最感興趣的目標之一：ROM 不可改，但如果能找到 BL1 解析 FIP header 或憑證時的漏洞（buffer overflow in certificate parser），可能在驗簽完成前就劫持控制流。歷史上 MediaTek、Qualcomm 的 BootROM 都出現過類似漏洞。

### BL2 — Trusted Boot Firmware

BL2 是第一個「可更新」的安全韌體，儲存在 Flash 上，需被 BL1 驗簽才執行。

**主要職責**：

1. 初始化 DRAM（DDR init），讓後續韌體有足夠記憶體。
2. 解析 **固件映像包（Firmware Image Package, FIP）**。FIP 是 TF-A 定義的容器格式，把 BL31/BL32/BL33 打包在單一二進位檔，每個映像都有對應的 UUID 和 X.509 憑證。
3. 依序驗簽並載入 BL31、BL32（若存在）、BL33。
4. 把控制權移交給 BL31。

FIP 結構示意：

```
FIP 容器
+-----------------------------+
| FIP ToC (Table of Contents) |
|  entry: UUID=BL31, offset=X |
|  entry: UUID=BL32, offset=Y |
|  entry: UUID=BL33, offset=Z |
+-----------------------------+
| BL31 image + cert           |
| BL32 image + cert (opt.)    |
| BL33 image + cert           |
+-----------------------------+
```

工具：`fiptool` 可以封裝、拆解、列出 FIP 內容，安全研究時常用來確認生產裝置的韌體組成。

### BL31 — EL3 Runtime Firmware / Secure Monitor（TF-A 核心）

BL31 是 TF-A 的核心，也是整個信任鏈最關鍵的常駐元件。

- **永遠常駐記憶體**。OS 跑起來之後 BL31 仍在某塊保留記憶體裡（通常是 SRAM 或 DRAM 最高位址段），任何人不得覆寫。
- 以 **Secure Monitor Call（SMC）** 介面對 EL1/EL2 提供服務（稱為 SMCCC——SMC Calling Convention）。
- 負責電源管理（PSCI：Power State Coordination Interface）、硬體安全配置（GIC 中斷路由、TrustZone 保護等）。
- 切換 Secure/Non-secure 世界：設定 `SCR_EL3.NS`，再做 `ERET` 降階。

**BL31 記憶體保護**：通常透過 TrustZone Address Space Controller（TZASC）把 BL31 所在 DRAM 區段設定為 Secure-only，Non-secure world 存取會觸發 abort。

### BL32 — Secure OS / OP-TEE（可選）

BL32 是在 Secure world EL1 跑的作業系統，最常見的開源實作是 **OP-TEE**（Open Portable Trusted Execution Environment）。

- 提供 **可信任應用程式（Trusted Application, TA）** 的執行環境：DRM 解密、指紋驗證、安全支付等。
- 與 Non-secure world 的溝通透過 SMC → BL31 → BL32 轉介。
- BL32 是可選元件，沒有它系統照常啟動，但 TrustZone 功能受限。

BL32 不在信任鏈的驗簽主路徑上（驗簽由 BL2 做），但它的攻擊面仍然重要：TA 的實作漏洞可能讓攻擊者從 Non-secure 世界進入 Secure world。

### BL33 — Non-trusted Firmware（U-Boot 或 UEFI）

BL33 是「第一個不受 TF-A 信任」的組件——仍會被 BL2 驗簽，但執行後它就在 Non-secure world，無法再存取 Secure world 的資源。

- 嵌入式系統常用 **U-Boot**；AOSP/手機平台用廠商客製 bootloader；伺服器平台用 **EDK2（UEFI）**。
- 負責載入 Linux kernel（或 Android boot image），設定 DTB（Device Tree Blob），最後跳轉到 OS。
- 從此刻起信任鏈的「硬保護」交棒給 OS 層（Secure Boot for Linux、dm-verity 等）。

---

## 15.4 TBBR 信任鏈逐級驗簽

**Trusted Board Boot Requirements（TBBR）**是 ARM 規範文件（DEN0006），規定每個 BL 階段用 X.509 憑證鏈驗簽下一階段。

驗簽鏈（Chain of Trust）：

```
OTP fuse
  |
  |  燒入 ROTPK（Root of Trust Public Key，SHA-256 hash）
  v
BL1 (ROM)
  |  內含 ROTPK hash
  |  讀取 BL2 的 X.509 trusted boot certificate
  |  從憑證取出 BL2 content hash
  |  SHA-256(BL2 image) == cert 中的 hash ?
  |     YES --> 跳轉 BL2
  |     NO  --> HALT
  v
BL2
  |  讀取 FIP 中每個映像的憑證鏈
  |  憑證由 ROTPK 簽發（或中繼 CA 簽發）
  |  驗 BL31 cert --> SHA-256(BL31) 比對
  |  驗 BL33 cert --> SHA-256(BL33) 比對
  |  （BL32 同理，若存在）
  v
BL31 / BL33 / BL32  (已驗過，可執行)
```

X.509 憑證在 TF-A 裡有兩種角色：
- **Content Certificate**：內嵌映像的 hash，確認映像完整性。
- **Key Certificate**：攜帶下一層的公鑰，形成公鑰鏈。

`cert_create` 工具（TF-A 源碼 `tools/cert_create`）可在開發時生成這些憑證，量產時需用 HSM 保護私鑰。

---

## 15.5 對照 x86 信任鏈

| 角色 | ARM TF-A | x86 對應 |
|------|----------|----------|
| 不可改 ROM 程式碼 | BL1（AP Trusted ROM） | Intel ACM / AMD PSP BootROM |
| 根信任錨點 | ROTPK 燒在 OTP fuse | Intel Boot Guard Key Manifest 燒在 ME fuse |
| 初始 DRAM init | BL2 前段 | MRC（Memory Reference Code）by FSP |
| 常駐安全監控層 | BL31（EL3，常駐） | SMM（System Management Mode，Ring -2） |
| 安全 OS | BL32 / OP-TEE | Intel TXT / SGX enclave（角色不完全等同） |
| Non-secure bootloader | BL33（U-Boot / EDK2） | UEFI DXE phase（BDS） |
| OS 層驗簽 | BL33 啟動後的 UEFI Secure Boot | UEFI Secure Boot（db/dbx） |
| 驗簽規範 | TBBR（DEN0006） | UEFI Secure Boot + Intel Boot Guard spec |

差異要點：
- BL31 與 SMM 都「常駐不離開記憶體」，但 BL31 是 ARM 標準介面，SMM 是 Intel 私有機制，兩者的攻擊面大小不同。
- x86 沒有 TrustZone 的世界切換概念；Intel TXT/SGX 提供部分類似功能但架構差異很大。
- ARM 的每個 BL 都有獨立 X.509 憑證，x86 Boot Guard 只保護到 UEFI IBB（Initial Boot Block），後續靠 Secure Boot。

---

## 15.6 真跑驗證——QEMU 跑 AArch64 UEFI

完整 TF-A 鏈（BL1→BL2→BL31→BL33）需要從 TF-A 源碼編譯，並搭配 QEMU `virt` machine 的 flash 配置，步驟繁瑣（詳見下一章）。本節先用 QEMU 搭配 EDK2/AAVMF 驗證 AArch64 UEFI 環境可正常運作。

**指令**（需安裝 `qemu-system-aarch64` 與 `ovmf`/`qemu-efi-aarch64`）：

```bash
qemu-system-aarch64 \
  -machine virt \
  -cpu cortex-a57 \
  -bios /usr/share/qemu-efi-aarch64/QEMU_EFI.fd \
  -nographic \
  -m 512M
```

**實際輸出**（節錄）：

```
[2J[01;01H[=3h[2J[01;01H
BdsDxe: failed to load Boot0001 "UEFI Misc Device" from
        VenHw(93E34C7E-B50E-11DF-9223-2443DFD72085,00): Not Found

>>Start PXE over IPv4.
```

**這段輸出說明什麼**

`QEMU_EFI.fd` 是 EDK2/AAVMF 編譯出的 AArch64 UEFI 固件，相當於 BL33 層（Non-secure world bootloader）。它完成了 UEFI DXE 初始化，進入 **BDS（Boot Device Selection）** 階段（`BdsDxe`）。

- `Boot0001 "UEFI Misc Device" ... Not Found`：BDS 嘗試從 NVRAM 記錄的啟動選項開機，但 QEMU 沒掛任何磁碟，找不到。
- `Start PXE over IPv4`：BDS 按優先順序 fallback 到網路開機（PXE），同樣因為 QEMU 沒設網路映像而失敗。

這個輸出**證明 AArch64 UEFI 環境正常**：CPU 在 AArch64 下執行，UEFI 固件完整跑完了 SEC→PEI→DXE 階段，到達 BDS。

**這不是完整的 TF-A 信任鏈**。AAVMF 是一個單體 UEFI 固件，沒有 TF-A BL1/BL2/BL31。完整的 TF-A + U-Boot + Linux 鏈需要從 TF-A 源碼（`git clone https://git.trustedfirmware.org/TF-A/trusted-firmware-a.git`）編譯，並配合 QEMU `virt` machine 的 `-drive if=pflash` 掛 BL1 ROM 映像，設定細節見第 16 章（未實測，本機環境缺少完整 cross-toolchain）。

---

## 15.7 底層執行流程（ASCII 圖）

```
Reset
  |
  v
+------------------+
| BL1 (AP ROM)     |  位址：SoC reset vector（e.g. 0x0 或 0xFFFF0000）
| - 初始化 CPU 狀態  |  執行環境：片上 SRAM，無 DRAM
| - 驗簽 BL2        |
+------------------+
  |  BL2 load to SRAM
  v
+------------------+
| BL2              |  執行環境：片上 SRAM
| - DRAM 初始化     |
| - 解析 FIP        |
| - 驗簽 BL31/32/33 |
| - load 到 DRAM   |
+------------------+
  |  EL3 跳轉到 BL31
  v
+------------------+
| BL31 (常駐 EL3)  |  位址：DRAM 保留區（TZASC 保護）
| - GIC 初始化      |
| - SMC handler 就位|
| - PSCI 就位       |
+------------------+
  |  (若有 BL32) 先跳 BL32 初始化，再回 BL31
  |
  |  SCR_EL3.NS=0 → 進入 Secure world EL1
  |  BL32 init，設定 Secure world 向量表
  |  BL32 完成後 SMC 回 BL31
  |
  |  SCR_EL3.NS=1 → 進入 Non-secure EL1
  v
+------------------+
| BL33 (U-Boot /   |  Non-secure world EL2 或 EL1
|  EDK2 UEFI)      |
| - 載入 kernel     |
| - DTB 設定        |
| - Jump to kernel  |
+------------------+
  |
  v
Linux Kernel (EL1-NS) / Android (EL1-NS)
  |  透過 SMC 呼叫 BL31 取得 PSCI 服務
  v
BL31 (常駐，永遠回應 SMC)
```

---

## 15.8 對比取捨

| 比較面向 | ARM BL31 / TF-A | x86 SMM / SMI handler |
|----------|-----------------|----------------------|
| 常駐位置 | DRAM 保留區，TZASC 保護 | SMRAM，由 TSEG/SMRR 保護 |
| 進入方式 | `SMC` 指令（SMCCC 規範） | SMI 中斷（軟硬體觸發） |
| 規範化程度 | SMCCC 是公開 ARM 規範 | SMI handler 廠商自訂，格式不統一 |
| 攻擊歷史 | CVE 見 TF-A Security Advisories | SMM 漏洞長期是 UEFI 安全研究熱點 |
| 對應 | BL31 | SMM（角色類似，但機制不同） |

| 比較面向 | ARM ROTPK / OTP fuse | x86 Intel Boot Guard KM |
|----------|----------------------|--------------------------|
| 儲存位置 | SoC OTP fuse | ME (Management Engine) fuse |
| 公鑰格式 | SHA-256 hash of RSA/EC public key | RSA-2048 公鑰 hash |
| 可撤銷？ | 否（fuse 一次性） | 否（fuse 一次性） |
| 驗簽對象 | BL1 驗 BL2 憑證 | ACM 驗 IBB（UEFI Initial Boot Block） |

| 比較面向 | OP-TEE（BL32） | Intel TXT / SGX |
|----------|----------------|-----------------|
| 隔離機制 | TrustZone 硬體世界切換 | 測量啟動 / enclave 記憶體加密 |
| 攻擊面 | TA 實作漏洞、SMC 介面 | SGX side-channel（Spectre 等） |
| 開源程度 | OP-TEE 完全開源 | TXT ACM 封閉，SGX 開放 SDK |

---

## 15.9 踩雷

**踩雷 1：BL31 記憶體位址衝突**

BL31 需要放在某個保留記憶體範圍，編譯時由 `BL31_BASE` 巨集指定。常見問題：U-Boot 或 Linux 的 reserved memory 節點沒有排除這塊，導致 OS 啟動後把 BL31 程式碼或資料覆寫，造成 SMC 呼叫（如 CPU hotplug）隨機崩潰。

除錯方式：查 TZASC 是否正確設定；在 Linux DTB 中加 `reserved-memory` 節點把 BL31 範圍標為 `no-map`。生產韌體這個問題要在晶片設計時就規劃好，否則後期改動 DRAM map 風險極高。

**踩雷 2：ROTPK 燒錯無法回頭**

OTP fuse 的物理特性決定了「燒錯就是永遠錯」。常見情境：工程樣品用了測試金鑰，量產前忘記換正式 ROTPK，或把 public key hash 算錯（例如對 DER 格式而非 raw key bytes 算 hash）。

燒錯的後果：裝置永遠無法通過 BL1 驗簽，每次上電就 halt，等同磚化。規避方式：在 fuse 燒入流程加 dry-run 驗證步驟；先用 SW-RoT（軟體模擬）測試，確認流程無誤才燒硬體。

**踩雷 3：BL32 可選 ≠ 攻擊面可忽略**

BL32 是可選元件，沒有它系統照常運作。有些產品為了簡化開發就不配置 BL32，但這不代表 Secure world 沒有攻擊面——BL31 本身的 SMC handler 就是攻擊面。更常見的問題是：有配置 BL32 但 TA（Trusted Application）品質差，存在堆疊溢位或格式字串漏洞，讓攻擊者從 Non-secure world 透過 `tee-supplicant` 呼叫鏈打進 Secure world。OP-TEE 的歷史 CVE（如 CVE-2019-1010298）都是這類型。

**踩雷 4：EL3 ≠ Secure World**

這是初學者最常犯的概念錯誤。EL3 是例外層級，Secure World 是 TrustZone 的世界屬性（由 `SCR_EL3.NS` 控制），兩者是正交的維度。

- EL3 本身**永遠是 Secure 狀態**（NS=0），但它不屬於「Secure World OS 那一層」。
- Secure World 的 EL1（OP-TEE）和 Non-secure World 的 EL1（Linux）都是 EL1，差別只在 NS bit。
- 攻打 BL31 不等於直接進 Secure World，而是取得了世界切換器的控制權，後果更嚴重。

---

## 進階延伸

- **TF-A 原始碼架構**：`bl1/`、`bl2/`、`bl31/` 各目錄分別對應本章各階段，閱讀 `plat/qemu/` 可看到 QEMU platform 的 memory map 和 FIP 組態，是最快速理解架構的方式。
- **SMCCC（SMC Calling Convention）**：ARM 規範 DEN0028，定義 32/64-bit SMC 的暫存器用法、function ID 格式（包含 Trusted OS、OEM、SiP 命名空間），閱讀此規範才能理解 BL31 攻擊面的全貌。
- **TZASC（TrustZone Address Space Controller）**：ARM CoreLink TZC-400 技術手冊，說明如何用硬體把 DRAM 分割成 Secure/Non-secure 區域，這是 BL31 記憶體保護的底層機制。

---

## 動手練習

1. 安裝 `qemu-system-aarch64` 與 `qemu-efi-aarch64`，執行本章的 QEMU 指令，觀察 BDS 輸出。試著加上 `-device virtio-blk-device,drive=hd0 -drive if=none,id=hd0,file=disk.img` 掛一個空磁碟，看 UEFI 是否改變 boot 順序。

2. 下載 TF-A 源碼（`git clone https://git.trustedfirmware.org/TF-A/trusted-firmware-a.git`），閱讀 `docs/getting_started/build-options.rst` 中關於 `TRUSTED_BOARD_BOOT` 和 `GENERATE_COT` 的選項說明，理解哪些選項打開才會啟用完整 TBBR 驗簽鏈。

3. 用 `fiptool` 拆解一個公開的 ARM 開發板 FIP 映像（例如 Raspberry Pi 4 的 `bl31.bin` 加上 U-Boot 封裝的 FIP），列出各 UUID 對應的映像，確認 BL31/BL33 各自的大小與 hash。

4. 查閱 TF-A Security Advisory 頁面（`trustedfirmware.org`），找一個 BL31 的 CVE，整理：漏洞類型、觸發路徑（哪個 SMC function ID）、修補方式。

---

## 本章重點

- ARM 信任鏈的硬根（ROTPK）燒在 OTP fuse，搭配 BL1 ROM 形成不可篡改的起點。
- BL1（ROM）→ BL2（Flash，DRAM init + FIP 解析）→ BL31（EL3 常駐）→ BL33（Non-secure bootloader）是標準 TF-A 四階段。
- TBBR 規範每個階段用 X.509 憑證鏈逐級驗簽，憑證鏈根植於 ROTPK。
- BL31 常駐 EL3，透過 SMC（SMCCC）提供 PSCI 和 Secure Monitor 服務，等同 x86 的 SMM 但介面是公開規範。
- EL3（例外層級）與 Secure World（TrustZone 世界）是兩個正交概念，混淆兩者會導致攻擊面分析錯誤。
- QEMU + AAVMF 可快速驗證 AArch64 UEFI（BL33 層）環境；完整 TF-A 鏈需從源碼編譯，見第 16 章。

---

## 自我檢核

- [ ] 我能說出 BL1 到 BL33 各階段在記憶體的執行位置（ROM / SRAM / DRAM）。
- [ ] 我理解 ROTPK 為何燒在 OTP fuse，而不是存在 Flash 裡。
- [ ] 我能畫出 AArch64 EL0~EL3 與 Secure/Non-secure World 的正交關係圖。
- [ ] 我知道 FIP（Firmware Image Package）的用途，以及 `fiptool` 能做什麼。
- [ ] 我能解釋為何 QEMU AAVMF 的輸出屬於 BL33 層，而非完整 TF-A 鏈。
- [ ] 我理解 BL31 與 x86 SMM 在功能上的類比，以及架構上的差異。
- [ ] 我能描述至少兩種針對信任鏈各層的真實攻擊類型（BootROM 漏洞 / TA 漏洞）。

---

## 延伸閱讀

1. **ARM Trusted Firmware-A 官方文件** — `trustedfirmware.org/docs/tf-a/`
   - 什麼：TF-A 的設計理念、各 BL 職責、TBBR 實作細節、Security Advisory 列表。
   - 關聯性：本章所有概念的第一手規範來源，做安全研究前必讀。

2. **ARM DEN0028（SMCCC Specification）** — `developer.arm.com/documentation/den0028`
   - 什麼：SMC Calling Convention，定義 BL31 SMC 介面的完整格式，包含 function ID 命名空間（Trusted OS / OEM / SiP）。
   - 關聯性：理解 BL31 攻擊面的前提；fuzzing SMC 介面前必須先讀這份規範。

3. **ARM DEN0006（TBBR Specification）** — `developer.arm.com/documentation/den0006`
   - 什麼：Trusted Board Boot Requirements 規範全文，定義憑證鏈格式、驗簽流程、各映像的 UUID。
   - 關聯性：理解 BL1→BL2→BL31 驗簽鏈的規範依據，cert_create 工具的設計直接對應此文件。

4. **「Breaking Samsung's ARM TrustZone」（Black Hat 2019）** — `i.blackhat.com/USA-19/Thursday/us-19-Peterlin-Breaking-Samsungs-ARM-TrustZone.pdf`
   - 什麼：三星 Exynos 平台 OP-TEE（BL32）的多個漏洞分析，包含從 Non-secure world 透過 SMC 打到 Secure world 的完整攻擊鏈。
   - 關聯性：本章踩雷 3（BL32 攻擊面）的實際案例，說明「BL32 可選」不代表可以忽視其安全性。

5. **「arm64 boot protocol」— Linux kernel 文件** — `kernel.org/doc/html/latest/arm64/booting.html`
   - 什麼：Linux AArch64 kernel 對 bootloader 的要求：記憶體狀態、暫存器初始值、DTB 傳遞方式。
   - 關聯性：BL33（U-Boot/UEFI）跳轉到 kernel 時必須滿足這些條件；嵌入式開發踩的「kernel 不啟動」問題多半出在這裡。

---

→ [下一章](./16-tf-a-internals.md)
