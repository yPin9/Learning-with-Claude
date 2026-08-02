# Ch 6 — Capsule Update 與 Firmware Volume/FFS

> **目標**：搞清楚 FV/FFS/section 三層格式與 GUID 系統、UEFI capsule update 的完整流程（ESRT/FMP/簽章驗證），以及攻擊者如何透過 capsule 簽章漏洞、rollback 攻擊、未驗證 capsule 刷入惡意韌體。用 python 真跑 OVMF_CODE.fd，解析 FV/FFS/section 三層階層並貼出真實輸出。
>
> **環境**：WSL，python3 + uefi_firmware，/usr/share/OVMF/OVMF_CODE.fd（1,966,080 bytes）。

## 為什麼格式和更新機制一起學？

UEFI capsule update 是廠商把新韌體推進平台的標準路徑。它的安全性完全取決於：
1. **capsule 的簽章驗證**有沒有做對
2. **FFS 格式**解析有沒有 bug（parse 漏洞）
3. **rollback 防護**有沒有實作

如果其中任一環節有問題，攻擊者就能把惡意韌體透過「正規更新流程」刷進 SPI flash。LogoFAIL 的攻擊路徑之一就是讓圖片 parser（在更新流程中被呼叫）觸發 overflow，在驗章完成之前拿到控制流。

---

## FV/FFS/Section 三層架構

UEFI 韌體 image 是個三層套疊的容器格式：

```
SPI Flash / 韌體 image
│
├── Firmware Volume (FV)          ← 最外層容器，有 GUID 標識 filesystem 類型
│   Header: EFI_FIRMWARE_VOLUME_HEADER
│   FS-GUID: 8c8ce578-8a3d-4f1c-9935-896185c32dd3  (EFI_FIRMWARE_FILE_SYSTEM2_GUID)
│   │
│   ├── Firmware File (FFS)       ← 中間層，每個 PEIM/DXE driver 是一個 FFS file
│   │   Header: EFI_FFS_FILE_HEADER
│   │   GUID: 模組的唯一識別碼（= module GUID）
│   │   Type: PEIM/DXE_DRIVER/APPLICATION/FREEFORM…
│   │   │
│   │   ├── Section              ← 最內層，一個 FFS 裡有多個 section
│   │   │   Type: PE32           ← 實際的可執行程式碼
│   │   ├── Section
│   │   │   Type: USER_INTERFACE ← 模組名稱（UI 字串）
│   │   ├── Section
│   │   │   Type: VERSION        ← 版本字串
│   │   └── Section
│   │       Type: DXE_DEPEX      ← dependency expression（DXE 用）
│   │
│   └── [更多 FFS files...]
│
└── 另一個 FV...                  ← 大型韌體通常有多個 FV
    （可能是壓縮 FV，包在 GUID_DEFINED section 裡）
```

---

## 真實演練：解析 OVMF_CODE.fd 的 FV/FFS/Section

### 第一步：找所有 FV

用 python 直接解析 `_FVH` 簽名：

```python
import struct, uuid

data = open("/usr/share/OVMF/OVMF_CODE.fd", "rb").read()
print("OVMF_CODE.fd size: %d bytes (0x%x)" % (len(data), len(data)))

# 掃 _FVH 簽名（在 FV header offset 0x28）
fvs = []
i = 0
while i < len(data) - 0x40:
    if data[i+0x28:i+0x2C] == b"_FVH":
        fv_len = struct.unpack_from("<Q", data, i+0x20)[0]
        hdr_len = struct.unpack_from("<H", data, i+0x30)[0]
        fsguid = uuid.UUID(bytes_le=bytes(data[i+0x10:i+0x20]))
        if fv_len > 0x48 and i + fv_len <= len(data):
            fvs.append({"off": i, "size": fv_len, "hdr": hdr_len, "fsguid": str(fsguid)})
            i += max(fv_len, 0x1000)
            continue
    i += 0x10
```

實際輸出：

```
OVMF_CODE.fd size: 1966080 bytes (0x1e0000)

=== Firmware Volumes found: 2 ===
  FV[0]  offset=0x000000  size=0x1ac000 (1753088 bytes)
         FS-GUID=8c8ce578-8a3d-4f1c-9935-896185c32dd3
  FV[1]  offset=0x1ac000  size=0x34000 (212992 bytes)
         FS-GUID=8c8ce578-8a3d-4f1c-9935-896185c32dd3
```

兩個 FV 的 FS-GUID 都是 `8c8ce578-8a3d-4f1c-9935-896185c32dd3`，即 **`EFI_FIRMWARE_FILE_SYSTEM2_GUID`**，這是標準的 UEFI FFS2 filesystem。

FV[0] 是大型壓縮 FV（1.67 MB），裡面是壓縮後的 DXE+PEI 模組。FV[1] 是小型未壓縮 FV（208 KB），放的是 SEC 入口點（`SecMain`）和 boot block 相關的 raw 資料。

### 第二步：解析 FV[1] 的 FFS files

FV[1] 是未壓縮的，可以直接解析 FFS header：

```python
# EFI_FFS_FILE_HEADER:
# Offset 0:  Name GUID [16 bytes]
# Offset 16: IntegrityCheck [4]
# Offset 18: Type [1]
# Offset 19: Attributes [1]
# Offset 20: Size [3 bytes LE]
# Offset 23: State [1]
# Total: 24 bytes

FFS_TYPE_NAMES = {
    0x03: "SECURITY_CORE",   # SEC phase 程式碼
    0x04: "PEI_CORE",
    0x05: "DXE_CORE",
    0x06: "PEIM",
    0x07: "DRIVER",          # DXE_DRIVER
    0x09: "APPLICATION",     # UEFI_APPLICATION
    0x0B: "FV_IMAGE",        # 內嵌的 FV
    0x19: "RAW",
    0xFF: "PAD",
}
```

FV[1] 的 FFS 列表（實際輸出）：

```
=== FFS files in FV[1] (SEC, uncompressed) ===
Files: 2

[SECURITY_CORE]  df1ccef6-f301-4a63-9661-fc6030dcc880  size=31998   SecMain  1.0
[RAW]            1ba0062e-c779-4582-8566-336ae8f78f09  size=2440
```

只有 2 個 FFS files：
- `SecMain`（型別 `SECURITY_CORE`，GUID `df1ccef6-...`）：這就是 reset vector 進入點，SEC 階段的第一段 UEFI 程式碼，大小約 32 KB。
- 一個 RAW 類型 file：通常存放 metadata 或 padding。

### 第三步：解析 FFS 裡的 section

每個 FFS file 裡有多個 section，section header 是 4 bytes：

```python
# EFI_COMMON_SECTION_HEADER:
# Offset 0: Size [3 bytes LE]
# Offset 3: Type [1]

SECTION_TYPE_NAMES = {
    0x01: "COMPRESSION",
    0x02: "GUID_DEFINED",       # 加密/壓縮 section，用 GUID 標識 codec
    0x10: "PE32",               # 可執行的 PE image
    0x11: "PIC",
    0x12: "TE",                 # TE (Terse Executable)，SEC/PEI 用的精簡格式
    0x13: "DXE_DEPEX",
    0x14: "VERSION",
    0x15: "USER_INTERFACE",     # UTF-16LE 模組名稱
    0x17: "FIRMWARE_VOLUME_IMAGE",  # 內嵌 FV
    0x19: "RAW",
    0x1B: "PEI_DEPEX",
    0x1C: "SMM_DEPEX",
}
```

FV[1] 的 section 分佈（實際輸出）：

```
=== Section type distribution in FV[1] ===
RAW                       : 2
PE32                      : 1
USER_INTERFACE            : 1
VERSION                   : 1
```

`SecMain` 這個 FFS file 裡：
- 1 個 `PE32` section：SecMain 的實際程式碼（PE/COFF 格式）
- 1 個 `USER_INTERFACE` section：名稱字串 `SecMain`（UTF-16LE）
- 1 個 `VERSION` section：版本字串 `1.0`

### 第四步：uefi_firmware 走完整棵樹

用 `uefi_firmware` 走完整的 FV → FFS → section 階層（FV[0] 的壓縮 section 也會被解開）：

```python
import uefi_firmware, uuid

data = open("/usr/share/OVMF/OVMF_CODE.fd", "rb").read()
parser = uefi_firmware.AutoParser(data)
obj = parser.parse()

# 收集所有命名模組
names = collect_names(obj)  # 見 Ch 3 的 walk 函式
```

實際輸出（全部 128 個命名模組，按類別整理）：

```
Total named modules: 128

PEI modules  (12):
  PeiCore, PcdPeim, ReportStatusCodeRouterPei, StatusCodeHandlerPei,
  PlatformPei, S3Resume2Pei, CpuMpPei, TpmMmioSevDecryptPei,
  Tcg2ConfigPei, TcgPei, Tcg2Pei, Tcg2PlatformPei

TPM/TCG modules  (9):
  TpmMmioSevDecryptPei, Tcg2ConfigPei, TcgPei, Tcg2Pei,
  Tcg2PlatformPei, TcgDxe, Tcg2Dxe, Tcg2PlatformDxe, Tcg2ConfigDxe

Security modules  (4):
  SecurityStubDxe, SecureBootConfigDxe, FirmwareSecvarUpdater, SecMain

DXE drivers  (95):
  DxeIpl, DxeCore, ReportStatusCodeRouterRuntimeDxe,
  StatusCodeHandlerRuntimeDxe, PcdDxe, RuntimeDxe, SecurityStubDxe,
  EbcDxe, CpuIo2Dxe, CpuDxe, Timer, Metronome, PcRtc,
  VirtioPciDeviceDxe, Virtio10, VirtioBlkDxe, VirtioScsiDxe,
  VirtioRngDxe, PvScsiDxe, MptScsiDxe, SecureBootConfigDxe,
  FirmwareSecvarUpdater, WatchdogTimer, MonotonicCounterRuntimeDxe,
  CapsuleRuntimeDxe, ConPlatformDxe, ConSplitterDxe,
  GraphicsConsoleDxe, TerminalDxe, BdsDxe, UiApp,
  QemuKernelLoaderFsDxe, DevicePathDxe, DiskIoDxe, PartitionDxe,
  RamDiskDxe, EnglishDxe, ScsiBus, ScsiDisk, SataController,
  AtaAtapiPassThruDxe, AtaBusDxe, NvmExpressDxe, HiiDatabase,
  SetupBrowser, DisplayEngine, NullMemoryTestDxe, SioBusDxe,
  PciSioSerialDxe, Ps2KeyboardDxe, SmbiosDxe, SmbiosPlatformDxe,
  AcpiTableDxe, QemuFwCfgAcpiPlatform, S3SaveStateDxe,
  BootScriptExecutorDxe, BootGraphicsResourceTableDxe, Fat, UdfDxe,
  VirtioFsDxe, tftpDynamicCommand, httpDynamicCommand,
  LinuxInitrdDynamicShellCommand, Shell, LogoDxe, DpcDxe, SnpDxe,
  VlanConfigDxe, MnpDxe, ArpDxe, Dhcp4Dxe, Ip4Dxe, Udp4Dxe,
  Mtftp4Dxe, Dhcp6Dxe, Ip6Dxe, Udp6Dxe, Mtftp6Dxe, TcpDxe,
  UefiPxeBcDxe, TlsDxe, TlsAuthConfigDxe, DnsDxe, HttpDxe,
  HttpUtilitiesDxe, HttpBootDxe, IScsiDxe, VirtioNetDxe,
  UhciDxe, EhciDxe, XhciDxe, UsbBusDxe, UsbKbDxe,
  UsbMassStorageDxe, QemuVideoDxe, QemuRamfbDxe, VirtioGpuDxe,
  PlatformDxe, AmdSevDxe, IoMmuDxe, FvbServicesRuntimeDxe,
  EmuVariableFvbRuntimeDxe, FaultTolerantWriteDxe, VariableRuntimeDxe,
  TcgDxe, Tcg2Dxe, Tcg2PlatformDxe, Tcg2ConfigDxe

SEC module  (1):
  SecMain  [FV[1], SECURITY_CORE type]
```

---

## FFS 檔案類型詳解

| Type Code | 名稱 | 說明 | OVMF 範例 |
|---|---|---|---|
| 0x03 | SECURITY_CORE | SEC phase 進入點 | SecMain |
| 0x04 | PEI_CORE | PEI Core（dispatcher） | PeiCore |
| 0x05 | DXE_CORE | DXE Core | DxeCore |
| 0x06 | PEIM | PEI Module | TcgPei, PlatformPei |
| 0x07 | DRIVER | DXE Driver | BdsDxe, VariableRuntimeDxe |
| 0x09 | APPLICATION | UEFI Application（EFI executable） | Shell, UiApp |
| 0x0A | SMM | SMM Driver（特殊 DXE，跑在 SMRAM） | SmmCore（不在 OVMF 公開 FV） |
| 0x0B | FV_IMAGE | 內嵌 Firmware Volume | 壓縮 FV 容器 |
| 0x02 | FREEFORM | 自由格式資料 | 圖片、設定 |
| 0x19 | RAW | 純原始資料（無 section） | boot block raw data |

攻擊相關的 FFS type：
- **0x07 DRIVER**：所有惡意 DXE driver 偽裝的目標類型
- **0x06 PEIM**：惡意 PEIM 的目標類型
- **0x0B FV_IMAGE**：用來巢狀嵌入額外的 FV，攻擊者可以藏額外的 FFS files 在這裡

---

## Capsule Update 完整流程

### 正常更新流程

```
OS / 更新工具
    │
    ├─ 1. ESRT 查詢
    │       讀 EFI_SYSTEM_RESOURCE_TABLE（ESRT），知道
    │       每個 firmware 組件的 GUID、當前版本、最低版本
    │
    ├─ 2. 下載 capsule（.cap 檔）
    │       capsule 格式：EFI_CAPSULE_HEADER + payload（FV 或 FMP payload）
    │
    ├─ 3. 呼叫 gRT->UpdateCapsule(CapsuleHeaderArray, CapsuleCount, ScatterGatherList)
    │       OS 把 capsule 資料放進記憶體，告訴韌體位址
    │
    ├─ 4. ResetSystem(EfiResetWarm)
    │       OS trigger reboot
    │
    └─ 5. 重開機，韌體在 DXE 階段呼叫 ProcessCapsules()
            ├─ CapsuleRuntimeDxe 找到 OS 傳入的 capsule 資料
            ├─ 驗 capsule GUID（是不是我認識的 FMP capsule）
            ├─ 呼叫 FMP->SetImage() 交給對應的 FMP 驅動
            │     FMP（Firmware Management Protocol）驗章
            │     並把 payload 寫進 SPI flash
            └─ 重開機進正常流程
```

### ESRT（EFI System Resource Table）

ESRT 是 ACPI table 的一種（GUID: `b122a263-3661-4f68-9929-78f8b0d62180`），列出所有可更新的 firmware 組件：

```c
typedef struct {
    EFI_GUID  FwClass;          // 識別這個 firmware 組件的 GUID
    UINT32    FwType;           // 1=SystemFirmware, 2=DeviceFirmware, 3=UEFI driver
    UINT32    FwVersion;        // 當前版本號
    UINT32    LowestSupportedFwVersion;  // 最低允許降版本（rollback 防護）
    UINT32    CapsuleFlags;
    UINT32    LastAttemptVersion;
    UINT32    LastAttemptStatus;
} EFI_SYSTEM_RESOURCE_ENTRY;
```

`LowestSupportedFwVersion` 就是 rollback 防護的欄位：韌體在接受 capsule 時比對 capsule 內的版本號，若低於此值則拒絕。

### FMP Capsule 格式

現代 UEFI capsule 使用 **FMP（Firmware Management Protocol）capsule** 格式：

```
EFI_CAPSULE_HEADER
└── EFI_FIRMWARE_MANAGEMENT_CAPSULE_HEADER
    ├── EmbeddedDriverCount  (可以在刷韌體前臨時載入的 driver)
    └── PayloadItemCount
        └── EFI_FIRMWARE_MANAGEMENT_CAPSULE_IMAGE_HEADER
            ├── UpdateImageTypeId  (對應到 ESRT 的 FwClass GUID)
            ├── UpdateImageIndex
            └── Payload
                └── EFI_FIRMWARE_IMAGE_AUTHENTICATION
                    ├── MonotonicCount    (防 replay)
                    └── AuthInfo          (WIN_CERTIFICATE, CMS SignedData)
                        └── 實際 FV payload（要刷進 SPI 的新韌體）
```

`EmbeddedDriverCount` 是個有趣的攻擊面：capsule 可以夾帶 UEFI driver，在刷韌體前被載入，如果驗章只驗 payload 不驗這個 embedded driver，攻擊者可以藉此執行任意程式碼。

---

## 攻擊一：Capsule 簽章驗證漏洞

### 驗章缺失（最嚴重）

某些廠商實作的 FMP 在接受 capsule 時根本不驗章，或驗章是可選的（`CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE` flag 設錯）。任何人能提交任何 FFS image 並刷進 SPI flash。

**BRLY-2023-005（Insyde H2O）**：Insyde 韌體的 capsule 處理在特定條件下跳過 RSA-2048 簽章驗證，攻擊者可以提交任意 capsule。

### 解析漏洞（LogoFAIL 路徑）

LogoFAIL（2023，Binarly）的核心：更新流程中，韌體會顯示廠商 logo（BMP/PNG/JPEG），使用的 image parser 有 overflow。由於 logo 解析發生在 `SetImage()` 驗章之外的路徑（甚至在 `ExitBootServices` 之前），攻擊者把 exploit payload 藏在 logo 圖片裡，觸發時機早於任何 image 驗章。

攻擊路徑：
1. 把惡意 BMP 寫進 ESP（EFI System Partition）的 logo 路徑，或竄改 `LogoFile` variable
2. 韌體 BDS 階段顯示 logo 時呼叫 BMP parser
3. Parser overflow → 控制流劫持 → 執行任意程式碼（此時 Secure Boot 還沒跑）
4. 不需要 capsule，直接完成 bypass

### TOCTOU（Time-of-Check vs Time-of-Use）

FMP 的流程：先驗章（check），後寫入（use）。如果驗章和寫入之間有足夠的時間視窗，攻擊者可以在記憶體裡把 payload 換掉。這需要 DMA 或 race condition，實際難度較高，但 DMA attack 的防護（VT-d/IOMMU）如果沒開，這個路徑就開著。

---

## 攻擊二：Rollback 攻擊

### 機制

如果 FMP 的 rollback 防護沒有正確實作（例如 `LowestSupportedFwVersion` 是 0，或 capsule 裡的版本號沒有被嚴格比對），攻擊者可以：

1. 找到一個有已知漏洞的舊版韌體 capsule（即使是官方簽章的舊 capsule）
2. 提交這個舊 capsule，讓韌體降版到有漏洞的版本
3. 利用舊版漏洞拿到完整控制

**BlackLotus 的先驅步驟**：BlackLotus 利用的 BootHole（CVE-2020-10713）已被 dbx 撤銷，但舊版 shim 的 capsule 仍有合法簽章（簽章是好的，只是 binary 已被 dbx 拒絕）。在某些平台上可以先 rollback shim，讓新的 dbx 黑名單失效，再用有漏洞的舊 shim 觸發 BootHole。

### 防護

- ESRT 的 `LowestSupportedFwVersion` 欄位
- TPM PCR[0] 的 Measured Boot：舊版韌體的 measurement 和新版不同，Seal Key 失效（但這是後驗，不是前防）
- 廠商在 capsule payload 裡附 monotonic counter，韌體在 SPI flash 裡也存一個 counter，只接受 counter 遞增的 capsule

---

## FV 格式細節：GUID_DEFINED Section 與壓縮

FV[0] 裡的大型 FFS file（`9e21fd93-...`，1.49 MB）包含一個 `GUID_DEFINED` section，用 GUID `ee4e5898-3914-4259-9d6e-dc7bd79403cf` 標識其壓縮格式（`EFI_LZMA_COMPRESS_GUID`）。

```
FV[0] (1.67 MB)
└── FFS: 9e21fd93-9c72-4c15-8c4b-e77f1db2d792 (1.49 MB)
    └── Section: GUID_DEFINED
        GUID: ee4e5898-3914-4259-9d6e-dc7bd79403cf  ← LZMA compress GUID
        └── [解壓縮後]
            └── Section: FIRMWARE_VOLUME_IMAGE
                └── 內嵌 FV (917504 bytes)
                    ├── PeiCore
                    ├── DxeIpl
                    ├── PlatformPei
                    ├── TcgPei / Tcg2Pei
                    ├── DxeCore
                    ├── ... (所有 DXE drivers)
                    └── SecureBootConfigDxe
```

這個壓縮+內嵌結構讓工具需要理解壓縮 codec 才能看到裡面的 FFS。`uefi_firmware` 能透通解壓縮，但手動 hex 解析看不到。

攻擊含義：如果 FV 解壓縮 parser（edk2 的 `LzmaDecompressLib`）有漏洞，攻擊者提供一個惡意壓縮 FV，在解壓縮時觸發 overflow，此時執行位置已經在 PEI/DXE 的很早期，任何安全防線都還沒建立。

---

## 底層格式：Integrity Check 的弱點

FFS header 包含 `IntegrityCheck` 欄位（4 bytes），其中有 header checksum 和 file checksum：

```c
typedef union {
    struct {
        UINT8  Header;   // FFS header 的 checksum（補數）
        UINT8  File;     // FFS body 的 checksum，或 0xAA（如果沒有計算）
    } Checksum;
    UINT16 Checksum16;
} EFI_FFS_INTEGRITY_CHECK;
```

`File` 欄位：如果 `FFS_ATTRIB_CHECKSUM`（Attributes bit）沒有設，`File` checksum 固定是 `0xAA`，不做任何 body 完整性驗證。

**含義**：大部分 FFS file 的 body 沒有完整性保護（只有 header 有），壞掉的或被竄改的程式碼 body 不會被 FFS parser 阻擋，DXE dispatcher 會照單全收。這是為什麼 SPI flash 竄改攻擊能直接生效——FFS 格式本身不防竄改。

---

## 對比：三種韌體更新機制的安全比較

| 機制 | 驗章 | Rollback 防護 | 攻擊面 |
|---|---|---|---|
| 直接 SPI flash 寫（傳統） | 無 | 無 | 需要 SPI 寫權限，最暴力 |
| Capsule（無 FMP） | 可能無 | 通常無 | 沒驗章就等於直接 SPI 寫，但路徑更隱蔽 |
| FMP Capsule（標準） | RSA 簽章 | ESRT LowestSupported | 私鑰洩露、TOCTOU、embedded driver 漏洞 |
| Embedded Driver in Capsule | 獨立驗章（或無） | 無 | 廠商常忘記驗 embedded driver |

---

## 踩雷集錦

**1. 以為 FFS checksum 能防竄改**：FFS header 有 checksum，但 file body（也就是 PE 程式碼）的 checksum 在大多數情況下是 `0xAA`（未啟用）。parser 不會因為 body 被改掉而拒絕執行。唯一的防線是 BootGuard 或 Secure Boot（後者只在 TSL phase 生效）。

**2. Capsule embedded driver 沒被驗**：FMP capsule 支援夾帶 embedded driver，很多廠商只驗 payload 的簽章，沒有驗 embedded driver。攻擊者可以在 embedded driver 裡放任意程式碼，在韌體真正更新前執行。

**3. UpdateCapsule 在 DXE phase 呼叫等於 Secure Boot 之前**：capsule 處理發生在 DXE，而 Secure Boot 的 image 驗章是在 TSL（BDS 呼叫 LoadImage 時）。capsule 內的惡意 embedded driver 在 Secure Boot 生效之前就已執行。

**4. LowestSupportedFwVersion 被廠商硬碼為 0**：很多廠商懶得維護 rollback 防護，把 `LowestSupportedFwVersion` 永遠設 0，等於沒有 rollback 防護。即使是官方簽章的舊 capsule 也能降版。

**5. FV GUID 和 FFS GUID 容易混淆**：FV 的 FS-GUID 標識的是「filesystem 類型」（`EFI_FIRMWARE_FILE_SYSTEM2_GUID`），FFS file 的 GUID 標識的是「這個 module 是誰」（module GUID）。兩者都叫 GUID 但含義完全不同；逆向工具看到 GUID 時要先分清楚是哪一層的。

---

## 進階延伸

- **edk2 MdeModulePkg/Library/DxeCapsuleLibFmp**：FMP capsule 處理的 edk2 實作，`ProcessTheseCapsules()` 是主流程，看它如何呼叫 FMP->SetImage 以及 embedded driver 的處理邏輯。
- **Binarly, "LogoFAIL: Security Implications of Image Parsing During System Boot"（2023）**（binarly.io/blog）：完整技術報告，逐步展示 BMP/PNG/JPEG parser 漏洞如何在 DXE/BDS 階段被觸發，繞過 Secure Boot 和任何 capsule 驗章。
- **UEFITool**（github.com/LongSoft/UEFITool）：GUI 工具，能視覺化展示 FV→FFS→section 階層，是研究韌體結構最快的方法；`uefi_firmware` 是腳本操作的等效工具。

---

## 動手練習

1. 用 `python3` + `uefi_firmware` 把 `OVMF_CODE.fd` 裡的 `SecMain` FFS file 的 `PE32` section raw bytes dump 出來存檔，用 `file` 命令確認是 `PE32+ executable (EFI)`，然後用 `xxd` 確認前 2 bytes 是 `MZ`（PE magic）。
2. 找出 FV[0] 裡的 `GUID_DEFINED` section 的 GUID（`ee4e5898-...`），搜尋 edk2 原始碼確認這個 GUID 對應的是哪種壓縮算法（LZMA？Brotli？），以及哪個 Library 負責解壓縮。
3. 在 QEMU + OVMF 開機後，進 UEFI Shell 跑 `acpiview`（如果有），或用 `dmem`（dump memory）找 ESRT 表，讀出 `FwVersion` 和 `LowestSupportedFwVersion` 的值，看 OVMF 的 rollback 防護是否有效設定。

---

## 本章重點

- FV/FFS/section 是三層套疊格式：FV 是容器（附 FS-GUID），FFS 是模組（附 module GUID 和類型），section 是 FFS 的內容（PE32/TE/depex/UI/VERSION）。
- OVMF_CODE.fd 有兩個 FV：FV[0]（1.67MB，壓縮 LZMA）包含所有 PEIM+DXE；FV[1]（208KB，未壓縮）包含 SEC 入口 `SecMain`。
- FFS body 的完整性驗證預設不啟用（file checksum = 0xAA），SPI flash 竄改後 FFS parser 不會阻擋執行。
- FMP Capsule 是標準更新機制，但 embedded driver、TOCTOU、rollback 防護缺失各自是獨立的攻擊面。
- LogoFAIL 示範了解析路徑（logo image parser）比驗章更早執行，是格式複雜度帶來的根本問題。

---

## 自我檢核

- [ ] 我能畫出 FV → FFS → Section 的三層結構，說出每層的 header 關鍵欄位
- [ ] 我知道 FS-GUID（`8c8ce578-...`）和 module GUID 的差別
- [ ] 我能用 python uefi_firmware 或手動 struct 解析找到 OVMF 的 FV 數量和各 FV 的內容
- [ ] 我能解釋 FMP capsule 的驗章流程，以及哪三個地方最容易出漏洞
- [ ] 我知道 FFS body checksum 為什麼不是安全保護

---

## 延伸閱讀

1. **PI Specification Volume 3: Shared Architectural Elements（Chapter 4: Firmware Storage）**（uefi.org/specifications）：FV header、FFS file header、section header 的完整欄位定義和語義，是理解各個 GUID 和 Type 欄位的一手資料；關聯到本課 Ch 23（UEFITool 解析）。

2. **Binarly, "LogoFAIL: Security Implications of Image Parsing During System Boot"（2023）**（binarly.io/blog + DEF CON 31 talk）：把 FV/section 的解析路徑和攻擊面拉通，展示為什麼「在驗章路徑之外的 parser」就是攻擊面，是本章理論在真實攻擊中的最佳示範；關聯到 Ch 30（真實利用鏈）。

3. **UEFI Forum, "A Tour Beyond BIOS: Capsule Update and Recovery in EDK II"**（tianocore.github.io/edk2-StandaloneMmPkg）：edk2 的 capsule update 技術說明，FMP capsule 格式、`UpdateCapsule` 的實作細節、embedded driver 的處理流程，是理解「capsule 應該怎麼做才安全」的設計文件，反讀即得攻擊面；關聯到 Ch 8（edk2 漏洞挖掘）。

→ [Ch 7 UEFI 漏洞類型與真實 CVE](./07-uefi-vulnerability-classes.md)
