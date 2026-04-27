# Ch 22 — User namespace + ksmbd + 其他子系統速覽

> 目標：快速覽過 kernelCTF 常見但前面沒講到的子系統 — user namespace（unprivileged user 的權限放大器）、ksmbd（in-kernel SMB server，bug 高發）、cls_route、af_unix gc。每個給一段模式描述 + 代表 CVE。

## User namespace：不是 bug，是「開門鑰匙」

user namespace 本身不是漏洞，但它讓 unprivileged user 拿到在自己 namespace 裡的各種 capability，包括：

- `CAP_NET_ADMIN`（CLONE_NEWNET）→ 可以操作 nf_tables、netlink、tc、socket options
- `CAP_SYS_ADMIN`（CLONE_NEWUSER + mapping）→ 可以 mount、操作 cgroup、BPF（部分）
- `CAP_BPF` via `CLONE_NEWUSER` → 可以用 BPF（kernel 5.9 後有限制）

**沒有 user namespace，kernelCTF 一半的 attack surface 不可用**：nf_tables 題（Ch 19）、BPF 題（Ch 21）、ksmbd 的部分接入點都要先過 user namespace。

```c
/* 標準開場白：進 user namespace */
static void enter_userns(void) {
    if (unshare(CLONE_NEWUSER | CLONE_NEWNET) < 0) {
        perror("unshare"); exit(1);
    }
    /* uid map: 把 uid 0 (ns 內) 映射到 host 上的 real uid */
    char buf[64];
    int fd;

    fd = open("/proc/self/setgroups", O_WRONLY);
    write(fd, "deny", 4); close(fd);

    snprintf(buf, sizeof(buf), "0 %d 1", getuid());
    fd = open("/proc/self/uid_map", O_WRONLY);
    write(fd, buf, strlen(buf)); close(fd);

    snprintf(buf, sizeof(buf), "0 %d 1", getgid());
    fd = open("/proc/self/gid_map", O_WRONLY);
    write(fd, buf, strlen(buf)); close(fd);
}
```

**為什麼 kernel 允許這樣**：理論上 namespace 是 isolated 的，你在 ns 裡的 CAP_NET_ADMIN 只影響你自己的 net ns，不影響 host。bug 是 ns 內的操作透過共用的 kernel 物件「逃」出去。

---

## ksmbd：in-kernel SMB3 server

`ksmbd`（`fs/ksmbd/`）是 5.15 合進主線的 in-kernel SMB3 server。啟用 `CONFIG_SMB_SERVER` 後，kernel 直接處理 SMB 協議，不走 user-space samba。

**為什麼 bug 多**：
- SMB3 協議複雜（negotiate / session setup / tree connect / file operations 等大量 compound commands）
- ksmbd 在 kernel 裡解析 network data — 任何 malformed packet 都可能觸發 kernel bug
- 大量的 pool alloc/free 操作，ref counting 不完善
- remote-accessible：不需要本地 user，網路封包就能觸發

**代表 CVE**：

| CVE | Type | 物件 |
|---|---|---|
| CVE-2022-47940 | OOB read（SMBv2 query info） | ksmbd_file，kmalloc-xxx |
| CVE-2023-32254 | race condition / UAF（directory change notify） | ksmbd_work，kmalloc-4096 |
| CVE-2023-32258 | double free（session setup error path） | ksmbd_session，kmalloc-512 |

**攻擊方式**：ksmbd 透過 Netlink socket 從 user-space 的 `ksmbd.mountd` daemon 接受配置，SMB client 直接連 TCP port 445。CTF 題通常提供一個 ksmbd 配置好的 VM，讓你從 loopback 發 SMB3 packet 觸發 bug。

---

## cls_route / TC（traffic control subsystem）

`cls_route`（`net/sched/cls_route.c`）是 Linux traffic control 的一個 filter classifier。

**CVE-2022-2588**（cls_route UAF）：
- root cause：filter delete 後，filter 仍殘留在 binding hash 中，後續查找觸發 UAF
- 物件：`route4_filter`，kmalloc-128
- unprivileged 觸發：需要 `CAP_NET_ADMIN`（可以透過 user namespace 取得）
- 利用：UAF → cross-cache → cred_jar（這是 Dirty Cred 論文的原始 demo exploit）

TC 子系統整體攻擊面廣（`cls_flower`、`cls_u32`、`cls_matchall`、`act_*` action modules），2022-2023 間出了大量 CVE。

---

## af_unix GC：garbage collector race

`af_unix`（Unix domain socket，`net/unix/garbage.c`）有一個 GC 系統，用來清理「循環引用的 socket pair」（A 送 fd 給 B，B 送 fd 給 A，然後兩個都 close → refcount 永遠不到 0 → 需要 GC）。

**歷史 bug 模式**：
- GC 和 close/sendmsg 同時發生時，GC 遍歷的 socket list 發生 concurrent modification
- `unix_sock` 的 `sk_receive_queue` 被 GC 清到、同時 sendmsg 在往裡插 → use-after-free
- `unix_sock` 的 `skb` freelist 在 GC 過程中被重用

**CVE-2021-0920**（Android 側）、後續的 Linux 主線 GC race 類 bug。

物件通常是 `sk_buff`（kmalloc-N）或 `unix_sock`（dedicated socket cache）。

---

## 其他速覽

### nfsd（NFS server）

類似 ksmbd，in-kernel NFS server 在 `fs/nfsd/`。協議解析 + lock 管理複雜，歷史上有 OOB 和 UAF。kernelCTF 題較少但不是沒有。

### fanotify / inotify

inotify/fanotify 的 event 佇列管理（`fsnotify_event`）有 refcount 問題和 use-after-free 歷史。物件在 `fsnotify_mark_connector` cache。

### nl80211（WiFi subsystem）

wireless 配置用 `cfg80211` + `nl80211` netlink。需要 `CAP_NET_ADMIN`，可以 user namespace 取得。2023 年有 OOB write（CVE-2022-42721 等）。

---

## 攻擊面總結地圖

```
unprivileged user
    └── unshare(CLONE_NEWUSER | CLONE_NEWNET)
         └── CAP_NET_ADMIN in ns
              ├── nf_tables（Ch 19）← 最多 bug
              ├── tc / cls_route（CVE-2022-2588）
              ├── nl80211
              └── af_unix GC
    └── network access（loopback）
         ├── ksmbd（TCP 445）← 遠端攻擊面
         └── nfsd（TCP 2049）

privileged / setuid binary
    └── CAP_BPF / CAP_SYS_ADMIN
         └── eBPF（Ch 21）
```

---

## 動手練習

1. **驗 user namespace 的 CAP**：`unshare -rn /bin/sh`，在 shell 裡 `cat /proc/self/status | grep Cap`，確認 CapEff 有 `CAP_NET_ADMIN` 對應的 bit。
2. **啟用 ksmbd**（QEMU）：在 kernel config 加 `CONFIG_SMB_SERVER=m`，load `ksmbd.ko`，用 `ksmbd-tools` 設定 share，從 loopback 用 `smbclient` 連上，確認基本 SMB 操作。
3. **讀 cls_route source**：`net/sched/cls_route.c` 的 `route4_delete`，找哪裡沒有從 hash 清掉 filter（CVE-2022-2588 的根本原因）。
4. **觀察 af_unix GC**：用 Python 建一個 cyclic socket reference pair（A 把 fd 傳給 B，B 把 fd 傳給 A），然後 close 雙方，用 `ss -x` 和 `cat /proc/net/unix` 觀察 GC 何時清掉它們。
5. **找 ksmbd CVE-2023-32254 的 race window**：讀 `fs/ksmbd/smb2pdu.c` 的 `smb2_set_info`，找 change notify 的 handler，確認 race 在哪個函式對之間發生。

## 自我檢核

- [ ] 能說出 user namespace 給哪些 capability、對應哪些攻擊面
- [ ] 知道 ksmbd 是 in-kernel SMB server，remote-accessible
- [ ] 能說出 cls_route CVE-2022-2588 的一句話根因（filter 殘留在 hash 中被 UAF）
- [ ] 知道 af_unix GC 的設計動機（清循環引用的 socket fd）和 bug 模式（GC + close race）
- [ ] 能畫出攻擊面地圖（從 unprivileged user 到各個 subsystem）

→ [Ch 23 — ARM64 kernel pwn 差異速查](./23-arm64-diff.md)
