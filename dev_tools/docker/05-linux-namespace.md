# Ch 5 — Linux Namespace

> 目標：理解 namespace 是容器隔離的核心機制，用 `unshare` 手動建立一個最小容器，並從 `/proc` 驗證 Docker 容器實際使用的 namespace。

---

## Namespace 是什麼

Namespace（命名空間）是 Linux kernel 的功能，讓一組進程看到系統資源的「私有視圖」。同一個 kernel 上，不同 namespace 裡的進程看到的 PID 表、網路介面、hostname 是各自獨立的，互不可見。

容器 = 進程 + 一組 namespace（+ cgroup 資源限制）。去掉神秘感：Docker 的容器就是把 `clone(2)` syscall 的 namespace flag 設好，然後 exec 你的應用程式。

---

## 六種 Namespace

| Namespace | flag | 隔離什麼 | 影響 |
|-----------|------|----------|------|
| PID | `CLONE_NEWPID` | Process ID | 容器裡看不到 host 的 process，自己有獨立的 PID 1 |
| Network | `CLONE_NEWNET` | 網路介面、路由表、iptables | 容器有自己的 eth0、lo |
| Mount | `CLONE_NEWNS` | 檔案系統掛載點 | 容器有自己的根目錄（rootfs），看不到 host 的掛載 |
| UTS | `CLONE_NEWUTS` | hostname、domainname | 容器可設不同 hostname |
| IPC | `CLONE_NEWIPC` | System V IPC、POSIX message queue | 容器間無法共享記憶體 |
| User | `CLONE_NEWUSER` | UID/GID 映射 | 容器裡的 root(0) 對應 host 的非特權 UID |

Linux 5.6 之後還有 Time namespace（隔離系統時間），但 Docker 目前不用。

---

## 動手用 unshare 手刻最小容器

這是本章精華。`unshare(1)` 是個工具，讓你在新的 namespace 裡跑指令。

```bash
# 需要 root 或適當的 capability
# 建立 pid + uts + mount namespace，並 fork
sudo unshare --pid --uts --mount --fork bash
```

你現在在一個新的 bash 進程裡，它有自己的 PID / UTS / mount namespace。

```bash
# 在新 namespace 裡測試 UTS 隔離
hostname
# 看到 host 的 hostname

hostname container-1
hostname
# container-1  <- 只有這個 namespace 看得到

# 回到另一個終端，在 host 上
hostname
# 原本的 hostname，沒被改到
```

```bash
# 在新 namespace 裡測試 PID 隔離
ps aux
# 注意：這裡仍然能看到 host 的 process！
# 因為 /proc 還是 host 的，需要 remount
mount -t proc proc /proc
ps aux
```

```
USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root         1  0.0  0.0   7232  4096 pts/0    S    10:23   0:00 bash
root        12  0.0  0.0   8812  3328 pts/0    R+   10:23   0:00 ps aux
```

PID 1 就是我們的 bash，pid namespace 隔離生效。容器裡看不到 host 的進程。

```bash
# mount 隔離：在新 namespace 裡 bind mount
mkdir -p /tmp/sandbox
mount --bind /tmp /tmp/sandbox
# 這個掛載只在這個 mount namespace 裡可見
# host 上的 /tmp/sandbox 沒有這個掛載
```

**退出**：`exit` 或 Ctrl+D，離開新的 namespace，回到 host。

---

## lsns：列出系統上所有 Namespace

```bash
lsns
```

```
        NS TYPE   NPROCS   PID USER             COMMAND
4026531836 mnt       142     1 root             /sbin/init
4026531837 uts       142     1 root             /sbin/init
4026531838 ipc       142     1 root             /sbin/init
4026531839 pid       142     1 root             /sbin/init
4026531840 net       143     1 root             /sbin/init
4026531992 user      142     1 root             /sbin/init
4026532560 mnt         1 12345 root             nginx: master process nginx
4026532561 uts         1 12345 root             nginx: master process nginx
...
```

每個 namespace 是一個 inode（數字就是 inode number）。兩個進程的 namespace inode 相同，表示它們在同一個 namespace 裡（這就是 `--network container:other` 共享 network namespace 的原理）。

---

## /proc/\<pid\>/ns/：驗證 Docker 容器的 Namespace

```bash
# 跑一個容器
docker run -d --name ns-test nginx:1.25-alpine

# 取得容器的 host PID
PID=$(docker inspect --format '{{.State.Pid}}' ns-test)
echo "Container PID on host: $PID"
```

```
Container PID on host: 15234
```

```bash
# 看這個進程的所有 namespace
ls -la /proc/$PID/ns/
```

```
lrwxrwxrwx 1 root root 0 Jan 15 10:30 ipc -> 'ipc:[4026532567]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 mnt -> 'mnt:[4026532565]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 net -> 'net:[4026532568]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 pid -> 'pid:[4026532566]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 uts -> 'uts:[4026532564]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 user -> 'user:[4026531992]'
```

和 host 的 init 進程（PID 1）比較：

```bash
ls -la /proc/1/ns/
```

```
lrwxrwxrwx 1 root root 0 Jan 15 10:30 ipc -> 'ipc:[4026531838]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 mnt -> 'mnt:[4026531836]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 net -> 'net:[4026531840]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 pid -> 'pid:[4026531839]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 uts -> 'uts:[4026531837]'
lrwxrwxrwx 1 root root 0 Jan 15 10:30 user -> 'user:[4026531992]'
```

比較結果：
- `mnt`、`net`、`pid`、`uts`、`ipc`：容器和 host 不同 → 隔離有效
- `user`：相同（`4026531992`）→ 容器共用 host 的 user namespace（這是預設 Docker，沒用 rootless）

---

## User Namespace：UID 映射

User namespace 讓容器裡的 root（UID 0）映射到 host 上的非特權 UID（例如 100000）。這是 **rootless Docker** 的基礎。

```
容器內 UID     host UID
    0      ->    100000   (容器 root = host 的普通用戶)
    1      ->    100001
   ...           ...
  65535    ->    165535
```

沒有 user namespace 的情況下（傳統 Docker）：容器內的 UID 0 **就是** host 的 UID 0。容器裡是 root，在 host 上也是 root。這就是為什麼 Docker 被長期批評「容器裡 root = host root」。

開啟 user namespace 後，容器的 root 在 host 看來是 UID 100000，沒有特權。

---

## Container Escape：namespace 隔離被突破

幾個已知的攻擊概念（只了解概念，細節見 Ch 23）：

| 手法 | 原理 |
|------|------|
| `--privileged` 逃逸 | 給了 CAP_SYS_ADMIN，可以 mount host 根目錄或操作 cgroup |
| Docker socket 掛進容器 | 容器能控制 docker daemon，等同 root |
| CVE-2019-5736 (runc) | 利用 runc 在進程開啟中覆寫 runc 二進位 |
| Kernel exploit | namespace 是軟隔離，kernel 漏洞可打穿所有容器 |

要記住的結論：namespace 隔離在沒有 kernel 漏洞的情況下是夠用的，但不是硬邊界。`--privileged` 基本上是把隔離關掉，能不用就不用。

---

## 自我檢核

- [ ] 能說明六種 namespace 各自隔離什麼
- [ ] 用 `unshare` 建新的 pid + uts + mount namespace，並驗證 hostname 隔離
- [ ] remount `/proc` 後在新 namespace 裡確認自己是 PID 1
- [ ] 跑 Docker 容器，取得 host PID，在 `/proc/<pid>/ns/` 驗證 namespace 隔離
- [ ] 能解釋 user namespace 的 UID 映射機制

下一章是 cgroups：namespace 管「看得見什麼」，cgroups 管「能用多少資源」。

→ [Ch 6 cgroups](./06-cgroups.md)
