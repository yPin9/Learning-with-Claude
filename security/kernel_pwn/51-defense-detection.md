# Ch 51 — 防禦與偵測視角：hardening config、exploit 偵測、lockdown、KSPP、藍隊怎麼看這些技術

> **目標**：把全書的攻擊技術翻成防禦語言。學完你能做三件事：為一台 Linux 6.6 kernel 開出一份有依據的 hardening 清單（KSPP 的 config、sysctl、boot parameter、lockdown、縮小攻擊面），並知道每一項擋的是哪一章的哪一招；設計能抓到 kernel exploit 的偵測（crash 遙測、auditd、eBPF runtime detection、integrity measurement），並誠實說出每種偵測在 ring 0 失守之後還剩多少可信度；看完一條攻擊鏈，能逐段指出「防禦者在這一格能做什麼」。這是全書的防禦面總結，也是 README 說「藍隊也需要這本」的兌現。

> **環境**：Linux 6.6 LTS、x86-64 基線。每個 config / sysctl 的引入版本都標在文中，「就我所知」表示沒有查到一手來源。本章的樣本輸出有一部分是在本機 WSL2 的 kernel（**6.18，不是 6.6**）上**實測**的，僅作為「真實發行 kernel 的設定長什麼樣」，標「實測」者才是實測。其餘 shell、auditd、bpftrace 指令**未實測，為理論預期行為**，請只在你自己擁有的 VM / 機器上驗證。

## 為什麼需要這個？

前面 50 章我們一直站在攻擊者的座位。這一章換位置，因為攻擊與防禦在 kernel 這個領域有一個不對稱，值得先講清楚：

1. **攻擊者只要一條路**。一個 nf_tables 的 UAF（[Ch 30](./30-netfilter-nftables.md)）加上一個合適的 spray，就足夠。防禦者要把**每一條路**都處理掉，或讓它們都不可達。
2. **kernel exploit 成功的瞬間，偵測就失去立足點**。攻擊者在 ring 0，可以關掉你的 eBPF 探針、改你的 audit 設定、偽造你的 log。所以偵測的主要價值在**成功之前**（失敗、重試、探索的階段）與**成功之後從 kernel 之外看**。這是本章最重要的設計原則，後面會反覆回到它。
3. **防禦者讀攻擊書的實際收益是「排序」**。你有 200 個 config 可以調，但資源有限。讀完全書你知道：哪些是「直接封死一整類」的（縮小攻擊面），哪些是「提高成本」的（機率型 mitigation），哪些只是「讓事後有跡可查」的。

歷史上防禦的演化也符合這個結構：

| 階段 | 做法 | 代表 |
|---|---|---|
| 2000–2015 | 外部 patch 組：把一整套硬化塞進一個 kernel fork | PaX / grsecurity（本章稍後專節） |
| 2015– | 把這些想法一項項上游化（upstream）進 mainline | Kernel Self Protection Project（KSPP）；Kees Cook 等人 |
| 2019– | 攻擊面層級的控制：Lockdown、userns 限制、sysctl 關閉子系統 | Lockdown LSM（5.4）、`io_uring_disabled` 等 |
| 2022– | 編譯器層級的控制流保護、heap 隨機化 | KCFI（6.1）、random kmalloc caches（6.6）（[Ch 26](./26-modern-mitigations.md)–[28](./28-cfi-kcfi.md)） |

## 先建立直覺：防禦的三道關

把攻擊者地圖（Bug → Primitive → Leak → Control/Data → Privesc → Cleanup）和防禦對齊：

```
   攻擊者要走的路 ──────────────────────────────────────────────────▶

  [到得了 bug]  [Bug]  [Primitive]  [Leak]  [Control/Data]  [Privesc]  [Cleanup]
       │          │         │          │          │             │          │
       ▼          ▼         ▼          ▼          ▼             ▼          ▼
  ┌─────────┐ ┌────────┐ ┌─────────────────────────────────┐ ┌───────────────────┐
  │ 第一關   │ │ 第二關  │ │            第二關                │ │      第三關        │
  │ 縮小    │ │ 修掉    │ │       破壞利用（mitigation）      │ │ 偵測 / 遏制 /     │
  │ 攻擊面  │ │ (patch, │ │  random caches / KCFI / init /  │ │ 從外部驗證完整性   │
  │ (不可達) │ │ fuzz)   │ │  usercopy / KASLR / lockdown    │ │ (crash 遙測, eBPF, │
  │         │ │         │ │                                 │ │  IMA, 外部 attest) │
  └─────────┘ └────────┘ └─────────────────────────────────┘ └───────────────────┘
   效益最大      根治        每層擋一招，疊起來提高成本          成功前最有價值
   (刪除>緩解)                                                  成功後須在 kernel 外
```

三條經驗法則，後面每一節都在驗證它：

1. **刪除攻擊面優於緩解**。一個你根本不載入的 module，不可能有 UAF 被打。成本通常最低，效益最大。
2. **mitigation 是提高成本，不是封死**（[Ch 26](./26-modern-mitigations.md) 的結論）。要評估「疊起來擋住多少種 exploit」，而不是「這項有沒有開」。
3. **偵測要設計成「失敗時就有訊號」**。kernel exploit 多為機率性（[Ch 45](./45-exploit-stabilization.md)），失敗常以 oops / panic 收場，這是你最大的偵測優勢。

> 如果你對各個 mitigation 的細節還不熟，先回看 [Ch 26 — 2023+ mitigation 全景](./26-modern-mitigations.md) 的總表與 [Ch 28 — CFI/KCFI](./28-cfi-kcfi.md)。

## 第一關：縮小攻擊面

### 為什麼它是第一優先

[Ch 30](./30-netfilter-nftables.md)–[Ch 37](./37-other-subsystems.md) 的子系統攻擊面，多數有一個共同的**可達性前提**：低權限使用者需要某種權限才能碰到有 bug 的程式碼路徑。最常見的前提是 `CAP_NET_ADMIN`，而 unprivileged user namespace（[Ch 36](./36-user-namespace.md)）讓任何使用者在自己的 namespace 內合法取得它。換句話說：**一個設定決定了數十個子系統對低權限使用者是否可達**。

### 縮小攻擊面的旋鈕

先看一份真實 kernel 的狀態。這是在 WSL2 的 6.18 kernel 上**實測**讀 `/proc/sys/...` 的結果（再次提醒：不是 6.6，也不代表你的發行版）：

```
kernel/unprivileged_bpf_disabled = 2
kernel/io_uring_disabled         = 0
kernel/kptr_restrict             = 1
kernel/dmesg_restrict            = 0
kernel/perf_event_paranoid       = 2
user/max_user_namespaces         = 87817
vm/unprivileged_userfaultfd      = 0
kernel/modprobe                  = /sbin/modprobe
kernel/yama/ptrace_scope         = 1
vm/mmap_min_addr                 = 65536
```

逐項解讀，同時當作旋鈕清單：

| 旋鈕 | 這個樣本的值 | 加固建議 | 對應章節 | 代價 |
|---|---|---|---|---|
| `kernel.unprivileged_bpf_disabled` | 2（關，但管理員可改） | 1（關，開機後不可改回） | [Ch 32](./32-ebpf.md) | 少數無特權 eBPF 工具失效 |
| `kernel.io_uring_disabled` | 0（全開） | 2（全關）或 1（限特權/指定群組） | [Ch 31](./31-io-uring.md) | 用 io_uring 的服務（資料庫、部分 runtime）失效或退回；**先確認你的服務有沒有用** |
| `user.max_user_namespaces` | 87817 | 0（全禁）或用 distro 提供的限制機制 | [Ch 36](./36-user-namespace.md) | 見下方踩雷：rootless container、瀏覽器 sandbox 受影響 |
| `vm.unprivileged_userfaultfd` | 0 | 保持 0 | [Ch 41](./41-widening-race-window.md) | 極少數程式（如部分 VM 遷移工具）需要 |
| `kernel.kptr_restrict` | 1 | 2 | [Ch 14](./14-kaslr-infoleak.md) | 部分 perf / debug 工具讀不到符號位址 |
| `kernel.dmesg_restrict` | 0（任何人可讀 dmesg） | 1 | Ch 14、[Ch 28](./28-cfi-kcfi.md) | 一般使用者無法 `dmesg`；oops 訊息含位址，不該對低權限開放 |
| `kernel.perf_event_paranoid` | 2 | 2 以上；有些發行版 patch 出 3（上游值域就我所知到 2，請查你的 kernel 文件） | [Ch 37](./37-other-subsystems.md) | 一般使用者無法 profiling |
| `kernel.modprobe` | `/sbin/modprobe` | 搭配 `STATIC_USERMODEHELPER` 或監控其值 | [Ch 16](./16-classic-primitives.md) | 見下節 |
| `kernel.yama.ptrace_scope` | 1 | 1 以上 | — | 限制 ptrace 同使用者其他 process |
| `vm.mmap_min_addr` | 65536 | 保持（擋 NULL deref，[Ch 10](./10-vulnerability-taxonomy.md)） | Ch 10 | 無 |

**io_uring 的案例特別值得細看**。2023 年 6 月 Google 的安全部落格〈Learnings from kCTF VRP's 42 Linux kernel exploits submissions〉報告：在 kCTF VRP 的 submission 中，繞過他們 mitigation 的利用都用了 io_uring bug；他們因此決定在自家產品限制它：ChromeOS 停用 io_uring，Android 以 seccomp-bpf 讓 app 碰不到它，Google 的 production server 停用，GKE Autopilot 當時在評估預設停用。這是「以刪除攻擊面作為主要策略」的大廠實例。kernel 側對應的是 `kernel.io_uring_disabled` sysctl（Matteo Rizzo 的 patch，後續加入 `io_uring_group`；2023-08 的 LKML 討論，依時程我們認為是 6.6 起，**請以 `Documentation/admin-guide/sysctl/kernel.rst` 為準**）。值的語意（依 v4 patch）：0 全開、1 僅特權或指定群組、2 一律 `-EPERM`；已建立的 instance 不受影響。

### 不載入、不編入、不暴露

| 手段 | 做法 | 例子 | 注意 |
|---|---|---|---|
| 編譯期移除 | `CONFIG_X=n` | 不需要的 filesystem、`CONFIG_SMB_SERVER`（ksmbd）、不用的 USB class driver | 最徹底；要自己編 kernel |
| module 黑名單 | `/etc/modprobe.d/x.conf` 寫 `install nf_tables /bin/false` | 冷門 filesystem、`af_packet`（若不用）、`n_hdlc` 之類 | `blacklist` 只擋 alias 自動載入，`install ... /bin/false` 才擋顯式載入 |
| 禁止自動載入 | `kernel.modules_disabled=1`（開機後鎖死） | 靜態部署的 appliance | 鎖死後也無法載入合法 module；請對 6.6 的 `kernel/module/` 驗證其與 `request_module` 的互動，我們沒有逐行驗證 |
| 限縮 line discipline | `dev.tty.ldisc_autoload=0`（5.1 起，就我所知） | 擋非特權使用者靠自動載入 TTY line discipline 打 [Ch 35](./35-tty-keyring-watchqueue.md) 的 TTY 類 bug | 少數序列埠工具需要 |
| syscall 過濾 | seccomp-bpf profile | container 預設 profile、Android app | 只減少可達的 syscall，不修 bug |
| 以更小的 TCB 取代 | gVisor、Kata Containers、microVM | [Ch 47](./47-container-vm-escape.md) | 把 host kernel 從「直接暴露」變成「間接暴露」 |

> 近年 Docker / containerd 的預設 seccomp profile 是否擋 `io_uring_*`，我們沒有一手來源可引，請查你的版本的 profile。

### 關 user namespace 的實務

userns 是兩難：它是攻擊面放大器，也是 rootless container、Chrome / Firefox sandbox、Flatpak 的基礎。可行的策略（版本與 distro 行為請自行驗證）：

- **全禁**：`sysctl user.max_user_namespaces=0`。最乾淨，也最容易弄壞東西。
- **Debian 系的專用開關**：`kernel.unprivileged_userns_clone`（Debian 的 patch，上游沒有，就我所知）。
- **Ubuntu 較新版本**：以 AppArmor 限制未受信 profile 的 unprivileged userns 建立（就我所知的 `kernel.apparmor_restrict_unprivileged_userns`，請查你的版本）。
- **容器主機**：評估 rootless 是否真的需要，不需要就禁。

## 第二關：hardening 清單（KSPP）

### 原則

Kernel Self Protection Project 維護一份「Recommended Settings」（見延伸閱讀）。我們不整份抄，而是用**攻擊鏈**重新組織：每一項標「擋哪一章的哪一招」與「預設/成本」，讓你能取捨而不是盲從。

### config 清單（build time）

| config | 擋什麼 | 章 | 引入版本 | 備註 |
|---|---|---|---|---|
| `RANDOMIZE_BASE`（KASLR）、`RANDOMIZE_MEMORY` | kernel 與 physmap 位址可預測 | [Ch 6](./06-kernel-memory-layout.md)、[Ch 14](./14-kaslr-infoleak.md) | 3.14 / 4.8（就我所知） | WSL2 樣本實測：`RANDOMIZE_BASE=y` |
| `STRICT_KERNEL_RWX`、`STRICT_MODULE_RWX` | 寫入 kernel text、執行 data | [Ch 12](./12-control-flow-hijack.md) | 4.11 前後（就我所知） | 樣本實測：開 |
| `MITIGATION_PAGE_TABLE_ISOLATION`（舊名 `PAGE_TABLE_ISOLATION`） | Meltdown；user 態看不到 kernel 映射 | [Ch 15](./15-kpti.md) | 4.15 | 樣本實測：開。關閉用 `pti=off`，**不要在 production 這樣做** |
| `STACKPROTECTOR_STRONG`、`VMAP_STACK` | stack overflow 改 return address；stack 溢出進鄰近物件 | [Ch 11](./11-stack-overflow.md) | 4.x | 樣本實測：兩者皆開 |
| `RANDOMIZE_KSTACK_OFFSET`（預設開） | 依賴固定 stack 偏移的利用 | Ch 11 | 5.13（就我所知） | 樣本實測：開（`..._DEFAULT=y`） |
| `STACKLEAK`（GCC plugin） | stack 殘留資料洩漏 | Ch 14 | 4.20（源自 PaX）（就我所知） | 需 GCC plugin 支援；樣本未啟用 |
| `SLAB_FREELIST_RANDOM`、`SLAB_FREELIST_HARDENED` | freelist 順序可預測、freelist pointer 被改寫 | [Ch 9](./09-slub-hardening.md) | 4.8 / 4.14（就我所知） | 樣本實測：兩者皆開 |
| `SLAB_MERGE_DEFAULT=n` + `slab_nomerge` | 相似大小的 cache 合併，擴大 cross-cache 的可能 | [Ch 9](./09-slub-hardening.md)、[Ch 22](./22-cross-cache.md) | — | 樣本實測：`# CONFIG_SLAB_MERGE_DEFAULT is not set`（不合併） |
| `SHUFFLE_PAGE_ALLOCATOR` | page 配置順序可預測 | [Ch 7](./07-buddy-allocator.md) | 5.2（就我所知） | 樣本實測：開 |
| `RANDOM_KMALLOC_CACHES` | spray 與 victim 同 cache | [Ch 26](./26-modern-mitigations.md)、[Ch 27](./27-random-kmalloc-caches.md) | 6.6 | 樣本實測：**未開** |
| `INIT_ON_ALLOC_DEFAULT_ON` / `INIT_ON_FREE_DEFAULT_ON` | uninit leak / UAF 殘留 | Ch 26 | 5.3 | 樣本：alloc 開、free 關 |
| `HARDENED_USERCOPY` | 越界 copy_to/from_user | Ch 26、[Ch 4](./04-syscall-boundary.md) | 4.8 | 樣本：開 |
| `FORTIFY_SOURCE` | 編譯期/執行期偵測 `memcpy`/`strcpy` 越界 | [Ch 17](./17-heap-overflow.md) | 4.13（就我所知） | 樣本：開 |
| `BUG_ON_DATA_CORRUPTION`、`DEBUG_LIST` | linked list / 資料結構被破壞時直接 BUG | [Ch 18](./18-uaf-double-free.md) | — | 樣本：前者開、`DEBUG_LIST` 關。`DEBUG_LIST` 有成本，要取捨 |
| `X86_KERNEL_IBT` | 間接跳轉進函式中間 | [Ch 28](./28-cfi-kcfi.md) | 5.18 | 樣本：開（需 CET 硬體才有實際作用） |
| `CFI_CLANG`（KCFI） | 劫 RIP | Ch 28 | 6.1，需 clang 16+ | 樣本：**未開** |
| `STATIC_USERMODEHELPER` | `modprobe_path` 類 | [Ch 16](./16-classic-primitives.md) | 4.11（見 Ch 26 的版本爭議） | 樣本：**未開** |
| `SECURITY_LOCKDOWN_LSM` | root 修改 / 讀取 running kernel | 本章下節 | 5.4 | 樣本：**未開** |
| `BPF_UNPRIV_DEFAULT_OFF` | 無特權 eBPF | [Ch 32](./32-ebpf.md) | 5.13（就我所知） | 樣本：開 |
| `ZERO_CALL_USED_REGS` | ROP gadget 與暫存器殘值 | [Ch 13](./13-smep-smap.md) | 5.15（就我所知） | 樣本：未開（編譯器支援但沒啟用） |
| `RANDSTRUCT_FULL`（GCC plugin / clang） | 依賴固定 struct 欄位偏移的利用 | [Ch 19](./19-spray-objects.md) | 4.13（就我所知） | 樣本：`RANDSTRUCT_NONE`。代價：out-of-tree module 必須用同樣 seed 重編 |
| `MODULE_SIG_FORCE` | 載入未簽章 module | [Ch 5](./05-first-vulnerable-module.md) | — | 與 lockdown、Secure Boot 搭配 |

這張表的讀法：**樣本欄是一份真實設定的剖面**。主流 kernel 預設開的是低成本的（freelist hardening、init_on_alloc、usercopy、stack 類）；高效益但有整合成本的（KCFI、random caches、static usermodehelper、lockdown、`RANDSTRUCT`）要你自己決定。

### boot parameter 與 sysctl 的建議起點

以下是**起點，不是處方**；每一項的副作用請先在測試環境量過。**未實測於 6.6，為理論預期行為。**

```bash
# /etc/default/grub 的 GRUB_CMDLINE_LINUX 加上（概念；請逐項評估）
slab_nomerge init_on_alloc=1 init_on_free=1 page_alloc.shuffle=1 \
randomize_kstack_offset=on vsyscall=none hardened_usercopy=on pti=on \
lockdown=integrity module.sig_enforce=1
```

- `init_on_free=1` 的成本比 `init_on_alloc` 高，要 benchmark。
- `vsyscall=none`：關掉固定位址的 legacy vsyscall page（它是固定位址的 gadget 來源，x86-64 特有）；舊 glibc 的程式可能壞。
- `lockdown=integrity`：見下節。
- x86 KPTI 開機參數是 `pti=on|off|auto`，**不是 `kpti=`**（就我所知 arm64 另有自己的 `kpti=` 參數；本書 x86 一律 `pti=`）。

```bash
# /etc/sysctl.d/90-kernel-hardening.conf（概念；請逐項評估）
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
kernel.unprivileged_bpf_disabled = 1
net.core.bpf_jit_harden = 2
kernel.io_uring_disabled = 2          # 先確認服務沒用 io_uring
vm.unprivileged_userfaultfd = 0
dev.tty.ldisc_autoload = 0
kernel.kexec_load_disabled = 1
kernel.yama.ptrace_scope = 2
# user.max_user_namespaces = 0        # 會影響 rootless container / 瀏覽器，慎用
```

### Lockdown

Kernel Lockdown 是一個 LSM，**5.4** 起進入 mainline（man page `kernel_lockdown(7)` 明載）。兩個模式：

| 模式 | 意義 |
|---|---|
| `integrity` | 阻擋「讓 userland（含 root）修改 running kernel」的功能 |
| `confidentiality` | integrity 的超集，再加上阻擋「讓 userland 讀出 kernel 內機密」的功能 |

可用 `lockdown=` 開機參數設定，也可在執行期寫 `/sys/kernel/security/lockdown` 往更嚴的方向切換（不可往回放寬）。典型被擋的：未簽章 module 載入、`/dev/mem` / `/dev/kmem` 直接寫、未簽章 `kexec`、hibernation 的某些路徑、部分 debugfs / MSR / ioperm 存取（confidentiality 另擋 `kprobes` 這類可讀 kernel 記憶體的介面；確切清單請見 man page，我們不在這裡逐項複誦）。

**關鍵邊界**：Lockdown 的信任邊界是「root ≠ kernel」。它擋的是「已經是 root 的人去動 kernel」。本書的 exploit 多數是「**非特權 → kernel bug → 任意寫**」，一旦任意寫成立，攻擊者已在 ring 0，Lockdown 的檢查（在 kernel 自己的 API 入口）被繞過。所以它的角色是：阻止**root 變 ring 0** 的捷徑（載模組、寫 `/dev/mem`），以及作為 Secure Boot 信任鏈的一環，**不是**阻止 kernel exploit 本身。這是面試與安全評估時常被混淆的點。

## 第三關：偵測

### 先講限制

偵測有一個結構性問題：**成功的 kernel exploit 讓攻擊者取得跟你的偵測程式一樣的權限**。eBPF 探針、auditd、sysctl、log 檔，在 ring 0 全部可以改。所以我們把偵測分成三種時機，可信度遞減：

| 時機 | 能看到什麼 | 可信度 |
|---|---|---|
| **成功之前**（嘗試、失敗、探索） | 崩潰、oops、異常 syscall 量、重試 | 高：攻擊者還沒能動你的偵測 |
| **成功當下** | `commit_creds`、cred 改變、helper 被呼叫 | 中：快，但攻擊者可能立刻關偵測 |
| **成功之後** | 持久化、橫向移動、外連 | 取決於你是否在 **kernel 之外**（網路、hypervisor、外部 attestation）觀察 |

### 1. crash 遙測：你最大的優勢

kernel exploit 多為機率性（race、heap layout、`RANDOM_KMALLOC_CACHES` 的 1/16 命中），失敗的結果是 oops 或 panic。**一台機器在短時間內反覆出現 kernel oops、特別是來自同一個子系統，是極強的訊號**。下列字串都是 mitigation 觸發時的典型 kernel 訊息（格式隨版本有差異，請以你的 kernel 的實際 log 為準，未實測）：

| log 特徵 | 意義（連回章節） |
|---|---|
| `usercopy: Kernel memory exposure attempt detected` / `overwrite attempt detected` | HARDENED_USERCOPY 擋下越界複製（[Ch 26](./26-modern-mitigations.md)） |
| `CFI failure at ...` | KCFI 擋下型別不符的間接呼叫（[Ch 28](./28-cfi-kcfi.md)） |
| `refcount_t: ... use-after-free` / `saturated` | refcount 溢位或歸零後使用（[Ch 10](./10-vulnerability-taxonomy.md)） |
| `list_add corruption` / `list_del corruption`（需 `DEBUG_LIST`） | 鏈結串列被破壞（[Ch 18](./18-uaf-double-free.md)） |
| `BUG: unable to handle page fault` / `general protection fault` | 利用失敗最常見的收場（stack pivot 錯、位址猜錯） |
| `kernel BUG at mm/slub.c` / `Object already free`（`slub_debug`） | double free（Ch 18） |
| `BUG: KASAN: slab-use-after-free`（測試 kernel） | 開發期用，production 不會有 |

要讓這個訊號真的有用，要做到：

1. **log 在崩潰前外送**：`pstore`（`ramoops`）、`netconsole`、`kdump` 搭配 `panic_on_oops=1` + 自動重開機。本機磁碟上的 log 對成功的攻擊者不可信。
2. **重開機次數告警**：同一台在 N 分鐘內 reboot 超過閾值，不論原因，發告警。
3. **oops 內容分類**：看 `RIP` 落在哪個子系統（`nf_tables`、`io_uring`、`tty`……），你就知道攻擊者在試哪一章。

### 2. auditd：syscall 層的行為訊號

auditd 看不到 kernel 內部，但能看到**誰在什麼時候呼叫了哪些敏感 syscall**。以下規則是起點（**未實測**；`-F auid!=unset` 在較新的 audit userspace 有效，舊版用 `-F auid!=4294967295`）：

```bash
# 非 login 之外的 namespace / userns 操作
-a always,exit -F arch=b64 -S unshare,setns -F auid>=1000 -F auid!=unset -k userns
# 載入 / 卸載 module
-a always,exit -F arch=b64 -S init_module,finit_module,delete_module -k modules
# 低頻、但屬於本書攻擊面入口的 syscall
-a always,exit -F arch=b64 -S bpf -F auid>=1000 -F auid!=unset -k bpf
-a always,exit -F arch=b64 -S io_uring_setup -k iouring
-a always,exit -F arch=b64 -S keyctl,add_key -F auid>=1000 -F auid!=unset -k keyring
-a always,exit -F arch=b64 -S userfaultfd -k uffd
# modprobe 設定檔變更
-w /etc/modprobe.d/ -p wa -k modprobe_cfg
```

限制要誠實講：

- **量大**：`io_uring_setup`、`bpf` 在某些主機上是合法高頻；要先量 baseline 再設閾值，直接全開會淹沒你。
- **看不到參數的語意**：auditd 記錄 `a0..a3` 暫存器值，但 `msg_msg` spray 的「數量與大小」要自己從 `msgsnd` 的呼叫次數推。
- **檔案監控對 data-only 無效**：`-w /proc/sys/kernel/modprobe` **沒有用**。[Ch 16](./16-classic-primitives.md) 的攻擊是用任意寫**直接改 kernel 記憶體**，不經過 procfs 的寫入路徑，所以任何靠「監看某檔案被寫入」的偵測都看不到它。你得監控**值本身**或**使用它的那一刻**（見下節）。

### 3. eBPF runtime detection

eBPF 能在 kernel 內掛探針、低開銷地觀察 syscall 與 kernel 函式。成熟的專案：Falco（有 modern eBPF probe）、Tetragon（Cilium 專案，能在 kernel 內直接執行 enforcement，例如對特定事件送 SIGKILL）、Tracee（Aqua）。我們用 `bpftrace` 展示**偵測的邏輯**，三個概念示例（**未實測，語法請以你的 bpftrace 版本為準；後兩個需要 kernel BTF**）：

**(a) spray 行為偵測**：對 [Ch 19](./19-spray-objects.md) 的 `msg_msg` 噴灑，一般 process 不會在幾秒內發出幾千次 `msgsnd`。

```
bpftrace -e '
tracepoint:syscalls:sys_enter_msgsnd { @msgsnd[pid, comm] = count(); }
interval:s:5 { print(@msgsnd); clear(@msgsnd); }'
```

閾值要用你的 baseline 決定。同樣思路適用於 `add_key`（`user_key_payload`）、`pipe2`、`/dev/ptmx` 的 open（`tty_struct`）、`sendmsg` 帶大量 ancillary data。

**(b) 特權提升當下**：`commit_creds` 是 kernel 設定 cred 的出口，合法呼叫（`setuid`、`execve` 一個 setuid binary）也會經過，所以**單獨看它沒有意義，要和「剛剛有沒有 setuid exec」關聯**。

```
bpftrace -e 'kprobe:commit_creds { printf("commit_creds pid=%d comm=%s\n", pid, comm); }'
```

偵測規則的形狀：「process P 呼叫 `commit_creds` 把 euid 變成 0，**但**在這之前沒有看到 P 執行過 setuid binary」。注意 [Ch 24](./24-dirty-cred.md) 與直接改 `cred` 的做法**根本不呼叫 `commit_creds`**，這種偵測抓不到它們——這就是 data-only 的偵測盲區（[Ch 29](./29-data-only-attack.md)）。能抓這類的是比較 cred 值與預期值的完整性檢查（見下節 LKRG）。

**(c) usermode helper 路徑核對**：`modprobe_path` 攻擊最終會經過 `call_usermodehelper_exec`，在這一刻核對「要執行的路徑是不是預期值」，比輪詢 `/proc/sys/kernel/modprobe` 可靠（攻擊者可以在觸發前改、觸發後立刻改回，輪詢會錯過窗口，這正是 [Ch 16](./16-classic-primitives.md) 與 [Ch 45](./45-exploit-stabilization.md) 教的 cleanup）。

```
bpftrace -e 'kprobe:call_usermodehelper_exec {
  printf("umh pid=%d comm=%s path=%s\n", pid, comm,
         str(((struct subprocess_info *)arg0)->path)); }'
```

**eBPF 偵測的上限**：它跑在 kernel 裡，一個 ring 0 的攻擊者可以卸載它。所以 eBPF 偵測最適合抓「**成功之前與成功當下**」，並且要把事件**即時外送**到 kernel 之外。

### 4. 完整性：IMA、LKRG、外部驗證

| 機制 | 做什麼 | 能抓什麼 | 抓不到什麼 |
|---|---|---|---|
| **IMA**（Integrity Measurement Architecture） | 在 exec / mmap / module 載入時度量（hash）檔案，可延伸到 TPM PCR，可做 appraisal（簽章不符拒絕執行） | 未簽章或被改過的檔案被執行：例如 `modprobe_path` 被指到 `/tmp/x`，若 exec policy 要求 appraisal，這個未簽章的 `/tmp/x` 會被拒絕（**概念，需要完整 policy，未實測**；腳本經直譯器執行的情況要另外處理） | **記憶體內**的 kernel 資料被改（`cred`、PTE）；只用既有合法 binary 的利用 |
| **LKRG**（Linux Kernel Runtime Guard，Openwall，out-of-tree module） | 週期性與事件驅動地檢查 process 的 `cred`、namespace、關鍵 kernel 結構是否與它追蹤的值一致 | 直接改 `cred` 這類 data-only 提權的**偵測**（偵測，不是預防） | 它自己也在 ring 0，能被同級攻擊者繞過；與 KCFI 等編譯器保護的整合、相容性需評估 |
| **遠端證明**（remote attestation，TPM + IMA，例如 Keylime） | 把度量結果簽章送到外部驗證者 | 開機鏈與已載入檔案被改 | 執行期的記憶體破壞 |
| **hypervisor / 外部觀察** | 從 VM 之外讀 guest 記憶體或監看行為 | 唯一不依賴 guest kernel 可信度的選項 | 部署成本高，Linux 上沒有主流的現成方案（就我所知） |

這張表說明一個設計結論：**data-only exploit 讓「檔案完整性」類的防禦失效，要抓只能靠「值」的完整性檢查或外部觀察**。這也是為什麼 [Ch 26](./26-modern-mitigations.md) 結尾說 data-only 這一層，production 沒有通用 mitigation。

## grsecurity / PaX 的歷史與影響

為什麼本章要專門講一段歷史？因為你今天在 KSPP 清單上看到的大部分項目，都能在 PaX / grsecurity 找到祖先。理解這條血緣，你就理解了「mainline 為什麼這樣長」。

**背景**：PaX（PaX Team，2000 年起）與 grsecurity（Brad Spengler，2001 年起）是長期維護的 out-of-tree Linux 安全 patch 組。它們在 mainline 開始認真做 kernel 自我保護之前十多年，就把許多想法做出來了。

下表是「PaX/grsecurity 的概念」與「今天 mainline 的對應物」，**對應是概念層級的血緣，不代表是同一份程式碼；各項引入版本是我們的記憶，請以 KSPP 的 Feature Status 表驗證**：

| PaX / grsecurity | 概念 | mainline 的後代（就我所知） | 本書章節 |
|---|---|---|---|
| `KERNEXEC` / `UDEREF` | kernel 不執行 / 不隨意存取 user 記憶體 | SMEP / SMAP 的軟體前身思想；`STRICT_KERNEL_RWX` | [Ch 13](./13-smep-smap.md) |
| `PAX_USERCOPY` | 檢查 copy 範圍不超出 object | `HARDENED_USERCOPY`（4.8） | Ch 26 |
| `PAX_MEMORY_SANITIZE` | 釋放時清零 | `init_on_free`、slab poisoning（5.3） | Ch 26 |
| `PAX_REFCOUNT` | reference count 溢位保護 | `refcount_t`（4.11 起逐步導入） | [Ch 10](./10-vulnerability-taxonomy.md) |
| `PAX_MEMORY_STACKLEAK` | 離開 syscall 時清 stack | `GCC_PLUGIN_STACKLEAK`（4.20） | Ch 14 |
| `PAX_RANDKSTACK` | 每次 syscall 隨機 kernel stack 偏移 | `RANDOMIZE_KSTACK_OFFSET`（5.13） | Ch 11 |
| `RANDSTRUCT` | struct 欄位順序隨機化 | `RANDSTRUCT`（4.13） | [Ch 19](./19-spray-objects.md) |
| `CONSTIFY` | 把函式指標表標成唯讀 | `__ro_after_init` 與 `const ops` 的廣泛使用 | [Ch 21](./21-heap-to-rip.md)（ops table 劫持） |
| **`RAP`** | forward / backward-edge 控制流保護 | 概念上對應 KCFI、IBT、shadow stack 的組合 | [Ch 28](./28-cfi-kcfi.md) |

**影響**有三條，我們認為每一條都值得記：

1. **想法先在外面證明可行**：PaX 證明了「這些防禦可以在真實 kernel 上跑，成本可接受」。這降低了上游採用的心理與技術門檻。
2. **上游化是翻譯，不是複製**：mainline 的維護者要求可維護、可分階段、不破壞相容性，所以同一個概念常以更保守、更模組化的形式落地，有些項目（FGKASLR、SLAB_VIRTUAL，見 [Ch 26](./26-modern-mitigations.md)）至今還在討論。
3. **組織上的轉折**：KSPP 在 2015 年由 Kees Cook 等人發起，目標就是把這類防禦系統性地推進 mainline。大約同一時期，grsecurity 的公開 stable patch 不再免費公開（就我所知是 2015 年，之後 test patch 的公開也在 2017 年結束；時間細節請以一手來源為準）。我們不評論各方立場，只記錄這個時序：**從「一包外部 patch」變成「mainline 逐項吸收」，這是 2015 之後 Linux kernel 防禦演化的主軸**。

## 攻擊 → 防禦對照：逐 Part 把書收回來

這是本章的核心，也是我們認為「攻擊者讀完這本書，防禦者從每一章學到的」最直接的表達。**一格一格看：每一列說「攻擊者學到的技術」與「防禦者能拿來做的事」**。

### Part 0–1：環境、記憶體與 allocator

| 章 | 攻擊者的技術 | 防禦者能做什麼 |
|---|---|---|
| [Ch 0](./00-environment-setup.md) QEMU + gdb | 在可控環境重現與調試 | 用同樣環境跑**防禦版 kernel**（開 KASAN、`slub_debug`、全部 mitigation）做發布前測試 |
| [Ch 4](./04-syscall-boundary.md) uaccess | `copy_{from,to}_user` 的長度與 TOCTOU | `HARDENED_USERCOPY`；`__user` 註記搭配 sparse / Coccinelle / smatch 靜態分析；code review 的 checklist：長度、整數溢位、double-fetch |
| [Ch 5](./05-first-vulnerable-module.md) vulnerable module | 自建靶 | 審查 out-of-tree driver：簽章、`MODULE_SIG_FORCE`、lockdown；對廠商 driver 做 fuzz |
| [Ch 6](./06-kernel-memory-layout.md) 記憶體布局 | 找 physmap、vmalloc 位址 | `RANDOMIZE_MEMORY`、KASLR；避免位址進 log（`%p` 預設雜湊化，4.15 起，就我所知）|
| [Ch 7](./07-buddy-allocator.md) buddy | page 層 grooming | `SHUFFLE_PAGE_ALLOCATOR` |
| [Ch 8–9](./08-slub-allocator.md) SLUB | freelist、cache 布局、spray | `SLAB_FREELIST_*`、`slab_nomerge`、`RANDOM_KMALLOC_CACHES`；測試環境的 `slub_debug` |

### Part 2：漏洞類型學與經典利用

| 章 | 攻擊者的技術 | 防禦者能做什麼 |
|---|---|---|
| [Ch 10](./10-vulnerability-taxonomy.md) 類型學 | 辨識可利用性高的 bug 類型 | 把類型當優先級：UAF / OOB write / type confusion 高優先修；用 `refcount_t`、`DEBUG_LIST`、KMSAN 在開發期抓；新 driver 評估以記憶體安全語言撰寫（Rust 支援在 6.1 進 mainline，就我所知）|
| [Ch 11](./11-stack-overflow.md) stack overflow | 蓋 return address | `STACKPROTECTOR_STRONG`、`VMAP_STACK`、`STACKLEAK`、`RANDOMIZE_KSTACK_OFFSET` |
| [Ch 12](./12-control-flow-hijack.md) 劫 RIP | `commit_creds(prepare_kernel_cred(0))` | KCFI / IBT；**偵測**：`commit_creds` 與 setuid exec 的關聯（上文 3(b)） |
| [Ch 13](./13-smep-smap.md) SMEP/SMAP | 繞過、ROP、pivot | **不要用 `nosmep`/`nosmap` 開機**；kernel 對 CR0/CR4 的敏感位元做 pinning（5.3 起，就我所知），改它們會被還原 |
| [Ch 14](./14-kaslr-infoleak.md) leak | KASLR 繞過、percpu、uninit | `kptr_restrict`、`dmesg_restrict`、`perf_event_paranoid`、`INIT_ON_*`、KMSAN、oops 不對低權限開放 |
| [Ch 15](./15-kpti.md) KPTI | trampoline、signal return | 保持 PTI 開（不用 `pti=off`）；它同時也是 Meltdown 的緩解 |
| [Ch 16](./16-classic-primitives.md) 經典原語 | `modprobe_path`、`core_pattern`、`poweroff_cmd`、cred | `STATIC_USERMODEHELPER`；上文 3(c) 在 `call_usermodehelper_exec` 核對路徑；IMA exec appraisal；注意：**檔案監控看不到記憶體寫入** |

### Part 3：heap 利用

| 章 | 攻擊者的技術 | 防禦者能做什麼 |
|---|---|---|
| [Ch 17](./17-heap-overflow.md) heap overflow | 相鄰物件覆寫 | `FORTIFY_SOURCE`、`HARDENED_USERCOPY`、KASAN 於 CI、`RANDOM_KMALLOC_CACHES` |
| [Ch 18](./18-uaf-double-free.md) UAF / double free | freelist 覆寫、poisoning | `SLAB_FREELIST_HARDENED`、`DEBUG_LIST`、`refcount_t`、`INIT_ON_FREE`；崩潰訊息分類（上文 crash 遙測表） |
| [Ch 19](./19-spray-objects.md) spray 物件 | `msg_msg`、`pipe_buffer`、`tty_struct`、`user_key_payload` | seccomp 拒絕你不需要的 syscall（`keyctl`、SysV msg 在容器可評估關閉）；偵測 spray 量（上文 3(a)）；`RANDSTRUCT` |
| [Ch 20](./20-heap-grooming.md) grooming | 精確控制 slab 布局 | `RANDOM_KMALLOC_CACHES`、`SHUFFLE_PAGE_ALLOCATOR`；grooming 的噪音本身是偵測訊號 |
| [Ch 21](./21-heap-to-rip.md) heap → RIP | ops table 劫持 | KCFI、`const` ops 表（唯讀）、IBT |
| [Ch 22](./22-cross-cache.md) cross-cache | 跨 cache 重用 page | `slab_nomerge`；SLAB_VIRTUAL（未進 mainline）；降低攻擊面（少讓非特權使用者碰到 victim cache） |
| [Ch 23](./23-dirty-pagetable.md) Dirty Pagetable | 覆寫 PTE | **production 無通用 mitigation**；靠前面各層擋住進入點，與 crash / 行為偵測 |
| [Ch 24](./24-dirty-cred.md) Dirty Cred | 換 `cred` / `file` | 同上；LKRG 類完整性檢查是少數可偵測的方向；研究提案是把 cred/file 放進隔離的 slab（概念上與 SLAB_VIRTUAL 同方向，未進 mainline，就我所知） |
| [Ch 25](./25-usma-page-spray.md) USMA / page spray | 把 kernel page 映射進 userspace | `STRICT_DEVMEM`、限制 `AF_PACKET`（`CAP_NET_RAW`，經 userns 可得，回到 [Ch 36](./36-user-namespace.md)） |

### Part 4–6：mitigation、子系統、並發

| 章 | 攻擊者的技術 | 防禦者能做什麼 |
|---|---|---|
| [Ch 26–28](./26-modern-mitigations.md) | 繞過 random caches、KCFI | 這就是防禦者自己的武器庫；重點是**知道哪些你的 kernel 沒開**（用 kernel-hardening-checker 掃） |
| [Ch 29](./29-data-only-attack.md) data-only | `pipe_buffer`、cred、合法 target | 縮小攻擊面（讓進入點不可達）、LSM（SELinux / AppArmor）限制 root 之外的資源、外部觀察；承認這一層是目前的缺口 |
| [Ch 30](./30-netfilter-nftables.md) nf_tables | netlink + `CAP_NET_ADMIN` | 限制 unprivileged userns；不用就黑名單 module；快速套 stable patch |
| [Ch 31](./31-io-uring.md) io_uring | SQE/CQE UAF | `io_uring_disabled`、seccomp；Google 的作法見上文 |
| [Ch 32](./32-ebpf.md) eBPF | verifier bypass、JIT | `unprivileged_bpf_disabled=1`、`bpf_jit_harden=2`、`BPF_UNPRIV_DEFAULT_OFF` |
| [Ch 33](./33-socket-skb.md) socket / skb | `AF_PACKET`、`AF_UNIX`、pgv | 不用的 protocol family 黑名單；userns 限制 |
| [Ch 34](./34-filesystem-attack-surface.md) 檔案系統 | image parsing | 不自動掛載不受信任的 image；停用冷門 fs；FUSE 與 loop mount 的權限控管 |
| [Ch 35](./35-tty-keyring-watchqueue.md) TTY / keyring | 經典 spray 與原語 | `dev.tty.ldisc_autoload=0`；seccomp 擋 `keyctl`；`CONFIG_WATCH_QUEUE` 不需要就關 |
| [Ch 36](./36-user-namespace.md) userns | 取得 `CAP_NET_ADMIN` | 上文「關 user namespace 的實務」 |
| [Ch 37](./37-other-subsystems.md) / [38](./38-android-vendor-subsystems.md) | ksmbd、DRM、USB、binder、廠商 driver | 不用就 `CONFIG_*=n`；USB 授權（`authorized_default`、USBGuard）；審查廠商 driver 的 ioctl；Android 的 SELinux + seccomp 分層 |
| [Ch 39–41](./39-concurrency-model.md) 並發 | race、`userfaultfd`、FUSE 拉長 window | `vm.unprivileged_userfaultfd=0`；KCSAN / lockdep 於 CI；race 型利用常伴隨高頻重試與崩潰，納入 crash 遙測 |

### Part 7–9：工具鏈、跨平台、實戰

| 章 | 攻擊者的技術 | 防禦者能做什麼 |
|---|---|---|
| [Ch 42](./42-patch-to-rootcause.md) patch → root cause | 從 stable patch 反推 1-day | **patch gap 是最大風險**：定 SLA（stable 版本多快上線）；用 `livepatch` 縮短暴露；把 stable 的 CVE 公告當作攻擊者也在看的清單 |
| [Ch 43](./43-syzkaller.md) syzkaller | 找洞 | 自己跑 syzkaller 對你的 config 與廠商 driver；訂閱 syzbot 看 mainline 的 open bug |
| [Ch 44](./44-debug-toolchain.md) debug 工具鏈 | KASAN / KCSAN / KMSAN | 同一套工具在 CI 與預發布 kernel 全開 |
| [Ch 45](./45-exploit-stabilization.md) 穩定化 | race retry、避免 panic、cleanup | 反向利用：`panic_on_oops=1` + 自動重開 + **重開機次數告警**；攻擊者追求「不 crash」本身說明 crash 是你最好的訊號 |
| [Ch 46](./46-arm64-kernel-pwn.md) ARM64 | PAC / MTE / BTI | 在 arm64 上啟用 PAC、BTI、MTE（硬體支援時）；Android GKI 的 KCFI + SCS |
| [Ch 47](./47-container-vm-escape.md) 容器逃逸 | host kernel 攻擊面 | 不用 `--privileged`、drop capabilities、seccomp、rootless；高風險多租戶用 gVisor / Kata；host kernel 是共享 TCB |
| [Ch 48](./48-other-architectures.md) 其他架構 | 嵌入式缺少的 mitigation | 嵌入式 config 常缺 KASLR、hardened usercopy 等；用 kernel-hardening-checker 掃自家 BSP |
| [Ch 49](./49-kernelctf-overview.md)–[50](./50-patch-to-exploit.md) | kernelCTF、N-day | 把 kernelCTF 公開 writeup 當威脅情報：新技術出現時，對自己的 kernel 做同樣的對照表 |

## 對比與取捨

| 層次 | 手段 | 成本 | 效益 | 對 data-only 的效果 |
|---|---|---|---|---|
| 縮小攻擊面 | 關 userns / io_uring / bpf、移除 module、seccomp | 低到中（可能弄壞功能） | **最高**（直接不可達） | 有效：進不去就沒有後面 |
| mitigation | KSPP config、KCFI、random caches | 低到中（效能、重編） | 中（提高成本） | 多數無效（Ch 29） |
| 及時 patch | stable 追蹤、livepatch | 中（流程成本） | 高（直接修 bug） | 有效 |
| 開發期偵測 | KASAN / KMSAN / syzkaller | 中（CI 資源） | 高（在出貨前抓） | 有效 |
| crash / 行為偵測 | 遙測、auditd、eBPF | 中（誤報管理） | 中（抓「成功前」） | 部分 |
| 完整性 / 外部驗證 | IMA、LKRG、attestation、hypervisor | 中到高 | 中（抓「成功後」） | LKRG 類有效；IMA 無效 |

## 踩雷集錦

1. **「SELinux / AppArmor / seccomp 就是 kernel exploit 的防線」**：它們限制「程序能做什麼」，不修 kernel bug。kernel exploit 在**被允許的 syscall 內**觸發 bug，一旦任意寫成立就在 ring 0，這些都可被關閉。它們的價值在縮小攻擊面（讓某些 syscall 碰不到），不在阻止已成立的 exploit。
2. **「偵測軟體裝了就能在被攻破時示警」**：ring 0 攻擊者可以卸載你的 eBPF 探針、改 audit 規則。偵測要設計成「失敗時就有訊號」與「事件即時外送」，而不是「事後問本機」。
3. **「關 userns 就萬無一失」**：它只移除「非特權使用者取得 `CAP_NET_ADMIN`」這條放大路徑，不修 bug；而且會弄壞 rootless container 與瀏覽器 sandbox。有些 bug 不需要 userns（例如 [Ch 31](./31-io-uring.md) 的 io_uring 本身對一般使用者可達）。
4. **「KSPP 全開就是安全」**：有成本（`init_on_free`、`DEBUG_LIST`、`RANDSTRUCT` 與 out-of-tree module 的相容性）、有衝突（lockdown 與某些除錯工具）。**先量 baseline、分批上、記錄每項的實際代價**。
5. **「CVSS 高 = 對我致命」**：看**可達性**：這個 bug 需要什麼 config、什麼權限、哪個 namespace？你的 kernel 沒編那個子系統，或使用者碰不到，風險就是 0。反過來，一個 CVSS 中等、但在你環境**可被一般使用者直接觸發**的 bug 比較危險。
6. **「檔案完整性監控能抓 `modprobe_path`」**：不能。[Ch 16](./16-classic-primitives.md) 的攻擊用記憶體任意寫，不經任何檔案。監控**使用時刻**（`call_usermodehelper_exec`）或**值本身**，並注意 cleanup 會把值改回。
7. **「更新 kernel 太麻煩，先靠 mitigation」**：mitigation 擋的是**未知**利用路徑的某些步驟，對已公開的 1-day（Ch 42、Ch 50）常常沒用——公開 exploit 已經針對常見 config 調好。patch 是最便宜的防禦。

## 進階：再往深一層

### 以工作負載做威脅模型

同一份 kernel，不同角色的最佳設定不一樣：

| 工作負載 | 主要威脅 | 優先順序（我們的觀點） |
|---|---|---|
| 桌機 / 工作站 | 瀏覽器 / 應用 → sandbox 逃逸 → kernel | 保留 userns（瀏覽器需要）、KSPP 低成本項、及時更新 |
| 伺服器（單租戶） | 遠端服務被打 → 本機提權 | 縮小攻擊面（關不用的 module / sysctl）、patch SLA、crash 遙測 |
| 容器主機（多租戶） | 租戶容器 → host kernel | seccomp、禁 `--privileged`、考慮 gVisor / Kata；host kernel 是共享 TCB，用刪除攻擊面為主 |
| Android / 嵌入式 | 惡意 app / 廠商 driver | GKI + KCFI + SCS + MTE、SELinux + seccomp；審 driver |

### 用工具量化，而不是用感覺

`kernel-hardening-checker`（Alexander Popov 維護，前身 `kconfig-hardened-check`）會讀你的 `/proc/config.gz`、cmdline、sysctl，與 KSPP 等來源的建議比對，列出每一項是 OK 還是 FAIL。它的價值是**可重複、可 diff、可放進 CI**，讓「我們的 kernel 比上個版本更硬還是更鬆」成為可量化的問題。

### 把書的方法論用在防禦上

[Ch 42](./42-patch-to-rootcause.md) 教的「從 patch diff 反推 root cause」，防禦者可以做同一件事：每個 stable 更新讀 commit，判斷**這個 bug 在我的 config 與權限模型下是否可達、可被利用到什麼程度**，據此排序更新的緊急度，而不是被動等 CVE 分數。

### 硬體路線

arm64 的 MTE（Memory Tagging Extension）可把 slab 的 UAF / OOB 變成確定性（硬體）偵測，見 [Ch 46](./46-arm64-kernel-pwn.md)；Intel CET 的 shadow stack 在 x86 的 **user space** 已有支援（6.6，就我所知），kernel 自己的 shadow stack 尚未進 mainline（就我所知，請查證）。這些硬體機制若落地，會改變 [Ch 26](./26-modern-mitigations.md) 那張表上「機率型 vs 決定型」的比例。

## 動手練習

> 全部只在你自己擁有的 VM / 機器操作。未標「實測」者為理論預期行為，需你在目標環境驗證。

1. **盤點**（實測於 WSL2）：
   ```bash
   zcat /proc/config.gz | grep -E 'RANDOM_KMALLOC|CFI|INIT_ON|HARDENED_USERCOPY|STATIC_USERMODEHELPER|LOCKDOWN|IBT|SLAB_MERGE|FREELIST'
   for k in kernel/unprivileged_bpf_disabled kernel/io_uring_disabled kernel/kptr_restrict \
            kernel/dmesg_restrict user/max_user_namespaces vm/unprivileged_userfaultfd; do
     printf '%s = ' $k; cat /proc/sys/$k; done
   ```
   把輸出與本章的樣本比對。
2. **用工具掃**：安裝 `kernel-hardening-checker`，對你的 kernel 跑一次，把 FAIL 清單依「刪除攻擊面 > 低成本 mitigation > 高成本」排序，寫下每一項為什麼開 / 不開。
3. **設計你自己的 sysctl drop-in**：從本章起點改成你的 workload 版本，並在 `sysctl --system` 前後用 `sysctl -a` diff，記錄哪個服務壞掉（若有）。
4. **偵測 spray**：在測試 VM 跑 [Ch 19](./19-spray-objects.md) 的 `msg_msg` spray 程式，同時跑本章 3(a) 的 bpftrace，記錄正常工作負載與 spray 的 `msgsnd` 次數差異，訂出你的閾值。
5. **偵測 helper 路徑**：跑本章 3(c)，再在你自己的測試 VM 重現 [Ch 16](./16-classic-primitives.md) 的 `modprobe_path` 流程（靶機，不是別人的機器），確認 `call_usermodehelper_exec` 的探針看得到被改過的路徑；再試著「觸發後改回」，比對輪詢 `/proc/sys/kernel/modprobe` 與 kprobe 哪個抓得到。
6. **防禦矩陣**：選一個你熟悉的 CVE（建議 [Ch 50](./50-patch-to-exploit.md) 的 N-day），依「第一關縮小攻擊面 / 第二關 mitigation / 第三關偵測」填一張表：哪些設定會讓這個 exploit 失敗、在哪一步失敗、失敗時會留下什麼 log。

## 本章重點整理

- 防禦三關：**縮小攻擊面**（刪除優於緩解）、**破壞利用**（KSPP、KCFI、random caches，提高成本不是封死）、**偵測**（成功前最有價值，成功後必須在 kernel 外觀察）。
- 偵測要設計成「失敗就有訊號」：kernel exploit 多為機率性，oops / panic 與重開機次數是最強訊號；檔案監控對 data-only 無效，要監控值或使用時刻。
- Lockdown 擋的是「root → ring 0」，不是「非特權 → ring 0」；SELinux / seccomp 縮小攻擊面，不阻止已成立的 exploit。
- data-only 是目前的缺口：production 沒有通用 mitigation，只能靠縮小攻擊面、及時 patch、完整性檢查與外部觀察。
- grsecurity / PaX 是今天 KSPP 清單的祖先；理解血緣就理解 mainline 為什麼是現在的樣子。

## 自我檢核

- [ ] 不看筆記，能否畫出攻擊鏈並在每一格標上防禦者的對應動作（縮小面 / mitigation / 偵測）？
- [ ] 能解釋為什麼「監看 `/proc/sys/kernel/modprobe` 被寫入」抓不到 `modprobe_path` 攻擊，以及該改監控什麼嗎？
- [ ] 面試被問「Lockdown 能擋 kernel exploit 嗎」，你能說清楚它的信任邊界與擋不到的情況嗎？
- [ ] 能說出至少三個「縮小攻擊面」的旋鈕、各自的代價，以及它們對應本書哪一章的攻擊嗎？
- [ ] 想一個你負責的系統：它屬於哪種工作負載？依本章的觀點，你的前三項行動是什麼？為什麼是這個順序？
- [ ] 能說出 PaX/grsecurity 的至少四個概念在 mainline 的後代，並指出哪些就我們查證還沒進 mainline 嗎？
- [ ] 如果攻擊者已在 ring 0，你的偵測還剩什麼可信度？你的設計怎麼處理這件事？

## 延伸閱讀

### 專案與清單

- **[Kernel Self Protection Project](https://kernsec.org/wiki/index.php/Kernel_Self_Protection_Project)**
  - **讀哪裡**：「Recommended Settings」頁（config / cmdline / sysctl 建議）與「Feature Status」表（各項進入的 kernel 版本）。
  - **為什麼值得讀**：本章 hardening 清單的主要依據，也是驗證本章所有「就我所知」版本號的第一站。前提：本章與 [Ch 26](./26-modern-mitigations.md)。
- **[kernel-hardening-checker](https://github.com/a13xp0p0v/kernel-hardening-checker)** — Alexander Popov
  - **這篇說什麼**：把 KSPP 等來源的建議編成可執行的檢查，輸出你的 kernel 逐項 OK / FAIL。
  - **讀哪裡**：README 的 usage 與輸出範例，再讀它的設定表如何對應各項 mitigation 的版本與架構。
  - **和本章的關聯**：動手練習 2；它的表格是「mitigation 版本與依賴」的活文件。

### 部落格 / 技術文章

- **[Learnings from kCTF VRP's 42 Linux kernel exploits submissions](https://security.googleblog.com/2023/06/learnings-from-kctf-vrps-42-linux.html)** — Google Security Blog（2023-06）
  - **這篇說什麼**：從 42 份 kCTF VRP submission 看哪些攻擊面與技術最常被用、哪些 mitigation 有用、Google 因此做了什麼決策（含停用 io_uring）。
  - **讀哪裡**：mitigation 效果的統計段與「我們做了什麼」的段落。
  - **為什麼值得讀**：這是防禦者以攻擊資料驅動決策的典範，直接對應本章「以刪除攻擊面為先」的觀點。前提：[Ch 49](./49-kernelctf-overview.md) 對 kernelCTF / kCTF 的背景。
- **[Linux Kernel Defence Map](https://github.com/a13xp0p0v/linux-kernel-defence-map)** — Alexander Popov
  - **這篇說什麼**：把「攻擊技術 / 漏洞類型」與「防禦機制」做成對照圖與表，並標出各機制的版本。
  - **讀哪裡**：主圖，以及它對應的 CONFIG 與 sysctl 清單。
  - **和本章的關聯**：本章「逐 Part 對照」一節的同類型參考，讀完可以拿來交叉檢查我們的對照有沒有遺漏。

### 官方文件

- **`kernel_lockdown(7)` man page**（<https://man7.org/linux/man-pages/man7/kernel_lockdown.7.html>）
  - **讀哪裡**：兩個模式各自阻擋的功能清單，以及啟用方式（`lockdown=`、`/sys/kernel/security/lockdown`）。
  - **注意**：清單隨版本演進；以你 kernel 版本的行為為準。
- **Linux 原始碼 `Documentation/admin-guide/sysctl/kernel.rst`**（[Bootlin Elixir](https://elixir.bootlin.com/linux/v6.6/source/Documentation/admin-guide/sysctl/kernel.rst)）
  - **讀哪裡**：`io_uring_disabled`、`unprivileged_bpf_disabled`、`kptr_restrict`、`perf_event_paranoid` 各項的值域與語意。
  - **和本章的關聯**：本章 sysctl 表的權威來源；我們標「請以文件為準」的地方都在這裡驗證。前提：[Ch 2](./02-reading-kernel-source.md)。

### 書籍

- **《A Guide to Kernel Exploitation: Attacking the Core》** — Enrico Perla & Massimiliano Oldani（Syngress, 2010）
  - **這本書的定位**：攻擊者框架的經典；2.6 時代的內容已過時，但「從威脅模型出發」的思考方式，對防禦者擬威脅模型仍然適用。
  - **讀哪幾章**：威脅模型與利用方法論的前幾章；具體技術可跳過。

全書到這裡走完：從 QEMU 開機、讀原始碼、SLUB、漏洞類型、經典與現代利用、子系統、並發、工具鏈，到防禦與偵測。最後一步是把這些整合起來：選一個已公開的 kernelCTF submission，從 patch 與 writeup 出發重建它，並用本章的防禦矩陣寫出「在哪個設定下它會失敗」。

→ [Final Project：重建一個已公開 kernelCTF submission](./final-project-kernelctf-rebuild.md)
