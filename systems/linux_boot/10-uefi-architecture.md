# Ch 10 — UEFI 架構：Boot Services / Runtime Services / Protocol

> 目標：搞懂 UEFI 不只是「新版 BIOS」 — 它是個小 OS。理解 phase、service 跟 protocol 三個核心概念。

## 我們在哪裡

第 2 階段 (Firmware) 的 UEFI 版本。對照 Ch 4 的 BIOS。

## UEFI 是什麼（一句話）

UEFI 是 Intel 主導的開機韌體規範。它定義了：

- 韌體開機後到 OS 接手之間的階段順序
- 韌體提供給 bootloader / OS 的 API（Boot Services + Runtime Services）
- 硬體驅動的標準介面（Protocol）
- bootloader 的執行檔格式（PE/COFF）
- 開機磁碟的分割表（GPT）跟檔案系統（FAT32 ESP）

**不是只有 firmware** — 它是個生態系。

## UEFI 的開機階段 (PI Spec)

UEFI Platform Initialization spec 定義了 7 個階段：

```
 SEC (Security)
   │ CPU reset 後第一段，初始化 cache as RAM
   ▼
 PEI (Pre-EFI Initialization)
   │ 初始化 RAM、找到 DXE volume
   ▼
 DXE (Driver Execution Environment)
   │ 載 driver、建立 protocol、初始化大部分硬體
   ▼
 BDS (Boot Device Selection)
   │ 讀 NVRAM 變數、找 bootloader、執行
   ▼
 TSL (Transient System Load)
   │ bootloader 跑、ExitBootServices() 之前
   ▼
 RT (Runtime)
   │ ExitBootServices() 之後，OS 跑
   ▼
 AL (After Life)
   │ Shutdown / reset 後
```

對使用者最重要的是 **BDS** — 這階段你按 F2/F12 進的「BIOS 設定畫面」、「開機選單」就在這。

## Boot Services vs Runtime Services

UEFI 給 bootloader 跟 OS 兩組 API：

```
 Boot Services:                       Runtime Services:
 ─────────────                        ─────────────────
 OS 啟動前可用                        OS 啟動後也可用
 含 file/disk/network/memory I/O     很少，主要是 NVRAM 變數
 ExitBootServices() 後消失            一直在記憶體裡
```

### Boot Services 提供什麼

- 記憶體管理：`AllocatePages`、`FreePages`、`GetMemoryMap`
- File I/O：`OpenProtocol(EFI_SIMPLE_FILE_SYSTEM_PROTOCOL)` → 讀檔
- 圖形：`EFI_GRAPHICS_OUTPUT_PROTOCOL`
- 網路：`EFI_TCP4_PROTOCOL`、`EFI_HTTP_PROTOCOL`（HTTP boot！）
- console：`SystemTable->ConOut->OutputString()`
- 載 driver、找 protocol：`LocateProtocol`
- 計時器：`CreateEvent`、`SetTimer`

bootloader 寫起來像在寫 application — 有完整 lib，遠比 BIOS INT 舒服。

### Runtime Services 提供什麼

- NVRAM 變數：`GetVariable` / `SetVariable`（這是 efibootmgr 背後的東西）
- 時間：`GetTime` / `SetTime`
- reset：`ResetSystem`
- capsule update：firmware update 機制

**就這些，沒了**。OS 啟動後絕大多數 UEFI 功能消失，因為 OS 接管了硬體。

## ExitBootServices() — 關鍵的告別

bootloader 的最後一步：呼叫 `gBS->ExitBootServices()`。意義：

- 告訴 UEFI「我要接管硬體了」
- UEFI 釋放 boot service 的記憶體（可能是幾 MB）
- 之後 boot service handle 全部 invalid，再呼叫會 crash
- 從這一刻起 OS 完全自己負責

```c
EFI_STATUS status = gBS->ExitBootServices(ImageHandle, MemoryMapKey);
// 從這行之後 boot services 都不能用了
```

`MemoryMapKey` 必須是最近一次 `GetMemoryMap` 的 key，不然會回 `EFI_INVALID_PARAMETER`。實務上 bootloader 會 retry：拿 map → ExitBootServices → 失敗就再拿 map → 再 try。

## Protocol 是什麼

UEFI 的「driver 介面」叫 **Protocol**。每個 protocol 是一組 function pointer 的 struct，由 GUID 識別。

例：file system protocol 的 GUID 是 `964e5b22-6459-11d2-8e39-00a0c969723b`。要讀 ESP 上的檔案：

```c
EFI_GUID gEfiSimpleFileSystemProtocolGuid = ...;
EFI_SIMPLE_FILE_SYSTEM_PROTOCOL *fs;

gBS->LocateProtocol(&gEfiSimpleFileSystemProtocolGuid, NULL, (void**)&fs);
fs->OpenVolume(fs, &root);
root->Open(root, &file, L"\\boot\\vmlinuz", EFI_FILE_MODE_READ, 0);
```

整個 UEFI 是「protocol 組合而成」的設計。寫 driver = 註冊新 protocol；寫 app = 使用現有 protocol。

常用的 protocol：

| Protocol | 用途 |
|---|---|
| `EFI_SIMPLE_TEXT_INPUT_PROTOCOL` | 鍵盤 |
| `EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL` | console 輸出 |
| `EFI_SIMPLE_FILE_SYSTEM_PROTOCOL` | 檔案系統 |
| `EFI_BLOCK_IO_PROTOCOL` | block 裝置 (硬碟原始 access) |
| `EFI_GRAPHICS_OUTPUT_PROTOCOL` | framebuffer |
| `EFI_LOADED_IMAGE_PROTOCOL` | 載入後的 image 自我描述 |
| `EFI_DEVICE_PATH_PROTOCOL` | 描述硬體路徑 (PCI/USB/...) |
| `EFI_TCP4_PROTOCOL` | TCP |

## SystemTable

UEFI app 的 entry point 簽名：

```c
EFI_STATUS efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable);
```

`SystemTable` 是 UEFI 給你的「全世界的入口」：

```c
typedef struct {
    EFI_TABLE_HEADER Hdr;
    CHAR16 *FirmwareVendor;
    UINT32 FirmwareRevision;
    EFI_HANDLE ConsoleInHandle;
    EFI_SIMPLE_TEXT_INPUT_PROTOCOL *ConIn;
    EFI_HANDLE ConsoleOutHandle;
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL *ConOut;
    EFI_HANDLE StandardErrorHandle;
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL *StdErr;
    EFI_RUNTIME_SERVICES *RuntimeServices;
    EFI_BOOT_SERVICES *BootServices;
    UINTN NumberOfTableEntries;
    EFI_CONFIGURATION_TABLE *ConfigurationTable;
} EFI_SYSTEM_TABLE;
```

最常用的：

- `SystemTable->ConOut->OutputString(SystemTable->ConOut, L"Hello\n")` — printf
- `SystemTable->BootServices->...` — 所有 boot service
- `SystemTable->RuntimeServices->...` — runtime service

`ConfigurationTable` 含 ACPI、SMBIOS 等 table 的指標 — kernel 會從這裡拿。

## PE/COFF 格式

UEFI app 是 **PE/COFF executable**（跟 Windows .exe 同 family）。為什麼？

- Intel 想要跨 CPU 架構的 executable 格式
- ELF 那時還沒這麼通用，PE 已經被 Windows 用得很穩
- PE 結構簡單，bootloader 自己 parse 不痛苦

UEFI 的 entry 簽名 `efi_main` 編進 PE 的 entry point。UEFI 韌體 load 完整個 image 後 call entry point，傳兩個參數。

## 一個常見誤解：「UEFI = 圖形 BIOS 設定畫面」

UEFI 跟「圖形 BIOS 設定畫面」**沒有必然關係**。

- 圖形 setup 是廠商 OEM 寫的 UEFI app（DXE driver）
- UEFI spec 沒規定一定要圖形
- 早期 UEFI 機器都是文字 setup，跟 BIOS 看起來一樣
- 現代 UEFI 機器的圖形 setup 是 ASUS/MSI 自己貼進韌體的

不要把「炫炮 setup」跟「UEFI 本身」混為一談。

## 一個常見誤解：「UEFI 一定有 Secure Boot」

Secure Boot 是 UEFI 的 **可選功能**。可以關掉，不用簽章一樣能開機。

關 Secure Boot 不會讓 UEFI 變 BIOS — 它還是 UEFI 的開機流程，只是不檢查 bootloader 簽章。Ch 24 會詳細講。

## 一個常見誤解：「UEFI 看不到 BIOS INT」

對 — UEFI 完全沒有 BIOS INT 服務。但 CSM (Compatibility Support Module) 可以「模擬 BIOS」：

- 韌體載一個叫 CSM 的 module
- CSM 提供 INT 10h、INT 13h 等假 BIOS 服務
- legacy bootloader（如老的 GRUB 1）以為自己在 BIOS 上跑

CSM 在 2020 年起被 Intel 淘汰。新主機板沒有 CSM 就沒辦法跑 legacy MBR bootloader。

## 動手練習

**1. 看你機器的 UEFI 變數**

```bash
ls /sys/firmware/efi/efivars/ | head -20
```

每一個檔名就是一個 NVRAM 變數，後面接 GUID。

**2. 看 BootOrder**

```bash
sudo efibootmgr -v
```

`BootOrder` 那行是 firmware 嘗試開機的順序。每個 `Boot0001` / `Boot0002` 是一個變數，描述「從哪個檔案開機」。

**3. 用 OVMF 開個空 UEFI shell**

```bash
mkdir -p uefi-test
cp /usr/share/OVMF/OVMF_VARS.fd uefi-test/
qemu-system-x86_64 -m 256 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=uefi-test/OVMF_VARS.fd \
  -nographic
```

進到 UEFI shell 後（如果 OVMF 含 shell）試：

```
Shell> ls
Shell> map
Shell> exit
```

`exit` 會回到 BDS 選單，可以看 firmware UI。

## 自我檢核

- [ ] 講得出 UEFI 7 個 phase（SEC → PEI → DXE → BDS → TSL → RT → AL）
- [ ] 知道 Boot Services 跟 Runtime Services 差在哪、ExitBootServices() 是什麼
- [ ] 懂 Protocol 用 GUID 識別、`LocateProtocol` 怎麼用
- [ ] 知道 UEFI app 是 PE/COFF 格式
- [ ] 跑過 OVMF + UEFI shell

下一章看 ESP 跟 efibootmgr — bootloader 怎麼放、怎麼註冊。

→ [Ch 11 ESP、efibootmgr、NVRAM 變數](./11-esp-and-efibootmgr.md)
