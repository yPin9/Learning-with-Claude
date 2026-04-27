# Ch 18 — Sanitizers (ASan / UBSan / TSan / MSan)

> 目標：搞懂 4 個 sanitizer 各抓什麼、互相不能搭、怎麼跟 valgrind 取捨。CI 跟 fuzzing 怎麼用。

## sanitizer 是什麼

**編譯時插入檢查 code** 的工具，跟 valgrind「runtime simulator」相反。gcc / clang 都支援。

優點：快（2-3x slowdown vs valgrind 10-50x）、抓更多種 bug。
缺點：要重編、需要 source、有時不能跟 valgrind 一起用。

四大家族：

| Sanitizer | 抓什麼 | flag |
|---|---|---|
| **ASan** (AddressSanitizer) | heap/stack/global OOB、UAF、double-free、leak | `-fsanitize=address` |
| **UBSan** (UndefinedBehaviorSanitizer) | UB（signed overflow、null deref、misalign...） | `-fsanitize=undefined` |
| **TSan** (ThreadSanitizer) | data race、deadlock | `-fsanitize=thread` |
| **MSan** (MemorySanitizer) | uninitialized read | `-fsanitize=memory`（**只 clang**） |

**ASan 跟 TSan 跟 MSan 不能同時用**（會打架）。UBSan 可以跟其他配。

## ASan — 最常用

```bash
gcc -fsanitize=address -g -O1 myprog.c -o myprog
./myprog
```

對 UAF：

```c
int *p = malloc(sizeof(int));
free(p);
*p = 42;
```

跑：

```
==1234==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010
WRITE of size 4 at 0x602000000010 thread T0
    #0 0x401234 in main uaf.c:8
    #1 0x... in __libc_start_main
    
0x602000000010 is located 0 bytes inside of 4-byte region [0x602000000010,0x602000000014)
freed by thread T0 here:
    #0 0x... in free
    #1 0x401123 in main uaf.c:7
previously allocated by thread T0 here:
    #0 0x... in malloc
    #1 0x401111 in main uaf.c:5
```

訊息結構同 valgrind：「現在做什麼」、「之前 free 在哪」、「alloc 在哪」。**clearer + 更快**。

## ASan 抓 stack OOB

```c
int main() {
    int a[10];
    a[10] = 0;        // ❌
}
```

```
==1234==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x...
WRITE of size 4 at 0x... thread T0
    #0 0x... in main stack.c:3

Address 0x... is located in stack of thread T0 at offset 72 in frame
    #0 0x... in main stack.c:1
```

**valgrind 抓不到的 stack OOB，ASan 抓得到**。這是 ASan 最大優勢。

## ASan 抓 global OOB

```c
int global[10];
int main() {
    global[10] = 0;
}
```

```
==1234==ERROR: AddressSanitizer: global-buffer-overflow ...
```

global 也抓。

## ASan 的 leak detection

預設啟用 LSan (LeakSanitizer)：

```c
int main() {
    malloc(100);
    return 0;
}
```

```
==1234==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 100 byte(s) in 1 object(s) allocated from:
    #0 0x... in malloc
    #1 0x401111 in main leak.c:3
```

跟 valgrind --leak-check=full 對等。

關掉 leak check：

```bash
ASAN_OPTIONS=detect_leaks=0 ./myprog
```

## ASan 環境變數

```bash
ASAN_OPTIONS=halt_on_error=0 ./myprog          # 不要第一錯就停
ASAN_OPTIONS=abort_on_error=1 ./myprog          # 錯時 SIGABRT，方便產 core
ASAN_OPTIONS=detect_stack_use_after_return=1 ./myprog
ASAN_OPTIONS=symbolize=1:print_stacktrace=1 ./myprog
ASAN_OPTIONS=log_path=/tmp/asan ./myprog        # 寫檔
```

CI 常用：

```bash
ASAN_OPTIONS="halt_on_error=1:abort_on_error=1:detect_leaks=1" ./test
```

## UBSan

抓 C / C++ 標準定義為「未定義行為」(UB) 的東西。**很多 UB 平常看似沒事但隨優化升級爆炸**。

```bash
gcc -fsanitize=undefined -g myprog.c -o myprog
./myprog
```

範例：signed overflow

```c
int x = INT_MAX;
x++;
```

```
runtime error: signed integer overflow: 2147483647 + 1 cannot be represented in type 'int'
```

範例：null deref

```c
int *p = NULL;
*p = 5;
```

```
runtime error: load of null pointer of type 'int'
```

範例：shift overflow

```c
int x = 1 << 35;
```

```
runtime error: shift exponent 35 is too large for 32-bit type 'int'
```

範例：misaligned access

```c
char buf[8];
int *p = (int*)(buf + 1);
*p = 0;
```

```
runtime error: store to misaligned address 0x... for type 'int', which requires 4 byte alignment
```

UBSan 開銷小（通常 < 20% slowdown），**production 都能跑**。Chrome / Firefox 的 release build 帶 UBSan subset。

UBSan 細項可選：

```bash
gcc -fsanitize=undefined,bounds,nullability myprog.c
```

`man gcc` 看完整列表。

## TSan

抓 race、lock 順序、condition variable misuse。對應 helgrind 但**快很多**且少 false positive。

```bash
gcc -fsanitize=thread -g myprog.c -lpthread -o myprog
./myprog
```

對 counter race：

```
WARNING: ThreadSanitizer: data race (pid=1234)
  Write of size 4 at 0x... by thread T2:
    #0 worker race.c:6 (myprog+0x...)

  Previous write of size 4 at 0x... by thread T1:
    #0 worker race.c:6 (myprog+0x...)

  Location is global 'counter' of size 4 at 0x...
```

清楚標出兩個 thread 的位置 + 變數名（如果 -g）。

```bash
TSAN_OPTIONS="halt_on_error=1:abort_on_error=1" ./test
```

## MSan

抓未初始化記憶體 read。**只 clang**。

```bash
clang -fsanitize=memory -g myprog.c -o myprog
./myprog
```

```c
int x;
if (x > 0) printf("yes\n");
```

```
==1234==WARNING: MemorySanitizer: use-of-uninitialized-value
    #0 0x... in main uninit.c:4
```

MSan 要求**所有依賴 lib 也用 MSan 編**，否則 lib 內 init 的記憶體會被 MSan 當未 init。所以實務 MSan 只在能完整重編整個 stack 的場合（如 Chrome）用。

## 為什麼不能同時用

ASan / TSan / MSan 都改 memory layout、插 hook，互相打架。

```bash
gcc -fsanitize=address,thread myprog.c
# 編不過或跑時 conflict
```

CI 通常**多份 build**：normal / asan-build / tsan-build / ubsan-build，分別跑 test。

## 配合 fuzz

ASan + fuzzer 是 modern security 標配：

```bash
clang -fsanitize=address,fuzzer myprog.c -o fuzzer
./fuzzer       # libfuzzer 自動跑
```

每次 input 跑一次，ASan 抓到 bug 就停 + dump input。

## 一個常見踩雷：optimize 太高漏報

```bash
gcc -O3 -fsanitize=address myprog.c
```

`-O3` 可能優化掉某些 ASan 能抓到的 path。**官方建議 `-O1`**：

```bash
gcc -O1 -g -fsanitize=address ...
```

CI 可以同時 build `-O0` `-O1` `-O2` 各一份。

## 一個常見踩雷：static linking 失敗

```bash
gcc -static -fsanitize=address myprog.c
# undefined reference to __asan_init...
```

ASan runtime 必須 dynamic link。static binary 不能加 ASan。

## 一個常見踩雷：在 docker / sandbox 跑 ASan

ASan 用 mmap shadow memory（很大的 virtual address），有時 container limit 擋住：

```
==1234==ERROR: AddressSanitizer failed to allocate ...
```

放寬 `--ulimit` 或關 ASLR。

## 一個常見踩雷：SUID binary + ASan

```bash
chmod +s ./myprog
./myprog
# ASan 不工作
```

SUID 程式為了安全 ASan 自動 disable（防 ASAN_OPTIONS 提權）。

## valgrind vs sanitizer 怎麼選

| 場景 | 選 |
|---|---|
| **CI / 開發**（能重編） | sanitizer（快） |
| **第三方 binary 沒 source** | valgrind |
| **stack OOB** | ASan only |
| **uninit read** | MSan / valgrind memcheck |
| **race** | TSan（比 helgrind 強） |
| **production 偵錯** | 都別跑（用 perf / bpftrace） |
| **fuzz** | sanitizer |

實務上**寫新 code 一定上 ASan + UBSan**，CI 跑。能用就用。

## 動手練習

**1. 對比 ASan 跟 valgrind 速度**

寫個 1 秒就跑完的 程式：

```bash
gcc -O0 prog.c -o normal
gcc -O1 -fsanitize=address -g prog.c -o asan

time ./normal
time valgrind ./normal
time ./asan
```

通常 ASan 比 valgrind 快 5-10x。

**2. 故意 stack OOB**

```c
int main() {
    int a[10];
    a[20] = 0;
    return 0;
}
```

```bash
valgrind ./prog       # 看不到
gcc -fsanitize=address prog.c -o prog && ./prog       # ASan 抓到
```

**3. UBSan signed overflow**

```c
#include <stdio.h>
#include <limits.h>
int main() {
    int x = INT_MAX;
    printf("%d\n", x + 1);
}
```

```bash
gcc -fsanitize=undefined uover.c -o uover
./uover
```

**4. TSan race**

寫 counter race，跑 TSan vs helgrind 比速度跟訊息。

**5. CI integration 思考**

設想一個 GitHub Action，每次 PR build 三份 binary（asan / tsan / ubsan）跑各自的 test。寫 pseudo Yaml。

## 自我檢核

- [ ] 知道 ASan / UBSan / TSan / MSan 各抓什麼
- [ ] 知道 ASan / TSan / MSan 不能同時用
- [ ] 用過 ASan 抓 stack OOB（valgrind 抓不到）
- [ ] 用過 UBSan 抓 signed overflow / null deref
- [ ] 用過 TSan 抓 race
- [ ] 知道何時 sanitizer 比 valgrind 適合，反之
- [ ] 知道 ASan 跟 SUID / static binary 不相容

下個是 Part 6 的整合練習：multithreaded race + leak + UAF 的綜合 hunt。

→ [練習 C：multithreaded race + leak hunt](./practice-c-multithreaded-hunt.md)
