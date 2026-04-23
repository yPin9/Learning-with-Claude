# Ch 24 — 觀測派 vs 阻擋派：Falco / Tetragon 架構

> 目標：用 Falco 與 Tetragon 兩個明星專案做案例分析，看大型 BPF 安全產品在「觀測 vs 阻擋」「user space 規則引擎 vs in-kernel enforcement」「kprobe vs LSM」這些設計選擇上的取捨。

## 兩個專案的定位

| | Falco | Tetragon |
|---|---|---|
| 主導 | Sysdig | Isovalent / Cilium |
| 起源 | 2016（早於 BPF LSM） | 2022 |
| 預設模式 | **觀測**（log 可疑事件） | **觀測 + 阻擋** |
| Hook 機制 | kprobe + tracepoint（早期 syscall + 自家 driver） | tracepoint + kprobe + **BPF LSM** |
| 規則語法 | YAML + 自家 condition DSL | YAML + TracingPolicy CRD |
| Kubernetes 整合 | 有，但非原生 | **原生**（K8s CRD） |
| 規則引擎位置 | **user space** | **in-kernel**（多數規則） |
| Open source | Apache 2.0 | Apache 2.0 |

## 兩種 paradigm

### Falco：觀測派

```
syscall ──→ kernel BPF (tracepoint) ──→ ringbuf
                                           │
                                           ▼
                              user space rule engine (Falco)
                                           │
                              比對 YAML 規則
                                           │
                              alert / log / webhook
```

**核心信念**：阻擋是危險的，先看清楚再說。所有規則在 user space 跑，BPF 只負責高效收集 event。

優點：
- 規則出錯不會影響 production（最多漏 log）
- 規則可以複雜（用 user space 完整 expression engine）
- 容易跟 SIEM / SOC pipeline 整合

缺點：
- 從 event 發生到 alert 有 latency（~10ms 級）
- 高 syscall 量場景 ringbuf 可能丟事件
- 阻擋能力靠後續手動或外部工具

### Tetragon：阻擋派

```
syscall / LSM hook ──→ kernel BPF
                          │
                          ├─→ 比對 TracingPolicy（in-kernel）
                          │     │
                          │     ▼
                          │   kill task / return -EPERM / log
                          │
                          └─→ ringbuf event 上報觀察用
```

**核心信念**：能在 kernel 阻擋就在 kernel 阻擋，不要把控制邏輯放 user space。

優點：
- 即時阻擋（攻擊行為發生那瞬間就 -EPERM）
- 不會丟 event（kernel 內 enforcement 不依賴 ringbuf 處理速度）
- 攻擊者更難 bypass（user space agent 可以被殺、kernel BPF 比較難）

缺點：
- 規則寫錯 → 系統壞掉（誤殺合法行為）
- Policy 表達力比 user space DSL 弱
- Kubernetes-centric 設計、非 K8s 環境少用

## Falco 規則範例

YAML：

```yaml
- rule: Read sensitive file untrusted
  desc: An attempt to read sensitive files (/etc/shadow, /etc/sudoers)
        from a process not in the trusted list
  condition: >
    open_read and
    sensitive_files and
    not proc.name in (trusted_readers)
  output: >
    Sensitive file opened for reading by non-trusted program
    (user=%user.name command=%proc.cmdline file=%fd.name)
  priority: WARNING
```

Falco user space agent 從 BPF ringbuf 拿到每個 syscall，跑這條 rule。觸發就送 webhook / Slack / Loki。

## Tetragon TracingPolicy 範例

Kubernetes CRD：

```yaml
apiVersion: cilium.io/v1alpha1
kind: TracingPolicy
metadata:
  name: file-monitoring
spec:
  kprobes:
  - call: "fd_install"
    syscall: false
    args:
    - index: 0
      type: int
    - index: 1
      type: "file"
    selectors:
    - matchArgs:
      - index: 1
        operator: "Prefix"
        values:
        - "/etc/shadow"
      matchActions:
      - action: Sigkill        # ← 直接殺
```

Tetragon agent 把這個 CRD 編譯成 BPF map entry — 規則的「比對 + 動作」都跑在 kernel BPF 裡。`fd_install` 那個 kprobe 一觸發、看到 path 是 `/etc/shadow`，**直接 kill 整個 process**。

## 開銷比較（粗略）

| 場景 | Falco | Tetragon |
|---|---|---|
| 100K syscall/sec 監控 | 中等（user space 處理 bottleneck） | 低 |
| 10K syscall/sec | 低 | 低 |
| 規則數量 1000+ | 在 user space 跑慢 | in-kernel 仍快（但 verifier 會抱怨複雜度） |
| Container start 阻擋 | 不能（事後 alert） | 能 |

實務上 Falco 能扛多數場景。Tetragon 的優勢在「需要極低 latency 阻擋」與「Kubernetes 原生整合」。

## 怎麼選

簡單決策樹：

```
你需要阻擋（不只觀測）？
├── 是
│   ├── Kubernetes 環境？──→ Tetragon
│   └── 非 K8s 環境 ────→ 自己用 BPF LSM 寫，或選 OpenSnitch / 其他
└── 否（只觀測）
    ├── 已有 SIEM pipeline？──→ Falco
    ├── 已用 Cilium？──────→ Tetragon（觀察模式）
    └── 都沒有 ────────→ Falco（社群大）
```

## Cilium / Tetragon 的更大畫面

Tetragon 不是孤立的 — 是 **Cilium 安全 + 觀測 stack** 的一部分：

```
┌──────────────────────────────────────────────┐
│   Cilium                                     │
│  ┌────────────────────────────────────────┐  │
│  │  Networking (CNI)                       │  │
│  │  TC / XDP BPF dataplane                  │  │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │  Hubble                                  │  │
│  │  Network observability                   │  │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │  Tetragon                                │  │
│  │  Runtime security (LSM + tracepoint)     │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

整套都是 BPF，共用 BPF 基礎設施與 daemon — 在 Cilium-based cluster 上 Tetragon 部署成本極低。

## 一個常見誤解

「Tetragon 比 Falco 新所以一定好」 — **不全然**。

兩個產品是不同 paradigm，不是迭代關係。Falco 在「觀測 + alerting + 大規模 SIEM 整合」場景仍是 production 王者。Tetragon 在「K8s 原生 + 即時阻擋」場景占優。

選工具看你的場景，不看版本號。

## 動手練習

1. **跑 Falco**：
   ```bash
   docker run --rm -i -t \
       --privileged --pid=host \
       -v /var/run/docker.sock:/host/var/run/docker.sock \
       -v /dev:/host/dev \
       -v /proc:/host/proc:ro \
       -v /boot:/host/boot:ro \
       -v /lib/modules:/host/lib/modules:ro \
       -v /usr:/host/usr:ro \
       falcosecurity/falco
   ```
   觸發 `cat /etc/shadow` 看 alert。
2. **跑 Tetragon**（需要 K8s 或 minikube）：照官方 quickstart。
3. **讀規則**：看 [falco-rules](https://github.com/falcosecurity/rules) 與 [tetragon TracingPolicies](https://tetragon.io/docs/concepts/tracing-policy/) — 學「人們在保護什麼」。

## 自我檢核

- [ ] 我能解釋觀測派與阻擋派在架構上的根本差別
- [ ] 我能說出 Falco 為什麼把規則引擎放 user space
- [ ] 我能說出 Tetragon 為什麼能即時阻擋
- [ ] 我能列出兩者各自適合的場景
- [ ] 我能在腦中畫出 Cilium / Hubble / Tetragon 的關係

Part 6 結束。下一個 Part 進「進階與生產」 — 從 ringbuf vs perfbuf 的細節差異開始，整理生產級 BPF 開發必備的細節。

→ [Ch 25 Ring buffer vs perf buffer](./25-ringbuf-vs-perfbuf.md)
