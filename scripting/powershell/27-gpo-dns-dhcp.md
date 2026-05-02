# Ch 27 — GPO、DNS、DHCP 腳本化

> 目標：用 PowerShell 模組管理 Group Policy、DNS 記錄、DHCP 保留，取代圖形化管理工具。

## Group Policy 模組

需要在 Domain Controller 或安裝了 GPMC RSAT 的機器上執行：

```powershell
# 確認模組可用
Get-Module -ListAvailable -Name GroupPolicy
Import-Module GroupPolicy
```

### 查詢 GPO

```powershell
# 列出所有 GPO
Get-GPO -All

# 查特定 GPO
Get-GPO -Name "Default Domain Policy"

# 查 GPO 連結（套用在哪些 OU）
Get-GPOReport -Name "Default Domain Policy" -ReportType Html -Path C:\Temp\gpo-report.html

# 找出特定 OU 套用的 GPO
Get-GPInheritance -Target "OU=IT,DC=corp,DC=com"
```

### 建立和修改 GPO

```powershell
# 建立空白 GPO
$gpo = New-GPO -Name "IT Workstation Policy" -Domain "corp.com"

# 連結到 OU
New-GPLink `
    -Name "IT Workstation Policy" `
    -Target "OU=IT,DC=corp,DC=com" `
    -LinkEnabled Yes

# 修改 GPO 設定（Registry 路徑）
Set-GPRegistryValue `
    -Name "IT Workstation Policy" `
    -Key "HKCU\Software\Policies\Microsoft\Windows\Control Panel\Desktop" `
    -ValueName "ScreenSaveTimeOut" `
    -Type DWord `
    -Value 600    # 10 分鐘

# 查詢 GPO 中的 Registry 設定
Get-GPRegistryValue `
    -Name "IT Workstation Policy" `
    -Key "HKCU\Software\Policies\Microsoft\Windows\Control Panel\Desktop"
```

### 備份和還原 GPO

```powershell
# 備份所有 GPO
Backup-GPO -All -Path C:\GPOBackup\$(Get-Date -Format 'yyyyMMdd')

# 備份特定 GPO
Backup-GPO -Name "IT Workstation Policy" -Path C:\GPOBackup

# 還原
Restore-GPO -Name "IT Workstation Policy" -Path C:\GPOBackup

# 從備份複製（到另一個環境）
Import-GPO -BackupId "{GUID}" -TargetName "New Policy Name" -Path C:\GPOBackup
```

## DNS 管理（DnsServer 模組）

在 DNS Server 上執行，或安裝 RSAT DNS 工具：

```powershell
Import-Module DnsServer
```

### 查詢記錄

```powershell
# 查詢區域
Get-DnsServerZone -ComputerName "dc01"

# 查詢 A 記錄
Get-DnsServerResourceRecord -ZoneName "corp.com" -RRType A -Name "web01"

# 列出區域所有記錄
Get-DnsServerResourceRecord -ZoneName "corp.com" -RRType A

# 查詢所有類型
Get-DnsServerResourceRecord -ZoneName "corp.com"
```

### 建立和刪除記錄

```powershell
# 建立 A 記錄
Add-DnsServerResourceRecordA `
    -ZoneName "corp.com" `
    -Name "app01" `
    -IPv4Address "192.168.1.50" `
    -TimeToLive (New-TimeSpan -Hours 1) `
    -ComputerName "dc01"

# 建立 CNAME 記錄
Add-DnsServerResourceRecordCName `
    -ZoneName "corp.com" `
    -Name "www" `
    -HostNameAlias "web01.corp.com." `
    -ComputerName "dc01"

# 建立 MX 記錄
Add-DnsServerResourceRecordMX `
    -ZoneName "corp.com" `
    -Name "@" `
    -MailExchange "mail.corp.com" `
    -Preference 10 `
    -ComputerName "dc01"

# 刪除記錄
Remove-DnsServerResourceRecord `
    -ZoneName "corp.com" `
    -RRType A `
    -Name "old-server" `
    -Confirm:$false `
    -ComputerName "dc01"
```

### 批次建立 DNS 記錄

```powershell
# dns-records.csv：Name,IP
$records = Import-Csv C:\Data\dns-records.csv

foreach ($r in $records) {
    if (Get-DnsServerResourceRecord -ZoneName "corp.com" -Name $r.Name -RRType A -ErrorAction SilentlyContinue) {
        Write-Warning "$($r.Name) 已存在"
        continue
    }
    Add-DnsServerResourceRecordA -ZoneName "corp.com" -Name $r.Name -IPv4Address $r.IP
    Write-Host "建立：$($r.Name) → $($r.IP)"
}
```

## DHCP 管理（DhcpServer 模組）

在 DHCP Server 上執行，或安裝 RSAT DHCP 工具：

```powershell
Import-Module DhcpServer
```

### 查詢租約

```powershell
# 查詢所有作用域（Scope）
Get-DhcpServerv4Scope -ComputerName "dhcp01"

# 查詢作用域的所有租約
Get-DhcpServerv4Lease -ScopeId "192.168.1.0" -ComputerName "dhcp01"

# 查詢特定 MAC 地址的租約
Get-DhcpServerv4Lease -ScopeId "192.168.1.0" -ComputerName "dhcp01" |
    Where-Object { $_.ClientId -eq "aa-bb-cc-dd-ee-ff" }
```

### 保留（Reservation）管理

保留 = 把特定 MAC 地址綁定到固定 IP，讓機器每次都拿到同一個 IP：

```powershell
# 建立保留
Add-DhcpServerv4Reservation `
    -ScopeId "192.168.1.0" `
    -IPAddress "192.168.1.50" `
    -ClientId "aa-bb-cc-dd-ee-ff" `
    -Description "Web Server 01" `
    -ComputerName "dhcp01"

# 查詢所有保留
Get-DhcpServerv4Reservation -ScopeId "192.168.1.0" -ComputerName "dhcp01"

# 移除保留
Remove-DhcpServerv4Reservation `
    -ScopeId "192.168.1.0" `
    -ClientId "aa-bb-cc-dd-ee-ff" `
    -ComputerName "dhcp01"
```

### 批次建立保留

```powershell
# reservations.csv：IPAddress,ClientId,Description
$reservations = Import-Csv C:\Data\reservations.csv

foreach ($r in $reservations) {
    Add-DhcpServerv4Reservation `
        -ScopeId "192.168.1.0" `
        -IPAddress $r.IPAddress `
        -ClientId $r.ClientId `
        -Description $r.Description `
        -ComputerName "dhcp01"
    Write-Host "保留：$($r.IPAddress) for $($r.ClientId)"
}
```

## 動手練習

```powershell
# 如果有環境，試試這幾個查詢指令（唯讀，不修改）：

# 1. 查詢 Default Domain Policy 連結了哪些 OU
Get-GPO -Name "Default Domain Policy" |
    Select-Object DisplayName, GpoStatus, CreationTime, ModificationTime

# 2. 查詢 DNS 主要區域（不修改）
Get-DnsServerZone | Where-Object { $_.ZoneType -eq "Primary" } |
    Select-Object ZoneName, DynamicUpdate, ReplicationScope

# 3. 查詢目前 DHCP 的 IP 使用情況
Get-DhcpServerv4Scope | ForEach-Object {
    $stats = Get-DhcpServerv4ScopeStatistics -ScopeId $_.ScopeId
    [PSCustomObject]@{
        Scope    = $_.ScopeId
        InUse    = $stats.InUse
        Free     = $stats.Free
        Reserved = $stats.Reserved
    }
}
```

## 自我檢核

- [ ] 知道 GPO 備份用 `Backup-GPO -All`，可以版本控制
- [ ] 能用 `Add-DnsServerResourceRecordA` 批次建立 A 記錄
- [ ] 理解 DHCP 保留（Reservation）的用途：讓特定 MAC 每次拿到同一 IP
- [ ] 知道這些模組（GroupPolicy、DnsServer、DhcpServer）需要 RSAT 或在伺服器上執行

→ [練習 C：AD 批次建立使用者腳本](./practice-c-ad-bulk-users.md)
