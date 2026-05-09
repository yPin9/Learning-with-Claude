# Ch 4 — NT 物件模型

> 目標：理解 Windows 萬物皆物件的設計，掌握 OBJECT_HEADER、Object Manager 命名空間、Handle Table 的運作，以及驅動如何建立和管理物件。

## 萬物皆物件

Windows NT 的設計原則之一：所有核心資源都是**物件（Object）**。

```
Process → EPROCESS 物件
Thread  → ETHREAD 物件
File    → FILE_OBJECT 物件
Device  → DEVICE_OBJECT 物件
Driver  → DRIVER_OBJECT 物件
Event   → KEVENT（包在 Object 裡）
Mutex   → KMUTEX
Section → 共享記憶體段（用於 DLL、mmap）
Token   → ACCESS_TOKEN（安全憑證）
```

Object Manager 統一管理這些物件的：生命週期（參考計數）、命名（路徑）、存取控制（ACL）。

## OBJECT_HEADER：物件的頭部

每個物件在記憶體中的布局：

```
低地址
┌─────────────────────────────┐
│ Optional Headers（可選）     │  ← 可能有 0–5 個
│  QuotaInfo                  │
│  HandleInfo                 │
│  NameInfo    ← 有名稱時存在  │
│  CreatorInfo                │
│  PaddingInfo                │
├─────────────────────────────┤
│ OBJECT_HEADER               │  ← 固定存在
│  PointerCount               │  ← 參考計數（ObDereferenceObject 時遞減）
│  HandleCount                │  ← 有多少 Handle 指向它
│  Lock                       │
│  TypeIndex                  │  ← 物件型別（OBJECT_TYPE 的索引）
│  InfoMask                   │  ← 哪些 Optional Headers 存在
│  Flags                      │
│  SecurityDescriptor         │  ← ACL
│  ...                        │
├─────────────────────────────┤
│ 物件本體（Body）             │  ← 你用指針指的地址
│  EPROCESS / FILE_OBJECT /   │
│  DEVICE_OBJECT / ...        │
└─────────────────────────────┘
高地址
```

你在代碼裡看到的 `PDEVICE_OBJECT`、`PFILE_OBJECT` 都是指向 Body 的指針。Object Manager 內部透過 `OBJECT_TO_OBJECT_HEADER(ptr)` 宏找到頭部。

```c
// 宏定義（ntoskrnl 內部）
#define OBJECT_TO_OBJECT_HEADER(o) \
    CONTAINING_RECORD((o), OBJECT_HEADER, Body)
```

### 在 WinDbg 查看 OBJECT_HEADER

```
kd> dt nt!_OBJECT_HEADER
   +0x000 PointerCount     : Int8B
   +0x008 HandleCount      : Int8B
   +0x008 NextToFree       : Ptr64 Void
   +0x010 Lock             : _EX_PUSH_LOCK
   +0x018 TypeIndex        : UChar
   +0x019 TraceFlags       : UChar
   +0x01a InfoMask         : UChar
   +0x01b Flags            : UChar
   ...
   +0x028 SecurityDescriptor : Ptr64 Void
   +0x030 Body             : _QUAD
```

查看某個 Device Object 的 Header：

```
kd> !object \Device\Null
Object: ffffe00012345678  Type: (ffffe000`deadbeef) Device
    ObjectHeader: ffffe00012345648   ← Header 在 Body 前 0x30 個 bytes
    HandleCount: 0  PointerCount: 3
    Directory Object: ffffe000`aabbccdd  Name: Null
```

## OBJECT_TYPE：物件型別

每種物件有一個 `OBJECT_TYPE` 結構，定義：
- `TypeName`（如 `"Process"`、`"File"`、`"Device"`）
- `AllocateProcedure` / `FreeProcedure`（建立/銷毀時呼叫）
- `OpenProcedure` / `CloseProcedure`
- `ParseProcedure`（路徑解析，讓 File Object 能做路徑導覽）
- `SecurityProcedure`（存取控制）
- `DefaultNonPagedPoolCharge`（預設記憶體計費）

```
kd> dt nt!_OBJECT_TYPE ffffe000`deadbeef
   +0x000 TypeList         : _LIST_ENTRY
   +0x010 Name             : _UNICODE_STRING "Device"
   +0x020 DefaultObject    : Ptr64 Void
   +0x028 Index            : UChar
   ...
   +0x040 TypeInfo         : _OBJECT_TYPE_INITIALIZER
```

## Object Manager 命名空間

Windows 有一個類似 Unix 的核心命名空間，根目錄是 `\`（反斜線）：

```
\                                ← 根目錄
├── Device                       ← 設備物件
│   ├── HarddiskVolume1
│   ├── Null                     ← NUL 設備（/dev/null）
│   ├── ConDrv                   ← Console
│   └── ...
├── DosDevices                   ← 符號連結到 Win32 設備名稱
│   ├── C: → \Device\HarddiskVolume3
│   ├── NUL → \Device\Null
│   └── ...
├── KnownDlls                    ← 預載入的 DLL
├── ObjectTypes                  ← 所有物件型別
└── Sessions\...                 ← 每個登入 Session
```

驅動建立 Device Object 後，通常用 `IoCreateSymbolicLink()` 建立 `\DosDevices\MyDriver` → `\Device\MyDriver` 的符號連結，讓用戶態能用 `\\.\MyDriver` 開啟它。

```c
UNICODE_STRING deviceName = RTL_CONSTANT_STRING(L"\\Device\\MyDriver");
UNICODE_STRING symLinkName = RTL_CONSTANT_STRING(L"\\DosDevices\\MyDriver");

// 建立設備物件
IoCreateDevice(DriverObject, 0, &deviceName, 
               FILE_DEVICE_UNKNOWN, 0, FALSE, &gDeviceObject);

// 建立符號連結
IoCreateSymbolicLink(&symLinkName, &deviceName);
```

用戶態存取方式：
```c
// CreateFile 中的路徑
HANDLE h = CreateFile(L"\\\\.\\MyDriver", ...);
// \\.\ 對應核心的 \DosDevices\（Win32 子系統的對應）
```

探索命名空間的工具：**WinObj**（Sysinternals）。

## Handle Table：用戶態如何存取物件

用戶態程式拿到的不是物件指針（那是核心地址），而是 **Handle**——一個整數索引。

```
Handle 4  → HANDLE_TABLE_ENTRY → 指向 EPROCESS 物件
Handle 8  → HANDLE_TABLE_ENTRY → 指向 FILE_OBJECT
Handle 12 → ...
```

Handle 是進程私有的。同一個物件，不同進程有不同的 Handle 值。

### 在 WinDbg 查看 Handle Table

```
kd> !process 0 0 notepad.exe
PROCESS ffffe00012345678
    ...
    
kd> !handle 0 3 ffffe00012345678
  ...
  0004: Object: ffffe00099887766  GrantedAccess: 001fffff
       ObjectHeader: ffffe00099887736
       HandleCount: 2  PointerCount: 5
       Directory Object: 00000000  Name: \Sessions\1\...
```

### 核心內的物件參考

核心代碼之間傳遞物件用**指針 + 參考計數**，不用 Handle：

```c
// 從 Handle 取得物件指針
PEPROCESS process;
ObReferenceObjectByHandle(hProcess, PROCESS_ALL_ACCESS, 
                           *PsProcessType, KernelMode, 
                           (PVOID*)&process, NULL);

// 用完後必須釋放參考
ObDereferenceObject(process);
```

`ObReferenceObject`（AddRef）和 `ObDereferenceObject`（Release）的配對是核心代碼最常見的內存管理模式。忘記 `ObDereferenceObject` → 物件永遠不會釋放 → 核心記憶體洩漏。

## 安全描述符（Security Descriptor）

每個有名稱的物件都有一個 Security Descriptor（存在 OBJECT_HEADER.SecurityDescriptor）：

```
Security Descriptor：
  Owner SID   → 誰擁有這個物件
  Group SID
  DACL        → Discretionary ACL：誰有什麼存取權限
  SACL        → System ACL：審計記錄
```

Security Reference Monitor 在每次 `OpenProcess()`、`OpenFile()` 時對 DACL 做 Access Check。

驅動也可以為自己的 Device Object 設定 Security Descriptor，控制哪些用戶能開啟它：

```c
// 只允許 SYSTEM 和 Admin 存取
UNICODE_STRING sddlString = RTL_CONSTANT_STRING(
    L"D:P(A;;GA;;;SY)(A;;GA;;;BA)");
WdmlibIoCreateDeviceSecure(..., &sddlString, ...);
```

## 動手：用 WinObj 探索命名空間

1. 下載 Sysinternals WinObj，以管理員執行
2. 瀏覽 `\Device` 看所有設備物件
3. 瀏覽 `\DosDevices`（或 `\??\`）看符號連結映射
4. 在 `\ObjectTypes` 可以看到所有物件型別

這讓你直觀理解驅動建立的 Device 去了哪裡。

## 自我檢核

- [ ] OBJECT_HEADER 在 Body 之前，包含參考計數、型別索引、安全描述符
- [ ] `OBJECT_TO_OBJECT_HEADER()` 從 Body 指針找 Header
- [ ] Object Manager 命名空間的根是 `\`，Device 物件在 `\Device\`
- [ ] `IoCreateSymbolicLink` 讓用戶態用 `\\\\.\\Name` 存取驅動
- [ ] Handle = 整數索引，進程私有；核心用指針 + 參考計數
- [ ] `ObReferenceObjectByHandle` 和 `ObDereferenceObject` 必須配對

→ [Ch 5 EPROCESS / ETHREAD](./05-eprocess-ethread.md)
