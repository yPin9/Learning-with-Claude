# Ch 32 — 任意 R/W 到 code execution

> **目標**：你已經有了任意讀寫（Part 3 的 read64/write64）。這一章回答「然後呢」——怎麼把「能讀能寫任意記憶體」升級成「執行我的機器碼」。看清經典手法（WASM RWX、覆寫 JIT code、劫持函式指標），以及 **V8 Sandbox 出現後這些路為什麼大多被斬斷**，逼現代 exploit 走向 data-only 與 sandbox escape。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`。本章多為利用手法與架構分析；完整 code-exec exploit 需 patched/vulnerable build，標「未實測，理論預期」，但關鍵前提（sandbox 下 backing store 經 external pointer table 間接）用現行 d8 的 `%DebugPrint` 驗證。

## 為什麼需要這個？

在 `binary_exploitation`，拿到任意寫之後你覆寫一個 GOT entry 或 `__free_hook` 就 game over。V8 沒有這麼便宜的事——尤其 sandbox 開著時。這一章是「拿到 RW 之後」的地圖，也是理解「為什麼現代 V8 exploit 這麼長」的關鍵：**任意讀寫不再等於贏**，中間隔著一整套針對「RW → code exec」的防禦。

## 先建立直覺：有了鑰匙，門卻換了

任意讀寫像是一把能打開任何抽屜的萬能鑰匙。傳統上，你用它打開「放函式指標的抽屜」，把裡面換成你的位址，等程式呼叫那個函式時就跳到你的碼。

但 V8（和現代作業系統）把「值錢的抽屜」都做了特殊處理：

- **可執行的記憶體（JIT code）和可寫的記憶體分開**（W^X：一塊記憶體不能同時可寫又可執行）。你能寫的地方不能執行，能執行的地方不能寫。
- **重要的指標（backing store、code entry）被間接化或加密**，你就算蓋了也跳不到你要的地方。

所以有了萬能鑰匙，你還得先搞清楚「哪個抽屜現在還值錢」。這一章就是在盤點這些抽屜，以及哪些被鎖死了。

## 經典手法（sandbox 前的黃金時代）

### 手法一：WebAssembly RWX 頁（[Ch 33](./33-wasm-rwx-jit-spray.md) 詳談）

曾經最愛用的一招：WebAssembly 的 JIT 出來的機器碼放在一塊 **RWX（可讀可寫可執行）** 的記憶體。你只要：

1. 建一個 WASM instance，讓 V8 配置一塊 RWX 頁、放進編好的 WASM code。
2. 用任意讀找到那塊 RWX 頁的位址。
3. 用任意寫把你的 shellcode 覆蓋上去。
4. 呼叫對應的 WASM 匯出函式 → 執行你的 shellcode。

乾淨俐落，因為 RWX 頁同時可寫（你能蓋）又可執行（跳進去就跑）。**這招現在大多死了**（見下），但它是理解「為什麼要 W^X」的最佳反例。

### 手法二：覆寫 JIT 過的函式 code entry

一個被 TurboFan 優化過的 JS 函式，其物件裡有個指標指向它的機器碼。任意寫改這個指標指向你控制的資料（先用別的手段讓那塊資料可執行），呼叫該函式時就跳過去。

### 手法三：劫持 vtable / 內建函式指標

V8 內部有各種函式指標表。任意寫蓋掉一個會被呼叫的指標，接管控制流。

這三招的共同前提：**「能寫的東西」和「能執行/會被當程式碼跳過去的東西」之間沒有隔離**。

## V8 Sandbox 怎麼斬斷這些路

> 前提用現行 d8 驗證：sandbox build 下 TypedArray 的 backing store 指標是間接的。

V8 Sandbox（ubercage，[Ch 34](./34-v8-sandbox.md) 專章）的核心思想：**假設攻擊者遲早會拿到 heap 內任意讀寫，那就限制「heap 內 RW 能造成的傷害」**。具體做法直接打擊上面的手法：

- **backing store / external pointer 間接化**：TypedArray 的 backing store 不再是一個裸指標，而是一個 **handle**（索引進 external pointer table）。看現行 sandbox build：

```
$ d8 --allow-natives-syntax -e 'let ta=new Uint8Array(16); %DebugPrint(ta);'
 - data_ptr: 0x20770104b0c0
   - base_pointer: 0x104b0b9
   - external_pointer: 0x207700000007   ← 不是真指標，是 external pointer table 的 handle
```

`external_pointer: 0x...00000007` 不是一個真實記憶體位址，而是一個 **tagged handle**——真正的指標存在 cage 外的 external pointer table 裡，由 V8 間接查表。你在 cage 內用任意寫**改不到那張表**（它在 cage 外），也就無法把 backing store 指向 cage 外的任意位址。這一刀砍掉「用 fake TypedArray 讀寫整個進程」的經典路——你的 RW 被關在 cage 內。

- **code pointer 也間接化 / sandbox 外**：JIT code 的指標、重要函式指標被移出 cage 或經 trusted space 管理，cage 內的寫改不到。
- **W^X 強制**：可寫和可執行分離，WASM RWX 頁的年代結束。

結果：**在 cage 內的任意讀寫，變成「只能讀寫 V8 heap 內的 JS 物件」**。要 code exec，你得額外：(a) 攻擊 sandbox 本身（[Ch 35](./35-bypassing-v8-sandbox.md)），或 (b) 走 data-only（[Ch 36](./36-cfi-cet-data-only.md)），或 (c) 找 sandbox 尚未覆蓋的角落。

## 現代路線圖：cage 內 RW 之後怎麼辦

```
   任意讀寫（cage 內）
        │
        ├─(a) 攻擊 V8 Sandbox 本身 ──► 打 external pointer table / 找 sandbox bug
        │        （Ch 35）              → 逃出 cage → 真正的進程級 RW → code exec
        │
        ├─(b) data-only attack ──────► 不劫持控制流，只改「資料」達成目的
        │        （Ch 36）              （改權限旗標、改 JIT 常數、污染物件…）
        │
        └─(c) sandbox 未覆蓋處 ──────► 尚未間接化的指標、trusted space 的縫隙
```

這也是為什麼 [Ch 24](./24-jit-side-effect.md) 說「一個 type confusion 不再等於贏」——你打完 Part 3/4 拿到 cage 內 RW，才走到這張圖的**起點**。

## 對比：sandbox 前後的「RW → code exec」

| 手法 | sandbox 前 | sandbox 後（現代） |
|---|---|---|
| WASM RWX 頁 | ✅ 首選 | ❌ W^X + code 間接化，基本死 |
| 覆寫 JIT code entry | ✅ | ❌ code pointer 移出 cage |
| fake TypedArray 打整個進程 | ✅ | ⚠️ 只能在 cage 內（backing store 是 handle） |
| data-only（改資料達成目的） | ✅（較少用） | ✅ **主流之一** |
| 攻擊 sandbox 本身 | 不需要 | ✅ **必經之路** |

看懂這張表，你就懂了現代 V8 exploit 為什麼分兩大段：**「拿到 cage 內 RW」**（Part 3/4）和**「從 cage 內 RW 到真正 code exec」**（Part 6 這裡）。

## 踩雷集錦

1. **以為任意讀寫就結束了**：這是從 userland pwn 帶來的最大慣性。在現代 V8，cage 內任意讀寫只是**中場**，不是終場。
2. **照抄舊 writeup 的 WASM RWX 手法**：2019 年的 exploit 用 WASM RWX 頁一步 code exec，現在多半失效（W^X、code 間接化）。看到這招先確認目標 V8 版本和 sandbox 狀態。
3. **搞混「cage 內 RW」和「進程級 RW」**：sandbox 開著時，你的 fake TypedArray 只能碰 cage 內的 4GB。以為能讀寫整個進程記憶體會撞牆。
4. **以為 W^X 只是作業系統的事**：V8 自己也強制 code/data 分離（trusted space、code space），不只靠 OS 的頁權限。
5. **忽略 data-only 的威力**：以為「不劫持控制流就打不了」。錯。改對一個資料（一個長度、一個權限旗標、一個 JIT 產生的常數）一樣能達成目的，且繞過所有 CFI（[Ch 36](./36-cfi-cet-data-only.md)）。

## 進階：再往深一層

- **external pointer table 的結構**：cage 外的這張表把 handle 映射到真實指標，並帶 type tag 防止跨型別濫用。[Ch 34](./34-v8-sandbox.md) 會拆它，[Ch 35](./35-bypassing-v8-sandbox.md) 談怎麼攻擊它。
- **code pointer sandboxing / trusted space**：V8 把可信的指標（code entry 等）放進一個 cage 內攻擊者寫不到的區域。追 V8 的 "code pointer sandboxing" 相關 commit 看演進。
- **leaking a stable base**：就算走 data-only，你通常還是需要一些位址（cage base、某物件位址）。GC 會搬物件（[Ch 13](./13-garbage-collection.md)），所以要即時 leak。
- **WASM 在 sandbox 時代的殘餘價值**：WASM 仍是有用的原語來源（可控的記憶體、函式表），只是「RWX 一步登天」沒了。[Ch 33](./33-wasm-rwx-jit-spray.md) 談它的現況。

## 動手練習

1. 在現行 sandbox build 用 `%DebugPrint` 看 TypedArray 的 `external_pointer`，確認它是 `0x...0007` 這種 handle 而非真實位址。再到 `out/x64.release.nosbx` 看同一個——比較兩者，理解 sandbox 把 backing store 間接化的效果。
2. 讀一篇 2018–2019 的 V8 WASM RWX exploit（延伸閱讀），找出它「找到 RWX 頁 → 覆寫 shellcode → 呼叫」的三步，然後對照本章說明「這三步現在各被哪個防禦擋住」。
3. 思考題（面試）：為什麼 V8 選擇「假設攻擊者會拿到 RW，限制傷害」而不是「努力防止所有 type confusion」？（提示：[Ch 24](./24-jit-side-effect.md) 的「effect 建模組合爆炸」——擋不完，只好限制後果。）

## 本章重點整理

- 任意讀寫到 code exec 的經典手法（WASM RWX、覆寫 JIT code、劫持函式指標）都依賴「可寫」和「可執行/會被跳過去」之間**沒有隔離**。
- **V8 Sandbox** 直接打擊這些：backing store/code pointer 間接化（handle 進 external pointer table）、W^X 強制——**cage 內任意讀寫只能碰 cage 內的 JS 物件**。
- 現行 d8 可見：sandbox build 的 TypedArray `external_pointer` 是 handle（`0x...0007`），不是真指標。
- 現代路線：cage 內 RW 之後要 (a) 攻擊 sandbox 本身、(b) data-only、或 (c) 找未覆蓋角落——「RW 不再等於贏」。
- 這是現代 V8 exploit 分成「拿 cage 內 RW」和「從 RW 到 code exec」兩大段的原因。

## 自我檢核

- [ ] 能說出三種經典「RW → code exec」手法及各自的前提
- [ ] 能解釋 W^X 和「code/data 分離」怎麼擋掉 WASM RWX 這一招
- [ ] 能用 `%DebugPrint` 指出 sandbox build 的 backing store 是間接的 handle
- [ ] 能畫出 cage 內 RW 之後的三條路線
- [ ] 面試被問「V8 拿到任意讀寫之後為什麼還很難 code exec」，能用 sandbox 的間接化 + W^X 回答

## 延伸閱讀

- **[“V8 Sandbox — High-Level Design” — V8 docs / v8.dev](https://v8.dev/blog/sandbox)** 及 [security/sandbox 設計文件](https://chromium.googlesource.com/v8/v8/+/main/src/sandbox/)
  - **這篇說什麼**：sandbox 為什麼假設「攻擊者會拿到 RW」、怎麼間接化指標限制傷害。
  - **讀哪裡**：external pointer table 與威脅模型段落。是本章「為什麼經典手法死了」的權威依據。
  - **和本章的關聯**：[Ch 34](./34-v8-sandbox.md) 會深拆，這裡先讀 high-level。

- **[各家 2018–2019 V8 WASM RWX exploit writeup（doar-e / saelo）](https://doar-e.github.io/)**
  - **這篇說什麼**：經典「任意寫 → WASM RWX → shellcode」的完整示範。
  - **為什麼值得讀**：親眼看黃金時代的一步登天，才體會現代為什麼要繞這麼多。讀時對照本章「這招現在被什麼擋住」。

- **[“The V8 Heap Sandbox” 相關 Project Zero / 研究者分析](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：sandbox 對 exploit 開發實務的衝擊、攻擊者的因應。
  - **和本章的關聯**：印證「RW 不再等於贏」，並預告 [Ch 35](./35-bypassing-v8-sandbox.md) 的攻擊面。

拿到 RW 之後的地圖畫好了。下一章先回頭把「WASM RWX」這個經典手法的來龍去脈與它的消亡講清楚——它是理解 W^X 與現代 code-exec 難度的最佳教材。

→ [Ch 33 — WebAssembly RWX / JIT spray 與消亡史](./33-wasm-rwx-jit-spray.md)
