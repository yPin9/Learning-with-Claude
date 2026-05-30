# Ch 37 — Offensive eBPF：rootkit 技術與偵測

> **目標**：理解攻擊者如何濫用 eBPF 做 rootkit——network backdoor、syscall output manipulation、credential theft——以及如何偵測 malicious eBPF programs，讓你設計更好的防禦工具。

> **重要聲明**：本章內容用於教育目的和防禦性安全研究。了解攻擊技術是設計有效防禦的前提。這些技術只能在你有授權的測試環境中使用。

## 為什麼 eBPF 是攻擊者的工具？

eBPF 的特性讓它成為有吸引力的 rootkit 平台：

- **合法性**：eBPF 是 Linux 核心功能，不是 malware；EDR 工具難以區分合法的 observability 工具和惡意工具
- **Stealthiness**：BPF programs 住在 kernel，不像傳統 rootkit 需要修改 kernel code
- **Persistence**：如果 pin 到 BPF filesystem，重啟後仍然存在（如果攻擊者能在 startup 重新 attach）
- **Visibility**：BPF 可以看到幾乎所有 kernel 事件，包括密碼輸入、加密前的明文

## 技術一：Network Backdoor（XDP 或 TC）

在封包的特定 magic bytes 出現時，silently 轉發到 attacker（不出現在 iptables 裡）：

```c
/* 惡意的 XDP backdoor（概念示範）*/
/* 這個 code 只用於說明偵測方法，不要部署 */
SEC("xdp")
int malicious_xdp(struct xdp_md *ctx)
{
    /* 檢查特定 magic bytes（attacker 的觸發信號）*/
    /* 如果封包包含 magic bytes，redirect 到 attacker IP */
    /* 從 iptables/netfilter 的角度「不可見」*/
    /* 因為 XDP 在 netfilter 之前執行 */
    return XDP_PASS;
}
```

**偵測方法**：
```bash
# 列出所有 loaded XDP programs
sudo bpftool prog list | grep -E "xdp|prog_type 6"

# 查看 XDP program 附加在哪個介面
ip link show | grep "xdp"

# 分析 XDP program 的 bytecode（找可疑的 redirect 操作）
sudo bpftool prog dump xlated id <prog-id>
```

## 技術二：Credential Theft（kprobe/USDT）

在密碼被 hash 之前捕捉明文密碼：

```c
/* 攻擊者可能在 PAM 的認證函式上掛 kprobe */
/* 例如 pam_unix 的 verify_pwd_hash 函式（在明文密碼被 hash 之前）*/
/* 或 sshd 的 auth_password 函式 */

/* 偵測方法：
   - 檢查是否有 BPF program 附加在 auth-related 函式上
   - Tetragon / Falco 的規則：偵測對 pam、sshd 等 binary 的 kprobe attach
*/
```

## 技術三：eBPF-based Rootkit 工具

公開的 eBPF rootkit/red team 工具（用於教育和防禦研究）：

- **ebpfkit**（GitHub: fourieese）：C&C channel over DNS via XDP、hide processes、hide network connections
- **bad-bpf**（GitHub: pathtofile）：educational eBPF malware samples
- **boopkit**（GitHub: krisnova）：SSH reverse shell via BPF

這些工具的存在說明了為什麼偵測 malicious eBPF programs 很重要。

## 偵測 Malicious BPF Programs

### 方法一：Enumerate loaded BPF programs

```bash
# 列出所有 loaded programs
sudo bpftool prog list --json | python3 -m json.tool

# 查看每個 program 的 attach 點
sudo bpftool link list

# 查看 pinned objects（persist 的 BPF objects）
sudo find /sys/fs/bpf -type f
```

### 方法二：用 Tetragon 或 Falco 監控 bpf() syscall

```yaml
# Tetragon policy：偵測任何 BPF_PROG_LOAD 操作
apiVersion: cilium.io/v1alpha1
kind: TracingPolicy
metadata:
  name: "monitor-bpf-load"
spec:
  tracepoints:
  - subsystem: "syscalls"
    event: "sys_enter_bpf"
    args:
    - index: 0
      type: "int"
    selectors:
    - matchArgs:
      - index: 0
        operator: "Equal"
        values:
        - "5"  # BPF_PROG_LOAD = 5
      matchActions:
      - action: Post
```

```bash
# Falco rule：audit BPF program loads
# 在 Falco rule file 裡加入：
# - rule: BPF Program Load
#   condition: evt.type = bpf and evt.arg.cmd = BPF_PROG_LOAD
#   output: BPF program loaded (user=%user.name pid=%proc.pid)
#   priority: NOTICE
```

### 方法三：分析 BPF bytecode（靜態分析）

```bash
# dump 可疑的 BPF program 的指令
sudo bpftool prog dump xlated id <suspicious-id>

# 找可疑的 helper call
# bpf_redirect_map → 可能是 network redirect backdoor
# bpf_perf_event_output → 可能在 exfiltrate 資料
# bpf_probe_read_user → 可能在讀 userspace 記憶體（credential theft）

# 分析 map（找 exfiltration channel）
sudo bpftool map dump id <map-id>
```

### 方法四：用 cilium/pwru 偵測網路異常

```bash
# pwru（packet，where are you？）追蹤封包在 kernel 的路徑
# 可以找出封包是否被 XDP program 悄悄 redirect
sudo pwru --filter-dst-ip 1.2.3.4
```

## 防禦最佳實踐

1. **限制 BPF 的 capabilities**：只讓需要的 service 有 `CAP_BPF + CAP_PERFMON`；使用 LSM 限制哪些 process 能載入 BPF programs

2. **Audit BPF syscall**：用 auditd 或 Tetragon 記錄所有 `bpf()` syscall

3. **定期 enumerate loaded programs**：在系統 health check 裡加入 BPF program enumeration，比較 baseline

4. **Immutable BPF filesystem**：在生產環境把 `/sys/fs/bpf` 設為 read-only，或用 LSM 限制寫入

5. **簽名驗證**：在 kernel 5.x 之後，BPF program 可以用 BTF 做 code signing（實驗性功能）

## 踩雷集錦

1. **eBPF rootkit 不需要 kernel module**：傳統 EDR 的 kernel module integrity check 無法偵測 eBPF rootkit；需要 BPF-aware 的工具（Tetragon、Falco with eBPF driver）

2. **XDP backdoor 繞過 iptables/netfilter**：傳統的 network IDS（基於 iptables log）看不到 XDP 丟棄的封包；需要基於 eBPF 的 network monitoring（如 Hubble）

3. **BPF program persist 不需要修改磁碟**：如果攻擊者在 init script 裡加入 BPF program 載入，只改 init script 就夠；傳統的 file integrity monitoring 不足

## 本章重點整理

- eBPF 的合法性和 kernel visibility 讓它成為有吸引力的 rootkit 平台
- 主要技術：network backdoor（XDP/TC）、credential theft（kprobe）、data exfiltration
- 偵測方法：enumerate loaded programs、audit bpf() syscall、分析 bytecode、network anomaly detection
- 最佳實踐：限制 BPF capability、audit、定期 baseline 對比

## 自我檢核

- [ ] 能說出 eBPF 為什麼比傳統 kernel module rootkit 更難偵測
- [ ] 知道哪些工具（Tetragon、Falco）可以用來偵測惡意 BPF 活動
- [ ] 能執行基本的 BPF 枚舉（列出所有 loaded programs 和 links）

→ [Ch 38 BPF 在容器與 Kubernetes 安全](./38-container-kubernetes-security.md)
