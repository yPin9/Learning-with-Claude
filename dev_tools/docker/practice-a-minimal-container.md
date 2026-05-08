# 練習 A：從零手刻最小容器

整合章節：Ch 5（Linux Namespace）、Ch 6（cgroups）、Ch 7（OverlayFS）、Ch 8（containerd/runc）

---

## 背景

Docker 不是魔法，它只是把三件事包在一起：

1. **Namespace（命名空間）** — 隔離「看到的世界」（PID、網路、filesystem、hostname）
2. **cgroups（控制群組）** — 限制「能用多少資源」（CPU、記憶體、I/O）
3. **OverlayFS（疊加式檔案系統）** — 讓每個 container 有自己的 rootfs 副本，但不浪費磁碟

這個練習不碰 `docker run`，全部手動組裝，讓你親眼看到 Docker 底層在做什麼。

---

## 環境需求

- Linux kernel 5.x+（Ubuntu 22.04 / Debian 12 以上）
- 一般 user 可用 `unshare --user`，但 Part 2、3 建議有 root 或 sudo
- 需要安裝：`runc`、`docker`（僅用 `docker export`）、`overlay` kernel module 已載入

```bash
# 確認 overlay 已載入
lsmod | grep overlay

# 安裝 runc（Ubuntu）
sudo apt-get install -y runc

# 確認版本
runc --version
```

---

## Part 1：用 unshare 手刻 Namespace 隔離

### 題目規格

用 `unshare(1)` 建一個同時隔離 PID、UTS、Mount namespace 的環境：

1. 在隔離環境內把 hostname 改成 `myjail`
2. 確認 PID 1 是自己（`bash` 是 PID 1）
3. 在隔離環境內做一個 bind mount，把 `/tmp/host_data` 掛到 `/tmp/mnt_data`
4. 確認 host 上看不到這個 mount，也看不到修改後的 hostname

### 期望輸出

```
# 隔離環境內
hostname          →  myjail
echo $$           →  1
ps aux            →  只有你自己的 bash（+ps），少於 5 個 process
ls /tmp/mnt_data  →  host_data 的內容（readme.txt）

# host 上（另一個 terminal）
hostname          →  （原本的 hostname，不變）
mount | grep mnt_data  →  （空的，看不到）
```

### 實作步驟建議

1. 準備 host 上的資料目錄：
   ```bash
   sudo mkdir -p /tmp/host_data /tmp/mnt_data
   echo "hello from host" | sudo tee /tmp/host_data/readme.txt
   ```

2. 用 `unshare` 進入隔離環境：
   ```bash
   # 有 root（建議）：
   sudo unshare --pid --uts --mount --fork --mount-proc /bin/bash

   # 一般 user（較受限）：
   unshare --user --pid --uts --mount --fork --map-root-user --mount-proc /bin/bash
   ```

3. 進去後依序執行：
   ```bash
   hostname myjail
   hostname
   echo $$
   ps aux
   mount --bind /tmp/host_data /tmp/mnt_data
   ls /tmp/mnt_data
   ```

4. 開另一個 terminal 驗證 host 狀態：
   ```bash
   hostname
   mount | grep mnt_data
   ```

### 參考解答

<details>
<summary>點開參考實作</summary>

```bash
#!/bin/bash
# Part 1 完整腳本
set -e

# === Host 準備 ===
sudo mkdir -p /tmp/host_data /tmp/mnt_data
echo "hello from host" | sudo tee /tmp/host_data/readme.txt > /dev/null

HOST_HOSTNAME=$(hostname)
echo "Host hostname 是：$HOST_HOSTNAME"

# === 進入隔離環境，非互動式示範 ===
# --pid       : 隔離 PID namespace，隔離環境的第一個 process 是 PID 1
# --uts       : 隔離 UTS namespace，可改 hostname 不影響 host
# --mount     : 隔離 Mount namespace，mount/umount 不影響 host
# --fork      : 讓 unshare fork 一個 child，讓 child 成為 new pid ns 的 PID 1
# --mount-proc: 在新的 pid ns 裡重新掛 /proc（否則 ps 看到的是 host 的 process）

sudo unshare --pid --uts --mount --fork --mount-proc /bin/bash << 'INNER'
echo "=== 隔離環境內 ==="
hostname myjail
echo "hostname: $(hostname)"
echo "PID ($$): $$"
echo "--- ps aux ---"
ps aux
echo "--- bind mount ---"
mount --bind /tmp/host_data /tmp/mnt_data
ls /tmp/mnt_data
echo "--- /proc/mounts 確認 ---"
grep mnt_data /proc/mounts
INNER

echo "=== 回到 host ==="
echo "hostname: $(hostname)（應該還是 $HOST_HOSTNAME）"
echo "mount | grep mnt_data: $(mount | grep mnt_data || echo '(空的，看不到)')"
```

示範輸出：

```
Host hostname 是：ubuntu
=== 隔離環境內 ===
hostname: myjail
PID ($$): 1
--- ps aux ---
USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root         1  0.0  0.0   4628  3744 pts/0    S    10:00   0:00 /bin/bash
root         7  0.0  0.0   6408  2948 pts/0    R+   10:00   0:00 ps aux
--- bind mount ---
readme.txt
--- /proc/mounts 確認 ---
/dev/sda1 /tmp/mnt_data ext4 rw,relatime 0 0
=== 回到 host ===
hostname: ubuntu（應該還是 ubuntu）
mount | grep mnt_data: (空的，看不到)
```

**關鍵觀察**：

- `--fork --mount-proc` 缺一不可。少 `--fork` 則 bash 不是 PID 1；少 `--mount-proc` 則 `/proc` 還是 host 的，`ps` 看到所有 process。
- UTS namespace 讓 `hostname` 修改完全沙盒化，兩個 namespace 各自有自己的 hostname。
- Mount namespace 讓 `mount --bind` 只在本 namespace 可見，host 的 mount table 不受影響。
</details>

### 測試用例

```bash
HOST_HN=$(hostname)

# T1a：隔離環境的 hostname 可以改
sudo unshare --pid --uts --mount --fork --mount-proc /bin/bash -c \
  'hostname testjail; hostname' | grep -q testjail && echo "PASS T1a"

# T1b：host hostname 沒有改變
[ "$(hostname)" = "$HOST_HN" ] && echo "PASS T1b"

# T2：PID 1 是 bash 本身
sudo unshare --pid --uts --mount --fork --mount-proc /bin/bash -c \
  'echo $$' | grep -q '^1$' && echo "PASS T2"

# T3：ps 只看到極少數 process（少於 5 行）
COUNT=$(sudo unshare --pid --uts --mount --fork --mount-proc /bin/bash -c \
  'ps aux | wc -l')
[ "$COUNT" -lt 5 ] && echo "PASS T3" || echo "FAIL T3（看到 $COUNT 行）"
```

### 自我檢核

- [ ] 改 hostname 後，另一個 terminal 的 `hostname` 沒有改變
- [ ] `echo $$` 輸出 `1`
- [ ] `ps aux` 只有 `bash` 和 `ps` 兩個 process
- [ ] bind mount 在隔離環境可見，在 host 上 `mount | grep` 找不到
- [ ] 能解釋 `--fork` 和 `--mount-proc` 各自解決什麼問題

---

## Part 2：用 OverlayFS 建 rootfs

### 題目規格

1. 用 `docker export` 把 alpine 的 rootfs 導出到 `/tmp/alpine-rootfs/`
2. 建立 OverlayFS 所需目錄：`lower`（唯讀）、`upper`（寫入層）、`work`（內部用）、`merged`（掛載點）
3. 把 alpine rootfs 當 `lowerdir`，掛上 OverlayFS
4. 在 `merged` 裡建一個新檔案 `/merged/tmp/new_file.txt`
5. 確認：`upperdir` 有這個檔案，`lower`（alpine rootfs）裡沒有
6. 刪除 `merged` 裡的既有檔案（`/merged/etc/hostname`），確認 `upperdir` 出現 whiteout（白化）檔

### 期望輸出

```bash
# 掛好之後
ls /tmp/overlay/merged/etc/        # 看到 alpine 的 /etc 內容

# 建新檔後
ls /tmp/overlay/upper/tmp/         # new_file.txt（只在 upper 裡）
ls /tmp/alpine-rootfs/tmp/         # （空的，lower 沒變）

# 刪除既有檔案後
ls -la /tmp/overlay/upper/etc/hostname  # c---------（whiteout character device）
ls /tmp/overlay/merged/etc/hostname     # ls: No such file or directory
```

### 實作步驟建議

1. 匯出 alpine rootfs：
   ```bash
   sudo mkdir -p /tmp/alpine-rootfs
   CID=$(docker create alpine)
   docker export $CID | sudo tar -xC /tmp/alpine-rootfs/
   docker rm $CID
   ```

2. 建 OverlayFS 目錄：
   ```bash
   sudo mkdir -p /tmp/overlay/{upper,work,merged}
   ```

3. 掛載 OverlayFS：
   ```bash
   sudo mount -t overlay overlay \
     -o lowerdir=/tmp/alpine-rootfs,upperdir=/tmp/overlay/upper,workdir=/tmp/overlay/work \
     /tmp/overlay/merged
   ```

4. 操作並觀察：
   ```bash
   # 建新檔
   sudo mkdir -p /tmp/overlay/merged/tmp
   echo "I was created" | sudo tee /tmp/overlay/merged/tmp/new_file.txt

   # 刪除既有檔
   sudo rm /tmp/overlay/merged/etc/hostname
   ```

5. 卸載：
   ```bash
   sudo umount /tmp/overlay/merged
   ```

### 參考解答

<details>
<summary>點開參考實作</summary>

```bash
#!/bin/bash
# Part 2 完整腳本
set -e

ROOTFS=/tmp/alpine-rootfs
OVL=/tmp/overlay

# === Step 1：匯出 alpine rootfs ===
echo "[1] 匯出 alpine rootfs..."
sudo mkdir -p "$ROOTFS"
CID=$(docker create alpine)
docker export "$CID" | sudo tar -xC "$ROOTFS"/
docker rm "$CID"
echo "    rootfs 大小: $(sudo du -sh "$ROOTFS" | cut -f1)"

# === Step 2：建 overlay 目錄 ===
echo "[2] 建 OverlayFS 目錄..."
sudo mkdir -p "$OVL"/{upper,work,merged}

# === Step 3：掛載 OverlayFS ===
echo "[3] 掛載 OverlayFS..."
sudo mount -t overlay overlay \
  -o lowerdir="$ROOTFS",upperdir="$OVL/upper",workdir="$OVL/work" \
  "$OVL/merged"

echo "    merged 根目錄（前 10）:"
ls "$OVL/merged" | head -10

# === Step 4：建新檔（CoW 觸發）===
echo "[4] 在 merged/tmp 建新檔..."
sudo mkdir -p "$OVL/merged/tmp"
echo "I was created in overlay" | sudo tee "$OVL/merged/tmp/new_file.txt" > /dev/null

echo "    upper/tmp 內容（應有 new_file.txt）:"
ls "$OVL/upper/tmp/"
echo "    lower/tmp 內容（應為空）:"
sudo ls "$ROOTFS/tmp/" 2>/dev/null || echo "    (空的)"

# === Step 5：刪除既有檔案（whiteout）===
echo "[5] 刪除 merged/etc/hostname..."
sudo rm "$OVL/merged/etc/hostname"

echo "    upper/etc/hostname（whiteout）:"
sudo ls -la "$OVL/upper/etc/hostname"
# 輸出類似：c--------- 1 root root 0, 0 ... hostname

echo "    merged/etc/hostname（應已消失）:"
sudo ls "$OVL/merged/etc/hostname" 2>&1 || echo "    (已消失，符合預期)"

# === Step 6：清理 ===
echo "[6] 卸載 OverlayFS..."
sudo umount "$OVL/merged"
echo "完成。"
```

**OverlayFS 三種操作的本質**：

| 操作 | 發生什麼 |
|---|---|
| 讀取 lower 的檔案 | 直接讀 lower，upper 不動 |
| 修改 lower 的檔案 | CoW（Copy-on-Write）：先複製到 upper，再修改 upper |
| 建立新檔 | 直接寫到 upper |
| 刪除 lower 的檔案 | 在 upper 建 whiteout（character device 0,0）遮住 lower |

**確認 whiteout**：

```bash
# file type 是 c（character device），major/minor 都是 0
sudo stat --format="%F %t %T" /tmp/overlay/upper/etc/hostname
# 輸出：character special file 0 0
```

**workdir 的必要性**：OverlayFS 用 workdir 做 atomic rename，確保寫入 upper 是原子操作，避免 crash 後部分寫入損壞資料。

**為什麼 Docker 需要 OverlayFS**：每個 container 從同一個 image layer 出發，但各自的修改互不干擾，且不複製整個 image，磁碟用量極小。
</details>

### 測試用例

```bash
# T1：merged 能看到 alpine 的標準目錄
sudo ls /tmp/overlay/merged/bin /tmp/overlay/merged/etc /tmp/overlay/merged/usr > /dev/null 2>&1 \
  && echo "PASS T1"

# T2a：新檔只在 upper，不在 lower
sudo test -f /tmp/overlay/upper/tmp/new_file.txt && echo "PASS T2a"

# T2b：lower 沒有改變
sudo test ! -f /tmp/alpine-rootfs/tmp/new_file.txt && echo "PASS T2b"

# T3a：whiteout 是 character device（type c）
sudo test -c /tmp/overlay/upper/etc/hostname && echo "PASS T3a"

# T3b：merged 看不到被刪的檔案
sudo test ! -e /tmp/overlay/merged/etc/hostname && echo "PASS T3b"
```

### 自我檢核

- [ ] 能解釋 lower / upper / work / merged 各自的用途
- [ ] 修改 `merged` 裡的檔案後，lower 原始內容沒有改變
- [ ] 看得懂 whiteout 是什麼，為什麼刪除不能動 lower
- [ ] 能說明 CoW 在 OverlayFS 裡的具體動作
- [ ] 知道 Docker 為什麼用 OverlayFS 而不是直接複製 image

---

## Part 3：用 runc 跑完整容器

### 題目規格

1. 準備乾淨的 alpine rootfs 在 `/tmp/mycontainer/rootfs/`
2. 用 `runc spec` 產生預設的 `config.json`
3. 修改 `config.json`：
   - hostname 改成 `runc-demo`
   - 記憶體限制設為 64 MB
   - process args 改成 `["/bin/sh"]`
4. `runc run mycontainer` 跑起來，進到 shell
5. 在另一個 terminal `runc list` 確認狀態是 `running`
6. 驗證 cgroup 記憶體限制有效

### 期望輸出

```bash
# terminal 1（容器內）
/ # hostname
runc-demo
/ # cat /proc/1/cmdline | tr '\0' ' '
/bin/sh

# terminal 2（host 上）
sudo runc list
# ID            PID    STATUS    BUNDLE                    CREATED                          OWNER
# mycontainer   1234   running   /tmp/mycontainer          2024-01-01T00:00:00.000000000Z   root

# cgroup 記憶體限制（cgroup v2）
cat /sys/fs/cgroup/mycontainer/memory.max
67108864
```

### 實作步驟建議

1. 準備 rootfs：
   ```bash
   sudo mkdir -p /tmp/mycontainer/rootfs
   CID=$(docker create alpine)
   docker export $CID | sudo tar -xC /tmp/mycontainer/rootfs/
   docker rm $CID
   ```

2. 產生 spec：
   ```bash
   cd /tmp/mycontainer
   sudo runc spec
   ```

3. 修改 `config.json`（用 python3 或 jq）：
   ```bash
   sudo python3 -c "
   import json
   with open('/tmp/mycontainer/config.json') as f:
       c = json.load(f)
   c['hostname'] = 'runc-demo'
   c['process']['args'] = ['/bin/sh']
   c.setdefault('linux', {}).setdefault('resources', {})['memory'] = {'limit': 67108864}
   with open('/tmp/mycontainer/config.json', 'w') as f:
       json.dump(c, f, indent=4)
   "
   ```

4. 啟動容器：
   ```bash
   sudo runc run mycontainer
   ```

5. 另一個 terminal 確認：
   ```bash
   sudo runc list
   sudo runc state mycontainer
   ```

### 參考解答

<details>
<summary>點開參考實作</summary>

**完整設定腳本**：

```bash
#!/bin/bash
# Part 3 完整腳本
set -e

BUNDLE=/tmp/mycontainer

# === Step 1：準備 rootfs ===
echo "[1] 準備 rootfs..."
sudo mkdir -p "$BUNDLE/rootfs"
CID=$(docker create alpine)
docker export "$CID" | sudo tar -xC "$BUNDLE/rootfs/"
docker rm "$CID"

# === Step 2：產生 OCI Runtime Spec ===
echo "[2] 產生 runc spec..."
cd "$BUNDLE"
sudo runc spec
echo "    config.json 已產生"

# === Step 3：修改 config.json ===
echo "[3] 修改 config.json..."
sudo python3 << 'PYEOF'
import json

CONFIG = "/tmp/mycontainer/config.json"
with open(CONFIG) as f:
    cfg = json.load(f)

# 改 hostname
cfg["hostname"] = "runc-demo"

# 改 process args
cfg["process"]["args"] = ["/bin/sh"]

# 開啟 terminal（互動式）
cfg["process"]["terminal"] = True

# 設 memory limit（64 MB = 64 * 1024 * 1024）
linux = cfg.setdefault("linux", {})
resources = linux.setdefault("resources", {})
resources["memory"] = {
    "limit": 67108864,
    "reservation": 33554432
}

with open(CONFIG, "w") as f:
    json.dump(cfg, f, indent=4)

print("  hostname     ->", cfg["hostname"])
print("  process.args ->", cfg["process"]["args"])
print("  memory.limit ->", resources["memory"]["limit"], "(64 MB)")
PYEOF

echo "[4] 啟動容器（互動式）..."
echo "    請在容器內執行："
echo "      hostname"
echo "      cat /proc/1/cmdline | tr '\\0' ' '"
echo "      cat /sys/fs/cgroup/memory.max  # cgroup v2"
echo "    然後 exit 退出"
echo ""
sudo runc run mycontainer

# 容器退出後自動清理
echo "[5] 清理..."
sudo runc delete mycontainer 2>/dev/null || true
echo "完成。"
```

**config.json 關鍵欄位說明**：

```json
{
    "ociVersion": "1.0.2",
    "hostname": "runc-demo",
    "process": {
        "terminal": true,
        "args": ["/bin/sh"],
        "noNewPrivileges": true
    },
    "root": {
        "path": "rootfs",
        "readonly": true
    },
    "linux": {
        "resources": {
            "memory": {
                "limit": 67108864,
                "reservation": 33554432
            }
        },
        "namespaces": [
            {"type": "pid"},
            {"type": "network"},
            {"type": "ipc"},
            {"type": "uts"},
            {"type": "mount"}
        ],
        "maskedPaths": [
            "/proc/kcore",
            "/proc/latency_stats",
            "/proc/timer_list",
            "/proc/sched_debug",
            "/sys/firmware"
        ],
        "readonlyPaths": [
            "/proc/asound",
            "/proc/bus",
            "/proc/fs",
            "/proc/irq",
            "/proc/sys",
            "/proc/sysrq-trigger"
        ]
    }
}
```

**確認 cgroup 記憶體限制**：

```bash
# cgroup v2（現代 Ubuntu/Debian）
# 先找 PID
PID=$(sudo runc state mycontainer | python3 -c "import json,sys; print(json.load(sys.stdin)['pid'])")
# 找 cgroup 路徑
cat /proc/$PID/cgroup
# 輸出類似：0::/system.slice/mycontainer
# 然後
cat /sys/fs/cgroup/system.slice/mycontainer/memory.max
# 67108864

# cgroup v1（舊系統）
cat /sys/fs/cgroup/memory/mycontainer/memory.limit_in_bytes
# 67108864
```

**runc run 的執行流程**：

```
runc run mycontainer
  │
  ├─ 讀 config.json（OCI Runtime Spec）
  ├─ 建立 namespaces（pid, net, ipc, uts, mount）
  ├─ 設定 cgroup（memory.max = 67108864）
  ├─ chroot 到 rootfs/
  ├─ 掛載 /proc, /dev, /sys, /tmp
  ├─ 套用 maskedPaths / readonlyPaths
  ├─ drop capabilities（只保留 spec 裡列的）
  ├─ noNewPrivileges = true
  └─ exec /bin/sh（PID 1）
```

**runc 和 containerd 的分工**：

```
docker CLI
  └─ dockerd（Docker Engine）
       └─ containerd（管理 container lifecycle、image、snapshot）
            └─ runc（執行單一 container，遵循 OCI Runtime Spec）
```

runc 只管「啟動一個 container 並等它結束」，不管 image pull、log、network plugin。
</details>

### 測試用例

```bash
# T1：config.json 語法正確
sudo python3 -c "import json; json.load(open('/tmp/mycontainer/config.json'))" \
  && echo "PASS T1（JSON 格式正確）"

# T2：hostname 欄位已修改
sudo python3 -c "
import json
c = json.load(open('/tmp/mycontainer/config.json'))
assert c['hostname'] == 'runc-demo', f'hostname 是 {c[\"hostname\"]}'
print('PASS T2')
"

# T3：memory limit 已設定
sudo python3 -c "
import json
c = json.load(open('/tmp/mycontainer/config.json'))
limit = c['linux']['resources']['memory']['limit']
assert limit == 67108864, f'limit 是 {limit}'
print('PASS T3（memory.limit =', limit, '）')
"

# T4：rootfs 存在且有 alpine 結構
sudo test -d /tmp/mycontainer/rootfs/bin \
  && sudo test -d /tmp/mycontainer/rootfs/etc \
  && echo "PASS T4"
```

### 自我檢核

- [ ] 知道 OCI Runtime Spec（開放容器規範）是什麼，`config.json` 是它的 bundle 描述
- [ ] 能說出 `runc` 和 `containerd` 的分工
- [ ] cgroup memory limit 在 config.json 的路徑是 `linux.resources.memory.limit`
- [ ] 知道 `maskedPaths` 和 `readonlyPaths` 各自保護什麼
- [ ] 知道為什麼 `noNewPrivileges: true` 要加（防止容器內的 binary 用 setuid 提權）

---

## 三個 Part 的整體連結

```
Part 1（unshare）              Part 2（OverlayFS）             Part 3（runc）
────────────────               ──────────────────              ─────────────
手動建 namespace          →    手動建 rootfs 層            →   OCI spec 整合三者
理解 pid/uts/mount ns          理解 lower/upper/merged          用標準介面執行容器
看到隔離的實際效果              看到 CoW 和 whiteout             理解 containerd 底層
```

Docker 在你執行 `docker run` 時，做的就是這三件事的自動化版本，再加上 image pull、network 設定、volume mount、log driver。

---

下一個練習：[練習 B：FastAPI + PostgreSQL + Redis + Nginx](./practice-b-compose-stack.md)
