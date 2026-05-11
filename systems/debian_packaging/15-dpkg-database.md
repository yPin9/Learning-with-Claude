# Ch 15 — dpkg 資料庫

> 目標：深入理解 /var/lib/dpkg/ 的每個組成，知道如何診斷和修復損壞的 dpkg 資料庫，以及如何查詢已安裝套件的完整資訊。

## dpkg 資料庫的目錄結構

```bash
ls -la /var/lib/dpkg/
```

```
/var/lib/dpkg/
├── alternatives/       ← update-alternatives 的設定（版本切換，如 python3 → python3.10）
├── info/              ← 每個已安裝套件的詳細資訊
├── lock               ← 防止多個 dpkg 同時執行的鎖定檔
├── lock-frontend      ← APT 前端的鎖定檔
├── parts/             ← 多分割 .deb 的暫存（dselect 時代產物）
├── triggers/          ← dpkg triggers 相關
│   ├── File
│   └── Unincorp
├── updates/           ← 待處理的 dpkg 操作
├── available          ← 可用套件列表（dpkg --update-avail 更新）
└── status             ← 所有已安裝套件的狀態資料庫（最重要）
```

## status 檔：所有套件的狀態

`/var/lib/dpkg/status` 是純文字，每個套件用空行分隔，格式和 control 類似：

```bash
# 查看整個檔案
wc -l /var/lib/dpkg/status      # 通常幾萬行

# 搜尋特定套件
grep -A20 "^Package: nginx$" /var/lib/dpkg/status
```

```
Package: nginx
Status: install ok installed    ← 格式：desired action installed-state
Priority: optional
Section: httpd
Installed-Size: 44
Maintainer: Ubuntu Developers...
Architecture: amd64
Version: 1.24.0-1~jammy
Depends: nginx-common (= 1.24.0-1~jammy), libnginx-mod-http-gzip-static...
Conffiles:
 /etc/nginx/nginx.conf 4ec79b77fde2cbea3e2a35d72b21ced9
Description: small, powerful, scalable web/proxy server
 ...
```

`Status` 欄位格式：`<desired> <error> <installed-state>`

```
install ok installed      ← 正常安裝
install ok half-installed ← 安裝中途中斷
install ok config-files   ← 已移除但有設定檔殘留
remove  ok not-installed  ← 已完整移除
hold    ok installed      ← 安裝中但被 hold 鎖定
```

## info/ 目錄：每個套件的詳細檔案

```bash
ls /var/lib/dpkg/info/ | grep "^nginx" 
```

```
nginx.conffiles    ← 設定檔列表
nginx.list         ← 安裝的所有檔案
nginx.md5sums      ← 每個檔案的 MD5（dpkg --verify 用）
nginx.postinst     ← 安裝後腳本
nginx.postrm       ← 移除後腳本
nginx.preinst      ← 安裝前腳本
nginx.prerm        ← 移除前腳本
nginx.triggers     ← 觸發器設定（若有）
```

```bash
# 看 nginx 裝了哪些檔案
cat /var/lib/dpkg/info/nginx.list

# 看 nginx 的設定檔列表
cat /var/lib/dpkg/info/nginx.conffiles
# /etc/nginx/nginx.conf
# /etc/nginx/sites-available/default

# 看安裝後腳本做了什麼
cat /var/lib/dpkg/info/nginx.postinst
# 通常是 systemctl daemon-reload && systemctl enable nginx
```

## dpkg Triggers（觸發器）

Triggers 是 dpkg 的事件機制。一個套件可以宣告「我對某個路徑感興趣」，當任何套件修改那個路徑下的檔案，都會觸發這個套件的 trigger。

最常見的例子：

```bash
# man-db 對 /usr/share/man/ 有 interest
cat /var/lib/dpkg/info/man-db.triggers
# interest /usr/share/man
# interest /usr/share/info

# 當你安裝任何含 man page 的套件，man-db 的 trigger 被觸發，重建 man 索引
```

這就是為什麼安裝套件時常看到 `Processing triggers for man-db...`。

## update-alternatives：管理同類工具的版本

`alternatives` 目錄管理多個同功能程式的「預設選擇」：

```bash
# 查看 python3 的 alternatives
update-alternatives --list python3

# 互動式選擇
sudo update-alternatives --config python3

# 查看 alternatives 的詳細設定
ls /etc/alternatives/
ls /var/lib/dpkg/alternatives/python3
```

```bash
$ cat /var/lib/dpkg/alternatives/python3
auto                     ← 模式（auto/manual）
/usr/bin/python3         ← symlink 位置
python3                  ← 名稱
                         ← 空行分隔
/usr/bin/python3.10      ← 選項 1 的路徑
100                      ← priority
                         ← 從屬 symlink（若有）
/usr/bin/python3.11
110
```

## 診斷和修復損壞的 dpkg 資料庫

**症狀 1：`dpkg: error: dpkg status database is locked`**

```bash
# 查看是哪個進程鎖住了
sudo fuser /var/lib/dpkg/lock-frontend
# 如果 apt 或 dpkg 掛了，強制解鎖（確認沒有 apt/dpkg 在跑才做）
sudo rm /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend
sudo rm /var/cache/apt/archives/lock
sudo dpkg --configure -a
```

**症狀 2：`dpkg: error: parsing file '/var/lib/dpkg/status'`**

```bash
# status 損壞，嘗試用備份修復
ls /var/lib/dpkg/status*
# /var/lib/dpkg/status       ← 當前（可能損壞）
# /var/lib/dpkg/status-old   ← 上次備份

sudo cp /var/lib/dpkg/status-old /var/lib/dpkg/status
sudo dpkg --configure -a
```

**症狀 3：套件處於 `half-installed` 狀態**

```bash
# 找出所有不正常狀態的套件
dpkg -l | grep -E "^(iU|iF|iH|rU|rF|rH|pU|pF|pH)"

# 強制重新設定
sudo dpkg --configure -a
sudo apt install -f
```

## 自我檢核

- [ ] `/var/lib/dpkg/status` = 所有套件的主資料庫（純文字）；`Status: install ok installed` = 正常
- [ ] `/var/lib/dpkg/info/<pkg>.list` = 套件安裝的所有檔案；`.conffiles` = 設定檔清單
- [ ] dpkg Triggers：套件對路徑感興趣，其他套件修改該路徑時自動觸發（如 man-db 重建索引）
- [ ] `update-alternatives` 管理同功能工具的預設選擇（如 python3 → python3.10 或 3.11）
- [ ] 資料庫損壞：先用 `status-old` 備份還原，再 `dpkg --configure -a` + `apt install -f`

→ [Ch 16 手工建立第一個 deb](./16-manual-deb.md)
