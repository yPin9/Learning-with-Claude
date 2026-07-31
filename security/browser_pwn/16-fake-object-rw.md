# Ch 16 — 從 addrof/fakeobj 到任意讀寫

> **目標**：把 [Ch 15](./15-addrof-fakeobj.md) 的兩把鑰匙鑄成第一個**穩定的任意讀寫原語**。核心手法：在一個你能精準寫入位元的 double 陣列裡，**逐格排出一個「假的 JSArray + 假的 FixedDoubleArray」**，用 `fakeobj` 把它變成真物件，然後**控制那個假陣列的 `elements` 指標**——指到哪，你就能讀寫哪。這一章教你「fake object 的記憶體佈局怎麼擺」：哪一格放 map、哪一格放 elements 指標、哪一格放 length，一個 byte 都不能錯。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，`~/v8build/v8/out/x64.release/d8`（sandbox on）。**JSArray/FixedDoubleArray 的欄位佈局、instance size、map 值全部用 `%DebugPrint` 真跑取得**；**完整 fake object → R/W 需 [Ch 14](./14-first-oob.md) 的 challenge patch OOB 建 confusion**，該部分標「理論預期」並給重編步驟，不捏造成功輸出。

## 為什麼需要這個？

你有了 `addrof`（知道任意物件在哪）和 `fakeobj`（把任意位址當物件）。但這兩把鑰匙本身**還不是**任意讀寫——`fakeobj(addr)` 只給你一個「V8 願意當物件對待的殼」，你還沒法用它讀寫任意記憶體。

缺的一步是：**讓 fakeobj 造出來的那個物件，是一個「elements 指標可控」的陣列**。回想 [Ch 7](./07-jsarray-elements-kind.md)：一個 `PACKED_DOUBLE` 陣列 `a`，`a[i]` = 「從 `a` 的 elements 指標 + 0x8 + i*8 讀寫一個 double」。**如果我能控制那個 elements 指標**，讓它指向任意位址 `X`，那 `a[0]` 就是讀寫 `X`、`a[1]` 讀寫 `X+8`……——這就是任意讀寫。

於是計畫成形：

1. 在一個真的 double 陣列裡，用 JS 寫入位元，**手工排出一個假的 JSArray 的每個欄位**（map、properties、elements、length）。
2. 把假 JSArray 的 **elements 欄位設成我要讀寫的目標位址**。
3. 用 `fakeobj` 把「那塊排好的記憶體」變成真的 JSArray。
4. 讀寫這個假陣列的 `[0]`，就是讀寫目標位址。

這章就是把這四步的**佈局細節**釘死。fake object 的核心難點從來不是概念，而是「**每一格到底放什麼**」——差一格、tag 差一位，V8 立刻崩。

## 先建立直覺：偽造一張假身分證

`fakeobj(addr)` 好比你跟 V8 說「`addr` 這個地方住著一個人（物件）」。V8 信了，但它接下來會**照物件的規矩去讀 `addr` 的欄位**：先讀 offset 0 的 map（「你是什麼型別？」），再依 map 讀其他欄位。

所以你必須**在 `addr` 那裡預先擺好一張合法的身分證**：

```
   你控制的 double 陣列 controller（真的，elements 你能精準寫）
   ┌─────────────────────────────────────────────┐
   │ controller[0] = itof(假 JSArray 的 map)      │ ← offset 0：map「我是 double 陣列」
   │ controller[1] = itof(properties | elements)  │ ← properties(4B) + elements 指標(4B)
   │ controller[2] = itof(length | ...)           │ ← length 欄位
   └─────────────────────────────────────────────┘
              ▲
              │ fakeobj(controller 的 elements 位址 + tag)
              ▼
   fake_arr = 一個 V8 當真的 JSArray
   → fake_arr.elements 指標 = 你在 controller[1] 塞的值 = 目標位址 X
   → fake_arr[0] 讀寫 X ！
```

關鍵洞察：**你不是憑空造記憶體，你是把「一塊你能精準寫入的 double 陣列」重新解讀成一個 JSArray**。double 陣列給你「精準寫位元」的能力，fakeobj 給你「讓 V8 把它當物件」的能力，兩者一合，你就能偽造任意欄位的物件。

## 底層機制一：JSArray 與 FixedDoubleArray 的精確佈局

要排假物件，先得知道真物件每一格放什麼。用 `%DebugPrint` 把一個真 double 陣列拆開（真跑）：

```
$ d8 --allow-natives-syntax -e 'let a=[1.1,2.2]; %DebugPrint(a);'
DebugPrint: 0x6f10104b179: [JSArray]
 - map: 0x06f10100cfc9 <Map[16](PACKED_DOUBLE_ELEMENTS)>
 - prototype: 0x06f10100c935 <JSArray[0]>
 - elements: 0x06f10104b161 <FixedDoubleArray[2]> [PACKED_DOUBLE_ELEMENTS]
 - length: 2
 - instance size: 16
```

`instance size: 16`（bytes）+ pointer compression（每個 tagged 欄位 4 bytes）→ **JSArray 的欄位佈局**：

```
   JSArray（16 bytes，壓縮下每欄 4 bytes）
   offset 0x0:  map           （壓縮指標）  ← 「我是 PACKED_DOUBLE 陣列」
   offset 0x4:  properties     （壓縮指標）  ← 通常指空 FixedArray
   offset 0x8:  elements       （壓縮指標）  ← ★ 你要控制的：指向 backing store
   offset 0xC:  length         （SMI）       ← JS 可見長度（SMI 編碼）
```

再看 elements 指向的 `FixedDoubleArray` 本身的佈局：

```
   FixedDoubleArray
   offset 0x0:  map           （壓縮指標）  ← FixedDoubleArray 的 map
   offset 0x4:  length         （SMI）       ← backing store 容量
   offset 0x8:  element[0]      （raw 8-byte double）
   offset 0x10: element[1]
   ...
```

**這兩張佈局圖是本章的作業指導書。** 你排假物件，就是把這些欄位一格一格填進你能控制的記憶體。

### 兩種偽造策略

有兩種擺法，難度和穩定性不同：

**策略一：偽造一個假 JSArray，elements 指向真 FixedDoubleArray（推薦入門）**

你不偽造 FixedDoubleArray（那要連 map+length 都排對），而是：造一個假 JSArray，把它的 elements 指標指向**目標位址減去 0x8**（因為 element[0] 在 FixedDoubleArray 的 offset 0x8）。這樣 `fake_arr[0]` = 讀寫「(目標-8) + 0x8」= 目標。但這需要目標前面 8 bytes 剛好是合法的 map+length，否則 V8 讀 elements 的 header 會崩。

**策略二：把整個 fake FixedDoubleArray 也排在你的 controller 裡（更穩、更常用）**

在同一塊你控制的記憶體裡，同時排好「假 JSArray」和「它指向的假 FixedDoubleArray（含合法 map + 超大 length）」，然後**只改假 FixedDoubleArray 的 element 位置**——不，更聰明的做法是：假 JSArray 的 elements 指向**你自己的 controller 陣列的 elements**，並把 length 設超大。這就退化成「一個 elements 別名 + 超大 length」的 OOB。

實務上最乾淨的是**下一章的 TypedArray 手法**（[Ch 17](./17-typedarray-attack.md)）。本章的 fake array 是理解「fakeobj 怎麼落地成 R/W」的必經之路，也是某些題目（禁 TypedArray、或 TypedArray 被加固）的唯一路。

## 底層機制二：排一個 fake JSArray（步步為營）

用 `addrof` + double 陣列，把一個假 JSArray 排進 `controller` 陣列的 elements 裡。核心程式碼（配合 [Ch 15](./15-addrof-fakeobj.md) 的 template）：

```js
// 前置：addrof/fakeobj/ftoi/itof 已就緒（Ch 15）
// controller 是一個真的 double 陣列，我們在它的 elements 裡排 fake JSArray 的欄位
let controller = [
  itof(0n),   // [0] 之後放 fake array 的 map
  itof(0n),   // [1] properties | elements
  itof(0n),   // [2] length | ...
  1.1, 2.2, 3.3, 4.4,   // 額外空間，也可當 fake FixedDoubleArray 的元素
];

// 1) 洩漏一個真 double 陣列的 map（PACKED_DOUBLE_ELEMENTS 的 map）
let real = [1.1];
let real_addr = addrof(real);
// 用某種讀原語（此刻還沒有！所以這步通常靠 OOB 先讀，或用已知 confusion）
// 假設我們已洩漏出 double_map（壓縮 map 值）與 empty_properties（空 FixedArray 壓縮值）

// 2) 在 controller 的 elements 裡排 fake JSArray：
//    map=double_map, properties=empty, elements=目標X, length=SMI(0x1000)
controller[0] = itof((BigInt(empty_properties) << 32n) | BigInt(double_map));
//         ↑ 一格 double = 8 bytes = 兩個壓縮欄位：低 32 是 map、高 32 是 properties？
//    ★ 注意欄位順序與 8-byte 打包（見下方踩雷）
```

**這裡最容易翻車的是「兩個 4-byte 壓縮欄位如何打包進一個 8-byte double 格」**。pointer compression 下 JSArray 每欄 4 bytes，但你的 double 陣列每格 8 bytes——所以**你寫一個 double，同時蓋掉兩個相鄰的 4-byte 欄位**。x64 小端序下，一個 double `0xAAAAAAAA_BBBBBBBB`：低位址 4 bytes 是 `0xBBBBBBBB`、高位址 4 bytes 是 `0xAAAAAAAA`。對照 JSArray 佈局：

```
   controller 的一個 double 格（8 bytes，小端）
   ┌────────────────┬────────────────┐
   │ 低 4 bytes     │ 高 4 bytes     │
   │ = JSArray[+0]  │ = JSArray[+4]  │
   │ = map          │ = properties   │
   └────────────────┴────────────────┘
   要寫的 double = itof( (properties_u32 << 32) | map_u32 )
```

所以「map + properties」打包成一個 double：`itof((BigInt(properties) << 32n) | BigInt(map))`；「elements + length」打包成下一個 double：`itof((BigInt(length_smi) << 32n) | BigInt(elements))`。**打包順序錯 = map 和 properties 互換 = V8 讀到亂 map = 崩。** 這是 fake array 最常見的死因。

## 底層機制三：fakeobj 指過去，得到 R/W

排好之後，用 `fakeobj` 把「controller 的 elements 起始位址」變成一個 JSArray：

```js
// controller 的 elements 位址 = addrof(controller) 讀出的 elements 欄位
// （或直接 addrof 一個放在 controller[0] 位置的標記物件來定位）
let fake_arr_addr = /* controller 的 elements backing store 位址 + tag */;
let fake_arr = fakeobj(fake_arr_addr);

// 現在 fake_arr 是一個 JSArray，它的 elements 指標 = 你在 controller 裡塞的「目標 X」
// 於是：
function read64(addr) {
  // 把 fake_arr 的 elements 指向 addr-8（FixedDoubleArray element[0] 在 +8）
  controller[1] = itof((BigInt(SMI_1000) << 32n) | BigInt(addr - 8n));  // 更新 elements 欄位
  return ftoi(fake_arr[0]);   // 讀 addr
}
function write64(addr, val) {
  controller[1] = itof((BigInt(SMI_1000) << 32n) | BigInt(addr - 8n));
  fake_arr[0] = itof(val);    // 寫 addr
}
```

**這就是任意讀寫原語的雛形**：透過「改 controller 裡假陣列的 elements 欄位」重新瞄準，`fake_arr[0]` 就讀寫任意位址。每次 read64/write64 都重設 elements 指標，達成「任意位址」。

> **cage 內限制（誠實說明）**：sandbox on 時，你 fakeobj 的位址、你控制的 elements 指標，**都是 cage 內的壓縮位址（32-bit offset）**。所以這個 fake array 原語天生**只能讀寫 cage 內**（4 GB heap 內）——碰不到 cage 外的 backing store、libc、返回位址。要突破到 cage 外，需要 [Ch 34](./34-v8-sandbox.md)/[Ch 35](./35-bypassing-v8-sandbox.md) 的能力。**sandbox off** 的 build 則不同：欄位是完整 64-bit 指標，你能把 elements 指到進程任意位址，直接得到進程級任意讀寫（經典路線）。這個差異 [Ch 18](./18-oob-to-arbitrary-rw.md) 統整。

## 用 challenge patch 真的跑（理論預期）

> **未實測，理論預期（需 [Ch 14](./14-first-oob.md) 的 challenge patch OOB 先建 confusion 與 addrof/fakeobj）**。JSArray/FixedDoubleArray 佈局、instance size、map 值已在上面真跑取得。完整 fake array R/W 需 patch build，這裡給預期行為，不捏造輸出。

完整流程（接 [Ch 15](./15-addrof-fakeobj.md) 的 addrof/fakeobj）：

```js
// 1) addrof/fakeobj/ftoi/itof 就緒（Ch 15，靠 Ch 14 的 OOB confusion）
// 2) 用 OOB 或 confusion 洩漏 double_map、empty_properties（一次性常數）
// 3) 準備 controller double 陣列，排 fake JSArray 欄位
// 4) fakeobj 指向 controller.elements，得到 fake_arr
// 5) 實作 read64/write64（上面）
// 預期驗證：
//   let probe = {mark: 1.234};
//   let paddr = addrof(probe);
//   read64(paddr) 回傳 probe 的 map（低 32 bit 是壓縮 map），與 %DebugPrint 對照一致
//   write64(某個你 own 的物件欄位, 值) 後 %DebugPrint 看到欄位被改
```

**預期輸出樣貌**：`read64(addrof({}))` 回傳一個低 32 bit 像壓縮 map（`0x..cfc9` 之類）的值；`write64` 後對目標物件 `%DebugPrint`，該欄位變成你寫的值。位址每跑不同——驗證的是「讀出的 map 和 `%DebugPrint` 一致」這個結構關係，不是某個固定數字。

**重編**：套 [Ch 14](./14-first-oob.md) patch → `autoninja -C out/x64.release d8` → 跑完整 `.js`。

## 對比：fake array R/W vs 其他收尾原語

| 原語 | 怎麼做 | 讀寫範圍 | 穩定性 | 何時用 |
|---|---|---|---|---|
| **fake JSArray**（本章） | 排假 JSArray、控 elements 指標 | cage 內（sandbox on）/ 全進程（off） | 中：每次重設 elements、佈局易錯 | 禁 TypedArray、或當中繼 |
| **fake/破壞 TypedArray**（[Ch 17](./17-typedarray-attack.md)） | 控 data_ptr | cage 內（sandbox on）/ 全進程（off） | 高：一次設定、之後乾淨 | **現代主流收尾** |
| **直接 OOB**（[Ch 14](./14-first-oob.md)） | 相對讀寫 | 相鄰記憶體 | 依 bug | 起點，範圍有限 |

**為什麼還要學 fake array**：它是理解「fakeobj → 物件欄位控制」最透明的一課，且是 TypedArray 手法的前置（TypedArray 的 data_ptr 偽造用的是同一套「排欄位」功夫）。此外某些題目把 TypedArray 加固了（或 data_ptr 進 external pointer table），fake array 反而是活路。

## 踩雷集錦

1. **錯誤直覺：「一個 double 格對應一個 JSArray 欄位」。正確**：pointer compression 下 JSArray 每欄 **4 bytes**，你的 double 格是 **8 bytes**——**一個 double 同時蓋兩個欄位**。map+properties 打包成一格、elements+length 打包成下一格，且要照**小端序**（低 4 bytes = 低 offset 欄位）。這是 fake array 頭號死因。
2. **錯誤直覺：「elements 指標直接填目標位址」。正確**：FixedDoubleArray 的 element[0] 在 header **offset 0x8**（map+length 佔前 8 bytes）。所以要讀寫 `X`，elements 指標要填 `X - 8`。忘了減 8，你讀寫的位置全偏 8 bytes。
3. **錯誤直覺：「fakeobj 位址不用 tag」。正確**：fakeobj 吃的是 **tagged 位址**（HeapObject 最低位 =1）。你填 backing store 位址時要加上 tag（壓縮下通常 `| 1`）。tag 錯，V8 把它當 SMI 或算錯 map 位置。
4. **錯誤直覺：「length 隨便填」。正確**：假 JSArray 的 length 是 **SMI 編碼**（值左移 1 位）。想要 length=0x1000 就填 `0x2000` 打包進高 32 bit。填成非 SMI（最低位 1）或太小，越界範圍不對或 V8 assert。
5. **錯誤直覺：「fake array 建好就一勞永逸、範圍無限」。正確**：sandbox on 時它**只能 cage 內**（欄位是 32-bit 壓縮 offset），碰不到 cage 外的 libc/backing store/返回位址。要出 cage 得靠 [Ch 34](./34-v8-sandbox.md)。sandbox off 才是經典的「全進程任意讀寫」。

## 進階：再往深一層

- **穩定化：把 controller 換成 old space**：young space 的物件會被 GC 搬（[Ch 13](./13-garbage-collection.md)），搬走後你排好的 fake 佈局就失效。CTF 老手常先讓 controller 陣列晉升 old space（多次 GC 或 `%PretenureAllocationSite` 類手法），或全程避開 GC，讓佈局穩定。
- **一次 fakeobj、重複瞄準**：不要每次 read64 都重新 fakeobj（貴且易崩）。建好一個 fake_arr 後，**只改 controller 裡它的 elements 欄位**來重新瞄準，fake_arr 物件本身不動。這是把「一次性偽造」變成「可重用讀寫槍」的關鍵技巧。
- **map 的取得**：你需要一個合法的 `PACKED_DOUBLE_ELEMENTS` map 值當假陣列的 map。最省事是 addrof 一個真 double 陣列、再讀它 offset 0 的 map（用 OOB 或已有的部分讀能力）。這個 map 是常數，洩漏一次全程用。
- **和 TypedArray 手法的關係**：[Ch 17](./17-typedarray-attack.md) 偽造/破壞 TypedArray、控 data_ptr，本質是同一套「排欄位」，只是 TypedArray 的關鍵欄位是 data_ptr（cage 外指標，sandbox on 時經 external pointer table 間接——這也是為什麼 sandbox 下 TypedArray 手法要多繞一步，[Ch 34](./34-v8-sandbox.md)）。
- **原始碼**：`src/objects/js-array.h`（JSArray 欄位 offset）、`src/objects/fixed-array.h`（FixedDoubleArray header）、`src/objects/map.h`（map 欄位，若你要連 map 都偽造）。offset 綁死 commit `ab2cad06`。

## 動手練習

1. **畫佈局圖**：不看本章，畫出 JSArray（16 bytes）和 FixedDoubleArray 的欄位 offset 圖，標出「pointer compression 下每欄 4 bytes」。然後畫「一個 8-byte double 格如何同時覆蓋兩個 4-byte 欄位」，標小端序。這張圖是排 fake object 的作業指導書。
2. **手算打包**：給定 `map=0x0100cfc9`、`properties=0x000007e5`、`elements=0x0104b161`、`length=2`（SMI 編碼 `0x4`），手算「controller[0]」和「controller[1]」該寫哪兩個 double 位元（`itof` 的引數）。和本章的打包公式核對。這是 fake array 最易錯的一步，手算一次記牢。
3. **套 patch 做 fake array R/W**（需 [Ch 14](./14-first-oob.md) patch）：實作本章的 read64/write64，驗證 `read64(addrof(probe))` 讀出 probe 的 map、`write64` 能改一個你 own 物件的欄位。刻意把「減 8」或「打包順序」寫錯，觀察 V8 怎麼崩——體會每一格的精確性。這是練習 B 的進階部分。

## 本章重點整理

- fake object R/W = 「在一個能精準寫位元的 **double 陣列**裡排出一個**假 JSArray**，用 `fakeobj` 讓 V8 當真，並**控制假陣列的 elements 指標**」——指到哪讀寫哪。
- 精確佈局是關鍵：JSArray（16B）欄位 map@0 / properties@4 / **elements@8** / length@12；FixedDoubleArray 的 element[0] 在 **offset 0x8**（所以 elements 指標要填 `目標-8`）。
- pointer compression 下**一個 8-byte double 同時覆蓋兩個 4-byte 欄位**，要按小端序打包（低 4B=低 offset 欄位）；map/properties 一格、elements/length 一格。**打包順序錯是頭號死因。**
- 技巧：**一次 fakeobj、之後只改 controller 裡的 elements 欄位重新瞄準**，把偽造變成可重用讀寫槍；controller 最好在 old space 避免 GC 搬動。
- **範圍限制**：sandbox on 時欄位是 32-bit 壓縮 offset → **只能 cage 內讀寫**；sandbox off 才是全進程任意讀寫。出 cage 靠 [Ch 34](./34-v8-sandbox.md)。這是本章原語的天花板。

## 自我檢核

- [ ] 能默寫 JSArray 與 FixedDoubleArray 的欄位 offset，並解釋 elements 指標為何要填「目標-8」
- [ ] 能解釋「一個 8-byte double 覆蓋兩個 4-byte 壓縮欄位」以及正確的小端打包順序
- [ ] 能說出 fakeobj 位址要帶 tag、length 要 SMI 編碼，寫錯各會怎麼崩
- [ ] 能描述「一次 fakeobj、改 elements 欄位重新瞄準」為什麼比每次重新偽造好
- [ ] 能講清楚 sandbox on（cage 內）vs off（全進程）對 fake array 讀寫範圍的差別
- [ ] （面試題）「有了 addrof/fakeobj，如何用一個 fake JSArray 得到任意讀寫？elements 指標為什麼是關鍵、要注意哪些 offset/編碼陷阱？」能完整答出

## 延伸閱讀

- **[saelo “Attacking JavaScript Engines” — Phrack 0x46 (§ fake objects)](http://www.phrack.org/issues/70/3.html)**
  - **這篇說什麼**：從 addrof/fakeobj 建構 fake object、控制其內部指標達成任意讀寫的奠基教學。
  - **讀哪裡**：fake object 佈局與「控制 backing pointer」那節。
  - **關聯**：本章「排假 JSArray、控 elements 指標」的思想原型。
- **[faraz.faith “V8 exploitation” / “From addrof to arbitrary read/write” 類 writeup](https://faraz.faith/)**
  - **這篇說什麼**：以現代 V8（pointer compression 後）示範 fake array 的欄位打包、tag、offset 細節——正是本章最易錯的地方。
  - **讀哪裡**：fake array 記憶體佈局與 read64/write64 實作段落。
  - **關聯**：把本章的打包公式對照真實 exploit 程式碼，補足壓縮下的實作細節。
- **[V8 `src/objects/js-array.h` / `fixed-array.h` 原始碼](https://source.chromium.org/chromium/chromium/src/+/main:v8/src/objects/js-array.h)**
  - **這篇說什麼**：JSArray/FixedDoubleArray 的欄位 offset 權威定義——你排 fake object 每一格的依據。
  - **讀哪裡**：`JSArray` 的 `kElementsOffset`/`kLengthOffset`、`FixedDoubleArray` 的 header 常數。
  - **關聯**：本章佈局圖的來源，綁死 commit `ab2cad06`；不同 commit offset 可能變。

fake array 給了你 cage 內任意讀寫，但它每次要重設 elements、佈局脆弱。有沒有更乾淨、設定一次就穩定的原語？有——偽造或破壞一個 TypedArray 的 backing store 指標。這是現代 V8 利用的主流收尾，下一章專講。

→ [Ch 17 — TypedArray 攻擊法：劫持 backing store](./17-typedarray-attack.md)
