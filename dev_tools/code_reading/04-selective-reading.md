# Ch 4 — 選擇性閱讀：黑箱、略讀，以及接受只懂 80%

> **目標**：學會決定「這個函式我不打開」和「這個函式我非打開不可」，能把一個 100+ 行的 kernel 函式在幾分鐘內削成 20 行主幹，並清楚標出自己沒讀懂的那 20%。
> **鴻溝**：本章主要練**目標鴻溝**（沒帶問題就硬讀），順帶處理**慣用法鴻溝**裡最常見的一種：goto 收尾階梯。

> **環境**：Linux kernel v6.18（`~/code_reading/linux`），WSL Ubuntu 22.04，執行中的 kernel 為 `6.18.58.1-microsoft-standard-WSL2`；Python 3.10、universal-ctags 6.2.0、gcc 11.4。

## 為什麼需要這個？

先算一筆帳。在 v6.18 的 tree 裡實際數一下：

```
$ find fs -name '*.[ch]' | xargs cat | wc -l
1562983
$ find fs -name '*.[ch]' | wc -l
2081
$ git ls-files '*.c' '*.h' | xargs cat | wc -l
35834665
```

光是 `fs/` 就有 156 萬行、2081 個檔案；整棵樹三千五百多萬行（其中一大塊是 GPU driver 自動產生的暫存器定義標頭）。假設你一天認真讀 1000 行——對 kernel code 來說這已經是很快的速度——`fs/` 要讀 1563 天，四年多。讀完的時候，你第一年讀的那些檔案已經被改寫過好幾輪了。

所以「從第一行讀到最後一行」不是一個慢一點的策略，它是**不可能完成**的策略。maintainer 不這樣讀，寫 patch 的人不這樣讀，找漏洞的人更不這樣讀。他們做的是：帶著一個問題（[Ch 3](./03-question-driven-reading.md)），只讀能回答那個問題的幾十行，其餘全部當成**黑箱**（black box）——知道它的輸入輸出，不管它裡面怎麼做。

這一章的技術問題只有兩個：

1. 哪些函式可以放心不打開？哪些時候非打開不可？
2. 決定要讀一個函式之後，怎麼在幾分鐘內抓到它的主幹，而不是陷進錯誤處理和鎖的細節裡？

## 先建立直覺

想像你在用地圖 App 規劃從台北到高雄的路線。你看的是高速公路層級的地圖，不會把每一條巷子都放大來看。只有到了某個交流道附近、路線行為跟你預期的不一樣（「為什麼要在這裡下高速公路？」），你才會放大那一區。

讀碼是同一回事。一個函式呼叫樹（call tree）展開來可能有幾百個節點，你只打開其中幾個：

```
do_renameat2()                         ← 你在讀這個
├── filename_parentat()      [黑箱]    名字說得很清楚：解析出父目錄
├── filename_parentat()      [黑箱]
├── mnt_want_write()         [黑箱]    取得「寫入權」，配對 mnt_drop_write()
├── lock_rename()            [黑箱]    鎖兩個父目錄
├── lookup_one_qstr_excl()   [打開！]  ← 行為和假說不符時才打開
│   ├── lookup_dcache()      [黑箱]
│   └── dir->i_op->lookup()  [黑箱]    間接呼叫，這次不追
├── security_path_rename()   [黑箱]    LSM hook，跟這次的問題無關
└── vfs_rename()             [黑箱]    真正做事的地方，但不是這次的問題
```

這張圖裡的「黑箱」不是「不重要」，而是「**對我現在這個問題**不重要」。換一個問題，打開的箱子就完全不同。這也是為什麼選擇性閱讀離不開提問：沒有問題，你就沒有標準決定要打開哪一個。

> 如果你對「提問驅動」還不熟，先回看 [Ch 3 — 帶著問題讀：提問驅動與假說驗證迴圈](./03-question-driven-reading.md)。

## 黑箱：什麼時候可以信任一個函式

把一個函式當黑箱，等於你對它下了一個假說：「它做的事就是它的名字和文件說的那樣」。這個假說的可信度來自三個地方，由弱到強：

| 信任來源 | 能告訴你什麼 | 會在哪裡騙你 |
|---|---|---|
| **名字** | 動詞 + 受詞，大致做什麼 | `do_`、`__` 前綴的函式名字常常很含糊；名字可能是十年前取的，行為早就變了 |
| **簽名** | 參數方向、回傳慣例、會不會失敗 | 回傳 `struct dentry *` 可能是 `ERR_PTR`、也可能是 `NULL`，兩者意思不同 |
| **kernel-doc / 註解** | 前置條件、鎖的要求、回傳值的意義 | 註解和程式碼不同步；但 kernel-doc 在 review 時會被看，可信度通常比隨手註解高 |

kernel 的 coding style 本身就在鼓勵你這樣讀。`Documentation/process/coding-style.rst` 第 6 節「Functions」說函式要短、只做一件事，複雜的邏輯要拆成「有描述性名字的 helper function」。換句話說，**kernel 的作者預期你靠名字理解 helper，而不是每個都點進去**。黑箱閱讀不是偷懶，是配合這套寫作慣例的讀法。

### 簽名本身就是一份壓縮過的文件

看這兩個簽名（都在 `fs/namei.c`，v6.18）：

```c
struct dentry *lookup_one_qstr_excl(const struct qstr *name,
				    struct dentry *base, unsigned int flags)

struct dentry *lock_rename(struct dentry *p1, struct dentry *p2)
```

不看函式本體，你已經能推出不少事：

- 回傳 `struct dentry *`，而且呼叫端緊接著寫 `if (IS_ERR(old_dentry))`——這是 `ERR_PTR` 慣例，錯誤碼被編碼在指標裡（[Ch 17](./17-kernel-error-conventions.md) 會完整拆解）。
- `name` 是 `const`，函式不會改它；`base` 不是 `const`，函式可能會動它（例如增加 refcount）。
- `flags` 是 `unsigned int`，代表有一組 bit flag 會改變行為——**這就是黑箱的「調整旋鈕」，行為不符預期時第一個懷疑的地方**。

### kernel-doc 是黑箱的契約

`vfs_rename()` 上方有一段標準的 kernel-doc（`fs/namei.c:5048-5063`，v6.18）：

```c
/**
 * vfs_rename - rename a filesystem object
 * @rd:		pointer to &struct renamedata info
 *
 * The caller must hold multiple mutexes--see lock_rename()).
 *
 * If vfs_rename discovers a delegation in need of breaking at either
 * the source or destination, it will return -EWOULDBLOCK and return a
 * reference to the inode in delegated_inode.  The caller should then
 * break the delegation and retry.  Because breaking a delegation may
 * take a long time, the caller should drop all locks before doing
 * so.
```

這幾行回答了三個你在讀呼叫端時會卡住的問題：呼叫前要先持有哪些鎖（`lock_rename()`）、為什麼呼叫端會有 `delegated_inode` 這個怪變數、為什麼呼叫端會有一個 `retry_deleg:` 的 label。**你不用讀 `vfs_rename()` 的 159 行本體，就能讀懂呼叫端的那一段。**

`lookup_one_qstr_excl()` 用的是普通註解而非 kernel-doc，但同樣寫了契約（`fs/namei.c:1676-1684`，v6.18）：

```c
/*
 * Parent directory has inode locked exclusive.  This is one
 * and only case when ->lookup() gets called on non in-lookup
 * dentries - as the matter of fact, this only gets called
 * when directory is guaranteed to have no in-lookup children
 * at all.
 * Will return -ENOENT if name isn't found and LOOKUP_CREATE wasn't passed.
 * Will return -EEXIST if name is found and LOOKUP_EXCL was passed.
 */
```

最後兩行特別值錢：它告訴你 `flags` 這個旋鈕怎麼影響錯誤碼。記住它，下面馬上會用到。

### 什麼時候必須打開黑箱

信任是有條件的。碰到以下任何一種情況，就把箱子打開：

1. **觀察到的行為和你的假說不符**。你以為某個錯誤碼在 A 處產生，但在 A 處找不到。
2. **它碰到你關心的狀態**。你的問題是「這個 refcount 什麼時候歸零」，那所有 `get`/`put` 類的函式都不能當黑箱。
3. **名字含糊或可能說謊**。`do_foo()`、`__foo()`、`foo_common()` 這類名字不告訴你差別在哪；`__` 前綴在 kernel 裡常代表「呼叫前要自己先拿鎖」或「不做某些檢查」的版本。
4. **你在做安全審計或除錯**。找漏洞時，黑箱的邊界正是攻擊面——「呼叫端以為它會檢查長度，其實它沒有」是一整類 bug 的來源。

### 實例：EEXIST 是從哪裡來的？

問題：`renameat2(..., RENAME_NOREPLACE)` 碰到目標已存在時會回 `EEXIST`。這個 `EEXIST` 是在 `do_renameat2()` 的哪一行產生的？

在 `do_renameat2()` 裡搜 `EEXIST`，只找到一處（`fs/namei.c:5301-5304`，v6.18）：

```c
	if (flags & RENAME_NOREPLACE)
		error = -EEXIST;
	if (new_type != LAST_NORM)
		goto exit2;
```

這是「目標路徑最後一段不是普通名字（例如是 `.` 或 `..`）」的情況，不是「目標已存在」。假說落空——條件 1 觸發，該打開箱子了。往前看，`RENAME_NOREPLACE` 在函式開頭還出現過一次（`fs/namei.c:5279-5280`）：

```c
	if (flags & RENAME_NOREPLACE)
		target_flags |= LOOKUP_EXCL;
```

`target_flags` 之後被傳進 `lookup_one_qstr_excl()`。對照剛剛讀的契約——「Will return -EEXIST if name is found and LOOKUP_EXCL was passed」——答案在黑箱裡面（`fs/namei.c:1717-1720`，v6.18）：

```c
	if (d_is_positive(dentry) && (flags & LOOKUP_EXCL)) {
		dput(dentry);
		return ERR_PTR(-EEXIST);
	}
```

**更有意思的是：這個答案在一年前不是這樣。** 用 `git show` 看 v6.14 的同一個函式：

```
$ git show v6.14:fs/namei.c | sed -n 5181,5183p
	error = -EEXIST;
	if ((flags & RENAME_NOREPLACE) && d_is_positive(new_dentry))
		goto exit5;
```

v6.14 的 `do_renameat2()` 自己檢查；v6.15 之後這個檢查被搬進了 `lookup_one_qstr_excl()`。用 `git log v6.14..v6.15 --grep=LOOKUP_EXCL -- fs/namei.c` 找到的是 NeilBrown 的 commit `204a575e91f3`「VFS: add common error checks to lookup_one_qstr_excl()」（2025-02-17），commit message 寫得很直白：很多呼叫端都在重複檢查 positive/negative，乾脆搬進去。

這個例子同時教了兩件事：

- **黑箱的邊界會搬家**。你三年前讀懂的「這個檢查在呼叫端」，今天可能已經在被呼叫端裡了。這也是為什麼本課規定所有引用都要從 v6.18 實際複製，不能憑記憶。
- **搬家的理由在 git 歷史裡**。這是第四道鴻溝（脈絡），[Ch 37](./37-git-archaeology.md) 會專門處理。

## 略讀技巧：先看骨架，再看肉

決定要讀一個函式之後，也不是從第一行讀到最後一行。下面四個步驟的順序是刻意的：每一步都用最少的閱讀量，排除掉最多的不確定性。

### 步驟 1：看簽名和呼叫者

先知道「誰會呼叫它、帶什麼進來」。`do_renameat2()` 有三個呼叫者，全部是 syscall（`fs/namei.c:5395-5413`，v6.18）：

```c
SYSCALL_DEFINE5(renameat2, int, olddfd, const char __user *, oldname,
		int, newdfd, const char __user *, newname, unsigned int, flags)
{
	return do_renameat2(olddfd, getname(oldname), newdfd, getname(newname),
			    flags);
}
```

`rename(2)`、`renameat(2)`、`renameat2(2)` 三個 syscall 最後都匯進同一個函式，差別只在 `flags` 是不是 0。這告訴你 `flags` 是「新功能的開關」，舊 API 永遠傳 0。

### 步驟 2：看所有的出口

把函式裡所有的 `return`、`goto`、label 抓出來，不看其他行：

```
$ awk 'NR>=5255 && NR<=5393 && (/goto|return|^[a-z_0-9]+:/) {printf "%d:%s\n", NR, $0}' fs/namei.c
5271:		goto put_names;
5275:		goto put_names;
5282:retry:
5286:		goto put_names;
5291:		goto exit1;
5295:		goto exit2;
5299:		goto exit2;
5304:		goto exit2;
5308:		goto exit2;
5310:retry_deleg:
5314:		goto exit_lock_rename;
5321:		goto exit3;
5326:		goto exit4;
5331:				goto exit5;
5338:			goto exit5;
5340:			goto exit5;
5345:		goto exit5;
5350:		goto exit5;
5355:		goto exit5;
5365:exit5:
5367:exit4:
5369:exit3:
5371:exit_lock_rename:
5375:			goto retry_deleg;
5378:exit2:
5382:exit1:
5387:		goto retry;
5389:put_names:
5392:	return error;
```

這張 30 行的清單已經透露了函式的形狀：

- **只有一個 `return`**（5392）。所有的錯誤都往下跳到收尾區，這是 kernel 的「goto 收尾階梯」慣用法（[Ch 8](./08-control-flow-idioms.md)、[Ch 17](./17-kernel-error-conventions.md)）。
- **兩個往回跳的 label**：`retry:`（5282）和 `retry_deleg:`（5310）。往回跳的 goto 代表重試迴圈，這是你要特別留意的控制流。
- **label 的順序就是資源取得的反序**：`exit5` 釋放最後拿到的東西，`put_names` 釋放最先拿到的東西。所以光看 label 名字和位置，你就能推出「這個函式依序拿了 names → old_path → new_path → write 權 → rename 鎖 → old_dentry → new_dentry」，雖然還沒讀任何一行實際邏輯。

### 步驟 3：分辨 happy path 和 error path

19 個 `goto` 裡，有 17 個是錯誤分支。kernel 函式的典型比例就是這樣：主幹（happy path）只佔一小部分，大部分行數在處理「萬一失敗」。讀的時候，第一遍把錯誤分支全部當作「不會發生」，先讀懂一切順利時發生了什麼；第二遍再挑你關心的錯誤分支回頭看。

### 步驟 4：把錯誤處理遮掉

這一步可以半自動化。下面這個 Python 腳本做的事很粗糙——刪掉「`if (...)` 緊接 `goto`」的兩行、刪掉 label 和單獨的 `error = -EXXX;`——但已經夠用：

```python
#!/usr/bin/env python3
"""把 kernel 函式的錯誤處理遮掉，只留主幹。粗糙但夠用的略讀輔助。"""
import re, sys

lines = open(sys.argv[1]).read().split('\n')
out, i = [], 0
IF   = re.compile(r'^\s*(} else )?if \(.*\)\s*$')
GOTO = re.compile(r'^\s*goto \w+;\s*$')
ERRASSIGN = re.compile(r'^\s*error = (-E[A-Z]+|PTR_ERR\(.*\));\s*$')
LABEL = re.compile(r'^\w+:\s*$')
while i < len(lines):
    l = lines[i]
    nxt = lines[i + 1] if i + 1 < len(lines) else ''
    if IF.match(l) and GOTO.match(nxt):      # 錯誤分支：if + goto 兩行
        i += 2; continue
    if GOTO.match(l) or ERRASSIGN.match(l) or LABEL.match(l):
        i += 1; continue
    out.append(l); i += 1
print(re.sub(r'\n\s*\n(\s*\n)+', '\n\n', '\n'.join(out)))
```

先看原貌，再看結果。

## 示範：do_renameat2() 的原貌與主幹

### 原貌：139 行

這是 `fs/namei.c:5255-5393`（v6.18）完整的 `do_renameat2()`。第一次看，不要逐行讀，用眼睛掃過去，感受一下「錯誤處理佔了多少」：

```c
int do_renameat2(int olddfd, struct filename *from, int newdfd,
		 struct filename *to, unsigned int flags)
{
	struct renamedata rd;
	struct dentry *old_dentry, *new_dentry;
	struct dentry *trap;
	struct path old_path, new_path;
	struct qstr old_last, new_last;
	int old_type, new_type;
	struct inode *delegated_inode = NULL;
	unsigned int lookup_flags = 0, target_flags =
		LOOKUP_RENAME_TARGET | LOOKUP_CREATE;
	bool should_retry = false;
	int error = -EINVAL;

	if (flags & ~(RENAME_NOREPLACE | RENAME_EXCHANGE | RENAME_WHITEOUT))
		goto put_names;

	if ((flags & (RENAME_NOREPLACE | RENAME_WHITEOUT)) &&
	    (flags & RENAME_EXCHANGE))
		goto put_names;

	if (flags & RENAME_EXCHANGE)
		target_flags = 0;
	if (flags & RENAME_NOREPLACE)
		target_flags |= LOOKUP_EXCL;

retry:
	error = filename_parentat(olddfd, from, lookup_flags, &old_path,
				  &old_last, &old_type);
	if (error)
		goto put_names;

	error = filename_parentat(newdfd, to, lookup_flags, &new_path, &new_last,
				  &new_type);
	if (error)
		goto exit1;

	error = -EXDEV;
	if (old_path.mnt != new_path.mnt)
		goto exit2;

	error = -EBUSY;
	if (old_type != LAST_NORM)
		goto exit2;

	if (flags & RENAME_NOREPLACE)
		error = -EEXIST;
	if (new_type != LAST_NORM)
		goto exit2;

	error = mnt_want_write(old_path.mnt);
	if (error)
		goto exit2;

retry_deleg:
	trap = lock_rename(new_path.dentry, old_path.dentry);
	if (IS_ERR(trap)) {
		error = PTR_ERR(trap);
		goto exit_lock_rename;
	}

	old_dentry = lookup_one_qstr_excl(&old_last, old_path.dentry,
					  lookup_flags);
	error = PTR_ERR(old_dentry);
	if (IS_ERR(old_dentry))
		goto exit3;
	new_dentry = lookup_one_qstr_excl(&new_last, new_path.dentry,
					  lookup_flags | target_flags);
	error = PTR_ERR(new_dentry);
	if (IS_ERR(new_dentry))
		goto exit4;
	if (flags & RENAME_EXCHANGE) {
		if (!d_is_dir(new_dentry)) {
			error = -ENOTDIR;
			if (new_last.name[new_last.len])
				goto exit5;
		}
	}
	/* unless the source is a directory trailing slashes give -ENOTDIR */
	if (!d_is_dir(old_dentry)) {
		error = -ENOTDIR;
		if (old_last.name[old_last.len])
			goto exit5;
		if (!(flags & RENAME_EXCHANGE) && new_last.name[new_last.len])
			goto exit5;
	}
	/* source should not be ancestor of target */
	error = -EINVAL;
	if (old_dentry == trap)
		goto exit5;
	/* target should not be an ancestor of source */
	if (!(flags & RENAME_EXCHANGE))
		error = -ENOTEMPTY;
	if (new_dentry == trap)
		goto exit5;

	error = security_path_rename(&old_path, old_dentry,
				     &new_path, new_dentry, flags);
	if (error)
		goto exit5;

	rd.old_parent	   = old_path.dentry;
	rd.old_dentry	   = old_dentry;
	rd.mnt_idmap	   = mnt_idmap(old_path.mnt);
	rd.new_parent	   = new_path.dentry;
	rd.new_dentry	   = new_dentry;
	rd.delegated_inode = &delegated_inode;
	rd.flags	   = flags;
	error = vfs_rename(&rd);
exit5:
	dput(new_dentry);
exit4:
	dput(old_dentry);
exit3:
	unlock_rename(new_path.dentry, old_path.dentry);
exit_lock_rename:
	if (delegated_inode) {
		error = break_deleg_wait(&delegated_inode);
		if (!error)
			goto retry_deleg;
	}
	mnt_drop_write(old_path.mnt);
exit2:
	if (retry_estale(error, lookup_flags))
		should_retry = true;
	path_put(&new_path);
exit1:
	path_put(&old_path);
	if (should_retry) {
		should_retry = false;
		lookup_flags |= LOOKUP_REVAL;
		goto retry;
	}
put_names:
	putname(from);
	putname(to);
	return error;
}
```

### 第一刀：機器遮掉錯誤分支（139 → 83 行）

把上面的函式存成 `do_renameat2.c`，跑腳本：

```
$ python3 skeleton.py do_renameat2.c > skel1.c
$ wc -l do_renameat2.c skel1.c
 139 do_renameat2.c
  83 skel1.c
```

輸出的中段長這樣（節錄，原樣保留）：

```c
	if ((flags & (RENAME_NOREPLACE | RENAME_WHITEOUT)) &&
	    (flags & RENAME_EXCHANGE))

	if (flags & RENAME_EXCHANGE)
		target_flags = 0;
	...
	if (flags & RENAME_NOREPLACE)

	error = mnt_want_write(old_path.mnt);

	trap = lock_rename(new_path.dentry, old_path.dentry);
	if (IS_ERR(trap)) {
	}
```

注意那些**殘骸**：跨兩行的 `if` 條件，腳本只認得單行的，所以留下一個沒有本體的 `if`；`if (IS_ERR(trap)) { }` 的大括號還在、內容沒了。這是刻意保留的失敗示範：**regex 不懂 C 的語法結構，機械過濾永遠會留下殘骸**。工具幫你做掉七成的苦工，剩下的要靠你手動清。想做得更準，要用真的 C parser（例如 clang 的 AST），那是 [Ch 50](./50-compiler-as-reading-tool.md) 的主題。

### 第二刀：手動清掉殘骸、鎖和重試（83 → 約 20 行）

再拿掉三類東西：殘骸、鎖與寫入權的配對（`lock_rename`/`unlock_rename`、`mnt_want_write`/`mnt_drop_write`）、兩個重試迴圈。剩下的就是主幹：

```c
int do_renameat2(olddfd, from, newdfd, to, flags)
{
	/* 1. 把 flags 換算成 lookup 的旋鈕 */
	if (flags & RENAME_NOREPLACE)
		target_flags |= LOOKUP_EXCL;

	/* 2. 兩條路徑各自解析成「父目錄 + 最後一段名字」 */
	filename_parentat(olddfd, from, ..., &old_path, &old_last, ...);
	filename_parentat(newdfd, to,   ..., &new_path, &new_last, ...);

	/* 3. 在父目錄裡找出來源與目標的 dentry */
	old_dentry = lookup_one_qstr_excl(&old_last, old_path.dentry, ...);
	new_dentry = lookup_one_qstr_excl(&new_last, new_path.dentry, ... | target_flags);

	/* 4. 打包成 renamedata，交給 VFS 層 */
	rd.old_parent = old_path.dentry;  rd.old_dentry = old_dentry;
	rd.new_parent = new_path.dentry;  rd.new_dentry = new_dentry;
	rd.flags = flags;
	error = vfs_rename(&rd);

	/* 5. 反序釋放 */
	dput(new_dentry); dput(old_dentry);
	path_put(&new_path); path_put(&old_path);
	putname(from); putname(to);
	return error;
}
```

這 20 多行就是 `do_renameat2()` 的「80%」。用一句話講完：**「把兩條路徑拆成父目錄和檔名，鎖住父目錄，查出兩個 dentry，做一串合法性檢查，交給 `vfs_rename()`。」** 能說出這句話，你就已經能在更大的脈絡裡（例如追 `rename(2)` 的完整路徑）把 `do_renameat2()` 當成一個黑箱來用了。

### 驗證主幹：用使用者看得到的行為對答案

主幹是你的假說，要驗證。被我們「遮掉」的錯誤分支，每一條都對應一個使用者看得到的錯誤碼。寫一個小程式在 WSL 上逐一觸發它們：

```c
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static void try(const char *what, const char *a, const char *b, unsigned flags)
{
	int r = renameat2(AT_FDCWD, a, AT_FDCWD, b, flags);
	printf("%-34s -> %d (%s)\n", what, r, r ? strerror(errno) : "ok");
}

int main(void)
{
	close(open("a", O_CREAT | O_WRONLY, 0644));
	close(open("b", O_CREAT | O_WRONLY, 0644));
	try("NOREPLACE, target exists", "a", "b", RENAME_NOREPLACE);
	try("NOREPLACE|EXCHANGE (bad combo)", "a", "b", RENAME_NOREPLACE | RENAME_EXCHANGE);
	try("unknown flag bit 1<<5", "a", "b", 1u << 5);
	try("cross mount: ./a -> /mnt/c/...", "a", "/mnt/c/Users/Public/a_rn_test", 0);
	try("trailing slash on non-dir", "a/", "c", 0);
	try("plain rename a -> c", "a", "c", 0);
	unlink("b"); unlink("c");
	return 0;
}
```

```
$ gcc -Wall -o rn rn.c && ./rn
NOREPLACE, target exists           -> -1 (File exists)
NOREPLACE|EXCHANGE (bad combo)     -> -1 (Invalid argument)
unknown flag bit 1<<5              -> -1 (Invalid argument)
cross mount: ./a -> /mnt/c/...     -> -1 (Invalid cross-device link)
trailing slash on non-dir          -> -1 (Not a directory)
plain rename a -> c                -> 0 (ok)
```

每一行都能對回原始碼：

| 輸出 | 產生位置（v6.18） |
|---|---|
| `File exists` | `lookup_one_qstr_excl()`，`fs/namei.c:1717-1720`（黑箱裡面） |
| `Invalid argument`（組合不合法） | `fs/namei.c:5273-5275` |
| `Invalid argument`（未知 bit） | `fs/namei.c:5270-5271` |
| `Invalid cross-device link` | `fs/namei.c:5293-5295`：`old_path.mnt != new_path.mnt`（`/mnt/c` 是另一個 mount） |
| `Not a directory` | `fs/namei.c:5335-5338`：來源不是目錄卻帶了結尾斜線 |

> WSL 執行中的 kernel 是 6.18.58（WSL 分支），不是 v6.18 tag 本身；這幾條錯誤路徑在兩者之間沒有差異，但嚴格來說我們驗證的是「v6.18 系列」的行為。

這就是「略讀 → 假說 → 驗證」的完整一圈：主幹靠略讀抓，細節靠行為反查，不需要先把 139 行背下來。

## 接受只懂 80%

讀完上面這些，`do_renameat2()` 裡還有幾件事你沒弄懂：

- `trap` 是什麼？為什麼 `old_dentry == trap` 就是 `EINVAL`？（註解說「source should not be ancestor of target」，但 `lock_rename()` 怎麼算出 trap 的？）
- `retry_estale()` 和 `LOOKUP_REVAL`：什麼情況下會整個重來一次？
- `delegated_inode` 和 `break_deleg_wait()`：NFS delegation 是什麼？

**這很正常，而且應該被明確寫下來。** 「只懂 80%」不是放棄那 20%，是把它們從「模糊的不安」變成「一張具體的未解疑問清單」。[Ch 7](./07-reading-notes-and-diagrams.md) 的筆記格式裡有一欄就叫「未解疑問」，專門放這些東西。清單上的每一條，等你的問題真的需要它時再回來——例如你哪天在追 NFS 的 bug，`delegated_inode` 就從 20% 升級成你必須打開的黑箱。

什麼時候 80% 不夠？

| 情境 | 為什麼 80% 不夠 |
|---|---|
| 寫 patch 修改這個函式 | 你改的那一行可能剛好在你沒讀懂的 20% 裡 |
| 安全審計 | 漏洞幾乎都藏在錯誤路徑和邊界條件裡，正好是略讀時被遮掉的部分 |
| 追 race condition | 鎖的取得與釋放順序就是答案本身，不能遮 |
| review 別人的 patch | 你要判斷的就是「這個改動有沒有破壞某條錯誤路徑」 |

規則很一致：**你要改動或攻擊的地方，必須讀到 100%；你只是路過的地方，80% 就夠。**

## 量化：fs/ 有多少，你需要讀多少

| 範圍 | 行數（v6.18） | 檔案數 |
|---|---|---|
| 整棵樹 `.c` + `.h` | 35,834,665 | — |
| `fs/` 全部 | 1,562,983 | 2,081 |
| `fs/*.c`（VFS 核心，不含各檔案系統子目錄） | 85,158 | 71 |
| `fs/xfs/` | 223,069 | — |
| `fs/btrfs/` | 164,316 | — |
| `fs/ext4/` | 66,378 | — |
| `fs/namei.c` | 5,623（170 個函式） | 1 |
| `do_renameat2()` | 139 | — |
| `do_renameat2()` 主幹 | 約 20 | — |

（函式數用 `ctags -x --kinds-C=f fs/namei.c | wc -l` 數出。）

回答「為什麼跨 mount 的 rename 會回 `EXDEV`」這個問題，實際需要讀的是：syscall 包裝（6 行）、`do_renameat2()` 的出口清單（掃過）、5293-5295 那三行。**加起來不到 20 行，佔 `fs/` 的百萬分之一點三。** 剩下的 99.9999% 都是黑箱，而且這是正確的。

順帶一提，用 ctags 可以直接列出一個檔案裡最長的函式，幫你決定「哪些是需要略讀技巧的大函式」：

```
$ ctags -x --kinds-C=f --fields=+e --_xformat='%N %n %e' fs/namei.c \
    | awk '{print $3-$2+1, $1}' | sort -rn | head -6
159 vfs_rename
139 do_renameat2
113 lookup_open
106 path_init
97 link_path_walk
81 getname_flags
```

`%n` 是起始行、`%e` 是結束行（`--fields=+e` 才會有），相減加一就是長度。

## 對比與取捨

| 讀法 | 投入 | 得到 | 適合 |
|---|---|---|---|
| 逐行精讀 | 最高 | 100% 理解，含所有錯誤路徑 | 要改的、要審計的函式 |
| 略讀主幹（本章） | 低 | 80% 理解：做什麼、依序呼叫誰 | 大部分路過的函式 |
| 黑箱（只看名字、簽名、文件） | 最低 | 輸入輸出的契約 | 呼叫樹裡和問題無關的分支 |
| 動態追蹤（ftrace、gdb） | 中（要環境） | 「實際上」走了哪條路 | 靜態閱讀分不出走哪條分支時（[Ch 51](./51-dynamic-tracing.md)、[Ch 52](./52-qemu-gdb-kernel.md)） |

這四種不是互斥的。一次真實的讀碼通常是：大部分函式當黑箱，三四個略讀，一個精讀，卡住時開 ftrace 看一眼。

## 踩雷集錦

1. **「把它當黑箱 = 它不重要」**：錯。黑箱只代表「對現在的問題不重要」。今天的黑箱可能是明天的主角；你的未解疑問清單就是在記錄這件事。
2. **「註解說它會檢查，所以它會檢查」**：註解是假說的來源，不是證據。`lookup_one_qstr_excl()` 的 EEXIST 註解在 v6.15 才加上去；如果你讀的是 v6.14 的 tree 卻拿著 v6.15 的知識，結論就錯了。行為和註解衝突時，**相信程式碼和實測**。
3. **「遮掉錯誤處理 = 錯誤處理沒內容」**：`do_renameat2()` 的錯誤分支裡藏著 `EXDEV`、`ENOTDIR`、`ENOTEMPTY` 這些語意規則——它們是 rename 的**規格**，只是在略讀第一遍時暫時不看。第二遍要挑你關心的回頭讀。
4. **「機器過濾的結果可以直接信」**：上面腳本的輸出有沒有本體的 `if`、空的大括號。拿這種輸出去推論控制流會出錯。工具輸出永遠要人工檢查一次。
5. **「`__foo()` 和 `foo()` 差不多，當同一個黑箱就好」**：kernel 裡 `__` 前綴常代表「呼叫前要自己拿鎖」或「少做某些檢查」的版本。這正是「名字含糊時要打開」的典型情況。

## 進階：再往深一層

- **讀 diff 而不是讀全文**：當你想知道一個函式「最近改了什麼」，`timeout 300 git log -L :do_renameat2:fs/namei.c v6.14..v6.18` 會列出這個版本範圍內每一次修改這個函式的 commit 和 diff（在 blobless clone 上 `-L` 要按需下載大量 blob，一定要限定版本範圍，並用 `timeout` 防止卡住）。很多時候讀最近三個 diff，比讀全文更快理解它現在的設計重點（[Ch 37](./37-git-archaeology.md)）。
- **用呼叫者反推黑箱的契約**：如果一個函式沒有文件，看它的所有呼叫者怎麼處理回傳值。`cscope -d -L3 lookup_one_qstr_excl` 列出所有呼叫者；如果每個呼叫者都寫 `if (IS_ERR(...))`，那它就是 `ERR_PTR` 慣例；如果有呼叫者還檢查 `NULL`，那它可能兩種都會回（[Ch 48](./48-elixir-cscope-gtags.md)）。
- **用 AST 而不是 regex 遮錯誤處理**：clang 可以 dump 一個函式的 AST（`clang -Xclang -ast-dump -fsyntax-only`），在 AST 上把「條件只檢查 error 變數的 if 分支」剪掉，就不會留下殘骸。kernel 需要正確的編譯旗標才能 parse，要搭配 `compile_commands.json`（[Ch 49](./49-clangd-lsp.md)、[Ch 50](./50-compiler-as-reading-tool.md)）。
- **Ko 等人 2006 年的觀察研究**發現，開發者在維護任務中花大量時間在「找到相關程式碼」和「在相關片段之間來回導航」，而不是讀懂單一片段（見延伸閱讀）。這是選擇性閱讀在實證研究上的背書：瓶頸在「選」，不在「讀」。

## 動手練習

1. **主幹抽取**：對 `fs/namei.c` 的 `do_linkat()`（`fs/namei.c:4965` 起，v6.18，70 行）重複本章四個步驟。先用 awk 抓出口清單，推出它依序取得了哪些資源；再跑 `skeleton.py`、手動清殘骸，寫出一句話摘要。
2. **找黑箱裡的錯誤碼**：`linkat(2)` 碰到目標已存在時回 `EEXIST`。這個錯誤碼在 v6.18 是哪一行產生的？在 v6.14 又是哪一行？（提示：`do_linkat()` 本身找不到 EEXIST，打開它呼叫的 `filename_create()`，看它傳給 lookup 的 flags；再用 `git show v6.14:fs/namei.c` 對照同一個函式。）
3. **故意讓工具失敗**：把 `skeleton.py` 套到 `fs/pipe.c` 的 `anon_pipe_read()`（`fs/pipe.c:268-404`，v6.18）。這個函式幾乎不用 goto，用的是 `break` 跳出 `for (;;)`。觀察腳本為什麼幾乎沒刪掉任何東西，然後想想：對「break 型」的函式，遮錯誤處理的規則應該怎麼改？

<details>
<summary>練習 3 的方向提示</summary>

`anon_pipe_read()` 的錯誤處理模式是「`if (!ret) ret = -EXXX; break;`」：錯誤碼只在還沒讀到任何資料時才設定，然後跳出迴圈。要遮掉它，規則要改成「刪掉內容只有 `ret = -E...` 和 `break` 的 if 區塊」。但小心：`if (!total_len) break;`（`fs/pipe.c:364-365`）是 happy path 的出口，不能刪。這正是「break 同時用於成功與失敗」讓機械過濾困難的原因。[Ch 7](./07-reading-notes-and-diagrams.md) 會完整讀這個函式。

</details>

## 本章重點整理

- 從頭讀到尾在 kernel 的規模下不可能完成；`fs/` 一個目錄就有 156 萬行。選擇性閱讀是唯一可行的讀法。
- 黑箱的信任來自名字、簽名、kernel-doc，由弱到強；行為不符、碰到你關心的狀態、名字含糊、做安全審計時，必須打開。
- 略讀四步驟：看呼叫者 → 抓所有出口 → 分 happy/error path → 遮掉錯誤處理。label 的順序就是資源取得的反序。
- 機械過濾會留殘骸，工具輸出要人工檢查。
- 「只懂 80%」要搭配一張明確的未解疑問清單；要改的、要審計的地方必須讀到 100%。

## 自我檢核

- [ ] 不看本章，能說出四個「必須打開黑箱」的條件，並各舉一個 kernel 裡的例子嗎？
- [ ] 拿到一個新的 100 行 kernel 函式，你會先執行哪個指令？為什麼是那個而不是從第一行開始讀？
- [ ] 如果同事說「`lookup_one_qstr_excl()` 不會回 EEXIST，我記得是呼叫端自己檢查的」，你怎麼在兩分鐘內判斷他對不對？他的記憶可能來自哪裡？
- [ ] 用自己的話解釋：為什麼 goto 階梯的 label 順序能讓你推出資源取得的順序？
- [ ] 想一個你最近讀過、但沒讀完的程式碼。你沒讀懂的那 20% 是什麼？把它寫成三條具體的未解疑問。

## 延伸閱讀

### 論文

- **An Exploratory Study of How Developers Seek, Relate, and Collect Relevant Information during Software Maintenance Tasks** — Andrew J. Ko, Brad A. Myers, Michael J. Coblenz, Htet Htet Aung，IEEE Transactions on Software Engineering 32(12)（2006）
  - **核心貢獻**：觀察開發者在 Eclipse 裡做維護任務，發現大量時間花在搜尋、導航、重新找回之前看過的程式碼，而非理解單一片段。
  - **讀哪裡**：結果與討論部分，特別是關於導航成本與「工作集」（working set）的段落。
  - **和本章的關聯**：為「選擇性閱讀」提供實證背景——讀碼的瓶頸在決定讀什麼，不在讀懂每一行。前提：不需要軟體工程研究背景，敘述性強。
- **Questions Programmers Ask During Software Evolution Tasks** — Jonathan Sillito, Gail C. Murphy, Kris De Volder，ACM SIGSOFT FSE（2006）
  - **核心貢獻**：整理出開發者在修改陌生程式碼時提出的 44 種問題，並分成四大類（找起點、擴展、理解子圖、理解多個子圖之間的關係）。
  - **讀哪裡**：問題分類表本身最有用。
  - **和本章的關聯**：決定黑箱要不要打開，取決於你在問哪一類問題；這篇給了你一份問題的詞彙表，和 [Ch 3](./03-question-driven-reading.md) 直接相連。

### 官方文件

- **Linux kernel coding style**（`Documentation/process/coding-style.rst`，docs.kernel.org 上的 `process/coding-style`）
  - **讀哪裡**：第 6 節「Functions」與第 7 節「Centralized exiting of functions」。
  - **能學到什麼**：第 6 節說明為什麼 kernel 偏好短函式加描述性命名的 helper（這是黑箱閱讀可行的前提）；第 7 節解釋 goto 收尾的理由，讀完你會知道步驟 2 的出口清單為什麼長那樣。
- **Pathname lookup**（`Documentation/filesystems/path-lookup.rst`）— Neil Brown，Al Viro、Jon Corbet 協助
  - **讀哪裡**：前半部關於 `nameidata`、`LAST_NORM` 與「parent + last component」的說明。
  - **能學到什麼**：本章刻意當成黑箱的 `filename_parentat()`、`LAST_NORM`、`LOOKUP_REVAL`，在這份文件裡有完整背景。它本身是三篇 LWN 文章改寫的（檔案開頭列出了原文連結），是補「領域鴻溝」的好例子。前提：知道 dentry、inode 是什麼。
- **Writing kernel-doc comments**（`Documentation/doc-guide/kernel-doc.rst`）
  - **讀哪裡**：函式註解的格式說明（`@param`、`Return:`、`Context:` 小節）。
  - **能學到什麼**：知道 kernel-doc 有哪些固定欄位，你就知道該在哪裡找「鎖的要求」「回傳值意義」這類黑箱契約。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 1 章（讀碼的動機與策略）與第 6 章「Tackling Large Projects」。
  - **這本書的定位**：本課的骨架。原書在大型專案的部分談到不要試圖理解全部、靠目錄結構與命名縮小範圍；例子是 2003 年的 NetBSD 與 Apache，方法論至今適用。

會挑了，下一個問題是：一個陌生的系統，第一刀從哪裡切？user-space 有 `main()`，kernel 沒有。

→ [Ch 5 從哪裡開始讀：入口點與「資料結構優先」](./05-entry-points-and-data-first.md)
