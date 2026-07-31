# Ch 36 — CET/CFI 之後與 data-only 思路

> **目標**：理解擋在「控制流劫持」前面的現代硬體/軟體防禦——**CET**（shadow stack + IBT）與 **CFI**——它們怎麼把 ROP/JOP 和「跳到任意位址」變得極難；以及攻擊者的回應：**data-only attack**——不劫持控制流，只改「資料」達成目的，從而繞過所有這些防禦。這是 Part 6 的收尾，也是現代 exploit 的思想終點。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`。本章為防禦機制與利用哲學分析；具體 data-only exploit 高度目標相依，標「未實測，理論預期」。

## 為什麼需要這個？

前幾章你看到 sandbox 把「改指標打穿進程」封死。就算你能改一個 code 指標，還有一層在等你：**CET/CFI 讓「跳到你要的地方」本身變得極難**。這一章解釋那層防禦，然後給你攻擊者的最終回應——**乾脆不跳**。data-only 是現代 V8（和一般現代軟體）利用的主流哲學，理解它，你才算真正走完「拿到 RW 之後」的思想旅程。

## 先建立直覺：門禁森嚴，那就不走門

傳統控制流劫持（ROP、覆寫返回位址、改 vtable）的本質是**改變程式的執行路徑**——讓 CPU 跳到你要的地方。現代防禦針對的正是「跳」這個動作：

- **後向邊（return）**：你不能隨便改返回位址跳回任意處——**shadow stack** 會對照。
- **前向邊（indirect call/jump）**：你不能隨便 call/jump 到任意處——**IBT / CFI** 要求目標是合法的入口。

門（控制流轉移）被看得死死的。攻擊者的頓悟：**如果改變執行路徑這麼難，那就不改路徑，改「資料」**。讓程式沿著它**正常的**執行路徑跑，但因為關鍵資料被你改了，正常路徑就做出你要的壞事。這就是 data-only。

## 底層機制一：CET（硬體控制流防禦）

Intel **CET（Control-flow Enforcement Technology）**是 CPU 層級的控制流保護，兩部分：

### Shadow Stack（後向邊）

CPU 維護一個**影子堆疊**，和一般堆疊平行。每次 `call` 時，返回位址**同時**被推到一般堆疊和 shadow stack。`ret` 時，CPU 比對兩者——**不一致就 fault**。

後果：你用任意寫改了堆疊上的返回位址（經典 stack overflow / ROP 的核心），`ret` 時和 shadow stack 對不上，直接崩。**ROP 的地基被硬體抽掉。**

### IBT（前向邊，Indirect Branch Tracking）

每個合法的**間接分支目標**（會被 `call rax` / `jmp rax` 跳到的地方）必須以一條特殊指令 **`endbranch`（endbr64）** 開頭。CPU 執行間接分支後，若落點的第一條指令不是 `endbranch`，就 fault。

後果：你不能跳到一個 gadget 的中間、或任意位址——只能跳到有 `endbranch` 標記的合法入口。**JOP / 跳進 code 中間的招被封。**

## 底層機制二：CFI（軟體控制流完整性）

**CFI（Control Flow Integrity）**是編譯期 + 執行期的軟體防護，概念類似 IBT 但更細：對每個間接呼叫，檢查目標是否屬於「這個呼叫點允許的合法目標集合」（例如「這個函式指標只能指向簽章相符的函式」）。Clang 的 CFI、以及 Chrome 用的各種 CFI 變體，讓「覆寫一個函式指標指向任意函式」失效——就算你改了指標，CFI 檢查會發現目標不在允許集合裡。

## 這些防禦一起造成什麼

把它們疊起來，傳統控制流劫持幾乎全滅：

| 傳統招 | 被誰擋 |
|---|---|
| 覆寫返回位址 (ROP) | CET shadow stack |
| 跳進 gadget 中間 (JOP) | CET IBT (endbranch) |
| 覆寫函式指標指向任意函式 | CFI |
| 覆寫 vtable 指向假 vtable | CFI / vtable 保護 |

於是「拿到寫的能力 → 改個指標 → 跳到 shellcode/gadget」這條在 `binary_exploitation` 用爛的路，在現代目標上處處碰壁。這逼出了 data-only。

## 底層機制三：data-only attack

data-only 的核心：**不碰任何 code 指標、不改任何控制流，只改「資料」**——因為 CET/CFI 只看「跳」，不看「資料對不對」。

在 V8 的脈絡，「資料」有很多值錢的目標，且**全在 cage 內**（所以連 [Ch 34](./34-v8-sandbox.md) 的 sandbox 都不擋，因為你根本不出 cage、不碰 code 指標）：

- **改一個陣列的 length**：把一個普通陣列的 length 改成巨大值 → 穩定的 cage 內 OOB（這本身就是很多 exploit 的「引擎」）。
- **偽造一個物件的 map**：讓 V8 用錯誤的型別解讀一塊記憶體 → 型別混淆的資料版。
- **改權限/狀態旗標**：例如某個「這段記憶體是否可寫」「這個操作是否被允許」的布林。
- **污染 JIT 依賴的資料**：改一個 feedback、一個 map 假設所依賴的欄位，間接讓優化碼做壞事。
- **改字串/TypedArray 的長度或內容**：讀寫更多 cage 內記憶體。

一句話：**讓 V8 沿著它正常的碼跑，但餵它被污染的資料，它就自願替你做壞事。** 沒有跳、沒有 gadget、沒有 shellcode，CET/CFI 無從發揮。

## data-only 的代價與現實

data-only 不是免費的：

- **它更「目標特定」**：ROP 有通用套路，data-only 要針對「這個程式的哪個資料改了會達成我的目的」量身設計，更費工、更需要理解目標內部。
- **它常常是「圖靈完備的資料機器」**：一個穩定的 cage 內 OOB（改 length 得來的）＋ addrof/fakeobj，本身就是一台能讀寫 cage 內任意處的機器——很多攻擊目的（洩漏跨站資料、達成邏輯目的）用這台機器就夠，根本不需要 code exec。
- **要 code exec 時仍需配合**：若最終真要進程級 code exec，data-only 通常要配合「找一個尚未 cage 化的指標」或「sandbox/CFI 的縫隙」——回到 [Ch 35](./35-bypassing-v8-sandbox.md)。

這呼應了整個 Part 6 的主旋律：**現代 exploit 是一連串「在限制下達成子目標」的組合，不是一發 shellcode**。

## 對比：控制流劫持 vs data-only

| 面向 | 控制流劫持（傳統） | data-only（現代主流） |
|---|---|---|
| 改什麼 | code 指標 / 返回位址 | 純資料（length、map、旗標） |
| 被 CET/CFI 擋 | ✅ 幾乎全滅 | ❌ 不碰控制流，繞過 |
| 被 V8 Sandbox 擋 | 部分（code 指標間接化） | ❌ 全在 cage 內資料 |
| 通用性 | 高（ROP 套路） | 低（目標特定，量身設計） |
| 現代可行性 | 低 | **高** |

這張表是整個 Part 6 的濃縮：防禦（sandbox + CET/CFI）把「劫持控制流」這條老路封死，攻擊者改走「改資料讓程式自願犯錯」——這就是現代 V8（乃至現代軟體）利用的重心遷移。

## 踩雷集錦

1. **以為拿到寫就能 ROP**：在開了 CET 的現代目標上，覆寫返回位址會被 shadow stack 抓到、崩掉。ROP 不再是預設選項。
2. **以為改函式指標就能跳任意函式**：CFI 會檢查目標合法性。改了也跳不到不合法的地方。
3. **忽略 data-only 才是現代主流**：還停在「一定要 code exec、一定要 shellcode」的思維，會在現代目標前處處碰壁。很多攻擊用 cage 內 data-only 就達成了。
4. **以為 data-only 很弱**：一個穩定的 cage 內 OOB + addrof/fakeobj 是圖靈完備的讀寫機器，威力極大，且繞過 CET/CFI/sandbox。弱的是「通用性」，不是「威力」。
5. **把 CET 當軟體防禦**：CET 是**硬體**（CPU）機制，shadow stack 和 IBT 由 CPU 強制。這是它比純軟體 CFI 更難繞的原因。

## 進階：再往深一層

- **shadow stack 的繞法研究**：學界有針對 shadow stack 的攻擊（例如利用尚未保護的路徑、signal handling、或非 CET-aware 的舊碼）。但在全 CET 的目標上極難，這也是為什麼大家轉向 data-only。
- **endbranch gadget**：IBT 只要求「落點有 endbranch」，若程式裡存在以 endbranch 開頭、後面剛好是有用序列的「合法入口」，仍有受限的 JOP 空間。研究「endbranch gadget」是繞 IBT 的一支。
- **V8 的 data-only 目標盤點**：系統性地列出「改哪個欄位會造成什麼」——length、map、elements 指標、feedback、protector cell——是 data-only exploit 開發的核心功課。
- **CFI 的粒度**：不同 CFI 方案粒度不同（粗粒度只檢查「是不是某函式開頭」，細粒度檢查簽章）。粒度越粗，可濫用的合法目標越多。理解目標用哪種 CFI 決定你的空間。
- **與 sandbox 的協同**：data-only 之所以在 V8 這麼有效，正因為它待在 cage 內——sandbox（防出 cage）和 CET/CFI（防劫持控制流）都在防「別的事」，data-only 從它們中間的空隙走。

## 動手練習

1. 反組譯一個現行 d8 的函式（`--print-opt-code` 或 `objdump`），找 `endbr64` 指令——確認 IBT 的落點標記真的存在於 code 裡。數一數一個函式開頭有沒有 endbranch。
2. 概念演練：在 cage 內，若你有任意讀寫，列出「改哪三個資料能立刻放大你的能力」（提示：陣列 length、TypedArray 的長度、物件的 map）。這就是 data-only 的思考起點。
3. 思考題（面試）：為什麼 CET/CFI 對 data-only attack 完全無效？用「它們保護的是控制流轉移、data-only 不轉移控制流」回答，並舉一個 V8 的 data-only 例子。

## 本章重點整理

- **CET**（硬體）：**shadow stack** 擋覆寫返回位址（ROP）、**IBT/endbranch** 擋跳進任意位址（JOP）。
- **CFI**（軟體）：檢查間接呼叫目標的合法性，擋「覆寫函式指標指向任意函式」。
- 這些一起讓**控制流劫持**幾乎全滅——傳統「改指標→跳 shellcode/gadget」的路封死。
- 攻擊者回應：**data-only**——不碰控制流，只改資料（length、map、旗標），讓程式沿正常路徑自願犯錯，**繞過 CET/CFI，且全在 cage 內繞過 sandbox**。
- 代價是目標特定、需深懂內部；但一個 cage 內 OOB + addrof/fakeobj 是圖靈完備的讀寫機器，威力足夠。

## 自我檢核

- [ ] 能解釋 shadow stack 和 IBT 各擋哪種控制流劫持
- [ ] 能說出 CFI 和 IBT 的關係與差異
- [ ] 能定義 data-only attack，並說明它為什麼繞過 CET/CFI **和** sandbox
- [ ] 能舉出至少三個 V8 裡的 data-only 目標（資料欄位）
- [ ] 面試被問「現代軟體有了 CET/CFI，記憶體漏洞是不是沒用了」，能用 data-only 反駁

## 延伸閱讀

- **[Intel CET 技術文件 / “A Technical Look at Intel's Control-flow Enforcement Technology”](https://www.intel.com/content/www/us/en/developer/articles/technical/technical-look-control-flow-enforcement-technology.html)**
  - **這篇說什麼**：shadow stack 與 IBT 的硬體機制第一手說明。
  - **讀哪裡**：shadow stack 與 IBT 兩節。本章硬體部分的權威依據。

- **[“Control-Flow Integrity: Precision, Security, and Performance” — Burow et al.（CFI 綜述）](https://dl.acm.org/doi/10.1145/3054924)**
  - **這篇說什麼**：CFI 的分類、粒度、安全/效能取捨的學術綜述。
  - **讀哪裡**：粒度與繞法段落。理解「粗/細粒度 CFI」對攻擊空間的影響。

- **[“Data-Oriented Programming (DOP)” — Hu et al., IEEE S&P 2016](https://ieeexplore.ieee.org/document/7546545)**
  - **這篇說什麼**：data-only attack 的學術化——證明純資料攻擊可達圖靈完備，繞過所有控制流防禦。
  - **和本章的關聯**：本章 data-only 哲學的理論根基。

- **[Project Zero / 現代 V8 exploit 的 data-only 實例](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實 exploit 怎麼在 sandbox + CET 下用 data-only 達成目的。
  - **前提**：先懂 [Ch 34](./34-v8-sandbox.md) 的 sandbox 與本章的 CET/CFI。

Part 6 走完：你懂了拿到 RW 之後，現代防禦（sandbox + CET/CFI）怎麼層層設限，以及攻擊者用 data-only 從縫隙穿過。下一個練習把 Part 6 綜合起來——在有/無 sandbox 兩種 build 上，把任意讀寫接到 code execution。

→ [練習 F — 任意 R/W 到 code execution](./practice-f-rw-to-code-exec.md)
