# Ch 1 — 為什麼學 Windows Kernel Driver

> 目標：理解 Windows kernel driver 的定位、和 Linux 核心模組的關鍵差異，以及 WDM / KMDF / UMDF 的選擇邏輯。

## 什麼是 Kernel Driver

Windows 把執行環境分成兩層：

```
Ring 3（用戶模式）: 應用程式、DLL、Win32 API
─────────────────────────────────────────────
Ring 0（核心模式）: NT Kernel、Driver、HAL
```

**Kernel Driver**（副檔名 `.sys`）是跑在 Ring 0 的代碼。它能直接：

- 存取任意實體記憶體和 I/O 端口
- 存取硬體暫存器
- 攔截任何系統呼叫
- 修改所有進程的記憶體空間
- 繞過幾乎所有用戶態的安全機制

代價是：**一個 NULL dereference 就讓整台機器 BSOD**。

## 誰住在 Kernel

```
ntoskrnl.exe  — NT 核心本體（排程、記憶體、物件管理）
hal.dll       — Hardware Abstraction Layer（中斷、DMA、時鐘）
win32k.sys    — 視窗系統、GDI（歷史上最多漏洞的驅動）
ndis.sys      — 網路驅動介面
fltMgr.sys    — 檔案系統過濾管理器
ntfs.sys      — NTFS 檔案系統驅動
─────────────────────────────────────────────────────
你的驅動.sys  — 也住在這裡
```

沒有記憶體保護邊界。你的代碼可以讀寫 `ntoskrnl.exe` 的任意地址（假設你知道位址）。

## 為什麼安全研究者要學

**攻擊面**：Windows kernel driver 是 LPE（Local Privilege Escalation）漏洞的最大來源之一。

典型攻擊路徑：
```
用戶態 app（低權限）
  → 對某個第三方驅動發 IOCTL
  → 驅動有任意寫漏洞
  → 覆蓋 SYSTEM 進程的 Token
  → 拿到 SYSTEM shell
```

知名案例：
- CVE-2021-21551：Dell BIOS Driver — 任意核心讀寫 → LPE
- CVE-2019-0803：win32k.sys — 條件競爭 → LPE
- BYOVD：把合法簽名的漏洞驅動帶進目標機器，利用它做任意寫

**防守面**：EDR（Endpoint Detection and Response）的核心傳感器都是驅動：

```
Minifilter Driver   → 攔截檔案 I/O
Process Callbacks   → 監控進程建立/注入
ObCallbacks         → 監控 Handle 開啟（偵測 process hollowing）
WFP Callouts        → 網路封包過濾
ETW Providers       → 行為遙測
```

看不懂驅動就看不懂 EDR，也不知道怎麼繞它。

## 和 Linux 核心模組的比較

有 Linux kernel 背景的人，這張表可以快速定位：

| 概念 | Linux | Windows |
|------|-------|---------|
| 模組入口 | `module_init()` / `module_exit()` | `DriverEntry()` / `DriverUnload()` |
| I/O 請求 | `file_operations` 結構體 | IRP（I/O Request Packet） |
| 記憶體分配 | `kmalloc()` / `vmalloc()` | `ExAllocatePoolWithTag()` |
| 中斷上下文 | `in_interrupt()` 檢查 | IRQL（Interrupt Request Level）系統 |
| 同步 | spinlock / mutex / semaphore | 基本相同，但名稱不同 |
| 調試 | `printk()` + `/proc/kmsg` | `DbgPrint()` + WinDbg KD |
| 崩潰 | Kernel Panic | BSOD（Blue Screen of Death） |
| 設備模型 | sysfs / kobject | Device Object / Device Stack |
| 過濾驅動 | `register_kprobe` / LSM | Minifilter / Filter Driver |

最大的差異：**Windows 有 IRQL 系統**（下一章詳述），這是所有新手犯錯最多的地方。

## Driver 的三種開發框架

### WDM（Windows Driver Model）

最底層。直接操作 I/O Manager 的 IRP。

```c
// WDM 風格：手動處理所有 IRP
NTSTATUS DispatchRead(PDEVICE_OBJECT DeviceObject, PIRP Irp) {
    // 手動填 IoStatus、呼叫 IoCompleteRequest
}
```

優點：完全可控，能做任何事
缺點：繁瑣，PnP 和電源管理要自己實作幾百行 boilerplate

**用途**：學習底層原理；安全研究（理解 IRP 結構）；legacy 驅動維護

### KMDF（Kernel-Mode Driver Framework）

微軟推薦的現代框架。WDF 框架幫你處理 PnP、電源、IRP 生命週期。

```c
// KMDF 風格：只寫業務邏輯
EVT_WDF_IO_QUEUE_IO_READ EvtIoRead;
void EvtIoRead(WDFQUEUE Queue, WDFREQUEST Request, size_t Length) {
    // WDF 幫你管記憶體和 IRP 完成
}
```

優點：大幅減少 boilerplate；自動處理 PnP/Power；更不容易出錯
缺點：多一層抽象，debug 稍複雜

**用途**：生產用驅動；硬體裝置驅動；這門課後面的實作

### UMDF（User-Mode Driver Framework）

驅動跑在用戶態（沙盒進程），崩潰不會 BSOD。

**用途**：不需要直接存取硬體的設備（USB、HID 等）

## 本課程的路線

```
Ch 6：先學 WDM —— 理解 IRP 的真實面貌
Ch 7 以後：轉 KMDF —— 實際開發用更少的代碼做更多的事
安全章節（Ch 26–35）：回到 WDM raw structure 視角
```

WDM 不是過時技術——它是 KMDF 的底層，也是漏洞研究者必須懂的東西。絕大多數有漏洞的第三方驅動都用 WDM。

## 第一個心態調整：BSOD 是正常的

跑 Linux kernel 模組：`insmod` 失敗，dmesg 報錯，繼續。

跑 Windows kernel driver：任何一個 NULL pointer dereference → 整台機器藍屏 → 重開機 → 重新調試。

不要怕 BSOD。每個 Windows driver 工程師一天可以造成十幾次 BSOD。重點是**能從 dump 裡看出為什麼崩潰**。這是 Ch 22 的主題。

## 自我檢核

- [ ] 理解 kernel driver 跑在 Ring 0，能存取任意記憶體
- [ ] 知道安全研究者學 driver 的兩個方向：攻擊（IOCTL 漏洞/BYOVD）和防守（EDR/callback）
- [ ] Linux 核心模組 vs Windows kernel driver 的主要對應關係
- [ ] WDM / KMDF / UMDF 的使用場景
- [ ] BSOD 不是失敗，是調試的起點

→ [Ch 2 NT 核心架構全貌](./02-nt-architecture.md)
