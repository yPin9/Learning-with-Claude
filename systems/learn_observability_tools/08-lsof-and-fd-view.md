# Ch 8 — lsof 與 fd 視角

> 目標：學會 lsof 各種查詢模式，從 fd 切角觀察整台機器：誰開了哪個檔、哪個 socket 是誰的、刪除了卻被 hold 住的檔案在哪。

## lsof 是什麼

「LiSt Open Files」。**Unix 的 fd 是萬物**，所以 lsof 能列出整台機器所有的：

- 一般檔案（regular file）
- 目錄（directory）
- char / block device
- pipe
- socket（TCP / UDP / Unix）
- anon_inode（epoll / eventfd / signalfd / inotify）
- shared memory
- 已刪除但還被 open 的 inode

它就是把所有 process 的 `/proc/PID/fd/` 跟 `/proc/PID/maps` 收集起來，按你給的 filter 印。

## 基本用法

```bash
sudo lsof | head           # 全機（很長，幾十萬行）
sudo lsof -p PID           # 單一 process
sudo lsof -u USER          # 某個 user
sudo lsof -c bash          # 命令名 prefix
sudo lsof /etc/passwd      # 誰開了這檔
sudo lsof +D /tmp          # 在 /tmp 下開的所有東西
sudo lsof -i :22           # listen / connect 22 port 的
sudo lsof -i tcp           # 所有 tcp
sudo lsof -i tcp@10.0.0.5  # 跟某 IP 連的 tcp
sudo lsof -i udp           # udp
sudo lsof -U               # Unix domain socket
sudo lsof +L1              # link count < 1（已刪除但還開著的）
```

加 `-n` 不解析 IP 反查 DNS、加 `-P` 不解析 port 對應的 service name —— **快很多**。

```bash
sudo lsof -nP -i tcp
```

## 輸出格式

```bash
sudo lsof -p $$ | head
```

```
COMMAND   PID  USER   FD   TYPE             DEVICE  SIZE/OFF NODE NAME
bash    12345  you  cwd    DIR              253,0      4096   12 /home/you
bash    12345  you  rtd    DIR              253,0      4096    2 /
bash    12345  you  txt    REG              253,0   1183448 1234 /usr/bin/bash
bash    12345  you  mem    REG              253,0      9456 5678 /usr/lib/libnss_files.so.2
bash    12345  you  mem    REG              253,0   2030928 9012 /usr/lib/libc.so.6
bash    12345  you    0u   CHR              136,0       0t0    3 /dev/pts/0
bash    12345  you    1u   CHR              136,0       0t0    3 /dev/pts/0
bash    12345  you    2u   CHR              136,0       0t0    3 /dev/pts/0
```

欄位：

| 欄位 | 意義 |
|---|---|
| `FD` | fd 號 + mode；或特殊：`cwd` / `rtd` / `txt` / `mem` |
| `TYPE` | REG / DIR / CHR / BLK / FIFO / IPv4 / IPv6 / unix / a_inode |
| `DEVICE` | major,minor |
| `SIZE/OFF` | 檔案大小 或 socket offset |
| `NODE` | inode |
| `NAME` | 路徑 / `socket:[N]` / `pipe:[N]` |

特殊 FD：
- `cwd` — current working directory
- `rtd` — root（chroot 後不同）
- `txt` — 程式 binary
- `mem` — mmap 的 lib / file
- `0u` / `1u` / `2u` — fd 0/1/2，u = read+write open

mode：`r` (read)、`w` (write)、`u` (read+write)、`W` (write lock)、`R` (read lock)。

## 反查：誰開了這個檔

```bash
sudo lsof /var/log/syslog
# COMMAND   PID         USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
# rsyslogd 1234         root  10w   REG  253,0     ...   ... /var/log/syslog
```

要刪一個檔但 OS 說「busy」？lsof 找誰 hold 著它。

## 找已刪除但還佔空間的檔

最經典場景：磁碟滿了，但 `du` 看不到那些檔。原因：檔案被 unlink 了但有 process 還 open，inode 不釋放。

```bash
sudo lsof +L1 | grep deleted
# nginx  1234 www-data  4w  REG  253,0  10737418240 12345 /var/log/nginx/access.log (deleted)
```

「access.log 被刪了但 nginx 還寫，10GB 卡在 inode」。**修法**：restart nginx，或 `> /proc/1234/fd/4`（透過 fd 把檔案 truncate）。

## socket 視角

```bash
sudo lsof -nP -i tcp
# COMMAND   PID  USER   FD   TYPE  DEVICE NODE NAME
# nginx    1234 root    6u  IPv4   12345  TCP  *:80 (LISTEN)
# nginx    1234 root    7u  IPv4   23456  TCP  10.0.0.5:80->10.0.0.9:54321 (ESTABLISHED)
# sshd     5678 root    3u  IPv4   34567  TCP  *:22 (LISTEN)
```

NAME 直接告訴你 listen 哪個 port、跟誰連。

跟 ss / netstat 對照：lsof 顯示「誰持有」（PID + 命令名），ss 顯示「socket 內部狀態」。**找「誰開了這 port」用 lsof，找「TCP retransmit / window size」用 ss**。

## 找指定 port

```bash
sudo lsof -i :80
sudo lsof -i :22-25     # range
sudo lsof -i :http      # service name
sudo lsof -i tcp:443    # protocol + port
sudo lsof -i @10.0.0.9  # 跟某 host 通的
```

## 篩 ESTABLISHED / LISTEN

```bash
sudo lsof -nP -i tcp -s tcp:LISTEN
sudo lsof -nP -i tcp -s tcp:ESTABLISHED
```

## Unix domain socket

```bash
sudo lsof -U
# systemd 1 root 30u unix 0x... 0t0 12345 /run/systemd/notify
# dockerd 567 root 4u unix 0x... 0t0 23456 /var/run/docker.sock
```

`/var/run/docker.sock`、`/var/run/postgresql/.s.PGSQL.5432` 這種 IPC 入口，lsof -U 一覽。

## 找誰 chdir 到某目錄

```bash
sudo lsof +D /tmp/work
```

不只開了 `/tmp/work` 下檔案的，連 cwd 在 `/tmp/work` 的 process 也算。「為什麼我 unmount 不了」可能就是某 shell 還 cd 在裡面。

## process 視角

```bash
sudo lsof -p 1234
sudo lsof -p 1234,5678
sudo lsof -p ^1234       # 所有 process 排除 1234
```

跟 `ls -l /proc/1234/fd/` 大致等價，但 lsof 多顯示 mem-mapped 檔案、cwd / rtd 等。

## 一個常見場景：fd leak

```bash
PID=$(pgrep myserver)
while true; do
  sudo lsof -p $PID 2>/dev/null | wc -l
  sleep 5
done
```

數字穩定增加 = leak。配 `lsof -p $PID | sort | uniq -c | sort -n` 看是哪類 fd 增加。

## 一個常見場景：檢查 service 用了什麼 socket

```bash
sudo lsof -p $(pgrep -f myapp) -nP | grep -E "TCP|UDP|unix"
```

debug「為什麼 myapp 連不到 redis」第一招：看它有沒有開 port 6379 的 socket、有開的話狀態是什麼。

## 一個常見場景：debug docker

```bash
# 找哪個 process 占用 docker.sock
sudo lsof /var/run/docker.sock
```

## 一個常見踩雷：lsof 慢得要命

`sudo lsof` 不加 filter 會掃所有 process 的所有 fd，幾十秒。**永遠加 filter**：`-p` / `-c` / `-i` / `-u` / `-n -P`。

## 一個常見踩雷：container 內的 lsof 看不到 host

container 用 PID namespace 隔離。container 內 PID 1 是 entrypoint，看不到 host 的 process。要看全部得：

```bash
nsenter -t HOST_PID -m -p sudo lsof   # 進 host namespace
```

## 一個常見踩雷：lsof 跟 /proc 結果不一致

罕見但有：lsof 跑時 process 剛 close fd，會看到「中間態」。一次性 snapshot 不可能完美 — 多跑幾次或用 strace。

## 動手練習

**1. 看你的 shell 開了什麼**

```bash
sudo lsof -p $$ | grep -v "mem\|cwd\|rtd\|txt"
```

過濾掉 lib mapping，剩下真的 fd。

**2. 找占用某檔的人**

```bash
# 開個檔
sleep 3600 < /etc/passwd &
sudo lsof /etc/passwd
# 看到你的 sleep
kill %1
```

**3. 模擬已刪除檔案**

```c
// hold.c
#include <unistd.h>
#include <fcntl.h>
int main() {
    int fd = open("/tmp/zombiefile", O_CREAT | O_WRONLY, 0644);
    unlink("/tmp/zombiefile");
    write(fd, "data", 4);
    sleep(60);
    return 0;
}
```

```bash
gcc hold.c -o hold
./hold &
sudo lsof +L1 | grep zombiefile
# hold ... /tmp/zombiefile (deleted)
```

**4. 找 listen 的 service**

```bash
sudo lsof -nP -i tcp -s tcp:LISTEN
```

每行對照「這個 port 是 ssh / nginx / postgres」之類。

**5. 找 fd leak 的 demo**

```c
// leak.c
#include <unistd.h>
#include <fcntl.h>
int main() {
    while (1) {
        open("/etc/passwd", O_RDONLY);
        sleep(1);
    }
}
```

```bash
gcc leak.c -o leak
./leak &
PID=$!
for i in 1 2 3 4 5; do
    sudo lsof -p $PID | wc -l
    sleep 2
done
# 5 6 7 8 9 — 每秒 +1
kill $PID
```

最後遲早會 hit `ulimit -n`。production 看到 fd 圖只升不降就要警報。

## 自我檢核

- [ ] lsof 各種 filter（`-p` `-c` `-u` `-i` `-U`）用得順
- [ ] 知道 cwd / rtd / txt / mem 特殊 fd 意義
- [ ] 用過 `+L1` 找已刪除的檔
- [ ] 用過 `-i` 找 listen 的 port + 對應 process
- [ ] 知道 `+D` 跟 lsof 一個檔的差別
- [ ] 知道為什麼 lsof 不加 filter 會慢

下一章看網路 — ss / tcpdump，跟 lsof 互補。

→ [Ch 9 ss / tcpdump — 網路觀察](./09-ss-and-tcpdump.md)
