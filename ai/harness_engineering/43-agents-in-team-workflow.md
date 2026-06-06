# Ch 43 — 把 agent 織進團隊工作流

> **目標**：學會把一個已經選好型（Ch 41）、放權層級也定好（Ch 42）的 agent，接進團隊既有的 CI、code review、on-call、support 流程——而不是讓它在流程外面自己跑。能設計清楚的「誰為輸出負責、出事誰收拾、輸出怎麼被審查」，並用一個可跑的「責任閘」把這些規則變成程式擋得住的東西。

> **環境**：本章的責任閘工具用 Python 3.11，純標準庫，複製即跑。其餘是組織與流程的設計，與語言無關。

## 為什麼需要這個？

[Ch 41](./41-when-to-agentify.md) 教你選對任務，[Ch 42](./42-gradual-rollout-trust.md) 教你逐格放權。但這兩章談的都是「agent 這一條線」。真實團隊裡，agent 從來不是孤島——它產出的 PR 要有人 review、它寫的 code 要過 CI、它做的客服回覆出包時要有人收拾、它能碰的 secret 要有人管。

最常見的失敗不是 agent 跑歪，而是**沒人想清楚 agent 在團隊流程裡的位置**：

- agent 開了 PR，但沒人覺得自己該為它負責，於是它就一直掛在那。
- agent 直接寫進 production，**繞過**了人類要走的 code review——你給人設的安全網，對 agent 失效。
- agent 出包了，oncall 被叫醒，卻發現沒有 trace、不知道它為什麼那樣做、也不知道怎麼關掉它。

這章談的是**組織層面**的織入：怎麼讓 agent 的輸出像團隊成員的輸出一樣**可歸屬、可審查、可問責**，以及怎麼用工程手段（而不是靠大家自律）確保它**走既有流程、不繞過安全網**。

## 先建立直覺：agent 是一個「需要掛靠在人身上」的貢獻者

把 agent 想成一個產出速度極快、但**不能對結果負責**的初級貢獻者。它能開 PR、能寫 code、能回工單，但當這些東西出問題時，組織問責的對象不可能是「那個 agent」——一定是某個**人**。

所以織入團隊流程的第一原則是：**每一份 agent 的輸出，都要掛靠在一個具名的人類 owner 身上。** 這個人不必逐字檢查 agent 寫的每一行（那是 Ch 42 的層級在管的），但他是「這份輸出出事時，組織去找的那個人」。

```
   人類貢獻者                          agent 貢獻者
   ┌──────────────┐                  ┌──────────────┐
   │ 寫 code       │                  │ 寫 code       │
   │ 開 PR         │                  │ 開 PR         │
   │ 對自己的 PR   │                  │ ✗ 無法問責    │ ← 缺口
   │   負責        │                  │               │
   └──────────────┘                  └──────┬───────┘
         │                                  │ 必須掛靠
         ▼                                  ▼
   走 review → CI → merge          具名人類 owner → 走同一條 review → CI → merge
                                   （owner 負責、trace 可查、不繞過安全網）
```

agent 不會取代你流程裡的任何一道關卡。它只是一個**新來的、跑很快的貢獻者**，而你要做的是讓它走**和人一樣**的關卡——而不是給它開後門。

## 三個織入的核心問題

把 agent 接進團隊流程前，先回答這三個問題。它們對應三個常見的事故來源。

### 問題一：誰為這份輸出負責？（accountability）

agent 的每一個輸出單位（一個 PR、一筆退款、一則客服回覆）都要能對應到一個**具名的人類 owner**。這不是形式主義——它決定了：

- **出事時組織去找誰**：oncall 被 agent 開的 PR 弄爆 CI 時，知道該叫醒誰。
- **review 的責任歸屬**：owner 是 review 這份輸出的人，或負責指派 reviewer 的人。
- **權限的來源**：agent 是「以誰的身分、在誰授權的範圍內」行動的。

實務上常見的做法是**按領域路由**——沿用團隊既有的 `CODEOWNERS` 或 on-call 輪值表，agent 碰到哪個領域的東西，就掛靠到那個領域的 owner。不要為 agent 另創一套問責體系，**重用團隊已經在用的那套**。一個提醒：`CODEOWNERS` 的條目可以是 team、多個 owner、甚至 email，不保證解析成「單一具名的人」。但問責要的是「出事時找得到一個會接手的人」，所以路由的**最後一哩**要落到**當班的個人或明確的 DRI**（directly responsible individual）——掛靠到一個 team 而沒人當班，等於沒掛靠。

### 問題二：它的輸出怎麼被審查？（reviewability）

agent 的輸出必須**和人類的輸出一樣可審查**，而且因為它跑得快、量更大，審查介面要更省人力：

- **像 PR 一樣可看 diff**：人能在 merge 前看到「agent 改了什麼」，而不是事後才發現。
- **每份輸出都帶 trace 連結**（[Ch 35](./35-observability.md)）：reviewer 點一下就能看到 agent「為什麼這樣做」——它看了哪些檔、呼叫了哪些工具、走了哪條推理。沒有 trace 的 agent 輸出是不可審查的黑盒。
- **審查負擔要配得上產量**：如果 agent 一天開 50 個 PR，但每個都要人逐行讀，你只是把瓶頸從「寫」搬到「審」。所以高產量場景要靠 Ch 42 的層級設計（哪些可以不逐一審）＋ 自動化檢查（CI、lint、eval）先擋一層，人只審機器擋不掉的。

### 問題三：出事了，怎麼停、怎麼收拾？（incident response）

放權給 agent 之前，團隊要先有答案：

- **怎麼停**：kill switch 在哪、誰有權按（Ch 42 的停損）。oncall 在凌晨三點要能在不讀 code 的情況下把它關掉。
- **怎麼查**：trace（Ch 35）＋ 失敗模式分類（[Ch 38](./38-failure-modes-debugging.md)）。事故報告要能回答「它做了什麼、為什麼」。
- **怎麼收拾**：可逆動作靠 rollback（Ch 42 的 blast radius 限制讓「最壞情況」很小）；不可逆動作要有補償流程（誰來退那筆錯誤的款）。
- **誰被通報**：agent 的告警要接進團隊既有的 on-call/告警管道，而不是只寫進一個沒人看的 log。

> 這三個問題不是「上線後再說」——它們是**上線的前提**。一個沒想清楚「出事找誰、怎麼停、怎麼收拾」的 agent，不該被放進團隊流程，無論它的 eval 分數多漂亮。

## 一個可跑的「責任閘」

把上面三個問題裡「可以被程式擋住」的部分寫成一個 gate：在 agent 的輸出進入團隊共用管道（開 PR、送出回覆）**之前**，先檢查它有沒有滿足團隊流程的硬性要求。這和 Ch 42 的 `autonomy_gate` 是兄弟——前者管「這個層級能不能自己執行」，這個管「執行的產物能不能進團隊管道」。

```python
# accountability_gate.py — agent 的輸出進入團隊共用管道前的硬性檢查。
# 純標準庫，複製即跑。資料是寫死的範例，真實使用時從你的 agent run 結果餵進來。
from dataclasses import dataclass

# 團隊既有的領域 owner 表（重用 CODEOWNERS / on-call 輪值，別另造一套）。
CODEOWNERS = {
    "billing":  "alice",
    "frontend": "bob",
    "infra":    "carol",
}

@dataclass
class AgentContribution:
    domain: str                 # 這份輸出碰的領域，用來路由 owner
    has_trace: bool             # 是否帶可查的 trace 連結（Ch 35）
    goes_through_review: bool   # 是否走團隊既有的 review/CI，而非直接生效
    is_reversible: bool         # 動作是否可回退
    has_rollback_plan: bool     # 不可逆動作是否有補償/rollback 流程
    autonomy_level: str         # 來自 Ch 42 的層級，如 "L1_in_the_loop"

def resolve_owner(domain: str) -> str | None:
    return CODEOWNERS.get(domain)

def can_enter_pipeline(c: AgentContribution) -> tuple[bool, list[str]]:
    blockers = []

    owner = resolve_owner(c.domain)
    if owner is None:
        blockers.append(f"領域 '{c.domain}' 在 CODEOWNERS 找不到負責人——沒人能為這份輸出問責")

    if not c.has_trace:
        blockers.append("缺 trace 連結——reviewer 無法審查「它為什麼這樣做」（Ch 35）")

    if not c.goes_through_review:
        blockers.append("繞過了團隊既有 review/CI——agent 不該走人走不到的後門")

    # 不可逆 + 沒有 rollback 計畫 = 出事收拾不了
    if not c.is_reversible and not c.has_rollback_plan:
        blockers.append("不可逆動作但沒有 rollback/補償流程——出事無法收拾")

    return (len(blockers) == 0), blockers

if __name__ == "__main__":
    # 案例一：billing 領域的可逆變更，走 review、帶 trace → 放行，掛靠到 alice
    ok1 = AgentContribution(
        domain="billing", has_trace=True, goes_through_review=True,
        is_reversible=True, has_rollback_plan=False, autonomy_level="L1_in_the_loop",
    )
    passed, why = can_enter_pipeline(ok1)
    print(f"案例一 進管道? {passed}  owner={resolve_owner(ok1.domain)}  {why}")

    # 案例二：碰一個 CODEOWNERS 沒有的領域、又繞過 review → 兩個 blocker
    bad = AgentContribution(
        domain="data_science", has_trace=True, goes_through_review=False,
        is_reversible=True, has_rollback_plan=False, autonomy_level="L2_on_the_loop",
    )
    passed, why = can_enter_pipeline(bad)
    print(f"案例二 進管道? {passed}  {why}")

    # 案例三：不可逆（發真錢退款）又沒有 rollback 計畫 → 擋下
    irreversible = AgentContribution(
        domain="billing", has_trace=True, goes_through_review=True,
        is_reversible=False, has_rollback_plan=False, autonomy_level="L1_in_the_loop",
    )
    passed, why = can_enter_pipeline(irreversible)
    print(f"案例三 進管道? {passed}  {why}")
```

跑起來：

```
案例一 進管道? True  owner=alice  []
案例二 進管道? False  ["領域 'data_science' 在 CODEOWNERS 找不到負責人——沒人能為這份輸出問責", '繞過了團隊既有 review/CI——agent 不該走人走不到的後門']
案例三 進管道? False  ['不可逆動作但沒有 rollback/補償流程——出事無法收拾']
```

重點不在這幾個欄位本身，而在**把團隊流程的隱性規則變成程式擋得住的硬條件**：沒有 owner 不准進、沒有 trace 不准進、繞過 review 不准進。這些規則靠口頭約定遲早會破，寫成 gate 才靠得住。注意這個 gate 跟 Ch 42 的層級是**正交**的——案例二就算在 L2、eval 分數再高，缺 owner ＋ 繞過 review 一樣被擋。

## 接進既有流程的幾個接點

把 agent 織入，不是發明新流程，而是把它接到團隊**已經在用**的那幾個接點上：

- **Code review / PR**：agent 開 PR，走和人一樣的 review。用 `CODEOWNERS` 自動指派 reviewer。PR 內文自動附 trace 連結與「這次跑了什麼 eval、通過率多少」。但要注意：`CODEOWNERS` 只負責**指派** reviewer，它本身**不會擋下 merge**——要讓「code owner 核可」與「禁止直接 push 主分支」真的成為硬性 gate，得靠 branch protection / rulesets 開啟 required reviews。**不要**給 agent 繞過這些保護的權限——它和初級工程師一樣，走 PR、等核可。
- **CI**：agent 的輸出進的是同一條 CI。CI 是對 agent 特別有效的安全網——它**確定性**地擋掉一整類 agent 會犯的錯（編譯不過、測試掛、lint 不過），不依賴人盯。
- **Hooks**（[Ch 30](./30-hooks.md)）：在 agent 動作前後掛 hook，做團隊政策的自動檢查（例如「碰到 `infra/` 路徑就 `ask`、不准 `allow`」）。要注意 hook 能做的是**擋下／放行／轉成 ask**；若你要的是「**一定要由 carol 本人核可**」，光靠 agent 的 hook `ask` 保證不了——那需要外部身份與審批系統，或乾脆走「PR + branch protection 要求 infra code owner 核可」這條既有路徑。hook 是把責任閘接進 agent 執行流的機制，但「指定某個具名的人核可」這種需求，通常落在 PR/審批系統那一層。
- **On-call / 告警**：agent 的異常接進團隊既有的告警管道與輪值，**不要**為 agent 另開一個沒人看的儀表板。oncall 的 runbook 要新增「agent 出事怎麼辦」一節。
- **Support / 工單**：agent 處理的工單要能無縫**升級轉人工**（escalation）——它處理不了或信心不足時，把完整脈絡交棒給人，而不是硬答或卡住。

## 對比與取捨

| 接入方式 | 問責 | 可審查性 | 風險 | 適用 |
|---|---|---|---|---|
| **agent 直接寫 production**（繞過流程） | 無 | 無（事後才知道） | 極高 | 幾乎永遠不該 |
| **agent 開 PR、走人類 review** | 清楚（PR owner） | 高（diff + trace） | 低 | 大多數寫 code 的場景 |
| **agent 自動執行、人事後抽查** | 需明確指派 owner | 中（靠 trace + 抽查） | 中 | 高頻、可逆、已有 L2 證據 |
| **agent 全自動、只在出界通報** | 需明確指派 owner + runbook | 靠 trace + 告警 | 視 blast radius | 高頻、低 blast radius、L3 |

選哪一行不是技術決定，是**團隊願意為這個任務承擔多少風險 ＋ 已經累積多少證據**（Ch 42）的結果。但無論哪一行（除了第一行），「具名 owner」和「可查 trace」都不能省。

## 踩雷集錦

1. **「給 agent 開後門，讓它直接寫 production」**：最危險的一種。你給人設了 review/CI 當安全網，卻讓 agent 繞過去——等於對你最快、最會犯統計性錯誤的貢獻者關掉了所有防護。agent 要走**和人一樣或更嚴**的流程，不是更鬆。
2. **「沒有具名 owner，輸出變孤兒」**：agent 開的 PR 沒人認領、它的錯誤沒人收拾。上線前就要把「碰這個領域 → 掛靠這個人」的路由定好，重用 `CODEOWNERS`。
3. **「告警另開一個沒人看的儀表板」**：agent 的異常要接進團隊**既有**的 on-call 管道。另開一個面板，等於沒有告警——出事時沒人會剛好在看那一頁。
4. **「agent 一天開 50 個 PR，瓶頸從寫搬到審」**：高產量沒配上自動化檢查（CI、eval）和合理的層級設計，只是把人的負擔從寫程式換成審程式。產量上去，審查介面和自動擋層也要跟上。
5. **「把 agent 當不會出錯的工具，而不是會出錯的貢獻者」**：它是統計性的、會出錯的貢獻者（Ch 42）。流程要按「這個貢獻者偶爾會出包」來設計——要有 review、要有 rollback、要有 kill switch——而不是按「工具一定對」來設計。

## 進階：再往深一層

- **agent 身分與最小權限**：agent 該用**自己的服務帳號**行動，而不是借某個工程師的個人 token。這樣它的權限可以獨立收放（least privilege——只給它任務需要的那幾個 scope），它的操作在 audit log 裡可獨立辨識，出事時也能單獨撤銷它的存取而不影響到人。借用個人 token 會讓問責、稽核、撤權全部糊成一團。
- **agent 處理的輸入是不可信的**（[Ch 36](./36-prompt-injection-security.md)）：一旦 agent 接進團隊流程、開始讀真實的 PR 內容、issue、客服訊息、網頁，這些都是**外部可控的輸入**，可能藏 prompt injection。織入流程時，「agent 能碰什麼工具、能動什麼範圍」的邊界（Ch 42 blast radius）同時也是 injection 的防線——即使被注入，它能造成的最壞情況也被鎖在邊界內。
- **把 agent 的能力包成團隊共用的 skill**（[Ch 29](./29-skills.md)）：當一個 agent 工作流在團隊裡證明有用，把它固化成可重用的 skill / 流程模板，讓整個團隊用同一套（含同樣的責任閘、同樣的 trace 規範），而不是每個人各自寫一份品質不一的 prompt。這是「個人用得好」邁向「團隊用得穩」的關鍵一步。
- **回饋迴路**：把 reviewer 否決 agent PR 的理由、被升級轉人工的工單，蒐集起來餵回 eval（[Ch 34](./34-eval.md)）當新的測試案例。團隊每天和 agent 互動產生的「它哪裡做錯了」，是最真實、最該被回收的 eval 素材。

## 動手練習

1. 寫一個**外層的 release policy** `can_release(contribution, autonomy_level)`，它**同時呼叫** Ch 42 的 `autonomy_gate` 和本章的 `accountability_gate`，並在外層多加一條跨兩者的規則：`autonomy_level == "L3_autonomous"` 的貢獻必須 `is_reversible == True`（全自主只開放給可逆動作）。重點是體會這條規則**不屬於任何單一個閘**——責任閘只管「進不進管道」、層級閘只管「能不能自己執行」，「L3 必須可逆」是**組合兩者的發布政策**。想清楚為什麼把它塞進 `accountability_gate` 會弄髒「兩個閘正交」這件事。
2. 為你在 Ch 41/42 練習選的任務，畫出它的完整織入路徑：agent 產出 → 掛靠誰（查你團隊真實的 CODEOWNERS）→ 走哪條 review/CI → trace 放哪 → 出事誰被通報、怎麼停。把缺口標出來。
3. 設計這個任務的「升級轉人工」（escalation）條件：agent 在什麼情況下該停手把脈絡交給人？交棒時要附上哪些資訊，人才能無縫接手而不用重頭查？

## 本章重點整理

- agent 是個跑很快、但**不能對結果問責**的貢獻者；織入團隊流程的核心是讓它的輸出**可歸屬、可審查、可問責**，並走和人一樣（或更嚴）的關卡。
- 上線前先回答三個問題：誰為輸出負責（具名 owner，重用 CODEOWNERS）、輸出怎麼被審查（diff + trace，產量配自動擋層）、出事怎麼停與收拾（kill switch + rollback + 接既有 on-call）。
- 把這些隱性規則寫成程式擋得住的「責任閘」——沒 owner、沒 trace、繞過 review、不可逆又沒 rollback，一律不准進團隊管道。這個閘和 Ch 42 的層級閘正交。
- 不要給 agent 開後門繞過 review/CI；用自己的服務帳號＋最小權限；把外部輸入當不可信（Ch 36）；把證明有用的工作流固化成團隊 skill（Ch 29）。

## 自我檢核

- [ ] 我能說出 agent 織入團隊流程要回答的三個核心問題，以及它們各自對應的事故來源
- [ ] 我能解釋為什麼「具名 owner」和「可查 trace」是底線，不能因為 eval 分數高就省
- [ ] 我能說出為什麼 agent 該走和人一樣的 review/CI，而不是開後門直接寫 production
- [ ] 我能區分「責任閘」（能不能進團隊管道）和 Ch 42「層級閘」（能不能自己執行）在管的是不同的事
- [ ] 我知道為什麼 agent 該用自己的服務帳號 + 最小權限，而不是借個人 token

## 延伸閱讀

每條都說清楚讀哪裡、學到什麼、前提是什麼。

### 部落格 / 技術文章

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)** — Anthropic（2024）
  - **這篇說什麼**：「保持簡單、保留人類監督」的原則延伸到團隊層面——為什麼 agent 要嵌進既有的人類審查環節，而不是取代它們。
  - **讀哪裡**：結尾談 human oversight 與 guardrail 的段落，對照本章「走和人一樣的關卡」。
  - **前提知識**：Ch 41、Ch 42 的選型與放權框架。

- **[Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)** — Anthropic Engineering
  - **這篇說什麼**：真實多人團隊怎麼讓多個 agent 協作、怎麼用 eval 與觀測撐起 production reliability、以及工程協作上踩過的坑——對照本章「織入既有流程」的實務（這篇談的是可靠性與工程協作，本章的 human accountability/owner 歸屬是它的延伸，不是它直接的主題）。
  - **讀哪裡**：談 production reliability、評估、與工程協作流程的段落。
  - **前提知識**：Ch 26（subagent）、Ch 27（multi-agent 編排）、Ch 35（observability）。

### 官方文件

- **[GitHub — About code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)** — GitHub Docs
  - **讀哪裡**：`CODEOWNERS` 檔案的語法與自動指派 reviewer 的機制。
  - **能學到什麼**：把本章「按領域路由到具名 owner」直接落到團隊真實在用的 CODEOWNERS 機制上——agent 的問責路由重用這套，不另造。
  - **前提知識**：用過 GitHub PR 流程。

### 書籍

- **《AI Engineering》— Chip Huyen（O'Reilly, 2025）**
  - **這本書的定位**：把 LLM 系統的上線、團隊協作、回饋迴路推到產品工程層面，補足本章「把 agent 接進既有工程流程」的系統視角。
  - **讀哪幾章**：談部署、監控、與把使用者回饋接回 eval 的章節，與本章「回饋迴路」最相關。

至此 Part 7 走完了「該不該上（Ch 41）→ 怎麼漸進放權（Ch 42）→ 怎麼織進團隊（Ch 43）」這條導入主線。接下來用一個真實任務，把這三章的框架實際走一遍。

→ [練習 F — 為一個真實任務寫導入評估 + 落地計畫](./practice-f-adoption-assessment.md)
