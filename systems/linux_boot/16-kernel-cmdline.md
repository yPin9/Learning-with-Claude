# Ch 16 — kernel cmdline

> 目標：搞清楚 cmdline 從 bootloader 怎麼傳到 kernel、怎麼解析、有哪些救命用的參數。

## 我們在哪裡

第 4 階段 (Kernel) 跟第 5 階段 (initramfs) 的接縫。cmdline 是 bootloader 跟 kernel 之間最重要的通訊。

## cmdline 是什麼

一條字串，bootloader 在啟動 kernel 時傳進去。kernel 用它決定一堆早期行為。

舉例：

```
BOOT_IMAGE=/vmlinuz-5.15 root=UUID=abcd-1234 ro quiet splash nokaslr
```

每個 token 是一個參數。kernel 跟 module 都可以註冊自己的參數 handler。

## 怎麼傳

### BIOS 路徑

bootloader 把 cmdline 字串放在記憶體某處，在 setup header 把 `cmd_line_ptr` 設成那個位址：

```c
boot_params.hdr.cmd_line_ptr = address_of_cmdline_string;
```

real-mode setup 在 `main.c` 把它拷到 `boot_params` 的 cmdline 欄位。

### EFI 路徑

EFI stub 從 LoadOptions 拿 cmdline。`efibootmgr` 註冊 entry 時 `-u` 後面的字串就是 LoadOptions：

```bash
sudo efibootmgr -c ... -u 'root=/dev/sda2 ro'
```

GRUB 在 `linux` 命令傳：

```
linux /boot/vmlinuz-5.15 root=/dev/sda2 ro quiet
```

GRUB 會把 `root=/dev/sda2 ro quiet` 放到 boot_params。

## 看你機器的 cmdline

```bash
cat /proc/cmdline
# BOOT_IMAGE=/boot/vmlinuz-5.15.0-X-generic root=UUID=abcd ro quiet splash
```

每個 token 都被 kernel 解析過。

## kernel 怎麼解析

`init/main.c` 的 `parse_args`：

```c
parse_args("Booting kernel", static_command_line, __start___param,
           __stop___param - __start___param,
           -1, -1, NULL, &unknown_bootoption);
```

每個 module / 子系統用 `module_param`、`early_param`、`__setup` 註冊。例：

```c
static int __init keep_bootcon_setup(char *str)
{
    keep_bootcon = true;
    return 0;
}
early_param("keep_bootcon", keep_bootcon_setup);
```

cmdline 出現 `keep_bootcon` 就會 call 這個 handler。

## cmdline 的層級

token 有三種命運：

1. **kernel 認得的參數**：被 handler 處理，例如 `quiet`、`nokaslr`、`maxcpus=4`
2. **kernel 不認得，看起來像 var=value**：當作 environment variable 傳給 init process
3. **kernel 不認得，看起來不像 var=value**：當作 argv 傳給 init process

例：cmdline 寫 `LANG=zh_TW.UTF-8`，kernel 不認 `LANG`，當 env var；init process 拿到 `LANG=zh_TW.UTF-8`。

systemd 的 `kernel.cmdline` 變數來自這。

## 常用 cmdline 參數

### root filesystem

| 參數 | 作用 |
|---|---|
| `root=/dev/sda1` | 指定 root device |
| `root=UUID=xxx` | 用 UUID（推薦） |
| `root=LABEL=foo` | 用 label |
| `rootfstype=ext4` | 指定檔案系統 type |
| `ro` / `rw` | 開機 mount 唯讀 / 可寫 |
| `rootflags=options` | mount options |
| `rootdelay=N` | 等 N 秒讓 root device 出現 |

### initrd / init

| 參數 | 作用 |
|---|---|
| `initrd=/boot/initramfs.img` | initramfs 路徑（EFI stub 用） |
| `init=/bin/sh` | **救命**：指定 init binary 為 shell |
| `rdinit=/sbin/setup` | 指定 initramfs 內的 init 路徑 |

### debug

| 參數 | 作用 |
|---|---|
| `quiet` | 減少 log |
| `verbose` / `debug` | 加 log |
| `loglevel=7` | 設 log 等級 |
| `console=ttyS0,115200` | log 走 serial |
| `earlyprintk=serial` | 更早就走 serial |
| `ignore_loglevel` | 印所有 log |

### 安全 / 性能

| 參數 | 作用 |
|---|---|
| `nokaslr` | 關 KASLR（debug 用） |
| `nopti` | 關 page table isolation（Meltdown 緩解） |
| `mitigations=off` | 關所有 spectre/meltdown mitigation |
| `nosmt` | 關 hyperthreading |
| `maxcpus=N` | 最多用 N 個 CPU |

### emergency

| 參數 | 作用 |
|---|---|
| `single` | 進 single-user mode |
| `emergency` / `systemd.unit=emergency.target` | 進 emergency mode |
| `rescue` / `systemd.unit=rescue.target` | rescue mode |
| `init=/bin/bash` | bypass init，直接 bash |
| `break=premount` / `break=mount` | initramfs 在指定階段 drop 到 shell |

## init=/bin/sh — 救命招

開機開不起來最後手段：

1. 開機按 `e` 編輯 GRUB entry
2. 把 cmdline 改成 `... init=/bin/sh`
3. `Ctrl-X` 開機

kernel 跳過 systemd，直接 exec `/bin/sh`。你會掉到一個 shell，root 是 read-only mount。要寫的話：

```sh
mount -o remount,rw /
# 修東西
sync
mount -o remount,ro /
exec /sbin/init    # 繼續正常開機
```

或直接 reboot：

```sh
sync && reboot -f
```

**警告**：這種模式下 systemd 沒跑，沒有 service、沒有 udev、沒有 network。只能修 config / `/etc/fstab` / 換 password 之類的事情。

## 改 password 用 init=/bin/sh

```sh
# 開機進 single shell
mount -o remount,rw /
passwd root
sync
reboot -f
```

這就是為什麼 BIOS / GRUB 要設 password — 沒設的話**任何能進實體機的人都能改 root password**。

## emergency.target / rescue.target

systemd 提供：

- `rescue.target`：local filesystem mounted、root shell、無 network
- `emergency.target`：只 mount root（read-only）、root shell、最小

在 GRUB cmdline 加：

```
systemd.unit=rescue.target
```

或舊參數：

```
single
```

或：

```
emergency
```

進 systemd 的 emergency mode，root 密碼登入後可以 debug。

## EFI initrd= 傳法

EFI stub boot 時 cmdline 直接帶 `initrd=`：

```
root=UUID=xxx initrd=\initramfs-5.15.img ro quiet
```

注意路徑用反斜線（UEFI 慣例）、相對 ESP root。

GRUB 不需要寫 `initrd=`，它用 `initrd /path` 這個 GRUB 命令。

## 一個常見踩雷：cmdline 太長

不同 protocol 版本有不同上限，舊的 256 byte，新的 2048 byte。`/proc/cmdline` 看到的就是 kernel 認的，`dmesg | grep "Command line"` 也能看。

過長會被 truncate，**沒警告**。一些 GRUB 老 version 還會直接拒絕載入。

## 一個常見踩雷：BOOT_IMAGE 是什麼

```
cat /proc/cmdline
# BOOT_IMAGE=/boot/vmlinuz-5.15...
```

`BOOT_IMAGE` 是 GRUB 加上去的，不是 kernel 自己。kernel 不認這個 token，當 init 環境變數傳。systemd 拿這個顯示在 boot menu。

## 一個常見踩雷：systemd.X 跟 kernel.X 不互通

cmdline 上 `systemd.log_level=debug` 是給 systemd 的，kernel 不認 — 它直接被丟給 init process。

`loglevel=7` 才是 kernel 的，影響 dmesg 印多少。

不要混。

## 動手練習

**1. 看你機器 cmdline**

```bash
cat /proc/cmdline
```

把每個 token 一個一個查 `man bootparam`、`man systemd.kernel` 或網路上找。

**2. 試 init=/bin/sh**

**先在 VM 試**！別在主機亂搞。在 GRUB menu 按 `e`、cmdline 結尾加 `init=/bin/sh`、`Ctrl-X`。

掉到 shell 後 `mount` 看狀態、`ls /` 看根。試試 `passwd`（會改密碼，記得改回來）。`reboot -f` 退出。

**3. 試 systemd.unit=rescue.target**

開機加 `systemd.unit=rescue.target`，輸入 root 密碼進 rescue。`systemctl list-units` 看哪些 unit 跑了（很少）。`systemctl default` 退出 rescue。

**4. 加 quiet / 拿掉 quiet**

預設多數 distro 加 `quiet splash`。拿掉看開機 log 完整版。

**5. 試 break=premount**

initramfs 在 mount 真實 root 前 drop shell。在 GRUB cmdline 加 `break=premount`，會看到 `(initramfs) #` shell。`exit` 繼續 boot。

## 自我檢核

- [ ] 知道 cmdline 從 bootloader 怎麼傳到 kernel
- [ ] 知道不認得的 token 變成 init 的 env var / argv
- [ ] 試過 `init=/bin/sh` 救機
- [ ] 知道 `loglevel`、`quiet`、`nokaslr` 各自影響
- [ ] 看過 systemd.unit / rescue / emergency 的差別

Part 4 結束。下一章進 initramfs — 為什麼存在、長什麼樣。

→ [Ch 17 initramfs 是什麼](./17-initramfs-overview.md)
