# Ch 25 — 祖先與對照：TDD / BDD / MDA / 文學編程

> **目標**：把 SDD 放到家族樹上——它和 test-first 方法哪裡相同、哪裡分叉，為何它回響了 2000 年代 Model-Driven Architecture（以 UML 生成程式碼）那場多半失敗的夢，以及文學編程（Literate Programming）這條更古老的血脈如何傳入當代。

## 把 SDD 放到時間軸上

先給一張圖，後面再逐一展開：

```
1984  文學編程 (Knuth)
      └─ 核心主張：散文主導，程式碼藏在文章裡
                  ↕ 「把意圖寫清楚」的精神傳下去

1986  Use Case (Jacobson)
      └─ 把「使用者目標」放到設計中心

1990s XP / TDD (Beck)
      └─ 核心主張：先寫測試，讓測試驅動設計

2000  MDA / Model-Driven Architecture (OMG)
      └─ 核心主張：從 UML 模型生成程式碼
      ↓  (大多失敗：模型比程式碼更難寫)

2001  Agile Manifesto
      └─ working software over comprehensive documentation

2003  JBehave (North) → BDD 雛形
2006  BDD + Given-When-Then (North 正式命名)
2009  EARS (Mavin et al., Rolls-Royce)
2011  Specification by Example (Adzic)

2017  Software 2.0 (Karpathy)
2023  "English is the hottest new programming language" (Karpathy tweet)

2024  Tessl "spec-centric" (Podjarny, Nov 2024)
2025  "The New Code" (Grove, OpenAI, June 2025)
      GitHub Spec Kit (Sept 2025)
      AWS Kiro (July 2025)
         ↑
        SDD as 2025-era umbrella term
```

這張圖的重點不是年份，而是因果：每一個節點都在回應上一個節點的痛點。

---

## TDD：顛倒的 SDD？

**測試驅動開發（Test-Driven Development，TDD）** 由 Kent Beck 在 1999–2000 年的 Extreme Programming（XP）著作中系統化。它的基本循環是 Red → Green → Refactor：先寫一個會失敗的單元測試，再用最少的程式碼讓它通過，最後重構。

TDD 的核心洞見是：測試是對**行為的精確描述**。如果你還沒辦法寫出測試，你的需求就還沒清楚。

```
TDD 的信念：
  測試 ──定義──▶ 行為
  行為 ──產生──▶ 實作
```

SDD（2025 AI 版）的信念：

```
SDD 的信念：
  規格（Spec）──定義──▶ 系統的形狀
  Spec ──驅動──▶ LLM 生成實作 + 測試
```

表面上很像，但差別在**粒度**和**生成方向**：

| 面向 | TDD | SDD（AI 世代） |
|------|-----|----------------|
| 精確度的載體 | 可執行的 unit test | Markdown 規格文件（+ 可選 EARS） |
| 誰產生「另一邊」 | 人類工程師 | LLM 代理 |
| 最小工作單元 | 一個函數/方法 | 一個功能（feature），含設計與任務拆解 |
| 驗證方式 | 測試通過即可 | 任務打勾 + 實作回溯規格 |
| 文件是副產品還是主角 | 副產品（測試即文件） | 主角（規格即真相來源） |
| 可獨立閱讀性 | 低（需懂程式語言） | 高（自然語言或接近） |

TDD 和 SDD 不是競爭關係，但也不是「SDD 就是大 TDD」。TDD 是**函數層級**的設計技術；SDD 是**功能層級**的意圖管理技術。事實上，Kiro 生成的 `tasks.md` 裡每個任務仍預期附帶測試——它把 TDD 當成 SDD pipeline 末端的一個細節。

---

## BDD：TDD 和領域語言的孩子

**行為驅動開發（Behaviour-Driven Development，BDD）** 是 Dan North 在 2006 年一篇〈Introducing BDD〉中正式命名的（最初在 Better Software 雜誌，2006 年 3 月）。North 說，他把 TDD 課程教了很多次，學員最常問的問題是「我到底該測什麼？」他的解法是把「test」這個詞換成「behaviour」——你描述**系統應該展現的行為**，而不是說「我在測這個函數」。

BDD 帶出了 Given-When-Then 格式：

```gherkin
Feature: ATM 現金提領

  Scenario: 帳戶有餘額、提款機有現金
    Given 帳戶餘額為 1000 元
    And   提款機裡有足夠現金
    When  客戶請求提領 200 元
    Then  帳戶餘額應減至 800 元
    And   提款機應吐出 200 元現金
    And   金融卡應歸還客戶
```

這個格式後來被 Gherkin 機械化（Aslak Hellesøy 建立 Cucumber，2007 年前後），讓 `.feature` 檔案直接跑成驗收測試。

BDD 是 SDD 的**直接前輩**，原因有二：

1. Given-When-Then 幾乎原封不動出現在 Kiro 的 `requirements.md` 驗收條件裡（搭配 EARS 格式）。
2. BDD 確立了「用**可執行的規格**代替被動文件」的精神——Gojko Adzic 2011 年的書《Specification by Example》把這個精神命名為「活的文件（living documentation）」。

> 如果你對 Given-When-Then 和 BDD 的細節還不熟，先回看 [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)。

但 BDD 和 SDD 的分叉點在於**誰來消費規格**：BDD 的 Gherkin 是讓 Cucumber 跑的，它要求步驟定義（step definitions）和程式碼嚴格綁定。SDD 的規格是讓 LLM 讀的，它允許 Markdown 敘述，不要求跟測試框架 1-1 對應。

> 如果你對 EARS 格式（Kiro 採用的那種）還不熟，先回看 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)。

---

## MDA：失敗的前輩，SDD 的鏡子

**模型驅動架構（Model-Driven Architecture，MDA）** 是 2000 年代初 Object Management Group（OMG）推的方法論，核心主張是：用 UML 寫抽象模型（PIM，Platform Independent Model），再讓工具自動生成針對特定平台的程式碼（PSM，Platform Specific Model）。

最雄心勃勃的版本叫 **Executable UML**（可執行 UML），直接宣稱 UML 圖就是程式，工具生成的程式碼不必人碰。

MDA 的理論很漂亮：

```
UML 模型（高層意圖）
      │
      ▼  自動轉換（Model-to-Code / M2C）
程式碼（低層實作）
```

**為什麼多半失敗了？**

- **模型比程式碼更難寫**：畫一張正確的 UML 類別圖，標上所有多重性（multiplicity）、限制（constraint）、狀態轉換，比直接寫 Java 需要更多精力，卻沒有 IDE 補全、沒有即時型別回饋。
- **生成器過於脆弱**：生成的程式碼難以閱讀和維護，遇到特殊需求就崩潰。工程師必須修改生成的程式碼，然後發現模型跟程式碼之間的同步是場噩夢。
- **工具鏈昂貴且封閉**：IBM Rational、Sparx EA 等工具都不便宜，而且互不相容。
- **圓形自指問題**：要驗證模型是否正確，你幾乎需要懂模型裡描述的那個領域——跟直接寫程式碼所需的知識差不多。

InfoQ 的一篇 SDD 分析（2025 年）直接把這個比較道出：SDD 被批評者稱為「MDA 的轉世」，只不過把剛性的 UML-to-code 生成器換成了 LLM。

這個類比既有道理，也有重要差異：

| 面向 | MDA（2000s） | SDD with LLM（2025） |
|------|-------------|----------------------|
| 「模型」長什麼樣 | 嚴格符號的 UML 圖 | 自然語言 Markdown 規格 |
| 轉換機制 | 剛性樣板引擎（M2C） | 有上下文理解的 LLM |
| 輸出品質 | 多半不可讀、難維護 | 通常可讀，但仍有幻覺風險 |
| 對模型精確度的要求 | 極高（一個多重性錯就失敗） | 中（LLM 會猜，這是優點也是缺點） |
| 工具成本 | 昂貴商業授權 | 主要是 token 費用，開源工具可用 |
| 最大的生態賭注 | 「UML 是通用語言」 | 「LLM 能理解意圖而非只是格式」 |

SDD 的樂觀論：LLM 解決了 MDA 的「生成器過於脆弱」問題，因為 LLM 理解意圖，不只是格式匹配。  
SDD 的悲觀論：如果規格不夠精確，LLM 會靜默地猜——MDA 生成器失敗時會崩潰，LLM 失敗時會悄悄給你看起來正確但語義錯誤的程式碼，更難發現。

> 如果你對 SDD 懷疑論者的完整論證有興趣，下一章 [Ch 26 懷疑論者的最強論證](./26-skeptics-case.md) 會系統整理。

---

## 文學編程（Literate Programming）：最古老的血脈

**文學編程（Literate Programming）** 是 Donald Knuth 在 1984 年提出的程式設計方法。他認為程式應該是給人類讀的文學作品，碰巧也能讓電腦執行。他的工具鏈是 WEB（後來有 CWEB）：把文件和程式碼交織在同一份檔案，用 `tangle` 抽出可執行程式碼，用 `weave` 產出排版精美的文件。

```
Knuth 的文學編程檔 (WEB)
       │
  tangle ──▶  可執行程式碼（Pascal / C）
  weave  ──▶  人類可讀的技術文章（TeX / PDF）
```

文學編程的精神：**意圖（散文）先行，程式碼只是意圖的一種表達形式**。

SDD 和文學編程的關係，是精神傳承而非直接繼承：

| 面向 | 文學編程（Knuth, 1984） | SDD（2025） |
|------|------------------------|-------------|
| 意圖的載體 | 散文 + 數學符號，夾雜程式碼 | Markdown 規格文件 |
| 誰寫程式碼 | 工程師（散文之間穿插） | LLM（規格之後生成） |
| 散文和程式碼的關係 | 散文**解釋**程式碼 | 規格**驅動**程式碼的生成 |
| 驗證 | 人類閱讀 | LLM 實作 + 可能的測試 |
| 規模化 | 個人工具（Knuth 自己用來寫 TeX） | 團隊工作流（Spec Kit / Kiro） |

Sean Grove（OpenAI）在 2025 年 AI Engineer World's Fair 的演講〈The New Code〉裡說（per a community transcript of the talk）：「程式碼只佔 10% 到 20% 的價值……其他 80% 到 90% 是結構化溝通。」這句話和 Knuth 的直覺有回響——意圖比實作更珍貴。但方向相反：Knuth 的工程師同時寫散文和程式碼；Grove 主張讓 LLM 寫程式碼，工程師只寫散文（規格）。

---

## 四條血脈的對比表

| | TDD | BDD | MDA | 文學編程 | SDD（2025） |
|---|---|---|---|---|---|
| **年代** | 1999–2000s | 2006 | 2000s | 1984 | 2024–2025 |
| **精確意圖載體** | 單元測試 | Given-When-Then | UML 圖 | 散文夾程式碼 | Markdown 規格 |
| **程式碼從哪來** | 人類 | 人類 | 模板生成器 | 人類（散文間） | LLM 生成 |
| **規格的可執行性** | 直接（測試即規格） | 直接（Gherkin 跑測試） | 嘗試（常失敗） | 不直接（散文描述） | 間接（LLM 解讀） |
| **最大賭注** | 測試能驅動好設計 | 行為語言能跨越技術/業務邊界 | 模型能生成好程式碼 | 閱讀性改善可維護性 | LLM 能理解意圖 |
| **歷史評價** | 成熟、廣泛採用 | 成熟，BDD/Gherkin 生態健全 | 多半失敗，教訓深刻 | 小眾但影響大（Jupyter Notebook 的精神繼承者） | 還在形成，2025 熱潮 |

---

## 一個可以跑的範例：同一個需求，四種記法

**需求**：使用者登入失敗三次後，帳號鎖定 15 分鐘。

### TDD（Python unittest，先寫測試）

```python
# tests/test_login.py
import unittest
from login import UserAccount

class TestLoginLockout(unittest.TestCase):
    def setUp(self):
        self.account = UserAccount(username="alice")

    def test_account_locks_after_three_failures(self):
        """三次失敗後帳號應鎖定"""
        for _ in range(3):
            self.account.attempt_login(password="wrong")
        
        # 此時帳號應被鎖定
        self.assertTrue(self.account.is_locked())

    def test_locked_account_rejects_correct_password(self):
        """鎖定期間即使密碼正確也應拒絕"""
        for _ in range(3):
            self.account.attempt_login(password="wrong")
        
        result = self.account.attempt_login(password="correct_password")
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "account_locked")

    def test_account_unlocks_after_15_minutes(self):
        """15 分鐘後應自動解鎖"""
        for _ in range(3):
            self.account.attempt_login(password="wrong")
        
        self.account.advance_time(minutes=15)
        result = self.account.attempt_login(password="correct_password")
        self.assertTrue(result.success)

if __name__ == "__main__":
    unittest.main()
```

執行（此時會全部紅，因為 `login.py` 還不存在）：
```
$ python -m pytest tests/test_login.py
FAILED tests/test_login.py::TestLoginLockout::test_account_locks_after_three_failures
FAILED tests/test_login.py::TestLoginLockout::test_locked_account_rejects_correct_password
FAILED tests/test_login.py::TestLoginLockout::test_account_unlocks_after_15_minutes
3 failed, 0 passed in 0.12s
```

接著實作 `UserAccount`，讓測試通過（綠）。

### BDD（Gherkin，給 Cucumber 跑）

```gherkin
# features/login_lockout.feature
Feature: 登入失敗鎖定機制

  Scenario: 三次失敗後帳號鎖定
    Given 使用者 "alice" 的帳號是正常的
    When  "alice" 以錯誤密碼嘗試登入 3 次
    Then  "alice" 的帳號應處於鎖定狀態

  Scenario: 鎖定期間正確密碼也被拒絕
    Given "alice" 的帳號已因連續失敗而被鎖定
    When  "alice" 以正確密碼嘗試登入
    Then  登入應失敗，原因為 "account_locked"

  Scenario Outline: 不同等待時間的解鎖行為
    Given "alice" 的帳號剛被鎖定
    When  等待 <分鐘> 分鐘後嘗試以正確密碼登入
    Then  登入 <結果>

    Examples:
      | 分鐘 | 結果 |
      | 14   | 應失敗（尚在鎖定期） |
      | 15   | 應成功（鎖定已解除） |
      | 30   | 應成功（鎖定已解除） |
```

### MDA（UML 狀態圖概念，以偽文字描述）

```
[StateMachine: UserAccount]
  State: Normal
    Transition: on login_failure [failure_count < 3] -> Normal / failure_count++
    Transition: on login_failure [failure_count == 3] -> Locked / lock_timestamp=now()
  
  State: Locked
    Transition: on login_attempt [now() - lock_timestamp < 15min] -> Locked / return REJECTED
    Transition: on login_attempt [now() - lock_timestamp >= 15min] -> Normal / failure_count=0

[CodeGen]
  Target: Java Spring Boot
  Template: stateMachine2Spring.mtl
```

（此處 MDA 工具鏈理論上能從這個狀態機模型生成 Java 程式碼，但實務上問題重重。）

### SDD Spec（Markdown，給 Kiro 或 Spec Kit 的 LLM 讀）

```markdown
# Feature: 登入失敗鎖定機制

## User Stories

**Story AUTH-04**  
As a 系統管理員  
I want 帳號在連續失敗後自動鎖定  
So that 暴力破解攻擊被阻斷  

## Acceptance Criteria（EARS 格式）

- WHEN 使用者連續登入失敗達 3 次，THE SYSTEM SHALL 將該帳號標記為鎖定狀態並記錄鎖定時間戳。
- WHILE 帳號處於鎖定狀態且距鎖定時間未滿 15 分鐘，WHEN 任何登入嘗試發生，THE SYSTEM SHALL 拒絕登入並回傳錯誤碼 AUTH_ACCOUNT_LOCKED。
- WHEN 距帳號鎖定時間達 15 分鐘，THE SYSTEM SHALL 自動解除鎖定並將失敗計數重置為 0。
- IF 管理員手動解除帳號鎖定，THEN THE SYSTEM SHALL 立即允許該帳號進行登入嘗試。

## 邊界條件

- 15 分鐘計時從**第三次失敗**的時間戳起算，不是從最後一次嘗試。
- 鎖定期間不應更新失敗計數（不應累積到 4、5 次）。
- 密碼重設成功不自動解鎖（需另一個流程）。
```

這四種記法，**BDD Gherkin 和 SDD Spec 看起來最接近**，但用途不同：Gherkin 要求有步驟定義（step definitions）才能跑；SDD Spec 是讓 LLM 理解並生成所有程式碼（包括測試）。

---

## 踩雷集錦

### 雷 1：把 BDD 框架（Cucumber）當成 SDD 工具

**錯誤直覺**：「我們已經在用 Cucumber 寫 Gherkin 了，這就是 SDD。」  
**正確認識**：BDD + Cucumber 是讓測試框架執行規格，規格必須對應 step definitions，程式碼仍由人類寫。SDD（AI 世代的意義）是讓 LLM 讀規格、生成程式碼（包括測試）。前者的規格是**測試驅動工具**；後者的規格是**代理驅動工具**。兩者可以互補，但不互相替代。

### 雷 2：認為 SDD 只是「MDA 2.0」，因此注定失敗

**錯誤直覺**：「MDA 就是從模型生成程式碼，SDD 也是從規格生成程式碼，所以 SDD 會重蹈覆轍。」  
**正確認識**：MDA 的生成器是**語法映射**——模板引擎把 UML 元素對應到程式碼樣板，遇到超出樣板範圍的需求就失敗。LLM 做的是**語意理解**——它讀散文描述的意圖，能應對模糊和非標準情況。這個差異是質的，不是量的。當然，「LLM 能理解意圖」本身是一個賭注，不是保證。

### 雷 3：以為 TDD 和 SDD 衝突，只能選一個

**錯誤直覺**：「SDD 是讓 LLM 生成測試，所以不需要再跑 TDD 了。」  
**正確認識**：TDD 和 SDD 在不同抽象層次工作。SDD pipeline（例如 Kiro 的 tasks.md）通常預期每個任務**包含**測試。LLM 生成的測試品質仍需人工審查，而 TDD 的 Red-Green-Refactor 節律可以讓你驗證 LLM 的實作是否真的符合規格意圖。

### 雷 4：文學編程給 SDD 的啟發只有「散文優先」

**錯誤直覺**：「文學編程就是先寫文件，SDD 也是先寫文件，所以是同一件事。」  
**正確認識**：文學編程的散文**解釋**人類寫的程式碼；SDD 的規格**生成**LLM 寫的程式碼。Knuth 的工程師同時是散文作者和程式碼作者；SDD 的工程師角色向「僅作散文作者」移動。這個角色轉移的副作用是：如果 LLM 生成的程式碼有問題，你需要有能力審查你沒有寫的程式碼，Knuth 的世界裡不存在這個問題。

### 雷 5：把「規格即執行」（BDD 意義）和「規格驅動生成」（SDD 意義）混用

**錯誤直覺**：「SDD 的 spec 是可執行的，就像 Gherkin feature 檔案。」  
**正確認識**：corrections.md 明確指出 SDD 有兩個競爭意義：（1）BDD/ATDD 世系的「可執行規格」，由測試框架直接跑；（2）2025 AI 工具世系的「規格驅動生成」，由 LLM 讀規格生成程式碼。2025 工具（Spec Kit、Kiro）借用了前者的語言（「executable」），但實際上是後者的做法。混淆這兩個意義會導致對 SDD 工具能力的錯誤期待。

> 關於 SDD 兩個意義的完整論述，見 [Ch 22 兩種「規格驅動」：可執行規格 vs 規格再生成](./22-two-meanings-of-spec-driven.md)。

---

## 進階延伸

### Specification by Example：BDD 和 SDD 的橋梁

Gojko Adzic 的《Specification by Example》（Manning，2011 年）系統化了「用具體例子做規格」的做法，把 BDD 的 Given-When-Then 推到整個交付流程。書裡的「活的文件（living documentation）」概念——規格自動和程式碼保持同步——直接預示了 SDD 對「規格為唯一真相來源」的主張。差異是 Adzic 的工具是 Cucumber/FitNesse，同步靠測試跑通；SDD 的工具是 LLM，同步靠重新生成。

### Event Storming：規格前的探索

MDA 和早期 BDD 都假設你**已經知道**你要規格化什麼。Event Storming（Alberto Brandolini，約 2013 年）是一種工作坊技術，讓你在寫規格之前先找出你不知道的事。SDD pipeline（特別是 Spec Kit 的 `/speckit.clarify` 和 Kiro 的 requirements 生成）都假設前期有類似 Event Storming 的探索。

> 詳見 [Ch 21 Event Storming 工作坊](./21-event-storming.md)。

### PlatformIndependent vs PlatformSpecific：MDA 留下的有用概念

MDA 失敗了，但它的 PIM/PSM 分層概念沒有完全死去——它演化成了現代架構的「領域層 vs 基礎設施層」分離，也回響在 DDD 的 Bounded Context 裡。SDD 的 spec 在某種意義上是 PIM：描述意圖而不指定框架；LLM 生成的程式碼是 PSM：在某個具體技術棧上實現。這個類比（[版本依賴，解釋框架仍有爭議]）幫助理解 SDD 的抽象層次。

---

## 動手練習

以下練習分兩個難度：

### 練習 25-A：家族樹填空（概念理解，不需跑程式）

拿一張紙，畫出以下五個方法的「血緣關係圖」：TDD、BDD、MDA、文學編程、SDD。  
對每條連線標注：  
- 繼承什麼（+）  
- 反對什麼或解決什麼問題（→fix）  
- 完全不同之處（≠）  

完成後，試著用一句話說出 SDD 和 TDD 最核心的一個差異。

### 練習 25-B：把一個現有 TDD 測試改寫成 SDD spec（需要能跑程式碼）

取你現有專案裡的任意一個 unit test 檔案（或用練習 25-A 的登入鎖定範例）。

步驟：  
1. 讀那個測試，用**自然語言**（不用 Gherkin，就是中文/英文散文）描述它測的行為，寫成 Markdown 的 Acceptance Criteria。  
2. 把你寫的 Markdown spec 貼給你使用的 AI 編碼助理，要求它根據 spec 生成實作。  
3. 比較它生成的實作和原來的測試，找出三個差異（可能是邊界條件、命名、錯誤處理）。  
4. 問自己：這三個差異是因為「你的 spec 不夠精確」，還是「LLM 的預設假設和你不一樣」？  

**預期踩的雷**：你的 spec 裡很可能遺漏了某個邊界條件，而原來的測試是靠你的隱性知識寫出來的。這個差異就是 SDD 的核心挑戰。

---

## 本章重點整理

- **TDD** 讓測試驅動設計，在函數層級運作；**BDD** 讓行為描述驅動測試，引入自然語言（Given-When-Then）；**MDA** 嘗試從 UML 模型生成程式碼，多半失敗；**文學編程** 要求散文和程式碼交織，意圖主導。
- **SDD（2025 AI 世代）** 繼承 BDD 的「行為描述優先」和文學編程的「散文主導」，把 MDA 的「從模型生成程式碼」夢用 LLM 重新嘗試。
- MDA 失敗的根本原因是「生成器只做語法映射，不理解意圖」。SDD 的賭注是「LLM 能理解意圖」。這個賭注尚未充分驗證（查證日期 2026-06-30）。
- BDD 的 spec（Gherkin）是讓**測試框架**執行的；SDD 的 spec（Markdown）是讓**LLM 代理**執行的。兩者「可執行規格」的意義不同。
- 「SDD 是 MDA 的轉世」是批評者的論點，也是 SDD 支持者需要嚴肅回應的問題，不能迴避。
- 這四條血脈有一個共同問題：**意圖和實作的同步**。BDD 靠測試跑通解決；MDA 沒解決；文學編程靠人工維護；SDD 靠（重新）生成。

---

## 自我檢核

- [ ] 我能用自己的話解釋 TDD 和 SDD 的「精確度載體」有何不同，不翻書也說得出來。
- [ ] 面試被問「SDD 跟 BDD 有什麼差別」，我有一個清晰的兩句話答案。
- [ ] 我能說出 MDA 失敗的兩個主要原因，以及 SDD 為何認為它能避開這兩個原因。
- [ ] 我能解釋文學編程裡「散文解釋程式碼」和 SDD 裡「規格生成程式碼」的方向差異。
- [ ] 我知道 corrections.md 對「SDD 的兩個意義」的澄清，以及為什麼不能把 BDD 工具直接稱為 SDD 工具。

---

## 延伸閱讀

**1. Sean Grove，〈The New Code〉（AI Engineer World's Fair 2025）**  
- 連結：https://www.youtube.com/watch?v=8rABwKRsec4  
- 讀什麼：Grove 的「shred the source, version-control the binary」類比，以及 OpenAI Model Spec 作為活規格的例子。本章把 SDD 放在家族樹上，Grove 這場演講是家族樹最新的主幹。引言來自社群謄本（lawwu.github.io），非官方逐字稿。

**2. Dan North，〈Introducing BDD〉（Better Software 雜誌，2006 年 3 月）**  
- 連結：https://dannorth.net/blog/introducing-bdd/  
- 讀什麼：BDD 的思想起源，Given-When-Then 的 ATM 範例，以及「我在建一個分析過程本身的通用語言」那段——這個說法和 SDD 的「規格即通用語言」遙相呼應。

**3. 〈Specification by Example〉Wikipedia 條目**  
- 連結：https://en.wikipedia.org/wiki/Specification_by_example  
- 讀什麼：BDD 和 ATDD 的譜系，FitNesse / Cucumber / Gherkin 的位置，以及「SDD 的 older lineage」到底包含哪些工具。

**4. 〈Literate programming〉Wikipedia 條目 + Knuth 原始論文**  
- 連結：https://en.wikipedia.org/wiki/Literate_programming  
- 讀什麼：Knuth 的 `tangle` / `weave` 機制，和 SDD「規格→程式碼」的方向對比。Knuth 原文（cs.stanford.edu/~knuth/lp.html）更深但更長。

**5. 〈Model-driven architecture〉Wikipedia 條目**  
- 連結：https://en.wikipedia.org/wiki/Model-driven_architecture  
- 讀什麼：MDA 的 PIM/PSM 層次、Executable UML 的野心，以及失敗的歷史記錄。讀完再想「LLM 是否真的解決了生成器問題」，這個問題的答案本章沒有給，也給不了。

**6. 〈Spec Driven Development: When Architecture Becomes Executable〉（InfoQ，2025）**  
- 連結：https://www.infoq.com/articles/spec-driven-development/  
- 讀什麼：InfoQ 的 SDD 定位文章，直接說「SDD 更像架構模式而非 TDD 那樣的方法論」，並點名 MDA 的類比。閱讀其「Why SDD ≠ TDD」段落，和本章的表格對照。

**7. Cucumber 官方 BDD 歷史頁面**  
- 連結：https://cucumber.io/docs/bdd/history/  
- 讀什麼：JBehave（2003）→ RSpec（2005）→ Cucumber 的完整演化，以及 Eric Evans 的通用語言如何影響了 Given-When-Then 的設計。是本章提到 BDD 工具鏈的一手文獻。

---

本章把四條血脈理清楚了，但有一個問題刻意留著沒答：如果 SDD 的所有問題都能回應，為什麼還有人不信？下一章系統整理懷疑論者的最強論證。

→ [Ch 26 懷疑論者的最強論證](./26-skeptics-case.md)
