# Ch 34 — SSH 完整指南

> 目標：把 SSH 用到專業級 — key 管理、port forwarding、tunneling、config 檔、安全配置。

## SSH 不只是「遠端 shell」

3 大功能：

1. **遠端命令執行**（基本）
2. **檔案傳輸**（scp / sftp / rsync over SSH）
3. **Tunneling**（local / remote / dynamic forward）

第 3 個讓 SSH 變超強工具。

## SSH key 完整管理

### 生 key

```bash
# Ed25519 (推薦，現代、快、短)
ssh-keygen -t ed25519 -C "your_email@example.com"

# RSA 4096 (相容性最好)
ssh-keygen -t rsa -b 4096 -C "your_email@example.com"

# ECDSA (中庸)
ssh-keygen -t ecdsa -b 521 -C "your_email@example.com"
```

存到 `~/.ssh/`：

```
id_ed25519       ← 私鑰（不能洩漏！）
id_ed25519.pub   ← 公鑰
```

### 多 key 管理

不同用途用不同 key：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_personal
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_work
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_github
```

### 安裝公鑰到 server

```bash
# 簡單法
ssh-copy-id user@server

# 指定 key
ssh-copy-id -i ~/.ssh/id_ed25519_work.pub user@server

# 手動
cat ~/.ssh/id_ed25519.pub | ssh user@server 'cat >> ~/.ssh/authorized_keys'
```

確認 server 端：

```bash
# 在 server
cat ~/.ssh/authorized_keys
ls -la ~/.ssh
# .ssh 應該 700, authorized_keys 600
```

## ~/.ssh/config

**最強的 SSH 生產力工具**。設別名 / 預設值：

```
# ~/.ssh/config

Host vps1
    HostName 1.2.3.4
    User root
    Port 22
    IdentityFile ~/.ssh/id_ed25519_personal

Host work-bastion
    HostName bastion.company.com
    User myname
    IdentityFile ~/.ssh/id_ed25519_work
    
Host work-internal
    HostName 10.0.0.50
    User myname
    IdentityFile ~/.ssh/id_ed25519_work
    ProxyJump work-bastion

Host github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_github

# wildcard
Host *.dev.example.com
    User developer
    IdentityFile ~/.ssh/id_dev
```

之後：

```bash
ssh vps1                    # = ssh -i ~/.ssh/id_ed25519_personal -p 22 root@1.2.3.4
ssh work-internal           # 自動跳 bastion
git clone git@github.com:... # 用對的 key
```

## ProxyJump（跳板機）

公司內部 server 通常不直接連，要先連 bastion / jump server：

```
 你 ──ssh──► bastion ──ssh──► internal-server
```

舊寫法：

```bash
ssh -J bastion.com user@internal.com
```

config 寫：

```
Host internal
    HostName 10.0.0.50
    ProxyJump bastion
    User myname
```

之後 `ssh internal` 自動跳。

## SSH agent

每次連都輸入 key passphrase 累。用 ssh-agent cache：

```bash
# 啟動 agent (多數系統 systemd 或 GNOME Keyring 自動)
eval $(ssh-agent)

# 加 key（只問一次 passphrase）
ssh-add ~/.ssh/id_ed25519
ssh-add ~/.ssh/id_ed25519_work

# 看
ssh-add -l
```

之後 SSH 連線不用 prompt passphrase。

## Port forwarding（重要！）

### Local forwarding (-L)

「**把對方 server 的某 port 對到本機**」：

```bash
ssh -L 8080:localhost:80 user@vps
```

本機 `localhost:8080` = vps 上 `localhost:80`。

用途：

- 連對方 internal database（不暴露到公網）
- 用 server 的 web admin 介面
- 內網跳機跳過 firewall

例：vps 上跑 phpmyadmin 但只 listen 127.0.0.1：

```bash
ssh -L 8888:localhost:80 user@vps
# 本機 browser 開 http://localhost:8888 → 看 vps 的 phpmyadmin
```

### Remote forwarding (-R)

「**把本機某 port 對到 server**」：

```bash
ssh -R 9000:localhost:8000 user@vps
```

vps 上 `localhost:9000` = 本機 `localhost:8000`。

用途：

- 從你機器暴露 service 給 vps（vps 是「**對外**」server）
- 反向 tunnel（你在 NAT 後，vps 在公網）

例：本機跑 dev server，讓朋友透過你 vps 連：

```bash
ssh -R 8080:localhost:3000 user@vps
# 朋友訪問 http://vps-ip:8080 → 你本機 :3000
```

### Dynamic forwarding (-D) = SOCKS5

```bash
ssh -D 1080 user@vps
```

`localhost:1080` 是 SOCKS5 proxy → 走 vps。

詳見 Ch 28。**極簡 VPN 替代**。

### -N 不開 shell

只做 tunnel，不開 shell：

```bash
ssh -N -L 8080:localhost:80 user@vps
# 沒 shell prompt，純 tunnel
```

加 `-f` 後台跑：

```bash
ssh -fN -L 8080:localhost:80 user@vps
```

## SSH config 持久 forward

```
Host vps1
    HostName 1.2.3.4
    LocalForward 8080 localhost:80
    LocalForward 5432 localhost:5432
    DynamicForward 1080
```

`ssh vps1` 自動建所有 forward。

## SCP / SFTP / rsync

```bash
# scp
scp file.txt user@server:/path/
scp -r dir/ user@server:/path/
scp user@server:/path/file.txt .
scp -P 2222 file.txt user@server:/path/

# sftp（互動）
sftp user@server
sftp> get file.txt
sftp> put local.txt

# rsync（推薦，增量同步）
rsync -av file.txt user@server:/path/
rsync -av --delete dir/ user@server:/backup/   # 完整 sync 含刪
rsync -avz --progress big.iso user@server:/path/    # 壓縮 + 進度
```

`rsync` 比 `scp` 強，**生產用 rsync**。

## SSH 安全配置（server 端）

```
# /etc/ssh/sshd_config

# 改預設 port（避免 scan）
Port 2222

# 不允許 root 直接登入
PermitRootLogin no

# 只允許 key 認證
PasswordAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes

# 只允許特定 user
AllowUsers myname admin

# 限制協議版本
Protocol 2

# 連線超時
ClientAliveInterval 300
ClientAliveCountMax 2

# 禁 X11 forwarding（除非需要）
X11Forwarding no

# 禁 agent forwarding（安全考量）
AllowAgentForwarding no
```

reload：

```bash
sudo systemctl restart sshd
```

**改完前另開一個 SSH session 測試**，否則 misconfig 會把自己鎖在外。

## 防 brute force

```bash
sudo apt install fail2ban
# 預設 enable SSH jail
sudo systemctl enable fail2ban
```

failure 5 次 → ban 10 分鐘。

## 一個常見踩雷：「SSH 突然斷線」

可能：

- 網路不穩
- ServerAliveInterval 沒設

config 加：

```
Host *
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

每 60 秒送個 keepalive。

## 一個常見踩雷：「Permission denied (publickey)」

可能：

- 本機沒對應 private key
- server 端 `~/.ssh/authorized_keys` 內容 / 權限錯
- server 端 sshd 設定不允許 key auth

debug：

```bash
ssh -v user@server
# 看詳細失敗原因
```

## 一個常見踩雷：「私鑰權限太鬆 → SSH 拒絕用」

```bash
$ ssh user@server
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@         WARNING: UNPROTECTED PRIVATE KEY FILE!          @
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
Permissions 0644 for '~/.ssh/id_ed25519' are too open.
```

修：

```bash
chmod 600 ~/.ssh/id_ed25519
chmod 700 ~/.ssh
```

## 動手練習

**1. 設多 key + config**

生 3 個 key，寫 ~/.ssh/config，每個用不同 user / server。

**2. ProxyJump**

如果有 bastion 場景，設 ProxyJump，1 個 ssh 命令直達內網。

**3. Local forwarding 練習**

連 vps 後 forward 一個 port（如 nginx 80）：

```bash
ssh -L 8080:localhost:80 user@vps
# 本機 browser 開 http://localhost:8080
```

**4. SOCKS5 proxy**

```bash
ssh -D 1080 user@vps
# Firefox 設 SOCKS5 → 看 IP 變 vps
```

**5. rsync 同步**

```bash
# 第一次同步整個資料夾
rsync -av --progress dir/ user@vps:/path/

# 後續只送變更
rsync -av dir/ user@vps:/path/
```

## 自我檢核

- [ ] 多 key 管理 + ~/.ssh/config 設熟
- [ ] 用過 ProxyJump
- [ ] LocalForward / RemoteForward / DynamicForward 都試過
- [ ] rsync 用得順
- [ ] SSH server 設安全配置
- [ ] 知道 fail2ban 怎麼配

下一章看 VPS 安全配置完整指南。

→ [Ch 35 VPS 安全配置](./35-vps-security.md)
