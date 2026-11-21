# Ch 47 — 容器 / 虛擬化逃逸邊緣：container escape、hypervisor 攻擊面概覽

> **目標**：理解容器隔離的真實邊界在哪裡——不是「容器是輕量 VM」，而是「容器是同一個 kernel 上的一組隔離標籤」——並建立一張攻擊面地圖：kernel LPE、capability 濫用、檔案系統暴露、mount 逃逸分別能把你從容器裡帶到哪裡；再往外一層看虛擬化堆疊（hypervisor、device emulation）的攻擊面和 kernel pwn 的關係與差異。
>
> **環境**：概念對照章，以近期 Linux kernel（cgroup v2 為主流預設，runc/containerd 生態）與主流 hypervisor（KVM/QEMU）為參照。本章不逐步構造 exploit，是攻擊面地圖；涉及特定 CVE 的細節若無法在你的環境復現會標注「未實測」。

## 為什麼要單獨講這一章？

前面 45 章幾乎都在打一個問題：「怎麼在單一 kernel 上，從 user space 爬到 ring 0」。但現實世界裡，攻擊者拿到的落腳點往往不是一台裸機，而是**一個容器**，或者**一台雲端 VM**。拿到 kernel LPE 之後，下一個問題永遠是：「這個 kernel 之上，還有誰？」

- 如果你在一個容器裡拿到 kernel LPE：**你已經在 host 的 kernel 裡了**，容器那層隔離形同虛設——這是本章最重要的一句話。
- 如果你在一台 VM 裡拿到 kernel LPE：你拿下的是 **guest** kernel，離 host 還有一整層 hypervisor 要過，這是完全不同量級的工作。

本書前 45 章教你的所有技巧（heap spray、UAF、data-only attack）在容器場景裡幾乎可以直接套用——容器內的 kernel 攻擊面和裸機完全相同，因為**它就是同一顆 kernel**。這章要做的不是教新的 exploit 技巧，是把「容器不是強隔離」這句話講透，並畫出 kernel pwn 之外，圍著它的那一圈隔離機制與逃逸面。

## 先建立直覺：容器不是虛擬機

```
虛擬機（VM）：
┌─────────────┐  ┌─────────────┐
│ Guest Kernel│  │ Guest Kernel│   ← 每個 VM 有自己獨立的 kernel
│  App A      │  │  App B      │
└──────┬──────┘  └──────┬──────┘
       │   hypervisor (KVM/QEMU)   │
       └───────────┬───────────────┘
            Host Kernel              ← 真正碰硬體的只有這一層

容器（Container）：
┌───────┐  ┌───────┐  ┌───────┐
│App A  │  │App B  │  │App C  │      ← 三個容器，共用同一顆 kernel
│(ns/cg)│  │(ns/cg)│  │(ns/cg)│
└───┬───┘  └───┬───┘  └───┬───┘
    └──────────┼──────────┘
          Host Kernel                 ← 只有一顆，大家共用
```

VM 之間、VM 和 host 之間，隔著一整層**硬體輔助虛擬化**（Intel VT-x / AMD-V）加上 hypervisor，guest 的程式碼連「看到」host 記憶體的機會都被硬體層卡住。容器之間，隔著的是**同一顆 kernel 裡的一組軟體標籤**——namespace、cgroup、capability、seccomp——這些標籤全部跑在同一個 kernel 的同一個權限層裡，**沒有硬體邊界**。

這張圖是本章一半的內容。另一半是：既然容器共用 kernel，一個 kernel LPE 幾乎等於容器逃逸；而虛擬化雖然隔離更強，host kernel 自己也暴露了一塊攻擊面（device emulation、virtio），這塊攻擊面本身又是一個「kernel pwn」問題。

## 容器隔離的四根支柱

Docker、containerd、Kubernetes 底下的容器，隔離感完全來自四個 Linux kernel 機制的組合，不是哪個新發明的沙盒技術：

| 機制 | 做什麼 | 類比 |
|---|---|---|
| **Namespace** | 讓一組 process 看到自己專屬的一份「視野」——自己的 PID 樹、自己的網路介面、自己的掛載點 | 一個人以為自己住在整棟大樓，其實只是被安排看到自己那層的電梯按鈕 |
| **Cgroup**（control group） | 限制一組 process 能用多少 CPU / 記憶體 / IO | 給這層設用電量上限 |
| **Capability** | 把「root 能做的事」拆成約 40 個獨立權限位，容器通常只給一部分 | 不給你大樓鑰匙，只給你你那層的鑰匙 |
| **Seccomp** | 限制一組 process 能呼叫哪些 syscall | 不准你用電梯裡某些按鈕，即使你有鑰匙 |

這四個都是 kernel 在同一個特權層（EL1/ring 0）裡實作的**邏輯邊界**，不是獨立的執行環境。對照一下：VM 裡的 guest 程式碼，連「看到」host 記憶體位址的機會都沒有，中間隔著 EPT/NPT（Extended/Nested Page Tables）這層硬體翻譯；容器裡的程式碼，和 host 的其他 process **物理上共用同一份 kernel 記憶體、同一組 page table 的 kernel 那一半（TTBR1/高位半）**，只是被 namespace 擋住視野、被 capability 擋住操作權限。

> User namespace 是這四根支柱裡唯一一個會**主動擴大攻擊面**的機制——讓容器內的「root」只是對映到 host 上一個無特權 UID，聽起來更安全，但它同時打開了大量原本只有 root 才能走到的 kernel 程式碼路徑（檔案系統 mount、某些 netlink 操作）給 unprivileged user。這塊本書 [Ch 36 — User namespace 與攻擊面放大](./36-user-namespace.md) 已經整章拆過，這裡不重複，只強調一句：**容器安全模型幾乎是靠 user namespace 把「看起來危險的操作」交給一個「其實沒有真實權限」的身分去做**，一旦某個 kernel 子系統沒有正確檢查「這個操作是不是來自一個被 user namespace 隔開的 root」，就是一個容器逃逸洞。

### 四根支柱各自擋的是哪一類操作，不是哪一類漏洞

容易搞混的地方是：這四根支柱各自管的是**使用者能不能發出某個操作**，不是「kernel 程式碼本身有沒有漏洞」。把它們放回攻擊鏈的位置看會更清楚：

- **Namespace** 管的是「**視野**」——你能不能看到、能不能定址到某個資源（另一個 process、host 的某個網路介面、host 檔案系統的某個路徑）。它不檢查操作合不合法，只決定你的 syscall 參數（PID、fd、路徑）解析出來會指向哪裡。
- **Cgroup** 管的是「**用量**」——和攻擊面幾乎無關，它是資源配額機制，唯一和逃逸相關的是 cgroup v1 的 `release_agent`（見下方第 3 類）這個歷史遺留的側門。
- **Capability** 管的是「**操作權限**」——你的 syscall 參數已經正確解析到某個資源了，capability 檢查決定你能不能對它執行這個動作（mount、ptrace、載入 module）。
- **Seccomp** 管的是「**入口本身**」——比 capability 更早一層，直接擋住你連呼叫這個 syscall 的機會都沒有，不管你有沒有 capability。

這個順序（seccomp → capability → namespace 解析出來的資源）剛好是一次 syscall 從發出到生效會依序經過的檢查點，也解釋了為什麼本章前面強調「seccomp 是最有效的第一道防線」——**它卡在最前面，根本不讓你的 syscall 走到後面那些檢查**。

### 一張圖：一個 kernel LPE 怎麼「一次」穿透整疊隔離

```
       seccomp filter          ← 第一關：這個 syscall 允不允許呼叫？
            │ 通過（這個 syscall 在白名單內）
            ▼
       capability 檢查          ← 第二關：你有沒有權限做這個操作？
            │ 通過（操作本身不需要特殊 capability，
            │        例如一般的 ioctl/mmap）
            ▼
       namespace 視野解析       ← 第三關：這個資源對你來說存在/可見嗎？
            │ 通過（你操作的是你自己能看到的 fd/物件）
            ▼
   ══════════════════════════  ← 到這裡為止，四根支柱全部「正常通過」
   你現在在合法呼叫的 kernel 程式碼裡，
   觸發了一個記憶體破壞漏洞（UAF/OOB/race）
   ══════════════════════════
            │
            ▼
   heap primitive → 控制流/資料劫持 → commit_creds(...)
            │
            ▼
   你現在是 host kernel 裡的完整 root —— 四根支柱在這一刻
   全部失去意義，因為它們全部是「同一顆 kernel 裡的檢查邏輯」，
   而你已經在修改這顆 kernel 的執行結果本身
```

這張圖要傳達的重點：**容器逃逸不是「繞過」四根支柱裡的某一根**，是在「四根支柱都沒有被違反」的前提下，單靠一個 kernel memory corruption 漏洞，直接跳到所有檢查邏輯之下的那一層。這和 VM escape 需要先打穿硬體虛擬化邊界的難度是完全不同量級的,下文會具體對照。

## 核心命題：一個 kernel LPE ≈ 一次容器逃逸

把這句話拆開看為什麼成立：

容器裡的程式碼要觸發一個 kernel bug，走的 syscall 路徑和裸機上完全一樣——`ioctl()`、`mmap()`、某個 netfilter 設定、某個 io_uring 操作，這些 syscall 介面**不會因為呼叫者在容器裡就變成不同的程式碼**（除非這個子系統本身有針對 namespace 做隔離,例如網路相關的部分邏輯綁定 net namespace，但記憶體管理、SLUB allocator、大部分子系統的 bug 不分 namespace）。

所以：容器裡的 unprivileged user 一樣可以：

1. 載入一個會觸發漏洞的 syscall 路徑（除非 seccomp profile 擋掉了對應 syscall——這是為什麼 Docker 預設 seccomp profile 會擋掉幾十個「危險」syscall，例如 `clone3` 某些用法、`ptrace`、各種少見的 namespace 操作）
2. 在這個 kernel 裡拿到 heap primitive（UAF、OOB），走本書 Part 3 的套路
3. 拿到 `commit_creds(prepare_kernel_cred(0))` 等級的控制流/資料劫持——這一刻，你已經是 **host kernel 裡的 root**，不是「容器裡的 root」

容器裡原本綁著的 capability、user namespace 身分，對「已經在 kernel 裡執行任意程式碼」這件事毫無意義——capability 檢查本身是 kernel 程式碼的一部分，你已經在改 kernel 的執行流了，自然可以跳過那些檢查（直接呼叫 `commit_creds` 把自己的 `cred` 換成 host 的 `init_cred` 或構造一個全權限 `cred`）。

**反過來說**：如果容器內根本碰不到任何有漏洞的 syscall 路徑（嚴格的 seccomp profile + 沒開的 namespace 功能），容器隔離依然有意義，只是意義建立在「縮小暴露的 kernel 攻擊面」上，而不是「kernel 本身對容器有特殊防護」。這也是為什麼 **seccomp profile 的嚴格程度，實際上是容器安全的第一道、也經常是最有效的一道防線**——擋掉 `io_uring_setup`、擋掉 `bpf()`，就擋掉了 Part 5 兩個最大的礦區（[Ch 31](./31-io-uring.md)、[Ch 32](./32-ebpf.md)）。

前面提到「部分邏輯綁定 net namespace」這句話值得具體展開：本書 [Ch 30](./30-netfilter-nftables.md) 講的 netfilter/nf_tables 漏洞，很多確實是**掛在某個特定 net namespace 的資料結構上觸發的**——容器(或任何 unprivileged user namespace + net namespace 的組合)能建立自己的 net namespace、設定自己的 nftables 規則，漏洞觸發路徑完全在這個隔離開的 net namespace 範圍內。但這不代表漏洞的「影響範圍」也被限制在這個 namespace 裡——net namespace 隔開的是「你設定的規則對誰生效」，不是「處理這些規則的 kernel 程式碼本身跑在哪個隔離沙盒裡」。一旦你透過設定自己 net namespace 裡的 nftables 規則觸發了 kernel 記憶體破壞,那個記憶體破壞影響的是**整顆共用的 kernel**,不會被神奇地限制在「你的」net namespace 裡——這正好印證本章核心命題:namespace 決定你能不能構造出觸發條件,不決定漏洞觸發後的影響範圍。

## Container escape 的常見面向（不透過 kernel 0-day）

除了「找一個 kernel LPE」這條硬路，容器逃逸還有幾類**不需要 kernel 漏洞**、純粹靠設定錯誤或 capability 濫用的路徑，這些在真實世界的滲透測試和 CTF 容器題裡出現頻率更高：

### 1. Capability 濫用

容器給了不該給的 capability，常見的危險組合：

| Capability | 能做什麼危險的事 |
|---|---|
| `CAP_SYS_ADMIN` | 幾乎等同半個 root——可以 mount 檔案系統、操作各種 namespace，公認「最危險的單一 capability」 |
| `CAP_SYS_PTRACE` | 可以 `ptrace` host 上的其他 process（如果 PID namespace 沒隔好，或目標恰好可見） |
| `CAP_SYS_MODULE` | 可以載入 kernel module——等同直接在 host kernel 裡跑任意程式碼 |
| `CAP_DAC_READ_SEARCH` | 繞過檔案權限檢查做任意讀，搭配 `open_by_handle_at()` 類技巧可以讀到容器 mount namespace 外的檔案 |

`docker run --privileged` 本質上就是把幾乎所有 capability 都給了，等於直接放棄這層隔離——這不是「有漏洞」，是「設定選擇放棄防禦」，但實務上大量 CI/CD、開發環境圖方便就這樣開，是最常見的真實逃逸起點。

### 2. `/proc` 與 `/sys` 暴露

即使 mount namespace 把容器的檔案系統視野限制住，如果容器仍能看到完整的（或部分未正確隔離的）`/proc`、`/sys`，裡面藏著大量能影響 host 的旋鈕：

- `/proc/sys/kernel/core_pattern`、`modprobe_path`——本書 [Ch 16 — 經典利用原語](./16-classic-primitives.md) 介紹過的那組全域可寫目標，如果容器內能寫到這些檔案（沒被正確限制在容器自己的 namespace 視野內，或根本沒做 namespace 化），就是直接的 host 層級任意程式碼執行。
- `/proc/[pid]/root`、`/proc/[pid]/cwd` 之類的符號連結，如果能看到 host 上某個 process 的 PID（PID namespace 沒隔好，或透過 `/proc` 的某種洩漏），可以透過這些連結跳出容器自己的檔案系統視野。

### 3. Cgroup release_agent 類逃逸（cgroup v1）

這是過去幾年公開 writeup 裡最有名的一類純設定面逃逸,原理值得理解：

cgroup v1 有一個 `release_agent` 機制——當一個 cgroup 裡的最後一個 process 退出、且該 cgroup 設了 `notify_on_release=1`，kernel 會**以 host 的身分**（而非容器內的身分）執行 `release_agent` 檔案裡指定的那支程式。攻擊鏈大致是：

1. 容器內如果有 `CAP_SYS_ADMIN`，可以重新 mount 一個可寫的 cgroup v1 層級
2. 把 `release_agent` 指向容器內某個攻擊者控制的腳本路徑（但這個路徑要換算成 host 看到的路徑——這一步常常要搭配 `/proc/self/mountinfo` 洩漏出 host 上容器的實際掛載路徑）
3. 觸發 `notify_on_release`，kernel 在 host context 執行那支腳本

這個洞的根源不是 kernel memory corruption，是「cgroup v1 的 release_agent 設計本身假設呼叫者是可信的 host 層級操作」，疏於檢查是不是來自一個被隔離的容器。**cgroup v2 的統一層級（unified hierarchy）結構不同，拿掉了這個特定機制的可寫路徑**，這是 cgroup v2 相對 v1 被認為更安全的原因之一——但這句話請以你實際測試的 kernel/容器 runtime 版本為準，統一層級底下仍可能有其他路徑，**未實測，為一般性原理陳述，非逐版本驗證結論**。

### 4. 掛載逃逸：overlayfs 與 bind mount 的視野破洞

容器的檔案系統視野靠 mount namespace 隔開，但「隔開視野」和「隔開底層儲存」是兩件事——這類逃逸的機制核心是：**容器看到的檔案系統，很多時候只是 host 上同一份底層儲存的另一種呈現方式，不是真正獨立的一份資料**。

- **Overlayfs 分層結構本身的洩漏**：容器映像（image）用 overlayfs 疊成,底層的 image layer 和容器執行時寫入的 upper layer 在 host 上都是真實存在的目錄。如果容器內的程式能透過某種方式取得指向這些底層目錄的檔案描述符(例如透過一個已經開啟、但 host 端路徑恰好可以被推算出來的 fd，或者透過 `/proc/[pid]/fd` 這類暴露)，就能繞過 mount namespace 本來要擋住的視野限制，直接碰到 host 上其他容器、甚至 host 自己的檔案。
- **Bind mount 掛了不該掛的路徑**：如果容器啟動設定裡 bind mount 了 host 的某個敏感目錄（常見錯誤：掛整個 `/`、掛 Docker socket `/var/run/docker.sock`、掛 host 的 `/proc`、`/sys` 而沒有做任何過濾），這不是漏洞，是設定錯誤本身直接給了逃逸的路——掛了 Docker socket 的容器,可以直接透過它請求 host 上的 Docker daemon 啟動一個新的、`--privileged` 的容器,間接達成逃逸,完全不需要碰任何 kernel 漏洞。
- **`/proc/[pid]/root` 這類符號連結被濫用**：如果容器內能看到 host 上某個其他 process 的 PID（例如 PID namespace 共享、或透過某個洩漏管道），`/proc/[pid]/root` 這個符號連結會指向那個 process 的檔案系統根目錄——對一個擁有足夠權限追蹤這個連結的攻擊者,等同繞過自己的 mount namespace 直接走進另一個 namespace 的檔案系統視野。

這三類的共同根源是：**mount namespace 隔開的是「路徑怎麼解析」，不是「底層 inode/儲存本身的存在」**——只要有任何管道能繞過路徑解析這一層、直接碰到底層物件，mount namespace 的隔離就形同沒有發生過。

### 5. Mount 逃逸 / runc 層級漏洞

容器執行的最底層（runc、containerd-shim）本身是跑在 host 上、用 host 身分操作檔案系統與 namespace 切換的程式，**這些程式本身的 bug 就是容器逃逸**，和 kernel 無關：

- 經典案例 **CVE-2019-5736**：runc 在進入容器 namespace 之前，透過 `/proc/self/exe` 這個符號連結去執行自己，攻擊者如果能控制容器內某個稍後會被 host 執行的二進位檔路徑，就能覆寫 runc 自己的二進位檔，讓 host 下次呼叫 runc 時執行攻擊者的程式碼——**這不是 kernel 漏洞，是 runc 自己的邏輯錯誤**，但影響等同 kernel LPE：一旦打穿，攻擊者在 host 上任意執行。
- 更近期的 **CVE-2024-21626**：runc 的 working directory 檔案描述符（fd）洩漏，可能讓容器內構造的 image 在 host 上建立容器時取得對 host 檔案系統的存取——同樣是 runtime 層而非 kernel 層的問題。

這類漏洞提醒我們:「容器逃逸」這個詞涵蓋的範圍比「kernel LPE」大得多，**容器的信任邊界其實包含整條 runtime 工具鏈（runc → containerd → dockerd），任何一環的邏輯錯誤都可能是逃逸點**，不是只有 kernel 這一層要盯。

## 再往外一層：虛擬化的攻擊面

容器共用 kernel，VM 不共用——但這不代表 VM 隔離無敵，只是攻擊面換了位置。

VM 隔離強度的根基是硬體輔助虛擬化（Intel VT-x / AMD-V）提供的**第二層位址翻譯**：guest 作業系統看到、操作的「實體位址」其實是 guest physical address（GPA），還要再經過一層由 host 端的 EPT（Extended Page Tables，Intel）或 NPT（Nested Page Tables，AMD）翻譯，才變成真正的 host physical address（HPA）。這一層翻譯完全由硬體執行、由 host（透過 KVM）設定，guest 裡的任何程式碼——即使它已經是 guest kernel 層級的攻擊者——**都無法直接表達出一個指向 host 其他記憶體區域的位址**，因為它能操作的位址空間從硬體層就被框限在這台 VM 分配到的 GPA 範圍內。這和容器的 namespace 隔離完全不同量級：namespace 是「kernel 軟體邏輯決定你看不看得到」，EPT/NPT 是「硬體翻譯表決定你的位址運算結果能不能落在別的範圍」。

這也是為什麼 VM escape 幾乎從不是「guest kernel 直接讀到 host 記憶體位址」這麼簡單的事，必須透過某個明確設計好、刻意打開的互動介面——device emulation 或 virtio——去間接影響 host 端的程式碼執行,這正是接下來兩類攻擊面的共同前提。

```
Guest VM
┌────────────────────┐
│  Guest Kernel       │ ← guest 的 LPE 只拿下 guest，不直接碰到 host
│  Guest App          │
└──────────┬──────────┘
           │ virtio queue / MMIO / PIO
           ▼
┌────────────────────┐
│ QEMU（device 模擬） │ ← 跑在 host userspace，模擬虛擬硬體
└──────────┬──────────┘
           │ ioctl(KVM_RUN) 等
           ▼
┌────────────────────┐
│ KVM（host kernel）  │ ← 真正做 CPU 虛擬化（VT-x/AMD-V）、EPT
└────────────────────┘
           │
      Host Kernel 其餘部分（vhost-net 等也在這裡）
```

VM escape（VM 逃逸）指的是：guest 裡的攻擊者,透過某種手段,執行到 host 的程式碼或拿到 host 的權限。常見的兩種攻擊面：

1. **Device emulation 漏洞（QEMU 層）**：QEMU 用軟體模擬一整套虛擬硬體（網卡、磁碟控制器、USB、音效卡……），guest 透過 MMIO/PIO 和這些模擬裝置互動。模擬裝置的程式碼如果有記憶體破壞漏洞（經典案例 **VENOM, CVE-2015-3456**，QEMU 的虛擬 floppy disk controller 有 buffer overflow），guest 可以透過精心構造的硬體操作序列，在 **host userspace**（QEMU process）裡拿到任意程式碼執行——這一步還沒到 host kernel，但 QEMU 通常以相當高的權限跑，拿下它往往離 host root 不遠。
2. **Virtio / vhost 漏洞（host kernel 層）**：為了效能，部分裝置模擬（尤其網路 `vhost-net`、SCSI `vhost-scsi`）**直接搬進了 host kernel**，而不是留在 QEMU userspace 跑。這意味著：guest 透過 virtio queue 送進來的資料，如果觸發 vhost 驅動裡的一個記憶體破壞漏洞，**這就是一次貨真價實的 host kernel pwn**——和本書前面 45 章的技巧（heap spray、UAF）直接適用，只是觸發介面換成了 virtio ring 而非普通 syscall。

### virtio 機制再具體一點：為什麼它既像 kernel pwn,又不是

virtio 是 guest 和 host 之間傳資料的標準化介面,核心資料結構是 **virtqueue**——一組環狀緩衝區（ring），guest 把要送出的資料描述符（descriptor，裡面是指向 guest 記憶體的位址與長度）寫進 ring，host 端（可能在 QEMU userspace，也可能在 vhost/host kernel）讀出這些描述符，照著指到的位址把資料搬過去或搬過來，完成之後再把結果寫回另一個 ring 通知 guest。

```
Guest                                    Host
┌──────────────┐                    ┌──────────────┐
│ 填 descriptor │──virtqueue (ring)─▶│ 讀 descriptor │
│ (指標+長度)   │                    │ 照著指標存取  │
└──────────────┘                    │ guest 記憶體  │
                                     └──────────────┘
```

這個機制本身就是一個「host 端的程式碼，根據 guest 送來的、本質上不可信的資料，去操作記憶體」的模式——**和 kernel 裡 `copy_from_user()` 之後處理使用者資料的模式是同構的**，差別只在於：

- 如果這段處理邏輯在 **QEMU userspace**，一個解析 descriptor 時的邊界檢查錯誤，後果是 QEMU process 的記憶體破壞——攻擊技巧是一般的 user-space heap/stack exploitation（對應 `ctf-pwn` 範疇），不是本書的 kernel pwn 技巧。
- 如果這段處理邏輯在 **vhost（host kernel 裡）**，同一種 descriptor 邊界檢查錯誤，後果直接是 host kernel 的記憶體破壞——這時候你面對的就是貨真價實的 SLUB allocator、kernel heap primitive，本書 Part 3 的技巧原封不動適用,只是**觸發的入口從 syscall 換成了 virtqueue 的 descriptor**。

這就是「既像又不同」的具體含義：**virtio 這個資料交換模式本身，不管實作在 userspace 還是 kernel，都延續了「處理不可信輸入」這個核心問題；但同一個模式漏洞的利用技巧,完全取決於這段處理邏輯實際跑在哪一層**——判斷一個 virtio/vhost CVE 該用哪套技巧打，第一步永遠是先確認漏洞函式所在的程式碼是不是在 `drivers/vhost/` 這類 host kernel 路徑下，而不是假設「virtio 相關 = 一定是 kernel pwn」或「一定是 user-space pwn」。

### VM escape 和 kernel pwn 的關係與差異

| | 容器逃逸 | VM escape（QEMU 層） | VM escape（vhost/host kernel 層） |
|---|---|---|---|
| 攻擊目標 | host kernel（同一顆） | host userspace process | host kernel |
| 需要 kernel 0-day 嗎 | 通常需要（或 runtime/設定漏洞） | 不一定，QEMU 自己的 bug 即可 | 需要（vhost 驅動本身的 bug） |
| 本書技巧適用度 | 完全適用 | 不適用（那是 user-space pwn 範疇） | 完全適用，只是觸發點不同 |
| 隔離強度假設 | 弱（同 kernel） | 中（有 CPU 虛擬化硬體邊界） | 中（有硬體邊界，但 bug 在 host kernel 內） |

**一句話總結這張表**：容器逃逸的「容易」程度建立在「隔離邊界本身很薄」；VM escape 的「困難」程度建立在「要先打穿硬體輔助虛擬化這層邊界」，但一旦打穿的位置恰好落在 host kernel 裡（vhost 類驅動），後續的利用技巧和裸機 kernel pwn 完全是同一套。

對防禦與架構設計的啟示：這也是為什麼 **gVisor、Kata Containers** 這類「用使用者空間 kernel 或輕量 VM 包一層」的方案會存在——它們想在容器的輕量與 VM 的隔離強度之間找一個折衷點，本質上是在「容器共用 kernel 太薄」和「每個容器都開一台完整 VM 太重」之間，插入一層額外的攻擊面縮小措施（gVisor 用 userspace 實作的 syscall 攔截層擋掉大部分直接碰 host kernel 的機會；Kata 給每個容器一台極輕量的 VM）。

## 對比與取捨

| 隔離方案 | 隔離強度 | 效能開銷 | kernel 攻擊面 |
|---|---|---|---|
| 原生容器（namespace+cgroup+cap+seccomp） | 弱（共用 kernel） | 幾乎為零 | 完整暴露給容器內 user（受 seccomp 限制） |
| gVisor | 中（userspace 攔截大部分 syscall） | 中（syscall 攔截開銷） | 大幅縮小，但攔截層自己是新攻擊面 |
| Kata Containers（輕量 VM） | 強（獨立 guest kernel） | 較高（每容器一個 VM） | 轉移到 guest kernel + hypervisor 邊界 |
| 完整 VM | 強 | 高 | 轉移到 hypervisor/device emulation 邊界 |

這張表的隱藏結論是：**沒有一個方案是「隔離強、效能又好、攻擊面又小」三者兼得**。原生容器選擇效能優先,把安全性賭在「kernel 本身不要有洞」加上嚴格的 seccomp/capability 設定；完整 VM 選擇隔離優先,代價是每個工作負載都要背一整顆獨立 kernel 的資源開銷；gVisor/Kata 想要折衷,但折衷本身就是在「原本共用 kernel 的攻擊面」和「原本獨立 VM 的資源開銷」之間找一個新的攻擊面(攔截層本身的 bug，或者精簡化 hypervisor 本身的 bug)。沒有正確答案,只有「你的威脅模型容許哪一種攻擊面存在」的工程判斷——這也是為什麼同一個公司的不同產線,可能同時用到原生容器(內部可信工作負載)和 Kata(跑客戶上傳的不可信程式碼)兩種方案。

## 踩雷集錦

1. **以為容器是「輕量版的虛擬機」**：這是最常見也最危險的錯誤心智模型。容器和 VM 的隔離機制完全不同層級——容器共用 kernel，VM 不共用。拿 VM 的信任假設套在容器上，會系統性低估容器逃逸的風險。
2. **以為 `--privileged` 只是「給多一點權限」**：它幾乎等同放棄所有四根隔離支柱,不是程度上的放鬆,是性質上的放棄。CI/CD 環境裡為了掛載 Docker socket 而加 `--privileged` 是常見但高風險的做法。
3. **以為 seccomp 預設 profile 什麼都擋住了**：Docker 的預設 seccomp profile 擋掉了幾十個 syscall，但沒擋住的那些裡仍有大量 kernel 攻擊面（例如多數 Part 5 子系統的入口 syscall 本身不在預設黑名單裡）。seccomp profile 的覆蓋範圍要具體去讀,不能假設它已經覆蓋了你擔心的那個 syscall。
4. **把 cgroup v1 的 release_agent 逃逸當成「kernel 漏洞」**：它是設計假設錯誤（假設呼叫者可信），不是 memory corruption，修補方式也不是打 patch 而是限制誰能寫 `release_agent`、或改用 cgroup v2。概念上要和本書前面講的 memory corruption 類漏洞分開歸類。
5. **低估 runtime 層（runc/containerd）漏洞的影響範圍**：CVE-2019-5736 這類漏洞完全不碰 kernel,但影響等同 kernel LPE——容器的信任邊界不是只有 kernel 一條線,工具鏈的每一層都要算進攻擊面。
6. **以為 mount namespace 隔開視野就等同隔開資料**：overlayfs 類逃逸的根源正是「視野隔開了,底層儲存沒有真正隔開」。只檢查「容器看不到那個路徑」不代表安全,要進一步確認有沒有任何 fd、符號連結、或掛載設定能繞過路徑解析直接碰到底層物件。
7. **看到 virtio/vhost 的 CVE 就假設一定要用 kernel pwn 技巧,或假設一定不用**：這個漏洞該怎麼打,取決於漏洞函式實際跑在 QEMU userspace 還是 host kernel 的 vhost 驅動——同一個 virtio 資料交換模式,兩種實作層級需要完全不同的利用技巧,先確認程式碼位置再決定方法論。
8. **以為 net namespace 把漏洞的影響範圍也隔開了**：net namespace 只決定「你的設定對誰生效」,不決定「處理這些設定的 kernel 程式碼本身跑在哪個隔離範圍」。在自己的 net namespace 裡觸發一個 nftables 記憶體破壞漏洞,影響的是整顆共用的 host kernel,不會被侷限在你自己的 net namespace 內——這是「namespace 決定能不能構造觸發條件」和「namespace 決定影響範圍」之間最容易混淆的一組概念。

## 進階：再往深一層

- **把「kernel bug → 容器逃逸」這條路實際走一遍**：選一個本書前面章節已經打穿的漏洞（例如 [練習 B](./practice-b-stack-to-root.md) 或 [練習 C](./practice-c-uaf-tty.md) 的題目），改成在一個 Docker 容器（非 `--privileged`，用預設 seccomp profile）裡跑一次，觀察哪些步驟因為 seccomp 被擋、需要調整利用鏈。這會讓你對「seccomp 實際擋了什麼」有具體感覺，而不是紙上談兵。
- **讀一篇完整的 cgroup release_agent writeup**，重建一次（在你自己的容器實驗環境），理解「設定面逃逸」和「記憶體破壞逃逸」在攻擊鏈構造思路上的本質差異。
- **往 hypervisor 攻擊面深入**，如果你對 VM escape 感興趣，QEMU 的 device emulation 程式碼本質上是一般的 C 程式記憶體破壞問題（user-space pwn 範疇），但 vhost 類驅動的漏洞和本書的 kernel pwn 技巧完全銜接——找一篇 vhost CVE 的 writeup，你會發現利用鏈長得和 Part 3 的 heap exploitation 幾乎一樣。
- **比較 gVisor 和 Kata Containers 實際怎麼縮小攻擊面**：gVisor 的 Sentry（用 Go 實作的 userspace kernel）把大部分 syscall 攔下來自己處理，只有少數真的需要碰硬體的操作才會透過一個精簡過的介面轉給 host kernel——去讀它的架構文件，具體列出它實際還保留了哪些會直接碰到 host kernel 的路徑，這些路徑就是 gVisor 環境下容器逃逸仍然可能發生的地方。Kata 則是另一個方向：直接給每個容器一台用輕量 hypervisor（如 Cloud Hypervisor、Firecracker）開的 VM，攻擊面轉移到我們這章後半討論的 hypervisor/device emulation 範疇。兩個方案代表了「縮小 kernel 攻擊面」的兩種不同思路,值得對照著讀。

## 動手練習

1. 啟動一個預設設定的 Docker 容器（不加 `--privileged`），在裡面執行 `capsh --print` 看預設給了哪些 capability；再執行 `cat /proc/self/status | grep Seccomp`，確認 seccomp 是否啟用（`Seccomp: 2` 代表 filter 模式啟用）。對照你剛才看到的 capability 清單，判斷哪些本書前面章節的 syscall 入口（例如 `bpf()`、`io_uring_setup()`、`unshare()`）在這個容器裡是否還能呼叫到。
2. 用 `docker run --cap-add=SYS_ADMIN` 啟動一個容器，在裡面嘗試 `mount -t cgroup -o none,name=x cgroup /mnt`，觀察這個操作在預設容器（無額外 capability）和加了 `SYS_ADMIN` 的容器裡分別是成功還是被拒絕，體會 capability 檢查的實際作用點。
3. （純概念,不需要動手）畫出你自己對「容器逃逸」和「VM escape」的攻擊面地圖，標出每一類逃逸分別落在本章表格裡的哪一格,確認你能解釋為什麼它落在那一格而不是別格。
4. 在一個測試容器裡執行 `mount | grep overlay`，觀察 overlayfs 的 lowerdir/upperdir/merged 實際對應到 host 上的哪些路徑（如果你的環境權限允許，用 `docker inspect` 或查看 `/var/lib/docker/overlay2/` 下對應的目錄結構），體會本章「視野隔開不等於資料隔開」這句話在實際檔案系統結構上長什麼樣——**僅在你自己的測試環境操作，不要對任何正在服務真實流量的容器做這個操作**。

## 本章重點整理

- 容器隔離建立在同一顆 kernel 上的四個軟體機制（namespace/cgroup/capability/seccomp），沒有硬體邊界；VM 隔離靠硬體輔助虛擬化，有獨立的 guest kernel。
- 一個 kernel LPE 幾乎等同容器逃逸，因為容器內觸發漏洞走的 syscall 路徑和裸機完全相同；seccomp profile 的覆蓋範圍是容器安全第一道、也常是最有效的防線。
- 容器逃逸的攻擊面比「kernel 漏洞」大得多，涵蓋 capability 濫用、`/proc`/`/sys` 暴露、cgroup release_agent 類設定面逃逸、以及 runc/containerd 這類 runtime 工具鏈自身的邏輯漏洞。
- VM escape 分兩個量級：打穿 QEMU device emulation 只拿到 host userspace；打穿 vhost 類搬進 host kernel 的裝置模擬,才是貨真價實的 kernel pwn,和本書技巧直接銜接。
- namespace 決定「能不能構造出觸發條件」,不決定「漏洞觸發後的影響範圍」——這是本章反覆強調、也最容易被誤解的一條界線。

## 自我檢核

- [ ] 能不看筆記解釋為什麼「容器共用 kernel」這句話決定了容器逃逸和 VM escape 的本質差異
- [ ] 能說出至少三種不需要 kernel 0-day 的容器逃逸路徑,並說明它們各自的根源(設定錯誤 vs 設計假設錯誤 vs runtime 層邏輯漏洞)
- [ ] 能解釋為什麼 vhost 類漏洞「等同 kernel pwn」而 QEMU device emulation 漏洞不是
- [ ] 如果有人問「我們的容器環境夠安全嗎」,能從 capability/seccomp/user namespace 三個維度具體回答,而不是只說「容器本來就不夠安全」

## 延伸閱讀

### 官方文件 / 權威來源

- **[Docker 官方 seccomp 文件](https://docs.docker.com/engine/security/seccomp/)**
  - **讀哪裡**：「Significant syscalls blocked by the default profile」整節
  - **能學到什麼**：Docker 預設 seccomp profile 實際擋了哪些 syscall,搭配本章「seccomp 第一道防線」的說法對照確認
  - **前提知識**：本章「容器隔離的四根支柱」一節

- **[runc security advisories（GitHub）](https://github.com/opencontainers/runc/security/advisories)**
  - **讀哪裡**：直接搜尋 CVE-2019-5736 與 CVE-2024-21626 的公告全文
  - **能學到什麼**：runtime 層漏洞的完整技術細節與修補方式,補足本章「runc 層級漏洞」一節的概要描述
  - **前提知識**：無,但建議先理解 OCI runtime 的基本角色（建立/進入 namespace 的那一層程式）

### 部落格 / 技術文章

- **[Understanding and Hardening Linux Containers（NCC Group）](https://research.nccgroup.com/)**
  - **這篇說什麼**：系統性拆解容器隔離的每一層機制與已知逃逸手法,是這個領域公認的扎實參考
  - **讀哪裡**：namespace 與 capability 兩個主要章節
  - **為什麼值得讀**：NCC Group 是容器安全研究的核心團隊之一,這份報告的廣度和深度在同類文章裡少見

- **[A Compendium of Container Escapes（Black Hat / DEF CON 演講，作者 Yuval Avrahami 等）](https://i.blackhat.com/)**
  - **這篇說什麼**：把近年公開的容器逃逸 CVE 分類整理,每一類給出攻擊鏈示意
  - **讀哪裡**：分類章節,挑你陌生的類別深入
  - **為什麼值得讀**：這是本章「container escape 常見面向」小節的擴展版,有更多具體 CVE 案例可以對照

- **[QEMU VENOM 漏洞公告（CVE-2015-3456）](https://www.openwall.com/lists/oss-security/2015/05/13/1)**
  - **這篇說什麼**：floppy disk controller 模擬程式碼裡的 buffer overflow 如何從 guest 端的硬體操作序列觸發到 QEMU host process 的記憶體破壞
  - **讀哪裡**：漏洞描述與影響範圍段落，程式碼修補的 diff 可以對照理解具體哪個邊界檢查漏了
  - **為什麼值得讀**：是「QEMU device emulation 漏洞」這個分類裡被引用最多的經典案例，補足本章「Device emulation 漏洞」小節的具體技術細節

### 書籍 / 深度資源

- **《Container Security》** — Liz Rice（O'Reilly, 2020）
  - **這本書的定位**：目前業界公認講 Linux 容器底層機制最清楚的書之一,不是「怎麼用 Docker」而是「Docker 底下的 kernel 機制」
  - **讀哪幾章**：namespace、capability、seccomp 各自獨立成章,直接對照本章「四根支柱」小節逐一深入

把容器和虛擬化的隔離邊界搞清楚之後,我們把視角切到另一個維度——同一套 kernel pwn 方法論,換到 x86-64 和 ARM64 之外的架構,要重新校準什麼。

→ [Ch 48 其他架構速覽](./48-other-architectures.md)
