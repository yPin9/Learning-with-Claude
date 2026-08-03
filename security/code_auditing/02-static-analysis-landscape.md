# Ch 2 — 靜態分析全景：sound、complete 與四工具地圖

> **目標**：建立靜態分析的世界觀。你會搞懂靜態 vs 動態的根本取捨；理解 **sound（無漏報）與 complete（無誤報）為什麼不可兼得**，以及這件事的理論根源（Rice 定理）；認識 flow-/path-/context-/field-sensitivity 這四個精度旋鈕各自貴在哪；最後把 CodeQL、Semgrep、Joern、weggli 四把刀擺進一張座標地圖，知道每把刀的 sound/complete 傾向與適用場景。這一章是全課的**地圖**，之後每一章都是在填地圖上的某一塊。

## SAST 生態鳥瞰：靜態 vs 動態的根本取捨

我們這門課做的事，行業術語叫 **SAST（static application security testing，靜態應用程式安全測試）**：不執行程式，只分析原始碼（或某種中間表示），找出安全問題。對立面是 **DAST（dynamic application security testing）**：跑起來、丟輸入、看它爆不爆——fuzzing、滲透測試都屬於這一類。

兩者的根本差別，一句話：**靜態看得到「所有可能」，動態只看得到「這次真的發生」。**

```
   靜態分析（SAST）                 動態分析（DAST / fuzzing）
   ───────────────                 ─────────────────────────
   不執行，分析全部 code path       執行，只走到有觸發的 path
   看得到所有分支（包括沒觸發的）    看得到真實的 runtime 值
   會誤報（推測的路徑不一定可達）    幾乎不誤報（爆了就是真爆）
   會漏報（近似掉了某些行為）        會漏報（沒觸發到的路徑看不到）
   不需要能跑起來 / 有輸入          需要能編譯、能執行、要餵輸入
```

這個取捨貫穿整門課，值得刻進骨子裡：

- **靜態的優勢是覆蓋**。它能沿著一條「理論上可達、但你 fuzzer 三個月都沒 fuzz 到」的路徑，指出那裡有個 OOB。動態工具永遠受限於「有沒有真的走到那條路徑」。
- **靜態的代價是精度**。它沒有 runtime 的真實值，只能**推測**「這條路徑可能可達」。推測就會錯——它可能報一條實際上永遠走不到的路徑（誤報），或為了不爆炸而把某些行為近似掉、結果漏了真 bug（漏報）。
- **動態的優勢是可信**。fuzzer 給你一個 crash，那就是真 crash，附一個能重現的 input。零誤報。
- **動態的代價是覆蓋**。它只知道「這次的輸入走到了這裡、爆了」，說不出「所有輸入裡有沒有別的爆法」。

所以理想的 workflow 是**靜態撒網、動態驗證**：靜態幫你在幾百萬行裡標出可疑點，動態幫你把可疑點變成可重現的 PoC（這是 Part 7「靜態 + 動態驗證」的主題，也是接 [`advanced_fuzzing`](../advanced_fuzzing/README.md)、[`symex_taint`](../symex_taint/README.md) 的地方）。

## sound 與 complete：不可兼得的宿命

現在講整個靜態分析領域最重要的一組概念。這兩個詞在學術界的定義很容易記混，先把它們釘死。我們談的是「分析器對某個性質（例如『這裡有 bug』）的判斷」：

- **sound（健全，無漏報）**：分析器說「沒 bug」時，就真的沒 bug。換句話說，**所有真的 bug 它都會報出來**——它不會漏。代價是它會為了不漏而寧可錯殺，於是**誤報一堆**。
- **complete（完備，無誤報）**：分析器報「有 bug」時，就真的有 bug。換句話說，**它報的每一個都是真的**——它不會誤報。代價是它為了不錯殺而寧可放過，於是**漏報一堆**。

> **記憶法**：sound = 「我保證抓到所有壞人」（寧可錯殺，誤報多）；complete = 「我抓的全是壞人」（寧可放過，漏報多）。學術文獻對這兩個詞的用法偶有反過來的（取決於你在證明哪個方向的性質），但在 vuln research 語境裡，記住「sound ↔ 無漏報 ↔ 誤報多」「complete ↔ 無誤報 ↔ 漏報多」這組對應就夠用。

用 confusion matrix 看最清楚。把「分析器怎麼判」對上「真實情況」：

|  | 實際有 bug | 實際沒 bug |
|---|---|---|
| **工具報有 bug** | True Positive（真陽性，抓對了） | **False Positive（誤報）** |
| **工具報沒 bug** | **False Negative（漏報）** | True Negative（真陰性，放對了） |

- **sound 的工具**：把 False Negative 那格清成零（絕不漏），代價是 False Positive 那格爆滿。
- **complete 的工具**：把 False Positive 那格清成零（絕不誤報），代價是 False Negative 那格爆滿。

**兩全其美——既 sound 又 complete——的工具，對一般程式的一般性質，不存在。** 這不是工程師不夠努力，是理論上不可能。

### 為什麼不可兼得：Rice 定理的直覺

根源是 **Rice 定理**：程式的任何「非平凡語意性質」（non-trivial semantic property，指那些取決於程式實際行為、且不是所有程式都有或都沒有的性質）都是**不可判定的（undecidable）**——不存在一個演算法，能對任意程式正確回答它有沒有這個性質。

「這段 code 會不會發生 OOB write」正是這種性質。要精確判斷它，等價於解 halting problem——你得知道每條路徑到底可不可達、每個變數在每個點的值域，而這在一般情況下不可計算。

既然精確不可能，工具只能**近似（approximate）**。近似有兩個方向：

```
   over-approximation（過度近似）        under-approximation（不足近似）
   ─────────────────────                ────────────────────────────
   假設「可能發生的都會發生」            假設「沒確認的就當沒發生」
   → 涵蓋所有真 bug（無漏報 = sound）    → 報的都是真 bug（無誤報 = complete）
   → 也涵蓋一堆假 bug（誤報多）          → 也放過一堆真 bug（漏報多）
   例：把不可達的分支也當可達            例：只認明確追得到的資料流
```

- **over-approximation** 往「安全但囉唆」的方向偏：寧可多報，於是 sound（不漏）但誤報多。多數 SAST 工具（CodeQL、學術分析器）站這邊。
- **under-approximation** 往「精準但放水」的方向偏：寧可少報，於是 complete（不誤報）但漏報多。符號執行、concolic 這類（見 [`symex_taint`](../symex_taint/README.md)）常站這邊，動態工具也天生站這邊。

**你選工具、寫 query，本質上都是在這條軸上選一個點**——要多偏 sound（願意 triage 更多誤報，換不漏）還是多偏 complete（接受漏一些，換命中乾淨）。沒有正確答案，只有適不適合你當下的目標。

## 精度的四個旋鈕：sensitivity

近似要做得多細，靠幾個「sensitivity（敏感度）」旋鈕控制。每多開一個，分析更精準（誤報更少），但成本（時間、記憶體）常常是指數級上漲。這裡先點名，深入留到 Part 1（[Ch 3](./03-program-representations-cpg.md) 起）：

| 旋鈕 | 它區分什麼 | 不開會怎樣 | 加一層貴在哪 |
|---|---|---|---|
| **flow-sensitive** | 敘述的**先後順序** | `x=safe; x=evil; use(x)` 分不清用的是哪個值 | 要沿 CFG 逐點算，中等成本 |
| **path-sensitive** | 不同**分支路徑**的條件 | `if(c) x=a else x=b` 後把兩條路徑混談 | 路徑數隨分支指數爆炸，最貴 |
| **context-sensitive** | 同一函式的**不同呼叫點** | `id(evil)` 和 `id(safe)` 的回傳被混為一談 | 呼叫鏈組合爆炸，很貴 |
| **field-sensitive** | struct/物件的**不同欄位** | `s.a` tainted 就把整個 `s` 當 tainted | 要追每個 field，中等成本 |

一個關鍵直覺：**精度不是越高越好**。全開所有 sensitivity 的分析器，對大型 codebase 常常跑到記憶體爆掉、或跑三天跑不完。實務上你永遠在「多開一層精度 vs 分析器還跑得動」之間權衡。這也是為什麼四把刀在這條軸上各站不同位置——有的刻意犧牲精度換速度（weggli），有的願意花成本換深度（CodeQL）。

## 四工具座標地圖

現在把四把刀擺上地圖。這張表是全課的骨架，之後每個 Part 都在把其中一格填厚：

| 工具 | 核心模型 | 要能 build 嗎 | 語言 | sound/complete 傾向 | 最適場景 |
|---|---|---|---|---|---|
| **CodeQL** | 關聯式 database + QL 查詢語言 | **要**（要抽取 build 過程） | C/C++/Java/JS/Python/Go/… | 偏 sound、深、跨函式 taint | 變體獵殺主力、深度 dataflow query |
| **Semgrep** | 貼近原始碼的 pattern + 輕量 taint | 否（tree-sitter parse） | 幾乎全語言 | 可調，預設偏 complete、快 | 快篩、規則工程、上 CI |
| **Joern** | code property graph（CPG）+ 圖查詢 | **否**（可 parse 殘缺 code） | C/C++/Java/JS/… | fuzzy，介於中間 | 無 build / 陌生 target、語意搜尋 |
| **weggli** | C/C++ 半結構化 pattern（懂 AST） | 否 | C/C++ 為主 | 偏 complete、超快 | 縮小攻擊面的第一道漏斗 |

三個對照維度值得單獨拉出來講：

- **要不要能 build？** 這是實務上最現實的分水嶺。CodeQL 要你把整個編譯過程包起來抽取——target 得能編譯，這對殘缺 source、閉源 SDK、奇怪 build system 常常是硬門檻。Joern、weggli、Semgrep 都不需要 build，指到檔案就跑，代價是它們對語意的掌握不如 CodeQL 那個「跟著 compiler 走一遍」的 database 精確。
- **深 vs 快。** CodeQL 花你幾十分鐘建 database、query 也可能跑很久，換來的是跨函式、context-sensitive 的深度 taint tracking。weggli 秒級掃完，但它只做「單函式內的結構 pattern matching」，追不了跨函式資料流。Semgrep 和 Joern 在中間。
- **上手成本。** Semgrep 的 pattern 長得像原始碼，你半小時能寫出第一條規則。CodeQL 要學 QL（一種 Datalog 味的宣告式語言）、學它的 dataflow library，前期投資最大，但天花板也最高。Joern 用 Scala/CPGQL，介於中間。

**一句話定位每把刀**（細節分別在 Part 3–6）：weggli 縮面、Semgrep 快篩、CodeQL 深挖、Joern 補上「build 不了但要語意」的那塊。它們不是競爭關係，是**漏斗上不同層級**——weggli 先把幾百萬行縮成幾千行可疑點，Semgrep/Joern 再篩一輪，CodeQL 對最可疑的部分下深查詢。這個「組合拳漏斗」是 [Ch 35](./35-funnel-combining-tools.md) 的主題。

## 同一個 bug，四工具各自怎麼看

回到 [Ch 1](./01-reading-to-auditing.md) 那個「user 控制的 `len` 直接 `memcpy`」的 bug，我們不真跑，只講**視角差異**，你就能體會四把刀的分工：

- **weggli** 看的是**語法形狀**：「找一個函式，裡面有 `memcpy($dst, $src, $len)`，且 `$len` 是個變數（不是 `sizeof`）。」它懂 AST，所以不會像 grep 那樣被 `sizeof(dst)` 騙到；但它**看不出 `$len` 是不是 user 控制的**——那是跨函式的資料流，超出它的視野。它幫你把幾百個 `memcpy` 縮成「size 是變數」的幾十個。**weggli 不關心資料從哪來，只關心這一段長什麼樣。**

- **Semgrep** 看的是**pattern + 輕量 taint**：你可以寫「`read(...)` 的結果流進 `memcpy` 的 size 參數」的 taint rule。它比 weggli 多一層資料流概念，能表達「tainted」，但它的 taint 是輕量的——跨檔案、複雜 alias 就容易斷。它幫你進一步縮到「看起來 size 真的來自某個 read」的那幾筆。

- **CodeQL** 看的是**全域、跨函式的資料流圖**：它能寫「size 參數的資料流，可以一路 backward 追溯到某個 network read source，且中間沒有經過任何 bound-check sanitizer」。這是最接近你自然語言意圖的查詢，也是唯一能可靠處理「`len` 先存進 struct、傳過三個函式、再拿出來用」這種跨函式流的工具。代價是你得先建 database、學 QL。**這正是變體獵殺的主力視角。**

- **Joern** 看的也是資料流，但走 **CPG 圖查詢**的路子，而且**不需要你能 build 這個 target**。如果你手上的 code 編不起來（殘缺、缺 header、奇怪 SDK），CodeQL 直接卡死，Joern 還能 parse 出一個近似的 CPG 讓你下語意查詢。代價是這個近似的圖比 CodeQL 的 database 更 fuzzy，誤報漏報都可能更多。

同一個 bug，四把刀從**語法形狀 → 輕量 taint → 全域資料流 → 免 build 的圖查詢**四個角度切入，精度和成本一路上升。你之後的功力，就是知道對當下的 target 該用哪一把、或該怎麼組合。

## 踩雷集錦

**踩雷 1：把「工具沒報」當成「安全」。**
錯誤直覺：「CodeQL / Semgrep 掃過了，沒報這裡，那這裡就沒問題。」
正確認識：除非你的工具對這個性質是 **sound（無漏報）**——而實務上你用的 query 幾乎都不是——「沒報」只代表「這條 query 的近似沒撈到它」，不代表沒 bug。你的 pattern 沒涵蓋那種寫法、精度旋鈕開得不夠、跨函式流斷在中間，都會讓真 bug 靜靜漏掉。**靜態的沉默不是無罪證明。** 想確認「真的沒有」，得靠 sound 分析或動態驗證，不能靠「工具沒吭聲」。

**踩雷 2：混淆 SAST 和 DAST。**
錯誤直覺：「靜態分析嘛，跑起來 fuzz 一下就是了。」
正確認識：SAST **不執行**程式、看所有 path、會誤報；DAST（fuzzing、pentest）**執行**程式、只看觸發到的 path、幾乎不誤報。兩者的漏報來源完全不同：SAST 漏在「近似掉了某些行為」，DAST 漏在「沒觸發到某些路徑」。搞混這兩者，你會用錯工具解錯問題——想要覆蓋率卻去 fuzz，想要可信 PoC 卻只信一條 CodeQL 命中。正解是兩者串起來用。

**踩雷 3：以為精度越高越好。**
錯誤直覺：「path-sensitive、context-sensitive、field-sensitive 全開，分析最準，當然全開。」
正確認識：每多開一層 sensitivity，成本常常指數上漲。對一個上百萬行的真實 codebase，全開精度的分析器很可能**跑到記憶體爆掉、或跑幾天跑不完**——一個跑不完的完美分析等於零。實務永遠在「精度 vs 可擴展性」之間妥協，這也是為什麼四把刀刻意站在精度軸的不同點。追求「更準」之前，先問「這個 target 這麼多程式碼，我開得起這個精度嗎」。

## 本章重點整理

- **SAST（靜態）vs DAST（動態）** 的根本取捨：靜態看所有 path、覆蓋廣但會誤報/漏報、不必能跑；動態只看觸發到的 path、幾乎不誤報但受限於覆蓋、必須能執行。正解是靜態撒網、動態驗證。
- **sound（無漏報，誤報多）與 complete（無誤報，漏報多）不可兼得**。根源是 **Rice 定理**：程式的非平凡語意性質不可判定，工具只能近似。
- **over-approximation** 偏 sound（多報，不漏）；**under-approximation** 偏 complete（少報，不誤）。選工具/寫 query 就是在這條軸上選點。
- 精度四旋鈕：**flow- / path- / context- / field-sensitivity**，每加一層更準但成本常指數上漲。**精度不是越高越好**。
- 四工具地圖：**weggli 縮面（超快、C/C++ 半結構）、Semgrep 快篩（跨語言、輕 taint）、CodeQL 深挖（database + QL、跨函式 taint、變體主力）、Joern 補免-build 的語意搜尋（CPG）**。它們是漏斗上的不同層級，不是競爭對手。

## 自我檢核

- [ ] （主動回憶）不看筆記，寫出 sound 和 complete 各自的定義，並對到「誤報多」還是「漏報多」。
- [ ] （主動回憶）畫出 confusion matrix，標出 sound 工具清空哪一格、complete 工具清空哪一格。
- [ ] （理解）用一句話說 Rice 定理怎麼推出「sound 和 complete 不可兼得」。over- 和 under-approximation 分別導向哪一邊？
- [ ] （理解）flow-/path-/context-/field-sensitivity 各區分什麼？舉一個「不開這層就會誤判」的最小例子。
- [ ] （應用）給你一個「必須能 build 才跑」和「編不起來的殘缺 source」兩種 target，你各會先拿四把刀裡的哪一把？為什麼？
- [ ] （理解）為什麼「Semgrep 掃過沒報，所以安全」是危險的推論？要怎樣才能真的說「這裡沒有這類 bug」？

## 延伸閱讀

- **[Semgrep 官方文件 — Overview 與 「Pattern syntax」](https://semgrep.dev/docs/)**：先讀 overview 建立「pattern 貼近原始碼」的直覺，配 [Playground](https://semgrep.dev/playground/) 即時試一條 pattern。這是四把刀裡上手最快的，適合第一個動手。前提：無。
- **[CodeQL 官方文件 — 「About CodeQL」與 「CodeQL for C/C++」入口](https://codeql.github.com/docs/)**：讀它怎麼描述「把 code 變成可查詢的 database」這個核心模型。先不用啃 QL 語法，理解模型即可（語法留 Part 4）。前提：略懂關聯式查詢概念會更好懂。
- **《Static Program Analysis》線上書（Møller & Schwartzbach）**：想把 sound/complete、lattice、sensitivity 的理論一次補齊，這本免費線上教材是最扎實的入門，讀前三章即可對上本課 Part 1。前提：不怕一點形式化符號。
- **[Yamaguchi et al., "Modeling and Discovering Vulnerabilities with Code Property Graphs" (IEEE S&P 2014)](https://ieeexplore.ieee.org/document/6956589)**：Joern 的理論源頭。讀 intro 與 CPG 定義那節，理解「為什麼把 AST + CFG + PDG 合一能同時表達語法與語意漏洞」——這是 [Ch 3](./03-program-representations-cpg.md) 的正菜。前提：看過 AST/CFG 概念（`reading_code` 或本課 Ch 3）。
- **[GitHub Security Lab research](https://securitylab.github.com/research/)**：挑一篇看他們怎麼在「偏 sound、願意 triage 誤報」的路線上把變體撈乾淨——把本章的理論對到真實產出。前提：略懂 CodeQL 概念。

有了「工具能力從哪來、四把刀站哪」的地圖，下一步要打地基：搞懂這些工具底下都在操作什麼樣的**程式表示**——AST、CFG、SSA、PDG，以及把它們合一的 code property graph。

→ [Ch 3 程式表示](./03-program-representations-cpg.md)
