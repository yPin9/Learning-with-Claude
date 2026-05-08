# Ch 6 — cgroups

> 目標：理解 cgroups 如何限制容器的資源使用，能用 Docker flags 設定 memory / CPU 限制，並從 `/sys/fs/cgroup` 直接驗證設定值。

---

## cgroups 是什麼

**cgroups（Control Groups）** 是 Linux kernel 功能，讓你對一組進程強制限制、計量、隔離資源使用。

namespace 控制進程「看得見什麼」，cgroups 控制進程「能用多少」。兩者合起來才是完整的容器隔離。

```
進程 A (容器 1)   進程 B (容器 1)
       |                 |
       +--------+--------+
                |
          cgroup: docker/<id>
          memory.max = 256m
          cpu.max = 50000 100000  (50%)
          pids.max = 100
                |
          超過限制 -> OOM kill / throttle / fork 失敗
```

---

## cgroups v1 vs v2

| | cgroups v1 | cgroups v2 |
|--|-----------|-----------|
| 結構 | 每個 controller 獨立階層（多棵樹） | 統一階層（單一樹） |
| 設定位置 | `/sys/fs/cgroup/<controller>/` | `/sys/fs/cgroup/` |
| memory+swap | 分開的 `memory.limit_in_bytes` / `memsw.limit_in_bytes` | 統一的 `memory.max` / `memory.swap.max` |
| 現代 Linux | Ubuntu 22.04 以前預設 v1 | Ubuntu 22.04+、Fedora 33+ 預設 v2 |
| Docker 支援 | 全支援 | 需要 Docker 20.10+，且 kernel 5.2+ |

確認你的系統用哪個版本：

```bash
mount | grep cgroup
```

```
# v2
cgroup2 on /sys/fs/cgroup type cgroup2 (rw,nosuid,nodev,noexec,relatime)

# v1（多個）
tmpfs on /sys/fs/cgroup type tmpfs ...
cgroup on /sys/fs/cgroup/memory type cgroup ...
cgroup on /sys/fs/cgroup/cpu,cpuacct type cgroup ...
```

本章以 v2 為主（現代 Linux 預設）。v1 的路徑不同，但概念一樣。

---

## 資源控制器（Controllers）

| Controller | 控制什麼 | 關鍵檔案 |
|-----------|---------|---------|
| cpu | CPU 時間份額和配額 | `cpu.max`（配額）、`cpu.weight`（份額） |
| memory | 記憶體上限、swap | `memory.max`、`memory.swap.max` |
| blkio / io | 磁碟 I/O 速率和 IOPS | `io.max` |
| pids | 進程數量上限 | `pids.max` |
| cpuset | 綁定到特定 CPU 核心 / NUMA 節點 | `cpuset.cpus`、`cpuset.mems` |

---

## Docker 如何用 cgroups

Docker 跑容器時，會在 cgroup 樹裡建一個 scope：

```
/sys/fs/cgroup/
└── system.slice/
    └── docker-<full-container-id>.scope/
        ├── memory.max
        ├── cpu.max
        ├── pids.max
        └── cgroup.procs
```

### 設定記憶體限制

```bash
docker run -d --memory=256m --name memtest nginx:1.25-alpine
```

找到 cgroup 目錄並驗證：

```bash
CID=$(docker inspect --format '{{.Id}}' memtest)
cat /sys/fs/cgroup/system.slice/docker-${CID}.scope/memory.max
```

```
268435456    <- 256 * 1024 * 1024 = 268435456 bytes = 256MB
```

### 設定 CPU 限制

```bash
docker run -d --cpus=0.5 --name cputest nginx:1.25-alpine
```

```bash
CID=$(docker inspect --format '{{.Id}}' cputest)
cat /sys/fs/cgroup/system.slice/docker-${CID}.scope/cpu.max
```

```
50000 100000
```

格式是 `<quota> <period>`，單位微秒。`50000 100000` = 每 100ms 只能用 50ms CPU 時間 = 50% 一個核心。

### 設定 PID 上限（防 fork bomb）

```bash
docker run -d --pids-limit=100 --name pidtest nginx:1.25-alpine
```

```bash
CID=$(docker inspect --format '{{.Id}}' pidtest)
cat /sys/fs/cgroup/system.slice/docker-${CID}.scope/pids.max
```

```
100
```

容器裡的進程數超過 100 個，`fork()` 就會失敗，防止 fork bomb 打爆 host。

---

## 動手實驗：OOM Kill 觀察

```bash
# 給 64MB 記憶體，試圖分配 200MB
docker run --rm --memory=64m --name oomtest \
  python:3.11-slim \
  python -c "x = 'a' * 200_000_000; print('allocated')"
```

```
Killed
```

容器被 OOM killer 殺掉，exit code 是 137（128 + SIGKILL）。

```bash
# 看退出碼
echo $?
# 137

# 用 docker events 或 dmesg 確認 OOM
docker run --rm --memory=64m --name oomtest \
  python:3.11-slim \
  python -c "x = 'a' * 200_000_000; print('allocated')" 2>&1; echo "Exit: $?"
```

```bash
# 在另一個終端監聽 docker events
docker events --filter container=oomtest
```

```
... container oom oomtest ...
... container die oomtest (exitCode=137) ...
```

---

## docker stats：即時資源用量

```bash
docker stats
```

```
CONTAINER ID   NAME       CPU %   MEM USAGE / LIMIT   MEM %   NET I/O       BLOCK I/O
a3f1d8e92c10   mynginx    0.00%   3.4MiB / 256MiB     1.3%    1.2kB / 0B    0B / 0B
b4e2f1a3c9d5   memtest    0.10%   12.1MiB / 256MiB    4.7%    648B / 0B     0B / 0B
```

`MEM USAGE / LIMIT`：實際用量 / 設定上限。沒設上限時 LIMIT 顯示 host 的總記憶體。

```bash
# 單次輸出（不進入 interactive 模式）
docker stats --no-stream
```

---

## systemd-cgtop：看 cgroup 樹的資源用量

```bash
systemd-cgtop
```

```
Control Group                           Tasks   %CPU   Memory  Input/s Output/s
/                                         312    2.1     3.0G        -        -
/system.slice                              89    1.8   512.0M        -        -
/system.slice/docker-a3f1....scope          3    0.0    12.1M        -        -
/system.slice/docker-b4e2....scope          2    0.1     3.4M        -        -
```

這是從系統角度看所有容器的資源消耗，比 `docker stats` 更全面，還能看到 systemd unit 的對比。

---

## cgroup.procs：確認進程歸屬

```bash
CID=$(docker inspect --format '{{.Id}}' mynginx)
cat /sys/fs/cgroup/system.slice/docker-${CID}.scope/cgroup.procs
```

```
15234
15267
15268
```

這些是在這個 cgroup 裡的所有進程 PID（host 視角）。`15234` 就是 nginx master，`15267`、`15268` 是 worker。

---

## 實用組合：記憶體 + CPU + PID 一起設

```bash
docker run -d \
  --name limited \
  --memory=256m \
  --memory-swap=256m \
  --cpus=0.5 \
  --pids-limit=50 \
  nginx:1.25-alpine
```

`--memory-swap=256m` 等於 `--memory`：禁用 swap（swap 空間為 0）。不設的話預設 swap = memory，容器可以用到 512MB 的 memory+swap。生產環境通常把兩個設一樣，避免進程 OOM 前慢慢 swap 造成效能問題。

---

## 自我檢核

- [ ] 能說明 cgroups v1 和 v2 的結構差異
- [ ] 跑一個有 `--memory=64m` 限制的容器，觸發 OOM kill，確認 exit code 137
- [ ] 在 `/sys/fs/cgroup/system.slice/docker-<id>.scope/` 看到 `memory.max` 和 `cpu.max`
- [ ] 用 `docker stats` 即時看容器資源用量
- [ ] 理解 `--memory-swap` 和 `--memory` 的關係
- [ ] 知道 `--pids-limit` 為什麼重要（fork bomb 防護）

下一章進 OverlayFS，看容器的 filesystem 實際怎麼疊起來的。

→ [Ch 7 OverlayFS](./07-overlayfs.md)
