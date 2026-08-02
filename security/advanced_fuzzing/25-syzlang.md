# Ch 25｜syzlang：用 DSL 告訴 syzkaller 該怎麼戳 kernel

> **目標**: 學會用 syzlang 撰寫 syscall description，讓 syzkaller 能正確生成有意義的輸入、追蹤 fd 生命週期，並理解 syz-extract/syz-sysgen 如何把描述轉成 fuzzer 可執行的 Go code。

---

## 為什麼需要 syzlang

AFL 對 binary fuzzing 可以純粹靠 coverage 亂猜。syscall fuzzing 不行——kernel interface 有強結構性：

- `ioctl` 第二個參數是 cmd number，錯了直接 EINVAL，coverage 不會增長
- `write` 到 socket 的 buffer 格式由協議決定，亂填 99% 進不了 parser
- 最關鍵：syscall 有**相依性**。`fcntl` 需要一個合法 fd，而這個 fd 必須先從 `open`/`socket` 拿到

syzkaller 用 syzlang 解決這三個問題：
1. **型別系統**描述每個參數的合法值範圍
2. **resource 機制**追蹤 producer/consumer 鏈（先拿 fd，才能用）
3. **常數提取**從 kernel headers 拉出真實的 ioctl number，不靠人工手打

沒有好的 description，syzkaller 就退化成一個很慢的隨機 syscall caller，找不到洞。

syzkaller 的論文（OSDI 2018）提供了量化數字：有精確 description 的 syscall，有效 prog（能成功執行進入核心邏輯的比例）從 4% 上升到 64%；找到 bug 的速度快了約 5-10 倍。resource 機制是最大貢獻者——光是把「fd 必須先取得」這個約束編進描述，就過濾掉絕大多數必然失敗的 syscall sequence。

---

## 先建立直覺

syzlang description 描述的是「syscall 呼叫圖」，而不只是單一呼叫的格式：

```
描述層 (.txt)                    執行層
┌────────────────────────────┐   ┌─────────────────────────────────┐
│ resource fd_mydev[fd]      │   │ prog 1:                         │
│                            │   │   r0 = openat$mydev(...)        │
│ openat$mydev(...)          │──▶│   ioctl$MYDEV_CMD_SET(r0, ...)  │
│  returns fd_mydev          │   │   ioctl$MYDEV_CMD_GET(r0, ...)  │
│                            │   │   close(r0)                     │
│ ioctl$MYDEV_CMD_SET(       │   └─────────────────────────────────┘
│   fd fd_mydev,             │
│   cmd const[MYDEV_CMD_SET],│   syzkaller 知道 r0 是 fd_mydev
│   arg ptr[in, mydev_req])  │   → 自動把 r0 餵給需要 fd_mydev 的呼叫
└────────────────────────────┘
```

關鍵洞察：**resource 讓 syzkaller 能生成有因果關係的 syscall sequence**，而不是隨機排列。

---

## 核心概念

### 基本型別系統

```
# 整數型別
int8    int16    int32    int64    intptr
```

`intptr` 會隨 target arch 自動調整（32/64-bit）。

```
# 固定值
const[0x1234, int32]         # 值固定為 0x1234，型別 int32
const[O_RDWR, int32]         # 值從 include 的 header 提取

# bitfield flags（需先定義 flagname）
flags[open_flags, int32]     # 從 open_flags 集合隨機組合 bits

open_flags = O_RDONLY, O_RDWR, O_CREAT, O_TRUNC

# 長度類型（最常用在 struct 內）
len[data, int32]             # 自動填 data 欄位的元素個數
bytesize[data, int32]        # 自動填 data 欄位的位元組數

# 陣列
array[int8, 16]              # 固定 16 個 int8
array[int8, 0:256]           # 0 到 256 個 int8（可變長）

# 指標
ptr[in, mydev_req]           # 指向 mydev_req，傳入 kernel
ptr[out, mydev_resp]         # kernel 寫出來的
ptr[inout, some_struct]      # 雙向

# 字串
string["mydev"]              # 固定字串 "mydev\0"
string[mydev_names]          # 從 mydev_names 集合選一個
stringnoz["noterm"]          # 不加 null terminator

# raw buffer
buffer[in]                   # 任意位元組輸入
buffer[out]                  # 任意位元組輸出

# struct 與 union
struct mydev_req {
    cmd    int32
    size   len[data, int32]
    data   array[int8, 0:64]
} [packed]                   # 對應 __attribute__((packed))

union mydev_payload {
    a   int32
    b   array[int8, 4]
}
```

`len[data, int32]` 的意思：這個欄位的值 = `data` 欄位的長度，syzkaller 在生成時自動計算並填入，不需要 fuzzer 亂猜。這是避免因為長度錯誤而在真正的解析邏輯之前就被 kernel 拒絕的關鍵。

---

### resource 機制（最重要的設計）

resource 告訴 syzkaller：某個型別的值有生命週期，不能憑空生成，必須從特定 syscall 拿到。

```
# 宣告：fd_mydev 是 fd 的子型別
resource fd_mydev[fd]

# producer：openat$mydev 回傳 fd_mydev
# syzkaller 執行後把回傳值存在 r0、r1... 等變數
openat$mydev(fd const[AT_FDCWD], file ptr[in, string["/dev/mydev"]],
             flags flags[open_flags], mode const[0]) fd_mydev

# consumer：需要 fd_mydev 的 syscall
ioctl$MYDEV_CMD_SET(fd fd_mydev, cmd const[MYDEV_CMD_SET],
                    arg ptr[in, mydev_req])
```

執行時 syzkaller 生成的 prog：
```
r0 = openat$mydev(0xffffffffffffff9c, &(0x7f0000000000)="/dev/mydev\x00",
                  0x2, 0x0)
ioctl$MYDEV_CMD_SET(r0, 0xc018de01, &(0x7f0000001000)={...})
```

`r0` 就是 `openat` 的回傳值，自動流入需要 `fd_mydev` 的地方。syzkaller 維護一張 resource pool，每次生成 prog 時從中取用或新建。

resource 可以形成階層：
```
resource sock[fd]
resource sock_tcp[sock]
resource sock_tcp_connected[sock_tcp]
```

越具體的子型別能被更少的 consumer 使用，讓 fuzzing 更精準。

---

## 完整範例：為 char device 寫 description

假設 `/dev/mydev` 支援兩個 ioctl：
- `MYDEV_CMD_SET`（`_IOW('D', 1, struct mydev_req)`）
- `MYDEV_CMD_GET`（`_IOR('D', 2, struct mydev_resp)`）

完整的 description 檔（`sys/linux/dev_mydev.txt`）：

```
# 指定要從哪個 header 提取常數
include <linux/ioctl.h>
include <linux/mydev.h>

# 手動定義（如果 syz-extract 無法自動從 header 拿到）
define MYDEV_CMD_SET  _IOW('D', 1, struct mydev_req)
define MYDEV_CMD_GET  _IOR('D', 2, struct mydev_resp)

# flags 集合定義
open_flags = O_RDWR, O_RDONLY, O_CLOEXEC

# resource 宣告
resource fd_mydev[fd]

# ─── syscall descriptions ───

# openat：producer，回傳 fd_mydev
openat$mydev(fd const[AT_FDCWD],
             file ptr[in, string["/dev/mydev"]],
             flags flags[open_flags],
             mode const[0]) fd_mydev

# ioctl SET：傳入 mydev_req
ioctl$MYDEV_CMD_SET(fd fd_mydev,
                    cmd const[MYDEV_CMD_SET],
                    arg ptr[in, mydev_req])

# ioctl GET：接收 mydev_resp（out）
ioctl$MYDEV_CMD_GET(fd fd_mydev,
                    cmd const[MYDEV_CMD_GET],
                    arg ptr[out, mydev_resp])

# close（consumer，釋放 resource）
close$mydev(fd fd_mydev)

# ─── struct 定義 ───

mydev_req {
    cmd     int32
    size    bytesize[data, int32]
    data    array[int8, 0:64]
} [packed]

mydev_resp {
    result  int32
    len     int32
    data    array[int8, 64]
} [packed]
```

幾個細節：

- `openat$mydev` 中的 `$mydev` 是**變體名**（variant name），讓 syzkaller 區分不同裝置的 `openat`，同一個 prog 可以同時 fuzz 多個 device
- `bytesize[data, int32]` 自動算 data 的位元組數（這裡等同 `len[data, int32]` 因為 data 是 int8）
- `[packed]` 對應 C 的 `__attribute__((packed))`，沒有 padding

### 驗證 description 正確性：syz-prog2c

寫完 description 之後，最快速的驗證方法是用 `syz-prog2c` 把 syzkaller 生成的 prog 轉成人可讀的 C 程式，肉眼核對結構是否合理：

```bash
# 先讓 syzkaller 跑一小段，蒐集一些 prog
# 然後把某個 prog 轉成 C
./bin/syz-prog2c -prog /path/to/corpus/prog001 -enable=all > /tmp/test.c
gcc -o /tmp/test /tmp/test.c && /tmp/test
```

轉出來的 C 程式直接包含 syscall 呼叫和參數值，可以確認：
- 傳入 ioctl 的 cmd number 是否是預期的常數值
- struct 裡的各欄位是否對應到正確的 offset
- fd 是否真的從正確的 `openat` 拿到

如果轉出來的 C 程式跑起來立刻 EINVAL 或 EBADF，通常是 description 有誤，不是 kernel 本身的 bug。

---

## 底層機制：syz-extract 和 syz-sysgen 流程

```
原始資料                            中間產物                   最終產物
┌──────────────┐                  ┌─────────────┐
│ Linux kernel │                  │             │
│   headers    │──syz-extract──▶  │ .const 檔   │──┐
│  (包含 ioctl │                  │(每個 arch)  │  │           ┌──────────────┐
│   number等)  │                  │             │  │           │  Go source   │
└──────────────┘                  └─────────────┘  ├─syz-sysgen▶  (syzkaller │
                                                    │           │   內部 pkg) │
┌──────────────┐                                   │           └──────────────┘
│ description  │                                   │
│  .txt 檔     │───────────────────────────────────┘
│(你手寫的)    │
└──────────────┘

syz-extract 做什麼：
  ┌─────────────────────────────────────────────────────────┐
  │ 1. 讀 description 中的 include 和 define 指令           │
  │ 2. 為每個 target arch 編譯一個小型 C 程式               │
  │    (#include <linux/mydev.h> + printf 各常數)           │
  │ 3. 執行後收集真實數值                                   │
  │ 4. 寫入 sys/linux/dev_mydev_amd64.const 等檔            │
  └─────────────────────────────────────────────────────────┘
  輸出範例：
    MYDEV_CMD_SET = 0xc018de01   # 在 amd64 上
    MYDEV_CMD_GET = 0x8040de02
    AT_FDCWD = 0xffffffffffffff9c

syz-sysgen 做什麼：
  ┌─────────────────────────────────────────────────────────┐
  │ 1. 解析所有 .txt description 檔                         │
  │ 2. 把 const 名稱替換成 .const 中的數值                  │
  │ 3. 生成 Go struct/interface，讓 syz-fuzzer 知道：       │
  │    - 每個 syscall 的參數型別樹                          │
  │    - resource 的 producer/consumer 關係圖               │
  │    - 如何序列化/反序列化 prog                           │
  └─────────────────────────────────────────────────────────┘
```

實際執行步驟（在 syzkaller repo 根目錄）：

```bash
# 1. 提取常數（需要對應 arch 的 kernel headers）
./bin/syz-extract -os linux -arch amd64 -sourcedir /path/to/linux \
    sys/linux/dev_mydev.txt

# 2. 重新生成 Go bindings（每次修改 .txt 都要跑）
make generate

# 這等同於：
./bin/syz-sysgen
```

生成的 `.const` 檔長這樣（`sys/linux/dev_mydev_amd64.const`）：

```
# Code generated by syz-sysgen. DO NOT EDIT.
arches = amd64
AT_FDCWD = 0xffffffffffffff9c
MYDEV_CMD_GET = 0x8040de02
MYDEV_CMD_SET = 0xc018de01
O_CLOEXEC = 0x80000
O_CREAT = 0x40
O_RDONLY = 0x0
O_RDWR = 0x2
O_TRUNC = 0x200
```

這個檔案要 commit 進 repo（syzkaller repo 裡所有 `.const` 都已 commit）。這樣 CI 和其他開發者不需要 kernel headers 也能 build syzkaller。如果你在沒有 kernel source 的環境要修改描述，先改 `.txt`，讓其他有 kernel source 的機器跑 syz-extract 更新 `.const`，再一起 commit。

---

## 進階用法

### include 和 define 指令

```
# 告訴 syz-extract 要掃哪些 header
include <linux/ioctl.h>
include <linux/mydev.h>
include <uapi/linux/mydev.h>

# 手動定義常數（syz-extract 抓不到，或需要覆蓋計算結果時使用）
define MY_MAGIC  0x44455600
define MYDEV_CMD_SET  _IOW(MY_MAGIC_TYPE, 0x01, struct mydev_req)
```

`define` 的右側可以是 C 表達式，syz-extract 會把它放進一個小型 C 程式裡計算，所以 `_IOW`/`_IOR` 宏展開是正確的，不需要手算 bit layout。

### type alias（避免重複）

```
# 為常用的型別組合取別名
type myfd fd[myfd, 0]

# 或是簡化重複的 flags 組合
type rwprot flags[mmap_prot, int32]
```

### template：參數化描述

```
# 定義 template（T 是型別參數）
type iov_opt[T] optional[ptr[in, T]]

# 在 struct 裡使用
some_struct {
    hdr   int32
    data  iov_opt[mydev_req]
}
```

template 在描述有多種 payload 格式的協議時有用，避免為每種組合寫一份 struct。

### `vma` 型別（mmap 地址空間）

```
mmap(addr vma, len len[addr], prot flags[mmap_prot],
     flags flags[mmap_flags], fd fd, offset fileoff) vma
```

`vma` 是 syzkaller 的特殊型別，代表一段合法的 virtual memory address range，讓 fuzzer 能正確處理 mmap/mprotect 這類操作，不會生成明顯無效的地址。

### `proc` 型別（per-process 唯一整數）

`proc[start, step, type]` 讓每個 syz-executor process 分到不同的整數範圍，避免多 process 並行時互相干擾（如 port number 衝突、pid 重複等）。常見於 `bind` 的 port 欄位。

### 描述 read/write 的格式

有些 device 的 `read`/`write` 本身也有結構：

```
write$mydev(fd fd_mydev, buf ptr[in, mydev_write_req],
            count bytesize[buf]) len[buf, intptr]

mydev_write_req {
    magic  const[0xDEAD, int16]
    cmd    int8
    data   array[int8, 0:128]
}
```

### `union` 的使用時機

當同一個欄位可以是不同格式時，`union` 比 `buffer[in]` 精確：

```
# cmd 決定後面 payload 的格式
mydev_cmd_req {
    cmd      int32
    payload  mydev_payload
}

union mydev_payload {
    set_req  mydev_set_args      # cmd == MYDEV_CMD_SET
    get_req  mydev_get_args      # cmd == MYDEV_CMD_GET
    raw      array[int8, 64]     # fallback
}

mydev_set_args {
    key    int32
    value  int64
}

mydev_get_args {
    key    int32
    flags  int32
}
```

syzlang 的 union 讓 syzkaller 隨機選一個 variant 填入，比 `array[int8, 64]` 更能生成有意義的輸入。不過 syzlang 沒有 tag-based dispatch（根據 cmd 值決定用哪個 union variant），這個語意需要 description 作者自己注意——如果 cmd 和 payload format 不匹配，kernel 可能直接拒絕，但 fuzzer 不知道。

### `fileoff` 和 `filename` 型別

```
# fileoff：用於 lseek/mmap 的 offset 參數
# syzkaller 會生成各種合理的 file offset 值
lseek(fd fd, offset fileoff, whence flags[seek_whence]) fileoff

# filename：路徑字串的特化型別
# syzkaller 會從已知的路徑集合取樣
open(file ptr[in, filename], flags flags[open_flags], mode flags[open_mode]) fd
```

`filename` 型別讓 syzkaller 知道這個字串是路徑，會優先嘗試 `/dev/...`, `/proc/...`, `/sys/...` 這類 kernel-relevant 路徑，而不是純亂填。

---

## 對比取捨

| 面向 | syzlang | 手刻 C fuzzer |
|------|---------|--------------|
| 學習成本 | 中（需理解型別系統） | 低（直接寫） |
| resource 追蹤 | 自動 | 手動管理 fd 生命週期 |
| 跨 arch 移植 | syz-extract 自動算常數 | 每個 arch 手改 |
| 描述精確度 | 取決於你寫的品質 | 可任意精確 |
| 維護成本 | API 改了只改 .txt | 改全部 C code |
| 生成 prog 多樣性 | syzkaller mutation engine 負責 | 自己設計 |
| 複雜協議（如 BT HCI） | 需要大量 description 工作 | 可直接 hardcode 封包 |
| 初始覆蓋率 | 高（description 對了就能進核心邏輯） | 低（需要花時間對齊格式） |

**結論**：寫 kernel driver 或新 subsystem 的 fuzzer，syzlang 是正確選擇；要針對已知格式的協議做深度 fuzzing，手刻 generator 搭配 syzlang 的 `buffer[in]` 也是常見混合策略。

---

## 踩雷

**錯誤直覺**：`ioctl` 的 cmd 直接填 `0xc018de01` 這樣的數字就好，不用費心 include 和 syz-extract。

**正確**：不同 kernel 版本、不同 arch，`_IOW`/`_IOR` 展開的數值可能不同。比如 32-bit 和 64-bit 的 `_IOC_SIZE` bit field 計算出來的 cmd number 就不一樣。hardcode 數字會讓 description 跑在錯誤 arch 時靜默失效（ioctl 回 ENOTTY 但 fuzzer 不會報錯）。一律用常數名 + syz-extract，讓工具算正確值。

---

**錯誤直覺**：`len[data, int32]` 的 `int32` 是 data 陣列元素的型別。

**正確**：`int32` 是**len 欄位本身的型別**（用幾個 bit 儲存長度數字），與 data 的元素型別無關。`len[data, int32]` 表示「用 int32 儲存 data 的元素個數」。如果 data 是 `array[int32, ...]`，那 `len[data, int32]` 回傳的是 int32 的個數，`bytesize[data, int32]` 才是位元組數。搞混的結果是 kernel 收到錯誤長度，在真正的邏輯之前就被 boundary check 拒絕，coverage 停滯在邊界。

---

**錯誤直覺**：resource 宣告 `resource fd_mydev[fd]` 之後，通用 `close(fd)` 就能釋放它，不需要特別寫 `close$mydev`；而且 ioctl 參數用通用 `fd` 也可以，反正 fd_mydev 是 fd 的子型別。

**正確**：通用 `close` 確實能回收 fd_mydev，這沒問題。問題在另一邊：如果 ioctl description 裡誤用了通用 `fd` 型別而非 `fd_mydev`，syzkaller 就不知道這個 ioctl 和 `openat$mydev` 之間有 producer/consumer 關係，可能生成 `ioctl$MYDEV_CMD_SET(5, ...)` 這種用隨機數字當 fd 的 prog，大多數執行都拿到 EBADF，浪費時間。**所有需要 fd_mydev 的 syscall，參數型別一定要寫 `fd_mydev`**，不能混用通用 `fd`。

---

**錯誤直覺**：修改 `.txt` 描述檔之後直接跑 `syz-fuzzer` 就能生效。

**正確**：必須跑 `make generate`（或至少 `syz-sysgen`）重新生成 Go bindings，再重新編譯整個 syzkaller。description 是 **compile-time** 資訊，syzkaller 在 build 時把它編譯進 binary，不是 runtime 讀取的配置。跳過這步，fuzzer 跑的還是舊描述，你改的東西完全沒效果，而且不會有任何警告。

---

**錯誤直覺**：`struct` 裡的欄位順序無所謂，反正 syzkaller 會按 description 填入。

**正確**：syzlang struct 的欄位順序**必須和 C struct 完全一致**，包含 padding。如果 kernel 的 C struct 有隱含 padding（比如 `int8` 後面跟 `int32`，中間有 3 bytes padding），description 裡要明確寫出：

```
mydev_padded {
    cmd     int8
    pad     array[const[0, int8], 3]   # 明確填 padding
    value   int32
}
```

或者用 `[packed]` 去掉 padding（前提是 kernel struct 也是 packed 的）。struct layout 不對，傳進 kernel 的 data 位移就錯，所有操作都打在錯誤的欄位上，描述的再精確也沒用。

---

**錯誤直覺**：`flags[open_flags, int32]` 會讓 syzkaller 生成所有可能的 flag 組合，覆蓋很廣。

**正確**：syzkaller 的 mutation engine 會從 `open_flags` 集合隨機選取並 OR 組合，但**不保證窮舉所有組合**。更重要的是：flags 集合要只包含合法的 flag 常數，不要放垃圾值。如果你把 `O_RDWR | 0x12345678` 這種非法值放進 flags 定義，大多數呼叫都會在 flag 解析這一步就被 kernel 拒絕，無法進入更深的邏輯。flags 定義應該和 kernel source 裡的 `#define` 保持同步。

---

## 進階延伸

**pseudo-syscall**：syzlang 支援描述「不是真實 syscall 但 fuzzer 需要執行的操作」，用 `syz_` 前綴：

```
syz_open_dev$mydev(dev ptr[in, string["/dev/mydev"]],
                   id proc[0, 1], flags flags[open_flags]) fd_mydev
```

`syz_open_dev` 是 syz-executor 內建的 helper，能展開 `/dev/mydev%d` 這類格式，適合有多個 instance 的 device（如 `/dev/video0`, `/dev/video1`）。其他常用的 pseudo-syscall：`syz_emit_ethernet`（直接發 L2 封包）、`syz_genetlink_get_family_id`（取得 netlink family id）。

**description 品質評估**：跑 syzkaller 一段時間後，用 syz-manager 的 coverage 報告看哪些函數沒被碰到，反推 description 缺少哪些 syscall 變體或 struct 欄位。沒被覆蓋到的路徑通常意味著 description 有錯誤或遺漏。

**從 kernel source 半自動生成 description**：`syz-declextract`（實驗性工具）能從 kernel 的 UAPI headers 掃出 ioctl 定義，生成初步的 description 框架，再手動補 resource 關係和語意約束。省掉最枯燥的 struct layout 工作，特別適合 struct 欄位多的大型 driver。

**多 description 檔協作**：syzkaller repo 的 `sys/linux/` 下有 800+ 個 .txt 檔，按 subsystem 切分。自己的 driver description 直接複用已有的 resource 定義（如通用 `fd`、`sock`、`pid` 等），只需要宣告你的子型別：`resource fd_mydev[fd]`，其他繼承自 `fd` 的行為自動有效。

**與 kernel_pwn / kernel_internals 的連接點**：本課的 kernel_pwn 章節有分析 ioctl 攻擊面的完整走法，kernel_internals 也有 VFS layer 的 `file_operations` 結構解析。寫 syzlang description 的前置工作其實是「讀 driver 的 `file_operations`」——`open` 對應 `.open`，`ioctl` 對應 `.unlocked_ioctl`，`mmap` 對應 `.mmap`。掌握了 kernel_internals 的 VFS 知識，就能更快判斷一個 driver 的攻擊面邊界在哪。

**description 的迭代工作流**：
```
1. 讀 kernel driver source → 找 file_operations 和 ioctl handler
2. 寫初版 description（只有基本 struct layout，flags 先用 int32）
3. syz-extract + make generate + 跑 5 分鐘
4. 看 coverage report，找沒被碰到的函數
5. 補充 flags 集合、細化 union variant、加缺少的 ioctl
6. 重複 3-5
```

不要想一次寫完美。先讓 fuzzer 跑起來，用 coverage 回饋指導 description 迭代，通常第三輪的 description 品質遠超過第一輪。

---

## 動手練習

1. 複製 `sys/linux/socket.txt` 中的 `resource sock[fd]` 定義，寫一個假想的 `NETPROTO_MYPROTO` socket description，包含 `socket$myproto`（producer）、`bind$myproto`、`connect$myproto`（consumer），bind 用一個自訂 `sockaddr_myproto` struct，struct 裡要有 `family const[AF_INET, int16]` 和 `port proc[20000, 1, int16be]`。

2. 找 `sys/linux/tty.txt`，讀懂 `ioctl$TIOCGWINSZ` 和 `ioctl$TIOCSWINSZ` 的描述方式，然後仿照寫 `ioctl$FIONBIO`（設定 non-blocking）。比對你的寫法和 `sys/linux/socket.txt` 裡類似 ioctl 的差異。

3. 在 syzkaller repo 執行：
   ```bash
   ./bin/syz-extract -os linux -arch amd64 \
       -sourcedir /path/to/linux-kernel-source \
       sys/linux/socket.txt
   ```
   看輸出的 `sys/linux/socket_amd64.const` 裡 `AF_INET` 的值（應為 2）、`SOCK_STREAM` 的值（應為 1），確認 syz-extract 從真實 kernel header 拿到的數字正確。

4. 寫一個最小可用的 description，描述 `memfd_create` syscall：包含 `resource fd_memfd[fd]`、flags 集合定義（`MFD_CLOEXEC`, `MFD_ALLOW_SEALING`），然後跑 `syz-sysgen` 確認沒有語法錯誤。加分題：再加一個 `fcntl$ADD_SEALS`，用 `F_ADD_SEALS` cmd 和 seals flags（`F_SEAL_WRITE`, `F_SEAL_SHRINK`, `F_SEAL_GROW`），構成完整的 memfd producer/consumer 對。

5. 閱讀 Linux kernel `drivers/char/mem.c` 裡的 `/dev/mem` 或 `/dev/null` 的 `file_operations`（這些 driver 很短），找出它支援的 `read`/`write`/`ioctl` 操作，然後用 syzlang 寫出對應的 description。重點練習點：`/dev/mem` 的 `read`/`write` offset 有特殊限制（不能超過 physical memory range），試著用 `fileoff` 型別搭配合理的 flags 限制 offset 範圍。

6. 到 syzkaller repo 的 `sys/linux/` 目錄下找一個你熟悉的 driver description（推薦 `dev_evdev.txt` 或 `input.txt`），統計其中：有幾個不同的 resource type、有幾個 producer syscall、有幾個 consumer syscall、最複雜的 struct 有幾個欄位。這個練習讓你感受真實 description 的規模，以及 syzkaller 社群花了多少工夫在 input device 這個 subsystem 上。

---

## 本章重點

- syzlang 用型別系統（int/const/flags/len/ptr/array/struct/union）約束每個參數的合法值空間，讓 fuzzer 不在無效輸入上浪費時間
- **resource 機制**是 syzkaller 最核心的設計：producer syscall 產生 resource，consumer syscall 消費它，syzkaller 自動排序確保先 open 才能 ioctl
- `len`/`bytesize` 型別讓長度欄位自動對齊，避免 kernel 在真正邏輯之前就拒絕請求
- **syz-extract** 從 kernel headers 提取真實常數值（ioctl number 等），生成 `.const` 檔；**syz-sysgen** 把 `.txt` + `.const` 編譯成 syzkaller 的 Go bindings
- 修改 description 後必須重跑 `make generate` 才生效，description 是 compile-time 資訊
- `include` 指令告訴 syz-extract 要看哪個 header；`define` 指令手動指定 syz-extract 抓不到的常數（右側可以是完整 C 表達式）

---

## 自我檢核

- [ ] 能說出 `len[data, int32]` 和 `bytesize[data, int32]` 的差別
- [ ] 能從頭寫出一個 char device 的完整 description（resource + openat + 2 個 ioctl + struct）
- [ ] 知道 `ptr[in, T]`、`ptr[out, T]`、`ptr[inout, T]` 各對應什麼方向
- [ ] 能解釋 resource producer/consumer 機制為什麼能確保 syscall 順序
- [ ] 知道 syz-extract 和 syz-sysgen 各做什麼、執行順序是什麼
- [ ] 能說出為什麼不應該在 description 裡 hardcode ioctl number
- [ ] 知道修改 .txt 之後需要做什麼才能讓 fuzzer 使用新描述
- [ ] 能說出 `$variant` 名稱（如 `openat$mydev`）的用途
- [ ] 知道 `.const` 檔的角色，以及為什麼它需要 commit 進 repo
- [ ] 能說出 `syz-prog2c` 的用途及何時用它驗證 description
- [ ] 知道 syzlang struct 欄位順序必須和 C struct 一致（含 padding）
- [ ] 能解釋 `union` 在 syzlang 裡的用途，以及它和 `buffer[in]` 的差別

---

## 延伸閱讀

1. **syzkaller 官方文件 — syscall_descriptions_syntax.md**
   https://github.com/google/syzkaller/blob/master/docs/syscall_descriptions_syntax.md
   讀「Types」和「Resources」兩節。這是 syzlang 的語言規格，所有型別的完整語義定義在這裡，本章沒涵蓋的邊緣型別（`vma`、`proc`、`compressed_image`、`offsetof`）查這份。每次 description 出現奇怪行為，回這份查語義。

2. **Dmitry Vyukov — Writing syzkaller descriptions**
   https://github.com/google/syzkaller/blob/master/docs/syscall_descriptions.md
   讀「How to describe a new syscall」和「Testing descriptions」兩節。作者說明如何從一個陌生 subsystem 開始、如何用 `syz-prog2c` 把 syzkaller prog 轉成可讀的 C 程式驗證正確性、如何用 coverage 評估 description 品質。關聯：第 26 章實際跑 syzkaller 時需要對照這份做 debug。

3. **Exploiting the Linux kernel via packet sockets（Google Project Zero, 2017）**
   https://googleprojectzero.blogspot.com/2017/05/exploiting-linux-kernel-via-packet.html
   讀第一節「How we found the bug」。作者說明 syzkaller 的 socket description 寫得夠精確，才能讓 fuzzer 走進 `packet_set_ring` 的深層路徑，最終找到 CVE-2017-7308。這個案例直接說明了「description 精確度 = 洞能不能被找到」的關係。關聯：配合本課 kernel_pwn 課程的 ring buffer 相關章節。

---

syzlang 寫得好不好，直接決定 syzkaller 能不能走進真正有趣的 code path。花時間把 description 對齊 kernel source、把 struct layout 量對、把 flags 集合設正確，比調 fuzzer 參數更有效益。

syzkaller 社群對高品質 description 的需求是持續的——`sys/linux/` 下有大量 TODO 和 FIXME，每年都有貢獻者在補新 driver 的描述。如果你在 fuzzing 一個沒有 description 的 driver，寫出描述並貢獻上游，是讓整個社群都受益的高 CP 值工作。

一個衡量 description 品質的粗略指標：跑 syzkaller 30 分鐘，看 syz-manager 頁面上「executed」和「signal」兩個數字的成長速度。signal 成長代表 coverage 在擴展，說明 description 帶著 fuzzer 走進了新的 code path。如果 executed 很高但 signal 平坦，多半是 description 有問題導致大量呼叫在邊界就被拒絕。

→ [下一章](./26-running-syzkaller.md)：把寫好的 description 裝進 syzkaller，在 QEMU 上跑起第一個 fuzzing session，看懂 syz-manager 的輸出和 crash log。
