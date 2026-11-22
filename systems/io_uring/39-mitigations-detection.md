# Ch 39 — 緩解與偵測：io_uring_disabled、seccomp、LSM

> **目標**：從攻擊面轉到防禦面。我們要把「關掉或限制 io_uring」的每一個現成機制講清楚——`io_uring_disabled` sysctl 的三級語意、用 seccomp 擋 io_uring 的三個 syscall、io_uring 曾經繞過 seccomp 的歷史與現況、LSM hooks——並親手在 WSL 跑能跑的部分、貼真實輸出。最後回答那個有痛感的問題：要不要為自己的系統停用 io_uring，怎麼取捨。

> **環境**：WSL2 kernel 6.18.58.1、liburing（系統 2.1，實測足夠）、gcc 11.4。對照 6.18 `io_uring/io_uring.c`、`sqpoll.c`。本章有實測段落並貼真輸出；需要 root 的部分（切換 `io_uring_disabled`）以原始碼佐證並標注「需 root 驗證」。所有實驗在自有環境進行。

## 為什麼防禦面要單獨講

前面八章（Ch 31–38）教的是「漏洞為何發生、長什麼樣、怎麼辨識」。但辨識出一個子系統風險高之後，防禦者的下一個問題很具體：**我手上有哪些開關、各自擋住什麼、關了會痛在哪？** io_uring 的特殊之處在於，它不像一般子系統只能靠修 bug——它有一套**粗粒度的系統級開關**，可以整個關掉或分級限制。這是因為 io_uring 是給應用拿效能用的「加值功能」，不是 kernel 運作的必需品，所以社群（在 Google 推動下）特地給了它一個 kill switch。

這一章把這些開關一個個拆開。重點不是「全部關掉最安全」——那誰都會說——而是**每個機制的確切邊界**：`io_uring_disabled=1` 到底擋誰、seccomp 擋 `io_uring_enter` 夠不夠、LSM hook 能做到多細。只有知道邊界，你才能做出有依據的取捨，而不是拍腦袋 harden。

## 先建立直覺：三道防線，作用在不同的層

```
   user 程式想用 io_uring
          │
          ▼
 ┌─────────────────────────────────────────────────────────┐
 │ 防線 1：io_uring_disabled sysctl                         │
 │   在 io_uring_setup 入口擋。0=全開 1=限特定人 2=全禁      │
 │   最粗、最有效，但全系統一刀切                           │
 ├─────────────────────────────────────────────────────────┤
 │ 防線 2：seccomp-bpf                                      │
 │   per-process 擋 io_uring_setup/enter/register syscall   │
 │   顆粒到程序，但只能擋「syscall 入口」，擋不了已建好的 ring│
 ├─────────────────────────────────────────────────────────┤
 │ 防線 3：LSM hooks (security_uring_*)                     │
 │   SELinux/AppArmor 等策略決定「誰能建 ring / 用某些能力」 │
 │   最細、可寫策略，但要 LSM 支援且策略要寫對              │
 └─────────────────────────────────────────────────────────┘
```

三道防線不是互斥的，是分層的：`io_uring_disabled` 是全系統的總閘，seccomp 是把特定程序（沙箱內的、不信任的）單獨關掉，LSM 是用策略語言做細緻的「誰可以、誰不行」。實務上常常組合使用——例如 Android 用 seccomp 讓一般 app 搆不到，再規劃用 SELinux 限制到少數系統程序。

## 防線一：io_uring_disabled sysctl

這是最重要的開關，kernel **6.6** 引入（由 Google 的 Matteo Rizzo 推動，背景是 Ch 31 講的 kernelCTF 數據）。它有三級語意：

| 值 | 語意 | `io_uring_setup` 行為 |
|---|---|---|
| `0` | 全開，任何程序都能建 ring | 正常成功 |
| `1` | 僅特定人：有 `CAP_SYS_ADMIN`，或屬於 `io_uring_group` | 其餘回 `-EPERM` |
| `2` | 全系統禁止，任何人都不能建新 ring | 一律回 `-EPERM` |

一個重要細節：`disabled=2` 禁止的是**建立新 ring**，**已經存在的 ring 仍可使用**。這是刻意的——避免在執行期切換時打斷正在用 io_uring 的服務。

先看一手實作，這是整個機制的權威來源，`io_uring.c` 的 `io_uring_allowed()`（6.18，一字不改）：

```c
static inline int io_uring_allowed(void)
{
	int disabled = READ_ONCE(sysctl_io_uring_disabled);
	kgid_t io_uring_group;

	if (disabled == 2)
		return -EPERM;

	if (disabled == 0 || capable(CAP_SYS_ADMIN))
		goto allowed_lsm;

	io_uring_group = make_kgid(&init_user_ns, sysctl_io_uring_group);
	if (!gid_valid(io_uring_group))
		return -EPERM;

	if (!in_group_p(io_uring_group))
		return -EPERM;

allowed_lsm:
	return security_uring_allowed();
}
```

逐行讀出它的語意，這比任何二手說明都準：

- `disabled == 2` → 直接 `-EPERM`，不管你是誰。
- `disabled == 0`，或你有 `CAP_SYS_ADMIN` → 跳過群組檢查，直接去問 LSM。
- 剩下的情況（`disabled == 1` 且無 `CAP_SYS_ADMIN`）→ 檢查 `io_uring_group`：群組沒設定（invalid）就 `-EPERM`，你不在群組裡也 `-EPERM`。
- 通過前面所有關卡，最後還要過 `security_uring_allowed()` 這個 LSM hook（防線三）。

> **常見簡化的修正**：很多資料把 `disabled=1` 說成「僅 CAP_SYS_ADMIN」。6.18 的精確語意是「**CAP_SYS_ADMIN 或屬於 `io_uring_group`**」。`io_uring_group` 這個 sysctl（預設 -1，即未設定）讓管理員可以開一個白名單群組，只讓群組成員用 io_uring，而不必給他們完整的 `CAP_SYS_ADMIN`。分析或設定時用精確版本，別用簡化版。

這個 sysctl 掛在 `kernel.io_uring_disabled`，由標準 sysctl 機制註冊（`io_uring.c` 的 `kernel_io_uring_disabled_table`，`.extra1 = SYSCTL_ZERO`、`.extra2 = SYSCTL_TWO`，所以合法值就是 0/1/2）。

### 實測一：讀當前值，並確認 disabled=0 下 setup 成功

WSL2 預設 `io_uring_disabled=0`。我們讀出來，並跑一支最小程式確認 `io_uring_setup` 正常：

```bash
$ cat /proc/sys/kernel/io_uring_disabled
0
```

```c
/* trysetup.c：最小 io_uring_setup 測試 */
#include <stdio.h>
#include <string.h>
#include <liburing.h>
int main(void) {
    struct io_uring ring;
    int ret = io_uring_queue_init(8, &ring, 0);
    if (ret < 0) { printf("io_uring_queue_init failed: %d (%s)\n", ret, strerror(-ret)); return 1; }
    printf("io_uring_setup OK, ring_fd=%d\n", ring.ring_fd);
    io_uring_queue_exit(&ring);
    return 0;
}
```

實測輸出（kernel 6.18.58.1，`disabled=0`）：

```
io_uring_setup OK, ring_fd=3
```

### disabled=2 的行為（需 root 驗證）

把 `io_uring_disabled` 切到 2 需要 root（`echo 2 | sudo tee /proc/sys/kernel/io_uring_disabled`）。本次 WSL 環境的 sudo 需要互動密碼，無法在自動化流程裡非互動切換，因此這一段的結果我們**從原始碼佐證**而非貼執行輸出：依 `io_uring_allowed()` 第一個分支，`disabled == 2` 時 `io_uring_setup` 一律回 `-EPERM`，上面那支 `trysetup.c` 會印出 `io_uring_queue_init failed: -1 (Operation not permitted)`。你在自有、可 root 的環境可這樣驗證：

```bash
# 需 root。切到 2 後，非特權程序建 ring 應失敗
echo 2 | sudo tee /proc/sys/kernel/io_uring_disabled
./trysetup          # 預期：Operation not permitted
echo 0 | sudo tee /proc/sys/kernel/io_uring_disabled   # 記得復原
```

> **持久化**：臨時 `echo` 到 `/proc/sys/...` 重開機失效。要持久，寫進 `/etc/sysctl.d/99-io_uring.conf`：`kernel.io_uring_disabled = 2`。這也是 ChromeOS / Google 伺服器那種「整台關掉」的實作方式。

## 防線二：用 seccomp 擋 io_uring

`io_uring_disabled` 是全系統的。如果你只想把**某個不信任的程序**（沙箱、容器裡的 worker、瀏覽器 renderer）擋掉 io_uring，而不影響全系統，就用 seccomp-bpf。io_uring 的入口只有三個 syscall：

- `io_uring_setup`（建 ring）
- `io_uring_enter`（提交/等待）
- `io_uring_register`（註冊 files/buffers 等）

擋掉這三個（尤其 `io_uring_setup`）就讓程序無法新建並使用 io_uring。seccomp 的好處是 **per-process、不需 root**（配合 `NO_NEW_PRIVS`），壞處下一節講。

### 實測二：seccomp 擋掉 io_uring_setup，前後對照

我們寫一支程式：先正常建一次 ring（證明環境可用），裝上一個「對三個 io_uring syscall 回 `EPERM`」的 seccomp filter，再建一次，看它被擋下。filter 用原始 cBPF（不依賴 libseccomp），核心片段：

```c
static int install_filter(void) {
    struct sock_filter filter[] = {
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K, __NR_io_uring_setup,    3, 0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K, __NR_io_uring_enter,    2, 0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K, __NR_io_uring_register, 1, 0),
        BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_ALLOW),
        BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA)),
    };
    struct sock_fprog prog = { .len = 6, .filter = filter };
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) return -1;   /* 不需 root 的關鍵 */
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog)) return -1;
    return 0;
}
```

`main` 裡前後各 `io_uring_queue_init` 一次，中間裝 filter。實測輸出（WSL2 6.18.58.1，**非特權 uid=1000**）：

```
before filter: io_uring_queue_init ret=0 (OK)
seccomp filter installed (NO_NEW_PRIVS, unprivileged uid=1000)
after  filter: io_uring_queue_init ret=-1 (Operation not permitted)
```

裝上 filter 前 `setup` 成功、裝上後回 `-EPERM`，而且全程不需 root（靠 `NO_NEW_PRIVS`）。這正是 Android 對一般 app 的做法：用 seccomp-bpf 讓 io_uring 對 app 不可達。

### seccomp 的致命盲點：io_uring 曾是「syscall filter 繞過機制」

這裡要講 io_uring 安全史上最重要的一段脈絡，防禦者必須懂。**seccomp 過濾的是 syscall；而 io_uring 的設計恰恰是「不用為每個操作各叫一次 syscall」。** 一旦一個 ring 建好了，你可以透過它要求 kernel 做 `openat`、`read`、`connect`、`sendmsg`……而這些操作**不經過對應的 syscall 入口**，於是**不受 seccomp filter 檢查**。

具體說：你用 seccomp 擋了 `openat`，想禁止一個沙箱程序開檔。但如果這個程序能建 io_uring，它就能提交 `IORING_OP_OPENAT`，kernel 內部去做開檔，**你擋 `openat` 的 filter 完全看不到**。kernel 開發者對此直言不諱——Kees Cook 稱 io_uring 是「一個相當巨大的 syscall filter 繞過機制」，Andy Lutomirski 指出如果一個函式庫先建了 ring、之後才載入 seccomp filter，io_uring 就繞過了那個 filter。根本原因是「要過濾的有趣資料大多在 submission queue 這個 out-of-band 的環裡」，seccomp 的 per-syscall 模型看不到。

**這段歷史的實務結論，至今仍成立**：如果你的沙箱靠 seccomp 擋某些操作（開檔、連網），你**必須同時擋掉 `io_uring_setup`**，否則沙箱內的程式可以用 io_uring 繞過你所有的 per-syscall filter。換句話說——

> **seccomp 擋 io_uring 的正確姿勢是「擋入口」，不是「擋操作」。** 你擋不了 `IORING_OP_OPENAT`（它不是 syscall），你只能擋 `io_uring_setup`（它是 syscall），從源頭不讓 ring 被建起來。上面實測二擋的就是入口，這是對的；任何「擋了 openat 就以為安全」的沙箱，在能建 io_uring 的情況下都是漏的。

至於「讓 seccomp 看得懂 io_uring 操作」的努力：社群有在推進對 io_uring 做 BPF-style 的 per-opcode 過濾（例如對 `IORING_OP_OPENAT` 檢查 open flags 的 restricted-ring 機制），但截至 6.18 這仍屬**新進、部分為 proof-of-concept** 的狀態，不能當成現成可靠的防線。現階段可靠的仍是「擋 `io_uring_setup`」這條粗但確定的路。

## 防線三：LSM hooks

最細的一層是 LSM（Linux Security Module，SELinux / AppArmor / 自訂模組）。6.18 的 io_uring 在幾個關鍵點埋了 LSM hook，讓安全策略能介入：

| LSM hook | 位置（6.18） | 攔截時機 |
|---|---|---|
| `security_uring_allowed()` | `io_uring.c` `io_uring_allowed()` 尾端 | 每次 `io_uring_setup`，決定「這個主體能不能建 ring」 |
| `security_uring_sqpoll()` | `sqpoll.c` | 建立 SQPOLL thread 時（SQPOLL 讓 kernel thread 代跑，權限更敏感） |
| `security_uring_override_creds()` | `io_uring.c`（issue 路徑） | 請求用 registered personality 覆寫 creds 時，檢查該 creds 是否允許 |

這幾個 hook 為什麼是這三個、而不是「每個 opcode 一個」？因為 io_uring 的操作不經 syscall 入口（這正是 seccomp 盲點的根源），要在**每個 I/O 操作**上做 LSM 檢查，等於要在 io_uring 內部重做一遍 VFS/網路層已有的 LSM 檢查——而好消息是，io_uring 的操作**本來就會呼叫底層的 `vfs_*` / socket 函式**，那些函式裡既有的 LSM hook（`security_file_open` 等）**依然會被觸發**。也就是說，io_uring 做 `IORING_OP_OPENAT` 時，開檔本身的 LSM 檢查不會被繞過——被繞過的是 **seccomp 的 syscall 層過濾**，不是 LSM 的物件層過濾。這是一個對防禦者很重要、也常被混淆的區別：**seccomp（syscall 層）會被 io_uring 繞過，LSM（物件層）大致不會**。io_uring 自己額外加的這三個 hook，補的是「建 ring / 用 SQPOLL / 換身分」這幾個 syscall 層與物件層都管不到的 io_uring 特有能力。

這幾個 hook 的設計對應了 io_uring 的幾個敏感能力：建 ring 本身、用獨立 kernel thread 輪詢（SQPOLL）、以及用別的身分（personality / override creds）執行操作。注意 `security_uring_allowed()` 是在 `io_uring_disabled` / group 的檢查都通過**之後**才呼叫——也就是說 LSM 是最後一道、也是可以寫最細策略的一道。SELinux 可以用它寫出「只有 `container_t` 類型的程序能建 ring」這種策略，比 `io_uring_disabled` 的全系統三級開關細緻得多。

要用這道防線，kernel 要編進對應的 LSM（`CONFIG_SECURITY` 加具體模組），而且策略要寫對——這也是它的門檻：它最靈活，但也最依賴你的策略品質。對多數自管系統，`io_uring_disabled` + seccomp 的組合已經覆蓋大部分需求，LSM 留給有完整 SELinux/AppArmor 策略體系的環境。

## 實測三：用 probe 看這個 kernel 支援哪些 op

防禦評估時，知道「這個 kernel 的 io_uring 支援到什麼程度」也有用——越多 opcode、越新的機制，攻擊面越大（Ch 31 原因四）。`io_uring_get_probe` 能查。這段不需任何權限：

```c
struct io_uring_probe *p = io_uring_get_probe();
printf("last supported opcode: %d\n", p->last_op);
/* 逐一 io_uring_opcode_supported(p, i) ... */
```

實測輸出（WSL2 6.18.58.1）：

```
last supported opcode: 62
  op  0 NOP              : yes
  op  1 READV            : yes
  op  2 WRITEV           : yes
  op  3 FSYNC            : yes
  op  4 READ_FIXED       : yes
  op  5 WRITE_FIXED      : yes
  op  6 POLL_ADD         : yes
  op  7 POLL_REMOVE      : yes
  op  8 SYNC_FILE_RANGE  : yes
  op  9 SENDMSG          : yes
  op 10 RECVMSG          : yes
  op 11 TIMEOUT          : yes
```

`last_op = 62` 說明這個 kernel 的 io_uring opcode 已排到 62 號——Ch 31 講的「攻擊面持續擴張」在這個數字上很具體。注意：`io_uring_get_probe` 本身要能建一個臨時 ring 才能查，所以如果 `io_uring_disabled=2`，probe 也會失敗（回 NULL）——這本身就是一個「io_uring 是否被禁用」的側面偵測手段。

## 對比與取捨：要不要停用 io_uring

把三道防線放進一張決策表。這才是這一章對防禦者最有價值的部分——不是「怎麼關」，而是「該不該關、關哪一層」。

| 你的情境 | 建議 | 理由 |
|---|---|---|
| 一般桌機 / 不跑 io_uring 應用的伺服器 | `io_uring_disabled=2` | 你根本不用它，關掉是淨收益，零痛感 |
| 跑 io_uring 應用的伺服器（DB、proxy） | `disabled=1` + `io_uring_group` 白名單 | 只讓需要的服務用，其餘程序一律擋 |
| 多租戶 / 跑不信任程式碼（容器、沙箱） | seccomp 擋 `io_uring_setup`（per-container） | 粒度到程序；同時務必別只靠 seccomp 擋操作 |
| 有完整 SELinux/AppArmor 策略體系 | LSM hook 寫細策略 | 最靈活；能做「只有某類程序能建 ring」 |
| 需要極限效能、且信任所有本地程式 | `disabled=0`，靠 kernel 更新與偵測 | 接受攻擊面，換效能；配合及時打 patch |

Google 的分產品決策（Ch 31）正是這張表的真實案例：ChromeOS 不需要它給 app → 直接關（相當於 `disabled=2`）；Android 一般 app 不該用但系統程序要 → seccomp 擋 app + 規劃 SELinux 限系統程序；生產伺服器評估後 → 關。**關鍵洞察是：停用 io_uring 的成本不是零（你放棄一整套 async I/O 效能），但對「根本不用它」的系統，成本趨近於零、收益很實在。** 多數防禦者的第一步應該是誠實回答「我的系統真的在用 io_uring 嗎」——很多系統的答案是「沒有」，那 `disabled=2` 就是免費的攻擊面削減。

## 踩雷集錦（防禦設定時常見誤解）

1. **以為 seccomp 擋了 `openat` 就擋住了 io_uring 的開檔。** 擋不住。`IORING_OP_OPENAT` 不經 `openat` syscall 入口，seccomp 看不到。要擋 io_uring 的檔案操作，唯一可靠的辦法是從源頭擋 `io_uring_setup`。這是 io_uring 安全史上最重要的一課。

2. **把 `io_uring_disabled=1` 理解成「只有 root / CAP_SYS_ADMIN 能用」。** 6.18 的精確語意是「CAP_SYS_ADMIN **或** `io_uring_group` 成員」。如果你設了 `io_uring_group` 卻忘了這一點，可能會意外放行一整個群組。

3. **以為 `disabled=2` 會立刻掐斷正在跑的 io_uring 服務。** 不會。它只擋**新建** ring，已存在的 ring 繼續可用。要讓切換生效到既有服務，得重啟那些服務。

4. **臨時 `echo` 到 `/proc/sys` 就當成已經 harden。** 那重開機就失效。要持久得寫 `/etc/sysctl.d/`。偵測別人系統是否「真的」關了 io_uring，也別只看當下值，要看開機設定。

5. **把「社群在做 io_uring 的 BPF 過濾」當成現成防線。** 截至 6.18 那還是新進、部分 PoC 的機制，不能依賴。現階段可靠的粗粒度防線仍是 `io_uring_disabled` 與「擋 setup」的 seccomp。

6. **只關 io_uring 卻忘了它只是攻擊面之一。** 關掉 io_uring 削減的是這一塊，netfilter、eBPF（本 repo kernel_pwn Ch 30、32）等礦區還在。io_uring 停用是 harden 的一環，不是終點。

## 進階：偵測與審計角度

停用之外，防禦者也常需要**偵測** io_uring 的使用：

- **誰在用 io_uring**：`io_uring_setup` 是可被 audit / eBPF 監控的 syscall。在不信任環境裡，一個程序突然建 ring 本身就是值得告警的訊號（尤其該環境政策上不該用它）。
- **probe 當偵測**：如前述，`io_uring_disabled=2` 時 probe 失敗——可用來遠端/腳本確認某台機器是否已停用。
- **fdinfo**：io_uring 的 ring 以 fd 形式存在，`/proc/<pid>/fdinfo/<fd>` 有該 ring 的狀態（Ch 的 fdinfo 機制），可供取證與審計。

### 一份最小偵測配方

把偵測落成可操作的步驟，對一台你想盯著的機器：

1. **確認停用狀態（不只看當下值）**：
   ```bash
   cat /proc/sys/kernel/io_uring_disabled          # 當下值
   grep -r io_uring_disabled /etc/sysctl.conf /etc/sysctl.d/   # 開機設定
   ```
   兩者要一致，否則重開機後保護會變。
2. **列出正在使用 io_uring 的程序**：io_uring 的 ring 是個 anon inode fd，可從 `/proc/*/fd` 的 symlink 指向 `anon_inode:[io_uring]` 認出來：
   ```bash
   ls -l /proc/*/fd 2>/dev/null | grep -i 'io_uring'
   ```
   在「政策上不該有人用 io_uring」的環境裡，任何一行命中都值得查。
3. **持續監控建立事件**：`io_uring_setup` 是可被 audit 子系統或 eBPF tracepoint 監控的 syscall。在高安全環境把「某程序呼叫 `io_uring_setup`」設成告警條件——尤其是非預期的程序（例如一個本該只做 CPU 運算的 worker 突然建 ring）。
4. **取證看 fdinfo**：對一個活著的 ring，`cat /proc/<pid>/fdinfo/<fd>` 能看到該 ring 的 SQ/CQ 狀態與註冊的資源，供事後分析。

這份配方刻意全部用不需特殊工具的手段（proc、sysctl、audit/eBPF 任一），因為防禦偵測的第一步往往是「先用手邊有的東西看清楚現況」，再決定要不要上更重的工具。

### 和 namespace / 容器的互動

一個常被問的問題：`io_uring_disabled` 是**全系統**的，它不是 per-namespace 的 sysctl——在一個 container（network/pid/user namespace）裡看到的 `kernel.io_uring_disabled` 和 host 是同一個值，容器內的非特權 root 也改不動它（受 sysctl 寫入權限與 namespace 限制）。這有兩個含意：(1) 你**不能**靠「在某個容器裡把它設成 2」來只關那個容器的 io_uring——要 per-container 控制得用 seccomp（防線二）綁在容器的 seccomp profile 上，這也是 Docker/containerd 一類 runtime 的作法；(2) host 層設 `disabled=2` 會**同時**關掉所有容器的 io_uring。規劃多租戶環境時，「全系統 sysctl」vs「per-container seccomp」的分工要想清楚：前者是大閘，後者才是能分租戶的細控。

### 緩解的分工：堵洞 vs 降低可利用性

最後釐清一個防禦者常混淆的分層。對付 io_uring 漏洞的手段作用在不同的層，不要混為一談：

| 手段 | 作用層 | 擋住什麼 | 不擋什麼 |
|---|---|---|---|
| `io_uring_disabled` / seccomp | **攻擊面** | 讓 io_uring 搆不到 → bug 碰不到 | 已經在用 io_uring 的服務仍暴露 |
| LSM 策略 | **攻擊面（細粒度）** | 限制誰能用、用到什麼程度 | 被允許者路徑上的 bug |
| KASLR / SMEP / SMAP / CFI | **利用階段** | 讓 bug 更難被打成提權 | bug 本身仍在，只是更難利用 |
| `init_on_free` / FREELIST_HARDENED / random caches | **reclaim 階段** | 讓 UAF 的拍 2（佔回）更難、更不穩 | bug 本身仍在（Ch 32） |
| 打 patch / 升 kernel | **bug 本身** | 真正修掉 root cause | 只對已知已修的 bug 有效 |

這張表的重點：**只有「打 patch」真正消滅 bug，其餘全是「降低風險」而非「消除風險」。** `io_uring_disabled` 把攻擊面整個拿掉（對不用它的系統是最乾淨的解），但對必須用 io_uring 的服務，剩下的就是「及時升 kernel + 靠利用階段緩解拖慢攻擊者」。防禦者要能清楚說出「我這層擋的是攻擊面、利用、還是 bug 本身」，才不會在一層防線上獲得虛假的安全感。

這些偵測手段和 Ch 40 的審計視角接起來：停用是「堵」，偵測是「看」，審計是「找出下一個該堵的洞」。

## 本章重點整理

- 三道防線分層：**`io_uring_disabled`（全系統總閘，6.6 引入，0/1/2）**、**seccomp（per-process 擋三個 syscall）**、**LSM hooks（策略級細控）**。
- `io_uring_disabled` 精確語意（6.18 原始碼）：2=全禁回 `-EPERM`；1=需 `CAP_SYS_ADMIN` 或屬 `io_uring_group`；0=全開；三者通過後還要過 `security_uring_allowed()`。禁用只擋新建 ring，既有 ring 續用。
- **seccomp 的致命盲點**：io_uring 操作不經 syscall 入口，擋 `openat` 擋不住 `IORING_OP_OPENAT`；可靠做法是從源頭擋 `io_uring_setup`。這是 io_uring 安全史的核心一課。
- LSM 三個 hook：`security_uring_allowed`（建 ring）、`security_uring_sqpoll`（SQPOLL）、`security_uring_override_creds`（personality 覆寫）。
- 取捨的本質：停用成本不是零，但對「根本不用 io_uring」的系統趨近於零——第一步是誠實回答「我真的在用它嗎」。
- 實測（WSL2 6.18）：`disabled=0` 下 setup 成功；seccomp filter 裝上後 setup 回 `-EPERM`（非特權、靠 `NO_NEW_PRIVS`）；probe 顯示 `last_op=62`。

## 自我檢核

- [ ] 我能默寫 `io_uring_disabled` 三個值的精確語意，包含 `disabled=1` 的「CAP_SYS_ADMIN 或 io_uring_group」。
- [ ] 我能解釋為什麼 seccomp 擋 `openat` 擋不住 io_uring 的開檔，以及正確的 seccomp 姿勢是什麼。
- [ ] 我能說出 io_uring 的三個 LSM hook 各自攔截什麼敏感能力。
- [ ] 我能在自有環境重現：讀 `io_uring_disabled`、用 seccomp 擋掉 setup、用 probe 看支援的 opcode。
- [ ] 給一個具體情境（桌機 / DB 伺服器 / 容器 / SELinux 環境），我能說出該用哪道防線、為什麼。

## 延伸閱讀

### 一手原始碼（最終仲裁）

- **[Linux 6.18 `io_uring/io_uring.c` 的 `io_uring_allowed`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c)**
  - **讀哪裡**：`io_uring_allowed()` 全函式、`kernel_io_uring_disabled_table`、`security_uring_override_creds` 呼叫點
  - **和本章關聯**：防線一、三的語意以此為準，勝過任何二手說明

### io_uring_disabled 的來龍去脈

- **[io_uring: add a sysctl to disable io_uring system-wide（patch 討論）](https://lkml.iu.edu/hypermail/linux/kernel/2308.2/00994.html)**
  - **讀哪裡**：patch 說明與討論；確認三級語意、`io_uring_group` 的設計意圖與 6.6 時程背景
- **[Google 限制 io_uring 的報導（Phoronix）](https://www.phoronix.com/news/Google-Restricting-IO_uring)**
  - **讀哪裡**：ChromeOS/Android/伺服器的分產品決策，對應本章決策表

### seccomp 繞過的歷史（防禦者必讀）

- **[LWN：io_uring、seccomp 與沙箱](https://lwn.net/Articles/1054225/)** 及相關 io-uring mailing list 討論
  - **讀哪裡**：Kees Cook、Andy Lutomirski、Jann Horn 對「io_uring 繞過 seccomp」的討論；理解「擋入口而非擋操作」為何是唯一可靠姿勢
  - **注意**：也看社群對 io_uring BPF per-opcode 過濾的進展，但記住截至 6.18 它仍新、部分 PoC

### man page / 設定參考

- **[io_uring_setup(2)](https://man7.org/linux/man-pages/man2/io_uring_setup.2.html)** 與 kernel 文件 `Documentation/admin-guide/sysctl/kernel.rst` 的 `io_uring_disabled` 條目
  - **讀哪裡**：`io_uring_disabled` / `io_uring_group` 的官方描述，設定前的最快參考

### 橫向

- **本 repo [kernel_internals Ch 49 seccomp 與 namespaces](../kernel_internals/49-seccomp-namespaces.md)**
  - **讀哪裡**：seccomp-bpf 的機制細節；本章的 filter 實作在那裡有更完整的背景

---

這一章我們把「堵」的工具全部攤開並實測了邊界。但堵只能擋住已知的、能被粗粒度開關覆蓋的風險；要找出下一個該修、該堵的洞，需要主動的審計與 fuzzing。下一章是 Part 4 的收口——防禦者視角：怎麼審計 io_uring 程式碼找 bug、syzkaller 怎麼 fuzz 它、怎麼讀 patch 學新的 bug 類型，把前九章的辨識能力收束成一套可持續的防禦工作流。

→ [Ch 40 防禦者視角：審計與 syzkaller fuzzing](./40-defender-view.md)
