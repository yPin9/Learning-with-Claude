# Ch 11 — malloc / calloc / realloc / free 內部機制

> 目標：理解 glibc ptmalloc2 的 chunk 結構與 bins 機制，能解釋 malloc 的時間複雜度，以及 double free 為什麼會破壞 heap 結構。

## glibc Heap 的基本單位：chunk

malloc 不是每次都去問 OS 要記憶體。它維護一個 **heap arena**，批量從 OS 拿（`brk` 或 `mmap`），再切割給使用者。

每塊 malloc 管理的記憶體叫 **chunk**：

```
   高地址
   ┌───────────────────────────────┐
   │  prev_size (8 bytes)          │  ← 只在前一個 chunk free 時有效
   │  size      (8 bytes)          │  ← chunk 總大小 | flags（低 3 bits）
   ├───────────────────────────────┤  ← malloc() 回傳的指標指這裡
   │  user data                    │
   │  ...                          │
   └───────────────────────────────┘
   低地址
```

`size` 欄位低 3 bits 是旗標：

| bit | 名稱 | 說明 |
|-----|------|------|
| bit 0 | PREV_INUSE | 前一個 chunk 是否在使用中 |
| bit 1 | IS_MMAPPED | 此 chunk 是否用 mmap 分配 |
| bit 2 | NON_MAIN_ARENA | 是否屬於非主 arena |

chunk 大小永遠是 16 bytes 對齊（64-bit 系統），所以最低 4 bits 可以放旗標。

---

## Bins：free chunk 的管理

free 掉的 chunk 進入對應的 bin，等待被下次 malloc 重用：

```
fastbins     [0x20][0x30][0x40][0x50][0x60][0x70][0x80]
              單向鏈表，LIFO，不合并相鄰 chunks

tcache       [0x20..0x410]  per-thread cache，比 fastbin 更快
              glibc 2.26+ 加入，每個 size class 最多 7 個

unsorted bin  雙向鏈表，新 free 的 chunk 先丟這裡

small bins   [0x20..0x3f0]  16-byte 間距，雙向鏈表
large bins   [>=0x400]      不固定大小，按大小排序
```

### malloc 的查找順序

1. tcache（若 size 符合且有緩存）
2. fastbins（size <= 0x80）
3. unsorted bin（嘗試精確匹配或劃分）
4. small bins（精確大小）
5. large bins（最近最佳匹配）
6. top chunk（heap 頂端，直接切割）
7. 新建 arena / mmap

---

## calloc vs malloc

```c
// malloc：分配但不清零
void *p = malloc(100);
// 內容是上一次 free 的殘餘資料！

// calloc：分配並清零
void *q = calloc(10, sizeof(int));
// q 指向 10 個全零的 int

// calloc 的速度優化：
// 從 OS 剛拿到的記憶體（top chunk 擴展），OS 保證清零
// 這種情況 calloc 可以跳過 memset
```

`calloc` 簽章 `calloc(nmemb, size)` 有內建溢位保護——先檢查 `nmemb * size` 是否溢位，不像 `malloc(nmemb * size)` 你要自己查。

---

## realloc 的三種情況

```c
void *p = malloc(64);
void *p2 = realloc(p, 128);  // 可能發生三件事：
```

1. **原地擴展**：若後面緊接的 chunk 是 free 且夠大 → 合并，不移動資料，O(1)
2. **搬遷**：原地擴展不行 → malloc 新空間 + memcpy + free 舊空間，O(n)
3. **縮小**：切掉尾端，多出來的釋放回 bin

**常見錯誤**：

```c
// 危險：realloc 可能回傳 NULL（記憶體不足），舊記憶體未被 free 但 p 被覆寫成 NULL
p = realloc(p, new_size);   // 若回傳 NULL：p = NULL，舊記憶體 leak！

// 正確做法：
void *tmp = realloc(p, new_size);
if (!tmp) {
    free(p);   // 處理錯誤，p 仍然有效，這裡決定要不要 free
    return NULL;
}
p = tmp;
```

---

## free 做什麼

```c
free(p);
```

1. 計算 chunk 邊界（`p - 2*sizeof(size_t)`）
2. 設定 size 欄位的 PREV_INUSE = 0
3. 嘗試合并前後相鄰的 free chunk（consolidation）
4. 把 chunk 丟進對應 bin（tcache → fastbin → unsorted）

**double free 的危險**：

```c
free(p);
free(p);   // 把同一個 chunk 插入 bin 兩次
           // tcache 有簡單保護（檢查 tcache key），但舊版/fastbin 沒有
           // 形成環狀鏈表 → 下次 malloc 能拿到 overlap chunk
           // 這是 heap exploitation 的基本技術（fastbin dup / tcache dup）
```

---

## 面試常見問答

**Q：`malloc(0)` 回傳什麼？**

實作定義（implementation-defined）。glibc 回傳非 NULL（一個最小 chunk 的指標），但 dereference 它是 UB。C 標準說「可以回傳 NULL 或可以被 free 的非 NULL 指標」。

**Q：free 之後指標的值變嗎？**

不變——`free` 不修改你的指標，它還是指向原來的地址。這就是 dangling pointer 的來源。好習慣：`free(p); p = NULL;`

**Q：malloc 的時間複雜度？**

一般情況 O(1)（tcache/fastbin/smallbin 精確匹配），最壞情況 O(log n)（large bin 搜索）。

**Q：malloc 是執行緒安全的嗎？**

是。glibc 的 malloc 有 mutex 保護 arena（也是它比自訂 allocator 慢的原因之一）。多執行緒下每個執行緒有 per-thread arena，降低競爭。

---

## 自我檢核

- [ ] 能畫出 chunk 的結構（prev_size / size / user data）
- [ ] 知道 tcache 是 per-thread 的，比 fastbin 快
- [ ] 能解釋 calloc 在某些情況下不需要 memset
- [ ] 知道 `p = realloc(p, n)` 的記憶體洩漏陷阱
- [ ] 知道 double free 在 ptmalloc 的後果（bin 環狀鏈表）

→ [Ch 12 記憶體錯誤完整圖鑑](./12-memory-errors.md)
