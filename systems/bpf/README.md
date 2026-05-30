# eBPF 完整學習筆記：從 kernel 底層到生產級 observability agent

> 給懂一點 C、想把 eBPF 從頭學到底的工程師。

這系列從 Classic BPF 的歷史出發，帶你走過 eBPF ISA、verifier 安全機制、BTF/CO-RE 相容性系統、全部主要工具鏈（bpftrace / BCC / libbpf / cilium-ebpf），深入 tracing、networking、security 三大應用域，最後整合成一個生產可用的 observability agent。讀完你能看懂 `kernel/bpf/` 的 source code、能設計自己的 BPF program、能讀懂 verifier 拒絕你的原因。

## 為什麼學這個？

- **observability 的現代標準**：Datadog Agent、Grafana Beyla、Cilium、Falco 底層都跑 eBPF；不懂它，你只能用別人封裝好的工具，永遠不知道為什麼出問題
- **理解底層設計**：verifier 怎麼用抽象解釋（abstract interpretation）證明安全性、JIT 怎麼把 BPF bytecode 變成 native x86-64、CO-RE 怎麼在不重新編譯的情況下跑在不同 kernel 版本——這些是值得花時間理解的系統設計
- **職涯實用性**：Linux kernel、SRE、platform engineering、cloud-native security 職位越來越常問 eBPF；Cloudflare、Meta、Google、Datadog、Isovalent 的技術面試都可能碰到

## 先修知識

- **C 語言**（程度：會指標、struct、function pointer；不需要 kernel programming 經驗）
- **Linux 基礎**（程度：會用 shell、知道 process 和 file descriptor 是什麼）
- 不需要：kernel module 開發、assembly、network programming（課程會補足必要背景）

## 課程地圖

### Part 1 — 基礎與歷史（Ch 0–5）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 為什麼是 eBPF？](./01-why-ebpf.md)
- [Ch 2 你需要的 Linux kernel 底層知識](./02-linux-kernel-basics.md)
- [Ch 3 Classic BPF：tcpdump 的 packet filter](./03-classic-bpf.md)
- [Ch 4 eBPF ISA 與 JIT 編譯器](./04-ebpf-isa-and-jit.md)
- [Ch 5 eBPF Verifier：安全性證明的工作原理](./05-ebpf-verifier.md)
- [練習 A：bpftool 全面探索](./practice-a-bpftool-exploration.md)

### Part 2 — 核心抽象（Ch 6–12）
- [Ch 6 Program Types 完整解析](./06-program-types.md)
- [Ch 7 Attach 機制與 bpf_link 生命週期](./07-attach-mechanisms.md)
- [Ch 8 BPF Maps：所有資料結構](./08-bpf-maps.md)
- [Ch 9 BTF：BPF Type Format 深入](./09-btf-deep-dive.md)
- [Ch 10 CO-RE：Compile Once Run Everywhere](./10-co-re.md)
- [Ch 11 Helper Functions 系統](./11-helper-functions.md)
- [Ch 12 BPF syscall 底層序列](./12-bpf-syscall-internals.md)
- [練習 B：裸 BPF syscall 實作](./practice-b-raw-bpf-syscall.md)

### Part 3 — 工具鏈（Ch 13–18）
- [Ch 13 bpftrace：動態腳本語言](./13-bpftrace.md)
- [Ch 14 BCC：Python-kernel 雙語框架](./14-bcc.md)
- [Ch 15 libbpf：現代 C 開發框架](./15-libbpf.md)
- [Ch 16 BPF Skeleton：自動生成的 userspace 介面](./16-bpf-skeleton.md)
- [Ch 17 cilium/ebpf：Go 生態系](./17-cilium-ebpf-go.md)
- [Ch 18 交叉比較：選哪個工具？](./18-toolchain-comparison.md)
- [練習 C：libbpf execve tracer](./practice-c-libbpf-execve-tracer.md)

### Part 4 — Tracing 深挖（Ch 19–25）
- [Ch 19 kprobes / kretprobes](./19-kprobes-kretprobes.md)
- [Ch 20 Tracepoints 與 raw_tracepoints](./20-tracepoints-raw-tracepoints.md)
- [Ch 21 fentry / fexit：BTF-based hooks](./21-fentry-fexit.md)
- [Ch 22 USDT：userspace 靜態探針](./22-usdt.md)
- [Ch 23 perf_event 與 PMU 硬體計數器](./23-perf-event-pmu.md)
- [Ch 24 Profiling 與 Flamegraph](./24-profiling-flamegraph.md)
- [Ch 25 ringbuf vs perfbuf：事件傳輸設計](./25-ringbuf-vs-perfbuf.md)
- [練習 D：PostgreSQL slow query tracer](./practice-d-postgresql-slow-query.md)

### Part 5 — Networking（Ch 26–33）
- [Ch 26 XDP：最快的 packet 處理](./26-xdp.md)
- [Ch 27 AF_XDP：零拷貝 userspace packet I/O](./27-af-xdp.md)
- [Ch 28 TC BPF：流量整形與分類](./28-tc-bpf.md)
- [Ch 29 Socket BPF：sk_msg, sk_skb, sockmap](./29-socket-bpf.md)
- [Ch 30 cgroup BPF：容器網路控制](./30-cgroup-bpf.md)
- [Ch 31 BPF 與 Cilium：Kubernetes CNI](./31-cilium-kubernetes.md)
- [Ch 32 BPF Load Balancer 設計](./32-bpf-load-balancer.md)
- [Ch 33 BPF 與 Service Mesh](./33-bpf-service-mesh.md)
- [練習 E：XDP stateful firewall](./practice-e-xdp-stateful-firewall.md)

### Part 6 — Security（Ch 34–38）
- [Ch 34 seccomp-bpf：syscall 過濾](./34-seccomp-bpf.md)
- [Ch 35 BPF-LSM：強制存取控制](./35-bpf-lsm.md)
- [Ch 36 Falco & Tetragon](./36-falco-tetragon.md)
- [Ch 37 Offensive eBPF：rootkit 技術](./37-offensive-ebpf.md)
- [Ch 38 BPF 在容器與 Kubernetes 安全](./38-container-kubernetes-security.md)

### Part 7 — 進階機制（Ch 39–44）
- [Ch 39 Tail calls 與 BPF-to-BPF calls](./39-tail-calls-bpf-to-bpf.md)
- [Ch 40 並發控制：spinlock, per-CPU maps, atomic](./40-concurrency-in-bpf.md)
- [Ch 41 bpf_timer 與非同步事件](./41-bpf-timer.md)
- [Ch 42 BPF Iterator 與批次操作](./42-bpf-iterator.md)
- [Ch 43 Task/inode/sk local storage](./43-task-inode-sk-storage.md)
- [Ch 44 Debugging：verifier 錯誤與 bpf_printk](./44-debugging-bpf.md)
- [練習 F：生產用 observability agent](./practice-f-observability-agent.md)

### Final Project
- [Final Project：生產級 eBPF agent](./final-project-production-ebpf-agent.md)

## 學習方式建議

1. **讀完一章就動手**：每章的「動手練習」不是選做，是強制——BPF 的坑必須親身踩過才能建立直覺
2. **故意把它弄壞**：改壞一個 BPF program，讀 verifier 的拒絕訊息；這比讀說明書更有教育意義
3. **追 kernel source**：`kernel/bpf/verifier.c`、`kernel/bpf/syscall.c`、`net/core/filter.c`——每章都會給對應的 kernel 檔案路徑

## 精選資料庫

### 必讀基礎

- **《BPF Performance Tools》** — Brendan Gregg（Addison-Wesley, 2019）
  - eBPF tracing 最完整的書；Ch 1–3 是架構通論，Ch 4 起是工具實戰；2019 年出版，以 BCC 為主，BPF skeleton 語法和現代 libbpf 有差距，注意版本差異

- **[Linux kernel BPF documentation](https://www.kernel.org/doc/html/latest/bpf/)**
  - 最終權威來源；`btf.rst`、`verifier.rst`、`maps.rst` 必讀；碰到 verifier reject 先來這裡查

- **[eBPF.io — What is eBPF?](https://ebpf.io/what-is-ebpf/)**
  - 官方生態系最好的非技術性入門；讀一遍建立概念全圖，約 15 分鐘

### 推薦論文

- **[The BSD Packet Filter: A New Architecture for User-level Packet Capture](https://www.tcpdump.org/papers/bpf-usenix93.pdf)** — McCanne & Jacobson, USENIX Winter 1993
  - eBPF 的歷史起點；理解 Classic BPF 的設計動機，以及為什麼需要「extended」版本；讀 Section 2–4 即可

- **[Fast Packet Processing with eBPF and XDP](https://dl.acm.org/doi/10.1145/3281411.3281443)** — Høiland-Jørgensen et al., ACM CoNEXT 2018
  - XDP 的第一篇系統性評測；Section 3 解釋架構，Section 4–5 是和 DPDK 的效能對比

### 推薦部落格 / 文章

- **[Brendan Gregg's eBPF posts](https://brendangregg.com/ebpf.html)** — Brendan Gregg
  - 全球最重要的 Linux performance 工程師的 eBPF 文章入口；`ebpf-future-of-observability.html` 首選

- **[BPF and XDP Reference Guide](https://docs.cilium.io/en/stable/reference-guides/bpf/)** — Cilium docs
  - 目前網路上最完整的 BPF 技術參考；比 kernel docs 更有系統，比書更新

### 讀完本課之後

- **《Systems Performance, 2nd ed.》** — Brendan Gregg（將本課 tracing 知識推到生產性能調優的完整框架）
- **[Linux kernel source: kernel/bpf/](https://elixir.bootlin.com/linux/latest/source/kernel/bpf)**（直接讀 BPF subsystem；`verifier.c` 和 `syscall.c` 先讀）
