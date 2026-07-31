# Ch 1 — Kernel 全貌：monolithic 設計與怎麼讀源碼

> **目標**：在鑽進任何子系統之前，先建立對「Linux kernel 這個東西」的整體地圖——它是什麼、跑在哪、和使用者空間的邊界在哪、源碼樹怎麼佈局、以及最重要的：面對三千多萬行程式碼，**怎麼找到並讀懂你要的那一段**。這章不教任何一個子系統，教的是後面 52 章都要用到的「導航能力」。

## 為什麼需要這個？

你已經在 Ch 0 build 出一顆 kernel、用 gdb 停在 `start_kernel`。但你手上握著的是一個**三千多萬行 C 程式碼**的東西——比大多數人一輩子讀過的所有程式碼加起來還多。如果你像讀一本書那樣從第一頁讀起，你會在 `init/main.c` 就迷路，永遠讀不到排程器。

kernel 的學習障礙從來不是「單一概念太難」，而是**尺度**：它太大、子系統太多、彼此交纏。所以在學任何具體機制之前，你需要兩樣東西：

1. **一張地圖**——kernel 分成哪些子系統、各自住在源碼樹的哪裡、大致負責什麼。這樣讀到某段程式碼時，你知道自己站在整體的哪個位置。
2. **一套導航法**——怎麼從「我想知道 `read()` 怎麼運作」出發，實際在源碼裡追到答案。這是本課反覆要用的核心技能：**跟著一條執行路徑往下讀**，而不是從上往下讀整棵樹。

這章給你這兩樣。它是後面所有章的前置——每一章都假設你會用 Bootlin 跳轉、會跟著一個函式往下追。

## 先建立直覺：kernel 是一個跑在特權層的大程式

先破除一個常見的模糊印象。kernel 不是「作業系統背景服務」那種一堆 process 的集合，它是**一個單一的、巨大的程式**，開機時被載入記憶體、跑在 CPU 的**最高特權層**，然後常駐到關機。

```
   ┌─────────────────────────────────────────────────────────────┐
   │  使用者空間 (user space) — CPU ring 3 / EL0，受限特權         │
   │                                                               │
   │   bash    firefox   nginx    你的程式  ...  各自獨立位址空間  │
   │     │        │        │         │                             │
   │     └────────┴────────┴─────────┘                             │
   │                  │  唯一的門：syscall (Ch 4)                   │
   │  ════════════════▼══════════════════════ 特權邊界 ══════════ │
   │                                                               │
   │  核心空間 (kernel space) — CPU ring 0 / EL1，全特權            │
   │                                                               │
   │   ┌─────────────────────────────────────────────────────┐   │
   │   │  一個大程式：Linux kernel                            │   │
   │   │  排程器 · 記憶體管理 · VFS · 網路堆疊 · 驅動 · ...    │   │
   │   │  跑在所有 CPU 上，共享一個核心位址空間                │   │
   │   └─────────────────────────────────────────────────────┘   │
   │                          │                                    │
   │  ════════════════════════▼═══════════════════════════════    │
   │              硬體：CPU · RAM · 磁碟 · 網卡 · ...               │
   └─────────────────────────────────────────────────────────────┘
```

幾個關鍵事實：

- **特權分層**：CPU 有特權等級（x86 的 ring 0/3、ARM64 的 EL1/EL0）。使用者程式跑在低特權（ring 3 / EL0），不能直接碰硬體、不能存取核心記憶體。kernel 跑在高特權（ring 0 / EL1），能做一切。這條界線由硬體強制。
- **唯一的門是 syscall**：使用者程式想要 kernel 做事（開檔、配記憶體、送封包），只能透過 syscall（Ch 4）跨越這條邊界——像進一棟高安全大樓只有一個檢查哨。這是 kernel 控制一切的根本：所有請求都得經過它的門。
- **kernel 沒有自己的 process**：kernel 不是一個在背景跑的 process。它是**寄生在每個 process 上執行**的——當你的程式呼叫 `read()`，CPU 從 ring 3 切到 ring 0，開始執行 kernel 的 `read` 程式碼，**還是同一個執行流、同一個 task**，只是換了特權層和 stack（Ch 2 詳述）。加上少數純核心執行緒（kthread，Ch 3/10）和中斷處理（Ch 29），構成 kernel 全部的執行時機。

> 你在 `linux_commands` 課看過的 `ps`、`/proc`、fd、權限，全都是站在 ring 3 這一側、透過那道門看到的**投影**。這門課做的事，是走到門的**另一側**，看那些投影背後的實體——`task_struct`（Ch 9）、VFS 物件（Ch 33）、page table（Ch 16）。

## monolithic 設計：一個地址空間裡的一切

作業系統核心有兩種經典架構，Linux 選了其中一種，這個選擇影響你讀到的每一段程式碼。

- **微核心（microkernel）**：核心只留最小的東西（排程、IPC、基本記憶體），把檔案系統、驅動、網路堆疊都放到**使用者空間的獨立 process**，彼此用訊息傳遞溝通。優點：隔離好，一個驅動崩了不會拖垮核心；缺點：每次跨元件都要訊息傳遞 + context switch，慢。代表：Minix、QNX、L4、（部分的）macOS XNU。
- **單體核心（monolithic kernel）**：檔案系統、驅動、網路堆疊**全部編進同一個核心、跑在同一個核心地址空間、同一特權層**，彼此直接函式呼叫。優點：快（直接呼叫，沒有訊息傳遞開銷）；缺點：一個驅動的 bug 能寫壞整個核心（沒有隔離）。**Linux 是單體核心。**

這不只是歷史八卦——它直接決定你讀源碼時看到什麼。因為是單體的：

- **各子系統直接呼叫彼此的函式**。VFS（Ch 33）直接呼叫檔案系統的 `read_iter`、檔案系統直接呼叫 block layer 的 `submit_bio`（Ch 36）——你能沿著函式呼叫一路追下去（這正是本課的讀法）。在微核心裡這些是跨 process 訊息，追不了。
- **一個空指標解參考就 panic 整台機器**（Ch 53）。沒有隔離牆。這是為什麼 kernel 的並行、記憶體、錯誤處理要這麼小心——你在 Part 4（同步）、Part 3（記憶體）學的謹慎，根源就在這裡。
- **模組（Ch 8）是「可動態載入的單體核心一部分」**：`insmod` 進來的 `.ko` 一樣跑在核心地址空間、同樣特權——它是單體核心的動態延伸，不是隔離的元件。這是為什麼 Ch 52 的 eBPF 才需要 verifier：因為核心裡跑的原生碼是**完全信任**的。

> **歷史插曲**：1992 年 Andrew Tanenbaum（Minix 作者）在 Usenet 上發文〈LINUX is obsolete〉，主張單體核心是「1970 年代的過時設計」，微核心才是未來。Torvalds 回擊，這場「Tanenbaum–Torvalds 論戰」是作業系統史上的著名對決。三十年後：單體的 Linux 跑在地球上絕大多數伺服器、手機、超級電腦上。務實（直接呼叫的效能、好寫驅動）贏過了理論上的優雅。但論戰的核心議題——隔離 vs 效能——今天以另一種形式回來了：eBPF（Ch 52）、虛擬化、以及把驅動搬進使用者空間的 VFIO/DPDK，都是在單體核心裡重新引入某種隔離。

## kernel 的尺度與源碼樹地圖

Linux 6.12 大約**三千五百萬行**程式碼。但別被嚇到——其中**約 70% 是驅動**（`drivers/`），你永遠不會全讀，也不需要。真正的核心邏輯集中在少數幾個目錄。這是源碼樹的地圖，也幾乎就是本課的目錄：

```
  linux/
  ├── init/         開機：start_kernel → init (Ch 3)
  ├── kernel/       核心中的核心
  │   ├── sched/      排程器 CFS/EEVDF (Ch 11-15)
  │   ├── locking/    spinlock/mutex/rwsem (Ch 25-26)
  │   ├── rcu/        RCU (Ch 27)
  │   ├── time/       timer/hrtimer (Ch 32)
  │   ├── irq/        中斷框架 (Ch 29)
  │   ├── bpf/        eBPF verifier/JIT (Ch 52)
  │   ├── cgroup/     cgroup (Ch 50)
  │   ├── fork.c      process 建立 (Ch 10)
  │   ├── kprobes.c   動態插樁 (Ch 51)
  │   └── ...         signal, module, seccomp (Ch 8/49) ...
  ├── mm/           記憶體管理 (Part 3, Ch 16-23)
  │                   page_alloc.c(buddy) slub.c memory.c(fault) vmscan.c(reclaim)
  ├── fs/           VFS 與各檔案系統 (Ch 33-35)
  │                   namei.c(路徑查找) libfs.c ramfs/ ext4/ proc/ ...
  ├── block/        block layer / blk-mq (Ch 36)
  ├── net/          網路堆疊 (Ch 43-46)
  │                   core/(skb,dev) ipv4/ netfilter/ sched/(qdisc) ...
  ├── drivers/      裝置驅動（全樹 ~70%）— base/(device model Ch 37) 等
  ├── security/     LSM / SELinux / AppArmor (Ch 48), commoncap.c(Ch 47)
  ├── ipc/          System V IPC
  ├── arch/         架構相關程式碼
  │   ├── x86/        本課主線
  │   └── arm64/      本課對照線
  ├── include/      標頭
  │   ├── linux/      核心內部 API（你會反覆開這裡）
  │   ├── uapi/       使用者空間看得到的 ABI（syscall 號、struct）
  │   └── asm-generic/ 架構無關的預設實作
  ├── lib/          核心用的資料結構/工具（rbtree, xarray Ch 5）
  ├── tools/        使用者空間工具（bpftool, perf, memory-model）
  └── Documentation/ 官方文件（很多子系統的權威說明在這）
```

記住幾個結構性規律，讀源碼會快很多：

- **`arch/<架構>/` 隔離架構相關程式碼**。同一個機制（context switch、page table、atomic）在 `kernel/sched/core.c` 有架構無關的框架，真正碰硬體的部分在 `arch/x86/` 和 `arch/arm64/`。本課的「x86 vs ARM64 對照」章（Ch 14/16/23）就是在對比這兩個目錄。
- **`include/linux/` vs `include/uapi/`**：前者是**核心內部**的型別與 API（`task_struct`、`file`），改了不影響使用者空間；後者是**使用者空間也看得到的 ABI**（syscall 號、`struct stat`），是神聖不可破壞的相容性契約——kernel 的鐵律「don't break userspace」守的就是 `uapi/`。
- **`Documentation/` 常有一手權威**：RCU、memory-barriers、locking、scheduler 各有設計者親筆寫的文件。遇到「這到底怎麼設計的」，先找 `Documentation/`，常比讀程式碼快。

## 怎麼讀 kernel 源碼（本課最重要的技能）

這是這章的核心，也是你會用一輩子的能力。面對三千萬行，有效的讀法只有一種：**別讀樹，追一條線**。

### 讀法一：跟著一條執行路徑往下追

不要問「排程子系統怎麼運作」（太大，讀不完），要問「一個 task 被搶佔時，`__schedule()` 之後發生什麼」（一條線，追得完）。挑一個**具體的入口函式**，跟著它的呼叫往下讀，只讀這條路徑上的東西，其他分支先跳過。本課每一章都是這樣組織的——Ch 34「一次 `read()` 的完整路徑」就是把 `read` syscall 從 `vfs_read` 一路追到磁碟，這是示範性的讀法。

一條 syscall 的典型追法（以 `read` 為例，Ch 34 會實作）：

```
  1. 在 include/uapi/asm/unistd*.h 或 syscall table 找 read 的號碼與入口
  2. grep "SYSCALL_DEFINE.*read"  →  fs/read_write.c 的 ksys_read/vfs_read
  3. vfs_read 呼叫 file->f_op->read_iter  →  這是多型（Ch 33），
     實際跑哪個看檔案系統；一般檔案走 generic_file_read_iter
  4. 往下到 mm/filemap.c 的 filemap_read（page cache，Ch 21）
  5. cache miss 就往 block layer（Ch 36）...
  沿途每個函式只讀「主線」，錯誤處理和邊角分支先略過
```

### 讀法二：善用工具跳轉，不要用眼睛 grep

- **[Bootlin Elixir](https://elixir.bootlin.com/linux/v6.12/source)**（本課的主力）：線上交叉索引。選 v6.12，任何函式/結構/巨集點一下就跳到定義、列出**所有呼叫點與引用點**。本課每章給的檔案路徑 + 函式名，配它可以邊讀邊跳。「這個函式被誰呼叫」「這個結構在哪裡被填」——Elixir 一鍵回答。
- **本機跳轉**：`make cscope` 或 `make tags`（ctags）產生索引，讓 vim/emacs 跳定義；VS Code 用 `scripts/clang-tools/gen_compile_commands.py`（Ch 0 提過）產生 `compile_commands.json` 給 clangd。沒有跳轉能力，讀 kernel 幾乎不可能。
- **`git log` / `git blame`**：想知道「這段程式碼為什麼這樣寫」，`git blame` 找到引入它的 commit，讀 commit message——kernel 的 commit message 品質極高，常常整段解釋設計理由。這是理解「為什麼」的金礦。

### 讀法三：認得 kernel 的慣用語（idiom）

kernel C 有一套自己的方言，第一次讀會卡，認得後讀速翻倍（Ch 5 深入，這裡先讓你有印象）：

- **`container_of(ptr, type, member)`**：從一個「嵌在結構裡的成員」的指標，回推整個結構的指標。kernel 到處在用（侵入式串列、各種 callback），是**讀 kernel 的鑰匙**——看不懂它，很多程式碼會像天書。Ch 5 逐行拆解。
- **`goto` 錯誤處理**：kernel 大量用 `goto err_xxx` 做清理階梯（配置了 A、B、C，中途失敗要反序釋放）。這在應用層是禁忌，在 kernel 是**標準且正確**的慣用法——因為沒有 RAII、沒有例外。
- **巨集重度使用**：`SYSCALL_DEFINE`（Ch 4）、`EXPORT_SYMBOL`（Ch 8）、`module_init`（Ch 0）、`list_for_each_entry`（Ch 5）——很多「函式」其實是巨集展開。Elixir 能幫你看巨集定義。
- **沒有 libc**（Ch 2）：沒有 `printf`（用 `printk`）、沒有 `malloc`（用 `kmalloc`，Ch 6）、不能用浮點、字串函式是 kernel 自己的 `include/linux/string.h`。

### 讀法四：接受你會略過大部分

專業的 kernel 讀法**不是把每行讀懂**，而是**知道哪些可以跳過**。第一遍追主線，錯誤處理、`#ifdef CONFIG_XXX` 的冷門分支、debug 程式碼、罕見架構的特化——先全部略過，抓到骨架再說。想「讀懂整個檔案」是初學者最大的時間陷阱。本課每章聚焦一條主線，就是在示範這種取捨。

## 這門課的地圖：52 章怎麼組織

有了源碼樹地圖，本課的結構就是它的一趟導覽路線（詳見 [README](./README.md)）：

- **Part 1（Ch 2-8）基礎設施**：先學在 kernel 裡寫程式碼的環境（context、syscall、資料結構、記憶體 API、模組）——是後面所有子系統的共同語言。
- **Part 2-6（Ch 9-36）核心子系統**：process/排程（`kernel/sched/`）、記憶體（`mm/`）、同步（`kernel/locking/`）、中斷與時間、VFS 與 block（`fs/` `block/`）——OS 教科書上的四大件，這裡讀真實實作。
- **Part 7-9（Ch 37-50）驅動、網路、容器**：device model 與驅動（`drivers/`）、網路堆疊（`net/`）、安全與容器底層（`security/` `kernel/cgroup/`）——把 kernel 連到真實硬體與現代雲原生。
- **Part 10（Ch 51-53）觀測與除錯**：kprobe/eBPF/ftrace/KASAN——回到 Ch 1 的問題「怎麼看穿一顆活的 kernel」，也是收尾的方法論。

每一章都遵循同一個節奏：**源碼讀懂設計 → 在 QEMU + gdb（Ch 0）裡停下來看它真的怎麼跑 → 動手改或寫模組驗證**。

## 動手練習

1. **樹一遍**：`git clone` 的源碼樹裡，`ls kernel/ mm/ fs/ net/`，對照上面的地圖，確認你認得每個主要目錄大致負責什麼。找出 `kernel/sched/`、`mm/page_alloc.c`、`fs/namei.c` 這幾個本課會反覆回來的檔案。
2. **Elixir 跳一次**：在 [Bootlin](https://elixir.bootlin.com/linux/v6.12/source) 搜 `struct task_struct`，跳到定義（`include/linux/sched.h`），再點 `comm` 欄位，看它在哪些地方被引用。這是 Ch 9 的主角，先混個臉熟。
3. **追一條線的起點**：`grep -rn "SYSCALL_DEFINE3(read" fs/`（或在 Elixir 搜 `SYSCALL_DEFINE3(read`）找到 `read` syscall 的定義，讀它前幾行呼叫了誰。你追到的就是 Ch 34 要走完的那條路的起點。
4. **讀一則 commit**：`git log --oneline kernel/sched/fair.c | head`，挑一個 commit `git show <hash>`，讀它的 message。感受 kernel commit message 的資訊密度——這是你以後理解「為什麼」的主要來源。

## 本章重點整理

- kernel 是**一個跑在最高特權層的單一大程式**，寄生在每個 process 上執行（syscall 進來）加上少數 kthread 與中斷；使用者空間只能透過 syscall 這道唯一的門和它互動。
- Linux 是**單體核心**：所有子系統編在一起、直接函式呼叫、共享核心地址空間——這帶來效能與可追蹤性，代價是零隔離（一個 bug 就 panic）。這個選擇解釋了本課後面對並行/記憶體/錯誤處理的所有謹慎。
- 源碼樹的核心邏輯集中在 `kernel/` `mm/` `fs/` `net/` `block/` `security/`，架構相關在 `arch/`，ABI 在 `include/uapi/`；`drivers/` 佔 70% 但你按需讀。
- 讀 kernel 的唯一有效方法是**跟著一條執行路徑往下追、用 Bootlin/cscope 跳轉、認得 container_of 等慣用語、坦然略過大部分**——這是本課每一章的讀法。

## 自我檢核

- [ ] 不看筆記，能解釋「kernel 沒有自己的 process、它寄生在每個 process 上執行」是什麼意思
- [ ] 能說出單體核心 vs 微核心的差別，以及 Linux 選單體對「你能怎麼讀源碼」的具體影響
- [ ] 給你一個子系統名（排程、記憶體、VFS、網路、同步），能說出它大致住在源碼樹哪個目錄
- [ ] 能說出「追一條執行路徑」為什麼比「從上讀整棵樹」有效，並能用 Bootlin 從一個函式跳到它的所有呼叫點
- [ ] 知道 `include/linux/` 和 `include/uapi/` 的差別，以及為什麼後者不能隨便改

## 延伸閱讀

### 官方文件

- **[Documentation/process/howto.rst](https://www.kernel.org/doc/html/latest/process/howto.html)**
  - **讀哪裡**：整篇，尤其「The development process」與參考書單兩節
  - **和本章的關聯**：kernel 社群官方寫給新人的入門地圖，補充本章沒展開的「怎麼參與開發」；讀完本課想發 patch 時再回來
- **[Documentation 首頁（kernel.org）](https://www.kernel.org/doc/html/latest/)**
  - **讀哪裡**：Core API、Subsystem 兩大類的目錄
  - **能學到什麼**：先掃一遍有哪些子系統文件，之後每章的延伸閱讀會指向具體篇章

### 工具

- **[Bootlin Elixir Cross Referencer](https://elixir.bootlin.com/linux/v6.12/source)**
  - **這是什麼**：本課全程的主力導航工具，v6.12 源碼交叉索引
  - **為什麼值得用**：本章「讀法二」的核心；沒有它，讀 kernel 的效率會差一個數量級

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love（Addison-Wesley, 2010）
  - **這本書的定位**：最好讀的 kernel 全貌入門；第 1 章（Introduction to the Linux Kernel）談單體 vs 微核心、kernel 的特性，正是本章的延伸
  - **注意**：講較舊 kernel，架構觀念適用、細節以 6.12 為準
- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati（O'Reilly, 2005）
  - **這本書的定位**：把各子系統的架構骨架講得最系統，當作本課的「地圖的地圖」；細節過時，大方向不過時

地圖有了、導航法有了。從下一章開始，我們正式走進門的另一側——先搞清楚「在 kernel 裡寫程式碼」和你熟悉的使用者空間到底有什麼不同：沒有 libc、stack 極小、隨時被搶佔、還有那個決定你能不能睡的「執行 context」。

→ [Ch 2 Kernel 的執行環境：context、stack、current](./02-execution-context.md)
