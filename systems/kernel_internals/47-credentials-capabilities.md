# Ch 47 — credentials 與 capabilities

> **目標**：搞懂 kernel 判斷「這個 process 能不能做這件事」時，到底看的是哪塊資料——`struct cred`。理解 cred 為什麼設計成不可變 + RCU 換上，這個設計如何直接對應到 `kernel_pwn` 課裡 `commit_creds(prepare_kernel_cred(NULL))` 這句經典提權；理解 capabilities 怎麼把 root 的全能拆成細粒度權限，以及一次檔案存取檢查（DAC）在 kernel 裡走過哪些函式。

> **本章環境**：延續 Ch 0 的 QEMU + gdb。這章的動手大量落在使用者空間（`/proc/<pid>/status`、`getpcaps`、`capsh`），最後用 gdb 停在 `commit_creds` 看提權的資料流。安全子系統（Part 9）從這章開頭，因為 cred 是後面 LSM（Ch 48）、namespace（Ch 49）、cgroup（Ch 50）都要疊在上面的地基。

## 為什麼需要這個？

kernel 每天要回答海量的「能不能」問題：這個 process 能不能開這個檔、能不能 `kill` 那個 process、能不能綁 80 埠、能不能載入模組、能不能改別人的 `nice` 值。這些判斷需要一個**依據**——某塊記錄「你是誰、你被授予了什麼權力」的資料。這塊資料就是 credentials（憑證），在 kernel 裡是 `struct cred`。

沒有集中憑證的話會怎樣？早期 UNIX 就是把 uid、gid、groups 這些欄位**散落**在 process 結構裡，每個要做權限判斷的地方各自去撈、各自比對。問題有二：

1. **一致性**：同一個 process 的多個執行緒共享權限，散落的欄位在改動時要逐一同步、逐一加鎖，容易出現「A 執行緒看到舊 uid、B 看到新 uid」的窗口。
2. **並行讀取的成本**：權限檢查是熱路徑（每次 `open`、每次 syscall 入口附近都可能檢查），如果每次讀 uid 都要拿鎖，代價很高。

Linux 2.6.29（2009）把所有安全相關欄位收攏進一個 `struct cred`，並讓 `task_struct` 只持有指向它的指標。這個重構的核心洞見是：**權限「讀多寫極少」**——一個 process 的 uid 在它的生命週期裡幾乎不變，但每個 syscall 都可能讀它。這正是 RCU（Ch 27）的黃金場景：讀者完全無鎖，寫者複製一份改完原子換上。這章要講的，就是這套「憑證集中化 + 不可變 + RCU 換上」的設計，以及它為什麼恰好是 `kernel_pwn` 提權的靶心。

## 先建立直覺

先在腦中建立這張圖：一個 process（`task_struct`）並不「內含」它的權限，而是**指向**一塊共享、唯讀的憑證。

```
   task_struct（Ch 9 的主角）
   ┌─────────────────────────────────┐
   │ ...                             │
   │ const struct cred __rcu *real_cred; ──┐  「客觀」憑證：別人看你是誰
   │ const struct cred __rcu *cred;    ────┼─┐「主觀」憑證：你動作時用哪個身分
   │ ...                             │   │ │  （多數時候兩者指同一塊）
   └─────────────────────────────────┘   │ │
                                          ▼ ▼
                          struct cred（include/linux/cred.h，唯讀、可共享、RCU 保護）
                          ┌──────────────────────────────────────────────┐
                          │ atomic_long_t usage;   ← refcount（第一個欄位）│
                          │ kuid_t uid, suid, euid, fsuid;  ← uid 家族      │
                          │ kgid_t gid, sgid, egid, fsgid;  ← gid 家族      │
                          │ kernel_cap_t cap_permitted;   ┐               │
                          │ kernel_cap_t cap_effective;   │ capability     │
                          │ kernel_cap_t cap_inheritable; │ 五個 set        │
                          │ kernel_cap_t cap_bset;        │               │
                          │ kernel_cap_t cap_ambient;     ┘               │
                          │ struct group_info *group_info; ← 補充群組       │
                          │ void *security;   ← LSM 安全 blob（接 Ch 48）   │
                          │ union { int non_rcu; struct rcu_head rcu; };   │
                          └──────────────────────────────────────────────┘
```

三個要記住的重點：

- **`task_struct` 有兩個 cred 指標**：`real_cred`（客觀 / objective）和 `cred`（主觀 / subjective，可覆寫）。絕大多數情況兩者指向同一塊 cred。差別只在少數特殊操作（後面「real vs effective 之外還有第三層」會講）。
- **cred 是唯讀的**：一旦某個 cred 生效（被 process 指向），就**不再原地修改**。要改權限，是「複製一份新的 → 改新的 → 原子地把指標換成新的」。
- **cred 可共享**：`fork` 出來的子 process 一開始和父 process 共享同一塊 cred（refcount +1），沒必要複製。這是唯讀帶來的直接好處——共享不用怕別人改壞。

這個「唯讀 + 換指標」的模式如果眼熟，那是因為它就是 RCU。讀者拿到 `cred` 指標後直接讀，不加鎖也不怕它變——因為它不會被原地改，最多是指標被換走，而舊的那塊會等到沒人讀了才釋放。

## struct cred：一個 process 的安全身分證

原始碼在 `include/linux/cred.h` 的 `struct cred`。挑關鍵欄位講設計，不逐欄位背。

### uid / gid 家族：為什麼一個身分要四個 uid

你在 `linux_commands` 學過 `id` 指令會印出 uid、euid，也知道 `passwd` 這種 setuid 程式能短暫變成 root。kernel 裡一個 process 的使用者身分不是一個數字，而是**四個**：

| 欄位 | 名稱 | 用途 |
|---|---|---|
| `uid` | real uid | 你「真正是誰」——啟動這個 process 的使用者 |
| `euid` | effective uid | 你「現在以誰的身分動作」——權限檢查主要看這個 |
| `suid` | saved uid | 暫存的 uid，讓程式能在 euid 之間來回切換 |
| `fsuid` | filesystem uid | 專門用於**檔案系統存取**檢查的 uid（歷史遺留，多數時候等於 euid） |

（gid 家族 `gid`/`egid`/`sgid`/`fsgid` 對稱，加上 `group_info` 存補充群組。）

為什麼要拆這麼多？核心是 setuid 機制的需求：一個以 root 身分執行的服務，想暫時「降權」去做某個使用者的操作，做完再升回來。有了 `euid`（現在用誰）和 `suid`（存著原本是誰），它可以 `euid=user` 做事、需要時再從 `suid` 恢復 `euid=root`。`fsuid` 則是一個更細的歷史產物：NFS 伺服器曾需要「以某使用者身分存取檔案，但不改變其他權限檢查」，於是把檔案存取專用的 id 獨立出來。今天你幾乎不用手動碰 `fsuid`，但它還在結構裡。

型別是 `kuid_t` / `kgid_t` 而不是裸 `uid_t`。這是 user namespace（Ch 49）帶來的：`kuid_t` 是「kernel 內部視角的絕對 uid」，而 process 在自己 namespace 裡看到的 uid 可能不同。namespace 內的「root」（uid 0）對映到 kernel 眼中可能是某個非零的 `kuid_t`——這是容器裡「假 root」的底層，Ch 49 展開。

### capabilities 五個 set

`cred` 裡有五個 `kernel_cap_t`，這是把 root 全能拆細後的產物，下一節專門講。

### security：LSM 的掛勾點

`void *security` 是留給 LSM（Linux Security Module，Ch 48）掛自己資料的指標。SELinux 在這裡掛 security context（例如 `unconfined_u:unconfined_r:unconfined_t`），AppArmor 掛 profile。DAC（uid/gid 那套）和 MAC（SELinux 那套）在 cred 這一層並存：cred 同時帶著「你的 uid」和「你的 SELinux label」，檢查時兩套都要過。

### usage 與 rcu：refcount 與延遲釋放

第一個欄位 `atomic_long_t usage` 是引用計數。多個 task 共享同一塊 cred 時靠它算人頭；歸零才真正釋放。結尾的 `union { int non_rcu; struct rcu_head rcu; }` 是 RCU 釋放用的掛勾——舊 cred 被換下後，不立刻 `free`，而是透過 `rcu_head` 排進 RCU callback，等到「所有可能還在讀它的讀者都離開」（一個 RCU grace period 之後）才回收。這正是讀者能無鎖讀的代價由誰承擔：寫者延後釋放，換讀者零成本。

## 底層機制：cred 的不可變性與 RCU 換上

這是這章的靈魂。理解了它，你就同時理解了「為什麼權限讀取無鎖」和「為什麼提權就是換一塊 cred」。

改一個 process 的權限，標準三步（都在 `kernel/cred.c`）：

```
   ① prepare_creds()                    ② 改新的那份              ③ commit_creds(new)
   複製當前 cred → new                   new->uid = 0;            原子把 task->cred 指標
   （new 是私有的，還沒人看得到）          new->euid = 0; ...       換成 new，舊的排進 RCU 回收

   task->cred ──► [ 舊 cred ]           task->cred ──► [ 舊 cred ]      task->cred ──┐
                  usage=1                              usage=1                        │
                                        [ 新 cred ]     ◄── 只有你改得到  [ 舊 cred ]  │
                                        uid=0,euid=0                     (等 RCU     │
                                                                          grace       ▼
                                                                          period) [ 新 cred ]
                                                                                  uid=0,euid=0
                                                                                  ▲ 全世界現在看到這塊
```

- **`prepare_creds()`**：`struct cred *prepare_creds(void)`。以當前 process 的 cred 為模板複製一份新的、可寫的 cred。回傳的這塊還沒被任何 task 指向，所以你能安全地原地改它——沒有讀者。
- **中間**：直接改 `new->uid`、`new->cap_effective` 等等。此刻沒有並行讀者看得到這塊，改它不需要鎖。
- **`commit_creds(new)`**：`int commit_creds(struct cred *new)`。**原子地**把 `task->cred`（和通常 `task->real_cred`）指標換成 `new`，把舊 cred 排進 RCU 延遲釋放。換指標是單一指標寫入，對讀者而言要嘛看到舊的、要嘛看到新的，永遠不會看到「改到一半」的狀態。

配套還有 `abort_creds(new)`（改到一半反悔，丟棄還沒 commit 的 new）、`override_creds()` / `revert_creds()`（暫時借用另一塊 cred 做事再還回去，kernel 內部像 `overlayfs`、`nfsd` 常用）。

讀者端呢？權限檢查時用 `current_cred()`（在 RCU read-side critical section 內取 `current->cred`）或 `__task_cred(task)` 讀別的 task 的 cred。讀者**完全無鎖**，因為：cred 不會被原地改（唯讀），指標換上是原子的，舊塊有 RCU 保護不會在讀者手上被 free。這就是為什麼把 uid 這種每次 syscall 都可能讀的東西放進 RCU 保護的結構——熱路徑零同步成本。

### 這正是 kernel_pwn 提權的靶心

現在從防禦方看那句你在 `kernel_pwn` 背過的經典：

```c
commit_creds(prepare_kernel_cred(NULL));   // 舊寫法；v6.2 起 NULL 語意改由 &init_cred
```

`prepare_kernel_cred()` 的簽名是 `struct cred *prepare_kernel_cred(struct task_struct *daemon)`，它生成一塊「kernel 執行緒等級」的 cred——uid/euid 全 0、capability 全開。傳 `NULL` 時（現代 kernel）以 `init_cred` 為模板，效果就是「一塊 root + 全能的 cred」。接著 `commit_creds()` 把它裝到當前 process 上。**這兩步一走完，發起攻擊的那個 process 就是 root 了**——不需要改任何檔案、不需要 `setuid` binary，只要能在 kernel context 執行這兩個函式呼叫。

為什麼攻擊者這麼愛它？因為它把「提權」濃縮成兩個 kernel 內既有函式的呼叫，符號都在 `kallsyms` 裡查得到位址。exploit 的典型流程是：透過某個漏洞（UAF、OOB write，見你打過的 slub 攻擊）取得任意執行或任意寫的原語，然後：

1. **函式呼叫路線**：控制執行流跳到 `commit_creds(prepare_kernel_cred(NULL))`（或用 ROP 串起這兩個呼叫）。
2. **資料覆寫路線**：更隱蔽——直接找到當前 `task_struct->cred` 指向的那塊 cred，把裡面的 `uid`/`euid`/`cap_effective` 用任意寫覆寫成 0 / 全 1。這條路連函式都不用呼叫，純資料改寫。

第二條路正好戳破一個常見誤解：「cred 唯讀」是**軟體約定**，不是硬體強制。kernel 自己遵守「不原地改 cred」的紀律（都走 prepare/commit），但一個有任意寫原語的攻擊者不受這個約定約束，它可以直接寫那塊記憶體。防禦手段（如把 cred 放進受保護區、CFI 阻止亂跳 `commit_creds`、`kCTF` 環境的各種 hardening）都是在補這個「約定不等於強制」的縫。這也是為什麼現代 exploit 越來越走資料導向（改 cred 資料）而非控制流導向——後者被 CFI/CET 擋，前者難防。

> 橫向連結：你在 `kernel_pwn` 學的是怎麼**製造**任意寫、怎麼**定位** cred。這章給你另一半——為什麼定位到 cred 就等於拿到 root，以及 kernel 這邊「唯讀 + RCU」的設計初衷（效能，不是防提權）恰好給了攻擊者一個集中、穩定的靶。

## capabilities：把 root 的全能拆成細粒度

傳統 UNIX 的權限模型太二元：你要嘛是 root（euid 0，什麼都能做），要嘛不是（什麼特權都沒有）。問題是很多程式只需要**一項**特權。`ping` 要送 raw ICMP 封包、綁 raw socket，傳統上得 setuid root——於是 `ping` 一旦有漏洞，攻擊者拿到的是**整個 root**，不只是「送封包」這一項權力。這違反最小權限原則。

capabilities（`include/uapi/linux/capability.h`）把 root 的全能拆成約 40 項獨立權限。挑幾個你會反覆遇到的：

| Capability | 值 | 授予什麼 |
|---|---|---|
| `CAP_CHOWN` | 0 | 改檔案 owner |
| `CAP_DAC_OVERRIDE` | 1 | **繞過**檔案 rwx 權限位元檢查（root 為什麼能讀任何檔就靠它） |
| `CAP_SETUID` | 7 | 任意設定 uid（`setuid` 家族不受限） |
| `CAP_NET_BIND_SERVICE` | 10 | 綁 <1024 的特權埠（`ping`/web server 只要這個，不用整個 root） |
| `CAP_NET_ADMIN` | 12 | 網路設定：介面、路由、防火牆、`tc` |
| `CAP_SYS_PTRACE` | 19 | `ptrace` 任意 process（`gdb` attach 別人的關鍵，接 `observability_tools`） |
| `CAP_SYS_ADMIN` | 21 | 萬能鑰匙——mount、setns、太多雜項都塞這，實務上「有它幾乎等於 root」 |

v6.12 的最高 capability 是 `CAP_CHECKPOINT_RESTORE`（值 40），`CAP_LAST_CAP` 就是它。

`CAP_SYS_ADMIN` 是個反面教材：它被塞了太多不相干的特權（mount、`swapon`、`setns`、quota…），以至於授予它幾乎等於給 root。「拆細 root」的理想被它某種程度上破壞了——這是 kernel 社群公認的設計債，但相容性讓它拆不動。你設計服務授權時，看到需求說「要 `CAP_SYS_ADMIN`」要警覺：它往往意味著「其實需要 root 等級信任」。

### 五個 capability set

cred 裡那五個 `kernel_cap_t` 各司其職。這五個是 capability 模型最容易搞混的地方，用一句話定位每個：

- **permitted**：你**可以**啟用的 capability 上限（能力的「錢包」）。
- **effective**：你**當前實際生效**的 capability（`capable()` 檢查看的是這個）。effective 必須是 permitted 的子集。
- **inheritable**：`exec` 時能**傳給**新程式的 capability（配合檔案的 inheritable set）。
- **bounding set（bset）**：一個上限遮罩，`exec` 後任何 capability 都不可能超出它。用來永久剝奪某些能力（丟掉就再也拿不回）。
- **ambient**：6.0 前後成熟的機制，讓非 setuid、非 file-capability 的普通程式也能**跨 exec 保留**某些 capability（補 inheritable 在無檔案 capability 時傳不過去的洞）。

初學只要牢記一條：**檢查時看 effective，effective 不能超過 permitted，exec 後不能超過 bounding**。其餘是 `exec` 時的傳遞規則。

### capable() 的檢查路徑

kernel 裡問「當前 process 有沒有某項 capability」用 `capable(int cap)`。它的呼叫鏈（跨 `kernel/capability.c` 和 `security/commoncap.c`）：

```
   capable(CAP_NET_ADMIN)                         kernel/capability.c
     └─► ns_capable(&init_user_ns, cap)           在 init user namespace 檢查
           └─► ns_capable_common(ns, cap, opts)
                 └─► security_capable(cred, ns, cap, opts)   ── LSM hook 點（Ch 48）
                       └─► （所有 LSM 的 capable hook 串起來）
                             └─► cap_capable(cred, ns, cap, opts)   security/commoncap.c
                                   檢查 cred->cap_effective 有沒有這個 bit
   成功 → 設 current->flags |= PF_SUPERPRIV（審計用：記錄用過特權）
```

兩個要點：

1. **`ns_capable` 帶 user namespace 參數**。`capable()` 只是 `ns_capable(&init_user_ns, cap)` 的簡寫——問「在最初的（真實的）user namespace 裡有沒有這個 cap」。容器裡的程式可能在自己的 user namespace 裡有 `CAP_NET_ADMIN`（管自己的網路 namespace），但在 `init_user_ns` 裡沒有（動不了 host 的網路）。這是容器能安全地給「namespace 內 root」的底層機制，Ch 49 展開。
2. **`security_capable` 是 LSM hook 點**。DAC 的 `cap_capable`（查 bit）只是其中一個 hook；SELinux/AppArmor 也在這裡掛自己的判斷。所以一次 capability 檢查實際上是「DAC 的 cap bit 檢查 **且** MAC 的 policy 檢查都要過」——這是 DAC 與 MAC 疊加的具體位置，Ch 48 詳談 LSM 怎麼串這些 hook。

## setuid 機制與 file capabilities

回到使用者空間視角（你在 `linux_commands` 的權限章看過的那些現象，這裡對到 kernel）。

### setuid bit：exec 時提權

`passwd` 需要改 `/etc/shadow`（只有 root 能寫），但普通使用者要能改自己的密碼。解法是 `passwd` binary 帶 **setuid bit**（`chmod u+s`，`ls -l` 顯示 `-rwsr-xr-x`），且 owner 是 root。當你 `exec` 一個 setuid root 的程式時，kernel 在 exec 路徑裡（`bprm_creds_from_file` 相關流程，最終落到 `commoncap.c` 的 `cap_bprm_creds_from_file`）依檔案的 setuid bit，把新程式的 **euid 設成檔案 owner（root）**，而 real uid 仍是你。於是 `passwd` 跑起來 euid=0，能寫 shadow；但它知道 real uid 是誰，只讓你改自己的密碼。

這就是「exec 時依檔案屬性設 euid」的機制。它強大也危險——任何 setuid root 程式的漏洞都可能變成 full root，所以 setuid binary 是攻擊面的重點（`oscp` 的本機提權經常從 `find / -perm -4000` 列 setuid 程式開始找可利用的）。

> 橫向連結（`oscp`）：本機提權的第一步常是枚舉 setuid binary 和當前 process 的 capability。`GTFOBins` 收錄的就是「哪些常見程式一旦是 setuid 或有某 capability，就能被濫用成 root shell」。這章讓你從 kernel 側理解**為什麼**這些程式一旦 setuid 就這麼危險——因為 exec 直接給了它們 euid=0。

### file capabilities：比 setuid root 更精準

setuid root 給整個 root，過猛。**file capabilities** 是更細的替代：給一個 binary **特定幾項 capability**，而非整個 root。存在檔案的擴充屬性（xattr）`security.capability` 裡。

例如給 `ping` 只加 `CAP_NET_RAW`（送 raw socket 需要的那項）：

```bash
sudo setcap cap_net_raw+ep /usr/bin/ping
getcap /usr/bin/ping          # /usr/bin/ping cap_net_raw=ep
```

現在 `ping` 不是 setuid root 了（`ls -l` 沒有 `s`），一旦有漏洞，攻擊者最多拿到「送 raw 封包」這一項，而不是整個 root。`+ep` 的 `e` 是把它放進 effective、`p` 是放進 permitted。這是最小權限原則的實踐，現代發行版的 `ping` 已經改用這種方式而非 setuid root。

## 檔案存取檢查：DAC 在 kernel 怎麼跑

你 `open("/etc/passwd", O_RDONLY)` 時，kernel 在哪裡、怎麼判斷你有沒有權限讀？這是 DAC（Discretionary Access Control，自主存取控制——「檔案 owner 自己決定」的 rwx 位元那套）的檢查，落在 `fs/namei.c`：

```
   open() → ... → inode_permission(idmap, inode, mask)       fs/namei.c
                    ├─ 先做 sb/inode 層級的粗檢（唯讀檔案系統上要寫就直接擋）
                    └─► generic_permission(idmap, inode, mask)
                          ├─ acl_permission_check()          比對 uid/gid + rwx 位元 / POSIX ACL
                          │    你是 owner？看 owner 的 rwx。是 group？看 group 的 rwx。都不是？看 other。
                          └─ 上面失敗 → capable_wrt_inode_uidgid(idmap, inode, CAP_DAC_OVERRIDE)
                               有 CAP_DAC_OVERRIDE？→ 繞過權限位元，放行
                               （這就是 root「無視權限」的真相——不是特殊 case，是它有這個 cap）
```

（v6.x 這些函式第一個參數是 `struct mnt_idmap *idmap`，這是 idmapped mount 帶來的——同一個檔案系統在不同 mount 點可以有不同的 uid 映射。細節超出本章，知道它存在即可。）

兩個重要認識：

1. **root 能讀任何檔不是「if uid==0 特判」**，而是 `generic_permission` 在權限位元檢查失敗後，退而問「有沒有 `CAP_DAC_OVERRIDE`」，root 有這個 cap 所以放行。把「無視權限」實作成一項 capability，而非硬編 uid 0 特判，正是 capability 拆細 root 的意義——你可以給一個非 root 服務 `CAP_DAC_OVERRIDE` 讓它讀任何檔，卻不給它其他 root 權力。
2. **DAC vs MAC**：這裡跑的是 DAC。SELinux（MAC，Ch 48）在**另一層**——即使 DAC 放行了，SELinux 的 policy 還可以擋（例如 `httpd` 的 SELinux type 不允許讀某個 label 的檔，就算 DAC 上 `apache` 使用者讀得到）。兩層都要過才放行。「DAC 決定你能不能，MAC 決定你被不被允許」——兩者是 AND 關係，不是二選一。這正是 `linux_commands` 裡你偶爾遇到「權限明明對了卻 Permission denied」的元兇：SELinux 在後面擋。

## 動手：看見 cred 與 capability

### 1. 從 /proc 看一個 process 的憑證

```bash
cat /proc/self/status | grep -E 'Uid|Gid|Groups|Cap'
```

你會看到類似：

```
Uid:    1000    1000    1000    1000     # real / effective / saved / fs
Gid:    1000    1000    1000    1000
Groups: 4 24 27 1000
CapInh: 0000000000000000               # inheritable set
CapPrm: 0000000000000000               # permitted set
CapEff: 0000000000000000               # effective set
CapBnd: 000001ffffffffff               # bounding set（普通使用者也有滿的 bounding）
CapAmb: 0000000000000000               # ambient set
```

`Uid` 那四個數字就是 cred 裡的 uid/euid/suid/fsuid。`Cap*` 五行就是五個 capability set 的 bitmask。普通使用者的 `CapEff` 是全 0（沒有任何生效的特權），`CapBnd` 卻滿的（bounding 是上限，不代表擁有）。對照一個 root shell 的 `/proc/self/status`，`CapEff` 會是 `000001ffffffffff`（所有 cap 都生效）——這一行變全 1，就是「這個 process 是全能 root」在 kernel 資料層面的樣子。攻擊者的任意寫要達成的，本質就是把 CapEff 對應的那塊記憶體改成全 1。

### 2. 用 capsh / getpcaps 解讀

bitmask 難讀，用工具翻成名字：

```bash
capsh --print                          # 印出當前 shell 的所有 capability set，帶名字
getpcaps $$                            # 印出 PID=$$（當前 shell）的 capability
getpcaps 1                             # 看 PID 1（init/systemd）的——通常滿滿
getcap -r /usr/bin 2>/dev/null         # 遞迴列出哪些 binary 有 file capability
```

`getpcaps 1` 對照普通使用者的 shell，直觀看到「systemd 有一堆 cap，我的 shell 一個都沒有」。

### 3. 寫一個用 capability 而非 root 的程式

示範綁特權埠（<1024）不用整個 root，只要 `CAP_NET_BIND_SERVICE`：

```c
// bind80.c —— 綁 80 埠。不 setuid root，改用 file capability
#include <stdio.h>
#include <string.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

int main(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(80) };
    if (bind(fd, (struct sockaddr *)&a, sizeof a) < 0) {
        perror("bind");                 // 沒有 CAP_NET_BIND_SERVICE 會在這裡 EACCES
        return 1;
    }
    printf("bound port 80, uid=%d\n", getuid());   // 注意 uid 仍是你，不是 0
    pause();
    return 0;
}
```

```bash
gcc -o bind80 bind80.c
./bind80                               # 失敗：bind: Permission denied（普通使用者綁不了 80）
sudo setcap cap_net_bind_service+ep ./bind80   # 只給這一項 capability
./bind80                               # 成功！而且 uid 還是你自己，不是 root
```

`bind()` 內部在 kernel 側對 <1024 的埠會呼叫 `ns_capable(net->user_ns, CAP_NET_BIND_SERVICE)`——就是前面那條 `capable` 路徑。你給了這一項 cap，檢查通過，卻沒給任何其他 root 權力。這就是「拆細 root」在生產環境的實際用法。

### 4. gdb 看 task->cred，並看見提權的資料流

在 QEMU + gdb（Ch 0 環境）裡，停在 `commit_creds` 看提權那一刻的資料：

```gdb
(gdb) break commit_creds
(gdb) continue
# 在 QEMU 裡跑 `sudo id`（或任何會換 cred 的動作）觸發
(gdb) print *new                       # 印出即將 commit 的新 cred
$1 = { usage = ..., uid = {val = 0}, euid = {val = 0},
       cap_effective = {...}, ... }     # uid/euid=0、cap 全開，就是 root cred 的樣子
(gdb) print current->cred               # 換之前，當前 task 指向的舊 cred
(gdb) finish                            # 跑完 commit_creds
(gdb) print current->cred               # 換之後，指標已指向 new
```

你會親眼看到 `current->cred` 這個指標從舊塊被換成 `new`。攻擊者的資料導向提權要做的，就是繞過 `commit_creds` 這個「正當管道」，直接把 `new` 那樣的內容寫進 `current->cred` 指向的記憶體，或直接把 `current->cred` 指標指向一塊自己準備好的 root cred。你在 gdb 裡看到的這個指標，就是 exploit 要定位的那個 offset。

> 定位技巧（呼應 `kernel_pwn`）：exploit 常先洩漏 `current`（`task_struct`）位址，再加上 `cred` 欄位的固定 offset（`task_struct` 裡 `real_cred`/`cred` 兩個相鄰指標）拿到 cred 位址，然後任意寫覆蓋 cred 裡的 uid/cap。`pahole task_struct` 或 gdb `print &((struct task_struct*)0)->cred` 就能算出這個 offset——防禦方（`STRUCT_LAYOUT` 隨機化、`__randomize_layout`，注意 cred 本身就標了 `__randomize_layout`）正是想讓這個 offset 不可預測。

## 對比與取捨

| 授權方式 | 授予範圍 | 風險 | 何時用 |
|---|---|---|---|
| setuid root binary | 整個 root（euid=0） | 一個漏洞 = full root，攻擊面最大 | 遺留程式；能改就改用 file capability |
| file capability | 特定幾項 cap | 漏洞只洩漏那幾項能力 | 現代做法（`ping`/`web server` 綁埠） |
| 給服務 `CAP_SYS_ADMIN` | 幾乎等於 root | 它塞了太多特權，形同 root | 盡量避免；看到就該問「真的需要 root 信任嗎」 |
| user namespace 內 root（Ch 49） | namespace 內全能、host 上無 | 容器逃逸漏洞才是威脅 | 容器（rootless container 的基礎） |
| ambient capability | 跨 exec 保留特定 cap | 需程式主動設定 | 無 file cap 又要傳 cap 給子程式時 |

一句取捨原則：**能用 file capability 就別用 setuid root，能用 namespace 內 root 就別給真 root**。每往細粒度走一步，漏洞的爆炸半徑就小一圈。

## 踩雷集錦

1. **「cred 唯讀所以攻擊者改不了」——錯**。唯讀是 kernel 自己遵守的軟體約定（都走 prepare/commit），不是硬體強制。有任意寫原語的攻擊者可以直接寫那塊記憶體，繞過整個約定。這正是資料導向提權（改 cred 資料）能成立的原因，也是它比控制流劫持更難防的原因。

2. **「euid=0 才是 root」——不完整**。決定「能不能繞過檔案權限」的是 `CAP_DAC_OVERRIDE`、「能不能任意 setuid」的是 `CAP_SETUID`。root 之所以全能是因為它擁有全部 capability，不是因為 uid 這個數字有魔力。一個 uid=0 但 capability 被剝光的 process（透過 bounding set 丟掉）並不全能。

3. **「有 permitted 就生效了」——錯**。`capable()` 檢查的是 **effective** set。permitted 只是「你能啟用的上限」，還沒放進 effective 就不生效。這是為什麼有些程式明明 `CapPrm` 有某項卻做不了對應操作——它沒把那項提到 `CapEff`。

4. **「`CAP_SYS_ADMIN` 是一項普通 capability」——低估它**。它被塞了太多不相關特權，實務上有它幾乎等於 root。設計授權時把它當「等同 root 信任」看待，別以為給了它就是最小權限。

5. **「DAC 過了就一定能存取」——漏了 MAC**。DAC（uid/gid + rwx）通過只是第一關，SELinux/AppArmor（MAC，Ch 48）在後面還能擋。`linux_commands` 裡「權限對了還是 Permission denied」十之八九是 SELinux。兩層是 AND 關係。

## 進階：再往深一層

- **real vs effective vs 第三層（override）**：`task_struct` 的 `real_cred`（客觀）和 `cred`（主觀）多數時候相同。差別出現在「代替別人做事」的場景——例如 `nfsd` 收到某使用者的請求，用 `override_creds()` 暫時把 `current->cred` 換成那個使用者的 cred 去存取檔案，做完 `revert_creds()` 換回來。這時「你動作用的身分」（subjective/`cred`）和「你真正是誰」（objective/`real_cred`）就分離了。`keyctl`、`overlayfs` 也用這招。面試常問「real_cred 和 cred 差在哪」，答案就是這個「客觀身分 vs 當前借用的動作身分」。

- **capability 的 exec 傳遞公式**：`exec` 後新程式的 cap 由「檔案的 cap set」「行程原有的 inheritable/ambient」「bounding set」按一條公式算出（`commoncap.c` 的 `cap_bprm_creds_from_file`）。核心是 `pP' = (X & fP) | (pI & fI) | pA`（新 permitted = bounding∩檔案permitted，加上 inheritable∩檔案inheritable，加上 ambient）。不用背，但知道「exec 後的 cap 不是憑空來的，是這幾個 set 按規則組出來的」，能解釋很多「為什麼我 exec 完 cap 不見了」的困惑。

- **面試常問**：「`commit_creds(prepare_kernel_cred(NULL))` 為什麼能提權，kernel 怎麼防」——答：前者換上 root cred，防禦包括 CFI/CET（阻止亂跳到 `commit_creds`）、把 cred 移到受保護記憶體、`kCTF` 的各種 hardening；但資料導向路線（直接改 cred 記憶體）繞過控制流防禦，是現在的主要威脅。這題把 Ch 47（cred 結構）、`kernel_pwn`（原語）、Ch 48（LSM 防禦）全串起來。

## 動手練習

1. **對照 root 與非 root 的 CapEff**：分別在普通 shell 和 `sudo -s` 開的 root shell 裡 `grep Cap /proc/self/status`，把兩邊的 `CapEff` 用 `capsh --decode=<那串 hex>` 翻成名字，寫下差異。確認「root = CapEff 全滿」。

2. **file capability 取代 setuid**：把上面的 `bind80.c` 編出來，先確認普通執行綁 80 失敗；`setcap cap_net_bind_service+ep` 後成功且 uid 不變。再 `setcap -r ./bind80`（移除）確認又失敗。體會「給一項 cap」和「給整個 root」的差別。

3. **gdb 抓提權那一刻**：在 QEMU 裡 `break commit_creds`，在 guest 跑 `sudo id`，`print *new` 看那塊 root cred 的 uid/cap。算出 `task_struct` 裡 `cred` 欄位的 offset（`print &((struct task_struct*)0)->cred`），對照 `kernel_pwn` exploit 裡用的 offset。

4. **弄壞 capability 傳遞**：用 `capsh --drop=cap_net_raw --` 開一個丟掉 `CAP_NET_RAW` 的 shell，在裡面跑需要 raw socket 的程式（如 `ping`，若它是 file-cap 版），看它怎麼失敗。理解 bounding set 剝奪的不可逆。

5. **看 DAC 繞過**：以 root `strace -e trace=openat cat /etc/shadow`，觀察 open 成功；再想：普通使用者做同樣操作在 `generic_permission` 的哪一步被擋、root 又在哪一步靠 `CAP_DAC_OVERRIDE` 放行。（進階：`bpftrace -e 'kprobe:cap_capable { printf("%d\n", arg2); }'` 觀測 capability 檢查——接 `bpf` 課的觀測視角。）

## 本章重點整理

- 一個 process 的所有安全身分集中在 `struct cred`（`include/linux/cred.h`）：uid/gid 四件套、五個 capability set、LSM security blob。`task_struct` 用 `real_cred`（客觀）和 `cred`（主觀）兩個指標指向它。
- cred **唯讀 + RCU 換上**：改權限走 `prepare_creds`（複製）→ 改 → `commit_creds`（原子換指標，舊塊 RCU 延遲釋放）。這讓權限讀取無鎖，是熱路徑的效能設計——也恰好是 `kernel_pwn` 提權的集中靶心（`commit_creds(prepare_kernel_cred(NULL))` 或直接覆寫 cred 記憶體）。
- capabilities 把 root 拆成約 40 項細粒度權限（v6.12 最高 `CAP_CHECKPOINT_RESTORE`=40）。`capable()` → `ns_capable()` → `security_capable()`（LSM hook）→ `cap_capable()` 查 effective set；帶 user namespace 參數是容器「假 root」的底層。
- DAC 檔案檢查在 `fs/namei.c` 的 `inode_permission`/`generic_permission`；root「無視權限」實作為 `CAP_DAC_OVERRIDE` 而非 uid 0 特判。DAC 過了 MAC（SELinux，Ch 48）還能擋，兩層 AND。

## 自我檢核

- [ ] 不看筆記，能畫出 `task_struct → cred` 的關係，並說出 cred 裡至少五類欄位（uid 家族、cap 五 set、group、security、usage/rcu）
- [ ] 能解釋為什麼 cred 設計成唯讀 + RCU（讀者無鎖），以及這個設計如何成為提權靶心
- [ ] 能默寫 prepare_creds → 改 → commit_creds 三步，並說明 `commit_creds` 為什麼是原子的、舊 cred 為什麼要 RCU 延遲釋放
- [ ] 面試被問「`commit_creds(prepare_kernel_cred(NULL))` 為什麼能提權」，能從 cred 結構 + 攻擊原語 + 防禦手段三個角度回答
- [ ] 能說出 effective / permitted / bounding 三個 capability set 的差別，並解釋為什麼 `capable()` 看 effective
- [ ] 能解釋 root 讀任何檔靠的是 `CAP_DAC_OVERRIDE` 而非 uid 0 特判，並指出這個 fallback 在 `generic_permission`
- [ ] 能用 file capability 取代一個 setuid root 的需求，並說明為什麼這樣爆炸半徑更小

## 延伸閱讀

### 官方文件

- **[Documentation/security/credentials.rst](https://www.kernel.org/doc/html/latest/security/credentials.html)** — David Howells（cred 機制的原作者）
  - **讀哪裡**：整篇。這是 cred 子系統設計者親自寫的權威說明，講清楚 objective vs subjective context、prepare/commit 生命週期、為什麼要 RCU
  - **和本章的關聯**：本章「不可變性與 RCU 換上」那節就是這篇的濃縮 + 提權視角，想看完整規則回這裡

- **[capabilities(7) man page](https://man7.org/linux/man-pages/man7/capabilities.7.html)** — Michael Kerrisk
  - **讀哪裡**：五個 capability set 的定義、exec 時的 cap 傳遞公式那節
  - **為什麼值得讀**：capability 模型最完整、最精確的單一參考；本章的傳遞公式 `pP' = (X & fP) | ...` 出自這裡，想搞懂 exec 後 cap 怎麼算必讀

### 攻防視角

- **[A Decade of Kernel Exploitation / cred 相關 write-up]（LWN + kernelCTF write-ups）**
  - **讀哪裡**：任一篇 kernelCTF 或 CVE 的 exploit write-up 裡「get root」那段
  - **能學到什麼**：`commit_creds(prepare_kernel_cred)` 和資料導向覆寫 cred 在真實 exploit 裡怎麼用；配合本章從防禦方理解的內容，兩面對照
  - **前提**：修過 `kernel_pwn`（slub/UAF 原語）

- **[GTFOBins](https://gtfobins.github.io/)** — 社群維護
  - **這是什麼**：收錄「哪些程式一旦 setuid 或帶某 capability 就能被濫用成 root」的清單
  - **為什麼值得用**：`oscp` 本機提權的必備工具；本章讓你理解**為什麼**這些 setuid/cap 程式危險（exec 給 euid=0、cap 直接生效），GTFOBins 給你**具體哪些**能被濫用

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 4 章「Process Management」與權限相關段落
  - **定位**：cred 之外的 process 生命週期背景（配 Ch 9、Ch 10），對 fork 時 cred 為什麼能共享（唯讀）給了直覺基礎
  - **注意**：講的 kernel 較舊，cred 集中化（2.6.29）之後的細節以本章 v6.12 源碼為準

理解了「一個 process 帶著什麼身分、kernel 怎麼用這身分做 DAC 判斷」之後，下一章我們往上疊一層——LSM（Linux Security Module）框架怎麼在 DAC 之上掛 MAC，SELinux 怎麼用 `cred->security` 那塊 blob 做 policy 判斷，以及本章反覆提到的 `security_*` hook 點在框架裡怎麼串起來。

→ [Ch 48 LSM 框架與 SELinux/AppArmor hook](./48-lsm-selinux.md)
