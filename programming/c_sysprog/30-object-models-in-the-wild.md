# Ch 30 — Linux 核心與開源專案的物件模型

> **目標**：讀得懂 Linux 核心的 `file_operations`、`container_of`、`kref`/`kobject` 與驅動程式模型，並能把 CPython、SQLite、FFmpeg、QEMU、GObject、nginx、libuv 的物件模型放在同一張表上比較：它們用了哪些相同的建構件、在哪裡做了不同的取捨、為什麼。

> **對應原文**：[你所不知道的 C 語言：物件導向程式設計篇](https://hackmd.io/@sysprog/c-oop) 的「Linux 核心的物件導向設計」「開放原始碼專案中的物件導向設計」兩大節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4。核心與各專案的程式碼為原始碼摘錄（原文摘錄自各專案目前的 master/main 分支，日後細節可能變動）；每個機制都附一個在使用者空間可執行的縮小模型。

## 為什麼需要這個？

Ch 29 用 Shape、Counter 這類玩具學會了三根柱子：不完整型別、vtable、結構嵌入。但玩具不會告訴你真實系統的難題：

- 方法表裡有 30 個函式指標，大部分驅動程式只實作 5 個，其餘怎麼辦？
- 基底結構不在第一個成員時怎麼向下轉型？
- 一個物件被 5 個子系統同時引用，誰負責釋放？
- 類別要在執行時期註冊（外掛、可載入模組），型別檢查從哪裡來？

這些問題在 Linux 核心、CPython、SQLite 等專案裡都已經被回答過，而且答案驚人地相似。Neil Brown 在 LWN 的兩篇〈Object-oriented design patterns in the kernel〉把核心的手法歸納成兩個主題：**方法分派**（method dispatch）與**資料繼承**（data inheritance）。這章先用這個架構讀 Linux 核心，再橫向比較六個其他專案。讀完你在任何大型 C 專案裡看到「一個結構的第一個成員是另一個結構」或「一張全是函式指標的 `const` 表」，就知道自己在看什麼。

## 先建立直覺

所有專案都用同一組積木，差別只在組合方式：

```
                     ┌─────────────── 方法分派 ───────────────┐
  物件               │ vptr → const 函式指標表（每類別一份）   │  Linux f_op、SQLite pMethods、
  ┌──────────┐       │ NULL 槽位 = 預設行為或「不支援」        │  CPython ob_type、QOM class
  │ vptr ────┼──────►└────────────────────────────────────────┘
  │ refcount │◄────── 生命週期：歸零時呼叫「虛擬解構子」         kref、ob_refcnt、Object.ref
  │ 基底欄位 │◄────── 資料繼承：基底嵌在子類別裡                 第一個成員（強制轉型）
  ├──────────┤                                                  或任意位置（container_of）
  │ 子類別   │
  │ 私有欄位 │       型別資訊（RTTI）：執行時期知道「我是誰」     AVClass、TypeInfo、GType
  └──────────┘       註冊：編譯時期清單 / 建置時期產生 / 執行時期登記
```

> 本章建立在 [Ch 29 — 物件導向 C](./29-oop-in-c.md) 之上；`container_of` 的推導與 `offsetof` 見 [Ch 19 — Linux list_head、container_of](./19-linux-list-head.md)。

## Linux 核心一：方法分派，`file_operations` 作為 vtable

`include/linux/fs.h` 的 `struct file_operations` 是核心最典型的 vtable，方法的第一個參數就是被操作的物件：

```c
struct file_operations {
	struct module *owner;
	loff_t (*llseek) (struct file *, loff_t, int);
	ssize_t (*read) (struct file *, char __user *, size_t, loff_t *);
	ssize_t (*write) (struct file *, const char __user *, size_t, loff_t *);
	ssize_t (*read_iter) (struct kiocb *, struct iov_iter *);
	...
```

驅動程式用 designated initializer 只填實作了的方法，其餘依 N1570 6.7.9 §19 是 null pointer。`drivers/char/mem.c` 的 `/dev/full` 只有 `read_iter`，沒有 `read`：

```c
static const struct file_operations full_fops = {
	.llseek		= full_lseek,
	.read_iter	= read_iter_zero,
	.write		= write_full,
	.splice_read	= copy_splice_read,
};
```

VFS 在 `fs/read_write.c` 中經由 `file->f_op` 分派，呼叫前檢查 `NULL`：

```c
	if (file->f_op->read)
		ret = file->f_op->read(file, buf, count, pos);
	else if (file->f_op->read_iter)
		ret = new_sync_read(file, buf, count, pos);
	else
		ret = -EINVAL;
```

同在 `mem.c` 的 `memory_open` 示範**執行時期更換 vtable**：`/dev/null`、`/dev/zero`、`/dev/full` 共用主裝置號碼 1，開啟時依次要裝置號碼從 `devlist[]` 取出對應的表，`filp->f_op = dev->fops;`，這個 `struct file` 從此成為另一個類別的物件。

我們在使用者空間重建這套結構（完整程式 `minivfs.c` 約 130 行，以下節錄核心部分）：

```c
struct file_operations {
    ssize_t (*read)(struct file *, char *, size_t);
    ssize_t (*read_iter)(struct file *, struct iov *);
    ssize_t (*write)(struct file *, const char *, size_t);
};

static ssize_t vfs_read(struct file *f, char *buf, size_t n)
{
    if (f->f_op->read)
        return f->f_op->read(f, buf, n);
    else if (f->f_op->read_iter)
        return new_sync_read(f, buf, n);      /* default: adapt read() to read_iter() */
    return -EINVAL;
}

static const struct file_operations null_fops = { .read = read_null, .write = write_null };
static const struct file_operations zero_fops = { .read_iter = read_iter_zero, .write = write_null };
static const struct file_operations full_fops = { .read_iter = read_iter_zero, .write = write_full };

static const struct { const char *name; const struct file_operations *fops; } devlist[] = {
    [3] = { "null", &null_fops }, [5] = { "zero", &zero_fops }, [7] = { "full", &full_fops },
};
static int memory_open(struct inode *inode, struct file *filp)
{
    filp->f_op = devlist[inode->minor].fops;   /* this file now belongs to another "class" */
    return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra -fsanitize=address,undefined minivfs.c -o minivfs && ./minivfs
/dev/null read=0 buf[0]=97 write=1
/dev/zero read=3 buf[0]=0 write=1
/dev/full read=3 buf[0]=0 write=-28 (ENOSPC)
```

對照真實系統的行為完全一致（次要裝置號碼也對得上）：

```
$ ls -l /dev/null /dev/zero /dev/full
crw-rw-rw- 1 root root 1, 7 ... /dev/full
crw-rw-rw- 1 root root 1, 3 ... /dev/null
crw-rw-rw- 1 root root 1, 5 ... /dev/zero
$ echo x > /dev/full
bash: echo: write error: No space left on device
$ head -c3 /dev/zero | od -An -tx1
 00 00 00
```

### `NULL` 方法的兩種意義

LWN 指出，`NULL` 方法幾乎都代表某種預設行為，分兩類：

1. **有預設實作**：`read` 為 `NULL` 時，VFS 用 `new_sync_read` 把它轉接到 `read_iter`。`fs/inode.c` 的 `alloc_inode` 在 `ops->alloc_inode` 為 `NULL` 時配置一般的 `struct inode`，也是同類。
2. **對這類物件沒有意義**：直接回傳錯誤。例如 `fs/namei.c` 的 `vfs_create` 在父目錄 inode 沒有 `create` 方法時 `return -EACCES;`，旁邊至今留著一行註解 "shouldn't it be ENOSYS?"，顯示錯誤碼選擇也曾有爭論。

LWN 對第 1 類有保留意見：呼叫端的 `if (f_op->read)` 等於中間層（mid-layer）對下層行為的假設，散落各處。替代方案是利用 C99 允許同一成員初始化多次、以最後一次為準（N1570 6.7.9 §19）的特性，定義 `FOO_DEFAULTS` 巨集明確填入預設方法，驅動程式再覆寫：

```c
#define FOPS_DEFAULTS .llseek = default_llseek, .read = default_read
static const struct file_operations my_fops = { FOPS_DEFAULTS, .read = my_read };
```

gcc 的 `-Woverride-init`（含於 `-Wextra`）會對這種覆寫發出警告，採用時要局部關閉。Ch 31 會看到 eBPF 直譯器的跳躍表用同一招。

## Linux 核心二：資料繼承，結構嵌入與 `container_of`

早期 `struct inode` 用一個 union 收納各檔案系統的私有欄位：每新增一個檔案系統就得改共用結構，而且每個 inode 都要大到容納最大的那個成員。Linux 2.5 開發期間改為**反向嵌入**：通用的 `struct inode` 嵌入檔案系統私有結構中，VFS 只傳遞內層指標，檔案系統用 `container_of` 取回外層物件。ext4 的版本（`fs/ext4/ext4.h`）：

```c
struct ext4_inode_info {
	...
	struct inode vfs_inode;
	...
};

static inline struct ext4_inode_info *EXT4_I(struct inode *inode)
{
	return container_of(inode, struct ext4_inode_info, vfs_inode);
}
```

`vfs_inode` **不是**第一個成員，Ch 29 的強制轉型行不通。`container_of` 把成員位址減去它在外層結構中的偏移量。現行定義（`include/linux/container_of.h`）多了一道編譯期型別檢查：

```c
#define container_of(ptr, type, member) ({				\
	static_assert(__same_type(*(ptr), typeof_member(type, member)) || \
		      __same_type(*(ptr), void),			\
		      "pointer type mismatch in container_of()");	\
	(type *)((void *)(ptr) - offsetof(type, member)); })
```

這個巨集原本在 `include/linux/kernel.h`，Linux v5.16 拆成獨立標頭檔；它會丟掉 `const`（傳入 `const struct inode *` 得到非 `const` 的外層指標），所以 v6.2 新增 `container_of_const()`，原文指出 v6.17 起標頭註解寫明 "Do not use container_of() in new code"。

### 誰負責配置？`super_operations` 作為建構子

`container_of` 的代價：VFS 不知道外層結構有多大，所以**配置責任必須交給子類別**。`struct super_operations` 提供 `alloc_inode` 與 `free_inode`，扮演建構與解構函式。我們的模型：

```c
struct myfs_inode_info {
    unsigned long i_blocks[4];   /* fs-private data first: layout chosen for the fs */
    int i_flags;
    struct inode vfs_inode;      /* embedded base */
};
static inline struct myfs_inode_info *MYFS_I(struct inode *inode)
{
    return container_of(inode, struct myfs_inode_info, vfs_inode);
}
static struct inode *myfs_alloc_inode(void)
{
    struct myfs_inode_info *ei = calloc(1, sizeof *ei);
    if (!ei) return NULL;
    ei->i_flags = 0x42;
    return &ei->vfs_inode;               /* VFS sees only the embedded base */
}
static void myfs_free_inode(struct inode *inode) { free(MYFS_I(inode)); }
static const struct super_operations myfs_sops = { myfs_alloc_inode, myfs_free_inode };

static struct inode *alloc_inode(const struct super_operations *ops)
{
    struct inode *inode = ops->alloc_inode ? ops->alloc_inode() : calloc(1, sizeof *inode);
    if (inode) inode->sb_ops = ops;
    return inode;
}
```

```
offsetof(vfs_inode)=40 inode=0x506000000048 ei=0x506000000020 flags=0x42
```

（位址每次執行不同。）`inode` 與 `ei` 相差 0x28 = 40，正是 `offsetof`。`free_inode` 必須釋放**外層**指標 `MYFS_I(inode)`；若誤寫成 `free(inode)`，ASan 會回報 `attempting free on address which was not malloc()-ed`。型別檢查也有效：對一個 `int *` 使用 `container_of(&x, struct myfs_inode_info, vfs_inode)`：

```
minivfs.c:12:5: error: static assertion failed: "pointer type mismatch in container_of()"
```

為什麼不乾脆把 `vfs_inode` 放第一個？`container_of` 讓檔案系統可以依 cache 行為自由安排欄位順序，也讓一個物件可以**同時嵌入多個**基底（例如好幾個 `list_head`）。LWN 把 `list_head` 這類歸為 **mixin** 而非子型別：一個物件可以嵌入多個 `list_head`，`list_head` 本身也沒有生命週期。

## Linux 核心三：多張 vtable 與設計取捨

一個子系統常同時使用多張表。ext4 的超級區塊方法 `ext4_sops` 前三項就是 inode 的建構與解構；inode 又依檔案種類分屬不同類別：`fs/ext4/inode.c` 依 `S_ISREG`、`S_ISDIR`、`S_ISLNK` 分別指定 `ext4_file_inode_operations`、`ext4_dir_inode_operations` 或符號連結的版本，只有目錄版本有 `.create`、`.lookup`、`.mkdir`，這正是前面 `vfs_create` 檢查父目錄 inode 那張表的原因。

網路子系統的 `struct net_device` 以 `const struct net_device_ops *netdev_ops` 指向驅動程式方法。最精簡的是 `drivers/net/loopback.c`：

```c
static const struct net_device_ops loopback_ops = {
	.ndo_init        = loopback_dev_init,
	.ndo_start_xmit  = loopback_xmit,
	.ndo_get_stats64 = loopback_get_stats64,
	.ndo_set_mac_address = eth_mac_addr,
};
```

`ndo_set_mac_address` 直接用乙太網路共用的 `eth_mac_addr`，相當於繼承基礎類別的實作；沒填的方法為 `NULL`，例如 `netif_set_mac_address()` 遇到沒有 `ndo_set_mac_address` 的裝置回傳 `-EOPNOTSUPP`。

LWN 整理出幾種偏離單純 vtable 的情形，正好是一張設計取捨清單：

| 偏離 | 理由 | 核心實例 |
|---|---|---|
| 方法指標直接放在物件內 | 物件少、方法少，或每個物件要個別調整；省一次間接 | `struct sock`、`struct request_queue`（LWN 撰文當時） |
| mixin：另一組獨立的 ops | 可選功能不污染主 vtable | `export_operations`（NFS 匯出）、`quota_format_ops` |
| 方法委派給上層物件 | 物件數量龐大，連一個 vptr 都嫌貴 | `struct page` 沒有 vptr，方法在 `address_space_operations` |
| 過度細分 | 除 ceph 與 procfs 外，每個檔案系統實際只用一組 `dentry_operations` | LWN 認為可併入超級區塊 |

最後一條值得記住：vtable 也有成本（每個物件一個指標欄位、一次間接存取），不是切越細越好。

### `const` 與核心自我保護

`Documentation/security/self-protection.rst` 指出：檔案、網路等 operations 結構中的函式指標會被核心查詢並據以執行，數量要降到最低；宣告為 `const` 讓它們進入受記憶體權限保護的唯讀區域，只在 `__init` 階段設定一次的則以 `__ro_after_init` 標記。Ch 29 已經實測寫入 `const` vtable 會觸發 segmentation fault；在核心裡，這道保護讓「改寫一個函式指標」這類攻擊原語失效。

## Linux 核心四：參照計數與虛擬解構子，`kref` 與 `kobject`

LWN 給了一個辨識資料繼承式嵌入的特徵：**最內層結構帶有參照計數**。`include/linux/kref.h` 的 `struct kref` 只包一個 `refcount_t`：

```c
static inline int kref_put(struct kref *kref, void (*release)(struct kref *kref))
{
	if (refcount_dec_and_test(&kref->refcount)) {
		release(kref);
		return 1;
	}
	return 0;
}
```

`struct kobject` 嵌入 `kref`，並以 `ktype` 指向型別描述，其中的 `release` 就是解構函式。`kobject_put` → `kref_put(&kobj->kref, kobject_release)` → `container_of` 由 `kref` 取回 `kobject` → `t->release(kobj)`。解構因此經由型別分派，就像 C++ 的 virtual destructor。`struct device` 的第一個成員是 `struct kobject kobj`，它的 `release` 是 `drivers/base/core.c` 的 `device_release`，依序嘗試 `dev->release`、`dev->type->release`、`dev->class->dev_release`，全都缺席時以 `WARN` 印出 "does not have a release() function"。這等於由最具體的類別往上找覆寫的方法。

整條鏈加上下一節的驅動程式模型，在使用者空間模型 `drvmodel.c`（約 150 行）中重建（節錄）：

```c
struct kref { atomic_int refcount; };
static int kref_put(struct kref *k, void (*release)(struct kref *))
{
    if (atomic_fetch_sub(&k->refcount, 1) == 1) {   /* we dropped the last reference */
        release(k);
        return 1;
    }
    return 0;
}

struct kobj_type { void (*release)(struct kobject *); };
struct kobject { const char *name; const struct kobj_type *ktype; struct kref kref; };
static void kobject_release(struct kref *kref)
{
    struct kobject *kobj = container_of(kref, struct kobject, kref);
    printf("  kobject_release(%s) -> ktype->release\n", kobj->name);
    kobj->ktype->release(kobj);
}

struct device {
    struct kobject kobj;
    const struct bus_type *bus;
    struct device_driver *driver;
    void (*release)(struct device *);          /* most specific override */
};
static void device_release(struct kobject *kobj)
{
    struct device *dev = container_of(kobj, struct device, kobj);
    if (dev->release)
        dev->release(dev);
    else
        printf("  WARNING: device '%s' does not have a release() function\n", kobj->name);
}
```

```
refcount:
  (first put: still referenced)
  kobject_release(uart) -> ktype->release
  platform_device_release(uart.0): free
  kobject_release(gpio) -> ktype->release
  WARNING: device 'gpio' does not have a release() function
```

（`-fsanitize=address,undefined` 下無錯誤。）`kref_put` 的關鍵是「**遞減並測試**」必須是一個原子動作：如果寫成 `refcount--; if (refcount == 0)`，兩個執行緒可能都看到 0 而重複釋放，或都看不到 0 而洩漏。核心的 `refcount_t` 還會在溢位與從 0 遞增時飽和並警告，防止 use-after-free 類攻擊；C11 atomics 見 [Ch 20](./20-lock-free-linked-list.md)。

LWN 以 `struct usb_device` 為例：它嵌入 `struct device`，後者嵌入 `struct kobject`，再嵌入 `struct kref`。**一個物件只能有一個生命週期計數**，所以資料繼承只能單一繼承，多重「繼承」只能用 `list_head` 這類沒有生命週期的 mixin。

## Linux 核心五：驅動程式模型，bus、device、driver

Greg Kroah-Hartman 在《Beautiful Code》的〈The Linux Kernel Driver Model: The Benefits of Working Together〉一章介紹了這套設計的由來。它把前面的手法組成一個類別階層：`struct bus_type` 與 `struct device_driver` 都有 `probe`、`remove` 等方法；`drivers/base/dd.c` 的 `call_driver_probe` 先呼叫 `dev->bus->probe`，匯流排沒提供才呼叫 `drv->probe`。platform 匯流排以嵌入擴充基礎類別（`include/linux/platform_device.h`）：

```c
struct platform_driver {
	int (*probe)(struct platform_device *);
	void (*remove)(struct platform_device *);
	...
	struct device_driver driver;
	...
};

#define to_platform_driver(drv)	(container_of((drv), struct platform_driver, \
				 driver))
```

`platform_bus_type.probe` 是 `platform_probe`，它用 `to_platform_driver(_dev->driver)` 與 `to_platform_device(_dev)` **向下轉型**，再以 `drv->probe(dev)` 呼叫驅動程式的方法，此時參數已是具體的 `struct platform_device *`。模型中的對應部分：

```c
static int platform_probe(struct device *_dev)          /* downcast, then call the typed method */
{
    struct platform_driver *drv = to_platform_driver(_dev->driver);
    struct platform_device *dev = to_platform_device(_dev);
    return drv->probe(dev);
}
static const struct bus_type platform_bus_type = { "platform", platform_match, platform_probe };

static int uart_probe(struct platform_device *pdev)
{
    printf("  uart_probe: got a struct platform_device * for %s.%d\n", pdev->name, pdev->id);
    return 1;
}
static struct platform_driver uart_driver = {
    .probe = uart_probe,
    .driver = { .name = "uart", .bus = &platform_bus_type },
};
```

```
probe:
  uart_probe: got a struct platform_device * for uart.0
  uart matched=1
  gpio matched=0
```

整體結構：

```
driver core 只認識的抽象層          轉接層（platform / PCI / USB ...）        驅動程式
───────────────────────            ─────────────────────────────            ─────────────
struct device_driver   ◄─嵌入─  struct platform_driver { probe(pdev); driver; }  uart_driver
struct device          ◄─嵌入─  struct platform_device { name; id; dev; }
bus_type.probe(dev) ──────────► platform_probe:
                                   to_platform_driver / to_platform_device（container_of）
                                   drv->probe(pdev) ───────────────────────────► uart_probe
```

driver core 只處理 `struct device` 與 `struct device_driver`；platform、PCI、USB 各自扮演轉接層，把通用的 `probe`/`remove` 轉成帶具體型別的虛擬方法。新增一種匯流排不需要改 driver core，新增一個驅動程式也不需要改匯流排。

> **本節的核心原始碼為摘錄，未在真實核心上實測**；`minivfs.c` 與 `drvmodel.c` 是保留結構、去掉並行與錯誤處理的使用者空間模型，不是核心程式碼的等價物（例如真實的 `kref` 使用 `refcount_t` 的飽和語意，我們用一般的 `atomic_int`）。

## 開源專案一：CPython 的 `PyObject` 與 `PyTypeObject`

`Include/object.h` 的註解直說 "This is inheritance built by hand"：

```c
#define PyObject_HEAD                   PyObject ob_base;
struct _object {
    ...                     // reference count, ob_refcnt
    PyTypeObject *ob_type;  // part of stable ABI; do not change
};
struct _longobject {
    PyObject_HEAD
    _PyLongValue long_value;
};
PyTypeObject PyLong_Type = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "int",                                      /* tp_name */
    ...
    long_dealloc,                               /* tp_dealloc */
    ...
    long_to_decimal_string,                     /* tp_repr */
```

每個 Python 物件以 `PyObject` 開頭，所以任何物件指標都能轉成 `PyObject *`，直譯器只需一種指標型別就能操作所有物件。`ob_type` 指向的 `PyTypeObject` 同時是類別與 vtable：`tp_repr` 對應 `repr()`、`tp_dealloc` 是解構子、`tp_base` 記錄繼承。`PyTypeObject` 自己也以 `PyObject_VAR_HEAD` 開頭，**型別也是物件**，`PyLong_Type` 的型別是 `PyType_Type`，`PyType_Type` 的型別是它自己，這就是 Python 的 metaclass。

縮小模型 `pyobj.c`（約 100 行）保留了這些關係，並加上 `bool` 作為只覆寫 `repr` 的 `int` 子類別：

```c
typedef struct _typeobject PyTypeObject;
typedef struct _object { long ob_refcnt; PyTypeObject *ob_type; } PyObject;
#define PyObject_HEAD PyObject ob_base;

struct _typeobject {
    PyObject_HEAD                                  /* a type is an object too */
    const char *tp_name;
    size_t tp_basicsize;
    void (*tp_dealloc)(PyObject *);
    void (*tp_repr)(PyObject *, char *, size_t);
    PyTypeObject *tp_base;
};

static void Py_DECREF(PyObject *o)
{
    if (--o->ob_refcnt == 0)
        Py_TYPE(o)->tp_dealloc(o);                 /* destruction is a virtual call */
}

PyTypeObject PyType_Type = { { 1, &PyType_Type }, "type", sizeof(PyTypeObject), NULL, NULL, NULL };
...
PyTypeObject PyBool_Type = { { 1, &PyType_Type }, "bool", sizeof(PyLongObject),
                             long_dealloc, bool_repr, &PyLong_Type };
```

```
$ gcc -std=c11 -Wall -Wextra -fsanitize=address,undefined pyobj.c -o pyobj && ./pyobj
42         refcnt=1 type=int
True       refcnt=1 type=bool
'hello'    refcnt=1 type=str
isinstance(True, int)=1 isinstance('hello', int)=0
type(int) is type, type(type) is type
drop references:
  long_dealloc(42)
  long_dealloc(1)
  ('hello' still alive)
  str_dealloc("hello")
```

幾個真實 CPython 的細節：

- **PEP 3123 與 strict aliasing**：Python 3.0 之前，`PyObject_HEAD` 是逐一展開的欄位（`Py_ssize_t ob_refcnt; PyTypeObject *ob_type;`），每個物件結構開頭並沒有一個 `PyObject` 型別的成員，不受 Ch 29 的 6.7.2.1 §15 保障，以 `PyObject *` 存取其實違反有效型別規則。PEP 3123 改成內嵌 `PyObject ob_base`，讓轉型有標準依據。
- **參照循環**：`ob_refcnt` 是主要的生命週期依據，參照循環則交給 `gc` 模組的循環垃圾回收器。
- **free-threaded 組建**（`Py_GIL_DISABLED`）：同一個 `struct _object` 換成 `ob_ref_local` 與 `ob_ref_shared` 兩個計數欄位，透過 `Py_INCREF` 等介面存取的程式碼不受影響，這是封裝的效果。
- Python 3.12 起 `PyLongObject` 改用 `PyObject_HEAD` 加 `_PyLongValue`（以 `lv_tag` 編碼位數與正負號），不再用 3.11 以前的 `PyObject_VAR_HEAD`。

## 開源專案二：SQLite 的 VFS

SQLite 用兩個介面隔離作業系統：`sqlite3_vfs` 負責開檔、刪檔、亂數、時間等全域操作；`sqlite3_io_methods` 負責已開啟檔案的讀寫與 lock。

```c
struct sqlite3_file {
  const struct sqlite3_io_methods *pMethods;  /* Methods for an open file */
};
struct unixFile {
  sqlite3_io_methods const *pMethod;  /* Always the first entry */
  sqlite3_vfs *pVfs;                  /* The VFS that created this unixFile */
  unixInodeInfo *pInode;              /* Info about locks on this inode */
  int h;                              /* The file descriptor */
  ...
};
int sqlite3OsRead(sqlite3_file *id, void *pBuf, int amt, i64 offset){
  ...
  return id->pMethods->xRead(id, pBuf, amt, offset);
}
```

`sqlite3_file` 是**只有一個 vptr 的抽象基底類別**。最有意思的是配置方式：`sqlite3_vfs` 有個 `szOsFile` 欄位告訴引擎「我的子類別有多大」，**引擎配置記憶體**，再交給 `xOpen` 就地建構，等同 C++ 的 placement new。這解決了 Linux `container_of` 那節「誰負責配置」的問題的另一種答案：不是子類別自己配置，而是子類別告訴你大小。

應用程式可以用 `sqlite3_vfs_register()` 加入自訂 VFS，或包裝既有 VFS 實作加密、壓縮與記錄（shim），這是 **Decorator** 模式。縮小模型 `minivfs_sqlite.c` 同時示範兩者（節錄）：

```c
typedef struct vfs {
    const char *zName;
    int szOsFile;                                      /* how big is my subclass? */
    int (*xOpen)(struct vfs *, const char *, vfs_file *);
    struct vfs *pNext;
    void *pAppData;
} vfs;

/* shim VFS: wraps another VFS and logs every call */
typedef struct { const io_methods *pMethod; vfs_file *pReal; } logFile;  /* real file follows */
static int log_open(vfs *v, const char *name, vfs_file *f)
{
    vfs *real = v->pAppData;
    logFile *l = (logFile *) f;
    l->pReal = (vfs_file *) (l + 1);                  /* the real object lives right after us */
    int rc = real->xOpen(real, name, l->pReal);
    l->pMethod = &log_io;
    return rc;
}

static void engine(const char *vfs_name)
{
    vfs *v = vfs_find(vfs_name);
    vfs_file *f = malloc((size_t) v->szOsFile);      /* engine allocates, VFS constructs */
    v->xOpen(v, "test.db", f);
    ...
}
int main(void)
{
    log_vfs.szOsFile = (int) sizeof(logFile) + mem_vfs.szOsFile;
    ...
}
```

```
$ ./sqlvfs
memdb  szOsFile=80 read back "SQLite"
  [log] xWrite 6@0
  [log] xRead  6@0
log    szOsFile=96 read back "SQLite"
  [log] xClose
```

shim 的 `szOsFile` = 自己的 16 位元組 + 被包裝者的 80 位元組，兩個物件放在同一塊配置裡，shim 的方法轉呼叫內層物件的方法並加上記錄。引擎端從頭到尾只透過 `pMethods` 分派，不知道底下是 POSIX、Windows、記憶體還是一層包一層的 shim。真實 SQLite 中，同一個 `unixFile` 還能依 lock 策略掛上 `posixIoMethods`、`nolockIoMethods`、`dotlockIoMethods`，對應 `"unix"`、`"unix-none"`、`"unix-dotfile"` 這些具名 VFS。

## 開源專案三：FFmpeg 的 `AVClass` 與 `FFCodec`

`AVClass` 是 FFmpeg 的輕量 RTTI。`av_log()` 的第一個參數是 `void *`，文件要求它指向 "an arbitrary struct of which the first field is a pointer to an AVClass struct"。`libavutil/log.c` 用 `*(AVClass **) avcl` 取出類別描述，把 `item_name()` 的結果與物件位址當成每行日誌的前綴。模型：

```c
typedef struct AVClass {
    const char *class_name;
    const char *(*item_name)(void *ctx);
} AVClass;

static void av_log(void *avcl, const char *fmt, ...)
{
    if (avcl) {
        const AVClass *cls = *(const AVClass **) avcl;          /* read the first field */
        const char *name = cls->item_name ? cls->item_name(avcl) : cls->class_name;
        printf("[%s @ %p] ", name, avcl);
    }
    ...
}

typedef struct { const AVClass *av_class; int width, height; const char *codec; } CodecContext;
typedef struct { const AVClass *av_class; const char *url; } FormatContext;
```

```
[h264 @ 0x7ffec890f850] frame size 1920x1080
[AVFormatContext @ 0x7ffec890f840] opening input.mp4
no context
```

（位址每次執行不同。）用過 `ffmpeg` 命令列的人一定看過 `[h264 @ 0x...]` 這種前綴，來源就是這裡。`AVClass` 的 `option` 欄位還讓 `AVOption` 系統能以名稱查詢、設定任意物件的參數，形同反射（reflection）。注意這裡**沒有共同基底結構**，只約定「第一個欄位是 `const AVClass *`」，`CodecContext` 和 `FormatContext` 除此之外毫無關係，這是比繼承更鬆的「介面」。

`FFCodec` 則示範**公開與私有介面分離**：

```c
typedef struct FFCodec {
    AVCodec p;                 /* public part, first member */
    ...
    int (*init)(struct AVCodecContext *);
    ...
} FFCodec;
const FFCodec ff_h264_decoder = {
    .p.name                = "h264",
    ...
    .init                  = h264_decode_init,
```

公開的 `AVCodec` 嵌在第一個成員 `p`，函式指標只存在於內部結構，外部程式看不到也無法依賴。`av_codec_iterate()` 回傳 `&c->p`，內部再以 `ffcodec()` 轉回 `const FFCodec *`（downcast）。註冊在**建置時期**完成：`configure` 依啟用的元件產生 `codec_list.c`。

## 開源專案四：QEMU 的 QOM

QOM（QEMU Object Model）把每個型別拆成兩個結構：**實體**（instance）以父類別實體為第一個成員，最底層的 `Object` 持有指向類別的指標與參照計數；**類別**同樣以 `parent_class` 開頭，存放所有實體共用的虛擬函式。

```c
struct Object
{
    /* private: */
    ObjectClass *class;
    ...
    uint32_t ref;
    ...
};
struct PCIDeviceClass {
    DeviceClass parent_class;
    void (*realize)(PCIDevice *dev, Error **errp);
    ...
};
static const TypeInfo edu_types[] = {
    {
        .name          = TYPE_PCI_EDU_DEVICE,
        .parent        = TYPE_PCI_DEVICE,
        .instance_size = sizeof(EduState),
        .class_init    = edu_class_init,
        ...
DEFINE_TYPES(edu_types)
```

教學用的 `edu` 裝置用 `TypeInfo` 宣告型別名稱、父類別**名稱**、實體大小與 `class_init`，`DEFINE_TYPES()` 在程式啟動時完成註冊；`edu_class_init()` 把 `k->realize` 設成 `pci_edu_realize`，這就是 override。`OBJECT_DECLARE_TYPE()` 產生 `DEVICE()` 這類帶執行時期檢查的轉型函式（預設建置選項 `qom_cast_debug` 開啟時，型別不符就 `abort()`），需要失敗回傳 `NULL` 的語意則呼叫 `object_dynamic_cast()`。

縮小模型 `miniqom.c`（約 120 行）重現了「以名稱註冊、類別結構延遲初始化、先複製父類別再覆寫、檢查過的轉型」（節錄）：

```c
static ObjectClass *type_class(TypeImpl *t)        /* lazy: built on first use */
{
    if (t->klass) return t->klass;
    t->klass = calloc(1, t->info.class_size);
    if (t->info.parent) {
        t->parent = type_lookup(t->info.parent);
        ObjectClass *pk = type_class(t->parent);
        memcpy(t->klass, pk, t->parent->info.class_size);   /* inherit every virtual method */
    }
    t->klass->type = t;
    if (t->info.class_init) t->info.class_init(t->klass);   /* then override */
    printf("  (class_init %s)\n", t->info.name);
    return t->klass;
}
...
static const TypeInfo infos[] = {
    { "object", NULL,     sizeof(Object),      sizeof(ObjectClass), object_class_init, NULL },
    { "device", "object", sizeof(DeviceState), sizeof(DeviceClass), device_class_init, NULL },
    { "edu",    "device", sizeof(EduState),    sizeof(DeviceClass), edu_class_init, edu_instance_init },
};
```

```
object_new("edu"):
  (class_init object)
  (class_init device)
  (class_init edu)
an object of type edu
  edu: realize, fact=120
dynamic_cast to device: ok, to edu: ok, to pci: NULL
now DEVICE(plain object):
miniqom.c:114: Object 0x60bc92ced340 is not an instance of type device
Aborted (core dumped)
```

（最後一行來自 stderr 與 shell；位址每次執行不同。）幾個觀察：

- **類別繼承是「複製再覆寫」**：子類別的類別結構以父類別開頭，`memcpy` 把父類別的所有函式指標帶進來，`class_init` 只改自己要覆寫的槽位。`edu` 沒有提供 `describe`，所以繼承到 `object` 的版本；它覆寫了 `realize`。
- **延遲初始化**：第一次 `object_new("edu")` 才沿著父鏈建出三個類別結構；之後建立 `object` 實體時不再印 `class_init`。GObject 也是首次需要類別時才配置並執行 `class_init`。
- **檢查過的 downcast**：這正是 Ch 29「C 不檢查 downcast」的補救。代價是每次轉型走一次父鏈；QEMU 允許以建置選項關閉檢查換取效能。

QEMU 另有 `QObject`、`QDict`、`QList` 等型別（位於 `include/qobject/`），它們**不屬於 QOM**，而是 QMP 與 JSON 用的值模型：以 `struct QObjectBase_ { QType type; size_t refcnt; }` 開頭，解構時依 `type` 查一張固定的 `qdestroy[]` 表，沒有類別結構、無法從外部註冊新型別。兩者都用首成員嵌入與參照計數，但 QOM 是可擴充的類別階層，QObject 是封閉的 tagged union。

## 開源專案五：GLib 的 GObject

GObject 的結構與 QOM 相似（`gio/gcancellable.h`）：

```c
struct _GCancellable
{
  GObject parent_instance;
  /*< private >*/
  GCancellablePrivate *priv;
};
struct _GCancellableClass
{
  GObjectClass parent_class;
  void (* cancelled) (GCancellable *cancellable);
  ...
};
G_DEFINE_TYPE_WITH_PRIVATE (GCancellable, g_cancellable, G_TYPE_OBJECT)
```

實體以 `GObject` 開頭（其下是只含 `g_class` 指標的 `GTypeInstance`），類別以 `GObjectClass` 開頭（其下是只含 `GType` 的 `GTypeClass`），`GObject` 另帶 `ref_count` 供 `g_object_ref()`/`g_object_unref()` 管理生命週期。`G_DEFINE_TYPE` 系列巨集（Ch 28 拆解過）產生 `g_cancellable_get_type()`，首次呼叫時向型別系統註冊；`_WITH_PRIVATE` 變體註冊一塊外部看不到結構的私有資料，並產生依偏移量取得它的 `get_instance_private()`。

GObject 最獨特的是 **signal**，它是 Observer 模式的工業實作。`g_signal_new()` 的 `G_STRUCT_OFFSET (GCancellableClass, cancelled)` 把類別結構中的虛擬函式**槽位**登記成該 signal 的 class closure：子類別可以覆寫 `cancelled` 改變預設行為，任何觀察者用 `g_signal_connect()` 掛上回呼；`g_signal_emit()` 依 `G_SIGNAL_RUN_LAST` 規則，先呼叫一般回呼，再呼叫 class closure，最後是 `g_signal_connect_after()` 掛上的回呼。虛擬函式與觀察者在這裡統一成同一套機制。

> **本段未實測**：本機未安裝 `libglib2.0-dev`（`pkg-config` 找不到 `gobject-2.0`）。`miniqom.c` 示範了與 GObject 相同的「實體/類別雙結構、延遲 class_init、父類別先初始化」骨架。

## 開源專案六：nginx 與 libuv

**nginx** 的每個模組是一個 `ngx_module_t` 實體，以函式指標對應行程生命週期（`init_module`、`init_process`、`exit_process` 等），`type` 欄位是型別標記，`ctx` 指向該類模組專屬的第二層介面；HTTP 模組的 `ctx` 是 `ngx_http_module_t`，提供 `postconfiguration` 等掛鉤：

```c
    for (m = 0; cf->cycle->modules[m]; m++) {
        if (cf->cycle->modules[m]->type != NGX_HTTP_MODULE) {
            continue;
        }
        module = cf->cycle->modules[m]->ctx;
        if (module->postconfiguration) {
            if (module->postconfiguration(cf) != NGX_OK) {
```

先比對 `type` 再把 `ctx` 轉型使用，是**以型別標記實作的 downcast**；`NULL` 掛鉤表示不覆寫，等同繼承一個什麼都不做的預設實作。靜態模組由 `configure` 寫入 `ngx_modules[]`（建置時期註冊），動態模組則由設定檔的 `load_module` 在執行時期載入。

**libuv**（Node.js 底層的非同步 I/O 函式庫）走另一條路：不嵌入基底結構，而用巨集把基底欄位逐一展開到每個子類別：

```c
#define UV_HANDLE_FIELDS                                                      \
  /* public */                                                                \
  void* data;                                                                 \
  /* read-only */                                                             \
  uv_loop_t* loop;                                                            \
  uv_handle_type type;                                                        \
  ...
struct uv_handle_s { UV_HANDLE_FIELDS };
struct uv_stream_s { UV_HANDLE_FIELDS UV_STREAM_FIELDS };
struct uv_tcp_s    { UV_HANDLE_FIELDS UV_STREAM_FIELDS UV_TCP_PRIVATE_FIELDS };
```

標頭註解直接稱 `uv_handle_t` 為 "abstract base class"。使用者可以把 `uv_tcp_t *` 轉成 `uv_handle_t *` 交給 `uv_close()`。但這和「基底結構作為第一個成員」**在標準上不同**：C 對開頭相同的兩個結構只有一項保證，即 common initial sequence（N1570 6.5.2.3 §6），而且限於它們同為某個 union 的成員、且該 union 的完整宣告可見時。兩個各自獨立的物件只因開頭欄位相同，並不能透過彼此的型別互相存取，這正是 CPython 在 PEP 3123 之前的處境。libuv 的做法是在自己的建置設定中加上 `-fno-strict-aliasing`，讓編譯器放棄依 strict aliasing 規則的最佳化。這是 libuv API 明文支援的用法，**不代表同樣的寫法在一般標準 C 程式中合法**。strict aliasing 的完整討論見 [Ch 34](./34-undefined-behavior.md)。

## 對比與取捨

| 專案 | 繼承方式 | 方法分派 | 生命週期 | RTTI / 型別檢查 | 類別註冊時機 |
|---|---|---|---|---|---|
| Linux VFS | 嵌入（任意位置）+ `container_of` | 每類別一張 `const` ops 表；`NULL` = 預設 | `kref`（`refcount_t`） | 無；`container_of` 有編譯期型別檢查 | 編譯時期（驅動程式內的 `static const`） |
| Linux driver model | 嵌入 + `to_xxx()` | bus → driver 兩層 probe | `kobject` → `ktype->release` | bus `match()` | 執行時期 `driver_register` |
| CPython | `PyObject` 為首成員 | `ob_type->tp_*` | `ob_refcnt` + 循環 GC | `ob_type`、`tp_base`、`tp_mro` | 靜態型別物件 + 執行時期 `class` |
| SQLite VFS | vptr 為首成員 | `pMethods->x*` | 引擎配置、`xClose` 解構 | 無 | 執行時期 `sqlite3_vfs_register` |
| FFmpeg | 公開結構嵌在首成員 | 內部 `FFCodec` 函式指標 | 各 API 的 `*_free` | `AVClass`（首欄位約定）+ `AVOption` 反射 | 建置時期 `codec_list.c` |
| QEMU QOM | 實體/類別雙結構，父在首位 | 類別結構函式指標，複製再覆寫 | `Object.ref` | `TypeInfo` 名稱鏈；檢查過的轉型 | 程式啟動時 `type_register` |
| GObject | 同 QOM | 同 QOM + signal | `ref_count` + toggle/weak ref | `GType`；`G_TYPE_CHECK_INSTANCE_CAST` | 首次呼叫 `get_type()` |
| nginx | `type` 標記 + `ctx` 第二層介面 | 生命週期掛鉤 | 行程與記憶體池 | `type` 欄位 | 建置時期 / `load_module` |
| libuv | 巨集展開共同欄位 | `type` 欄位 + switch | `uv_close` 回呼 | `type` 欄位 | 無（封閉集合） |

一條可以帶走的規律：**型別集合越封閉、越在意效能，越傾向編譯時期決定**（Linux ops 表、libuv 的 `type` 欄位）；**越需要外掛與擴充，越傾向執行時期的型別系統**（QOM、GObject、CPython）。QEMU 同時有 QOM 與 QObject，正好示範了同一個專案依需求選擇兩種極端。

## 踩雷集錦

1. **以為 `container_of` 很安全**：傳錯 member 名稱或指向錯誤型別的指標，舊版巨集照樣算出一個位址，讀到的是別的記憶體。現代核心的 `static_assert` 能擋住型別不符，但擋不住「型別對、物件不對」（例如這個 `inode` 其實屬於另一個檔案系統）。
2. **`container_of` 丟掉 `const`**：從 `const struct inode *` 得到可寫的 `struct ext4_inode_info *`，等於繞過了 `const`。新程式碼用 `container_of_const()`。
3. **用錯指標釋放**：嵌入式繼承中，`free()` 必須拿到**最外層**的位址。`free(inode)` 而非 `free(EXT4_I(inode))` 是典型錯誤；ASan 會回報 `attempting free on address which was not malloc()-ed`。
4. **把 `NULL` 方法當成必定是「不支援」**：`read` 為 `NULL` 可能只是代表「請用 `read_iter`」。呼叫端要依介面文件決定預設行為，不能一律回傳錯誤。
5. **參照計數的「遞減後檢查」拆成兩步**：`count--; if (count == 0) release();` 在多執行緒下會重複釋放或洩漏。必須是原子的 dec-and-test。
6. **以為共同開頭欄位就等於繼承**：libuv 的寫法依賴 `-fno-strict-aliasing`；在自己的專案裡照抄而不加這個旗標，高最佳化等級下可能出現難以解釋的錯誤。標準的做法是內嵌一個基底結構（PEP 3123 的教訓）。

## 進階：再往深一層

- **讀核心原始碼的順序建議**：`include/linux/kref.h`（極短）→ `include/linux/container_of.h` → `lib/kobject.c` 的 `kobject_put`/`kobject_cleanup` → `drivers/base/platform.c` 的 `platform_probe` 與 `__platform_driver_register` → `drivers/char/mem.c` 的 `memory_open`。每一個都能對應到本章模型的某個函式。
- **生命週期的進階問題**：參照計數處理不了循環（CPython 用循環 GC；GObject 提供 weak reference 與 toggle reference）。核心的 RCU 則讓讀者不取參照也能安全存取正在被刪除的物件，延遲到所有讀者離開後才釋放，見 jserv 的 Linux 核心設計系列。
- **面試題**：「Linux 核心怎麼在 C 裡做繼承？」好答案涵蓋：嵌入基底結構、`container_of` 與 `offsetof`、為什麼不限第一個成員、`super_operations->alloc_inode` 的配置責任、`kref` 作為最內層與單一繼承的限制，最後提 `file_operations` 的 `NULL` 預設語意。

## 動手練習

1. 在 `minivfs.c` 加一個 `/dev/random` 風格的裝置（次要號碼 8），只實作 `read_iter`；再加一個 `llseek` 方法與 `vfs_llseek`，讓 `NULL` 時回傳 `-ESPIPE`（不可 seek），對照真實核心 `no_llseek` 的語意。
2. 在 `drvmodel.c` 中讓 `uart_probe` 對裝置呼叫一次 `kobject_get` 並在 `remove` 時 `kobject_put`，確認最後一次 put 才觸發 release；故意少寫一次 put，用 ASan 看洩漏報告。
3. 為 `miniqom.c` 加上「介面」：`TypeInfo` 新增 `const char *interfaces[]`，`object_dynamic_cast` 也要能轉到介面名稱。想想介面方法表要放在哪裡（提示：QOM 為每個介面另建一個類別結構）。
4. 在 `minivfs_sqlite.c` 再寫一個 `xor` shim，把寫入內容 XOR 一個金鑰、讀出時還原，疊在 `log` 外面變成 `xor → log → memdb` 三層，確認 `szOsFile` 的計算與指標位置。

## 本章重點整理

- Linux 核心的物件導向可歸納為方法分派（`const` ops 表、`NULL` 表示預設或不支援、執行時期可換表）與資料繼承（嵌入 + `container_of`、子類別負責配置、最內層是 `kref`）。
- `container_of` 解除「基底必須在第一個成員」的限制，代價是配置責任上移與型別安全只靠編譯期檢查。
- 生命週期是物件模型的另一半：參照計數必須原子地遞減並測試，解構經由型別分派（虛擬解構子），一個物件只能有一個計數。
- 七個開源專案用同一組積木：首成員嵌入、函式指標表、參照計數、型別描述；差別在註冊時機（編譯/建置/啟動/首次使用）與型別檢查的強度。
- 共同開頭欄位不等於標準保證的繼承；PEP 3123 與 libuv 的 `-fno-strict-aliasing` 是同一個問題的兩種處理。

## 自我檢核

- [ ] 不看筆記，能說出 VFS 在 `read` 為 `NULL` 時怎麼辦，並舉一個「`NULL` 表示不支援」的反例
- [ ] 能畫出 `ext4_inode_info` 與 `vfs_inode` 的關係，解釋 `EXT4_I()` 的計算，以及為什麼 `alloc_inode` 必須由檔案系統提供
- [ ] 能從 `kobject_put` 一路追到 `platform_device_release`，說出每一步做了什麼
- [ ] 如果面試官問「SQLite 怎麼在不知道子類別大小的情況下配置 VFS 檔案物件」，能回答 `szOsFile` 與 placement 建構
- [ ] 能比較 QOM 與 Linux driver model 在「型別檢查」上的差異，並說出各自的理由
- [ ] 能解釋為什麼 libuv 需要 `-fno-strict-aliasing` 而 CPython 3.0 之後不需要
- [ ] 想一個你用過的外掛系統（編輯器、瀏覽器、建置工具），說明它對應表格中的哪一種註冊時機

## 延伸閱讀

### 部落格 / 技術文章

- **[Object-oriented design patterns in the kernel, part 1](https://lwn.net/Articles/444910/) 與 [part 2](https://lwn.net/Articles/446317/)** — Neil Brown（LWN，2011）
  - **這篇說什麼**：part 1 談方法分派（vtable、`NULL` 方法、方法放物件內 vs 共用表、mixin），part 2 談資料繼承（union → 嵌入、`container_of`、參照計數作為辨識特徵）
  - **讀哪裡**：兩篇都讀完；part 1 結尾的設計取捨清單就是本章表格的來源
  - **為什麼值得讀**：作者是長期的核心開發者（md/RAID、NFS 維護者），這是描述核心物件導向手法最被廣泛引用的文章
  - **前提知識**：Ch 29

- **[Linux 核心原始程式碼巨集：container_of](https://hackmd.io/@sysprog/linux-macro-containerof)** — jserv
  - **讀哪裡**：從 `offsetof` 的推導到 `typeof` 型別檢查的演進
  - **和本章的關聯**：本章只用了 `container_of`，這篇完整推導它為什麼長這樣

- **[PEP 3123 – Making PyObject_HEAD conform to standard C](https://peps.python.org/pep-3123/)** — Martin von Löwis（2007）
  - **這篇說什麼**：為什麼舊的 `PyObject_HEAD` 違反 strict aliasing、改成內嵌 `PyObject ob_base` 怎麼解決
  - **為什麼值得讀**：短，而且是「共同開頭欄位 vs 內嵌基底」這個標準議題最清楚的一手說明

### 官方文件

- **[The QEMU Object Model (QOM)](https://www.qemu.org/docs/master/devel/qom.html)** — QEMU 文件
  - **讀哪裡**：「Class Initialization」與「Interfaces」兩節，對照本章 `miniqom.c` 與練習 3
  - **延伸**：[qemu-object-model](https://github.com/Gyumeijie/qemu-object-model) 把 QOM 抽成可單獨編譯的精簡版

- **[SQLite: The OS Interface Object (VFS)](https://sqlite.org/vfs.html)** — SQLite 文件
  - **讀哪裡**：「VFS Shims」一節描述 Decorator 用法；「szOsFile」的說明在 `sqlite3_vfs` 的 API 文件
  - **前提知識**：本章 SQLite 小節

- **[GObject Tutorial](https://docs.gtk.org/gobject/tutorial.html) 與 [Signals](https://docs.gtk.org/gobject/signals.html)** — GTK 文件
  - **讀哪裡**：Tutorial 走一遍定義衍生型別；Signals 的「Signal emission」說明 RUN_FIRST/RUN_LAST 的呼叫順序

### 書籍

- **《Beautiful Code》** — Andy Oram、Greg Wilson 編（O'Reilly，2007），其中 "The Linux Kernel Driver Model: The Benefits of Working Together"（Greg Kroah-Hartman）
  - **這本書的定位**：driver model 維護者親自講設計由來，包含 `kref` 誕生的理由
  - **注意**：寫於 2.6 時代，細節（如 `struct class_device` 已被移除）過時，但設計思想適用

- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley，2010）
  - **讀哪幾章**：第 13 章（VFS 的四個主要物件與它們的 operations）、第 17 章（devices and modules，含 kobject 與 kref）
  - **注意**：基於 2.6.34，API 名稱有變動，但物件關係不變

物件模型是「資料如何組織」；下一章轉向「控制如何流動」。Linux 核心 20 萬處 `goto`、`switch` 背後的跳躍表、直譯器的 computed goto、Duff's device 與 stackless coroutine，都是同一個問題的不同答案：程式的下一步要去哪裡，以及離開時誰負責收拾。

→ [Ch 31 goto 與流程控制：清理鏈、跳躍表與 computed goto](./31-goto-and-control-flow.md)
