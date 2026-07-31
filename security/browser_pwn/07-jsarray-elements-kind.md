# Ch 7 — JSArray 與 elements kind 轉換

> **目標**：吃透 V8 陣列的 **elements kind**——PACKED/HOLEY × SMI/DOUBLE/OBJECT 六種、它們的轉換規則（**只升不降**的單向格）、COW（copy-on-write），以及本課的重頭伏筆：**為什麼 elements kind confusion 是最經典、最好用的入門利用原語**。一句話先講：如果你能讓 V8 相信一個陣列是 `DOUBLE`（值攤平存）但實際存的是物件指標（或反之），你就同時拿到了 `addrof` 和 `fakeobj`。這一章是 Part 3 的地基。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，`~/v8build/v8/out/x64.release/`。d8：`~/v8build/v8/out/x64.release/d8`。本章所有 `%DebugPrint`、elements kind、transition 全部真跑。

## 為什麼需要這個？

JS 只有一種 `Array`，但 V8 為了速度，**偷偷把陣列分成六種內部表示**，依「裡面裝什麼」動態切換。裝純小整數的陣列，V8 用最緊湊的方式存；裝浮點數的，攤平存 8-byte double；裝混雜物件的，存 tagged 指標。每種的讀寫 fast-path 都不同——這是 V8 讓 `for (i...) a[i]+=1` 快到接近 C 的祕密。

對利用者，這六種 kind 之間的**轉換**是金礦。因為：

1. **DOUBLE 陣列直接攤平存 8-byte 原始位元**（不裝箱成 HeapNumber）——它是一個能攜帶任意 64-bit 值的容器。
2. **OBJECT 陣列存的是 tagged 指標**——V8 會把裡面的值當指標解引用。
3. 如果你能製造一個「Map 說是 DOUBLE、但被當 OBJECT 用」或反過來的混淆，V8 就會把 double 位元當指標、或把指標當 double——這正是 `addrof`/`fakeobj`（[Ch 15](./15-addrof-fakeobj.md)）。

而 TurboFan 對 elements kind 的**推測**（「這個陣列一直是 DOUBLE，我省掉檢查」）一旦被騙，就是 type confusion（[Ch 12](./12-speculation-deopt.md)）。所以 elements kind 是 Part 3 和 Part 4 共同的核心地形。

## 先建立直覺：六格單向棋盤

```
                 SMI              DOUBLE            OBJECT（elements/tagged）
             ┌──────────────┬──────────────────┬────────────────────┐
   PACKED    │ PACKED_SMI   │  PACKED_DOUBLE   │  PACKED_ELEMENTS    │
   （無洞）   └──────┬───────┴────────┬─────────┴──────────┬─────────┘
                    │ 出現 hole       │                    │
                    ▼                 ▼                    ▼
             ┌──────────────┬──────────────────┬────────────────────┐
   HOLEY     │ HOLEY_SMI    │  HOLEY_DOUBLE    │  HOLEY_ELEMENTS     │
   （有洞）   └──────────────┴──────────────────┴────────────────────┘

   轉換方向：只能往右（型別泛化）、往下（變 holey）走，永不回頭。
   越往右下越「不特化」、越慢，但越通用。
```

兩個維度：

- **PACKED（緊湊）vs HOLEY（有洞）**：陣列有沒有「洞」（未初始化的索引，如 `[1,,3]`）。PACKED 保證每格有值，fast-path 不用檢查 hole，更快。
- **SMI → DOUBLE → OBJECT（特化程度）**：裝什麼。SMI 最特化（純小整數）、DOUBLE 次之（浮點）、OBJECT 最通用（任何值，存 tagged 指標）。

**核心規則：轉換只升不降。** 一旦陣列從 PACKED 變 HOLEY、或從 SMI 泛化到 DOUBLE，就回不去了（就算你把洞填滿、把 double 拿掉）。這個單向性是利用要利用的性質之一。

## 底層機制：親眼看六種 kind 與轉換

跑一個陣列，逐步「污染」它，看 elements kind 一路泛化。這一整段是本章最該逐格對照的實測：

```
$ d8 --allow-natives-syntax -e '
  let a=[1,2,3];  %DebugPrint(a);              // PACKED_SMI
  a[3]=4.4;       %DebugPrint(a);              // → PACKED_DOUBLE
  a[4]={};        %DebugPrint(a);'             // → PACKED_ELEMENTS
```

**第一步：PACKED_SMI_ELEMENTS**

```
 - map: 0x33580100c90d <Map[16](PACKED_SMI_ELEMENTS)> [FastProperties]
 - elements: 0x33580101e3c1 <FixedArray[3]> [PACKED_SMI_ELEMENTS (COW)]
 - elements: {
           0: 1
           1: 2
           2: 3
 }
```

純小整數，用 `FixedArray` 存，值直接是 SMI。（注意 `(COW)`，稍後講。）

**第二步：加一個 double `4.4` → PACKED_DOUBLE_ELEMENTS**

```
 - map: 0x33580100cfc9 <Map[16](PACKED_DOUBLE_ELEMENTS)> [FastProperties]
 - elements: 0x33580104b211 <FixedDoubleArray[22]> [PACKED_DOUBLE_ELEMENTS]
 - elements: {
           0: 1 (0x3ff0000000000000)
           1: 2 (0x4000000000000000)
           2: 3 (0x4008000000000000)
           3: 4.4 (0x401199999999999a)
        4-21: <the_hole>
 }
```

**這一步是全章最重要的觀察**：

- backing store 從 `FixedArray` 換成 **`FixedDoubleArray`**——連原本的整數 `1,2,3` 都被**攤平成 8-byte IEEE754 double** 重存（`1` → `0x3ff0000000000000`）。
- **值是原始位元、不是 HeapNumber 指標**。`4.4` 就是 `0x401199999999999a` 這 8 個 byte 直接躺在 backing store 裡。
- 這意味著：**一個 DOUBLE 陣列是一塊你能用 JS 精準寫入任意 64-bit 位元的記憶體**。你在 JS 寫 `a[3] = someDouble`，那 8 個 byte 就原封不動進了 backing store。這就是 `addrof` 為什麼要把目標「弄成 double 陣列的一格來讀出」的原因。

**第三步：加一個物件 `{}` → PACKED_ELEMENTS**

```
 - map: 0x33580100d051 <Map[16](PACKED_ELEMENTS)> [FastProperties]
 - elements: 0x33580104b2e5 <FixedArray[22]> [PACKED_ELEMENTS]
 - elements: {
           0: 0x33580104b369 <HeapNumber 1.0>    ← 現在整數/浮點都裝箱成 HeapNumber 指標了
           1: 0x33580104b35d <HeapNumber 2.0>
           2: 0x33580104b351 <HeapNumber 3.0>
           3: 0x33580104b345 <HeapNumber 4.4>
           4: 0x33580104b2c9 <Object map = 0x335801005741>   ← 那個 {}
        5-21: 0x33580002fffd <the_hole_value>
 }
```

泛化到最通用的 `PACKED_ELEMENTS`：backing store 回到 `FixedArray`，但現在每格是 **tagged 指標**——連數字都被裝箱成 HeapNumber、存指標。**V8 會把這裡的每個值當指標解引用**。這就是 confusion 的另一半：若 V8 以為這是 OBJECT 陣列而去解引用，但你塞的其實是個受控的假位址……（[Ch 15](./15-addrof-fakeobj.md)）。

### PACKED → HOLEY：戳一個洞

```
$ d8 --allow-natives-syntax -e 'let b=[1,2,3]; b[5]=9; %DebugPrint(b);'
 - map: 0x33580100cf85 <Map[16](HOLEY_SMI_ELEMENTS)> [FastProperties]
 - elements: {
           0: 1
           1: 2
           2: 3
         3-4: 0x33580002fffd <the_hole_value>    ← 跳過 index 3,4 → 洞
           5: 9
 }
```

`b[5]=9` 跳過了 index 3、4，V8 把它們填成 **`the_hole_value`**（一個特殊的內部 sentinel，`0x..2fffd`），並把陣列從 `PACKED_SMI` 降級成 **`HOLEY_SMI`**。「洞」不是 `undefined`——它是一個 V8 內部值，讀到它時 V8 才回傳 `undefined`。HOLEY 陣列的每次讀取都要多一步「是不是 hole」的檢查，所以比 PACKED 慢。**`the_hole` 在利用裡也是個角色**：某些原語靠讀寫 hole、或把 hole 洩漏到 JS 層製造混淆。

### transition 存在 Map 裡

回顧 [Ch 5](./05-map-hidden-class.md)：elements kind **存在 Map**，所以每次 kind 轉換就是換一張 Map、走一條 transition。看 PACKED_SMI 的 Map 掛的 transition：

```
0x33580100c90d: [Map] (PACKED_SMI_ELEMENTS)
 - transitions #1:
     (transition to HOLEY_SMI_ELEMENTS) -> 0x33580100cf85 <Map[16](HOLEY_SMI_ELEMENTS)>
```

和它的 back pointer 鏈一起看，六張 Map 構成前面那張棋盤的 transition 圖。**「elements kind confusion」本質上就是「Map confusion」**——這是為什麼 [Ch 5](./05-map-hidden-class.md) 要先鋪 Map。

## COW：copy-on-write elements

你可能注意到 `[1,2,3]` literal 的 elements 標了 **`(COW)`**。array literal 的初始 elements 是 **copy-on-write（寫時複製）**：多個由同一 literal 產生的陣列**共享同一份 elements backing store**，直到有人寫入才複製。實測：

```
$ d8 --allow-natives-syntax -e '
  function mk(){return [1,2,3];}
  let c1=mk(), c2=mk();
  %DebugPrint(c1);         // c1.elements = 0x..e5a5 (COW)
  c1[0]=99;                // 寫入 → 觸發複製
  %DebugPrint(c1);         // c1.elements = 0x..b449 (新的，非 COW)
  %DebugPrint(c2);'        // c2.elements = 0x..e5a5 (還是共享那份)
```

實跑結果：`c1` 和 `c2` 初始都指向 `elements: 0x..e5a5 [PACKED_SMI_ELEMENTS (COW)]`；`c1[0]=99` 後，**`c1` 換到全新的 `0x..b449`**（非 COW），而 **`c2` 仍指向 `0x..e5a5`** 保有原值 `1,2,3`。這證明「寫入時才複製、且只複製寫的那個」。

COW 的利用意義：它是 V8 的一個「共享狀態」，歷史上有過 COW 相關的 confusion（例如優化器沒正確處理 COW→非COW 的轉換）。知道 literal 陣列一開始是 COW，能解釋一些「明明改了 a、b 卻變了」的困惑。

## 六種 elements kind 對照表

| elements kind | backing store | 每格存什麼 | 讀取要檢查 hole？ | 利用價值 |
|---|---|---|---|---|
| `PACKED_SMI` | FixedArray | SMI（值） | 否 | 起點；最特化 |
| `HOLEY_SMI` | FixedArray | SMI 或 the_hole | 是 | hole 相關原語 |
| `PACKED_DOUBLE` | **FixedDoubleArray** | **原始 8-byte double** | 否 | **`addrof` 載體**：能攜任意 64-bit |
| `HOLEY_DOUBLE` | FixedDoubleArray | double 或 hole | 是 | 同上 + hole |
| `PACKED_ELEMENTS` | FixedArray | **tagged 指標** | 否 | **`fakeobj` 側**：V8 會解引用 |
| `HOLEY_ELEMENTS` | FixedArray | 指標或 the_hole | 是 | 最通用；物件的預設 |

記住兩個對角：**DOUBLE（攤平位元）** 和 **OBJECT/ELEMENTS（tagged 指標）** 是 confusion 的兩端。SMI 是起點，HOLEY 是「有洞」的變體。

## 為什麼 elements kind confusion 能利用

把全章收束成利用者的核心圖景：

> 一個 `PACKED_DOUBLE` 陣列的第 3 格存的是**你寫進去的原始 8-byte 位元**。一個 `PACKED_ELEMENTS` 陣列的第 3 格存的是**一個 V8 會拿去解引用的 tagged 指標**。這兩塊記憶體的**位元佈局幾乎一樣**（都是一排 8-byte／壓縮後 slot），差別**只在 Map 說它是哪種**。

於是兩個夢幻原語自然浮現：

- **`addrof(obj)`**：把 `obj` 放進一個 OBJECT 陣列（存的是 `obj` 的指標），然後透過 confusion 讓 V8 以為那是 DOUBLE 陣列去讀——你就把 `obj` 的**位址當成一個 double 數字讀了出來**。
- **`fakeobj(addr)`**：把一個數字 `addr` 寫進一個 DOUBLE 陣列（存原始位元），然後讓 V8 以為那是 OBJECT 陣列——V8 就把你的 `addr` **當成物件指標解引用**，你憑空造出一個「物件」。

達成 confusion 的手段有很多（TurboFan typer bug、直接改 Map、OOB 蓋掉 elements kind……），[Ch 15](./15-addrof-fakeobj.md)、[Ch 16](./16-fake-object-rw.md) 專門教。本章你只要把「**DOUBLE=原始位元、OBJECT=會被解引用的指標、差別只在 Map**」這個對立刻進骨子裡。

## 進階：再往深一層

- **length vs elements capacity**：`length`（JS 可見長度）和 elements backing store 的實際容量（capacity）是**兩回事**。上面 `FixedDoubleArray[22]` 但 length 才 4——V8 預配了 22 格緩衝。改 `length` 大於 capacity 是一類 OOB 的溫床（[Ch 17](./17-typedarray-attack.md)）。
- **DICTIONARY_ELEMENTS**：`a[100000]=1` 造成極稀疏陣列時，elements 退回 `NumberDictionary`（hash），避免配十萬格。這是第七種（慢速）kind，fast-path 不套用。
- **transition 的不可逆是「per-map」**：陣列物件本身沿 transition 走到更泛化的 Map；但**新建**一個 `[1,2,3]` 仍從 PACKED_SMI 起步。不可逆說的是「這個陣列實例回不去」，不是「這個形狀消失」。
- **TurboFan 的 elements kind 推測**：優化器會針對「這個陣列一直是 PACKED_DOUBLE」生成省檢查的快碼，並掛 deopt 守衛。若能讓 kind 在它背後偷偷變（side-effect），守衛失效就是 type confusion——[Ch 12](./12-speculation-deopt.md)、Part 4 的主戲。
- **原始碼**：`src/objects/elements-kind.h`（六種 kind 的 enum 值——利用時要用的常數就在這，綁死你的 commit）、`src/objects/elements.cc`（各 kind 的存取實作）。

## 踩雷集錦

1. **錯誤直覺：「DOUBLE 陣列裡存的是 HeapNumber 指標」。正確：** `FixedDoubleArray` **攤平存原始 8-byte double 位元**，不裝箱。這正是它能當「任意 64-bit 攜帶者」的原因，是 `addrof` 的載體。
2. **錯誤直覺：「elements kind 可升可降，填滿洞就回 PACKED」。正確：** **只升不降**。變 HOLEY / 泛化到 DOUBLE/OBJECT 後回不去，即使你把洞填滿、把 double 移除。
3. **錯誤直覺：「hole 就是 undefined」。正確：** hole 是內部 sentinel `the_hole_value`，讀到它 V8 才回傳 undefined。它是獨立的值，HOLEY 陣列每次讀都要檢查它。
4. **錯誤直覺：「array literal 各自有獨立 elements」。正確：** 同 literal 產生的陣列初始 **COW 共享** 同一份 elements，寫入才複製。忽略這點會誤判佈局。
5. **錯誤直覺：「length 就是 backing store 大小」。正確：** length（JS 可見）≠ elements capacity（實配容量），V8 預留緩衝。兩者不一致是 OOB 的溫床。

## 動手練習

1. 對六種 kind 各造一個代表陣列（`[1,2,3]`、`[1,,3]`、`[1.1]`、`[1.1,,3.3]`、`[{},1]`、`[{},,1]`），`%DebugPrint` 全部，把每個的 map、elements kind、backing store 型別（FixedArray vs FixedDoubleArray）列成你自己的對照表，和本章的表核對。
2. 造一個 `PACKED_DOUBLE` 陣列，寫入一個特定的 double（例如 `a[0] = 1.5`），到 [Ch 3](./03-value-representation.md) 學的把它的 8-byte 位元算出來（`1.5 = 0x3ff8000000000000`），確認 `%DebugPrint` 印的原始位元一致。體會「double 陣列 = 我能精準寫入 64-bit 的容器」。
3. 重現 COW 實驗：用工廠函式造兩個同 literal 陣列，`%DebugPrint` 確認初始共享 elements；改其中一個，再 `%DebugPrint` 確認只有被改的那個換了 backing store。畫出前後兩張佈局圖。

## 本章重點整理

- V8 陣列有**六種 elements kind**：PACKED/HOLEY × SMI/DOUBLE/OBJECT，依內容動態切換，存在 **Map** 裡（故 kind confusion = Map confusion）。
- 轉換是**只升不降**的單向格：PACKED→HOLEY、SMI→DOUBLE→OBJECT，回不了頭。
- **DOUBLE 陣列（FixedDoubleArray）攤平存原始 8-byte 位元、不裝箱** → 能攜帶任意 64-bit，是 `addrof` 載體；**OBJECT 陣列存 tagged 指標、V8 會解引用** → 是 `fakeobj` 側。兩塊佈局幾乎一樣，差別只在 Map。
- **hole** 是內部 sentinel `the_hole_value`（非 undefined）；**COW** 讓 literal 陣列初始共享 elements、寫入才複製。
- **elements kind confusion 是最經典的入門原語**：讓 DOUBLE/OBJECT 混淆即同時得到 `addrof`+`fakeobj`（[Ch 15](./15-addrof-fakeobj.md)）；TurboFan 對 kind 的推測被騙就是 type confusion（[Ch 12](./12-speculation-deopt.md)）。

## 自我檢核

- [ ] 能默寫六種 elements kind 的棋盤與轉換方向，並解釋為什麼只升不降
- [ ] 能解釋 DOUBLE 陣列存原始位元 vs OBJECT 陣列存指標的差別，及各自的利用角色
- [ ] 能說出 hole（the_hole_value）不是 undefined、以及 HOLEY 為何較慢
- [ ] 能描述 COW 的行為並解釋它為何是共享狀態、有何利用意涵
- [ ] 能講清楚「elements kind confusion 為什麼同時給你 addrof 和 fakeobj」
- [ ] （面試題）「V8 有幾種 array elements kind？`[1,2,3]` 加一個 `1.1` 會發生什麼？為什麼 double 陣列對 exploit 特別有用？」能完整答出

## 延伸閱讀

- **[“Elements kinds in V8” — v8.dev/blog/elements-kinds](https://v8.dev/blog/elements-kinds)**
  - **這篇說什麼**：官方講六種 elements kind、單向 transition 格、PACKED vs HOLEY 的效能差、COW。
  - **讀哪裡**：整篇，配它的 transition 格圖和本章棋盤對照。
  - **關聯**：本章實測的一切，這裡有官方的設計說明與效能數據。
- **[doar-e / Jeremy Fetiveau “Introduction to TurboFan” 及其 elements kind confusion 範例](https://doar-e.github.io/blog/2019/01/28/introduction-to-turbofan/)**
  - **這篇說什麼**：從 TurboFan 角度講它如何推測 elements kind、以及 confusion 如何變成 addrof/fakeobj。
  - **讀哪裡**：elements kind 與 typer 相關段落先讀。
  - **關聯**：把本章的「差別只在 Map」直接接到 Part 4 的 typer bug 與 Part 3 的原語。
- **[saelo “Attacking JavaScript Engines” — Phrack 0x46 / “Exploiting the Wren...” 類經典](http://www.phrack.org/issues/70/3.html)**
  - **這篇說什麼**：奠基性地講 addrof/fakeobj 如何從陣列型別混淆建構——雖以 JSC 為例，觀念完全相通。
  - **讀哪裡**：addrof/fakeobj 建構那節。
  - **關聯**：本章鋪的 DOUBLE/OBJECT 對立，在這篇是完整的原語建構；直通 [Ch 15](./15-addrof-fakeobj.md)。

陣列的 elements 這條軸講透了。但 V8 還有一類特殊的「陣列」——`ArrayBuffer` / `TypedArray` / `DataView`，它們不走 elements kind，而是直接握著一個指向原始 bytes 的 backing store 指標。這正是 Part 3 任意讀寫的**終極目標物件**。下一章拆它們的內部結構。

→ [Ch 8 — ArrayBuffer / TypedArray / DataView 與 backing store](./08-arraybuffer-typedarray.md)
