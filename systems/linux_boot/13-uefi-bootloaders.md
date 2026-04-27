# Ch 13 — UEFI 下的 GRUB2 與 systemd-boot

> 目標：對照看兩種 UEFI bootloader 的設計哲學、檔案結構、何時該選哪個。

## 我們在哪裡

第 3 階段 (Bootloader) 的 UEFI 版本實務。對照 Ch 9 的 BIOS GRUB。

## 兩家設計哲學

**GRUB2** — 「萬能 bootloader」：

- 自己有完整的 shell、scripting language、檔案系統 driver
- 能 chainload 其他 bootloader、能 dual boot Windows
- config (`grub.cfg`) 是 Turing-complete script
- 巨大、複雜、bug 多但也最穩定

**systemd-boot** (舊名 `gummiboot`) — 「最小 bootloader」：

- 完全依賴 UEFI 服務，不重新發明檔案系統 driver
- 沒有 scripting，config 是純文字
- 只開機 UEFI 看得到的 OS
- 體積小、簡單、好 debug

簡單對照：

| 項目 | GRUB2 | systemd-boot |
|---|---|---|
| 檔案系統 driver | 自帶（ext4, btrfs, zfs, xfs, ...） | 沒有，靠 UEFI（只能讀 ESP / FAT32） |
| Kernel 放哪 | 任意 partition（可在 ext4 root 上） | 必須在 ESP（FAT32） |
| Config 語法 | Shell-like script | 純 key=value |
| Network boot | ✅ | ❌ |
| Multi-boot | ✅ 用 chainloader | ❌（只列 UEFI 認得的） |
| Secure Boot | ✅ 透過 shim | ✅ 簽章直接驗 |
| 大小 | ~1MB | ~100KB |
| 學習曲線 | 陡 | 平 |

## GRUB2 在 UEFI 上的檔案

```
/boot/efi/EFI/<distro>/
├── grubx64.efi        # GRUB2 的 UEFI bootloader (PE)
├── grub.cfg           # 通常很短，只 chainload 真正的 cfg
├── shimx64.efi        # Secure Boot shim（如果開了 Secure Boot）
├── mmx64.efi          # MOK Manager (Machine Owner Key 管理)
└── fbx64.efi          # fallback / boot manager

/boot/grub/
├── grub.cfg           # 真正的主 config
├── grubenv            # 動態變數
├── x86_64-efi/        # GRUB module，UEFI 平台
└── ...
```

開機流程：

1. UEFI firmware 讀 NVRAM `Boot0001`，找到 `grubx64.efi`（或 `shimx64.efi`）
2. 載入 `grubx64.efi`，跳到它的 entry
3. `grubx64.efi` 用 UEFI Boot Services 讀 `EFI/<distro>/grub.cfg`
4. 那個 cfg 通常 chainload `/boot/grub/grub.cfg`
5. 主 cfg 解析 menuentry，使用者選一個
6. GRUB 用自己的 driver 讀 `/boot/vmlinuz-...` 跟 initramfs（GRUB 認得 ext4 / btrfs，不需要 ESP）
7. 把 kernel 跟 initramfs 載入記憶體
8. 設好 boot params，呼叫 `ExitBootServices`，jmp 到 kernel

關鍵：**GRUB 自己讀 ext4**，所以 kernel 不必在 ESP。這是 GRUB 比 systemd-boot 強的地方。

## systemd-boot 的檔案

```
/boot/                                    # = ESP，通常直接 mount /boot
├── EFI/
│   ├── BOOT/
│   │   └── BOOTX64.EFI                  # = systemd-bootx64.efi 的 copy（fallback）
│   └── systemd/
│       └── systemd-bootx64.efi
├── loader/
│   ├── loader.conf                      # 全域設定
│   ├── entries/
│   │   ├── arch.conf                    # 一個 boot entry 一個檔案
│   │   ├── arch-fallback.conf
│   │   └── windows.conf
│   └── random-seed                      # entropy
└── vmlinuz-linux                        # kernel！直接放 ESP
└── initramfs-linux.img                  # initramfs 也在 ESP
```

注意：**`/boot` 直接是 ESP**。Arch / 一些現代 distro 用這種佈局。

`loader/loader.conf`：

```
default arch.conf
timeout 3
console-mode max
editor no
```

`loader/entries/arch.conf`：

```
title    Arch Linux
linux    /vmlinuz-linux
initrd   /initramfs-linux.img
options  root=UUID=xxxx-yyyy rw quiet
```

這就是全部。**沒有 script、沒有 if/else、沒有 function**。每個 menu entry 一個檔案，systemd-boot 開機時掃 `entries/` 下所有檔案組成 menu。

## 開機流程對照

```
 GRUB2:                                  systemd-boot:

 UEFI                                    UEFI
   ↓                                       ↓
 grubx64.efi                             systemd-bootx64.efi
   ↓                                       ↓
 grub.cfg (script)                       loader/entries/*.conf (parse)
   ↓                                       ↓
 GRUB 自己 mount ext4                    UEFI 讀 ESP 檔案
   ↓                                       ↓
 載 vmlinuz + initramfs                  載 vmlinuz + initramfs
   ↓                                       ↓
 ExitBootServices()                      ExitBootServices()
   ↓                                       ↓
 jmp kernel                              jmp kernel
```

關鍵差別在「讀 kernel」這一步：GRUB 自己 mount 任何檔案系統；systemd-boot 只能讀 ESP。

## systemd-boot 的限制：kernel 必須在 ESP

這個限制有兩個含意：

- **ESP 要夠大**：要放 kernel + initramfs + 多個版本，建議 1GB
- **更新時需要 sync**：`/boot/vmlinuz-X.Y` 在 ESP，所以 distro 套件管理直接寫 ESP 沒問題

Arch 的 `mkinitcpio` hook 預設就把產出寫到 `/boot`，正好是 ESP。

對照 Debian / Ubuntu 預設的 GRUB 模式：`/boot` 在 ext4 root partition，`/boot/efi` 是 ESP。GRUB 從 ext4 讀 kernel。

## chainload 是什麼

GRUB 可以 `chainloader /EFI/Microsoft/Boot/bootmgfw.efi`，把控制權交給 Windows bootloader。

systemd-boot 沒有 chainloader 命令，但**自動**列出 ESP 上找到的 `bootmgfw.efi` 跟其他 `.efi`，不用設定就能 dual boot。

## 一個常見誤解：「systemd-boot 是 systemd 一部分」

**部分對**。systemd-boot 跟 systemd 同個 source tree，但執行時跟 systemd 完全分開：

- systemd-boot 是 PE/COFF，跑在 UEFI 環境，systemd 還沒啟動
- systemd 是 PID 1，跑在 Linux userspace
- 兩者不互通

選 systemd-boot 不一定要用 systemd init（雖然絕大多數一起用）。

## 一個常見誤解：「systemd-boot 比 GRUB 安全」

兩者 Secure Boot 支援都有但路徑不同：

- **GRUB 走 shim 路線**：shim (Microsoft 簽) → grub2 (distro 簽) → kernel (distro 簽)
- **systemd-boot 直接 sign**：systemd-boot.efi 用 distro key 簽，UEFI 直接驗

systemd-boot 的 chain 短，攻擊面小。但 GRUB 也不是不安全，只是複雜度高 bug 多。

## 何時選哪個

選 **GRUB**：

- Debian / Ubuntu 系（預設裝這個，換有風險）
- 要 dual boot Windows + 多種 distro
- root 在 LVM / btrfs subvolume / LUKS（GRUB 認得這些）
- 要 PXE / network boot

選 **systemd-boot**：

- Arch / Fedora Silverblue（ESP 容易管）
- 純 Linux 機器（不要 dual boot）
- 想要 boot config 簡單、人類可讀
- ESP 夠大、kernel 不大

實務上 cloud / server 多用 systemd-boot 或 direct kernel boot，桌面多用 GRUB（因 distro 預設）。

## 命令對照

```bash
# GRUB
sudo grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=mydistro
sudo grub-mkconfig -o /boot/grub/grub.cfg
sudo update-grub                          # Debian wrapper

# systemd-boot
sudo bootctl install                       # 把 systemd-boot.efi 寫到 ESP + 註冊
sudo bootctl status                        # 顯示目前狀態
sudo bootctl update                        # 升級 ESP 上的 systemd-boot
```

## 動手練習

**1. 看你機器的 bootloader**

```bash
ls /boot/efi/EFI/
# 看到 ubuntu/debian/fedora → GRUB
# 看到 systemd/ + loader/ → systemd-boot
```

**2. 看 GRUB 的 efi**

```bash
file /boot/efi/EFI/*/grub*.efi
file /boot/efi/EFI/*/shim*.efi
# 都是 PE32+ executable
```

**3. 看 systemd-boot config（如果用 systemd-boot）**

```bash
cat /boot/loader/loader.conf
ls /boot/loader/entries/
cat /boot/loader/entries/*.conf
```

**4. 在 OVMF 試 systemd-boot**

```bash
# 建一個 ESP
mkdir -p esp/EFI/systemd
mkdir -p esp/loader/entries

# 從你機器拷 systemd-boot
sudo cp /usr/lib/systemd/boot/efi/systemd-bootx64.efi esp/EFI/systemd/
cp esp/EFI/systemd/systemd-bootx64.efi esp/EFI/BOOT/BOOTX64.EFI

# 寫一個假 entry
cat > esp/loader/loader.conf <<EOF
default fake
timeout 5
EOF

cat > esp/loader/entries/fake.conf <<EOF
title  Fake Kernel
linux  /no-kernel
EOF

# 跑
qemu-system-x86_64 -m 256 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=/tmp/OVMF_VARS_test.fd \
  -drive format=raw,file=fat:rw:esp
```

你會看到 systemd-boot 的 menu，選 `Fake Kernel` 會 fail 因為沒真的 kernel。但 menu 顯示出來代表 bootloader 跑起來了。

## 自我檢核

- [ ] 講得出 GRUB2 跟 systemd-boot 設計哲學的差異
- [ ] 知道 systemd-boot 為什麼 kernel 必須在 ESP
- [ ] 知道兩者 config 語法的差別（script vs key=value）
- [ ] 知道何時選哪個
- [ ] 在 OVMF 跑過 systemd-boot

Part 3 結尾的練習：在 OVMF 上完整做一次「自己的 boot entry」流程。

→ [練習 A：在 OVMF 上加自製 boot entry](./practice-a-uefi-boot-entry.md)
