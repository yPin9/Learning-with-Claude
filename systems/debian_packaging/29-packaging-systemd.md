# Ch 29 — 打包 systemd service

> **目標**：理解如何用 `dh_installsystemd` 正確打包提供 daemon 的套件——unit file 的安裝、安裝時的 enable/start 時機與政策、`debian/<pkg>.service` 慣例、以及為什麼不該在 maintainer script 手動呼叫 systemctl。

> **環境**：debhelper 13、systemd（Debian 12 預設 init）。本章承接 Ch 5（maintainer scripts）的狀態機知識。

## 為什麼 service 打包需要特別處理？

提供 daemon 的套件（web server、database、background worker）裝完後通常要：
- 安裝 systemd unit file（`.service`）
- 決定是否 enable（開機自動啟動）
- 決定是否 start（裝完立即啟動）
- 升級時 restart（讓新版生效）
- 移除時 stop（不然檔案刪了 daemon 還在跑舊的 `.so`）

這些時機和政策很微妙——做錯了會在 CI/容器環境卡住、或在升級時讓服務中斷。`dh_installsystemd` 把這些標準化。

## 先建立直覺：dh_installsystemd 注入正確的 script 邏輯

```
你提供：debian/myservice.service（unit file）
        │
   dh_installsystemd（在 dh sequence 裡，Ch 12）自動：
     1. 把 .service 裝到 /lib/systemd/system/
     2. 在 postinst 注入：enable + start（透過 #DEBHELPER#，Ch 5）
     3. 在 prerm 注入：stop
     4. 在 postrm 注入：disable
        │
   → 使用者裝完，服務自動 enable 並啟動
   → 升級自動 restart，移除自動 stop
```

關鍵：你**只提供 unit file**，所有 enable/start/stop 的 maintainer script 邏輯由 `dh_installsystemd` 透過 `#DEBHELPER#`（Ch 5）自動注入。你不手寫 systemctl。

## 一個 service 套件的完整結構

upstream 的 daemon（假設 `myservice` 執行檔）。打包加上：

`debian/myservice.service`（systemd unit file）：
```ini
[Unit]
Description=My example service
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/myservice --config /etc/myservice/config.yaml
Restart=on-failure
User=myservice
Group=myservice

[Install]
WantedBy=multi-user.target
```

`debian/control`：
```
Source: myservice
...
Build-Depends: debhelper-compat (= 13)

Package: myservice
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}, adduser
Description: example background service
 A daemon demonstrating systemd integration in Debian packaging.
```

`debian/rules`（dh 13 預設已含 systemd 處理，通常不用特別寫）：
```makefile
#!/usr/bin/make -f
%:
	dh $@
# dh 13 的 sequence 自動含 dh_installsystemd
# 它會找 debian/myservice.service 並處理
```

> debhelper 13 的 `dh` sequence **預設**包含 `dh_installsystemd`（舊版要 `--with systemd`，現在不用）。只要你放了 `debian/<pkg>.service`，它就自動被安裝和處理。

## unit file 的命名慣例

```
debian/<pkg>.service       → 裝成 <pkg>.service
debian/<pkg>@.service      → template unit（多實例）
debian/<pkg>.<name>.service → 裝成 <name>.service（套件含多個 unit）
debian/<pkg>.socket        → socket activation
debian/<pkg>.timer         → systemd timer（取代 cron）
debian/<pkg>.target        → target
```

放對名字，`dh_installsystemd` 自動裝到 `/lib/systemd/system/` 並處理。

> unit file 裝到 `/lib/systemd/system/`（套件提供的），**不是** `/etc/systemd/system/`（使用者覆寫用的）。使用者要客製化會在 `/etc/systemd/system/` 放覆寫檔，不動你的 `/lib/` 版本。這個分離（套件提供 vs 使用者覆寫）類似 conffile 的精神。

## enable/start 政策：dh_installsystemd 的預設

```
dh_installsystemd 的預設行為：
  postinst configure:
    - systemctl enable（開機自動啟動）
    - systemctl start（裝完立即啟動）
  升級時:
    - systemctl restart（或 try-restart，讓新版生效）
  prerm remove:
    - systemctl stop
  postrm purge:
    - systemctl disable + 清理
```

可以調整這個政策：

```makefile
# 不要自動 enable（裝完不開機啟動，由使用者決定）
override_dh_installsystemd:
	dh_installsystemd --no-enable

# 不要自動 start（裝完不立即啟動）
override_dh_installsystemd:
	dh_installsystemd --no-start

# 指定處理特定 unit
override_dh_installsystemd:
	dh_installsystemd --name=myservice-worker myservice-worker.service
```

> Debian 的傳統政策是「裝完即 enable + start」（policy-rc.d 可覆寫）。這和某些發行版不同（如 RHEL 預設不 start）。如果你的服務需要設定才能跑（如要先填 config），用 `--no-start` 避免裝完就因缺設定而失敗。

## policy-rc.d：容器/CI 環境的 service 控制

在容器或 chroot build 環境，你不希望裝套件時真的啟動服務（沒有 init，或不該啟動）。`policy-rc.d` 機制讓環境能攔截 service 操作：

```bash
# 容器/chroot 裡常見：一個拒絕所有 service 啟動的 policy-rc.d
cat /usr/sbin/policy-rc.d
# #!/bin/sh
# exit 101    ← 101 = 拒絕所有 init script / service 操作

# 所以在 sbuild chroot（Ch 15）裡裝有 service 的套件，
# 服務不會真的啟動（policy-rc.d 擋住），build/test 不受影響
```

這就是為什麼 `dh_installsystemd` 注入的 start 在 build/容器環境「安全」——`policy-rc.d` 攔截了真實啟動。理解這個，你才懂為什麼「裝 service 套件不會在 build 環境亂啟動東西」。

## 配合 postinst 建立 service 使用者

service 通常要專屬使用者（不用 root 跑 daemon）。這部分要手寫 postinst（Ch 5），和 `dh_installsystemd` 的 `#DEBHELPER#` 配合：

`debian/postinst`：
```bash
#!/bin/sh
set -e

case "$1" in
    configure)
        # 建立 service 使用者（在 dh_installsystemd 的 start 之前！）
        if ! getent passwd myservice >/dev/null; then
            adduser --system --group --no-create-home \
                    --home /var/lib/myservice myservice
        fi
        mkdir -p /var/lib/myservice
        chown myservice:myservice /var/lib/myservice
        ;;
esac

#DEBHELPER#   ← dh_installsystemd 在這裡注入 enable + start
              #   此時使用者已建好，start 才不會因 User= 不存在而失敗

exit 0
```

> 順序關鍵：使用者建立要在 `#DEBHELPER#`（service start）**之前**。`#DEBHELPER#` 注入的 `systemctl start` 會以 unit file 的 `User=myservice` 啟動——如果使用者還沒建，啟動失敗。所以你的 `case configure` 在 `#DEBHELPER#` 上面。

## 故意弄壞：手動呼叫 systemctl

```bash
# 錯誤：在 postinst 手動 systemctl（lintian 會抓，且在容器/build 會出問題）
# debian/postinst:
# case "$1" in
#   configure)
#     systemctl enable myservice    ← 錯！
#     systemctl start myservice     ← 錯！
#   ;;

lintian myservice_*.deb
# E: myservice: maintainer-script-calls-systemctl postinst
#   → 該用 dh_installsystemd（透過 #DEBHELPER#），不要手動
```

為什麼不能手動 systemctl：
- 不尊重 `policy-rc.d`（容器/build 環境會出問題）
- 不處理 systemd 不可用的情況（如 chroot）
- 重複造輪子，且容易遺漏邊界情況

正確永遠用 `dh_installsystemd` + `#DEBHELPER#`。

## 踩雷集錦

1. **手動呼叫 systemctl**：不尊重 policy-rc.d、不處理 systemd 不可用。永遠用 `dh_installsystemd` + `#DEBHELPER#`

2. **service 使用者在 `#DEBHELPER#` 之後才建**：start 時 `User=` 不存在，啟動失敗。使用者建立要在 `#DEBHELPER#` 之前

3. **unit file 放錯目錄**：套件提供的放 `/lib/systemd/system/`（dh_installsystemd 自動），不是 `/etc/systemd/system/`（使用者覆寫用）

4. **裝完即 start 但服務缺設定**：daemon 需要 config 才能跑，但 `dh_installsystemd` 預設 start，導致裝完就 crash。用 `--no-start` 或提供能用的預設 config

5. **升級不 restart 導致跑舊版**：升級後檔案換新但 daemon 還跑舊的。`dh_installsystemd` 預設處理 restart，別 override 掉它

6. **ExecStart 用絕對路徑但路徑錯**：unit file 的 `ExecStart` 要用安裝後的絕對路徑（`/usr/bin/myservice`），不是 build 目錄的

## 進階：socket activation 與 timer

systemd 提供 cron 之外的現代機制，打包也支援：

**socket activation**（按需啟動）：
```
debian/myservice.socket:
[Socket]
ListenStream=8080
[Install]
WantedBy=sockets.target
```
服務不開機就啟動，而是有連線進 8080 時才被 systemd 啟動。省資源（閒置時不佔記憶體）。`dh_installsystemd` 同樣處理 `.socket`。

**timer**（取代 cron）：
```
debian/mytask.timer:
[Timer]
OnCalendar=daily
[Install]
WantedBy=timers.target
```
配合 `mytask.service`（`Type=oneshot`），systemd timer 取代 cron job。優點：有 log（journald）、能設定資源限制、依賴管理。現代套件的定期任務優先用 timer 而非 `/etc/cron.d`。

```bash
# 看系統的 timer
systemctl list-timers
```

> 新套件的定期任務，優先考慮 systemd timer 而非 cron——有更好的 log、資源控制、依賴。`dh_installsystemd` 處理 `.timer` 的方式和 `.service` 一樣（放對名字自動處理）。

## 動手練習

1. 打包一個簡單的 daemon（一個 `while true; sleep` 的 script 即可），提供 `debian/myservice.service`，build 後在 VM 裝，確認服務自動 enable + start（`systemctl status myservice`）

2. 加 service 使用者：在 postinst 建立專屬使用者（在 `#DEBHELPER#` 之前），unit file 用 `User=`，確認服務以該使用者跑

3. 故意弄壞：在 postinst 手動 `systemctl enable`，跑 lintian 看它報 `maintainer-script-calls-systemctl`，改用 `dh_installsystemd` 修正

4. 試 timer：把一個 service 改成 `.timer` + `Type=oneshot` 的 `.service`，`systemctl list-timers` 確認，對比 cron 的差別

## 本章重點整理

- 提供 `debian/<pkg>.service`，`dh_installsystemd`（dh 13 預設含）自動裝 unit 並注入 enable/start/stop 邏輯
- 永遠用 `dh_installsystemd` + `#DEBHELPER#`，不手動呼叫 systemctl（lintian 會抓）
- service 使用者建立要在 `#DEBHELPER#`（start）之前，否則 `User=` 不存在啟動失敗
- unit 裝 `/lib/systemd/system/`（套件提供），使用者覆寫在 `/etc/systemd/system/`
- `policy-rc.d` 讓容器/build 環境安全地攔截 service 啟動；socket activation 和 timer 是現代機制

## 自我檢核

- [ ] 能解釋為什麼不該在 maintainer script 手動呼叫 systemctl
- [ ] 知道 `dh_installsystemd` 透過什麼機制注入 service 邏輯（#DEBHELPER#，Ch 5）
- [ ] 知道為什麼 service 使用者要在 `#DEBHELPER#` 之前建立
- [ ] 能解釋 policy-rc.d 如何讓 build/容器環境不被 service 啟動干擾
- [ ] 知道 systemd timer 相比 cron 的優點

## 延伸閱讀

### 官方文件

- **[dh_installsystemd(1) man page](https://manpages.debian.org/bookworm/debhelper/dh_installsystemd.1.html)**
  - **讀哪裡**：所有 option（--no-enable/--no-start/--name）和檔案命名慣例
  - **學什麼**：service 處理的完整選項；本章講了常用的
  - **前提**：讀完本章

- **[Debian Policy §9.11 (systemd)](https://www.debian.org/doc/debian-policy/ch-opersys.html#init-systems-and-init-d-scripts)** 和 systemd.unit(5)
  - **讀哪裡**：init system 整合那節
  - **學什麼**：Debian 對 init/service 的規範
  - **前提**：本章

### 部落格 / 文章

- **[systemd for Administrators (Lennart Poettering)](http://0pointer.de/blog/projects/systemd-for-admins-1.html)** — systemd 作者
  - **這篇說什麼**：systemd 的設計理念、unit/socket activation/timer
  - **讀哪裡**：socket activation 和 timer 那幾篇
  - **為什麼值得讀**：作者是 systemd 創造者；理解 unit file 背後的設計，打包才打得對

→ [Ch 30 打包 kernel module（DKMS）](./30-packaging-dkms.md)
