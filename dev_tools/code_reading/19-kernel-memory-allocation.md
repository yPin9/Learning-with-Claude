# Ch 19 — kernel 記憶體配置：kmalloc、GFP、slab、vmalloc、devm

> **目標**：看到一行 `kzalloc(..., GFP_KERNEL_ACCOUNT)`、`kvmalloc_array(...)` 或 `kmem_cache_alloc(filp_cachep, GFP_KERNEL)`，能立刻讀出三件事：這段程式碼**處在什麼 context**（能不能睡）、這塊記憶體**歸誰、活多久、該用哪個函式還回去**、以及**長度算式有沒有溢位防護**。重點是讀，不是教你寫 driver。

> **鴻溝**：本章主要練**慣用法鴻溝**（`kcalloc`、`struct_size`、`__free(kfree)` 這些 pattern）與**領域鴻溝**（reclaim、slab、zone 這些背景）。GFP flag 的推理也碰到一點**脈絡鴻溝**：為什麼這裡不用 `GFP_KERNEL`，答案常在 git log。

> **環境**：Linux v6.18 原始碼（`~/code_reading/linux`），x86_64 defconfig + kvm_guest 建出的 kernel，QEMU（TCG）裡用 ftrace 觀察配置事件；user-space 範例用 gcc 11.4。

## 為什麼需要這個？

在 user-space，你寫 `malloc(n)`，失敗了回 `NULL`，大不了程式結束。你不需要告訴 `malloc`「我現在能不能等」，因為 user-space 的 thread 永遠可以被排程走、永遠可以等 page fault。

kernel 沒有這個奢侈。同一個配置函式可能被三種完全不同的呼叫者叫到：

- 一個正在執行 `pipe(2)` 的 process，處在 process context，可以睡，等多久都行；
- 一個拿著 spinlock 的程式碼，**不准睡**，睡了就可能 deadlock；
- 一個 interrupt handler，連「被排程走」這個概念都沒有。

更麻煩的是，記憶體不夠時 allocator 的標準動作是**回收**（reclaim）：把 page cache 寫回磁碟、把 slab 縮小、甚至呼叫 filesystem 的 callback。回收本身會睡、會做 I/O、會拿 lock。如果呼叫者自己正拿著回收過程需要的 lock，就是自己等自己。

所以 kernel 的配置函式多了一個參數：**GFP flags**（Get Free Pages flags）。它是呼叫者寫給 allocator 的一張紙條：「我現在能接受你做什麼」。對讀碼的人來說，這張紙條反過來是一份**免費的 context 情報**——寫這行的人已經替你說明了他當下的處境。

> 如果你對 user-space 的所有權慣例（誰配置誰釋放、goto 收尾）還不熟，先回看 [Ch 12 — 動態記憶體與所有權慣例](./12-memory-ownership-conventions.md)。這章是它在 kernel 的延伸。

## 先建立直覺

把 GFP flag 想成你對 allocator 說的話：

```
GFP_KERNEL   「慢慢來，記憶體不夠就去回收，要我等多久都行，
               要寫磁碟、要叫 filesystem 都可以。」
               → 說這句話的人：可以睡（process context、沒拿 spinlock）

GFP_NOFS     「可以等、可以回收，但別回頭呼叫 filesystem。」
               → 說這句話的人：自己就在 filesystem 裡，拿著 fs 的 lock

GFP_NOIO     「可以等，但別發起任何 I/O。」
               → 說這句話的人：在 block layer / 儲存路徑上

GFP_NOWAIT   「有就給，沒有就算了，我不等。」
               → 說這句話的人：不能睡，或拿著回收會需要的 lock，失敗有退路

GFP_ATOMIC   「我不能等，而且這次一定要成功，請動用緊急預備金。」
               → 說這句話的人：在 atomic context（IRQ、softirq、spinlock 內）
```

這個表格是本章的骨架。後面所有東西都在回答兩個問題：(1) 這些「話」在原始碼裡是怎麼用 bit 拼出來的，(2) 你在讀碼時怎麼反向利用它。

## GFP flag 的真身：從 gfp_types.h 讀起

`GFP_KERNEL` 不是魔法常數，是幾個 bit 的組合。v6.18 的定義：

```c
/* include/linux/gfp_types.h:377-383（v6.18） */
#define GFP_ATOMIC	(__GFP_HIGH|__GFP_KSWAPD_RECLAIM)
#define GFP_KERNEL	(__GFP_RECLAIM | __GFP_IO | __GFP_FS)
#define GFP_KERNEL_ACCOUNT (GFP_KERNEL | __GFP_ACCOUNT)
#define GFP_NOWAIT	(__GFP_KSWAPD_RECLAIM | __GFP_NOWARN)
#define GFP_NOIO	(__GFP_RECLAIM)
#define GFP_NOFS	(__GFP_RECLAIM | __GFP_IO)
#define GFP_USER	(__GFP_RECLAIM | __GFP_IO | __GFP_FS | __GFP_HARDWALL)
```

而 `__GFP_RECLAIM` 又是兩個 bit：

```c
/* include/linux/gfp_types.h:261-263（v6.18） */
#define __GFP_DIRECT_RECLAIM	((__force gfp_t)___GFP_DIRECT_RECLAIM) /* Caller can reclaim */
#define __GFP_KSWAPD_RECLAIM	((__force gfp_t)___GFP_KSWAPD_RECLAIM) /* kswapd can wake */
#define __GFP_RECLAIM ((__force gfp_t)(___GFP_DIRECT_RECLAIM|___GFP_KSWAPD_RECLAIM))
```

拆開來看，整張表其實只靠幾個 bit 決定：

| 組合 | DIRECT_RECLAIM（呼叫者自己回收、會睡） | KSWAPD_RECLAIM（叫醒背景回收） | IO | FS | HIGH（動用預備金） |
|---|---|---|---|---|---|
| `GFP_KERNEL` | ✓ | ✓ | ✓ | ✓ | |
| `GFP_NOFS` | ✓ | ✓ | ✓ | | |
| `GFP_NOIO` | ✓ | ✓ | | | |
| `GFP_NOWAIT` | | ✓ | | | |
| `GFP_ATOMIC` | | ✓ | | | ✓ |

**能不能睡，看的就是 `__GFP_DIRECT_RECLAIM` 這一個 bit**。kernel 自己也是這樣判斷的：

```c
/* include/linux/gfp.h:38-41（v6.18） */
static inline bool gfpflags_allow_blocking(const gfp_t gfp_flags)
{
	return !!(gfp_flags & __GFP_DIRECT_RECLAIM);
}
```

`gfp_types.h` 裡那段 `DOC: Useful GFP flag combinations` 是官方的語意說明，值得逐字讀一遍。其中兩句是讀碼時最常用的：

> %GFP_ATOMIC users can not sleep and need the allocation to succeed. A lower watermark is applied to allow access to "atomic reserves".
>
> %GFP_KERNEL is typical for kernel-internal allocations. The caller requires %ZONE_NORMAL or a lower zone for direct access but can direct reclaim.
>
> —— include/linux/gfp_types.h:316-322（v6.18）

注意 `GFP_ATOMIC` 那句還有後半：「The current implementation doesn't support NMI and few other strict non-preemptive contexts (e.g. raw_spin_lock)」。所以「atomic context 就用 GFP_ATOMIC」也不是萬用解，NMI 裡根本不該配置。

### `__GFP_` 開頭的修飾 bit

讀碼時你還會看到這些「加料」：

| flag | 讀出的意思 |
|---|---|
| `__GFP_ZERO` | 回傳前清零。`kzalloc` = `kmalloc` + 這個 bit |
| `__GFP_NOWARN` | 失敗不要印警告。呼叫者**預期**可能失敗、有退路（例如大小由 user 決定） |
| `__GFP_ACCOUNT` | 記到 memory cgroup 帳上。看到它 → 這塊記憶體的大小或數量**受 user 控制**，要防止某個 container 吃光 kernel 記憶體 |
| `__GFP_NOFAIL` | 永不失敗，無限重試。必須能睡。看到它 → 呼叫者真的沒有錯誤處理路徑 |
| `__GFP_RETRY_MAYFAIL` | 努力重試但最終可以失敗 |
| `__GFP_HIGHMEM` / `__GFP_MOVABLE` | 可以放在 kernel 不能直接定址的 zone、可以被搬移；通常是給 user page 用 |

`__GFP_ACCOUNT` 值得多說一句：它在 `fs/pipe.c` 幾乎每一個配置都出現，因為 pipe 的 buffer 數量是 user 用 `fcntl(F_SETPIPE_SZ)` 調的。這個 flag 本身就是一個 **beacon**（[Ch 2](./02-program-comprehension-models.md) 的術語）：看到它，你就知道「這裡有一個 user 可以放大的資源」。

## 一次真實的配置長什麼樣：fs/pipe.c

```c
/* fs/pipe.c:792-839（v6.18，節錄） */
struct pipe_inode_info *alloc_pipe_info(void)
{
	struct pipe_inode_info *pipe;
	unsigned long pipe_bufs = PIPE_DEF_BUFFERS;
	struct user_struct *user = get_current_user();
	unsigned long user_bufs;
	unsigned int max_size = READ_ONCE(pipe_max_size);

	pipe = kzalloc(sizeof(struct pipe_inode_info), GFP_KERNEL_ACCOUNT);
	if (pipe == NULL)
		goto out_free_uid;
	...
	pipe->bufs = kcalloc(pipe_bufs, sizeof(struct pipe_buffer),
			     GFP_KERNEL_ACCOUNT);

	if (pipe->bufs) {
		...
		return pipe;
	}

out_revert_acct:
	(void) account_pipe_buffers(user, pipe_bufs, 0);
	kfree(pipe);
out_free_uid:
	free_uid(user);
	return NULL;
}
```

用本章的眼光逐行讀：

1. `GFP_KERNEL_ACCOUNT` → 呼叫者可以睡（process context，正在跑 `pipe(2)` 或 `open()` 一個 FIFO），且這個物件記帳到 cgroup。
2. `kzalloc` → 整個 struct 清零，後面只初始化非零欄位；讀後面的初始化時，**沒被設定的欄位就是 0**，不是垃圾值。
3. `kcalloc(n, size, ...)` → 陣列配置，內建乘法溢位檢查（下一節細講），也清零。
4. 錯誤路徑 `kfree(pipe)` → 和 `kzalloc` 配對。成功路徑沒有 free，因為所有權交給了回傳值；對應的釋放在 `free_pipe_info()`（`fs/pipe.c:841`），那裡你會看到 `kfree(pipe->bufs); kfree(pipe);` 兩行，順序和配置相反。
5. `get_current_user()` / `free_uid(user)` 是另一對 get/put——這是 [Ch 20](./20-refcount-and-lifetime.md) 的主題。

### 在 QEMU 裡親眼看這兩次配置

光讀不夠，我們讓 kernel 自己說。在 QEMU guest 裡打開 `kmem:kmalloc` 和 `kmem:kmem_cache_alloc` 兩個 tracepoint，然後跑一個 `echo hello | cat`（shell 會建一個 pipe）：

```sh
# guest 內執行（腳本透過 ~/code_reading/qemu/run-script.sh 送進去）
T=/sys/kernel/tracing
echo 1 > $T/events/kmem/kmalloc/enable
echo 1 > $T/events/kmem/kmem_cache_alloc/enable
echo 1 > $T/tracing_on
echo hello | cat > /dev/null
echo 0 > $T/tracing_on
grep -E "alloc_pipe_info|alloc_empty_file" $T/trace
```

真實輸出（v6.18，x86_64 TCG，行尾的 `node=-1` 已截掉）：

```
sh-70 [001] ..... 4.113119: kmalloc: call_site=alloc_pipe_info+0x5e/0x220 ptr=(____ptrval____) bytes_req=176 bytes_alloc=192 gfp_flags=GFP_KERNEL_ACCOUNT|__GFP_ZERO
sh-70 [001] ..... 4.113213: kmalloc: call_site=alloc_pipe_info+0xd7/0x220 ptr=(____ptrval____) bytes_req=640 bytes_alloc=1024 gfp_flags=GFP_KERNEL_ACCOUNT|__GFP_ZERO
sh-70 [001] ..... 4.113271: kmem_cache_alloc: call_site=alloc_empty_file+0x41/0x130 ptr=(____ptrval____) name=filp bytes_req=184 bytes_alloc=192 gfp_flags=GFP_KERNEL
sh-70 [001] ..... 4.113288: kmem_cache_alloc: call_site=alloc_empty_file+0x41/0x130 ptr=(____ptrval____) name=filp bytes_req=184 bytes_alloc=192 gfp_flags=GFP_KERNEL
```

這四行把好幾件事坐實了：

- **第一行**是 `kzalloc(sizeof(struct pipe_inode_info), ...)`：176 bytes，被放進 192 bytes 的 bucket。`gfp_flags` 顯示 `GFP_KERNEL_ACCOUNT|__GFP_ZERO`——`kzalloc` 確實就是 `kmalloc` 加 `__GFP_ZERO`。
- **第二行**是 `kcalloc(16, sizeof(struct pipe_buffer), ...)`：`PIPE_DEF_BUFFERS` 是 16（`include/linux/pipe_fs_i.h:5`），`struct pipe_buffer` 是 40 bytes（用 `pahole -C pipe_buffer vmlinux` 可確認），16 × 40 = 640，進了 1024 的 bucket。浪費了 384 bytes——kmalloc 的 bucket 是 2 的冪次加上 96、192 兩個特例，這是用空間換速度的取捨。
- **第三、四行**是 `pipe(2)` 要建兩個 `struct file`（讀端、寫端），走的是**專用 cache** `filp`，不是 kmalloc。
- **`accounted=false`**：雖然帶了 `__GFP_ACCOUNT`，但這個 kernel 的 `.config` 沒開 `CONFIG_MEMCG`，所以沒有地方記帳。flag 表達的是**意圖**，實際效果取決於 config——這正是 [Ch 36](./36-kconfig-kbuild-ifdef.md) 要講的事。

## kmalloc 家族：每個名字都在說話

```c
/* include/linux/slab.h:997-1005（v6.18） */
static inline __alloc_size(1, 2) void *kmalloc_array_noprof(size_t n, size_t size, gfp_t flags)
{
	size_t bytes;

	if (unlikely(check_mul_overflow(n, size, &bytes)))
		return NULL;
	return kmalloc_noprof(bytes, flags);
}
#define kmalloc_array(...)			alloc_hooks(kmalloc_array_noprof(__VA_ARGS__))
```

```c
/* include/linux/slab.h:1044（v6.18） */
#define kcalloc(n, size, flags)		kmalloc_array(n, size, (flags) | __GFP_ZERO)
```

你會注意到 v6.18 裡每個配置函式都有一個 `_noprof` 版本，外面包著 `alloc_hooks(...)`。這是 v6.10 起的**記憶體配置剖析**（memory allocation profiling，`CONFIG_MEM_ALLOC_PROFILING`）機制：`alloc_hooks` 在每個呼叫點放一個靜態 tag，用來統計「哪一行配置了多少記憶體」。讀碼時把 `_noprof` 和 `alloc_hooks` 當透明的就好——**它不改變語意**。這是「選擇性閱讀」（[Ch 4](./04-selective-reading.md)）的典型應用：認出它是基礎設施，跳過。

`kmalloc_noprof` 本體也值得一看，它解釋了 trace 裡的 bucket：

```c
/* include/linux/slab.h:948-963（v6.18） */
static __always_inline __alloc_size(1) void *kmalloc_noprof(size_t size, gfp_t flags)
{
	if (__builtin_constant_p(size) && size) {
		unsigned int index;

		if (size > KMALLOC_MAX_CACHE_SIZE)
			return __kmalloc_large_noprof(size, flags);

		index = kmalloc_index(size);
		return __kmalloc_cache_noprof(
				kmalloc_caches[kmalloc_type(flags, _RET_IP_)][index],
				flags, size);
	}
	return __kmalloc_noprof(size, flags);
}
#define kmalloc(...)				alloc_hooks(kmalloc_noprof(__VA_ARGS__))
```

`__builtin_constant_p(size)`（[Ch 15](./15-kernel-c-gnu-extensions.md)）：如果大小在編譯期已知，就在編譯期算好 bucket index，直接打到那個 cache；否則走執行期查表。`kmalloc_type(flags, ...)` 則說明 **GFP flag 還決定了物件放在哪一組 cache**——例如帶 `__GFP_ACCOUNT` 的物件會進 `kmalloc-cg-*`，和不記帳的物件分開（前提是開了 memcg）。

整理成讀碼用的對照表：

| 你看到 | 讀出 | 配對的釋放 |
|---|---|---|
| `kmalloc(size, gfp)` | 小塊、實體連續、內容未初始化 | `kfree` |
| `kzalloc(size, gfp)` | 同上，清零 | `kfree` |
| `kmalloc_array(n, sz, gfp)` | 陣列，乘法有溢位檢查 | `kfree` |
| `kcalloc(n, sz, gfp)` | 陣列 + 清零 | `kfree` |
| `kmemdup(src, len, gfp)` | 配置 + 複製 | `kfree` |
| `kmem_cache_alloc(cache, gfp)` | 固定大小的專用物件 | `kmem_cache_free`（v6.4 起 `kfree` 也可以） |
| `vmalloc(size)` | 大塊、虛擬連續、實體不連續、**會睡** | `vfree` |
| `kvmalloc(size, gfp)` | 先試 kmalloc，失敗退到 vmalloc | `kvfree` |
| `alloc_pages(gfp, order)` | 2^order 個實體 page | `__free_pages` |
| `devm_kzalloc(dev, size, gfp)` | 綁在 device 上，driver 卸載時自動釋放 | 不用（或 `devm_kfree`） |

## 底層機制：slab、vmalloc、buddy 三層

```
                 kmalloc / kmem_cache_alloc
                           │
              ┌────────────▼────────────┐
              │  slab allocator (SLUB)  │   固定大小物件的快取
              │  kmalloc-8 … kmalloc-8k │   一個 slab = 一或多個連續 page
              │  filp, dentry, inode…   │   切成等大小的 object
              └────────────┬────────────┘
                           │ slab 用完時要新 page
 vmalloc ──┐               │
           │  ┌────────────▼────────────┐
           └─►│  buddy allocator        │   以 page 為單位，2^order
              │  alloc_pages(gfp, order)│   GFP 在這裡決定能不能 reclaim
              └────────────┬────────────┘
                           │ 記憶體不足
              ┌────────────▼────────────┐
              │  reclaim                │   DIRECT_RECLAIM：呼叫者自己做（會睡）
              │  kswapd / direct        │   KSWAPD_RECLAIM：叫醒背景執行緒
              └─────────────────────────┘
```

- **slab（v6.18 只剩 SLUB 一種實作）**：kernel 裡大部分物件都很小、大小固定、配置釋放極頻繁。slab 先拿一批 page，切成等大的格子，配置時拿一格。`kmalloc` 是一組預建好、按大小分級的 slab cache；`kmem_cache_create` 是為某個 struct 建專屬的 cache。
- **buddy**：以 page 為單位、2 的冪次大小配置實體連續記憶體。slab 的 page 從這裡來，`alloc_pages` 直接打這一層。
- **vmalloc**：從 buddy 一頁一頁拿（不要求實體連續），再在 kernel 的 vmalloc 區段建 page table 把它們拼成虛擬連續的一段。建 page table 需要配置記憶體、可能睡，所以 **vmalloc 不能在 atomic context 用**。

GFP flag 真正生效的地方在最下面兩層：slab cache 有現成格子時，根本不會碰到 reclaim，這也是為什麼「在 spinlock 裡誤用 `GFP_KERNEL`」常常測試時不出事——要等到記憶體吃緊、真的進到 direct reclaim 時才睡下去。這種 bug 要靠 `CONFIG_DEBUG_ATOMIC_SLEEP` 這類檢查才抓得到（下面踩雷集錦會再提）。

### 專用 cache：`kmem_cache_create`

`struct file` 有自己的 cache：

```c
/* fs/file_table.c:588-600（v6.18，節錄） */
void __init files_init(void)
{
	struct kmem_cache_args args = {
		.use_freeptr_offset = true,
		.freeptr_offset = offsetof(struct file, f_freeptr),
	};

	filp_cachep = kmem_cache_create("filp", sizeof(struct file), &args,
				SLAB_HWCACHE_ALIGN | SLAB_PANIC |
				SLAB_ACCOUNT | SLAB_TYPESAFE_BY_RCU);
	...
}
```

讀 `SLAB_*` flag 和讀 GFP 一樣，每個都是情報：

| flag | 讀出 |
|---|---|
| `SLAB_HWCACHE_ALIGN` | 物件對齊 cache line，避免 false sharing → 這個物件很熱 |
| `SLAB_PANIC` | 建 cache 失敗就 panic → 開機必要元件，沒有它系統不用跑了 |
| `SLAB_ACCOUNT` | 整個 cache 的物件都記 memcg 帳（等於每次配置都帶 `__GFP_ACCOUNT`） |
| `SLAB_TYPESAFE_BY_RCU` | **最重要的一個**：物件被 free 之後，記憶體在 RCU grace period 內**仍是同型別的物件**（可能已被重用成另一個 `struct file`）。看到它 → 一定有 lockless 的查找路徑，而且那條路徑必須在拿到 reference 後**重新驗證身分** |

最後一個會在 [Ch 20](./20-refcount-and-lifetime.md) 的 `__fget_files_rcu` 用到。另外，v6.18 的 `kmem_cache_create` 本身是個 `_Generic` 巨集（`include/linux/slab.h:461`），會依第三個參數的型別分派到新舊兩種 API——這是 [Ch 15](./15-kernel-c-gnu-extensions.md) 講過的 `_Generic` 在真實程式碼裡的用法。很多 cache 則用更短的 `KMEM_CACHE(struct_name, flags)`（`include/linux/slab.h:478`），它用 `#__struct` 把 struct 名字字串化當 cache 名稱。

配置與釋放的配對在 `alloc_empty_file` / `file_free`：

```c
/* fs/file_table.c:237-245（v6.18） */
	f = kmem_cache_alloc(filp_cachep, GFP_KERNEL);
	if (unlikely(!f))
		return ERR_PTR(-ENOMEM);

	error = init_file(f, flags, cred);
	if (unlikely(error)) {
		kmem_cache_free(filp_cachep, f);
		return ERR_PTR(error);
	}
```

注意 cache 函式回傳 `NULL`，但 `alloc_empty_file` 對外回傳 `ERR_PTR(-ENOMEM)`——兩套錯誤慣例在這裡交接（[Ch 17](./17-kernel-error-conventions.md)）。

## 從 GFP flag 反推 context：讀碼技巧

這是本章最實用的一招。流程：

```
看到 GFP flag
   │
   ├─ GFP_KERNEL / GFP_KERNEL_ACCOUNT / GFP_USER
   │     → 呼叫者可以睡：process context，沒拿 spinlock，沒關中斷
   │     → 推論：這個函式的所有呼叫者也都在可睡 context
   │       （否則就是 bug——這本身是一個可以驗證的假說）
   │
   ├─ GFP_NOFS / GFP_NOIO
   │     → 可以睡，但呼叫者在 fs / block 路徑上，拿著回收可能需要的 lock
   │
   ├─ GFP_NOWAIT
   │     → 要嘛不能睡，要嘛「拿著回收需要的 lock」；一定有失敗退路，去找它
   │
   └─ GFP_ATOMIC
         → atomic context：IRQ / softirq / spinlock 內 / RCU read side
         → 往上找：spin_lock、local_irq_save、rcu_read_lock、或 handler 註冊點
```

### 例一：先配置，再上鎖

```c
/* fs/pipe.c:1291-1315（v6.18，節錄） */
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
	...
	if (nr_slots < n) {
		spin_unlock_irq(&pipe->rd_wait.lock);
		kfree(bufs);
		return -EBUSY;
	}
```

三個讀點：

1. `GFP_KERNEL_ACCOUNT` 出現在 `spin_lock_irq` **之前**——這不是巧合，是刻意的結構。spinlock 內不能睡，所以配置被提到鎖外面。你在 kernel 裡會一再看到這個形狀：**鎖外配置、鎖內交換、失敗時鎖外釋放**。
2. `__GFP_NOWARN`：`nr_slots` 來自 user（`fcntl(F_SETPIPE_SZ)`），user 要一個巨大的 pipe 而失敗是預期中的事，不值得在 dmesg 印一大段 warning。
3. `kfree(bufs)` 在 `spin_unlock_irq` **之後**。`kfree` 本身可以在 atomic context 呼叫，但把它放在鎖外是好習慣，縮短臨界區。

### 例二：GFP_NOWAIT 不一定是「不能睡」

在上面同一段 trace 裡，我們也看到了這種配置：

```
sh-70 [001] ..... 4.114319: kmem_cache_alloc: call_site=anon_vma_clone+0x63/0x1c0 ... name=kmalloc-64 bytes_req=64 bytes_alloc=64 gfp_flags=GFP_NOWAIT
```

去讀 `anon_vma_clone`：

```c
/* mm/rmap.c:279-297（v6.18，節錄） */
int anon_vma_clone(struct vm_area_struct *dst, struct vm_area_struct *src)
{
	struct anon_vma_chain *avc, *pavc;
	struct anon_vma *root = NULL;

	list_for_each_entry_reverse(pavc, &src->anon_vma_chain, same_vma) {
		struct anon_vma *anon_vma;

		avc = anon_vma_chain_alloc(GFP_NOWAIT);
		if (unlikely(!avc)) {
			unlock_anon_vma_root(root);
			root = NULL;
			avc = anon_vma_chain_alloc(GFP_KERNEL);
			if (!avc)
				goto enomem_failure;
		}
		anon_vma = pavc->anon_vma;
		root = lock_anon_vma_root(root, anon_vma);
		...
```

如果你套用「GFP_NOWAIT = 不能睡」的公式，會卡住：失敗後它馬上改用 `GFP_KERNEL` 重試，所以呼叫者**明明可以睡**。而且 `lock_anon_vma_root`（`mm/rmap.c:242`）拿的是 `down_write(&root->rwsem)`——rwsem 本身就是會睡的鎖。

這是**脈絡鴻溝**。去考古（[Ch 37](./37-git-archaeology.md)）：

```
$ git log --format='%h %ad %s' --date=short -i --grep='GFP_NOWAIT' -- mm/rmap.c
adf085ff0d6f 2025-08-12 mm: remove redundant __GFP_NOWARN
dd34739c03f2 2011-06-17 mm: avoid anon_vma_chain allocation under anon_vma lock
```

`dd34739c03f2`（Linus Torvalds，進入 v3.0）的 commit message 說得很清楚：lockdep 抓到一個潛在 deadlock——**page reclaim 會去拿匿名 page 的 anon_vma lock**，而這裡正拿著那把鎖去做 `GFP_KERNEL` 配置，配置觸發 reclaim，reclaim 等這把鎖。解法是：拿著鎖時只試 `GFP_NOWAIT`（不進 direct reclaim），失敗就**先放鎖**再用 `GFP_KERNEL`。

所以更精確的讀法是：**GFP flag 告訴你「呼叫者手上有什麼東西不能讓 reclaim 碰到」**。可能是「整個 CPU 不能睡」（atomic），也可能是「我拿著 reclaim 需要的那把鎖」。

另外第一行的 commit 也順手教了一件事：`GFP_NOWAIT` 從 v6.8（commit `16f5dfbc851b`）起已經內含 `__GFP_NOWARN`，所以舊程式碼裡的 `GFP_NOWAIT | __GFP_NOWARN` 在 v6.18 的合併窗口被清掉了。你讀舊版 kernel 時會看到這種冗餘寫法，不用驚訝。

## 溢位防護：`struct_size`、`array_size`、`kcalloc`

kernel 的長度常常來自 user。`kmalloc(sizeof(*p) + n * sizeof(p->items[0]), ...)` 這種寫法，一旦 `n` 夠大，乘法繞回，配到一塊很小的記憶體，後面照 `n` 去寫——heap overflow。這是真實存在過很多次的漏洞類型（[kernel_pwn 課](../../security/kernel_pwn/README.md)有整章在講利用面）。

v6.18 的防護：

```c
/* include/linux/overflow.h:336-344（v6.18） */
static inline size_t __must_check size_mul(size_t factor1, size_t factor2)
{
	size_t bytes;

	if (check_mul_overflow(factor1, factor2, &bytes))
		return SIZE_MAX;

	return bytes;
}
```

```c
/* include/linux/overflow.h:397, 424-443（v6.18） */
#define array_size(a, b)	size_mul(a, b)

#define flex_array_size(p, member, count)				\
	__builtin_choose_expr(__is_constexpr(count),			\
		(count) * sizeof(*(p)->member) + __must_be_array((p)->member),	\
		size_mul(count, sizeof(*(p)->member) + __must_be_array((p)->member)))

#define struct_size(p, member, count)					\
	__builtin_choose_expr(__is_constexpr(count),			\
		sizeof(*(p)) + flex_array_size(p, member, count),	\
		size_add(sizeof(*(p)), flex_array_size(p, member, count)))
```

核心思想是**飽和**（saturation）而不是報錯：溢位時結果變成 `SIZE_MAX`，而 `kmalloc(SIZE_MAX)` 一定失敗回 `NULL`。這樣呼叫端不用多寫任何檢查，原本的 `if (!p) return -ENOMEM;` 就涵蓋了溢位。`__must_be_array` 則確保 `member` 真的是陣列（[Ch 22](./22-kernel-macro-decoding.md) 會拆 `ARRAY_SIZE` 的同一招）。

我們在 user-space 用同樣的語意驗證一次：

```c
/* ovf.c — 語意取自 include/linux/overflow.h，簡化版 */
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>

static inline size_t size_mul(size_t a, size_t b)
{
	size_t bytes;
	if (__builtin_mul_overflow(a, b, &bytes))
		return SIZE_MAX;            /* 飽和，而不是繞回 */
	return bytes;
}
static inline size_t size_add(size_t a, size_t b)
{
	size_t bytes;
	if (__builtin_add_overflow(a, b, &bytes))
		return SIZE_MAX;
	return bytes;
}

struct msg {
	uint32_t len;
	uint64_t items[];               /* flexible array member */
};

int main(void)
{
	/* count 來自 user（例如 ioctl 參數） */
	size_t count = (SIZE_MAX / sizeof(uint64_t)) + 2;

	size_t naive = sizeof(struct msg) + count * sizeof(uint64_t);
	size_t safe  = size_add(sizeof(struct msg),
				size_mul(count, sizeof(uint64_t)));

	printf("count           = %zu\n", count);
	printf("naive size      = %zu   <- 繞回成很小的數\n", naive);
	printf("struct_size-ish = %zu   <- SIZE_MAX，kmalloc 必定失敗\n", safe);

	/* 32-bit 乘法的經典版本：unsigned int 先乘再轉 size_t */
	unsigned int n = 0x40000001, sz = 4;
	printf("unsigned int n*sz = %u (轉成 size_t 前已經繞回)\n", n * sz);
	printf("size_mul(n, sz)   = %zu\n", size_mul(n, sz));
	return 0;
}
```

```
$ gcc -O2 -Wall -o ovf ovf.c && ./ovf
count           = 2305843009213693953
naive size      = 16   <- 繞回成很小的數
struct_size-ish = 18446744073709551615   <- SIZE_MAX，kmalloc 必定失敗
unsigned int n*sz = 4 (轉成 size_t 前已經繞回)
size_mul(n, sz)   = 4294967300
```

`naive size = 16` 是 `sizeof(struct msg)`（8，因為 `uint64_t` 要求 8 對齊）加上繞回後的 8。配 16 bytes，然後照 count 寫進去——這就是漏洞。第二組數字說明另一個常見坑：`size_mul` 的參數是 `size_t`，`unsigned int` 會在**乘之前**被提升成 64 bit，所以不會在 32 bit 裡繞回（[Ch 9](./09-expressions-integers-bits.md) 的整數提升）。

真實的使用長這樣：

```c
/* fs/fhandle.c:372-373（v6.18） */
	handle = kmalloc(struct_size(handle, f_handle, f_handle.handle_bytes),
			 GFP_KERNEL);
```

讀碼時的判斷規則：**長度算式裡有乘法、而且某個因子可能來自 user，卻沒用 `struct_size` / `array_size` / `kcalloc` / `kmalloc_array`**——停下來，往上追那個因子的來源和上界檢查。這一行在 `fhandle.c` 前面有 `f_handle.handle_bytes > MAX_HANDLE_SZ` 的檢查（`fs/fhandle.c:352`），所以就算沒有 `struct_size` 也安全；但 `struct_size` 讓審查者不用往上追就能放心。

## vmalloc 與 kvmalloc：大塊記憶體

`kmalloc` 要求實體連續。配幾十 KB 以上時，記憶體碎片化可能讓連續的 page 湊不出來。`vmalloc` 不要求實體連續，代價是要建 page table、TLB 壓力較大、而且會睡。

`kvmalloc` 是折衷：先試 kmalloc，失敗再退 vmalloc。v6.18 的實作：

```c
/* mm/slub.c:7103-7124（v6.18，節錄） */
void *__kvmalloc_node_noprof(DECL_BUCKET_PARAMS(size, b), unsigned long align,
			     gfp_t flags, int node)
{
	void *ret;

	/*
	 * It doesn't really make sense to fallback to vmalloc for sub page
	 * requests
	 */
	ret = __do_kmalloc_node(size, PASS_BUCKET_PARAM(b),
				kmalloc_gfp_adjust(flags, size),
				node, _RET_IP_);
	if (ret || size <= PAGE_SIZE)
		return ret;

	/* non-sleeping allocations are not supported by vmalloc */
	if (!gfpflags_allow_blocking(flags))
		return NULL;
	...
```

兩個讀點：`kmalloc_gfp_adjust`（`mm/slub.c:7058`）在 size 大於一頁時會**拿掉 `__GFP_DIRECT_RECLAIM`**，意思是「kmalloc 那次只是試試看，別為了湊連續記憶體去大費周章回收，反正有 vmalloc 可以退」；而退到 vmalloc 前用 `gfpflags_allow_blocking` 檢查——又是那一個 bit。`__kvmalloc_node` 的 kernel-doc 寫得直接：「GFP_NOWAIT and GFP_ATOMIC are not supported」。

釋放用 `kvfree`，它在執行期判斷位址屬於哪一區：

```c
/* mm/slub.c:7150-7156（v6.18） */
void kvfree(const void *addr)
{
	if (is_vmalloc_addr(addr))
		vfree(addr);
	else
		kfree(addr);
}
```

真實使用：fd table 的大小跟著 process 開的 fd 數量長，可以很大，所以用 `kvmalloc_array`：

```c
/* fs/file.c:215-221, 237-242（v6.18，節錄） */
	fdt = kmalloc(sizeof(struct fdtable), GFP_KERNEL_ACCOUNT);
	if (!fdt)
		goto out;
	fdt->max_fds = nr;
	data = kvmalloc_array(nr, sizeof(struct file *), GFP_KERNEL_ACCOUNT);
	if (!data)
		goto out_fdt;
	...
out_arr:
	kvfree(fdt->fd);
out_fdt:
	kfree(fdt);
```

同一個函式裡 `kmalloc`↔`kfree`、`kvmalloc_array`↔`kvfree` 各自配對。**讀碼時配對錯誤是一個真 bug 訊號**：`kvmalloc` 配的用 `kfree` 釋放，在 fallback 到 vmalloc 的那一次就會出事。

## alloc_pages：直接要 page

```c
/* fs/pipe.c:114-125（v6.18） */
static struct page *anon_pipe_get_page(struct pipe_inode_info *pipe)
{
	for (int i = 0; i < ARRAY_SIZE(pipe->tmp_page); i++) {
		if (pipe->tmp_page[i]) {
			struct page *page = pipe->tmp_page[i];
			pipe->tmp_page[i] = NULL;
			return page;
		}
	}

	return alloc_page(GFP_HIGHUSER | __GFP_ACCOUNT);
}
```

`GFP_HIGHUSER` 是讀點：pipe 的資料 page 不需要 kernel 永久直接映射（在 32-bit 有 highmem 的機器上可以放 highmem），kernel 要碰內容時才臨時映射（`kmap_local_page` 一類的 API；在 x86_64 上沒有 highmem，這個區分沒有實際作用）。它和 `GFP_USER` 一樣帶 `__GFP_RECLAIM | __GFP_IO | __GFP_FS`，所以這裡也是可睡 context。對應的釋放在 `free_pipe_info` 裡的 `__free_page(pipe->tmp_page[i])`（`fs/pipe.c:863`）。上面那個 `tmp_page` 迴圈是一個小型 page 快取，省掉反覆向 buddy 要 page 的成本。

## devm_*：把生命週期綁在 device 上

driver 的 probe 函式常常配一堆東西，錯誤路徑的 goto 階梯會很長。`devm_*`（device-managed）把每個資源登記在 `struct device` 上，driver 解除綁定或 probe 失敗時，driver core 自動照相反順序釋放。

```c
/* include/linux/device/devres.h:51-54（v6.18） */
static inline void *devm_kzalloc(struct device *dev, size_t size, gfp_t gfp)
{
	return devm_kmalloc(dev, size, gfp | __GFP_ZERO);
}
```

```c
/* drivers/misc/lan966x_pci.c:166-171（v6.18） */
	data = devm_kzalloc(dev, sizeof(*data), GFP_KERNEL);
	if (!data)
		return -ENOMEM;

	pci_set_drvdata(pdev, data);
	data->dev = dev;
```

讀 `devm_` 程式碼時的關鍵轉換：**你找不到 `kfree` 是正常的**。這段 probe 後面的錯誤路徑直接 `return ret`，`data` 由 devres 收；`lan966x_pci_remove` 裡也沒有 `kfree(data)`。反過來，如果你在 `devm_kzalloc` 配的東西上看到手動 `kfree`，那是 double free 的徵兆。

`devm_` 的生命週期是「**到 driver 和 device 解除綁定為止**」，不是「到最後一個使用者放手為止」。如果某個物件會被 user-space 透過開著的 fd 拿住，生命週期可能長過 driver 綁定——這時用 `devm_` 配它就是 UAF。這是 driver 審查裡常見的一類 bug，[Ch 43](./43-driver-model.md) 會在 driver model 的脈絡下再看一次。

## `__free(kfree)`：cleanup.h 版的所有權

[Ch 17](./17-kernel-error-conventions.md) 介紹過 `cleanup.h`。和本章直接相關的是 `slab.h` 裡的定義：

```c
/* include/linux/slab.h:512（v6.18） */
DEFINE_FREE(kfree, void *, if (!IS_ERR_OR_NULL(_T)) kfree(_T))
```

這讓你寫 `struct foo *p __free(kfree) = NULL;`，變數離開 scope 時自動 `kfree`。讀這類程式碼的要點：**所有權轉移會寫成 `no_free_ptr(p)` 或 `return_ptr(p)`**，那是告訴編譯器「這個指標我交出去了，別在 scope 結束時 free」。看一個真實例子：

```c
/* fs/pidfs.c:900-930（v6.18，節錄） */
int pidfs_register_pid(struct pid *pid)
{
	struct pidfs_attr *new_attr __free(kfree) = NULL;
	struct pidfs_attr *attr;

	might_sleep();
	...
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

本章的所有技巧在這幾行裡全部出現：`might_sleep()` 明說可睡、`GFP_KERNEL` 在 `guard(spinlock_irq)` **之前**、兩個提早 `return` 都會自動 free `new_attr`（別人搶先設好了，我配的這份丟掉）、成功路徑用 `no_free_ptr` 轉移所有權。還有一個細節：`new_attr` 來自 `kmem_cache_zalloc`，卻用 `__free(kfree)` 釋放——v6.4 起這是合法的（`Documentation/core-api/memory-allocation.rst` 在 commit `ae65a5211d90` 明文寫入「Objects allocated by `kmem_cache_alloc` can be freed with `kmem_cache_free`, `kfree` or `kvfree`」）。讀 v6.4 以前的程式碼時，這樣寫會是 bug。

## 對比與取捨

| 方案 | 優點 | 缺點 | 讀碼時代表什麼 |
|---|---|---|---|
| `kmalloc` | 快、實體連續、可在 atomic context（配 `GFP_ATOMIC`） | 大塊容易因碎片失敗；bucket 取整浪費 | 一般小物件 |
| `kmem_cache_*` | 無 bucket 浪費、可設對齊與 `TYPESAFE_BY_RCU`、`/proc/slabinfo` 可觀察 | 要先建 cache | 高頻、固定大小、重要的核心物件 |
| `vmalloc` | 大塊不怕碎片 | 會睡、TLB 成本、不能拿去 DMA | 大表格、module 程式碼、一次性大 buffer |
| `kvmalloc` | 小時快、大時不失敗 | 不能 atomic；釋放必須 `kvfree` | 大小由 user 決定、可能很大的表 |
| `alloc_pages` | 拿到 `struct page`，可映射給 user、可 DMA | 以 page 為單位 | page cache、pipe buffer、DMA |
| `devm_*` | 免寫錯誤路徑 | 生命週期綁 driver，不綁使用者 | driver probe |

## 踩雷集錦

1. **「GFP_KERNEL 只是預設值，隨便用」**：錯。`GFP_KERNEL` 是一個關於 context 的宣告，在 spinlock 內、IRQ handler 內、`rcu_read_lock()` 內使用都是 bug。麻煩的是它通常**不會立刻出事**——slab 有現成格子時根本不睡。這類 bug 要開 `CONFIG_DEBUG_ATOMIC_SLEEP` 才會在每次呼叫時印 `BUG: sleeping function called from invalid context`。本課的 build 沒開這個選項，所以**本段的警告訊息未實測，為理論預期行為**；要驗證，在 `.config` 打開 `CONFIG_DEBUG_ATOMIC_SLEEP=y` 重建，寫一個在 `spin_lock` 內 `kmalloc(GFP_KERNEL)` 的 module 在 QEMU 載入。
2. **「GFP_NOWAIT／GFP_ATOMIC 就代表 atomic context」**：不一定。`anon_vma_clone` 用 `GFP_NOWAIT` 是因為拿著 reclaim 需要的鎖。正確讀法是「呼叫者有某個理由不讓 reclaim 發生」，理由要去上下文或 git log 找。
3. **「kfree 什麼都能放」**：`kfree` 不能放 vmalloc 的記憶體，不能放 `alloc_pages` 的 page，不能放 `devm_` 配的東西。配對錯誤是審查時的高價值目標。反過來，v6.4 以後 `kfree` 可以放 `kmem_cache_alloc` 的物件，別誤報。
4. **「kmalloc(n * size) 沒問題，n 不會那麼大」**：只要 `n` 的源頭是 user，就要假設它會那麼大。看到乘法沒用 `kcalloc` / `kmalloc_array` / `struct_size`，往上追上界檢查。
5. **「GFP_ATOMIC 比較安全，乾脆都用它」**：`GFP_ATOMIC` 會吃緊急預備金，濫用會讓真正需要它的 interrupt 路徑失敗；而且它更容易失敗（不能回收），呼叫者必須處理失敗。在可睡 context 用 `GFP_ATOMIC`，review 時會被要求改掉。
6. **「vmalloc 的記憶體和 kmalloc 一樣用」**：vmalloc 的記憶體實體不連續，不能直接交給要求實體連續的 DMA 硬體，也不能用 `virt_to_page` 這類假設線性映射的函式。

## 進階：再往深一層

- **scope API 取代 GFP_NOFS/NOIO**：`gfp_types.h` 的註解建議「use memalloc_nofs_{save,restore} to mark the whole scope」。新程式碼傾向在一段範圍開頭呼叫 `memalloc_nofs_save()`，範圍內所有配置自動繼承 `GFP_NOFS`。讀 filesystem 程式碼時，若看到 `GFP_KERNEL` 卻在 fs 的 lock 內，先往上找有沒有 `memalloc_nofs_save`——這個 context 資訊不在那一行裡。
- **`kmalloc_nolock`**：v6.18 的 `slab.h` 出現了 `kmalloc_nolock_noprof`（`include/linux/slab.h` 在 `kmalloc` 定義下方），給連 spinlock 都不能拿的 context（例如 BPF 程式在任意位置執行）。它的存在本身說明「GFP_ATOMIC 並非萬能」那句註解不是空話。
- **slab 隔離與安全**：`kmalloc_type(flags, _RET_IP_)` 的第二個參數是呼叫點位址，配合 `CONFIG_RANDOM_KMALLOC_CACHES` 可以讓不同呼叫點落到不同的 cache 副本，增加 heap 利用的難度。`SLAB_TYPESAFE_BY_RCU` 和 cross-cache 攻擊的關係則是 [kernel_pwn](../../security/kernel_pwn/README.md) 課的主題。
- **觀察工具**：`/proc/slabinfo`（需要 root）、`slabtop`、tracepoint `kmem:*`（本章用過）。開 `CONFIG_MEM_ALLOC_PROFILING` 後 `/proc/allocinfo` 會列出每一個呼叫點配置了多少記憶體——那就是 `alloc_hooks` 的用途。

## 動手練習

1. 在 v6.18 tree 裡跑 `rg -n 'GFP_ATOMIC' fs/*.c | head -20`，挑三個，各自往上找出「為什麼這裡不能睡」的證據（spin_lock？RCU？在 IRQ handler 裡？）。找不到證據的那個，可能是一個可以改成 `GFP_KERNEL` 的地方，也可能是你漏看了呼叫者——用 `rg` 找它的呼叫點驗證。
2. 用本章的 QEMU 腳本，把 `echo hello | cat` 換成 `ls /`，看 `kmem_cache_alloc` 裡出現哪些 cache 名稱（`dentry`？`inode`？），對照 `kmem_cache_create` 的呼叫點。
3. 在 `fs/` 底下找一個 `kvmalloc`，確認它的每一條釋放路徑都用 `kvfree`。

## 本章重點整理

- GFP flag 是呼叫者對自己 context 的宣告；能不能睡看 `__GFP_DIRECT_RECLAIM`。
- `GFP_NOWAIT`/`GFP_ATOMIC` 的理由可能是 atomic context，也可能是「拿著 reclaim 需要的鎖」；後者要靠上下文或考古確認。
- kmalloc 走 slab bucket，`kmem_cache_*` 是專用 cache，`vmalloc` 虛擬連續且會睡，`kvmalloc` 兩者折衷；每一種都有固定的釋放函式。
- 長度算式裡有 user 控制的乘法，就該看到 `struct_size`/`array_size`/`kcalloc`；它們靠飽和到 `SIZE_MAX` 讓配置失敗。
- `devm_` 的生命週期綁 driver 綁定；`__free(kfree)` 的所有權轉移寫成 `no_free_ptr`。

## 自我檢核

- [ ] 不看書，寫出 `GFP_KERNEL`、`GFP_NOFS`、`GFP_ATOMIC` 各由哪些 `__GFP_` bit 組成，並說出差別的那幾個 bit 各代表什麼承諾。
- [ ] 有人說「這個函式用了 GFP_KERNEL，所以它所有的呼叫者都在 process context」。這個推論什麼時候成立？什麼時候它其實是一個待驗證的假說？
- [ ] 解釋 `anon_vma_clone` 為什麼先試 `GFP_NOWAIT` 再退到 `GFP_KERNEL`，並說出你會用什麼指令找到這個答案。
- [ ] `struct_size` 溢位時回傳什麼？為什麼這個設計讓呼叫端不用多寫檢查？
- [ ] 一個 driver 用 `devm_kzalloc` 配了一個會被開著的 fd 引用的物件。描述會出什麼事、在什麼時間點。

## 延伸閱讀

### 官方文件

- **[Memory Allocation Guide](https://docs.kernel.org/core-api/memory-allocation.html)**（tree 內：`Documentation/core-api/memory-allocation.rst`）
  - **讀哪裡**：「Get Free Page flags」與「Selecting memory allocator」兩節。第 94–125 行附近那張「`GFP_KERNEL & ~__GFP_RECLAIM` … `GFP_KERNEL | __GFP_NORETRY`」的清單，把每種 reclaim 行為講得比本章更細。
  - **和本章的關聯**：本章 GFP 表格與 kmalloc/kvmalloc/kmem_cache 釋放配對規則的官方出處。
  - **前提**：知道 page、zone 是什麼即可。
- **`include/linux/gfp_types.h` 的 kernel-doc**（v6.18 tree 內）
  - **讀哪裡**：`DOC: Page mobility and placement hints`、`DOC: Reclaim modifiers`、`DOC: Useful GFP flag combinations` 三段，全部不到 200 行。
  - **為什麼值得讀**：這是 GFP 語意的第一手來源，比任何二手文章都新；讀 kernel 時遇到沒看過的 `__GFP_` bit，先查這裡。
- **[Devres - Managed Device Resource](https://docs.kernel.org/driver-api/driver-model/devres.html)**（tree 內：`Documentation/driver-api/driver-model/devres.rst`）
  - **讀哪裡**：開頭的動機說明與「Devres Groups」之前的部分；最後的 API 清單當字典查。
  - **和本章的關聯**：`devm_*` 的生命週期模型與它為什麼能取代 goto 階梯。

### 書籍

- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley, 2010）
  - **讀哪幾章**：第 12 章「Memory Management」。slab 那節描述的是當時的 SLAB 實作（v6.18 只剩 SLUB），但 slab 的設計動機、GFP 的分類邏輯、「atomic context 不能睡」的推理仍然適用。
  - **注意**：書中的 GFP 定義（例如 `__GFP_WAIT`）已經不存在，對照本章引用的 v6.18 定義讀。
- **《Understanding the Linux Virtual Memory Manager》** — Mel Gorman（Prentice Hall, 2004）
  - **讀哪幾章**：buddy allocator 與 slab allocator 的章節。基於 2.4/2.6，細節已過時，但它是理解「為什麼要分 slab、buddy、vmalloc 三層」最完整的一本；作者後來是 mm 子系統的長期維護者。

### 論文

- **[The Slab Allocator: An Object-Caching Kernel Memory Allocator](https://www.usenix.org/legacy/publications/library/proceedings/bos94/bonwick.html)** — Jeff Bonwick，USENIX Summer 1994
  - **核心貢獻**：提出 slab：為固定大小物件建快取，保留物件的已初始化狀態，降低配置成本與碎片。Linux 的 slab 概念直接來自這篇。
  - **讀哪裡**：Section 3（設計）與 Section 4（slab 的資料結構）；SunOS 特有的實作細節可以跳過。
  - **和本章的關聯**：讀完你會理解 `kmem_cache_create` 的參數（物件大小、對齊、constructor）為什麼長那樣。

下一章處理記憶體配置的另一半：配置出來的物件**被誰拿著、什麼時候才能放**。

→ [Ch 20 refcount 與物件生命週期：kref、get/put 配對](./20-refcount-and-lifetime.md)
