# Ch 0 — 環境搭建

> 目標：在你的 Linux 機器上裝齊 BPF 開發、執行、調試所需的工具，並用一行 bpftrace 與一個 bpftool 指令驗證整套環境真的會動。

## Kernel 版本：BPF 最重要的單一變數

BPF 不是「裝個 package 就有」的東西 — **它的能力綁在你的 kernel 版本**。同一支 BPF 程式在 5.4 跑不起來、在 5.15 完全沒問題，是常態。

| Kernel 版本 | 你會得到什麼 | 評語 |
|---|---|---|
| < 4.x | 只有 classic BPF (cBPF) | 史前時代，不是這門課的範圍 |
| 4.x | 早期 eBPF、基本 maps、kprobe | 能跑但很多現代 feature 沒有 |
| 5.4 | BTF 開始穩定 | CO-RE 的最低門檻，但體驗差 |
| 5.8 | CO-RE、BPF LSM 進來 | 算現代 BPF 的起點 |
| **5.15** | **ringbuf、fentry、BPF LSM 都成熟** | **本教材的基準版本** |
| 6.x+ | kfunc、HID-BPF、struct_ops 強化 | 能用就用，未來方向 |

查你目前的 kernel：

```bash
uname -r
```

低於 5.15 的話，後面有些章節的範例會跑不動。**不要硬撐** — 升級 distro 或裝新 kernel 比繞 workaround 省 100 倍時間。Ubuntu 22.04 LTS 預設 5.15、Ubuntu 24.04 LTS 是 6.8，都直接可用。

## 工具關係釐清

BPF 生態的工具名很容易混。先把角色釐清：

| 工具 | 角色 | 你會在哪用 |
|---|---|---|
| **clang / llvm** | 把 C 編譯成 BPF bytecode | 寫 kernel side BPF 必備 |
| **libbpf** | C 函式庫，user space loader | Ch 13–14 主角 |
| **bpftool** | 官方 CLI，查/載/dump BPF 物件 | 天天用，等同 BPF 的 `strace` |
| **bpftrace** | 高階腳本語言（像 awk） | one-liner 排查 |
| **bcc** | Python 包 C 的舊框架 | 一堆既有工具還在用 |
| **vmlinux.h** | kernel 所有型別的標頭檔 | 用 BTF 自動生成，CO-RE 必備 |

**主流寫 BPF 的方式有三種**：bpftrace（最高階，one-liner）、bcc（Python + C 拼接）、libbpf + CO-RE（最現代、生產級）。Ch 11–15 會三種都教。Ch 0 先把工具裝齊。

## 必備 Kernel Config

新版 distro 預設都開了，但偶爾有缺。檢查方式：

```bash
# 看 kernel config 有沒有開 BPF 相關 flag
zcat /proc/config.gz 2>/dev/null || cat /boot/config-$(uname -r) | grep -E "BPF|BTF"
```

關鍵的幾個必須是 `=y` 或 `=m`：

```
CONFIG_BPF=y
CONFIG_BPF_SYSCALL=y
CONFIG_BPF_JIT=y
CONFIG_HAVE_EBPF_JIT=y
CONFIG_DEBUG_INFO_BTF=y      ← CO-RE 的關鍵，沒有的話 vmlinux.h 生不出來
CONFIG_BPF_LSM=y             ← Ch 23 會用
```

`CONFIG_DEBUG_INFO_BTF` 沒開的話會看到「沒有 `/sys/kernel/btf/vmlinux`」的錯。Ubuntu 22.04+ / Fedora 36+ / Arch 預設都有開。自編 kernel 才需要操心。

## 安裝

### Ubuntu 22.04 / 24.04（最推薦）

```bash
sudo apt update
sudo apt install -y \
    clang llvm                             \
    libelf-dev libbpf-dev                  \
    linux-headers-$(uname -r)              \
    linux-tools-$(uname -r) linux-tools-common  \
    bpftrace bpfcc-tools                   \
    build-essential pkg-config
```

`linux-tools-$(uname -r)` 裡有 `bpftool`。`bpfcc-tools` 是 bcc 的工具集（execsnoop、opensnoop 等就在這）。

### Fedora 36+

```bash
sudo dnf install -y \
    clang llvm \
    elfutils-libelf-devel libbpf-devel \
    kernel-devel kernel-headers \
    bpftool bpftrace bcc-tools \
    make
```

### Arch / Manjaro

```bash
sudo pacman -S clang llvm libelf libbpf bpf bcc bpftrace
```

### WSL2 注意

WSL2 預設 kernel **是 Microsoft 客製版**，BPF 支援不完整。要嘛升級到 WSL2 的最新 kernel（≥ 5.15.x microsoft）並啟用 BPF feature、要嘛自編 kernel。**本教材建議用原生 Linux 或 VM**，後面有些網路相關章節（XDP）在 WSL2 會卡。

## 驗證安裝

跑這四個，全部要有輸出：

```bash
clang --version              # 要 >= 12
bpftool version              # 要 >= 5.15
bpftrace --version           # 要 >= 0.14
ls /sys/kernel/btf/vmlinux   # BTF 必須存在
```

最後一行是關鍵 — 沒有 `vmlinux` 這個檔，CO-RE 整個免談。

## 第一個範例：用 bpftrace 觀察檔案開啟

不寫程式、不編譯，一行 BPF 程式追蹤系統上**所有**的 `openat` syscall：

```bash
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_openat { printf("%s opened %s\n", comm, str(args->filename)); }'
```

跑起來之後，開另一個 terminal 隨便做點事 — `cat /etc/hostname`、`ls`、開個 vim — 你會看到第一個 terminal 飛快滾出：

```
bash opened /etc/passwd
cat opened /etc/hostname
ls opened /usr/lib/locale/locale-archive
vim opened /etc/vim/vimrc
...
```

這一行做了什麼？

```
bpftrace -e '...'
   │
   ▼
解析腳本 → 編譯成 BPF bytecode → 透過 BPF syscall 載入 kernel
   │
   ▼
attach 到 sys_enter_openat tracepoint
   │
   ▼
每次有人呼叫 openat() → kernel 跑你的 BPF code
   │                        │
   │                        ▼
   │                   把 comm + filename 透過 ring buffer 送回 user space
   ▼
bpftrace 印出來
```

整個過程 **沒有改一行 kernel code、沒有重開機、沒有裝 module**。這就是 BPF 的魔力。

按 Ctrl+C 結束，bpftrace 會自動 detach。

## 第二個範例：用 bpftool 看你跑了哪些 BPF

```bash
sudo bpftool prog list
```

如果你的系統正在跑 systemd / Docker / Cilium，你會看到一堆程式：

```
3: cgroup_skb  name sd_devices  tag 6deef7357e7b4530
        loaded_at 2026-04-23T08:15:42+0800  uid 0
        xlated 64B  jited 54B  memlock 4096B
4: cgroup_skb  name sd_devices  tag 6deef7357e7b4530
...
```

每一條都是一個正在 kernel 裡跑的 BPF 程式。`bpftool` 是你之後天天用的工具 — 想知道某個 BPF 程式在哪、attach 到什麼、map 內容是啥，全靠它。

```bash
sudo bpftool map list           # 列出所有 map
sudo bpftool btf list           # 列出所有 BTF 物件
```

## 生成 vmlinux.h（後面章節會用）

CO-RE 寫 BPF 需要 `vmlinux.h` — 一個包含 kernel 所有型別定義的巨大標頭檔，從 BTF 自動生成：

```bash
mkdir -p ~/bpf-workspace
cd ~/bpf-workspace
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h
wc -l vmlinux.h
# 大概 10 萬行起跳
```

這個檔本機生成、本機用 — **不要把別人機器的 vmlinux.h 搬過來用**，type 偏移會對不上。Ch 13 會詳細解釋 CO-RE 怎麼處理跨機器相容。

## 常見坑

1. **`Permission denied` 跑 bpftrace**：BPF 大多需要 root 或 `CAP_BPF` + `CAP_PERFMON`。教材都假設你 `sudo`。
2. **`/sys/kernel/btf/vmlinux: No such file`**：你的 kernel 沒開 `CONFIG_DEBUG_INFO_BTF`。換 distro 或重編 kernel。
3. **bpftrace 找不到 tracepoint**：用 `sudo bpftrace -l 'tracepoint:syscalls:*'` 列出所有可用的 — 不同 kernel 版本有些會增刪。
4. **`bpftool: command not found`**：在 Ubuntu 上常常是因為 `linux-tools-$(uname -r)` 沒裝對版本（剛升級 kernel 後最常見）。重裝一次。
5. **WSL2 `BPF: ...` 一堆錯**：見上面 WSL2 段，建議換環境。

## 動手練習

1. 跑通上面那個 `openat` one-liner，餵它「開另一個 terminal 隨便操作」的負載。
2. 把 one-liner 改成只印 `comm == "cat"` 的事件 — hint：用 bpftrace 的 `/filter/` 語法。
3. `sudo bpftool prog show id <某個 id>` — 隨便挑個 id，看一下輸出，認識 `xlated` / `jited` 兩個欄位的差別。
4. 自己生成 `vmlinux.h`，用 `grep "struct task_struct {" vmlinux.h` 找到 task struct 的定義，瞄一眼有幾個欄位。

## 自我檢核

- [ ] 我能說出自己的 kernel 版本與 BPF 能用到哪一代 feature
- [ ] 我能跑通 bpftrace one-liner 並理解它是怎麼跑進 kernel 的
- [ ] 我能用 `bpftool prog list` 看到系統上的 BPF 程式
- [ ] 我能生成 `vmlinux.h` 並知道為什麼不能跨機器搬

下一章我們先把鏡頭拉遠 — 講 BPF 的 30 年歷史與全貌，看清楚它從哪來、現在站在哪、為什麼會變成 cloud-native infra 的新底層。

→ [Ch 1 BPF 是什麼？從 packet filter 到 universal kernel runtime](./01-bpf-overview.md)
