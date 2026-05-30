# 練習 C — 自製 initramfs + switch_root

> **目標**：整合 Ch 21–25 的 kernel 啟動知識，做一個**完整的** initramfs——不只給 shell（Ch 25 的最小版），而是真的掛載一個獨立的 root 檔案系統並 `switch_root` 過去，執行真正 root 上的 init。完成後你掌握了現代 Linux 開機 kernel→initramfs→真正 root→init 這一整棒的完整機制。

## 背景與動機

Ch 25 你做了最小 initramfs（給個 shell）。但真實系統的 initramfs 做更多——它掛載真正的 root 檔案系統，然後把控制權交過去（switch_root）。這個練習做完整版：建一個獨立的 root 磁碟、寫 initramfs 掛它、switch_root 到真正的 root、執行真正 root 上的 init。

這正是你每天開機時 dracut 生成的 initramfs 做的事（只是它還處理 LVM/加密/各種驅動）。做過完整版，你對「為什麼開機要經過 initramfs」「switch_root 切換了什麼」有了親手驗證的理解。

## 任務規格

建立並驗證一個完整的 kernel→initramfs→真正 root 開機流程：

| 元件 | 要求 |
|---|---|
| 真正的 root（disk image）| 一個 ext4 磁碟 image，含 busybox + 一個 `/sbin/init`（真正的 init，印訊息證明切過來了）|
| initramfs | 掛載那個 root image、switch_root 過去、execve 真正的 /sbin/init |
| QEMU 開機 | kernel + initramfs + root disk，完整跑通，最後在真正 root 的 init |

**驗收標準**：
- QEMU 開機，先看到 initramfs 的訊息（"in initramfs"），再 switch_root，最後看到真正 root 的 init 訊息（"in real root, PID 1"）
- 在真正 root 的 shell 裡，`mount` 顯示 root 是那個 ext4 磁碟（不是 initramfs 的 tmpfs）
- `cat /proc/1/...` 確認 PID 1 是真正 root 的 init（不是 initramfs 的 /init）
- 故意讓 initramfs 缺少掛 root 的能力（如不 modprobe ext4），看掉到 emergency shell

**技術限制**：
- busybox-static（避免 library 麻煩）
- root 用 ext4（需要 kernel 有 ext4 支援，或 initramfs modprobe）
- 用 switch_root（不是 pivot_root）

## 期望輸出範例

```
$ make run
... kernel boot messages ...
[initramfs] Hello, I am the initramfs /init (PID 1)
[initramfs] Mounting real root /dev/vda...
[initramfs] Switching to real root...
[real-root] Hello from the REAL root's /sbin/init (PID 1)
[real-root] My root is:
/dev/vda on / type ext4 ...        ← 確認 root 是 ext4 磁碟
/ #                                 ← 真正 root 的 shell
```

## 如果你卡住了

1. 分兩部分：先建好「真正的 root」磁碟（能獨立驗證內容），再寫 initramfs 掛它
2. 真正 root 的 `/sbin/init` 也要是可執行的腳本/binary，且不能退出（PID 1 鐵律，Ch 25）
3. switch_root 的語法：`exec switch_root <新root掛載點> <新init路徑>`
4. root image 用 `mkfs.ext4` 建，掛載後放 busybox + /sbin/init，卸載
5. QEMU 的 root 磁碟是 `/dev/vda`（virtio）或 `/dev/sda`（看 -drive 設定）
6. 如果 switch_root 失敗，檢查：root 有沒有掛上（mount 成功？）、新 root 有沒有 /sbin/init、/sbin/init 可不可執行
7. kernel 可能需要 ext4 支援——用系統 kernel 通常內建 ext4，或在 initramfs modprobe

## 實作步驟建議

### Step 1：建「真正的 root」磁碟（ext4 + busybox + /sbin/init）
### Step 2：寫 initramfs 的 /init（掛 root + switch_root）
### Step 3：打包 initramfs
### Step 4：QEMU 開機跑通
### Step 5：驗證 + 故意弄壞測試

## 完整參考解答

**寫完再看！**

<details>
<summary>build.sh（完整建置腳本）</summary>

```bash
#!/bin/bash
# build.sh — 建立完整的 root + initramfs
set -e

BUSYBOX=/bin/busybox   # static busybox（apt install busybox-static）

# ========================================
# Step 1: 建「真正的 root」磁碟
# ========================================
echo "=== Building real root disk ==="
rm -f rootfs.img
dd if=/dev/zero of=rootfs.img bs=1M count=64
mkfs.ext4 -q rootfs.img

# 掛載並填內容
mkdir -p mnt
sudo mount -o loop rootfs.img mnt

# 建立目錄結構
sudo mkdir -p mnt/{bin,sbin,proc,sys,dev,etc}

# 放 busybox + symlinks
sudo cp $BUSYBOX mnt/bin/busybox
sudo ln -sf busybox mnt/bin/sh
sudo ln -sf busybox mnt/bin/mount
sudo ln -sf busybox mnt/bin/ls
sudo ln -sf ../bin/busybox mnt/sbin/busybox  2>/dev/null || true

# 真正 root 的 /sbin/init（這是 switch_root 後執行的）
sudo tee mnt/sbin/init > /dev/null <<'EOF'
#!/bin/sh
# 真正 root 的 init
mount -t proc none /proc 2>/dev/null
mount -t sysfs none /sys 2>/dev/null
echo ""
echo "[real-root] Hello from the REAL root's /sbin/init (PID $$)"
echo "[real-root] My root is:"
mount | grep ' / '
echo ""
exec /bin/sh
EOF
sudo chmod +x mnt/sbin/init

sudo umount mnt
echo "real root disk: rootfs.img ready"

# ========================================
# Step 2-3: 建 initramfs（掛 root + switch_root）
# ========================================
echo "=== Building initramfs ==="
rm -rf initramfs
mkdir -p initramfs/{bin,proc,sys,dev,mnt}

cp $BUSYBOX initramfs/bin/busybox
ln -sf busybox initramfs/bin/sh
ln -sf busybox initramfs/bin/mount
ln -sf busybox initramfs/bin/switch_root

# initramfs 的 /init
cat > initramfs/init <<'EOF'
#!/bin/sh
# initramfs /init：掛真正 root 並 switch_root

mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev 2>/dev/null

echo ""
echo "[initramfs] Hello, I am the initramfs /init (PID $$)"

# （真實系統這裡會 modprobe 驅動、組 LVM、解密 LUKS...）
# 我們的 root 是簡單的 ext4 磁碟 /dev/vda

echo "[initramfs] Mounting real root /dev/vda..."
mkdir -p /mnt/root
if ! mount -o ro /dev/vda /mnt/root; then
    echo "[initramfs] FAILED to mount root! Dropping to emergency shell."
    exec /bin/sh    # emergency shell（掛 root 失敗時）
fi

# switch_root 到真正的 root，execve 它的 /sbin/init
echo "[initramfs] Switching to real root..."
exec switch_root /mnt/root /sbin/init
EOF
chmod +x initramfs/init

# 打包成 cpio
( cd initramfs && find . | cpio -o -H newc 2>/dev/null | gzip ) > initramfs.cpio.gz
echo "initramfs: initramfs.cpio.gz ready"
```

</details>

<details>
<summary>Makefile</summary>

```makefile
KERNEL := /boot/vmlinuz-$(shell uname -r)

all: build

build:
	bash build.sh

run: build
	qemu-system-x86_64 \
	  -kernel $(KERNEL) \
	  -initrd initramfs.cpio.gz \
	  -drive file=rootfs.img,format=raw,if=virtio \
	  -append "console=ttyS0 root=/dev/vda" \
	  -nographic -m 512
	# -drive if=virtio → root 磁碟是 /dev/vda
	# 退出 QEMU: Ctrl-A 然後 X

# 故意弄壞：initramfs 不掛 root（看 emergency shell）
run-broken: build
	# 改 initramfs/init 把 mount 那行弄壞再打包...（手動測試）
	@echo "Edit initramfs/init to break mount, rebuild, run"

clean:
	rm -rf initramfs mnt initramfs.cpio.gz rootfs.img

.PHONY: all build run run-broken clean
```

```bash
make run    # 完整跑通：initramfs → switch_root → 真正 root
# Ctrl-A X 退出 QEMU
```

**解答說明**：

- **兩個 init**：initramfs 的 `/init`（掛 root）和真正 root 的 `/sbin/init`（switch_root 後執行）。兩個都不能退出（都 exec /bin/sh）
- **switch_root 的本質**：`exec switch_root /mnt/root /sbin/init` 把 /mnt/root 變成新的 /，釋放 initramfs，execve 新 root 的 /sbin/init（Ch 24）
- **root 是 /dev/vda**：QEMU 用 `if=virtio`，磁碟是 /dev/vda。`root=/dev/vda` 傳給 kernel（雖然我們的 initramfs 寫死掛 /dev/vda，真實系統會讀 root= 參數）
- **驗證切換成功**：真正 root 的 init 印 `mount | grep ' / '`，顯示 root 是 ext4 的 /dev/vda（不是 initramfs 的 rootfs）。PID 還是 1（switch_root 後 init 接管 PID 1）
- **emergency shell**：mount 失敗時 `exec /bin/sh` 給救援 shell（模擬 Ch 24 的 `(initramfs)#`）

</details>

## 測試用案例

| 操作 | 預期 | 驗證 |
|---|---|---|
| `make run` | 先 initramfs 訊息，再 switch_root，再真正 root 訊息 | 完整流程 |
| 真正 root 的 `mount \| grep / ` | root 是 ext4 /dev/vda | switch_root 成功 |
| `echo $$` 在真正 root shell | 1（PID 1）| init 接管 PID 1 |
| initramfs 不 mount root | 掉到 "emergency shell" | 掛 root 失敗的處理 |
| 真正 root 的 /sbin/init 不 chmod +x | switch_root 後 panic（No init）| init 可執行的要求 |
| 真正 root 的 init 直接退出（不 exec）| "Attempted to kill init" panic | PID 1 鐵律 |

## 延伸挑戰（加分）

- **挑戰一**：讓 initramfs 讀 kernel 的 `root=` 參數（從 `/proc/cmdline` parse）決定掛哪個磁碟，而非寫死 `/dev/vda`——這是真實 initramfs 的做法

- **挑戰二**：root 用 LVM——在 rootfs.img 外包一層 LVM（用 losetup + pvcreate/vgcreate/lvcreate），initramfs 裡 `modprobe dm-mod` + `lvm vgchange -ay` 啟用後再掛。體驗「為什麼複雜儲存需要 initramfs」

- **挑戰三**：root 加密（LUKS）——用 cryptsetup 加密 rootfs，initramfs 裡 `cryptsetup open` 解密（會問密碼）後掛。這是現代加密筆電的開機流程

- **挑戰四**：真正的 root 上裝 systemd（或 busybox 的 init），讓 switch_root 後跑一個真正的 init 系統而非簡單 shell——通往 Ch 26 和 Final Project

## 自我檢核

- [ ] 能不看參考做出「initramfs 掛真正 root + switch_root」的完整流程
- [ ] 理解 initramfs 的 /init 和真正 root 的 /sbin/init 是兩個不同的 init
- [ ] 知道 switch_root 切換了什麼（新 root 變 /、釋放 initramfs、execve 新 init）
- [ ] 能驗證 switch_root 成功（root 變成真正的磁碟、PID 1 是真正的 init）
- [ ] 理解為什麼複雜 root（LVM/加密）必須經過 initramfs（kernel 不能直接掛）

→ [Ch 26 init 系統：從 SysV 到 systemd](./26-init-systemd.md)
