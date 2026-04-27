# Ch 19 — netfilter / nf_tables：kernelCTF 最大礦區

> 目標：nf_tables 從 2022 開始出 bug 出到現在 — CVE-2022-32250、CVE-2023-32233、CVE-2024-1086 全部在這。這章講 nf_tables 的 object 模型（table / chain / rule / set）、為什麼這麼多 bug、怎麼寫 PoC 戳它。

## 為什麼 nf_tables 這麼多 bug

nf_tables（`net/netfilter/nf_tables_api.c`）是 iptables 的現代替代，2013 年進主線，由 netlink socket 操作。它的問題是**複雜度極高**：

1. **transaction model**：nf_tables 用 batch commit 的方式修改規則 — 你送一堆 netlink message，kernel 先 apply，確認沒問題再 commit；若中途失敗要 rollback（abort）。這個兩階段 commit 邏輯複雜，abort path 特別容易出錯。
2. **多種物件互相 reference**：table → chain → rule → expression，再加上 set、set element、object binding，ref counting 非常複雜。
3. **user namespace 開了 CAP_NET_ADMIN**：unprivileged user 用 `unshare(CLONE_NEWUSER|CLONE_NEWNET)` 就能在自己的 net namespace 操作 nf_tables，攻擊面完全對 unprivileged user 開放。

---

## Object 模型

```
net namespace
└── nft_table（"filter"）
    └── nft_chain（"INPUT"）
        └── nft_rule
            └── nft_expr（一系列 expressions，e.g., nft_immediate, nft_cmp, nft_counter）
                └── expr data（依 expression type 不同，緊跟在 nft_expr 後面）
    └── nft_set（"myset"）
        └── nft_set_elem（set element，存 key + data）
└── nft_object（stateful object，e.g., counter, quota）
```

每個物件：

| 物件 | kmalloc size | 關鍵欄位 |
|---|---|---|
| `nft_table` | ~256 | `name`, `chains`, `sets`, `objects` list |
| `nft_chain` | ~384 | `table`, `rules` list, `ops` |
| `nft_rule` | 48 + expr data | `chain`, exprs, dlen |
| `nft_expr` | `expr_type->size` | `ops`（指向 nft_expr_ops） |
| `nft_set` | ~512 | `ops`（指向 set backend），elem count |
| `nft_set_elem` | 依 backend | set key, data |
| `nft_object` | 依 type | ops, refcnt |

---

## 三個代表性 CVE

### CVE-2022-32250：nft_expr UAF in abort path

**Root cause**：在 batch transaction 的 abort（回滾）path 中，`nft_immediate_destroy` 被呼叫了兩次：一次在 commit check 失敗時，一次在最終 abort 清理時。`nft_expr` 裡指向的 data 被 double free。

**UAF 物件**：`nft_set`（透過 `nft_immediate` expression 引用）。Object 在 `kmalloc-128`。

**利用路徑**：
```
double free nft_set → dangling pointer 留在 rule expression 裡
→ UAF write（在下次 batch 中） → 覆寫 nft_set 的 ops pointer
→ trigger set 操作 → RIP 控制
（2022 年時還沒有 KCFI，ops hijack 還有效）
```

### CVE-2023-32233：nft_set_elem UAF in anonymous set

**Root cause**：anonymous set（`NFT_SET_ANONYMOUS`）在 `nft_trans_set_add` 後、commit 前有一個時間窗口，set 的 refcount 管理有誤。在特定 batch 序列下，set 和 set element 可以被 free 兩次。

**UAF 物件**：`nft_set` 的 internal element（依 backend 不同，在 `kmalloc-128` 或 `kmalloc-256`）。

**利用路徑（典型 2023 writeup）**：
```
UAF on nft_set_elem（kmalloc-128）
→ cross-cache to cred_jar（Ch 13 手法）
→ overwrite cred uid = 0
（data-only，繞 KCFI）
```

### CVE-2024-1086：nft_verdict double free

**Root cause**：`nft_verdict_init()` 在某些 chain binding 場景下，`chain->use` refcount 被 decrement 兩次，導致 chain 被提前 free，留下 dangling reference。

**UAF 物件**：`nft_chain`（`kmalloc-512`）。

**利用路徑**：
```
UAF on nft_chain → dangling nft_chain pointer 留在 table 的 chain list
→ table/chain 操作觸發 dangling read/write
→ cross-cache + overwrite cred（data-only）或 ops hijack（無 KCFI 環境）
```

---

## PoC 基本框架：用 libmnl 操作 nf_tables

操作 nf_tables 要用 `NFNL_SUBSYS_NFTABLES` 的 Netlink socket。可以用原始 netlink 或 `libmnl` / `libnftables`。

### 取得攻擊能力（user namespace）

```c
#include <sched.h>
#include <unistd.h>

static void enter_user_net_ns(void) {
    /* unshare NEWUSER + NEWNET → 在自己的 namespace 有 CAP_NET_ADMIN */
    if (unshare(CLONE_NEWUSER | CLONE_NEWNET) < 0) {
        perror("unshare"); _exit(1);
    }
    /* 映射 uid：讓 kernel 認為 user 0 在這個 ns 有特權 */
    int fd;
    fd = open("/proc/self/uid_map", O_WRONLY);
    write(fd, "0 1000 1", 8); close(fd);
    fd = open("/proc/self/setgroups", O_WRONLY);
    write(fd, "deny", 4); close(fd);
    fd = open("/proc/self/gid_map", O_WRONLY);
    write(fd, "0 1000 1", 8); close(fd);
}
```

### 開 netlink socket + 基本 message 結構

```c
#include <linux/netlink.h>
#include <linux/netfilter/nfnetlink.h>
#include <linux/netfilter/nf_tables.h>
#include <sys/socket.h>

static int nl_sock;

static void nl_open(void) {
    nl_sock = socket(AF_NETLINK, SOCK_RAW, NETLINK_NETFILTER);
    struct sockaddr_nl addr = { .nl_family = AF_NETLINK };
    bind(nl_sock, (struct sockaddr *)&addr, sizeof(addr));
}

/* 傳送一個 nfnetlink batch begin / end */
static void batch_begin(void) { /* 發 NFNL_MSG_BATCH_BEGIN */ }
static void batch_end(void)   { /* 發 NFNL_MSG_BATCH_END */ }

/* 在 batch 裡加一個 add table message */
static void nft_add_table(const char *name) {
    /* 組 struct nlmsghdr + struct nfgenmsg + NLA: NFTA_TABLE_NAME */
}

/* 加 chain */
static void nft_add_chain(const char *table, const char *chain) { ... }

/* 加 anonymous set（UAF 觸發點） */
static void nft_add_anon_set(const char *table) { ... }

/* 刪除操作（觸發 abort） */
static void nft_del_set(...) { ... }
```

真正 CTF exploit 的 PoC 不用 libmnl，直接手工組 netlink message（`struct nlmsghdr` + `struct nfgenmsg` + NLA attributes）更靈活，但需要仔細對應 nf_tables 的 message format。

### CVE-2023-32233 觸發序列（概念）

```
batch_begin
  add table "t1"
  add chain "t1" "c1"
  add anonymous set "t1" (ref 1)
  add rule "t1" "c1" with verdict referencing the anon set (ref 2)
  delete the anon set explicitly (ref 1 → 0 → free)
  → rule still has dangling reference to set
batch_end (commit)
  → kernel tries to clean up the now-freed set again in commit path
  → double free / UAF
```

PoC 要精確地找到「在哪個 message 之間觸發」，這需要讀 nf_tables_api.c 的 transaction 邏輯。

---

## 為什麼 nf_tables 特別適合 kernelCTF

1. **unprivileged user 可觸發**：user namespace 的 CAP_NET_ADMIN 讓 unpriv user 能玩 nf_tables。
2. **物件大小多樣**：`nft_set`（kmalloc-128）、`nft_chain`（kmalloc-512）、`nft_rule`（可控 size）— 你能選物件落在你想要的 cache size。
3. **複雜的 refcount + abort path**：每次 kernel 修 nf_tables，都可能引入新 bug。2022-2024 幾乎每年都有 kernelCTF 用 nf_tables。
4. **spray 友好**：每個 netlink message 一次 alloc 一個物件，精確控制 alloc/free 時序。

---

## 動手練習

1. **讀 nf_tables_api.c 的 `nft_set_destroy`**：找它在 commit path 和 abort path 分別在哪裡被呼叫，確認 CVE-2023-32233 的 double free 路徑。
2. **用 nft 命令列驗 object model**：`nft add table ip t; nft add chain ip t c; nft list ruleset`，觀察每個物件的名字和結構對應關係。
3. **手寫 netlink message**：不用 libmnl，直接用 `socket(AF_NETLINK, SOCK_RAW, NETLINK_NETFILTER)` + 手組 `nlmsghdr` 發一個 `NFT_MSG_NEWTABLE`，確認 table 被建立。
4. **在 user namespace 裡跑 nft**：`unshare -rn nft add table ip t`，確認 unprivileged user 能操作 nf_tables（你的 exploit 的前置條件）。
5. **讀 CVE-2024-1086 PoC**（公開）：理解它的 batch message 序列，把每個 message 對應到 kernel source 的哪個函式。

## 自我檢核

- [ ] 能畫出 nf_tables 的 object hierarchy（table → chain → rule → expr → set）
- [ ] 知道 user namespace + unshare 怎麼給 unprivileged user CAP_NET_ADMIN
- [ ] 能說出 CVE-2022-32250 / 2023-32233 / 2024-1086 各自的 root cause（一句話）
- [ ] 知道 nf_tables 操作用哪個 netlink subsystem（NFNL_SUBSYS_NFTABLES）
- [ ] 知道 nft_rule 的 size 可控（expr data 長度由 rule 內容決定）
- [ ] 能解釋 nf_tables 的 batch commit / abort 模式

→ [Ch 20 — io_uring：SQE/CQE 與 async ring 的 UAF 模式](./20-io-uring.md)
