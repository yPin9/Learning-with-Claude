# Ch 3 — 權限模型

> 目標：理解 rwx bit 的二進位表示、owner/group/other 三層結構，掌握 `chmod`/`chown`，知道 SUID/SGID/sticky bit 的作用。

## 三層 × 三種：9 個 bit

```
ls -l script.sh
-rwxr-xr--  1  alice  devs  512  Jan 15  script.sh
 ││││││││└─ other: 讀
 ││││││└──  other: 不可執行
 │││││└─── other: 不可寫
 ││││└──── group: 可執行
 │││└───── group: 不可寫
 ││└────── group: 可讀
 │└─────── owner: 可執行
 └──────── owner: 可寫
└───────── owner: 可讀
```

每個 rwx 各佔 1 個 bit，三個合起來是 0–7 的八進位數：

| 二進位 | 八進位 | 意義 |
|--------|--------|------|
| 000 | 0 | 無任何權限 |
| 001 | 1 | 只能執行 |
| 100 | 4 | 只能讀 |
| 101 | 5 | 讀＋執行 |
| 110 | 6 | 讀＋寫 |
| 111 | 7 | 讀＋寫＋執行 |

`chmod 755 script.sh` = owner:7(rwx) group:5(r-x) other:5(r-x)

## 目錄的 rwx 意義不同

對**目錄**而言，rwx 的意義和檔案不一樣：

| 權限 | 對目錄的意義 |
|------|------------|
| r | 可以 `ls` 列出目錄內容 |
| w | 可以在目錄裡新增/刪除/重命名檔案 |
| x | 可以進入（`cd`）這個目錄，以及存取裡面的檔案 |

沒有 `x` 的目錄你連 `cd` 進去都不行，即使有 `r` 也只能看到檔名但無法存取內容。

## chmod：修改權限

**八進位模式**（精確）：

```bash
chmod 755 script.sh    # rwxr-xr-x
chmod 644 config.txt   # rw-r--r--
chmod 600 private.key  # rw-------
chmod 777 /tmp/shared  # rwxrwxrwx（危險，慎用）
chmod -R 755 /var/www  # -R 遞迴修改目錄
```

**符號模式**（相對修改）：

```bash
chmod +x script.sh     # 所有人加上執行權限
chmod -w file.txt      # 所有人移除寫權限
chmod u+x,g-w file    # owner 加執行，group 移除寫
chmod o= file.txt      # 清除 other 的所有權限
chmod a+r file.txt     # a = all（owner+group+other）全部加讀
```

符號模式適合「我只想改某一位」，八進位適合「我要設定精確的值」。

## chown / chgrp：修改擁有者

```bash
chown alice file.txt           # 只改 owner
chown alice:devs file.txt      # 同時改 owner 和 group
chown :devs file.txt           # 只改 group（chgrp 也可以）
chown -R alice:devs /var/www   # 遞迴
```

需要 `sudo` 才能改擁有者（除非改的是你自己擁有的檔案）。

## umask：預設權限的遮罩

新建檔案的預設權限由 `umask` 決定：

```bash
umask           # 通常是 0022
```

計算方式：
- 新建**檔案**的基礎是 `666`（不帶 execute），減去 umask
- 新建**目錄**的基礎是 `777`，減去 umask

```
umask = 022
檔案預設 = 666 - 022 = 644 (rw-r--r--)
目錄預設 = 777 - 022 = 755 (rwxr-xr-x)
```

## SUID / SGID / Sticky bit

這三個是第四組特殊 bit，用在安全和多人協作場景。

**SUID（Set UID）**：執行這個程式時，以**檔案擁有者**的 UID 執行，而不是呼叫者的 UID：

```bash
ls -l /usr/bin/passwd
-rwsr-xr-x  root  root  /usr/bin/passwd
   └─ s = SUID，owner 的 x 位置

# passwd 需要寫入 /etc/shadow（只有 root 能寫）
# 有 SUID 的話，一般使用者執行 passwd 時，程式以 root 身份執行
```

**SGID（Set GID）**：對**目錄**設定時，目錄裡新建的檔案繼承目錄的群組，而不是建立者的群組：

```bash
chmod g+s /project   # 設定 SGID
# 之後所有在 /project 裡建立的檔案都屬於 /project 的群組
```

**Sticky bit**：設在目錄上，只有**檔案擁有者或 root** 才能刪除該目錄裡的檔案（即使其他人有寫入權限）：

```bash
ls -ld /tmp
drwxrwxrwt  root  root  /tmp
         └─ t = sticky bit

# /tmp 的 sticky bit 讓所有人都能建立檔案，但只能刪自己的
```

## 動手練習

```bash
# 1. 建立腳本，測試執行權限
echo '#!/bin/bash
echo "Hello from script"' > test.sh

./test.sh    # 應該報錯（Permission denied）
chmod +x test.sh
./test.sh    # 現在可以跑了

# 2. 用八進位測試各種權限
chmod 000 test.sh    # 完全鎖死
cat test.sh          # Permission denied
chmod 400 test.sh    # 只有 owner 可讀
cat test.sh          # 可以讀了

# 3. 確認 umask
umask
touch newfile.txt
ls -la newfile.txt   # 應該是 rw-r--r-- (644)

# 4. 看看系統上的 SUID 程式
find /usr/bin -perm -4000 2>/dev/null   # 找有 SUID 的程式
ls -l /usr/bin/passwd   # 看 passwd 的 SUID
```

## 自我檢核

- [ ] 能心算 `chmod 755`、`chmod 644` 代表什麼 rwx 組合
- [ ] 理解目錄的 `x` 權限代表「可以進入」，不是「可以執行」
- [ ] 知道 SUID 讓程式以擁有者身份執行（`passwd` 的例子）
- [ ] 知道 sticky bit 保護 `/tmp`

→ [Ch 4 使用者與群組](./04-users-and-groups.md)
