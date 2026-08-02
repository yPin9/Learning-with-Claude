# Ch 22 — KCOV 底層

> **目標**：理解 KCOV 如何讓 kernel coverage 對 userland 程式可見，能寫出完整的 KCOV 收集程式，並理解它和 LLVM trace-pc-guard（Ch 3）在機制上的異同。

> **環境**：本章有實際可跑的 C 程式，但需要有 `CONFIG_KCOV=y` 的 kernel。WSL2 預設 kernel 通常**不含** KCOV，執行前請確認（見下方「驗證環境」段落）。能跑就跑，不能跑的段落標注「**本段未實測，為理論預期行為**」並給出自 build 驗證步驟。

## 為什麼需要 KCOV？

回顧 Ch 3 的核心問題：fuzzer 需要知道「剛才那次輸入讓 target 跑過了哪些程式碼」，才能判斷這個 input 是否值得加入 corpus（coverage 有增加），才能導引突變方向。

在 userland fuzzing 裡，這個問題用 compile-time 插樁（`-fsanitize-coverage`）解決——在每個 basic block 或 edge 上插入一條 `__sanitizer_cov_trace_pc()` 呼叫，累積到一個 bitmap 裡。

在 kernel fuzzing 裡，你遇到兩個問題：

**問題一**：kernel 跑在 ring 0，你的 fuzzer 跑在 ring 3，跑過的 PC 地址是 kernel virtual address，userland 程式怎麼拿到？

**問題二**：kernel 是 shared resource，同一時間可能有幾十個 CPU 都在跑 kernel code。你的 syscall 讓哪些 kernel 路徑跑了，和系統裡其他程式觸發的 kernel 路徑，如何分開？

KCOV 的答案：
- 透過 `/sys/kernel/debug/kcov` 這個 character device 和 mmap，把 kernel 的 coverage 資料共享到 userland 的地址空間
- Per-task 收集：只追蹤當前這個 task（process/thread）觸發的 kernel coverage，其他 CPU 跑的 kernel code 不算

## 建立直覺：一次 syscall 的 coverage 流

```
使用者程式          kernel（ring 0，有 KCOV 插樁）
─────────────       ──────────────────────────────
fd = open(          系統呼叫進入點 do_sys_open()
  "/dev/kcov")          │
                        │  pc = __builtin_return_address(0)
ioctl(fd,               │  if (t->kcov_mode != KCOV_MODE_TRACE_PC) goto done
  KCOV_INIT_TRACE,       ▼
  COVER_SIZE)        vfs_open()
                        │
                        ▼  ← 插樁點：記錄這個 PC
ioctl(fd,          path_openat()
  KCOV_ENABLE,          │
  KCOV_TRACE_PC)         ▼  ← 插樁點
                    may_open()
                        │
mmap(...)               ▼  ← 插樁點
  ↓                 inode_permission()
  共享記憶體           │
  [count | PC_0        ▼
   PC_1 | ...]     do_dentry_open()  ...（更多插樁點）
                        │
write("/sys/...")        ▼
  ↓                 返回 userland
  syscall n        
  ← 插樁追蹤               ↑ 每個插樁點把 PC 寫入共享記憶體

ioctl(fd,
  KCOV_DISABLE)
  
讀取 mmap[0] = N（記錄到的 PC 數）
讀取 mmap[1..N] = kernel PC 序列
```

關鍵點：`mmap[0]` 是計數器，`mmap[1], mmap[2], ...` 是依序記錄的 kernel PC。這塊記憶體同時被 kernel（寫）和 userland（讀）看到。

## 驗證環境：你的 kernel 有沒有 KCOV？

在開始之前，先確認：

```bash
# 方法一：查 kernel config（如果有 /proc/config.gz）
zcat /proc/config.gz 2>/dev/null | grep CONFIG_KCOV
# 期望輸出：CONFIG_KCOV=y

# 方法二：查 sysfs（KCOV 存在的話這個節點存在）
ls -la /sys/kernel/debug/kcov 2>/dev/null
# 如果輸出 "No such file or directory"，kernel 沒有 KCOV

# 方法三：直接試打開
python3 -c "open('/sys/kernel/debug/kcov')" 2>&1

# 在 WSL2 上通常會看到：
# ls: cannot access '/sys/kernel/debug/kcov': No such file or directory
```

如果你的環境有 KCOV，後面的程式可以直接跑。如果沒有，見本章末的「自 build kernel」段落。

## KCOV 的 kernel 側插樁

KCOV 的 kernel 側插樁是在 compile time 完成的，用的 flag 是：

```
-fsanitize-coverage=trace-pc
```

這個 flag 讓 GCC/Clang 在每個 basic block 的入口插入一個 `__sanitizer_cov_trace_pc()` 呼叫。Kernel 的 KCOV 提供了這個 hook 的實作：

```c
/* kernel/kcov.c（簡化版）*/
void notrace __sanitizer_cov_trace_pc(void)
{
    struct task_struct *t;
    unsigned long *area;
    unsigned long ip = _RET_IP_;  /* 就是 __builtin_return_address(0) */
    unsigned long pos;

    t = current;
    if (!t || !t->kcov_size || !t->kcov_area)
        return;  /* 這個 task 沒有啟用 KCOV */
    
    /* 只追蹤 SOFTIRQ 外的路徑（避免中斷噪音）*/
    if (!in_task())
        return;

    area = t->kcov_area;   /* 指向那塊 mmap 的共享記憶體 */
    pos = READ_ONCE(area[0]) + 1;
    if (pos < t->kcov_size) {
        area[pos] = ip;
        WRITE_ONCE(area[0], pos);
    }
}
```

`t->kcov_area` 指向的就是 userland 用 mmap 拿到的那塊記憶體。每次 `__sanitizer_cov_trace_pc()` 被呼叫，就在 `area[0]`（計數）後面的下一個 slot 寫入當前的 instruction pointer。

### 和 LLVM trace-pc-guard 的對映（回顧 Ch 3）

Ch 3 介紹的 `trace-pc-guard` 在 edge 上插樁，並提供一個 `__sanitizer_cov_trace_pc_guard()` callback：

| | LLVM trace-pc-guard（Ch 3，userland）| KCOV（kernel）|
|---|---|---|
| 插樁粒度 | edge（兩個 basic block 之間）| basic block 入口 |
| Callback | `__sanitizer_cov_trace_pc_guard()` | `__sanitizer_cov_trace_pc()` |
| 資料結構 | 由使用者實作（通常 bitmap）| kernel 維護，寫到 per-task array |
| 共享機制 | 直接 in-process 記憶體 | mmap + kernel character device |
| 去雜訊 | 靠 process isolation 天然解決 | per-task + in_task() 過濾 |

本質上是同一個 LLVM 插樁機制，在 kernel 和 userland 的兩種不同實作。

## /sys/kernel/debug/kcov 的 mmap+ioctl 介面

KCOV 暴露為一個 character device，介面由三個 ioctl 組成：

```c
/* include/uapi/linux/kcov.h */

/* 初始化：告訴 kernel 你要追蹤多少個 PC */
#define KCOV_INIT_TRACE     _IOR('c', 1, unsigned long)

/* 開始追蹤當前 task */
#define KCOV_ENABLE         _IO('c', 100)

/* 停止追蹤 */
#define KCOV_DISABLE        _IO('c', 101)

/* trace mode 有兩種 */
#define KCOV_TRACE_PC       0   /* 只記 PC（用於 coverage 引導）*/
#define KCOV_TRACE_CMP      1   /* 也記 comparison（用於 cmp feedback）*/
```

**完整使用流程**：

```
open("/sys/kernel/debug/kcov")
       ↓
ioctl(fd, KCOV_INIT_TRACE, N)   ← 告知 trace buffer 大小
       ↓
mmap(...)  ← 拿到共享記憶體指標
       ↓
ioctl(fd, KCOV_ENABLE, KCOV_TRACE_PC)  ← 對當前 task 開始追蹤
       ↓
[執行你要測試的 syscall]
       ↓
ioctl(fd, KCOV_DISABLE)   ← 停止追蹤
       ↓
讀 cover[0] = PC 數量，讀 cover[1..N] = PC 序列
       ↓
munmap / close
```

## 完整 KCOV 收集程式

**本段在有 `CONFIG_KCOV=y` 的 kernel 上可真跑。WSL2 預設 kernel 通常無 KCOV，標注「未實測」—— 需自 build kernel 才能驗證（見本章末）。**

```c
/* kcov_demo.c
 * 收集一次 open("/etc/hostname") 的 kernel coverage
 * 編譯：gcc -O0 -o kcov_demo kcov_demo.c
 * 執行：sudo ./kcov_demo
 * 需求：kernel 需有 CONFIG_KCOV=y，debugfs 已掛載
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <linux/kcov.h>    /* KCOV_INIT_TRACE, KCOV_ENABLE 等 */
#include <errno.h>
#include <string.h>

#define COVER_SIZE  (64 << 10)   /* trace buffer: 64K 個 entry */

int main(void)
{
    int kcov_fd;
    unsigned long *cover;
    unsigned long n, i;

    /* 1. 開啟 KCOV device */
    kcov_fd = open("/sys/kernel/debug/kcov", O_RDWR);
    if (kcov_fd < 0) {
        perror("open /sys/kernel/debug/kcov");
        fprintf(stderr, "是否確認 kernel 有 CONFIG_KCOV=y 且 debugfs 已掛載？\n");
        return 1;
    }

    /* 2. 初始化 trace buffer */
    if (ioctl(kcov_fd, KCOV_INIT_TRACE, COVER_SIZE) < 0) {
        perror("ioctl KCOV_INIT_TRACE");
        close(kcov_fd);
        return 1;
    }

    /* 3. mmap：把 trace buffer 映射到使用者空間 */
    cover = (unsigned long *)mmap(NULL,
                                  COVER_SIZE * sizeof(unsigned long),
                                  PROT_READ | PROT_WRITE,
                                  MAP_SHARED,
                                  kcov_fd, 0);
    if (cover == MAP_FAILED) {
        perror("mmap");
        close(kcov_fd);
        return 1;
    }

    /* 4. 清零計數器，開始追蹤 */
    __atomic_store_n(&cover[0], 0, __ATOMIC_RELAXED);
    if (ioctl(kcov_fd, KCOV_ENABLE, KCOV_TRACE_PC) < 0) {
        perror("ioctl KCOV_ENABLE");
        goto cleanup;
    }

    /* ==== 被追蹤的程式碼區段 ==== */
    /* 這個 open() 呼叫進入 kernel，kernel 裡跑過的每個 PC 都會被記錄 */
    {
        int tmp_fd = open("/etc/hostname", O_RDONLY);
        if (tmp_fd >= 0) close(tmp_fd);
    }
    /* ==== 被追蹤的程式碼區段結束 ==== */

    /* 5. 停止追蹤 */
    if (ioctl(kcov_fd, KCOV_DISABLE, 0) < 0) {
        perror("ioctl KCOV_DISABLE");
        goto cleanup;
    }

    /* 6. 讀取結果 */
    n = __atomic_load_n(&cover[0], __ATOMIC_RELAXED);
    printf("open(\"/etc/hostname\") 觸發了 %lu 個 kernel PC\n", n);

    /* 印出前 20 個 PC（kernel virtual address）*/
    unsigned long limit = n < 20 ? n : 20;
    printf("\n前 %lu 個 kernel PC：\n", limit);
    for (i = 1; i <= limit; i++) {
        printf("  [%3lu] 0x%lx\n", i, cover[i]);
    }

    if (n > 20) {
        printf("  ... 還有 %lu 個 PC 未印出\n", n - 20);
    }

cleanup:
    munmap(cover, COVER_SIZE * sizeof(unsigned long));
    close(kcov_fd);
    return 0;
}
```

**本段未實測，為理論預期行為**。在有 KCOV 的 kernel 上預期輸出類似：

```
open("/etc/hostname") 觸發了 1823 個 kernel PC

前 20 個 kernel PC：
  [  1] 0xffffffff812a4e30
  [  2] 0xffffffff812a4e50
  [  3] 0xffffffff812b1020
  [  4] 0xffffffff811d3c10
  [  5] 0xffffffff811d3c40
  [  6] 0xffffffff811d4120
  [  7] 0xffffffff81237800
  [  8] 0xffffffff81237830
  [  9] 0xffffffff812379a0
  [ 10] 0xffffffff8124b200
  ... （以下省略）
```

PC 的具體數值依 kernel 版本和 KASLR 偏移不同。這些 kernel virtual address 可以用 `/proc/kallsyms` 解析成函式名稱（需要 `kptr_restrict=0`）。

## 如何把 PC 對映回函式名稱

**本段未實測，為理論預期行為**。

```bash
# 暫時關閉 kptr_restrict（否則 kallsyms 全部顯示 0）
sudo sysctl kernel.kptr_restrict=0

# 解析一個 PC：假設 PC = 0xffffffff812a4e30
PC=0xffffffff812a4e30

# 方法一：直接 grep（按地址排序，找最近的符號）
sudo awk -v addr="$PC" '
    $1 <= addr { last=$3; lastaddr=$1 }
    $1 > addr { print last "@" lastaddr; exit }
' /proc/kallsyms

# 方法二：用 addr2line（需要帶 debug info 的 vmlinux）
# addr2line -e vmlinux -f $PC

# 還原 kptr_restrict
sudo sysctl kernel.kptr_restrict=1
```

syzkaller 在內部做的事基本上就是這樣——收集一次 syscall 序列的 KCOV 輸出，把 PC 列表雜湊成 corpus 的 signal，判斷這次執行是否探索了新路徑。

## remote coverage：KCOV_REMOTE_ENABLE

基本的 KCOV 只追蹤當前 task 的 kernel 路徑。問題是：有些 kernel 操作是**非同步**的——你呼叫了一個 ioctl，kernel 把工作推給一個 worker thread 或 softirq，然後立刻回傳給你。那個 worker thread 跑的 kernel code，基本的 KCOV_ENABLE 追蹤不到。

`KCOV_REMOTE_ENABLE` 解決這個問題，讓你能追蹤跨 task 的 coverage：

```c
/* include/uapi/linux/kcov.h */
#define KCOV_REMOTE_ENABLE  _IOW('c', 102, struct kcov_remote_arg)

struct kcov_remote_arg {
    __u32       trace_mode;   /* KCOV_TRACE_PC 或 KCOV_TRACE_CMP */
    __u32       num_handles;
    __aligned_u64 common_handle;
    __aligned_u64 handles[0];  /* 要追蹤的 remote subsystem handles */
};
```

**使用場景**：fuzz USB driver 時，你的 userland 程式插入一個虛擬 USB 裝置，kernel 的 USB 子系統在一個 kthread 裡處理，這個 kthread 的 coverage 你用基本 KCOV 拿不到。KCOV_REMOTE_ENABLE 讓 syzkaller 追蹤這類情況。

**本段未實測，為理論預期行為**。

## KCOV 與 syzkaller 的整合

syzkaller 的 syz-executor（在 VM 裡跑的那個元件，Ch 24 詳述）就是一個複雜版的上述程式。它：

1. 開啟 KCOV device，設定 trace buffer
2. 呼叫 `KCOV_ENABLE` 開始追蹤
3. 執行一個 syzkaller program（一系列 syscall）
4. 呼叫 `KCOV_DISABLE` 結束追蹤
5. 讀取 trace buffer，去除重複的 PC，轉成 edge hash（把相鄰兩個 PC 組成 pair 算 hash）
6. 透過共享記憶體把這個 coverage signal 回傳給 syz-fuzzer

這個流程每次 syscall 序列執行都要跑一遍，所以效率很重要——trace buffer 用 uint64 array 加上 atomic 操作，盡量避免同步開銷。

```
syz-executor 內部（偽代碼）:
──────────────────────────────────────────
fd = open("/sys/kernel/debug/kcov")
ioctl(fd, KCOV_INIT_TRACE, COVER_SIZE)
cover = mmap(...)

for each syscall in program:
    cover[0] = 0              // 清零
    ioctl(fd, KCOV_ENABLE, KCOV_TRACE_PC)
    
    execute_syscall(nr, args) // 實際 syscall
    
    ioctl(fd, KCOV_DISABLE)
    n = cover[0]
    for i in 1..n:
        edges.add(hash(cover[i-1], cover[i]))  // edge hash

report_edges_to_fuzzer(edges)
```

## 自 build kernel（在 QEMU 上驗證 KCOV）

如果你在 WSL2 或沒有 KCOV 的環境，以下是最快速的驗證路徑：

```bash
# 在 WSL2 / Linux 上執行

# 1. 安裝依賴
sudo apt-get install -y build-essential libncurses-dev bison flex \
    libssl-dev libelf-dev qemu-system-x86 debootstrap

# 2. 下載 kernel source（選一個穩定版本）
wget https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.30.tar.xz
tar xf linux-6.6.30.tar.xz
cd linux-6.6.30

# 3. 生成最小 config，啟用 KCOV 和 KASAN
make defconfig
scripts/config --enable CONFIG_KCOV
scripts/config --enable CONFIG_KASAN
scripts/config --enable CONFIG_KASAN_INLINE
scripts/config --enable CONFIG_DEBUG_FS
scripts/config --enable CONFIG_NAMESPACES
scripts/config --enable CONFIG_USER_NS
make olddefconfig

# 4. 編譯（-j$(nproc) 平行）
make -j$(nproc) 2>&1 | tail -5
# 預期最後幾行：
# Kernel: arch/x86/boot/bzImage is ready

# 5. 建立最小 rootfs（用 debootstrap 建 Debian）
mkdir /tmp/rootfs
sudo debootstrap --arch=amd64 --include=gcc,make,strace \
    bookworm /tmp/rootfs http://deb.debian.org/debian/
# 建立 /dev/kcov 節點的 init script（略，詳見 syzkaller 文件）

# 6. 在 QEMU 裡啟動
qemu-system-x86_64 \
    -kernel arch/x86/boot/bzImage \
    -append "console=ttyS0 root=/dev/sda rw" \
    -drive file=/tmp/rootfs.img,format=raw \
    -nographic -m 2G \
    -enable-kvm  # 如果有 KVM
```

這是一次性的設定，一旦環境建好，就可以在 QEMU VM 裡編譯上面的 `kcov_demo.c` 並真跑驗證。

syzkaller 官方文件有更完整的 `create-image.sh` script，見「延伸閱讀」。

## KCOV 的開銷

KCOV 開啟後每個 basic block 都有額外的 store 操作，overhead 大約是：

- **CPU overhead**：單 task 追蹤約 10–15% 的執行時間增加
- **記憶體**：每個啟用 KCOV 的 task 佔用 `COVER_SIZE * 8` bytes（64K entry = 512 KB）
- **對系統整體的影響**：因為是 per-task 的，idle 的 task 不會有影響

在 fuzzing 情境下這個 overhead 可接受，因為你需要 coverage 才能有 feedback。在生產 kernel 上永遠不會開 KCOV。

## 踩雷

**錯誤直覺 1**：「KCOV 給我的 PC 是函式入口，所以 N 個 PC = N 個函式。」

KCOV 預設的 `KCOV_TRACE_PC` 在每個 basic block（不是函式）插樁。一個函式可能有幾十個 basic block（每個 if/for/switch 分支都產生新的 BB）。看到 1823 個 PC，代表 1823 個 basic block 被跑過，不是 1823 個函式。

**錯誤直覺 2**：「我在多執行緒程式裡用 KCOV，兩個 thread 都開 KCOV_ENABLE，可以同時追蹤。」

KCOV 是 per-task 的。每個 task 的 `cover` buffer 要獨立分配（各自 `open()` 一個新的 fd，各自 `mmap()`）。如果你在同一個 fd 上讓兩個 thread 都 `KCOV_ENABLE`，第二個 enable 會失敗或覆蓋第一個的追蹤。正確做法：每個 thread 獨立開 `/sys/kernel/debug/kcov` 拿到自己的 fd 和 buffer。

**錯誤直覺 3**：「KCOV 追蹤了我呼叫的 syscall，所以中斷和軟中斷也會被追蹤。」

KCOV 的 `__sanitizer_cov_trace_pc()` 實作裡有 `if (!in_task()) return` 這個過濾。只有在 task context（非 interrupt context）的 kernel 路徑才會被記錄。這樣設計是為了避免中斷帶來的 coverage 噪音——系統背景的網路封包、timer 等都不會汙染你的 trace。但這也意味著 interrupt handler 裡的 bug 靠基本 KCOV 找不到，需要 KCOV_REMOTE_ENABLE 的進一步設定。

**錯誤直覺 4**：「trace buffer 大小隨便設就好，反正 64K 夠大。」

如果你的 syscall 序列很複雜（syzkaller 一次執行幾十個 syscall），64K entries 可能被填滿。一旦滿了，`__sanitizer_cov_trace_pc()` 就直接 return，後面的 PC 全部丟棄，但你不會收到任何錯誤警告——只是 coverage 不完整。syzkaller 預設用更大的 buffer（256K 或更大），並且會監控 `cover[0]` 是否接近上限。

## 進階延伸

- **KCOV_TRACE_CMP**：除了 PC，還記錄 comparison 操作的兩個運算元（`==`、`<` 等），用於「cmp feedback」——讓 fuzzer 知道距離 pass 一個比較條件還差多少，類似 AFL 的 complog。syzkaller 有一個 experimental 的 CMP feedback 模式。
- **kcov-collector 工具**：syzkaller 的 tools/syz-cover 可以把收到的 PC list 對映回 kernel source 的特定行，生成類似 lcov 的覆蓋報告，幫助開發者看哪些程式碼還沒被 fuzz 到。
- **KCOV 與 kernel module**：如果你寫了一個 kernel module 想被 KCOV 追蹤，module 要用 `KCOV_INSTRUMENT_MODULE=y` 編譯，或者在 module 的 Makefile 裡加上 `-fsanitize-coverage=trace-pc`。預設 module 不一定被插樁。

## 動手練習

1. 在你的環境執行「驗證環境」段的三個檢查命令，確認你的 kernel 有沒有 KCOV。如果沒有，記錄 `/proc/config.gz` 裡 KCOV 相關的設定行（`CONFIG_KCOV`、`CONFIG_KCOV_ENABLE_COMPARISONS`）。
2. 閱讀 kernel source `kernel/kcov.c`（線上：https://elixir.bootlin.com/linux/latest/source/kernel/kcov.c）的 `kcov_ioctl_locked()` 函式，理解三個 ioctl command 各自的實作路徑。
3. 在有 KCOV 的環境（自 build 或 VM）跑上面的 `kcov_demo.c`，修改被追蹤的 syscall：改成 `stat("/etc/hostname", &st)`，比較 PC 數量和 `open()` 有什麼差異。
4. 閱讀 syzkaller 的 `executor/executor.cc`（GitHub：google/syzkaller），找到 `cover_enable()` 和 `cover_get()` 函式，對照本章的 mmap+ioctl 流程。

## 本章重點

- KCOV 透過 `-fsanitize-coverage=trace-pc` 在 kernel compile time 插樁，把每個 basic block 的 PC 記錄在 per-task 的 trace buffer 裡。
- userland 透過 `open("/sys/kernel/debug/kcov")` + `KCOV_INIT_TRACE` + `mmap` 拿到共享記憶體，`KCOV_ENABLE` / `KCOV_DISABLE` 控制追蹤範圍。
- Per-task 設計和 `in_task()` 過濾確保你拿到的只是這次 syscall 序列觸發的 kernel coverage，不受其他 CPU 或中斷影響。
- KCOV 是 syzkaller 的 coverage source——executor 收完 trace 後把相鄰 PC pair hash 成 edge signal 回傳給 fuzzer。
- WSL2 預設無 KCOV；需自 build kernel 並在 QEMU 中驗證。

## 自我檢核

- [ ] 我能解釋 `cover[0]` 和 `cover[1..]` 各代表什麼
- [ ] 我能說出 KCOV_INIT_TRACE / KCOV_ENABLE / KCOV_DISABLE 各自在做什麼
- [ ] 我能解釋為什麼 KCOV 是 per-task 的，以及 `in_task()` 過濾的必要性
- [ ] 我知道 `KCOV_TRACE_PC` 的插樁粒度（basic block，不是函式）
- [ ] 我能把 KCOV 和 Ch 3 的 LLVM trace-pc-guard 做對映

## 延伸閱讀

1. **[Linux kernel documentation: KCOV](https://www.kernel.org/doc/html/latest/dev-tools/kcov.html)**
   - 讀哪段：全部，特別是「Usage」section 的兩個範例程式（PC trace 和 CMP trace）。
   - 學什麼：官方文件給的完整 API，包含 remote coverage 的詳細說明，比本章更完整。
   - 關聯：本章所有 ioctl 定義的權威來源。

2. **[Dmitry Vyukov — "KCOV: code coverage for fuzzing"（kernel commit history + lwn.net）](https://lwn.net/Articles/671640/)**
   - 讀哪段：lwn.net 的文章正文，以及對應的 kernel commit message（`git log --grep="kcov" --oneline v4.6`）。
   - 學什麼：KCOV 的設計決策——為什麼選 per-task、為什麼用 mmap 而不是 copy_to_user，是理解設計意圖的最佳入口。
   - 關聯：本章的「為什麼需要 KCOV」段落。

3. **[syzkaller executor 原始碼：executor/executor.cc](https://github.com/google/syzkaller/blob/master/executor/executor.cc)**
   - 讀哪段：`cover_open()`、`cover_enable()`、`cover_get()`、`cover_reset()` 這幾個函式。
   - 學什麼：生產級的 KCOV 使用方式，包含 edge hash 的計算、buffer overflow 的處理、multi-thread coverage 的管理——和本章的簡化版對照，能看出真實系統需要處理的 edge case。
   - 關聯：Ch 24 的 syz-executor 架構。

→ [下一章：KASAN/KMSAN/KCSAN 當 oracle](./23-kernel-sanitizers.md)
