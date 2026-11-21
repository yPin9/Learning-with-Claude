# Code Reading 心法：從看不懂一段 kernel code 到讀懂整個子系統

> 給 C 已經會寫、也讀過一些教材，但隨便抓一段 open source（尤其是 Linux kernel）還是讀不進去的工程師。

這門課以 Diomidis Spinellis《Code Reading: The Open Source Perspective》（Addison-Wesley, 2003）的骨架為底——讀碼的動機與策略、基本控制結構、資料型別、資料結構、進階控制流、大型專案、風格慣例、文件、架構、工具、完整案例——但把 2003 年的 NetBSD／Apache 例子全部換成**今天的 Linux kernel v6.18**，並在每一層補上原書沒有的現代心法與工具：git／mailing list 考古、clangd、ftrace、QEMU + gdb、LLM 輔助。暖身用 user-space 的 FreeBSD utility、Lua 和 Redis，然後一路往 kernel 推。學完後，你拿到一段沒看過的 kernel 函式，知道自己卡在哪一道鴻溝、該用哪一招把它拆開；拿到一個沒碰過的子系統，能在幾天內畫出它的架構、追出它的關鍵路徑，並說出「為什麼它要這樣寫」。

## 為什麼學這個？

- **你看不懂，通常不是 C 不夠好**。一段 kernel code 同時壓著五道鴻溝：慣用法（`container_of`、`ERR_PTR`）、間接（`f->f_op->read_iter()` 追到這裡就斷）、領域（不知道 page cache 是什麼）、脈絡（那三行是在修一個 race，理由寫在 mailing list）、目標（沒有帶問題就硬讀）。這門課的第一件事是教你**診斷自己卡在哪一道**，然後每一道各給一套拆解方法。
- **讀碼是工程師花最多時間、卻最少被教的技能**。寫一行之前要讀十行；接手專案、追 bug、審 patch、找漏洞、學新技術，全部都是讀碼。這是少數投資一次、所有領域都回本的能力。
- **Linux kernel 是 C 世界的終極讀碼試煉**。它大、間接多、慣用法密、歷史長，而且資料齊全：每一行都有 git 歷史，每一個 patch 都有公開的 mailing list 討論。在 kernel 上練出來的方法，拿去讀 Redis、SQLite、Postgres、任何 C/C++ 專案都綽綽有餘。
- **和這個 repo 其他課接起來**。[`c_sysprog`](../../programming/c_sysprog/README.md) 給你語言底子，[`kernel_pwn`](../../security/kernel_pwn/README.md) 需要你讀得懂 kernel，這門課就是中間那座橋。

## 先修知識

- **C 語言**：會讀會寫，懂指標、struct、函式指標、前置處理器的基本用法（程度：讀過 K&R 或寫過幾千行 C）
- **Linux 命令列與 git**：會 `grep`、`make`、`git log`（程度：日常使用）
- **作業系統概念**：知道 process、system call、virtual memory、interrupt 是什麼（程度：修過 OS 課或讀過 OSTEP 前半）
- 沒有也沒關係的：kernel 開發經驗、x86 組語（用到的地方會補）

## 課程地圖

分成入門（Part 1–2）、進階（Part 3–6）、精通（Part 7–9）三段。每一章開頭會標出它在練哪一道鴻溝。

### Part 1 — 心法基礎：先學會怎麼讀，再讀什麼（Ch 0–7）【入門】
- [Ch 0 環境與語料庫：WSL、kernel v6.18、讀碼工具鏈](./00-environment-setup.md)
- [Ch 1 為什麼你看不懂：五道鴻溝的自我診斷](./01-why-you-cant-read-it.md)
- [Ch 2 程式理解的認知模型：chunking、beacons、top-down 與 bottom-up](./02-program-comprehension-models.md)
- [Ch 3 帶著問題讀：提問驅動與假說驗證迴圈](./03-question-driven-reading.md)
- [Ch 4 選擇性閱讀：黑箱、略讀，以及接受只懂 80%](./04-selective-reading.md)
- [Ch 5 從哪裡開始讀：入口點與「資料結構優先」](./05-entry-points-and-data-first.md)
- [Ch 6 暖身：用全套心法讀完 FreeBSD wc](./06-warmup-bsd-wc.md)
- [Ch 7 讀碼筆記與畫圖：把理解外部化](./07-reading-notes-and-diagrams.md)
- [練習 A：冷讀一段陌生的 user-space 程式碼](./practice-a-cold-read-userspace.md)

### Part 2 — C 語言層：看懂每一行（Ch 8–14）【入門 → 進階】
- [Ch 8 控制流慣用法：goto 收尾、early return、迴圈與 switch 的讀法](./08-control-flow-idioms.md)
- [Ch 9 運算式、整數與位元：flags、mask、整數提升的陷阱](./09-expressions-integers-bits.md)
- [Ch 10 指標慣用法：走訪、指標的指標、函式指標](./10-pointer-idioms.md)
- [Ch 11 struct、union 與 tagged union：讀懂資料的形狀](./11-structs-unions-tagged.md)
- [Ch 12 動態記憶體與所有權慣例：誰配置、誰釋放](./12-memory-ownership-conventions.md)
- [Ch 13 前置處理器基礎：巨集、條件編譯、展開來看](./13-preprocessor-basics.md)
- [Ch 14 巨集元程式：X-macro、token pasting 與巨集生成的程式碼](./14-macro-metaprogramming.md)
- [練習 B：解碼 user-space 天書片段（Lua、Redis）](./practice-b-decode-userspace-snippets.md)

### Part 3 — Kernel C 方言：kernel 不是你學的那個 C（Ch 15–22）【進階】
- [Ch 15 GNU C 擴充：statement expression、typeof、attribute、flexible array](./15-kernel-c-gnu-extensions.md)
- [Ch 16 container_of：kernel 最重要的一個巨集](./16-container-of.md)
- [Ch 17 kernel 的錯誤慣例：-errno、ERR_PTR、goto 階梯與 cleanup.h](./17-kernel-error-conventions.md)
- [Ch 18 編譯器標註與 sparse：__user、__rcu、__must_check、likely](./18-annotations-and-sparse.md)
- [Ch 19 kernel 記憶體配置：kmalloc、GFP、slab、vmalloc、devm](./19-kernel-memory-allocation.md)
- [Ch 20 refcount 與物件生命週期：kref、get/put 配對](./20-refcount-and-lifetime.md)
- [Ch 21 READ_ONCE 與 memory barrier：那些看起來多餘的巨集](./21-read-once-and-barriers.md)
- [Ch 22 kernel 巨集解碼大全：for_each、DEFINE_*、module_init、EXPORT_SYMBOL](./22-kernel-macro-decoding.md)
- [練習 C：逐行解碼 10 段 kernel 天書](./practice-c-decode-kernel-snippets.md)

### Part 4 — Kernel 資料結構：先讀懂資料，程式碼就讀懂一半（Ch 23–27）【進階】
- [Ch 23 侵入式鏈結串列：list_head 與 hlist](./23-list-head-hlist.md)
- [Ch 24 紅黑樹：rbtree 與 augmented tree](./24-rbtree.md)
- [Ch 25 hash table：hashtable.h 與 rhashtable](./25-hash-tables.md)
- [Ch 26 xarray 與 maple tree：從 radix tree 演進而來](./26-xarray-maple-tree.md)
- [Ch 27 bitmap、per-cpu 變數與 kfifo](./27-bitmap-percpu-kfifo.md)
- [練習 D：替一個子系統畫出完整的資料結構地圖](./practice-d-data-structure-map.md)

### Part 5 — 控制流與間接：追到斷掉之後怎麼辦（Ch 28–34）【進階】
- [Ch 28 ops table 與間接呼叫：找出真正被呼叫的是誰](./28-ops-tables-indirect-calls.md)
- [Ch 29 syscall 入口與路徑：從 SYSCALL_DEFINE 開始追](./29-syscall-entry-paths.md)
- [Ch 30 讀有 lock 的程式碼：臨界區、lock 規則與 lockdep 標註](./30-reading-locking-code.md)
- [Ch 31 讀 RCU 程式碼：reader、updater 與 grace period](./31-reading-rcu-code.md)
- [Ch 32 非同步控制流：中斷、softirq、workqueue、timer](./32-async-irq-softirq-workqueue.md)
- [Ch 33 等待與喚醒：wait queue 與 completion](./33-wait-queues-completion.md)
- [Ch 34 notifier 與 callback 註冊模式](./34-notifiers-and-callbacks.md)
- [練習 E：從 read(2) 一路追到 page cache](./practice-e-trace-read-syscall.md)

### Part 6 — 脈絡：為什麼要寫成這樣（Ch 35–40）【進階】
- [Ch 35 kernel 原始碼樹地圖與子系統邊界](./35-kernel-source-tree-map.md)
- [Ch 36 Kconfig、Kbuild 與 #ifdef：哪些程式碼真的會被編進去](./36-kconfig-kbuild-ifdef.md)
- [Ch 37 git 考古：blame、log -L、pickaxe、Fixes tag](./37-git-archaeology.md)
- [Ch 38 mailing list 考古：lore.kernel.org 與 patch series](./38-mailing-list-archaeology.md)
- [Ch 39 文件與風格：Documentation、kernel-doc、coding style](./39-documentation-and-style.md)
- [Ch 40 把測試當規格讀：selftests 與 KUnit](./40-tests-as-specification.md)
- [練習 F：考古一個功能，還原它的設計理由](./practice-f-feature-archaeology.md)

### Part 7 — 架構與領域：從函式到系統（Ch 41–46）【精通】
- [Ch 41 分層與抽象：VFS 作為範例](./41-layering-and-vfs.md)
- [Ch 42 C 語言裡的物件導向：struct 嵌入、vtable、kobject](./42-object-oriented-c.md)
- [Ch 43 driver model：bus、device、driver 與 probe](./43-driver-model.md)
- [Ch 44 state machine 與事件驅動架構](./44-state-machines-event-driven.md)
- [Ch 45 辨識 kernel 裡的設計模式](./45-design-patterns-in-kernel.md)
- [Ch 46 快速補領域知識：LWN、Documentation、書與論文](./46-acquiring-domain-knowledge.md)

### Part 8 — 讀碼工具：讓機器幫你讀（Ch 47–53）【精通】
- [Ch 47 grep、ripgrep 與 regex 搜尋策略](./47-grep-and-regex-search.md)
- [Ch 48 elixir、cscope 與 GNU Global](./48-elixir-cscope-gtags.md)
- [Ch 49 clangd 與 LSP：compile_commands.json 讓編輯器真的懂 kernel](./49-clangd-lsp.md)
- [Ch 50 把編譯器當讀碼工具：.i、.s、objdump、pahole](./50-compiler-as-reading-tool.md)
- [Ch 51 動態觀察：ftrace function_graph 與 bpftrace](./51-dynamic-tracing.md)
- [Ch 52 QEMU + gdb：單步追 kernel](./52-qemu-gdb-kernel.md)
- [Ch 53 用 LLM 輔助讀碼，以及它會在哪裡騙你](./53-llm-assisted-reading.md)
- [練習 G：寫一個 ops table callback 自動定位工具](./practice-g-ops-callback-finder.md)

### Part 9 — 精通實戰：冷讀（Ch 54–58 + Final）【精通】
- [Ch 54 冷讀實戰一：隨機抓一個 kernel 函式](./54-cold-read-random-function.md)
- [Ch 55 冷讀實戰二：一個 driver 的 probe 函式](./55-cold-read-driver-probe.md)
- [Ch 56 冷讀實戰三：網路收包路徑的一段](./56-cold-read-network-rx.md)
- [Ch 57 讀 patch 與 code review：讀 diff 的心法](./57-reading-patches-and-review.md)
- [Ch 58 讀碼箴言與檢查清單](./58-maxims-and-checklist.md)
- [Final Project：完整導讀 fs/pipe.c 與 eventfd](./final-project-pipe-eventfd-guided-reading.md)

## 學習方式建議

1. **Ch 1 的五道鴻溝要背起來**。之後每次讀不懂，先問自己「我現在卡在哪一道？」。這個問題本身就能解掉一半的卡關，因為每一道鴻溝的解法完全不同：慣用法靠查、間接靠工具、領域靠補背景、脈絡靠考古、目標靠重新提問。
2. **每章都在你自己的 tree 上動手**。教材裡引用的檔案與行號都固定在 v6.18；`git checkout v6.18` 之後，打開同一個檔案跟著讀。只看教材不開原始碼，等於只看別人健身。
3. **寫筆記，畫圖**。Ch 7 的筆記法從練習 A 開始一路用到 final。讀碼的理解如果沒有外部化，三天後會蒸發。
4. **先猜，再驗證**。讀到一個函式名稱，先猜它做什麼，再讀內文。猜錯的地方就是你學到東西的地方。這是 Ch 3 假說驗證迴圈的日常版。
5. **不要從第一行讀到最後一行**。Ch 4 講得很清楚：沒有人這樣讀 kernel，包括 maintainer。

## 精選資料庫

這裡列的是整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - 整門課的骨架。例子老了，方法論沒老；第 1、2、9、10 章與附錄的 maxims 最值得重讀
- **[Linux kernel 原始碼（Bootlin Elixir，v6.18）](https://elixir.bootlin.com/linux/v6.18/source)**
  - 線上交叉參照；任何一個識別字點下去就知道它在哪定義、在哪被用
- **[The Linux Kernel documentation](https://docs.kernel.org/)**
  - 官方文件。`process/coding-style`、`core-api/`、`filesystems/vfs` 是本課最常引用的幾篇

### 推薦論文

- **[Towards a theory of the comprehension of computer programs](https://doi.org/10.1016/S0020-7373(83)80031-5)** — Ruven Brooks，International Journal of Man-Machine Studies（1983）
  - 提出「假說驅動、由上而下」的理解模型與 beacon 概念；Ch 2、Ch 3 的理論基礎
- **[Program Comprehension During Software Maintenance and Evolution](https://doi.org/10.1109/2.402076)** — von Mayrhauser & Vans，IEEE Computer（1995）
  - 整合 top-down、bottom-up 與 situation model 的理解模型；說明熟手為什麼會在策略之間切換

### 推薦部落格 / 文章

- **[LWN.net Kernel Index](https://lwn.net/Kernel/Index/)** — Jonathan Corbet 等
  - 補 kernel 領域知識最有效率的地方；幾乎每個子系統的設計演進都有一篇 LWN 文章
- **[lore.kernel.org](https://lore.kernel.org/)**
  - 所有 kernel mailing list 的公開封存；patch 背後的「為什麼」都在這裡（Ch 38）

### 讀完本課之後

- **《Linux Kernel Development》（3rd ed.）** — Robert Love（2010）：子系統的概念導覽，版本老但模型仍然適用，適合當 Part 7 的領域背景
- **《Understanding the Linux Kernel》（3rd ed.）** — Bovet & Cesati（2005）：比 Love 深得多，2.6 時代的細節多已改變，但資料結構的設計理由仍有參考價值
- **[kernel_pwn 課程](../../security/kernel_pwn/README.md)**：讀懂 kernel 之後，換個角度看它怎麼被攻破
