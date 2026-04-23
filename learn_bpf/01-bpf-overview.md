# Ch 1 — BPF 是什麼？從 packet filter 到 universal kernel runtime

> 目標：在動手之前，先看清 BPF 30 年的演進軌跡 — 為什麼 1992 年要發明它、2014 年怎麼脫胎換骨、為什麼今天 cloud-native infra 全部都在 BPF 上。理解了故事，後面學的每個 feature 才不會像散落的 trivia。

## 一個老故事：1992 的 BSD packet filter

時間倒回 1992 年。Steven McCanne 和 Van Jacobson 在 Berkeley 寫了一篇短論文：

> *The BSD Packet Filter: A New Architecture for User-level Packet Capture*

問題是這樣的：那個年代你想抓網路封包（像 `tcpdump` 那樣），需要 kernel 把每個 packet 複製一份到 user space，再讓 user space 程式自己過濾。**99% 的 packet 是你不要的**，但你已經付了複製到 user space 的成本。慢得不能看。

他們的解法：**讓 user space 把過濾邏輯送進 kernel 跑**。

這想法在當時是大膽的 — 你怎麼能讓一個 user 程式在 kernel ring 0 跑任意 code？答案是：**設計一個極小的虛擬機**，user space 只能送這個 VM 的 bytecode 進來，kernel 解釋執行。VM 的指令集刻意設計成不能無窮迴圈、不能存取任意記憶體 — 也就是說，**安全性建在語言層**，不是建在權限管理上。

這就是 **classic BPF（cBPF）**：Berkeley Packet Filter，一個 32-bit、2 個 register、約 30 條指令的迷你 VM。

`tcpdump` 你今天在用的 `tcpdump host 1.2.3.4 and port 80`，背後就是把這個 expression 編譯成 cBPF bytecode 灌進 kernel：

```bash
sudo tcpdump -d 'host 1.2.3.4 and port 80'
# 輸出 cBPF disassembly：
# (000) ldh      [12]
# (001) jeq      #0x800           jt 2    jf 12
# (002) ld       [26]
# (003) jeq      #0x1020304       jt 6    jf 4
# ...
```

**這是 BPF 的第一性原理**：把使用者邏輯下放到 kernel 跑、用 VM 換取安全。記住這句，eBPF 只是把它做大。

## 2014：Alexei Starovoitov 的轉折

2011–2014 年，Linux 開發者 Alexei Starovoitov 開始大改 BPF，目標是讓它**遠遠超越 packet filter**：

| 改動 | cBPF | eBPF |
|---|---|---|
| 暫存器 | 2 個 32-bit | 11 個 64-bit |
| 指令數 | ~30 | ~100 |
| Stack | 16 bytes | 512 bytes |
| 呼叫 helper function | 無 | 有（kernel 提供白名單） |
| 跨呼叫共享狀態 | 無 | **maps**（key-value store） |
| 能掛在哪 | 只有 socket / packet filter | **kernel 任意地方**（kprobe、tracepoint、XDP、TC、LSM ...） |
| JIT | 部分 arch | 主要 arch 全有 |
| Verifier | 簡單檢查 | 複雜 symbolic execution |

**eBPF（extended BPF）誕生了**。從 2014 (kernel 3.18) 一路長到今天，每個版本都還在加東西。

到了現在，這個名字其實已經誤導：

- 它不只做 **packet filter**，更多用在 tracing 與 security
- 它幾乎不再「filter」什麼，而是收集資料、改 packet、阻擋 syscall
- 「Berkeley」也只剩歷史意義

社群慢慢改口叫它 **BPF**，不展開縮寫 — 就像 SQL 不再叫「Structured Query Language」。本教材也跟這個慣例：**寫「BPF」就指 eBPF**，需要強調舊版才寫 cBPF。

## 現代 BPF 在 kernel 哪一塊？

一張圖看清 BPF 在哪：

```
┌─────────────────────────────────────────────────────┐
│                    User Space                       │
│  ┌──────────────┐  ┌─────────────┐  ┌────────────┐  │
│  │ bpftrace.bt  │  │ libbpf .c   │  │ Go agent   │  │
│  └──────┬───────┘  └──────┬──────┘  └──────┬─────┘  │
│         │ bpf() syscall   │                │        │
└─────────┼─────────────────┼────────────────┼────────┘
          ▼                 ▼                ▼
═══════════════════════════════════════════════════════ kernel/user 邊界
┌─────────────────────────────────────────────────────┐
│                       Kernel                        │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │           BPF Subsystem                      │   │
│  │  ┌────────┐  ┌────────┐  ┌──────────────┐    │   │
│  │  │Verifier│→ │  JIT   │→ │ BPF programs │    │   │
│  │  └────────┘  └────────┘  └──────┬───────┘    │   │
│  │                                 │            │   │
│  │                          ┌──────▼─────┐      │   │
│  │                          │   Maps     │      │   │
│  │                          └────────────┘      │   │
│  └──────────────────────────────────────────────┘   │
│        ▲           ▲           ▲           ▲        │
│        │           │           │           │        │
│   ┌────┴────┐ ┌────┴────┐ ┌────┴────┐ ┌────┴────┐   │
│   │ kprobe  │ │tracepnt │ │   XDP   │ │   LSM   │   │
│   └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘   │
│        ▼           ▼           ▼           ▼        │
│  ┌─────────────────────────────────────────────┐    │
│  │  Kernel 各子系統（network、scheduler、fs ...）│    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

關鍵看這幾件事：

1. BPF 程式本身**跑在 kernel 裡**，但它**從 user space 載入**。
2. 載入要先過 **verifier** 那關 — 證明你的 code 安全，否則直接拒絕。
3. 通過後 **JIT** 編譯成 native CPU 指令（不是解釋執行）。
4. 程式會 **attach** 到某個事件（kprobe、tracepoint、XDP ...），事件觸發時 BPF code 就跑。
5. **Maps** 是 BPF 程式存狀態、跟 user space 交換資料的唯一管道。

這五件事你只要記住，後面整個 Part 2 就是把它們各自展開。

## BPF 的四根支柱

整個 BPF 系統可以歸納成四件事：

| 支柱 | 是什麼 | 你會在哪一章學 |
|---|---|---|
| **Instruction set** | 64-bit RISC-like ISA | Ch 6 |
| **Verifier** | 載入時的靜態安全檢查 | Ch 9 |
| **Maps** | 跨呼叫 / 跨 user-kernel 共享狀態 | Ch 8 |
| **Helpers** | kernel 提供的 BPF 可呼叫函式白名單 | 散在 Ch 7、11、13 |

漏掉任何一根，BPF 都不成立。沒 verifier 就不安全；沒 maps 程式無狀態無用；沒 helpers 你只能算 register 沒辦法做事。

## 三大應用面

現代 BPF 用在三個方向，剛好對應教材的 Part 4–6：

### 1. Observability（可觀測性）— 看見以前看不見的東西

最大的應用面，由 Brendan Gregg 在 Netflix 推紅。經典工具：

- `execsnoop`：誰在啟動 process
- `opensnoop`：誰在開哪個檔
- `biolatency`：磁碟 IO 延遲分布
- `tcpconnect`：誰在發起 TCP 連線
- `runqlat`：scheduler run queue 延遲

過去這些事要嘛裝 SystemTap（好難用）、要嘛 strace（會讓目標慢 10 倍）、要嘛 dtrace（Solaris/macOS 才有）。**BPF 出來之後這些工具開銷低到可以開在 production**，這是革命性的。

代表產品：**Pixie**（Kubernetes 自動 observability）、**Parca**（continuous profiling）、**Hubble**（Cilium 的 network observability）。

### 2. Networking（網路）— Cilium 的時代

XDP（eXpress Data Path）讓 packet 在還沒進 kernel network stack 時就被 BPF 處理。能在 commodity 硬體上做到 **百萬 PPS 的 DDoS mitigation**。

代表產品：**Cilium**（Kubernetes CNI，把 iptables 全換成 BPF）、**Cloudflare** 的 DDoS 防護、**Meta** 的 L4 LB Katran。

### 3. Security（安全）— 從監控到阻擋

最古老的安全 BPF 應用是 **seccomp-bpf**（你用的 Docker / Chrome sandbox 全靠它）。新世代是 **BPF LSM** — 用 BPF 寫 kernel 級安全鉤子，能即時阻擋可疑行為。

代表產品：**Falco**（runtime threat detection）、**Tetragon**（Cilium 的 security 兄弟）。

## 為什麼不用 kernel module？

很多人第一個問題是：能進 kernel 跑 code，那寫 kernel module 不就好了？對比一下就知道：

| | Kernel Module | BPF |
|---|---|---|
| 安全性 | **無**，crash kernel 整台死 | Verifier 保證不會 panic kernel |
| 部署 | 需 `insmod`、可能要重編 | 動態載入、隨時 detach |
| 跨 kernel 版本 | 重編，可能改 source | CO-RE 一份 binary 跨版本跑 |
| 開發門檻 | 需深 kernel 知識 | 會 C / 看 manual 即可 |
| 上 production 阻力 | 高（誰敢讓你裝 module） | 低（沙盒裡跑） |
| 能力 | 100% kernel 能做的 | 約 70%，但持續長 |

**結論**：能用 BPF 解決的，沒人會想寫 kernel module。BPF 的安全模型（verifier）、相容性（CO-RE）、部署彈性（動態載入），把寫 kernel code 的門檻從 PhD 降到資深 engineer。

## 為什麼 cloud-native 全押 BPF？

Kubernetes 把所有東西丟進 container、丟進 cgroup、丟進 namespace 之後，傳統觀測手段（看 `/proc`、`netstat`、`tcpdump` ）就破功了 — 你看到的是宿主機，看不到 pod 裡發生什麼。

BPF 剛好相反：它在 kernel 裡，**所有 namespace 的活動都看得見**。一個 BPF 程式可以同時觀察 200 個 pod 的網路流量、syscall 行為、檔案存取，一份 code、不用裝在每個 container 裡。

這個結構性優勢，是 Cilium / Falco / Pixie 等專案一致選 BPF 而不是別的方案的根本原因。

## 不是萬能：BPF 不能做什麼

別被 hype 騙了。BPF **不是**：

- **通用語言**：沒有 unbounded loop、沒有 dynamic memory、沒有任意 syscall。能寫的東西被嚴格限制。
- **取代 kernel module**：driver、檔案系統、scheduler 還是要 kernel module。
- **零成本**：每個 hook 觸發都跑 BPF code，密集的 hook（如 `kprobe` 在熱路徑上）會有可量測的 overhead。Ch 28 會討論。
- **跨 OS**：BPF 是 Linux 的東西。Windows 有 ebpf-for-windows 但不是同一隻 kernel，相容性看運氣。

## 30 年濃縮成一張時間軸

```
1992 ─── BSD Packet Filter（McCanne & Jacobson）
         ↓ 影響
1997 ─── Linux 加入 cBPF（用在 socket filter）
         ↓
2007 ─── seccomp-bpf 進 kernel（syscall 過濾）
         ↓
2011 ─── Alexei Starovoitov 開始改造 BPF
         ↓
2014 ─── eBPF 進 mainline（kernel 3.18）— 起點
         ↓
2016 ─── XDP 出現 — networking 時代開啟
         ↓
2018 ─── BTF 開始穩定 — CO-RE 的基礎
         ↓
2020 ─── BPF LSM 進 mainline — security 時代開啟
         ↓
2021 ─── eBPF Foundation 成立（Meta、Google、Isovalent、Microsoft、Netflix）
         ↓
2024 ─── Linux 6.x 持續加 kfunc、struct_ops 等強化
         ↓
今天 ─── 你站在這裡
```

## 動手練習

1. 把 Ch 0 的 `tcpdump -d 'host 1.2.3.4 and port 80'` 跑一次，**真的去看** cBPF disassembly。讀不懂沒關係，感受一下「原來這就是 BPF 的祖宗」。
2. 用 `sudo bpftool prog list | head -20` 看你機器上跑了什麼，**猜每個 program 是哪個服務的**（提示：systemd-networkd 會掛 cgroup_skb；如果裝了 Docker 會看到 cgroup_device）。
3. 開瀏覽器到 <https://ebpf.io/applications/>，瀏覽一下「現實中誰在用 BPF 做什麼」，挑兩個感興趣的記下來，到 Part 4–6 學完後回頭看你能不能猜出他們怎麼實作的。

## 自我檢核

- [ ] 我能說出 cBPF 與 eBPF 的核心差異
- [ ] 我能講出 BPF 的四根支柱（ISA、verifier、maps、helpers）
- [ ] 我知道為什麼 BPF 比 kernel module 更適合 cloud-native infra
- [ ] 我知道 BPF 三大應用面（observability、networking、security）各有哪些代表產品
- [ ] 我能解釋為什麼 BPF 不是「萬能取代 kernel module」

下一章開始 Part 1，補 kernel 基礎課 — 我們先搞懂 user space 與 kernel 之間那條神祕邊界，以及為什麼歷史上要進到 kernel 跑 code 是一件危險的事。

→ [Ch 2 Kernel / User space 邊界與 syscall](./02-kernel-userspace-boundary.md)
