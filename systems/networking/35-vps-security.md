# Ch 35 — VPS 安全加固

> **目標**：把 VPS 的安全加固做成一套完整流程——SSH 加固（關閉密碼登入/root 登入/改 port）、防火牆（Ch 18/19 的實戰）、fail2ban（自動封鎖攻擊者）、自動安全更新、最小化攻擊面。Ch 33 你看到 VPS 一上線就被攻擊，這章教你怎麼讓它在公網叢林裡生存。這綜合了 Ch 12（SSH）、Ch 18（防火牆）、Ch 28（使用者權限）的知識成「實際的伺服器加固」。

> **環境**：VPS（Ubuntu/Debian），已完成 Ch 33 初始設定。需要 sudo。

## 為什麼安全加固是必須的？

Ch 33 你親眼看到——VPS 一上線就被世界各地的機器人瘋狂攻擊（暴力破解 SSH、掃描漏洞）。這不是「可能被攻擊」，是「正在被攻擊」。一台沒加固的 VPS（弱密碼、開著一堆服務、沒防火牆）可能幾小時內就被攻破。

安全加固就是把這些攻擊面關閉/最小化，讓你的 VPS 能在公網生存。這章把前面學的安全知識（SSH 金鑰、防火牆、最小權限）組合成**一套實際的加固流程**。完成後你的 VPS 從「裸奔」變成「有基本防護」——這是部署任何服務（Ch 36）的前提。安全不是部署完才考慮的，是部署前的基礎。

## 先建立直覺:減少攻擊面 + 增加成本

```
VPS 安全的兩大原則：

  1. 減少攻擊面（attack surface）：
     攻擊面 = 「攻擊者能嘗試的入口」
     關閉不需要的服務/port → 入口變少 → 難攻擊
     例：只開 SSH 和必要的服務，其他全擋（Ch 18 白名單）
        │
  2. 增加攻擊成本：
     讓攻擊變得「不划算」（成本 > 收益）
     - 金鑰登入（密碼破解不了）
     - fail2ban（多次失敗就封鎖）
     - 改 SSH port（減少自動掃描的噪音）
        │
  → 安全不是「絕對防禦」（不存在），是
    「減少入口 + 提高成本」讓攻擊者轉向更軟的目標
    （公網上有無數目標，你只要不是最軟的那個）
        │
  分層防禦（defense in depth）：
    多道防線，一道破了還有下一道
    SSH 加固 + 防火牆 + fail2ban + 更新 + 最小權限
```

關鍵心智：VPS 安全的兩大原則是「**減少攻擊面**」（關閉不需要的入口）和「**增加攻擊成本**」（讓攻擊不划算）。安全不是「絕對防禦」（不存在），而是「減少入口 + 提高成本」讓攻擊者轉向更軟的目標。配合**分層防禦**（多道防線：SSH 加固 + 防火牆 + fail2ban + 更新）。

> 這章綜合 Ch 12（SSH 金鑰）、Ch 18/19（防火牆）、Ch 28（使用者/最小權限）。如果這些不熟，回看對應章節。本章是把它們組合成「實際的 VPS 加固」。

## SSH 加固

SSH 是主要入口（Ch 33 看到它被狂攻），加固它是第一優先：

```bash
# === 編輯 SSH 設定 ===
sudo nano /etc/ssh/sshd_config
# 關鍵設定（改這些）：

# 1. 關閉密碼登入（最重要！只允許金鑰，Ch 12）
PasswordAuthentication no
# → 暴力破解完全失效（沒密碼可破）

# 2. 關閉 root 直接登入（用非 root 使用者 + sudo，Ch 28/33）
PermitRootLogin no

# 3. 改 SSH port（減少自動掃描噪音，非必須但有用）
Port 2222
# → 大部分機器人只掃 22，改 port 大幅減少攻擊嘗試

# 4. 其他加固
PubkeyAuthentication yes           # 確保金鑰登入開著
PermitEmptyPasswords no            # 不允許空密碼
MaxAuthTries 3                     # 限制嘗試次數
AllowUsers deploy                  # 只允許特定使用者登入
UseDNS no                          # 加速登入（Ch 34）

# === 套用（重啟 SSH）===
sudo systemctl restart sshd
# ★ 重啟前「先在另一個終端機測試新設定能登入」！（別鎖死自己）
# ssh -p 2222 deploy@IP   測試成功再放心

# 如果改了 port，記得防火牆也要開新 port（下節）！
```

```
SSH 加固的優先順序：
  1. 金鑰登入 + 關閉密碼（PasswordAuthentication no）★★★ 最重要
  2. 關閉 root 登入（PermitRootLogin no）★★
  3. 改 port（Port 2222）★ 減少噪音（非安全核心）
  4. AllowUsers 白名單 ★
        │
  → 「關閉密碼登入」是最關鍵的一步
    它讓 Ch 33 看到的暴力破解「完全無效」
```

> **「關閉密碼登入（`PasswordAuthentication no`）」是 SSH 加固最關鍵的一步——它讓 Ch 33 看到的暴力破解完全失效**。Ch 33 你看到 VPS 被瘋狂的密碼暴力破解攻擊。**金鑰登入 + 關閉密碼登入**直接讓這些攻擊**無效**——沒有密碼可破解（只接受金鑰，而金鑰幾乎不可能暴力破解）。這是 SSH 加固的第一優先。其次是**關閉 root 直接登入**（`PermitRootLogin no`，Ch 28/33——攻擊者連 root 都登不了，要先攻破非 root 使用者再提權，多一道關卡）。**改 SSH port**（`Port 2222`）不是安全核心（不能防針對性攻擊），但能**大幅減少自動掃描的噪音**（大部分機器人只掃 22，改 port 後攻擊嘗試驟減，log 乾淨很多）。`AllowUsers`（白名單只允許特定使用者）、`MaxAuthTries`（限制嘗試）是額外加固。**鐵律：改 SSH 設定後，重啟前先在「另一個終端機」測試新設定能登入**（和 Ch 33/18 的「留後路」一樣——改 port 還要先開防火牆，否則重啟後鎖死自己）。完成 SSH 加固，你的主要入口就從「被狂攻的弱點」變成「攻不破的鐵門」。這是 VPS 安全最重要的一步。

## 防火牆:只開需要的

```bash
# === 用 ufw（簡單的 iptables 前端）或直接 iptables/nftables（Ch 18/19）===
# ufw 適合快速設定，底層是 iptables

# 預設策略：拒絕入站、允許出站（白名單，Ch 18）
sudo ufw default deny incoming
sudo ufw default allow outgoing

# 開放需要的 port（★ 先開 SSH 再 enable，別鎖死！Ch 18）
sudo ufw allow 2222/tcp            # SSH（如果改了 port，開新的！）
sudo ufw allow 80/tcp             # HTTP（如果跑網站）
sudo ufw allow 443/tcp            # HTTPS
sudo ufw allow 51820/udp          # WireGuard（如果架 VPN，Ch 24）

# 啟用
sudo ufw enable                    # ★ 確認 SSH port 開了才 enable！
sudo ufw status verbose            # 看規則

# === 進階：限制 SSH 來源（如果你 IP 固定）===
sudo ufw allow from 1.2.3.4 to any port 2222   # 只允許你的 IP 連 SSH
# → 連 SSH 都白名單，最安全（但你 IP 變動就麻煩）

# 用 iptables/nftables 做同樣的事（Ch 18/19 的知識）
# 記得規則持久化（Ch 18，重開機別消失）
```

> **防火牆的「預設拒絕入站 + 只開需要的 port」是減少攻擊面的核心——但別忘了先開 SSH 再 enable**。防火牆（Ch 18/19）是「減少攻擊面」的主力——**預設拒絕所有入站**（`default deny incoming`），**只開明確需要的 port**（SSH、HTTP/HTTPS、VPN…）。這樣攻擊者只有這幾個入口能嘗試（其他全擋），而非整台機器的所有 port。`ufw` 是 iptables 的簡單前端（適合快速設定，底層還是 Ch 18 的 iptables），也能直接用 iptables/nftables。**鐵律（和 Ch 18 一樣）：先 `allow` SSH port 再 `enable`**——否則 enable 後 SSH 被擋，你就鎖死自己（尤其如果改了 SSH port，要開**新 port** 不是 22！這是改 port 後最常見的鎖死災難）。進階：如果你的來源 IP 固定，`ufw allow from 你的IP to any port 2222`（連 SSH 都白名單，只有你能連，最安全，但 IP 變動就要改）。記得**規則持久化**（Ch 18，ufw 自動持久，iptables 要 save）。防火牆 + SSH 加固是 VPS 安全的兩大支柱——前者減少入口、後者守住主要入口。`ufw status verbose` 隨時檢查你開了哪些 port（定期審視——有沒有不小心開了不該開的，如資料庫 port 暴露到公網，Ch 13 的危險）。

## fail2ban:自動封鎖攻擊者

```bash
# fail2ban：監測 log，多次失敗就「自動封鎖」那個 IP
sudo apt install fail2ban

# 基本設定（/etc/fail2ban/jail.local）
sudo tee /etc/fail2ban/jail.local > /dev/null <<'EOF'
[DEFAULT]
bantime = 1h                      # 封鎖 1 小時
findtime = 10m                    # 10 分鐘內
maxretry = 5                      # 失敗 5 次就封

[sshd]
enabled = true
port = 2222                       # 你的 SSH port（改了要對應）
EOF

sudo systemctl restart fail2ban

# 看 fail2ban 的戰績
sudo fail2ban-client status sshd
# Currently banned: ... （看封了多少攻擊 IP）
sudo fail2ban-client status         # 所有 jail

# 解封某 IP（如果誤封自己）
# sudo fail2ban-client set sshd unbanip 1.2.3.4
```

> **fail2ban 監測 log、自動封鎖反覆攻擊的 IP——它是「增加攻擊成本」的自動化防線**。即使關閉了密碼登入（攻擊無法成功），攻擊嘗試還是會持續（消耗資源、塞滿 log）。**fail2ban** 監測 SSH（和其他服務）的 log，當某 IP 在短時間內多次失敗（如 10 分鐘內 5 次），就**自動用防火牆封鎖**那個 IP 一段時間。這自動化地「增加攻擊成本」——攻擊者試幾次就被封，無法持續轟炸。`fail2ban-client status sshd` 看它封了多少 IP（你會驚訝有多少攻擊被擋）。fail2ban 不只防 SSH，也能防其他服務（nginx、郵件等）的攻擊（暴力破解、掃描）。它是分層防禦的一環——即使前面的防線（金鑰登入）讓攻擊無法成功，fail2ban 進一步減少噪音和資源消耗。注意設定 SSH port 要對應（改了 port 要在 jail.local 改）。誤封自己時用 `unbanip` 解。fail2ban 是 VPS 加固的標準組件——它讓你的 log 乾淨、減少攻擊噪音、自動處理反覆攻擊者，是「設好就忘」的有效防護。

## 自動更新與最小化

```bash
# === 自動安全更新（修補已知漏洞，重要！）===
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades   # 啟用
# → 自動安裝安全更新（漏洞被修補，不用手動盯著）

# === 最小化攻擊面：關閉不需要的服務 ===
# 看開了哪些服務在聽（Ch 13）
sudo ss -tlnp
# → 檢查每個監聽的 port：這個服務需要嗎？該對外嗎？
# 關閉/移除不需要的服務
# sudo systemctl disable --now <不需要的服務>

# === 檢查：有沒有服務不小心對外（Ch 13 的危險）===
sudo ss -tlnp | grep '0.0.0.0\|:::'
# → 對外監聽的服務，每個都該是「故意對外」的
#   資料庫（5432/3306/6379/27017）絕不該對外！（綁 127.0.0.1）

# === 定期檢查 log（Ch 31 的攻防意識）===
sudo grep "Failed\|Invalid" /var/log/auth.log | tail   # SSH 攻擊
sudo journalctl -u sshd --since today                   # SSH 服務 log
```

> **自動安全更新 + 「檢查沒有服務不小心對外」是容易忽略卻關鍵的加固——資料庫暴露到公網是資料外洩的頭號原因**。**自動安全更新**（`unattended-upgrades`）修補已知漏洞——很多入侵是利用「已被修補但你沒更新」的舊漏洞，自動更新讓這個攻擊面持續關閉（不用手動盯著）。**最小化攻擊面**——用 `ss -tlnp`（Ch 13）檢查「開了哪些服務在聽」，關閉不需要的（每個對外服務都是潛在入口）。最關鍵的檢查：**有沒有服務不小心監聽 0.0.0.0（對外）**——特別是**資料庫**（PostgreSQL 5432、MySQL 3306、Redis 6379、MongoDB 27017）**絕不該對外**（綁 127.0.0.1 或內網，Ch 13）！無數的資料外洩事件就是因為資料庫不小心聽了 0.0.0.0 又沒防火牆——攻擊者掃到開放的資料庫 port，直接連進去拖走所有資料。`ss -tlnp | grep 0.0.0.0` 檢查所有對外服務，確認每個都是「故意對外」的。定期檢查 log（auth.log 的攻擊、服務 log）培養 Ch 31 的攻防意識。這些加固加上 SSH 加固、防火牆、fail2ban，組成完整的 VPS 安全基線。記住安全是**持續的**（定期更新、定期檢查），不是一次設好就忘。完成這套加固，你的 VPS 能在公網叢林裡安全運行，可以放心部署服務（Ch 36）。

## 故意弄壞:驗證加固生效

```bash
# 驗證加固確實生效（從攻擊者視角測試）

# 1. 密碼登入真的關了嗎？
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no deploy@IP -p 2222
# Permission denied (publickey)   ← 密碼登入被拒（只接受金鑰）✓

# 2. root 真的不能登入嗎？
ssh root@IP -p 2222
# Permission denied   ← root 登入被拒 ✓

# 3. 防火牆真的擋了嗎？（從外部掃描，Ch 17）
nmap -p 22,80,443,2222,3306 your-vps-ip
# 22/tcp    filtered   ← 舊 SSH port 被擋（改了 port）
# 2222/tcp  open       ← 新 SSH port 開著
# 3306/tcp  filtered   ← 資料庫被擋（沒對外）✓

# 4. fail2ban 真的封人嗎？
sudo fail2ban-client status sshd   # 看 banned IP 列表

# 5. 加固前後對比 auth.log
sudo grep "Failed password" /var/log/auth.log | wc -l   # 失敗嘗試（加固後攻擊無效）
# → 攻擊還在（機器人不知道你加固了），但「無法成功」
```

> **用 nmap 從外部掃描自己的 VPS、驗證加固生效——這是「從攻擊者視角」確認防護的關鍵步驟**。加固後不要假設它生效了，要**驗證**：(1) **密碼登入真的關了**——`ssh -o PreferredAuthentications=password` 強制密碼登入應被拒（Permission denied publickey）；(2) **root 真的不能登入**；(3) **防火牆真的擋了**——用 `nmap`（Ch 17）從**外部**掃描你的 VPS，確認只有該開的 port 是 open、其他（特別是資料庫 port）是 filtered；(4) **fail2ban 真的封人**。這種「從攻擊者視角驗證」是專業的做法——你站在攻擊者的位置，確認他們真的進不來。特別是 `nmap your-vps` 看「外部能看到哪些 port」——這直接顯示你的攻擊面（open 的 port 就是入口，每個都該是故意開的）。如果掃到不該開的（如 3306 資料庫 open），就發現了漏洞（Ch 13 的危險）。加固後 auth.log 還是有攻擊嘗試（機器人不知道你加固了，繼續試），但它們**無法成功**（金鑰登入擋住了）——這就是加固的效果：攻擊持續但無效。驗證讓你確信防護到位，而非盲目信任。這也呼應 Ch 31 的攻防意識——你要像對手一樣思考，才能確保防護有效。完成驗證，你的 VPS 真正安全了，可以部署服務。

## 動手練習

1. SSH 加固：關閉密碼登入和 root 登入、改 port（先測試能登入再重啟）

2. 防火牆：設預設拒絕入站、只開 SSH/HTTP/HTTPS（先開 SSH 再 enable）

3. fail2ban：裝並設定，過一陣子看 `fail2ban-client status sshd` 的戰績

4. 最小化：`ss -tlnp` 檢查對外服務，確認沒有資料庫等敏感服務暴露

5. 跑「故意弄壞」：用 nmap 從外部掃自己的 VPS，驗證加固生效（密碼登入關了、防火牆擋了）

## 本章重點整理

- 安全兩原則：減少攻擊面（關不需要的入口）+ 增加攻擊成本（讓攻擊不划算）；分層防禦
- SSH 加固（最關鍵）：關閉密碼登入（讓暴力破解無效）+ 關 root 登入 + 改 port（減噪音）+ AllowUsers
- 防火牆：預設拒絕入站、只開需要的 port（先開 SSH 再 enable，別鎖死）；持久化
- fail2ban 自動封鎖反覆攻擊的 IP（增加攻擊成本）；自動安全更新修補漏洞
- 最小化：ss -tlnp 檢查對外服務，資料庫絕不對外（綁 127.0.0.1）；用 nmap 從外部驗證加固

## 自我檢核

- [ ] 能做完整的 SSH 加固（關密碼/root/改 port），知道為什麼關密碼登入最關鍵
- [ ] 會設防火牆白名單，知道怎麼避免鎖死自己
- [ ] 知道 fail2ban 和自動更新的作用
- [ ] 會檢查並確保敏感服務（資料庫）不對外
- [ ] 會用 nmap 從外部驗證加固生效

## 延伸閱讀

### 官方教學

- **[Securing SSH](https://www.digitalocean.com/community/tutorials/how-to-harden-openssh-on-ubuntu-20-04)** + **[ufw 設定](https://www.digitalocean.com/community/tutorials/how-to-set-up-a-firewall-with-ufw-on-ubuntu-22-04)** — DigitalOcean
  - **讀哪裡**：SSH 加固和防火牆設定那幾節
  - **為什麼值得讀**：本章 SSH/防火牆加固的標準教學

### 文章

- **[Linux server security checklist](https://github.com/imthenachoman/How-To-Secure-A-Linux-Server)** — imthenachoman
  - **這篇說什麼**：極詳盡的 Linux 伺服器加固清單
  - **讀哪裡**：SSH、防火牆、fail2ban 那幾節
  - **為什麼值得讀**：本章加固的完整擴充版，可當 checklist

### 工具 / 標準

- **[CIS Benchmarks](https://www.cisecurity.org/cis-benchmarks)** — Center for Internet Security
  - **為什麼值得讀**：業界標準的系統加固基準，深入安全的權威來源

下一章是 Part 8 的高潮——用 nginx 部署一個真正的服務（reverse proxy + HTTPS），把你的 VPS 變成一台對外提供服務的伺服器。

→ [Ch 36 用 nginx 部署服務](./36-nginx-deploy.md)
