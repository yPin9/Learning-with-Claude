# Ch 27 — AF_XDP：零拷貝 userspace packet I/O

> **目標**：理解 AF_XDP socket 的架構——UMEM 的記憶體模型、四個 ring 的角色（Fill/Completion/TX/RX）、如何把 XDP 程式和 AF_XDP 結合做高效能的 userspace packet processing。

## AF_XDP 的定位

AF_XDP 是一個特殊的 socket 類型，讓 XDP 程式可以把封包直接 **redirect 到 userspace 的記憶體**，不走 kernel 的 networking stack，不做額外拷貝。

```
封包進入路徑：

NIC driver → XDP program
               │
               ├── XDP_PASS → 正常 networking stack
               │
               └── bpf_redirect_map(&xsks_map, queue_id, 0)
                         │
                         └── AF_XDP socket（userspace 直接讀取）
                               ↑ 零拷貝（NIC DMA → userspace memory）
```

AF_XDP 的用途：
- 高效能封包捕捉（取代 libpcap）
- Kernel bypass 封包處理（類似 DPDK，但不需要替換驅動）
- Custom L3/L4 協定棧

## UMEM：共享記憶體空間

UMEM（Userspace Memory）是 AF_XDP 的核心：一塊由 userspace 分配、被 NIC 和 userspace 共享的記憶體，封包直接 DMA 進這塊記憶體，不需要任何拷貝。

```
UMEM 佈局：

┌──────────────────────────────────────────────────────┐
│                      UMEM                             │
│  Frame 0: [封包資料][...] (chunk_size bytes)          │
│  Frame 1: [封包資料][...]                             │
│  ...                                                  │
│  Frame N: [封包資料][...]                             │
└──────────────────────────────────────────────────────┘

每個 frame 的地址 = frame_index × chunk_size
預設 chunk_size = 2048 bytes（或 4096）
```

## 四個 Ring

```
AF_XDP 的四個 ring（每個都是 SPSC ring buffer）：

Fill Ring（Userspace → Kernel）：
  userspace 把空的 UMEM frame 地址放入，告訴 kernel 可以用來接收封包

RX Ring（Kernel → Userspace）：
  kernel 把收到的封包的 UMEM 地址放入，告訴 userspace 有封包可以讀

TX Ring（Userspace → Kernel）：
  userspace 把要發送的封包的 UMEM 地址放入，讓 kernel 發送

Completion Ring（Kernel → Userspace）：
  kernel 通知 userspace 哪些 TX frame 已經發送完畢（可以重用）
```

## 最小的 AF_XDP 接收程式

```c
#include <linux/if_xdp.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <linux/if_link.h>
#include <net/if.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define FRAME_SIZE   2048
#define NUM_FRAMES   4096
#define RING_SIZE    2048

/* UMEM 設定 */
struct xsk_umem_info {
    void  *area;
    size_t size;
    struct xdp_umem_reg umem_reg;
};

int setup_umem(int xsk_fd, struct xsk_umem_info *umem)
{
    size_t total = (size_t)NUM_FRAMES * FRAME_SIZE;
    void *area = mmap(NULL, total,
                      PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB, -1, 0);
    if (area == MAP_FAILED) {
        /* 如果 hugepage 不可用，回退到普通 mmap */
        area = mmap(NULL, total, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    }
    if (area == MAP_FAILED) return -1;

    umem->area = area;
    umem->size = total;

    struct xdp_umem_reg reg = {
        .addr       = (unsigned long)area,
        .len        = total,
        .chunk_size = FRAME_SIZE,
        .headroom   = 0,
    };
    umem->umem_reg = reg;

    /* 向 kernel 注冊 UMEM */
    return setsockopt(xsk_fd, SOL_XDP, XDP_UMEM_REG, &reg, sizeof(reg));
}
```

**完整的 AF_XDP 接收 loop（概念示範）**：

```c
/*
 * 完整的 AF_XDP 實作比較複雜，這裡只展示核心概念。
 * 建議用 libxdp（libbpf 的 AF_XDP 層）或 xdp-tools 的 AF_XDP skeleton。
 */

while (1) {
    /* 1. poll（等待封包到來）*/
    struct pollfd fds = { .fd = xsk_fd, .events = POLLIN };
    poll(&fds, 1, 1000);

    /* 2. 從 RX ring 讀取 frame */
    unsigned int cons_idx;
    if (xsk_ring_cons__peek(&rx_ring, BATCH_SIZE, &cons_idx) == 0)
        continue;

    /* 3. 處理每個 frame */
    for (unsigned int i = 0; i < rx_batch; i++) {
        const struct xdp_desc *desc = xsk_ring_cons__rx_desc(&rx_ring, cons_idx + i);
        uint8_t *pkt = (uint8_t *)umem_area + desc->addr;
        uint32_t len = desc->len;

        /* 在這裡處理封包（不需要 copy，直接讀 UMEM）*/
        printf("received %u bytes\n", len);

        /* 4. 把 frame 放回 Fill ring 供下次接收 */
        xsk_ring_prod__fill_addr(&fill_ring, prod_idx + i) = desc->addr;
    }

    xsk_ring_cons__release(&rx_ring, rx_batch);
    xsk_ring_prod__submit(&fill_ring, rx_batch);
}
```

## XDP 程式：把封包 redirect 到 AF_XDP

XDP 程式需要用 `XSKMAP` 把封包導向 AF_XDP socket：

```c
/* xsk_redirect.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>

/* XSKMAP：queue id → AF_XDP socket fd */
struct {
    __uint(type, BPF_MAP_TYPE_XSKMAP);
    __uint(key_size, sizeof(int));
    __uint(value_size, sizeof(int));
    __uint(max_entries, 64);
} xsks_map SEC(".maps");

SEC("xdp")
int xdp_redirect_to_xsk(struct xdp_md *ctx)
{
    /* 把封包 redirect 到 AF_XDP socket（queue 0）*/
    int queue_id = ctx->rx_queue_index;
    int ret = bpf_redirect_map(&xsks_map, queue_id, XDP_PASS);
    /* 如果 XSKMAP 裡 queue_id 沒有 socket，XDP_PASS fallback */
    return ret;
}

char LICENSE[] SEC("license") = "GPL";
```

**Userspace 把 AF_XDP socket fd 放入 XSKMAP**：

```c
int xsk_fd = socket(AF_XDP, SOCK_RAW, 0);
/* ... 設定 UMEM、bind 到介面和 queue ... */

/* 把 xsk fd 放到 XSKMAP 的 queue 0 */
int queue_id = 0;
bpf_map_update_elem(xsks_map_fd, &queue_id, &xsk_fd, BPF_ANY);
```

## AF_XDP vs DPDK 的比較

| 面向 | AF_XDP | DPDK |
|---|---|---|
| **驅動需求** | 標準 NIC driver（native XDP 支援）| 專用 DPDK PMD 驅動 |
| **和 kernel 整合** | 是（可以 pass 到正常 networking stack）| 否（完全 bypass）|
| **效能** | 接近 DPDK（native 模式）| 業界最高 |
| **複雜度** | 較低（利用現有 BPF 生態）| 較高（獨立生態系）|
| **安全性** | 受 BPF verifier 保護 | 無 verifier |
| **適合場景** | 需要偶爾 pass 給 kernel 的場景 | 純 kernel bypass |

## 踩雷集錦

1. **AF_XDP 需要 root 或 `CAP_NET_RAW + CAP_BPF`**：普通 user 無法建立 AF_XDP socket

2. **UMEM 和 ring 的 alignment 要求**：chunk_size 必須是 2 的幂次，且 ring size 必須是 2 的幂次；否則 `setsockopt` 回傳 EINVAL

3. **不是所有 NIC 都支援 zero-copy 模式**：`XDP_COPY` 模式（software copy）幾乎所有 NIC 都支援；`XDP_ZEROCOPY` 需要 NIC driver 的 zerocopy 支援（Intel i40e、mlx5 等）

4. **libxdp vs 手動 AF_XDP API**：手動用系統呼叫建立 AF_XDP socket 非常繁瑣；建議用 libxdp（`xdp-tools` 的一部分）或 cilium/ebpf 的 AF_XDP 支援

## 動手練習

1. 安裝 xdp-tools（`apt install libxdp-dev xdp-tools`），用 `xdpdump` 工具捕捉網路封包，對比 tcpdump 的 overhead（用 `perf stat` 比較）

2. 讀 `xdp-tools` 的 `lib/libxdp/` 的 source，理解 AF_XDP socket 的建立序列（重點看 `xsk_socket__create`）

## 本章重點整理

- AF_XDP 讓 XDP 程式把封包 redirect 到 userspace 的 UMEM，不走 kernel networking stack
- 四個 ring（Fill/RX/TX/Completion）構成 userspace 和 kernel 之間的 lock-free 溝通機制
- 比 DPDK 更能和 kernel networking stack 共存；比 libpcap 效能高
- 實際使用建議透過 libxdp 而不是直接用低階 syscall

## 自我檢核

- [ ] 能解釋 UMEM 是什麼，以及「零拷貝」是如何實現的
- [ ] 知道四個 ring 各自的方向（誰寫誰讀）和用途
- [ ] 能說出 AF_XDP 和 DPDK 的主要差異

→ [Ch 28 TC BPF：流量整形與分類](./28-tc-bpf.md)
