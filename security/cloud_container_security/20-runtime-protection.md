# Ch 20 — Runtime 防護：seccomp / AppArmor / SELinux 怎麼被繞

> **目標**：掌握容器 runtime 防護的四層機制（seccomp、AppArmor、capabilities、no-new-privileges），能從內部判斷容器的硬化（hardening）程度，並知道攻擊者如何找到設定錯誤的容器。
>
> **環境**：Docker 26.x / Ubuntu 22.04 / Linux kernel 6.x。AppArmor 功能需 Ubuntu；SELinux 需 RHEL/Fedora。

---

## 為什麼需要

Ch 17–18 展示了逃逸技術，核心前提是「容器本身沒有足夠限制」。一個典型的逃逸路徑長這樣：容器跑在 root、有 `CAP_SYS_ADMIN`、seccomp 關掉、沒有 AppArmor profile——這種設定讓攻擊者幾乎直接面對 kernel。

容器的隔離不是只靠 namespace。namespace 只是視野隔離，它本身不阻止 syscall。runtime 防護的真正工作是：

- **seccomp（Secure Computing Mode，安全計算模式）**：在 syscall 層過濾，決定哪些 syscall 能進 kernel
- **AppArmor / SELinux**：強制訪問控制（Mandatory Access Control, MAC），決定哪些檔案路徑、socket 操作被允許
- **capabilities（能力位）**：把 root 的超級權限切成 39 個細粒度能力，按需給予
- **no-new-privileges**：鎖死 privilege escalation（提權）入口，讓 SUID binary 失效

這四層是深度防禦（defense in depth）。沒有任何一層是萬能的，但疊在一起能讓大多數已知逃逸路徑失效。本章同時從防禦者和攻擊者兩個視角看這四層。

---

## 先建直覺

把防護層想像成容器和 kernel 之間的過濾網：

```
Container process
       │
       │  syscall (read, write, open, ptrace, ...)
       ▼
┌──────────────────────────────────────────────┐
│  no-new-privileges                           │  ← 進場前就鎖死提權
├──────────────────────────────────────────────┤
│  seccomp BPF filter                          │  ← syscall 白名單/黑名單
│  (per-syscall decision: ALLOW / KILL / TRAP) │
├──────────────────────────────────────────────┤
│  capabilities check                          │  ← 需要特權的 syscall 要有對應 cap
│  (CAP_NET_BIND_SERVICE, CAP_SYS_ADMIN, ...)  │
├──────────────────────────────────────────────┤
│  AppArmor / SELinux MAC                      │  ← 資源路徑層過濾
│  (file path, socket, mount 操作)             │
├──────────────────────────────────────────────┤
│  Linux Kernel                                │
└──────────────────────────────────────────────┘
```

syscall 要通過每一層才能抵達 kernel 真正執行。攻擊者找的是哪一層被關掉或設定過鬆。

---

## 底層機制

### seccomp：syscall 層過濾

seccomp 有三個模式，從 `/proc/self/status` 的 `Seccomp` 欄位可讀到：

- `0`：停用（disabled）
- `1`：strict mode（只允許 `read`、`write`、`exit`、`sigreturn`，幾乎沒有容器在跑）
- `2`：filter mode（BPF 規則，這是實際使用的模式）

Docker 預設會套用一個內建 profile，封鎖約 44 個高風險 syscall，包含：

| syscall | 為什麼封鎖 |
|---------|-----------|
| `kexec_load` | 載入新 kernel，逃逸手段 |
| `ptrace` | 追蹤其他 process，跨容器攻擊 |
| `add_key` / `keyctl` | 操作 kernel keyring，可竊取 session key |
| `mount` | 掛載 host 檔案系統 |
| `clone` with `CLONE_NEWUSER` | 建立新 user namespace，提權路徑 |
| `reboot` | 重啟主機 |

實際的 Docker default seccomp profile 是一個 JSON 白名單（whitelist），定義了允許通過的 syscall 清單，清單外的全部封鎖。profile 原始碼在 Docker 專案的 `profiles/seccomp/default.json`。

seccomp profile 是 BPF（Berkeley Packet Filter）程式，在 kernel 裡執行，overhead 極低（每次 syscall 約幾十 ns）。

---

## 具體範例

### 範例一：確認容器的 seccomp 狀態

從容器內部判斷自己受到多少限制：

```bash
# 在容器內執行
docker run --rm ubuntu:22.04 bash -c "grep Seccomp /proc/self/status"
# Seccomp:	2
# 2 = filter mode，Docker 預設 profile 啟用中

docker run --rm --security-opt seccomp=unconfined ubuntu:22.04 \
    bash -c "grep Seccomp /proc/self/status"
# Seccomp:	0
# 0 = 完全停用，攻擊者的目標
```

`Seccomp: 2` 不代表安全，還要看 profile 允許了哪些 syscall。`Seccomp: 0` 是最危險的訊號——攻擊者拿到這個容器等同於拿到裸 kernel 的存取。

### 範例二：自訂 seccomp profile（最小化白名單）

假設有一個只需要讀寫檔案、不需要網路的靜態分析工具，可以做最小化 profile：

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "architectures": ["SCMP_ARCH_X86_64"],
  "syscalls": [
    {
      "names": [
        "read", "write", "open", "openat", "close",
        "fstat", "stat", "lstat", "lseek", "mmap",
        "mprotect", "munmap", "brk", "exit", "exit_group",
        "futex", "rt_sigaction", "rt_sigprocmask",
        "getcwd", "getdents64", "readlink", "readlinkat",
        "access", "faccessat", "ioctl", "fcntl"
      ],
      "action": "SCMP_ACT_ALLOW"
    }
  ]
}
```

```bash
# 套用自訂 profile
docker run --rm \
    --security-opt seccomp=/path/to/minimal-profile.json \
    my-static-analyzer:latest \
    /tool/run --input /data/sample.bin
```

`SCMP_ACT_ERRNO` 是預設動作：其他 syscall 一律回傳 `EPERM`（Operation not permitted）而不是殺掉 process。用 `SCMP_ACT_KILL` 更嚴格，syscall 被封鎖時直接 kill process（SIGSYS）。生產環境建議先用 `SCMP_ACT_LOG` 做稽核，確認程式實際需要哪些 syscall，再切成白名單。

### 範例三（邊界情境）：seccomp 擋不住的操作

seccomp 在 syscall 層過濾，但有些操作能在允許的 syscall 範圍內完成：

```bash
# ptrace 被封鎖，但如果 process 有 CAP_SYS_PTRACE，
# 某些 /proc/[pid]/mem 讀取還是能做到
# seccomp 和 capabilities 要一起鎖，不能只靠其中一層

# 實驗：在預設 profile 下嘗試 kexec_load（會被 seccomp 殺掉）
docker run --rm ubuntu:22.04 bash -c '
python3 -c "
import ctypes, sys
NR_kexec_load = 246
ret = ctypes.CDLL(None).syscall(NR_kexec_load, 0, 0, None, 0)
print(f\"ret={ret}\", file=sys.stderr)
"
'
# Killed（SIGSYS）— seccomp default action 殺掉 process
# 如果用 SCMP_ACT_ERRNO 則得到 ret=-1 errno=1 (EPERM)
```

`kexec_load` 的 syscall number 在 x86-64 是 246，這個數字在 ABI 裡是固定的（`/usr/include/asm/unistd_64.h` 可查）。

---

## AppArmor：強制訪問控制

AppArmor（Application Armor）是 Ubuntu/Debian 預設的 MAC 系統。Docker 在有 AppArmor 的系統上會自動套用 `docker-default` profile。

`docker-default` 的主要限制：

- 禁止寫入 `/proc/sysrq-trigger`（避免觸發 SysRq 指令）
- 禁止建立 raw socket（防止 ARP spoofing 等 L2 攻擊）
- 禁止大多數 mount 操作
- 禁止 `ptrace` 其他 process

從容器內確認 AppArmor 狀態：

```bash
docker run --rm ubuntu:22.04 cat /proc/self/attr/current
# docker-default (enforce)
# "enforce" 表示違規會被拒絕並記錄到 kernel log
# "complain" 模式違規只記錄不拒絕，用於 profile 開發階段

docker run --rm --security-opt apparmor=unconfined ubuntu:22.04 \
    cat /proc/self/attr/current
# unconfined
# 攻擊者的目標狀態
```

AppArmor profile 的語法（節錄）：

```
profile docker-default flags=(attach_disconnected,mediate_deleted) {
  # Allow read on /proc/sys except sysrq
  /proc/sys/** r,
  deny /proc/sysrq-trigger w,

  # Deny raw sockets
  network raw,      # deny by default
  network packet,   # deny by default

  # Allow basic file operations
  file,
  umount,
}
```

AppArmor 和 seccomp 的關鍵差異：seccomp 看 **syscall number**，AppArmor 看**資源路徑和 capability**。兩者互補，都要鎖。

### SELinux（簡述）

SELinux（Security-Enhanced Linux）是 RHEL/Fedora 預設的 MAC 系統，機制和 AppArmor 類似但更細粒度——每個物件都有 **label（標籤）**，政策決定哪個 label 能存取哪個 label。

容器在 SELinux 環境下的核心 label：

- 容器 process：`container_t`（限制型 domain）
- 容器寫入的檔案：`container_file_t`
- 跨容器隔離的標籤：`svirt_sandbox_file_t`——不同容器各自的 label 不同，防止容器 A 讀取容器 B 的 volume

```bash
# 在 RHEL/Fedora 上確認容器 label
ps -eZ | grep container
# system_u:system_r:container_t:s0:c123,c456  ...
# s0:c123,c456 是 MCS (Multi-Category Security) 分類，每個容器不同
```

Ubuntu 用 AppArmor，RHEL/Fedora 用 SELinux——兩者只能存在其一，不能同時啟用。

---

## Capabilities：把 root 切碎

Linux 把 root 的特權切成 39 個 capability（能力位）。容器常見的危險 capability：

| Capability | 能做什麼 | 攻擊者為何要它 |
|------------|---------|--------------|
| `CAP_SYS_ADMIN` | 幾乎等同 root，mount、namespace 操作 | 逃逸的萬能鑰匙 |
| `CAP_SYS_PTRACE` | ptrace 任意 process | 跨 namespace 讀 process 記憶體 |
| `CAP_NET_ADMIN` | 修改網路介面、iptables | ARP spoofing、流量劫持 |
| `CAP_DAC_OVERRIDE` | 忽略 DAC（自主存取控制）權限 | 讀任意檔案 |
| `CAP_SETUID` / `CAP_SETGID` | 切換 UID/GID | 切到 root |

Docker 預設保留的 capability 集（約 14 個）比完整 root 少，但對大多數攻擊已夠用。最佳做法是 `--cap-drop=ALL` 再按需補回：

```bash
# 最危險：完整 cap set（privileged 隱含）
docker run --rm --privileged alpine capsh --print
# Current: = cap_chown,cap_dac_override,...,cap_sys_admin,...+eip
# 幾乎等同在 host 上跑

# 最安全：全部 drop，只補 NET_BIND_SERVICE（讓程式能綁 port < 1024）
docker run --rm \
    --cap-drop=ALL \
    --cap-add=NET_BIND_SERVICE \
    alpine capsh --print
# Current: = cap_net_bind_service+eip
# 只剩一個

# 確認容器內自己的 cap（hex 格式，需要解碼）
cat /proc/self/status | grep Cap
# CapInh: 0000000000000000
# CapPrm: 00000000000004a0
# CapEff: 00000000000004a0
# CapBnd: 00000000000004a0

# 用 capsh 解碼（在容器內）
capsh --decode=00000000000004a0
# cap_net_bind_service,cap_net_broadcast,cap_net_admin,cap_net_raw
```

十六進位值 `0x4a0` = bit 5（`CAP_NET_BROADCAST`）+ bit 7（`CAP_NET_ADMIN`）+ bit 8（`CAP_NET_BIND_SERVICE`）+ bit 9（`CAP_NET_RAW`）的 OR 結果，每個 bit 對應一個 capability 編號（在 `linux/capability.h` 裡定義）。

---

## no-new-privileges：鎖死 SUID 提權

SUID（Set User ID）binary 執行時以 owner 的 UID 跑，傳統用來讓普通用戶以 root 執行 `passwd`、`sudo` 等工具。容器裡若存在 SUID binary，且沒有 no-new-privileges，攻擊者可以：

```
低權限容器 process → 執行 SUID binary → 得到 root effective UID
                                        → 搭配 CAP_SYS_ADMIN 逃逸
```

`--security-opt no-new-privileges:true` 在底層呼叫 `prctl(PR_SET_NO_NEW_PRIVS, 1)`，這個設定後 `execve()` 無法再提升 effective UID/GID，SUID bit 被忽略。

```bash
# 建立測試 image：帶 SUID /bin/id 的容器
cat > /tmp/Dockerfile.suid << 'EOF'
FROM alpine
# copy id binary and set SUID so it runs as root owner
RUN cp /bin/id /suid-id && chmod u+s /suid-id
USER nobody
EOF

docker build -t suid-test /tmp/

# 沒有 no-new-privileges：SUID 有效
docker run --rm suid-test /suid-id
# uid=0(root) gid=65534(nobody) groups=65534(nobody)
# ^ effective uid = 0，即使 process 是 nobody 啟動的

# 有 no-new-privileges：SUID 被無視
docker run --rm --security-opt no-new-privileges:true suid-test /suid-id
# uid=65534(nobody) gid=65534(nobody) groups=65534(nobody)
# ^ 維持 nobody，SUID 失效
```

這個差異說明了為什麼只設 `USER nobody` 不夠——SUID binary 還是能繞過。

---

## Rootless Container（無根容器）

傳統 Docker daemon 以 root 跑，一旦逃逸就直接得到 host root。rootless 模式讓 Docker daemon 本身以普通用戶跑，容器內的 root 只是透過 user namespace 映射的假 root：

```bash
# 安裝 rootless Docker（Ubuntu）
dockerd-rootless-setuptool.sh install

# 容器內看起來是 root
docker run --rm alpine id
# uid=0(root) gid=0(root) groups=0(root)

# 但在 host 上看同一個 process
ps aux | grep "alpine"
# 100000  12345  0.0  0.0  ...  sh
# ^ UID 100000，不是 0
# 100000 是 /etc/subuid 給這個用戶配置的 sub-UID 起點
```

`/etc/subuid` 裡的設定決定映射範圍，例如 `alice:100000:65536` 表示 alice 的容器 root（0）映射到 host UID 100000，容器 UID 1 → host UID 100001，以此類推。

rootless 的限制：
- AppArmor profile 在某些情境下不能套用（user namespace 的 MAC 限制）
- 網路效能略差（使用 slirp4netns 而非 veth）
- 某些 storage driver 不支援
- `--net=host` 等 host 資源共享功能受限

即使有上述限制，multi-tenant 環境（如共用 CI runner）應優先考慮 rootless。

---

## gVisor 與 Kata Containers：沙箱化隔離

上述四層防護的根本問題：**container process 還是在呼叫 host kernel 的 syscall**。只要 kernel 有洞（未修補的 CVE），防護層可能全部失效。

兩種替代方案從架構上解決這個問題：

**gVisor（Google）**

在 container process 和 host kernel 之間插入一個 Go 寫的 userspace kernel（稱為 Sentry）。container 的所有 syscall 被 Sentry 攔截，由 Sentry 模擬或轉為少數安全的 host syscall。

```
container process → syscall → Sentry (Go userspace kernel)
                                      │
                                      └─ 少數 host syscall → real kernel
```

Sentry 暴露給 host kernel 的 syscall 面只有約 100 個（相對於 container 可能發出的 300+ syscall），大幅縮小攻擊面。

**Kata Containers（Intel/IBM/Red Hat）**

每個容器有自己的輕量 VM（基於 QEMU/cloud-hypervisor），有獨立的 kernel。逃逸需要先破 VM，再破 host kernel——兩個 kernel 的漏洞都要利用。

```bash
# 使用 gVisor runtime（需先安裝 runsc）
docker run --runtime=runsc --rm alpine uname -r
# 4.4.0  ← gVisor 回傳的假 kernel 版本（固定值，不是 host kernel 版本）

# 在 gVisor 容器內嘗試需要 host kernel 特性的操作
docker run --runtime=runsc --rm ubuntu:22.04 \
    bash -c "cat /proc/self/status | grep Seccomp"
# Seccomp:	0  ← gVisor 自己攔截 syscall，不需要 seccomp filter
```

## 對比取捨表

| 機制 | 防護層 | 效能開銷 | 繞過難度 | 適用場景 |
|------|--------|----------|----------|----------|
| seccomp（預設 profile）| syscall filter | 極低（< 1%） | 中（需找允許 syscall 的利用路徑） | 所有容器預設啟用 |
| seccomp（自訂最小化）| syscall filter | 極低 | 高 | 已知 syscall 需求的服務 |
| AppArmor docker-default | 資源路徑 MAC | 低（< 5%） | 中高 | Ubuntu/Debian 環境 |
| SELinux container_t | label-based MAC | 低 | 高 | RHEL/Fedora 環境 |
| cap-drop=ALL | 特權能力 | 零 | 高（需找到 cap 洩漏） | 所有容器，應成為預設 |
| no-new-privileges | SUID 封鎖 | 零 | 高 | 有非 root USER 的容器 |
| Rootless Docker | UID 映射 | 低（網路略差） | 高（需破 user namespace） | multi-tenant CI/CD |
| gVisor | userspace kernel | 中（CPU bound 服務 5–30%）| 很高（需破 Sentry） | 不可信 workload |
| Kata Containers | VM 隔離 | 高（VM overhead, 100ms+ 啟動）| 極高（需雙層逃逸）| 高合規/金融場景 |

---

## 踩雷集錦

**1. `--privileged` 關掉了所有防護層**

`--privileged` 同時關掉 seccomp、AppArmor、SELinux，並給予完整 cap set。這是一個開關讓你完全放棄所有防護。CI pipeline 常因為「需要在容器裡跑 Docker」而加上 `--privileged`，正確解法是 Docker-in-Docker（dind）image 搭配 rootless，或用 `--device /dev/fuse` 只給需要的裝置。

**2. seccomp profile 路徑問題導致 profile 被靜默忽略**

```bash
docker run --security-opt seccomp=/wrong/path.json alpine echo "hi"
# 在舊版 Docker 這條命令成功，但 seccomp 被設成 unconfined
# 新版會報錯：invalid security option "seccomp=..."
# 寫 CI script 時要驗證 profile 路徑存在再跑容器
```

**3. cap-drop 之後 bind mount 可能失敗**

drop 掉 `CAP_DAC_OVERRIDE` 後，容器 process 嚴格遵守 DAC 權限。若 bind mount 進來的目錄 owner 是 host root（UID 0）、mode 是 700，容器內的 nobody process 就無法讀取，且錯誤訊息是 `Permission denied`，看起來像是 volume 設定錯誤，實際是 capability 問題。

**4. AppArmor profile 和 seccomp 都設了，但 `--network=host` 繞過網路限制**

AppArmor 的 raw socket 封鎖在 container 自己的 network namespace 裡有效，但用 `--network=host` 讓容器進入 host network namespace 後，AppArmor 的 profile 仍套用（不像 `--privileged` 完全關掉），但加上 `CAP_NET_RAW` 就可以在 host namespace 裡發 raw packet。確保 `--network=host` 的容器同時 cap-drop=ALL。

**5. rootless 容器的 `/proc/[pid]/maps` 問題**

rootless 環境下，容器內的 root（host UID 100000）嘗試讀 `/proc/1/maps` 會成功（因為 pid 1 也是同一個 user namespace 下的 process），但讀 host 上其他用戶的 `/proc/[pid]/maps` 會 EPERM。這個邊界行為很容易被誤以為「rootless 完全隔離 /proc」，實際上同一個 user namespace 內的 process 還是可以互相讀。

---

## 進階延伸

### 攻擊者的容器審計腳本

拿到一個容器 shell 後，第一步是快速判斷防護程度：

```bash
#!/bin/sh
# container-audit.sh — run inside a container to check hardening

echo "=== seccomp ==="
grep Seccomp /proc/self/status
# 0=off, 2=filter

echo "=== AppArmor ==="
cat /proc/self/attr/current 2>/dev/null || echo "not available"
# "docker-default (enforce)" or "unconfined"

echo "=== capabilities ==="
capsh --print 2>/dev/null || cat /proc/self/status | grep Cap
# look for cap_sys_admin, cap_sys_ptrace

echo "=== UID ==="
id
# root = potential issue; check if namespace root only

echo "=== dangerous mounts ==="
mount | grep -E "(/dev|/proc/sysrq|/sys/kernel)" | grep -v "ro"
# writable /dev or /sys/kernel/debug mounts are escape vectors

echo "=== SUID binaries ==="
find / -perm -4000 2>/dev/null | head -20
# SUID in container + no-new-privileges=false → privesc

echo "=== no-new-privileges ==="
# If this write fails, no-new-privileges is set
cat /proc/self/status | grep NoNewPrivs 2>/dev/null
# NoNewPrivs: 1 = locked; 0 = SUID escalation possible
```

`NoNewPrivs: 1` 表示 `prctl(PR_SET_NO_NEW_PRIVS)` 已設定，這個 bit 一旦設上無法清除（不可逆操作），子 process 也繼承。

### 繞過防護層的思路

從攻擊者角度，防護層的弱點在於「交集不是空集合」：

- seccomp 允許 `open()`、`write()` → 但 AppArmor 的 profile 可能阻止寫 `/proc/sysrq`
- seccomp 允許 `socket(AF_PACKET)` → 但 `CAP_NET_RAW` 沒給就無法用
- AppArmor 允許 mount → 但沒有 `CAP_SYS_ADMIN` 就 mount 不了

實際上能逃逸代表同時繞過了所有有效的防護層。現代容器逃逸 CVE（如 runc CVE-2019-5736）的利用條件幾乎都要求「至少一層防護缺失」。

---

## 防禦最佳實踐清單

部署容器時要確認以下每一項：

1. **最小化 base image**：用 `alpine` 或 `distroless`，減少可利用的 binary（無 `sh`、無 `curl` → 攻擊者難以轉移工具）
2. **cap-drop=ALL，按需 add**：`--cap-drop=ALL --cap-add=NET_BIND_SERVICE`，只給必要 capability
3. **保持 seccomp 啟用**：不要加 `--security-opt seccomp=unconfined`；需要更嚴格時提供自訂 profile
4. **no-new-privileges 預設開啟**：`--security-opt no-new-privileges:true`，防 SUID 提權
5. **Read-only rootfs**：`--read-only`，攻擊者無法在容器裡寫工具；搭配 `--tmpfs /tmp` 給需要寫入的路徑
6. **非 root USER**：Dockerfile 裡 `USER 1000:1000`，配合 no-new-privileges 使 SUID 完全失效
7. **限制資源**：`--memory=256m --cpu-shares=512`，防止 DoS 或 crypto mining
8. **定期掃描 image**：`trivy image my-app:latest`，找已知 CVE 和 misconfig

這八條裡，第 2、3、4、5 是純 runtime flag，不需要改 image，是最快落地的防護措施。

---

## 本章重點整理

- seccomp filter（`Seccomp: 2`）在 syscall 層封鎖高風險操作；`Seccomp: 0` 是攻擊者找的第一個訊號
- Docker default seccomp profile 封鎖約 44 個 syscall，但不能當成唯一防護；生產環境建議最小化自訂 profile
- AppArmor `docker-default` 在 Ubuntu 自動套用，封鎖 raw socket 和危險 /proc 路徑；`cat /proc/self/attr/current` 可確認
- SELinux 在 RHEL/Fedora 提供 label-based 隔離，`svirt_sandbox_file_t` 防跨容器 volume 存取
- `--cap-drop=ALL --cap-add=<needed>` 是 capability 硬化的正確模式；`capsh --decode` 解讀 `/proc/self/status` 的 hex cap 值
- `no-new-privileges` 在底層設 `PR_SET_NO_NEW_PRIVS`，讓 SUID binary 失效；`USER nobody` 單獨不夠
- Rootless Docker 讓容器 root 映射到 host 高 UID（如 100000），逃逸後不得 host root
- gVisor 用 userspace kernel 攔截 syscall；Kata 用輕量 VM 給每容器獨立 kernel——兩者都從架構上縮小 host kernel 暴露面
- `--privileged` 關掉所有防護層，等同直接暴露 host kernel；任何環境都不應在生產使用

---

## 自我檢核

- [ ] 我能從 `/proc/self/status` 的 `Seccomp` 欄位判斷容器的 seccomp 狀態，並說出 0/1/2 各代表什麼
- [ ] 我能說出 Docker default seccomp profile 封鎖了哪類 syscall，以及為什麼要封鎖 `ptrace` 和 `kexec_load`
- [ ] 我能說出 AppArmor 和 seccomp 的層次差異：一個看 syscall number，一個看資源路徑
- [ ] 我能解釋為什麼 `--cap-drop=ALL --cap-add=NET_BIND_SERVICE` 比預設 cap set 更安全
- [ ] 我能說出 `no-new-privileges` 在 kernel 層面呼叫了什麼 prctl，以及它如何讓 SUID 失效
- [ ] 我知道 rootless 容器的 UID 映射機制，以及逃逸後攻擊者在 host 上的實際 UID
- [ ] 我能說出 gVisor 和 Kata Containers 的架構差異，以及各自適合的場景
- [ ] 我能寫出從容器內審計防護狀態的一行指令集，並知道每個輸出代表什麼

---

## 延伸閱讀

1. **Docker seccomp profiles 官方文件**
   URL：https://docs.docker.com/engine/security/seccomp/
   為什麼讀：包含 default profile 的完整 syscall 清單，以及如何用 `strace` 收集 syscall trace 再產生最小化 profile 的完整流程。

2. **Linux man page: seccomp(2)**
   URL：https://man7.org/linux/man-pages/man2/seccomp.2.html
   為什麼讀：seccomp 的 kernel 視角——BPF 程式的結構、`SECCOMP_RET_*` 各回傳動作的語義、`PR_SET_NO_NEW_PRIVS` 和 seccomp 的關係在這裡說得最清楚。

3. **AppArmor Docker profile source（GitHub）**
   URL：https://github.com/moby/moby/blob/master/profiles/apparmor/template.go
   為什麼讀：看 `docker-default` 實際允許/拒絕了什麼，了解 profile 後才知道攻擊者能在「有 AppArmor」的容器裡做什麼。

4. **gVisor Architecture Overview**
   URL：https://gvisor.dev/docs/architecture_guide/
   為什麼讀：Sentry 如何攔截 syscall、Gofer 如何處理 file I/O——理解 userspace kernel 的設計能讓你判斷 gVisor 的侷限（不是所有 syscall 都實作了）和效能 trade-off。

5. **Trail of Bits — Understanding and Hardening Linux Containers**
   URL：https://github.com/trailofbits/publications/blob/master/papers/understanding_hardening_linux_containers.pdf
   為什麼讀：2016 年的白皮書，但底層機制至今不變。系統性地把 namespace、capabilities、seccomp、AppArmor 放在同一個框架裡分析，是本章內容的最好對照讀物。

---

Part 3 到這裡收尾。我們從容器隔離的安全模型出發（Ch 16），拆解了兩種逃逸路徑（Ch 17–18），看了供應鏈的攻擊面（Ch 19），最後在本章把防護層和攻擊者的繞過視角對齊。下一部分轉向 Kubernetes，先從架構開始建心智模型——同樣的「先懂結構才打得準」邏輯。

→ [Ch 21 — K8s 架構：control plane / node / etcd / API server](21-k8s-architecture.md)
