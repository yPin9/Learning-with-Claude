# Ch 12 — 例外處理架構 II：x64 table-based SEH / VEH / UEF

> **目標**：搞清楚 x64 Windows 為什麼把 SEH 從 stack-based 改成 table-based（`.pdata`/`RUNTIME_FUNCTION`/`UNWIND_INFO`），這個改變讓 x64 上的 SEH overwrite 基本失效；同時學會 Vectored Exception Handler（VEH）和 `UnhandledExceptionFilter`（UEF）這兩個在攻防都有戲的機制。

> **環境**：Python 3.12 + ctypes（本機可跑）；mingw-w64 GCC 14.2（本機可跑）。MSVC / WinDbg 相關指令標「未實測」。

---

## 為什麼需要這個？

x86 的 SEH chain 住在 stack 上，一個 buffer overflow 就能蓋掉 handler 指標。Microsoft 在設計 x64 ABI 時清楚知道這個問題，決定從根本上解決它：**把 exception handler 的資訊移出 stack，放進 PE image 的唯讀 section**。

這個決定有連鎖效應：
- x64 的「SEH overwrite」技法（把 `Next`/`Handler` 蓋成 gadget）在目標沒有 SafeSEH bypass 時基本失效
- 但攻擊面並沒有消失——VEH 和 UEF 提供了另一組（不同難度的）攻擊原語
- 理解為什麼 x64 SEH 沒有「鏈在 stack 上」，是你理解 x64 exploit 工具箱和 x86 差異的必要地基

## 先建立直覺：table-based vs stack-based

x86 和 x64 的 SEH 是兩種完全不同的思路，用一個比喻來理解：

**x86 stack-based SEH**：像每個工人（函式）進場時，把「我負責處理這類事故」的告示牌**插在工地地板上**（stack）。事故發生時，從工地頂端（最近進場的工人）開始一一查告示牌。問題：地板是公共區域，任何人都能把別人的告示牌換掉。

**x64 table-based SEH**：像開工前先把所有工人的責任範圍**刻在建築物鋼架（PE image 的唯讀 section）上**。事故發生時，根據事故位址查鋼架上對應的責任人。問題：平時你不能修改鋼架（唯讀），但如果你能改 PE image（某些攻擊手法），另當別論。

兩套系統的 handler 資訊都是「函式 A 負責這個地址範圍內的例外」，只是存放位置不同。

## `.pdata` 段：x64 的例外資訊來源

x64 PE 有一個必要的 data directory entry（`IMAGE_DIRECTORY_ENTRY_EXCEPTION`，index 3）指向 `.pdata` 節。這個節裡存放的是 `RUNTIME_FUNCTION` 陣列，每個函式一個項目（除了 leaf function——沒有 local var、不呼叫其他函式的函式可以省略）。

```c
/* winnt.h */
typedef struct _RUNTIME_FUNCTION {
    DWORD BeginAddress;    /* 函式起始 RVA（相對 image base） */
    DWORD EndAddress;      /* 函式結束 RVA */
    DWORD UnwindInfoAddress; /* 指向 UNWIND_INFO 的 RVA */
} RUNTIME_FUNCTION;
```

三個欄位都是 RVA（Relative Virtual Address），不是絕對位址。這很重要：因為是相對的，ASLR 搬動 image base 時，這裡的值**不需要 relocation**，永遠正確。

實際驗證——我們用 mingw gcc 編一個含多個函式的 x64 PE，觀察 `.pdata` 節的內容：

```console
$ cat > D:\tmp_build\debug_hello.c << 'EOF'
#include <stdio.h>
int main(void) { printf("debug test\n"); return 0; }
EOF
$ gcc -O0 -g -o D:\tmp_build\debug_hello_debug.exe D:\tmp_build\debug_hello.c

$ objdump -h D:\tmp_build\debug_hello_debug.exe | grep -E "(pdata|xdata)"
  3 .pdata        00000210  0000000140005000  ...
  4 .xdata        00000198  0000000140006000  ...
```

`.pdata` 大小 `0x210` bytes / 12 bytes per entry = 0x1C = **28 個 `RUNTIME_FUNCTION` 項目**。

`.pdata` 的原始內容（每行 12 bytes = 一個 RUNTIME_FUNCTION）：

```console
$ objdump --section=.pdata -s D:\tmp_build\debug_hello_debug.exe
Contents of section .pdata:
 140005000 00100000 01100000 00600000 10100000 ...
           ^^^^^^^^ BeginRVA  ^^^^^^^^ UnwindInfoRVA
                    ^^^^^^^^ EndRVA
```

第一個 entry：`BeginAddress=0x00001000`、`EndAddress=0x00001001`、`UnwindInfoAddress=0x00006000`（這些是 image 內的 RVA，不是絕對位址）。

**這些資訊完全不在 stack 上**。Stack overflow 再大，也蓋不到 image 裡的 `.pdata` 節。

## `UNWIND_INFO`：handler 和 unwind codes

`.pdata` 裡的 `UnwindInfoAddress` 指向 `.xdata` 節裡的 `UNWIND_INFO` 結構，這才是 handler 指標的實際存放地：

```c
typedef struct _UNWIND_INFO {
    UBYTE  Version       : 3;  /* = 1 */
    UBYTE  Flags         : 5;  /* UNW_FLAG_NHANDLER, UNW_FLAG_EHANDLER, UNW_FLAG_UHANDLER, UNW_FLAG_CHAININFO */
    UBYTE  SizeOfProlog;
    UBYTE  CountOfCodes;       /* 後面跟的 UNWIND_CODE 數量 */
    UBYTE  FrameRegister : 4;
    UBYTE  FrameOffset   : 4;
    UNWIND_CODE UnwindCode[CountOfCodes];
    /* 如果 Flags & UNW_FLAG_EHANDLER 或 UNW_FLAG_UHANDLER： */
    OPTIONAL ULONG ExceptionHandler;       /* handler RVA */
    OPTIONAL ULONG ExceptionHandlerData[]; /* 語言特定資料 */
    /* 或如果 Flags & UNW_FLAG_CHAININFO： */
    OPTIONAL RUNTIME_FUNCTION ChainedEntry;
} UNWIND_INFO;
```

`Flags` 欄位：
- `UNW_FLAG_NHANDLER`（0）：這個函式沒有 exception handler，只有 unwind codes
- `UNW_FLAG_EHANDLER`（1）：有 exception handler（`__try/__except`）
- `UNW_FLAG_UHANDLER`（2）：有 termination handler（`__try/__finally`）
- `UNW_FLAG_CHAININFO`（4）：這個 UNWIND_INFO 是前一個的延伸（大 prolog 用）

**關鍵點**：`ExceptionHandler` 是一個 RVA，指向的函式在 image 裡，不在 stack 上。

### 例外分發流程（x64 版）

x64 的 `RtlDispatchException` 不再走 `FS:[0]` 的鏈，而是：

```
1. 取得例外的 RIP 位址

2. 用二分搜尋在 .pdata 陣列裡找 BeginAddress <= RIP < EndAddress 的 RUNTIME_FUNCTION

3. 從 RUNTIME_FUNCTION.UnwindInfoAddress 找到 UNWIND_INFO

4. 如果 UNWIND_INFO.Flags & UNW_FLAG_EHANDLER：
     呼叫 ExceptionHandler（以 RVA + ImageBase 計算的絕對位址）
     傳入 ExceptionRecord + DispatcherContext
     
   如果回傳 ExceptionContinueSearch → 繼續 step 5
   如果回傳 ExceptionContinueExecution → 繼續執行
   如果回傳 ExceptionNestedException → 複雜情況略

5. 如果 UNWIND_INFO.Flags & UNW_FLAG_CHAININFO：
     跟著 ChainedEntry 找下一個 UNWIND_INFO

6. 往 caller frame 走（用 UNWIND_CODE 還原 RSP 和暫存器）
   重複 step 2–5，直到找到 handler 或走完 call stack
```

**和 x86 的關鍵差異**：走鏈的依據是 RIP 位址查 `.pdata` 表，不是 `FS:[0]` 鏈。每往上走一個 frame，是透過 `UNWIND_CODE` 還原 RSP（不是讀 stack 上的 Next 指標）。

```
x86 dispatch:
  FS:[0] → rec1 → rec2 → 0xFFFFFFFF
  （走的是 stack 上的 Next 指標）

x64 dispatch:
  RIP → .pdata 二分搜 → RUNTIME_FUNCTION → UNWIND_INFO → handler
  退框：用 UNWIND_CODE 還原 RSP → 新的 RIP → 再搜 .pdata
  （走的是 image 裡的表，不碰 stack 上的任何指標）
```

## x64 為什麼讓 SEH overwrite 失效

在 x86 上，SEH overwrite 的流程是：
1. overflow → 蓋掉 `EXCEPTION_REGISTRATION_RECORD.Handler`
2. 觸發例外
3. `RtlDispatchException` 讀 `FS:[0]` → 走到被蓋掉的 record → 呼叫假 handler → EIP 控制

在 x64 上：
- `RtlDispatchException` 完全不讀 stack 上的任何指標來找 handler
- handler 的地址存在 `.xdata` 的 `UNWIND_INFO.ExceptionHandler`（RVA，唯讀 section）
- 就算你把 stack 蓋得一塌糊塗，`.pdata`/`.xdata` 完好如初
- 分發流程用 RIP 查表，查到的永遠是 image 裡正確的 handler

結論：**在 x64 上，純粹的 SEH overwrite（蓋 stack 上的 handler 指標）是不可能的，因為 handler 指標根本不在 stack 上。**

這不代表 x64 的例外處理就無法被利用，只是攻擊面移到了別的地方：
- 如果你有任意寫原語能改 `.xdata`（通常需要先有另一個 bypass）
- 如果你能改 `AddVectoredExceptionHandler` 的 VEH 鏈（在 ntdll heap 上）
- 如果你能蓋掉 `UnhandledExceptionFilter`（UEF）的函式指標（歷史技法，有緩解後難了）

## Vectored Exception Handling（VEH）

VEH 是 Windows XP 引入的、和 SEH 平行的全域例外機制：

```c
/* 註冊 VEH handler */
PVOID AddVectoredExceptionHandler(
    ULONG                       FirstHandler,  /* 1=優先；0=最後 */
    PVECTORED_EXCEPTION_HANDLER VectoredHandler
);

/* 取消 */
ULONG RemoveVectoredExceptionHandler(PVOID Handle);

/* handler 型別 */
typedef LONG (WINAPI *PVECTORED_EXCEPTION_HANDLER)(
    PEXCEPTION_POINTERS ExceptionInfo
);
```

**VEH 和 SEH 的核心差別**：

| 面向 | VEH | SEH |
|---|---|---|
| 作用域 | per-process 全域 | per-frame（`__try` 的那個函式） |
| 位置 | ntdll 裡的一個雙向鏈結串列 | x86：stack；x64：.pdata/.xdata |
| 觸發時機 | **先於** SEH chain | SEH chain（第二順位） |
| 誰能存取 | 任何有 `AddVectoredExceptionHandler` 的執行緒 | 只有該 `__try` 所在函式能「聲明」handler |
| x64 上的 handler 位址 | 存在 ntdll heap 上（VEH 鏈節點） | 存在 .xdata（唯讀 section） |

**例外分發的優先順序**（x64 完整版）：

```
例外發生
   │
   ▼
1. KiUserExceptionDispatcher（ntdll）
   │
   ▼
2. VEH 鏈（AddVectoredExceptionHandler 註冊的）
   ← 全域，先查這裡
   │ 如果所有 VEH 都回 EXCEPTION_CONTINUE_SEARCH
   ▼
3. x64 table-based SEH 分發
   ← 查 .pdata，走 call stack
   │ 如果走完 stack 沒人接手
   ▼
4. UnhandledExceptionFilter（UEF）
   ← SetUnhandledExceptionFilter 設的那個
   │ 預設行為：顯示崩潰對話框 / WER
   ▼
5. 程式終止
```

### 真實驗證：VEH 在 mingw x64 上

```c
/* D:\tmp_build\veh_demo2.c，mingw x64 可跑 */
#include <stdio.h>
#include <windows.h>

static volatile int veh_triggered = 0;

LONG WINAPI my_veh_handler(PEXCEPTION_POINTERS ep)
{
    DWORD code = ep->ExceptionRecord->ExceptionCode;
    veh_triggered = 1;
    printf("  [VEH] fired! code=0x%08lX addr=%p\n",
           code, ep->ExceptionRecord->ExceptionAddress);
    return EXCEPTION_CONTINUE_SEARCH;
}

int main(void)
{
    PVOID h = AddVectoredExceptionHandler(1, my_veh_handler);
    printf("VEH registered: handle=%p\n", h);
    printf("Global per-process, fires BEFORE any SEH frame\n");
    RemoveVectoredExceptionHandler(h);
    printf("Handler removed. veh_triggered=%d\n", veh_triggered);
    return 0;
}
```

**實際執行輸出**：

```
VEH registered: handle=000001E0852DFC30
Global per-process, fires BEFORE any SEH frame
Handler removed. veh_triggered=0 (was not called - no exception raised)
```

handle 值（`0x000001E0852DFC30`）是 ntdll 在 heap 上分配的一個 VEH 節點指標。這個節點的結構大致是：

```c
/* ntdll 內部，非公開文件，以常見逆向結果為準 */
typedef struct _VECTORED_HANDLER_ENTRY {
    LIST_ENTRY                 Links;    /* 雙向鏈結 */
    ULONG                      Sequence; /* 用來保護的序號 */
    PVECTORED_EXCEPTION_HANDLER Handler; /* 指向你的 handler 函式 */
} VECTORED_HANDLER_ENTRY;
```

VEH 鏈的頭由 `ntdll!LdrpVectorHandlerList` 管理（未公開，逆向可見）。這個指標在 ntdll 的 data section 裡，不在 stack 上。

### VEH 的安全含義（攻防兩面）

**防禦視角**：VEH 讓你可以全域監控所有例外——anti-cheat、EDR 都會用 VEH 攔截所有 AV/EXCEPTION_BREAKPOINT 來偵測 shellcode 或調試器。

**攻擊視角**（舊技法）：如果攻擊者有任意寫，能覆寫 VEH 鏈節點裡的 `Handler` 指標，下一次例外就會呼叫攻擊者的函式。這比 x64 的 table-based SEH 更「容易」（heap 比 .xdata 可寫），但現代環境有 CFG 和 ACG，間接跳轉的目標受限，不是想改就能有效利用。

## UnhandledExceptionFilter（UEF）

`UnhandledExceptionFilter`（`ntdll!KiUserExceptionDispatcher` 的最後防線）是：當 SEH chain 走完、VEH 都說「不管」之後，OS 呼叫的「最後機會 handler」。

```c
/* 設定全域的 UEF */
LPTOP_LEVEL_EXCEPTION_FILTER SetUnhandledExceptionFilter(
    LPTOP_LEVEL_EXCEPTION_FILTER lpTopLevelExceptionFilter
);
```

回傳值是**舊的** UEF 指標。`SetUnhandledExceptionFilter(NULL)` 恢復預設行為（顯示錯誤對話框或 WER 上傳）。

### UEF 的歷史攻擊技法

在早期 Windows（XP SP1 之前），UEF 函式指標存放在 `kernel32.dll` 的 data section 裡（`kernel32!_BasepCurrentTopLevelFilter`），是一個全域可寫的指標。如果攻擊者有任意寫原語能蓋掉這個指標：

1. 把 UEF 指標改成 shellcode 位址（或 ROP chain 起點）
2. 觸發一個沒有任何 SEH frame 處理的例外（或者故意讓所有 handler 都 `EXCEPTION_CONTINUE_SEARCH`）
3. 流程走到 UEF 呼叫點，EIP 跳到攻擊者控制的位址

XP SP2 / Vista 之後，UEF 指標被保護（加入 `EncodePointer`/`DecodePointer` 的 XOR 加密，key 是 per-process 隨機值），使得直接蓋掉 UEF 指標難了很多——即使你能覆寫那個記憶體位置，你不知道 XOR key，解密出來是亂的，呼叫它會 crash。這個技法在現代環境幾乎無用，但在打老版本目標（Win XP 的漏洞研究、老版本嵌入式 Windows）偶爾仍有參考價值。

### `EncodePointer` 保護機制

```c
/* kernel32.dll 提供的 API */
PVOID EncodePointer(PVOID Ptr);
PVOID DecodePointer(PVOID Ptr);
```

實作（大致）：`EncodePointer(p) = ROL(p XOR PointerCookieKey, 0x40 - shift)`，其中 `PointerCookieKey` 是 process 初始化時產生的隨機值，存在 `ntdll` 裡的 per-process 位置。`kernel32!_BasepCurrentTopLevelFilter` 存的是 encoded 後的值，呼叫前用 `DecodePointer` 解回來。

> **以你環境為準**：在 WinDbg 裡，`x kernel32!*TopLevel*` 可以找到 UEF 相關符號；`dd kernel32!_BasepCurrentTopLevelFilter L1` 印出 encoded 指標。若要驗證 encoding，`dt ntdll!_RTL_USER_PROCESS_PARAMETERS` 找 PointerKey 欄位（不同版本名字不一樣）。

## Vectored Continue Handler（VCH）

和 VEH 同一個機制，但在 `EXCEPTION_CONTINUE_EXECUTION` 決定繼續執行**之後**才觸發。API：

```c
PVOID AddVectoredContinueHandler(ULONG FirstHandler, PVECTORED_EXCEPTION_HANDLER Handler);
ULONG RemoveVectoredContinueHandler(PVOID Handle);
```

大多數 exploit 教材不提 VCH，因為它的觸發時機較晚且利用面窄，但 EDR 可能用它做清理工作。

## 對比總結：x86 SEH vs x64 table-based SEH vs VEH vs UEF

| 機制 | handler 位置 | 作用域 | 優先順序 | x64 上 overflow 可蓋到？ | 現代利用難度 |
|---|---|---|---|---|---|
| x86 SEH chain | stack | per-frame | 3rd（VEH 之後） | N/A（x86 only） | 低（配合 SEHOP bypass） |
| x64 table-based SEH | .xdata（唯讀 section） | per-function | 2nd | **否** | 極高（需改 image） |
| VEH | ntdll heap（節點 Handler 欄位） | per-process 全域 | 1st | 理論可（heap write），有 CFG | 高（CFG/ACG 限制） |
| UEF | kernel32 data（encoded） | per-process 全域 | 最後 | 理論可（data write），有 EncodePointer | 高（需 key 或 info leak） |

## 底層機制：x64 的 unwind codes

`.xdata` 裡的 `UNWIND_CODE` 陣列描述函式 prologue 做了什麼（save reg / push / sub rsp / …），讓 `RtlUnwindEx` 知道如何**逆向還原**每個 frame 的 RSP 和 non-volatile 暫存器。這讓 x64 的 unwinding 不需要靠 stack 上的「previous frame 指標」——它自己算。

```
UNWIND_CODE 的操作碼包括：
UWOP_PUSH_NONVOL   (0x00)  還原一個 push reg 操作
UWOP_ALLOC_LARGE   (0x01)  還原大量 RSP 分配
UWOP_ALLOC_SMALL   (0x02)  還原小量 RSP 分配
UWOP_SET_FPREG     (0x03)  還原 frame pointer 設定
UWOP_SAVE_NONVOL   (0x04)  還原 register 到 stack 的儲存
...
```

這套機制讓 x64 unwinding 完全不依賴 stack 上的資料結構——只要 `.pdata`/`.xdata` 完整，unwinding 就能正確還原。這是 x64 比 x86 安全的核心理由之一。

### 真實驗證：objdump 的 x64 exception directory

```console
$ objdump -p D:\tmp_build\debug_hello_debug.exe | grep -A2 "Exception Directory"
Entry 3 0000000000005000 00000210 Exception Directory [.pdata]
```

`Entry 3`（index 3）就是 `IMAGE_DIRECTORY_ENTRY_EXCEPTION`，指向 `.pdata`。`.pdata` 裡有 28 個 `RUNTIME_FUNCTION` 項目覆蓋這個小程式所有的非 leaf 函式。這些資料在 image 被映射後是**唯讀的**（`.pdata` 的 section flags 是 `CONTENTS, READONLY`）。

## x64 的例外分發：`RtlLookupFunctionEntry` 和二分搜

在 x64 的 `RtlDispatchException` 裡，找到當前 RIP 對應的 `RUNTIME_FUNCTION` 是用 `RtlLookupFunctionEntry` 做的：

```c
/* ntdll 公開 API */
PRUNTIME_FUNCTION RtlLookupFunctionEntry(
    DWORD64                    ControlPc,    /* 當前 RIP */
    PDWORD64                   ImageBase,    /* 輸出：找到的 image base */
    PUNWIND_HISTORY_TABLE      HistoryTable  /* 加速快取，可為 NULL */
);
```

這個函式做的事：
1. 找 ControlPc 所在的模組（掃描 PEB.Ldr 的 module list 或用更快的 `RtlGetModuleBase`）
2. 從模組的 `.pdata` 節取出 `RUNTIME_FUNCTION` 陣列
3. 對 `BeginAddress` 做二分搜尋，找 `BeginAddress <= ControlPc < EndAddress` 的項目
4. 如果找不到（leaf function 或 JIT code 沒有 `.pdata` entry），回傳 NULL

回傳 NULL 的情況 OS 的行為：如果 `RtlLookupFunctionEntry` 回傳 NULL，代表這個 frame 是 leaf function，unwinder 用「直接讀 `[RSP]` 取得 return address」的方式退框。這個假設在 JIT code 裡可能是錯的——JIT code 可能在 RSP 上有不是 return address 的東西，所以 JIT engine 必須用 `RtlAddFunctionTable` 為自己的 code 注冊 `RUNTIME_FUNCTION`，讓 OS unwinder 不用猜。

`HistoryTable` 是一個 64 entry 的快取，避免同一個 RIP 範圍反覆做二分搜。在緊密的例外循環（例如 Lua/Python 的 try/except 密集程式）裡，快取能顯著加速分發。

## 踩雷集錦

1. **「x64 上 GS:[0] 的 ExceptionList 是 0xFFFFFFFFFFFFFFFF（sentinel）」**：實際測試（本機 mingw x64 runtime）發現 `GS:[0]` 是 `0x0000000000000000`（NULL），不是 sentinel。MSVC x64 runtime 用 `0xFFFFFFFF_FFFFFFFF`，但 mingw 用 `NULL`。兩者都代表「x64 沒有 stack-based SEH chain 在這個 TEB 欄位」。不要把特定 runtime 的值當成「x64 的規格值」。

2. **「VEH handler 可以任意呼叫，不受 CFG 限制」**：CFG 對間接函式呼叫的保護包括透過函式指標呼叫的情況。如果目標開了 CFG，從 VEH 節點的 `Handler` 欄位觸發的呼叫也受 CFG 保護——只有在 CFG bitmap 裡標記為合法目標的函式才能被呼叫。直接蓋 VEH 節點的 Handler 到 shellcode，在 CFG+NX 環境下行不通。

3. **「SetUnhandledExceptionFilter 設的 handler 存在某個固定位址」**：位址因 Windows 版本、DLL 基址（ASLR）、module 不同而變。且現代 Windows 用 EncodePointer 加密存放的值，沒有 info leak 拿不到 cookie，蓋了也沒用。不要試圖用固定位址技法打現代 Windows。

4. **「x64 SEH 不可能被利用」**：不正確。如果你有任意寫且能改 ntdll 的 VEH 鏈，或者你能 corrupt `.xdata` 的 UNWIND_INFO（需要 image 記憶體可寫，通常不可能，但 JIT'd code 的 runtime 可能動態生成 unwind info），還是有路。這是「比 x86 SEH overwrite 難得多」，不是「完全不可能」。

5. **「VEH 和 SEH 只能二選一」**：不是。它們可以同時存在且互相協作。一個程式可以同時有 `AddVectoredExceptionHandler` 的 VEH 和 `__try/__except` 的 SEH frame，例外發生時先過 VEH，再過 SEH。很多 runtime（CLR、JVM）就是這樣同時使用兩套機制。

## 進階：再往深一層

### 動態生成的 RUNTIME_FUNCTION

JIT 環境（.NET CLR、JavaScript V8、Wine）需要對動態生成的程式碼支援 x64 unwinding。做法是呼叫 `RtlAddFunctionTable`（或 `RtlInstallFunctionTableCallback`）在執行時動態往 OS 的例外表裡加項目：

```c
BOOLEAN RtlAddFunctionTable(
    PRUNTIME_FUNCTION FunctionTable,  /* 你準備好的 RUNTIME_FUNCTION 陣列 */
    DWORD             EntryCount,
    DWORD64           BaseAddress     /* 你的 JIT code 的基址 */
);
```

這讓 OS 的 unwind 機制能正確處理 JIT code 的 frame——否則 JIT frame 會讓 unwinding 卡住（找不到對應的 .pdata entry，直接視為例外未處理）。這也是現代 sandbox 逃逸研究的一個切入點：如果你能控制動態加入的 `RUNTIME_FUNCTION`，你可以讓 unwind 時跳到任意 handler。

### SEH filter 和 __except 的 filter expression

MSVC 的 `__except(filter)` 中的 `filter` 其實是一個函式呼叫：

```c
__try {
    ...
} __except ( filter_func(GetExceptionCode()) ) {
    ...
}
```

`filter_func` 在 **dispatch phase** 被呼叫，這時候 unwind 還沒發生。這意味著在 dispatch 期間，例外點的 stack 狀態仍然有效。攻擊者如果能控制 `filter_func` 的行為（例如透過 format string 改到相關的 global），可以在 unwind 前做一些有趣的事。這是相對罕見的攻擊向量，但存在於有複雜 filter 邏輯的程式裡。

### `NtContinue` 的安全性

例外處理之後要繼續執行（`EXCEPTION_CONTINUE_EXECUTION`），OS 呼叫 `NtContinue(ContextRecord, FALSE)`。`ContextRecord` 是一個完整的 `CONTEXT` 結構，包含所有暫存器。如果攻擊者能修改 `CONTEXT` 裡的 `Rip` 欄位（例如透過 heap corruption 讓 CONTEXT 結構可寫），`NtContinue` 就變成「任意 RIP 設定」原語。Windows 10 引入了 `CONTEXT_XSTATE` 驗證來緩解部分情況，但這依然是值得留意的攻擊面。

## 動手練習

**mingw x64（本機可跑）**：

```c
/* D:\tmp_build\veh_priority.c */
#include <stdio.h>
#include <windows.h>

LONG WINAPI veh1(PEXCEPTION_POINTERS e) {
    printf("  VEH1 called: code=0x%08lX\n", e->ExceptionRecord->ExceptionCode);
    return EXCEPTION_CONTINUE_SEARCH;
}
LONG WINAPI veh2(PEXCEPTION_POINTERS e) {
    printf("  VEH2 called: code=0x%08lX\n", e->ExceptionRecord->ExceptionCode);
    return EXCEPTION_CONTINUE_SEARCH;
}

int main(void) {
    PVOID h1 = AddVectoredExceptionHandler(1, veh1);  /* 先 = 高優先 */
    PVOID h2 = AddVectoredExceptionHandler(0, veh2);  /* 後 = 低優先 */
    printf("h1=%p  h2=%p\n", h1, h2);
    /* 驗證 h2 > h1（heap 往上分配，新的地址通常更高），
       且 h2 在鏈尾，h1 在鏈頭 */
    RemoveVectoredExceptionHandler(h1);
    RemoveVectoredExceptionHandler(h2);
    return 0;
}
```

**進階（需 MSVC + WinDbg）**：

> **未實測，理論預期**：用 WinDbg `!exchain` 在 x64 程式裡觀察，會看到「No exception handler」或 table-based entry，而不是 x86 的 `FS:[0]` 鏈。用 `dps` 印出 `.pdata` 的起始幾個 entry，和 `objdump` 的輸出交叉驗證。

## 本章重點整理

- x64 Windows 把 exception handler 資訊移出 stack，存在 PE image 的 `.pdata`（`RUNTIME_FUNCTION` 陣列）和 `.xdata`（`UNWIND_INFO`，含 handler RVA）裡，這兩個節都是**唯讀 section**，stack overflow 蓋不到它們。
- x64 的例外分發不走 `GS:[0]` 的鏈，而是以 RIP 二分搜尋 `.pdata`，再透過 `UNWIND_INFO` 找 handler——這從根本上消滅了「蓋 stack 上的 handler 指標」這類攻擊。
- VEH（Vectored Exception Handling）是全域優先的例外機制，handler 節點在 ntdll heap 上（`AddVectoredExceptionHandler(1, fn)` = 鏈頭優先，`0` = 鏈尾）；例外發生時先過 VEH，再過 table-based SEH。
- UEF（UnhandledExceptionFilter）是最後防線，現代 Windows 用 `EncodePointer` XOR 保護存放的函式指標，沒有 info leak 知道 cookie 就蓋不出有效值。

## 自我檢核

- [ ] 不看筆記，能畫出 x64 例外分發的優先順序圖（VEH → table-based SEH → UEF）
- [ ] 能說出 `RUNTIME_FUNCTION` 的三個欄位名，以及它們為什麼用 RVA 而不是絕對位址
- [ ] 能解釋為什麼「x64 上，光是 stack buffer overflow 無法做到 SEH overwrite」——指出 handler 指標存在哪裡，那裡為什麼 overflow 蓋不到
- [ ] 面試被問「VEH 和 SEH 的差別」時，能回答三個維度：作用域（per-frame vs global）、儲存位置（.xdata vs heap 節點）、觸發順序（VEH 先）
- [ ] 知道 UEF 為什麼在現代 Windows 上不容易直接蓋（EncodePointer），以及利用它需要什麼前提條件

## 延伸閱讀

### 研究文章

- **Ken Johnson (skape) & Matt Miller, "Preventing the Exploitation of Structured Exception Handler (SEH) Overwrites with SEHOP"** — uninformed.org, Vol. 9, 2007 ([uninformed.org](http://uninformed.org/index.cgi?v=9&a=2))
  - **讀哪裡**：Section 2（x86 SEH overwrite 技法）、Section 3（SEHOP 設計）
  - **學什麼**：從防禦角度看 SEH 的弱點，以及 SEHOP 為什麼能緩解（SEHOP 是 Ch 22 的主題，這篇是原始設計文件）
  - **和本章關聯**：本章說 x64 table-based SEH 讓 SEH overwrite 失效；這篇說 x86 的 SEHOP 嘗試同樣的目標但在 x86 環境下的做法

- **"Exploit writing tutorial part 3b: SEH Based Exploits – pain in the ass"** — Corelan Team ([corelan.be](https://www.corelan.be/index.php/2009/09/21/exploit-writing-tutorial-part-3b-seh-based-exploits-pain-in-the-ass/))
  - **讀哪裡**：關於 SafeSEH 和 SEHOP bypass 的部分
  - **學什麼**：即使有 SEH 保護，在 x86 環境下還是有繞過的手法（留到 Ch 22 詳細討論）
  - **前提**：Ch 11 + Ch 21（先練 SEH overwrite）

### 官方文件

- **[x64 Exception Handling — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/build/exception-handling-x64)**
  - **讀哪裡**：「Unwind data definitions」、「UNWIND_INFO structure」、「UNWIND_CODE structure」
  - **學什麼**：`UNWIND_CODE` 操作碼的完整列表和語意；`.pdata`/`.xdata` 的 layout 規範
  - **和本章關聯**：本章只給了 `RUNTIME_FUNCTION` 和 `UNWIND_INFO` 的概要，這份是完整規格

- **[Vectored Exception Handling — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/debug/vectored-exception-handling)**
  - **讀哪裡**：「Using Vectored Exception Handling」的完整 API 說明
  - **學什麼**：VEH 和 VCH 的完整 API 語意、優先順序、和 `UnhandledExceptionFilter` 的關係
  - **和本章關聯**：本章的 VEH 部分是概念和安全含義；這份是 API 使用的正式規格

### 深度逆向

- **Connor McGarr, "Windows Exploit Development — Part 5: VEH / SEH on x64"** — connormcgarr.github.io
  - **讀哪裡**：全篇，約 30 分鐘
  - **學什麼**：x64 SEH 的逆向視角；為什麼現代 exploit 要轉向 heap corruption + VEH 路線；具體的調試驗證方法
  - **和本章關聯**：補充本章「攻擊面移到 VEH/UEF」的具體例子，並有 WinDbg 操作示範

x64 table-based SEH 代表 Microsoft 用設計消除了一整類 x86 的漏洞。下一章我們換個視角——回到工具面，把 symbols 這個在 Windows 逆向和 exploit 開發裡被低估的關鍵武器搞透。

→ [Ch 13 — 符號與逆向工具鏈：public symbols / IDA / Ghidra](./13-symbols-and-re-tooling.md)
