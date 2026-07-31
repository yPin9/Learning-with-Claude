# Ch 33 — 買 VPS 與初始設定

> **目標**：實際買一台 VPS 並完成初始設定——選商家/規格/地點、建立、第一次 SSH 登入、建立非 root 使用者、部署 SSH 金鑰、基本系統設定（更新/時區/主機名）。完成後你真正擁有一台公網伺服器，為後面的 SSH 加固（Ch 34）、安全（Ch 35）、部署（Ch 36）打基礎。這章從「概念」走到「真的有一台機器」。

> **環境**：任一 VPS 商（DigitalOcean/Vultr/Linode 等），Ubuntu 22.04+ / Debian 12+。需要信用卡（~$5/月）。

## 為什麼要真的買一台？

前面的概念和 netns 實驗都很好，但「真的擁有一台公網伺服器」是質變——你會體驗真實的公網環境（被掃描攻擊、真實的延遲、真實的 IP）、把 VPN/服務真的部署上去、學到「紙上談兵學不到」的運維。

這章帶你從零買一台並設定好。不用怕——入門 VPS 每月幾美元，搞砸了刪掉重建（VPS 的好處：完全可拋棄）。完成後你有一台屬於自己的伺服器，後面的所有實務（SSH 加固、安全、部署 HTTPS、架 VPN）都在它上面做。這是 Part 8 從理論走向實戰的起點。

## 先建立直覺:VPS 的生命週期

```
從買 VPS 到能用的流程：

  1. 註冊 VPS 商 + 付款
        │
  2. 建立 VPS（選規格/地點/OS）
     → 商家給你：IP、root 密碼（或你預先放的 SSH 金鑰）
        │
  3. 第一次 SSH 登入（用 root）
        │
  4. 初始設定（安全的起點）：
     - 更新系統
     - 建立非 root 使用者（不要一直用 root！）
     - 部署 SSH 金鑰（Ch 12）
     - 設時區/主機名
        │
  5. 之後：SSH 加固（Ch 34）、安全（Ch 35）、部署（Ch 36）
        │
  → 重點：建立後「立刻」做安全的初始設定
    因為一上線就被攻擊（Ch 32）
```

關鍵心智：VPS 的生命週期是「建立 → 第一次 root 登入 → **立刻做安全的初始設定**（更新、建非 root 使用者、SSH 金鑰）→ 部署服務」。關鍵是「立刻」——因為 VPS 一上線就被掃描攻擊（Ch 32），初始設定要在攻擊得逞前完成。

> 這章用 Ch 12 的 SSH 金鑰知識。如果對 SSH 金鑰認證不熟，回看 [Ch 12](./12-ssh-and-others.md)。初始設定是 Ch 34（SSH 加固）和 Ch 35（安全）的起點。

## 選擇與建立 VPS

```
選 VPS 的決策（Ch 32 的考量落地）：

  商家：
    新手推薦：Vultr / DigitalOcean / Linode（穩定、介面友善、文件好）
    更便宜：Hetzner（歐洲，CP 值高）、各種小廠（風險自負）
        │
  規格（個人用途）：
    最小方案：1 vCPU / 1GB RAM / 25GB SSD / ~$5/月
    （夠跑 VPN + 小網站；要跑更多再升級）
        │
  地點：
    離你近（低延遲）或目標用途的地點
        │
  虛擬化：KVM（不要 OpenVZ，Ch 32）
        │
  OS：Ubuntu 22.04 LTS 或 Debian 12（穩定、文件多、本課基準）
        │
  ★ 建立時「就放 SSH 公鑰」（很多商家支援）
    → 一開始就用金鑰登入，不用 root 密碼（更安全）
```

```bash
# 建立 VPS 前，先在「本機」準備好 SSH 金鑰（Ch 12）
ssh-keygen -t ed25519 -C "vps-key"
# 產生 ~/.ssh/id_ed25519（私鑰）和 .pub（公鑰）
cat ~/.ssh/id_ed25519.pub
# 把這個公鑰「貼到 VPS 商的控制台」（建立時的 SSH key 欄位）
# → 這樣建立的 VPS 一開始就能用金鑰登入（不用 root 密碼）

# 建立後，商家給你 VPS 的 IP（如 192.0.2.123）
```

> **建立 VPS 時「就放 SSH 公鑰」，讓它從第一秒就用金鑰登入——這是安全起步的關鍵**。大多數 VPS 商在建立時讓你貼 SSH 公鑰（Ch 12）——**務必用這個**，而不是讓商家給你 root 密碼。原因：(1) 密碼登入不安全（會被暴力破解，Ch 32 的公網攻擊）；(2) 一開始就金鑰登入，省去後面換的麻煩；(3) 金鑰更方便（不用記密碼）。所以流程是：**先在本機 `ssh-keygen` 產生金鑰對**（Ch 12，用 ed25519）→ 把**公鑰**貼到 VPS 商控制台 → 建立 VPS → 它從第一秒就只接受你的金鑰。選擇上：新手用 Vultr/DigitalOcean/Linode（穩定、文件好、介面友善），規格選最小方案（1核1GB ~$5/月夠用），**虛擬化選 KVM**（Ch 32，能架 VPN）、**OS 選 Ubuntu 22.04 LTS 或 Debian 12**（本課基準、穩定、文件多）、**地點選離你近的**（低延遲，Ch 16）。建立後商家給你 IP。注意 LTS（長期支援）版本比較穩定（適合伺服器），別選非 LTS 的新版（更新頻繁、支援期短）。

## 第一次登入與初始設定

```bash
# === 第一次 SSH 登入（用建立時放的金鑰）===
ssh root@192.0.2.123             # 用 VPS 的 IP
# 第一次會問 host key（Ch 12 的 TOFU），確認後 yes
# → 登入成功，你在 VPS 的 root shell 了！

# === 初始設定（建議寫成一連串，快速完成）===

# 1. 更新系統（修補已知漏洞，重要！）
apt update && apt upgrade -y

# 2. 設時區和主機名
timedatectl set-timezone Asia/Taipei      # 設時區
hostnamectl set-hostname myvps             # 設主機名

# 3. 建立非 root 使用者（不要一直用 root！）
adduser deploy                             # 建立使用者 deploy（會問密碼）
usermod -aG sudo deploy                    # 加入 sudo 群組（能 sudo）

# 4. 把 SSH 金鑰也給新使用者（讓新使用者能金鑰登入）
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/   # 複製 root 的授權金鑰
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys    # 權限（Ch 12 的 SSH 嚴格權限）

# 5. 測試新使用者能登入（開「另一個」終端機測，別關掉 root！）
# ssh deploy@192.0.2.123
# sudo whoami   → root（確認 sudo 能用）

# 確認新使用者 OK 後，後面就用 deploy（不用 root）
```

```
為什麼建立「非 root 使用者」（Ch 28 的最小權限）：

  一直用 root 的問題：
    - 一個誤操作（rm -rf）就毀掉系統（root 無防護）
    - root 被入侵 = 整台機器完蛋
    - 很多服務不該用 root 跑（Ch 28 最小權限）
        │
  非 root 使用者 + sudo：
    平常用受限的使用者（誤操作影響小）
    需要時用 sudo 提權（Ch 28，有日誌、有確認）
        │
  → 建立後立刻建非 root 使用者，之後用它
    這是 Ch 28（使用者/sudo）的實戰應用
```

> **建立 VPS 後「立刻建非 root 使用者 + 部署金鑰 + 測試能登入」——這是安全運維的標準起步，呼應 Ch 28 的最小權限**。第一次用 root 登入後，**不要一直用 root**——建立一個非 root 使用者（`adduser deploy` + `usermod -aG sudo`，Ch 28），之後平常用它，需要特權時 `sudo`。原因（Ch 28 的最小權限）：root 一個誤操作（`rm -rf`）就毀系統、root 被入侵=整台完蛋、最小權限原則。關鍵步驟是**把 SSH 金鑰也給新使用者**（複製 authorized_keys 並設對權限，Ch 12 的 SSH 嚴格權限——`.ssh` 要 700、authorized_keys 要 600，否則 SSH 拒絕）。**鐵律：測試新使用者能登入前，別關掉 root 的 session**——開「另一個」終端機測 `ssh deploy@IP` + `sudo whoami`，確認能登入且 sudo 能用，再放心。這個「留後路」原則（和 Ch 18 設防火牆別鎖死自己一樣）很重要——如果新使用者設錯卻關了 root，你就鎖死在外面了（雖然 VPS 商有 console 救援，但麻煩）。其他初始設定：**立刻更新系統**（`apt upgrade`，修補已知漏洞——VPS 的 OS image 可能有舊漏洞）、設時區（log 時間正確）、設主機名。這些是 Ch 34（SSH 加固）、Ch 35（安全）之前的基礎準備。完成後你有一台設定好基礎、能用金鑰登入、用非 root 使用者操作的 VPS。

## 故意弄壞:理解 VPS 一上線就被攻擊

```bash
# 在你的 VPS 上，親眼看「公網攻擊」（Ch 32 的真實性）

# 看 SSH 的登入嘗試（攻擊者的暴力破解）
sudo grep "Failed password" /var/log/auth.log | tail -20
# 會看到一堆來自各地 IP 的失敗登入嘗試！
# Failed password for root from 1.2.3.4 ...
# Failed password for admin from 5.6.7.8 ...
#   → 這些是機器人在猜密碼（Ch 32 說的公網叢林）

# 統計攻擊來源（用 linux_commands 課的工具）
sudo grep "Failed password" /var/log/auth.log | grep -oP 'from \K[\d.]+' | sort | uniq -c | sort -rn | head
# 看哪些 IP 攻擊最多（一堆來自世界各地）

# 看嘗試的使用者名（攻擊者猜的）
sudo grep "Failed password" /var/log/auth.log | grep -oP 'for \K\w+' | sort | uniq -c | sort -rn | head
# root, admin, test, user, ubuntu... （常見的猜測目標）

# → 這就是為什麼要：
#   1. 金鑰登入（密碼破解不了，Ch 12）
#   2. 關閉密碼登入（Ch 35）
#   3. 改 SSH port / fail2ban（Ch 35）
#   你的 VPS 從上線那一刻就在這個攻擊洪流裡
```

> **在 VPS 的 auth.log 親眼看到「來自世界各地的暴力破解嘗試」——這讓 Ch 32 的「一上線就被攻擊」變成可見的現實**。VPS 上線後，`grep "Failed password" /var/log/auth.log` 會顯示**一堆失敗的登入嘗試**——來自世界各地 IP 的機器人，不停嘗試用常見密碼登入 root、admin、test 等帳號。這不是你被「針對」——而是公網上無數機器人 24/7 掃描所有 IP、嘗試破解的常態（Ch 32 的「公網叢林」）。用 linux_commands 課的工具統計（`grep | sort | uniq -c | sort -rn`，呼應那課的 log 分析）能看到攻擊來源和被猜的帳號。這個「親眼所見」讓安全的必要性變得具體——這就是為什麼要：**金鑰登入**（Ch 12，密碼破解不了你也沒密碼可破）、**關閉密碼登入**（Ch 35，讓暴力破解完全失效）、**改 SSH port / 裝 fail2ban**（Ch 35，減少攻擊噪音）。你的 VPS 從上線那一刻就在這個攻擊洪流裡，所以初始設定（金鑰登入）+ Ch 34（SSH 加固）+ Ch 35（安全）不是「學術練習」，是「讓你的伺服器不被攻破」的必須。看著 auth.log 裡滾動的攻擊嘗試，你會理解為什麼安全不能等。這也是「真的買一台 VPS」比 netns 實驗更有教育意義的地方——你體驗到真實的對抗。

## 動手練習

1. 準備金鑰：在本機 `ssh-keygen -t ed25519`，準備好公鑰

2. 買一台 VPS：選 Vultr/DO/Linode，最小方案、KVM、Ubuntu 22.04、離你近的地點、建立時放公鑰

3. 第一次登入：`ssh root@IP`，更新系統、設時區/主機名

4. 建非 root 使用者：建 deploy 使用者、加 sudo、部署金鑰、測試登入（留 root 後路）

5. 跑「故意弄壞」：看 auth.log 的攻擊嘗試，統計攻擊來源，體會公網的真實對抗

## 本章重點整理

- VPS 生命週期：建立 → root 登入 → 立刻安全初始設定 → 部署；關鍵是「立刻」（一上線就被攻擊）
- 建立時就放 SSH 公鑰（Ch 12）→ 從第一秒金鑰登入，不用 root 密碼
- 選擇：Vultr/DO/Linode、最小方案 ~$5/月、KVM（不要 OpenVZ）、Ubuntu/Debian LTS、離你近
- 初始設定：更新系統、設時區/主機名、建非 root 使用者+sudo+金鑰（Ch 28 最小權限）、測試前留 root 後路
- auth.log 的攻擊嘗試讓「一上線就被攻擊」變可見——金鑰登入/Ch 35 加固是必須

## 自我檢核

- [ ] 能準備 SSH 金鑰並建立一台 VPS（金鑰登入）
- [ ] 知道初始設定該做什麼（更新/使用者/金鑰/時區）
- [ ] 理解為什麼要建非 root 使用者（Ch 28 最小權限）
- [ ] 知道「測試新使用者前留 root 後路」的重要性
- [ ] 親眼看過 VPS 被攻擊的證據，理解安全的必要性

## 延伸閱讀

### 官方教學

- **[Initial Server Setup with Ubuntu](https://www.digitalocean.com/community/tutorials/initial-server-setup-with-ubuntu-22-04)** — DigitalOcean
  - **讀哪裡**：整篇（建使用者、金鑰、防火牆）
  - **為什麼值得讀**：VPS 初始設定的標準教學，本章的權威實作版

### 文章

- **[My First 5 Minutes on a Server](https://www.lebsanft.org/?p=386) / 各種 server hardening 入門**
  - **這篇說什麼**：剛建好伺服器該立刻做的安全設定
  - **為什麼值得讀**：「立刻安全初始設定」的具體清單，連接 Ch 34/35

下一章深入 SSH——它是你管理 VPS 的命脈，這章把 SSH 從基礎用法推到進階（config、多金鑰、agent、進階 tunnel），讓你高效安全地管理伺服器。

→ [Ch 34 SSH 完整](./34-ssh-complete.md)
