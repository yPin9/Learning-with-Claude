# Ch 20 — 讀一個 git 子命令的完整實作

> **目標**：把 Ch 18 的命令分派和 Ch 19 的 object store 接起來。挑一個中等複雜度的子命令 `git cat-file`，從 `builtin/cat-file.c` 的 `cmd_cat_file` entry 一路追到 Ch 19 讀過的 `repo_read_object_file`，示範一套「怎麼從一個 CLI 命令追進實作核心」的可複製流程。

> **目標codebase**：git v2.47.1（commit `92999a4`）

## 為什麼需要這個？

前兩章我們分別建了兩塊地圖：命令怎麼分派（Ch 18）、object 怎麼存取（Ch 19）。但真實的讀碼任務不是「讀懂一個 struct」，而是「老闆說 `git cat-file -p` 有個 bug，你去看它怎麼實作的」——你得從一個**具體命令**出發，穿過門面、穿過參數解析、穿過幾層 indirection，抵達真正幹活的那幾行。

這正是 `reading_code` 整套 SOP 的縮影：找 entry（Ch 6）→ 追 control flow（Ch 9）→ 順 data flow（Ch 8）→ 收斂到核心（Ch 11）。這一章我們不講抽象方法，直接拿 `git cat-file` 走一遍完整流程，你會得到一個模板：**下次面對任何 git 命令，照這個節奏追就對了。**

為什麼挑 `cat-file`？它夠簡單（不像 `merge`/`rebase` 纏繞互動邏輯）又夠真實（直接讀 object store，串起前兩章）。而且它是 plumbing，本質就是「把 object store 的內容吐出來」，追它等於把 Ch 19 的讀取路徑從另一頭再走一次。

## 先建立直覺

我們要追的目標命令：`git cat-file -p HEAD`（pretty-print HEAD 指向的 object）。先把預期的路徑草圖畫出來——**讀碼前先有假設**（`reading_code` Ch 10）：

```
   git cat-file -p HEAD
        │
        ▼  Ch 18 的分派鏈
   cmd_main → handle_builtin → get_builtin → run_builtin → p->fn
        │
        ▼  p->fn == cmd_cat_file
   cmd_cat_file (builtin/cat-file.c)
        │  parse_options 解析 -p / -t / -s / --batch ...
        │  分流：batch 模式？→ batch_objects()
        │       單一 object？→ cat_one_file()   ← 我們追這條
        ▼
   cat_one_file()
        │  ① get_oid_with_context：把 "HEAD" 解析成一個 oid
        │  ② switch(opt)：-p 走 case 'p'
        │     ├─ blob → stream_blob（串流吐出，不全載入記憶體）
        │     ├─ tree → 轉呼叫 cmd_ls_tree
        │     └─ commit/tag → repo_read_object_file（Ch 19！）
        ▼
   write_or_die(1, buf, size)   ← 印到 stdout
```

這張草圖是假設，不是結論。追的過程就是驗證它、修正它。真實 code 常常在你以為「就這樣」的地方拐個彎——那些拐彎處就是這一章最有價值的部分。

## 第一步：從 `commands[]` 找到 entry

按 Ch 18 學的，追任何命令第一動作固定：在 `git.c` 查它對應哪個函式。

```bash
$ rg '"cat-file"' git.c
511:	{ "cat-file", cmd_cat_file, RUN_SETUP },
```

一行就定位：`cat-file` → `cmd_cat_file`，flag 是 `RUN_SETUP`（表示這命令要先進 git repo 目錄才跑，因為它要讀 `.git/objects`）。函式在 `builtin/cat-file.c`。這就是 entry point。

**這一步永遠這樣做。** 別在 `builtin/` 目錄用檔名猜，也別直接搜 `cmd_cat_file`（有時命令名和函式名對不上，或有 alias）。從 `commands[]` 這張權威表反查，才不會追錯命令。

## 第二步：讀 entry——先跳過參數解析的噪音

打開 `cmd_cat_file`，第一眼你會看到一大片 `struct option options[]`：

```c
// builtin/cat-file.c:926 (v2.47.1)
int cmd_cat_file(int argc, const char **argv, const char *prefix,
		 struct repository *repo UNUSED)
{
	int opt = 0;
	...
	const struct option options[] = {
		OPT_CMDMODE('e', NULL, &opt, N_("check if <object> exists"), 'e'),
		OPT_CMDMODE('p', NULL, &opt, N_("pretty-print <object> content"), 'p'),
		OPT_CMDMODE('t', NULL, &opt, N_("show object type ..."), 't'),
		OPT_CMDMODE('s', NULL, &opt, N_("show object size"), 's'),
		...
	};
```

**這是讀命令 entry 的第一個技巧：參數解析（`parse_options` + `options[]`）是噪音，先掃過去、別逐行讀。** 你只需要抓兩件事：(1) 這命令有哪些模式（這裡 `-e/-p/-t/-s` 用 `OPT_CMDMODE` 把選到的 flag 塞進同一個 `opt` 變數），(2) 解析完之後控制流往哪分。git 用 `OPT_CMDMODE` 這個巧妙的巨集——四個互斥選項共用一個 `opt` int，選到 `-p` 就 `opt = 'p'`。記住這個 `opt` 變數，它是後面 `switch` 的分流依據。

參數解析後，`cmd_cat_file` 的核心分流只有兩條路：

```c
// builtin/cat-file.c (v2.47.1) —— 節錄尾段
	if (batch.enabled) {
		...
		return batch_objects(&batch);      // ── 批次模式（--batch）
	}
	...
	return cat_one_file(opt, exp_type, obj_name, unknown_type);  // ── 單一 object
```

batch 模式（`git cat-file --batch`，一次處理 stdin 餵進來的一堆 oid）是另一條路，較複雜、我們不追。單一 object 的 `-p HEAD` 走 `cat_one_file`。**這就是收斂**（`reading_code` Ch 11）：一個看起來一百多行的 entry，真正跟我們目標相關的只有最後這一行呼叫。其餘（參數解析、batch 分支）在這次任務裡是雜訊，果斷跳過。

## 第三步：追進核心 `cat_one_file`

`cat_one_file` 才是幹活的地方。它的骨架：

```c
// builtin/cat-file.c:97 (v2.47.1)
static int cat_one_file(int opt, const char *exp_type, const char *obj_name,
			int unknown_type)
{
	struct object_id oid;
	enum object_type type;
	char *buf;
	unsigned long size;
	...
	// ① 把使用者給的名字（"HEAD"）解析成一個具體 oid
	if (get_oid_with_context(the_repository, obj_name, get_oid_flags, &oid,
				 &obj_context))
		die("Not a valid object name %s", obj_name);
	...
	// ② 按 opt 分流做事
	switch (opt) {
	case 't': ...      // 印型別
	case 's': ...      // 印大小
	case 'e': ...      // 檢查存在
	case 'p':          // ← 我們追這個：pretty print
		...
	}
	...
	write_or_die(1, buf, size);   // ③ 把內容印到 stdout
}
```

三段對得上我們的草圖。逐段追。

### ① 名字 → oid：`get_oid_with_context`

使用者打的是 `HEAD`，不是 40 字元的 oid。git 得先把 `"HEAD"`（或 `main`、`v1.0`、`HEAD~3`、`abc123`…這些「revision 表示法」）解析成一個具體 oid：

```c
// builtin/cat-file.c:120 (v2.47.1)
	if (get_oid_with_context(the_repository, obj_name, get_oid_flags, &oid,
				 &obj_context))
		die("Not a valid object name %s", obj_name);
```

`get_oid_with_context` 在 `object-name.c`，它是個 inline 包裝，真正幹活的是 `get_oid_with_context_1`：

```c
// object-name.c:2165 (v2.47.1)
	return get_oid_with_context_1(repo, str, flags, NULL, oid, oc);
```

**這裡有個讀碼判斷：要不要往下追 `get_oid_with_context_1`？** 它是 git 最複雜的函式之一——要處理 `HEAD~3`、`main^2`、`v1.0^{tree}`、`:path/to/file`、縮寫 oid 等一大堆語法。這次任務我們只要知道「它把名字變成 oid」，不需要讀懂全部語法解析。**這是收斂的紀律**：對當前任務不關鍵的深水區，記一句「這裡把 revision 字串解析成 oid，細節先不看」，繼續往主線走。真的要改 revision 解析時再回來讀。（想追的話，`HEAD` 這種最簡單的名字最後會落到讀 `.git/HEAD` 那個 ref、拿到它指向的 oid。）

追完 ①：`oid` 現在裝著 HEAD commit 的 oid。

### ② `-p` 的分流：blob / tree / commit 各走各的路

`case 'p'` 是這章的重心。讀清楚它怎麼按 object 型別分三條路：

```c
// builtin/cat-file.c:185 (v2.47.1) —— case 'p' 節錄
	case 'p':
		type = oid_object_info(the_repository, &oid, NULL);   // 先問型別
		if (type < 0)
			die("Not a valid object name %s", obj_name);

		/* custom pretty-print here */
		if (type == OBJ_TREE) {                    // ── tree：轉呼叫 ls-tree
			const char *ls_args[3] = { NULL };
			ls_args[0] = "ls-tree";
			ls_args[1] = obj_name;
			ret = cmd_ls_tree(2, ls_args, NULL, the_repository);
			goto cleanup;
		}

		if (type == OBJ_BLOB) {                    // ── blob：串流吐出
			ret = stream_blob(&oid);
			goto cleanup;
		}
		buf = repo_read_object_file(the_repository, &oid, &type, &size);  // ── commit/tag
		if (!buf)
			die("Cannot read object %s", obj_name);
		...
		break;
```

**這裡藏著三個第一次讀會意外的拐點，全是好教材：**

**拐點 1：tree 的 pretty-print 竟然是「呼叫另一個命令」。** `git cat-file -p <tree>` 印出來的目錄列表，其實是 `cat_one_file` 直接呼叫 `cmd_ls_tree(...)`——就是 `git ls-tree` 那個命令的函式。git 內部命令可以互相呼叫（都是普通 C 函式）。如果你只讀 `cat-file.c`、以為 tree 的印法在這裡，會找半天找不到——**它把工作外包給 ls-tree 了**。這是 `reading_code` Ch 23「indirection」的變體：控制流跳到另一個 `builtin/` 檔去了。

**拐點 2：blob 不用 `repo_read_object_file`，用 `stream_blob`。** commit 和 tag 用 `repo_read_object_file`（Ch 19 讀過的，一次把整個 object 載入記憶體）；blob 卻走 `stream_blob`：

```c
// builtin/cat-file.c:90 (v2.47.1)
static int stream_blob(const struct object_id *oid)
{
	if (stream_blob_to_fd(1, oid, NULL, 0))
		die("unable to stream %s to stdout", oid_to_hex(oid));
	return 0;
}
```

為什麼區別對待？**blob 可能非常大**（一個 500MB 的檔案就是一個 500MB 的 blob）。`repo_read_object_file` 會把整個 object malloc 進記憶體再印，遇到大 blob 會吃爆記憶體。`stream_blob` 邊解壓邊往 fd 1（stdout）寫，記憶體用量固定。commit/tag 一定很小（幾百 bytes），才敢整個載入。**同一個「印出 object」的需求，因為 blob 可能巨大而走了不同實作**——這種「因資料大小而分路」的設計，你讀完不查根本不會知道，必須真的追到 `stream_blob` vs `repo_read_object_file` 的分岔才發現。

**拐點 3：commit/tag 這條才接回 Ch 19。** 只有 commit 和 tag（一定很小）走 `repo_read_object_file`——這正是我們 Ch 19 讀穿的那個函式！它內部 `oid_object_info_extended` → 先 `find_pack_entry`（pack）→ 否則 `loose_object_info`（loose + zlib inflate）。**兩章在這裡合流了。** 你追一個命令，最後落到你已經讀懂的儲存層函式——這就是「地圖拼起來」的時刻。

### ③ 印出去：`write_or_die`

commit/tag 這條路拿到 `buf` 後，走到 `cat_one_file` 尾段：

```c
// builtin/cat-file.c:265 (v2.47.1)
	if (!buf)
		die("git cat-file %s: bad file", obj_name);
	write_or_die(1, buf, size);   // fd 1 = stdout
	ret = 0;
cleanup:
	free(buf);
	object_context_release(&obj_context);
	return ret;
```

`write_or_die(1, buf, size)` 把 object 的原始內容寫到 stdout。這就是你 `git cat-file -p HEAD` 螢幕上看到的 `tree ...\nauthor ...\n\nfirst commit`。追完了。

## 完整 call chain 一覽

把整條路徑收成一張圖（每個節點都標了真實檔案:行號，v2.47.1）：

```
git cat-file -p HEAD
   │
   ▼  git.c:511  commands[] = { "cat-file", cmd_cat_file, RUN_SETUP }
cmd_cat_file            builtin/cat-file.c:926
   │  parse_options（跳過噪音）→ 非 batch → 
   ▼
cat_one_file            builtin/cat-file.c:97
   │
   ├─① get_oid_with_context   object-name.c:2165  ── "HEAD" → oid
   │
   └─② switch(opt='p'):
        │  oid_object_info 問型別
        ├─ OBJ_TREE  → cmd_ls_tree(...)            builtin/ls-tree.c   （外包）
        ├─ OBJ_BLOB  → stream_blob → stream_blob_to_fd  （串流，防大檔爆記憶體）
        └─ OBJ_COMMIT/TAG → repo_read_object_file  object-file.c:1875  ← Ch 19！
                              └─ oid_object_info_extended
                                   ├─ find_pack_entry（pack 優先）
                                   └─ loose_object_info（loose + zlib inflate）
   │
   ▼③
write_or_die(1, buf, size)   → stdout
```

**這張圖就是「讀一個命令」的成品。** 它不是 code dump，是一張把命令、參數解析、型別分流、儲存層合流全串起來的地圖。有了它，這個命令你就讀懂了——你能回答「blob 怎麼印」「tree 為什麼在別的檔」「commit 走哪條讀取路徑」。

## 用 debugger 驗證這條路徑（可選但強烈建議）

讀出來的 call chain 是假設，用 gdb 驗證它真的這樣跑（`reading_code` Ch 18「debugger-driven reading」）。build git 後：

```bash
$ make -j    # 需要 zlib/openssl-dev；產出 ./git
$ gdb --args ./git cat-file -p HEAD
(gdb) break cat_one_file
(gdb) break repo_read_object_file
(gdb) run
# 命中 cat_one_file → continue → 命中 repo_read_object_file，證明 commit 走這條
(gdb) bt      # backtrace 印出真實呼叫堆疊，和你畫的圖對照
```

如果你在一個 blob 上跑（`git cat-file -p <blob-oid>`），`repo_read_object_file` 的中斷點**不會命中**——因為 blob 走 `stream_blob`。這個「中斷點沒命中」本身就是證據，證明你讀對了分流。**讀 + 跑 + 驗證三管齊下**，比純讀踏實得多。

（本章的 call chain 由讀 source + `rg`/`sed` 核對得出；gdb 步驟為建議做法，讀者可在 build 起 git 後自行驗證。）

## 我們跳過的那條路：batch 模式（示範「該收斂時怎麼收斂」）

追命令最難的判斷不是「怎麼往下追」，是「什麼時候該停」。我們在第二步果斷跳過了 `batch_objects` 那條分支——現在回頭看一眼它，示範「收斂」不是「不看」，而是「快速確認它不是我這次的目標，記一句就走」。

`git cat-file --batch` 是給程式/GUI 用的：從 stdin 一次餵進上萬個 oid，批次查詢輸出，避免每個 oid 都 fork 一次 `git cat-file`。它的入口：

```c
// builtin/cat-file.c:776 (v2.47.1)
static int batch_objects(struct batch_options *opt)
{
	...
```

掃一眼你會發現它跟 `cat_one_file` 是**完全不同的世界**：它圍繞一個 `struct expand_data`（`:276`）在轉，`batch_one_object`（`:519`）處理單個、`batch_object_write`（`:460`）負責輸出格式、`print_object_or_die`（`:380`）真正吐內容。整套是為了「大量、可自訂格式、高吞吐」設計的。

**這裡的讀碼判斷**：如果你的任務是「`git cat-file -p` 為什麼印錯」，batch 這條路**跟你無關**——它是另一個模式、另一組函式。你花三十秒確認「喔這是批次模式的獨立實作，我的 `-p` 走的是 `cat_one_file`」，然後**記一句、關掉、回主線**。這就是收斂：不是假裝 batch 不存在，是判斷它不在你這次的路徑上、果斷不追。

反過來，如果任務是「`--batch` 吞吐太慢」，那你該追的就是 `batch_objects` 這條、`cat_one_file` 反而無關。**同一個檔案，任務不同、該讀的分支就不同。** 讀碼的第一動作永遠是「先確定我這次的路徑是哪一條」，再沿那條追到底，其餘全部記一句跳過。判斷力（追哪條）比追蹤技巧（怎麼追）更決定你的速度。

## 對比與取捨

| 讀命令的做法 | 好處 | 壞處 / 適用時機 |
|---|---|---|
| 從 `commands[]` 反查 entry | 權威、不追錯命令 | 永遠這樣做，無壞處 |
| 逐行讀 `parse_options`/`options[]` | —— | 幾乎是浪費時間；抓「有哪些模式 + 解析後往哪分」即可 |
| 追進每個被呼叫的函式到底 | 完整 | 會迷路（如追進 `get_oid_with_context_1`）；該收斂就收斂 |
| 只追跟任務相關的那條分支 | 快、聚焦 | 需要判斷力：先有假設（要追哪條）再追 |
| 讀完用 gdb 驗證 | 抓出讀錯的分流 | 需要能 build；但值得 |

核心取捨是**收斂 vs 完整**。讀一個命令不是把它每一行讀懂，是**追出你關心的那一條路徑**，其餘記一句「這裡幹嘛」跳過。`cat_one_file` 有 `-t/-s/-e/-p` 四五條分支，我們只追 `-p` 的 commit 路徑，其餘掃過。這是速度的來源。

## 踩雷集錦

1. **從 `builtin/cat-file.c` 直接開始讀、沒先查 `commands[]`。** 你可能追錯——某些命令的函式名和命令名不一致，或有多個 entry。永遠先 `rg '"命令名"' git.c` 反查權威 entry。
2. **卡在 `parse_options` 和一大片 `options[]` 逐行讀。** 那是宣告命令選項的樣板，不是邏輯。抓「有哪些模式、解析後控制流往哪分」，其餘跳過。新手最常在這裡耗掉一半時間。
3. **以為 `git cat-file -p <tree>` 的印法在 `cat-file.c`。** 錯，它 `goto` 之前直接 `cmd_ls_tree(...)` 外包給 ls-tree 了。控制流跳到另一個 `builtin/` 檔——沒追到那一行你會在 `cat-file.c` 裡找一個不存在的 tree 印法。
4. **以為所有 object 都用 `repo_read_object_file` 讀。** blob 走 `stream_blob`（串流、防大檔爆記憶體），只有 commit/tag 走 `repo_read_object_file`。沒追到這個分岔，你會誤以為 `git cat-file` 一個 5GB 的 blob 會 OOM。
5. **追進 `get_oid_with_context_1` 想讀懂全部 revision 語法然後迷路。** 那函式處理 `HEAD~3^2:path` 等一堆語法，是個獨立深水區。當前任務只要知道「名字 → oid」，記一句跳過，別掉進去。
6. **讀完不驗證就當定論。** 你畫的 call chain 是假設。至少對一個 commit 和一個 blob 各跑一次、用 gdb 確認分流（或觀察 `repo_read_object_file` 中斷點在 blob 上不命中）。讀對沒讀對，跑一下就知道。

## 進階：再往深一層

- **追 `git cat-file --batch` 那條路。** 我們跳過的 batch 模式（`batch_objects`）其實更有意思：它從 stdin 讀 oid、批次查詢、用 `struct expand_data` 快取查詢結果，是為了效能（一次 fork 處理上萬個 oid，git GUI 工具大量用它）。追它能學到「批次查詢 API 怎麼設計」。
- **對照 `git ls-tree` 怎麼印 tree。** 既然 `cat-file -p <tree>` 外包給 `cmd_ls_tree`，那就去讀 `builtin/ls-tree.c`，看它怎麼 parse 一棵 tree object（`tree.c` 的 `parse_tree` / `init_tree_desc` + `tree_entry`）。這是 Ch 18 說「檔名住在 tree 裡」的實作證據。
- **`stream_blob_to_fd` 的串流機制。** 它怎麼做到不把整個 blob 載入記憶體？追進去你會看到它從 pack 或 loose 邊解壓（zlib inflate 的 streaming API）邊寫 fd。這是處理「object 可能超大」的通用 pattern，對照你在 `observability_tools`/`networking` 課看過的串流拷貝。

## 本章重點整理

- **讀一個命令的固定流程**：① `rg '"命令名"' git.c` 從 `commands[]` 反查 entry 函式 → ② 讀 entry，跳過 `parse_options` 噪音，抓「有哪些模式 + 解析後往哪分」→ ③ 追進核心函式的目標分支 → ④ 一路追到儲存層/核心操作 → ⑤ gdb 驗證分流。
- `git cat-file -p HEAD` 的真實 call chain：`cmd_cat_file` → `cat_one_file` → `get_oid_with_context`（名字→oid）→ `switch(opt='p')` → 依型別分三路（tree 外包 `cmd_ls_tree`／blob 走 `stream_blob`／commit·tag 走 `repo_read_object_file`）→ `write_or_die` 印 stdout。
- 三個第一次讀會意外的拐點：**tree 外包給 ls-tree**、**blob 串流防大檔爆記憶體**、**只有 commit/tag 接回 Ch 19 的 `repo_read_object_file`**。這些拐點不追進去看不出來——這是「讀」勝過「猜」的地方。
- 收斂是速度來源：只追跟任務相關的那條分支（`-p` 的 commit 路徑），其餘（batch、`-t/-s/-e`、`get_oid_with_context_1` 深水區）記一句跳過。

## 自我檢核

- [ ] 給我一個沒讀過的 git 命令，我能說出第一步該做什麼（`rg '"..."' git.c` 反查 entry）。
- [ ] 我能不看教材畫出 `git cat-file -p HEAD` 從 entry 到 stdout 的 call chain。
- [ ] 我能解釋為什麼 blob 走 `stream_blob` 而 commit/tag 走 `repo_read_object_file`（大小差異、記憶體）。
- [ ] 我能解釋 `git cat-file -p <tree>` 的印法為什麼在 `cat-file.c` 裡找不到（外包給 `cmd_ls_tree`）。
- [ ] 我知道在 `cat_one_file` 裡哪些是該讀的（分流邏輯）、哪些是該跳過的（`options[]`、`get_oid_with_context_1` 內部）。
- [ ] 我能用 gdb 在 `cat_one_file`/`repo_read_object_file` 下中斷點驗證分流，並解釋 blob 上為何不命中後者。

## 延伸閱讀

- **本 clone 的 `builtin/cat-file.c`（`cmd_cat_file`、`cat_one_file`、`stream_blob`）**
  - **讀哪裡**：`cmd_cat_file`（:926）掃參數分流、`cat_one_file`（:97）逐段、`case 'p'`（:185 附近）三路分岔、`stream_blob`（:90）。
  - **學什麼**：親手把本章 call chain 的每一跳 `sed -n` 出來核對，特別確認三個拐點真的存在。
  - **前提**：讀過 Ch 18、Ch 19；已 clone v2.47.1（`92999a4`）。
- **`Documentation/git-cat-file.txt`（本 clone 附帶，官方 man page）**
  - **讀哪裡**：`-p` / `-t` / `-s` / `--batch` 的說明。先讀「這命令對外承諾什麼行為」，再讀實作怎麼兌現，是讀任何命令的好順序。
  - **學什麼**：把「使用者看到的行為」對回「code 走的分支」，養成規格↔實作對照的習慣。
  - **前提**：無。
- **`reading_code` Ch 6（找 entry point）、Ch 11（收斂到你要改的 200 行）、Ch 18（debugger-driven reading）**
  - **讀哪裡**：這三章是本章方法的來源。Ch 6 教怎麼找 entry、Ch 11 教怎麼判斷哪些分支該跳過、Ch 18 教怎麼用 gdb 驗證。
  - **學什麼**：把本章在 git 上的具體操作抽象回可遷移的 SOP，套到任何專案的任何命令。
  - **前提**：無。

你已經能從一個命令追進核心了。下一章我們退一步，把 git 這三章讀到的東西結晶成 pattern 卡片——content addressing、DAG、雙層儲存、command dispatch、delta 壓縮——並連到本課其他 codebase 會再遇到的同類 idiom。

→ [Ch 21 萃取 pattern：content addressing / DAG](./21-git-patterns-extracted.md)
