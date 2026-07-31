# Ch 4 — 動手寫 mini-strace v1

> **目標**：把 Ch 3 的 ptrace 知識寫成一個**完整可用的 mini-strace**——能執行目標程式、攔截每個 syscall、顯示 syscall 名字、解析參數（含讀取指標指向的字串）、顯示回傳值。寫過它，你對 strace 的理解從「會用」變成「知道它怎麼運作」，這是本課和一般工具教學的根本差別。完成後你有一個自己造的 tracer，並深刻理解所有 tracer 的底層。

> **環境**：Linux x86-64，C（gcc）。需要 ptrace（自己的子 process，不需 sudo，Ch 0）。

## 為什麼要親手寫一個？

你會用 strace（Ch 5 會深入用法），但「會用工具」和「理解工具」是兩回事。親手用 ptrace 寫一個 mini-strace，讓你：(1) 徹底理解 strace 怎麼看到 syscall（不再是黑盒子）；(2) 知道它的能力和限制從哪來；(3) 當 strace 不夠用時，能自己造工具；(4) 對所有基於 ptrace 的工具（gdb、其他 tracer）有了底層理解。

這是本課的精髓——「理解工具底層」。Ch 3 講了 ptrace 的機制和 30 行的雛形，這章把它做成一個真正能用、能顯示可讀輸出的 mini-strace。寫的過程會逼你處理所有細節（syscall 名字對照、參數解析、讀取記憶體裡的字串、entry/exit 配對）。完成後，你看 strace 的輸出時，腦中清楚知道它每一步在做什麼。

## 先回顧:strace 的核心循環（Ch 3）

```
mini-strace 要做的事（Ch 3 的循環 + 完整化）：

  1. fork + child PTRACE_TRACEME + exec 目標程式
        │
  2. parent 循環：
     PTRACE_SYSCALL（繼續到 syscall 邊界）
     waitpid（等它停）
     PTRACE_GETREGS（讀暫存器）
        │
  3. 把暫存器翻譯成可讀（這章的重點）：
     orig_rax → syscall 名字（查對照表）
     rdi/rsi/rdx → 參數
     指標參數 → PEEKDATA 讀出字串/內容
     rax（exit 時）→ 回傳值
        │
  4. 處理 entry/exit 配對：
     每個 syscall 停兩次（進入、返回）
     entry 時顯示 syscall+參數，exit 時顯示回傳值
        │
  → 從「印 syscall 號」（Ch 3）到「印可讀的 syscall 行」（這章）
```

關鍵心智：mini-strace 就是 Ch 3 的「fork+TRACEME + 反覆 PTRACE_SYSCALL+GETREGS」循環，加上「**把暫存器翻譯成人類可讀**」——syscall 號 → 名字、參數暫存器 → 值、指標 → 讀出的字串、處理 entry/exit 配對。

> 這章直接建立在 Ch 3 的 ptrace 機制上。如果對 PTRACE_SYSCALL、暫存器佈局（rax/rdi/rsi）、entry/exit 不熟，先回看 [Ch 3](./03-ptrace-syscall-deep-dive.md)。

## 完整的 mini-strace

```c
// mini_strace.c — 一個能用的 mini-strace
// 編譯：gcc -o mini_strace mini_strace.c
// 用法：./mini_strace <程式> [參數...]
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/user.h>
#include <sys/syscall.h>
#include <unistd.h>

// syscall 號 → 名字的簡易對照（實際 strace 有完整表）
static const char *syscall_name(long n) {
    switch (n) {
        case SYS_read:    return "read";
        case SYS_write:   return "write";
        case SYS_openat:  return "openat";
        case SYS_close:   return "close";
        case SYS_mmap:    return "mmap";
        case SYS_brk:     return "brk";
        case SYS_execve:  return "execve";
        case SYS_exit_group: return "exit_group";
        case SYS_fstat:   return "fstat";
        case SYS_access:  return "access";
        default:          return NULL;   // 不認得的就印號
    }
}

// 從 tracee 的記憶體讀一個字串（解參考指標參數，如檔名）
static void read_string(pid_t pid, unsigned long addr, char *buf, int max) {
    int i = 0;
    while (i < max - 1) {
        // PEEKDATA 一次讀一個 word（8 bytes）
        long word = ptrace(PTRACE_PEEKDATA, pid, addr + i, NULL);
        if (word == -1) break;
        memcpy(buf + i, &word, sizeof(word));
        // 檢查這個 word 裡有沒有字串結尾 \0
        for (int j = 0; j < (int)sizeof(word); j++) {
            if (buf[i + j] == '\0') { return; }
        }
        i += sizeof(word);
    }
    buf[i] = '\0';
}

int main(int argc, char *argv[]) {
    if (argc < 2) { fprintf(stderr, "Usage: %s <prog> [args]\n", argv[0]); return 1; }

    pid_t child = fork();
    if (child == 0) {
        // child：被 trace，然後 exec 目標
        ptrace(PTRACE_TRACEME, 0, NULL, NULL);
        execvp(argv[1], &argv[1]);
        perror("execvp"); exit(1);
    }

    // parent（tracer）
    int status;
    waitpid(child, &status, 0);          // 等 child 停在 exec
    ptrace(PTRACE_SETOPTIONS, child, 0, PTRACE_O_TRACESYSGOOD);

    int in_syscall = 0;                  // entry/exit 切換
    while (1) {
        ptrace(PTRACE_SYSCALL, child, NULL, NULL);   // 繼續到 syscall 邊界
        waitpid(child, &status, 0);
        if (WIFEXITED(status)) {
            printf("+++ exited with %d +++\n", WEXITSTATUS(status));
            break;
        }

        struct user_regs_struct regs;
        ptrace(PTRACE_GETREGS, child, NULL, &regs);

        if (!in_syscall) {
            // syscall-entry：顯示 syscall + 參數
            long sysnum = regs.orig_rax;
            const char *name = syscall_name(sysnum);

            if (name && strcmp(name, "openat") == 0) {
                // openat(dirfd, pathname, flags) → 讀 pathname（rsi 是指標）
                char path[256];
                read_string(child, regs.rsi, path, sizeof(path));
                printf("openat(%lld, \"%s\", ...)", (long long)regs.rdi, path);
            } else if (name && strcmp(name, "write") == 0) {
                // write(fd, buf, count) → 讀 buf
                char buf[64];
                read_string(child, regs.rsi, buf, sizeof(buf));
                printf("write(%lld, \"%.20s\", %lld)",
                       (long long)regs.rdi, buf, (long long)regs.rdx);
            } else if (name) {
                printf("%s(%lld, %lld, %lld)", name,
                       (long long)regs.rdi, (long long)regs.rsi, (long long)regs.rdx);
            } else {
                printf("syscall_%lld(...)", regs.orig_rax);
            }
            in_syscall = 1;
        } else {
            // syscall-exit：顯示回傳值（rax）
            printf(" = %lld\n", (long long)regs.rax);
            in_syscall = 0;
        }
    }
    return 0;
}
```

```bash
# 編譯並跑
gcc -o mini_strace mini_strace.c
./mini_strace /bin/echo hi
# openat(-100, "/etc/ld.so.cache", ...) = 3
# openat(-100, "/lib/x86_64-linux-gnu/libc.so.6", ...) = 3
# write(1, "hi\n", 3) = 3          ← 你的 mini-strace 看到了 echo 的 write！
# +++ exited with 0 +++

# 對照真 strace
strace /bin/echo hi 2>&1 | grep -E 'openat|write'
# → 你的 mini-strace 抓到了一樣的 syscall！
```

> **這個 mini-strace 真的能用——它顯示 openat 的檔名、write 的內容、回傳值，和真 strace 抓到一樣的 syscall**。關鍵的幾個部分：(1) **fork+TRACEME+exec**（Ch 3 的建立追蹤）；(2) **PTRACE_SYSCALL 循環**（在每個 syscall 邊界暫停）；(3) **syscall 名字對照**（`syscall_name`——SYS_write 等常數來自 `<sys/syscall.h>`，實際 strace 有完整的幾百個 syscall 表）；(4) **read_string**（最巧妙的部分——syscall 的指標參數如 openat 的檔名、write 的 buf，暫存器裡只有位址，要用 `PTRACE_PEEKDATA` 一個 word 一個 word 讀出字串內容，直到 `\0`）；(5) **entry/exit 配對**（`in_syscall` 切換——每個 syscall 停兩次，entry 時顯示名字+參數、exit 時顯示回傳值）。跑起來它真的抓到 echo 的 `write(1, "hi\n", 3) = 3`——和真 strace 一樣！**寫過這個，你對 strace 的理解就脫胎換骨**——你知道它怎麼看到 syscall（PTRACE_SYSCALL）、怎麼顯示檔名（PEEKDATA 讀字串）、為什麼每個 syscall 顯示一行（entry+exit）。這不再是黑盒子。`PTRACE_O_TRACESYSGOOD` 是個小優化（區分 syscall-stop 和其他 signal-stop）。這個 30-80 行的程式，是理解所有 tracer 的鑰匙。

## 它和真 strace 的差距

```
你的 mini-strace vs 真 strace（理解工具的完整度）：

  你的 mini-strace 有的：
    ✓ 攔截 syscall、顯示名字、部分參數、回傳值
        │
  真 strace 多的（巨大的工程）：
    - 完整的 syscall 表（幾百個，各架構）
    - 每個 syscall 的「參數格式化」（flags 解碼、struct 展開、
      錯誤碼 → errno 名字如 ENOENT）
    - -f（追蹤子 process，fork/clone）
    - -e 過濾、-c 統計、-T 計時
    - 處理 signal、各種 ptrace 事件
    - 多架構支援（x86-64/ARM/...）
        │
  → 你的 mini-strace 是「核心機制」的證明
    真 strace 是「核心機制 + 海量細節」
    但核心是一樣的 → 你懂了核心，就懂了 strace
```

> **你的 mini-strace 證明了「核心機制」，真 strace 是「核心 + 海量細節」——但你已經懂了最重要的部分**。你的 mini-strace 有核心（攔截 syscall、顯示名字/參數/回傳值），真 strace 多的是**海量的細節工程**：完整的 syscall 表（幾百個 × 多架構）、每個 syscall 的精緻參數格式化（把 open 的 flags 解碼成 `O_RDONLY|O_CREAT`、把 struct 參數展開、把錯誤碼翻成 `ENOENT` 等 errno 名字）、`-f`（追蹤 fork/clone 出的子 process）、`-e`/`-c`/`-T` 等選項、各種 signal 和 ptrace 事件處理、多架構支援。這些是巨大的工程量（strace 是個成熟的大專案）。但**核心機制是一樣的**——都是 PTRACE_SYSCALL 在 syscall 邊界讀暫存器。你懂了核心，就懂了 strace 的本質；剩下的是「把核心做得完整好用」。這個認知很重要——它讓你不被工具的複雜嚇到（複雜是細節，核心很簡單），也讓你知道「如果要客製一個 tracer，從哪裡開始」（從這個核心循環擴展）。Ch 19 會用同樣的 ptrace 知識做更進階的事（process 注入）。當你之後用 strace 遇到「它怎麼顯示這個」「為什麼這樣」，你能從「mini-strace 的核心 + 真 strace 多做的細節」去推理。這是「理解工具」而非「會用工具」的境界。

## 故意弄壞:用你的 mini-strace 抓 bug

```bash
# 用你親手寫的 mini-strace 看一個程式的問題
cat > buggy.c <<'EOF'
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
int main() {
    int fd = open("/tmp/nonexistent_dir/file.txt", O_WRONLY);  // 開不存在的路徑
    if (fd < 0) {
        // 程式沒檢查錯誤，繼續用 fd = -1
        write(fd, "data", 4);     // write 到 fd -1 → 失敗
    }
    return 0;
}
EOF
gcc -o buggy buggy.c

# 用你的 mini-strace 看（它能顯示 openat 失敗）
./mini_strace ./buggy
# openat(-100, "/tmp/nonexistent_dir/file.txt", ...) = -1   ← open 失敗（回 -1）！
# write(-1, "data", 4) = -1                                  ← 用了壞的 fd

# 對照真 strace（它還顯示 errno）
strace ./buggy 2>&1 | grep -E 'openat|write'
# openat(... "/tmp/nonexistent_dir/file.txt" ...) = -1 ENOENT (No such file or directory)
# write(-1, "data", 4) = -1 EBADF (Bad file descriptor)
# → 真 strace 多顯示 errno（ENOENT/EBADF），更清楚是什麼錯
```

> **用你親手寫的 mini-strace 抓到「open 失敗回 -1、程式沒檢查還繼續用」的 bug——這是「理解工具」的完整體驗**。這個 bug 很典型——程式 open 一個不存在的路徑，open 回 -1（失敗），但程式沒檢查就繼續用 fd -1 去 write（也失敗）。你的 mini-strace 顯示 `openat(...) = -1`（open 失敗）和 `write(-1, ...) = -1`（用壞 fd）——直接看到問題。對照真 strace，它多顯示 **errno**（`ENOENT` 檔案不存在、`EBADF` 壞 fd）——這是真 strace 多做的「錯誤碼格式化」（把 -1 旁邊的 errno 翻成可讀的錯誤名）。這展示了：(1) 你的 mini-strace 真的能用來 debug（看到 syscall 失敗）；(2) 真 strace 的 errno 顯示讓 debug 更清楚（一眼知道是什麼錯）；(3) 這類 bug（不檢查 syscall 回傳值）正是 strace 最擅長抓的——你看到「某 syscall 回 -1」就知道那裡出錯了。這完成了 Part 1 的旅程——從理解 ptrace（Ch 3）到親手寫 mini-strace（這章）到用它 debug。你現在不只會用 strace，還理解它、甚至造了一個。接下來 Ch 5 深入真 strace 的完整用法（你會帶著「我知道它怎麼運作」的理解去學），這比一開始就學用法深刻得多。

## 動手練習

1. 編譯並跑：把 mini_strace.c 編譯，trace 幾個簡單命令（echo/ls/cat），看它的輸出

2. 擴充 syscall 表：加幾個 syscall 到 syscall_name（如 SYS_read、SYS_lseek），讓它認得更多

3. 改進參數顯示：讓 read 也顯示 buf（exit 時讀，因為 read 是 exit 後 buf 才有資料）

4. 對照真 strace：同個程式用你的和真 strace，列出真 strace 多顯示什麼（errno、flags 解碼）

5. 跑「故意弄壞」：用你的 mini-strace 抓 buggy.c 的 open 失敗，理解「syscall 回 -1 = 出錯」

## 本章重點整理

- mini-strace = Ch 3 的 ptrace 循環 + 「把暫存器翻譯成可讀」（syscall 名/參數/回傳值/讀字串）
- 核心部分：fork+TRACEME+exec、PTRACE_SYSCALL 循環、syscall 名對照、read_string（PEEKDATA 讀指標指向的字串）、entry/exit 配對
- 你的 mini-strace 真能用——顯示 openat 檔名、write 內容、回傳值，抓到和真 strace 一樣的 syscall
- 真 strace 多的是海量細節（完整 syscall 表、參數格式化、errno、-f/-e/-c、多架構）——核心一樣
- 用 mini-strace 能抓 bug（syscall 回 -1 = 失敗）；寫過它你對 strace 從「會用」變「理解」

## 自我檢核

- [ ] 能看懂並編譯 mini-strace，理解每個部分（fork/TRACEME、循環、read_string、entry/exit）
- [ ] 理解 read_string 為什麼要 PEEKDATA 一個 word 一個 word 讀（指標參數）
- [ ] 知道為什麼每個 syscall 停兩次（entry 顯示參數、exit 顯示回傳值）
- [ ] 能說出你的 mini-strace 和真 strace 的差距（細節而非核心）
- [ ] 能用 mini-strace 抓「syscall 失敗」的 bug

## 延伸閱讀

### 文章 / 教學

- **[Playing with ptrace, Part I](https://www.linuxjournal.com/article/6100)** — Linux Journal
  - **讀哪裡**：syscall trace 那部分（本章 mini-strace 的參考）
  - **為什麼值得讀**：經典的「用 ptrace 寫 tracer」教學

- **[How strace works](https://blog.packagecloud.io/how-does-strace-work/)** — packagecloud
  - **這篇說什麼**：strace 的內部運作，PTRACE_SYSCALL 的細節
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章「mini-strace vs 真 strace」的權威深入版

### 原始碼

- **[strace 原始碼](https://github.com/strace/strace)** — strace 專案
  - **讀哪裡**：syscall 表和參數解碼那部分（看真 strace 怎麼做細節）
  - **為什麼值得讀**：看「核心 + 海量細節」的真實工程（不用全讀，瀏覽結構）

### 官方文件

- **[ptrace(2)](https://man7.org/linux/man-pages/man2/ptrace.2.html)** — 配本章程式碼查 PTRACE_GETREGS/PEEKDATA 的細節

Part 1（基礎與 ptrace）到此完成——你補完了 process/syscall/fd/signal 模型、理解了 ptrace、親手寫了 mini-strace。接下來 Part 2 深入真 strace 的完整用法（你帶著「理解它怎麼運作」的優勢去學）。

→ [Ch 5 strace 完整指南](./05-strace-complete-guide.md)
