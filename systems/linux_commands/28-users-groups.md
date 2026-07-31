# Ch 28 — user / group / sudo

> **目標**：理解 Linux 的使用者與權限模型——UID/GID 是什麼（核心只認數字，不認名字）、/etc/passwd 和 /etc/shadow 的結構、real/effective/saved UID 的區別、setuid 機制（為什麼 passwd 能改 /etc/shadow）、sudo 怎麼運作、以及為什麼 root（UID 0）特殊。這把 Ch 7（權限位元）從「檔案層」提升到「使用者與身分」層。

> **環境**：Linux，主流 distro。systemd 系統的 user 管理。

## 為什麼要懂使用者模型？

Ch 7 教了權限位元（rwx），但權限是「給誰」的？答案是使用者（user）和群組（group）。多使用者是 Unix 的根基——一台機器多人用、服務以專屬使用者跑（nginx 用 `www-data`、資料庫用 `postgres`），隔離彼此。

理解使用者模型回答了一堆「為什麼」：為什麼 `passwd` 這個普通命令能改只有 root 能寫的 /etc/shadow（setuid）？為什麼 `sudo` 要打密碼？為什麼 root 是 UID 0 而不是別的？為什麼有些 process 的「擁有者」會變？這些是系統安全的核心，也是資安和滲透測試的基礎（提權攻擊就是在玩 UID）。

## 先建立直覺：核心只認數字

```
使用者的真相：kernel 只認數字（UID/GID），名字是給人看的

  你看到的：     alice, www-data, root
  kernel 看到的：1000,  33,        0
        │
  /etc/passwd 是「數字 ↔ 名字」的對照表（給人類方便）：
    alice:x:1000:1000:Alice:/home/alice:/bin/bash
     名字    UID  GID  描述  家目錄      shell
        │
  檔案的擁有者其實存的是 UID（數字），不是名字：
    ls -l 顯示 "alice" 是 ls 查 /etc/passwd 翻譯的
    inode 裡存的是 1000（Ch 4）
        │
  → 刪掉使用者後，他的檔案會顯示成「1000」（沒人對應那個數字了）
    這證明 kernel 只存數字
```

關鍵心智：kernel 只認 **UID/GID（數字）**，使用者名/群組名是 /etc/passwd 和 /etc/group 提供的「數字↔名字」對照表，純粹給人類方便。檔案的擁有者（inode 裡，Ch 4）存的是 UID 數字。root 之所以是 root，是因為它的 UID 是 **0**——kernel 對 UID 0 特殊對待（跳過所有權限檢查）。

> 如果你對檔案權限位元（rwx、owner/group/other）還不熟，先回看 [Ch 7 — 權限位元與 ownership](./07-permissions.md)。本章是它的「身分層」——權限是給 UID/GID 的。

## /etc/passwd 與 /etc/shadow

使用者資訊存在這兩個檔案，理解它們的結構：

```bash
# /etc/passwd：使用者基本資訊（所有人可讀）
cat /etc/passwd
# root:x:0:0:root:/root:/bin/bash
# daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
# alice:x:1000:1000:Alice Smith:/home/alice:/bin/bash
#  (1)  (2)(3)(4)  (5)         (6)          (7)

# 七個欄位（用 : 分隔，正好用 cut/awk 練習！）
#  1 username   使用者名
#  2 password   x（密碼放 shadow，歷史上放這，不安全）
#  3 UID        使用者數字 ID
#  4 GID        主要群組 ID
#  5 GECOS      描述（全名等）
#  6 home       家目錄
#  7 shell      登入 shell（/usr/sbin/nologin = 不能登入）

# 用 Ch 26/27 的工具分析
awk -F: '$3 >= 1000 && $3 < 65534 {print $1}' /etc/passwd   # 一般使用者（UID >= 1000）
awk -F: '$3 == 0 {print $1}' /etc/passwd                     # UID 0 的（應該只有 root！）
awk -F: '{print $7}' /etc/passwd | sort | uniq -c            # 統計各 shell

# /etc/shadow：密碼雜湊（只有 root 可讀！）
sudo cat /etc/shadow
# alice:$6$xyz...$hash:19000:0:99999:7:::
#  username:密碼雜湊:最後改密碼日:最短:最長:警告:...
# $6$ = SHA-512，$y$ = yescrypt（現代）
ls -l /etc/shadow            # -rw-r----- root shadow（一般使用者不可讀！）
```

```
為什麼密碼分 passwd 和 shadow 兩個檔案：

  歷史上密碼雜湊放 /etc/passwd（欄位 2）
  問題：/etc/passwd 必須所有人可讀（要翻譯 UID→名字）
    → 任何人都能讀到密碼雜湊 → 離線暴力破解
        │
  解法（1980s）：密碼雜湊移到 /etc/shadow（只有 root 可讀）
    /etc/passwd 的密碼欄變成 'x'（表示「看 shadow」）
    → 一般使用者讀不到雜湊，無法離線破解
        │
  → 這是「最小權限」原則的經典應用
    分離「公開資訊」（UID 對照）和「機密」（密碼雜湊）
```

> **密碼從 /etc/passwd 移到 /etc/shadow 是經典的安全設計**。/etc/passwd 必須**所有人可讀**（因為 `ls -l` 等命令要查 UID→名字的對照），但這意味著如果密碼雜湊也在裡面，任何使用者都能讀到所有人的雜湊去離線破解。解法是把雜湊移到 /etc/shadow（權限 `640`，只有 root 和 shadow 群組可讀），passwd 的密碼欄填 `x`。這完美體現「分離公開資訊和機密」——對照表公開，雜湊保密。現代雜湊用 `$6$`（SHA-512）或 `$y$`（yescrypt），帶 salt 防 rainbow table。理解這個分離，你會懂為什麼 `cat /etc/shadow` 要 sudo、為什麼讀到 shadow 是嚴重的權限洩漏。

## 底層機制:real/effective/saved UID 與 setuid

這是使用者模型最深、最重要的部分——一個 process 有多個 UID：

```
一個 process 其實有三個 UID：

  real UID（RUID）：    「我是誰」—— 啟動這個 process 的使用者
  effective UID（EUID）：「我以誰的身分操作」—— 權限檢查看這個
  saved UID（SUID）：   「我能切回去的身分」—— 暫存
        │
  平常三者相同（你跑命令，三個都是你的 UID）
        │
  setuid 程式打破這個：
    passwd 這個檔案有 setuid bit + 擁有者是 root
    你執行 passwd 時：
      RUID = 你（1000）        ← 還是你啟動的
      EUID = root（0）         ← 但以 root 身分跑！（setuid 的效果）
    → passwd 因此能寫 /etc/shadow（EUID=0 過權限檢查）
        │
  → 這就是「為什麼普通使用者能改自己密碼」
    passwd 是 setuid root 程式，跑起來 EUID=0
```

```bash
# 看 passwd 的 setuid bit（Ch 7 的特殊權限位）
ls -l /usr/bin/passwd
# -rwsr-xr-x 1 root root ... /usr/bin/passwd
#     ↑ s（不是 x）= setuid bit！擁有者是 root
#   執行時 EUID 變成 root（檔案擁有者）

# 找系統上所有 setuid 程式（資安檢查的常見動作）
find / -perm -4000 -type f 2>/dev/null
# /usr/bin/passwd, /usr/bin/sudo, /usr/bin/su, ...
#   這些都是「執行時提權到擁有者」的程式 —— 攻擊者的目標

# 觀察 process 的 UID（Ch 16 的 /proc）
cat /proc/self/status | grep -E '^(Uid|Gid)'
# Uid:  1000  1000  1000  1000    ← real, effective, saved, filesystem UID
# （平常都一樣；setuid 程式跑起來 effective 會不同）

# setuid 的危險：寫得爛的 setuid 程式 = 提權漏洞
# 如果一個 setuid root 程式有 bug（如能執行任意命令），
# 攻擊者就能以 root 跑任意東西 → 這是滲透測試的核心攻擊面
```

> **real/effective/saved UID 是 Linux 權限模型最精巧也最危險的部分**。一個 process 不只有一個 UID——**real UID**（誰啟動的）、**effective UID**（以誰的身分操作，權限檢查看這個）、**saved UID**（能切回的身分）。平常三者相同。但 **setuid 程式**（檔案有 `s` bit + 擁有者 root）執行時，EUID 變成檔案擁有者（root）——這就是 `passwd`（一般使用者跑的命令）能修改 /etc/shadow（只有 root 能寫）的原因：它跑起來 EUID=0。這個機制讓「需要特權的操作」能安全地開放給一般使用者（passwd 只改密碼，不做別的）。但它也是**最大的攻擊面**——一個有 bug 的 setuid root 程式 = 提權漏洞（攻擊者利用它以 root 跑任意命令）。`find / -perm -4000` 列出所有 setuid 程式是資安稽核的標準動作。理解三個 UID，你才懂提權攻擊和 sudo 的本質。

## sudo:受控的提權

sudo 是現代「臨時變成 root」的標準方式：

```bash
# sudo：以另一個使用者（預設 root）執行命令
sudo command                     # 以 root 跑 command
sudo -u postgres psql            # 以 postgres 使用者跑
sudo -i                          # 開一個 root 的互動 shell（像登入 root）
sudo -l                          # 列出「我能用 sudo 跑什麼」

# sudo 的設定在 /etc/sudoers（用 visudo 編輯，別直接改！）
sudo visudo
# alice ALL=(ALL:ALL) ALL              ← alice 能以任何身分跑任何命令
# %wheel ALL=(ALL) ALL                 ← wheel 群組的成員都能 sudo
# bob ALL=(ALL) NOPASSWD: /bin/systemctl restart nginx   ← bob 只能不打密碼重啟 nginx

# sudo 怎麼運作（它也是 setuid root 程式）
ls -l /usr/bin/sudo
# -rwsr-xr-x root root ... ← setuid root！
#   sudo 本身 EUID=0，檢查 /etc/sudoers 確認你有權限，才以目標身分執行
```

```
sudo vs su 的差別：

  su（switch user）：
    su -          切換成 root（要 root 的密碼）
    su - alice    切換成 alice（要 alice 的密碼）
    → 真的「變成」那個使用者（開新 shell）
        │
  sudo（superuser do）：
    sudo command  以 root 執行「單一命令」（要「你自己」的密碼）
    → 不變身，只是借權限跑一個命令
    → 有日誌（誰用 sudo 做了什麼，記在 /var/log）
    → 細粒度授權（sudoers 控制誰能跑什麼）
        │
  → 現代偏好 sudo：不用共享 root 密碼、有審計、能限制範圍
```

> **sudo 比 su 安全，因為它不需要共享 root 密碼、有審計、能細粒度授權**。`su -` 要你知道 **root 的密碼**（多人共享一個密碼 = 安全惡夢，誰洩漏的查不出）。`sudo` 要你打**自己的密碼**，sudo 查 /etc/sudoers 確認你有權限——root 密碼可以根本不存在（很多現代系統 root 無密碼，只能 sudo）。sudo 還記日誌（`/var/log/auth.log`，誰在何時跑了什麼）和支援細粒度規則（`bob` 只能 `NOPASSWD` 重啟 nginx，不能做別的）。sudo 自己是 setuid root 程式（EUID=0），所以能切換身分。**永遠用 `visudo` 編輯 /etc/sudoers**（不要直接 `vim`）——visudo 會語法檢查，避免你寫錯把自己鎖在外面（壞掉的 sudoers = 沒人能 sudo = 災難）。

## 使用者與群組管理命令

```bash
# 新增/刪除使用者
sudo useradd -m -s /bin/bash alice    # -m 建家目錄，-s 設 shell
sudo passwd alice                      # 設密碼
sudo userdel -r alice                  # -r 連家目錄一起刪

# 群組
sudo groupadd developers               # 建群組
sudo usermod -aG docker alice          # -aG：把 alice「附加」到 docker 群組（重要：-a 別漏！）
groups alice                           # 看 alice 屬於哪些群組
id alice                               # 看 alice 的 UID/GID/群組（最完整）
# uid=1000(alice) gid=1000(alice) groups=1000(alice),998(docker)

# 切換主要群組 / 臨時群組
newgrp docker                          # 臨時切換主要群組

# 看自己的身分
whoami                                 # 當前 effective 使用者名
id                                     # 完整身分（UID/GID/群組）
who                                    # 誰登入了系統
```

> **`usermod -aG` 的 `-a` 千萬別漏——漏了會「取代」而非「附加」群組**。`sudo usermod -aG docker alice` 把 alice **加入** docker 群組（保留她原有的群組）。但 `usermod -G docker alice`（漏了 `-a`）會把 alice 的附加群組**整個取代**成只有 docker——她原本所屬的其他群組（如 sudo、www-data）全沒了，可能瞬間失去 sudo 權限或其他存取。這是運維的經典災難。記法：**`-aG` 永遠一起用**（append to Groups）。改完群組要重新登入（或 `newgrp`）才生效，因為群組成員身分在登入時載入到 process（Ch 15 的繼承）。`id` 命令看完整身分是驗證的最佳工具。

## 故意弄壞：理解 root 的全能與危險

```bash
cd ~/cmdlab
# root（UID 0）跳過所有權限檢查 —— 驗證
echo "test" > readonly.txt
chmod 000 readonly.txt           # 拿掉所有權限
cat readonly.txt                 # Permission denied（你是一般使用者）
sudo cat readonly.txt            # test（root 無視權限位元！）
#   UID 0 的 process，kernel 跳過權限檢查（這是 root 的本質）

# 危險：root 能做任何破壞，沒有防護網
# sudo rm -rf /          ← 千萬別！root 不會阻止你刪整個系統
#   （現代 rm 對 / 有保護，但對其他路徑沒有）

# 為什麼服務不該用 root 跑（最小權限）
ps aux | awk '$1 == "root"' | head    # 看哪些 process 是 root（越少越好）
ps aux | grep nginx                    # nginx worker 通常是 www-data（不是 root）
#   如果服務被入侵，攻擊者拿到的是 www-data（受限）而非 root（全能）

chmod 644 readonly.txt; rm readonly.txt   # 清理
```

> **root（UID 0）的本質是「kernel 跳過所有權限檢查」——這既是全能也是巨大風險**。一般使用者的每個操作，kernel 都檢查 UID/GID 對檔案權限位元（Ch 7）。但 UID 0 的 process，kernel **直接跳過檢查**——所以 root 能讀寫任何檔案、殺任何 process、做任何事。這是「root 無視 `chmod 000`」的原因。但全能 = 無防護網：`rm -rf /` 不會被阻止（除了現代 rm 對 `/` 本身的特例）、一個錯誤命令能毀掉整個系統。**最小權限原則**：服務（nginx、postgres）應以**專屬的低權限使用者**跑（www-data、postgres），不用 root——這樣即使服務被入侵，攻擊者拿到的是受限身分，不是整台機器。`ps aux | awk '$1=="root"'` 看有多少 root process——越少越安全。這是容器（Ch docker 課）和系統 hardening 的核心思想。

## 動手練習

1. 分析 passwd：用 `awk -F:` 找出所有 UID≥1000 的使用者、所有 nologin shell 的帳號、有沒有 UID 0 的非 root 帳號（資安檢查）

2. 找 setuid：`find /usr/bin -perm -4000 -ls` 列出 setuid 程式，理解為什麼 passwd/sudo/su 在列表裡

3. 觀察三個 UID：`cat /proc/self/status | grep Uid`，理解 real/effective/saved（平常相同）

4. 群組實驗：建一個測試使用者，用 `usermod -aG` 加群組，用 `id` 驗證；故意漏 `-a` 看群組被取代（在測試帳號上）

5. 跑「故意弄壞」：`chmod 000` 一個檔案，自己 cat（拒絕）vs sudo cat（成功），理解 root 無視權限

## 本章重點整理

- kernel 只認 UID/GID（數字）；使用者名是 /etc/passwd 的對照表；root = UID 0，kernel 對它跳過權限檢查
- /etc/passwd（公開，七欄位）+ /etc/shadow（root 才可讀，密碼雜湊）——分離公開資訊和機密
- process 有 real/effective/saved 三個 UID；setuid 程式（passwd/sudo）執行時 EUID 變擁有者（提權的核心機制）
- sudo 比 su 安全：用自己密碼、有審計、細粒度授權；永遠用 visudo 編輯 sudoers
- `usermod -aG`（append）別漏 `-a`（漏了會取代群組）；最小權限：服務用專屬低權限使用者跑

## 自我檢核

- [ ] 能解釋 kernel 只認 UID，以及 root 為什麼是 UID 0
- [ ] 知道 /etc/passwd 和 /etc/shadow 的差別和為什麼分開
- [ ] 能解釋 setuid 機制，以及 passwd 為什麼能改 /etc/shadow
- [ ] 知道 sudo 和 su 的差別，為什麼現代偏好 sudo
- [ ] 理解最小權限原則，為什麼服務不該用 root 跑

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 8-9 (Users/Groups, Process Credentials)** — Kerrisk
  - **讀哪幾章**：Ch 8（passwd/shadow/group 檔案）、Ch 9（real/effective/saved UID、setuid 機制）
  - **這本書的定位**：使用者模型和 process credential 的權威；本章的 UID 機制全部來自 Ch 9
  - **前提**：本章 + Ch 7（權限）

### 官方文件

- **[credentials(7)](https://man7.org/linux/man-pages/man7/credentials.7.html)** — Linux man-pages
  - **讀哪裡**：整篇，特別是 "User and group identifiers" 段
  - **為什麼值得讀**：權威定義 real/effective/saved UID 和它們的轉換規則

- **[sudoers(5) man page](https://www.sudo.ws/docs/man/sudoers.man/)** — sudo 官方
  - **讀哪裡**：EXAMPLES 段（各種授權規則的寫法）
  - **為什麼值得讀**：設定 sudo 權限的權威參考，細粒度授權的完整語法

### 文章

- **[Setuid demystified](https://www.usenix.org/legacy/event/sec02/full_papers/chen/chen.pdf)** — Chen, Wagner, Dean（USENIX Security 2002）
  - **核心貢獻**：分析 setuid 的複雜語意和常見的安全錯誤，是 setuid 機制的學術經典
  - **讀哪裡**：Section 2-3（setuid 的語意模型）
  - **和本章的關聯**：本章 setuid 那節的深入版，理解為什麼 setuid 程式難寫對

→ [Ch 29 環境變數與 PATH](./29-env-path.md)
