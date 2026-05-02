# Ch 20 — 網路管理工具

> 目標：用 PowerShell 取代 `ping`、`tracert`、`netstat`、`ipconfig`，以及做基本的網路連通性診斷腳本。

## Test-Connection：ping 的替代

```powershell
# 基本 ping（預設 4 次）
Test-Connection -ComputerName "google.com"
Test-Connection -ComputerName "8.8.8.8"

# 只回傳 True/False（腳本最常用）
Test-Connection -ComputerName "server01" -Count 1 -Quiet

# 批次測試多台主機
$hosts = @("8.8.8.8", "1.1.1.1", "192.168.1.1", "192.168.1.99")
$hosts | ForEach-Object {
    $alive = Test-Connection -ComputerName $_ -Count 1 -Quiet
    [PSCustomObject]@{
        Host   = $_
        Status = if ($alive) { "UP" } else { "DOWN" }
    }
} | Format-Table -AutoSize

# 測試延遲（取得 RTT）
Test-Connection -ComputerName "google.com" -Count 4 |
    Measure-Object -Property ResponseTime -Average -Maximum -Minimum
```

## Test-NetConnection：更強大的連通性測試

```powershell
# 基本
Test-NetConnection -ComputerName "google.com"

# 測試特定 TCP Port
Test-NetConnection -ComputerName "web01" -Port 443
Test-NetConnection -ComputerName "db01"  -Port 5432

# 輸出包含：TcpTestSucceeded, PingSucceeded, RemoteAddress 等

# 測試多個 Port
@(80, 443, 8080) | ForEach-Object {
    $result = Test-NetConnection -ComputerName "web01" -Port $_ -WarningAction SilentlyContinue
    [PSCustomObject]@{
        Port    = $_
        Success = $result.TcpTestSucceeded
    }
}
```

`-WarningAction SilentlyContinue` 可以抑制連線失敗時的警告訊息，腳本裡通常加上。

## Resolve-DnsName：DNS 查詢

```powershell
# 基本查詢（等同 nslookup）
Resolve-DnsName "google.com"

# 查特定記錄類型
Resolve-DnsName "google.com" -Type A       # IPv4
Resolve-DnsName "google.com" -Type AAAA    # IPv6
Resolve-DnsName "google.com" -Type MX      # 郵件伺服器
Resolve-DnsName "google.com" -Type TXT     # SPF/DKIM 等

# 反向查詢（IP → 主機名稱）
Resolve-DnsName "8.8.8.8"

# 指定 DNS 伺服器查詢
Resolve-DnsName "internal.corp" -Server "192.168.1.1"
```

## Get-NetAdapter：網路介面卡

```powershell
# 列出所有網卡
Get-NetAdapter

# 只看啟用的
Get-NetAdapter | Where-Object Status -eq Up

# 查看詳細資訊（包含 MAC 地址）
Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, MacAddress, LinkSpeed

# 查 IP 設定
Get-NetIPAddress | Where-Object AddressFamily -eq IPv4

# 查預設閘道
Get-NetRoute | Where-Object { $_.DestinationPrefix -eq "0.0.0.0/0" }
```

## Get-NetIPConfiguration：ipconfig 的替代

```powershell
# 等同 ipconfig /all
Get-NetIPConfiguration

# 詳細版本
Get-NetIPConfiguration -Detailed

# 輸出包含：InterfaceAlias, IPv4Address, IPv4DefaultGateway, DNSServer
```

## Get-NetTCPConnection：netstat 的替代

```powershell
# 列出所有 TCP 連線
Get-NetTCPConnection

# 只看 ESTABLISHED 的連線
Get-NetTCPConnection -State Established

# 找特定 Port
Get-NetTCPConnection -LocalPort 443
Get-NetTCPConnection -RemotePort 5432

# 對應行程名稱（組合）
Get-NetTCPConnection -State Established |
    Where-Object { $_.RemoteAddress -ne "::1" -and $_.RemoteAddress -ne "127.0.0.1" } |
    Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort, State,
        @{N='ProcessName'; E={
            (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name
        }} |
    Sort-Object RemoteAddress |
    Format-Table -AutoSize
```

## 網路設定管理

```powershell
# 設定靜態 IP
New-NetIPAddress -InterfaceAlias "Ethernet" `
    -IPAddress "192.168.1.100" `
    -PrefixLength 24 `
    -DefaultGateway "192.168.1.1"

# 設定 DNS
Set-DnsClientServerAddress -InterfaceAlias "Ethernet" `
    -ServerAddresses @("8.8.8.8", "8.8.4.4")

# 改回 DHCP
Set-NetIPInterface -InterfaceAlias "Ethernet" -Dhcp Enabled
Remove-NetIPAddress -InterfaceAlias "Ethernet" -Confirm:$false
Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ResetServerAddresses
```

## 網路診斷腳本

```powershell
function Test-NetworkHealth {
    param(
        [string[]]$Hosts = @("8.8.8.8", "1.1.1.1", "google.com"),
        [int[]]$Ports
    )

    Write-Host "`n=== 網路連通性報告 ===" -ForegroundColor Cyan
    Write-Host "時間：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n"

    # Ping 測試
    Write-Host "Ping 測試：" -ForegroundColor Yellow
    foreach ($h in $Hosts) {
        $alive = Test-Connection -ComputerName $h -Count 1 -Quiet
        $rtt   = if ($alive) {
            (Test-Connection -ComputerName $h -Count 1).ResponseTime
        } else { "N/A" }
        $color = if ($alive) { "Green" } else { "Red" }
        Write-Host ("  {0,-20} {1,-6} {2}" -f $h, $(if ($alive){"UP"}else{"DOWN"}), $(if ($alive){"${rtt}ms"}else{""})) -ForegroundColor $color
    }

    # Port 測試
    if ($Ports) {
        Write-Host "`nPort 測試：" -ForegroundColor Yellow
        foreach ($h in $Hosts) {
            foreach ($p in $Ports) {
                $ok = (Test-NetConnection -ComputerName $h -Port $p -WarningAction SilentlyContinue).TcpTestSucceeded
                Write-Host ("  {0,-20}:{1,-6} {2}" -f $h, $p, $(if ($ok){"OPEN"}else{"CLOSED"})) -ForegroundColor $(if ($ok){"Green"}else{"Red"})
            }
        }
    }
}

Test-NetworkHealth -Ports 80, 443
```

## 動手練習

```powershell
# 掃描本機網段（192.168.1.1-254）存活的主機
$subnet = "192.168.1"
1..254 | ForEach-Object -Parallel {
    $ip = "$using:subnet.$_"
    if (Test-Connection -ComputerName $ip -Count 1 -Quiet -TimeoutSeconds 1) {
        $hostname = try {
            (Resolve-DnsName $ip -ErrorAction Stop).NameHost
        } catch { "unknown" }
        [PSCustomObject]@{ IP = $ip; Hostname = $hostname }
    }
} -ThrottleLimit 50 |
    Where-Object { $_ } |
    Sort-Object { [Version]$_.IP }
```

## 自我檢核

- [ ] 知道 `Test-NetConnection -Port` 可以測試 TCP Port 開不開
- [ ] 能用 `Get-NetTCPConnection` + `Get-Process` 找出佔用某 Port 的行程
- [ ] 能用 `-Parallel` 做平行網路掃描
- [ ] 知道 `Get-NetIPConfiguration` 是 `ipconfig /all` 的 PS 版

→ [Ch 21 登錄檔操作](./21-registry.md)
