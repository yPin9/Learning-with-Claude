# Ch 13 — 嚴謹的另一端：形式化規格 TLA+ / Alloy

> **目標**：用 TLA+ 對一個小型並行協定做 model-checking、用 Alloy 探索結構約束；對照散文規格與結構化記法，弄清楚精確度的天花板在哪、以及它的代價。
>
> **環境**：TLA+ Toolbox 1.8.0 / VS Code TLA+ 擴充套件（均適用 TLC model checker）；Alloy 6.x（alloytools.org）。（查證日期 2026-06-30）

---

## 精確度的光譜

從 Ch 8 到 Ch 12，我們沿著一條「讓語言更不模糊」的路走過來：

```
散文需求
  │  ← 八種自然語言病（Ch 8）
  ▼
User Story + 驗收條件  ← Given-When-Then（Ch 10）
  │  ← 受限的英文句型
  ▼
EARS 句型（Ch 11）
  │  ← 關鍵字固定，但仍是英文
  ▼
Use Case + NFR（Ch 12）
  │  ← 結構更完整，仍含自然語言
  ▼
形式化規格（TLA+、Alloy）
  │  ← 數學符號，可機器驗證
  ▼
程式碼本身（最具體，但最難推理）
```

走到光譜最嚴謹的那端，我們得到的是**可機器驗證的精確性**。代價是：學習曲線、寫規格的人力、以及當規格和實作不一致時，不知道是規格錯了還是實作錯了。

這章要做兩件事：先用 TLA+ 抓到一個你用測試抓不到的並行 bug；再用 Alloy 探索一個資料結構的結構約束。最後誠實討論什麼時候值得用、什麼時候不值得。

---

## 歷史脈絡：為什麼形式化方法一直在邊緣？

1970 年代，形式化驗證（Formal Verification）風靡學術界。Edsger Dijkstra 的「程式設計學科」把正確性證明當作首要目標；Tony Hoare 的 Hoare Logic 提供了「{前置條件} 程式碼 {後置條件}」的推理框架。美國國防部的 ADA 語言帶著強型別和子集語言的夢想，想讓軍用軟體可以被正式驗證。

然後現實澆了一盆冷水：大多數工業系統太複雜，完整正式證明的成本比重寫整個系統還高。形式化方法退縮到安全關鍵領域（航電、核電、醫療設備），成為大多數工程師一輩子不會碰到的東西。

**轉折點是 Leslie Lamport**。他在 1994 年發表了 TLA（Temporal Logic of Actions），提出了一個更務實的主張：你不需要完整地「證明」一個系統，只要對你最不確定的那一部分做 **model-checking**——讓機器自動窮舉所有可能的執行路徑，找到反例。

2015 年，AWS 工程師在 Communications of the ACM 上發表了《How Amazon Web Services Uses Formal Methods》（Newcombe 等人），記錄了 TLA+ 在 S3、DynamoDB、EBS 找到的真實設計 bug——都是測試抓不到的，需要 35 步以上的狀態追蹤才能重現其中幾個。這篇論文讓形式化方法在工業界重新被認真對待。

同一時期，MIT 的 Daniel Jackson 採取了另一條路：Alloy。他的論點是「完整證明太昂貴，但有限範圍內的自動反例搜尋幾乎是免費的」。Alloy 的哲學叫做「小範疇假說（Small Scope Hypothesis）」：絕大多數 bug 都能在小的輸入範疇內被發現。

> 如果你對 Ch 11 的 EARS 記法還有疑問，先回看 [Ch 11 — EARS 深入：五種句型馴服英文](./11-ears-notation.md)，因為這章的對比會用到 EARS 作為「散文的最嚴謹邊界」。

---

## TLA+：Temporal Logic of Actions

### 核心直覺

TLA+ 把一個系統描述成**狀態機**：

```
初始狀態                   (Init)
    │
    ▼
狀態 1  ──動作 A──▶  狀態 2  ──動作 B──▶  狀態 3
                        │
                        ▼
                     動作 C 失敗 → 反例！
```

「動作（Action）」是一個謂詞：`Next == A \/ B \/ C`，表示下一個狀態由哪些動作之一觸發。TLC model checker 會從 Init 出發，展開所有可能的狀態轉移圖，檢查你的不變量（Invariant）在每個狀態是否成立。

TLA+ 的數學基礎是 **Temporal Logic of Actions**（Lamport 1994，ACM TOPLAS，DOI 10.1145/177492.177726）。它用數學集合論加上時序邏輯（`[]P` 表示「永遠 P」，`<>P` 表示「最終 P」）來描述一個系統在所有可能執行中的性質。

### 一個具體小協定：互斥鎖

先給散文版：

> 有兩個行程 P1 和 P2 共用一個資源。任何時刻，最多一個行程可以在臨界區（Critical Section）。

然後給 TLA+ 版（Peterson's Algorithm 的簡化互斥協定）：

```tla
---------------------- MODULE Mutex ----------------------
VARIABLES flag, turn, pc

\* pc[i] 是行程 i 的程式計數器：
\* "idle" -> "want" -> "wait" -> "critical" -> "idle"

Init ==
    /\ flag = [i \in {1, 2} |-> FALSE]
    /\ turn = 1
    /\ pc   = [i \in {1, 2} |-> "idle"]

\* 行程 i 決定要進入臨界區
Want(i) ==
    /\ pc[i] = "idle"
    /\ flag' = [flag EXCEPT ![i] = TRUE]
    /\ turn' = (3 - i)          \* 把 turn 讓給另一個行程
    /\ pc'   = [pc   EXCEPT ![i] = "wait"]
    /\ UNCHANGED <<>>

\* 行程 i 等待，直到可以進入
Enter(i) ==
    /\ pc[i] = "wait"
    /\ (~flag[3-i] \/ turn = i)  \* 另一個不在等，或輪到我了
    /\ pc' = [pc EXCEPT ![i] = "critical"]
    /\ UNCHANGED <<flag, turn>>

\* 行程 i 離開臨界區
Exit(i) ==
    /\ pc[i] = "critical"
    /\ flag' = [flag EXCEPT ![i] = FALSE]
    /\ pc'   = [pc   EXCEPT ![i] = "idle"]
    /\ UNCHANGED <<turn>>

Next ==
    \E i \in {1, 2} :
        Want(i) \/ Enter(i) \/ Exit(i)

Spec == Init /\ [][Next]_<<flag, turn, pc>>

\* 不變量：兩個行程不能同時在臨界區
MutualExclusion ==
    ~(pc[1] = "critical" /\ pc[2] = "critical")

THEOREM Spec => []MutualExclusion
==========================================================
```

把這個 spec 用 TLC 跑起來：

1. 在 VS Code 安裝 TLA+ 擴充套件
2. 把上面內容存成 `Mutex.tla`
3. 在 `.cfg` 文件加：
```
SPECIFICATION Spec
INVARIANT MutualExclusion
```
4. 執行 TLC：`CTRL+SHIFT+P → TLA+: Check model`

**預期輸出（正確情況）：**
```
Model checking completed.
No error has been found.
Finished in 0s at (2026-06-30 12:00:00)
State space statistics:
  Distinct states: 44
  Generated states: 106
```

### 刻意製造一個 bug，看 TLC 如何回報

把 `Enter(i)` 條件改錯：把 `(~flag[3-i] \/ turn = i)` 改成 `(~flag[3-i])`（拿掉 turn 條件），讓協定退化成一個不正確的版本：

```tla
Enter(i) ==
    /\ pc[i] = "wait"
    /\ (~flag[3-i])          \* 這裡拿掉了 \/ turn = i，造成 race
    /\ pc' = [pc EXCEPT ![i] = "critical"]
    /\ UNCHANGED <<flag, turn>>
```

TLC 會找到一個反例，輸出類似：

```
Error: Invariant MutualExclusion is violated.
The behavior up to this point is:
State 1: flag = [1 |-> FALSE, 2 |-> FALSE], turn = 1, pc = [1 |-> "idle", 2 |-> "idle"]
State 2: flag = [1 |-> TRUE, 2 |-> FALSE],  turn = 2, pc = [1 |-> "wait", 2 |-> "idle"]
State 3: flag = [1 |-> TRUE, 2 |-> TRUE],   turn = 1, pc = [1 |-> "wait", 2 |-> "wait"]
State 4: flag = [1 |-> TRUE, 2 |-> TRUE],   turn = 1, pc = [1 |-> "critical", 2 |-> "wait"]
State 5: flag = [1 |-> TRUE, 2 |-> TRUE],   turn = 1, pc = [1 |-> "critical", 2 |-> "critical"]
```

7 行 TLA+ 找到了一個需要 5 步才能重現的 race condition。你用單元測試幾乎不可能寫出這個路徑——測試可以驗證「你想到的路徑」，model-checking 驗證「所有路徑」。

### PlusCal：更接近虛擬碼的語法

TLA+ 的數學符號對多數工程師有門檻。PlusCal 是 Lamport 在 2009 年提出的高層語法，編譯成 TLA+：

```pluscal
--algorithm Mutex {
    variables flag = [i \in {1, 2} |-> FALSE], turn = 1;

    fair process (proc \in {1, 2}) {
    idle: while (TRUE) {
    want: flag[self] := TRUE;
          turn := 3 - self;
    wait: await ~flag[3-self] \/ turn = self;
    crit: skip;       \* 臨界區
    exit: flag[self] := FALSE;
    }}}
```

用工具轉譯（Toolbox 或 VS Code 擴充套件有 `Translate PlusCal` 命令），再用 TLC 跑。PlusCal 更接近一般開發者熟悉的虛擬碼結構，但底層仍是 TLA+ 的狀態機語義。

---

## Alloy：關係代數與結構約束

### 核心直覺

如果 TLA+ 擅長「行為（這個系統在所有執行路徑下是否保持某個性質）」，Alloy 擅長「結構（這個資料模型是否存在滿足或違反某條件的實例）」。

Alloy Analyzer 的工作流程：

```
你寫的 Alloy model（關係 + 謂詞 + 斷言）
    │
    ▼
Analyzer 轉譯成 SAT 問題（Boolean Satisfiability）
    │
    ▼
SAT solver 搜尋有限範疇內的解
    │
    ├── 找到反例 → 視覺化顯示「這個結構打破了你的斷言」
    └── 找不到反例 → 在 scope 內沒有違反（不代表全域正確）
```

關鍵限制：Alloy 的結果是「有限範疇內未發現反例」，而不是「全域正確」。這就是「小範疇假說」的意思。對設計工具而言這已經夠了：大多數設計錯誤都能用 3-5 個物件的例子重現。

### 一個具體例子：檔案系統目錄樹

散文規格：

> 一個目錄可以包含零或多個子目錄或檔案。一個目錄或檔案只能有一個父目錄（根目錄除外，它沒有父目錄）。目錄結構不可以有循環。

用 Alloy 表達：

```alloy
module FileSystem

abstract sig FSObject {}
sig Dir  extends FSObject {
    contents: set FSObject
}
sig File extends FSObject {}

one sig Root extends Dir {}

-- 每個非根節點恰好有一個父目錄
fact oneParent {
    all o: FSObject - Root |
        one d: Dir | o in d.contents
}

-- 根目錄不在任何 contents 裡
fact rootHasNoParent {
    Root not in Dir.contents
}

-- 沒有循環（目錄不能是自己的後代）
fact acyclic {
    no d: Dir | d in d.^contents
}

-- 斷言：確認上面的 facts 一起排除了所有錯誤結構
assert noOrphans {
    all o: FSObject - Root |
        some d: Dir | o in d.contents
}

check noOrphans for 5 FSObject, 3 Dir
```

執行 Alloy Analyzer：File → Open → Run `noOrphans`。

**正常輸出**：Alloy 在範疇 5 個 FSObject、3 個 Dir 內找不到反例，顯示綠色「No counterexample found」。

### 刻意破壞，讓 Alloy 找到反例

把 `rootHasNoParent` 刪掉，看 Alloy 能否找到根目錄同時也是子目錄的例子：

```alloy
-- (刪除 rootHasNoParent fact)
-- 現在根目錄可以出現在某個 Dir 的 contents 裡

assert rootIsTopLevel {
    Root not in Dir.contents
}

check rootIsTopLevel for 4
```

Alloy 立刻找到反例，並在視覺化介面中畫出來：

```
Dir0 { contents: Root, File0 }
Root { contents: Dir0 }         ← Root 既是根又是 Dir0 的子節點，且形成循環
```

這個反例顯示了兩個問題同時浮現：根目錄有父節點，且形成了循環。

### Alloy 與 TLA+ 的分工

| 維度 | TLA+ | Alloy |
|---|---|---|
| 主要問題域 | 行為、時序、並行協定 | 結構、約束、資料模型 |
| 驗證機制 | 狀態空間窮舉（model-checking） | SAT-based 反例搜尋 |
| 完備性 | 在有限狀態下完備 | 在有限範疇內完備（小範疇假說） |
| 學習曲線 | 高（需要了解 TLA 邏輯） | 中（關係代數較直覺） |
| 視覺化 | 狀態轉移圖（文字或 Toolbox GUI） | 關係圖（Alloy Analyzer GUI） |
| 最適合 | 分散式協定、快取一致性、資料庫事務 | ER 模型、存取控制政策、設定檔語義 |

---

## 底層機制深挖

### TLC 如何窮舉狀態空間

TLC 使用**廣度優先搜尋（BFS）**展開狀態圖：

```
佇列: [InitState]
已訪問: {}

迭代1: 取出 InitState
  → 計算 Next 的所有可能後繼狀態
  → 對每個後繼狀態，檢查所有 Invariants
  → 把未訪問的後繼狀態放入佇列

迭代2: 取出 State1
  → ... 同上 ...

直到佇列為空（找不到違反）或找到違反的狀態（報告反例）
```

關鍵特性：
- **完備性**：在有限狀態空間下，TLC 的搜尋是完備的。如果有違反，一定找得到。
- **狀態壓縮**：TLC 用雜湊值記錄已訪問狀態，避免重複展開。
- **對稱性化簡（Symmetry Reduction）**：如果多個行程/節點在語義上可互換，可以用 `SYMMETRY` 指示詞大幅減少搜尋空間。
- **限制**：狀態空間可能指數級爆炸。上面的互斥鎖例子只有 44 個狀態；DynamoDB 的真實 spec 有數百萬個狀態。AWS 用 TLC 的分散式版本（執行在多台機器上）來應對這個問題。

### Alloy Analyzer 的 SAT 轉譯

Alloy 把關係模型轉成**命題公式**：

1. 把 sig 的每個關係展開成矩陣（有限範疇內的所有可能組合）
2. 把 facts 和謂詞轉成布林公式
3. 呼叫 SAT solver（通常是 MiniSat 或 Glucose）找到一個「賦值」——即一個具體的關係實例
4. 如果找到賦值且它違反了斷言，就是反例；否則就是「在此範疇內沒有反例」

這就是為什麼 Alloy 的結論永遠附帶「for N」的範疇限定：它的完備性是有條件的。

---

## 和散文規格、EARS、Given-When-Then 的對比

我們拿同一個需求——互斥鎖——用四種記法寫，感受精確度的差異：

### 散文版（Ch 8 的反面教材）

> 系統應該確保多個行程不會同時存取共用資源。

**問題**：「確保」怎麼確保？「同時」是什麼意思（同一毫秒？同一個時脈週期？）？「共用資源」範疇？無法機器驗證，也無法判斷是否達成。

### EARS 版（Ch 11 的工具）

```
When process P1 requests the critical section,
  if process P2 is currently in the critical section,
  the system shall suspend P1 until P2 exits.

When no process is in the critical section,
  the system shall grant the critical section to the
  first requesting process.
```

**進展**：事件和條件清楚了。但「第一個請求的行程」在並行環境下如何定義？兩個行程同時請求時，誰是「第一個」？EARS 無法表達競爭條件（Race Condition）。

### Given-When-Then 版（Ch 10 的工具）

```gherkin
Scenario: Mutual exclusion
  Given P1 is in the critical section
  When P2 requests the critical section
  Then P2 shall be blocked until P1 exits

Scenario: Concurrent requests
  Given neither P1 nor P2 is in the critical section
  When P1 and P2 request the critical section simultaneously
  Then exactly one shall enter and the other shall be blocked
```

**進展**：第二個情境試圖處理並行，但「simultaneously」這個詞在測試時無法精確復現，依賴測試框架的調度，有概率性。

### TLA+ 版（本章的工具）

如上面的 `Mutex.tla`，TLC 窮舉 44 個狀態，**確定性地**驗證了互斥性質在所有可能的交錯執行中成立。

| 記法 | 可讀性 | 表達並行 | 可機器驗證 | 學習成本 | 適用場景 |
|---|---|---|---|---|---|
| 散文 | 極高 | 模糊 | 否 | 零 | 早期溝通 |
| User Story / EARS | 高 | 弱（無法表達競爭） | 部分（語法對，語義仍模糊） | 低 | 大多數功能需求 |
| Given-When-Then | 高 | 受限（依賴測試框架） | 是（可執行） | 中 | 驗收條件、回歸測試 |
| TLA+ | 低 | 原生支援 | 是（窮舉所有路徑） | 高 | 協定、分散式系統核心邏輯 |
| Alloy | 中 | 弱（結構性約束為主） | 是（有限範疇） | 中 | 資料模型、存取控制 |

---

## 踩雷集錦

**1. 誤以為 TLC 通過就是「數學證明正確」**

錯誤直覺：TLC 說「No error found」，這個協定就在數學上被證明正確了。

正確認識：TLC 是 model-checker，只窮舉你指定的有限狀態空間。如果你的 TLA+ spec 本身有錯（例如狀態變數範疇設得太窄，導致某些真實狀態沒有被建模），TLC 不會發現 spec 的問題。真正的數學證明需要 TLAPS（TLA+ Proof System），難度高兩個量級。TLC 的價值是「對我建模的系統，在我指定的範疇內，這個不變量成立」——這已經非常有用，但要誠實標記它的邊界。

**2. 混淆 Alloy 的 fact 和 assert**

錯誤直覺：`fact` 和 `assert` 都是「必須成立的條件」，沒什麼差別。

正確認識：`fact` 是**公理**——Analyzer 直接接受它為真，不嘗試找反例。`assert` 是**斷言**——Analyzer 嘗試找一個滿足所有 facts 但違反 assert 的反例。如果你把本來想驗證的性質寫成 `fact`，Analyzer 永遠不會報錯，因為它假設它為真——你什麼都沒驗證到。

**3. 把形式化規格當「最終規格」**

錯誤直覺：TLA+ spec 是最精確的，所以讓工程師把所有需求都寫成 TLA+ 再開始實作。

正確認識：TLA+ 的建模粒度和實作層次通常有落差。TLA+ 描述的是「系統的抽象行為」，而不是「程式碼的實作細節」。把一個 TLA+ spec 直接翻譯成程式碼幾乎不可能；它的價值是驗證**設計層次的性質**（特別是並行和分散式協定），不是當作設計文件使用。AWS 工程師用 TLA+ 驗證演算法，然後再用一般語言實作。

**4. 以為 Alloy 能驗證動態行為**

錯誤直覺：我的系統有狀態變化，Alloy 能驗證它嗎？

正確認識：Alloy 能建模狀態變化（透過 ordering module 或明確加時間維度的關係），但這需要明確地把「時間」編碼進模型，且仍受小範疇限制。對「系統在某個執行序列下是否保持某個性質」的問題，TLA+ 是更自然的工具。Alloy 的主場是**靜態結構和約束**。

**5. 低估「規格和實作不一致」的維護成本**

錯誤直覺：一旦 TLA+ spec 通過驗證，實作完就可以忘掉 spec。

正確認識：這是最常見的死亡模式。實作演進、spec 不同步，導致 spec 喪失參考價值。有些團隊規定「每次修改協定，先改 spec、TLC 重跑通過後，才能改程式碼」，把 spec 放進 CI。代價是 CI 變慢，但保持了 spec 的可信度。在正式化這件事上不做半套：要嘛把 spec 當活文件維護，要嘛別寫。

---

## 形式化方法的代價與適用邊界

AWS 的報告誠實地列出了成本：

- TLA+ 需要工程師額外學習時間（一般估計 2-4 週才能熟練基本功）
- 建模一個複雜系統可能需要數週
- 維護 spec 需要工程師在協定改動時同步更新

他們同樣誠實地列出了回報：TLA+ 找到的 bug 類型——需要 35 步交錯執行才能重現的 DynamoDB 複製 bug——是測試和 code review 幾乎不可能發現的。AWS 在某些領域的判斷是：這個代價值得。

什麼情況下你應該認真考慮形式化方法：

- 分散式協定（共識算法、複製機制、分散式鎖）
- 安全關鍵系統（航電、醫療設備、核電控制）
- 資料一致性協定（資料庫事務語義、快取失效策略）
- 存取控制政策（「哪些主體在哪些條件下可以對哪些資源做什麼」的精確定義）

什麼情況下形式化方法代價過高：

- 業務邏輯頻繁變動（規格要同步維護，敏捷環境下維護成本高）
- 介面層、UI 層（這類需求用 Given-When-Then 更直覺、更接近用戶驗收）
- 「做完再說型」早期探索（規格都還沒穩定，形式化為時過早）
- 團隊裡沒人有形式化方法經驗（學習曲線的代價不可低估）

---

## 和 SDD 工具的關聯

2025 年的 SDD 工具（Ch 27-31 會詳細介紹）並沒有直接整合 TLA+ 或 Alloy。GitHub Spec Kit 和 AWS Kiro 的「形式化」是輕量級的——用 EARS 句型約束英文、用 Given-When-Then 讓需求可執行，而不是數學形式化。

這個對比是有意義的：

- **SDD 工具的目標**是「讓 LLM 有足夠清楚的輸入」，而不是「數學驗證正確性」
- **TLA+/Alloy 的目標**是「在設計層次排除一類 bug 的可能性」

兩者不互斥。一個完整的流程可以是：
1. 用 TLA+ 驗證分散式協定的核心不變量
2. 把通過驗證的協定描述用 EARS 轉寫成 requirements.md
3. 把 requirements.md 餵給 Kiro 或 Spec Kit 生成任務

但這是精英團隊的做法，不是大多數人的起點。

---

## 進階延伸

### PlusCal 的實用策略

對多數工程師來說，直接上 TLA+ 數學符號太陡。PlusCal 是更務實的切入點。建議路徑：

1. 先用 PlusCal 描述算法（類虛擬碼）
2. 讓工具翻譯成 TLA+
3. 理解翻譯結果，逐步學習 TLA+ 語義
4. 需要更細膩的時序性質時，才直接寫 TLA+

### Alloy 的 ordering 和 time

Alloy 的標準函式庫有 `util/ordering` 模組，可以給任何 sig 加上全序，用來模擬時序。這讓 Alloy 也能表達「A 必須在 B 之前發生」這類性質，擴大了適用範圍。

### Lean 4 和互動式定理證明

TLA+ 的 TLC 是 model-checking（自動，但有邊界）；Lean 4、Coq、Isabelle 是**互動式定理證明系統**——可以寫出數學上完全嚴格的證明，但需要人機合作，成本更高。這是精確度光譜更極端的另一端，適用於密碼學原語、操作系統核心驗證等場域。

---

## 動手練習

**練習 1：跑互斥 bug**

把本章的 `Mutex.tla` 複製到本地，用錯誤的 `Enter(i)` 條件跑 TLC，確認看到反例。然後把條件改回正確版本，確認 TLC 報告「No error found」。記錄：反例有幾個狀態？TLC 探索了多少 distinct states？

**練習 2：Alloy 目錄樹**

把本章的 `FileSystem.als` 在 Alloy Analyzer 執行。先確認有 `rootHasNoParent` fact 的版本無反例。然後刪掉這個 fact，重跑 `check rootIsTopLevel`，觀察 Analyzer 生成的視覺化反例，確認你能讀懂它的意思。

**練習 3：把散文需求形式化**

從 Ch 12 的練習挑一個非功能需求（例如：「任何單點失效不得造成資料遺失」），嘗試用 TLA+ 或 Alloy 的語言精確描述它。不要求跑通，重點是過程中你發現哪些詞語是模糊的、需要澄清的。把這些模糊點列出來。

---

## 本章重點整理

- 形式化規格（TLA+、Alloy）是精確度光譜的頂端：可機器驗證，但學習成本高、維護成本高。
- TLA+ 擅長時序性質和並行行為，TLC 做狀態空間窮舉；Alloy 擅長結構約束，依賴 SAT-based 反例搜尋。
- TLC 通過不等於數學證明——它是在你建模的有限空間內的完備搜尋，不是全域正確性保證。
- AWS 用 TLA+ 找到了 S3、DynamoDB、EBS 的設計 bug，確立了形式化方法在工業界的地位（CACM 2015）。
- 散文、EARS、Given-When-Then、TLA+/Alloy 各有適用層次，不是誰取代誰，而是精確度代價的不同取捨。
- SDD 工具（Kiro、Spec Kit）採用的是輕量形式化（EARS + Given-When-Then），目標是讓 LLM 有清楚的輸入，而不是數學驗證。
- 形式化方法的適用邊界：分散式協定、安全關鍵、存取控制政策；不適合：頻繁變動的業務邏輯、UI 層、無先驗知識的探索階段。

---

## 自我檢核

- [ ] 我能用自己的話解釋 TLA+ 的「動作（Action）」和「不變量（Invariant）」是什麼，面試被問到時不需要翻書
- [ ] 我能說出 TLC model-checking 和「數學證明」的差異，以及 TLC 的完備性邊界在哪
- [ ] 我能解釋 Alloy 的「小範疇假說（Small Scope Hypothesis）」是什麼意思，以及它為什麼是有效的工程假設
- [ ] 我能區分 Alloy 的 `fact` 和 `assert`，說清楚把性質寫成 `fact` 為什麼什麼都沒驗證
- [ ] 我能說出 TLA+ 和 Alloy 各自適合哪類問題，為什麼
- [ ] 我能解釋為什麼 2025 年的 SDD 工具選擇「輕量形式化」而不是 TLA+/Alloy

---

## 延伸閱讀

- **The TLA+ Home Page — Leslie Lamport**
  <https://lamport.azurewebsites.net/tla/tla.html>
  TLA+ 發明者的首頁，包含《Specifying Systems》（2002）電子書、TLA+ Video Course（影片課程），以及 TLC、TLAPS、PlusCal 的說明。從「Learning」區塊的 Video Course 開始，是最系統的入門路徑。與本章的直接關聯：給你一個完整的學習路線圖，把本章的 Mutex 例子延伸到更複雜的系統。

- **How Amazon Web Services Uses Formal Methods — Newcombe 等人（CACM 2015）**
  <https://cacm.acm.org/research/how-amazon-web-services-uses-formal-methods/>
  公開 PDF 鏡像：<https://lamport.azurewebsites.net/tla/formal-methods-amazon.pdf>
  AWS 工程師記錄 TLA+ 在 S3、DynamoDB、EBS 找到具體 bug 的過程，含 bug 類型和需要的狀態追蹤步數。本章引用的工業案例的第一手來源；讀完這篇才能判斷「這個代價值不值得」。

- **Alloy: about / official site — Daniel Jackson 與 MIT Software Design Group**
  <https://alloytools.org/about.html>
  書籍伴侶：<https://alloytools.org/book.html>
  Alloy 官方網站，含《Software Abstractions》（MIT Press，2006/2012 第二版）的連結。「About」頁說明了 Alloy 設計哲學；書的第一章免費提供，把「agile modeling」的概念解釋得很清楚。與本章的目錄樹例子可以直接對應。

- **Specifying Systems — Leslie Lamport（2002）**
  <https://lamport.azurewebsites.net/tla/book.html>（電子書免費）
  TLA+ 的完整技術書。第一部分（Ch 1-4）夠用來寫本章等級的 spec。第三部分才進入 TLAPS 定理證明，初期可跳過。

- **Software Abstractions: Logic, Language, and Analysis — Daniel Jackson（MIT Press，2006；第二版 2012）**
  Alloy 的技術書。前三章建立關係邏輯基礎，Ch 4-6 是 Alloy 語言本體，Ch 7 開始才是真實案例分析。建議搭配 Alloy Analyzer 邊讀邊跑例子。

---

練習 B 會讓你把同一個功能需求用三種不同記法（Given-When-Then、EARS、以及 TLA+ 或 Alloy 的一種）各寫一遍，直接感受精確度與表達難度的取捨。

→ [練習 B 同一功能用三種記法各寫一遍](./practice-b-three-notations.md)
