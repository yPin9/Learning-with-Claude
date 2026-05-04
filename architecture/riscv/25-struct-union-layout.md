# Ch 25 — Struct / Union Layout：padding、alignment、bitfield 在 64 位元下的行為

> 目標：能手動計算任何 struct 在 LP64D ABI 下的記憶體佈局；理解為什麼 struct layout 是 ABI 的一部分；知道 `__attribute__((packed))` 的代價。

---

## 25.1 Alignment 規則

C 語言的 struct layout 由 **alignment（對齊）** 規則決定。每個型別有自己的 alignment requirement（對齊需求），struct 的每個欄位必須放在其 alignment 的整數倍位址上。

| 型別         | ILP32D 大小 | ILP32D 對齊 | LP64D 大小 | LP64D 對齊 |
|------------|-----------|-----------|---------|---------|
| `char`     | 1         | 1         | 1       | 1       |
| `short`    | 2         | 2         | 2       | 2       |
| `int`      | 4         | 4         | 4       | 4       |
| `long`     | 4         | 4         | 8       | 8       |
| `long long`| 8         | 8         | 8       | 8       |
| `float`    | 4         | 4         | 4       | 4       |
| `double`   | 8         | 8         | 8       | 8       |
| `pointer`  | 4         | 4         | 8       | 8       |

**Struct 的 alignment = 其最大成員的 alignment。**
**Struct 的 size = 最後一個欄位後面 pad 到 struct alignment 的整數倍。**

---

## 25.2 Padding 範例：ILP32D vs LP64D

```c
struct A {
    char a;     // offset ?
    int  b;     // offset ?
    long c;     // offset ?
};
```

**ILP32D（RV32）：**

```
offset 0: char a    (1 byte)
offset 1: [pad 3]   (對齊 int 到 4)
offset 4: int  b    (4 bytes)
offset 8: long c    (4 bytes，long = 4 in ILP32)
total size = 12 bytes，alignment = 4
```

**LP64D（RV64）：**

```
offset 0:  char a    (1 byte)
offset 1:  [pad 7]   (對齊 long 到 8)
  但等一下，int b 只需要 4-byte 對齊：
offset 1:  [pad 3]   (對齊 int 到 4)
offset 4:  int  b    (4 bytes)
offset 8:  long c    (8 bytes，long = 8 in LP64)
total size = 16 bytes，alignment = 8
```

Layout 圖：

```
ILP32D:                        LP64D:
0  [a][pad][pad][pad]          0  [a][pad][pad][pad]
4  [b  b  b  b ]               4  [b  b  b  b ][pad][pad][pad][pad]
8  [c  c  c  c ]               8  [c  c  c  c  c  c  c  c ]
Size = 12                      Size = 16 (16-byte 對齊後不需再 pad)
```

注意：`long` 從 4-byte 變 8-byte 直接讓 struct 從 12 到 16 bytes。這是 64-bit 移植最常見的 size 變化。

---

## 25.3 更複雜的 Padding 案例

```c
struct B {
    char   a;    // 1 byte
    char   b;    // 1 byte
    int    c;    // 4 bytes
    char   d;    // 1 byte
    double e;    // 8 bytes
};
```

Layout（LP64D）：

```
offset 0:  char a
offset 1:  char b
offset 2:  [pad 2]   (對齊 int 到 4)
offset 4:  int c     (4 bytes)
offset 8:  char d
offset 9:  [pad 7]   (對齊 double 到 8)
offset 16: double e  (8 bytes)
offset 24: [pad 0]   (struct alignment = 8，24 % 8 = 0，不需 pad)
total size = 24 bytes
```

如果把欄位順序換一下，可以消除 padding：

```c
struct B_packed_order {
    double e;    // offset 0,  8 bytes
    int    c;    // offset 8,  4 bytes
    char   a;    // offset 12, 1 byte
    char   b;    // offset 13, 1 byte
    char   d;    // offset 14, 1 byte
    char   pad;  // offset 15（若不手動 pad，compiler 會加）
};               // size = 16 bytes（節省 8 bytes）
```

這就是「把大型別放前面」的最佳化技巧。

---

## 25.4 Union 的 Layout

Union 的大小 = 最大成員的大小。Union 的 alignment = 最大 alignment 成員的 alignment。

```c
union U {
    char   a;    // 1 byte, align 1
    int    b;    // 4 bytes, align 4
    double c;    // 8 bytes, align 8
    long   d;    // 8 bytes, align 8
};
// size = 8, alignment = 8
```

所有成員從 offset 0 開始重疊。寫 `u.a` 只寫最低 1 byte，讀 `u.d` 讀全部 8 bytes（其中 7 bytes 是 `a` 沒有設定的垃圾值，除非你用 `memset` 清零）。

---

## 25.5 Bitfield 在 64-bit 下

```c
struct Flags {
    unsigned int valid : 1;
    unsigned int dirty : 1;
    unsigned int rw    : 2;
    unsigned int       : 28;  // 匿名 padding bitfield
};
```

bitfield 的行為：
- `unsigned int` bitfield 的 container 是 `int`（4 bytes）
- 如果你用 `unsigned long`，container 就是 8 bytes（LP64）
- Bitfield 不能跨 container 邊界（如果 fit 不下就移到下一個 container）

**在 64-bit 下的差異：**

```c
struct PageFlags {
    unsigned long valid  : 1;
    unsigned long dirty  : 1;
    unsigned long ppn    : 44;   // 44-bit PPN
    unsigned long rsw    : 2;
    unsigned long pad    : 16;
};
// LP64: container 是 long（8 bytes），可以容納 64 bit
// ILP32: 這個 struct 爆掉了（44+1+1+2+16 = 64 > 32-bit container）
```

**避免跨 ABI 使用 bitfield**：bitfield 的 bit 排列順序是 implementation-defined（little-endian/big-endian CPU 也不同）。做硬體 register mapping 時用 bitfield 很危險。

---

## 25.6 `__attribute__((packed))`

```c
struct __attribute__((packed)) Packed {
    char a;   // offset 0
    int  b;   // offset 1（!!不對齊!!)
    long c;   // offset 5（!!不對齊!!)
};
// size = 1 + 4 + 8 = 13 bytes
```

代價：
1. **Misaligned access**：讀 `b` 需要兩次 4-byte load 然後拼起來，或直接 trap
2. **禁止 compiler 最佳化**：不能用 SIMD、不能 vectorize
3. **取地址後賦值有 UB 風險**：`int *p = &packed.b; *p = 1;` 是 UB

什麼時候可以用：網路封包、序列化格式（只做 byte-by-byte 存取，不 dereference 成員指標）。

---

## 25.7 ABI Stability 的重要性

**Struct layout 是 ABI 的一部分。**

```c
// library v1（ILP32 版）
struct Config {
    int  version;    // offset 0, 4 bytes
    int  flags;      // offset 4, 4 bytes
    long timeout;    // offset 8, 4 bytes (ILP32)
};

// library v2（LP64 版）
struct Config {
    int  version;    // offset 0, 4 bytes
    int  flags;      // offset 4, 4 bytes
    long timeout;    // offset 8, 8 bytes (LP64!!)
};
```

如果你的 app（用 ILP32 ABI）call 一個用 LP64 ABI 編譯的 shared library，傳一個 `struct Config` 進去，`timeout` 欄位的位置不對，library 讀到的是垃圾。

這是為什麼 Linux 的系統呼叫介面用固定大小的型別（`__u32`、`__u64`）而不用 `long`，也是為什麼 32-bit process 不能直接和 64-bit process share 記憶體裡的 struct。

---

## 25.8 診斷工具

```c
#include <stddef.h>
#include <stdio.h>

struct A { char a; int b; long c; };

int main() {
    printf("sizeof(A)   = %zu\n", sizeof(struct A));
    printf("offsetof(b) = %zu\n", offsetof(struct A, b));
    printf("offsetof(c) = %zu\n", offsetof(struct A, c));
    return 0;
}
```

編譯時加 `-m32` 或 `-m64` 看兩個 ABI 的結果。

---

## 自我檢核

- [ ] 能手動計算 `struct { char a; int b; long c; }` 在 LP64D 下的大小和各欄位 offset
- [ ] 知道 union 大小由哪個成員決定
- [ ] 能說出 `__attribute__((packed))` 的兩個主要代價
- [ ] 理解為什麼改 struct layout 會 break shared library ABI
- [ ] 知道 `offsetof` 巨集的用途

→ [Ch 26 — 64 位元 Inline Assembly](26-inline-assembly-rv64.md)
