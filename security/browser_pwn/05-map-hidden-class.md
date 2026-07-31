# Ch 5 — Map / hidden class：物件的形狀與 transition

> **目標**：把 V8 最核心的一個結構 **Map**（別名 hidden class、shape）吃透——它是什麼、為什麼每個 HeapObject 第一個欄位都是它、Map transition（形狀轉移）怎麼形成一棵樹、DescriptorArray 怎麼描述屬性、以及**為什麼 Map 是整個利用鏈裡最關鍵的欄位**。一句先講在前面：`addrof`/`fakeobj`、type confusion、fake object，本質上都是在**騙 V8「這個物件的 Map 是什麼」**。這一章沒吃透，Part 3、Part 4 都會卡。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，`~/v8build/v8/out/x64.release/`。d8：`~/v8build/v8/out/x64.release/d8`。本章所有 `%DebugPrint` 真跑。

## 為什麼需要這個？

JS 物件是動態的：`let o = {}; o.x = 1; o.y = 2;` 隨時能加欄位。最天真的實作是每個物件自帶一張 hash table（key → value）。但那樣**每次讀屬性都要 hash 查找**，慢到不可接受——JS 程式讀屬性的頻率極高。

V8 的解法借自 Self / V8 團隊的老智慧：**把「形狀」和「值」分開**。「這個物件有哪些欄位、各在第幾個 slot、什麼型別」這種**結構資訊**抽出來，存進一個叫 **Map** 的共享物件；物件本體只存**值**。形狀相同的物件**共用同一個 Map**。這樣讀 `o.x` 時，V8 查 Map 一次得知「x 在第 0 個 slot」，之後靠 **inline cache** 記住，往後直接取 slot、不再查——快到接近靜態語言。

對利用者，Map 之所以是聖杯，因為它回答了「V8 怎麼解讀一塊記憶體」這個終極問題：**同一塊 bytes，配上不同的 Map，V8 就當成完全不同的東西**。把一個 double 陣列的 Map 換成物件陣列的 Map，V8 就會把裡面的 double 位元當成物件指標解讀——這就是 type confusion 的核心，也是 `fakeobj` 的原理。所以「Map 是什麼、怎麼被改、改了會怎樣」是你這門課從頭用到尾的軸線。

## 先建立直覺：Map 是「怎麼讀這塊記憶體」的說明書

```
   兩個形狀相同的物件，共用一張 Map
                                    ┌─────────────────────────┐
   o1 @ 0x..339                     │  Map @ 0x..ee01          │
   ┌──────────────┐   map 指標      │  type: JS_OBJECT_TYPE    │
   │ map ─────────┼───────────────► │  instance size: 20       │
   │ properties   │            ┌───►│  inobject props: 2       │
   │ elements     │            │    │  elements kind: HOLEY    │
   │ [slot0] = 1  │  ← a 的值   │    │  descriptors ───────┐    │
   │ [slot1] = 2  │  ← b 的值   │    │  back pointer ...   │    │
   └──────────────┘            │    └─────────────────────┼────┘
                               │                          ▼
   o2 @ 0x..365                │              ┌──────────────────────┐
   ┌──────────────┐            │              │ DescriptorArray       │
   │ map ─────────┼────────────┘              │  #0 "a" → field slot0 │
   │ properties   │  同一張 Map！             │  #1 "b" → field slot1 │
   │ [slot0] = 9  │                           └──────────────────────┘
   │ [slot1] = 8  │
   └──────────────┘
```

物件本體只有：map 指標、properties 指標、elements 指標、然後是 in-object 的值。**「哪個 slot 是哪個屬性、叫什麼名字、什麼型別」全在 Map（經由 DescriptorArray）裡**，被所有同形狀物件共享。物件本身不知道自己的 slot0 叫 "a"——那是 Map 說的。

這也是為什麼「換掉 Map」威力這麼大：物件的值一個沒動，只要 Map 換了，V8 對這堆值的**解讀方式**就整個變了。

## 底層機制：親手驗證「同形狀共用 Map」

先證明核心命題。兩個屬性完全同名同順序的物件：

```
$ d8 --allow-natives-syntax -e 'let p1={a:1,b:2}, p2={a:9,b:8}; %DebugPrint(p1); %DebugPrint(p2);'
DebugPrint: 0xba70104c339: [JS_OBJECT_TYPE]
 - map: 0x0ba70101ee01 <Map[20](HOLEY_ELEMENTS)> [FastProperties]
 ...
    #a: 1 (const data field 3, in-obj, attrs: [WEC])
    #b: 2 (const data field 4, in-obj, attrs: [WEC])
 ...
DebugPrint: 0xba70104c365: [JS_OBJECT_TYPE]
 - map: 0x0ba70101ee01 <Map[20](HOLEY_ELEMENTS)> [FastProperties]
 ...
    #a: 9 (const data field 3, in-obj, attrs: [WEC])
    #b: 8 (const data field 4, in-obj, attrs: [WEC])
```

兩個物件位址不同（`0x..c339` vs `0x..c365`），但 **map 是同一個 `0x0ba70101ee01`**。這就是命題的鐵證：形狀決定 Map，值不影響 Map。V8 靠這個讓「所有 `{a,b}` 物件」共用一份形狀元資料，也讓 inline cache 能跨物件生效。

## Map 這張說明書上寫了什麼

拆一個物件的完整 Map dump，逐欄位讀（這是你要練到能秒讀的技能）：

```
$ d8 --allow-natives-syntax -e 'let o={x:1,y:2,z:3}; %DebugPrint(o);'
0xba70101e5e1: [Map] in OldSpace
 - map: 0x0ba7010049a9 <MetaMap ...>          ← Map 自己的 map（見下「MetaMap」）
 - type: JS_OBJECT_TYPE                         ← 這是哪種 instance type
 - instance size: 24                            ← 物件本體佔幾 byte（含 map/props/elems + inobj）
 - inobject properties: 3                       ← 有幾個 in-object 屬性槽
 - unused property fields: 0                     ← 還有幾個空槽
 - elements kind: HOLEY_ELEMENTS                ← indexed 元素的種類（Ch 7）
 - enum length: invalid
 - stable_map                                    ← 這張 map 目前穩定（沒被 deprecate）
 - back pointer: 0x0ba70101e5b9 <Map[24]...>    ← 形狀樹的父節點（見下「transition」）
 - prototype_validity_cell: ...                  ← prototype 是否被動過的守衛
 - instance descriptors (own) #3: 0x..b271 <DescriptorArray[3]>  ← 屬性清單（見下）
 - prototype: 0x0ba701005b95 <Object map ...>    ← 這個物件的 __proto__
 - constructor: ... <JSFunction Object ...>
 - construction counter: 0
```

利用時你最該盯的幾欄：

- **`type` / `instance size`**：換 Map 做 type confusion 時，新舊 Map 的 type 與 size 差異，決定 V8 會把記憶體錯讀成什麼、會不會越界。
- **`elements kind`**：陣列的元素種類。[Ch 7](./07-jsarray-elements-kind.md) 整章在講它，而它就是**存在 Map 裡**的——這是「elements kind confusion」為什麼靠換 Map 達成。
- **`prototype`**：`__proto__`。改 prototype 會使 map transition（見下），也是 prototype pollution 類問題的底層。
- **`instance descriptors`**：指向 DescriptorArray，屬性的完整清單。
- **`back pointer`**：指向形狀樹裡的父 Map——這是理解 transition 的鑰匙。

### MetaMap：Map 的 Map

你注意到 Map 自己也有 `- map:` 一行，指向一個 **MetaMap**。因為 Map 也是 HeapObject，HeapObject 第一欄恆為 map（[Ch 3](./03-value-representation.md) 的鐵律），所以 Map 的 map 就是 MetaMap。這條鏈到 MetaMap 為止收斂（MetaMap 的 map 是自己）。實戰上你很少直接碰 MetaMap，知道「為什麼 Map 也有 map」即可，不然會覺得無限遞迴。

## Map transition：形狀是一棵樹，不是一張表

這是本章最重要的機制。你逐步加屬性時，V8 **不是**改物件的 Map，而是**沿著一條 transition 鏈往下走到新 Map**。看加屬性的 transition：

```
$ d8 --allow-natives-syntax -e 'let o={x:1,y:2,z:3}; %DebugPrint(o);'
0xba70101e5e1: [Map]                          ← {x,y,z} 的 map
 - back pointer: 0x0ba70101e5b9 <Map[24]...>  ← 這是 {x,y} 的 map
```

`{x,y,z}` 的 Map 的 back pointer 指向 `{x,y}` 的 Map。這條鏈完整長這樣：

```
   空物件 {} 的 Map
        │  加 "x"
        ▼
   {x} 的 Map ──── 加 "y" ────► {x,y} 的 Map ──── 加 "z" ────► {x,y,z} 的 Map
        (每一步是一次 transition；back pointer 反向指回父節點)
```

**為什麼做成樹？** 因為這樣「凡是用 `{x:_, y:_}` literal 建的物件」都會走到**同一個** `{x,y}` Map，自然共用形狀——不用比對欄位，順著 transition 走就到同一個節點。這是「同形狀共用 Map」能發生的機制。

從陣列的角度看 transition 更直觀。一個陣列改變 elements kind 時，也是走 Map transition：

```
$ d8 --allow-natives-syntax -e 'let a=[1,2,3]; %DebugPrint(a);'
0x33580100c90d: [Map] (PACKED_SMI_ELEMENTS)
 - transitions #1:
     (transition to HOLEY_SMI_ELEMENTS) -> 0x33580100cf85 <Map[16](HOLEY_SMI_ELEMENTS)>
```

`PACKED_SMI` 的 Map 上掛著一條「若出現 hole → 轉去 HOLEY_SMI」的 transition。這就是 [Ch 7](./07-jsarray-elements-kind.md) 講的 elements kind 升級——每次升級**就是換到 transition 樹上的另一個 Map**。所以「elements kind confusion」和「Map confusion」是同一件事的兩個名字。

### transition 樹對利用的意義

- **Map 位址在同版本、同建構順序下高度可預測**：因為所有 `[1,2,3]` 都走到同一個 PACKED_SMI Map，你能預先知道某個形狀對應哪張 Map，這對「準備一張假 Map」或「認出目標 Map」很有用。
- **強制某物件走到你要的 Map**：利用時常需要「讓受害物件的 Map 變成攻擊者期望的那張」，做法就是觸發對的 transition（加特定屬性、改 elements kind）。
- **deprecated / stable map**：transition 樹上的 Map 可能被 deprecate（見進階），這關係到 map check 的可繞過性。

## DescriptorArray：屬性的細目清單

Map 說「有 3 個 in-object 屬性」，但**每個屬性叫什麼、在第幾 slot、什麼型別、什麼 attribute**，寫在 Map 指向的 **DescriptorArray** 裡：

```
 - All own properties (excluding elements): {
    #x: 1 (const data field 3, in-obj, attrs: [WEC])
    #y: 2 (const data field 4, in-obj, attrs: [WEC])
    #z: 3 (const data field 5, in-obj, attrs: [WEC])
 }
```

逐項讀 `#x: 1 (const data field 3, in-obj, attrs: [WEC])`：

- **`x`**：屬性名。
- **`const data field`**：這是個 data 屬性（不是 accessor / getter），而且目前是 const（V8 追蹤「值有沒有被改過」做優化——這也是一類 side-effect 攻擊面）。
- **`field 3`**：它在物件記憶體的第 3 個 word（前面 0/1/2 是 map/properties/elements）。
- **`in-obj`**：值就存在物件本體內（相對於溢位到 PropertyArray，見 [Ch 6](./06-properties-elements.md)）。
- **`attrs: [WEC]`**：Writable / Enumerable / Configurable，屬性的三個旗標。

DescriptorArray 被同形狀物件共享（掛在 Map 上），所以它是「形狀」的一部分，不是「值」的一部分。想深挖 accessor（getter/setter）怎麼在 descriptor 裡表示，見 [Ch 6](./06-properties-elements.md)。

## Map 為什麼是利用時最關鍵的欄位

把前面所有線索收束成一句可操作的結論：

> **V8 對一塊記憶體的所有解讀，都由那塊記憶體開頭的 Map 指標決定。控制了一個物件的 Map，你就控制了 V8 怎麼看它。**

這句話展開成三個你之後會反覆用的利用手法：

1. **type confusion（[Ch 15](./15-addrof-fakeobj.md) 起）**：讓兩個不同 Map 的物件在某條 JIT 路徑上被當成同一種。例如把 double 陣列（`FLOAT64`）當成物件陣列（`OBJECT`）——V8 就把 double 位元當指標解讀，達成 `addrof`（把物件當數字讀）與 `fakeobj`（把數字當物件用）。
2. **fake object**：偽造一塊記憶體，開頭放一個「看起來合法的 Map 指標」，讓 V8 把它當成真物件。因為 Map 位址在同版本可預測（transition 樹），你能算出要放哪張 Map。
3. **Map 欄位篡改**：拿到相對讀寫後，直接改一個真物件的 Map 欄位，把它「變身」成另一種型別（例如把 `length` 改大、把 elements kind 改成能存 raw double）。

這也是為什麼 Part 1 前四章要先鋪 tagging（[Ch 3](./03-value-representation.md)）和 compression（[Ch 4](./04-pointer-compression.md)）：**Map 指標本身就是一個 32-bit 壓縮的 tagged 指標**，你偽造/篡改它時得懂它的編碼。

## 對比：Map（形狀）vs 一般物件的值

| 面向 | Map（形狀元資料） | 物件本體（值） |
|---|---|---|
| 存什麼 | type、size、elements kind、descriptor、prototype、transition | 各屬性/元素的實際值 |
| 誰共享 | **所有同形狀物件共享一張** | 每個物件獨立 |
| 改它的後果 | 改變 V8 對「所有共享它的物件」的解讀 | 只改一個物件的一個值 |
| 利用價值 | **極高**：控制 Map = 控制型別解讀 | 中：是資料，但 Map 才決定怎麼讀 |
| 住在哪 | OldSpace（多半） | young/old space |

## 進階：再往深一層

- **deprecated map 與 map migration**：當一個形狀的某屬性型別「泛化」（例如原本存 SMI 的 field 被寫入 double），舊 Map 會被 **deprecated**，物件在下次存取時 **migrate** 到新 Map。這個過程有歷史 bug（migration 途中的中間狀態），也是「map check 為何有時能繞過」的來源。dump 裡的 `stable_map` / `deprecated` 標記就是這件事。
- **dictionary map（`DictionaryProperties`）**：物件太動態（大量增刪屬性）時，V8 放棄 fast 形狀、退回 hash table 模式，Map 標 `dictionary_map`（[Ch 6](./06-properties-elements.md) 細講）。這時「共用 Map」的假設不成立——所有 dictionary 物件的 fast-path 優化都沒了。
- **prototype 與 map 的耦合**：改一個物件的 `__proto__` 會產生 map transition（prototype 是 Map 的一部分）。inline cache、TurboFan 對 prototype chain 的假設，是 [Ch 20](./20-checkbounds-redundancy-elimination.md) 一類 prototype 攻擊面的基礎。
- **`Map` 的原始碼**：`src/objects/map.h` / `map.cc`、`descriptor-array.h`。Map 的欄位佈局（第幾個 word 是 instance type、第幾個是 bit field）在利用時是要背的偏移——但**綁死你這個 commit**，跨版本會變。
- **Map 位址的可預測性 vs GC**：Map 多在 OldSpace，相對不易被 compaction 搬，比一般物件穩。這對「認出目標 Map / 準備 fake Map」是利多。

## 踩雷集錦

1. **錯誤直覺：「加屬性是改物件的 Map」。正確：** 是**換**到 transition 樹上的另一張 Map（原 Map 不動、被別的同形狀物件繼續共用）。back pointer 就是這棵樹的反向邊。
2. **錯誤直覺：「每個物件有自己獨立的形狀資訊」。正確：** 同形狀物件**共享同一張 Map**（本章實測 p1/p2 同 map）。這是 inline cache 和 fast property 能運作的前提。
3. **錯誤直覺：「Map 只是效能優化，跟安全無關」。正確：** Map 是 V8 對記憶體的**型別系統本身**。所有 type confusion、fakeobj、變身攻擊都圍繞「控制 Map」。它是本課的軸線。
4. **錯誤直覺：「elements kind 存在物件裡」。正確：** elements kind 存在 **Map** 裡；改 elements kind = map transition。所以 elements kind confusion 就是 Map confusion（[Ch 7](./07-jsarray-elements-kind.md)）。
5. **錯誤直覺：「Map 位址每次都隨機、沒法預測」。正確：** cage base 隨機（[Ch 4](./04-pointer-compression.md)），但**同版本、同建構順序下，某形狀對應哪張 Map 的相對位置高度可預測**，這正是 fake Map 可行的原因。

## 動手練習

1. 建三個形狀相同的物件（如 `{a:1,b:2}` × 3，值各異），`%DebugPrint` 全部，確認三者 map 位址相同。再建一個 `{a:1,b:2,c:3}`，確認它的 map 的 back pointer 指回 `{a,b}` 的 map——親手畫出這條 transition 鏈。
2. 對 `[1,2,3]`、`[1,2,3.5]`（double）、`[1,,3]`（有 hole）各 `%DebugPrint`，把三張 map 的 `elements kind` 和彼此的 transition/back pointer 記下來，畫出 elements kind 的 transition 樹（這是 [Ch 7](./07-jsarray-elements-kind.md) 的預習）。
3. 建一個物件，反覆 `delete` 又 `add` 大量屬性（40 個以上），`%DebugPrint` 觀察它的 map 何時從 `[FastProperties]` 變成 `[DictionaryProperties]`。記下那個臨界點——這是 [Ch 6](./06-properties-elements.md) 的核心現象。

## 本章重點整理

- **Map（hidden class / shape）** 把物件的「形狀」（type、size、elements kind、descriptors、prototype、transition）抽出來共享；物件本體只存值。**HeapObject[0] 恆為 map 指標。**
- **同形狀物件共用同一張 Map**（實測 p1/p2 同 map），這是 fast property 與 inline cache 的前提。
- 加屬性 / 改 elements kind 是走 **Map transition**——形狀是一棵樹，back pointer 是反向邊；因此「elements kind confusion = Map confusion」。
- **DescriptorArray** 記每個屬性的名字/slot/型別/attribute，掛在 Map 上、被同形狀共享。
- **控制 Map = 控制 V8 對記憶體的型別解讀**，這是 `addrof`/`fakeobj`/type confusion/變身（[Ch 15](./15-addrof-fakeobj.md)–[Ch 18](./18-oob-to-arbitrary-rw.md)）的共同核心，也是本課軸線。

## 自我檢核

- [ ] 能解釋為什麼 V8 要把形狀抽成 Map、這比「每物件一張 hash table」快在哪
- [ ] 能證明並解釋「同形狀共用 Map」，說出這對 inline cache 的意義
- [ ] 能讀懂一個 Map dump 的關鍵欄位（type/size/elements kind/back pointer/descriptors）
- [ ] 能畫出 transition 樹、解釋 back pointer 的方向與用途
- [ ] 能講清楚「為什麼控制 Map 就能做 type confusion」，並連到 addrof/fakeobj
- [ ] （面試題）「V8 的 hidden class 是什麼？加一個屬性時發生什麼？兩個 `{a,b}` 物件共用 map 嗎？」能完整答出

## 延伸閱讀

- **[“JavaScript engine fundamentals: Shapes and Inline Caches” — Mathias Bynens & Benedikt Meurer](https://mathiasbynens.be/notes/shapes-ics)**
  - **這篇說什麼**：shape（=Map）、shape transition、inline cache 的白話全解，配大量圖。
  - **讀哪裡**：整篇，尤其 transition chain 和 IC 那兩節。
  - **關聯**：本章機制的最佳補充；它把「為什麼這樣設計快」講到位。
- **[“Fast properties in V8” — v8.dev/blog/fast-properties](https://v8.dev/blog/fast-properties)**
  - **這篇說什麼**：官方講 in-object vs fast vs dictionary properties、DescriptorArray、以及 map transition 的細節。
  - **讀哪裡**：Map/DescriptorArray 段落先讀；properties backing store 部分是 [Ch 6](./06-properties-elements.md) 的正課。
  - **關聯**：本章的 DescriptorArray dump，這篇給你結構圖。
- **[doar-e / Jeremy Fetiveau “Circumventing Chrome's hardening of typer bugs” 系列](https://doar-e.github.io/blog/2019/05/09/circumventing-chromes-hardening-of-typer-bugs/)**
  - **這篇說什麼**：實戰示範「篡改 Map / elements kind」如何轉成利用原語。
  - **讀哪裡**：先看它怎麼描述「把陣列 map 換掉造成型別混淆」的段落。
  - **關聯**：本章「控制 Map = 控制解讀」的抽象結論，在這裡變成真實 exploit 步驟；是通往 [Ch 15](./15-addrof-fakeobj.md) 的預覽。

Map 決定形狀，但「值到底存在哪」還有分別：有的塞在物件本體內（in-object），有的溢位到外部的 properties 陣列，太動態的退回字典。下一章把「屬性存哪」和「元素存哪」這兩條 backing store 徹底分清楚。

→ [Ch 6 — Properties 與 Elements：in-object / fast / dictionary](./06-properties-elements.md)
