# 練習 D：讀懂一個 git 子命令

> **目標**：不靠教材帶路，自己用 Ch 20 的流程讀懂一個沒挑過的 git 子命令的完整實作。這次的目標是 `git ls-files`——從 `builtin/ls-files.c` 的 entry 追到它真正在讀什麼、印什麼。限時、外化、驗證。

> **目標codebase**：git v2.47.1（commit `92999a4`）

## 這個練習在練什麼

Ch 20 我帶你走了 `git cat-file` 一遍。現在換你自己走一遍 `git ls-files`——一個你（大概）沒讀過的命令。Ch 20 是「看示範」，這裡是「上場」。刻意選 `ls-files` 是因為它會逼你碰到一塊 Ch 18–20 沒深講的東西：**index（暫存區 / staging area）**。你會發現 `git ls-files` 印的既不是 object store、也不是工作目錄，而是那個介於兩者之間的 `.git/index` 檔——這個「第三個資料結構」是很多人對 git 的認知盲區。

讀完你要能回答：`git ls-files` 從哪讀資料？它讀的東西和 blob/tree 有什麼關係？`git ls-files -s` 印出的那個 oid 是哪來的？

## 任務規格

**主任務**：追出 `git ls-files`（無參數）從 entry 到印出檔名列表的完整 call chain，畫成一張 call graph（每個節點標真實檔案:行號）。

具體要能回答這五個問題：

1. `ls-files` 對應 `commands[]` 裡哪個函式？在哪個檔？
2. 這個命令的資料**來源**是什麼？它在 entry 裡呼叫哪個函式把資料載進來？（提示：不是 `repo_read_object_file`）
3. 載進來的資料存在哪個 struct、什麼欄位？它是一個怎樣的資料結構（陣列？樹？）？
4. 從那個 struct 到「印出一行檔名」，中間經過哪個/哪些函式？
5. `git ls-files -s` 多印的 `<mode> <oid> <stage>` 是從哪個欄位來的？這個 oid 和 Ch 18 的 blob oid 是同一個東西嗎？

**限時**：60 分鐘。
- 0–10 分：偵察（找 entry、看命令有哪些選項、猜資料來源）。
- 10–40 分：追主線 call chain，邊追邊畫圖。
- 40–55 分：回答五個問題，補完圖上缺的節點。
- 55–60 分：（若已 build git）用 gdb 驗證你的 call chain。

**外化要求**：全程開一個檔案手寫。每追一跳就記一行「`函式A`（檔:行）呼叫 `函式B`，因為……」。不准只在腦中追。（`reading_code` Ch 35「外化理解」——腦中讀不算讀。）

## 先準備一個有內容的 repo

`ls-files` 讀 index，所以你需要一個 `git add` 過東西的 repo：

```bash
$ mkdir /tmp/lsdemo && cd /tmp/lsdemo && git init -q
$ echo "hello git" > file.txt
$ echo "hello 2"   > second.txt
$ git add file.txt second.txt        # 進 index，但先別 commit
$ git ls-files                        # 你要追的命令
file.txt
second.txt
$ git ls-files -s                     # 加碼版本，多印 mode/oid/stage
100644 8d0e41234f24b6da002d962a26c2495ea16a425f 0	file.txt
100644 c9835dfd7d3c3d547df9ed94479f556fcaf5615d 0	second.txt
```

（以上為 v2.47.1 真跑輸出。注意 `git add` 後**還沒 commit**，`ls-files` 就已經印得出來——這本身就是線索：它讀的不是 commit/tree，是更前面的東西。）

## 開場：先花 5 分鐘偵察，別急著追

上場前先用 `reading_code` Ch 5 的偵察動作暖身。你追的是 `builtin/ls-files.c`，先對它做三件事，建立「這命令大概多大、長怎樣」的直覺：

```bash
$ cd ~/cbcs/git
$ wc -l builtin/ls-files.c              # 這檔多大？（v2.47.1 是 765 行）
$ rg -n "^static |^int cmd_ls_files" builtin/ls-files.c | head -20   # 有哪些函式？
```

掃一眼函式清單，你會看到 `cmd_ls_files`（entry）、`show_files`、`show_ce`、`show_other_files`、`show_killed_files`…。**光看名字就能猜出結構**：一個 entry、一個 `show_files` 主迴圈、幾個 `show_xxx` 針對不同模式（cached / others / killed）。這就是偵察的價值——還沒讀任何一行邏輯，你已經有了地圖的骨架，知道 `git ls-files`（無參數）大概走 `cmd_ls_files → show_files → show_ce` 這條。**帶著這個假設去追**，比盲讀快得多（`reading_code` Ch 10「假設驅動」）。

一個常見的偵察誤判：你可能以為 765 行「不大，全讀」。但其中一半是處理 `-o`（others）、`-i`（ignored）、submodule、sparse index 等邊角模式的 code。**無參數的 `ls-files` 主線只碰其中一小條路**。收斂的紀律從偵察就開始：先鎖定「無參數走哪條」，其餘模式的函式（`show_other_files` 等）這次連看都不看。

## 如果你卡住了（5 個方向提示）

按順序看，每個提示只推一步，別一次看完。

1. **找 entry 的方法你已經會了。** 用 Ch 18/Ch 20 的固定第一動作，在 `git.c` 反查 `ls-files` 對應哪個函式。別在 `builtin/` 目錄用檔名猜。

2. **資料來源不在 object store。** 在 `cmd_ls_files`（`builtin/ls-files.c`）裡找一個名字含 `index` 的函式呼叫——它在 `parse_options` 之前就被呼叫了（因為讀 index 是這命令的前提）。`rg 'read_index' builtin/ls-files.c`。

3. **資料載進哪裡？** 那個 read index 的函式把資料填進 `the_repository->index`。去 `read-cache-ll.h` 找 `struct index_state`，看它有沒有一個「一堆 entry 的陣列」和一個「entry 數量」的欄位。`rg 'cache_nr|cache;' read-cache-ll.h`。

4. **印出來的迴圈在哪？** entry 讀完 index 後會呼叫一個 `show_files`（`builtin/ls-files.c`）。在裡面找一個 `for` 迴圈，它從 0 跑到某個 `_nr`，每圈拿出一個 entry。看它每圈呼叫什麼函式把一個 entry 印出去。

5. **`-s` 的 oid 從哪來？** 每個 index entry 是一個 `struct cache_entry`（`read-cache-ll.h`）。看它有沒有一個 `struct object_id oid` 欄位。`git ls-files -s` 印的 oid 就是這個欄位——想想它是什麼時候被填進去的（`git add` 時）。

## 分段步驟（追的時候照這個節奏）

**Step 1（找 entry）**：`rg '"ls-files"' git.c`，記下函式名和所在檔。打開那個檔，找到 `cmd_ls_files`。

**Step 2（看命令形狀，別逐行讀選項）**：掃一下 `cmd_ls_files` 裡的 `options[]`，抓「這命令有哪些模式」（`-c/--cached`、`-s/--stage`、`-o/--others`、`-m/--modified`…）。**不要逐行讀選項宣告**，那是噪音（Ch 20 的教訓）。

**Step 3（找資料來源）**：在 `cmd_ls_files` 找 `parse_options` **之前**的那個 index 讀取呼叫。記下它的名字。這回答問題 2。

**Step 4（找資料結構）**：跳到那個 struct（`the_repository->index` 的型別），找「entry 陣列 + 數量」欄位。這回答問題 3。

**Step 5（追印出的迴圈）**：找 `cmd_ls_files` 尾段呼叫的 `show_files`，進去找那個 `for (i = 0; i < ...->cache_nr; i++)` 迴圈，看它呼叫什麼把一個 entry 印出來（一個名字含 `ce` 的函式）。這回答問題 4。

**Step 6（追 `-s` 的 oid）**：進到 Step 5 找到的那個印出函式，找 `show_stage` 為真時印 oid 的那段，確認 oid 來自 `ce->oid`。這回答問題 5。

**Step 7（收斂成圖）**：把 Step 1–6 串成一張 call graph，標行號。

**Step 8（驗證，可選）**：build git 後 `gdb --args ./git ls-files`，在你圖上的關鍵函式下中斷點，`run` + `bt` 對照。

### 每一步你應該看到什麼（自我校準）

追的過程中拿這張表對照，確認你沒追歪：

| Step | 你應該找到 | 如果找不到，代表 |
|---|---|---|
| 1 | `git.c` 裡一行 `{ "ls-files", cmd_ls_files, RUN_SETUP }` | 你可能 `rg` 錯字串，注意是 `"ls-files"` 帶引號 |
| 3 | `cmd_ls_files` 裡 `parse_options` **之前**有個 `repo_read_index(the_repository)` | 你可能只看了選項宣告，往下看到函式邏輯開始的地方 |
| 4 | `struct index_state` 有 `struct cache_entry **cache` 和 `unsigned int cache_nr` | 你可能跳錯 struct，確認是 `the_repository->index` 的型別 |
| 5 | `show_files` 裡 `for (i = 0; i < ...->cache_nr; i++)` 迴圈呼叫 `show_ce` | 你可能被前面 `if (show_others...)` 那段帶偏（見下方「常追錯」） |
| 6 | `show_ce` 裡 `show_stage` 為真時 `printf(...ce->oid...)` | 你可能在讀 `-c` 的路徑，找 `-s`（`show_stage`）那個分支 |

如果每一步都對上，你的圖就是對的。對不上的那一步，回去看對應的「如果找不到」欄，多半是追歪了一個小地方。

---

<details>
<summary>參考解答（追完再看——先自己追滿 60 分鐘）</summary>

### 完整 call chain

```
git ls-files
   │
   ▼  git.c  commands[] = { "ls-files", cmd_ls_files, RUN_SETUP }
cmd_ls_files                 builtin/ls-files.c:564
   │
   ├─① repo_read_index(the_repository)   builtin/ls-files.c:655
   │        └─ 把 .git/index 讀進 the_repository->index
   │           （型別 struct index_state，read-cache-ll.h）
   │
   ├─  parse_options(...)               builtin/ls-files.c:658  ← 選項解析，跳過
   │
   └─② show_files(the_repository, &dir) builtin/ls-files.c:751
          │
          └─ for (i = 0; i < repo->index->cache_nr; i++)   builtin/ls-files.c:415
                │   const struct cache_entry *ce = repo->index->cache[i];
                ▼
             show_ce(repo, dir, ce, fullname.buf, ...)  builtin/ls-files.c:429
                │
                ├─ show_stage 為真（-s）：                builtin/ls-files.c:337
                │     printf("%s%06o %s %d\t", tag, ce->ce_mode,
                │            repo_find_unique_abbrev(repo, &ce->oid, abbrev),
                │            ce_stage(ce));           ← -s 的 mode/oid/stage 在這
                │
                └─ write_name(fullname);              builtin/ls-files.c:345
                      └─ 印出檔名到 stdout
```

### 五個問題的答案

**Q1：對應哪個函式？**
`git.c` 的 `commands[]` 裡 `{ "ls-files", cmd_ls_files, RUN_SETUP }`，函式在 `builtin/ls-files.c:564`。

**Q2：資料來源？**
不是 object store。是 **index（`.git/index`）**。`cmd_ls_files` 在 `parse_options` **之前**就呼叫 `repo_read_index(the_repository)`（`builtin/ls-files.c:655`）——因為讀 index 是這命令的前提。這就是關鍵盲區：`git ls-files` 讀的是**暫存區**，不是 commit、不是工作目錄。這也解釋了為什麼你 `git add` 後**還沒 commit** 就印得出來——資料在 index 裡等著，還沒被包成 tree/commit。

**Q3：資料結構？**
`repo_read_index` 把資料填進 `the_repository->index`，型別是 `struct index_state`（`read-cache-ll.h`）。關鍵兩個欄位：

```c
// read-cache-ll.h:167 (v2.47.1)
	struct cache_entry **cache;          // entry 指標陣列
	...
	unsigned int cache_nr, cache_alloc, cache_changed;   // cache_nr = entry 數量
```

它是一個 **`cache_entry *` 的陣列**（不是樹），一個 entry 對應一個被追蹤的檔案。`ls-files` 就是把這個陣列印出來。

**Q4：從 struct 到印一行？**
`cmd_ls_files` 尾段呼叫 `show_files`（`builtin/ls-files.c:751`）。`show_files` 裡的迴圈（`:415`）：

```c
// builtin/ls-files.c:415 (v2.47.1)
	for (i = 0; i < repo->index->cache_nr; i++) {
		const struct cache_entry *ce = repo->index->cache[i];
		...
		show_ce(repo, dir, ce, fullname.buf, ...);   // 印一個 entry
	}
```

每圈拿一個 `cache_entry`，交給 `show_ce`（`:311`），`show_ce` 最後 `write_name(fullname)` 印檔名。

**Q5：`-s` 的 oid？**
每個 `cache_entry` 有一個 `struct object_id oid` 欄位：

```c
// read-cache-ll.h (v2.47.1)
struct cache_entry {
	...
	unsigned int ce_mode;
	...
	struct object_id oid;          // ← -s 印的就是這個
	char name[FLEX_ARRAY];
};
```

`show_ce` 在 `show_stage`（`-s`）為真時：

```c
// builtin/ls-files.c:337 (v2.47.1)
		printf("%s%06o %s %d\t",
		       tag,
		       ce->ce_mode,                                     // 100644
		       repo_find_unique_abbrev(repo, &ce->oid, abbrev), // ← ce->oid
		       ce_stage(ce));                                   // 0
```

**這個 oid 和 Ch 18 的 blob oid 是同一個東西。** 對照你的 demo：`git ls-files -s` 印的 `file.txt` 的 oid `8d0e412...` 和 Ch 18 `git hash-object file.txt` / `git cat-file` 看到的 blob oid **完全一致**。因為 `git add file.txt` 時 git 就已經把檔案內容存成一個 blob object、算出 oid、並把 `(檔名, mode, oid)` 記進 index 的一個 cache_entry。所以 index 其實是「一張 檔名 → blob oid 的表」——它是尚未被包成 tree 的、扁平的目錄快照。**`git commit` 做的事，本質就是把 index 這張扁平表轉成一棵 tree、再包一個 commit。** 你剛剛從 `ls-files` 讀出了 index 在 git 資料模型裡的位置。

### 驗證（gdb，若已 build git）

```bash
$ make -j                              # 需 zlib/openssl-dev
$ cd /tmp/lsdemo
$ gdb --args /path/to/git/git ls-files
(gdb) break repo_read_index
(gdb) break show_ce
(gdb) run
# 先命中 repo_read_index（載入 index）
(gdb) continue
# 再命中 show_ce（印第一個 entry），bt 看堆疊
(gdb) bt
# 應該看到 show_ce ← show_files ← cmd_ls_files，和你畫的圖一致
(gdb) print ce->name          # "file.txt"
(gdb) print ce->ce_mode       # 0100644 (八進位)
```

`show_ce` 會被命中兩次（兩個檔），`repo_read_index` 只一次。這證明「讀一次 index、逐 entry 印」的結構。

（本參考解答的 call chain 由讀 v2.47.1 source + `rg`/`sed` 核對；命令輸出為真跑；gdb 步驟為建議驗證法，讀者可自行執行。）

</details>

---

## 追這個命令時，人們常追錯的三個地方

分享幾個真實會踩的坑，追之前先知道，省時間：

1. **在 `show_files` 裡被 `show_others`/`show_killed` 那段帶偏。** `show_files` 開頭有一段：

   ```c
   // builtin/ls-files.c:399 (v2.47.1)
   	if (show_others || show_killed) {
   		...
   		fill_directory(dir, repo->index, &pathspec);   // 掃工作目錄
   ```

   這段處理 `-o`（列出未追蹤檔）——它會去 `readdir` 工作目錄，跟 index 無關。**無參數的 `ls-files` 不走這段**（`show_others`/`show_killed` 都是 0）。第一次讀很容易一頭栽進 `fill_directory` 以為那是主線，其實無參數走的是它後面的 `for (i = 0; i < ...cache_nr; i++)` 迴圈。看到 `if (show_others || show_killed)` 就該想「這是 `-o` 的支線，我無參數不走」，跳過。

2. **以為 `repo_read_index` 會去讀工作目錄。** 不會。它只讀 `.git/index` 那個檔（把磁碟上的 index 格式 parse 成 `struct index_state`）。工作目錄的實際檔案內容它碰都不碰。這正是為什麼 `ls-files` 快、也為什麼它反映的是「你上次 `git add` 的狀態」而非「工作目錄現況」。

3. **想在 `show_ce` 裡找「怎麼算 oid」。** 找不到——oid 早就算好、存在 `ce->oid` 裡了（`git add` 時算的）。`show_ce` 只是**印**這個已存在的 oid，不重算。追寫入側（oid 怎麼被算出來）是挑戰 2 的事（`git hash-object`/`git add` 的路徑），別在 `ls-files` 的讀取路徑裡找。

**這三個坑的共通點**：都是「把支線當主線」或「在讀取路徑找寫入邏輯」。追命令時隨時問自己「我這條路徑（無參數 ls-files）真的會走到這行嗎」，別被檔案裡其他模式的 code 牽著走。

## 測試 / 驗證你讀對了

不看參考解答，先自問自答，再對照：

1. **黑箱驗證**：`git ls-files -s` 印的 `file.txt` oid，和 `git hash-object file.txt` 一樣嗎？（一樣 → 你懂了 index entry 的 oid = blob oid。）
2. **假設驗證**：既然 `ls-files` 讀 index、不讀工作目錄，那你**改了 `file.txt` 但沒 `git add`**，`git ls-files -s` 印的 oid 會變嗎？先猜，再試。（不會變——index 還是舊的；這證明 ls-files 讀 index 不讀工作目錄。）
3. **gdb 驗證**（若能 build）：`show_ce` 中斷點命中的次數 = 你 `git add` 的檔案數嗎？

三個都對得上，這個命令你就讀穿了。

## 你剛剛讀出的東西：index 是 git 的第三個資料結構

這個練習真正的收穫不只是「會追一個命令」，是你親手挖出了 git 資料模型裡最容易被忽略的一層——**index**。把三層擺在一起：

```
   工作目錄（working tree）        index（暫存區）             object store + refs
   你在編輯器裡改的檔案      ──▶  .git/index               ──▶  .git/objects + .git/refs
   實際的檔案內容                 一張「檔名 → blob oid」表        blob/tree/commit（不可變）
                          git add                    git commit
                          （算 blob、記進 index）      （index 轉成 tree、包 commit、挪 branch）
```

- `git ls-files` 讀的是**中間那層（index）**——你剛追出來的 `repo_read_index` → `index_state.cache[]` → `show_ce`。
- Ch 20 的 `git cat-file` 讀的是**右邊那層（object store）**——`repo_read_object_file`。
- 大多數人對 git 的困惑（「add 了又改、commit 進去的是哪版？」）都來自看不見 index 這層。**你現在看得見了**：`ls-files -s` 印的 oid 就是 `git add` 那一刻算出並記進 index 的 blob oid，之後你再改工作目錄的檔、不 `git add`，index 不變，所以 `commit` 進去的是 index 裡的舊版。

`struct index_state`（`read-cache-ll.h`）除了 `cache[]`/`cache_nr`，還有一個 `struct cache_tree *cache_tree` 欄位——那是 index 轉 tree 時的快取，`git commit` 靠它把扁平的 index 高效轉成階層 tree。你不用現在讀它，但知道它在那，你就懂了 index 和 tree 之間的橋在哪。**這一層讀通，git 對你不再有黑盒。**

## 延伸挑戰

做完主任務行有餘力：

- **挑戰 1（同類命令）**：用同一套流程讀 `git rev-parse HEAD`（`builtin/rev-parse.c`）。它把 `HEAD` 這種名字解析成 oid——追到它怎麼讀 `.git/HEAD`、順著 ref 拿到 commit oid。這補上 Ch 20 我們跳過的 `get_oid_with_context` 那塊。

  <details>
  <summary>挑戰 1 的追蹤起點（卡住再看）</summary>

  entry 在 `builtin/rev-parse.c:694` 的 `cmd_rev_parse`。它前面一大段在處理 `--parseopt`/`--sq-quote` 等特殊子模式（`:713` 起，跳過）。核心是一個對每個參數的迴圈，把「不是選項的參數」（如 `HEAD`）丟給 `repo_get_oid`：

  ```c
  // builtin/rev-parse.c:209 (v2.47.1)
  		if (!repo_get_oid(the_repository, s, &oid)) {
  			show_rev(NORMAL, &oid, s);   // 解析成功 → 印出 oid
  ```

  `repo_get_oid`（在 `object-name.c`，最後也落到 Ch 20 提過的 `get_oid_with_context_1`）就是把 `"HEAD"` 這種 revision 字串解析成具體 oid 的那個函式——`HEAD` → 讀 `.git/HEAD`（`ref: refs/heads/master`）→ 讀那個 branch ref → 拿到 commit oid。`show_rev`（`:140`）負責印。所以 `git rev-parse HEAD` 的主線是：`cmd_rev_parse` → 逐參數 → `repo_get_oid`（名字→oid）→ `show_rev`（印）。對照 `git cat-file` 的 `get_oid_with_context`，你會發現**「把使用者給的名字解析成 oid」是很多命令共用的第一步**——認出這個共用步驟，一整批命令的開頭你都秒懂。

  </details>
- **挑戰 2（寫入側）**：讀 `git hash-object -w file.txt`（`builtin/hash-object.c` 的 `cmd_hash_object`），追它怎麼呼叫 Ch 19 的 `write_object_file`/`index_fd` 把一個檔案變成 blob 寫進 object store。這是 Ch 19 寫入路徑的命令入口。

  <details>
  <summary>挑戰 2 的追蹤起點（卡住再看）</summary>

  entry `cmd_hash_object`（`builtin/hash-object.c:88`）解析參數後，對每個檔案呼叫 `hash_object` → `hash_fd`（`:42`）。`hash_fd` 的核心分岔在這（v2.47.1）：

  ```c
  // builtin/hash-object.c:49 (v2.47.1)
  	    (literally
  	     ? hash_literally(&oid, fd, type, flags)
  	     : index_fd(the_repository->index, &oid, fd, &st,
  			type_from_string(type), path, flags)))
  ```

  正常情況走 `index_fd`——它讀檔內容、（`-w` 時）呼叫 Ch 19 的 `write_object_file` 把內容寫成 blob、算出 oid、`printf` 印 oid。**這是 Ch 19 寫入路徑的命令入口**：Ch 19 你讀的是 `write_object_file_flags` 的內部（算 oid、freshen、write_loose_object），這裡你看到它是被哪個命令、從哪一行呼叫的。追完你就把「寫入路徑的內部（Ch 19）」和「寫入路徑的命令入口（這裡）」接起來了。加碼觀察：`hash_fd` 尾端 `printf("%s\n", oid_to_hex(&oid))` 印的就是你 `git hash-object file.txt` 螢幕上看到的那行 oid——和 Ch 18 手算的 `8d0e412...` 對得上。

  </details>
- **挑戰 3（index 格式）**：讀 `Documentation/gitformat-index.txt`，理解 `.git/index` 的實際 byte 佈局，再回頭看 `repo_read_index` 怎麼 parse 它。這讓你從「index 是個 cache_entry 陣列」升級到「index 在磁碟上長怎樣」。

  <details>
  <summary>挑戰 3 的觀察起點（卡住再看）</summary>

  先用 hexdump 直接看 `.git/index` 的檔頭（v2.47.1 真跑）：

  ```bash
  $ xxd -l 12 .git/index
  00000000: 4449 5243 0000 0002 0000 0002    DIRC........
  ```

  頭 4 byte 是 magic `DIRC`（"dircache"），接著 4 byte 版本（`0000 0002` = version 2），再 4 byte entry 數（`0000 0002` = 兩個檔）——和你 `git add` 兩個檔對得上。這正是 `struct index_state` 裡 `version` 和 `cache_nr` 兩個欄位的磁碟來源。之後是一連串 cache entry（每個含 ctime/mtime/dev/ino/mode/uid/gid/size/oid/flags/name），你在 `git ls-files --debug` 看到的那些 `ctime:`/`mtime:`/`size:` 就是這裡逐欄印出來的。`repo_read_index` 的工作就是把這個 byte 流 parse 成記憶體裡的 `cache_entry` 陣列——**磁碟格式 ↔ 記憶體 struct 的對照**，是讀任何有持久化格式的系統的核心技能（對照 Ch 19 讀 packfile 格式）。

  </details>
- **挑戰 4（對照 tree）**：既然 index 是「檔名 → blob oid 的扁平表」、tree 是「檔名 → blob/tree oid 的一層」，讀 `write_index_as_tree`（`cache-tree.c` 附近）看 `git commit` 怎麼把扁平的 index 轉成有階層的 tree。這把 index 和 Ch 18 的 tree 接起來。

  這一步是整個 git 資料模型的「合流點」：`git commit` 拿 index（扁平的 `檔名 → blob oid` 表）→ 建出對應的 tree object（有階層，子目錄自己是一棵 tree）→ 包一個 commit 指向這棵 root tree → 挪 branch 指標。你在挑戰 4 讀懂這條路，就把 Ch 18（object model）、Ch 19（寫入 object）、本練習（index）三塊全接起來了——一個 commit 從「你改的檔」到「不可變的 object 圖」的完整生命週期。做完這個挑戰，git 對你就是白盒了。

- **挑戰 5（自選命令）**：拿本練習末尾那張七步 checklist，挑一個你完全沒讀過的命令（`git mv`、`git rm`、`git symbolic-ref` 之類短的）自己跑一遍，不看任何提示。跑得順，代表流程真的變成你的肌肉了。

## 自我檢核

- [ ] 我在 60 分鐘內畫出了 `git ls-files` 的 call chain，每個節點標了真實檔案:行號。
- [ ] 我用外化的方式（開檔手寫）追，不是只在腦中追。
- [ ] 我能回答那五個問題，特別是「資料來源是 index 不是 object store」和「`-s` 的 oid = blob oid」。
- [ ] 我驗證了 `git ls-files -s` 的 oid = `git hash-object` 的輸出，理解了 index 是「檔名 → blob oid 的扁平表」。
- [ ] 我理解了 index 在 git 資料模型裡的位置：工作目錄 →（`git add`）→ index →（`git commit`）→ tree/commit。
- [ ]（可選）我用 gdb 驗證了 `show_ce` 命中次數 = 檔案數、call stack 和我的圖一致。

## 把這套流程抽象成一張 checklist（帶著走）

你在這個練習做的事，抽掉「ls-files」這個具體目標，剩下一套可套到**任何 git 命令**、甚至任何「命令分派表 + 核心 library」CLI 專案的流程：

1. **偵察**：`wc -l` + `rg '^static |^int cmd_'` 看目標檔多大、有哪些函式，先猜結構。
2. **反查 entry**：`rg '"命令名"' git.c`，從權威的 `commands[]` 表找函式名，別用檔名猜。
3. **跳過選項噪音**：掃 `options[]` 抓「有哪些模式」，不逐行讀；找到解析後的控制流分流點。
4. **鎖定你這次的路徑**：確定「我要追的模式（如無參數）走哪條分支」，其餘支線（`-o`/`-s`/batch…）記一句跳過。
5. **找資料來源**：這命令讀什麼？（index？object store？工作目錄？refs？）通常在 entry 前段有個 `read_xxx`。
6. **追核心迴圈/操作**：從資料來源到「印出/寫入」，追那條主線函式鏈，邊追邊畫圖標行號。
7. **驗證**：黑箱（命令輸出符合你的理解嗎）+ gdb（中斷點命中次數/call stack 符合你的圖嗎）。

**這七步就是你的成品——比讀懂 `ls-files` 這一個命令值錢得多。** 把它抄進你的讀碼筆記，下次面對任何陌生 CLI 命令，照著跑。

追完這個命令，你已經能獨立攻堅任何 git 子命令了：`rg` 反查 entry → 跳過選項噪音 → 找資料來源 → 追印出/寫入迴圈 → gdb 驗證。這套流程對任何有「命令分派表 + 核心 library」結構的 CLI 專案都適用（很多 CLI 工具、shell builtin、甚至 kernel 的 syscall 分派都是同構的）。Part 4 到此結束，下一 Part 我們攻 CPython——三個 VM 的第三個，也是你每天在用的那個 runtime。

→ [Ch 22 CPython 偵察：object model 與 eval 入口](./22-cpython-recon.md)
