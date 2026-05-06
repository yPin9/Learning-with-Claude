# Ch 25 — Kubernetes 入門（AI 導向）

> 目標：從零理解 Kubernetes 核心概念，掌握 AI 服務部署需要的 RBAC、Secret 管理、ResourceQuota 和 NetworkPolicy，能寫出安全的 LLM 服務部署 YAML。

---

## 這章學什麼、不學什麼

沒用過 K8s 也能讀這章。學習目標是「夠用就好」——能看懂並寫出部署 AI 服務的 YAML，能在 K8s 環境做安全配置。

不需要懂的：etcd 內部結構、controller manager 原理、kube-scheduler 演算法、CNI 插件實作。

---

## 核心概念速覽

| 物件 | 用途 | AI 服務對應 |
|------|------|------------|
| Pod | 最小部署單位，一個或多個容器 | 跑 LLM API 的容器 |
| Deployment | 管理 Pod 的副本數和滾動更新 | LLM 服務的主要部署方式 |
| Service | 給 Pod 一個穩定的 IP/DNS | 讓其他服務能找到 LLM API |
| Namespace | 邏輯隔離的資源空間 | 把 AI 服務和其他服務隔開 |
| Secret | 儲存敏感資料（base64 編碼） | API key、資料庫密碼 |
| ConfigMap | 儲存非敏感設定 | 模型參數、prompt 模板 |
| ServiceAccount | Pod 的身份識別 | Agent 存取 K8s API 的身份 |

```
Kubernetes Cluster 架構（簡化）
────────────────────────────────────────────────────────
Namespace: ai-prod
┌──────────────────────────────────────────────────────┐
│                                                      │
│  Deployment: llm-api          Service: llm-api-svc   │
│  ┌─────────────────────┐      ┌──────────────────┐   │
│  │  Pod (replica 1)    │      │  ClusterIP       │   │
│  │  Container: fastapi │◄─────│  port: 8000      │   │
│  └─────────────────────┘      └──────────────────┘   │
│  ┌─────────────────────┐                             │
│  │  Pod (replica 2)    │      ConfigMap: llm-config  │
│  │  Container: fastapi │      Secret: api-keys       │
│  └─────────────────────┘                             │
│                                                      │
│  Deployment: vector-db        Service: chroma-svc    │
│  ┌─────────────────────┐      ┌──────────────────┐   │
│  │  Pod                │◄─────│  ClusterIP       │   │
│  │  Container: chroma  │      │  port: 8001      │   │
│  └─────────────────────┘      └──────────────────┘   │
│                                                      │
└──────────────────────────────────────────────────────┘
```

---

## 為什麼 AI 服務要上 K8s

不上 K8s 直接跑 Docker 也可以活，但 AI 服務有幾個特性讓 K8s 值得：

**資源隔離**：LLM 推論吃大量 GPU 和記憶體，在共享節點上如果不設 limit，一個服務可以把整台機器榨乾影響其他服務。K8s 的 `resources.limits` 強制限制。

**水平擴展**：流量高峰時需要多跑幾個 replica，K8s HPA（Horizontal Pod Autoscaler）可以根據 CPU/記憶體自動擴縮。

**Rolling Update**：更新模型或程式碼時，K8s 可以一個一個替換 Pod，不中斷服務。直接 `docker stop` 就是停機更新。

**Health Management**：Pod 掛掉 K8s 自動重啟，不用自己寫 supervisor。

---

## RBAC：Role-Based Access Control

這是 AI Agent 在 K8s 環境裡最重要的安全機制。

```
RBAC 關係鏈
────────────────────────────────────────────────
ServiceAccount（誰）
    │
    └── RoleBinding（誰有什麼角色）
            │
            └── Role（這個角色能做什麼操作）
```

### 為什麼 AI Agent 需要特別注意 RBAC

如果 LangChain Agent 有工具可以呼叫 Kubernetes API（例如查看 Pod 狀態、讀取 ConfigMap），而這個 Agent 的 ServiceAccount 權限太大，攻擊者就能透過 prompt injection 讓 Agent 做出惡意操作。

**最小權限原則**：ServiceAccount 只給它真正需要的操作。

```yaml
# 建立 ServiceAccount
apiVersion: v1
kind: ServiceAccount
metadata:
  name: llm-agent-sa
  namespace: ai-prod
automountServiceAccountToken: false  # 不自動掛載 token，需要時才手動掛

---
# Role：只允許讀取特定 ConfigMap
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: llm-agent-role
  namespace: ai-prod
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    resourceNames: ["llm-config"]  # 只能讀這一個 ConfigMap
    verbs: ["get"]                 # 只能讀，不能修改

---
# RoleBinding：把 ServiceAccount 和 Role 綁在一起
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: llm-agent-rolebinding
  namespace: ai-prod
subjects:
  - kind: ServiceAccount
    name: llm-agent-sa
    namespace: ai-prod
roleRef:
  kind: Role
  apiRef: llm-agent-role
  apiGroup: rbac.authorization.k8s.io
```

常見錯誤：`verbs: ["*"]`（所有操作）或 `resources: ["*"]`（所有資源）。這等同沒有 RBAC。

---

## Secret 管理

K8s Secret 預設是 base64 編碼，不是加密，存在 etcd 裡。任何能存取 etcd 或有足夠 RBAC 權限的人都能讀。

```bash
# 建立 Secret
kubectl create secret generic api-keys \
  --from-literal=openai-api-key=sk-proj-xxx \
  --from-literal=anthropic-api-key=sk-ant-xxx \
  --namespace ai-prod

# 查看（會顯示 base64 編碼）
kubectl get secret api-keys -o yaml -n ai-prod

# 解碼
kubectl get secret api-keys -o jsonpath='{.data.openai-api-key}' | base64 -d
```

在 Pod 裡使用 Secret 有兩種方式：

```yaml
# 方式一：以環境變數注入（較常見，但 env 可能被 proc 讀到）
env:
  - name: OPENAI_API_KEY
    valueFrom:
      secretKeyRef:
        name: api-keys
        key: openai-api-key

# 方式二：掛載成檔案（更安全，程式從檔案讀取）
volumes:
  - name: api-secrets
    secret:
      secretName: api-keys
      defaultMode: 0400  # 只有 owner 可讀

volumeMounts:
  - name: api-secrets
    mountPath: /run/secrets
    readOnly: true
```

### 整合 HashiCorp Vault（進階）

生產環境的正確做法是不把 secret 存在 K8s etcd，而是用 Vault：

```
Pod 啟動 → Vault Agent Sidecar 認證 → 從 Vault 拉 secret → 注入到 Pod
```

Vault 的 secret 有 lease（TTL），過期自動輪換，不用擔心 key 永久洩漏。

---

## 完整範例：部署 FastAPI LLM 服務

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ai-prod

---
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: llm-config
  namespace: ai-prod
data:
  MODEL_NAME: "gpt-4o-mini"
  MAX_TOKENS: "2048"
  LOG_LEVEL: "INFO"

---
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llm-api
  namespace: ai-prod
spec:
  replicas: 2
  selector:
    matchLabels:
      app: llm-api
  template:
    metadata:
      labels:
        app: llm-api
    spec:
      serviceAccountName: llm-agent-sa

      # 安全設定
      securityContext:
        runAsNonRoot: true
        runAsUser: 1001
        runAsGroup: 1001
        fsGroup: 1001
        seccompProfile:
          type: RuntimeDefault

      containers:
        - name: fastapi
          image: my-registry/llm-api:v1.2.0
          imagePullPolicy: Always

          ports:
            - containerPort: 8000

          # 資源限制：防止單一 Pod 榨乾節點
          resources:
            requests:
              cpu: "500m"
              memory: "1Gi"
            limits:
              cpu: "2000m"
              memory: "4Gi"

          # 環境變數從 ConfigMap 和 Secret 取得
          envFrom:
            - configMapRef:
                name: llm-config
          env:
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef:
                  name: api-keys
                  key: openai-api-key

          # 容器安全設定
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL

          # 需要寫入的暫存目錄
          volumeMounts:
            - name: tmp-volume
              mountPath: /tmp
            - name: cache-volume
              mountPath: /app/cache

          # 健康檢查
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30

          readinessProbe:
            httpGet:
              path: /ready
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10

      volumes:
        - name: tmp-volume
          emptyDir:
            medium: Memory
            sizeLimit: 100Mi
        - name: cache-volume
          emptyDir:
            sizeLimit: 2Gi

---
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: llm-api-svc
  namespace: ai-prod
spec:
  selector:
    app: llm-api
  ports:
    - port: 8000
      targetPort: 8000
  type: ClusterIP  # 不對外暴露，只在 cluster 內部
```

---

## AI 特定安全配置

### LimitRange / ResourceQuota

防止 LLM 服務把 GPU 或記憶體全部吃光：

```yaml
# limitrange.yaml：設定 Pod 的預設和最大資源
apiVersion: v1
kind: LimitRange
metadata:
  name: ai-prod-limits
  namespace: ai-prod
spec:
  limits:
    - type: Container
      default:
        cpu: "500m"
        memory: "1Gi"
      defaultRequest:
        cpu: "100m"
        memory: "256Mi"
      max:
        cpu: "4000m"
        memory: "16Gi"
        nvidia.com/gpu: "1"  # 最多用一張 GPU

---
# resourcequota.yaml：限制整個 namespace 的資源上限
apiVersion: v1
kind: ResourceQuota
metadata:
  name: ai-prod-quota
  namespace: ai-prod
spec:
  hard:
    requests.cpu: "10"
    requests.memory: "40Gi"
    limits.cpu: "20"
    limits.memory: "80Gi"
    requests.nvidia.com/gpu: "4"  # 整個 namespace 最多 4 張 GPU
    count/pods: "20"
```

### NetworkPolicy：限制 Pod 間通訊

預設 K8s 裡所有 Pod 可以互相通訊。NetworkPolicy 讓 LLM 服務 Pod 只能和指定的 Pod 說話：

```yaml
# networkpolicy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: llm-api-netpol
  namespace: ai-prod
spec:
  podSelector:
    matchLabels:
      app: llm-api

  policyTypes:
    - Ingress
    - Egress

  ingress:
    # 只接受來自 api-gateway 的連線
    - from:
        - podSelector:
            matchLabels:
              app: api-gateway
      ports:
        - port: 8000

  egress:
    # 只允許連到 vector-db 和 DNS
    - to:
        - podSelector:
            matchLabels:
              app: vector-db
      ports:
        - port: 8001
    - to: []  # 允許對外（OpenAI API）
      ports:
        - port: 443  # HTTPS only
    # DNS 查詢
    - to:
        - namespaceSelector: {}
      ports:
        - port: 53
          protocol: UDP
```

---

## 自我檢核

- [ ] 我能說出 Pod、Deployment、Service、Secret、ConfigMap 各自的用途
- [ ] 我知道為什麼 AI Agent 的 ServiceAccount 要最小權限
- [ ] 我能寫出 Role 和 RoleBinding 的 YAML
- [ ] 我知道 K8s Secret 預設是 base64 不是加密，以及這代表什麼風險
- [ ] 我能寫出含 securityContext 的安全 Deployment YAML
- [ ] 我知道 LimitRange 和 ResourceQuota 的差異
- [ ] 我能用 NetworkPolicy 限制 LLM 服務 Pod 的入站和出站流量
- [ ] 我知道 `readOnlyRootFilesystem: true` 搭配 `emptyDir` 的用途

Kubernetes 解決了容器編排的安全問題，但 LLM 本身的部署安全——Ollama 和 vLLM 暴露的攻擊面——還需要專門處理。

→ [Ch 26 vLLM / Ollama 部署安全](./26-vllm-ollama-security.md)
