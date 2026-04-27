# Ch 0 — 環境搭建

> 目標：把整套課的工具一次裝齊、知道每個是幹嘛、跑一輪 sanity check。

## 工具大集合

我們會用到這些，分四群：

| 群 | 工具 | 看什麼 |
|---|---|---|
| 動態追蹤 | `strace` `ltrace` `perf` `ftrace` `bpftrace` | 跑著的 process 在做什麼 |
| File / Network 觀察 | `lsof` `ss` `tcpdump` `iotop` | 跨 process 的 fd / 網路 / IO |
| Memory / correctness | `valgrind` ASan / TSan / UBSan / MSan | 記憶體錯誤、race、UB |
| 靜態 / 編譯 / debug | `gcc` `g++` `gdb` `nm` `readelf` `objdump` `addr2line` | binary 內部、source 對應 |

## 一次裝完

**Ubuntu / Debian**：

```bash
sudo apt update
sudo apt install -y \
  build-essential gdb \
  strace ltrace \
  lsof \
  iproute2 net-tools tcpdump \
  sysstat htop iotop \
  linux-tools-common linux-tools-generic linux-tools-$(uname -r) \
  trace-cmd kernelshark \
  bpftrace bpfcc-tools \
  valgrind \
  binutils elfutils \
  patchelf
```

**Arch**：

```bash
sudo pacman -S base-devel gdb strace ltrace lsof iproute2 net-tools \
  tcpdump sysstat htop iotop perf trace-cmd bpftrace \
  valgrind binutils patchelf
```

**Fedora**：

```bash
sudo dnf install -y gcc gcc-c++ gdb strace ltrace lsof iproute net-tools \
  tcpdump sysstat htop iotop perf trace-cmd bpftrace \
  valgrind binutils patchelf
```

## 一個踩雷：perf 裝錯版本

`perf` 在 Linux source tree 裡面，**每個 kernel 版本對應自己的 perf**。Ubuntu 把它包在 `linux-tools-<version>`，`linux-tools-generic` 應該對應目前 kernel。

跑：

```bash
perf --version
```

看到 `WARNING: perf not found for kernel X.Y.Z` 就是版本對不上。`uname -r` 看你跑的 kernel 版本，再 `apt install linux-tools-<那個版本>` 補上。

## 權限：很多工具要 root 或 capability

| 工具 | 權限 |
|---|---|
| `strace` 看自己的 process | 不需要 root |
| `strace -p PID` attach 別人 | 預設受 `ptrace_scope` 限制（見下） |
| `tcpdump` | 需要 `CAP_NET_RAW`，慣例直接 sudo |
| `perf record` | 看 `kernel.perf_event_paranoid` 設定 |
| `bpftrace` | root 或 `CAP_BPF` |
| `ftrace` (`/sys/kernel/tracing/`) | root |
| `valgrind` | 不需要 root |
| `lsof -p PID` 看別人 | root（看自己 OK） |

權限不夠的時候訊息常常是 `Operation not permitted`、`Permission denied`、`Failed to open ...`。**第一個猜的就是「要 root 嗎」**。

## 設幾個 kernel 參數讓工具順手

```bash
# perf：放寬 user 看硬體 event
sudo sysctl -w kernel.perf_event_paranoid=1

# 開放 ptrace attach 同 user 的任何 process
echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope

# (debug 用，重開機後還原) 關 ASLR，地址會固定，gdb 比較好對
echo 0 | sudo tee /proc/sys/kernel/randomize_va_space
```

`yama/ptrace_scope` 是個重點。預設多半是 `1`，意思「只能 attach 自己 fork 的 child」。`0` 是「同 user 任意 attach」。

教學環境設 `0` 比較順；production 機器**保留 1**，否則 root exploit 攻擊面變大。

## Sanity check

寫個基準程式 `hello.c`：

```c
#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>

int main(void) {
    int fd = open("/tmp/hello.log", O_CREAT | O_WRONLY | O_TRUNC, 0644);
    dprintf(fd, "hello from pid %d\n", getpid());
    close(fd);
    sleep(1);
    return 0;
}
```

```bash
gcc -g -O0 hello.c -o hello
```

跑下面這串，每個工具都該有合理輸出、沒抱怨缺工具：

```bash
# strace：看 syscall
strace -e trace=openat,write,close ./hello

# ltrace：看 lib call
ltrace ./hello

# lsof：看 shell 自己開的 fd
lsof -p $$ | head

# ss：看 listen 的 socket
ss -tlnp | head

# tcpdump：抓 5 個封包就停（需要 sudo）
sudo tcpdump -i any -c 5 -nn 2>/dev/null

# perf stat：基本計數
perf stat -- ./hello

# valgrind：跑無 leak 的程式
valgrind --leak-check=full ./hello 2>&1 | tail -10

# AddressSanitizer
gcc -fsanitize=address -g hello.c -o hello-asan
./hello-asan
```

每行有輸出、沒 `command not found`、沒 `permission denied` 就 OK。

## 一個常見踩雷：在 docker / WSL 裡跑

Docker container 預設禁了大部分 ptrace、perf、bpf —— 都是 syscall 危險清單。要在 container 裡 debug：

```bash
docker run --cap-add=SYS_PTRACE --security-opt seccomp=unconfined ...
```

或乾脆 `--privileged`（教學 OK，production 不能這樣開）。

WSL 2 跑 user-space 工具沒問題（strace、ltrace、valgrind、gdb），但 `perf` / `bpftrace` 需要對應 kernel module，預設 WSL kernel 沒 build。**強烈建議裝原生 Linux 或開 VM 跑這套課**，不然 Part 5（perf / ftrace / bpftrace）會卡。

## 工具之間的關係預覽

下一章會詳細展開，先看一下：

```
            ┌─────────────────┐
   user     │  你的 C 程式    │
   space    └────────┬────────┘
                     │ libc call (fopen, malloc, ...)
                     ▼
            ┌─────────────────┐  ← ltrace 在這層觀察
            │   libc / .so    │
            └────────┬────────┘
                     │ syscall (openat, mmap, ...)
─────────────────────┼─────────────────  user / kernel 邊界
                     ▼            ← strace 在這條線上
            ┌─────────────────┐
            │  Linux kernel   │
            │  ├ scheduler    │  ← perf / ftrace / bpftrace 在這層
            │  ├ filesystem   │
            │  └ network      │
            └─────────────────┘
```

## 自我檢核

- [ ] 上面套件全部裝完
- [ ] `strace ./hello` 跑得出來、看得到 `openat` 跟 `write`
- [ ] `perf stat -- ./hello` 跑得出來、看得到 cycles / instructions
- [ ] `valgrind ./hello` 沒抱怨缺東西
- [ ] `sudo tcpdump -i any -c 1` 抓得到至少一個封包
- [ ] 知道 `ptrace_scope` 是什麼、設 0 跟 1 差別

下一章看這套工具的全景：什麼工具看什麼層、什麼時機選什麼。

→ [Ch 1 觀察工具全景](./01-observation-tools-overview.md)
