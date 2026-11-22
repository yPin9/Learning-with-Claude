# Ch 1 — 為什麼需要 io_uring：async I/O 演進史

> **目標**：理解在 io_uring 之前，Linux 的非同步 I/O 為什麼一直做不好。把 blocking → select/poll → epoll → Linux AIO 的痛點走一遍，你才會懂 ring 模型每個設計在解什麼問題，而不是把它當又一套 API 死背。

## 先建立直覺：I/O 的根本矛盾

一個程式要和外界（磁碟、網卡、檔案）交換資料。外界很慢，CPU 很快。核心矛盾只有一句話：

> **當資料還沒好，發出請求的那個 thread 該做什麼？**

歷史上每一代 I/O 模型，都是對這句話的不同回答：

```
blocking    : 原地睡著，什麼都不做           → 要很多 thread 才有並行
select/poll : 一個 thread 盯著一堆 fd 問「好了沒」 → 每次都要重問全部
epoll       : kernel 主動告訴你「這幾個好了」     → 但只告訴你「可以讀了」，還得自己讀
Linux AIO   : 請求直接交給 kernel，好了再通知    → 只對 O_DIRECT 有效，處處是坑
io_uring    : 和 kernel 共享兩個環，提交與完成都不離開使用者記憶體 → 本課主角
```

把這張表記住。接下來逐個拆。

## 第一代：blocking I/O —— 一個 thread 配一個連線

最原始的模型：`read()` 沒資料就讓 thread 睡著，資料來了再醒。

```c
char buf[4096];
ssize_t n = read(fd, buf, sizeof buf);   // 沒資料？thread 卡在這裡
```

要同時服務一萬個連線怎麼辦？開一萬個 thread（或 process）。這就是著名的 **C10K 問題**（同時處理一萬個連線）。問題在哪：

- 每個 thread 有 kernel 堆疊、排程成本、context switch 開銷。一萬個 thread 光排程就把 CPU 吃光。
- 記憶體：每 thread 預設 8MB 堆疊（virtual），一萬個就是 80GB 位址空間。

blocking 模型簡單好懂，到今天小規模服務還是這樣寫。但它的並行度受限於「你敢開幾個 thread」。

## 第二代：select / poll —— 一個 thread 盯多個 fd

想用少少的 thread 盯住很多連線，就需要「一次問多個 fd 誰好了」。`select`（1983, BSD）與 `poll`（System V）就是幹這個：

```c
fd_set rfds;
FD_ZERO(&rfds);
for (int i = 0; i < n; i++) FD_SET(fds[i], &rfds);
select(maxfd + 1, &rfds, NULL, NULL, NULL);   // 阻塞直到任一 fd 可讀
for (int i = 0; i < n; i++)
    if (FD_ISSET(fds[i], &rfds)) read(fds[i], ...);
```

比 blocking 進步：一個 thread 顧一萬個 fd。但有結構性缺陷：

1. **每次呼叫都要把整組 fd 從 user 傳進 kernel**，回來再掃一遍誰就緒——複雜度 **O(n)**，連線越多越慢，哪怕只有一個 fd 有事。
2. `select` 的 `fd_set` 預設上限 **1024**（`FD_SETSIZE`），超過就是 UB。
3. 每次呼叫 `fd_set` 會被改寫，要重建。

`poll` 解掉了 1024 上限（用陣列不用 bitmap），但 O(n) 重複掃描的根本問題還在。

## 第三代：epoll —— kernel 幫你記住誰在等

Linux 2.5.44（2002）引入 **epoll**。關鍵轉變：**狀態留在 kernel**。你先用 `epoll_ctl` 把關心的 fd 註冊進一個 epoll 實例（一次性），之後 `epoll_wait` 只回傳「現在就緒的那幾個」。

```c
int ep = epoll_create1(0);
struct epoll_event ev = { .events = EPOLLIN, .data.fd = sock };
epoll_ctl(ep, EPOLL_CTL_ADD, sock, &ev);          // 註冊一次
// ...
struct epoll_event events[64];
int nr = epoll_wait(ep, events, 64, -1);          // 只回傳就緒的
for (int i = 0; i < nr; i++) read(events[i].data.fd, ...);
```

epoll 是 Linux 高效能網路服務（nginx、Redis、Node.js 的 libuv）二十年來的地基。但它有兩個本課最在意的侷限：

**侷限一：它是「就緒通知」（readiness），不是「完成」（completion）。** epoll 只告訴你「這個 fd 現在讀不會阻塞了」，真正的 `read()` 還是要你自己再呼叫一次。所以一次資料傳輸至少兩次 syscall：`epoll_wait` + `read`。連線多、事件密時，syscall 數量很可觀。

**侷限二：它對一般檔案（regular file）基本無效。** 磁碟檔案在 epoll 眼中「永遠就緒」——因為它語意上總是可讀，哪怕實際要等磁碟轉半天。於是**磁碟 I/O 根本無法用 epoll 做非同步**。做網路的用 epoll，做檔案的只能另尋出路——出路就是下一代。

> 這就是為什麼高效能資料庫、儲存引擎對 epoll 一直不滿意：它們的瓶頸是磁碟，而 epoll 幫不上磁碟。

## 第四代（的失敗）：Linux AIO —— 對，但只對了一半

Linux 2.6.22 左右引入 **native AIO**（`io_setup` / `io_submit` / `io_getevents`，user-space 封裝成 libaio）。這是第一次真正的「completion 模型」：把 I/O 請求直接丟給 kernel，kernel 做完把結果放進一個完成佇列，你再去收。概念和 io_uring 已經很像了。

那它為什麼被公認是失敗品？

1. **只對 `O_DIRECT` 真正非同步**。用在一般（buffered）檔案 I/O 上，`io_submit` 會**偷偷變回同步阻塞**——API 說非同步，實際卡住，而且不報錯。這是最惡名昭彰的坑。
2. **支援的操作極少**：早期連 `fsync` 都不能非同步做。
3. **API 難用又易錯**：`io_submit` 在某些情況下仍會阻塞（例如要分配 metadata、要等 request slot）。
4. **每次 `io_submit`/`io_getevents` 都是一次 syscall**，高頻場景 syscall 開銷依舊。

Linus Torvalds 對 Linux AIO 的評價廣為流傳，大意是「這個介面爛透了，是被一群只在意自己那塊 microbenchmark 的人設計出來的」。AIO 證明了 completion 模型方向對，但實作把路走歪了。

> **認識論誠實**：libaio 至今仍在（MySQL/InnoDB 用它很久），不是完全沒用；說它「失敗」是指它沒能成為 Linux 通用的非同步 I/O 介面。io_uring 的目標之一，就是把 AIO 想做而沒做好的事做對。

## 第五代：io_uring —— 共享環，把 syscall 也省掉

2019 年 5 月，**io_uring** 隨 kernel **5.1** 合併，作者 **Jens Axboe**（也是 Linux block layer 與 `fio` 的維護者）。它的設計目標，正是前四代痛點的逐條回答：

| 前代痛點 | io_uring 的回答 |
|---|---|
| epoll 是 readiness，還要再 read | **completion 模型**：提交 read，完成時結果直接在 CQE |
| epoll 對檔案無效 | io_uring 對 **buffered 檔案、O_DIRECT、網路、甚至更多**都非同步 |
| AIO buffered 偷偷阻塞 | io_uring 用 kernel worker/poll 真正非阻塞處理 |
| 每次操作都 syscall | **共享記憶體環**：提交/完成都在 user 能直接讀寫的環上；配合 SQPOLL 可做到**零 syscall** |
| 一次只能提交一個 | **批次提交**：填好一批 SQE，一次 `io_uring_enter` 全送 |

核心機制（Ch 2 就會動手，這裡先有畫面）：kernel 和 user 共享**兩個環形緩衝區**：

```
      user space                         kernel space
   ┌──────────────┐                   ┌──────────────┐
   │ Submission   │  ──填入請求──▶    │              │
   │ Queue (SQ)   │                   │   io_uring   │
   └──────────────┘                   │   處理請求    │
   ┌──────────────┐                   │              │
   │ Completion   │  ◀──放入結果──    │              │
   │ Queue (CQ)   │                   └──────────────┘
   └──────────────┘
   兩個環都是 mmap 出來的共享記憶體，user 直接讀寫
```

你把請求（SQE, Submission Queue Entry）寫進 SQ，通知 kernel；kernel 處理完把結果（CQE, Completion Queue Entry）放進 CQ，你直接讀。環是共享記憶體，所以「通知」之外不需要為每個請求各自 syscall，甚至用 SQPOLL 模式連通知都省了（kernel 有個 thread 自己輪詢 SQ）。

**為什麼「省 syscall」在 2019 後變得特別重要？** Meltdown/Spectre 之後的緩解措施（KPTI，kernel page table isolation）讓每次進出 kernel 的成本大幅上升——syscall 不再便宜。io_uring 用「批次 + 共享環」把 syscall 次數壓到極低，正好打在這個時間點的痛處。

## 一個常見誤解

「io_uring 就是比較快的 epoll，拿來換掉就好。」

不完全對，三個層面要分清：

1. **模型不同**：epoll 是 readiness，io_uring 是 completion。移植不是把 `epoll_wait` 換成 `io_uring_wait_cqe` 那麼簡單，整個事件迴圈的所有權（buffer 由誰持有、何時可釋放）都變了。Ch 8 和 Part 2 會反覆碰到這個差異。
2. **範圍更廣**：io_uring 不只做網路，檔案、`fsync`、`openat`、`statx`、甚至任意 ioctl（`URING_CMD`）都能非同步化。它想取代的不只是 epoll，是「所有會阻塞的 syscall」。
3. **複雜度與攻擊面也更大**：共享環、非同步執行、大量生命週期管理——這正是 Part 3（實作）和 Part 4（安全）的核心。能力越大，出錯和被攻擊的面也越大。

## 本章重點整理

- I/O 模型的演進，都在回答「資料沒好時 thread 幹嘛」：blocking 睡、poll 輪詢、epoll 被通知、AIO/io_uring 交給 kernel 做完再收。
- epoll 的兩大侷限：**readiness 而非 completion**（還要再 read）、**對一般檔案無效**。
- Linux AIO 方向對（completion）但實作失敗：只對 `O_DIRECT`、buffered 偷偷阻塞、操作少、API 難用。
- io_uring（5.1, 2019, Jens Axboe）用**共享的 SQ/CQ 雙環 + 批次提交 + 可選 SQPOLL** 把前代痛點逐條解掉，並把適用範圍擴到幾乎所有阻塞操作。

## 自我檢核

- [ ] 不看筆記，我能說出 epoll 相對 select/poll 的關鍵進步是什麼（狀態留在 kernel）
- [ ] 我能解釋「readiness 模型」和「completion 模型」的差別，以及為什麼這個差別讓 buffer 管理變複雜
- [ ] 有人問我「為什麼不直接用 Linux AIO」，我能講出至少兩個它的致命缺陷
- [ ] 我能說出 io_uring 為什麼能大幅減少 syscall 次數，以及為什麼這在 Spectre 緩解後更重要

## 延伸閱讀

### 必讀設計文件

- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe（2019）
  - **這篇說什麼**：io_uring 作者親筆的設計動機與 ring 模型說明，開頭就在數落 Linux AIO 的問題
  - **讀哪裡**：第 1–2 節（motivation 與 ring 結構）整篇最該讀；memory ordering 那段 Ch 5 會回來細看
  - **前提**：懂 syscall 與基本記憶體概念即可

### 技術文章

- **[The rapid growth of io_uring](https://lwn.net/Articles/810414/)** — Jonathan Corbet（LWN, 2020）
  - **這篇說什麼**：io_uring 剛起步時 LWN 的全景介紹，把它和 AIO 的對比講得很清楚
  - **讀哪裡**：整篇不長；LWN 的 io_uring tag 下還有一整個系列，想追某功能的來龍去脈很有用
  - **為什麼值得讀**：Corbet 的 kernel 報導是業界公認最準的一手整理

- **[The C10K problem](http://www.kegel.com/c10k.html)** — Dan Kegel
  - **這篇說什麼**：2000 年代初把「一萬連線」問題講清楚的經典文，select/poll/epoll 的動機都在這
  - **讀哪裡**：前半的模型分類；後半的具體 API 清單已略過時，當歷史看
  - **前提**：無，適合建立大圖像

### 延伸

- **[Why you should use io_uring for network I/O](https://developers.redhat.com/articles/2023/04/12/why-you-should-use-iouring-network-io)** — Red Hat Developers
  - **這篇說什麼**：從網路角度講 io_uring 相對 epoll 的優勢與遷移考量，和本章「誤解」一節互補

理論講完，下一章立刻動手：不用 liburing，直接用三個 syscall 和兩個 mmap，手刻出一個最小的 io_uring hello world，把這章的 SQ/CQ 雙環從圖變成能跑的 code。

→ [Ch 2 三十分鐘全貌：SQ/CQ ring 與 raw hello world](./02-thirty-minute-tour.md)
