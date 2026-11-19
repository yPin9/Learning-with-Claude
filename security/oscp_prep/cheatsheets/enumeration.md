# 速查：枚舉（Enumeration）

> 考試時 Ctrl-F 用。原理看 [Ch 6](../06-nmap.md)–[Ch 9](../09-vuln-search.md)、[Ch 44](../44-enum-automation.md)。

## nmap 開場（每台都做）

```bash
# 1) 全埠快掃找開放埠
nmap -p- --min-rate 2000 -T4 -oN allports.txt IP
# 2) 針對開放埠深掃（腳本+版本）
nmap -sCV -p<逗號分隔埠> -oN deep.txt IP
# 3) UDP top 100（別忘）
sudo nmap -sU --top-ports 100 IP
# 擋 ping 的主機：全部加 -Pn
```

## 依埠對照該做什麼

| Port | 服務 | 先做 |
|------|------|------|
| 21 | FTP | 匿名登入 `ftp IP`（anonymous/空）、能否上傳 |
| 22 | SSH | 版本漏洞、拿到憑證後登入 |
| 25 | SMTP | `VRFY`/`EXPN` 枚舉使用者、swaks |
| 53 | DNS | zone transfer `dig axfr @IP domain` |
| 80/443 | HTTP | 見 [web 速查](./web.md)：目錄爆破、vhost、指紋 |
| 88 | Kerberos | 這是 DC → 走 [AD 速查](./active-directory.md) |
| 110/143 | POP3/IMAP | 憑證登入讀信 |
| 111/2049 | NFS | `showmount -e IP`、mount |
| 139/445 | SMB | 見下方 SMB 區塊 |
| 161 UDP | SNMP | `snmpwalk`、onesixtyone |
| 389/636 | LDAP | `ldapsearch` null bind → 走 AD |
| 1433 | MSSQL | `mssqlclient.py`、xp_cmdshell |
| 3306 | MySQL | 弱密碼、讀檔寫 webshell |
| 3389 | RDP | 憑證登入 `xfreerdp` |
| 5985/5986 | WinRM | `evil-winrm -i IP -u u -p p` |

## SMB

```bash
smbclient -L //IP/ -N                 # 匿名列分享
nxc smb IP -u '' -p '' --shares       # null session 列分享
nxc smb IP -u u -p p --shares         # 有憑證
nxc smb IP -u u -p p --users --rid-brute
smbmap -H IP -u u -p p
smbclient //IP/share -N               # 進入分享
# SMB 版本漏洞：MS17-010 (EternalBlue) → nmap --script smb-vuln*
```

## FTP / SNMP / NFS / DNS

```bash
ftp IP                                 # anonymous / 空密碼
snmpwalk -v2c -c public IP             # community 不只 public
onesixtyone -c /usr/share/seclists/Discovery/SNMP/common-snmp-community-strings.txt IP
showmount -e IP                        # NFS 匯出
dig axfr @IP domain.local              # DNS zone transfer
```

## 漏洞搜尋

```bash
searchsploit <product> <version>
searchsploit -m <path>                 # 複製 exploit 到當前目錄
# 版本號會騙人：searchsploit 無果 → 回頭做服務層枚舉
```

## 自動化

```bash
sudo autorecon IP1 IP2 IP3             # 開場一次全丟（不受 MSF 限制）
# 結果：results/<IP>/scans/ → 人工判讀，別只等結論
```

## 鐵律

- 永遠 `-p-`（全埠），永遠補 UDP top 100
- **每拿到一組新憑證 → 重跑所有枚舉**（SMB/LDAP/各服務）
- 「找不到入口」= 「還沒枚舉完」
