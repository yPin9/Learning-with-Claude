# Ch 10 — Environment modeling：syscall、libc、外部世界

> 目標：理解為什麼 symex 工具都附一大包 "runtime"（POSIX model、uclibc、SimProcedure）。這些東西不是額外功能，是 symex 能不能跑起來的關鍵。

## 問題：程式不是孤立的

Pure symex 假設你把整個 program 當 formula 看。但真實 program 跟外部世界互動：

- **syscall**：read、write、open、mmap、fork、...
- **libc**：printf、malloc、memcpy、strcmp、...
- **external library**：OpenSSL、libxml、libpng、...
- **OS**：時鐘、環境變數、network

symex engine 走到 syscall instruction 怎麼辦？

- 真的呼叫 kernel？那 symbolic state 怎麼跟 concrete kernel 溝通？kernel 看不懂 symbolic byte
- 不呼叫？那 program 拿不到 syscall 的回傳值，卡住

**答案永遠是**：把外部世界**model**出來。寫一堆 symex 版本的 "syscall stub"、"libc stub"，用 symex 自己理解的 concrete/symbolic 層面回答。

## KLEE 的 POSIX runtime

KLEE 的對策是附一個 **uclibc + POSIX runtime**：

- **uclibc**：完整 libc 實作（KLEE 編譯成 LLVM IR）— printf、malloc、memcmp 都是真正的 uclibc 實作，symex 跑這些 IR
- **POSIX runtime**：handles syscall — `open`、`read`、`write` 都 wrapped，底下接 KLEE 的 **symbolic file system model**

例：`open("/tmp/input", O_RDONLY)`：

```
1. program 呼叫 open()
2. uclibc 的 open() 用 syscall instruction
3. KLEE 攔截 syscall instruction
4. 查 POSIX model 裡有沒有 /tmp/input
5. 如果有 → 回傳 fd（concrete 小整數），之後 read 從這個 file 拿 byte
6. 如果沒有 → 回傳 -1、errno = ENOENT
```

`read(fd, buf, n)`：如果 fd 是 symbolic file（KLEE `--sym-files`），read 把 symbolic byte 寫到 buf。buf 後續的 access 就是 symbolic。

KLEE 的 POSIX runtime 實作：`runtime/POSIX/*.c`。幾千行 code，每個 syscall 一個 handler。

### --sym-files、--sym-stdin

實務跑 KLEE 你會看到：

```bash
klee --posix-runtime --libc=uclibc --sym-files 3 1024 --sym-stdin 512 target.bc
```

意思：
- `--posix-runtime`：使用 POSIX model
- `--libc=uclibc`：連結 uclibc
- `--sym-files 3 1024`：創 3 個 symbolic file，每個最多 1024 byte
- `--sym-stdin 512`：stdin 是 512 byte symbolic input

這是 KLEE 跑 coreutils 找 bug 的經典配置（2008 年 paper）。

## angr 的 SimProcedure

angr 沒有 uclibc — 它在 binary 上跑，libc 是 binary 裡的 .so。

如果 `auto_load_libs=True`，angr 把 libc.so 載進來，symex 真的會 step into libc 的 machine code。**極慢**，而且 libc 實作有很多 weird optimization（SSE memcmp、glibc 的 hand-rolled assembly），讓 formula 爆炸。

所以 angr 有 **SimProcedure** 機制：對常見 libc function 提供 Python 版本的 model。

```python
# angr/procedures/libc/strlen.py (簡化版)
class strlen(angr.SimProcedure):
    def run(self, s):
        # s 是 pointer
        max_sym_len = self.state.libc.max_str_len
        symbolic_str = self.state.memory.load(s, max_sym_len)
        # 找到第一個 0 byte
        for i in range(max_sym_len):
            byte = self.state.memory.load(s + i, 1)
            if self.state.solver.is_true(byte == 0):
                return i
        return max_sym_len
```

**用 Python + claripy 重寫 libc function 的 semantic**。angr 預設有 ~500 個 SimProcedure 覆蓋 libc、libpthread、Windows API。

### 啥時會 fallback 到真實 libc

- 你沒 hook 的 function（uncommon libc call、third-party library）
- 你明確關掉 auto-hook

這時 angr 會真的跑 .so 的 code。慢、容易炸，但有時是唯一選擇。

### 自己寫 SimProcedure

用 angr 做 RE 時常常要寫自己的 hook。例子：target 呼叫了一個閉源加密 function，你不想讓 angr 真的 step into：

```python
class skip_crypto(angr.SimProcedure):
    def run(self, input_ptr, output_ptr, length):
        # 假裝 crypto 沒做事，直接把 input 抄到 output
        data = self.state.memory.load(input_ptr, length)
        self.state.memory.store(output_ptr, data)
        return 0

proj.hook_symbol('mystery_crypto', skip_crypto())
```

或你知道它的 invariant（output 必然長度 32）、就寫一個 model 回傳 symbolic 32 byte：

```python
class model_crypto(angr.SimProcedure):
    def run(self, input_ptr, output_ptr, length):
        fake_output = claripy.BVS('crypto_out', 32 * 8)
        self.state.memory.store(output_ptr, fake_output)
        return 0
```

這種 hack 在真實 symex 工程裡**非常常見**。

## Triton 的做法

Triton 更偏 concolic / taint，對 external call 有另一套：

- 預設 concolic — syscall 真的跑 concrete，symbolic 那邊記 branch
- 要 symbolic 某個 syscall 的結果，手動 overwrite

```python
# 假設 getchar 返回到 al，我們想讓它 symbolic
ctx.concretizeRegister(ctx.registers.rax)
sym_byte = ctx.symbolizeRegister(ctx.registers.al, 'user_input')
```

比較手動，但精確。Part 5 Triton 細講。

## 常見 "model bug" 與怎麼診斷

### Bug 1：SimProcedure 實作不完整

典型：`printf("%d", x)` 的 symex — angr 的 printf SimProcedure 對複雜 format string 可能不完整處理，symbolic argument 會被 concretize。

diagnoser：symex 跑到 printf 後，symbolic 值變 concrete 了。

對策：手動寫更完整的 hook，或用 `unicorn` backend（直接跑 machine code）。

### Bug 2：libc version mismatch

KLEE 的 uclibc 版本舊。現代 target（glibc 2.30+）的 syscall 編號、struct layout 可能不對。

對策：用 docker 鎖住 KLEE 2.3 / uclibc 版本，不要自己升。

### Bug 3：thread / signal 沒 model

KLEE、angr 對 multi-threading 基本沒支援。pthread_create 走進去就卡。

對策：改 target 變成 single-threaded，或放棄 symex 改動態分析。

### Bug 4：filesystem 沒存在的 file

KLEE：`open("/proc/self/status", ...)` — POSIX runtime 沒 model `/proc`，open 回 -1。如果 program 依賴讀 /proc，要手動餵 symbolic file 進去。

```bash
klee --sym-files 1 512  # 創 /tmp/sym_file.a 等
```

然後 target 去 open 那個 file。

## 兩個策略：精確 model vs 模糊

**精確 model**：每個 external call 都寫對應的 SimProcedure。
- 優點：精度高、能 reason external 的 effect
- 缺點：大量手動工作、每個 target 都要寫

**模糊 model**：externals 回傳 unconstrained symbolic。
- 優點：省事
- 缺點：很多 false path，因為 unconstrained symbolic 什麼值都可以

實務混用：**常見 libc 用精確 model（已經有 SimProcedure）**，**罕見 external 回傳 unconstrained + 加人工 constraint**（「我知道這個 function 回正數」）。

## File descriptor / stream model

KLEE / angr 都把 fd 當成 `int`。internal 有個 fd → file_contents 的 map：

```
fd 0 → stdin (symbolic, bounded size)
fd 1 → stdout (bytes accumulate)
fd 2 → stderr
fd 3+ → other symbolic files or concrete files
```

`read(fd, buf, n)`：去查 fd → file，取 n byte 寫 buf。

`write(fd, buf, n)`：如果 fd == 1（stdout），把 buf 的內容 append 到 `state.posix.stdout`。之後 test code 可以用 `state.posix.dumps(1)` 讀回來。

這就是 Ch 0 hello angr 例子用 `b'win' in s.posix.dumps(1)` 判斷 win 的原因 — state.posix.stdout 是 symex 模擬的 stdout 內容。

## Malloc / heap model

`malloc(size)` 在 symex 中：
- concrete size：engine 的 heap allocator 配一塊，回傳 address（通常 concrete）
- symbolic size：要 enumerate 或 concretize

`free(ptr)`：engine 記錄這塊 freed。後續 load/store 報 use-after-free。

KLEE 的 heap 是 bump allocator（`malloc(100)` → `malloc_arena_ptr`, bump 100）。簡單但不 fragmentation。適合 bug-finding，不適合測 heap exploitation。

angr 用 pluggable heap — 你可以裝 `SimHeapLibc`（模擬 glibc 真實 allocator）或 `SimHeapPT`（Page table-like）。測 heap-related CVE 時常需要 simulate 真實 glibc 的 heap 行為。

## 時間、隨機、環境變數

- `time()`、`clock_gettime()`：工具通常回傳 concrete（0 或固定值）或 symbolic unconstrained
- `rand()`、`/dev/urandom`：同上
- `getenv()`：KLEE 的 `--env-file` 可餵環境變數；angr 可手動 hook

**關鍵觀察**：crypto 軟體很依賴 randomness，symex 跑 crypto 通常會把隨機也 symbolize — 導致 path explosion 或 formula 爆炸。**不適合 symex 做 crypto 實作分析**（雖然可以做 constant-time 驗證）。

## 為什麼 KLEE 論文對 coreutils 這麼強

KLEE 對 coreutils（`ls`、`cat`、`echo`）很強的原因：

1. coreutils 的 input 主要是 **arg + stdin**，syscall 少
2. 用的 libc call 幾乎都是 uclibc model 過的
3. 沒 network、沒 threading
4. 短小、path 可控

KLEE 在 coreutils 90+% line coverage 並找出 **50+ 個 historical bug**。這是 symex 最成功的論文 benchmark。

但別被這個誤導 — 你把 KLEE 拿去跑 Chrome、跑 nginx、跑 OpenSSL，它沒這麼光彩。environment 太複雜。

## 心法：model 是第一 class citizen

做 symex 工程，你花在 **寫 model / hook / SimProcedure** 的時間，往往超過寫 symex script 本身。

模式：

1. 先裸跑一次 angr / KLEE
2. 看哪個 function 讓 state 爆 / 走偏 / concretize 掉
3. 寫 hook 讓那個 function 不要被完整 symex
4. 重複

這就是 "symex 工程" 的日常。沒有什麼 plug-and-play。

## 自我檢核

- [ ] 解釋 KLEE 的 POSIX runtime + uclibc 在做什麼
- [ ] 理解 angr SimProcedure 的角色，能自己寫一個 hook 替換某個 function
- [ ] 知道常見 env model bug（libc mismatch、missing syscall、/proc）
- [ ] 區分「精確 model」 vs 「unconstrained return」兩種 strategy
- [ ] 解釋為什麼 KLEE 對 coreutils 強但對 Chrome 弱

Part 2 結束。下一個是 **練習 A**，把 Ch 7 的 mini concolic 擴展成一個完整的工具，加上 memory、loop 限制、更多 bytecode。做完你就有一個能跑的 toy symex framework。

→ [練習 A：寫一個 mini concolic executor](./practice-a-mini-concolic.md)
