# Ch 54 冷讀實戰一：隨機抓一個 kernel 函式

> **目標**：建立一套可以重複的冷讀訓練：用固定種子隨機抽一個沒看過的 kernel 函式，在 45 分鐘內照 [Ch 58](./58-maxims-and-checklist.md) 的檢查清單讀完，最後交出三句話的答案、一張鴻溝診斷表和一份未解問題清單，再自己對答案。
> **鴻溝**：五道鴻溝全練。本章兩個實例，第一個以**慣用法鴻溝**和**間接鴻溝**為主，第二個以**領域鴻溝**為主。

> **環境**：WSL Ubuntu 22.04、Universal Ctags 6.2.0、Linux kernel tree 在 tag v6.18（`~/code_reading/linux`，blobless clone）。本章的抽題腳本、指令與輸出都實際跑過；引用的程式碼一律標 v6.18 的 `file:line`。

## 為什麼需要這個？

前面 53 章，每一章都是「我挑好一段程式碼，帶你讀」。挑選本身就是一種提示：你知道這章講 RCU，看到 `rcu_dereference` 就不會慌；你知道這章講 driver model，就會先找 `probe`。真實情況不是這樣。追 bug 時，`git bisect` 停在哪個函式不由你決定；讀 crash log 時，stack trace 最上面那一行可能是你從沒聽過的子系統。

**冷讀**是指在沒有任何事前提示的情況下，讀一段沒看過的程式碼。它是整門課的期末考，也是可以每天練的基本功。Spinellis 在原書最後一章用一個完整案例示範「把全部技巧串起來」；本章做的是同一件事，只是把案例換成**隨機抽到的函式**，讓你沒辦法事先準備。

隨機有兩個好處：

1. **它會抽到你的盲區。** 自己挑題目的人，會不自覺挑自己熟的子系統。隨機抽題逼你面對 `mm/`、`kernel/audit*`、`kernel/kexec*` 這些平常不會主動打開的地方。
2. **它讓練習可以重複、可以比較。** 固定種子 + 固定 tag，同一題任何人跑都抽到同一個函式。你可以三個月後重讀同一題，看自己進步多少；也可以跟別人對答案。

## 先建立直覺

把冷讀想成急診室的檢傷分類。病人（函式）推進來，你沒有病歷，只有 45 分鐘：

```
  0 min          10 min                      35 min          45 min
  ├──── 望診 ────┼────────── 檢查 ──────────┼──── 寫報告 ───┤
  │ 不用任何工具  │ 帶著問題用工具：            │ 三句話答案      │
  │ 只看這一段    │ rg / cscope / blame /      │ 鴻溝診斷表      │
  │ 寫下第一印象  │ 讀呼叫者 / 讀資料結構       │ 未解問題 + 下一步│
  │ 與鴻溝初診    │                            │ 對答案、評分    │
  └──────────────┴────────────────────────────┴───────────────┘
```

**望診**這段刻意不准開工具，因為它練的是「認得 pattern」的速度（[Ch 2](./02-program-comprehension-models.md) 的 beacon 與 chunking）。一開工具，你就會開始逐行追，失去全貌。**檢查**這段要帶著**一個**問題進去，不是「把它讀懂」，而是「`_pte` 指向哪裡」這種可以用一段程式碼回答的問題（[Ch 3](./03-question-driven-reading.md)）。**寫報告**這段強迫你把理解外部化（[Ch 7](./07-reading-notes-and-diagrams.md)），寫不出三句話就是還沒懂。

比喻的限制：急診醫生面對的病人不會「被設計成難懂」，但 kernel 函式有時候就是刻意寫得很緊湊（為了效能或為了和另一個函式對稱）。所以冷讀時看到「怪」的地方不要急著判斷是 bug，先假設作者有理由，然後去找那個理由。

## 抽題器：可重現的隨機

### 規則

| 規則 | 理由 |
|---|---|
| 只抽 25–70 行的函式 | 太短（`next_slave` 那種 4 行的）練不到東西，[Ch 58](./58-maxims-and-checklist.md) 已經示範過；太長的放到 Ch 55、56 和 Final 練 |
| 只用 ctags 認得的函式定義 | 巨集生成的函式（`SYSCALL_DEFINE*`）ctags 也會列，可以接受；抽到純巨集包裝就重抽一次 |
| 固定種子、固定 tag | 讓題目可以重現，可以三個月後重讀比較 |
| 抽到的第一題就讀，不准挑 | 挑題目就失去隨機的意義 |

### 腳本

`~/code_reading/scratch/54/coldread.sh`：

```bash
#!/bin/bash
# coldread.sh：從指定目錄隨機抽 N 個 25–70 行的函式當冷讀題目
# 用法：coldread.sh <種子> <N> <目錄...>     例：coldread.sh 54 5 kernel mm
seed=$1; n=$2; shift 2
cd ~/code_reading/linux || exit 1
files=(); for d in "$@"; do files+=("$d"/*.c); done
ctags --c-kinds=f --fields=+ne -o - "${files[@]}" 2>/dev/null |
awk -F"\t" '{ ln=""; en="";
       for (i = 4; i <= NF; i++) {
         if ($i ~ /^line:/) ln = substr($i, 6)
         if ($i ~ /^end:/)  en = substr($i, 5)
       }
       if (ln && en) { len = en - ln + 1
         if (len >= 25 && len <= 70) print $2 ":" ln "\t" len "\t" $1 } }' |
sort -u > /tmp/coldread.cands
# 注意：shuf 讀「檔案」和讀 stdin 的抽法不同，同一個種子結果會不一樣；固定讀檔案才可重現
shuf -n "$n" --random-source=<(yes "$seed") /tmp/coldread.cands
```

重點說明：

- `--fields=+ne` 讓 ctags 輸出 `line:` 和 `end:` 兩個欄位，`end - line + 1` 就是函式長度。Universal Ctags 才有 `end:`，Exuberant Ctags 沒有（[Ch 48](./48-elixir-cscope-gtags.md)）。
- `--random-source=<(yes 54)` 把 `shuf` 的亂數來源換成一串固定的位元組，這是 coreutils 官方建議的「可重現亂數」做法。
- 最後一行的註解是實際踩到的雷，本章「踩雷集錦」會說明。

### 實際輸出

```
$ ~/code_reading/scratch/54/coldread.sh 54 5 kernel mm
mm/khugepaged.c:510	25	release_pte_pages
mm/page_alloc.c:7306	32	take_page_off_buddy
mm/kmemleak.c:475	30	mem_pool_alloc
mm/page_alloc.c:7453	38	cond_accept_memory
kernel/auditfilter.c:901	34	audit_find_rule
$ wc -l < /tmp/coldread.cands
2103
```

`kernel/` 和 `mm/` 底下一共有 2103 個符合長度條件的函式。本章讀前兩題，後三題留給「動手練習」。

## 評分表：冷讀交什麼

每次冷讀結束交四樣東西，照這張表自己評分（每項 0–2 分，滿分 8）：

| 項目 | 0 分 | 1 分 | 2 分 |
|---|---|---|---|
| **三句話答案** | 寫不出來，或只是把程式碼翻成中文 | 說得出它做什麼，但說不出為什麼 | 做什麼 + 誰呼叫 + 為什麼要這樣寫 |
| **鴻溝診斷表** | 沒有，或只寫「看不懂」 | 有分類，但沒附 `file:line` | 每道鴻溝至少一個具體卡點，附 `file:line` 與處方 |
| **證據** | 全憑猜測 | 有讀呼叫者或資料結構，但沒交叉確認 | 每個結論都指得出支持它的那一行 |
| **未解問題** | 沒有 | 有問題，但沒寫下一步 | 每個問題都標了「下次從哪裡查」 |

「把程式碼翻成中文」是最常見的 0 分答案。`while (--_pte >= pte)` 翻成「從 `_pte` 往回走到 `pte`」是翻譯，不是理解；理解是「它在撤銷呼叫者已經做完的那幾格，失敗的那一格不算」。

## 實例一：`release_pte_pages()`

### 0–10 分鐘：望診（不用工具）

只看這 25 行，`mm/khugepaged.c:510–534`（v6.18），加上它上面那個 8 行的 helper（`mm/khugepaged.c:501–508`）：

```c
static void release_pte_folio(struct folio *folio)
{
	node_stat_mod_folio(folio,
			NR_ISOLATED_ANON + folio_is_file_lru(folio),
			-folio_nr_pages(folio));
	folio_unlock(folio);
	folio_putback_lru(folio);
}

static void release_pte_pages(pte_t *pte, pte_t *_pte,
		struct list_head *compound_pagelist)
{
	struct folio *folio, *tmp;

	while (--_pte >= pte) {
		pte_t pteval = ptep_get(_pte);
		unsigned long pfn;

		if (pte_none(pteval))
			continue;
		pfn = pte_pfn(pteval);
		if (is_zero_pfn(pfn))
			continue;
		folio = pfn_folio(pfn);
		if (folio_test_large(folio))
			continue;
		release_pte_folio(folio);
	}

	list_for_each_entry_safe(folio, tmp, compound_pagelist, lru) {
		list_del(&folio->lru);
		release_pte_folio(folio);
	}
}
```

望診筆記（這一段的原則是：**只寫看得到的東西和第一個猜測，不查證**）：

```
[望診 release_pte_pages]
- 檔案是 khugepaged：背景把小頁合併成 huge page 的 kernel thread（Ch 5 的入口表看過）
- 名字是 release：猜它是某個「取得」動作的反操作
- 參數 pte 和 _pte：兩個指向 pte_t 的指標，一個開頭、一個……結尾？
  底線前綴通常是「同一個東西的另一個版本」，猜 _pte 是游標
- while (--_pte >= pte)：先減再比，從 _pte 的「前一格」開始往回走到 pte
  → 很像 goto 收尾時「把做過的倒著撤銷」（Ch 8）
- 三個 continue：pte 空的、zero page、large folio → 這三種不處理
- large folio 不處理，但後面有一個 compound_pagelist 迴圈專門處理 → large 的走另一條路
- release_pte_folio 做三件事：統計扣回去（注意負號）、unlock、放回 LRU
  → 反過來推：「取得」那邊應該做了 統計加上去、lock、從 LRU 拿出來
```

### 初步鴻溝診斷

| 鴻溝 | 卡點 | 位置 | 處方 |
|---|---|---|---|
| 慣用法 | `while (--_pte >= pte)` 的起點到底是哪一格？ | `mm/khugepaged.c:515` | 找呼叫者，看 `_pte` 傳進來時指向哪裡 |
| 慣用法 | `list_for_each_entry_safe` 為什麼要 `_safe`？ | `mm/khugepaged.c:530` | [Ch 23](./23-list-head-hlist.md)：迴圈內有 `list_del`，必須先記住下一個 |
| 間接 | 誰呼叫它、`_pte` 和 `compound_pagelist` 由誰準備 | — | `rg -n 'release_pte_pages\('` |
| 領域 | pte、pfn、zero page、folio、LRU isolation、`NR_ISOLATED_ANON` | 全函式 | [Ch 46](./46-acquiring-domain-knowledge.md)；先只補「夠回答問題」的量 |
| 脈絡 | 為什麼 large folio 要另外用一個 list 處理？ | `mm/khugepaged.c:525–526` | `git log -S compound_pagelist` |
| 目標 | 還沒有問題 | — | 下面定義 |

**問題**：`_pte` 傳進來時指向哪裡？這個函式撤銷的是哪幾格、為什麼跳過那三種？

這個問題能用一段程式碼回答（呼叫者），而且回答它就等於回答了「這個函式在做什麼」。

### 10–35 分鐘：檢查

**第一步：找呼叫者（間接鴻溝）。**

```
$ rg -n 'release_pte_pages\(' mm/
mm/khugepaged.c:510:static void release_pte_pages(pte_t *pte, pte_t *_pte,
mm/khugepaged.c:671:	release_pte_pages(pte, _pte, compound_pagelist);
mm/khugepaged.c:762:	release_pte_pages(pte, pte + HPAGE_PMD_NR, compound_pagelist);
```

兩個呼叫點。第 762 行最簡單：`_pte` 是 `pte + HPAGE_PMD_NR`，也就是整張 page table（x86-64 上 512 格）的**尾後一格**。`--_pte` 第一次之後指向第 511 格，一路走到第 0 格。全部撤銷。

第 671 行比較有意思，`_pte` 是呼叫者的區域變數。打開 `__collapse_huge_page_isolate()`（`mm/khugepaged.c:536`）。

**第二步：把「做」和「撤銷」並排讀。**

這是本章最重要的一招：**撤銷函式是「做」函式的鏡像，兩個要並排讀**。`__collapse_huge_page_isolate()` 的骨架（`mm/khugepaged.c:548–671`，略去統計與 trace）：

```c
	for (_pte = pte; _pte < pte + HPAGE_PMD_NR;
	     _pte++, addr += PAGE_SIZE) {
		pte_t pteval = ptep_get(_pte);
		if (pte_none(pteval) || (pte_present(pteval) &&
				is_zero_pfn(pte_pfn(pteval)))) {
			++none_or_zero;
			if (... 沒超過上限 ...)
				continue;                      /* ← 跳過，什麼都沒做 */
			else { result = SCAN_EXCEED_NONE_PTE; goto out; }   /* :561 */
		}
		if (!pte_present(pteval)) { result = SCAN_PTE_NON_PRESENT; goto out; } /* :566 */
		...
		folio = page_folio(page);
		...
		if (folio_test_large(folio)) {
			list_for_each_entry(f, compound_pagelist, lru)
				if (folio == f)
					goto next;                  /* ← 同一個 large folio 已經處理過 */
		}
		if (!folio_trylock(folio)) { result = SCAN_PAGE_LOCK; goto out; }      /* :613 */
		if (folio_expected_ref_count(folio) != folio_ref_count(folio)) {
			folio_unlock(folio);                /* ← 自己這格先收拾 */
			result = SCAN_PAGE_COUNT; goto out;                                 /* :630 */
		}
		if (!folio_isolate_lru(folio)) {
			folio_unlock(folio);                /* ← 自己這格先收拾 */
			result = SCAN_DEL_PAGE_LRU; goto out;                               /* :640 */
		}
		node_stat_mod_folio(folio,
				NR_ISOLATED_ANON + folio_is_file_lru(folio),
				folio_nr_pages(folio));     /* :642 ← 正號 */
		...
		if (folio_test_large(folio))
			list_add_tail(&folio->lru, compound_pagelist);                     /* :649 */
next:
		...
	}

	if (unlikely(cc->is_khugepaged && !referenced)) {
		result = SCAN_LACK_REFERENCED_PAGE;                                     /* :663 */
	} else {
		result = SCAN_SUCCEED;
		...
		return result;
	}
out:
	release_pte_pages(pte, _pte, compound_pagelist);                            /* :671 */
```

並排對照：

| `isolate` 每一格做的事 | `release` 每一格撤銷的事 |
|---|---|
| `pte_none` 或 zero page → `continue`，什麼都沒做 | `pte_none` → `continue`；`is_zero_pfn` → `continue` |
| `folio_trylock` | `folio_unlock` |
| `folio_isolate_lru`（從 LRU 拿出來） | `folio_putback_lru`（放回 LRU） |
| `node_stat_mod_folio(..., +nr)` | `node_stat_mod_folio(..., -nr)` |
| large folio：只處理第一次，之後 `goto next`；處理完放進 `compound_pagelist` | large folio：逐格迴圈裡 `continue`，最後從 `compound_pagelist` 統一撤銷一次 |

三個 `continue` 不再神祕：它們跳過的正是 `isolate` 當初**沒做事**（空的、zero page）或**不在逐格層級做事**（large folio）的那幾格。

**第三步：回答問題——`_pte` 指向哪裡。**

迴圈在第 k 格 `goto out` 時，`_pte` 指向第 k 格，也就是**失敗的那一格**。`release_pte_pages` 的 `--_pte` 先減一，從第 k−1 格開始撤銷，所以失敗的那一格**不會被撤銷**。

這對嗎？看每個 `goto out` 的位置：

- 第 561、566、570、575、588 行：失敗發生在 `folio_trylock` 之前，這一格什麼都還沒做，不需要撤銷。
- 第 613 行：`trylock` 失敗，沒拿到鎖，不需要撤銷。
- 第 630、640 行：已經拿到鎖了，但在 `goto out` 前**自己先 `folio_unlock`**。

結論：**每個 `goto out` 點都把自己那一格收拾乾淨，`release_pte_pages` 只負責「之前已經完整做完」的那幾格。** 這是一個分工約定，`while (--_pte >= pte)` 的先減，就是這個約定的程式碼表達。

另一個出口是第 663 行（`SCAN_LACK_REFERENCED_PAGE`）：迴圈整個跑完才發現不值得合併，這時 `_pte == pte + HPAGE_PMD_NR`，效果和第 762 行一樣，全部撤銷。

**第四步：一個望診時沒發現的疑點。**

`isolate` 判斷 zero page 時有 `pte_present(pteval) &&` 保護，`release` 卻直接 `pte_pfn(pteval)`，沒檢查 present。對一個不在記憶體裡的 swap entry 取 pfn，會得到垃圾。這是 bug 嗎？

不是。`release` 只會走到第 k−1 格以前，而第 0 到 k−1 格都**通過了** `isolate` 的檢查：不是 none/zero 就是 present（不 present 的格子在第 566 行就 `goto out` 了，它自己就是第 k 格）。所以 `release` 看到的每一格要嘛是 none，要嘛是 present，前提成立。

這又是 [Ch 58](./58-maxims-and-checklist.md) 那條習慣的實例：**看到一個不檢查邊界的 helper，答案通常在呼叫者的條件式裡。**

**第五步：脈絡——為什麼 large folio 另外處理。**

```
$ git log --oneline -S"compound_pagelist" v6.18 -- mm/khugepaged.c
98c76c9f1ef7 mm/khugepaged: recover from poisoned anonymous memory
5503fbf2b0b8 khugepaged: allow to collapse PTE-mapped compound pages
$ git describe --contains 5503fbf2b0b8
v5.8-rc1~163^2~65
```

最早的那個（`git log` 由新到舊列，最後一行最早）是 2020 年 Kirill A. Shutemov 的 commit，message 只有一段：

```
We can collapse PTE-mapped compound pages.  We only need to avoid handling
them more than once: lock/unlock page only once if it's present in the PMD
range multiple times as it handled on compound level.  The same goes for
LRU isolation and putback.
```

順手也查一下 `while (--_pte >= pte)` 這行本身的來歷：

```
$ git log --oneline -S"while (--_pte >= pte)" v6.18 -- mm/khugepaged.c mm/huge_memory.c
ba76149f47d8 thp: khugepaged
```

只有一個結果，就是 2011 年把 khugepaged 合進 mainline 的原始 commit。也就是說，「撤銷到失敗那一格之前」這個分工約定從第一天就存在，`compound_pagelist` 是九年後才疊上去的。

一個 large folio（例如 64KB 的 mTHP）在 page table 裡佔好幾格。lock、isolate、putback 都是**整個 folio** 一次的操作，如果逐格做，同一個 folio 會被 lock 兩次（自己 deadlock）或 putback 兩次。所以第一次遇到時處理並記進 list，之後遇到就 `goto next`；撤銷時逐格迴圈跳過它，最後照 list 統一撤銷一次。

> **在 blobless clone 上跑 pickaxe**：`git log -S` 要比對每個 commit 的檔案內容，blobless clone 會在第一次跑時逐一下載歷史 blob，限定路徑之後仍然可能花上幾分鐘（我們第一次跑這條花了五分鐘以上）。冷讀限時 45 分鐘時，**把 pickaxe 丟到背景跑**，先做別的事。[Ch 37](./37-git-archaeology.md) 有更多加速方法。

### 35–45 分鐘：寫報告

```
[冷讀報告] mm/khugepaged.c:510 release_pte_pages（v6.18）

三句話答案：
1. 它是 __collapse_huge_page_isolate() 的撤銷函式：把已經 lock + 從 LRU 拿出來的 page
   放回去、解鎖、把 NR_ISOLATED_* 統計扣回來。
2. _pte 指向「失敗的那一格」或「尾後一格」，--_pte 先減，所以只撤銷完整做完的格子；
   失敗那一格由各個 goto out 點自己收拾（:628、:638 的 folio_unlock）。
3. 空的、zero page 跳過，因為 isolate 沒對它們做事；large folio 跳過，因為它們以 folio
   為單位只處理一次，記在 compound_pagelist，最後統一撤銷（5503fbf2b0b8，v5.8）。

鴻溝：慣用法（--_pte 起點，:515）→ 讀呼叫者解決；間接（兩個呼叫點 :671、:762）；
     領域（pte/folio/LRU isolation）→ 只補到夠用；脈絡（compound_pagelist）→ pickaxe 解決

未解問題：
- 98c76c9f1ef7（poisoned memory recovery）改了 compound_pagelist 的什麼？
  → 下次：git show 98c76c9f1ef7 -- mm/khugepaged.c
- __collapse_huge_page_copy_succeeded（:689）的迴圈用 _pte += nr_ptes 一次跳多格，
  和這裡的逐格不同，為什麼？→ 下次：讀 :680–735，看 nr_ptes 怎麼算
```

自評：三句話 2 分、診斷表 2 分、證據 2 分、未解問題 2 分。這一題順利，主要是因為「撤銷函式要和做函式並排讀」這一招一次解開了三個卡點。

## 實例二：`take_page_off_buddy()`

第二題是同一次抽題的第二個結果。它比第一題短，但卡點完全不同：幾乎沒有慣用法問題，**全部卡在領域**。

### 望診

`mm/page_alloc.c:7306–7337`（v6.18）：

```c
/*
 * Take a page that will be marked as poisoned off the buddy allocator.
 */
bool take_page_off_buddy(struct page *page)
{
	struct zone *zone = page_zone(page);
	unsigned long pfn = page_to_pfn(page);
	unsigned long flags;
	unsigned int order;
	bool ret = false;

	spin_lock_irqsave(&zone->lock, flags);
	for (order = 0; order < NR_PAGE_ORDERS; order++) {
		struct page *page_head = page - (pfn & ((1 << order) - 1));
		int page_order = buddy_order(page_head);

		if (PageBuddy(page_head) && page_order >= order) {
			unsigned long pfn_head = page_to_pfn(page_head);
			int migratetype = get_pfnblock_migratetype(page_head,
								   pfn_head);

			del_page_from_free_list(page_head, zone, page_order,
						migratetype);
			break_down_buddy_pages(zone, page_head, page, 0,
						page_order, migratetype);
			SetPageHWPoisonTakenOff(page);
			ret = true;
			break;
		}
		if (page_count(page_head) > 0)
			break;
	}
	spin_unlock_irqrestore(&zone->lock, flags);
	return ret;
}
```

```
[望診 take_page_off_buddy]
- 註解說：把一個「要被標成 poisoned」的 page 從 buddy allocator 拿走
  → 硬體回報某個實體頁壞了（memory failure），要確保它永遠不會被配置出去
- spin_lock_irqsave(&zone->lock)：buddy 的 free list 受 zone->lock 保護（Ch 30）
- for order = 0..：一階一階往上試
- pfn & ((1 << order) - 1)：pfn 對 2^order 取餘數 → page 減掉餘數 = 往下對齊到 2^order
  → page_head 是「如果 page 在一個 order 階的區塊裡，那個區塊的開頭」
- PageBuddy && page_order >= order：這個開頭是 free 區塊、而且夠大 → 找到了
- 找到之後：從 free list 拿掉、break_down、標記 → 拆開大區塊，只把壞的那頁留在外面
- page_count(page_head) > 0 → break：看不懂為什麼「被使用中」就可以停
```

診斷結果幾乎一面倒：

| 鴻溝 | 卡點 | 位置 |
|---|---|---|
| 慣用法 | 對齊的位元運算 | `mm/page_alloc.c:7316` |
| **領域** | buddy allocator 的不變式：free 區塊的開頭對齊、`PageBuddy`、order 存在哪 | `:7317–7319` |
| **領域** | 為什麼 `page_count > 0` 就能停 | `:7332` |
| **領域** | `break_down_buddy_pages` 怎麼拆 | `:7326` → `:7277` |
| 間接 | 誰呼叫、什麼時候 | — |

### 檢查：只補回答問題需要的領域知識

**問題**：迴圈在找什麼、為什麼遇到 `page_count > 0` 可以停？

需要的 buddy allocator 知識只有三條（[Ch 19](./19-kernel-memory-allocation.md) 講過配置端，這裡補 free list 端的不變式）：

1. free 的區塊大小一定是 2^order 頁，而且**開頭的 pfn 一定對齊 2^order**。
2. free 區塊只有**開頭那一頁**有 `PageBuddy` 標記，order 存在開頭那頁的 `page_private`。
3. free 區塊裡每一頁的 refcount 都是 0。

有了這三條，迴圈就讀得懂了：

```
order 0：page_head = page 自己               → page 本身是 free 的 order-0 區塊開頭嗎？
order 1：page_head = page 往下對齊到 2 頁    → page 在一個 free 的 2 頁區塊裡嗎？
order 2：page_head = page 往下對齊到 4 頁    → page 在一個 free 的 4 頁區塊裡嗎？
...
```

`page_order >= order` 確保找到的 free 區塊夠大、真的蓋住 `page`：區塊從 `page_head` 開始，長 2^page_order 頁，而 `page - page_head < 2^order ≤ 2^page_order`。

再看 `page_count(page_head) > 0` 為什麼能停：如果 `page` 落在某個更高階的 free 區塊裡，這一階的 `page_head` 也必然落在那個區塊裡（兩者都在同一個對齊範圍內）；根據第 3 條，它的 refcount 應該是 0。現在 refcount 大於 0，代表 `page_head` 正在使用中，**不可能**有更大的 free 區塊同時蓋住它和 `page`，再往上找也沒用。這是一個靠不變式推出來的提前結束。

**疑點**：`buddy_order()` 的定義（`mm/internal.h:616–620`）明寫：

```c
static inline unsigned int buddy_order(struct page *page)
{
	/* PageBuddy() must be checked by the caller */
	return page_private(page);
}
```

但 `take_page_off_buddy` 在第 7317 行**先**呼叫 `buddy_order()`，第 7319 行才檢查 `PageBuddy()`。違反約定了嗎？

技術上是，但無害：`page_order` 只在 `PageBuddy(page_head)` 成立時才被使用（`&&` 短路），不成立時讀到的 `page_private` 值被丟掉；而且整段在 `zone->lock` 裡，讀 `page_private` 本身不會出事。冷讀時這種「看起來違反約定」的地方要記下來，但結論是**先確認讀到的值有沒有被使用**，再判斷是不是 bug。

**呼叫者**：

```
$ rg -n 'take_page_off_buddy\(' mm/
mm/memory-failure.c:182:		ret = take_page_off_buddy(page);
mm/memory-failure.c:2240:				if (take_page_off_buddy(p)) {
mm/page_alloc.c:7306:bool take_page_off_buddy(struct page *page)
```

第 182 行在 `__page_handle_poison()`（`mm/memory-failure.c:163`）：先 `dissolve_free_hugetlb_folio()`，再 `drain_all_pages()` 把 per-cpu list 上的頁趕回 buddy，最後才 `take_page_off_buddy()`。前面那段長註解解釋了為什麼不用 `zone_pcp_disable()`（會造成 lock 順序問題），這是**脈絡鴻溝**的答案直接寫在程式碼裡的好例子。

### 報告

```
[冷讀報告] mm/page_alloc.c:7306 take_page_off_buddy（v6.18）

三句話答案：
1. memory failure 處理壞掉的實體頁時呼叫它（mm/memory-failure.c:182、:2240），
   目的是把該頁從 buddy free list 永久拿走，標 HWPoisonTakenOff。
2. 迴圈從 order 0 往上，把 page 往下對齊到 2^order 當候選開頭；候選是 PageBuddy
   且 order 夠大，就從 free list 拿掉整塊，用 break_down_buddy_pages 拆開、只留壞頁在外面。
3. 候選開頭的 refcount > 0 代表它在使用中，依 buddy 不變式不可能還有更大的 free 區塊
   蓋住 page，提前結束。

鴻溝：領域為主（buddy 三條不變式）；慣用法一個（對齊位元運算 :7316）；
     buddy_order 先於 PageBuddy 的疑點 → 值未被使用，無害

未解問題：
- break_down_buddy_pages（:7277）拆的時候 set_page_guard 是什麼？
  → 下次：Documentation 搜 debug_guardpage_minorder
- 第 7343 行的 put_page_back_buddy 是反操作，什麼時候會呼叫？
  → 下次：rg -n put_page_back_buddy mm/
```

自評：三句話 2、診斷表 2、證據 2、未解問題 2。

### 兩題對照

| | `release_pte_pages` | `take_page_off_buddy` |
|---|---|---|
| 主要鴻溝 | 慣用法 + 間接 | 領域 |
| 解開的關鍵 | 讀呼叫者，和「做」函式並排比對 | 補三條 buddy 不變式 |
| 疑點 | 沒檢查 `pte_present` | `buddy_order` 先於 `PageBuddy` |
| 疑點的答案 | 前提由呼叫者保證 | 讀到的值沒被使用 |

兩個疑點的答案都不在函式本身，這不是巧合：**25 行的函式通常只是一個大流程的一小段，它的正確性靠呼叫者或不變式撐著**。冷讀短函式時，「它依賴哪些前提」比「它做了什麼」更值得問。

## 長期訓練計畫

| 頻率 | 做法 |
|---|---|
| 每週 2–3 次 | 用今天的日期當種子（`coldread.sh 20261121 1 <目錄>`），抽一題，限時 45 分鐘 |
| 每次 | 照評分表自評，把報告存成 `~/code_reading/coldread/<日期>-<函式名>.md` |
| 每月 | 把這個月的報告翻一遍，統計五道鴻溝各出現幾次。出現最多的那道，下個月回頭重讀對應的 Part |
| 每季 | 重抽三個月前的題目（同種子同目錄），不看舊報告重讀一次，比較分數 |

目錄的難度階梯，可以當作循序漸進的順序：

| 階段 | 目錄 | 特色 |
|---|---|---|
| 1 | `lib/`、`kernel/` 的工具類檔案 | 自成一體，領域知識需求低 |
| 2 | `fs/`（不含檔案系統子目錄）、`kernel/sched/` | 有 VFS、排程的領域知識，Part 7 學過 |
| 3 | `mm/`、`net/core/` | 領域密度最高，不變式多 |
| 4 | `drivers/` 任一子目錄、`arch/x86/kernel/` | 需要硬體知識，Ch 55 示範 |

## 對比與取捨

| 做法 | 優點 | 缺點 | 適合 |
|---|---|---|---|
| 隨機抽函式（本章） | 涵蓋盲區、可重現 | 可能抽到太孤立的函式，練不到大結構 | 日常練基本功 |
| 從 stack trace 或 bug report 開始 | 有真實的問題，目標明確 | 題目來源不穩定，難以重複 | 實戰、Ch 57 讀 patch |
| 讀 LWN 文章後讀對應程式碼 | 領域知識先到位 | 等於有提示，不是冷讀 | 學新子系統 |
| 讓 LLM 出題、先解釋 | 快 | 解釋可能錯（[Ch 53](./53-llm-assisted-reading.md)），而且剝奪了自己診斷的機會 | 冷讀**結束後**對答案 |

LLM 在冷讀裡的正確位置是**最後**：報告寫完之後，再把函式丟給 LLM，比較它的解釋和你的報告。兩者不同的地方，回原始碼找證據決定誰對。

## 踩雷集錦

- **`shuf` 讀檔案和讀 stdin，同一個種子抽出不同結果。** 實測：`shuf -n 2 --random-source=<(yes 54) /tmp/c2.txt` 抽到 `release_pte_pages`、`take_page_off_buddy`；同一個檔案用 `cat /tmp/c2.txt | shuf -n 2 --random-source=<(yes 54)` 卻抽到 `mm/mempolicy.c` 的兩個函式。原因是 `shuf` 對可以隨機存取的輸入和串流輸入用了不同的抽樣演算法，消耗亂數的方式不同。要可重現，**固定讀檔案**，這就是腳本最後兩行的由來。
- **把「翻譯」當「理解」。** 報告裡如果每一句都對應一行程式碼，就是翻譯。三句話答案的每一句應該橫跨好幾行，而且至少一句提到程式碼以外的東西（呼叫者、不變式、歷史）。
- **望診階段忍不住開工具。** 一開 cscope 就會開始深度優先地追下去，十分鐘後還在 `pfn_folio` 的定義裡。望診的價值在於強迫你先建立全貌的假說。
- **抽到看不懂的就重抽。** 規則是只有「純巨集包裝」才能重抽。看不懂正是要練的東西。
- **疑點直接判定為 bug。** 本章兩個疑點都不是 bug。判定 bug 之前至少要走完：值有沒有被使用、前提是誰保證的、`git log -L` 看這一行有沒有被修過。

## 進階：再往深一層

- **把冷讀變成 review**：抽到函式後，額外問「如果我要改這個函式，最容易改壞的是哪裡？」。`release_pte_pages` 的答案是：如果有人在 `isolate` 新增一個「拿了鎖才失敗」的 `goto out`，卻忘了先 unlock，就會留下一個鎖住的 folio。這種「隱含約定」正是 code review 要守的東西（[Ch 57](./57-reading-patches-and-review.md)）。
- **限時遞減**：連續三題拿滿分後，把時限從 45 分鐘降到 30 分鐘，望診維持 10 分鐘，壓縮檢查階段。
- **換語料**：同一個腳本改 `cd` 到 Redis 或 Lua 的原始碼樹，練 user-space 的冷讀（[練習 A](./practice-a-cold-read-userspace.md) 的延伸）。

## 動手練習

1. 讀同一次抽題的第三題 `mm/kmemleak.c:475 mem_pool_alloc`。問題：slab 配置失敗時，它從哪兩個地方拿 object？為什麼這個 fallback 需要存在（想想 kmemleak 自己是在追蹤誰的配置）？
2. 讀第五題 `kernel/auditfilter.c:901 audit_find_rule`。問題：三個分支裡，有一個分支**沒有設定** `*p`。是哪一個？呼叫者 `audit_add_rule()` 怎麼確保之後用到的 `list` 是有效的？
3. 用你自己的種子（例如今天的日期）抽一題 `lib/` 的函式，完整走一次 45 分鐘流程，照評分表自評。

<details>
<summary>練習提示與參考答案</summary>

1. 先試 slab（`object_cache`，`mm/kmemleak.c:482–487`），失敗後在 `kmemleak_lock` 保護下依序試 `mem_pool_free_list`（之前釋放回池子的 object）和靜態陣列 `mem_pool[]`（從尾端往前發，`mem_pool[--mem_pool_free_count]`，第 495–496 行）。fallback 存在的理由：kmemleak 要替**每一次**記憶體配置建立一個追蹤 object，包括 slab 自己還沒初始化完的早期開機階段（`object_cache` 在第 2302 行才建立），以及 slab 配置失敗的時候。靜態池讓它不依賴自己正在追蹤的那個配置器。兩個都沒有時只 `pr_warn_once`，提示調大 `CONFIG_DEBUG_KMEMLEAK_MEM_POOL_SIZE`。
2. `else if (entry->rule.watch)` 那個分支（第 911–921 行）：它走遍整個 `audit_inode_hash` 找重複規則，但不設定 `*p`，因為 watch 規則還不知道 inode 號碼，沒辦法決定要放進哪個 bucket。`audit_add_rule()`（第 940 行起）在 `audit_find_rule()` 回傳 NULL 之後，對 watch 規則呼叫 `audit_add_watch(&entry->rule, &list)`（第 972 行），由它設定 `list`；之後第 1004／1009 行的 `list_add_rcu`／`list_add_tail_rcu` 才用到 `list`。這又是「前提由呼叫者保證」的例子，而且這個約定只寫在程式碼的結構裡，沒有寫在註解裡。
3. 沒有標準答案。檢查你的報告：三句話裡有沒有至少一句提到函式以外的東西？未解問題有沒有標下一步？

</details>

## 本章重點整理

- 冷讀是可以每天練的技能：固定種子抽題、限時 45 分鐘、望診 10 → 檢查 25 → 報告 10，照評分表自評。
- 望診不用工具，練的是認 pattern 的速度；檢查階段帶一個能用程式碼回答的問題進去。
- 撤銷函式要和「做」函式並排讀：每一個 `continue`、每一個起點偏移，都對應到「做」那邊的一個決定。
- 短函式的正確性通常靠呼叫者或不變式撐著；看到疑點先問「前提是誰保證的」「讀到的值有沒有被使用」，再判斷是不是 bug。

## 自我檢核

- [ ] 不看筆記，說出冷讀三階段各做什麼、為什麼望診階段不准開工具
- [ ] 能解釋 `while (--_pte >= pte)` 為什麼不會撤銷失敗的那一格，以及這個設計要求每個 `goto out` 點遵守什麼約定
- [ ] 能用 buddy allocator 的三條不變式，解釋 `take_page_off_buddy` 為什麼遇到 `page_count > 0` 就能停
- [ ] 能說出 `shuf --random-source` 在什麼情況下不可重現
- [ ] 用自己的種子抽一題並拿到 6 分以上

## 延伸閱讀

### 官方文件

- **`Documentation/admin-guide/mm/transhuge.rst`**（[線上版](https://docs.kernel.org/admin-guide/mm/transhuge.html)）
  - **讀哪裡**：「khugepaged」一節，以及 `max_ptes_none`、`max_ptes_shared` 兩個參數
  - **學到什麼**：實例一裡 `khugepaged_max_ptes_none`、`khugepaged_max_ptes_shared` 兩個上限的使用者介面；補 `__collapse_huge_page_isolate` 的領域知識
- **`Documentation/mm/hwpoison.rst`**（[線上版](https://docs.kernel.org/mm/hwpoison.html)）
  - **學到什麼**：memory failure 處理的整體流程，實例二的領域背景
- **GNU coreutils 手冊的「Random sources」一節**（`info coreutils 'Random sources'`）
  - **學到什麼**：`--random-source` 的正式語意，以及官方建議用 `openssl enc` 產生可重現亂數流的寫法

### 論文

- **Program Comprehension During Software Maintenance and Evolution** — Anneliese von Mayrhauser、A. Marie Vans，IEEE Computer 28(8)（1995）
  - **核心貢獻**：整合 top-down、bottom-up 和 situation model 的「整合式程式理解模型」，並用實驗觀察專業工程師在三種模式間切換
  - **讀哪裡**：描述模式切換的段落
  - **和本章的關聯**：望診是 top-down 建假說，檢查是 bottom-up 驗證；本章的流程就是刻意安排這兩種模式的切換點

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 11 章（A Complete Example）
  - **定位**：原書的「把所有技巧串起來」示範；它的案例是作者事先挑好的，本章把挑選換成隨機，對照著讀可以看出兩者練的東西不同
- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman（Prentice Hall, 2004）
  - **讀哪幾章**：第 6 章（Physical Page Allocation）
  - **定位**：buddy allocator 的經典說明，程式碼是 2.6 時代的，但不變式到今天都沒變；實例二需要的三條不變式都在這一章

隨機函式練的是「一小段」；下一章換成一個有完整生命週期的 driver，看 probe 怎麼把 clock、reset、IRQ、DMA 一樣一樣拿到手，以及 remove 時怎麼一樣一樣還回去。

→ [Ch 55 冷讀實戰二：一個 driver 的 probe 函式](./55-cold-read-driver-probe.md)
