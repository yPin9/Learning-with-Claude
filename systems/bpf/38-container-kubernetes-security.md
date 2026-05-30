# Ch 38 — BPF 在容器與 Kubernetes 安全

> **目標**：整合 Part 6 所有安全知識，理解 eBPF 如何在容器和 Kubernetes 安全的每個層次發揮作用——syscall filtering、network policy、runtime detection、supply chain security。

## 容器安全的層次模型

```
容器安全層次（由外到內）：

L1 Host security  
  ├── 系統 hardening（SELinux/AppArmor）
  └── eBPF: BPF-LSM audit + enforcement

L2 Container runtime security
  ├── seccomp-bpf（syscall filtering）
  └── capabilities（CAP_* 限制）

L3 Network policy
  ├── Kubernetes NetworkPolicy（L3/L4）
  └── eBPF: Cilium（identity-based, L7）

L4 Runtime detection
  ├── Falco（anomaly detection）
  └── Tetragon（enforcement）

L5 Supply chain（image security）
  ├── Image signing（Cosign）
  └── Policy（OPA/Kyverno + Tetragon 驗證 exec）
```

## eBPF 在每個層次的應用

### L1：Host Security

```bash
# 啟用 BPF-LSM
sudo sed -i 's/GRUB_CMDLINE_LINUX="/GRUB_CMDLINE_LINUX="lsm=landlock,lockdown,yama,integrity,apparmor,bpf /' /etc/default/grub
sudo update-grub
sudo reboot

# 確認 BPF-LSM 啟用
cat /sys/kernel/security/lsm
# landlock,lockdown,yama,integrity,apparmor,bpf

# 載入 BPF-LSM audit program（監控所有 exec）
```

### L2：Seccomp in Kubernetes

```yaml
# Kubernetes Pod 的 seccomp profile
apiVersion: v1
kind: Pod
metadata:
  name: secure-pod
spec:
  securityContext:
    seccompProfile:
      type: RuntimeDefault  # 使用 container runtime 的預設 profile
  containers:
  - name: app
    image: nginx:alpine
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      runAsNonRoot: true
```

```bash
# 查看 Pod 的 seccomp profile
kubectl get pod secure-pod -o yaml | grep seccomp
# 查看哪個 process 有 seccomp filter
cat /proc/$(pgrep nginx)/status | grep Seccomp
# Seccomp: 2  (2 = filter mode)
```

### L3：Network Policy with Cilium

```yaml
# Cilium NetworkPolicy（L7 aware）
apiVersion: cilium.io/v1
kind: CiliumNetworkPolicy
metadata:
  name: allow-only-get-api
spec:
  endpointSelector:
    matchLabels:
      app: backend
  ingress:
  - fromEndpoints:
    - matchLabels:
        app: frontend
    toPorts:
    - ports:
      - port: "8080"
        protocol: TCP
      rules:
        http:
        - method: "GET"
          path: "/api/.*"   # 只允許 GET /api/*，拒絕 POST
```

### L4：Runtime Detection Pipeline

```
Tetragon （kernel-layer detection） →
  JSON events →
    Kafka / Elasticsearch →
      SIEM（Splunk / Datadog）→
        Alert（PagerDuty / Slack）
```

```yaml
# Tetragon + Prometheus metrics 整合
# 把 runtime security events 輸出成 Prometheus metrics
# alert on: exec_total{binary=~".*/nc|.*/bash"} > 0
```

## 完整的 Kubernetes 安全 eBPF Stack

```
Kubernetes 節點（每個節點都有）：

┌───────────────────────────────────────────────────────┐
│                     Node                              │
│                                                       │
│  seccomp-bpf profiles                                 │
│  ├── defaultAction: SCMP_ACT_ERRNO                   │
│  └── allowed syscalls: read, write, open...           │
│                                                       │
│  Cilium BPF datapath                                  │
│  ├── TC BPF（policy enforcement per pod）             │
│  ├── CGROUP_SOCK_ADDR（service load balancing）        │
│  └── Hubble（flow visibility）                        │
│                                                       │
│  Tetragon                                             │
│  ├── TracingPolicy: monitor exec/file/network         │
│  └── Enforcement: Sigkill on policy violation         │
│                                                       │
│  Falco                                                │
│  ├── Rules: shell in container, sensitive file read   │
│  └── Output: alert + SIEM integration                 │
└───────────────────────────────────────────────────────┘
```

## 實際場景：偵測並阻止 Container Escape

**攻擊場景**：攻擊者利用容器應用的漏洞，試圖逃出容器到 host：

```
攻擊步驟：
1. 利用 RCE 漏洞在 container 裡執行 code
2. 嘗試 nsenter --target 1 --mount --uts --ipc --net
   （切換到 host 的 namespace）
3. 嘗試 mount /dev/sda1 /mnt
   （mount host 的磁碟）
```

**eBPF 防禦**：

```yaml
# Tetragon policy：阻止 nsenter
apiVersion: cilium.io/v1alpha1
kind: TracingPolicy
metadata:
  name: "block-namespace-switch"
spec:
  kprobes:
  - call: "sys_setns"
    syscall: true
    selectors:
    - matchArgs:
      - index: 1
        operator: "Equal"
        values:
        - "CLONE_NEWNS"  # mount namespace switch
      matchNamespaces:
      - namespace: Mnt
        operator: NotIn
        values:
        - host
      matchActions:
      - action: Sigkill

# Falco rule：alert on nsenter
- rule: Container namespace escape attempt
  condition: >
    spawned_process and container
    and proc.name = nsenter
  output: Container escape attempt detected (pid=%proc.pid container=%container.id)
  priority: CRITICAL
```

## 面試常見問題

**Q: seccomp 和 BPF-LSM 的差別？**

A: seccomp 過濾 syscall（在 syscall 進入 kernel 時決定是否允許）；BPF-LSM 在 kernel 內部的 security hook 上執行（更細粒度，可以看到 kernel 物件如 inode、socket）。seccomp 更早執行（效能好），BPF-LSM 能做更細緻的判斷（如根據 file path 決定）。

**Q: Falco 為什麼不能像 Tetragon 那樣 enforce？**

A: Falco 的事件 pipeline 是：kernel event → userspace 規則引擎 → alert。從 event 到 alert 有幾毫秒的延遲；攻擊可以在這段時間完成。Tetragon 的 enforcement 在 kernel 層直接執行，在 syscall 完成前就能 kill process。

**Q: eBPF 的安全應用和 iptables 的比較？**

A: iptables 是 L3/L4 的 stateful packet filter；eBPF 能做到 L7 awareness（HTTP method、gRPC service）、identity-based（pod label）、runtime behavior（exec path、file access）。eBPF 的性能也更好（O(1) map lookup vs O(N) iptables rule traversal）。

## 踩雷集錦

1. **seccomp 的 whitelist 需要仔細測試**：太嚴格的 seccomp profile 會讓 container 無法啟動；使用 `SCMP_ACT_LOG` 先 audit 再 block

2. **Tetragon 的 Sigkill 是不可恢復的**：一旦 policy 觸發 Sigkill，process 立刻消失；在 production 部署之前，先用 `Post` action 監控，確認沒有 false positive

3. **Cilium + Tetragon 的依賴**：Tetragon standalone 版本已不再需要 Cilium；但若已有 Cilium，整合更緊密

## 動手練習

1. 在 kind cluster 安裝 Cilium + Falco + Tetragon，對 nginx pod 做 `kubectl exec nginx -- bash`，確認 Falco 觸發 "shell in container" 告警，Tetragon 記錄 exec event

2. 把 nginx pod 的 seccomp profile 改成 `RuntimeDefault`，用 `seccomp-tools` 分析哪些 syscall 被允許，哪些被封鎖

## 本章重點整理

- 容器安全是多層的：seccomp（syscall filtering）+ Cilium（network policy）+ Tetragon/Falco（runtime detection）
- eBPF 在每個層次都有應用，互補而非替代
- 完整的 defense-in-depth 需要：防止 container escape + network policy + runtime anomaly detection

## 自我檢核

- [ ] 能說出 seccomp、BPF-LSM、Cilium network policy、Falco/Tetragon 各自的防護層次
- [ ] 知道為什麼 Tetragon 的 enforcement 比 Falco 的 alert 更快
- [ ] 能描述一個 container escape 的攻擊場景和對應的 eBPF 防禦措施

→ [Ch 39 Tail calls 與 BPF-to-BPF calls](./39-tail-calls-bpf-to-bpf.md)
