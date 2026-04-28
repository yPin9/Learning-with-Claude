# Final Project — 完整 VPS 部署

> 目標：把整套課程整合 — 買 VPS + 加固 + 架 WireGuard + nginx reverse proxy + HTTPS + tcpdump debug + 寫成完整文件。完成後你能獨立做 production-ready 部署。

## 任務規格

| # | 任務 | Part 對應 |
|---|---|---|
| 1 | 買 VPS + 加固 | Part 8 (Ch 32-35) |
| 2 | 架 WireGuard server | Part 6 (Ch 24) |
| 3 | 部署 nginx 跟自寫應用 | Part 8 (Ch 36) |
| 4 | nginx reverse proxy 到 backend | Part 8 (Ch 36) |
| 5 | Let's Encrypt HTTPS | Part 8 (Ch 36) |
| 6 | 用 tcpdump debug 1 個問題 | Part 4 (Ch 14) |
| 7 | 寫完整部署文件 | 全部 |

## 預計時間

5-10 小時。可分多天做。

## 最終架構

```
                      ┌──────────────────────────────┐
                      │   你的 VPS (1.2.3.4)         │
                      │                              │
   你的 phone ───VPN──►│ wg0 (10.10.10.1)           │
                      │     │                        │
                      │     ▼                        │
                      │  你的內網 service             │
                      │                              │
   public user ──────►│ nginx (443) → backend (3000) │
                      │     ▲                        │
                      │     │ HTTPS (Let's Encrypt) │
                      │                              │
                      └──────────────────────────────┘
```

## 完整 Step-by-step

### Phase 1：VPS provisioning

按 Ch 35 完整 10 步加固。

```bash
# 假設 VPS IP = 1.2.3.4
ssh root@1.2.3.4

adduser deploy
usermod -aG sudo deploy
# (本機) ssh-copy-id deploy@1.2.3.4

# SSH 配置
sudo vi /etc/ssh/sshd_config
# PermitRootLogin no
# PasswordAuthentication no  
# AllowUsers deploy
sudo systemctl restart sshd

# Firewall
sudo apt install ufw
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 51820/udp   # WireGuard
sudo ufw enable

# fail2ban + auto update
sudo apt install fail2ban unattended-upgrades
sudo systemctl enable fail2ban
sudo dpkg-reconfigure unattended-upgrades

# 系統 update
sudo apt update && sudo apt upgrade -y
```

### Phase 2：WireGuard

按 Ch 24 完整流程：

```bash
sudo apt install wireguard

cd /etc/wireguard
sudo wg genkey | sudo tee server_private.key | sudo wg pubkey | sudo tee server_public.key

sudo tee wg0.conf <<EOF
[Interface]
Address = 10.10.10.1/24
ListenPort = 51820
PrivateKey = $(sudo cat server_private.key)
PostUp   = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
EOF

sudo sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf
sudo sysctl -p

sudo wg-quick up wg0
sudo systemctl enable wg-quick@wg0
```

加 client peer（按 Ch 24 / Practice C）。

### Phase 3：DNS

註冊 domain（如果沒）。DNS A record 指向 1.2.3.4：

```
A   your-domain.com    1.2.3.4
A   www.your-domain.com 1.2.3.4
```

10-30 分鐘 propagate。

```bash
dig your-domain.com
# 應該看到 1.2.3.4
```

### Phase 4：簡單 backend app

寫個 Python Flask（或 Node Express）作為 backend：

```python
# app.py
from flask import Flask, jsonify
import socket

app = Flask(__name__)

@app.route('/')
def index():
    return jsonify({
        'message': 'Hello from VPS!',
        'hostname': socket.gethostname(),
        'visit': 'success'
    })

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=3000)
```

```bash
sudo apt install python3-pip python3-venv
python3 -m venv venv
source venv/bin/activate
pip install flask gunicorn
```

跑 backend：

```bash
gunicorn --bind 127.0.0.1:3000 --workers 2 --daemon app:app
```

systemd service 化（production）：

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=My App
After=network.target

[Service]
User=deploy
WorkingDirectory=/home/deploy/myapp
Environment="PATH=/home/deploy/myapp/venv/bin"
ExecStart=/home/deploy/myapp/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:3000 app:app

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now myapp
sudo systemctl status myapp
```

### Phase 5：nginx reverse proxy

```bash
sudo apt install nginx
```

```nginx
# /etc/nginx/sites-available/myapp
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;
    
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    access_log /var/log/nginx/myapp_access.log;
    error_log /var/log/nginx/myapp_error.log;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/myapp /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

打開 http://your-domain.com — 看到 Flask response。

### Phase 6：HTTPS

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

certbot 自動改 nginx config + 處理 redirect。

打開 https://your-domain.com — HTTPS 通了。

### Phase 7：debug 練習

故意製造一個問題，用 tcpdump debug：

```bash
# 模擬：backend 突然 down
sudo systemctl stop myapp
curl -I https://your-domain.com
# 應該看到 502 Bad Gateway

# debug
sudo tail -20 /var/log/nginx/myapp_error.log
# 看到 "connect() failed (111: Connection refused)" 

# tcpdump
sudo tcpdump -nn -i lo 'port 3000'
# 確認 backend 沒 listen

sudo ss -tnlp | grep 3000
# 沒 process

# 修
sudo systemctl start myapp
curl -I https://your-domain.com
# 200 OK
```

寫成「debug 報告」。

### Phase 8：寫完整文件

把所有 step 整理成一份 `DEPLOYMENT.md`：

```markdown
# 完整部署文件 — your-domain.com

## 環境
- VPS: Vultr Tokyo
- Specs: 1 CPU, 1 GB RAM, $6/month
- OS: Ubuntu 22.04 LTS
- Domain: Namecheap

## Steps
（按 Phase 1-7 整理）

## 配置檔
- /etc/ssh/sshd_config（加固版）
- /etc/wireguard/wg0.conf
- /etc/nginx/sites-available/myapp
- /etc/systemd/system/myapp.service

## 維運
- 每月 `sudo apt update && sudo apt upgrade`
- `sudo systemctl status myapp nginx wg-quick@wg0`
- 看 `journalctl -u myapp -f`

## Backup
- VPS provider snapshot 每週
- rsync /etc + /home/deploy/myapp 到別處

## Disaster recovery
- 用 backup 重建（步驟）
```

## 進階挑戰

**A. monitoring**：裝 Netdata，從外部能看 metrics dashboard

**B. CI/CD**：push 到 GitHub → 自動 deploy（GitHub Actions）

**C. Cloudflare**：DNS 走 Cloudflare proxy，DDoS 防護 + CDN

**D. multi-app**：在同 nginx 下跑 2-3 個 app（不同 subdomain）

**E. 加入監控警報**：CPU > 80% 或 disk > 80% 寄 email

**F. K8s 部署**：用 k3s 在這台 VPS 跑 K8s，container 化你的 app

## 最終驗收

- [ ] 從零開始能在 1 小時內重建這個 stack
- [ ] HTTPS 正常 + cert auto renew
- [ ] WireGuard VPN 跑著、能連
- [ ] backend service 自動啟動
- [ ] nginx access log 看得到 traffic
- [ ] 能用 tcpdump / ss / journalctl debug
- [ ] 文件完整，6 個月後自己也看得懂

恭喜完課！如果你做完整套 39 章 + 4 練習 + 1 final，你已經有「**production DevOps / SRE 入門**」的完整網路知識架構。

接下來如果想繼續深入：

- **Performance**：高併發 web (1M concurrent)、tcp tuning、io_uring
- **Security**：WAF / DDoS / pentest（連你 security/ 系列）
- **K8s**：完整容器編排
- **Cloud-native**：service mesh / observability / chaos engineering
- **Network programming**：raw socket、自家 protocol、TCP/IP 內部

→ 回到 [課程地圖](./README.md)
