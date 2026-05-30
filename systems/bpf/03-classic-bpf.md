# Ch 3 — Classic BPF：tcpdump 的 packet filter

> **目標**：理解 Classic BPF 的設計動機、VM 架構、bytecode 格式，以及它如何被重新利用在 seccomp-bpf 上——這些是理解「為什麼 eBPF 要設計成現在這樣」的必要背景。

## 為什麼需要這個？

1992 年，tcpdump 在效能上有一個根本問題。

當你執行 `tcpdump 'tcp and port 80'` 的時候，傳統做法是：把**所有**封包從 kernel 複製到 userspace，然後在 userspace 用 C 程式做過濾，只留下 TCP port 80 的封包。問題是在繁忙的網路上，每秒可能有幾萬個封包。把所有封包都 copy 到 userspace 的 CPU 和記憶體 overhead 很高，而且大部分封包都會被丟掉，做了無用功。

Steven McCanne 和 Van Jacobson 在 USENIX 1993 提出的解法是：**把 filter 程式移到 kernel 裡執行**，只把符合條件的封包才複製到 userspace。這個 in-kernel filter 就是 BSD Packet Filter（BPF）。

「把使用者提供的程式放到 kernel 裡執行」聽起來很危險。BPF 的解決方法是定義一個沙箱化的 virtual machine：有受限的指令集，沒有任何方式存取 kernel 記憶體以外的封包資料，並且**保證程式終止**（沒有迴圈）。

這個沙箱 VM 的設計，就是 eBPF 的直接前身。

## 先建立直覺：BPF VM 是什麼？

把 Classic BPF 想成一個極度簡化的 CPU：

```
Classic BPF Virtual Machine

暫存器（只有兩個）：
  A（accumulator）：主要工作暫存器，32-bit
  X（index register）：輔助暫存器，32-bit

記憶體：
  16 個 32-bit word 的 scratch memory（M[0]..M[15]）
  封包資料（只能讀，不能寫）

指令格式（每條指令 8 bytes）：
  ┌──────┬────┬────┬──────────┐
  │ code │ jt │ jf │   k      │
  │ 2B   │1B  │1B  │  4B      │
  └──────┴────┴────┴──────────┘
  code: 指令類型
  jt:   jump if true（往前跳幾條）
  jf:   jump if false（往前跳幾條）
  k:    立即數或記憶體偏移量
```

只有兩個暫存器、16 個 word 的 scratch memory——這個 VM 能做的事很有限，但對封包過濾已經夠了。

## Classic BPF 指令集

Classic BPF 指令分成幾類：

**Load 指令**（從封包或 scratch memory 載入資料到 A 或 X）：

```
ldb [k]      # 從封包偏移 k 載入 1 byte 到 A
ldh [k]      # 從封包偏移 k 載入 2 bytes（半字）到 A
ld  [k]      # 從封包偏移 k 載入 4 bytes 到 A
ld  #k       # 把立即數 k 載入到 A
ldx M[k]     # 從 scratch memory M[k] 載入到 X
```

**ALU 指令**（對 A 做運算）：

```
add #k       # A += k
and #k       # A &= k（常用於 mask 出特定 bits）
or  #k       # A |= k
lsh #k       # A <<= k
rsh #k       # A >>= k
```

**Jump 指令**（條件跳轉）：

```
jeq #k, jt, jf   # if A == k: jump jt steps, else jump jf steps
jgt #k, jt, jf   # if A > k: jump jt, else jf
jge #k, jt, jf   # if A >= k: jump jt, else jf
jset #k, jt, jf  # if A & k: jump jt, else jf
```

**Return 指令**（結束 filter，給 kernel 決策）：

```
ret #0    # 丟棄這個封包（回傳 0）
ret #-1   # 接受這個封包（回傳 65535 = 接受全部）
ret A     # 接受 A 個 bytes
```

## tcpdump 怎麼使用 BPF

當你執行 `tcpdump 'tcp and port 80'`，libpcap 把這個 filter expression 編譯成 BPF bytecode，然後透過 `setsockopt(SO_ATTACH_FILTER)` 把這個 bytecode 附加到 raw socket 上。kernel 用 BPF VM 執行這個 filter，只把符合條件的封包往 raw socket 傳。

你可以看到 tcpdump 生成的 BPF bytecode：

```bash
# 用 -d 印出 BPF 指令
tcpdump -d 'tcp and port 80'
# 輸出類似：
# (000) ldh      [12]                # 載入 Ethernet type（偏移 12）
# (001) jeq      #0x86dd  jt 2  jf 7  # 如果是 IPv6，跳到 2
# (002) ldb      [20]                # 載入 IPv6 next header
# (003) jeq      #0x6     jt 4  jf 19 # 如果是 TCP (0x6)，繼續
# (004) ldh      [54]                # 載入 TCP src port
# (005) jeq      #0x50    jt 18 jf 6  # 如果是 port 80，接受
# (006) ldh      [56]                # 載入 TCP dst port
# ...
# (019) ret      #0                  # 丟棄
```

## 用 C 直接寫 Classic BPF

你可以在 C 程式裡直接構造 BPF filter：

```c
#include <linux/filter.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/*
 * 這個 filter 接受所有 TCP port 80 的封包，丟棄其他。
 * 假設 Ethernet + IPv4 + TCP，不處理 IPv6 和其他 L3。
 *
 * 指令意義：
 * [12] = Ethernet type 欄位（2 bytes）
 * [23] = IPv4 protocol 欄位（1 byte）
 * [20] = IPv4 fragment offset（用來確認不是 fragment）
 * [34] = TCP src port（2 bytes）
 * [36] = TCP dst port（2 bytes）
 */
struct sock_filter tcp_port80_filter[] = {
    /* ldh [12] — 載入 Ethernet type */
    BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 12),
    /* jeq 0x0800 — 如果不是 IPv4，丟棄 */
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0x0800, 0, 8),
    /* ldb [23] — 載入 IP protocol */
    BPF_STMT(BPF_LD | BPF_B | BPF_ABS, 23),
    /* jeq 6 — 如果不是 TCP，丟棄 */
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 6, 0, 6),
    /* ldh [20] — 載入 fragment offset */
    BPF_STMT(BPF_LD | BPF_H | BPF_ABS, 20),
    /* jset 0x1FFF — 如果是 fragment，丟棄 */
    BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, 0x1FFF, 4, 0),
    /* ldxb 4*([14]&0xF) — 計算 IP header 長度，存到 X */
    BPF_STMT(BPF_LDX | BPF_B | BPF_MSH, 14),
    /* ldh [x+14] — 載入 TCP src port */
    BPF_STMT(BPF_LD | BPF_H | BPF_IND, 14),
    /* jeq 80 — 如果是 port 80，接受 */
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 80, 2, 0),
    /* ldh [x+16] — 載入 TCP dst port */
    BPF_STMT(BPF_LD | BPF_H | BPF_IND, 16),
    /* jeq 80 — 如果是 port 80，接受 */
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 80, 0, 1),
    /* ret 0xFFFF — 接受（回傳封包長度，-1 表示全部） */
    BPF_STMT(BPF_RET | BPF_K, 0xFFFF),
    /* ret 0 — 丟棄 */
    BPF_STMT(BPF_RET | BPF_K, 0),
};

int main(void)
{
    struct sock_fprog fprog = {
        .len    = sizeof(tcp_port80_filter) / sizeof(tcp_port80_filter[0]),
        .filter = tcp_port80_filter,
    };

    /* 建立 raw socket */
    int sock = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (sock < 0) { perror("socket"); return 1; }

    /* 附加 BPF filter */
    if (setsockopt(sock, SOL_SOCKET, SO_ATTACH_FILTER,
                   &fprog, sizeof(fprog)) < 0) {
        perror("setsockopt");
        close(sock);
        return 1;
    }

    printf("Filter attached. Waiting for TCP port 80 packets...\n");

    char buf[1500];
    ssize_t n;
    while ((n = recv(sock, buf, sizeof(buf), 0)) > 0)
        printf("Received %zd bytes\n", n);

    close(sock);
    return 0;
}
```

編譯和執行（需要 root）：

```bash
gcc -o tcp_filter tcp_filter.c
sudo ./tcp_filter
# 在另一個 terminal：curl http://example.com
# 應該看到 "Received NNN bytes"
```

## seccomp-bpf：Classic BPF 的第二生命

2012 年，Will Drewry 把 Classic BPF 重新應用到 syscall 過濾上：你提供一個 BPF 程式，它讀取 syscall 的號碼和參數，回傳 ALLOW / DENY / 其他 action。這就是 seccomp-bpf（kernel 3.5）。

Chrome 是第一個大規模使用 seccomp-bpf 的程式，用它讓 renderer process 只能呼叫 rendering 需要的 syscall，限制惡意 web content 能做的事。Docker/containerd 用它限制 container 的 syscall 集合。

```c
/* 一個簡單的 seccomp filter：只允許 read/write/exit，其他全部 kill */
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <linux/audit.h>
#include <sys/prctl.h>
#include <syscall.h>
#include <stdio.h>
#include <stdlib.h>

#define ALLOW_SYSCALL(name) \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_##name, 0, 1), \
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)

static struct sock_filter filter[] = {
    /* 載入 syscall 號碼（在 seccomp 的 BPF context 裡，偏移 0 是 arch，偏移 4 是 syscall nr） */
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
             (offsetof(struct seccomp_data, nr))),
    ALLOW_SYSCALL(read),
    ALLOW_SYSCALL(write),
    ALLOW_SYSCALL(exit),
    ALLOW_SYSCALL(exit_group),
    /* 其他 syscall：KILL */
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL),
};

int main(void)
{
    struct sock_fprog prog = {
        .len    = sizeof(filter) / sizeof(filter[0]),
        .filter = filter,
    };

    /* 設定 seccomp filter（需要 PR_SET_NO_NEW_PRIVS） */
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) {
        perror("prctl");
        return 1;
    }
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog)) {
        perror("seccomp");
        return 1;
    }

    /* 現在只能用 read/write/exit */
    write(STDOUT_FILENO, "Hello from seccomp sandbox!\n", 28);

    /* 這行會觸發 SECCOMP_RET_KILL，process 被強制終止 */
    /* getpid();  <-- 取消這行的註解來測試 */

    return 0;
}
```

```bash
gcc -o seccomp_demo seccomp_demo.c
./seccomp_demo
# 輸出：Hello from seccomp sandbox!
# 取消 getpid() 那行的註解再編譯，跑起來會看到 Killed
```

## Classic BPF 的限制——為什麼需要「Extended」

Classic BPF 的這些限制促成了 eBPF 的誕生：

| 限制 | 問題 | eBPF 的解法 |
|---|---|---|
| 只有 2 個 32-bit 暫存器 | 複雜邏輯無法表達 | 11 個 64-bit 暫存器 |
| 沒有 map（持久化資料） | filter 不能保存 state | BPF maps（多種型別） |
| 只能做封包 / syscall 過濾 | 不能 attach 到任意 kernel 函式 | 多種 program type + hook |
| 沒有 JIT（早期） | 效能有限 | 所有主流架構都有 JIT |
| filter 不能呼叫 kernel 函式 | 無法存取複雜的 kernel 資料 | BPF helper functions |
| 不能做 map lookup | 無法做 stateful 過濾 | 有 map，能做 per-connection 狀態 |

## 踩雷集錦

1. **Classic BPF 的 `ret` 語意和 eBPF 不同**：Classic BPF 的 `ret` 是過濾結果（0 = 丟棄，非 0 = 接受多少 bytes）；eBPF 的 `return` 是 program 特定的語意（XDP 的 return 是 action，kprobe 的 return 是忽略的）

2. **`BPF_JUMP` 的 `jt`/`jf` 是相對偏移，不是絕對位置**：`BPF_JUMP(JEQ, 0x800, 0, 8)` 是「如果相等，往前跳 0 條（執行下一條）；否則跳過 8 條」。初學者常弄錯方向（是往後跳，不是往前跳）

3. **seccomp filter 的 `offsetof` 要對應 `struct seccomp_data`**：`struct seccomp_data` 裡的欄位偏移量是固定的（`nr` 在 offset 0，`arch` 在 offset 4，`instruction_pointer` 在 offset 8，`args` 在 offset 16）；寫錯偏移就是讀到錯誤資料

4. **Classic BPF 的封包偏移是從 L2 開始**：offset 0 是 Ethernet header 的開始，不是 IP header 的開始。這和某些其他 packet filter 不同

5. **`SO_ATTACH_FILTER` vs `SO_ATTACH_BPF`**：`SO_ATTACH_FILTER` 附加 Classic BPF filter；`SO_ATTACH_BPF` 附加 eBPF program（一個 fd）。兩者語意不同

## 動手練習

1. 安裝 tcpdump，執行 `sudo tcpdump -d 'icmp'`，然後 `sudo tcpdump -d 'port 443 and not port 80'`，閱讀生成的 BPF 指令，嘗試理解每一條指令的意義

2. 把上面的 `tcp_filter.c` 改成過濾 UDP port 53（DNS），編譯並測試（執行 `dig google.com`）

3. 修改 `seccomp_demo.c`，嘗試把 `getpid()` 改成 `SECCOMP_RET_ERRNO | EPERM`（回傳錯誤而不是 kill process），觀察行為

4. 執行 `sudo tcpdump -ddd 'tcp and port 80'`（三個 d），看到的是十進位格式的 BPF bytecode；嘗試和 `-d`（人類可讀格式）對照

## 本章重點整理

- Classic BPF 是 1992 年為 tcpdump 設計的 in-kernel packet filter VM，解決了把封包複製到 userspace 再過濾的效能問題
- BPF VM 有 2 個暫存器、16 個 scratch word、受限指令集，保證程式沒有迴圈（一定終止）
- seccomp-bpf 是 2012 年把 Classic BPF 重新利用到 syscall 過濾上的機制，Chrome、Docker 都在用
- Classic BPF 的限制（只有 2 個暫存器、沒有 map、只能做 filter）是 eBPF 被重新設計的直接原因

## 自我檢核

- [ ] 能解釋 Classic BPF 為什麼比「把封包全 copy 到 userspace 再過濾」快
- [ ] 知道 `jt` 和 `jf` 是什麼意思，以及它們是相對偏移還是絕對位置
- [ ] 能說出 seccomp-bpf 和 Classic BPF socket filter 的本質相同點和使用場景差異
- [ ] 知道至少 3 個 Classic BPF 的限制，以及 eBPF 如何解決它們

## 延伸閱讀

### 論文

- **[The BSD Packet Filter: A New Architecture for User-level Packet Capture](https://www.tcpdump.org/papers/bpf-usenix93.pdf)** — McCanne & Jacobson, USENIX Winter 1993
  - **核心貢獻**：在 kernel 裡執行 filter 的設計；BPF VM 的指令集設計選擇
  - **讀哪裡**：Section 2（BPF 架構）和 Section 3（filter 語言）；Figure 1 和 Figure 2 是精華
  - **和本章的關聯**：本章所描述的 Classic BPF 就是這篇論文的實作

### 官方文件

- **[Linux kernel: Classic BPF](https://www.kernel.org/doc/html/latest/networking/filter.html)**
  - **讀哪裡**：前半段（BPF filter format、BPF engine）；seccomp 那一節
  - **學什麼**：kernel 官方對 Classic BPF 指令集和 filter 語義的完整描述

- **[seccomp man page](https://man7.org/linux/man-pages/man2/seccomp.2.html)**
  - **讀哪裡**：整頁；特別是 `struct seccomp_data` 的欄位定義和 return value 的語意
  - **學什麼**：seccomp-bpf 的完整 API，包括 `SECCOMP_RET_*` 的所有選項

### 部落格

- **[A seccomp overview](https://lwn.net/Articles/656307/)** — Jake Edge, LWN.net, 2015
  - **這篇說什麼**：seccomp 的歷史演進（從 strict mode 到 BPF filter），以及它的安全性保證
  - **讀哪裡**：整篇
  - **為什麼值得讀**：比 man page 更有歷史脈絡，解釋了為什麼 seccomp 要加 BPF filter 模式

→ [Ch 4 eBPF ISA 與 JIT 編譯器](./04-ebpf-isa-and-jit.md)
