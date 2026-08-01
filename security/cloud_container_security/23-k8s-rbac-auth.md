# Ch 23 — RBAC 與認證：ServiceAccount / token / Role

> **目標**：理解 Kubernetes 的認證機制與 RBAC 授權模型，掌握 ServiceAccount token 的掛載原理與 JWT 結構，能讀懂並撰寫 Role / RoleBinding，並識別哪些 RBAC 設定會在 Ch 26 成為提權的入口。

---

## 為什麼需要認證與授權

你在 Ch 21 學過 K8s 的核心是 API server——所有操作，不管是 `kubectl apply`、Scheduler 抓 Pod 清單、kubelet 回報節點狀態，全都是對這個 HTTP API 發請求。

問題來了：**API server 怎麼知道請求是誰發的？發請求的人又能做哪些事？**

在 Linux kernel 世界，你習慣的是 UID / GID + file permission + capability。K8s 的世界沒有這套，它自己搭了一套認證（Authentication）加授權（Authorization）的機制，而這套機制的設計選擇，直接決定了你能在 Ch 26 打哪些洞。

K8s 的授權模式預設是 **RBAC（Role-Based Access Control，基於角色的存取控制）**。RBAC 把「誰」能對「什麼資源」做「什麼動作」的三元組具象化成可寫的 YAML 物件。寫對了它是 least-privilege 的基礎；寫錯了它是最順手的提權梯子。

---

## 先建直覺

把整個 K8s cluster 想成一間公司。

- **API server** 是公司大廳的警衛台，所有人進出都要刷門禁。
- **認證（Authentication）** 是警衛確認你是「你說的那個人」——驗識別證、驗指紋、驗 JWT。
- **授權（RBAC）** 是警衛查「這個人可以進幾樓、哪些會議室」——查的是一張角色權限表。
- **ServiceAccount** 是公司給每台機器人員工核發的識別證——Pod 啟動時自動夾在身上，進大廳就出示。

關鍵點：警衛台不儲存任何帳號密碼。它只認憑證（cert）和 token。

---

## 底層機制

### K8s 的認證機制（Authentication）

K8s **沒有內建使用者資料庫**。它不存「user: alice, password: xxx」，而是把認證工作外包給幾種可插拔的機制，API server 按順序試，任一個通過就認定身分：

#### 1. X.509 用戶端憑證

你平常用 `kubectl` 能打到 cluster，是因為 `~/.kube/config` 裡放了 client certificate 和 private key，這對金鑰由 cluster CA 簽署。API server 收到請求，驗 TLS handshake 裡的 client cert，從 `CN`（Common Name）欄位取出 username，從 `O`（Organization）欄位取出 group。

```bash
# 看你的 kubeconfig 用的是哪種認證
kubectl config view --minify -o jsonpath='{.users[0].user}'
```

這是 `kubectl` 的預設路徑，人類操作者用這種方式認證。

#### 2. Bearer Token / ServiceAccount Token

HTTP header 裡帶 `Authorization: Bearer <token>`。Token 有兩種形式：

- **靜態 token**：cluster 啟動時從檔案載入，幾乎沒人在生產環境用，太危險。
- **ServiceAccount JWT**：由 API server 頒發，Pod 掛載後自動使用。這是這一章最重要的部分。

#### 3. OIDC（OpenID Connect）

企業環境整合 SSO——Okta、Azure AD、Google Workspace 都可以接。用戶先向 IdP（Identity Provider，身分提供商）取得 ID token，再把這個 JWT 當 Bearer token 送給 K8s。K8s 驗 JWT 簽名即可，不用知道你的密碼。這個路徑本課點到為止，Ch 30 的 EKS / AKS 設定會再碰到。

### ServiceAccount（SA）與 Pod 的 token 掛載

ServiceAccount（服務帳號）是 K8s 給 Pod 用的身分物件。人類用 X.509 cert 認證，Pod 用 SA token 認證。

幾個關鍵事實：

1. 每個 Namespace 建立時，K8s 自動建一個名叫 `default` 的 SA。
2. 建 Pod 時若沒指定 `spec.serviceAccountName`，Pod 自動綁到 `default` SA。
3. K8s 自動把 SA token 掛載到 Pod 內的固定路徑。

掛載路徑下有三個檔案：

```
/var/run/secrets/kubernetes.io/serviceaccount/
├── token      ← JWT，Pod 向 API server 認證用
├── ca.crt     ← cluster CA 憑證，Pod 驗 API server TLS 用
└── namespace  ← 當前 Namespace 名稱（純文字）
```

**這是攻擊金礦**。攻擊者只要拿下任何一個 Pod，第一件事就是讀這個 token，然後用它打 API server。能打到什麼，完全取決於這個 SA 被綁了哪些 RBAC 權限。

#### 進 Pod 讀 token 並解析 JWT

**本段需要真實 cluster 才能操作，以下為理論預期行為。**

```bash
# 進入 Pod 的 shell
kubectl exec -it <pod-name> -- /bin/sh

# 讀 token
cat /var/run/secrets/kubernetes.io/serviceaccount/token

# Base64 解碼 JWT payload（JWT 由三段 base64url 組成，取第二段）
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

解碼後的 payload 大致長這樣：

```json
{
  "aud": ["https://kubernetes.default.svc"],
  "exp": 1785000000,
  "iat": 1753464000,
  "iss": "https://kubernetes.default.svc",
  "kubernetes.io": {
    "namespace": "default",
    "pod": {
      "name": "my-app-7d4b9f-xxxxx",
      "uid": "a1b2c3d4-..."
    },
    "serviceaccount": {
      "name": "default",
      "uid": "e5f6a7b8-..."
    }
  },
  "nbf": 1753464000,
  "sub": "system:serviceaccount:default:default"
}
```

幾個欄位的攻擊意義：

| 欄位 | 內容 | 攻擊者關心什麼 |
|------|------|--------------|
| `sub` | `system:serviceaccount:<namespace>:<sa-name>` | 確認是哪個 SA 的 token，RBAC 查這個 |
| `exp` | Unix timestamp | K8s 1.24+ 的 projected token 有過期時間（預設 1 小時），過期後 kubelet 自動換新 |
| `aud` | token 的合法接收方 | 只有列在 `aud` 的 audience 才能驗這個 token |
| `kubernetes.io.namespace` | Namespace | 確認攻擊起點的 Namespace，決定 Role 的作用域 |

#### K8s 1.24+ 的 Projected Token

1.24 之前，SA token 是永久的 Secret 物件，偷到就永遠有效。1.24 之後改用 **projected token**（投影 token）：

- token 由 kubelet 動態生成，預設有效期 1 小時
- Pod 存活期間 kubelet 自動更新（每個週期 80% 有效期時換新）
- 沒有對應的 Secret 物件，`kubectl get secrets` 看不到它

攻擊影響：偷來的 token 最多有效 1 小時，需要快速利用或找其他持久化手段。

#### automountServiceAccountToken: false

Pod spec 或 SA 定義可以設這個欄位來停止掛載：

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: no-token-sa
  namespace: default
automountServiceAccountToken: false
```

若 token 沒掛載，`/var/run/secrets/kubernetes.io/serviceaccount/` 路徑不存在，攻擊者讀不到 token。這是最直接的防禦手段，不需要 API server 認證的 Pod 應全部設 `false`。

---

## RBAC 物件：四個物件、兩對關係

RBAC 由四個物件構成，形成兩對對稱設計：

```
Role          ←→  ClusterRole
RoleBinding   ←→  ClusterRoleBinding
```

### Role 與 ClusterRole

**Role（角色）** 定義在特定 Namespace 內有效的權限集合。它描述「能對哪些資源做哪些動作」：

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pod-reader
  namespace: default
rules:
- apiGroups: [""]        # "" 代表 core API group（pods, secrets, services 等）
  resources: ["pods"]
  verbs: ["get", "list", "watch"]
```

`rules` 是一個權限規則清單，每條規則三元組：
- **`apiGroups`**：API group，core 資源用 `""`，其他如 `apps`（Deployment）、`rbac.authorization.k8s.io`（Role/Binding）
- **`resources`**：資源種類，複數名詞（`pods`, `secrets`, `deployments`）
- **`verbs`**：動作，即 HTTP 動詞的語義化版本

**ClusterRole（叢集角色）** 與 Role 結構相同，但作用域不同：

- 可以授權 **cluster-level 資源**（`nodes`, `persistentvolumes`, `namespaces`）——這類資源沒有 Namespace，Role 管不到
- 也可以授權所有 Namespace 的 Namespace-level 資源（透過 ClusterRoleBinding）

### RoleBinding 與 ClusterRoleBinding

**RoleBinding** 把一個 Role（或 ClusterRole）綁到一組 subject（主體），生效範圍是單一 Namespace。

**ClusterRoleBinding** 把 ClusterRole 綁到 subject，生效範圍是整個 cluster。

Subject 可以是三種類型：

| kind | 說明 |
|------|------|
| `User` | 人類用戶（K8s 不管理其存在，只靠名字比對） |
| `Group` | 用戶群組（e.g., `system:masters` = cluster-admin） |
| `ServiceAccount` | Pod 的 SA，需指定 `namespace` |

一個完整的 RoleBinding：

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: read-pods
  namespace: default
subjects:
- kind: ServiceAccount
  name: readonly-sa
  namespace: default
roleRef:
  kind: Role
  name: pod-reader
  apiGroup: rbac.authorization.k8s.io
```

`roleRef` 一旦建立就**不能修改**，只能刪掉重建。

### 常用 verb 與 resource 速查

常用 verb：

| verb | 對應 HTTP | 說明 |
|------|-----------|------|
| `get` | GET（單一資源） | 讀特定名稱的資源 |
| `list` | GET（集合） | 列出某類資源的所有實例 |
| `watch` | GET + `?watch=true` | 訂閱資源變更事件 |
| `create` | POST | 建立新資源 |
| `update` | PUT | 全量更新 |
| `patch` | PATCH | 部分更新 |
| `delete` | DELETE | 刪除 |
| `*` | 全部 | 萬用符，表示所有動作 |

常用 resource：`pods`, `secrets`, `configmaps`, `services`, `deployments`, `nodes`, `namespaces`, `rolebindings`, `clusterrolebindings`

---

## 具體可跑範例

### 範例一：建 SA + Role + RoleBinding 完整流程

**本段未實測，為理論預期行為。**

```yaml
# sa-rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: readonly-sa
  namespace: default
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pod-reader
  namespace: default
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: read-pods
  namespace: default
subjects:
- kind: ServiceAccount
  name: readonly-sa
  namespace: default
roleRef:
  kind: Role
  name: pod-reader
  apiGroup: rbac.authorization.k8s.io
```

```bash
# 套用
kubectl apply -f sa-rbac.yaml

# 確認物件建立
kubectl get sa readonly-sa
kubectl get role pod-reader
kubectl get rolebinding read-pods
```

### 範例二：用 kubectl auth can-i 驗證權限

`kubectl auth can-i` 是測試「我現在能不能做 X」的標準工具，底層打 `SubjectAccessReview` API。

**本段未實測，為理論預期行為。**

```bash
# 以當前身分測試
kubectl auth can-i list pods -n default
kubectl auth can-i list secrets -n default
kubectl auth can-i list secrets -n kube-system

# 以特定 SA 身分測試（impersonate）
kubectl auth can-i list pods \
  --as=system:serviceaccount:default:readonly-sa \
  -n default
# 預期回傳：yes

kubectl auth can-i list secrets \
  --as=system:serviceaccount:default:readonly-sa \
  -n default
# 預期回傳：no（Role 沒給 secrets 權限）

# 測試建立 pod 的權限
kubectl auth can-i create pods \
  --as=system:serviceaccount:default:readonly-sa
# 預期回傳：no
```

`--as` 參數本身需要你的帳號有 `impersonate` 權限，cluster-admin 預設有。

### 範例三：危險 RBAC 壞範例 vs 好範例

這是本章最重要的對照，直接影響 Ch 26 的攻擊面。

**壞範例：cluster-admin 偽裝者**

```yaml
# BAD: 給 SA 等同 cluster-admin 的權限
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: dangerous-role
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["*"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: dangerous-binding
subjects:
- kind: ServiceAccount
  name: my-app-sa
  namespace: default
roleRef:
  kind: ClusterRole
  name: dangerous-role
  apiGroup: rbac.authorization.k8s.io
```

這個設定把 `my-app-sa` 升成等同 cluster-admin。攻擊者拿下這個 Pod 之後，讀 token 就拿到整個 cluster 的控制權。

**好範例：最小權限**

```yaml
# GOOD: 只給應用程式實際需要的權限
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: app-minimal
  namespace: production
rules:
- apiGroups: [""]
  resources: ["configmaps"]
  resourceNames: ["my-app-config"]   # 限定只能讀特定名稱
  verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: app-minimal-binding
  namespace: production
subjects:
- kind: ServiceAccount
  name: my-app-sa
  namespace: production
roleRef:
  kind: Role
  name: app-minimal
  apiGroup: rbac.authorization.k8s.io
```

`resourceNames` 把權限鎖定到特定名稱的資源，即使攻擊者拿到 token，能讀的也只有 `my-app-config` 這一個 ConfigMap。

### 範例四：automountServiceAccountToken 停用與失敗邊界

**本段未實測，為理論預期行為。**

```bash
# 確認 token 是否掛載
kubectl exec -it <pod-name> -- ls /var/run/secrets/kubernetes.io/serviceaccount/

# 若 Pod 設定了 automountServiceAccountToken: false，上面指令回傳：
# ls: /var/run/secrets/kubernetes.io/serviceaccount/: No such file or directory
# 攻擊者在這個 Pod 內拿不到任何 K8s token
```

若 Pod 啟動後 token 還沒就緒（極罕見的時序問題），同樣路徑不存在或 token 為空。實際更常見的失敗案例是 token 過期——1.24+ 的 projected token 在 cluster 時鐘偏差過大時驗證會失敗。

---

## 對比取捨表

| 物件 | 作用域 | 適用場景 | 攻擊風險 |
|------|--------|----------|---------|
| `Role` | 單一 Namespace | 大多數應用程式 SA；限制爆炸半徑 | 低——即使被提權，影響範圍侷限在一個 Namespace |
| `ClusterRole` + `RoleBinding` | 跨 Namespace 共用同一組規則，但分開生效 | 多個 Namespace 需要同樣規則時（如 monitoring SA） | 中——規則複用性高，改一個 ClusterRole 影響所有 RoleBinding |
| `ClusterRole` + `ClusterRoleBinding` | 整個 cluster | cluster-level 資源管理（Node、PV、Namespace）；絕對不要給應用程式 SA | 高——任一綁定的 SA 被拿下，整個 cluster 淪陷 |

特別注意：一個 `RoleBinding` 可以引用 `ClusterRole`（`roleRef.kind: ClusterRole`）——這讓 ClusterRole 只在那個 Namespace 生效。這是 K8s 設計的彈性，也是誤解的來源。很多人以為用 RoleBinding 就一定安全，但如果引用的 ClusterRole 本身權限很大，攻擊面依然在。

---

## 踩雷集錦

**1. `default` SA 的 RBAC 常被忽略**

很多人知道要建自訂 SA，但忘了 `default` SA 也可能被 RoleBinding 綁到危險 Role。應定期用 `kubectl get rolebindings,clusterrolebindings -A -o json` 審計所有 SA 的綁定。

**2. 把 ClusterRoleBinding 用在應用程式 SA**

最常見的 RBAC 過度授權。正確做法：應用程式 SA 幾乎永遠只需要 Role + RoleBinding，cluster-level 物件給平台團隊管理的 SA 才用 ClusterRoleBinding。

**3. `apiGroups: ["*"]` 的隱患**

`apiGroups: ["*"]` 包含 `rbac.authorization.k8s.io`，這意味著攻擊者可以透過這個 token 建立或修改 RoleBinding——相當於給了自己開後門的鑰匙。沒有任何應用程式 SA 需要操作 RBAC 物件。

**4. `resourceNames` 限制不適用於 `list`**

這是 K8s 的已知限制：`verbs: ["list"]` 配合 `resourceNames` 不起作用——`list` 不針對單一資源，K8s 不支援在 list 上限定名稱。若要限制讀取，用 `get`（讀特定名稱）而不是 `list`。

**5. `escalate` 和 `bind` verb 的被動提權**

若某個 SA 有 `escalate` verb，它可以修改自己已有存取權的 Role 的規則，把規則升高（即使這個規則超出自己原本的權限上限）。`bind` verb 允許把一個 Role 綁到 subject，即使這個 Role 的權限高於自己。這兩個 verb 是 K8s 防止 privilege escalation 的安全閥，一旦被授予，那個 SA 就等同可以無限升權。

---

## 進階延伸

### 特殊 Group

K8s 有幾個預定義 Group 值得記：

- `system:masters`：繞過 RBAC，直接取得 cluster-admin 等級——任何 X.509 cert 的 `O` 欄位設成這個，就是最高權限，沒有例外
- `system:authenticated`：所有成功通過認證的請求
- `system:unauthenticated`：匿名請求（API server 預設允許一部分匿名路徑）

如果攻擊者能自簽一張 cert 讓 K8s CA 簽署，並把 `O` 設成 `system:masters`，RBAC 對他完全無效。

### Admission Controller 與 RBAC 的分工

RBAC 只管「你能不能做這件事」，不管「這件事是否符合安全策略」。例如 RBAC 允許你建 Pod，但 OPA / Kyverno（Admission Controller）可以擋掉 `privileged: true` 的 Pod。兩道關卡缺一不可，Ch 35 深入這個主題。

### 審計 RBAC 的工具

```bash
# rbac-tool：分析 RBAC 物件
kubectl krew install rbac-tool
kubectl rbac-tool who-can create pods

# rakkess：用矩陣形式顯示當前身分的所有權限
kubectl krew install rakkess
kubectl rakkess

# audit2rbac：從 audit log 產生最小權限 RBAC 設定
# https://github.com/liggitt/audit2rbac
```

### SubjectAccessReview API

`kubectl auth can-i` 背後打的是 `SubjectAccessReview` API，也可以直接呼叫：

```bash
kubectl create -f - <<EOF
apiVersion: authorization.k8s.io/v1
kind: SubjectAccessReview
spec:
  user: "system:serviceaccount:default:readonly-sa"
  resourceAttributes:
    namespace: default
    verb: list
    resource: secrets
EOF
```

在程式中做動態權限判斷時，直接打這個 API 是標準做法。

---

## 本章重點整理

- K8s **沒有內建 user 資料庫**；認證靠 X.509 cert（人類操作者）或 SA token（Pod）
- SA token 自動掛載到 Pod 的 `/var/run/secrets/kubernetes.io/serviceaccount/token`，是攻擊者拿下 Pod 後的第一個目標
- JWT payload 的 `sub` 欄位格式為 `system:serviceaccount:<namespace>:<sa-name>`，RBAC 用這個 subject 查權限
- K8s 1.24+ 的 projected token 有過期時間，偷來的 token 最長有效 1 小時
- RBAC 四物件：Role（Namespace 內）、ClusterRole（全 cluster）、RoleBinding（綁定，Namespace 範圍）、ClusterRoleBinding（綁定，全 cluster）
- 危險 verb 組合：`create pods`、`get/list secrets`、`create rolebindings`、`bind`、`escalate`、`impersonate`、`exec pods`——這些在 Ch 26 都有對應的提權路徑
- `kubectl auth can-i` 是測試當前（或模擬身分）權限的標準工具
- 防禦原則：最小權限 + `automountServiceAccountToken: false` + `resourceNames` 限定 + 定期審計 RBAC 綁定

---

## 自我檢核

1. K8s API server 用什麼機制驗證一個 Pod 的請求者身分？為什麼 K8s 不需要自己管密碼？

2. 一個 Pod 啟動後，哪個路徑下有 token？這個 token 的 JWT `sub` 欄位格式是什麼？

3. K8s 1.24 前後 SA token 的最大差異是什麼？對攻擊者有什麼影響？

4. `Role` 和 `ClusterRole` 的作用域差異是什麼？什麼情況下用 `RoleBinding` 引用 `ClusterRole`？

5. 以下哪個權限組合讓攻擊者最能快速擴大戰場，為什麼？  
   A. `get pods` in namespace `default`  
   B. `list secrets` in namespace `kube-system`  
   C. `create rolebindings` in namespace `default`  
   D. `exec pods` in namespace `production`

6. `kubectl auth can-i create pods --as=system:serviceaccount:default:my-sa` 這條指令在測試什麼？執行這條指令本身需要什麼權限？

7. 一個 Role 設定了 `resourceNames: ["app-config"]` 和 `verbs: ["list"]`，為什麼這個限制實際上不會生效？

---

## 延伸閱讀

1. [Kubernetes 官方文件 — RBAC Authorization](https://kubernetes.io/docs/reference/access-authn-authz/rbac/)：規格的原始定義，`escalate` 和 `bind` verb 的說明在這裡找最準確

2. [Kubernetes 官方文件 — Projected Volumes（SA token）](https://kubernetes.io/docs/concepts/storage/projected-volumes/#serviceaccounttoken)：1.24+ projected token 的技術細節

3. [HackTricks Cloud — Kubernetes Role Abuse](https://cloud.hacktricks.wiki/pentesting-cloud/kubernetes-security/kubernetes-role-abuse)：危險 verb 的完整攻擊路徑整理，Ch 26 的前置閱讀

4. [RBAC Police — Risky Permissions 分析](https://github.com/PaloAltoNetworks/rbac-police)：掃描 cluster 中所有危險 RBAC 設定的工具，含判斷規則說明

5. [Kubernetes 官方文件 — Using RBAC Authorization: Privilege Escalation Prevention](https://kubernetes.io/docs/reference/access-authn-authz/rbac/#privilege-escalation-prevention-and-bootstrapping)：`escalate` 和 `bind` verb 的防護機制設計說明

---

RBAC 是 K8s 安全的地基，也是最常被誤設的地方。我們下一章先把 K8s 的另外兩根柱子補完——網路與機密——然後 Part 5 就能直接拿這章學的知識打實際的提權鏈。

→ [Ch 24 — 網路與機密：CNI / NetworkPolicy / Secret / ConfigMap](./24-k8s-networking-secrets.md)
