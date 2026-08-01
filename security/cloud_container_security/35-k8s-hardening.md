# Ch 35 — K8s hardening：Pod Security Standards、Admission Control、Falco 偵測

> **目標**：把 Ch 25–29 學過的 K8s 攻擊路徑，逐一對應到防禦控制點；掌握 Pod Security Standards（PSS）三個層級的實際限制；能部署 Kyverno policy、設定 NetworkPolicy 預設拒絕、並理解 Falco 如何在 syscall 層抓住你在攻擊端做的事。

---

## 為什麼需要 K8s hardening

K8s 預設設計以「可用性優先」：Pod 可以掛 hostPath、可以跑 privileged、namespace 間網路互通、Secret 只是 base64 存進 etcd。這些預設在生產環境是連環災難。

問題不在 K8s 寫爛了，而在它把「要不要鎖」的決定全部交給你。你在 Ch 26–29 學的每一條攻擊路徑——RBAC 提權、Pod 逃逸到 host、惡意 admission webhook——全部對應到一個你沒開的防禦控制點。

這章把那張地圖補完。

---

## 攻擊回顧 → 防禦地圖

我們先把已經學過的攻擊向量和對應的防禦控制點並排：

| 攻擊章節 | 攻擊技術 | 攻擊效果 | 對應防禦控制點 |
|---------|---------|---------|--------------|
| Ch 27 | hostPath 掛載 host fs | 讀寫節點任意檔案、逃逸 | Pod Security Standards (restricted) + Kyverno deny-hostpath |
| Ch 27 | privileged container | 取得完整 host capabilities | PSS (restricted) + Kyverno deny-privileged |
| Ch 27 | hostPID + nsenter | 進入 host PID namespace | PSS (baseline) 禁止 hostPID |
| Ch 26 | `get secrets` + SA token 竊取 | 取得高權限 token | RBAC 最小化：禁 `*` verb，SA 只綁需要的 namespace |
| Ch 26 | `bind` / `escalate` verb | 提升自己到 cluster-admin | RBAC 稽核 + Kyverno 阻擋危險 ClusterRoleBinding |
| Ch 29 | 惡意 admission webhook | 攔截並修改所有 Pod spec | Webhook TLS 驗證 + OPA/Kyverno 嚴格 policy |
| Ch 24 | Pod 間不受限網路 | 橫向移動 | NetworkPolicy 預設拒絕 + CNI 強制執行 |
| 全部 | 事後無記錄 | 無法 IR 溯源 | Falco runtime 偵測 + audit log |

這張表就是本章的學習地圖。每一行對應後面一個小節。

---

## 先建直覺

把 K8s cluster 想成一棟辦公大樓，Pod 是每個工作人員：

```
[外部]
  │
  ▼
[大門警衛：Admission Controller]  ← 進門前審查，Kyverno / OPA 在這攔
  │
  ▼
[安全規範：Pod Security Standards]  ← 你帶的工具包（掛載、特權）被管制
  │
  ├──[辦公室 A]──[辦公室 B]──[辦公室 C]
  │     Pod           Pod         Pod
  │
  ├──[走廊通行證：NetworkPolicy]  ← 沒有通行證，辦公室間不能串門
  │
  └──[監視器：Falco]  ← 在每個房間裝，記錄誰在 shell 裡敲什麼
```

這三層加上 RBAC 最小化，構成縱深防禦。任何一層被繞，其他層還在。

---

## Pod Security Standards（PSS）

### 三個層級

PSS 在 K8s 1.25 正式 GA，取代了被廢棄的 PodSecurityPolicy（PSP）。它定義三個層級，每個層級是一組允許或禁止的 Pod spec 欄位：

| 層級 | 設計意圖 | 允許 | 禁止 |
|------|---------|------|------|
| **privileged** | 完全不限制，給基礎設施元件 | 一切 | 無 |
| **baseline** | 防止已知的嚴重逃逸，但維持可用性 | 大部分一般 workload | `privileged: true`、`hostPID/IPC/Network: true`、危險 capabilities（NET_ADMIN 等） |
| **restricted** | 目前最嚴格的 hardening 基準 | 非 root 跑、read-only root fs | 所有 baseline 禁的 + hostPath、所有 capabilities 預設 drop、runAsNonRoot 強制 |

對安全工程師來說，目標是讓應用 namespace 全跑 `restricted`，基礎設施元件（如 Falco DaemonSet）按需要設 `privileged` 豁免。

### 實作方式：namespace label

PSS 透過 namespace annotation 啟動，不需要安裝任何額外元件：

```yaml
# 對 namespace 貼標籤來啟用 PSS
apiVersion: v1
kind: Namespace
metadata:
  name: production
  labels:
    # enforce: 拒絕違規的 Pod 建立
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/enforce-version: v1.29
    # warn: 建立成功但回傳警告
    pod-security.kubernetes.io/warn: restricted
    pod-security.kubernetes.io/warn-version: v1.29
    # audit: 建立成功，記錄到 audit log
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/audit-version: v1.29
```

三種模式意義如下：
- `enforce`：違規直接拒絕，Pod 建不起來，回傳 403。這是真正的防線。
- `warn`：建立成功，但 kubectl 輸出會顯示警告。適合做遷移期的緩衝。
- `audit`：建立成功，在 K8s audit log 留下記錄。配合 SIEM 可以摸清現況再開始限制。

### 違反 restricted 的失敗範例

**本段未實測，為理論預期行為。** 自驗方法：在已貼 `restricted` 標籤的 namespace 執行 `kubectl apply -f bad-pod.yaml`。

先建一個違規的 Pod：

```yaml
# bad-pod.yaml — 在 restricted namespace 會被拒絕
apiVersion: v1
kind: Pod
metadata:
  name: bad-pod
  namespace: production  # 已設 enforce: restricted
spec:
  containers:
  - name: app
    image: ubuntu:22.04
    command: ["sleep", "infinity"]
    securityContext:
      privileged: true      # 違規點 1：restricted 不允許 privileged
    volumeMounts:
    - name: host-root
      mountPath: /host
  volumes:
  - name: host-root
    hostPath:
      path: /              # 違規點 2：restricted 不允許 hostPath
      type: Directory
```

預期的錯誤輸出：

```
Error from server (Forbidden): error when creating "bad-pod.yaml":
pods "bad-pod" is forbidden: violates PodSecurity "restricted:v1.29":
  privileged (container "app" must not set securityContext.privileged=true),
  hostPath volumes (volume "host-root")
```

API server 在 admission 階段就攔住，etcd 裡不會有這個物件。

符合 restricted 的正確寫法：

```yaml
# good-pod.yaml — 符合 restricted 的最小配置
apiVersion: v1
kind: Pod
metadata:
  name: good-pod
  namespace: production
spec:
  securityContext:
    runAsNonRoot: true          # restricted 要求：不能跑 root
    runAsUser: 10001            # 10001 是任意非 0 的 UID
    seccompProfile:
      type: RuntimeDefault      # restricted 要求：要指定 seccomp profile
  containers:
  - name: app
    image: ubuntu:22.04
    command: ["sleep", "infinity"]
    securityContext:
      allowPrivilegeEscalation: false   # restricted 要求
      capabilities:
        drop: ["ALL"]                   # restricted 要求：drop 所有 capabilities
```

### Warn vs Enforce vs Audit 選擇策略

遷移既有 cluster 時，直接貼 `enforce: restricted` 幾乎必定把大量 workload 打爆。建議的路徑：

1. 先貼 `audit: restricted`，收集 audit log 看有哪些 Pod 違規
2. 換成 `warn: restricted`，讓開發者在 CI 看到警告，但不擋生產
3. 最後改成 `enforce: restricted`，把違規的 Pod 逐一修正

---

## Admission Control：OPA Gatekeeper vs Kyverno

PSS 只管 Pod security context 那幾個欄位。如果你要更細的控制——比如「映像一定要從內部 registry 拉」、「所有 Deployment 一定要設 resource limit」——你需要 Admission Controller 搭配 policy engine。

### 兩者架構對比

OPA Gatekeeper 和 Kyverno 都是透過 K8s 的 ValidatingWebhookConfiguration 和 MutatingWebhookConfiguration 掛進 API server 的 admission 流程：

```
kubectl apply → API Server → [Authentication] → [Authorization]
                                                     │
                                          MutatingAdmissionWebhook
                                          (Kyverno/OPA mutate)
                                                     │
                                         ValidatingAdmissionWebhook
                                          (Kyverno/OPA validate)
                                                     │
                                              etcd 儲存
```

| 維度 | OPA Gatekeeper | Kyverno |
|------|---------------|---------|
| Policy 語言 | Rego（自己的 DSL，學習曲線陡） | 原生 YAML（貼近 K8s 風格） |
| Policy 物件 | ConstraintTemplate + Constraint | ClusterPolicy / Policy |
| Mutate 能力 | 有，但 Rego 寫起來較複雜 | 有，YAML patch 語法清楚 |
| 社群採用 | 歷史較久，Kubernetes 官方 SIG 支援 | 近年成長快，CNCF Graduated |
| 除錯 | rego playground 線上測試 | kyverno cli 本地測試 |

我們用 Kyverno 示範，因為 YAML 風格更直接。

### Kyverno 安裝（一行）

```bash
# 用 Helm 安裝 Kyverno，裝進 kyverno namespace
helm repo add kyverno https://kyverno.github.io/kyverno/
helm install kyverno kyverno/kyverno -n kyverno --create-namespace
```

### Kyverno Policy 範例

**本段未實測，為理論預期行為。** 自驗方法：`kubectl apply -f` 各 policy 後，再嘗試建立違規資源，應收到 403。

#### a. 禁止 privileged container

```yaml
# deny-privileged.yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: deny-privileged-containers
  annotations:
    policies.kyverno.io/title: Deny Privileged Containers
    policies.kyverno.io/description: >
      禁止任何 namespace 的 Pod 使用 privileged mode。
      攻擊者利用 privileged 取得 host CAP_SYS_ADMIN，可直接逃逸。
spec:
  validationFailureAction: Enforce   # Enforce = 拒絕; Audit = 只記錄
  background: true                   # 也掃描已存在的資源（不擋，只報告）
  rules:
  - name: deny-privileged
    match:
      any:
      - resources:
          kinds:
          - Pod
    validate:
      message: "privileged mode 被禁止。移除 securityContext.privileged: true。"
      pattern:
        spec:
          containers:
          - =(securityContext):      # = 前綴：欄位存在時才檢查
              =(privileged): "false | null"
          =(initContainers):
          - =(securityContext):
              =(privileged): "false | null"
```

#### b. 禁止 hostPath volume

```yaml
# deny-hostpath.yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: deny-hostpath-volumes
  annotations:
    policies.kyverno.io/title: Deny HostPath Volumes
    policies.kyverno.io/description: >
      hostPath 讓 Pod 掛載 host 任意路徑，是 Ch 27 逃逸的基礎技術。
spec:
  validationFailureAction: Enforce
  background: true
  rules:
  - name: deny-hostpath
    match:
      any:
      - resources:
          kinds:
          - Pod
    validate:
      message: "不允許使用 hostPath volume。改用 PersistentVolumeClaim 或 emptyDir。"
      deny:
        conditions:
          any:
          - key: "{{ request.object.spec.volumes[].hostPath | length(@) }}"
            operator: GreaterThan
            value: "0"
```

#### c. 要求 non-root user

```yaml
# require-non-root.yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: require-non-root-user
  annotations:
    policies.kyverno.io/title: Require Non-Root User
    policies.kyverno.io/description: >
      container 以 root（UID 0）跑會大幅提升容器逃逸後的危害。
spec:
  validationFailureAction: Enforce
  background: true
  rules:
  - name: check-runasnonroot
    match:
      any:
      - resources:
          kinds:
          - Pod
    validate:
      message: "Pod 必須設定 runAsNonRoot: true 且 runAsUser > 0。"
      pattern:
        spec:
          securityContext:
            runAsNonRoot: true
          containers:
          - securityContext:
              =(runAsUser): ">0"    # 若有設 runAsUser，必須 > 0
```

### OPA Gatekeeper 對等 ConstraintTemplate

OPA Gatekeeper 把 policy 拆成兩層：ConstraintTemplate（定義規則邏輯，用 Rego 寫）+ Constraint（實例化並指定套用範圍）。

```yaml
# 對等的 deny-privileged，OPA Gatekeeper 版
# 第一步：ConstraintTemplate 定義規則
apiVersion: templates.gatekeeper.sh/v1
kind: ConstraintTemplate
metadata:
  name: k8sdenyprivileged
spec:
  crd:
    spec:
      names:
        kind: K8sDenyPrivileged
  targets:
  - target: admission.k8s.gatekeeper.sh
    rego: |
      package k8sdenyprivileged

      violation[{"msg": msg}] {
        c := input.review.object.spec.containers[_]
        c.securityContext.privileged == true
        msg := sprintf("container %v 不允許使用 privileged mode", [c.name])
      }
---
# 第二步：Constraint 套用規則
apiVersion: constraints.gatekeeper.sh/v1beta1
kind: K8sDenyPrivileged
metadata:
  name: deny-privileged-containers
spec:
  match:
    kinds:
    - apiGroups: [""]
      kinds: ["Pod"]
```

Rego 對沒學過的人是阻力，Kyverno 的 YAML pattern 更快上手。選工具時考量團隊背景。

---

## RBAC 最小化

RBAC 最小化不是「大家都給 read-only」，而是精確地給「剛好需要的動作 × 剛好需要的資源 × 剛好需要的 namespace」。

### 危險 verb 回顧

Ch 26 詳細講過，這裡整理成快速參考：

| Verb | 危險原因 |
|------|---------|
| `*`（萬用字元） | 等同於 root，不管資源 |
| `get` on `secrets` | 可讀取所有 Secret 含 token、DB 密碼 |
| `list` on `secrets` | 配合 `get` 可枚舉 + 讀取所有 Secret |
| `escalate` | 可提升自己綁的 Role 到更高權限 |
| `bind` | 可自行把 ClusterRole 綁到任意 Subject |
| `impersonate` | 可假冒任意 user/SA，完全繞過 RBAC |
| `patch` on `deployments` | 可修改 Deployment spec，偷換映像或注入 env |
| `create` on `rolebindings` | 可自己建立新的 binding 給自己 |

### 最小化原則

三個實用原則：

1. **用 Role 不用 ClusterRole**：ClusterRole 跨全 cluster 生效，Role 只在特定 namespace。能用 Role 就不用 ClusterRole。

2. **SA 只綁所屬 namespace**：一個 namespace 的 Pod 的 SA 不應該有其他 namespace 的 secrets 存取權。

3. **定期稽核**：新增服務容易，清除遺留權限難。要把 RBAC 稽核放進 CI/CD pipeline。

### RBAC 稽核指令

```bash
# 查詢特定 ServiceAccount 能做哪些事
# system:serviceaccount:<namespace>:<sa-name> 是 SA 的 username 格式
kubectl auth can-i --list \
  --as=system:serviceaccount:default:my-sa \
  --namespace=production

# 查詢某個 SA 能不能讀 secrets
kubectl auth can-i get secrets \
  --as=system:serviceaccount:default:my-sa \
  --namespace=production

# 列出所有 ClusterRoleBinding（找 cluster-admin 綁了誰）
kubectl get clusterrolebindings \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.roleRef.name}{"\t"}{range .subjects[*]}{.kind}/{.name}{" "}{end}{"\n"}{end}' \
  | grep cluster-admin
```

---

## NetworkPolicy 預設拒絕

### 為什麼 K8s 預設全通

K8s 本身沒有網路實作——它只定義了 CNI（Container Network Interface，容器網路介面）的規格，讓 Flannel、Calico、Cilium 等插件實作。NetworkPolicy 物件存在 API server，但**執行**它靠的是 CNI 插件。

問題：**Flannel 預設不支援 NetworkPolicy**。裝了 Flannel 的 cluster，你寫的 NetworkPolicy 是死的，根本沒效。Calico 和 Cilium 完整支援。

如果沒有 NetworkPolicy，K8s 的預設行為是：所有 Pod 可以互通，不管 namespace。這讓 Ch 26–27 的橫向移動輕鬆很多——拿下一個 Pod，整個 cluster 的服務都可以直連。

### Default-deny 模板

建議每個非 kube-system namespace 都加這兩個 NetworkPolicy：

```yaml
# default-deny-all.yaml
# 拒絕所有 ingress（入站）流量
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: production      # 套用到 production namespace
spec:
  podSelector: {}            # {} = 選取 namespace 內所有 Pod
  policyTypes:
  - Ingress                  # 只管 ingress，不管 egress
  # 沒有 ingress 規則 = 拒絕所有入站
---
# 拒絕所有 egress（出站）流量
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
  namespace: production
spec:
  podSelector: {}
  policyTypes:
  - Egress
  # 注意：把 egress 也鎖起來後，DNS 查詢也會被擋
  # 需要另外開放 UDP 53 到 kube-dns
```

### 開放特定流量

在 default-deny 基礎上，只開放真正需要的：

```yaml
# allow-frontend-to-backend.yaml
# 允許 frontend Pod → backend Pod 的 8080 port
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-frontend-to-backend
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: backend            # 這條規則套用到 backend Pod（保護對象）
  policyTypes:
  - Ingress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: frontend       # 只允許帶這個 label 的 Pod 入站
    ports:
    - protocol: TCP
      port: 8080              # 只允許 8080，不是所有 port
---
# 同時，backend 要能向外查 DNS
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns-egress
  namespace: production
spec:
  podSelector: {}             # 套用到所有 Pod
  policyTypes:
  - Egress
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system  # kube-dns 在 kube-system
    ports:
    - protocol: UDP
      port: 53                # DNS
    - protocol: TCP
      port: 53                # DNS over TCP（大回應時用）
```

NetworkPolicy 規則是加法，不是覆蓋——多條規則的 `ingress.from` 取聯集。

---

## Falco runtime 偵測

前面的 PSS、Kyverno、NetworkPolicy 全是「預防」——在壞事發生前擋住。Falco 是「偵測」——壞事發生時立刻告警。

攻擊者繞過 admission control（比如在 Kyverno policy 部署前就建了惡意 Pod），或拿到合法 Pod 後在裡面搞事，Falco 是你的最後防線。

### Falco 架構

```
[Kernel]
   │
   ├── eBPF probe (推薦，不需要 kernel module)
   │   └── 掛 tracepoint / kprobe，攔截所有 syscall
   │
   └── 核心模組（kmod，需 kernel headers，某些 managed K8s 不支援）

[Falco 主程序]
   ├── 規則引擎：把 syscall event 比對 rules.yaml
   └── 輸出器：stdout / webhook / SIEM (Splunk / Elasticsearch)
```

Falco 以 DaemonSet 在每個節點跑一個 Pod，覆蓋整個 cluster。

### Falco 安裝（Helm，一行）

```bash
helm repo add falcosecurity https://falcosecurity.github.io/charts
helm install falco falcosecurity/falco \
  --namespace falco \
  --create-namespace \
  --set driver.kind=ebpf              # 使用 eBPF 模式，不需要 kmod
```

### 內建重要規則範例

Falco 內建 250+ 條規則，以下是與 K8s 攻擊最直接相關的兩條（Falco YAML rule 格式）：

**規則一：Container 內出現互動式 shell**

這是最重要的一條。`kubectl exec -it ... -- /bin/bash` 以及 Ch 17–18 容器逃逸後的 shell 都會觸發：

```yaml
# Falco rule 格式（非 K8s YAML，是 Falco 自己的 YAML）
- rule: Terminal shell in container
  desc: >
    偵測到 container 內啟動了互動式 shell。
    正常應用不需要在 runtime 開 shell；
    這幾乎必定是人為介入（攻擊者或 debug）。
  condition: >
    spawned_process
    and container
    and shell_procs             # shell_procs 是內建 macro，涵蓋 sh/bash/zsh/fish 等
    and proc.tty != 0           # tty != 0 代表有終端機，即互動式
    and not container.image.repository in (trusted_images)
  output: >
    Shell spawned in container
    (user=%user.name container=%container.name
     image=%container.image.repository:%container.image.tag
     shell=%proc.name parent=%proc.pname cmdline=%proc.cmdline)
  priority: WARNING
  tags: [container, shell, mitre_execution]
```

**規則二：讀取敏感檔案**

Ch 27 拿到 hostPath 後第一件事就是讀 `/etc/kubernetes/admin.conf` 或 `/etc/shadow`：

```yaml
- rule: Read sensitive file trusted after startup
  desc: >
    container 啟動後讀取敏感系統檔案。
    /etc/shadow 是密碼 hash，/etc/kubernetes/admin.conf 是 cluster-admin kubeconfig。
  condition: >
    open_read
    and container
    and sensitive_files          # macro，包含 /etc/shadow, /etc/kubernetes/admin.conf 等
    and not proc.name in (known_sensitive_file_readers)
    and not container.image.repository in (trusted_images)
  output: >
    Sensitive file read in container
    (user=%user.name file=%fd.name
     container=%container.name image=%container.image.repository
     proc=%proc.name cmdline=%proc.cmdline)
  priority: ERROR
  tags: [container, filesystem, mitre_credential_access]
```

### 自訂規則：偵測 kubectl exec

`kubectl exec` 在 API server 側會留 audit log，但在節點 syscall 側，Falco 可以從 container 內的 `proc.pname`（父 process 名稱）識別：

```yaml
- macro: is_kubectl_exec
  condition: >
    proc.pname in (runc, containerd-shim, containerd-shim-runc-v2)
    and proc.name in (sh, bash, dash, python3, python)
    and container

- rule: Detect kubectl exec
  desc: >
    偵測透過 kubectl exec 進入 container 的行為。
    runc / containerd-shim 作為父 process 是 kubectl exec 的特徵。
    這條規則可能有誤報（如 container 啟動時的 init script），
    需要根據環境調整 trusted_images 清單。
  condition: >
    spawned_process
    and is_kubectl_exec
    and not container.image.repository in (trusted_images)
  output: >
    Kubectl exec detected
    (user=%user.name container=%container.name
     image=%container.image.repository
     proc=%proc.name parent=%proc.pname
     cmdline=%proc.cmdline)
  priority: WARNING
  tags: [container, shell, mitre_execution, kubectl_exec]
```

---

## Secret 加密（etcd encryption at rest）

一個容易被忽略的防禦點：K8s Secret 預設只是 base64 編碼，**沒有加密**，明文存進 etcd。

驗證這個事實（有 etcd 存取權時）：

```bash
# 直接從 etcd 讀 Secret（有存取 etcd 才能做）
# 可以看到 base64 後的 data，etcdctl 直接輸出原始 bytes
ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  get /registry/secrets/default/my-secret
```

### 開啟 EncryptionConfiguration

**本段未實測，為理論預期行為。** 自驗方法：套用 EncryptionConfiguration 後，用上述 etcdctl 指令確認 Secret value 已變成亂碼（加密後的 bytes）。

```yaml
# /etc/kubernetes/encryption-config.yaml
# 放在 kube-apiserver 能讀到的路徑，然後加 --encryption-provider-config 啟動參數
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
- resources:
  - secrets                    # 只加密 secrets，其他資源不動
  providers:
  - aescbc:                    # AES-CBC 加密（較舊但廣泛支援）
      keys:
      - name: key1
        # 32 bytes base64 = 256-bit AES key
        # 產生方式: head -c 32 /dev/urandom | base64
        secret: <base64-encoded-32-byte-key>
  - identity: {}               # fallback: 不加密（讓舊資料還能讀）
```

更好的做法是用 KMS provider（如 AWS KMS 或 HashiCorp Vault），讓 key 不存在 etcd 裡，而是每次讀寫都向 KMS 請求解密：

```yaml
providers:
- kms:
    apiVersion: v2
    name: myKMSPlugin
    endpoint: unix:///var/run/kmsplugin/socket.sock   # KMS gRPC socket
    timeout: 3s                # KMS 回應超時，超時後 API server 拒絕讀 secret
- identity: {}
```

啟用後，**現有的 Secret 不會自動重新加密**。需要執行：

```bash
# 觸發所有 Secret 重寫（強制用新 provider 加密）
kubectl get secrets --all-namespaces -o json \
  | kubectl replace -f -
```

---

## 踩雷集錦

**1. PSS restricted 打爆 DaemonSet，包含 Falco 自己**

Falco 需要 `privileged: true` 或大量 capabilities 才能做 eBPF。在 `kube-system` 和 `falco` namespace 加豁免標籤，或用 Kyverno policy 的 `exclude.any.namespaces`：

```bash
# 對基礎設施 namespace 設 privileged 層級
kubectl label namespace kube-system pod-security.kubernetes.io/enforce=privileged
kubectl label namespace falco pod-security.kubernetes.io/enforce=privileged
```

不要把 `production` 等應用 namespace 加豁免——那就沒意義了。

**2. Kyverno policy apply 之前已存在的 Pod 不受影響**

Admission webhook 只攔截新的 API 請求。先部署了 policy，cluster 裡已跑的 privileged Pod 不會被殺。`background: true` 只是讓 Kyverno 定期掃描並**回報**違規（PolicyReport），不會強制刪除。要清除存量違規，需要手動或透過另外的 remediation 機制。

**3. NetworkPolicy 需要 CNI 支援，Flannel 預設不管**

最常見的慘案：你寫了 NetworkPolicy，測試卻發現完全沒效。先確認 CNI：

```bash
# 查看節點用的 CNI
ls /etc/cni/net.d/
# 如果看到 10-flannel.conflist，NetworkPolicy 不會執行
# 換成 Calico 或 Cilium
```

Calico 提供 drop-in 替換 Flannel 的選項（使用 VXLAN mode）；Cilium 功能更豐富但設定更複雜。

**4. Falco kmod 需要 kernel headers，部分 managed K8s 鎖住了**

EKS 的 Bottlerocket OS、某些 GKE node image 不允許載入自訂 kernel module。這時強制使用 `driver.kind=ebpf`。eBPF probe 需要 Linux kernel 5.8+，且 BPF 的 `CONFIG_BPF_SYSCALL=y`。確認：

```bash
uname -r                             # 確認 kernel 版本 >= 5.8
cat /boot/config-$(uname -r) | grep CONFIG_BPF_SYSCALL
```

**5. EncryptionConfiguration key rotation 沒做好導致 Secret 讀不回來**

舊 key 必須留在 providers 清單裡直到所有 Secret 都用 `kubectl replace` 重寫完。刪掉舊 key 後，用舊 key 加密的 Secret 會讀取失敗，API server 報錯。rotation 步驟：新 key 移到第一位 → 重新加密所有 Secret → 才能刪舊 key。

---

## 進階延伸

**Tetragon（Cilium 的 runtime enforcement）**

Falco 只能偵測並告警；Tetragon 更進一步，可以用 eBPF 在 syscall 層直接 kill process 或 drop 網路封包，實現 runtime enforcement。適合需要比 Falco alert 更強干預的場景。

**cert-manager + mTLS**

Pod 間通訊即使有 NetworkPolicy，流量本身沒加密。cert-manager 自動為 Pod 核發短期 TLS 憑證，配合 Istio 或 Linkerd 的 sidecar，實現 Pod 間 mTLS（雙向 TLS）。攻擊者就算做到 cluster 內網路側錄，拿到的也是加密流量。

**image signature 驗證（Kyverno + cosign）**

Ch 32 講過 cosign 簽名。Kyverno 可以在 admission 時驗證映像簽名，沒有合法簽名的 image 直接拒絕 Pod 建立。這關住了供應鏈攻擊的最後一道門——攻擊者無法把未簽名的惡意映像塞進你的 cluster：

```yaml
# Kyverno verify-image policy 示意（簡化）
spec:
  rules:
  - name: verify-image-signature
    match:
      resources:
        kinds: [Pod]
    verifyImages:
    - image: "registry.example.com/*"
      attestors:
      - count: 1
        entries:
        - keys:
            publicKeys: |-
              -----BEGIN PUBLIC KEY-----
              <cosign 公鑰>
              -----END PUBLIC KEY-----
```

---

## 本章重點整理

- **攻擊 → 防禦映射**：Ch 26–29 的每條攻擊路徑都有對應的防禦控制點，沒有一個是「防不住」的，只是「有沒有設」。
- **PSS 三層**：`privileged`（全開）/ `baseline`（擋已知嚴重逃逸）/ `restricted`（最嚴格），以 namespace label 套用，三種模式 enforce/warn/audit 對應不同遷移階段。
- **Kyverno 補 PSS 不管的欄位**：映像來源、resource limits、label 規範等都可以用 ClusterPolicy 強制，且 YAML 風格比 OPA Rego 更好上手。
- **NetworkPolicy 預設拒絕**：先 default-deny ingress + egress，再按需開放。但要先確認 CNI 支援（Flannel 不支援）。
- **Falco 是 runtime 的眼睛**：eBPF hook 在 syscall 層，admission control 前的存量 Pod 也能監控；自訂 rule 可以針對你的 cluster 行為調整。
- **etcd 加密**：Secret 預設 base64 明文，EncryptionConfiguration 補上這個洞；KMS provider 更好，因為 key 不存在 cluster 裡。

---

## 自我檢核

1. PSS 的 `restricted` 層級禁止哪三件事是 Ch 27 逃逸的直接前提？
2. Kyverno 的 `validationFailureAction: Enforce` vs `Audit` 的差異是什麼？對已存在的 Pod 有沒有效？
3. 你在 namespace 套了 `default-deny-ingress` NetworkPolicy，但 Pod 還是可以被外部存取。可能原因是什麼？（提示：CNI）
4. Falco 的 rule 裡 `spawned_process and container and proc.tty != 0` 這三個條件各自篩選了什麼？
5. K8s Secret 沒開 EncryptionConfiguration 時，攻擊者取得 etcd 存取權後需要做什麼才能看到明文？

---

## 延伸閱讀

- [Kubernetes Pod Security Standards 官方文件](https://kubernetes.io/docs/concepts/security/pod-security-standards/)：三個層級的完整 spec，包含每個欄位的精確定義。
- [Kyverno 官方 Policy Library](https://kyverno.io/policies/)：300+ 個現成 policy，涵蓋 CIS Kubernetes Benchmark 所有控制點，可以直接套用。
- [Falco Rules 文件與內建規則清單](https://falco.org/docs/rules/default-macros/)：所有內建 macro 和 rule，是自訂 rule 的起點。
- [NCC Group: Kubernetes Hardening Guide](https://www.nccgroup.com/us/research-blog/kubernetes-threat-modelling/)：業界實戰 hardening 指南，攻防對照視角。
- [NSA/CISA Kubernetes Hardening Guidance](https://media.defense.gov/2022/Aug/29/2003066362/-1/-1/0/CTR_KUBERNETES_HARDENING_GUIDANCE_1.2_20220829.PDF)：美國政府發布的 K8s hardening 標準，CIS Benchmark 的互補文件。

---

防禦做完了，下一步是偵測工程——攻擊發生了你怎麼知道？CloudTrail 的 event 要怎麼變成可追蹤的 alert？

→ [Ch 36 雲端偵測工程：CloudTrail→SIEM / GuardDuty / 雲端 IR](./36-cloud-detection.md)
