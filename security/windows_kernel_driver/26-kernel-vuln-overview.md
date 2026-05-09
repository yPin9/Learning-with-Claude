# Ch 26 — Windows 核心漏洞概覽

> 目標：理解 Windows 核心漏洞的主要分類，熟悉各類型的攻擊面和代表性 CVE，建立後續章節的知識框架。

## 核心漏洞的價值

用戶態漏洞（RCE）：從外部執行任意代碼，但通常權限有限（WWW 服務帳號、受限沙盒）。

核心漏洞（LPE - Local Privilege Escalation）：從低權限本地用戶提升到 SYSTEM。

組合起來：`RCE in browser → renderer sandbox escape → LPE via kernel bug → SYSTEM → 持久化`

LPE 的典型售價（漏洞黑市）：$500K – $2M（Windows 現代緩解下）。

## 漏洞分類

### 1. IOCTL 漏洞

最常見的攻擊面。第三方驅動（防毒、遊戲反作弊、硬體工具）暴露 IOCTL 介面，處理用戶輸入時有 bug。

子類型：
- **任意讀/寫**：驅動沒有驗證緩衝區地址，讓攻擊者讀寫任意核心地址
- **緩衝區溢出**：`InputBufferLength` 沒有上限，用戶可以傳超長數據覆蓋 Pool
- **Type Confusion**：不同 IOCTL code 共用同一個緩衝區但解析為不同結構，偏移不同造成越界

代表性 CVE：
- **CVE-2021-21551**（Dell BIOS Driver）：IOCTL 任意讀寫，在野外被 APT 利用
- **CVE-2021-31727**（Netfilter）：任意地址寫，公開 PoC 廣泛使用

### 2. 有符號整數越界

```c
// 典型錯誤
NTSTATUS DispatchIoctl(PDEVICE_OBJECT DevObj, PIRP Irp)
{
    LONG offset = *(LONG*)Irp->AssociatedIrp.SystemBuffer;  // 用戶控制
    PVOID target = (PUCHAR)gBaseBuffer + offset;
    
    // 如果 offset 是負數 → target 指向 gBaseBuffer 之前的記憶體
    // 如果 offset 很大 → target 指向 gBaseBuffer 之後的記憶體
    *(ULONG*)target = 0xdeadbeef;  // 任意寫！
}
```

### 3. 核心 UAF（Use-After-Free）

核心物件釋放後仍有指針指向它：
- 惡意軟體 spray Pool（放置自己的數據在相同位置）
- 通過 UAF 指針存取 Sprayed 數據 = 控制核心行為

代表性 CVE：
- **CVE-2020-17087**（cng.sys）：UAF，Google Project Zero 發現
- **CVE-2021-1732**（win32k.sys）：UAF，APT 組織在野利用

### 4. 整數溢出

```c
// 缺乏上限檢查
ULONG count = userInput->count;  // 用戶控制
ULONG size = count * sizeof(ENTRY);  // 如果 count = 0xFFFFFFFF → size 溢出
PVOID buf = ExAllocatePoolWithTag(NonPagedPool, size, 'Test');
// size 溢出後可能是 0 或很小的值
// 後續操作 count 個 ENTRY，寫入超過分配的記憶體範圍
```

### 5. 條件競爭（Race Condition）

雙取競爭（TOCTOU - Time of Check to Time of Use）：

```c
// 驅動 Check 時
ULONG size = *(PULONG)userBuf;  // = 100（合法）

// 用戶在 Check 和 Use 之間改了 userBuf
*(PULONG)userBuf = 0xFFFFFFFF;

// 驅動 Use 時
// size 沒有重新讀，但 buf 的內容已被修改
PVOID p = ExAllocatePoolWithTag(Pool, size, 'T');
// 後續使用 p 時按 0xFFFFFFFF 的 size 操作 → 溢出
```

代表性：**CVE-2019-0803**（win32k.sys Race Condition）。

### 6. Pool 溢出

溢出核心池分配，覆蓋相鄰 Pool 塊（Pool 利用見 Ch 29）：

- **Windows 10 21H1 以前**：Pool 不隔離，可以溢出覆蓋特定的核心物件
- **Windows 10 21H1 以後**：Segment Heap，Pool 隔離，利用更複雜

## 常見的攻擊目標（利用成功後）

### 1. Token 竊取（最常見）

找 SYSTEM 進程（PID 4）的 Token，複製到當前進程（見 Ch 5 詳述）。

```
任意寫漏洞 → 寫 EPROCESS.Token = SYSTEM 進程的 Token → SYSTEM 進程建立
```

### 2. HalDispatchTable Hook

`nt!HalDispatchTable` 是個函式指針表，一些觸發點（如 `NtQueryIntervalProfile`）會呼叫其中的指針。

```
任意寫漏洞 → 覆蓋 HalDispatchTable[1] → NtQueryIntervalProfile() → 執行 shellcode
```

這是舊版 exploit 的常用技術，現代緩解（SMEP/SMAP）讓它複雜很多（見 Ch 30）。

### 3. 覆蓋函式指針

任意寫 → 覆蓋某個核心回調函式指針 → 觸發時執行 shellcode。

目標：DRIVER_OBJECT.MajorFunction、各種 Object Type 的 callback。

## 練習資源：HEVD（HackSys Extreme Vulnerable Driver）

HEVD 是專門為練習 kernel exploit 設計的漏洞驅動，包含各種故意有 Bug 的 IOCTL：

```
https://github.com/hacksysteam/HackSysExtremeVulnerableDriver

包含：
- Stack Buffer Overflow
- Pool Buffer Overflow  
- Integer Overflow
- NULL Pointer Dereference
- Use-After-Free
- Double Fetch（TOCTOU）
- Type Confusion
- ...
```

在 VM 上安裝 HEVD，搭配 Ch 27–29 的技術，用真實的受控漏洞練習 exploit 開發。

## 自我檢核

- [ ] IOCTL 漏洞是最大的攻擊面：任意讀/寫、緩衝區溢出、Type Confusion
- [ ] 整數溢出 → size 計算錯誤 → Pool 分配太小 → 後續越界寫
- [ ] TOCTOU：Check 和 Use 之間有視窗，用多執行緒利用
- [ ] 利用成功後最常見的 payload：Token 竊取（寫 EPROCESS.Token）
- [ ] HEVD 是安全的本地練習環境，涵蓋主要漏洞類型

→ [Ch 27 IOCTL 漏洞](./27-ioctl-vulnerabilities.md)
