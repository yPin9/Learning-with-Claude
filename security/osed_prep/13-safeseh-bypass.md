# Ch 13 — SafeSEH 與模組選擇繞過

> **目標**：搞懂 `/SAFESEH` 連結旗標怎麼把「合法 handler」收進一張白名單、`RtlIsValidHandler` 怎麼用這張表擋掉 Ch 12 的 POP POP RET、以及怎麼用 mona 挑出一個能繞過它的模組。順便搞懂 SEHOP 這道更深的防線擋在哪裡，和 SafeSEH 的分工差異。

> **環境**：Win10 x64 22H2 + WinDbg（2026）+ mona v3 + Python3

---

## 為什麼需要這個？

Ch 12 的骨架裡藏了一個沒說清楚的假設：「隨便找一個 `pop; pop; ret` 位址填進去就行」。這在真實世界不成立——如果你隨便挑一個系統 DLL（例如 `kernel32.dll`）裡的 PPR 填進去，程式會直接終止，連 PPR 都沒機會執行。

原因是 `/SAFESEH`：微軟在 2004 年（Windows XP SP2 之後的 MSVC）加入的連結器旗標，把「這個模組裡所有合法的 SEH handler 位址」收進 PE 的一張白名單表裡。例外分發呼叫任何 `Handler` 之前，`RtlIsValidHandler` 會先查這張表——不在表裡，直接拒絕，不呼叫。

這一章要解決的問題：**怎麼知道哪個模組受 SafeSEH 保護、哪個沒有，以及為什麼「沒有 SafeSEH 資訊的模組」反而是攻擊者的機會**。

## 先建立直覺：白名單 vs 完全沒掛名單

把 SafeSEH 想成大樓的門禁系統，差別在於：

- **有 `/SAFESEH` 的模組**：門口貼了一張名單，寫著「這棟樓裡合法的 SEH handler 只有這幾個位址」。保全（`RtlIsValidHandler`）看到不在名單上的 handler，直接擋下。
- **沒有 `/SAFESEH` 的模組**（老 DLL、沒用新版 MSVC 重編過的第三方元件）：這棟樓根本沒裝門禁系統，保全壓根不會去檢查——**任何位址都放行**，因為系統邏輯是「如果這個模組沒提供名單，就當作它不受這層保護」。

這是整章最重要的反直覺事實：**SafeSEH 不是「掃全系統、擋掉所有可疑 handler」，它是逐模組 opt-in 的機制**。只要進程裡載入了任何一個沒有 `/SAFESEH` 的模組，攻擊者就能在那個模組裡找 PPR，繞過整道檢查。

## 核心機制：`RtlIsValidHandler` 的判斷邏輯

```c
/* 簡化邏輯，概念示意 */
BOOL RtlIsValidHandler(PVOID handler_addr) {
    MODULE *module = find_module_containing(handler_addr);

    if (module == NULL) {
        /* handler 不屬於任何已載入模組（例如指向 stack/heap） */
        return FALSE;
    }

    if (module->HasSafeSEHTable) {
        /* 模組有 SafeSEH 白名單：handler 位址必須在表裡 */
        return handler_addr_in_table(module, handler_addr);
    }

    /* 模組完全沒有 SafeSEH 資訊：視為不受保護，直接放行 */
    return TRUE;
}
```

`/SAFESEH` 編譯出來的 PE，在 `IMAGE_LOAD_CONFIG_DIRECTORY` 裡多了兩個欄位：

```
IMAGE_LOAD_CONFIG_DIRECTORY.SEHandlerTable  → 合法 handler 的 RVA 陣列
IMAGE_LOAD_CONFIG_DIRECTORY.SEHandlerCount  → 陣列長度
```

這張表是**連結時靜態產生**的——連結器知道程式裡所有合法的 SEH handler 入口（`__except_handler3/4`、C++ 的 `__CxxFrameHandler3` 等），全部列進去。執行期查表只是比對位址在不在這個固定清單裡，不是動態分析。

三種結果：

1. Handler 位址不屬於任何已載入模組（例如你把它指向 stack 上的 shellcode）→ **拒絕**。這是為什麼不能直接把 `Handler` 填成 shellcode 本身的位址。
2. Handler 位址屬於一個有 SafeSEH 表的模組，但不在表裡 → **拒絕**。
3. Handler 位址屬於一個**完全沒有** SafeSEH 資訊的模組 → **放行**，不管位址是什麼。

Ch 12 的 PPR gadget，策略就是精準命中第 3 種情況。

## 多範例：挑模組的決策過程

| 模組來源 | SafeSEH 狀態 | 能不能用它裡面的 PPR |
|---|---|---|
| 系統 DLL（`kernel32.dll`、`ntdll.dll`，現代 Windows） | 通常有 `/SAFESEH` | 不能，位址不在表裡會被拒絕 |
| vulnserver 這類教學靶程式本體 | 視編譯方式而定（教學靶常**不用** `/SAFESEH`） | 如果本體沒開，整個 exe 範圍內的 PPR 都能用 |
| 老舊第三方 DLL（遊戲 mod、舊版元件庫） | 常常沒有 `/SAFESEH`（用舊版 MSVC 或非 MSVC 編譯器編的） | 能用，是繞過的標準來源 |
| 服務本身附帶的輔助 DLL（例如 vulnserver 的 `essfunc.dll`） | 教學用途常刻意不開 `/SAFESEH` | 能用，OSED 練習裡的常見 PPR 來源 |
| 某個模組開了 ASLR | 和 SafeSEH 是**獨立**的旗標，兩者都要分別檢查 | 即使 SafeSEH=False，若 ASLR 開著，每次重啟位址會變，仍不能硬編位址 |

## 用 mona 找「SafeSEH OFF、無 ASLR、無壞字元」的模組

```
!py mona seh -cpb "\x00\x0a\x0d"
```

`!mona seh` 會列出所有已載入模組，逐個標示幾個關鍵欄位：

```
Address     Module         SafeSEH  ASLR   Rebase  OS Dll  v2.0   Size      BadChars
---------   ----------     -------  -----  ------  ------  -----  --------  --------
0x625012F2  essfunc.dll    False    False  False   False   False  0x0ac000  (none found)
  Found POP POP RETN at 0x625012F2
  Instructions: POP EBX; POP EBP; RETN

0x7C8B172F  kernel32.dll   True     False  ...
  (Skipped — SafeSEH enabled for this module)

0x6F8812AA  SomeLib.dll    False    True   ...
  Found POP POP RETN, but module has ASLR enabled — address not reliable across reboots
```

挑選一個合格 gadget 要同時滿足四個條件：

1. **`SafeSEH: False`**——Ch 13 的核心門檻。
2. **`ASLR: False`**——否則位址每次重啟都變，無法硬編進 exploit。
3. **`Rebase: False`**——模組不會在執行期被重新定位到別的基址。
4. **gadget 本身的 bytes 不含壞字元**——`-cpb` 參數已經幫你篩掉，但仍要用 Ch 8 的壞字元清單覆核。

`-cpb "\x00\x0a\x0d"` 的意思是「排除含 `\x00`（null terminator）、`\x0a`（LF）、`\x0d`（CR）這三個 byte 的位址」——這三個是網路協定/字串處理最常見的壞字元，依你實測出的清單調整。

> **未實測，理論預期**：上面的 mona 輸出格式是示意（依照 Corelan `!mona seh` 文件慣例排版），真實輸出欄位順序、模組清單會依你靶機實際載入的 DLL 不同。驗證法：在你自己的 vulnserver 環境裡掛上 WinDbg，crash 之後跑這條指令，對照真實輸出挑選。

## 多範例：從 mona 輸出到最終選擇的完整決策鏈

假設你的 `!mona seh -cpb "\x00\x0a\x0d"` 跑完，列出了四個候選位址，實務上要怎麼一步步篩到最後只剩一個可用：

| 候選 | SafeSEH | ASLR | Rebase | 含壞字元 | 結論 |
|---|---|---|---|---|---|
| `0x625012F2`（essfunc.dll） | False | False | False | 無 | **可用**：四個條件全過 |
| `0x71AB33C0`（WS2_32.dll） | True | False | False | 無 | 不可用：SafeSEH=True，`RtlIsValidHandler` 會拒絕 |
| `0x6F8812AA`（SomeLib.dll） | False | **True** | False | 無 | 不可用：ASLR 開啟，位址每次重啟不同，無法硬編進 exploit（除非額外做 info leak，那是更高難度的路線） |
| `0x62501300`（essfunc.dll） | False | False | False | 含 `\x0a` | 不可用：gadget 的 opcode bytes 本身含壞字元，資料送到一半會被截斷或曲解 |

這張表想說明的是：**mona 的輸出不是給你「隨便挑第一個」，四個欄位要同時核對**，任何一個不符合都要往下一個候選位址找，而不是直接假設第一筆輸出就是答案。

## 底層機制：為什麼「沒有 SafeSEH 資訊」等於「放行」

這個設計決策本身值得停下來想一下：為什麼微軟不乾脆讓「沒有 SafeSEH 表的模組」也被拒絕，一步到位全部鎖死？

答案是相容性。`/SAFESEH` 是 2004 年後才有的連結器旗標。如果微軟讓「沒有白名單的模組」一律被拒絕，那所有用舊版工具鏈編譯、還沒重新編譯的第三方 DLL，一旦真的用到 SEH，程式會直接崩潰——這對當時大量仍在使用的老軟體是不可接受的破壞性變更。於是妥協方案是：**只對「宣告了白名單」的模組做驗證，沒宣告的視為舊時代遺產，放行**。

這個妥協留下的攻擊面持續了很多年，直到 SEHOP（下一節）從完全不同的角度（驗 chain 本身的結構，而不是驗 handler 位址）把這個漏洞堵上大半。

## SEHOP 簡介：另一道防線

SafeSEH 驗的是「這個 handler 位址合法嗎」；SEHOP（Structured Exception Handler Overwrite Protection，Vista SP1 起逐步部署）驗的是完全不同的東西：**整條 SEH chain 的拓撲結構是否完整**。

直覺：正常的 SEH chain 從 `fs:[0]` 一路走下去，每個 record 的 `Next` 都指向更高位址（更靠近 stack bottom），最後以 OS 注入的 sentinel（`ntdll!FinalExceptionHandlerPad`）結尾。如果 overflow 蓋壞了 nSEH（例如填成短跳指令的 bytes 被解讀成一個位址），這條鏈會斷掉或走不到合法的 sentinel——SEHOP 在**呼叫任何 handler之前**先走一遍整條鏈驗證，驗不過直接終止程式，連 PPR 都沒機會被呼叫。

```
SafeSEH  驗的是：Handler 指標本身是否在白名單裡（逐模組靜態表）
SEHOP    驗的是：整條 SEH chain 的 Next 指標走向是否合法、是否以 sentinel 結尾（執行期動態走鏈）
```

兩者是互補的雙層防線，不是誰取代誰：同時開啟時，攻擊者要讓 Handler 通過 SafeSEH 檢查**而且**讓整條鏈通過 SEHOP 檢查，Ch 12 的標準 payload（蓋壞 nSEH）在 SEHOP 面前直接會被攔下，因為 nSEH 被蓋成短跳指令的 bytes，幾乎不可能恰好組成一個合法的 stack 位址並指向真正的 sentinel。

繞過 SEHOP（重建一條偽造的合法鏈）需要 info leak 取得 ntdll 基址和 stack 位址，難度遠高於 SafeSEH 繞過，且牽涉到更深的 ntdll 內部機制——這部分 OSED 課程不是重點，**internals 更深、含繞過技法推導看 [`windows_pwn` Ch 22](../windows_pwn/22-sehop.md)**，那裡有完整的偽造鏈結構和驗證邏輯偽碼。

本課只需要記住一個實務判斷：**遇到 x86 目標時先確認 SEHOP 是否開啟**（登錄機碼 `DisableExceptionChainValidation`，或 WDEG 設定）。多數現代 CTF/練習靶為了聚焦在單一機制，常會關閉 SEHOP，直接走 Ch 12 的標準流程；真實滲透遇到開啟 SEHOP 的現代系統，SEH overwrite 往往不是最優先的攻擊路線。

## 多範例：三個真實情境的判斷練習

在動手找 gadget 之前，先練習判斷「這個目標的 SEH overwrite 可行度」：

**情境 1**：Windows Server 2012，x86 的老舊網路服務，附帶一個 2008 年編譯、從未更新過的第三方 DLL。

- SEHOP：Server 版本預設開啟（參見 `windows_pwn` Ch 22 的版本表）
- 第三方 DLL：2008 年的工具鏈大概率沒有 `/SAFESEH`
- 判斷：SafeSEH 繞過沒問題（老 DLL 可用），**但 SEHOP 會先擋下**——除非這台伺服器被明確關閉過 SEHOP，否則標準流程打不過，需要先確認 SEHOP 狀態再決定要不要投入這條路線。

**情境 2**：Windows 7 Professional（client 版）桌面應用，x86，應用本體用新版 MSVC 編譯（有 `/SAFESEH`），但載入了一個舊版印表機驅動相關的 DLL。

- SEHOP：client 版本預設關閉
- 舊 DLL：沒有 `/SAFESEH`
- 判斷：**可行**。SEHOP 關閉代表鏈完整性不會被驗，SafeSEH 繞過走舊 DLL 裡的 PPR 即可，是 Ch 12 標準流程直接適用的情境。

**情境 3**：Windows 10 22H2，x64 的現代應用程式。

- 判斷：**整個技法不適用**。x64 用 table-based SEH，handler 資訊在 `.xdata` 唯讀節，stack overflow 蓋不到。遇到 x64 目標，直接跳過 SEH overwrite 這條路線，進 Part 7 的 DEP/ROP。

這三個情境想傳達的判斷順序是：**先看架構（x86/x64）→ 再看 SEHOP 狀態 → 最後才是找 SafeSEH=False 的模組**。順序反過來做（一開始就埋頭找 gadget）常常是白做工——如果 SEHOP 擋著或目標是 x64，再怎麼找 gadget 都沒用。

## 對比取捨：直接控 EIP vs SEH overwrite 各自何時用

| 情境 | 建議路線 |
|---|---|
| Overflow 能一路蓋到 saved return address，且沒有 `/GS` 或 cookie 已被繞過 | 直接控 EIP（Part 2），最簡單，不需要找 PPR、不受 SafeSEH/SEHOP 影響 |
| 開了 `/GS`，SEH record 在 cookie 之前，overflow 會先觸發例外 | SEH overwrite，繞過 cookie 檢查的時間點 |
| 目標模組全部都有 `/SAFESEH`（現代重編譯的程式），且 SEHOP 開啟 | 兩道防線都要過，SEH overwrite 難度大增；若有其他 overflow 路徑（例如直接控 EIP 的點），優先考慮那條 |
| 目標有任何一個老舊、無 `/SAFESEH` 的附屬 DLL（教學靶常見） | SEH overwrite 可行，照 Ch 12 流程直接找 PPR |
| x64 目標 | SEH overwrite 整條技法不適用（table-based SEH），直接走 DEP/ROP 路線（Part 7） |

## 踩雷集錦

1. **錯誤直覺：「找到任何 `pop; pop; ret` 都能用」。** 正確：必須是在**沒有 `/SAFESEH`** 的模組裡的位址。系統 DLL（現代 `kernel32.dll` 等）幾乎都有 SafeSEH，直接用會被 `RtlIsValidHandler` 拒絕。

2. **錯誤直覺：「SafeSEH 是系統層級的全域防護，開了就全擋」。** 正確：SafeSEH 是**逐模組**（per-binary）opt-in 的編譯期旗標。進程裡只要有一個模組沒開，攻擊者就能從那裡找 gadget，其他模組開不開都無所謂。

3. **錯誤直覺：「SafeSEH 和 SEHOP 是同一個機制的不同名字」。** 正確：完全不同。SafeSEH 驗 handler 位址是否在白名單（編譯期產生的靜態表）；SEHOP 驗整條 chain 的拓撲結構（執行期動態走鏈）。可以只開一個、都開、或都不開，是兩道獨立的檢查。

4. **錯誤直覺：「只要 PPR 所在模組沒 SafeSEH，SEHOP 就一定擋不住」。** 正確：兩者各管各的。SafeSEH 過了，不代表 SEHOP 的鏈完整性檢查也會過——SEHOP 檢查發生在呼叫任何 handler 之前，和 handler 位址在哪個模組無關，它看的是 nSEH 這個欄位本身有沒有被蓋壞到讓整條鏈斷掉。

5. **錯誤直覺：「ASLR 關掉就代表 SafeSEH 也沒用了」。** 正確：ASLR 和 SafeSEH 是互相獨立的旗標／機制。ASLR 管模組載入基址是否隨機化；SafeSEH 管 handler 位址白名單。你需要同時確認「這個模組的 SafeSEH 狀態」和「這個模組的 ASLR 狀態」，兩者缺一都會讓硬編的 gadget 位址失效或被拒絕。

## 進階：再往深一層

- **一個模組局部有 SafeSEH 資訊，但 Load Config 目錄本身可能缺失或被某些打包/加殼工具破壞**：少數老程式經過非標準打包後，PE 的 Load Config 目錄整個消失，即使原始碼用 `/SAFESEH` 編譯過，`RtlIsValidHandler` 查不到表，一樣視為「沒有 SafeSEH 資訊」而放行。遇到行為異常（理論上該擋卻沒擋）時，這是一個值得檢查的方向。
- **`IMAGE_DLLCHARACTERISTICS_NO_SEH`**：PE Optional Header 裡還有一個旗標，標示「這個模組完全不使用 SEH」，有這個旗標的模組裡的任何位址都會被 `RtlIsValidHandler` 直接拒絕，比單純「沒有 SafeSEH 表」更嚴格。找 gadget 時如果發現某個預期沒 SafeSEH 的模組卻一樣被拒絕，檢查看看是不是中了這個旗標。
- **ASLR 和 SafeSEH 疊加時的實務選擇順序**：如果一個進程裡「SafeSEH=False」的模組同時也「ASLR=False」的只有一個，而且剛好也符合壞字元要求，那基本上沒什麼選擇空間，直接用它；但如果有多個候選，優先挑**不是系統核心 DLL**（例如應用程式自帶的附屬 DLL）的那個，因為系統 DLL 在不同 Windows 更新版本之間位址編排可能微調，應用程式自帶的 DLL 相對穩定，考場或練習環境重開機後更容易重現同一個位址。
- **SEHOP 開啟時，SafeSEH 繞過還有意義嗎**：有，但只是「必要不充分」條件——SEHOP 檢查發生在 SafeSEH 檢查**之前**（先驗鏈完整性，鏈都驗不過就不會走到逐個呼叫 handler 的階段，更不會走到 `RtlIsValidHandler`）。也就是說，就算你的 gadget 完美符合 SafeSEH=False 的條件，如果 SEHOP 先把鏈判定為不完整，整個流程根本不會進行到「呼叫 Handler」這一步。這是為什麼本章一開始就強調「先確認 SEHOP 狀態」是比找 gadget 更前面的步驟。

## 本章重點整理

- `/SAFESEH` 把合法 handler 位址收進 PE 的 `SEHandlerTable`，`RtlIsValidHandler` 在呼叫 handler 前查表；不在表裡 → 拒絕。
- 關鍵妥協：**完全沒有 SafeSEH 資訊的模組（老 DLL）直接放行**，不管 handler 位址是什麼——這是繞過的標準切入點。
- 用 `!mona seh -cpb "<壞字元>"` 篩選：必須同時滿足 `SafeSEH: False`、`ASLR: False`、無壞字元。
- SEHOP 是另一道獨立防線，驗整條 SEH chain 的拓撲結構，發生在呼叫任何 handler 之前；和 SafeSEH 互補但機制完全不同，深入繞過邏輯見 `windows_pwn` Ch 22。
- 選攻擊路線先看 overflow 能不能直接到 saved return address；碰到 `/GS` 和 SEH record 才考慮 SEH overwrite；再視 SafeSEH/SEHOP 狀態決定是否還可行。

## 自我檢核

- [ ] 能解釋為什麼「完全沒有 SafeSEH 資訊的模組」反而是放行而不是拒絕
- [ ] 能說出用 mona 篩選合格 PPR gadget 時要同時確認的四個條件
- [ ] 能說出 SafeSEH 和 SEHOP 各自驗證的對象、檢查的時機（編譯期產生/執行期走鏈）有什麼不同
- [ ] 遇到一個 x86 目標，能列出判斷「SEH overwrite 現在可行嗎」要依序確認的條件清單
- [ ] 能解釋為什麼 ASLR 開啟會讓一個 SafeSEH=False 的 gadget 仍然不能直接硬編使用

## 延伸閱讀

- **Corelan — "Exploit writing tutorial part 3b: SEH based exploits – the sequel"**（[corelan.be](https://www.corelan.be/index.php/2009/07/28/seh-based-exploit-writing-tutorial-continued-just-another-example/)）
  - 讀哪裡：文中「/GS vs SEH overwrite」和 SafeSEH 繞過的段落。
  - 學什麼：用真實靶示範怎麼找 SafeSEH=False 的 DLL 做 PP gadget 來源。
  - 前提：Ch 12 讀過。
- **Connor McGarr — exploit development 系列**（[connormcgarr.github.io](https://connormcgarr.github.io/)）
  - 讀哪裡：站內搜尋 "SafeSEH"。
  - 學什麼：現代工具鏈下重做 SafeSEH 繞過的實際操作步驟。
  - 前提：本章讀完。
- **Microsoft Learn — `/SAFESEH`（連結器參考）**（[learn.microsoft.com](https://learn.microsoft.com/en-us/cpp/build/reference/safeseh-image-has-safe-exception-handlers)）
  - 讀哪裡：旗標說明與適用條件。
  - 學什麼：官方對 `/SAFESEH` 的定義與限制（例如僅 x86、需要目標模組所有物件都支援）。
  - 前提：無，隨時可查。
- **[`windows_pwn` Ch 22 — SEHOP：機制與繞過](../windows_pwn/22-sehop.md)**
  - 讀哪裡：全文，尤其是「繞過路線 1：重建合法 SEH chain」和「SafeSEH vs SEHOP 差異」表格。
  - 學什麼：SEHOP 驗證邏輯的完整偽碼、偽造鏈繞過所需的 info leak 鏈路；internals 更深看那裡，本章只取判斷層級的切片。
  - 前提：本章讀完。
- **[mona.py — Corelan（GitHub）](https://github.com/corelan/mona)**
  - 讀哪裡：README 的 `!mona seh` 指令與欄位說明（SafeSEH / ASLR / Rebase / OS Dll）。
  - 學什麼：每個欄位代表的檢查項目，挑選 gadget 時逐一核對。
  - 前提：Ch 12 讀過。

機制、流程、繞過門檻都齊了。接下來用一次完整的練習，把 Ch 11–13 串成一條從零到彈 shell 的 exploit。

→ [練習 B — SEH-based exploit](./practice-b-seh-exploit.md)
