# Ch 22 — Capabilities 限制

> 目標：掌握 Linux capability 系統的運作，學會用 `--cap-drop ALL --cap-add` 原則把容器的特權降到最低，並能用 `amicontained` 驗證實際結果。

## 複習 Ch 9：Docker 預設給了什麼

Linux 把傳統的 root 超能力拆成 40+ 個獨立的 capability（能力）。Docker 不給容器完整的 root capability set，但預設還是給了一批：

```bash
# 在容器內查看目前的 capability
docker run --rm alpine apk add -q libcap && capsh --print
```

Docker 預設給的 capability（部分列表）：

| Capability | 能做什麼 |
|------------|----------|
| `CHOWN` | 任意改變檔案 UID/GID |
| `DAC_OVERRIDE` | 繞過檔案讀寫執行的權限檢查 |
| `FOWNER` | 繞過需要 UID 相符的操作 |
| `KILL` | 向任意 process 發 signal |
| `NET_BIND_SERVICE` | bind 小於 1024 的 port |
| `NET_RAW` | raw socket（ping、packet sniff） |
| `SETUID` / `SETGID` | 任意改變 process UID/GID |
| `SYS_CHROOT` | 呼叫 chroot() |
| `AUDIT_WRITE` | 寫入 kernel audit log |

這些大多數 web app 根本用不到，但攻擊者可以利用它們橫向移動或提權。

## 最小 capability 原則

原則很簡單：**先全部 drop，再按需 add**。

```bash
docker run --cap-drop ALL --cap-add NET_BIND_SERVICE myapp
```

`--cap-drop ALL` 移除所有預設 capability，`--cap-add` 只加回真正需要的。

測試效果：

```bash
# 全部 drop 後，連 chown 都不能做
docker run --rm --cap-drop ALL alpine chown nobody /etc/hosts
# chown: /etc/hosts: Operation not permitted

# 全部 drop 但保留 CHOWN
docker run --rm --cap-drop ALL --cap-add CHOWN alpine chown nobody /etc/hosts
# 成功
```

## 常用場景的 capability 需求

| 場景 | 需要的 Capability | 備註 |
|------|-------------------|------|
| Web server bind port 80 | `NET_BIND_SERVICE` | 若用 port > 1024 完全不需要 |
| `ping` / raw socket | `NET_RAW` | 生產環境 app 不應需要 |
| 改系統時間 | `SYS_TIME` | 幾乎沒有 app 需要，NTP 靠 host |
| `strace` / ptrace 另一個 process | `SYS_PTRACE` | 只應出現在 debug 環境 |
| 掛載 filesystem | `SYS_ADMIN` | 危險，見下面實驗 |
| 改 hostname | `SYS_ADMIN` | 容器裡改 hostname 通常沒意義 |
| 設定 network interface | `NET_ADMIN` | Routing、iptables 等 |
| 讀 `/proc/<pid>/mem` | `SYS_PTRACE` | memory forensics |
| `iptables`、`tc` | `NET_ADMIN` | 只有網路工具容器需要 |

結論：**一般 API server 完全不需要 `--cap-drop ALL` 以外的任何 capability**，只要 app 監聽 port > 1024 就連 `NET_BIND_SERVICE` 也不用。

## Compose 設定

```yaml
services:
  api:
    image: myapp:latest
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE
    ports:
      - "80:80"

  # Debug 工具：允許 ptrace
  debugger:
    image: debug-tools:latest
    cap_drop:
      - ALL
    cap_add:
      - SYS_PTRACE
    profiles:
      - debug   # 只在 --profile debug 時啟動
```

`profiles:` 讓 debug 服務不在正式環境出現。

## 實驗：SYS_ADMIN 能做什麼

故意給一個不必要的 `SYS_ADMIN`，看看攻擊者能拿到多少：

```bash
# 1. 改容器 hostname（通常沒什麼用，但說明 SYS_ADMIN 有多廣）
docker run --rm --cap-add SYS_ADMIN alpine hostname hacked
# hacked

# 2. 在容器內掛載 tmpfs（沒 SYS_ADMIN 會失敗）
docker run --rm --cap-add SYS_ADMIN alpine sh -c \
  "mount -t tmpfs tmpfs /mnt && echo 'mounted!' && umount /mnt"
# mounted!

# 沒有 SYS_ADMIN：
docker run --rm --cap-drop ALL alpine sh -c \
  "mount -t tmpfs tmpfs /mnt"
# mount: permission denied (are you root?)

# 3. 更危險：如果 host 有共享目錄，SYS_ADMIN + 重新 mount 可以繞過 read-only
docker run --rm --cap-add SYS_ADMIN --cap-add DAC_READ_SEARCH ubuntu sh -c \
  "mount --bind / /mnt 2>/dev/null && ls /mnt/etc/shadow"
# 在某些設定下可能看到 host 的 /etc/shadow
```

`SYS_ADMIN` 是最危險的 capability，基本上是 root 的另一個名字。給了它，其他限制大幅削弱。

## seccomp 和 capability 的關係

這兩個是不同層次的限制，要兩個都要：

```
capability：你被允許用什麼「角色」（例如 NET_ADMIN）
seccomp：你被允許呼叫什麼「系統呼叫」（例如 socket、mmap）
```

一個 process 可以有 `NET_RAW` capability，但如果 seccomp 擋了 `socket()` 系統呼叫，它仍然無法建立 raw socket。兩層都設，攻擊者要繞過兩層才能利用。

Docker 預設的 seccomp profile 已經封鎖了 ~44 個危險 syscall（包括 `keyctl`、`ptrace`、`clone` 的部分 flag）。在 capability 和 seccomp 之間，capability 是你首先要動的。

## amicontained：列出容器實際的安全狀態

`amicontained` 是一個方便的工具，在容器內跑，會列出：
- 所有 capability
- seccomp 狀態
- namespace 類型
- 是否在容器內

```bash
# 直接跑，不需要安裝到 image
docker run --rm r.j3ss.co/amicontained
```

預設 Docker 容器的輸出大概像這樣：

```
Container Runtime: docker
Has Namespaces:
  pid: true
  user: false        <- 沒有 user namespace！
AppArmor Profile: docker-default (enforce)
Capabilities:
  BOUNDING -> chown dac_override fowner fsetid kill setgid setuid ...
Seccomp: filtering
Blocked Syscalls (46):
  MSGRCV SYSLOG SETPGID SETSID VHANGUP ...
```

重要：`user: false` 確認了 Ch 21 說的——沒有 user namespace，容器 UID 0 等於 host UID 0。

加了 `--cap-drop ALL` 後再跑：

```bash
docker run --rm --cap-drop ALL r.j3ss.co/amicontained
```

```
Capabilities:
  BOUNDING ->    <- 空的！
```

確認所有 capability 都被移除。

## 完整範例：最小化的 nginx

```bash
# nginx 預設 bind port 80，需要 NET_BIND_SERVICE
# 如果改用 port 8080，連這個也不需要
docker run -d \
  --name nginx-hardened \
  --cap-drop ALL \
  --cap-add NET_BIND_SERVICE \
  --read-only \
  --tmpfs /var/cache/nginx:size=32m \
  --tmpfs /var/run:size=4m \
  --no-new-privileges \
  -p 80:80 \
  nginx:alpine

# 驗證它還能正常服務
curl http://localhost/
```

## 自我檢核

- [ ] 能列出 Docker 預設給的至少 5 個 capability 及其用途
- [ ] 能解釋 `--cap-drop ALL --cap-add NET_BIND_SERVICE` 的語義
- [ ] 能說明 `SYS_ADMIN` 為什麼是最危險的 capability
- [ ] 能在 Compose 設定 `cap_drop` / `cap_add`
- [ ] 能用 `amicontained` 驗證容器的 capability 和 seccomp 狀態
- [ ] 能解釋 capability 和 seccomp 是不同層次的限制

下一章講 Docker socket 的危險與 rootless 模式——這是架構層面的安全問題，比 capability 設定更根本。

→ [Ch 23 Docker Socket 與 Rootless](./23-docker-socket-rootless.md)
