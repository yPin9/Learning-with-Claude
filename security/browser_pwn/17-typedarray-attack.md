# Ch 17 — TypedArray 攻擊法：劫持 backing store

> **目標**：掌握現代 V8 利用的**主流收尾原語**——偽造或破壞一個 `TypedArray`（或它底下的 `ArrayBuffer`）的 **`data_ptr` / `backing_store` 指標**，得到一把**設定一次就穩定**的任意讀寫槍。相較 [Ch 16](./16-fake-object-rw.md) 的 fake JSArray（每次要重設 elements、佈局脆弱），TypedArray 手法乾淨太多：`ta[i]` 幾乎就是「讀寫 `data_ptr + i`」，沒有 elements kind 那層間接。這一章講清楚為什麼它是現代主流，以及 **sandbox 如何把 `data_ptr` 藏進 external pointer table、讓這條路變難**。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`。sandbox on = `~/v8build/v8/out/x64.release/d8`；sandbox off = `~/v8build/v8/out/x64.release.nosbx/d8`。**TypedArray 的 `data_ptr`/`base_pointer`/`external_pointer` 欄位、on-heap vs off-heap 差異全部 `%DebugPrint` 真跑**；**完整劫持 → R/W 需 [Ch 14](./14-first-oob.md) 的 challenge patch**，該部分標「理論預期」，不捏造輸出。

## 為什麼需要這個？

[Ch 16](./16-fake-object-rw.md) 的 fake JSArray 能讀寫，但它有三個痛點：

1. **每次瞄準都要改 elements 欄位**——多一次寫、多一個出錯點。
2. **佈局脆弱**——打包順序、tag、offset 差一點就崩，GC 一搬就失效。
3. **語意間接**——JSArray 的讀寫要過 elements kind 檢查、hole 檢查等一層層邏輯。

TypedArray 把這三點全解決。回顧 [Ch 8](./08-arraybuffer-typedarray.md)：`TypedArray` 的讀寫**極度直接**——`u8[i]` ≈ 「從 `data_ptr + i` 讀一個 byte」，中間沒有 elements kind 的間接。所以：

> **如果你能把一個 TypedArray 的 `data_ptr` 改成任意位址 `X`、`byte_length` 改夠大——那個 TypedArray 就是一把指向 `X` 的任意讀寫槍，`ta[0..n]` 直接讀寫 `X..X+n`。而且改一次 data_ptr 就穩定，之後 `ta[i]` 想讀哪換個 `i` 即可，不用再碰 metadata。**

這就是為什麼**幾乎每一份現代 V8 exploit 的收尾都是「弄一個 data_ptr 可控的 TypedArray」**。它是本課主線劇情（[Ch 8](./08-arraybuffer-typedarray.md) 講的「confusion → cage 內 RW → TypedArray 收尾 → 撞 sandbox」）的收尾那一步。

但也正因為它太好用，**V8 Sandbox（[Ch 34](./34-v8-sandbox.md)）存在的首要目的就是斬斷這條路**——把 data_ptr 從「明碼指標」改成「經 external pointer table 間接的 handle」。這一章你要同時學會經典手法（sandbox off）和它撞上 sandbox 的樣子（sandbox on）。

## 先建立直覺：一把可以隨意瞄準的槍

fake JSArray 像一把每次射擊前要重新裝彈、對準的老槍。data_ptr 可控的 TypedArray 像一把**已上膛、瞄準線可自由平移**的狙擊槍：

```
   一個 TypedArray（Uint8Array / Float64Array）
   ┌──────────────────────────────┐
   │ map    (是 Uint8/Float64...)  │
   │ buffer (指回 ArrayBuffer)     │
   │ byte_length: 巨大 ◄───────────┼── 你改大它 → 射程無限
   │ data_ptr: X    ◄──────────────┼── 你改成任意位址 → 瞄準線落在 X
   └──────────────────────────────┘
        │
        ▼
   ta[i]  ==  讀寫  X + i     （i 隨你換，不用再碰 metadata）
```

兩個要改的欄位：

- **`data_ptr`**（讀寫起點）：改成你要攻擊的位址。
- **`byte_length` / `length`**（射程）：改夠大，讓 `ta[大 i]` 都合法。

改完之後，這把槍**持久有效**：`read64(addr)` = 「設 data_ptr=addr、讀 ta[0]」或更省事「data_ptr 設成一個基準、用 ta 的相對 index 讀」。比 fake array 少一層間接、少一次佈局風險。

## 底層機制一：data_ptr 到底怎麼組成（真跑）

要劫持 data_ptr，先看它的實際結構。over 一個 ArrayBuffer 的 Float64Array（真跑，sandbox on）：

```
$ d8 --allow-natives-syntax -e '
  let ab=new ArrayBuffer(0x100); let ta=new Float64Array(ab); %DebugPrint(ta);'
DebugPrint: 0x385e0104b225: [JSTypedArray]
 - map: 0x385e01007a85 <Map[60](FLOAT64ELEMENTS)>
 - buffer: 0x385e0104b1d9 <ArrayBuffer map = 0x385e01010ce1>
 - byte_offset: 0
 - byte_length: 256
 - length: 32
 - data_ptr: 0x386000004000
   - base_pointer: 0
   - external_pointer: 0x386000004000
```

盯這三行（[Ch 8](./08-arraybuffer-typedarray.md) 講過，這裡是劫持視角）：

- **`data_ptr: 0x386000004000`** —— 實際讀寫起點。注意高位 `0x3860...` 和物件本體 `0x385e...`（cage base）**不同**——backing store 在 **cage 外、是完整 64-bit raw pointer**。這是 over-ArrayBuffer（off-heap）的情況。
- **`base_pointer: 0`** —— off-heap 時為 0。
- **`external_pointer: 0x386000004000`** —— off-heap 時等於完整外部位址，`data_ptr = base_pointer + external_pointer`。

再看一個 on-heap 的小 TypedArray（真跑）：

```
$ d8 --allow-natives-syntax -e 'let small=new Float64Array(4); %DebugPrint(small);'
 - data_ptr: 0x385e0104b2b4
   - base_pointer: 0x104b2ad          ← 非 0！cage 內壓縮位址
   - external_pointer: 0x385e00000007  ← cage_base + 小常數（handle！）
```

**這兩個 dump 的對比是本章的關鍵**：

- **off-heap**（over ArrayBuffer）：`base_pointer=0`、`external_pointer` = 完整 cage 外指標。data_ptr 直接就是那個外部位址。
- **on-heap**（小 TA，V8 自配 buffer 在 cage 內）：`base_pointer` 非 0（cage 內壓縮位址）、`external_pointer = cage_base + 7`——**那個 `+7` 不是位址，是 external pointer table 的 handle**（sandbox 機制）。

## 底層機制二：sandbox on/off 決定 data_ptr 怎麼被保護

這是全章最重要的分野，直接決定你的劫持手法：

### sandbox OFF（經典路線，`out/x64.release.nosbx`）

`data_ptr` 是**明碼存在 TypedArray 物件裡的完整 64-bit 指標**。劫持超直接：

1. addrof 你控制的一個 TypedArray、或 fakeobj 造一個。
2. 用你的 cage 內讀寫原語（[Ch 16](./16-fake-object-rw.md)），**直接把 data_ptr 欄位改成任意進程位址 X**。
3. `ta[i]` 立刻讀寫 X——**進程級任意讀寫**，能碰 libc、堆疊、GOT、任何東西。

這是 2018 年前所有 writeup 的收尾，也是 sandbox off build 的收尾。乾淨、無敵。

### sandbox ON（現代 default，`out/x64.release`）

`data_ptr` **不是明碼**。sandbox 下 external pointer（包括 backing store 指標）存在一個 **external pointer table** 裡，物件欄位存的是 **handle（table 的 index，帶 tag）**，不是指標本體。上面 dump 的 `external_pointer = cage_base + 7` 的 `+7` 就是 handle。

於是你用 cage 內讀寫原語去改 TypedArray 的「data_ptr 欄位」時：

- 你只能改到 **handle**（一個 index），**改不到指標實體**——指標實體在 table 裡、table 在 cage 外、你的 cage 內原語摸不到。
- 你能做的是「把 handle 改成指向 table 裡別的 entry」——但 table 裡的 entry 都是 V8 合法配過的 backing store 指標，你頂多換到另一個合法 buffer，**沒法指向任意進程位址**。

**結論**：sandbox on 時，「改 data_ptr → 任意讀寫」這條經典路徑**被斬斷**。你的 fake array/TypedArray 原語**天生只能在 cage 內（4 GB heap）讀寫**。要得到 cage 外（libc/RCE）能力，得另外破 sandbox（[Ch 34](./34-v8-sandbox.md)/[Ch 35](./35-bypassing-v8-sandbox.md)）——那是另一整套功夫（攻擊 external pointer table 本身、或找 cage 內就能達成 code exec 的路，如 [Ch 33](./33-wasm-rwx-jit-spray.md) 的 WASM）。

```
   sandbox OFF                          sandbox ON
   ta.data_ptr = 0x7fff_libc_addr       ta.external_ptr 欄位 = handle (index)
        │ 直接改成任意位址                    │ 只能改 index
        ▼                                    ▼
   ta[i] 讀寫進程任意位址               external pointer table[index] = 合法 backing store
   （進程級任意 R/W）                         │ 指標實體在 cage 外、你摸不到
                                             ▼
                                        只能 cage 內任意 R/W（撞 sandbox）
```

## 底層機制三：兩種劫持手法

不管 sandbox 開關，劫持 TypedArray 有兩種做法：

### 手法 A：破壞既有 TypedArray 的 data_ptr（corruption）

你有一個真的 TypedArray，用 cage 內讀寫原語（[Ch 16](./16-fake-object-rw.md)）直接改它的 data_ptr / external_pointer 欄位。sandbox off 時改成任意位址即成；sandbox on 時只能改 handle（如上，受限）。

### 手法 B：偽造一個 fake TypedArray（fakeobj）

在你控制的 double 陣列裡排一個**假 TypedArray 的完整欄位**（map=FLOAT64ELEMENTS map、buffer、byte_length、base_pointer、external_pointer/data_ptr），用 fakeobj 造出來。假 TypedArray 的欄位比 JSArray 多、佈局更複雜（要對 buffer、byte_offset、length、base/external pointer），但一旦排對，你**完全控制 data_ptr**。

TypedArray 的欄位比 JSArray 多幾格（instance size 60 vs JSArray 16），所以 fake TypedArray 的「排欄位」比 [Ch 16](./16-fake-object-rw.md) 的 fake array 更講究——但概念完全一樣（[Ch 16](./16-fake-object-rw.md) 的打包功夫直接套用）。

**實務上手法 A（破壞既有）比 B（偽造）簡單**：真的 TypedArray 的 map、buffer 都是合法的，你只需改 data_ptr 一處，出錯面小很多。所以現代主流是「**用 fake JSArray 得到 cage 內 R/W → 破壞一個真 TypedArray 的 data_ptr → 升級成穩定 TypedArray R/W**」。

## 用 challenge patch 真的跑（理論預期）

> **未實測，理論預期（需 [Ch 14](./14-first-oob.md) challenge patch 的 OOB，經 addrof/fakeobj/fake array 建 cage 內 R/W）**。TypedArray 欄位、data_ptr 組成、sandbox on/off 差異已在上面真跑。完整劫持需 patch build，這裡給預期行為，不捏造輸出。

**sandbox OFF build（經典，`out/x64.release.nosbx`）**：

```js
// 前置：ftoi/itof/addrof/fakeobj/read64/write64（cage 內，Ch 15/16）就緒
let ta = new Float64Array(0x100);            // 受害 TypedArray
let ta_addr = addrof(ta);
// TypedArray 的 data_ptr 欄位 offset（instance size 60，見 %DebugPrint 佈局）
// sandbox off：data_ptr 是明碼 64-bit，直接寫任意位址
write64(ta_addr + DATA_PTR_OFF, TARGET_ADDR);   // 把 data_ptr 改成任意進程位址
// 現在 ta 是一把指向 TARGET_ADDR 的槍：
function aar(addr) { write64(ta_addr + DATA_PTR_OFF, addr); return ftoi(ta[0]); }
function aaw(addr, v) { write64(ta_addr + DATA_PTR_OFF, addr); ta[0] = itof(v); }
// 預期：aar/aaw 讀寫進程任意位址（libc、堆疊…）——進程級任意 R/W
```

**sandbox ON build（現代 default，`out/x64.release`）**：

```js
// 同樣手法，但 data_ptr 欄位是 handle：
write64(ta_addr + EXT_PTR_OFF, 任意值);   // 只改到 handle（index）
// 預期：改不成任意進程位址。頂多換到 table 裡另一個合法 backing store。
// → 你仍只有 cage 內 R/W。要出 cage 需 Ch 34/35 破 external pointer table。
```

**這兩段的對比就是本章要你刻進骨子裡的事**：同一套劫持程式碼，sandbox off 給你進程級任意讀寫、sandbox on 撞牆停在 cage 內。**不要拿舊 writeup（sandbox 前）的「改 data_ptr 就贏」套到現代 default build。**

**重編**：套 [Ch 14](./14-first-oob.md) patch，分別 `autoninja -C out/x64.release d8` 和 `autoninja -C out/x64.release.nosbx d8`，兩顆都跑，親眼看差異。

## 對比：fake JSArray vs TypedArray 收尾

| 面向 | fake JSArray（[Ch 16](./16-fake-object-rw.md)） | TypedArray 劫持（本章） |
|---|---|---|
| 關鍵欄位 | elements 指標 | **data_ptr / byte_length** |
| 讀寫語意 | 過 elements kind 檢查、要 -8 | **直接 `data_ptr+i`**、無間接 |
| 每次瞄準 | 改 elements 欄位 | data_ptr 設一次，換 index 即可 |
| 佈局脆弱度 | 高（打包/tag/offset 易錯） | 低（改一個 data_ptr） |
| sandbox off | cage 內或全進程 | **全進程**（data_ptr 明碼） |
| sandbox on | cage 內 | **cage 內**（data_ptr 是 handle）|
| 地位 | 中繼 / 禁 TA 時的活路 | **現代主流收尾** |

**標準組合技**：OOB → fake JSArray（cage 內 R/W）→ 破壞真 TypedArray 的 data_ptr → 穩定 TypedArray R/W。前半用 fake array 的靈活、後半用 TypedArray 的乾淨。

## 踩雷集錦

1. **錯誤直覺：「改 data_ptr 就能任意讀寫（任何 build）」。正確**：只有 **sandbox off** 時 data_ptr 是明碼、改了就進程級任意 R/W。**sandbox on（現代 default）data_ptr 是 external pointer table 的 handle**，你只能改 index、指不到任意進程位址，被鎖在 cage 內。這是新手打現代 V8 最大的認知落差。
2. **錯誤直覺：「TypedArray 的 backing store 一定在 cage 外」。正確**：over 大 ArrayBuffer 的（off-heap）在 cage 外、`base_pointer=0`；小的 on-heap TA 的 backing 在 cage 內、`base_pointer` 非 0。兩者 data_ptr 組成方式不同，劫持要看清是哪種。
3. **錯誤直覺：「data_ptr = external_pointer」。正確**：`data_ptr = base_pointer + external_pointer`。off-heap 時 base=0 才相等；on-heap 時 base 非 0，要兩者相加。改錯欄位（只改 external 沒管 base）會算歪。
4. **錯誤直覺：「fake TypedArray 和 fake JSArray 一樣好排」。正確**：TypedArray instance size 60、欄位多（buffer、byte_offset、byte_length、length、base_pointer、external_pointer），排錯面大。實務多用**破壞既有 TypedArray**（只改 data_ptr 一處），出錯少。
5. **錯誤直覺：「拿到 TypedArray 任意讀寫 = 拿到 shell」。正確**：sandbox on 時你只有 cage 內 R/W，離 code exec 還有「破 sandbox（[Ch 34](./34-v8-sandbox.md)）+ RW→code exec（[Ch 32](./32-arbitrary-rw-to-code-exec.md)/[Ch 33](./33-wasm-rwx-jit-spray.md)）」兩大關。別把收尾原語當終點。

## 進階：再往深一層

- **external pointer table 的 handle 編碼**：sandbox on 時 `external_pointer = cage_base | (index << shift | tag)`。想確切理解 `+7` 從哪來、handle 怎麼解，看 `src/sandbox/external-pointer-table.h`——這是 [Ch 34](./34-v8-sandbox.md)/[Ch 35](./35-bypassing-v8-sandbox.md) 攻擊 table 本身的地基。攻 table：若你能改 table entry（table 在 cage 外，需先有某種 cage 外寫），就能把某個 handle 指向的 backing store 換成任意位址——這是破 sandbox 的一條路。
- **byte_length / length 的一致性**：改 data_ptr 時通常也把 byte_length/length 改到很大（如 `0x1000`），否則 `ta[大 i]` 被 length 擋。注意 TypedArray 有 `byte_length` 和 `length`（元素數）兩個欄位，且 detach 檢查會看 buffer——都要顧到，否則越界被擋或觸發 detach 檢查崩。
- **detach 與 resizable buffer 的額外攻擊面**：`ArrayBuffer.prototype.transfer()` detach 後 backing_store 置 null，若優化過的碼還握舊 data_ptr 就是 UAF（[Ch 8](./08-arraybuffer-typedarray.md) 提過）。resizable buffer 讓 length 在優化路徑中途變，是 Part 4 的 confusion 面。這些是「不靠 fake object 也能拿到 TypedArray 原語」的另一類 bug 來源。
- **為什麼現代主流仍是 TypedArray（即便 sandbox 限制它）**：因為在 cage 內，TypedArray 一樣是最乾淨的讀寫載體；sandbox 只是把它的「射程」從全進程縮到 cage 內。你先用它拿穩 cage 內 R/W，再單獨處理出 cage——分工比「一個原語打天下」清楚。
- **原始碼**：`src/objects/js-array-buffer.h`（JSTypedArray/JSArrayBuffer 欄位 offset、base/external pointer）、`src/objects/backing-store.h`（on/off-heap 判斷）、`src/sandbox/external-pointer-table.h`（handle 機制）。綁死 commit `ab2cad06`。

## 動手練習

1. **看清 data_ptr 組成**：真跑本章兩個 `%DebugPrint`（over ArrayBuffer 的大 TA、on-heap 小 TA），對照 `base_pointer`/`external_pointer`/`data_ptr` 三者關係，畫出「off-heap: base=0, data=external」「on-heap: base≠0, external=cage+handle」兩張圖。體會 sandbox 的 handle 從哪冒出來。
2. **sandbox on vs off 的欄位差異**：同一段建 TypedArray 的 JS，分別用 `x64.release`（on）和 `x64.release.nosbx`（off）的 d8 跑 `%DebugPrint`，比較 data_ptr 欄位（on 有 handle、off 是明碼）。這是理解「為什麼經典手法在現代 default 失效」最直接的實驗。
3. **套 patch 做劫持**（需 [Ch 14](./14-first-oob.md) patch）：在 **sandbox off** build 上，用 [Ch 16](./16-fake-object-rw.md) 的 read64/write64 破壞一個真 TypedArray 的 data_ptr，驗證能讀到一個已知進程位址（如 libc 某符號）。再到 **sandbox on** build 跑同段，觀察改 data_ptr 欄位為何得不到任意進程位址（只換到 table 裡的合法 backing）。親手撞一次 sandbox 的牆。

## 本章重點整理

- TypedArray 劫持是**現代 V8 利用的主流收尾原語**：改 `data_ptr`（讀寫起點）+ `byte_length`（射程），得到一把**設定一次就穩定、無 elements kind 間接**的任意讀寫槍，比 fake JSArray 乾淨。
- `data_ptr = base_pointer + external_pointer`；**off-heap**（over ArrayBuffer）base=0、data 是 cage 外 raw pointer；**on-heap** 小 TA base≠0、external 帶 handle。
- **sandbox off**：data_ptr 明碼 → 改成任意位址 = **進程級任意 R/W**（經典）。**sandbox on（default）**：data_ptr 是 **external pointer table 的 handle** → 只能改 index、指不到任意進程位址，**被鎖 cage 內**。這是攻防最關鍵的分野。
- 兩種手法：**破壞既有 TypedArray 的 data_ptr（簡單、主流）** vs **fakeobj 假 TypedArray（欄位多、易錯）**。標準組合：fake array 拿 cage 內 R/W → 破真 TypedArray → 穩定 TypedArray R/W。
- 拿到 TypedArray R/W ≠ 拿到 shell：sandbox on 時只有 cage 內，還要破 sandbox（[Ch 34](./34-v8-sandbox.md)）+ RW→code exec（[Ch 32](./32-arbitrary-rw-to-code-exec.md)/[Ch 33](./33-wasm-rwx-jit-spray.md)）。

## 自我檢核

- [ ] 能說出為什麼 TypedArray 收尾比 fake JSArray 乾淨（無 elements kind 間接、設定一次穩定）
- [ ] 能解釋 `data_ptr = base_pointer + external_pointer`，並區分 on/off-heap 的組成
- [ ] 能講清楚 sandbox off（data_ptr 明碼 → 全進程 R/W）vs sandbox on（handle → cage 內 R/W）的根本差異
- [ ] 能說出「破壞既有 TypedArray」為什麼比「偽造 fake TypedArray」實務上更常用
- [ ] 知道拿到 TypedArray R/W 後在 sandbox on 下還差哪兩大關才到 code exec
- [ ] （面試題）「為什麼 TypedArray 的 data_ptr 是現代 V8 exploit 的黃金收尾？V8 Sandbox 用什麼機制讓它變難？」能完整答出

## 延伸閱讀

- **[“The V8 Sandbox” — v8.dev/blog/sandbox](https://v8.dev/blog/sandbox)**
  - **這篇說什麼**：官方講 external pointer table 如何保護 backing store / data_ptr、把「改指標就任意讀寫」的經典路徑斬斷。
  - **讀哪裡**：external pointer table 與 backing store 段落。本章 `external_pointer = cage_base + handle` 在這有解。
  - **關聯**：本章 sandbox on 那半的權威來源，直通 [Ch 34](./34-v8-sandbox.md)。
- **[Project Zero “Virtually Unlimited Memory” / TypedArray data_ptr 利用 writeup](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實案例示範拿到原語後如何操縱 TypedArray 達成任意讀寫、以及 sandbox 帶來的額外步驟。
  - **讀哪裡**：任意讀寫收尾與繞 sandbox 段落。
  - **關聯**：把本章靜態欄位接到真實 exploit 收尾動作。
- **[V8 `src/sandbox/external-pointer-table.h` / `src/objects/js-array-buffer.h` 原始碼](https://source.chromium.org/chromium/chromium/src/+/main:v8/src/sandbox/external-pointer-table.h)**
  - **這篇說什麼**：external pointer table 的 handle 編碼、以及 JSTypedArray/JSArrayBuffer 的欄位 offset——你劫持 data_ptr 的精確依據與 sandbox 攔你的機制。
  - **讀哪裡**：`ExternalPointerTable` 的 handle/tag、`JSTypedArray` 的 `kBasePointerOffset`/`kExternalPointerOffset`。
  - **關聯**：本章欄位與 handle 機制的權威定義，綁死 commit `ab2cad06`。

fake array 和 TypedArray 兩把收尾都齊了，也看清了 sandbox 怎麼把它們鎖在 cage 內。下一章把整條「OOB → addrof/fakeobj → 任意 R/W」串成一套可重用的 exploit template，並統整 pointer compression 對整套原語的影響——你會得到一份能直接搬去打 CTF 的骨架。

→ [Ch 18 — 「OOB → 任意 R/W」標準流程整合](./18-oob-to-arbitrary-rw.md)
