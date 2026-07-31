# Ch 8 — ArrayBuffer / TypedArray / DataView 與 backing store

> **目標**：拆開 `ArrayBuffer`、`TypedArray`、`DataView` 三者的內部結構，盯死三個欄位——**`backing_store` / `data_ptr`（資料指向哪）、`byte_length`（多長）、`buffer`（誰的資料）**。這一章是全課 Part 3「任意讀寫」的**終極目標物件**：現代 V8 利用的收尾動作，幾乎都是「想辦法把一個 TypedArray 的 `data_ptr` 或 `byte_length` 改成我要的值」。搞懂這三個欄位在記憶體哪個位置、sandbox 怎麼保護它們，你才知道 Part 3、Part 5 到底在攻什麼。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，`~/v8build/v8/out/x64.release/`（**開 sandbox + pointer compression**）。d8：`~/v8build/v8/out/x64.release/d8`。本章所有 `%DebugPrint` 真跑。

## 為什麼需要這個？

前兩章的 JSArray（elements kind）是「V8 幫你管的陣列」——V8 決定怎麼存值、怎麼 GC、能不能越界。`ArrayBuffer` 系列不一樣：它是 **JS 用來直接操作原始 bytes 的介面**。`ArrayBuffer` 是一塊裸記憶體，`TypedArray`（`Uint8Array`、`Float64Array`…）和 `DataView` 是套在上面的「視窗」，讓你以特定型別讀寫那塊 bytes。

這對利用者是天堂也是靶心，原因是 `TypedArray` 的讀寫**極度直接**：`u8[i]` 幾乎就是「從 `data_ptr + i` 讀一個 byte」，中間沒有 elements kind 那套間接。所以：

> 如果你能把一個 `TypedArray` 的 `data_ptr` 改成任意位址、`byte_length` 改成很大——那個 TypedArray 就變成一把**任意位址讀寫的槍**：`u8[任意offset]` 直接讀寫那個位址。

這是幾乎每一條 V8 exploit 鏈的收尾原語（[Ch 18](./18-oob-to-arbitrary-rw.md)）。而 V8 Sandbox（[Ch 34](./34-v8-sandbox.md)）存在的**首要目的**，就是斬斷「改 data_ptr 就任意讀寫」這條路。所以這三個結構是攻防雙方的正面戰場，你必須先認清它們長什麼樣。

## 先建立直覺：資料與視窗分離

```
   ArrayBuffer（擁有那塊 bytes）              cage 外的原始記憶體
   ┌────────────────────────┐               ┌──────────────────────────┐
   │ map                    │  backing_store│  [ b0 b1 b2 ... b15 ]     │
   │ backing_store ─────────┼──────────────►│  (16 bytes 裸記憶體)      │
   │ byte_length: 16        │               └──────────────────────────┘
   └────────────────────────┘                        ▲   ▲
        ▲                                             │   │
        │ buffer                                      │   │ 都指向同一塊
   ┌────┴───────────────────┐   ┌────────────────────┴───┴───────────┐
   │ TypedArray (Uint8Array)│   │ DataView                            │
   │ buffer ────────────────┘   │ buffer ─────────────────────────────┘
   │ data_ptr ─────────────────►│ byte_offset / byte_length          │
   │ byte_offset / length      │                                     │
   └────────────────────────┘   └─────────────────────────────────────┘
```

三個角色分工：

- **`ArrayBuffer`**：**擁有**一塊原始 bytes（`backing_store` 指向它，在 cage 外），記其長度（`byte_length`）。它本身不提供型別化讀寫。
- **`TypedArray`**：套在某個 `ArrayBuffer` 上的**型別視窗**，決定「以什麼型別、從哪個 offset、看多長」。`u8[i]` 就是透過它讀寫底層 bytes。
- **`DataView`**：另一種視窗，讓你手動指定 endianness 和型別逐次讀寫（`dv.getUint32(4, true)`）。

多個 view 可以看同一個 buffer。利用時要分清「我改的是 buffer 的 backing_store，還是 view 的 data_ptr」。

## 底層機制一：ArrayBuffer

```
$ d8 --allow-natives-syntax -e 'let ab=new ArrayBuffer(16); %DebugPrint(ab);'
DebugPrint: 0x2320104b261: [JSArrayBuffer]
 - backing_store: 0x23400004000
 - byte_length: 16
 - max_byte_length: 16
 - detach key: (undefined)
 - views: (no views)
 - detachable
 ...
 - type: JS_ARRAY_BUFFER_TYPE
 - instance size: 52
```

盯這幾欄：

- **`backing_store: 0x23400004000`** ——指向真正那 16 bytes 的指標。**注意它的高位 `0x234...` 和物件本體 `0x232...`（cage base）完全不同**：backing store 在 **cage 外**、是**完整 64-bit raw pointer**（[Ch 4](./04-pointer-compression.md) 的分界線）。這就是為什麼「堆內壓縮 OOB」摸不到它、需要破 sandbox。
- **`byte_length: 16`** ——buffer 有多長。改大它 = 越界的地盤變大。
- **`detachable` / `detach key`** ——buffer 可以被 detach（`transfer` 或 `postMessage` 後），detach 後 backing_store 變 null。**detach 相關的 UAF / confusion 是一整類漏洞**（優化器持有已 detach 的 buffer 指標）。
- **`views`** ——記錄有哪些 view 掛在這個 buffer 上（GC / detach 要通知它們）。

## 底層機制二：TypedArray（over ArrayBuffer）

在同一個 buffer 上套一個 `Uint8Array`：

```
$ d8 --allow-natives-syntax -e 'let ab=new ArrayBuffer(16); let u8=new Uint8Array(ab); %DebugPrint(u8);'
DebugPrint: 0x2320104b2ad: [JSTypedArray]
 - map: 0x02320100d41d <Map[60](UINT8ELEMENTS)> [FastProperties]
 - buffer: 0x02320104b261 <ArrayBuffer map = 0x23201010ce1>   ← 指回那個 ab
 - byte_offset: 0
 - byte_length: 16
 - length: 16
 - data_ptr: 0x23400004000                                    ← = ab.backing_store + byte_offset
   - base_pointer: 0
   - external_pointer: 0x23400004000
 - type: JS_TYPED_ARRAY_TYPE
 - instance size: 60
```

拆給你看（這幾個欄位是 Part 3 收尾要改的目標）：

- **`buffer`**：指回它套的那個 ArrayBuffer（`0x..b261`，正是上面的 `ab`）。
- **`byte_offset: 0` / `length: 16`**：這個視窗從 buffer 的第 0 byte 起、看 16 個元素。
- **`data_ptr: 0x23400004000`**：**這是讀寫的實際起點**。`u8[i]` = 讀寫 `data_ptr + i`。它等於 `ab.backing_store + byte_offset`。**改掉 data_ptr = 讓 u8 指向任意位址**。
- **`base_pointer: 0` + `external_pointer: 0x23400004000`**：V8 把 data_ptr 拆成兩部分。當 backing store 在 **cage 外**（over 一個 ArrayBuffer 時），`base_pointer=0`、`external_pointer` 是完整外部位址，`data_ptr = base + external`。

`elements kind` 是 `UINT8ELEMENTS`——TypedArray 也有 elements kind，但它是「以什麼型別解讀 bytes」（Uint8/Float64…），和 JSArray 的 PACKED/HOLEY 那套是不同維度。

### on-heap vs off-heap backing store

新建一個**不 over 現有 buffer** 的小 TypedArray，V8 可能把 backing store 放 cage **內**：

```
$ d8 --allow-natives-syntax -e 'let f=new Float64Array(4); f[0]=1.5; %DebugPrint(f);'
 - buffer: 0x226d0104b119 <ArrayBuffer ...>      ← V8 幫它自動配了個 buffer
 - data_ptr: 0x226d0104b154
   - base_pointer: 0x104b14d                      ← 非 0！指向 cage 內
   - external_pointer: 0x226d00000007             ← cage_base + 小常數
```

這裡 `base_pointer: 0x104b14d`（一個 cage 內壓縮位址，指向 backing store）**非 0**，`external_pointer` 是 `cage_base + 7`——這 `+7` 是 sandbox 的 **external pointer table** 機制在起作用（見下）。`data_ptr` 由這兩者算出。**on-heap（cage 內）vs off-heap（cage 外）backing store 的區別，影響你的原語能不能只靠 cage 內能力就改到它**——這是 [Ch 34](./34-v8-sandbox.md) 的核心議題。

## 底層機制三：DataView

```
$ d8 --allow-natives-syntax -e 'let ab=new ArrayBuffer(16); let dv=new DataView(ab); %DebugPrint(dv);'
DebugPrint: 0x2320104b3b1: [JSDataView]
 - buffer =0x02320104b261 <ArrayBuffer ...>
 - byte_offset: 0
 - byte_length: 16
 - type: JS_DATA_VIEW_TYPE
 - instance size: 48
```

`DataView` 比 TypedArray 更「手動」：沒有固定的元素型別，你每次呼叫 `getUint32/setFloat64` 指定型別與 endianness。它一樣握著 `buffer`、`byte_offset`、`byte_length`。利用時 DataView 有時比 TypedArray 順手，因為它能任意型別、任意 offset 讀寫同一塊記憶體，做 leak 拼湊很方便。

## 三者對照

| 面向 | ArrayBuffer | TypedArray | DataView |
|---|---|---|---|
| 角色 | 擁有 raw bytes | 型別化視窗 | 手動型別視窗 |
| 關鍵欄位 | `backing_store`、`byte_length` | `data_ptr`、`byte_length`、`length`、`buffer` | `byte_offset`、`byte_length`、`buffer` |
| 讀寫方式 | 不直接讀寫 | `ta[i]`（固定型別） | `getT/setT`（指定型別+endian） |
| instance type | `JS_ARRAY_BUFFER_TYPE` | `JS_TYPED_ARRAY_TYPE` | `JS_DATA_VIEW_TYPE` |
| exploit 收尾角色 | 改 `backing_store` | **改 `data_ptr`/`byte_length` → 任意讀寫** | 改 `byte_offset`/backing → 任意讀寫 |

## 為什麼這是 Part 3 的終極目標

把全課後半的收尾邏輯講清楚：

假設你已經（透過 elements kind confusion、[Ch 15](./15-addrof-fakeobj.md)–[Ch 16](./16-fake-object-rw.md)）拿到一個「cage 內任意讀寫」原語。收尾就是：

1. 找到（或偽造）一個 `TypedArray` 物件。
2. 用你的原語**改它的 `data_ptr`（或 `byte_length`）**。
3. 現在 `ta[i]` 就是**任意位址的讀寫**——一把穩定、以 JS index 操作的槍（[Ch 18](./18-oob-to-arbitrary-rw.md)）。

這比「一直用 confusion 原語」穩定太多，所以是標準收尾。**但這裡就撞上 sandbox**：

- sandbox 開啟時，`data_ptr` 不是明碼存在 TypedArray 物件裡，而是經過 **external pointer table** 間接（那個 `external_pointer = cage_base + 小常數` 的 `+常數` 其實是 table 的 index/handle）。你「改 data_ptr 欄位」只能改到 handle，指標實體在 table 裡、table 在 cage 外——這就是 sandbox 斬斷經典路徑的機制。
- 於是現代利用的 Part 3 收尾其實是「**cage 內任意讀寫**」，要變成「**進程級任意讀寫 + RCE**」還得再破 sandbox（[Ch 34](./34-v8-sandbox.md)、Part 5）。

**這整條「confusion → cage 內 RW → TypedArray 收尾 → 撞 sandbox → 破 sandbox」的鏈，就是本課的主線劇情。** 你現在在 Part 1 認識這三個結構，是為了後面每一步都知道自己在動哪個欄位。

## 進階：再往深一層

- **detach 與 UAF**：`ArrayBuffer.prototype.transfer()` 或 `postMessage` 會 detach buffer，backing_store 置 null。若某段（優化過的）程式碼仍持有舊 data_ptr 而沒重新檢查，就是 use-after-free / OOB。detach 類 bug 是 TypedArray 攻擊面的常客。
- **resizable ArrayBuffer / growable**：新特性 `maxByteLength`（本章 dump 有 `max_byte_length`）允許 buffer 動態 resize，帶來「length 在優化路徑中途改變」的新 confusion 面（Part 4 會提）。
- **external pointer table 的 handle 編碼**：sandbox 下 `external_pointer` 是 `cage_base | (handle)`，handle 低位有 tag。想確切理解 `+7` 從哪來，看 `src/sandbox/external-pointer-table.h`——[Ch 34](./34-v8-sandbox.md) 會逐位元拆。
- **`%ArrayBufferDetach` 等 intrinsic**：debug build 有一票操作 buffer 的內部函式，做 PoC 時很省事。
- **原始碼**：`src/objects/js-array-buffer.h`（三者的欄位佈局——你要用的 offset 在此，綁死 commit）、`src/objects/backing-store.h`（backing store 的生命週期與 on/off-heap 判斷）。

## 踩雷集錦

1. **錯誤直覺：「TypedArray 的 backing store 在 V8 堆（cage）內、和物件本體偏移固定」。正確：** over ArrayBuffer 的 backing store 在 **cage 外、完整 64-bit raw pointer**（實測 `0x234...` vs cage `0x232...`）；small on-heap TA 才可能在 cage 內。兩種情況原語需求不同。
2. **錯誤直覺：「改 data_ptr 就能直接任意讀寫（現代 V8）」。正確：** sandbox 下 data_ptr 經 external pointer table 間接，你改的是 handle 不是實體指標。經典「改 data_ptr」路徑被斬斷，需另破 sandbox（[Ch 34](./34-v8-sandbox.md)）。
3. **錯誤直覺：「ArrayBuffer 和它的 view 是同一個物件」。正確：** ArrayBuffer 擁有 bytes，view（TypedArray/DataView）是套上去的獨立物件，透過 `buffer` 欄位關聯；一個 buffer 可有多個 view。
4. **錯誤直覺：「detach 只是把 length 設 0」。正確：** detach 使 backing_store 失效（null），是 UAF/OOB 的來源——關鍵在「還有沒有人握著舊指標」。
5. **錯誤直覺：「TypedArray 的 elements kind（UINT8ELEMENTS）和 JSArray 的 PACKED/HOLEY 是同一套」。正確：** 是不同維度——前者是「以什麼型別解讀 bytes」，後者是「陣列內部表示的緊湊度」。別混。

## 動手練習

1. 建一個 `ArrayBuffer(0x100)`，在上面套一個 `Uint8Array`、一個 `Float64Array`（用 `byte_offset` 錯開）、一個 `DataView`。`%DebugPrint` 全部，畫出「一個 buffer、三個 view」的關係圖，標出各自的 `data_ptr`/`byte_offset`/`byte_length`，確認 view 的 data_ptr = buffer.backing_store + byte_offset。
2. 對照本章：建一個 `new Float64Array(4)`（on-heap）和一個 over 大 `ArrayBuffer` 的 `Float64Array`（off-heap），`%DebugPrint` 比較兩者的 `base_pointer`（一個非 0、一個 0）和 backing store 位址在不在 cage 內。體會 on/off-heap 差異。
3. 思考題（先不做 exploit）：假設你有「cage 內任意 32-bit 相對寫」。你想把某個 TypedArray 變成任意讀寫槍。列出你會嘗試改哪個欄位、sandbox 會在哪一步擋你、你因此需要 [Ch 34](./34-v8-sandbox.md) 的什麼能力。把這條推理寫下來——這就是 Part 3→Part 5 的劇情大綱。

## 本章重點整理

- **ArrayBuffer** 擁有一塊 raw bytes（`backing_store`，在 **cage 外、64-bit raw pointer**）並記 `byte_length`；**TypedArray/DataView** 是套上去的視窗，透過 `buffer` 關聯、用 `data_ptr`+`byte_offset`+`byte_length/length` 決定看哪段。
- `ta[i]` ≈ 讀寫 `data_ptr + i`——**改 `data_ptr`/`byte_length` = 得到任意位址讀寫**，這是 Part 3 的標準收尾原語（[Ch 18](./18-oob-to-arbitrary-rw.md)）。
- backing store 有 **on-heap（cage 內）/ off-heap（cage 外）** 之分（`base_pointer` 是否為 0）；sandbox 下 data_ptr 經 **external pointer table** 間接（`external_pointer = cage_base + handle`），斬斷經典「改 data_ptr」路徑。
- 本課主線劇情：**confusion → cage 內任意讀寫 → TypedArray 收尾 → 撞 sandbox → 破 sandbox（Part 5）**。這三個結構是這條鏈的靶心。
- detach、resizable buffer 是額外的 confusion/UAF 攻擊面。

## 自我檢核

- [ ] 能畫出「一個 ArrayBuffer + 多個 view」的關係圖，說出各關鍵欄位
- [ ] 能解釋 `data_ptr` 怎麼由 `base_pointer` + `external_pointer` 算出、on/off-heap 差在哪
- [ ] 能說出為什麼「改 data_ptr → 任意讀寫」是收尾原語，以及 sandbox 如何擋它
- [ ] 知道 backing store 在 cage 外、是 64-bit raw pointer，並連到 [Ch 4](./04-pointer-compression.md) 的兩層牢籠
- [ ] 能複述本課主線劇情，並指出這三個結構在其中的位置
- [ ] （面試題）「TypedArray 為什麼是 V8 exploit 的黃金目標？V8 Sandbox 如何讓它變難？」能完整答出

## 延伸閱讀

- **[“The V8 Sandbox” — v8.dev/blog/sandbox](https://v8.dev/blog/sandbox)**
  - **這篇說什麼**：官方講 sandbox 如何用 external pointer table 保護 backing store / data_ptr、斬斷「改指標就任意讀寫」的經典路徑。
  - **讀哪裡**：external pointer table 與 backing store 段落。本章 dump 裡的 `external_pointer = cage_base + 常數` 在這篇有解。
  - **關聯**：本章的靶心 + [Ch 34](./34-v8-sandbox.md) 的地基。
- **[Mozilla / MDN “ArrayBuffer, TypedArray, DataView” 規格頁](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/ArrayBuffer)**
  - **這篇說什麼**：三者的 JS 語意、detach、byteOffset/byteLength、多 view 共享 buffer 的規則。
  - **讀哪裡**：detach（`transfer`）和多 view 部分。
  - **關聯**：理解利用要玩的 JS 層行為（detach UAF、view 錯位）的權威語意來源。
- **[Project Zero “Virtually Unlimited Memory: Escaping the Chrome Sandbox” / 各 TypedArray data_ptr 利用 writeup](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實案例示範「拿到原語後如何操縱 TypedArray 達成任意讀寫、以及 sandbox 帶來的額外步驟」。
  - **讀哪裡**：任意讀寫收尾與繞 sandbox 的段落。
  - **關聯**：把本章的靜態結構，接到真實 exploit 的收尾動作；通往 [Ch 18](./18-oob-to-arbitrary-rw.md) 與 [Ch 34](./34-v8-sandbox.md)。

Part 1 的物件模型到此拼齊了：值的表示（[Ch 3](./03-value-representation.md)）、壓縮（[Ch 4](./04-pointer-compression.md)）、Map（[Ch 5](./05-map-hidden-class.md)）、properties/elements（[Ch 6](./06-properties-elements.md)）、elements kind（[Ch 7](./07-jsarray-elements-kind.md)）、backing store（本章）。理論拼齊，該親手把一個物件從記憶體逐格拆開驗證了——下一個檔是練習 A，你會用 `%DebugPrint` + gef 把一個 JSArray 和一個物件解剖到每一格。

→ [練習 A — 用 %DebugPrint / gef 解剖 V8 物件模型](./practice-a-object-model-dissection.md)
