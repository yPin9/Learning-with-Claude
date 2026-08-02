# 練習 A — 寫一個攔改開機流程的 DXE Driver

> **目標**：寫一個真實的 UEFI DXE Runtime driver，hook `gRT->SetVariable()`，攔截並 log 所有 variable 寫入呼叫（包含 name、GUID、size）。理解韌體層 hook 的機制：保存原始函數指標、替換、更新 CRC32。

> **環境**：edk2 + OVMF（QEMU）。edk2 build 需要 Ubuntu/Linux host（非 WSL 或標明「本段未實測」）；QEMU 執行段在 WSL 理論可用。**本練習的 build 步驟未在本機實測**，所有 `build` 指令皆為「理論預期行為」，完整理論指令照給，QEMU 可觀察的輸出格式也附上。

---

## 背景與動機

你在 Ch 4 學了「惡意 DXE driver」的概念，Ch 7–9 學了各類漏洞。現在把它們接起來：寫一個**主動的** DXE driver，不只是被載入，而是主動修改 runtime 服務表。

這個技術是韌體攻防的核心原語（primitive）：

- **攻擊者**用它 hook `LoadImage`（攔截一切 EFI 二進位的載入）、hook `SetVariable`（攔截 Secure Boot key 寫入）、hook `ExitBootServices`（在 OS 接手前做最後的持久化）。
- **防禦者**用它做 variable policy enforcement、audit trail、甚至 integrity check。

學會這個原語，後面的 bootkit 構造（Ch 31）就是在這裡加一層持久化邏輯。

---

## 任務規格

### 輸入 / 輸出

| 項目 | 說明 |
|------|------|
| 輸入 | 任何程式（UEFI shell、BDS、其他 driver）呼叫 `gRT->SetVariable()` |
| 輸出 | 透過 `DEBUG()` macro 印出：caller 傳入的 variable name（Unicode）、GUID（hex）、Attributes、DataSize |
| 驗收 | QEMU 的 OVMF debug 輸出或 serial console 顯示 hook log；原始 SetVariable 功能正常（BootOrder 改了真的有效）|

### 技術要求

1. 以 DXE Runtime Driver（`MODULE_TYPE = DXE_RUNTIME_DRIVER`）實作，這樣它在 ExitBootServices 後仍然存在。
2. hook 後**必須呼叫原始函數**，不能斷鏈（否則 UEFI 環境崩潰）。
3. 更新 `gRT->Hdr.CRC32`：每次修改 System Table 的任何指標都要重新算 CRC32，否則某些韌體會拒絕呼叫。
4. hook 程式碼要兼容 `EFI_EVENT` `EVT_SIGNAL_VIRTUAL_ADDRESS_CHANGE`——ExitBootServices 後地址重映射時，hook 函數的指標也要跟著更新。

---

## 期望輸出範例

QEMU OVMF 的 DEBUG console（`-serial stdio` 或 `-debugcon file:debug.log,id=uefi_debug`）應看到：

```
[SetVarHook] Name=BootOrder Guid=8be4df61-93ca-11d2-aa0d-00e098032b8c Attrs=0x7 Size=6
[SetVarHook] Name=Boot0001 Guid=8be4df61-93ca-11d2-aa0d-00e098032b8c Attrs=0x7 Size=...
[SetVarHook] Name=ConOut Guid=8be4df61-93ca-11d2-aa0d-00e098032b8c Attrs=0x7 Size=...
[SetVarHook] Name=SecureBoot Guid=8be4df61-93ca-11d2-aa0d-00e098032b8c Attrs=0x6 Size=1
```

每一行對應一個 SetVariable 呼叫。開機過程中典型有 20–50 個。

---

## 如果卡住

1. **CRC32 更新後 UEFI 仍然崩潰**：確認用的是 edk2 的 `CalculateCrc32()` 而不是一般 CRC32 算法——UEFI 用的是標準 CRC32/ISO 3309，edk2 的 `CalculateCrc32` 就是這個，不需要外部庫。更新範圍是整個 `EFI_RUNTIME_SERVICES` 結構（從 `Hdr` 到結構結束），大小從 `gRT->Hdr.HeaderSize` 讀。

2. **VirtualAddressChange event 後 hook 失效或 crash**：`EVT_SIGNAL_VIRTUAL_ADDRESS_CHANGE` 的 handler 裡要用 `EfiConvertPointer()` 把 `gOrigSetVariable`（存原始指標的全域變數）從物理地址轉成虛擬地址。漏掉這步，OS 階段呼叫 SetVariable 時原始指標仍然是物理地址，呼叫即 crash。

3. **`DEBUG()` 沒有輸出**：OVMF 預設 DEBUG build 才有 debug log。確認用 `OvmfPkg/OvmfPkgX64.dsc` 的 DEBUG target 建置（`-b DEBUG`），且 `DebugLib` 是 `SerialPortDebugLib` 或 `PeiDxeDebugLibReportStatusCode`。RELEASE build 的 DEBUG macro 是 no-op。

---

## 實作步驟（五步）

### Step 1：寫 .inf（Module Information File）

`.inf` 是 edk2 的 module manifest，告訴 build system 這個 driver 用什麼 library、export 什麼 protocol、依賴什麼。

檔案：`SetVarHookDxe/SetVarHookDxe.inf`

```ini
[Defines]
  INF_VERSION                    = 0x00010005
  BASE_NAME                      = SetVarHookDxe
  FILE_GUID                      = 12345678-1234-1234-1234-123456789ABC
  MODULE_TYPE                    = DXE_RUNTIME_DRIVER
  VERSION_STRING                 = 1.0
  ENTRY_POINT                    = SetVarHookDxeEntryPoint

[Sources]
  SetVarHookDxe.c

[Packages]
  MdePkg/MdePkg.dec
  MdeModulePkg/MdeModulePkg.dec

[LibraryClasses]
  UefiDriverEntryPoint
  UefiBootServicesTableLib
  UefiRuntimeServicesTableLib
  UefiRuntimeLib
  BaseLib
  BaseMemoryLib
  DebugLib
  PrintLib

[Protocols]
  # 不安裝也不依賴任何 protocol，純 hook

[Depex]
  TRUE
```

關鍵點：
- `MODULE_TYPE = DXE_RUNTIME_DRIVER`：告訴 DXE dispatcher 這個 driver 在 ExitBootServices 後仍然要保留在記憶體（使用 `EfiRuntimeServicesCode/Data` 類型）。
- `UefiRuntimeLib`：提供 `EfiConvertPointer()`，地址轉換用。
- `ENTRY_POINT = SetVarHookDxeEntryPoint`：入口函數名稱，必須與 .c 一致。

---

### Step 2：實作 hook 核心邏輯

檔案：`SetVarHookDxe/SetVarHookDxe.c`

```c
#include <Uefi.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiRuntimeServicesTableLib.h>
#include <Library/UefiRuntimeLib.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DebugLib.h>
#include <Library/PrintLib.h>
#include <Guid/EventGroup.h>

/* 全域：保存原始函數指標 */
static EFI_SET_VARIABLE  gOrigSetVariable = NULL;
static EFI_EVENT         gVirtualAddrChangeEvent;

/* hook 函數：攔截所有 SetVariable 呼叫 */
static EFI_STATUS
EFIAPI
HookedSetVariable (
  IN  CHAR16                       *VariableName,
  IN  EFI_GUID                     *VendorGuid,
  IN  UINT32                       Attributes,
  IN  UINTN                        DataSize,
  IN  VOID                         *Data
  )
{
  /* 印出呼叫資訊 */
  DEBUG ((DEBUG_INFO,
    "[SetVarHook] Name=%s Guid=%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x "
    "Attrs=0x%x Size=%d\n",
    VariableName,
    VendorGuid->Data1, VendorGuid->Data2, VendorGuid->Data3,
    VendorGuid->Data4[0], VendorGuid->Data4[1],
    VendorGuid->Data4[2], VendorGuid->Data4[3],
    VendorGuid->Data4[4], VendorGuid->Data4[5],
    VendorGuid->Data4[6], VendorGuid->Data4[7],
    Attributes, DataSize
  ));

  /* 必須呼叫原始函數，否則 UEFI 功能中斷 */
  return gOrigSetVariable (VariableName, VendorGuid, Attributes, DataSize, Data);
}
```

---

### Step 3：保存原始指標並安裝 hook

```c
/* 更新 gRT 的 CRC32（修改 System Table 後必須做） */
static VOID
UpdateRtCrc32 (VOID)
{
  gRT->Hdr.CRC32 = 0;
  gBS->CalculateCrc32 (gRT, gRT->Hdr.HeaderSize, &gRT->Hdr.CRC32);
}

/* VirtualAddressChange 事件 handler：更新指標到虛擬地址 */
static VOID
EFIAPI
OnVirtualAddressChange (
  IN EFI_EVENT  Event,
  IN VOID       *Context
  )
{
  /* 把保存的原始指標從物理地址轉成虛擬地址 */
  EfiConvertPointer (0, (VOID **)&gOrigSetVariable);
}

/* 模組入口點 */
EFI_STATUS
EFIAPI
SetVarHookDxeEntryPoint (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS Status;

  /* 1. 保存原始 SetVariable 指標 */
  gOrigSetVariable = gRT->SetVariable;

  /* 2. 替換成我們的 hook */
  gRT->SetVariable = HookedSetVariable;

  /* 3. 更新 CRC32（漏掉就可能被 firmware sanity check 拒絕） */
  UpdateRtCrc32 ();

  /* 4. 訂閱 VirtualAddressChange event，確保 OS 啟動後指標仍然有效 */
  Status = gBS->CreateEventEx (
                  EVT_NOTIFY_SIGNAL,
                  TPL_NOTIFY,
                  OnVirtualAddressChange,
                  NULL,
                  &gEfiEventVirtualAddressChangeGuid,
                  &gVirtualAddrChangeEvent
                  );
  if (EFI_ERROR (Status)) {
    /* 還原 hook，避免後續 crash */
    gRT->SetVariable = gOrigSetVariable;
    UpdateRtCrc32 ();
    return Status;
  }

  DEBUG ((DEBUG_INFO, "[SetVarHook] Installed. gRT->SetVariable hooked.\n"));
  return EFI_SUCCESS;
}
```

---

### Step 4：Build（edk2 環境）

**本段未實測，為理論預期行為。** 以下指令在 Ubuntu 22.04 + edk2 clone 上執行：

```bash
# 1. 設定 edk2 build 環境
cd ~/edk2
source edksetup.sh

# 2. 把 SetVarHookDxe 加到某個 DSC（以 OvmfPkg 為例）
# 在 OvmfPkg/OvmfPkgX64.dsc [Components] 段加入：
# OvmfPkg/SetVarHookDxe/SetVarHookDxe.inf

# 同時在 OvmfPkgX64.fdf [FV.DXEFV] 段加入：
# INF OvmfPkg/SetVarHookDxe/SetVarHookDxe.inf

# 3. 建置 DEBUG target（DEBUG 才有 serial output）
build -a X64 -t GCC5 -p OvmfPkg/OvmfPkgX64.dsc -b DEBUG

# 輸出的 OVMF.fd 在：
# Build/OvmfX64/DEBUG_GCC5/FV/OVMF.fd
```

或者用獨立方式：把 driver build 成獨立 .efi，放到 ESP，從 UEFI Shell 手動 load：

```bash
# 建置獨立模組
build -a X64 -t GCC5 -p MdeModulePkg/MdeModulePkg.dsc \
      -m OvmfPkg/SetVarHookDxe/SetVarHookDxe.inf -b DEBUG

# 產出: Build/.../SetVarHookDxe.efi
```

---

### Step 5：QEMU 跑起來觀察

**本段 QEMU 指令可在 WSL 執行**（WSL 已裝 `qemu-system-x86_64` + OVMF）：

```bash
# 準備 ESP（包含 SetVarHookDxe.efi，如果手動 load 方式）
mkdir -p /tmp/esp/EFI/BOOT
cp SetVarHookDxe.efi /tmp/esp/
cp startup.nsh /tmp/esp/  # 內容: load fs0:\SetVarHookDxe.efi

# 建立 ESP image
dd if=/dev/zero of=/tmp/esp.img bs=1M count=64
mkfs.vfat -F 32 /tmp/esp.img
mcopy -i /tmp/esp.img -s /tmp/esp/::

# 啟動 QEMU，開 debug console
qemu-system-x86_64 \
  -machine q35,smm=on \
  -cpu Skylake-Client \
  -m 512M \
  -drive if=pflash,format=raw,file=/usr/share/OVMF/OVMF_CODE.fd,readonly=on \
  -drive if=pflash,format=raw,file=/tmp/OVMF_VARS.fd \
  -drive format=raw,file=/tmp/esp.img \
  -serial stdio \
  -debugcon file:/tmp/debug.log,id=uefi_debug \
  -global isa-debugcon.iobase=0x402 \
  -nographic

# 在另一個 terminal 追 debug log
tail -f /tmp/debug.log | grep SetVarHook
```

**預期輸出**（`/tmp/debug.log` 中）：
```
[SetVarHook] Installed. gRT->SetVariable hooked.
[SetVarHook] Name=ConIn Guid=8be4df61-... Attrs=0x7 Size=...
[SetVarHook] Name=ConOut Guid=8be4df61-... Attrs=0x7 Size=...
[SetVarHook] Name=BootOrder Guid=8be4df61-... Attrs=0x7 Size=4
[SetVarHook] Name=Boot0000 Guid=8be4df61-... Attrs=0x7 Size=...
```

---

## 完整參考解答

<details>
<summary>點開查看完整 .inf + .c（先自己試，卡住再看）</summary>

### SetVarHookDxe.inf（完整版）

```ini
[Defines]
  INF_VERSION                    = 0x00010005
  BASE_NAME                      = SetVarHookDxe
  FILE_GUID                      = 12345678-1234-1234-1234-123456789ABC
  MODULE_TYPE                    = DXE_RUNTIME_DRIVER
  VERSION_STRING                 = 1.0
  ENTRY_POINT                    = SetVarHookDxeEntryPoint

[Sources]
  SetVarHookDxe.c

[Packages]
  MdePkg/MdePkg.dec
  MdeModulePkg/MdeModulePkg.dec

[LibraryClasses]
  UefiDriverEntryPoint
  UefiBootServicesTableLib
  UefiRuntimeServicesTableLib
  UefiRuntimeLib
  BaseLib
  BaseMemoryLib
  DebugLib
  PrintLib

[Depex]
  TRUE
```

### SetVarHookDxe.c（完整版）

```c
/** @file
    SetVarHookDxe — hook gRT->SetVariable() 並 log 所有呼叫
    DXE Runtime Driver，存活至 OS 啟動後

    教育用途：示範 UEFI runtime hook 機制
**/

#include <Uefi.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiRuntimeServicesTableLib.h>
#include <Library/UefiRuntimeLib.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DebugLib.h>
#include <Library/PrintLib.h>
#include <Guid/EventGroup.h>

/* 保存原始函數指標（全域，讓 VirtualAddressChange handler 能轉換它） */
STATIC EFI_SET_VARIABLE  gOrigSetVariable      = NULL;
STATIC EFI_EVENT         gVirtualAddrChangeEvent;

/**
  Hook 函數：替換 gRT->SetVariable。
  攔截呼叫、印出資訊後，轉發給原始函數。
**/
STATIC
EFI_STATUS
EFIAPI
HookedSetVariable (
  IN  CHAR16     *VariableName,
  IN  EFI_GUID   *VendorGuid,
  IN  UINT32     Attributes,
  IN  UINTN      DataSize,
  IN  VOID       *Data
  )
{
  /* 防禦：NULL 指標保護 */
  if (VariableName == NULL || VendorGuid == NULL) {
    return EFI_INVALID_PARAMETER;
  }

  DEBUG ((
    DEBUG_INFO,
    "[SetVarHook] Name=%-20s "
    "Guid=%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x "
    "Attrs=0x%08x Size=%5d\n",
    VariableName,
    VendorGuid->Data1,
    VendorGuid->Data2,
    VendorGuid->Data3,
    VendorGuid->Data4[0], VendorGuid->Data4[1],
    VendorGuid->Data4[2], VendorGuid->Data4[3],
    VendorGuid->Data4[4], VendorGuid->Data4[5],
    VendorGuid->Data4[6], VendorGuid->Data4[7],
    Attributes,
    (UINT32)DataSize
  ));

  /* 轉發給原始 SetVariable */
  return gOrigSetVariable (VariableName, VendorGuid, Attributes, DataSize, Data);
}

/**
  更新 EFI_RUNTIME_SERVICES 的 CRC32。
  任何修改 gRT 指標後都必須呼叫，否則部分韌體的 sanity check 會 fail。
**/
STATIC
VOID
UpdateRtCrc32 (
  VOID
  )
{
  gRT->Hdr.CRC32 = 0;
  gBS->CalculateCrc32 (
         (VOID *)gRT,
         gRT->Hdr.HeaderSize,
         &gRT->Hdr.CRC32
         );
}

/**
  VirtualAddressChange 事件 handler。
  OS 呼叫 SetVirtualAddressMap() 時觸發；此時需要把物理地址的指標
  轉換成對應的虛擬地址，否則 OS 階段的 gRT 呼叫會使用失效的物理地址。
**/
STATIC
VOID
EFIAPI
OnVirtualAddressChange (
  IN EFI_EVENT  Event,
  IN VOID       *Context
  )
{
  EfiConvertPointer (0x0, (VOID **)&gOrigSetVariable);
  /* HookedSetVariable 是 .text 段，由 EFI Runtime 記憶體重映射自動處理 */
}

/**
  模組入口點。
  在 DXE 階段（開機中途）被 DXE dispatcher 呼叫。
**/
EFI_STATUS
EFIAPI
SetVarHookDxeEntryPoint (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS  Status;

  /* 1. 保存原始 SetVariable 指標 */
  gOrigSetVariable = gRT->SetVariable;
  if (gOrigSetVariable == NULL) {
    DEBUG ((DEBUG_ERROR, "[SetVarHook] gRT->SetVariable is NULL, aborting\n"));
    return EFI_UNSUPPORTED;
  }

  /* 2. 安裝 hook */
  gRT->SetVariable = HookedSetVariable;

  /* 3. 更新 CRC32 */
  UpdateRtCrc32 ();

  /* 4. 訂閱 VirtualAddressChange event */
  Status = gBS->CreateEventEx (
                  EVT_NOTIFY_SIGNAL,
                  TPL_NOTIFY,
                  OnVirtualAddressChange,
                  NULL,
                  &gEfiEventVirtualAddressChangeGuid,
                  &gVirtualAddrChangeEvent
                  );
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_ERROR,
      "[SetVarHook] CreateEventEx failed: %r, reverting hook\n", Status));
    gRT->SetVariable = gOrigSetVariable;
    UpdateRtCrc32 ();
    return Status;
  }

  DEBUG ((DEBUG_INFO,
    "[SetVarHook] Installed successfully. gRT->SetVariable @ %p -> HookedSetVariable\n",
    (VOID *)gOrigSetVariable
  ));

  return EFI_SUCCESS;
}
```

</details>

---

## 測試用例表

| 測試案例 | 預期行為 | 驗證方法 |
|---------|---------|---------|
| 正常開機（BDS 設定 BootOrder） | hook log 出現 `Name=BootOrder`，開機正常完成 | 看 debug.log |
| 從 UEFI Shell 執行 `setvar` | hook log 出現對應 variable name | shell 輸出 + debug.log |
| 嘗試寫 `SecureBoot`（應被韌體拒絕） | hook 有 log（hook 先跑），original 回傳 `EFI_WRITE_PROTECTED` | debug.log + shell 顯示 error |
| OS 啟動後（Linux）`efivar` 寫入 | hook 仍然攔截（因為是 Runtime driver） | OS 的 efi_attr demo / dmesg |
| DataSize=0（delete variable） | hook 正確 log `Size=0`，delete 成功 | debug.log |

---

## 延伸挑戰

### 挑戰 1：改成 LoadImage hook（攔截所有 EFI 載入）

把 hook 目標換成 `gBS->LoadImage`——每個 .efi 二進位被載入都會走這裡。攔截後可以：
- 印出被載入的 image 路徑
- 驗算 image hash（比對白名單）
- 直接在這裡塞後門（persistence bootkit 的一步）

```c
/* 改 hook gBS->LoadImage（概念） */
gOrigLoadImage = gBS->LoadImage;
gBS->LoadImage = HookedLoadImage;
/* 更新 gBS 的 CRC32（gBS->Hdr.CRC32）*/
```

注意：`gBS` 是 Boot Services，在 ExitBootServices 後失效，不需要 VirtualAddressChange handler（但也因此 OS 階段呼不到）。

### 挑戰 2：攔截 BootOrder 並持久化後門

在 `HookedSetVariable` 裡：
```
if (StrCmp(VariableName, L"BootOrder") == 0) {
    /* 在 BootOrder 的前面插入我們的 Boot entry（如 Boot0099） */
    /* 先呼叫 gOrigSetVariable 設定 Boot0099 指向我們的 EFI */
    /* 再呼叫 gOrigSetVariable 寫入竄改後的 BootOrder */
    /* 跳過呼叫原始 BootOrder SetVariable（或讓它完成後再覆寫）*/
}
```

這是「persistence bootkit 最小 PoC」的核心：不改 BIOS flash，只靠 NVRAM 的 BootOrder 讓惡意 EFI 每次開機都被執行。

### 挑戰 3：把 hook log 輸出到 NVRAM variable

改用 `AppendWrite`（`EFI_VARIABLE_APPEND_WRITE`）把 audit log 持續附加到一個自定義 variable（如 `HookLog-XXXXXXXX`），OS 啟動後可以用 efivar 讀出。這是「不依賴 debug console」的持久化 audit trail。

---

## 這份練習在課程地圖的位置

```
Ch 4（惡意 DXE driver 概念）
    │
    ▼
Ch 7–9（漏洞類型、挖洞、runtime 邊界）
    │
    ▼
練習 A（本章：動手 hook，理解 hook 機制）
    │
    ▼
Ch 10–13（SMM：更深的 ring -2 hook）
    │
    ▼
Ch 31（bootkit 構造：把 hook + persistence 組合起來）
```

這裡學的 `gRT->SetVariable` hook 是最小可行的 hook 原語。bootkit 構造章（Ch 31）會把這個原語和 SPI flash 寫入、NVRAM persistence 組合成完整攻擊鏈。

---

→ [Ch 10：SMM / SMRAM / SMI：Ring -2 為何是聖杯](./10-smm-smram-smi.md)
