# Ch 14 — 第一個 OOB：JSArray 越界

> **目標**：拿到你這門課的第一個記憶體破壞原語——**一個能越界讀寫的 JSArray**。不假設你已經有 TurboFan bug（那是 Part 4 的事），而是用 CTF 最常見的「**challenge patch**」手法，人工在 d8 裡植入一個 OOB：改個 builtin、拿掉一個 bounds check、或加一個不檢查邊界的 getter。用這個植入的 OOB 建立一個貫穿 Part 3 的直覺——**越界一格，你摸到的是隔壁物件的 metadata（map、length、elements 指標），而這些 metadata 一旦能改，整個堆就對你敞開**。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，`~/v8build/v8/out/x64.release/d8`（sandbox on）。本章的 `%DebugPrint`、相鄰佈局觀察全部真跑；**完整 OOB exploit 需要套 challenge patch 重編 V8**，這部分標「理論預期」並給出 patch 位置與重編步驟，不捏造成功輸出。

## 為什麼需要這個？

Part 1 你把 V8 的物件模型拆到每一格：值怎麼 tag（[Ch 3](./03-value-representation.md)）、指標怎麼壓（[Ch 4](./04-pointer-compression.md)）、Map 是什麼（[Ch 5](./05-map-hidden-class.md)）、六種 elements kind（[Ch 7](./07-jsarray-elements-kind.md)）、TypedArray 的 backing store（[Ch 8](./08-arraybuffer-typedarray.md)）。那些是**地形**。這一章開始，你要在這片地形上打第一發子彈。

但這裡有個先有雞還是先有蛋的問題：**要打洞，得先有洞**。真實的 V8 bug（TurboFan 把 `CheckBounds` 消掉、typer 算錯 range）要到 Part 4 才生得出來，機制複雜。如果非得先懂 Part 4 才能練 Part 3 的原語，你會卡死在半空中。

CTF 圈的標準解法、也是本章的做法：**先給你一個「作弊」的 OOB**。出題者把一顆乾淨的 d8 打上一個小 patch，人工塞一個越界能力進去（這正是絕大多數 V8 CTF 題的形式）。你不用管這個 OOB 怎麼來的，只專注在「**有了 OOB，怎麼把它變成 addrof/fakeobj、再變成任意讀寫**」。等 Part 4 你學會生真 bug，換掉這個作弊入口即可——**中間那套原語建構完全一樣**。這就是為什麼把「地基原語」和「漏洞來源」拆開教。

所以本章的定位：**建立「OOB → 摸到相鄰 metadata」的骨髓直覺**，並交給你一個可重編、可重用的 challenge patch 當練習環境（練習 B 直接用）。

## 先建立直覺：越界一格，你在讀隔壁的門牌

想像 V8 的堆是一排緊鄰的公寓，每戶（每個堆物件）門口都掛著門牌（map 指標）、住戶名冊（length、elements 指標）。你手上的 JSArray 是其中一戶，正常情況下 `a[i]` 只讓你進**自己家**的房間（index 0..length-1）。

`bounds check`（邊界檢查）就是「進門前確認 `i < length`」的門禁。**OOB 就是門禁壞了**：你能 `a[length]`、`a[length+3]`、`a[-2]`——走出自己家，踏進隔壁戶。而隔壁戶門口那些**門牌和名冊**，正是你最想改的東西：

- 隔壁如果是另一個陣列，它的 **length 欄位**就在你越界範圍內——把它改成 `0x1000`，那個陣列瞬間「合法地」能讀寫一大片。
- 隔壁的 **map 指標**在你範圍內——改 map = 改型別，把 double 陣列的 map 換成 object 陣列的 map，就是 elements kind confusion（[Ch 15](./15-addrof-fakeobj.md) 的核心）。
- 隔壁如果是 TypedArray，它的 **data_ptr**（[Ch 8](./08-arraybuffer-typedarray.md)）在範圍內——改它 = 任意讀寫（[Ch 17](./17-typedarray-attack.md)）。

```
   你的 OOB array                 相鄰的受害者物件
   ┌────────────────┐            ┌──────────────────────────┐
   │ elements[0]    │            │ map        ← 改型別        │
   │ elements[1]    │  越界寫 ──► │ length     ← 改成超大      │
   │ elements[2]    │            │ elements ─► ...           │
   │ ...(length 到) │            │ data_ptr   ← 任意讀寫      │
   │ ↓↓↓ 出界 ↓↓↓   │            └──────────────────────────┘
   │ [length+0] ────┼────────────► 落在受害者的 metadata 上
   │ [length+1] ────┼────────────►
   └────────────────┘
```

**一句話**：OOB 的價值不在「多讀幾個 byte 的資料」，而在「**摸到相鄰物件的 metadata**」。資料是死的，metadata 是控制流——改 length、改 map、改 data_ptr，你就從「越界一格」升級成「掌控整個堆的型別與邊界」。這一章教你把這個直覺坐實。

## challenge patch：CTF 怎麼植入一個 OOB

真實 CTF 題目給你的是一顆「加料」的 d8。出題者的 patch 通常是下面三種手法之一。你要能看懂 patch diff（[Ch 27](./27-patch-diffing.md) 專講），因為**patch 就是題目說明書**——它精確告訴你「哪個 API 現在能越界」。

### 手法一：加一個不檢查邊界的 builtin / intrinsic

最常見。出題者在 `src/builtins/` 加一個新方法，或在既有方法裡拿掉 bounds check。典型的是給 Array 加一個 `oob` getter/method，直接讀寫 elements 而不比對 length：

```cpp
// src/builtins/builtins-array.cc （示意 patch）
// 新增一個 Array.prototype.oobRead(i) / oobWrite(i, v)，故意不檢查 i < length
BUILTIN(ArrayOobRead) {
  HandleScope scope(isolate);
  Handle<JSArray> array = Handle<JSArray>::cast(args.receiver());
  double idx = args.atOrUndefined(isolate, 1)->Number();
  Handle<FixedDoubleArray> elems(
      FixedDoubleArray::cast(array->elements()), isolate);
  // 故意的 bug：沒有 idx < array->length() 檢查
  double val = elems->get_scalar(static_cast<int>(idx));
  return *isolate->factory()->NewNumber(val);
}
```

這種 patch 給你的是「以 `double` 為單位、相對 elements 起點的越界讀寫」。**乾淨、可控、offset 好算**——本章與練習 B 用的就是這一類。

### 手法二：讓 `length` 與 `elements capacity` 脫鉤

出題者加一個 intrinsic 直接改 JSArray 的 `length` 欄位、但不動 elements backing store。回顧 [Ch 7](./07-jsarray-elements-kind.md) 的伏筆：**length（JS 可見長度）≠ elements capacity（實配容量）**。若能把 length 改大於 capacity，`a[大index]` 就合法地越界——V8 只比對 length，而 length 是假的。

### 手法三：patch 掉某個既有 bounds check（模擬真 bug）

出題者在 `elements.cc` 或 TurboFan 的 `CheckBounds` 相關程式碼裡直接 `#if 0` 掉一段檢查，模擬「一個真的 TurboFan bug 會造成的效果」。這種最貼近 Part 4 的真實漏洞，但也最難單獨觸發。

**三種手法的共同點**：最終都給你一個「以某個型別為單位、相對某個 backing store、能越界讀/寫」的原語。**Part 3 之後的所有內容都只需要這個抽象**——不管它怎麼來。所以本章你只要有一個能跑的 OOB，我們就開工。

## 底層機制：越界時你踩到的到底是什麼

先不管 patch 怎麼寫，用 `%DebugPrint` 看**相鄰佈局**，把「越界會讀到 metadata」這件事看成事實而非傳說。跑兩個相鄰陣列：

```
$ d8 --allow-natives-syntax -e '
  let flt = [1.1, 2.2, 3.3, 4.4];   // FixedDoubleArray
  let objs = [{}, {}];              // FixedArray of pointers
  %DebugPrint(flt);
  %DebugPrint(objs);'
```

真跑（節錄關鍵行，位址每次不同、看結構）：

```
DebugPrint: 0xd5e0104b209: [JSArray]
 - map: 0x0d5e0100cfc9 <Map[16](PACKED_DOUBLE_ELEMENTS)>
 - elements: 0x0d5e0104b1e1 <FixedDoubleArray[4]> [PACKED_DOUBLE_ELEMENTS]
 - length: 4
DebugPrint: 0xd5e0104b279: [JSArray]
 - map: 0x0d5e0100d051 <Map[16](PACKED_ELEMENTS)>
 - elements: 0x0d5e0104b231 <FixedArray[2]> [PACKED_ELEMENTS]
 - length: 2
```

把位址排成一條線（記住 [Ch 4](./04-pointer-compression.md)：這些是 cage 內壓縮值，低位在動）：

```
   0xd5e0104b1e1  flt 的 elements（FixedDoubleArray[4]）  ← 你越界的起點
   0xd5e0104b209  flt 的 JSArray 本體
   0xd5e0104b231  objs 的 elements（FixedArray[2]）
   0xd5e0104b279  objs 的 JSArray 本體
```

`flt.elements` 在 `0x..b1e1`，往後 0x50 bytes 就撞到 `objs.elements` 在 `0x..b231`。**如果 `flt` 有 OOB 讀寫，`flt[大index]` 就會落在 `objs` 的 elements、甚至 `objs` 的 JSArray header（map / length / elements 指標）上。** 這不是巧合——V8 的 young space 是線性配置（bump pointer），你**連續 new 出來的物件在記憶體裡就是連續的**。這給了利用者一個強力假設：**我 spray 出來的相鄰物件，佈局可預測**。

### FixedDoubleArray 的 header 長怎樣

越界時第一個踩到的，是相鄰 backing store 自己的 header。`FixedDoubleArray` 的佈局（pointer compression 下，每欄 4 bytes 壓縮值）：

```
   offset 0x0:  map          （4 bytes，壓縮）— 說「我是 FixedDoubleArray」
   offset 0x4:  length        （4 bytes，SMI）— backing store 的容量
   offset 0x8:  element[0]     （8 bytes，raw double）
   offset 0x10: element[1]
   ...
```

所以如果你的 OOB 是「以 double 為單位、從 element[0] 起算」，往負方向 `oob[-1]` 就是 `[length | map]` 這 8 bytes（兩個 4-byte 壓縮值併在一個 double 位置）。往正方向越過自己的容量，就進入下一個物件的 header。**這就是為什麼「越界一格 = 摸到 metadata」不是比喻，是位元組級的事實。**

## 用 challenge patch 真的打一發（理論預期）

> **未實測，理論預期（需套用 challenge patch 並重編 V8）**。本 batch 未提供 patch build，下面給出**可自行套用**的最小 patch、重編步驟、以及套上後的**預期**行為。可驗證的中間步驟（相鄰佈局、IEEE754 位元）已在上面/其他章真跑。

### 最小 challenge patch（手法一）

在 `~/v8build/v8/src/builtins/builtins-array.cc` 末尾附近（`namespace internal` 內）加兩個 builtin，並在 `src/init/bootstrapper.cc` 把它們掛到 `Array.prototype`。最省事的做法是掛在既有的 array proto 安裝處，加：

```cpp
// builtins-array.cc
BUILTIN(ArrayOob) {
  HandleScope scope(isolate);
  Handle<JSArray> a = Handle<JSArray>::cast(args.receiver());
  Handle<FixedDoubleArray> e(
      FixedDoubleArray::cast(a->elements()), isolate);
  double idx = args.atOrUndefined(isolate, 1)->Number();
  int i = static_cast<int>(idx);
  if (args.length() >= 3) {                     // oob(i, v) → 寫
    double v = args.atOrUndefined(isolate, 2)->Number();
    e->set(i, v);                               // 無 bounds check
    return ReadOnlyRoots(isolate).undefined_value();
  }
  return *isolate->factory()->NewNumber(e->get_scalar(i));  // oob(i) → 讀
}
```

掛載（`bootstrapper.cc`，在 array prototype 設定區塊）：

```cpp
SimpleInstallFunction(isolate_, proto, "oob",
                      Builtins::kArrayOob, 2, false);
```

### 重編（只重編、不重新 gclient）

```
cd ~/v8build/v8
autoninja -C out/x64.release d8
```

`autoninja` 只會重編動到的檔（builtins/bootstrapper），約數分鐘。sandbox-off build 同理換 `out/x64.release.nosbx`。

### 套上後預期怎麼用

```js
let flt = [1.1, 2.2, 3.3, 4.4];   // PACKED_DOUBLE，OOB 以 double 為單位
let objs = [{}, {}];              // 緊鄰在後

// 預期：flt.oob(i) 讀到超過 length=4 的 double；某個 i 會落在 objs 的 metadata
for (let i = 4; i < 20; i++) {
  print(i + ": " + ftoi(flt.oob(i)).toString(16));  // ftoi 見 Ch 15
}
```

**預期輸出的樣貌**（不是實測值，是結構預測）：`i=4..` 開始出現非資料的位元——你會看到某格的低 32 bit 像個壓縮 map 指標（`0x0..cfc9` 之類）、某格像個 SMI length（`0x2`＝ length 2 左移一位）。**認出那格就是你的著力點**：`flt.oob(那格, 0x1000...)` 寫進去，`objs` 的 length 或 map 就被你改了。[Ch 15](./15-addrof-fakeobj.md) 把這一步接成 addrof/fakeobj。

**踩雷提醒**：GC 可能在你 spray 和越界之間搬動物件，讓相鄰假設失效（[Ch 13](./13-garbage-collection.md)）。CTF 裡常見的穩定手法是：一次 new 一大批同型別物件（spray）、選中間的幾個當「洞主 + 受害者」、盡量不觸發 GC。

## 對比：OOB 的兩種型別視角

同一個越界能力，用不同 elements kind 的陣列去做，語意完全不同——這是 Part 3 全部把戲的來源：

| OOB 載體 | 越界讀到的是 | 越界寫進去變成 | 主要用途 |
|---|---|---|---|
| `PACKED_DOUBLE` 陣列 | 相鄰記憶體的**原始 8-byte 位元**（當 double 給你） | 你寫的 64-bit 位元原封不動落地 | **洩漏指標**（把 metadata 當 double 讀出）、寫假指標 |
| `PACKED_ELEMENTS` 陣列 | 相鄰記憶體的值**被當 tagged 指標解引用** | 你寫的值被當指標存 | **偽造物件**（把受控位址當物件用） |
| `TypedArray`（Uint8/Float64） | backing store 的 bytes（cage 外/內） | 直接寫 bytes | 最終任意讀寫載體（[Ch 17](./17-typedarray-attack.md)） |

記住這張表的第一、二列——**double 陣列讀出「位元」、object 陣列解引用「指標」**，這兩句話是 [Ch 15](./15-addrof-fakeobj.md) 的全部。OOB 只是讓你有機會把這兩種視角**套到同一塊記憶體上**。

## 踩雷集錦

1. **錯誤直覺：「OOB 的價值是多讀幾格資料」。正確**：資料無關緊要，OOB 的黃金在**相鄰物件的 metadata**（map / length / elements 指標 / data_ptr）。改 length 得到更大 OOB、改 map 得到型別混淆、改 data_ptr 得到任意讀寫——控制流全在 metadata。
2. **錯誤直覺：「越界的單位是 byte」。正確**：越界單位是**你越界載體的 element 型別**。用 double 陣列越界，`oob[i]` 每步跳 8 bytes；用 object 陣列越界，每步跳一個壓縮指標。算 offset 前先確定你的 stride。
3. **錯誤直覺：「相鄰物件的順序隨機、沒法預測」。正確**：young space 是 bump-pointer 線性配置，**連續 new 的同型別物件通常相鄰且順序可預測**。spray + 選中間，佈局可控。但 GC 一動就變（下一雷）。
4. **錯誤直覺：「拿到 OOB 就穩了」。正確**：GC 會搬物件（[Ch 13](./13-garbage-collection.md)），你的相鄰假設可能在越界前被打散。exploit 要壓低 GC 觸發、或在 spray 後立刻越界、或用 old space 的較穩佈局。
5. **錯誤直覺：「challenge patch 的 OOB 和真 bug 不一樣，練了沒用」。正確**：patch 只是**替換了 OOB 的來源**。從 OOB 到 addrof/fakeobj 到任意讀寫的**整條原語鏈完全相同**——Part 4 換上真 TurboFan bug 時，Part 3 這套 template 一字不改照用。

## 進階：再往深一層

- **負向越界（underflow）**：很多題目的 OOB 允許負 index。`oob[-1]`、`oob[-2]` 讀到的是你**自己 backing store 的 header**（length、map），甚至前一個物件的尾巴。負向越界常比正向更好用，因為前面物件的佈局你更能掌控（你先 new 它）。
- **OOB 的 primitive 分級**：越界能力有強弱——「相對讀」＜「相對寫」＜「相對讀+寫」＜「越界範圍可由你控制的 length 決定」。你手上是哪一級，決定你要走 [Ch 15](./15-addrof-fakeobj.md) 的哪條路。相對讀寫兼備最舒服。
- **length 欄位的 SMI 編碼**：JSArray/FixedArray 的 length 是 SMI（[Ch 3](./03-value-representation.md)），32-bit 壓縮下 SMI 是「值左移 1 位」。你越界寫 length 時要寫**編碼後**的值：想要 length=0x1000，寫 `0x2000`（或視版本 tag 規則）。寫錯會得到一半大小或觸發 assert。
- **真 bug 的 OOB 形狀**：Part 4 的 TurboFan `CheckBounds` 消除（[Ch 20](./20-checkbounds-redundancy-elimination.md)）產生的 OOB，形狀通常是「在優化過的迴圈裡，某個 `a[i]` 的 i 超出真實 length 但優化器以為安全」。和 challenge patch 的差別只在**觸發方式**，落地效果一樣是相對讀寫。
- **原始碼**：`src/objects/fixed-array.h`（FixedArray/FixedDoubleArray 的 header 佈局與 offset）、`src/objects/js-array.h`（JSArray 的 length 欄位）、`src/builtins/builtins-array.cc`（你要 patch 的地方）。

## 動手練習

1. **看清相鄰佈局**：用本章的 `%DebugPrint` 兩陣列實驗，換不同型別的陣列（double 後接 object、object 後接 double、中間插一個大物件），把每次的 elements 位址、header 位址畫成記憶體條，標出「若 double 陣列有 OOB，第幾格會落在下一個物件的 map / length 上」。建立「哪格是 metadata」的手感。
2. **套 patch 真跑**：照本章的最小 patch 加 `Array.prototype.oob`，`autoninja` 重編。跑越界迴圈 `flt.oob(i)` for i=4..30，把讀到的位元用 `ftoi`（[Ch 15](./15-addrof-fakeobj.md)）印成 hex，**親眼找出哪一格是相鄰物件的壓縮 map、哪一格是 length**。這是練習 B 的暖身。
3. **改 length 放大 OOB**：承上，用 `flt.oob(那格, itof(編碼後的大length))` 把相鄰 object 陣列的 length 改成 0x1000，然後正常 `objs[500]` 讀讀看——你剛把一個「越界一格」升級成「一個合法越界 4096 格的陣列」。體會 metadata 改寫的威力。

## 本章重點整理

- Part 3 的入口是「**先有一個 OOB**」；真 bug 在 Part 4，本章用 CTF 標準的 **challenge patch** 人工植入一個乾淨可控的 OOB（加不檢查邊界的 builtin / 讓 length 脫鉤 / patch 掉 bounds check）。
- OOB 的價值**不在資料，在相鄰物件的 metadata**：map（改型別）、length（放大越界）、elements/data_ptr（任意讀寫）。越界一格 = 摸到門牌與名冊。
- young space 線性配置讓 **spray 出來的相鄰物件佈局可預測**；但 **GC 會搬動**，穩定性要靠壓低 GC、spray 後立刻越界。
- **double 陣列越界讀出「原始位元」、object 陣列越界「解引用指標」**——這兩種型別視角套在同一塊記憶體上，就是下一章 addrof/fakeobj 的全部。
- challenge patch 只替換 OOB 的**來源**；從 OOB 到任意讀寫的**原語鏈與 Part 4 真 bug 共用**，這套 template 一次學會、全課通用。

## 自我檢核

- [ ] 能說出 OOB 的真正價值為什麼是「相鄰 metadata」而非資料本身，並舉出改 map / length / data_ptr 各得到什麼
- [ ] 能列出 challenge patch 植入 OOB 的三種手法，並解釋它們最終都給你「相對讀寫」這個抽象
- [ ] 能用 `%DebugPrint` 看兩個相鄰陣列，指出「若前者有 OOB，第幾格落在後者的 header」
- [ ] 知道越界的 stride 是 element 型別、length 是 SMI 編碼、負向越界踩到自己的 header
- [ ] 理解 GC 為什麼威脅相鄰假設、CTF 怎麼壓低這個風險
- [ ] （面試題）「給你一個 double 陣列的 OOB 相對讀寫，你的下一步是什麼？為什麼第一個目標是相鄰物件的 metadata？」能完整答出

## 延伸閱讀

- **[saelo “Attacking JavaScript Engines” — Phrack 0x46/0x48](http://www.phrack.org/issues/70/3.html)**
  - **這篇說什麼**：奠基性地示範「一個陣列的 OOB / 型別混淆如何一步步變成 addrof/fakeobj」。雖以 JSC 為例，OOB → metadata → 原語的推理和 V8 完全相通。
  - **讀哪裡**：從 OOB 談到 metadata 改寫那幾節。
  - **關聯**：本章的「越界摸 metadata」直覺，這篇是完整的思想源頭；直通 [Ch 15](./15-addrof-fakeobj.md)。
- **[doar-e / Jeremy Fetiveau “Introduction to TurboFan”](https://doar-e.github.io/blog/2019/01/28/introduction-to-turbofan/)**
  - **這篇說什麼**：從 TurboFan 角度看 OOB 怎麼從一個 bounds-check 消除長出來——讓你知道 challenge patch 在模擬什麼真實效果。
  - **讀哪裡**：CheckBounds 與 elements kind 段落。
  - **關聯**：把本章的「人工 OOB」接到 Part 4 [Ch 20](./20-checkbounds-redundancy-elimination.md) 的「真 OOB」，理解兩者落地效果同構。
- **[V8 `src/objects/fixed-array.h` / `src/builtins/builtins-array.cc` 原始碼](https://source.chromium.org/chromium/chromium/src/+/main:v8/src/objects/fixed-array.h)**
  - **這篇說什麼**：FixedArray/FixedDoubleArray 的 header 佈局（map/length offset）、以及你要 patch 的 builtin 實作。
  - **讀哪裡**：`FixedArrayBase` 的 length offset、`FixedDoubleArray::get_scalar/set`。
  - **關聯**：本章 patch 與 header 佈局的權威來源，綁死 commit `ab2cad06`。

有了 OOB、也看清了它踩到的是相鄰 metadata，下一步是把這個能力鑄成兩把最重要的鑰匙——`addrof`（洩漏任意物件的位址）和 `fakeobj`（把任意位址當物件）。這是全課引用最多次的一章。

→ [Ch 15 — 建立 addrof / fakeobj](./15-addrof-fakeobj.md)
