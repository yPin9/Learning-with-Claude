# Final Project — 從零組一個可開機系統

> **目標**：整合本課 70%+ 的核心概念，從零組一個完整的、能在 QEMU 從電源到 shell 全程自己控制的最小 Linux 系統——你選一條主線（BIOS bootloader 或 UEFI bootloader）載入 kernel，自製 initramfs 掛載真正的 root 並 switch_root，真正的 root 上跑一個 init。完成後你親手控制了開機接力的每一棒，徹底理解「電腦怎麼從電源變成 shell」。

## 專案概覽

你要組一個 **MiniBoot** 系統——一個你完全掌控的最小可開機 Linux：

```
MiniBoot 的完整開機鏈（你全程控制）：

  QEMU 電源
        │
  ┌─ 主線 A：自製 bootloader ──────────┐  ┌─ 主線 B：UEFI bootloader ─┐
  │  BIOS：兩階段 bootloader（練習 A）  │  │  UEFI app 載入 kernel     │
  │  real → protected → long           │  │  （練習 B 擴展）           │
  │  載入 kernel + initramfs            │  │  讀 kernel + initramfs    │
  │  按 boot protocol 跳 kernel（Ch20） │  │  ExitBootServices 跳 kernel│
  └────────────────┬───────────────────┘  └──────────┬────────────────┘
                   │（或用現成 kernel + QEMU -kernel 簡化）│
                   ▼
  kernel 解壓、初始化（Ch 21-22）
        │
  自製 initramfs（練習 C）：
    載入驅動、掛真正 root、switch_root
        │
  真正的 root：跑一個 init（busybox init 或簡單 systemd）
        │
  → shell prompt（你全程控制的系統！）
```

這個專案是全課的縮影——你把學的每個階段串成一條你親手控制的開機鏈。

## 整合的核心概念（對照表）

| 概念 | 章節 | 在本專案的應用 |
|---|---|---|
| 開機接力全圖 | Ch 1 | 整個專案的骨架 |
| CPU 模式切換 | Ch 7-8 | 自製 bootloader（主線 A）|
| boot protocol | Ch 20 | bootloader 交棒 kernel |
| kernel 解壓/初始化 | Ch 21-22 | 理解 kernel 接手後做什麼 |
| kernel → init | Ch 23 | execve init |
| initramfs 機制 | Ch 24 | 自製 initramfs 掛 root |
| 自製 initramfs | Ch 25 + 練習 C | cpio、/init、switch_root |
| init 系統 | Ch 26 | 真正 root 的 init |
| 開機診斷 | Ch 29 | debug 整個過程 |
| BIOS bootloader | 練習 A | 主線 A |
| UEFI bootloader | 練習 B | 主線 B |
| initramfs + switch_root | 練習 C | 核心元件 |

## 任務規格

組一個完整的 MiniBoot 系統。**選一條主線**（A 或 B），完成完整開機鏈：

### 共通要求（兩條主線都要）
- **kernel**：用系統現有的 kernel（`/boot/vmlinuz`）或自編一個 minimal kernel
- **自製 initramfs**：載入必要驅動、掛載真正的 root、switch_root（練習 C 的核心）
- **真正的 root**：一個獨立的磁碟 image（ext4），含 busybox + 一個真正的 init
- **真正的 init**：busybox init（或簡單的 /sbin/init 腳本），啟動幾個「服務」（如印訊息、給 shell）
- **QEMU 從電源到 shell**：完整跑通，每一棒你都理解

### 主線 A（BIOS，硬核）
- 用練習 A 的兩階段 bootloader（real → protected → long）
- bootloader 從磁碟載入 kernel 和 initramfs
- 按 Linux boot protocol（Ch 20）填 boot_params、跳 kernel
- （這條最難，因為要自己做完整的 boot protocol handover）

### 主線 B（UEFI，較易整合真 kernel）
- 用練習 B 的 UEFI app 擴展
- 用 file system protocol 讀 kernel 和 initramfs
- ExitBootServices，按 boot protocol 跳 kernel
- 或用 EFI stub（Ch 16）讓 kernel 自己當 bootloader（最簡單）

### 簡化選項（聚焦 initramfs + init）
- 用 QEMU 的 `-kernel` + `-initrd`（QEMU 當 bootloader）
- 專注做好「自製 initramfs + 真正 root + 真正 init」這後半段
- （適合想聚焦 kernel 之後的部分，跳過自製 bootloader 的複雜）

## 驗收標準

```
□ QEMU 開機，完整跑通到 shell
□ 自製 initramfs 正確掛載真正的 root 並 switch_root（Ch 24-25）
□ 真正的 root 是獨立的 ext4 磁碟（不是 initramfs 的 tmpfs）
□ 真正的 init 是 PID 1，啟動了幾個「服務」
□ 在最終 shell 裡，mount 顯示 root 是 ext4 磁碟，PID 1 是真正的 init
□ 你能解釋開機鏈的每一棒（從 QEMU 電源到 shell）

主線 A 額外：
□ bootloader 完成 real→protected→long 並按 boot protocol 跳 kernel

主線 B 額外：
□ UEFI app 讀 kernel/initramfs 並 ExitBootServices 跳 kernel
□ 或用 EFI stub 直接開機

整合品質：
□ 故意弄壞任一棒（如 initramfs 缺驅動、init 退出），能診斷並修復（Ch 29）
```

## 實作藍圖

### 藍圖一：真正的 root（所有主線共通）

```bash
# 建一個 ext4 root，含 busybox + 真正的 init（多個「服務」）
dd if=/dev/zero of=root.img bs=1M count=128
mkfs.ext4 root.img
sudo mount -o loop root.img /mnt
sudo mkdir -p /mnt/{bin,sbin,etc,proc,sys,dev}
sudo cp /bin/busybox /mnt/bin/
# ... busybox symlinks ...

# 真正的 init（busybox init 風格，啟動「服務」）
sudo tee /mnt/sbin/init > /dev/null <<'EOF'
#!/bin/sh
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev 2>/dev/null

echo "===================================="
echo " MiniBoot: Real root init (PID $$)"
echo "===================================="
# 啟動幾個「服務」（模擬真實 init）
echo "[service] Starting logger..."
( while true; do echo "log tick $(date +%s)" >> /var/log/mini.log; sleep 10; done ) &
echo "[service] Starting shell..."
mount | grep ' / '   # 證明 root 是 ext4
exec /bin/sh
EOF
sudo chmod +x /mnt/sbin/init
sudo umount /mnt
```

### 藍圖二：自製 initramfs（練習 C 的核心）

```bash
# initramfs：掛真正 root + switch_root（練習 C）
mkdir -p initramfs/{bin,proc,sys,dev,mnt}
cp /bin/busybox initramfs/bin/
# ... symlinks: sh, mount, switch_root ...

cat > initramfs/init <<'EOF'
#!/bin/sh
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev 2>/dev/null
echo "[initramfs] Loading drivers, mounting real root..."
# modprobe 需要的驅動（如果 kernel 沒內建）
mkdir -p /mnt/root
mount -o rw /dev/vda /mnt/root || { echo "mount failed"; exec /bin/sh; }
echo "[initramfs] switch_root to real root..."
exec switch_root /mnt/root /sbin/init
EOF
chmod +x initramfs/init
( cd initramfs && find . | cpio -o -H newc 2>/dev/null | gzip ) > initramfs.cpio.gz
```

### 藍圖三：開機（選你的主線）

```bash
# 簡化選項（QEMU 當 bootloader，聚焦 initramfs + init）：
qemu-system-x86_64 \
    -kernel /boot/vmlinuz-$(uname -r) \
    -initrd initramfs.cpio.gz \
    -drive file=root.img,format=raw,if=virtio \
    -append "console=ttyS0 root=/dev/vda rw" \
    -nographic -m 512

# 主線 A（自製 BIOS bootloader）：
# 把練習 A 的 bootloader 擴展成「從磁碟讀 kernel + initramfs，
# 填 boot_params，跳 kernel」（Ch 20）
# 組成磁碟 image：bootloader + kernel + initramfs

# 主線 B（UEFI bootloader）：
# 把練習 B 的 UEFI app 擴展成「讀 kernel + initramfs，
# ExitBootServices，跳 kernel」（Ch 16）
# 或用 EFI stub：efibootmgr 指向 vmlinuz，QEMU + OVMF
```

### 藍圖四：完整測試

```bash
# 跑通後，在最終 shell 驗證：
/ # echo $$              # 1（PID 1，真正的 init）
/ # mount | grep ' / '   # /dev/vda on / type ext4（真正的 root）
/ # cat /var/log/mini.log  # logger 服務在跑
/ # ps                   # 看 process（init + logger + shell）
```

## 完整參考實作

這個 Final Project 整合了三個練習（A/B/C）和全課概念。參考實作分散在各練習：

<details>
<summary>整合指引</summary>

- **真正的 root + init**：藍圖一是新的（練習 C 的 root 更簡單，這裡的 init 啟動「服務」更接近真實）
- **自製 initramfs**：直接用練習 C 的解答（掛 root + switch_root）
- **主線 A bootloader**：練習 A 的解答 + Ch 20 的 boot protocol handover（讀 kernel/initramfs 進記憶體、填 boot_params、跳 64-bit entry）。這是最難的部分——完整的 Linux boot protocol 實作很複雜，建議先用簡化選項跑通整個鏈，再挑戰主線 A
- **主線 B bootloader**：練習 B 的解答 + Ch 16 的讀 kernel + ExitBootServices + 跳 kernel。或用 EFI stub（最簡單，kernel 自己當 bootloader）

**建議路徑**：
1. 先用「簡化選項」（QEMU -kernel -initrd）跑通「自製 initramfs + 真正 root + 真正 init」這後半段——確認你掌握 kernel 之後的所有環節
2. 再挑戰主線 A 或 B 的自製 bootloader，替換掉 QEMU 的 bootloader 角色
3. 最終：從你的 bootloader 一路到 shell，全程你的 code

完整的「自製 bootloader 載入真 Linux kernel」是進階挑戰——Linux boot protocol（Ch 20）的完整實作（填好 boot_params 的每個欄位、e820/efi memory map、cmdline、initramfs 位址）相當繁瑣。很多人的 Final Project 用簡化選項（QEMU 當 bootloader）聚焦 initramfs+init，這完全 OK——重點是理解整個鏈。

</details>

## 自我評估 Checklist

完成後，用這個檢驗你的理解：

**開機鏈掌握**
- [ ] 能畫出你的 MiniBoot 從 QEMU 電源到 shell 的完整接力圖
- [ ] 每一棒你都能解釋「它做什麼、交給誰、怎麼交」
- [ ] 自製 initramfs 的 /init 你完全理解（掛 root、switch_root）
- [ ] 真正 root 的 init 是 PID 1，你理解它和 initramfs 的 /init 的區別

**技術深度**
- [ ]（主線 A）能解釋 real→protected→long 的每一步和 boot protocol handover
- [ ]（主線 B）能解釋 UEFI 讀 kernel、ExitBootServices、跳 kernel
- [ ] 能解釋 switch_root 切換了什麼（root、釋放 initramfs、execve 新 init）
- [ ] 能用 mount/ps 驗證系統處於「真正 root + 真正 init」狀態

**診斷能力（Ch 29）**
- [ ] 故意弄壞 initramfs（缺驅動）→ 能診斷（(initramfs)#）並修復
- [ ] 故意讓 init 退出 → 能診斷（Attempted to kill init）並修復
- [ ] 故意改錯 root= → 能診斷並修復

## 延伸挑戰

- **挑戰一（主線 A 完整版）**：完整實作 Linux boot protocol 的自製 BIOS bootloader——讀 bzImage、解析 setup header、填完整的 boot_params（e820、cmdline、initramfs）、跳 64-bit entry。這是最硬核的挑戰

- **挑戰二（複雜 root）**：真正的 root 用 LVM 或 LUKS 加密，initramfs 裡組裝/解密（Ch 24）。體驗「為什麼複雜 root 需要 initramfs」

- **挑戰三（真 init 系統）**：真正的 root 上跑真正的 systemd（或更完整的 busybox init），啟動真正的服務（網路、SSH）

- **挑戰四（自編 minimal kernel）**：自己編譯一個 minimal Linux kernel（`make tinyconfig` + 必要選項），用它而非系統 kernel。理解 kernel config 對開機的影響

- **挑戰五（UKI）**：把 kernel + initramfs + cmdline 打包成 Unified Kernel Image（Ch 17），用 UEFI 直接開機

## 結語

你從「按下電源，CPU 從 reset vector 開始」學起，現在能：

- 解釋開機接力的每一棒（firmware → bootloader → kernel → initramfs → init）
- 親手寫 BIOS bootloader（real→protected→long）和 UEFI bootloader
- 理解 kernel 怎麼解壓、初始化、execve 第一個 process
- 自製 initramfs 掛載真正的 root 並 switch_root
- 診斷任何階段的開機問題
- 把 x86 開機知識放進 ARM/UEFI/Secure Boot 的更廣脈絡

開機不再是黑盒子。下次你的伺服器開不了機、或你想自製一個嵌入式系統、或面試問「電腦開機時發生什麼」，你有完整的、動手驗證過的答案。

去組一個真正的最小 Linux 吧——從 kernel 到 shell，全部你掌控。
