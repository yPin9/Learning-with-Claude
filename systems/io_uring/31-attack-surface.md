# Ch 31 — 為什麼 io_uring 是漏洞溫床：攻擊面分析

> **目標**：建立一張「io_uring 為什麼容易出漏洞」的結構性地圖。我們不急著拆任何一個 CVE，而是先回答更底層的問題：這個子系統的哪些**設計決策**系統性地製造了漏洞，漏洞又分成哪幾類。讀完這章，你看到一個 io_uring commit 的標題就能猜出它大概屬於哪一類 bug，也知道後面 Ch 32–40 各自在補地圖的哪一塊。

> **環境**：對照 Linux 6.18 的 `io_uring/` 原始碼（WSL2 實測 6.18.58.1）。本章是分析與分類，不含可執行的攻擊碼；需要跑的實測集中在 Ch 39。本課所有安全內容以理解機制、建立防禦與審計能力為目的，實驗一律在自有或獲授權的受控環境進行。

## 為什麼要先畫地圖，而不是直接拆 CVE

你可以挑一個 io_uring CVE，把 patch diff 讀到滾瓜爛熟，然後對下一個 CVE 一無所知——因為你學到的是「這一個 bug」，不是「這一類 bug 為什麼會發生」。io_uring 的 CVE 有一個特徵：它們**彼此長得很像**。同樣是 async 執行時物件已經被釋放、同樣是某個 refcount 少加一次、同樣是 user memory 在 kernel 檢查與使用之間被改掉。拆十個案例不如先看懂製造這些案例的那四五個結構性原因。

這一章我們就做這件事：把 io_uring 當成一個「漏洞生產線」來分析，看清楚生產線上的每一道工序為什麼會出錯。這對防禦者（也就是本課 Part 4 想把你訓練成的角色）是最有槓桿的視角——你要 harden 一個系統、要 review 一段 io_uring 程式碼、要評估一個 CVE 的真實可利用性，靠的不是記住某個 offset，而是知道**這個子系統的錯誤都長在哪裡**。

> 這章接續 Part 3（Ch 19–30）的 kernel 內部實作。那邊我們中性地看一個請求怎麼被提交、派發、完成、回收；這章把同一套機制「翻過來」，問每一步「如果這裡出錯會怎樣」。如果 Part 3 還沒讀熟，建議至少把 Ch 21（io_kiocb 生命週期）、Ch 26（io-wq）、Ch 29（rsrc）複習一遍再往下。

## 先建立直覺：一個請求要經過多少個「可能出錯的交接點」

先看一張圖。這是一個 io_uring 請求從 user 提交到最終釋放，可能經過的執行環境（execution context）：

```
  user space                     kernel space
 ┌──────────┐   io_uring_enter  ┌───────────────────────────────────────┐
 │ 填 SQE   │ ────────────────► │ 提交執行緒 (submitter task)           │
 │ 推 tail  │                   │   io_submit_sqes → io_issue_sqe       │
 └──────────┘                   │        │                              │
      ▲                         │        ├─ 立刻完成 (inline)            │
      │ 收 CQE                  │        │                              │
      │                         │        ├─ 丟給 io-wq worker ──────┐   │
      │                         │        │   (另一條 kernel thread) │   │
      │                         │        ├─ arm poll → 等事件 ───┐  │   │
      │                         │        │   (軟中斷/回呼 context)│  │   │
      │                         │        └─ 排 task_work ──────┐ │  │   │
      │                         │            (回到 task 時執行)│ │  │   │
      │                         │                             ▼ ▼  ▼   │
      │                         │                  io_req_complete      │
      │                         │                  填 CQE / 釋放 req    │
      └─────────────────────────┴───────────────────────────────────────┘
```

重點在中間那四個分支：**同一個 `io_kiocb` 請求物件，可能在提交執行緒、io-wq worker、poll 回呼、task_work 四種不同的 context 裡被碰到**，而且常常是其中幾種**同時**碰。每一個「交接」——把請求從一個 context 傳到另一個——都是一次所有權（ownership）與生命週期的轉移。轉移時誰持有這個物件、誰負責釋放、釋放後還有沒有人會碰它，只要有一個環節算錯，就是一個 UAF 或 race。

對比一下傳統同步 syscall：`read()` 從進入 kernel 到返回，整條路都在**同一個 task context**、同一個 stack frame 上跑完，物件生命週期和函式呼叫邊界對齊，出錯空間小得多。io_uring 用「請求物件化 + 多 context 非同步執行」換來了吞吐量，代價就是把生命週期管理從「編譯器幫你顧的 stack」搬到了「要手動顧的 heap 物件 + refcount」。**漏洞溫床的第一性原因就在這張圖裡。**

## 五個結構性原因

把上面的直覺拆成可以逐條檢查的原因。這五條是後面所有章節的骨架。

### 原因一：非同步多執行流（io-wq / task_work / SQPOLL）

io_uring 的核心賣點是「提交和完成解耦」。一個請求提交後不一定當場完成，可能被丟到 `io-wq` 的 kernel worker thread pool（Ch 26）、可能掛在 poll 上等 fd 就緒（Ch 28）、可能排成 `task_work` 等 owning task 下次進 kernel 時執行（Ch 27）。這代表：

- 同一個請求物件會被**多條執行流**看到，需要 refcount 或 lock 協調所有權。
- 「提交」和「執行」在時間上分離，中間這段**時間窗**裡，user 可能已經改了共享記憶體、關了 fd、或對這個請求下了 cancel。
- teardown（ring 關閉）要能安全地把散落在各 context 的 in-flight 請求收回來——這本身就是一個極難寫對的並發問題。

這條原因直接生出 **race 類**漏洞（Ch 33）和一大半 **UAF 類**漏洞（Ch 32）。

### 原因二：大量引用計數物件，生命週期彼此糾纏

io_uring 裡幾乎每個重要物件都有自己的引用計數或等價機制：

| 物件 | 計數機制（6.18） | 大致語意 |
|---|---|---|
| `io_kiocb`（請求） | `req->refs`（`atomic_t`，**按需啟用**） | 多 context 共享時才啟用 |
| `io_rsrc_node`（資源節點） | `node->refs`（`int`，`uring_lock` 保護） | registered file/buffer 的中介節點 |
| `io_mapped_ubuf`（固定 buffer） | `imu->refs`（`refcount_t`） | buffer 本體，可被多 node 共享 |
| `io_ring_ctx`（ring 本身） | 多個 refcount + completion | 整個實例的生命週期 |
| io-wq `io_worker` | `worker->ref`（`refcount_t`） | worker thread 生命週期 |

這些計數**互相牽連**：一個請求引用一個 rsrc node，node 引用一個 ubuf，ubuf 可能被另一個 ring 透過 clone 共享。任何一層少加一次 get、多呼一次 put，或在錯的 context 下 put，都會讓某一層提早歸零被釋放，而上層還握著指標——典型 UAF。Ch 32 整章在拆這個模式。

### 原因三：生命週期複雜，所有權在路徑間反覆轉移

承上，問題不只是「物件多」，而是「同一個物件的所有權在一條路徑上被交接很多次」。看一個真實片段，`io_wq_submit_work`（`io_uring.c`，把請求丟給 io-wq worker 執行時）：

```c
void io_wq_submit_work(struct io_wq_work *work)
{
	struct io_kiocb *req = container_of(work, struct io_kiocb, work);
	...
	/* one will be dropped by io_wq_free_work() after returning to io-wq */
	if (!(req->flags & REQ_F_REFCOUNT))
		__io_req_set_refcount(req, 2);
	else
		req_ref_get(req);
	...
}
```

這三行在說：「這個請求現在要被 worker thread 執行，但 io-wq 框架返回時還會再碰它一次，所以我要確保有兩個 reference——一個給執行、一個給框架回收。」這段 code 本身是對的，但它示範了 io_uring 到處都是的這種「我現在要多拿一個 ref，因為等一下有另一條路也會碰它」的推理。**只要有一個這樣的交接點漏了 `req_ref_get`，或多了一個 `put`，就是洞。** io_uring 歷史上大量 CVE 的 root cause 一句話就能講完：「某條錯誤處理路徑（error path）上少 get / 多 put 了一次。」

### 原因四：演進極快，新碼多、攻擊面持續擴張

io_uring 幾乎每個 kernel release 都在加新 opcode 和新機制：provided buffer ring、multishot、zero-copy send、`IORING_OP_URING_CMD`、registered ring、napi busy poll……`opdef.c` 裡 6.18 的 opcode 已經排到 60 幾號。新機制意味著新程式碼，新程式碼意味著還沒被 fuzzer 和 reviewer 充分洗過的 bug。更糟的是，新機制常常和舊機制**交互**（例如 provided buffer 配 multishot 配 recv），交互路徑的組合爆炸，是最容易漏審的地方。

這也是為什麼 io_uring 的攻擊面不是「一塊固定的地」，而是「一塊還在長大的地」。防禦者要意識到：你今天 review 過的版本，下個 release 可能又多了幾條沒人走過的路徑。

### 原因五：原語強大，一旦有洞就好利用

io_uring 讓 user 能**精確地、批次地、可控時機地**要求 kernel 做記憶體操作：分配 kernel buffer、在指定 kernel 物件裡讀寫、精確控制 async 時機。這些能力在正常使用下是效能利器，在利用（exploitation）時就是現成的 heap grooming 與原語建構工具——這也是為什麼 io_uring 的 bug 「可利用性」普遍偏高，一個看似普通的 UAF 常常能被穩定地打成提權。這塊是 Ch 38 的主題，本課只在概念層說明「為什麼可利用」，不產出武器化攻擊碼。

> **把五條串起來**：原因一、三製造 race 與所有權錯誤，原因二把一個所有權錯誤放大成跨物件的 UAF，原因四讓這些錯誤持續有新的滋生地，原因五讓這些錯誤值得攻擊者投入。這就是「溫床」的完整配方。

## 底層機制：從原始碼看「為什麼難寫對」

挑兩個 6.18 的設計細節，看 kernel 開發者自己有多小心——小心本身就是「這裡很容易錯」的證據。

### 證據一：refcount 是「按需啟用」的，這是一把雙面刃

io_uring 不給每個請求都配 refcount。預設情況下一個請求**沒有** refcount，靠「同一時間只有一條路擁有它」這個不變量（invariant）來省掉 atomic 操作。只有當一個請求真的會被多方引用時，才呼叫 `io_req_set_refcount` 把 `REQ_F_REFCOUNT` 打開。看 `refs.h`：

```c
static inline bool req_ref_put_and_test(struct io_kiocb *req)
{
	if (likely(!(req->flags & REQ_F_REFCOUNT)))
		return true;   // 沒啟用 refcount：直接當作「最後一個 ref」處理

	WARN_ON_ONCE(req_ref_zero_or_close_to_overflow(req));
	return atomic_dec_and_test(&req->refs);
}
```

這個設計省了效能，但製造了一個隱性陷阱：**判斷「這個請求該不該啟用 refcount」本身是人工推理，而且必須在所有會多方引用的路徑上都一致。** 如果某條新加的路徑會讓請求被第二方引用，卻忘了先 `io_req_set_refcount`，那 `req_ref_put_and_test` 會在第一次 put 時就回報「最後一個 ref」，於是物件被提早釋放，另一方還握著它——UAF。歷史上這類「漏設 refcount」的 bug 出現過不只一次。6.18 用 `WARN_ON_ONCE` 在多處哨戒，正說明這是個已知的易錯點。

### 證據二：讀 user 記憶體一律 `READ_ONCE`，因為「user 不是好公民」

SQE 存在 user 和 kernel 共享的記憶體裡（Ch 4）。kernel 從 SQE 讀欄位時，不能假設讀兩次會得到一樣的值——user 可能在兩次讀之間用另一條執行緒改掉它。看 `io_get_sqe` 上方那段註解（`io_uring.c`，一字不改）：

```c
/*
 * Fetch an sqe, if one is available. Note this returns a pointer to memory
 * that is mapped by userspace. This means that care needs to be taken to
 * ensure that reads are stable, as we cannot rely on userspace always
 * being a good citizen. If members of the sqe are validated and then later
 * used, it's important that those reads are done through READ_ONCE() to
 * prevent a re-load down the line.
 */
```

「we cannot rely on userspace always being a good citizen」——這就是 double-fetch（double fetch / TOCTOU）的教科書警語。每一個 SQE 欄位在 `io_init_req` 裡都用 `READ_ONCE` 讀，就是為了「驗證後不會被重新載入成另一個值」。Ch 33 會看到，一旦某條路徑漏掉這個紀律，「檢查用的值」和「實際使用的值」就可能不同，形成可利用的 race。

### 證據三：丟一個請求去 async 的那一刻，攻擊面全開

把前面的原因接到一行具體的程式碼上。當一個請求不能當場完成、要轉給 io-wq worker 執行時，走的是 `io_queue_iowq`（`io_uring.c`）：

```c
static void io_queue_iowq(struct io_kiocb *req)
{
	struct io_uring_task *tctx = req->tctx;
	BUG_ON(!tctx);
	...
	/* init ->work of the whole link before punting */
	io_prep_async_link(req);
	/*
	 * Not expected to happen, but if we do have a bug where this _can_
	 * happen, catch it here and ensure the request is marked as canceled.
	 */
	if (WARN_ON_ONCE(!same_thread_group(tctx->task, current)))
		atomic_or(IO_WQ_WORK_CANCEL, &req->work.flags);
	...
	io_wq_enqueue(tctx->io_wq, &req->work);
}
```

這短短一個函式，把本章五個原因全演了一遍：

- `io_wq_enqueue` 把請求交給**另一條 kernel thread**——原因一（多執行流）當場發生，請求的所有權從提交 task 轉到 worker。
- 轉交前要 `io_prep_async_link` 把整條 link（Ch 16）的 `->work` 都備好，牽動多個請求物件的狀態——原因二、三（糾纏的物件、所有權轉移）。
- 那個 `WARN_ON_ONCE(!same_thread_group(...))` 連著一段註解「如果真有 bug 讓這發生，在這裡抓住並標記 cancel」——開發者在程式碼裡**預先替一個假想的 bug 埋了防線**。這是「正確性靠人工紀律」最赤裸的證據：連「這不該發生，但萬一發生了」都要手動兜底。

**一個請求轉 async 的這一瞬間，就是攻擊面從「單執行緒、stack 對齊」切換到「多執行緒、heap 物件、手動生命週期」的那條線。** io_uring 的多數記憶體漏洞，root cause 都落在這條線的某一側處理錯了。

這三個證據的共同點：**正確性靠的是一套人工維持的紀律，而不是編譯器或型別系統強制的保證。** 紀律一鬆，就是 CVE。

## 歷史弧線：io_uring 的攻擊面怎麼長成今天這樣

把 io_uring 的安全史壓成一條時間線，能看清楚「攻擊面持續擴張」不是抽象說法，而是有具體節奏的：

- **2019（5.1）誕生**：最初只有少數幾個 op（readv/writev/fsync…），攻擊面小。但「請求物件化 + async 執行」的核心設計——也就是本章五個原因的根——從第一天就埋下了。
- **2020–2021 機制爆發**：provided buffers、linked ops、SQPOLL、registered files/buffers、poll、fast poll 陸續加入。每個機制都帶來新的生命週期與並發路徑。seccomp 繞過的爭議（Ch 39）也在這段期間被 kernel 社群正式討論。
- **2022–2023 利用成熟期**：安全社群累積出成套的 io_uring 利用手法（物件當 heap spray 工具、UAF 轉提權的穩定流程）。Google kernelCTF 的數據在這段期間暴露——約 60% 的有效 submission 打的是 io_uring。
- **2023 社群反應**：`io_uring_disabled` sysctl 在 6.6 落地（Ch 39）；Google 分產品停用；資源管理（rsrc）經歷多次重構，把早期的 `percpu_ref` + RCU 延遲釋放簡化掉（這本身就是為了消滅一整類生命週期 bug）。
- **2023 至今 持續演進**：multishot、zero-copy、`IORING_OP_URING_CMD`、registered ring、napi busy poll 繼續加入，opcode 編號在 6.18 已排到 60 幾號。新機制 × 舊機制的交互路徑是當前最活躍的 bug 滋生地。

這條弧線的教訓很直接：**io_uring 的攻擊面是一個移動目標。** 它早期的 bug 多在基礎機制（poll、refcount），中期在利用手法成熟，近期則集中在「新機制及其與舊機制的交互」。防禦者讀一個 CVE 時，先定位它在這條弧線的哪一段，就能快速判斷它是「老機制的經典錯誤」還是「新機制的首批 bug」——兩者的分析重點和後續該查的範圍不同。

## 攻擊面分類：五大類漏洞

把 io_uring 的歷史漏洞歸納成五類。這個分類是 Ch 34–37 案例拆解的座標系。

```
         ┌─────────────────────────────────────────────┐
         │              io_uring 漏洞類別                │
         ├───────────────┬─────────────────────────────┤
  記憶體  │ UAF           │ async 執行時物件已釋放、     │
  安全    │ (Ch 32)       │ refcount 錯誤、double free   │
         ├───────────────┼─────────────────────────────┤
         │ Race / TOCTOU │ double-fetch、completion vs  │
         │ (Ch 33)       │ cancel、teardown race        │
         ├───────────────┼─────────────────────────────┤
         │ OOB R/W       │ buffer ring / provided buf   │
         │ (Ch 36)       │ 索引或長度計算錯誤            │
         ├───────────────┼─────────────────────────────┤
         │ Type confusion│ fixed buffer / rsrc node     │
         │ (Ch 34)       │ 把一種物件當另一種用          │
         ├───────────────┼─────────────────────────────┤
  邏輯    │ 邏輯 / 權限    │ creds 覆寫、drain/link 語意、 │
         │ (散見)        │ msg_ring 跨 ring 誤用        │
         └───────────────┴─────────────────────────────┘
```

幾點說明：

- **這些類別會重疊。** 一個 type confusion 常常**導致**一個 UAF；一個 race 常常是 UAF 的**觸發條件**。真實 CVE 很少是單一類別，通常是「用 A 類手法穩定地觸發 B 類後果」。例如 Ch 34 的案例是「type confusion 造成 UAF」。
- **記憶體安全類是大宗。** UAF 和 race 加起來佔了 io_uring CVE 的絕大多數，這也是為什麼 Ch 32、33 給了兩整章總論，而不是只在個別案例裡帶過。
- **邏輯類較少但更隱蔽。** 像 `IORING_OP_URING_CMD` 的權限檢查、`msg_ring` 跨 ring 傳遞、personality/creds 覆寫這類，不是記憶體壞掉，而是「做了不該被允許的事」，fuzzer 不容易抓，要靠人讀語意（Ch 40）。

把這五類對應到 Part 4 後續章節，就是這門課安全篇的導覽圖：

| 類別 | 總論章 | 案例/實作章 | 對應的結構性原因 |
|---|---|---|---|
| UAF | Ch 32 | Ch 34（type confusion→UAF）、Ch 35（race→UAF）、練習 E | 原因二、三 |
| Race / TOCTOU | Ch 33 | Ch 35 | 原因一、三 |
| OOB R/W | （散於 Ch 32/36） | Ch 36（buffer ring OOB） | 原因四、五 |
| Type confusion | （Ch 32 導入） | Ch 34 | 原因二 |
| 邏輯 / 權限 | （Ch 39 防禦面） | Ch 37（rsrc/msg_ring 類） | 原因三、四 |

讀到這裡你會發現，後續每一章都是在這張地圖上「放大某一格」。Ch 32、33 先把最大的兩格（UAF、race）的通用模式講透，Ch 34–37 再用真實 CVE 把模式落到個案，Ch 38 看這些 bug 的利用價值，Ch 39、40 轉到防禦與審計。地圖在手，後面就不會迷路。

## 對比與取捨：io_uring vs 其他「高危子系統」

io_uring 常和 netfilter、eBPF 並列為 kernelCTF 的「三大礦區」。放在一起看能更懂它的特性：

| 子系統 | 主要 bug 來源 | 攻擊者友善度 | 防禦難點 |
|---|---|---|---|
| **io_uring** | async 生命週期、refcount、double-fetch | 高（原語強、grooming 易） | 新碼多、停用會砍效能 |
| **netfilter / nf_tables** | 規則集狀態機、物件生命週期 | 高 | 功能必需、難停用 |
| **eBPF** | verifier 邏輯漏洞 | 中高（繞過 verifier 後很強） | 可用 `unprivileged_bpf_disabled` 關非特權 |

io_uring 的獨特處在於：它的 bug **不是**來自某個複雜演算法（像 eBPF verifier）或某個狀態機（像 nf_tables），而是來自**並發與生命週期管理這件事本身的困難**。這意味著它的 bug 比較「均勻地」散佈在整個子系統，而不是集中在一兩個熱點函式——這對 fuzzer 是好消息（到處都可能找到），對想靠 code review 清乾淨的維護者是壞消息（沒有單一的「把這個函式寫對就好」）。

取捨的另一面是**停用成本**。eBPF 的非特權使用可以關掉、netfilter 的個別模組可以不載入，但 io_uring 是給應用拿效能用的，關掉它等於放棄一整套 async I/O 的效能優勢。這就是為什麼「要不要停用 io_uring」是個真實的、有痛感的決策，而不是無腦 harden——Ch 39 專門談這個取捨。

### 為什麼 libaio 沒這麼慘，io_uring 卻有

一個很有啟發性的對照：Linux 老的非同步 I/O 介面 `libaio`（`io_submit`/`io_getevents`）也是 async，為什麼它沒變成漏洞溫床？差別恰恰點出 io_uring 的攻擊面本質：

| 面向 | libaio | io_uring |
|---|---|---|
| 支援的操作 | 基本上只有檔案 direct I/O | 檔案、網路、poll、timeout、splice、uring_cmd…幾乎全部 |
| 共享記憶體 | 無（都走 syscall 傳參） | SQ/CQ 環與 user 共享（double-fetch 面） |
| 執行 context | 相對單純 | io-wq / task_work / SQPOLL / poll 回呼多路 |
| 物件與 refcount | 少 | 大量、多層、可跨 ring 共享 |
| 演進速度 | 近乎停滯 | 每個 release 擴張 |

libaio 的攻擊面小，是因為它**做得少**——操作少、不共享記憶體、執行路徑單純。io_uring 的攻擊面大，是因為它**做得多**——它想成為「所有 I/O 的統一 async 介面」，於是把本章五個原因全部拉滿。這個對照說明了一件事：io_uring 的漏洞密度不是實作品質問題，而是**野心的代價**。一個想涵蓋所有 I/O、又要極致效能、又要非同步的介面，必然站在生命週期與並發正確性最難的位置。理解這點，你對「io_uring 為什麼是溫床」就有了最根本的答案，也對「為什麼修不完」有了合理的預期。

## Google 的反應：一個產業級的風向標

把前面的分析放到真實世界：Google 在 2023 年的 kernelCTF 數據裡發現，**過去一年約 60% 的有效 submission 利用的是 io_uring**，為此付出約 100 萬美元賞金；io_uring 漏洞被用在**所有**成功繞過其 mitigation 的 submission 裡。Google 的回應是分產品採取不同程度的封鎖：

- **ChromeOS**：直接停用 io_uring，直到找到能妥善 sandbox 它的方法。
- **Android**：不整個關，而是用 seccomp-bpf filter 讓一般 app **搆不到** io_uring，並規劃用 SELinux 把存取限制在少數系統程序。
- **Google 生產伺服器**：停用。

這組決策本身就是本章論點的最強背書：一個**以效能為核心賣點、由 kernel 社群積極開發**的子系統，被幾個世界級的安全團隊評估為「風險高到值得整個關掉」。這不是因為 io_uring 寫得特別爛，而是因為它所在的設計空間（高並發 async + 複雜生命週期 + 強原語）天生難寫對。同一份數據也解釋了為什麼 kernelCTF 一度把 io_uring 排除在某些賽道之外——它實在太多產。Ch 39 會把這些決策翻成「你該怎麼為自己的系統做同樣的取捨」。

## 踩雷集錦（分析時常見誤解）

1. **以為「io_uring 漏洞多」等於「io_uring 程式碼品質差」。** 不是。它的維護很積極、review 很嚴、fuzzer 覆蓋很密。漏洞多是因為它所處理的問題（高並發非同步生命週期）本質困難，不是因為人不用心。把「困難」誤讀成「粗糙」，你會低估對手、也會錯判防禦策略。

2. **以為關掉某個 opcode 就安全了。** 攻擊面是整條生命週期路徑，不是單一 opcode。多數 UAF/race 發生在所有 opcode 共用的提交、完成、取消、teardown 路徑上，跟你用哪個 op 關係不大。真正的粗粒度緩解是停用整個 `io_uring_setup`（Ch 39），不是挑 opcode。

3. **把 CQE 的 `res` 當成唯一的錯誤通道，忽略 async 錯誤路徑。** io_uring 的錯誤常常發生在 error path（分配失敗、cancel、fd 失效）上，而這些路徑正是 refcount 最容易算錯的地方。分析時盯著 happy path 看不到洞，洞幾乎都在 `goto fail` 之後。

4. **以為 `READ_ONCE` 到處都有就沒有 double-fetch 問題了。** `READ_ONCE` 只保證「單次讀不被編譯器拆成多次」，它**不**保證「我讀到的值和我稍後用的值一致」。如果一條路徑讀了欄位、做了檢查、稍後又從 user 記憶體重讀一次，中間 user 改了值，照樣是 double-fetch。紀律是「驗證後用的是同一份拷貝」，不是「每個讀都加 `READ_ONCE`」。

5. **把 io_uring 的 refcount 想成 glibc 式的、每物件都有。** io_uring 的請求 refcount 是**按需啟用**的（`REQ_F_REFCOUNT`）。分析一段路徑時，先問「這個請求此刻有沒有啟用 refcount」，答案會改變你對「這次 put 會不會釋放物件」的判斷。假設它一定有 refcount，你會誤判生命週期。

6. **把 io_uring 的漏洞密度和它的「功能價值」對立起來。** 有人會得出「io_uring 這麼多洞，根本不該用」的結論。這跳過了取捨：io_uring 帶來的效能對某些工作負載是數量級的，而它的風險可以用 Ch 39 的分級手段管理。正確的框架不是「用 vs 不用」的二元對立，而是「這個系統需不需要它、需要的話怎麼把攻擊面控制到可接受」。把安全分析變成一刀切的否定，既不準確也不實用——防禦者的價值在於做出有依據的取捨，不是喊停。

## 進階：怎麼用這張地圖讀一個新 CVE

拿到一個陌生的 io_uring CVE，照這張地圖走：

1. **先定位 opcode / 機制**：它牽涉哪個 op？是舊機制還是新機制（原因四，新機制更可疑）？
2. **畫執行流**：這個請求會走 inline / io-wq / poll / task_work 哪幾條？有沒有跨 context（原因一）？
3. **數 refcount**：涉及哪些 refcounted 物件（原因二）？patch 改的是 get 還是 put？在哪條路徑上（原因三，通常是 error path）？
4. **找時間窗**：有沒有 user 可控的記憶體在「檢查」和「使用」之間（double-fetch），或「完成」和「取消」之間（Ch 33）？
5. **歸類**：落在五大類的哪一格？導致的後果（UAF / OOB / …）是哪一類？

這五步就是 Ch 34–37 每一個案例拆解的隱含流程。把它內化，你讀第五個 CVE 的速度會是第一個的好幾倍。

### 一個微型推演：五個原因怎麼在一個 bug 裡同時出現

把方法走一遍。假設我們讀到一個（示意的）bug：某個走 io-wq 的 opcode，在 cancel 與完成同時發生時 UAF。照五步：

1. **opcode / 機制**：它會 punt 到 io-wq（原因一的多執行流立刻在場）。
2. **執行流**：請求在 worker thread 上執行，同時 user 下了 cancel——兩條路爭同一個 `io_kiocb`（原因三的所有權轉移）。
3. **refcount**：worker 接手時 `__io_req_set_refcount(req, 2)`（Ch 32 模式二），但若 cancel 路徑的 error branch 多 put 了一次，計數提早歸零（原因二）。
4. **時間窗**：cancel 和完成的重疊，就是「完成 vs cancel」的 race（Ch 33 模式 B）觸發了那個多餘的 put。
5. **歸類**：race（觸發）導致 refcount 失衡（原因），造成 UAF（後果）——落在「race 觸發的 UAF」，典型的複合型。

注意五個結構性原因**不是五種獨立的 bug**，而是在**同一個** bug 裡層層疊加：多執行流（一）創造了所有權轉移（三）的需求，轉移靠 refcount（二）維持，race 在 error path 上打破了 refcount，而這一切之所以值得攻擊者investigate，是因為原語夠強（五）、新機制還在長（四）。看懂「一個 bug 同時是五個原因的交會」，你就真正掌握了這張地圖。

## 本章重點整理

- io_uring 的漏洞不是運氣問題，是五個結構性原因的產物：**非同步多執行流**、**大量糾纏的引用計數物件**、**反覆轉移的所有權**、**快速演進的新碼**、**強大易利用的原語**。
- 核心直覺：同一個 `io_kiocb` 可能同時被提交執行緒、io-wq、poll、task_work 碰到，每個交接點都是一次生命週期轉移，錯一個就是 UAF 或 race。
- 正確性靠**人工維持的紀律**（按需 refcount、`READ_ONCE`、error path 的 get/put 平衡），不是型別系統強制的保證；紀律一鬆就是 CVE。
- 漏洞分五大類：**UAF**（Ch 32）、**race/TOCTOU**（Ch 33）、**OOB**（Ch 36）、**type confusion**（Ch 34）、**邏輯/權限**，且真實 CVE 常是多類複合。
- Google 的數據（kernelCTF 約 60% submission、ChromeOS/Android/伺服器停用）是這套分析的產業級背書，也引出 Ch 39 的停用取捨。

## 自我檢核

- [ ] 我能不看書說出 io_uring 漏洞的五個結構性原因，並各舉一個對應的漏洞類別。
- [ ] 我能解釋「同一個請求跨 context」為什麼是 UAF 與 race 的共同根源。
- [ ] 我能說明 io_uring 的請求 refcount 為什麼是「按需啟用」，以及這個設計為什麼是雙面刃。
- [ ] 我能解釋 `READ_ONCE` 解決了什麼、沒解決什麼，以及 double-fetch 的真正定義。
- [ ] 拿到一個陌生的 io_uring CVE，我能照五步地圖把它歸類並定位 root cause 的大致位置。

## 延伸閱讀

### 權威數據與產業決策

- **[The io_uring Security Story（Google Security Blog / kernelCTF 相關報導）](https://security.googleblog.com/)**
  - **讀哪裡**：io_uring 佔 kernelCTF submission 比例、ChromeOS/Android/伺服器的停用決策那幾段
  - **和本章關聯**：本章「Google 的反應」一節的一手來源，也是五大結構性原因的真實世界驗證
- **[Google Limiting io_uring Use Due To Security Vulnerabilities（Phoronix）](https://www.phoronix.com/news/Google-Restricting-IO_uring)**
  - **讀哪裡**：整篇；對 Google 跨產品封鎖決策的中立整理，附社群討論連結

### 學術系統性分類

- **《Understanding and Detecting Harmful Memory-based Vulnerabilities in io_uring》**（搜尋標題，arXiv）
  - **讀哪裡**：漏洞分類與生命週期分析兩節，和本章五大類互相印證
  - **前提**：本章讀完再看，會對它的分類法特別有感

### 一手原始碼（最終仲裁）

- **[Linux 6.18 `io_uring/io_uring.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c)** 與 **[`refs.h`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/refs.h)**
  - **讀哪裡**：`io_get_sqe` 上方的 double-fetch 註解、`refs.h` 的 `req_ref_*` 與 `REQ_F_REFCOUNT` 判斷
  - **注意**：本章引用的兩個「證據」都在這兩個檔案，自己 grep 一遍印象最深

### 背景脈絡

- **[The rapid growth of io_uring（LWN）](https://lwn.net/Articles/810414/)** 與 LWN io_uring 系列 — Jonathan Corbet
  - **讀哪裡**：各新機制「何時加入、為什麼加」的報導，對應本章「原因四：演進極快」

---

這一章我們畫完了地圖：知道 io_uring 為什麼是溫床、漏洞分哪幾類、怎麼用結構性視角讀一個 CVE。接下來兩章把地圖上最大的兩塊填滿——先是 UAF。下一章我們把「引用計數錯誤如何變成 UAF」拆到可以在 code review 裡辨識的程度。

→ [Ch 32 UAF 模式總論：引用計數陷阱](./32-uaf-patterns.md)
