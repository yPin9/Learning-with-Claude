# Ch 20 — PID 1 簡史

> 目標：搞懂 PID 1 為什麼特殊、sysvinit / Upstart / systemd 三代設計差在哪、為什麼 systemd 贏。

## 我們在哪裡

第 7 階段。switch_root 後 kernel exec `/sbin/init`，這個 process 變成 PID 1。

## PID 1 為什麼特殊

PID 1 是「孤兒之父」。kernel 對它有特殊規定：

- **第一個 userspace process**，由 kernel 直接 exec
- **不能被 kill**（除非送給它的 signal 它自己 install 了 handler）
- **死了 = panic**（kernel 看到 PID 1 exit 直接 `Kernel panic - Attempted to kill init`）
- **所有 orphan process 的 parent**：父 process 死了的 child 自動 reparent 到 PID 1
- **要 reap zombie**：上面那些 orphan 死了會變 zombie，PID 1 必須 wait()

PID 1 死掉系統就死。所以實作要極度穩定 + 簡單。

## 第一代：sysvinit (System V init)

1980 年代 Unix System V 風格，Linux 早期沿用。一直到 2010 年代後期都是 Debian 預設。

設計：

- PID 1 = `/sbin/init`，一個小的 C 程式
- 讀 `/etc/inittab`，內容像：

```
id:5:initdefault:                  # 預設 runlevel = 5

# runlevel 切換時跑這
l5:5:wait:/etc/rc.d/rc 5

# tty 啟動
1:2345:respawn:/sbin/agetty 38400 tty1
2:2345:respawn:/sbin/agetty 38400 tty2

# Ctrl-Alt-Del
ca::ctrlaltdel:/sbin/shutdown -t 3 -r now
```

- runlevel 是 0-6 的整數：0 halt、1 single user、3 multi-user no GUI、5 multi-user with GUI、6 reboot
- `init 3` 切 runlevel
- 啟動 service 靠 `/etc/rc.d/rc N` script，**sequential** 跑 `/etc/rc.d/rc N.d/S*` 下所有 script

每個 service 是一個 shell script 在 `/etc/init.d/`：

```sh
#!/bin/sh
# /etc/init.d/sshd
case "$1" in
  start)  /usr/sbin/sshd ;;
  stop)   killall sshd ;;
  restart) $0 stop; $0 start ;;
esac
```

`S20sshd` 開機時跑 `start`，`K20sshd` 關機時跑 `stop`。number 控制順序。

### sysvinit 的問題

- **慢**：sequential，每個 service 等前面跑完。100 個 service × 50ms = 5 秒
- **依賴隱含**：要靠 number 排序，沒有真正的 dependency graph
- **shell script 脆弱**：service script 寫錯一行整個 boot stuck
- **重啟靠 polling**：service 死了 init 不會自動拉，要靠 cron / monit 之類
- **runlevel 太粗**：只有 7 個，無法表達細緻狀態

## 第二代：Upstart (2006-2014)

Ubuntu 提出，主要解決「event-driven boot」：service 不該等時間順序，應該等事件（device 出現、檔案系統 mount 完）。

設計：

- 每個 service 一個 `.conf` 檔案在 `/etc/init/`
- 描述 「this service starts on event X, stops on event Y」
- Upstart core 是 event broker，event 觸發時拉對應 service

範例：

```
# /etc/init/sshd.conf
description "SSH Server"

start on (filesystem and net-device-up IFACE!=lo)
stop on runlevel [!2345]

respawn
exec /usr/sbin/sshd -D
```

`start on` 是條件 — `filesystem` event 觸發、且網路介面 up 時啟動。`respawn` 是 service 死了自動拉。

### Upstart 的好處跟限制

好處：
- Event-driven，比 sysvinit 平行 + 快
- 自動 respawn

限制：
- 只 Ubuntu 用，其他 distro 沒跟進
- Event model 概念上吸引人，但實作起來很難 debug
- 沒解決 「跨 boot 持久狀態」、「socket activation」 等更深層需求
- Canonical 跟 Linux community 開發者人事問題

2014 年 Ubuntu 宣布轉 systemd，Upstart 死亡。

## 第三代：systemd (2010-)

Lennart Poettering（pulseaudio 作者）跟 Red Hat 主導。2010 年發表，2015 年起幾乎所有主流 distro 預設。

核心想法：

1. **Unit**：一切都是 unit — service、mount、device、socket、target、timer
2. **Dependency graph**：unit 之間的關係用宣告式描述（Wants、Requires、After、Before）
3. **Parallel by default**：dependency 滿足就同時跑
4. **Socket activation**：service 啟動延遲到第一次連線（仿 inetd 但更現代）
5. **cgroup integration**：每個 service 在自己的 cgroup，方便管 resource、track child
6. **整合一切**：日誌（journald）、登入（logind）、解析（resolved）、網路（networkd）、定時（timer），通通自家做

範例 unit：

```ini
# /etc/systemd/system/sshd.service
[Unit]
Description=OpenSSH server daemon
After=network.target sshd-keygen.target
Wants=sshd-keygen.target

[Service]
Type=notify
ExecStart=/usr/sbin/sshd -D $OPTIONS
ExecReload=/bin/kill -HUP $MAINPID
KillMode=process
Restart=on-failure
RestartSec=42s

[Install]
WantedBy=multi-user.target
```

幾乎所有「該怎麼啟動」的細節都在這個檔案。systemd 解析、計算 dependency、平行起動。

## 為什麼 systemd 贏

不是因為它最簡單（複雜得要命），而是因為它**統一了一堆過去鬆散的工具**：

- sysvinit + xinetd + inetd + cron + atd + udev + console-tools + dbus + ConsoleKit + ...
- 全部被 systemd 一個套件取代

對 distro 維護者來說，**少維護 30 個獨立 package** 是很大的勝利。對使用者來說，**boot 從 30 秒變 5 秒**也很有感。

爭議在於：

- systemd 大、複雜，違反 Unix 「do one thing well」
- Lennart 個人風格不討喜
- 把太多事情塞在 PID 1 周邊，故障爆炸半徑大

但**勝負已定**。Debian / Ubuntu / Fedora / Arch / openSUSE 都用 systemd。少數 alternatives：

- **OpenRC** (Gentoo, Alpine)：sysvinit 風格但平行化
- **runit** (Void Linux)：超簡單，service 一個資料夾
- **s6** (Alpine 也用)：複雜但乾淨

對學習 Linux boot，**systemd 是必修**。後面 Ch 21-23 都在講 systemd。

## systemd 的子工具

systemd 不只是 PID 1，還有一堆配套：

| 工具 | 作用 |
|---|---|
| `systemd` | PID 1，core init |
| `systemctl` | 控制 unit（start/stop/enable/...） |
| `journalctl` | 看 log |
| `systemd-journald` | log daemon |
| `systemd-logind` | 登入 session 管理 |
| `systemd-udevd` | device 偵測（fork 自 udev） |
| `systemd-resolved` | DNS resolver |
| `systemd-networkd` | 網路設定 |
| `systemd-timesyncd` | NTP |
| `systemd-boot` | UEFI bootloader（Ch 13） |
| `systemd-nspawn` | 輕量 container |
| `systemd-analyze` | boot 時間分析 |
| `loginctl` | session 控制 |
| `hostnamectl` / `timedatectl` / `localectl` | 系統設定 |

每個都可以獨立看，但相互整合。

## 一個常見誤解：「systemd 是個 process」

不只一個。`systemd` PID 1 是核心，但啟動後會 fork 一堆 helper：

```bash
ps -ef | grep systemd
root           1  /sbin/init
root         234  /lib/systemd/systemd-journald
root         245  /lib/systemd/systemd-udevd
root         567  /lib/systemd/systemd-logind
root         789  /lib/systemd/systemd-resolved
yourname    1234  /lib/systemd/systemd --user    # 每個 user 一個
```

`systemd --user` 是 user instance，跟 system instance 平行存在。Ch 21 會詳細講。

## 一個常見誤解：「systemd 取代 init script」

對也不對。systemd 還能跑 sysvinit script — 在 `/etc/init.d/` 放 LSB 標準 script，systemd 自動產生對應 unit。

但新 service **應該寫 unit**，不該寫 init script。Unit 簡單、不易出錯、可平行。

## 動手練習

**1. 看你機器有沒有 sysvinit 殘留**

```bash
ls /etc/init.d/        # 還有檔案？
ls /etc/rc*.d/         # 還有 symlink？
```

很多 distro 為了相容還保留，但實際是 systemd 在跑。

**2. 看 PID 1 是什麼**

```bash
ps -p 1
ls -l /proc/1/exe
ls -l /sbin/init
```

`/sbin/init` 應該 symlink 到 systemd。

**3. 看 systemd unit**

```bash
systemctl list-units --type=service --state=running
systemctl status sshd
cat /lib/systemd/system/sshd.service
```

**4. 對照 sysvinit script**

```bash
ls /etc/init.d/ | head
# 隨便挑一個還在的
cat /etc/init.d/ssh 2>/dev/null | head -50
# 對照 systemd unit 寫法
cat /lib/systemd/system/ssh.service
```

## 自我檢核

- [ ] 知道 PID 1 為什麼特殊（不能 kill、死了 panic）
- [ ] 講得出 sysvinit / Upstart / systemd 三代設計差別
- [ ] 知道 systemd 為什麼贏（統一一堆工具）
- [ ] 知道 systemd 不只一個 process
- [ ] 看自己機器的 PID 1 是什麼

下一章詳細看 systemd unit / target / dependency。

→ [Ch 21 systemd unit / target / dependency](./21-systemd-units.md)
