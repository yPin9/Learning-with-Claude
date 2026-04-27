# Ch 1 — 為什麼學 SAT/SMT：全景圖

> 目標：把 SAT 和 SMT 放到電腦科學的地圖上，知道它們從哪來、憑什麼值得學一整本書、為什麼你平常寫 code 沒碰過但其實整個業界都靠它。

## 先把地圖畫出來

邏輯能解的問題是一條光譜：

```
      簡單                                                  困難
   ────────────────────────────────────────────────────────────►

    線性方程組    命題邏輯   SMT（可決 fragment）  一階邏輯    任意程式
    高斯消去     (SAT)      QF_UF, QF_LRA, QF_BV  （半決）   （停機不可決）
                  │
               NP-complete      也是 NP 或更硬
```

這張圖讀三件事：

1. **SAT 是 NP-complete 的代表**。1971 年 Stephen Cook 證明這件事，開創複雜度理論。任何 NP 問題都可以多項式時間轉成 SAT，所以會解 SAT 就等於會解所有 NP 問題（在複雜度意義上）。
2. **SMT 在 SAT 之上**：它保留命題結構，但允許變數屬於某個「理論」 — 整數、實數、bit-vector、array、uninterpreted function。有些理論的 SMT 可決（可以給出明確 yes/no），有些甚至是 NP（跟 SAT 同級）。
3. **一階邏輯本身不可決**，但它的「Quantifier-Free fragment」（`QF_*` 開頭那些）往往是可決的，而且 solver 能跑。

我們這門課的目標是：**從左邊打到中間**。左邊的命題邏輯你要會手推，中間的 SAT + SMT 你要能自己刻。

## SAT 的歷史三個關鍵節點

```
1971  Cook–Levin 定理
      SAT 是第一個被證 NP-complete 的問題
      理論上從此「這問題很硬」有了精確意義

2001  Moskewicz et al. — Chaff
      引入 two watched literals + VSIDS
      把「10 萬變數的工業 SAT」從不可行變成幾秒鐘
      SAT competition 從此變成軍備競賽

2002  Eén & Sörensson — MiniSat
      把 Chaff 的想法用 600 行 C++ 乾淨寫出來
      成為之後 20 年所有 solver 的祖師爺
      你看任何現代 SAT paper，benchmark 一定跟 MiniSat 比
```

**為什麼這三個點重要？** 因為它們解釋了一個反直覺的事實：

> SAT 是 NP-complete（理論最壞情況指數時間），但實務上，我們天天用它解 **百萬變數** 的問題。

為什麼？因為工業界的 SAT instance 不是最壞情況 — 它們有結構。現代 CDCL solver 能抓到這些結構。這整件事 **不是演算法複雜度理論的勝利**，是 **工程 heuristic 的勝利**。Ch 13–16 你會看到這些 heuristic 有多「土」又多有效。

## SMT：SAT 不夠用的時候

SAT 只認 true/false。但你寫 verifier、symbolic executor、scheduler 的時候，你要推理的不是布林：

- **整數**：`x + y > 10 AND x < 5` 有解嗎？
- **實數**：排班問題、浮點規格
- **Bit-vector**：CPU 指令的 overflow、位元運算
- **Array**：程式裡的 array/memory `a[i] = v; a[j]`
- **Uninterpreted Function**：還不想解讀某個 function，只用 `f(x) = f(y) ⇒ x = y` 這類等式推理

硬用 SAT 做這些事也可以（把整數展成 bit-vector 再 bit-blast 成 CNF），但效率極差。SMT 的主意：**SAT 管布林骨架，理論 solver 管語義**。

```
           SMT formula
    例：(x > 0) ∧ ((x < 5) ∨ (f(x) = f(0)))
                  │
       把每個 atom 當成一個布林變數
                  ▼
         SAT 骨架（CNF）
         p_1 ∧ (p_2 ∨ p_3)
         where p_1 = (x>0), p_2 = (x<5), p_3 = (f(x)=f(0))
                  │
       SAT solver 找一組布林指派
                  ▼
         {p_1=T, p_2=T, p_3=F}
                  │
       送給理論 solver 檢查「語義」上是否一致
                  ▼
         x>0 ∧ x<5 ∧ f(x)≠f(0) → 可以（e.g. x=1）
                                       ↑
                                   SMT 說 SAT，給 model
```

這個「SAT 跟理論 solver 合作」的架構叫 **DPLL(T)**，是 Ch 23 整章的主題。

## SAT/SMT 養活了哪些東西

你可能沒直接碰過 SAT solver，但你 daily 用的工具很多底下就是：

| 工具 / 場景 | 用 SAT/SMT 做什麼 |
|---|---|
| CBMC, SeaHorn, Klee | 把 C 程式翻成 SMT，問「能不能走到 assert fail」 |
| angr, Manticore | Symbolic execution，path constraints 丟 SMT |
| LLVM alive2, Souper | Compiler 優化正確性，證明 `peephole rule` 合法 |
| Dafny, Verus, F\* | 形式化驗證語言，VCs 丟 Z3 |
| Boolector (→cvc5) | HW 驗證、bit-vector 推理 |
| rust-analyzer 的 trait 解析 | 某些 corner case 需要 SAT-like 搜索 |
| solver-aided synthesis (Rosette, Sketch) | 合成程式碼，空白處用 SMT 填 |
| SAT competition 的挑戰題 | 密碼學 (SHA-1 collision 部分工作)、組合問題 |
| 每日數獨、掃雷、憲兵難題 generator/solver | 小菜一碟 |

**簡單的底層，撐起整個形式化方法的世界。**

## 為什麼不直接學用 Z3 就好？

你可以。很多人就是這樣 — 把 Z3 當黑盒、拼 SMT-LIB、看它吐 sat / unsat。

但你現在選的是 **最深路線**。差別：

- **用 Z3 的人**：碰到 solver 跑不出來的 instance，不知道怎麼辦。Tactic 選錯、timeout 就卡死。
- **會刻 solver 的人**：看到 instance 會想「這對 VSIDS friendly 嗎、LRA 的 bound 結構如何、要不要 bit-blast」。Z3 跑爛時會換 tactic、改編碼、或直接換 cvc5。

自己刻過一次 CDCL，你對「為什麼這題 Z3 快、那題 Z3 慢」會有物理直覺。這是單純當 API user 得不到的。

## 這門課的學習弧線

```
Part 0    邏輯基礎 ─────────────► 會手推 SAT/SMT 的小例子
          (7 章 + 練習 A)         能把 formula 轉 CNF
          Ch 1 在這                能看懂論文的 notation
                                 │
                                 ▼
Part 1    SAT from scratch ────► 兩版 C++ solver
          (14 章 + 練習 B, C)    DPLL → CDCL + watched literals + VSIDS
                                 能 parse DIMACS、跑 benchmark
                                 │
                                 ▼
Part 2    SMT 核心理論 ────────► 五個理論 solver 的原理
          (12 章 + 練習 D, E)    親手刻 EUF congruence closure
                                 串 DPLL(T) 骨架
                                 │
                                 ▼
Part 3    實戰 + final ─────────► mini-SMT (QF_UF + QF_LRA)
          (2 章 + final project)  吃 SMT-LIB v2 子集
                                 你是 SMT 大師了
```

## 三個觀念現在就要建立

先在這裡放進你腦袋，後面每章會反覆打磨：

1. **Completeness ≠ 快**：一個演算法「保證找到解」跟「能跑得動」是兩件事。DP（Ch 9）完備但沒用，DPLL（Ch 10）完備且能跑小題，CDCL（Ch 13）完備且能跑工業題。
2. **SAT 的一切都繞著 unit propagation 轉**：這是 Ch 10 會介紹的核心 operation，佔現代 solver **80% 執行時間**。watched literals（Ch 12）和其他一切加速都是為了它。
3. **SMT = SAT + theories**，不是全新演算法。你從 Part 1 學到的東西在 Part 2 全都用得上。

## 自我檢核

- [ ] 說得出 SAT 是 NP-complete 但實務能解百萬變數的理由（「工業題有結構、CDCL 抓得到」）
- [ ] 畫得出 SMT = SAT + 理論 solver 的架構圖
- [ ] 說得出三個真實世界用 SAT/SMT 的系統
- [ ] 知道這門課 Part 0/1/2/3 各自會得到什麼

地圖有了，下一章我們從零開始，先把命題邏輯的語法與語義精確定義 — 這是所有後面章節的詞彙表，馬虎不得。

→ [Ch 2 — 命題邏輯的語法與語義](./02-propositional-syntax-semantics.md)
