# Ch 5 — 提交與完成的 memory ordering

> **目標**：把 Ch 2、Ch 4 一直當規則死記的「推 tail 要 store-release、讀 tail 要 acquire」講到骨子裡。你會懂：為什麼共享環需要記憶體屏障、編譯器和 CPU 各自會怎麼重排、release/acquire 到底保證什麼、漏了屏障的具體後果、liburing 把屏障放在哪幾行、kernel 側怎麼和你配對、為什麼這類 bug 在 x86 上幾乎不現形卻在 ARM 上爆炸，以及 SQPOLL 模式多出來的那個 full barrier 是在守什麼。

> **環境**：kernel 6.18、gcc 11.4、liburing 2.16。本章有 raw 範例與 objdump，實測機器是 x86-64 WSL2。

## 為什麼這章是 io_uring 正確性的核心

io_uring 的整個效能故事建立在一件事上：**user 和 kernel 共享記憶體，不用為每個請求各自 syscall**。但共享記憶體是把雙面刃——一旦兩個執行流（你的 thread 和 kernel）同時讀寫同一塊記憶體，**它們看到彼此寫入的順序**就不再是理所當然的。

具體到 io_uring：你先把 SQE 內容寫好（64 byte 的 opcode、fd、buffer 位址……），**再**把 SQ tail +1 告訴 kernel「有新請求了」。這個「先寫內容、再更新 tail」的順序，對正確性是生死攸關的——如果 kernel 看到新 tail 的那一刻，卻還沒看到你寫的 SQE 內容，它就會拿一個**半成品 SQE** 去派發。讀到舊的 opcode、舊的 fd、舊的 buffer 位址——這是 UAF、亂寫記憶體、安全漏洞的溫床。

問題在於：**「先寫內容、再更新 tail」這個你在 C 原始碼裡寫下的順序，不保證是 kernel 實際觀察到的順序。** 編譯器會重排，CPU 會重排。memory ordering 這整套機制，就是用來把這個順序重新釘死的。少了它，io_uring 不是慢，是**偶發地、極難重現地錯**——最可怕的一種 bug。

這章沒有新的 API，只有一個觀念：**怎麼讓兩個執行流對「誰先誰後」達成一致**。這個觀念是 Ch 2 那些 `atomic_store_explicit(..., memory_order_release)` 背後的全部理由。

## 先建立直覺：三層重排

你寫的 `A; B;`（先做 A 再做 B），到 kernel 眼裡可能變成 `B; A;`。有三個獨立的「搗亂者」會重排你的操作：

```
 你寫的原始碼順序:   寫 SQE 內容 (A)  →  更新 tail (B)
       │
       │ ① 編譯器重排:優化時可能把 B 提到 A 前面
       ▼
 編譯後的機器碼:     B  →  A        (若編譯器認為兩者無關)
       │
       │ ② CPU 亂序執行 / store buffer:
       │    即使機器碼是 A→B,CPU 可能讓 B 的寫入「先被別人看到」
       ▼
 其他 CPU/kernel 觀察到的順序:  可能是 B 先、A 後
       │
       │ ③ 快取一致性傳播延遲:A 的寫入還卡在這顆 CPU 的 store buffer,
       │    還沒傳到跑 kernel 的那顆 CPU
       ▼
 kernel 讀到:  新 tail (B),但 SQE 還是舊的 (A 沒到)  ← 災難
```

三層要分清楚，因為對付它們的手段不同：

- **① 編譯器重排**：編譯器在不改變「單執行緒可觀察行為」的前提下，自由調換無依賴的指令順序。它不知道有另一個執行流在看這塊記憶體。對付它要**編譯器屏障**（告訴編譯器「這裡不准跨過去重排」）。
- **② CPU 亂序 / store buffer**：現代 CPU 亂序執行，寫入先進 store buffer 再慢慢寫回快取。別的 CPU 看到這些寫入的順序，不一定是程式順序。對付它要**CPU 記憶體屏障**（fence 指令）。
- **③ 傳播延遲**：多核間的快取一致性傳播需要時間。這其實是 ② 的一部分，屏障也一併處理。

`memory_order_release` / `memory_order_acquire` 這組工具，一次把 ①②③ 都管好——它既是編譯器屏障，又在需要的架構上插入 CPU fence。這就是為什麼我們不用 `volatile`（只擋編譯器、不擋 CPU）也不用全域 `mb()`（太重），而是用精準的 release/acquire。

## release / acquire 到底保證什麼

這兩個是成**對**使用的。單獨一個沒有意義——release 要和另一個執行流的 acquire 配對，才形成保證。

**store-release（寫端）**：`atomic_store_explicit(p, v, memory_order_release)`。保證：**這個 store 之前的所有讀寫，都排在這個 store 之前**，對任何用 acquire 讀到這個 `v` 的執行流可見。換句話說，「我在 release 之前寫的東西，你只要 acquire 讀到了我 release 的值，就一定也看得到」。

**load-acquire（讀端）**：`atomic_load_explicit(p, memory_order_acquire)`。保證：**這個 load 之後的所有讀寫，都排在這個 load 之後**。你不會「提前」看到 load 之後才該讀的東西。

把兩者配起來，就是 io_uring 提交端的核心保證：

```
 你(user)                              kernel
 ────────                              ──────
 寫 sqe->opcode = READ    ┐
 寫 sqe->fd = ...         │ 這些都排在
 寫 sqe->addr = buf       │ release 之前
 寫 sqe->user_data = ...  ┘
 store-release(sq.tail, tail+1) ──配對──▶ load-acquire(sq.tail)
                                           看到新 tail,就保證
                                           也看到上面那四行寫的 SQE 內容
                                           ↓
                                           安全地讀 sqe->opcode 等
```

release 像「封箱前把所有東西裝好、貼封條」，acquire 像「看到封條才開箱」。看到封條（新 tail）就保證箱子裡是完整的（SQE 內容齊了）。沒有這對屏障，kernel 可能「看到封條但箱子還半空」。

完成端完全對稱，方向相反：

```
 kernel                                 你(user)
 ──────                                 ────────
 寫 cqe->user_data = ...   ┐
 寫 cqe->res = ...         │ 排在 release 前
 寫 cqe->flags = ...       ┘
 store-release(cq.tail, tail+1) ─配對─▶ load-acquire(cq.tail)
                                         看到新 tail,就保證
                                         也看到完整的 CQE 內容
                                         ↓
                                         讀 cqe->res 等
 ... 等你收完 ...
 load/讀 cq.head  ◀──────配對──────── store-release(cq.head, head+n)
 (回收槽位)                             「這些 CQE 我收了,槽可回收」
```

四個配對點，記成一張表（這就是 io_uring 全部的 ordering 規則）：

| 共享欄位 | 寫端（release） | 讀端（acquire/對應屏障） | 守的不變式 |
|---|---|---|---|
| SQ tail | user：寫完 SQE 再 release 推 tail | kernel：acquire 讀 tail 再讀 SQE | kernel 不讀到半成品 SQE |
| SQ head | kernel：讀完 SQE 再 release 推 head | user：讀 head 再覆寫 SQE 槽 | user 不覆寫 kernel 還沒讀的 SQE |
| CQ tail | kernel：寫完 CQE 再 release 推 tail | user：acquire 讀 tail 再讀 CQE | user 不讀到半成品 CQE |
| CQ head | user：讀完 CQE 再 release 推 head | kernel：讀 head 再覆寫 CQE 槽 | kernel 不覆寫 user 還沒收的 CQE |

四條規則同一個形狀：**生產者「填好內容 → release 推進計數器」，消費者「acquire 讀計數器 → 讀內容」。** 把這個形狀記住，四個方向都是它。

## kernel 側的配對：讀 6.18 原始碼

這不是 user 側一廂情願——kernel 原始碼裡有和你完全對稱的屏障。6.18 的 `io_uring/io_uring.c` 開頭有一段註解，把整套配對講得明明白白，直接引用：

```
 * After the application reads the CQ ring tail, it must use an
 * appropriate smp_rmb() to pair with the smp_wmb() the kernel uses
 * before writing the tail (using smp_load_acquire to read the tail will do).
 * ...
 * Likewise, the application must use an appropriate smp_wmb() before
 * writing the SQ tail ..., which pairs with smp_load_acquire in
 * io_get_sqring (smp_store_release to store the tail will do).
```

對照具體函式，四個配對點在 kernel 側各是哪一行：

**① kernel acquire 讀 SQ tail**（`io_uring/io_uring.h` 的 `io_sqring_entries`）：

```c
static inline unsigned int io_sqring_entries(struct io_ring_ctx *ctx)
{
    struct io_rings *rings = ctx->rings;
    unsigned int entries;
    /* make sure SQ entry isn't read before tail */
    entries = smp_load_acquire(&rings->sq.tail) - ctx->cached_sq_head;
    return min(entries, ctx->sq_entries);
}
```

`smp_load_acquire(&rings->sq.tail)`——kernel 用 acquire 讀你推的 SQ tail，正是你 store-release 的配對端。那句註解「make sure SQ entry isn't read before tail」就是在說「acquire 保證讀 tail 後才讀 SQE，不會提前」。

**② kernel release 推 SQ head**（`io_commit_sqring`）：

```c
static void io_commit_sqring(struct io_ring_ctx *ctx)
{
    struct io_rings *rings = ctx->rings;
    /*
     * Ensure any loads from the SQEs are done at this point,
     * since once we write the new head, the application could
     * write new data to them.
     */
    smp_store_release(&rings->sq.head, ctx->cached_sq_head);
}
```

kernel 讀完 SQE 後，release 推 SQ head。註解點破了守的不變式：**一旦寫了新 head，user 就可能覆寫那些 SQE 槽**，所以必須保證 kernel 對 SQE 的讀全部完成後才推 head。

**③ kernel release 推 CQ tail**（`io_commit_cqring`）：

```c
static inline void io_commit_cqring(struct io_ring_ctx *ctx)
{
    /* order cqe stores with ring update */
    smp_store_release(&ctx->rings->cq.tail, ctx->cached_cq_tail);
}
```

kernel 填完 CQE，release 推 CQ tail——配對你的 load-acquire 讀 CQ tail。

**④ CQ head 的配對**靠 `io_get_cqe` 裡的控制相依（control dependency）加 release，kernel 讀 CQ head 決定還有沒有空間放新 CQE。

看到這裡，「user 側屏障」就不再是玄學——它是和 kernel 原始碼逐點對應的契約。你少一邊，契約就破。

## liburing 把屏障放在哪

你平常用 liburing，這些屏障被包進 `io_uring_submit` / `io_uring_for_each_cqe` / `io_uring_cqe_seen`。但它們就在原始碼裡，一行行可查。liburing 的屏障工具在 `src/include/liburing/barrier.h`，C 版本是對 `stdatomic.h` 的薄封裝：

```c
#define io_uring_smp_store_release(p, v)            \
    atomic_store_explicit((_Atomic __typeof__(*(p)) *)(p), (v), \
                          memory_order_release)
#define io_uring_smp_load_acquire(p)                \
    atomic_load_explicit((_Atomic __typeof__(*(p)) *)(p),   \
                         memory_order_acquire)
#define io_uring_smp_mb()                           \
    atomic_thread_fence(memory_order_seq_cst)
```

這和 Ch 2 我們手寫的 `atomic_store_explicit(..., memory_order_release)` 是同一個東西——liburing 沒有魔法。重點是它把每個屏障放對了位置：

**提交端**（`src/queue.c` 的 `__io_uring_flush_sq`）：

```c
if (sq->sqe_head != tail) {
    sq->sqe_head = tail;
    /* Ensure kernel sees the SQE updates before the tail update. */
    if (!(ring->flags & IORING_SETUP_SQPOLL))
        *sq->ktail = tail;                           // ← 注意:普通寫入!
    else
        io_uring_smp_store_release(sq->ktail, tail); // ← SQPOLL 才 release
}
```

這裡藏著一個**非常重要、幾乎所有教學都漏講**的細節：**非 SQPOLL 模式下，liburing 推 SQ tail 用的是普通寫入 `*sq->ktail = tail`，不是 store-release！** 為什麼可以？

因為非 SQPOLL 模式下，kernel **只在你呼叫 `io_uring_enter` 之後**才去讀 SQ。而 `io_uring_enter` 是個 **syscall**——syscall 本身就是一個完整的記憶體屏障（進 kernel 要做的事，天然排在你 syscall 之前的所有寫入之後）。既然寫 SQE 內容和推 tail 都發生在 enter 這個屏障**之前**，kernel 在 enter **之後**讀，順序自然有保證，不需要額外的 store-release。

只有 **SQPOLL** 模式例外：那時 kernel 有個 poll thread **並行地、不經過 syscall** 輪詢你的 SQ tail。沒有 syscall 當屏障了，你推 tail 和寫 SQE 內容之間就需要真正的 store-release 來保證順序。所以那個 `if/else` 不是可有可無的優化，是對「kernel 何時讀、經不經過 syscall」的精確回應。

> Ch 2 的 raw 範例在非 SQPOLL 下也用了 store-release 推 tail——那是**對但多餘**。多一個 release 在 x86 上零成本（下節會證明），在弱序架構上多一道 fence，正確性沒問題、效能有一丁點損失。liburing 選擇在非 SQPOLL 下省掉它，是把這個「syscall 即屏障」的洞察用到極致。

**完成端**（`src/include/liburing.h`，`io_uring_cqe_iter_init` 與 `io_uring_cq_advance`）：

```c
// 讀 CQ tail:acquire
.tail = io_uring_smp_load_acquire(ring->cq.ktail),   // 保證讀 tail 後才讀 CQE
// 收完推 CQ head:release
io_uring_smp_store_release(cq->khead, *cq->khead + nr); // 保證讀完 CQE 才推 head
```

完成端兩個屏障一個都不能省——因為 CQ 是 kernel **並行**寫的（kernel 隨時可能填新 CQE、推 CQ tail），不像 SQ 那樣有 syscall 當同步點。這就是為什麼完成端永遠要 acquire/release，提交端在非 SQPOLL 下可以偷懶。

## 漏掉屏障會怎樣，以及為什麼 x86 讓你看不到

這是最反直覺、最多人踩的坑。**在 x86 上故意漏掉 store-release，程式幾乎永遠「看起來正確」——然後同一份 code 搬到 ARM 伺服器上偶發崩潰。**

原因是 x86 的記憶體模型（x86-TSO）特別強：它**本來就不會重排 store-store 和 load-load**（store-load 才會重排）。所以在 x86 上，store-release 和普通 store 編出來的機器碼**幾乎一樣**。我們直接證明給自己看。這支程式有兩個 `noinline` 函式，一個 store-release、一個 load-acquire：

```c
// ch05_ordering.c（節錄）
__attribute__((noinline))
void publish_tail(_Atomic unsigned *tail, unsigned new_tail) {
    atomic_store_explicit(tail, new_tail, memory_order_release);
}
__attribute__((noinline))
unsigned read_tail_acquire(const _Atomic unsigned *tail) {
    return atomic_load_explicit(tail, memory_order_acquire);
}
```

編譯後 objdump 看這兩個函式編成什麼（x86-64）：

```bash
gcc -O2 -Wall -g ch05_ordering.c -o ch05_ordering
objdump -d --no-show-raw-insn ch05_ordering
```

實測輸出（kernel 6.18、x86-64）：

```
0000000000001480 <publish_tail>:
    1480:	endbr64 
    1484:	mov    %esi,(%rdi)      ← store-release 就是一個普通 mov!
    1486:	ret    
0000000000001490 <read_tail_acquire>:
    1490:	endbr64 
    1494:	mov    (%rdi),%eax      ← load-acquire 也是普通 mov!
    1496:	ret    
```

`memory_order_release` 的 store 編出來是 `mov %esi,(%rdi)`——一個**平凡的 mov**，沒有任何 fence 指令（沒有 `mfence`、`lock`、`sfence`）。load-acquire 同樣是平凡的 `mov`。這就是鐵證：

- **在 x86 上，store-release / load-acquire 對 CPU 是零成本**（和普通讀寫同一條指令）。它們唯一的作用是擋**編譯器**重排（編譯器仍被告知不准跨過它優化）。
- 所以如果你**漏掉** release、改用普通寫入，在 x86 上編出來的 CPU 指令**完全一樣**——程式照常跑對。bug 被 x86 的強記憶體模型藏起來了。
- 搬到 **ARM/AArch64**（弱記憶體模型，store-store 可重排），store-release 會編成 `stlr` 指令（store-release register），普通 store 編成 `str`。漏掉 release 用了 `str`，CPU 就真的可能把 tail 的寫入重排到 SQE 內容之前——kernel 讀到半成品 SQE，偶發崩潰。

這解釋了一個業界反覆上演的慘劇：**一份在 x86 上跑了三年沒事的 io_uring 程式，一上 ARM 伺服器（Graviton、Ampere）就開始偶發詭異崩潰。** 根因往往就是某處漏了屏障，x86 一直在替它遮掩。

> 這也是為什麼「我的程式跑起來沒問題」**完全不能**證明屏障用對了。並行正確性不能靠測試驗證——x86 上你測一萬次都對，不代表 ARM 上不炸。只能靠**推理**：對照上面那張四配對表，確認每個生產/消費點都有對應的 release/acquire。這是 io_uring（乃至所有 lock-free 程式）的鐵律。

## 一次正確的 raw 往返

把屏障放對的完整往返，用 Ch 2 的骨架送一個 NOP。關鍵在註解標出的「先寫資料、後 release」與「先 acquire、後讀資料」：

```c
// ch05_ordering.c（節錄）
unsigned tail = atomic_load_explicit(stail, memory_order_relaxed);
unsigned idx = tail & *smask;
memset(&sqes[idx], 0, sizeof(struct io_uring_sqe));
sqes[idx].opcode    = IORING_OP_NOP;     // ┐ 先把「資料」寫好
sqes[idx].user_data = 0xABCD;            // │
sarray[idx] = idx;                       // ┘
publish_tail(stail, tail + 1);           // 再 release 發佈 tail

io_uring_enter(fd, 1, 1, IORING_ENTER_GETEVENTS);

unsigned h = atomic_load_explicit(chead, memory_order_relaxed);
unsigned t = read_tail_acquire(ctail);   // 先 acquire 讀 tail
while (h != t) {
    struct io_uring_cqe *c = &cqes[h & *cmask];  // 後讀 CQE 內容
    printf("round-trip ok: user_data=0x%llx res=%d\n",
           (unsigned long long)c->user_data, c->res);
    h++;
}
atomic_store_explicit(chead, h, memory_order_release);  // release 推 head
```

注意讀 `head`（`stail` 的消費端自己擁有的那個 head，和對側）可以用 `relaxed`——因為那個計數器是**你自己**在寫，不需要和自己同步順序。只有和**另一個執行流**共享的方向才需要 release/acquire。這是個常被忽略的細節：屏障只在跨執行流的那一端加，自己讀自己寫的用 relaxed 就夠。

實測輸出：

```
round-trip ok: user_data=0xabcd res=0
seen 1 completion(s); release/acquire 配對全部走過
```

NOP 完成、`res=0`、`user_data` 原樣回來。這條路和 x86 上漏掉屏障跑出來的輸出**一模一樣**——再次印證：輸出相同，不代表正確性相同。差別只在弱序架構上才現形。

## SQPOLL 的額外 ordering：IORING_SQ_NEED_WAKEUP

SQPOLL 模式（Ch 9 細講）下，kernel 的 poll thread 自己輪 SQ，你不呼叫 enter。但 poll thread 閒置久了會睡，這時你得呼叫 `enter(..., IORING_ENTER_SQ_WAKEUP)` 叫醒它。問題是：**怎麼知道它睡了？** 讀 SQ flags 有沒有 `IORING_SQ_NEED_WAKEUP` 位。

這裡有個微妙的 ordering 陷阱，需要一個**比 release/acquire 更強的 full barrier**。考慮這個競態：

```
 你(user)                        kernel poll thread
 ────────                        ──────────────────
 推 SQ tail (有新活了)             檢查 SQ 有沒有新活
 讀 SQ flags 看要不要 wakeup        沒有 → 設 NEED_WAKEUP,準備睡
```

如果這兩邊的操作被重排，可能出現：你推完 tail、讀 flags 時 NEED_WAKEUP 還沒設（以為 thread 醒著，不 wakeup）；而 kernel 這邊設完 NEED_WAKEUP 睡了、沒看到你剛推的 tail。結果：**你有新活、thread 睡了、雙方都以為對方會處理**——請求永遠卡住。

這是個 **store-load** 重排問題（你 store tail、load flags；kernel store flags、load tail），而 store-load 正是 x86 **也會**重排的那一種。所以連 x86 都躲不掉這個 bug，必須用 full memory barrier（`smp_mb` / `mfence`）。liburing 的 `sq_ring_needs_enter`（`src/queue.c`）正是這麼做：

```c
if (!(ring->flags & IORING_SETUP_SQPOLL))
    return true;
/*
 * Ensure the kernel can see the store to the SQ tail before we read the flags.
 */
io_uring_smp_mb();                                    // ← full barrier!
if (IO_URING_READ_ONCE(*ring->sq.kflags) & IORING_SQ_NEED_WAKEUP) {
    *flags |= IORING_ENTER_SQ_WAKEUP;
    return true;
}
return false;
```

kernel 原始碼開頭那段註解也專門點名這件事：「When using the SQ poll thread, the application needs to check the SQ flags for `IORING_SQ_NEED_WAKEUP` **after** updating the SQ tail; a full memory barrier `smp_mb()` is needed between.」

這就是 SQPOLL 比普通模式多出來的那道屏障。記住它的特殊性：**它不是 release/acquire 能解決的，因為它防的是 store-load 重排**，而 store-load 是唯一連 x86-TSO 都放行的重排。SQPOLL 的 wakeup 漏 full barrier 的 bug，是少數在 x86 上也會現形的 ordering bug——某種程度上反而「幸運」，因為 x86 就能測出來。

## 對比與取捨

| 屏障種類 | 擋什麼 | x86 成本 | ARM 成本 | io_uring 用在哪 |
|---|---|---|---|---|
| 普通寫入 | 什麼都不擋 | mov | str | 非 SQPOLL 推 SQ tail（靠 syscall 當屏障） |
| `volatile` | 只擋編譯器重排 | mov | str | io_uring **不**用它（不夠） |
| store-release | 編譯器 + store-store/load-store | mov（零成本） | stlr | 推 CQ head、SQPOLL 推 SQ tail、kernel 推 CQ/SQ tail |
| load-acquire | 編譯器 + load-load/load-store | mov（零成本） | ldar | 讀 CQ tail、kernel 讀 SQ tail |
| full barrier `smp_mb` | 全部，含 store-load | mfence（有成本） | dmb ish | SQPOLL 的 NEED_WAKEUP 檢查 |

一句話總結取捨：**用剛好夠強的屏障**。release/acquire 擋住 io_uring 絕大多數場景需要的重排，且在 x86 上零成本；只有 SQPOLL 的 store-load 場景才動用昂貴的 full barrier。過度使用 `smp_mb` 到處灑會白白拖慢；用 `volatile` 代替 atomic 則根本擋不住 CPU 重排。

## 踩雷集錦

1. **用 `volatile` 代替 atomic 屏障**。錯誤直覺：「我把 tail 宣告成 `volatile`，編譯器就不會亂搞了。」正確認識：`volatile` 只擋**編譯器**重排，完全不擋 **CPU** 重排和跨核傳播。在 ARM 上 `volatile` 的寫入照樣被 CPU 重排，kernel 照樣讀到半成品。io_uring 要的是 `memory_order_release/acquire`（或 kernel 的 `smp_*`），不是 `volatile`。

2. **「我在 x86 測過沒問題」當成正確性證明**。錯誤直覺：「跑了一百萬次都對，屏障肯定沒問題。」正確認識：x86-TSO 把 store-store/load-load 重排 bug 全藏起來了，store-release 編成普通 mov（本章 objdump 實證）。x86 測一億次都對，ARM 上可能第一萬次就炸。並行正確性只能靠**對照配對表推理**，不能靠測試。

3. **以為提交端一定要 store-release**。錯誤直覺：「推 SQ tail 永遠要 release。」正確認識：**非 SQPOLL** 下 kernel 只在 `io_uring_enter`（一個 syscall，天然 full barrier）之後讀 SQ，所以 liburing 用普通寫入即可；**只有 SQPOLL**（kernel 並行輪詢、不經 syscall）才需要真正的 store-release。屏障的必要性取決於「對方何時讀、經不經過 syscall」。

4. **完成端偷懶省掉 acquire/release**。錯誤直覺：「CQ 那邊應該也能像 SQ 一樣靠 syscall 省屏障。」正確認識：**不行**。kernel 是**並行**地填 CQE、推 CQ tail 的（不等你 syscall），所以讀 CQ tail 必須 acquire、推 CQ head 必須 release，一個都不能省。SQ 的偷懶是因為它有 enter 當同步點，CQ 沒有。

5. **忘了 SQPOLL 的 wakeup 要 full barrier**。錯誤直覺：「SQPOLL 檢查 NEED_WAKEUP，用 acquire 讀 flags 就好。」正確認識：那是個 **store-load** 競態（你 store tail / load flags，kernel store flags / load tail），release/acquire 擋不住 store-load 重排，必須 `smp_mb()`。漏了它，poll thread 睡死、請求卡住——而且這個 bug 連 x86 都會現形。

## 進階：再往深一層

**為什麼 release/acquire 比全域 fence 更對、而不只是更快。** 直覺上「到處灑 `smp_mb` 最安全」。但 release/acquire 不只是便宜，它**表達了更精確的意圖**：release 說「我前面的寫入要讓看到這個值的人看見」，acquire 說「我要看見寫這個值的人之前的寫入」。這種「配對的、有方向的」語意，比一個「這裡全部排序」的 full fence 更能讓讀 code 的人（和編譯器）理解到底在同步什麼。io_uring 四個配對點各自只守一個不變式，用 release/acquire 把「守哪個」寫得清清楚楚——換成滿地 `smp_mb`，正確性可能還在，但意圖糊掉了，也關掉了編譯器在 fence 之間優化的空間。

**`READ_ONCE`/`WRITE_ONCE` 在這套裡的角色。** kernel 和 liburing 對共享記憶體的**每一次**存取都包 `READ_ONCE`/`WRITE_ONCE`（relaxed 原子），即使那次不需要 acquire/release 語意。為什麼？因為共享記憶體隨時可能被對側改，若用普通 C 存取，編譯器可能「tearing」（把一次讀拆成多次）、「重新載入」（假設值沒變而重用舊暫存器）、或「發明讀寫」。`READ_ONCE` 保證「就讀一次、原子地、每次都真的去讀」。所以完整的規則是：**跨執行流的同步點用 release/acquire，其餘每一次共享記憶體存取至少用 `READ_ONCE`/`WRITE_ONCE`**。Ch 4 看到的 `io_get_sqe` 裡 `READ_ONCE(ctx->sq_array[head])` 就是這個——它讀的是 user 可改的記憶體，必須保證讀到的值在後續檢查和使用之間是穩定的（先 `READ_ONCE` 存下來，再 `>= sq_entries` 檢查，再拿去索引，全程同一個值，杜絕 TOCTOU）。

**kernel 的屏障層級其實比 user 細。** 我們為了對稱把 kernel 側也說成 release/acquire，但 kernel 內部用的是更底層的 `smp_load_acquire`/`smp_store_release`/`smp_rmb`/`smp_wmb`，而且在不同路徑上會依據已持有的 lock、已經過的 syscall 邊界，省掉某些屏障。Part 3 讀 `io_uring/io_uring.c` 時你會看到，kernel 常靠「我這裡持著 `uring_lock`」或「我剛從 syscall 進來」來論證某個屏障可省——和 user 側非 SQPOLL 省 SQ tail release 是同一種推理。這套「屏障能省與否取決於還有沒有別的同步點」的思路，是讀所有 lock-free kernel code 的通用鑰匙。橫向對照 kernel_internals 對記憶體屏障的系統性整理會更踏實：→ [../kernel_internals/24-atomics-memory-ordering.md](../kernel_internals/24-atomics-memory-ordering.md)。

## 本章重點整理

- 共享環的正確性取決於「寫內容」和「更新計數器」的**順序**，而這順序會被①編譯器②CPU 亂序③傳播延遲三層重排打亂。release/acquire 一次管好三層。
- 四個配對點同一形狀：**生產者「填內容→release 推計數器」，消費者「acquire 讀計數器→讀內容」**。SQ tail、SQ head、CQ tail、CQ head 各一對，守的不變式見本章表格。
- kernel 側有對稱屏障：`io_sqring_entries`（acquire 讀 SQ tail）、`io_commit_sqring`（release 推 SQ head）、`io_commit_cqring`（release 推 CQ tail）——是逐點可查的契約，不是玄學。
- **非 SQPOLL 下推 SQ tail 可用普通寫入**，因為 `io_uring_enter`（syscall）就是 full barrier；**只有 SQPOLL** 需要真 store-release。完成端因 kernel 並行寫 CQ，acquire/release 一個都不能省。
- x86-TSO 把 store-release 編成**普通 mov**（本章 objdump 實證），零成本但也**把漏屏障的 bug 藏起來**——同一份 code 到 ARM 才爆炸。正確性靠推理不靠測試。
- SQPOLL 的 `NEED_WAKEUP` 檢查是 **store-load** 競態，release/acquire 擋不住，必須 full barrier `smp_mb()`；這類 bug 連 x86 都會現形。

## 自我檢核

- [ ] 不看筆記，我能說出為什麼共享環需要記憶體屏障，以及三層重排各是什麼、各用什麼擋
- [ ] 我能畫出四個配對點，說出每個的生產者/消費者和守的不變式
- [ ] 我能解釋為什麼**非 SQPOLL** 推 SQ tail 不需要 store-release，而完成端一定需要
- [ ] 有人說「我在 x86 測過沒問題」，我能說出為什麼這不能證明屏障用對了（並舉 objdump 的證據）
- [ ] 我能解釋 SQPOLL 的 `NEED_WAKEUP` 為什麼要 full barrier 而不是 acquire，以及它防的是哪種重排
- [ ] 我能說出 `READ_ONCE`/`WRITE_ONCE` 和 acquire/release 的分工
- [ ] 我能在 kernel 原始碼裡指出至少兩個和 user 側配對的屏障

## 延伸閱讀

### 必讀設計文件

- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：memory ordering 那節，作者親自說明 SQ/CQ 的屏障配對與 SQPOLL 的額外考量
  - **和本章關聯**：本章四配對表就是把這節展開 + 對照原始碼

### 原始碼（最權威的配對證據）

- **Linux 6.18 `io_uring/io_uring.c` 檔頭註解** — <https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/io_uring.c>
  - **讀哪裡**：檔案開頭那段 "A note on the read/write ordering memory barriers"，把 user/kernel 四個配對點和 SQPOLL full barrier 一次講完
  - **前提**：讀完本章再看，每句都對得上號
- **liburing `src/include/liburing/barrier.h` 與 `src/queue.c`**
  - **讀哪裡**：`barrier.h` 的 `io_uring_smp_store_release`/`load_acquire`/`mb` 定義；`queue.c` 的 `__io_uring_flush_sq`（非 SQPOLL 普通寫 vs SQPOLL release）和 `sq_ring_needs_enter`（NEED_WAKEUP 的 `smp_mb`）
  - **為什麼值得讀**：這是「屏障該放哪一行」的生產級範本

### 記憶體模型基礎

- **[Linux kernel memory barriers（`Documentation/memory-barriers.txt`）](https://www.kernel.org/doc/Documentation/memory-barriers.txt)**
  - **讀哪裡**：ACQUIRE/RELEASE 與 "SMP barrier pairing" 兩節，是理解本章配對的權威底層文件
  - **前提**：較硬，配合本章的 io_uring 具體例子讀比較不抽象
- **[Memory ordering（C11 atomics）- cppreference](https://en.cppreference.com/w/c/atomic/memory_order)**
  - **讀哪裡**：`memory_order_release`/`acquire`/`seq_cst` 的定義與範例，對應本章用的 `atomic_*_explicit`
  - **橫向連結**：本 repo 的 [../kernel_internals/24-atomics-memory-ordering.md](../kernel_internals/24-atomics-memory-ordering.md) 有系統性整理，建議對照

Part 1 的底層三章（syscall、佈局、ordering）到此打完地基。下一章起我們收起 raw syscall，改用 liburing——但你現在知道 `io_uring_submit` 那一行底下，推的是哪個 tail、放的是哪個屏障、守的是哪個不變式。liburing 不再是黑盒。

→ [Ch 6 liburing 入門](./06-liburing-intro.md)
