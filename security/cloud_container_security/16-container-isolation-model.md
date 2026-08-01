# Ch 16 — 容器隔離的安全模型：從攻擊視角看 namespace/cap

> **目標**：從攻擊者角度拆解容器（container）的隔離機制——namespace、cgroup、capabilities、seccomp、LSM——搞清楚每一層隔離的**邊界在哪裡、不隔離什麼**，理解「容器不是 VM」這句話對攻擊面的真實含義，並掌握進入容器後第一件事：判斷自己身處什麼隔離等級。

---

Part 2 打完了雲端服務攻擊面。現在切換戰場：**容器**。你在 docker 課已經學過怎麼用容器，這裡不重教那些。Ch 16 的目的是從零建立一個攻擊者的隔離模型，讓你之後在 Ch 17–18 做逃逸時，清楚知道自己在突破哪一層、哪一層擋得住你、哪一層根本就沒有在擋。

---

## 為什麼需要重新看隔離模型

你學過 Docker。你知道 `docker run` 會把 process 放進隔離的環境。但「隔離」是個模糊詞，攻擊視角需要更精確的問題：

**這個隔離擋得住什麼攻擊？擋不住什麼？**

容器的隔離機制是堆疊起來的，每一層有不同目的、不同強度、不同繞過面：

- **namespace** — 讓容器裡的 process 看不到宿主上的其他 process/網路/掛載點
- **cgroup** — 限制 CPU/記憶體/IO 用量，跟安全無直接關係
- **capabilities** — 容器跑在縮減的特權子集裡，但不是零
- **seccomp** — syscall 白名單/黑名單過濾器
- **LSM（AppArmor/SELinux）** — MAC（Mandatory Access Control，強制存取控制）策略層

這五層不是同一個東西。攻擊者需要分開看每一層，因為每一層的失守條件完全不同。

---

## 先建直覺：容器和 VM 的根本差異

做逃逸前要先把這張圖刻進腦子裡：

```
VM 隔離模型：
┌──────────────────────────────────────────────────────────────┐
│  Hardware                                                    │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Hypervisor                                            │  │
│  │  ┌────────────────────────┐  ┌──────────────────────┐  │  │
│  │  │  Guest OS (kernel A)   │  │  Guest OS (kernel B) │  │  │
│  │  │  ┌──────────────────┐  │  │  ┌────────────────┐  │  │  │
│  │  │  │  App / process   │  │  │  │  App / process │  │  │  │
│  │  │  └──────────────────┘  │  │  └────────────────┘  │  │  │
│  │  └────────────────────────┘  └──────────────────────┘  │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘

每個 VM 有自己的 kernel，kernel bug 要先穿透 hypervisor 才能影響其他 VM。

容器隔離模型：
┌──────────────────────────────────────────────────────────────┐
│  Hardware                                                    │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Host OS kernel（所有容器共用同一個）                   │  │
│  │                                                        │  │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │  │
│  │  │  namespace A │ │  namespace B │ │  namespace C │   │  │
│  │  │  cgroup A    │ │  cgroup B    │ │  cgroup C    │   │  │
│  │  │  container 1 │ │  container 2 │ │  container 3 │   │  │
│  │  └──────────────┘ └──────────────┘ └──────────────┘   │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘

容器只是 kernel 的「視角限制」，沒有真正的邊界。
kernel 有 bug？三個容器都暴露。
```

VM 裡的 process 打穿 guest kernel 也只是在沙盒裡，還需要打 hypervisor 才能影響宿主。容器裡的 process 打到 kernel，**就直接打到 host**。這是容器逃逸比 VM 逃逸頻繁、成本低的根本原因。

---

## 底層機制：六種 namespace 各自隔離什麼

Linux namespace（命名空間）是容器隔離的骨幹。每種 namespace 控制一個資源的「視角」。

### PID namespace（程序 ID 命名空間）

容器內的 PID 1 不是宿主的 PID 1，是自己命名空間裡的 PID 1。從容器內看 `/proc`，只能看到同命名空間的 process。

**不隔離什麼**：如果有 `SYS_PTRACE` capability，容器內的 process 仍然可以 ptrace 宿主 process（如果 PID namespace 沒完全分離）。`--pid=host` 選項會讓容器直接用宿主的 PID namespace，完全沒有隔離。

### Network namespace（網路命名空間）

容器有自己的網路介面、路由表、iptables 規則。預設容器看不到宿主的 `eth0`、`lo` 之外的介面。

**不隔離什麼**：`--network=host` 讓容器共用宿主 network namespace，這是最常見的高風險配置之一。容器此時可以 bind 宿主任意 port，也可以 sniff 宿主流量。

### Mount namespace（掛載命名空間）

容器有自己的 rootfs 和掛載樹，看不到宿主的 `/etc`、`/var` 等。

**不隔離什麼**：如果 `docker run -v /:/host` 把宿主根目錄掛進來，namespace 照樣存在，但攻擊者已經有 host rootfs 的讀寫能力。namespace 擋的是「看不到」，不是「寫不到已掛進來的東西」。

### User namespace（使用者命名空間）

這是最微妙、也最重要的一個。User namespace 讓容器內的 UID 0（root）對應到宿主上的非特權 UID（比如 UID 1000）。這樣容器內的「root」在宿主上沒有特權。

**不隔離什麼**：**Docker 預設不啟用 user namespace remapping**。預設情況下，容器內的 UID 0 就是宿主的 UID 0。這是關鍵事實——後面會反覆碰到它。

確認 user namespace 是否啟用：
```bash
# 在宿主上
docker info | grep -i "userns"
# 如果沒有輸出 userns-remap 相關內容，代表沒開
```

### IPC namespace（程序間通訊命名空間）

隔離 System V IPC（shared memory、semaphore、message queue）和 POSIX message queue。防止容器存取宿主或其他容器的 shared memory。

**不隔離什麼**：`--ipc=host` 讓容器共用宿主 IPC namespace，可以存取宿主的 shared memory 區段。某些高效能場景（GPU 運算）會這樣用，是個安全陷阱。

### UTS namespace（主機名稱命名空間）

讓每個容器有自己的 hostname 和 domainname。

**不隔離什麼**：UTS namespace 本身影響不大，但如果沒有 UTS namespace（`--uts=host`），容器可以呼叫 `sethostname()` 修改宿主的 hostname。

### Cgroup namespace（控制組命名空間）

讓容器看到的 `/sys/fs/cgroup` 路徑是自己的子樹根，而不是宿主的完整 cgroup 樹。

---

## Cgroup：資源限制，不是安全邊界

Cgroup（control group，控制組）常被誤解為安全機制。它不是。

Cgroup 做的事：
- 限制 CPU 使用量（`--cpus=2.0`）
- 限制記憶體上限（`--memory=512m`）
- 限制磁碟 I/O 速率
- 限制 process 數量（`pids.max`）

Cgroup 不做的事：
- 不阻止 process 存取 kernel API
- 不阻止 syscall
- 不阻止 file 存取（那是 namespace + DAC/MAC 的工作）
- `pids.max` 可以限制 fork bomb，但擋不住橫向 exploit

對攻擊者的意義：如果你在容器裡成功 exploit，cgroup 不會阻止你做任何事。它頂多限制你能用多少 CPU 打暴力破解，但那通常不是容器逃逸的手段。

---

## Capabilities：縮減的特權子集

Linux 把傳統的 root「全能」拆成 ~40 個獨立的 capability（能力）。容器預設跑在其中的一個子集上，而不是完整的 capability set。

Docker 預設給容器的 capability（14 個，刻意背下來）：
```
CHOWN, DAC_OVERRIDE, FSETID, FOWNER, MKNOD, NET_RAW,
SETGID, SETUID, SETFCAP, SETPCAP, NET_BIND_SERVICE,
SYS_CHROOT, KILL, AUDIT_WRITE
```

攻擊者最感興趣的 capability，以及它們帶來的逃逸面：

| Capability | 能做什麼 | 逃逸相關度 |
|-----------|---------|-----------|
| `SYS_ADMIN` | mount、clone namespace、設定 cgroup 等 | 非常高，基本等同 root |
| `SYS_PTRACE` | ptrace 任意 process | 高，可以 attach 到宿主 process |
| `SYS_MODULE` | 載入 kernel module | 極高，直接打 kernel |
| `NET_ADMIN` | 修改路由、iptables | 中，可做網路攻擊 |
| `DAC_READ_SEARCH` | 繞過 file permission 讀任意檔 | 中高，可讀 host file |
| `CAP_SETUID` | 改 UID，包括設成 0 | 高，配合其他條件提權 |

`--privileged` 旗標給的不只是所有 capability，它還會：
1. 關閉 seccomp 過濾器
2. 關閉 AppArmor profile
3. 掛載宿主的 `/dev`，讓容器可以存取宿主的 block device

`--privileged` 容器等同於有根的殼，Ch 17 會展示多種具體逃逸手法。

---

## Seccomp：syscall 過濾器

Seccomp（Secure Computing，安全運算模式）讓 kernel 在 process 呼叫 syscall 時先過一道 BPF 過濾器，符合規則的允許，不符合的拒絕（通常是 EPERM 或 SIGSYS）。

Docker 預設的 seccomp profile 封鎖了約 44 個 syscall，包括：
- `kexec_load`、`kexec_file_load` — 載入新 kernel
- `create_module`、`init_module`、`finit_module` — 載入 kernel module
- `ptrace` — 預設封鎖（但 CAP_SYS_PTRACE 可以繞過 seccomp 的 ptrace 限制）
- `mount` — 掛載 filesystem
- `clock_settime` — 改系統時間
- `acct` — process accounting

對攻擊者的意義：如果你在容器裡找到一個需要 `init_module` 的提權手法，seccomp 會在 syscall 層就把它擋掉，不讓你碰到 kernel 的 module loading 邏輯。但 seccomp 是黑名單模式，漏掉的 syscall 就能用——而且 `--privileged` 直接停用它。

自訂或停用 seccomp：
```bash
# 停用 seccomp（不推薦，但某些偵錯情境用）
docker run --security-opt seccomp=unconfined ...

# 使用自訂 profile
docker run --security-opt seccomp=/path/to/my-profile.json ...
```

---

## LSM：最後一道 MAC 層

LSM（Linux Security Module，Linux 安全模組）是 kernel 內部的 hook 框架，AppArmor 和 SELinux 都掛在上面。

**AppArmor**（Ubuntu 預設）：以 profile 形式限制 process 能存取哪些路徑、能執行哪些操作。Docker 的預設 AppArmor profile（`docker-default`）會阻止：
- mount filesystem
- 寫入 `/proc/sys`、`/sys`（部分路徑）
- ptrace 其他 process

**SELinux**（RHEL/CentOS 預設）：基於 label 的 MAC，每個 process 和 file 都有 type label，policy 定義哪個 type 可以存取哪個 type。Docker 容器 process 預設跑在 `container_t` type，被限制只能存取 `container_file_t` label 的 file。

對攻擊者的意義：LSM 在 namespace + capability + seccomp 都過了之後再擋一層。但 LSM 的 profile/policy 必須被啟用且正確設定——雲端環境很多節點因為相容性問題把 AppArmor profile 設成 unconfined，或把 SELinux 設成 permissive（只記錄不阻擋）。

---

## 容器內的 root vs 宿主的 root

這是整個隔離模型最重要的一個問題：**容器裡的 UID 0 是真的 root 嗎？**

答案取決於 user namespace 的設定：

| 情況 | 容器 UID 0 | 宿主 UID | 逃逸後後果 |
|-----|-----------|---------|----------|
| 預設 Docker（無 userns-remap）| root | root（UID 0）| 逃逸即得 host root |
| 啟用 userns-remap | root（in NS）| 非特權 UID（如 165536）| 逃逸後得到宿主普通用戶 |
| rootless Docker | root（in NS）| 啟動 Docker 的用戶 UID | 逃逸後得到普通用戶 |

**預設 Docker 不啟用 userns-remap**，這意味著：如果你在預設配置的容器裡得到了 UID 0，並且找到任何能影響宿主 filesystem 的路徑（掛載、device 存取、kernel module），你就是宿主 root。

這是容器逃逸之所以「值錢」的原因——從容器逃到宿主通常直接得到 root，不像 VM 逃逸後還需要在宿主 OS 再提一次權。

---

## 攻擊者怎麼偵測自己在容器裡

進入一個 shell 後，第一步是確認自己的環境。以下是確認在容器內的標準手法：

### 1. 檢查 `/.dockerenv`

Docker 在容器裡會建這個空檔案：

```bash
ls /.dockerenv
# /.dockerenv
# 存在 = Docker 容器的強烈指標
```

這個檔案在 runc 建立容器時由 Docker daemon 寫入，幾乎所有 Docker 容器都有。Podman 等其他 container runtime 預設不建這個。

### 2. 檢查 `/proc/1/cgroup`

容器的 PID 1 的 cgroup 路徑會帶有容器 ID：

```bash
cat /proc/1/cgroup
# 預期輸出（Docker 容器）：
# 12:blkio:/docker/7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a
# 11:memory:/docker/7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a
# 10:cpu,cpuacct:/docker/7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a
# ...（64 個字元的 container ID）
```

路徑中的那串 hex 就是 container ID。在 Kubernetes 環境下，路徑格式不同：

```bash
cat /proc/1/cgroup
# K8s 環境下的典型輸出：
# 12:blkio:/kubepods/besteffort/pod8a7b6c5d-4e3f-2a1b-0c9d-8e7f6a5b4c3d/7f8a9b2c3d4e5f6a
# 格式：/kubepods/<qos>/<pod-uid>/<container-id>
```

這個資訊除了確認「在容器裡」，還直接洩漏了 container ID 和（K8s 情況下的）Pod UID，有助於後續攻擊定位。

### 3. 用 `capsh` 檢查 capabilities

```bash
capsh --print
# 預期輸出（預設 Docker 容器，非 privileged）：
# Current: = cap_chown,cap_dac_override,cap_fsetid,cap_fowner,cap_mknod,\
#   cap_net_raw,cap_setgid,cap_setuid,cap_setfcap,cap_setpcap,\
#   cap_net_bind_service,cap_sys_chroot,cap_kill,cap_audit_write+eip
# Bounding set =cap_chown,cap_dac_override,cap_fsetid,cap_fowner,cap_mknod,\
#   cap_net_raw,cap_setgid,cap_setuid,cap_setfcap,cap_setpcap,\
#   cap_net_bind_service,cap_sys_chroot,cap_kill,cap_audit_write
# Securebits: 00/0x0/1'b0
# uid=0(root) gid=0(root)
```

對比 `--privileged` 容器的輸出：

```bash
capsh --print
# privileged 容器的輸出：
# Current: = cap_chown,cap_dac_override,cap_dac_read_search,cap_fsetid,\
#   cap_fowner,cap_kill,cap_setgid,cap_setuid,cap_setpcap,cap_linux_immutable,\
#   cap_net_bind_service,cap_net_broadcast,cap_net_admin,cap_net_raw,\
#   cap_ipc_lock,cap_ipc_owner,cap_sys_module,cap_sys_rawio,cap_sys_chroot,\
#   cap_sys_ptrace,cap_sys_pacct,cap_sys_admin,cap_sys_boot,cap_sys_nice,\
#   cap_sys_resource,cap_sys_time,cap_sys_tty_config,cap_mknod,cap_lease,\
#   cap_audit_write,cap_audit_control,cap_setfcap,cap_mac_override,\
#   cap_mac_admin,cap_syslog,cap_wake_alarm,cap_block_suspend,cap_audit_read+eip
# Bounding set =（同上，完整 38 個）
```

一看 Bounding set 的長度就能分辨。預設容器 14 個，`--privileged` 是完整集合（38個左右，隨 kernel 版本增減）。

### 4. 直接讀 `/proc/self/status` 裡的 Capabilities

```bash
cat /proc/self/status | grep -i cap
# 輸出：
# CapInh: 0000000000000000
# CapPrm: 00000000a80425fb
# CapEff: 00000000a80425fb
# CapBnd: 00000000a80425fb
# CapAmb: 0000000000000000
```

這幾個 hex 值是 bitmap，每個 bit 對應一個 capability。`a80425fb` 轉成二進位就能數出有哪幾個 bit 被設。`capsh --decode=00000000a80425fb` 可以直接解碼：

```bash
capsh --decode=00000000a80425fb
# 輸出：
# 0x00000000a80425fb=cap_chown,cap_dac_override,cap_fsetid,cap_fowner,\
#   cap_mknod,cap_net_raw,cap_setgid,cap_setuid,cap_setfcap,cap_setpcap,\
#   cap_net_bind_service,cap_sys_chroot,cap_kill,cap_audit_write
```

如果看到 CapEff 是 `000001ffffffffff`（全 1），代表跑在 privileged 模式。

### 5. hostname 和 `/proc` 的異常

```bash
hostname
# 輸出類似：7f8a9b2c3d4e（就是容器 ID 的前 12 個字元）
# 正常機器的 hostname 是人類可讀的名稱

ls /proc/ | wc -l
# 容器裡的數字遠少於宿主（宿主可能有幾百個 PID 對應的目錄，容器可能只有個位數）
```

---

## 一個失敗案例：seccomp 擋住的逃逸嘗試

以下展示 seccomp 實際生效的情況——這在學 Ch 17 的繞過前要先有感覺：

```bash
# 在預設 Docker 容器內，嘗試掛載 proc（需要 mount syscall）
mount -t proc proc /tmp/hostproc
# 輸出：
# mount: /tmp/hostproc: permission denied.

# 用 strace 看實際的 syscall 結果
strace mount -t proc proc /tmp/testproc 2>&1 | grep -E "mount|eperm"
# 輸出：
# mount("proc", "/tmp/testproc", "proc", 0, NULL) = -1 EPERM (Operation not permitted)

# 嘗試載入 kernel module（init_module 被 seccomp 封鎖）
# 先建一個空 module：
# insmod /tmp/test.ko
# 輸出：
# insmod: ERROR: could not insert module /tmp/test.ko: Operation not permitted

strace insmod /tmp/test.ko 2>&1 | grep -E "init_module|finit"
# 輸出：
# finit_module(3, "", 0)       = -1 EPERM (Operation not permitted)
# EPERM 來自 seccomp，kernel 甚至不看 module 內容就回絕
```

EPERM 是 seccomp 和 capability 拒絕的通用錯誤碼，兩者都用它，strace 分不出是哪一層擋的。Ch 20 會深入這個除錯問題。

---

## 對比取捨表：各隔離機制一覽

| 機制 | 隔離目的 | 攻擊繞過難度 | 是否預設啟用（Docker）|
|-----|---------|------------|---------------------|
| PID namespace | process 可見性 | 低（`--pid=host` 直接關）| 是 |
| Net namespace | 網路介面隔離 | 低（`--network=host` 直接關）| 是 |
| Mount namespace | rootfs 隔離 | 低（volume 掛載繞過）| 是 |
| User namespace | UID 映射 | 中（userns-remap 預設未開）| **否（預設未啟用 remap）**|
| IPC namespace | IPC 物件隔離 | 低（`--ipc=host`）| 是 |
| UTS namespace | hostname 隔離 | 低（`--uts=host`）| 是 |
| Cgroup | 資源限制 | N/A（不是安全邊界）| 是 |
| Capabilities | 特權子集 | 中（`--cap-add` 逐個加回）| 是（縮減集合）|
| Seccomp | syscall 過濾 | 中（`--security-opt seccomp=unconfined`）| 是（預設 profile）|
| AppArmor | MAC 路徑控制 | 中（`--security-opt apparmor=unconfined`）| 是（Ubuntu 節點）|
| SELinux | MAC label 控制 | 高（正確設定時）| 是（RHEL/CentOS 節點）|

「攻擊繞過難度」指的是配置層面關掉這個機制有多容易，不是 exploit 的難度。實際上很多生產環境為了方便把上面多個機制手動關掉，Ch 17 的逃逸就從這裡下手。

---

## 踩雷集錦

**1. 以為 namespace 等於隔離**

Namespace 讓你「看不到」某些東西，但不代表「存取不到」。把宿主目錄用 volume 掛進容器，namespace 還在，但攻擊者可以讀寫那個目錄。很多人以為 namespace 是安全屏障，實際上是視角限制。

**2. 以為容器裡的 root 沒有宿主 root 的能力**

在預設 Docker（無 userns-remap）下，容器 UID 0 = 宿主 UID 0。如果你能從容器存取任何宿主資源（透過 device、kernel exploit、volume），你就是宿主 root。這個預設行為讓大量逃逸直接提權到宿主 root。

**3. Seccomp 和 capabilities 是互補的，不是一樣的**

有些 syscall 需要特定 capability 才能呼叫，有些被 seccomp 封鎖。關掉 seccomp 但保留縮減 capabilities，某些攻擊還是會被 capability check 擋住；反之亦然。要同時考慮兩層。

**4. `capsh` 不一定安裝，換用 `/proc/self/status`**

目標容器通常是精簡的生產 image，`capsh` 不在 PATH 裡。`/proc/self/status` 的 Cap* 欄位永遠都有，記得用 `cat /proc/self/status | grep Cap`，再手動解碼 hex。

**5. cgroup namespace 的路徑洩漏常被忽視**

`/proc/1/cgroup` 的路徑不只說明「在容器裡」，還洩漏 container ID、Kubernetes Pod UID、QoS class。這些資訊在後續打 container runtime API 或 kubelet 時可能有用，別忽略。

---

## 進階延伸

**runc 的 namespace 設定**：`docker inspect <container>` 的 `HostConfig` 裡有 `PidMode`、`NetworkMode`、`IpcMode`、`UTSMode`、`UsernsMode` 等欄位，直接看你面對的配置。在紅隊場景裡，如果你能存取 Docker socket，先 inspect 一遍目標容器。

**`/proc/self/ns/`**：每個 namespace 都有對應的 fd，`ls -la /proc/self/ns/` 可以看 inode number。比較容器的 ns inode 和宿主的 ns inode，inode 相同代表共用同一個 namespace（`--pid=host`、`--network=host` 等情況）。

**Capabilities 的繼承模型**：除了 Effective/Permitted/Inheritable/Bounding 四個集合，Linux 5.1 以後有 Ambient set，讓非 root process 也能繼承 capabilities。某些 capability 提升的手法利用 Ambient set，偵測時要連這個一起看。

**OCI Runtime Specification**：Docker/Kubernetes 的容器配置最終都轉成 OCI runtime spec（通常是 `config.json`），裡面明確列出 namespaces、capabilities、seccomp、mounts 的完整設定。在宿主上有 root 時，看 `/run/containerd/io.containerd.runtime.v2.task/<namespace>/<container-id>/config.json` 可以完整重建攻擊面。

---

## 本章重點整理

- 容器隔離靠五層堆疊：namespace（視角限制）、cgroup（資源限制）、capabilities（特權子集）、seccomp（syscall 過濾）、LSM（MAC 層）
- Namespace 隔離的是「看得到什麼」，不是「能做什麼」；每種 namespace 都有對應的「關掉它」選項
- Cgroup 不是安全機制，只管資源用量
- 容器預設拿到 14 個 capability 的子集；`--privileged` 給完整集合並停用 seccomp 和 AppArmor
- Docker 預設不啟用 user namespace remapping，容器 UID 0 = 宿主 UID 0，逃逸即得 host root
- 進入容器後，依序檢查：`/.dockerenv`、`/proc/1/cgroup`、`capsh --print`（或 `/proc/self/status`）確認環境
- 容器不是 VM：共用 host kernel 代表 kernel 漏洞直接影響所有容器；逃逸成本比 VM 逃逸低

---

## 自我檢核

1. 在預設 Docker（無 userns-remap）的容器裡，你是 UID 0，container 的 mount namespace 和宿主不同，但有一個 volume `-v /etc:/host-etc`。你能改 `/host-etc/passwd` 嗎？為什麼？
2. `--network=host` 讓容器和宿主共用哪個 namespace？這個配置開了之後，容器內的攻擊者可以做到哪些在隔離網路下做不到的事？
3. `capsh --print` 輸出 Bounding set 有 38 個 capability，你在哪個模式下？
4. 你 `strace` 一個失敗的 `insmod` 看到 `EPERM`，怎麼判斷是 seccomp 擋的還是 capability 不夠？（提示：`/proc/self/status` 和 `--security-opt`）
5. 為什麼 `/.dockerenv` 的存在不能 100% 確認你在 Docker 容器裡？哪些情況下它可能不存在，或者存在但你不在 Docker 容器裡？

---

## 延伸閱讀

- [Linux man-pages: namespaces(7)](https://man7.org/linux/man-pages/man7/namespaces.7.html) — 七種 namespace 的官方文件，每個 namespace 的語義和 flag 都在這裡定義，查邊界行為的第一手來源
- [Docker 官方：Security — AppArmor security profiles](https://docs.docker.com/engine/security/apparmor/) — 包含預設 profile 的完整內容，以及如何自訂；對照 Ch 20 的繞過技術時必看
- [NCC Group: Understanding and Hardening Linux Containers](https://research.nccgroup.com/wp-content/uploads/2020/07/ncc_group_understanding_hardening_linux_containers-1-1.pdf) — 容器安全模型最完整的白皮書之一，涵蓋本章所有機制的深度分析
- [Jessie Frazelle: Container Security: Fundamental Technology Concepts that Protect Containerized Applications](https://www.oreilly.com/library/view/container-security/9781492056690/) — O'Reilly 的容器安全書，本章內容對應前三章，逃逸部分對應後續章節
- [Linux capabilities(7) man page](https://man7.org/linux/man-pages/man7/capabilities.7.html) — 每個 capability 的確切定義，Ch 17 需要反覆對照這裡

---

隔離模型清楚了，下一步是實際打破它。Ch 17 會從最常見、成功率最高的逃逸手法開始：privileged 容器、危險 volume 掛載、capability 濫用——這些都是你在真實 K8s 集群裡最容易碰到的配置錯誤。

→ [Ch 17 — 容器逃逸（一）：privileged / 掛載 / capabilities / device](./17-container-escape-1.md)
