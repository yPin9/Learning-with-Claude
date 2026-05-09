# Ch 4 — struct / union / bitfield 記憶體佈局

> 目標：能計算任意 struct/union 的大小和成員偏移，理解 bitfield 的限制，以及 union 的合法用途。

## struct 佈局規則

成員按宣告順序放，編譯器在成員間插入 **padding** 滿足對齊需求：

```
對每個成員：插入 padding 使偏移對齊到 alignof(member)，再放成員
struct 尾端：插入 padding 使 sizeof(struct) 是最大對齊的倍數
```

```c
struct A {
    char  c;   // offset 0, size 1
               // 3 bytes padding（讓 i 對齊到 4）
    int   i;   // offset 4, size 4
    char  c2;  // offset 8, size 1
               // 3 bytes padding（sizeof 對齊到 4）
};
// sizeof(struct A) == 12

struct B {
    int   i;   // offset 0, size 4
    char  c;   // offset 4, size 1
    char  c2;  // offset 5, size 1
               // 2 bytes padding
};
// sizeof(struct B) == 8
```

**同樣的成員，不同順序，大小不同。把大成員放前面可以減少 padding。**

---

## offsetof 巨集

```c
#include <stddef.h>

struct Foo { int a; char b; double d; };
// a: offset 0
// b: offset 4
// d: offset 8（double 需要 8-byte 對齊）
// sizeof == 16（1 byte b + 3 byte padding + double）

printf("%zu\n", offsetof(struct Foo, d));  // 8
printf("%zu\n", sizeof(struct Foo));        // 16
```

---

## `__attribute__((packed))` — 消除 padding

```c
struct __attribute__((packed)) P {
    char c; int i; char c2;
};
// sizeof == 6，無 padding
```

代價：
- **ARM/MIPS**：存取未對齊成員觸發硬體例外或效能懲罰
- **指標取址後 dereference** 是 UB（strict aliasing + alignment）：

```c
struct __attribute__((packed)) P { char c; int i; };
struct P p;
int *ip = &p.i;   // ip 可能是 odd address
*ip = 42;         // UB：未對齊存取
memcpy(&p.i, &(int){42}, sizeof(int));  // 安全做法
```

packed 主要用於網路封包解析，要格外小心。

---

## 彈性陣列成員（Flexible Array Member，C99）

```c
struct Packet {
    uint32_t len;
    uint8_t  payload[];   // 必須是最後一個成員
};

// 分配 payload 10 bytes：
struct Packet *pkt = malloc(sizeof(struct Packet) + 10);
pkt->len = 10;
// sizeof(struct Packet) == 4，彈性成員不計入
```

比舊式的 `uint8_t payload[1]` hack 更乾淨，是 C99 的標準方式。

---

## union：共享記憶體

所有成員共享同一塊記憶體，大小等於最大成員（含尾端 padding）：

```c
union Val {
    int    i;   // 4 bytes
    float  f;   // 4 bytes
    double d;   // 8 bytes
};
// sizeof(union Val) == 8（最大成員 double）
```

**合法用途一：透過 union 做 type punning（C 標準明確允許）**

```c
union FloatBits {
    float    f;
    uint32_t bits;
};

union FloatBits fb = { .f = -0.0f };
printf("sign bit: %u\n", (fb.bits >> 31) & 1);  // 1
// 讀取 f 寫入的 bytes 當作 uint32_t 解讀，C 標準合法
```

注意：C++ 裡這樣做是 UB（C++ 不像 C 明確允許 union type punning）。

**合法用途二：tagged union（模擬泛型）**

```c
typedef enum { TYPE_INT, TYPE_FLOAT, TYPE_STR } VType;
typedef struct {
    VType type;
    union {
        int   i;
        float f;
        char *s;
    };
} Variant;

Variant v = { .type = TYPE_INT, .i = 42 };
```

---

## bitfield

```c
struct Flags {
    unsigned int enable : 1;   // 1 bit
    unsigned int mode   : 3;   // 3 bits，值範圍 0-7
    unsigned int level  : 4;   // 4 bits，值範圍 0-15
};
// 三個 bitfield 合在一個 unsigned int 裡，sizeof 可能是 4
```

**三個面試必知限制**：

1. **不能取址**：`&flags.enable` 編譯錯誤（bitfield 不是可定址物件）
2. **bit 排列順序是實作定義**：little-endian 和 big-endian 平台上 bit 0 可能在不同位置
3. **跨 storage unit 行為實作定義**：不要讓 bitfield 跨越 `unsigned int` 邊界

```c
struct Bad {
    uint8_t  a : 4;
    uint16_t b : 4;   // 跨越 storage unit！行為實作定義
};
```

嵌入式常見的 register map 用法（但可移植性差）：

```c
typedef union {
    uint8_t byte;
    struct { uint8_t b0:1; uint8_t b1:1; uint8_t b2:1; uint8_t b3:1;
             uint8_t b4:1; uint8_t b5:1; uint8_t b6:1; uint8_t b7:1; } bits;
} Reg8;

Reg8 r = { .byte = 0xA5 };
printf("%u\n", r.bits.b0);  // x86 LE：1（最低位）
```

---

## 動手練習（先算後跑）

```c
// x86-64 System V ABI，答案各是多少？
struct S1 { char a; int b; char c; };
struct S2 { int b; char a; char c; };
struct S3 { double d; char c; int i; };
struct S4 { char a; char b; short s; int i; };
union  U1 { int i; char c[5]; };
```

答案：12、8、16、8、8。

## 自我檢核

- [ ] 能手算含 padding 的 struct 大小和各成員 offset
- [ ] 知道把大成員放前面可以減少 padding
- [ ] 知道 union type punning 在 C 是合法的（不像 C++）
- [ ] 能說出 bitfield 的三個限制

→ [Ch 5 Alignment 與 Padding](./05-alignment-padding.md)
