# Ch 12 — UEFI Boot Services 與 Runtime Services

> **目標**：理解 UEFI 提供給 `.efi` 程式的兩大類服務——Boot Services（開機階段可用）和 Runtime Services（OS 執行後仍可用）、System Table 的結構、Protocol 與 Handle 的物件模型，讓你知道 UEFI bootloader「能呼叫哪些服務」。

> **環境**：UEFI 2.x spec，gnu-efi。本章是概念與 API 結構，Ch 13 開始實際呼叫。

## 為什麼要懂 UEFI 服務的分類？

UEFI bootloader 是「跑在韌體 OS 上的應用程式」（Ch 10），它透過呼叫 UEFI 提供的**服務**做事——配置記憶體、讀檔案、操作裝置、讀寫變數。但這些服務分兩類，有個關鍵區別：

- **Boot Services**：只在開機階段可用。一旦 bootloader 呼叫 `ExitBootServices`（把控制權交給 kernel），這些服務就消失了
- **Runtime Services**：OS 接手後仍然可用（kernel 能呼叫它們，如讀寫 UEFI 變數、設定時間）

搞混這兩類會出大問題——在 ExitBootServices 後用 Boot Service = 崩潰。這章把服務的分類和取用方式講清楚。

## 先建立直覺：兩類服務的生命週期

```
UEFI bootloader 的時間軸：

  .efi 載入並執行
        │
  ┌─────────────────────────────────┐
  │  開機階段                         │
  │  Boot Services 可用：             │
  │  - AllocatePool（配記憶體）       │
  │  - 讀檔案（透過 protocol）        │
  │  - GetMemoryMap（記憶體地圖）     │
  │  - 操作裝置                       │
  │                                  │
  │  Runtime Services 也可用          │
  └────────────┬────────────────────┘
               │ 呼叫 ExitBootServices()
               ▼  ★ Boot Services 從此消失！
  ┌─────────────────────────────────┐
  │  kernel 接手（OS 階段）           │
  │  只剩 Runtime Services：          │
  │  - GetVariable/SetVariable        │
  │  - GetTime/SetTime                │
  │  - ResetSystem                    │
  └─────────────────────────────────┘
```

關鍵分界線是 `ExitBootServices`（Ch 14 詳述）。它之前是「韌體還管理硬體」，之後是「kernel 接管硬體」。Boot Services 是韌體管理硬體時提供的服務，kernel 接管後就不存在了；Runtime Services 是韌體保留給 OS 用的少數服務。

## System Table：服務的入口

`.efi` 程式的 entry point 收到一個 **System Table**——所有 UEFI 服務的入口：

```c
// UEFI .efi 程式的 entry point
EFI_STATUS efi_main(EFI_HANDLE image_handle, EFI_SYSTEM_TABLE *system_table)
{                          //   ↑ 這個 image 的 handle    ↑ 服務入口表
    // 透過 system_table 取用所有服務
    system_table->ConOut->OutputString(system_table->ConOut, L"Hello\n");
    //          ↑ 控制台輸出
    return EFI_SUCCESS;
}
```

System Table 的結構（簡化）：

```c
struct EFI_SYSTEM_TABLE {
    EFI_TABLE_HEADER  Hdr;
    CHAR16           *FirmwareVendor;     // 韌體廠商（如 "EDK II"）
    UINT32            FirmwareRevision;
    EFI_HANDLE        ConsoleInHandle;
    EFI_SIMPLE_TEXT_INPUT_PROTOCOL  *ConIn;   // 控制台輸入（鍵盤）
    EFI_HANDLE        ConsoleOutHandle;
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL *ConOut;  // 控制台輸出（螢幕）
    EFI_HANDLE        StandardErrorHandle;
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL *StdErr;
    EFI_RUNTIME_SERVICES *RuntimeServices;    // ← Runtime Services 表
    EFI_BOOT_SERVICES    *BootServices;       // ← Boot Services 表
    UINTN             NumberOfTableEntries;
    EFI_CONFIGURATION_TABLE *ConfigurationTable; // ACPI/SMBIOS 表等
};
```

`BootServices` 和 `RuntimeServices` 各是一張函式指標表。你透過 `system_table->BootServices->AllocatePool(...)` 這種方式呼叫服務。

## Boot Services：開機階段的工具箱

Boot Services 提供開機階段需要的功能：

```c
struct EFI_BOOT_SERVICES {
    // 記憶體管理
    EFI_ALLOCATE_PAGES   AllocatePages;     // 配整頁記憶體
    EFI_FREE_PAGES       FreePages;
    EFI_GET_MEMORY_MAP   GetMemoryMap;       // 取記憶體地圖（Ch 14）
    EFI_ALLOCATE_POOL    AllocatePool;       // 配任意大小（像 malloc）
    EFI_FREE_POOL        FreePool;

    // Protocol 管理（取用裝置/服務）
    EFI_LOCATE_PROTOCOL  LocateProtocol;     // 找一個 protocol
    EFI_HANDLE_PROTOCOL  HandleProtocol;     // 從 handle 取 protocol
    EFI_OPEN_PROTOCOL    OpenProtocol;

    // 影像（載入其他 .efi）
    EFI_IMAGE_LOAD       LoadImage;          // 載入另一個 .efi（如 kernel）
    EFI_IMAGE_START      StartImage;

    // 事件與時間
    EFI_STALL            Stall;              // 忙等（微秒）
    EFI_SET_WATCHDOG_TIMER SetWatchdogTimer;

    // 退出開機服務（關鍵！）
    EFI_EXIT_BOOT_SERVICES ExitBootServices; // 交棒給 kernel（Ch 14）
    // ... 還有很多
};
```

常用的幾個：
- `AllocatePool` / `FreePool`：像 malloc/free，配置記憶體
- `GetMemoryMap`：取記憶體地圖（交給 kernel 前必做，Ch 14）
- `LocateProtocol` / `HandleProtocol`：取用 protocol（讀檔案、操作裝置都靠這）
- `LoadImage` / `StartImage`：載入並執行另一個 `.efi`
- `ExitBootServices`：交棒給 kernel（Ch 14）

## Runtime Services：OS 也能用的服務

Runtime Services 是韌體保留給 OS 的少數服務，ExitBootServices 後仍可用：

```c
struct EFI_RUNTIME_SERVICES {
    // 時間
    EFI_GET_TIME         GetTime;            // 讀 RTC 時間
    EFI_SET_TIME         SetTime;

    // 變數（NVRAM）
    EFI_GET_VARIABLE     GetVariable;        // 讀 UEFI 變數（Ch 15）
    EFI_SET_VARIABLE     SetVariable;        // 寫 UEFI 變數
    EFI_GET_NEXT_VARIABLE_NAME GetNextVariableName;

    // 系統
    EFI_RESET_SYSTEM     ResetSystem;        // 重開機/關機
    EFI_GET_NEXT_HIGH_MONO_COUNT GetNextHighMonotonicCount;
    // ...
};
```

Runtime Services 數量少（韌體在 OS 接管硬體後能保留的功能有限）。最重要的是 `GetVariable`/`SetVariable`——Linux kernel 透過它讀寫 UEFI 變數（這就是為什麼 `efibootmgr` 能在 OS 裡改開機項，Ch 15）。

## Protocol 與 Handle：UEFI 的物件模型

UEFI 用 **Protocol** 和 **Handle** 組成物件導向的裝置/服務模型：

```
UEFI 物件模型：

  Handle = 一個「物件」（裝置、影像、服務的實例）
  Protocol = 一個「介面」（一組函式 + GUID 識別）
        │
  一個 Handle 可以「實作」多個 Protocol
  例：一個磁碟的 Handle 實作：
    - EFI_BLOCK_IO_PROTOCOL（區塊讀寫）
    - EFI_DISK_IO_PROTOCOL（位元組讀寫）
    - EFI_SIMPLE_FILE_SYSTEM_PROTOCOL（檔案系統）
        │
  你用 Protocol GUID 查詢：
    "給我實作 SIMPLE_FILE_SYSTEM 的 handle"
    → 拿到那個 protocol 的函式表 → 呼叫它讀檔案
```

```c
// 用 protocol 讀檔案的概念流程（Ch 13/16 會實作）
EFI_GUID fs_guid = EFI_SIMPLE_FILE_SYSTEM_PROTOCOL_GUID;
EFI_SIMPLE_FILE_SYSTEM_PROTOCOL *fs;

// 找一個實作 file system protocol 的 handle，取得它的 protocol
BootServices->LocateProtocol(&fs_guid, NULL, (void**)&fs);

// 用 protocol 開啟根目錄、開檔、讀檔...
EFI_FILE_PROTOCOL *root;
fs->OpenVolume(fs, &root);
// root->Open(...), root->Read(...)
```

> Protocol/Handle 模型是 UEFI 的核心抽象，讓它能用統一的方式存取各種裝置和服務。每個 protocol 有個 GUID（唯一識別碼），你用 GUID 查詢「誰實作了這個介面」。這比 BIOS 的「固定中斷號」靈活——UEFI 能無限擴充新 protocol（新裝置類型），不會像 BIOS 中斷號用完。這就是 UEFI「Extensible」（可擴充）的由來。

## EFI_STATUS：錯誤處理

UEFI 函式回傳 `EFI_STATUS`——一個編碼成功/失敗的值：

```c
EFI_STATUS status;
status = BootServices->AllocatePool(EfiLoaderData, size, &buffer);
if (EFI_ERROR(status)) {       // 巨集檢查是否錯誤
    // 處理錯誤
    ConOut->OutputString(ConOut, L"AllocatePool failed\n");
    return status;
}

// 常見 EFI_STATUS 值：
// EFI_SUCCESS              成功（= 0）
// EFI_OUT_OF_RESOURCES     資源不足
// EFI_NOT_FOUND            找不到
// EFI_INVALID_PARAMETER    參數錯誤
// EFI_BUFFER_TOO_SMALL     buffer 太小（常見於 GetMemoryMap，Ch 14）
```

`EFI_STATUS` 的高 bit 表示錯誤（`EFI_ERROR` 巨集檢查這個）。每次呼叫 UEFI 服務後檢查 status 是好習慣（像 BIOS 線檢查 carry flag，Ch 4）。

## 故意弄壞：ExitBootServices 後用 Boot Service

```c
// 錯誤：呼叫 ExitBootServices 後還用 Boot Service
BootServices->ExitBootServices(image_handle, map_key);
// Boot Services 從此失效！

// 之後還呼叫 Boot Service：
BootServices->AllocatePool(...);  // ← 崩潰！Boot Services 已不存在
// （函式指標可能還在，但底層的服務已拆除，行為未定義）
```

`ExitBootServices` 拆除 Boot Services（把硬體控制權交給 kernel）。之後呼叫任何 Boot Service 是未定義行為（通常崩潰）。bootloader 的正確流程：用 Boot Services 做完所有準備（載入 kernel、配記憶體、取 memory map），**最後**呼叫 ExitBootServices，然後直接跳 kernel（不再碰 Boot Services）。Ch 14 詳述這個流程。

## 踩雷集錦

1. **ExitBootServices 後用 Boot Service**：Boot Services 已拆除，呼叫 = 崩潰。所有 Boot Service 操作必須在 ExitBootServices 之前

2. **混淆 Boot 和 Runtime Services**：`GetMemoryMap` 是 Boot Service（ExitBootServices 前用）；`GetVariable` 是 Runtime Service（之後也能用）。查 spec 確認服務屬於哪類

3. **不檢查 EFI_STATUS**：UEFI 函式失敗回傳錯誤 status。不檢查就在失敗後繼續用無效資料。用 `EFI_ERROR()` 檢查

4. **protocol GUID 用錯**：每個 protocol 有特定 GUID。GUID 錯了 `LocateProtocol` 找不到。從 spec 或 gnu-efi headers 取正確 GUID

5. **以為 Runtime Services 很多**：Runtime Services 只有少數幾個（時間、變數、reset）。大部分功能（記憶體、檔案、裝置）是 Boot Services，ExitBootServices 後就沒了

## 進階：UEFI driver model 與 protocol 的擴充性

UEFI 的 protocol 模型支援動態載入驅動：

```
UEFI driver model：
  韌體可以載入 UEFI driver（.efi 格式的驅動）
        │
  driver 「安裝」protocol 到 handle 上
  例：一個網卡 driver 安裝 EFI_SIMPLE_NETWORK_PROTOCOL
        │
  → 其他 .efi 能透過那個 protocol GUID 用網路
        │
  這讓 UEFI 能支援開機前的網路（PXE）、圖形（GOP）等
  全靠 protocol 抽象，不用改韌體核心
```

這個可擴充性是 UEFI 名字裡「Extensible」的核心。對比 BIOS——BIOS 的功能是固定的中斷號，加新功能要改韌體。UEFI 用 protocol 抽象，新裝置/功能用新 protocol，不動核心。這也是為什麼 UEFI 能支援開機前的圖形介面（GOP protocol）、網路開機（network protocol）等 BIOS 做不到的事。

理解 protocol 模型，你會懂 Ch 13 的 UEFI app 怎麼透過 `ConOut`（一個 protocol）印字、Ch 16 怎麼透過 file system protocol 載入 kernel——全是「用 protocol GUID 查介面，呼叫函式」這個統一模式。

## 動手練習

1. 讀 gnu-efi 的 headers（`/usr/include/efi/efiapi.h`），找到 `EFI_BOOT_SERVICES` 和 `EFI_RUNTIME_SERVICES` 的結構定義，看它們各有哪些函式

2. 在 UEFI shell（OVMF）裡，用 `memmap` 指令看記憶體地圖（這背後是 GetMemoryMap），`dmpstore` 看 UEFI 變數（背後是 GetVariable）

3. 對照分類：列出你能想到的 bootloader 操作（配記憶體、讀 kernel 檔、取記憶體地圖、讀變數、跳 kernel），判斷每個用 Boot 還是 Runtime Service

4. 讀 UEFI spec 的 Boot Services 章節，找出 `LoadImage`/`StartImage`，理解 UEFI 怎麼「載入並執行另一個 .efi」（這是 GRUB chainload 的基礎）

## 本章重點整理

- UEFI 服務分兩類：Boot Services（開機階段，ExitBootServices 後消失）、Runtime Services（OS 接管後仍可用）
- System Table 是服務入口；`.efi` 的 entry point 收到它，透過它取用 BootServices/RuntimeServices/ConOut 等
- Boot Services：記憶體、檔案、裝置、LoadImage、ExitBootServices；Runtime Services：時間、變數、reset（少數）
- Protocol（介面+GUID）+ Handle（物件）是 UEFI 的物件模型，用 GUID 查介面——這是 UEFI 「可擴充」的核心
- ExitBootServices 後不能用 Boot Services；UEFI 函式回傳 EFI_STATUS，要檢查

## 自我檢核

- [ ] 能解釋 Boot Services 和 Runtime Services 的生命週期差異（ExitBootServices 為界）
- [ ] 知道 System Table 是什麼、`.efi` 怎麼透過它取用服務
- [ ] 能說出 Protocol 和 Handle 的關係，以及 GUID 的作用
- [ ] 知道為什麼 ExitBootServices 後不能用 Boot Service
- [ ] 能判斷一個操作（如讀記憶體地圖、讀變數）屬於哪類服務

## 延伸閱讀

### 官方文件

- **[UEFI Spec, Section 4 (EFI System Table), 6 (Boot Services), 8 (Runtime Services)](https://uefi.org/specifications)**
  - **讀哪裡**：Section 4（System Table 結構）、6.1-6.2（Boot Services 概覽）、8.1（Runtime Services）
  - **學什麼**：每個服務的精確簽名和語意；當查閱手冊
  - **前提**：本章

- **[OSDev Wiki: UEFI](https://wiki.osdev.org/UEFI)** — Boot/Runtime Services 那節
  - **讀哪裡**：services 和 protocol 那幾節
  - **學什麼**：實作角度的服務取用方式
  - **前提**：本章

### 部落格 / 文章

- **[Programming for EFI: Creating a "Hello, World" Program](https://www.rodsbooks.com/efi-programming/index.html)** — Rod Smith
  - **這篇說什麼**：UEFI 程式設計入門，System Table、protocol 的實際用法
  - **讀哪裡**：System Table 和 protocol 那幾節
  - **為什麼值得讀**：把抽象的服務結構連到實際的 C code（通往 Ch 13）

→ [Ch 13 寫一個 UEFI application](./13-write-uefi-app.md)
