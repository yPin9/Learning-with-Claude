# Ch 18 — 「OOB → 任意 R/W」標準流程整合

> **目標**：把 [Ch 14](./14-first-oob.md)–[Ch 17](./17-typedarray-attack.md) 的每一步串成**一套可重用的 exploit template**——`ftoi`/`itof` → `addrof`/`fakeobj` → `read64`/`write64` → 穩定 TypedArray R/W。你會得到一份能直接搬去打 CTF 的骨架，只要換掉最前面的「OOB 來源」（challenge patch 或 Part 4 的真 bug）即可。同時把 **pointer compression 對整套原語的影響**講死：為什麼 sandbox on 時你的 R/W 天生**只在 cage 內**、什麼時候你需要「cage 外」能力、以及這條分界線如何決定你接下來要不要走 [Ch 34](./34-v8-sandbox.md)。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`。sandbox on = `~/v8build/v8/out/x64.release/d8`；off = `~/v8build/v8/out/x64.release.nosbx/d8`。**可驗證的中間步驟（IEEE754、%DebugPrint 佈局）已在 [Ch 15](./15-addrof-fakeobj.md)–[Ch 17](./17-typedarray-attack.md) 真跑**；**完整 template 需 [Ch 14](./14-first-oob.md) 的 challenge patch OOB**，標「理論預期」，不捏造輸出。

## 為什麼需要這個？

前四章你學了一堆原語，但它們散在各處。真正打 CTF 時，你需要的是**一份骨架**——一個把「拿到 OOB」變成「拿到 read64/write64」的固定流程，讓你每次比賽只需填那個「OOB 從哪來」的空格，其餘照抄。

這正是資深 pwner 的工作方式：**exploit 的 90% 是 template，10% 是這題特有的 bug 觸發**。addrof/fakeobj/read64/write64 對每一題幾乎一模一樣，值得寫成一份你自己的、閉著眼睛能默寫的模板。這一章就是幫你把這份模板定型，並標清楚每一格「哪些是常數、哪些每題要換、哪些受 sandbox 影響」。

此外，pointer compression 這條線在前幾章零散提過，這裡要一次講死：**它把你的 R/W 切成「cage 內」和「cage 外」兩個世界**，這條線決定了你的 exploit 到此為止（cage 內原語）還是要繼續（破 sandbox 出 cage）。搞懂這條線，你才知道一題「拿到 read64/write64」之後，離「拿 shell」還有多遠。

## 先建立直覺：一條流水線，四個工位

```
   OOB 來源                 兩把鑰匙               任意讀寫             穩定收尾
   ┌──────────┐   confusion  ┌──────────┐  fake array ┌──────────┐  劫持 ┌──────────┐
   │ challenge│─────────────►│ addrof   │────────────►│ read64   │──────►│ TypedArray│
   │ patch /  │              │ fakeobj  │             │ write64  │       │ data_ptr │
   │ 真 bug   │              │ (Ch 15)  │             │ (Ch 16)  │       │ (Ch 17)  │
   └──────────┘              └──────────┘             └──────────┘       └──────────┘
      每題不同                  幾乎不變                  幾乎不變            幾乎不變
        ▲                                                                     │
        │                                                          ┌──────────┴──────────┐
   你唯一要換的空格                                             sandbox on → cage 內為止
                                                                sandbox off → 進程級 R/W
```

四個工位，只有**最左邊「OOB 來源」每題要換**，右邊三個工位是共用 template。這就是為什麼把「地基原語」和「漏洞來源」拆開教（[Ch 14](./14-first-oob.md) 開篇講的）——你學一次原語，全課、乃至你整個 CTF 生涯都在複用。

## 完整 exploit template（可重用骨架）

下面是把 [Ch 15](./15-addrof-fakeobj.md)–[Ch 17](./17-typedarray-attack.md) 串起來的完整骨架。**中間可驗證的部分（IEEE754）在乾淨 d8 真跑無誤；confusion→原語那段標「理論預期」（需 [Ch 14](./14-first-oob.md) patch）。**

```js
// ============================================================
//  V8 exploit template — OOB → arbitrary R/W
//  環境：V8 15.3.0 (ab2cad06), pointer compression on
// ============================================================

// --- [1] IEEE754 位元互轉（真跑可驗證，Ch 15）---
let _buf = new ArrayBuffer(8);
let _f64 = new Float64Array(_buf);
let _u64 = new BigUint64Array(_buf);
function ftoi(f) { _f64[0] = f; return _u64[0]; }
function itof(i) { _u64[0] = i; return _f64[0]; }
function hex(x)  { return '0x' + x.toString(16); }

// --- [2] OOB 來源：每題唯一要換的空格 ---
//   challenge patch：靠 Array.prototype.oob（Ch 14）
//   Part 4 真 bug：靠 TurboFan CheckBounds 消除等（Ch 19+）
//   ↓ 假設它給我們一個 double 陣列 dbl 與相鄰 object 陣列 obj 的 confusion
//     （或直接的相對讀寫），下面把它包成 addrof/fakeobj。

// --- [3] addrof / fakeobj（理論預期，Ch 15）---
//   假設 dbl_arr / obj_arr 已別名到同一塊 elements
function addrof(o) { obj_arr[0] = o; return ftoi(dbl_arr[0]); }
function fakeobj(addr) { dbl_arr[0] = itof(addr); return obj_arr[0]; }

// --- [4] read64 / write64：cage 內任意讀寫（理論預期，Ch 16）---
//   用 fake JSArray：controller 是 double 陣列，排 fake array 欄位
//   （打包/tag/offset 見 Ch 16；此處示介面）
let controller = [itof(0n), itof(0n), 1.1, 2.2, 3.3, 4.4];
let fake_arr = fakeobj(/* controller.elements 位址 + tag */);
function read64(addr) {
  controller[1] = itof((BigInt(SMI_BIG_LEN) << 32n) | BigInt(addr - 8n));
  return ftoi(fake_arr[0]);
}
function write64(addr, val) {
  controller[1] = itof((BigInt(SMI_BIG_LEN) << 32n) | BigInt(addr - 8n));
  fake_arr[0] = itof(val);
}

// --- [5] 穩定收尾：劫持 TypedArray 的 data_ptr（理論預期，Ch 17）---
let ta = new Float64Array(0x1000);
let ta_addr = addrof(ta);
function aar(addr) {                  // 穩定 arbitrary read
  write64(ta_addr + DATA_PTR_OFF, addr);
  return ftoi(ta[0]);
}
function aaw(addr, val) {             // 穩定 arbitrary write
  write64(ta_addr + DATA_PTR_OFF, addr);
  ta[0] = itof(val);
}
// sandbox off：aar/aaw 讀寫進程任意位址（進程級 R/W）
// sandbox on ：只到 cage 內；出 cage 需 Ch 34/35
```

**這份骨架的價值在於「分層」**：`ftoi`/`itof` 是純工具（真跑無誤），`addrof`/`fakeobj` 是原語核心，`read64`/`write64` 是 cage 內 R/W，`aar`/`aaw` 是穩定收尾。你每題只換 [2] 那一格，其餘幾乎照抄。**把這份骨架背下來、抄進你的 CTF 筆記，就是你打 V8 題的起手式。**

## 哪些是常數、哪些每題要換

template 裡有幾個「值」要釐清來源，否則你會不知道 `SMI_BIG_LEN`、`DATA_PTR_OFF` 從哪來：

| 符號 | 是什麼 | 怎麼取得 | 每題會變嗎 |
|---|---|---|---|
| `ftoi`/`itof` | IEEE754 互轉 | 固定寫法 | 永不變 |
| OOB 來源 | 越界能力 | challenge patch / 真 bug | **每題必換** |
| `double_map`/`empty_properties` | fake array 要用的 map/空 properties | addrof + 讀出來（一次性洩漏） | 同 build 內固定 |
| `SMI_BIG_LEN` | fake array 的 length（SMI 編碼） | 你自己選（如 `0x1000<<1`） | 常數 |
| `DATA_PTR_OFF` | TypedArray data_ptr 欄位 offset | `%DebugPrint` 佈局 / 原始碼 | 綁 commit，換版本會變 |
| `ta_addr`、各物件位址 | 執行期位址 | addrof 當下讀 | **每次執行都不同** |

**兩個原則**：（1）**offset/map 綁死 commit**——換 V8 版本要重測（[Ch 26](./26-reading-v8-source-commits.md)/[Ch 27](./27-patch-diffing.md)）。（2）**位址每次執行不同、絕不寫死**——用 addrof 當下 leak。前幾章反覆強調的「教結構不背位址」，在 template 裡就落實成「位址一律 runtime leak」。

## Pointer compression 對整套原語的影響（講死）

這是本章的另一半重點。pointer compression（[Ch 4](./04-pointer-compression.md)）把 V8 堆塞進一個 4 GB 對齊的 **cage**，堆內指標只存 32-bit offset。這對你的 R/W 原語有三個決定性影響：

### 影響一：你的原語天生「壓縮世界」

`addrof` 讀出的、`fake_arr` 的 elements 欄位、fake object 的每個指標——**全是 32-bit 壓縮值**。所以你的 `read64`/`write64` 若透過 fake JSArray 的 elements 指標，那個指標是壓縮的 → **你只能瞄準 cage 內（4 GB heap 內）**。想讀寫 cage 外（libc、堆疊、backing store 實體）？fake array 的壓縮 elements 指標**根本表達不了 cage 外位址**。

### 影響二：cage 內 vs cage 外是兩個世界

```
   64-bit 虛擬位址空間
   ┌────────────────────────────────────────────────────┐
   │  ┌── cage（4 GB）──────────────┐                    │
   │  │  所有 JS 堆物件、on-heap TA  │  ← 你的壓縮原語能到 │
   │  └──────────────────────────────┘                    │
   │  cage 外：off-heap backing store、libc、stack、GOT   │
   │           ← 需要 64-bit 指標能力才碰得到              │
   └────────────────────────────────────────────────────┘
```

- **cage 內任意讀寫**：fake array / TypedArray（data_ptr 若是 cage 內 handle）給你的。夠你改 heap 上任何物件的欄位、洩漏 heap 佈局、偽造更多物件。
- **cage 外任意讀寫**：要碰 libc/stack/GOT/RCE 才需要。**sandbox on 時你沒有**——這正是 sandbox 的設計目的。

### 影響三：sandbox on/off 決定你停在哪

- **sandbox OFF**：TypedArray 的 data_ptr 是明碼 64-bit（[Ch 17](./17-typedarray-attack.md)）。你的 `aar`/`aaw` 一旦劫持它，**直接是進程級任意 R/W**——cage 內外通吃。經典路線，一步到位。
- **sandbox ON（現代 default）**：data_ptr 是 external pointer table 的 handle，你改不到指標實體。你的原語**卡在 cage 內**。要出 cage 得破 sandbox（[Ch 34](./34-v8-sandbox.md)/[Ch 35](./35-bypassing-v8-sandbox.md)）——攻 external pointer table、或找 cage 內就能 code exec 的路（[Ch 33](./33-wasm-rwx-jit-spray.md) 的 WASM RWX）。

**一句話總結**：pointer compression + sandbox 把「拿到 read64/write64」和「拿到進程控制」之間硬生生插了一道牆。sandbox off 沒這道牆（一路到底），sandbox on 有（cage 內 R/W 只是中場、後面還有 [Ch 34](./34-v8-sandbox.md) 整個 Part）。**你打一題前先確認它是 on 還是 off——這決定你的 exploit 有多長。**

## 對比：三種 build/場景下 template 到哪為止

| 場景 | OOB 來源 | R/W 上限 | 到 code exec 還缺 |
|---|---|---|---|
| CTF sandbox off | challenge patch | 進程級任意 R/W | 直接 [Ch 32](./32-arbitrary-rw-to-code-exec.md) 改 GOT/寫 shellcode |
| CTF sandbox on | challenge patch | cage 內任意 R/W | 破 sandbox（[Ch 34](./34-v8-sandbox.md)）+ [Ch 33](./33-wasm-rwx-jit-spray.md) |
| 真實 Chrome | Part 4 真 bug | cage 內任意 R/W | 破 sandbox + renderer sandbox escape（[Ch 39](./39-renderer-mojo-sandbox-escape.md)） |

**同一份 template，終點差很多。** CTF 常給 sandbox off 讓題目「短一點」，或給 sandbox on 逼你練完整鏈。看清你在哪一列，才知道拿到 read64/write64 後該翻到哪一章。

## 踩雷集錦

1. **錯誤直覺：「拿到 read64/write64 就贏了」。正確**：sandbox on 時那只是 **cage 內** R/W——中場而已。離 code exec 還隔著破 sandbox（[Ch 34](./34-v8-sandbox.md)）。只有 sandbox off 才是「read64/write64 = 快到終點」。先看 build。
2. **錯誤直覺：「template 的位址/offset 抄一次就通用」。正確**：**位址每次執行不同**（runtime leak）、**offset/map 綁 commit**（換版本重測）。template 抄的是**結構**，值一律現查。
3. **錯誤直覺：「fake array 的 read64 能讀 cage 外」。正確**：fake array 的 elements 指標是 **32-bit 壓縮值**，表達不了 cage 外位址，天生只能 cage 內。要 cage 外得靠別的（sandbox off 的明碼 data_ptr，或破 sandbox）。
4. **錯誤直覺：「addrof/fakeobj 每題都要重寫」。正確**：它們是 template 裡**幾乎不變**的部分。每題變的只有「OOB 來源」那一格。把 addrof/fakeobj/read64/write64 寫成你的固定模板，比賽時省下大把時間。
5. **錯誤直覺：「sandbox on/off 只影響最後一步」。正確**：它從 `read64`/`write64` 的**可達範圍**就開始影響——決定你的整條 exploit 是「一路到底」還是「cage 內中場 + 破 sandbox 下半場」。開賽前就要判斷。

## 進階：再往深一層

- **把 template 模組化**：資深 pwner 會把 `ftoi/itof/addrof/fakeobj/read64/write64/aar/aaw` 寫成一個可 `import` 的 prelude，每題只寫「觸發 bug + 建立 confusion」那段。維護一份你自己的 `v8_prelude.js`，隨版本更新 offset。
- **read/write 原語的品質分級**：不是所有 read64 一樣好用。理想是「任意位址、任意次數、不破壞其他狀態、cage 內外皆可」。實際常有限制（只能 cage 內、每次讀會擾動 controller、對齊要求）。評估你手上原語的品質，決定收尾策略。
- **cage 內能做的事比你想的多**：即使卡在 cage 內，你能改任何 heap 物件——包括改一個 JSFunction 的 code 指標指向 heap 上的 WASM RWX 頁（[Ch 33](./33-wasm-rwx-jit-spray.md)），這在 cage 內就能達成 code exec，**不一定要先破 sandbox**。所以「cage 內 R/W」離 RCE 未必很遠，看你走哪條收尾。
- **與 Part 4 的接點**：本章 template 的 [2] 空格，在 Part 4 會換成真的 TurboFan type confusion（[Ch 19](./19-turbofan-type-confusion.md)）。那些 bug 產出的往往正是「一個 OOB 的 double/object 陣列」或「一個型別被搞混的陣列」——**恰好接上本 template 的入口**。這就是為什麼 Part 3 先教原語、Part 4 再教怎麼生 OOB。
- **原始碼**：`src/common/ptr-compr-inl.h`（壓縮/解壓）、`src/objects/js-array-buffer.h`（data_ptr offset）、`src/sandbox/`（cage / external pointer table）。綁死 commit `ab2cad06`。

## 動手練習

1. **默寫 template**：不看本章，默寫從 `ftoi`/`itof` 到 `aar`/`aaw` 的完整骨架，標出每一段對應哪一章、哪些是常數/每題換/runtime leak。默不出來的段落回去補。這份骨架要能閉眼寫。
2. **畫 cage 邊界圖**：畫 64-bit 位址空間、cage、cage 內外各住什麼，標出「fake array 原語到哪」「sandbox off 的 TypedArray 到哪」「sandbox on 卡在哪」。再標「要碰 libc/stack 得跨過哪條線、靠什麼能力」。這張圖是你判斷「exploit 還要多長」的地圖。
3. **套 patch 跑完整鏈**（需 [Ch 14](./14-first-oob.md) patch）：在 sandbox off build 上，把 template 填完整（OOB 來源用 `Array.prototype.oob`），驗證 `aar(一個已知進程位址)` 讀到預期值。再到 sandbox on build 跑，確認同段卡在 cage 內。你剛跑完一條完整的 Part 3 主線，也親手驗證了 pointer compression 那道牆。

## 本章重點整理

- Part 3 的產出是一套**可重用 exploit template**：`ftoi`/`itof` → `addrof`/`fakeobj` → `read64`/`write64` → 劫持 TypedArray 的 `aar`/`aaw`。**每題只換「OOB 來源」一格**，其餘照抄。
- template 裡：IEEE754 工具永不變、addrof/fakeobj/read64/write64 幾乎不變、**offset/map 綁 commit**、**位址一律 runtime leak**（教結構不背值）。
- **pointer compression 把 R/W 切成 cage 內/外兩世界**：fake array 的壓縮 elements 指標天生只能 cage 內；要 cage 外（libc/stack/RCE）需 64-bit 指標能力。
- **sandbox off**：劫持明碼 data_ptr = 進程級 R/W，一路到底。**sandbox on（default）**：data_ptr 是 handle，卡 cage 內，出 cage 要破 sandbox（[Ch 34](./34-v8-sandbox.md)）。開賽前先判斷 on/off，它決定 exploit 有多長。
- 拿到 read64/write64 ≠ 贏：sandbox on 時是中場。但 cage 內也能達成 code exec（改 JSFunction code 指標指向 WASM RWX，[Ch 33](./33-wasm-rwx-jit-spray.md)），未必非破 sandbox 不可。

## 自我檢核

- [ ] 能默寫完整 template 並標出每段對應章節、常數/runtime/綁 commit 的分類
- [ ] 能說出「每題只換 OOB 來源」為什麼成立，及它如何接 Part 4 的真 bug
- [ ] 能畫 cage 邊界圖，指出 fake array / sandbox off TypedArray / sandbox on 各到哪
- [ ] 能解釋 pointer compression 為何讓 fake array 原語天生只在 cage 內
- [ ] 能判斷一題 sandbox on/off、以及這如何決定 exploit 剩下多長
- [ ] （面試題）「請描述從一個 OOB 到穩定任意讀寫的完整 template，並說明 pointer compression 與 V8 sandbox 各在哪一步限制了你的原語」能完整答出

## 延伸閱讀

- **[doar-e / Jeremy Fetiveau “A journey into a V8 exploit” 系列](https://doar-e.github.io/blog/)**
  - **這篇說什麼**：從 bug 到完整 exploit 的端到端流程，addrof/fakeobj/read64/write64 如何串成一條鏈——正是本章 template 的真實範本。
  - **讀哪裡**：primitive 建構與收尾整合段落。
  - **關聯**：把本章骨架對照一份真 exploit 的完整程式碼組織。
- **[“The V8 Sandbox” — v8.dev/blog/sandbox](https://v8.dev/blog/sandbox)**
  - **這篇說什麼**：cage 與 external pointer table 如何把 R/W 鎖在 cage 內——本章「pointer compression 那道牆」的官方說明。
  - **讀哪裡**：sandbox 邊界與 threat model 段落。
  - **關聯**：本章 cage 內/外分界的權威來源，直通 [Ch 34](./34-v8-sandbox.md)。
- **[saelo / faraz.faith 的 V8 exploit template 開源程式碼](https://faraz.faith/)**
  - **這篇說什麼**：可直接參考的 `addrof/fakeobj/read/write` prelude 寫法（含 pointer compression 下的 offset 處理）。
  - **讀哪裡**：prelude / helper 那幾個函式。
  - **關聯**：把本章骨架落實成可維護的個人模板，練習 B 直接用得上。

Part 3 的原語鏈到此完整。理論骨架都有了，該用一顆真的（模擬 challenge patch 的）有 OOB 的 d8 親手打一遍——下一個檔是練習 B，你會從一個給定 OOB 做出 addrof/fakeobj 與 read64/write64，把這整章的 template 變成你自己的肌肉記憶。

→ [練習 B — 從 OOB 到任意讀寫](./practice-b-oob-to-rw.md)
