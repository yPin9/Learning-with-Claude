# Ch 9 — Capabilities 與 seccomp：容器的權限邊界

> 目標：理解 Linux capabilities 和 seccomp 這兩層安全機制，知道 Docker 預設給了哪些權限、封鎖了哪些 syscall，以及如何從 capability / seccomp 設定不當切入容器逃逸。

---

## Linux Capabilities：把 root 權限切碎

傳統 Unix 只有兩種：root（uid=0，什麼都能做）和普通用戶（什麼都不能）。這太粗糙了。

Linux capabilities（能力）把 root 的特權拆成約 40 個細粒度能力，可以單獨賦予或撤銷。一個 process 可以是非 root uid，但擁有特定 capability，例如只有 `CAP_NET_BIND_SERVICE` 讓它綁 1024 以下的 port。

```
傳統：
  root -> 無限制
  user -> 不能 bind < 1024，不能 mount，不能 chown，...

Capabilities：
  process A: uid=1000 + CAP_NET_BIND_SERVICE  -> 可以 bind port 80
  process B: uid=1000 + CAP_SYS_PTRACE        -> 可以 ptrace 其他 process
  process C: uid=0    + 無任何 capability      -> 連 chown 都不行
```

---

## Docker 預設給容器的 Capabilities

Docker 不給容器完整的 root capabilities，預設給這些：

| Capability | 用途 |
|------------|------|
| CAP_CHOWN | 改變任意檔案的 uid/gid |
| CAP_DAC_OVERRIDE | 繞過 DAC（自主存取控制）的讀寫 x 位元限制 |
| CAP_FSETID | 設定 setuid/setgid bit |
| CAP_FOWNER | 繞過「owner 才能操作」的限制 |
| CAP_MKNOD | 建立 device 節點（`mknod`） |
| CAP_NET_RAW | 使用 raw socket，做 ping / ARP spoofing |
| CAP_SETGID | 修改 process 的 gid |
| CAP_SETUID | 修改 process 的 uid |
| CAP_SETFCAP | 設定任意 file capability |
| CAP_SETPCAP | 調整 process 的 capability set |
| CAP_NET_BIND_SERVICE | 綁定 < 1024 的 port |
| CAP_SYS_CHROOT | 呼叫 chroot() |
| CAP_KILL | 對任意 process 送 signal |
| CAP_AUDIT_WRITE | 寫 kernel audit log |

預設沒有的（這些都很危險）：

| Capability | 為什麼危險 |
|------------|------------|
| CAP_SYS_ADMIN | 幾乎等於 root，可以 mount、改 kernel 參數 |
| CAP_SYS_PTRACE | 可以 ptrace 其他 process，讀寫記憶體 |
| CAP_SYS_MODULE | 載入 kernel module，直接拿 ring 0 |
| CAP_NET_ADMIN | 修改路由表、設定 iptables |
| CAP_SYS_RAWIO | 直接存取 /dev/mem，讀寫物理記憶體 |

---

## 查看 Capabilities

```bash
# 在 host 上查看當前 shell 的 capabilities
capsh --print
# Current: =ep    <- 完整的 effective + permitted capability set

# 進入容器看
docker run --rm alpine capsh --print
# Current: =eip cap_net_bind_service,cap_net_raw,...  <- 只有部分

# 也可以看 /proc/self/status
docker run --rm alpine cat /proc/self/status | grep Cap
# CapInh: 0000000000000000
# CapPrm: 00000000a80425fb
# CapEff: 00000000a80425fb
# CapBnd: 00000000a80425fb
# CapAmb: 0000000000000000

# 用 capsh 解碼十六進位值
capsh --decode=00000000a80425fb
```

---

## 操控 Capabilities

```bash
# 丟掉所有 capability，只加回需要的（最小權限原則）
docker run --cap-drop ALL --cap-add NET_BIND_SERVICE \
  -p 80:80 nginx

# 加上危險的 capability（只在必要時）
# 例如需要 strace 時
docker run --cap-add SYS_PTRACE --security-opt seccomp=unconfined \
  ubuntu:22.04 strace ls

# 加上 SYS_ADMIN（非常危險，幾乎等於 --privileged）
docker run --cap-add SYS_ADMIN ubuntu:22.04 mount -t tmpfs tmpfs /mnt
```

`--privileged` 是給容器所有 capabilities + 關掉 seccomp + 給 host 的 device 存取，等於沒有容器隔離。永遠不要在生產環境用，除非你在跑 Docker-in-Docker。

---

## seccomp：過濾 syscall 的白/黑名單

seccomp（Secure Computing Mode，安全計算模式）是 Linux kernel 的 syscall 過濾器。Docker 預設載入一個 seccomp profile，封鎖約 44 個 syscall。

syscall 被封鎖時，process 收到 `EPERM` 或 `ENOSYS`，或直接被 `SIGSYS` 殺死（取決於 profile 設定的 action）。

### Docker 預設封鎖的典型 syscall

| syscall | 封鎖原因 |
|---------|---------|
| `ptrace` | 可以讀寫其他 process 的記憶體，container escape 常見媒介 |
| `keyctl` | 存取 kernel keyring，可能竊取密鑰材料 |
| `perf_event_open` | 可以用來做 side-channel attack（Spectre 類） |
| `clone` (with CLONE_NEWUSER) | 建立 user namespace，搭配其他漏洞可提權 |
| `unshare` | 同上 |
| `mount` | 掛載 filesystem，可能繞過 rootfs 隔離 |
| `pivot_root` | 修改 rootfs，逃逸用 |
| `swapon/swapoff` | 操作 swap，影響 host |
| `syslog` | 讀 kernel log（可能洩漏 KASLR 等資訊） |
| `acct` | 開啟 process accounting，可能造成 DoS |

完整列表在 Docker 的 GitHub：`moby/moby/profiles/seccomp/default.json`

### seccomp profile JSON 結構

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
  "syscalls": [
    {
      "names": ["accept", "accept4", "access", "adjtimex"],
      "action": "SCMP_ACT_ALLOW"
    },
    {
      "names": ["ptrace"],
      "action": "SCMP_ACT_ERRNO",
      "errnoRet": 1
    },
    {
      "names": ["kill"],
      "action": "SCMP_ACT_ALLOW",
      "args": [
        {
          "index": 1,
          "value": 9,
          "op": "SCMP_CMP_NE"
        }
      ]
    }
  ]
}
```

| action | 效果 |
|--------|------|
| SCMP_ACT_ALLOW | 允許 |
| SCMP_ACT_ERRNO | 傳回 errno（預設 EPERM） |
| SCMP_ACT_KILL | 直接殺死 process |
| SCMP_ACT_TRACE | 讓 ptrace tracer 處理（測試用） |
| SCMP_ACT_LOG | 允許但寫 audit log |

### 使用自訂 seccomp profile

```bash
# 完全關掉 seccomp（危險，通常是為了 debug 或 CTF）
docker run --security-opt seccomp=unconfined ubuntu:22.04 bash

# 用自訂 profile
docker run --security-opt seccomp=/path/to/my-profile.json ubuntu:22.04 bash

# 什麼時候需要關掉 seccomp：
# 1. 用 strace/perf 做 profiling（需要 ptrace / perf_event_open）
# 2. 在容器裡跑 Chrome/Chromium（它需要很多 syscall）
# 3. CTF 逆向或 pwn 題目環境
```

---

## AppArmor 與 SELinux：MAC 補充防線

capabilities 和 seccomp 還不夠，因為它們是 process 層級的控制。MAC（Mandatory Access Control，強制存取控制）在 kernel 層面加了一層 policy，不管 process 的 uid/capability 是什麼，都要過 MAC 檢查。

| MAC 機制 | 主要發行版 | Docker 整合方式 |
|---------|-----------|----------------|
| AppArmor | Ubuntu、Debian | 預設 profile `docker-default`，限制 `/proc`、`/sys` 等路徑 |
| SELinux | RHEL、CentOS、Fedora | 用 `container_t` label，限制容器能存取的 host 資源 |

```bash
# 查看 AppArmor 是否啟用
docker info | grep -i apparmor
# Security Options: apparmor seccomp

# 關掉 AppArmor（不建議）
docker run --security-opt apparmor=unconfined ubuntu:22.04 bash

# 查看容器的 AppArmor profile
docker inspect <container_id> | grep AppArmor
```

---

## CTF Container Escape：capability / seccomp 不當的實際案例

### 案例一：CAP_SYS_PTRACE + 相同 PID namespace

如果容器有 `CAP_SYS_PTRACE` 且和 host 共用 PID namespace（`--pid=host`），可以 ptrace host 上任何 process，注入 shellcode，直接拿 host shell。

```bash
# 危險組合
docker run --pid=host --cap-add SYS_PTRACE ubuntu:22.04 bash
# 在容器裡可以 ptrace host 的 PID 1 (systemd/init)
```

### 案例二：CVE-2019-5736（runc < 1.0-rc6）

攻擊者可以覆蓋 host 上的 `/usr/bin/runc` binary。條件：

1. 攻擊者能控制容器的 image（例如 `docker pull` 的 image 是攻擊者的）
2. 或是容器有寫 host filesystem 某些路徑的能力

原理：利用 `/proc/self/exe` 在 runc 執行過程中的 race condition，通過 `O_PATH` fd 的方式覆蓋 runc binary。修復版本是 runc 1.0-rc6+，Kubernetes/Docker 的緊急安全更新。

### 案例三：CVE-2022-0492（CAP_SYS_ADMIN + cgroup v1 release_agent）

cgroup v1 的 `release_agent` 會在 cgroup 清空時在 host 上執行一個 script。如果容器有 `CAP_SYS_ADMIN` 且能 mount cgroup，就能把 `release_agent` 指向 host 上的惡意 binary，觸發 host 執行任意指令。

```bash
# 這個攻擊需要的條件：
# 1. 容器有 CAP_SYS_ADMIN
# 2. cgroupv1 可用
# 3. 容器不在 user namespace 保護下
```

### 最小權限實戰範例

```bash
# 跑一個只需要 bind port 80 的 web server
# 不需要 chown、mknod、net_raw 等
docker run \
  --cap-drop ALL \
  --cap-add NET_BIND_SERVICE \
  --security-opt no-new-privileges \
  --read-only \
  --user 1000:1000 \
  -p 80:80 \
  nginx:alpine
```

`--security-opt no-new-privileges`：防止容器內的 process 透過 `execve` suid binary 提升權限，是一個幾乎沒有副作用但很有效的防禦。

---

## 自我檢核

- [ ] 能說清楚 Linux capabilities 解決了傳統 root/non-root 二元模型的哪個問題
- [ ] 能列出 Docker 預設給的 capability 裡至少 5 個，並說明各自的用途
- [ ] 知道 `--privileged` 的實際意義（不只是「很危險」，要知道它具體做了什麼）
- [ ] 能看懂 seccomp profile JSON 的 defaultAction / syscalls / args 結構
- [ ] 知道 `--security-opt seccomp=unconfined` 在什麼場景下必要
- [ ] 能說出 CVE-2019-5736 或 CVE-2022-0492 的攻擊條件（不需要完整 exploit，能說出前提條件）
- [ ] 能寫出一個使用最小 capability 的 `docker run` 指令

capabilities 和 seccomp 是跑起來之後的安全邊界，build 的過程也有很多可以優化的地方，最重要的是縮小 image size 和 build 時間。

→ [練習 A：從零手刻最小容器](./practice-a-minimal-container.md)
