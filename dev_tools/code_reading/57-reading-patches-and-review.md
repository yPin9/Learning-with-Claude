# Ch 57 — 讀 patch 與 code review：讀 diff 的心法

> **目標**：拿到一個 kernel patch（`git show` 的輸出或 mailing list 上的一封信），能按固定順序讀完它，判斷「它宣稱修了什麼、真的修到了沒、有沒有漏掉的路徑」，並寫出有內容的 review 意見。
> **鴻溝**：本章主要練**脈絡鴻溝**與**目標鴻溝**，順帶練**間接鴻溝**（從 diff 追回呼叫點）。

> **環境**：WSL Ubuntu 22.04、git 2.34.1、Linux kernel tree 在 tag v6.18（`~/code_reading/linux`）。同一個 clone 裡也有 `origin/master`（比 v6.18 新），本章有一處會用 `git show <commit>` 看 v6.18 之後的修正，但**不 checkout**。本章所有指令都實際跑過。

## 為什麼需要這個？

前面三章冷讀的對象是「一段已經存在的程式碼」。但在真實工作裡，你讀到的程式碼有一大半是以 **diff** 的形式出現的：

- 追 bug 時 `git bisect` 停在某個 commit，你要讀懂那個 commit 改了什麼
- 審同事的 PR、審 mailing list 上的 patch
- 研究 CVE，要讀修補 patch 反推漏洞成因（[`kernel_pwn`](../../security/kernel_pwn/README.md) 的日常）
- 升級 kernel 版本，要知道某個子系統這半年動了什麼

diff 是一種**刻意不完整**的文件。它只給你改動的那幾行和上下各三行，其他全部省略。Spinellis 在原書第 10 章把 diff 當成工具介紹，重點放在「用 diff 看出兩個版本差在哪」；但到了以 git 和 mailing list 為中心的今天，讀 diff 本身變成一門獨立的手藝：**你要從一個殘缺的視窗，重建出改動前後的完整語意**。

沒練過的人讀 patch，通常是從第一個 `+` 讀到最後一個 `-`，然後說「看起來沒問題」。這種 review 抓不到任何真 bug，因為真 bug 幾乎都藏在 **diff 沒有顯示的地方**：沒改到的呼叫點、沒動的錯誤路徑、同一個 pattern 在別的檔案的另一份拷貝。

## 先建立直覺

把 diff 想成道路施工的告示牌：

```
       你看到的 diff                      實際的程式
  ┌──────────────────────┐        ┌──────────────────────────────┐
  │ @@ -96,9 +96,36 @@   │        │ 呼叫者 A  (mm/gup.c)          │
  │   3 行上下文         │        │   └─ 處理 -EEXIST 的 retry    │
  │ + 新增 27 行         │  ───►  │ memfd_alloc_folio()           │
  │   3 行上下文         │        │   ├─ reserve  ← diff 沒顯示   │
  └──────────────────────┘        │   ├─ alloc                    │
                                  │   ├─ [diff 只在這裡]          │
   告示牌只告訴你「這裡在施工」   │   └─ error path ← diff 沒顯示 │
                                  │ 參考實作 hugetlb_no_page()    │
                                  │   └─ 同一個 pattern 的正本    │
                                  └──────────────────────────────┘
```

告示牌說「這段路在修」，但車子會不會在下一個路口卡住，告示牌不會告訴你。讀 patch 的心法就一句：**diff 告訴你看哪裡，不告訴你看到哪裡為止**。

這個比喻的限制：施工告示不會說謊，但 commit message 會「選擇性陳述」——作者描述的是他以為自己修了什麼，不一定等於他實際修了什麼。所以讀 commit message 是建立假說，不是接受結論（[Ch 3 — 帶著問題讀](./03-question-driven-reading.md) 的假說驗證迴圈在這裡原樣適用）。

## unified diff 格式：每一行都有資訊

先把格式讀熟。下面是本章主角 `de8798965fd0` 的 diff 開頭（真實輸出）：

```
diff --git a/mm/memfd.c b/mm/memfd.c
index 1d109c1acf21..a405eaa451ee 100644
--- a/mm/memfd.c
+++ b/mm/memfd.c
@@ -96,9 +96,36 @@ struct folio *memfd_alloc_folio(struct file *memfd, pgoff_t idx)
 						    NULL,
 						    gfp_mask);
 		if (folio) {
+			u32 hash;
```

| 行 | 意思 | 讀者要拿它做什麼 |
|---|---|---|
| `diff --git a/... b/...` | 改動前後的路徑 | 路徑不同代表 rename；先確認檔案沒換名字 |
| `index 1d109c1acf21..a405eaa451ee 100644` | 改動前後的 **blob** hash 與檔案模式 | `git show 1d109c1acf21` 可以直接拿到改動前的**完整檔案** |
| `--- a/` / `+++ b/` | 舊檔、新檔 | `/dev/null` 代表新增或刪除整個檔案 |
| `@@ -96,9 +96,36 @@` | hunk header：舊檔從第 96 行起 9 行，新檔從第 96 行起 36 行 | 36−9=27，這個 hunk 淨增 27 行；和 `--stat` 對得上 |
| `@@` 後面那段文字 | **hunk 所在的函式**（git 猜的） | 快速定位，但**不保證正確**，見下面 |

### hunk header 的函式名是猜的

`@@` 後面那段 `struct folio *memfd_alloc_folio(...)` 不是 diff 演算法的一部分，是 git 往 hunk 起點**往上找**、找到第一行符合「函式開頭」regex 的行，把它貼上來。用什麼 regex 由 `.gitattributes` 決定。kernel tree 的設定：

```
$ cat .gitattributes
# SPDX-License-Identifier: GPL-2.0-only
*.[ch] diff=cpp
*.dts diff=dts
*.dts[io] diff=dts
*.rs diff=rust
```

`diff=cpp` 讓 git 用內建的 C/C++ 規則。這個猜法有兩個陷阱，兩個都實測過：

**陷阱一：hunk 起點如果在函式之前，header 會顯示「前一個」函式。** 用 `-W`（`--function-context`，把整個函式當上下文）重看同一個 commit：

```
$ git show -W --format= de8798965fd0 | head -6
diff --git a/mm/memfd.c b/mm/memfd.c
index 1d109c1acf211..a405eaa451ee8 100644
--- a/mm/memfd.c
+++ b/mm/memfd.c
@@ -62,67 +62,94 @@ static void memfd_tag_pins(struct xa_state *xas)
 /*
```

改的明明是 `memfd_alloc_folio()`，header 卻寫 `memfd_tag_pins()`。原因是 `-W` 把 hunk 往上擴到函式上方的註解（第 62 行），而從第 62 行往上找到的第一個函式開頭是 `memfd_tag_pins`。

**陷阱二：規則換了，結果也會換。** 把兩個版本的檔案抽出來，放在沒有 `.gitattributes` 的目錄用 `git diff --no-index` 比，git 改用預設規則（行首是字母、`_` 或 `$` 的行）。這次剛好結果一樣：

```
$ git show de8798965fd0^:mm/memfd.c > old.c; git show de8798965fd0:mm/memfd.c > new.c
$ git diff --no-index old.c new.c | grep '^@@'
@@ -96,9 +96,36 @@ struct folio *memfd_alloc_folio(struct file *memfd, pgoff_t idx)
```

但預設規則遇到行首的 label（kernel 的 `out:` 常常寫在第 0 欄）或巨集展開的函式定義（`SYSCALL_DEFINE3(...)`）時就可能貼錯。**結論：header 的函式名是導航提示，不是事實；要確認改的是哪個函式，看行號，或者用 `-W`。**

### 四個讓 diff 變完整的選項

| 選項 | 效果 | 什麼時候用 |
|---|---|---|
| `-U10`（`--unified=10`） | 上下文從 3 行變 10 行 | 改動靠近某個 `if` 或迴圈開頭，3 行看不到條件 |
| `-W` / `--function-context` | 上下文擴到整個函式 | **讀 bug fix 的預設選項**；錯誤路徑、鎖的配對都在同一個函式裡 |
| `--color-moved[=zebra]` | 被搬動的區塊用不同顏色，和真正的新增/刪除區分開 | 重構 patch、「把一段 code 往上移」的修正 |
| `--color-moved-ws=allow-indentation-change` | 搬動時縮排改變也算搬動 | 程式碼從內層 block 搬到外層（kernel 很常見） |

`-U10` 的實際效果，可以看到 3 行上下文時看不到的 `hugetlb_reserve_pages()` 呼叫：

```
$ git show -U10 --format= de8798965fd0 | sed -n 5,15p
@@ -89,23 +89,50 @@ struct folio *memfd_alloc_folio(struct file *memfd, pgoff_t idx)
 
 		nr_resv = hugetlb_reserve_pages(inode, idx, idx + 1, NULL, 0);
 		if (nr_resv < 0)
 			return ERR_PTR(nr_resv);
 
 		folio = alloc_hugetlb_folio_reserve(h,
 						    numa_node_id(),
 						    NULL,
 						    gfp_mask);
 		if (folio) {
+			u32 hash;
```

`--color-moved` 的威力要等到本章後面的「後續修正」才看得出來，先記住它的存在。

## 讀 patch 的順序

不要從 diff 的第一行開始讀。固定這個順序：

```
1. commit message  ──► 建立假說：它宣稱 bug 是什麼、修法是什麼
        │
2. 測試 / reproducer ─► 規格：什麼輸入會觸發 bug？patch 有沒有附測試？
        │
3. 資料結構變更 ────► struct 欄位、鎖、flag 有沒有新增或改語意？
        │
4. 呼叫點 ──────────► 被改的函式被誰呼叫？呼叫者怎麼處理它的回傳值？
        │
5. diff 本體 ───────► 用 -W 讀，逐行對照第 1 步的假說
        │
6. 回到完整檔案 ────► 錯誤路徑、鎖的配對、同 pattern 的其他實作
        │
7. 歷史 ────────────► Fixes: 指向誰？同一個 commit 被修過幾次？
```

為什麼不是先讀 diff？因為**沒有假說的閱讀會被 diff 牽著走**。diff 長什麼樣，你就想什麼；它沒顯示的地方，你就不會想到。先讀 message 和呼叫點，你腦中就有一張「應該被改到的地方」的清單，讀 diff 時是在對答案，漏掉的會跳出來。這是 [Ch 3](./03-question-driven-reading.md) 的提問驅動和 [Ch 5 — 從哪裡開始讀](./05-entry-points-and-data-first.md) 的「資料結構優先」在 patch 上的應用。

## kernel patch 的 tags：誰說了什麼、為什麼

kernel commit message 結尾那串 `Xxx-by:` 叫 **trailer**。它們不是裝飾，每一個都在回答一個讀 patch 時會問的問題。規範在 `Documentation/process/submitting-patches.rst`（v6.18）：

| Tag | 意思 | 讀 patch 時拿它做什麼 |
|---|---|---|
| `Signed-off-by:` | 開發者原產地證明（DCO，見該檔第 396 行「Sign your work」）；也記錄 patch 經過哪些 maintainer 的手 | 最後一個 SoB 通常是把它合進樹的 maintainer（mm 是 Andrew Morton） |
| `Reviewed-by:` | 做過技術審查，並接受第 562 行起的「Reviewer's statement of oversight」 | 有人**認真看過**的訊號；沒有任何 Reviewed-by 的修正要多看一眼 |
| `Acked-by:` | 「我同意」，通常來自該區域的 maintainer；第 483 行明說它比 Reviewed-by 不正式 | 表示方向被接受，不代表逐行審過 |
| `Tested-by:` / `Reported-by:` / `Suggested-by:` | 誰測過、誰回報、誰出的主意 | `Reported-by: syzbot` 代表有 reproducer，去找它 |
| `Fixes: <12 位 hash> ("subject")` | 這個 bug 是哪個 commit 引入的（第 146 行要求至少 12 位） | 回答「bug 存在於哪些版本」，也是 stable 團隊決定 backport 範圍的依據（第 605 行） |
| `Cc: stable@vger.kernel.org` | 請 stable 團隊 backport | 第 612 行強調：**有 Fixes 不等於會進 stable**，要另外 Cc |
| `Link:` | 指向 mailing list 討論或背景資料（第 116 行） | 「為什麼這樣修」幾乎都在這裡；[Ch 38 — mailing list 考古](./38-mailing-list-archaeology.md) |
| `Closes:` | 指向公開的 bug 報告；第 545 行要求 Reported-by 後面接 Closes | syzbot 的頁面有 reproducer 與 crash log |

用 `git interpret-trailers` 把 trailer 抽出來統計，比用眼睛數準：

```
$ git log -1 --format=%B de8798965fd0 | git interpret-trailers --parse \
    | awk -F: '{print $1}' | sort | uniq -c | sort -rn
      7 Cc
      3 Acked-by
      2 Suggested-by
      2 Signed-off-by
      2 Link
      1 Tested-by
      1 Reported-by
      1 Fixes
      1 Closes

$ git log -1 --format='%(trailers:key=Fixes,valueonly)' de8798965fd0
89c1905d9c14 ("mm/gup: introduce memfd_pin_folios() for pinning memfd folios")
```

注意：這個 patch 有三個 Acked-by，**零個 Reviewed-by**。這不代表它有問題，但它告訴你「沒有人簽下逐行審查的聲明」。

kernel 也附了兩個機械化的 review 工具，可以先跑掉形式問題：

```
$ git format-patch -1 de8798965fd0 --stdout > p.patch
$ ./scripts/checkpatch.pl --no-tree p.patch
WARNING: Reported-by: should be immediately followed by Closes: with a URL to the report
#30: 
Reported-by: syzbot+f64019ba229e3a5c411b@syzkaller.appspotmail.com
Link: https://lore.kernel.org/all/20251112031631.2315651-1-kartikey406@gmail.com/ [v1]

total: 0 errors, 1 warnings, 36 lines checked
```

`checkpatch.pl` 只抓格式（這裡是 trailer 順序），**完全不懂語意**。`./scripts/get_maintainer.pl --no-git p.patch` 會列出該 Cc 的人與 list（輸出結尾是 `linux-mm@kvack.org`、`linux-kernel@vger.kernel.org`），用來判斷「該看這個 patch 的人有沒有看到」。

## 實例：完整讀一個 mm/ 的 bug fix

挑的是 v6.18 釋出前合進來的 `de8798965fd0 mm/memfd: fix information leak in hugetlb folios`。選它的理由：中等大小（+27 行）、有 `Fixes:`、有 `Cc: stable`、有 syzbot reproducer，而且——先不劇透——它值得問「有沒有漏」。

```
$ git describe --contains de8798965fd0
v6.18~23^2~6
$ git describe --contains 89c1905d9c14
v6.11-rc1~85^2~71
```

bug 從 v6.11 存在到 v6.18 前夕。

### Step 1：commit message → 假說

完整 message（`git show de8798965fd0` 真實輸出，trailer 從略）：

```
    When allocating hugetlb folios for memfd, three initialization steps are
    missing:

    1. Folios are not zeroed, leading to kernel memory disclosure to userspace
    2. Folios are not marked uptodate before adding to page cache
    3. hugetlb_fault_mutex is not taken before hugetlb_add_to_page_cache()

    The memfd allocation path bypasses the normal page fault handler
    (hugetlb_no_page) which would handle all of these initialization steps.
    ...
    Fix by matching the initialization pattern used in hugetlb_no_page():
```

把它改寫成可以驗證的假說：

- H1：`memfd_alloc_folio()` 配到的 hugetlb folio 沒有清零 → 洩漏 kernel 記憶體
- H2：沒有 `__folio_mark_uptodate()`
- H3：沒拿 `hugetlb_fault_mutex`，可能和並行的 page fault 搶同一個 index
- H4（作者的隱含主張）：**照 `hugetlb_no_page()` 的 pattern 做，就正確了**

H4 是最值得懷疑的一條。「照著某個 pattern 做」的 patch，常見的失敗方式是**抄了動作、沒抄範圍**。

### Step 2：測試與 reproducer

diff 沒有改任何 `tools/testing/` 下的檔案，沒有 selftest。reproducer 在 `Closes:` 指向的 syzbot 頁面。這表示 patch 的正確性靠「syzbot 不再 crash」（`Tested-by: syzbot`）驗證——syzbot 能證明**那一條**路徑不 crash 了，不能證明鎖的範圍對。記下來：**H3 沒有被測試覆蓋**。

### Step 3：資料結構

patch 沒有改 struct，但引入了一個全域鎖陣列 `hugetlb_fault_mutex_table[]`，用 `(mapping, idx)` 雜湊選鎖。要問的是：這把鎖**保護什麼不變量**？去看別的使用者怎麼說。`hugetlb_fault()` 在 `mm/hugetlb.c:6692–6699`（v6.18）：

```c
	/*
	 * Serialize hugepage allocation and instantiation, so that we don't
	 * get spurious allocation failures if two CPUs race to instantiate
	 * the same page in the page cache.
	 */
	mapping = vma->vm_file->f_mapping;
	hash = hugetlb_fault_mutex_hash(mapping, vmf.pgoff);
	mutex_lock(&hugetlb_fault_mutex_table[hash]);
```

關鍵字是「allocation **and** instantiation」——鎖保護的是「配置 + 放進 page cache」這一整段，不是只有放進去那一下。

### Step 4：呼叫點

```
$ rg -n 'memfd_alloc_folio' --type c .
./mm/memfd.c:68:struct folio *memfd_alloc_folio(struct file *memfd, pgoff_t idx)
./mm/gup.c:3523:				folio = memfd_alloc_folio(memfd, start_idx);
./include/linux/memfd.h:9:struct folio *memfd_alloc_folio(struct file *memfd, pgoff_t idx);
./include/linux/memfd.h:23:static inline struct folio *memfd_alloc_folio(struct file *memfd, pgoff_t idx)
```

唯一的呼叫者在 `mm/gup.c:3521–3530`（v6.18）：

```c
			if (!nr_found) {
				folio = memfd_alloc_folio(memfd, start_idx);
				if (IS_ERR(folio)) {
					ret = PTR_ERR(folio);
					if (ret != -EEXIST)
						goto err;
					folio = NULL;
				}
			}
```

呼叫者**明確預期** `-EEXIST`：查 page cache 沒找到、去配置的這段空檔裡，別人（並行的 page fault）可能已經把 folio 放進去了，那就重查一次。記下來：**`-EEXIST` 是正常路徑，不是罕見錯誤**。

### Step 5：用 -W 讀 diff 本體

v6.18 修完之後的 `memfd_alloc_folio()`（`mm/memfd.c:68–145`）中段：

```c
		nr_resv = hugetlb_reserve_pages(inode, idx, idx + 1, NULL, 0);
		if (nr_resv < 0)
			return ERR_PTR(nr_resv);

		folio = alloc_hugetlb_folio_reserve(h,
						    numa_node_id(),
						    NULL,
						    gfp_mask);
		if (folio) {
			u32 hash;

			folio_zero_user(folio, 0);                       /* H1 */
			__folio_mark_uptodate(folio);                    /* H2 */

			hash = hugetlb_fault_mutex_hash(memfd->f_mapping, idx);
			mutex_lock(&hugetlb_fault_mutex_table[hash]);    /* H3 */

			err = hugetlb_add_to_page_cache(folio,
							memfd->f_mapping,
							idx);

			mutex_unlock(&hugetlb_fault_mutex_table[hash]);

			if (err) {
				folio_put(folio);
				goto err_unresv;
			}
			...
			return folio;
		}
err_unresv:
		if (nr_resv > 0)
			hugetlb_unreserve_pages(inode, idx, idx + 1, 0);
		return ERR_PTR(err);
```

（註解從略，完整版請用 `sed -n 68,145p mm/memfd.c` 看。）H1、H2 確認修到了。H3：鎖**只包住** `hugetlb_add_to_page_cache()` 一行。

### Step 6：回到參考實作，比對範圍

作者說「照 `hugetlb_no_page()` 做」。去看正本，`mm/hugetlb.c:6508–6527`（v6.18）：

```c
		folio_zero_user(folio, vmf->real_address);
		__folio_mark_uptodate(folio);
		new_folio = true;

		if (vma->vm_flags & VM_MAYSHARE) {
			int err = hugetlb_add_to_page_cache(folio, mapping,
							vmf->pgoff);
			if (err) {
				/*
				 * err can't be -EEXIST which implies someone
				 * else consumed the reservation since hugetlb
				 * fault mutex is held when add a hugetlb page
				 * to the page cache. So it's safe to call
				 * restore_reserve_on_error() here.
				 */
				restore_reserve_on_error(h, vma, vmf->address,
							folio);
```

這段註解就是答案：正本之所以可以在錯誤路徑放心歸還 reservation，是因為**整個 fault 期間都拿著 fault mutex**（在 `hugetlb_fault()` 一開頭就拿了），所以 `-EEXIST` 不可能發生。第二個參考實作 `hugetlbfs_fallocate()`（`fs/hugetlbfs/inode.c:786–822`）也一樣：先拿鎖、在鎖內查 page cache、配置、放進去、錯誤處理，最後才放鎖。

而 memfd 版本：

```
   hugetlb_reserve_pages()     ← 鎖外
   alloc_hugetlb_folio_reserve ← 鎖外
   mutex_lock
   hugetlb_add_to_page_cache() ← 這裡可能回 -EEXIST（Step 4 已知是正常路徑）
   mutex_unlock
   err_unresv: hugetlb_unreserve_pages()  ← 鎖外，而且在 -EEXIST 時也會跑
```

在 `-EEXIST` 的情況下，reservation 已經被那個搶先完成的 fault「用掉」了，memfd 卻還是呼叫 `hugetlb_unreserve_pages()` 歸還一次。**這是 H4 的破口：抄了「拿鎖」這個動作，沒抄「鎖住整個 reserve → alloc → instantiate」這個範圍。**

讀到這裡，一個合格的 review 意見是：

> The fault mutex now only covers hugetlb_add_to_page_cache(). Since the only caller (memfd_pin_folios) explicitly handles -EEXIST, that path is expected; in that case the error path calls hugetlb_unreserve_pages() although the reservation was consumed by the concurrent fault. hugetlb_no_page() relies on holding the mutex across reserve/alloc/instantiate (see the comment above restore_reserve_on_error()). Should the mutex be taken before hugetlb_reserve_pages()?

注意它的結構：**指出位置 → 給出觸發條件 → 引用 code base 自己的證據 → 提出問題而非命令**。

### Step 7：歷史驗證

第一個問題：被 `Fixes:` 的那個 commit 被修過幾次？

```
$ git log --oneline v6.18 --grep='Fixes: 89c1905d9c14'
de8798965fd0 mm/memfd: fix information leak in hugetlb folios
dc677b5f3765 mm/hugetlb: simplify refs in memfd_alloc_folio
ce645b9fdc78 mm/gup: fix memfd_pin_folios alloc race panic
9289f020da47 mm/gup: fix memfd_pin_folios hugetlb page allocation
26a8ea80929c mm/hugetlb: fix memfd_pin_folios resv_huge_pages leak
c56b6f3d801d mm/hugetlb: fix memfd_pin_folios free_huge_pages leak
c225c4f6056b mm/filemap: fix filemap_get_folios_contig THP panic
```

同一個 commit 在 v6.18 之前已經收到七個修正，其中兩個標題就寫著 `resv_huge_pages leak`。這是一個**熱點**：reservation 記帳在這條路徑上反覆出錯。review 時看到這種歷史，標準要往上調。

第二個問題：我們的懷疑有沒有被證實？v6.18 之後的歷史（同一個 clone 的 `origin/master`）：

```
$ git log --oneline v6.18..origin/master -- mm/memfd.c | grep -i fix
50ee565c1e90 mm/memfd: fix hugetlb reservation accounting in error paths
$ git describe --contains 50ee565c1e90
v7.4-rc1~59^2~62
```

它的 message 開頭：

```
    If hugetlb_add_to_page_cache() in memfd_alloc_folio() fails with -EEXIST,
    a concurrent fault has already instantiated the folio in the page cache,
    and the reservation now belongs to that folio.  Calling
    hugetlb_unreserve_pages() in that case incorrectly removes the region
    backing the cached folio and releases one reservation more than it should,
    ...
    So hold the hugetlb fault mutex from hugetlb_reserve_pages() until the
    error-path unreserve completes to make the reserve, allocate and
    instantiate steps atomic against concurrent faults.
```

這就是 Step 6 的推論，幾乎逐字吻合。現在用 `--color-moved` 看這個後續修正——它的核心是「把一段 code 往上搬」，不用 color-moved 會看成「刪一段、加一段」。下面是真實輸出，ANSI 色碼用 `sed -E 's/\x1b\[([0-9;]*)m/<\1>/g'` 轉成文字標記（`<32>`/`<31>` 是一般新增/刪除，`<1;36>`/`<1;35>` 是**被搬動**的新增/刪除）：

```
$ git show --format= --color=always --color-moved=zebra \
      --color-moved-ws=allow-indentation-change 50ee565c1e90 | sed -E '...'
 		idx >>= huge_page_order(h);<>
 <>
<1;36>+<>		<1;36>/*<>
<1;36>+<>		<1;36> * Serialize hugepage allocation and instantiation to prevent<>
...
<1;36>+<>		<1;36>hash = hugetlb_fault_mutex_hash(memfd->f_mapping, idx);<>
<1;36>+<>		<1;36>mutex_lock(&hugetlb_fault_mutex_table[hash]);<>
 		nr_resv = hugetlb_reserve_pages(inode, idx, idx + 1, NULL, EMPTY_VMA_FLAGS);<>
...
<1;35>-			hash = hugetlb_fault_mutex_hash(memfd->f_mapping, idx);<>
<1;35>-			mutex_lock(&hugetlb_fault_mutex_table[hash]);<>
...
 err_unresv:<>
 		if (nr_resv > 0)<>
 			hugetlb_unreserve_pages(inode, idx, idx + 1, 0);<>
<32>+<><32>out_unlock:<>
<1;36>+<>		<1;36>mutex_unlock(&hugetlb_fault_mutex_table[hash]);<>
```

拿鎖那段被整塊搬到 `hugetlb_reserve_pages()` 之前、`mutex_unlock` 被搬到 error path 之後。**如果不加 `--color-moved-ws=allow-indentation-change`**，因為搬動時少了一層縮排，git 認不出這是同一段，只有單獨那行 `mutex_unlock` 會被標成搬動，其他全部顯示成普通的刪除/新增——實測確實如此。

> 認識論提醒：`50ee565c1e90` 不在 v6.18 裡。如果你的 clone 只有到 v6.18，`git show 50ee565c1e90` 會失敗；那就把 Step 7 的第二部分當成「v6.18 時點上未被修的問題」，這反而更接近真實 review 的處境——你沒有未來的答案可以對。

### 這個實例的判決

| 假說 | 判決 | 證據 |
|---|---|---|
| H1 清零 | 修到 | `folio_zero_user(folio, 0)`；`addr_hint=0` 在註解裡有說明理由 |
| H2 uptodate | 修到 | `__folio_mark_uptodate(folio)` |
| H3 拿 fault mutex | 部分 | 拿了，但範圍只有一行 |
| H4 等同 `hugetlb_no_page()` 的 pattern | 不成立 | 正本的錯誤路徑依賴「全程持鎖使 -EEXIST 不可能」，memfd 版本不滿足 |

## code inspection 與 review checklist

Spinellis 在原書提到 code inspection——源自 Michael Fagan 1976 年在 IBM 提出的正式檢查流程：一組人帶著**檢查清單**，逐段走過程式碼，目標是找缺陷而不是討論設計。今天的 mailing list review 鬆散得多，但核心還在：**有清單的 review 比憑感覺的 review 抓到更多東西**，因為清單逼你看 diff 沒顯示的地方。

本課的 patch review 清單（適用於 kernel 的 bug fix）：

```
訊息與脈絡
□ commit message 有沒有說清楚 bug 的觸發條件？我能改寫成可驗證的假說嗎？
□ Fixes: 指向的 commit 是否真的引入了這個 bug？（git show 它看一眼）
□ 同一個 Fixes 目標被修過幾次？（git log --grep='Fixes: <hash>'）
□ 該 Cc 的人與 list 有沒有在？（get_maintainer.pl）

正確性
□ 被改函式的所有呼叫點都看過了？它們怎麼處理每一種回傳值？
□ 每個錯誤路徑：資源（記憶體、refcount、reservation、鎖）有沒有對稱釋放？
□ 鎖：拿鎖的範圍是否涵蓋整個不變量？有沒有在持鎖時睡眠或呼叫可能拿同一把鎖的函式？
□ patch 宣稱「照著 X 做」：X 的前提條件在這裡成立嗎？
□ 同一個 pattern 在 tree 裡還有幾份拷貝？其他拷貝需要一起修嗎？

驗證
□ 有沒有測試或 reproducer？它覆蓋了哪些假說、漏了哪些？
□ checkpatch 乾淨嗎？（形式問題先排掉，不要浪費 reviewer 的注意力）
```

## 對比與取捨

| 讀法 | 優點 | 缺點 | 適用 |
|---|---|---|---|
| `git show <commit>`（預設 3 行） | 快、篇幅小 | 看不到錯誤路徑與條件 | 第一眼判斷改動規模 |
| `git show -W` | 整個函式都在，鎖配對、goto 階梯一覽無遺 | 大函式會很長 | **bug fix 的預設讀法** |
| `git show <hash>:path` 讀完整檔案 | 能看同檔的其他函式與 pattern | 失去「改了哪裡」的標記 | Step 6 比對參考實作 |
| 讀 mailing list thread（`Link:`） | 有討論、有被否決的版本、有 reviewer 的疑慮 | 要花時間找 | 想知道「為什麼這樣修」 |
| `git range-diff` 比對 patch 的 v1/v2 | 看出作者根據 review 改了什麼 | 要先把兩版都抓下來 | 跟進一個還在 review 中的 series |

## 踩雷集錦

1. **「diff 裡沒問題就沒問題」**：真 bug 幾乎都在 diff 外——本章實例的問題出在**沒被改到**的 `hugetlb_reserve_pages()` 位置與 error path。每次讀 bug fix，強制自己用 `-W` 或回到完整檔案。
2. **相信 hunk header 的函式名**：它是 git 往上找的猜測，`-W` 一開就可能顯示前一個函式（實測 `memfd_tag_pins`）。要確認位置看行號。
3. **把 Acked-by 當成 Reviewed-by**：`submitting-patches.rst` 第 483 行明說 Acked-by 較不正式。三個 Ack、零個 Review 的 patch，代表方向被認可，不代表有人逐行簽了審查聲明。
4. **以為 Fixes: 指向「bug 變得可利用」的那個 commit**：它指向作者認為的起點。Dirty Pipe（CVE-2022-0847）的修正 `9d2231c5d74e` 寫的是 `Fixes: 241699cd72a8`（v4.9，新增 pipe-backed iov_iter 時沒初始化 `flags`），但真正讓未初始化的 flags 變成可利用漏洞的，是 v5.8 的 `f6dd975583bd`（引入 `PIPE_BUF_FLAG_CAN_MERGE`）。判斷影響範圍要兩個都看。
5. **只看 commit message 不看 code**：message 是作者的自我陳述，它說「match the pattern」不代表 match 了。本章的 H4 就是 message 寫對、code 沒做到。

## 進階：再往深一層

- **stable backport 的讀法**：stable tree 裡的同一個修正常常長得不一樣（上下文不同、API 不同）。讀 backport 時先 `git log --grep='commit <upstream hash> upstream'` 找對應，再比對兩者差異，看 backport 有沒有漏掉依賴的前置 patch。
- **`git log -L` 追一個函式的所有修改**：`git log -L :memfd_alloc_folio:mm/memfd.c v6.18` 把這個函式的每次改動都列出來，是 Step 7 的加強版（blobless clone 第一次跑會下載 blob，比較慢）。[Ch 37 — git 考古](./37-git-archaeology.md) 有完整介紹。
- **b4**：kernel 社群的 patch 工具，`b4 am <message-id>` 從 lore 抓整個 series、自動加上 trailer。本環境未安裝，**本段未實測**；安裝與用法見 [Ch 38](./38-mailing-list-archaeology.md)。
- **面試題**：「你怎麼 review 一個你不熟的子系統的 patch？」好的回答要包含：先讀呼叫點、找參考實作比對、查 Fixes 目標的修補歷史、把疑問寫成「觸發條件 + 證據 + 問題」。

## 動手練習

1. 讀 `1c2a936edd71 mm, swap: fix potential UAF issue for VMA readahead`（v6.18 內，+13 行）。按本章七步驟走一遍，回答：它在 `swp_type(entry) != swp_type(targ_entry)` 時才拿 device reference，為什麼相同 type 時不用拿？證據在哪個呼叫者？
2. 對 `3d252160b818 fs/pipe: Read pipe->{head,tail} atomically outside pipe->mutex` 跑 `git show -W`，找出所有在 `pipe->mutex` **外**讀 `head`/`tail` 的地方，確認 patch 都改到了。
3. 跑 `git log --oneline v6.18 --grep='Fixes: 241699cd72a8'`，看有幾個 commit 明確標了 Fixes 指向 pipe-backed iov_iter 的引入點。結果和你的預期一樣嗎？

<details>
<summary>練習提示與參考答案</summary>

1. 呼叫 `swap_vma_readahead()` 的人已經為 `targ_entry` 的 swap device 拿了 reference（commit message 第二段就這麼說），所以同一個 device 的其他 entry 受同一個 reference 保護；不同 device 才需要 `get_swap_device()`。要確認，從 `swap_vma_readahead`（`mm/swap_state.c:730`）往上找呼叫者 `swapin_readahead`（`mm/swap_state.c:816`），再往上是 `do_swap_page()`（`mm/memory.c:4582`）：它在第 4659 行 `si = get_swap_device(entry);` 拿了 reference，第 4714 行才呼叫 `swapin_readahead()`。
2. v6.18 的三個 lockless 讀者是 `pipe_readable()`（`fs/pipe.c:230`）、`pipe_writable()`（`fs/pipe.c:421`）、`pipe_poll()`（`fs/pipe.c:660`），三個都改用 `union pipe_index idx = { .head_tail = READ_ONCE(pipe->head_tail) }`。Final Project 會再回來看這個 commit。
3. v6.18 裡只有一個：`9d2231c5d74e lib/iov_iter: initialize "flags" in new pipe_buffer`，也就是 Dirty Pipe 的修正本身。教訓是 `--grep='Fixes: <hash>'` 只找得到**有標 tag** 的修正；早年的修正常常沒標，或用 8 位短 hash 標。要找全，再補 `git log --grep=<短 hash>` 與 `git log -L` 交叉比對。

</details>

## 本章重點整理

- diff 只是施工告示：告訴你看哪裡，不告訴你看到哪裡為止；bug fix 一律用 `-W` 或回到完整檔案讀。
- 固定順序：message → 測試 → 資料結構 → 呼叫點 → diff 本體 → 完整檔案與參考實作 → 歷史。
- trailer 是 metadata：Fixes 決定影響版本、Cc stable 決定 backport、Link 給「為什麼」、Reviewed-by 和 Acked-by 份量不同。
- 「照著 X 做」的 patch，要檢查 X 的前提在這裡是否成立——抄動作不抄範圍是最常見的失敗。

## 自我檢核

- [ ] 不看筆記，畫出 unified diff 的四種 header 行，說出每一行能拿來做什麼
- [ ] 能說出 hunk header 的函式名是怎麼來的，以及至少一個它會出錯的情境
- [ ] 如果面試官問「你 review 一個 patch 的流程是什麼」，能用三十秒講完七個步驟，並解釋為什麼 diff 本體排在第五
- [ ] 能解釋本章實例中，為什麼「鎖只包住一行」會讓 error path 的 `hugetlb_unreserve_pages()` 出錯
- [ ] 想一個你最近 review 過（或被 review）的 PR，套用本章的清單，找出當時漏看的一項

## 延伸閱讀

### 官方文件

- **[Submitting patches: the essential guide to getting your code into the kernel](https://docs.kernel.org/process/submitting-patches.html)**（tree 內 `Documentation/process/submitting-patches.rst`）
  - **讀哪裡**：「Describe your changes」（Fixes 格式與 12 位 hash，第 146 行起）、「Using Reported-by:, Tested-by:, Reviewed-by:, Suggested-by: and Fixes:」（第 539 行起）、Reviewer's statement of oversight
  - **學到什麼**：每個 trailer 的正式語意；讀 patch 時判斷「誰保證了什麼」的依據
  - **前提**：會用 git
- **[Everything you ever wanted to know about Linux -stable releases](https://docs.kernel.org/process/stable-kernel-rules.html)**（`Documentation/process/stable-kernel-rules.rst`）
  - **讀哪裡**：Option 1（`Cc: stable@vger.kernel.org` 的用法，第 67 行起）
  - **學到什麼**：哪些修正會被 backport、怎麼標版本範圍；讀 stable tree 的 patch 時必備
- **`git help diff` 的 `--color-moved`、`--function-context` 段落與 `git help gitattributes` 的「Defining a custom hunk-header」**
  - **學到什麼**：本章四個選項的完整語意，以及如何為自己的專案寫 `xfuncname` regex

### 論文

- **Design and Code Inspections to Reduce Errors in Program Development** — Michael E. Fagan，IBM Systems Journal 15(3)（1976）
  - **核心貢獻**：提出有角色分工、有檢查清單、有缺陷統計的正式 code inspection 流程，並用 IBM 內部資料顯示它比測試更早、更便宜地抓到缺陷
  - **讀哪裡**：描述 inspection 流程（overview → preparation → inspection → rework → follow-up）與檢查清單的段落
  - **和本章的關聯**：本章 review 清單的祖先；理解「為什麼要有清單」比清單內容更重要

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 10 章（Code-Reading Tools）中 diff 與版本控制的段落，以及談 code inspection 的部分
  - **定位**：工具是 CVS 時代的，但「用版本差異理解變化」的觀念不變；讀完再回來對照本章 git 時代的做法

讀 patch 是把前面所有章節壓進一個殘缺視窗的練習；下一章把整門課的方法收斂成一份可以隨身帶著的箴言與檢查清單。

→ [Ch 58 讀碼箴言與檢查清單](./58-maxims-and-checklist.md)
