# Ch 35 — VPS 安全配置

> 目標：把 VPS 從「default 設定」加固到「production-ready」 — SSH 加固、firewall、auto-update、monitoring。

## 為什麼要安全配置

新 VPS 上線 5 分鐘內就會被掃。我自己看過：

- Day 1：上千個 IP 嘗試 SSH brute force
- Week 1：上百個 IP 嘗試 web exploit（如果開了 80 / 443）
- Week 2：可能被入侵（如果 default password / 開放 service）

**安全配置不是 optional**。

## 標準加固清單

### 1. 改 root password（如果用 password）

```bash
sudo passwd root
# 設複雜密碼
```

### 2. 建非 root user

不要全程用 root：

```bash
adduser myname
usermod -aG sudo myname
```

### 3. SSH key 認證

```bash
# 本機
ssh-copy-id myname@<VPS-IP>

# 確認能 ssh 進
ssh myname@<VPS-IP>
sudo whoami    # 確認 sudo 可用
```

### 4. 禁 password authentication

```bash
sudo vi /etc/ssh/sshd_config

# 改 / 加：
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
AllowUsers myname

sudo systemctl restart sshd
```

**改前另開一個 SSH session 確認 key 能登入**！否則鎖死。

### 5. 改 SSH port（可選）

```bash
sudo vi /etc/ssh/sshd_config
# Port 2222
sudo systemctl restart sshd

# Firewall 開 new port（先做！）
sudo ufw allow 2222/tcp
```

改 port 不防駭，但**減少自動化 scan 噪音**。

### 6. Firewall

```bash
sudo apt install ufw

# 預設 deny incoming, allow outgoing
sudo ufw default deny incoming
sudo ufw default allow outgoing

# 允許 SSH（先！）
sudo ufw allow 22/tcp     # 或改的 port

# 你需要的服務
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# 啟用
sudo ufw enable
sudo ufw status verbose
```

### 7. fail2ban

防 brute force：

```bash
sudo apt install fail2ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban

# 看 jails
sudo fail2ban-client status
sudo fail2ban-client status sshd
```

### 8. 自動更新

```bash
# Debian / Ubuntu
sudo apt install unattended-upgrades
sudo dpkg-reconfigure unattended-upgrades

# 確認設定
cat /etc/apt/apt.conf.d/50unattended-upgrades
```

每天自動 install security update。

### 9. 系統 update

```bash
sudo apt update
sudo apt upgrade -y
sudo apt autoremove -y
```

每月手動跑一次（自動 update 主要管 security）。

### 10. 移除不必要 service

```bash
# 看跑哪些
systemctl list-units --type=service --state=running

# 沒用的關
sudo systemctl disable some-service
sudo systemctl stop some-service
```

少 service = 少攻擊面。

## SSH 進階加固

### Limit auth tries

```
# /etc/ssh/sshd_config
MaxAuthTries 3
MaxSessions 5
```

### Disable empty passwords

```
PermitEmptyPasswords no
```

### Disable X11

```
X11Forwarding no
```

### LoginGraceTime

```
LoginGraceTime 30
```

連線後 30 秒沒 login 就斷。

### 完整加固範本

```
# /etc/ssh/sshd_config

Port 2222
Protocol 2

# Auth
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
PermitEmptyPasswords no
ChallengeResponseAuthentication no
AllowUsers myname admin
MaxAuthTries 3
MaxSessions 5
LoginGraceTime 30

# Forwarding
AllowAgentForwarding no
AllowTcpForwarding no    # 嚴：禁 SSH tunneling
GatewayPorts no
X11Forwarding no

# Misc
ClientAliveInterval 300
ClientAliveCountMax 2
UseDNS no    # 加快 SSH 連線
```

## Web service 加固（如果跑 nginx）

### 隱藏 server 版本

```
# /etc/nginx/nginx.conf
server_tokens off;
```

### TLS 強配置

用 Mozilla SSL Configurator 產生 nginx config：https://ssl-config.mozilla.org/

選 "Intermediate" 或 "Modern"。

### Rate limit

```
http {
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    
    server {
        location /api {
            limit_req zone=api burst=20 nodelay;
        }
    }
}
```

## Monitoring

### 系統 metrics

```bash
sudo apt install htop iotop sysstat
```

定期看：

- CPU / RAM 用量
- disk 用量
- network throughput

### Log monitoring

```bash
# SSH login attempts
sudo tail -f /var/log/auth.log

# system errors
sudo journalctl -p err --since today
```

### 推薦工具

- **Netdata**（real-time monitoring web UI）
- **Prometheus + Grafana**（生產 metrics）
- **Lynis**（安全 audit）：

```bash
sudo apt install lynis
sudo lynis audit system
```

## Backup

```bash
# 1. VPS provider's snapshot（最快）
# 2. rsync 重要資料到別處
rsync -av /etc /home user@backup-server:/backup/$(hostname)/

# 3. 自動化 cron
crontab -e
# 0 3 * * * rsync -aq /etc /backup/etc-$(date +%F)
```

**別只信 VPS snapshot** — 廠商可能整個機房 down / 帳號被 ban。多備一份在不同地方。

## 一個常見踩雷：「改 SSH 配置後鎖死」

**永遠**：

1. 改 sshd_config 前**另開一個 ssh session** 保留
2. 在原 session 改、restart sshd
3. **新開一個** session 測連線
4. 都 OK 才關原 session

如果鎖死 → 用 VPS provider 的 console（web 介面 KVM）救援。

## 一個常見踩雷：「fail2ban ban 了自己」

**測試自己 brute force（故意打錯密碼幾次）→ 自己 IP 被 ban → 連不上**。

預防：把自己 IP 加 whitelist：

```ini
# /etc/fail2ban/jail.local
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 YOUR_HOME_IP/32
```

## 一個常見踩雷：「unattended-upgrades 跟壞了 service」

自動 update 偶爾會 break service（kernel update / lib 不相容）。

對策：

- 只開 security update（default）
- production 重要 service 後面接 monitoring，異常 alert
- 重要時段 disable update window

## 一個常見踩雷：「`ufw` 先 enable 後加 SSH rule → 鎖死」

順序：

1. 加 SSH allow rule
2. 確認 rule 在 list 裡
3. enable

```bash
sudo ufw allow 22/tcp
sudo ufw status
sudo ufw enable
```

順序錯就鎖死。

## 動手練習

**1. 完整加固一台 VPS**

新買的 VPS 從零跑這 10 步加固。記時間（通常 30-60 分）。

**2. 看自動 scan**

加固後 24 小時，看 log：

```bash
sudo tail -100 /var/log/auth.log | grep -i fail
sudo fail2ban-client status sshd
```

看到多少 IP 嘗試 brute force？

**3. Lynis audit**

```bash
sudo lynis audit system
```

看分數（0-100）。修建議的 finding。

**4. 自動 backup script**

寫 cron job 每天 backup `/etc` 跟 `/home` 到別處 server。

**5. monitoring**

裝 Netdata：

```bash
bash <(curl -Ss https://my-netdata.io/kickstart.sh)
```

打開 web UI 看 real-time metrics。

## 自我檢核

- [ ] 完整跑 10 步加固
- [ ] SSH 不允許 password / root login
- [ ] firewall 啟用 + 規則正確
- [ ] fail2ban 跑著
- [ ] 自動 update 啟用
- [ ] 有 backup 計畫
- [ ] 跑過 Lynis audit

下一章看部署 nginx + HTTPS — production web 的核心。

→ [Ch 36 部署服務 (nginx + HTTPS)](./36-nginx-deploy.md)
