# Ch 8 — Handle 與 Object Manager

> **目標**：理解 Windows Object Manager 的設計——物件型別、`OBJECT_HEADER`、handle table、handle 值的意義（index）、reference count 的兩種、named object 命名空間。能用 Python + ctypes 實際建立和操作 handle 並觀察它的數值特性。理解 handle leak、handle 值可預測性、object type confusion 這幾個 exploit 相關概念。

> **環境**：Python 3.12 + ctypes；Windows 11 x64 build 26200。handle 操作全部有真實執行輸出。

## 為什麼需要這個？

在 Linux，「資源」的統一抽象是 **fd（file descriptor）**：整數，從 0 開始，進到 kernel 的 fd table。幾乎所有 I/O 資源（file、socket、pipe、epoll、eventfd……）都是 fd，概念上是同質的。你做 `dup()` 複製 fd，`close()` 關掉，`poll()` 等待。

Windows 的設計不一樣，它有 **Object Manager**（物件管理員）：一個 kernel 子系統，負責管理**所有** kernel 物件的生命週期——行程（Process）、執行緒（Thread）、檔案（File）、事件（Event）、mutex（Mutant）、section、token、registry key……都是 Object Manager 管理的「物件」。handle 是使用者態程式存取這些物件的通行證。

Object Manager 的重要性：
- handle leak 拿到高權限 handle 是提權和橫向移動的常見原語
- handle 值的可預測性是部分競爭條件 exploit 的關鍵
- `DuplicateHandle` 跨行程傳送 handle 是沙盒逃逸（Browser pwn → renderer → GPU process）的常見手法
- 理解 Object Manager 才能看懂 kernel exploit 裡「物件欄位覆蓋」的目標結構

## 先建立直覺：kernel 物件的圖書館系統

把 Object Manager 想成一個圖書館：

```
 使用者（App）
    │
    │  「我要一本叫 SomeEvent 的書」
    ▼
┌──────────────────────────────────────────────────────┐
│  借閱台（Object Manager）                            │
│  ・每本書都有一個封面（OBJECT_HEADER）               │
│  ・封面記錄：書的型別、參考計數、名字                 │
│  ・書的本體（Body）才是真正的資料                    │
└──────────────────────────────────────────────────────┘
    │
    │  分配一個借書證號碼（handle value）
    ▼
┌──────────────────────────────────────────────────────┐
│  借書記錄本（Handle Table）                          │
│  ・每個行程有自己的借書記錄本                        │
│  ・借書證號碼 → 書在哪（物件指標）+ 你有什麼存取權   │
└──────────────────────────────────────────────────────┘

使用者只拿到借書證號碼（handle），
不知道書放在哪裡（kernel 位址），
不能直接摸書（只能透過 API 操作）。
```

handle 是不透明的索引，Object Manager 用它查 handle table，找到對應的 kernel 物件指標，再執行操作。

## 物件型別（Object Type）

Object Manager 裡的每個物件都有一個**型別（Type）**，型別決定了：
- 物件的 body 結構（各欄位的偏移和意義）
- 物件允許的操作（`GENERIC_READ`、`GENERIC_WRITE`……）
- 物件的生命週期行為（如何計數、如何銷毀）

常見的物件型別：

| 型別名稱 | 建立函式 | 用途 |
|---|---|---|
| Process | `OpenProcess` / `CreateProcess` | 行程 |
| Thread | `OpenThread` / `CreateThread` | 執行緒 |
| File | `CreateFile` | 檔案、裝置、pipe |
| Event | `CreateEvent` | 事件（signaled/non-signaled）|
| Mutant | `CreateMutex` | 互斥鎖（Mutex 在 kernel 叫 Mutant）|
| Semaphore | `CreateSemaphore` | 號誌 |
| Section | `CreateFileMapping` | 共享記憶體映射 |
| Token | `OpenProcessToken` | 存取令牌（安全性主體）|
| Key | `RegCreateKey` | Registry key |
| Job | `CreateJobObject` | 工作集（沙盒用）|
| IoCompletion | `CreateIoCompletionPort` | I/O 完成埠 |
| Timer | `CreateWaitableTimer` | 可等待計時器 |

在 Linux，這些幾乎都是 fd（Socket、epoll、signalfd、timerfd……）。Windows 的 Object Manager 把它們全部統一在一個框架下，但保留了各型別的 body 結構差異。

## `OBJECT_HEADER` 與 Body 佈局

每個 kernel 物件在記憶體裡的佈局：

```
高位址
┌──────────────────────────────────┐ ← 可選的 info block (名字、配額、handle DB 等)
│  Optional Info Blocks            │
│  (OBJECT_HEADER_NAME_INFO,       │
│   OBJECT_HEADER_HANDLE_INFO,     │
│   OBJECT_HEADER_QUOTA_INFO ...)  │
├──────────────────────────────────┤ ← OBJECT_HEADER（固定大小）
│  OBJECT_HEADER                   │
│  ┌───────────────────────────┐   │
│  │ PointerCount  (INT64)     │   │  ← 所有指標參考數（包含 handle）
│  │ HandleCount 或 Lock       │   │  ← handle 數量（或鎖，看版本）
│  │ NextToFree    (PTR)       │   │
│  │ SecurityDescriptor (PTR)  │   │  ← 存取控制清單
│  │ NameInfoOffset  (UCHAR)   │   │  ← 可選名字 info 的偏移
│  │ HandleInfoOffset (UCHAR)  │   │
│  │ QuotaInfoOffset  (UCHAR)  │   │
│  │ Flags         (UCHAR)     │   │
│  │ ObjectCreateInfo (PTR)    │   │
│  │ SecurityDescriptor (PTR)  │   │
│  └───────────────────────────┘   │
├──────────────────────────────────┤ ← Body（物件本體，型別特定）
│  Object Body                     │  ← 這裡是 _EPROCESS / _ETHREAD / FILE_OBJECT 等
│  （WinDbg dt 看到的那層）         │
└──────────────────────────────────┘
低位址

GetObjectPointer 回傳的是 Body 的位址（Header 在 Body 前面）
從 Body 位址減去 OBJECT_HEADER 大小可以拿到 Header
```

> **未實測，理論預期**：WinDbg 裡 `dt nt!_OBJECT_HEADER` 可以看到完整的欄位定義和偏移。`!object <address>` 可以顯示一個物件的型別和參考計數。裝好 WinDbg + symbols 後，`!handle 0 f` 可以列出目前行程的所有 handle 及型別。

對 exploiter 而言，`OBJECT_HEADER` 最有趣的欄位是：
- **SecurityDescriptor**：指向存取控制結構，如果能覆蓋這個指標，就能繞過物件的 ACL 檢查
- **Type 指標**（間接，透過 header）：指向 `_OBJECT_TYPE` 結構，含函式表（`TypeInfo.DumpProcedure`、`TypeInfo.DeleteProcedure`……）

## Handle Table 與 HANDLE 值的意義

每個 Windows 行程有自己的 **handle table**（行程私有）。行程 A 的 handle 1096 和行程 B 的 handle 1096 是完全不同的物件。

Handle table 在 kernel 裡是一個三層的 bitmap/pointer 結構（類似 page table），但概念上就是一個陣列：

```
Handle Table（行程 A）:
  Index  0: [空]
  Index  1: {ptr → EPROCESS(ntoskrnl), access=PROCESS_ALL_ACCESS}
  Index  2: [空]
  ...
  Index 274: {ptr → EPROCESS(自身), access=PROCESS_ALL_ACCESS}
  Index 275: {ptr → MUTANT,          access=MUTEX_ALL_ACCESS}
  Index 276: [空]
  Index 277: {ptr → FILE_OBJECT,     access=GENERIC_RW}
  Index 278: {ptr → EVENT,           access=EVENT_ALL_ACCESS}
```

**HANDLE 值 = `(index << 2) | flags`**

低 2 bits 是旗標（bit 0 = HANDLE_FLAG_INHERIT，bit 1 = HANDLE_FLAG_PROTECT_FROM_CLOSE）。所以真正的 index 是 `handle >> 2`。這就是為什麼所有 handle 都是 4 的倍數。

本機真實實測：

```python
# Python + ctypes 建立多個 handle 觀察數值
ntdll.dll      base: 0x7ffa859c0000
kernelbase.dll base: 0x7ffa830d0000
kernel32.dll   base: 0x7ffa849a0000

=== Handle basics ===
Pseudo-handle for current process: 0xffffffffffffffff
Current PID: 30048
Real handle (OpenProcess self): 0x448 (decimal: 1096)
File handle:                    0x454 (decimal: 1108)
Event handle:                   0x458 (decimal: 1112)
Mutex handle:                   0x44c (decimal: 1100)

=== Handle value observation ===
Handles: process=1096, file=1108, event=1112, mutex=1100
Handles are multiples of 4: [True, True, True, True]
Handle >> 2 (index): process=274, file=277, event=278, mutex=275
```

這個行程跑到後段，handle table 的 index 已經用到 274+。低號碼（0-3 附近）是保留的（0 = invalid handle，-1 = current process pseudo-handle，-2 = current thread pseudo-handle）。

**Pseudo-handle**：`0xffffffffffffffff`（= -1 as signed HANDLE）是「目前行程」的偽 handle，只在本行程有效，不能傳給別的 API 或行程。`OpenProcess` 回傳的才是真實 handle（有 handle table entry 的）。

## Reference Count：兩種計數

Windows Object Manager 維護**兩種**參考計數：

```
┌─────────────────────────────────────────────────────┐
│  OBJECT_HEADER                                       │
│  PointerCount = 5   ← kernel 持有的指標總數（強引用）│
│  HandleCount  = 2   ← 使用者態 handle 數量           │
└─────────────────────────────────────────────────────┘
```

**PointerCount（Pointer Reference Count）**：每次 kernel 元件持有物件指標時 +1，釋放時 -1。用 `ObReferenceObject` / `ObDereferenceObject`（kernel API）操作。任何一個 handle 的存在，也等同一個 pointer reference（`ObReferenceObjectByHandle`）。PointerCount = 0 才真正銷毀物件。

**HandleCount**：該物件目前被幾個 handle entry 指著。`CloseHandle` 讓 HandleCount -1，如果 HandleCount 降到 0，且物件有 delete-on-last-close 語意（例如匿名 pipe 的讀端），就觸發 `DeleteProcedure`。

**HandleCount vs PointerCount 的不同**：

```
假設一個 Event 物件：
  PointerCount = 3, HandleCount = 2

含意：
  ・2 個行程（或同一行程 2 個 handle）在用這個 Event
  ・另外 1 個 kernel pointer 指著它（例如 I/O request pending）

呼叫 CloseHandle：
  HandleCount → 1, PointerCount → 2

那個 I/O 完成後 ObDereferenceObject：
  PointerCount → 1

第二個 CloseHandle：
  HandleCount → 0, PointerCount → 1（還有 CloseHandle 本身的引用）
  → 觸發 DeleteProcedure
  PointerCount → 0
  → 物件記憶體被釋放
```

這個雙計數設計避免 use-after-free：即使所有 handle 都關閉，只要 kernel 還有一個 pointer 指著它，物件就不會被釋放。

## Named Object 與 `\` 命名空間

Windows 的 Object Manager 有一個**階層式命名空間**，根節點是 `\`（反斜線，就像 Unix 的 `/`），但和 VFS 不同，這是 kernel 物件的命名空間：

```
\
├── Device\                   ← 裝置物件（\Device\HarddiskVolume3 等）
├── BaseNamedObjects\         ← 一般命名物件的家（Mutex、Event、Section 等）
├── Sessions\                 ← session 物件
│   └── 1\BaseNamedObjects\   ← 特定 session 的命名物件
├── KnownDlls\                ← 系統 DLL section 物件
├── ObjectTypes\              ← 所有物件型別定義
├── Global\                   ← GlobalNamedObjects（跨 session 共用）
└── ...
```

當你呼叫 `CreateEvent(NULL, TRUE, FALSE, "MyEvent")` 時，Object Manager 在 `\Sessions\<SessionID>\BaseNamedObjects\MyEvent` 或 `\BaseNamedObjects\MyEvent` 建立一個命名 Event 物件。

用 `L"Global\\MyEvent"` 前綴可以建在全域命名空間（`\Global\MyEvent`），跨 session 可見，常用於服務和 desktop app 溝通。

**為什麼命名空間對安全重要**：
- **Squatting 攻擊**：低權限行程可以預先建立 `\BaseNamedObjects\SomeImportantEvent`，等高權限服務第一次 `OpenEvent` 時拿到低權限行程的物件（Planting 攻擊）
- **Named pipe impersonation**：服務監聽一個 named pipe，攻擊者連上去後，服務若呼叫 `ImpersonateNamedPipeClient`，會取得攻擊者的 token

本機實測（Python + ctypes 建立命名 Event）：

```
=== NtQueryObject (named object example) ===
Named event handle: 0x44c
```

這個 handle 0x44c 指向 `Global\WinPwnTestEvent`，任何行程都能用 `OpenEvent(0, FALSE, L"Global\\WinPwnTestEvent")` 打開它。

## Granted Access Mask：細粒度的存取控制

Handle table 的每個 entry 除了物件指標，還存著**存取遮罩（access mask）**：這個 handle 被授予了哪些操作權限。

```
ACCESS_MASK 是 32 bits：
  bit 0–15：物件特定的存取（每個型別自定義）
  bit 16–23：標準存取（DELETE, READ_CONTROL, WRITE_DAC, WRITE_OWNER, SYNC）
  bit 24–27：保留
  bit 28：GENERIC_ALL
  bit 29：GENERIC_EXECUTE
  bit 30：GENERIC_WRITE
  bit 31：GENERIC_READ
```

常見的 Process 存取遮罩（以 Win11 為準）：

| 常數 | 值 | 含意 |
|---|---|---|
| `PROCESS_TERMINATE` | 0x0001 | 可殺死行程 |
| `PROCESS_CREATE_THREAD` | 0x0002 | 可建立遠端執行緒 |
| `PROCESS_VM_READ` | 0x0010 | 可讀行程記憶體 |
| `PROCESS_VM_WRITE` | 0x0020 | 可寫行程記憶體 |
| `PROCESS_VM_OPERATION` | 0x0008 | 可操作行程記憶體（VirtualAllocEx）|
| `PROCESS_DUP_HANDLE` | 0x0040 | 可 DuplicateHandle |
| `PROCESS_QUERY_INFORMATION` | 0x0400 | 可查詢行程資訊 |
| `PROCESS_ALL_ACCESS` | 0x1FFFFF | 全部 |

**為何 access mask 對 exploit 重要**：如果你拿到一個 handle，但它的 granted access 只有 `PROCESS_QUERY_INFORMATION`，你無法用它寫記憶體。Handle 的 access mask 在建立時固定，之後無法「升級」——你需要重新呼叫 `OpenProcess` 申請更高的 mask（如果你有足夠的安全性權限）。

本機實測（GetHandleInformation）：

```
=== GetHandleInformation ===
Handle flags: 0x0
(HANDLE_FLAG_INHERIT=1, HANDLE_FLAG_PROTECT_FROM_CLOSE=2)
```

旗標 = 0 代表這個 handle 既不可繼承也不受保護（可以被 CloseHandle 關掉）。

## DuplicateHandle：跨行程傳遞 handle

```python
# 本機實測
=== DuplicateHandle (shared handle mechanism) ===
Original handle: 0x44c, Duplicated handle: 0x458
Both point to same Process object (same PID=30048)
```

`DuplicateHandle(srcProc, srcHandle, dstProc, &dstHandle, access, inherit, options)` 在目標行程的 handle table 裡建立一個新的 entry，指向同一個 kernel 物件（PointerCount +1）。

**安全性含意**：
- 如果你有另一個行程的 handle（`PROCESS_DUP_HANDLE` 權限），你可以把那個行程的任意 handle 複製到自己的行程
- Windows 沙盒（renderer → browser process）的攻擊路徑裡，拿到 broker 行程的 handle 後 `DuplicateHandle` 它的高權限 handle 是常見手法

## Exploit 相關：Handle Leak、可預測性、Type Confusion

### Handle Leak

高權限服務建立一個物件後忘記關掉 handle，低權限攻擊者能透過某些機制拿到那個 handle。

最直接的例子：**可繼承 handle（inheritable handle）**。如果父行程建立了一個 `HANDLE_FLAG_INHERIT` 的高權限物件，然後用 `CreateProcess` 建立子行程，子行程會**繼承所有可繼承的 handle**（除非明確關閉）。如果子行程是低權限的（例如沙盒），它就意外拿到了高權限的 handle。

**WinObj / Process Hacker 工具**：可以查看各行程的 handle table，找出洩漏的高權限 handle。`NtQuerySystemInformation(SystemHandleInformation)` 從使用者態可枚舉所有行程的所有 handle（需要一定權限，但不需要 SeDebugPrivilege）。

### Handle 值的可預測性

Handle 值是從 index 4 開始，按行程使用狀況順序分配（如果有空洞就填空洞）。在**競爭條件（race condition）exploit**裡，如果能預測下一個 handle 會是哪個值（例如行程從一個乾淨狀態開始，handle 4 是第一個分配的），就可能在目標分配 handle 之前先佔那個 slot，讓目標的第一個 handle 操作失敗或落到可控的位置。

這在現代 Windows 上難度很高（handle table 動態且有保護），但在特定的低權限環境（Windows CE、某些 IoT 系統）仍然可行，且概念值得理解。

### Object Type Confusion

如果攻擊者能讓 kernel 以為一個物件是另一個型別，就能呼叫到錯誤的 type-specific 函式，導致未定義行為（UB）。例如：

```
目標：讓 kernel 把 Section 物件當作 Process 物件處理
效果：Process 型別的 TypeInfo 函式被呼叫在 Section 的 body 上
→ 型別特定的欄位偏移錯誤，導致 kernel 讀/寫到 Section body 的不同欄位
→ 可能控制函式指標或寫任意 kernel 位址
```

type confusion 通常需要先有某種 kernel write 原語（覆蓋 OBJECT_HEADER 裡的 TypeIndex），或者利用 Object Manager 本身的 bug（歷史上有幾個）。這是 kernel exploit 的範疇，userland pwn 課只需要知道這個概念存在及其威脅模型。

## 底層機制：Handle Table 的三層結構

Windows handle table 為了效能採用三層（類似 page table）：

```
行程的 EPROCESS.ObjectTable 指向：

Level 1 Table（頁面大小）
  ├── [0] → Level 2 Table 或直接是 handle entries（小行程）
  ├── [1] → Level 2 Table
  ...

Level 2 Table
  ├── handle entries（每個 entry = 16 bytes）
  ...

Handle Entry（16 bytes）:
  ┌────────────────┐
  │ Object Ptr     │  8 bytes（帶 flags 在低位）
  │ Access Mask    │  4 bytes
  │ Attributes     │  4 bytes（PROTECT_FROM_CLOSE, INHERIT...）
  └────────────────┘
```

object pointer 的低 3 bits 用作旗標（bit 0 = lock bit，bit 1 = inherit，bit 2 = protect from close），所以真正的物件位址要把低 3 bits 清掉：`obj_ptr = entry.ptr & ~7`。

這個細節在 kernel exploit 的 handle table spray / grooming 技法中很重要（雖然是 kernel 課的話題，但使用者態看到的 handle 值和這個結構有直接關係）。

## 對比與取捨

### Windows Handle vs Linux fd

| 面向 | Windows HANDLE | Linux fd |
|---|---|---|
| 型別 | 物件有明確型別（Process / File / Event…）| fd 幾乎是同質的（都是 file-like）|
| 存取控制 | 每個 handle 有 granted access mask | fd 繼承 file 的 flags（O_RDONLY 等）|
| 命名空間 | `\`（Object Manager namespace）| VFS（`/`）、abstract socket（`@`）|
| 跨行程傳遞 | `DuplicateHandle` | `SCM_RIGHTS` over Unix socket |
| 繼承 | `HANDLE_FLAG_INHERIT` per-handle | `FD_CLOEXEC` per-fd（預設可繼承）|
| 計數 | PointerCount + HandleCount 雙重計數 | 只有 file description 的引用計數 |
| 最大數量 | 理論 16M+，實際由配額限制 | `RLIMIT_NOFILE`（預設 1024/soft）|
| 值含意 | 4 的倍數，`>> 2` 是 index | 從 0 開始，`dup2` 可指定任意值 |

**Linux fd 比 Windows handle 更「平坦」**：每個 fd 都能 `read`/`write`/`poll`/`ioctl`，只是不同型別的物件支援不同的 ioctl。Windows 的型別系統更嚴格：你不能對 Event handle 呼叫 ReadFile，API 層面就會拒絕。

### Handle Table 在 exploit 裡的角色

| 攻擊場景 | handle 的角色 |
|---|---|
| 特權提升（kernel） | 覆蓋 handle table entry 改 access mask |
| 沙盒逃逸（browser） | DuplicateHandle 高權限 handle 跨行程傳 |
| 競爭條件（TOCTOU） | 預測 handle 值，在 check 和 use 之間替換 |
| 資訊洩露 | `NtQuerySystemInformation` 枚舉所有行程 handle |
| Named object squatting | 預先占用命名空間的物件名字 |

## 踩雷集錦

1. **「`CloseHandle` 就一定釋放物件記憶體」**：錯。`CloseHandle` 讓 HandleCount -1；只有 PointerCount 也降到 0，物件才被釋放。只要還有 kernel pointer 持有物件（例如 I/O 還在進行），關掉所有 handle 也不會立即釋放。

2. **「handle = 0 是第一個 handle」**：handle = 0 是無效的（NULL handle），類似 Linux 的 fd = -1。第一個有效 handle 通常從 4 開始（`HANDLE_FLAG_INHERIT = 0x00` 的 handle，index = 1，handle = 4）。

3. **「pseudo-handle -1 可以傳給其他 API 用」**：不行。`(HANDLE)-1`（current process）和 `(HANDLE)-2`（current thread）是「快捷方式」，只在呼叫者本身的行程有意義，傳給 `DuplicateHandle` 的 `hSourceProcess` 等 API 時需要先轉成真實 handle（用 `GetCurrentProcess` 但不是 pseudo，而是 `OpenProcess(self, ...)`）。

4. **「命名物件是全系統可見的」**：不一定。`\BaseNamedObjects\Name` 只在建立者的 session 可見。跨 session 需要 `Global\` 前綴（`\Global\Name`）。服務在 session 0，一般用戶在 session 1+，這就是 `Global\` 存在的原因。

5. **「handle 值在 DuplicateHandle 後會一樣」**：不一定。`DuplicateHandle` 在目標行程分配一個新的 handle entry，新 handle 的值取決於目標行程 handle table 的空位，不需要和來源 handle 相同。本機實測：來源 0x44c、目標 0x458，不同。

## 進階：再往深一層

**`ObRegisterCallbacks`（Kernel API）**：允許驅動/EDR 在物件 open 操作上注冊回調，可以在 `OpenProcess`/`OpenThread` 時修改 granted access mask（降低）。Windows Defender Credential Guard 用這個來保護 LSASS 行程：任何嘗試以高權限 open LSASS 的操作都被攔截、access mask 被降級。

**`NtSetInformationObject`（ObjectHandleFlagInformation）**：可以動態修改 handle 的 PROTECT_FROM_CLOSE 和 INHERIT 旗標（就是 SetHandleInformation 的底層）。

**`!handle 0 f` 在 WinDbg**（未實測，需安裝 WinDbg + symbols）：

```
> !handle 0 f
  ...
  HANDLE 0x448 → _EPROCESS (PID 30048)  GrantedAccess: 0x1fffff
  HANDLE 0x454 → _FILE_OBJECT            GrantedAccess: 0xc0100080
  HANDLE 0x458 → _KEVENT                 GrantedAccess: 0x1f0003
  HANDLE 0x44c → _KMUTANT                GrantedAccess: 0x1f0001
```

> **未實測，理論預期**：輸出格式和欄位名稱以實際 WinDbg 版本為準。symbols 對齊後才能看到型別名稱。

**面試題：解釋 Windows handle 的安全模型**（常在 Windows 安全面試出現）：HANDLE 是不透明的 index，由 Object Manager 維護 handle table，每個 entry 含 object pointer 和 granted access mask。操作前 kernel 檢查請求的 access 是否被 granted access 涵蓋（access check），不滿足就 ACCESS_DENIED。額外的安全：DACL 在物件的 security descriptor 裡，Open 時就做 ACL 檢查，拿到 handle 之後的操作只看 granted access。

## 動手練習

1. **Handle 觀察**：用 Python + ctypes 建立一個 Mutex、一個 Event、一個 Section（`CreateFileMapping`），用 `GetHandleInformation` 印出每個的旗標，用 `DuplicateHandle` 複製一個，確認兩個 handle 都能操作同一個 Event（一個 Set，另一個 Wait）。

2. **Handle table 枚舉**：呼叫 `NtQuerySystemInformation(SystemHandleInformation, ...)` 取得全系統 handle 表，找出哪些行程持有 `\KnownDlls\kernel32.dll` section 的 handle（提示：OBJECT_TYPE_NUMBER = Section = 0x24 左右，實際值用 WinObj 查）。

3. **Named object 碰撞**：建立一個命名 Event `Local\TestRace`，再用另一個 Python 行程試圖建立同名物件，觀察 `GetLastError()` 回傳 `ERROR_ALREADY_EXISTS`（183）的行為。

## 本章重點整理

- Windows Object Manager 統一管理所有 kernel 物件（Process / Thread / File / Event / Section…），每個物件有 `OBJECT_HEADER`（含 PointerCount / HandleCount / SecurityDescriptor）加 body。
- HANDLE 值 = `(index << 2) | flags`，全部是 4 的倍數，`>> 2` 是 handle table 的索引，低 2 bits 是旗標。
- 兩種計數：PointerCount（kernel 內部指標）決定物件的真正生命週期；HandleCount（使用者態 handle 數）決定何時觸發 DeleteProcedure。
- Named object 在 `\BaseNamedObjects\`（session 內）或 `\Global\`（跨 session）；命名空間搶佔（squatting）是常見安全問題。
- Exploit 關聯：handle leak 拿高權限 handle、`DuplicateHandle` 跨行程傳遞、object type confusion 覆蓋 OBJECT_HEADER 的型別指標。

## 自我檢核

- [ ] 不看筆記，能畫出 `OBJECT_HEADER + Body` 的記憶體佈局，說出 PointerCount 和 HandleCount 各在哪、各代表什麼
- [ ] 能解釋為什麼所有 HANDLE 值都是 4 的倍數（從 handle table entry 結構推導）
- [ ] 面試被問「`CloseHandle` 後物件一定被釋放嗎」，能給出完整的 PointerCount / HandleCount 計數邏輯
- [ ] 能說出 `(HANDLE)-1` 的意義，以及為什麼它不能直接傳給 `DuplicateHandle` 當 source handle
- [ ] 能解釋 named object squatting 攻擊的步驟，以及 `Global\` 和不加前綴的差異
- [ ] 對照 Linux fd，能說出 Windows handle 最大的三個設計差異（型別系統、access mask、命名空間）

## 延伸閱讀

### 官方文件

- **[Object Manager — Microsoft Learn（WDK）](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/object-manager)**
  - **讀哪裡**：「About Object Manager」概覽；`ObReferenceObject`、`ObDereferenceObject` 的 API 說明
  - **和本章的關聯**：本章的 PointerCount / HandleCount 雙計數機制的官方說明

- **[Handles and Objects（Win32 API）— Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/sysinfo/handles-and-objects)**
  - **讀哪裡**：「About Handles and Objects」；Handle 繼承和 CloseHandle 的行為說明
  - **和本章的關聯**：本章 handle 基礎知識的官方對應文件

- **[CreateEvent / CreateMutex / CreateFileMapping — MSDN](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createeventw)**
  - **讀哪裡**：各 Create 函式的 access mask 說明；`lpSecurityAttributes` 如何影響物件的 DACL
  - **和本章的關聯**：理解 granted access mask 在建立時如何被決定

### 書籍

- **《Windows Internals, 7th Edition》Part 1 Ch 8 — Security**
  - **讀哪裡**：「Access Tokens」、「Security Descriptors」、「Object Security」三節
  - **和本章的關聯**：access mask 和 DACL 的完整安全模型；SecurityDescriptor 在 OBJECT_HEADER 的角色
  - **前提**：本章讀完

- **《Windows Internals, 7th Edition》Part 1 Ch 3 — System Mechanisms（Object Manager 這節）**
  - **讀哪裡**：「Object Manager」節；handle table 三層結構圖；Object Type 的 TypeInfo 函式表
  - **和本章的關聯**：本章概念的最深入官方教材，補 OBJECT_HEADER 的每個欄位定義

### 工具 / 研究

- **[WinObj（Sysinternals）](https://learn.microsoft.com/en-us/sysinternals/downloads/winobj)**
  - **讀哪裡**：下載後直接跑，瀏覽 `\BaseNamedObjects`、`\KnownDlls`、`\ObjectTypes`
  - **和本章的關聯**：讓 Object Manager 命名空間可視化，是理解「named object 在哪裡」的最快方法

- **[Process Hacker / System Informer（GitHub）](https://github.com/winsiderss/systeminformer)**
  - **讀哪裡**：「Handles」頁籤；選任意行程看它的 handle table 內容
  - **和本章的關聯**：Handle leak 偵測、handle 的型別和 granted access mask 的實際觀察工具；開 LSASS 的 handle 看看 Credential Guard 怎麼降 access mask

- **[Project Zero：「One font vulnerability to rule them all」（2016）](https://googleprojectzero.blogspot.com/2016/05/one-font-vulnerability-to-rule-them-all.html)**
  - **讀哪裡**：Section Object 的操作相關部分；handle table 的 exploit 路徑
  - **和本章的關聯**：真實 Windows object 漏洞的具體案例；理解 object 濫用路徑的起點
  - **前提**：本章 + Ch 9（記憶體）讀完更易理解

→ [Ch 9 — 虛擬記憶體與保護](./09-virtual-memory.md)
