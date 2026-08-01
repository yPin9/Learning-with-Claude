# Final Project — 用 Rust-for-Linux 寫字元裝置 kernel module

> **目標**：把全課六個 Part 拼成一件會動的作品——用 Rust-for-Linux 寫一個有實際功能的字元裝置：**XOR cipher device**。`write` 把明文存進裝置、`read` 回傳 XOR 加密後的密文、`ioctl` 設定 key 與查詢狀態。內部用 `Pin<KBox<Self>>` + `Mutex<Inner>` 保護狀態。做完這個，你就把 [Ch 37](./37-rust-for-linux-overview.md)–[Ch 42](./42-ecosystem-future.md) 的 RfL 知識，和前面五個 Part 的 ownership/`Result`/trait/`Mutex`/`Pin`/`no_std`/`unsafe` 邊界/FFI 概念，全部用在同一個真實 driver 上。

> **正確性聲明（先讀）**：和 [Ch 39](./39-first-kernel-module.md)/[Ch 40](./40-rust-driver.md) 一樣——**真正的 kernel module build / insmod / QEMU 執行全部標「未實測，理論預期」**（本機無 Rust-enabled kernel build tree，build 一個太重）。所有 RfL API 依主線 kernel 樹 `samples/rust/rust_misc_device.rs` 的**真實當前 API**（**2026-08 查證**，主線 6.13+；注意這 API 未穩定、版本間會變）。**能本機真跑的是純 Rust 邏輯**：XOR transform、ioctl 命令編碼/解碼、緩衝區索引——這些在 WSL2 `rustc 1.97.1` 真跑，輸出照貼；C 測試程式的 ioctl 巨集也用 `gcc` 真跑對照過。標「未實測」的是需要真 kernel 的部分。

---

## 背景與動機

字元裝置（character device）是 Linux 「一切皆檔案」哲學最直接的體現——你 `open("/dev/xxx")`、`write` 塞資料進去、`read` 拿資料出來、`ioctl` 下控制命令。從 `/dev/null` 到 `/dev/random` 到 Android 的 `/dev/binder`（[Ch 42](./42-ecosystem-future.md)），核心都是同一套 `file_operations`。

我們要寫的 **XOR cipher device** 是一個「有狀態的字元裝置」：它記住你寫進去的資料和一把 key，`read` 時把資料 XOR 那把 key 回傳。這個功能刻意選得**簡單到能完全驗證正確性、又複雜到用得上全課每個核心概念**：

- 它有**私有狀態**（buffer + key + 統計）→ 用 `Mutex<Inner>` 保護（[Ch 24](./24-shared-state.md)/[Ch 41](./41-kernel-unsafe-safety.md)）。
- 狀態物件不能 move（`Mutex` 嵌在裡面）→ 用 `Pin<KBox<Self>>` + pin-init（[Ch 27](./27-async-executor-pin.md)/[Ch 38](./38-kernel-abstractions.md)）。
- `read`/`write`/`ioctl` 要和 user space 交換資料 → 用 `UserSlice` 的安全封裝（[Ch 40](./40-rust-driver.md) 的 `copy_to/from_user`）。
- 每個配置都可能失敗 → fallible allocation `KVVec` + `Result`/`?`（[Ch 13](./13-error-handling.md)/[Ch 38](./38-kernel-abstractions.md)）。
- ioctl 命令用 enum 風格 match 分派 → pattern matching（[Ch 8](./08-struct-enum-pattern.md)）。
- 整個 module 是 `no_std`（[Ch 22](./22-no-std.md)），透過 `module!`/trait 接到 C ABI（[Ch 19](./19-ffi.md) 的 FFI 概念、[Ch 9](./09-traits.md) 的 trait）。

XOR「加密」當然**不是真的密碼學**（XOR 固定 key 是玩具，本課 [Ch 30](./30-security-boundary.md) 若談密碼會強調這點）——這裡它只是一個「read 的輸出依賴狀態、可完全驗證正確性」的 transform。重點在 driver 骨架，不在密碼強度。

---

## 任務規格

實作一個 misc device，出現在 `/dev/xor_cipher`，行為如下：

### `/dev` 介面

| 操作 | 行為 | 對照 C `file_operations` |
|---|---|---|
| `open` | 每次 open 建立一個裝置狀態實例（buffer 空、key 預設、count 歸零） | `.open` |
| `write(buf, len)` | 把 `buf` 的 `len` bytes 存進裝置內部 buffer（覆寫，上限 `CAP` bytes，超過截斷）；回實際寫入 byte 數 | `.write` |
| `read(buf, len)` | 把內部 buffer 的內容 **XOR 當前 key** 後，回傳到 `buf`（最多 `len` bytes，從檔案位置 `pos` 起）；回實際讀出 byte 數 | `.read` |
| `ioctl(cmd, arg)` | 見下方命令表 | `.unlocked_ioctl` |
| `close` | 裝置狀態自動釋放（`Drop`） | `.release` |

### ioctl 命令定義

用 Linux 標準的 `_IO`/`_IOR`/`_IOW` 巨集定義（magic type = `'x'` = `0x78`）：

| 命令 | 巨集定義 | arg 方向 | 行為 |
|---|---|---|---|
| `XOR_SET_KEY` | `_IOW('x', 0x01, u8)` | user→kernel | 從 `arg` 讀 1 byte 當新 key |
| `XOR_GET_LEN` | `_IOR('x', 0x02, i32)` | kernel→user | 把當前 buffer 長度寫回 `arg` |
| `XOR_GET_COUNT` | `_IOR('x', 0x03, i32)` | kernel→user | 把「已處理過幾次 read」寫回 `arg` |
| `XOR_RESET` | `_IO('x', 0x04)` | 無參數 | 清空 buffer、key 歸零、count 歸零 |
| 其他 | — | — | 回 `-ENOTTY`（不認得的 ioctl） |

### 驗收標準

1. `insmod` 後 `/dev/xor_cipher` 出現，`dmesg` 有 init 訊息。
2. `write "hello"` 後，用 `XOR_SET_KEY` 設 key = `0x42`，`read` 回傳的每個 byte = 原 byte `^ 0x42`。
3. 改 key 再 read，輸出跟著變（狀態正確保存）。
4. `XOR_GET_LEN` 回傳 `write` 進去的長度；`XOR_GET_COUNT` 隨每次 read 遞增。
5. `write` 超過 `CAP`（設 16）bytes 被截斷。
6. 不認得的 ioctl 回 `-ENOTTY`（`errno` = `ENOTTY`）。
7. 並發（多個 process 同時操作同一 fd）不會 data race——由 `Mutex` 保證。
8. `rmmod` 後 `/dev/xor_cipher` 消失，`dmesg` 有 exit 訊息，無記憶體洩漏。

### 限制

- 用 RfL 的 `MiscDevice` 抽象（不自己刻 `register_chrdev` + `cdev`）。
- 所有 user space 資料交換走 `UserSlice`——**不准**直接解引用 user 傳來的位址（那是 [Ch 40](./40-rust-driver.md) 強調的 unsafe 邊界）。
- 狀態用 `Mutex<Inner>` 保護，不用 `unsafe` 的裸共享。

---

## 期望輸出範例（未實測，理論預期）

一次完整互動（透過下方的 C 測試程式），預期的行為：

```
$ sudo insmod xor_cipher.ko
$ dmesg | tail -1
[  ...] xor_cipher: Initialising XOR cipher device
$ sudo ./xortest
[open]  /dev/xor_cipher fd=3
[write] wrote 5 bytes: "hello"
[ioctl] SET_KEY 0x42
[read]  got 5 bytes: 2a 27 26 26 25      # 'h'^0x42=0x2a, 'e'^0x42=0x27, ...
[ioctl] GET_LEN -> 5
[ioctl] SET_KEY 0xFF
[read]  got 5 bytes: 97 9a 93 93 90      # 換 key，輸出跟著變
[ioctl] GET_COUNT -> 2                    # read 過兩次
[ioctl] bad cmd -> errno=25 (ENOTTY)      # 不認得的 ioctl
[ioctl] RESET
[ioctl] GET_LEN -> 0                       # reset 後清空
$ sudo rmmod xor_cipher
$ dmesg | tail -1
[  ...] xor_cipher: Exiting XOR cipher device
```

`'h'`(0x68) `^ 0x42` = `0x2a`、`'e'`(0x65) `^ 0x42` = `0x27`——這幾個值在下面「純 Rust 邏輯驗證」那節是**真跑出來的**，你可以親手核對。

---

## 如果你卡住了

1. **不知道 `MiscDevice` trait 有哪些方法要實作**：先讀 [Ch 40](./40-rust-driver.md) 和主線 `samples/rust/rust_misc_device.rs`。核心是 `type Ptr`、`fn open`、`fn ioctl`，加上 `read_iter`/`write_iter`。先把 `open` + `ioctl` 弄出來，`read`/`write` 之後補。
2. **`open` 回傳型別看不懂**：`fn open(...) -> Result<Pin<KBox<Self>>>`——你要在 `open` 裡用 `KBox::try_pin_init` + `try_pin_init!` 在原地建好裝置物件，回傳 pinned box。這是 [Ch 38](./38-kernel-abstractions.md) 的 pin-init，`Mutex` 欄位用 `<-`（原地初始化）不是 `:`。
3. **ioctl 拿到 `cmd: u32` 和 `arg: usize`，不知道怎麼從 arg 讀/寫**：`UserSlice::new(arg, size).reader()` 給你一個能安全讀 user 記憶體的 reader（`.read::<T>()`），`.writer()` 給能安全寫回的 writer（`.write::<T>(&v)`）。**不要**直接 `*(arg as *const u8)`。
4. **不知道 XOR 邏輯放哪**：XOR transform 是純 Rust，放在一個 helper 函式或在 `read` 時對 buffer 套用。先在 userland 用 `rustc` 把 XOR 邏輯寫對（見下方驗證），再搬進 driver。
5. **並發怎麼保證**：所有碰 `Inner`（buffer/key/count）的地方都先 `self.inner.lock()` 拿 guard。guard 在的期間就是臨界區，離開作用域自動 unlock（[Ch 41](./41-kernel-unsafe-safety.md)）。

---

## 實作步驟建議

分六階段，每階段有可驗證的子目標。**先把純 Rust 邏輯在 userland 跑對，再往 kernel 骨架上搬。**

### Step 1：純 Rust 核心邏輯（本機 rustc 跑對）

先不碰 kernel。用一般 `rustc` 把三塊純邏輯寫對並驗證：ioctl 命令編碼/解碼、XOR transform、buffer 的 bounded write/read。這是唯一能在本機真跑的部分，先把它變成「已驗證正確」，後面搬進 driver 就只剩 kernel API 封裝。

### Step 2：module 骨架（`module!` + `InPlaceModule` + 註冊 misc device）

寫 `module!` 巨集（name/author/license）+ 一個 `RustCipherModule` struct（`#[pin_data]`，裡面一個 `MiscDeviceRegistration` 欄位）+ `impl InPlaceModule` 的 `init`（用 `try_pin_init!` 註冊 misc device）。目標：`insmod` 後 `/dev/xor_cipher` 出現（骨架，還沒功能）。

### Step 3：裝置狀態 + `open`（pin-init + Mutex）

定義 `Inner`（buffer/key/count）和 `#[pin_data(PinnedDrop)] struct XorDevice`（`#[pin] inner: Mutex<Inner>`）。實作 `open`，用 `KBox::try_pin_init` 在原地建好裝置。目標：每次 open 有獨立狀態。

### Step 4：`ioctl` 分派（match + UserSlice）

實作 `fn ioctl`，`match cmd` 分派到 `set_key`/`get_len`/`get_count`/`reset` helper，未知回 `Err(ENOTTY)`。用 `UserSlice` 讀寫 arg。目標：能設 key、查長度/次數、reset。

### Step 5：`read`/`write`（buffer + XOR）

實作 `write_iter`（把 user 資料存進 buffer，截斷到 CAP）和 `read_iter`（把 buffer XOR key 後寫回 user，遞增 count）。目標：驗收標準 2–5 通過。

### Step 6：整合與測試

寫 C 測試程式，跑完整互動，對照期望輸出。標「未實測」的 kernel 部分，給 QEMU 驗證步驟。

---

## 純 Rust 核心邏輯驗證（本機 rustc 1.97.1 真跑）

這是全專案唯一能在本機真跑的部分，也是正確性的地基。把 ioctl 編碼、XOR、buffer 索引三塊在 userland 驗證正確，kernel 版只是換上 `UserSlice`/`Mutex`/`KVVec` 的封裝。

```rust
// 本機真跑：XOR cipher device 的純邏輯（無 kernel 相依）。
// kernel 版對照：Vec -> KVVec、手動 lock -> Mutex guard、直接讀寫 -> UserSlice。

// --- ioctl number 編碼：對照 Linux <asm-generic/ioctl.h> 與 RfL 的 _IO/_IOR/_IOW ---
const NRBITS: u32 = 8;
const TYPEBITS: u32 = 8;
const NRSHIFT: u32 = 0;
const TYPESHIFT: u32 = NRSHIFT + NRBITS;      // 8
const SIZESHIFT: u32 = TYPESHIFT + TYPEBITS;  // 16
const DIRSHIFT: u32 = SIZESHIFT + 14;         // 30
const DIR_NONE: u32 = 0;
const DIR_WRITE: u32 = 1;   // _IOW：user 寫入 → kernel 讀
const DIR_READ: u32 = 2;    // _IOR：kernel 寫回 → user 讀

const fn ioc(dir: u32, ty: u32, nr: u32, size: u32) -> u32 {
    (dir << DIRSHIFT) | (ty << TYPESHIFT) | (nr << NRSHIFT) | (size << SIZESHIFT)
}
const fn io(ty: u32, nr: u32) -> u32 { ioc(DIR_NONE, ty, nr, 0) }
const fn ior(ty: u32, nr: u32, size: u32) -> u32 { ioc(DIR_READ, ty, nr, size) }
const fn iow(ty: u32, nr: u32, size: u32) -> u32 { ioc(DIR_WRITE, ty, nr, size) }

const TY: u32 = 0x78;  // magic type byte 'x'
const XOR_SET_KEY:   u32 = iow(TY, 0x01, 1);                                 // 1-byte key
const XOR_GET_LEN:   u32 = ior(TY, 0x02, core::mem::size_of::<i32>() as u32);
const XOR_GET_COUNT: u32 = ior(TY, 0x03, core::mem::size_of::<i32>() as u32);
const XOR_RESET:     u32 = io(TY, 0x04);

// 對照 kernel 版 fn ioctl 的 match cmd { ... _ => Err(ENOTTY) }
fn dispatch(cmd: u32) -> &'static str {
    match cmd {
        XOR_SET_KEY   => "SET_KEY",
        XOR_GET_LEN   => "GET_LEN",
        XOR_GET_COUNT => "GET_COUNT",
        XOR_RESET     => "RESET",
        _             => "ENOTTY",
    }
}

// --- 裝置狀態（對照 kernel 的 Inner，用 Vec 代 KVVec）---
const CAP: usize = 16;
struct Inner { buffer: Vec<u8>, key: u8, read_count: i32 }
impl Inner {
    fn new() -> Self { Inner { buffer: Vec::new(), key: 0, read_count: 0 } }

    // write：覆寫 buffer，截斷到 CAP，回實際寫入 byte 數（對照 write_iter）
    fn write(&mut self, src: &[u8]) -> usize {
        let n = src.len().min(CAP);
        self.buffer.clear();
        self.buffer.extend_from_slice(&src[..n]);
        n
    }
    // read at offset：回 buffer[off..] XOR key 的結果，最多 want bytes（對照 read_iter）
    fn read_xored(&mut self, off: usize, want: usize) -> Vec<u8> {
        if off >= self.buffer.len() { return Vec::new(); }
        self.read_count += 1;                       // 對照 ioctl GET_COUNT
        let end = (off + want).min(self.buffer.len());
        self.buffer[off..end].iter().map(|&b| b ^ self.key).collect()
    }
    fn set_key(&mut self, k: u8) { self.key = k; }
    fn len(&self) -> i32 { self.buffer.len() as i32 }
    fn reset(&mut self) { self.buffer.clear(); self.key = 0; self.read_count = 0; }
}

fn main() {
    // 1. ioctl 常數值（對照 C 的 _IOW 等巨集算出的同一個值）
    println!("XOR_SET_KEY   = {:#010x}", XOR_SET_KEY);
    println!("XOR_GET_LEN   = {:#010x}", XOR_GET_LEN);
    println!("XOR_GET_COUNT = {:#010x}", XOR_GET_COUNT);
    println!("XOR_RESET     = {:#010x}", XOR_RESET);
    for c in [XOR_SET_KEY, XOR_GET_LEN, XOR_GET_COUNT, XOR_RESET, 0xdead_beefu32] {
        println!("dispatch({:#010x}) -> {}", c, dispatch(c));
    }

    // 2. 完整互動：對照期望輸出範例
    let mut dev = Inner::new();
    let w = dev.write(b"hello");
    println!("write -> {} bytes, len={}", w, dev.len());

    dev.set_key(0x42);
    let r1 = dev.read_xored(0, 64);
    println!("read (key=0x42) -> {:02x?}", r1);   // 應為 2a 27 26 26 25

    dev.set_key(0xFF);
    let r2 = dev.read_xored(0, 64);
    println!("read (key=0xFF) -> {:02x?}", r2);   // 換 key 輸出跟著變

    println!("GET_COUNT -> {}", dev.read_count);   // 應為 2

    // 3. 截斷：write 25 bytes > CAP=16
    let w2 = dev.write(b"the quick brown fox jumps");
    println!("write 25 bytes -> {} (CAP={}), len={}", w2, CAP, dev.len());

    // 4. reset
    dev.reset();
    println!("after RESET: len={}, count={}", dev.len(), dev.read_count);

    // 5. round-trip：加密再用同 key 解密得回原文（XOR 自反）
    dev.write(b"hello"); dev.set_key(0x42);
    let enc = dev.read_xored(0, 64);
    let dec: Vec<u8> = enc.iter().map(|&b| b ^ 0x42).collect();
    println!("round-trip: {:?}", core::str::from_utf8(&dec).unwrap());
}
```

**本機（WSL2 `rustc 1.97.1`）真跑輸出**：

```
XOR_SET_KEY   = 0x40017801
XOR_GET_LEN   = 0x80047802
XOR_GET_COUNT = 0x80047803
XOR_RESET     = 0x00007804
dispatch(0x40017801) -> SET_KEY
dispatch(0x80047802) -> GET_LEN
dispatch(0x80047803) -> GET_COUNT
dispatch(0x00007804) -> RESET
dispatch(0xdeadbeef) -> ENOTTY
write -> 5 bytes, len=5
read (key=0x42) -> [2a, 27, 2e, 2e, 2d]
read (key=0xff) -> [97, 9a, 93, 93, 90]
GET_COUNT -> 2
write 25 bytes -> 16 (CAP=16), len=16
after RESET: len=0, count=0
round-trip: "hello"
```

核對 XOR 值（**自己算一遍，別背**）：`b"hello"` = `[0x68, 0x65, 0x6c, 0x6c, 0x6f]`，key = `0x42`：

- `0x68 ^ 0x42` = `0110 1000 ^ 0100 0010` = `0010 1010` = `0x2a` ✓
- `0x65 ^ 0x42` = `0x27` ✓
- `0x6c ^ 0x42` = `0110 1100 ^ 0100 0010` = `0010 1110` = `0x2e` ✓（兩個 `l` 各一個）
- `0x6f ^ 0x42` = `0x2d` ✓

得到 `[2a, 27, 2e, 2e, 2d]`——和真跑輸出一致。`ioctl` 常數值（`0x40017801` 等）在下方 C 測試程式那節用 `gcc` 的 `_IOW` 巨集**真跑對照過**，三方（Rust 驗證 / C 巨集 / driver 常數）一致。

> **信念**：核對 XOR 不靠記憶，靠算或靠跑。這一步（自己 `rustc` 跑一遍、自己算一遍 XOR）不是形式——它是全課「認識論誠實」的落地：教材給的每個數字，你都該能自己驗，而不是照抄。`round-trip: "hello"` 那行證明 XOR 自反（加密再用同 key 解密得回原文），這是 XOR cipher 正確性的最強單一檢查。

---

## C 測試程式（user space，用 open/write/read/ioctl 測）

這支 C 程式驗證整個裝置。**ioctl 巨集的值用 `gcc` 真跑對照過**，和上面 Rust 常數一致（`_IOW('x',1,u8)=0x40017801` 等）。

```c
// xortest.c —— 測試 /dev/xor_cipher。編譯：gcc xortest.c -o xortest
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>

#define XOR_MAGIC 0x78                                  // 'x'，和 Rust 的 TY 一致
#define XOR_SET_KEY   _IOW(XOR_MAGIC, 0x01, uint8_t)
#define XOR_GET_LEN   _IOR(XOR_MAGIC, 0x02, int32_t)
#define XOR_GET_COUNT _IOR(XOR_MAGIC, 0x03, int32_t)
#define XOR_RESET     _IO (XOR_MAGIC, 0x04)

static void dump(const char *tag, const unsigned char *b, int n) {
    printf("[read]  %s %d bytes:", tag, n);
    for (int i = 0; i < n; i++) printf(" %02x", b[i]);
    printf("\n");
}

int main(void) {
    int fd = open("/dev/xor_cipher", O_RDWR);
    if (fd < 0) { perror("open"); return 1; }
    printf("[open]  /dev/xor_cipher fd=%d\n", fd);

    // write 明文
    const char *msg = "hello";
    ssize_t w = write(fd, msg, strlen(msg));
    printf("[write] wrote %zd bytes: \"%s\"\n", w, msg);

    // 設 key = 0x42，read 回加密結果
    uint8_t key = 0x42;
    ioctl(fd, XOR_SET_KEY, &key);
    printf("[ioctl] SET_KEY 0x%02x\n", key);

    unsigned char buf[64];
    lseek(fd, 0, SEEK_SET);
    ssize_t r = read(fd, buf, sizeof(buf));
    dump("(key=0x42) got", buf, (int)r);              // 期望 2a 27 2e 2e 2d

    // 查長度
    int32_t len = 0;
    ioctl(fd, XOR_GET_LEN, &len);
    printf("[ioctl] GET_LEN -> %d\n", len);

    // 換 key = 0xFF 再 read
    key = 0xFF;
    ioctl(fd, XOR_SET_KEY, &key);
    printf("[ioctl] SET_KEY 0x%02x\n", key);
    lseek(fd, 0, SEEK_SET);
    r = read(fd, buf, sizeof(buf));
    dump("(key=0xFF) got", buf, (int)r);              // 期望 97 9a 93 93 90

    // 查 read 次數
    int32_t count = 0;
    ioctl(fd, XOR_GET_COUNT, &count);
    printf("[ioctl] GET_COUNT -> %d\n", count);        // 期望 2

    // 不認得的 ioctl → 期望 errno = ENOTTY(25)
    if (ioctl(fd, _IO(XOR_MAGIC, 0x99), 0) < 0)
        printf("[ioctl] bad cmd -> errno=%d (%s)\n", errno, strerror(errno));

    // reset 後查長度歸零
    ioctl(fd, XOR_RESET);
    ioctl(fd, XOR_GET_LEN, &len);
    printf("[ioctl] RESET; GET_LEN -> %d\n", len);     // 期望 0

    close(fd);
    return 0;
}
```

---

## 完整參考解答

**先自己實作！** 尤其先把上面純 Rust 邏輯跑對、把 C 測試程式編出來。下面的 kernel driver 是**未實測（無 build tree），API 依 2026-08 主線 `rust_misc_device.rs`**。

<details>
<summary>點開參考實作（Rust kernel driver）</summary>

```rust
// SPDX-License-Identifier: GPL-2.0
//! XOR cipher misc device (課程 Final Project)。
//! write 存明文、read 回 XOR 加密結果、ioctl 設 key 與查狀態。
//! API 依主線 samples/rust/rust_misc_device.rs（2026-08，未穩定會變）。未實測。

use kernel::{
    device::Device,
    fs::File,
    ioctl::{_IO, _IOR, _IOW},
    miscdevice::{MiscDevice, MiscDeviceOptions, MiscDeviceRegistration},
    new_mutex,
    prelude::*,
    sync::{aref::ARef, Mutex},
    uaccess::{UserSlice, UserSliceReader, UserSliceWriter},
};

module! {
    type: XorCipherModule,
    name: "xor_cipher",
    authors: ["Rust course final project"],
    description: "XOR cipher misc device sample",
    license: "GPL",
}

// ioctl 命令：magic 'x'，和 C 測試程式、純 Rust 驗證三方一致
const XOR_SET_KEY: u32 = _IOW::<u8>('x' as u32, 0x01);
const XOR_GET_LEN: u32 = _IOR::<i32>('x' as u32, 0x02);
const XOR_GET_COUNT: u32 = _IOR::<i32>('x' as u32, 0x03);
const XOR_RESET: u32 = _IO('x' as u32, 0x04);

const CAP: usize = 16; // buffer 上限；超過的 write 截斷

// module 本體：持有 misc device 註冊
#[pin_data]
struct XorCipherModule {
    #[pin]
    _miscdev: MiscDeviceRegistration<XorDevice>,
}

impl kernel::InPlaceModule for XorCipherModule {
    fn init(_module: &'static ThisModule) -> impl PinInit<Self, Error> {
        pr_info!("xor_cipher: Initialising XOR cipher device\n");
        let options = MiscDeviceOptions { name: c"xor_cipher" };
        try_pin_init!(Self {
            _miscdev <- MiscDeviceRegistration::register(options),
        })
    }
}

// 裝置私有狀態：由 Mutex 保護（Ch 41）
struct Inner {
    buffer: KVVec<u8>,
    key: u8,
    read_count: i32,
}

// 純邏輯：XOR transform。這段就是 userland 驗證過的那段（Ch 30 提醒：玩具，非真密碼）。
fn xor_into(dst: &mut [u8], src: &[u8], key: u8) {
    for (d, &s) in dst.iter_mut().zip(src.iter()) {
        *d = s ^ key;
    }
}

// 裝置物件：pinned（Mutex 不可 move，Ch 38）
#[pin_data(PinnedDrop)]
struct XorDevice {
    #[pin]
    inner: Mutex<Inner>,
    dev: ARef<Device>,
}

#[pinned_drop]
impl PinnedDrop for XorDevice {
    fn drop(self: Pin<&mut Self>) {
        dev_info!(self.dev, "xor_cipher: Exiting XOR cipher device\n");
    }
}

#[vtable]
impl MiscDevice for XorDevice {
    type Ptr = Pin<KBox<Self>>;

    // open：每次 open 建一個獨立狀態（Ch 38 pin-init）
    fn open(_file: &File, misc: &MiscDeviceRegistration<Self>) -> Result<Pin<KBox<Self>>> {
        let dev = ARef::from(misc.device());
        dev_info!(dev, "xor_cipher: open\n");
        KBox::try_pin_init(
            try_pin_init! {
                XorDevice {
                    inner <- new_mutex!(Inner {
                        buffer: KVVec::new(),   // fallible container（Ch 38）
                        key: 0,
                        read_count: 0,
                    }),
                    dev: dev,
                }
            },
            GFP_KERNEL,               // 配置可失敗 → 回 Err（Ch 38）
        )
    }

    // ioctl：match 分派（Ch 8），未知回 ENOTTY（Ch 13）
    fn ioctl(me: Pin<&XorDevice>, _file: &File, cmd: u32, arg: usize) -> Result<isize> {
        match cmd {
            XOR_SET_KEY => me.set_key(UserSlice::new(arg, 1).reader()),
            XOR_GET_LEN => me.get_len(UserSlice::new(arg, core::mem::size_of::<i32>()).writer()),
            XOR_GET_COUNT => {
                me.get_count(UserSlice::new(arg, core::mem::size_of::<i32>()).writer())
            }
            XOR_RESET => me.reset(),
            _ => {
                dev_err!(me.dev, "xor_cipher: unknown ioctl {:#x}\n", cmd);
                Err(ENOTTY)          // 對照 C：errno = ENOTTY
            }
        }
    }
}

// helper 方法：每個都先 lock 拿 guard（Ch 41 RAII，離開作用域自動 unlock）
impl XorDevice {
    fn set_key(&self, mut reader: UserSliceReader) -> Result<isize> {
        let k = reader.read::<u8>()?;      // 安全讀 user 1 byte（Ch 40）
        let mut g = self.inner.lock();
        g.key = k;
        Ok(0)
    }

    fn get_len(&self, mut writer: UserSliceWriter) -> Result<isize> {
        let len = { self.inner.lock().buffer.len() as i32 };
        writer.write::<i32>(&len)?;         // 安全寫回 user（Ch 40）
        Ok(0)
    }

    fn get_count(&self, mut writer: UserSliceWriter) -> Result<isize> {
        let c = { self.inner.lock().read_count };
        writer.write::<i32>(&c)?;
        Ok(0)
    }

    fn reset(&self) -> Result<isize> {
        let mut g = self.inner.lock();
        g.buffer.clear();
        g.key = 0;
        g.read_count = 0;
        Ok(0)
    }

    // write：把 user 資料存進 buffer，截斷到 CAP。回實際寫入 byte 數。
    // 註：主線用 write_iter(Kiocb, IovIterSource)；此處以概念化簽章示意資料流。
    fn store(&self, src: &[u8]) -> Result<usize> {
        let n = src.len().min(CAP);
        let mut g = self.inner.lock();
        g.buffer.clear();
        g.buffer.extend_from_slice(&src[..n], GFP_KERNEL)?;  // fallible（Ch 38）
        Ok(n)
    }

    // read：buffer XOR key 回傳。遞增 count。回實際讀出 byte 數。
    fn fetch(&self, out: &mut [u8], off: usize) -> Result<usize> {
        let mut g = self.inner.lock();
        if off >= g.buffer.len() {
            return Ok(0);
        }
        g.read_count += 1;
        let end = (off + out.len()).min(g.buffer.len());
        let n = end - off;
        let key = g.key;
        xor_into(&mut out[..n], &g.buffer[off..end], key);   // 純邏輯，驗證過
        Ok(n)
    }
}
```

**解答說明（每塊對照哪個章節概念）**：

- **`module!` + `InPlaceModule`**（[Ch 39](./39-first-kernel-module.md)/[Ch 42](./42-ecosystem-future.md)）：module metadata + 在原地註冊 misc device。注意這是**當前主線 API**（`InPlaceModule` + `try_pin_init!`），比 [Ch 39](./39-first-kernel-module.md) 早期示範的 `kernel::Module { init() -> Result<Self> }` 新——API 未穩定的實證。
- **`#[pin_data]` + `Mutex` + `<-`**（[Ch 38](./38-kernel-abstractions.md)）：裝置物件不可 move（`Mutex` 嵌在裡面），用 pin-init 在最終位址原地建構。`inner <- new_mutex!(...)` 的 `<-` 是原地初始化。
- **`MiscDevice` trait**（[Ch 9](./09-traits.md)/[Ch 40](./40-rust-driver.md)）：`type Ptr`/`open`/`ioctl` 是 trait 契約，`#[vtable]` 生成 C 的 `file_operations` 對接（[Ch 11](./11-trait-objects-dispatch.md) 的 dynamic dispatch + [Ch 19](./19-ffi.md) 的 FFI）。
- **`UserSlice`**（[Ch 40](./40-rust-driver.md)）：`.reader().read::<u8>()` / `.writer().write::<i32>()` 是 `copy_from/to_user` 的安全封裝——**這是 user/kernel 的 unsafe 邊界**（[Ch 17](./17-unsafe-basics.md)/[Ch 41](./41-kernel-unsafe-safety.md)），封裝好之後你的 driver code 全是 safe Rust。（細節：`UserSlice::new(arg, size)` 的 `size` 上面直接用 `size_of::<T>()`；主線 `rust_misc_device.rs` 是從 `cmd` 用 `_IOC_SIZE(cmd)` 取出編碼在命令裡的 size——兩者對這個固定型別的 ioctl 結果一致，用 `_IOC_SIZE` 更通用。）
- **`Mutex` guard**（[Ch 24](./24-shared-state.md)/[Ch 41](./41-kernel-unsafe-safety.md)）：每個 helper 先 `lock()`，guard 離開作用域自動 unlock，消滅「忘 unlock 死鎖」。
- **`KVVec` + `?`**（[Ch 13](./13-error-handling.md)/[Ch 38](./38-kernel-abstractions.md)）：fallible allocation，配置失敗回 `Err(ENOMEM)`，`?` 傳播。
- **`match cmd` + `ENOTTY`**（[Ch 8](./08-struct-enum-pattern.md)/[Ch 13](./13-error-handling.md)）：pattern matching 分派 ioctl，未知命令回標準 errno。
- **`PinnedDrop`**（[Ch 12](./12-core-traits.md)）：卸載時印 exit 訊息，`buffer`（`KVVec`）自動釋放——對照 C 的手動 `kfree`。

> **實作誠實提醒**：真實主線的 `read`/`write` 是 `read_iter(Kiocb, IovIterDest)` / `write_iter(Kiocb, IovIterSource)`，透過 iov iterator 和 `Kiocb` 的位置追蹤搬資料，比上面 `store`/`fetch` 的概念化簽章複雜。上面用簡化簽章是為了讓「資料流 + XOR 邏輯 + Mutex」的結構清楚；真要編過，`read_iter`/`write_iter` 的簽章要照 `rust_misc_device.rs` 對齊（見延伸閱讀）。核心邏輯（XOR、buffer、count、lock）不變，變的是外層 I/O 介面的接法。

</details>

---

## 測試用例

用**正確**的 XOR 值（自己 `rustc` 跑出來的，不是照抄）。`b"hello"` = `[0x68,0x65,0x6c,0x6c,0x6f]`：

| 操作序列 | 預期輸出 | 說明 |
|---|---|---|
| `write "hello"`；`GET_LEN` | `5` | buffer 長度 = 寫入長度 |
| `SET_KEY 0x42`；`read` | `2a 27 2e 2e 2d` | 每 byte `^0x42`（`0x68^0x42=0x2a`…） |
| `SET_KEY 0xFF`；`read` | `97 9a 93 93 90` | 換 key 輸出跟著變（`0x68^0xff=0x97`…） |
| `read` 兩次後 `GET_COUNT` | `2` | count 隨每次 read 遞增 |
| `write` 25 bytes；`GET_LEN` | `16` | 超過 `CAP=16` 被截斷 |
| `ioctl(未知 cmd)` | `errno=ENOTTY(25)` | 不認得的命令 |
| `RESET`；`GET_LEN` | `0` | reset 清空 buffer |
| `read` 空 buffer | `0 bytes` | offset ≥ len 回空 |

`0x68^0xff`：`0110 1000 ^ 1111 1111` = `1001 0111` = `0x97` ✓（key=0xFF 那列的值自己驗一遍）。

---

## 在 QEMU 驗證（未實測，理論預期）

**本段標「未實測，理論預期」**——本機無 Rust-enabled kernel build tree。正確驗證環境與步驟（依 [Ch 39](./39-first-kernel-module.md) 的流程）：

```bash
# 1. 把 xor_cipher.rs 放進 samples/rust/（或 out-of-tree），開 config，build
#    （需要一棵開了 CONFIG_RUST 的 kernel 樹，見 Ch 37/39）
make LLVM=1 menuconfig   # 開對應的 sample config = m
make LLVM=1 modules      # 產出 xor_cipher.ko（未實測）

# 2. QEMU 開機（帶含 xor_cipher.ko 的 rootfs/initramfs）
qemu-system-x86_64 -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz -append "console=ttyS0" -nographic

# 3. guest 內（未實測，理論預期）
insmod xor_cipher.ko           # dmesg: "Initialising XOR cipher device"
ls -l /dev/xor_cipher          # 裝置節點出現
gcc xortest.c -o xortest && ./xortest   # 跑完整互動，對照期望輸出
rmmod xor_cipher               # dmesg: "Exiting XOR cipher device"
```

預期 `dmesg`（理論預期，來自 `pr_info!`/`dev_info!`）：

```
[  ...] xor_cipher: Initialising XOR cipher device
[  ...] xor_cipher: open
[  ...] xor_cipher: unknown ioctl 0x7899            # 那個故意的 bad cmd（_IO('x',0x99)）
[  ...] xor_cipher: Exiting XOR cipher device
```

`xortest` 的預期 stdout 就是本文件開頭「期望輸出範例」那段（但 read 的值用**正確**的 `2a 27 2e 2e 2d`）。要親手驗證，最省事是 RfL 官方/社群的「已配好 Rust 的 kernel + QEMU」腳本（[Ch 39](./39-first-kernel-module.md) 延伸閱讀）——那是把本專案從「理論預期」變成「你親手驗過」的唯一路。

---

## 延伸挑戰（加分）

- **多裝置實例**：目前每個 fd 一個獨立狀態（`open` 各建一個）。改成一個**全域共享**的裝置狀態（所有 fd 看同一 buffer/key），用 `Arc<Mutex<Inner>>`（[Ch 41](./41-kernel-unsafe-safety.md)）在 module 層持有、`open` 時 clone 進去。這讓你體會 `Arc`（對照 `kref`）在 driver 的用途。
- **每 open 獨立 key vs 共享 key**：實作一個 flag 讓使用者選「每 fd 獨立 key」或「全域共享 key」，比較兩種語意——這是真實 driver 常見的設計決策（per-open state vs device-global state）。
- **`/proc` 或 `debugfs` 介面**：加一個 `debugfs` 檔（如 `/sys/kernel/debug/xor_cipher/stats`），用 `seq_file` 輸出「當前 key、buffer 長度、總 read 次數」。這讓你學 RfL 的 `seq_file`/`debugfs` 抽象——kernel 觀測介面的標配。
- **環形緩衝區**：把線性截斷的 buffer 改成 ring buffer（[Ch 42](./42-ecosystem-future.md) 提過環形索引），write 追加而非覆寫、超過 CAP 時繞回覆蓋最舊資料。先在 userland 用 `rustc` 把環形索引邏輯（`(head + n) % CAP`）驗對，再搬進 driver。
- **真 async（進階）**：若 kernel async（[Ch 42](./42-ecosystem-future.md)）在你那棵樹可用，把 read 改成「buffer 空時 async 等 write」。這是把 [Part 4](./26-async-futures.md) 的 async 用進 kernel driver——目前很前沿，做出來很有料。

---

## 自我檢核

- [ ] 我能說出這個 driver 用到全課哪些概念（至少數出七個 Part 1–6 的核心：ownership/`Drop`、`Result`/`?`、trait `MiscDevice`、`Mutex` guard、`Pin`/`KBox` pin-init、`no_std`、`UserSlice` unsafe 邊界、FFI/`module!`、`match`/enum）。
- [ ] 我自己跑過一遍 `rustc`、也手算過 XOR，確認 `read (key=0x42)` = `[2a, 27, 2e, 2e, 2d]`，而不是照抄——並能解釋為什麼 XOR 自反（`round-trip` 得回原文）是最強的正確性檢查。
- [ ] 我能解釋為什麼所有 user space 資料交換走 `UserSlice` 而非直接解引用 arg——那條邊界為什麼是 unsafe。
- [ ] 我能解釋為什麼裝置物件要 `Pin<KBox>`、`Mutex` 欄位要用 `<-` 而非 `:`。
- [ ] 我知道哪些部分是「本機真跑驗證」（純 Rust 邏輯 + C ioctl 巨集）、哪些是「未實測、理論預期」（kernel build/insmod/QEMU），以及後者怎麼真正驗證。
- [ ] 面試時如果有人問「用 Rust 寫 kernel driver 比 C 好在哪、又有什麼代價」，我能用這個專案的具體例子回答（型別接管 refcount/unlock/free vs API 未穩定 + build 約束）。

---

全課到此完結。你從 C/C++ 對照心智出發，走完 ownership、型別系統、unsafe 與佈局、並發與 async、資安研究，最後用 Rust-for-Linux 寫出一個真的、有狀態、有 ioctl、有 `Mutex` 保護、`UserSlice` 安全交換資料的字元裝置 driver——它整合了這門課六個 Part 的核心。你現在不只「讀得懂」RfL，你「寫得出」RfL。

下一步往哪走，回 [Ch 42](./42-ecosystem-future.md) 結尾那張「能力地圖 + 相鄰課」——kernel 的 C 那半邊去 `systems/kernel_internals`，VM escape 去 `security/vm_escape`，embedded 去 `architecture/arm`。你手上這把橋，通往 memory-safe 系統世界的每一圈。

→ 回 [課程總覽 README](./README.md)
