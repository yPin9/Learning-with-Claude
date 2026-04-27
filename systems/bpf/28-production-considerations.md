# Ch 28 — 效能、安全、生產部署考量

> 目標：把 BPF 從個人工具升級到生產級服務 — overhead 模型、攻擊面、capability、CI 跨 kernel 測試、kernel 升級策略、observability of BPF itself。

## Overhead 模型

每個 BPF hook 觸發時的成本可以拆成：

```
Total cost = Hook overhead + BPF code overhead + Side effect cost
```

實測量級（in nanoseconds）：

| 項目 | 開銷 |
|---|---|
| kprobe trap + entry | ~50–100 ns |
| fentry | ~10–30 ns |
| tracepoint | ~30–50 ns |
| XDP per-packet | ~20–50 ns |
| BPF_CALL helper | ~10–50 ns/each |
| `bpf_get_current_pid_tgid` | ~20 ns |
| `bpf_map_lookup_elem` (HASH) | ~20–50 ns |
| `bpf_ringbuf_reserve` + `submit` | ~100–200 ns |

對 90% 場景，BPF 開銷 **< 1% CPU**。但對熱路徑（每個 packet、每個 syscall）可能放大：

- 100K syscall/sec × 200ns = 20ms/sec = 2% 一個 CPU
- 10M packet/sec × 50ns = 500ms/sec = 5 個 CPU 滿載

**Production 上線前先量、不要猜**。`bpftool prog profile` 是工具。

## 減少 overhead 的技巧

1. **用 fentry 不要用 kprobe**（2–3× 快）
2. **early return**：filter 條件越早判越好
3. **避免熱路徑用昂貴 helper**：例如 `bpf_get_current_comm` 不便宜，能少用就少用
4. **PERCPU map 取代 HASH**：高頻計數場景無 lock 加快
5. **批次 submit**：累積一批再 ringbuf submit
6. **採樣**：不要每個 event 都收，用 `bpf_get_prandom_u32() & MASK` 採樣
7. **控制 verifier complexity**：拆 subprogram、用 tail call

## 攻擊面

BPF 跑在 ring 0，雖然有 verifier，但**仍然是攻擊面**：

歷史上 BPF 出過的 CVE：
- 2020 CVE-2020-8835：verifier 整數運算追蹤 bug，可被利用提權
- 2021 CVE-2021-3490：類似的 verifier bypass
- 2022 CVE-2022-23222：BPF helper 邊界檢查 bug
- 多個 spectre 系列利用 BPF 觸發

**BPF 是新攻擊面**。緩解：

1. **限制誰能載 BPF**：default `unprivileged_bpf_disabled = 2`（kernel 5.16+ 預設）
2. **CAP_BPF + CAP_PERFMON**：5.8+ 把 CAP_SYS_ADMIN 拆成更細，給服務最小權限
3. **prod 跑最新 LTS kernel**：BPF 的 CVE 修得快，舊 kernel 風險高
4. **限制可掛 LSM**：BPF LSM 寫錯能搞死系統

## Capability 規劃

從前 BPF 都要 root（CAP_SYS_ADMIN），現在拆細了：

| Capability | 5.8+ 用途 |
|---|---|
| `CAP_BPF` | 載入 BPF 基本能力 |
| `CAP_PERFMON` | 用 perf_event、kprobe |
| `CAP_NET_ADMIN` | 用 XDP / TC |
| `CAP_SYS_ADMIN` | 載 LSM、寫 kernel memory（罕用） |

容器 / systemd service 可以只給需要的 capability：

```yaml
# Kubernetes pod
securityContext:
  capabilities:
    add: ["BPF", "PERFMON"]
    drop: ["ALL"]
```

```ini
# systemd
[Service]
AmbientCapabilities=CAP_BPF CAP_PERFMON
```

別偷懶給整個 root。

## CI 跨 kernel 測試矩陣

CO-RE 號稱「一份 binary 跨 kernel」，但你還是要**驗證**。實務 matrix：

```yaml
# .github/workflows/test.yml
strategy:
  matrix:
    kernel:
      - 5.10   # oldest LTS we support
      - 5.15   # baseline
      - 6.1    # newer LTS
      - 6.6    # latest LTS
      - latest # mainline
```

跑測試的方式：

- **Vagrant + libvirt**：每個 kernel 一個 VM
- **vmtest**：Meta 的 [vmtest 工具](https://github.com/danobi/vmtest)，輕量化跑 BPF 測試
- **GitHub Actions + qemu**：`actions-rs/grcov` 之類社群 action

`libbpf-bootstrap` 的 CI 是好範本。

## Min kernel 矩陣

每個 BPF feature 有最低 kernel 需求。常見的：

| Feature | Min kernel |
|---|---|
| CO-RE | 4.18 |
| ringbuf | 5.8 |
| bounded loop | 5.3 |
| `bpf_loop` helper | 5.17 |
| BPF LSM | 5.7 |
| fentry / fexit | 5.5 |
| BTF kernel modules | 5.11 |
| sleepable BPF | 5.10 |
| atomic ops | 5.12 |

工具上線前列你**用了什麼 feature** + **支援多舊 kernel**，發 README 寫清楚。

## Kernel 升級策略

BPF 工具最大問題：kernel 一升級，verifier 行為可能變、fentry 列表變、struct 偏移變（CO-RE 處理 offset，但 type 改 layout 仍可能爆）。

策略：

1. **CI 跑全 kernel matrix**：每次 push 都跑
2. **Canary deploy**：先升一台，跑 24h 看有無 anomaly
3. **記下 verifier 變更**：upgrade kernel 後跑你的 test suite，verifier reject pattern 變了要加 fallback
4. **BTF mismatch 監控**：load 失敗時 alert 而不是 fail-silent

## Observability of BPF itself

BPF 也要被觀察 — 別變黑箱：

```bash
# 看當前 BPF 用多少記憶體
sudo bpftool prog list -j | jq '.[] | .bytes_memlock' | paste -sd+ | bc

# 看哪個 BPF 跑最久
sudo bpftool prog profile id <id> duration 10 cycles
```

Production export 到 Prometheus：

```
bpf_program_count{type="kprobe"}     875
bpf_program_count{type="xdp"}        12
bpf_map_memlock_bytes                123456789
bpf_program_run_count_total{name="my_tracer"} 1234567
```

`node_exporter` 的 textfile collector 配 bpftool 腳本就能達成。

## Production checklist

部署前確認：

- [ ] kernel ≥ feature 需求（README 寫清楚）
- [ ] CI 跨 kernel matrix 跑過
- [ ] 用 fentry 不用 kprobe（如果 kernel 支援）
- [ ] verifier complexity 留 buffer（不要 just-fits）
- [ ] capability 拆到最小（不給 SYS_ADMIN）
- [ ] ringbuf size 根據峰值流量算過
- [ ] BPF program memlock 算進服務的 memory budget
- [ ] 有 graceful unload（user 端死了 BPF 也清掉）
- [ ] CO-RE relocation log 在 verbose 模式下被 collect
- [ ] Loss / drop 計數器有 export
- [ ] Canary 部署 24h 觀察

## 一個常見誤解

「BPF 太新還不能上 production」 — **過時看法**。

BPF 在 Cilium / Cloudflare / Meta / Netflix 等 hyperscaler 上 production 跑了 5+ 年。**問題不是「BPF 能不能上」，而是「你有沒有做好 production 工程的功課」**。CI、canary、observability、權限管理這些 — BPF 工具跟一般 production service 一樣需要。

## 動手練習

1. **量自己工具的 overhead**：用 `bpftool prog profile` 量你前面寫的工具。
2. **設 min kernel matrix**：給你寫的工具列「我用了哪些 feature」「最低 kernel 是多少」。
3. **跑 vmtest**：clone vmtest，跑你的工具在多個 kernel 上。
4. **export Prometheus**：寫個小 script 把 `bpftool prog list` 轉 Prometheus textfile。

## 自我檢核

- [ ] 我能列出 BPF 各 hook 的開銷量級
- [ ] 我能列出 5 個減少 overhead 的技巧
- [ ] 我能解釋 CAP_BPF / CAP_PERFMON 的拆分
- [ ] 我能設計一個跨 kernel 測試 matrix
- [ ] 我能列出 production deploy checklist 至少 8 項

**Part 7 完工**。剩下最後一站 — 把全部 28 章學的東西串成一個生產級 mini agent。

→ [Final Project：Mini observability + security agent](./final-project-mini-agent.md)
