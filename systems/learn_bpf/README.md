# BPF 學習筆記：從 packet filter 到生產級 eBPF 工具

> 給已經會 C、想徹底搞懂 Linux kernel 內部觀測與擴展機制的工程師。

這是一系列循序漸進的教學文章，從 1992 年的 classic BPF 講起，一路寫到現代 eBPF 的 verifier、CO-RE、libbpf，並涵蓋 observability、networking、security 三大應用面。最終你會用 Go + eBPF 寫出一個自己的 mini observability + security agent。

## 為什麼學這個？

- **看得見以前看不見的東西**：syscall 流量、process 檔案存取、TCP retransmit、function latency — 不用改一行 kernel code，全部都觀察得到。
- **這是 cloud-native infra 的新底層**：Cilium（Kubernetes 網路）、Falco / Tetragon（runtime security）、Pixie / Parca（observability）背後都是 eBPF。會 BPF 才看得懂這個世代的 infra。
- **不再害怕 kernel**：BPF 給你一個安全的方式進到 kernel 跑 code，verifier 會幫你把關。比寫 kernel module 友善 100 倍，但能力相當接近。
- **debug 神器**：production 上一個間歇性的 bug，用 bpftrace 可能 3 行解決。你會反向懷疑「以前怎麼活下來的」。

## 課程地圖

### Part 0 — 起步
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 BPF 是什麼？從 packet filter 到 universal kernel runtime](./01-bpf-overview.md)

### Part 1 — Kernel 速成（給不熟 kernel 的人）
- [Ch 2 Kernel / User space 邊界與 syscall](./02-kernel-userspace-boundary.md)
- [Ch 3 傳統 kernel 觀測手段：printk、ftrace、perf、strace](./03-traditional-kernel-observation.md)
- [Ch 4 Kernel 鉤子機制：kprobe / uprobe / tracepoint / fentry](./04-kernel-hooks.md)

### Part 2 — BPF 核心架構
- [Ch 5 classic BPF：為什麼會發明一個 in-kernel VM](./05-classic-bpf.md)
- [Ch 6 eBPF instruction set、register、JIT 與 sandboxing](./06-ebpf-isa-and-jit.md)
- [Ch 7 Program types 與 attach 點全景](./07-program-types-and-attach.md)
- [Ch 8 BPF maps：kernel 與 user space 共享狀態](./08-bpf-maps.md)
- [Ch 9 Verifier 深入：為什麼你的 BPF 會被拒絕](./09-verifier-deep-dive.md)
- [Ch 10 BTF 與 CO-RE：跨 kernel 版本部署](./10-btf-and-core.md)
- [練習 A：用 bpftool 探索系統上的 BPF](./practice-a-bpftool-exploration.md)

### Part 3 — 寫 BPF：從高階到低階
- [Ch 11 bpftrace：一行解決問題的高階語言](./11-bpftrace.md)
- [Ch 12 bcc：Python 包 C 的混合方式](./12-bcc.md)
- [Ch 13 libbpf + CO-RE 入門（kernel side C）](./13-libbpf-core-kernel-side.md)
- [Ch 14 User space loader：用 C 寫 loader](./14-userspace-loader-c.md)
- [Ch 15 cilium/ebpf：Go 寫 user space](./15-cilium-ebpf-go.md)
- [練習 B：execve tracer 三種寫法](./practice-b-execve-tracer.md)

### Part 4 — Observability 應用
- [Ch 16 效能分析經典工具巡禮](./16-bpf-performance-tools.md)
- [Ch 17 USDT：觀察 user space 應用](./17-usdt.md)
- [Ch 18 Profiling 與 flamegraph 製作](./18-profiling-flamegraph.md)
- [練習 C：SQL 慢查詢 tracer](./practice-c-sql-slow-query-tracer.md)

### Part 5 — Networking
- [Ch 19 XDP：最快的封包處理路徑](./19-xdp.md)
- [Ch 20 TC BPF：ingress/egress 流量控制](./20-tc-bpf.md)
- [Ch 21 Socket-level BPF：sockops、sk_msg、sock_filter](./21-socket-level-bpf.md)
- [練習 D：XDP 防火牆](./practice-d-xdp-firewall.md)

### Part 6 — Security
- [Ch 22 seccomp-bpf：syscall 過濾](./22-seccomp-bpf.md)
- [Ch 23 BPF LSM：kernel 級安全鉤子](./23-bpf-lsm.md)
- [Ch 24 觀測派 vs 阻擋派：Falco / Tetragon 架構](./24-falco-tetragon.md)

### Part 7 — 進階與生產
- [Ch 25 Ring buffer vs perf buffer](./25-ringbuf-vs-perfbuf.md)
- [Ch 26 Tail call、program chain、map-in-map](./26-tailcall-and-composition.md)
- [Ch 27 Debug 技巧：verifier log、bpftool、bpf_printk](./27-debugging-bpf.md)
- [Ch 28 效能、安全、生產部署考量](./28-production-considerations.md)

### Part 8 — 整合專案
- [Final Project：Mini observability + security agent](./final-project-mini-agent.md)

## 學習方式建議

1. **每章親手敲過**：BPF 是「不跑就不會懂」的領域。verifier 的脾氣只有被它拒絕過才會記得。
2. **故意觸發 verifier**：寫 unbounded loop、寫越界存取、忘記 NULL check — 把它逼到拒絕你，看 verifier log 怎麼罵。這是 debug 必備技能。
3. **對照 kernel source**：很多 helper function 沒有完整文件，看 `tools/lib/bpf/`、`include/uapi/linux/bpf.h` 才是答案。本教材會在關鍵處給你檔案路徑。
4. **慎選 kernel 版本**：BPF 演進極快，5.4、5.8、5.13、6.x 各自開放了不同 feature。本教材以 **kernel ≥ 5.15**（CO-RE、ringbuf、BPF LSM 都成熟）為基準。
5. **善用 bpftool**：這是你最重要的調試工具，地位等同 `strace` 之於 syscall。Ch 0 就會裝起來，之後天天用。

## 參考資料

- 《Learning eBPF》— Liz Rice, O'Reilly（最新、最對口的入門書）
- 《BPF Performance Tools》— Brendan Gregg, Addison-Wesley（observability 聖經）
- 《Linux Observability with BPF》— David Calavera & Lorenzo Fontana, O'Reilly
- ebpf.io 官方入口：<https://ebpf.io>
- libbpf-bootstrap（最佳 libbpf 範例集）：<https://github.com/libbpf/libbpf-bootstrap>
- Cilium eBPF 文件：<https://docs.cilium.io/en/stable/bpf/>
- bpftrace 一行教學集：<https://github.com/bpftrace/bpftrace/blob/master/docs/tutorial_one_liners.md>
