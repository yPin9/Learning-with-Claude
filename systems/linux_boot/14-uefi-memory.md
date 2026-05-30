# Ch 14 — UEFI 的記憶體與 ExitBootServices

> **目標**：理解 UEFI 的記憶體管理——記憶體類型、`AllocatePool`/`AllocatePages`、`GetMemoryMap` 的用法與 map key 機制，以及 `ExitBootServices` 的關鍵儀式（取 memory map → 退出 boot services → 交棒 kernel），這是 UEFI bootloader 交棒給 kernel 的核心。

> **環境**：gnu-efi，OVMF，QEMU。承接 Ch 12（Boot/Runtime Services）、Ch 13（UEFI app）。原理深挖章。

## 為什麼 ExitBootServices 這麼關鍵又這麼容易出錯？

UEFI bootloader 的最後一步是把控制權交給 kernel——這透過 `ExitBootServices` 完成。但這個呼叫有個惡名昭彰的陷阱：它需要一個正確的 **map key**（記憶體地圖的版本號），而取得 map key 和呼叫 ExitBootServices 之間如果記憶體地圖變了，呼叫就失敗。

這個「取 memory map → ExitBootServices」的舞蹈是 UEFI 開機最容易卡住的地方。理解它，你才能寫出能正確交棒給 kernel 的 bootloader（Ch 16 會用到）。

## 先建立直覺：交棒前要「凍結」記憶體狀態

```
ExitBootServices 的本質：交接記憶體控制權

  開機階段：韌體管理記憶體（你用 AllocatePool 配置）
        │
  交棒給 kernel：kernel 要接管記憶體
        │  但 kernel 需要知道「現在記憶體長什麼樣」
        │  （哪些被韌體用、哪些可用、哪些是 MMIO）
        ▼
  所以交棒儀式是：
    1. 取得當前 memory map（含一個 map key = 版本號）
    2. 呼叫 ExitBootServices(image, map_key)
       韌體檢查 map_key 是否還是最新的
       是 → 退出 boot services，交棒成功
       否 → 失敗（記憶體地圖在你取得後又變了）
        │
  關鍵：取 map 和 ExitBootServices 之間不能讓記憶體地圖變
```

map key 像個版本戳。你拿到 map（和它的 key），然後說「我要用這個 map 交棒」。如果這之間記憶體地圖變了（key 過期），韌體拒絕——因為你手上的 map 已經不準了。

## UEFI 記憶體類型

UEFI 把記憶體分成多種類型，告訴 kernel 每塊記憶體的用途：

```c
typedef enum {
    EfiReservedMemoryType,       // 保留，別用
    EfiLoaderCode,               // bootloader 的 code
    EfiLoaderData,               // bootloader 的 data
    EfiBootServicesCode,         // Boot Services 的 code（ExitBootServices 後可回收）
    EfiBootServicesData,         // Boot Services 的 data（同上）
    EfiRuntimeServicesCode,      // Runtime Services 的 code（OS 後仍保留）
    EfiRuntimeServicesData,      // Runtime Services 的 data（保留）
    EfiConventionalMemory,       // 一般可用 RAM ← kernel 主要用這個
    EfiUnusableMemory,           // 壞記憶體
    EfiACPIReclaimMemory,        // ACPI 表（用完可回收）
    EfiACPIMemoryNVS,            // ACPI NVS（保留）
    EfiMemoryMappedIO,           // MMIO（裝置暫存器，不是 RAM）
    EfiMemoryMappedIOPortSpace,
    EfiPalCode,
    EfiPersistentMemory,
    EfiMaxMemoryType
} EFI_MEMORY_TYPE;
```

關鍵類型：
- `EfiConventionalMemory`：一般可用 RAM（kernel 接管後主要用這個）
- `EfiBootServicesCode/Data`：ExitBootServices 後可回收（boot services 不再需要）
- `EfiRuntimeServicesCode/Data`:kernel 必須保留（runtime services 還要用）
- `EfiMemoryMappedIO`：MMIO（裝置，不是 RAM，碰了會操作裝置）

這比 BIOS 的 e820（Ch 3）細緻——UEFI 的記憶體類型更多、更精確。kernel 用這個 map 建立它的記憶體管理。

## 配置記憶體：AllocatePool / AllocatePages

UEFI bootloader 用 Boot Services 配置記憶體：

```c
// AllocatePool：像 malloc，配任意大小（從 pool）
void *buffer;
EFI_STATUS status = SystemTable->BootServices->AllocatePool(
    EfiLoaderData,      // 記憶體類型
    4096,               // 大小（bytes）
    &buffer);           // 回傳指標
if (EFI_ERROR(status)) { /* 處理錯誤 */ }
// 用 buffer ...
SystemTable->BootServices->FreePool(buffer);   // 釋放

// AllocatePages：配整頁（4KB 對齊），給需要頁對齊的東西（如 kernel 載入）
EFI_PHYSICAL_ADDRESS addr;
status = SystemTable->BootServices->AllocatePages(
    AllocateAnyPages,   // 配置策略
    EfiLoaderData,
    16,                 // 頁數（16 頁 = 64KB）
    &addr);
```

`AllocatePool` 像 malloc（任意大小），`AllocatePages` 配整頁（4KB 對齊，給 kernel 載入這種需要對齊的）。

## GetMemoryMap：取得記憶體地圖

`GetMemoryMap` 是交棒前必做的——取得當前記憶體地圖和 map key：

```c
EFI_MEMORY_DESCRIPTOR *map = NULL;
UINTN map_size = 0, map_key, desc_size;
UINT32 desc_version;

// 第一次呼叫：buffer = NULL，韌體回傳需要的大小（status = BUFFER_TOO_SMALL）
SystemTable->BootServices->GetMemoryMap(
    &map_size, map, &map_key, &desc_size, &desc_version);
// 此時 map_size 被設成需要的大小

// 配置足夠的 buffer（多配一點，因為配置動作本身可能改變 map）
map_size += 2 * desc_size;
SystemTable->BootServices->AllocatePool(EfiLoaderData, map_size, (void**)&map);

// 第二次呼叫：真正取得 map
EFI_STATUS status = SystemTable->BootServices->GetMemoryMap(
    &map_size, map, &map_key, &desc_size, &desc_version);
//                      ↑ map_key 是這個 map 的「版本」

// 現在 map 是記憶體描述符陣列，遍歷它：
EFI_MEMORY_DESCRIPTOR *desc = map;
for (UINTN i = 0; i < map_size / desc_size; i++) {
    // desc->Type, desc->PhysicalStart, desc->NumberOfPages
    desc = (EFI_MEMORY_DESCRIPTOR*)((char*)desc + desc_size);
    //     ↑ 用 desc_size 跨步，不要用 sizeof（韌體可能用更大的 descriptor）
}
```

幾個微妙點：
- **兩次呼叫**：第一次用 NULL buffer 問大小（回傳 BUFFER_TOO_SMALL），配好 buffer 後第二次真正取
- **多配一點**：配 buffer 的動作本身可能改變 memory map（多一個 allocation），所以多配 `2 * desc_size`
- **用 `desc_size` 跨步**：遍歷 descriptor 陣列要用韌體回傳的 `desc_size`，**不要**用 `sizeof(EFI_MEMORY_DESCRIPTOR)`——韌體可能用更大的 descriptor（為未來擴充留空間）

> **「用 desc_size 不用 sizeof」是個經典陷阱**。memory descriptor 的實際大小由韌體決定（`desc_size`），可能比你 header 裡的 struct 大。用 `sizeof` 跨步會錯位，讀到垃圾。永遠用韌體回傳的 `desc_size`。

## ExitBootServices：交棒儀式

取得 memory map（含 map_key）後，呼叫 ExitBootServices 交棒：

```c
EFI_STATUS status = SystemTable->BootServices->ExitBootServices(
    ImageHandle, map_key);
//             ↑ 剛取得的 map_key

if (status == EFI_INVALID_PARAMETER) {
    // map_key 過期了！（取 map 和 ExitBootServices 之間記憶體地圖變了）
    // 必須重新 GetMemoryMap 取得新 key，再試一次
    // ... 重新取 map ...
    status = SystemTable->BootServices->ExitBootServices(ImageHandle, map_key);
}

// ExitBootServices 成功後：
//  - Boot Services 全部失效（不能再用 AllocatePool 等！）
//  - 韌體不再管理硬體，kernel 接管
//  - 只剩 Runtime Services 可用
//  - 接下來直接跳 kernel（不能再呼叫任何 Boot Service）
```

ExitBootServices 的正確流程是個迴圈：

```c
// 標準的 ExitBootServices 迴圈（處理 map_key 過期）
EFI_STATUS exit_boot_services(EFI_HANDLE image, EFI_SYSTEM_TABLE *st) {
    EFI_MEMORY_DESCRIPTOR *map;
    UINTN map_size, map_key, desc_size;
    UINT32 desc_ver;
    EFI_STATUS status;

    do {
        // 取得最新的 memory map
        map_size = 0; map = NULL;
        st->BootServices->GetMemoryMap(&map_size, map, &map_key, &desc_size, &desc_ver);
        map_size += 2 * desc_size;
        st->BootServices->AllocatePool(EfiLoaderData, map_size, (void**)&map);
        st->BootServices->GetMemoryMap(&map_size, map, &map_key, &desc_size, &desc_ver);

        // 立刻嘗試 ExitBootServices（用剛取得的 key）
        status = st->BootServices->ExitBootServices(image, map_key);
        // 如果 key 過期（EFI_INVALID_PARAMETER），迴圈重試
        if (EFI_ERROR(status)) {
            st->BootServices->FreePool(map);  // 釋放舊 map，重來
        }
    } while (EFI_ERROR(status));

    // 成功！記得把 map 傳給 kernel（kernel 需要記憶體地圖）
    return EFI_SUCCESS;
}
```

> 為什麼要迴圈重試？因為 `GetMemoryMap` 和 `ExitBootServices` 之間，任何事件（如韌體的背景活動）都可能改變記憶體地圖，讓 map_key 過期。所以正確做法是：取 map → 立刻 ExitBootServices → 失敗就重來。兩個動作之間**不要做別的**（尤其不要 print、不要 allocate），減少地圖變動的機會。

## 故意弄壞：ExitBootServices 和 GetMemoryMap 之間做事

```c
// 錯誤：取 map 後、ExitBootServices 前做了會改變記憶體的事
GetMemoryMap(..., &map_key, ...);
Print(L"About to exit boot services\n");  // ← print 可能改變記憶體地圖！
AllocatePool(...);                          // ← allocate 一定改變記憶體地圖！
ExitBootServices(image, map_key);           // ← map_key 過期，EFI_INVALID_PARAMETER
```

`GetMemoryMap` 和 `ExitBootServices` 之間做任何可能改變記憶體的事（print、allocate），會讓 map_key 過期，ExitBootServices 失敗。正確做法：取 map 後立刻 ExitBootServices，中間什麼都不做。所有準備工作（載入 kernel、配記憶體、印訊息）要在取 map **之前**做完。

## 踩雷集錦

1. **GetMemoryMap 和 ExitBootServices 之間做事**：任何 allocate/print 改變記憶體地圖，map_key 過期。中間什麼都別做，取 map 後立刻 exit

2. **不處理 map_key 過期**：ExitBootServices 第一次可能因 key 過期失敗。要迴圈重試（重新取 map）

3. **用 sizeof 而非 desc_size 跨步**：memory descriptor 實際大小是韌體回傳的 desc_size，可能比 struct 大。用 sizeof 跨步會錯位

4. **ExitBootServices 後用 Boot Service**：成功後 Boot Services 全失效。之後只能用 Runtime Services 和跳 kernel。再 AllocatePool 等 = 崩潰

5. **忘記把 memory map 傳給 kernel**：kernel 接管後需要記憶體地圖建立記憶體管理。ExitBootServices 用的 map 要傳給 kernel（Linux boot protocol 有對應欄位，Ch 20）

6. **GetMemoryMap 第一次沒處理 BUFFER_TOO_SMALL**：第一次呼叫故意用 NULL/0 問大小，回傳 BUFFER_TOO_SMALL 是正常的，據此配 buffer 再第二次取

## 進階：為什麼這個設計這麼麻煩

ExitBootServices 的 map_key 機制看起來很麻煩，但它解決一個真實問題：

```
問題：交棒給 kernel 時，記憶體狀態必須一致
  - kernel 接管記憶體要知道精確的當前狀態
  - 但韌體在背景可能還在改記憶體（事件、driver 活動）
  - 如果 kernel 用了「過期的」記憶體地圖，會把韌體還在用的記憶體當可用 → 損壞

解法：map_key 當「一致性 token」
  - 取 map 時記下 key（當前版本）
  - ExitBootServices 時韌體檢查 key 還是不是最新
  - 是 → 保證 kernel 拿到的 map 和當前狀態一致，安全交棒
  - 否 → 拒絕，逼你取最新的 map
        │
  → map_key 是「樂觀並發控制」：假設沒變，變了就重試
```

這是個經典的並發控制設計（類似資料庫的 optimistic locking）。麻煩但必要——它保證 kernel 接管時記憶體狀態一致，不會踩到韌體還在用的記憶體。理解這個，你會欣賞這個設計的嚴謹（雖然寫起來煩）。

## 動手練習

1. 寫一個 UEFI app，用 GetMemoryMap 取得記憶體地圖並印出所有 descriptor（type、起始位址、頁數）。對照 BIOS 的 e820（Ch 3），看 UEFI 的記憶體類型更細

2. 統計記憶體：遍歷 memory map，計算 `EfiConventionalMemory`（可用 RAM）總量，印出來。對比 QEMU 給的記憶體大小

3. 實作正確的 ExitBootServices 迴圈（取 map → exit → 失敗重試），確認能成功退出（之後 app 會「卡住」因為沒跳 kernel，這正常——Ch 16 會接著跳 kernel）

4. 故意弄壞：在 GetMemoryMap 和 ExitBootServices 之間加一個 Print，看 ExitBootServices 回傳 EFI_INVALID_PARAMETER（map_key 過期）

## 本章重點整理

- UEFI 記憶體分多種類型（ConventionalMemory 可用、BootServices* 可回收、RuntimeServices* 保留、MemoryMappedIO 是裝置）
- `AllocatePool`（任意大小，像 malloc）/ `AllocatePages`（整頁，給 kernel 載入）配置記憶體
- `GetMemoryMap` 取得記憶體地圖 + map_key（版本號）；遍歷用 desc_size 跨步，不用 sizeof
- `ExitBootServices(image, map_key)` 交棒：map_key 過期會失敗，要迴圈重試（取 map → 立刻 exit）
- 取 map 和 ExitBootServices 之間不能做改變記憶體的事（print/allocate）；成功後 Boot Services 全失效

## 自我檢核

- [ ] 能解釋 map_key 的作用（記憶體地圖的一致性 token）
- [ ] 知道為什麼 GetMemoryMap 和 ExitBootServices 之間不能做事
- [ ] 知道為什麼遍歷 memory descriptor 要用 desc_size 不用 sizeof
- [ ] 能寫出正確的 ExitBootServices 迴圈（處理 key 過期）
- [ ] 知道 ExitBootServices 成功後什麼能用、什麼不能用（只剩 Runtime Services）

## 延伸閱讀

### 官方文件

- **[UEFI Spec, Section 7.2 (Memory Allocation Services), 7.4 (Image Services - ExitBootServices)](https://uefi.org/specifications)**
  - **讀哪裡**：7.2（AllocatePool/Pages/GetMemoryMap）、ExitBootServices 那節
  - **學什麼**：記憶體服務和 ExitBootServices 的精確語意、map_key 機制
  - **前提**：本章

### 部落格 / 文章

- **[The ExitBootServices dance](https://wiki.osdev.org/UEFI#My_OS_is_loaded.2C_but_my_OS_loader_keeps_crashing_when_I_call_ExitBootServices)** — OSDev Wiki
  - **這篇說什麼**：ExitBootServices 的常見陷阱和正確流程
  - **讀哪裡**：ExitBootServices 那節
  - **為什麼值得讀**：直接針對「ExitBootServices 崩潰」這個經典問題，給正確做法

- **[Writing a UEFI bootloader](https://krinkinmu.github.io/2020/10/11/efi-getting-started.html)** — Mike Krinkin
  - **這篇說什麼**：從零寫 UEFI bootloader，含 memory map 和 ExitBootServices 的詳細處理
  - **讀哪裡**：memory map 和 exit boot services 那幾節
  - **為什麼值得讀**：完整可跑的 code，把本章的概念落地

→ [Ch 15 UEFI 變數與開機項管理](./15-uefi-variables.md)
