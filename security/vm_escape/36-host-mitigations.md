# Ch 36 — host 端 mitigation：QEMU seccomp、sVirt、namespace

> **目標**：搞清楚逃逸後你面對的是什麼——不是空曠的 host，而是五層防禦依次攔你，每一層都有明確的擋截點與繞過代價。

## 為什麼需要這個？

Ch 16–24 我們把逃逸寫得很爽：MMIO OOB → infoleak → function pointer 劫持 → ROP → `execve("/bin/sh", ...)` → host shell。但那個環境是拆掉防禦後跑的。

真實生產環境完全不同。2015 年 VENOM（CVE-2015-3456）公開時，Red Hat 工程師的第一反應是：「sVirt 在那裡，就算 FDC bug 被打爆，攻擊者出不了 sVirt boundary 也無法跨 VM。」不是說 sVirt 擋住了逃逸本身——而是說在真實部署中，**拿到 QEMU RIP 只是第一關，後面還有好幾道門**。

忽略這些層的人，在 CTF 裡寫出能打通 local 測試機的 exploit，搬到真實目標上卻什麼都做不了。理解每一層擋什麼、不擋什麼，才能估計「這個洞在生產環境的實際 impact」，也才能知道 full chain 還缺哪些環。

## 先建立直覺

防禦不是單點的，是分層的。每一層假設前面的層已經被打穿，各自做「最小必要擋截」。概念上和 defence-in-depth 的 Swiss cheese model 一樣：任一單層都有洞，但洞的位置不同，要全穿才能通。

QEMU 的防禦分層從外到內大致是：

```
┌────────────────────────────────────────────┐
│  host 作業系統                              │
│  ┌──────────────────────────────────────┐  │
│  │  sVirt / SELinux (MAC 層)            │  │
│  │  ┌────────────────────────────────┐  │  │
│  │  │  namespaces / cgroups          │  │  │
│  │  │  ┌──────────────────────────┐  │  │  │
│  │  │  │  DAC: qemu 使用者 (非 root) │  │  │
│  │  │  │  ┌────────────────────┐  │  │  │  │
│  │  │  │  │  QEMU seccomp      │  │  │  │  │
│  │  │  │  │  sandbox (syscall  │  │  │  │  │
│  │  │  │  │  whitelist)        │  │  │  │  │
│  │  │  │  │  ┌──────────────┐  │  │  │  │  │
│  │  │  │  │  │  QEMU 行程   │  │  │  │  │  │
│  │  │  │  │  │  (含 guest)  │  │  │  │  │  │
│  │  │  │  │  └──────────────┘  │  │  │  │  │
│  │  │  │  └────────────────────┘  │  │  │  │
│  │  │  └──────────────────────────┘  │  │  │
│  │  └────────────────────────────────┘  │  │
│  └──────────────────────────────────────┘  │
│  host ASLR / NX / CET                      │
└────────────────────────────────────────────┘
```

**逃逸本身讓你拿到 QEMU 行程的 code execution**，你在最內層。要真正控制 host，要穿過上面每一層。

## 底層機制：逐層剖析

### 層 1：host 二進位強化（ASLR / NX / CET）

這是任何 Linux 行程都有的通用保護，在拿到 QEMU RIP 之前就在擋你。

- **ASLR**：`/proc/sys/kernel/randomize_va_space=2`，PIE binary + ASLR，QEMU 的 `.text`、heap、stack 全部隨機。Ch 17 infoleak 處理的就是這個。
- **NX（W^X）**：heap/stack 不可執行，強迫你走 ROP。Ch 22 ROP 處理的就是這個。
- **CET（Control-flow Enforcement Technology）**：現代 CPU + 新版 gcc/clang 的控制流強化——Shadow Stack（影子堆疊，SHSTK）防止 RET 被劫持，Indirect Branch Tracking（間接分支追蹤，IBT）要求間接呼叫只能跳到合法 `endbr64` 目標。

#### CET 技術細節

IBT 的運作方式：CPU 有一個「TRACKER」狀態機，`CALL`/`JMP` 之後進入 WAIT_ENDBRANCH 狀態，若下一條指令不是 `endbr64`（或 `endbr32`）就觸發 `#CP` 例外。這表示 ROP gadget 必須以 `endbr64` 開頭才能被間接呼叫使用。實際上，合法的函式進入點都有 `endbr64`，但純 ROP gadget（`.text` 中間段）沒有，所以 gadget 集合大幅縮水。

SHSTK 的運作方式：CPU 維護一個平行的 shadow stack，`CALL` 時自動把回傳位址同時寫進 shadow stack，`RET` 時比對一般 stack 上的回傳位址與 shadow stack 的值——不符就 `#CP`。Stack pivot + fake return address 之類的 ROP 手法直接被擋。

Linux kernel 從 6.6 版開始在 x86_64 正式支援 CET-SS（shadow stack）的使用者空間保護，以 `arch_prctl(ARCH_SHSTK_ENABLE, ...)` 啟用。QEMU 官方 build 目前不保證對所有 distro 啟用 CET；Fedora 39+ 的 QEMU 套件已試驗性地啟用 IBT。若 CET 全面啟用，ROP gadget 的選擇受到大幅限制，逃逸成本顯著提高。

**擋什麼**：讓你在取得任意寫原語之後還要額外 infoleak，讓 shellcode 不可用；CET 進一步限制 ROP gadget 選擇空間。
**不擋什麼**：只要洩漏成功，ASLR 失效；NX 擋不住 ROP；CET 在 IBT 模式下 gadget 仍存在於所有 `endbr64` 開頭的位置，只是更少。CET 提高代價但不是無法繞過。

### 層 2：QEMU seccomp sandbox

這是逃逸研究者最常遇到的第一道硬牆。QEMU 自 1.6 起支援 seccomp（Secure Computing Mode）過濾，以 libseccomp 建立白名單。

啟動方式（libvirt 預設會加）：
```
qemu-system-x86_64 -sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny ...
```

各參數意義（未實測，對照 QEMU 5.x+ 原始碼 `qemu-seccomp.c`）：
- `on`：啟用 seccomp BPF filter
- `obsolete=deny`：過時 syscall（如 `uselib`）一律拒絕
- `elevateprivileges=deny`：拒絕 `setuid`、`setgid`、`setcap` 等提權 syscall
- `spawn=deny`：拒絕 `execve`、`execveat`——這是逃逸後常見的第一個壁壘
- `resourcecontrol=deny`：拒絕 `setrlimit`、`ioprio_set` 等資源控制

#### BPF filter 執行流程

seccomp-BPF（Berkeley Packet Filter）的評估在 kernel 內、syscall 真正執行前進行。流程如下：

```
  使用者空間 QEMU 行程
       │
       │  syscall 指令（如 execve）
       ▼
  ┌─────────────────────────────┐
  │  kernel syscall entry point  │
  │  (syscall_64 / entry_SYSCALL_64) │
  └──────────┬──────────────────┘
             │
             ▼  seccomp hook 觸發（若已安裝 filter）
  ┌─────────────────────────────┐
  │  BPF bytecode evaluation     │
  │                              │
  │  輸入：seccomp_data 結構體    │
  │   .nr     = syscall number   │
  │   .arch   = AUDIT_ARCH_X86_64│
  │   .args[] = 前 6 個 syscall  │
  │             參數             │
  │                              │
  │  BPF 指令逐條執行             │
  │  ├─ 讀取 .nr                 │
  │  ├─ 比對白名單               │
  │  └─ 輸出 verdict             │
  └──────────┬──────────────────┘
             │
     ┌───────┴──────────┐
     │                  │
     ▼                  ▼
  SECCOMP_RET_ALLOW  SECCOMP_RET_KILL_PROCESS
  (繼續執行)          (整個行程 SIGKILL)
  
  其他可能的 verdict：
  SECCOMP_RET_ERRNO  → 回傳指定 errno，行程繼續
  SECCOMP_RET_TRACE  → 通知 tracer（ptrace）
  SECCOMP_RET_TRAP   → 送 SIGSYS 給行程
```

關鍵：BPF 評估是在 kernel 內完成的，QEMU 行程本身無法截取或繞過這個評估流程。你在 QEMU 行程裡取得的任意程式碼執行，其實已經在 seccomp 保護圈之內——你發出的每個 syscall 都要過這道關卡。

#### seccomp whitelist 的程式化方式

QEMU 用 libseccomp 建立 filter，簡化的 C 片段如下（對照原始碼，未逐字引用）：

```c
/* 初始化：預設動作是 KILL，只有白名單內的 syscall 才 ALLOW */
scmp_filter_ctx ctx = seccomp_init(SCMP_ACT_KILL);

/* 白名單：QEMU 正常運作所需 */
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(read),   0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(write),  0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(open),   0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(openat), 0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(close),  0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(mmap),   0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(mprotect), 0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(munmap), 0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(ioctl),  0);  /* /dev/kvm */
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(futex),  0);  /* 多執行緒 */
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(sendmsg), 0); /* 網路 backend */
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(recvmsg), 0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(epoll_wait), 0);
seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(eventfd2), 0);
/* ... 其餘白名單 ... */

/* 若 spawn=deny：execve 不在白名單 → 預設 KILL */
/* 若 elevateprivileges=deny：setuid/setgid 等同理 */

/* 載入 filter，從此生效 */
seccomp_load(ctx);
seccomp_release(ctx);
```

重點是 `seccomp_init(SCMP_ACT_KILL)` 這個呼叫——預設動作是 KILL，白名單是往裡面加 ALLOW 規則。這種「default-deny whitelist」的設計，讓攻擊者的選項只有「白名單裡有的 syscall」。

QEMU seccomp 允許的 syscall 集合包含：`open`、`read`、`write`、`mmap`、`munmap`、`mprotect`、`ioctl`（`/dev/kvm` 必要）、`futex`（多執行緒）、`sendmsg`/`recvmsg`（網路 backend）、`epoll_*`、`eventfd`、`signalfd` 等 QEMU 正常運作所需的系統呼叫。

完整清單在原始碼 `qemu-seccomp.c`（各版本略有不同）。「有哪些 syscall 被允許」直接決定了 Ch 37 的攻擊策略。

**擋什麼**：`execve` 被 `spawn=deny` 封住，`system("/bin/sh")` / `execve("/bin/sh", ...)` 直接 SIGKILL。
**不擋什麼**：file I/O、網路 I/O、記憶體操作仍可用——攻擊者可以不用 shell，用允許的 syscall 直接做 data exfiltration 或寫檔。

### 層 3：DAC（自主存取控制）

QEMU 行程跑在專用的非 root 使用者下（常見是 `qemu` 或 `kvm`），而非 root。

```bash
$ ps aux | grep qemu
qemu   12345  ... qemu-system-x86_64 ...
```

**擋什麼**：即使拿到 QEMU 行程的 code exec，也只有 `qemu` 使用者的 UID 權限。寫 `/etc/shadow`、`/etc/crontab`、`/root/` 等 root-only 路徑直接 EPERM。
**不擋什麼**：`qemu` 使用者自己能讀的所有東西——VM 磁碟映像（`qemu` 使用者必須能讀寫）、網路 tap 裝置等。

### 層 4：sVirt / SELinux（強制存取控制）

這是 Red Hat / Fedora / RHEL / CentOS 系最重要的一層，也是常被低估的一層。

**libvirt** 透過 sVirt（Security Virtualization）給每個 QEMU 行程打一個獨立的 SELinux（Security-Enhanced Linux）標籤：

```
system_u:system_r:svirt_t:s0:c123,c456
```

其中 `svirt_t` 是類型（type）、`s0:c123,c456` 是 MCS（Multi-Category Security，多類別安全）分類。每個 VM 的 category pair（`c123,c456`）是唯一的，libvirt 在啟動 QEMU 前隨機產生並寫進 SELinux policy。

#### libvirt 分配 MCS category 的流程

```
  libvirt（virtqemud / libvirtd）決定啟動新 VM
       │
       ▼
  ┌────────────────────────────────────────┐
  │  隨機產生唯一的 (cN, cM) pair           │
  │  （從 1024 個 category 中選兩個不重複）  │
  │  確保目前所有執行中 VM 都沒用這個 pair   │
  └──────────────┬─────────────────────────┘
                 │
                 ▼
  ┌────────────────────────────────────────┐
  │  呼叫 setfilecon() / semanage          │
  │  把 VM 磁碟映像標記為：                 │
  │    svirt_image_t:s0:cN,cM             │
  │  把 VM 設定檔等資源同步打標籤           │
  └──────────────┬─────────────────────────┘
                 │
                 ▼
  ┌────────────────────────────────────────┐
  │  fork() + execve() 啟動 QEMU 行程      │
  │  在 exec 前呼叫 setsockcreatecon() 或  │
  │  透過 libvirt security driver 呼叫      │
  │  selinux_setexeccon()，設定新行程的     │
  │  context 為：                           │
  │    system_u:system_r:svirt_t:s0:cN,cM │
  └──────────────┬─────────────────────────┘
                 │
                 ▼
  ┌────────────────────────────────────────┐
  │  QEMU 行程執行中                        │
  │  context = svirt_t:s0:cN,cM           │
  │  只能存取帶有相同 MCS pair 標籤的資源   │
  └────────────────────────────────────────┘
```

這意味著：

```
VM A 的 QEMU：svirt_t:c10,c20
VM B 的 QEMU：svirt_t:c30,c50
VM A 的磁碟映像：svirt_image_t:c10,c20
VM B 的磁碟映像：svirt_image_t:c30,c50
```

VM A 的 QEMU 行程即使拿到 code exec，也只能存取帶 `c10,c20` 標籤的資源——它讀不了 VM B 的磁碟映像，更不能直接存取 host 上其他行程的資源。

#### MCS 的 Bell-LaPadula 讀寫規則

MCS 的存取判斷邏輯源自 Bell-LaPadula（BLP）模型的「dominance」概念。SELinux 中，subject（主體，此處是 QEMU 行程）的 category set 必須「完全 dominate」object（客體，此處是磁碟映像）的 category set，才能存取：

```
dominate(S, O) = S.sensitivity >= O.sensitivity
                 AND S.categories ⊇ O.categories
```

具體例子：

```
QEMU 行程 context：svirt_t:s0:c10,c20
磁碟映像 A context：svirt_image_t:s0:c10,c20  → {c10,c20} ⊆ {c10,c20} ✓ 可存取
磁碟映像 B context：svirt_image_t:s0:c30,c50  → {c30,c50} ⊄ {c10,c20} ✗ EACCES
host 系統檔案：      system_u:object_r:etc_t:s0  → type 不符 svirt_t 的 allow 規則 ✗ EACCES
```

這裡的關鍵是「category pair 必須完全包含」——不是超集，是完全相同（因為 libvirt 在 object 上打的 category 與 QEMU 行程的 category 完全一致，所以 ⊇ 等價於 =）。攻擊者即使拿到 QEMU RIP，無法改變 kernel 維護的 process context，也就無法突破 MCS 邊界。

**擋什麼**：橫向移動（從逃逸的那個 QEMU 跑去動別的 VM）、存取 host 上沒有 sVirt 標籤的系統資源。
**不擋什麼**：sVirt 的邊界是 SELinux policy 定義的。若 policy 有洞、或 guest 的攻擊本身走的是 sVirt 允許的 channel（如網路），則無效。更重要的是：**它不擋 VM escape 本身**——escape 是在 QEMU 行程內部發生的，sVirt 是在 QEMU 行程與外部資源之間設界，兩件事不同層。

### 層 5：namespaces / cgroups

較新的部署（Kubernetes + KubeVirt、Kata Containers）會把 QEMU 行程再放進 Linux namespaces：

- **PID namespace**：QEMU 的 PID namespace 與 host init 樹分開，`kill(-1, SIGKILL)` 不會掃到 host 行程。
- **mount namespace**：filesystem view 受限，`/proc/`、`/sys/` 下很多 host 資訊看不到。
- **network namespace**：網路界面隔離。
- **cgroups**：限制 CPU / memory / io，即使能做任意操作也不能把 host 資源打光（阻絕服務難度提高）。

**擋什麼**：提供額外的資源隔離；即使 sVirt 失效，namespace 隔離也可能限制橫向移動。
**不擋什麼**：這一層在傳統 libvirt 部署中往往是空的或只有部分 namespace；cgroups 不阻止程式碼執行，只限制資源。

## 對比與取捨

| 層 | 機制 | 擋什麼 | 不擋什麼 | 部署要求 | Ubuntu/AppArmor 對應 |
|---|---|---|---|---|---|
| ASLR/NX/CET | 二進位強化 | shellcode、未洩漏時位址猜測 | 洩漏後 ROP | 預設啟用 | 同，kernel 層無差異 |
| seccomp | syscall 白名單 | execve/setuid 等 | file/net I/O | QEMU `-sandbox on` | 同，libseccomp 無關 distro |
| DAC | UID 隔離 | root-only 資源 | qemu 使用者可讀資源 | 配置正確的服務帳號 | 同 |
| sVirt/SELinux | MAC + MCS | 跨 VM 存取、host 資源 | VM escape 本身 | RHEL/Fedora 系預設 | AppArmor profile（path-based）|
| namespaces/cgroups | Linux namespace | 視野隔離、資源耗盡 | 程式碼執行本身 | 需主動配置 | 同 |

### AppArmor vs SELinux：兩種 MAC 的差異

Ubuntu 系預設用 AppArmor（Application Armor）而非 SELinux，libvirt 有對應的 AppArmor profile（`usr.sbin.libvirtd`）和 QEMU 的 abstraction（`/etc/apparmor.d/libvirt/TEMPLATE`），限制 QEMU 可存取的路徑與裝置。

兩者的核心差異在於「存取決策的基準」：

**SELinux（label-based）**：
- 每個檔案、行程、socket 都有 security label（context）
- 存取決策基於 label 對 label 的 policy 規則
- label 是 kernel 強制維護的，使用者無法在 userspace 偽造
- MCS 讓同 type 的行程（都是 `svirt_t`）仍能透過 category 互相隔離
- policy 複雜，寫錯 policy 很容易漏出洞，但正確的 policy 攻擊面極小

**AppArmor（path-based）**：
- 存取決策基於「這個行程可以存取哪些路徑（path）」
- 設定比 SELinux 直觀，profile 是人類可讀的 path pattern
- 缺點：path-based 意味著 hard link、symlink race 等技巧可能繞過——若攻擊者能在允許的路徑上建 symlink 指向敏感目標，某些 AppArmor profile 可能被繞過
- 沒有 MCS 概念，跨 VM 隔離需要靠 profile 的路徑規則精確性

從逃逸後的角度看：
- SELinux + MCS 的跨 VM 隔離是 kernel label 層面的強隔離，攻擊者在 userspace 幾乎無法繞過（除非有 kernel 本身的 SELinux bypass）
- AppArmor 的隔離相對依賴 profile 的完整性，若 profile 允許存取某個路徑而那個路徑恰好可被攻擊者控制，就有機可乘

不是說 AppArmor 弱，而是兩種 MAC 的繞過思路不同。

## 踩雷集錦

**「拿到 QEMU RIP 就等於 host shell」**
→ 在任何有 seccomp 的環境都不成立。`execve` 被 `spawn=deny` 封住，你的 ROP chain 最後那個 `execve("/bin/sh", ...)` 會被 SIGKILL。逃逸 exploit 的後半段是「在 seccomp 限制下取得有效的 impact」。驗證方式：在測試環境啟動 QEMU 時手動加 `-sandbox on,spawn=deny`，把現有的逃逸 exploit 打上去，看 QEMU 行程是否在 execve 點被 SIGKILL 而非跳出 shell。

**「CTF 題都沒有 sVirt，所以實際也沒差」**
→ CTF 為了讓題目可解，拆掉大部分 host 防禦。真實 RHEL/CentOS 雲端節點預設開 sVirt；你的 CTF exploit 在生產環境撞上 sVirt policy 就死。sVirt 不是讓逃逸變不可能，而是讓「逃逸後的影響範圍」縮小到那個 VM 的 sVirt boundary。驗證方式：在 Fedora 上用 libvirt 起一個 VM，逃逸後嘗試讀另一個 VM 的磁碟映像——你會拿到 EACCES，而不是預期的資料。

**「seccomp 只過濾 execve，其他都沒差」**
→ 視 QEMU 版本與 distro 包的設定，被封的可能不只 execve。`fork`、`clone`、`setuid`、`socket`（某些 backend 不需要）都可能被封。只用 execve 假設攻擊面，很容易低估 seccomp 的實際限制。驗證方式：用 `seccomp-tools dump` 傾印 QEMU 行程的 BPF filter，逐條確認哪些 syscall 被封，不要靠假設——靠原始碼或 dump 結果。

**「sVirt 擋住了 escape 本身」**
→ sVirt 完全不擋 escape。QEMU 行程內部（包含 device emulation bug 被觸發）發生的事，sVirt 無感知。sVirt 只在 QEMU 行程試圖存取「外部資源（檔案/其他行程/裝置）」時生效。驗證方式：在開 sVirt 的環境打一個 memory corruption 洞直到劫持 RIP——你仍然能劫持到，sVirt 沒有攔住。只是後續的「往外做事」才受限。

**「Ubuntu/Debian 沒有 sVirt 所以更不安全」**
→ Ubuntu 預設用 AppArmor 而非 SELinux，libvirt 有對應的 AppArmor profile（`usr.sbin.libvirtd`），限制 QEMU 可存取的路徑。不是無防護，只是換了 MAC framework。驗證方式：在 Ubuntu 上執行 `aa-status | grep qemu`，觀察 AppArmor 是否對 QEMU 行程有 enforce 模式的 profile 生效。

## 進階：再往深一層

**seccomp filter 的 NO_NEW_PRIVS 與不可移除性**

QEMU 在安裝 seccomp filter 之前，通常先呼叫：

```c
prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
```

這個 `prctl` 設定之後有兩個關鍵效果：

1. **filter 不可移除**：seccomp filter 一旦安裝，無法被移除或弱化。即使你在 QEMU 行程內取得任意程式碼執行，也不能呼叫任何 syscall 來解除 filter——因為解除 filter 本身就需要 syscall，而那個 syscall 也要過 filter。理論上能想到「修改 kernel 記憶體中的 filter 資料結構」，但那需要 kernel write primitive，代價遠超過逃逸本身。

2. **exec 後 filter 仍然有效（子行程繼承）**：`PR_SET_NO_NEW_PRIVS` + seccomp filter 會在 `execve` 後被子行程繼承。即使你繞過了 `spawn=deny`（這本身幾乎不可能，但假設 filter 設計有疏漏），新 exec 出來的行程仍然繼承相同的 seccomp filter。這意味著「在 seccomp 下 execve 之後的新行程仍受限」——你沒有辦法靠 exec 一個「乾淨」的 shell 來甩掉 filter。

3. **多層 filter 的堆疊**：seccomp filter 可以被多次 `seccomp_load`——每次安裝會堆疊一個新 filter，取所有 filter 中最嚴格的決策（最高優先是 KILL，其次是 ERRNO，最後才是 ALLOW）。QEMU 有時會分階段安裝 filter，但整體只會越來越嚴，不會越來越鬆。

**CET 的 IBT 與 SHSTK 技術細節補充**

IBT 對 ROP 的具體限制：傳統 ROP gadget 是從 `.text` 段中間的位元組序列截取出來的（例如 `pop rdi; ret` 可能出現在某個函式的中間）。IBT 強制間接 `CALL`/`JMP` 的目標必須是 `endbr64`，而 `endbr64` 只出現在合法函式進入點，不會出現在隨機位元組序列的中間。結果：
- 傳統 ROP gadget 幾乎全滅
- 但「以 `endbr64` 開頭的 ROP gadget」仍然存在——每個函式的進入點都是合法 IBT 目標，攻擊者只能從這些點選 gadget，限制了但沒有消滅 ROP

SHSTK 對 ROP 的具體限制：shadow stack 是 CPU 硬體維護的，在使用者空間無法直接寫入（`WRUSS` 指令需要 CPL 0 或特定 permission）。`RET` 指令會同時彈出一般 stack 的回傳位址和 shadow stack 的回傳位址並比對：
- 若兩者不符 → `#CP` 例外（Control Protection Exception）→ kernel 預設行為是 SIGSEGV
- 傳統 stack overflow 覆蓋回傳位址 → 被 SHSTK 擋住
- Stack pivot（把 RSP 換到攻擊者控制的記憶體）→ 若 pivot 目標的回傳值與 shadow stack 不符 → SHSTK 擋住

Linux 6.6+ x86_64 支援 CET-SS 使用者空間保護，透過 `arch_prctl(ARCH_SHSTK_ENABLE, ARCH_SHSTK_SHSTK)` 啟用。QEMU 8.x 開始考慮 CET 相容性，確保自身的 JIT（TCG）產生的程式碼在 IBT 環境下能正確帶 `endbr64`。

**seccomp BPF filter 的可逆性**：若你能取得 QEMU 行程的 memory write，理論上可以嘗試修改 seccomp filter 本身——但現代 kernel 有 `PR_SET_NO_NEW_PRIVS` 和 seccomp filter 的不可移除性保護，加上需要額外 root privilege 才能改 filter，這條路幾乎封死。

**SELinux policy 洞**：sVirt 的保護力完全取決於 SELinux policy 的嚴謹程度。若 policy 允許 `svirt_t` 存取某個共享資源（如 shared memory、Unix domain socket），攻擊者可以從那個 channel 跨出去。CVE-2015-8567（QEMU net/vmxnet3 OOB）搭配 sVirt 分析是理解 sVirt 邊界最好的案例之一。

**nested namespace escape**：Kata Containers 等把 QEMU 放進 OCI container namespace 的方案，等於在 sVirt 之外又加了一層 namespace。但若有 kernel namespace escape（如 `runc` 歷史 CVE），多層 namespace 可能被一個 kernel bug 直接穿透。這是「多個 escape 接在一起」的真實場景，Ch 40 會回頭看全景。

## 動手練習

1. **實際觀察 seccomp filter**：用 `seccomp-tools dump` 或 `strace -e seccomp` 跑一個帶 `-sandbox on` 的 QEMU，確認哪些 syscall 被允許、哪些被 kill。（未實測，需要 `seccomp-tools` + 有 `-sandbox on` 的 QEMU build）

2. **模擬 seccomp 擋截**：寫一個小程式，先呼叫 `prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ...)` 安裝一個只允許 `read`/`write`/`exit` 的 filter，然後嘗試 `execve`——確認收到 SIGKILL。理解 seccomp 的感知是 syscall 層，不是 C 函式層。

3. **查看 sVirt 標籤**：在 RHEL 或 Fedora 系機器上執行 `ps auxZ | grep qemu`，觀察 QEMU 行程的 SELinux 標籤，確認每個 VM 的 MCS category 不同。用 `ls -lZ` 看對應的磁碟映像，確認 MCS pair 吻合。

## 本章重點整理

- 逃逸後你在最內層，外面有五層不同機制在擋你
- QEMU seccomp `-sandbox on` 用 `spawn=deny` 封掉 `execve`，讓直接 shell 不可能
- seccomp BPF filter 在 kernel 的 syscall entry 點評估，QEMU 行程無法繞過評估流程本身
- `PR_SET_NO_NEW_PRIVS` 讓 seccomp filter 不可移除，exec 後子行程仍繼承相同 filter
- sVirt/SELinux 給每個 QEMU 行程獨立 MCS category，即使逃逸也只能碰自己 VM 的標記資源
- MCS 的 dominance 規則：subject category set 必須完全包含 object category set 才能存取
- DAC 確保 QEMU 跑在非 root 使用者，限縮可操作的 host 資源
- AppArmor（Ubuntu）是 path-based MAC，SELinux 是 label-based MAC，繞過思路不同
- namespaces/cgroups 在容器化部署中提供額外隔離
- 每層有明確的「擋什麼 / 不擋什麼」——沒有任何單層是萬能的

## 自我檢核

- [ ] 能說出 QEMU `-sandbox on` 帶的各個子參數分別封什麼
- [ ] 能畫出 seccomp BPF filter 的執行流程（syscall → hook → BPF evaluation → verdict）
- [ ] 能解釋 sVirt MCS category 為何能做 VM 間隔離，以及 dominance 規則的判斷邏輯
- [ ] 能說出「sVirt 不擋 escape 本身」的原因
- [ ] 能列出 QEMU seccomp 仍然允許的 syscall 類別（及其意義）
- [ ] 知道 AppArmor 是 Ubuntu 對 SELinux 的對應方案，且兩者的繞過思路不同
- [ ] 能解釋 `PR_SET_NO_NEW_PRIVS` 如何讓 seccomp filter 不可移除
- [ ] 能說出 CET IBT 對 ROP gadget 選擇的具體限制

## 延伸閱讀

1. **QEMU `qemu-seccomp.c` 原始碼**（`qemu.git/system/qemu-seccomp.c` 或舊版 `qemu-seccomp.c`）
   - 直接看 QEMU 目前允許哪些 syscall，以及各 `-sandbox` 旗標對應的 libseccomp 規則。學什麼：whitelist 的實際範圍。關聯 Ch 37 繞 seccomp。

2. **Red Hat sVirt 官方文件**（`access.redhat.com/documentation/…/virtualization_security_guide/`，搜尋 sVirt）
   - libvirt 如何產生 MCS category、如何把標籤打到 QEMU 行程和 VM 磁碟映像。學什麼：sVirt 的完整 lifecycle。

3. **Dan Walsh「sVirt：Hardening Linux Virtualization with Mandatory Access Control」**（Linux.conf.au 2009，可在 lwn.net 找到）
   - sVirt 設計者的原始介紹，說明設計動機與對 KVM/Xen 的應用。學什麼：威脅模型視角的 MAC 設計思路。

4. **libseccomp 文件**（`github.com/seccomp/libseccomp`）
   - QEMU seccomp 底層用的函式庫。學什麼：如何用 `seccomp_rule_add`/`seccomp_load` 建立 BPF filter，有助於理解 QEMU seccomp 的實作細節。

5. **「Exploiting and Bypassing Linux Seccomp」（syscall.party 或類似資源）**
   - 介紹 seccomp 繞過的標準手法（orw、利用允許的 syscall 做 pivot）。學什麼：Ch 37 的理論基礎。關聯 security/binary_exploitation 的 seccomp 逃逸章節。

6. **「SELinux Policy for Virtualization」（Fedora wiki / libvirt 官方 wiki，搜尋 SELinux Virtualization Policy）**
   - 說明 `svirt_t`、`svirt_image_t`、`svirt_content_t`、`svirt_save_image_t` 等 type 的完整定義，以及各 type 在 SELinux policy 中允許的動作（`allow` 規則）。說明 MCS pair 如何被 libvirt 自動管理（libvirt-security-manager 模組的實作）、VM 啟動/關閉/遷移時 label 的生命週期。學什麼：sVirt 的完整 policy 架構，用來判斷哪些 channel 在 policy 允許範圍內（即可能被攻擊者利用的 sVirt 邊界缺口）。

---

→ [Ch 37 — 逃逸後還被關著：繞過 QEMU seccomp sandbox](./37-bypass-seccomp.md)
