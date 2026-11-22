# Ch 0 — 環境搭建：WSL kernel 6.18、編 liburing、第一個 io_uring_setup

> **目標**：把一台能編譯、能跑、能對照原始碼的 io_uring 實驗機架好。本章結束你會有：一個新版 liburing、一鍵編譯腳本、一支真的跑起來的 io_uring 程式，以及一份 6.18 的 `io_uring/` 原始碼在手邊。

> **環境**：本課全程以 Linux kernel **6.18**、**liburing 2.16**、**gcc 11.4** 為基準。實測機器是 WSL2 的 `6.18.58.1-microsoft-standard-WSL2`。你用原生 Linux、VM、雲主機都可以，只要 kernel 夠新。

## 為什麼不能只用發行版的 liburing？

io_uring 這套東西有兩個獨立的版本線，初學者最常在這裡踩第一個坑：

- **kernel 版本**決定「有哪些 opcode、哪些 flag、哪些機制」——功能是 kernel 實作的。
- **liburing 版本**只是一層 user-space 封裝，決定「有沒有對應的 `io_uring_prep_xxx()` helper 可以呼叫」。

發行版（例如 Ubuntu 22.04）內建的 liburing 常常是 2.0 這種好幾年前的版本，缺一堆新 helper（buffer ring、multishot、zero-copy send 的封裝都沒有）。kernel 支援的功能，liburing 不一定幫你包好。所以本課**從 source 編一份新的 liburing**，但 kernel 用你系統現成的——只要夠新。

> 這個「kernel 新、liburing 舊」或反過來的錯配，是 io_uring 程式「編不過」或「`-EINVAL`」最常見的原因之一。先把這個心智模型建立起來：**功能看 kernel，helper 看 liburing**。

## Step 1：確認 kernel 夠新、io_uring 沒被關掉

```bash
uname -r
cat /proc/sys/kernel/io_uring_disabled
```

實測輸出：

```
6.18.58.1-microsoft-standard-WSL2
0
```

兩件事：

1. **kernel 版本**：io_uring 在 5.1 誕生，但本課用到的機制（provided buffer ring 5.19、multishot recv 6.0、zero-copy send 6.0、`DEFER_TASKRUN` 6.1、registered ring fd、futex op 6.7……）很多要 6.x。低於 6.1（上一個 LTS）會有不少章節跑不動，本課會逐章標注最低版本。
2. **`io_uring_disabled`**：這是 6.6 加入的全系統開關（Ch 39 會細談）。`0` = 完全啟用；`1` = 只有具 `CAP_SYS_ADMIN` 的程序能用；`2` = 全系統禁用（`io_uring_setup` 直接回 `-EPERM`）。ChromeOS、Android、以及一些加固過的發行版預設是 `2`。如果你這裡讀到 `2`，先 `sudo sysctl kernel.io_uring_disabled=0`（在你自己的機器上）。

> 這個檔案不存在（舊 kernel）不代表 io_uring 被關，只代表還沒有這個 sysctl——io_uring 本身是從 5.1 就在的。

## Step 2：編 liburing

```bash
sudo apt-get update && sudo apt-get install -y build-essential git   # 若還沒裝
mkdir -p ~/io_uring_course && cd ~/io_uring_course
git clone https://github.com/axboe/liburing.git
cd liburing
./configure
make -j"$(nproc)"
```

編完確認版本與產物：

```bash
grep -E "IO_URING_VERSION" src/include/liburing/io_uring_version.h
ls -l src/liburing.a src/liburing.so.*
```

實測輸出：

```
#define IO_URING_VERSION_MAJOR 2
#define IO_URING_VERSION_MINOR 16
-rw-r--r-- 1 ypp ypp 38800 ... src/liburing.a
-rwxr-xr-x 1 ypp ypp 40224 ... src/liburing.so.2.16
```

我們**不**執行 `sudo make install`——故意的。把它裝進系統會和發行版的 liburing 打架。本課一律用「指到剛編好的這份」的方式連結，下一步就設定。

## Step 3：一鍵編譯腳本

每次手打一長串 `-I -L -Wl,-rpath` 很煩，包成一個腳本。把下面存成 `~/io_uring_course/bin/iou-cc`：

```bash
#!/usr/bin/env bash
# iou-cc foo.c [-o foo] [extra gcc args] — 連結剛編好的 liburing 2.16
LU="$HOME/io_uring_course/liburing"
exec gcc -O2 -Wall -g "$@" -I"$LU/src/include" -L"$LU/src" -Wl,-rpath,"$LU/src" -luring
```

```bash
chmod +x ~/io_uring_course/bin/iou-cc
export PATH="$HOME/io_uring_course/bin:$PATH"   # 建議也寫進 ~/.bashrc
```

四個 flag 各自的作用，別當 magic：

| flag | 作用 |
|---|---|
| `-I"$LU/src/include"` | 讓 `#include <liburing.h>` 找到剛編的標頭，不是系統那份 |
| `-L"$LU/src"` | 連結時到這裡找 `liburing.so` |
| `-Wl,-rpath,"$LU/src"` | **執行**時也到這裡找 `.so`（否則跑起來會 `cannot open shared object file`） |
| `-luring` | 實際連結 liburing |

`-rpath` 這行是新手最常漏的：少了它編得過但跑不起來，因為 loader 預設不會去你的家目錄找 `.so`。

## Step 4：第一支 io_uring 程式

用 io_uring 讀一個檔案，印出來。先看 code，逐行的意義 Ch 2 會展開，這裡只要它跑起來：

```c
// smoke.c
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <liburing.h>

int main(void) {
    struct io_uring ring;
    int ret = io_uring_queue_init(8, &ring, 0);   // 建一個深度 8 的 ring
    if (ret < 0) { fprintf(stderr, "queue_init: %s\n", strerror(-ret)); return 1; }

    int fd = open("/etc/hostname", O_RDONLY);
    char buf[64] = {0};

    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);      // 拿一個提交槽
    io_uring_prep_read(sqe, fd, buf, sizeof(buf)-1, 0);      // 填成一個 read 請求
    io_uring_submit(&ring);                                  // 提交給 kernel

    struct io_uring_cqe *cqe;
    io_uring_wait_cqe(&ring, &cqe);                          // 等一個完成事件
    printf("read returned %d bytes: %.*s", cqe->res, cqe->res, buf);
    io_uring_cqe_seen(&ring, cqe);                           // 標記這個 CQE 處理完了

    io_uring_queue_exit(&ring);
    return 0;
}
```

編譯並執行：

```bash
iou-cc smoke.c -o smoke
./smoke
```

實測輸出：

```
read returned 16 bytes: DESKTOP-JK2JTGG
```

跑出來了：一個 read 不是用 `read(2)` syscall 當場完成的，而是**提交一個請求、之後取一個完成事件**。這個「提交」與「完成」分離，就是 io_uring 的全部精髓。注意 `cqe->res` 帶的是 read 的回傳值（讀到的位元組數）——io_uring 的錯誤與結果都從 CQE 回來，不是從提交當場回來。這點 Ch 8 會反覆強調。

> **故意弄壞它**：把 `open` 的路徑改成一個不存在的檔案（`fd` 會是 `-1`），重跑。你會看到 `cqe->res` 變成 `-9`（`-EBADF`）——錯誤不是在 `open` 當場 crash，而是以負的 errno 從 CQE 回來。先記住這個數字，Ch 8 解釋為什麼是負值。

## Step 5：探測這顆 kernel 支援哪些 opcode

io_uring 的能力隨 kernel 版本長，怎麼知道手上這顆支援什麼？用 `io_uring_get_probe()`：

```c
// probe.c
#include <stdio.h>
#include <liburing.h>
int main(void) {
    struct io_uring_probe *p = io_uring_get_probe();
    if (!p) { printf("probe failed (io_uring unavailable?)\n"); return 1; }
    printf("kernel supports opcodes 0..%d\n", p->last_op);
    const int ops[] = {IORING_OP_READ, IORING_OP_ACCEPT, IORING_OP_SEND_ZC,
                       IORING_OP_URING_CMD, IORING_OP_FUTEX_WAIT, IORING_OP_MSG_RING};
    const char *nm[] = {"READ","ACCEPT","SEND_ZC","URING_CMD","FUTEX_WAIT","MSG_RING"};
    for (int i = 0; i < 6; i++)
        printf("  %-10s = %2d : %s\n", nm[i], ops[i],
               io_uring_opcode_supported(p, ops[i]) ? "yes" : "NO");
    io_uring_free_probe(p);
    return 0;
}
```

```bash
iou-cc probe.c -o probe && ./probe
```

實測輸出（6.18）：

```
kernel supports opcodes 0..62
  READ       = 22 : yes
  ACCEPT     = 13 : yes
  SEND_ZC    = 47 : yes
  URING_CMD  = 46 : yes
  FUTEX_WAIT = 51 : yes
  MSG_RING   = 40 : yes
```

`last_op = 62` 說明這顆 kernel 認得 0 到 62 號 opcode。你在舊 kernel 上跑，這個數字會小很多，而 `SEND_ZC`、`FUTEX_WAIT` 這些可能是 `NO`。**本課每個用到較新 op 的章節，都會先提醒你用 probe 確認。**

## Step 6：把 6.18 的原始碼放手邊

Part 3 之後我們會逐檔讀 `io_uring/` 的 kernel 原始碼。不用抓整棵 kernel tree（太大），用 sparse checkout 只取 `io_uring/`：

```bash
cd ~/io_uring_course
mkdir -p ref && cd ref
git clone --depth 1 --branch v6.18 --filter=blob:none --sparse \
    https://github.com/torvalds/linux.git
cd linux
git sparse-checkout set io_uring include/uapi/linux include/linux/io_uring.h \
    include/linux/io_uring_types.h
ls io_uring/*.c | wc -l
```

實測：`io_uring/` 底下有 38 個 `.c` 檔、38 個 `.h` 檔。核心幾個先眼熟一下：`io_uring.c`（主體）、`rsrc.c`（registered 資源）、`poll.c`、`net.c`、`rw.c`、`timeout.c`、`io-wq.c`、`msg_ring.c`、`kbuf.c`（provided buffers）。Ch 19 會給完整地圖。

> **注意**：WSL2 跑的是微軟維護的 kernel（`6.18.58.1-microsoft-standard-WSL2`），它和 upstream v6.18 幾乎一致，但 config 與極少數 patch 可能有差。本課的原始碼引用一律以 **upstream v6.18** 為準；行為若和你的 WSL kernel 有出入，以你實機跑出來的為準，並留意是否 config 差異。

## 踩雷集錦

1. **編得過但跑不起來（`cannot open shared object file: liburing.so.2`）**：忘了 `-Wl,-rpath`。loader 執行時找不到你家目錄的 `.so`。要嘛加 rpath（本課做法），要嘛 `LD_LIBRARY_PATH=...`，要嘛靜態連結 `liburing.a`。
2. **`io_uring_setup: Operation not permitted`（`-EPERM`）**：`io_uring_disabled=2`，或在限制性的 seccomp/容器環境裡。先查 Step 1 的 sysctl；在雲端 managed 環境（部分 GKE、serverless）常被整個關掉。
3. **以為發行版的 liburing 就夠**：Ubuntu 22.04 的 liburing 2.0 沒有 `io_uring_prep_recv_multishot`、沒有 buffer ring API，本課 Part 2 一半的範例會 `undefined reference`。一定要編新的。
4. **用 kernel 版本去推功能、卻忘了 liburing 也要夠新**：反過來的錯配。kernel 6.18 支援某 op，但你連舊 liburing，沒有對應 `prep` helper，一樣用不了。兩邊都要新。
5. **在 WSL 裡預期能 `modprobe` 或動 kernel**：WSL2 的 kernel 不好換、載模組受限。Part 3 的練習 D（改 kernel 加 opcode）和 Part 4 的 PoC，建議另外開一台 QEMU 或 VM，本課對應章節會說明。

## 本章重點整理

- io_uring 的功能由 **kernel 版本**決定，可呼叫的 helper 由 **liburing 版本**決定，兩者獨立，都要夠新。
- 本課基準：kernel 6.18 / liburing 2.16 / gcc 11.4；用 `iou-cc` 腳本連結自編的 liburing。
- io_uring 的核心動作是**提交請求**與**取完成事件**分離；結果（含錯誤）從 CQE 的 `res` 回來。
- 用 `io_uring_get_probe()` 探測 kernel 支援的 opcode；用 `io_uring_disabled` 確認沒被全系統關掉。

## 自我檢核

- [ ] 我能說出「kernel 版本」和「liburing 版本」各自決定什麼，以及錯配會怎樣
- [ ] 我的 `iou-cc smoke.c -o smoke && ./smoke` 真的印出東西了
- [ ] 我知道 `-Wl,-rpath` 少了會發生什麼
- [ ] 我能用 probe 程式說出我這顆 kernel 的 `last_op` 是多少
- [ ] 我手邊有一份 6.18 的 `io_uring/` 原始碼可以 grep

## 延伸閱讀

### 官方文件 / 原始碼

- **[liburing README 與 `configure`](https://github.com/axboe/liburing)**
  - **讀哪裡**：README 的 build 段、`test/` 目錄；想確認某 helper 存不存在，直接 grep `src/include/liburing.h`
  - **為什麼值得讀**：liburing 的 helper 命名規律（`io_uring_prep_<op>`）一旦摸熟，查 API 比查文件快

### 部落格 / 文章

- **[Lord of the io_uring — Setup](https://unixism.net/loti/tutorial/index.html)** — Shuveb Hussain
  - **這篇說什麼**：用最小步驟帶你把 io_uring 跑起來，和本章互補
  - **讀哪裡**：Setup 與 Low-level 兩節；它的 raw syscall 版本 Ch 2–3 會對照
  - **前提**：會基本 C 與 Linux 編譯

### 參考（WSL 使用者）

- **[Microsoft WSL kernel repo](https://github.com/microsoft/WSL2-Linux-Kernel)**
  - **讀哪裡**：對應你 `uname -r` 版本的 tag；想確認 WSL kernel 的 io_uring config（`CONFIG_IO_URING`）開了沒，看 `Microsoft/config-wsl`
  - **注意**：一般學習不用動它；只有要自編 WSL kernel 時才需要

下一章先不碰 code，把「在 io_uring 之前，Linux 的 async I/O 為什麼一直做不好」這條歷史講清楚——理解了痛點，才知道 ring 模型每個設計在解什麼問題。

→ [Ch 1 為什麼需要 io_uring：async I/O 演進史](./01-why-io-uring.md)
