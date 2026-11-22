# Ch 40 — 防禦者視角：審計與 syzkaller fuzzing

> **目標**：把 Part 4 前九章的辨識能力收束成一套**可持續的防禦工作流**。我們要回答三個防禦者的實作問題：怎麼**人工審計** io_uring 程式碼找 bug（查引用計數、async 路徑物件生命週期、user memory double-fetch）？syzkaller 怎麼**自動 fuzz** io_uring、KASAN/KCSAN 扮演什麼角色？怎麼**讀 patch/commit** 持續學習新的 bug 類型？這章是 Part 4 的收口，把「看懂一個漏洞」升級成「主動找出下一個」。

> **環境**：對照 Linux 6.18 `io_uring/`（WSL2 6.18.58.1）。本章是方法論與工具鏈導讀，不含可執行攻擊碼。fuzzing 與 sanitizer 的動手實作本課不要求在 WSL 跑完整 syzkaller（需要可控 VM/自建 kernel），而是給出可驗證的方法路徑。所有工作以建立防禦能力為目的。

## 為什麼防禦者需要「主動」而不只是「堵」

Ch 39 教的是「堵」——用現成開關把 io_uring 關掉或限制。但堵有兩個天花板：一是堵掉就沒了效能（很多系統真的需要 io_uring），二是堵只能防已知的、能被粗粒度開關覆蓋的風險。真正讓一個子系統長期變安全的，是**持續把 bug 找出來修掉**——這是維護者、安全研究者、藍隊共同的工作，也是本課想把你訓練成的終極角色。

io_uring 的 bug 找法分三條互補的路：

```
 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │ 人工審計      │   │ 自動 fuzzing │   │ 讀 patch 學習 │
 │ (read code)  │   │ (syzkaller)  │   │ (commit 分析) │
 ├──────────────┤   ├──────────────┤   ├──────────────┤
 │ 懂語意、抓邏輯│   │ 覆蓋廣、跑得快│   │ 從別人修的 bug│
 │ 洞；慢、靠人   │   │ 抓淺層記憶體 │   │ 學新 bug 類型 │
 │              │   │ bug；不懂語意 │   │ 站在巨人肩上  │
 └──────────────┘   └──────────────┘   └──────────────┘
        └──────────────┬───────────────────┘
          三者互補：fuzzer 找淺的、人審找深的、
                    讀 patch 讓你知道該找什麼
```

三條路各有盲區，合起來才完整。這一章就把這三條路各講透一節。

## 第一條路：人工審計 io_uring 程式碼

人工審計的優勢是 fuzzer 沒有的：**它懂語意**。fuzzer 能發現「這個輸入讓 kernel KASAN 報 UAF」，但說不出「因為這條 error path 少了一次 `req_ref_get`」。後者需要人讀懂程式碼的意圖。Ch 32、33 已經給了四個 UAF 模式和三個 race 模式，這裡把它們整理成一套**可執行的審計流程**。

### 審計切入點：跟著「一個請求的一生」走

審計 io_uring 不要漫無目的地讀檔案，而是**挑一個 opcode，跟著它的請求走完整條生命週期**，在每個關鍵點套用 Ch 32/33 的檢查。路線圖（對照 Part 3 的 Ch 21–30）：

```
 prep (io_init_req)  ──►  issue (io_issue_sqe)  ──►  async?  ──►  complete  ──►  free
   │                        │                        │             │             │
   查：SQE 欄位是否         查：拿了哪些資源          查：接手有     查：填 CQE    查：所有
   都 READ_ONCE 單讀？      node？lookup→refs++       get、歸還有    的時機與      refcount
   有無 double-fetch？      在同一 lock 內？          put 嗎？       ownership？   收支平衡？
                                                     sqe_copy 了嗎？              error path?
```

### 三張審計檢查表

把前面章節的辨識要點濃縮成三張可逐條打勾的表。拿到一段 io_uring 程式碼就照表走。

**表一：引用計數（對應 Ch 32）**

- [ ] 這個請求會被第二方引用嗎（掛 list、排 work、存進別人欄位）？有的話，引用前有 `io_req_set_refcount` / `req_ref_get` 嗎？
- [ ] **每一條**退出路徑（成功、各種 `goto fail`）都有對應的 put 嗎？收支平衡嗎？
- [ ] 牽涉的 rsrc node：`lookup → node->refs++` 在**同一個 `uring_lock` 臨界區**內嗎？（對照 `io_find_buf_node`）
- [ ] 牽涉可共享物件（`io_mapped_ubuf` 等）：所有 share 的 `refcount_inc` 和所有釋放的 `refcount_dec_and_test` 嚴格配對嗎？跨 ring teardown 安全嗎？
- [ ] 版本確認：這段 rsrc 程式碼是 6.18 的 lock 保護 `int`，還是你在看舊版的 RCU/`percpu_ref`？（別把分析套錯版本）

**表二：async 路徑生命週期（對應 Ch 32 模式二）**

- [ ] 這個 opcode 會走 io-wq 或 task_work 嗎？
- [ ] 接手處（如 `io_wq_submit_work`）有 `req_ref_get` / 設 refcount，歸還處（`io_wq_free_work`）有對應 put 嗎？
- [ ] 用到的每個 `REQ_F_*` flag，「設 flag」和「配置物件」成對、「清 flag」和「釋放物件」成對嗎？（對照 `io_clean_op`）
- [ ] error path 和 fallback 路徑（task exit 時的 `io_fallback_tw`）的 refcount 收支，和正常路徑一致嗎？

**表三：user memory double-fetch（對應 Ch 33 模式 A）**

- [ ] 每個 SQE 欄位都只透過 `READ_ONCE` 讀一次嗎？
- [ ] 有沒有欄位被讀了、檢查了、稍後又重新從 user 記憶體讀一次？（檢查用的值 ≠ 使用的值）
- [ ] 走 async 的路徑，轉 async 前有正確的 `sqe_copy`（`REQ_F_SQE_COPIED`）嗎？
- [ ] SQPOLL 路徑：有沒有假設「user 提交後不改 SQE」？（SQPOLL 下這個假設不成立）

### 走一遍：用檢查表審 `READ_FIXED` 的 buffer 查找

把三張表套到一條真實路徑上。`IORING_OP_READ_FIXED`（用 registered buffer 的讀）在 issue 時要查出對應的 buffer node，走 `io_find_buf_node`（`rsrc.c`）：

```c
inline struct io_rsrc_node *io_find_buf_node(struct io_kiocb *req,
					     unsigned issue_flags)
{
	struct io_ring_ctx *ctx = req->ctx;
	struct io_rsrc_node *node;

	if (req->flags & REQ_F_BUF_NODE)
		return req->buf_node;          // 已找過，直接用快取
	req->flags |= REQ_F_BUF_NODE;

	io_ring_submit_lock(ctx, issue_flags);
	node = io_rsrc_node_lookup(&ctx->buf_table, req->buf_index);
	if (node) {
		node->refs++;                  // ← lookup 成功立刻 +1
		req->buf_node = node;
		io_ring_submit_unlock(ctx, issue_flags);
		return node;
	}
	req->flags &= ~REQ_F_BUF_NODE;
	io_ring_submit_unlock(ctx, issue_flags);
	return NULL;
}
```

拿表一（引用計數）逐條打勾：

- **「`lookup → node->refs++` 在同一個 lock 臨界區嗎？」** 是。`io_ring_submit_lock` 到 `node->refs++` 到 `unlock` 是一段完整臨界區，lookup 和 get 之間沒有放鎖——這條過關。如果有人把 `node->refs++` 挪到 unlock 之後，中間 node 就可能被 `io_reset_rsrc_node` 換掉釋放，變成 Ch 32 模式三的 UAF。
- **「`req->buf_index` 怎麼來的、可信嗎？」** 它在 `io_init_req` 由 `READ_ONCE(sqe->buf_group)` 設（表三 double-fetch 關注點），而 `io_rsrc_node_lookup` 裡用 `array_index_nospec` 擋了 Spectre-style 的越界推測——這條也過關。
- **「拿了 node 的這一次 `refs++`，對應的 put 在哪？」** 往下追：請求完成/清理時要對 `buf_node` 做 `io_put_rsrc_node`。表一要求「每條退出路徑都平衡」——這就要去看 `io_find_buf_node` 回 `NULL`（lookup 失敗）那條路有沒有少 put（它沒 `refs++`，所以也不該 put，平衡），以及拿到 node 後若 issue 失敗的 error path 有沒有正確 put。**這一步就是審計的真正工作**：不是看這個函式對不對，而是看**所有拿了這個 ref 的路徑有沒有都還回去**。

這一遍走下來，你會發現：單看 `io_find_buf_node` 本身寫得很乾淨，嫌疑點不在它，而在「它的呼叫者在各種 error path 上有沒有守住 put」。**這正是 io_uring 審計的典型形狀——bug 很少在單一函式裡，而在跨函式的 refcount 收支上。** 檢查表的價值就是逼你問對問題、追到對的地方。

### 哨兵即路標：跟著 WARN 和註解走

Ch 31、32、33 反覆強調：kernel 自己埋的 `WARN_ON_ONCE`、`lockdep_assert_held`、以及長註解，標的正是開發者認定的易錯點。審計時把這些當**路標**——它們告訴你「這裡歷史上錯過、或很容易錯」。例如 `refs.h` 裡每個 `req_ref_*` 都圍著 `WARN_ON_ONCE(!(req->flags & REQ_F_REFCOUNT))`，`io_put_rsrc_node` 開頭的 `lockdep_assert_held(&ctx->uring_lock)`，`io_poll_double_prepare` 那段講 race 的長註解。**grep 這些哨兵，就是 grep 出「該重點看的地方」。**

6.18 還有一類防禦性設計值得審計者認得：`io_poison_req` 把釋放後的快取請求的關鍵指標填成 `IO_URING_PTR_POISON`。這對審計的意義是——如果你在 review 時懷疑某條路徑會在請求回收後還 deref 它，poison 讓這種錯誤在測試時**立刻崩潰**而非悄悄被利用，所以搭配 sanitizer 跑測試能把你的懷疑變成可觀察的證據。

## 第二條路：syzkaller 怎麼 fuzz io_uring

人工審計慢且依賴人力。覆蓋廣度上，**syzkaller**（Google 的 coverage-guided kernel fuzzer）是 io_uring bug 的最大來源之一。防禦者要懂它怎麼運作，才能讀懂它的 report、甚至跑自己的。

### syzkaller 的基本迴圈

```
 ┌─────────────────────────────────────────────────────────┐
 │ 1. 依 syscall descriptions 生成/變異一串 syscall 呼叫     │
 │ 2. 在 VM 裡執行這串呼叫                                  │
 │ 3. 用 kcov 收集 kernel 走過的程式碼覆蓋率                 │
 │ 4. 覆蓋率變高的輸入 → 留進語料庫，繼續變異               │
 │ 5. KASAN/KCSAN 等 sanitizer 一旦報錯 → 產生 crash report │
 └─────────────────────────────────────────────────────────┘
```

關鍵在第 1 步的 **descriptions**。syzkaller 不是盲射——它靠一份用 syzlang 寫的「syscall 長什麼樣」的描述，知道 `io_uring_setup` 的參數結構、`io_uring_enter` 怎麼配合、SQE 有哪些欄位和合法值。io_uring 的 descriptions 在 syzkaller 的 `sys/linux/io_uring.txt` 一類檔案裡，描述了 SQE 的 opcode、flags、各 op 的參數佈局。descriptions 寫得越準，fuzzer 越能生成「結構上合法、語意上刁鑽」的輸入，越能鑽進深路徑。

這也解釋了 io_uring 為什麼是 syzkaller 的高產區（呼應 Ch 31 原因四）：**每加一個新 opcode / 新機制，就要補一段 descriptions，而新路徑往往還沒被充分 fuzz**。社群維護 io_uring 的 descriptions 是一個持續的工作，新機制的描述補上後，syzbot 常常很快就在新路徑上報出 bug。

descriptions 大致長這樣（示意，說明結構而非逐字）：它用 syzlang 宣告 `io_uring_setup` 回傳一個 fd resource、`io_uring_enter` 吃那個 fd，並描述 SQE 的欄位與各 opcode 的參數型別。因為 syzkaller 知道「`io_uring_setup` 產出的 fd 要餵給 `io_uring_enter`」這種**資源依賴關係**，它才不會盲目亂叫，而能生成「先 setup、register 一個 buffer、再用那個 buffer 發 READ_FIXED」這種有狀態、有順序的呼叫串——這正是觸發深層 io_uring 路徑所必需的。descriptions 寫得越完整（涵蓋越多 opcode、越準的欄位約束、越對的資源依賴），fuzzer 能到達的深度就越深。反過來，**一個新機制如果 descriptions 還沒補，fuzzer 基本上碰不到它**——這段「描述空窗期」正是新機制 bug 容易逃過自動化、要靠人工審計補位的時候。

### syzbot：讀懂現成的 report

你不一定要自己跑 syzkaller——**syzbot** 持續在 mainline 上跑 syzkaller 並公開所有 report。對 io_uring 防禦者，syzbot 是一座金礦：

- 到 syzbot dashboard 篩 io_uring 相關的 report，能看到**當前還 open 的、以及已修的** io_uring bug。
- 每個 report 附**reproducer**（C 或 syzlang 程式）、**crash 的 KASAN/KCSAN 輸出**、以及（修了的話）**對應的 fix commit**。
- 讀一個 report 的流程：看 KASAN 說什麼類型（UAF / OOB / …）→ 看 call stack 落在哪個 io_uring 函式 → 用 Ch 32/33 的模式歸類 → 看 reproducer 怎麼觸發 → 看 fix 改了什麼。

這個流程把 Part 4 的辨識框架和真實 bug 直接對接。**一個很好的練習是：挑一個已修的 io_uring syzbot report，先只看 reproducer 和 KASAN 輸出，自己用四個 UAF 模式 / 三個 race 模式推 root cause，再去對 fix commit 驗證你推對沒有。**

### KASAN 與 KCSAN 的分工

syzkaller 自己不判斷「記憶體壞了」——那是 sanitizer 的事。兩個對 io_uring 最關鍵：

| sanitizer | 抓什麼 | 對 io_uring 的意義 |
|---|---|---|
| **KASAN** (Kernel Address Sanitizer) | UAF、OOB、double free 等**記憶體錯誤** | io_uring 最大宗的 UAF/OOB 幾乎都由 KASAN 抓出並定位到具體物件與釋放堆疊 |
| **KCSAN** (Kernel Concurrency Sanitizer) | **data race**（沒正確同步的並發讀寫） | io_uring 的 race（Ch 33）很多是 data race，KCSAN 能在沒崩潰時就報出「這兩個存取沒同步」 |

兩者互補：KASAN 抓「已經壞掉的後果」（UAF 發生了），KCSAN 抓「可能導致壞掉的原因」（並發存取沒同步，即使這次沒壞）。對 io_uring 這種並發密集的子系統，KCSAN 特別有價值——它能在 race 還沒惡化成 UAF 時就標出 Ch 33 說的那些「沒被正確序列化的重疊」。防禦者自建測試 kernel 時，`CONFIG_KASAN` 幾乎必開，追 race 時加開 `CONFIG_KCSAN`。

> **實作路徑（本課不在 WSL 跑完整 fuzzer）**：完整 syzkaller 需要一個可控的 VM 和自建的 instrumented kernel（開 `CONFIG_KASAN`/`CONFIG_KCOV`），這超出 WSL 的範圍，屬於本 repo kernel_pwn Ch 43 的工具鏈主題。本章的目標是讓你**讀懂 syzbot report、知道 descriptions 與 sanitizer 各自的角色**，具備把一個 report 拆到 root cause 的能力；真要自架 fuzzer，照 kernel_pwn Ch 43 的環境走。

### 實作導讀：怎麼讀一份 KASAN UAF report

把「讀 report」講具體。一份典型的 io_uring KASAN UAF report（以下為**示意結構**，說明各段怎麼讀，非任何真實漏洞的可複製內容）長這樣：

```
BUG: KASAN: use-after-free in <某個 io_uring 函式> ...
Read of size 8 at addr ffff888... by task ...
                                  ↑ ① use 的現場：誰、讀還是寫、幾 bytes
Call Trace:
  <某 io_uring 函式>          ← ② use 發生在哪條路徑（通常是 complete/poll/cancel）
  ...
Allocated by task N:            ← ③ 這塊記憶體當初在哪配置（物件是什麼）
  kmem_cache_alloc / io_...
Freed by task M:                ← ④ 關鍵！它在哪裡、被誰 free 的
  kfree / io_free_req / ...
```

讀的順序**不是**由上而下，而是：

1. **先看第 ① 行的類型**：`use-after-free`？`out-of-bounds`？`double-free`？這決定你用 Ch 32（UAF）還是 Ch 36（OOB）的框架。
2. **看第 ④ 段「Freed by」**：這才是 root cause 的鄰居。UAF 的病因是「這個 free 太早了」，所以 free 的堆疊告訴你**是哪條路徑釋放了它**——error path？cancel？teardown？對照 Ch 32 的四個模式。
3. **看第 ③ 段「Allocated by」**：確認壞掉的是什麼物件（`io_kiocb`?`io_mapped_ubuf`?），這決定它屬於哪一層 refcount。
4. **最後才看第 ② 段的 use 堆疊**：它是「崩潰現場」不是「病因」。別被它誤導——use 在 complete 路徑，不代表 bug 在 complete 路徑，bug 在那個「太早的 free」。
5. **對照 reproducer**：看 user 用什麼 opcode / flag / 時序觸發，反推到被修的路徑。

這個「先類型、再 free、再 alloc、最後 use」的讀法，是把一份機器產生的 report 翻成 Ch 32/33 模式語言的標準流程。練熟它，你讀 syzbot 的速度會快一個量級。

## 第三條路：讀 patch / commit 學新 bug 類型

第三條路最被低估，卻是進步最快的：**讀 io_uring 的 bug fix commit**。每一個修 bug 的 commit 都是一份免費的、權威的教材——它告訴你一個真實的 bug 長什麼樣、root cause 在哪、正確的修法是什麼。讀多了，你的審計直覺會直接升級，因為你在**別人踩過的坑上建立模式庫**。

### 怎麼找 io_uring 的 bug fix commit

```bash
# 在 linux 原始碼樹裡，看 io_uring/ 的提交史，篩出像在修 bug 的
git log --oneline -- io_uring/ | grep -iE 'fix|race|uaf|use-after|leak|double|overflow|cancel|teardown'
```

### 從 commit 嗅出 bug 類型（延續 Ch 33 的嗅覺）

讀一個 io_uring fix commit，照這個流程拆：

1. **讀 commit message**：它通常直說 root cause。關鍵詞 `race`/`UAF`/`use-after-free`/`double free`/`leak`/`overflow`/`refcount` 直接告訴你類別。
2. **讀 diff 的形狀**：
   - 補了一個 `req_ref_get` 或移動了一個 put → Ch 32 的 refcount 模式。
   - 動了 `READ_ONCE`/`WRITE_ONCE` 或新增 `sqe_copy` → Ch 33 的 double-fetch 模式。
   - 動了 `poll_refs` / ownership / 某個 lock 的範圍 → Ch 33 的 completion vs cancel 模式。
   - 改在 `goto fail` 之後、cancel 路徑、teardown 路徑 → error/teardown 高發區。
3. **用模式歸類**：把它落到四個 UAF 模式 / 三個 race 模式之一。
4. **反推觸發**：想「user 要怎麼構造請求才能走到這條被修的路徑」——這就接到了 Ch 34–37 案例拆解和練習 E 的 PoC 構造。
5. **擴散**：問「同樣的錯誤模式，io_uring 裡還有沒有別的地方也這樣寫？」——這是從「讀懂一個 fix」到「主動找下一個 bug」的跳板，也是真實安全研究者的核心手法。

第 5 步是關鍵。很多 io_uring CVE 是「某人讀了一個 fix，發現同樣的 pattern 在另一個 opcode 裡沒被修」而發現的。**fix commit 不只教你這一個 bug，它教你一整類 bug 該往哪裡找。**

### 把三條路接起來

三條路不是平行的，是一個循環：

```
   讀 patch ──► 學到一個 bug 模式 ──► 人工審計時拿這個模式去掃其他路徑
      ▲                                          │
      │                                          ▼
   syzbot report ◄──── fuzzer 在你沒掃到的淺層路徑上補洞 ◄──── 發現新嫌疑
```

讀 patch 給你模式，人工審計拿模式去深路徑找，fuzzer 在你顧不到的淺路徑上掃，syzbot 把 fuzzer 的成果變成新的 patch 讓你再學。防禦者的持續工作流就是在這個循環裡轉。

## 對比與取捨：三條路的分工

| 路徑 | 強項 | 盲區 | 成本 |
|---|---|---|---|
| **人工審計** | 懂語意、抓深層邏輯/生命週期 bug | 慢、靠人、容易疲勞漏看 | 高（人力與專注） |
| **syzkaller / syzbot** | 覆蓋廣、跑得快、抓淺層記憶體 bug | 不懂語意、深路徑難觸發、依賴 descriptions | 中（讀 report 免費，自架高） |
| **讀 patch** | 權威、高效學新模式、找同類擴散 | 只能學「已被發現」的 bug | 低（最划算） |

沒有哪一條能單獨搞定 io_uring。維護者靠 syzbot 擋住淺層洪水、靠人工審計守住深層邏輯、靠互讀 patch 傳播模式。防禦者（即使不修 kernel）要懂這個分工，才能正確評估「一個 io_uring CVE 的真實風險」——它是 fuzzer 隨便就能撞到的淺洞，還是要懂語意才能構造的深洞？這個判斷直接影響你該多緊張、多快打 patch。

## 踩雷集錦（防禦工作流常見誤解）

1. **以為 fuzzer 跑過就代表安全。** syzkaller 抓的是「它的 descriptions 能生成、且會觸發 sanitizer」的 bug。深路徑、需要精確時序的 race、語意邏輯洞，fuzzer 常常碰不到。fuzzer clean 不等於 code clean。

2. **審計時只讀 happy path。** 重申 Part 4 的鐵律：io_uring 的 UAF/race 幾乎都在 error / cancel / teardown / fallback 路徑。從 `goto fail` 之後開始讀，比從函式開頭讀效率高得多。

3. **讀 patch 只滿足於「看懂這一個」。** 真正的價值在第 5 步——把這個 bug 的模式拿去掃 io_uring 裡的其他路徑。停在「我懂這個 CVE 了」，就浪費了 patch 最大的教學價值。

4. **忽略 KCSAN，只信 KASAN。** KASAN 抓已經壞掉的（UAF 發生了），但 io_uring 的很多 race 在「還沒惡化成 UAF」時就已經是 data race。追並發 bug 不開 KCSAN，等於只在事後驗屍，不做事前偵測。

5. **把 syzbot report 的 KASAN stack 當成 root cause。** KASAN 的 stack 是「壞掉的現場」（use 的那一刻），root cause 常在**別處**（那個錯誤的 free，或少掉的 get）。要往回推，別把「崩潰點」當「病因」。

6. **以為審計 io_uring 要先精通利用。** 不用。審計找的是「拍 1：錯誤的 free / 沒同步的存取」，那是生命週期與並發的推理，不需要你會把它打成提權。利用知識幫助你評估危害，但找 bug 靠的是讀懂語意。

7. **拿最新 mainline 的分析去套舊的 LTS kernel。** io_uring 演進極快，一段路徑在 6.18 的樣子可能和你實際在跑的 6.1 LTS 差很多（rsrc 的 RCU vs lock、某些 flag 的語意、opcode 的有無）。審計或評估一個 CVE 時，第一件事是對齊「目標系統實際跑的版本」，而不是預設 mainline。版本錯位會讓整個生命週期分析失準。

8. **把「讀懂了機制」當成「找得到 bug」。** 讀懂 io_uring 怎麼運作是必要條件，不是充分條件。找 bug 還需要「刻意用懷疑的眼光讀 error path、主動用模式庫比對、願意追跨函式的 refcount 收支」這套主動的工作習慣。被動地讀懂，和主動地找洞，是兩種不同的閱讀模式——Part 4 訓練的是後者。

## 進階：建立你自己的 io_uring bug 模式庫

把這一章變成長期能力的做法，是**維護一份自己的模式庫**。每讀一個 io_uring fix commit 或 syzbot report，記三件事：

- **觸發結構**：user 怎麼構造請求（哪個 opcode、什麼 flag 組合、什麼時序）。
- **root cause 模式**：落在四個 UAF / 三個 race 模式的哪一格，具體是哪種失衡（少 get？多 put？漏 `READ_ONCE`？繞過 ownership？）。
- **擴散線索**：同樣的 pattern 可能還藏在哪些 opcode / 路徑。

給模式庫開個頭，三條示範條目（形式，不是要你背）：

```
條目 1  類別：UAF / 模式二（async 生命週期）
  觸發：某 opcode punt 到 io-wq，執行中 ring teardown
  root cause：error path 少一次 req_ref_get
  擴散：所有會 punt 且有 goto fail 的 opcode 的 error branch

條目 2  類別：race / 模式 A（double-fetch）
  觸發：SQPOLL 下 user 持續改某個 SQE 欄位
  root cause：該欄位讀了兩次，驗證用第一次、使用用第二次
  擴散：所有在 prep 後、issue 時重讀 SQE 欄位的 opcode

條目 3  類別：UAF / 模式四（跨 ring）
  觸發：CLONE_BUFFERS 後兩 ring 各自 teardown
  root cause：共享 ubuf 的 refcount 在某條 error path 上失衡
  擴散：所有可共享物件（notif、registered ring）的 share/release 配對
```

這三條不是真實 CVE 的細節，而是**模式的骨架**——真正讀 syzbot / patch 時，你把具體的函式名、commit、觸發串填進這個骨架。累積起來的不是一堆孤立案例，而是一張「io_uring 的錯誤都長在哪」的活地圖。

累積幾十條，你再審計一段新程式碼時，掃過去就會自動和模式庫比對——這就是資深 io_uring 審計者和新手的差距：不是記憶力，是**模式庫的厚度**。Ch 34–37 的四個案例、以及接下來練習 E 的 PoC，都是往這個模式庫裡存貨的好起點。

### 如果你真的找到一個 bug：負責任揭露

本課的定位是防禦研究。如果你在審計或 fuzzing 中真的發現了一個 io_uring 的新 bug（不是已公開的），正確的下一步是**負責任揭露（responsible disclosure）**，而不是公開 PoC：

- **先確認是真 bug**：用 KASAN/KCSAN kernel 重現，確定不是你的測試環境問題；對照 mainline 最新版（可能已被悄悄修掉）。
- **回報給正確的對象**：io_uring 的 bug 走 `security@kernel.org`（kernel 安全團隊）與 io_uring 維護者；若影響發行版，也通報對應的 distro 安全窗口。mailing list `io-uring@vger.kernel.org` 是技術討論處，但**安全性 bug 不該先發公開 list**。
- **給出可重現但不武器化的材料**：一個最小 reproducer（觸發 KASAN 即可）足以讓維護者定位，不需要附帶完整的提權 exploit。
- **遵守 embargo**：kernel 安全流程有協調揭露的時間窗，在 fix 與 CVE 編號就緒前不公開細節。

這套流程本身就是防禦文化的一部分：找洞的能力要服務於「讓系統更安全」，而不是製造風險。本課教你辨識與重現 bug，正是為了讓你有能力走這條負責任的路。

### 把 Part 4 串成一條能力線

回頭看整個 Part 4，它其實是一條單向遞進的能力線：**辨識（Ch 31–33 的模式）→ 實證（Ch 34–37 的案例）→ 評估（Ch 38 的利用價值、Ch 39 的緩解）→ 主動（本章的審計與 fuzzing）**。這條線的終點不是「會打 io_uring」，而是「能持續地讓 io_uring 更安全」——能讀懂任一 CVE、能評估其真實風險、能審計找出同類、能正確地揭露。這才是資安教育真正要交付的東西，也是這門課把安全章節放在整本書壓軸的原因。

## 本章重點整理

- 防禦者找 io_uring bug 有三條互補的路：**人工審計**（懂語意、抓深層）、**syzkaller/syzbot fuzzing**（覆蓋廣、抓淺層記憶體 bug）、**讀 patch**（高效學新模式、找同類擴散）。
- 人工審計的方法：挑一個 opcode 跟著請求的一生走，在 prep/issue/async/complete/free 各點套用三張檢查表（引用計數、async 生命週期、double-fetch）；`WARN_ON_ONCE`/`lockdep_assert`/長註解是路標。
- syzkaller 靠 **descriptions** 生成結構合法的輸入，**kcov** 導向覆蓋率，**KASAN**（抓記憶體錯誤後果）與 **KCSAN**（抓 data race 原因）判斷壞掉；syzbot 公開現成 report + reproducer + fix，是防禦者的金礦。
- 讀 patch 的五步：讀 message 定類別 → 看 diff 形狀 → 用模式歸類 → 反推觸發 → **擴散**（同模式還藏在哪）。第五步是「讀懂一個」到「找出下一個」的跳板。
- 三條路構成一個循環，長期能力的核心是累積**自己的 bug 模式庫**（觸發結構 + root cause 模式 + 擴散線索）。

## 自我檢核

- [ ] 我能挑一個 io_uring opcode，跟著它的請求走完 prep→free，並在每個關鍵點說出該查什麼。
- [ ] 我能默寫三張審計檢查表的核心問題（引用計數、async 生命週期、double-fetch）。
- [ ] 我能解釋 syzkaller 的 descriptions、kcov、KASAN、KCSAN 各自的角色，以及為什麼 io_uring 是 syzbot 高產區。
- [ ] 我能拿一個已修的 io_uring syzbot report，只看 reproducer+KASAN 就用 Part 4 的模式推 root cause，再對 fix 驗證。
- [ ] 我能說出「讀 patch 五步」，特別是第五步「擴散」為什麼是找新 bug 的關鍵。

## 延伸閱讀

### fuzzing 與 sanitizer

- **[syzkaller 文件](https://github.com/google/syzkaller/blob/master/docs/linux/setup.md)** 與 **[syzbot dashboard](https://syzkaller.appspot.com/upstream)**
  - **讀哪裡**：syzkaller 的 descriptions（syzlang）章節；syzbot 上篩 io_uring 的 open/fixed report，挑一個已修的練 root cause 推導
  - **和本章關聯**：第二條路的一手工具與練習素材
- **本 repo [kernel_pwn Ch 43 Syzkaller / syzbot](../../security/kernel_pwn/43-syzkaller.md) 與 [Ch 44 debug 工具鏈](../../security/kernel_pwn/44-debug-toolchain.md)**
  - **讀哪裡**：自架 syzkaller 的完整環境、KASAN/KCSAN/KMSAN 的開法；本章刻意不在 WSL 跑的部分在這裡有完整實作路徑

### 一手原始碼（審計對象）

- **[Linux 6.18 `io_uring/`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring)**
  - **讀哪裡**：拿三張檢查表實際掃 `rw.c`、`net.c`、`poll.c`、`rsrc.c` 的任一 opcode；grep `WARN_ON_ONCE`、`lockdep_assert_held`、`READ_ONCE` 找路標
  - **用途**：審計不是讀文章，是讀 code；這是唯一的最終對象

### 讀 patch 學 bug

- **[io_uring git 提交史](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/log/io_uring)** 與 **[io-uring mailing list](https://lore.kernel.org/io-uring/)**
  - **讀哪裡**：篩 `fix`/`race`/`UAF` 的 commit，用「讀 patch 五步」拆；mailing list 看 bug 被討論與修正的完整脈絡
- **本 repo [kernel_pwn Ch 42 讀 patch 找 root cause](../../security/kernel_pwn/42-patch-to-rootcause.md)**
  - **讀哪裡**：patch diff → root cause 的通用方法論；本章第三條路是它在 io_uring 的具體化

### 學術系統性方法

- **《Understanding and Detecting Harmful Memory-based Vulnerabilities in io_uring》**（搜尋標題，arXiv）
  - **讀哪裡**：它的自動偵測方法與分類；和本章人工審計 + fuzzing 的分工互相印證

---

到這裡，Part 4 走完了一條完整的弧線：Ch 31 畫出攻擊面地圖，Ch 32–33 歸納 UAF 與 race 的通用模式，Ch 34–37 用真實 CVE 實戰，Ch 38 拆物件的記憶體配置與加固，Ch 39 把防禦開關攤開實測，Ch 40 把辨識能力收束成可持續的審計與 fuzzing 工作流。你現在有能力獨立讀一個 io_uring CVE、歸類它的 root cause、評估它的風險，並主動去找下一個。

把這套能力落到一次動手上——下一步是在受控環境裡，親手把一個最小的 UAF 從「辨識」走到「重現」，看 KASAN 真的報出來。那是驗證你是否真懂的試金石。

→ [練習 E 最小 UAF PoC（受控環境）](./practice-e-uaf-poc.md)
