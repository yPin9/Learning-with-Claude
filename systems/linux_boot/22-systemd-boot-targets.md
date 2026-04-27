# Ch 22 — systemd 早期 boot target chain

> 目標：搞清楚 systemd 從接手到 multi-user.target 經過哪些 target、每個 target 在幹嘛、可以從哪裡攔截。

## 我們在哪裡

第 7 階段（PID 1）的細節。switch_root 後 systemd 接手到登入畫面之前。

## Target chain 全景圖

```
 [kernel exec /sbin/init]
        │
        ▼
 ┌─────────────────────────────────────┐
 │ systemd 接手                        │
 │ 解析 unit files                     │
 │ 建 dependency graph                 │
 └─────────────────────────────────────┘
        │
        ▼
 default.target (= multi-user.target / graphical.target)
        │ 透過 dependency 拉起
        ▼
 multi-user.target
        │ Requires
        ▼
 basic.target
        │ Requires
        ▼
 sysinit.target
        │ Requires (parallel)
        ├── local-fs.target
        ├── swap.target
        ├── cryptsetup.target
        ├── systemd-tmpfiles-setup.service
        ├── systemd-modules-load.service
        ├── systemd-udevd.service
        └── ...
```

理解這個圖最重要的事：systemd **不是一條線跑下來**，而是一個 dependency graph，所有不互相依賴的 unit 平行啟動。

## 從 initramfs 看：兩段 systemd

如果用 initramfs，會有**兩個** systemd 階段：

```
 [bootloader]
   ↓
 [kernel + initramfs]
   ↓
 systemd inside initramfs
   ↓ initrd.target
   ↓ initrd-switch-root.service
   ↓ switch_root
 systemd inside real root      ← 重新 exec systemd
   ↓ default.target
```

initramfs 裡也跑 systemd（如果用 dracut + systemd-boot）。它的目標是 `initrd.target`，做完就 switch_root。

switch_root 後 **systemd 重新 exec 自己**（path 換成 real root 的 systemd binary），重置整個 dependency graph，從 default.target 開始。

也有 distro 的 initramfs 不用 systemd，用簡單的 sh script — 那情況 systemd 只在 real root 跑。

## 細看每個 target

### sysinit.target — 系統最早期

啟動所有「在能跑 service 之前必須完成」的東西：

- `systemd-tmpfiles-setup.service`：建立 `/run/`、`/tmp/` 等 tmpfs 結構
- `systemd-sysctl.service`：apply `/etc/sysctl.d/`
- `systemd-modules-load.service`：載 `/etc/modules-load.d/` 列的 module
- `systemd-udevd.service`：udev daemon（device 偵測）
- `systemd-random-seed.service`：載 `/var/lib/systemd/random-seed`
- `cryptsetup.target`：解 LUKS volume
- `local-fs.target`：mount 所有 `/etc/fstab` 的 local fs
- `swap.target`：開 swap

```bash
systemctl list-dependencies sysinit.target
```

幾十個 unit。注意這階段 boot script 不該跑（service 通常 After=sysinit.target）。

### basic.target — 最低運作環境

sysinit 之後、service 之前。設好：

- `paths.target`：所有 `.path` unit
- `slices.target`：cgroup slice 結構
- `sockets.target`：所有 `.socket` unit (準備 socket activation)
- `timers.target`：所有 `.timer` unit
- 各種 base service（dbus 等）

**到這裡系統已經「能用」**，user service 可以在 basic.target 之後安全啟動。

### multi-user.target — 多用戶 CLI

basic.target 之後。啟動絕大多數 service：

- `sshd.service`
- `cron.service` / `crond.service`
- `network.target` 確保網路 config done
- `nginx.service`、`postgresql.service`、...
- `getty@tty1.service` — login prompt

這對應 sysvinit 的 runlevel 3。

### graphical.target — 桌面

`Requires=multi-user.target`，加上：

- `display-manager.service` (gdm / sddm / lightdm)
- 其他桌面相關 service

對應 runlevel 5。

### default.target — 你想停哪

`/etc/systemd/system/default.target` 是個 symlink，指向想要的 target：

```bash
ls -l /etc/systemd/system/default.target
# default.target -> /lib/systemd/system/graphical.target
```

切預設：

```bash
sudo systemctl set-default multi-user.target   # 之後不啟動 GUI
sudo systemctl set-default graphical.target    # 之後啟動 GUI
```

## 看實際開機 chain

```bash
systemctl list-dependencies                # 列 default.target 的整個樹
systemctl list-dependencies multi-user.target
systemctl list-dependencies basic.target
systemctl list-dependencies sysinit.target
```

看時間花在哪：

```bash
systemd-analyze
# Startup finished in 1.234s (firmware) + 0.567s (loader) + 1.890s (kernel) + 2.345s (initrd) + 5.678s (userspace) = 11.714s
# graphical.target reached after 5.678s in userspace

systemd-analyze blame | head -20
# 哪些 service 最慢

systemd-analyze critical-chain
# 開機 critical path
```

`critical-chain` 特別有用：列出 boot 時間取決於哪條 dependency 鏈。

## 攔截：在某個 target 停下

cmdline 加 `systemd.unit=multi-user.target` → 不會啟動 GUI。

加 `systemd.unit=rescue.target` → 進 rescue。

加 `systemd.unit=emergency.target` → 進 emergency。

**這比 `single` 或 `1` 更精確**（雖然 systemd 為了相容也認舊參數）。

## getty@.service — 登入 prompt

`getty@tty1.service` 是 instantiated service — 從 template `getty@.service` 用 instance name `tty1` 實例化。

template `/lib/systemd/system/getty@.service`：

```ini
[Service]
ExecStart=-/sbin/agetty --noclear %I $TERM
Type=idle
```

`%I` 是 instance name (`tty1`)。

multi-user.target wants `getty.target` wants `getty@tty1.service`，就拉起來了。看到登入 prompt。

graphical.target 不需要 tty1 上的 getty，但通常還是有。

## socket activation

systemd 取代 inetd 的功能：

```ini
# /lib/systemd/system/sshd.socket
[Unit]
Description=OpenSSH Server Socket

[Socket]
ListenStream=22
Accept=no

[Install]
WantedBy=sockets.target
```

```ini
# /lib/systemd/system/sshd@.service
[Service]
ExecStart=-/usr/sbin/sshd -i
StandardInput=socket
```

`enable sshd.socket` 後：systemd 開機就 listen 22 port，**但 sshd 不啟動**。第一次有人連 22 port，systemd fork sshd 起來、把 socket fd 傳給它。

好處：

- 啟動快（service 延遲到第一次連線）
- service crash 不影響 listen（fd 還在 systemd 手上）
- service 升級可以 zero downtime

但 sshd 多數機器選擇直接啟動，因為 latency 比節省 RAM 重要。

## 一個常見誤解：「systemd 一定平行所有 service」

不一定。Dependency 寫對，平行。`After` 鏈長一條就 sequential。

`systemd-analyze critical-chain` 看你機器的 critical path，常常會發現有不必要的 After，可以拿掉。

## 一個常見誤解：「multi-user.target 起完 = boot 結束」

systemd 認為「default.target 達成 = boot 結束」。`systemd-analyze` 報的時間到此為止。

但**user-perceived 開機**還包括 GDM 顯示登入畫面、user login 後啟動所有 user service。那部分 systemd-analyze 不算。

## 一個常見誤解：「kernel cmdline 加 emergency 等於 emergency.target」

對，systemd 認 `emergency` / `rescue` / `single` / `1` / `3` / `5` 等舊 sysvinit 參數，map 到對應 target。

但更明確的是 `systemd.unit=emergency.target`，**任意 target 都能指定**。

## 動手練習

**1. 看你機器的 default.target**

```bash
ls -l /etc/systemd/system/default.target
systemctl get-default
```

**2. 跑一次 systemd-analyze**

```bash
systemd-analyze
systemd-analyze blame | head -20
systemd-analyze critical-chain
```

**3. 把 critical-chain 上某個 service 看 source**

如果 critical chain 上 `foo.service` 花了 3 秒，看 unit：

```bash
systemctl cat foo.service
```

問自己：它真的需要那麼久嗎？After 是否合理？

**4. 切 default 試試**

```bash
sudo systemctl set-default multi-user.target
# 重開
# 進不了 GUI
sudo systemctl set-default graphical.target
# 重開
# 回 GUI
```

**5. 用 systemd-analyze plot**

```bash
systemd-analyze plot > boot.svg
```

開 SVG 圖看每個 service 啟動時間 + dependency。**這張圖是 boot 慢的時候第一個要看的東西**。

## 自我檢核

- [ ] 畫得出 sysinit → basic → multi-user → graphical 的 chain
- [ ] 知道 default.target 是 symlink、可以 set-default
- [ ] 知道 socket activation 是什麼
- [ ] 跑過 `systemd-analyze blame / critical-chain / plot`
- [ ] 知道 `systemd.unit=` 可以攔截到任意 target

下一章看 boot 出問題怎麼辦 — 各種排錯工具跟救機方法。

→ [Ch 23 開機排錯](./23-boot-troubleshooting.md)
