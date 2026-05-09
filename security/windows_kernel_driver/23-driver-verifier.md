# Ch 23 — Driver Verifier

> 目標：理解 Driver Verifier 的機制，能設定適當的 Verifier 選項，用它捕捉驅動的記憶體錯誤和 IRQL 違規。

## 為什麼需要 Driver Verifier

驅動 bug 有些不會立刻崩潰，而是悄悄污染記憶體，等很久以後在完全無關的地方才崩潰。

Driver Verifier 在驅動做出錯誤行為的**那一刻**立刻 BSOD，告訴你是哪個驅動、哪行代碼、錯了什麼。

等同於 AddressSanitizer + UBSan 的 Windows 驅動版。

## 啟用 Driver Verifier

在 **Target VM** 上（管理員 CMD）：

```cmd
# 啟用對特定驅動的驗證（推薦：只驗你自己的驅動）
verifier /standard /driver MyDriver.sys

# 啟用多個驅動
verifier /standard /driver MyDriver.sys AnotherDriver.sys

# 查看當前設定
verifier /query

# 清除所有設定
verifier /reset
```

設定後**必須重開機**才生效。

## Verifier 的主要檢測項目

### Standard Flags（最常用的基礎組）

**1. Pool Memory Checks**
- 分配時在前後加魔術數 guard bytes
- 釋放後把記憶體填充 `0xDD`（Dangling Dereference 偵測）
- 如果讀到 `0xDDDDDDDD` = 讀了已釋放的記憶體

**2. IRQL Checks**
- 追蹤每個 API 呼叫時的 IRQL
- 如果在 DISPATCH_LEVEL 呼叫了需要 PASSIVE_LEVEL 的 API → 立刻 BSOD

**3. Thread Priority Checks**
- 捕捉在 IRQL 高時持有 Mutex 等問題

**4. Miscellaneous Checks**
- IRP 結構的完整性（例如偵測 double-complete）

### 進階選項

```cmd
# Special Pool：分配在邊界，越界立刻崩潰（類似 Valgrind 的 Red Zone）
verifier /standard /flags 0x9 /driver MyDriver.sys

# 0x1  = Pool Memory Checks
# 0x2  = Force IRQL Checking
# 0x8  = Pool Integrity Checking
# 0x10 = I/O Verification（更嚴格的 IRP 檢查）
# 0x80 = DMA Verification
# 0x200 = Security Checks（NULL 頁面映射等）
```

**Special Pool（0x8）** 特別有用：
把你的驅動分配的 Pool 放在頁面邊界，前或後緊接著 Guard Page。越界一個 byte 就立刻 Page Fault = BSOD，而不是悄悄污染別人的記憶體。

## Verifier GUI

```cmd
verifier    ← 不帶參數開啟 GUI
```

GUI 模式有更方便的勾選界面，可以看目前狀態和統計。

## 讀懂 Verifier 觸發的 BSOD

Driver Verifier 觸發時 Bugcheck 是 `DRIVER_VERIFIER_DETECTED_VIOLATION（0xC4）`：

```
DRIVER_VERIFIER_DETECTED_VIOLATION (c4)
Arg1: 0000000000000062  ← Violation Code
Arg2: ffffe000`12345678 ← 違規的地址或參數
Arg3: ...
Arg4: ...

FOLLOWUP_MODULE: MyDriver
```

Arg1（Violation Code）的常見值：

| Code | 意義 |
|------|------|
| 0x00 | NULL pointer dereference |
| 0x01 | 非分頁池越界 |
| 0x10 | IRQL 不正確時呼叫 Wait 函式 |
| 0x14 | 在 IRQL > PASSIVE_LEVEL 釋放 Pool |
| 0x51 | 釋放已釋放的記憶體（Double Free）|
| 0x52 | MDL 相關違規 |
| 0x62 | 取消了一個不是 pending 的 IRP |

完整列表在 MSDN 的 Bug Check 0xC4 頁面。

## 實戰：讓 Verifier 抓到 Bug

故意寫一個有 Bug 的驅動：

```c
// 故意的 Double Free Bug
NTSTATUS DispatchWrite(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PVOID buf = ExAllocatePoolWithTag(NonPagedPoolNx, 1024, 'Test');
    if (!buf) { /* ... */ }
    
    ExFreePoolWithTag(buf, 'Test');
    ExFreePoolWithTag(buf, 'Test');  // Double Free！
    
    Irp->IoStatus.Status = STATUS_SUCCESS;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}
```

啟用 Verifier 後，執行到 Double Free 時立刻 `0xC4 Violation Code = 0x51`，並在 WinDbg 中：

```
kd> !analyze -v
DRIVER_VERIFIER_DETECTED_VIOLATION (c4)
Arg1: 0000000000000051  ← 0x51 = 釋放已釋放的記憶體
...

FOLLOWUP_MODULE: MyDriver
MyDriver!DispatchWrite+0x..
```

## 性能影響

Driver Verifier 有顯著的性能影響（Special Pool 尤其重）。**只在測試 VM 上開啟**，不要在生產機器上開。

開啟 Verifier 後 VM 跑得比正常慢很多，這是正常的。

## 自我檢核

- [ ] `verifier /standard /driver MyDriver.sys` 啟用基礎驗證，必須重開機
- [ ] Verifier 捉到的 Bugcheck = `0xC4 DRIVER_VERIFIER_DETECTED_VIOLATION`
- [ ] Special Pool：把分配放在頁面邊界，越界立刻崩潰（精確定位 buffer overflow）
- [ ] Pool 填充 `0xDD`：釋放後的記憶體被填充，讀到 `0xDD` = Use-After-Free
- [ ] IRQL Checking：在錯誤 IRQL 呼叫 API 立刻崩潰，而不是悄悄出問題

→ [Ch 24 DSE + PatchGuard](./24-dse-patchguard.md)
