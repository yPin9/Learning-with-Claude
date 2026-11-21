# Ch 42 — 讀 patch / commit 找 bug：patch diff → root cause 方法論、CVE 拆解

> **目標**：給一個 upstream 的 fixing commit（或一個 CVE 編號），能獨立重建出「原始漏洞是什麼、屬於 [Ch 10](./10-vulnerability-taxonomy.md) 哪一類、觸發它需要什麼 syscall 序列、它值多少可利用性」——這是所有 N-day 開發的第一步，也是 [Ch 50](./50-patch-to-exploit.md) 完整 walkthrough 的前半段。

## 為什麼需要這個？

[Ch 43](./43-syzkaller.md) 教的 fuzzing 是「從零生出一個 bug」；這一章教的是反方向——**bug 已經有人修好了，你要從那個修補動作反推出 bug 長什麼樣**。這條路看起來比 fuzzing 遜色（別人都修好了，還有什麼好打的？），但它是 kernel 安全產業裡最實際賺錢、也最常被低估的一條路，理由很具體：

- **stable kernel 永遠落後 mainline**。Google 每天在 mainline 上修幾十個漏洞，但你手上的 Android 手機、雲端 VM、嵌入式設備可能跑著半年前甚至兩年前凍結的 kernel。一個 mainline commit 修掉的洞，在那些裝置上**還活著**——這就是 N-day：漏洞編號是「N 天前就修好了」，但目標系統沒跟上。
- **patch diff 比 fuzzer crash log 乾淨得多**。fuzzer 給你的是一段 call trace 和一個 KASAN splat（[Ch 44](./44-debug-toolchain.md)），你要自己走查程式碼才知道根因在哪；patch diff 直接告訴你「開發者認為正確的程式碼長什麼樣」，兩者一減，根因幾乎是送到你面前。
- **這是真實攻防的常態**。防守方一天修幾十個 commit，攻擊方沒有資源逐一驗證。能快速從 diff 判斷「這個值不值得追」，是比會寫 exploit 更稀缺的技能。Google Project Zero、各大 vendor 的漏洞研究團隊，日常工作有一大塊就是盯 `git log` 掃 commit。

這章教的不是某個特定 CVE 的細節，是一套**可以重複套用在任何 commit 上**的讀法。

## 先建立直覺

把一個 fixing commit 想成**犯罪現場的「事後」照片**。你沒有「事前」照片，但你有：

```
┌─────────────────────────────────────────────┐
│  commit message                              │  ← 偵探的自白（但不一定全說實話）
│    "fix use-after-free when X happens"       │
├─────────────────────────────────────────────┤
│  diff（事後現場）                             │
│  - 舊程式碼（事前，犯罪發生時的樣子）           │
│  + 新程式碼（事後，補上的鎖/檢查/順序）         │
└─────────────────────────────────────────────┘
          │
          │  反推
          ▼
┌─────────────────────────────────────────────┐
│  事前現場重建：                               │
│  - 缺了這段檢查/鎖/順序時，哪個狀態是可達的？   │
│  - 要走到那個狀態，使用者（attacker）要做       │
│    哪幾個動作（syscalls）？                   │
└─────────────────────────────────────────────┘
```

你的工作不是「讀懂新程式碼」，是**讀懂新程式碼補上去的那一小段差異，到底在防誰**。diff 裡加的每一行都是一句無聲的控訴：「少了我，有人可以做到 X」。你的任務就是把 X 說清楚。

## 核心概念：一份 fixing commit 的解剖

Linux kernel commit 有約定成俗的格式，認得這套格式，讀 diff 的效率會差一個量級。

```
netfilter: nf_tables: deactivate anonymous set from preparation phase   ← 標題：<子系統>: <一句話說做了什麼>

Toggle deleted anonymous sets as inactive in the next generation, so   ← body：為什麼要做這個改動
users cannot perform any update on it. Clear the generation bitmask
in case the transaction is aborted.

The following KASAN splat shows a set element deletion for a bound    ← 附上觸發證據（常見但非必有）
anonymous set that has been already removed in the same transaction.

[   64.921510] ====...
[   64.923123] BUG: KASAN: wild-memory-access in nf_tables_commit+...
...

Fixes: 958bee14d071 ("netfilter: nf_tables: use dedicated mutex...")  ← 指向「引入這個 bug 的那個 commit」
Signed-off-by: Pablo Neira Ayuso <pablo@netfilter.org>                  ← 作者
```

四個欄位各有用途：

| 欄位 | 讀法 |
|---|---|
| 標題 | 先看動詞——"fix"、"deactivate"、"check"、"add lock"。動詞本身就在暗示 bug 的類型（加檢查 → 原本缺檢查；加鎖 → 原本有 race；加 ordering → 原本的順序錯）。 |
| body | 作者有時會解釋「為什麼」，但很少解釋「怎麼觸發」——那是你要自己補的部分。如果附了 crash log（KASAN splat、GPF trace），直接當線索用，不用自己猜。 |
| `Fixes:` tag | 指向**引入**這個 bug 的 commit，不是指向這次修復的 commit。這個 tag 是 stable tree 決定「要不要 backport」的依據——也是你判斷**哪些版本有洞**的最快方法（下一節講怎麼用）。 |
| `Cc: stable@vger.kernel.org` | 如果有這行，代表作者認為這個修復值得 backport 到舊的 LTS 分支。看到它，直接假設**所有還沒合併這個 commit 的 stable 分支都中獎**。 |

> `Fixes:` tag 不是必填欄位，很多年代較早或作者沒細查的 commit沒有它。沒有 `Fixes:` 不代表 bug 很新，只代表沒人去挖引入點——你稍後會學到自己挖（`git blame`/`git bisect`）。

## 底層機制：三種典型的 diff 模式，對應三類根因

讀多了會發現，kernel 的 fixing diff 幾乎都落在三種模式裡，每種直接對應 [Ch 10](./10-vulnerability-taxonomy.md) 的某一格：

```
模式一：加檢查（most common）
-     buf[idx] = val;
+     if (idx >= ARRAY_SIZE(buf))
+         return -EINVAL;
+     buf[idx] = val;
→ Ch 10：OOB write（長度/邊界檢查缺）

模式二：加鎖 / 改順序
-     obj->use--;
-     kfree(obj);
+     mutex_lock(&obj->lock);
+     obj->use--;
+     if (!obj->use)
+         kfree(obj);
+     mutex_unlock(&obj->lock);
→ Ch 10：race / refcount 失衡 → 熬成 UAF

模式三：加/調整 activate-deactivate 的生命週期順序
-     set->use++;
+     if (nft_set_is_anonymous(set))
+         nft_clear(ctx->net, set);
+     set->use++;
→ Ch 10：logic bug（狀態機順序錯）→ 熬成 UAF 或 type confusion
```

看到新增的那幾行，先問：**它在補什麼？**

- 補一個邊界/型別/權限的**檢查**——原本是 memory corruption 或權限缺失。
- 補一個**鎖**或改變加鎖範圍——原本是 race（TOCTOU）。
- 調整**操作順序**（先做 A 還是先做 B）——原本是邏輯上的狀態機漏洞，通常熬成 UAF。

這一步做完，你已經完成了「對號入座到 Ch 10 總表」，下一步才是具體重建觸發路徑。

## 範例一：CVE-2023-32233 —— nf_tables 的 UAF（logic bug 類）

> 以下 commit hash、commit message、KASAN splat均由 GitHub API 查證（`git show c1592a89942e9678f7d9c8030efa777c0d57edab`），非憑記憶轉述。這是一個真實、公開、已修復的 CVE，Linux 官方編號 CVE-2023-32233，影響到 6.3.1 之前的版本。

commit：`c1592a89942e9678f7d9c8030efa777c0d57edab`，作者 Pablo Neira Ayuso（nf_tables 維護者），2023-05-03 合併進 mainline。commit message：

```
netfilter: nf_tables: deactivate anonymous set from preparation phase

Toggle deleted anonymous sets as inactive in the next generation, so
users cannot perform any update on it. Clear the generation bitmask
in case the transaction is aborted.

The following KASAN splat shows a set element deletion for a bound
anonymous set that has been already removed in the same transaction.

[   64.921510] ==================================================================
[   64.923123] BUG: KASAN: wild-memory-access in nf_tables_commit+0xa24/0x1490 [nf_tables]
[   64.924745] Write of size 8 at addr dead000000000122 by task test/890
[   64.927903] CPU: 3 PID: 890 Comm: test Not tainted 6.3.0+ #253
[   64.931120] Call Trace:
[   64.932699]  <TASK>
[   64.934292]  dump_stack_lvl+0x33/0x50
[   64.935908]  ? nf_tables_commit+0xa24/0x1490 [nf_tables]
[   64.937551]  kasan_report+0xda/0x120
[   64.939186]  ? nf_tables_commit+0xa24/0x1490 [nf_tables]
[   64.940814]  nf_tables_commit+0xa24/0x1490 [nf_tables]
[   64.948898]  nfnetlink_rcv_batch+0x709/0xd90 [nfnetlink]

Signed-off-by: Pablo Neira Ayuso <pablo@netfilter.org>
```

先讀 diff（`net/netfilter/nf_tables_api.c` 與三個 expr 檔案）：

```c
// net/netfilter/nf_tables_api.c
+void nf_tables_activate_set(const struct nft_ctx *ctx, struct nft_set *set)
+{
+	if (nft_set_is_anonymous(set))
+		nft_clear(ctx->net, set);
+
+	set->use++;
+}
+EXPORT_SYMBOL_GPL(nf_tables_activate_set);

 void nf_tables_deactivate_set(const struct nft_ctx *ctx, struct nft_set *set,
 			      struct nft_set_binding *binding,
 			      enum nft_trans_phase phase)
 {
 	switch (phase) {
 	case NFT_TRANS_PREPARE:
+		if (nft_set_is_anonymous(set))
+			nft_deactivate_next(ctx->net, set);
+
 		set->use--;
 		return;
```

同時，`nft_dynset.c` / `nft_lookup.c` / `nft_objref.c` 三個檔案裡的 `_activate()` 函式全部從 `priv->set->use++;` 改成呼叫新寫的 `nf_tables_activate_set(ctx, priv->set)`。

**逐行重建根因：**

1. nf_tables 的 anonymous set（匿名集合，使用者可以在一次 netlink transaction 裡建立、綁定到 rule、再刪除）用 `set->use` 當 refcount，`use` 歸零才真正 `kfree`。
2. 修復前，`nft_dynset_activate` / `nft_lookup_activate` / `nft_objref_map_activate` 直接 `set->use++`，**完全沒檢查這個 set 在同一筆 transaction 裡是不是已經被標記成要刪除**。
3. 問題場景：使用者在**同一個 netlink batch** 裡先刪除一個 anonymous set（進入 `NFT_TRANS_PREPARE` 階段，`use--`），接著在同一批次裡又讓另一條 rule 綁定（activate）同一個已經要被刪的 set——`use++` 把它救回來，但 nft_tables 的 generation bitmask（決定這個物件在「下一代」是否存活）沒有同步更新。commit 階段跑到 `nf_tables_commit`，照著 generation 判斷該物件已死，對一塊實際上還在被引用、甚至已經被釋放的記憶體寫入——對應 KASAN splat 裡那個離奇的位址 `dead000000000122`（這類 `dead...` 位址是 kernel 故意把已釋放記憶體的指標欄位寫成哨兵值的結果，一眼就能認出是 UAF/release-after-free，而不是普通野指標）。
4. **對號入座到 [Ch 10](./10-vulnerability-taxonomy.md)**：這是一個 logic bug——`activate`/`deactivate` 的生命週期狀態機沒有對齊 transaction 的 prepare/commit/abort 三階段，根因不是記憶體檢查缺失，是**狀態轉換順序錯**。它熬成的產物是 UAF（commit 階段對已經邏輯上死亡、甚至物理上已釋放的 set 做寫入）。

**反推觸發所需的 syscall/操作序列**（未實測，依 commit message 與 nf_tables netlink 介面理論重建；實測需要你在自有環境用 `nft`/libnftnl 或手刻 netlink batch 驗證）：

```
1. NFT_MSG_NEWTABLE / NFT_MSG_NEWCHAIN   建立 table/chain
2. NFT_MSG_NEWSET（匿名、NLM_F_EXCL|...）  建立一個 anonymous set
3. 單一 netlink batch 內：
     a. NFT_MSG_DELSET   刪除該 anonymous set（進入 PREPARE，use--）
     b. NFT_MSG_NEWRULE  在同一個 batch 裡新增一條 rule，
                         該 rule 透過 nft_lookup/nft_dynset 綁定「剛才要刪的那個 set」
                         （activate 路徑，use++，但沒清 generation bitmask）
4. commit 這個 batch → nf_tables_commit 走到對該 set 的操作時觸發 UAF
```

**可利用性評估**：unprivileged user 若能建立 user namespace 中具 `CAP_NET_ADMIN` 的 netns（[Ch 36](./36-user-namespace.md) 講的攻擊面放大），就能打這條路——這也是為什麼 nf_tables 長年是 kernelCTF 的最大礦區（[Ch 30](./30-netfilter-nftables.md)）。UAF 本身只是「邏輯上的生命週期錯位」，但後續可以走 [Ch 18](./18-uaf-double-free.md)、[Ch 19](./19-spray-objects.md) 的標準 spray 鏈路把它升級成任意寫。

## 範例二：CVE-2023-2598 —— io_uring 的 OOB（integer/假設錯誤類）

commit `776617db78c6d208780e7c69d4d68d1fa82913de`，作者 Tobias Holl，`Fixes: 57bebf807e2a ("io_uring/rsrc: optimise registered huge pages")`。diff 只有五行，是本章至今讀過最小的一個：

```c
// io_uring/rsrc.c
	if (nr_pages > 1) {
		folio = page_folio(pages[0]);
		for (i = 1; i < nr_pages; i++) {
-			if (page_folio(pages[i]) != folio) {
+			/*
+			 * Pages must be consecutive and on the same folio for
+			 * this to work
+			 */
+			if (page_folio(pages[i]) != folio ||
+			    pages[i] != pages[i - 1] + 1) {
				folio = NULL;
				break;
			}
```

**重建根因**：`io_uring` 註冊使用者提供的 buffer 時，若偵測到底層連續多個 `struct page` 屬於同一個 huge page `folio`，就會把它們「合併」成一個 bvec entry 來省記憶體（`57bebf807e2a` 引入的優化）。但「屬於同一個 folio」不等於「這些 page 在實體位址上連續」——同一塊大頁裡的 page 陣列順序理論上可以不連續地被存取到（例如透過特殊的 mapping 操作讓 `pages[]` 陣列裡的順序跳著排）。原本的檢查只驗證了 `page_folio(pages[i]) == folio`，沒有驗證 `pages[i]` 緊接在 `pages[i-1]` 後面。合併後產生的單一 bvec entry，其長度跨過了真正連續的實體範圍——後續對這個 buffer 做 I/O 時，access 到的實體位址超出了原本該 buffer 覆蓋的範圍，形成**對任意實體記憶體的 OOB 存取**。

**對號入座**：這不是傳統的「`size` 算術溢位」，但本質一樣——**一個沒驗證完整的假設（folio 相同 ⟹ 連續）被拿去做了記憶體範圍合併的決策**，屬於 Ch 10 的 OOB write/read 類，根因可以歸在「邊界/假設檢查缺」。這個模式值得記：很多看似「加密碼學等級判斷錯誤」的漏洞，拆開來看都只是一個隱含假設沒被驗證。

**觸發所需操作**（未實測，理論重建）：透過 `io_uring_register`（`IORING_REGISTER_BUFFERS`）註冊一塊使用者記憶體，該記憶體底層由 huge page 支撐，但透過特定手法（例如部分 remap）使得 `get_user_pages` 拿到的 `pages[]` 陣列裡,屬於同一 folio 但不連續的頁面相鄰排列；之後對這個已註冊的 buffer 發起讀寫 I/O（如 `IORING_OP_READ`/`WRITE_FIXED`），kernel 依照錯誤合併後的 bvec 去存取,實際落到了鄰近、未授權的實體頁面。

## 從 diff 找「引入點」：git log -p 與 git blame

修復 commit 常附 `Fixes:` tag，但沒附的時候，你要自己挖。兩個工具：

```bash
# 針對一個函式/一段邏輯，看它的歷史變更，-p 會印出每次變更的 diff
git log -p --follow -- net/netfilter/nf_tables_api.c | less

# 針對某一行（例如修復後新增檢查前的那一行），回溯是誰、哪個 commit 寫下它
git blame -L 5120,5140 net/netfilter/nf_tables_api.c
```

`git blame` 給你的是「這一行最後一次被改動的 commit」，不一定是「引入 bug 的 commit」——如果這段邏輯後來被其他 refactor 動過但沒修 bug，blame 只會指到 refactor 那次。更精確的做法是 `git bisect`：

```bash
# 如果你有一個可重現的觸發方式（來自 Ch 43 的 reproducer，或你自己手刻的 PoC）
git bisect start <known_bad_commit> <known_good_commit>
# 每一步編譯跑一次 PoC，依結果回答 good/bad，bisect 收斂到引入點
git bisect good   # 或 git bisect bad
```

syzbot 本身就是用這套機制做「cause bisection」（[Ch 43](./43-syzkaller.md) 會展開），你在分析任何一個 syzbot report 時，可以直接讀它幫你跑好的 bisection log，省掉自己跑一次的時間。

## 讀 diff 語法本身：unified diff 速查

如果你還不熟 unified diff 格式，先把這個記熟，否則讀 hunk header 會卡：

```
@@ -5127,12 +5127,24 @@ static void nf_tables_unbind_set(...)
   │     │  │     │
   │     │  │     └─ 新檔案裡，這段從第幾行開始、共幾行
   │     │  └─ 新檔案的起始行號
   │     └─ 舊檔案裡，這段從第幾行開始、共幾行
   └─ 舊檔案的起始行號
```

`-` 開頭的行是舊版本有、新版本沒有（被刪除或被取代）；`+` 開頭的行是新版本新增的；沒有前綴符號的行是**上下文**（兩版都有，單純用來讓你定位）。讀一個 hunk 時，先掃一遍沒有符號的上下文行，搞清楚你正站在哪個函式的哪個邏輯分支裡，再去看 `-`/`+` 具體改了什麼——直接跳到變更行而跳過上下文，常常會誤判變更的範圍（例如漏看變更其實發生在一個 `if` 的某個分支裡，不是整個函式都受影響）。

## 評估可利用性：一個檢查清單

重建出觸發路徑之後，別急著下「這能不能打」的結論，依序問這幾個問題（呼應 [Ch 10](./10-vulnerability-taxonomy.md) 的四軸原語語言）：

1. **誰能觸發？** 需要 root，還是任何 unprivileged user？需要特殊 capability（`CAP_NET_ADMIN`、`CAP_SYS_ADMIN`）還是普通 netns 就能碰到？——範例一的 nf_tables 洞,只要能進一個有 `CAP_NET_ADMIN` 的 user namespace 就碰得到，攻擊面因此大很多（見 [Ch 36](./36-user-namespace.md)）。
2. **原語有多強？** 回 Ch 10 的四軸：讀/寫、相對/絕對、受限/任意值、一次/可重複。範例一是 UAF（熬出來的 type confusion 潛力),範例二是直接的 OOB 讀寫實體記憶體——後者原語起點更強,不需要額外的 spray 就已經是 arbitrary physical memory access 的量級。
3. **觸發穩定嗎？** 需要特定時序（race window）還是任何時候都能穩定重現？range 從「每次必中」到「機率性,需要重打幾千次」,直接決定 exploit 開發的工程量（[Ch 41](./41-widening-race-window.md)、[Ch 45](./45-exploit-stabilization.md)）。
4. **這個版本範圍有多大？** 用 `Fixes:` tag 配合 `git log <tag1>..<tag2> -- <path>` 找出引入版本與修復版本之間的所有 release，再去查你的目標系統（例如一支 Android 裝置的 kernel 版本）落在哪個區間。
5. **有沒有現成的 mitigation 擋著？** 這個洞給的原語,碰上目標系統實際開的 KASLR/SMEP/SMAP/CFI 組合,還差幾步才能變成完整 exploit？這一步直接銜接 [Ch 50](./50-patch-to-exploit.md) 的完整 walkthrough。

## 對比與取捨：三條找洞的路

| 方法 | 起點 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|---|
| 讀 patch / commit（本章） | 已知的 fixing commit / CVE | 根因幾乎現成，不用自己跑 fuzzer；N-day 開發最快入口 | 只能找「已經被人修過」的洞；舊版本才有價值 | 你的目標是落後版本的真實系統（Android、雲端 VM、嵌入式） |
| coverage-guided fuzzing（[Ch 43](./43-syzkaller.md)） | 跑起來的 kernel + syscall 描述 | 能找全新、沒人報過的 0-day | 需要運算資源、長時間跑；triage 一個 crash 到 root cause 仍要花工夫 | 目標是最新 mainline，或你要主動貢獻漏洞報告 |
| 手讀 source code | 一整個子系統的原始碼 | 能找到連 fuzzer 都覆蓋不到的邏輯漏洞（權限模型、狀態機設計缺陷） | 最耗時、最依賴經驗；沒有自動化輔助 | 你對某個子系統特別熟，或懷疑某類設計模式（如 refcount 不對稱）系統性地出現在多處 |

三者不互斥，實務上常常混用：fuzzer 找到 crash → 用這章的讀法做 triage → 讀 source 確認同一模式是否出現在其他函式裡（這正是 Project Zero 常見的「一個洞帶出一類洞」寫法）。

## 踩雷集錦

1. **把 commit message 當成完整的事實陳述**。commit message 是作者**為什麼改**的說明,不是**詳盡的攻擊面分析**。作者往往只描述他親眼見到的那個觸發路徑（常常來自一份 crash report），不代表那是唯一的觸發方式，也不代表他想清楚了所有後續影響。你自己重建觸發路徑時,經常會發現比 commit message 描述的更廣的攻擊面。
2. **`Fixes:` tag 一定指向真正的引入點**。tag 由人工填寫，依賴作者自己 `git blame`/回憶的結果，可能指到一個「相關但不是最初引入」的 refactor commit。沒有十足把握時,自己用 bisect 驗證一次,不要直接拿 tag 當結論去判斷哪些版本中獎。
3. **diff 小 = 影響小**。範例二只改了五行、加了一個條件判斷,CVSS 評分卻是高分的任意實體記憶體存取。diff 的大小只反映「修起來多容易」,不反映 bug 的嚴重度——嚴重度要看它開出的原語有多強（回 [Ch 10](./10-vulnerability-taxonomy.md) 的四軸）。
4. **只要是 CVE，就一定有清楚的 PoC/exploit 可查**。多數 CVE 的公開資訊停在「NVD 描述 + 修復 commit」,連一份完整的觸發步驟都沒有,更不用說 exploit。本章做的「反推」工作,正是因為多數時候你拿到的就只有這些。
5. **以為 stable tree 會自動把所有修復 backport 回去**。`Cc: stable@vger.kernel.org` 只是「作者建議 backport」，真正是否被各個 LTS 分支採用，要去對應分支的 git log 裡確認（`git log --oneline <stable-branch> | grep <短 hash>`）。沒被 backport 的洞,在那條分支上就是活的 N-day,即使 mainline 早就修了。

## 進階：規模化做這件事

單篇 commit 分析是基本功，真正的 N-day 開發團隊做的是**規模化掃描**：

- **逐日/逐週 diff 兩個 stable tag**，抓出所有安全相關 commit（關鍵字：`UAF`、`use-after-free`、`overflow`、`race`、`NULL deref`、`Fixes:`），批量過 triage。
- **訂閱 `linux-stable-mirror` / `oss-security` mailing list**，許多安全修復會先在這裡討論，比 CVE 公告早。
- **交叉比對 syzbot 的 dup 清單**：syzbot 會把同根因的多份 crash report 標記為重複（`#syz dup`），這些重複群組本身就是一種「這個 bug 很容易被 fuzzer 撞到」的訊號——代表它可能有一個相對淺的觸發路徑。
- **留意同一子系統裡重複出現的模式**：nf_tables、io_uring 這類子系統反覆出現同一類根因（activate/deactivate 生命週期、refcount 不對稱）不是巧合——一旦你抓到一種模式，回頭在同一子系統的其他函式裡搜尋同樣的寫法,常常能提前抓到下一個還沒被發現的洞。這正是 [Ch 10](./10-vulnerability-taxonomy.md) 強調「原語思維」的實戰版本。

## 動手練習

1. 到 [kernel.org 的 stable release 公告](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/)，挑一個近期 6.6.x point release，找出其中一個帶 `Fixes:` tag 的安全相關 commit。
2. 不看任何外部 writeup，只憑 commit message + diff，寫下：這是 Ch 10 哪一類？直接產物是什麼原語？
3. 用 `git log -p` 或 `git blame` 追出 `Fixes:` tag 指向的那個引入點 commit，讀一遍，確認它和你的猜測吻合。
4. 試著寫出一句話的觸發步驟（即使無法實際驗證），格式仿照本章兩個範例的「反推觸發所需的 syscall/操作序列」段落。

## 本章重點整理

- Fixing commit 的四個欄位（標題、body、`Fixes:`、`Cc: stable`）各有讀法；`Fixes:` 指向引入點，不是指向本次修復。
- diff 的改動幾乎都落在三種模式：加檢查、加鎖/改順序、調整生命週期狀態機——分別對應 Ch 10 的 memory corruption 缺檢查、race、logic bug 三類根因。
- 讀懂 diff 之後要做的事：對號入座到 Ch 10 總表、反推觸發所需的操作序列、評估可利用性——這三步是本章方法論的核心。
- `git blame` 找「最後動過這行的人」，`git bisect` 找「真正引入 bug 的那次變更」，兩者用途不同不能互相取代。

## 自我檢核

- [ ] 不看筆記，能說出一份 fixing commit 的四個關鍵欄位,以及各自能告訴你什麼、不能告訴你什麼
- [ ] 給一段加了 `if` 檢查的 diff，能判斷它原本屬於 Ch 10 哪一類根因
- [ ] 能解釋為什麼 `Fixes:` tag 不一定準確,以及什麼情況下該用 `git bisect` 而不是直接信任它
- [ ] 能用 CVE-2023-32233 或 CVE-2023-2598 其中一個為例，口頭講出「從 diff 到觸發序列」的完整反推過程
- [ ] 能說出「讀 patch」「fuzzing」「手讀 source」三條找洞路徑各自的成本與適用情境

## 延伸閱讀

### 權威來源 / 真實案例

- **[nf_tables CVE-2023-32233 修復 commit](https://github.com/torvalds/linux/commit/c1592a89942e9678f7d9c8030efa777c0d57edab)**
  - **讀哪裡**：commit message 全文（含 KASAN splat）與四個檔案的 diff。
  - **和本章的關聯**：本章範例一逐行分析的就是這個 commit；對照原文可以驗證本章的推論。
- **[io_uring CVE-2023-2598 修復 commit](https://github.com/torvalds/linux/commit/776617db78c6d208780e7c69d4d68d1fa82913de)**
  - **讀哪裡**：五行 diff 本身,以及 `Fixes:` 指向的 `57bebf807e2a` 原始優化 commit——兩個一起讀才看得出「優化引入漏洞」的完整故事。
  - **和本章的關聯**：本章範例二;也是「diff 很小、影響很大」的最佳教材。

### 部落格 / 技術文章

- **[Google Project Zero blog](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：幾乎每篇都是「從一個 patch 或 crash report 出發，重建完整根因與 exploit」的實戰示範,寫作順序與本章方法論高度一致。
  - **讀哪裡**：搜尋標籤 "Linux"、"kernel"的文章；Jann Horn 與 Seth Jenkins 的系列對本章主題最直接相關。
  - **為什麼值得讀**：作者是目前業界公開 writeup 水準最高的團隊之一，讀法本身就是在學方法論。

### 官方文件 / 流程

- **[linux-stable-mirror mailing list archive](https://lore.kernel.org/stable/)**
  - **讀哪裡**：搜尋近期帶 `[PATCH]` 標題、內文含 `Fixes:` 的討論串，可以看到 backport 決策的即時過程。
  - **注意**：量很大,建議用關鍵字（子系統名 + "use-after-free"/"overflow"）過濾,不要整串讀。

這章給你的是「找到一個已知洞」的讀法；下一章反過來——如果你想找一個**還沒被任何人報告過**的洞，syzkaller 的 coverage-guided fuzzing 是現代 kernel 安全研究最主要的武器。

→ [Ch 43 — Syzkaller / syzbot：fuzzer 原理、看懂 report、跑自己的 fuzzer](./43-syzkaller.md)
