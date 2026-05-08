# Ch 8 — containerd 與 runc：容器運行時的層次

> 目標：理解 Docker 背後從 dockerd 到實際 process 的完整呼叫鏈，知道每一層的職責，並能不靠 Docker，直接用 runc 跑一個容器。

---

## Container Runtime 層次

`docker run` 按下去之後，發生的事情比你想像的多：

```
+------------------+
|   docker CLI     |  <- 你下指令的地方，只是個 HTTP client
+--------+---------+
         | Unix socket /var/run/docker.sock
         v
+------------------+
|    dockerd       |  <- Docker daemon，處理 API、network、volume
+--------+---------+
         | gRPC
         v
+------------------+
|   containerd     |  <- CNCF 專案，管 image / snapshot / container lifecycle
+--------+---------+
         | 每個容器 fork 一個
         v
+------------------+
| containerd-shim  |  <- 「保姆」，讓 runc 退出後容器繼續活著
+--------+---------+
         | exec
         v
+------------------+
|      runc        |  <- OCI runtime 參考實作，呼叫 clone()/pivot_root()/execve()
+--------+---------+
         | 設好 namespace / cgroup / rootfs 後 execve
         v
+------------------+
| container process|  <- 你的 nginx / python / bash
+------------------+
```

每層職責：

| 元件 | 職責 | 常駐？ |
|------|------|--------|
| docker CLI | 解析指令，呼叫 dockerd REST API | 否（每次執行後退出） |
| dockerd | 管理 network、volume、log、high-level API | 是 |
| containerd | image pull/push、snapshot 管理、container lifecycle | 是 |
| containerd-shim | 持有 container stdio，讓 containerd 重啟後容器不死 | 每個容器一個 |
| runc | 實際設定 namespace、cgroup、rootfs，然後 exec 容器 process | 否（container 啟動後退出） |
| container process | 你的應用 | 是（這就是容器的 PID 1） |

---

## containerd：比 dockerd 更底層的守護程序

containerd 是 CNCF（Cloud Native Computing Foundation）的畢業專案，Docker 在 2017 年把它捐出去，現在 Kubernetes 也直接用 containerd 而不需要 dockerd。

**containerd 做的事**：

- **Image management（映像管理）**：pull / push / unpack layer tar，管理 image metadata
- **Snapshot management（快照管理）**：用 OverlayFS 建立每個 container 的 upperdir
- **Container lifecycle（容器生命週期）**：create / start / stop / delete，轉交給 shim + runc 執行
- **Content store**：儲存 layer blob，位置在 `/var/lib/containerd/`

containerd 暴露 gRPC API，dockerd 是它的一個 client。你也可以直接用 `ctr` 或 `nerdctl` 操作 containerd，不需要 dockerd。

```bash
# 查看 containerd 版本
containerd --version

# 用 ctr 列出 images（containerd 原生 CLI）
ctr images ls

# containerd 的 namespace（和 Linux namespace 不同概念，這是 containerd 內部的隔離）
ctr namespaces ls
# NAME    LABELS
# moby           <- docker 用這個 namespace
# k8s.io         <- kubelet 用這個
```

---

## containerd-shim：容器的保姆

runc 是一次性的，它設好隔離環境、exec 容器 process 後就退出了。但容器的 stdio（stdin/stdout/stderr）需要有人持有，`docker logs` 才能讀到。

containerd-shim 就是那個「持有 stdio、等 container 退出、回報 exit code」的常駐 process。

```
如果沒有 shim：
  containerd 重啟 -> 所有容器的 stdio 全部斷掉 -> 容器 process 收到 SIGHUP -> 可能全死

有了 shim：
  containerd 重啟 -> shim 還活著 -> stdio 繼續 -> 容器 process 完全不受影響
```

每個 running container 對應一個 shim process：

```bash
ps aux | grep containerd-shim
# root  1234  containerd-shim-runc-v2 -namespace moby -id <container_id> ...
```

---

## runc：OCI runtime 的參考實作

runc 是 OCI（Open Container Initiative，開放容器倡議）Runtime Specification 的參考實作，用 Go 寫的，實際執行這些 Linux syscall：

```
runc run 時的呼叫序列：

1. clone(CLONE_NEWPID | CLONE_NEWNS | CLONE_NEWNET | ...)
   -> 建立新的 namespace（見 Ch 5）

2. mount("overlay", ...) + pivot_root()
   -> 設定 OverlayFS rootfs（見 Ch 7）

3. cgroupv2 / cgroupv1 設定
   -> 套用資源限制（見 Ch 6）

4. capset() + prctl(PR_SET_SECCOMP, ...)
   -> 設定 capability 和 seccomp（見 Ch 9）

5. execve("/bin/sh", ...)
   -> 執行容器的 entrypoint，runc 本身退出
```

---

## OCI Image Spec

OCI（Open Container Initiative）定義了 image 格式，讓不同的 runtime（runc、gVisor、Kata）都能跑同一個 image。

一個 OCI image 包含：

```
<image>/
├── blobs/
│   └── sha256/
│       ├── <config_hash>    <- config.json：environment、entrypoint、labels
│       └── <layer_hash>     <- 各個 layer 的 tar.gz
├── index.json               <- manifest list（多平台 index）
└── oci-layout               <- 版本標記
```

`config.json`（runc 實際讀的設定檔）結構：

```json
{
  "process": {
    "user": {"uid": 0, "gid": 0},
    "args": ["/bin/sh"],
    "env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
    "cwd": "/"
  },
  "root": {"path": "rootfs", "readonly": false},
  "mounts": [
    {"destination": "/proc", "type": "proc", "source": "proc"},
    {"destination": "/dev", "type": "tmpfs", "source": "tmpfs", "options": ["nosuid","strictatime"]}
  ],
  "linux": {
    "namespaces": [
      {"type": "pid"}, {"type": "network"}, {"type": "ipc"},
      {"type": "uts"}, {"type": "mount"}
    ],
    "resources": {
      "memory": {"limit": 134217728}
    }
  }
}
```

---

## 動手用 runc 跑容器（不靠 Docker）

以下需要 root + 已安裝 runc（`apt install runc` 或從 GitHub releases 下載）。

```bash
# step 1：準備 rootfs（從 Docker 匯出 busybox 的 filesystem）
mkdir -p /tmp/mycontainer/rootfs
docker export $(docker create busybox) | tar -C /tmp/mycontainer/rootfs -xf -

# 確認 rootfs 有東西
ls /tmp/mycontainer/rootfs
# bin  dev  etc  home  root  tmp  usr  var

# step 2：生成預設 config.json
cd /tmp/mycontainer
runc spec
# 產生 config.json

# step 3：（可選）調整 config.json，改掉 terminal: true 避免 tty 問題
# 把 "terminal": true 改成 "terminal": false，或直接跑互動模式

# step 4：跑起來
runc run mycontainer
# 進入容器的 sh

# 另一個 terminal 確認
runc list
# ID           PID   STATUS    BUNDLE              CREATED
# mycontainer  5678  running   /tmp/mycontainer    ...

# 退出容器後清理
runc delete mycontainer
rm -rf /tmp/mycontainer
```

這個流程和 Docker 做的完全相同，只是少了 dockerd、containerd、shim 這幾層的包裝。

---

## Alternative Runtimes：gVisor 與 Kata Containers

runc 的隔離是 Linux kernel namespace，所有容器共用同一個 kernel。如果 kernel 本身有漏洞，容器逃逸就可能拿到 host 權限。

| Runtime | 隔離機制 | 效能 | 適用場景 |
|---------|---------|------|---------|
| runc | Linux namespace + seccomp | 最好 | 一般生產環境 |
| gVisor (runsc) | 用戶空間 kernel（Go 實作），攔截 syscall | 中等 | 多租戶、不信任的工作負載 |
| Kata Containers | 每個容器一個輕量 VM（QEMU/Firecracker） | 略差 | 金融、高安全需求 |

使用方式：只要替換 OCI runtime，containerd / Docker 的其他部分完全不用改。

```bash
# Docker 指定 runtime
docker run --runtime=runsc ubuntu:22.04 uname -a
# 會看到 gVisor 的 kernel 版本而非 host kernel

# containerd 設定 runtime（/etc/containerd/config.toml）
# [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata]
#   runtime_type = "io.containerd.kata.v2"
```

---

## 自我檢核

- [ ] 能默寫 dockerd -> containerd -> shim -> runc -> container process 這條鏈，並說出每層的職責
- [ ] 知道 containerd-shim 為什麼必要（containerd 重啟時容器不死）
- [ ] 知道 runc 實際呼叫了哪些 Linux syscall（clone / pivot_root / execve）
- [ ] 知道 OCI Image Spec 的 config.json 裡 process / mounts / linux 三個區塊各放什麼
- [ ] 能用 runc 不靠 Docker 跑起一個容器
- [ ] 知道 gVisor 和 Kata Containers 分別用什麼機制提供更強隔離

容器已經起來了，但它預設有哪些 Linux 權限？哪些 syscall 被封鎖了？這些是容器安全的核心問題。

→ [Ch 9 Capabilities 與 seccomp](./09-capabilities-seccomp.md)
