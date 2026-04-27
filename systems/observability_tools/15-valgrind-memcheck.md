# Ch 15 — valgrind memcheck

> 目標：用 valgrind memcheck 抓 memory leak、use-after-free、uninit read、double-free。讀懂錯誤訊息、suppress false positive、知道它的限制。

## valgrind 是什麼

一個 **dynamic binary translator**。它先把你的 binary 翻成自家 IR（叫 VEX），再插入額外檢查 code，再 JIT 回 native 跑。

換句話說：**程式不是直接跑在 CPU 上，而是跑在 valgrind 的虛擬 CPU 裡**。每條指令前後都能加 hook。

代價：**慢 10-50x**。但能抓到很多 sanitizer 漏掉的細節，**不需要 recompile**。

valgrind 是個框架，下面有多個 tool：

| Tool | 抓什麼 |
|---|---|
| `memcheck` | memory leak / UAF / uninit / OOB |
| `helgrind` | thread race / lock 順序 |
| `drd` | thread race（另一種演算法） |
| `cachegrind` | cache simulator |
| `callgrind` | call graph + cycle 估計 |
| `massif` | heap profiler |
| `dhat` | heap profiler（更新版） |

預設 tool 是 memcheck。

## memcheck 用法

```bash
valgrind ./myprog                          # 預設 memcheck
valgrind --leak-check=full ./myprog        # 詳細 leak
valgrind --leak-check=full --show-leak-kinds=all ./myprog
valgrind --leak-check=full --track-origins=yes ./myprog
valgrind --suppressions=my.supp ./myprog
valgrind --gen-suppressions=all ./myprog   # 產 suppression
valgrind --log-file=val.log ./myprog
valgrind -q ./myprog                        # quiet（只印錯誤）
```

最常用：

```bash
valgrind --leak-check=full --show-leak-kinds=all --track-origins=yes ./myprog
```

## 偵測：use-after-free

```c
// uaf.c
#include <stdlib.h>
#include <stdio.h>

int main(void) {
    int *p = malloc(sizeof(int));
    *p = 42;
    free(p);
    printf("%d\n", *p);    // ❌ UAF
    return 0;
}
```

```bash
gcc -g uaf.c -o uaf
valgrind ./uaf
```

```
==1234== Invalid read of size 4
==1234==    at 0x401189: main (uaf.c:8)
==1234==  Address 0x4a5f040 is 0 bytes inside a block of size 4 free'd
==1234==    at 0x484CFA1: free (...)
==1234==    by 0x401184: main (uaf.c:7)
==1234==  Block was alloc'd at
==1234==    at 0x4848899: malloc (...)
==1234==    by 0x40117D: main (uaf.c:5)
```

三段資訊：

1. **Invalid read** at uaf.c:8（用了已 free 的）
2. **free'd at** uaf.c:7（哪裡 free 的）
3. **alloc'd at** uaf.c:5（哪裡 alloc 的）

完整故事一目了然。

## 偵測：double free

```c
free(p);
free(p);
```

```
==1234== Invalid free() / delete / delete[] / realloc()
==1234==    at 0x484CFA1: free
==1234==  Address 0x... is 0 bytes inside a block of size 4 free'd
==1234==    at 0x... by ...
==1234==  Block was alloc'd at ...
```

## 偵測：out-of-bound

```c
int *a = malloc(10 * sizeof(int));
a[10] = 0;            // ❌ OOB write
```

```
==1234== Invalid write of size 4
==1234==    at 0x401234: main (oob.c:7)
==1234==  Address 0x... is 0 bytes after a block of size 40 alloc'd
```

valgrind 對 heap OOB 抓得很好。**對 stack OOB 不抓**（它看不出來 stack 邊界），那是 ASan 的領域。

## 偵測：uninitialized memory

```c
int x;             // 沒初始化
if (x > 0) ...     // ❌ 用未初始化值
```

```
==1234== Conditional jump or move depends on uninitialised value(s)
==1234==    at 0x401234: main (uninit.c:5)
```

加 `--track-origins=yes` 顯示「未初始化值是哪變數」：

```
==1234==  Uninitialised value was created by a stack allocation
==1234==    at 0x401222: main (uninit.c:3)
```

## 偵測：memory leak

```c
int *p = malloc(100);
return 0;     // 沒 free
```

```bash
valgrind --leak-check=full --show-leak-kinds=all ./leak
```

```
==1234== HEAP SUMMARY:
==1234==     in use at exit: 100 bytes in 1 blocks
==1234==   total heap usage: 1 allocs, 0 frees, 100 bytes allocated
==1234== 
==1234== 100 bytes in 1 blocks are definitely lost in loss record 1 of 1
==1234==    at 0x4848899: malloc (...)
==1234==    by 0x40117D: main (leak.c:4)
==1234== 
==1234== LEAK SUMMARY:
==1234==    definitely lost: 100 bytes in 1 blocks
==1234==    indirectly lost: 0 bytes in 0 blocks
==1234==      possibly lost: 0 bytes in 0 blocks
==1234==    still reachable: 0 bytes in 0 blocks
==1234==         suppressed: 0 bytes in 0 blocks
```

四種 leak 分類：

| 類別 | 意義 |
|---|---|
| **definitely lost** | 沒指標指向、leak 確認 |
| **indirectly lost** | 主結構 leak 連帶的（如 linked list head leak，所有 node 也 indirectly leak） |
| **possibly lost** | 有指標但指到 block 內部，不是開頭 — 可能 leak 也可能 valid |
| **still reachable** | 程式結束時還有指標指向，沒 free 但「沒丟失」（globals、static cache 等） |

**definitely + indirectly 是必修 bug。possibly 要 case by case 看。still reachable 可以接受**（許多 lib 設計就是這樣）。

## 處理 still reachable 的方法

```c
__attribute__((destructor))
void cleanup(void) {
    free(global_cache);
}
```

加個 destructor 在程式結束時 free。

或乾脆接受 — Linux 程式 exit 時 kernel 自動回收所有記憶體，long-running daemon 才需要在意每個 leak。

## 偵測：bad realloc

```c
char *buf = malloc(100);
free(buf);
buf = realloc(buf, 200);     // ❌ realloc on free'd ptr
```

```
==1234== Invalid free() / delete / delete[] / realloc()
```

## suppression

某些 false positive 或 third-party lib 的 leak（不關你事）—— 寫 suppression：

```bash
valgrind --gen-suppressions=all ./myprog 2> errors.txt
```

把每個 error 轉成 suppression 範本。挑要 suppress 的，存成 `my.supp`：

```
{
   ignore_libfoo_leak
   Memcheck:Leak
   match-leak-kinds: definitely
   fun:malloc
   ...
   fun:libfoo_init
   ...
}
```

下次：

```bash
valgrind --suppressions=my.supp ./myprog
```

## 一個常見場景：long-running daemon 找 leak

```bash
valgrind --leak-check=full --log-file=val.log ./mydaemon
# ... 跑一段時間（比如壓測 10 分鐘）...
# Ctrl-C
less val.log
```

leak 集中在反覆呼叫的 path（每個 request leak 100 byte，跑 10 分鐘累積一堆）。

## 一個常見踩雷：valgrind 爆量訊息

第一次跑很多 lib（OpenSSL 等）會印一堆 conditional jump 訊息。多半是 lib 自己用的 trick，不是你的 bug。

策略：

1. 用 distro 的 `glibc-debuginfo` 等 debug package
2. 用標準 suppression（`/usr/share/valgrind/default.supp`）
3. 加自己的 suppression

## 一個常見踩雷：valgrind 改 timing，race 不重現

valgrind 把程式變慢 10x，原本 race 的兩個 thread 不再 race。這也是為什麼 race 用 helgrind / TSan 而不是 memcheck。

## 一個常見踩雷：valgrind 抓不到 stack-based bug

```c
char buf[10];
buf[10] = 0;          // stack OOB，valgrind 看不到
```

valgrind 對 heap OOB 強，stack OOB 弱。**ASan 是 stack OOB 的標準工具**（Ch 18）。

## 一個常見踩雷：custom allocator 騙過 valgrind

```c
char pool[1024 * 1024];
void *my_alloc(size_t n) { ... }
void my_free(void *p) { ... }
```

自己管 pool valgrind 看不出 — 它只 hook libc malloc / free。

修：加 valgrind macro，告訴它 alloc / free 邊界：

```c
#include <valgrind/memcheck.h>

void *my_alloc(size_t n) {
    void *p = pool + offset;
    VALGRIND_MALLOCLIKE_BLOCK(p, n, 0, 0);
    return p;
}
void my_free(void *p) {
    VALGRIND_FREELIKE_BLOCK(p, 0);
}
```

## valgrind 跟 ASan 比較

| 項目 | valgrind memcheck | ASan |
|---|---|---|
| 不需 recompile | ✅ | ❌（要 -fsanitize=address） |
| 速度 | 10-50x slowdown | 2-3x slowdown |
| heap OOB | ✅ | ✅ |
| stack OOB | ❌ | ✅ |
| global OOB | ❌ | ✅ |
| UAF | ✅ | ✅ |
| uninit read | ✅ | ❌ (MSan 才有) |
| memory leak | ✅ | ✅（exit 時） |
| 對 third-party binary | ✅ | ❌ |

兩者互補。

## 動手練習

**1. 寫 5 種 bug 各一支跑 valgrind**

UAF / double-free / OOB write / OOB read / leak。看每個錯誤訊息能解出什麼。

**2. fix 一個現實程式**

拿你寫過的小程式跑 valgrind，**90% 機會有 still reachable 或 leak**。修。

**3. 試 suppression**

寫個用 OpenSSL 的程式：

```c
#include <openssl/sha.h>
int main() {
    SHA256_CTX ctx;
    SHA256_Init(&ctx);
    return 0;
}
```

跑 valgrind 看到 OpenSSL 內部「leak」。寫 suppression 把它消音。

**4. 對比 valgrind 跟 ASan 速度**

```bash
gcc -O2 myprog.c -o normal
gcc -O2 -fsanitize=address myprog.c -o asan

time ./normal
time valgrind ./normal
time ./asan
```

valgrind 通常慢很多。

**5. memcheck on long-running**

寫個服務每秒 leak 100 byte。跑 valgrind 一分鐘 Ctrl-C 看 leak 累積。

## 自我檢核

- [ ] 用 valgrind 抓過 UAF / double-free / OOB / leak / uninit
- [ ] 解得開「Invalid read / write / free」訊息
- [ ] 知道 4 種 leak 分類（definitely / indirectly / possibly / still reachable）
- [ ] 用過 suppression
- [ ] 知道 valgrind 跟 ASan 的優劣
- [ ] 知道 valgrind 抓不到 stack OOB

下一章看 helgrind / drd —— race condition 專用。

→ [Ch 16 valgrind helgrind / drd](./16-valgrind-helgrind-drd.md)
