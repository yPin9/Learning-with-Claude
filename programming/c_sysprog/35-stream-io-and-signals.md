# Ch 35 — Stream I/O、EOF 與 signal

> **目標**：能說清楚 C 的串流（stream）和 UNIX 的檔案描述子（file descriptor）各自統一了什麼；理解 `getchar` 為什麼回傳 `int`、EOF 為什麼是「狀態」而不是字元、誰在什麼時候決定 EOF；能預測 stdio 緩衝在終端機、管線、`fork`、signal 之下的行為；能寫出正確處理 short count、`EINTR` 與 async-signal-safe 限制的程式；並看懂 QEMU、Git、coreutils 為這些細節補上的程式碼。

> **對應原文**：[你所不知道的 C 語言：Stream I/O, EOF 和例外處理篇](https://hackmd.io/@sysprog/c-stream-io) 的「串流與檔案描述子」「為何 getchar 傳回 int」「EOF 是狀態而非字元」「誰決定 end-of-file」「Unix I/O 與 stdio 的分工」「signal」「處理器例外如何成為 signal」「行程的建立、結束與 stdio 緩衝」「案例：開放原始碼專案的處置」「C 語言的設計取捨」等節

> **環境**：WSL2 Ubuntu 22.04 x86_64（AMD Ryzen 7 5700G），Linux 6.18（WSL 核心），gcc 11.4、clang 14、glibc 2.35、coreutils 8.32。終端機互動以 `script -qc` 或 Python `pty` 模組提供虛擬終端機（pty）重現。arm64 的行為（`char` 無號、整數除以零不 trap）本機無法執行，以 `-funsigned-char` 模擬或引用原文在 Ampere eMAG 上的結果，段落內標明。條款以 C11（N1570）為主；C23 把 `<stdio.h>` 由 7.21 移到 7.23，本章引用的段號除另行註明外不變。

## 為什麼需要這個？

C 講究效率，`getchar` 卻回傳 `int` 而非 `char`；UNIX 號稱一切皆檔案，C 標準卻把檔案操作歸在「串流」之下；`printf` 回傳成功，資料卻可能從來沒寫出去；同一個程式輸出到終端機印 6 行，接到 `wc -l` 卻變 8 行。這些看似瑣碎的現象都源於同一件事：**輸入可能結束、出錯或被打斷，程式除了資料本身，還得知道資料為什麼不再到來，以及寫出去的資料到底到了哪裡。**

前兩章反覆遇到這個問題：[Ch 33](./33-tricks-pointers-and-performance.md) 的 `ERR_PTR` 錯誤檢查讓程式 SIGSEGV，連前一行正常的輸出都消失了；[Ch 34](./34-undefined-behavior.md) 的除以零實驗，訊息能不能看到取決於 stdout 接的是終端機還是檔案。這一章把串流、緩衝、EOF 與 signal 之間的關係講清楚。

## 先建立直覺

一個 C 程式的 I/O 有三層，每一層各自「統一」了不同的東西：

```
  ┌─────────────────────────────── 程式 ───────────────────────────────┐
  │   getchar() / scanf() / printf() / fputs()                         │
  └───────────────┬────────────────────────────────────▲───────────────┘
                  │                                    │
  ┌───────────────▼──── C 串流（FILE，stdio）──────────┴───────────────┐
  │  緩衝區（全 / 行 / 無）、錯誤指示、end-of-file 指示                 │  ← 統一「各種作業系統」
  │  text / binary 的換行轉換（Windows CRLF）                          │    的 I/O 語意（C 標準）
  └───────────────┬────────────────────────────────────▲───────────────┘
                  │ write(1, buf, n)                   │ read(0, buf, 4096)
  ┌───────────────▼──── 檔案描述子（Unix I/O）─────────┴───────────────┐
  │  fd 0/1/2 → open file description（檔案位置、O_NONBLOCK）          │  ← 統一一個作業系統內
  │                                                                    │    的各種裝置（POSIX）
  └───────────────┬────────────────────────────────────▲───────────────┘
                  ▼                                    │
         終端機（line discipline） / 管線 / 一般檔案 / socket
                  ▲
                  └── signal 可以在任何一層「插隊」：打斷 read、殺掉行程（緩衝區跟著消失）
```

記住三件事，本章所有現象都能從這張圖推出來：

1. **EOF 不是資料**，是串流上的一個旗標（end-of-file indicator），由最底層 `read` 回傳 0 觸發。
2. **`printf` 只寫到中間那層的緩衝區**，什麼時候真的 `write` 下去，取決於緩衝模式與目的地。
3. **signal 不按牌理出牌**：它可以讓 `read` 失敗返回、可以讓行程在緩衝區還沒清空時就死掉。

## 串流與檔案描述子

UNIX 把裝置、管線、一般檔案都抽象為檔案描述子：行程啟動時 0、1、2 分別是標準輸入、輸出、錯誤，shell 在 `fork` 之後、`exec` 之前用 `dup2` 改變它們的指向，就是重導向與管線的實作。《The Linux Programming Interface》稱之為 universality of I/O：同一組 `open`/`read`/`write`/`close` 適用於所有檔案。但「一切皆檔案」並不徹底：socket 由 `socket()` 而非 `open()` 建立，終端機視窗大小這類控制交給 `ioctl` 這個萬用入口。更精確的說法是「一切皆檔案描述子」。

C 標準不能假設底層是 UNIX。C11 7.21.2 §1：

> Input and output, whether to or from physical devices such as terminals and tape drives, or whether to or from files supported on structured storage devices, are mapped into logical data streams, whose properties are more uniform than their various inputs and outputs.

C 誕生時許多作業系統的檔案以記錄（record）為單位、換行慣例各不相同，所以標準只定義「串流」並區分 **text stream** 與 **binary stream**。7.21.2 §2：text stream 輸入輸出時 "Characters may have to be added, altered, or deleted" 以符合主機慣例（Windows 的 CR-LF），只有由可列印字元組成、換行前沒有空白、以換行結尾的資料才保證讀回相同；§3：binary stream 讀回相同，但結尾可能多出實作定義個數的 null 字元——C99 Rationale 7.19.2 解釋："many systems do not keep track of the length of a file to the nearest byte"。

C89 委員會刻意**不把** `open`/`read`/`write` 納入 C。C99 Rationale 7.19 列出理由：與 stdio 功能重複、許多環境不支援「檔案是位元組陣列」的模型、換行轉換會遺失、0/1/2 號描述子在其他系統另有用途。代價寫在 7.19.2："The I/O facilities defined by the Standard are both more complex and more restrictive than the ancestral I/O facilities of UNIX."

`FILE` 物件依 C11 7.21.1 §2 記錄：檔案位置、緩衝區指標、**錯誤指示**（error indicator）、**end-of-file 指示**（end-of-file indicator）。後兩者是本章主角。

| | 檔案描述子（POSIX） | 串流 `FILE *`（C 標準） |
|---|---|---|
| 統一的範圍 | 一個作業系統內的各種裝置 | 各種作業系統之間的 I/O 語意 |
| 例外怎麼處理 | 推給 `ioctl` 與專屬系統呼叫 | 寫成 implementation-defined 條款 |
| 緩衝 | 無（每次都是系統呼叫） | 有，可用 `setvbuf` 調整 |
| short count | 呼叫端自己處理 | `fread`/`fwrite` 自動補齊 |
| 中繼資料（大小、權限） | `fstat` | 無 |
| 在 signal handler 中可用 | 是（`read`/`write` 是 async-signal-safe） | **否** |

## 為什麼 `getchar` 回傳 `int`

C11 7.21.7.1 §2–§3：`fgetc` 把讀到的字元「as an unsigned char converted to an int」回傳，所以正常結果落在 0–`UCHAR_MAX`（0–255）；7.21.1 §3 規定 `EOF` 是型別 `int` 的負值常數（glibc 定義為 `-1`）。`fgetc` 要表示 **256 種位元組 + 「沒有字元」共 257 種結果**，`char` 放不下。把回傳值存進 `char` 就會出錯：

```c
#include <stdio.h>

int main(void)
{
    char c;               /* 錯：應該是 int */
    long n = 0;

    while ((c = getchar()) != EOF)
        n++;
    printf("%ld\n", n);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -O2 eofchar.c -o eofchar        # 沒有任何警告
$ printf 'ab\377cd' | ./eofchar
2
```

5 個位元組只數到 2。`char` 是否有號由實作決定（C11 6.2.5 §15），x86-64 Linux 上有號：`getchar` 讀到 `0xFF` 回傳 255，存進 `char` 超出範圍，依 6.3.1.3 §3 轉換結果由實作定義，gcc 取模得到 -1——**和 `EOF` 相等**，迴圈在資料中途結束。

arm64 Linux 上 `char` 無號，錯法相反。本機用 `-funsigned-char` 模擬：

```
$ gcc -std=c11 -Wall -Wextra -O2 -funsigned-char eofchar.c -o eofu
eofchar.c:8:28: warning: comparison is always true due to limited range of data type [-Wtype-limits]
$ printf 'ab\377cd' | timeout 2 ./eofu; echo "exit=$?"
exit=124
$ clang -std=c11 -O2 -funsigned-char eofchar.c
eofchar.c:8:28: warning: result of comparison of constant -1 with expression of type 'char' is always true [-Wtautological-constant-out-of-range-compare]
```

`c` 永遠不等於 -1：讀到檔尾後 `getchar` 持續回傳 `EOF`，截成 255，迴圈永不結束，被 `timeout` 終止（124）。gcc 的 `-Wtype-limits` 由 `-Wextra` 啟用；clang 不加選項就警告。原文在真正的 arm64 機器上得到相同結果。

```
                         char 有號（x86-64）           char 無號（arm64）
  資料位元組 0xFF  ──►  c = -1 == EOF                 c = 255
                        資料中途誤判為檔尾 ✗           正確 ✓
  檔尾 (EOF = -1)  ──►  c = -1 == EOF                 c = 255 != EOF
                        正確結束 ✓                     迴圈永不結束 ✗
```

兩種架構錯法不同，修正方式相同：**用 `int` 接收，比較過 `EOF` 後再當字元使用**。

```
$ printf 'ab\377cd' | ./eofint        # 同一程式，c 宣告為 int
5
```

`<ctype.h>` 是同一個設計：C11 7.4 §1 規定引數「shall be representable as an unsigned char or shall equal the value of the macro EOF」，其他值是 UB。所以 `islower(c)` 的引數若來自有號 `char`，要寫 `islower((unsigned char) c)`。glibc 的分類表有 384 項，容許 -128 到 255 的索引來包容「broken old programs」，但 `0xFF` 轉成有號 `char` 是 -1，和 `EOF` 共用一項，Latin-1 的 `ÿ` 被當成 `EOF` 分類——這種錯誤函式庫無法彌補。

## EOF 是狀態，不是字元

`getchar` 回傳 `EOF` 有兩種可能：到達檔尾，或讀取錯誤。C11 7.21.7.1 註腳 289 指出要用 `feof` 與 `ferror` 區分。同一條 §3 還規定：

> If the end-of-file indicator for the stream is set, or if the stream is at end-of-file, the end-of-file indicator for the stream is set and the fgetc function returns EOF.

也就是說 **end-of-file 指示一旦設定，後續讀取一律回傳 `EOF`**（sticky），直到 `clearerr`、`rewind`、成功的 `fseek` 或 `ungetc` 清除它。glibc 長年沒遵守這條（從終端機讀到 EOF 後，下次讀取仍會再向終端機要資料），直到 glibc 2.28 修正 [Bug 1190](https://sourceware.org/bugzilla/show_bug.cgi?id=1190)，NEWS 寫明 "This corrects a longstanding C99 conformance bug"。本機 glibc 2.35 已包含這項修正，下一節會實際看到。

指示只在讀取**失敗之後**才設定，所以這個常見寫法會多跑一輪：

```c
while (!feof(fp)) {          /* 錯：最後一次讀取失敗後才變真 */
    fgets(line, sizeof line, fp);
    process(line);           /* 最後一輪處理的是上一行（或垃圾） */
}
```

正確寫法是**檢查讀取函式的回傳值**，迴圈結束後再用 `feof`/`ferror` 判斷原因。

`scanf` 一族遵循同樣模型：在第一個轉換完成前遇到 input failure 回傳 `EOF`，否則回傳成功指定的項目數。matching failure（例如 `%d` 遇到 `abc`）之後，依 7.21.6.2 §9，出錯的字元「remains unread」，所以只重試的迴圈會停不下來。常見的「補救」是 `fflush(stdin)`——但 C11 7.21.5.2 §2 只為輸出串流定義 `fflush`，對輸入串流是 UB：

```c
#include <stdio.h>

int main(void)
{
    int x;

    if (scanf("%d", &x) != 1) {
        fflush(stdin); /* ISO C：對輸入串流是未定義行為 */
        printf("next: %c\n", getchar());
    }
    return 0;
}
```

```
$ echo abc | ./flushin
next: a
$ ./flushin < abc.txt
next: a
```

不論管線或檔案，glibc 2.35 上 `fflush(stdin)` 都沒有丟掉任何輸入（POSIX 對可定位檔案的定義是「把描述子位置移回串流目前位置」，未讀的資料之後仍會讀到）。可攜作法是用 `getchar` 讀到換行或 `EOF` 為止，或用 `fgets` 讀整行再用 `strtol` 解析——後者還避開 `%d` 超出 `int` 範圍時的 UB（7.21.6.2 §10）。

## 誰決定 end-of-file

**一般檔案**：`read` 在檔案位置到達長度後回傳 0，C 函式庫據此設定 end-of-file 指示。**管線**：所有寫入端都關閉、資料讀盡後 `read` 回傳 0。**終端機**：由核心的 line discipline 製造，前提是處於 canonical mode（`ICANON`）。termios(3) 描述 `VEOF`（預設 Ctrl-D）：

> this character causes the pending tty buffer to be sent to the waiting user program without waiting for end-of-line. If it is the first character of the line, the read(2) in the user program returns 0, which signifies end-of-file.

**Ctrl-D 不會進入程式的輸入，也不是 EOF 字元**：它只讓 `read` 立即返回已累積的內容；在行首按下時內容為空，`read` 回傳 0，程式才解讀為檔尾。用 Python 的 `pty` 模組模擬打字（`|` 分隔每次送出的內容，`^D` 代表送出 `0x04`）：

```c
#include <stdio.h>

int main(void)
{
    int x, n = 0;

    while (scanf("%d", &x) == 1)
        n++;
    printf("read %d numbers.\n", n);
    int eof = feof(stdin) != 0;
    int c = getchar();                     /* EOF 是黏著的：不會再去 read */
    printf("feof=%d, getchar()=%d\n", eof, c);
    clearerr(stdin);
    printf("after clearerr: getchar()=%d\n", getchar());
    return 0;
}
```

```
$ printf '1 2 3 4\n|5 6 7 8|^D|^D|x\n' | python3 ptyrun.py ./count
1 2 3 4
5 6 7 8read 8 numbers.
feof=1, getchar()=-1
x
after clearerr: getchar()=120
```

（`ptyrun.py` 是 25 行的小工具：`pty.fork()` 後把每段內容寫進 pty 主端、收集畫面輸出。檔名不要取 `tty.py`——會遮蔽標準函式庫的 `tty` 模組，`pty` 匯入時循環引用失敗，這是作者實際踩到的雷。）

時序拆開來看：

```
  使用者                    line discipline              stdin (FILE)            程式
  "1 2 3 4" + Enter ──────► read 回傳 "1 2 3 4\n" ─────► scanf 取得 1,2,3,4
  "5 6 7 8"（不按 Enter）
  第一次 Ctrl-D ──────────► read 回傳 "5 6 7 8"（沒有換行）► 取得 5,6,7；讀完 8 後
                                                          還需要下一個字元才能確定數字結束
  第二次 Ctrl-D ──────────► tty 緩衝區已空，read 回傳 0 ► 設定 EOF 指示，8 被接受
                                                          下一次 scanf 回傳 EOF
                                                          getchar：不呼叫 read，立刻回 EOF（sticky）
                                                          clearerr 之後 getchar
  "x" + Enter ────────────► read 回傳 "x\n" ───────────► 120
```

**為什麼要按兩次 Ctrl-D**：第一次只把未完成的行交出去；第二次時緩衝區已空，`read` 才回傳 0。這也說明 end-of-file 是終端機驅動、C 函式庫、程式三方協作的結果。在 QEMU 的 `-serial stdio` 這類 raw mode 下（清除 `ICANON`），Ctrl-D 只是普通的位元組 `0x04`，原樣交給 guest。

其他系統用資料中的字元標示文字檔結尾：CP/M 的檔案以 128 位元組記錄為單位、長度只精確到記錄，文字檔以 Ctrl-Z（`0x1A`）結尾；Microsoft C 執行時期程式庫至今規定 text mode 下「CTRL+Z is interpreted as an end-of-file character on input」，用 `"rb"` 開啟才沒有這項轉換。所以 **text/binary 的區分在 POSIX 上沒有作用，在 Windows 上會改變讀到的內容**。

最後一行是否需要換行也是實作定義（7.21.2 §2）。POSIX 對「行」的定義是「零個以上非換行字元加一個換行」，工具據此運作：

```
$ printf 'a\nb' | wc -l
1
```

## Unix I/O 與 stdio 的分工

CS:APP 第 10 章把 C 程式可用的 I/O 分成兩層。**Unix I/O** 最通用、額外負擔最低、可以取得中繼資料（`fstat`）、在 signal handler 中可用，代價是 short count 與「讀一行」都要自己處理。**stdio** 以緩衝減少系統呼叫並自動補齊 short count，但沒有中繼資料、不是 async-signal-safe、也不適合 socket（下面會說為什麼）。

### short count

`read(fd, buf, n)` 回傳本次實際傳輸的位元組數，可能小於 `n`，CS:APP 稱為 **short count**。發生時機：到達檔尾；canonical 終端機一次最多交一行；管線與 socket 交出當下已到的資料就返回；傳輸部分資料後被 signal 打斷。**讀本地磁碟的一般檔案，除了檔尾以外實務上不會遇到 short count**——所以只拿檔案測試的程式，常在接上管線或網路後才出錯。直接用 Unix I/O 要用迴圈補齊：

```c
#include <errno.h>
#include <stdio.h>
#include <unistd.h>

static int calls;

/* 讀滿 n 位元組，除非先遇到檔尾或錯誤 */
static ssize_t readn(int fd, void *buf, size_t n)
{
    char *p = buf;
    size_t left = n;

    while (left > 0) {
        ssize_t r = read(fd, p, left);
        calls++;
        if (r == -1) {
            if (errno == EINTR)
                continue;          /* 被 signal 打斷：重試 */
            return -1;
        }
        if (r == 0)
            break;                 /* 檔尾 */
        p += r;
        left -= (size_t) r;
    }
    return (ssize_t) (n - left);
}

int main(void)
{
    char buf[16];
    ssize_t n = readn(STDIN_FILENO, buf, sizeof buf);

    if (n == -1) {
        perror("read");
        return 1;
    }
    printf("%zd bytes in %d read calls\n", n, calls);
    return 0;
}
```

```
$ printf abcdefgh | ./readn
8 bytes in 2 read calls
$ (printf abc; sleep 1; printf defgh) | ./readn
8 bytes in 3 read calls
$ ./readn < r100.bin                       # 100 位元組的一般檔案
16 bytes in 1 read calls
```

第一次：寫入端一次送出 8 位元組，第一次 `read` 全拿到，第二次回傳 0。第二次：第一次 `read` 只拿到 `abc`，一秒後才拿到 `defgh`。一般檔案：一次就讀滿 16 位元組。CS:APP 的 `rio_readn`、OpenSSH 的 `atomicio6`、QEMU 的 `qemu_write_full`、Git 的 `read_in_full` 都是這種迴圈。

`fread` 已替呼叫端補齊 short count（C11 7.21.8.1 把它定義成逐一呼叫 `fgetc`）；但 glibc 把 `EINTR` 當成讀取錯誤，設定錯誤指示後返回，後面 signal 一節會看到。

### 緩衝的動機：系統呼叫有多貴

讀完標準輸入並計數，有引數時每個位元組呼叫一次 `read`，否則用 `getchar`：

```c
#include <stdio.h>
#include <unistd.h>

int main(int argc, char *argv[])
{
    (void) argv;
    long n = 0;

    if (argc > 1) {            /* 每個位元組一次 read 系統呼叫 */
        char c;
        ssize_t r;
        while ((r = read(STDIN_FILENO, &c, 1)) == 1)
            n++;
        if (r == -1) {
            perror("read");
            return 1;
        }
    } else {                   /* stdio 一次補滿緩衝區 */
        while (getchar() != EOF)
            n++;
        if (ferror(stdin)) {
            perror("getchar");
            return 1;
        }
    }
    printf("%ld\n", n);
    return 0;
}
```

讀 1 MiB 的檔案：

```
$ strace -e trace=read ./cb < 1m.bin 2>&1 | grep -m2 "read(0"
read(0, "\315k\202\346..."..., 4096) = 4096
read(0, "\335H\"se..."..., 4096) = 4096
$ /usr/bin/time -f "getchar %e s (user %U sys %S)" taskset -c 3 ./cb < 1m.bin
getchar 0.00 s (user 0.00 sys 0.00)
$ /usr/bin/time -f "read1   %e s (user %U sys %S)" taskset -c 3 ./cb x < 1m.bin
read1   0.72 s (user 0.18 sys 0.53)
```

`getchar` 版本每次向核心要 4096 位元組（`strace -c` 統計整個行程共 258 次 `read`，含動態載入器讀取 libc 的那一次），逐位元組版本是 1,048,577 次，三次執行都在 0.72–0.73 秒，其中約 0.5 秒是核心態時間。換算每次 `read` 約 0.7 微秒——和原文在 Threadripper 上量到的 0.66 微秒同一量級。代價來自兩處：模式切換（`syscall` 指令進入核心再返回），以及核心執行時對快取、分支預測器等處理器狀態的干擾（FlexSC 論文在 OSDI 2010 量到一次 `pwrite` 後使用者程式的 IPC 最多要 14,000 個週期才恢復）。stdio 的緩衝區大小取自 `fstat` 的 `st_blksize`（本機 ext4 是 4096）。

### 何時不用 stdio

1. **signal handler 裡**：stdio 不是 async-signal-safe。
2. **socket**：C11 7.21.5.3 §7 規定同一串流「輸入之後要輸出，中間必須有定位函式」，socket 卻無法定位（`lseek` 回 `ESPIPE`）。CS:APP 的建議是 socket 用 RIO，需要格式化時先 `sprintf` 到記憶體再 `rio_writen`。
3. **非阻塞描述子**：stdio 不是為 `O_NONBLOCK` 設計的，遇到 `EAGAIN` 就設錯誤指示（QEMU 案例會看到後果）。

## 緩衝模式：全緩衝、行緩衝、無緩衝

C11 7.21.3 §3 定義三種模式，用詞是 "intended"（例如行緩衝 "are intended to be transmitted ... when a new-line character is encountered"），並說 "Support for these characteristics is implementation-defined"。§7 規定：**標準錯誤不得為全緩衝；標準輸入輸出若且唯若能判定不指向互動式裝置時才是全緩衝**。glibc 依描述子是否為終端機決定 stdout 的模式（判斷細節見 [Ch 41](./41-c-runtime.md)）：

| stdout 接到 | glibc 的模式 | 什麼時候 `write` |
|---|---|---|
| 終端機 | 行緩衝 | 遇到換行、緩衝區滿、讀取行緩衝的 stdin 時 |
| 管線、檔案 | 全緩衝（4096 或 8192） | 緩衝區滿、`fflush`、`exit` |
| stderr | 無緩衝 | 每次呼叫 |

§3 還期望「在需要向主機要資料的行緩衝或無緩衝串流上要求輸入時，把累積的字元送出」。glibc 的作法比字面窄，用 `strace` 看：

```c
#include <stdio.h>

int main(void)
{
    printf("name? "); /* 沒有換行 */
    int c = getchar();
    printf("got %d\n", c);
    return 0;
}
```

```
$ printf 'x\n' | script -qc "strace -e trace=read,write ./prompt" /dev/null    # stdin、stdout 都是終端機
write(1, "name? ", 6)                   = 6
read(0, "x\n", 1024)                    = 2
write(1, "got 120\n", 8)                = 8
$ script -qc "strace -e trace=read,write ./prompt < in.txt" /dev/null         # stdin 改成檔案
read(0, "x\n", 4096)                    = 2
write(1, "name? got 120\n", 14)         = 14
```

stdin 是終端機時，提示在 `read` 之前寫出；stdin 改成檔案（stdout 仍是終端機），`read` 先發生，提示和結果一起寫出。glibc 的 `_IO_new_file_underflow` 只在被讀的串流是行緩衝或無緩衝時清空行緩衝的 stdout，註解直言 "This really isn't required by any standard"。互動程式不必在提示後呼叫 `fflush` 也能運作，靠的是這項慣例而非標準保證。

### `fork` 之後的重複輸出

緩衝區在使用者空間，`fork` 會連同緩衝區一起複製：

```c
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>

int main(void)
{
    for (int i = 0; i < 2; i++) {
        if (fork() < 0) {
            perror("fork");
            return 1;
        }
        printf("-\n");
    }
    while (wait(NULL) > 0)
        ;
    return 0;
}
```

```
$ ./forkbuf | wc -l
8
$ script -qc ./forkbuf /dev/null | wc -l
6
```

`printf` 一共被呼叫 6 次（第一輪 2 個行程各一次，第二輪 4 個行程各一次）。接終端機時行緩衝，每次 `printf` 立即寫出，6 行。接管線時全緩衝：第一輪印的 `-\n` 還在緩衝區，第二輪 `fork` 把它複製給子行程，最後 4 個行程結束時各寫出兩行，**8 行**。

```
  全緩衝（接管線）時的緩衝區內容：

  P ── printf ──► [ -\n ]
  │ fork（第 1 輪）
  ├─ P  [ -\n ]          ── fork（第 2 輪）──┬─ P   [ -\n -\n ] ─ exit 時寫出 2 行
  │                                          └─ P'  [ -\n -\n ] ─ exit 時寫出 2 行
  └─ C  [ -\n ]          ── fork（第 2 輪）──┬─ C   [ -\n -\n ] ─ 2 行
                                             └─ C'  [ -\n -\n ] ─ 2 行      共 8 行
```

修正方法：`fork` 前呼叫 `fflush(stdout)`；子行程若在 `exec` 失敗後要結束，用 `_exit` 而不是 `exit`，避免再次寫出繼承來的緩衝區。

### `fork` 共享的是什麼：三張表

`fork` 複製使用者空間的緩衝區，**核心中的檔案狀態卻是共享的**。CS:APP 10.8 用三張表描述：每個行程有自己的描述子表；描述子指向全系統共用的 open file table（POSIX 稱 open file description，記錄**檔案位置**與 file status flag 如 `O_NONBLOCK`）；它再指向 v-node（Linux 的 inode）。`open` 兩次得到兩個 open file description；`dup`、`dup2`、`fork` 則讓多個描述子指向同一個：

```c
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

static char next(int fd)
{
    char c;

    if (read(fd, &c, 1) != 1) {
        perror("read");
        exit(1);
    }
    return c;
}

int main(void)
{
    int fd1 = open("abcdef.txt", O_RDONLY);
    int fd2 = open("abcdef.txt", O_RDONLY);   /* 第二次 open：另一個 open file description */

    if (fd1 == -1 || fd2 == -1) {
        perror("open");
        return 1;
    }
    char a = next(fd1), b = next(fd2);
    printf("two opens: %c %c\n", a, b);
    fflush(stdout);                           /* fork 前清空，避免重複輸出 */

    pid_t pid = fork();
    if (pid == -1) {
        perror("fork");
        return 1;
    }
    if (pid == 0) {
        printf("child: %c\n", next(fd1));     /* 繼承的 fd1：共享檔案位置 */
        return 0;
    }
    if (waitpid(pid, NULL, 0) == -1) {
        perror("waitpid");
        return 1;
    }
    printf("parent: %c\n", next(fd1));
    return 0;
}
```

```
$ printf abcdef > abcdef.txt && ./share
two opens: a a
child: b
parent: c
```

```
   親代 fd1 ─┐                          親代 fd2 ─┐
   子行程 fd1 ┴─► open file desc A          子行程 fd2 ┴─► open file desc B
                  位置: 0→1→2→3                            位置: 0→1
                  O_NONBLOCK? 參照計數 2                   參照計數 2
                         └──────────► inode (abcdef.txt) ◄──────┘
```

`fd1`、`fd2` 各自從頭讀到 `a`；子行程透過繼承的 `fd1` 讀到 `b`，移動的是和親代共享的位置，親代接著讀到 `c`。shell 重導向也靠這個區別：

```
$ sh -c 'echo out; echo err >&2' > log 2>&1; cat log     # dup2：共用位置，依序接在後面
out
err
$ sh -c 'echo out; echo err >&2' > log 2> log; cat log   # 兩次 open：各從位置 0 寫，互相覆蓋
err
```

## signal

`<signal.h>` 自 C89 起是標準的一部分，原型是 `void (*signal(int sig, void (*func)(int)))(int);`——「接受 `int` 與函式指標，回傳函式指標」（宣告的讀法見 [Ch 9](./09-void-pointer-and-callbacks.md)）。C11 7.14 §3 只定義六個：`SIGABRT`、`SIGFPE`、`SIGILL`、`SIGINT`、`SIGSEGV`、`SIGTERM`，§4 也不要求實作主動產生它們（除了經 `raise`）。依產生方式分兩類：**同步**的（程式自己的指令觸發，如存取無效位址的 `SIGSEGV`、整數除以零的 `SIGFPE`），和**非同步**的（外部送來，如 Ctrl-C 的 `SIGINT`、`kill` 的 `SIGTERM`，可能在任何兩道指令之間到達）。

### signal handler 能做的事很少

handler 可能在主程式執行任何函式的中途被呼叫——例如 `printf` 正在改 stdout 的緩衝區、`malloc` 正在調整 heap 的串列。在 handler 裡再呼叫 `printf` 或 `malloc`，就可能看到（或弄壞）半更新的內部狀態。C11 7.14.1.1 §5 因此規定：非經 `abort`/`raise` 引發的 signal，handler 若

- 存取具靜態或執行緒儲存期、且不是 lock-free atomic 的物件（**對 `volatile sig_atomic_t` 指定值除外**），或
- 呼叫 `abort`、`_Exit`、`quick_exit`、重設同一 signal 的 `signal` 以外的標準函式庫函式，

行為未定義。§3 另規定 handler 從 `SIGFPE`、`SIGILL`、`SIGSEGV` 等計算例外**返回**是 UB；在 Linux 上，由硬體 fault 引發的 signal 從 handler 返回後，處理器會重新執行同一道出錯的指令，條件沒變就再觸發一次。POSIX 另列出 async-signal-safe 函式清單（`write`、`read`、`_exit`、`sigaction`、`tcsetattr` 等，見 signal-safety(7)），`printf` 與 `malloc` 都不在其中。

CS:APP 8.5.5 把這些整理成準則：G0 handler 越簡單越好（設旗標就返回）；G1 只呼叫 async-signal-safe 函式；G2 進入時保存 `errno`、離開前還原（handler 裡的 `write` 可能改寫 `errno`，主程式剛失敗的系統呼叫原因就被覆蓋）；G3 存取共享資料時暫時遮蔽 signal；G4 全域變數宣告 `volatile`；G5 旗標用 `volatile sig_atomic_t`。還有一條常被忽略：**signal 不會排隊**——每種 signal 只有一個 pending 位元，handler 執行期間再來的至多留一個，所以不能用 handler 的執行次數計算事件次數（`SIGCHLD` 的 handler 要用迴圈回收所有子行程）。

最保守的 handler 有兩種：設 `volatile sig_atomic_t` 旗標交給主程式，或立即結束：

```c
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>

static void on_fpe(int sig)
{
    (void) sig;
    _Exit(3);             /* 從 SIGFPE handler 返回是未定義行為，直接離開 */
}

int main(int argc, char *argv[])
{
    (void) argv;
    volatile int zero = argc - 1;

    if (signal(SIGFPE, on_fpe) == SIG_ERR) {
        perror("signal");
        return EXIT_FAILURE;
    }
    printf("dividing..."); /* 沒有換行：留在緩衝區 */
    return 42 / zero;
}
```

```
$ ./fault; echo " exit=$?"          # -O0、-O2 皆同
 exit=3
```

`dividing...` 沒有換行，不論接終端機或管線都還在緩衝區；glibc 的 `_Exit` 直接進核心不清空緩衝區（C11 7.22.4.5 §2 說是否清空由實作定義），訊息遺失。POSIX 的 async-signal-safe 清單包含 `_Exit` 與 `_exit`，但**不含** C11 的 `quick_exit`，所以在 POSIX 環境下 handler 用前兩者結束最穩妥。原文指出同一份程式在 arm64 上印出 `dividing...` 並以 0 結束：arm64 的 `sdiv` 除以零得 0，不觸發例外（本機無法驗證）。

### `signal` 的兩種語意與 `EINTR`

行程阻塞在 `read`、尚未讀到資料時收到 signal，handler 執行完後系統呼叫有兩種結果：**自動重新開始**，或**以 `EINTR` 失敗返回**。歷史上 System V 的 `signal` 選後者（而且 handler 執行一次就恢復為 `SIG_DFL`），BSD 選前者並保留 handler。glibc 的 `signal` 一律用 `sigaction` 實作，**依 feature test macro 選擇旗標**：定義 `__USE_MISC` 時是 BSD 語意（`SA_RESTART`），否則轉向 `__sysv_signal`（`SA_ONESHOT | SA_NOMASK | SA_INTERRUPT`）。而 `-std=c11` 讓編譯器預先定義 `__STRICT_ANSI__`，glibc 的 `<features.h>` 因此不定義 `_DEFAULT_SOURCE` 與 `__USE_MISC`。結果是：**同一份原始碼，換個 `-std` 選項就改變 I/O 行為**。

```c
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>

static volatile sig_atomic_t stop;

static void on_int(int sig)
{
    stop = sig;
}

int main(void)
{
    long lines = 0;
    int c;

    if (signal(SIGINT, on_int) == SIG_ERR) {
        perror("signal");
        return EXIT_FAILURE;
    }
    while ((c = getchar()) != EOF) {
        if (c == '\n')
            lines++;
    }
    if (ferror(stdin) && !stop) {
        perror("stdin");
        return EXIT_FAILURE;
    }
    printf("%ld lines, stop=%d, ferror=%d\n", lines, (int) stop, ferror(stdin) != 0);
    return EXIT_SUCCESS;
}
```

輸入兩行後保持管線開著 3 秒，第 1 秒時送 `SIGINT`：

```
$ gcc -std=c11 -O2 intr.c -o intr-c11
$ { printf 'a\nb\n'; sleep 3; } | ./intr-c11 & sleep 1; pkill -INT -f intr-c11; wait
2 lines, stop=2, ferror=1            ← 1 秒時立刻結束
$ gcc -std=gnu11 -O2 intr.c -o intr-gnu11
$ { printf 'a\nb\n'; sleep 3; } | ./intr-gnu11 & sleep 1; pkill -INT -f intr-gnu11; wait
2 lines, stop=2, ferror=0            ← 等到 3 秒後寫入端關閉才結束
```

用 `strace` 看兩者的差別：

```
== -std=c11
rt_sigaction(SIGINT, {sa_handler=0x587f37789290, sa_mask=[], sa_flags=SA_RESTORER|SA_INTERRUPT|SA_NODEFER|SA_RESETHAND|0xffffffff00000000, ...}, ...) = 0
read(0, "a\n", 4096)                    = 2
read(0, 0x587f538262a0, 4096)           = ? ERESTARTSYS (To be restarted if SA_RESTART is set)
--- SIGINT {si_signo=SIGINT, si_code=SI_USER, ...} ---
1 lines, stop=2, ferror=1                ← read 以 EINTR 失敗，getchar 回 EOF 並設錯誤指示
== -std=gnu11
rt_sigaction(SIGINT, {sa_handler=0x58094f498290, sa_mask=[INT], sa_flags=SA_RESTORER|SA_RESTART, ...}, ...) = 0
read(0, "a\n", 4096)                    = 2
read(0, 0x58095e9812a0, 4096)           = ? ERESTARTSYS (To be restarted if SA_RESTART is set)
--- SIGINT {si_signo=SIGINT, si_code=SI_USER, ...} ---
read(0, "", 4096)                       = 0     ← 核心自動重新開始 read，等到真正的 EOF
1 lines, stop=2, ferror=0
```

`-std=c11` 的旗標是 `SA_INTERRUPT|SA_NODEFER|SA_RESETHAND`（System V 語意：不重啟、一次性），`-std=gnu11` 是 `SA_RESTART`（BSD 語意）。這也是 **`getchar` 回傳 `EOF` 時必須檢查 `ferror` 的另一個理由：`EOF` 未必代表輸入結束**。

所以 signal(2) 手冊建議**避免 `signal`，改用 `sigaction` 明確指定 `sa_flags`**：要不要 `SA_RESTART`、handler 執行期間遮蔽哪些 signal、要不要一次性。依 signal(2)，`signal` 唯一可攜的用法是設成 `SIG_DFL` 或 `SIG_IGN`。C11 7.14.1.1 §7 另規定在多執行緒程式中使用 `signal` 是 UB。

### 把 signal 變成主迴圈的事件

全螢幕終端機程式要在視窗大小改變（`SIGWINCH`）時重繪。jserv 的 [mazu-editor](https://github.com/jserv/mazu-editor) 在 handler 裡直接呼叫 `editor_refresh()`——它用 `realloc` 累積輸出、用 `snprintf` 格式化，兩者都不是 async-signal-safe；`SIGWINCH` 若剛好在主迴圈的 `realloc` 中途到達，heap 可能損毀。正確設計是 handler 只設旗標、重繪交給主迴圈，並用**不含 `SA_RESTART`** 的 `sigaction` 讓阻塞中的 `read` 以 `EINTR` 返回、主迴圈才看得到旗標。這仍留一個狹窄的競態：signal 若在「檢查旗標之後、進入 `read` 之前」到達，`read` 不會被打斷。完整解法有三種：

| 方法 | 原理 | 可攜性 |
|---|---|---|
| `pselect`/`ppoll` | 平時遮蔽 signal，只在等待期間原子地解除遮蔽 | POSIX |
| self-pipe | handler 往管線寫一個位元組，主迴圈 `poll` 這條管線 | POSIX |
| `signalfd` | 遮蔽 signal 讓它保持 pending，`signalfd` 的描述子在有 pending signal 時變可讀 | Linux 2.6.22+ |

`signalfd` 是「一切皆檔案描述子」的延伸：signal 和標準輸入一起交給 `poll`，handler、旗標、競態窗口都不存在了。注意一定要先 `sigprocmask` 遮蔽——`SIGWINCH` 預設處置是忽略，沒遮蔽時一到就被丟棄，不會留給 `signalfd`。（原文附有 `sigaction` 版與 `signalfd` 版的完整程式與 `TIOCSWINSZ` 實驗；本章未重跑視窗大小的實驗，**本段為依原文的說明，未實測**。）後面的 QEMU 案例就是這個設計的工業版。

## 處理器例外如何成為 signal

`SIGSEGV`、`SIGFPE` 這類同步 signal 源自處理器的例外（exception）。CS:APP 8.1.2 依起因與返回去向分四類：

| 類別 | 起因 | 同步 | 處理完後 | 例子 |
|---|---|---|---|---|
| 中斷 interrupt | I/O 裝置 | 否 | 回到下一道指令 | 計時器、網卡 |
| trap | 程式刻意引發 | 是 | 回到下一道指令 | `syscall` |
| fault | 可能可修復的錯誤 | 是 | 可能重新執行目前指令 | page fault |
| abort | 無法修復 | 是 | 不返回 | machine check |

x86-64 Linux 上的路徑：

```
  idiv 除數為 0 / INT_MIN÷-1 ──► #DE（例外 0）──► SIGFPE, si_code = FPE_INTDIV
  記憶體存取 ─┬─ 正規位址 ──► #PF（例外 14）─┬─ 合法區域但分頁未就緒 ─► 載入分頁，重新執行同一道指令
             │                              ├─ 不屬於任何區域 ────────► SIGSEGV, SEGV_MAPERR (1)
             │                              └─ 權限不符 ──────────────► SIGSEGV, SEGV_ACCERR (2)
             └─ 非正規位址 ─► #GP（例外 13）────────────────────────────► SIGSEGV, SI_KERNEL (128)
  syscall 指令 ──► trap ──► 回到下一道指令
```

用 `SA_SIGINFO` 的 handler 觀察 `si_code`（handler 裡只用 `write`，自己把數字轉成字串）：

```c
#define _GNU_SOURCE
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void on_segv(int sig, siginfo_t *si, void *ctx)
{
    (void) sig, (void) ctx;
    char buf[96];
    /* handler 裡只用 async-signal-safe 的 write，自己轉數字 */
    int n = 0;
    const char *pre = "si_code=";
    while (*pre) buf[n++] = *pre++;
    int code = si->si_code;
    char tmp[8]; int k = 0;
    do { tmp[k++] = (char) ('0' + code % 10); code /= 10; } while (code);
    while (k) buf[n++] = tmp[--k];
    buf[n++] = '\n';
    write(STDOUT_FILENO, buf, (size_t) n);
    _exit(0);
}

int main(int argc, char **argv)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_segv;
    sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, NULL);
    int mode = argc > 1 ? atoi(argv[1]) : 0;
    if (mode == 0)
        *(volatile int *) (uintptr_t) 16 = 1;                    /* 沒有對映的位址 */
    else if (mode == 1)
        *(volatile unsigned char *) (uintptr_t) main = 0xc3;     /* 寫入唯讀的程式碼區段 */
    else
        *(volatile int *) (uintptr_t) 0x8000000000000000ull = 1; /* 非正規位址 */
    return 1;
}
```

```
$ gcc -O0 segv.c -o segv && ./segv 0 && ./segv 1 && ./segv 2
si_code=1
si_code=2
si_code=128
```

位址 16 不屬於任何區域 → `SEGV_MAPERR`；寫入程式碼區段 → `SEGV_ACCERR`；`0x8000000000000000` 不是正規位址（x86-64 的 48 位元虛擬位址要求高 16 位元是位元 47 的符號擴充）→ `#GP` → `SI_KERNEL`。CS:APP 把前兩種歸給 #GP，實際上它們先觸發的是 page fault，由核心查不到區域或權限不符才送出 `SIGSEGV`；`si_code` 讓我們看出差別。handler 結尾用 `_exit` 而不是返回：從 `SIGSEGV` handler 返回會重新執行同一道指令，又觸發一次。

`SIGFPE` 的名字是 floating-point exception，但 Linux 送出它最常見的原因是**整數**除法（[Ch 34](./34-undefined-behavior.md) 的 `testdiv`、`int8div` 都是）。IEEE 754 的浮點例外預設**不中斷程式**，只設狀態旗標並回傳預設結果（0/0 得 NaN，[Ch 25](./25-ieee754.md)）。要讓浮點錯誤變成 `SIGFPE`，得用 glibc 的 `feenableexcept`（GNU 擴充）開啟 trap：

```c
#define _GNU_SOURCE
#include <fenv.h>
#include <stdio.h>

int main(int argc, char *argv[])
{
    (void) argv;
    volatile double zero = argc - 1;

    printf("%f\n", zero / zero);
    if (feenableexcept(FE_INVALID) == -1) {
        puts("FE_INVALID trap not supported");
        return 1;
    }
    printf("%f\n", zero / zero);
    return 0;
}
```

```
$ gcc -std=c11 -O2 fpe.c -o fpe -lm
$ script -qc ./fpe /dev/null            # 接終端機：-nan 先印出，之後行程被 SIGFPE 終止
-nan
$ ./fpe | cat; echo "exit=${PIPESTATUS[0]}"     # 接管線：連 -nan 都看不到
exit=136
```

第一次 0/0 得到 NaN（x86-64 SSE 產生的預設 NaN 符號位元為 1，glibc 印成 `-nan`），開啟 trap 後第二次觸發 `SIGFPE`，exit 136（128 + 8）。接管線時第一行也看不到：全緩衝的 stdout 還沒寫出，行程就被 signal 終止了。原文在 Ampere eMAG（arm64）上 `feenableexcept` 回傳 -1：AArch64 的浮點 trap 是選配功能，該處理器沒有實作。

## 結束時的緩衝區：資料何時真的寫出去

C11 5.1.2.2.3 §1 規定 `main` 返回等同以回傳值呼叫 `exit`；7.22.4.4 §3–§4 規定 `exit` **先**執行 `atexit` 登記的函式，**再**清空所有串流。整理成一張圖：

```
                              ┌─► 緩衝區滿 ───────────────────────┐
  printf ──► stdio 緩衝區 ────┼─► 行緩衝且遇到換行 ───────────────┤
                              ├─► fflush / fclose ────────────────┼─► write(2)：交給核心
                              ├─► 讀取行緩衝/無緩衝的串流（glibc）┤
                              ├─► exit / main 返回 ───────────────┘
                              ├╌► _exit / glibc 的 _Exit ╌╌╌╌╌╌╌╌╌╌► 消失
                              ├╌► 被 signal 終止 ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌► 消失
                              ├╌► execve 成功 ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌► 消失
                              └┄► fork ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄► 子行程也有一份
```

最容易被忽略的是「`printf` 成功不代表資料寫出去了」：

```c
#include <stdio.h>

int main(int argc, char *argv[])
{
    (void) argv;
    printf("hello\n");
    if (argc > 1 && fclose(stdout) != 0) { /* 結束前確認輸出真的寫出去了 */
        perror("write error");
        return 1;
    }
    return 0;
}
```

```
$ ./hello > /dev/full; echo "exit=$?"
exit=0
$ ./hello check > /dev/full; echo "exit=$?"
write error: No space left on device
exit=1
```

`/dev/full` 的每次寫入都以 `ENOSPC` 失敗。導向它時 stdout 全緩衝，`printf` 只寫進緩衝區並回傳 6（C11 7.21.6.3 §3 說回傳 "the number of characters transmitted"）；資料到 `exit` 清空串流時才寫出、才失敗，但**結束狀態早在呼叫 `exit` 時就決定了**，清空時的錯誤沒有管道改變它，行程以 0 結束。`fclose` 依 7.21.5.1 §3 在偵測到錯誤時回傳 `EOF`，是程式改變結束狀態的最後機會。`make` 與 shell 的 `&&` 只看結束狀態，會把殘缺的輸出當成功。

## 案例：開放原始碼專案怎麼處理這些細節

### coreutils：結束前確認標準輸出

GNU coreutils 的程式在 `main` 開頭以 `atexit(close_stdout)` 登記 gnulib 的 `close_stdout`。它呼叫的 `close_stream` 註解說明動機：

> The last printf can succeed in writing to the internal stream buffer, and yet the fclose(STREAM) could still fail (due e.g., to a disk full error) when it tries to write out that buffered data. Thus, you would be left with an incomplete output file and the offending program would exit successfully.

`close_stdout` 經 `atexit` 登記，在 `exit` 清空串流**之前**執行（7.22.4.4 §3 先於 §4），所以趕得上改變結束狀態。它回報錯誤後呼叫 `_exit` 而非 `exit`，註解的理由是它通常由 `atexit` 呼叫，而 C11 7.22.4.4 §2 規定呼叫 `exit` 超過一次是 UB。

```
$ seq 3 > /dev/full; echo "exit=$?"
seq: write error: No space left on device
exit=1
```

### Git：`xread`/`xwrite` 與結束時的檢查

Git 大多不直接呼叫 `read`/`write`，而是經過 `xread`/`xwrite`：遇到 `EINTR` 重試、遇到 `EAGAIN` 用 `poll` 等就緒、單次傳輸量上限 8 MiB（2013 年的修補：Mac OS X 10.8.4 上一次 `read` 超過 2 GB 會 `EINVAL`）；其上的 `read_in_full`/`write_in_full` 再補齊 short count。結束時 `run_builtin` 若 stdout 不是管線或 socket，依序檢查 `fflush`、`ferror`、`fclose`：

```
$ git --version > /dev/full; echo "exit=$?"
fatal: write failure on standard output: No space left on device
exit=128
```

管線被排除的原因是 Git 啟動時把 `SIGPIPE` 還原成預設處置，註解說 Git 許多部分仰賴 "the upstream of a pipe to die with SIGPIPE"：`git log | head` 中 `head` 提早結束時，Git 直接被 `SIGPIPE` 終止（exit 141），輪不到這項檢查。

### QEMU：借用終端機之後要歸還

`-serial stdio` 讓 QEMU 把 guest 的序列埠接到自己的標準輸入輸出：它把終端機改成近似 raw mode（清除 `ICANON`、`ECHO`，Ctrl-D 原樣交給 guest），並對輸出描述子設定 `O_NONBLOCK`。2024 年的 [Issue 2423](https://gitlab.com/qemu-project/qemu/-/issues/2423) 回報：QEMU 結束後，同一腳本中接著執行的 `seq` 寫到一半就失敗。原因是 **`O_NONBLOCK` 記在 open file description**（前面三張表的中間那張），而管線中 shell 與之後的命令都共用它；QEMU 結束時只還原了描述子 0。用一支只設旗標就結束的小程式重現：

```c
#include <fcntl.h>
#include <stdio.h>

int main(void)
{
    int flags = fcntl(1, F_GETFL);

    /* 把 stdout 設成 O_NONBLOCK，結束時不還原 */
    if (flags == -1 || fcntl(1, F_SETFL, flags | O_NONBLOCK) == -1) {
        perror("fcntl");
        return 1;
    }
    return 0;
}
```

```
$ { ./setnb; seq 1 400000; } | { sleep 1; wc -l; }
seq: write error: Resource temporarily unavailable
12773
$ { seq 1 400000; } | { sleep 1; wc -l; }          # 對照組
400000
```

讀取端先睡 1 秒，管線填滿 Linux 預設的 65,536 位元組後，`seq` 的下一次 `write` 以 `EAGAIN` 失敗；1–12773 連同換行共 65,532 位元組，其餘 4 個位元組是被截斷、沒有換行的 `1277`，`wc -l` 不計。**出錯的是 `seq`，留下旗標的卻是先前已結束的行程。** coreutils 的 `seq` 經 stdio 輸出，glibc 遇到 `EAGAIN` 就設錯誤指示，`seq` 據此回報。QEMU 的修正〈char-stdio: Restore blocking mode of stdout on exit〉改為同時保存並還原描述子 1 的旗標（依原文，QEMU 9.1 起包含，並回移到 8.2.6、9.0.2）。

QEMU 的 signal 處理則是前面 `signalfd` 設計的工業版：主執行緒遮蔽 `SIGIO`、`SIGALRM`、`SIGBUS` 等，交給 `qemu_signalfd` 轉成描述子登記到主迴圈；`sigfd_handler` 讀到一筆就查出登記的 handler 直接呼叫——handler 因此在主迴圈的一般情境中執行，**不受 async-signal-safe 限制**。`SIGINT` 刻意排除，註解說是為了讓 gdb 下的 Ctrl-C 能打斷 QEMU。沒有 `signalfd` 的平台改用一條管線加一個 `sigwait` 執行緒模擬。QEMU 也在啟動時把 stdout 固定為行緩衝（`setvbuf(stdout, NULL, _IOLBF, 0)`），讓以換行結尾的訊息在當機或被 `SIGKILL` 之前就已寫出。

### 經驗

- short count、`EINTR`、`EAGAIN`、傳輸上限各有負責的層次；同一套重試策略**不能**用在自己刻意設定的非阻塞描述子上（Git 2022 年的修補就是為此避免 deadlock）。
- `printf` 成功不保證資料已寫出；寫入成敗要到 `fflush`/`fclose` 才確定。
- `O_NONBLOCK` 與檔案位置記在 open file description，termios 記在終端機裝置，都由多個行程共用：借用的程式要原樣歸還，接收的程式要預期它們可能被改過。
- signal 最好轉成描述子交給主迴圈。

## C 的設計取捨

C99 Rationale 的導論列出 C89 委員會想保存的 "spirit of C"：Trust the programmer、Don't prevent the programmer from doing what needs to be done、Keep the language small and simple、Provide only one way to do an operation、Make it fast, even if it is not guaranteed to be portable。本章的現象都能對應回去：

- **Keep the language small**：C 從 K&R 時代就沒有 I/O 敘述，串流、緩衝、signal 全由函式庫提供，標準只規定能在各種作業系統實作的部分，其餘交給 POSIX。同一個 `signal` 隨 `-std` 得到 BSD 或 System V 語意，選擇發生在 glibc 標頭，語言本身不涉入。
- **Make it fast**：Rationale 舉的例子正是 `char`——"whether the values of char objects widen to signed or unsigned quantities typically depends on which byte operation is more efficient on the target machine"。`getchar` 不另設型別，而以多一個值的 `int` 同時傳資料與檔尾，是 small and simple 的作法；兩者相加，正好在 `char` 上出錯。
- **Trust the programmer**：C 沒有例外機制，檔尾與錯誤以回傳值通知，編譯器不強制檢查。忽略 short count、讀到 `EOF` 不查 `ferror` 的程式都能編譯，也能通過以一般檔案為輸入的測試，要到接上終端機、管線或遇到 signal 才出錯。C23 的 `[[nodiscard]]` 只是 "Recommended practice"，標準函式庫沒有一個函式這樣宣告；實務上的提醒來自 glibc 的 `__wur`（`_FORTIFY_SOURCE` 時展開為 `warn_unused_result`，[Ch 32](./32-tricks-syntax-and-resources.md)）——而它只標了 `read`、`fread`，沒有標 `fwrite`，偏偏輸出錯誤正是 `/dev/full` 實驗中被遺漏的那一種。
- Rationale 5.2.3 承認 signal 難以與系統無關地規範："about the only thing a strictly conforming program can do in a signal handler is to assign a value to a volatile static variable which can be written uninterruptedly and promptly return"；7.14 承認 `SIGFPE`、`SIGILL`、`SIGSEGV` 的名稱源自 PDP-11 硬體術語，"The occurrence of SIGFPE, for instance, does not necessarily indicate a floating-point error"——和本章用整數除法觸發 `SIGFPE` 的實驗相符。

## 對比與取捨

| 需求 | 用 stdio | 用 Unix I/O | 用 `signalfd`/`poll` |
|---|---|---|---|
| 讀寫文字檔、終端機 | 首選 | 要自己處理行與緩衝 | — |
| 二進位檔 | `fread`/`fwrite` 可以；不要用 `fgets`/`scanf` | 可以 | — |
| socket、非阻塞描述子 | 不適合 | 首選（加 short count 迴圈） | 搭配使用 |
| signal handler 內 | 禁止 | `write`、`_exit` 可以 | handler 根本不需要 |
| 等待 signal 與 I/O | — | self-pipe、`pselect` | Linux 上最乾淨 |

## 踩雷集錦

1. **`char c = getchar()`**：x86-64 上 `0xFF` 被當 EOF、arm64 上永不結束；gcc 預設連警告都沒有。永遠用 `int`。
2. **`while (!feof(fp))`**：EOF 指示在讀取**失敗後**才設，迴圈多跑一輪。檢查讀取函式的回傳值。
3. **`fflush(stdin)`**：ISO C 中是 UB；glibc 上不丟任何輸入。用 `getchar` 讀到換行，或 `fgets` + `strtol`。
4. **只用一般檔案測試 `read`/`write`**：檔案幾乎沒有 short count，管線與 socket 才有。用 `(printf abc; sleep 1; printf def) | ./prog` 這種輸入測試。
5. **`fork` 前沒 `fflush`**：輸出接管線時重複印出。`exec` 失敗後的子行程用 `_exit`。
6. **在 handler 裡呼叫 `printf`、`malloc`**：在 heap 或 stdio 內部狀態更新到一半時重入，偶發性地壞掉，極難重現。只設 `volatile sig_atomic_t` 旗標，或用 `write`/`_exit`。
7. **以為 `EOF` 就是輸入結束**：System V 語意的 `signal`（`-std=c11` 下 glibc 的預設）讓 `read` 以 `EINTR` 失敗，`getchar` 回 `EOF` 並設錯誤指示。用 `sigaction` 明確指定 `SA_RESTART` 與否。
8. **`printf` 成功就以為寫出去了**：要在結束前 `fclose(stdout)` 或 `fflush` 並檢查，否則磁碟滿時以 0 結束。

## 進階：再往深一層

- **讀 glibc 的 `libio/fileops.c`**：`_IO_new_file_underflow`（讀取時清空 stdout 的慣例）、`_IO_file_doallocate`（依 `st_blksize` 與 isatty 決定緩衝大小與模式）。對照本章的 `strace` 結果。
- **讀 [The TTY demystified](https://www.linusakesson.net/programming/tty/)**：line discipline、session、job control 如何決定 Ctrl-C、Ctrl-D 與 `SIGWINCH`。
- **`io_uring` 與 short count**：非同步 I/O 介面的完成事件同樣回報實際傳輸量，short count 的觀念不會消失，只是換了地方出現。
- **面試題**：「為什麼 `printf("hello"); fork();` 會印兩次 hello？」好的回答要涵蓋緩衝區在使用者空間、`fork` 複製定址空間、行緩衝與全緩衝的差別、以及 `fflush` 或 `_exit` 的修正。

```c
/* 進階練習起點：寫一個 async-signal-safe 的「印整數」函式，供 handler 使用 */
#include <unistd.h>
static void safe_put_long(long v)
{
    char buf[24];
    int n = sizeof buf;
    unsigned long u = v < 0 ? -(unsigned long) v : (unsigned long) v;   /* 避免 LONG_MIN 取負溢位 */
    do { buf[--n] = (char) ('0' + u % 10); u /= 10; } while (u);
    if (v < 0) buf[--n] = '-';
    write(STDERR_FILENO, buf + n, sizeof buf - n);
}
```

（取負的寫法沿用 [Ch 33](./33-tricks-pointers-and-performance.md) lwan 一節的修正；這段請自行加上 `LONG_MIN` 的測試驗證。）

## 動手練習

1. 把 `eofchar.c` 改成 `unsigned char c`，在 x86-64 上跑 `printf 'ab\377cd'`，預測並驗證結果。
2. 用 `strace -f` 跑 `forkbuf` 接管線的版本，數出 `write(1, ...)` 出現幾次、每次寫幾個位元組。
3. 把 `intr.c` 改成用 `sigaction`，分別設與不設 `SA_RESTART`，確認兩者的行為不再隨 `-std` 改變。
4. 寫一個程式：用 `signalfd` 等 `SIGINT`、用 `poll` 同時等標準輸入，按 Ctrl-C 時印出「got SIGINT」並繼續，輸入 `q` 時結束。在 pty 中測試。

## 本章重點整理

- 檔案描述子統一一個作業系統內的各種裝置，C 串流統一各種作業系統之間的 I/O 語意；`FILE` 多出緩衝區、錯誤指示與 end-of-file 指示。
- `getchar` 要表示 257 種結果所以回傳 `int`；`char` 的有號與否讓錯誤在不同架構上表現相反。
- EOF 是黏著的狀態，由 `read` 回傳 0 觸發；終端機上的 Ctrl-D 只是讓 `read` 立即返回，在行首時才成為 EOF。
- stdout 接終端機時行緩衝、接管線或檔案時全緩衝；緩衝區在使用者空間，會被 `fork` 複製、隨 signal 或 `_exit` 消失；`printf` 成功不代表寫出。
- signal handler 只能做極少的事（`volatile sig_atomic_t`、async-signal-safe 函式）；`signal` 的語意隨 feature test macro 改變，應改用 `sigaction`；最好把 signal 轉成描述子交給主迴圈。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼 `char c = getchar()` 在 x86-64 與 arm64 上錯法相反
- [ ] 能說出「在終端機輸入 `5 6 7 8` 後要按兩次 Ctrl-D」的原因，以及第二次 `getchar` 為什麼不再呼叫 `read`
- [ ] 如果有人問「為什麼我的程式輸出到檔案時少了最後幾行」，你會問他哪三個問題？
- [ ] 能畫出 `fork` 後親代與子行程的描述子、open file description、inode 三層關係，並說出哪些狀態共享、哪些各自一份
- [ ] 能解釋同一份 `intr.c` 為什麼 `-std=c11` 與 `-std=gnu11` 行為不同（追到 `__STRICT_ANSI__` 與 `sa_flags`）
- [ ] 想一個你寫過的網路程式或 CLI 工具，它有沒有處理 short count、`EINTR`、結束時的 `fclose` 錯誤？

## 延伸閱讀

### 書籍

- **《Computer Systems: A Programmer's Perspective》（3rd ed.）** — Bryant, O'Hallaron（Pearson，2015）
  - **讀哪幾章**：第 8 章 8.1（例外分類）、8.5（signal 與 handler 準則）；第 10 章（Unix I/O、RIO、三張表、何時用 stdio）
  - **和本章的關聯**：本章的例外分類表、handler 準則 G0–G5、short count 與三張表都出自這兩章
- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch，2010）
  - **讀哪幾章**：第 13 章（File I/O Buffering）、第 20–22 章（Signals）、第 62 章（Terminals）
  - **這本書的定位**：比 CS:APP 更貼近 Linux 實作；出版超過 10 年，但這些章節的 API 至今穩定，作者的 man-pages 持續更新

### 官方文件 / Spec

- **[N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**：7.21.2（串流、text/binary）、7.21.3（緩衝模式）、7.21.7.1（`fgetc` 與 EOF 指示）、7.14.1.1（signal handler 的限制）、7.22.4.4（`exit` 的順序）
- **[signal-safety(7)](https://manpages.debian.org/trixie/manpages/signal-safety.7.en.html)、[signal(7)](https://manpages.debian.org/trixie/manpages/signal.7.en.html)、[signal(2)](https://manpages.debian.org/trixie/manpages-dev/signal.2.en.html)**
  - **讀哪裡**：signal-safety(7) 的函式清單；signal(7) 的 "Interruption of system calls and library functions by signal handlers"（哪些系統呼叫受 `SA_RESTART` 影響）；signal(2) 的 "Portability" 一節
- **[C99 Rationale](https://www.open-std.org/jtc1/sc22/wg14/www/C99RationaleV5.10.pdf)**
  - **讀哪裡**：Introduction 的 "spirit of C"、5.2.3（signal）、7.19（為何不收 `open`/`read`）
  - **為什麼值得讀**：標準條款說「是什麼」，Rationale 說「為什麼」

### 論文

- **[FlexSC: Flexible System Call Scheduling with Exception-Less System Calls](https://www.usenix.org/conference/osdi10/flexsc-flexible-system-call-scheduling-exception-less-system-calls)** — Livio Soares, Michael Stumm，OSDI（2010）
  - **核心貢獻**：量化系統呼叫的直接成本（進出核心約 150 週期）與間接成本（快取、TLB 污染讓 IPC 下降），提出批次化的 exception-less 系統呼叫
  - **讀哪裡**：Section 2（量測系統呼叫成本）；Section 3 之後的設計可選讀
  - **和本章的關聯**：解釋「緩衝的動機」實驗中，逐位元組 `read` 為什麼慢那麼多

### 部落格 / 技術文章

- **[The TTY demystified](https://www.linusakesson.net/programming/tty/)** — Linus Åkesson
  - **這篇說什麼**：終端機在核心中的完整結構，line discipline、session、foreground process group
  - **前提**：本章「誰決定 end-of-file」一節
- **[Tearing apart printf()](https://www.maizure.org/projects/printf/)** — MaiZure
  - **這篇說什麼**：從 `printf` 一路追到 glibc 的緩衝區與 `write` 系統呼叫
  - **讀哪裡**：buffering 與 vfprintf 的部分

> 和 `programming/c_interview/` 的關係：signal 與 setjmp 的面試題見 [../c_interview/23-signal-setjmp.md](../c_interview/23-signal-setjmp.md)，`printf` 與可變參數見 [../c_interview/17-variadic-printf.md](../c_interview/17-variadic-printf.md)。

Part 5 到此結束：巨集、物件模型、`goto`、技巧、未定義行為與 I/O 都學過了。接下來的練習把它們組合起來，寫一個迷你 unit test 框架——巨集、cleanup、`fork` 與緩衝、signal 這幾章的知識都會派上用場（例如測試在子行程中崩潰時，父行程要怎麼得知、緩衝區裡的輸出會不會重複或遺失）。

→ [練習 D：迷你 unit test 框架](./practice-d-unit-test-framework.md)
