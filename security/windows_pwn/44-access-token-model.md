# Ch 44 — access token 模型：SID / privileges / integrity level

> **目標**：徹底理解 Windows 存取控制的核心資料結構——access token。學完你能說清楚「拿到 SYSTEM token 為何等於提權」，能用 `whoami` 和 Python ctypes 把自己行程的 token 翻出來看，並對照 Linux uid/gid/capabilities 說出 Windows 的設計差異。

## 為什麼需要這個？

Linux 的存取控制你很熟：一個行程有 `uid`/`gid`/`euid`/`egid`，加上 capabilities。要提權就是讓 `euid → 0`，或拿到某個 capability（`CAP_SYS_ADMIN`、`CAP_NET_ADMIN`...）。這套模型扁平、簡單，記憶體裡的 `task_struct` 幾個欄位就搞定。

Windows 的模型複雜得多。登入後每個行程都帶著一個 **access token（存取權杖）**，裡面塞了：誰在跑這個程式（SID）、它屬於哪些群組（Groups）、它有哪些特殊特權（Privileges）、它在系統信任階層的哪一層（Integrity Level）。Windows 的核心元件（LSASS、SRM）每次做存取判斷都查這個 token——你能不能開某個檔案、能不能 debug 別人的行程、能不能繞過 UAC，全從 token 讀。

**為什麼一定要懂這個？**

Part 7 講的提權，機制就是「換掉你自己的 token」或「偷一個權限更高的 token 去做事」。不懂 token 的結構，你就不知道「偷什麼」，也看不懂 CVE 的提權原語在改什麼。

## 先建立直覺

把 Linux 和 Windows 的存取控制心智模型並排：

```
Linux                              Windows
─────────────────────────────      ─────────────────────────────────────
task_struct.cred:                  _TOKEN:
  uid / gid                 →        User SID
  euid / egid               →        (Groups SIDs + attributes)
  supplementary groups      →        Groups[] (含 SID + Attributes)
  capabilities (bitmask)    →        Privileges[] (LUID + Attributes)
  (沒有 IL 概念)             →        IntegrityLevel (Mandatory Policy)
                                     TokenType (Primary / Impersonation)
                                     ImpersonationLevel
```

Linux 的 credential 是 kernel 裡的幾個整數；Windows 的 token 是 **Object Manager 管理的核心物件**，行程透過 handle 持有它，可以複製、可以偽裝、可以限制。這是最根本的架構差異。

從高空看 token 如何連到行程：

```
  EPROCESS (行程核心物件)
  ┌────────────────────────────────┐
  │  UniqueProcessId               │
  │  Peb → PEB (userland)          │
  │  Token → ┌────────────────┐   │  ← primary token
  │           │  _TOKEN        │   │
  │           │  ├ UserSID     │   │
  │           │  ├ Groups[]    │   │
  │           │  ├ Privileges[]│   │
  │           │  ├ IL SID      │   │
  │           │  └ TokenType   │   │
  │           └────────────────┘   │
  │  ThreadListHead                │
  │    ↓                           │
  │  ETHREAD ┌────────────────┐   │
  │           │ ClientSecurity │   │  ← impersonation token（可選，覆蓋 primary）
  │           └────────────────┘   │
  └────────────────────────────────┘
```

行程（process）持有 **primary token**；個別執行緒（thread）可以額外持有 **impersonation token** 暫時偽裝成另一個身份。服務端程式（如 named pipe server）靠這個機制替客戶端做事而不需要真的切換行程。

## SID：Security Identifier

SID 是 Windows 身份識別的原子單位，對應 Linux 的 `uid`/`gid`（但比整數豐富得多）。格式：

```
S-{revision}-{identifier-authority}-{subauthority1}-{subauthority2}-...
```

常見範例：

| SID | 意義 | Linux 類比 |
|---|---|---|
| `S-1-5-18` | Local System（SYSTEM 帳號）| uid 0（root）|
| `S-1-5-19` | Local Service | uid 65534（nobody 類）|
| `S-1-5-20` | Network Service | uid 65533 |
| `S-1-1-0` | Everyone（所有人）| 沒有直接對應 |
| `S-1-5-32-544` | BUILTIN\Administrators | wheel / sudo group |
| `S-1-5-21-{domain}-{RID}` | 特定網域/本機使用者 | 自訂 uid |
| `S-1-16-12288` | High Integrity Level | 沒有直接對應 |

`S-1-5-18`（SYSTEM）是提權的終點目標——持有這個 SID 的 token 對本機幾乎無限制。

### 用 whoami 看自己的 SID（實跑）

```powershell
whoami /user
```

實際輸出（本機 PowerShell）：

```
USER INFORMATION
----------------
User Name         SID
================= ================================================
desktop-xxx\ypp   S-1-5-21-3012345678-1234567890-987654321-1001
```

`S-1-5-21-{3 個子權威}` 是本機帳號的標準格式，最後的 `-1001` 是 RID（Relative Identifier），從 1000 起算的本機使用者。`-500` 是內建 Administrator，`-501` 是 Guest。

## Groups 與 Attributes

token 裡的 Groups 不只是「你屬於哪些群組」，每個群組 SID 還帶一個 **Attributes 旗標**，決定這個群組在這次登入裡的狀態：

| Attribute | 十六進位值 | 意義 |
|---|---|---|
| `SE_GROUP_ENABLED` | `0x00000004` | 群組目前有效（參與存取判斷）|
| `SE_GROUP_MANDATORY` | `0x00000001` | 無法 disable，始終有效 |
| `SE_GROUP_USE_FOR_DENY_ONLY` | `0x00000010` | 只用來拒絕（不授予）|
| `SE_GROUP_INTEGRITY` | `0x00000020` | Integrity Level 用的群組 |
| `SE_GROUP_LOGON_ID` | `0xC0000000` | 代表登入 session 的 SID |

「DENY ONLY」狀態很微妙：這個群組的存在只能拒絕存取，不能授予——Restricted Token（`CreateRestrictedToken`）和沙箱設計常用這招限制行程。

用 `whoami /groups` 看完整清單（實跑）：

```
GROUP INFORMATION
-----------------
Group Name                                         Type             SID          Attributes
================================================== ================ ============ ==================================================
Everyone                                           Well-known group S-1-1-0      Mandatory group, Enabled by default, Enabled group
BUILTIN\Administrators                             Alias            S-1-5-32-544 Group used for deny only
BUILTIN\Performance Log Users                      Alias            S-1-5-32-559 Mandatory group, Enabled by default, Enabled group
NT AUTHORITY\Authenticated Users                   Well-known group S-1-5-11     Mandatory group, Enabled by default, Enabled group
...
Mandatory Label\Medium Mandatory Level             Label            S-1-16-8192  Mandatory group, Enabled by default, Enabled group
```

注意最後一行：`S-1-16-8192` 是 **Medium Integrity Level** 的 SID。Integrity Level 就是用 Group SID 的特殊命名空間（`S-1-16-*`）實作的。

也注意 `BUILTIN\Administrators` 顯示 `Group used for deny only`——說明目前的 token 是 **Medium IL 分裂 token**（UAC 開著時的標準狀態）。Administrators 群組存在但被標 deny-only，代表你有 admin 身份但這個 token 沒有完整 admin 權限——這就是 UAC 的核心機制，Ch 45 細講。

## Privileges：特殊特權

Privileges 是 Windows token 裡最直接影響提權的部分，對應 Linux 的 **capabilities**（`CAP_SYS_PTRACE`、`CAP_NET_BIND_SERVICE`...）但設計不同。

每個 privilege 有：
- **LUID（Locally Unique Identifier）**：64 位元，開機期間穩定，重開機可能改變
- **Name**：人可讀的名稱（`SeDebugPrivilege`）
- **Attributes**：目前狀態（Disabled / Enabled / Enabled by default）

關鍵：privilege 可以**存在但被 disabled**。行程必須呼叫 `AdjustTokenPrivileges` **主動 enable** 才能使用。這是 Linux capabilities 沒有的概念。

### 提權關鍵 Privileges 一覽

| Privilege | 常見持有者 | 提權用途 |
|---|---|---|
| `SeDebugPrivilege` | Administrators（High IL 後）| 可 `OpenProcess` 任意行程（含 SYSTEM），複製 token |
| `SeImpersonatePrivilege` | IIS / SQL Server 服務帳號 | 偽裝任意 token（Potato 家族核心）|
| `SeAssignPrimaryTokenPrivilege` | SYSTEM | 可替行程換 primary token |
| `SeBackupPrivilege` | Backup Operators 群組 | 無視 DACL 讀任意檔案 |
| `SeRestorePrivilege` | Backup Operators 群組 | 無視 DACL 寫任意檔案 |
| `SeTcbPrivilege` | SYSTEM | 「是作業系統的一部分」，可建立任意 token |
| `SeLoadDriverPrivilege` | Administrators | 可載入 kernel driver（BYOVD 入口）|
| `SeTakeOwnershipPrivilege` | Administrators | 可取得任意物件所有權 |
| `SeCreateTokenPrivilege` | 極少行程持有 | 可從頭建立任意 token |

Linux 對照：`SeDebugPrivilege` ≈ `CAP_SYS_PTRACE`；`SeImpersonatePrivilege` ≈ 沒有直接對應（Linux 服務帳號不能這樣偽裝 uid）；`SeLoadDriverPrivilege` ≈ `CAP_SYS_MODULE`。

### 用 whoami /priv 看自己的 privileges（實跑）

```powershell
whoami /priv
```

普通 Medium IL 使用者帳號實際輸出（本機）：

```
PRIVILEGES INFORMATION
----------------------

Privilege Name                Description                          State
============================= ==================================== ========
SeShutdownPrivilege           Shut down the system                 Disabled
SeChangeNotifyPrivilege       Bypass traverse checking             Enabled
SeUndockPrivilege             Remove computer from docking station Disabled
SeIncreaseWorkingSetPrivilege Increase a process working set       Disabled
SeTimeZonePrivilege           Change the time zone                 Disabled
```

`SeDebugPrivilege` 不在列表裡——這是一般使用者帳號的正常狀態。如果你是 Administrators 群組且 token 已提權（High IL），才會看到：

```
SeDebugPrivilege              Debug programs                       Disabled
```

還是 `Disabled`——因為 privilege 需要行程主動 enable。`whoami /priv` 顯示的是「有沒有」，不是「現在用不用」。

### Python 實跑：用 ctypes 讀自己的 token privileges

```python
# 真實可跑（Python 3.12 + ctypes，不需要 MSVC）
import ctypes
import ctypes.wintypes as wt

TOKEN_QUERY = 0x0008
TokenPrivileges = 3  # TOKEN_INFORMATION_CLASS

ADVAPI32 = ctypes.WinDLL("advapi32")
KERNEL32 = ctypes.WinDLL("kernel32")

class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]

class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Luid", LUID),
        ("Attributes", wt.DWORD),
    ]

SE_PRIVILEGE_ENABLED_BY_DEFAULT = 0x00000001
SE_PRIVILEGE_ENABLED            = 0x00000002

def get_privilege_name(luid):
    buf = ctypes.create_unicode_buffer(256)
    size = wt.DWORD(256)
    ADVAPI32.LookupPrivilegeNameW(None, ctypes.byref(luid), buf, ctypes.byref(size))
    return buf.value

# 拿當前行程的 token
hProc = KERNEL32.GetCurrentProcess()
hToken = wt.HANDLE()
ok = ADVAPI32.OpenProcessToken(hProc, TOKEN_QUERY, ctypes.byref(hToken))
if not ok:
    raise ctypes.WinError()

# 第一次呼叫取得需要的 buffer 大小
needed = wt.DWORD()
ADVAPI32.GetTokenInformation(hToken, TokenPrivileges, None, 0, ctypes.byref(needed))

buf = ctypes.create_string_buffer(needed.value)
ok = ADVAPI32.GetTokenInformation(
    hToken, TokenPrivileges, buf, needed.value, ctypes.byref(needed)
)
if not ok:
    raise ctypes.WinError()

# TOKEN_PRIVILEGES 頭 4 bytes 是 PrivilegeCount
count = wt.DWORD.from_buffer(buf).value
print(f"Privilege count: {count}")

# 走變長陣列（從 offset 4 開始，每個 LUID_AND_ATTRIBUTES = 12 bytes on x64）
item_size = ctypes.sizeof(LUID_AND_ATTRIBUTES)
for i in range(count):
    offset = ctypes.sizeof(wt.DWORD) + i * item_size
    la = LUID_AND_ATTRIBUTES.from_buffer(buf, offset)
    name = get_privilege_name(la.Luid)
    attrs = []
    if la.Attributes & SE_PRIVILEGE_ENABLED_BY_DEFAULT:
        attrs.append("default_enabled")
    if la.Attributes & SE_PRIVILEGE_ENABLED:
        attrs.append("ENABLED")
    else:
        attrs.append("disabled")
    print(f"  {name:<45} {', '.join(attrs)}")

KERNEL32.CloseHandle(hToken)
```

執行後你會看到和 `whoami /priv` 一致的清單，但多了 LUID 可以直接對應後續的 `AdjustTokenPrivileges` 呼叫。

## Integrity Level（整合層級，IL）

這是 Windows Vista 引入的概念，Linux **沒有對應物**（最接近的是 SELinux/AppArmor 的 label，但那是強制存取控制政策，不同層次）。

IL 實作上是 token Groups 裡一個特殊的 **Mandatory Label SID**（`S-1-16-{level}`），代表行程在信任階層的位置：

```
Integrity Level   SID               等級值    典型使用者 / 情境
─────────────────────────────────────────────────────────────────────
Untrusted         S-1-16-0          0x0000    受限最嚴的行程
Low               S-1-16-4096       0x1000    瀏覽器 renderer（IE EPM / Chrome / Edge sandbox）
Medium            S-1-16-8192       0x2000    普通使用者行程（預設）
Medium+           S-1-16-8448       0x2100    （少用，UIAccess）
High              S-1-16-12288      0x3000    提權後的 Administrators
System            S-1-16-16384      0x4000    Windows 服務、SYSTEM 帳號行程
Protected Process S-1-16-20480      0x5000    防毒軟體核心、LSASS（PPL）
```

Mandatory Integrity Control（MIC）的核心規則是 **No Write Up**：

```
   System IL  ────────────────── Windows 服務、SYSTEM 行程
      ↑  不可寫上
   High IL    ────────────────── 已提權的 admin cmd/PowerShell
      ↑  不可寫上
   Medium IL  ────────────────── 普通 Explorer、應用程式（你現在在這）
      ↑  不可寫上
   Low IL     ────────────────── 瀏覽器 renderer / sandbox
      ↑  不可寫上
   Untrusted  ────────────────── 完全隔離的執行環境
```

**No Write Up** 的意思：Low IL 行程就算被 exploit 拿下，它寫不了你 Desktop 上的檔案（Medium IL 物件）——這就是沙箱的本質。沙箱逃脫的核心任務是「從 Low IL 拿到 Medium 或以上的 token」。

> 「No Read Up」和「No Execute Down」預設**不強制**——預設只有寫的方向被 MIC 卡住，讀取需要額外設定 SACL 才會限制。這是常見誤解：Low IL 行程能讀很多 Medium IL 的東西，只是寫不進去。

## Primary Token vs Impersonation Token

這個區分 Linux 沒有——它是 Windows 服務/RPC/pipe 設計的核心。

**Primary token**：行程的「身份證」。`CreateProcess` 時繼承或指定，代表「這個行程是誰跑的」。SRM 在行程沒有 impersonation token 時，用 primary token 做存取判斷。

**Impersonation token**：執行緒層級的臨時身份。Server 行程（如 named pipe server）接到 client 連線後，呼叫 `ImpersonateNamedPipeClient` 拿到 client 的 impersonation token，暫時以 client 身份做存取判斷，結束後 `RevertToSelf` 恢復。

Impersonation level 有四種：

| Level | SECURITY_IMPERSONATION_LEVEL | 能做什麼 |
|---|---|---|
| Anonymous | 0 | server 拿不到 client 身份資訊 |
| Identification | 1 | server 能查 client 身份但不能用來存取資源 |
| Impersonation | 2 | server 能以 client 身份存取**本機**資源 |
| Delegation | 3 | server 能以 client 身份存取**遠端**資源（需 Kerberos 委派）|

**提權觀點**：如果你的行程（服務帳號）有 `SeImpersonatePrivilege`，而你能讓 SYSTEM 行程連到你的 named pipe，你就能拿 SYSTEM 的 impersonation token——這是 Potato 家族的核心原語（Ch 46 細講）。

## DACL / ACE 與 token 的關係

存取判斷（你能不能開一個檔案）發生在 SRM（Security Reference Monitor，核心元件）：

```
  行程呼叫 OpenFile("C:\secret.txt", GENERIC_READ)
         ↓
  SRM 取得：
    ① 行程/執行緒的 access token（你是誰、有什麼權）
    ② 目標物件的 Security Descriptor（SD）
         └─ Discretionary ACL（DACL）
              ├─ ACE: ALLOW  Administrators  READ | WRITE
              ├─ ACE: ALLOW  Users           READ
              └─ ACE: DENY   Everyone        DELETE
         ↓
  比對 token 裡的 SID 清單 vs DACL 裡的 ACE
  ① 先掃 DENY ACE：有符合且要求的存取位元被拒 → 直接拒絕
  ② 再掃 ALLOW ACE：符合 SID 且涵蓋所有要求位元 → 允許
  ③ 沒有 ACE 符合要求 → 拒絕（預設 deny-all）
```

為什麼「拿到 SYSTEM token = 提權」？因為 `S-1-5-18`（SYSTEM）幾乎是所有系統物件 DACL 裡最高權限的 ACE 持有者，且 SYSTEM 還帶著 `SeTcbPrivilege`、`SeDebugPrivilege` 等一整批特殊特權。一旦你的行程用 SYSTEM token 做存取判斷，幾乎所有本機資源都對你敞開。

## 底層機制：_TOKEN 核心結構

> **未實測，理論預期**——WinDbg 裝好後用 `dt nt!_TOKEN` 驗證欄位。下列偏移以 Windows 11 x64 常見版本為參考，實際以除錯器輸出為準。

```
_TOKEN（部分欄位，x64 偏移，版本相依）
  +0x000  TokenSource               : _TOKEN_SOURCE    （8 bytes 來源識別）
  +0x010  TokenId                   : _LUID            （token 唯一 ID）
  +0x018  AuthenticationId          : _LUID            （登入 session）
  +0x028  TokenType                 : _TOKEN_TYPE      （Primary=1 / Impersonation=2）
  +0x02c  ImpersonationLevel        : _SECURITY_IMPERSONATION_LEVEL
  +0x030  TokenFlags                : ULONG
  +0x040  SessionId                 : ULONG
  +0x048  UserAndGroupCount         : ULONG
  +0x04c  RestrictedSidCount        : ULONG
  +0x050  PrivilegeCount            : ULONG
  +0x058  UserAndGroups             : ptr → SID_AND_ATTRIBUTES[]
  +0x060  RestrictedSids            : ptr → SID_AND_ATTRIBUTES[]
  +0x068  Privileges                : ptr → LUID_AND_ATTRIBUTES[]
  ...
  +0x0c8  IntegrityLevelIndex       : ULONG  （Groups[] 裡 IL SID 的索引）
```

Kernel exploit 常見的 token stealing 就是定位另一個行程的 `EPROCESS`，讀出它的 `Token` 欄位（`EX_FAST_REF` 型別，低幾 bit 是引用計數，要清掉再用），把這個 token 指標寫進你自己的 `EPROCESS.Token`——Ch 46 和 `windows_kernel_driver` 課要講的 kernel 層原語就是這招。

## 對比與取捨

| 面向 | Linux | Windows | 關鍵差異 |
|---|---|---|---|
| 身份識別 | uid / gid（整數）| SID（可變長度，結構化）| Windows SID 含層次，能表達更多語意 |
| 特殊特權 | capabilities（bitmask，一次設定）| Privileges（LUID，可 enable/disable）| Windows privilege 有明確的 enable 動作 |
| 信任分層 | 無（SELinux/AppArmor 是 MAC 另一層）| Integrity Level | Win 沙箱（瀏覽器）靠 Low IL 隔離 |
| 行程身份物件 | task_struct.cred（kernel inline 整數）| _TOKEN（獨立核心物件，可 handle 複製）| Windows token 可 duplicate 給另一個行程 |
| 執行緒獨立身份 | 無 | Impersonation token（執行緒層）| Server 行程可暫時切換身份 |
| 提權目標 | euid → 0 | token SID → S-1-5-18（SYSTEM）| 概念相似，實作完全不同 |

## 踩雷集錦

1. **「Privilege Enabled = 正在使用」**：錯。`whoami /priv` 顯示 `Enabled` 是「已開啟待用」，`Disabled` 是「有這個 privilege 但沒開」。行程要用某個特權要先呼叫 `AdjustTokenPrivileges` 把它打開，否則 kernel 不承認。

2. **「Administrators 群組 = High IL」**：錯。UAC 開啟時，admin 帳號登入後拿到的是 **Medium IL 的 split token**，Groups 裡的 Administrators SID 被標成 `deny-only`。要真正拿 High IL 需要 consent.exe 出現、使用者點 Yes——Ch 45 的主題。

3. **「拿掉 Administrators 群組就安全」**：不夠。`SeBackupPrivilege` 可以無視 DACL 讀所有檔案；`SeImpersonatePrivilege` 可以偽裝任意 token——這些與群組 SID 無關。特權的威脅要分開評估。

4. **「Integrity Level 只有兩種：普通和 System」**：不對。Low IL 是瀏覽器沙箱（讀得了但寫不了你 Desktop 的檔案）、Untrusted 更嚴格、Protected Process 是防毒/LSASS 用的——每一層的攻擊面完全不同，沙箱逃脫的目標通常是 Low → Medium，不一定要打到 System。

5. **「複製了 token 就等於提權完成」**：複製 token 只是第一步。如果你只能拿到 `Identification` level 的 impersonation token，那個 token 只能查身份，做不了存取操作。要 `Impersonation` 或 `Delegation` level 才能真正作為那個身份存取資源。

## 進階：再往深一層

**Restricted Token**（`CreateRestrictedToken` API）：從現有 token 建一個「閹割版」，可以把部分群組標成 deny-only、拿掉部分 privileges、加上限制 SID 清單（額外限制能存取什麼）。Chrome 和 Edge 的 renderer sandbox 用 Restricted Token + Low IL 組合拿到雙重限制。真正理解瀏覽器逃脫，要先知道 Restricted Token 怎麼運作。

**Token Filtering（Split Token）**：UAC 在 admin 登入時做的事——從完整的 admin token 過濾出一個受限版本，同時儲存兩個 token（Linked Token，`_TOKEN.ParentTokenId` 互相連結）。用 `OpenProcessToken` + `TokenLinkedToken` info class 可以查詢對應的另一個 token——這是 UAC bypass 研究的起點。

**面試題**：「一個有 `SeImpersonatePrivilege` 但沒有 `SeDebugPrivilege` 的服務帳號，能提權到 SYSTEM 嗎？」——能。`SeImpersonatePrivilege` 足夠，你可以偽裝 SYSTEM 的 token，不需要直接 `OpenProcess` 那個行程。這正是 Potato 系列的攻擊情境。

## 動手練習

**目標**：用 Python ctypes 查詢自己行程的 Integrity Level，印出 IL SID 字串和對應的 IL 名稱。

步驟提示：
1. `OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, ...)` — 拿 token handle
2. `GetTokenInformation(hToken, TokenIntegrityLevel, ...)` — `TOKEN_INFORMATION_CLASS = 25`
3. 結果是 `TOKEN_MANDATORY_LABEL`（SID_AND_ATTRIBUTES 結構）
4. `ConvertSidToStringSidW` 把 SID 轉字串，比對上方表格確認你在哪一層
5. 嘗試在「以系統管理員執行的 PowerShell」裡跑同樣的腳本，比較輸出差異（SID 從 `S-1-16-8192` 變成 `S-1-16-12288`）

延伸：用 `whoami /priv` 比較 Medium IL 和 High IL 下的 privilege 清單差異，重點觀察 `SeDebugPrivilege` 和 `SeImpersonatePrivilege` 的出現時機。

## 本章重點整理

- Access token 是 Windows 存取控制的核心物件，掛在行程（primary）或執行緒（impersonation），比 Linux `task_struct.cred` 豐富且可複製傳遞。
- SID 是身份識別的原子單位；Privileges 是特殊特權（注意 enabled/disabled 的區別）；Integrity Level 用 Mandatory Label SID 實作信任分層（沙箱的基礎，No Write Up 規則）。
- `SeDebugPrivilege` 讓你 `OpenProcess` 任意行程；`SeImpersonatePrivilege` 讓你偽裝任意 token——這兩個是提權路徑的關鍵入口。
- 「拿到 SYSTEM token」= 拿到 `S-1-5-18` SID + 一整批頂級特權，概念上等於 Linux 的 `euid=0` 但機制完全不同。

## 自我檢核

- [ ] 不看筆記，能畫出 `EPROCESS → _TOKEN → SID / Privileges / IL` 的層次關係
- [ ] 能解釋為什麼 admin 帳號開啟的普通 cmd 是 Medium IL，而不是 High IL
- [ ] 被問「`SeDebugPrivilege` 能做什麼」——能說出具體的 Win32 API 操作路徑（OpenProcess → DuplicateHandle / OpenProcessToken → ...）
- [ ] 能說出 Impersonation token 和 Primary token 的差異，以及服務端為什麼需要前者
- [ ] 能用 `whoami /priv` + `whoami /groups` 輸出，指出哪些 SID 和 privilege 是高風險的

## 延伸閱讀

### 官方文件

- **[Access Tokens — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/secauthz/access-tokens)**
  - **讀哪裡**：「Access Token Components」與「Primary and Impersonation Tokens」兩節
  - **學什麼**：官方對 token 各欄位的語意定義，含 `RestrictedSids`、`SessionId` 等本章略過的部分
  - **和本章關聯**：本章 SID/Privileges/IL 概念的一手來源；遇到 API 行為不符預期時回來查

- **[Mandatory Integrity Control — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control)**
  - **讀哪裡**：整篇不長，直接全讀；特別注意「Integrity Levels and ACLs」一節
  - **學什麼**：MIC 的 No Write Up 規則的精確定義，以及 SACL 裡 Mandatory Label 的欄位結構
  - **前提**：本章 IL 概念

### 研究 / 部落格

- **[Calling Local Windows RPC Servers from .NET — James Forshaw（Project Zero Blog）](https://googleprojectzero.blogspot.com/2019/12/calling-local-windows-rpc-servers-from.html)**
  - **讀哪裡**：文中關於 token handle 傳遞和 impersonation level 操縱的段落
  - **學什麼**：Token 在 RPC 偽裝鏈中的傳遞方式，以及哪些邊界是可操縱的
  - **和本章關聯**：本章 impersonation token 概念在 RPC 場景下的延伸；是 Potato 家族技術的學術背景

- **[Windows Access Control — The Definitive Guide（itm4n's blog）](https://itm4n.github.io/windows-access-control/)**
  - **讀哪裡**：SID 格式、DACL/ACE 比對流程、token 結構三個段落
  - **學什麼**：比官方文件更有利用者視角的存取控制說明；itm4n 是 PrintSpoofer/PrivescCheck 作者，觀點直接
  - **前提**：本章全讀，再去深化 DACL 細節

### 書籍

- **《Windows Internals, 7th Edition》Part 1，Chapter 7（Security）** — Yosifovich, Ionescu, Russinovich, Solomon
  - **讀哪裡**：「Security Access Tokens」和「Security Reference Monitor」兩節（約 60 頁）
  - **學什麼**：`_TOKEN` 核心結構的完整欄位解析，以及 SRM 做存取判斷的完整流程（本章是這份材料的快速入門版）
  - **前提**：本章讀完再去，效率最高

→ [Ch 45 — UAC 與 integrity level 繞過概觀](./45-uac-integrity-level.md)
