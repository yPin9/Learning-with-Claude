# 練習 D — XDP 防火牆

> 目標：寫一個 production-shape 的 XDP firewall — LPM trie 維護 IP/CIDR blocklist、丟棄符合的 packet、用 PERCPU array 統計、user space 用 Go (cilium/ebpf) 控制 + 監控。整合 Ch 8、19 與 Go user space。

## 任務規格

實作 `xdp-fw` 工具，支援：

- 動態加 / 移除 / 列出 blocklist entry（IP 或 CIDR）
- 每秒輸出 stats（pass count / drop count、總 byte / drop byte）
- attach / detach 任意 NIC（支援 XDP 的）
- LPM trie 容量 1024 entry

CLI 介面（自由設計）：

```bash
sudo xdp-fw attach eth0
sudo xdp-fw add 192.168.1.100/32
sudo xdp-fw add 10.0.0.0/24
sudo xdp-fw list
sudo xdp-fw stats
sudo xdp-fw remove 192.168.1.100/32
sudo xdp-fw detach eth0
```

## 設計

```
┌─────────────────────────────────────────────────────┐
│  Go user space CLI                                  │
│   - subcommands: attach/add/remove/list/stats       │
│   - 用 cilium/ebpf 操作 maps                          │
└─────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  BPF Kernel programs + maps                         │
│   - XDP filter program                              │
│   - LPM trie map: blocklist                         │
│   - PERCPU_ARRAY map: stats[5] (pass/drop counts)   │
└─────────────────────────────────────────────────────┘
```

## kernel side

<details>
<summary>展開 bpf/xdpfw.bpf.c</summary>

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <linux/if_ether.h>
#include <linux/ip.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

#define MAX_BLOCKLIST 1024

struct lpm_key {
    __u32 prefixlen;
    __u32 addr;
};

struct {
    __uint(type, BPF_MAP_TYPE_LPM_TRIE);
    __type(key, struct lpm_key);
    __type(value, __u8);
    __uint(max_entries, MAX_BLOCKLIST);
    __uint(map_flags, BPF_F_NO_PREALLOC);
} blocklist SEC(".maps");

enum {
    STAT_PASS_PKTS = 0,
    STAT_PASS_BYTES,
    STAT_DROP_PKTS,
    STAT_DROP_BYTES,
    STAT_PARSE_ERR,
    STAT_MAX
};

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __type(key, __u32);
    __type(value, __u64);
    __uint(max_entries, STAT_MAX);
} stats SEC(".maps");

static __always_inline void bump(__u32 idx, __u64 by) {
    __u64 *v = bpf_map_lookup_elem(&stats, &idx);
    if (v) *v += by;
}

SEC("xdp")
int xdp_firewall(struct xdp_md *ctx) {
    void *data     = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;
    __u64 pkt_len  = data_end - data;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) {
        bump(STAT_PARSE_ERR, 1); return XDP_PASS;
    }
    if (eth->h_proto != bpf_htons(ETH_P_IP)) {
        bump(STAT_PASS_PKTS, 1); bump(STAT_PASS_BYTES, pkt_len); return XDP_PASS;
    }

    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end) {
        bump(STAT_PARSE_ERR, 1); return XDP_PASS;
    }

    struct lpm_key key = { .prefixlen = 32, .addr = ip->saddr };
    if (bpf_map_lookup_elem(&blocklist, &key)) {
        bump(STAT_DROP_PKTS, 1); bump(STAT_DROP_BYTES, pkt_len);
        return XDP_DROP;
    }

    bump(STAT_PASS_PKTS, 1); bump(STAT_PASS_BYTES, pkt_len);
    return XDP_PASS;
}
```

</details>

## user side (Go)

<details>
<summary>展開 main.go</summary>

```go
package main

//go:generate go run github.com/cilium/ebpf/cmd/bpf2go -cc clang -cflags "-O2 -g -Wall" xdpfw ./bpf/xdpfw.bpf.c -- -I./bpf

import (
    "encoding/binary"
    "fmt"
    "log"
    "net"
    "os"
    "os/signal"
    "strings"
    "syscall"
    "time"

    "github.com/cilium/ebpf"
    "github.com/cilium/ebpf/link"
    "github.com/cilium/ebpf/rlimit"
)

type lpmKey struct {
    PrefixLen uint32
    Addr      uint32
}

const (
    statPassPkts = iota
    statPassBytes
    statDropPkts
    statDropBytes
    statParseErr
    statMax
)

func main() {
    if len(os.Args) < 2 { usage() }

    if err := rlimit.RemoveMemlock(); err != nil { log.Fatal(err) }

    objs := xdpfwObjects{}
    spec, err := loadXdpfw()
    if err != nil { log.Fatal(err) }

    coll, err := ebpf.NewCollection(spec)
    if err != nil { log.Fatal(err) }
    defer coll.Close()

    // 找 pinned blocklist；沒就 pin
    pinPath := "/sys/fs/bpf/xdpfw_blocklist"
    blocklist := coll.Maps["blocklist"]
    if _, err := os.Stat(pinPath); os.IsNotExist(err) {
        blocklist.Pin(pinPath)
    }

    statsPin := "/sys/fs/bpf/xdpfw_stats"
    statsMap := coll.Maps["stats"]
    if _, err := os.Stat(statsPin); os.IsNotExist(err) {
        statsMap.Pin(statsPin)
    }

    switch os.Args[1] {
    case "attach":
        attach(coll.Programs["xdp_firewall"], os.Args[2])
    case "detach":
        detach(os.Args[2])
    case "add":
        addBlock(blocklist, os.Args[2])
    case "remove":
        removeBlock(blocklist, os.Args[2])
    case "list":
        listBlock(blocklist)
    case "stats":
        showStats(statsMap)
    default:
        usage()
    }
}

func usage() {
    fmt.Fprintln(os.Stderr, "usage: xdp-fw {attach|detach|add|remove|list|stats} [args]")
    os.Exit(2)
}

func ifIndex(name string) int {
    iface, err := net.InterfaceByName(name)
    if err != nil { log.Fatal(err) }
    return iface.Index
}

func attach(prog *ebpf.Program, iface string) {
    l, err := link.AttachXDP(link.XDPOptions{
        Program:   prog,
        Interface: ifIndex(iface),
    })
    if err != nil { log.Fatal(err) }
    if err := l.Pin("/sys/fs/bpf/xdpfw_link_" + iface); err != nil {
        log.Fatal(err)
    }
    fmt.Printf("Attached to %s\n", iface)
}

func detach(iface string) {
    pin := "/sys/fs/bpf/xdpfw_link_" + iface
    os.Remove(pin)
    fmt.Printf("Detached from %s\n", iface)
}

func parseCIDR(s string) (lpmKey, error) {
    if !strings.Contains(s, "/") { s += "/32" }
    _, n, err := net.ParseCIDR(s)
    if err != nil { return lpmKey{}, err }
    pl, _ := n.Mask.Size()
    addr := binary.LittleEndian.Uint32(n.IP.To4())
    return lpmKey{PrefixLen: uint32(pl), Addr: addr}, nil
}

func addBlock(m *ebpf.Map, s string) {
    k, err := parseCIDR(s); if err != nil { log.Fatal(err) }
    var v uint8 = 1
    if err := m.Put(&k, &v); err != nil { log.Fatal(err) }
    fmt.Printf("Added %s\n", s)
}

func removeBlock(m *ebpf.Map, s string) {
    k, err := parseCIDR(s); if err != nil { log.Fatal(err) }
    if err := m.Delete(&k); err != nil { log.Fatal(err) }
    fmt.Printf("Removed %s\n", s)
}

func listBlock(m *ebpf.Map) {
    iter := m.Iterate()
    var k lpmKey
    var v uint8
    fmt.Println("BLOCKLIST:")
    for iter.Next(&k, &v) {
        ip := make(net.IP, 4)
        binary.LittleEndian.PutUint32(ip, k.Addr)
        fmt.Printf("  %s/%d\n", ip, k.PrefixLen)
    }
}

func showStats(m *ebpf.Map) {
    sig := make(chan os.Signal, 1)
    signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
    tick := time.NewTicker(time.Second)
    defer tick.Stop()
    for {
        select {
        case <-sig: return
        case <-tick.C:
            for i := uint32(0); i < statMax; i++ {
                var perCPU []uint64
                if err := m.Lookup(&i, &perCPU); err != nil { continue }
                total := uint64(0)
                for _, v := range perCPU { total += v }
                fmt.Printf("[%d]=%d ", i, total)
            }
            fmt.Println()
        }
    }
}
```

</details>

## Build

```bash
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > bpf/vmlinux.h
go generate
go build -o xdp-fw .
```

## 測試流程

```bash
# 1. attach to lo
sudo ./xdp-fw attach lo

# 2. 加一個 blocklist
sudo ./xdp-fw add 127.0.0.1/32

# 3. 試 ping localhost - 應該 100% 失敗
ping -c 3 -W 1 127.0.0.1

# 4. 看 stats
sudo ./xdp-fw stats &
ping -c 100 127.0.0.1   # 觸發
sleep 5
kill %1

# 5. 移除 + 還原 ping
sudo ./xdp-fw remove 127.0.0.1/32
ping -c 3 127.0.0.1   # 又通了

# 6. 清乾淨
sudo ./xdp-fw detach lo
sudo rm /sys/fs/bpf/xdpfw_*
```

## 進階

7. **支援 IPv6**：加一個 LPM trie 給 IPv6 (`prefixlen + 128-bit addr`)
8. **TCP/UDP port 過濾**：加另一個 map 做 (proto, port) → action
9. **Rate limit**：用 token bucket 對特定 IP 限速
10. **Prometheus exporter**：把 stats 變 `/metrics` endpoint

## 自我檢核

- [ ] 我能寫 LPM trie + bound-checked XDP 程式
- [ ] 我能用 PERCPU_ARRAY 做高頻計數、user 端聚合
- [ ] 我能用 cilium/ebpf 從 Go pin map / attach XDP / 操作 LPM trie
- [ ] 我能用 net.ParseCIDR + binary 處理 CIDR ↔ LPM key 轉換
- [ ] 我能整支端到端跑通：attach → add rule → 觀察 drop → 看 stats

下一個 Part 進 security 領域 — 從最古老但仍主流的 seccomp-bpf 開始。

→ [Ch 22 seccomp-bpf：syscall 過濾](./22-seccomp-bpf.md)
