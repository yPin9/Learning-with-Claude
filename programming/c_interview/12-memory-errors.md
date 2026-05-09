# Ch 12 — 記憶體錯誤完整圖鑑

> 目標：能快速辨識八種記憶體錯誤的根本原因，並知道每種錯誤在工具（ASan / Valgrind）下的輸出特徵。

## 記憶體錯誤分類

```
記憶體錯誤
├── Heap
│   ├── Memory leak          ← 忘記 free
│   ├── Double free          ← free 兩次
│   ├── Use-after-free (UAF) ← free 後繼續用
│   ├── Heap buffer overflow ← 越界寫入 heap
│   └── Off-by-one           ← 邊界條件差一
├── Stack
│   ├── Stack buffer overflow← 本地陣列越界
│   ├── Stack overflow       ← 遞迴太深 / alloca 過大
│   └── Dangling pointer     ← 返回區域變數的地址
└── 其他
    ├── Uninitialized read   ← 讀未初始化的記憶體
    └── Wild pointer         ← 從未初始化的指標
```

---

## 1. Memory Leak

```c
void leak_example(void) {
    char *buf = malloc(1024);
    if (!buf) return;
    process(buf);
    // 忘記 free(buf)！每次呼叫洩漏 1 KB
}

// 更隱蔽的 leak：提前 return
void parse_config(const char *path) {
    FILE *f = fopen(path, "r");
    char *line = malloc(256);
    if (!line) { fclose(f); return; }
    if (!f) {
        // 忘記 free(line)！
        return;
    }
    // ...
    free(line);
    fclose(f);
}
```

**ASan/LSan 輸出**：程式結束後印 `LEAK SUMMARY`。需加 `-fsanitize=leak`（或 `-fsanitize=address`，Linux 預設含 LSan）。

**長時間服務的危害**：server 不重啟，heap 慢慢增長，最終 OOM 被 kill。

---

## 2. Double Free

```c
free(p);
// ... 一些程式碼，可能 p 被重新傳給其他函式 ...
free(p);   // double free → UB → heap corruption
```

glibc 的 tcache 有簡單保護（`tcache_entry->key` 欄位），被二次 free 時觸發 abort。但這只是「讓它 crash」而不是「防止利用」——攻擊者可以在兩次 free 之間覆寫 key。

**ASan 輸出**：
```
ERROR: AddressSanitizer: attempting double-free on 0x602...
previously freed by thread T0 here:
    #0 in free
    #1 in main prog.c:8
```

---

## 3. Use-After-Free (UAF)

```c
int *p = malloc(sizeof(int));
*p = 42;
free(p);
printf("%d\n", *p);  // UAF：可能印 42（值還在），也可能印垃圾
                      // 若 allocator 重用此記憶體，可能印任何東西
```

**為什麼是安全漏洞**：攻擊者可以在 free 後、UAF 存取前，malloc 同大小的記憶體並控制內容，進而讓 UAF 讀到偽造的資料（或偽造的函式指標 / vtable）。

這是近年最常見的 CVE 類型之一（Chrome、Linux kernel 都有 UAF 漏洞）。

---

## 4. Heap Buffer Overflow

```c
char *buf = malloc(8);
strcpy(buf, "AAAAAAAA!");  // 9 bytes + '\0' = 10 bytes，溢位 2 bytes
// 覆寫相鄰 chunk 的 size 欄位或其他使用者資料
```

**影響**：覆寫下一個 chunk 的 header，導致 malloc/free 行為異常（通常是 crash）。在可控輸入下是 heap exploitation 的起點。

**ASan 輸出**：
```
ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602...
WRITE of size 1 at 0x602...
0x602... is located 0 bytes to the right of 8-byte region [0x602..., 0x602...)
```

---

## 5. Stack Buffer Overflow

```c
void foo(const char *input) {
    char buf[8];
    strcpy(buf, input);  // 若 input > 7 chars → 覆蓋 stack frame
    // 覆蓋順序：buf → saved rbp → return address → 任意程式碼執行
}
```

**防護機制**：
- **Stack canary**（`-fstack-protector`）：在 buf 和 return address 之間放隨機值，返回前檢查
- **ASLR**：stack 位址隨機化
- **NX/DEP**：stack 不可執行

這些防護有繞過方法，但有就比沒有好。

---

## 6. Dangling Pointer

```c
// Stack 版：返回區域變數地址
int *get_local(void) {
    int x = 5;
    return &x;   // x 在 return 後被回收，p 懸空
}

// Heap 版：free 後繼續使用
int *p = malloc(sizeof(int));
free(p);
*p = 42;   // UAF：p 是 dangling pointer
p = NULL;  // 養成習慣：free 後立刻清 NULL
```

---

## 7. Uninitialized Read

```c
int arr[5];
printf("%d\n", arr[2]);   // 讀未初始化的 stack 記憶體（殘留值）

char *p = malloc(64);
if (p[0] == 'A') ...      // malloc 不清零，條件取決於殘留資料
```

**為什麼危險**：殘留資料可能是密碼、密鑰、指標——information disclosure 漏洞。記憶體中的殘留資料可能跨 context 洩漏。

**Valgrind 輸出**：
```
Conditional jump or move depends on uninitialised value(s)
  at 0x... (main): prog.c:5
Uninitialised value was created by a heap allocation
  at 0x... (malloc): ...
```

---

## 8. Off-by-One

```c
char buf[10];
for (int i = 0; i <= 10; i++)   // i == 10 時越界！
    buf[i] = 'A';

// 常見於字串操作：
char dst[5];
strncpy(dst, "hello", 5);   // 複製 5 個，但沒有 '\0' 的空間
                              // 後面 strlen(dst) → 讀到越界
```

off-by-one 在 heap 上很危險：覆寫相鄰 chunk 的 PREV_INUSE bit 或整個 size 欄位（null byte overflow），可導致 free 時的合并邏輯出錯，是 heap exploitation 的常見入口。

---

## 診斷工具一覽

| 工具 | leak | UAF/OOB | uninit | 開銷 | 需要重編 |
|------|------|---------|--------|------|----------|
| ASan | ✅ (LSan) | ✅ | ❌ | ~2× | 是 |
| Valgrind Memcheck | ✅ | ✅ | ✅ | ~10× | 否 |
| MSan | ❌ | ❌ | ✅ | ~3× | 是 |
| UBSan | ❌ | 部分 | ❌ | <1.1× | 是 |

Ch 14 詳細說明工具的實際用法。

---

## 自我檢核

- [ ] 能說出 UAF 被攻擊者利用的原理（控制 free 後重分配的內容）
- [ ] 知道 heap overflow 和 stack overflow 的防護機制不同（canary 只保護 stack）
- [ ] 知道 off-by-one null byte 在 heap 上的危害（PREV_INUSE 被清除）
- [ ] 知道 Valgrind 可以抓 uninitialized read，ASan 不行

→ [Ch 13 自製 Memory Pool](./13-memory-pool.md)
