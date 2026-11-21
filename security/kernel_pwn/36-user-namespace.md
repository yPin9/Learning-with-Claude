# Ch 36 — User namespace 與攻擊面放大：unprivileged user 怎麼拿 CAP_NET_ADMIN

> **目標**：搞清楚 `unshare(CLONE_NEWUSER)` 在 kernel 裡到底授予了什麼、沒授予什麼；為什麼它讓原本只有 root 摸得到的子系統（nf_tables、traffic control、AF_PACKET、mount 路徑）對一般 user 開放；為什麼 user namespace 是現代 Linux LPE 的關鍵前置條件；以及各發行版怎麼用 sysctl、LSM 收緊它。學完你能解釋「capability 在 namespace 內外的映射」，並判斷一個 CVE 在某個發行版預設設定下是否可達。

> **環境**：Linux 6.6 LTS、x86-64。本章 shell 指令與 C 程式**未在本環境實測**（作者環境無 Linux kernel 可跑 `unshare`），輸出標為「理論預期」，驗證方式在「動手練習」。sysctl 與發行版預設值的資訊來自 WebSearch（Ubuntu Discourse / Qualys / Debian kernel list 等）；查不到的（例如 RHEL 預設值）我們明說未驗證。

## 為什麼 user namespace 值得單獨一章？

[Ch 30](./30-netfilter-nftables.md) 說 nf_tables 是 kernelCTF 最大礦區。但 nf_tables 的操作要求 `CAP_NET_ADMIN`，一般 user 沒有。如果這道門檻真的擋得住，nf_tables 的 bug 就只是「root 才能打 root」的無聊漏洞。

現實是：在大多數桌面與雲端發行版的預設設定下，**一般 user 可以自己給自己一份 `CAP_NET_ADMIN`**：

```c
unshare(CLONE_NEWUSER | CLONE_NEWNET);
/* 現在這個 process 在新的 user ns 裡有 CAP_NET_ADMIN，
   且這個 ns 擁有一個全新的 network namespace */
```

這一行把整個 netfilter、tc、xfrm、AF_PACKET 的攻擊面，從「root only」變成「任何本地帳號」。這就是 user namespace 的「攻擊面放大器」角色：**它本身的 bug 不多，但它讓別人的 bug 變得可達**。

歷史脈絡：在 user namespace（3.8 起允許 unprivileged 建立，就我所知）出現之前，容器（LXC 早期）要不是需要 root 起，要不是 root 在 container 內等於 host root。User namespace 的設計目標是**讓 container 內的 root 在 host 上只是普通 uid**，進而讓 rootless container（Podman、rootless Docker）、Chrome / Firefox sandbox、`bubblewrap`、Flatpak 成為可能。好處是真實的：這些軟體靠它降低攻擊面。代價是：kernel 內大量「以為只有 root 會走到」的程式碼路徑，突然暴露給所有人。

## 先建立直覺：capability 不是全域的，是「對某個 namespace」的

把傳統的 capability 想成「一把萬能鑰匙」：有 `CAP_NET_ADMIN` 就能管整台機器的網路。user namespace 把它改成「**門牌制**的鑰匙」：你有的是**某一棟大樓**（某個 namespace）的管理鑰匙，不是整座城市的。

```
init_user_ns (host，uid 0 在這)
 │   擁有：init 的 net ns、init 的 mount ns、整個 /dev、全域 sysctl、module loading
 │
 └── child user ns A  (由 uid 1000 的一般 user 建立)
       │  建立者在 A 內有「全部 capability」（對 A 以及 A 擁有的資源）
       │
       ├── 擁有的資源：在 A 裡建立的 net ns、mount ns、uts ns、ipc ns、pid ns…
       │
       └── 「全部 capability」的意思是：
             對 A 擁有的 net ns       → CAP_NET_ADMIN 有效
             對 A 擁有的 mount ns     → CAP_SYS_ADMIN 有效（可 mount 被允許的 fs）
             對 init 的 net ns        → 無效（init net ns 屬於 init_user_ns）
             對全域資源（/dev/mem、module）→ 無效
```

> 這個「門牌」比喻在 capability **檢查語意**上成立；但要注意它不涵蓋「kernel 程式碼自己有 bug、把 namespace 內的操作漏到全域」這種情況，那正是本章後半要講的 bug 類型。

## capability 檢查的機制：`capable()` vs `ns_capable()`

kernel 裡有兩個層級的檢查函式（`include/linux/capability.h`、`kernel/capability.c`，就我所知，請在 6.6 source 驗證）：

| 函式 | 語意 | 典型用途 |
|---|---|---|
| `capable(cap)` | 等同 `ns_capable(&init_user_ns, cap)`：要求你在**初始** user ns 有該 cap | 全域資源：載入 module、`/dev/mem`、全域 sysctl |
| `ns_capable(ns, cap)` | 要求你在 `ns`（或其祖先）有該 cap | 某個 namespace 擁有的資源 |
| `netlink_net_capable(skb, cap)` / `netlink_ns_capable(...)` | 對 socket 所屬的 net ns 的 owner user ns 做 `ns_capable` | netlink 子系統（netfilter、routing、tc） |
| `file_ns_capable(file, ns, cap)` | 用 open 當時的 cred 檢查 | 避免 TOCTOU，檢查「開檔者」的權限 |

核心規則（概念上）：一個 process 的 cred 帶有 `user_ns`；資源（net ns、mount ns…）也記錄自己的 owner `user_ns`。`ns_capable(ns, cap)` 檢查：

1. 如果 process 的 `cred->user_ns == ns`，且 cap 在 `cred->cap_effective`，通過。
2. 如果 `ns` 是 process 所在 user ns 的後代，且 process 是 `ns` 的 owner（或其祖先的 owner），通過（父 ns 的管理者對子 ns 有全部 cap）。
3. 否則失敗。

所以一般 user 呼叫 `unshare(CLONE_NEWUSER)` 後：

- 新 user ns 的 owner 是他的 euid；他在新 ns 內拿到**完整的 permitted / effective capability set**（`CAP_LAST_CAP` 以下的全部，在 6.6 上全部 bit 為 1 對應 `CapEff: 000001ffffffffff`，理論值：6.6 的 `CAP_LAST_CAP` 是 40，共 41 個 bit；未實測，請以 `/proc/self/status` 為準）。
- 同時 `CLONE_NEWNET` 建立的新 net ns，owner 就是這個新 user ns，所以 `netlink_net_capable(skb, CAP_NET_ADMIN)` 通過。
- 這**不需要任何 host 的特權**（受 sysctl 與 LSM 限制，後文）。

## 最小可驗證範例

```c
// userns_caps.c — 觀察新 user ns 內的 capability（未實測，理論預期）
#define _GNU_SOURCE
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>

static void show(const char *tag) {
    char line[256];
    FILE *f = fopen("/proc/self/status", "r");
    printf("== %s (uid=%d euid=%d) ==\n", tag, getuid(), geteuid());
    while (fgets(line, sizeof line, f))
        if (!strncmp(line, "Cap", 3)) fputs(line, stdout);
    fclose(f);
}

int main(void) {
    show("before unshare");
    if (unshare(CLONE_NEWUSER | CLONE_NEWNET) < 0) {
        perror("unshare");           /* 被 sysctl / LSM 擋時，這裡看到 EPERM */
        return 1;
    }
    show("after unshare");
    return 0;
}
```

編譯執行（在你自己的 Linux VM 上）：

```bash
gcc -o userns_caps userns_caps.c && ./userns_caps
```

**理論預期輸出**（一般 user、無 sysctl / LSM 限制）：

```
== before unshare (uid=1000 euid=1000) ==
CapInh: 0000000000000000
CapPrm: 0000000000000000
CapEff: 0000000000000000
CapBnd: 000001ffffffffff
CapAmb: 0000000000000000
== after unshare (uid=65534 euid=65534) ==
CapInh: 0000000000000000
CapPrm: 000001ffffffffff
CapEff: 000001ffffffffff
CapBnd: 000001ffffffffff
CapAmb: 0000000000000000
```

注意 `after unshare` 的 uid 變成 `65534`（overflow uid，`nobody`）：此時 uid map 還沒寫，kernel 回報的是「這個 uid 在新 ns 沒有映射」。這是我們的**預期**，細節以實測為準。

要看到 `uid=0`，需要寫 `/proc/self/uid_map`：

```bash
unshare -Ur bash           # -U 新 user ns，-r 把目前 uid 映射成 ns 內的 0
id                         # 理論預期：uid=0(root) ...
cat /proc/self/uid_map     # 理論預期：         0       1000          1
```

`uid_map` 三欄是：`ns 內起始 id`、`ns 外（parent）起始 id`、`長度`。這裡 ns 內的 0 對應外面的 1000。**ns 內的 "root" 只是外面的 uid 1000**，所以對檔案系統的實際存取權限還是 uid 1000 的權限，這是它安全性的核心假設。

## 哪些操作在 user ns 內被允許（也就是攻擊面放大了什麼）

這張表是本章的核心：user ns 內拿到的 cap 能碰到哪些 kernel 程式碼。

| 子系統 | 需要的 cap | 在 user ns 內是否可達 | 說明 |
|---|---|---|---|
| netfilter / nf_tables | `CAP_NET_ADMIN`（對 net ns） | 是（搭配 `CLONE_NEWNET`） | [Ch 30](./30-netfilter-nftables.md)：nf_tables 幾乎所有 kernelCTF 提權的入口條件 |
| traffic control（qdisc / cls / act） | `CAP_NET_ADMIN` | 是 | `tc` 的各種 classifier、qdisc 歷史上 bug 多（我們不在此列未查證的編號） |
| xfrm / IPsec | `CAP_NET_ADMIN` | 是 | 擁有自己的 SA / policy |
| AF_PACKET raw socket | `CAP_NET_RAW` | 是 | [Ch 33](./33-socket-skb.md)：`PACKET_RX_RING`、`PACKET_VERSION` 一類 bug 的入口 |
| mount 特定 fs | `CAP_SYS_ADMIN`（對 mount ns）+ `FS_USERNS_MOUNT` | 僅限標記過的 fs | tmpfs、proc、sysfs、devpts、overlayfs、fuse 等（就我所知，請 grep `FS_USERNS_MOUNT`）；ext4 等 block fs 不行 |
| 巢狀 namespace（再 unshare） | `CAP_SYS_ADMIN` | 是 | 可層層疊 |
| 載入 kernel module | `CAP_SYS_MODULE`（init ns） | **否** | 全域資源，`capable()` 檢查 |
| `/dev/mem`、raw 設備 | `CAP_SYS_RAWIO` + device 權限 | 否 | 全域 |
| `mknod` 裝置檔 | `CAP_MKNOD`（init ns） | 否 | 不允許在 user ns 內建立任意裝置 |

還有一個容易被忽略的放大路徑：**module 自動載入（autoload）**。許多子系統在你第一次用到時由 kernel 呼叫 `request_module()` 自動載入（例如建立某種 socket family、用某個 nf_tables expression、某個 qdisc 型別）。就我所知，這個動作是 kernel 代為執行，不要求呼叫者在 init ns 有 `CAP_SYS_MODULE`，所以 user ns 內的使用者可以間接觸發載入一堆平常沒載入的 module，把它們的 bug 也帶進攻擊面。這是為什麼「把不需要的 module 黑名單掉」是 hardening 的一環（見 [Ch 51](./51-defense-detection.md)）。這一點的精確條件隨 module 與版本而異，請以實測為準。

## 底層機制：`unshare(CLONE_NEWUSER)` 在 kernel 裡做了什麼

把 syscall 拆開看，才知道「免費取得 cap」不是魔法，而是一組明確的步驟（以下是就我所知的流程，函式名請在 `kernel/user_namespace.c` 與 `kernel/cred.c` 驗證；細節隨版本有出入）：

```
unshare(CLONE_NEWUSER)
  └─ unshare_userns()
       ├─ prepare_creds()                 複製目前 cred
       └─ create_user_ns(new_cred)
            ├─ 檢查：沒有超過 max_user_namespaces（ucount 計數）
            ├─ 檢查：不在 chroot 內、不是多執行緒共享 fs 的特殊情況（細節以 source 為準）
            ├─ security_create_user_namespace()   LSM hook，政策可在此拒絕
            ├─ 配置新的 struct user_namespace，parent = 目前的 user_ns，owner = euid
            └─ set_cred_user_ns(cred, ns)
                 ├─ cred->cap_permitted = CAP_FULL_SET
                 ├─ cred->cap_effective = CAP_FULL_SET
                 ├─ cred->cap_inheritable / ambient 清空
                 └─ cred->user_ns = ns
```

兩個重點：

1. **授予完整 cap 的是 `set_cred_user_ns()`**：對新 ns 而言，建立者就是它的「root」。這步不看呼叫者原本有什麼 cap，所以 unprivileged 也成立。
2. **檢查發生在建立之前，而且只有三道閘**：計數上限（sysctl）、LSM hook、少數結構性限制。這就是為什麼收緊 userns 的手段只有 sysctl 與 LSM 兩條路（外加 seccomp 在 syscall 入口擋）。

建立完成後，後續的 `unshare(CLONE_NEWNET)` 或 `CLONE_NEWNS` 呼叫會用新 cred 做 `ns_capable(cred->user_ns, CAP_SYS_ADMIN)`，通過後，新的 net ns / mount ns 把 `user_ns` 欄位設成這個新 ns。這就串起了前面「資源記錄 owner user ns」的模型。

### 失敗是教學素材：寫 `gid_map` 前沒有 `setgroups deny`

```bash
# 理論預期（未實測）：在 unshare -U 後的 shell 裡手動寫映射
echo "0 $(id -u) 1" > /proc/self/uid_map      # 成功
echo "0 $(id -g) 1" > /proc/self/gid_map      # 預期失敗：Operation not permitted
echo deny > /proc/self/setgroups              # 必須先做這步
echo "0 $(id -g) 1" > /proc/self/gid_map      # 現在才成功
```

為什麼要這道規矩：傳統 Unix 有「negative permission」——檔案 mode 對 group 比對 other 更嚴格（例如 `---rwx` 表示 group 成員反而不能存取）。如果 unprivileged 的 user 能在自己的 ns 內自由 `setgroups()` 丟掉某個 group，就能繞過這類以 group 擋住自己的規則。所以 kernel 要求：unprivileged 寫 `gid_map` 前，先永久關掉這個 ns 內的 `setgroups`。這是 userns 設計「**把新能力開給 unprivileged，就要逐條檢查舊世界的安全假設**」的縮影，也是 userns 本身歷史上 bug 的來源：每個舊假設都可能漏掉一條。

## user namespace 如何放大攻擊面：一條典型的 LPE 鏈

```
1. 一般 user (uid 1000)
      │  unshare(CLONE_NEWUSER|CLONE_NEWNET)  ← 本章主題：免費取得 ns 內 CAP_NET_ADMIN
      ▼
2. 在自己的 net ns 內操作 nf_tables / tc / xfrm
      │  觸發 netfilter 的 UAF / OOB（Ch 30）
      ▼
3. kernel heap 上取得 primitive（Ch 17、18、19）
      ▼
4. 繞 mitigation，提權到 init_user_ns 的 uid 0（Ch 13–16、22–29）
```

為什麼「前置」這個詞很重要：第 2、3 步的 bug 本身 **和 userns 無關**，但沒有第 1 步，它們對 unprivileged user 是死路。kernelCTF 與真實世界的本地提權報告（Project Zero、各大 Android 之外的 Linux LPE）有很高比例依賴這條前置。這也是我們「看到一個 netfilter / tc bug，第一個問題是『可達嗎？』」的原因。

### 案例一：CVE-2022-0185（見 [Ch 34](./34-filesystem-attack-surface.md)）

`legacy_parse_param` 的 unsigned 下溢造成 heap overflow（WebSearch：Red Hat bugzilla 2040358、oss-security 通報）。**可達性條件：`CAP_SYS_ADMIN`（可以是 namespaced）+ 目標 fs 走 legacy 路徑**。oss-security 通報原文指出 unprivileged user 可用 `unshare(CLONE_NEWNS|CLONE_NEWUSER)` 取得所需 cap；bug 自 5.1 起存在。

教學重點：**這個 bug 的「漏洞」在 fs，使它對一般 user 可達的是 user namespace**。如果 sysctl 關掉 unprivileged userns，同一個 bug 的 severity 從「本地提權」降為「需要 root 的 bug」。

### 案例二：CVE-2022-0492（cgroups v1 `release_agent`）

WebSearch（Unit 42、Red Hat bugzilla 2051505、Sysdig 文章）給出的 root cause：寫入 cgroup v1 的 `release_agent` 檔案時，kernel **缺少對 `CAP_SYS_ADMIN`（在 init user ns）的檢查**。`release_agent` 指定的程式會在 cgroup 內最後一個 process 結束時由 kernel 以 root 身分執行。

user namespace 的角色：攻擊者在新 user ns 內掛載一個新的 cgroupfs（cgroup v1），取得 ns 內的 `CAP_SYS_ADMIN`，就能寫那個 cgroupfs 的 `release_agent`；因為 kernel 的檢查只確認「是不是 cgroup 檔案的擁有者 ns 的 admin」而不是 init ns，結果是在 host 上以 root 執行任意程式。Unit 42 提到，容器內的 cgroup mount 通常是唯讀，攻擊者要自己另外掛一個可寫的。Sysdig 指出 SELinux 政策可以預設擋下 OpenShift 節點上的這個逃逸。

教學重點：這是**權限檢查層級選錯**（該用 `capable()`，用成 `ns_capable()` 或沒檢查）的標準範例。對應 [Ch 10](./10-vulnerability-taxonomy.md) 的邏輯錯誤類（logic bug），不是 memory corruption，exploit 穩定、不需要 heap spray，這類 bug 特別危險。

### 案例三：netfilter 類（模式，不給未經查證的編號）

CVE-2021-22555（netfilter `xt` 相關的 heap OOB write，需要 `CAP_NET_ADMIN`，利用時透過 user namespace 取得）是常被引用的例子。**本次 WebSearch 沒有取得它的機制細節，我們不描述其 root cause**；請讀 Andy Nguyen 的公開 writeup 自己驗證。nf_tables 的整個系列（[Ch 30](./30-netfilter-nftables.md)）也是同一個模式：bug 在 netfilter，userns 提供入口。

## user namespace 本身的 bug：它們長什麼樣

user ns 的核心程式碼（`kernel/user_namespace.c`、`kernel/capability.c`、`security/commoncap.c`）規模不大、被審得多，直接的 memory corruption 不常見。我們把「userns 相關的 bug」分成幾類（描述模式，不編造編號）：

1. **權限層級混淆**（本章案例二）：該要求 init ns 的 cap，卻只檢查 namespaced cap；或該檢查「資源 owner ns」，卻檢查「呼叫者目前 ns」。
2. **uid / gid 映射處理錯誤**：`uid_map`、`gid_map`、`setgroups`、`projid_map` 的寫入規則（尤其「寫 `gid_map` 前要先 `setgroups deny`」這條，就我所知是為了防止「用 `setgroups` 丟掉 group 來繞 negative permission」）。映射計算錯誤會讓 ns 內外的 uid 對應不一致，造成 file permission 繞過。
3. **file capability / setuid 與 ns 的互動**：一個在 ns 內有 root 的 process，exec 一個有 file capability 的 binary，結果 cap 是相對於哪個 ns 計算，早期有多個 bug。
4. **resource accounting 與 rlimit**：每個 user ns 內的 `RLIMIT_*` 與 per-user 計數（`ucount`）若算錯，會造成 DoS 或資源洩漏。
5. **新功能開放進 userns 的審查疏漏**：每次有人加入「user ns 內也可以用 X」，就是新增一塊攻擊面（例如開放 overlayfs 的 unprivileged mount，就我所知在 5.11 左右，請查 commit）。

## 防禦：收緊 user namespace

我們的觀點：**如果你的系統上不跑 rootless container、Flatpak、Chrome sandbox 等依賴它的軟體，把它關掉是成本最低、收益最大的 LPE 緩解之一**。如果需要，就用 LSM 做「按程式授權」。

### 機制一覽（含發行版差異）

| 機制 | 型態 | 說明（以 WebSearch 查證為準） |
|---|---|---|
| `CONFIG_USER_NS=n` | 編譯期 | 徹底移除。Android 預設（就我所知）不開放 app 使用 user ns；各 vendor 不同，未查證 |
| `user.max_user_namespaces`（sysctl） | upstream 機制 | 設 `0` 可禁止建立新的 user ns（對 unprivileged 與 root 都適用，包含需要 userns 的 rootless container）；預設值隨記憶體量而定（Debian packaging 筆記所述，未取得 mainline 精確數字） |
| `kernel.unprivileged_userns_clone` | **Debian 專屬 patch**（非 upstream） | `1` 允許、`0` 禁止 unprivileged user 建立 user ns。Debian 從 Bullseye（11，kernel 5.10+）起預設為允許（等同 upstream 行為）；此 knob 屬於 Debian patch，可能被移除，Debian 討論串建議改用 `user.max_user_namespaces=0` |
| Ubuntu `kernel.apparmor_restrict_unprivileged_userns` | LSM（AppArmor） | Ubuntu 23.10 引入、24.04 預設啟用（Ubuntu blog、Qualys 2025 報告）：unprivileged process 只有在 AppArmor profile 含 `userns,` 規則（或有 `CAP_SYS_ADMIN`）時才能建立 user ns |
| seccomp（container runtime 預設 profile） | syscall filter | 容器 runtime 預設 seccomp 常擋 `unshare` 與帶 `CLONE_NEWUSER` 的 `clone`（就我所知 Docker 預設 profile 如此，除非有 `CAP_SYS_ADMIN`；請查你的 runtime 版本） |
| LSM hook `security_create_user_namespace` | LSM | 讓 SELinux / BPF LSM 等能寫政策拒絕 userns 建立（就我所知 6.1 前後加入，請查 commit 確認版本） |
| RHEL 系 | 未驗證 | 本次 WebSearch 沒有查到 RHEL 的預設值，請自行在你的版本驗證 `sysctl user.max_user_namespaces` |

注意 Ubuntu 的細節：Qualys 的報告（2025）指出 24.04 上 `kernel.unprivileged_userns_clone` 預設仍然是 1，但因為 AppArmor 限制，unprivileged 使用者的 user ns 建立仍然被擋，且該報告列出三個繞過方式。教學結論：**LSM 式限制是縱深而不是牆，看 CVE 時不能預設它擋得住**。

### 關閉之後的代價

```
關掉 unprivileged userns ⇒ 以下依賴它的軟體會受影響
  - rootless Podman / rootless Docker
  - Flatpak / bubblewrap / Snap 的 sandbox
  - Chrome / Chromium 的 user-namespace sandbox（可退回 setuid sandbox 或無 sandbox）
  - Electron 應用（VS Code 等已有使用者回報被 Ubuntu 限制影響，WebSearch 結果所述）
  - 一些 CI 與 build 工具（Tor Browser build、SingularityCE 在 24.04 的回報）
```

## 對比取捨表

| 方案 | 防到什麼 | 代價 | 適用情境 |
|---|---|---|---|
| 全關（`max_user_namespaces=0`） | 所有依賴 userns 入口的 LPE | 破壞 rootless container 與多個 sandbox | 伺服器、不跑這類軟體的環境 |
| AppArmor 按程式授權（Ubuntu） | 未授權程式的 userns 入口 | 要維護 profile；有繞過研究 | 桌面、需要選擇性開放 |
| 不改，靠修 bug | 無 | 每個新 bug 都是 LPE | 不建議，但是很多環境的現狀 |
| seccomp 擋 `CLONE_NEWUSER` | 容器內的 process | 容器本身要有人維護 profile | 容器 runtime |
| 用 `CONFIG_USER_NS=n` 的 kernel | 徹底 | 完全無法使用 userns | 嵌入式、特定硬化 kernel |
| 改用 VM 隔離 | 把 LPE 變成 VM escape | 成本高 | 高風險工作負載 |

## 踩雷集錦

1. **「ns 內的 root = host 的 root」**：錯。ns 內 uid 0 在 host 看是原本的一般 uid（`uid_map` 的第二欄）。真正的風險不是「變成 host root」，而是「用 ns 內的 cap 碰到本來碰不到的 kernel 程式碼」。把兩件事分開，才不會一邊低估（以為 ns 內 root 沒用）、一邊高估（以為已經逃逸）。
2. **「user ns 內拿到的 cap 就是全部 capability」**：只對 ns 擁有的資源成立。`CAP_SYS_MODULE`、`CAP_SYS_RAWIO`、`CAP_MKNOD` 這類作用在全域資源的 cap，在 user ns 內不會讓你載入 module 或建裝置。評估一個 bug 能不能在 userns 內觸發，要看它的檢查用 `capable()` 還是 `ns_capable()`。
3. **「關掉 `kernel.unprivileged_userns_clone` 就一勞永逸」**：這個 sysctl 是 Debian 專屬 patch，mainline 沒有；而且 Ubuntu 24.04 上它預設還是 1，真正生效的是 AppArmor 的 `kernel.apparmor_restrict_unprivileged_userns`（Qualys 報告所述）。**不同發行版的 knob 不同，先確認你用的 kernel 有沒有那個 knob**，找不到 sysctl 檔不代表被關了。
4. **「CAP_NET_ADMIN in userns 不能碰 nf_tables」**：能。netlink 的權限檢查是對 socket 所屬 net ns 的 owner user ns 做的；用 `CLONE_NEWNET` 建的 net ns 的 owner 就是新 user ns。所以 `CLONE_NEWUSER` **一定要搭配 `CLONE_NEWNET`**（否則 net ns 仍是 init 的，netlink 檢查通過不了），這是寫 PoC 與讀 exploit 時常被忽略的細節。
5. **「userns 限制是 kernel 修補」**：多數限制是**發行版 patch 或 LSM policy**，不是 mainline kernel 行為。你拿 mainline 6.6 在 VM 跑，和 Ubuntu 24.04 的行為可能不同。釘死環境才能重現。
6. **「LSM 擋住就不用升級 kernel」**：Qualys 2025 報告列出三種繞過 Ubuntu 限制的方式（WebSearch 標題所述）。把它當縱深，不當唯一防線。
7. **gid_map 要先 `setgroups deny`**：unprivileged 寫 `gid_map` 前要先對 `/proc/self/setgroups` 寫 `deny`，否則 `EPERM`。這是 uid/gid 映射的細節，自己寫 PoC 或排錯時常撞到（理論預期，請實測）。

## 進階：再往深一層

- **讀 `ns_capable` 的實作**：`security/commoncap.c` 的 `cap_capable()`，裡面有一個沿著 user ns 父鏈往上走的迴圈：「如果目標 ns 的 owner 是我 → 通過；往上一層再試」。讀完你就能自己推導「哪些操作可達」。
- **`CLONE_NEWUSER` 的權限來源**：`create_user_ns()` 檢查 `max_user_namespaces`、`ucount`、`chroot` 狀態（在 chroot 內不能建 user ns，就我所知是防止繞過 chroot），以及 LSM hook。
- **kernelCTF 的 userns 政策**：不同賽道的 userns 設定不同（就我所知 mitigation 賽道會收緊，請讀 [Ch 49](./49-kernelctf-overview.md) 與官方 repo 的規則），這直接影響「這個 bug 能不能提交」。
- **容器逃逸視角**：userns 讓 rootless container 更安全，但也讓「container 內的 root」握有 ns 內的 cap，用它打 kernel bug 是 [Ch 47](./47-container-vm-escape.md) 的主題。
- **偵測**：`unshare`、`clone(CLONE_NEWUSER)` 在 auditd / eBPF（tracepoint `sys_enter_unshare`）上可觀察；一個一般 user 帳號頻繁呼叫它，值得告警（細節見 [Ch 51](./51-defense-detection.md)）。

## 動手練習

> 在你**自己擁有的 Linux VM**進行，以下**皆未實測**。

1. **跑本章的 C 程式**，比較 `before unshare` 與 `after unshare` 的 `Cap*`。再試 `unshare -Ur bash`，看 `id` 與 `/proc/self/uid_map`。
2. **只用 `CLONE_NEWUSER`（不加 `CLONE_NEWNET`）**，嘗試 `ip link add dummy0 type dummy`，觀察是否失敗；加上 `CLONE_NEWNET` 再試。解釋差異（對應踩雷 4）。
3. **讀 source**：在 6.6 source 裡找 `cap_capable()`（`security/commoncap.c`），畫出父鏈迴圈的流程圖；再找 `netlink_net_capable()`（`net/netlink/af_netlink.c`），確認它對哪個 ns 做檢查。
4. **讀 CVE-2022-0492 的 fix**：照 Unit 42 與 Red Hat bugzilla 2051505 找到 commit，指出它新增了什麼檢查（我們的描述是「缺少 init ns 的 `CAP_SYS_ADMIN` 檢查」，核對 patch 確認或修正）。
5. **切換限制**：在 Ubuntu 24.04 VM 上對比 `sysctl kernel.apparmor_restrict_unprivileged_userns` 的 0 / 1，觀察 `unshare -U` 的結果；在 Debian 上看 `sysctl kernel.unprivileged_userns_clone` 是否存在。
6. **寫偵測**：用 `bpftrace -e 'tracepoint:syscalls:sys_enter_unshare { printf("%s %d\n", comm, args->unshare_flags); }'` 看哪些程式在呼叫 unshare（理論預期；`CLONE_NEWUSER` 的值是 `0x10000000`）。

## 本章重點整理

- user namespace 讓 capability 變成「對某個 namespace 的鑰匙」；unprivileged user 建立 userns 後在 ns 內握有全部 cap，並且擁有 `CLONE_NEWNET` 等 ns 內的資源。
- 它放大攻擊面：nf_tables、tc、xfrm、AF_PACKET 與特定 fs 的 mount 路徑，原本 root only，現在任何本地帳號可達。user ns 本身的 bug 不多，**它的角色是前置條件**。
- 兩個教學案例：CVE-2022-0185（fs 的 integer 下溢，userns 提供 `CAP_SYS_ADMIN`）、CVE-2022-0492（權限層級檢查缺失，邏輯 bug）。
- 防禦是 sysctl + LSM + seccomp + 編譯期的組合，不同發行版機制不同；Debian 的 knob 是專屬 patch，Ubuntu 靠 AppArmor。
- 評估 CVE 時，把「可達性」當成第一個問題，它取決於發行版的 userns 政策。

## 自我檢核

- [ ] 不看筆記，能解釋 `capable()` 與 `ns_capable()` 的差別，並舉一個各自適用的 kernel 操作。
- [ ] 能說出為什麼 `CLONE_NEWUSER` 要搭配 `CLONE_NEWNET` 才能打 nf_tables。
- [ ] 面試問「user namespace 讓 container 更安全還是更危險」，你能分別從 rootless 與攻擊面放大兩側回答。
- [ ] 給你一個 netfilter UAF 與一台 Debian 12、一台 Ubuntu 24.04，你能說出各自預設下 unprivileged user 能不能觸發，並說出依據與你不確定的地方。
- [ ] 能解釋 CVE-2022-0492 為什麼是邏輯 bug 而不是 memory corruption，以及這對 exploit 穩定性意味什麼。
- [ ] 想一個你自己環境中依賴 userns 的軟體，評估關掉 userns 的代價。

## 延伸閱讀

### 官方文件

- **[user_namespaces(7) man page](https://man7.org/linux/man-pages/man7/user_namespaces.7.html)**
  - **讀哪裡**：「Capabilities」與「Interaction of user namespaces and other types of namespaces」兩節，以及 uid_map / setgroups 的規則。
  - **為什麼值得讀**：本章 capability 映射規則的權威來源；前提是懂 Linux capability 基本概念（`capabilities(7)`）。
- **[Ubuntu Discourse — Understanding AppArmor User Namespace Restriction](https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007)**
  - **讀哪裡**：整篇，特別是 profile 的 `userns,` 規則。
  - **學什麼**：發行版如何在不拿掉功能的前提下收緊 userns。

### 漏洞與研究

- **[Qualys — three bypasses of Ubuntu unprivileged user namespace restrictions（2025）](https://www.qualys.com/2025/three-bypasses-of-Ubuntu-unprivileged-user-namespace-restrictions.txt)**
  - **讀哪裡**：三個繞過各自利用的是哪個可信 binary 或 profile 的行為。
  - **和本章的關聯**：說明為什麼 LSM 限制是縱深而不是牆；前提是懂 AppArmor profile 基本語意。
- **[Unit 42 — CVE-2022-0492 cgroups](https://unit42.paloaltonetworks.com/cve-2022-0492-cgroups/)**
  - **讀哪裡**：root cause 與「container 內自己 mount cgroupfs」的利用流程。
  - **學什麼**：邏輯 bug 如何借 userns 逃出 namespace。
- **[oss-security — CVE-2022-0185 通報](https://www.openwall.com/lists/oss-security/2022/01/18/8)**
  - **讀哪裡**：開頭描述 unprivileged userns 條件的段落。
  - **和本章的關聯**：fs bug 因 userns 對一般 user 可達的原始說法。

userns 把攻擊面放到一般 user 手上；接下來我們離開「通用機制」，用一章快速巡覽其他曾出過 LPE 的子系統：ksmbd、GPU/DRM、USB、v4l2、perf、timerfd，建立「看到這些名字該聯想什麼」的地圖。

→ [Ch 37 其他子系統速覽：ksmbd / GPU-DRM / USB / v4l2 / perf / timerfd](./37-other-subsystems.md)
