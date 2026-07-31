# Ch 34 — V8 Sandbox：ubercage / external pointer table

> **目標**：正面解剖現代 V8 利用最重要的防禦——**V8 Sandbox（ubercage）**。理解它的威脅模型（假設你已經有 heap 內任意讀寫）、它怎麼用 **pointer compression cage** + **external pointer table** 把「cage 內 RW」和「進程級 RW」隔開，以及這對你 Part 3 那套 addrof/fakeobj/任意讀寫的**具體限制**。這是全課「現代 vs 古代」的分水嶺章。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`（sandbox on）與 `out/x64.release.nosbx/d8`（off）。本章的 TypedArray external pointer 佈局用兩個 build 的 `%DebugPrint` 真跑對照。

## 為什麼需要這個？

你在 Part 3 學的「偽造 TypedArray、控制 backing store 指標、讀寫整個進程」——那是**古代**。在現代開了 sandbox 的 V8 上，那個 backing store 指標**不再是你能隨便改成任意值的裸指標**。如果你不懂 sandbox 改了什麼，你會照 Part 3 做出一個「任意讀寫」，然後困惑為什麼它只能碰到 4GB 的一小塊、碰不到你要的 libc 或 code 頁。這一章解釋那道牆，也是後面 [Ch 35](./35-bypassing-v8-sandbox.md)（攻擊 sandbox）、[Ch 36](./36-cfi-cet-data-only.md)（data-only）的前提。

## 先建立直覺：先假設小偷會進屋

傳統防禦的思路是「別讓小偷進屋」（別讓攻擊者拿到 RW）。V8 團隊看清一個殘酷現實（[Ch 24](./24-jit-side-effect.md)）：JIT type confusion **擋不完**，小偷遲早會進屋（拿到 heap 內 RW）。

於是 V8 Sandbox 換了思路：**假設小偷一定會進到「客廳」（V8 heap），那就把值錢的東西（能打穿整個進程的指標、code 指標、系統資源 handle）全部鎖進「保險庫」（cage 外），客廳裡放的都是搬不走、也開不了保險庫的東西。** 小偷在客廳能翻箱倒櫃（heap 內任意讀寫），但摸不到保險庫。

```
   ┌──────────── V8 Sandbox cage（4GB 客廳）────────────┐
   │  所有 JS 物件、陣列、字串…都在這                     │
   │  攻擊者的「任意讀寫」= 只能在這 4GB 內橫行            │
   │  堆內指標都是 32-bit 壓縮的（出不了 cage）           │
   │                                                     │
   │   TypedArray.backing_store ─── handle ──┐           │
   └────────────────────────────────────────┼───────────┘
                                             │（間接）
   ┌── cage 外（保險庫，攻擊者的 cage 內 RW 碰不到）──────┐
   │   External Pointer Table：handle → 真實指標 + type   │
   │   Code pointer table、trusted space、真正的         │
   │   backing store 位址、系統資源…                      │
   └─────────────────────────────────────────────────────┘
```

## 底層機制一：cage 把堆內指標壓成出不了門的 32-bit

[Ch 4](./04-pointer-compression.md) 的 pointer compression 是 sandbox 的地基。整個 V8 heap 被放進一個對齊的 **4GB cage**，所有堆內 tagged 指標只存 **32-bit 壓縮偏移**（相對 cage base）。關鍵後果：

- 一個堆內指標欄位**在物理上只有 32 bit 能表達**——它最多指到 cage 內的 4GB，**無法表達 cage 外的位址**。
- 你用任意寫蓋一個物件指標欄位，蓋進去的值再大也只是 cage 內偏移。**你沒辦法讓一個堆內指標指到 cage 外。**

光這一點就把「堆內 RW → 直接指向 libc/任意進程位址」封死了。但 TypedArray 的 backing store 需要指向 cage 外的真實記憶體（大 buffer 不放堆內），怎麼辦？這就要第二個機制。

## 底層機制二：external pointer table（handle 間接）

凡是需要「指向 cage 外」的指標（TypedArray 的 backing store、embedder 物件、系統資源…），V8 不把裸指標存在堆內，而是：

1. 真實指標存在 **cage 外**的一張 **External Pointer Table（EPT）**裡，每個 entry 帶一個 **type tag**。
2. 堆內物件只存一個 **handle**——EPT 的索引（外加 tag 位元）。
3. 用的時候，V8 拿 handle 去 EPT 查出真實指標，並**驗證 type tag**。

親眼對照兩個 build 的 TypedArray（真跑）：

```
# sandbox build
$ d8 --allow-natives-syntax -e 'let ta=new Uint8Array(16); %DebugPrint(ta);'
 - data_ptr: 0x20770104b0c0
   - base_pointer: 0x104b0b9
   - external_pointer: 0x207700000007    ← handle（cage_base + 小索引/tag），不是真指標

# no-sandbox build（對照）
$ d8n --allow-natives-syntax -e 'let ta=new Uint8Array(16); %DebugPrint(ta);'
 - data_ptr: 0x14f70108b0d4
   - external_pointer: 0x14f700000007     ← 同樣經 handle（此小陣列資料在堆內）
```

（這個 16-byte 小陣列的資料其實放在堆內 `base_pointer`；`external_pointer` 欄位存的是 handle 形式。對大的 off-heap buffer，真正的 backing store 位址就存在 EPT 裡，堆內只有 handle。）

**對攻擊者的致命限制**：你用堆內任意寫想改 backing store 指向任意位址時，你能改的只是**堆內的 handle 欄位**——你把它改成別的值，V8 拿去查 EPT，要嘛落在 EPT 的別的 entry（那也是個受控的、合法的指標），要嘛 type tag 對不上被擋。**你改不到 EPT 本身（它在 cage 外），也就無法憑空塞一個「指向 cage 外任意位址」的指標。** Part 3 那招「把 backing store 改成 libc 位址讀寫整個進程」就這樣被斬斷。

## 底層機制三：code / trusted pointer 也間接化

同樣的邏輯套到最敏感的指標：

- **Code pointer table**：JIT code 的 entry 指標經專門的 table 間接，cage 內寫不到，也無法偽造指向任意 code。
- **Trusted space**：一些「絕不能被攻擊者控制」的物件放進 cage 內但受保護的區域（或 cage 外），例如 WASM 的某些 metadata。

這封死了 [Ch 32](./32-arbitrary-rw-to-code-exec.md)、[Ch 33](./33-wasm-rwx-jit-spray.md) 講的「覆寫 code 指標 / 跳進 RWX」的經典 code-exec 路。

## sandbox 之後，你的「任意讀寫」變成什麼

把限制講清楚——開了 sandbox，Part 3 做出的原語**降級**成：

| 原語 | sandbox 前 | sandbox 後 |
|---|---|---|
| addrof/fakeobj | 完整功能 | 仍可用（cage 內操作） |
| fake TypedArray 任意讀寫 | **進程級**（整個位址空間） | **只限 cage 內**（4GB heap） |
| 改 backing store 指向 libc | ✅ | ❌（handle 間接 + type tag） |
| 覆寫 code 指標劫持控制流 | ✅ | ❌（code pointer table） |

**你仍然能在 cage 內為所欲為**——讀寫任何 JS 物件、偽造物件、破壞 metadata。但要打穿到 cage 外（真正的 code exec、碰系統資源），你得攻擊 sandbox 本身（[Ch 35](./35-bypassing-v8-sandbox.md)）或走 data-only（[Ch 36](./36-cfi-cet-data-only.md)）。

## 對比：V8 Sandbox vs 傳統進程沙盒

| 面向 | 傳統 renderer 沙盒（[Ch 1](./01-why-renderer-attack-surface.md)） | V8 Sandbox（本章） |
|---|---|---|
| 邊界 | 進程邊界（seccomp/namespace） | 進程**內**的記憶體區域（cage） |
| 防什麼 | renderer 逃到 OS / 別的進程 | heap 內 RW 逃到 cage 外的進程記憶體 |
| 攻擊者位置 | 已在 renderer 內 code exec | 已在 heap 內任意讀寫 |
| 下一步 | sandbox escape（Mojo，[Ch 39](./39-renderer-mojo-sandbox-escape.md)） | sandbox escape（EPT，[Ch 35](./35-bypassing-v8-sandbox.md)） |

注意這是**兩層不同的 sandbox**，名字都叫 sandbox 但層級完全不同：V8 Sandbox 在 renderer 進程**內部**，renderer 沙盒是 renderer 進程**外部**。一個完整 chain 要穿過**兩層**。別搞混。

## 踩雷集錦

1. **把「V8 Sandbox」和「renderer 進程沙盒」搞混**：前者是進程內的記憶體 cage，後者是進程級的 seccomp/namespace。名字一樣，層級不同，一個 full chain 要各破一次。
2. **以為 sandbox 修了 type confusion**：完全沒有。sandbox 不管 bug，它管「bug 之後你能做多少壞事」。type confusion 照樣觸發、照樣給你 cage 內 RW。
3. **照 Part 3 做出 RW 卻想讀 libc**：sandbox 下 backing store 是 handle，你的 RW 出不了 cage。看到舊 exploit 直接改 backing store 指向任意位址，那是無 sandbox 年代的。
4. **以為 32-bit 壓縮指標「只是效能優化」**：它同時是 sandbox 的地基——32 bit 物理上就無法表達 cage 外位址。壓縮和隔離是一體兩面。
5. **忽略 type tag**：EPT entry 帶 type tag，你就算把 handle 改到別的 entry，型別對不上也會被擋。以為「改 handle 就能亂指」會撞牆。

## 進階：再往深一層

- **EPT 的實際結構**：讀 `src/sandbox/external-pointer-table.h`——entry 怎麼編碼真實指標 + tag、怎麼做 mark-sweep（EPT 也要 GC）、handle 的位元佈局。這是 [Ch 35](./35-bypassing-v8-sandbox.md) 攻擊它的前提。
- **sandbox 的邊界不是完美的**：不是所有指標都已間接化——sandbox 是漸進部署的，總有「還沒進 cage」的角落。找那些角落是攻擊面研究的一支。
- **`--sandbox-testing` 與 sandbox violation 偵測**：V8 有機制在 debug 下偵測「試圖用 cage 外指標」的行為。研究時可用來確認你的原語是否真的被 sandbox 擋。
- **cage 保留的巨大虛擬位址空間**：cage 是一塊對齊的、保留的大虛擬位址區。理解它的配置（`src/sandbox/`）有助於你判斷哪些位址在 cage 內、哪些在外。
- **與硬體的關係**：cage 的隔離是純軟體（靠 32-bit 表達限制），不像 CET 靠硬體。這是它相對輕量、但也不是不可繞的原因。

## 動手練習

1. 對照兩個 build 跑 `%DebugPrint` 一個**大**的 TypedArray（例如 `new Uint8Array(0x10000)`，資料 off-heap），比較 sandbox / nosbx 的 `external_pointer` 與 `data_ptr`。看 sandbox build 的 backing store 是不是經 handle。
2. 用 `%DebugPrint` 觀察同一物件的堆內指標欄位（map、elements），確認它們都是 `0x....` 這種 cage 內的壓縮值。想一想：你用任意寫能把它改到 cage 外嗎？（不能——只有 32 bit。）
3. 讀 V8 sandbox 的 high-level design（延伸閱讀），畫出「一個 handle 從堆內欄位 → EPT → 真實 cage 外指標」的查表流程，標出攻擊者在每一步能/不能控制什麼。

## 本章重點整理

- **V8 Sandbox（ubercage）**的威脅模型：**假設攻擊者已有 heap 內任意讀寫**，目標是限制「cage 內 RW 能造成的傷害」。
- 三個機制：**cage**（堆內指標只有 32-bit 壓縮，出不了 4GB）、**external pointer table**（cage 外指標經 handle + type tag 間接）、**code/trusted pointer table**（code 指標間接化）。
- 現行 d8 可見：sandbox build 的 TypedArray `external_pointer` 是 handle（`0x...0007`），改它改不到真實指標。
- 後果：Part 3 的任意讀寫**降級為 cage 內**；改 backing store 指向 libc、覆寫 code 指標都被斬斷。
- V8 Sandbox（進程內記憶體 cage）與 renderer 進程沙盒（seccomp/namespace）是**兩層**，full chain 要各破一次。

## 自我檢核

- [ ] 能解釋 V8 Sandbox 的威脅模型為什麼「先假設攻擊者有 RW」
- [ ] 能說出 cage + EPT + code pointer table 各自封死了哪個經典手法
- [ ] 能用 `%DebugPrint` 指認 sandbox build 的 backing store 是 handle
- [ ] 能清楚區分 V8 Sandbox 和 renderer 進程沙盒（層級、防什麼）
- [ ] 面試被問「V8 Sandbox 是什麼、擋住了什麼」，能用「假設有 RW，把危險指標間接化限制傷害」回答

## 延伸閱讀

- **[“V8 Sandbox” 官方設計文件 — v8.dev/blog/sandbox 與 `src/sandbox/`](https://v8.dev/blog/sandbox)**
  - **這篇說什麼**：sandbox 的威脅模型、cage、external pointer table 的第一手設計說明。
  - **讀哪裡**：威脅模型 + external pointer table 段落。本章就是它的利用視角導讀。
  - **和本章的關聯**：權威來源，讀它把細節補齊。

- **[V8 `src/sandbox/external-pointer-table.h` / `sandbox.h`（原始碼）](https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/sandbox/)**
  - **讀哪裡**：EPT entry 編碼、handle 位元佈局、type tag。
  - **和本章的關聯**：這是 EPT 在原始碼裡的樣子，也是 [Ch 35](./35-bypassing-v8-sandbox.md) 攻擊它的地圖。

- **[Project Zero / 研究者對 V8 Sandbox 的攻擊面分析](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：sandbox 的弱點、尚未間接化的角落、對 exploit 開發的實際衝擊。
  - **和本章的關聯**：預告 [Ch 35](./35-bypassing-v8-sandbox.md)，看攻擊者怎麼面對這道牆。

牆看清楚了。下一章談攻擊者怎麼面對它——在 cage 內作業、以及攻擊 sandbox 本身（external pointer table）逃出 cage 的思路。

→ [Ch 35 — 繞過 / 在 V8 sandbox 內作業](./35-bypassing-v8-sandbox.md)
