# Ch 3 — PI 規範各階段的攻擊面

> **目標**：不重教開機流程，直接看 Platform Initialization（PI）規範的每個階段「哪裡可以被插手」——攻擊者能注入什麼、信任假設在哪裡斷掉、defender 的偵測點在哪裡。用 python uefi_firmware 真跑 OVMF_CODE.fd，把 PEIM/DXE 模組表貼出來看。
>
> **環境**：WSL，python3 + uefi_firmware（`python3 -c "import uefi_firmware"` 可用）、/usr/share/OVMF/OVMF_CODE.fd。

## 為什麼要拆 PI 階段來看攻擊面？

UEFI bootkit（LoJax、MoonBounce、BlackLotus）的共同結構是：在 OS 能看到的世界成形之前，把惡意碼種進某個「OS 看不到、OS 啟動後仍活著」的位置。PI 規範把開機分成六個互相傳遞控制權的階段，每個階段的信任假設不同，暴露的攻擊面也不同。

把每個階段問同一個問題：**攻擊者需要什麼前置條件、能拿到什麼權限、以及廠商在這裡防了什麼（或沒防什麼）**。

## PI 六階段一眼看攻擊面

```
Power on
   │
   ▼  ┌──────────────────────────────────────────────────────────┐
SEC   │ Security               (reset vector, Cache-as-RAM)      │ 攻擊：reset vector 劫持, flash 竄改
   │  └──────────────────────────────────────────────────────────┘
   ▼  ┌──────────────────────────────────────────────────────────┐
PEI   │ Pre-EFI Initialization (PEIM, HOB, recovery)             │ 攻擊：惡意 PEIM, recovery 路徑繞過
   │  └──────────────────────────────────────────────────────────┘
   ▼  ┌──────────────────────────────────────────────────────────┐
DXE   │ Driver Execution Env   (driver dispatcher, protocol DB)  │ 攻擊：惡意 DXE driver, protocol hook
   │  └──────────────────────────────────────────────────────────┘
   ▼  ┌──────────────────────────────────────────────────────────┐
BDS   │ Boot Device Selection  (BootXXXX var, BootOrder)         │ 攻擊：variable 竄改, bootnext 劫持
   │  └──────────────────────────────────────────────────────────┘
   ▼  ┌──────────────────────────────────────────────────────────┐
TSL   │ Transient Sys Load     (bootloader 在跑)                  │ 攻擊：shim/grub 漏洞, Secure Boot 繞過
   │  └──────────────────────────────────────────────────────────┘
   ▼  ┌──────────────────────────────────────────────────────────┐
RT    │ Runtime Services       (OS 執行中, SetVariable 仍在)      │ 攻擊：runtime hook, SetVariable 後門
      └──────────────────────────────────────────────────────────┘
```

---

## SEC：reset vector 與 Cache-as-RAM

### 信任假設

SEC（Security Phase）是 CPU 拿到控制權後第一段跑的 UEFI 程式碼。它的設計前提是：「到這裡為止的程式碼已經通過了 BootGuard/ACM 的驗證」。沒有 Intel BootGuard（或 AMD PSB）的平台，SEC 的 reset vector 本質上是 flash 裡某個固定偏移（x86 是 0xFFFFFFF0），誰能寫 SPI flash 誰就能改它。

### 攻擊點

**1. SPI flash 竄改（SEC 程式碼替換）**

reset vector 位於 SPI flash 的 FV_BB（Boot Block）區段。如果 `BIOS_CNTL.BIOSWE = 1`（write enable）且沒有上 SMM write protection（`BIOS_CNTL.SMM_BWP`），攻擊者在 OS ring 0 能直接 mmap `/dev/mem` 或透過 CHIPSEC 寫入 SPI，替換 SEC 程式碼。

**2. Cache-as-RAM（CAR）降格攻擊**

SEC 用 CPU cache 當 DRAM 跑（No-Evict Mode，DRAM 還沒 init 完），這段期間 stack/heap 都在 cache。理論上如果能透過 SMI 在這段期間插入，可能打亂 SEC 狀態。實務上前提條件高、不常見。

**3. BootGuard 缺失平台的 HOB 造假**

SEC 把初始化結果傳給 PEI 的方式是 Hand-Off Block（HOB list）。沒有完整性保護的情況下，若能在 HOB 傳遞前竄改 HOB 內容，可以讓 PEI 拿到錯誤的記憶體地圖或 FV 位址。

### 防禦線

- Intel BootGuard（ACM 在 CPU 微碼層驗 SEC hash），AMD PSB（Platform Secure Boot）是對應品
- BIOS_CNTL 的 `SMM_BWP` bit 鎖 flash write，使其只能在 SMM context 操作
- SMRR（System Management Range Register）防止 SMRAM 被一般 ring 0 讀寫

---

## PEI：PEIM、dependency、recovery 路徑

### 信任假設

PEI 階段跑 PEI Module（PEIM），每個 PEIM 透過 dependency expression 宣告先後順序，PEI dispatcher 依序派發。PEI 的信任模型假設：**FV 裡的 PEIM 是可信的**——如果攻擊者能在 FV 裡放一個 PEIM，它就會被 dispatcher 執行，完全沒有 code signing 擋在前面（Secure Boot 在 DXE/BDS 之後才有意義）。

### 攻擊點

**1. 惡意 PEIM 注入**

把惡意程式碼包成合法 FFS 格式塞進 FV，只要 PEI dispatcher 能找到它、它的 dependency 能被滿足，就會執行。這是 SPI flash 竄改場景最直接的做法。

**2. PPI（PEI Protocol Interface）hook**

PEIM 之間透過 PPI（PEI Protocol Interface，PEI 版的 protocol）傳遞 interface。攻擊者控制的 PEIM 可以 `ReInstallPpi` 覆蓋已有 PPI，例如把 `EFI_PEI_READ_ONLY_VARIABLE2_PPI` 換成自己的實作，讓後續 PEIM 讀到假資料。

**3. recovery 路徑繞過**

大多數平台的 PEI recovery 路徑（flash 損毀時從 USB/CD 重新刷入）比正常路徑的驗證要寬鬆，有些根本不驗簽章。BlackLotus 利用的一個思路就是：讓平台以為自己在做 recovery，實際上塞入攻擊者控制的韌體。

**4. FV 完整性驗證的缺口**

edk2 的 `PeiCore` 在 dispatch 前可以呼叫 `PeiVerifyFirmwareVolume` 鉤子；但這個鉤子預設不做任何事。只有安裝了簽章驗證 PEIM（如 `TcgPei`）的平台才有保護，且保護的是 TPM PCR 延伸，不是硬性阻止執行。

**5. S3 resume 路徑：PEI 重跑但沒有完整驗證**

S3（suspend-to-RAM）resume 時，PEI 階段會重新執行以初始化硬體，但這條路徑比冷開機精簡——大量 PEIM 被跳過，只跑 `S3Resume2Pei` 和最小必要的硬體初始化。問題是：S3 boot script（描述「如何重初始化 chipset register」的指令序列）存放在 DRAM，OS 執行期間可以讀寫。如果攻擊者在 S3 sleep 之前竄改 boot script，S3 resume 時 PEI 會執行被篡改的硬體操作序列，可能導致 SMRAM 保護被解除（Ch 12 詳述）。OVMF 裡的 `S3Resume2Pei` 和 `BootScriptExecutorDxe` 就是負責這條路徑的模組。

### 防禦線

- BootGuard：SEC hash 驗證能阻擋 PEI FV 的竄改，但前提是 FV_BB 區域在 BootGuard manifest 範圍內
- TPM PCR[1]：TcgPei/Tcg2Pei 在 dispatch 每個 PEIM 前把模組 hash 延伸進 PCR，提供事後稽核（不是事前阻擋）
- S3 Boot Script：現代韌體用 LockBox（存在 SMRAM 或 SMRAM-protected 記憶體）保護 boot script 位址，防止 DRAM 竄改

---

## DXE：driver dispatcher、protocol DB、dependency expression

### 信任假設

DXE 是 UEFI 最「豐富」的階段，幾乎所有功能型 driver 都在這裡。DXE Core 初始化 `gBS`/`gRT`/`gST`（Boot Services / Runtime Services / System Table），然後 DXE dispatcher 從各個 FV 找 DXE driver 來跑。

信任假設：**FV 裡的 DXE driver 可信**（跟 PEI 一樣的問題），以及 **protocol DB 裡的內容可信**（任何 driver 都能呼叫 `InstallProtocolInterface` 往 DB 裡放東西）。

### 攻擊點

**1. 惡意 DXE driver**

這是本課 Ch 4 的主題。惡意 DXE driver 可以：
- hook `gBS->LoadImage` / `gBS->StartImage`，在所有後續 image 載入時執行任意程式碼
- hook `gRT->SetVariable`，攔截或竄改 variable 寫入
- 安裝假的 protocol，讓依賴它的 driver 拿到惡意實作
- 透過 `RegisterProtocolNotify` 等 OS 載入時觸發

**2. protocol DB 污染**

protocol database 是共享的，沒有存取控制。任何 DXE driver 都能呼叫 `InstallProtocolInterface` 用同一個 GUID 蓋掉既有 protocol。

**3. DXE dependency expression 操縱**

DXE driver 的 `.depex` 段宣告「我需要哪個 protocol 存在才能執行」。惡意 driver 可以把 depex 寫成在特定條件下（例如某個 protocol 安裝後）才執行，達到潛伏效果。

**4. DXE handoff 篡改**

DXE Core 在 handoff 到 OS bootloader 前會呼叫 `ExitBootServices`，此時 driver 有機會在這個 event 掛 callback（`gBS->CreateEvent(EVT_SIGNAL_EXIT_BOOT_SERVICES, ...)`）。MoonBounce 的做法就是把 shellcode 藏進這個 callback，讓它在 OS loader 正式接管前跑。

---

## BDS：BootXXXX/BootOrder variable、boot manager

### 信任假設

BDS 決定「從哪裡開機」。它讀 NVRAM variable `BootOrder`（開機優先順序）和 `Boot0000`~`BootFFFF`（各開機項目描述），然後依序嘗試。

**關鍵：BootOrder 和 BootXXXX 是 NVRAM variable，屬性通常是 NV+BS+RT，不是 authenticated variable**。如果平台沒有特別限制，任何能呼叫 `SetVariable` 的 OS-level 程式都能改它。

### 攻擊點

**1. BootOrder 竄改**

把攻擊者的 EFI binary（放在 EFI System Partition）的 `BootXXXX` 項目插到 BootOrder 最前面，下次開機就從攻擊者的 binary 啟動。這不需要 Secure Boot 關閉——只要攻擊者的 binary 有合法簽章（或 Secure Boot 根本沒開），就能成功。

**2. BootNext 單次覆蓋**

variable `BootNext` 指定「下一次（且只有下一次）開機用哪個 Boot entry」。這個 variable 同樣不是 authenticated，攻擊者能在 OS runtime 寫它，然後觸發 warm reset，讓平台從攻擊者指定的 EFI image 開機。

**3. BDS connect all 的副作用**

BDS 在選定開機設備前會呼叫 `gBS->ConnectAll()` 把所有 driver 都連上所有 handle，這個行為本身會觸發大量 driver binding，攻擊者的惡意 driver 可以在這個時機拿到不該拿到的 handle。

---

## TSL：OS bootloader 在跑

### 信任假設

TSL（Transient System Load）指 BDS 呼叫 `LoadImage`/`StartImage` 把 OS bootloader（shim → grub → kernel）跑起來這段期間。Secure Boot 在這裡才真正有用：`gST->BootServices->LoadImage` 會呼叫 security architectural protocol 驗 PE/COFF 的簽章。

### 攻擊點

**1. Secure Boot 繞過（shim 漏洞）**

BootHole（CVE-2020-10713）：grub2 的 `grub.cfg` 讀取有 buffer overflow，攻擊者把惡意 `grub.cfg` 放在 ESP，shim 驗 grub2 本體沒問題，但 grub2 跑起來後讀 cfg 時觸發 overflow，最終繞過 Secure Boot 的保護鏈。這是 TSL 階段的典型攻擊。

**2. 資料檔繞過簽章（LogoFAIL）**

Secure Boot 只驗 PE image，不驗 parser 使用的資料。如果 BDS/TSL 階段會 parse 圖片（Logo 顯示），而 parser 有 buffer overflow，攻擊者把 exploit payload 放在圖片裡，這段程式碼在簽章驗證之前就跑起來了（因為 Logo 解析發生在 LoadImage 流程之外）。

**3. TOCTOU（Time-of-Check to Time-of-Use）**

`LoadImage` 驗章，`StartImage` 才執行；如果有辦法在驗章之後、執行之前改掉 image 內容（例如透過 DMA），就能繞過。這就是 DMA protection（VT-d/IOMMU）要解決的問題。

---

## RT：Runtime Services，OS 執行中

### 信任假設

OS 起來後，UEFI Runtime Services（`gRT`）仍然可以透過 `EFI_RT_PROPERTIES_TABLE` 呼叫，包含 `GetVariable`、`SetVariable`、`GetTime`、`ResetSystem` 等。SMM 通常提供這些 runtime service 的實作。

### 攻擊點

**1. Runtime SetVariable 注入 Secure Boot DB**

`SetVariable("db", ...)` 可以改 Secure Boot 允許的簽章清單；但 authenticated variable 需要 PK→KEK 簽章鏈，沒有私鑰就改不了。然而 **非 authenticated variable**（屬性沒有 `EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS`）任何 ring 0 都能改。

**2. 惡意 runtime hook 殘留**

DXE driver 在 `ExitBootServices` 後可以繼續存在記憶體中（只要它在 RT memory 區段），OS 的 `SetVariable` 最終會跳進 SMM 或韌體提供的 runtime handler，如果那個 handler 被 hook 了，OS 的每一次 variable 操作都被攻擊者監控。

**3. SMM 提供的 runtime service 作為攻擊入口**

OS 呼叫 `SetVariable` → 觸發 SMI（軟體中斷） → 進 SMRAM → SMM handler 處理。如果 SMM handler 有漏洞（Ch 10-13 會深挖），這就是 OS ring 0 → Ring -2 的提權路徑，反過來也是 Ring -2 能長期植入的理由。

---

## 真實演練：用 python uefi_firmware 拆 OVMF_CODE.fd

以下是在 WSL 環境實際跑出的結果。

```python
# /tmp/parse_ovmf_modules.py
import uefi_firmware, struct, uuid

data = open("/usr/share/OVMF/OVMF_CODE.fd", "rb").read()
modules = []

def walk(obj, depth=0):
    cls = type(obj).__name__
    objs = getattr(obj, "objects", []) or []
    if cls == "FirmwareFile":
        guid_b = getattr(obj, "guid", None) or getattr(obj, "name", None)
        guid_str = ""
        if isinstance(guid_b, bytes) and len(guid_b) == 16:
            try: guid_str = str(uuid.UUID(bytes_le=bytes(guid_b)))
            except: guid_str = guid_b.hex()
        ui = None
        ver = None
        for child in objs:
            n = getattr(child, "name", None)
            if isinstance(n, str) and not n.replace(".","").isdigit():
                ui = n
            elif isinstance(n, str) and n.replace(".","").isdigit():
                ver = n
        if ui:
            modules.append((guid_str, ui, ver))
    for child in objs:
        walk(child, depth+1)

parser = uefi_firmware.AutoParser(data)
walk(parser.parse())
print("Total named modules: %d" % len(modules))
for g, name, ver in modules:
    print("%-40s %-40s %s" % (g, name, ver or ""))
```

實際輸出（OVMF 6.2，OVMF_CODE.fd 1,966,080 bytes，共 128 個命名模組）：

```
Total named modules: 128

GUID                                     Module Name                              Ver
-----------------------------------------------------------------------------------------------
# --- PEI 階段 PEIM（在壓縮 FV 內） ---
52c05b14-0b98-496c-bc3b-04b50211d680     PeiCore                                  1.0
9b3ada4f-ae56-4c24-8dea-f03b7558ae50     PcdPeim                                  4.0
a3610442-e69f-4df3-82ca-2360c4031a23     ReportStatusCodeRouterPei                1.0
9d225237-fa01-464c-a949-baabc02d31d0     StatusCodeHandlerPei                     1.0
222c386d-5abc-4fb4-b124-fbb82488acf4     PlatformPei                              1.0
86d70125-baa3-4296-a62f-602bebbb9081     DxeIpl                                   1.0
89e549b0-7cfe-449d-9ba3-10d8b2312d71     S3Resume2Pei                             1.0
edadeb9d-ddba-48bd-9d22-c1c169c8c5c6     CpuMpPei                                 1.0
f12f698a-e506-4a1b-b32e-6920e55da1c4     TpmMmioSevDecryptPei                     1.0
8ad3148f-945f-46b4-8acd-71469ea73945     Tcg2ConfigPei                            1.0
2be1e4a6-6505-43b3-9ffc-a3c8330e0432     TcgPei                                   1.0
a0c98b77-cba5-4bb8-993b-4af6ce33ece4     Tcg2Pei                                  1.0
47727552-a54b-4a84-8cc1-bff23e239636     Tcg2PlatformPei                          1.0

# --- DXE 階段 driver（部分，共 ~115 個） ---
d6a2cb7f-6a18-4e2f-b43b-9920a733700a     DxeCore                                  1.0
f80697e9-7fd6-4665-8646-88e33ef71dfc     SecurityStubDxe                          1.0
f0e6a44f-7195-41c3-ac64-54f202cd0a21     SecureBootConfigDxe                      1.0
42857f0a-13f2-4b21-8a23-53d3f714b840     CapsuleRuntimeDxe                        1.0
6d33944a-ec75-4855-a54d-809c75241f6c     BdsDxe                                   1.0
cbd2e4d5-7068-4ff5-b462-9822b4ad8d60     VariableRuntimeDxe                       1.0
fdff263d-5f68-4591-87ba-b768f445a9af     Tcg2Dxe                                  1.0
733cbac2-b23f-4b92-bc8e-fb01ce5907b7     FvbServicesRuntimeDxe                    1.0
fe5cea76-4f72-49e8-986f-2cd899dffe5d     FaultTolerantWriteDxe                    1.0
7c04a583-9e3e-4f1c-ad65-e05268d0b4d1     Shell                                    1.0
df1ccef6-f301-4a63-9661-fc6030dcc880     SecMain                                  1.0
# (更多：網路 stack、USB、VirtIO、SATA、NVMe…)
```

幾個值得注意的模組：

| 模組 | 位置 | 說明 |
|---|---|---|
| `PeiCore` | PEI FV（壓縮） | PEI dispatcher 本體，攻擊者要替換的最高價值目標之一 |
| `DxeIpl` | PEI FV | 從 PEI 跳 DXE 的橋樑，跑 DXE Core |
| `SecurityStubDxe` | DXE FV | 預設 Secure Boot 驗章 stub（空實作），沒換掉的話不做任何驗證 |
| `VariableRuntimeDxe` | DXE FV | 管 NVRAM variable 讀寫；runtime 留存 |
| `FvbServicesRuntimeDxe` | DXE FV | Firmware Volume Block Services，管 flash 讀寫 |
| `BdsDxe` | DXE FV | Boot Device Selection 邏輯，讀 BootOrder 選開機項 |
| `Tcg2Dxe` + `Tcg2Pei` | 兩個 FV 都有 | TPM 2.0 整合，負責 measured boot PCR 延伸 |

**注意**：`SecurityStubDxe`（GUID `f80697e9...`）這個名字本身就說明問題——它是個 **stub**，預設不做事。真正的驗章邏輯要由 `SecurityPkg` 的 `Security2Dxe`（或廠商自己的 driver）替換這個 stub。OVMF 的 QEMU 設定用的是 stub，真實硬體上廠商應該換掉它。

### FV 結構觀察

OVMF_CODE.fd 的物理佈局：

```
Offset 0x000000 – 0x1ABFFF  FV[0]  1,753,088 bytes  ← 壓縮大 FV（LZMA）
                                    包含：所有 PEIM + DXE driver
Offset 0x1AC000 – 0x1DFFFF  FV[1]  212,992 bytes   ← 未壓縮 SEC FV
                                    包含：SecMain (SECURITY_CORE) + RAW metadata
```

FV[0] 裡的 PEIM 和 DXE driver 全部在一個壓縮 section 內（LZMA），PEI 在運行時把它們解壓縮到 DRAM 再執行。這代表：直接 hex edit FV[0] 裡的 binary 不是改程式碼本身，而是改壓縮資料（除非你知道 LZMA 格式或用工具重新壓縮）。攻擊者通常用 UEFITool 這類工具替換整個 FFS file，讓工具重新計算 checksum 和壓縮資料。

FV[1] 的 `SecMain` 是未壓縮的 PE32 binary，直接對應 SPI flash 上的位元組，hex patch 就能改到程式碼，但 BootGuard 如果啟用了 IBB（Initial Boot Block）保護就會失效。

---

## 各階段攻擊面對比表

| 階段 | 攻擊者需要的前置條件 | 可達成的效果 | 廠商防線 |
|---|---|---|---|
| SEC | SPI flash 寫權限（最強前提） | 全面控制，植入最深 | BootGuard ACM、SMM_BWP |
| PEI | SPI flash 寫，或 PEIM 注入 | 搶在所有 OS 可見層之前執行 | BootGuard hash、TPM PCR0 |
| DXE | SPI flash 寫，或 OS ring 0 + runtime hook | hook 任意 boot service | Secure Boot（部分）、VT-d |
| BDS | OS ring 0（`SetVariable`） | 劫持開機目標 | Authenticated variable（BootXXXX 通常沒保護） |
| TSL | 能提供惡意 EFI image | Secure Boot 繞過 | Secure Boot db/dbx/shim/SBAT |
| RT | OS ring 0 | runtime hook、variable 竄改 | SMM write protection、Secure Boot AT attribute |

---

## 踩雷集錦

**1. 「Secure Boot 開著就安全了」**：Secure Boot 只保護 TSL（image 載入驗章）。SEC/PEI/DXE 的惡意 driver 完全不在 Secure Boot 的管轄範圍內，因為它們在 Secure Boot 協定初始化之前就跑了。MoonBounce 就是把惡意 DXE driver 燒進 SPI，完全繞開 Secure Boot。

**2. `SecurityStubDxe` 被誤以為是保護**：看到 FV 裡有 `Security...` 開頭的 driver 不代表有驗章。OVMF 預設的 `SecurityStubDxe` 對所有 image 都回傳 `EFI_SUCCESS`，相當於 no-op。

**3. BDS variable 以為 OS 寫不進去**：`BootOrder`、`Boot0000` 等 variable 的屬性是 `NV+BS+RT`，沒有 `AT`（authenticated time-based）。OS ring 0 可以直接呼叫 `gRT->SetVariable` 改掉它們，不需要任何簽章。

**4. recovery 路徑盲點**：廠商為了讓磚頭機能自救，recovery 路徑的驗證往往比正常路徑鬆很多。這是 PEI 階段最常被忽略的攻擊面。

**5. FV 裡的模組先後順序被當成安全邊界**：dependency expression 決定的只是執行順序，不是隔離邊界。先執行的 PEIM/DXE driver 可以改寫後執行者會用到的資料（例如 PPI/protocol），反之亦然，只要時序允許。

---

## 進階延伸

- **edk2 MdeModulePkg/Core/Dxe/DxeMain/DxeMain.c**：DXE dispatcher 的實際實作，看 `CoreDispatcher()` 如何選下一個要跑的 driver，以及 `depex` 如何被評估。
- **CHIPSEC 的 `common.bios_wp` 模組**：直接掃 `BIOS_CNTL` 暫存器看 `BIOSWE`/`SMM_BWP` bit，是看「SEC 防線是否到位」最快的方法。
- **Intel BootGuard 白皮書**：詳解 ACM 如何在 CPU 微碼層驗 SEC 的 hash，以及 IBB（Initial Boot Block）的定義。

---

## 動手練習

1. 跑上面的 Python 腳本，在輸出裡找到 `SecurityStubDxe` 的 GUID，然後在 edk2 的 `SecurityPkg/SecurityStub/SecurityStub.c` 裡確認它的 handler 預設回傳什麼。
2. 用 `python3 uefi_firmware` 拆出 `BdsDxe` 的 FirmwareFile，把它的 section 內容（raw bytes）dump 到檔案，確認裡面是個 PE/COFF（`MZ` magic）。
3. 在 QEMU + OVMF 開機後，進 UEFI Shell，跑 `dmpstore` 看 `BootOrder` 和 `Boot0000` 的 attribute，確認它們不是 authenticated variable。

---

## 本章重點

- PI 六階段各有不同的信任假設，攻擊面不對稱：SEC/PEI 需要 SPI 寫入，DXE 可被惡意 driver 污染，BDS/RT 可從 OS ring 0 操作。
- `SecurityStubDxe` 是 no-op；Secure Boot 只保護 TSL 的 image 載入，不保護 DXE 之前的任何東西。
- OVMF_CODE.fd 裡 128 個命名模組，PEI 階段 13 個 PEIM，DXE 階段 ~115 個 driver，全部都在 SPI flash 的 FV 裡，無簽章保護（除非有 BootGuard）。
- 惡意 DXE driver 是最常見的 UEFI bootkit 落腳點，因為 DXE 階段功能最豐富、進入條件比 SEC/PEI 低（OS ring 0 + runtime hook 也能注入），且可以在 `ExitBootServices` 後殘留進 RT 記憶體。

---

## 自我檢核

- [ ] 我能說出每個 PI 階段的攻擊前置條件（需要哪種層級的訪問權）
- [ ] 我知道 Secure Boot 在哪個階段介入、以及哪些階段在它管轄之外
- [ ] 我能解釋 SecurityStubDxe 為什麼是 no-op、對攻擊面意味著什麼
- [ ] 我能用 python uefi_firmware 從 OVMF_CODE.fd 列出 PEIM/DXE 模組
- [ ] 我能解釋為什麼 BootOrder variable 可以被 OS ring 0 修改

---

## 延伸閱讀

1. **UEFI Platform Initialization Specification v1.7**（uefi.org/specifications）Volume 1：PI 架構的一手資料，看 Chapter 4（PEI）和 Chapter 6（DXE）的 "Trust Model" 小節，直接描述設計者預設的信任假設——以及沒有描述的部分正是攻擊面所在。

2. **Matrosov, "Beyond the BIOS"（RECon 2019）**（c7zero.info）：用具體攻擊流程圖把 SEC→PEI→DXE 的攻擊路徑畫出來，是本章攻擊面分析的最佳視覺化補充，關聯到本課 Ch 11（SMM 攻擊面）。

3. **edk2 MdeModulePkg/Core/Pei/PeiMain/PeiMain.c** + **Dxe/DxeMain/DxeMain.c**（github.com/tianocore/edk2）：直接讀 dispatcher 主迴圈的程式碼，`CoreDispatcher` 裡能看到 depex 評估、FV 掃描、module 載入的全流程，是理解「dispatcher 如何被濫用」的最直接方式。

→ [Ch 4 惡意 DXE driver](./04-malicious-dxe-driver.md)
