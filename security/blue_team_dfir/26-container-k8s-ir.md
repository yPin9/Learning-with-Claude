# Ch 26 — 容器 / K8s IR

> 目標：掌握容器化環境的 IR 獨特挑戰——ephemeral 和 immutability 如何改變鑑識邏輯、從 host 視角偵測容器逃逸、用 K8s audit log 追蹤控制平面操作、用 Falco 做 runtime 偵測，並把你在 cloud_container_security 課打過的每一招反過來看防守面留了什麼。

---

## 容器鑑識的根本挑戰

地端主機 IR 的基本假設是「artifact 是持久的」：$MFT、event log、bash history、/var/log，關機後都還在。容器把這個假設拆碎。

**兩個核心矛盾：**

**Ephemeral（短暫性）**：容器設計上就是用完即丟。Pod 被 Kubernetes 重啟、節點置換、或只是 Deployment rolling update，那個 container 就消失了。你在 container 裡的 bash history、寫入 overlay filesystem 的檔案、進程的 /proc 下的一切，統統跟著消失。CR（Container Runtime）刪掉 container 之後，overlay 的 upperdir（寫入層）通常也會被清掉。

**Immutability（不可變性）**：生產環境的最佳實踐要求容器不應該在 runtime 修改自己，應該靠 image rebuild 更新。這代表「正常的」容器應該幾乎沒有對 rootfs 的寫入——任何寫入都是 noise，也就是 signal。

這兩個特性加在一起，對 IR 的意義是：

- 你的分析視窗很短，container 消失前你必須先保全狀態（`kubectl exec` 進去拿資料、或在 host 上直接操作 overlay fs）
- 但反過來，攻擊者的持久化也更難——他改了 container 的什麼，重啟後就消失了，所以攻擊者若要持久化，必須走出容器影響 host 或控制平面
- Image 層是可以離線分析的，跟磁碟 image 類似

---

## 容器逃逸在 Host 上的樣子

你在 cloud_container_security 課學的容器逃逸手法（privileged container、hostPID、device mount、runc CVE、cgroup escape），從 host 端看有清晰的痕跡。

### 異常 syscall 特徵

容器逃逸幾乎都涉及不應該出現在正常 containerized workload 的 syscall。

| 逃逸手法 | 典型 syscall / 操作 | host 上的訊號 |
|---|---|---|
| Privileged container 寫 cgroup | `open("/proc/1/fd/...")` 、`mkdir /tmp/cgrp` + mount | container namespace 內的 mount(2) 呼叫 |
| hostPID + ptrace | `ptrace(PTRACE_ATTACH, <host-pid>)` | 對 container 外 PID 的 ptrace |
| Device mount + overwrite host binary | `mknod /dev/sda` + `debugfs` / `mount` | container 內的 mknod、/dev 下非預期 node |
| runc CVE-2019-5736 | overwrite `/proc/self/exe` via symlink | host 上的 runc binary 被 touch |
| cap_SYS_ADMIN + nsenter | `nsenter --target 1 --mount --uts --ipc --net /bin/bash` | `nsenter` process 在 host PID namespace 執行 |

### host 上找逃逸痕跡的具體操作

**1. 檢查 overlay 寫入層（在 container 還活著時）**

containerd 的 overlay fs 掛在 `/run/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/` 下，每個 container 有自己的 upperdir（寫入差異層）。即使你沒有 exec 進容器，也可以在 host 上直接讀那個目錄：

```bash
# 找 container 的 overlay upperdir（示意，路徑依 containerd 版本而異）
CONTAINER_ID="abc123"
UPPER=$(cat /run/containerd/io.containerd.runtime.v2.task/k8s.io/${CONTAINER_ID}/rootfs 2>/dev/null || \
        nsenter --mount=/proc/$(docker inspect --format '{{.State.Pid}}' $CONTAINER_ID)/ns/mnt \
        -- findmnt -n -o TARGET,SOURCE / | head -1)
```

更直接的方式：找 container 的 PID，進它的 mount namespace：

```bash
CPID=$(docker inspect --format '{{.State.Pid}}' <container_id>)
ls /proc/${CPID}/root/  # 可以看到容器的整個 rootfs，包含 overlay 寫入層
```

**2. 檢查 /proc 下的跨 namespace 存取**

正常 container 的 process 只會看到自己 namespace 內的 /proc。如果 container 內的進程嘗試 open `/proc/1/maps`（host 的 init process），這是強烈的逃逸訊號。

auditd 規則抓這個行為：

```
# /etc/audit/rules.d/container-escape.rules
-a always,exit -F arch=b64 -S open,openat -F dir=/proc/1 -F key=container_escape_probe
-a always,exit -F arch=b64 -S mount -F key=container_mount
-a always,exit -F arch=b64 -S ptrace -k container_ptrace
```

**3. 檢查新出現的 root process**

逃逸成功後攻擊者通常會在 host namespace spawn 一個 shell。看 host 上有沒有 parent PID 是 container runtime（`containerd-shim`、`runc`）但本身跑在 host PID namespace 的 root shell：

```bash
# 找 containerd-shim 的子 process，但跑在 host mount namespace（示意）
for pid in /proc/[0-9]*/; do
    pidnum=${pid//[^0-9]/}
    mntns=$(readlink /proc/${pidnum}/ns/mnt 2>/dev/null)
    [ "$mntns" = "$HOST_MNTNS" ] && cat /proc/${pidnum}/status | grep -E '^(Name|Pid|PPid|Uid):'
done
```

### 不可變容器的寫入異常

一個設計為 read-only 的 container（`securityContext.readOnlyRootFilesystem: true`），如果你在 overlay upperdir 看到任何寫入，就是 IOC。

即使 rootfs 是 read-write，在正常 workload 裡，你不會看到在 `/tmp`、`/usr/local/bin` 新增執行檔，或修改 `/etc/crontab`。這些在 container 環境裡的 IOC 價值比在傳統主機上更高，因為 false positive 少得多。

---

## K8s Audit Log：控制平面的黑匣子

K8s audit log 記錄所有對 API server 的請求，功能類似 CloudTrail 之於 AWS。它是 K8s IR 的核心 log 源。

### 欄位結構

```json
// 示意 JSON，欄位依 Kubernetes 版本而異
{
  "kind": "Event",
  "apiVersion": "audit.k8s.io/v1",
  "level": "RequestResponse",
  "auditID": "f0c4e4b3-...",
  "stage": "ResponseComplete",
  "requestURI": "/api/v1/namespaces/production/pods/web-deployment-7d9f/exec",
  "verb": "create",
  "user": {
    "username": "system:serviceaccount:monitoring:prometheus-svc",
    "uid": "ab12cd34-...",
    "groups": ["system:serviceaccounts", "system:serviceaccounts:monitoring"]
  },
  "sourceIPs": ["10.0.1.50"],
  "userAgent": "kubectl/v1.28.0 (linux/amd64) kubernetes/abc1234",
  "objectRef": {
    "resource": "pods",
    "namespace": "production",
    "name": "web-deployment-7d9f",
    "subresource": "exec"
  },
  "responseStatus": { "code": 101 },
  "requestReceivedTimestamp": "2024-03-15T14:23:00Z",
  "stageTimestamp": "2024-03-15T14:23:00Z"
}
```

**重要欄位：**
- `verb`：HTTP 動詞對映到 K8s 操作（`get`、`list`、`create`、`delete`、`patch`、`watch`）
- `user.username`：呼叫者，可能是人（`alice`）、service account（`system:serviceaccount:ns:name`）、或系統元件（`system:node:...`）
- `objectRef`：操作的目標資源（resource type + namespace + name + subresource）
- `userAgent`：client 程式，`kubectl` vs 異常的 curl 或自訂工具

### 高 Signal 的 K8s Audit 事件

| 操作 | verb + resource | 為什麼重要 |
|---|---|---|
| exec 進 pod | `create` + `pods/exec` | 正常 production 幾乎不應該有；攻擊者 lateral movement |
| port-forward | `create` + `pods/portforward` | 可能是 C2 tunnel |
| 建立特權 pod | `create` + `pods`，且 `spec.containers[].securityContext.privileged: true` | 直接的逃逸跳板 |
| 建立 hostPID/hostNetwork pod | `create` + `pods`，且 `spec.hostPID/hostNetwork: true` | 同上 |
| 存取 secret | `get`/`list` + `secrets` | 非預期的 service account 或 user 存取憑證 |
| 修改 RBAC | `create`/`patch` + `clusterroles`/`clusterrolebindings` | 權限提升；建立後門 binding |
| 建立 daemonset | `create` + `daemonsets` | 在所有 node 上跑惡意容器 |
| 修改 admission webhook | `create`/`patch` + `mutatingwebhookconfigurations` | 劫持 API server 的 object 處理流程 |
| 讀取 `/api/v1/nodes` 全部 | `list` + `nodes` | 大規模 enumeration |

### 範例：攻擊者 exec 進 prod pod 的 audit 事件

```json
// 示意 JSON，欄位依 Kubernetes 版本而異
{
  "verb": "create",
  "user": {
    "username": "developer-alice",
    "groups": ["dev-team", "system:authenticated"]
  },
  "objectRef": {
    "resource": "pods",
    "namespace": "production",
    "name": "payment-api-6f7b9",
    "subresource": "exec"
  },
  "requestURI": "/api/v1/namespaces/production/pods/payment-api-6f7b9/exec?command=bash&stdin=true&stdout=true&tty=true",
  "sourceIPs": ["10.0.1.50"],
  "responseStatus": { "code": 101 }
}
```

`subresource: exec` + `command=bash` + `tty=true` 是互動式 shell。developer-alice 在 production 的 payment pod 裡開 bash，這個組合在合理的 production 環境幾乎不應該出現。

### 範例：透過 RBAC 提權建立 ClusterRoleBinding

```json
// T+0：攻擊者用 compromise 的 service account 建立新 ClusterRoleBinding（示意 JSON）
{
  "verb": "create",
  "user": {
    "username": "system:serviceaccount:monitoring:prometheus-svc"
  },
  "objectRef": {
    "resource": "clusterrolebindings",
    "name": "attacker-admin-binding"
  },
  "requestObject": {
    "roleRef": {
      "apiGroup": "rbac.authorization.k8s.io",
      "kind": "ClusterRole",
      "name": "cluster-admin"
    },
    "subjects": [{
      "kind": "ServiceAccount",
      "name": "attacker-sa",
      "namespace": "default"
    }]
  }
}
```

prometheus-svc 這個 service account 不應該有建立 ClusterRoleBinding 的權限——這是 RBAC 設定被誤開或已經被提權的訊號。建立的 binding 把 `cluster-admin` 賦給 `attacker-sa`。

---

## Runtime 偵測：Falco

Falco 是 CNCF 的開源 runtime security 工具，hook 進 Linux syscall 層（透過 eBPF probe 或 kernel module），對照規則集，即時產生 alert。

### Falco 規則語法結構

```yaml
# Falco 規則（規則語法依 Falco 版本而異，以下為 0.37.x 格式）
- rule: Terminal shell in container
  desc: 偵測 container 內互動式 shell 被開啟
  condition: >
    spawned_process
    and container
    and shell_procs
    and proc.tty != 0
  output: >
    Shell spawned in a container
    (user=%user.name container=%container.name image=%container.image.repository
     shell=%proc.name parent=%proc.pname cmdline=%proc.cmdline)
  priority: WARNING
  tags: [container, shell, T1059]
```

Falco condition 的核心 macro：
- `container`：事件發生在容器裡（而不是 host）
- `spawned_process`：`execve` syscall
- `proc.tty != 0`：有 tty，代表互動式
- `container.privileged`：特權容器

### 幾個有用的 Falco 規則範例

**偵測 container 內寫入 /etc**：

```yaml
# 示意規則，語法依 Falco 版本而異
- rule: Write below etc in container
  desc: container 內對 /etc 的寫入，可能是 config tamper 或 backdoor
  condition: >
    open_write
    and container
    and fd.name startswith /etc
    and not known_etc_writers
  output: >
    Write to /etc in container
    (user=%user.name container=%container.name file=%fd.name)
  priority: ERROR
```

**偵測 container 內使用 nsenter / unshare**：

```yaml
# 示意規則
- rule: Namespace escape attempt
  desc: container 內執行 nsenter 或 unshare，試圖逃逸 namespace
  condition: >
    spawned_process
    and container
    and proc.name in (nsenter, unshare)
  output: >
    Namespace escape tool run in container
    (user=%user.name container=%container.name cmdline=%proc.cmdline)
  priority: CRITICAL
  tags: [container_escape, T1611]
```

**偵測 container 內讀取 service account token**：

```yaml
# 示意規則
- rule: K8s service account token read
  desc: 進程讀取 /run/secrets/kubernetes.io/serviceaccount/token，可能準備用 SA token 呼叫 API server
  condition: >
    open_read
    and container
    and fd.name = /run/secrets/kubernetes.io/serviceaccount/token
    and not k8s_trusted_readers
  output: >
    SA token read in container
    (user=%user.name container=%container.name image=%container.image.repository proc=%proc.name)
  priority: WARNING
```

### Falco 在容器逃逸場景的實際 alert

你在 cloud_container_security 課學的 cgroup escape（dirty-pipe + write cgroup release_agent），Falco 會看到：

1. container 內 `mount` syscall（掛 cgroup tmpfs）→ `Unexpected mount in container` alert
2. container 內對 `/tmp/cgrp/release_agent` 寫入 → `Write below /tmp` + container 旗
3. Host 上突然出現 parent 是 `containerd-shim` 的 `bash` process，不在任何 cgroup → `Terminal shell in container` 但 container 欄位異常

這三個 alert 序列是逃逸發生的強訊號。

---

## Image 與供應鏈痕跡

容器逃逸之外，另一條攻擊鏈是供應鏈污染：惡意 image 推進 registry，或在 CI 過程注入惡意 layer。

IR 場景下的 image 鑑識：

**1. 分析 image history**：

```bash
docker history <image>:<tag>  # 看每一 layer 加了什麼
# 或用 dive 工具
dive <image>:<tag>
```

被污染的 layer 常見特徵：
- 有 `wget` / `curl` 下載並執行外部腳本
- ADD 或 COPY 放進奇怪的執行檔
- 修改 `ENTRYPOINT` 或 `CMD` 插入 wrapper script

**2. 比對 image digest**：

```bash
# 從 registry 拉到的 image digest
docker inspect <image> --format '{{.RepoDigests}}'
# 比對你 CI 系統記錄的應該是什麼 digest
```

Digest 不符就是 image 被調包。

**3. 用 Trivy 或 Grype 掃 known CVE**：

```bash
trivy image <image>:<tag>
```

這不是 IR 的直接工具，但能快速評估 image 的攻擊面，幫助判斷攻擊者可能利用的 entry point。

---

## K8s IR 流程

**1. 保全 audit log**

K8s audit log 通常在 control plane node 上的 `/var/log/audit/kube-apiserver-audit.log`（路徑依 distro 而異）。managed K8s（GKE、EKS、AKS）的 audit log 在各自的 log 服務裡（CloudWatch Logs、Cloud Logging、Azure Monitor）。

**立刻做**：確認 audit log 的 policy level。如果是 `None`（什麼都不記），IR 基本沒有控制平面視角。如果是 `Metadata`，只有後設資料沒有 request body，你看不到惡意 pod spec 的內容。

**2. 隔離被入侵的 workload**

```bash
# 加 NetworkPolicy 斷掉 pod 所有網路（但不 kill，保留 artifact）
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: isolate-compromised-pod
  namespace: <namespace>
spec:
  podSelector:
    matchLabels:
      app: <compromised-app>
  policyTypes: [Ingress, Egress]
EOF
# 同時給 pod 打 label，阻止 service 把流量導進來
kubectl label pod <pod-name> dfir-isolated=true
```

**3. 保全 container 狀態**

在 kill pod 之前，從 container 裡拿：
- 進程列表：`kubectl exec <pod> -- ps auxf`
- 網路連線：`kubectl exec <pod> -- ss -tnap`
- 環境變數（含 injected secret）：`kubectl exec <pod> -- env`
- 寫入層（在 node 上操作 overlay fs）

**4. 吊銷被 compromise 的 ServiceAccount**

```bash
# 刪掉舊 secret（K8s 1.24 以前 SA 會有自動建立的 secret）
kubectl delete secret <sa-token-secret> -n <namespace>
# 對 IAM / OIDC 綁定的 SA，在雲端 IAM 側撤銷
```

---

## 踩雷紀錄

1. **Pod 重啟後 artifact 消失**：最常見的錯誤。節點記憶體壓力或 health check 失敗導致 pod 重啟，overlay upperdir 被清掉，你什麼都沒了。IR 一開始就要鎖住：刪掉 pod 的 deployment（不是 pod 本身），讓它不被重啟。

2. **Audit log policy 設錯**：很多 K8s 叢集為了省資源把 audit policy 設成只記 `Metadata` 或根本 `None`。沒有 `RequestResponse` level，你看不到惡意 pod 的 spec（`privileged: true` 藏在 requestObject 裡）。事前確認 audit policy。

3. **Falco alert 太吵被忽略**：預設的 Falco 規則 false positive 很高（正常的 CI/CD pipeline 會觸發 "Read sensitive file" 等規則）。沒有 tune 過規則的 Falco 很容易讓 SOC 陷入 alert fatigue，真正的 exploit alert 被埋在噪音裡。

4. **忘記 service account token 是持久化的**：攻擊者從 container 裡讀到 SA token，用 token 呼叫 API server，這個 token 在 SA 沒被刪除或 secret 沒輪換前一直有效——即使 pod 被殺掉。只殺 pod 不撤銷 SA token 等於沒隔離。

5. **多 namespace 搜尋漏網**：IR 習慣只查 `default` 或特定 namespace。攻擊者建立的後門 pod/SA 可能在一個你沒注意到的 namespace 裡。要全局 `kubectl get pods --all-namespaces`。

---

## 進階延伸

- **Tetragon**（Cilium 的 eBPF security observability）：比 Falco 更深，能在 syscall 層做 policy enforcement，直接 kill process 而不只是 alert。
- **K8s admission controllers**：OPA/Gatekeeper 或 Kyverno 在 object 創建時就攔截，是 Falco（runtime）的前置防線。
- **CNCF TAG Security — Kubernetes Threat Model**：K8s 官方的威脅模型文件，對映攻擊面到 K8s 元件，適合建構 detection coverage map。
- **KubeAudit**：分析 K8s cluster 設定的靜態分析工具，找 RBAC 過度授權、SA token automount 等常見錯誤。
- **Falco Talon**：Falco 的 response engine，可以把 Falco alert 接上自動化動作（殺進程、隔離 pod）。

---

## 本章重點整理

- 容器的 ephemeral 特性讓你必須比重啟更快保全 artifact；immutability 讓容器內的任何寫入都是高 signal。
- 容器逃逸在 host 上留下 mount 操作、跨 namespace 的 open/ptrace、以及在 host PID namespace 的異常進程。
- K8s audit log 是控制平面的黑匣子：`pods/exec`、`secrets`、`clusterrolebindings` 的操作是最高 signal 事件。
- Falco 在 syscall 層做 runtime 偵測，補 K8s audit log 看不到的 container 內行為。
- IR 流程：NetworkPolicy 隔離 → 保全 container 狀態 → 撤銷 SA token → 分析 overlay fs / audit log。
- Audit log policy 決定你能看到多少；沒有 `RequestResponse` level 就看不到 pod spec 的惡意欄位。

## 自我檢核

不看筆記，回答：

1. 容器的 ephemeral 特性對 IR 最大的衝擊是什麼？應該在什麼時機點做 overlay fs 保全？
2. K8s audit log 裡，`create` verb + `pods/exec` subresource 代表什麼操作？怎麼判斷這是可疑行為？
3. 攻擊者在 container 內讀取 service account token 後能做什麼？IR 時要怎麼斷掉這條路？
4. Falco 是在什麼層面做偵測的？它能看到 K8s audit log 看不到的什麼？
5. 為什麼「只殺掉惡意 pod」不等於完成隔離？還需要做什麼？

## 延伸閱讀

1. **Falco 官方文件 — Rules**（[falco.org/docs/rules](https://falco.org/docs/rules/)）——規則語法、內建 macro、field reference。寫 custom rule 前要讀 field list，知道哪些 container/process/network 欄位可以用。關聯：本章 runtime 偵測段落的規則範例都基於這份文件的語法。
2. **Kubernetes Audit Logging**（[kubernetes.io/docs/tasks/debug/debug-cluster/audit](https://kubernetes.io/docs/tasks/debug/debug-cluster/audit/)）——K8s 官方 audit log 設定文件，涵蓋 policy level、log backend、欄位定義。IR 前先確認你的叢集 audit policy 是否足夠，這裡有標準設定範例。
3. **The DFIR Report — Kubernetes Attacks**（[thedfirreport.com](https://thedfirreport.com/)，搜尋 "kubernetes" 或 "container"）——真實案例，看職業藍隊怎麼從 K8s audit log 和 Falco alert 還原攻擊鏈，細節比本章任何範例都更接近實際。
4. **CNCF Cloud Native Security Whitepaper**（[github.com/cncf/tag-security](https://github.com/cncf/tag-security/tree/main/security-whitepaper)）——K8s 生態的完整威脅模型，對映攻擊技術到 K8s 元件，適合建 detection coverage matrix，補你在 cloud_container_security 課的攻擊視角對應的防守框架。
5. **Sysdig — Container Forensics**（[sysdig.com/blog/container-forensics](https://sysdig.com/blog)）——Sysdig 的容器鑑識系列文章，涵蓋 overlay fs 取證、/proc 取證、即時 capture（sysdig 工具）在 container IR 的使用，實用操作細節多。

---

→ [下一步：練習 C — 跨網路 + 主機關聯追 C2 beacon](./practice-c-network-host-correlation.md)
