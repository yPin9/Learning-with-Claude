# Ch 29 — Kubernetes 入門

> **目標**：能把 LLM 服務部署到 Kubernetes，理解 Pod Security Standards、Network Policy、RBAC 在 AI 服務上的應用。
>
> **環境**：Kubernetes 1.28+, Docker 24+, Ubuntu 22.04, Ollama + llama3.2:3b

---

## 為什麼需要這個？

Ch 28 把 LLM 服務鎖進加固的 Docker container 裡了。但 single server 的 Docker 有三個問題解不了：

1. **GPU 排程**：4 張 A100、5 個 LLM 服務——誰用哪張 GPU？Docker 沒有 GPU 排程器
2. **Auto-scaling**：流量暴漲自動擴、回落自動縮——Docker 做不到
3. **Rolling update**：更新 model 版本不停機——`docker stop && docker run` 會中斷服務

Kubernetes（K8s）解決這三個問題，但也帶來新的攻擊面：RBAC 設錯讓任何人都能 deploy、Pod 之間預設沒有 network 隔離、Secret 用 base64 encode 而非 encrypt。

---

## 先建立直覺

把 K8s 想成一個資料中心的作業系統。Docker container 是一個 process，K8s 是管理這些 process 的 OS。你告訴 K8s「我要 3 個 Ollama，每個要 1 張 GPU」，K8s 自動決定跑在哪些 node 上、做 health check、掛掉自動重啟、流量自動分配。

```
  Control Plane（API Server + Scheduler + Controller Manager）
                     │ kubectl / API
  ┌──────────────────▼──────────────────────────┐
  │  Worker Node 1 (GPU)    Worker Node 2 (GPU) │
  │  ┌────────────────┐    ┌────────────────┐   │
  │  │ Pod: Ollama-1  │    │ Pod: Ollama-2  │   │
  │  │ GPU: A100 #0   │    │ GPU: A100 #1   │   │
  │  └────────────────┘    └────────────────┘   │
  └─────────────────────────────────────────────┘
```

---

## 核心概念：把 Ollama 安全地部署到 K8s

### K8s 安全三件事

在寫 YAML 之前，先記住 K8s 安全的三個核心：

1. **Pod Security Standards（PSS）**：限制 Pod 能做什麼（像 Docker 的 `--cap-drop`）
2. **Network Policy**：限制 Pod 跟誰通訊（像防火牆規則）
3. **RBAC（Role-Based Access Control）**：限制誰能對 K8s API 做什麼操作

### 範例一：Ollama 的安全 K8s Deployment

```yaml
# ollama-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ollama
  namespace: ai-inference
  labels:
    app: ollama
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ollama
  template:
    metadata:
      labels:
        app: ollama
    spec:
      # --- 安全設定 ---
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
      - name: ollama
        image: ollama/ollama:latest
        securityContext:
          allowPrivilegeEscalation: false
          readOnlyRootFilesystem: true
          capabilities:
            drop: ["ALL"]
        # --- GPU 資源 ---
        resources:
          requests:
            memory: "16Gi"
            cpu: "4"
            nvidia.com/gpu: "1"
          limits:
            memory: "32Gi"
            cpu: "8"
            nvidia.com/gpu: "1"
        # --- 環境變數 ---
        env:
        - name: OLLAMA_HOST
          value: "0.0.0.0:11434"  # Pod 內部綁定（Service 控制外部存取）
        - name: OLLAMA_MODELS
          value: "/models"
        # --- Volume ---
        volumeMounts:
        - name: models
          mountPath: /models
        - name: tmp
          mountPath: /tmp
        - name: shm
          mountPath: /dev/shm
        # --- Health Check ---
        readinessProbe:
          httpGet:
            path: /api/tags
            port: 11434
          initialDelaySeconds: 30
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: /api/tags
            port: 11434
          initialDelaySeconds: 60
          periodSeconds: 30
          failureThreshold: 5
        ports:
        - containerPort: 11434
          name: http
      volumes:
      - name: models
        persistentVolumeClaim:
          claimName: ollama-models-pvc
      - name: tmp
        emptyDir:
          sizeLimit: 2Gi
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 2Gi
---
# Service：只在 cluster 內部暴露
apiVersion: v1
kind: Service
metadata:
  name: ollama-svc
  namespace: ai-inference
spec:
  type: ClusterIP  # 不用 NodePort 或 LoadBalancer
  selector:
    app: ollama
  ports:
  - port: 11434
    targetPort: 11434
    name: http
---
# PVC for model storage
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ollama-models-pvc
  namespace: ai-inference
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 100Gi
```

這個 YAML 做了哪些安全加固：

| 設定 | 對應 Docker flag | 作用 |
|------|-----------------|------|
| `runAsNonRoot: true` | `USER ollama` | 禁止用 root 執行 |
| `readOnlyRootFilesystem` | `--read-only` | rootfs 唯讀 |
| `capabilities.drop: ["ALL"]` | `--cap-drop ALL` | 丟棄所有特權 |
| `allowPrivilegeEscalation: false` | `--security-opt no-new-privileges` | 禁止提權 |
| `seccompProfile: RuntimeDefault` | `--security-opt seccomp=default` | 啟用 seccomp |
| `nvidia.com/gpu: "1"` | `--gpus '"device=0"'` | 限定 1 張 GPU |
| `Service: ClusterIP` | `-p 127.0.0.1:11434:11434` | 不對外暴露 |

---

## 底層機制：Admission Controller 如何 Enforce Pod Security Standards

K8s 的 Pod Security Standards 不是自動生效的——需要有東西去 enforce。K8s 1.25+ 內建了 Pod Security Admission（PSA）controller。

```
kubectl apply → API Server → Admission Controllers:
  1. MutatingAdmission（修改 request，如注入 sidecar）
  2. ValidatingAdmission（檢查 policy，PSA 在這裡）
     → enforce: 違反就拒絕
     → audit: 允許但寫 log
     → warn: 允許但給 warning
  → 通過 → Pod 建立到 Node 上

三個等級：
  Privileged  → 完全不限制（不推薦）
  Baseline    → 禁止 hostNetwork、hostPID、privileged container
  Restricted  → 加上 non-root、read-only rootfs、drop ALL caps、seccomp
                → 我們的 Ollama Deployment 符合這個等級
```

對 namespace 啟用 PSA enforcement：

```bash
kubectl label namespace ai-inference \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/warn=restricted
```

---

## 進一步用法：Network Policy 限制 LLM Pod 通訊

### 範例二：限制 Ollama Pod 的 ingress 和 egress

預設情況下，K8s 的 Pod 之間完全互通——任何 Pod 可以和任何 Pod 通訊。這代表如果攻擊者拿下一個 web 應用的 Pod，他可以直接存取同一 cluster 裡的 LLM inference server。

```yaml
# network-policy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ollama-network-policy
  namespace: ai-inference
spec:
  podSelector:
    matchLabels:
      app: ollama
  policyTypes:
  - Ingress
  - Egress
  # --- Ingress：只有 api-gateway 能存取 Ollama ---
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: api-gateway
    ports:
    - protocol: TCP
      port: 11434
  # --- Egress：Ollama 只能存取 DNS 和 model registry ---
  egress:
  # 允許 DNS 查詢
  - to:
    - namespaceSelector: {}
      podSelector:
        matchLabels:
          k8s-app: kube-dns
    ports:
    - protocol: UDP
      port: 53
  # 允許下載 model（HTTPS）
  - to:
    - ipBlock:
        cidr: 0.0.0.0/0
    ports:
    - protocol: TCP
      port: 443
```

效果：

```
Before NetworkPolicy:
  Any Pod ──────→ Ollama Pod ✓
  Ollama Pod ───→ Any endpoint ✓

After NetworkPolicy:
  api-gateway Pod ──→ Ollama Pod :11434 ✓
  Other Pods ───────→ Ollama Pod ✗ (blocked)
  Ollama Pod ───────→ DNS :53 ✓
  Ollama Pod ───────→ HTTPS :443 ✓ (model download)
  Ollama Pod ───────→ Other ports ✗ (blocked)
```

RBAC：限制誰可以 deploy model。建立一個 `ai-deployer` Role，只給 `get/list/update/patch` Deployment 和 `get/list` Pod/Log 的權限——不能刪 Deployment、不能碰 Secret：

```yaml
# rbac.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ai-deployer
  namespace: ai-inference
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "update", "patch"]
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list"]
```

API key 放 Secret（不是 env var 或 ConfigMap）。注意 K8s Secret 只是 base64 encode——任何有 `get secrets` 權限的人都能解碼。生產環境用 sealed-secrets 或 external-secrets 做真正的加密。

---

## 對比與取捨

| 面向 | 直接 Docker（單機） | Kubernetes |
|------|---------------------|-----------|
| **部署複雜度** | 低：一行 `docker run` | 高：需要 cluster + YAML + NVIDIA plugin |
| **GPU 排程** | 手動（你自己指定 device） | 自動（scheduler 分配 GPU node） |
| **Auto-scaling** | 無 | HPA（Horizontal Pod Autoscaler）支援 |
| **Rolling update** | 停機更新 | 零停機滾動更新 |
| **Network 隔離** | Docker network（手動設） | Network Policy（聲明式） |
| **存取控制** | Docker socket 權限 | RBAC（細粒度到 namespace/resource level） |
| **Secret 管理** | Docker secret 或 env var | K8s Secret + external-secrets / sealed-secrets |
| **監控** | 自己裝 Prometheus + Grafana | K8s 生態系整合好（metrics-server, Prometheus Operator） |
| **適用場景** | 開發、PoC、單機部署 | 生產環境、多 GPU node、需要 HA |

---

## 踩雷集錦

**1. GPU node 沒裝 NVIDIA device plugin**

K8s scheduler 看不到 GPU——你的 Pod 會一直 `Pending`。錯誤訊息是 `0/3 nodes are available: 3 Insufficient nvidia.com/gpu`。需要先部署 NVIDIA GPU Operator 或手動安裝 NVIDIA device plugin DaemonSet：

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.15.0/deployments/static/nvidia-device-plugin.yml
```

**2. Model 檔案太大，Pod 啟動超慢**

llama3.2:3b 約 2 GB，70b 模型超過 40 GB。如果每次 Pod 啟動都要從 registry 下載 model，啟動時間可能超過 10 分鐘，K8s 會判定 readiness probe 失敗並不斷重啟。解法：用 PersistentVolume 存 model，或用 init container 預先下載。

```yaml
initContainers:
- name: model-downloader
  image: ollama/ollama:latest
  command: ["ollama", "pull", "llama3.2:3b"]
  volumeMounts:
  - name: models
    mountPath: /root/.ollama
```

**3. K8s Secret 是 base64 encode 不是 encrypt**

`kubectl get secret llm-api-keys -o jsonpath='{.data.api-key}' | base64 -d` 就能拿到原文。生產環境用 sealed-secrets（`kubeseal` 加密後才 commit 到 git）或 external-secrets（從 Vault/AWS Secrets Manager 同步）。

**4. LLM Pod 的 memory request 設太低**

Ollama 載入 llama3.2:3b 需要約 4-6 GB RAM（系統 RAM，不是 VRAM）。memory request 設太低會被 OOM kill。memory limit 設到 model 大小的 2 倍以上。

**5. NetworkPolicy 需要 CNI 支援**

K8s 的 NetworkPolicy 只是一個 API spec——需要 CNI（Container Network Interface，容器網路介面）插件來 enforce。如果你用的是 Flannel，NetworkPolicy 不會生效（Flannel 不支援）。改用 Calico 或 Cilium。

---

## 進階

### GPU Scheduling 策略

K8s 的 GPU scheduling 預設是 all-or-nothing：一個 Pod 要 1 張 GPU，scheduler 找一個有空閒 GPU 的 node。你不能要 0.5 張 GPU。NVIDIA 提供兩種進階方案：

- **MIG（Multi-Instance GPU）**：A100/H100 硬體切割，真正的 memory 隔離。K8s 裡顯示為 `nvidia.com/mig-3g.20gb`
- **GPU Time-Slicing**：多個 Pod 共享一張 GPU，沒有 memory 隔離但更省錢

### OPA Gatekeeper

PSS 三個等級太粗時，用 OPA（Open Policy Agent）Gatekeeper 寫自訂 constraint（如「必須用特定 image registry」）。

---

## 動手練習

1. **本地 K8s 部署 Ollama**：用 minikube 或 kind 建一個本地 K8s cluster，部署上面的 Ollama Deployment YAML（去掉 GPU resource request）。驗證 Service 可以在 cluster 內部存取。

2. **Pod Security 測試**：對 `ai-inference` namespace 啟用 `restricted` PSA。嘗試 deploy 一個 `privileged: true` 的 Pod——應該被拒絕。

3. **Network Policy 驗證**：部署 Network Policy 後，從一個不符合 selector 的 Pod 嘗試 `curl ollama-svc:11434`——應該超時。從符合 `app: api-gateway` label 的 Pod 嘗試——應該成功。

4. **RBAC 測試**：建立 `ai-deployer` Role 和 RoleBinding，用 `kubectl auth can-i` 驗證該使用者可以 `update deployments` 但不能 `delete deployments` 或 `get secrets`。

---

## 重點整理

- K8s 解決了 Docker 單機的三個問題：GPU 排程、auto-scaling、rolling update。
- K8s 安全三件事：Pod Security Standards（限制 Pod 行為）、Network Policy（限制 Pod 通訊）、RBAC（限制人的操作權限）。
- Pod Security Standards 有三個等級：Privileged（不限制）→ Baseline（禁止危險設定）→ Restricted（嚴格加固）。AI 服務用 Restricted。
- Network Policy 限制 LLM Pod 只能被 API gateway 存取，不能直接被外部使用者碰到。
- K8s Secret 是 base64 encode 不是 encrypt——生產環境用 sealed-secrets 或 external-secrets。
- GPU node 需要 NVIDIA device plugin；model 檔案用 PersistentVolume 而非每次下載。
- NetworkPolicy 需要 CNI 支援——Flannel 不行，用 Calico 或 Cilium。

---

## 自我檢核

- 說出 Pod Security Standards 的三個等級和各自的限制。你的 LLM Deployment 應該用哪一個？
- 解釋為什麼 K8s Pod 之間預設完全互通是安全風險。Network Policy 如何解決這個問題？
- K8s Secret 的 base64 encoding 和 encryption 有什麼區別？生產環境該怎麼處理？
- 為什麼 LLM Pod 的 readiness probe 特別重要？如果 model loading 時間很長，你會怎麼設定？
- RBAC 的 Role 和 ClusterRole 有什麼區別？為什麼 AI team 不應該有 `get secrets` 的權限？

---

## 延伸閱讀

### 官方文件

- **[Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)**
  - **讀哪裡**：三個等級的完整限制列表
  - **學什麼**：理解 Baseline 和 Restricted 的差異，確認你的 Deployment 符合哪個等級

- **[NVIDIA GPU Operator for Kubernetes](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/)**
  - **讀哪裡**：Getting Started 和 GPU Feature Discovery 段落
  - **學什麼**：如何在 K8s cluster 上自動安裝和管理 GPU driver、device plugin

### 工具

- **[Kubescape](https://github.com/kubescape/kubescape)**
  - K8s 安全掃描工具，能根據 NSA/CISA、CIS Benchmark 自動檢查你的 cluster 和 YAML
  - `kubescape scan framework nsa` 一行看安全狀況

- **[Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets)**
  - 讓你安全地把 Secret 放進 git——用 cluster 的公鑰加密，只有 cluster 能解

---

→ [Ch 30 — vLLM / Ollama 部署安全](./30-vllm-ollama-security.md)
