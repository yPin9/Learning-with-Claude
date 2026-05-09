# Ch 21 — WinDbg 核心調試

> 目標：掌握 WinDbg 核心調試的日常命令，能在雙機環境中追蹤驅動執行、檢視核心結構、分析崩潰。

## 連線後的第一步

雙機調試連上 VM 後，輸入 `Ctrl+Break`（Break）讓 VM 凍住：

```
kd> .sympath SRV*C:\symbols*https://msdl.microsoft.com/download/symbols
kd> .reload                    ← 載入符號
kd> g                          ← 繼續執行
```

符號文件（.pdb）讓 WinDbg 知道結構欄位名稱、函式名。沒有符號只有原始地址，很難看。

## 基本執行控制

```
g            ← 繼續執行（Go）
p            ← 單步執行（Step Over，不進函式）
t            ← 單步執行（Step Into，進函式）
gu           ← 執行到函式返回（Step Out）
Ctrl+Break   ← 中斷 VM 執行
```

## 斷點

```
bp nt!NtCreateFile              ← 在函式入口設斷點
bp MyDriver!DispatchRead        ← 在你的驅動設斷點
bu MyDriver!DriverEntry         ← Unresolved（模組還沒載入時也能設）
bm nt!Io*                       ← 萬用字元（所有 nt!Io 開頭的函式）

bl                              ← 列出所有斷點
bd 0                            ← 停用斷點 0
be 0                            ← 啟用斷點 0
bc 0                            ← 刪除斷點 0
bc *                            ← 刪除所有斷點

ba r4 0xFFFF800012345678        ← 記憶體存取斷點（讀寫 4 bytes 時觸發）
ba w8 nt!MmPageFaultCount       ← 寫入 8 bytes 時觸發
```

## 查看記憶體和結構

```
dt nt!_EPROCESS                 ← 顯示結構定義（不帶地址）
dt nt!_EPROCESS ffffe000`12345678  ← 顯示特定地址的結構內容
dt nt!_EPROCESS -r ffffe000`...   ← 遞迴展開子結構

dps ffffe000`12345678 L10       ← 顯示 16 個指針大小的值（帶符號）
dd ffffe000`12345678 L10        ← DWORD
dq ffffe000`12345678 L10        ← QWORD
db ffffe000`12345678 L100       ← Bytes（十六進位）
da ffffe000`12345678 L100       ← ASCII 字串
du ffffe000`12345678 L100       ← Unicode 字串

r                               ← 顯示所有暫存器
r rax, rbx, rip                 ← 顯示特定暫存器
r rax = 0x1234                  ← 修改暫存器（危險！）
```

## 進程和執行緒

```
!process 0 0                    ← 列出所有進程（簡短）
!process 0 0 notepad.exe        ← 找特定進程
!process ffffe000`12345678 7    ← 詳細顯示（7 = 全部資訊）

.process /r /p ffffe000`12345678  ← 切換到這個進程的上下文（載入其符號）

!thread                         ← 當前執行緒
!thread ffffe000`deadbeef 0f    ← 顯示特定執行緒

!peb                            ← 用戶態 PEB（需先 .process 切換到正確進程）
!teb                            ← 用戶態 TEB
```

## Driver 和 Device

```
lm                              ← 列出所有載入模組（包含 .sys）
lm t n                          ← 顯示模組起止地址
lm m MyDriver*                  ← 過濾特定模組

!drvobj MyDriver                ← 顯示 DRIVER_OBJECT
!drvobj ffffe000`...            ← 用地址
!devobj ffffe000`...            ← 顯示 DEVICE_OBJECT
!devstack ffffe000`...          ← 顯示 Device Stack 層次

!object \Device\MyDriver        ← 查命名空間物件
!object \                       ← 根目錄
```

## IRP 分析

```
!irp ffffe000`...               ← 顯示 IRP 詳細資訊（IRP stack trace）
!irpfind                        ← 搜尋系統中所有 IRP（很慢，偵錯用）
```

## 核心調試的 WinDbg 常用 Extension 命令

```
!analyze -v                     ← 自動分析崩潰（最常用！）
!pcr                            ← Processor Control Region（IRQL 等）
!prcb                           ← Processor Control Block
!locks                          ← 列出所有持有的 Executive 鎖
!qlocks                         ← 列出所有 Queued SpinLock
!pool ffffe000`...              ← 分析這個地址所在的 Pool 資訊
!poolused                       ← Pool 使用統計（by Tag）
!vm                             ← 虛擬記憶體統計
!address ffffe000`...           ← 查詢地址的記憶體屬性

.trap ffffe000`...              ← 切換到 Trap Frame（崩潰發生時的暫存器狀態）
.frame <n>                      ← 切換到 call stack 的第 n 層
k                               ← 顯示 call stack（Kernel）
kb                              ← call stack + 前三個參數
kP                              ← call stack + 所有參數（帶名稱）
```

## 設定有條件的斷點

```
# 只在特定進程中命中（PID = 0x1234）
bp MyDriver!DispatchRead "j (poi(nt!PsGetCurrentProcessId()) == 0x1234) ''; 'gc'"

# 每次命中時印出一行
bp MyDriver!DispatchRead ".echo Hit DispatchRead; g"

# 命中 5 次後停住（前 4 次自動 g）
bp /p 5 MyDriver!DispatchRead
```

## 在驅動代碼中主動觸發 Debugger

```c
// 插入 int 3 觸發 WinDbg 斷點（只在有 Debugger 連線時有效）
DbgBreakPoint();

// 在代碼中留一個軟斷點
__debugbreak();

// 條件斷點（只在 DEBUG 組態）
if (someCondition) {
    KdBreakPoint();  // = DbgBreakPoint() 的宏版本
}
```

## 實戰：在 DispatchRead 設斷點，看 IRP 內容

```
kd> bu MyDriver!DispatchRead
kd> g

（VM 執行，用戶端呼叫 ReadFile）

Breakpoint 0 hit
MyDriver!DispatchRead:
    ...

kd> kb           ← 看 call stack
kd> r rcx        ← 第一個參數 = DeviceObject
kd> r rdx        ← 第二個參數 = IRP 指針
kd> !irp @rdx    ← 用 rdx 的值查 IRP（@rdx = 暫存器的當前值）
kd> dt nt!_IRP @rdx          ← 顯示完整 IRP 結構
kd> dt nt!_IO_STACK_LOCATION  ← 查 Stack Location 結構定義
```

## 自我檢核

- [ ] `.sympath` + `.reload` 載入符號（沒有符號什麼都看不懂）
- [ ] `bp` / `bu` / `ba` 三種斷點的使用場景
- [ ] `dt nt!_EPROCESS <addr>` 顯示結構內容
- [ ] `!process 0 0` 列出進程；`.process /r /p` 切換進程上下文
- [ ] `!drvobj` / `!devobj` / `!irp` 查看驅動相關結構
- [ ] `!analyze -v` 是 BSOD 分析的第一個命令

→ [Ch 22 BSOD 崩潰分析](./22-bsod-analysis.md)
