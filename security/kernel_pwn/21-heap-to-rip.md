# Ch 21 — 從 heap 到 RIP：函式指標劫持的機制與它的黃昏

> **目標**：理解「heap corruption 怎麼變成一次控制流轉移」這個經典機制——它的核心是 type confusion 把一個被解讀成**函式指標**的欄位換成攻擊者的值。學完你要能解釋這條路為什麼曾經是 heap 利用的標準終點、`tty_struct`/`seq_operations`/`pipe_buffer` 為什麼被反覆當例子，以及——更重要的——為什麼 CFI/KCFI（[Ch 28](./28-cfi-kcfi.md)）之後它大幅褪色，主流改走 data-only（[Ch 29](./29-data-only-attack.md)）。這章偏**機制理解**，不是操作手冊。

> **環境**：Linux 6.6 LTS、x86-64。以機制與版本演進為主，code 僅示意結構佈局。未實測，理論預期行為。

## 為什麼 heap 控流必然是「間接」的

[Ch 11](./11-stack-overflow.md) 的 stack overflow 直接蓋 saved return address，`ret` 當場轉移控制。heap 沒有這種現成的 return address（[Ch 17](./17-heap-overflow.md) 說過），所以 heap 要拿到控制流，只能透過一個**間接呼叫**——kernel 本來就會在某處執行 `call [某物件->某函式指標]`，而你把那個函式指標的值換掉了。

```
  heap corruption 改掉某物件的「函式指標欄位」
        │
  kernel 正常流程走到 call [obj->fn]      ← 間接呼叫（indirect call）
        │
  RIP = 你填進那個欄位的值
```

一句話：**heap 控流 = 把一個「會被當作函式指標呼叫的欄位」透過 type confusion 換成你的值。** 這也解釋了它天生的弱點——它依賴「kernel 做一個間接呼叫、而且不檢查目標合不合法」。CFI 要堵的正是這個前提。

## 先建立直覺：ops 指標就是 C 版的 vtable

kernel 用「物件裡放一個 `ops` 指標、指向一張函式指標表」實作多型，概念等同 C++ 的 vtable：

```
  struct tty_struct            struct tty_operations（這張表在唯讀的 .rodata）
  ┌──────────────┐             ┌────────────────────────┐
  │ magic        │             │ .open  → tty_open       │
  │ ops ─────────┼────────────▶│ .write → tty_write      │
  │ ...          │             │ .ioctl → tty_ioctl      │
  └──────────────┘             └────────────────────────┘
```

對這個模式，heap corruption 有兩個著力點，威力不同：

- **改 `ops` 指標本身**：讓它不再指向真正的 `tty_operations`，而指向你偽造的一張表。因為原表在唯讀記憶體你改不動，你改的是「指向哪張表」的那個指標。
- **改表裡的某個 entry**：若那張表本身在可寫記憶體（有些物件的 ops 是動態配置的），直接改某個函式指標。

兩者最終都是：kernel 之後呼叫 `ops->某函式` 時，跳到非預期的位址。這是 type confusion（[Ch 10](./10-vulnerability-taxonomy.md)）的直接後果——同一塊記憶體，kernel 當它是合法的 ops 關係，實際那個指標已被你換過。

## 三張經典牌代表的三種觸發點

[Ch 19](./19-spray-objects.md) 的牌庫裡，三個物件之所以反覆出現在 heap→RIP 的討論，是因為它們各自提供一個「user 能從外部觸發的間接呼叫」：

| 物件 | 間接呼叫在哪 | user 怎麼觸發 | 代表的 cache |
|---|---|---|---|
| `tty_struct` | `tty->ops->*`（write/ioctl…） | 對 `/dev/ptmx` 開出的 fd 做 write/ioctl | 大 cache（~1k 級） |
| `seq_operations` | `seq->start/stop/next/show` | 對 seq_file fd（如 `/proc/self/stat`）做 read | kmalloc-32 |
| `pipe_buffer` | `buf->ops->*`（release 等） | 對 pipe 做讀寫/關閉 | 特定 cache |

共通結構：**一個含函式指標的物件 + 一條從 user-space 能觸達「kernel 呼叫那個指標」的路徑**。理解這個配對，比記住「用哪個物件」重要——換了 kernel 版本、某張牌被 hardening 掉，你要能自己找下一個「含函式指標、又能被 user 觸發呼叫」的物件。

> 這些物件的函式指標欄位 offset、ops 是否可寫，隨版本變動大，且部分已被移到專用 cache 或加 hardening。這裡只給概念；要實作得對你的 kernel 用 `pahole` 確認佈局，並確認它在你的版本是否仍可當 victim。

## 轉移之後：還是回到 Ch 12–16 那套

假設控制流轉移成功（RIP 落到你要的地方），接下來做什麼，和漏洞是 heap 還是 stack 無關——完全回到 [Ch 12](./12-control-flow-hijack.md)–[Ch 16](./16-classic-primitives.md)：

- SMEP/SMAP 開 → 不能跳 user，得 kernel ROP（但間接呼叫的第一跳就要先過 CFI，見下）。
- 提權走 `commit_creds` 或 data-only；回程走 KPTI trampoline。

所以 heap→RIP 只是「把控制流拿到手」的那**一跳**，後半段是共用的收尾。值得注意的是：heap 給你的通常是「一次間接呼叫」，不像 stack overflow 給你「一整排可連續 ret 的 ROP 空間」。要把「一次 call」擴張成完整 ROP，往往還要先做 stack pivot（[Ch 13](./13-smep-smap.md)）把 rsp 轉到你排好鏈的地方——這也是 heap 控流比 stack 麻煩的一點。

## 這條路的黃昏：CFI/KCFI 為什麼讓它褪色

這章最重要的一段。上面整套的前提是「kernel 做間接呼叫時不檢查目標」。Linux 的 KCFI（clang-based，[Ch 28](./28-cfi-kcfi.md)）正是來打掉這個前提：每個間接呼叫點，在 call 之前檢查**目標函式的型別簽章**對不對。

後果對本章的技術是毀滅性的：

```
  CFI 之前：call [tty->ops->ioctl]  → 不檢查 → 跳哪都行 → 你的位址可達
  CFI 之後：call 前檢查目標的 type hash
            你填的位址（ROP gadget / commit_creds / 任意函式）
            型別簽章對不上「ioctl 應有的簽章」 → CFI 攔截 → panic
```

於是「把 ops 指標指向你偽造的表、填任意位址」這條經典路，在開 KCFI 的 kernel 上**當場失效**——你填的目標過不了型別檢查。CFI 擋不住的只剩「填一個型別剛好相符的合法 kernel 函式」，可用的落點大幅縮水，純 ROP 幾乎走不動。

這就是為什麼 2024 之後主流轉向 **data-only**（[Ch 29](./29-data-only-attack.md)）：既然「劫函式指標」被 CFI 掐死，那就根本不碰控制流——用 heap 原語去改資料（cred、page table、[Ch 16](./16-classic-primitives.md) 的全域字串），kernel 分不出那是攻擊還是合法寫入，CFI 對資料寫入無能為力。本章的技術不是沒用了，而是**退化成「目標 kernel 沒開 CFI 時才用」的一條路**；開了 CFI，你的腦袋要直接切到 data-only。

## 踩雷集錦

1. **把 heap→RIP 當成和 stack overflow 一樣直接**——heap 給你的是「一次間接呼叫」，不是一排 saved RIP。要連續 ROP 通常還得 stack pivot，別以為蓋到函式指標就等於有了完整 ROP 空間。
2. **忽略目標物件在你的版本是否還能用**——`tty_struct`/`seq_operations` 這些牌近年被陸續 hardening（移專用 cache、加 account 分流）。照抄舊 writeup 用某張牌前，先用 `pahole` 和 `/proc/slabinfo` 確認它在你的 kernel 還落在你打得到的 cache。
3. **在開 KCFI 的 kernel 上硬走函式指標劫持**——你填的任意位址過不了型別檢查，直接 panic。開 CFI 就別在這條路上耗，切 data-only。先確認目標有沒有開 `CONFIG_CFI_CLANG`。
4. **以為偽造的 ops 表隨便放哪都行**——kernel 要能讀到它（在可讀的 kernel 位址或 physmap 映射到的地方），且你得先知道它的位址（通常要 leak）。「我在 user-space 放一張表」在 SMAP 下 kernel 根本讀不到。
5. **把控制流轉移當目的**——目的是提權（換 cred / 任意讀寫），控制流只是手段之一，而且是越來越貴的那個。能用 data-only 達到同樣效果時，別為了「劫到 RIP」而劫。

## 進階：一個判斷流程

拿到一個 heap 原語、要決定終點走哪條，用這個順序問：

```
目標 kernel 開 CFI 嗎？
├─ 沒開：函式指標劫持可用（本章）。但仍優先評估 data-only——它更穩、更少依賴 leak。
└─ 有開：函式指標劫持基本死路。直接走 data-only：
          ├─ 能拿到 cred/task 位址 → 改 cred uid（Ch 16）
          ├─ 能任意位址寫 → modprobe_path / core_pattern（Ch 16）
          └─ 能改 page table → Dirty Pagetable 拿任意實體讀寫（Ch 23）
```

這個流程圖其實濃縮了 Part 3 後半到 Part 4 的全部走向：heap 利用的「終點技術」正在從控制流劫持，整體遷移到資料導向。本章是那段歷史的轉折點——你要知道舊路怎麼走（才讀得懂大量既有 writeup），更要知道它為什麼讓位。

## 動手練習

以「理解機制 + 觀察防禦」為主（在自有 QEMU 靶）：

1. 用 `pahole` 查你 kernel 上 `tty_struct`、`seq_operations`、`pipe_buffer` 的佈局，找出函式指標欄位的 offset，確認它們各自落在哪個 cache。
2. 關 CFI（`CONFIG_CFI_CLANG` 不開）編一版 kernel，在 UAF 靶上把某個 `seq_operations` 的函式指標蓋成一個好認的值，觸發 read，看 dmesg 的 fault RIP 確認控制流可被轉移。
3. 開 CFI 重編，重跑步驟 2，觀察它怎麼在間接呼叫點被 CFI 攔下（而不是跳到你的值）——親眼看到這條路在 CFI 下的死法。
4. 把步驟 2 的終點換成 data-only（改一個你能定位的資料結構），比較兩種終點對 CFI 的敏感度。

## 本章重點整理

- heap 控流必然是間接的：透過 type confusion 把「會被當函式指標呼叫的欄位」換成你的值。
- ops 指標 = C 版 vtable；著力點是「改 ops 指向偽表」或「改可寫表的 entry」。
- 三張經典牌（tty_struct/seq_operations/pipe_buffer）各提供一個 user 可觸發的間接呼叫；重點是理解「含函式指標 + 可被觸發」這個配對。
- 轉移之後的收尾回到 Ch 12–16；heap 常只給「一次 call」，要 ROP 得先 stack pivot。
- CFI/KCFI 檢查間接呼叫目標的型別，讓函式指標劫持大幅失效，主流因此遷向 data-only。

## 自我檢核

- [ ] 能解釋為什麼 heap 控流一定是間接呼叫，而非像 stack 那樣直接蓋 return address
- [ ] 能說出「改 ops 指標」和「改 ops 表 entry」的差別，以及為什麼原表通常改不動
- [ ] 能說出三張經典牌共通的「含函式指標 + user 可觸發」結構，並據此判斷一個新物件能不能用
- [ ] 能解釋 KCFI 怎麼讓函式指標劫持失效，以及為什麼它擋不住 data-only
- [ ] 拿到一個 heap 原語，能用「有沒有開 CFI」決定走控制流還是 data-only

## 延伸閱讀

- **[pawnyable — Linux Kernel: 各 Holstein 章（tty_struct / ops hijack）](https://pawnyable.cafe/linux-kernel/)**
  - **這篇說什麼**：示範在未開 CFI 的靶上，用 tty_struct/seq_operations 的 ops 做控制流轉移，含佈局與觸發路徑。
  - **讀哪裡**：UAF / heap overflow 篇的 "control flow hijack" 段。
  - **為什麼值得讀**：把本章的機制落到可觀察的實例；讀它時對照本章「CFI 之後怎麼死」，理解它適用的前提。
- **[Control Flow Integrity in the Linux Kernel（kernel docs / KCFI 設計）](https://docs.kernel.org/dev-tools/checkpatch.html)** 建議改讀 **[clang KCFI 文件與 LWN 報導](https://lwn.net/Articles/898040/)**
  - **這篇說什麼**：KCFI 怎麼在間接呼叫點插入型別檢查、為什麼選這個設計。
  - **讀哪裡**：KCFI 的機制段；對照本章「黃昏」一節。
  - **和本章的關聯**：本章對「CFI 怎麼擋函式指標劫持」的說明以此為準，細節在 [Ch 28](./28-cfi-kcfi.md)。
- **[struct tty_struct / seq_operations / pipe_buffer 定義](https://elixir.bootlin.com/linux/v6.6/source/include/linux/tty.h)**
  - **讀哪裡**：三個結構的函式指標欄位；用 `pahole` 對你的版本算 offset。
  - **和本章的關聯**：確認牌在你版本的佈局與 cache，而非照抄舊值。
  - **前提知識**：Ch 8 的 cache、Ch 19 的牌庫。

函式指標劫持卡在一個前提：victim 要和你能 spray 的可控物件同 cache。當 victim 住在一個**專用 slab cache**、一般 user 根本配不出同 cache 的可控物件時，前面整套 spray 都失效。下一章的 cross-cache attack 就是來打破這個牆——從 page 的層面跨過 cache 邊界。

→ [Ch 22 — Cross-cache attack：跨 kmalloc cache 打 dedicated slab](./22-cross-cache.md)
