# Ch 14 — Valgrind 與 AddressSanitizer 實戰

> 目標：學會用兩個最主要的記憶體偵錯工具，能看懂輸出並定位到根本原因。

## AddressSanitizer（ASan）

### 原理

ASan 在編譯時插入 instrumentation code。每個 malloc/free 的記憶體前後都加上 **紅區（redzones）**，並維護一個 **shadow memory** 追蹤每個 byte 的狀態。

```
shadow memory：1 byte 對應 8 bytes 實際記憶體
0x00 = 全部可存取
0xfa = heap left redzone
0xfb = heap right redzone
0xfd = heap freed（use-after-free 能偵測到）
0xf1 = stack left redzone
0xf8 = stack right redzone
```

存取記憶體前，ASan 插入的程式碼查 shadow map；違規就 abort 並印報告。

### 使用方式

```bash
gcc -fsanitize=address -g -O1 prog.c -o prog
./prog

# 同時開 leak 和 UB：
gcc -fsanitize=address,undefined -g -O1 prog.c -o prog

# 只偵測 leak（不加入 ASan 的 redzone 機制）：
gcc -fsanitize=leak -g prog.c -o prog
```

`-O1` 比 `-O0` 好：稍微優化讓 call stack 更接近原始 code，但不要開 `-O2`（inline 過多，報告難讀）。

### 輸出解讀

```
=================================================================
==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602000000018
READ of size 4 at 0x602000000018 thread T0
    #0 0x4011a2 in main prog.c:8          ← 這行觸發錯誤
    #1 0x7f... in __libc_start_main

0x602000000018 is located 0 bytes to the right of 8-byte region
  [0x602000000010, 0x602000000018)
allocated by thread T0 here:
    #0 0x... in malloc
    #1 0x4011... in main prog.c:6         ← 這裡分配的

SUMMARY: AddressSanitizer: heap-buffer-overflow prog.c:8 in main
```

讀法：
1. 錯誤類型（`heap-buffer-overflow`）
2. `READ/WRITE of size N`：哪裡出問題、讀還是寫
3. Call stack（要 `-g` 才有行號）
4. `allocated by`：這塊記憶體在哪分配
5. UAF 時還有 `previously freed by`

### 常見錯誤類型

| ASan 錯誤名稱 | 原因 |
|--------------|------|
| `heap-buffer-overflow` | malloc 後越界讀/寫 |
| `stack-buffer-overflow` | 局部陣列越界 |
| `heap-use-after-free` | free 後存取 |
| `double-free` | free 兩次 |
| `alloc-dealloc-mismatch` | malloc/delete 或 new/free 混用 |
| `global-buffer-overflow` | 全域陣列越界 |

---

## LeakSanitizer（LSan）

Linux 上 ASan 預設包含 LSan。也可以單獨用：

```bash
gcc -fsanitize=leak -g prog.c -o prog
./prog
```

輸出：
```
==12345==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 1024 byte(s) in 1 object(s) allocated from:
    #0 in malloc
    #1 in main prog.c:5

SUMMARY: LeakSanitizer: 1024 byte(s) leaked in 1 allocation(s).
```

環境變數控制：
```bash
LSAN_OPTIONS=detect_leaks=0 ./prog          # 關閉 leak 偵測（只看其他 ASan 錯誤）
ASAN_OPTIONS=detect_leaks=1:verbosity=1     # 更詳細輸出
```

---

## Valgrind Memcheck

Valgrind 不需要重新編譯，速度慢約 10–20 倍。好處是能抓 **uninitialized read**，ASan 做不到。

```bash
valgrind \
  --leak-check=full \
  --show-leak-kinds=all \
  --track-origins=yes \
  --error-exitcode=1 \
  ./prog
```

重要 flag：

| Flag | 說明 |
|------|------|
| `--leak-check=full` | 顯示所有 leak 的 call stack |
| `--show-leak-kinds=all` | 包含 indirect / possible leak |
| `--track-origins=yes` | 追蹤 uninitialized value 的來源（更慢但更有用）|
| `--error-exitcode=1` | 有錯誤時回傳 non-zero（CI 流水線用）|

### Valgrind 常見輸出

```
Invalid read of size 4         ← 讀了無效地址（越界/UAF）
Invalid write of size 1        ← 寫了無效地址
Conditional jump or move depends on uninitialised value(s)
Use of uninitialised value of size 8

definitely lost: 1,024 bytes in 1 blocks    ← 確定的 leak（無任何指標指向）
indirectly lost: 32 bytes in 1 blocks       ← 因為 definitely lost 物件帶走的
possibly lost: 128 bytes in 2 blocks        ← 有指標但 offset 非零
still reachable: 64 bytes in 1 blocks       ← 有指標但沒 free（不一定是 bug）
```

---

## UndefinedBehaviorSanitizer（UBSan）

```bash
gcc -fsanitize=undefined -g prog.c -o prog
./prog
```

能抓：有號整數溢位、null pointer dereference、misaligned access、out-of-bound shift、invalid enum value 等。

輸出：
```
prog.c:10:15: runtime error: signed integer overflow:
  2147483647 + 1 cannot be represented in type 'int'
prog.c:5:3: runtime error: null pointer passed as argument 1, which is declared to never be null
```

推薦組合：
```bash
gcc -fsanitize=address,undefined -g -O1 prog.c -o prog
```

---

## 工具選擇指南

```
需要快速 CI 測試？             → ASan + UBSan（2× 開銷，夠快）
需要抓 uninitialized read？   → Valgrind --track-origins=yes
production binary 無法重編？  → Valgrind（不需重編）
只想看 leak？                 → gcc -fsanitize=leak（比完整 ASan 開銷小）
嵌入式 / 沒有 libc？          → 自訂 guard page 或 allocator 加 magic number
```

---

## 實戰：一個有三個 bug 的程式

```c
// buggy.c
#include <stdlib.h>
#include <string.h>

int main(void) {
    char *p = malloc(10);
    strcpy(p, "Hello, World!");   // heap overflow（13 + 1 bytes > 10）
    
    int arr[5];
    int x = arr[3];               // uninitialized read（Valgrind 抓得到）
    
    char *q = malloc(32);
    free(q);
    free(q);                       // double free
    
    return 0;
}
```

```bash
gcc -fsanitize=address,undefined -g -O1 buggy.c -o buggy
./buggy
# ASan 會在 strcpy 那行 abort 並印 heap-buffer-overflow

valgrind --track-origins=yes ./buggy
# Valgrind 會找到 uninitialized read（ASan 找不到）
```

---

## 自我檢核

- [ ] 能說出 ASan shadow memory 的原理（1 byte shadow 對應 8 bytes 記憶體）
- [ ] 知道 LSan 在 Linux ASan 下預設包含
- [ ] 能讀懂 ASan 的 `heap-buffer-overflow` 報告，找到分配點和錯誤點
- [ ] 知道 Valgrind 比 ASan 慢但能抓 uninitialized read
- [ ] 知道 `--track-origins=yes` 追蹤未初始化值的來源

→ [Ch 15 函式指標與 Callback 模式](./15-function-pointers.md)
