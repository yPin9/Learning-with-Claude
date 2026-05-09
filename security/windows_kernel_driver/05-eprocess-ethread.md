# Ch 5 — EPROCESS / ETHREAD

> 目標：掌握 EPROCESS 和 ETHREAD 的關鍵欄位，理解 Token 竊取攻擊的原理，能在 WinDbg 中追蹤進程和執行緒。

## 為什麼驅動要懂 EPROCESS

`EPROCESS` 是 Windows 進程的核心態表示。對安全研究者來說，它是最重要的結構之一：

- Token 竊取攻擊（最常見的 LPE 技術）需要修改 `EPROCESS.Token`
- DKOM（Direct Kernel Object Manipulation）攻擊修改 `EPROCESS.ActiveProcessLinks` 隱藏進程
- 驅動回調（Process Notify）傳進來的就是 `PEPROCESS`

## EPROCESS：進程的核心表示

`EPROCESS` 很大（Windows 11 上超過 2KB），這裡列關鍵欄位：

```c
// 簡化版，偏移因 Windows 版本而異
typedef struct _EPROCESS {
    KPROCESS Pcb;                    // +0x000 排程器使用的部分

    // 進程識別
    LARGE_INTEGER CreateTime;        // 建立時間
    HANDLE UniqueProcessId;          // PID
    
    // 進程連結串列
    LIST_ENTRY ActiveProcessLinks;   // 串接所有活躍進程的雙向連結
                                     // ← DKOM 攻擊的目標
    
    // 記憶體管理
    PVOID VadRoot;                   // Virtual Address Descriptor 樹
    ULONG_PTR DirectoryTableBase;    // CR3 值（頁目錄基址）
    
    // 安全
    EX_FAST_REF Token;              // 進程的安全 Token
                                    // ← Token 竊取攻擊的目標
    
    // 映像資訊
    UNICODE_STRING ImageFileName;    // 可執行檔名（前 15 字元）
    PVOID SectionBaseAddress;        // PE 基址
    
    // 父進程
    HANDLE InheritedFromUniqueProcessId;  // PPID
    
    // Handle Table
    PHANDLE_TABLE ObjectTable;       // 進程的 Handle Table
    
    // 保護等級（PPL，Protected Process Light）
    PS_PROTECTION Protection;        
    
    // 子系統
    PVOID Win32Process;              // GUI 進程的 win32k 資料
    
} EPROCESS;
```

**偏移不固定**：每個 Windows 版本偏移都不同。

動態查詢偏移的方式：
```c
// PsGetProcessImageFileName 等 API 不需要知道偏移
// 需要直接存取的欄位用 PsLookupProcessByProcessId 等 API
```

或在 exploit 中直接硬編碼（需要版本判斷），或從 ntoskrnl 的 PDB 找偏移。

## 在 WinDbg 查看 EPROCESS

```
kd> !process 0 0 notepad.exe
PROCESS ffffe00012345678
    SessionId: 1  Cid: 1234    Peb: 00000012aaaabbbb  ParentCid: 0abc
    DirBase: 12345000  ObjectTable: ffffe000aabb0000  HandleCount: 137
    Image: notepad.exe

kd> dt nt!_EPROCESS ffffe00012345678
   +0x000 Pcb              : _KPROCESS
   +0x2d8 ProcessLock      : _EX_PUSH_LOCK
   +0x2e0 UniqueProcessId  : 0x00001234
   +0x2e8 ActiveProcessLinks : _LIST_ENTRY [ 0xffffe000`deadbeef - 0xffffe000`12345670 ]
   +0x358 Token            : _EX_FAST_REF
   +0x450 ImageFileName    : [15]  "notepad.exe"
```

列出所有進程（沿 ActiveProcessLinks 走）：

```
kd> !process 0 0
（列出所有進程的 EPROCESS 地址、PID、映像名）
```

## ActiveProcessLinks：進程連結串列

所有活躍進程通過 `ActiveProcessLinks`（雙向連結串列）串接。

PsActiveProcessHead 是哨兵節點，遍歷方式：

```c
// 核心代碼遍歷進程
PEPROCESS currentProcess = PsInitialSystemProcess;
do {
    // 做一些事
    currentProcess = (PEPROCESS)((PLIST_ENTRY)currentProcess + 
                     FIELD_OFFSET(EPROCESS, ActiveProcessLinks))->Flink;
    // 修正偏移...（實際用 CONTAINING_RECORD）
} while (currentProcess != PsInitialSystemProcess);
```

**DKOM 進程隱藏**：把某個進程從這個連結串列摘除，`tasklist`、`Process Explorer` 就看不到它了（因為它們都靠這個連結串列枚舉進程）。Task Manager 在核心用 `NtQuerySystemInformation` 枚舉，最終也走這條路。

## Token 竊取：最常見的 LPE 技術

`Token` 是 `EX_FAST_REF`，裡面是 `ACCESS_TOKEN` 的指針（低 4 位是參考計數）。

Token 裡最重要的欄位是 `Privileges`（特權集合）和 `Sids`（進程的身份 SID）。

**SYSTEM Token**：`PsInitialSystemProcess`（System 進程，PID 4）擁有 SYSTEM Token，包含所有特權。

Token 竊取攻擊步驟：

```c
// 1. 找到 System 進程（PID 4）的 EPROCESS
PEPROCESS systemProcess = PsInitialSystemProcess;

// 2. 讀取 System 進程的 Token
EX_FAST_REF systemToken = *(EX_FAST_REF*)((PUCHAR)systemProcess + TOKEN_OFFSET);

// 3. 找到當前進程（exploit 進程）的 EPROCESS
PEPROCESS currentProcess = PsGetCurrentProcess();

// 4. 把 System Token 複製到當前進程
*(EX_FAST_REF*)((PUCHAR)currentProcess + TOKEN_OFFSET) = systemToken;

// 之後這個進程就有 SYSTEM 權限了
```

這是最乾淨的 kernel exploit payload 之一。利用任意寫漏洞，寫入那個 TOKEN_OFFSET，就能提權。

實際上用 shellcode 形式（exploit 後控制 RIP 跳過來的代碼）：

```c
// Token 竊取 shellcode（x64，Windows 10）
// TOKEN_OFFSET 因版本不同（Windows 10 1909 是 0x358）
void TokenSteal() {
    PEPROCESS current = PsGetCurrentProcess();  // 但在 shellcode 裡用 GS 暫存器直接算
    // ...
}
```

## ETHREAD：執行緒的核心表示

```c
typedef struct _ETHREAD {
    KTHREAD Tcb;                     // +0x000 排程器使用的部分

    LARGE_INTEGER CreateTime;
    HANDLE Cid;                      // ClientId：{PID, TID}
    
    // 所屬進程
    PEPROCESS ThreadsProcess;        // 指向 EPROCESS
    
    // APC 佇列
    KAPC_STATE ApcState;             // 附著的 APC 佇列
    
    // Win32 執行緒資訊
    PVOID Win32Thread;               // win32k 的執行緒結構
    
    // IRP 串列（執行緒等待的 I/O）
    LIST_ENTRY IrpList;
    
    // 系統呼叫資訊
    ULONG_PTR SystemCallNumber;      // 最近的 syscall number
    
} ETHREAD;
```

## FS/GS 暫存器：找到當前執行緒和進程

x64 下，GS 暫存器指向 **KPCR（Kernel Processor Control Region）**：

```
GS:0x000 → KPCR.GdtBase
GS:0x008 → KPCR.TssBase
GS:0x018 → KPCR.SelfPCR（自指標）
GS:0x020 → KPCR.CurrentPrcb（KPRCB，Processor Control Block）
GS:0x188 → KPRCB.CurrentThread  ← 當前執行緒的 KTHREAD/ETHREAD
```

```c
// 核心代碼可以這樣拿當前執行緒（編譯器會最佳化成 GS 存取）
PETHREAD currentThread = PsGetCurrentThread();
PEPROCESS currentProcess = PsGetCurrentProcess();

// 等同於（低層實作）
// PETHREAD = *(PETHREAD*)(__readgsqword(0x188));
// PEPROCESS = CONTAINING_RECORD(currentThread, ETHREAD, Tcb)->ThreadsProcess
```

Exploit shellcode 通常直接用 GS 暫存器走到 EPROCESS，不依賴 API：

```asm
; x64 shellcode snippet
swapgs                          ; 如果從用戶態進來需要這步
mov rax, gs:[188h]              ; KTHREAD（當前執行緒）
mov rax, [rax + PROCESS_OFFSET] ; EPROCESS（當前進程）
; 然後沿著 ActiveProcessLinks 找 System 進程
```

## PEB / TEB：用戶態的進程/執行緒結構

EPROCESS / ETHREAD 是核心態的。用戶態程式看的是：
- **PEB（Process Environment Block）**：DLL 列表、堆積、命令列、環境變數
- **TEB（Thread Environment Block）**：執行緒局部儲存（TLS）、最後錯誤碼、棧基址

用戶態找 PEB：
```c
// x64
PEB* peb = (PEB*)__readgsqword(0x60);  // TEB.ProcessEnvironmentBlock
// 或 NtCurrentPeb()

// 找載入的 DLL（InMemoryOrderModuleList）
LIST_ENTRY* moduleList = &peb->Ldr->InMemoryOrderModuleList;
```

從核心找用戶態 PEB：
```c
PPEB peb = PsGetProcessPeb(process);
```

## 自我檢核

- [ ] `EPROCESS.ActiveProcessLinks` 串接所有進程，DKOM 可從中摘除進程實現隱藏
- [ ] `EPROCESS.Token` 是安全憑證，Token 竊取 = 把 SYSTEM 的 Token 指針複製進來
- [ ] `GS:0x188` 在核心模式下指向當前執行緒的 KTHREAD
- [ ] `PsGetCurrentProcess()` / `PsGetCurrentThread()` 是驅動拿當前執行緒的標準 API
- [ ] Token 偏移因 Windows 版本而異，exploit 需要版本判斷或動態查詢

→ [Ch 6 WDM 骨架](./06-wdm-skeleton.md)
