# Ch 0 — 環境搭建：雙機調試 + WDK + WinDbg

> 目標：搭建一個能寫、能跑、能調試 Windows kernel driver 的完整環境。環境沒搭好，後面什麼都做不了。

## 為什麼需要雙機調試

Windows kernel driver 跑在 Ring 0。寫錯一行，整台機器藍屏（BSOD）。

你不可能在同一台機器上調試自己讓它崩潰的驅動。解法是**雙機調試（Two-Machine Debugging）**：

```
Host（開發機）                   Target（測試 VM）
─────────────────────            ─────────────────
Visual Studio + WDK              Windows 10/11 VM
WinDbg                    KD←──  已啟用 kernel debugging
編譯驅動 .sys                     載入驅動 .sys
```

Host 上的 WinDbg 通過 KD（Kernel Debugger）連線到 Target VM，可以在 Target 上設斷點、檢查記憶體、單步執行核心代碼。

## 1. 安裝開發工具（Host）

### Visual Studio 2022

1. 下載 Visual Studio 2022 Community（免費）
2. 安裝時勾選：**Desktop development with C++**
3. 安裝後先不要打開，接著裝 WDK

### Windows Driver Kit（WDK）

到 [docs.microsoft.com/windows-hardware/drivers/download-the-wdk](https://docs.microsoft.com/windows-hardware/drivers/download-the-wdk) 下載最新 WDK（配合 VS 2022）。

安裝順序很重要：
```
1. 安裝 Windows SDK（WDK 安裝程式會提示）
2. 安裝 WDK
3. WDK 安裝結束時會自動安裝 VS extension
```

驗證安裝成功：打開 VS 2022，新增專案，搜尋「Kernel Mode Driver」，應該看到 KMDF Driver、WDM Driver 等模板。

### WinDbg Preview

Microsoft Store 搜尋「WinDbg Preview」，免費安裝。比舊版 WinDbg 的介面好很多，支援時間旅行調試（Time Travel Debugging）。

## 2. 建立 Target VM

推薦 VMware Workstation，serial port 和 network KD 都穩定。Hyper-V 也可以但設定稍複雜。

### 建立 VM

- OS：Windows 10 22H2 或 Windows 11（選你要研究的目標版本）
- RAM：4GB 以上
- 磁碟：80GB
- 不要開啟 Secure Boot（後面裝驅動用 Test Signing，Secure Boot 會擋）

### 啟用 Test Signing Mode

在 VM 內以管理員執行 PowerShell：

```powershell
# 允許載入未簽名驅動
bcdedit /set testsigning on

# 驗證
bcdedit /enum
# 看到 testsigning Yes 就對了
```

重開機後桌面右下角會出現「Test Mode」浮水印，表示成功。

### 啟用 Kernel Debugging

**方案 A：Network KD（推薦，速度快）**

```powershell
# 在 VM 內執行
bcdedit /debug on
bcdedit /dbgsettings net hostip:192.168.x.x port:50000 key:1.2.3.4
# hostip = Host 機的 IP；port 任選 49152–65535；key 任填

# 重開機
```

VM 重開時會在啟動畫面等待 debugger 連線（等約 30 秒後繼續開機，debugger 可以隨後連）。

**方案 B：Serial KD（VMware 穩定，速度較慢）**

在 VM 設定加一條 Serial Port，類型選「Use named pipe」，pipe name `\\.\pipe\com_1`。

```powershell
# 在 VM 內執行
bcdedit /debug on
bcdedit /dbgsettings serial debugport:1 baudrate:115200
```

### 驗證調試連線（Host）

打開 WinDbg Preview，選 **Kernel Debug**：

- Network KD：填 Port 和 Key
- Serial KD：填 Pipe Name

出現 `Waiting to reconnect...` 後重啟 VM，看到 `Connected to Windows...` 就成功了。

在 WinDbg 按 **Break**（Ctrl+Break），VM 會凍住：

```
Break instruction exception - code 80000003
nt!DbgBreakPointWithStatus:
fffff800`12345678 cc              int     3
kd>
```

輸入 `g`（Go）讓 VM 繼續跑。

## 3. 第一個驅動：Hello, Kernel

### 建立 WDM Driver 專案

VS 2022 → 新增專案 → 搜尋「Empty WDM Driver」→ 命名 `HelloDriver`

**HelloDriver.c**：

```c
#include <ntddk.h>

DRIVER_UNLOAD DriverUnload;
void DriverUnload(PDRIVER_OBJECT DriverObject) {
    UNREFERENCED_PARAMETER(DriverObject);
    DbgPrint("[HelloDriver] Unloaded\n");
}

NTSTATUS DriverEntry(PDRIVER_OBJECT DriverObject, PUNICODE_STRING RegistryPath) {
    UNREFERENCED_PARAMETER(RegistryPath);

    DbgPrint("[HelloDriver] Hello from kernel!\n");
    DriverObject->DriverUnload = DriverUnload;

    return STATUS_SUCCESS;
}
```

**編譯設定**：
- Configuration：Debug / x64
- 確認 Target Platform Version 和你的 WDK 版本一致

Build → 成功後在 `x64\Debug\` 下有 `HelloDriver.sys`。

### 部署到 Target VM

把 `HelloDriver.sys` 複製到 VM（拖拉到 VMware 窗口、共享資料夾、或網路傳輸）。

在 VM 以管理員執行 PowerShell 或 `sc.exe`：

```powershell
# 安裝驅動服務
sc create HelloDriver type= kernel binPath= C:\HelloDriver.sys

# 啟動
sc start HelloDriver

# 停止
sc stop HelloDriver

# 刪除
sc delete HelloDriver
```

### 觀察 DbgPrint 輸出

在 Host 的 WinDbg 連上 VM 後，執行：

```
kd> ed nt!Kd_DEFAULT_Mask 0xFFFFFFFF
```

這會開啟所有調試輸出的過濾。

現在在 VM 執行 `sc start HelloDriver`，WinDbg 會出現：

```
[HelloDriver] Hello from kernel!
```

成功！你已經在 Windows kernel 執行了自己的代碼。

## 4. 必裝工具

| 工具 | 用途 | 來源 |
|------|------|------|
| WinDbg Preview | 核心調試 | Microsoft Store |
| DebugView | 觀察 DbgPrint（不需要 KD）| Sysinternals |
| Process Hacker | 觀察驅動、Handle、Token | processhacker.github.io |
| OSR Driver Loader | 方便的驅動載入 GUI | osronline.com |
| x64dbg | 用戶態調試 | x64dbg.com |
| PE-bear | PE/驅動結構分析 | GitHub |

### Sysinternals DebugView 替代 WinDbg

如果你只是想看 `DbgPrint` 輸出，不需要 KD：

在 VM 上以管理員開啟 DebugView → 勾選 **Capture Kernel**。

安裝啟動驅動後，DebugView 就會顯示輸出。但這個方法**沒有辦法設斷點**，只適合快速 printf-debug。

## 5. 常見問題排查

**問題：`sc start` 回報 1275（驅動未簽名）**
```powershell
# 確認 Test Signing 是否開啟
bcdedit /enum | findstr testsigning
# 確認是 Yes；如果不是，重新執行 bcdedit /set testsigning on 並重開機
```

**問題：WinDbg 連不上 VM**
- Network KD：檢查防火牆（VM 和 Host 都要），確認 IP 正確
- Serial KD：確認 pipe name 一致（VM 設定和 bcdedit 都是 `com_1`）
- 確認 VM 的 bcdedit /debug 是 Yes

**問題：驅動載入後立刻 BSOD**
- 這是正常的！記錄下 bugcheck code
- 在 WinDbg 設置 `sxe cc`（catch exceptions）可以在崩潰前攔截

## 自我檢核

- [ ] Host 裝好 WDK + VS 2022，能新增 KMDF Driver 專案
- [ ] VM 啟用 Test Signing + Kernel Debugging
- [ ] WinDbg 能成功連上 VM，Break/g 能正常工作
- [ ] HelloDriver.sys 能載入、DbgPrint 出現在 WinDbg 輸出中
- [ ] 知道 DebugView 是 DbgPrint 的快速替代方案（無法設斷點）

→ [Ch 1 為什麼學 Windows Kernel Driver](./01-why-windows-kernel-driver.md)
