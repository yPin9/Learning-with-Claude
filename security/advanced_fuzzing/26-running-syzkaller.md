# Ch 26：從零跑起 syzkaller

> **目標**: 走完 kernel build → guest image → config.json → syz-manager 啟動的完整流程，理解每個環節的用途與常見炸點。

> **環境**: 需要 **實體 Linux 主機或具備 KVM 支援的 VM**（巢狀虛擬化）；WSL2 預設不支援 KVM，本章步驟皆標注「需 KVM 環境」或「未實測」。最低規格：4 核心、8 GB RAM、30 GB 磁碟。建議 Ubuntu 22.04 / Debian bookworm。

---

## 為什麼要跑 syzkaller，而不是直接用 AFL？

對一般 userspace binary 來說，AFL 已經夠用。但對 kernel syscall interface 來說，問題的性質完全不同：

- **觸發點是 syscall**，每個 syscall 有嚴格的型別語義（`fd` 要合法、`struct` 的 magic 要對），隨機 byte 流命中率極低。
- **執行環境需要隔離**：kernel crash 會把整台機器拉下來，必須在 VM 裡跑。
- **Coverage 要加在 kernel 裡**：AFL 的 `__AFL_LOOP` 沒辦法 inject 進 kernel，需要 KCOV。

syzkaller 把這三個問題一次解決：有 syzlang 的型別感知生成、有 QEMU guest 隔離、有 KCOV 驅動的 coverage feedback。

---

## 整體流程直覺

```
你的機器（host）
├─ syz-manager          ← 主控，讀 config.json
│   ├─ 管理 N 個 QEMU VM
│   ├─ 收 coverage / crash
│   └─ HTTP dashboard :port
│
├─ build/               ← 你自己 build 出來的東西
│   ├─ bzImage          ← step 1 產出
│   ├─ vmlinux          ← step 1 產出（KASAN 報告用）
│   └─ bookworm.img     ← step 2 產出（guest 磁碟）
│
└─ QEMU VM（跑起來後）
    ├─ syz-executor     ← 被 syz-manager 透過 SSH push 進去
    └─ kernel           ← 你 build 的那顆，帶 KCOV/KASAN
         │
         └─ 執行 syscall → crash → syz-manager 收到 → 存 workdir/crashes/
```

整個流程的依賴順序：

```
Step 1: build kernel (bzImage + vmlinux)
    ↓
Step 2: build guest image (bookworm.img + SSH key)
    ↓
Step 3: 寫 config.json（把上面的路徑填進去）
    ↓
Step 4: ./bin/syz-manager -config my.cfg
    ↓
Step 5: 瀏覽器看 dashboard，等 crash
```

---

## Step 1：Build Kernel

### 1-1 取得 kernel source

```bash
git clone --depth=1 https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git
cd linux
```

`--depth=1` 避免拉 20 GB 完整 history，但如果要 bisect 就需要完整 clone。

### 1-2 kernel config 清單

建一個 `.config` 或用 `make defconfig` 後再覆蓋，以下每個 option 都有存在理由：

```
# --- coverage instrumentation ---
CONFIG_KCOV=y
CONFIG_KCOV_ENABLE_COMPARISONS=y   # 比較值 feedback，對跳過 magic check 有幫助

# --- bug detectors ---
CONFIG_KASAN=y                     # heap UAF / OOB 偵測
CONFIG_KASAN_INLINE=y              # inline 版比 outline 快，適合 fuzzing
CONFIG_UBSAN=y                     # UB：signed overflow、misalign 等
CONFIG_DEBUG_KMEMLEAK=y            # memory leak，開了會慢很多，初期可以不開

# --- debug symbols（KASAN 報告需要 symbol 解析）---
CONFIG_DEBUG_INFO=y
CONFIG_DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT=y
CONFIG_FRAME_POINTER=y             # 讓 stack trace 更完整

# --- symbol table ---
CONFIG_KALLSYMS=y
CONFIG_KALLSYMS_ALL=y

# --- namespace（syzkaller 用來隔離 fuzzing process）---
CONFIG_NAMESPACES=y
CONFIG_UTS_NS=y
CONFIG_IPC_NS=y
CONFIG_PID_NS=y
CONFIG_NET_NS=y
CONFIG_USER_NS=y

# --- cgroup（syz-executor 沙盒化）---
CONFIG_CGROUP_PIDS=y
CONFIG_MEMCG=y

# --- fault injection（讓 fuzzer 能觸發錯誤路徑）---
CONFIG_FAULT_INJECTION=y
CONFIG_FAULT_INJECTION_DEBUG_FS=y
CONFIG_FAILSLAB=y
CONFIG_FAIL_PAGE_ALLOC=y
CONFIG_FAIL_MAKE_REQUEST=y
CONFIG_FAIL_IO_TIMEOUT=y
CONFIG_FAIL_FUTEX=y
CONFIG_FAULT_INJECTION_USERCOPY=y

# --- locking debug ---
CONFIG_LOCKDEP=y                   # 死鎖偵測
CONFIG_PROVE_LOCKING=y
CONFIG_DEBUG_ATOMIC_SLEEP=y

# --- 其他 hardening（讓 bug 更早爆）---
CONFIG_REFCOUNT_FULL=y
CONFIG_FORTIFY_SOURCE=y
```

把這些 option 整理成一個 fragment 檔 `syzkaller.config`，然後：

```bash
# 先產生 defconfig 基底
make defconfig

# 用 merge_config 合入 syzkaller 的 option
scripts/kconfig/merge_config.sh .config syzkaller.config

# 確認關鍵 config 有進去
grep -E "CONFIG_KCOV|CONFIG_KASAN|CONFIG_UBSAN" .config
```

（本段未實測，為理論預期行為）

### 1-3 安裝 build 依賴

在 Ubuntu/Debian 上：

```bash
sudo apt install build-essential flex bison bc libssl-dev libelf-dev \
    libncurses-dev git pahole dwarves python3
```

`pahole` / `dwarves` 在 kernel 6.x 之後是必要的（BTF debug info 生成需要），沒裝會在最後 link 時才報錯，白費前面幾十分鐘。

### 1-4 實際 build

```bash
make -j$(nproc) bzImage 2>&1 | tee build.log
```

**耗時預估**：4 核心機器約 20-40 分鐘，8 核心約 10-20 分鐘。第一次 build 包含 `vmlinux`，後續 incremental build 快很多。

build 完確認產出：

```bash
ls -lh arch/x86/boot/bzImage   # ~ 10-15 MB
ls -lh vmlinux                  # ~ 800 MB（帶 debug info）
```

在有 KVM 的 Linux 主機上驗證，先確認 build 沒錯誤再繼續。（需 KVM 環境）

### 1-5 安裝 syzkaller

```bash
# 需要 Go 1.21+
wget https://go.dev/dl/go1.21.0.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.21.0.linux-amd64.tar.gz
export PATH=$PATH:/usr/local/go/bin

git clone https://github.com/google/syzkaller
cd syzkaller
make
# 產出在 bin/ 下：syz-manager, syz-executor 等
ls bin/
```

（本段未實測，為理論預期行為）

---

## Step 2：Build Guest Image

syzkaller 在 `tools/create-image.sh` 提供官方腳本，用 `debootstrap` 建一個最小 Debian 系統。

### 2-1 安裝工具

```bash
sudo apt install debootstrap qemu-utils qemu-system-x86
```

### 2-2 跑建置腳本

```bash
cd /path/to/syzkaller
chmod +x tools/create-image.sh
./tools/create-image.sh -d bookworm -s 4096
```

參數說明：
- `-d bookworm`：Debian 版本（bookworm = Debian 12）
- `-s 4096`：image 大小 MB，4 GB 夠用

腳本會做這些事：
1. `debootstrap` 下載 Debian base system
2. 設定 root 密碼、SSH 設定（PermitRootLogin yes、PasswordAuthentication no）
3. 建立 SSH keypair（`bookworm.id_rsa` / `bookworm.id_rsa.pub`），把 pub key 放進 image
4. 用 `qemu-img` 把目錄轉成 raw image

產出：

```
bookworm.img        ← QEMU 磁碟 image
bookworm.id_rsa     ← SSH private key（syz-manager 用來連進 VM）
```

為什麼需要 SSH key：syz-manager 不用 password，完全靠 key 做 passwordless SSH，這樣才能在 crash 後自動重開 VM 並繼續 fuzzing，不需要人工介入。

（本段未實測，為理論預期行為。在有 KVM 的機器上驗證：跑完腳本後 `qemu-img info bookworm.img` 確認 image 正常。）

---

## Step 3：寫 config.json

這是整個 syzkaller 設定的核心。把 Step 1/2 的產出路徑填進去。

```json
{
    "target": "linux/amd64",
    "http": "127.0.0.1:56741",
    "workdir": "/home/user/syzkaller-workdir",
    "kernel_obj": "/home/user/linux",
    "image": "/home/user/bookworm.img",
    "sshkey": "/home/user/bookworm.id_rsa",
    "syzkaller": "/home/user/syzkaller",
    "procs": 8,
    "type": "qemu",
    "vm": {
        "count": 4,
        "kernel": "/home/user/linux/arch/x86/boot/bzImage",
        "cpu": 2,
        "mem": 2048,
        "qemu_args": "-enable-kvm -cpu host,migratable=off"
    }
}
```

### 欄位逐一說明

| 欄位 | 說明 |
|------|------|
| `target` | 目標架構，`linux/amd64` 是最常用。ARM64 用 `linux/arm64` |
| `http` | Web dashboard 監聽位址。填 `0.0.0.0:56741` 讓外部存取 |
| `workdir` | crash / corpus / log 的儲存根目錄，事先 `mkdir -p` |
| `kernel_obj` | kernel build 目錄，syzkaller 在這裡找 `vmlinux`，KASAN 報告 symbol 解析用 |
| `image` | guest 磁碟 image（Step 2 產出） |
| `sshkey` | SSH private key（Step 2 產出），syz-manager 用來 push executor / 取回 log |
| `syzkaller` | syzkaller 的根目錄（`bin/` 在這裡下面） |
| `procs` | 每個 VM 裡並行的 syz-executor 數量。通常 8 是合理起點 |
| `type` | VM 類型，`"qemu"` 是最常見。還有 `gce`、`gvisor`、`isolated` |
| `vm.count` | 開幾個 QEMU VM。受 host RAM 限制：`count * mem` 不能超過可用記憶體 |
| `vm.cpu` | 每個 VM 的 vCPU 數 |
| `vm.mem` | 每個 VM 的 RAM（MB） |
| `vm.qemu_args` | 傳給 QEMU 的額外參數。`-enable-kvm` 是關鍵，沒有它速度慢 10-100 倍 |

### 縮窄 fuzzing 範圍

如果只想 fuzz 特定 subsystem，用 `enable_syscalls`：

```json
{
    "enable_syscalls": [
        "openat",
        "read",
        "write",
        "ioctl$VFIO_*",
        "mmap"
    ]
}
```

或用 `disable_syscalls` 排除高雜訊 syscall（例如 `clock_gettime` 這類沒什麼 interesting path 的）：

```json
{
    "disable_syscalls": [
        "clock_gettime",
        "gettimeofday",
        "clock_getres"
    ]
}
```

---

## Step 4：跑 syz-manager

### 4-1 建立 workdir

```bash
mkdir -p /home/user/syzkaller-workdir
```

workdir 的結構在 fuzzing 開始後會自動建立：

```
syzkaller-workdir/
├── corpus/         ← 有效 program（找到新 coverage 的）
│   ├── <hash>      ← 一個 syzlang bytecode 檔
│   └── ...
├── crashes/        ← crash 資料
│   ├── <crash-id>/
│   │   ├── description
│   │   ├── log0
│   │   ├── report0
│   │   └── repro.prog
│   └── ...
├── bench             ← 效能統計 JSON
└── manager.log       ← syz-manager 自己的 log
```

### 4-2 啟動

```bash
./bin/syz-manager -config my.cfg
```

（需 KVM 環境。在有 KVM 的機器上驗證：啟動後 30 秒內應該看到 VM boot log，1-2 分鐘內開始看到 coverage 數字上升。）

### 正常 log 長這樣

```
2024/01/15 10:23:01 loading corpus...
2024/01/15 10:23:01 [1] loaded 0 programs (0 total)
2024/01/15 10:23:02 starting 4 VMs
2024/01/15 10:23:05 vm-0: booting...
2024/01/15 10:23:05 vm-1: booting...
2024/01/15 10:23:35 vm-0: running
2024/01/15 10:23:36 vm-1: running
2024/01/15 10:23:40 vm-0: cover: 1234, corpus: 5, triage: 0, signal: 1234
2024/01/15 10:23:45 vm-1: cover: 987, corpus: 3, triage: 0, signal: 987
```

欄位意義：
- `cover`：目前打到的 coverage edge 數量（數字越大越好）
- `corpus`：放進 corpus 的 program 數
- `triage`：正在 reproduce / 分類的 crash 數
- `signal`：feedback signal 總量

### 看到 crash 時

```
2024/01/15 11:45:23 vm-2: crash: KASAN: heap-out-of-bounds in some_driver_write+0x123/0x456
2024/01/15 11:45:23 vm-2: saving crash to workdir/crashes/0001-heap-oob-some_driver
```

crash 資料存在 `workdir/crashes/<hash>/`：

```
workdir/crashes/0001-heap-oob-some_driver/
├── description       ← crash 摘要一行
├── log0              ← 完整 kernel log（含 KASAN 報告）
├── report0           ← 解析後的 crash report
└── repro.prog        ← syzlang reproducer（如果 reproduce 成功）
```

---

## Step 5：Web Dashboard 解讀

瀏覽器開 `http://127.0.0.1:56741`。

### Crashes 頁面

最重要。每一行是一個 unique crash（以 crash title 去重）：

| 欄位 | 說明 |
|------|------|
| Title | crash 類型，例如 `KASAN: heap-out-of-bounds in foo_write` |
| Count | 同類 crash 出現次數 |
| Last Time | 最後出現時間 |
| Repro | 有沒有成功 reproduce（syz repro / C repro） |

點進去可以看完整 log，下載 `C reproducer`（一個獨立的 `.c` 檔，編譯後直接執行就能觸發 crash）。

### Coverage 頁面

顯示哪些 kernel 函式 / 原始碼行被打到，以及 coverage 百分比。如果某個 subsystem 的覆蓋率一直很低，代表需要更好的 syzlang description 或更多 corpus seed。

### Corpus 頁面

目前 corpus 裡的 program 數量與大小。corpus 太小代表 fuzzer 發現新路徑的速度在下降。

---

## 底層機制：syz-manager 怎麼管 VM

```
syz-manager
    │
    ├─ 啟動 QEMU（fork + exec qemu-system-x86_64 + 你的參數）
    │
    ├─ 等 VM boot（透過 serial console 偵測 login prompt）
    │
    ├─ SSH 進 VM，上傳 syz-executor binary
    │
    ├─ 透過 SSH stdin/stdout 傳送 program（syzlang bytecode）
    │
    ├─ syz-executor 執行 syscall，回傳 coverage（KCOV 讀出）
    │
    └─ coverage 回到 manager → 更新 corpus → 生成下一個 program
```

crash 偵測：manager 監控 VM 的 serial console output，看到 `BUG:` / `KASAN:` / `general protection fault` 等 pattern 就觸發 crash capture，然後把那個 VM reset 重新跑，避免一個 crash 卡住整個 session。

---

## 加入自訂 Description

Ch 25 寫的 `.txt` description 要讓 syz-manager 讀到，有兩種方式：

### 方式一：放在 sys/linux/ 下重新 build

把你的 `my_driver.txt` 放進 `syzkaller/sys/linux/`，然後：

```bash
make generate
make
```

`make generate` 會把所有 `.txt` 解析成 Go 程式碼（`sys/linux/my_driver.go`），再跟 syzkaller binary 一起 build 進去。這是官方推薦的方式。

### 方式二：限定 enable_syscalls 縮焦到你的 module

config.json 裡用 `enable_syscalls` 確保 fuzzer 專注打你描述的 syscall：

```json
{
    "enable_syscalls": [
        "ioctl$MY_DRIVER_CMD_A",
        "ioctl$MY_DRIVER_CMD_B",
        "open$my_driver"
    ]
}
```

這樣可以避免 fuzzer 把資源浪費在 `clock_gettime` 之類的無聊路徑上。

---

## 對比取捨

| 選項 | 優點 | 缺點 |
|------|------|------|
| `vm.count` 多（如 8） | 並行度高，找 bug 快 | RAM 消耗線性增加 |
| `procs` 多（如 16） | 單 VM 吞吐量高 | VM 裡 CPU 競爭，KASAN 有時 race |
| `KASAN_INLINE` | 比 KASAN_OUTLINE 快約 2x | binary 較大（bzImage 膨脹） |
| `DEBUG_KMEMLEAK` 開 | 能抓 leak | 嚴重拖慢，初期 fuzzing 先關 |
| `enable_syscalls` 縮窄 | 快速打深指定 subsystem | 錯過跨 subsystem 的 interaction bug |
| 完整 syscall 集 | 能找到 interaction bug | 初期 coverage 散亂，不容易深入 |

---

## 踩雷

**1. VM 一直 boot loop，SSH 連不進去**

最常見原因是 `bzImage` 和 `bookworm.img` 裡的 kernel module ABI 不配。image 裡的 `/lib/modules/` 是 apt 裝的版本，和你自己 build 的 kernel 版本不同。解法：build 完 kernel 後執行 `make modules_install INSTALL_MOD_PATH=<image_mount_point>`，或者直接用 `CONFIG_MODULES=n` 把所需模組全 built-in。

**2. syz-manager 啟動後 coverage 一直是 0**

先確認 `CONFIG_KCOV=y` 真的在 `.config` 裡（`grep CONFIG_KCOV /path/to/linux/.config`）。如果 config 正確，確認 QEMU 版本夠新（建議 4.x+），太舊的 QEMU 有時跑不起來帶 KASAN 的 kernel。另一個可能是 `-enable-kvm` 沒生效，用 `kvm-ok` 確認 host 支援 KVM。

**3. KASAN 報告裡 symbol 全是 `?` 或純 offset**

`kernel_obj` 路徑填錯，或者 build 時沒有 `CONFIG_DEBUG_INFO=y`。syz-manager 用 `addr2line` 解析 vmlinux，vmlinux 要有 debug info 且路徑正確。確認方法：`nm vmlinux | grep some_driver_write` 看得到 symbol 代表 debug info 有進去。

**4. `create-image.sh` 跑到一半失敗，提示 `/dev/loopX busy`**

loopback device 沒有乾淨 detach。執行 `losetup -a` 看哪個 loop device 還掛著，`losetup -d /dev/loopX` 手動卸載後重跑腳本。如果反覆遇到，考慮改用 `--with-virtio` 或改 `qemu-nbd` 方式 mount image。

**5. 開 KVM 後 QEMU 報 `KVM_CREATE_VM` permission denied**

用戶不在 `kvm` group：`sudo usermod -aG kvm $USER`，重新 login 後生效。用 `groups` 確認有 `kvm` 字樣。

**6. syz-manager 一直 reproduce crash 但 repro.prog 一直是空的**

Reproduce 有 timeout。如果 crash 是 race condition，reproduce 成功率很低，manager 可能嘗試幾十次都失敗。這種 crash 通常要靠 log0 手動分析，不要等 C repro。

---

## 進階延伸

- **Google OSS-Fuzz / syzbot**：Google 用 syzkaller 持續跑 Linux mainline，結果公開在 `syzkaller.appspot.com`，可以看別人找到的 crash 學習如何讀報告，也是理解「一份有效 crash report 長什麼樣」的最好素材庫。
- **QEMU snapshot 模式**：`-snapshot` flag 讓 QEMU 不回寫 image，每次 crash 重開 VM 速度更快（不用等 fsck）。對 image 壽命也有好處。
- **多架構 fuzzing**：syzkaller 支援 ARM64、RISCV、MIPS。`target: "linux/arm64"` 配合 `qemu_args` 換成 `qemu-system-aarch64` 即可，適合 fuzz 嵌入式 BSP driver。
- **Corpus 共享**：syz-manager 的 `workdir/corpus/` 可以在多台機器間 rsync，加速 corpus 成長。跑兩台機器共享 corpus 比單台開兩倍 VM 更有效（corpus 多樣性更高）。
- **syz-ci**：syzkaller 附帶 CI 工具，可以自動追蹤 kernel commit 並跑 bisect，找出哪個 commit 引入了 bug。

---

## 動手練習

以下練習假設你有一台 Linux 主機（或 GCP/Azure VM 開了巢狀虛擬化）。

1. **Kernel config 驗證**（可在無 KVM 環境做）：
   clone kernel，套入本章的 config 清單，`make olddefconfig` 後確認 `grep -E "CONFIG_KCOV|CONFIG_KASAN|CONFIG_UBSAN" .config` 全部是 `=y`。確認 `make -j$(nproc) bzImage` 能 build 過。

2. **觀察 workdir 結構**（需 KVM 環境）：
   完整跑起 syzkaller 後，等 30 分鐘再看 `workdir/` 的目錄結構。`ls workdir/corpus/ | wc -l` 應該有幾十到幾百個檔案，`ls workdir/crashes/` 看有沒有 crash 進來。

3. **讀懂一份 KASAN 報告**：
   從 `syzkaller.appspot.com` 找一個 `KASAN: heap-out-of-bounds` 的 crash，看懂 stack trace 的每一層，找出哪個函式越界、越界了幾個 byte、access type 是 read 還是 write。

4. **寫最小可用 config.json**：
   不用真的跑起來，只練習把路徑填對：把 Step 1/2 的產出路徑填進 config 範本，`enable_syscalls` 只填 `read` 和 `write`，然後用 `python3 -m json.tool my.cfg` 驗證 JSON 語法正確。

---

## 本章重點

- syzkaller 的執行流程：build kernel（含 KCOV/KASAN）→ build guest image → 填 config.json → syz-manager 啟動 QEMU VM → crash 自動收集。
- Kernel config 裡最關鍵的三件事：`CONFIG_KCOV`（coverage feedback）、`CONFIG_KASAN`（bug 偵測）、namespace 系列（executor 沙盒）。
- `create-image.sh` 用 debootstrap 建 Debian image，同時生成 SSH keypair，這個 key 是 syz-manager 和 VM 通訊的唯一管道。
- `config.json` 的 `kernel_obj` 指向 vmlinux，不填對 KASAN 報告的 symbol 解析會全部失敗。
- Web dashboard 的 Crashes 頁面是最重要的，C reproducer 能直接拿來送 bug report。
- 沒有 KVM 就沒有實用的 fuzzing 速度，WSL2 預設無法使用，需要實體機或開了巢狀虛擬化的雲端 VM。

---

## 自我檢核

- [ ] 我能說出 `CONFIG_KCOV` 和 `CONFIG_KASAN` 各自的功能差異
- [ ] 我知道 `create-image.sh` 為什麼要生成 SSH keypair，而不是用密碼
- [ ] 我能解釋 `config.json` 裡 `kernel_obj` 和 `image` 分別指向什麼
- [ ] 我看到 syz-manager 的 `cover: 0` 時知道先檢查哪三個地方
- [ ] 我能說出 `vm.count` 和 `procs` 的差異（VM 數量 vs 每 VM 的 executor 數）
- [ ] 我知道如何把自訂 description 加進 syzkaller build

---

## 延伸閱讀

- [syzkaller Getting Started 官方文件](https://github.com/google/syzkaller/blob/master/docs/linux/setup.md) — 官方最新步驟，本章的理論基礎。
- [syzkaller.appspot.com（syzbot）](https://syzkaller.appspot.com) — Google 持續跑的 fuzzing 結果，數千個 crash report 的真實範本，是讀報告最好的練習素材。
- [KCOV: code coverage for fuzzing](https://www.kernel.org/doc/html/latest/dev-tools/kcov.html) — 官方 kernel 文件，解釋 KCOV 的 API 與 syzlang 怎麼讀它。
- [KASAN: Kernel Address Sanitizer](https://www.kernel.org/doc/html/latest/dev-tools/kasan.html) — inline vs outline 模式詳細說明，理解為什麼 fuzzing 偏好 inline。

---

→ [下一章](./27-kernel-fuzzing-advanced.md)
