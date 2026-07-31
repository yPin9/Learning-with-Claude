# Ch 48 — LSM 框架與 SELinux/AppArmor hook

> **目標**：理解 Linux 怎麼在 DAC（自主存取控制）之上再疊一層由系統 policy 強制、連 root 都框得住的 MAC（強制存取控制）。讀懂 LSM（Linux Security Module）框架怎麼在 kernel 各處埋 hook、SELinux 的 type enforcement 與 AppArmor 的 path-based 模型差在哪、以及怎麼用 BPF LSM 自己寫一個安全 hook。

## 為什麼需要這個？

Ch 47 我們看完了 DAC——`struct cred` 裡的 uid/gid、檔案的 `rwxrwxrwx`、capabilities。DAC 有一個致命的哲學問題：**權限的決定權在資源擁有者手上**。你的檔案，你想 `chmod 777` 就 777；程式以 root 跑，root 對整台機器幾乎全能。這叫「自主」（Discretionary）——由主體自己決定。

問題出在「被攻破」的那一刻。想像一個以 root 跑的 web server（Ch 45 講的 socket 就是它綁的），被打進來拿到 shell。DAC 世界裡，攻擊者現在**就是 root**——他能讀 `/etc/shadow`、能改 `/etc/passwd`、能載惡意 kernel module（Ch 8）、能開任意 port。DAC 唯一的門檻「你是不是 owner / 有沒有 capability」他全部通過了，因為他偷到的正是那個身分。

MAC（Mandatory Access Control，強制存取控制）換一個思路：**存取規則由系統管理員寫成 policy，強制施加於所有人，包括 root，主體無權更改**。同一個被攻破的 web server，在 MAC 下即使變成 root，policy 仍然規定「`httpd` 這個 domain 只能讀 `/var/www`、只能綁 80/443 port、不准碰 `/etc/shadow`、不准載 module」。攻擊者拿到 root 也動不了——因為限制他的不是「他是誰」，而是「這支程式被 policy 框在哪個籠子裡」。

這正是 `oscp` 課裡你反覆撞牆的東西：拿到 shell 卻 `cat /etc/shadow` 被 `Permission denied`，而且你明明是 root。那多半就是 SELinux 在擋。這章我們從防禦方，看清楚那道牆是怎麼砌的。

> 一句話抓住 DAC vs MAC 的分工：**DAC 問「你是誰、你擁有它嗎」；MAC 問「這支程式、這個情境，policy 允許嗎」。兩道關卡串聯，任一擋下就拒絕。**

## 先建立直覺

先把「一次檔案 open 要過幾關」在腦中畫出來。這是理解整章的骨架：

```
   使用者程式 open("/etc/shadow", O_RDONLY)
        │
        ▼  syscall 進 kernel（Ch 4）
   ┌─────────────────────────────────────────────────────────┐
   │ VFS do_sys_open → do_filp_open → ... （Ch 33/34）          │
   │                                                           │
   │  關卡 1：DAC 檢查（Ch 47）                                  │
   │    inode_permission() → 比對 cred 的 uid/gid vs 檔案權限位  │
   │    root 或 owner 過關 ─────────────────┐                    │
   │    不過 → -EACCES（直接拒絕，不再往下）  │                    │
   │                                        ▼                   │
   │  關卡 2：LSM hook                                           │
   │    security_file_open(file)  ← kernel 埋的 hook 點          │
   │      │                                                     │
   │      ├─► SELinux：查 AVC / policy，                         │
   │      │     「httpd_t domain 能對 shadow_t 檔案做 read 嗎？」 │
   │      │       能 → 回 0；不能 → 回 -EACCES                     │
   │      │                                                     │
   │      └─► （若 stacking）AppArmor / BPF LSM 也各表態          │
   │            任一回非 0 就拒絕                                 │
   └─────────────────────────────────────────────────────────┘
        │
        ▼  兩關都過 → 回傳 fd；任一關擋下 → 回傳 -EACCES
```

三個關鍵直覺：

1. **LSM 在 DAC 之後**。DAC 先擋，過了才輪到 LSM。所以 MAC 只會「更嚴」不會「更鬆」——它不能授予 DAC 已經拒絕的存取，只能在 DAC 放行後再砍一刀。這是設計上的鐵律：LSM 是**限制器（restrictive）**，不是授權器。

2. **hook 是「決策點」，不是「決策者」**。kernel 只負責在敏感操作前呼叫 `security_*()`，至於答案是什麼，交給掛上去的安全模組（SELinux / AppArmor / …）。kernel 不寫死任何一種 policy——這是「框架」的精髓。

3. **hook 遍佈整個 kernel**。不只 open，還有 exec、mmap、綁 socket、ptrace、載 module、發訊號……每個安全敏感的路口都有一個 `security_*()`。SELinux 之所以「什麼都管得到」，是因為框架把 hook 埋到了每個路口。

## LSM 框架：為什麼要框架，而不是寫死一種

回到 2001 年前後。NSA 把 SELinux（Security-Enhanced Linux）以一組 patch 的形式丟出來，同時 Immunix 有 AppArmor 的前身、還有 Smack、LIDS 各自的方案。Linus 的態度很明確：**我不要在 mainline 裡選邊站，選一種 MAC 塞進 kernel**。他要的是一個中立的**掛載點框架**，讓各家安全模組都能插上來，社群自己去比高下。

於是有了 LSM（Linux Security Module）。它的設計哲學：

- kernel 在每個安全敏感操作前，呼叫一個 `security_xxx()` 函式（例如 `security_file_open()`、`security_bprm_check()`、`security_socket_bind()`）。
- 這些函式本身不做決策，只是**把呼叫轉發給註冊上來的安全模組**。
- 安全模組實作對應的 hook 函式，回傳 `0`（允許）或負的 errno（通常 `-EACCES` / `-EPERM`，拒絕）。
- kernel 收到非 0 就中止該操作、把錯誤往上回傳到 syscall。

源碼看兩個地方就懂框架：`security/security.c`（框架核心）與 `include/linux/lsm_hook_defs.h`（所有 hook 的定義清單）。

`security/security.c` 裡，每個 `security_*()` 大致長這樣（以 file_open 為例，實際欄位以 v6.12 為準）：

```c
// security/security.c，security_file_open() 概念形狀
int security_file_open(struct file *file)
{
    int ret;
    // call_int_hook：走訪所有註冊了 file_open hook 的 LSM，
    // 逐一呼叫；任何一個回非 0 就短路返回那個錯誤
    ret = call_int_hook(file_open, file);
    if (ret)
        return ret;
    return fsnotify_open_perm(file);   // 順帶做 fsnotify 的權限通知
}
```

`call_int_hook` 是框架的心臟。它走訪一串「掛在 file_open 這個 hook 上」的 callback，任何一個回傳非 0（拒絕）就立刻短路、返回那個錯誤碼；全部回 0 才算通過。「掛在某個 hook 上的 callback 串」以前叫 `security_hook_heads`（一堆 hlist），v6.x 演進成用靜態陣列 + static call 的實作以降低每次 hook 的開銷——但概念不變：**一個 hook 點，一串 LSM 的 callback，逐一問過去，一票否決。**

hook 有哪些？看 `include/linux/lsm_hook_defs.h`——這個檔用一堆 `LSM_HOOK(...)` 巨集把上百個 hook 一次列出來，是理解「LSM 到底能管哪些操作」最快的地圖。挑幾個感受一下涵蓋面：

| hook（`security_*`） | 埋在哪 | 管什麼 | 對應章節 |
|---|---|---|---|
| `security_file_open` | VFS open 路徑 | 開檔 | Ch 33/34 |
| `security_inode_permission` | 每次路徑解析 | 存取 inode | Ch 33 |
| `security_bprm_check` | `execve` 載入時 | 執行程式（換 domain 的關鍵） | Ch 10 |
| `security_mmap_file` | `mmap` | 映射檔案（W^X 政策） | Ch 19 |
| `security_socket_bind` / `_connect` | socket 層 | 綁 / 連 port | Ch 45 |
| `security_task_kill` | 送訊號 | 誰能 kill 誰 | Ch 9 |
| `security_ptrace_access_check` | ptrace attach | 誰能 debug 誰 | Ch 51 / gdb 課 |
| `security_kernel_read_file` | 載 module / firmware | 載入哪些檔案進 kernel | Ch 8 |

看到 `security_ptrace_access_check` 了嗎？這就是 Yama LSM 掛的點——`gdb`/`observability_tools` 課裡你偶爾遇到「attach 不上別的 process」，就是 Yama 這個 hook 在 `/proc/sys/kernel/yama/ptrace_scope` 的控制下擋掉的。

### stacking：現代 kernel 可以同時開多個 LSM

早年 LSM 是「二選一」：開機時 `security=selinux` 或 `security=apparmor`，只能有一個 major LSM。這限制很痛——你不能同時要 SELinux 的 type enforcement 和 Yama 的 ptrace 限制。

v5.1 起 kernel 支援 **LSM stacking**（堆疊）。現在的模型分兩類：

- **minor / capability-style LSM**（capabilities、Yama、Landlock、LoadPin、BPF LSM）：一直都能疊，彼此不衝突，每個各管一塊。
- **major LSM**（SELinux、AppArmor、Smack、TOMOYO）：因為都要在 `struct` 上掛自己的 label（用 LSM blob，見下），過去只能開一個；stacking 讓「有限度共存」成為可能，但完整的多 major-LSM 共存仍在演進。

開機參數 `lsm=` 決定順序，例如 `lsm=capability,yama,selinux,bpf`。順序有意義：`call_int_hook` 按這個順序逐一問，任一否決即拒。開機後可以直接讀 `/sys/kernel/security/lsm` 看這台機器到底疊了哪些：

```bash
cat /sys/kernel/security/lsm
# 例如：capability,landlock,lockdown,yama,integrity,apparmor
```

（Ubuntu 24.04 預設就是這串，末尾是 apparmor；換成 Fedora/RHEL 會看到 selinux。）

### LSM 怎麼在物件上存自己的資料：security blob

SELinux 要為每個 inode 記一個 security context、每個 task 記一個 domain。這些資料存哪？答案是 **LSM blob**：`struct inode`、`struct cred`、`struct file`、`struct task_struct` 等結構裡預留一個 `void *security`（或由框架統一配置的 blob 區），每個 LSM 在裡面切一塊自己用。這就是為什麼 major LSM 過去難共存——它們都想用那塊空間，framework 要協調誰佔多少 offset。理解這點，你就懂 stacking 為什麼是個硬工程問題，而不是加個 if 就好。

## SELinux：type enforcement 的世界

`security/selinux/` 是 SELinux 的實作，核心 hook 都在 `security/selinux/hooks.c`——這個檔把上面清單裡的每個 `security_*` 對應到 SELinux 自己的判斷函式（例如 `selinux_file_open()`、`selinux_bprm_creds_for_exec()`）。

SELinux 的核心模型是 **type enforcement（TE，型別強制）**：

- 每個 process 跑在一個 **domain**（本質是一個 type，慣例上 domain type 以 `_t` 結尾，如 `httpd_t`）。
- 每個物件（檔案、port、socket…）有一個 **type**（如 `shadow_t`、`http_port_t`）。
- policy 是一大堆 `allow` 規則：**「哪個 domain，能對哪個 type，做哪些操作」**。

```
   allow httpd_t   httpd_content_t : file  { read getattr open };
         ^^^^^^^   ^^^^^^^^^^^^^^^   ^^^^   ^^^^^^^^^^^^^^^^^^^^^
         主體domain 客體type         類別    允許的動作集

   意思：跑在 httpd_t 的 process，可以對 httpd_content_t 的 file
         做 read/getattr/open。沒寫 allow shadow_t 的規則，
         所以 httpd_t 讀 shadow_t（/etc/shadow）→ 預設拒絕。
```

關鍵是 **default deny**：policy 沒明文 `allow` 的，一律拒絕。這跟 DAC 的「default allow，靠權限位擋」相反。SELinux 的強大與難用都來自這裡——你得為每個合法操作寫一條 allow，漏一條程式就跑不動；但也正因如此，攻擊者想做的任何 policy 沒預期的事，全部撞牆。

### security context 與 label 的傳遞

每個主體/客體的完整身分叫 **security context**，格式是四段：

```
   system_u : object_r : shadow_t : s0
   ^^^^^^^^   ^^^^^^^^   ^^^^^^^   ^^
   SELinux   role        type      MLS/MCS level
   user      （角色）     （最關鍵） （多層級安全，一般機器多為 s0）
```

TE 只看 **type** 那一段（第三段）——這是 99% 情況下真正做決策的欄位。role 和 user 用於 RBAC/多使用者隔離，MLS level 用於軍規式的多層級保密（一般伺服器用不到，維持 s0）。

用 `ls -Z` 看檔案的 context、`ps -Z` 看 process 的 domain：

```bash
ls -Z /etc/shadow
# system_u:object_r:shadow_t:s0 /etc/shadow

ps -eZ | grep httpd
# system_u:system_r:httpd_t:s0 ... /usr/sbin/httpd
```

domain 怎麼決定？靠 exec。當 `init_t` 執行 `/usr/sbin/httpd`（它被標成 `httpd_exec_t`），policy 裡的 domain transition 規則讓新 process 從 `init_t` 切換成 `httpd_t`。這個切換的 hook 正是 `security_bprm_creds_for_exec()`（Ch 10 的 `execve` 路徑會呼叫）——SELinux 在這裡算出「exec 之後 process 該進哪個 domain」，並把它寫進新的 `cred`（接 Ch 47：exec 時 cred 會被重算）。

### AVC：為什麼查 policy 不會慢死

policy 動輒上萬條規則，每次 open 都線性掃一遍會慢到不能用。SELinux 用 **AVC（Access Vector Cache，存取向量快取）**：把「(source type, target type, class) → 允許的動作集」的判斷結果快取起來。第一次問某個 (httpd_t, shadow_t, file) 組合時查 policy DB，之後同組合直接命中快取。這是 SELinux 能在生產環境全時開啟卻幾乎不影響效能的關鍵。

被拒絕時，SELinux 會發一條 **AVC denied** 到 audit log。這是你排查 SELinux 問題的命脈：

```bash
# 看最近的拒絕記錄
ausearch -m AVC -ts recent
# 或直接翻 audit log
grep 'avc:.*denied' /var/log/audit/audit.log
# type=AVC msg=audit(...): avc:  denied  { read } for  pid=1234
#   comm="httpd" name="shadow" dev="sda1" ino=...
#   scontext=system_u:system_r:httpd_t:s0
#   tcontext=system_u:object_r:shadow_t:s0 tclass=file permissive=0
```

`scontext`（source，誰）、`tcontext`（target，動誰）、`tclass`（class）、`{ read }`（想做什麼）——一條 AVC denied 把「誰想對誰做什麼被擋了」講得清清楚楚。`oscp` 裡你被 SELinux 擋住時，第一件事就是去讀 audit log 這條記錄，它直接告訴你缺哪條 allow。

### enforcing vs permissive

SELinux 有三個全域模式：

- **enforcing**：違反 policy 就真的拒絕。生產環境該用這個。
- **permissive**：違反 policy **不拒絕，只記 AVC denied**。這是 debug policy 的黃金模式——讓程式跑，把所有本該被擋的操作都記下來，再一次補齊 allow 規則。
- **disabled**：完全關閉（v6.x 起 kernel 傾向廢棄「開機時 disabled」這條路，建議用 permissive 取代）。

```bash
getenforce            # Enforcing / Permissive / Disabled
sestatus              # 完整狀態：目前模式、policy 名稱、掛載點
setenforce 0          # 暫時切 permissive（重開機還原）
setenforce 1          # 切回 enforcing
```

> **踩雷預告**：新手遇到 SELinux 擋路，最常見的錯誤是 `setenforce 0` 一關了事——這等於把整台機器的 MAC 保護拆了。正確做法是切 permissive 收集 AVC、用 `audit2allow` 產生缺的規則、或用正確的工具（`semanage fcontext` / `restorecon`）修 label，而不是關掉它。

## AppArmor：path-based，好懂在哪

`security/apparmor/` 是 AppArmor 的實作。它跟 SELinux 解同一個問題（MAC），但模型完全不同：**AppArmor 用「路徑」而非「label」**。

SELinux 說「`httpd_t` 能讀 `shadow_t`」——你得先知道每個檔案被貼了什麼 type。AppArmor 直接說「`/usr/sbin/httpd` 能讀 `/var/www/**`、能寫 `/var/log/httpd/*`」——用檔案路徑講規則。一個 AppArmor **profile** 就是一支程式的能力清單：

```
   # /etc/apparmor.d/usr.sbin.httpd（概念示意）
   /usr/sbin/httpd {
     #include <abstractions/base>
     capability net_bind_service,      # 只准綁 <1024 的 port
     network tcp,
     /var/www/**            r,          # 讀網站內容
     /var/log/httpd/*.log   w,          # 寫自己的 log
     /etc/shadow            deny r,     # 明確不准讀 shadow
   }
```

好懂的代價是**沒有 label 那麼精準**。path-based 的軟肋：同一個 inode 可以有多條路徑（hard link、bind mount、symlink），path rule 得處理這些等價路徑，否則會有繞過空間；SELinux 貼在 inode 上的 label 天生沒這個問題（label 跟著 inode，不跟著路徑）。這是兩者最本質的取捨：**AppArmor 好寫好懂但路徑語意脆弱；SELinux 精準強固但學習曲線陡。**

Ubuntu、SUSE 預設 AppArmor；Fedora、RHEL、Android（用改造版 SELinux）預設 SELinux。看 AppArmor 狀態：

```bash
aa-status                    # 載入了哪些 profile、各在 enforce/complain 模式
aa-complain /path/to/prog    # 把某 profile 切 complain（等同 SELinux 的 permissive）
aa-enforce  /path/to/prog    # 切回 enforce
```

（AppArmor 的 **complain** 模式對應 SELinux 的 **permissive**——記錄但不拒絕，同樣是 debug profile 的模式。）

## 其他 LSM：一人管一塊

major LSM 之外，還有一票 minor LSM，各解一個小而明確的問題，且能跟 major LSM stacking 共存：

- **Smack**（Simplified Mandatory Access Control Kernel）：比 SELinux 簡化的 label-based MAC，常見於嵌入式 / 車用 / Tizen。你的 MTK 韌體世界裡若見到 MAC，Smack 的機率不低於 SELinux。
- **TOMOYO**：path-based、以「學習模式」自動生成 policy 著稱，日本社群主導。
- **Yama**：只做一件事——限制 ptrace（`security_ptrace_access_check`）。`/proc/sys/kernel/yama/ptrace_scope` 設 1 時，非父行程不能 attach 別的 process。這是為什麼你在現代 Ubuntu 上 `gdb -p <別人的 pid>` 常常要 sudo（接 `gdb`/`observability_tools` 課）。
- **Landlock**（v5.13 起）：**unprivileged 沙盒**——不需要 root，程式可以呼叫 syscall 自己把自己關進更小的籠子（限制能碰哪些檔案路徑、之後版本加了 network）。這跟 SELinux/AppArmor 由管理員從外部施加 policy 相反，是「程式自我設限」。想在應用層做沙盒（接 seccomp，Ch 49）而不想寫 SELinux policy 時，Landlock 是現代解法。
- **BPF LSM**（`security=bpf`，v5.7 起）：**用 eBPF 程式當 LSM hook**。你把一段 BPF 程式 attach 到某個 LSM hook 點（如 `file_open`），它就在每次該操作時跑、回 0 或 -EPERM。這是把「寫 LSM」從「改 kernel、重編、重開機」降維成「寫個 BPF、動態載入」的革命——`bpf` 課與 Ch 52 專門講它，這裡先讓你知道它掛在 LSM 框架上。

### IMA/EVM：完整性度量（點一下）

`security/integrity/` 下的 IMA（Integrity Measurement Architecture）與 EVM（Extended Verification Module）不做「允不允許存取」，而做「這個檔案有沒有被竄改」：

- **IMA**：在檔案被讀 / 執行時算 hash，記進一個度量清單（measurement list），可延伸進 TPM 的 PCR——這就是「measured boot」延伸到 runtime 的部分（接 `linux_boot` 的 secure boot / TPM 章）。也能配 policy 做 appraisal：hash / 簽章不符就拒絕開檔。
- **EVM**：保護檔案的擴充屬性（xattr，SELinux label 就存在 xattr 裡）不被離線竄改。

從 secure boot（韌體驗 bootloader）→ measured boot（驗 kernel）→ IMA（驗 runtime 檔案），是一條完整的信任鏈。你的 MTK 韌體 / RISC-V 平台若要談「開機到 runtime 的完整性」，IMA/EVM 是 kernel 這一段的答案。

## 底層機制：一次 exec 怎麼換 domain 又過 hook

把 SELinux 最精髓的一幕——`execve` 觸發 domain transition——串成一條路徑。這比 open 更能看出 LSM 的威力，因為它是「攻擊者最想控制、policy 最想框住」的點。

```
   shell（domain: unconfined_t 或 init_t）
        │  execve("/usr/sbin/httpd", ...)
        ▼
   fs/exec.c: do_execveat_common → bprm_execve
        │
        ├─► security_bprm_creds_for_exec(bprm)        ← LSM hook #1
        │     └─ selinux_bprm_creds_for_exec():
        │          查 policy 的 type_transition 規則：
        │          「init_t 執行 httpd_exec_t 的檔案 → 新 domain = httpd_t」
        │          把 httpd_t 寫進 bprm->cred（新 cred，接 Ch 47）
        │
        ├─► （載入 ELF、設定新位址空間，Ch 10/19）
        │
        ├─► security_bprm_check(bprm)                  ← LSM hook #2
        │     └─ 最後確認：這個 domain transition 本身被 allow 嗎？
        │          policy 需有 allow init_t httpd_t:process transition;
        │
        ▼
   commit_creds(bprm->cred)    ← 新 domain 正式生效（Ch 47）
        │
        ▼
   httpd 開始跑，此後它做的每個 open/bind/kill
   都以 httpd_t 這個 domain 去撞 policy
```

看懂這條路徑，你就懂了兩件事：

1. **domain transition 發生在 exec，不是 fork**。fork 出來的 child 繼承父的 domain（Ch 10 的 `copy_process` 複製 cred）；真正換 domain 是 exec 時，由 policy 的 type_transition 規則決定。這就是為什麼攻擊者「在 httpd 裡 fork 一個 shell」時，那個 shell 仍是 `httpd_t`——它被 httpd 的籠子綁著，即使它是 `/bin/bash`。這是 MAC 擋住「shell escape 提權」的機制核心。

2. **hook 分兩段**：`bprm_creds_for_exec`（算新 domain、填 cred）和 `bprm_check`（最後審核）。兩段之間 kernel 做 ELF 載入等重活。把「決定身分」和「最終放行」分開，是為了讓 LSM 能在正確的時機各做各的判斷。

## 動手：觀察與寫一個最小 BPF LSM

**A. 盤點這台機器的 MAC 狀態**

```bash
# 這台機器疊了哪些 LSM，順序如何
cat /sys/kernel/security/lsm

# SELinux 系（Fedora/RHEL/CentOS/Android）
getenforce && sestatus
ls -Z /etc/shadow /var/www
ps -eZ | head

# AppArmor 系（Ubuntu/SUSE）
sudo aa-status

# Yama ptrace 限制（跨發行版都可能有）
cat /proc/sys/kernel/yama/ptrace_scope
```

**B. 觸發並讀懂一條 AVC denied（SELinux 機器上）**

```bash
# 切 permissive 才不會真的擋、但仍記錄（安全地製造一條 denial）
sudo setenforce 0
# 用一個「本不該有權」的 domain 去碰敏感檔，然後：
sudo ausearch -m AVC -ts recent | tail -20
# 讀 scontext / tcontext / tclass / 動作，練習「一眼看出缺哪條 allow」
sudo setenforce 1     # 記得切回來
```

`audit2allow` 能把 AVC denied 直接翻成建議的 allow 規則（生產環境要審過再套，別無腦全套）：

```bash
sudo ausearch -m AVC -ts recent | audit2allow -m mymodule
```

**C. 寫一個最小 BPF LSM（接 bpf 課 / Ch 52）**

前提：kernel 開了 `CONFIG_BPF_LSM=y`，且 `bpf` 出現在 `/sys/kernel/security/lsm`（開機參數 `lsm=...,bpf`）。用 `SEC("lsm/...")` 把 BPF 程式掛到某個 hook。下面這支攔 `file_open`，讓任何人都開不了某個特定 inode：

```c
// mini_lsm.bpf.c —— 用 libbpf + CO-RE 編（細節見 bpf 課）
#include <vmlinux.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

char LICENSE[] SEC("license") = "GPL";   // BPF LSM 需要 GPL

// 掛在 LSM hook "file_open"，對應 kernel 的 security_file_open
SEC("lsm/file_open")
int BPF_PROG(block_open, struct file *file, int ret)
{
    // ret 是「前面的 LSM / 這條 chain 目前的結果」：
    // 若已被拒（ret != 0），尊重前者，別把拒絕改成放行
    if (ret != 0)
        return ret;

    // 示範：禁止開 inode 號 == 某個值的檔案（真實情境會比對更有意義的條件）
    unsigned long ino = BPF_CORE_READ(file, f_inode, i_ino);
    if (ino == 12345)
        return -EPERM;      // 非 0 = 拒絕，等同 SELinux 回 -EACCES

    return 0;               // 允許
}
```

兩個關鍵設計，和整章呼應：

- **回傳語意跟 C 寫的 LSM 完全一樣**：0 放行、負 errno 拒絕。BPF LSM 只是把「hook 的 callback」從 kernel C 換成動態載入的 BPF，框架的 `call_int_hook` 走訪、一票否決的邏輯不變。
- **`ret` 參數體現 stacking**：BPF LSM 拿得到 chain 目前的結果，慣例是「別把別人的拒絕翻成放行」（LSM 是限制器，只能更嚴）。這正是本章開頭第 1 點直覺的落地。

用 `bpftool prog load` / libbpf skeleton 載入後（流程見 `bpf` 課），它立刻對每個 `open` 生效——不需重編 kernel、不需重開機。這就是 BPF LSM 相對傳統 LSM 的殺手級優勢。

## 對比與取捨

| 面向 | SELinux | AppArmor | BPF LSM | Landlock |
|---|---|---|---|---|
| 模型 | type enforcement（label） | path-based（profile） | 任意（你寫 BPF 邏輯） | path-based 沙盒 |
| policy 施加者 | 管理員（外部） | 管理員（外部） | 管理員 / 工具 | **程式自己**（內部） |
| 需要 root | 是 | 是 | 載入需 CAP_BPF/CAP_MAC_ADMIN | **否**（unprivileged） |
| 學習曲線 | 陡（label、role、TE 規則） | 平（讀路徑就懂） | 中（要會 BPF） | 中 |
| 精準度 | 最高（inode label） | 中（路徑等價性弱點） | 看你怎麼寫 | 中 |
| 改規則要重開機 | 否（載新 policy 即可） | 否（reload profile） | 否（動態載 BPF） | 否（程式呼叫 syscall） |
| 預設於 | Fedora/RHEL/Android | Ubuntu/SUSE | 需自建 | 應用自行採用 |
| 典型場景 | 高保安伺服器、行動裝置 | 一般伺服器、快速上手 | 客製化 / 觀測整合 | 應用層沙盒 |

沒有「最好的」——SELinux 精準但重、AppArmor 好上手但路徑語意脆、BPF LSM 靈活但要會 BPF、Landlock 讓應用自我設限但覆蓋面窄。生產上常見**組合**：一個 major LSM（SELinux 或 AppArmor）+ Yama（ptrace）+ Lockdown（限制 root 對 kernel 的存取）+ 選擇性的 BPF LSM，靠 stacking 疊起來。

## 踩雷集錦

1. **以為 LSM 能「授權」DAC 拒絕的存取**。錯。LSM 在 DAC 之後、只能更嚴（restrictive）。DAC 說 no 就直接 `-EACCES`，根本走不到 LSM。想靠 SELinux 讓某 process「多」讀一個 DAC 擋著的檔案是不可能的——先改 DAC。

2. **`setenforce 0` 當成「修好了」**。切 permissive / disabled 只是把牆拆了，問題（缺 allow 規則、label 貼錯）還在。正確流程：permissive 收 AVC → `audit2allow` 產規則或 `restorecon` 修 label → 切回 enforcing 驗證。生產環境長期 permissive 等於沒 MAC。

3. **把「檔案打不開」全賴給權限（DAC），忘了看 SELinux**。`ls -l` 顯示 rwx 都對、`id` 也是對的 user，卻還是 `Permission denied`——這時要 `ls -Z` 看 label、翻 audit log 找 AVC denied。DAC 過了不代表 LSM 過了，兩關獨立。

4. **改了檔案位置卻沒 `restorecon`，label 錯了**。SELinux label 存在檔案的 xattr 上，跟著 inode 走。你 `mv` 一個檔到 `/var/www`，它可能還帶著舊 context（如 `user_home_t`），httpd 讀不到。要 `restorecon -v` 依 fcontext 規則重貼 label，而不是 `chcon` 手動硬設（`chcon` 會被下次 `restorecon` 覆蓋）。

5. **以為 fork 出的 shell 會逃出 domain**。不會。domain transition 只在 exec 時依 policy 發生；fork 繼承父 domain。攻擊者在 `httpd_t` 裡起一個 `/bin/bash`，那個 bash 仍是 `httpd_t`，被同一個籠子框著。這正是 MAC 擋 shell-escape 的核心，也是 `oscp` 裡「拿到 shell 卻幾乎什麼都做不了」的原因。

## 進階：再往深一層

- **hook 的效能**：早年 `security_hook_heads` 是 hlist，每個 hook 點都要走 list、間接呼叫。v6.x 用 static call 把「沒開的 LSM」的 hook 直接短路掉，開了的用直接跳轉，把 LSM always-on 的開銷壓到很低。這是「安全機制不該讓你為了效能而關掉它」的工程實踐。
- **`security_add_hooks` 與 `__lsm_ro_after_init`**：LSM 的 hook 表在 init 後標成 read-only（`ro_after_init`），防止有人在 runtime 竄改 hook 指標把 SELinux 換成 no-op——這是防「攻擊者停用 LSM」的縱深防禦。看 `security/security.c` 的初始化路徑。
- **面試常問**：「DAC 和 MAC 差在哪、為什麼有了 DAC 還要 MAC？」——答案就是本章開頭那個「被攻破的 root」情境。能講清楚「MAC 的限制主體無權更改、連 root 都受 policy 約束」就到位了。進階版：「SELinux 怎麼在 exec 時換 domain？」考的是 `bprm_creds_for_exec` 那段。
- **Android 的 SELinux**：Android 從 5.0 起全 enforcing，用大量 SELinux policy 隔離每個 app、每個系統服務——這是 SELinux 在真實世界最大規模的部署。你的行動 / MTK 世界裡，`sepolicy` 是繞不開的東西，本章的 TE 模型就是它的基礎。
- **和 seccomp/namespace 的分工（Ch 49）**：LSM 管「能不能對某物件做某操作」；seccomp 管「能不能呼叫某 syscall」；namespace 管「你看得到哪些資源」。三者正交、常合用——容器（`docker` 課）就是 namespace + cgroup + seccomp + 常配一個 AppArmor/SELinux profile 疊出來的。

## 動手練習

1. **畫出你這台機器的關卡順序**：讀 `/sys/kernel/security/lsm`，把每個 LSM 按順序列出，標注哪個是 major、哪個管 ptrace、哪個是 capability。然後用一次 `open` 為例，說出 DAC 和這串 LSM 各在哪一步介入。
2. **製造並讀懂一條 denial**：SELinux 機器切 permissive，用 `curl`/`httpd` 去讀一個 label 錯的檔案，`ausearch -m AVC -ts recent` 找到那條記錄，寫下 scontext/tcontext/tclass/動作，並說出「要加哪條 allow 或改哪個 label」才能修好（別真的關 enforcing）。
3. **對照兩種模型寫同一條規則**：用一句話寫「httpd 只能讀 /var/www」——分別用 SELinux 的 `allow ... : file read` 語意和 AppArmor 的 `/var/www/** r,` 語意表達，說出兩者在「hard link 到 /var/www 的檔案」情境下的差異。
4. **（進階，接 bpf 課）跑一個最小 BPF LSM**：在開了 `CONFIG_BPF_LSM` 的 kernel 上，把上面 `mini_lsm.bpf.c` 改成「禁止任何人 open 某個你 touch 出來的檔案」，載入後驗證 `cat` 那個檔會拿到 `-EPERM`，`rmmod`/detach 後恢復。體會「不重編 kernel 就新增一條 MAC 規則」。

## 本章重點整理

- **DAC 由擁有者自主、root 全能；MAC 由系統 policy 強制、連 root 都框住**。LSM 在 DAC 之後執行，只能更嚴（restrictive），是防「被攻破的 root」的關鍵。
- **LSM 是中立框架**：kernel 在敏感操作前埋 `security_*()` hook（清單見 `include/linux/lsm_hook_defs.h`），callback 由 SELinux/AppArmor/BPF LSM 等可插拔模組實作，回 0 放行、負 errno 拒絕；現代可 stacking 疊多個。
- **SELinux = type enforcement（label-based）**：process 有 domain、物件有 type，policy 的 `allow` 規則決定誰能對誰做什麼，default deny，靠 AVC 加速、AVC denied 記進 audit log；domain transition 在 exec 時由 `bprm_creds_for_exec` 觸發。
- **AppArmor = path-based**，用路徑寫 profile，好懂但路徑語意較脆；其餘還有 Yama（ptrace）、Landlock（unprivileged 沙盒）、BPF LSM（動態）、IMA/EVM（完整性）各管一塊。

## 自我檢核

- [ ] 不看筆記，能講清楚 DAC 與 MAC 的差異，以及「為什麼有了 DAC 還需要 MAC」（用被攻破的 root 情境）
- [ ] 能解釋 LSM 為什麼是「框架」而非寫死一種 MAC，以及 hook 為什麼在 DAC 之後、只能更嚴
- [ ] 面試被問「SELinux 怎麼在 exec 時決定 process 的 domain」，能說出 type_transition + `bprm_creds_for_exec`
- [ ] 看到一條 AVC denied，能指出 scontext/tcontext/tclass/動作，並說出該加哪條 allow 或修哪個 label
- [ ] 能說出 SELinux（label）與 AppArmor（path）模型的取捨，並舉一個 path-based 的弱點（hard link / bind mount）
- [ ] 知道 BPF LSM 掛在 LSM 框架上、回傳語意與 C LSM 相同，且不需重編 kernel（接 bpf 課 / Ch 52）

## 延伸閱讀

### 官方文件

- **[Documentation/admin-guide/LSM/index.rst](https://www.kernel.org/doc/html/latest/admin-guide/LSM/index.html)**
  - **讀哪裡**：總覽 + SELinux/AppArmor/Yama/Landlock/BPF 各子頁。這是理解「有哪些 LSM、各自定位」最權威的地圖
  - **和本章關聯**：本章「其他 LSM」一節就是這裡的濃縮；想深入某個 LSM 從對應子頁進去

- **[Documentation/bpf/prog_lsm.rst](https://www.kernel.org/doc/html/latest/bpf/prog_lsm.html)**
  - **讀哪裡**：整篇，短。BPF LSM 怎麼寫、`SEC("lsm/...")` 怎麼對應 hook、回傳語意
  - **前提**：先有 `bpf` 課的 libbpf/CO-RE 基礎；本章動手 C 段是它的最小版

### 深入 SELinux

- **[The SELinux Notebook](https://github.com/SELinuxProject/selinux-notebook)** — SELinux Project 官方維護
  - **讀哪裡**：Core Components、Type Enforcement、Security Context 幾章
  - **為什麼值得讀**：市面上把 TE / context / policy 語言講得最完整的免費資料，遠比發行版文件深入
  - **前提**：讀完本章的 TE 直覺再進去，會事半功倍

- **[Bootlin Elixir: security/](https://elixir.bootlin.com/linux/v6.12/source/security)** — Bootlin
  - **怎麼用**：直接讀 `security/security.c`（框架 `call_int_hook`）、`security/selinux/hooks.c`（SELinux 各 hook 實作）、`include/linux/lsm_hook_defs.h`（hook 全清單）
  - **搭配本章**：本章給的每個函式名都能在這裡點進去看實作與所有呼叫點

### 進階 / 歷史

- **LWN.net 的 LSM stacking 系列文**（在 lwn.net 搜 "LSM stacking"）
  - **能學到什麼**：為什麼 major LSM 難共存、security blob 的 offset 協調問題、stacking 一路演進的工程權衡
  - **為什麼值得讀**：把本章「security blob」與「stacking」那兩段的背後難題講透，也是理解 kernel 安全社群怎麼做決策的好樣本

MAC 管「能不能對某物件做某操作」，是縱深防禦的一層。下一章換一個正交的維度：seccomp 直接限制「一個 process 能呼叫哪些 syscall」，namespace 則限制「它看得到哪些資源」——這兩者加上本章的 LSM，正是容器沙盒的三根支柱。

→ [Ch 49 seccomp-BPF 與 namespace](./49-seccomp-namespaces.md)
