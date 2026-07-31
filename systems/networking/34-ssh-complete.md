# Ch 34 — SSH 完整

> **目標**：把 SSH 從基礎推到精通——`~/.ssh/config`（讓 SSH 管理變優雅）、多金鑰管理、ssh-agent（金鑰免重複輸密碼）、進階 tunnel（跳板/ProxyJump）、SCP/SFTP/rsync 傳檔、SSH 的安全選項。SSH 是你管理 VPS 的命脈（每天用），這章讓你高效又安全地用它。Ch 12 講了 SSH 原理，這章是實戰精通。

> **環境**：Linux/Mac（OpenSSH）。VPS 管理場景。

## 為什麼 SSH 值得深入？

你會用 SSH 管理 VPS（Ch 33）——每天登入、傳檔、執行命令。基礎的 `ssh user@host` 能用，但管理多台機器、用跳板、自動化時，基礎用法很笨拙。SSH 有一整套讓管理變優雅的功能（config、agent、ProxyJump），掌握它們大幅提升效率。

更重要的是安全——SSH 是 VPS 的主要入口（Ch 33 看到它被瘋狂攻擊），正確設定 SSH 是 VPS 安全的核心（Ch 35 會深入）。這章把 SSH 的實戰功能講透，讓你從「會 ssh 登入」進化到「優雅高效安全地管理伺服器群」。Ch 12 是原理，這章是精通。

## SSH config:管理的優雅之道

```bash
# 沒有 config 的痛苦：每次打一長串
ssh -i ~/.ssh/vps_key -p 2222 deploy@192.0.2.123    # 每次都要記/打這些

# === ~/.ssh/config：把連線設定存起來 ===
cat > ~/.ssh/config <<'EOF'
Host myvps                          # 別名（之後 ssh myvps 就好）
    HostName 192.0.2.123            # 真實 IP
    User deploy                     # 使用者
    Port 2222                       # SSH port（如果改了，Ch 35）
    IdentityFile ~/.ssh/vps_key     # 用哪個金鑰

Host work
    HostName work.example.com
    User alice
    IdentityFile ~/.ssh/work_key

# 萬用設定（套用到所有 host）
Host *
    ServerAliveInterval 60          # 每 60 秒送 keepalive（防 NAT 斷線，Ch 8）
    ServerAliveCountMax 3
    AddKeysToAgent yes              # 自動加金鑰到 agent
EOF
chmod 600 ~/.ssh/config

# 現在登入超簡單
ssh myvps                          # 等於那一長串！
scp file.txt myvps:/tmp/           # scp 也認 config 的別名
```

> **`~/.ssh/config` 是 SSH 管理的核心——把每台機器的設定存成別名，從此 `ssh myvps` 取代一長串參數**。管理多台機器時，每次打 `ssh -i key -p port user@host` 很痛苦。`~/.ssh/config` 讓你為每台機器定義別名和設定——之後 `ssh myvps` 就自動用對的 IP、使用者、port、金鑰。這不只省打字，還讓 scp/rsync/git 都能用這些別名。`Host *`（萬用）設定套用到所有機器——常用的有 `ServerAliveInterval 60`（每 60 秒送 keepalive，防止 NAT 表過期斷線，Ch 8 的問題在 SSH 的解法）、`AddKeysToAgent yes`（自動管理金鑰）。config 還支援萬用比對（`Host *.example.com`）、繼承、各種選項。這是從「會用 SSH」到「優雅管理伺服器群」的關鍵工具——專業的運維都重度使用 ssh config。記得 config 檔權限要 600（Ch 12 的 SSH 嚴格權限）。掌握 config，你管理 10 台機器和 1 台一樣輕鬆。

## ssh-agent:金鑰免重複輸密碼

```bash
# 問題：金鑰加了密碼保護（好習慣），但每次登入都要輸 → 煩
# 解法：ssh-agent 記住解密的金鑰

# 啟動 agent（通常桌面環境自動啟動）
eval "$(ssh-agent -s)"

# 把金鑰加進 agent（輸一次密碼）
ssh-add ~/.ssh/vps_key             # 輸入金鑰密碼一次
ssh-add -l                         # 列出 agent 裡的金鑰

# 之後登入不用再輸金鑰密碼（agent 幫你認證）
ssh myvps                          # 直接登入（agent 提供金鑰）

# agent forwarding（跳板場景，謹慎用）
ssh -A myvps                       # 把本地 agent「轉發」到遠端
# → 在 myvps 上能用「你本地的金鑰」連其他機器（不用把私鑰放 VPS）
# 注意：agent forwarding 有安全風險（遠端 root 能用你的 agent）
#       現代建議用 ProxyJump 取代（下節）
```

> **ssh-agent 讓「加密碼保護的金鑰」也能方便使用——輸一次密碼，之後 agent 幫你認證**。好習慣是金鑰加密碼保護（Ch 12，雙因素：有私鑰檔+知道密碼），但每次登入都輸密碼很煩。**ssh-agent** 解決——`ssh-add` 把金鑰加進 agent（輸一次密碼解密），之後 agent 在記憶體裡保管解密的金鑰，登入時自動提供，不用再輸。這兼得「金鑰加密碼的安全」和「不用重複輸的方便」。**agent forwarding**（`ssh -A`）能把本地 agent 轉發到遠端——讓你在 VPS 上用「本地的金鑰」連其他機器（不用把私鑰複製到 VPS，私鑰留在本地更安全）。但 agent forwarding **有安全風險**（遠端的 root 能透過你轉發的 agent 用你的金鑰）——**現代建議用 ProxyJump（下節）取代**它，更安全。ssh-agent 是日常管理的便利工具，配合 config 的 `AddKeysToAgent yes` 自動化。理解它，你的金鑰既安全（有密碼）又好用（不用重複輸）。

## 進階 tunnel:跳板與 ProxyJump

```bash
# === ProxyJump：透過跳板機連內網機器（現代做法）===
# 場景：internal-server 在內網，只能透過 jump-host（跳板）連
ssh -J jump-host internal-server   # -J = ProxyJump，自動透過 jump-host

# 在 config 裡設定（更優雅）
cat >> ~/.ssh/config <<'EOF'
Host internal
    HostName 10.0.0.50              # 內網 IP
    User admin
    ProxyJump jump-host             # 透過 jump-host 跳轉
EOF
ssh internal                       # 自動透過跳板連內網機器

# === 多層跳板 ===
ssh -J jump1,jump2 final-server    # 跳 jump1 → jump2 → final

# === port forwarding（Ch 12 複習 + 實戰）===
# 連遠端內網的資料庫（本地 5432 → 透過 VPS → 內網 db）
ssh -L 5432:db-internal:5432 myvps
# 之後本機 localhost:5432 = 遠端內網的 db

# === 動態 SOCKS proxy（Ch 12/28）===
ssh -D 1080 myvps                  # 本機 1080 = 經 myvps 的 SOCKS proxy

# === 持久化 tunnel（背景跑 + 自動重連）===
ssh -fN -L 5432:db:5432 myvps      # -f 背景 -N 不開 shell
# 或用 autossh（斷線自動重連）
# autossh -M 0 -fN -L 5432:db:5432 myvps
```

> **ProxyJump（`-J`）是透過跳板機連內網的現代做法——取代了有安全風險的 agent forwarding**。常見場景：要連的機器在內網（只能透過一台有公網 IP 的「跳板機/bastion」連）。傳統做法是 SSH 到跳板再 SSH 到目標（兩段，麻煩）或用 agent forwarding（有風險）。**ProxyJump** `ssh -J jump-host target` 自動透過跳板連目標——SSH 先連跳板，在跳板上建立到目標的連線，但**認證和加密是端到端的**（跳板看不到你和目標之間的內容，比 agent forwarding 安全）。在 config 裡設 `ProxyJump jump-host` 更優雅（`ssh internal` 自動跳轉），還能多層跳板（`-J jump1,jump2`）。這是企業環境（內網機器透過 bastion 連）的標準做法。配合 Ch 12 的 port forwarding（`-L` 連內網服務、`-D` 當 SOCKS proxy）——這些 tunnel 是日常運維的利器（連雲端 VPC 內的資料庫、把 VPS 當跳板）。`-fN`（背景+不開 shell）做持久 tunnel，`autossh` 能斷線自動重連（長期 tunnel 用）。掌握 ProxyJump 和 tunnel，你能優雅地穿透複雜的網路拓樸管理機器。

## 傳檔:scp / sftp / rsync

```bash
# === scp：簡單複製（Ch 12 複習）===
scp file.txt myvps:/tmp/                    # 本地 → 遠端
scp myvps:/var/log/app.log .                # 遠端 → 本地
scp -r mydir/ myvps:/opt/                   # 遞迴複製目錄

# === rsync：高效同步（增量，推薦大量/重複傳輸）===
rsync -avz mydir/ myvps:/opt/mydir/         # 同步目錄（增量）
#   -a 保留屬性, -v 詳細, -z 壓縮
rsync -avz --delete mydir/ myvps:/opt/mydir/  # --delete：刪除目標多餘的（完全同步）
rsync -avz --progress bigfile myvps:/tmp/   # 顯示進度

# rsync 的優勢（vs scp）：
#   - 增量（只傳變動的部分，重複同步快）
#   - 中斷可續傳（--partial）
#   - 能用 --exclude 排除檔案

# === sftp：互動式傳檔 ===
sftp myvps
# sftp> put file.txt    上傳
# sftp> get remote.txt  下載
# sftp> ls / cd / mkdir 等
```

> **rsync（增量同步）比 scp（每次全傳）高效——部署和備份的首選**。`scp` 簡單（一次性複製），但每次都**全部重傳**。`rsync` 是**增量同步**——只傳「變動的部分」，所以重複同步（如反覆部署更新的程式碼、定期備份）快很多。`rsync -avz`（保留屬性+詳細+壓縮）是常用組合，`--delete`（刪除目標多餘檔案，完全鏡像）、`--progress`（看進度）、`--exclude`（排除某些檔案，如 .git/node_modules）都很實用。rsync 還能**中斷續傳**（`--partial`，大檔案傳輸中斷不用重來）。它走 SSH（加密），所以一樣安全，且認 ssh config 的別名。實務上：**部署程式碼用 rsync**（增量快、能排除不需要的）、**備份用 rsync**（增量、能鏡像，呼應 linux_commands 課的備份練習）、**一次性傳單檔用 scp**（簡單）。`sftp` 是互動式傳檔（像 FTP 但加密）。掌握這些，你能高效地在本地和 VPS 之間傳輸——這是部署服務（Ch 36）和維護的日常。

## 故意弄壞:SSH 連線問題進階排查

```bash
# SSH 進階 debug（Ch 12 基礎的延伸）

# 1. 超詳細 debug（-vvv 看每一步）
ssh -vvv myvps 2>&1 | less
# 看認證過程、金鑰嘗試、協商的每一步（debug 認證/連線問題）

# 2. 測試 config 解析（看 SSH 實際會用什麼設定）
ssh -G myvps                       # 印出對 myvps 解析出的所有設定
# 確認 HostName/User/Port/IdentityFile 是你預期的

# 3. 金鑰問題（Ch 12 的權限 + 進階）
ssh-add -l                         # agent 裡有金鑰嗎？
ssh -i ~/.ssh/specific_key myvps   # 強制用特定金鑰（繞過 agent/config）

# 4. 連線卡住/慢（DNS 反解問題）
# SSH 登入慢常是伺服器端的 DNS 反解（UseDNS）
# 伺服器 /etc/ssh/sshd_config: UseDNS no （加速登入）

# 5. 「too many authentication failures」
# agent 裡金鑰太多，SSH 一個個試超過上限
ssh -o IdentitiesOnly=yes -i ~/.ssh/correct_key myvps  # 只用指定的金鑰

# 6. host key 改變（Ch 12，重裝 VPS 後常見）
# ssh-keygen -R myvps    # 清掉舊的 host key 記錄（確認是預期的重裝）
```

> **`ssh -vvv`（超詳細）和 `ssh -G`（看解析的設定）是 SSH 進階 debug 的兩大利器**。當 SSH 連線/認證出問題，`ssh -vvv myvps` 顯示**每一步的細節**——它嘗試哪些金鑰、config 怎麼解析、協商了什麼、卡在哪——是 debug 的終極工具（雖然輸出多，但問題的線索都在裡面）。`ssh -G myvps` 顯示「對這個 host **實際解析出的所有設定**」——確認 HostName/User/Port/IdentityFile 是你預期的（debug「config 沒生效」「連到錯的地方」）。常見進階問題：(1) **「too many authentication failures」**——agent 裡金鑰太多，SSH 一個個試超過伺服器上限，用 `-o IdentitiesOnly=yes -i 正確金鑰` 只試指定的；(2) **登入慢**——常是伺服器端的 DNS 反解（`UseDNS no` 加速）；(3) **host key 改變**（Ch 12，重裝 VPS 後）——確認是預期的重裝再 `ssh-keygen -R`。這些進階 debug 技巧讓你解決 Ch 12 基礎之外的 SSH 疑難。SSH 是 VPS 管理的命脈，debug 它的能力直接影響你的運維效率。配合 Ch 12 的基礎（權限、refused vs timeout、host key），你能應付幾乎任何 SSH 問題。

## 動手練習

1. 設 config：為你的 VPS 設 ssh config 別名，體會 `ssh myvps` 取代一長串

2. 用 agent：金鑰加密碼保護，用 ssh-add 加進 agent，體會不用重複輸密碼

3. ProxyJump：如果有跳板場景（或用兩台 VPS 模擬），用 `-J` 透過跳板連

4. rsync 同步：用 rsync 同步一個目錄到 VPS，改個檔案再同步，看它只傳變動的

5. 跑「故意弄壞」：用 `ssh -vvv` 看認證過程、`ssh -G` 看解析的設定，熟悉進階 debug

## 本章重點整理

- `~/.ssh/config` 是管理核心：把每台機器設成別名（HostName/User/Port/IdentityFile），`Host *` 套用通用設定
- ssh-agent 讓加密碼的金鑰也方便（輸一次，agent 保管）；ProxyJump（-J）取代有風險的 agent forwarding
- ProxyJump 透過跳板連內網（端到端加密，跳板看不到內容）；port forwarding/-D SOCKS 是日常 tunnel
- rsync（增量同步）比 scp（全傳）高效——部署/備份首選；--delete 鏡像、--exclude 排除
- 進階 debug：`ssh -vvv`（每步細節）、`ssh -G`（看解析設定）、IdentitiesOnly（太多金鑰時）

## 自我檢核

- [ ] 會用 ssh config 管理多台機器，理解 Host 別名和通用設定
- [ ] 知道 ssh-agent 的作用，以及 ProxyJump 為什麼比 agent forwarding 安全
- [ ] 會用 ProxyJump 透過跳板連內網，會用各種 tunnel
- [ ] 知道 rsync 比 scp 好在哪，會用它同步/部署
- [ ] 會用 ssh -vvv / ssh -G 做進階 debug

## 延伸閱讀

### 官方文件

- **[ssh_config(5)](https://man.openbsd.org/ssh_config)** — OpenSSH
  - **讀哪裡**：所有設定選項（Host/ProxyJump/IdentityFile 等）
  - **為什麼值得讀**：ssh config 所有選項的權威

### 文章

- **[SSH config 完整指南](https://www.ssh.com/academy/ssh/config)** — SSH.com
  - **這篇說什麼**：ssh config 的所有用法和範例
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章 config 那節的完整版

- **[rsync 完整教學](https://www.digitalocean.com/community/tutorials/how-to-use-rsync-to-sync-local-and-remote-directories)** — DigitalOcean
  - **這篇說什麼**：rsync 的所有選項和場景
  - **為什麼值得讀**：本章 rsync 那節的擴充

### 書籍

- **《SSH Mastery》— Michael W Lucas**
  - **讀哪幾章**：config、tunnel、金鑰管理那幾章
  - **這本書的定位**：SSH 實戰精通的權威，薄而精

下一章是 VPS 安全的核心——把 SSH 加固、防火牆、fail2ban、自動更新組合成完整的 VPS 安全加固，讓你的伺服器能在公網叢林裡生存。

→ [Ch 35 VPS 安全加固](./35-vps-security.md)
