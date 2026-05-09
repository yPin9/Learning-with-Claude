# Ch 22 — BSOD 崩潰分析

> 目標：能從 Memory Dump 分析出 BSOD 的根因，掌握最常見的 Bugcheck Code 和對應的分析方法。

## Dump 類型設定

在 VM 設定崩潰 Dump（控制台 → 進階系統設定 → 進階 → 啟動及修復）：

```
核心記憶體傾印（Kernel Memory Dump）— 推薦：只含核心記憶體，夠小夠快
完整記憶體傾印（Complete Memory Dump）— 用於特殊情況，很大
自動記憶體傾印（Automatic）— Windows 自動選擇
小型記憶體傾印（Minidump）— 最小，通常不夠用
```

Dump 存放在 `C:\Windows\MEMORY.DMP`（核心）或 `C:\Windows\Minidump\`（小型）。

## 分析流程

WinDbg Preview 直接拖 dump 進來，或 File → Open Crash Dump：

```
kd> !analyze -v
```

`!analyze -v` 是最重要的命令。它自動：
1. 顯示 Bugcheck Code 和參數
2. 嘗試找出故障的驅動
3. 顯示崩潰時的 Call Stack
4. 給出初步分析

## 常見 Bugcheck Code

### DRIVER_IRQL_NOT_LESS_OR_EQUAL（0xD1）

最常見的驅動 bug。在 DISPATCH_LEVEL 存取了分頁記憶體。

```
!analyze -v 輸出：
DRIVER_IRQL_NOT_LESS_OR_EQUAL (d1)
Arg1: ffffe000`12345678  ← 存取的地址
Arg2: 0000000000000002   ← IRQL（2 = DISPATCH_LEVEL）
Arg3: 0000000000000000   ← 0 = 讀；1 = 寫
Arg4: fffff800`deadbeef  ← 造成問題的代碼地址

FOLLOWUP_IP: 
MyDriver+0x1234
fffff800`deadbeef: mov rax,[rcx]  ← 存取了 Paged Pool

分析：代碼在 DISPATCH_LEVEL 解引用了一個分頁記憶體的指針
修復：把被存取的結構移到 NonPagedPoolNx，或確保存取時 IRQL < 2
```

### PAGE_FAULT_IN_NONPAGED_AREA（0x50）

在不該有 Page Fault 的地方觸發了 Page Fault。

常見原因：
- 存取已釋放的記憶體（Use-After-Free）
- Stack 溢出（核心棧只有 12KB）
- 存取無效指針

```
KERNEL_STACK_OVERFLOW 是 0x50 的一個特化版本，
!analyze -v 後可以看到 Stack 幾乎全部是 0xXXXX 的填充值
```

### SYSTEM_SERVICE_EXCEPTION（0x3B）

系統服務（通常是核心函式）處理時發生例外。常見於：
- 呼叫了一個 API，傳了無效參數
- 存取了無效指針

```
!analyze -v 後找 EXCEPTION_CODE 和 EXCEPTION_RECORD：
kd> .exr -1          ← 顯示最後一個例外記錄
kd> .cxr <addr>      ← 切換到例外發生時的 Context Record（看正確的 call stack）
kd> kb               ← 例外發生時的 call stack
```

### MULTIPLE_IRP_COMPLETE_REQUESTS（0x44）

對同一個 IRP 呼叫了 `IoCompleteRequest` 超過一次。立刻找到哪個 dispatch routine 或 completion routine 做了 double-complete。

### ATTEMPTED_WRITE_TO_READONLY_MEMORY（0xBE）

嘗試寫入唯讀記憶體。常見於：
- 直接修改 Code Section（Text Segment）
- 嘗試 Patch 受 PatchGuard 保護的結構

### BUGCODE_USB_DRIVER / SYSTEM_THREAD_EXCEPTION_NOT_HANDLED

通常有更具體的第一個 Bugcheck 參數，指向觸發例外的 NTSTATUS 碼。

## 實戰分析流程

```
1. kd> !analyze -v
   → 看 FOLLOWUP_IP 和 MODULE_NAME 找到問題驅動
   → 看 STACK_TEXT 追蹤 call stack

2. kd> .trap <trap frame address>  ← 切換到崩潰時的暫存器狀態
   → 看暫存器找出具體是哪個指針無效

3. kd> dt nt!_EPROCESS rcx   ← 用暫存器值解析結構（如果 rcx 是 EPROCESS）

4. kd> !pool rcx              ← 看這個地址是什麼 Pool 分配的

5. kd> ln <address>           ← 找最近的符號（"nearest symbol"）
```

## 常用分析命令

```
!analyze -v          ← 自動分析（必用）
.trap <addr>         ← 切換到 Trap Frame
.exr -1              ← 最後一個例外記錄
.cxr <addr>          ← 切換到例外 Context Record
kb                   ← call stack
kd> ln <addr>        ← 找這個地址對應的符號
!pool <addr>         ← 分析 Pool 塊
!address <addr>      ← 地址屬性（是否有效、是哪個映射）
```

## 常見模式：Driver Verifier 觸發的崩潰

Driver Verifier（Ch 23）會在驅動 bug 觸發 BSOD 前刻意 BSOD，讓你更早發現問題。

這些 BSOD 的 Bugcheck Code 以 `WDM_` 開頭：
- `WDM_VIOLATION（0x10D）`：Verifier 捉到的各種 WDM 違規

```
!analyze -v
→ 看 FOLLOWUP_MODULE 找到哪個驅動的哪行代碼違規
→ Verifier 通常會給具體的錯誤描述（如 "Freeing a pool block that is still locked"）
```

## 自建 Crash Dump 分析

讓驅動主動觸發 crash（用於測試 dump 分析）：

```c
// 強制 BSOD（測試用）
KeBugCheckEx(
    0xDEAD,          // Bugcheck Code（自定義）
    (ULONG_PTR)"MyDriver fault",  // Arg1（字串指針）
    0, 0, 0);
```

## 自我檢核

- [ ] 設定 VM 的 Dump 類型（Kernel Memory Dump 是日常用途的最佳選擇）
- [ ] `!analyze -v` 是 Dump 分析的第一個命令
- [ ] `0xD1`（DRIVER_IRQL_NOT_LESS_OR_EQUAL）= 在 DISPATCH_LEVEL 存取分頁記憶體
- [ ] `0x50`（PAGE_FAULT_IN_NONPAGED_AREA）= UAF、Stack 溢出、無效指針
- [ ] `.trap` / `.cxr` 切換到崩潰時的執行環境，再用 `kb` 看真實 call stack
- [ ] `!pool` 檢查地址的 Pool 來源，判斷是否是 UAF

→ [Ch 23 Driver Verifier](./23-driver-verifier.md)
