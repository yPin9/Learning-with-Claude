# Ch 25 — 寫一個自製 initramfs

> **目標**：親手打包一個最小的 initramfs——寫一個靜態 `/init`、用 cpio 打包、用 QEMU 開機進到 shell。這是 kernel 啟動線的動手里程碑，讓你徹底理解 initramfs 不是黑盒子，而 dracut/initramfs-tools 只是自動化了你手做的這些步驟。

> **環境**：Linux，QEMU，busybox（或 static binary），cpio。承接 Ch 24（initramfs 機制）。

## 為什麼親手做 initramfs？

Ch 24 你理解了 initramfs 的機制。現在親手做一個——這會把所有抽象概念變具體：cpio 怎麼打包、`/init` 怎麼寫、kernel 怎麼解開並執行它、為什麼 `/init` 是 PID 1。

做過一次，你對「kernel → initramfs → shell」這一棒就有了 X 光視野。dracut 那種複雜工具產生的 initramfs，本質就是你手做的這個東西的精緻版（多了驅動偵測、複雜儲存支援）。

## 先建立直覺：最小 initramfs 就是「一個 /init + 它需要的東西」

```
最小 initramfs 的內容：

  /init                  ← 必須！kernel execve 的就是這個（PID 1）
  /bin/sh（或 busybox）   ← /init 可能需要的 shell
  /bin/...               ← /init 需要的其他工具（mount、ls...）
  /lib/...               ← 那些工具需要的 library（除非用 static binary）
  /proc /sys /dev        ← 空目錄，給掛載虛擬檔案系統用
        │
  打包成 cpio archive
        │
  kernel 解開 → execve /init → 你的 /init 跑起來
```

最小 initramfs 的核心就是 `/init`——kernel 解開 initramfs 後 execve 它。我們的 `/init` 簡單做：掛載虛擬檔案系統、印個訊息、給一個 shell。真實的 initramfs 的 /init 還會掛真正的 root + switch_root（Ch 24），但最小版先讓你進到 shell。

## 用 busybox 做最小 initramfs

busybox 是「一個 binary 包含幾百個 Unix 工具」（sh、mount、ls、cat...），且能 static 編譯（不依賴 library）——完美的 initramfs 工具。

```bash
# 1. 取得 static busybox
sudo apt install busybox-static
which busybox          # /bin/busybox
file /bin/busybox     # ... statically linked（重要！不依賴 library）

# 2. 建立 initramfs 的目錄結構
mkdir -p initramfs/{bin,sbin,proc,sys,dev}
cd initramfs

# 3. 放 busybox 並建立 symlink（讓 sh、mount 等指向 busybox）
cp /bin/busybox bin/
ln -s busybox bin/sh
ln -s busybox bin/mount
ln -s busybox bin/ls
ln -s busybox bin/cat
# 或讓 busybox 自己建所有 symlink：
# bin/busybox --install -s bin/
```

## 寫 /init

```bash
# 4. 寫 /init（initramfs 的入口，會是 PID 1）
cat > init <<'EOF'
#!/bin/sh
# 最小 initramfs 的 /init

# 掛載基本虛擬檔案系統
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev 2>/dev/null

echo ""
echo "==================================="
echo " Hello from my custom initramfs!"
echo " I am PID $$"
echo "==================================="
echo ""

# 給一個互動 shell（busybox 的 sh）
exec /bin/sh
EOF

chmod +x init     # /init 必須可執行！
```

關鍵點：
- **`/init` 必須在 initramfs 根目錄**：kernel 找的就是 `/init`
- **必須可執行**（`chmod +x`）：否則 kernel execve 失敗
- **`echo "PID $$"`**：證明 `/init` 是 PID 1（`$$` 是當前 process 的 PID，會印 1）
- **`exec /bin/sh`**：用 shell 取代 /init（給你互動 shell）。如果 /init 結束（不 exec），kernel 會 panic（PID 1 不能退出！）

## 打包成 cpio

```bash
# 5. 打包成 cpio archive（在 initramfs 目錄裡）
find . | cpio -o -H newc | gzip > ../my-initramfs.cpio.gz
#       │     │  │  └──── newc 格式（kernel 要的 cpio 格式）
#       │     │  └─ create（建立 archive）
#       │     └─ cpio
#       └─ 列出所有檔案
# 結果：my-initramfs.cpio.gz（壓縮的 cpio）

cd ..
ls -lh my-initramfs.cpio.gz
```

cpio 的 `newc` 格式是 kernel 認的 initramfs 格式（Ch 24）。`find . | cpio -o` 把當前目錄的所有檔案打包，gzip 壓縮。

## 用 QEMU 開機

我們用系統現有的 kernel + 我們的 initramfs 開機（不需要真正的 root，因為 /init 直接給 shell）：

```bash
# 6. 用 QEMU 開機（kernel + 我們的 initramfs）
qemu-system-x86_64 \
    -kernel /boot/vmlinuz-$(uname -r) \
    -initrd my-initramfs.cpio.gz \
    -append "console=ttyS0" \
    -nographic \
    -m 512
#   -kernel：直接給 kernel（QEMU 當 bootloader）
#   -initrd：我們的 initramfs
#   -append：kernel command line（console=ttyS0 讓輸出走 serial）
#   -nographic：用終端機而非圖形視窗（輸出在你的 terminal）

# 應該看到：
# ... kernel 開機訊息 ...
# ===================================
#  Hello from my custom initramfs!
#  I am PID 1                         ← 證明 /init 是 PID 1
# ===================================
# /bin/sh:  ← 你進到 initramfs 的 shell！
```

`-kernel` + `-initrd` 讓 QEMU 直接載入 kernel 和 initramfs（QEMU 當 bootloader）。kernel 解開我們的 initramfs，execve `/init`，你進到 shell。`I am PID 1` 證明 /init 就是第一個 process。

退出 QEMU：`Ctrl-A` 然後 `X`。

## 進階：加上 switch_root（掛真正的 root）

最小版只給 shell。真實 initramfs 會掛真正的 root 並 switch_root（Ch 24）。練習 C 會做完整版，這裡看概念：

```bash
# /init 的進階版（掛真正 root + switch_root）
cat > init <<'EOF'
#!/bin/sh
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev

echo "Mounting real root..."
# 假設真正的 root 在 /dev/vda1（QEMU 的虛擬磁碟）
mkdir -p /mnt/root
mount -o ro /dev/vda1 /mnt/root

# switch_root 到真正的 root，execve 真正的 init
echo "Switching to real root..."
exec switch_root /mnt/root /sbin/init
EOF
```

這需要一個真正的 root 磁碟（QEMU 的 `-drive`）。練習 C 會完整做這個（建 root 檔案系統 + initramfs 掛它 + switch_root）。

## 故意弄壞：/init 退出

```bash
# 錯誤的 /init：沒有 exec，且結束了
cat > init <<'EOF'
#!/bin/sh
echo "Hello"
# 沒有 exec /bin/sh，腳本結束
EOF
# 打包開機後：
# Hello
# Kernel panic - not syncing: Attempted to kill init!
#   exitcode=0x00000000
#   ↑ PID 1（init）退出了 → kernel panic！
```

**PID 1 不能退出**。如果 `/init` 執行完就結束（沒有 `exec` 取代自己，也沒有無限迴圈），PID 1 退出，kernel panic（"Attempted to kill init!"）。這是 PID 1 的鐵律——它必須一直存在（要嘛 exec 成別的長存程式如 shell/systemd，要嘛自己無限迴圈）。這也解釋了為什麼真正的 init（systemd）是個常駐 daemon。

## 故意弄壞：/init 不可執行

```bash
# 忘記 chmod +x init
# 打包開機後：
# Kernel panic - not syncing: No working init found.
#   ↑ kernel 找到 /init 但不能執行它
```

`/init` 必須可執行。忘記 `chmod +x`，kernel execve 失敗，找不到能跑的 init，panic（Ch 23 的 "No working init found"）。

## 踩雷集錦

1. **/init 退出導致 panic**：PID 1 不能退出。/init 要 `exec` 成長存程式（shell/systemd）或無限迴圈。腳本跑完就結束 = panic

2. **/init 不可執行**：忘記 `chmod +x init`，kernel execve 失敗。/init 必須可執行

3. **用動態連結的 binary 但沒放 library**：如果 /init 或工具是動態連結（依賴 .so），要把 library 也放進 initramfs。用 static binary（busybox-static）最簡單，免去 library 麻煩

4. **cpio 格式錯**：kernel 要 `newc` 格式（`cpio -H newc`）。用其他格式 kernel 解不開

5. **/init 不在根目錄**：kernel 找 `/init`（根目錄）。放在 `/bin/init` 或別處，kernel 找不到。檔名必須是 `init`，在根

6. **忘記掛 /proc /sys /dev**：很多工具需要這些虛擬檔案系統。/init 要先 mount 它們，否則後續操作（如讀 /proc）失敗

## 進階：initramfs 的真實複雜度

我們的最小 initramfs 約 1MB（一個 busybox + 簡單 /init）。真實 initramfs（dracut/initramfs-tools 生成）複雜得多：

```
真實 initramfs 比最小版多的東西：
  - 大量驅動模組（你的硬體可能需要的，NVMe/SATA/USB/網卡...）
  - 複雜儲存支援（LVM、LUKS、RAID、iSCSI、NFS root...）
  - udev（裝置管理，等待裝置出現）
  - 完整的 /init 邏輯（處理各種 root 來源、錯誤恢復、emergency shell）
  - 微碼（microcode，Ch 24 進階）
        │
  → 真實 initramfs 約 30-100 MB
  → 但核心概念和你的最小版一樣：cpio + /init
```

> 做過最小 initramfs，你會發現 dracut 的複雜不是「不同的東西」，而是「同一個東西加上一堆 edge case 處理」。核心永遠是：cpio archive + 一個 /init。dracut 的價值在於它自動偵測你需要哪些驅動、自動處理 LVM/加密、自動加 emergency shell。但本質和你手做的一樣。這個理解讓你 debug 真實 initramfs 問題時有底氣——它不是魔法。

## 動手練習

1. 完整做出本章的最小 initramfs（busybox + /init），用 QEMU `-kernel -initrd` 開機，進到 shell，確認 `echo $$` 是 1（PID 1）

2. 在 initramfs shell 裡探索：`ls /`、`cat /proc/cmdline`、`mount`（看掛了什麼）、`busybox`（看 busybox 有哪些工具）

3. 故意弄壞：(a) /init 不 exec 直接結束 → 看 "Attempted to kill init" panic；(b) 忘記 chmod +x → 看 "No working init found"。理解 PID 1 的鐵律

4. 加功能：讓 /init 在給 shell 前印出 kernel 版本（`uname -a`）、記憶體（`cat /proc/meminfo | head`）、和一個倒數計時，體驗 early userspace 能做的事

## 本章重點整理

- 最小 initramfs = 一個 /init（kernel execve 的，PID 1）+ 它需要的工具（busybox-static 最方便）
- /init 必須在根目錄、可執行（chmod +x）；用 `find . | cpio -o -H newc | gzip` 打包
- QEMU `-kernel vmlinuz -initrd my-initramfs.cpio.gz` 直接開機測試（QEMU 當 bootloader）
- PID 1 不能退出：/init 要 exec 成長存程式（shell/systemd）或無限迴圈，否則 kernel panic
- 真實 initramfs（dracut）只是最小版加上驅動偵測、複雜儲存、edge case——核心都是 cpio + /init

## 自我檢核

- [ ] 能不看參考做出一個最小 initramfs 並用 QEMU 開機進 shell
- [ ] 知道 /init 必須在根目錄、可執行，且不能退出（否則 panic）
- [ ] 知道為什麼用 static binary（busybox-static）省去 library 麻煩
- [ ] 能解釋 cpio newc 格式、find | cpio 打包的流程
- [ ] 理解真實 initramfs（dracut）和最小版的關係（核心相同，多了 edge case）

## 延伸閱讀

### 官方文件

- **[Linux kernel: ramfs-rootfs-initramfs.rst (building initramfs)](https://www.kernel.org/doc/html/latest/filesystems/ramfs-rootfs-initramfs.html)**
  - **讀哪裡**：building initramfs 那節（cpio 打包）
  - **學什麼**：kernel 對 initramfs 格式的要求（cpio newc）
  - **前提**：本章 + Ch 24

### 部落格 / 文章

- **[Custom Initramfs (Gentoo Wiki)](https://wiki.gentoo.org/wiki/Custom_Initramfs)**
  - **這篇說什麼**：手工製作 initramfs 的完整指南，含 busybox、/init、switch_root
  - **讀哪裡**：整頁
  - **為什麼值得讀**：本章和練習 C 的最佳實作參考，把每步講清楚

- **[Minimal Linux Live](https://github.com/ivandavidov/minimal)** — Ivan Davidov
  - **這篇說什麼**：從零建一個最小可開機 Linux（kernel + busybox initramfs）
  - **讀哪裡**：build 流程
  - **為什麼值得讀**：把「最小 Linux」整個串起來，是 Final Project 的靈感來源

→ [練習 C：自製 initramfs + switch_root](./practice-c-initramfs.md)
