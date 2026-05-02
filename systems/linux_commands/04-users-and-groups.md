# Ch 4 — 使用者與群組

> 目標：理解 `/etc/passwd` 和 `/etc/shadow` 的格式，掌握 `sudo`/`su` 的使用方式，能管理本機使用者和群組。

## /etc/passwd：使用者資料庫

```bash
cat /etc/passwd | head -5
```

每行格式（冒號分隔七欄）：

```
alice:x:1000:1000:Alice Wang:/home/alice:/bin/bash
│     │ │    │    │           │           └─ 預設 shell
│     │ │    │    │           └─ 家目錄
│     │ │    │    └─ GECOS（全名或備註）
│     │ │    └─ 主要群組 GID
│     │ └─ UID
│     └─ 密碼（x = 加密後存在 /etc/shadow）
└─ 使用者名稱
```

UID 規則：
- `0` = root
- `1–999` = 系統帳號（daemon、www-data、nobody...）
- `1000+` = 一般使用者

密碼欄放 `x` 表示實際密碼存在 `/etc/shadow`（只有 root 可讀）。

## /etc/shadow：密碼儲存

```bash
sudo cat /etc/shadow | grep alice
```

格式（九欄）：

```
alice:$6$salt$hash...:19380:0:99999:7:::
│     │               │     │ │     └─ 到期前幾天警告
│     │               │     │ └─ 密碼最長有效天數
│     │               │     └─ 密碼最短使用天數
│     │               └─ 上次修改密碼的天數（從 1970-01-01 算）
│     └─ 加密後的密碼（$6$ = SHA-512）
└─ 使用者名稱
```

`$6$` 是 SHA-512 雜湊。不存明文，也沒有解密的方式——`passwd` 修改密碼時是重新雜湊後覆蓋這個欄位。

## /etc/group：群組資料庫

```bash
cat /etc/group | grep alice
```

格式（四欄）：

```
devs:x:1001:alice,bob
│    │ │    └─ 群組成員（逗號分隔）
│    │ └─ GID
│    └─ 群組密碼（幾乎不用）
└─ 群組名稱
```

一個使用者有一個**主要群組**（在 `/etc/passwd` 的第四欄），以及零個以上的**附加群組**（在 `/etc/group` 的成員欄）。

## id：查詢使用者身份

```bash
id
# uid=1000(alice) gid=1000(alice) groups=1000(alice),27(sudo),1001(devs)

id alice        # 查特定使用者
id -u           # 只輸出 UID
id -G           # 輸出所有 GID
```

## sudo：暫時提權

`sudo`（superuser do）讓一般使用者以 root 或其他使用者的身份執行指令：

```bash
sudo apt update            # 以 root 身份執行
sudo -u www-data ls /var/www   # 以 www-data 身份執行
sudo -i                    # 開一個 root shell（互動式）
sudo -s                    # 開 root shell（保留環境變數）
sudo !!                    # 用 sudo 重跑上一個指令
```

`sudo` 的設定在 `/etc/sudoers`，用 `visudo` 安全編輯：

```bash
sudo visudo
# alice ALL=(ALL:ALL) ALL    ← 讓 alice 可以用 sudo 跑任何指令
# alice ALL=(ALL) NOPASSWD: /bin/systemctl restart nginx  ← 特定指令不需密碼
```

## su：切換使用者

```bash
su alice         # 切換到 alice（需要 alice 的密碼）
su -             # 切換到 root（需要 root 密碼），- 代表完整登入環境
su - alice       # 切換到 alice 的完整登入環境
exit             # 退回原本的使用者
```

`su` 和 `sudo -i` 的差異：
- `su -` 需要 target 使用者的密碼
- `sudo -i` 需要**你自己**的密碼（更安全，有稽核記錄）

現代系統通常停用 root 密碼（Ubuntu 預設），所以 `su -` 會失敗，要用 `sudo -i`。

## 管理使用者（需要 root）

```bash
# 建立使用者
useradd -m -s /bin/bash -G sudo,devs newuser
# -m = 建立家目錄
# -s = 指定 shell
# -G = 加入附加群組

# 設定密碼
passwd newuser

# 修改使用者
usermod -aG devs alice     # -a = append，把 alice 加到 devs 群組
usermod -s /bin/zsh alice  # 改預設 shell
usermod -L alice           # 鎖定帳號（密碼前加 !）
usermod -U alice           # 解鎖

# 刪除使用者
userdel alice              # 只刪帳號，保留家目錄
userdel -r alice           # 連家目錄和郵件也刪

# 管理群組
groupadd devs              # 建立群組
groupdel devs              # 刪除群組
gpasswd -a alice devs      # 把 alice 加到 devs
gpasswd -d alice devs      # 把 alice 從 devs 移除
```

加入新群組後要**重新登入**才生效（或 `newgrp devs` 暫時切換到那個群組）。

## 動手練習

```bash
# 1. 看自己的身份
id
cat /etc/passwd | grep $USER
cat /etc/group | grep $USER

# 2. 查 /etc/passwd 欄位
# 找 root 帳號
grep "^root:" /etc/passwd
# 找 shell 是 /bin/false 或 /usr/sbin/nologin 的系統帳號（不能登入）
grep -E "nologin|/bin/false" /etc/passwd | head -5

# 3. 試試 sudo
sudo id                 # 確認是 root
sudo cat /etc/shadow | grep $USER   # 看自己的密碼雜湊

# 4. 建立一個測試使用者，加入群組，再刪掉
sudo useradd -m testuser
sudo passwd testuser
id testuser
sudo userdel -r testuser
```

## 自我檢核

- [ ] 理解 `/etc/passwd` 七欄的意義，知道密碼為什麼是 `x`
- [ ] 知道主要群組（primary group）和附加群組（supplementary group）的差異
- [ ] 理解 `sudo` 和 `su -` 的使用時機差異
- [ ] 能用 `useradd`/`usermod`/`userdel` 管理帳號

→ [Ch 5 目錄與檔案操作](./05-file-operations.md)
