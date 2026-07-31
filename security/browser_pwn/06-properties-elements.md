# Ch 6 — Properties 與 Elements：in-object / fast / dictionary

> **目標**：把「一個 JS 物件的值到底存在哪」徹底分清楚。V8 對物件有**兩條完全獨立的儲存軸**：**named properties**（`o.x`）和 **indexed elements**（`o[0]`），各有自己的 backing store 和快慢模式。你要能看著 `%DebugPrint` 說出：這個屬性是 in-object 還是溢位到 PropertyArray、這個物件是 fast 還是 dictionary、elements 指向哪。這是理解 [Ch 7](./07-jsarray-elements-kind.md)（elements kind）和後續「OOB 讀寫打哪個 backing store」的前提。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，`~/v8build/v8/out/x64.release/`。d8：`~/v8build/v8/out/x64.release/d8`。本章所有 `%DebugPrint` 真跑。

## 為什麼需要這個？

「`o.x` 和 `o[0]` 不都是存值嗎，能有多不同？」——差很多。V8 把這兩件事當成兩套系統：

- **`o.x`（named property，具名屬性）**：key 是字串/symbol。V8 假設「一個形狀的具名屬性數量與名字相對固定」，用 Map + slot 的 fast 機制。
- **`o[0]`（indexed element，索引元素）**：key 是整數。V8 假設「這是連續的陣列」，用一個緊湊的 backing array，還細分成六種 elements kind（[Ch 7](./07-jsarray-elements-kind.md)）。

這個「兩軸分離」不是學術細節，是利用的地形圖。`%DebugPrint` 裡每個物件都有**兩個獨立的 backing store 欄位**：`- properties:` 和 `- elements:`。你之後拿到 OOB 讀寫，第一個問題永遠是「我越界到的是哪個 backing store、它的相鄰是什麼」。搞不清這兩軸，你會把「陣列 OOB」和「屬性溢位」混為一談，判斷錯攻擊路徑。

## 先建立直覺：一個物件，兩個 backing store，四種模式

```
        JSObject
   ┌──────────────────┐
   │ map              │
   │ properties ──────┼──► named 屬性溢出時放這（PropertyArray / NameDictionary）
   │ elements ────────┼──► indexed 元素放這（FixedArray / FixedDoubleArray / Dictionary）
   │ [inobj slot 0]   │◄┐
   │ [inobj slot 1]   │  ├─ 前幾個 named 屬性直接塞在物件本體內（in-object，最快）
   │ [inobj slot 2]   │◄┘
   └──────────────────┘

   named 屬性有兩種模式：  fast（in-object + PropertyArray，靠 Map/descriptor）
                          dictionary（退回 NameDictionary hash table）
   indexed 元素有兩種模式：fast（FixedArray/FixedDoubleArray，連續）
                          dictionary（NumberDictionary，稀疏時）
```

記住兩件事：（1）**named 和 indexed 是兩條互不相干的軸**，各自有 fast/dictionary；（2）named 的 fast 模式又細分 **in-object（塞本體內）** 和 **溢位到 PropertyArray**。下面逐一實測。

## 底層機制一：named property 的 in-object 儲存

先看少量屬性——直接塞進物件本體：

```
$ d8 --allow-natives-syntax -e 'let o={x:1,y:2,z:3}; %DebugPrint(o);'
DebugPrint: 0xba70104b2a5: [JS_OBJECT_TYPE]
 - map: 0x0ba70101e5e1 <Map[24](HOLEY_ELEMENTS)> [FastProperties]
 - elements: 0x0ba7000007e5 <FixedArray[0]> [HOLEY_ELEMENTS]
 - properties: 0x0ba7000007e5 <FixedArray[0]>       ← 空！屬性不在這
 - All own properties: {
    #x: 1 (const data field 3, in-obj, attrs: [WEC])   ← in-obj，field 3
    #y: 2 (const data field 4, in-obj, attrs: [WEC])
    #z: 3 (const data field 5, in-obj, attrs: [WEC])
 }
 ...
 - instance size: 24
 - inobject properties: 3
```

關鍵觀察：

- **`properties:` 指向一個空的 `FixedArray[0]`**（那個 `0x..07e5` 是全 isolate 共用的「空陣列」單例）。屬性**不在** properties backing store 裡。
- 每個屬性標 **`in-obj`、`field 3/4/5`**：它們直接塞在物件本體的第 3、4、5 個 word（0/1/2 是 map/properties/elements）。這是最快的儲存——讀 `o.x` 就是 `[object + 3*4]`（壓縮指標 4 byte），一次記憶體存取。
- `instance size: 24` = 3 個 header word + 3 個 in-object slot（壓縮下每 slot 4 byte，加上對齊）。`inobject properties: 3` 說明有 3 個內建槽。

**in-object 屬性是 exploit 常用的「可控相鄰資料」**：你能透過屬性把已知的值精準放在物件本體的已知 offset，之後 fake object 或計算相鄰佈局時很有用。

## 底層機制二：溢位到 PropertyArray（`ooo` = out-of-object）

物件的 in-object 槽有限（Map 決定有幾個）。塞滿後，多的屬性**溢位到外部的 PropertyArray**：

```
$ d8 --allow-natives-syntax -e 'let o={a:1,b:2,c:3,d:4}; o.e=5; o.f=6; %DebugPrint(o);'
DebugPrint: 0xba70104b38d: [JS_OBJECT_TYPE]
 - properties: 0x0ba70104b40d <PropertyArray[3]>     ← 不再是空陣列！
 - All own properties: {
    #a: 1 (const data field 3, in-obj, attrs: [WEC])
    #b: 2 (const data field 4, in-obj, attrs: [WEC])
    #c: 3 (const data field 5, in-obj, attrs: [WEC])
    #d: 4 (const data field 6, in-obj, attrs: [WEC])
    #e: 5 (const data field 2, ooo, attrs: [WEC])     ← ooo = out-of-object！
    #f: 6 (const data field 3, ooo, attrs: [WEC])
 }
 - instance size: 28
 - inobject properties: 4
 - unused property fields: 1
```

拆解：

- `{a,b,c,d}` literal 讓 V8 預留了 **4 個 in-object 槽**（`inobject properties: 4`）。`a`–`d` 是 `in-obj`。
- 之後 `o.e`、`o.f` 塞不下，**溢位到 `properties:` 指向的 `PropertyArray[3]`**，標成 **`ooo`（out-of-object）、`field 2`/`field 3`**——這裡的 field index 是相對 PropertyArray 的。
- `unused property fields: 1`：V8 每次擴 PropertyArray 會多留一格緩衝（避免每加一個屬性就重配），所以 array 長度 3 但只用了 2。

**這條「in-object 滿了就溢位」的規則，在利用時很重要**：PropertyArray 是一個獨立的堆物件，它和 elements、和物件本體是分開配置的，OOB 越界時打到哪個由佈局決定。

## 底層機制三：dictionary mode（退回 hash table）

物件太動態（大量增刪屬性）時，維護 Map transition 樹的成本高於好處，V8 **放棄 fast 形狀，退回 hash table**——`NameDictionary`：

```
$ d8 --allow-natives-syntax -e 'let d={}; for(let i=0;i<40;i++)d["k"+i]=i; delete d.k5; %DebugPrint(d);'
DebugPrint: 0xba70104b479: [JS_OBJECT_TYPE]
 - map: 0x0ba70101857d <Map[12](HOLEY_ELEMENTS)> [DictionaryProperties]   ← 注意！
 - properties: 0x0ba70104bea5 <NameDictionary[198]>                        ← hash table
 - All own properties: {
   k16: 16 (data, dict_index: 17, attrs: [WEC])
   k13: 13 (data, dict_index: 14, attrs: [WEC])
   ...（順序亂掉了——hash table 不保序）
 }
 - instance size: 12
 - inobject properties: 0
 - dictionary_map
```

三個關鍵差異：

- Map 標 **`[DictionaryProperties]` / `dictionary_map`**，不再是 `[FastProperties]`。
- `properties:` 指向 **`NameDictionary[198]`**——一個開放定址 hash table，key、value、細節（`dict_index`）都存在裡面。
- 屬性列出來**順序是亂的**（`k16, k13, k24...`），因為 hash 決定位置。fast 模式下屬性是有序的（依插入）。
- `inobject properties: 0`、instance size 縮回最小——dictionary 物件不用 in-object 槽了。

**dictionary mode 的利用意義**：它是「共用 Map + inline cache」那套快取假設的**破口**。有些利用刻意把物件推進/拉出 dictionary mode 來操縱佈局或繞過某些 fast-path 假設；反過來，dictionary 物件因為每次查 hash，很多 JIT 優化不套用。`%HasFastProperties(o)` 可以查一個物件當前是不是 fast。

## indexed elements：另一條軸

上面全在講 named property。**indexed element 是完全獨立的第二條軸**，指向 `- elements:`：

```
$ d8 --allow-natives-syntax -e 'let a=[1,2,3]; %DebugPrint(a);'
 - elements: 0x33580101e3c1 <FixedArray[3]> [PACKED_SMI_ELEMENTS (COW)]
 - length: 3
```

`elements:` 指向一個 `FixedArray`（或 double 時的 `FixedDoubleArray`），裡面連續存索引元素。它有六種 **elements kind**（PACKED/HOLEY × SMI/DOUBLE/OBJECT），[Ch 7](./07-jsarray-elements-kind.md) 整章專拆。這裡只要確立一件事：

> **`o.x` 走 `properties` 軸，`o[0]` 走 `elements` 軸，兩者是不同的 backing store、不同的快慢規則，互不干擾。**

一個物件可以同時 fast properties + dictionary elements，或反過來。連 `JSObject`（非陣列）也有 elements 軸——你 `obj[0]=1` 就會用到。dump 裡每個物件都有 `elements:` 一行，普通物件多半是空的 `FixedArray[0]`。

## named vs indexed：一張對照表

| 面向 | named property（`o.x`） | indexed element（`o[0]`） |
|---|---|---|
| key 型別 | 字串 / symbol | 整數索引 |
| backing store 欄位 | `- properties:` | `- elements:` |
| fast 儲存 | in-object slot + PropertyArray | FixedArray / FixedDoubleArray |
| 慢速退化 | NameDictionary | NumberDictionary（稀疏時） |
| 形狀資訊 | 存 Map 的 DescriptorArray | 存 Map 的 **elements kind** 欄位 |
| 快取機制 | inline cache（讀 `o.x`） | elements kind + bounds check |
| 利用相關 | fake object 的可控相鄰、屬性溢位 | 陣列 OOB、elements kind confusion（Ch 7） |

## in-object / fast / dictionary：三種 named 儲存狀態機

```
   {}  ──加幾個屬性──►  in-object（塞本體內，最快）
                            │ in-object 槽滿
                            ▼
                     fast + PropertyArray（溢位到外部陣列，仍靠 Map）
                            │ 太動態（大量增刪）
                            ▼
                     dictionary（NameDictionary，退回 hash，放棄 Map 快取）

   一旦掉進 dictionary，通常不會自己回到 fast（除非顯式優化）。
```

這條「越來越慢」的滑坡，決定了一個物件在利用中的「可預測性」——fast 物件佈局規整、Map 可預測；dictionary 物件佈局散、Map 是專屬的。你在做 heap groom（[Ch 14](./14-first-oob.md)）時會刻意保持物件在 fast 狀態以求佈局可控。

## 進階：再往深一層

- **PropertyArray vs elements 的配置時機**：兩者是不同 allocation。加屬性溢位時新配 PropertyArray；陣列成長時新配 FixedArray。它們在堆上的相鄰關係影響 OOB 打到誰——[Ch 14](./14-first-oob.md) 的 groom 就在操縱這個。
- **`ooo` 的 field index 語意**：out-of-object 屬性的 `field N` 是相對 PropertyArray 起點的索引，和 in-object 的 `field N`（相對物件本體）語意不同，別混。
- **為什麼加屬性可能改 instance size**：V8 有 in-object slot 的「預留」策略（`unused property fields`）。literal `{a,b,c,d}` 一次宣告會精算槽數；逐個 `o.x=` 加則走 transition 逐步擴。這影響同名物件是否真的共用 Map。
- **elements 也有 dictionary**：`a[100000]=1`（造成巨大稀疏陣列）會讓 elements 退回 `NumberDictionary`（`DICTIONARY_ELEMENTS`），避免配置十萬格 FixedArray。這是 [Ch 7](./07-jsarray-elements-kind.md) 的邊界情況。
- **原始碼**：`src/objects/property-array.h`、`src/objects/dictionary.h`、`src/objects/js-objects.h`。想確認某物件的 in-object slot 數怎麼算，看 `Map::GetInObjectProperties`。

## 踩雷集錦

1. **錯誤直覺：「`o.x` 和 `o[0]` 存在同一個地方」。正確：** 兩條獨立軸——named 走 `properties`、indexed 走 `elements`，不同 backing store、不同規則。OOB 分析第一步就是分清打到哪條軸。
2. **錯誤直覺：「屬性都存在 properties backing store 裡」。正確：** 前幾個具名屬性多半 **in-object**（塞物件本體），`properties:` 常是空陣列；只有溢位（`ooo`）的才進 PropertyArray。
3. **錯誤直覺：「dictionary mode 只是慢一點」。正確：** 它是**放棄 fast 形狀**——Map 變 dictionary_map、屬性無序、共用 Map / inline cache 假設全失效。這對佈局可控性與 JIT 假設有實質影響。
4. **錯誤直覺：「只有陣列有 elements」。正確：** 每個 JSObject 都有 elements 軸（`obj[0]=1` 就用到）；普通物件多半是空 `FixedArray[0]` 而已。
5. **錯誤直覺：「屬性順序不重要」。正確：** fast 模式屬性有序（依插入、對應 Map transition 鏈）；掉進 dictionary 後順序被 hash 打亂——這個「有序 vs 亂序」是判斷物件在哪個模式的快速線索。

## 動手練習

1. 建一個 `{}`，逐個 `o.p0=0; o.p1=1; ...` 加到十幾個屬性，每加幾個 `%DebugPrint` 一次，觀察：（a）`properties:` 何時從空 `FixedArray[0]` 變成 `PropertyArray[N]`；（b）屬性標記何時從 `in-obj` 變 `ooo`。記下臨界的屬性數。
2. 用 `delete` + 大量 add 把一個物件推進 dictionary mode，用 `%HasFastProperties(o)` 前後各查一次確認狀態翻轉，並觀察 `%DebugPrint` 裡屬性順序如何從有序變亂。
3. 對同一個物件同時操作兩條軸：`o.name="hi"; o[0]=1; o[1]=2;` 然後 `%DebugPrint`，指出哪些值在 `properties` 側、哪些在 `elements` 側、哪些 in-object。畫出這個物件的完整記憶體佈局草圖（哪個 slot 是什麼）。

## 本章重點整理

- V8 物件有**兩條獨立儲存軸**：**named property**（`o.x`，走 `properties`）與 **indexed element**（`o[0]`，走 `elements`），各有 fast / dictionary 模式。
- named 的 fast 儲存分 **in-object**（塞物件本體，最快）和溢位到 **PropertyArray**（標 `ooo`）；太動態則退回 **NameDictionary**（dictionary mode，放棄 Map 快取、屬性變無序）。
- indexed element 指向 `FixedArray`/`FixedDoubleArray`，有六種 elements kind（存在 Map 裡，[Ch 7](./07-jsarray-elements-kind.md)），稀疏時退回 NumberDictionary。
- `%DebugPrint` 裡 `in-obj` / `ooo` / `[FastProperties]` / `[DictionaryProperties]` 是判斷儲存狀態的關鍵標記。
- 這兩軸和它們的 backing store 相鄰關係，是 OOB 讀寫（[Ch 17](./17-typedarray-attack.md)）與 heap groom（[Ch 14](./14-first-oob.md)）判斷「打到誰」的地形圖。

## 自我檢核

- [ ] 能說出 named 和 indexed 是兩條獨立軸、各對應哪個 `%DebugPrint` 欄位
- [ ] 能解釋 in-object 屬性 vs PropertyArray（`ooo`）溢位的差別與觸發時機
- [ ] 能描述 fast → dictionary 的退化條件與後果（Map 變化、順序、快取失效）
- [ ] 看一段 `%DebugPrint` 能指出每個值住在 in-obj / properties / elements 哪裡
- [ ] （面試題）「V8 物件加屬性時值存在哪？什麼時候溢位？什麼時候變 dictionary？」能完整答出
- [ ] 理解為什麼這兩軸的佈局對 OOB 攻擊路徑判斷是關鍵

## 延伸閱讀

- **[“Fast properties in V8” — v8.dev/blog/fast-properties](https://v8.dev/blog/fast-properties)**
  - **這篇說什麼**：官方權威講 in-object / fast (PropertyArray) / dictionary 三種 named 儲存、以及 elements 的快慢模式。
  - **讀哪裡**：整篇。本章的每個 `%DebugPrint` 現象在這裡都有結構圖對應。
  - **關聯**：本章是它的實測版；讀完你能把 dump 和圖對起來。
- **[“Elements kinds in V8” — v8.dev/blog/elements-kinds](https://v8.dev/blog/elements-kinds)**
  - **這篇說什麼**：indexed element 那條軸的深入——六種 elements kind、fast vs dictionary elements、稀疏陣列。
  - **讀哪裡**：現在讀「fast vs dictionary elements」段落；六種 kind 的轉換是 [Ch 7](./07-jsarray-elements-kind.md) 的正課。
  - **關聯**：補齊本章刻意只點到的 elements 軸細節。
- **[V8 原始碼 `src/objects/property-array.h`、`src/objects/dictionary.h`、`src/objects/js-objects.h`](https://source.chromium.org/chromium/chromium/src/+/main:v8/src/objects/property-array.h)**
  - **讀哪裡**：`PropertyArray` 的欄位佈局、`NameDictionary` 的結構、`JSObject::RawFastPropertyAt`。
  - **關聯**：想確認你這個 commit 的 PropertyArray/Dictionary 實際佈局（利用要用的 offset）時的權威來源。

named 那條軸講完，indexed 那條軸值得整整一章——因為陣列的 elements kind 是 V8 利用最經典的戰場之一。下一章拆六種 elements kind、它們的轉換規則、COW，以及「elements kind confusion」為什麼是入門級的強力原語。

→ [Ch 7 — JSArray 與 elements kind 轉換](./07-jsarray-elements-kind.md)
