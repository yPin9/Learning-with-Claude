# Ch 36 — Falco & Tetragon

> **目標**：理解 Falco 和 Tetragon 的架構和應用場景——runtime security detection 的設計模式、規則 DSL、Kernel 事件到安全告警的 pipeline，以及如何用它們做生產環境的威脅偵測。

## Runtime Security 的定位

```
安全防護的層次：

靜態掃描（Trivy、Snyk）：掃描 image 裡的已知 CVE
→ 能找到已知漏洞，但不能偵測 zero-day 或異常行為

運行時安全（Falco、Tetragon）：監控 container/process 的實際行為
→ 偵測「已知是惡意的行為模式」：
   - 在 container 裡執行 bash
   - 讀取 /etc/shadow
   - 建立 reverse shell 連線
   - 修改可執行檔

兩者互補，不是替代關係
```

## Falco：CNCF 的 Runtime Security 標準

Falco 是 CNCF 的 runtime security 工具，用 eBPF（或 kernel module）捕捉 kernel 事件，然後用規則引擎偵測異常行為。

**Falco 的架構**：

```
Kernel 事件（syscall）
         │
         ▼
  eBPF probe（或 kernel module）
         │
         ▼
  Falco userspace
  ├── Rules engine（評估 rule 是否匹配）
  ├── Output（stdout / file / gRPC / webhook）
  └── Alert（Slack / PagerDuty / SIEM）
```

**安裝 Falco**：

```bash
# 用 Helm 安裝（Kubernetes）
helm repo add falcosecurity https://falcosecurity.github.io/charts
helm install falco falcosecurity/falco --set driver.kind=ebpf

# 或在 host 直接安裝
curl -fsSL https://falco.org/repo/falcosecurity-packages.asc | sudo gpg --dearmor -o /usr/share/keyrings/falco-archive-keyring.gpg
sudo apt install falco
```

**Falco Rule 語法**：

```yaml
# Falco rule 範例：偵測 container 裡執行 shell

- rule: Terminal shell in container
  desc: A shell was used as the entrypoint/exec point in a container with an attached terminal
  condition: >
    spawned_process and container
    and shell_procs and proc.tty != 0
    and container_entrypoint
    and not user_known_shell_containers
  output: >
    A shell was spawned in a container with an attached terminal
    (user=%user.name user_loginuid=%user.loginuid %container.info
     shell=%proc.name parent=%proc.pname cmdline=%proc.cmdline
     terminal=%proc.tty container_id=%container.id image=%container.image.repository)
  priority: NOTICE
  tags: [container, shell, MITRE_T1059_001]
```

**Falco 的常用 macro 和 filter**：

```yaml
# 偵測讀取敏感檔案
- rule: Read sensitive file untrusted
  condition: >
    open_read and not proc.name in (falco_authorized_processes)
    and (fd.name contains /etc/shadow or fd.name contains /etc/sudoers)
  output: Sensitive file read (user=%user.name file=%fd.name)
  priority: WARNING

# 偵測 outbound 連線到非預期的 IP
- rule: Unexpected outbound connection
  condition: >
    outbound and not container
    and not proc.name in (curl, wget, apt, pip)
    and fd.rip != "8.8.8.8"
  output: Unexpected outbound connection (proc=%proc.name ip=%fd.rip)
  priority: WARNING
```

**查看 Falco 的 alert**：

```bash
# 觸發一個告警
docker exec -it <container> bash  # 這應該觸發 "shell in container" rule

# 查看 Falco 的輸出
sudo journalctl -u falco -f
# 或
sudo falco -r /etc/falco/falco_rules.yaml -p %evt.num -p %proc.name
```

## Tetragon：Cilium 的 Runtime Security

Tetragon（Isovalent/Cilium）是比 Falco 更底層的 runtime security 工具，它不只是 observe，還能在 kernel 裡 **直接 enforce**（不等 syscall 完成就 block）。

**Tetragon vs Falco 的關鍵差異**：

| 面向 | Falco | Tetragon |
|---|---|---|
| **架構** | eBPF event + userspace 規則引擎 | 完全 eBPF（kernel 層直接 enforce）|
| **Enforcement** | 不能（只能 alert）| 能（SIGKILL in kernel）|
| **Latency** | Alert 有延遲（userspace processing）| 即時（在 syscall 完成前 kill）|
| **MITRE ATT&CK** | 豐富的 rule library | 基於 TracingPolicy |
| **Kubernetes 整合** | 是 | 是（依賴 Cilium）|

**Tetragon TracingPolicy**：

```yaml
# 偵測並 kill 嘗試讀取 /etc/shadow 的 process
apiVersion: cilium.io/v1alpha1
kind: TracingPolicy
metadata:
  name: "block-shadow-read"
spec:
  kprobes:
  - call: "security_file_open"
    syscall: false
    args:
    - index: 0
      type: "file"
    selectors:
    - matchArgs:
      - index: 0
        operator: "Postfix"
        values:
        - "/etc/shadow"
      matchActions:
      - action: Sigkill  # 直接 kill process！
```

```yaml
# 偵測 privilege escalation（setuid call）
apiVersion: cilium.io/v1alpha1
kind: TracingPolicy
metadata:
  name: "monitor-setuid"
spec:
  tracepoints:
  - subsystem: "syscalls"
    event: "sys_enter_setuid"
    args:
    - index: 0
      type: "int"
    selectors:
    - matchArgs:
      - index: 0
        operator: "Equal"
        values:
        - "0"  # 嘗試 setuid(0)
      matchActions:
      - action: Post  # 記錄但不 block
        argFmt: "setuid(0) called by pid=%pid"
```

## 實際的威脅偵測場景

**場景一：Reverse shell 偵測（Falco）**

```yaml
# 偵測 bash -i 的 reverse shell 模式
- rule: Reverse shell
  condition: >
    spawned_process and
    proc.name = bash and
    proc.args contains "-i" and
    (proc.args contains ">& /dev/tcp/" or proc.args contains "0>&1")
  output: Possible reverse shell (proc=%proc.cmdline pid=%proc.pid)
  priority: CRITICAL
```

**場景二：Container escape attempt（Tetragon）**

```yaml
# 偵測試圖存取 host filesystem（/proc/1/root）
apiVersion: cilium.io/v1alpha1
kind: TracingPolicy
metadata:
  name: "container-escape-detection"
spec:
  kprobes:
  - call: "security_inode_follow_link"
    args:
    - index: 0
      type: "dentry"
    selectors:
    - matchArgs:
      - index: 0
        operator: "Prefix"
        values:
        - "/proc/1/root"
      matchNamespaces:
      - namespace: Mnt
        operator: NotIn
        values:
        - host  # 只在 container 裡的 mnt namespace
      matchActions:
      - action: Sigkill
```

## 踩雷集錦

1. **Falco 的 eBPF probe 需要 kernel 5.8+**：舊版 Falco 用 kernel module，現在建議用 eBPF probe（更安全、不需要 kernel module 簽名）

2. **Tetragon 需要 Cilium**：Tetragon 依賴 Cilium 的 BPF maps infrastructure；在非 Cilium 環境需要 standalone 部署

3. **Falco rule 的 false positive**：預設規則集有很多 false positive（例如 deployment 時執行 bash 是正常的）；需要根據你的環境調整 `user_known_shell_containers` 等例外清單

4. **Alert 的 enrichment**：raw event（pid, comm）通常不夠，需要 enrichment（pod name、namespace、user）；Falco 的 gRPC output 可以讓外部工具做 enrichment

## 動手練習

1. 安裝 Falco，故意在 container 裡執行 `bash`，確認 Falco 輸出告警；修改預設規則，讓你的特定 container 不觸發這個規則

2. 用 `falco --list` 列出所有可用的 filter field；寫一個自訂規則，偵測執行 `nc`（netcat）的 process

## 本章重點整理

- Falco 用 eBPF 捕捉 kernel 事件 + 規則引擎偵測異常；只能 alert，不能 enforce
- Tetragon 在 kernel 層直接 enforce（Sigkill），能在 syscall 完成前就 block
- Runtime security 和靜態掃描互補：一個找已知 CVE，一個偵測異常行為

## 自我檢核

- [ ] 能說出 Falco 和 Tetragon 在「enforcement」上的根本差異
- [ ] 知道 Falco rule 的基本語法（condition、output、priority）
- [ ] 能列出 2–3 個 Falco 規則適合偵測的威脅場景

→ [Ch 37 Offensive eBPF：rootkit 技術](./37-offensive-ebpf.md)
