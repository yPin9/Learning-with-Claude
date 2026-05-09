# Ch 25 — Token、Privilege 與 ACL

> 目標：理解 Windows 安全模型的核心（Token、Privilege、SID/ACL），掌握驅動如何執行存取控制和特權操作。

## 安全主體與 SID

Windows 用 **SID（Security Identifier）** 識別安全主體（用戶、群組、機器）：

```
S-1-5-21-XXXXXXXX-XXXXXXXX-XXXXXXXX-1000  ← 本機用戶
S-1-5-18                                   ← SYSTEM
S-1-5-32-544                               ← Administrators 群組
S-1-1-0                                    ← Everyone
S-1-5-11                                   ← Authenticated Users
```

## Token：進程的安全憑證

每個進程（EPROCESS）有一個 Token（ACCESS_TOKEN），記錄「這個進程以誰的身份執行」：

```c
// Token 的關鍵欄位
typedef struct _TOKEN {
    TOKEN_SOURCE    TokenSource;    // 來自哪個認證程序
    LUID            AuthenticationId;  // Logon Session ID
    
    ULONG           UserAndGroupCount;
    PSID_AND_ATTRIBUTES UserAndGroups;  // 用戶 SID + 所有群組 SID
    
    ULONG           PrivilegeCount;
    PLUID_AND_ATTRIBUTES Privileges;   // 特權列表（Enabled/Disabled）
    
    PSID            PrimaryGroup;   // 主要群組
    PACL            DefaultDacl;    // 新物件的預設 DACL
    
    SE_TOKEN_TYPE   TokenType;      // Primary（進程）或 Impersonation（執行緒）
    SECURITY_IMPERSONATION_LEVEL ImpersonationLevel;
    
    TOKEN_FLAGS     TokenFlags;     // 是否 Elevated、是否 UAC 受限
} TOKEN;
```

## Privilege（特權）

特權是超越 ACL 的能力，授予特定操作的權限：

| 特權 | LUID | 意義 |
|------|------|------|
| SeDebugPrivilege | 20 | 調試其他進程（`OpenProcess` 可訪問任何進程）|
| SeLoadDriverPrivilege | 10 | 載入/卸載驅動 |
| SeTcbPrivilege | 7 | 充當 OS 的一部分（強大）|
| SeImpersonatePrivilege | 29 | 模擬其他用戶（Token 模擬）|
| SeCreateTokenPrivilege | 2 | 建立任意 Token（最強）|
| SeAssignPrimaryTokenPrivilege | 3 | 替換進程 Token |

特權預設是 Disabled（存在但未啟用），需要程式主動啟用：

```c
// 用戶態啟用特權
TOKEN_PRIVILEGES tp;
tp.PrivilegeCount = 1;
LookupPrivilegeValue(NULL, SE_DEBUG_NAME, &tp.Privileges[0].Luid);
tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
AdjustTokenPrivileges(hToken, FALSE, &tp, 0, NULL, NULL);
```

## 驅動的安全操作

### 在核心存取其他進程

驅動預設在 SYSTEM 安全上下文執行，可以開啟任何進程：

```c
// 核心開啟其他進程（不經過 SeDebugPrivilege 檢查）
PEPROCESS targetProcess;
PsLookupProcessByProcessId((HANDLE)pid, &targetProcess);

// 在目標進程上下文中操作
KAPC_STATE apcState;
KeStackAttachProcess(targetProcess, &apcState);
// 現在在目標進程的地址空間中
// ...
KeUnstackDetachProcess(&apcState);
ObDereferenceObject(targetProcess);
```

### 從驅動建立的物件的安全描述符

```c
// 建立只有 SYSTEM 和 Admin 能存取的 Device
UNICODE_STRING sddl = RTL_CONSTANT_STRING(L"D:P(A;;GA;;;SY)(A;;GA;;;BA)");
// D:P       = DACL, Protected（不繼承）
// (A;;GA;;;SY) = Allow, Generic All, SYSTEM
// (A;;GA;;;BA) = Allow, Generic All, Builtin Admins

WdmlibIoCreateDeviceSecure(DriverObject, ..., &sddl, &GUID_MY_DEVICE, &deviceObject);
```

### ObRegisterCallbacks：保護你的進程 Handle

EDR 常用：阻止外部進程用 PROCESS_TERMINATE 打開目標進程：

```c
OB_CALLBACK_REGISTRATION cbReg = {0};
OB_OPERATION_REGISTRATION opReg = {0};

opReg.ObjectType = PsProcessType;
opReg.Operations = OB_OPERATION_HANDLE_CREATE | OB_OPERATION_HANDLE_DUPLICATE;
opReg.PreOperation = OnPreOpenProcess;
opReg.PostOperation = NULL;

cbReg.Version = OB_FLT_REGISTRATION_VERSION;
cbReg.OperationRegistrationCount = 1;
cbReg.OperationRegistration = &opReg;

ObRegisterCallbacks(&cbReg, &gCbHandle);

// Callback：攔截 OpenProcess，移除 PROCESS_TERMINATE 權限
OB_PREOP_CALLBACK_STATUS OnPreOpenProcess(
    PVOID RegistrationContext, POB_PRE_OPERATION_INFORMATION OpInfo)
{
    PEPROCESS targetProcess = (PEPROCESS)OpInfo->Object;
    
    // 如果是保護進程
    if (IsProtected(targetProcess)) {
        // 從請求的 Access 中移除危險權限
        ACCESS_MASK desired = OpInfo->Parameters->CreateHandleInformation.DesiredAccess;
        desired &= ~(PROCESS_TERMINATE | PROCESS_VM_WRITE | PROCESS_VM_READ);
        OpInfo->Parameters->CreateHandleInformation.DesiredAccess = desired;
    }
    
    return OB_PREOP_SUCCESS;
}
```

## ACL 和 Security Descriptor

Security Descriptor 包含 DACL（決定存取）和 SACL（決定審計）：

```
DACL（Discretionary ACL）：
  ACE 1: Allow SYSTEM = Full Control（0x1F01FF）
  ACE 2: Allow Administrators = Full Control
  ACE 3: Allow Users = Read/Execute（0x20089）
  ACE 4: Deny Guest = Any Access
```

Security Reference Monitor 在每次 `ObOpenObjectByName` 時：
1. 取得請求者的 Token（SID + Group + Privileges）
2. 取得物件的 Security Descriptor（DACL）
3. 逐條比對 ACE，決定是否允許

驅動用 `SeAccessCheck` 做核心層的存取控制：

```c
NTSTATUS CheckAccess(SECURITY_DESCRIPTOR* sd, ACCESS_MASK desired)
{
    BOOLEAN granted;
    ACCESS_MASK grantedAccess;
    NTSTATUS accessStatus;
    PRIVILEGE_SET privilegeSet;
    ULONG privilegeSetLength = sizeof(PRIVILEGE_SET);
    
    PACCESS_TOKEN token = PsReferencePrimaryToken(PsGetCurrentProcess());
    
    SeAccessCheck(
        sd,
        &SecurityContext,  // 包含 Token
        TRUE,              // SubjectContextLocked
        desired,
        0,                 // PreviouslyGrantedAccess
        NULL,              // Privileges
        &gGenericMapping,
        UserMode,
        &grantedAccess,
        &accessStatus);
    
    PsDereferencePrimaryToken(token);
    return accessStatus;
}
```

## 自我檢核

- [ ] SID 識別安全主體；SYSTEM = S-1-5-18；Admins Group = S-1-5-32-544
- [ ] Token 包含 UserSID + 群組 SID + 特權列表
- [ ] 特權（Privilege）預設 Disabled，需要 `AdjustTokenPrivileges` 啟用
- [ ] `SeDebugPrivilege` 讓用戶能 OpenProcess 任何進程；驅動預設有 SYSTEM 等級
- [ ] `ObRegisterCallbacks` 攔截 Handle 建立，可移除危險 Access Mask（EDR 常用）
- [ ] `WdmlibIoCreateDeviceSecure` 用 SDDL 字串設定 Device 的存取控制

→ [練習 B：WinDbg 崩潰分析](./practice-b-bsod-analysis.md)
