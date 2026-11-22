# 練習 E — 用 Sanitizer 偵測 io_uring 記憶體錯誤

> **目標**：把 Part 4 學到的 UAF 概念，變成一項**可操作的防禦技能**——用 AddressSanitizer 抓出 io_uring 應用層最常見的 use-after-free，並理解 kernel 側要用 KASAN 怎麼觀察記憶體錯誤。這是偵測練習，不是攻擊練習：我們要學會**讓記憶體錯誤現形並讀懂報告**，這是審計與除錯的基本功。

> **環境**：Part 1（應用層，ASan）在 WSL2 kernel 6.18 + liburing 2.16 + gcc 11.4 實測可跑。Part 2（kernel 層，KASAN）需在你自有的 QEMU + 自編 KASAN kernel 環境，本練習標明為受控環境操作。全程在自有或獲授權的環境進行，目的是建立偵測與防禦能力。

## 背景與動機

Ch 34–37 拆的是 kernel 本身的 CVE——那是 kernel 開發者要修的。但**使用 io_uring 寫應用程式的你**，每天會遇到的其實是另一類 UAF：**應用層自己的生命週期管理錯誤**。

io_uring 的 completion 模型把這個問題放大了。epoll 時代，你 `read()` 當場回來，buffer 和 context 的壽命很好管。io_uring 下，你**提交**一個請求、**稍後**才收到完成——在這段空窗裡，請求引用的 buffer、`user_data` 指向的 context 物件，**都必須活著**。開發者最常犯的錯是：以為「這個請求不要了」就把它的 context 釋放掉，但 CQE 還是會回來，你的 handler 一解參照那個 `user_data`，就是一個 use-after-free。

這類 bug 在壓力下偶發、極難重現——除非你用對工具讓它**每次都現形**。這就是這個練習要練的：用 sanitizer 把「偶發」變「必現」。

## 任務規格

### Part 1：用 ASan 抓應用層 UAF（必做，可在 WSL 跑）

| 項目 | 要求 |
|---|---|
| 目標 | 寫一支程式，重現「提早釋放 `user_data` 指向的 context，CQE 回來後 handler 解參照它」的 UAF |
| 工具 | 用 `-fsanitize=address` 編譯，讓 ASan 攔截並報告 |
| 驗收 | 程式跑起來，ASan 印出 `heap-use-after-free`，且報告中能看到**三個位置**：UAF 存取點、釋放點、原始配置點 |
| 進一步 | 修正這個 bug（正確的釋放時機），確認修正後 ASan 不再報錯 |

### Part 2：用 KASAN 觀察 kernel 側記憶體錯誤（選做，需受控 QEMU 環境）

| 項目 | 要求 |
|---|---|
| 目標 | 在自編的 KASAN kernel 上，跑 io_uring 的壓力測試（例如 liburing 的 `test/`），學會讀 KASAN 報告格式 |
| 工具 | `CONFIG_KASAN=y` 的 kernel、QEMU、liburing testsuite |
| 驗收 | 能讀懂一份 KASAN 報告的每一段（存取類型、物件、free/alloc stack、呼叫鏈） |
| 說明 | 本部分不要求重現特定 CVE 的完整利用；目標是**偵測與判讀**，建立「KASAN 說了什麼」的閱讀能力 |

## 期望輸出範例

Part 1 觸發 UAF 後，ASan 的報告長這樣（實測擷取）：

```
==504==ERROR: AddressSanitizer: heap-use-after-free on address 0x507000000100 ...
READ of size 4 at 0x507000000100 thread T0
    #0 ... in main .../pe_uaf.c:37          ← UAF 存取點：handler 解參照
freed by thread T0 here:
    #1 ... in main .../pe_uaf.c:31          ← 釋放點：提早 free(ctx)
previously allocated by thread T0 here:
    #1 ... in main .../pe_uaf.c:20          ← 原始配置點：calloc 出 ctx
SUMMARY: AddressSanitizer: heap-use-after-free .../pe_uaf.c:37 in main
```

修正後（把 `free` 移到 CQE 處理**之後**），程式正常輸出、ASan 不報錯：

```
completed req id=7, res=16
```

## 如果你卡住了

1. **ASan 沒報錯？** 想想：kernel 把資料寫進 `ctx->buf` 是在 **kernel** 做的，ASan 看不到 kernel 的存取。要讓 ASan 爆，UAF 的**存取必須發生在你的 user code 裡**——也就是 CQE 回來後，你的 handler 去讀那個已釋放的 context。確認你真的在 `free` 之後、於 user code 解參照了它。
2. **程式在 `free` 當下就崩，不是在 handler？** 你可能 free 了還在被提交路徑讀的東西。先讓 `io_uring_submit` 回來、確定請求已遞交，再 free，把 UAF 窗口放在「submit 後、handler 前」。
3. **想讓它更穩定現形？** 用會走 async 路徑的 fd（例如沒資料的 pipe）讓完成延遲，UAF 窗口更明顯。regular file 的 read 常常 inline 完成，窗口很短。
4. **Part 2 的 KASAN kernel 編不出來？** 先確認 `CONFIG_KASAN=y`、`CONFIG_KASAN_INLINE=y`、`CONFIG_DEBUG_INFO=y`，並用和 kernel 相容的編譯器。參考 [kernel_pwn 的環境章](../../security/kernel_pwn/00-environment-setup.md)。

## 實作步驟建議

### Step 1：寫一個會在 user code 解參照 user_data 的流程
配一個 context 物件、`io_uring_sqe_set_data(sqe, ctx)`、提交。

### Step 2：製造提早釋放
在 `io_uring_submit` 之後、`io_uring_wait_cqe` 之前 `free(ctx)`，模擬「誤以為請求作廢」。

### Step 3：在 handler 解參照它
`io_uring_cqe_get_data(cqe)` 取回指標，讀它的欄位——這就是 UAF 存取點。

### Step 4：用 ASan 編譯並跑
加 `-fsanitize=address -g`，跑，讀報告的三段 stack。

### Step 5：修正並驗證
把 `free` 移到 `io_uring_cqe_seen` 之後，重編重跑，確認 ASan 不再報錯。這一步最重要——它把「bug 長怎樣」變成「正確寫法長怎樣」。

## 完整參考解答

**先自己做再看。**

<details>
<summary>點開參考實作（Part 1，ASan 版）</summary>

```c
// pe_uaf.c — 用 AddressSanitizer 抓 io_uring 應用層最常見的 UAF
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <liburing.h>

struct req_ctx {         // 每個請求配一個 context，位址放進 user_data
    int   id;
    char  buf[64];
};

int main(void) {
    struct io_uring ring;
    io_uring_queue_init(8, &ring, 0);

    int fd = open("/etc/hostname", O_RDONLY);

    struct req_ctx *ctx = calloc(1, sizeof *ctx);
    ctx->id = 7;

    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, fd, ctx->buf, sizeof(ctx->buf) - 1, 0);
    io_uring_sqe_set_data(sqe, ctx);         // user_data = ctx
    io_uring_submit(&ring);

    // ——應用層 bug——
    // 誤以為「這個請求不要了」就釋放 context，但 CQE 還是會回來。
    free(ctx);                               // 提早釋放

    struct io_uring_cqe *cqe;
    io_uring_wait_cqe(&ring, &cqe);
    struct req_ctx *done = io_uring_cqe_get_data(cqe);
    printf("completed req id=%d, res=%d\n", done->id, cqe->res);  // UAF！
    io_uring_cqe_seen(&ring, cqe);

    io_uring_queue_exit(&ring);
    return 0;
}
```

編譯執行（liburing 用 Ch 0 的自編版；注意加 `-fsanitize=address`）：

```bash
LU=~/io_uring_course/liburing
gcc -O1 -g -fsanitize=address pe_uaf.c -o pe_uaf \
    -I"$LU/src/include" -L"$LU/src" -Wl,-rpath,"$LU/src" -luring
./pe_uaf
```

實測輸出（kernel 6.18、ASan）：

```
==504==ERROR: AddressSanitizer: heap-use-after-free on address 0x507000000100 ...
READ of size 4 at 0x507000000100 thread T0
    #0 ... in main .../pe_uaf.c:37
0x507000000100 is located 0 bytes inside of 68-byte region [...)
freed by thread T0 here:
    #0 ... in __interceptor_free
    #1 ... in main .../pe_uaf.c:31
previously allocated by thread T0 here:
    #0 ... in __interceptor_calloc
    #1 ... in main .../pe_uaf.c:20
SUMMARY: AddressSanitizer: heap-use-after-free .../pe_uaf.c:37 in main
```

**解答說明**：ASan 的三段 stack 正好對應 UAF 的三拍（Ch 32 的「三拍結構」）——配置（line 20）、釋放（line 31）、釋放後存取（line 37）。注意 ASan 抓到的是**你的 user code 解參照**（line 37），不是 kernel 寫 `ctx->buf`——因為 ASan 只看 user space。這帶出一個重要認知：kernel 側的 UAF（kernel 寫進你已釋放的 buffer）ASan 抓不到，那要 Part 2 的 KASAN。

**修正版**：把釋放時機移到正確的點。

```c
    struct io_uring_cqe *cqe;
    io_uring_wait_cqe(&ring, &cqe);
    struct req_ctx *done = io_uring_cqe_get_data(cqe);
    printf("completed req id=%d, res=%d\n", done->id, cqe->res);
    io_uring_cqe_seen(&ring, cqe);
    free(done);                              // ← 處理完 CQE 才釋放
```

修正後實測輸出：

```
completed req id=7, res=16
```

ASan 不再報錯。這條規則一句話：**`user_data` 指向的 context，壽命必須覆蓋到你處理完它的 CQE 為止**；在那之前釋放，就是 UAF。buffer 同理（Ch 12 的 buffer 所有權）。

</details>

<details>
<summary>點開 Part 2 指引（KASAN，受控 QEMU 環境）</summary>

> 本部分未在 WSL 實測（WSL kernel 不含 KASAN、不易替換），以下為在自有受控環境的標準做法與報告判讀。

**為什麼 Part 1 的 ASan 不夠**：ASan 只偵測 **user space** 的記憶體錯誤。io_uring 真正棘手的 UAF 發生在 **kernel**（kernel 寫進一個已釋放的 kernel 物件，Ch 34–37）。要偵測這些，需要 kernel 的對應工具 **KASAN**（Kernel Address Sanitizer）。

**環境準備**（在你自己的 QEMU VM）：
1. 取一份 kernel 原始碼，開 `CONFIG_KASAN=y`、`CONFIG_KASAN_INLINE=y`、`CONFIG_DEBUG_INFO=y`、`CONFIG_IO_URING=y`。
2. 編 kernel，用 QEMU 開機（參考 [kernel_pwn 環境章](../../security/kernel_pwn/00-environment-setup.md)）。
3. 在 VM 裡跑 io_uring 壓力：liburing 的 `make runtests`（`test/` 目錄有數百個回歸測試，會密集操作各種 op 與生命週期）。

**一份 KASAN UAF 報告的典型格式**（示意格式，非特定 CVE 輸出）：

```
==================================================================
BUG: KASAN: use-after-free in io_<某函式>+0x.../0x...
Read of size 8 at addr ffff888... by task <程序名>/<pid>

CPU: ...  Comm: ...  Not tainted ...
Call Trace:
 dump_stack+...
 print_report+...
 kasan_report+...
 io_<某函式>+...        ← UAF 發生在哪個 io_uring 函式
 ...
Allocated by task <pid>:          ← 物件在哪配置
 kasan_save_stack+...
 __kmalloc / kmem_cache_alloc+...
 io_<配置路徑>+...
Freed by task <pid>:              ← 物件在哪釋放
 kasan_save_stack+...
 kfree / kmem_cache_free+...
 io_<釋放路徑>+...
==================================================================
```

**怎麼讀它**（這才是練習重點）：
- **第一行**點出存取類型（use-after-free / out-of-bounds）、出事的函式、位址。
- **Call Trace** 是「誰碰了這塊壞記憶體」——對 io_uring 常常是某個 async 路徑（io-wq worker、task_work、poll 回呼）。看到這裡出現非提交者的執行流，就印證了 Ch 33 的 race 來源。
- **Allocated by / Freed by** 是 UAF 三拍的後兩拍：物件在哪生、在哪死。把這兩條 stack 和 Ch 21/29 的生命週期對起來，就能定位「哪一條路徑少持了一個引用 / 早釋放了一步」。

**防禦者的用法**：KASAN + syzkaller（Ch 40）是 kernel 社群找 io_uring bug 的主力組合。你在自有環境跑 liburing testsuite 或 syzkaller 的 io_uring descriptions，一旦 KASAN 報了，上面的讀法就是你分類這個 bug 的第一步。

</details>

## 測試用例

| 情境 | 預期結果 | 說明 |
|---|---|---|
| Part 1 原版 + ASan | `heap-use-after-free`，三段 stack | UAF 被攔 |
| Part 1 原版 **不加** ASan | 可能「看似正常」或偶發怪值/崩潰 | 印證 UAF 偶發、難抓——所以要 sanitizer |
| Part 1 修正版 + ASan | 正常輸出 `res=16`，無報錯 | 釋放時機正確 |
| Part 2 KASAN kernel 跑 testsuite | 乾淨或報出具體 UAF/OOB | 偵測 kernel 側錯誤 |

> 第二列值得親自試：拿掉 `-fsanitize=address` 重編，你會發現程式**常常印出看似正常的結果**——因為被釋放的記憶體還沒被覆寫。這正是 UAF 最陰險之處，也是「不能靠跑過沒事證明沒 bug」（呼應 Ch 5 的並行正確性論點）的又一例。

## 延伸挑戰（加分）

1. **buffer 所有權版**：把 UAF 從 `user_data` context 換成 **I/O buffer**——提交一個 read 進某 buffer，在完成前 `free` 那個 buffer，觀察行為。想想為什麼這個版本 ASan **不一定**抓得到（提示：誰在寫那個 buffer？）。
2. **多請求混淆**：同時在途多個請求，故意釋放其中一個的 context，看混在一起時更難用肉眼發現、但 ASan 一樣精準定位。
3. **把偵測自動化**：寫一個小 harness，用 ASan 跑你專案裡所有 io_uring 使用點，當作 CI 的一關——這是把這個練習變成實際防線的一步。
4. **（受控環境）重現一個已知 CVE 於 KASAN**：在自有 VM 用 Ch 35（CVE-2022-29582）或 Ch 37（CVE-2024-0582）影響範圍內的舊 kernel，跑公開的觸發程式，**觀察 KASAN 報告**（目標是判讀報告，不是提權）。

## 自我檢核

- [ ] 我能解釋為什麼 io_uring 的 completion 模型讓應用層 buffer/context 的生命週期特別容易出錯
- [ ] 我的 ASan 版真的印出了 `heap-use-after-free`，而且我看懂了三段 stack 各代表什麼
- [ ] 我能說出為什麼 ASan 抓不到 kernel 寫進已釋放 buffer 的那類 UAF，要用什麼工具
- [ ] 我能讀一份 KASAN 報告，指出存取點、配置點、釋放點
- [ ] 我理解「不加 sanitizer 跑起來沒事」為什麼不能證明沒有 UAF
- [ ] 我知道 `user_data` context 與 I/O buffer 的壽命，各自必須覆蓋到什麼時間點

Part 4 的防禦弧線到此收尾：從攻擊面地圖、漏洞模式、真實 CVE，到緩解政策、審計 fuzzing，再到這個親手讓記憶體錯誤現形的練習。你現在不只讀得懂 io_uring 的漏洞，也有工具把它們抓出來。下一章轉向效能工程——io_uring 的另一半價值。

→ [Ch 41 效能工程：polling、NAPI busy poll、benchmark](./41-performance-engineering.md)
