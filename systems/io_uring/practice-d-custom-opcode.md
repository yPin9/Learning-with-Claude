# 練習 D — 給 kernel 加一個自訂 io_uring opcode

> **目標**：把 Part 3 讀懂的東西從「看得懂」變成「改得動」。你會在 io_uring 加一個全新的 opcode `IORING_OP_MYADD`——它收兩個整數、回傳相加結果——走過 `io_uring_op` enum、`opdef.c` 表、`->prep` / `->issue` handler、編 kernel、到 user 程式提交驗證的**完整**路徑。做完這個，你就真正摸過 io_uring 加一個操作需要動的每一塊。

> **環境**：kernel 6.18 原始碼（`v6.18`，commit `7d0a66e`）。練習參考 `io_uring/nop.c`、`io_uring/opdef.c`、`include/uapi/linux/io_uring.h`。

---

## ⚠️ 環境前提（務必先讀）

**改 kernel 的部分無法在 WSL 實測。** WSL2 跑的是微軟維護的 kernel,不好換、載模組受限（見 Ch 0 踩雷 5）。本練習的 **kernel 改動步驟（Step 1–4）未在 WSL 實測,是依 6.18 原始碼結構推導的預期做法**——每一處改動都對照真實的 6.18 原始碼結構,但「編出來能跑、res 正確回 42」這件事需要你在**可自編 kernel 的環境**驗證:

- **QEMU + 自編 kernel + initramfs**:最標準的做法,和 repo 的 [kernel_pwn Ch 0](../../security/kernel_pwn/00-environment-setup.md) 完全一致——那章教你怎麼 `make` 一顆 kernel、包 initramfs、用 QEMU 開機、gdb remote。本練習假設你已有那套環境,或照它架一套。
- **一台可重開機的 VM**（VirtualBox / KVM / 雲主機）**自編 kernel 安裝**:也可以,但重開機迭代慢。
- **不要**在你日常用的實體機上裝這顆實驗 kernel。

相對地,**Step 5 的 user 端測試程式可以、也已經在 WSL 實測**——因為 opcode 不存在時 kernel 回 `-EINVAL`,這段在 stock 6.18 就能驗證(下詳,有實測輸出)。所以本練習的驗證分兩半:**user 端在 WSL 實測、kernel 端在 QEMU 推導預期**,各自標清楚。

---

## 背景動機:為什麼要會加 opcode

讀完 Part 3,你知道一個請求怎麼從 SQE 被 dispatch 到 operation handler(Ch 23–24)、怎麼完成(Ch 25)。但「看懂 dispatch」和「自己接一個進去」是兩回事。親手加一個 opcode 會逼你回答一串具體問題:

- opcode 編號是哪裡定義的?加一個要動幾個檔?
- `->prep` 和 `->issue` 的分工到底是什麼?為什麼要拆兩段?
- SQE 的哪些欄位我可以拿來放參數?handler 怎麼讀?
- 結果怎麼變成 CQE 的 `res`?

這些問題 Part 3 都「講過」,但只有動手加一次,你才會真的內化。而且這是很多進階工作的基礎:研究新 io_uring 功能、分析一個新 opcode 引入的 CVE(Part 4)、甚至自己給 kernel 加 I/O 介面——都從「會加一個 opcode」開始。

我們選「兩數相加」這個最蠢的操作,正是因為它蠢——把所有注意力留給**接入 io_uring 框架的機制**,而不是操作本身的邏輯。

---

## 任務規格

加一個 opcode `IORING_OP_MYADD`:

| 項目 | 規格 |
|---|---|
| 參數一 | 第一個加數,從 `sqe->off` 讀(u64) |
| 參數二 | 第二個加數,從 `sqe->addr` 讀(u64) |
| 行為 | 計算兩數之和 |
| 結果 | 和放進 CQE 的 `res`(s32) |
| 完成方式 | inline 立即完成(像 NOP,不阻塞、不走 io-wq) |
| opcode 編號 | 緊接 `IORING_OP_PIPE`(62)之後,即 **63** |

驗收:user 程式提交一個 `IORING_OP_MYADD`,`off=40`、`addr=2`,收到 CQE `res == 42`。

**為什麼放 `off` 和 `addr`?** SQE 是個固定 64-byte 結構(Ch 7),有很多欄位(`off`、`addr`、`len`、`fd`……)。對不需要 fd / buffer 的操作,這些欄位可以拿來當你自訂操作的參數槽——這正是 NOP 的 `IORING_NOP_INJECT_RESULT` 從 `sqe->len` 讀注入值的做法。我們照搬這個慣例。

---

## 要改哪些檔(全景)

加一個 opcode 要動五處,先有全景再逐一做:

```
1. include/uapi/linux/io_uring.h   ← 加 IORING_OP_MYADD 到 enum(ABI)
2. io_uring/myadd.c  (新檔)         ← 寫 io_myadd_prep / io_myadd handler
3. io_uring/myadd.h  (新檔)         ← handler 的宣告
4. io_uring/opdef.c                ← 兩張表各加一個 [IORING_OP_MYADD] entry
5. io_uring/Makefile               ← 加 myadd.o 進編譯
                                      (+ 在 user 端程式 #define opcode 編號)
```

對照 Ch 19 的原始碼地圖:opcode 的定義(uapi)、handler(各 op 自己的 .c)、dispatch 表(opdef.c)三者分離,正是 io_uring 的模組化設計。我們順著這個結構接進去。

---

## 實作步驟

### Step 1 — 在 `io_uring_op` enum 加編號 ⚠️（未實測,理論預期）

打開 `include/uapi/linux/io_uring.h`,找到 enum 尾端(6.18 實際長這樣):

```c
	IORING_OP_READV_FIXED,
	IORING_OP_WRITEV_FIXED,
	IORING_OP_PIPE,

	/* this goes last, obviously */
	IORING_OP_LAST,
```

在 `IORING_OP_PIPE` 之後、`IORING_OP_LAST` 之前插入:

```c
	IORING_OP_READV_FIXED,
	IORING_OP_WRITEV_FIXED,
	IORING_OP_PIPE,
	IORING_OP_MYADD,          /* 新增:兩數相加,編號 63 */

	/* this goes last, obviously */
	IORING_OP_LAST,
```

**為什麼一定要加在 `IORING_OP_LAST` 之前?** 因為這個 enum 是 **ABI**——既有 opcode 的編號不能改(user space 和既有程式依賴它們)。只能往尾端追加。`IORING_OP_LAST` 永遠是「最後一個 + 1」,是 opdef 表大小和合法性檢查的邊界。你的新 op 擠在 PIPE(62) 和 LAST 之間,自然拿到編號 63,`IORING_OP_LAST` 自動變成 64。

### Step 2 — 寫 handler `io_uring/myadd.c` ⚠️（未實測,理論預期）

照 `nop.c` 的結構(它是最簡單的 handler 範本)。新建 `io_uring/myadd.c`:

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/kernel.h>
#include <linux/errno.h>
#include <linux/fs.h>
#include <linux/io_uring.h>

#include <uapi/linux/io_uring.h>

#include "io_uring.h"
#include "myadd.h"

struct io_myadd {
	/* NOTE: kiocb 的第一個成員是 file,所以這裡別再放 file */
	struct file	*file;
	u64		a;
	u64		b;
};

int io_myadd_prep(struct io_kiocb *req, const struct io_uring_sqe *sqe)
{
	struct io_myadd *m = io_kiocb_to_cmd(req, struct io_myadd);

	/* 不接受我們沒用到的欄位被亂塞,保持嚴格 */
	if (READ_ONCE(sqe->fd) || READ_ONCE(sqe->len) ||
	    READ_ONCE(sqe->buf_index) || READ_ONCE(sqe->rw_flags))
		return -EINVAL;

	m->a = READ_ONCE(sqe->off);
	m->b = READ_ONCE(sqe->addr);
	return 0;
}

int io_myadd(struct io_kiocb *req, unsigned int issue_flags)
{
	struct io_myadd *m = io_kiocb_to_cmd(req, struct io_myadd);
	s32 res = (s32)(m->a + m->b);

	io_req_set_res(req, res, 0);
	return IOU_COMPLETE;
}
```

逐點解釋(對照 Ch 23–25):

- **`io_kiocb_to_cmd(req, struct io_myadd)`**:io_uring 把每個 op 的私有資料塞在 `io_kiocb` 物件裡那段 per-command 空間(Ch 21)。這個巨集把它轉型成你的結構。第一個成員必須是 `struct file *file`(kiocb 佈局要求),即使你不用 file。
- **`->prep` 和 `->issue` 為什麼拆兩段**(Ch 23):`prep` 在**提交時**跑,負責「從 SQE 把參數**複製進** req 物件、驗證合法性」。為什麼要複製?因為 SQE 在共享記憶體,提交後 user 可能改它、或 SQE 槽被重用;`issue` 真正執行時不能再信任 SQE,只能用 `prep` 階段複製進來的值。這是 io_uring 一條安全鐵律:**prep 複製 + 驗證,issue 只用複製好的值**。
- **`READ_ONCE`**:SQE 在共享記憶體,用 `READ_ONCE` 防編譯器把讀取優化掉或拆分(對照 Ch 5 的 memory ordering)。
- **`io_req_set_res(req, res, 0)`**:把結果填進 `req->cqe.res`(Ch 25)。注意是 `s32`——CQE 的 `res` 是 32-bit signed,和同步 syscall 的回傳值語意一致。
- **`return IOU_COMPLETE`**:告訴 dispatch「我當場完成了」,`io_issue_sqe` 接手走完成路徑(Ch 25 的 `io_req_complete_defer` / `io_req_complete_post`)。因為我們不阻塞,直接 inline 完成,不碰 io-wq。

### Step 3 — handler 宣告 `io_uring/myadd.h` ⚠️（未實測,理論預期）

照 `nop.h`:

```c
// SPDX-License-Identifier: GPL-2.0

int io_myadd_prep(struct io_kiocb *req, const struct io_uring_sqe *sqe);
int io_myadd(struct io_kiocb *req, unsigned int issue_flags);
```

### Step 4 — 接進 dispatch 表 `io_uring/opdef.c` ⚠️（未實測,理論預期）

`opdef.c` 有**兩張**表(Ch 23):`io_issue_defs[]`(熱路徑:prep/issue 函式指標 + flags)和 `io_cold_defs[]`(冷路徑:name、cleanup、fail)。兩張都要加。

先在檔案頂端的 include 區加你的 header(跟在 `#include "nop.h"` 附近):

```c
#include "nop.h"
#include "myadd.h"      /* 新增 */
```

在 `io_issue_defs[]` 裡,找到 `[IORING_OP_NOP]` 那個 entry 當範本:

```c
	[IORING_OP_NOP] = {
		.audit_skip		= 1,
		.iopoll			= 1,
		.prep			= io_nop_prep,
		.issue			= io_nop,
	},
```

加上你的(可以放在表的任意位置,慣例是按 opcode 編號順序,但 C 的 designated initializer 不要求順序):

```c
	[IORING_OP_MYADD] = {
		.audit_skip		= 1,
		.prep			= io_myadd_prep,
		.issue			= io_myadd,
	},
```

`.audit_skip = 1` 表示這個 op 不需審計(它不碰檔案/網路,沒有安全相關的 side effect 要記)。不設 `needs_file`(我們不用 file)、不設 `pollin/pollout`(不走 poll)。

在 `io_cold_defs[]` 裡,找到 `[IORING_OP_NOP]`:

```c
	[IORING_OP_NOP] = {
		.name			= "NOP",
	},
```

加:

```c
	[IORING_OP_MYADD] = {
		.name			= "MYADD",
	},
```

`name` 給 tracing / `fdinfo` 顯示用。我們沒有要清理的 async data,不需 `cleanup`/`fail`。

### Step 5 — 加進編譯 `io_uring/Makefile` ⚠️（未實測,理論預期）

`io_uring/Makefile` 有一行 `io_uring-y += ...` 列出所有 .o。把 `myadd.o` 加進去:

```make
io_uring-y += ... nop.o myadd.o ...
```

(找到含 `nop.o` 的那行,把 `myadd.o` 接在後面即可。)

### Step 6 — 編 kernel 並開機 ⚠️（未實測,理論預期）

這一步完全依賴你的自編 kernel 環境。以 **QEMU + 自編 kernel**(和 [kernel_pwn Ch 0](../../security/kernel_pwn/00-environment-setup.md) 一致)為例,大致指引:

```bash
# 在 kernel 原始碼樹(完整的,不是本課的 sparse checkout)
make O=build defconfig                 # 或用你現有的 .config
# 確認 CONFIG_IO_URING=y
scripts/config --file build/.config -e IO_URING
make O=build -j"$(nproc)"              # 編譯;只改了 io_uring/ 的話增量很快
# 產物:build/arch/x86/boot/bzImage

# 用 QEMU 開機(配合你的 initramfs,見 kernel_pwn Ch 0)
qemu-system-x86_64 \
    -kernel build/arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr" \
    -nographic -m 512 -no-reboot
```

重點提醒:

- **本課的 `~/io_uring_course/ref/linux` 是 sparse checkout(只有 io_uring/ 等幾個目錄),不能直接編**。你需要一棵**完整**的 v6.18 kernel tree 來編。可以 `git clone --branch v6.18 --depth 1 https://github.com/torvalds/linux.git` 另抓一棵完整的,把你的改動套上去。
- 只改 `io_uring/` 底下的檔,增量編譯(`make`)只會重編 io_uring 相關 object,很快。第一次全編才久。
- initramfs 怎麼包、QEMU 參數細節、gdb remote 怎麼接,**完全參照 kernel_pwn Ch 0**,這裡不重複。
- 把你的 user 測試程式(靜態編譯)塞進 initramfs,開機後在 QEMU 裡跑。

### Step 7 — user 端測試程式 ✅（已在 WSL 實測）

這段**可以、也已經在 WSL 實測**。因為沒有 liburing prep helper(opcode 是你自己加的),我們**手動填 SQE**。建 `test_myadd.c`:

```c
// test_myadd.c — 測試自訂 opcode IORING_OP_MYADD（假設編號 63）
// 在 stock 6.18 kernel:opcode 不存在 → 預期 res = -EINVAL
// 在已套用本練習 patch 的 kernel:預期 res = a + b
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <liburing.h>

#ifndef IORING_OP_MYADD
#define IORING_OP_MYADD 63   /* 緊接 IORING_OP_PIPE(62) 之後 */
#endif

int main(void) {
    struct io_uring ring;
    int ret = io_uring_queue_init(8, &ring, 0);
    if (ret < 0) { fprintf(stderr, "queue_init: %s\n", strerror(-ret)); return 1; }

    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    memset(sqe, 0, sizeof(*sqe));     /* 沒有 prep helper,手動清 + 填 */
    sqe->opcode    = IORING_OP_MYADD;
    sqe->off       = 40;              /* 第一個加數 */
    sqe->addr      = 2;               /* 第二個加數 */
    sqe->user_data = 0xABCD;

    io_uring_submit(&ring);

    struct io_uring_cqe *cqe;
    io_uring_wait_cqe(&ring, &cqe);
    printf("opcode=%d user_data=0x%llx res=%d",
           IORING_OP_MYADD, (unsigned long long)cqe->user_data, cqe->res);
    if (cqe->res < 0)
        printf(" (%s)\n", strerror(-cqe->res));
    else
        printf(" (40 + 2 = %d)\n", cqe->res);
    io_uring_cqe_seen(&ring, cqe);
    io_uring_queue_exit(&ring);
    return 0;
}
```

編譯(用 Ch 0 的 `iou-cc`)並在 **stock 6.18 WSL** 跑:

```bash
iou-cc test_myadd.c -o test_myadd
./test_myadd
```

**WSL 實測輸出(stock 6.18,opcode 63 尚不存在)**:

```
opcode=63 user_data=0xabcd res=-22 (Invalid argument)
```

`res=-22` 就是 `-EINVAL`。這證明兩件事:

1. 你的提交路徑是對的——SQE 填好、提交、收到 CQE,整條 user 側流程通。
2. kernel 的合法性檢查運作正常:opcode 63 ≥ 當前的 `IORING_OP_LAST`,`io_init_req` 的 opcode 範圍檢查直接把它擋成 `-EINVAL`(對照 Ch 23)。

**預期(已套用 patch 的 kernel,在 QEMU 裡跑)**:`res == 42`,輸出應是:

```
opcode=63 user_data=0xabcd res=42 (40 + 2 = 42)
```

這個「同一支 user 程式,stock kernel 回 -EINVAL、patched kernel 回 42」的對比,就是你驗證 patch 成功的判據——`-22 → 42` 的轉變代表你的 opcode 真的被接進 dispatch 了。

---

## 完整參考解答

**自己做完再看!** 尤其 kernel 那五處改動,先自己對著 `nop.c` / `opdef.c` 試著接,卡住再翻。

<details>
<summary>點開參考實作(kernel patch + user 程式)</summary>

### kernel 改動彙總(⚠️ 未實測,依 6.18 結構推導)

**`include/uapi/linux/io_uring.h`**(enum 尾端):

```diff
 	IORING_OP_READV_FIXED,
 	IORING_OP_WRITEV_FIXED,
 	IORING_OP_PIPE,
+	IORING_OP_MYADD,
 
 	/* this goes last, obviously */
 	IORING_OP_LAST,
```

**`io_uring/myadd.c`**(新檔,完整):

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/kernel.h>
#include <linux/errno.h>
#include <linux/fs.h>
#include <linux/io_uring.h>

#include <uapi/linux/io_uring.h>

#include "io_uring.h"
#include "myadd.h"

struct io_myadd {
	struct file	*file;
	u64		a;
	u64		b;
};

int io_myadd_prep(struct io_kiocb *req, const struct io_uring_sqe *sqe)
{
	struct io_myadd *m = io_kiocb_to_cmd(req, struct io_myadd);

	if (READ_ONCE(sqe->fd) || READ_ONCE(sqe->len) ||
	    READ_ONCE(sqe->buf_index) || READ_ONCE(sqe->rw_flags))
		return -EINVAL;

	m->a = READ_ONCE(sqe->off);
	m->b = READ_ONCE(sqe->addr);
	return 0;
}

int io_myadd(struct io_kiocb *req, unsigned int issue_flags)
{
	struct io_myadd *m = io_kiocb_to_cmd(req, struct io_myadd);
	s32 res = (s32)(m->a + m->b);

	io_req_set_res(req, res, 0);
	return IOU_COMPLETE;
}
```

**`io_uring/myadd.h`**(新檔):

```c
// SPDX-License-Identifier: GPL-2.0

int io_myadd_prep(struct io_kiocb *req, const struct io_uring_sqe *sqe);
int io_myadd(struct io_kiocb *req, unsigned int issue_flags);
```

**`io_uring/opdef.c`**:

```diff
 #include "nop.h"
+#include "myadd.h"
```

```diff
 	[IORING_OP_NOP] = {
 		.audit_skip		= 1,
 		.iopoll			= 1,
 		.prep			= io_nop_prep,
 		.issue			= io_nop,
 	},
+	[IORING_OP_MYADD] = {
+		.audit_skip		= 1,
+		.prep			= io_myadd_prep,
+		.issue			= io_myadd,
+	},
```

```diff
 	[IORING_OP_NOP] = {
 		.name			= "NOP",
 	},
+	[IORING_OP_MYADD] = {
+		.name			= "MYADD",
+	},
```

**`io_uring/Makefile`**(把 `myadd.o` 接在 `nop.o` 後):

```diff
-io_uring-y += ... nop.o ...
+io_uring-y += ... nop.o myadd.o ...
```

### user 程式

見上面 Step 7 的 `test_myadd.c`(已實測)。

### 驗證判據

| kernel | 預期 res | 意義 |
|---|---|---|
| stock 6.18(WSL) | `-22`(-EINVAL) | opcode 不存在,範圍檢查擋下 ✅ 已實測 |
| patched(QEMU) | `42` | opcode 接進 dispatch,handler 正確執行 ⚠️ 理論預期 |

</details>

---

## 卡住提示

<details>
<summary>提示 1:編譯報 "io_myadd undeclared" 或 link error</summary>

多半是漏了某一處。檢查清單:(a) `myadd.h` 的宣告有沒有被 `opdef.c` include?(b) `Makefile` 加了 `myadd.o` 沒?(c) handler 函式名和 opdef 表裡的 `.prep`/`.issue` 有沒有拼一致?五處改動缺一不可。
</details>

<details>
<summary>提示 2:編過了,但 user 程式還是回 -EINVAL</summary>

代表 QEMU 跑的不是你新編的 kernel,或 opcode 編號對不上。(a) 確認 QEMU 的 `-kernel` 指到你剛編的 `bzImage`,不是舊的。(b) 確認 user 程式 `#define IORING_OP_MYADD` 的值和你在 enum 裡的位置算出來的編號一致(PIPE 之後是 63)。(c) `dmesg` 看有沒有 io_uring 相關 warning。
</details>

<details>
<summary>提示 3:res 回來是奇怪的值不是 42</summary>

(a) 檢查 `prep` 是不是從對的 SQE 欄位讀(`off` 和 `addr`,不是 `len`/`fd`)。(b) user 程式有沒有 `memset(sqe, 0, ...)` 清乾淨?沒清的話其他欄位有殘值,會被你 prep 裡的嚴格檢查擋成 -EINVAL,或 off/addr 讀到髒值。(c) `s32` 轉型:如果 a+b 溢位 32-bit,res 會是截斷值——這是預期行為(CQE res 就是 32-bit)。
</details>

<details>
<summary>提示 4:kernel 開機就 panic / io_uring 完全不能用</summary>

最可能是 opdef 表的 entry 和 enum 編號對不上,導致表錯位。確認你的 `[IORING_OP_MYADD]` 用的是 designated initializer(中括號語法),而不是依賴位置順序插進去。designated initializer 會按 enum 值放對位置,位置插錯則整張表歪掉。
</details>

---

## 延伸挑戰

做完基本版,試試這些(難度遞增,都 ⚠️ 未實測,自己在 QEMU 驗):

1. **加 flags 欄位**:仿 NOP 的 `nop_flags`,讓 `IORING_OP_MYADD` 支援一個 flag,例如 `MYADD_SATURATE`——溢位時回 `INT_MAX` 而非截斷。你需要在 SQE 挑一個欄位放 flags(NOP 用的是 union 裡的 `nop_flags`),prep 時讀出來、驗證、存進 req 結構。

2. **做成會「阻塞」的版本**:加一個 `MYADD_DELAY` flag,讓 handler 在 issue 時睡一下(例如 `msleep`)。這會逼它走 io-wq(Ch 26)——因為 issue 時帶 `IO_URING_F_NONBLOCK`,你要回 `-EAGAIN` 讓它 punt 到 worker。觀察 `ps` 看 `iou-wrk` 冒出來。這是把 Ch 24 的 NONBLOCK 重試、Ch 26 的 io-wq 連起來的好練習。

3. **回傳 CQE32 的 extra 欄位**:仿 NOP 的 `IORING_NOP_CQE32`,讓操作除了 `res` 還回傳兩個 u64(放進 `big_cqe` 的 extra1/extra2,Ch 25)。例如同時回傳 `a+b` 和 `a*b`。ring 要用 `IORING_SETUP_CQE32` 建立。

4. **multishot 版**:做一個 `IORING_OP_MYADD_MULTI`,每次就緒吐一筆 CQE、累加——理解 Ch 28 的 `io_req_post_cqe` + `IORING_CQE_F_MORE`。這個最難,要處理 multishot 的狀態機。

5. **讀真的 user 記憶體**:讓參數不是直接放 SQE,而是 `sqe->addr` 指向一個 user buffer 裡的兩個整數,handler 用 `copy_from_user` 讀。這會碰到「issue 可能在 io-wq context,user mm 還在不在」的問題——是理解 Ch 26 身份繼承的入口。

---

## 自我檢核

- [ ] 我能說出加一個 opcode 要動哪五處,各自的角色
- [ ] 我能解釋 `->prep` 和 `->issue` 為什麼拆兩段,以及「prep 複製+驗證、issue 只用複製值」這條安全鐵律
- [ ] 我知道為什麼新 opcode 只能加在 `IORING_OP_LAST` 之前(ABI)
- [ ] 我能解釋 `io_kiocb_to_cmd` 在做什麼,以及為什麼結構第一個成員要是 `file`
- [ ] 我理解為什麼 user 程式要手動填 SQE(沒有 liburing prep helper)
- [ ] 我能說出「stock kernel 回 -EINVAL、patched kernel 回 42」這個對比驗證了什麼
- [ ] (做了延伸)我試過讓 opcode 走 io-wq,並看到 `iou-wrk` worker

---

## 延伸閱讀

### 必讀(範本)

- **Linux 6.18 原始碼 `io_uring/nop.c`** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/nop.c?h=v6.18>
  - **讀哪裡**:整個檔案。它是最簡單的 handler,本練習的 `myadd.c` 就是它的簡化版
  - **和本練習關聯**:`io_nop_prep` / `io_nop` 的結構、`io_kiocb_to_cmd`、`io_req_set_res`、`IOU_COMPLETE` 的用法直接照搬

- **Linux 6.18 原始碼 `io_uring/opdef.c`**
  - **讀哪裡**:`io_issue_defs[]` 和 `io_cold_defs[]` 兩張表的結構,以及 `struct io_issue_def` 的欄位定義(`opdef.h`)
  - **和本練習關聯**:Step 4 接 dispatch 表的依據

### 環境(自編 kernel)

- **[kernel_pwn Ch 0 — 環境搭建:QEMU + initramfs + kernel build + gdb remote](../../security/kernel_pwn/00-environment-setup.md)**
  - **讀哪裡**:整章。怎麼編 kernel、包 initramfs、QEMU 開機、gdb remote 接進去,本練習 Step 6 完全依賴它
  - **和本練習關聯**:本練習 kernel 端「未實測」的部分,就是要在這套環境驗證

- **[../kernel_internals/README.md](../kernel_internals/README.md)**
  - **讀哪裡**:kernel 編譯與執行環境相關章節
  - **和本練習關聯**:理解 `make` kernel、config、模組的通用背景

### 本課對照

- **[Ch 23 提交路徑:io_submit_sqes 與 opcode dispatch](./23-submission-path.md)** 與 **[Ch 24 io_issue_sqe](./24-issue-sqe.md)**
  - **讀哪裡**:opcode 怎麼從 SQE 被 dispatch 到 `->prep`/`->issue`
  - **和本練習關聯**:你加的 entry 就是被這條路徑呼叫的;`-EINVAL` 的範圍檢查也在這裡

- **[Ch 25 完成路徑](./25-completion-path.md)**
  - **讀哪裡**:`IOU_COMPLETE` 之後 `io_req_set_res` 填的 `res` 怎麼變成 CQE
  - **和本練習關聯**:你的 handler 回 `IOU_COMPLETE` 之後發生的事

恭喜——你不只讀懂了 io_uring 的 kernel 內部,還親手往裡面接了一個操作。你現在對「一個 opcode 從定義到執行到完成」的每一塊都有第一手的手感。這份手感是 Part 4 的硬底氣:當你分析一個「某新 opcode 引入的 UAF」時,你會本能地去看它的 `prep` 有沒有好好複製+驗證、它的 `issue` 在什麼 context 跑、它碰的資源生命週期對不對——因為你自己接過一個。

→ [Ch 31 為什麼 io_uring 是漏洞溫床:攻擊面分析](./31-attack-surface.md)
