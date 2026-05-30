# Ch 0 — 環境搭建

> **目標**：在 Ubuntu 22.04 上建立完整的 eBPF 開發環境，涵蓋 clang/libbpf/bpftool/bpftrace/BCC，驗證 BTF 與 CO-RE 支援，並成功編譯、載入第一個 BPF 程式到 kernel 中執行。

> **環境**：Ubuntu 22.04.x LTS，kernel 6.2+（透過 `linux-image-generic-hwe-22.04` 取得）。Ubuntu 20.04 搭配 HWE kernel 5.15 亦可，但 ringbuf 和部分 CO-RE relocation 功能不完整；本課建議 22.04。

## 為什麼 eBPF 環境比普通 C 程式麻煩？

一般 C 開發只需要 `gcc` 一個工具。eBPF 開發有三個麻煩的地方：

**兩個 world，兩套編譯**：你的 eBPF 程式分成 kernel-side（BPF bytecode）和 userspace-side（一般 ELF）。前者用 `clang -target bpf` 編，後者用正常的 clang/gcc 編。兩邊共享一部分標頭檔，但編譯選項完全不同；搞混了就是神秘的 undefined symbol 錯誤。

**kernel 版本敏感**：BTF（5.2+）、CO-RE（5.2+ 搭配 clang 10+）、ringbuf（5.8+）、fentry/fexit（5.5+）、BPF-LSM（5.7+）各有最低版本需求。版本不夠不會給你清楚的錯誤，只會說 "Operation not permitted" 或 "Invalid argument"。

**工具鏈碎片化**：bpftrace、BCC、libbpf 是三個獨立工具，各有自己的版本節奏。Ubuntu 22.04 的官方套件夠用；Ubuntu 20.04 的 BCC 套件太舊，某些功能不支援。

這章把所有坑整理成一個流程，你照著做就能搭好。

## 先建立直覺：工具箱全圖

```
你寫的 hello.bpf.c  (kernel-side C code)
        │
        ▼  clang -g -O2 -target bpf -c
     hello.bpf.o  (BPF bytecode ELF + BTF debug info)
        │
        │  libbpf: open() → load() → attach()
        ▼
     bpf() syscall
        │
        ├──▶ kernel verifier  (安全性檢查)
        │         │
        │         ├── reject → 給你詳細 log
        │         └── accept → JIT 編成 native code → 執行
        │
        └── maps  (BPF programs 和 userspace 共享的資料結構)

更高層的工具（建在 libbpf 之上）：
  ├── bpftool    ← 查看 / 載入 / dump loaded programs 和 maps
  ├── bpftrace   ← 高階腳本語言，適合快速 one-liner 觀測
  └── BCC        ← Python front-end + kernel C，老牌框架
```

libbpf 是正統開發路線；bpftrace 和 BCC 是建在它之上的更高層工具。這門課三個都教，但 libbpf 是核心。

## Step 1：確認 kernel 版本與 BTF 支援

```bash
# 確認 kernel 版本（需要 5.15+，建議 6.x）
uname -r

# 確認 BTF（BPF Type Format）已啟用——CO-RE 的基礎
ls -la /sys/kernel/btf/vmlinux
# 應該看到一個有內容的檔案，約 3–6 MB

# 確認 kernel config 的 BPF 選項
grep -E 'CONFIG_BPF|CONFIG_DEBUG_INFO_BTF' /boot/config-$(uname -r)
# 你需要看到：
# CONFIG_BPF=y
# CONFIG_BPF_SYSCALL=y
# CONFIG_DEBUG_INFO_BTF=y
```

如果 `/sys/kernel/btf/vmlinux` 不存在，你的 kernel 沒開 `CONFIG_DEBUG_INFO_BTF`。Ubuntu 的官方 kernel 有開；自編 kernel 要手動加這個選項重新編譯。

如果 `uname -r` 顯示 5.4.x（Ubuntu 20.04 預設），先安裝 HWE kernel：

```bash
sudo apt install linux-image-generic-hwe-20.04
sudo reboot
# reboot 後確認版本
uname -r  # 應該是 5.15.x 或更高
```

## Step 2：安裝 Build Toolchain

eBPF 程式用 clang 編譯，需要 clang 12+ 才有完整的 BPF backend 和 BTF 支援。

```bash
sudo apt update
sudo apt install -y \
    clang \
    llvm \
    llvm-dev \
    libelf-dev \
    zlib1g-dev \
    pkg-config \
    make \
    gcc \
    git

# 確認版本
clang --version
# 需要 clang 12.0+；Ubuntu 22.04 預設給 clang 14，OK

llc --version | grep -i bpf
# 應該看到 "bpf    - BPF (host endian)" 和 "bpfeb" / "bpfel"
# 表示 LLVM 包含 BPF backend
```

如果 `llc --version` 看不到 BPF，你的 LLVM 安裝不完整。Ubuntu 22.04 上 `apt install llvm` 會正確安裝；如果有問題，試 `apt install llvm-14`。

## Step 3：安裝 libbpf

libbpf 是 eBPF 開發的核心 C library，提供 `bpf()` syscall wrapper、BTF relocation engine、BPF object lifecycle 管理。

```bash
# Ubuntu 22.04 官方套件（libbpf 0.8–1.x，夠用）
sudo apt install -y libbpf-dev

# 確認安裝
pkg-config --modversion libbpf
# 應該顯示版本號，例如 0.8.0 或 1.1.0

ls /usr/include/bpf/
# 應該看到：bpf.h, libbpf.h, bpf_helpers.h, bpf_tracing.h,
#           bpf_endian.h, bpf_core_read.h 等
```

如果你需要最新版本的 libbpf（例如要用 BPF arena 或最新的 map type），從 source 編：

```bash
git clone https://github.com/libbpf/libbpf.git
cd libbpf/src
make
sudo make install
sudo ldconfig
# 確認
pkg-config --modversion libbpf
```

## Step 4：安裝 bpftool

bpftool 是你和 kernel BPF subsystem 互動的主要介面：查看 loaded programs、dump bytecode、生成 skeleton、查看 maps。

```bash
# 從 apt 安裝
sudo apt install -y linux-tools-$(uname -r) linux-tools-generic

# 確認
sudo bpftool version
# 例如：v7.3.0

sudo bpftool prog list
# 列出目前 kernel 裡所有已載入的 BPF programs
# 剛裝好的系統可能已有 systemd 或 dockerd 放的 programs
```

如果 apt 安裝的 bpftool 版本太舊（比你的 kernel 版本舊），從 kernel source 編：

```bash
# bpftool 在 kernel source tree 裡
git clone --depth 1 https://github.com/torvalds/linux.git /tmp/linux
cd /tmp/linux/tools/bpf/bpftool
sudo apt install -y libcap-dev binutils-dev
make
sudo cp bpftool /usr/local/bin/
sudo bpftool version
```

## Step 5：安裝 bpftrace

bpftrace 是 eBPF 的高階腳本語言，語法類似 DTrace，適合快速的 one-liner 觀測。

```bash
sudo apt install -y bpftrace

# 驗證（需要 root 或 CAP_BPF）
sudo bpftrace -e 'BEGIN { printf("bpftrace works\n"); exit(); }'
# 輸出：
# Attaching 1 probe...
# bpftrace works
```

> bpftrace 幾乎都需要 root 或 `CAP_BPF + CAP_PERFMON`。本課的 bpftrace 範例都以 `sudo` 執行。

## Step 6：安裝 BCC

BCC（BPF Compiler Collection）提供 Python front-end，讓你在 runtime 動態編譯 kernel-side C 程式。

```bash
sudo apt install -y bpfcc-tools python3-bpfcc libbcc-dev

# 驗證
sudo python3 -c "from bcc import BPF; print('BCC OK')"

# 試跑 BCC 自帶的工具
sudo /usr/sbin/execsnoop-bpfcc
# 在另一個 terminal 跑 ls，這邊應該能看到 execve 事件
# Ctrl+C 停止
```

> Ubuntu 22.04 的 bpfcc 版本是 BCC 0.24，夠用。如果需要最新功能，從 https://github.com/iovisor/bcc source 編（過程較複雜，本課用 apt 版本即可）。

## Step 7：生成 vmlinux.h

vmlinux.h 包含整個 kernel 的型別定義（`struct task_struct`、`struct sk_buff` 等），讓你在 BPF 程式裡直接用這些型別，不需要引入 kernel 標頭檔。這是 CO-RE 開發的基礎。

```bash
# 從目前執行的 kernel 的 BTF 資訊生成
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h

# 確認內容
wc -l vmlinux.h
# 通常有 30,000–50,000 行

grep "struct task_struct {" vmlinux.h
# 應該看到 task_struct 的定義

# 放到 /usr/include/ 讓所有 project 共用（選擇性）
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c \
    > /usr/include/vmlinux.h
```

每次升級 kernel 之後，vmlinux.h 要重新生成。CO-RE 機制允許用舊 kernel 生成的 vmlinux.h 在新 kernel 上執行，但通常還是用當前 kernel 生成比較保險。

## 第一個 BPF 程式：Hello World

建立工作目錄：

```bash
mkdir -p ~/ebpf-hello && cd ~/ebpf-hello
```

**hello.bpf.c**（kernel-side，編成 BPF bytecode）：

```c
/* hello.bpf.c */
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

/*
 * SEC("tracepoint/syscalls/sys_enter_execve") 宣告：
 * 當任何 process 呼叫 execve() syscall 時，執行這個函式。
 * "tracepoint" 是 program type，
 * "syscalls/sys_enter_execve" 是 attach 目標。
 */
SEC("tracepoint/syscalls/sys_enter_execve")
int hello(struct trace_event_raw_sys_enter *ctx)
{
    /*
     * bpf_printk() 輸出到 /sys/kernel/debug/tracing/trace_pipe。
     * bpf_get_current_pid_tgid() 回傳 (tgid << 32 | pid)，
     * 右移 32 位得到 process 的 PID（tgid）。
     */
    bpf_printk("hello from eBPF: pid=%d\n",
               bpf_get_current_pid_tgid() >> 32);
    return 0;
}

/*
 * BPF 程式必須宣告 license。
 * 很多 helper function 只在 GPL 相容的 license 下才能用。
 */
char LICENSE[] SEC("license") = "GPL";
```

**Makefile**：

```makefile
# Makefile
CLANG   := clang
# -g          包含 BTF debug info（CO-RE 需要）
# -O2         BPF verifier 需要 optimized code（unoptimized 的某些 pattern 會被 reject）
# -target bpf 編成 BPF bytecode 而非 native binary
# -D__TARGET_ARCH_x86_64  告訴 bpf_tracing.h 用 x86_64 的 register layout
CFLAGS  := -g -O2 -target bpf -D__TARGET_ARCH_x86_64

# ARM64 機器改成：-D__TARGET_ARCH_arm64

.PHONY: all clean

all: hello.bpf.o

hello.bpf.o: hello.bpf.c
	$(CLANG) $(CFLAGS) -c $< -o $@

clean:
	rm -f *.o
```

編譯並載入：

```bash
# 編譯
make
# 應該生成 hello.bpf.o，沒有錯誤

# 查看 BPF bytecode（確認編譯成功）
llvm-objdump -d hello.bpf.o
# 應該看到 BPF 指令，例如：
# 0000000000000000 <hello>:
#        0:  85 00 00 00 0e 00 00 00  call 14  # bpf_get_current_pid_tgid
#        1:  77 00 00 00 20 00 00 00  r0 >>= 32
#        ...

# 用 bpftool 載入並 pin 到 BPF filesystem
sudo bpftool prog load hello.bpf.o /sys/fs/bpf/hello

# 附加到 tracepoint
sudo bpftool prog attach \
    pinned /sys/fs/bpf/hello \
    tracepoint syscalls sys_enter_execve

# 觸發 execve（在另一個 terminal 執行 ls）
# 然後讀 trace_pipe
sudo cat /sys/kernel/debug/tracing/trace_pipe &
ls /tmp
# 應該看到類似：
# <...>-12345 [000] .... 1234.567890: bpf_trace_printk: hello from eBPF: pid=12345

# 清理
sudo bpftool prog detach \
    pinned /sys/fs/bpf/hello \
    tracepoint syscalls sys_enter_execve
sudo rm /sys/fs/bpf/hello
```

如果一切正常，你剛剛讓一段你寫的 C code 在 Linux kernel 裡面執行了——這就是 eBPF 的核心體驗。

## 踩雷集錦

1. **`/sys/kernel/btf/vmlinux` 不存在**：kernel 沒開 `CONFIG_DEBUG_INFO_BTF`。Ubuntu 官方 kernel 有開；自編 kernel 要手動加。確認方式：`grep CONFIG_DEBUG_INFO_BTF /boot/config-$(uname -r)`

2. **`bpf_helpers.h` 找不到**：沒裝 `libbpf-dev`，或標頭檔路徑不對。確認：`ls /usr/include/bpf/bpf_helpers.h`；找不到就 `sudo apt install libbpf-dev`

3. **`clang: error: unknown target triple 'bpf'`**：LLVM 沒有包含 BPF backend。`apt install llvm` 通常包含；不行試 `apt install llvm-14`

4. **`bpftool prog load` 失敗說 Permission denied**：需要 root 或 `CAP_BPF + CAP_PERFMON`。改用 `sudo`

5. **`invalid argument` 或 `operation not permitted`**：90% 是 kernel 版本不夠或 kernel config 沒開。先用 `sudo bpftool feature probe` 查你的 kernel 支援什麼

6. **`llvm-objdump -d hello.bpf.o` 顯示空白**：`.o` 沒有 BPF section。確認 Makefile 有 `-target bpf`，且 source 有 `SEC(...)` 宣告

7. **`-O2` 去掉之後 verifier 不接受**：BPF verifier 做 dead-code elimination 等分析；沒有優化的 code 有時會讓 verifier 看不出 pointer 的邊界。永遠加 `-O2`

8. **`cat /sys/kernel/debug/tracing/trace_pipe` 沒有輸出**：確認 attach 成功（`sudo bpftool prog list` 看有沒有你的 program），以及有觸發 execve（`bpftool prog` 輸出的 `run_cnt` 有沒有增加）

## 完整環境驗證腳本

把這段存成 `check-env.sh` 跑一遍：

```bash
#!/bin/bash
# check-env.sh — eBPF 開發環境驗證
set -euo pipefail

ok()   { echo "  [OK]  $*"; }
warn() { echo "  [!!]  $*"; }
fail() { echo "  [XX]  $*"; }

echo "=== kernel 版本 ==="
KVER=$(uname -r)
MAJOR=$(echo "$KVER" | cut -d. -f1)
MINOR=$(echo "$KVER" | cut -d. -f2)
echo "  kernel: $KVER"
if [ "$MAJOR" -gt 6 ] || { [ "$MAJOR" -eq 6 ] && [ "$MINOR" -ge 0 ]; }; then
    ok "kernel >= 6.0（全功能）"
elif [ "$MAJOR" -ge 5 ] && [ "$MINOR" -ge 15 ]; then
    ok "kernel >= 5.15（大部分功能可用）"
else
    warn "kernel $KVER 低於建議的 5.15，部分章節功能不可用"
fi

echo ""
echo "=== BTF 支援 ==="
if [ -f /sys/kernel/btf/vmlinux ]; then
    SIZE=$(du -sh /sys/kernel/btf/vmlinux 2>/dev/null | cut -f1)
    ok "/sys/kernel/btf/vmlinux 存在（$SIZE）— CO-RE 可用"
else
    fail "/sys/kernel/btf/vmlinux 不存在！CO-RE 無法使用"
fi

echo ""
echo "=== 工具版本 ==="
clang --version | head -1 | while read l; do ok "clang: $l"; done
if pkg-config --modversion libbpf 2>/dev/null; then
    ok "libbpf: $(pkg-config --modversion libbpf)"
else
    fail "libbpf: 未找到（需要 apt install libbpf-dev）"
fi
bpftool version 2>/dev/null | head -1 | while read l; do ok "bpftool: $l"; done \
    || fail "bpftool: 未找到"
bpftrace --version 2>/dev/null | while read l; do ok "bpftrace: $l"; done \
    || warn "bpftrace: 未安裝（Part 3 需要）"
sudo python3 -c "from bcc import BPF; print('BCC: OK')" 2>/dev/null \
    | while read l; do ok "$l"; done \
    || warn "BCC: 未安裝（Part 3 需要）"

echo ""
echo "=== bpftool feature probe（節錄）==="
sudo bpftool feature probe 2>/dev/null \
    | grep -E "^(map_type|prog_type|helper)" \
    | grep "is available" \
    | head -20 \
    || warn "bpftool feature probe 失敗（需要 sudo）"

echo ""
echo "=== 完成 ==="
```

```bash
chmod +x check-env.sh
sudo ./check-env.sh
```

## 動手練習

1. 跑完 `check-env.sh`，記下你的 kernel 版本和缺少的工具

2. 修改 `hello.bpf.c`，把 tracepoint 換成追蹤 `sys_enter_openat`（把 `SEC(...)` 裡的名稱換掉，其他不動），重新 `make` 並載入，確認能捕捉到 `ls` 觸發的 openat 事件

3. 執行 `sudo bpftool feature probe` 找出你的 kernel **不支援**的 program type 或 map type，思考一下為什麼不支援（版本不夠？config 沒開？）

4. 執行 `sudo bpftrace -e 'tracepoint:syscalls:sys_enter_execve { printf("%s\n", comm); }'`，在另一個 terminal 跑 `ls`，確認 bpftrace 能捕捉到

## 本章重點整理

- eBPF 有 kernel-side（BPF bytecode）和 userspace-side 兩個部分，用不同方式編譯
- `/sys/kernel/btf/vmlinux` 的存在代表 kernel 支援 BTF，這是 CO-RE 的前提
- libbpf 是現代 eBPF 開發的核心 library；bpftrace 和 BCC 是建在它之上的高層工具
- bpftool 是你和 kernel BPF subsystem 互動的主要介面
- vmlinux.h 從 kernel BTF 生成，包含所有 kernel 型別定義，讓你不需要引入 kernel 標頭檔

## 自我檢核

- [ ] 能解釋為什麼 eBPF kernel-side 要用 `clang -target bpf` 而不是 `gcc`
- [ ] 知道 BTF 是什麼（不只是「一個檔案」）以及它和 CO-RE 的關係
- [ ] 能解釋 `SEC("tracepoint/syscalls/sys_enter_execve")` 這個 annotation 做了什麼
- [ ] 能用 bpftool 列出目前 kernel 裡所有 loaded BPF programs，並說出每個欄位的意義
- [ ] 知道 `-O2` 在 BPF 編譯裡為什麼不能省略

## 延伸閱讀

### 官方文件

- **[libbpf README](https://github.com/libbpf/libbpf#readme)**
  - **讀哪裡**：Building 一節和 API docs 連結
  - **學什麼**：libbpf 官方建議的安裝方式，以及 API 版本相容性說明
  - **前提**：無

- **[bpftool man page](https://www.mankier.com/8/bpftool)**
  - **讀哪裡**：整頁，特別是 `prog` 和 `map` 的 subcommand
  - **學什麼**：bpftool 的完整功能；這章只用了冰山一角，後面章節會大量用到
  - **前提**：無

### 部落格

- **[Getting Started with eBPF](https://ebpf.io/get-started/)** — eBPF.io
  - **這篇說什麼**：官方生態系的安裝指引，比本章更簡短但連結更多工具
  - **讀哪裡**：整頁，點進各個工具的連結看看有什麼
  - **為什麼值得讀**：了解 eBPF 生態系的廣度，很多工具本課不涵蓋但你應該知道它們存在

- **[BCC Installation](https://github.com/iovisor/bcc/blob/master/INSTALL.md)** — iovisor/bcc
  - **這篇說什麼**：各 Linux distro 的 BCC 安裝方式
  - **讀哪裡**：Ubuntu 那一節
  - **為什麼值得讀**：apt 版本不夠新時，這裡有從 source 編的完整步驟

→ [Ch 1 為什麼是 eBPF？](./01-why-ebpf.md)
