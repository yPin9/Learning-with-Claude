# Ch 14 — KASLR 與 info leak 大全：leak 途徑、percpu、uninitialized 記憶體、oracle

> **目標**：把前三章一直假裝不存在的 KASLR 加回來。學完你要能回答：KASLR 到底隨機化了什麼、為什麼「leak 一個位址」就能推回整片、以及 leak 從哪來——OOB read、未初始化記憶體、已知結構裡的指標、percpu、side-channel oracle。並把 [Ch 11](./11-stack-overflow.md) 欠下的 canary leak-and-restore 補完。沒有 leak，你 [Ch 13](./13-smep-smap.md) 辛苦湊的 ROP 鏈連第一個 gadget 的位址都填不出來——這章是所有真實題的共同前置。

> **環境**：Linux 6.6 LTS、x86-64、QEMU。本章**開** KASLR（移除 Ch 0 flag 表裡的 `nokaslr`），SMEP/SMAP 視範例而定。leak 的具體數值依你的 build 而不同，exploit code 未在本環境實測，標為理論預期行為。

## 為什麼 leak 是整場 exploit 的樞紐

回頭看 Ch 11–13，我們一直偷懶用 `nokaslr`，於是 `commit_creds`、gadget 的位址都是固定已知的常數，直接寫死。真實 kernel 預設**開 KASLR**，開機時把 kernel image 整塊搬到一個隨機位址——那些「固定常數」每次開機都不一樣。你寫死的位址一跑就 fault。

但 KASLR 有個**致命的結構性弱點**：它是「整塊平移」，不是「把每個函式打散」。kernel text 裡 `commit_creds` 和 `prepare_kernel_cred` 的**相對距離**永遠不變——變的只有整塊的起點（base）。

```
  開機 A：base = 0xffffffff81000000
          commit_creds = base + 0x0a5b40
  開機 B：base = 0xffffffff9e200000   ← base 變了
          commit_creds = base + 0x0a5b40   ← offset 沒變！
```

所以攻擊的形狀是：**leak 任何一個 kernel text 位址 → 減掉它的已知 offset → 得到 base → base + 其他 offset = 你要的任何位址**。整場 exploit 常常就卡在「怎麼弄到那一個 leak」。弄到了，後面全是加減法。

> 這也是為什麼 [Ch 12](./12-control-flow-hijack.md) 說「不要逐一 leak 每個函式，leak 一次 base」。offset 從**你手上那份 vmlinux** 算出來（`readelf -s vmlinux | grep commit_creds` 之類），和目標同一個 build 就準。

## KASLR 隨機化了什麼（以及沒隨機化什麼）

```
  位址區域                     KASLR 下是否隨機化      對應的 base
  ─────────────────────────   ──────────────────      ──────────
  kernel text（.text/.data）   整塊平移（9 bits 熵）    kernel base
  direct map / physmap         獨立隨機化               physmap base
  vmalloc area                 獨立隨機化               vmalloc base
  module 載入位址              獨立隨機化               module base
  固定的 cpu_entry_area 等     部分固定（KPTI 相關）    —
```

關鍵細節：

- **各區域各自有 base，不共用。** leak 一個 kernel text 位址只給你 kernel base；要打 physmap（[Ch 23](./23-dirty-pagetable.md)）得另外 leak 一個 physmap 位址。別拿 text 的 base 去算 physmap 的位址。
- **熵不高。** x86-64 的 kernel text KASLR 大約只有 9 bits 隨機（對齊到 2 MB），理論上有暴力空間——但 kernel 上猜錯一次常常就 panic，所以實務上還是靠 leak 而非爆破。
- **FGKASLR**（function-granular KASLR）想補強「整塊平移」的弱點，把函式順序也打散，讓「一個 text leak 推全部」失效。它的現況與繞法放在 [Ch 26](./26-modern-mitigations.md)；本章先假設標準 KASLR（offset 可推）。

## leak 從哪來：五條途徑

### 途徑一：OOB read 讀相鄰物件的指標

最直接。如果 bug 給你一個**越界讀**，就去讀一個「裡面本來就存著 kernel 指標」的相鄰物件。很多 kernel 物件的第一個欄位就是指向 ops table（函式指標表）的指標，而 ops table 在 kernel text / 固定資料區——讀到它就等於讀到一個 kernel text 位址。

用 [Ch 5](./05-first-vulnerable-module.md) 的 `vulndevice.c` 來說明：它有一條 `VULN_IOCTL_READ`（`vuln_do_read`），若它也拿 user 控的 `size` 當讀取長度而沒檢上界，就是一個 OOB read：

```c
/* user 端：用 OOB read 把相鄰記憶體連指標一起讀回來（概念示意） */
struct vuln_req req = { .size = 0x400, .buf = leak_buf };  /* 故意超過物件大小 */
ioctl(fd, VULN_IOCTL_READ, &req);
/* 在 leak_buf 裡掃描「長得像 kernel 指標」的 8-byte 值：
   0xffffffff8....... 開頭的多半是 kernel text */
for (int i = 0; i < 0x400/8; i++)
    if ((leak_buf[i] >> 40) == 0xffffff)     /* 粗略的 kernel text 樣式 */
        printf("[%d] candidate kernel ptr: %lx\n", i, leak_buf[i]);
```

拿到候選值後，和你 vmlinux 裡的符號比對，確認它是哪個符號，減掉 offset 得 base。

> 怎麼讓「裡面有指標的物件」剛好排在你後面，是 heap grooming（[Ch 20](./20-heap-grooming.md)）的事。本章先假設讀得到，重點在「讀到之後怎麼變成 base」。

### 途徑二：未初始化記憶體（uninit leak）

kernel 配了一塊記憶體但沒清就回傳給 user——裡面殘留著**上一個住戶**留下的 kernel 指標。常見於「回傳一個 struct 給 user，但某些欄位沒填」的路徑，或結構有 padding bytes 沒清。這類 bug 很安靜（不 corrupt 任何東西，只是洩漏），是最理想的 leak 來源。

`CONFIG_INIT_ON_ALLOC`（[Ch 26](./26-modern-mitigations.md)）就是來堵這類的——它把 alloc 出來的記憶體清零。但它不覆蓋所有路徑（例如某些 stack 上的結構、某些 copy 前沒清的 padding），縫仍在。看到「回傳結構給 user」的 code，就該檢查是不是每個欄位、每段 padding 都初始化了。

### 途徑三：已知結構裡的指標（固定佈局）

有些結構的佈局是已知且穩定的，裡面的某個欄位固定指向 kernel 已知位置。經典例子是 `tty_struct`（[Ch 19](./19-spray-objects.md)）——它開頭附近有指向 `tty_operations`（或 `ptm_unix98_ops` 等）的指標，而那些 ops table 在 kernel text 的固定 offset。spray 一堆 `tty_struct`、用 UAF/OOB 讀出其中的 ops 指標，就拿到 kernel base。這是「用 spray 物件當 leak 來源」的標準玩法。

### 途徑四：percpu 與其他固定來源

percpu 區域、`gs` base 指向的 per-cpu 結構裡也常有可推的 kernel 位址。有些題透過 leak 一個 percpu 位址或 `current`（當前 task_struct）的位址，再推其他東西。這類 leak 的價值常不只 base——`current`/`task_struct` 的位址還是 data-only 提權（直接改 cred）的入口（[Ch 16](./16-classic-primitives.md)）。

### 途徑五：side-channel / oracle（沒有記憶體洩漏時）

如果 bug 不直接吐記憶體，但你能觀察到「某個操作在位址對/不對時行為不同」（時間、是否 panic、是否回傳錯誤），就能把它當成一個**oracle**，一個 bit 一個 bit 問出位址。例如針對 KASLR 的 TLB/快取時間側信道、或「寫到某位址會不會 fault」的 fault-based oracle。這類較進階、較不穩，通常是「沒有乾淨 leak 時的最後手段」。知道它存在即可，別一開始就往這裡鑽。

## 把 leak 變成 base：實作骨架

```c
/* 假設 leak 出來的是某個已知符號的執行期位址 */
unsigned long leaked = /* 來自上面任一途徑 */;

/* 這個 offset 來自你的 vmlinux：
   readelf -s vmlinux | grep ' <符號名>$'  → 取其 st_value，
   減掉 vmlinux 的 kernel base（通常 0xffffffff81000000）得 offset */
#define OFF_LEAKED_SYM   0x????   /* 依你的 vmlinux 算出 */
#define OFF_COMMIT_CREDS 0x????
#define OFF_PREP_CRED    0x????
#define OFF_POP_RDI_RET  0x????   /* gadget，來自 ROPgadget vmlinux */

unsigned long kbase = leaked - OFF_LEAKED_SYM;
unsigned long commit_creds       = kbase + OFF_COMMIT_CREDS;
unsigned long prepare_kernel_cred= kbase + OFF_PREP_CRED;
unsigned long pop_rdi            = kbase + OFF_POP_RDI_RET;
```

拿到這些，Ch 13 的 ROP 鏈就能填進真實位址了。**一個 leak，解鎖整片。**

## 補完 Ch 11 的坑：canary leak-and-restore

[Ch 11](./11-stack-overflow.md) 為了先跑通提權，把 stack canary 關掉了。真實題 canary 開著，而你的連續溢位一定會蓋壞它、觸發 `__stack_chk_fail` panic。正解：**先 leak canary，溢位時把它原樣填回原位**，檢查就過。

canary 怎麼 leak？同樣靠上面五條途徑之一——最常見是一個**同函式內的 OOB read**（讀到 stack 上 canary 的值），或另一條洩漏路徑讀到 `__stack_chk_guard`。拿到後：

```
  payload 佈局（canary 開啟時）：
  [ 填滿 stack_buf 到 canary 前 ]
  [ leaked_canary ]   ◀── 原樣填回，騙過檢查
  [ 蓋 saved RBP ]
  [ ROP 鏈 / escalate 位址 ]  ◀── 蓋 saved RIP
```

注意：canary 是 **per-task** 的（每個 process 的 canary 不同，但同一個 process 內穩定），所以你 leak 的 canary 必須是**同一個 process** 的——跨 process leak 來的填回去不對。這點很多人踩。

## 踩雷集錦

1. **拿 text base 去算 physmap/module 位址**——各區域 base 獨立隨機化。text leak 只給 kernel text base。要打 physmap 得另外 leak physmap 位址，用錯 base 算出來的位址一碰就 fault。
2. **offset 用別人 writeup 的值**——offset 綁死在特定 vmlinux build（config、編譯器、版本）。照抄別題的 `OFF_COMMIT_CREDS` 幾乎一定錯。永遠對**你自己的 vmlinux**用 `readelf`/`ROPgadget` 算。
3. **把「像指標的值」直接當某個符號**——leak 回來一堆 8-byte，不是每個 `0xffffffff8...` 都是你以為的那個符號。要和 vmlinux 比對確認它是哪個符號、offset 多少；猜錯符號 → base 算錯 → 全盤皆錯。先驗證「leaked - offset 是不是落在合理的 2MB 對齊 base」。
4. **canary 跨 process 填回**——canary per-task。用 A process leak 的 canary 去填 B process 的溢位，檢查不會過。leak 和觸發要在同一個 process。
5. **以為 uninit leak 也要 grooming**——uninit leak 洩漏的是「這塊記憶體上個住戶的殘留」，你反而要讓「上個住戶是個帶 kernel 指標的物件」。它和 OOB read 的 grooming 方向類似但思路相反（控制的是**前一個**佔用者，不是相鄰者）。別套錯模板。

## 進階：leak 的「品質」與 FGKASLR

不是每個 leak 等價。評估一個 leak 看三點：

- **它洩漏哪個 base？** text / physmap / module / heap——決定你能解鎖什麼。最值錢的常是「同時能推 text base 和一個 heap 位址」的 leak。
- **穩不穩、能不能重複？** 一次性 leak 要求你一發入魂；可重複 leak 讓你能先 leak、驗證、再打，穩定性天差地別。
- **精不精確？** 直接讀到符號位址，vs 只能側信道問出幾個 bit——後者慢且易錯。

FGKASLR 把函式順序也打散後，「一個 text leak 推全部 offset」這個前提崩了——你 leak 到函式 A 的位址，推不出函式 B，因為它們的相對位置被隨機化。繞法（leak 多個點、或攻擊沒被 FGKASLR 動到的區域）在 [Ch 26](./26-modern-mitigations.md)。好消息是 FGKASLR 至今未普遍預設開啟，多數題仍是標準 KASLR。（部署現況會變，以你的目標 config 為準。）

## 動手練習

在自有 QEMU 靶（開 KASLR）上：

1. 給 `vulndevice.c` 的 read 路徑做一個 OOB read（拿 `req.size` 當長度不檢上界），從 user 端讀回一塊相鄰記憶體，掃出候選 kernel 指標。
2. 開機兩次，分別記下你 leak 到的值和 `/proc/kallsyms`（root）裡對應符號的真實位址，驗證「leaked - offset = base」且兩次的 offset 相同、base 不同。
3. 把 Ch 13 的 ROP 鏈改成用 leak 算出的動態位址，在開 KASLR 下跑通提權——這是你第一次打一題「位址不是寫死」的 exploit。
4. 加碼：把 canary 打開，加一條 leak canary 的路徑，做 leak-and-restore，確認不再 panic。

## 本章重點整理

- KASLR 是「整塊平移」：leak 一個 text 位址 → 減 offset → 得 base → 推出任何位址。offset 來自你自己的 vmlinux。
- 各區域（text / physmap / vmalloc / module）base 獨立，不可混用。
- leak 五途徑：OOB read 相鄰指標、uninit 殘留、已知結構的 ops 指標（spray 物件）、percpu/current、side-channel oracle。
- canary per-task，真實題要 leak-and-restore，且 leak 與觸發須同 process。
- FGKASLR 想打破「一 leak 推全部」，但尚未普遍預設開；標準 KASLR 仍是多數題前提。

## 自我檢核

- [ ] 能解釋為什麼 leak 一個位址就能推回整片 kernel text，以及這依賴 KASLR 的什麼性質
- [ ] 能說出 text base 和 physmap base 為什麼不能互相換算
- [ ] 不看筆記，能列出至少三條 leak 途徑，並各舉一個會出現它的情境
- [ ] 能解釋 canary leak-and-restore，以及為什麼 leak 與觸發要在同一個 process
- [ ] 知道 offset 為什麼不能照抄別人的 writeup，以及你會用什麼指令對自己的 vmlinux 算

## 延伸閱讀

- **[KASLR 原理與 kernel 自解壓重定位（Documentation/x86/x86_64/mm 與 arch/x86/boot/compressed）](https://elixir.bootlin.com/linux/v6.6/source/arch/x86/boot/compressed/kaslr.c)**
  - **讀哪裡**：`kaslr.c` 的 `choose_random_location`；看 kernel 怎麼決定隨機 base、對齊到多少、熵多少。
  - **和本章的關聯**：本章「9 bits、2MB 對齊」的說法來源；讀它才知道暴力空間為何這麼小。
  - **前提知識**：Ch 6 的記憶體佈局。
- **[A collection of Linux kernel exploitation leak techniques（pawnyable / 各 writeup）](https://pawnyable.cafe/linux-kernel/)**
  - **這篇說什麼**：示範 tty_struct、seq_operations 等物件怎麼當 leak 來源，附完整 code。
  - **讀哪裡**：各篇的 "info leak" 段，對照本章途徑三。
  - **為什麼值得讀**：把「spray 物件 → 讀 ops 指標 → 推 base」這條最常用的 leak 鏈講到可直接照打。
- **[Finding and exploiting uninitialized memory（Project Zero 相關文章）](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實案例示範 uninit/padding leak 的找法與利用。
  - **讀哪裡**：挑一篇 Linux kernel uninit leak 的 writeup，看作者怎麼定位沒清的欄位。
  - **為什麼值得讀**：uninit leak 最安靜、最好用，但也最難「看出來」；跟著真實案例練眼力。

有了 leak、能在 KASLR 下算出任何位址，我們離「全 mitigation 下打穿」只差最後一道牆——KPTI。它會讓 Ch 12 的 `swapgs; iretq` 回程失效，逼我們借 kernel 自己的 exit trampoline。

→ [Ch 15 — KPTI：swapgs_restore_regs_and_return_to_usermode 與 signal trampoline](./15-kpti.md)
