# Ch 2 — NT 核心架構全貌

> 目標：理解 Windows NT 的分層架構，知道每個組件的職責，能在 WinDbg 中定位和檢視關鍵結構。

## NT 架構鳥瞰

```
┌─────────────────────────────────────────────────────────┐
│  User Mode                                              │
│  Win32 App │ .NET App │ POSIX App │ Win64 App           │
│      ↕           ↕          ↕          ↕                │
│  ntdll.dll（syscall stub，呼叫核心的橋樑）              │
├─────────────────────────────────────────────────────────┤ ← Kernel Boundary
│  Kernel Mode                                            │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Executive（執行層）                             │   │
│  │  I/O Mgr │ Obj Mgr │ Mem Mgr │ Proc Mgr │ SecRef│   │
│  │  Cache Mgr │ PnP Mgr │ Power Mgr │ Config Mgr   │   │
│  ├──────────────────────────────────────────────────┤   │
│  │  Kernel（核心層）                                │   │
│  │  排程器 │ 中斷分發 │ 同步基元 │ DPC/APC 機制    │   │
│  ├──────────────────────────────────────────────────┤   │
│  │  HAL（Hardware Abstraction Layer）               │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  Drivers: ntfs.sys │ ndis.sys │ win32k.sys │ 你的.sys   │
└─────────────────────────────────────────────────────────┘
         ↕ HW Interface
   CPU │ RAM │ 磁碟 │ 網路 │ 其他硬體
```

這不是 microkernel（Mach、L4），是**monolithic kernel with layered structure**。
所有東西跑在同一個地址空間（System Space），沒有隔離，全部信任彼此。

## Executive：執行層

Executive 是 `ntoskrnl.exe` 的主體，提供高層次的服務。

### I/O Manager（I/O 管理員）

管理所有 I/O 操作的中心。你的驅動和 I/O Manager 的關係最密切。

職責：
- 建立並管理 IRP（I/O Request Packet）
- 把用戶態的 `ReadFile()`、`WriteFile()`、`DeviceIoControl()` 轉換成 IRP 發送給驅動
- 管理 Device Object 和 Driver Object 的關係
- 處理 I/O 完成通知

```
用戶態 ReadFile()
→ ntdll!NtReadFile（syscall）
→ nt!NtReadFile（核心）
→ I/O Manager 建立 IRP_MJ_READ
→ 發給設備對應的 Driver 的 DispatchRead 函式
→ Driver 填入數據，呼叫 IoCompleteRequest
→ I/O Manager 通知用戶態完成
```

### Object Manager（物件管理員）

Windows 萬物皆物件（Process、Thread、File、Event、Mutex、Driver、Device...）。Object Manager 負責：

- 物件的建立、參考計數、銷毀
- 命名空間（`\Device\Harddisk0\DR0`、`\DosDevices\C:`）
- Handle 的分配和追蹤

```
WinDbg: !object \Device
→ 列出所有 Device 物件
```

### Memory Manager（記憶體管理員）

- 虛擬記憶體（每個進程 16TB 虛擬空間，64 位元）
- 分頁（Paging）到磁碟
- 分頁池（Paged Pool）和非分頁池（NonPaged Pool）

關鍵：**核心代碼跑在 IRQL ≥ DISPATCH_LEVEL 時，不能存取分頁記憶體**（因為 Page Fault 需要排程，而排程被停用了）。這是 IRQL 系統的核心限制，Ch 11 詳述。

### Process Manager（進程管理員）

建立、結束進程和執行緒。每個進程有一個 `EPROCESS` 結構，每個執行緒有一個 `ETHREAD` 結構（Ch 5 詳述）。

### Security Reference Monitor（安全參考監視器）

實施存取控制：Token 比對、ACL 檢查。每次 `OpenProcess()`、`OpenFile()` 都會走這裡。

## Kernel：核心層

Executive 之下是真正的「Kernel」（`nt!Ke*` 系列函式）：

- **排程器（Scheduler）**：決定哪個執行緒跑在哪個 CPU
- **中斷分發（IDT handling）**：硬體中斷 → ISR → DPC
- **同步基元（Sync Primitives）**：Spinlock、Mutex、Event 的底層實作
- **DPC/APC 機制**：延遲程序呼叫和非同步程序呼叫（Ch 14）

## HAL：硬體抽象層

HAL 把「直接存取硬體」的代碼隔離出來，讓 Executive 和 Kernel 不需要知道具體的硬體型號。

歷史上 HAL 更重要（x86/Alpha/MIPS 等不同平台）。現在 Windows 只支援 x86/x64/ARM，HAL 相對薄了，但仍然存在。

你的驅動通常**不直接用 HAL**，除非你寫硬體驅動（直接存取 I/O 端口或記憶體映射 I/O）。

## System Space vs User Space

64 位元 Windows 的地址空間分割：

```
0x0000000000000000 – 0x00007FFFFFFFFFFF  用戶空間（每個進程獨立）
                                         (128 TB)
0xFFFF800000000000 – 0xFFFFFFFFFFFFFFFF  系統空間（所有進程共享）
                                         (128 TB)
```

系統空間（Kernel Space）關鍵地址：

```
0xFFFFF80000000000  ntoskrnl.exe 基址（KASLR 隨機化）
0xFFFFFA8000000000  Process / Thread 物件區域
0xFFFFF70000000000  PML4 自引用（頁表映射）
```

KASLR（Kernel ASLR）從 Windows 8 起就有了，但很多 infoleak 可以洩漏 kernel base。

## Driver 的位置

Driver 載入後：

1. 在系統空間分配代碼/數據記憶體
2. I/O Manager 建立 `DRIVER_OBJECT` 結構
3. 呼叫 `DriverEntry()`，驅動初始化自己

```
WinDbg 查看所有載入的驅動：
kd> lm t n
  → 列出所有模組，包含你的 .sys
  
kd> !drvobj HelloDriver
  → 查看 HelloDriver 的 DRIVER_OBJECT

kd> dt nt!_DRIVER_OBJECT <address>
  → 顯示 DRIVER_OBJECT 結構欄位
```

## 在 WinDbg 中探索 NT 架構

連上 VM 後，Break 進去：

```
kd> dt nt!_DRIVER_OBJECT
   +0x000 Type             : Int2B
   +0x002 Size             : Int2B
   +0x008 DeviceObject     : Ptr64 _DEVICE_OBJECT
   +0x010 Flags            : Uint4B
   +0x018 DriverStart      : Ptr64 Void
   +0x020 DriverSize       : Uint4B
   +0x028 DriverSection    : Ptr64 Void
   +0x030 DriverExtension  : Ptr64 _DRIVER_EXTENSION
   +0x038 DriverName       : _UNICODE_STRING
   +0x048 HardwareDatabase : Ptr64 _UNICODE_STRING
   +0x050 FastIoDispatch   : Ptr64 _FAST_IO_DISPATCH
   +0x058 DriverInit       : Ptr64     long
   +0x060 DriverStartIo    : Ptr64     void
   +0x068 DriverUnload     : Ptr64     void
   +0x070 MajorFunction    : [28] Ptr64     long   ← Dispatch table（IRP handlers）
```

`MajorFunction[28]` 就是 IRP dispatch table——`IRP_MJ_READ`、`IRP_MJ_WRITE`、`IRP_MJ_DEVICE_CONTROL` 等 28 個 handler 的函式指針。

這也是攻擊者覆蓋的目標之一（DKOM 攻擊）。

## 系統呼叫的流程

用戶態的每個 API 最終都轉成系統呼叫進核心：

```
app.exe: ReadFile(hFile, buf, size, ...)
  → kernel32.dll: ReadFile()
  → ntdll.dll: NtReadFile()
      mov rax, 6      ; syscall number for NtReadFile
      syscall         ; 進入核心
  → nt!KiSystemCall64()  ← 核心入口
      → nt!NtReadFile()   ← 真正的核心函式
          → I/O Manager → IRP → 驅動
```

`syscall` 指令把 CPU 從 Ring 3 切換到 Ring 0，把控制權交給 `KiSystemCall64`。

SSDT（System Service Descriptor Table）是 syscall number 到核心函式的映射表。早期 rootkit 就是 hook SSDT 做攔截。現在 PatchGuard 保護 SSDT，直接 hook 會 BSOD。

## 自我檢核

- [ ] NT 架構的三層：HAL / Kernel / Executive，各自職責
- [ ] I/O Manager 把用戶 API 轉換成 IRP 發給驅動
- [ ] Object Manager 管理所有核心物件（包含 Device Object、Driver Object）
- [ ] 系統空間 vs 用戶空間的地址分割（64 位元）
- [ ] `DRIVER_OBJECT.MajorFunction` 是 IRP dispatch table（28 個函式指針）
- [ ] 在 WinDbg 中 `dt nt!_DRIVER_OBJECT` 能看到結構定義

→ [Ch 3 核心模式 vs 用戶模式](./03-kernel-user-mode.md)
