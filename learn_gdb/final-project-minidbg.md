# Final Project — minidbg：用 ptrace + DWARF 從零寫一個 mini debugger

> 目標：把整個課程學到的東西整合成一個真正能跑的產品 — 一個大約 500–800 行 C 的 mini debugger，支援：下斷點、single-step、看 backtrace、print 變數、讀 source line。

## 範圍

minidbg 要達成的功能：

1. **啟動與 attach**：`minidbg <program>` 啟動目標並接管
2. **REPL**：讀使用者指令（像 gdb 的 `(gdb)` prompt）
3. **基本執行控制**：`cont`、`step`（single instruction）、`next`（source line level）
4. **Breakpoint**：`break FUNCTION` / `break FILE:LINE` / `break *0xADDR`，命中 auto handling
5. **讀 register 與 memory**：`regs`、`dump ADDR LEN`
6. **Print 變數**：`print VAR`（需要 DWARF location interpretation）
7. **Backtrace**：`bt`（用 libunwind 或 libdw 的 unwind API）
8. **Source line**：`list`（讀 source file + DWARF line table）

進階（選做）：

- Conditional breakpoint
- Watchpoint（hardware）
- Signal handling
- Inferior call

## 技術選擇

| 任務 | 選 library |
|---|---|
| Ptrace | Linux 內建（`<sys/ptrace.h>`） |
| ELF parsing | `libelf`（elfutils）或手寫 |
| DWARF parsing | `libdw`（elfutils）或 `libdwarf` |
| Unwinding | `libunwind` 或 `libdw` 的 unwind API |
| Disassembly | `libcapstone`（可選） |
| Readline | GNU readline 或 linenoise |

**建議**：全用 `elfutils`（`libelf` + `libdw`），一個 library 搞定 ELF 跟 DWARF。

```bash
sudo apt install libdw-dev libunwind-dev libreadline-dev libcapstone-dev
```

## 架構

```
minidbg/
├── Makefile
├── main.c               ← 進入點、主 loop
├── debugger.h / .c      ← Debugger class：啟動 child、REPL、命令分派
├── breakpoint.h / .c    ← Breakpoint manager（Ch 19 的包裝）
├── dwarf.h / .c         ← DWARF 讀取：pc→line、var location
├── unwind.h / .c        ← Backtrace
└── ptrace_util.h / .c   ← PEEK/POKE/GETREGS 的小包裝
```

## Step-by-step 實作計畫

以下是**建議的實作順序**。每個 step 都產出可以跑、可以 demo 的版本。

### Step 1：啟動目標 + attach + REPL 骨架

```c
// main.c
int main(int argc, char **argv) {
    if (argc < 2) die("usage: minidbg <prog>");

    pid_t pid = fork();
    if (pid == 0) {
        ptrace(PTRACE_TRACEME, 0, 0, 0);
        execv(argv[1], &argv[1]);
        die("exec failed");
    }

    int status;
    waitpid(pid, &status, 0);           // 等目標執行到第一個 SIGTRAP

    Debugger *dbg = debugger_new(pid, argv[1]);
    debugger_run(dbg);
    debugger_free(dbg);
    return 0;
}
```

```c
// debugger.c - REPL
void debugger_run(Debugger *dbg) {
    char *line;
    while ((line = readline("(minidbg) ")) != NULL) {
        if (strlen(line) > 0) add_history(line);
        debugger_handle_command(dbg, line);
        free(line);
    }
}
```

命令用 `strtok` 切開，dispatch 到各 handler。此時支援的命令：`cont`、`quit`。

**驗收**：能啟動目標、看到 prompt、打 `cont` 讓目標跑完、`quit` 離開。

### Step 2：Single-step + register dump

加 `step` 與 `regs` 命令：

```c
void cmd_step(Debugger *dbg) {
    ptrace(PTRACE_SINGLESTEP, dbg->pid, 0, 0);
    int status;
    waitpid(dbg->pid, &status, 0);
    printf("stopped at 0x%lx\n", read_rip(dbg->pid));
}

void cmd_regs(Debugger *dbg) {
    struct user_regs_struct regs;
    ptrace(PTRACE_GETREGS, dbg->pid, 0, &regs);
    printf("rax: 0x%llx\n", regs.rax);
    printf("rbx: 0x%llx\n", regs.rbx);
    // ... all regs ...
    printf("rip: 0x%llx\n", regs.rip);
}
```

**驗收**：能 single-step、印 register 正常。

### Step 3：Breakpoint manager

實作 Ch 19 的 Breakpoint struct，加 `break *ADDR` 命令：

```c
typedef struct {
    unsigned long addr;
    long orig_data;
    int enabled;
} MiniBreakpoint;

void bp_set(pid_t pid, MiniBreakpoint *bp) { /* Ch 19 */ }
void bp_remove(pid_t pid, MiniBreakpoint *bp) { /* Ch 19 */ }
```

Debugger 維護一個 `MiniBreakpoint bps[MAX_BPS]`。

`cmd_break` handler：

```c
void cmd_break(Debugger *dbg, const char *arg) {
    unsigned long addr = strtoul(arg + 1, NULL, 0);    // 假設 "*0x1234"
    MiniBreakpoint *bp = &dbg->bps[dbg->bp_count++];
    bp->addr = addr;
    bp_set(dbg->pid, bp);
    printf("breakpoint %d at 0x%lx\n", dbg->bp_count - 1, addr);
}
```

在 `cmd_cont` 裡處理「已停在斷點上」的情境：要先還原 byte + RIP-- + single-step + re-arm，再 CONT：

```c
void cmd_cont(Debugger *dbg) {
    unsigned long rip = read_rip(dbg->pid);
    MiniBreakpoint *bp = find_bp_at(dbg, rip - 1);
    if (bp && bp->enabled) {
        bp_after_hit(dbg->pid, bp);    // Ch 19 的 5 步驟
    }
    ptrace(PTRACE_CONT, dbg->pid, 0, 0);
    int status;
    waitpid(dbg->pid, &status, 0);
    if (WIFSTOPPED(status) && WSTOPSIG(status) == SIGTRAP) {
        unsigned long rip2 = read_rip(dbg->pid);
        MiniBreakpoint *bp2 = find_bp_at(dbg, rip2 - 1);
        if (bp2) printf("hit breakpoint at 0x%lx\n", bp2->addr);
    }
}
```

**驗收**：`break *0x11b8`、`cont` 應該命中。`cont` 繼續能再 run。

### Step 4：DWARF 載入

用 `libdw`：

```c
#include <elfutils/libdwfl.h>

// Debugger 結構加上 Dwfl *dwfl

Dwfl_Callbacks cb = { /* standard callbacks */ };
dbg->dwfl = dwfl_begin(&cb);
dwfl_report_elf(dbg->dwfl, "target", program_path, -1, 0, false);
dwfl_report_end(dbg->dwfl, NULL, NULL);
```

**驗收**：dwfl 建立成功，能查 `dwfl_addrmodule(dwfl, pc)`。

### Step 5：`break FUNCTION` / `break FILE:LINE`

用 DWARF 查函式與 line：

```c
// 查函式 foo 的位址
Dwarf_Addr find_function(Dwfl *dwfl, const char *name) {
    Dwfl_Module *mod = /* iterate */;
    GElf_Sym sym;
    GElf_Addr addr;
    if (dwfl_module_getsym_info(mod, index, &sym, &addr, NULL, NULL, NULL) != NULL) {
        if (strcmp(sym.st_name_string, name) == 0) return addr;
    }
    return 0;
}
```

或用 `dwfl_module_addrsym` / `dwarf_getsrc_file` 等 API 實際實作 `FILE:LINE`。

**驗收**：`break main` 可以找到 main、下斷點。

### Step 6：`list` 讀 source line

```
(minidbg) list
     5  int square(int n) {
     6      return n * n;
     7  }
     8
  >  9  int main(void) {
    10      int x = 3;
    11      printf("%d\n", square(x));
    12  }
```

- 用 DWARF `.debug_line` 拿到「當前 PC 對應 file:line」
- 讀該 file，印出當前 line 前後各幾行

用 `dwfl_module_addrsrc` 或 `dwarf_getsrc_line` API。

**驗收**：`list` 能印出源碼 context。

### Step 7：Backtrace

用 `libdw` 的 unwind API：

```c
#include <elfutils/libdw.h>
#include <elfutils/libdwfl.h>

static int frame_cb(Dwfl_Frame *state, void *arg) {
    Dwarf_Addr pc;
    dwfl_frame_pc(state, &pc, NULL);
    Dwfl *dwfl = arg;
    Dwfl_Module *mod = dwfl_addrmodule(dwfl, pc);
    const char *fname = dwfl_module_addrname(mod, pc);
    printf("#%d  0x%lx  %s\n", (int)pc, fname ? fname : "??");
    return DWARF_CB_OK;
}

void cmd_bt(Debugger *dbg) {
    dwfl_getthread_frames(dbg->dwfl, dbg->pid, frame_cb, dbg->dwfl);
}
```

**驗收**：`bt` 印出目前 call stack。

### Step 8：Print 變數

最複雜的一步。需要：

1. 查 DWARF 找當前 scope 裡叫 `x` 的變數 DIE
2. 讀它的 `DW_AT_location`（DWARF expression bytecode）
3. 跑 expression interpreter，得到「變數位址」或直接「值」
4. 讀目標 memory，依 `DW_AT_type` 印出

`libdw` 提供 `dwarf_getlocation` API 做 expression 跑。

```c
void cmd_print(Debugger *dbg, const char *var_name) {
    // 查當前 function 的 DIE
    Dwarf_Addr pc = read_rip(dbg->pid) - 1;
    Dwarf_Die *cu_die = /* dwfl_addrmodule + module_getdwarf */;
    Dwarf_Die func_die;
    find_enclosing_function(cu_die, pc, &func_die);
    Dwarf_Die var_die;
    find_variable(&func_die, var_name, &var_die);

    // 拿 location
    Dwarf_Op *expr;
    size_t expr_len;
    dwarf_getlocation_addr(...);

    // 執行 expression → 得到位址或 register
    unsigned long addr = eval_expr(dbg->pid, expr, expr_len);

    // 讀 memory、依 type 印
    Dwarf_Die type_die;
    dwarf_formref_die(/* DW_AT_type attribute */, &type_die);
    int size = dwarf_bytesize(&type_die);
    long value = ptrace(PTRACE_PEEKDATA, dbg->pid, (void *)addr, 0);
    printf("%s = %ld\n", var_name, value & ((1L << (8 * size)) - 1));
}
```

這部分有不少細節。精簡版先支援 local int 變數，其他型別往後加。

**驗收**：`print x` 在 main 裡能顯示當下的 `int x` 值。

### Step 9：`next`（source line level）

`step` 是 single instruction，`next` 是 source line。實作：

1. 當前 PC 對應的 source line N
2. 下 breakpoint 在「同函式、行號 > N」的下一條指令位址
3. Continue
4. 到斷點後清掉

gdb 的 `next` 更複雜（要跳過 sub-call），最小版本先實作「行號 >= N+1 就停」即可。

### Step 10：收尾

- 支援 `del N` 刪除斷點
- `help` 印指令說明
- 處理 inferior 已 exit 的狀態（所有指令對已死 inferior 都要 graceful 回應）
- 優化錯誤訊息

## 例子：完整 session

```
$ ./minidbg ./hello
target pid 12345
child stopped at 0x7ffff7fe3290 (dynamic linker)

(minidbg) break main
breakpoint 0 at 0x5555555551b8

(minidbg) cont
hit breakpoint at 0x5555555551b8

(minidbg) list
     5  int square(int n) {
     6      return n * n;
     7  }
     8
  >  9  int main(void) {
    10      int x = 3;
    11      printf("%d\n", square(x));
    12      return 0;
    13  }

(minidbg) bt
#0  0x5555555551b8  main at hello.c:9
#1  0x7ffff7e...    __libc_start_main

(minidbg) step
stopped at 0x5555555551bf

(minidbg) next
stopped at 0x5555555551c6

(minidbg) print x
x = 3

(minidbg) regs
rax: 0x...
rdi: 0x...
rip: 0x5555555551c6

(minidbg) cont
3
[inferior exited]
(minidbg) quit
```

## 取巧與擴展

### 取巧

- **不處理 multi-thread**：第一版單 thread 就好，Ch 11 那些 API 很複雜。
- **不處理 ASLR**：開始 inferior 前 `personality(ADDR_NO_RANDOMIZE)`，省得換算 load base。
- **不做 pretty print**：所有 `print` 結果都當 long 顯示。

### 擴展

- **用 capstone 做 `disas`**：一條指令顯示組語。
- **Conditional breakpoint**：在 breakpoint 結構加 expression，命中時 eval（簡化到「變數 == 常數」）。
- **Hardware watchpoint**：寫 DR7。
- **Scripting**：跑 Python？太重。可以支援一個極簡 command file。

## 參考資源

- **Sy Brand's "Writing a Linux Debugger"**：從零寫 debugger 的 10 篇系列，是本課靈感來源。Google 得到。
- **elfutils documentation**：<https://sourceware.org/elfutils/>
- **GDB source code**：`gdb/target.c`、`gdb/remote.c`、`gdb/breakpoint.c`。看 grown-up 版長什麼樣。
- **libdwarf book**：David Anderson 寫的完整 libdwarf 參考。

## 驗收 checklist

完整版最少要能：

- [ ] 啟動目標、看到 `(minidbg)` prompt
- [ ] `break main` / `break sample.c:10` / `break *0xADDR` 三種都能下
- [ ] `cont` 命中斷點正確停下、指令位址正確顯示
- [ ] `step` 單步一條機器指令
- [ ] `next` 跨 source line（同函式內）
- [ ] `regs` 印出主要暫存器
- [ ] `list` 印源碼 + 標記當前行
- [ ] `bt` 印出呼叫鏈至少 2 層
- [ ] `print INT_VAR` 印 int 局部變數的值
- [ ] `quit` 清乾淨

## 自我檢核（整個課程的最終版）

- [ ] 我能從 source 讀懂 GDB 如何用 ptrace 控制 tracee
- [ ] 我能解釋 breakpoint 從下到命中到 re-arm 的完整 byte-level round-trip
- [ ] 我能用 DWARF 自己查函式位址、source line、變數位置
- [ ] 我能用 libdw 做 unwinding 印 backtrace
- [ ] 我能實作一個 ~500 行 C 的 debugger 涵蓋基本功能
- [ ] 未來看到 gdb 的行為，我能合理猜到底下發生什麼

---

## 結語

從 Ch 0 的 `-g` 到這個 Final Project，你走了 21 章的路。

一開始 gdb 對你是個黑盒子 — 你打 `break main`、`run`、看到 `Breakpoint 1, main () at ...`，覺得神奇。現在你知道：

- 那個 `0x1149` 是 DWARF 裡 `DW_TAG_subprogram` 的 `DW_AT_low_pc`
- GDB 用 `ptrace(PTRACE_POKEDATA)` 把 `0x55` 改成 `0xcc`
- CPU 執行到那裡觸發 `#BP`，kernel 發 `SIGTRAP`
- GDB 的 `waitpid()` 醒來，查內部斷點表，印出 `Breakpoint 1`
- 你打 `print x`，GDB 查 `.debug_info` 找 `x` 的 `DW_AT_location`，跑 `DW_OP_fbreg -20`，算出位址，`PEEKDATA` 讀值，按 `DW_AT_type` 格式化印出

整個 **debugger 這個程式類別**在你心裡從神秘變清晰。

接下來你可以：

- 看 gdb 原始碼：有了這些 mental model 後，source tree 不再天書
- 玩 rr、lldb 等其他 debugger：差異主要在實作選擇，核心觀念一樣
- 為你熟悉的語言 / runtime 寫 gdb 擴充（pretty printer、custom commands）
- 在 production 把 debug 能力變成同事的武器

出師了。
