# Ch 24 — initramfs / initrd 機制

> **目標**：徹底理解 initramfs——它解決的根本問題（掛真正 root 需要驅動）、cpio 格式、kernel 如何把它解開成 rootfs、early userspace 的工作、以及 pivot_root/switch_root 切換到真正 root 的機制。也釐清 initramfs 和舊的 initrd 的差異。

> **環境**：Linux kernel 6.x，initramfs（現代）。承接 Ch 23（kernel → init、root 掛載問題）。原理深挖章。

## 為什麼需要 initramfs？

Ch 23 留下一個問題：kernel 要 `execve("/sbin/init")`，但 `/sbin/init` 在 root 檔案系統上，而掛載 root 可能需要驅動（磁碟控制器、檔案系統、LVM/LUKS）。這些驅動如果全編進 kernel，kernel 會肥大且不靈活；如果不編進去，kernel 又掛不上 root。

```
雞生蛋問題（root 掛載）：
  掛 root 需要驅動（NVMe 驅動、ext4 驅動、LVM、LUKS 解密...）
  但驅動通常是模組（不編進 kernel，保持 kernel 精簡）
  模組在哪？在 root 檔案系統的 /lib/modules/
  但要讀 /lib/modules 需要先掛 root
  → 掛 root 需要驅動，驅動在 root 上 → 死結
```

initramfs 是這個死結的解法：一個**小小的、在記憶體裡的臨時 root**，裡面有「掛載真正 root 需要的驅動和工具」。kernel 先掛這個臨時 root，用它把真正的 root 掛起來，然後切換過去。

## 先建立直覺：initramfs 是「開機用的臨時工具箱」

```
initramfs 的角色：

  kernel（精簡，沒有所有驅動）
        │
  掛載 initramfs（記憶體裡的臨時 rootfs，自帶必要工具）
        │  initramfs 裡有：
        │   - 載入驅動需要的模組（NVMe、ext4、LVM、LUKS...）
        │   - userspace 工具（modprobe、lvm、cryptsetup...）
        │   - 一個 /init 腳本
        ▼
  initramfs 的 /init 跑：
        1. 載入需要的驅動模組
        2. 組 LVM / 解密 LUKS / 組 RAID（如果 root 在這些上面）
        3. 掛載真正的 root 檔案系統
        4. switch_root 到真正的 root
        ▼
  真正的 root 上的 /sbin/init（systemd）接手（Ch 26）
```

initramfs 像「開機用的臨時工具箱」——它自帶掛載真正 root 需要的一切，用完就丟（switch_root 後 initramfs 被釋放）。這讓 kernel 保持精簡（驅動不用全編進去），同時能應付複雜的 root（LVM/加密/網路儲存）。

## cpio 格式：initramfs 的包裝

initramfs 是個 **cpio archive**（不是 tar、不是檔案系統 image）：

```
為什麼用 cpio 而非 tar 或 ext4 image？
  - cpio 是極簡的 archive 格式（一串「檔案 header + 內容」）
  - kernel 能直接解開 cpio（內建簡單的 cpio 解析器）
  - 解開後直接成為 rootfs（tmpfs，記憶體檔案系統）
  - 不需要「掛載一個檔案系統 image」（那又需要檔案系統驅動，雞生蛋）
        │
  initramfs = cpio archive（可能再用 gzip/zstd 壓縮）
```

```bash
# 看 initramfs 的內容（Debian/Ubuntu 用 lsinitramfs）
lsinitramfs /boot/initrd.img-$(uname -r) | head -30
# .
# kernel
# kernel/x86
# usr
# usr/bin/sh
# usr/lib/modules/.../kernel/drivers/...
# init                  ← 關鍵：initramfs 的 /init

# 手動解開看（cpio）
mkdir /tmp/initramfs && cd /tmp/initramfs
# initramfs 可能是壓縮的 cpio，先解壓再解 cpio
zstdcat /boot/initrd.img-$(uname -r) | cpio -idmv 2>/dev/null
# 或 gzip：zcat ... | cpio -idmv
ls   # 看到 init、bin、lib、usr...（一個 minimal rootfs）
```

## kernel 如何處理 initramfs

```
kernel 處理 initramfs 的流程：

  bootloader 把 initramfs 載入記憶體，位址寫進 boot_params（Ch 20）
        │
  kernel 早期：populate_rootfs()
    - 把 initramfs（cpio）解開
    - 解開的檔案直接成為 rootfs（一個 tmpfs / ramfs）
        │
  kernel_init（Ch 23）：
    - rootfs 已經是 initramfs 的內容
    - execve("/init")  ← initramfs 的 /init，不是 /sbin/init！
        │
  initramfs 的 /init 接手（early userspace）
```

關鍵：kernel 把 initramfs 解開成 rootfs，然後 execve 它的 `/init`（不是真正 root 的 `/sbin/init`）。這個 `/init` 是 early userspace 的第一個程式，負責掛載真正的 root。

## early userspace：initramfs 的 /init 做什麼

initramfs 的 `/init`（現代發行版由 dracut 或 initramfs-tools 生成）做：

```bash
#!/bin/sh
# initramfs 的 /init（極度簡化的概念）

# 1. 掛載基本的虛擬檔案系統
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev

# 2. 載入需要的驅動模組
modprobe nvme          # 例：root 在 NVMe 磁碟
modprobe ext4          # root 的檔案系統

# 3. 如果 root 在複雜儲存，組裝它
# lvm vgchange -ay              # 啟用 LVM
# cryptsetup open /dev/sda2 root # 解密 LUKS（會問密碼）
# mdadm --assemble ...          # 組 RAID

# 4. 掛載真正的 root（root= 參數指定，Ch 20）
mount -o ro /dev/mapper/root /sysroot
#                            ↑ 真正的 root 掛到 /sysroot

# 5. switch_root 到真正的 root，execve 真正的 init
exec switch_root /sysroot /sbin/init
#                ↑ 切換 root      ↑ 真正的 init（systemd）
```

這個 `/init` 是「掛載真正 root 的所有複雜邏輯」——載入驅動、組 LVM、解密、掛 root、切換。它在 userspace 做（而非 kernel），讓 kernel 保持精簡，且能用 userspace 工具（lvm、cryptsetup）處理複雜儲存。

## switch_root：切換到真正的 root

`switch_root` 是 initramfs 交棒給真正 root 的關鍵動作：

```
switch_root 做的事：
  1. 把真正的 root（/sysroot）變成新的 /（根）
  2. 釋放 initramfs 佔的記憶體（它用完了）
  3. execve 真正 root 上的 /sbin/init
        │
  → 之後系統的 / 是真正的 root，PID 1 是真正的 init
  → initramfs 完全消失（記憶體釋放）
```

```bash
# switch_root 的本質（簡化）
# 1. 移動掛載點：/sysroot 成為 /
# 2. 刪除 initramfs 的內容（釋放記憶體）
# 3. exec /sbin/init（在新 root 上）
```

> `switch_root`（initramfs 用）和 `pivot_root`（更底層）的差別：`pivot_root` 把舊 root 移到一個子目錄（保留）；`switch_root` 直接丟棄舊 root（initramfs）釋放記憶體。initramfs 用 switch_root（initramfs 用完就丟，要釋放記憶體）；容器等場景可能用 pivot_root（保留舊 root）。本課的 initramfs 流程用 switch_root。

## initramfs vs initrd：名稱與機制差異

兩個容易混淆的名稱：

```
initrd（舊，initial RAM disk）：
  - 一個「檔案系統 image」（如 ext2 image）
  - kernel 把它當成一個 block device（/dev/ram0）掛載
  - 需要檔案系統驅動來掛（雞生蛋的殘留問題）
  - 用 pivot_root 切換
  - 已過時

initramfs（新，initial RAM filesystem）：
  - 一個 cpio archive
  - kernel 直接解開成 rootfs（tmpfs，不用掛 block device）
  - 不需要檔案系統驅動來掛（解決了 initrd 的問題）
  - 用 switch_root 切換
  - 現代標準
        │
  名稱混淆：檔案常叫 /boot/initrd.img（沿用舊名）
  但內容是 initramfs（cpio）！
```

> **檔名 initrd.img 但內容是 initramfs**——這是個歷史遺留的命名混淆。`/boot/initrd.img-*` 沿用舊的 initrd 檔名，但現代發行版裡它的內容是 initramfs（cpio archive）。你 `file /boot/initrd.img-*` 會看到它是 "ASCII cpio archive" 或壓縮的。別被檔名騙了——現代系統用 initramfs，不是 initrd。

```bash
file /boot/initrd.img-$(uname -r)
# /boot/initrd.img-6.1.0: Zstandard compressed data ...
# 解壓後是 cpio archive（= initramfs，不是 initrd 的 fs image）
```

## 生成 initramfs

initramfs 由工具生成（不是手寫，雖然練習 C 會手做簡單版）：

```bash
# Debian/Ubuntu：initramfs-tools
sudo update-initramfs -u            # 更新當前 kernel 的 initramfs
sudo update-initramfs -c -k <ver>   # 為特定 kernel 建立

# Fedora/RHEL/Arch：dracut
sudo dracut --force /boot/initramfs-$(uname -r).img $(uname -r)

# 這些工具：
#  - 偵測你的 root 在哪種儲存（NVMe? LVM? LUKS?）
#  - 把需要的驅動模組和工具放進 initramfs
#  - 生成 /init 腳本
#  - 打包成 cpio + 壓縮
```

> initramfs 生成工具（initramfs-tools / dracut）的智慧在於「偵測你的系統需要什麼」——root 在 NVMe 就放 NVMe 驅動，root 加密就放 cryptsetup。這就是為什麼換 root 的儲存方式（如改成 LVM、加密）後要 `update-initramfs`——新的 initramfs 要包含新需求的驅動和工具。忘記更新 initramfs 是 root 改動後開不了機的常見原因。

## 故意弄壞：initramfs 缺少 root 的驅動

```bash
# 場景：把 root 從 ext4 換成 btrfs，但沒更新 initramfs
# 開機時：
# [    X] Loading initramfs...
# initramfs 的 /init 跑，但沒有 btrfs 驅動
# mount /dev/sda2 /sysroot → 失敗（不認識 btrfs）
# → switch_root 失敗 → 掉到 initramfs 的 emergency shell
#   (initramfs)#       ← 你卡在 initramfs，root 沒掛上

# 救援：在 initramfs shell 手動載入驅動掛 root
(initramfs)# modprobe btrfs
(initramfs)# mount /dev/sda2 /sysroot
(initramfs)# exit   # 繼續開機
# 進系統後：sudo update-initramfs -u 修復
```

initramfs 缺少 root 需要的驅動，`/init` 掛不上真正的 root，掉到 initramfs 的 emergency shell（`(initramfs)#`）。這是 Ch 23 的 "Unable to mount root fs" 的 initramfs 版本——root 掛不上，但這次卡在 initramfs 而非 kernel panic。救援：手動 modprobe 驅動掛 root，進系統後 update-initramfs。

## 踩雷集錦

1. **檔名 initrd.img 以為是 initrd**：現代是 initramfs（cpio），只是沿用 initrd 檔名。`file` 確認內容

2. **root 改動後沒 update-initramfs**：換 root 儲存（LVM/加密/檔案系統），initramfs 沒更新，缺驅動，開不了機。改 root 後一定更新 initramfs

3. **以為 initramfs 是可選的小東西**：現代發行版幾乎都依賴它掛 root。沒有它（且驅動沒編進 kernel），掛不上 root

4. **混淆 initramfs 的 /init 和真正的 /sbin/init**：kernel execve initramfs 的 /init（掛 root）；switch_root 後才 execve 真正的 /sbin/init（systemd）。兩個不同的 init

5. **emergency shell 卡住不知怎麼救**：`(initramfs)#` 是 initramfs 的救援 shell（root 沒掛上）。手動 modprobe + mount + exit 繼續，或重開用對的 initramfs

## 進階：initramfs 的兩種來源與 microcode

kernel 其實能接受多個 initramfs（串接）：

```
kernel 的 initramfs 來源：
  1. 編進 kernel 的 built-in initramfs（CONFIG_INITRAMFS_SOURCE）
     （少用，通常空的）
  2. bootloader 載入的外部 initramfs（/boot/initrd.img）
        │
  kernel 把它們串接（先 built-in，後外部）
        │
  特殊用途：CPU microcode
    - microcode 更新要在 kernel 早期載入
    - 放在 initramfs 最前面（未壓縮的 cpio）
    - kernel 開機極早期就讀到並套用 microcode
        │
  所以 /boot/initrd.img 可能是：
    [未壓縮 cpio: microcode] + [壓縮 cpio: 真正的 initramfs]
```

> microcode（CPU 韌體更新）藏在 initramfs 最前面是個巧妙設計——microcode 要在 kernel 極早期套用（修正 CPU bug），放 initramfs 前段（未壓縮）讓 kernel 開機就讀到。這就是為什麼你的 initrd.img 開頭可能是未壓縮的 cpio（microcode），後面才是壓縮的真正 initramfs。理解這個能解釋為什麼 initrd.img 的結構有時是「兩段 cpio 串接」。

## 動手練習

1. 探索你的 initramfs：`lsinitramfs /boot/initrd.img-$(uname -r) | less`，看裡面有哪些驅動、工具、/init。`file /boot/initrd.img-*` 確認是 cpio/壓縮

2. 解開 initramfs：在臨時目錄用 `zstdcat`/`zcat` + `cpio -idmv` 解開，看 /init 腳本的內容（理解 early userspace 做什麼）

3. 看 root 掛載：`cat /proc/cmdline`（看 root= 和 initrd 參數）、`dmesg | grep -i "freeing initrd"`（initramfs 用完釋放）、`dmesg | grep -i switch_root`

4. 救援練習（VM）：故意破壞 initramfs（或用缺驅動的），看掉到 `(initramfs)#` shell，手動 modprobe + mount + exit 救援。這是練習 C 的暖身

## 本章重點整理

- initramfs 解決「掛 root 需要驅動，但驅動在 root 上」的死結：提供記憶體裡的臨時工具箱
- initramfs 是 cpio archive（不是 fs image），kernel 直接解開成 rootfs（tmpfs），不需檔案系統驅動
- kernel execve initramfs 的 /init（early userspace）：載入驅動、組 LVM/解密、掛真正 root、switch_root
- switch_root 切換到真正 root 並釋放 initramfs；initramfs（新，cpio）取代 initrd（舊，fs image）
- 檔名 initrd.img 但內容是 initramfs；root 改動後要 update-initramfs（否則缺驅動開不了機）

## 自我檢核

- [ ] 能解釋 initramfs 解決的根本問題（掛 root 需要驅動的死結）
- [ ] 知道 initramfs 為什麼用 cpio 而非 fs image（kernel 直接解開，不需檔案系統驅動）
- [ ] 能描述 initramfs 的 /init 做什麼（載入驅動、掛 root、switch_root）
- [ ] 知道 initramfs（cpio）和 initrd（fs image）的差異，以及檔名混淆
- [ ] 知道 root 改動後為什麼要 update-initramfs

## 延伸閱讀

### 官方文件

- **[Linux kernel: Documentation/filesystems/ramfs-rootfs-initramfs.rst](https://www.kernel.org/doc/html/latest/filesystems/ramfs-rootfs-initramfs.html)**
  - **讀哪裡**：整份，initramfs 的設計和 initrd 的差異
  - **學什麼**：initramfs 機制的權威說明，為什麼從 initrd 演進到 initramfs
  - **前提**：本章

### 部落格 / 文章

- **[Initramfs explained](https://wiki.gentoo.org/wiki/Custom_Initramfs)** — Gentoo Wiki
  - **這篇說什麼**：手工製作 initramfs 的完整指南，/init 腳本的細節
  - **讀哪裡**：custom initramfs 那節
  - **為什麼值得讀**：為 Ch 25（自製 initramfs）和練習 C 鋪墊，把 /init 講透

- **[dracut documentation](https://github.com/dracutdevs/dracut/wiki)**
  - **這篇說什麼**：dracut（現代 initramfs 生成工具）的運作
  - **讀哪裡**：overview 和 modules
  - **為什麼值得讀**：理解生成工具怎麼決定放哪些驅動

→ [Ch 25 寫一個自製 initramfs](./25-custom-initramfs.md)
