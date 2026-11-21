# Ch 58 — 讀碼箴言與檢查清單

> **目標**：把整門課壓成一份可以貼在螢幕旁邊的工具：五十條讀碼箴言、一份冷讀檢查清單、每道鴻溝卡住時的處方、一把從入門到精通的自我評量尺，以及讀完本課之後的長期練習計畫。
> **鴻溝**：本章五道鴻溝全部覆蓋；它是索引，不是新內容。

> 本章是總結章，**不引入新機制**，所以模板裡的「底層機制」由各條箴言連回的原章節負責；範例只有一個，示範怎麼把檢查清單用在一個隨機函式上。Spinellis 原書附錄也有一份 maxims 清單，本章**不搬原文**：底下每一條都是用本課的語言、本課的例子重新整理的，有些條目原書根本沒有（git 考古、LLM、動態追蹤）。

## 為什麼需要這個？

五十八章讀完，你腦中有幾百個技巧。問題是：**卡住的那一刻，你想不起來該用哪一個**。冷讀一段 kernel code 時腦袋的工作記憶已經被程式碼佔滿，沒有餘裕去回想「Ch 28 說過什麼」。

飛行員有 checklist，外科醫師有 checklist，不是因為他們不會，是因為**壓力下的記憶不可靠**。讀碼也一樣。這一章做的事情是把方法外部化：卡住時不用想，翻到對應的那一格照做。

## 先建立直覺：一張總圖

```
               ┌──────────────── 開始前 ───────────────┐
               │ 我的問題是什麼？（目標）               │
               │ 入口在哪？資料結構是什麼？             │
               └──────────────────┬─────────────────────┘
                                  ▼
    ┌────────────────────── 讀的時候 ───────────────────────┐
    │  看不懂一行 ──► 診斷：卡在哪一道鴻溝？                  │
    │     ├─ 字都認得，整行不懂 ──► 慣用法：查、展開、認 pattern │
    │     ├─ 追到函式指標斷了   ──► 間接：找賦值點、動態追蹤    │
    │     ├─ 懂語法不懂目的     ──► 領域：補背景，先別讀 code   │
    │     ├─ 懂目的不懂為什麼   ──► 脈絡：blame、log -L、lore   │
    │     └─ 讀了半天不知道在幹嘛 ──► 目標：停下來，重新提問    │
    └──────────────────────────┬────────────────────────────┘
                               ▼
               ┌──────────────── 結束時 ───────────────┐
               │ 能用三句話回答原本的問題嗎？           │
               │ 筆記、圖、未解問題寫下來了嗎？         │
               └────────────────────────────────────────┘
```

> 這張圖的骨架是 [Ch 1 — 為什麼你看不懂](./01-why-you-cant-read-it.md) 的五道鴻溝；如果「診斷」這一步對你還不自然，先回去重讀 Ch 1 的自我診斷表。

## (a) 讀碼箴言

每條格式：**箴言**——一句說明（對應章節）。

### 目標鴻溝：帶著問題讀

1. **沒有問題，就不要開檔案。**——「理解這個子系統」不是問題；「pipe 滿的時候 writer 睡在哪裡」才是。（[Ch 3](./03-question-driven-reading.md)）
2. **問題要能被一段程式碼回答。**——問得太大就拆，拆到每個子問題都能對應到一個函式或一個欄位。（[Ch 3](./03-question-driven-reading.md)）
3. **先猜，再讀。**——讀函式內文前先寫下你猜它做什麼；猜錯的地方就是你學到的地方。（[Ch 3](./03-question-driven-reading.md)）
4. **80% 的理解就夠用，剩下 20% 留給需要時。**——接受黑箱，標記它，繼續走。（[Ch 4](./04-selective-reading.md)）
5. **略讀是一種技術，不是偷懶。**——先看函式簽名、回傳路徑、呼叫了誰，再決定要不要細讀。（[Ch 4](./04-selective-reading.md)）
6. **從入口點開始，不要從檔案第一行開始。**——syscall、ops table 的回呼、`module_init`、`main`。（[Ch 5](./05-entry-points-and-data-first.md)）
7. **讀了十分鐘還說不出在幹嘛，就停下來。**——不是你笨，是問題丟了；回到第 1 條。（[Ch 1](./01-why-you-cant-read-it.md)）

### 慣用法鴻溝：認得 pattern

8. **一個看不懂的巨集，先展開再說。**——`make fs/pipe.i` 或 `gcc -E`，讓前置處理器替你讀。（[Ch 13](./13-preprocessor-basics.md)、[Ch 50](./50-compiler-as-reading-tool.md)）
9. **`do { } while (0)` 是語法包裝，不是迴圈。**——看到它就跳過外殼，讀裡面。（[Ch 13](./13-preprocessor-basics.md)）
10. **`goto out` 是 C 的解構子。**——錯誤路徑從下往上讀，每個 label 釋放一層資源。（[Ch 8](./08-control-flow-idioms.md)、[Ch 17](./17-kernel-error-conventions.md)）
11. **看到 `container_of`，就畫出外層 struct。**——它是「從成員回到容器」，侵入式資料結構全靠它。（[Ch 16](./16-container-of.md)）
12. **回傳指標的函式可能回傳錯誤碼。**——`IS_ERR()` 檢查的是指標裡藏的 `-errno`，不是 NULL。（[Ch 17](./17-kernel-error-conventions.md)）
13. **`__user` 是一道牆。**——標了它的指標不能直接 dereference，一定要經過 `copy_from_user` 類函式。（[Ch 18](./18-annotations-and-sparse.md)）
14. **`READ_ONCE` 出現的地方，就是有人在不拿鎖的情況下讀。**——它是並行的 beacon，旁邊一定有一段說明為什麼可以不拿鎖。（[Ch 21](./21-read-once-and-barriers.md)）
15. **flag 欄位要拆成位元讀。**——`flags & ~(A | B)` 是在檢查「有沒有不認識的 flag」。（[Ch 9](./09-expressions-integers-bits.md)）
16. **巨集生成的程式碼，grep 不到定義。**——`DEFINE_*`、X-macro、token pasting；找不到時搜尋拼接前的片段。（[Ch 14](./14-macro-metaprogramming.md)、[Ch 22](./22-kernel-macro-decoding.md)）
17. **每個 get 都要找到它的 put。**——refcount 不對稱是 kernel 最常見的 bug 類型之一。（[Ch 20](./20-refcount-and-lifetime.md)）
18. **GFP flag 告訴你這段 code 能不能睡。**——`GFP_KERNEL` 可以，`GFP_ATOMIC` 不行；這決定了周圍能拿什麼鎖。（[Ch 19](./19-kernel-memory-allocation.md)）

### 間接鴻溝：追到斷了之後

19. **函式指標斷了，就找賦值點。**——搜 `.read_iter\s*=`，不要搜呼叫點。（[Ch 28](./28-ops-tables-indirect-calls.md)）
20. **ops table 是一張路由表。**——把它整張抄進筆記，每個欄位對應到哪個函式，就是這個物件的完整行為清單。（[Ch 28](./28-ops-tables-indirect-calls.md)、[Ch 42](./42-object-oriented-c.md)）
21. **靜態讀不出來的，就讓它跑起來看。**——ftrace `function_graph` 一次給你整條實際路徑。（[Ch 51](./51-dynamic-tracing.md)）
22. **不確定走哪條分支，就下斷點。**——QEMU + gdb 單步，比猜快。（[Ch 52](./52-qemu-gdb-kernel.md)）
23. **用編譯器建好的索引，不要用你的記憶。**——`compile_commands.json` + clangd 只跳到**真的會被編譯**的那個定義。（[Ch 49](./49-clangd-lsp.md)）
24. **`grep` 結果太多，就收緊 regex；太少，就放鬆。**——搜尋策略本身是一種迭代。（[Ch 47](./47-grep-and-regex-search.md)）
25. **callback 註冊的地方，就是控制流倒過來的地方。**——notifier、workqueue、timer：先找「誰把函式交出去」，再找「誰會呼叫它」。（[Ch 32](./32-async-irq-softirq-workqueue.md)、[Ch 34](./34-notifiers-and-callbacks.md)）
26. **睡下去的人，要找到叫醒它的人。**——每個 `wait_event` 都要配上一個 `wake_up`，以及兩者共用的條件。（[Ch 33](./33-wait-queues-completion.md)）
27. **syscall 從 `SYSCALL_DEFINE` 開始追。**——grep 函式名找不到 `sys_xxx`，因為它是巨集生成的。（[Ch 29](./29-syscall-entry-paths.md)）

### 領域鴻溝：補背景

28. **語法都懂卻不懂目的，就關掉程式碼。**——去讀 LWN、Documentation、書，回來再讀。（[Ch 46](./46-acquiring-domain-knowledge.md)）
29. **先讀資料結構，程式碼就讀懂一半。**——struct 的欄位和註解是作者留下的設計說明書。（[Ch 5](./05-entry-points-and-data-first.md)、[Ch 11](./11-structs-unions-tagged.md)）
30. **辨認資料結構的家族。**——`list_head`、`rbtree`、`xarray`、`kfifo` 各自有固定的操作語彙；認出家族就認出一半操作。（[Ch 23](./23-list-head-hlist.md)–[Ch 27](./27-bitmap-percpu-kfifo.md)）
31. **分層看系統。**——VFS 不管 ext4 怎麼存資料，ext4 不管 block layer 怎麼排程；知道自己在哪一層，就知道哪些細節可以忽略。（[Ch 41](./41-layering-and-vfs.md)）
32. **認出 state machine，就畫出來。**——狀態欄位 + switch 或 flag 轉換，畫成圖比讀十遍有用。（[Ch 44](./44-state-machines-event-driven.md)）
33. **設計模式是壓縮過的領域知識。**——看出「這是 observer」，就知道該去找 subscribe 和 notify。（[Ch 45](./45-design-patterns-in-kernel.md)）
34. **driver 的一生從 probe 開始。**——bus 匹配到 device，才呼叫 probe；不懂 driver model，probe 函式永遠讀不懂。（[Ch 43](./43-driver-model.md)）
35. **鎖保護的是不變量，不是程式碼。**——讀有鎖的程式碼，先找出「這把鎖保證什麼永遠成立」。（[Ch 30](./30-reading-locking-code.md)）
36. **RCU reader 不擋 writer，所以 reader 看到的可能是舊的。**——讀 RCU 時區分「誰在讀、誰在換、誰在等 grace period」。（[Ch 31](./31-reading-rcu-code.md)）

### 脈絡鴻溝：為什麼要寫成這樣

37. **奇怪的程式碼通常有一個 commit message 在解釋它。**——`git blame` 第一步，`git log -L` 第二步。（[Ch 37](./37-git-archaeology.md)）
38. **commit message 說「what」，mailing list 說「why」。**——`Link:` 指過去，被否決的版本也在那裡。（[Ch 38](./38-mailing-list-archaeology.md)）
39. **`Fixes:` 是 bug 的族譜。**——`git log --grep='Fixes: <hash>'` 看同一個起點被修過幾次，次數多就是熱點。（[Ch 37](./37-git-archaeology.md)、[Ch 57](./57-reading-patches-and-review.md)）
40. **測試是可以執行的規格。**——selftests 告訴你作者認為「正確」是什麼意思。（[Ch 40](./40-tests-as-specification.md)）
41. **`#ifdef` 裡的程式碼，可能根本沒被編進去。**——先查 `.config`，再決定要不要讀。（[Ch 36](./36-kconfig-kbuild-ifdef.md)）
42. **目錄結構就是子系統邊界。**——`MAINTAINERS` 告訴你誰負責、哪個 list 討論。（[Ch 35](./35-kernel-source-tree-map.md)）
43. **kernel-doc 註解比函式名可信，函式名比你的猜測可信。**——但三者都不如程式碼本身。（[Ch 39](./39-documentation-and-style.md)）
44. **diff 告訴你看哪裡，不告訴你看到哪裡為止。**——bug fix 一律回到完整函式讀。（[Ch 57](./57-reading-patches-and-review.md)）

### 通用

45. **讀過的東西如果沒寫下來，三天後就蒸發了。**——筆記寫問題、假說、證據，不寫流水帳。（[Ch 7](./07-reading-notes-and-diagrams.md)）
46. **畫圖是讀碼，不是讀完之後的作業。**——畫不出來的地方，就是還沒懂的地方。（[Ch 7](./07-reading-notes-and-diagrams.md)）
47. **chunking：把五行讀成一個概念。**——熟手不是讀得快，是一次吃得多。（[Ch 2](./02-program-comprehension-models.md)）
48. **找 beacon。**——`list_for_each_entry`、`spin_lock_irqsave`、`copy_to_user` 這類記號一出現，就能猜出整段在做什麼。（[Ch 2](./02-program-comprehension-models.md)）
49. **top-down 和 bottom-up 要交替用。**——有假說時往下驗證，假說崩了就從細節往上重建。（[Ch 2](./02-program-comprehension-models.md)）
50. **先在小程式上練方法，再拿去打 kernel。**——wc、Lua、Redis 是同一套方法的低難度版本。（[Ch 6](./06-warmup-bsd-wc.md)）
51. **寫一個 user-space 小程式驗證你的理解。**——你以為 pipe 滿了會 `EAGAIN`，跑一次就知道。（[Final Project](./final-project-pipe-eventfd-guided-reading.md)）
52. **LLM 的解釋是假說，不是答案。**——它給的行號、函式名、版本行為都要回原始碼驗證。（[Ch 53](./53-llm-assisted-reading.md)）
53. **工具會撒謊的方式各不相同。**——ctags 不懂 `#ifdef`、grep 不懂巨集、hunk header 的函式名是猜的；知道每個工具的盲點。（[Ch 48](./48-elixir-cscope-gtags.md)、[Ch 57](./57-reading-patches-and-review.md)）
54. **冷讀是一項可以訓練的技能。**——隨機抓函式、限時、事後對答案，像刷題一樣。（[Ch 54](./54-cold-read-random-function.md)–[Ch 56](./56-cold-read-network-rx.md)）

## (b) 冷讀檢查清單

**開始前（2 分鐘）**

```
□ 寫下一個具體問題（能用一段程式碼回答的那種）
□ 確認版本：我讀的是哪個 tag？.config 是什麼？（不同版本程式碼可能差很多）
□ 找入口：syscall？ops callback？initcall？我的問題從哪個入口進來？
□ 找資料結構：這段 code 操作哪幾個 struct？先讀它們的定義與註解
□ 開好工具：clangd / cscope / elixir 一個、git blame 一個、筆記一個
```

**讀的時候**

```
□ 每讀一個函式，先猜它做什麼，再讀內文
□ 不懂一行時，先診斷是哪一道鴻溝，再用對應處方（見 (c)）
□ 遇到函式指標：寫下「這裡可能呼叫誰」，標記為待查，或立刻找賦值點
□ 遇到鎖：記下拿鎖、放鎖的位置，以及鎖內讀寫了哪些欄位
□ 遇到 wait：記下等待條件，找到對應的 wake_up
□ 遇到看起來多餘或奇怪的程式碼：標記，結束前 blame 它
□ 每 15 分鐘問一次：我還在回答原本的問題嗎？
```

**卡住時**

```
□ 能說出卡在哪一道鴻溝嗎？說不出來 → 目標鴻溝，回到開始前
□ 同一個地方卡超過 20 分鐘 → 換一種處方（靜態 → 動態、讀 code → 讀文件）
□ 把卡住的地方寫成一個問題，放進「未解問題」清單，先跳過
□ 降低範圍：只追這一條 call path，其他分支全部當黑箱
```

**結束時**

```
□ 能用三句話回答原本的問題嗎？寫下來
□ 畫出至少一張圖（資料結構圖或 call path）
□ 未解問題清單整理好了嗎？每個問題標上「下次從哪裡查」
□ 有沒有一個可以用小程式或 trace 驗證的假說？驗證它
```

### 用一次：隨機抓一個函式

用固定種子從 `fs/*.c` 隨機抓函式（真實輸出）：

```
$ cd ~/code_reading/linux
$ rg -n --no-heading '^(static )?[a-z_ ]+\*?[a-z_0-9]+\(' fs/*.c | shuf -n 3 --random-source=<(yes 58)
fs/pnode.c:27:static inline struct mount *next_slave(struct mount *p)
fs/fs_parser.c:367:int fs_param_is_blockdev(struct p_log *log, const struct fs_parameter_spec *p,
fs/statfs.c:321:static int put_compat_statfs64(struct compat_statfs64 __user *ubuf, struct kstatfs *kbuf)
```

取第一個，`fs/pnode.c:27–30`（v6.18）：

```c
static inline struct mount *next_slave(struct mount *p)
{
	return hlist_entry(p->mnt_slave.next, struct mount, mnt_slave);
}
```

照清單走：

- **問題**：「給一個 mount，`next_slave` 回傳什麼？」
- **資料結構**：`rg -n 'mnt_slave' fs/mount.h` → `fs/mount.h:69–71`：`struct hlist_head mnt_slave_list;`（我的 slave 們）、`struct hlist_node mnt_slave;`（我在 master 的 slave list 裡的節點）、`struct mount *mnt_master;`
- **診斷**：`hlist_entry` 不認得 → 慣用法鴻溝。處方：它是 `container_of` 的別名（[Ch 23](./23-list-head-hlist.md)）
- **三句話答案**：`p` 是某個 master 的 slave 之一；`p->mnt_slave.next` 是同一個 master 底下下一個 slave 的節點；`hlist_entry` 從節點回到包住它的 `struct mount`。
- **未解問題**：走到 list 結尾時 `next` 是 NULL，`hlist_entry(NULL, ...)` 會回傳一個非 NULL 的壞指標——呼叫者怎麼避免走過頭？→ 下次從 `next_slave` 的呼叫者查。（`rg -n 'next_slave\(' fs/pnode.c`）
- **領域**：「slave mount」是什麼？→ 領域鴻溝，去讀 `Documentation/filesystems/sharedsubtree.rst`。

四行的函式，清單逼出了一個資料結構、一個慣用法、一個邊界問題、一個領域缺口。這就是清單的用處。

順手把那個未解問題收掉。`rg -n 'next_slave\(' fs/pnode.c` 找到兩個呼叫點（第 187、247 行），第一個在 `__propagation_next()`，`fs/pnode.c:176–191`（v6.18）：

```c
static struct mount *__propagation_next(struct mount *m,
					 struct mount *origin)
{
	while (1) {
		struct mount *master = m->mnt_master;

		if (master == origin->mnt_master) {
			struct mount *next = next_peer(m);
			return (next == origin) ? NULL : next;
		} else if (m->mnt_slave.next)
			return next_slave(m);

		/* back at master */
		m = master;
	}
}
```

答案在 `else if (m->mnt_slave.next)`：呼叫者**先檢查** `next` 不是 NULL 才呼叫 `next_slave()`，所以 `next_slave` 本身不必處理結尾。這是 kernel 常見的分工——小 helper 假設前置條件成立，檢查責任放在呼叫者。從這個例子可以再抽出一條讀碼習慣：**看到一個不檢查邊界的 helper，答案通常在呼叫者那一行的條件式裡**。

### 清單的兩種用法

借用航空界的分類，清單有兩種用法，對應讀碼的兩種情境：

| 用法 | 怎麼做 | 讀碼情境 |
|---|---|---|
| **read-do**（照唸照做） | 一項一項唸，唸一項做一項 | 剛開始冷讀、讀完全陌生的子系統、精神不好的時候 |
| **do-confirm**（做完再核對） | 憑經驗做完，停下來對一次清單，看有沒有漏 | 熟悉的子系統、時間壓力下的 bug 追蹤、review patch 的最後一步 |

L1–L3 的讀者用 read-do；L4 以上的讀者多半改成 do-confirm——但**卡住時退回 read-do**。退回不丟臉，它通常是最快的脫困方式。

## (c) 每道鴻溝的處方速查表

| 鴻溝 | 症狀 | 第一招 | 第二招 | 第三招（重武器） | 常見誤用 |
|---|---|---|---|---|---|
| **慣用法** | 每個字都認得，整行看不懂 | 查本課 Part 3 的巨集表；elixir 點進定義 | `make <file>.i` 展開前置處理器 | 寫一個 user-space 最小程式重現這個寫法 | 硬猜巨集語意而不展開 |
| **間接** | 追到 `->op()` 斷掉 | 搜賦值點：`rg '\.op\s*='` | clangd「find references」看 struct 欄位被誰設定 | ftrace `function_graph` / bpftrace / QEMU+gdb 看實際呼叫 | 只搜呼叫點，結果一堆 `->op(` |
| **領域** | 語法懂，不知道在幹嘛 | Documentation/ 對應子系統的 `.rst` | LWN Kernel Index 找設計文章 | 讀書（Love、Bovet & Cesati）或原始論文 | 繼續讀 code 期望自己悟出來 |
| **脈絡** | 懂在幹嘛，不懂為什麼這樣寫 | `git blame -w` 找到引入的 commit | `git log -L :func:file` 看整段演進 | `Link:` 到 lore 讀討論，含被否決的版本 | 用 blame 只看最後一次改動（常常是格式調整） |
| **目標** | 讀了很久不知道在幹嘛 | 停下來，寫一個具體問題 | 縮小範圍：只追一條 path | 換讀法：先寫小程式觀察行為，再讀 code | 從檔案第一行讀到最後一行 |

**連鎖卡關**：真實情況常常是兩道以上疊在一起。順序原則是**先解目標，再解領域，最後解慣用法與間接**——沒有問題時解慣用法是在浪費時間；不懂領域時追間接呼叫會追到一堆看不懂的函式。脈絡鴻溝通常最後處理，因為它回答的是「為什麼」，而你要先知道「是什麼」。

## (d) 自我評量階梯

| 級別 | 能做到什麼 | 典型驗證方式 | 對應本課 |
|---|---|---|---|
| **L0 入門前** | 能讀自己寫的 C；kernel code 看三行就放棄 | — | 開課前 |
| **L1 會讀 user-space** | 拿到一個陌生的 user-space 工具（幾百行），一小時內畫出結構、回答指定問題 | 練習 A、B | Part 1–2 |
| **L2 看得懂每一行 kernel** | 隨機抓一段 kernel 函式，能逐行解釋每個巨集與慣用法，不需要查超過三次 | 練習 C | Part 3 |
| **L3 看得懂資料與控制流** | 能畫出一個子系統的資料結構地圖；追函式指標不會斷；讀得懂鎖與 wait queue 的配對 | 練習 D、E | Part 4–5 |
| **L4 看得懂為什麼** | 對任何一段奇怪的程式碼，能在 30 分鐘內透過 git/lore 找到它的設計理由 | 練習 F | Part 6 |
| **L5 看得懂系統** | 拿到一個沒碰過的子系統，幾天內寫出導讀報告：架構、入口、關鍵路徑、並行、歷史 | Final Project | Part 7–9 |
| **L6 能審查** | 能 review 一個陌生子系統的 patch，提出被 maintainer 認真回應的技術意見 | 在 mailing list 上真的 review 一個 patch | Ch 57 之後的長期練習 |
| **L7 精通** | 讀 code 的速度接近寫 code 的人；能預測一個改動會影響哪些地方；別人卡住時來問你 | 你被列進某個 `MAINTAINERS` 條目的 `R:`（reviewer） | 數年的累積 |

誠實的提醒：**L5 到 L7 不是讀完一門課能達到的**。L5 是本課的終點；L6、L7 靠的是下面的長期練習。

## (e) 讀完本課之後的長期練習計畫

| 頻率 | 練習 | 時間 | 產出 |
|---|---|---|---|
| **每天** | 冷讀一個隨機函式（用上面的 `shuf` 指令），走一遍清單 | 15 分鐘 | 三句話答案 + 一個未解問題 |
| **每週** | 從 lore 挑一個你有興趣子系統的 patch，按 Ch 57 的七步驟讀，寫下你的 review 意見（不一定要寄出） | 1 小時 | 一份私人 review |
| **每週** | 讀一篇 LWN kernel 文章，找到它提到的程式碼，對照讀 | 1 小時 | 文章 ↔ 程式碼的對照筆記 |
| **每月** | 選一個沒碰過的子系統（`fs/eventpoll.c`、`kernel/futex/`、`mm/mmap.c`……），照 Final Project 的格式寫一份縮小版導讀 | 4–8 小時 | 一份導讀報告 |
| **每季** | 換一個非 kernel 的大型 C/C++ 專案（SQLite、Postgres、nginx、CPython）做同樣的事 | 一個週末 | 驗證方法的可攜性 |
| **長期** | 在 mailing list 上寄出第一個真正的 `Reviewed-by:` 或回覆 | — | 進入 L6 |

兩個不要做的事：

- **不要囤積「之後要讀」的清單**。清單會無限長，讀的時間不會。每週的練習對象，週一決定，週日結束。
- **不要只讀不驗證**。每份導讀至少要有一個 user-space 實驗或一個 trace，否則你只是在讀自己的猜測。

## 進階：寫你自己的箴言

本章的 54 條是**本課**的經驗。真正有用的箴言清單，最後一定是你自己的：每次卡關後脫困，花一分鐘問「下次怎麼更快？」，寫成一條。格式建議：

```
#NN  <一句命令句，十五字以內>
     情境：<什麼時候會用到>
     來源：<哪一次卡關、哪個檔案、哪一天>
     反例：<什麼情況下這條不適用>
```

「反例」這一欄最重要。沒有反例的箴言會被過度套用——例如「先讀資料結構」在讀純演算法（排序、壓縮）時就不太適用，因為那類程式碼的核心在控制流不在資料形狀。寫得出反例，代表你懂這條箴言的**適用範圍**，而不只是記住了它。

另一個進階練習是**反向使用清單**：拿一份你寫得最好的導讀筆記，倒推它做到了清單的哪幾項，然後拿一份你半途放棄的筆記做同樣的事。兩份的差異，就是你個人最常漏掉的那一步。

## 踩雷集錦

1. **「箴言背起來就會讀了」**：箴言是索引，不是能力。每一條背後都是一章的練習；沒做過那章的練習，這條箴言對你只是一句話。
2. **「清單太長，熟了就不用了」**：飛行員飛了一萬小時還是逐條唸 checklist。清單的價值在你**累的時候、卡的時候**——正好是你最想跳過它的時候。
3. **「卡住就是要更努力讀」**：卡住的正確反應是**換處方**，不是同一招用更久。同一個地方 20 分鐘沒進展，換鴻溝重新診斷。
4. **「L5 之後就是 L6」**：讀懂和能審查是兩種能力。審查要求你主動找出**不存在**的程式碼（漏掉的錯誤處理、漏改的呼叫點），這需要對子系統的不變量有完整的地圖。
5. **把自我評量當成考試**：階梯是用來決定下一步練什麼，不是用來給自己打分數。卡在 L3 的人，該做的是多追幾條 call path，不是去讀 L6 的資料。

## 動手練習

1. 用 `shuf` 指令換一個種子（`yes 1`、`yes 2`……）抽三個函式，每個用 15 分鐘走完檢查清單。記錄每個函式你主要卡在哪一道鴻溝。
2. 回頭看你在練習 A 寫的筆記，對照本章 (b) 的清單：哪幾項你當時沒做？
3. 從 (a) 挑五條你最常違反的箴言，寫在便條上貼在螢幕旁邊，一個月後換一批。

## 本章重點整理

- 卡住時不要回想，要查表：先診斷鴻溝，再用 (c) 的對應處方。
- 檢查清單的四個階段：開始前定問題與入口、讀的時候猜與記、卡住時換招、結束時三句話回答與畫圖。
- 本課的終點是 L5（能寫子系統導讀）；L6 以上靠每天、每週、每月的長期練習。

## 自我檢核

- [ ] 不看本章，能說出五道鴻溝各自的症狀與第一招處方
- [ ] 能從 (a) 中每一類各舉一條箴言，並說出它在本課哪個例子裡出現過
- [ ] 如果同事說「我讀 kernel 都卡在追函式指標」，你能給他三個由輕到重的具體做法
- [ ] 誠實評估：你現在在階梯的哪一級？要到下一級，具體缺的是哪一個能力？
- [ ] 寫下你下週一要冷讀的子系統名稱與要回答的問題

## 延伸閱讀

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪裡**：附錄的 maxims 清單（原書每章結尾也有對應的條目）
  - **學到什麼**：對照本章的 (a)，看哪些原則二十年沒變、哪些被工具改變了（原書沒有 git 考古、動態追蹤、LLM）
  - **前提**：讀完本課；直接讀附錄而沒讀過正文，條目會很抽象
- **《The Checklist Manifesto》** — Atul Gawande（Metropolitan Books, 2009）
  - **這本書的定位**：外科醫師寫的、關於檢查清單為什麼在高技能領域有效的書；不談程式，但解釋了本章 (b) 存在的理由
  - **讀哪幾章**：談「do-confirm」與「read-do」兩種清單、以及清單該多長的章節
- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley, 2010）
  - **定位**：長期練習計畫中「每月一個子系統」的領域背景來源；版本老，但每章的概念模型仍然是很好的起點

### 論文

- **[Program Comprehension During Software Maintenance and Evolution](https://doi.org/10.1109/2.402076)** — A. von Mayrhauser & A. M. Vans，IEEE Computer（1995）
  - **核心貢獻**：整合 top-down、bottom-up 與 situation model，觀察到熟手會在策略間頻繁切換
  - **和本章的關聯**：箴言 49 與 (c) 的「換處方」原則的理論依據——卡住時切換策略不是退縮，是熟手的正常行為

### 官方文件

- **[Linux kernel documentation](https://docs.kernel.org/)**
  - **讀哪裡**：`process/` 底下的 `submitting-patches`、`researcher-guidelines`；`filesystems/` 底下你要冷讀的子系統
  - **用法**：長期練習時，每冷讀一個子系統，先找它在 Documentation/ 的對應頁面

箴言與清單是地圖，最後一份作業是拿這張地圖走完一整個子系統：fs/pipe.c 與 eventfd。

→ [Final Project：完整導讀 fs/pipe.c 與 eventfd](./final-project-pipe-eventfd-guided-reading.md)
