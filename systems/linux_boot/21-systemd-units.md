# Ch 21 — systemd unit / target / dependency

> 目標：搞懂 systemd 的核心抽象 — unit、target、dependency。讀得懂 unit file，知道 service 為什麼依某個順序啟動。

## 我們在哪裡

第 7 階段（PID 1 / systemd）的核心觀念。

## Unit 是什麼

systemd 把所有「可管理的東西」抽象成 **unit**。每個 unit 有：

- 一個檔案（`.service`、`.mount`、`.target`、...）描述它
- 一個狀態（active / inactive / failed / activating / ...）
- 一個 dependency graph

unit 類型：

| Type | 描述 |
|---|---|
| `.service` | 一個 daemon / process |
| `.target` | 一組 unit 的「同步點」(sysvinit runlevel 的對應) |
| `.mount` | mount 點 |
| `.automount` | 自動 mount 觸發 |
| `.swap` | swap 區 |
| `.device` | hardware device（udev 自動建） |
| `.socket` | UNIX / TCP socket |
| `.timer` | 定時觸發（取代 cron） |
| `.path` | 監聽檔案系統事件 |
| `.slice` | cgroup 階層分組 |
| `.scope` | 不是 systemd 啟動的 process group（如 user session） |

最常碰的是 `.service` 跟 `.target`。

## Service unit 範例

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=My Cool Application
Documentation=https://example.com/docs
After=network-online.target
Wants=network-online.target
Requires=postgresql.service

[Service]
Type=simple
User=myapp
Group=myapp
WorkingDirectory=/opt/myapp
ExecStart=/opt/myapp/bin/server --config /etc/myapp/config.toml
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5s
TimeoutStartSec=30s
Environment=NODE_ENV=production

# Sandbox
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

三個 section：

### `[Unit]`

通用 metadata + dependency：

| Key | 意義 |
|---|---|
| `Description` | 給人看的描述 |
| `Documentation` | URL（多個用空白分） |
| `Requires=foo` | 強依賴：foo 沒起 / 死了，我也不該起 / 該停 |
| `Wants=foo` | 弱依賴：希望 foo 起，但 foo 失敗我還是起 |
| `After=foo` | 順序：foo 起完我再起（不蘊含依賴） |
| `Before=foo` | 順序：我起完 foo 才能起 |
| `Conflicts=foo` | 衝突：我起 foo 必停，反之亦然 |

**重要**：`Requires` 跟 `After` **正交** — `Requires=foo` 不蘊含 `After=foo`。沒寫 `After` 兩者會平行 race。

### `[Service]`

service 怎麼跑：

| Key | 意義 |
|---|---|
| `Type` | service 類型（見下表） |
| `User` / `Group` | 用哪個 user / group 跑 |
| `WorkingDirectory` | cwd |
| `ExecStart` | 啟動命令 |
| `ExecStop` | 停止命令（不寫的話送 SIGTERM） |
| `ExecReload` | reload 命令 |
| `Restart` | 失敗時的行為（no / on-failure / always / ...） |
| `RestartSec` | restart 之間等多久 |
| `Environment` | 環境變數 |
| `EnvironmentFile` | 從檔案讀環境變數 |
| `TimeoutStartSec` | 啟動超時 |

`Type` 是個關鍵：

| Type | 什麼時候 service 算「啟動完成」 |
|---|---|
| `simple` | ExecStart fork 完就算 active（預設） |
| `forking` | ExecStart fork 一個 daemon 後 exit，daemon 是真正的 service |
| `oneshot` | 跑一次然後 exit，適合 init script |
| `dbus` | 等 service 註冊 D-Bus name |
| `notify` | service 自己 call `sd_notify(READY=1)` 通知 systemd |
| `idle` | 等其他 unit 都起完才起（純 cosmetic） |

寫錯 Type 會讓 systemd 誤判 service state — 例如 `Type=simple` 但 ExecStart 是個 daemonize 的程式，systemd 會以為 main process 死了。

### `[Install]`

`enable` 時要做什麼：

| Key | 意義 |
|---|---|
| `WantedBy=foo.target` | enable 時建立 `foo.target.wants/myapp.service` symlink |
| `RequiredBy=foo.target` | 同上但 RequiredBy |
| `Alias=` | 建立別名 |

**只在 `systemctl enable` 時用**，service 跑時不影響。

`enable` 不會 start service；要立刻啟動：`systemctl enable --now myapp`。

## Target 是什麼

Target 是一組 unit 的「同步點」 — 不執行任何東西，只是個聚集點。對應 sysvinit 的 runlevel。

常見 target：

| Target | 對應 runlevel | 描述 |
|---|---|---|
| `default.target` | 5 (typically) | 開機後的最終目標，通常 symlink 到下面其中一個 |
| `multi-user.target` | 3 | multi-user CLI mode |
| `graphical.target` | 5 | multi-user + GUI |
| `rescue.target` | 1 | single user，root shell |
| `emergency.target` | - | 最小，只 root shell + read-only / |
| `network.target` | - | 網路 config 完成（不保證有連線！） |
| `network-online.target` | - | 網路有連線 |
| `sysinit.target` | - | 系統最早期初始化 |
| `basic.target` | - | sysinit 之後、多數 service 啟動前 |
| `shutdown.target` | 6 | 關機 |

target 像「做完 X 群事情」的 milestone。要 service 在 X 之前 ready，加 `Before=X.target`；之後跑加 `After=X.target`。

切換 target：

```bash
systemctl isolate multi-user.target     # 切到無 GUI
systemctl set-default graphical.target  # 預設改 GUI
systemctl get-default
```

## Dependency 圖

systemd 啟動時建一個 dependency graph，計算 boot order。看一個 unit 的 deps：

```bash
systemctl list-dependencies sshd
```

輸出像樹：

```
sshd.service
● ├─system.slice
● ├─sshd-keygen.target
● │ ├─sshd-keygen@ecdsa.service
● │ ├─sshd-keygen@ed25519.service
● │ └─sshd-keygen@rsa.service
● ├─sysinit.target
● │ ├─dev-hugepages.mount
● │ ├─...
● └─basic.target
●   ├─paths.target
●   ├─slices.target
●   ├─sockets.target
●   ├─sysinit.target
●   └─timers.target
```

reverse direction（誰依賴我）：

```bash
systemctl list-dependencies --reverse sshd
```

## Wants vs Requires：很重要的差別

```ini
Requires=foo.service
```

意義：「foo 必須 active 我才能 active；foo 死了 systemd 也停我」。

```ini
Wants=foo.service
```

意義：「希望 foo active，但即使 foo 失敗我還是繼續」。

**多數情況用 Wants**。Requires 太硬，foo 出錯會 cascade 停整串。實務上 Requires 適合「這 service 完全沒辦法在 foo 沒跑時運作」 — 例如 myapp 必須 connect postgresql，可以 Requires。但通常 myapp 寫個 retry 比硬綁好。

## After 跟 Wants 必須搭配

```ini
Wants=postgresql.service
After=postgresql.service
```

`Wants` 說「拉起 postgresql」。`After` 說「postgresql 起完再起我」。**沒 After 的話會 race**。

很多新手寫 unit 漏 `After`，service 起來時依賴還沒 ready，bug 詭異難 debug。

## 找 unit file 的順序

systemd 找 unit 順序（前面優先）：

1. `/etc/systemd/system/` — local override / custom
2. `/run/systemd/system/` — runtime generated
3. `/usr/lib/systemd/system/` 或 `/lib/systemd/system/` — distro 包提供

**改 unit 不要直接改 distro 那份**，會被 update 覆蓋。正確做法：

```bash
# 完全 override（很少這樣做）
sudo cp /lib/systemd/system/sshd.service /etc/systemd/system/sshd.service
sudo vi /etc/systemd/system/sshd.service

# Drop-in 部分 override（推薦）
sudo systemctl edit sshd
# 開啟 editor，寫部分 override（如改 Restart=always）
```

`systemctl edit` 建立 `/etc/systemd/system/sshd.service.d/override.conf`，只覆蓋你寫的 key。

## 一個常見誤解：「After=network.target 就有網路」

**錯**。`network.target` 只表示 network manager / networkd 啟動完成 — **不代表已連網**。

要等真的連線：

```ini
After=network-online.target
Wants=network-online.target
```

`network-online.target` 由 `NetworkManager-wait-online` 或 `systemd-networkd-wait-online` 拉起，會 block 等到至少一個 interface up 才完成。

注意 wait-online service 會讓 boot 變慢，server 用容易，桌面常省略。

## 一個常見誤解：「systemctl start 自動 enable」

**錯**。`start` 跟 `enable` 是兩件事：

- `start`：現在啟動（reboot 後不會自動）
- `enable`：建 symlink 讓開機自動啟動（現在不會跑）

要兩個都做：

```bash
sudo systemctl enable --now sshd
```

## 一個常見誤解：「edit unit 自動生效」

**錯**。改完 unit 必須 reload：

```bash
sudo systemctl daemon-reload
sudo systemctl restart myapp
```

`daemon-reload` 讓 systemd 重讀 unit files；`restart` 才實際生效。

## 動手練習

**1. 看你機器跑的 service**

```bash
systemctl list-units --type=service --state=running
systemctl --failed
```

**2. 查一個 service 的依賴**

```bash
systemctl list-dependencies sshd
systemctl list-dependencies --reverse multi-user.target | head -30
```

**3. 寫一個自己的 service**

```bash
sudo tee /etc/systemd/system/hello.service <<EOF
[Unit]
Description=Hello Loop

[Service]
Type=simple
ExecStart=/bin/sh -c 'while true; do echo "hello $(date)"; sleep 5; done'

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl start hello
sudo systemctl status hello
sudo journalctl -u hello -f
sudo systemctl stop hello
```

**4. 用 edit 部分 override**

```bash
sudo systemctl edit hello
```

加：

```ini
[Service]
Restart=always
RestartSec=2s
```

存檔後 `sudo systemctl restart hello`。試 kill 它，看是否自動起。

**5. 比較 simple / forking / oneshot**

寫三個 service 對照，故意搞錯 Type，看 systemd state 怎麼反應。

## 自我檢核

- [ ] 講得出 unit 的 7+ 種類型
- [ ] 知道 Wants / Requires / After / Before 各自意義
- [ ] 知道 Type=simple / forking / notify / oneshot 差別
- [ ] 知道 enable 跟 start 不同
- [ ] 用 `systemctl edit` 過 override
- [ ] 寫過自己的 service 跑起來

下一章看 systemd 早期 boot 的 target chain — 從 initrd-switch-root 怎麼一步步走到 multi-user。

→ [Ch 22 systemd 早期 boot target chain](./22-systemd-boot-targets.md)
