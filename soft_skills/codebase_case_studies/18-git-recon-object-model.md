# Ch 18 — git 偵察：plumbing vs porcelain 與 object model

> **目標**：用 `reading_code` 的 60 分鐘偵察 SOP，在 git 這個「表面複雜、核心極簡」的專案裡快速定位兩件事——命令怎麼分派（`git.c` 的 `commands[]` 表 → `builtin/*`），以及整個 git 建立在什麼資料模型上（四種 object + content-addressed store）。讀完你手上有一張能繼續往下鑽的地圖。

> **目標codebase**：git v2.47.1（commit `92999a4`）

## 為什麼需要這個？

git 給人的第一印象是「命令多到記不完」——`add`、`commit`、`rebase`、`cherry-pick`、`reflog`、`worktree`…光 `builtin/` 目錄底下就一百多個 `.c` 檔。如果你抱著「把每個命令讀一遍」的心態進來，三天後你會放棄。

但 git 的作者（Linus 與後續維護者）反覆講一句話：**「git 的核心是它的資料模型，命令只是操作這個模型的薄殼。」** 這句話對讀碼的意義是決定性的：你不該從命令切入，你該先搞懂那個模型。搞懂之後，任何命令你都能預測「它大概在對 object store 做什麼」。

這正是 `reading_code` Ch 5「第一次接觸：60 分鐘偵察」的精神——**不要一開始就想讀懂全部，先找到系統的「重心」在哪裡**。git 的重心不在命令，在 `object.h` 定義的那四種 object 和它們構成的圖。這一章我們用兩條偵察線同時推進：一條追命令怎麼分派（讓你有能力挑任何命令追下去，Ch 20 做），一條建立 object model 的心智模型（Ch 19 深挖儲存層）。

## 先建立直覺

在讀任何 code 之前，先把 git 的兩層心智圖畫出來。這張圖是你後面所有閱讀的骨架：

```
   使用者打的命令                    git 內部真正的世界
   （porcelain，瓷器）              （plumbing，管路）
   ┌─────────────────────┐         ┌──────────────────────────────────┐
   │ git commit          │         │        content-addressed         │
   │ git log             │──操作──▶│           object store           │
   │ git merge           │         │                                  │
   │ git rebase          │         │   ┌──────┐  ┌──────┐  ┌────────┐ │
   └─────────────────────┘         │   │ blob │  │ tree │  │ commit │ │
        給人用的                    │   └──────┘  └──────┘  └────────┘ │
                                    │      每個 object 的名字          │
   ┌─────────────────────┐         │      = SHA-1(它的內容)           │
   │ git cat-file        │         │                                  │
   │ git hash-object     │──直接──▶│   ref（branch/tag）只是指向       │
   │ git rev-parse       │  暴露   │   某個 commit oid 的一個名字      │
   │ git update-ref      │  模型   │                                  │
   └─────────────────────┘         └──────────────────────────────────┘
    plumbing：給程式/腳本用
    也是我們讀碼時的「顯微鏡」
```

**porcelain（瓷器）vs plumbing（管路）** 是 git 自己的術語。porcelain 是給人用的高階命令（`commit`、`log`、`merge`）；plumbing 是給腳本用的低階命令，它們直接暴露資料模型。對讀碼的人來說，plumbing 命令是**最好的顯微鏡**——`git cat-file` 讓你直接看任何 object 的原始內容，`git hash-object` 讓你看一段內容會變成哪個 oid。我們這一章就用它們把模型看透，再回頭讀實作。

## 偵察第一步：60 分鐘掃出架構

假設你剛 clone 完 `v2.47.1`，`cd` 進去。按 `reading_code` Ch 5 的動作：先看目錄結構，再找 entry point。

```bash
$ cd ~/cbcs/git
$ ls *.c | wc -l          # 頂層 .c 檔
$ ls builtin/*.c | wc -l  # 每個子命令一個檔
```

頂層一堆 `.c`（`object.c`、`commit.c`、`tree.c`、`blob.c`、`object-file.c`、`packfile.c`…）是**函式庫**；`builtin/` 底下每個檔對應一個子命令。這個切分本身就是一條偵察線索：**核心邏輯在頂層的 library，命令只是門面**。

git 的 entry point 在哪？`git` 這個執行檔的 `main` 呼叫的是 `cmd_main()`（git 自己包了一層 `common-main.c`）。我們直接看 `git.c`：

```c
// git.c:892 (v2.47.1)
int cmd_main(int argc, const char **argv)
{
	const char *cmd;
	...
	if (skip_prefix(cmd, "git-", &cmd)) {
		argv[0] = cmd;
		handle_builtin(argc, argv);   // ← 分派入口
```

`handle_builtin` 就是命令分派的核心。這是我們的第一個 beacon（`reading_code` Ch 3 的「地標」概念）——找到它，等於找到「所有 `git <cmd>` 是怎麼變成一個函式呼叫」的答案。

## 底層機制一：命令分派——一張表 + 一次線性掃描

git 怎麼把字串 `"cat-file"` 對應到函式 `cmd_cat_file`？答案樸素到令人意外：**一張陣列表 + `strcmp` 線性掃描**。

先看那張表的元素長什麼樣：

```c
// git.c:32 (v2.47.1)
struct cmd_struct {
	const char *cmd;
	int (*fn)(int, const char **, const char *, struct repository *);
	unsigned int option;
};
```

三個欄位：命令名字串、一個函式指標（真正的實作）、一組 option flag（如 `RUN_SETUP` 表示這命令要先進到 git repo 目錄）。整個 git 的命令集合就是這種 struct 的一個大陣列：

```c
// git.c:506 (v2.47.1)
static struct cmd_struct commands[] = {
	{ "add", cmd_add, RUN_SETUP | NEED_WORK_TREE },
	{ "am", cmd_am, RUN_SETUP | NEED_WORK_TREE },
	...
	{ "cat-file", cmd_cat_file, RUN_SETUP },
	...
```

分派靠 `get_builtin()`，一個從頭掃到尾的 `for` 迴圈：

```c
// git.c:653 (v2.47.1)
static struct cmd_struct *get_builtin(const char *s)
{
	int i;
	for (i = 0; i < ARRAY_SIZE(commands); i++) {
		struct cmd_struct *p = commands + i;
		if (!strcmp(s, p->cmd))
			return p;
	}
	return NULL;
}
```

沒有 hash table、沒有 trie，就是 `strcmp` 一路比下去。這是可以接受的——命令總數才一百多個，比一次是奈秒級。找到之後 `run_builtin()` 做完該做的環境設定（進 repo 目錄、設 pager…），最後一行才真正把控制權交給那個命令：

```c
// git.c:483 (v2.47.1)
	status = p->fn(argc, argv, prefix, (p->option & RUN_SETUP)? repo : NULL);
```

`p->fn` 就是 `cmd_cat_file`、`cmd_commit` 這些。在真正呼叫 `p->fn` 之前，`run_builtin` 還做了一連串「所有命令共用的前置」，值得看一眼，因為它解釋了 `commands[]` 第三欄那些 flag 的用途：

```c
// git.c:456 (v2.47.1) —— run_builtin 節錄
	if (run_setup & RUN_SETUP) {
		prefix = setup_git_directory();          // RUN_SETUP：進到 .git 所在目錄
	} else if (run_setup & RUN_SETUP_GENTLY) {
		int nongit_ok;
		prefix = setup_git_directory_gently(&nongit_ok);
	}
	...
	if (!help && p->option & NEED_WORK_TREE)
		setup_work_tree();                        // NEED_WORK_TREE：需要工作目錄
	...
	status = p->fn(argc, argv, prefix, ...);      // 最後才交棒
```

看懂這段你就懂 `{ "cat-file", cmd_cat_file, RUN_SETUP }` 的 `RUN_SETUP` 是什麼意思——它是「這命令要先確定自己在一個 git repo 裡、並 chdir 過去」。`{ "clone", cmd_clone }` 沒有 `RUN_SETUP`，因為 clone 時還沒有 repo。**flag 是宣告式的前置條件**，`run_builtin` 統一處理，各命令不用自己重寫「我要不要在 repo 裡」的判斷。這是把橫切關注點（cross-cutting concern）從一百多個命令抽到一處的設計。

整條分派鏈是：

```
   cmd_main (git.c:892)
      │  argv[0] = "cat-file"
      ▼
   handle_builtin (git.c:711)
      │  cmd = "cat-file"
      ▼
   get_builtin (git.c:653)   ── for 迴圈 strcmp ──▶  找到 commands[] 裡的
      │                                              { "cat-file", cmd_cat_file, ... }
      ▼
   run_builtin (git.c:444)   ── setup_git_directory / pager 等前置 ──▶
      │
      ▼
   p->fn(...)  ==  cmd_cat_file(argc, argv, prefix, repo)   ← builtin/cat-file.c
```

**這就是 `reading_code` Ch 23「讀懂 indirection」的實戰**：`p->fn(...)` 這一行你單看看不出它呼叫哪個函式——是函式指標，得先知道 `p` 指向表裡哪一格才知道跳去哪。掌握了這個 pattern，你以後追任何 git 命令，第一步都固定：`rg '"命令名"' git.c` 找到它在 `commands[]` 的那一格，看第二欄的函式名，跳去 `builtin/` 對應檔。

> 這種「字串 → 函式指標表」的 **command dispatch table** 我們在別的 codebase 還會遇到：SQLite 的 VDBE opcode 分派（Ch 9）、Lua VM 的 opcode（Ch 4）、CPython 的 eval loop（Ch 23）本質上都是「一個索引 → 一段對應邏輯」。git 這裡是最好認的入門版，因為索引是人類可讀的命令字串。

## 底層機制二：四種 object 與 content addressing

命令分派搞懂了，回到真正的重心：資料模型。git 只有**四種** object，全定義在 `object.h` 的一個 enum 裡：

```c
// object.h:97 (v2.47.1)
enum object_type {
	OBJ_BAD = -1,
	OBJ_NONE = 0,
	OBJ_COMMIT = 1,
	OBJ_TREE = 2,
	OBJ_BLOB = 3,
	OBJ_TAG = 4,
	/* 5 for future expansion */
	OBJ_OFS_DELTA = 6,     // 這兩個是 packfile 內部用的 delta，Ch 19 講
	OBJ_REF_DELTA = 7,
	...
};
```

前四個（`commit`/`tree`/`blob`/`tag`）是你會直接接觸的。它們的意義：

- **blob**：一坨位元組。通常是一個檔案的內容。**只有內容，沒有檔名**——這點很多人一開始會搞錯。
- **tree**：一個目錄。裡面是一串 `(mode, 名字, 指向的 oid)`，把名字綁到 blob（檔案）或 tree（子目錄）。**檔名住在 tree 裡，不在 blob 裡。**
- **commit**：一次提交。指向一棵 tree（那一刻的完整目錄快照）、零到多個 parent commit、作者/committer、commit message。
- **tag**：annotated tag，指向某個 object（通常是 commit）加上 tagger 與訊息。

所有 object 在記憶體裡的共同表頭是這個極小的 struct：

```c
// object.h (v2.47.1)
struct object {
	unsigned parsed : 1;
	unsigned type : TYPE_BITS;
	unsigned flags : FLAG_BITS;
	struct object_id oid;    // ← 這個 object 的名字
};
```

關鍵在 `oid`（object id）。它是什麼？

```c
// hash.h:191 (v2.47.1)
struct object_id {
	unsigned char hash[GIT_MAX_RAWSZ];
	int algo;	/* XXX requires 4-byte alignment */
};
```

`oid` 就是一個雜湊值（SHA-1 是 20 bytes，SHA-256 是 32 bytes，`GIT_MAX_RAWSZ` 取大的）。**而這個雜湊值，是 object 內容本身算出來的。** 這就是 git 的核心設計——**content addressing（內容定址）**：一個 object 的名字 = 它內容的雜湊。內容一樣 → 名字一樣 → 自動去重；內容改一個位元 → 名字全變 → 天生防竄改。

## 用顯微鏡（plumbing）親眼看模型

別停在讀 struct，用 plumbing 命令把模型看出來。以下都是在一個乾淨 demo repo 真跑的輸出（v2.47.1）：

```bash
$ mkdir demo && cd demo && git init -q
$ echo "hello git" > file.txt
$ git add file.txt && git commit -q -m "first commit"
```

先看 commit object 的原始內容——`cat-file -p` 是「pretty print 這個 object」：

```bash
$ git cat-file -p HEAD
tree c8bcfef1da123a980537a5fa4cf9b7c4f387d451
author demo <a@b.c> 1767196800 +0800
committer demo <a@b.c> 1767196800 +0800

first commit
```

看清楚：commit **不直接存檔案**，它存一行 `tree <oid>` 指向一棵 tree。這棵 tree 才是目錄快照：

```bash
$ git cat-file -p HEAD^{tree}
100644 blob 8d0e41234f24b6da002d962a26c2495ea16a425f	file.txt
```

tree 裡一行是 `<mode> <type> <oid>\t<名字>`。名字 `file.txt` 住在**這裡**（tree），指向一個 blob。那個 blob 呢：

```bash
$ git cat-file -p 8d0e41234f24b6da002d962a26c2495ea16a425f
hello git
```

blob 就是純內容，`hello git`（加上換行共 10 bytes），**沒有檔名**。你可以問任何 object 的型別和大小：

```bash
$ git cat-file -t 8d0e41234f24b6da002d962a26c2495ea16a425f
blob
$ git cat-file -s 8d0e41234f24b6da002d962a26c2495ea16a425f
10
```

一張圖把這個 demo 串起來：

```
   HEAD ──▶ commit e824989...
             │  author / committer / message
             │  tree ─────────┐
             │  (parent: 無，這是第一個 commit)
             └────────────────┘
                              ▼
                        tree c8bcfef...
                        100644 blob 8d0e412...  file.txt
                                       │  ← 「file.txt」這個名字在 tree 裡
                                       ▼
                                 blob 8d0e412...
                                 "hello git\n"   ← 純內容，無檔名
```

## content addressing 不是玄學：手算一次 oid

「blob 的 oid 是內容的 SHA-1」——別當口號，親手驗證一次。git 算 blob oid 的規則是：對 `"blob <內容長度>\0<內容>"` 這整串算 SHA-1。內容是 `hello git\n`（10 bytes），所以：

```bash
$ printf 'blob 10\0hello git\n' | sha1sum
8d0e41234f24b6da002d962a26c2495ea16a425f  -

$ git hash-object file.txt
8d0e41234f24b6da002d962a26c2495ea16a425f
```

**一模一樣。** 你剛剛手動重現了 git 給 object 命名的整個算法。這個 `"<type> <len>\0"` 的表頭，我們 Ch 19 會在 `object-file.c` 的 `format_object_header()` 讀到它是怎麼被組出來的。這種「讀了 struct，再用命令驗證，最後手算重現」的三段式，就是這門課要練的讀碼肌肉——不相信二手描述，親手核對到底。

## 對比與取捨

| 面向 | git 的選擇 | 常見替代方案 | git 為什麼這樣選 |
|---|---|---|---|
| 命令分派 | 靜態陣列 + `strcmp` 線性掃描 | hash table / 動態註冊 | 命令數固定且少（~150），線性掃夠快，code 極簡好讀 |
| object 命名 | 內容雜湊（content-addressed） | 遞增 ID / 路徑當 key | 天生去重 + 防竄改 + 分散式可離線合併 |
| 檔名歸屬 | 存在 tree，不在 blob | 檔名跟內容綁一起 | 同內容不同名的檔可共用一個 blob，省空間 |
| 歷史結構 | commit 指向 parent 的 DAG | 線性 diff 串 / 資料庫 rows | 分支/合併天然是圖，快照 + 共享子樹省空間 |
| object 種類 | 只有 4 種 | 更多特化型別 | 極少的正交概念組合出全部功能，模型好推理 |

取捨的重點：git 用**極少的正交概念**（4 種 object + content addressing + DAG）搭出全部功能。這是「模型即一切」的體現——概念少，你就能在腦中完整模擬它，任何命令都變成「它在對這個模型做什麼」。

## 踩雷集錦

1. **以為 blob 裡有檔名。** 錯。blob 只有內容。檔名住在 tree 的每一行裡（`... file.txt`）。所以兩個內容相同、檔名不同的檔案，共用**同一個 blob**。跑一次 `git cat-file -p <blob>` 你會發現輸出裡根本沒有檔名。
2. **以為 commit 存的是「這次改了什麼」（diff）。** 錯。commit 指向一棵 **完整的 tree**——那一刻整個專案的快照，不是差異。你看到的 `git log -p` 的 diff 是 git **臨時算**出來的（比對兩棵 tree），不是存下來的。這點誤解會讓你完全讀錯儲存層。
3. **想從 `builtin/` 一個一個命令讀懂 git。** 錯。一百多個命令會淹死你。git 的重心在頂層 library（object model + object store），命令是薄殼。先讀模型，命令自然好懂。這是 `reading_code` Ch 11「收斂」的教訓。
4. **看到 `p->fn(...)` 以為能直接跳定義。** 那是函式指標，LSP 也跳不過去。你得先在 `commands[]` 裡查那個命令對應的函式名，才知道實際跳去哪。這是 indirection 的經典陷阱。
5. **把 porcelain 命令當成讀碼的入口。** `git rebase` 這種高階命令內部纏繞很多互動邏輯，難讀。想理解模型，用 plumbing（`cat-file`/`hash-object`/`rev-parse`）當顯微鏡，短、直接、暴露本質。

## 進階：再往深一層

- **ref 只是一個指向 oid 的名字。** branch（`refs/heads/main`）、tag（`refs/tags/v1`）、`HEAD`——這些都不是 object，是**指標**，內容就是一個 oid（或指向另一個 ref）。親眼看（v2.47.1 真跑）：

  ```bash
  $ cat .git/HEAD
  ref: refs/heads/master          # HEAD 指向一個 branch（symbolic ref）
  $ git rev-parse master
  e824989828dc7522a00ad6b2d950025df0cb1b49   # master 這個名字指向的 commit oid
  $ git for-each-ref
  e824989828dc7522a00ad6b2d950025df0cb1b49 commit	refs/heads/master
  ```

  `HEAD` 指向 `refs/heads/master`，`master` 指向一個 commit oid `e824989...`。整個 ref 系統就是「名字 → oid」的一層薄指標。理解「object 是不可變的內容，ref 是可變的名字」是理解整個 git 的鑰匙：`git commit` 做的是「產生新 object + 把 branch 這個指標往前挪」；`git reset` 只挪指標、不動 object；`git checkout` 換 `HEAD` 指向。**所有『修改歷史』的命令，動的都是指標，不是 object。** 這對照 `reading_code` Ch 8 的 data flow：改 branch 只是改一個指標指向，object 本身永遠不變。
- **為什麼 content addressing 讓分散式協作成為可能。** 兩個人各自離線 commit，同一份內容在兩邊算出的 oid 一致，push/pull 時 git 靠 oid 就能判斷「這個 object 你有沒有」，不需要中央伺服器發序號。這是 git 打敗 SVN 的根本原因，全來自「名字 = 內容雜湊」這一個決定。
- **SHA-1 到 SHA-256 的遷移。** `object_id` 裡有個 `algo` 欄位、`GIT_MAX_RAWSZ` 取兩者較大值，就是為了同時支援兩種雜湊。你在 `object-file.c` 會看到很多 `compat_hash_algo` 的分支——那是為了讓 SHA-1 repo 和 SHA-256 repo 能互通的相容層。第一次讀時可以先跳過這些分支，抓主線。

## 本章重點整理

- git 的重心是**資料模型**，不是命令。命令（`builtin/*`）是操作模型的薄殼；核心邏輯在頂層 library。
- 命令分派 = `git.c` 的 `commands[]` 陣列（`{名字, 函式指標, flags}`）+ `get_builtin()` 的 `strcmp` 線性掃描 + `run_builtin()` 呼叫 `p->fn`。追任何命令，先在 `commands[]` 查它的函式名。
- 只有四種 object：**blob**（純內容）、**tree**（目錄，檔名住這裡）、**commit**（快照 + parent，指向 tree）、**tag**。
- **content addressing**：object 的名字（oid）= 它內容的雜湊。去重 + 防竄改 + 分散式協作全由此而來。可用 `printf 'blob <len>\0<內容>' | sha1sum` 手動重現。
- plumbing 命令（`cat-file`/`hash-object`/`rev-parse`）是讀碼時的顯微鏡；porcelain 是給人用的門面。

## 自我檢核

- [ ] 我能說出 `git cat-file -p HEAD` 這條命令，從 `cmd_main` 到 `cmd_cat_file` 中間經過哪幾個函式（handle_builtin → get_builtin → run_builtin → p->fn）。
- [ ] 我能解釋 `p->fn(...)` 為什麼不能直接用 LSP 跳定義，以及正確的追法。
- [ ] 我能不看教材說出四種 object 各自存什麼，特別是「檔名住在 tree 不在 blob」「commit 存快照不存 diff」。
- [ ] 我能解釋 content addressing 是什麼、它帶來哪三個好處，並知道怎麼手動算一個 blob 的 oid。
- [ ] 我能區分 object（不可變、內容定址）和 ref（可變、一個指向 oid 的名字）。

## 延伸閱讀

- **[Pro Git — 10.2 Git Objects](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)**
  - **讀哪裡**：整個 10.2 節。它用 `git hash-object -w` / `git cat-file` 一步步手動建出 blob→tree→commit，和本章的 plumbing 顯微鏡法完全一致，是最好的對照。
  - **學什麼**：不靠高階命令、純用 plumbing 手工組出一個 commit，把 object model 內化。
  - **前提**：會用 git 基本命令。
- **[Documentation/gitcore-tutorial.txt（git 官方，隨 clone 附帶）](https://git-scm.com/docs/gitcore-tutorial)**
  - **讀哪裡**：開頭到「Creating a git repository」與「Object database」幾節。這是 git 自己寫的「用 plumbing 理解核心」教學。
  - **學什麼**：git 作者視角的 object database 心智模型，和你讀 `object.h` 對得起來。
  - **前提**：讀得懂命令列範例。
- **本 clone 的 `git.c`（`cmd_main`/`handle_builtin`/`get_builtin`/`run_builtin`）與 `object.h`（`enum object_type`、`struct object`）**
  - **讀哪裡**：`git.c:892`、`:711`、`:653`、`:444` 四個函式串起分派鏈；`object.h:97` 的 enum 與 `struct object`。
  - **學什麼**：親手 `rg` 過去核對本章每一段引用，養成不信二手描述的習慣。
  - **前提**：已 clone v2.47.1（commit `92999a4`）。

模型的骨架有了。下一章我們鑽進儲存層：這些 object 實際上怎麼躺在 `.git/objects` 裡（loose object 的 zlib 壓縮 + 兩碼分目錄），以及當它們變多時，git 怎麼用 packfile + delta 壓縮把它們打包起來。

→ [Ch 19 content-addressed store 與 packfile](./19-git-object-store-packfiles.md)
