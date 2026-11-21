# Ch 46 — 快速補領域知識：LWN、Documentation、書與論文

> **目標**：碰到一個陌生的子系統時，知道該按什麼順序去哪裡找背景知識、每個來源能給你什麼、怎麼判斷它有沒有過時；能在 30 分鐘內替一個陌生子系統建立「足夠開始讀碼」的概念模型與術語表；理解領域知識和讀碼如何互相引導。
>
> **鴻溝**：本章主要練**領域鴻溝**——語法都懂、慣用法也認得，但不知道這段程式碼在解決什麼問題。

> **環境**：Linux kernel v6.18（`~/code_reading/linux`）；示範子系統為 maple tree；在 `~/code_reading/scratch/46/` 用 `git archive` 匯出原始碼建置 user-space 測試。

## 為什麼需要這個？

[Ch 1](./01-why-you-cant-read-it.md) 定義的五道鴻溝裡，領域鴻溝最讓人挫折，因為**它是唯一一道「再認真讀程式碼也過不去」的鴻溝**。慣用法鴻溝靠查、間接鴻溝靠工具、脈絡鴻溝靠 git，這三道都能在原始碼樹裡解決。但如果你不知道 B-tree 的 node 為什麼要分 pivot 和 slot、不知道 RCU reader 為什麼不能看到半更新的節點，你可以把 `lib/maple_tree.c` 的 7268 行逐字讀完，還是不懂它在幹嘛。

傳統的建議是「先去讀書」。問題是：

- 書很慢。一本 OS 教科書讀完要幾週，而你今天下午就想讀懂某個函式。
- 書很舊。kernel 每 9–10 週出一個版本，書出版時講的實作細節常常已經改了。maple tree 在 v6.1（2022 年 12 月）才合入，2010 年出版的書不可能提到它。
- 書很廣。你要的是「maple tree 的 node 結構」，書給你的是「資料結構概論」。

所以需要一個**分層的查找策略**：從離程式碼最近、最新、最具體的來源開始，不夠再往遠處走。這一章把這個策略系統化，然後用 maple tree 實際走一遍。

## 先建立直覺

把補領域知識想成**問路**。你在一個陌生城市迷路，有幾種方法：

```
   離你近、便宜、可能不完整                           離你遠、貴、深入但可能過時
   ─────────────────────────────────────────────────────────────────────────▶
   ① 路邊的路牌      ② 當地導覽報     ③ 市政府的       ④ 導遊的       ⑤ 旅遊書    ⑥ 城市規劃
   （MAINTAINERS、   （LWN）          規劃說明書        現場解說        （書）      論文
    Documentation/）                  （cover letter）  （會議演講）                （論文）

   新鮮度：隨版本走   按日期           寫在合入當時      演講當年        出版當年    研究當年
   具體度：最具體     具體             最具體（為什麼）   中               抽象        最抽象
   成本：  分鐘        10 分鐘          10–30 分鐘        30–60 分鐘       數小時      數小時
```

原則是**由左往右，夠了就停**。大部分時候走到 ③ 就足以開始讀碼；④⑤⑥ 是你要深入某個子系統、或碰到真正艱深的演算法時才需要。

這個類比的限制：真實情況下各層會互相指引——LWN 文章會連到 cover letter，cover letter 會引用論文——你不會嚴格按順序走。順序的意義在於**決定先打開哪一個**。

## 查找順序：六層來源

### ① 原始碼樹內：MAINTAINERS 與 Documentation/

第一步永遠是查 `MAINTAINERS`。它告訴你這個子系統的邊界（哪些檔案）、誰負責、去哪個 mailing list 問：

```console
$ sed -n '/^MAPLE TREE/,/^$/p' MAINTAINERS
MAPLE TREE
M:	Liam R. Howlett <Liam.Howlett@oracle.com>
R:	Alice Ryhl <aliceryhl@google.com>
R:	Andrew Ballance <andrewjballance@gmail.com>
L:	maple-tree@lists.infradead.org
L:	linux-mm@kvack.org
S:	Supported
F:	Documentation/core-api/maple_tree.rst
F:	include/linux/maple_tree.h
F:	include/trace/events/maple_tree.h
F:	lib/maple_tree.c
F:	lib/test_maple_tree.c
F:	rust/helpers/maple_tree.c
F:	rust/kernel/maple_tree.rs
F:	tools/testing/radix-tree/maple.c
F:	tools/testing/shared/linux/maple_tree.h
```

這 15 行給的資訊比你想像的多：

- `F: Documentation/core-api/maple_tree.rst` → **有官方文件**，下一步讀它。
- `F: lib/test_maple_tree.c`、`F: tools/testing/radix-tree/maple.c` → **有測試**，而且有 user-space 版本（[Ch 40](./40-tests-as-specification.md) 說過，測試是可執行的規格）。
- `F: rust/kernel/maple_tree.rs` → 有 Rust 綁定，代表有 Rust 端的使用者，API 被認為夠穩定。
- `L: maple-tree@lists.infradead.org` → 專屬 mailing list，去 lore.kernel.org 能找到歷史討論（[Ch 38](./38-mailing-list-archaeology.md)）。

`Documentation/` 的優點是**它和程式碼在同一棵樹、同一個版本**。你讀的是 v6.18 的文件，對應 v6.18 的程式碼。缺點是覆蓋率不均，而且 [Ch 39](./39-documentation-and-style.md) 說過它也會過時——但至少你可以用 `git log` 查它最後更新的時間。

### ② LWN.net Kernel Index

[LWN Kernel Index](https://lwn.net/Kernel/Index/) 是按主題分類的 kernel 文章索引，二十多年來幾乎每個重要的子系統變動都有報導。它的價值在於**有人替你把 mailing list 上幾百封信消化成一篇三千字的文章**。

maple tree 在 Index 裡的條目（2026-11 查詢）：

| 日期 | 標題 | 對應的 kernel 時間點 |
|---|---|---|
| 2019-05-08 | How to get rid of mmap_sem | 構想期 |
| 2021-02-12 | Introducing maple trees | RFC 階段，距合入將近兩年 |
| 2022-05-06 | The ongoing search for mmap_lock scalability | 合入前夕 |
| 2024-05-27 | The next steps for the maple tree | 合入（v6.1）後一年半 |

讀 LWN 的關鍵技巧是**對照版本**，下一節專門講。

LWN 的部分新文章在發表後一週內只對訂閱者開放，之後免費。Kernel Index 本身任何人都能看。

### ③ 原始 patch series 的 cover letter

一個功能第一次被提出時，作者會寫一封 cover letter（patch 0/N）說明動機、設計、取捨。這是**最接近作者原意**的文件，而且寫在合入當時，時間點很明確。

找 cover letter 有兩條路：

1. **lore.kernel.org**：用 commit message 的 `Link:` tag 直接跳過去（[Ch 38](./38-mailing-list-archaeology.md)）。
2. **commit message 本身**：mm 子系統的維護者 Andrew Morton 有個習慣，會把 cover letter 貼進 series 第一個 commit 的訊息裡。所以對 mm 相關的功能，`git log` 就能讀到 cover letter。

maple tree 正是後者：

```console
$ git log --format=%h --diff-filter=A -- lib/maple_tree.c | tail -1 | xargs git log -1 --format='%h %ad %an %s' --date=short
54a611b60590 2022-09-06 Liam R. Howlett Maple Tree: add new data structure
$ git describe --contains 54a611b60590
v6.1-rc1~21^2~394
$ git log -1 --format=%B 54a611b60590 | head -26
Maple Tree: add new data structure

Patch series "Introducing the Maple Tree"

The maple tree is an RCU-safe range based B-tree designed to use modern
processor cache efficiently.  There are a number of places in the kernel
that a non-overlapping range-based tree would be beneficial, especially
one with a simple interface.  If you use an rbtree with other data
structures to improve performance or an interval tree to track
non-overlapping ranges, then this is for you.

The tree has a branching factor of 10 for non-leaf nodes and 16 for leaf
nodes.  With the increased branching factor, it is significantly shorter
than the rbtree so it has fewer cache misses.  The removal of the linked
list between subsequent entries also reduces the cache misses and the need
to pull in the previous and next VMA during many tree alterations.

The first user that is covered in this patch set is the vm_area_struct,
where three data structures are replaced by the maple tree: the augmented
rbtree, the vma cache, and the linked list of VMAs in the mm_struct.  The
long term goal is to reduce or remove the mmap_lock contention.
```

訊息結尾的 `Link:` tag 指向 lore 上的原始郵件：

```console
$ git log -1 --format=%B 54a611b60590 | grep 'Link:'
Link: https://lkml.kernel.org/r/20220906194824.2110408-1-Liam.Howlett@oracle.com
Link: https://lkml.kernel.org/r/20220906194824.2110408-2-Liam.Howlett@oracle.com
```

`-1-` 是 cover letter 本身（patch 0），`-2-` 是第一個 patch。

這段 cover letter 一口氣回答了三個領域問題：**它是什麼**（RCU-safe、range-based B-tree）、**為什麼需要**（取代 VMA 的 rbtree + vma cache + linked list 三個資料結構，最終目標是減少 mmap_lock 競爭）、**為什麼比舊方案好**（分支因子大、樹矮、cache miss 少）。

### ④ 會議演講

kernel 開發者的演講通常比文件更口語、更願意講「我們試過什麼但失敗了」。三個最重要的會議：

- **Linux Plumbers Conference（LPC）**：kernel 開發者面對面討論設計的場合，議程與投影片放在 lpc.events。Liam Howlett 在 LPC 2019（與 Matthew Wilcox 合講，Kernel Summit track）、LPC 2021（Performance and Scalability track）、LPC 2022（Kernel Summit track）都講過 maple tree。
- **Kernel Recipes**：法國巴黎的單軌會議，演講者多是子系統維護者，影片與投影片公開。
- **Open Source Summit（OSS）**：Linux Foundation 主辦，範圍較廣。

另外 Linux Foundation 的 Mentorship Series webinar 有一場 Liam Howlett 的「The Maple Tree: Structure and Algorithms」（2024），投影片公開。

演講的缺點是**它講的是演講當年的狀態**。LPC 2019 的 maple tree 演講是在徵求使用者需求的階段，和最終合入的設計會有差距。

### ⑤ 書

書適合補**穩定的基礎概念**，不適合查**特定版本的實作細節**。對 kernel 讀碼最有用的幾本：

| 書 | 出版年 | 適合補什麼 | 注意 |
|---|---|---|---|
| 《Linux Kernel Development》3rd ed., Robert Love | 2010 | 子系統的概念導覽（process、scheduler、VFS、memory） | 2.6.34 時代；資料結構章沒有 xarray、maple tree |
| 《Understanding the Linux Kernel》3rd ed., Bovet & Cesati | 2005 | 比 Love 深得多的機制說明 | 2.6.11 時代；細節多已改變，設計理由仍有價值 |
| 《Understanding the Linux Virtual Memory Manager》, Mel Gorman | 2004 | mm 子系統的經典 | 2.4／2.6 早期；VMA 那章講的正是 maple tree 取代掉的舊結構 |
| 《The Linux Programming Interface》, Michael Kerrisk | 2010 | user space 看到的 syscall 語意 | 讀 syscall 實作前先讀它 |

注意 Gorman 那本：它描述的 VMA 管理（rbtree + linked list）正是 maple tree 要取代的東西。**讀舊書了解「被取代的是什麼」，本身就是理解新設計的一種方法**。

### ⑥ 論文

學術論文提供的是**最抽象、最嚴謹**的背景：演算法的正確性證明、效能模型、和其他方案的系統性比較。kernel 的實作很少直接照論文做，但 cover letter 常會引用論文。maple tree 的 cover letter 就提到：

```
A similar work has been discovered in the academic press

	https://pdos.csail.mit.edu/papers/rcuvm:asplos12.pdf

Sheer coincidence.  We designed our tree with the intention of solving the
hardest problem first.  Upon settling on a b-tree variant and a rough
outline, we researched ranged based b-trees and RCU b-trees and did find
that article.  So it was nice to find reassurances that we were on the
right path, but our design choice of using ranges made that paper unusable
for us.
```
（`54a611b60590` 的 commit message）

那篇論文是 Clements、Kaashoek、Zeldovich 的〈Scalable Address Spaces Using RCU Balanced Trees〉（ASPLOS 2012），提出用 RCU 平衡二元樹（BONSAI）存放記憶體映射，讓 soft page fault 能和修改位址空間的操作並行。cover letter 這段話很有價值：它告訴你**論文和實作的關係**——方向相同（RCU + 樹 → 減少 mmap 鎖競爭），但 maple tree 選了 range-based B-tree，所以論文的具體設計用不上。讀論文能幫你理解「為什麼要 RCU 化 VMA 查詢」，但不能幫你讀懂 `mas_wr_node_store()`。

## 判斷 LWN 文章（或任何二手來源）是否過時

二手來源最大的風險是：**它寫的時候是真的，現在不是了**。判斷方法是把文章裡的每個具體主張，對照到 kernel 版本與現在的程式碼。

### 步驟

```
  1. 文章日期 ──▶ 對應到哪個 kernel 版本？（git log -1 --format=%ci <tag>）
  2. 文章說的狀態 ──▶ RFC？已合入？哪一版合入？（git describe --contains）
  3. 文章提到的函式、struct、檔案 ──▶ 現在還在嗎？（rg / git show <tag>:<file>）
  4. 文章的具體主張 ──▶ 對照現在的程式碼，還成立嗎？
  5. Kernel Index 裡同主題有沒有更新的文章？
```

### 實例：用 v6.18 檢驗「Introducing maple trees」（2021-02-12）

根據 LWN Kernel Index 的條目與這篇文章的摘要，它描述的是：一個 70 個 patch 的 RFC series，把 VMA 操作中的 rbtree 換成 maple tree，並移除 VMA cache 與 linked list；當時程式碼還在 RFC 階段，**不支援 32 位元系統**。

逐條對照：

**1. 時間點。**

```console
$ git log -1 --format=%ci v5.11
2021-02-14 14:32:24 -0800
$ git log -1 --format=%ci v6.1
2022-12-11 14:15:18 -0800
```

文章發表時 kernel 剛到 v5.11；maple tree 合入的 v6.1 是將近兩年後。所以文章描述的是一個**還會改很多**的版本。

**2. 狀態。** `git describe --contains 54a611b60590` 得到 `v6.1-rc1~21^2~394`：最終合入 v6.1。commit message 寫「This patch (of 70)」——70 個 patch 的規模和 RFC 時一致。

**3. 「不支援 32 位元」還成立嗎？**

```c
#if defined(CONFIG_64BIT) || defined(BUILD_VDSO32_64)
/* 64bit sizes */
#define MAPLE_NODE_SLOTS	31	/* 256 bytes including ->parent */
#define MAPLE_RANGE64_SLOTS	16	/* 256 bytes */
#define MAPLE_ARANGE64_SLOTS	10	/* 240 bytes */
#define MAPLE_ALLOC_SLOTS	(MAPLE_NODE_SLOTS - 1)
#else
/* 32bit sizes */
#define MAPLE_NODE_SLOTS	63	/* 256 bytes including ->parent */
#define MAPLE_RANGE64_SLOTS	32	/* 256 bytes */
#define MAPLE_ARANGE64_SLOTS	21	/* 240 bytes */
#define MAPLE_ALLOC_SLOTS	(MAPLE_NODE_SLOTS - 2)
#endif /* defined(CONFIG_64BIT) || defined(BUILD_VDSO32_64) */
```
（`include/linux/maple_tree.h:27-39`（v6.18））

v6.18 有明確的 32 位元分支。**這條主張已經過時**——而且 VMA 管理在所有架構上都用 maple tree（`struct mm_struct` 裡就是 `struct maple_tree mm_mt;`，`include/linux/mm_types.h:961`），不可能只支援 64 位元。

**4. 「分支因子 10 / 16」這類數字。** cover letter 寫「branching factor of 10 for non-leaf nodes and 16 for leaf nodes」。對照上面的常數：`MAPLE_ARANGE64_SLOTS` 是 10、`MAPLE_RANGE64_SLOTS` 是 16，在 64 位元上仍然成立。但要注意這句話是以 VMA tree 的情境來說的：VMA tree 開了 allocation range（追蹤 gap）功能，內部節點用 `maple_arange_64`（10 個 slot）。沒開 gap 追蹤的 maple tree，內部節點用 `maple_range_64`，是 16 個 slot。**數字沒錯，但它的適用範圍比句子看起來窄**——這是讀二手來源時另一種常見的陷阱。

**5. 更新的文章。** Kernel Index 裡有 2024-05-27 的「The next steps for the maple tree」。讀 2021 年的文章建立歷史脈絡，讀 2024 年的了解現況。

結論：**2021 年的文章對「為什麼要做 maple tree」仍然是好材料，對「maple tree 現在支援什麼」已經不可靠**。動機類的內容老化得慢，實作細節老化得快。

## 30 分鐘實戰：替 maple tree 建立概念模型

下面是一次真實的流程記錄。時間是目標分配，實際上每一步的指令都跑過；建置和測試各花約 30 秒（實測）。

> 如果你已經讀過 [Ch 26 — xarray 與 maple tree](./26-xarray-maple-tree.md)，這一節可以當作「假裝不知道，重走一次」的練習。本節的重點是**獲取知識的過程**，不是 maple tree 本身。

### 第 0–3 分鐘：劃定邊界

讀 `MAINTAINERS`（上面已列）。產出：檔案清單、有文件、有 user-space 測試。再看規模：

```console
$ wc -l lib/maple_tree.c include/linux/maple_tree.h
  7268 lib/maple_tree.c
   903 include/linux/maple_tree.h
  8171 total
```

8000 行。決定：**不讀 `.c`，先讀 `.h` 和文件**（[Ch 4](./04-selective-reading.md) 的選擇性閱讀）。

### 第 3–10 分鐘：讀官方文件的 Overview

`Documentation/core-api/maple_tree.rst` 共 221 行，先讀 Overview 一節：

```
The Maple Tree is a B-Tree data type which is optimized for storing
non-overlapping ranges, including ranges of size 1.  The tree was designed to
be simple to use and does not require a user written search method.  It
supports iterating over a range of entries and going to the previous or next
entry in a cache-efficient manner.  The tree can also be put into an RCU-safe
mode of operation which allows reading and writing concurrently.  Writers must
synchronize on a lock, which can be the default spinlock, or the user can set
the lock to an external lock of a different type.
```
（`Documentation/core-api/maple_tree.rst:13-20`（v6.18））

邊讀邊抽術語，寫進筆記：B-Tree、non-overlapping range、RCU-safe mode、writer lock。再往下：

```
The advanced API is based around the ma_state, this is where the 'mas'
prefix originates.  The ma_state struct keeps track of tree operations to make
life easier for both internal and external tree users.
```
（`Documentation/core-api/maple_tree.rst:145-147`（v6.18））

這一段解開了一個慣用法鴻溝：程式碼裡滿地的 `mas_*` 函式，`mas` 是 **ma_state** 的縮寫。而 `mtree_*` 是 normal API。這種命名線索，文件不寫你要猜很久。

### 第 10–15 分鐘：讀 cover letter

上面已經讀過。產出三句話的「為什麼」：

1. 取代 VMA 的三個結構（augmented rbtree、vma cache、linked list）。
2. B-tree 分支因子大 → 樹矮 → cache miss 少。
3. 長期目標：RCU 模式下 reader 不被 writer 擋住，減少 mmap_lock 競爭。

### 第 15–20 分鐘：讀標頭檔的資料結構

文件給了概念，標頭檔給形狀（[Ch 5](./05-entry-points-and-data-first.md) 的「資料結構優先」）：

```c
enum maple_type {
	maple_dense,
	maple_leaf_64,
	maple_range_64,
	maple_arange_64,
};
```
（`include/linux/maple_tree.h:144-149`（v6.18））

```c
struct maple_tree {
	union {
		spinlock_t		ma_lock;
#ifdef CONFIG_LOCKDEP
		struct lockdep_map	*ma_external_lock;
#endif
	};
	unsigned int	ma_flags;
	void __rcu      *ma_root;
};
```
（`include/linux/maple_tree.h:229-238`（v6.18））

還有一段關鍵的術語定義：

```c
 * In regular B-Tree terms, pivots are called keys.  The term pivot is used to
 * indicate that the tree is specifying ranges,  Pivots may appear in the
 * subtree with an entry attached to the value whereas keys are unique to a
 * specific position of a B-tree.  Pivot values are inclusive of the slot with
 * the same index.
 */

struct maple_range_64 {
	struct maple_pnode *parent;
	unsigned long pivot[MAPLE_RANGE64_SLOTS - 1];
	union {
		void __rcu *slot[MAPLE_RANGE64_SLOTS];
```
（`include/linux/maple_tree.h:96-107`（v6.18））

**pivot 就是 B-tree 的 key，但它表示範圍的上界（inclusive）**。16 個 slot 搭配 15 個 pivot：slot[i] 涵蓋 (pivot[i-1], pivot[i]]。這一句把「B-tree」和「range」兩個概念接起來了。

### 第 20–27 分鐘：跑測試，看真實的樹

文件和標頭檔讀完，腦中有了模型，但還沒「看過」一棵 maple tree。`tools/testing/radix-tree/` 能把 kernel 的 `lib/maple_tree.c` 直接編成 user-space 程式。為了不弄髒 `~/code_reading/linux`，用 `git archive` 把需要的目錄匯出到 scratch 再建置：

```console
$ cd ~/code_reading/linux
$ mkdir -p ~/code_reading/scratch/46/src
$ git archive v6.18 tools/testing/radix-tree tools/testing/shared tools/include tools/lib \
      tools/arch/x86 tools/scripts lib/maple_tree.c lib/test_maple_tree.c lib/xarray.c \
      lib/radix-tree.c lib/idr.c lib/find_bit.c lib/bitmap.c lib/test_xarray.c \
      include/linux include/asm-generic include/uapi include/vdso \
      include/trace/events/maple_tree.h lib/radix-tree.h \
  | tar -x -C ~/code_reading/scratch/46/src
$ cd ~/code_reading/scratch/46/src/tools/testing/radix-tree
$ time make maple -j8 2>&1 | grep -v warning | tail -1
cc -fsanitize=address -fsanitize=undefined  maple.o xarray-shared.o radix-tree.o idr.o linux.o slab.o find_bit.o bitmap.o hweight.o vsprintf.o xarray.o test.o  -lpthread -lurcu -o maple

real	0m31.938s
$ time ./maple 2>&1 | tail -3
maple_tree: 346014041 of 346014041 tests passed
nr_allocated = 1776

real	0m31.409s
```

（需要 liburcu；這台機器已裝。注意它預設開了 AddressSanitizer 與 UBSan。）

三億多個斷言全過。但對建立概念模型更有用的是測試順便印出的**樹的 dump**：

```
maple_tree(0x5e8a172fc1c0) flags C, height 3 root 0x515016286a16
0-18446744073709551615: node 0x515016286a00 depth 0 type 2 parent 0x5e8a172fc1c1 contents: 0x515016262114 224 0x515016271614 449 0x515016280b14 674 0x515016286f14 809 0x51501628c214 18446744073709551615 (nil) 0 ... 0x4
  0-224: node 0x515016262100 depth 1 type 2 parent 0x515016286a06 contents: 0x51501624af0c 14 0x51501624be0c 29 ... 0x51501625860c 224 0xe
    0-14: node 0x51501624af00 depth 2 type 1 parent 0x515016262106 contents: 0x1 0 0x3 1 0x5 2 0x7 3 0x9 4 0xb 5 0xd 6 0xf 7 0x11 8 0x13 9 0x15 10 0x17 11 0x19 12 0x1b 13 0x1d 14 0xe
      0: value 0 (0x0) [0x1]
      1: value 1 (0x1) [0x3]
      2: value 2 (0x2) [0x5]
```
（`./maple` 輸出節錄，位址每次執行不同）

現在用剛建立的模型解讀它——這就是「領域知識引導讀碼」的瞬間：

| dump 裡看到的 | 用模型解釋 |
|---|---|
| `height 3` | 三層的樹；存了約一千個 entry 才三層，對應 cover letter 說的「樹很矮」 |
| `type 2`、`type 1` | `enum maple_type`：2 = `maple_range_64`（內部節點），1 = `maple_leaf_64`（葉節點） |
| `contents: <指標> 224 <指標> 449 ...` | slot 和 pivot 交錯印出：slot[0] 涵蓋 0–224、slot[1] 涵蓋 225–449…… |
| 葉節點 `0-14` 有 15 個 entry | 一個 leaf 塞滿是 16 個 slot；這裡存了 15 個 |
| root `0x...6a16`，node 位址 `0x...6a00` | 低位元被拿來編碼型別：`0x16 >> 3 = 2`（range_64）；node 本身 256 bytes 對齊 |
| `value 0 (0x0) [0x1]` | 存的是 `xa_mk_value(0)`，編碼成 `0x1`；呼應文件說「低兩位為 `10` 的小值保留給內部用」，所以使用者要用 `xa_mk_value()` 轉換 |

最後一列的「低位元編碼型別」，可以在 `lib/maple_tree.c` 確認：

```c
static __always_inline enum maple_type mte_node_type(
		const struct maple_enode *entry)
{
	return ((unsigned long)entry >> MAPLE_NODE_TYPE_SHIFT) &
		MAPLE_NODE_TYPE_MASK;
}
```
（`lib/maple_tree.c:231-236`（v6.18）；`MAPLE_NODE_TYPE_SHIFT` 為 `0x03`、`MAPLE_NODE_TYPE_MASK` 為 `0x0F`，定義在 `include/linux/maple_tree.h:191-192`）

`0x16 >> 3 & 0xF = 2`，`0x0e >> 3 & 0xF = 1`。dump 和程式碼對上了。

### 第 27–30 分鐘：寫下術語表、模型圖與待解問題

```
                    struct maple_tree
                    ├─ ma_lock（writer 用）
                    └─ ma_root ──▶ 編碼過的指標（低位元 = node type）
                                     │
               ┌─────────────────────┴───────────────────────┐
               ▼                                               
     maple_range_64 / maple_arange_64（內部節點）
       pivot[0..14]  上界（inclusive）
       slot[0..15]   子節點指標
       （arange 多了 gap[]，用來找空洞；VMA tree 用這種）
               │
               ▼
     maple_leaf_64（葉節點）
       pivot[0..14]
       slot[0..15]   使用者存的 entry（void *）
```

待解問題（帶著它們去讀 `.c`，這是 [Ch 3](./03-question-driven-reading.md) 的提問驅動）：

1. 寫入時節點滿了怎麼辦？split 的邏輯在哪？（猜：`mas_wr_*` 系列）
2. RCU 模式下，reader 怎麼知道自己讀到的節點已經被替換？（線索：標頭檔第 21–23 行說「Removed nodes have their ->parent set to point to themselves」）
3. VMA 的程式碼怎麼用它？（線索：`struct vma_iterator` 裡面就是一個 `struct ma_state mas;`，`include/linux/mm_types.h:1352-1354`）

## 建立術語表

術語表是領域知識的「外部化」（[Ch 7](./07-reading-notes-and-diagrams.md)）。一個好的術語表條目有四欄：

| 術語 | 一句話定義 | 在程式碼裡長什麼樣 | 從哪學到的 |
|---|---|---|---|
| range | 一段連續的 index 區間，maple tree 的基本儲存單位 | `mtree_store_range(mt, first, last, entry, gfp)` | 文件 Overview |
| pivot | 節點內各 slot 範圍的上界（inclusive）；B-tree 的 key | `node->pivot[i]` | `maple_tree.h:96-100` 註解 |
| slot | 節點內的指標欄位，內部節點指向子節點、葉節點存 entry | `node->slot[i]` | `maple_tree.h` struct 定義 |
| node type | dense／leaf_64／range_64／arange_64 | `enum maple_type`；編碼在指標的 bit 3–6 | `maple_tree.h:144`、`mte_node_type()` |
| arange | allocation range：多一個 `gap[]` 陣列，可找出夠大的空洞 | `maple_arange_64`、`MT_FLAGS_ALLOC_RANGE` | cover letter + 標頭檔 |
| ma_state / mas | 記錄一次樹操作的游標狀態；advanced API 的核心 | `MA_STATE(mas, mt, index, last)`、`mas_*()` | 文件 Advanced API 節 |
| normal API | 自己處理 lock 的簡化介面 | `mtree_*()` | 文件 Normal API 節 |
| RCU mode | reader 不拿鎖，writer 互斥；被換掉的節點延遲釋放 | `MT_FLAGS_USE_RCU` | 文件 Overview + cover letter |

「從哪學到的」那一欄很重要：之後發現某個定義錯了，你知道是哪個來源誤導了你，也知道該回去對照哪裡。

## 領域知識和讀碼的互相引導

上面的流程有一個值得注意的形狀：**不是先學完再讀，而是交替進行**。

```
        ┌──────────────────────────────────────────────────┐
        │                                                  │
        ▼                                                  │
   ┌─────────┐   產生術語與假說   ┌─────────┐   碰到不懂的   │
   │ 領域知識 │ ─────────────────▶ │  讀碼   │ ─────────────┘
   │（文件、  │                    │（.h、    │   名詞或行為
   │ LWN、   │ ◀───────────────── │  dump、  │
   │ cover   │   驗證或推翻假說    │  .c）    │
   │ letter）│                    └─────────┘
   └─────────┘
```

具體例子：

- **知識 → 碼**：cover letter 說「分支因子 10/16」→ 去標頭檔找到 `MAPLE_ARANGE64_SLOTS 10`、`MAPLE_RANGE64_SLOTS 16`。
- **碼 → 知識**：dump 裡的 `type 2` 看不懂 → 回頭查 `enum maple_type` → 再回到文件確認 range_64 和 arange_64 的差別。
- **碼推翻知識**：LWN 說不支援 32 位元 → 標頭檔有 32 位元分支 → 這條知識作廢。

每一圈都讓模型更精確。停止的時機是：**你的問題能回答了**。如果你的目標是「讀懂 `find_vma()` 怎麼用 maple tree」，大概轉兩三圈就夠；如果目標是「審 maple tree 的 patch」，那要轉幾十圈，而且要把 `.c` 讀透。

## 對比與取捨

| 來源 | 新鮮度 | 具體度 | 給你什麼 | 主要風險 |
|---|---|---|---|---|
| MAINTAINERS | 隨版本 | 高 | 邊界、負責人、mailing list | 只是索引，不給概念 |
| Documentation/ | 隨版本（但可能落後） | 高 | 官方概念與 API | 覆蓋率不均；也會過時 |
| LWN | 按日期 | 中高 | 消化過的設計討論與爭議 | 寫的是當時；要對照版本 |
| cover letter | 合入當時 | 最高（為什麼） | 作者原始動機、取捨 | 合入後的修改不在裡面 |
| 會議演講 | 演講當年 | 中 | 口語化解釋、失敗嘗試 | 常是未合入時的計畫 |
| 書 | 出版當年 | 低（概念）| 穩定的基礎 | 實作細節多已過時 |
| 論文 | 研究當年 | 最抽象 | 演算法與理論 | 和 kernel 實作常有落差 |

## 踩雷集錦

1. **「先把背景知識學完再開始讀碼」**：這會讓你在書裡待三週。正確做法是學到「能提出具體問題」就回去讀碼，用讀碼碰到的問題決定下一步補什麼。
2. **「LWN 寫的就是現在的行為」**：LWN 寫的是**文章日期當時**的狀態，而且常報導還沒合入的 RFC。本章實例中，2021 年文章說的「不支援 32 位元」在 v6.18 已不成立。每次引用前，先做「日期 → 版本 → rg 驗證」三步。
3. **「書越經典越可靠」**：經典書的**概念**可靠，**實作細節**不可靠。《Understanding the Linux Kernel》講的 VMA 結構早已被 maple tree 取代。把書當成「舊設計的說明」來讀，反而能幫你理解新設計解決了什麼。
4. **「cover letter 就是最終設計」**：cover letter 是 v1 或某一版的說明；series 常改到 v10 以上，合入後還有大量修補。commit message 裡的 cover letter 是合入那一版的，但之後的演變要看 `git log`。
5. **「論文提出的方法就是 kernel 的做法」**：maple tree 的 cover letter 明說「our design choice of using ranges made that paper unusable for us」。論文給你問題空間與方向，不是實作藍圖。
6. **「數字對就代表理解對」**：「分支因子 10/16」是對的，但只在 allocation-range 模式下精確描述內部節點。讀到數字時，要問它的前提條件。

## 進階：再往深一層

- **訂閱子系統的 mailing list**：`maple-tree@lists.infradead.org` 在 lore.kernel.org 有封存，可以用 lei 或 RSS 追蹤。長期追一個 list 是從「會讀」到「懂這個子系統的走向」最直接的路。
- **看 Rust 綁定學 API 語意**：`rust/kernel/maple_tree.rs` 為了提供安全的 Rust 介面，必須把 C API 的前提條件寫成型別約束或註解。Rust 綁定常常比 C 文件更明確地寫出「這個指標什麼時候有效」。
- **追蹤點（tracepoint）**：`include/trace/events/maple_tree.h` 定義了 maple tree 的 tracepoint。在 QEMU 裡用 ftrace 打開它們（[Ch 51](./51-dynamic-tracing.md)），能看到真實的 VMA 操作怎麼呼叫樹的函式。
- **同樣的流程套用到 io_uring**：io_uring 是另一個「Documentation/ 裡幾乎沒有專門文件」的子系統，正好練習在 ① 不夠時往 ②③④ 走。見下方動手練習。

## 動手練習

1. **30 分鐘挑戰（io_uring）**：計時 30 分鐘，對 `io_uring/` 走一遍本章流程。起點：`sed -n '/^IO_URING$/,/^$/p' MAINTAINERS`；`rg -il io_uring Documentation | head`（你會發現沒有一份專門的總覽文件）；LWN 的「Ringing in a new asynchronous I/O API」（Jonathan Corbet，2019-01-15，lwn.net/Articles/776703）；Jens Axboe 的〈Efficient IO with io_uring〉（2019）；Axboe 在 Kernel Recipes 2019 的演講「Faster IO through io_uring」。產出：一張概念圖、8 條術語表、3 個待解問題。
2. **過時檢驗**：讀 LWN 的「Ringing in a new asynchronous I/O API」，挑出三個具體主張（函式名、syscall 名、限制條件），用 `git show v5.1:<path>` 和 v6.18 的 tree 逐一對照，標記哪些已過時。
3. **找被取代的東西**：在 v6.0 的 tree 裡找 `struct vm_area_struct` 的 `vm_rb`、`vm_next` 欄位（`git show v6.0:include/linux/mm_types.h | grep -n 'vm_rb\|vm_next'`），再在 v6.18 確認它們不見了。用一段話說明 maple tree 取代了什麼。
4. **自己的術語表**：挑一個你目前工作中或興趣上碰到的子系統，照本章四欄格式寫至少 6 條術語。

## 本章重點整理

- 領域鴻溝是唯一不能靠「更認真讀碼」解決的鴻溝，要從外部補知識。
- 查找順序：MAINTAINERS 與 Documentation/ → LWN Kernel Index → cover letter → 會議演講 → 書 → 論文。由近到遠，夠了就停。
- 二手來源要對照版本：日期 → 版本 → 狀態 → 名稱還在不在 → 主張還成不成立。動機老化得慢，實作細節老化得快。
- 30 分鐘建模的產出是：邊界、三句話的「為什麼」、資料結構草圖、術語表、待解問題。
- 領域知識和讀碼交替進行，每一圈都修正模型；能回答你的問題時就停。

## 自我檢核

- [ ] 不看筆記，說出六層來源的順序，以及每一層「最適合回答什麼問題」。
- [ ] 如果有人拿一篇 2019 年的 LWN 文章跟你說「io_uring 不支援 X」，你會用哪幾個指令驗證？
- [ ] 解釋為什麼 maple tree 的 cover letter 引用了一篇 ASPLOS 論文，卻又說那篇論文「unusable for us」。這告訴你論文和 kernel 實作的什麼關係？
- [ ] 不翻回去，畫出 maple tree 內部節點的結構（pivot、slot 的數量關係），並說明 pivot 和一般 B-tree key 的差別。
- [ ] 想一個你曾經卡在領域鴻溝的經驗。用本章的流程重來一次，你會先打開什麼？

## 延伸閱讀

### 官方文件

- **[Maple Tree](https://docs.kernel.org/core-api/maple_tree.html)**（tree 內：`Documentation/core-api/maple_tree.rst`）
  - **讀哪裡**：Overview、Normal API、Advanced API 三節；Locking 節在你讀 RCU 相關程式碼時再回來看。
  - **能學到什麼**：normal API 與 advanced API 的分工、保留值的規則、鎖的選擇。
  - **前提**：知道 B-tree 是什麼；RCU 的基本概念（[Ch 31](./31-reading-rcu-code.md)）。

### LWN 文章

- **[Introducing maple trees](https://lwn.net/Articles/845507/)** — Marta Rybczyńska（LWN，2021-02-12）
  - **這篇說什麼**：maple tree RFC 階段的設計與動機，以及它要取代的 VMA 結構。
  - **讀哪裡**：整篇。讀的時候對照本章「判斷是否過時」一節，標出已過時的部分。
  - **為什麼值得讀**：動機的部分到現在仍然準確，是理解「為什麼要有 maple tree」最好的入門。
- **[The next steps for the maple tree](https://lwn.net/Articles/974860/)**（LWN，2024-05-27）
  - **這篇說什麼**：合入一年半後的現況與後續計畫。
  - **讀哪裡**：整篇，和 2021 年那篇對照著讀，觀察設計怎麼演變。
  - **為什麼值得讀**：示範了「同主題新舊文章對照」的價值。
- **[LWN Kernel Index](https://lwn.net/Kernel/Index/)**
  - **讀哪裡**：用瀏覽器搜尋你要的子系統名稱，條目按時間排序。
  - **為什麼值得讀**：這是本章第 ② 層的入口；任何陌生子系統都從這裡找第一篇文章。

### 會議演講

- **The Maple Tree** — Liam R. Howlett（Linux Plumbers Conference 2022，Kernel Summit track）
  - **在哪裡**：lpc.events 的 LPC 2022 議程（event 16），有投影片 PDF。
  - **讀哪裡**：投影片前半的結構說明，以及 MM 之外的使用者。
  - **前提**：讀過本章 30 分鐘流程。

### 論文

- **Scalable Address Spaces Using RCU Balanced Trees** — Austin T. Clements, M. Frans Kaashoek, Nickolai Zeldovich（ASPLOS 2012，DOI 10.1145/2150976.2150998）
  - **核心貢獻**：用 RCU 讓 soft page fault 能和修改同一位址空間的操作並行，並提出 RCU 平衡二元樹 BONSAI 存放記憶體映射；在 Linux 2.6.37 上的 80 核實驗得到 1.7–3.4 倍提升。
  - **讀哪裡**：Introduction 與設計章節；可以跳過證明細節。
  - **和本章的關聯**：maple tree cover letter 直接引用了它（PDF 位址在 `54a611b60590` 的 commit message 裡）；讀它能理解「為什麼要 RCU 化 VMA 查詢」，同時也能看到 kernel 實作為什麼沒有照搬。

### 書籍

- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman（Prentice Hall, 2004）
  - **這本書的定位**：mm 子系統的經典導讀，作者後來成為 mm 的長期貢獻者。
  - **讀哪幾章**：Process Address Space 那章，它描述的 VMA 管理方式正是 maple tree 取代的對象。版本是 2.4／2.6 早期，請當作「舊設計說明」來讀。

領域知識讓你知道程式碼在解決什麼問題；但要在幾千萬行裡找到那段程式碼，你需要搜尋工具。Part 8 從最基本、也最常被用錯的 grep 開始。

→ [Ch 47 grep、ripgrep 與 regex 搜尋策略](./47-grep-and-regex-search.md)
