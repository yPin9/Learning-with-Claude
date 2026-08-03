# Ch 14 — trace 執行：strace / ltrace / 自寫 tracer

> **目標**：學會用 trace 這一支柱——不停下來、只側錄程式的每一次對外互動。`strace` 錄它跟作業系統要什麼（開哪個檔、讀寫什麼、連哪個位址），`ltrace` 錄它呼叫哪些函式庫函式（`strcmp` 比了什麼、`fopen` 開了什麼）。很多時候一行 asm 都不用讀，光看 trace 就知道 binary 在幹嘛。最後用 ptrace 手寫一個 30 行的極簡 tracer，看穿這一切的底層。

> **環境**：WSL2 / Linux x86-64，gcc + strace + ltrace。本章 strace/ltrace 輸出全是真跑貼上的。

Ch 13 的 gdb 是「停下來精細地看」。這章是另一種姿勢：**不停下來，只在旁邊側錄**。程式從頭跑到尾，你事後拿到一份完整的「它跟外界互動」的清單。斷點是顯微鏡，trace 是行車紀錄器——各有各的場合。

## 為什麼需要這個？

逆向一個陌生 binary，最貴的成本是「不知道從哪看起」。一個幾 MB 的 strip binary，你總不能從第一條指令讀到最後一條。你需要一個**快速定位**的手段，先搞清楚「這東西大概在幹嘛、跟外界要了什麼」，再決定該深挖哪裡。

trace 就是那個手段，而且便宜到不可思議——一行指令、零 setup：

```bash
$ strace ./mystery_binary     # 它跟 OS 要了什麼？
$ ltrace ./mystery_binary     # 它呼叫了哪些庫函式？
```

一個程式不管內部多複雜、混淆多嚴重，只要它想做**有意義的事**——讀檔、寫檔、連網、比對字串——就得穿過作業系統和函式庫這兩道邊界。這兩道邊界是它藏不住的地方。trace 就守在這兩道邊界上，把穿過去的每一次呼叫都記下來。**內部可以混淆，但對外的 syscall/libcall 騙不了人。**

## 先建立直覺：兩道無法隱藏的邊界

一個程式和外界的互動，一定穿過兩層邊界。trace 的兩個工具剛好各守一層：

```
        你的程式（可能被混淆、strip、加密）
              │                    │
              │ 呼叫 libc 函式      │ 直接發 syscall
              ▼                    │
   ┌──────────────────────┐        │
   │  函式庫 (libc, ...)   │◄── ltrace 守這層
   │  strcmp/fopen/malloc  │        │
   └──────────┬───────────┘        │
              │ 底層也發 syscall     │
              ▼                    ▼
   ═══════════════════════════════════════  ← 使用者/核心邊界
   ┌──────────────────────────────────────┐
   │  作業系統核心 (kernel)                 │◄── strace 守這層
   │  openat/read/write/connect/execve     │
   └──────────────────────────────────────┘
```

- **ltrace** 攔的是「程式 → 函式庫」的呼叫。你看到 `strcmp("input", "secret")`、`fopen("/etc/passwd", "r")`——連參數都看得到，因為它攔在呼叫發生的當下。
- **strace** 攔的是「程式 → kernel」的系統呼叫。你看到 `openat(..., "/tmp/license.key", O_RDONLY)`、`read(3, "BADKEY\n", 4096)`、`connect(...)`——所有真正動到外界的動作最終都變成 syscall，逃不掉。

關鍵洞察：**ltrace 資訊更豐富（連字串參數都給），但只在程式動態連結 libc 時有效**；**strace 更底層、更難逃避**（連 static 連結、直接發 syscall 的程式都攔得到），但看到的是原始 syscall。逆向常先 strace 看大局，再 ltrace 挖細節。

## strace：看它跟作業系統要什麼

我們用一個 ground-truth 目標貫穿——一個讀 license 檔、比對 key 的程式。先寫 source（標準答案，逆的時候蓋起來）：

```c
// licrd.c
#include <stdio.h>
#include <string.h>
int main(){
    FILE *f = fopen("/tmp/re/license.key","r");
    if(!f){ printf("no license\n"); return 1; }
    char buf[64];
    if(!fgets(buf,sizeof buf,f)){ fclose(f); return 1; }
    fclose(f);
    buf[strcspn(buf,"\n")]=0;
    if(strcmp(buf,"S3CR3T-KEY")==0){ printf("licensed\n"); return 0; }
    printf("invalid license\n"); return 1;
}
```

```bash
$ gcc -O0 -o licrd licrd.c
$ echo "BADKEY" > license.key       # 先放一把錯的 key
```

假裝你沒看過 source，只有 `licrd`。strace 它（過濾掉 loader/libc 載入那堆雜訊，只留檔案和寫入相關的 syscall），真跑：

```bash
$ strace -e trace=openat,read,write,close ./licrd
...
openat(AT_FDCWD, "/tmp/re/license.key", O_RDONLY) = 3
read(3, "BADKEY\n", 4096)               = 7
close(3)                                = 0
write(1, "invalid license\n", 16invalid license
)       = 16
+++ exited with 1 +++
```

**光這幾行你就逆出這程式的行為了，一行 asm 都沒讀**：

- 它 `openat` 開了 `/tmp/re/license.key`（拿到 fd 3）——原來它要讀這個檔。
- `read(3, "BADKEY\n", 4096) = 7`——讀進了 7 個 byte，內容 `"BADKEY\n"`（就是我們放的錯 key）。
- `write(1, "invalid license\n", 16)`——往 stdout（fd 1）印了 `invalid license`。

你現在知道：**這程式讀 `/tmp/re/license.key` 判斷授權**。下一步該做什麼一目了然——改那個檔的內容看看。這就是 trace 的威力：它把「該看哪裡」直接指給你。

### strace -c：先看全局統計

想快速知道「這程式大概在做哪類事」，`strace -c` 給 syscall 統計摘要（真跑）：

```bash
$ strace -c ./licrd
% time     seconds  usecs/call     calls    errors syscall
------ ----------- ----------- --------- --------- ----------------
 33.00    0.000033          33         1           write
 25.00    0.000025          12         2           read
 23.00    0.000023           5         4           newfstatat
 19.00    0.000019           6         3           close
  0.00    0.000000           0         8           mmap
  ...
  0.00    0.000000           0         3           openat
  ...
------ ----------- ----------- --------- --------- ----------------
100.00    0.000100           2        42         2 total
```

一眼看出：有 `openat`/`read`/`write`（碰檔案和輸出）、沒有 `connect`/`socket`（不連網）。42 個 syscall、2 個出錯。這是逆向一個未知樣本的第一個「這是什麼東西」的快照。

### strace 的常用旗標（逆向向）

| 旗標 | 用途 |
|---|---|
| `-e trace=openat,read,...` | 只看指定 syscall（過濾雜訊，最常用） |
| `-e trace=file` / `network` / `process` | 按類別過濾（檔案/網路/行程操作） |
| `-c` | syscall 統計摘要，看大局 |
| `-f` | 跟蹤 fork 出的子行程（多行程程式必加） |
| `-s 200` | 把字串參數印長一點（預設只印前 32 字元，key 可能被截斷） |
| `-p PID` | attach 到已在跑的行程 |
| `-o out.txt` | 存檔（trace 很長，存下來慢慢看） |

> **一個真實踩雷**：strace 預設把字串參數截到 32 字元，一把長 key 可能被 `...` 截掉看不全。逆向讀 key/密碼時記得加 `-s 200`。

## ltrace：看它呼叫哪些函式庫函式

strace 告訴我們它讀了 `/tmp/re/license.key`，但**它拿讀到的內容跟什麼比？** strace 看不到——`strcmp` 是 libc 函式，不是 syscall，穿不過使用者/核心邊界。這就是 ltrace 上場的地方。真跑：

```bash
$ ltrace ./licrd
fopen("/tmp/re/license.key", "r")                = 0x5631592012a0
fgets("BADKEY\n", 64, 0x5631592012a0)            = 0x7fffc5570cb0
fclose(0x5631592012a0)                           = 0
strcspn("BADKEY\n", "\n")                        = 6
strcmp("BADKEY", "S3CR3T-KEY")                   = -17
puts("invalid license")                          = 16
invalid license
+++ exited (status 1) +++
```

**看那一行 `strcmp`**：

```
strcmp("BADKEY", "S3CR3T-KEY")  = -17
```

它拿我們檔案裡的 `"BADKEY"`，跟一個寫死在程式裡的字串 **`"S3CR3T-KEY"`** 比。ltrace 把兩個參數都攤在你眼前——**正確的 license key 就是 `S3CR3T-KEY`，我們一行 asm 都沒讀、一個斷點都沒下。** 這是 Ch 12 心法「觀察勝於推理」最漂亮的示範：不推理、不手算，binary 在函式邊界自己招了。

驗證：

```bash
$ echo "S3CR3T-KEY" > license.key
$ ./licrd
licensed                          ← 逆對了
```

### ltrace 的限制（很重要）

ltrace 只能攔**動態連結、經過 PLT** 的函式庫呼叫。三種情況它會失效：

1. **static 連結的 binary**：libc 被靜態塞進 binary，沒有動態呼叫邊界，ltrace 攔不到（改用 strace 或 gdb）。
2. **inline 的邏輯**：比對如果不是呼叫 `strcmp`，而是編譯器把它 inline 成一段內嵌的迴圈/指令，就沒有 libcall 可攔——ltrace 看不到，得靠 gdb/靜態。**練習 B 的目標就故意這樣**：它的核心 hash 比對是自己寫的內嵌迴圈，ltrace 只看得到 `strlen`，看不到那個關鍵比對。
3. **static 函式 / 自寫函式**：ltrace 攔的是**函式庫**函式，程式內部自己定義的 `check()`、`transform()` 這種它攔不到（那是 gdb 斷點的活）。

這個限制本身是逆向資訊：**ltrace 一片安靜、只有零星幾個 libc 呼叫，通常意味著關鍵邏輯被 inline 或程式是 static 連結的**——該切回 gdb/靜態了。

## strace vs ltrace：完整對比

| 面向 | strace | ltrace |
|---|---|---|
| 攔什麼 | 系統呼叫（程式↔kernel） | 函式庫呼叫（程式↔libc 等） |
| 底層機制 | ptrace（`PTRACE_SYSCALL`） | 攔 PLT / 動態符號解析 |
| 看得到的資訊 | syscall 號、參數、回傳 | 函式名、**字串參數**、回傳 |
| 對 static binary | 有效（syscall 逃不掉） | **失效**（無動態呼叫邊界） |
| 對 inline 邏輯 | 看不到（不是 syscall） | 看不到（沒有 libcall） |
| 逆向典型用途 | 開了哪個檔、連哪個網路、寫了什麼 | 比對了什麼字串、malloc 多大、算了什麼 |
| 逆向定位價值 | 看大局「它在跟外界做什麼」 | 挖細節「它拿你的輸入跟什麼比」 |

實務上兩個一起用：**strace 找出它碰的資源（檔案/網路），ltrace 找出它的字串比對/邏輯線索**，兩份 trace 湊起來，往往不用讀 asm 就能重建大半行為。

## 底層機制：自寫一個 30 行的極簡 tracer

strace 不是魔法。它底層就是 Ch 12 講的 ptrace（你的 [`gdb`](../gdb/README.md) 課 Ch 2 深講過）。核心招式是 **`PTRACE_SYSCALL`**：讓 tracee 一路跑，但每次**進入或離開一個 syscall** 就停下來、把控制權交回 tracer。tracer 這時 `PTRACE_GETREGS` 讀暫存器——`orig_rax` 就是 syscall 號。

自己寫一個看看（真跑）：

```c
// minitrace.c — 極簡 syscall tracer，strace 的骨架
#include <stdio.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/user.h>
#include <unistd.h>
int main(int argc,char**argv){
    pid_t child=fork();
    if(child==0){
        ptrace(PTRACE_TRACEME,0,0,0);   // 「我自願被追蹤」
        execvp(argv[1],&argv[1]);        // 換成要 trace 的程式
        return 1;
    }
    int status;
    waitpid(child,&status,0);            // 等 child 停在 execve 後
    while(1){
        ptrace(PTRACE_SYSCALL,child,0,0);// 跑到下一個 syscall 進/出就停
        waitpid(child,&status,0);
        if(WIFEXITED(status)) break;
        struct user_regs_struct r;
        ptrace(PTRACE_GETREGS,child,0,&r);
        fprintf(stderr,"syscall #%llu\n",(unsigned long long)r.orig_rax);
        ptrace(PTRACE_SYSCALL,child,0,0);// 跑過 syscall 的「離開」那一停
        waitpid(child,&status,0);
        if(WIFEXITED(status)) break;
    }
    return 0;
}
```

```bash
$ gcc -O0 -o minitrace minitrace.c
$ ./minitrace /bin/echo hi
syscall #12      ← brk
syscall #158     ← arch_prctl
syscall #9       ← mmap
syscall #21      ← access
syscall #257     ← openat
syscall #262     ← newfstatat
syscall #9       ← mmap
syscall #3       ← close
syscall #257     ← openat
syscall #0       ← read
syscall #17      ← pread64
...
```

30 行，你就有了 strace 的骨架。它印的是 syscall 號（`12`=brk、`9`=mmap、`257`=openat、`0`=read、`1`=write……對照 `/usr/include/asm/unistd_64.h` 或 `ausyscall`）。真正的 strace 多做的事：把號碼翻成名字、解碼參數（讀 `rdi/rsi/rdx` 再從 tracee 記憶體把字串抓出來）、處理 fork、格式化。但核心就是這個 `PTRACE_SYSCALL` 迴圈。

看懂這個，你就懂了三件事同源：**strace 用 `PTRACE_SYSCALL`、gdb 斷點用 `int3` + `PTRACE_CONT`、改記憶體用 `POKEDATA`——全是同一個 ptrace 介面的不同 request。** 動態逆向的三支柱底下是同一塊地基。

## 踩雷集錦

1. **strace/ltrace 輸出被雜訊淹沒**：程式啟動時 loader 會 `mmap`/`openat` 一堆 `.so`，前 20 行常常都是這些。用 `-e trace=...` 過濾成你關心的類別（`file`/`network`），或先看你程式**自己**的檔案/網路操作，別被啟動雜訊帶偏。
2. **字串被截斷看不全 key**：strace 預設截到 32 字元。逆 key/密碼一定加 `-s 200`（或更大）。ltrace 也有 `-s`。
3. **ltrace 一片空白就以為程式沒做事**：更可能是 **static 連結**或**關鍵邏輯被 inline**——沒有 libcall 可攔（練習 B 就是這樣）。這不是「沒事」，是「該換工具」的訊號，切回 strace/gdb/靜態。
4. **忘了 `-f`，子行程的動作全漏了**：程式 fork/exec 子行程時，不加 `-f` 只 trace 父行程，子行程做的事（可能才是重點）全看不到。多行程程式一律 `-f`。
5. **在受保護環境 strace 不到**：現代發行版可能限制 ptrace（`yama ptrace_scope`），attach 別人的行程會被拒。自己啟動的（`strace ./prog`）通常沒問題；attach（`-p`）可能要權限或調 `/proc/sys/kernel/yama/ptrace_scope`。
6. **反調試偵測到被 trace 就變臉**：惡意樣本可能檢查 `/proc/self/status` 的 `TracerPid` 或自己 `ptrace(TRACEME)` 佔位來偵測 strace/ltrace/gdb（Ch 23）。看到程式在被 trace 時行為異常，先懷疑 anti-trace。

## 進階：再往深一層

- **eBPF 系的現代 tracer**：`strace` 每個 syscall 都要停 tracee 兩次（進/出），對高頻 syscall 的程式**拖慢很多**（可能 10 倍以上），還可能改變時序讓 race/反調試行為不同。`bpftrace`、`perf trace`、`bpf_trace` 用 eBPF 在 kernel 裡側錄，開銷低得多、不用停行程——你的 `bpf` 課專門講。逆向對效能敏感或會偵測 ptrace 的目標時，eBPF trace 是更隱蔽的選擇。
- **trace 系統呼叫的參數解碼**：真正的 strace 會把 `openat` 的 flag（`O_RDONLY`）、`mmap` 的 prot（`PROT_READ|PROT_EXEC`）解成人讀得懂的常數——這需要一張 syscall 參數 schema。想擴充你的 minitrace，下一步就是讀 `rdi/rsi/rdx` 並用 `PTRACE_PEEKDATA` 把指標指向的字串從 tracee 記憶體抓出來。
- **library call 攔截的另一招——`LD_PRELOAD`**：不用 ltrace，也能自己寫一個 `.so` 用 `LD_PRELOAD` 覆蓋 `strcmp`/`fopen`，在裡面 log 參數再呼叫真正的函式。這是 ltrace 之外的攔截術，也是很多 anti-cheat/hook 框架的原理，Ch 15 的 DBI 是它的重量級版本。

## 本章重點整理

- trace 是可觀察性第二支柱：**不停下來、只側錄程式的每一次對外互動**。守在兩道程式藏不住的邊界上——內部可混淆，對外的 syscall/libcall 騙不了人。
- **strace** 攔系統呼叫（程式↔kernel）：看它開哪個檔、讀寫什麼、連哪個網路。對 static binary 也有效（syscall 逃不掉）。
- **ltrace** 攔函式庫呼叫（程式↔libc）：看它 `strcmp` 比了什麼、`fopen` 開了什麼——連字串參數都給。但只對動態連結、非 inline 的 libcall 有效。
- 很多時候光看 trace 就逆出行為：strace 找出它碰的資源、ltrace 找出它的字串比對，不用讀一行 asm。
- 底層是 ptrace 的 `PTRACE_SYSCALL`——30 行就能手寫 strace 骨架。strace/gdb 斷點/改記憶體全是同一個 ptrace 介面的不同 request。

## 自我檢核

- [ ] 我能說出 strace 和 ltrace 各攔哪一道邊界、各適合看什麼
- [ ] 我能用 strace 找出一個程式讀了哪個檔、用 ltrace 找出它拿輸入跟什麼字串比
- [ ] 我知道 ltrace 對 static 連結或 inline 邏輯會失效，以及「ltrace 一片空白」代表什麼
- [ ] 我記得逆 key/密碼要加 `-s 200`、多行程要加 `-f`
- [ ] 我能解釋 `PTRACE_SYSCALL` 怎麼讓 tracer 攔到每個 syscall，並知道它和 gdb 斷點同源

## 延伸閱讀

### 官方文件 / 工具

- **[strace(1)](https://man7.org/linux/man-pages/man1/strace.1.html) 與 [ltrace(1)](https://man7.org/linux/man-pages/man1/ltrace.1.html) man page**
  - **讀哪裡**：strace 的 `-e trace=` 表達式語法、`-c`/`-f`/`-s`；ltrace 的 `-e`/`-l`（只看某 library）/`-S`（連 syscall 一起）
- **[ptrace(2) man page](https://man7.org/linux/man-pages/man2/ptrace.2.html)**
  - **讀哪裡**：`PTRACE_SYSCALL` 與 `PTRACE_GETREGS` 段落——本章 minitrace 的一手依據
- **你自己的 [`gdb`](../gdb/README.md) 課 Ch 2、[`bpf`](../bpf/README.md) 課**
  - **這是什麼**：gdb Ch 2 深講 ptrace（minitrace 的完整版）；bpf 課講 eBPF 系的低開銷 tracer（strace 的現代替代）

### 書籍

- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **讀哪幾章**：Ch 6（動態分析工具，含 strace/ltrace 在逆向流程的定位）
- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch, 2010）
  - **讀哪裡**：Ch 44–45（ptrace 概念）與 syscall 機制章——想把 minitrace 擴成完整 tracer 的權威參考

trace 讓 binary 在邊界上招供，但它只能「觀察」既有的呼叫。下一支柱更猛：在 binary 執行時**注入你自己的 code**——hook 任意函式看參數、甚至改掉它的行為。

→ [Ch 15 動態插樁（DBI）：Frida / Pin / DynamoRIO](./15-dynamic-instrumentation-dbi.md)
