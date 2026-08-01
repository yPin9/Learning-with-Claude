# Ch 40 — Rust driver：字元 / misc device（原理深挖）

> **目標**：把 [Ch 39](./39-first-kernel-module.md) 的 module 骨架加上真功能——寫一個 **misc device**：註冊 `/dev/rust-misc-device`、實作 `MiscDevice` trait 的 `open`/`ioctl`/`read_iter`/`write_iter`（對照 C 的 `struct file_operations` + `misc_register`）。你會看清三件事的 Rust 化：(1) 使用者空間資料傳遞——`UserSlice`/`UserSliceReader`/`UserSliceWriter`（對照 `copy_to_user`/`copy_from_user`，以及為什麼**絕不能**直接解引用 user pointer——連回你 kernel_pwn 的攻擊面）；(2) 每裝置私有狀態——`Pin<KBox<Self>>` + `Mutex` 存進 `file->private_data`；(3) ioctl 命令解碼——`_IO`/`_IOR`/`_IOW` 與錯誤回傳 `Result`→errno。全程對照 C。

> **正確性聲明（先讀）**：本章的 RfL API 依主線 kernel 樹 `samples/rust/rust_misc_device.rs`、`rust/kernel/miscdevice.rs`、`rust/kernel/uaccess.rs`、`rust/kernel/ioctl.rs` **逐字查證（2026-08，主線 `v7.2-rc5`）**。**跟 [Ch 37–39] 的斷言一致：這層 API 未穩定、版本間會變**——而且本章要誠實指出一個重要變動：misc device 的讀寫**現在是 `read_iter`/`write_iter`（走 `iov_iter`），不是舊教材裡的 `read`/`write`**。真 kernel module 的 build/`insmod`/QEMU 一律標「**未實測，理論預期**」（本機 WSL2 無 kernel build tree，[Ch 39](./39-first-kernel-module.md) 已確認）。**能本機真跑**的三段純 Rust 邏輯（ioctl 號解碼、`UserSliceReader` 位置前進、`private_data` 所有權往返）標明是 WSL2 `rustc 1.97.1` 真跑的輸出。

## 為什麼需要這個？

你在 C 寫過字元裝置：一個 `struct file_operations`（填 `.open`/`.read`/`.write`/`.unlocked_ioctl`），一次 `misc_register()`（或 `register_chrdev()`），然後 user space 就能 `open("/dev/xxx")` / `read` / `write` / `ioctl`。那是「kernel 跟 user space 溝通」最基本的介面，也是**攻擊面最集中的地方**——你做 kernel_pwn 打的洞，一大半就從某個 driver 的 `ioctl` handler 或 `copy_from_user` 進去。

這一章寫同一個東西的 Rust 版，目的不是換語法，而是看清楚：C 那個 `file_operations` 裡每一個「你必須自己做對」的危險點——`copy_from_user` 的長度、`file->private_data` 的型別、ioctl 的 arg 是個 `__user` 指標不能直接碰——RfL 用型別怎麼收掉。你 kernel_pwn 的經驗在這裡是資產：你知道這些點**為什麼危險**，所以你能真正看懂 RfL 的封裝**擋掉了什麼**，而不是把它當黑箱。

具體要回答：(1) C 的 `file_operations` 在 Rust 裡對應什麼（`MiscDevice` trait）；(2) `copy_to_user`/`copy_from_user` 那條 kernel↔user 邊界，Rust 怎麼包成「呼叫端拿不到 raw user pointer、只能透過安全 API 讀寫」；(3) 每個開啟的 fd 的私有狀態（C 的 `file->private_data`）在 Rust 裡怎麼變成一個有型別、有鎖保護的物件。

## 先建立直覺：driver 是「被 kernel 回呼的一組 handler」

C 的字元裝置心智模型是**一張函式指標表**：

```
  C：一個 struct file_operations，kernel 在對應 syscall 時回呼你的函式
  user: open("/dev/xxx") ─────▶ fops.open(inode, file)
  user: ioctl(fd, cmd, arg) ──▶ fops.unlocked_ioctl(file, cmd, arg)   arg 是 __user 指標
  user: read(fd, buf, n) ─────▶ fops.read(file, buf, n, pos)          buf 是 __user 指標
  user: close(fd) ────────────▶ fops.release(inode, file)
        │
        └── 每個 fd 的狀態存在 file->private_data（void*，型別你自己記）
```

Rust 的心智模型是**一個 trait，你填它的方法，kernel crate 幫你接到那張表上**：

```
  RfL：impl MiscDevice for YourDev { fn open / ioctl / read_iter / write_iter }
       │
       │  kernel crate 生一張 file_operations，每個 slot 指向一個 unsafe extern "C" shim，
       │  shim 把 raw C 參數轉成安全型別，再呼你的 trait 方法
       ▼
  open  回 Pin<KBox<YourDev>> ── kernel crate 把它 into_foreign() 存進 file->private_data
  ioctl 的 arg ── 包成 UserSlice（不是裸 __user 指標，你碰不到裸的）
  read/write ── 走 UserSliceWriter/Reader 或 IovIter，copy_*_user 藏在裡面
  release ── kernel crate from_foreign() 取回，物件 Drop
```

關鍵差異：**C 給你一張填函式指標的表 + 一個 `void*` private_data，正確性全靠你（長度對不對、型別轉對不對、user 指標碰不碰得、鎖拿了沒）；Rust 給你一個 trait，每個危險轉換（raw C 指標→安全型別、user 指標→`UserSlice`、`void*`→你的型別）都由 kernel crate 的 shim 用審過的 `// SAFETY:` 做掉，你只寫安全那一半**。這是 [Ch 38](./38-kernel-abstractions.md) 「型別接管契約」在 driver 場景的直接落地。

> 這章大量用 [Ch 38](./38-kernel-abstractions.md) 的東西：`Mutex<T>` 的 RAII guard、`Pin<KBox<T>>`、`pin_init!`/`new_mutex!`、`Result`/`?`、`ForeignOwnable`（Rust 物件交給 C 持有）。不熟先回看 Ch 38。

## misc device 為什麼是入門首選

先講清楚**為什麼是 misc device**，不是完整的 `register_chrdev` + `cdev`。

C 裡註冊一個字元裝置有兩條路：(1) 完整的 `alloc_chrdev_region` + `cdev_init` + `cdev_add`——你要自己管 major/minor number、`struct cdev`、`/dev` 節點；(2) **misc device**（`miscdevice` 子系統）——你只給一個名字和一組 `file_operations`，kernel 自動配一個共享 major（10）下的 minor，`misc_register()` 一呼，`/dev/<name>` 就出現了。misc device 是「我只要一個 `/dev` 節點跑我的 handler」的最省事路徑，`rust_misc_device.rs` 選它正是這個原因。

對照 C 的 misc device：

```c
/* C misc device：填 fops + 一個 struct miscdevice，misc_register 一次搞定 */
static const struct file_operations rust_misc_fops = {
    .owner          = THIS_MODULE,
    .open           = my_open,
    .release        = my_release,
    .unlocked_ioctl = my_ioctl,
    .read_iter      = my_read_iter,     /* 現代慣例走 iov_iter */
    .write_iter     = my_write_iter,
};
static struct miscdevice my_misc = {
    .minor = MISC_DYNAMIC_MINOR,        /* 自動配 minor */
    .name  = "rust-misc-device",        /* -> /dev/rust-misc-device */
    .fops  = &rust_misc_fops,
};
/* init 裡： */  misc_register(&my_misc);
/* exit 裡： */  misc_deregister(&my_misc);
```

RfL 把這整組（`file_operations` + `struct miscdevice` + `misc_register`/`misc_deregister`）包成 `MiscDeviceRegistration<T>` 這一個型別 + `MiscDevice` 這一個 trait。**你不手填 `file_operations`，也不手呼 `misc_register`/`misc_deregister`**——註冊在建構時做、反註冊在 `Drop` 時做（[Ch 38](./38-kernel-abstractions.md) 的 RAII，套到裝置註冊上）。

## 完整的 Rust misc device：逐塊拆

以下是主線 `samples/rust/rust_misc_device.rs` 的**真實原始碼**（**2026-08 逐字查證，主線 `v7.2-rc5`**），拆塊講。先看 use 和 ioctl 號、module 註冊：

```rust
use kernel::{
    device::Device,
    fs::{File, Kiocb},
    ioctl::{_IO, _IOC_SIZE, _IOR, _IOW},
    iov::{IovIterDest, IovIterSource},
    miscdevice::{MiscDevice, MiscDeviceOptions, MiscDeviceRegistration},
    new_mutex,
    prelude::*,
    sync::{aref::ARef, Mutex},
    uaccess::{UserSlice, UserSliceReader, UserSliceWriter},
};

const RUST_MISC_DEV_HELLO: u32 = _IO('|' as u32, 0x80);
const RUST_MISC_DEV_GET_VALUE: u32 = _IOR::<i32>('|' as u32, 0x81);
const RUST_MISC_DEV_SET_VALUE: u32 = _IOW::<i32>('|' as u32, 0x82);

module! {
    type: RustMiscDeviceModule,
    name: "rust_misc_device",
    authors: ["Lee Jones"],
    description: "Rust misc device sample",
    license: "GPL",
}
```

`_IO`/`_IOR::<i32>`/`_IOW::<i32>` 就是 C 的 `_IO`/`_IOR`/`_IOW` 巨集（後面「ioctl 命令解碼」節細講）。注意 `_IOR::<i32>` 帶型別參數——它用 `size_of::<i32>()` 算 size 欄位，這是 Rust 比 C 好的地方：C 的 `_IOR('|', 0x81, int)` 要你手寫型別名，Rust 用泛型參數，型別和 size 不會對不上。

### module 是「持有一個 registration」的物件

```rust
#[pin_data]
struct RustMiscDeviceModule {
    #[pin]
    _miscdev: MiscDeviceRegistration<RustMiscDevice>,
}

impl kernel::InPlaceModule for RustMiscDeviceModule {
    fn init(_module: &'static ThisModule) -> impl PinInit<Self, Error> {
        pr_info!("Initialising Rust Misc Device Sample\n");

        let options = MiscDeviceOptions {
            name: c"rust-misc-device",
        };

        try_pin_init!(Self {
            _miscdev <- MiscDeviceRegistration::register(options),
        })
    }
}
```

跟 [Ch 39](./39-first-kernel-module.md) 的 `rust_minimal` 對照，兩個變化：

1. **`impl InPlaceModule` 而非 `impl Module`**：[Ch 39](./39-first-kernel-module.md) 的 `Module::init` 回 `Result<Self>`（一個建好、可以 move 的值）。這裡 module 持有 `MiscDeviceRegistration`——那東西**不可 move**（它一註冊，kernel 的 miscdevice 子系統就記了它的位址）。所以要用 `InPlaceModule`，`init` 回 `impl PinInit<Self, Error>`（一個「在最終位址原地建好」的 initializer，[Ch 38](./38-kernel-abstractions.md) 的 pin-init），配上 `#[pin_data]` + `#[pin]` 標記 `_miscdev` 是 pinned 欄位。這是 [Ch 38](./38-kernel-abstractions.md) 「不可 move 物件需要 in-place 初始化」的又一個真實案例——**registration 就是那種物件**。
2. **`MiscDeviceRegistration::register(options)` 是 initializer**：它不是回一個建好的 registration，而是回 `impl PinInit<Self, Error>`——`<-` 語法（原地初始化）把它接進 `_miscdev`。這個 initializer 內部呼 `misc_register()`（見下方底層機制）。`_miscdev` 前綴 `_` 因為你不直接用它，但它必須活著（它一 drop 就 `misc_deregister`，`/dev` 節點消失）。

`MiscDeviceOptions { name: c"rust-misc-device" }` 的 `c"..."` 是 Rust 的 **C 字串字面量**（`&CStr`），因為要傳給 C 的 `misc_register`（它吃 `const char *`）。對照 C 的 `.name = "rust-misc-device"`。

### 每裝置私有狀態：`Mutex<Inner>` + `Pin<KBox<Self>>`

```rust
struct Inner {
    value: i32,
    buffer: KVVec<u8>,
}

#[pin_data(PinnedDrop)]
struct RustMiscDevice {
    #[pin]
    inner: Mutex<Inner>,
    dev: ARef<Device>,
}
```

`RustMiscDevice` 是**每個 open 的 fd 的私有狀態**——對照 C 你 `kmalloc` 一塊、存進 `file->private_data` 的那個 struct。它有：

- `inner: Mutex<Inner>`——受鎖保護的可變狀態（一個 `value` 和一個 `buffer`）。`#[pin]` 因為 `Mutex` 不可 move（[Ch 38](./38-kernel-abstractions.md)）。`KVVec` 是 kernel 的 vmalloc 版 vector（`KVec` 是 kmalloc 版），細節不重要，當它是「fallible 的 `Vec<u8>`」。
- `dev: ARef<Device>`——一個對 `struct device` 的引用計數 handle（[Ch 38](./38-kernel-abstractions.md) 的 `Arc` 家族，`ARef` 是「Abstract Ref」綁 kernel refcount），用來 log（`dev_info!`）。

對照 C：C 你會寫 `struct rust_misc_device { struct mutex lock; int value; struct kvec buffer; struct device *dev; };`，然後 `file->private_data` 是 `void *`——**型別被抹掉了**，你每次從 `private_data` 取出來都要手動 cast 回 `struct rust_misc_device *`，cast 錯了是 UB。Rust 這邊 `private_data` 存的是 `Pin<KBox<RustMiscDevice>>`（型別化），kernel crate 的 shim 幫你 cast，cast 對不對是 kernel crate 保證的，不是你。

### `open`：建構私有狀態，對照 `fops.open` + 存 private_data

```rust
#[vtable]
impl MiscDevice for RustMiscDevice {
    type Ptr = Pin<KBox<Self>>;

    fn open(_file: &File, misc: &MiscDeviceRegistration<Self>) -> Result<Pin<KBox<Self>>> {
        let dev = ARef::from(misc.device());
        dev_info!(dev, "Opening Rust Misc Device Sample\n");

        KBox::try_pin_init(
            try_pin_init! {
                RustMiscDevice {
                    inner <- new_mutex!(Inner {
                        value: 0_i32,
                        buffer: KVVec::new(),
                    }),
                    dev: dev,
                }
            },
            GFP_KERNEL,
        )
    }
```

`#[vtable]` 標記這個 impl 會被展開成一張 vtable（`file_operations`）；`type Ptr = Pin<KBox<Self>>` 宣告「私有狀態用 `Pin<KBox>` 包」。

`open` 對照 C 的 `fops.open`：**每次 user space `open("/dev/rust-misc-device")`，這個方法跑一次**，建一個新的 `RustMiscDevice`（`value=0`、空 buffer），回 `Ok(Pin<KBox<Self>>)`。這裡用 [Ch 38](./38-kernel-abstractions.md) 的 `try_pin_init!` + `new_mutex!`（`inner <-` 原地初始化 `Mutex`）+ `KBox::try_pin_init`（配 kernel 記憶體、原地建好、`GFP_KERNEL` fallible）。

**關鍵**：C 的 `open` 你要手動 `p = kmalloc(...); if (!p) return -ENOMEM; p->value = 0; ...; file->private_data = p;`——配置檢查、初始化、存 private_data 全手做，漏一步（忘配 `private_data`、忘檢查 NULL）就是 bug。Rust 你回 `Ok(物件)`，**kernel crate 的 open shim 幫你把它存進 `file->private_data`**（下方底層機制會看到那行 `into_foreign()`）。你根本碰不到 `private_data` 這個 `void*`。

### `ioctl`：命令分派 + user 資料傳遞

```rust
    fn ioctl(me: Pin<&RustMiscDevice>, _file: &File, cmd: u32, arg: usize) -> Result<isize> {
        dev_info!(me.dev, "IOCTLing Rust Misc Device Sample\n");

        let arg = UserPtr::from_addr(arg);      // arg 是使用者位址，包成 UserPtr（不是裸指標）
        let size = _IOC_SIZE(cmd);              // 從 cmd 解出資料大小（_IOR/_IOW 編進去的）

        match cmd {
            RUST_MISC_DEV_GET_VALUE => me.get_value(UserSlice::new(arg, size).writer())?,
            RUST_MISC_DEV_SET_VALUE => me.set_value(UserSlice::new(arg, size).reader())?,
            RUST_MISC_DEV_HELLO => me.hello()?,
            _ => {
                dev_err!(me.dev, "-> IOCTL not recognised: {}\n", cmd);
                return Err(ENOTTY);             // 未知命令 -> -ENOTTY（對照 C 的 return -ENOTTY）
            }
        };

        Ok(0)
    }
```

`me: Pin<&RustMiscDevice>` 是 kernel crate 從 `file->private_data` 借出來的私有狀態（借用，不奪所有權）。對照 C 你要 `struct rust_misc_device *me = file->private_data;`——Rust 幫你借好、型別對好。

看 `match cmd`——這就是 C 的 `switch (cmd)` ioctl 分派：

- `GET_VALUE`：把 `arg`（使用者位址）包成一個 `UserSlice`，取它的 **writer**（我們要**寫**資料**到**使用者空間），交給 `get_value`。
- `SET_VALUE`：取 **reader**（我們要**從**使用者空間**讀**），交給 `set_value`。
- 未知命令：回 `Err(ENOTTY)`——`?`/`return Err` 會被 kernel crate 轉成 `-ENOTTY` 回給 user space（[Ch 38](./38-kernel-abstractions.md) 的 `Result`→errno）。`ENOTTY` 是「不是這個裝置認得的 ioctl」的標準 errno，C driver 也回這個。

**`arg` 不是裸的 user 指標**——它是 `UserPtr`（`UserSlice::new(arg, size)` 吃它，回一個 `UserSlice`）。你**不能**對它 `unsafe { *arg }`——這是本章下一節的重點。

### `set_value` / `get_value`：`UserSlice` 讀寫

```rust
impl RustMiscDevice {
    fn set_value(&self, mut reader: UserSliceReader) -> Result<isize> {
        let new_value = reader.read::<i32>()?;   // 從 user 讀一個 i32（對照 copy_from_user）
        let mut guard = self.inner.lock();       // 拿鎖（RAII guard）
        guard.value = new_value;
        Ok(0)
        // guard drop -> 自動 unlock
    }

    fn get_value(&self, mut writer: UserSliceWriter) -> Result<isize> {
        let guard = self.inner.lock();
        let value = guard.value;
        drop(guard);                             // 提早解鎖：copy 到 user 時不持鎖（好習慣）
        writer.write::<i32>(&value)?;            // 寫一個 i32 到 user（對照 copy_to_user）
        Ok(0)
    }

    fn hello(&self) -> Result<isize> {
        dev_info!(self.dev, "-> Hello from the Rust Misc Device\n");
        Ok(0)
    }
}
```

`reader.read::<i32>()?` 對照 C 的 `copy_from_user(&new_value, arg, sizeof(int))`；`writer.write::<i32>(&value)?` 對照 `copy_to_user(arg, &value, sizeof(int))`。差別下一節細講。

注意 `get_value` 的 `drop(guard)`——它在 `copy_to_user`（`writer.write`）**之前**就解鎖了。為什麼？因為 `copy_to_user` **可能睡眠**（user 那頁可能被 swap 出去，要 page fault 拉回來），而你不該在持鎖時做可能睡眠的事（會拖長臨界區、增加 contention）。這是 kernel 領域的好習慣，C 也一樣要注意——差別是 Rust 你用 `drop(guard)` 明確控制解鎖點，C 你手寫 `mutex_unlock`。

### `read_iter` / `write_iter`：注意，不是 `read`/`write`

```rust
    fn read_iter(mut kiocb: Kiocb<'_, Self::Ptr>, iov: &mut IovIterDest<'_>) -> Result<usize> {
        let me = kiocb.file();
        let inner = me.inner.lock();
        let read = iov.simple_read_from_buffer(kiocb.ki_pos_mut(), &inner.buffer)?;
        Ok(read)
    }

    fn write_iter(mut kiocb: Kiocb<'_, Self::Ptr>, iov: &mut IovIterSource<'_>) -> Result<usize> {
        let me = kiocb.file();
        let mut inner = me.inner.lock();
        inner.buffer.clear();
        let len = iov.copy_from_iter_vec(&mut inner.buffer, GFP_KERNEL)?;
        *kiocb.ki_pos_mut() = 0;                 // 重設位置，讓下次 read 從頭
        Ok(len)
    }
```

**這是本章最需要「認識論誠實」的一點**：很多 RfL 教材（含早期的）寫 misc device 的讀寫是 `read`/`write`。**主線現在不是**——`MiscDevice` trait 提供的是 `read_iter`/`write_iter`，走 `iov_iter`（分散/聚集 I/O 的迭代器，對照 C 現代 `file_operations` 的 `.read_iter`/`.write_iter` 而非老的 `.read`/`.write`）。`Kiocb` 是「kernel I/O control block」（對照 C 的 `struct kiocb`，帶檔案位置 `ki_pos`）；`IovIterDest`/`IovIterSource` 是 `iov_iter` 的目標/來源端。

`read_iter` 把 `inner.buffer` 的內容依 `ki_pos`（檔案位置）讀給 user；`write_iter` 把 user 寫來的資料**取代** `buffer` 內容並把位置歸零。這對照 C 現代 driver 用 `copy_to_iter`/`copy_from_iter` + `iov_iter` 的寫法。

> 為什麼是 iter 不是舊的 `read`/`write`？kernel 現代 I/O 路徑（readv/writev、非同步 I/O、`io_uring`）統一走 `iov_iter`；`.read`/`.write` 是老介面，新 driver 建議走 `_iter` 版。RfL 的 `MiscDevice` 直接只給 `_iter` 版，順應主線方向。**如果你看到別處寫 `fn read(...)`，那是舊版 API 或簡化，對著你那棵 kernel 的 `rust/kernel/miscdevice.rs` 為準。**

### `PinnedDrop`：對照 `fops.release`

```rust
#[pinned_drop]
impl PinnedDrop for RustMiscDevice {
    fn drop(self: Pin<&mut Self>) {
        dev_info!(self.dev, "Exiting the Rust Misc Device Sample\n");
    }
}
```

`close(fd)` 時，kernel crate 的 release shim 從 `private_data` 取回 `Pin<KBox<RustMiscDevice>>`，它離開作用域 → `PinnedDrop::drop` 跑（`#[pinned_drop]` 是 pinned 物件的 Drop，因為物件是 `Pin` 的）。對照 C 的 `fops.release`——但 C 你要在 release 裡手動 `kfree(file->private_data)`，Rust 這裡 `drop` 只印訊息，`inner`/`dev` 的釋放是自動的（物件 drop 連帶欄位 drop）。**忘記 release 裡 `kfree` 是 C driver 的經典 leak，Rust 結構上不可能漏。**

## 底層機制：user 指標為什麼不能直接碰，`UserSlice` 怎麼包安全

這一節是本章的靈魂，也最貼你 kernel_pwn 的直覺。

### 為什麼不能 `*arg`（直接解引用 user 指標）

`ioctl` 的 `arg` 是一個**使用者空間位址**。你在 kernel context（ring 0）跑，但那個位址指向 user 的位址空間。直接 `unsafe { *(arg as *const i32) }` 在 kernel 裡是**災難**，四個原因（你 kernel_pwn 全知道）：

```
  user 傳來 arg = 0xdeadbeef
     │
  ┌──┴─────────────────────────────────────────────┐
  │ 1. 那頁可能沒 mapped → 直接 deref = kernel oops  │
  │ 2. 那頁可能被 swap 出去 → 要 page fault 拉回，    │
  │    直接 deref 在 atomic context 會炸             │
  │ 3. user 可能傳一個 KERNEL 位址（0xffff...）→      │
  │    直接 deref = 你替 user 讀寫 kernel 記憶體！     │
  │    這正是無數 CVE 的根：缺 access_ok 檢查          │
  │ 4. SMAP/PAN 硬體會 fault：kernel 不准直接碰 user  │
  └────────────────────────────────────────────────┘
```

第 3 點是重點：如果 driver 直接 `*arg` 而不驗證 `arg` 在 user 範圍，user 傳一個 kernel 位址進來，driver 就替他讀/寫任意 kernel 記憶體——**任意讀寫原語**，你 kernel_pwn 夢寐以求的東西。C kernel 正確做法是**永遠**透過 `copy_from_user`/`copy_to_user`（它們內部做 `access_ok` 檢查 + 處理 page fault + 尊重 SMAP）。漏了走這條路直接 deref，就是漏洞。

### `copy_from_user`/`copy_to_user` 在 C 是什麼

```c
/* C：唯一正確的 kernel<->user 資料傳遞。回傳「沒 copy 成功的 byte 數」，0 = 全成功 */
if (copy_from_user(&kbuf, user_ptr, len))   /* 內部：access_ok + 處理 fault */
    return -EFAULT;                          /* 失敗回 -EFAULT */
if (copy_to_user(user_ptr, &kbuf, len))
    return -EFAULT;
```

`copy_from_user` 內部：`access_ok(user_ptr, len)`（確認整段在 user 範圍，擋掉 kernel 位址）→ 逐 byte copy（碰到未 mapped 頁走 exception fixup，回沒 copy 的 byte 數，不 oops）。**它是那條 kernel↔user 邊界唯一該走的門。** 但 C 不強制你走——你可以 `*user_ptr` 直接 deref，編譯器不擋，於是漏洞產生。

### RfL 怎麼把這條邊界包成「碰不到裸指標」

RfL 的手法：**根本不讓 driver 作者拿到裸 user 指標**。看 `ioctl` 的 `arg` 型別——它是 `usize`（一個位址整數），你把它包成 `UserPtr`（`UserPtr::from_addr(arg)`），再包成 `UserSlice::new(arg, size)`。`UserSlice` 是「一段 user 記憶體」，它的文件（`rust/kernel/uaccess.rs`，2026-08 逐字）寫得很清楚：

> All methods on this struct are safe: attempting to read or write on bad addresses (either out of the bound of the slice or unmapped addresses) will return `EFAULT`.

翻譯：**`UserSlice` 上所有方法都是 safe 的**——碰到壞位址（越界或未 mapped）回 `EFAULT`，不 oops、不 UB。你要讀就取 `.reader()`（`UserSliceReader`），要寫就取 `.writer()`（`UserSliceWriter`），透過它們的 `read`/`write`/`read_slice`/`write_slice` 存取。**你手上從頭到尾沒有一個能直接 deref 的裸 user 指標。**

`read`/`read_slice` 內部就是包了 `copy_from_user`。看 `UserSliceReader::read_raw` 的**真實原始碼**（`rust/kernel/uaccess.rs`，2026-08 逐字）：

```rust
pub fn read_raw(&mut self, out: &mut [MaybeUninit<u8>]) -> Result {
    let len = out.len();
    let out_ptr = out.as_mut_ptr().cast::<c_void>();
    if len > self.length {                  // 越界檢查：不能讀超過這個 slice 的長度
        return Err(EFAULT);
    }
    // SAFETY: `out_ptr` points into a mutable slice of length `len`, so we may write
    // that many bytes to it.
    let res = unsafe { bindings::copy_from_user(out_ptr, self.ptr.as_const_ptr(), len) };
    if res != 0 {                           // copy_from_user 回非 0 = 有沒 copy 成功的 byte
        return Err(EFAULT);                 // -> EFAULT
    }
    self.ptr = self.ptr.wrapping_byte_add(len);  // 位置前進 len
    self.length -= len;                          // 剩餘長度減 len
    Ok(())
}
```

三個要點：

1. **裡面確實是 `copy_from_user`**（那行 `unsafe { bindings::copy_from_user(...) }`）——RfL 沒繞過 C 的邊界機制，它就是包了它，連同 `access_ok`/fault 處理全在 `copy_from_user` 裡。
2. **上面有 `// SAFETY:`**——這行 unsafe 的契約（`out_ptr` 指向長度 `len` 的可寫 slice）寫明了。這是 [Ch 38](./38-kernel-abstractions.md) 講的「unsafe 收在幾行、每行有 SAFETY 契約」。driver 作者呼叫的 `read_slice`/`read` 是 **safe** 的，unsafe 被封在這裡。
3. **越界回 `EFAULT` 不 UB**——`len > self.length` 直接回 `Err(EFAULT)`，你不可能讀超過 `UserSlice` 宣告的長度。C 你要自己確保 `len` 不超過（傳錯 len 是經典 bug），Rust 這個 slice 型別記著長度。

`read::<i32>()`（`set_value` 用的）更嚴——它要求 `T: FromBytes`（[Ch 18](./18-unsafe-advanced.md) 的概念：任何 bit pattern 都是該型別的合法值，`i32` 是）。看真實原始碼（節錄）：

```rust
pub fn read<T: FromBytes>(&mut self) -> Result<T> {
    let len = size_of::<T>();
    if len > self.length { return Err(EFAULT); }
    let mut out: MaybeUninit<T> = MaybeUninit::uninit();
    // SAFETY: ... 用 _copy_from_user 變體 ...
    let res = unsafe { bindings::_copy_from_user(out.as_mut_ptr().cast(), self.ptr.as_const_ptr(), len) };
    if res != 0 { return Err(EFAULT); }
    // ... 前進位置 ...
    // SAFETY: read 已初始化所有 byte，且 T: FromBytes 保證任何 bit pattern 合法
    Ok(unsafe { out.assume_init() })
}
```

`T: FromBytes` 約束擋掉一類 C 的 bug：C 你 `copy_from_user(&my_struct, arg, sizeof(my_struct))` 讀進一個帶 enum 或指標的 struct——user 可以塞任意 bytes，那個 enum 變非法值、那個指標變 user 控制的位址（又是攻擊面）。Rust 的 `FromBytes` 只讓你 `read` 成「任何 bit pattern 都合法」的型別（整數、`[u8; N]` 等），帶 enum/指標的型別編不過，逼你手動驗證。

### 防雙重取用（double-fetch / TOCTOU）

`UserSlice` 的文件還講了一個你 kernel_pwn 一定打過的 bug 類：**double-fetch / TOCTOU**（time-of-check-to-time-of-use）。C 裡如果你對同一個 user 位址 `copy_from_user` 兩次（一次檢查、一次使用），user 可以在兩次之間**改掉那塊記憶體**（另一條 user thread 同時寫），讓你檢查的值和使用的值不同——繞過驗證。文件（逐字）：

> These APIs are designed to make it difficult to accidentally write TOCTOU bugs. Every time a memory location is read, the reader's position is advanced by the read length and the next read will start from there.

`UserSliceReader` **每讀一次自動前進位置**，下次讀從下一個位置開始——**你不會不小心讀同一個位置兩次**。要 double-fetch（有時合法需要）得明確 `clone_reader()` 開第二個 reader。這把「意外雙重取用」變成「你得刻意寫」，縮小攻擊面。這個「讀了就前進」的行為，本機能真跑驗證：

```rust
// 本機真跑：模擬 UserSliceReader「每讀一次自動前進、越界回 EFAULT」
struct UserSliceReader<'a> { data: &'a [u8], pos: usize, remaining: usize }
impl<'a> UserSliceReader<'a> {
    fn new(d: &'a [u8]) -> Self { Self { data: d, pos: 0, remaining: d.len() } }
    fn read_u32(&mut self) -> Result<u32, &'static str> {
        if 4 > self.remaining { return Err("EFAULT"); }         // 對照 len > self.length
        let mut b = [0u8; 4];
        b.copy_from_slice(&self.data[self.pos..self.pos + 4]);
        self.pos += 4; self.remaining -= 4;                     // 位置前進，防重讀
        Ok(u32::from_ne_bytes(b))
    }
}
fn main() {
    let user_mem = [1u8,0,0,0, 2,0,0,0, 3,0,0,0];               // 三個 u32
    let mut r = UserSliceReader::new(&user_mem);
    println!("read #1 = {:?}, remaining = {}", r.read_u32(), r.remaining);
    println!("read #2 = {:?}, remaining = {}", r.read_u32(), r.remaining);
    println!("read #3 = {:?}, remaining = {}", r.read_u32(), r.remaining);
    println!("read #4 = {:?}  <- 越界，安全回 EFAULT", r.read_u32());
}
```

本機（WSL2 `rustc 1.97.1`）真跑輸出：

```
read #1 = Ok(1), remaining = 8
read #2 = Ok(2), remaining = 4
read #3 = Ok(3), remaining = 0
read #4 = Err("EFAULT")  <- 越界，安全回 EFAULT
```

每次讀 `remaining` 遞減、位置前進；第四次越界回 `EFAULT` 而非讀到垃圾。真的 `UserSliceReader` 就是這個形狀，只是把「讀本地 slice」換成「`copy_from_user`」。

## 底層機制：`file->private_data` 的所有權往返

C 的 `file->private_data` 是 `void*`——你在 `open` 存進去、在 `ioctl`/`read`/`write` 取出來 cast、在 `release` 取出來 `kfree`。型別被抹掉、所有權靠你腦子記。RfL 用 `ForeignOwnable`（[Ch 38](./38-kernel-abstractions.md) 提過）把這套自動化。看 kernel crate 的 open shim **真實原始碼**（`rust/kernel/miscdevice.rs`，2026-08 節錄）：

```rust
// kernel crate 生成的 open shim（unsafe extern "C"，橋接 C fops.open）
unsafe extern "C" fn open(inode: ..., raw_file: *mut bindings::file) -> c_int {
    // ... generic_file_open、取出 MiscDeviceRegistration ...
    let ptr = match T::open(file, misc) {        // 呼你的 MiscDevice::open
        Ok(ptr) => ptr,                          // ptr: Pin<KBox<YourDev>>
        Err(err) => return err.to_errno(),
    };
    // SAFETY: The open call of a file can access the private data.
    unsafe { (*raw_file).private_data = ptr.into_foreign() };  // 把物件存進 private_data
    0
}
```

`ptr.into_foreign()` 把你 `open` 回的 `Pin<KBox<YourDev>>` **所有權「凍」成一個 C 能持有的裸指標**，存進 `file->private_data`（[Ch 38](./38-kernel-abstractions.md) 的 `ForeignOwnable::into_foreign`）。之後：

- **`ioctl` shim**：`<T::Ptr as ForeignOwnable>::borrow(private)`——**借出**（不奪所有權），你在 `ioctl` 拿到 `Pin<&RustMiscDevice>`。
- **`release` shim**：`<T::Ptr as ForeignOwnable>::from_foreign(private)`——**取回**所有權，物件離開作用域 → `Drop`。

對照 C：`into_foreign`=你手寫 `file->private_data = p`，`borrow`=你手寫 `p = file->private_data`（每次要 cast），`from_foreign`=你手寫 `p = file->private_data; kfree(p)`。RfL 的差別是**型別對得住**（borrow 回來是 `Pin<&RustMiscDevice>` 不是 `void*`）、**所有權清楚**（借用 vs 取回是型別區分的：`borrow` 回引用、`from_foreign` 回擁有的值），且 `release` 忘不了釋放（`from_foreign` 回的值一定會 drop）。這套所有權往返本機能真跑驗證形狀：

```rust
// 本機真跑：模擬 private_data 的所有權往返。std 等價物：
// Box::into_raw = into_foreign、&*ptr = borrow、Box::from_raw = from_foreign
use std::ffi::c_void;
struct DeviceState { value: i32 }
impl Drop for DeviceState {
    fn drop(&mut self) { println!("DeviceState({}) dropped  <- release: from_foreign", self.value); }
}
struct FakeFile { private_data: *mut c_void }
fn open() -> FakeFile {                          // 對照 T::open -> Pin<KBox> -> into_foreign
    println!("open: 存進 private_data（into_foreign）");
    FakeFile { private_data: Box::into_raw(Box::new(DeviceState { value: 41 })) as *mut c_void }
}
fn ioctl_borrow(f: &FakeFile) {                  // 對照 ioctl: ForeignOwnable::borrow（借用）
    let s = unsafe { &*(f.private_data as *const DeviceState) };
    println!("ioctl: 借出 private_data，讀到 value={}", s.value);
}
fn release(f: FakeFile) {                        // 對照 release: from_foreign -> drop
    let s = unsafe { Box::from_raw(f.private_data as *mut DeviceState) };
    println!("release: 取回所有權（from_foreign）");
    drop(s);
}
fn main() {
    let f = open();
    ioctl_borrow(&f); ioctl_borrow(&f);          // 多次 ioctl，都借用，不釋放
    release(f);                                  // 只有這裡釋放
    println!("done");
}
```

本機真跑輸出：

```
open: 存進 private_data（into_foreign）
ioctl: 借出 private_data，讀到 value=41
ioctl: 借出 private_data，讀到 value=41
release: 取回所有權（from_foreign）
DeviceState(41) dropped  <- release: from_foreign
done
```

`ioctl_borrow` 呼兩次都只讀不釋放，只有 `release` 那次真的取回並 drop——這正是 misc device 生命週期：多次 ioctl/read/write 借用 private_data，最後 close 一次釋放。C 你靠紀律確保「ioctl 裡別 free、release 裡 free 一次」，Rust 靠型別（`borrow` 不能釋放、`from_foreign` 消費所有權）。

## ioctl 命令解碼：`_IO` / `_IOR` / `_IOW`

ioctl 的 `cmd` 是一個 32-bit 整數，但它**不是隨便一個數**——它是編碼過的，塞了四個欄位（方向、type、number、size）。C 的 `_IO`/`_IOR`/`_IOW`/`_IOWR` 巨集（`include/asm-generic/ioctl.h`）就是把這四個欄位打包成那個數：

```
  32-bit ioctl cmd 的佈局（x86 泛型）：
  ┌────────┬──────────────┬──────────┬──────────┐
  │ dir(2) │   size(14)   │ type(8)  │  nr(8)   │
  └────────┴──────────────┴──────────┴──────────┘
   31    30 29           16 15       8 7        0
   dir: NONE/READ/WRITE（資料流向）   size: 資料大小（sizeof）
   type: 幻數（區分子系統）           nr: 命令序號
```

`_IO(type, nr)`：無資料（dir=NONE, size=0）。`_IOR::<T>(type, nr)`：讀（kernel→user，dir=READ, size=sizeof(T)）。`_IOW::<T>(type, nr)`：寫（user→kernel, dir=WRITE, size=sizeof(T)）。`_IOWR`：雙向。**dir 是站在 user 視角**：`_IOR` = user 讀 = kernel 寫給 user。

RfL 的 `kernel::ioctl` 提供**同樣語意**的 `_IO`/`_IOR`/`_IOW`/`_IOWR`（`rust/kernel/ioctl.rs`，2026-08 逐字），差別是型別用泛型：

```rust
// RfL kernel::ioctl 的真實定義（節錄）
pub const fn _IO(ty: u32, nr: u32) -> u32 { _IOC(_IOC_NONE, ty, nr, 0) }
pub const fn _IOR<T>(ty: u32, nr: u32) -> u32 { _IOC(_IOC_READ, ty, nr, core::mem::size_of::<T>()) }
pub const fn _IOW<T>(ty: u32, nr: u32) -> u32 { _IOC(_IOC_WRITE, ty, nr, core::mem::size_of::<T>()) }
pub const fn _IOC_SIZE(nr: u32) -> usize { ((nr >> _IOC_SIZESHIFT) & _IOC_SIZEMASK) as usize }
```

`_IOR::<i32>('|' as u32, 0x81)` = 「type=`'|'`(0x7c)、nr=0x81、dir=READ、size=4」。`ioctl` handler 裡 `_IOC_SIZE(cmd)` 把 size 欄位解出來（`UserSlice::new(arg, size)` 用它當長度）。這套純算術本機能真跑，且**結果和 C 的巨集一致**（可拿 C 的 `printf("%#x", _IOR('|', 0x81, int))` 對）：

```rust
// 本機真跑：復刻 _IO/_IOR/_IOW/_IOC_SIZE（依 asm-generic/ioctl.h 的位移/遮罩，x86 泛型）
const IOC_NRBITS: u32 = 8; const IOC_TYPEBITS: u32 = 8; const IOC_SIZEBITS: u32 = 14;
const IOC_NRSHIFT: u32 = 0;
const IOC_TYPESHIFT: u32 = IOC_NRSHIFT + IOC_NRBITS;
const IOC_SIZESHIFT: u32 = IOC_TYPESHIFT + IOC_TYPEBITS;
const IOC_DIRSHIFT: u32 = IOC_SIZESHIFT + IOC_SIZEBITS;
const IOC_SIZEMASK: u32 = (1 << IOC_SIZEBITS) - 1;
const IOC_NONE: u32 = 0; const IOC_WRITE: u32 = 1; const IOC_READ: u32 = 2;
const fn ioc(dir: u32, ty: u32, nr: u32, size: u32) -> u32 {
    (dir << IOC_DIRSHIFT) | (ty << IOC_TYPESHIFT) | (nr << IOC_NRSHIFT) | (size << IOC_SIZESHIFT)
}
const fn io(ty: u32, nr: u32) -> u32 { ioc(IOC_NONE, ty, nr, 0) }
const fn ior<T>(ty: u32, nr: u32) -> u32 { ioc(IOC_READ, ty, nr, core::mem::size_of::<T>() as u32) }
const fn iow<T>(ty: u32, nr: u32) -> u32 { ioc(IOC_WRITE, ty, nr, core::mem::size_of::<T>() as u32) }
const fn ioc_size(nr: u32) -> u32 { (nr >> IOC_SIZESHIFT) & IOC_SIZEMASK }
fn main() {
    println!("HELLO     = {:#010x}", io('|' as u32, 0x80));
    let g = ior::<i32>('|' as u32, 0x81);
    let s = iow::<i32>('|' as u32, 0x82);
    println!("GET_VALUE = {:#010x}  size={}", g, ioc_size(g));
    println!("SET_VALUE = {:#010x}  size={}", s, ioc_size(s));
}
```

本機真跑輸出：

```
HELLO     = 0x00007c80
GET_VALUE = 0x80047c81  size=4
SET_VALUE = 0x40047c82  size=4
```

拆 `GET_VALUE = 0x80047c81`：低 8 bits `0x81`=nr、次 8 bits `0x7c`=type(`'|'`)、size 欄位 `0x004`=4（`sizeof(i32)`）、最高 dir bits `0b10`=READ（`0x8000_0000` 那位）。`SET_VALUE = 0x40047c82` 的 dir 是 `0b01`=WRITE。這和 sample 裡 C 使用者程式的 `_IOR('|', 0x81, int)` / `_IOW('|', 0x82, int)` **完全對得上**——這就是為什麼 Rust driver 和 C user 程式能用同一組 ioctl 號溝通。

## 對比與取捨

| 面向 | C 字元/misc device | RfL misc device | 型別擋掉的 bug |
|---|---|---|---|
| 註冊 | 填 `file_operations` + `misc_register`/`misc_deregister` 手動配對 | `MiscDeviceRegistration<T>`（建構註冊、Drop 反註冊） | 忘 `misc_deregister`（leak/殘留節點） |
| handler | `fops.open/read_iter/write_iter/ioctl` 函式指標 | `impl MiscDevice` trait 方法 | 函式簽章對不上（Rust 編譯期抓） |
| 私有狀態 | `file->private_data`（`void*`，手動 cast） | `Pin<KBox<T>>` via `ForeignOwnable`（型別化） | cast 錯型別（UB）、release 忘 kfree |
| user 資料 | `copy_from_user`/`copy_to_user`，可被繞過直接 deref | `UserSlice`（碰不到裸指標，方法全 safe） | 直接 deref user 指標、傳 kernel 位址進來 |
| 讀進固定型別 | `copy_from_user(&struct, ..)` 任意 bytes | `read::<T>()` 要求 `T: FromBytes` | 讀進非法 enum/user 控制的指標 |
| double-fetch | 重複 `copy_from_user` 同址（TOCTOU） | reader 讀了就前進，重讀要明確 `clone_reader` | 意外雙重取用 |
| ioctl 號 | `_IOR('|',0x81,int)` 手寫型別 | `_IOR::<i32>(..)` 泛型，size 自動 | 型別和 size 對不上 |
| 上鎖 | `mutex_lock`/`unlock` 手動配對 | `Mutex<Inner>` + RAII guard | error path 忘 unlock（死鎖）、沒鎖存取 |
| 錯誤 | `return -EFAULT`/`-ENOTTY` 手動 | `Err(EFAULT)`/`Err(ENOTTY)` + `?` | 忘檢查回傳、忘傳播 |

總原則和 [Ch 38](./38-kernel-abstractions.md) 一致：**RfL 的 misc device 抽象把 C `file_operations` 每個「你必須做對」的危險點（user 指標、private_data 型別、鎖、長度、錯誤碼）變成型別強制的規則。** 你 kernel_pwn 打過的那些洞（直接 deref user 指標拿任意讀寫、double-fetch、release 的 UAF），大部分在這層寫不出來。

## 踩雷集錦

1. **以為 misc device 讀寫是 `read`/`write`**：**不是**——主線 `MiscDevice` trait 提供 `read_iter`/`write_iter`（走 `iov_iter`/`Kiocb`），對照 C 現代 `file_operations` 的 `.read_iter`/`.write_iter`。舊教材或簡化文章寫 `fn read(...)` 是過時或不準。以你那棵 kernel 的 `rust/kernel/miscdevice.rs` 為準（本章依 2026-08 主線 `v7.2-rc5`）。

2. **想直接解引用 `ioctl` 的 `arg`**：`arg: usize` 是**使用者位址**，你**絕不能** `unsafe { *(arg as *const T) }`——那會 oops、或更糟：user 傳 kernel 位址進來，你替他任意讀寫 kernel 記憶體（你 kernel_pwn 最愛的原語）。永遠透過 `UserSlice::new(arg, size).reader()/.writer()`。RfL 甚至不給你方便的方式拿裸指標，正是為了逼你走這條門。

3. **`read::<T>()` 對帶指標/enum 的型別編不過，別 transmute 繞過**：`read::<T>()` 要求 `T: FromBytes`（任何 bit pattern 合法）。帶 `enum`、`bool`、裸指標的 struct 不滿足（user 可塞非法值）。編不過是**保護你**——別用 `transmute`（[Ch 18](./18-unsafe-advanced.md)）硬繞，那等於把 C 的「`copy_from_user` 進一個帶指標的 struct」漏洞搬回來。要嘛只讀 POD、要嘛讀進 `[u8; N]` 後自己驗證。

4. **在持鎖時 `copy_*_user`（可能睡眠）**：`copy_from_user`/`copy_to_user`（`UserSlice` 的 `read`/`write`）**可能睡眠**（user 頁要 fault 拉回）。持 **spinlock** 時做這個是 bug（spinlock 不可睡，[Ch 38](./38-kernel-abstractions.md) 的 `GFP_ATOMIC` 那節）；持 `Mutex` 雖可睡但拖長臨界區也不好。sample 的 `get_value` 特意 `drop(guard)` 後才 `writer.write::<i32>()`——先解鎖再 copy。這是 kernel 領域知識，Rust 型別**不會**幫你抓（它不知道 `copy_to_user` 會睡），要靠你懂。

5. **忘記 `MiscDeviceRegistration` 必須活著**：`_miscdev` 一旦 drop 就 `misc_deregister`，`/dev` 節點消失。它存在 module struct 裡（`RustMiscDeviceModule._miscdev`），module 活著它就活著。如果你不小心讓它提早 drop（例如在 `init` 裡建了但沒放進回傳的 struct），裝置註冊完立刻反註冊，`/dev/rust-misc-device` 根本不會出現。這對照 C 你把 `struct miscdevice` 放全域（活整個 module 生命週期）。

6. **`module!` 用 `Module` 而非 `InPlaceModule`**：持有 `MiscDeviceRegistration`（不可 move）的 module 要 `impl InPlaceModule`（`init` 回 `impl PinInit<Self, Error>`），不是 [Ch 39](./39-first-kernel-module.md) 的 `Module`（回 `Result<Self>`）。用錯會編不過（registration 不能 move 進 `Ok(Self{...})`）。這是「哪些 module 需要 pin-init」的判準：**持有任何不可 move 欄位（registration、內嵌 `Mutex` 等）就要 `InPlaceModule`**。

## 進階：再往深一層

- **`#[vtable]` 與 `HAS_*` 常數**：`MiscDevice` trait 的方法多數有 default（`build_error!` 佔位），你只 impl 需要的。`#[vtable]` 巨集為每個方法生一個 `HAS_XXX: bool` 常數（你有沒有 override），kernel crate 據此決定 `file_operations` 對應 slot 填函式指標還是 `None`（看 `miscdevice.rs` 的 `const VTABLE`：`read_iter: if T::HAS_READ_ITER { Some(..) } else { None }`）。這是「編譯期決定 fops 表長相」的機制——對照 C 你手填 `.read_iter = ...` 或留 NULL。
- **`Kiocb` 與 `iov_iter` 的深水區**：`read_iter`/`write_iter` 的 `Kiocb`（帶 `ki_pos` 檔案位置）、`IovIterDest`/`IovIterSource`（分散/聚集緩衝）對應 C 的 `struct kiocb` + `struct iov_iter`。真正做非阻塞、`O_DIRECT`、`io_uring` 路徑的 driver 要深入這套。sample 用 `simple_read_from_buffer`/`copy_from_iter_vec` 是最簡路徑。想深入讀 `rust/kernel/iov.rs` 和 C 的 `Documentation/filesystems/` 對 `iov_iter` 的說明。
- **`clone_reader` 與合法的 double-fetch**：有時你**需要**讀同一塊 user 記憶體兩次（先讀長度、再依長度讀內容——但這本身就是 TOCTOU 溫床）。`UserSliceReader::clone_reader()` 開第二個 reader 讓你明確這麼做。正確模式是「讀進 kernel 一次、之後只信 kernel 那份 copy」——`UserSlice` 的設計就是推你往這個方向。這正是你 kernel_pwn 找 double-fetch 洞時該檢查的點：driver 有沒有對同一 user 位址取用兩次還信第二次。
- **面試/研究角度**：能講清楚「為什麼 ioctl 的 arg 不能直接 deref、user 傳 kernel 位址會怎樣」「`copy_from_user` 內部做了什麼（`access_ok` + fault fixup）」「`UserSlice` 怎麼讓 driver 碰不到裸 user 指標」「`read::<T: FromBytes>` 擋掉哪類 bug」「`file->private_data` 的所有權往返在 Rust 怎麼型別化」「double-fetch 怎麼被 reader-advances 擋住」，就是真懂這章，而且這些正是你做 kernel_pwn 時反過來要找的洞。

## 動手練習

1. **本機驗 ioctl 號 + 對 C**：跑本章 ioctl 解碼 demo，然後寫一支 C：`#include <sys/ioctl.h>`，`printf("%#x\n", _IOR('|', 0x81, int));` 編譯執行，確認和 Rust 的 `0x80047c81` 一致。改幾個 nr/type/size，觀察哪些 bit 變。這讓你對「ioctl cmd 是編碼的、不是隨便的數」有實感。

2. **本機改 `UserSliceReader` demo 體會防護**：把本章那個 reader demo 改成「讀第一個 u32 當長度 N，再讀 N bytes」，故意讓 N 大於剩餘長度，觀察第二次讀回 `EFAULT` 而不是越界讀。這模擬 driver 收到惡意長度時 `UserSlice` 的越界保護——對照 C 若不檢查 `copy_from_user` 的 len 會怎樣。

3. **紙上把一個 C ioctl handler 改寫成 RfL**：找一個你熟的 C 字元裝置 `unlocked_ioctl`（有 `switch(cmd)` + `copy_from_user`/`copy_to_user` + `file->private_data`），在紙上改寫成 `MiscDevice::ioctl` + `UserSlice` + `Mutex` 形狀。標出：C 版有幾個「直接 deref user 指標」或「忘檢查 copy 回傳」的潛在洞，Rust 版怎麼結構性消掉。這個對照做完，你會很清楚 RfL 收掉了你 kernel_pwn 攻擊面的哪一塊。

4. **（有環境的話）真跑 sample**：若你 build 了 Rust-enabled kernel（[Ch 39](./39-first-kernel-module.md) 延伸閱讀的 QEMU 路徑），開 `CONFIG_SAMPLE_RUST_MISC_DEVICE=m`，`insmod rust_misc_device.ko`，跑 sample 檔頭附的那支 C user 程式（`open` /dev/rust-misc-device → `ioctl` HELLO/GET/SET），`dmesg` 對照本章預期輸出。做了就把本章「未實測」在你筆記裡改成「已驗」。

## 本章重點整理

- misc device 是「一個 `/dev` 節點跑我的 handler」最省事的路：C 填 `file_operations` + `misc_register`/`misc_deregister`；RfL 用 `MiscDeviceRegistration<T>`（建構註冊、Drop 反註冊）+ `impl MiscDevice`（`open`/`ioctl`/`read_iter`/`write_iter`）。持有 registration 的 module 用 `InPlaceModule`（pin-init，因為 registration 不可 move）。
- **user 資料傳遞是攻擊面核心**：`ioctl` 的 `arg` 是 user 位址，**絕不能直接 deref**（會 oops，或 user 傳 kernel 位址進來 = 任意讀寫）。RfL 用 `UserSlice`/`UserSliceReader`/`UserSliceWriter` 包住——所有方法 safe、越界回 `EFAULT`、內部才是 `copy_from_user`/`copy_to_user`（帶 `// SAFETY:` 契約）。`read::<T: FromBytes>` 擋掉「讀進非法 enum/指標」，reader-advances 擋掉 double-fetch/TOCTOU。
- **每裝置私有狀態**（對照 `file->private_data`）：`Pin<KBox<T>>` via `ForeignOwnable`——`open` 回物件 → kernel crate `into_foreign()` 存進 private_data → `ioctl` `borrow()` 借出 → `release` `from_foreign()` 取回 → Drop。型別化（不是 `void*`）、所有權清楚、release 忘不了釋放。用 `Mutex<Inner>` 保護可變狀態。
- **ioctl 號**：`_IO`/`_IOR::<T>`/`_IOW::<T>` 把 dir/type/nr/size 編進 32-bit cmd（和 C 巨集語意一致、結果一致），`_IOC_SIZE(cmd)` 解出資料大小當 `UserSlice` 長度。錯誤回 `Result`→errno（`ENOTTY` 未知命令、`EFAULT` 壞位址）。
- 本章 RfL API 依主線 `v7.2-rc5`（2026-08 逐字查證 `rust_misc_device.rs`/`miscdevice.rs`/`uaccess.rs`/`ioctl.rs`）；**未穩定、會變**。build/insmod/QEMU **未實測、理論預期**；本機真跑的是 ioctl 解碼、reader 位置前進、private_data 所有權往返三段純 Rust 邏輯（`rustc 1.97.1`）。

## 自我檢核

- [ ] 不看筆記，能說出 misc device 的四個 handler（`open`/`ioctl`/`read_iter`/`write_iter`）各對照 C `file_operations` 的什麼，以及註冊/反註冊怎麼從手動 `misc_register`/`deregister` 變成 registration 的建構/Drop。
- [ ] 能解釋為什麼 `ioctl` 的 `arg` 不能直接 deref（至少講出「user 傳 kernel 位址 = 任意讀寫」這個攻擊），以及 `UserSlice` 怎麼讓你碰不到裸 user 指標。
- [ ] 能講出 `copy_from_user` 內部做了什麼（`access_ok` + fault fixup + 回沒 copy 的 byte 數），以及 `read_raw` 怎麼包它、`// SAFETY:` 契約在哪。
- [ ] 能解釋 `read::<T: FromBytes>` 的約束擋掉哪類 bug、reader-advances 怎麼擋 double-fetch/TOCTOU——並連到你 kernel_pwn 找過的對應洞。
- [ ] 能說出 `file->private_data` 的所有權往返（`into_foreign`/`borrow`/`from_foreign`）對照 C 的 `void*` 手動管，型別化了什麼、release 為什麼忘不了釋放。

## 延伸閱讀

### 官方文件 / 一手來源

- **[samples/rust/rust_misc_device.rs](https://github.com/torvalds/linux/blob/master/samples/rust/rust_misc_device.rs)（主線 kernel 樹）** — 本章逐字引用的來源
  - **讀哪裡**：全檔。檔頭的 C user 程式（`open`/`ioctl` HELLO/GET/SET/FAIL）是對照 Rust driver 的另一半；`impl MiscDevice` 的四個方法、`set_value`/`get_value` 的 `UserSlice` 用法、ioctl `match`。
  - **學到什麼**：本章所有片段的完整、可對照上下文；`InPlaceModule`、`#[vtable]`、`ARef<Device>`、`KVVec` 的真實用法。
  - **前提**：讀完本章 + [Ch 38](./38-kernel-abstractions.md) 的 pin-init/`ForeignOwnable`；帶「對照 C `file_operations` 的什麼」去讀最有效。

- **kernel 樹 `rust/kernel/uaccess.rs`（[rust.docs.kernel.org](https://rust.docs.kernel.org/kernel/uaccess/index.html)）** — `UserSlice` 權威
  - **讀哪裡**：`UserSlice`/`UserSliceReader`/`UserSliceWriter` 的 struct doc（尤其 TOCTOU/double-fetch/EFAULT 那幾段，本章直接引用）、`read`/`read_raw`/`read::<T>`/`write_slice` 的實作與 `// SAFETY:`。
  - **學到什麼**：kernel↔user 邊界安全封裝的**真實**原始碼——`copy_from_user`/`copy_to_user` 包在哪、`FromBytes` 約束為何、reader 為何讀了就前進。
  - **前提**：懂本章 user 指標為何危險 + [Ch 18](./18-unsafe-advanced.md) 的 `FromBytes`/`MaybeUninit`；想真懂「安全封裝擋掉什麼」的下一步。

- **kernel 樹 `rust/kernel/miscdevice.rs` 與 `rust/kernel/ioctl.rs`**（同 rustdoc 站）
  - **讀哪裡**：`miscdevice.rs` 的 `MiscDevice` trait 定義、`MiscDeviceRegistration::register`（`misc_register`）、open/release/ioctl 的 `unsafe extern "C"` shim（`private_data` 的 `into_foreign`/`from_foreign`/`borrow`）、`const VTABLE: file_operations`；`ioctl.rs` 全檔（`_IO`/`_IOR`/`_IOC_SIZE` 的位移定義）。
  - **學到什麼**：本章「底層機制」兩節（private_data 往返、fops vtable、ioctl 位元佈局）的一手依據。
  - **前提**：懂本章 handler 對照 + [Ch 38](./38-kernel-abstractions.md) 的 `ForeignOwnable`/pin-init。

### 書籍 / C 對照

- **《Linux Device Drivers, 3rd ed.》(LDD3) 的 char driver 與 ioctl 章** — Corbet/Rubini/Kroah-Hartman（O'Reilly, 2005，[LWN 免費線上](https://lwn.net/Kernel/LDD3/)）
  - **讀哪裡**：Ch 3「Char Drivers」（`file_operations`/`open`/`release`/`read`/`write`）、Ch 6「Advanced Char Driver Operations」的 ioctl 節（`_IO`/`_IOR`/`_IOW`、`access_ok`、`copy_*_user`）。
  - **學到什麼**：本章對照的 **C 那半邊**的權威解釋——尤其 ioctl 號編碼和 `copy_from_user` 的語意，看懂 C 版你才知道 RfL 收掉了什麼。
  - **前提**：無（這是 C driver 經典）。注意：書年份舊（2005），`register_chrdev`/`ioctl` 細節（如 `unlocked_ioctl` 取代 `ioctl`、`read_iter` 取代 `read`）現代有演進，但 ioctl 號編碼與 `copy_*_user` 的核心概念不變——這正是本章挑它當對照的部分。

現在你能寫一個真的有功能、跟 user space 溝通、每裝置有狀態的 Rust driver 了。但這章你也看到：`UserSlice`、`ForeignOwnable`、misc registration 這些「安全」的封裝，內部全是 unsafe 的 C binding 呼叫。下一章專門拆這件事——**kernel 裡的 unsafe 和 userland 差在哪**（更多 raw pointer、更嚴的 invariant：中斷/鎖/preemption/原子上下文不能睡）、kernel crate 怎麼把 C 的生命週期/所有權規則編碼進型別（`ARef` 綁 refcount、guard 綁鎖、pin 綁不可移動），以及**同一個 driver bug（漏鎖 race / UAF / 直接 deref user 指標）在 C 版可能編過、生產爆炸，在 Rust 版怎麼被擋在編譯期或縮到可稽核的 unsafe**——直接連你 kernel_pwn 的攻擊視角。

→ [Ch 41 kernel unsafe 與安全性](./41-kernel-unsafe-safety.md)
