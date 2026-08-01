# Ch 9 — 虛擬記憶體與保護：VirtualAlloc/Protect / section / W^X

> **目標**：學完這章你能在腦中畫出一個 Windows 行程的虛擬位址空間完整佈局，精確說出 MEM_RESERVE 和 MEM_COMMIT 的差異、七種頁保護常數的語意、section object 如何讓兩個行程共享同一塊實體記憶體，以及 W^X（Write XOR Execute）和 DEP 的底層關係——這些是 Part 3 打 ROP-to-VirtualProtect 的理論基礎。

> **環境**：Windows 11 Pro x64；Python 3.12 + ctypes；mingw-w64 GCC 14.2（`C:\msys64\ucrt64\bin`）。本章範例全部用 Python ctypes 實跑，mingw 用於觀察 PE 頁屬性。標注「未實測」的段落需要 WinDbg symbols 或 MSVC。

## 為什麼需要這個？

在 Linux 你用 `mmap(2)` 分配記憶體、`mprotect(2)` 改保護屬性——一個 syscall 搞定，內部是 kernel 的 VMA（Virtual Memory Area）鏈。Windows 的模型截然不同：Win32 API 把記憶體操作拆成**三個維度**（保留/提交/保護），並引入「section object」做跨行程共享。

不懂這個架構的後果：

- 你不知道為什麼 `VirtualAlloc` 分兩步、「保留」和「提交」差在哪
- 你看不懂 `VirtualQuery` 輸出，在除錯器裡對著 `MEM_RESERVE` 區域發呆
- 你不知道 ROP chain 打 `VirtualProtect` 的目標參數怎麼填
- 你搞不清楚 DEP/NX、W^X、`PAGE_EXECUTE_*` 這幾個詞的確切關係

這章把這三個維度說清楚，並用 Python ctypes 真跑驗證。

## 先建立直覺

### 從 Linux mmap 出發

Linux 的 `mmap(NULL, size, PROT_READ|PROT_WRITE, MAP_ANON|MAP_PRIVATE, -1, 0)` 一步到位：kernel 分配 VMA、指定保護屬性、頁表懶填（page fault 才真正分配實體頁）。

Windows 拆成三層：

```
  Linux                            Windows
  ──────                           ───────
  mmap → 分配 + 映射              VirtualAlloc(MEM_RESERVE) → 佔用位址空間
                                   VirtualAlloc(MEM_COMMIT)  → 綁實體頁（pagefile）
  mprotect → 改屬性               VirtualProtect            → 改頁保護
  munmap → 釋放                   VirtualFree(MEM_RELEASE)  → 全部歸還
                                   VirtualFree(MEM_DECOMMIT) → 退還實體頁但保留位址
```

### 位址空間佈局（x64 行程）

一個 64 位元 Windows 行程在使用者態能看到的虛擬位址空間是 128 TB（`0x0000_0000_0000_0000` 到 `0x0000_7FFF_FFFF_FFFF`），kernel 佔高地址端（`0xFFFF_8000_...` 以上，使用者態不可見）。

```
  使用者態虛擬位址空間（x64，128 TB）

  高地址 0x0000_7FFF_FFFF_FFFF
  ─────────────────────────────────────────────────────────
  │  ntdll / kernel32 / ... （系統 DLL，各行程基址相同）   │  MEM_IMAGE
  ─────────────────────────────────────────────────────────
  │              heap（NT Heap / Segment Heap）             │  MEM_PRIVATE, MEM_COMMIT
  │              （HeapAlloc 在這裡動）                     │
  ─────────────────────────────────────────────────────────
  │              thread stack × N                          │  MEM_PRIVATE
  │              （每 thread 預設 1 MB 保留、64 KB commit） │  含 GUARD 頁
  ─────────────────────────────────────────────────────────
  │              section view / 共享記憶體 / mmap 檔案     │  MEM_MAPPED
  ─────────────────────────────────────────────────────────
  │              image（PE 映像：.text/.data/.rdata/...）   │  MEM_IMAGE
  ─────────────────────────────────────────────────────────
  低地址 0x0000_0000_0001_0000
  ─────────────────────────────────────────────────────────
  │  64 KB null 頁（保留，不可用）                         │  MEM_FREE / 保留
  ─────────────────────────────────────────────────────────
  0x0000_0000_0000_0000
```

**VS Linux**：Linux 的 `maps` 看到的是 VMA 鏈；Windows 的 `VirtualQuery` 走訪得到的是**區域（region）鏈**，每個區域有 `State`（FREE/RESERVE/COMMIT）、`Protect`、`Type`（IMAGE/MAPPED/PRIVATE）。

## VirtualAlloc：兩步分配模型

### MEM_RESERVE — 只佔位，不燒實體頁

```c
LPVOID VirtualAlloc(
    LPVOID lpAddress,        // NULL = 讓系統選；非 NULL = 要求特定地址
    SIZE_T dwSize,           // 單位：byte，自動上取 allocation granularity（64 KB）
    DWORD  flAllocationType, // MEM_RESERVE | MEM_COMMIT | MEM_RESERVE|MEM_COMMIT
    DWORD  flProtect         // PAGE_* 常數
);
```

`MEM_RESERVE` 的效果：在行程的**位址空間**裡佔一段範圍，但**不分配實體頁、不分配 pagefile 配額**。這塊位址你不能讀、不能寫，存取就 Access Violation。

**為什麼要分開這一步？** 大型資料結構（例如 64 MB 的可增長緩衝）先 reserve 一塊連續位址空間，然後隨需求逐頁 commit——避免一次燒掉大量 pagefile 配額，但保持位址連續。這是 Linux `mmap` 的懶分配思路在 API 層的顯式版本。

### MEM_COMMIT — 真正綁實體資源

`MEM_COMMIT` 告訴 Memory Manager：在 pagefile 裡給我配額（backing）。第一次存取時 page fault，kernel 把零填充頁裝入實體 RAM，更新頁表。

可以兩步（reserve then commit）或一步合並（`MEM_RESERVE | MEM_COMMIT`）：

```c
// 一步到位（等同 mmap with MAP_ANON）
void *p = VirtualAlloc(NULL, 4096, MEM_RESERVE|MEM_COMMIT, PAGE_READWRITE);
```

### MEM_DECOMMIT / MEM_RELEASE

- `MEM_DECOMMIT`：退還實體頁配額，但**保留位址範圍**（仍佔位址空間，狀態從 COMMIT 變 RESERVE）。對應 `madvise(MADV_FREE)` 的效果但更顯式。
- `MEM_RELEASE`：完整釋放，位址空間和 pagefile 配額全還。呼叫 `VirtualFree(ptr, 0, MEM_RELEASE)` 時 `dwSize` 必須是 0。

**踩雷**：`MEM_DECOMMIT` 時 `dwSize` 可以不為 0（decommit 部分頁），但 `MEM_RELEASE` 的 `dwSize` 必須是 0，傳非零值會回傳 ERROR（87，Invalid parameter）。

### 實跑驗證（Python ctypes，本機真實輸出）

```python
import ctypes
import ctypes.wintypes as wt

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.VirtualAlloc.restype  = ctypes.c_void_p
kernel32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]

MEM_RESERVE = 0x2000
MEM_COMMIT  = 0x1000
PAGE_NOACCESS  = 0x01
PAGE_READWRITE = 0x04

# Step 1: reserve 64 KB
reserved = kernel32.VirtualAlloc(None, 64*1024, MEM_RESERVE, PAGE_NOACCESS)
# Step 2: commit first 4 KB
committed = kernel32.VirtualAlloc(reserved, 4096, MEM_COMMIT, PAGE_READWRITE)
buf = (ctypes.c_char * 4096).from_address(committed)
buf[0:5] = b"hello"
print(bytes(buf[0:5]))
```

**實際輸出**：

```
============================================================
Test 1: VirtualAlloc MEM_RESERVE, PAGE_NOACCESS
  reserved ptr  = 0x0000022C6A970000
  RegionSize    = 0x10000 (64 KB)
  State         = MEM_RESERVE
  Protect       = 0x00
  Type          = MEM_PRIVATE

============================================================
Test 2: VirtualAlloc MEM_COMMIT, PAGE_READWRITE on first 4096 bytes
  committed ptr = 0x0000022C6A970000
  State         = MEM_COMMIT
  Protect       = PAGE_READWRITE
  wrote b'hello', read back: b'hello'
```

注意：`Protect = 0x00` 出現在 MEM_RESERVE 的區域——保留頁沒有有效的頁保護，這不是 bug，是正常狀態（`AllocationProtect` 是 `PAGE_NOACCESS`，但 `Protect` 欄位只在 COMMIT 頁才有意義）。

## 頁保護常數詳解

Windows 的頁保護常數（`PAGE_*`）控制 CPU 頁表項的讀/寫/執行位元：

| 常數 | 值 | 讀 | 寫 | 執行 | 用途 |
|---|---|---|---|---|---|
| `PAGE_NOACCESS` | `0x01` | ✗ | ✗ | ✗ | 保留頁、陷阱頁 |
| `PAGE_READONLY` | `0x02` | ✓ | ✗ | ✗ | `.rdata`、`const` 全域 |
| `PAGE_READWRITE` | `0x04` | ✓ | ✓ | ✗ | 堆、stack、`.data` |
| `PAGE_WRITECOPY` | `0x08` | ✓ | COW | ✗ | 共享頁的寫入時複製 |
| `PAGE_EXECUTE` | `0x10` | ✗ | ✗ | ✓ | 罕見（執行但不可讀）|
| `PAGE_EXECUTE_READ` | `0x20` | ✓ | ✗ | ✓ | `.text`（W^X 合規）|
| `PAGE_EXECUTE_READWRITE` | `0x40` | ✓ | ✓ | ✓ | **RWX，exploit 目標** |
| `PAGE_EXECUTE_WRITECOPY` | `0x80` | ✓ | COW | ✓ | JIT 的 COW 變體 |

**修飾符**（OR 進去）：

| 修飾符 | 值 | 效果 |
|---|---|---|
| `PAGE_GUARD` | `0x100` | 第一次存取觸發 EXCEPTION_GUARD_PAGE，然後保護失效；stack 成長用此機制 |
| `PAGE_NOCACHE` | `0x200` | 停用 CPU cache，裝置 DMA 用 |
| `PAGE_WRITECOMBINE` | `0x400` | 合併寫，視訊幀緩衝用 |

**VS Linux**：Linux 的 `PROT_READ|PROT_WRITE|PROT_EXEC` 三個旗標自由組合；Windows 用列舉不用 bitmask——`PAGE_EXECUTE_READWRITE = 0x40` 不是三個位元 OR 的結果，是一個獨立值。要改保護屬性，不能用 OR 疊加，只能傳整個新值給 `VirtualProtect`。

### PAGE_GUARD：stack 成長的秘密

每個 thread 的 stack 不是一開始就全部 commit：

```
  thread stack 虛擬佈局（x64 預設 1 MB 保留）

  高地址（stack bottom, initial RSP）
  ┌──────────────────────────┐
  │  committed pages          │  PAGE_READWRITE（已用）
  │  ...                      │
  ├──────────────────────────┤  ← 成長邊界
  │  GUARD page               │  PAGE_READWRITE | PAGE_GUARD
  ├──────────────────────────┤
  │  reserved (uncommitted)   │  MEM_RESERVE
  │  ...                      │
  ├──────────────────────────┤
  │  stack overflow sentinel  │  PAGE_NOACCESS（固定，用於偵測溢位）
  └──────────────────────────┘
  低地址（stack top）
```

當 RSP 跨過 GUARD 頁時，CPU 觸發 `EXCEPTION_GUARD_PAGE`；kernel 的例外處理器把 GUARD 頁改 READWRITE、在下一頁設新 GUARD、commit 下一頁，讓 stack 悄悄往下延伸。這比 Linux 的 signal-based stack growing 更細緻，也是 stack overflow 探測的關鍵機制（Ch 19 細講）。

**exploit 意義**：stack overflow 要跨過 GUARD 頁才能觸碰 reserved 區，一旦 GUARD 被消除（已被觸發過）且 reserved 區的頁都被 commit 填滿，再往下就是 PAGE_NOACCESS——你只有那麼多 overflow 空間。

## VirtualProtect：改頁保護屬性

```c
BOOL VirtualProtect(
    LPVOID lpAddress,     // 頁對齊地址
    SIZE_T dwSize,        // 影響的字節數（自動上取到頁邊界）
    DWORD  flNewProtect,  // 新的 PAGE_* 值
    PDWORD lpflOldProtect // 輸出：舊的保護值
);
```

**VS mprotect**：Linux 的 `mprotect(addr, len, PROT_*)` 語意一致，但 Windows 多了「輸出舊保護值」的便利，不必自己先 `VirtualQuery` 再改。

**實跑驗證**：

```
============================================================
Test 3: VirtualProtect -> PAGE_EXECUTE_READWRITE
  VirtualProtect returned: 1  (old prot: PAGE_READWRITE)
  new Protect   = PAGE_EXECUTE_READWRITE

============================================================
Test 4: VirtualProtect -> PAGE_READONLY
  VirtualProtect returned: 1  (old prot: PAGE_EXECUTE_READWRITE)
  new Protect   = PAGE_READONLY
```

**為什麼 exploit 需要它**：DEP（Data Execution Prevention）讓 heap/stack 頁是 `PAGE_READWRITE` 而非 `PAGE_EXECUTE_*`，shellcode 放在這些頁上無法直接執行。ROP 繞過的目標是把 shellcode 所在頁改成 `PAGE_EXECUTE_READWRITE`（RWX），這就是「ROP-to-VirtualProtect」（Part 3 的核心技法，Ch 23 細講）。

## VirtualQuery：審視記憶體佈局

```c
SIZE_T VirtualQuery(
    LPCVOID lpAddress,
    PMEMORY_BASIC_INFORMATION lpBuffer,
    SIZE_T  dwLength
);
```

回傳一個 `MEMORY_BASIC_INFORMATION` 結構：

```c
typedef struct _MEMORY_BASIC_INFORMATION {
    PVOID  BaseAddress;       // 區域起始（頁對齊）
    PVOID  AllocationBase;    // 整個保留塊的起始
    DWORD  AllocationProtect; // VirtualAlloc 時指定的原始保護
    SIZE_T RegionSize;        // 本區域的大小（同類屬性的連續頁）
    DWORD  State;             // MEM_COMMIT | MEM_RESERVE | MEM_FREE
    DWORD  Protect;           // 當前頁保護（只在 COMMIT 有意義）
    DWORD  Type;              // MEM_IMAGE | MEM_MAPPED | MEM_PRIVATE
} MEMORY_BASIC_INFORMATION;
```

`VirtualQuery` 一次只回傳**一個同質區域**——相同 State/Protect/Type 的連續頁構成一個區域。走訪整個位址空間要用迴圈，每次 `addr += mbi.RegionSize` 移到下一個區域。

**實跑驗證**（reserve 64 KB，commit 前 4 KB，走訪三個區域）：

```
============================================================
Test 5: VirtualQuery walk across reserved region
  [region 0] Base=0x0000022C6A970000  Size=0x1000  State=MEM_COMMIT  Protect=PAGE_READONLY
  [region 1] Base=0x0000022C6A971000  Size=0xF000  State=MEM_RESERVE  Protect=0x00
  [region 2] Base=0x0000022C6A980000  Size=0x7000  State=MEM_COMMIT  Protect=PAGE_READWRITE
```

三個區域：第 0 頁 COMMIT（region 0）、剩下 60 KB RESERVE（region 1）、緊接其後屬於別人的 COMMIT 頁（region 2）。這就是「reserve 不 commit 其餘」的實際空間呈現。

**VS Linux**：`/proc/PID/maps` 給你 VMA；`VirtualQuery` 給你的是更細粒度的「protection region」——同一個 VMA 裡若用 `VirtualProtect` 把部分頁改成不同保護，會拆成多個 `VirtualQuery` 區域。

### 用 VirtualQuery 找 RWX 頁

exploit 開發時常需要找行程內哪些頁是 RWX（`PAGE_EXECUTE_READWRITE`）——JIT engine、打包器、老舊程式碼都可能留 RWX 頁。掃描方式：

```python
PAGE_EXECUTE_READWRITE = 0x40
MEM_COMMIT = 0x1000
addr = 0x10000
while addr < 0x7FFF_FFFF_0000:
    m = query(addr)
    if m.State == MEM_COMMIT and (m.Protect & 0xFF) == PAGE_EXECUTE_READWRITE:
        print(f"RWX region: 0x{m.BaseAddress:016X} size=0x{m.RegionSize:X}")
    addr += m.RegionSize
```

這是找「已有 RWX 頁，直接用不必打 VirtualProtect」的原語。

## 底層機制：VAD（Virtual Address Descriptor）

OS 側真正管理位址空間的資料結構是 **VAD（Virtual Address Descriptor）樹**，一棵 AVL 樹（Windows Internals 書中有時稱 balanced binary tree），每個節點描述一段保留區域。`VirtualAlloc(MEM_RESERVE)` 插入一個 VAD 節點；`VirtualFree(MEM_RELEASE)` 刪除它。

```
  Process 的 VAD 樹（簡化）

  EPROCESS
  ├── VadRoot ──► (VAD node: ntdll image)
  │               ├── left: (VAD node: heap)
  │               └── right: (VAD node: stack of thread 1)
  │                          └── right: (VAD node: stack of thread 2)
  └── ...

  每個 VAD 節點記錄：
    StartVpn, EndVpn   ← 虛擬頁號範圍
    Flags              ← Private/Mapped/Image, Protection...
    Subsection         ← （IMAGE/MAPPED 才有）指向 section object
```

使用者態看到的 `VirtualQuery` 結果就是對 VAD 樹的使用者態投影。在 WinDbg 可用 `!vad` 指令印出整棵樹。

> **未實測，理論預期**：裝好 WinDbg + symbols 後在目標行程可驗證：
> ```
> 0:000> !vad
> VAD      level      start      end  commit
> ...
> 22c6a970  (  4)  22c6a970  22c6a97f       1  Private  READWRITE
> ```

**VS Linux**：Linux kernel 的 `mm_struct` 有 `mmap_tree`（RB-tree of `vm_area_struct`）；Windows 的 `EPROCESS` 有 `VadRoot`（AVL tree of `_MMVAD`）。概念一致，結構名稱不同。

## Section Object 與共享記憶體

### Section = Windows 的「具名 mmap」

Linux 的 `mmap` 有兩種用法：匿名映射（`MAP_ANON`）和檔案映射（`open+mmap`）。Windows 用 **section object**（核心物件）統一這兩種：

```
  CreateFileMapping / NtCreateSection
       │
       ▼
  Section Object（在 Object Manager 命名空間）
       │
  ┌────┴─────┐
  │  view 1  │  MapViewOfFile(hSection, ...) ← 行程 A
  │  view 2  │  MapViewOfFile(hSection, ...) ← 行程 B
  └──────────┘
  兩個 view 指向同一塊實體頁（共享，零拷貝）
```

建立步驟：

```c
// 1. 建立 section（pagefile 匿名 → hFile = INVALID_HANDLE_VALUE）
HANDLE hMap = CreateFileMapping(
    INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE,
    0, 4096, L"Local\\MySharedMem");

// 2. 映射到本行程
void *view = MapViewOfFile(hMap, FILE_MAP_ALL_ACCESS, 0, 0, 4096);
```

`FILE_MAP_ALL_ACCESS = 0xF001F`，包含讀/寫/執行（執行部分受 section 的 AllocationProtect 約束）。

**實跑驗證**（兩個 view 指向同一塊記憶體）：

```
============================================================
CreateFileMapping (anonymous, 4KB) + MapViewOfFile
  hmap = 360 (0=failure)
  view ptr = 0x00000188CCCB0000
  wrote: b'shared secret'
  view2 ptr = 0x00000188CCCC0000
  read via view2: b'shared secret'
  Unmapped and closed. Done.
```

兩個 view 的虛擬位址不同（`...B0000` vs `...C0000`），但映射到同一塊實體頁——`view` 寫，`view2` 立刻讀到。這是 IPC 最快的路徑，因為沒有系統呼叫拷貝。

> 注意輸出中「hmap = 360 (0=failure)」的說明：360 是 HANDLE 的整數值，`CreateFileMapping` 失敗才會回傳 NULL（0），所以 360 ≠ 0 代表成功。

### PE 映像本身就是一個 section

你可能沒想到：PE loader 把 `.exe` 和所有 `.dll` 都映射成 section——`NtCreateSection` 讀 PE 檔、`NtMapViewOfSection` 把它映射進行程位址空間。這就是為什麼 `VirtualQuery` 裡 `Type = MEM_IMAGE` 的區域是 PE 的各個 section（`.text`、`.data` 等），而每個行程各有自己的 view 指向同一份實體頁（`.text` 共享、`.data` COW）。

### WRITECOPY（COW）

`PAGE_WRITECOPY` 是 Windows 的 Copy-on-Write 保護：多個行程共享同一份唯讀頁（典型是 DLL 的 `.data`），只要任何行程企圖寫入，Memory Manager 立刻為那個行程複製一份私有頁——其他行程的 view 不受影響。

**VS Linux**：Linux 的 `MAP_PRIVATE` 就是 COW；Windows 的 `PAGE_WRITECOPY` 是頁保護層的 COW 旗標，且 PE loader 在映射共享 DLL 時自動用它保護可寫 section（避免 DLL 的 global 變數污染其他行程）。

## W^X 與 DEP

### W^X 是什麼

W^X（Write XOR Execute）是一個安全原則：任何頁**要麼可寫（Write），要麼可執行（Execute），不能同時**。這對應到 Windows 頁保護：合規的頁只用 `PAGE_READWRITE`（可寫不可執行）或 `PAGE_EXECUTE_READ`（可執行不可寫），而 `PAGE_EXECUTE_READWRITE`（RWX，同時可寫可執行）是 W^X 的違例。

### DEP（Data Execution Prevention）= 硬體 W^X 執行

DEP 是 W^X 原則的**硬體執行**機制，在 Windows 上的底層是 CPU 的 **NX 位元**（Intel 叫 XD, Execute Disable；AMD 叫 NX, No Execute）：

```
  頁表項（PTE）x64

  bit 63 = NX bit（No Execute）
    0 = 可執行
    1 = 執行觸發 #PF（Page Fault）→ Access Violation

  Windows Memory Manager 根據 PAGE_* 常數設定 NX bit：
    PAGE_READWRITE          → NX=1（heap/stack 不可執行）
    PAGE_EXECUTE_READ       → NX=0（.text 可執行）
    PAGE_EXECUTE_READWRITE  → NX=0（RWX，W^X 違規）
```

PE 標頭的 `DllCharacteristics` 有 `IMAGE_DLLCHARACTERISTICS_NX_COMPAT`（`0x0100`）位元——這告訴 loader 要求 DEP 開啟。如果這個位元沒設，系統可能在相容模式下不強制 DEP（老程式的情境）。

### DEP 的四個模式（BCDEDIT 控制）

| 模式 | OptIn | OptOut | AlwaysOn | AlwaysOff |
|---|---|---|---|---|
| 說明 | 只保護 Windows 元件 + 自選程式 | 所有程式開，除非 opt-out | 所有程式強制 | 停用 DEP |
| Win11 預設 | ✓ | | | |
| 漏洞研究用 | | | | ✓（虛擬機） |

現代 Win11 幾乎都是 `OptIn`，而多數系統服務和瀏覽器都自選加入 DEP，所以實際上和 `AlwaysOn` 差不多。

### exploit 關聯：ROP-to-VirtualProtect

DEP 開啟後，shellcode 放在 `PAGE_READWRITE` 的頁（heap、stack）不能直接跳到那裡執行——CPU 的 NX 位元擋住了。

標準繞法（Part 3 的主題，x64 calling convention 版本）：

```
  ROP chain 目標（x64 Windows System V 相反，用 Microsoft ABI）：

  gadget: pop rcx; ret   ← 放 shellcode 所在頁的位址（第 1 參數）
  gadget: pop rdx; ret   ← 頁大小 4096（第 2 參數）
  gadget: pop r8;  ret   ← PAGE_EXECUTE_READWRITE 0x40（第 3 參數）
  gadget: pop r9;  ret   ← &lpOldProtect（任意 writable 地址，第 4 參數）
  gadget: ret            ← 堆疊對齊（x64 ABI 要求 16 byte 對齊）
  VirtualProtect         ← 從 kernel32 IAT 或 PEB walk 拿到
  shellcode_ptr          ← VirtualProtect 返回後 ret 跳來這裡執行
```

這就是 Ch 23 的骨架；這章的任務是讓你徹底理解「VirtualProtect 把頁屬性從 RW 改成 RWX，DEP/NX 機制就繞過了」的底層原因。

## 對比與取捨

| 項目 | Linux | Windows |
|---|---|---|
| 匿名分配 API | `mmap(MAP_ANON\|MAP_PRIVATE)` | `VirtualAlloc(MEM_RESERVE\|MEM_COMMIT, PAGE_READWRITE)` |
| 改保護 | `mprotect(addr, len, PROT_*)` | `VirtualProtect(addr, len, PAGE_*, &old)` |
| 查詢布局 | `/proc/PID/maps`、`pmap` | `VirtualQuery` 迴圈 |
| 共享記憶體 | `mmap(MAP_SHARED)`、`shm_open` | `CreateFileMapping` + `MapViewOfFile` |
| COW 機制 | `MAP_PRIVATE` | `PAGE_WRITECOPY` |
| W^X 執行 | NX bit（核心選項可配置）| `PAGE_EXECUTE_READWRITE` 是顯式單值；NX bit 由 MM 根據常數設定 |
| 保留/提交分離 | 不顯式（懶分配隱式完成） | 顯式（`MEM_RESERVE` vs `MEM_COMMIT`） |
| 分配粒度 | 頁（4 KB） | 保留 64 KB 對齊；commit 可到 1 頁 |

**為什麼 Windows 要 64 KB 分配粒度（allocation granularity）？**

歷史遺留決定：16 位元 OS/2 時代為了讓 Intel 286 的段選擇子空間對齊而選了 64 KB；遷移到 32/64 位元時保留了這個值以維持相容性。`VirtualAlloc` 保留區域的起始地址永遠是 64 KB 的倍數，但 commit 和保護改動可以精確到 4 KB 頁。這個設計讓你「不小心 reserve 到緊接別人的頁」的風險降低。

## 踩雷集錦

1. **「VirtualAlloc 傳 PAGE_EXECUTE_READWRITE 就能直接在返回的指標跑 shellcode」**：對，但你必須同時確認頁確實 commit（`MEM_COMMIT`）且已寫入 shellcode。只 reserve 的頁即使保護是 `PAGE_EXECUTE_READWRITE`，存取還是 AV。

2. **「VirtualProtect 失敗說頁地址 invalid」**：`VirtualProtect` 要求地址在一個 **commit** 的區域。對 `MEM_RESERVE` 的頁呼叫 `VirtualProtect` 會失敗（`GetLastError()` 返回 487，`ERROR_INVALID_ADDRESS`）。必須先 commit 再改保護。

3. **「MEM_RELEASE 傳 dwSize 非零」**：`VirtualFree(ptr, 4096, MEM_RELEASE)` 會失敗，返回 `ERROR_INVALID_PARAMETER`（87）。`MEM_RELEASE` 的 `dwSize` 必須是 0，意味「整個 allocation 一起釋放」。

4. **「PAGE_GUARD 頁可以重複用」**：`PAGE_GUARD` 是「一次性」——第一次存取觸發 `EXCEPTION_GUARD_PAGE` 後，保護自動消失（變成底層的基本保護），頁就是普通的 COMMIT 頁了。如果你想模擬 stack guard，每次用完要重新 `VirtualProtect` 加回 `PAGE_GUARD`。

5. **「VirtualQuery 在 MEM_RESERVE 的頁上，Protect 欄位有意義」**：`Protect` 只在 `State == MEM_COMMIT` 時有意義；保留頁的 `Protect` 欄位通常是 0，你想知道原始保護值要看 `AllocationProtect`。

## 進階：再往深一層

### 大頁（Large Page）

`VirtualAlloc` 可傳 `MEM_LARGE_PAGES`（`0x20000000`）請求 2 MB 大頁（x64 平台）。大頁減少 TLB miss，對高吞吐記憶體密集型應用有意義，但需要 `SeLockMemoryPrivilege`，且不支援 `PAGE_EXECUTE_*`（DEP 強制 large page 不可執行是故意的安全設計）。

### AWE（Address Windowing Extensions）

`AllocateUserPhysicalPages` + `MapUserPhysicalPages`：讓 32 位元行程訪問超過 4 GB 的實體記憶體（類似 Linux PAE）。x64 時代幾乎不用，但漏洞研究有時碰到舊系統。

### Section Object 的保護繼承

`CreateFileMapping` 時指定的 `PAGE_*` 是**上限（maximum protection）**——`MapViewOfFile` 的 `dwDesiredAccess` 不能超過這個上限。若 section 是 `PAGE_READONLY`，你不能用 `FILE_MAP_WRITE` 映射它。這個上限機制在 kernel 層由 `_SECTION` 的 `Access` 欄位控制，是強制的。

### 面試題

**Q：堆（heap）的頁和 stack 的頁，保護屬性有什麼不同？**

A：都是 `PAGE_READWRITE`（RW，no execute）；但 stack 的頁還有成長機制——最低的已 commit 頁下面有一個 `PAGE_READWRITE | PAGE_GUARD` 頁，再下面是 `MEM_RESERVE`。heap 沒有 GUARD 頁，因為 heap 的成長由 HeapAlloc 內部 `VirtualAlloc` 管理，不靠頁錯誤觸發。

## 動手練習

寫一個 Python ctypes 程式，掃描目前行程的整個使用者態虛擬位址空間，印出所有 **`Type == MEM_IMAGE` 且 `State == MEM_COMMIT` 且 `Protect == PAGE_EXECUTE_READ`** 的區域（這些是 PE 映像的 `.text` 段）。輸出每個區域的 `BaseAddress`、`RegionSize`、`AllocationBase`（映像基址）。

提示：`VirtualQuery` 迴圈，`addr += mbi.RegionSize`，直到 `addr >= 0x7FFF_FFFF_0000` 或 `VirtualQuery` 返回 0。

## 本章重點整理

- Windows 的記憶體分配是**三層**：`VirtualAlloc(MEM_RESERVE)` 佔位址、`MEM_COMMIT` 綁實體頁、`VirtualProtect` 改頁保護。保留頁不能讀寫；只有 commit 頁的 Protect 欄位有意義。
- **七種頁保護常數**中，`PAGE_EXECUTE_READWRITE`（0x40）是 W^X 違例（RWX），是 ROP-to-VirtualProtect 的目標；合規的執行頁是 `PAGE_EXECUTE_READ`。
- **DEP** 是 W^X 的硬體執行（CPU NX bit）；繞過方式是用 ROP chain 呼叫 `VirtualProtect` 把 shellcode 所在頁從 `PAGE_READWRITE` 升為 `PAGE_EXECUTE_READWRITE`。
- **Section object** = Windows 的具名 mmap：`CreateFileMapping` + `MapViewOfFile`，零拷貝跨行程共享；`PAGE_WRITECOPY` 是 COW 保護；PE 映像本身就是 section 映射。

## 自我檢核

- [ ] 不看筆記，能說出 MEM_RESERVE 和 MEM_COMMIT 的差異，以及為什麼要分兩步（節省 pagefile 配額 + 保持位址連續）
- [ ] 能說出 `PAGE_EXECUTE_READWRITE` 的值（0x40）以及它在 W^X 模型中是什麼角色
- [ ] 知道 `VirtualQuery` 的 `Protect` 欄位在 `MEM_RESERVE` 頁上回傳什麼（0，無意義），以及應該看哪個欄位（`AllocationProtect`）
- [ ] 能用一段偽代碼描述「ROP-to-VirtualProtect 繞 DEP」的大概流程（參數怎麼設、返回後跳哪裡）
- [ ] 知道 `PAGE_GUARD` 是一次性的，以及 thread stack 用它做什麼
- [ ] 面試題：為什麼 Windows 的保留粒度是 64 KB 而不是 4 KB？（歷史原因 + 減少碰撞風險）

## 延伸閱讀

### 官方文件

- **[VirtualAlloc function — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualalloc)**
  - **讀哪裡**：`flAllocationType` 與 `flProtect` 的所有值列表，尤其是 `MEM_LARGE_PAGES` 和 `MEM_PHYSICAL`；Remarks 段的分配粒度（64 KB）說明
  - **和本章的關聯**：本章的 API 語意參照來源；Part 3 寫 exploit 時反覆查保護常數值

- **[Memory Protection Constants — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/memory/memory-protection-constants)**
  - **讀哪裡**：所有 `PAGE_*` 常數的精確定義與修飾符（GUARD/NOCACHE/WRITECOMBINE）
  - **和本章的關聯**：本章表格的來源；打 exploit 時這張表是必查的

### 書籍

- **《Windows Internals, 7th Edition》Part 1，Chapter 5：Memory Management**（Yosifovich, Ionescu 等，Microsoft Press）
  - **讀哪裡**：「Virtual Address Space Layouts」、「VADs（Virtual Address Descriptors）」、「Working Sets」三節；約 100 頁但密度極高
  - **和本章的關聯**：本章的 VAD 樹描述和 W^X 機制是這本書的精煉版；要真正理解 kernel 怎麼管頁表需要回來讀這裡
  - **前提知識**：本章讀完後再來，否則 VAD/PTE 的術語會看不懂

### 部落格 / 研究

- **[Connor McGarr — Exploit development series](https://connormcgarr.github.io/)**
  - **讀哪裡**：搜 "VirtualAlloc" 或 "VirtualProtect"；他的 exploit 開發文章把 VirtualProtect 在 ROP 中的用法從底層到實戰講清楚
  - **和本章的關聯**：本章 ROP-to-VirtualProtect 的動機；Part 3（Ch 23）的延伸讀物
  - **前提知識**：本章讀完 + 基本 ROP 概念

- **[j00ru — Windows kernel / exploit research](https://j00ru.vexillium.org/)**
  - **讀哪裡**：Exploit 方法論與 Windows 記憶體保護的文章；側重 kernel 但 userland 部分的分析也適用
  - **和本章的關聯**：VirtualQuery-based 信息洩漏（找 module 基址）在他的文章裡有完整範例

- **[Corelan — Exploit Writing Tutorial Part 10: Chained return-to-libc/ROP](https://www.corelan.be/index.php/2011/12/31/exploit-writing-tutorial-part-11-heap-spraying-demystified/)**
  - **讀哪裡**：Corelan Part 10（ROP 篇）關於 VirtualProtect 作為 ROP payload 的段落
  - **和本章的關聯**：本章講原理，Corelan 給你第一手「拼 ROP-to-VirtualProtect」的操作細節
  - **前提知識**：x86 calling convention（Corelan 文章是 x86，概念可遷移到 x64）

行程的虛擬記憶體是底層地基；下一章我們往上一層，看一個行程從無到有是怎麼建立起來的——CreateProcess 的內部七個階段。

→ [Ch 10 — 行程與執行緒建立：CreateProcess 內部](./10-process-thread-creation.md)
