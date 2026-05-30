# Ch 9 — 型別系統

> **目標**：搞懂 GDB 怎麼理解型別、怎麼用 `ptype` / `whatis` 探查未知型別、怎麼處理 struct / union / enum / typedef / bitfield / 函式指標，以及怎麼用轉型把一段未知記憶體「套上」結構來看。型別是 `print` 能印出人話的根本。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`。

## 為什麼型別這麼關鍵

Ch 7 你看到 `print p` 印出 `{x = 3, y = 7}`——GDB 怎麼知道 `p` 是個有 `x`、`y` 兩個 int 欄位的結構？因為 DWARF 裡完整記錄了型別資訊。**型別是把「一串 byte」翻譯成「有意義的值」的字典。** 同樣 8 個 byte，當 `int64_t` 是一個數、當兩個 `int` 是一對、當 `struct Point` 是座標、當指標是位址。沒有型別，記憶體只是 byte。

逆向工程的一大半工作，就是「替未知記憶體推斷/套上正確型別」。把 GDB 的型別工具練熟，這件事會輕鬆很多。

## `whatis` vs `ptype`：兩種探查深度

```c
// type_demo.c — gcc -g -O0
typedef struct Node { int val; struct Node *next; } Node;
typedef unsigned long u64;
enum Color { RED, GREEN=5, BLUE };
union Bits { int i; float f; unsigned char b[4]; };

struct Packet {
    unsigned version : 4;     // bitfield
    unsigned flags   : 12;
    enum Color color;
    int (*handler)(int);      // 函式指標
};

Node n = {42, 0};
enum Color c = GREEN;
struct Packet pkt;
int main(void){ return 0; }
```

```
(gdb) whatis n              # 淺：只給型別名
type = Node
(gdb) whatis c
type = enum Color
(gdb) ptype n              # 深：展開完整定義
type = struct Node {
    int val;
    struct Node *next;
}
(gdb) ptype Node           # 對 typedef 名也行
type = struct Node {
    int val;
    Node *next;
}
```

- **`whatis`**：只告訴你「型別叫什麼」，**不展開**，typedef 也只剝一層。
- **`ptype`**：展開到底——struct 的所有欄位、enum 的所有值、typedef 背後的真身。

口訣：想知道「這是什麼型別」用 `whatis`；想知道「這型別長怎樣」用 `ptype`。

## 探查 enum / union / bitfield

```
(gdb) ptype enum Color
type = enum Color {RED, GREEN = 5, BLUE}    # 看到每個列舉值（含跳號）
(gdb) print c
$1 = GREEN                                   # enum 印成名字，不是數字 5！
(gdb) print (int)c
$2 = 5                                        # 要看底層數字得轉型

(gdb) ptype union Bits
type = union Bits {
    int i;
    float f;
    unsigned char b[4];
}                                            # union 所有成員共用記憶體

(gdb) ptype struct Packet
type = struct Packet {
    unsigned int version : 4;                # bitfield 寬度顯示出來
    unsigned int flags : 12;
    enum Color color;
    int (*handler)(int);                     # 函式指標型別完整呈現
}
```

GDB 對這些複雜型別都有完整支援——enum 印名字、union 顯示所有成員、bitfield 標寬度、函式指標給簽章。這些全靠 DWARF。

## 轉型：把未知記憶體「套」上型別

逆向 / 處理 `void*` 時最重要的技巧：用轉型把一段裸記憶體當成某型別來看。

```
(gdb) print *(struct Node *)0x5555555592a0    # 把這位址當 Node 看
$3 = {val = 42, next = 0x0}
(gdb) print ((struct Packet *)buf)->color     # 把 buf 當 Packet 取欄位
(gdb) print *(u64 *)$sp                        # 把 stack top 當 u64 讀
(gdb) print (char *)ptr                        # 把它當 C 字串
```

這招在以下情境是命脈：

- `void *data` 你知道實際指向某結構
- 從暫存器拿到一個位址（`$rax`），想當結構看：`print *(struct Foo *)$rax`
- 逆向時推斷出某段 heap 是某結構

練習 A 的「強闖」、練習 B 的「資料結構偵探」、Final Project 的 heap 分析都靠它。

## 型別也是值：在表示式裡用型別

```
(gdb) print sizeof(struct Packet)        # 型別的大小
$4 = 24
(gdb) print sizeof(Node)
$5 = 16
(gdb) whatis &n                          # 取址後的型別
type = Node *
(gdb) whatis n.next                      # 欄位的型別
type = Node *
(gdb) whatis main                        # 函式的型別 = 簽章
type = int (void)
```

`whatis 表達式` 對任何表示式都能回答「這算出來是什麼型別」，debug 複雜表示式時很有用。

## 探查記憶體佈局：offset 與對齊

想知道結構欄位各在哪個 offset（debug 序列化、ABI、padding 問題時）：

```
(gdb) ptype/o struct Packet              # GDB 10+：顯示每個欄位的 offset 與 size！
/* offset    |  size */  type = struct Packet {
/*    0: 0   |     4 */    unsigned int version : 4;
/*    0: 4   |     4 */    unsigned int flags : 12;
/*    4      |     4 */    enum Color color;
/*    8      |     8 */    int (*handler)(int);
                          /* total size (bytes):   16 */
}
```

`ptype/o` 是 GDB 10+ 的好東西——直接看到 offset、size、padding，再也不用手算。debug 結構對齊、跨語言 FFI、wire format 不一致時救命。

## typedef 的剝洋蔥

```
(gdb) whatis u64
type = unsigned long          # whatis 剝一層
(gdb) ptype u64
type = unsigned long          # 這層之後就是基本型別

# 多層 typedef
(gdb) whatis some_alias       # 只剝一層
(gdb) ptype some_alias        # 一路剝到底層真身
```

當你被一堆 `typedef`（特別是 kernel / 大型專案常見的 `__u32`、`size_t`、`pthread_t`）搞糊塗時，`ptype` 剝到底看真身。

## 踩雷集錦

1. **`whatis` 不展開害你以為沒資訊**：`whatis n` 只給 `Node`，要看欄位得用 `ptype`。別怪 GDB 沒資訊。
2. **enum 印成名字找不到數值**：`print c` 給 `GREEN`，要數字得 `print (int)c`。反之 `print (enum Color)5` 把數字轉回名字。
3. **轉型轉錯結構**：`print *(struct Wrong *)ptr` 不會報錯，會印出一堆垃圾——GDB 照你說的型別硬解。轉型結果不合理時，先懷疑型別猜錯。
4. **bitfield 不能取址**：`print &pkt.version` 會失敗，因為 bitfield 沒有獨立位址。這是 C 的限制，不是 GDB 的。
5. **union 印出來「只有一個成員對」**：union 所有成員共用記憶體，同時只有一個有意義的值，其他是同一段 byte 的不同詮釋。`print bits` 全印，要自己判斷哪個成員當前有效。
6. **`ptype` 對 opaque 型別給不出細節**：如果某結構在當前編譯單元只有前向宣告（`struct Foo;`）沒定義，`ptype` 只能說 `struct Foo { <incomplete type> }`。要有定義它的編譯單元的 DWARF 才行。

## 進階：再往深一層

- **`ptype /o` 的進階**：配合 `set print type ...` 系列開關，控制要不要顯示 methods（C++）、typedefs。
- **C++ 型別**：`ptype` 一個 C++ class 會列出成員、方法、繼承、vtable 資訊。Ch 29 專門講 C++ 的型別 debug（template、virtual、mangling）。
- **動態型別（RTTI）**：對多型的 C++ 物件，`set print object on` 讓 GDB 用 RTTI 判斷「實際」型別而非宣告型別。debug 多型時關鍵（Ch 29）。
- **`maint print type`**：maintenance 指令，看 GDB 內部對某型別的完整表示——debug 「為什麼 GDB 把這型別搞錯」時用。
- **自訂型別顯示**：type printer（Ch 28 的 Python xmethod 鄰居）可以讓複雜 typedef 顯示成你要的樣子。
- **DWARF 的型別表示**：每個型別在 DWARF 是一棵 DIE（debugging information entry）樹，`ptype` 就是在走這棵樹。Ch 38 會直接看這些 DIE。

## 動手練習

1. 對 `type_demo.c` 的每個型別（Node、enum Color、union Bits、struct Packet）各做一次 `whatis` 和 `ptype`，比較深淺。
2. 用 `ptype/o struct Packet` 看欄位 offset，手算驗證 padding，理解為什麼 `sizeof` 是那個數。
3. 用 `print (enum Color)5` 把數字轉成 enum 名、`print (int)BLUE` 把名轉回數字。
4. `malloc` 一塊記憶體寫入一個 Node 的內容，然後用 `print *(struct Node *)那位址` 把它「套」回 Node 印出來。
5. 故意 `print *(union Bits *)&some_float`，觀察同一段 byte 當 int / float / byte[] 的不同詮釋。

## 本章重點整理

- 型別是把 byte 翻譯成有意義值的字典；`print` 能印人話全靠 DWARF 的型別資訊。
- `whatis`（淺，只給型別名，剝一層 typedef）vs `ptype`（深，展開完整定義）。
- enum 印名字（`(int)` 看數值）、union 共用記憶體、bitfield 標寬度——GDB 全支援。
- 轉型 `*(T *)addr` 把未知記憶體套上型別，是逆向與處理 `void*` 的核心技巧。
- `ptype/o` 顯示欄位 offset/size/padding（GDB 10+），debug 對齊與 wire format 必備。

## 自我檢核

- [ ] `whatis` 和 `ptype` 差在哪？想看 struct 所有欄位用哪個？
- [ ] 怎麼把暫存器 `$rax` 裡的位址當成 `struct Foo *` 來看它指向的內容？
- [ ] enum 變數 `print` 出名字，怎麼看它底層的整數值？
- [ ] 想知道結構某欄位在 offset 幾、有沒有 padding，用什麼指令？
- [ ] 為什麼 `print *(struct Wrong *)ptr` 不報錯卻印出垃圾？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Examining the Symbol Table](https://sourceware.org/gdb/current/onlinedocs/gdb/Symbols.html)**
  - **讀哪裡**：`whatis`、`ptype`（含 `/o` flag）那幾段。
  - **和本章的關聯**：本章兩大指令的完整選項。

- **[GDB Manual: Print Settings — type printing](https://sourceware.org/gdb/current/onlinedocs/gdb/Print-Settings.html)**
  - **讀哪裡**：`set print object`、`set print type` 系列。
  - **和本章的關聯**：控制型別顯示細節；`set print object on` 是 C++ 多型 debug 關鍵。

### 部落格 / 文章

- **[Understanding DWARF types](https://www.tweag.io/blog/2022-05-31-debugger-deep-dive/)** 類的 DWARF 型別解析文
  - **這篇說什麼**：型別在 DWARF 裡怎麼以 DIE 表示。
  - **和本章的關聯**：`ptype` 走的就是這棵 DIE 樹；Ch 38 的預習。

下一章從「單一變數的值」拉高到「整個函式呼叫鏈」：stack 與 frame，看程式是怎麼一層層呼叫進來的。

→ [Ch 10 Stack 與 frame](./10-stack-and-frames.md)
