# Ch 0 — 環境搭建：QEMU + initramfs + kernel build + gdb remote

> 目標：建立一個「隨時可以換 kernel 版本、可以開關任何 mitigation、可以 gdb 進去、可以丟 static exploit 進去跑」的 kernel pwn 工作站。之後 26 章都站在這個環境上，這章裝完後面沒必要再動。

## 這一章要做什麼

kernel pwn 的訓練靶場長這樣：

```
    你的 host (WSL2 Ubuntu)
    ┌──────────────────────────────────────────────┐
    │                                              │
    │   linux-6.6/            ← kernel 原始碼      │
    │     .config             ← mitigation 開關    │
    │     arch/x86/boot/bzImage  ← 編譯出的 kernel │
    │     vmlinux             ← gdb 讀的 symbol    │
    │                                              │
    │   initramfs/            ← 模擬 rootfs        │
    │     ├─ bin/busybox      ← 一切 cmd           │
    │     ├─ init             ← 開機腳本           │
    │     ├─ home/user/exploit ← 你的 exploit 丟這 │
    │     └─ vuln.ko          ← 題目 module        │
    │                                              │
    │   initramfs.cpio.gz     ← 打包給 QEMU        │
    │                                              │
    │   run.sh                ← QEMU 啟動腳本      │
    │                                              │
    └──────────────────────────────────────────────┘
                     │
                     │  qemu-system-x86_64 -s -kernel ... -initrd ...
                     ▼
    ┌──────────────────────────────────────────────┐
    │   guest VM（就是我們要打的 kernel）          │
    │                                              │
    │   / # ./exploit                              │
    │   [  1.234] vuln: module loaded              │
    │   ...                                        │
    │   # id                                       │
    │   uid=0(root)                                │
    └──────────────────────────────────────────────┘
                     ▲
                     │  tcp :1234
                     │
                gdb-multiarch vmlinux
```

重點：kernel pwn 沒有「用另一個工具代替」的空間。QEMU 是唯一穩定又快的沙箱，initramfs 是唯一輕量的 rootfs，你真的要一層層自己 build。**別想找捷徑**（docker 代替 QEMU 之類），真實題目都長這樣，跳過就永遠和 kernelCTF 的 run 環境脫節。

## Step 1 — WSL2 + Ubuntu 22.04

kernel build、QEMU、gdb-multiarch 在 Linux 生活最順。原生 Windows 上幾乎都跑不起來，macOS 只能用 UTM 模擬，WSL2 是 Windows 上最乾淨的選項。

如果你跑過 `symex_taint` 或 `afl_plus_plus`，WSL 應該已經有了，跳到 Step 2。沒有的話：

```powershell
# PowerShell 管理員模式
wsl --install -d Ubuntu-22.04
```

重開機，進 Ubuntu 設好帳號。之後**所有指令都在 WSL bash 裡跑**。選 22.04 是因為 kernel 6.6 的 build 在 Ubuntu 22.04 / 24.04 都可以，但 24.04 的 gcc-13 對部分舊 kernel module warning 轉 error，22.04 + gcc-11 最不會卡。

## Step 2 — 工具鏈

```bash
sudo apt update
sudo apt install -y \
    build-essential git curl wget unzip cpio bc kmod \
    flex bison libelf-dev libssl-dev libncurses-dev \
    qemu-system-x86 qemu-utils \
    gdb gdb-multiarch \
    python3 python3-pip \
    vim
```

- `flex bison libelf-dev libssl-dev libncurses-dev`：build kernel 必備
- `cpio bc kmod`：打包 initramfs、kernel build 工具
- `qemu-system-x86`：跑 guest VM
- `gdb-multiarch`：能讀 vmlinux 的 DWARF debug info

驗證：

```bash
qemu-system-x86_64 --version | head -1
# QEMU emulator version 6.x 或更新
gdb-multiarch --version | head -1
# GNU gdb (Ubuntu 12.x) ...
```

## Step 3 — 目錄結構

後面 26 章會一直操作下面這個樹，先建好：

```bash
mkdir -p ~/kpwn/{kernel,busybox,initramfs,module,exploit,scripts}
cd ~/kpwn
```

```
~/kpwn/
├── kernel/      ← linux source 解壓到這
├── busybox/     ← busybox source 解壓到這
├── initramfs/   ← guest VM 的 rootfs
├── module/      ← 每章的 vulnerable module
├── exploit/     ← 每章的 exploit
└── scripts/     ← run.sh、make-initramfs.sh 之類
```

## Step 4 — Build Linux kernel 6.6 LTS

挑 **6.6 LTS** 的理由：現行 LTS，有 `CONFIG_RANDOM_KMALLOC_CACHES`（Ch 17 會打它），kernelCTF 的 LTS 賽道也在這上面。新版太新、舊版太缺 mitigation。

下載 source（版本挑 6.6.60 或更新的 6.6.y，到 <https://cdn.kernel.org/pub/linux/kernel/v6.x/> 找最新）：

```bash
cd ~/kpwn/kernel
wget https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.60.tar.xz
tar xf linux-6.6.60.tar.xz
cd linux-6.6.60
```

最小化 + debug 友善的 config：

```bash
make defconfig                              # x86-64 預設
./scripts/config --enable DEBUG_INFO         # gdb 讀得到符號
./scripts/config --enable DEBUG_INFO_DWARF4  # 用 dwarf4，gdb 最相容
./scripts/config --enable FRAME_POINTER      # 棧回溯正確
./scripts/config --enable GDB_SCRIPTS        # 附送 gdb 輔助腳本
./scripts/config --enable RELOCATABLE        # KASLR 需要
./scripts/config --enable RANDOMIZE_BASE     # KASLR 本體（啟不啟等後面章節）
./scripts/config --disable DEBUG_INFO_REDUCED  # 別砍符號
./scripts/config --disable RANDOMIZE_MEMORY  # 先關 memory KASLR 便於 debug
./scripts/config --set-val SYSTEM_TRUSTED_KEYS ''  # 避免 build 卡在 key
./scripts/config --set-val SYSTEM_REVOCATION_KEYS ''
```

開始 build（第一次大概 10–30 分鐘，看你機器）：

```bash
make -j$(nproc) bzImage
make -j$(nproc) modules   # 我們自己不用 modules，但 headers 會生成
```

產出：

- `arch/x86/boot/bzImage` — QEMU `-kernel` 要的壓縮 kernel image
- `vmlinux` — 未壓縮含 symbol 的 ELF，**gdb 讀這個**

確認大小合理：

```bash
ls -lh arch/x86/boot/bzImage vmlinux
# bzImage 大概 10–15 MB
# vmlinux 大概 400 MB+（含 debug info）
```

## Step 5 — Build BusyBox（initramfs 的全部指令）

guest VM 裡沒 glibc、沒 bash、沒 ls — 一切要 busybox 提供。

```bash
cd ~/kpwn/busybox
wget https://busybox.net/downloads/busybox-1.36.1.tar.bz2
tar xf busybox-1.36.1.tar.bz2
cd busybox-1.36.1

make defconfig
./scripts/config --enable STATIC   # 靜態連結，不依賴 libc
make -j$(nproc)
```

產出：`busybox` 這隻單一 binary，大約 2 MB 左右，後面 initramfs 的 `ls`、`mount`、`cat`、`/bin/sh` 全部靠它。

## Step 6 — 組 initramfs

這步最容易卡人。原理：initramfs 就是一個 cpio 壓縮檔，裡面是 rootfs 的目錄結構，kernel 啟動時把它解到記憶體當 `/`，執行 `/init`。

```bash
cd ~/kpwn/initramfs
mkdir -p bin sbin etc proc sys dev tmp home/user

# 放 busybox
cp ~/kpwn/busybox/busybox-1.36.1/busybox bin/
# 建 busybox symlink（ls / sh / cat ... 都指向 busybox）
for cmd in sh ls cat echo mount umount mknod mkdir chmod insmod rmmod lsmod \
           dmesg cp mv rm find grep sleep id whoami su poweroff reboot; do
    ln -sf busybox bin/$cmd
done
```

寫 init 腳本：

```bash
cat > init <<'EOF'
#!/bin/sh
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev 2>/dev/null || mdev -s

echo "=== kernel pwn lab ==="
dmesg -n 1   # 安靜一點

# 開一個普通 user，default 是 root — 後面練習需要 unprivileged shell 時切 user
echo "root:x:0:0:root:/root:/bin/sh" > /etc/passwd
echo "user:x:1000:1000:user:/home/user:/bin/sh" >> /etc/passwd

# 想預設 root 就 exec sh；想預設 user 就 exec su user -c sh
exec /bin/sh
EOF
chmod +x init
```

打包腳本（之後每次改完 rootfs 都跑一次）：

```bash
cat > ~/kpwn/scripts/make-initramfs.sh <<'EOF'
#!/bin/bash
set -e
cd ~/kpwn/initramfs
find . -print0 | cpio --null -ov --format=newc 2>/dev/null | gzip -9 > ~/kpwn/initramfs.cpio.gz
echo "initramfs size: $(du -h ~/kpwn/initramfs.cpio.gz | cut -f1)"
EOF
chmod +x ~/kpwn/scripts/make-initramfs.sh

~/kpwn/scripts/make-initramfs.sh
# initramfs size: 1.x M
```

## Step 7 — QEMU 啟動腳本

```bash
cat > ~/kpwn/scripts/run.sh <<'EOF'
#!/bin/bash
cd ~/kpwn
qemu-system-x86_64 \
    -kernel kernel/linux-6.6.60/arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr quiet panic=1 oops=panic" \
    -m 512M \
    -cpu qemu64,+smep,+smap \
    -smp 1 \
    -monitor none \
    -no-reboot \
    -nographic \
    -s
EOF
chmod +x ~/kpwn/scripts/run.sh
```

幾個關鍵 flag：

| Flag | 用途 |
|---|---|
| `-kernel` | 直接給 kernel image，不走 bootloader |
| `-initrd` | 附 initramfs 當 rootfs |
| `-append "..."` | kernel command line — `nokaslr` 先關 KASLR 方便 debug |
| `-cpu qemu64,+smep,+smap` | **明確打開** SMEP + SMAP（預設 qemu64 沒開） |
| `-smp 1` | 單 CPU，race condition 章節會改 |
| `-s` | `-gdb tcp::1234` 的縮寫 — gdb 遠端 attach 用 |
| `-nographic` | 串口當 console，不開視窗 |
| `panic=1 oops=panic` | kernel 出事就重啟（避免 exploit 爛掉後卡住） |

跑：

```bash
~/kpwn/scripts/run.sh
```

你應該看到 kernel boot log → `=== kernel pwn lab ===` → `/ #`。

按 `Ctrl-A x` 退出 QEMU（`Ctrl-A` 是 screen escape，`x` 是 exit）。

## Step 8 — gdb remote debug

開另一個 WSL terminal：

```bash
cd ~/kpwn/kernel/linux-6.6.60
gdb-multiarch vmlinux
```

進 gdb 後：

```
(gdb) target remote :1234
Remote debugging using :1234
(gdb) b start_kernel
(gdb) c
```

如果 guest 剛 boot，你可能會停在 `start_kernel` — 看到符號、參數、源碼行號，就對了。

如果你沒看到符號、只看到 raw 地址，那是 `vmlinux` 有問題，回 Step 4 確認 `DEBUG_INFO` 有開、rebuild。

繼續 guest 執行：

```
(gdb) c
```

想中斷 guest 回 gdb：gdb 裡打 `Ctrl-C`。

## Step 9 — 第一個 kernel module（確認 module 路徑通）

Ch 2 會正式寫 vulnerable module，這裡只驗「module 能編、能載、能看 dmesg」。

```bash
mkdir -p ~/kpwn/module/hello
cd ~/kpwn/module/hello

cat > hello.c <<'EOF'
#include <linux/module.h>
#include <linux/init.h>

static int __init hello_init(void) {
    pr_info("hello kpwn: module loaded at %px\n", hello_init);
    return 0;
}

static void __exit hello_exit(void) {
    pr_info("hello kpwn: bye\n");
}

module_init(hello_init);
module_exit(hello_exit);
MODULE_LICENSE("GPL");
EOF

cat > Makefile <<'EOF'
obj-m += hello.o
KDIR := /home/$(USER)/kpwn/kernel/linux-6.6.60

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
EOF

make
ls hello.ko
```

把 `.ko` 丟進 initramfs、重打包、開 QEMU：

```bash
cp hello.ko ~/kpwn/initramfs/
~/kpwn/scripts/make-initramfs.sh
~/kpwn/scripts/run.sh
```

在 guest VM 裡：

```
/ # insmod /hello.ko
/ # dmesg | tail -2
[    x.xxxxxx] hello kpwn: module loaded at ffffffff...
/ # rmmod hello
```

看到 `hello kpwn: module loaded at ffffffff...` 就通了。記下這個地址 — 這是 kernel text 地址，你剛親眼看到 KASLR 關掉時 kernel 在哪裡。

## Step 10 — Static exploit 編譯

guest initramfs 裡沒 libc。exploit 要 `gcc -static`：

```bash
mkdir -p ~/kpwn/exploit
cat > ~/kpwn/exploit/hello-pwn.c <<'EOF'
#include <stdio.h>
#include <unistd.h>

int main() {
    printf("hello from userspace, uid=%d\n", getuid());
    return 0;
}
EOF

gcc -static -O2 -o ~/kpwn/exploit/hello-pwn ~/kpwn/exploit/hello-pwn.c
# 檢查確實是靜態連結
file ~/kpwn/exploit/hello-pwn
# ... statically linked ...
```

丟進 guest 試：

```bash
cp ~/kpwn/exploit/hello-pwn ~/kpwn/initramfs/home/user/
~/kpwn/scripts/make-initramfs.sh
~/kpwn/scripts/run.sh
```

guest 裡：

```
/ # /home/user/hello-pwn
hello from userspace, uid=0
```

之後所有 exploit 都用 `gcc -static` 編、丟 `/home/user/`、在 guest 跑。

## 常見踩雷

**`make: *** No rule to make target 'modules'`** — `KDIR` 路徑指錯、或 kernel 還沒編完（缺 `Module.symvers`）。先確認 `ls $KDIR/Module.symvers` 存在。

**`insmod: ERROR: could not insert module: Invalid module format`** — 90% 是 module 是拿 host kernel headers 編的，而不是你 `~/kpwn/kernel/` 裡那個 6.6.60 source。檢查 Makefile 的 `KDIR`。

**QEMU 黑螢幕一直不動** — 通常是 kernel panic 但 `-append` 沒加 `console=ttyS0`。加回去。或 `-nographic` 漏掉也會沒輸出。

**gdb 連不上 `:1234`** — QEMU 沒跑、或 `-s` 沒加。`ss -tlnp | grep 1234` 看 port 在不在。

**exploit 丟進 initramfs 跑不起來、報 `not found`** — 你 `cp` 進去後沒重跑 `make-initramfs.sh`。initramfs 是靜態打包的 cpio，不是 live 目錄，每次改都要重打包。

**KASLR 沒關掉** — `-append` 裡的 `nokaslr` 是必要字串，不是 `noaslr`。打錯沒效果。

**`ls -lh vmlinux` 只有 10 MB 出頭** — debug info 沒編進去。回 Step 4 的 config、rebuild。

## 一次檢查：env-check.sh

所有工具 + 整條鏈路一次驗完。把這個丟到 `~/kpwn/scripts/env-check.sh` 留著：

```bash
cat > ~/kpwn/scripts/env-check.sh <<'EOF'
#!/bin/bash
set -e
echo "=== host tools ==="
qemu-system-x86_64 --version | head -1
gdb-multiarch --version | head -1
gcc --version | head -1
echo "=== artifacts ==="
ls -lh ~/kpwn/kernel/linux-6.6.60/arch/x86/boot/bzImage
ls -lh ~/kpwn/kernel/linux-6.6.60/vmlinux | awk '{print $5, $NF}'
ls -lh ~/kpwn/busybox/busybox-1.36.1/busybox
ls -lh ~/kpwn/initramfs.cpio.gz
echo "=== symbols in vmlinux ==="
nm ~/kpwn/kernel/linux-6.6.60/vmlinux | grep -E " (commit_creds|prepare_kernel_cred|modprobe_path)$"
EOF
chmod +x ~/kpwn/scripts/env-check.sh
~/kpwn/scripts/env-check.sh
```

最後那個 `nm ... grep` 能列出 `commit_creds`、`prepare_kernel_cred`、`modprobe_path` 三個符號 — 你後面每章都會用到它們的地址。現在能看到代表 symbol 沒被 strip、後面 Ch 5 直接能開幹。

## 自我檢核

- [ ] `~/kpwn/scripts/run.sh` 能開機到 `/ #`
- [ ] `~/kpwn/scripts/env-check.sh` 全綠，最後列出三個 kernel symbol 地址
- [ ] hello module 能 `insmod` 看到 dmesg
- [ ] `gcc -static` 編的 exploit 能丟進 guest 跑、`uid=0`
- [ ] gdb-multiarch 能 `target remote :1234`、在 `start_kernel` 斷點、看到 source

這章的產出會被後面 26 章反覆使用。**任何一項不確認過就往下，之後都會倒回來**。

下一章不寫 code，拉高視角把「user 程式呼叫一個 syscall，kernel 端發生了什麼」整條路畫清楚 — syscall instruction、entry_SYSCALL_64、sysret、swapgs — 每個名字在後面 SMAP、KPTI、ret2usr 章節都會回來。沒有這張地圖，後面 bypass 都是黑魔法。

→ [Ch 1 — Linux kernel 從 user 視角：syscall、user/kernel 切換、address space](./01-kernel-from-user-view.md)
