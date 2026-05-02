# Ch 30 — 套件管理與 systemd

> 目標：掌握 apt/dnf 套件管理，能用 `systemctl`/`journalctl` 管理和診斷服務，理解 unit file 的基本結構。

## 套件管理

Linux 的套件管理分兩大派系：

| 工具 | 發行版 | 套件格式 |
|------|--------|---------|
| `apt` | Debian、Ubuntu、Kali | `.deb` |
| `dnf` / `yum` | RHEL、Fedora、CentOS | `.rpm` |
| `pacman` | Arch、Manjaro | `.pkg.tar.xz` |

### apt（Debian/Ubuntu）

```bash
# 更新套件清單（先做這個，再安裝）
sudo apt update

# 安裝
sudo apt install nginx
sudo apt install -y nginx    # -y = 自動回答 yes

# 刪除
sudo apt remove nginx        # 只刪程式，留設定檔
sudo apt purge nginx         # 連設定檔一起刪

# 升級
sudo apt upgrade             # 升級已安裝的套件
sudo apt full-upgrade        # 升級，可以刪舊套件（更積極）

# 搜尋
apt search nginx
apt show nginx               # 顯示詳細資訊

# 查看已安裝
dpkg -l | grep nginx
dpkg -L nginx                # 列出套件安裝的所有檔案

# 清理
sudo apt autoremove          # 刪除不需要的依賴
sudo apt clean               # 清空 cache（/var/cache/apt/archives）
```

### dnf（RHEL/Fedora/CentOS）

```bash
sudo dnf install nginx
sudo dnf remove nginx
sudo dnf update
sudo dnf search nginx
sudo dnf info nginx
dnf list installed | grep nginx
sudo dnf autoremove
sudo dnf clean all
```

## systemd：服務管理

現代 Linux 幾乎都用 systemd 作為 init system（PID 1）。它管理服務（daemon）的啟動、停止、重啟、開機自動啟動。

### systemctl 基本操作

```bash
# 服務操作
sudo systemctl start nginx       # 啟動
sudo systemctl stop nginx        # 停止
sudo systemctl restart nginx     # 重啟（stop + start）
sudo systemctl reload nginx      # 重新載入設定（不中斷服務）
sudo systemctl status nginx      # 查看狀態

# 開機設定
sudo systemctl enable nginx      # 開機自動啟動
sudo systemctl disable nginx     # 取消自動啟動
sudo systemctl enable --now nginx  # 啟動 + 設為自動啟動

# 查詢
systemctl is-active nginx        # 是否在跑（exit code 0 = 是）
systemctl is-enabled nginx       # 是否設為自動啟動
systemctl list-units --type=service --state=running   # 列出跑中的服務
systemctl list-units --type=service --state=failed    # 列出失敗的服務
```

### systemctl status 解讀

```bash
sudo systemctl status sshd
```

輸出：

```
● ssh.service - OpenBSD Secure Shell server
     Loaded: loaded (/lib/systemd/system/ssh.service; enabled; vendor preset: enabled)
     Active: active (running) since Mon 2024-01-15 09:00:01 UTC; 5h 30min ago
    Process: 1234 ExecStartPre=/usr/sbin/sshd -t (code=exited, status=0/SUCCESS)
   Main PID: 1235 (sshd)
      Tasks: 1 (limit: 4915)
     Memory: 4.7M
        CPU: 251ms
     CGroup: /system.slice/ssh.service
             └─1235 "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups"

Jan 15 09:00:01 hostname sshd[1235]: Server listening on 0.0.0.0 port 22.
```

## journalctl：查看日誌

systemd 的 journal 取代了傳統的 `/var/log/syslog`：

```bash
journalctl                          # 所有 log（很多，不要這樣直接跑）
journalctl -u nginx                 # 某個服務的 log
journalctl -u nginx -f              # -f = follow（即時追蹤）
journalctl -u nginx -n 50           # 最後 50 行
journalctl -u nginx --since "1 hour ago"
journalctl -u nginx --since "2024-01-15 09:00" --until "2024-01-15 10:00"
journalctl -p err                   # 只看 error 以上級別（emerg/alert/crit/err）
journalctl -b                       # 本次開機的 log
journalctl -b -1                    # 上次開機的 log
journalctl --disk-usage             # journal 佔用多少空間
sudo journalctl --vacuum-size=200M  # 清到剩 200MB
```

## Unit File 基本結構

服務設定在 `/etc/systemd/system/` 或 `/lib/systemd/system/`：

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=My Application
After=network.target        # 在 network 啟動後才啟動

[Service]
Type=simple
User=myapp
WorkingDirectory=/opt/myapp
ExecStart=/opt/myapp/bin/myapp --port 8080
Restart=on-failure          # 失敗時自動重啟
RestartSec=5                # 重啟前等 5 秒
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target  # 什麼 target 時啟動（= 一般多使用者模式）
```

改完 unit file 之後：

```bash
sudo systemctl daemon-reload     # 重新載入設定
sudo systemctl enable --now myapp
```

## 服務排障流程

```bash
# 1. 看狀態
systemctl status myapp

# 2. 看近期 log
journalctl -u myapp -n 50

# 3. 如果服務啟動失敗，看詳細錯誤
journalctl -u myapp -b   # 本次開機的所有 log

# 4. 確認設定檔沒問題
nginx -t                 # nginx 設定語法檢查
myapp --check-config     # 看應用是否有 dry-run 模式

# 5. 確認 port 沒被佔用
ss -tlnp | grep :8080

# 6. 確認使用者和檔案權限
ls -la /opt/myapp/
id myapp   # 確認 myapp user 存在
```

## 動手練習

```bash
# 1. 查看所有跑中的服務
systemctl list-units --type=service --state=running

# 2. 查看 SSH 服務狀態和最近 log
systemctl status sshd
journalctl -u sshd -n 20

# 3. 建立一個簡單的自訂服務（不需要 root 可以用 --user）
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/hello.service << 'EOF'
[Unit]
Description=Hello Timer

[Service]
Type=oneshot
ExecStart=/bin/echo "Hello from systemd at %t"
EOF

systemctl --user daemon-reload
systemctl --user start hello
journalctl --user -u hello

# 4. 看 journal 空間使用
journalctl --disk-usage

# 5. 找最近失敗的服務
systemctl list-units --type=service --state=failed 2>/dev/null || echo "No failed services"
```

## 自我檢核

- [ ] 記住 `apt update` 更新套件清單，`apt upgrade` 才是實際升級
- [ ] 能用 `systemctl enable --now` 一次完成啟動和設為自動啟動
- [ ] 知道 `journalctl -u <service> -f` 可以即時追蹤服務 log
- [ ] 能寫一個基本的 `.service` unit file

→ [Final Project：SysOps 腳本工具包](./final-project-sysops-scripts.md)
