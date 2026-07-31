# Ch 35 — 繞過 / 在 V8 sandbox 內作業

> **目標**：面對上一章那道牆，攻擊者怎麼辦。理解三條現代路線——**在 cage 內把能做的做到極致**、**攻擊 external pointer table / sandbox 尚未覆蓋的角落**、以及認清 **V8 Sandbox 的定位**（它是縱深防禦、還在成熟中，不是萬里長城）。這章給你的是「拿到 cage 內 RW 之後往哪走」的實戰思路，不是一鍵繞過。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`。本章為攻擊面分析與思路；具體 sandbox escape exploit 針對特定 V8 版本、高度版本相依，標「未實測，理論預期」，並指向公開研究。

## 為什麼需要這個？

[Ch 34](./34-v8-sandbox.md) 說「你的 RW 被關在 cage 內」。這聽起來像死路，但現代 V8 exploit 每天都在越過它。這一章解釋他們怎麼做——不是因為 sandbox 有某個萬用後門，而是因為 **sandbox 是一道還在施工的牆，且「cage 內能做的事」比你想的多**。

## 先建立直覺：牆很高，但不是每塊磚都砌好了

V8 Sandbox 不是一個「完成品」的安全邊界。V8 團隊自己講得很清楚：它是**縱深防禦（defense in depth）、仍在成熟中**的機制。這意味著兩件事：

1. **牆有缺口**：不是所有「指向 cage 外」的指標都已間接化。sandbox 是**漸進部署**的，總有還沒被 cage 化的舊角落、新加的功能、embedder 的介面。找到一個沒砌好的磚，就能把 cage 內 RW 放大成 cage 外 RW。
2. **牆內空間很大**：cage 內有整個 V8 heap——所有物件、所有 JS 可達的狀態。光在牆內「改對一個資料」，很多時候就已經達成攻擊目的（data-only，[Ch 36](./36-cfi-cet-data-only.md)），不一定非得出牆。

## 路線一：在 cage 內作業（data-only）

先問一個問題：**你真的需要出 cage 嗎？** 很多攻擊目標在 cage 內就能達成：

- **偽造/破壞 JS 物件的 metadata**：改一個物件的 map、改一個陣列的 length、污染一個內建的欄位——這些都在 cage 內，你的 RW 完全夠用。
- **改「資料」而非劫持控制流**：例如把一個「權限檢查用的布林」改成 true、把 WASM 的某個 metadata 改掉、污染 JIT 依賴的常數。
- **在 renderer 內的目標**：如果你的最終目的是「在 renderer 內讀某個站台的資料」（[Ch 1](./01-why-renderer-attack-surface.md)），cage 內的 RW 加上對 V8 物件的掌控可能就夠——你不一定需要進程級 code exec。

這條路的精神：**sandbox 限制的是「打穿進程」，不是「控制 V8」**。先看清你到底需不需要打穿。[Ch 36](./36-cfi-cet-data-only.md) 專門講 data-only。

## 路線二：攻擊 external pointer table / sandbox 缺口

如果你確實需要出 cage（真正的 code exec、碰系統資源），思路是**攻擊 sandbox 機制本身**：

### 2a. 找尚未 cage 化的指標

sandbox 漸進部署，總有指標還沒間接化。研究方法：對 V8 的物件做全面盤點，找「還是裸指標、指向 cage 外、且 JS 可觸及」的欄位。這種欄位一旦被你的 cage 內 RW 蓋到，就直接給你 cage 外 RW。這類缺口會隨版本被逐一補上，所以**高度版本相依**——這也是為什麼 sandbox escape exploit 綁死特定 commit。

### 2b. 攻擊 EPT 的一致性

external pointer table 本身是個資料結構（在 cage 外），但**管理它的邏輯**（配置 entry、GC 掃描、handle 驗證）在 cage 內有部分狀態。若能製造 EPT 的狀態不一致（例如 handle 混淆、entry 型別混淆、use-after-free 一個 EPT entry），可能繞過 type tag 檢查，讓一個 handle 解出你想要的指標。這是研究者持續探索的方向。

### 2c. 攻擊 sandbox 外但 handle 可達的物件

有些 cage 外的結構透過 handle 從 cage 內可達。如果那些結構的處理邏輯有記憶體 bug，你可能用「合法的 handle 操作」觸發 cage 外的破壞。

## 路線三：認清定位——它是縱深防禦，不是唯一防線

一個關鍵的認識論校準：**V8 Sandbox 目前的官方定位是「縱深防禦」而非「硬安全邊界」**。V8 團隊為它跑 bug bounty、持續補洞，但也明說它還在成熟。實務意義：

- 一個「V8 sandbox bypass」在完整 exploit chain 裡是**一個環節**，不是不可逾越的終點。
- 真實的 full chain（[Ch 39](./39-renderer-mojo-sandbox-escape.md)）通常還是靠 **renderer 進程沙盒逃逸（Mojo）**當主要的「出 renderer」手段，V8 sandbox 只是讓「從 V8 bug 到 renderer 內 code exec」這一段變難。
- 換句話說：V8 Sandbox 提高了「從一個 type confusion 到 renderer 內 RCE」的成本，但它上面還有一層真正的進程沙盒要處理。

## 對比：三條路線怎麼選

| 你的目標 | 該走哪條 | 依賴 |
|---|---|---|
| 在 renderer 內讀資料 / 達成邏輯目的 | 路線一（cage 內 data-only） | 通常夠用，最穩 |
| 真正的進程級 code exec | 路線二（攻擊 sandbox） | 高度版本相依，需 sandbox bug |
| 完整 full chain | 路線一/二 + renderer sandbox escape | 見 [Ch 39](./39-renderer-mojo-sandbox-escape.md) |

新手的直覺常是「一定要打穿到 shell」，但**很多時候路線一就達成目的了**。先問「我到底需要什麼」，別為了 code exec 而 code exec。

## 踩雷集錦

1. **以為 sandbox 有一個萬用 bypass**：沒有。繞過靠的是「特定版本的特定缺口」或「攻擊目的其實不需要出 cage」。看到號稱通用 bypass 要存疑。
2. **忽略「cage 內就夠」的可能**：很多人拿到 cage 內 RW 就急著找出 cage 的路，卻沒問「我需要出去嗎」。data-only（路線一）常常直接達陣。
3. **把 sandbox escape exploit 當通用**：它綁死特定 commit（缺口會被補）。照抄別版本的 sandbox bypass 幾乎必失敗。
4. **混淆 V8 sandbox escape 和 renderer sandbox escape**：前者出 cage（進程內），後者出 renderer 進程（Mojo，[Ch 39](./39-renderer-mojo-sandbox-escape.md)）。full chain 兩者都要，別當成一回事。
5. **低估 type tag 的攔阻**：以為改個 handle 就能亂指。EPT 的 type tag 會擋跨型別濫用，繞它需要製造一致性錯誤，不是改個數字。

## 進階：再往深一層

- **EPT 的 GC 與 handle 生命週期**：external pointer table 也要 GC（回收沒用的 entry）。handle 的配置/回收時機是製造 UAF/混淆的潛在點。讀 `src/sandbox/external-pointer-table.cc`。
- **追 sandbox 的補洞 commit**：在 V8 repo 搜 `sandbox` 相關的 security commit，能看到「哪些指標最近才被 cage 化」——那些就是不久前還可用的缺口，也預示還沒補的類似角落。
- **trusted space 的邊界**：哪些物件被放進 trusted space、哪些還在一般 cage，邊界處是研究重點。
- **embedder 介面**：Chrome（embedder）和 V8 之間傳遞的物件/指標是否都正確 cage 化，是一個容易被忽略的攻擊面。
- **sandbox bug bounty 的公開報告**：V8 為 sandbox bypass 設了獎勵，公開的報告是學習「缺口長什麼樣」的一手材料。

## 動手練習

1. 盤點練習：在現行 d8 挑幾種物件（TypedArray、WASM instance、DataView、String），用 `%DebugPrint` 看它們有哪些指標欄位，判斷哪些是 cage 內壓縮值、哪些是 external（handle）。培養「哪裡可能有沒 cage 化的裸指標」的嗅覺。
2. 讀 V8 sandbox 的 threat model 文件（延伸閱讀），找出官方明列的「已知不在保護範圍內」的東西——那些就是路線二的起點。
3. 思考題（面試）：為什麼「data-only 在 cage 內達成目的」常常比「攻擊 sandbox 出 cage」更可靠？（提示：版本相依 vs 通用、需不需要額外 bug。）

## 本章重點整理

- V8 Sandbox 是**縱深防禦、仍在成熟**，不是完美邊界——有漸進部署的缺口、cage 內空間也很大。
- 三條路線：**cage 內 data-only**（常常就夠，最穩）、**攻擊 EPT / 未 cage 化的指標**（出 cage，但高度版本相依）、認清它**上面還有 renderer 進程沙盒**。
- sandbox escape **沒有萬用 bypass**，靠特定版本的特定缺口或「其實不需要出 cage」。
- V8 sandbox escape（出 cage、進程內）≠ renderer sandbox escape（出進程、Mojo）——full chain 兩者都要。
- 先問「我到底需要什麼」，別為了 code exec 而 code exec。

## 自我檢核

- [ ] 能說出面對 V8 Sandbox 的三條路線及各自的適用場景
- [ ] 能解釋為什麼「cage 內 data-only」常比「出 cage」可靠
- [ ] 能區分 V8 sandbox escape 和 renderer sandbox escape
- [ ] 知道為什麼 sandbox escape exploit 高度版本相依
- [ ] 面試被問「拿到 V8 heap RW 之後怎麼繼續」，能分場景回答（需不需要出 cage）

## 延伸閱讀

- **[V8 Sandbox threat model / “what is and isn't protected” — v8.dev/blog/sandbox 與 docs](https://v8.dev/blog/sandbox)**
  - **這篇說什麼**：官方明列 sandbox 保護什麼、**不**保護什麼、成熟度定位。
  - **讀哪裡**：threat model 與 limitations 段落。路線二的起點清單就在這。
  - **和本章的關聯**：本章路線二/三的權威依據。

- **[V8 Sandbox bug bounty 公開報告 / issue tracker（chromium bug tracker，sandbox 標籤）](https://issues.chromium.org/)**
  - **這篇說什麼**：真實的 sandbox bypass 報告——缺口長什麼樣、怎麼被補。
  - **為什麼值得讀**：一手的「缺口博物館」，比任何二手教材都真實。

- **[Project Zero / 研究者的 V8 sandbox escape 分析](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：攻擊 EPT / 未 cage 化角落的實際案例。
  - **前提**：先懂 [Ch 34](./34-v8-sandbox.md) 的 EPT 結構。

既然「出 cage」這麼貴，很多現代 exploit 選擇**根本不出去**——在 cage 內用 data-only 達成目的，順便繞過所有控制流防禦。下一章專講這條路，以及擋在它前面的 CET/CFI。

→ [Ch 36 — CET/CFI 之後與 data-only 思路](./36-cfi-cet-data-only.md)
