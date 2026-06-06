# 練習 F — 為一個真實任務寫導入評估 + 落地計畫

> **目標**：把 Part 7 的三章收斂到一份**會動的決策工具**上——拿你工作裡一個**真實**任務，先用 [Ch 41](./41-when-to-agentify.md) 的五維度判斷「**該不該上**」（含硬門檻），再用 [Ch 42](./42-gradual-rollout-trust.md) 的信任階梯定出「**從哪一格起跑、最高能爬到哪、升級要看什麼證據**」，最後用 [Ch 43](./43-agents-in-team-workflow.md) 的責任閘確認「**進團隊管道前的硬條件**」。完成後你會有一個能力：拿到任何一個「要不要交給 agent」的問題，你能在三十分鐘內產出一份**有框架、擋得住硬傷、別人看得懂**的導入評估＋落地計畫——而不是憑感覺說「這個應該可以吧」。

> **環境**：Python 3.11，純標準庫，複製即跑。這題**不呼叫任何 API、不跑 agent**——它是一個**決策輔助工具**，把你對任務的判斷（五維評分、團隊事實）變成結構化的 go/no-go + 起跑層級 + 進管道檢查。重點是「框架」而非「程式」，程式只是逼你把判斷講清楚、別漏項。

## 背景與動機

Part 7 前三章各給了你一個閘：

- **Ch 41 `agentify_score`**：五維度評分 + 硬門檻（gate_flags），回答「**該不該上**」。
- **Ch 42 `autonomy_gate`**：用 eval 證據決定「**能不能升到下一個自主層級**」。
- **Ch 43 `accountability_gate`**：輸出進團隊管道前的硬條件，回答「**能不能進管道**」。

但真實導入決策**不是只跑其中一個**——它是一條鏈：先判斷該不該上（Ch 41），上了之後從哪一格起跑、天花板在哪（Ch 42），以及這個任務在你團隊裡的問責與審查路徑長怎樣（Ch 43）。三者**互相約束**：一個「不可逆 ×低容錯」的任務（Ch 41 硬門檻擋下），就算你很想讓它全自主，Ch 42 的天花板也只能到 L1、Ch 43 的責任閘還會因為「不可逆又沒 rollback」再擋一次。**這三道防線是設計來互相補位的**，這題就是逼你把它們串成一條完整的評估鏈，套到一個你真懂的任務上。

最常見的失敗不是「框架不會用」，而是**跳過框架直接拍腦袋**：看到一個任務「感覺能自動化」就開始寫 prompt，沒問過「驗得了嗎」「出錯賠得起嗎」「出事誰負責」。這題的價值在於：用一個你**真實**的任務，把這些問題一個一個問完，並親眼看到框架在哪一步把你的樂觀擋了下來。

## 任務規格

做一個 `adoption_assessment.py`，把三章的判斷串成一條鏈。輸入是你對**一個真實任務**的描述（五維評分 + 團隊事實），輸出是一份結構化的導入評估 + 落地計畫。

**輸入：一個 `TaskProfile`**
- **五維評分**（Ch 41，各 1–5）：`verifiability`、`reversibility`、`tool_coverage`、`fault_tolerance`、`frequency`。
- **團隊事實**（Ch 43）：`domain`（用來查 owner）、`has_human_baseline`（有沒有人類決策可當影子模式對照組）、`goes_through_review`、`has_rollback_plan`。

**輸出：三段式報告**

1. **該不該上（Ch 41）**：算總分、列出觸發的硬門檻（gate_flags），給 verdict：
   - 有 flag → **先解硬門檻**（硬門檻優先於總分，Ch 41 核心論點）。
   - 無 flag 且分數夠高 → 適合。
   - 無 flag 但分數中等 → 邊緣，列出要補強的弱維度。
   - 分數太低 → 別上。

2. **落地計畫（Ch 42）**：只有在第 1 段「沒有 flag 擋死」時才產出——
   - **起跑層級**：有人類對照組 → 從 L0 影子起跑（先零業務副作用收證據）；沒有 → 從 L1 起跑。
   - **自主天花板**：這個任務**最高**該爬到哪一格（不是現在在哪，是上限）。由任務本質決定，尤其是可逆性與容錯度：不可逆 ×低容錯 → 封頂 L1；可驗、可逆、容錯、頻率四維皆高 → 可到 L3；其餘 → L2。
   - **升級要看的證據**：點名 Ch 42 的門檻（含樣本量、回歸），低容錯任務門檻要更嚴。

3. **進團隊管道前的硬條件（Ch 43）**：跑責任閘——有沒有 owner（查 CODEOWNERS）、走不走 review、不可逆動作有沒有 rollback。列出擋住的 blocker。

**禁止**

- 不准**跳過硬門檻直接看總分**：Ch 41 的核心是「硬門檻比總分重要」。一個總分 14 但帶「不可逆 ×低容錯」flag 的任務，verdict 必須先講 flag，不能因為「14 接近邊緣」就含糊放行。
- 不准**讓起跑層級等於天花板**：Ch 42 的靈魂是「自主程度靠證據一格一格爬」。評估產出的「起跑」必須是低層級（L0/L1），「天花板」是另一回事——把兩者混為一談就是回到「一上線就全自主」的老錯。
- 不准**第 1 段擋死了還硬產第 2、3 段的放行計畫**：有硬門檻沒解，後面的落地計畫沒有意義，要明確標「先回去解硬門檻」。
- 不准**用一個假任務**：必須是你工作裡（或你真懂的）一個具體任務。五維評分要寫得出**理由**——這題逼你面對自己任務的真實限制，編一個「五維全 5」的完美任務學不到東西。

**可選加分**

- **敏感度分析**：把某一維 ±1，看 verdict / 天花板會不會翻盤——找出你這個任務的「決定性維度」（哪一維動一下結論就變）。
- **多任務比較表**：評估 3 個你工作裡的任務，並排出表，看框架怎麼把它們排出「最該先上 / 最不該碰」的順序。
- **把評估接到 Ch 42 的 `autonomy_gate`**：當你之後真的累積了 eval 證據，把證據餵進 `can_promote`，讓「天花板」和「現在能升到哪」接起來，形成完整的「評估 → 放權」迴路。

## 期望輸出範例

關鍵是看「**三段式評估鏈** + **硬門檻擋在最前** + **起跑 ≠ 天花板**」這條主線：

```
$ python adoption_assessment.py

================ 任務：自動修掛掉的單元測試 ================
【1. 該不該上（Ch 41）】 總分 23/25
   硬門檻：無
   → 適合 agent。最弱維度：容錯度(4)、頻率(4)——可接受。

【2. 落地計畫（Ch 42）】
   起跑層級：L0 影子模式（有人類決策可當對照組，先零業務副作用收一致率）
   自主天花板：L3 全自主（高可驗 ×可逆 ×高容錯 ×高頻）
   升級證據：eval 通過率 + 足夠樣本量 + 無顯著回歸（Ch 42 門檻）

【3. 進團隊管道前（Ch 43）】 可進管道：True
   owner：dave（domain=testing）
   硬條件：全數通過

================ 任務：自動發放客訴退款 ================
【1. 該不該上（Ch 41）】 總分 14/25
   硬門檻：⚠ 不可逆 ×低容錯（reversibility=1, fault_tolerance=1）
   → 先解硬門檻，別看總分。

【2. 落地計畫（Ch 42）】 ⏸ 硬門檻未解，暫不產出放行計畫。
   （參考：即使要上，此任務自主天花板為 L1（不可逆 ×低容錯，永不開放自動執行）。）

【3. 進團隊管道前（Ch 43）】 可進管道：False
   owner：alice（domain=billing）
   blocker：不可逆動作但沒有 rollback/補償流程——出事無法收拾
```

兩個任務並排，框架的價值就很清楚：修測試一路綠燈、能爬到 L3；退款在 Ch 41 就被硬門檻攔下（要解這個 flag，得把退款**限額**讓它變可逆/低 blast，或只讓 agent **草擬**、由人按下發放），Ch 42 不產放行計畫，Ch 43 又因「不可逆無 rollback」再擋一次——**三道防線互相補位**，沒有任何一道單獨放它過。

## 如果你卡住了

1. **五維評分不知道怎麼打**：回 Ch 41 的維度定義，每一維問一個具體問題——「跑完怎麼自動知道對不對？」（可驗證性）、「做錯了能撤銷嗎、最壞波及多大？」（可逆/blast radius）、「每一步都有工具/API 能做嗎？」（工具可達）、「偶爾錯的代價多大？」（容錯）、「多常發生、值得投入嗎？」（頻率）。打分時**寫下理由**，理由寫不出來就是你還沒想清楚。
2. **「起跑層級」和「天花板」分不清**：起跑是「**現在**從哪格開始」——答案幾乎永遠是 L0 或 L1，因為你還沒有證據。天花板是「就算證據再充足，這任務**最高**值得爬到哪」——由任務本質（可逆性、容錯度）決定，跟證據多寡無關。一個能爬到 L3 的任務也得從 L0/L1 起跑。
3. **天花板的判斷規則**：最保守也最重要的一條——**不可逆 ×低容錯（兩者都 ≤2）→ 封頂 L1**，永遠要人在每個出口。另一端，**五維裡可驗證、可逆、容錯、頻率都高（≥4）→ 可開放到 L3**。中間地帶 → L2。把這寫成函式，別靠每次重想。
4. **責任閘的 owner 從哪來**：重用你團隊真實的 `CODEOWNERS` 或 on-call 輪值表（Ch 43）。這題用一個寫死的 dict 模擬——換成你團隊真實的領域→人對照。查不到 owner 本身就是一個 blocker（沒人能問責）。
5. **第 1 段擋死後第 2 段該印什麼**：別印「放行計畫」，印「⏸ 硬門檻未解，暫不產出」。可以附一句「參考：即使要上，天花板會是 LX」當前瞻，但要明確這不是放行。
6. **覺得「我的任務五維都還行，沒 flag，但我還是不確定該不該上」**：這正是框架要給你的誠實——**沒有 flag ≠ 一定要上**。第 1 段的 verdict 對「無 flag、分數中等」會給「邊緣」並列出弱維度，那是在說「可以上，但先補強這幾項、或從更低層級起跑」。框架不替你做最終決定，它幫你把決定建立在想清楚的維度上。

## 實作步驟建議

### Step 1：定義 `TaskProfile`
一個 dataclass，裝五維評分（1–5）+ 團隊事實（domain / has_human_baseline / goes_through_review / has_rollback_plan）。加 `__post_init__` 驗證五維都是 1–5 的整數（沿用 Ch 41 的做法，擋住手滑打成 0 或 6）。

### Step 2：搬 Ch 41 的計分與硬門檻
重用 Ch 41 的 `score = sum(五維)`、`gate_flags`（驗不了 / 不可逆 ×低容錯 / 缺手腳）、`verdict`（flag 優先於總分）。這段直接從 Ch 41 搬過來，是評估鏈的第一道。

### Step 3：寫 Ch 42 的「起跑層級」與「天花板」
- `recommend_start(profile)`：`has_human_baseline` → 回「L0 影子模式…」，否則回「L1 人在迴圈裡…」（回給人看的中文字串即可；要更嚴謹可回 enum 再另外做顯示對應，但本練習直接回顯示字串，和範例輸出對齊）。
- `recommend_ceiling(profile)`：不可逆 ×低容錯 → `"L1…"`；四維（可驗/可逆/容錯/頻率）皆 ≥4 → `"L3…"`；其餘 → `"L2…"`。

### Step 4：搬 Ch 43 的責任閘
重用 Ch 43 的 `CODEOWNERS` 路由 + `can_enter_pipeline`（owner / trace / review / 不可逆無 rollback）。把 `TaskProfile` 的對應欄位餵進去。注意 trace 在評估階段假設「上線時一定會配」，所以這裡用 review/owner/rollback 三項當硬條件即可（或讓 profile 也帶 `has_trace_plan`）。

### Step 5：把三段串成一份報告
`assess(name, profile)`：依序跑第 1/2/3 段，照期望輸出格式印。第 1 段有 flag 時，第 2 段印「⏸ 暫不產出」、第 3 段照常跑責任閘（因為責任閘是獨立的硬條件，正交於層級——Ch 43 強調過）。

### Step 6：用你的真實任務跑
把範例的兩個任務換成**你工作裡的**任務，誠實打分、寫理由，看框架把它判成什麼。如果結論跟你的直覺不同，想清楚是你的直覺漏了什麼，還是評分需要調整。

## 完整參考解答

**先自己寫完再看！** 這題的價值在「**親手把三章的判斷串成一條會擋人的鏈**」——尤其是「硬門檻優先於總分」和「起跑 ≠ 天花板」這兩處，最容易寫成「把總分當神諭」或「評估完直接建議全自主」的假評估。照抄會錯過「原來我那個『感覺能自動化』的任務，在第一道閘就被攔下了」的頓悟。

<details>
<summary>點開參考實作（adoption_assessment.py，純標準庫）</summary>

```python
# adoption_assessment.py — 把 Part 7 三章串成一條導入評估鏈。純標準庫，複製即跑。
# Ch 41 該不該上 → Ch 42 起跑層級/天花板 → Ch 43 進管道硬條件。
from dataclasses import dataclass

# ===== 團隊既有的領域 owner 表（Ch 43：重用 CODEOWNERS / on-call，別另造一套） =====
CODEOWNERS = {"testing": "dave", "billing": "alice", "frontend": "bob", "infra": "carol"}

@dataclass
class TaskProfile:
    # 五維評分（Ch 41），各 1–5
    verifiability: int       # 跑完能不能自動知道對不對
    reversibility: int       # 做錯能不能撤銷、blast radius 多大（分數越高＝越可逆/越小）
    tool_coverage: int       # 每一步是否都有工具/API 可達
    fault_tolerance: int     # 偶爾出錯的代價（分數越高＝越能容忍）
    frequency: int           # 多常發生、值不值得投入
    # 團隊事實（Ch 43）
    domain: str
    has_human_baseline: bool      # 有沒有人類決策可當影子模式對照組
    goes_through_review: bool     # 是否走團隊既有 review/CI
    has_rollback_plan: bool       # 不可逆動作是否有 rollback/補償流程

    def __post_init__(self):
        for f in ("verifiability", "reversibility", "tool_coverage",
                  "fault_tolerance", "frequency"):
            v = getattr(self, f)
            # 用 type(v) is int 而非 isinstance——bool 是 int 子類，
            # isinstance(True, int) 會放行 verifiability=True（當成 1），不是我們要的。
            if type(v) is not int or not (1 <= v <= 5):
                raise ValueError(f"{f} 必須是 1–5 的整數，收到 {v!r}")

# ===== 第一道：該不該上（Ch 41） =====

def score(p: TaskProfile) -> int:
    return (p.verifiability + p.reversibility + p.tool_coverage
            + p.fault_tolerance + p.frequency)

def gate_flags(p: TaskProfile) -> list[str]:
    """硬門檻：不是扣分，是『擋死』。任一觸發，總分再高也要先解。"""
    flags = []
    if p.verifiability <= 2:
        flags.append(f"驗不了（verifiability={p.verifiability}）")
    if p.reversibility <= 2 and p.fault_tolerance <= 2:
        flags.append(f"不可逆 ×低容錯（reversibility={p.reversibility}, "
                     f"fault_tolerance={p.fault_tolerance}）")
    if p.tool_coverage <= 2:
        flags.append(f"缺手腳（tool_coverage={p.tool_coverage}）")
    return flags

def weakest_dims(p: TaskProfile) -> str:
    dims = {"可驗證性": p.verifiability, "可逆性": p.reversibility,
            "工具可達": p.tool_coverage, "容錯度": p.fault_tolerance, "頻率": p.frequency}
    lo = min(dims.values())
    return "、".join(f"{k}({v})" for k, v in dims.items() if v == lo)

def verdict_should_agentify(p: TaskProfile) -> tuple[str, list[str]]:
    """回 (結論字串, flags)。flags 優先於總分——Ch 41 核心論點。"""
    flags = gate_flags(p)
    s = score(p)
    if flags:
        return ("先解硬門檻，別看總分。", flags)
    if s >= 20:
        return (f"適合 agent。最弱維度：{weakest_dims(p)}——可接受。", flags)
    if s >= 14:
        return (f"邊緣。可上，但先補強最弱維度：{weakest_dims(p)}，或從更低層級起跑。", flags)
    return (f"別上 agent（總分 {s} 偏低，價值/適配不足）。", flags)

# ===== 第二道：起跑層級與天花板（Ch 42） =====

def recommend_start(p: TaskProfile) -> str:
    # 起跑幾乎永遠是低層級——你還沒有證據。有人類對照組就先影子，否則人在迴圈裡。
    if p.has_human_baseline:
        return "L0 影子模式（有人類決策可當對照組，先零業務副作用收一致率）"
    return "L1 人在迴圈裡（沒有可當對照的人類決策，直接從逐一核可起跑）"

def recommend_ceiling(p: TaskProfile) -> str:
    # 天花板由任務本質決定，跟你累積多少證據無關。
    if p.reversibility <= 2 and p.fault_tolerance <= 2:
        return "L1（不可逆 ×低容錯，永不開放自動執行）"
    if min(p.verifiability, p.reversibility, p.fault_tolerance, p.frequency) >= 4:
        return "L3 全自主（高可驗 ×可逆 ×高容錯 ×高頻）"
    return "L2 人在迴圈上（自己做、人盯著）"

# ===== 第三道：進團隊管道前的硬條件（Ch 43） =====

def resolve_owner(domain: str):
    return CODEOWNERS.get(domain)

def can_enter_pipeline(p: TaskProfile) -> tuple[bool, list[str], str | None]:
    blockers = []
    owner = resolve_owner(p.domain)
    if owner is None:
        blockers.append(f"領域 '{p.domain}' 在 CODEOWNERS 找不到負責人——沒人能問責")
    if not p.goes_through_review:
        blockers.append("繞過了團隊既有 review/CI——agent 不該走後門")
    # 不可逆（reversibility 低）又沒有 rollback 計畫 = 出事收拾不了
    if p.reversibility <= 2 and not p.has_rollback_plan:
        blockers.append("不可逆動作但沒有 rollback/補償流程——出事無法收拾")
    return (len(blockers) == 0), blockers, owner

# ===== 串成一份報告 =====

def assess(name: str, p: TaskProfile):
    print(f"\n================ 任務：{name} ================")

    # 第 1 段
    concl, flags = verdict_should_agentify(p)
    print(f"【1. 該不該上（Ch 41）】 總分 {score(p)}/25")
    print(f"   硬門檻：{'⚠ ' + '；'.join(flags) if flags else '無'}")
    print(f"   → {concl}")

    # 第 2 段
    print(f"\n【2. 落地計畫（Ch 42）】", end="")
    if flags:
        print(" ⏸ 硬門檻未解，暫不產出放行計畫。")
        print(f"   （參考：即使要上，此任務自主天花板為 {recommend_ceiling(p)}。）")
    else:
        print()
        print(f"   起跑層級：{recommend_start(p)}")
        print(f"   自主天花板：{recommend_ceiling(p)}")
        print(f"   升級證據：eval 通過率 + 足夠樣本量 + 無顯著回歸（Ch 42 門檻）")

    # 第 3 段（責任閘獨立於層級——Ch 43 強調的正交性，所以照常跑）
    ok, blockers, owner = can_enter_pipeline(p)
    print(f"\n【3. 進團隊管道前（Ch 43）】 可進管道：{ok}")
    print(f"   owner：{owner or '（無）'}（domain={p.domain}）")
    if blockers:
        for b in blockers:
            print(f"   blocker：{b}")
    else:
        print(f"   硬條件：全數通過")

if __name__ == "__main__":
    fix_tests = TaskProfile(
        verifiability=5, reversibility=5, tool_coverage=5, fault_tolerance=4, frequency=4,
        domain="testing", has_human_baseline=True, goes_through_review=True,
        has_rollback_plan=True,
    )
    assess("自動修掛掉的單元測試", fix_tests)

    refund = TaskProfile(
        verifiability=3, reversibility=1, tool_coverage=4, fault_tolerance=1, frequency=5,
        domain="billing", has_human_baseline=True, goes_through_review=True,
        has_rollback_plan=False,
    )
    assess("自動發放客訴退款", refund)
```

</details>

**解答說明**：

- **三道閘是一條鏈，不是三個獨立工具**：`assess` 依序跑 Ch 41 → Ch 42 → Ch 43，而且**前一道的結果會改變後一道怎麼呈現**——第 1 段有 flag，第 2 段就不產放行計畫。這正是真實導入決策的樣子：你不會在「該不該上」還沒答之前，就去設計「升到哪一格」。
- **硬門檻優先於總分（Ch 41 的靈魂）**：`verdict_should_agentify` 先看 `flags` 再看 `score`。退款總分 14、看起來「接近邊緣」，但因為帶「不可逆 ×低容錯」flag，結論直接是「先解硬門檻」——不給「14 還行啦」的模糊空間。這是把「分數不是神諭」寫進程式。
- **起跑 ≠ 天花板（Ch 42 的靈魂）**：`recommend_start` 幾乎永遠回 L0/L1（你還沒證據），`recommend_ceiling` 才是「這任務最高值得到哪」。修測試**起跑 L0、天花板 L3**——這兩個是不同的數字，把它們分開正是「自主靠證據一格一格爬」的程式落實。把起跑直接設成天花板，就是回到「一上線就全自主」的事故起點。
- **天花板由任務本質決定，與證據無關**：`recommend_ceiling` 只看五維（可逆性、容錯度等），不看任何 eval 數字。因為「這任務最壞情況多嚴重」是任務的固有性質——退款不可逆 ×低容錯，**再多 eval 證據都不該讓它全自主**，封頂 L1。證據能決定的是「爬到天花板的速度」，不是「天花板本身」。
- **責任閘正交於層級（Ch 43 的論點）**：第 3 段照常跑，不管第 1 段擋沒擋。退款就算假設它過了 Ch 41，責任閘還是因為「不可逆無 rollback」擋下——**三道防線互相補位**，這是刻意的冗餘設計。一個任務要真的上線，得三道都過。
- **這版刻意省略的（真用要補）**：(1) 五維評分**靠人主觀打**——框架不會幫你判斷「這任務可驗證性是 3 還是 4」，它只保證你**打了分、且分數會被一致地用**。要更客觀可以為每一維寫 rubric（Ch 34 judge 的思路）。(2) 沒有接 `autonomy_gate` 的**真實證據迴路**——這版的「天花板」是靜態建議；真用時要把累積的 eval 證據餵進 Ch 42 的 `can_promote`，讓「現在能升到哪」動起來（見延伸挑戰）。(3) `recommend_ceiling` 的門檻（≤2、≥4）是**示意值**，跟 Ch 41/42 的分數門檻一樣，要按你組織的風險胃納調。

## 測試用例

| 步驟 | 操作 | 預期行為 | 驗證了什麼 |
|---|---|---|---|
| 1 | 跑 `fix_tests`（五維高、無 flag） | 第 1 段「適合」、第 2 段起跑 L0 /天花板 L3、第 3 段全過 | 一路綠燈的任務長怎樣 |
| 2 | 跑 `refund`（不可逆 ×低容錯） | 第 1 段「先解硬門檻」、第 2 段「⏸ 暫不產出」、第 3 段被 rollback blocker 擋 | 三道防線互相補位 |
| 3 | 把 `refund.fault_tolerance` 改成 4 | 「不可逆 ×低容錯」flag 消失、天花板從 L1 升到 L2 | 硬門檻是「兩者都低」才觸發 |
| 4 | 把某任務 `goes_through_review` 設 False | 第 3 段多一個「繞過 review」blocker | 責任閘獨立於 Ch 41/42 |
| 5 | 把某任務 `domain` 設成 CODEOWNERS 沒有的值 | 第 3 段「找不到負責人」blocker、owner 顯示（無） | 沒人問責 = 不准進管道 |
| 6 | 五維打一個 0 或 6 | `__post_init__` 拋 `ValueError` | 輸入驗證 |
| 7 | 換成你**真實**任務的五維 + 團隊事實 | 產出你那個任務的評估 | 框架套到真實場景 |

第 2、3 步是核心驗收——**硬門檻擋在最前**、**三道防線各自獨立又互相補位**。第 7 步才是這題真正的目的：把框架套到你自己的任務上。

## 延伸挑戰（加分）

1. **敏感度分析**：寫一個 `sensitivity(profile)`，把每一維輪流 ±1，印出哪些維度一動就翻盤 verdict 或天花板。找出你任務的「決定性維度」——通常它就是你導入前最該先改善的那一項（例如「把可驗證性從 2 拉到 4」可能比什麼都重要）。
2. **多任務優先序**：評估你工作裡 3–5 個任務，並排成一張表（總分 / flag / 天花板 / 可進管道），按「最該先上」排序。體會框架怎麼把「我有一堆想自動化的東西」變成「先做這個、這個別碰」的明確順序。
3. **接上 Ch 42 的證據迴路**：把 Ch 42 的 `autonomy_gate.can_promote` import 進來，給每個任務再加一組「目前累積的 eval 證據（pass_rate / n_runs / …）」，讓報告不只說「天花板 L3」，還說「**以目前證據，現在能升到 L2**」。這就把「靜態評估」變成「動態放權追蹤」——Part 7 三章的完整閉環。
4. **產出 Markdown 報告**：把 `assess` 的輸出改成寫一份 `adoption_report.md`，含三段評估 + 一張決策表，可以直接貼進團隊的 RFC 或 PR 討論。導入決策本來就該被團隊看見、被挑戰——讓評估變成可分享的文件，而不是只在你終端機裡跑一次。

## 自我檢核

- [ ] 我用的是一個**真實**任務，五維評分每一項**寫得出理由**
- [ ] 我的 verdict **硬門檻優先於總分**——帶 flag 的任務不會因為「分數還行」被放行
- [ ] 我清楚區分「**起跑層級**」（L0/L1，靠證據往上爬）和「**自主天花板**」（任務本質決定的上限）
- [ ] 我能解釋為什麼「不可逆 ×低容錯」的任務天花板封頂 L1，再多證據都不該全自主
- [ ] 我的責任閘**獨立於** Ch 41/42 的結論跑——能說出為什麼這三道防線要互相補位
- [ ] 我把框架套到自己任務後，結論若跟直覺不同，我想清楚了是直覺漏了什麼、還是評分要調

做完這題，你把 Part 7 的三章——**該不該上（Ch 41）→ 怎麼漸進放權（Ch 42）→ 怎麼織進團隊（Ch 43）**——收斂成了一個你能反覆使用的決策工具。下次再有人問你「這個要不要交給 agent」，你不再憑感覺回答，而是有一條擋得住硬傷、講得清楚、別人能挑戰的評估鏈。

這也是整門課的收束：你從「自己刻一個能跑的 harness」（Final Project）一路走到「判斷什麼時候、用什麼方式、在什麼組織條件下把它放出去」。**會刻，且知道該不該放、怎麼放**——這才是完整的 harness engineering。
