# 練習 D — 買 VPS + 部署 nginx + HTTPS

> 目標：從零跑完「買 VPS → 加固 → 部署 nginx → 申請 HTTPS」完整流程。production-ready 部署的最小 case。

## 任務規格

| # | 任務 | 驗收 |
|---|---|---|
| 1 | 買 VPS（5-10 USD/月）| 能 SSH 進去 |
| 2 | 安全加固（Ch 35）| 不允許 password / root login |
| 3 | 買 domain + DNS 指過去 | `dig` 看到 VPS-IP |
| 4 | nginx 配置 + 靜態頁面 | http://your-domain/ 顯示頁面 |
| 5 | Let's Encrypt HTTPS | https://your-domain/ 顯示頁面 |
| 6 | 文件 | 寫成可重現的 step-by-step |

## 預計時間

- 買 VPS：10 分鐘
- 加固：30-60 分鐘
- DNS：5 分鐘設定 + 10-30 分鐘 propagate
- nginx + cert：30 分鐘
- 文件：30 分鐘

**總計 2-3 小時**。

## 預算

- VPS：5-10 USD/月（必要）
- Domain：10 USD/年（必要）
- 備份：1-3 USD/月（選）

**總計約 15-20 USD/月**。可用免費 tier（Oracle Cloud + Cloudflare-hosted Free Domain like .tk）省錢。

## 完整流程

### Step 1：買 VPS

選 Vultr / Linode / DigitalOcean。$5-10/月：

- 1 GB RAM
- 1 CPU
- Ubuntu 22.04 LTS
- 機房選離你近的

deploy 後 5 分鐘拿到 IP + root password / SSH key。

### Step 2：加固（按 Ch 35）

從本機 ssh：

```bash
ssh root@<VPS-IP>
```

按 Ch 35 完整 10 步：

```bash
# 1. 改 root password
passwd

# 2. 建 user
adduser myname
usermod -aG sudo myname

# 3. 從本機 ssh-copy-id
# (在本機執行)
ssh-copy-id myname@<VPS-IP>

# 4. 測新 user
ssh myname@<VPS-IP>
sudo whoami    # root

# 5. SSH config
sudo vi /etc/ssh/sshd_config
# PermitRootLogin no
# PasswordAuthentication no
# AllowUsers myname
sudo systemctl restart sshd

# 6. 確認新 user key 還能 ssh，root password 不能

# 7. ufw
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# 8. fail2ban
sudo apt install fail2ban
sudo systemctl enable --now fail2ban

# 9. auto update
sudo apt install unattended-upgrades

# 10. system update
sudo apt update && sudo apt upgrade -y
```

### Step 3：DNS

買 domain（Namecheap / Cloudflare）。

加 A record：

```
Type: A
Name: @ (root) 或 www
Value: <VPS-IP>
TTL: 3600
```

10-30 分鐘後：

```bash
dig your-domain.com
# 應該看到 VPS-IP
```

### Step 4：nginx

```bash
sudo apt install nginx
sudo systemctl enable --now nginx

# 確認跑
sudo systemctl status nginx

# 開瀏覽器 http://your-domain.com 看 default page
```

### Step 5：寫自己的 site

```bash
sudo mkdir -p /var/www/mysite

sudo tee /var/www/mysite/index.html <<EOF
<!DOCTYPE html>
<html>
<head><title>My Site</title></head>
<body>
<h1>Hello from VPS!</h1>
<p>This is my self-deployed site.</p>
</body>
</html>
EOF

sudo tee /etc/nginx/sites-available/mysite <<EOF
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;
    
    root /var/www/mysite;
    index index.html;
    
    location / {
        try_files \$uri \$uri/ =404;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/mysite /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

打開 http://your-domain.com 應該看到「Hello from VPS!」。

### Step 6：HTTPS

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
# 給 email、同意 ToS、選 redirect HTTPS
```

打開 https://your-domain.com — 綠鎖 + 你的頁面。

確認 auto renew：

```bash
sudo systemctl status certbot.timer
sudo certbot renew --dry-run
```

### Step 7：寫文件

把上面所有命令 + 環境變數整理成可重現的 markdown：

```markdown
# 部署文件 — your-domain.com

## 環境
- VPS：Vultr/Linode/...
- Region：Tokyo
- Plan：$6/月
- Domain：Namecheap

## Steps
1. Provisioning
   - Vultr deploy Ubuntu 22.04
   - 拿到 IP: 1.2.3.4
   - 設 SSH key

2. Hardening
   - (按上面)

3. DNS
   - Namecheap 設 A record

4. nginx + HTTPS
   - (按上面)

## 驗證
- ssh myname@1.2.3.4 ✓
- https://your-domain.com ✓
- Let's Encrypt auto renew ✓

## 維護
- 每月 sudo apt upgrade
- 每月看 fail2ban 統計
```

## 進階挑戰

**A. 加 nginx security headers**：CSP / HSTS / X-Frame-Options。

**B. 跑個 reverse proxy**：本機 Python flask 在 :3000，nginx 接到 :443。

**C. 接 Cloudflare**：DNS 用 Cloudflare proxy（橘雲）→ DDoS 防護 + 加快速度。

**D. backup 自動化**：cron + rsync 定期備份到別處。

**E. monitoring**：裝 Netdata 或 Prometheus，看自己 site metrics。

**F. domain 跑多 site**：sub.your-domain.com + www.your-domain.com 各自 nginx server block。

## 常見錯誤

| 症狀 | 原因 |
|---|---|
| https 連不上 | DNS 沒 propagate / firewall 沒開 443 |
| certbot 拒絕 | DNS 沒對 / port 80 不通 / domain 寫錯 |
| nginx 改了沒生效 | 沒 reload / browser cache |
| 502 bad gateway | reverse proxy 後端沒跑 |
| 突然 SSH 不通 | 加固改錯 → 用 VPS console 救 |

## 自我檢核

- [ ] VPS 買並加固
- [ ] HTTPS 部署成功
- [ ] DNS 指對
- [ ] cert 自動 renew 跑著
- [ ] 文件寫完，下次能 5 分鐘重做
- [ ] 知道如何 debug 各類錯誤

下個 Part 進進階速覽：容器網路 / IPv6 / QUIC。

→ [Ch 37 容器網路](./37-container-networking.md)
