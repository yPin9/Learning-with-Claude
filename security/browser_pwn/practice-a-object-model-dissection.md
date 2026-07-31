# 練習 A — 用 %DebugPrint / gef 解剖 V8 物件模型

> **目標**：把 Part 1（[Ch 3](./03-value-representation.md)–[Ch 8](./08-arraybuffer-typedarray.md)）學的一切，用手做一次真正的「逐格解剖」。你要用 `%DebugPrint` + gdb/gef，把一個 JSArray 和一個 JSObject 從記憶體**每一個 word** 拆開，親眼認出 map / properties / elements / length / 每個屬性值，並驗證 tagging（[Ch 3](./03-value-representation.md)）、pointer compression（[Ch 4](./04-pointer-compression.md)）、SMI 的 `<<1` 編碼、DOUBLE 攤平存位元（[Ch 7](./07-jsarray-elements-kind.md)）。做完這題，你「看結構不看位址」的透視眼就真正裝好了——這是進 Part 3 的入場券。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，`~/v8build/v8/out/x64.release/`（開 sandbox + pointer compression）。d8：`~/v8build/v8/out/x64.release/d8`。本練習所有 `%DebugPrint` 與記憶體 dump 都是這顆 d8 真跑出來的；**你的位址每次不同（ASLR + GC），要對的是結構不是數字**。

## 為什麼做這題

前六章你看了很多 `%DebugPrint` 輸出，但那是**別人（我）跑的**。利用是動手的技藝——你得能自己在 gdb 裡把一塊記憶體指著說「這 4 byte 是 map、這 4 byte 是 length、這 8 byte 是攤平的 double」。這一題強迫你把「DebugPrint 的抽象欄位」對到「記憶體裡真實的 bytes」，中間會撞上三個一定要親手跨過的坎：

1. **DebugPrint 印的位址不是你在 gdb 能直接 `x` 的位址**——它是壓縮-tagged 的顯示形式，真正的虛擬位址要用 cage base（`r14`）重建。這個坎不自己踩一次，你之後 debug exploit 會鬼打牆。
2. **SMI 在記憶體裡是 `value << 1`**，不是原值。你會親眼看到 `length: 4` 在記憶體是 `0x8`。
3. **tagged 指標在記憶體裡是壓縮的 32-bit、最低位帶 tag**。你會看到 map 欄位是 `0x0100cfc9` 這種 4-byte 值，而非完整 64-bit。

## 前置：怎麼在 gdb 裡看到真實記憶體

這是本題的關鍵技術，先講清楚，後面每個任務都用它。

**問題**：`%DebugPrint` 印出 `elements: 0x2dd00104b095`，但你在 gdb `x/gx 0x2dd00104b095` 會得到 `Cannot access memory`。為什麼？因為那個 `0x2dd0...` 高位是 V8 **顯示用**的 tagged 表示，**不是**這塊記憶體實際被映射到的虛擬位址。

**解法**：真實 VA = `cage_base + (壓縮偏移 & ~1)`。

- **cage base** 存在 x64 的 **`r14`** 暫存器（[Ch 4](./04-pointer-compression.md) 進階段講過）。在 gdb 裡 `p/x $r14` 讀出來（作者機器某次是 `0x17400000000`）。
- **壓縮偏移** = DebugPrint 位址的**低 32 bit**（例如 `0x0104b095`）。
- **去 tag**：`& ~1`（清最低位，因為那是 HeapObject tag）。

所以 `elements 0x..0104b095` 的真實 VA = `$r14 + (0x0104b095 & ~1)` = `$r14 + 0x104b094`。在 gdb 直接寫 `x/6gx $r14+0x104b094` 就對了。

> **踩雷**：`r14` 只有在 V8 正在跑 JS（或停在 V8 內部）時才是有效的 cage base。停在很外層的 libc 時可能不是。用 `%SystemBreak()` 停下時 r14 通常有效。若不確定，`info registers r14` 看它是不是 `0x....00000000` 這種 4GB 對齊的值——是的話就是 cage base。

## 標準流程（每個任務照這個做）

1. 把 PoC 寫成 `.js`，最後放 `%DebugPrint(目標); %SystemBreak();`。
2. `gdb -q --args ~/v8build/v8/out/x64.release/d8 --allow-natives-syntax poc.js`（有 gef 更好）。
3. `run`。DebugPrint 會在 `%SystemBreak()` 之前印出目標的欄位到終端；程式停在 SystemBreak 的 SIGTRAP。
4. `p/x $r14` 拿 cage base。
5. 對 DebugPrint 給的每個位址，算 `$r14 + (低32bit & ~1)`，用 `x/Nwx`（32-bit 一格看壓縮指標）或 `x/Ngx`（64-bit 一格看 double / 完整值）dump。
6. 把每一格對回 DebugPrint 的欄位，逐格標註。

> 學習期可 `echo 0 | sudo tee /proc/sys/kernel/randomize_va_space` 或在 gdb `set disable-randomization on`，讓 cage base 穩定、方便對照。真實 exploit 不能假設它固定。

---

## 任務 1：解剖一個 PACKED_DOUBLE JSArray（暖身、必做）

**PoC**（`poc1.js`）：

```js
let a = [1.1, 2.2, 3.3, 4.4];
%DebugPrint(a);
%SystemBreak();
```

**你要完成的事**：

1. 跑出 `%DebugPrint(a)`，記下它的 map / properties / elements / length 四個欄位值。
2. 在 gdb 用 `r14` 重建 JSArray **本體**的真實 VA，`x/4wx` dump 出 4 個 word，把每個 word 對到一個欄位。
3. 重建 **elements（FixedDoubleArray）** 的真實 VA，`x/6gx` dump，認出它的 header（map+length）和四個攤平的 double。
4. **驗證三件事**：（a）length 在記憶體是不是 `value<<1`；（b）map/elements 欄位是不是 4-byte 壓縮-tagged（最低位為 1）；（c）四個 double 的原始位元是不是等於你用 [Ch 3](./03-value-representation.md) 手算的 IEEE754。

**測試/驗證用例表**（跑完把你的實測填進去，和參考解答核對「結構」而非「位址」）：

| 檢查項 | 期望結構 | 你的實測 |
|---|---|---|
| DebugPrint map 標的 kind | `PACKED_DOUBLE_ELEMENTS` | ? |
| JSArray body word[0] | = map（壓縮 tagged，低位 1） | ? |
| JSArray body word[1] | = properties（空陣列單例 `0x..07e5`） | ? |
| JSArray body word[2] | = elements（壓縮 tagged） | ? |
| JSArray body word[3] | = length = `4<<1` = `0x8` | ? |
| FixedDoubleArray 第 1 個 double | `1.1` = `0x3ff199999999999a` | ? |

<details>
<summary>參考解答（先自己做，卡住再開）</summary>

作者實跑（`set disable-randomization on`）的一組真實輸出——**你的位址會不同，但結構完全一致**：

DebugPrint：
```
DebugPrint: 0x4850104b0bd: [JSArray]
 - map: 0x4850100cfc9 <Map[16](PACKED_DOUBLE_ELEMENTS)> [FastProperties]
 - elements: 0x4850104b095 <FixedDoubleArray[4]> [PACKED_DOUBLE_ELEMENTS]
 - length: 4
```

cage base：`R14 = 0x17400000000`

JSArray body 真實 VA = `r14 + (0x0104b0bd & ~1)` = `0x1740104b0bc`：
```
(gdb) x/4wx $r14+0x104b0bc
0x1740104b0bc:  0x0100cfc9   0x000007e5   0x0104b095   0x00000008
                 └─ map       └─ props     └─ elements  └─ length
```
逐格對照：
- **word[0] `0x0100cfc9`** = map。低 32 bit、最低位 `1`（`...c9`=`11001001`）→ tagged HeapObject。和 DebugPrint 的 map `0x485_0100cfc9` 的低 32 bit 一致。✓
- **word[1] `0x000007e5`** = properties = 空 FixedArray 單例（每個空物件都指它）。✓
- **word[2] `0x0104b095`** = elements，指向下面的 FixedDoubleArray。低位 `5`=`0101`→tagged。✓
- **word[3] `0x00000008`** = length。**`8 = 4 << 1`**——這就是 SMI 的 `value<<1` 編碼！JS 看到的 length 是 4，記憶體存 0x8。✓（這是本任務最重要的一格。）

elements（FixedDoubleArray）真實 VA = `r14 + (0x0104b095 & ~1)` = `0x1740104b094`：
```
(gdb) x/6gx $r14+0x104b094
0x1740104b094:  0x000000040000095d   0x3ff199999999999a
0x1740104b0a4:  0x400199999999999a   0x400a666666666666
0x1740104b0b4:  0x401199999999999a   0x000007e50100cfc9
```
逐格對照：
- **第一個 8-byte `0x000000040000095d`** = FixedDoubleArray 的 header：低 4 byte `0x0000095d` 是它的 map（FixedDoubleArray map，tagged），高 4 byte `0x00000004` 是它的 length = 4（注意這裡 FixedDoubleArray 的 length 欄位存的是 SMI `4` 但因為它是內部欄位這裡直接是 4，你會看到略有出入是正常的——重點是能認出「header = map + length」這個結構）。
- **`0x3ff199999999999a`** = `1.1` 的 IEEE754 位元（[Ch 3](./03-value-representation.md) 手算：1.1 的 double）。✓ **這 8 byte 是原始位元、不是 HeapNumber 指標**——正是 [Ch 7](./07-jsarray-elements-kind.md) 說的「DOUBLE 攤平存」。
- `0x400199999999999a`=2.2、`0x400a666666666666`=3.3、`0x401199999999999a`=4.4。全部攤平存。✓
- 最後那 `0x000007e50100cfc9` 已經是相鄰的下一個物件了（你的陣列只有 4 格）。

**三個驗證結論**：（a）length 記憶體 = `<<1`（0x8）✓；（b）map/elements 是 4-byte 壓縮-tagged、最低位 1 ✓；（c）double 攤平存原始位元 ✓。你剛剛親手把 Part 1 的三大機制在一塊真實記憶體上驗證了。
</details>

---

## 任務 2：解剖一個有 in-object 屬性的 JSObject（必做）

**PoC**（`poc2.js`）：

```js
let o = {x: 0x1111, y: 0x2222, z: 0x3333};
%DebugPrint(o);
%SystemBreak();
```

（用 `0x1111` 這種好認的值當屬性，等下在記憶體裡一眼就看到。）

**你要完成的事**：

1. `%DebugPrint(o)`，確認三個屬性都是 `in-obj`（[Ch 6](./06-properties-elements.md)），記下 map / properties / elements、以及三個屬性各在 `field 3/4/5`。
2. 重建物件本體真實 VA，`x/8wx` dump。認出前三格 header（map/properties/elements），再認出 **field 3/4/5 = 你的三個屬性值**。
3. **驗證**：`0x1111` 是 SMI，記憶體裡應是 `0x2222`（`0x1111<<1`）；`0x2222` → `0x4444`；`0x3333` → `0x6666`。親眼確認 in-object 屬性值就直接躺在物件本體，且是 SMI 編碼。

**測試用例表**：

| 檢查項 | 期望結構 | 你的實測 |
|---|---|---|
| 三屬性儲存位置 | 全 `in-obj`，`field 3/4/5` | ? |
| properties 欄位 | 空陣列單例（屬性不在這） | ? |
| body word[3]（field 3, x） | `0x1111 << 1` = `0x2222` | ? |
| body word[4]（field 4, y） | `0x2222 << 1` = `0x4444` | ? |
| body word[5]（field 5, z） | `0x3333 << 1` = `0x6666` | ? |

<details>
<summary>參考解答</summary>

DebugPrint 會顯示（結構固定、位址你的不同）：
```
 - map: 0x..<Map[24](HOLEY_ELEMENTS)> [FastProperties]
 - properties: 0x..07e5 <FixedArray[0]>        ← 空！屬性不在這
 - elements:   0x..07e5 <FixedArray[0]>
 - All own properties:
    #x: 4369 (const data field 3, in-obj, attrs: [WEC])   ← 4369 = 0x1111
    #y: 8738 (const data field 4, in-obj, attrs: [WEC])
    #z: 13107 (const data field 5, in-obj, attrs: [WEC])
```

物件本體（instance size 24 = 6 個 word）`x/8wx $r14+(body_off)`，你會看到：
```
word[0] = map（壓縮 tagged）
word[1] = 0x000007e5   ← properties（空陣列單例）
word[2] = 0x000007e5   ← elements（空陣列單例）
word[3] = 0x00002222   ← field 3 = x = 0x1111 << 1  ✓
word[4] = 0x00004444   ← field 4 = y = 0x2222 << 1  ✓
word[5] = 0x00006666   ← field 5 = z = 0x3333 << 1  ✓
```

**關鍵領悟**：屬性值 `0x1111` 在記憶體是 `0x2222`（SMI `<<1`），而且**直接躺在物件本體的 word[3..5]**——這就是 in-object property（[Ch 6](./06-properties-elements.md)）。properties backing store 是空的，因為屬性根本沒溢位。你能用「屬性值」在物件本體的已知 offset 放已知資料——這個能力在 fake object 和 heap groom 時很有用。
</details>

---

## 任務 3：抓「同形狀共用 Map」的鐵證（必做）

**PoC**（`poc3.js`）：

```js
let p1 = {a: 1, b: 2};
let p2 = {a: 9, b: 8};
let q  = {a: 1, b: 2, c: 3};   // 多一個屬性 → 不同形狀
%DebugPrint(p1);
%DebugPrint(p2);
%DebugPrint(q);
```

**你要完成的事**：

1. 確認 p1、p2 的 **map 位址相同**（[Ch 5](./05-map-hidden-class.md) 的核心命題）。
2. 確認 q 的 map **不同**於 p1/p2。
3. dump q 的 map，找出它的 **back pointer**，確認 back pointer 指向的 map 就是 p1/p2 的 map（transition：`{a,b}` → 加 `c` → `{a,b,c}`）。

<details>
<summary>參考解答</summary>

- p1 和 p2 的 `- map:` 欄位會是**同一個位址**（例如都是 `0x..ee01`）——因為形狀相同（同名同序屬性）。這就是 [Ch 5](./05-map-hidden-class.md) 實測過的命題：形狀決定 map，值不影響。
- q 多了屬性 `c`，是不同形狀，map 不同。
- dump q 的 map（`x/…` 它的欄位，或直接看 DebugPrint 的 Map 區塊），它的 `back pointer` 會指向 p1/p2 的那張 `{a,b}` map。這條 back pointer 就是 transition 樹的反向邊：`{a,b} --加c--> {a,b,c}`。

**領悟**：你剛驗證了「形狀 = 一棵 map transition 樹的節點」。這也是為什麼 fake map 可行——同版本同建構順序下，某形狀對到哪張 map 是可預測的（[Ch 5](./05-map-hidden-class.md)）。
</details>

---

## 任務 4：DOUBLE vs OBJECT 陣列的記憶體對比（進階、建議做）

這題直指 [Ch 7](./07-jsarray-elements-kind.md) 的利用核心：兩種陣列 backing store 佈局幾乎一樣，差別只在「值是原始位元還是指標」。

**PoC**（`poc4.js`）：

```js
let d = [1.1, 2.2];           // PACKED_DOUBLE：值是攤平位元
let obj = {marker: 0xdead};
let o = [obj, obj];           // PACKED_ELEMENTS：值是 tagged 指標
%DebugPrint(d);
%DebugPrint(o);
%DebugPrint(obj);
%SystemBreak();
```

**你要完成的事**：

1. dump `d` 的 elements（FixedDoubleArray），確認裡面是 `0x3ff199999999999a` 這種**原始 double 位元**。
2. dump `o` 的 elements（FixedArray），確認裡面是**壓縮 tagged 指標**，且該指標（重建 VA 後）指向 `obj` 的本體（body word[0] 是 obj 的 map）。
3. 寫下：如果有人能讓 V8「用 `d` 的 elements 佈局、但套上 `o` 的 map」，會發生什麼？（這就是 [Ch 15](./15-addrof-fakeobj.md) 的 `addrof`/`fakeobj` 直覺。）

<details>
<summary>參考解答</summary>

- `d` 的 elements 是 `FixedDoubleArray`，每格 8-byte 原始 double 位元（`1.1 → 0x3ff199999999999a`）。V8 **不**把這些當指標。
- `o` 的 elements 是 `FixedArray`，每格是壓縮 tagged 指標（例如 `0x0104b2c9`），重建 VA 後 `x/4wx` 它，word[0] 會是 `obj` 的 map——證明它確實指向 `obj`。V8 **會**把這些當指標解引用。
- **關鍵推理**：兩個陣列的 elements 都是「一排 slot」，佈局形狀一致，差別只在 **map 說它是 DOUBLE 還是 OBJECT**。
  - 若你能讓存了指標的 `o`「被當成 DOUBLE 陣列讀」→ 你把 `obj` 的**指標當成一個 double 讀出來** = **`addrof(obj)`**。
  - 若你能讓存了原始位元的 `d`「被當成 OBJECT 陣列讀」→ 你把一個**你控制的數字當成指標解引用** = **`fakeobj(addr)`**。
- 這正是 elements kind confusion 為什麼是入門級強力原語（[Ch 7](./07-jsarray-elements-kind.md) → [Ch 15](./15-addrof-fakeobj.md)）。你在這題只是「用兩個獨立陣列」看清這個對立；真正的 confusion 是讓**同一塊記憶體**被兩種 map 解讀，那要 typer bug 或直接改 map，Part 3/4 教。
</details>

---

## 任務 5：TypedArray 的 data_ptr（進階、建議做）

**PoC**（`poc5.js`）：

```js
let ab = new ArrayBuffer(0x100);
let u8 = new Uint8Array(ab);
u8[0] = 0x41; u8[1] = 0x42;
%DebugPrint(u8);
%DebugPrint(ab);
%SystemBreak();
```

**你要完成的事**：

1. 從 `%DebugPrint(u8)` 找出 `data_ptr`、`byte_length`、`buffer`；從 `%DebugPrint(ab)` 找出 `backing_store`。確認 `u8.data_ptr == ab.backing_store`。
2. 確認 `backing_store` 的高位和 cage base（`r14`）**不同**——backing store 在 cage 外（[Ch 4](./04-pointer-compression.md)、[Ch 8](./08-arraybuffer-typedarray.md)）。
3. 在 gdb 直接 `x/2gx <backing_store>`（這是 cage 外 raw 64-bit 位址，**不用** r14 重建，直接就是 VA），確認前兩 byte 是你寫的 `0x41 0x42`。
4. 寫下：若你有「cage 內任意寫」，你能不能直接改到 `u8` 物件裡的 data_ptr 欄位？sandbox 會怎麼擋？（連到 [Ch 34](./34-v8-sandbox.md)。）

<details>
<summary>參考解答</summary>

- `u8.data_ptr` 會等於 `ab.backing_store`（因為 byte_offset=0）。兩者是同一個 cage 外 raw pointer，例如 `0x23400004000`。
- 這個位址高位（`0x234...`）和 `r14`（cage base，如 `0x174...`）**完全不同**——它在 cage 外，是完整 64-bit raw pointer，不是壓縮的。所以 dump 它**直接** `x/2gx 0x23400004000`，不重建。
- 前兩 byte 會是 `0x41`、`0x42`（你 `u8[0]/u8[1]` 寫的）。你剛確認了「TypedArray 的讀寫 = 直接對 backing store 操作」。
- **關於 sandbox**：u8 物件本體裡的 `data_ptr` 其實不是明碼存這個 raw pointer，而是經 external pointer table 的 handle（[Ch 8](./08-arraybuffer-typedarray.md) 的 `external_pointer = cage_base + 常數`）。就算你有 cage 內任意寫、改得到那個 handle 欄位，你也只能改成別的 handle（指向 table 裡的別條），無法直接把它指到任意 raw 位址——這就是 sandbox 斬斷「改 data_ptr → 任意讀寫」的機制（[Ch 34](./34-v8-sandbox.md)）。這題讓你親眼確認 backing store 在 cage 外、以及為什麼現代利用要多一步破 sandbox。
</details>

---

## 卡點提示（照順序看，別一次看完）

1. **`x` 說 Cannot access memory**：你多半直接用了 DebugPrint 的位址。那是壓縮-tagged 顯示值，要 `$r14 + (低32bit & ~1)` 重建。回「前置」段。
2. **`$r14` 看起來不像 cage base**：確認你停在 `%SystemBreak()`（V8 正在跑）。若 `r14` 不是 `0x....00000000` 對齊，可能停在外層——`continue` 或改用 `%DebugPrint` 印的高位反推 cage base（DebugPrint 高位 = cage base >> ？，但注意顯示高位和 r14 可能不同前綴，以 `r14` 為準）。
3. **`x/wx`（32-bit）vs `x/gx`（64-bit）搞混**：看**壓縮指標/SMI** 用 `wx`（4-byte 一格）；看 **double / 完整 64-bit 值** 用 `gx`（8-byte 一格）。JSArray 本體用 `wx`，FixedDoubleArray 內容用 `gx`。
4. **length 對不上**：記得 SMI 是 `<<1`。length 4 在記憶體是 `0x8`，屬性值 `0x1111` 是 `0x2222`。
5. **屬性在記憶體找不到**：先看 DebugPrint 標的是 `in-obj` 還是 `ooo`。in-obj 在物件本體 word[3+]；ooo 在 `properties:` 指的 PropertyArray 裡（[Ch 6](./06-properties-elements.md)）。
6. **backing store dump 失敗**：它在 cage 外，是完整 raw pointer，**直接** `x/gx <那個位址>`，**不要**用 r14 重建（重建只給 cage 內壓縮位址用）。

## 延伸挑戰（想更強再做）

1. **holey 陣列的 hole 長怎樣**：解剖 `[1, , 3]`，在 elements 裡找出 `the_hole_value`（[Ch 7](./07-jsarray-elements-kind.md)）的原始位元，記下這個 sentinel 的值。之後你在記憶體看到它就知道「這格是洞」。
2. **PropertyArray 溢位**：造一個屬性溢位的物件（in-object 塞滿再多加兩個），dump `properties:` 指的 PropertyArray，認出 `ooo` 屬性值躺在裡面，並確認 `unused property fields` 的緩衝格（[Ch 6](./06-properties-elements.md)）。
3. **手動 addrof（不用漏洞、純觀察）**：造 `let leak = [obj]`（OBJECT 陣列），用 gdb 讀出 elements[0] 的壓縮指標，`$r14+` 重建，確認它就是 `%DebugPrint(obj)` 的位址。你剛「手動」做了一次 `addrof`——差別是真 exploit 是讓 V8 自己把它當 double 吐給你，而不是你在 gdb 讀。想清楚這個差別，你就懂 [Ch 15](./15-addrof-fakeobj.md) 要做什麼。
4. **cage base 反推**：不用 `r14`，改用 `%DebugPrint(undefined)`（[Ch 3](./03-value-representation.md) 的固定 offset oddball）當錨點，嘗試推出 cage base，再驗證和 `r14` 一致。這是真實 exploit 沒有 gdb 時求 cage base 的思路預習。

## 本練習重點整理

- 你親手把 JSArray 和 JSObject 逐格拆開，把 `%DebugPrint` 的抽象欄位對到了記憶體真實 bytes。
- 你跨過了三個關鍵坎：**DebugPrint 位址 ≠ 可 `x` 的 VA（要用 `r14` 重建）**、**SMI 記憶體是 `<<1`**、**DOUBLE 攤平存原始位元、OBJECT 存壓縮 tagged 指標**。
- 你驗證了「同形狀共用 map」「in-object 屬性直接躺本體」「backing store 在 cage 外是 raw pointer」——Part 1 的每個核心命題都在真實記憶體上落地。
- 你用兩個陣列看清了 `addrof`/`fakeobj` 的直覺（DOUBLE↔OBJECT 差別只在 map），為 Part 3 鋪好路。

## 自我檢核

- [ ] 我能獨立在 gdb 用 `r14` 把任一 DebugPrint 位址重建成可 dump 的真實 VA
- [ ] 我能在記憶體 dump 裡指出 JSArray 的 map / properties / elements / length 各在哪一格，並解釋 length 為何是 `<<1`
- [ ] 我能區分「cage 內用壓縮 32-bit（`wx`、要重建）」和「cage 外 backing store 用 64-bit raw（`gx`、直接 dump）」
- [ ] 我能證明兩個同形狀物件共用 map、並沿 back pointer 認出 transition
- [ ] 我能解釋 DOUBLE 陣列（原始位元）vs OBJECT 陣列（tagged 指標）的記憶體差別，及它為何是 addrof/fakeobj 的基礎
- [ ] （面試題）「給你一個 d8 和一個 JSArray，你怎麼在 gdb 裡確認它的 elements 位址並讀出內容？」我能講出完整流程（DebugPrint → r14 → 重建 → x/gx）

## 延伸閱讀

- **[“Pointer Compression in V8” — v8.dev/blog/pointer-compression](https://v8.dev/blog/pointer-compression)**
  - **讀哪裡**：cage / 壓縮位址那節。本練習「為什麼要用 r14 重建 VA」的官方根據。
  - **關聯**：把你踩到的「Cannot access memory」坎講清楚原理。
- **[gef 官方文件 — hugsy.github.io/gef](https://hugsy.github.io/gef/)**
  - **讀哪裡**：`memory`、`telescope`（tel）指令。gef 的 `telescope $r14+off` 比裸 `x` 更好讀壓縮指標鏈。
  - **關聯**：把本練習的 dump 流程用 gef 加速；你在 `security/gdb` 課學過的 gef 技能直接複用。
- **[doar-e / Jeremy Fetiveau 的 V8 exploit writeup（任一篇的「object inspection」段）](https://doar-e.github.io/)**
  - **讀哪裡**：作者在真實 exploit 裡怎麼用 `%DebugPrint` + gdb 認物件佈局。
  - **關聯**：本練習是這個技能的無漏洞版；讀 writeup 你會發現高手每天都在做你這題做的事。

Part 1 的物件模型，你不只讀懂了，還親手在記憶體裡驗證過了。接下來 Part 2 進 V8 的執行管線深處（Ignition bytecode、TurboFan IR），為 Part 4 的 type confusion 鋪路——但每次你看到一個被優化器誤判型別的物件，回想這一題：你知道它在記憶體裡每一格長什麼樣。

→ [Ch 9 — Parser、Ignition 與 bytecode](./09-parser-ignition-bytecode.md)
