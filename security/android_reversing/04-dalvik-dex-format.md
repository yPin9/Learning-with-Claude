# Ch 4 — Dalvik bytecode 與 DEX 格式深挖

> **目標**：把 `classes.dex` 從 header 一路拆到 map_list，搞懂它為什麼是這個佈局、每個區塊裝什麼、它們怎麼互相索引。你要能回答：為什麼 DEX 用**暫存器式**虛擬機而不是 JVM 的**堆疊式**？一條 Dalvik 指令在記憶體裡怎麼編碼？為什麼字串、型別、方法都各自集中成一個 pool？學完這章，Ch 5 的 smali 對你就不再是天書——你會知道每一行 smali 背後對應 DEX 裡的哪個結構。

> **環境**：本章所有 DEX 二進位解析、ULEB128 編碼、header 完整性欄位（adler32/SHA-1）、bytecode 指令編解碼，都用 **Python 3** 在本機**實際跑出**，輸出標「**實際輸出**」（純演算法/二進位，不需 Android 工具鏈）。真實 App 的 DEX 內容為代表性說明。

## 為什麼要懂 DEX 格式？

因為它是 App 邏輯的**唯一載體**。你用 jadx 讀的 Java、用 apktool 改的 smali、用 Frida hook 的方法——最終都對應到 DEX 裡的某個 `method_id`、某段 `code_item`、某個 `string_data`。工具幫你翻譯，但工具會出錯、會被混淆騙、脫殼脫出來的是半殘 DEX 要你手修——這時候能不能自己讀懂 DEX 的原始位元組，決定你是「卡死」還是「自己動手」。

而且 DEX 的設計本身就是一堂課。它是 Google 在 2008 年為「記憶體只有幾十 MB 的手機」重新設計的執行檔格式，每個決定——為什麼共用 string pool、為什麼暫存器式、為什麼 header 有自校驗——都在回應「省空間、快載入、能驗證完整性」這幾個約束。懂了這些設計動機，你對「一個 bytecode 格式該怎麼設計」的理解會上一個台階。

## 先建立直覺：DEX 是一個「去重過的關聯式資料庫」

先給心智模型。一個 App 有幾千個類、幾萬個方法，裡面大量重複——`java.lang.String` 這個型別名、`toString` 這個方法名、`"error"` 這個字串，可能在幾百個地方用到。`.class`（JVM）的做法是**每個 class 檔各自存一份自己的常數池**，重複的字串在每個檔案裡各存一遍。

DEX 反其道而行：**把整個 App 所有類的字串、型別、方法簽名全部抽出來去重，集中成幾個大表，本體只存索引**。這就像把一堆 Excel 從「每張表各自重複寫公司名」重構成「一張公司表 + 其他表用 ID 引用」——關聯式資料庫的正規化。

```
   JVM (.class)：每個類各自帶常數池        DEX：全 App 共用去重的 pool
 ┌──────────┐ ┌──────────┐              ┌────────────────────────────┐
 │ Foo.class│ │ Bar.class│              │ string_ids  (全部字串一份)  │
 │ "String" │ │ "String" │  ──重構──▶    │ type_ids    (全部型別一份)  │
 │ "toString"│ │ "toString"│             │ method_ids  (全部方法一份)  │
 │  ...重複  │ │  ...重複  │              │ class_defs → 用索引引用上表  │
 └──────────┘ └──────────┘              └────────────────────────────┘
```

這個「去重 + 索引」是理解 DEX 整個佈局的鑰匙：**DEX 的前半是一堆 `*_ids` 索引表，後半是被索引的實際資料（`data` 區）**。所有 `*_ids` 表都排序過（為了二分查找快、也為了驗證），彼此用 index 交叉引用。你手撕 DEX 時，就是在這些表之間跳來跳去解引用。

## DEX 檔案的整體佈局

一個 DEX 檔從頭到尾的區塊順序（固定）：

```
offset 0
 ┌─────────────────────────────────────────────┐
 │ header (0x70 = 112 bytes)                    │  ← 檔案的目錄：各區的 size/offset
 ├─────────────────────────────────────────────┤
 │ string_ids[]   (每項 4 bytes: → string_data) │  ┐
 │ type_ids[]     (每項 4 bytes: → string_ids)  │  │
 │ proto_ids[]    (方法原型: 回傳+參數)          │  ├ 索引表 (都排序)
 │ field_ids[]    (欄位: class+type+name)       │  │
 │ method_ids[]   (方法: class+proto+name)      │  │
 │ class_defs[]   (類定義: 最豐富的一項)         │  ┘
 ├─────────────────────────────────────────────┤
 │ data[]         (實際資料: string_data /       │  ← 被上面索引指向的東西
 │                 code_item / class_data /      │
 │                 encoded_array / annotations…) │
 ├─────────────────────────────────────────────┤
 │ map_list       (整個檔案的目錄清單, 校驗用)   │  ← 每種 item 的 type/count/offset
 └─────────────────────────────────────────────┘
```

記住這個大結構，我們逐塊往下拆。

## header：檔案的目錄（前 112 bytes）

Ch 2 已經拆過 header 的前 40 個 byte（magic / checksum / signature / file_size / header_size / endian_tag）。這章補完後面——header 其實就是**整個 DEX 的目錄**，記錄每個索引表的**數量與起始 offset**：

```
offset  欄位              說明
  0     magic[8]          "dex\n035\0"      ← 版本號在這 (035/037/038/039)
  8     checksum (u32)    adler32(bytes[12:])
 12     signature[20]     SHA-1(bytes[32:])
 32     file_size (u32)
 36     header_size (u32) 固定 0x70
 40     endian_tag (u32)  0x12345678
 44     link_size / link_off
 52     map_off (u32)     → map_list 的位置
 56     string_ids_size / string_ids_off    ← 有幾個字串, 表在哪
 64     type_ids_size / type_ids_off
 72     proto_ids_size / proto_ids_off
 80     field_ids_size / field_ids_off
 88     method_ids_size / method_ids_off
 96     class_defs_size / class_defs_off
104     data_size / data_off
```

我用 Python 手工組一個結構正確的 header、算完 checksum/signature，再解析回來（**實際輸出**）：

```python
import struct, zlib, hashlib
HEADER_SIZE = 0x70
body = b"\x2a" * 48
hdr = bytearray(HEADER_SIZE)
hdr[0:8] = b"dex\n035\x00"
struct.pack_into("<I", hdr, 32, HEADER_SIZE + len(body))  # file_size
struct.pack_into("<I", hdr, 36, HEADER_SIZE)              # header_size
struct.pack_into("<I", hdr, 40, 0x12345678)               # endian_tag
data = bytearray(bytes(hdr) + body)
data[12:32] = hashlib.sha1(bytes(data[32:])).digest()                        # signature
data[8:12]  = struct.pack("<I", zlib.adler32(bytes(data[12:])) & 0xffffffff) # checksum
```

```
magic        : b'dex\n035\x00'
dex version  : 035
checksum     : 0xf7de1387   = adler32(bytes[12:])
signature    : a6ba46eb3489d551f72b1bc1c403b12e5a0de61d = SHA-1(bytes[32:])
file_size    : 160 (actual len: 160 )
header_size  : 0x70
endian_tag   : 0x12345678
signature verifies : True
checksum verifies  : True
[tamper 1 byte] signature still ok?  False
[tamper 1 byte] checksum  still ok?  False
```

最後兩行是 Ch 2 那個因果的完整版：**改 body 一個 byte，SHA-1 signature 與 adler32 checksum 立刻雙雙失效**。ART 載入時驗這兩欄，對不上就拒。這就是「手改 DEX byte 會壞」的機制原點，也是為什麼改邏輯要走 smali 重組（讓組譯器重算這兩欄）。

> **DEX 版本號的差別要誠實標注**：magic 裡的三位數字（`035`/`037`/`038`/`039`）對應不同 Android 版本引進的 bytecode 特性。`037` 起（Android 7）改了預設對齊與部分 opcode；`038`（Android 8）加了 `invoke-polymorphic`/`invoke-custom` 支援 `MethodHandle` 與 `invokedynamic`（lambda 相關）；`039`（Android 9+）再加了一些。**逆到高版本 DEX 時，太舊的 baksmali/工具可能不認得新 opcode**——這是 Ch 5 你可能踩到「工具報 unknown opcode」的根因。

### 65536 方法限制與 multidex：為什麼一個 App 有好幾個 DEX

header 裡的 `method_ids_size` 藏著一個 App 開發者天天罵、逆向者天天遇到的硬限制。DEX 裡引用方法用的 method 索引，在很多指令（如 `invoke-*`）的編碼裡是 **16-bit** 欄位——16-bit 最多表示 `0..65535`，也就是 **一個 DEX 最多只能引用 65536 個 method**（含它呼叫的所有 framework/library 方法，不只自己寫的）。

現代 App 隨便就破這個數（一個大型 App 加上一堆 SDK，方法數幾十萬跑不掉）。解法是 **multidex**：把類拆進 `classes.dex`、`classes2.dex`、`classes3.dex`… 多個 DEX，每個各自有一套 65536 額度。

```
 App 方法數 > 65536
       │  d8/r8 打包時
       ▼
 classes.dex   (前 6.5 萬個 method 的引用)
 classes2.dex  (接下來的)
 classes3.dex  (再接下來的)  ← 執行期由 ART 依序載入、跨 DEX 解引用
```

這對逆向的兩個直接影響：

1. **你搜一個方法/字串要搜遍所有 `classes*.dex`**，不能只看 `classes.dex`。apktool 反出來的 `smali/`、`smali_classes2/`、`smali_classes3/` 對應各個 DEX——關鍵邏輯可能在第幾個 DEX 沒有規律（取決於打包器怎麼分），全域搜才不會漏。
2. **有些加固故意利用這個結構藏東西**：把真邏輯放某個非主 DEX、或動態載入額外 DEX，讓只看 `classes.dex` 的人找不到。看到 App 的 DEX 數量異常多、或某個 DEX 大小異常，是偵察時的訊號。

## ULEB128：DEX 到處用的變長整數

在拆索引表前，得先懂 DEX 的一個基礎編碼：**ULEB128（Unsigned Little-Endian Base-128）**。DEX 裡大量的長度、索引、計數不是用固定 4 bytes 存，而是用這種**變長**編碼——小的數字只佔 1 byte，省空間。

規則：每個 byte 用低 7 bit 存資料、最高 bit（0x80）當「還有下一個 byte」的旗標。我用 Python 實作並驗證（**實際輸出**）：

```python
def uleb128_encode(n):
    out = bytearray()
    while True:
        b = n & 0x7f; n >>= 7
        out.append(b | 0x80 if n else b)
        if not n: return bytes(out)
```

```
     0 -> 00         -> 0
     1 -> 01         -> 1
   127 -> 7f         -> 127      ← 7 bit 塞得下, 1 byte
   128 -> 8001       -> 128      ← 超過 7 bit, 進位到第 2 byte
   255 -> ff01       -> 255
 16384 -> 808001     -> 16384    ← 要 3 bytes
  7983 -> af3e       -> 7983
```

看 `128 -> 8001`：低 byte `0x80`（最高 bit=1 表示「還有」，資料位=0），高 byte `0x01`（最高 bit=0 表示「結束」，資料位=1），拼起來 `1<<7 | 0 = 128`。**理解 ULEB128 你才讀得懂 `string_data`、`class_data`、`code_item` 裡那些變長欄位**——它們全用這個編。

## string_ids 與 string_data：字串怎麼存

`string_ids[]` 是最簡單的索引表：每項就是一個 4-byte offset，指向 `data` 區裡的一個 `string_data_item`。而 `string_data_item` 的佈局是：**ULEB128(UTF-16 長度) + MUTF-8 位元組 + 一個 `\x00` 結尾**。

我實作這個佈局（**實際輸出**）：

```python
def make_string_data(s):
    utf16_len = len(s)  # ASCII 下等於字元數
    return uleb128_encode(utf16_len) + s.encode('utf-8') + b'\x00'
```

```
string_data('Lcom/example/Foo;') = 114c636f6d2f6578616d706c652f466f6f3b00  (len=19)
string_data('hello')             = 0568656c6c6f00                          (len=7)
string_data('sign')              = 047369676e00                            (len=6)
```

看 `hello`：`05`（ULEB128，長度 5）+ `68656c6c6f`（"hello" 的 ASCII）+ `00`（結尾）。兩個逆向重點：

1. **長度用的是 UTF-16 code unit 數，不是 byte 數**，而內容卻是 **MUTF-8**（Modified UTF-8，跟標準 UTF-8 差在 ` ` 編成 `C0 80` 兩 byte、且補充平面用代理對各自編）。這個「長度單位 ≠ 內容編碼單位」的錯位是手寫 DEX parser 常見的 bug 來源。
2. **`Lcom/example/Foo;` 這種怪字串就是型別描述符**——`L...;` 包住一個 class 名，`/` 是套件分隔（原本的 `.`）。這是 Ch 5 smali 型別描述符的來源，我們馬上會在 type_ids 看到它。

**所有字串集中在一個 pool、排序、去重**——這是 DEX 省空間的核心。逆向時你搜一個關鍵字（`"password"`、某個 URL），本質是在這個 string pool 裡找，找到後反查「哪些 method 的 code 引用了這個 string index」。

## 五張索引表：靠層層引用組出型別系統

DEX 的優雅在於後面幾張表**用前面表的 index 一層層堆出完整語意**：

```
string_ids ──┐
             ▼
 type_ids[i] = { descriptor_idx → string_ids }        "型別 = 一個字串"
             │
             ▼
 proto_ids[i] = { shorty_idx→string, return_type_idx→type,   "方法原型 = 回傳+參數列"
                  parameters_off→type_list }
             │
             ├──────────────┐
             ▼              ▼
 field_ids[i] =            method_ids[i] =
   { class_idx→type,         { class_idx→type,       "方法 = 屬於哪個類 + 原型 + 名字"
     type_idx→type,            proto_idx→proto,
     name_idx→string }         name_idx→string }
```

逐張看它們裝什麼、逆向時代表什麼：

| 表 | 每項內容 | 逆向意義 |
|---|---|---|
| **string_ids** | → string_data 的 offset | 全 App 字串池；搜關鍵字的地方 |
| **type_ids** | descriptor_idx → 一個字串（`Lcom/x/Y;`） | 全 App 用到的所有型別；`Ljava/lang/String;`、`I`、`[I` 都是一項 |
| **proto_ids** | shorty + return_type + parameters | 方法簽名的「形狀」（回傳什麼、收什麼參數）；重載方法靠它區分 |
| **field_ids** | class + type + name | 每個欄位的完整身分；hook 欄位、找成員變數時查它 |
| **method_ids** | class + proto + name | **每個方法的完整身分**；Frida hook 一個方法、jadx 顯示方法簽名，底層都是這一項 |
| **class_defs** | 一個類的全部（見下節） | 資訊最豐富，串起整個類 |

一個具體解引用範例：你要表達方法 `com.example.Foo.sign(String): String`——

```
 method_id {
   class_idx  → type_ids → string "Lcom/example/Foo;"
   proto_idx  → proto_ids { return: type "Ljava/lang/String;",
                            params: [type "Ljava/lang/String;"] }
   name_idx   → string_ids → "sign"
 }
```

三個索引各自往下解，拼出完整的方法簽名。**Frida 的 `Java.use("com.example.Foo").sign` 定位一個方法，對應的就是找到這個 method_id。** 你懂了這層，就懂為什麼重載方法（同名不同參數）在 Frida 要用 `.overload("java.lang.String")` 指定——因為 name_idx 一樣，得靠 proto_idx 區分。

## class_defs 與 code_item：邏輯真正在哪

`class_defs[]` 每一項描述一個完整的類——它的父類、介面、`class_data_off`（指向欄位與方法清單）、`static_values_off` 等。順著 `class_data` 進去，每個方法有個 `code_off` 指向 **`code_item`**——**這才是真正的 bytecode 所在**：

```
 code_item {
   registers_size   ← 這個方法用幾個暫存器 (Ch 5 的 .registers)
   ins_size         ← 收幾個參數 (含 this)
   outs_size        ← 呼叫別的方法時最多傳幾個參數
   tries_size       ← 有幾個 try 區塊
   debug_info_off   ← 行號/區域變數名 (常被 strip 或混淆掉)
   insns_size       ← bytecode 有幾個 16-bit code unit
   insns[]          ← ★ 實際的 Dalvik bytecode ★
   try_items[] / handlers  ← try/catch 資訊
 }
```

`insns[]` 那一串 16-bit code unit 就是這門課後面所有動作的核心——你 hook 的、你 patch 的、你逆的，最後都落到這裡。下一節我們就親手編解碼它。

### 從 class_def 走到一個方法的 bytecode

把上面的引用鏈完整走一遍，你才真正懂「找一個方法的程式碼」在 DEX 裡是怎麼一步步解出來的：

```
 class_defs[k]                          "我想看 Foo.sign() 的 bytecode"
   ├ class_idx     → type_ids → "Lcom/example/Foo;"    確認是這個類
   ├ superclass_idx→ type_ids → "Ljava/lang/Object;"
   ├ class_data_off ──────────┐
   └ ...                       │
                               ▼
 class_data_item {            "這個類的欄位與方法清單 (數量用 ULEB128)"
   static_fields_size  / instance_fields_size
   direct_methods_size / virtual_methods_size
   direct_methods[] / virtual_methods[] = encoded_method {
       method_idx_diff  (ULEB128, 相對前一個方法的差值 → 解出 method_id)
       access_flags     (public/private/static…)
       code_off ────────────────┐
   }                             │
 }                               ▼
                          code_item { registers_size, ins_size, …, insns[] }
                                     └──── ★ 這裡才是 sign() 的 Dalvik bytecode ★
```

兩個容易踩的細節：

- **`method_idx_diff` 是「差值編碼」**：`encoded_method` 存的不是 method_id 的絕對索引，而是**跟同一清單裡前一個方法的差**（第一個是相對 0）。這樣連續遞增的索引能用小的 ULEB128 表示，更省空間。手寫 parser 忘了做累加、直接把 diff 當絕對索引，是經典 bug。
- **`code_off` 為 0 代表沒有 code**：抽象方法（abstract）、介面方法、native 方法（`.so` 裡實作）在 DEX 裡 `code_off = 0`——它們沒有 Dalvik bytecode。逆向時看到某方法 `code_off=0` 又標了 `native`，就知道**真正的邏輯在 `.so` 裡**，該切到 Part 4 逆 native，不是在 DEX 裡找。

這條「class_def → class_data → encoded_method → code_item」的鏈，就是 baksmali 產一個方法 smali 時走的路，也是脫殼工具重建 DEX 時要親手接回的鏈。

## Dalvik = 暫存器式 VM（vs JVM 堆疊式）

這是 DEX 相對 JVM 最本質的設計差異，也是你讀 smali 前必須先建立的直覺。

**JVM 是堆疊式（stack-based）**：計算靠一個運算元堆疊（operand stack）。算 `a + b` 要「push a、push b、iadd（彈兩個相加再 push 結果）」——指令短（不用寫運算元從哪來，就是堆疊頂），但**指令數多**。

**Dalvik 是暫存器式（register-based）**：計算直接對「虛擬暫存器」（`v0`、`v1`…）操作。算 `a + b` 是 `add-int v0, v1, v2`（把 v1+v2 存進 v0）——一條指令，運算元明確寫在指令裡。指令**數少但每條較長**。

```
   算 v0 = v1 + v2

   JVM (堆疊式)              Dalvik (暫存器式)
   ┌──────────────┐         ┌──────────────────────┐
   │ iload_1      │         │ add-int v0, v1, v2   │  ← 一條搞定
   │ iload_2      │         └──────────────────────┘
   │ iadd         │           運算元直接寫在指令裡
   │ istore_0     │
   └──────────────┘
   4 條, 靠堆疊隱含傳值        1 條, 暫存器顯式指定
```

為什麼手機選暫存器式？**指令條數少 → dispatch 次數少 → 直譯器跑得快**（每條指令的 fetch-decode-dispatch 是有固定開銷的，條數少總開銷就低）。代價是每條指令變長、且要編譯器做暫存器分配。對 2008 年 CPU 慢的手機，這筆帳划算。這也直接決定了 smali 長什麼樣——smali 滿眼的 `v0`、`v1`、`p0` 就是這些虛擬暫存器（Ch 5 展開）。

## 親手編解碼一條 Dalvik 指令

理論講完，動手。指令是 16-bit code unit（little-endian）串成的。我編四條指令、印出原始位元組、再解碼回來驗證（**實際輸出**）：

```python
# const/4 v0, #1 ; const/16 v1, #256 ; add-int/2addr v0, v1 ; return v0
# 對應 opcode: 0x12 (11n) / 0x13 (21s) / 0xb0 (12x) / 0x0f (11x)
```

```
raw bytes : 121013010001b0100f00
code units: 1012 0113 0100 10b0 000f
  const/4      v0, #1
  const/16     v1, #256
  add-int/2addr v0, v1
  return       v0
```

拆第一條 `const/4 v0, #1`：位元組是 `12 10`，code unit（LE）是 `0x1012`。格式代號 **11n** 的佈局是 `[B|A|op]`——低 byte 是 opcode `0x12`，高 byte 拆成兩個 nibble：低 nibble `A=0`（目標暫存器 v0）、高 nibble `B=1`（4-bit 有號字面值 #1）。**一個 16-bit code unit 同時塞了 opcode + 暫存器編號 + 字面值**——這種緊湊編碼就是暫存器式指令「條數少每條長」的具體長相。

再看 `const/16 v1, #256`（`13 01 00 01`）：格式 **21s** 是 `[AA|op][BBBB]`——第一個 code unit 低 byte opcode `0x13`、高 byte `AA=1`（v1），第二個 code unit 是有號 16-bit 字面值 `0x0100=256`。因為 256 塞不進 4-bit，就得用能帶 16-bit 立即數的 `const/16`。

**這解釋了 smali 為什麼有 `const/4`、`const/16`、`const`、`const/high16` 一整族**——不是隨便分的，是「立即數多大就用哪種編碼寬度」的省空間設計。Ch 5 你會看到這些指令，現在你知道它們背後的位元佈局了。

> **格式代號（format id）小抄**：DEX 用 `11n`、`21s`、`35c` 這種代號描述每條指令怎麼編碼——第一位數字是**佔幾個 16-bit code unit**、第二位是**用幾個暫存器/多少運算元**、字母是運算元類型（`n`=4bit立即數、`s`=有號、`c`=常數池索引、`x`=無額外資料）。逆到看不懂的指令時，查它的 format id 就知道怎麼拆位元。完整表在 AOSP 的 [Dalvik bytecode 格式](https://source.android.com/docs/core/runtime/instruction-formats) 頁。

### 指令怎麼引用 string pool：追一條 const-string

前面的指令都是自帶立即數。但 App 邏輯裡到處是字串常數（URL、金鑰、log 訊息），它們不塞在指令裡，而是**指令帶一個 index、去 string_ids 解引用**。以 `const-string vAA, string@BBBB`（opcode `0x1a`，格式 `21c`）為例——`BBBB` 是 string_ids 的索引。我把「指令 → string_ids → string_data」整條解引用鏈實跑一遍（**實際輸出**）：

```
instruction bytes : 1a020200
opcode 0x1a = const-string
target reg        : v2
string index      : 2
resolves to       : 'SECRET_KEY'

string_ids table:
  [0] off=0x100 -> 'Lcom/example/Foo;'
  [1] off=0x113 -> 'sign'
  [2] off=0x119 -> 'SECRET_KEY'
  [3] off=0x125 -> 'hello'
```

指令 `1a 02 0200` 拆開：opcode `0x1a`（const-string）、`vAA=2`（結果放 v2）、後兩 byte `0x0002` 是字串索引 2。索引 2 → `string_ids[2]` → offset `0x119` → 解 `string_data` → 拿到 `"SECRET_KEY"`。**這就是逆向「搜字串定位程式碼」的底層原理**——你在 jadx 搜 `"SECRET_KEY"`，工具先在 string pool 找到它是索引 2，再反查「哪些方法的 code 裡有 `const-string v?, string@2`」，就定位到用這字串的地方。字串是逆向最好的路標，因為它明碼可搜，而 `const-string` 這條指令就是把字串接進程式邏輯的那根線。

## map_list：整個檔案的目錄與校驗依據

DEX 尾端的 `map_list` 是一張「這個檔案裡有哪些種類的 item、各有幾個、在哪個 offset」的總表。每個 `map_item` 記 `{ type, size, offset }`，type 是像 `TYPE_STRING_ID_ITEM`(0x0001)、`TYPE_CODE_ITEM`(0x2001)、`TYPE_MAP_LIST`(0x1000) 這種常數。

它的價值有二：

1. **它是 header 資訊的冗餘備份 + 完整清單**：header 記了主要索引表的位置，但 `data` 區裡各種 item（code_item、annotation、debug_info…）的分布，靠 map_list 才完整。工具驗證 DEX、脫殼後重建 DEX，都要靠 map_list 對帳。
2. **脫殼時它常被動手腳**：有些加固會故意讓 map_list 與實際內容不符、或把某些 offset 指到假地方來干擾靜態工具。Part 5 脫殼你會遇到「dump 出來的 DEX map_list 對不上」需要手修的情況——這時候懂 map_list 結構就是能不能修好的分水嶺。

`map_item` 的佈局是 `{ ushort type, ushort unused, uint count, uint offset }`。我造一個小 map_list 塞幾種 item、再解析回來（**實際輸出**）：

```
map_list size = 5 entries

type                       count    offset
TYPE_HEADER_ITEM               1       0x0
TYPE_STRING_ID_ITEM           42      0x70
TYPE_CLASS_DEF_ITEM            5     0x300
TYPE_CODE_ITEM                12     0x500
TYPE_MAP_LIST                  1     0x900
```

讀這張表就像讀 DEX 的「總目錄」：這個檔有 1 個 header、42 個 string_id、5 個 class_def、12 個 code_item，各在哪個 offset。**驗證一份 dump 出來的 DEX 是否完整、脫殼後重組 DEX 是否正確，第一步就是拿 map_list 跟實際 offset 對帳**——每一種 item 的 count 和 offset 都要對得上真實內容，對不上就是這份 DEX 有問題。常見的 type 常數：`0x0000` header、`0x0001` string_id、`0x0006` class_def、`0x2001` code_item、`0x2002` string_data、`0x1000` map_list 本身（它也把自己列進去）。

## 對比與取捨：DEX vs JVM class

| 面向 | JVM `.class` | DEX |
|---|---|---|
| 檔案粒度 | 一類一檔 | 全 App 打包成一個（或少數幾個）DEX |
| 常數 | 每類各自常數池，重複 | 全 App 共用去重 pool |
| VM 模型 | 堆疊式 | 暫存器式 |
| 指令特性 | 條數多、每條短 | 條數少、每條長 |
| 設計目標 | 平台無關、通用 | 省空間 + 手機快載入 |
| 完整性 | 無內建自校驗 | header 有 adler32 + SHA-1 |
| 逆向可讀性 | 反編譯品質高（metadata 多） | smali 一對一無損；Java 反編譯近似 |

一句話：**DEX 是「把 JVM 為手機重新最佳化」的產物**——犧牲通用性換省空間與載入速度，順便加了完整性校驗。這些取捨的每一項，都在你逆向時留下痕跡。

## 踩雷集錦

1. **錯誤直覺：「string_data 的長度就是 byte 數」→ 正確認識**：那個 ULEB128 長度是 **UTF-16 code unit 數**，內容卻是 **MUTF-8** byte。長度單位跟內容編碼單位不一致，手寫 parser 直接拿它當 byte 長度讀會錯位。
2. **錯誤直覺：「Dalvik 跟 JVM 都是堆疊式，指令差不多」→ 正確認識**：Dalvik 是**暫存器式**。這不是細節——它決定了 smali 滿眼 `v0/v1`、決定了指令編碼方式、決定了為什麼有 `const/4`vs`const/16` 一族。搞錯這個，smali 你會讀得很痛苦。
3. **錯誤直覺：「改 DEX 只要改對 bytecode 就好」→ 正確認識**：改了 body 沒重算 header 的 checksum(adler32)+signature(SHA-1)，ART 載入直接拒（本章實跑驗證過雙雙失效）。所以走 smali 重組讓工具重算，別手 patch。
4. **錯誤直覺：「所有 DEX 都一樣，工具吃得下」→ 正確認識**：DEX 版本 `035`/`037`/`038`/`039` 引進不同 opcode（如 `038` 的 `invoke-polymorphic`）。舊工具碰新版 DEX 會報 unknown opcode。逆向前先看 magic 的版本號、確認工具夠新。
5. **錯誤直覺：「map_list 只是個目錄不重要」→ 正確認識**：脫殼重建 DEX 時 map_list 是對帳依據，而且加固常對它動手腳干擾靜態分析。dump 出來的 DEX 打不開、工具報結構錯，第一個要查的就是 map_list 跟實際內容對不對得上。

## 進階：再往深一層

- **`shorty` 描述符**：proto_ids 裡的 `shorty_idx` 指向一個「簡寫簽名」字串，例如 `LL`（回傳 object、收一個 object）——`V/Z/B/S/C/I/J/F/D` 對應各基本型別、`L` 統一代表所有 object 型別。它是給 runtime 快速判斷「這方法回傳/參數是不是需要 GC 追蹤的 reference」用的，比完整 type descriptor 快查。逆向時看到 shorty 就知道方法的粗略形狀。
- **`hiddenapi` 旗標與 `019` 之後的擴充**：Android 10+ 在 DEX 裡塞了 hidden API 的存取限制資訊（放在 `data` 區的擴充結構），這是為什麼你在新版系統上反射呼叫某些隱藏 API 會被擋。逆向繞 hidden API 限制時會碰到這層。
- **compact DEX（cdex）**：ART 內部（dex2oat 之後）會用一種更省空間的 `cdex` 格式，多個 DEX 共用一個 shared data section。脫殼時如果從記憶體 dump 到的是 cdex 而非標準 dex，很多工具不吃，需要先轉回標準 DEX。Part 6 會碰到。
- **debug_info 的價值**：`code_item` 的 `debug_info_off` 指向行號與區域變數名資訊。**沒被 strip 的 App**，這裡有原始變數名——jadx 能還原出漂亮變數名多半是靠它。混淆/加固第一件事就是砍掉 debug_info，這也是為什麼混淆後的 Java 全是 `p0/v1` 這種沒意義的名字。

## 動手練習

1. 拿本章的 Python header 片段，改 `HEADER_SIZE` 附近**任意一個非完整性欄位的 byte**（例如故意把 `endian_tag` 改成別的值），重算 checksum/signature，觀察它們**會不會**變——理解「完整性欄位涵蓋的是 byte[12:] 與 byte[32:]，涵蓋範圍內任何改動都會反映」。
2. 自己實作 ULEB128 的**解碼**（本章只完整給了 encode），對 `8001`、`808001` 解回 128、16384，驗證你真的懂 base-128 進位。再試 `af3e` 解回 7983。
3. 手工編一條 `move v3, v5`（opcode `0x01`，格式 `12x`，`[B|A|op]`）與一條 `invoke-virtual`（挑戰題，格式 `35c`，會用到方法索引），對照 AOSP 指令格式頁確認你的位元佈局對不對。編對了，你就真的懂暫存器式指令的編碼了。
4. 找一個小 APK，用 `unzip` 抽出 `classes.dex`，用 Python 讀它真正的 header：解出 `string_ids_size`、`method_ids_size`、`class_defs_size`。感受一個真實 App 有幾千個字串、幾萬個方法——這些數字就是它的規模。

## 本章重點整理

- **DEX 是「去重 + 索引」的格式**：全 App 字串/型別/方法集中成排序過的 pool，本體只存 index，靠層層解引用組出完整語意。這是它省空間的核心。
- **佈局 = header（目錄）→ 五張索引表 → data（實際資料）→ map_list（總清單）**；一個方法的完整身分由 method_id 的 class/proto/name 三個索引拼出。
- **Dalvik 是暫存器式 VM**（vs JVM 堆疊式）：指令條數少每條長，運算元顯式寫在指令裡（`add-int v0,v1,v2`）——這決定了 smali 的長相與 `const/4`vs`const/16` 這類編碼族。
- **header 的 adler32 checksum + SHA-1 signature 是自校驗**（本章實跑：改 1 byte 即失效），所以改邏輯走 smali 重組讓工具重算，不能手 patch；DEX 版本 `035`~`039` 有 opcode 差異，工具要夠新。

## 自我檢核

- [ ] 不看筆記，能講出 DEX 從 header 到 map_list 的區塊順序，以及索引表和 data 區的關係
- [ ] 能解釋「去重 + 索引」為什麼省空間，並說出一個 method_id 靠哪三個索引拼出完整簽名
- [ ] 能講清楚暫存器式 vs 堆疊式 VM 的差別，以及 Dalvik 為什麼選暫存器式
- [ ] 能大致說出一條 `const/4 v0, #1` 在 16-bit code unit 裡怎麼塞 opcode / 暫存器 / 立即數
- [ ] 能解釋為什麼手改 DEX byte 會被 ART 拒，牽涉 header 的哪兩個欄位
- [ ] 知道 DEX 版本號在哪、為什麼版本差異會讓舊工具報 unknown opcode

## 延伸閱讀

### 官方規格（一手依據）

- **[DEX 檔案格式](https://source.android.com/docs/core/runtime/dex-format)** — AOSP
  - **讀哪裡**：`header_item`、`string_id_item`/`string_data_item`、`proto_id_item`、`method_id_item`、`class_def_item`、`code_item`、`map_list` 逐節對照本章
  - **為什麼值得讀**：這是 DEX 每個欄位的最終定義。本章是導讀，真正手撕 DEX 時攤開這頁當字典
  - **注意**：ULEB128/MUTF-8 的精確規則在這頁的 "encoded value" 附近
- **[Dalvik bytecode 與指令格式](https://source.android.com/docs/core/runtime/dalvik-bytecode)** — AOSP
  - **讀哪裡**：opcode 總表（每個指令的 format id）；配合 [instruction-formats](https://source.android.com/docs/core/runtime/instruction-formats) 看每個 format 怎麼拆位元
  - **和本章的關聯**：本章手編指令用的 `11n`/`21s`/`12x`/`11x` 佈局全出自這，Ch 5 讀 smali 時常回來查

### 深入設計

- **[Dalvik 的暫存器式設計論文/文件](https://source.android.com/docs/core/runtime)** — AOSP Runtime 概覽
  - **這篇說什麼**：Dalvik/ART 的整體設計動機，暫存器式 VM 與 JVM 的取捨
  - **讀哪裡**：runtime 概覽與 dex2oat 那節（Part 6 會回來）
  - **和本章的關聯**：本章「為什麼選暫存器式」的延伸，把「省空間快載入」的設計哲學講深

### 工具與實作

- **[baksmali/smali 專案 wiki](https://github.com/JesusFreke/smali/wiki)** — JesusFreke
  - **這篇說什麼**：把 DEX 反組譯成 smali 的參考實作，是理解 DEX→smali 對應的最佳範本
  - **讀哪裡**：Registers 與 TypesMethodsAndFields 頁，正好銜接 Ch 5
  - **前提知識**：讀過本章的 DEX 結構，這裡看它怎麼把結構翻成文字

下一章我們把這章的 bytecode 換上「人臉」——smali。你會看到本章的 method_id、type descriptor、`const/4`、暫存器全部以文字形式出現，並學會怎麼把一段 smali 對照回它原本的 Java。這是你能動手改 App 的第一個真本事。

→ [Ch 5 Smali 語法完整導覽](./05-smali-language.md)
