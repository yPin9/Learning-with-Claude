# Ch 5 — Alignment 與 Padding

> 目標：理解對齊需求的硬體來源，能計算任意結構的 padding，以及手動控制對齊。

## 為什麼需要對齊

CPU 讀取記憶體時，偏好從特定邊界開始的位址：

```
位址:  0   1   2   3   4   5   6   7
      [ int @ 0  ][ int @ 4  ]   ← 對齊：一次 load
              [ int @ 2      ]   ← 未對齊：跨越 4-byte 邊界
```

- **x86**：硬體處理未對齊，有效能懲罰（額外記憶體匯流排週期）
- **ARM/MIPS**：觸發對齊例外（Alignment Fault），可能 crash
- **SIMD**：SSE/AVX 嚴格要求 16/32-byte 對齊，否則直接 segfault

---

## 各型別的對齊需求（x86-64 System V ABI）

| 型別 | sizeof | alignof |
|------|--------|---------|
| `char` | 1 | 1 |
| `short` | 2 | 2 |
| `int` | 4 | 4 |
| `long` | 8 | 8 |
| `float` | 4 | 4 |
| `double` | 8 | 8 |
| `pointer` | 8 | 8 |
| `long double` | 16 | 16 |

struct 的 `alignof` = 成員中最大的 `alignof`。

---

## Padding 計算完整算法

```
current_offset = 0
for each member:
    padding = (alignof(member) - current_offset % alignof(member)) % alignof(member)
    current_offset += padding          # 插入 padding
    member_offset   = current_offset
    current_offset += sizeof(member)   # 放下成員

struct_size = current_offset
# 尾端 padding：使 struct_size 是 max_align 的倍數
struct_size = ROUND_UP(struct_size, max_align)
```

範例：

```c
struct S {
    char   a;    // offset 0, align 1 → 不需 pad
    double d;    // offset 1, align 8 → pad 7 → offset 8
    char   b;    // offset 16, align 1 → 不需 pad
    int    i;    // offset 17, align 4 → pad 3 → offset 20
                 // 尾端：24 到 24（24 是 8 的倍數）
};
// sizeof(struct S) == 24
```

---

## `_Alignof` 和 `_Alignas`（C11）

```c
printf("%zu\n", _Alignof(double));  // 8
printf("%zu\n", _Alignof(int));     // 4

// 強制對齊到 64 bytes（快取行大小）：
struct _Alignas(64) CacheLine {
    int data[16];  // 保證從快取行起始
};

// gcc 方式：
struct __attribute__((aligned(64))) CacheLineGCC {
    int data[16];
};
```

`_Alignas` 只能放大對齊（不能比自然對齊還小）。

---

## 動態記憶體的對齊

標準 `malloc` 保證返回的記憶體對齊到 `max_align_t`（通常是 16 bytes）。SIMD 需要更嚴格的對齊：

```c
// C11 標準方式（大小必須是 alignment 的倍數）：
void *p = aligned_alloc(64, 1024);
free(p);

// POSIX 方式：
void *p;
posix_memalign(&p, 64, 1024);
free(p);

// 棧上對齊（C11）：
char _Alignas(64) buf[1024];
```

---

## False Sharing（快取行競爭）

兩個執行緒各自更新不同變數，但它們在同一個 64-byte 快取行：

```c
// 壞：counter_a 和 counter_b 在同一快取行
struct { int counter_a; int counter_b; } shared;

// 好：各自佔一個快取行
struct { int counter_a; char _pad[60]; int counter_b; } padded;
// 或：
struct { _Alignas(64) int counter_a; _Alignas(64) int counter_b; } aligned;
```

修改 `counter_a` 會使 `counter_b` 的快取失效（即使它沒被改），導致另一個執行緒必須重新載入——這就是 false sharing，詳見 Ch 24。

---

## 節省記憶體：成員重排技巧

```c
// 糟糕：32 bytes
struct Bad { char a; double d; char b; double e; };
// a(1)+pad(7)+d(8)+b(1)+pad(7)+e(8) = 32

// 優化：24 bytes（把相同大小的放在一起）
struct Good { double d; double e; char a; char b; };
// d(8)+e(8)+a(1)+b(1)+pad(6) = 24
```

規則：**大成員放前面**，相同大小成員聚在一起。

---

## 面試題：不算 sizeof，直接問你

```c
struct A { char c; int i; char d; };
struct B { int i; char c; char d; };
struct C { double d; char c; int i; };

// sizeof：12, 8, 16
```

解釋 C：`d(8) + c(1) + pad(3) + i(4) = 16`。

---

## 自我檢核

- [ ] 能套用算法計算任意 struct 的 sizeof 和每個成員的 offset
- [ ] 知道 `aligned_alloc` 和 `posix_memalign` 的差異
- [ ] 能解釋 false sharing 的機制
- [ ] 知道 `__attribute__((packed))` 和 `__attribute__((aligned(N)))` 的差異和代價

Part 1 結束。接下來進入最危險的領域：未定義行為。

→ [Ch 6 未定義行為（UB）全圖](./06-undefined-behavior.md)
