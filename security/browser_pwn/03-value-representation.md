# Ch 3 — 值的表示：SMI、HeapObject、pointer tagging

> **目標**：搞懂 V8 在記憶體裡怎麼表示「一個 JS 值」——一個整數 `42`、一個浮點數 `1.5`、一個物件、一個 `true`/`null`。核心是一件事：**V8 用一個 machine word 同時裝下「小整數」和「指向堆物件的指標」，靠最低幾個 bit 的 tag 區分**。這是整個物件模型的地基，也是 Part 3 `addrof`/`fakeobj` 為什麼可行的根本原因——當你能控制一個 word 的內容，你就控制了 V8 對「這是整數還是指標」的判斷。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，build 在 `~/v8build/v8/out/x64.release/`（`is_debug=false`，`v8_enable_object_print/disassembler/sandbox/pointer_compression=true`）。d8 在 `~/v8build/v8/out/x64.release/d8`。本章所有 `%DebugPrint` 輸出都是這顆 d8 真跑出來的。

## 為什麼需要這個？

你在 `binary_exploitation` 裡，一個 C 的 `int` 就是 32 或 64 bit 的補數，一個指標就是一個位址，兩者在型別系統裡涇渭分明，編譯器幫你記住哪個 word 是什麼。**JS 沒有靜態型別**——同一個變數這行是整數、下一行是物件、再下一行是字串。V8 必須在**執行期**、用**同一個 64-bit 欄位**，表示任何可能的值，而且要能快速判斷「這個 word 現在到底是什麼」。

它的解法是 **pointer tagging（指標標記）**：借用指標的最低幾個 bit（因為堆物件都對齊，位址低位本來就是 0）當型別標籤。這個決定滲透到 V8 的每個角落——你之後看到的每個 `%DebugPrint` 位址、每個 elements 陣列裡的值、每次 type confusion，都建立在「這個 word 的 tag 是什麼」之上。

不懂 tagging，你會看不懂為什麼 `addrof`（把物件位址當成數字讀出來）和 `fakeobj`（把一個數字當成物件指標用）是一體兩面——它們玩的就是「同一個 word，被當成 SMI 還是被當成 HeapObject」這個把戲。

## 先建立直覺：一個欄位，兩種身分

想像一個 64-bit 的盒子，裡面裝的東西有兩種可能：

```
   一個 tagged value（V8 的 Object，就是一個 machine word）
   ┌────────────────────────────────────────────────────────┐
   │  ...............................................  b1 b0  │
   └────────────────────────────────────────────────────────┘
                                                        └┴─ 最低位元
                                                           = tag

   若最低位 = 0  →  這是一個 SMI（Small Integer，小整數），值就寫在上面
   若最低位 = 1  →  這是一個 HeapObject 指標，指向堆上的物件
```

關鍵的巧思：堆物件都對齊到偶數位址（實際上更嚴格），所以合法指標的最低位天生是 0。V8 反過來用——**把 HeapObject 指標故意 +1**，讓它的最低位變 1 當標記；用的時候再 -1（減掉 tag）才是真正的位址。而 SMI 的最低位保持 0，剩下的 bit 拿來存整數。

一個 word 兩種身分，靠這 1 個 bit 分。這就是 tagging 的全部精神。細節（SMI 到底佔幾 bit、指標的 tag 是不是只有 1 bit）下面拆。

## 底層機制：三種東西住在一個 word 裡

### SMI：Small Integer（小整數）

先看最簡單的。跑 `%DebugPrint(42)`：

```
$ d8 --allow-natives-syntax -e '%DebugPrint(42);'
DebugPrint: Smi: 0x2a (42)
```

`0x2a` 就是 42，V8 直接告訴你「這是個 Smi」。注意它**沒有印出任何堆位址、沒有 map**——因為 SMI 根本不在堆上，它整個值就編碼在那個 machine word 裡，不需要配置任何記憶體。這是 V8 的第一個效能招數：小整數零配置、零 GC 負擔。

**SMI 在 x64（開 pointer compression）佔幾 bit？** 在 64-bit 且開 pointer compression 的 V8，tagged value 其實是 **32-bit**（下一章 [Ch 4](./04-pointer-compression.md) 講為什麼）。SMI 用掉最低 1 bit 當 tag（值 0），剩下 **31 bit 存有號整數**，範圍是 −2³⁰ 到 2³⁰−1。親眼驗證這個邊界：

```
$ d8 --allow-natives-syntax -e '%DebugPrint(1073741823); %DebugPrint(1073741824);'
DebugPrint: Smi: 0x3fffffff (1073741823)

DebugPrint: 0x2eb40101e2d5: [HeapNumber] in OldSpace
 - map: 0x2eb400000515 <Map[12](HEAP_NUMBER_TYPE)>
 - value: 1073741824.0
```

`1073741823 = 2³⁰−1` 還是 Smi（`0x3fffffff`）；**再加 1 變 `1073741824 = 2³⁰`，就裝不下 31-bit 有號範圍，V8 只好把它裝箱成一個 HeapNumber**（堆上的浮點數物件）。這個「31-bit 邊界」不是背的——是 `2^30-1` 這條數學線，你剛剛親眼看它跨過去。

> **SMI 的實際位元佈局（x64 + pointer compression）**：32-bit tagged value 裡，SMI 是 `value << 1`（最低位補 0 當 tag），所以 31-bit payload 存在高位。V8 印給你的 `0x2a (42)` 是解碼後的值，不是原始 bit pattern。之後你在 gef 看記憶體，一個 SMI 42 在 elements 陣列裡的原始 32-bit 會是 `0x54`（42<<1），別被嚇到。負數 `-1` 也一樣是 Smi：

```
$ d8 --allow-natives-syntax -e '%DebugPrint(-1);'
DebugPrint: Smi: 0xffffffff (-1)
```

### HeapObject：堆上的物件

不是 SMI 的東西——物件、陣列、字串、浮點數、函式——都是 **HeapObject**，活在 V8 的堆上，用一個「最低位 = 1」的 tagged 指標引用。看一個最普通的空物件：

```
$ d8 --allow-natives-syntax -e '%DebugPrint({});'
DebugPrint: 0x2eb40104b155: [JS_OBJECT_TYPE]
 - map: 0x2eb401005741 <Map[28](HOLEY_ELEMENTS)> [FastProperties]
 - prototype: 0x2eb401005b95 <Object map = 0x2eb401004f2d>
 - elements: 0x2eb4000007e5 <FixedArray[0]> [HOLEY_ELEMENTS]
 - properties: 0x2eb4000007e5 <FixedArray[0]>
 - All own properties (excluding elements): {}
```

第一行 `0x2eb40104b155` 是這個物件的（tagged）位址。**注意結尾 `5` = `0101`，最低位是 1**——這就是 HeapObject 的 tag。真正的堆位址是 `0x2eb40104b154`（減掉那個 1）。V8 幾乎所有印出來的物件位址結尾都是奇數 nibble（`1`/`5`/`9`/`d`），你之後看多了會有直覺：**位址結尾是偶數 → 可能是 raw pointer 或 SMI；結尾是奇數 → tagged HeapObject**。

每個 HeapObject 的**第一個欄位永遠是 map 指標**（`- map:` 那行），map 決定「這個物件是什麼形狀、怎麼解讀後面的欄位」。map 本身也是個 HeapObject。Map 是利用時最關鍵的欄位，[Ch 5](./05-map-hidden-class.md) 專章拆它，這裡先記住「HeapObject[0] = map」這個鐵律。

### HeapNumber：裝不下 SMI 的數字

浮點數、或超出 SMI 範圍的整數，會被裝箱成 **HeapNumber**——一個堆上的物件，裡面存 8-byte IEEE 754 double：

```
$ d8 --allow-natives-syntax -e '%DebugPrint(1.5);'
DebugPrint: 0x2eb40101e2c9: [HeapNumber] in OldSpace
 - map: 0x2eb400000515 <Map[12](HEAP_NUMBER_TYPE)>
 - value: 1.5
```

`Map[12]` 是說這個 map 的 instance size 是 12 byte（4-byte map 指標 + 8-byte double）。HeapNumber 有 map、要配置、要 GC——這就是為什麼 SMI 快：能用 SMI 就不要用 HeapNumber。之後 Part 3 你會發現，**PACKED_DOUBLE_ELEMENTS 陣列裡的浮點數是「攤平」直接存的原始 8-byte，不是 HeapNumber 指標**（[Ch 7](./07-jsarray-elements-kind.md)），這個「double 陣列能直接攜帶任意 64-bit 位元」的性質，是 `addrof` 原語的載體。

### Oddball：undefined / null / true / false

`undefined`、`null`、`true`、`false` 這幾個「單例值」在 V8 裡是一種特殊 HeapObject，叫 **Oddball**：

```
$ d8 --allow-natives-syntax -e '%DebugPrint(true); %DebugPrint(null); %DebugPrint(undefined);'
DebugPrint: 0x2eb400000071: [Oddball] in ReadOnlySpace: #true
...
DebugPrint: 0x2eb40000002d: [Oddball] in ReadOnlySpace: #null
...
DebugPrint: 0x2eb400000011: [Oddball] in ReadOnlySpace: #undefined
```

三個關鍵觀察：

- 它們都在 **ReadOnlySpace**——V8 的唯讀堆區，整個 isolate 共用這幾個單例，全世界只有一個 `undefined` 物件。
- 它們的位址是**固定的低位址**：`undefined = 0x...0011`、`null = 0x...002d`、`true = 0x...0071`。這個 offset（相對 isolate root）在同一個 build 裡是穩定的。你在別的 `%DebugPrint` 輸出裡到處看到 `0x...0011 <undefined>`、`0x...002d <null>`，現在知道那是什麼了。
- `null` 和 `undefined` 標了 `undetectable`（[Ch 5](./05-map-hidden-class.md) 的 map 旗標），這是 `typeof null === "object"`、`document.all` 那些 JS 怪癖的底層來源。

這幾個固定 offset 在利用時很有用：當你 leak 到一個位址、想確認 isolate root 在哪，`undefined` 的固定 offset 是個好錨點。

## 三種值放在一起對比

| 值 | V8 表示 | 在堆上？ | 有 map？ | tag（最低位） | 備註 |
|---|---|---|---|---|---|
| `42` | **SMI** | 否 | 否 | 0 | 31-bit 有號，零配置 |
| `2**30` | **HeapNumber** | 是 | 是 | 1（指標） | 超出 SMI 範圍，裝箱 |
| `1.5` | **HeapNumber** | 是 | 是 | 1（指標） | 8-byte IEEE754 |
| `{}` | **JSObject** | 是 | 是 | 1（指標） | HeapObject[0]=map |
| `true`/`null`/`undefined` | **Oddball** | 是（ReadOnly） | 是 | 1（指標） | 固定 offset 單例 |
| `"hi"` | **String** | 是 | 是 | 1（指標） | 見下方 |

字串也是 HeapObject，但 V8 對字串有一整套次型別（internalized、cons、sliced、external……）：

```
$ d8 --allow-natives-syntax -e '%DebugPrint("hi");'
DebugPrint: 0x2eb40101e25d: [String] in OldSpace: #hi
 ... type: INTERNALIZED_ONE_BYTE_STRING_TYPE
```

`hi` 是 one-byte（Latin-1）且被 internalized（去重快取）。字串表示是另一個大題目，本課主線用不到太深，知道「字串也是 tagged HeapObject」即可。

## 動手驗證：tag 就是位址最低那一位

跑一組混合值，把「SMI 沒位址、HeapObject 位址結尾奇數」這件事看清楚：

```
$ d8 --allow-natives-syntax -e '%DebugPrint(7); %DebugPrint({}); %DebugPrint([1,2,3]);'
DebugPrint: Smi: 0x7 (7)               ← SMI，無位址
DebugPrint: 0x....b155: [JS_OBJECT_TYPE]   ← 結尾 5 = 奇數 = tagged
DebugPrint: 0x....b1e9: [JSArray]          ← 結尾 9 = 奇數 = tagged
```

把每個 HeapObject 位址的最後一個十六進位數字寫出來，全是奇數（`1`/`5`/`9`/`d`），因為最低 bit 恆為 1。這不是巧合，是 tag。

## 進階：再往深一層

- **為什麼 SMI tag 用 1 bit、指標 tag 也是 1 bit？** 在 pointer-compression 的 V8，tagged value 是 32-bit，只用最低 1 bit 分 SMI（0）/ 強指標（1）。**弱指標（weak reference）**另外借用 bit 1（值 `0b10` 那類），你在 map 裡看到的 `[weak]` 標記（如 `prototype_validity_cell: ... [weak]`）就是弱 tag。完整的 tag 定義在 V8 原始碼 `src/common/globals.h` 的 `kSmiTag`、`kHeapObjectTag`、`kWeakHeapObjectTag`。
- **SMI 的「32-bit vs 64-bit」之爭**：**沒開** pointer compression 的 64-bit V8，tagged value 是完整 64-bit，SMI 佔 **32 bit**（低 32 bit 全 0 當 tag 空間，值在高 32 bit）——所以有些老 writeup 說「SMI 是 32-bit 整數」是指那種 build。你的 build 開了 compression，SMI 只有 31-bit。**看 writeup 先看它開沒開 compression**，SMI 範圍會差一倍。
- **`%DebugPrintPtr`**：想直接看某個 raw pointer 被 V8 怎麼解讀，有 `%DebugPrintPtr(addr)`。實戰 debug 一個 fake object 時常用它確認「V8 到底把我這個 word 當成什麼」。
- **tagging 對 JIT 的成本**：每次算術要先確認是不是 SMI、要 untag（右移）、算完再 tag。TurboFan 的優化很大一部分就是「消除多餘的 tag/untag」和「賭這個值恆為 SMI 所以省掉 check」——這正是 Part 4 型別混淆的溫床（[Ch 2](./02-v8-architecture.md) 講過的「賭食材」）。

## 踩雷集錦

1. **錯誤直覺：「V8 的整數就是 C 的 int64」。正確：** 小整數是 31-bit（compression build）的 SMI、內嵌在 tagged word 裡；一超界就變堆上的 HeapNumber。心裡要有「SMI ↔ HeapNumber 的邊界在 ±2³⁰」這條線。
2. **錯誤直覺：「`%DebugPrint` 印的位址就是真正的記憶體位址」。正確：** 那是 **tagged** 位址（HeapObject 恆 +1）。要在 gef `x/gx` 看它，得先減掉 tag（減 1）。而且它還是**壓縮過的**表示（[Ch 4](./04-pointer-compression.md)），不是傳統 64-bit 虛擬位址。
3. **錯誤直覺：「浮點數陣列裡存的是 HeapNumber 指標」。正確：** `PACKED_DOUBLE_ELEMENTS` 陣列把 double **攤平**成原始 8-byte 直接存，不裝箱。這個差異是 `addrof` 原語的關鍵（[Ch 7](./07-jsarray-elements-kind.md)、[Ch 15](./15-addrof-fakeobj.md)）。
4. **錯誤直覺：「`null` 和 `undefined` 是特殊的空指標」。正確：** 它們是 ReadOnlySpace 裡真實存在的 Oddball 物件，有 map、有固定 offset，不是 C 的 `NULL`。
5. **錯誤直覺：「tag 只是效能細節，跟利用無關」。正確：** tag 是 `addrof`/`fakeobj` 的整個機制核心——那兩個原語就是在「同一個 word 被當 SMI 還是當指標」之間切換。不懂 tag 就不懂 Part 3。

## 動手練習

1. 跑 `%DebugPrint` 掃過這些值：`0`、`-1073741824`（−2³⁰）、`-1073741825`（−2³⁰−1）、`0.5`、`NaN`、`Infinity`。找出負數方向 SMI 的邊界在哪，哪些變成了 HeapNumber。把邊界值抄下來，和正數方向的 `2³⁰−1` 對照。
2. 對三個不同的 HeapObject（一個 `{}`、一個 `[]`、一個字串）`%DebugPrint`，把它們位址的最後一個十六進位數字都記下來，確認全是奇數，並手算出各自減掉 tag 後的真實堆位址。
3. 跑 `%DebugPrint(undefined)`、`%DebugPrint(null)`、`%DebugPrint(true)`、`%DebugPrint(false)`，把四個 Oddball 的固定位址記成一張小表。之後你在別的輸出看到 `<undefined>`/`<null>` 時，回來對照確認位址一致。

## 本章重點整理

- V8 用**一個 machine word** 表示任何 JS 值，靠**最低位元的 tag** 區分：`0` = SMI（值內嵌），`1` = HeapObject 指標。
- **SMI** 在你的 compression build 是 **31-bit 有號整數**，範圍 ±2³⁰，零配置零 GC；超界就裝箱成 **HeapNumber**。
- **HeapObject** 的第一個欄位恆為 **map**；tagged 指標 = 真實位址 + 1，所以印出來位址結尾恆為奇數。
- **Oddball**（`undefined`/`null`/`true`/`false`）是 ReadOnlySpace 的固定-offset 單例物件，可當 leak 時的錨點。
- tagging 是 `addrof`/`fakeobj`（[Ch 15](./15-addrof-fakeobj.md)）的機制核心——同一個 word 在 SMI / 指標兩種身分間切換，就是那兩個原語的本質。

## 自我檢核

- [ ] 能說出「一個 tagged word 最低位是 0 vs 1」各代表什麼
- [ ] 能講出你的 build 裡 SMI 的位元寬度與數值範圍，並解釋為什麼是 31-bit 而不是 32
- [ ] 看到一個 `%DebugPrint` 位址，能立刻判斷它是 SMI 還是 HeapObject、算出去掉 tag 的真實位址
- [ ] 能解釋 HeapNumber 和「double 攤平存進 elements」的差別，以及為什麼後者對 exploit 重要
- [ ] （面試題）「V8 為什麼要 pointer tagging？SMI 的範圍是多少、為什麼？」你能不看筆記答出來
- [ ] （面試題）「`addrof` 原語為什麼可行？和 tagging 什麼關係？」你能講出「同一 word 兩種解讀」這條線

## 延伸閱讀

- **[“Pointer Compression in V8” — v8.dev/blog/pointer-compression](https://v8.dev/blog/pointer-compression)**
  - **這篇說什麼**：V8 官方講 tagged value 的位元佈局、SMI vs HeapObject tag、以及壓縮怎麼影響它們。
  - **讀哪裡**：前半「Tagged values and pointers」那段就是本章的官方版；後半是 [Ch 4](./04-pointer-compression.md) 的內容。
  - **關聯**：本章的 tag 規則、SMI 31-bit 的原因，都在這篇有第一手說明。
- **[“JavaScript engine fundamentals: Shapes and Inline Caches” — Mathias Bynens / Benedikt Meurer](https://mathiasbynens.be/notes/shapes-ics)**
  - **這篇說什麼**：從「引擎怎麼表示物件與值」講起，SMI/HeapObject 的動機解釋得很白話。
  - **關聯**：本章講 tagging，這篇補「為什麼引擎要這樣設計」的宏觀動機，接著就通往 [Ch 5](./05-map-hidden-class.md) 的 shape/map。
- **[V8 原始碼 `src/objects/smi.h`、`src/common/globals.h`（`kSmiTag` 等）](https://source.chromium.org/chromium/chromium/src/+/main:v8/src/objects/smi.h)**
  - **讀哪裡**：`Smi::FromInt` / `Smi::value` 幾個函式，和 `globals.h` 裡 `kSmiTagSize`、`kHeapObjectTag`、`kWeakHeapObjectTag` 的定義。
  - **關聯**：本章講的 tag 常數，這裡是它們的權威來源。想確認「你這個 build 的 SMI 到底幾 bit」，看 `kSmiShiftSize`。

值的表示搞定了，但你可能注意到一件怪事：這些位址都是 `0x2eb4...` 開頭、看起來不像 64-bit 虛擬位址那麼「長」。那是因為它們是**壓縮過的**——下一章拆開 pointer compression，看 V8 怎麼把 64-bit 指標塞進 32-bit，以及這對利用意味著什麼。

→ [Ch 4 — Pointer Compression](./04-pointer-compression.md)
