# Ch 2 — 程式理解的認知模型：chunking、beacons、top-down 與 bottom-up

> **目標**：知道 1979–1995 年間程式理解研究提出的七個核心觀念——工作記憶與 chunking、syntactic／semantic 知識、Brooks 的假說驅動與 beacon、Soloway & Ehrlich 的 programming plan 與 discourse rule、Pennington 的 program model／situation model、Letovsky 與 Littman 等人的提問與策略研究、von Mayrhauser & Vans 的整合模型——並且每一個都能落成「所以讀 kernel 時該怎麼做」的具體動作。
> **鴻溝**：本章是五道鴻溝的理論底座，主要解釋**慣用法鴻溝**（為什麼熟手一眼認得）、**領域鴻溝**（為什麼需要 situation model）與**目標鴻溝**（為什麼要帶假說讀）。

## 為什麼需要這個？

Ch 1 的五道鴻溝回答了「你卡在哪」，但沒回答一個更根本的問題：**同一段 `eventfd_read()`，為什麼 maintainer 三十秒讀完，你要一個小時？** 他的眼睛沒有比較快，他的 C 也不一定比你好。差別在他腦中多了一些東西，讓他讀的方式根本不同。

這不是玄學。從 1970 年代末開始，一批認知心理學家和軟體工程研究者用實驗研究「程式設計師讀程式時腦中發生什麼事」：讓人讀程式、出聲思考（think-aloud）、事後回憶、修改程式，再分析熟手和新手的差異。他們得出的模型彼此有爭議，但幾個核心觀察經過四十年仍然站得住。

為什麼讀碼課要花一整章講理論？因為「多讀就會了」這句建議是對的，但不完整——**知道熟手腦中多了什麼，你就能刻意去建立那些東西**，而不是等它自然長出來。本章每一節的結尾都會落到一個具體動作。

> 認識論誠實：本章引用的研究多數在 1980 年代完成，受試者讀的是幾十到幾百行的 Pascal、COBOL、FORTRAN，不是兩千萬行的 kernel。von Mayrhauser & Vans 1995 年的研究才開始觀察大型程式碼的維護工作。把這些結論套到 kernel 是**合理外推，不是實驗證明**。本章會標出哪些地方外推得比較遠。

## 先建立直覺：西洋棋大師的記憶

在程式理解研究之前，認知心理學有一個著名的發現。Chase 與 Simon 在 1973 年（延續 de Groot 更早的研究）讓西洋棋大師和新手看一個棋盤幾秒鐘，然後憑記憶把棋子擺回去。

- 棋盤是**真實對局中的局面**：大師幾乎全對，新手只擺得出幾顆。
- 棋盤是**隨機亂擺的棋子**：大師和新手一樣差。

大師的記憶力沒有比較好。他記的不是「25 顆棋子各在哪」，而是「這是西西里防禦的某個變化，王車易位過了，c 線開放」——三四個**有意義的單位**，每個單位裡包著好幾顆棋子。棋子隨機擺時，這些單位不存在，大師也只能一顆一顆記。

```
新手看到的：                         大師看到的：
  ♜ at a8                             ┌─────────────────┐
  ♞ at b8                             │ 王車易位後的城堡 │  ← 1 個 chunk
  ♚ at g8                             ├─────────────────┤
  ♜ at f8                             │ 開放的 c 線      │  ← 1 個 chunk
  ♟ at f7, g7, h7                     ├─────────────────┤
  ♟ at c5 ...                         │ 西西里的兵形     │  ← 1 個 chunk
  （25 個獨立項目，記不住）           └─────────────────┘
                                      （3–4 個 chunk，輕鬆記住）
```

McKeithen 等人在 1981 年用程式碼重做了這個實驗，結果相同：有意義的程式，熟手記得遠比新手多；把同樣的程式碼行打亂順序，熟手的優勢大幅縮小。

**換成 kernel**：maintainer 看 `eventfd_read()` 時，看到的不是 32 行，而是「非阻塞檢查 → 等待條件成立 → 消耗計數 → 喚醒對面 → 複製到 user」五個 chunk。你看到的是 32 行、十四個卡點。本章剩下的部分，都是在解釋這些 chunk 是什麼、怎麼建立。

## 1. 工作記憶與 chunking：理解的硬體限制

人類的工作記憶（working memory）——你此刻在腦中「拿著」的東西——容量很小。Miller 1956 年那篇著名的論文說是「七加減二」個項目；Cowan 2001 年回顧後續研究，認為排除複誦等策略之後，真正的容量更接近**四個 chunk**左右。數字有爭議，但「很小」沒有爭議。

**chunk** 是一個可以被當成單一單位操作的意義組。關鍵在於：chunk 的大小不固定，取決於你的長期記憶裡有什麼。對新手，`spin_lock_irqsave(&ctx->wqh.lock, flags)` 是一個要拆解的陌生東西，佔掉好幾個槽位（這是什麼函式？`&ctx->wqh.lock` 是什麼？`flags` 從哪來？為什麼不用回傳值？）；對熟手，它是**一個** chunk：「關中斷、拿鎖、把中斷狀態存進 flags」。

讀 kernel 時工作記憶的典型崩潰方式：

```
你在讀 anon_pipe_write()                     工作記憶（約 4 槽）
  → 遇到 pipe_buf(pipe, head - 1)             [anon_pipe_write 在做什麼]
    → 跳去看 pipe_buf 的定義                    [head 是什麼]
      → 遇到 pipe->ring_size - 1 的 mask        [pipe_buf 回傳什麼]
        → 跳去看 ring_size 何時設定             [ring_size 為什麼是 2 的冪]
          → 遇到 round_pipe_size()              ← 第 5 項，最早那項被擠掉
            ...
回到 anon_pipe_write，已經忘了剛才要查什麼
```

這不是你笨，是工作記憶滿了。熟手不會遇到這個問題，因為 `pipe_buf(pipe, head - 1)` 對他是一個 chunk（「拿最後一個 slot」），根本不需要跳出去。

### 所以讀 kernel 時該怎麼做

1. **把工作記憶外部化**。跳進一個定義之前，在筆記寫一行「我在 anon_pipe_write:479，要查 pipe_buf 回傳什麼」。回來時看筆記，不靠腦子。Ch 7 會系統化這件事。
2. **限制遞迴深度**。跳出去查定義最多兩層。第三層還沒解決，就把它標成黑箱（「pipe_buf：拿 ring 裡第 n 個 slot，細節之後再說」），回到主線。Ch 4 講黑箱策略。
3. **刻意累積 chunk**。慣用法鴻溝本質上就是「缺 chunk」。Part 2、Part 3 一章一章把常見慣用法變成 chunk，就是在擴充你的長期記憶，讓同樣的工作記憶裝得下更多。

## 2. Syntactic 與 semantic 知識：兩種不同的「懂」

Shneiderman 與 Mayer 在 1979 年提出一個區分：程式設計師的長期記憶裡有兩種知識。

- **Syntactic 知識**（語法知識）：特定語言、特定專案的細節。`for` 迴圈怎麼寫、`spin_lock_irqsave` 的第二個參數是 lvalue、kernel 的錯誤碼慣例是回傳負的 errno。這類知識**任意、精確、要背**，換一個語言或專案就不適用。
- **Semantic 知識**（語意知識）：與語言無關的概念。「用迴圈走訪串列找符合條件的元素」「用鎖保護共享資料」「生產者／消費者」。這類知識**結構化、可遷移**，學會了到哪都能用。

他們的模型認為，理解程式就是把程式碼從 syntactic 表徵**重新編碼**成 semantic 表徵：看到一串語法，在腦中轉成「這是在做 X」。

| | syntactic 知識 | semantic 知識 |
|---|---|---|
| 例子 | `list_for_each_entry(pos, head, member)` 的參數順序 | 「走訪一個串列」 |
| 怎麼學 | 背、查、重複接觸 | 理解、類比、做中學 |
| 可遷移性 | 低：Redis 的 `listIter` 是另一套 | 高：哪裡的串列走訪都一樣 |
| 忘了怎麼辦 | 查文件 | 很難忘，忘了要重新理解 |
| 對應的鴻溝 | **慣用法鴻溝** | **領域鴻溝**（較高層的那部分） |

這個區分解釋了一個常見的挫折：你懂作業系統（semantic 知識夠），讀 kernel 還是卡（syntactic 知識不夠）。反過來也有：你把 kernel 巨集背得滾瓜爛熟（syntactic 夠），讀到 RCU 還是不懂它在幹嘛（semantic 不夠）。**兩種知識要分開補，用的方法不同**。

### 所以讀 kernel 時該怎麼做

1. **卡住時先判斷缺哪一種**。「這個巨集的參數是什麼意思」是 syntactic，查定義就好，五分鐘解決；「為什麼要在這裡關中斷」是 semantic，需要理解中斷與鎖的交互，可能要讀一章書。用錯方法很浪費：對 semantic 問題反覆查巨集定義，查不出答案。
2. **把 syntactic 知識交給工具**。巨集展開、參數順序、函式簽名，這些交給 clangd、`.i` 檔、Elixir（Ch 49、50）。你的腦力留給 semantic 層。
3. **semantic 知識要用自己的話寫下來**。讀懂一個機制，就用一段不含任何 kernel 識別字的話描述它。寫不出來，表示你還停在 syntactic 層。

## 3. Brooks：假說驅動、由上而下，以及 beacon

Ruven Brooks 1983 年的理論是程式理解研究中被引用最多的模型之一。他的核心主張是：**理解不是從程式碼往上組裝，而是從領域往下驗證**。

### 領域之間的映射

Brooks 把「寫程式」看成一連串的映射：從**問題領域**（例如「讓兩個 process 透過一段緩衝區傳資料」）經過若干中間領域（演算法、資料結構），最後到**程式領域**（具體的 C 程式碼）。理解程式就是**重建這串映射**。

```
問題領域        「process A 寫、process B 讀，緩衝區滿了 A 要等」
    │
    ▼
中間領域        環狀緩衝區 + 兩個等待佇列 + 一把鎖
    │
    ▼
程式領域        pipe->head / pipe->tail / pipe->bufs[]
                pipe->rd_wait / pipe->wr_wait
                mutex_lock(&pipe->mutex)
```

### 假說驅動

Brooks 認為讀者一開始就形成一個**主要假說**（primary hypothesis），通常來自檔名、函式名、文件——「`fs/pipe.c` 實作 pipe，應該有一個環狀緩衝區和讀寫兩邊的等待」。然後把它拆成**子假說**（「應該有判斷滿和空的邏輯」「寫的那邊滿了要睡」），一層一層往下，直到子假說具體到可以直接拿程式碼驗證。

這是**由上而下**（top-down）的理解：你不是讀完程式碼才知道它在幹嘛，而是先猜它在幹嘛，再去程式碼裡找證據。

### Beacon：觸發與確認假說的路標

Brooks 的一個重要貢獻是 **beacon**（路標）這個概念：程式碼裡某些特徵，熟手一看到就能推斷某個結構或操作的存在。Brooks 的經典例子是排序程式裡交換兩個元素的那三行——看到 swap，就知道「這附近有排序」。Wiedenbeck 1986 年的實驗支持了這個想法：有經驗的程式設計師對 beacon 行的回憶明顯好於非 beacon 行，新手則沒有這種差異。

Beacon 有兩個作用：**觸發**假說（看到它，腦中浮現一個猜想），以及**確認**假說（找到它，證實你的猜想）。

### Kernel 裡的 beacon：熟手腦中觸發什麼

下面這張表是本章最實用的部分。左邊是你在 kernel 裡天天會看到的寫法，右邊是熟手看到時腦中自動浮現的東西——那些**你還沒有、但可以刻意建立**的聯想。

| beacon | 熟手腦中立刻觸發的假說與預期 |
|---|---|
| `list_for_each_entry(pos, head, member)` | 「走訪一個侵入式串列，`pos` 是外層 struct 指標。」預期：迴圈附近有鎖或 RCU 保護；若迴圈裡有 `list_del`，要嘛用 `_safe` 版本，要嘛刪完立刻離開迴圈 |
| `spin_lock_irqsave(&x->lock, flags)` | 「這把鎖也會在中斷 context 被拿，或呼叫者可能已經關了中斷。」預期：同函式內有配對的 `spin_unlock_irqrestore`；臨界區裡不會睡（不會有 `kmalloc(GFP_KERNEL)`、`mutex_lock`、`copy_from_user`） |
| `goto out_free;` / `goto err_xxx;` | 「錯誤處理階梯。」預期：函式結尾有一串標籤，順序與資源配置順序**相反**；每個標籤釋放一樣東西然後掉到下一個標籤 |
| `container_of(ptr, type, member)` | 「手上是一個嵌在大 struct 裡的成員指標，要拿回外層 struct。」預期：這是 callback 或串列走訪的入口，`ptr` 是框架傳進來的 |
| `kref_put(&obj->kref, release_fn)` | 「引用計數歸零時呼叫 `release_fn`。」預期：`release_fn` 裡有 `container_of` 和 `kfree`；呼叫 `kref_put` 之後**不能再碰** `obj` |
| `IS_ERR(p)` / `PTR_ERR(p)` | 「`p` 可能是一個編碼成指標的錯誤碼。」預期：配置函式失敗回傳 `ERR_PTR(-Exxx)` 而不是 NULL |
| `rcu_read_lock()` | 「讀端臨界區，資料可能同時被更新者替換。」預期：裡面用 `rcu_dereference()` 拿指標；不會睡；指標不能帶出臨界區 |
| `wait_event_*(wq, cond)` | 「睡到 `cond` 成立。」預期：某處有對同一個 `wq` 的 `wake_up_*`；`cond` 會在醒來後重新檢查 |
| `lockdep_assert_held(&x->lock)` | 「呼叫者必須已經持有這把鎖。」這是**確認型** beacon：它不只觸發假說，它就是一份可執行的文件 |

這些 beacon 有多常見？在 v6.18 的 `.c`/`.h` 裡數一下（`rg -c` 計的是「出現的行數」）：

```bash
#!/usr/bin/env bash
# beacons.sh — 數 kernel 裡常見 beacon 出現在幾「行」（rg --type c 涵蓋 .c 與 .h）
cd ~/code_reading/linux
for pat in 'container_of\(' 'IS_ERR\(' 'unlikely\(' 'spin_lock_irqsave\(' \
           'list_for_each_entry\(' 'mutex_lock\(' 'READ_ONCE\(' \
           'goto out_free' 'rcu_read_lock\(\)' 'kref_put\(' 'wait_event\w*\('; do
  n=$(rg -c --type c "$pat" . | awk -F: '{s+=$2} END {print s}')
  printf '%7d  %s\n' "$n" "$pat"
done | sort -nr
```

```
  33931  IS_ERR\(
  23419  mutex_lock\(
  22707  container_of\(
  22426  unlikely\(
  16047  spin_lock_irqsave\(
  10343  list_for_each_entry\(
   7059  READ_ONCE\(
   4613  rcu_read_lock\(\)
   4027  goto out_free
   2173  wait_event\w*\(
   1359  kref_put\(
```

`container_of` 兩萬兩千多行、`IS_ERR` 三萬三千多行。**每一個 beacon 只要學會一次，就在幾千到幾萬個地方回本**。這就是為什麼慣用法鴻溝雖然一開始看起來很大，卻是最值得投資的一道：它的數量有限，出現頻率極高。

### 實戰：用 beacon 讀一段你沒看過的 f2fs 程式碼

假設你完全不懂 f2fs（一個 flash 檔案系統）。下面這段來自 v6.18：

```c
/* fs/f2fs/node.c:365–383（v6.18） */
void f2fs_del_fsync_node_entry(struct f2fs_sb_info *sbi, struct folio *folio)
{
	struct fsync_node_entry *fn;
	unsigned long flags;

	spin_lock_irqsave(&sbi->fsync_node_lock, flags);
	list_for_each_entry(fn, &sbi->fsync_node_list, list) {
		if (fn->folio == folio) {
			list_del(&fn->list);
			sbi->fsync_node_num--;
			spin_unlock_irqrestore(&sbi->fsync_node_lock, flags);
			kmem_cache_free(fsync_node_entry_slab, fn);
			folio_put(folio);
			return;
		}
	}
	spin_unlock_irqrestore(&sbi->fsync_node_lock, flags);
	f2fs_bug_on(sbi, 1);
}
```

用 beacon 讀，熟手的內心獨白大概是這樣：

1. 函式名 `del_..._entry` → 主要假說：「從某個集合刪掉一個項目」。
2. `spin_lock_irqsave` + `list_for_each_entry` → 「在鎖保護下走訪串列找東西」。這是一個 **search-and-delete** 的 plan（下一節會正式定義 plan）。
3. 迴圈裡有 `list_del` 但用的不是 `_safe` 版本 → 警報響起。再看下一行：刪完之後解鎖、`return`，**沒有繼續走訪**。所以不用 `_safe` 是對的。警報解除。
4. 解鎖在 `kmem_cache_free` 和 `folio_put` **之前** → 「釋放動作移到臨界區外面」，常見的縮短持鎖時間手法，而且 `folio_put` 可能觸發更多工作。
5. 迴圈結束都沒找到 → `f2fs_bug_on(sbi, 1)` → 「呼叫者保證項目一定存在，找不到就是 bug」。

整個過程不需要知道 f2fs 是什麼、`fsync_node_entry` 代表什麼。五個 beacon 加上幾條慣例，就把這個函式的**結構**和**不變式**讀出來了。領域意義（為什麼 f2fs 要追蹤 fsync 的 node）是另一個問題，屬於領域鴻溝，要另外補。

### 所以讀 kernel 時該怎麼做

1. **先猜，再讀**。看到函式名先寫下一句假說，再看內文。猜錯的地方就是你學到東西的地方。
2. **建立你自己的 beacon 表**。上面那張表只是起點。每次發現一個新的慣用寫法，加一行：「看到 X → 預期 Y」。Ch 58 會整理成檢查清單。
3. **beacon 會說謊**，所以要確認。`_locked` 不代表一直持有鎖（Ch 1 的 `wait_event_interruptible_locked_irq`）；`list_del` 在 `list_for_each_entry` 裡不一定是 bug（上面的 f2fs）。beacon 觸發的是**假說**，不是結論。

## 4. Soloway & Ehrlich：programming plan 與 discourse rule

Soloway 與 Ehrlich 1984 年的論文把熟手的知識進一步結構化成兩類：

- **Programming plan**（程式計畫）：代表某種刻板行動序列的通用程式片段。例如「累加計數迴圈」「找最大值」「哨兵控制的讀取迴圈」。在 kernel 裡，「錯誤處理階梯」「在鎖保護下 search-and-delete」「wait 迴圈：檢查條件 → 加入佇列 → 睡 → 重新檢查」都是 plan。
- **Rules of programming discourse**（程式論述規則）：程式設計師之間的寫作慣例，規定 plan 該怎麼組合、怎麼命名。例如「變數名應反映其用途」「不要做出讓讀者預期錯誤的事」。

他們的實驗設計很巧妙：準備兩種程式，一種符合 plan 和 discourse rule（plan-like），另一種功能相同但故意違反慣例（unplan-like，例如把一個叫 `max` 的變數拿來存最小值）。結果：讀 plan-like 程式時，熟手大幅勝過新手；讀 unplan-like 程式時，熟手的優勢**大幅縮小**。

這個結果的意義是：**熟手的優勢有很大一部分來自「慣例」，而慣例被違反時，熟手會被自己的預期誤導**。

### Kernel 的 discourse rule

kernel 有大量不成文（以及部分成文於 `Documentation/process/coding-style.rst`）的論述規則。熟手依賴它們，違反時會被絆倒：

| discourse rule | 讀者因此預期什麼 |
|---|---|
| `__foo()` 是 `foo()` 的底層版本 | 呼叫者已經處理好鎖、檢查等前置條件；`__foo` 本身不做 |
| `foo_locked()` | 呼叫時必須持有某把鎖 |
| `do_foo()` | 實際做事的 worker，通常被 syscall 包裝函式呼叫（`SYSCALL_DEFINE1(eventfd, ...)` 呼叫 `do_eventfd()`） |
| 回傳 `int` 的函式：0 成功、負 errno 失敗 | 呼叫端寫 `if (ret) goto err;` |
| 回傳指標的函式：失敗回 `ERR_PTR` 或 NULL（各子系統不同） | 這條**不統一**，是常見的陷阱 |
| 錯誤路徑標籤依釋放順序命名（`out_free_uid`、`out_revert_acct`） | 標籤名告訴你「從這裡開始要釋放什麼」 |

看一個 discourse rule 運作良好的例子：

```c
/* fs/pipe.c:792–839（v6.18），節錄頭尾 */
struct pipe_inode_info *alloc_pipe_info(void)
{
	struct pipe_inode_info *pipe;
	unsigned long pipe_bufs = PIPE_DEF_BUFFERS;
	struct user_struct *user = get_current_user();
	...
	pipe = kzalloc(sizeof(struct pipe_inode_info), GFP_KERNEL_ACCOUNT);
	if (pipe == NULL)
		goto out_free_uid;
	...
	if (too_many_pipe_buffers_hard(user_bufs) && pipe_is_unprivileged_user())
		goto out_revert_acct;
	...
	return pipe;
	...
out_revert_acct:
	(void) account_pipe_buffers(user, pipe_bufs, 0);
	kfree(pipe);
out_free_uid:
	free_uid(user);
	return NULL;
}
```

熟手讀這段幾乎不用思考：`get_current_user()` 拿了一個引用 → 最底下的 `out_free_uid` 會 `free_uid`；`kzalloc` 失敗跳到 `out_free_uid`（只要還 user）；計帳之後失敗跳到 `out_revert_acct`（要退計帳、還 pipe、再掉到還 user）。**標籤的順序和資源取得的順序剛好相反**——這條 discourse rule 讓讀者可以用「對齊」的方式驗證正確性，而不用模擬每一條路徑。Ch 17 會講到 `cleanup.h` 如何用另一種方式解決同一個問題。

### 所以讀 kernel 時該怎麼做

1. **學 plan，不只學 API**。學 `wait_event` 時，記的不是參數列表，而是整個 wait 迴圈的 plan：檢查、入佇列、設狀態、再檢查、睡、醒來回到檢查。有了 plan，你看到任何一種變體（`wait_event_interruptible_exclusive`、手寫的 `prepare_to_wait` 迴圈）都認得。
2. **違反慣例的地方要放慢**。Soloway & Ehrlich 的實驗告訴你：慣例被打破時，你的直覺會騙你。看到「怪」的地方，不是跳過，而是停下來：這是 bug、是刻意的、還是你的慣例知識不對？這正是脈絡鴻溝的入口。
3. **注意子系統之間的方言差異**。discourse rule 不是全 kernel 統一的。網路子系統、檔案系統、driver 各有自己的慣例。換子系統時，先花十分鐘讀幾個代表性函式，校準你的預期。

## 5. Pennington：program model 與 situation model

Nancy Pennington 1987 年的研究觀察專業程式設計師讀 FORTRAN 與 COBOL 程式，提出讀者會建立**兩種不同的心智表徵**：

- **Program model**（程式模型）：貼近程式文本的表徵，以**控制流**為主——先做什麼、再做什麼、哪裡有迴圈、哪裡有分支。它是**由下而上**建立的：從程式碼本身的結構組起來。
- **Situation model**（情境模型）：用**問題領域的詞彙**描述程式在做什麼，以**資料流和功能**為主——「這段在計算這個月的薪資」「這段確保讀者不會讀到半寫的資料」。

她的研究發現，讀者通常**先建立 program model**，之後才（如果有）建立 situation model。她同年在 Empirical Studies of Programmers 第二屆研討會發表的另一篇研究〈Comprehension strategies in programming〉進一步觀察到，理解表現最好的那群程式設計師會在程式碼和領域之間**交叉對照**，而不是只停在其中一邊。

用 `anon_pipe_write()` 的等待那一段當例子：

```
Program model（控制流）：
  迴圈：
    如果沒有 reader → 送 SIGPIPE、回 -EPIPE、離開
    如果沒滿 → 配一個 page、複製、head+1、繼續
    如果 O_NONBLOCK → 回 -EAGAIN、離開
    如果有 signal → 回 -ERESTARTSYS、離開
    解鎖 → 若原本是空的，喚醒 reader → 睡到 pipe_writable() → 加鎖

Situation model（領域）：
  「writer 把資料一頁一頁推進環狀緩衝區；緩衝區滿了，
    它先確保 reader 知道有資料可讀，然後讓出 CPU，
    等 reader 消耗掉至少一個 slot 再回來。」
```

兩個模型都是「理解」，但回答不同的問題。program model 能回答「第 573 行之後執行哪一行」；situation model 能回答「為什麼睡之前要先喚醒 reader」（因為如果 reader 也在睡、而 writer 不叫醒它，兩邊就會互相等待，沒有人前進）。

**領域鴻溝的本質就是建不起 situation model**。你能建 program model（逐行追控制流），但缺少領域詞彙，無法把它翻譯成「這在做什麼」。

### 所以讀 kernel 時該怎麼做

1. **檢查自己建了哪一種模型**。讀完一個函式，試著不用任何識別字描述它。說得出來 → 有 situation model；只能說「先呼叫 A 再呼叫 B」→ 只有 program model。
2. **刻意做交叉對照**。讀程式碼時一邊問「這在領域裡對應什麼？」，讀文件時一邊問「這在程式碼裡對應哪幾行？」。最有效的做法是把 `Documentation/` 或 LWN 文章和原始碼並排打開。
3. **situation model 要用領域詞彙寫進筆記**。Ch 7 的筆記模板會分兩欄：「程式碼做什麼」和「這代表什麼」。

## 6. Letovsky、Littman 等人：提問、猜想，以及系統性 vs 按需策略

### Letovsky：理解者是一個機會主義的提問者

Stanley Letovsky（1986 年在 Empirical Studies of Programmers 第一屆研討會發表，期刊版 1987 年刊於 Journal of Systems and Software）用 think-aloud 方法觀察專業程式設計師理解程式，提出的模型有三個部分：

- **Knowledge base**：讀者帶進來的知識——程式語言、領域、plan、慣例。
- **Mental model**：讀者正在建立的、關於這個程式的理解，包含規格層（程式要達成什麼）、實作層（程式怎麼做）、以及兩者之間的對應註記。
- **Assimilation process**：結合程式碼、文件與 knowledge base 來更新 mental model 的過程。

他分析受試者出聲思考的內容，發現理解過程充滿**提問**（inquiry）和**猜想**（conjecture）。提問大致可分成幾類：**why**（這段為什麼存在？）、**how**（這個目標怎麼達成？）、**what**（這個變數或函式是什麼？）、**whether**（它會不會這樣做？）、**discrepancy**（這和我的預期為什麼不一樣？）。讀者不是依固定順序讀，而是**機會主義地**（opportunistically）在由上而下和由下而上之間切換，哪一邊有線索就往哪邊走。

這是 Ch 3「帶著問題讀」的直接理論基礎：熟手的閱讀過程**本來就是**一連串的提問與猜想，只是他們不一定意識到。

### Littman 等人：系統性 vs 按需

同一個研究群（Littman、Pinto、Letovsky、Soloway，1986 年 ESP 研討會；期刊版 1987 年）觀察程式設計師在修改一個小程式之前怎麼讀它，發現兩種明顯不同的策略：

- **Systematic（系統性）**：在動手修改之前，先追蹤整個程式的資料流和控制流，建立全域的理解，包括各部分之間的**因果交互**。
- **As-needed（按需）**：只讀和眼前修改相關的部分，需要時再去看其他地方。

結果是：用系統性策略的人建立了更完整的 mental model（尤其是各部分如何互相影響），修改也成功；用按需策略的人只有片段的、偏靜態的理解，容易因為沒看到某個遠處的交互而改錯。

> 這裡要特別誠實：Littman 等人研究的是**小程式**，系統性地讀完整個程式是可行的。對兩千萬行的 kernel，系統性策略在整體層級上**不可能**——沒有人讀完 kernel。所以這個發現不能直接套用成「要系統性地讀」。

### 這兩個研究怎麼調和到 kernel

正確的外推是：**在你的問題範圍內系統性，在範圍外按需**。

```
         整個 kernel：按需（不可能系統性）
    ┌──────────────────────────────────────────┐
    │   fs/：按需                               │
    │  ┌────────────────────────────────────┐  │
    │  │ fs/pipe.c：按需                     │  │
    │  │  ┌──────────────────────────────┐  │  │
    │  │  │ 你的問題範圍：                │  │  │
    │  │  │ anon_pipe_write 的等待路徑    │  │  │
    │  │  │ + anon_pipe_read 的喚醒路徑   │  │  │
    │  │  │ + 共用的 pipe_inode_info 欄位 │  │  │
    │  │  │ → 在這裡系統性：追完所有      │  │  │
    │  │  │   讀寫這些欄位的地方          │  │  │
    │  │  └──────────────────────────────┘  │  │
    │  └────────────────────────────────────┘  │
    └──────────────────────────────────────────┘
```

Littman 等人的教訓是「按需策略會漏掉遠處的交互」。在 kernel 裡，遠處的交互通常透過**共用的資料結構**發生（兩個函式讀寫同一個欄位）。所以在問題範圍內做到系統性的具體方法是：**找出你關心的那幾個欄位，把所有讀寫它們的地方都列出來**。例如問「writer 怎麼被叫醒」，就要 `rg -n 'wr_wait' fs/pipe.c` 把所有碰 `wr_wait` 的地方列出來，而不是只看你碰巧讀到的那一處。Ch 5 的「資料結構優先」就是這個想法的展開。

### 所以讀 kernel 時該怎麼做

1. **把提問說出口**。讀碼時用 why/how/what 把疑問寫下來，而不是讓它在腦中一閃而過。被寫下來的問題才能被追蹤、被回答。
2. **先劃定範圍，範圍內系統性**。問題確定之後，找出問題涉及的資料欄位，用 grep 列出它們的**所有**讀寫點。這一步防止按需策略的盲點。
3. **範圍外大方地按需**。和問題無關的函式，讀到簽名和名字就停，標成黑箱。

## 7. von Mayrhauser & Vans：整合模型

到了 1990 年代，研究者面對一個問題：top-down（Brooks）、bottom-up（Shneiderman & Mayer、Pennington 的 program model）、situation model（Pennington）、機會主義（Letovsky）——哪個才對？

Anneliese von Mayrhauser 與 A. Marie Vans 1995 年在 IEEE Computer 發表的整合模型回答：**全部都對，熟手會在它們之間切換**。他們的模型有四個部分：

- **Top-down model**（或稱 domain model）：Brooks 式的，從領域知識出發，形成假說往下驗證。熟悉領域時用。
- **Program model**：Pennington 式的，從程式碼的控制流往上組。對程式碼完全陌生時用。
- **Situation model**：Pennington 式的，用領域詞彙描述資料流與功能。在 program model 有了一定基礎之後，往上抽象時建立。
- **Knowledge base**：支撐以上三者的長期記憶，同時也被理解過程更新。

他們觀察專業維護人員處理大型程式碼（不是實驗室的小程式），發現理解者**在任何時刻都可能切換模型**：讀到熟悉的部分走 top-down，碰到陌生的實作細節切到 program model，拼出一段功能之後往上建 situation model，碰到矛盾又跳回程式碼。

```
              ┌────────────────────┐
              │   Top-down model   │ ← 熟悉的領域：猜 → 驗證
              │ （領域知識、假說）  │
              └─────────┬──────────┘
                        │ ▲
          找不到 beacon  │ │ 拼出功能、往上抽象
          假說失敗       ▼ │
              ┌────────────────────┐
              │  Situation model   │ ← 資料流、功能、領域詞彙
              └─────────┬──────────┘
                        │ ▲
           需要細節      │ │ 組出意義
                        ▼ │
              ┌────────────────────┐
              │   Program model    │ ← 陌生程式碼：逐行、控制流
              └────────────────────┘
                 三者共用並更新
              ┌────────────────────┐
              │   Knowledge base   │
              └────────────────────┘
```

### 用整合模型看「你為什麼讀不懂 kernel」

把五道鴻溝放進這個模型，會看到它們卡在不同的位置：

| 鴻溝 | 卡在整合模型的哪裡 |
|---|---|
| 慣用法 | Knowledge base 裡缺 syntactic chunk，program model 建不起來（每行都要拆解） |
| 間接 | program model 的控制流在指標處斷掉，無法從程式碼往下組 |
| 領域 | 無法從 program model 往上建 situation model；也無法走 top-down（沒有領域假說可以驗證） |
| 脈絡 | situation model 建好了，但其中有「無法解釋的部分」——knowledge base 裡沒有那段歷史 |
| 目標 | 沒有假說，top-down 無從啟動；只能用最費力的 program model 從頭組 |

這張表說明了為什麼新手讀 kernel 那麼累：**他們被迫只用 program model**。慣用法不熟讓 program model 很慢，領域不熟讓他們無法往上抽象也無法 top-down，沒有目標讓 top-down 無從開始。熟手則可以在三個模型之間自由切換，哪條路省力就走哪條。

### 所以讀 kernel 時該怎麼做

1. **意識到自己正在用哪個模型**。讀得很累、每行都在拆解時，你多半卡在 program model。問自己：能不能切到 top-down？（有沒有可以先猜的假說？）能不能先補一點領域知識讓自己能往上抽象？
2. **熟悉的部分 top-down，陌生的部分 bottom-up**。讀 pipe 時，「環狀緩衝區」這部分如果你熟，就直接猜它有 head/tail 再去驗證；`watch_queue` 那部分如果完全陌生，就老實地逐行建 program model，或者乾脆標黑箱。
3. **讓 knowledge base 持續增長**。每讀完一段，把新學到的 beacon、plan、慣例寫下來（Ch 7）。整合模型的最後一塊是 knowledge base，而它是唯一能跨越不同讀碼任務累積的東西。

## 對比與取捨：七個觀念一覽

| 觀念 | 提出者（年份） | 一句話 | 對應的鴻溝 | 讀 kernel 的具體動作 |
|---|---|---|---|---|
| 工作記憶與 chunking | Miller（1956）、Chase & Simon（1973）、Cowan（2001） | 容量很小，chunk 越大裝得越多 | 慣用法 | 外部化筆記、限制跳轉深度 |
| syntactic / semantic | Shneiderman & Mayer（1979） | 語言細節 vs 通用概念，分開補 | 慣用法 / 領域 | 先判斷缺哪種知識 |
| 假說驅動 + beacon | Brooks（1983）、Wiedenbeck（1986） | 先猜再驗證，beacon 觸發與確認假說 | 目標 / 慣用法 | 先寫假說；建立 beacon 表 |
| plan + discourse rule | Soloway & Ehrlich（1984） | 熟手靠慣例，慣例被破壞時會被誤導 | 慣用法 / 脈絡 | 學 plan；怪的地方放慢 |
| program / situation model | Pennington（1987） | 控制流表徵 vs 領域功能表徵 | 領域 | 不用識別字描述程式 |
| 提問 + 策略 | Letovsky（1987）、Littman 等（1986） | 理解是提問與猜想；範圍內系統性 | 目標 / 間接 | 寫下問題；列出欄位所有讀寫點 |
| 整合模型 | von Mayrhauser & Vans（1995） | 熟手在三種模型間切換 | 全部 | 意識到自己用哪個模型 |

這些模型之間有真實的爭議：top-down 和 bottom-up 誰才是「主要」策略，在 1980 年代吵了很久。整合模型的回答（看情況切換）比較像是和解，而不是某一方的勝利。對讀碼實務來說，這個和解是好消息：**重點不是選邊，而是能切換**。

## 踩雷集錦

1. **「熟手記憶力比較好」**：Chase & Simon 和 McKeithen 等人的實驗都顯示，素材沒有意義時熟手和新手一樣差。熟手多的是 chunk，不是記憶容量。所以你能追上，方法是累積 chunk。
2. **「top-down 比 bottom-up 高級」**：top-down 需要領域知識和合適的假說，沒有的時候硬用只會亂猜。對完全陌生的程式碼，老實地 bottom-up 建 program model 是正確選擇。
3. **「看到 beacon 就下結論」**：beacon 觸發的是假說。kernel 裡有大量「看起來像 X 其實不是」的情況：`_locked` 的語意、在 `list_for_each_entry` 裡 `list_del`、`waitqueue_active()` 在無鎖時的 race（它的註解特別警告過）。每個 beacon 都要確認。
4. **「系統性讀碼比較好，所以要從頭讀到尾」**：Littman 等人研究的是小程式。對 kernel，系統性必須限縮在問題範圍內。從 `fs/pipe.c` 第一行讀到第 1553 行不是系統性，是目標鴻溝。
5. **把這些模型當成已證實的定律**：這些研究的受試者人數多半不大、程式規模小、語言老舊。它們是有用的**思考框架**，不是精確的科學定律。拿它們來反省自己的讀碼方式很有效；拿它們來預測「讀這段要花幾分鐘」就是過度延伸。

## 進階：再往深一層

- **Storey 的綜述**：Margaret-Anne Storey 2006 年在 Software Quality Journal 的綜述〈Theories, tools and research methods in program comprehension: past, present and future〉把本章這些模型放在一起比較，並討論它們如何影響讀碼工具的設計（例如為什麼 IDE 要有 call hierarchy、為什麼 code search 重要）。讀完本章之後讀它，可以看到研究在 1995 年之後往哪裡走。
- **工具即認知輔助**：從整合模型的角度看，Part 8 的每一個工具都在補某一個模型的缺口——cscope 和 clangd 幫你建 program model（跳定義、找呼叫者）；ftrace 幫你在間接處接上 program model；`git log` 和 lore 補 knowledge base 裡缺的歷史。用這個角度選工具：先問「我現在卡在哪個模型」，再挑工具。
- **LLM 與 chunking**：LLM 輔助讀碼（Ch 53）在認知上的作用，很大一部分是**代替你做 syntactic → semantic 的重新編碼**。這能加速，但也有風險：如果 LLM 給你的 situation model 是錯的（例如它記得的是舊版 kernel 的 `pipe_write`），你會帶著一個自信的錯誤模型繼續讀。Ch 53 細講。

## 動手練習

1. **beacon 預測**：在 v6.18 tree 裡打開 `kernel/sched/wait.c`，只看第 92–116 行 `__wake_up_common()` 的函式名、參數和第一個 beacon（第 97 行 `lockdep_assert_held`），先寫下三個假說，再讀完整個函式，對照你猜中幾個。
2. **兩種模型**：選 `fs/eventfd.c` 的 `eventfd_ctx_remove_wait_queue()`（第 198–211 行），分別寫一段 program model（控制流）和一段 situation model（不准出現任何識別字）。哪一段比較難寫？為什麼？
3. **擴充 beacon 表**：用 `beacons.sh` 的寫法，再數三個你覺得常見的 kernel 寫法（例如 `WARN_ON_ONCE(`、`kfree_rcu(`、`smp_load_acquire(`），並各寫一行「看到它，我預期什麼」。

<details>
<summary>練習 1 參考解答</summary>

`__wake_up_common()` 的程式碼（`kernel/sched/wait.c:92–116`，v6.18）：

```c
static int __wake_up_common(struct wait_queue_head *wq_head, unsigned int mode,
			int nr_exclusive, int wake_flags, void *key)
{
	wait_queue_entry_t *curr, *next;

	lockdep_assert_held(&wq_head->lock);

	curr = list_first_entry(&wq_head->head, wait_queue_entry_t, entry);

	if (&curr->entry == &wq_head->head)
		return nr_exclusive;

	list_for_each_entry_safe_from(curr, next, &wq_head->head, entry) {
		unsigned flags = curr->flags;
		int ret;

		ret = curr->func(curr, mode, wake_flags, key);
		if (ret < 0)
			break;
		if (ret && (flags & WQ_FLAG_EXCLUSIVE) && !--nr_exclusive)
			break;
	}

	return nr_exclusive;
}
```

合理的假說與驗證：

- 「呼叫者必須持有 wait queue 的鎖」——第 97 行 `lockdep_assert_held` 直接確認。
- 「會走訪佇列上的每一個等待者並喚醒他們」——第 104 行 `list_for_each_entry_safe_from` 確認走訪；用 `_safe` 是因為 `curr->func` 可能把 `curr` 從串列上移除（預設的 `autoremove_wake_function` 正是如此）。
- 「`nr_exclusive` 限制喚醒的數量」——第 111 行確認，但更精確：只有帶 `WQ_FLAG_EXCLUSIVE` 且被成功喚醒（`ret` 非零）的等待者才會扣減額度；非 exclusive 的等待者全部被喚醒。這正是 Ch 3 會看到的 pipe「exclusive wait」避免 thundering herd 的機制。
- 第 108 行 `curr->func(...)` 是一個**間接呼叫**——喚醒動作本身是 callback，可以被替換。這是間接鴻溝，也是 Ch 1 裡 `in_eventfd` 那段歷史的根源：某些等待者的 `func` 會去呼叫 `eventfd_signal()`。

</details>

## 本章重點整理

- 熟手的優勢不是記憶力，是 **chunk**：同樣的工作記憶，chunk 越大裝得越多。慣用法鴻溝就是缺 chunk。
- 理解需要兩種知識：**syntactic**（任意、要背、交給工具）和 **semantic**（概念、可遷移、要理解）。卡住時先判斷缺哪種。
- Brooks：理解是**假說驅動**的，**beacon** 觸發與確認假說；但 beacon 會說謊，要驗證。
- Soloway & Ehrlich：熟手依賴 **plan** 和 **discourse rule**；慣例被打破時熟手會被誤導，所以「怪」的地方要放慢。
- Pennington：**program model**（控制流）和 **situation model**（領域功能）是兩種理解；領域鴻溝就是建不起 situation model。
- Letovsky、Littman 等：理解是一連串的**提問與猜想**；在問題範圍內**系統性**，範圍外**按需**。
- von Mayrhauser & Vans：熟手在 top-down、program model、situation model 之間**切換**；新手被迫只用 program model，所以累。

## 自我檢核

- [ ] 不看本章，能用西洋棋實驗解釋「chunk」是什麼，並說出它和慣用法鴻溝的關係
- [ ] 讀 kernel 時發現自己忘了剛才在查什麼，能用工作記憶的概念解釋發生了什麼，並說出兩個具體對策
- [ ] 能各舉一個 kernel 的例子說明 syntactic 知識不足和 semantic 知識不足，以及兩者的補法有什麼不同
- [ ] 看到 `spin_lock_irqsave` 和 `goto out_free`，能說出熟手腦中會觸發哪些預期——而且能說出一個「beacon 會說謊」的例子
- [ ] 如果有人說「Littman 的研究證明讀碼要系統性，所以讀 kernel 要從頭讀」，你會怎麼反駁？正確的外推是什麼
- [ ] 想一次你讀碼讀得很累的經驗：用整合模型分析，當時你被困在哪個模型裡？有沒有機會切換到別的模型？

## 延伸閱讀

### 論文

- **[Towards a theory of the comprehension of computer programs](https://doi.org/10.1016/S0020-7373(83)80031-5)** — Ruven Brooks，International Journal of Man-Machine Studies 18(6)（1983）
  - **核心貢獻**：理解是重建從問題領域到程式碼的映射；由假說驅動、由上而下；提出 beacon
  - **讀哪裡**：理論部分（映射、假說的分解、beacon）；論文篇幅不長，可以整篇讀
  - **和本章的關聯**：第 3 節的全部內容；也是 Ch 3 假說驗證迴圈的源頭
- **Empirical Studies of Programming Knowledge** — Elliot Soloway & Kate Ehrlich，IEEE Transactions on Software Engineering SE-10(5)（1984）
  - **核心貢獻**：用 plan-like 與 unplan-like 程式的對照實驗，顯示熟手的優勢來自 plan 和 discourse rule
  - **讀哪裡**：實驗設計與結果；附錄裡的程式範例很有啟發性
  - **和本章的關聯**：第 4 節；解釋為什麼 kernel 的命名慣例對讀者這麼重要
- **Stimulus structures and mental representations in expert comprehension of computer programs** — Nancy Pennington，Cognitive Psychology 19(3)（1987）
  - **核心貢獻**：區分 program model 與 situation model，並顯示讀者通常先建前者
  - **讀哪裡**：討論部分（關於兩種模型與交叉對照策略）；實驗細節較長，可略讀
  - **和本章的關聯**：第 5 節；領域鴻溝的理論解釋
- **Delocalized Plans and Program Comprehension** — Stanley Letovsky & Elliot Soloway，IEEE Software 3(3)（1986）
  - **核心貢獻**：當一個 plan 的程式碼分散在多處時，讀者辨識它的機率下降，維護時容易出錯
  - **讀哪裡**：整篇（短，偏實務）
  - **和本章的關聯**：間接鴻溝的理論源頭；也呼應第 6 節 Littman 等人關於「遠處交互」的發現
- **[Program Comprehension During Software Maintenance and Evolution](https://doi.org/10.1109/2.402076)** — Anneliese von Mayrhauser & A. Marie Vans，IEEE Computer 28(8)（1995）
  - **核心貢獻**：回顧之前的模型，提出整合 top-down、program model、situation model 與 knowledge base 的 metamodel
  - **讀哪裡**：前半的模型回顧可以當成本章的英文版複習；後半是整合模型與觀察結果
  - **和本章的關聯**：第 7 節；也是讀其他所有論文之前最好的入口，先讀它再回頭讀原始論文

### 綜述

- **Theories, tools and research methods in program comprehension: past, present and future** — Margaret-Anne Storey，Software Quality Journal 14(3)（2006）
  - **這篇說什麼**：程式理解研究三十年的地圖，包含理論、工具、研究方法
  - **讀哪裡**：理論部分對照本章；工具部分對照 Part 8
  - **為什麼值得讀**：它把「認知理論如何影響工具設計」講得很清楚，幫你理解為什麼 IDE 長成今天的樣子

### 書籍

- **《The Programmer's Brain》** — Felienne Hermans（Manning, 2021）
  - **這本書的定位**：把本章這類認知科學研究寫給一般程式設計師看的實用書，有大量練習
  - **讀哪幾章**：第 1 章把讀碼時的困惑分成三種（缺知識、缺資訊、缺處理能力，分別對應長期記憶、短期記憶、工作記憶），可以拿來和本課的五道鴻溝對照；第 2–5 章講 chunking、快速學語法、讀複雜程式碼與建立更深的理解，與本章最相關
  - **前提知識**：無；例子以 Python、Java 為主，但觀念通用

認知模型告訴你熟手腦中多了什麼，其中最可以立刻模仿的一件事是：**帶著假說讀**。下一章把 Brooks 和 Letovsky 的觀念變成一個可操作的迴圈，並用 pipe 的阻塞路徑完整走一遍。

→ [Ch 3 帶著問題讀：提問驅動與假說驗證迴圈](./03-question-driven-reading.md)
