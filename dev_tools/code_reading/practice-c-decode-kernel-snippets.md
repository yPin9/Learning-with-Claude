# 練習 C — 逐行解碼 10 段 kernel 天書

> **目標**：把 Part 3（Ch 15–22）學到的 kernel C 方言全部拿來用，逐行解碼 10 段從 Linux v6.18 原封不動複製出來的程式碼。每一段你都要回答：每一行在做什麼、它依賴哪一章的知識、你卡住時卡在哪一道鴻溝、以及**你怎麼驗證自己的解讀是對的**。做完之後，你應該能拿任何一段 `fs/` 或 `kernel/` 底下的函式，在不查資料的情況下說出八成。

> **鴻溝**：本練習主要練慣用法鴻溝，後幾段加上間接與脈絡鴻溝。

## 背景與動機

讀碼能力的瓶頸常常不在「知不知道某個概念」，而在**同時**處理好幾個概念。單獨看 `container_of`、`ERR_PTR`、`guard()`、`READ_ONCE` 都不難，但真實的 kernel 函式會在十行內把它們全部用上，再加上一個你沒看過的子系統 struct。這個練習模擬的就是這個情境：每一段都是真實的、正在被使用的程式碼，沒有為教學簡化。

這 10 段全部來自 VFS 與 pipe 相關的檔案（`fs/file_table.c`、`fs/file.c`、`fs/pipe.c`、`fs/fhandle.c`、`fs/pidfs.c`、`kernel/watch_queue.c`、`include/linux/file.h`），和 [Ch 19](./19-kernel-memory-allocation.md)–[Ch 22](./22-kernel-macro-decoding.md) 的主線相同，也是 [Final Project](./final-project-pipe-eventfd-guided-reading.md) 的預備。你會發現同一個物件（`struct file`、`struct pipe_inode_info`）從不同角度反覆出現——這正是讀一個子系統的真實體驗。

## 任務規格

| 項目 | 規格 |
|---|---|
| 輸入 | 下面 10 段程式碼，每段標 `檔案:行號（v6.18）`。請在自己的 tree（`git checkout v6.18`）開同一個檔案對照，上下文可以自由看 |
| 每段要交的東西 | (1) 逐行解碼筆記；(2) 回答該段的「問題」；(3) 標出用到哪幾章；(4) 標出你卡住的鴻溝（Ch 1 的五道之一或多道）；(5) 至少一個**驗證方法**：`rg`、`make foo.i`、`nm`、`pahole`、`git log`、ftrace、QEMU 實驗擇一 |
| 允許使用 | 本課 Ch 1–22、kernel 原始碼、`Documentation/`、`git log`、編譯產物 |
| 禁止使用 | 先看解答；把程式碼貼給 LLM 要它解釋（[Ch 53](./53-llm-assisted-reading.md) 會講怎麼用，但這個練習是在建立你自己的判斷基準） |
| 驗收標準 | 你的解讀和解答在「這段程式碼的不變式與生命週期」上一致；措辭不必相同。每段至少一個驗證動作有實際執行 |
| 建議時間 | 第 1–4 段各 15 分鐘，第 5–7 段各 30 分鐘，第 8–10 段各 45–60 分鐘 |

難度分三級：

```
第 1–4 段   ★     單一技巧為主，練認 pattern
第 5–7 段   ★★    兩三個技巧交錯，要往上下文找資訊
第 8–10 段  ★★★   跨函式、跨檔案、涉及並行；要攤平呼叫鏈或找 barrier 的另一半
```

## 期望輸出範例

以第 0 段（示範，不計分）說明筆記格式：

```c
/* include/linux/kref.h:62-69（v6.18） */
static inline int kref_put(struct kref *kref, void (*release)(struct kref *kref))
{
	if (refcount_dec_and_test(&kref->refcount)) {
		release(kref);
		return 1;
	}
	return 0;
}
```

```
逐行：
  62  第二個參數是函式指標：release callback，最後一份參考還掉時呼叫
  64  refcount_dec_and_test：減一，結果為 0 回傳 true；附帶 release ordering，
      成功時 acquire（Ch 20、Ch 21）
  65  只有「減到零的那一個呼叫者」會執行 release
  66  回傳 1 告訴呼叫者「物件已經被拆了，別再碰它」
用到：Ch 20（kref、get/put）、Ch 21（ordering）
鴻溝：間接（release 是指標，要找 kref_put 的呼叫點傳入了什麼函式）
驗證：rg -n 'kref_put\(' drivers/base/ | head  → 看實際傳入的 release 函式
```

## 如果你卡住了

1. **先分類每一行**：是宣告、配置、鎖、參考計數操作、錯誤處理、還是巨集登記？把每行貼上標籤，整段的骨架就出來了。
2. **看到不認識的巨集，不要猜**：`rg -n 'define 巨集名' include/` 找定義，或 `make O=../build 檔案.i` 讓編譯器展開（[Ch 22](./22-kernel-macro-decoding.md)）。
3. **看到函式名帶 get/put/alloc/free**：立刻在筆記開一欄「參考帳本」，記下每一份參考的來源與去向（[Ch 20](./20-refcount-and-lifetime.md)）。
4. **看到 GFP flag**：寫下它對 context 的宣告，再往上確認沒有鎖（[Ch 19](./19-kernel-memory-allocation.md)）。
5. **看到 barrier 或 `_acquire`/`_release`**：不要只看這一段，去找它的另一半；找到之前不要宣稱你懂了（[Ch 21](./21-read-once-and-barriers.md)）。

## 實作步驟建議

### Step 1：建環境
在 WSL 開 `~/code_reading/linux`，確認 `git describe --tags` 是 `v6.18`。準備一個筆記檔，每段一節。

### Step 2：先冷讀，不查任何東西
每段先花五分鐘只看原文，寫下你的第一版解讀與疑問。這是你的基準線，做完後回來比對，看自己在哪些 pattern 上進步了。

### Step 3：查與驗證
針對每個疑問，用「如果你卡住了」的方法查。每段至少執行一個驗證指令，把指令與輸出貼進筆記。

### Step 4：回答問題、標章節與鴻溝
寫完再展開解答比對。差異大的地方，回到對應章節重讀那一節。

### Step 5：整合
做完 10 段後，回頭寫一段話：哪一道鴻溝出現最多次？哪一個驗證工具最常用？這會告訴你接下來 Part 4–8 應該加強哪裡。

---

## 第 1 段 ★ — 從內嵌成員回到外層物件

```c
/* fs/file_table.c:46-55（v6.18） */
/* Container for backing file with optional user path */
struct backing_file {
	struct file file;
	union {
		struct path user_path;
		freeptr_t bf_freeptr;
	};
};

#define backing_file(f) container_of(f, struct backing_file, file)
```

```c
/* fs/file_table.c:69-81（v6.18） */
static inline void file_free(struct file *f)
{
	security_file_free(f);
	if (likely(!(f->f_mode & FMODE_NOACCOUNT)))
		percpu_counter_dec(&nr_files);
	put_cred(f->f_cred);
	if (unlikely(f->f_mode & FMODE_BACKING)) {
		path_put(backing_file_user_path(f));
		kmem_cache_free(bfilp_cachep, backing_file(f));
	} else {
		kmem_cache_free(filp_cachep, f);
	}
}
```

**問題**：

1. `backing_file(f)` 回傳什麼？如果把 `struct file file;` 移到 `union` 後面，這段程式碼還對嗎？
2. 為什麼釋放時要分兩個 cache？如果對一個 backing file 呼叫 `kmem_cache_free(filp_cachep, f)` 會怎樣？
3. `put_cred`、`path_put` 各自在還什麼？

**提示**：[Ch 16](./16-container-of.md) 的 `container_of`；[Ch 19](./19-kernel-memory-allocation.md) 的 `files_init` 建了哪兩個 cache；[Ch 20](./20-refcount-and-lifetime.md) 的「物件死亡時，它對別人持有的參考也一併還掉」。

<details>
<summary>點開解答</summary>

**逐行解碼**

- `struct backing_file` 把一個完整的 `struct file` 內嵌為第一個成員，後面接一個 union。這是 C 的「繼承」：`backing_file` 是一種 `file`，外部程式碼拿到的是 `struct file *`，只有 `file_table.c` 知道外面還包了一層（[Ch 42](./42-object-oriented-c.md) 的 struct 嵌入）。
- union 裡 `user_path` 只在物件活著時有用，`bf_freeptr` 只在物件回到 slab 後用（和 `struct file` 的 `f_freeptr` 同一個用途：`SLAB_TYPESAFE_BY_RCU` cache 的 freelist 指標要放在一個不會被 lockless reader 誤讀的位置）。生命週期互斥，所以共用空間。
- `#define backing_file(f) container_of(f, struct backing_file, file)`：給一個指向內嵌 `file` 成員的指標，減掉 `offsetof(struct backing_file, file)` 回到外層。
- `file_free`：`security_file_free` 通知 LSM；`FMODE_NOACCOUNT` 的檔案沒有計入 `nr_files`（見 `alloc_empty_file_noaccount`），所以不減；`put_cred` 還掉 `init_file` 裡 `get_cred` 拿的參考。
- `FMODE_BACKING` 分支：先 `path_put` 還掉 `user_path` 裡的 dentry 與 vfsmount 參考，再把**外層物件**還給 `bfilp_cachep`。

**問題回答**

1. 回傳外層 `struct backing_file *`。移動成員後仍然正確——`container_of` 用 `offsetof` 算偏移，不假設成員在開頭。這正是用 `container_of` 而不是直接強制轉型的理由。（但 `kmem_cache_free` 會拿到正確的外層位址，強制轉型就不會。）
2. 兩種物件大小不同，分別來自 `filp` 與 `bfilp` 兩個 cache（`fs/file_table.c:595-602`）。把 backing file 還給 `filp_cachep` 是 cache 錯配：SLUB 在 `CONFIG_SLAB_FREELIST_HARDENED` 或 debug 選項下會偵測並警告，沒開時就是記憶體損毀。另外 `f` 指向內嵌成員，雖然這裡它剛好在偏移 0，但語意上要還的是外層物件。
3. `put_cred` 還 credential 的參考；`path_put` 還 `struct path` 裡的兩份參考（`dput(path->dentry)` 與 `mntput(path->mnt)`）。

**用到**：Ch 16、Ch 19、Ch 20、Ch 11（union 的生命週期用法）。
**鴻溝**：慣用法（`container_of`）。
**驗證**：
```
$ pahole -C backing_file ~/code_reading/build/vmlinux
```
確認 `file` 的偏移是 0、union 的大小與位置；再 `rg -n 'FMODE_BACKING' fs/` 看誰設定這個旗標（`alloc_empty_backing_file`）。

</details>

---

## 第 2 段 ★ — 兩套錯誤慣例的交接點

```c
/* fs/file_table.c:218-256（v6.18） */
struct file *alloc_empty_file(int flags, const struct cred *cred)
{
	static long old_max;
	struct file *f;
	int error;

	/*
	 * Privileged users can go above max_files
	 */
	if (unlikely(get_nr_files() >= files_stat.max_files) &&
	    !capable(CAP_SYS_ADMIN)) {
		/*
		 * percpu_counters are inaccurate.  Do an expensive check before
		 * we go and fail.
		 */
		if (percpu_counter_sum_positive(&nr_files) >= files_stat.max_files)
			goto over;
	}

	f = kmem_cache_alloc(filp_cachep, GFP_KERNEL);
	if (unlikely(!f))
		return ERR_PTR(-ENOMEM);

	error = init_file(f, flags, cred);
	if (unlikely(error)) {
		kmem_cache_free(filp_cachep, f);
		return ERR_PTR(error);
	}

	percpu_counter_inc(&nr_files);

	return f;

over:
	/* Ran out of filps - report that */
	if (get_nr_files() > old_max) {
		pr_info("VFS: file-max limit %lu reached\n", get_max_files());
		old_max = get_nr_files();
	}
	return ERR_PTR(-ENFILE);
}
```

**問題**：

1. 這個函式可能回傳哪幾種值？呼叫者該怎麼檢查？用 `if (!f)` 檢查會出什麼事？
2. 為什麼先用 `get_nr_files()` 判斷，超過了再用 `percpu_counter_sum_positive()` 確認一次？
3. 從 `GFP_KERNEL` 你能推論出這個函式的呼叫者有什麼限制？
4. `static long old_max;` 是做什麼的？它有並行問題嗎？嚴不嚴重？

**提示**：[Ch 17](./17-kernel-error-conventions.md) 的 `ERR_PTR`；[Ch 27](./27-bitmap-percpu-kfifo.md) 會講 percpu counter，現在先記住它「快但不精確」；[Ch 19](./19-kernel-memory-allocation.md) 的 GFP 反推。

<details>
<summary>點開解答</summary>

**逐行解碼**

- 回傳型別是 `struct file *`，但錯誤用 `ERR_PTR(-ENOMEM)`、`ERR_PTR(error)`、`ERR_PTR(-ENFILE)` 編碼在指標裡，**永遠不回傳 NULL**。
- `get_nr_files()` 讀 percpu counter 的近似值（`percpu_counter_read_positive`），非常便宜；`percpu_counter_sum_positive` 把每個 CPU 的局部計數加總，精確但昂貴。先用便宜的判斷，只有可能超標時才付精確的代價——**快速路徑／慢速路徑**分離。
- `kmem_cache_alloc(filp_cachep, GFP_KERNEL)`：從專用 cache 配置，回傳 NULL 代表失敗；這裡把 NULL 轉成 `ERR_PTR(-ENOMEM)`。
- `init_file` 失敗時用 `kmem_cache_free` 還回同一個 cache，再把錯誤碼包成 `ERR_PTR`。
- 成功才 `percpu_counter_inc`——計數只反映真正存在的 file。
- `over:` 標籤：只在超過「上次報告過的最大值」時才印，避免洗版。

**問題回答**

1. 成功時回傳有效指標；失敗時回傳 `ERR_PTR(-ENOMEM)`、`ERR_PTR(-ENFILE)` 或 `init_file` 的錯誤（LSM 拒絕時）。呼叫者要用 `IS_ERR(f)` 檢查、`PTR_ERR(f)` 取錯誤碼。用 `if (!f)` 會把錯誤指標當有效指標用——解參考一個接近 `(void *)-12` 的位址，kernel oops。這種「NULL 和 ERR_PTR 混淆」是 kernel 常見 bug 類型（`smatch` 這類靜態分析工具會檢查）。
2. percpu counter 每個 CPU 有自己的局部值，`read` 只讀全域部分，可能和真實值差「CPU 數 × batch」那麼多。如果只用近似值判斷，接近上限時會誤判失敗；每次都加總又太慢。兩段式檢查在正常情況下（遠低於上限）只付一次便宜讀取的成本。
3. `GFP_KERNEL` → 呼叫者必須在可睡 context：不能拿 spinlock、不能在 IRQ 或 RCU read section 內。這是一個可驗證的假說：`rg -n 'alloc_empty_file\(' fs/` 找到的呼叫者（`fs/namei.c:4120` 的 `path_openat`、`fs/file_table.c:349` 的 `alloc_file`（被 `alloc_file_pseudo` 使用）、`fs/open.c` 裡的幾個 helper）都在 syscall 或 kernel 內部的可睡路徑上。
4. 記錄上次報告時的檔案數，讓「達到上限」的訊息只在數量創新高時印。它有 data race（多個 CPU 同時讀寫 `old_max`），但後果只是多印或少印一行 log，不影響正確性。照現在的標準，這類存取會被 KCSAN 報告，通常會補 `READ_ONCE`/`WRITE_ONCE` 或 `data_race()` 標記；這段沒有，是舊程式碼的痕跡。

**用到**：Ch 17、Ch 19、Ch 18（`unlikely`）、Ch 21（data race 的判斷）。
**鴻溝**：慣用法（ERR_PTR）、領域（percpu counter 的精確度取捨）。
**驗證**：
```
$ rg -n 'alloc_empty_file\(' fs/ | head
$ rg -n 'static inline s64 percpu_counter_read_positive|percpu_counter_sum_positive' include/linux/percpu_counter.h
```
看呼叫者的 context；讀 percpu counter 的兩個函式本體確認「近似 vs 精確」。

</details>

---

## 第 3 段 ★ — 指標裡藏旗標

```c
/* include/linux/file.h:33-63（v6.18） */
/* either a reference to struct file + flags
 * (cloned vs. borrowed, pos locked), with
 * flags stored in lower bits of value,
 * or empty (represented by 0).
 */
struct fd {
	unsigned long word;
};
#define FDPUT_FPUT       1
#define FDPUT_POS_UNLOCK 2

#define fd_file(f) ((struct file *)((f).word & ~(FDPUT_FPUT|FDPUT_POS_UNLOCK)))
static inline bool fd_empty(struct fd f)
{
	return unlikely(!f.word);
}

#define EMPTY_FD (struct fd){0}
static inline struct fd BORROWED_FD(struct file *f)
{
	return (struct fd){(unsigned long)f};
}
static inline struct fd CLONED_FD(struct file *f)
{
	return (struct fd){(unsigned long)f | FDPUT_FPUT};
}

static inline void fdput(struct fd fd)
{
	if (unlikely(fd.word & FDPUT_FPUT))
		fput(fd_file(fd));
}
```

**問題**：

1. 為什麼可以把旗標存在指標的低兩個 bit？這依賴什麼保證？在 `include/linux/fs.h` 裡找到那個保證。
2. 為什麼用 `struct fd { unsigned long word; }` 而不是直接用 `unsigned long`？
3. 「borrowed」和「cloned」在參考計數上差在哪？`fdput` 對兩者分別做什麼？

**提示**：[Ch 9](./09-expressions-integers-bits.md) 的 mask；[Ch 11](./11-structs-unions-tagged.md) 的 tagged 表示法；[Ch 20](./20-refcount-and-lifetime.md) 的 `__fget_light`。

<details>
<summary>點開解答</summary>

**逐行解碼**

- `struct fd` 只有一個 `unsigned long`：指標和旗標打包在一起。0 代表空。
- `FDPUT_FPUT`（bit 0）：結束時要 `fput`；`FDPUT_POS_UNLOCK`（bit 1）：結束時要解開 `f_pos_lock`。
- `fd_file(f)`：用 `& ~(1|2)` 把低兩位清掉，還原成真正的指標。
- `BORROWED_FD`：不設任何旗標——沒拿參考，結束時什麼都不做。
- `CLONED_FD`：設 `FDPUT_FPUT`——拿了一份參考，結束時要還。
- `fdput`：只有設了 `FDPUT_FPUT` 才 `fput`。

**問題回答**

1. `struct file` 的位址至少是 4 的倍數，所以低兩位一定是 0，可以拿來存資料。保證寫在 `include/linux/fs.h:1251-1252`：`} __randomize_layout __attribute__((aligned(4)));	/* lest something weird decides that 2 is OK */`。實際上 slab 配置的物件對齊更大，但這行明文保證了 4-byte 對齊，即使 `__randomize_layout` 打亂欄位順序也不會變。
2. 型別安全。如果是裸的 `unsigned long`，任何整數都能傳進 `fdput`，也能不小心把它當指標用；包成 struct 之後，編譯器會阻止你把 `struct fd` 直接當 `struct file *` 用，你必須經過 `fd_file()`。這是 kernel 常用的「用單成員 struct 做 strong typedef」技巧（`atomic_t`、`refcount_t` 也是同一招）。
3. borrowed：沒有增加 `f_ref`，借用的是 fd table 那份參考，前提是借用期間沒人能 close 這個 fd（[Ch 20](./20-refcount-and-lifetime.md) 列的三條規則）；`fdput` 不做事。cloned：`f_ref` 加了一，`fdput` 呼叫 `fput` 還掉。

**用到**：Ch 9、Ch 11、Ch 20、Ch 18（`unlikely`）。
**鴻溝**：慣用法（指標 tagging）。
**驗證**：
```
$ rg -n 'BORROWED_FD|CLONED_FD' fs/file.c
```
找到 `__fget_light` 和 `fdget_pos`，看兩種 fd 分別在什麼條件下產生。

</details>

---

## 第 4 段 ★ — 鎖外配置、鎖內交換

```c
/* fs/pipe.c:1291-1315（v6.18） */
int pipe_resize_ring(struct pipe_inode_info *pipe, unsigned int nr_slots)
{
	struct pipe_buffer *bufs;
	unsigned int head, tail, mask, n;

	/* nr_slots larger than limits of pipe->{head,tail} */
	if (unlikely(nr_slots > (pipe_index_t)-1u))
		return -EINVAL;

	bufs = kcalloc(nr_slots, sizeof(*bufs),
		       GFP_KERNEL_ACCOUNT | __GFP_NOWARN);
	if (unlikely(!bufs))
		return -ENOMEM;

	spin_lock_irq(&pipe->rd_wait.lock);
	mask = pipe->ring_size - 1;
	head = pipe->head;
	tail = pipe->tail;

	n = pipe_occupancy(head, tail);
	if (nr_slots < n) {
		spin_unlock_irq(&pipe->rd_wait.lock);
		kfree(bufs);
		return -EBUSY;
	}
```

**問題**：

1. `(pipe_index_t)-1u` 的值是多少？在 32-bit 和 64-bit kernel 上一樣嗎？
2. 這段裡每一個 GFP bit 告訴你什麼？
3. 為什麼 `kcalloc` 在 `spin_lock_irq` 之前，`kfree` 在 `spin_unlock_irq` 之後？把它們挪進鎖內各會出什麼問題？
4. 為什麼是 `spin_lock_irq` 而不是 `spin_lock`？

**提示**：`rg -n 'pipe_index_t' include/linux/pipe_fs_i.h`；[Ch 19](./19-kernel-memory-allocation.md) 例一；`pipe->rd_wait.lock` 還被誰拿？（想想第 10 段的 `post_one_notification`）。

<details>
<summary>點開解答</summary>

**逐行解碼**

- `nr_slots > (pipe_index_t)-1u`：把 `-1u`（全 1）轉成 `pipe_index_t`，得到該型別的最大值。head/tail 是 `pipe_index_t`，slot 數不能超過它能表示的範圍。
- `kcalloc(nr_slots, sizeof(*bufs), ...)`：陣列配置、乘法溢位檢查、清零。
- `GFP_KERNEL_ACCOUNT`：可睡、記 memcg 帳；`__GFP_NOWARN`：失敗不印警告。
- `spin_lock_irq(&pipe->rd_wait.lock)`：拿 wait queue 內建的 spinlock，同時關本地中斷。
- 讀 `head`/`tail` 算目前占用量 `n`；新容量放不下就放鎖、釋放、回 `-EBUSY`。

**問題回答**

1. `include/linux/pipe_fs_i.h:41-45`：64-bit 上 `pipe_index_t` 是 `unsigned int`（最大 4294967295），32-bit 上是 `unsigned short`（最大 65535）。所以這個檢查在兩種架構上的上限不同。寫成 `(pipe_index_t)-1u` 而不是寫死常數，是為了讓同一行程式碼自動適應 typedef。
2. `__GFP_DIRECT_RECLAIM`（在 `GFP_KERNEL` 裡）→ 呼叫者可睡，此刻沒拿 spinlock；`__GFP_ACCOUNT` → slot 數由 user 控制（`fcntl(F_SETPIPE_SZ)` 會走到這裡），要記帳防濫用；`__GFP_NOWARN` → 失敗是預期中的（user 可能要一個巨大的 pipe），不值得印 warning。
3. `kcalloc` 帶 `GFP_KERNEL` 可能睡，在 spinlock 內睡是 bug（可能 deadlock，開了 `CONFIG_DEBUG_ATOMIC_SLEEP` 會報 `sleeping function called from invalid context`）。`kfree` 本身可以在 atomic context 呼叫，挪進鎖內不會錯，但會無謂地拉長關中斷的臨界區。
4. 這把鎖也會被中斷相關的 context 拿：wake up 路徑會拿 wait queue 的鎖，而 `post_one_notification`（第 10 段）用 `spin_lock_irq` 拿同一把鎖。如果這裡只用 `spin_lock`，持鎖時被中斷打斷、中斷處理路徑又去拿同一把鎖，就會在同一個 CPU 上自己等自己。規則是：**同一把鎖只要有任何一處在中斷 context 拿，其他地方就要關中斷拿**（[Ch 30](./30-reading-locking-code.md)）。

**用到**：Ch 9（`-1u` 與轉型）、Ch 19（GFP、`kcalloc`）、Ch 18（`unlikely`）、Ch 30 預告。
**鴻溝**：慣用法（`(T)-1u`）、領域（spinlock 與中斷的關係）。
**驗證**：
```
$ rg -n 'rd_wait\.lock' fs/pipe.c kernel/watch_queue.c
$ rg -n 'pipe_resize_ring\(' fs/ kernel/
```
列出所有拿這把鎖的地方與方式；確認 `pipe_resize_ring` 的呼叫者（`pipe_set_size`、`watch_queue_set_size`）都在可睡 context。

</details>

---

## 第 5 段 ★★ — 沒有呼叫者的函式

```c
/* fs/pipe.c:1536-1553（v6.18） */
static int __init init_pipe_fs(void)
{
	int err = register_filesystem(&pipe_fs_type);

	if (!err) {
		pipe_mnt = kern_mount(&pipe_fs_type);
		if (IS_ERR(pipe_mnt)) {
			err = PTR_ERR(pipe_mnt);
			unregister_filesystem(&pipe_fs_type);
		}
	}
#ifdef CONFIG_SYSCTL
	register_sysctl_init("fs", fs_pipe_sysctls);
#endif
	return err;
}

fs_initcall(init_pipe_fs);
```

以及它用到的變數宣告：

```c
/* fs/pipe.c:869（v6.18） */
static struct vfsmount *pipe_mnt __ro_after_init;
```

**問題**：

1. 誰呼叫 `init_pipe_fs`？什麼時候？請用至少兩種方法證明（一個靜態、一個看產物）。
2. `__init` 和 `__ro_after_init` 各自代表什麼？開機完成後，`init_pipe_fs` 的程式碼和 `pipe_mnt` 的值各處於什麼狀態？
3. 如果另一個 `fs_initcall` 函式在 `init_pipe_fs` 之前執行、而且呼叫了 `pipe(2)` 的內部實作，會發生什麼事？

**提示**：[Ch 22](./22-kernel-macro-decoding.md) 的 initcall 整條鏈；`make O=../build fs/pipe.i` 看最後一行；`nm vmlinux`。

<details>
<summary>點開解答</summary>

**逐行解碼**

- `static int __init`：放在 `.init.text`，開機後被釋放（Ch 22）。
- `register_filesystem(&pipe_fs_type)`：向 VFS 登記 pipefs 檔案系統型別。
- `kern_mount`：在 kernel 內部掛載一個 pipefs 實例，回傳 `struct vfsmount *`，失敗時是 `ERR_PTR`（Ch 17）。所以用 `IS_ERR`/`PTR_ERR` 檢查，不是 NULL。
- 掛載失敗時 `unregister_filesystem` 撤銷前一步——迷你版的 goto 階梯。
- `#ifdef CONFIG_SYSCTL`：只在有 sysctl 時登記 `/proc/sys/fs/pipe-*` 參數（Ch 36）。
- `fs_initcall(init_pipe_fs)`：登記為級別 5 的 initcall。
- `pipe_mnt` 的 `__ro_after_init`：開機期間可寫，`mark_rodata_ro()` 之後變成唯讀頁面。

**問題回答**

1. 由 `do_initcalls()` → `do_initcall_level(5, ...)` → `do_one_initcall()` 在開機時呼叫（`init/main.c:1333-1346`）。靜態證據：`make O=../build fs/pipe.i` 的最後一行把 `fs_initcall` 展開成 `.section ".initcall5.init"` 裡的 `.long init_pipe_fs - .`。產物證據：
   ```
   $ objdump -h ../build/fs/pipe.o | grep initcall
     4 .initcall5.init 00000004 ...
   $ nm ../build/vmlinux | grep -E '__initcall5_start|__initcall6_start|init_pipe_fs5'
   ffffffff83c913c0 D __initcall5_start
   ffffffff83c914e0 D __initcall6_start
   ffffffff83c91408 d __initcall__kmod_pipe__647_1553_init_pipe_fs5
   ```
   登記項目位於級別 5 與 6 的起點之間。
2. `__init`：開機完成後 `free_initmem()` 釋放 `.init.text`，`init_pipe_fs` 的機器碼不復存在，任何開機後呼叫它的程式碼都是 bug。`__ro_after_init`：`pipe_mnt` 在開機時被寫一次，之後所在頁面被設為唯讀，再寫會觸發 page fault。這讓「開機後不該再改的指標」得到硬體保護，攻擊者也無法藉著任意寫入改掉它。
3. `pipe_mnt` 還是 NULL（`.data` 的初始值），`create_pipe_files` 呼叫 `alloc_file_pseudo(inode, pipe_mnt, ...)` 會用到它，結果是 NULL 解參考。initcall 級別就是在定義這種順序依賴：需要 pipe 的程式碼要用比 5 更晚的級別（例如 `device_initcall`）。

**用到**：Ch 22（initcall、`__init`）、Ch 17（`IS_ERR`）、Ch 36 預告（`#ifdef CONFIG_*`）、Ch 18（`__ro_after_init` 這類 section 標註）。
**鴻溝**：間接（呼叫關係在 linker section 裡）、慣用法。
**驗證**：上面的 `objdump`/`nm` 指令；另外 `rg -n '__ro_after_init' include/linux/cache.h` 找到它的定義是 `__section(".data..ro_after_init")`。若要看執行時序，在 QEMU 開機參數加 `initcall_debug`（本練習未實測）。

</details>

---

## 第 6 段 ★★ — 從 user 讀一個變長結構

```c
/* fs/fhandle.c:339-385（v6.18，節錄） */
static int handle_to_path(int mountdirfd, struct file_handle __user *ufh,
		   struct path *path, unsigned int o_flags)
{
	int retval = 0;
	struct file_handle f_handle;
	struct file_handle *handle __free(kfree) = NULL;
	struct handle_to_path_ctx ctx = {};
	const struct export_operations *eops;

	if (copy_from_user(&f_handle, ufh, sizeof(struct file_handle)))
		return -EFAULT;

	if ((f_handle.handle_bytes > MAX_HANDLE_SZ) ||
	    (f_handle.handle_bytes == 0))
		return -EINVAL;
	...
	retval = get_path_anchor(mountdirfd, &ctx.root);
	if (retval)
		return retval;
	...
	handle = kmalloc(struct_size(handle, f_handle, f_handle.handle_bytes),
			 GFP_KERNEL);
	if (!handle) {
		retval = -ENOMEM;
		goto out_path;
	}
	/* copy the full handle */
	*handle = f_handle;
	if (copy_from_user(&handle->f_handle,
			   &ufh->f_handle,
			   f_handle.handle_bytes)) {
		retval = -EFAULT;
		goto out_path;
	}
	...
```

函式結尾（`fs/fhandle.c:400-405`）：

```c
	retval = do_handle_to_path(handle, path, &ctx);

out_path:
	path_put(&ctx.root);
	return retval;
}
```

以及結構定義：

```c
/* include/linux/fs.h:1254-1259（v6.18） */
struct file_handle {
	__u32 handle_bytes;
	int handle_type;
	/* file identifier */
	unsigned char f_handle[] __counted_by(handle_bytes);
};
```

**問題**：

1. `handle` 在哪裡被釋放？列出所有離開函式的路徑，說明每條路徑上 `handle` 和 `ctx.root` 各自怎麼處理。
2. `ufh->handle_bytes` 被 user 讀了兩次（第一次 `copy_from_user` 整個 header，第二次複製資料時，user 那邊的記憶體可能已被另一個 thread 改掉）。這段程式碼有 double-fetch 漏洞嗎？為什麼？
3. `struct_size(handle, f_handle, f_handle.handle_bytes)` 在這裡算出多少的上限？就算不用 `struct_size`，這裡會溢位嗎？
4. `__counted_by(handle_bytes)` 告訴編譯器和讀者什麼？

**提示**：[Ch 17](./17-kernel-error-conventions.md) 的 `__free`；[Ch 18](./18-annotations-and-sparse.md) 的 `__user`；[Ch 19](./19-kernel-memory-allocation.md) 的溢位防護；[Ch 15](./15-kernel-c-gnu-extensions.md) 的 flexible array。`rg -n 'define MAX_HANDLE_SZ' include/`。

<details>
<summary>點開解答</summary>

**逐行解碼**

- `struct file_handle __user *ufh`：`ufh` 指向 user space，不能直接解參考，必須用 `copy_from_user`（sparse 會檢查，Ch 18）。
- `struct file_handle *handle __free(kfree) = NULL;`：scope 結束時自動 `kfree(handle)`；初始化成 NULL，所以提早 return 時 `kfree(NULL)` 是無害的（`DEFINE_FREE(kfree, ...)` 還檢查了 `IS_ERR_OR_NULL`）。
- 第一次 `copy_from_user`：只複製固定大小的 header（`handle_bytes`、`handle_type`）到 stack 上的 `f_handle`。
- 上界檢查：`handle_bytes` 必須在 `(0, MAX_HANDLE_SZ]`。
- `get_path_anchor`：取得 `ctx.root`（一份 `struct path` 參考）。從這一行起，離開函式都要 `path_put`——所以之後的錯誤都 `goto out_path`，而之前的錯誤直接 `return`。
- `kmalloc(struct_size(...), GFP_KERNEL)`：header + 變長資料；`GFP_KERNEL` 表示可睡（syscall 路徑）。
- `*handle = f_handle;`：把**已驗證過的** header 複製進 heap 物件。
- 第二次 `copy_from_user`：複製長度用的是 stack 上的 `f_handle.handle_bytes`，不是重新從 user 讀。

**問題回答**

1. 路徑分兩類：
   - `get_path_anchor` 之前的 `return -EFAULT`/`-EINVAL`：`handle` 是 NULL，`__free` 呼叫 `kfree(NULL)` 無事；`ctx.root` 還沒拿，不用還。
   - 之後的所有路徑都走 `out_path:` → `path_put(&ctx.root)` → `return`，然後 `handle` 在函式的 `}` 由 `__free` 自動 `kfree`（無論成功或失敗）。成功路徑上 `do_handle_to_path` 只是**使用** `handle`，沒有取得它的所有權，所以 `handle` 一律在這裡釋放。
   這就是 cleanup.h 帶來的變化：`handle` 的釋放點是**宣告所在 scope 的結尾**，你在程式碼裡看不到 `kfree`。
2. 沒有。double-fetch 漏洞的形狀是「第一次讀長度做檢查，第二次再讀一次長度拿來用」。這裡長度只從 user 讀了一次（存在 `f_handle`），之後的檢查、配置、複製都用這個 kernel 內的副本；第二次 `copy_from_user` 讀的是資料本體，user 改掉資料只會讓 handle 內容變成垃圾，不會影響長度。另外 `*handle = f_handle` 用的也是已驗證的副本，而不是把 user 的 header 再複製一次——如果寫成 `copy_from_user(handle, ufh, sizeof(*handle) + f_handle.handle_bytes)`，`handle->handle_bytes` 就可能和檢查過的值不同，而 `__counted_by` 會讓後續存取依賴那個被竄改的值。
3. 上限是 `sizeof(struct file_handle) + MAX_HANDLE_SZ`（8 + 128 bytes；`MAX_HANDLE_SZ` 在 `include/linux/exportfs.h` 定義為 128）。因為前面已檢查 `handle_bytes <= MAX_HANDLE_SZ`，不用 `struct_size` 也不會溢位；`struct_size` 在這裡是**防禦性的**，讓審查者不需要往上追上界檢查。
4. 告訴編譯器 `f_handle[]` 的元素個數存在 `handle_bytes` 欄位裡。開了 `CONFIG_UBSAN_BOUNDS` 或 `CONFIG_FORTIFY_SOURCE` 時，對 `f_handle[i]` 的存取會在執行期檢查 `i < handle_bytes`。對讀者而言，它把「這個陣列有多長」從註解升級成可檢查的宣告。

**用到**：Ch 15（flexible array）、Ch 17（`__free`、goto 收尾）、Ch 18（`__user`、`__counted_by`）、Ch 19（`struct_size`、GFP）。
**鴻溝**：慣用法（`__free` 讓釋放點隱形）、脈絡（double-fetch 是一整類歷史漏洞）。
**驗證**：
```
$ rg -n 'define MAX_HANDLE_SZ' include/
$ git log --oneline -3 -- fs/fhandle.c
$ git log -L '/^static int handle_to_path/,/^}/:fs/fhandle.c' --oneline | grep -m3 -i free
```
第三個指令找 `__free(kfree)` 是哪個 commit 引入的（`git log -L` 第一次在 blobless clone 上會比較慢）。

</details>

---

## 第 7 段 ★★ — 雙重檢查、搶輸就丟掉

```c
/* fs/pidfs.c:900-931（v6.18） */
int pidfs_register_pid(struct pid *pid)
{
	struct pidfs_attr *new_attr __free(kfree) = NULL;
	struct pidfs_attr *attr;

	might_sleep();

	if (!pid)
		return 0;

	attr = READ_ONCE(pid->attr);
	if (unlikely(attr == PIDFS_PID_DEAD))
		return PTR_ERR(PIDFS_PID_DEAD);
	if (attr)
		return 0;

	new_attr = kmem_cache_zalloc(pidfs_attr_cachep, GFP_KERNEL);
	if (!new_attr)
		return -ENOMEM;

	/* Synchronize with pidfs_exit(). */
	guard(spinlock_irq)(&pid->wait_pidfd.lock);

	attr = pid->attr;
	if (unlikely(attr == PIDFS_PID_DEAD))
		return PTR_ERR(PIDFS_PID_DEAD);
	if (unlikely(attr))
		return 0;

	pid->attr = no_free_ptr(new_attr);
	return 0;
}
```

```c
/* fs/pidfs.c:29（v6.18） */
#define PIDFS_PID_DEAD ERR_PTR(-ESRCH)
```

**問題**：

1. `pid->attr` 可能有哪三種狀態？`PIDFS_PID_DEAD` 為什麼用 `ERR_PTR` 而不是 NULL 或一個特殊位址？
2. 為什麼第一次讀用 `READ_ONCE`，第二次讀（在 `guard` 之後）不用？
3. `new_attr` 在四個 `return` 上各有什麼命運？
4. `new_attr` 是用 `kmem_cache_zalloc` 配的，卻用 `__free(kfree)` 釋放。這是 bug 嗎？
5. 這個「先不拿鎖檢查、配置、再拿鎖檢查一次」的結構叫什麼？為什麼不一開始就拿鎖？

**提示**：[Ch 17](./17-kernel-error-conventions.md) 的 `guard`、`no_free_ptr`；[Ch 19](./19-kernel-memory-allocation.md) 最後一節；[Ch 21](./21-read-once-and-barriers.md) 的 marked access。

<details>
<summary>點開解答</summary>

**逐行解碼**

- `might_sleep()`：明說這個函式會睡（開 `CONFIG_DEBUG_ATOMIC_SLEEP` 時會在 atomic context 呼叫時警告）。和下面的 `GFP_KERNEL` 互相印證。
- `attr = READ_ONCE(pid->attr)`：不拿鎖讀，標記為 lockless 並行存取。
- 三種狀態判斷：`PIDFS_PID_DEAD`（pid 已死）→ 回 `-ESRCH`；非 NULL → 已經有人註冊過，回 0；NULL → 需要配置。
- `kmem_cache_zalloc(pidfs_attr_cachep, GFP_KERNEL)`：在**鎖外**配置。
- `guard(spinlock_irq)(&pid->wait_pidfd.lock)`：拿鎖並關中斷，scope 結束（任何 `return`）時自動解鎖。註解點名同步對象是 `pidfs_exit()`。
- 鎖內再讀一次 `pid->attr`，因為在「第一次檢查」和「拿到鎖」之間別人可能改了它。
- `pid->attr = no_free_ptr(new_attr)`：把 `new_attr` 設成 NULL 並回傳原值，`__free` 在 scope 結束時看到 NULL 就不釋放——所有權轉移給 `pid`。

**問題回答**

1. NULL（尚未註冊）、有效指標（已註冊）、`PIDFS_PID_DEAD`（pid 已退出，不能再註冊）。用 `ERR_PTR(-ESRCH)` 當哨兵值有兩個好處：它保證不會和任何有效的 kernel 指標衝突（落在最後一頁，Ch 17）；而且錯誤碼就編在裡面，`PTR_ERR(PIDFS_PID_DEAD)` 直接得到 `-ESRCH`，不需要另外定義對應關係。
2. 第一次讀不拿鎖，`pidfs_exit` 可能在別的 CPU 上同時寫這個欄位，所以要 marked access：防止編譯器重讀（讀兩次得到不同值，一次判斷非 NULL、一次拿來用）或撕裂，也讓 KCSAN 知道這是刻意的 race。第二次讀在 `wait_pidfd.lock` 內，寫端（`pidfs_exit`）也拿同一把鎖，鎖本身提供順序與互斥，普通存取就夠了。
3. - `!pid` 的 return：`new_attr` 還是 NULL，`kfree(NULL)` 無事。
   - 鎖外的兩個 return：同上。
   - `-ENOMEM`：配置失敗，`new_attr` 是 NULL。
   - 鎖內的兩個 return（DEAD 或別人搶先）：`new_attr` 有值，scope 結束時**先解鎖還是先 free？** cleanup 變數依宣告的**相反順序**清理，`guard` 宣告在 `new_attr` 之後，所以先解鎖、再 `kfree(new_attr)`——free 在鎖外。
   - 成功：`no_free_ptr` 把 `new_attr` 清成 NULL，不釋放。
4. v6.4 起不是。`Documentation/core-api/memory-allocation.rst` 明文允許用 `kfree` 釋放 `kmem_cache_alloc` 的物件（commit `ae65a5211d90`）。在 v6.4 以前的 kernel 上這樣寫才是錯的。這也是「讀碼時要知道自己讀的是哪個版本」的例子。
5. 這是 double-checked initialization（雙重檢查）的一種：快速路徑不拿鎖（大多數呼叫時 `attr` 已存在，直接 return），只有需要初始化時才付拿鎖的成本。不一開始拿鎖還有一個硬理由：`GFP_KERNEL` 配置會睡，不能在 spinlock 內做。所以結構只能是「鎖外配置 → 鎖內確認並發佈 → 搶輸就丟掉自己配的那份」。

**用到**：Ch 17（`ERR_PTR` 哨兵、`guard`、`__free`、`no_free_ptr`）、Ch 19（GFP、`kmem_cache_zalloc`、kfree 規則）、Ch 21（`READ_ONCE`）。
**鴻溝**：慣用法（cleanup.h 的清理順序）、脈絡（v6.4 的 kfree 規則改變）。
**驗證**：
```
$ rg -n 'PIDFS_PID_DEAD' fs/pidfs.c
$ rg -n 'define DEFINE_GUARD\b|define guard\(' include/linux/cleanup.h
```
第一個找到 `pidfs_exit` 寫入 `PIDFS_PID_DEAD` 的位置，確認它也拿 `wait_pidfd.lock`；第二個讀 `guard` 的定義，確認它用 `__attribute__((cleanup))` 實作。清理順序由 GCC 的 cleanup attribute 語意決定（後宣告者先清理），`include/linux/cleanup.h` 開頭的註解有說明。

</details>

---

## 第 8 段 ★★★ — 最後一份參考還掉之後

```c
/* fs/file_table.c:494-546（v6.18，節錄） */
static void ____fput(struct callback_head *work)
{
	__fput(container_of(work, struct file, f_task_work));
}

static DECLARE_DELAYED_WORK(delayed_fput_work, delayed_fput);
...
static void __fput_deferred(struct file *file)
{
	struct task_struct *task = current;

	if (unlikely(!(file->f_mode & (FMODE_BACKING | FMODE_OPENED)))) {
		file_free(file);
		return;
	}

	if (likely(!in_interrupt() && !(task->flags & PF_KTHREAD))) {
		init_task_work(&file->f_task_work, ____fput);
		if (!task_work_add(task, &file->f_task_work, TWA_RESUME))
			return;
		/*
		 * After this task has run exit_task_work(),
		 * task_work_add() will fail.  Fall through to delayed
		 * fput to avoid leaking *file.
		 */
	}

	if (llist_add(&file->f_llist, &delayed_fput_list))
		schedule_delayed_work(&delayed_fput_work, 1);
}

void fput(struct file *file)
{
	if (unlikely(file_ref_put(&file->f_ref)))
		__fput_deferred(file);
}
EXPORT_SYMBOL(fput);
```

**問題**：

1. 從 `fput` 被呼叫到 `__fput` 真正執行，有哪幾條路徑？分別在什麼條件下走？
2. `____fput` 只拿到一個 `struct callback_head *`，它怎麼找回 `struct file`？為什麼 `f_task_work` 和 `f_llist` 可以放在同一個 union 裡（看 `include/linux/fs.h:1243-1248`）？
3. 為什麼「沒有 `FMODE_OPENED`」的 file 可以直接 `file_free`，不用延後？
4. `llist_add` 的回傳值是什麼意思？為什麼只有它回傳 true 時才 `schedule_delayed_work`？
5. `DECLARE_DELAYED_WORK` 和 Ch 22 的 `DEFINE_*` 家族是同一類巨集嗎？

**提示**：[Ch 20](./20-refcount-and-lifetime.md) 的「死亡」一節與 ftrace 實驗；[Ch 16](./16-container-of.md)；`rg -n 'bool llist_add\b|static inline bool llist_add' include/linux/llist.h`；`rg -n 'define DECLARE_DELAYED_WORK' include/`。

<details>
<summary>點開解答</summary>

**逐行解碼**

- `____fput`：task_work 的 callback 型別固定是 `void (*)(struct callback_head *)`。`container_of(work, struct file, f_task_work)` 從內嵌的 `callback_head` 回到 `struct file`。
- `DECLARE_DELAYED_WORK(delayed_fput_work, delayed_fput)`：定義一個靜態初始化好的 `struct delayed_work` 變數，執行函式是 `delayed_fput`。
- `__fput_deferred`：
  - 沒有 `FMODE_OPENED` 也不是 backing file → 直接 `file_free`。
  - 不在中斷、不是 kernel thread → 掛 task_work，`TWA_RESUME` 表示在返回 user-space 前執行。成功就結束。
  - 掛失敗（task 正在退出，已經跑過 `exit_task_work`）或在中斷／kernel thread 中 → 加進全域的 lockless 鏈結串列 `delayed_fput_list`，必要時排程 delayed work。
- `fput`：`file_ref_put` 回傳 true 代表這是最後一份參考，才進入延後拆解。`EXPORT_SYMBOL`（非 GPL）：任何模組都能用。

**問題回答**

1. 三條：(a) task_work 路徑：一般 process context 的 `fput`，`__fput` 在該 task 返回 user-space（或退出時的 `exit_task_work`）時執行——Ch 20 的 ftrace 看到的 `__fput <- task_work_run`，來自 `do_exit`；(b) delayed work 路徑：在中斷 context、kernel thread、或 task 已無法再加 task_work 時，`__fput` 在 workqueue 的 worker thread 裡執行；(c) 不在這段裡但要知道的：`close(2)` 走 `fput_close_sync`，在 syscall 內同步呼叫 `__fput`（ftrace 看到的 `__fput <- __x64_sys_close`）。另外沒有 `FMODE_OPENED` 的 file 根本不走 `__fput`，直接 `file_free`。
2. `container_of` 用 `offsetof(struct file, f_task_work)` 回推外層位址。union 裡的四個成員生命週期互斥：`f_ra`（readahead 狀態）只在檔案開著時用；`f_task_work` 與 `f_llist` 只在最後一份參考還掉後、等待 `__fput` 時用，而且一個 file 一次只會走其中一條延後路徑；`f_freeptr` 只在物件回到 slab 後用。最後一份參考消失時沒有人會再讀 readahead 狀態，所以可以覆寫。
3. `FMODE_OPENED` 是在 `do_dentry_open` 成功後才設的。沒有它，代表這個 file 從來沒有成功開啟過——沒有呼叫過 `->open`，沒有拿 dentry/mount 的參考，沒有掛 epoll 或 lock。`__fput` 要做的那一長串清理全部不需要，只剩 `file_free` 要還的 cred 與計數，那些可以在任何 context 做。
4. `llist_add` 在加入前串列為空時回傳 true（`include/linux/llist.h` 的 kernel-doc：「Returns true if the list was empty prior to adding this entry」）。只有「第一個加入者」需要排程 work；後面加入的人知道 work 已經排了、還沒執行，`delayed_fput` 執行時會用 `llist_del_all` 一次把整串拿走。這避免了每次都 `schedule_delayed_work` 的成本，也不需要鎖。
5. 是同一類：「定義加初始化」。命名上 `DECLARE_*` 在這裡其實是**定義**（它產生一個帶初始值的變數），kernel 的 `DECLARE_`/`DEFINE_` 命名並不完全一致——例如 `DECLARE_WAIT_QUEUE_HEAD`、`DECLARE_DELAYED_WORK` 都是定義。讀碼時看展開，不要靠前綴判斷是宣告還是定義。

**用到**：Ch 16（`container_of`）、Ch 20（`file_ref_put`、延後拆解）、Ch 22（`DECLARE_*`、`EXPORT_SYMBOL`）、Ch 11（生命週期 union）、Ch 32 預告（task_work、workqueue）。
**鴻溝**：間接（callback 經 task_work / workqueue 呼叫）、領域（task_work 的執行時機）。
**驗證**：Ch 20 的 ftrace 腳本，把 filter 換成 `____fput delayed_fput`，在 guest 裡跑一個會在 kernel thread 中放掉檔案的操作，看兩個函式各自被誰呼叫。
```
$ rg -n 'define DECLARE_DELAYED_WORK' include/linux/workqueue.h
$ rg -n 'FMODE_OPENED' fs/open.c | head -3
```

</details>

---

## 第 9 段 ★★★ — 什麼時候可以不拿參考

```c
/* fs/file.c:1153-1178（v6.18） */
static inline struct fd __fget_light(unsigned int fd, fmode_t mask)
{
	struct files_struct *files = current->files;
	struct file *file;

	/*
	 * If another thread is concurrently calling close_fd() followed
	 * by put_files_struct(), we must not observe the old table
	 * entry combined with the new refcount - otherwise we could
	 * return a file that is concurrently being freed.
	 *
	 * atomic_read_acquire() pairs with atomic_dec_and_test() in
	 * put_files_struct().
	 */
	if (likely(atomic_read_acquire(&files->count) == 1)) {
		file = files_lookup_fd_raw(files, fd);
		if (!file || unlikely(file->f_mode & mask))
			return EMPTY_FD;
		return BORROWED_FD(file);
	} else {
		file = __fget_files(files, fd, mask);
		if (!file)
			return EMPTY_FD;
		return CLONED_FD(file);
	}
}
```

**問題**：

1. `files->count == 1` 代表什麼？為什麼這個條件成立時可以借用而不 get？
2. 註解說 `atomic_read_acquire()` 和 `put_files_struct()` 裡的 `atomic_dec_and_test()` 配對。去找到 `put_files_struct`，畫出兩欄圖：另一個 thread 做了什麼、這裡讀到什麼、如果只用普通的 `atomic_read` 會看到什麼危險的組合？
3. `files->count` 為 1 的瞬間之後，有沒有可能變成 2（例如另一個 thread 被建立而共用 fd table）？如果可能，借用還安全嗎？
4. 這個函式回傳 `struct fd`，第 3 段的哪個旗標在兩個分支上分別被設定？

**提示**：[Ch 20](./20-refcount-and-lifetime.md) 的 borrowed/cloned 一節；[Ch 21](./21-read-once-and-barriers.md) 的「看到 barrier 找另一半」與隱含 barrier（有回傳值的 atomic 是 full barrier）。`rg -n '^void put_files_struct' -A15 fs/file.c`。

<details>
<summary>點開解答</summary>

**逐行解碼**

- `current->files`：目前 task 的 fd table。多個 thread（`CLONE_FILES`）可以共用同一個 `files_struct`，`files->count` 是共用者的數量。
- `atomic_read_acquire(&files->count) == 1`：帶 acquire 語意地讀共用數。
- 成立時：`files_lookup_fd_raw` 直接查表，不加參考，回傳 `BORROWED_FD`。
- 不成立時：`__fget_files` 走 RCU 查找並 `file_ref_get`（就是 `__fget_files_rcu`，[Ch 20](./20-refcount-and-lifetime.md)），回傳 `CLONED_FD`。
- `mask`：呼叫者不想要的 `f_mode` 位元（例如 `FMODE_PATH`），命中就當作沒有。

**問題回答**

1. 只有目前這個 task 在用這張 fd table。在這個 syscall 執行期間，能 close 這個 fd 的只有自己，而自己正在執行這個 syscall、不會同時 close。所以 fd table 持有的那一份參考在整個 syscall 期間都在，借用它是安全的——前提是遵守函式上方註解的三條規則（不能在 syscall 結束後還記著、中間不能 `filp_close`、中間不能 clone）。
2. `put_files_struct`（`fs/file.c`）在 `atomic_dec_and_test(&files->count)` 成功時關閉並釋放整張表裡的檔案。情境：另一個 thread B 原本共用這張表，正在退出：

   ```
   thread B（退出中）                        thread A（本函式）
   ─────────────────                        ─────────────────
   close_fd(fd) → 表格項目清空、fput
   ...
   atomic_dec_and_test(&files->count)  ──►  atomic_read_acquire(&files->count) 讀到 1
     （full barrier：之前的表格修改                  （acquire：之後的查表
       全部在 dec 之前可見）                          一定看到 B 在 dec 之前做的修改）
                                            files_lookup_fd_raw(files, fd)
   ```

   保證：如果 A 讀到 B 減完之後的 1，A 接下來查表一定看得到 B 在減之前做的 `close_fd`（表格項目已清空），不會讀到「舊的表格項目 + 新的計數 1」這個組合。如果用沒有順序保證的普通讀取，在弱記憶體架構上，A 可能先讀到計數 1（「沒人共用了，可以借用」），卻讀到表格裡尚未清掉的舊指標——借用一個 B 已經 `fput` 掉、正在被釋放的 file，就是 use-after-free。這裡的另一半是**隱含的**：`atomic_dec_and_test` 有回傳值，屬於 full barrier（`Documentation/atomic_t.txt`）。
3. 能讓共用數從 1 變成 2 的，只有「共用這張表的 task 自己去 clone 一個 `CLONE_FILES` 的 thread」。而能這樣做的只有 A 自己——其他 task 沒有這張表的參考，無法增加它的計數。A 正在執行這個 syscall，所以在借用期間不會 clone。這正是註解規則 3「You must not clone the current task in between」存在的原因。
4. 借用分支：`BORROWED_FD`，不設 `FDPUT_FPUT`，`fdput` 時不做事。get 分支：`CLONED_FD`，設 `FDPUT_FPUT`，`fdput` 時 `fput`。（`FDPUT_POS_UNLOCK` 由外層的 `fdget_pos` 視情況加上。）

**用到**：Ch 20（參考的持有者推理、borrowed/cloned）、Ch 21（acquire、隱含 barrier、找另一半）、本練習第 3 段。
**鴻溝**：領域（fd table 共用的語意）、脈絡（為什麼要 acquire，答案在另一個函式）。
**驗證**：
```
$ rg -n '^void put_files_struct' -A15 fs/file.c
$ git log --format='%h %ad %s' --date=short -L '/^static inline struct fd __fget_light/,/^}/:fs/file.c' | grep -i acquire
```
第二個指令找出 `atomic_read_acquire` 是哪個 commit 引入的；讀它的 commit message，看當初回報的是什麼 race（`git log -L` 在 blobless clone 上第一次會比較慢）。

</details>

---

## 第 10 段 ★★★ — 跨兩個檔案的 barrier

讀端：

```c
/* fs/pipe.c:267-295（v6.18，節錄） */
static ssize_t
anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)
{
	...
	mutex_lock(&pipe->mutex);
	...
	for (;;) {
		/* Read ->head with a barrier vs post_one_notification() */
		unsigned int head = smp_load_acquire(&pipe->head);
		unsigned int tail = pipe->tail;
```

寫端：

```c
/* kernel/watch_queue.c:97-133（v6.18，節錄） */
static bool post_one_notification(struct watch_queue *wqueue,
				  struct watch_notification *n)
{
	void *p;
	struct pipe_inode_info *pipe = wqueue->pipe;
	struct pipe_buffer *buf;
	struct page *page;
	unsigned int head, tail, note, offset, len;
	bool done = false;

	spin_lock_irq(&pipe->rd_wait.lock);

	head = pipe->head;
	tail = pipe->tail;
	if (pipe_full(head, tail, pipe->ring_size))
		goto lost;
	...
	buf = pipe_buf(pipe, head);
	buf->page = page;
	buf->private = (unsigned long)wqueue;
	buf->ops = &watch_queue_pipe_buf_ops;
	buf->offset = offset;
	buf->len = len;
	buf->flags = PIPE_BUF_FLAG_WHOLE;
	smp_store_release(&pipe->head, head + 1); /* vs pipe_read() */
```

**問題**：

1. 畫出兩欄圖，寫出這一對 barrier 提供的保證（「如果讀端看到 X，它一定看到 Y」）。
2. 一般的 `pipe_write` 也會推進 `pipe->head`，為什麼它不需要 `smp_store_release`？（看它拿什麼鎖。）
3. 如果把 `smp_store_release` 換成普通的 `pipe->head = head + 1;`，在 x86 和 ARM64 上各會怎樣？請用 Ch 21 的實驗結果與 barrier 實作佐證。
4. 讀端的 `pipe->tail` 是普通讀取，為什麼不需要 barrier？
5. 這一對 barrier 對應 LKMM 的哪一種 litmus 模式？在 tree 裡找到對應的 litmus test 檔名。

**提示**：[Ch 21](./21-read-once-and-barriers.md) 例一（就是這一對）、x86 與 ARM64 的 barrier 實作；`rg -n 'mutex_lock\(&pipe->mutex\)|__pipe_lock' fs/pipe.c`；`ls tools/memory-model/litmus-tests/ | grep MP`。

<details>
<summary>點開解答</summary>

**逐行解碼**

- 讀端（pipe 的 `read_iter`，這個版本叫 `anon_pipe_read`）：拿 `pipe->mutex`，迴圈裡用 `smp_load_acquire` 讀 `head`，用普通讀取讀 `tail`。
- 寫端（watch queue 的通知投遞）：拿 `pipe->rd_wait.lock`（spinlock，關中斷），**不拿** `pipe->mutex`；填好 `pipe_buf(pipe, head)` 指向的 slot 的每個欄位，最後用 `smp_store_release` 推進 `head`。

**問題回答**

1. ```
   post_one_notification（寫端，持 rd_wait.lock）   anon_pipe_read（讀端，持 pipe->mutex）
   ──────────────────────────────────────────      ─────────────────────────────────────
   buf->page = page
   buf->ops = &watch_queue_pipe_buf_ops
   buf->offset / len / flags = ...
   smp_store_release(&pipe->head, head+1) ──────►  head = smp_load_acquire(&pipe->head)
                                                    if (head != tail)
                                                        讀 pipe_buf(pipe, tail)->ops / page / len
   ```
   保證：如果讀端讀到的 `head` 已經包含寫端推進的那一格，那麼讀端在 acquire 之後對那一格 `buf` 的任何讀取，都會看到寫端在 release 之前寫入的值。換句話說：**看到新的 head，就一定看到填好的 slot**。
2. `pipe_write` 和 `pipe_read` 都拿 `pipe->mutex`。mutex 的 unlock 是 release、lock 是 acquire，寫端解鎖前的所有寫入，讀端拿到鎖之後都看得到——鎖本身就提供了這一對順序。`post_one_notification` 不拿 `pipe->mutex`（它可能在不能睡的 context 被呼叫，而 mutex 會睡），只拿 `rd_wait.lock`，和讀端**沒有共用的鎖**，所以要自己用 barrier 建立順序。這種「同一個資料結構有兩種寫端、各拿不同的鎖」的情況，是 barrier 出現的典型原因。
3. **x86**：`smp_store_release` 在 x86 上是 `barrier(); WRITE_ONCE(*p, v);`（`arch/x86/include/asm/barrier.h:59-64`），不產生任何指令；TSO 本來就不重排 store→store。所以換成普通賦值後，CPU 層面不會出錯，但**編譯器**可以把 `pipe->head = head + 1` 移到 `buf->...` 的寫入之前（它們是不同變數），丟掉 `barrier()` 就失去了這層保護。**ARM64**：`smp_store_release` 是 `stlr` 指令（`arch/arm64/include/asm/barrier.h:151`），換成普通 `str` 後，CPU 可以讓 `head` 的寫入比 `buf->ops` 的寫入先被其他 CPU 看到。讀端看到新的 head、讀到舊的 `buf->ops`，接著呼叫 `pipe_buf_release` 或 `pipe_buf_confirm` 時透過一個舊的（甚至是另一種型別的）ops 指標做間接呼叫——這在 x86 筆電上永遠測不出來。Ch 21 的 `acqrel.c` 組語顯示同樣的 C11 release store 在 x86 是 `movl`、在 ARM64 是 `stlr`。
4. `tail` 只被讀端自己（以及同樣拿 `pipe->mutex` 的其他讀取者）修改；寫端 `post_one_notification` 只**讀** `tail` 來判斷滿不滿，從不寫它。讀端在 mutex 內讀自己這邊維護的 `tail`，不涉及與寫端的順序。
5. Message Passing（MP）：P0 寫資料再 release 旗標，P1 acquire 旗標再讀資料。對應 `tools/memory-model/litmus-tests/MP+pooncerelease+poacquireonce.litmus`，結論「Result: Never」。

**用到**：Ch 21（全章）、Ch 20（ops 指標的生命週期）、Ch 30 預告（各自拿什麼鎖）、Ch 28 預告（`buf->ops` 是 ops table）。
**鴻溝**：脈絡（另一半在 `kernel/watch_queue.c`）、領域（memory model、鎖與 barrier 的關係）。
**驗證**：
```
$ rg -n 'pipe->head' fs/pipe.c kernel/watch_queue.c
$ ls ~/code_reading/linux/tools/memory-model/litmus-tests/ | grep '^MP'
```
第一個列出所有寫 `pipe->head` 的位置，確認除了 `post_one_notification` 之外的寫端都在 `pipe->mutex` 內。若安裝了 herd7，可以對上面的 litmus 檔求解（本課環境未安裝，未實測）。

</details>

---

## 測試用例

把這張表當作自評表：每一列是一個你應該能從程式碼裡讀出的事實。

| 段 | 你應該讀出的事實 | 錯誤解讀的典型樣子 |
|---|---|---|
| 1 | `backing_file(f)` 回傳外層物件，與成員位置無關 | 「它把 `file` 強制轉型成 `backing_file`」 |
| 2 | 失敗一律回傳 `ERR_PTR`，從不回 NULL | 「呼叫者檢查 `if (!f)` 就好」 |
| 3 | borrowed 不增加參考，cloned 增加；旗標在指標低位 | 「`struct fd` 是一個 fd 號碼」 |
| 4 | 配置在鎖外是因為 `GFP_KERNEL` 會睡 | 「順序無所謂，只是風格」 |
| 5 | `init_pipe_fs` 由 initcall 級別 5 在開機時呼叫 | 「沒有呼叫者，是死碼」 |
| 6 | 長度只從 user 讀一次；`handle` 在 scope 結尾自動釋放 | 「這裡有 double-fetch」或「`handle` 洩漏了」 |
| 7 | 搶輸時自己配的那份被丟掉，且在解鎖之後才 free | 「搶輸時 `new_attr` 洩漏」 |
| 8 | 拆解延後到 task_work 或 workqueue；union 成員生命週期互斥 | 「`fput` 返回時 `->release` 已經執行完」 |
| 9 | 借用的安全性來自「只有自己能改這張表」+ acquire 配對 | 「`files->count == 1` 只是效能最佳化，沒有並行問題」 |
| 10 | 看到新 head 就看到填好的 slot；x86 測不出漏掉的 barrier | 「x86 上不需要 `smp_store_release`」 |

## 延伸挑戰（加分）

- **第 11 段（加分，★★★★）**：讀 `fs/file.c:989` 起的 `__fget_files_rcu` 全文。回答：(a) `array_index_mask_nospec` 在防什麼？（提示：Spectre v1，`include/linux/nospec.h:19` 的註解）(b) 為什麼 `file_ref_get` 成功之後還要重讀 `*fdentry` 與 `files->fdt`？如果 `filp` cache 不是 `SLAB_TYPESAFE_BY_RCU`，這個重讀還需要嗎？(c) 註解說「file_ref_get() gives us a full memory barrier. We only really need an 'acquire' one」——為什麼 acquire 就夠？它保護的是哪些讀取？
- **自己挑一段**：從 `kernel/` 或 `mm/` 隨機挑一個 30–60 行、用到至少三種 Part 3 技巧的函式，照本練習的格式寫一份「原文、問題、提示、解答」。能出題，代表你真的會了。
- **反向驗證**：挑第 4 段或第 7 段，寫一個 out-of-tree module（[Ch 22](./22-kernel-macro-decoding.md) 的 `M=` 方式），故意在 spinlock 內做 `GFP_KERNEL` 配置，打開 `CONFIG_DEBUG_ATOMIC_SLEEP` 重建 kernel 後在 QEMU 載入，記錄 kernel 印出的訊息。（需要重建 kernel，耗時較長；請在自己的 VM 中操作。）
- **考古**：對第 3 段的 `struct fd`，用 `git log -L` 找出它從「一個 struct 含 `file *` 與 `flags` 兩個欄位」變成「一個 `unsigned long word`」的 commit，讀 commit message，說明動機（[Ch 37](./37-git-archaeology.md) 的技巧）。

## 自我檢核

- [ ] 我能不看解答，對第 4、7 段說出每個 GFP flag 與鎖的位置之間的因果關係。
- [ ] 我能對第 6、7 段精確說出 `__free` 變數在每一條 return 路徑上的命運，包括和 `guard` 的清理順序。
- [ ] 我能對第 9、10 段畫出兩欄圖，並指出另一半 barrier 在哪個函式（包括隱含的那種）。
- [ ] 我知道第 5、8 段那些「沒有直接呼叫者」的函式是透過什麼機制被呼叫的，並能用 `nm`/`objdump`/ftrace 證明。
- [ ] 做完後，我能說出自己最常卡在哪一道鴻溝，以及 Part 4 之後該加強什麼。
- [ ] 我能說出自己的解讀與參考解答的差異，並解釋各自的依據。

Part 3 的語言層到這裡結束。你現在看得懂每一行了；接下來的問題變成：這些行操作的**資料結構**長什麼樣。kernel 最基本的資料結構是一個只有兩個指標的 struct，而它用的正是你在第 1 段練過的 `container_of`。

→ [Ch 23 侵入式鏈結串列：list_head 與 hlist](./23-list-head-hlist.md)
