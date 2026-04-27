# Ch 5 — 推論系統：Hilbert、自然演繹、Sequent

> 目標：知道有哪些**形式化的 proof system**，它們各自怎麼運作，為什麼有三套以上。本章偏邏輯學史/概念，但必要 — Ch 6 的 resolution 是這條線上的**第四種** proof system，你得先看過前面三種才知道它的取捨。

## 什麼是推論系統

SAT solver 回答「這個公式 SAT 嗎」。但邏輯學更本源的問題是：「從一組前提 `Γ` 出發，能不能**推出** 結論 `φ`？」

```
Γ ⊢ φ          讀作「從 Γ 可以證明 φ」
```

能與否取決於你定的 **proof system**：一組規則，告訴你什麼步驟合法。不同系統對同一個 `Γ ⊢ φ` 可能都對、但 proof 的長相完全不同。

好的 proof system 兩個性質：

- **Soundness（可靠性）**：能證出的都是真的。`Γ ⊢ φ` ⇒ `Γ ⊨ φ`
- **Completeness（完備性）**：所有真的都能證。`Γ ⊨ φ` ⇒ `Γ ⊢ φ`

`⊢` 是 **syntactic**（靠規則推）、`⊨` 是 **semantic**（靠 valuation 成立）。**兩個哲學上完全不同的箭頭**，靠 soundness + completeness 綁在一起。命題邏輯這兩個性質都有，謝天謝地。

## 第一種：Hilbert 系統（公理化）

Hilbert 1920s 的風格：**幾條 axiom + 一條推論規則**，剩下全部你自己推。

典型的 Hilbert 系統 3 條 axiom schema + 1 條 rule：

```
(A1)  φ → (ψ → φ)
(A2)  (φ → (ψ → χ)) → ((φ → ψ) → (φ → χ))
(A3)  (¬φ → ¬ψ) → (ψ → φ)

(MP)  Modus Ponens：從 φ 和 φ → ψ 導出 ψ
```

**硬派程度：十星**。要證 `p → p` 都要搞 5 步（把 A1、A2 套進去湊 MP）。實務上沒人用 Hilbert 做證明，但它的**優點**是系統本身極簡，用來**元證明**（meta-theorem，例如證明 soundness/completeness 本身）最乾淨。

你讀形式邏輯教科書看到詭異的 5-step `p → p` proof，就是 Hilbert。Claude Shannon、Gödel 的 paper 都這種風格。

## 第二種：自然演繹（Natural Deduction）

Gentzen 1934 不滿 Hilbert，他說：**人類推理不是套公理，是用 rule**。於是他設計了 Natural Deduction：每個連接詞配 **introduction 規則** 和 **elimination 規則**。

| 連接詞 | Introduction | Elimination |
|---|---|---|
| `∧` | 從 φ、ψ 得 φ ∧ ψ | 從 φ ∧ ψ 得 φ（或 ψ） |
| `∨` | 從 φ 得 φ ∨ ψ | 從 φ ∨ ψ、φ→χ、ψ→χ 得 χ |
| `→` | 假設 φ，推出 ψ，則得 φ → ψ（**卸掉假設**） | 從 φ、φ → ψ 得 ψ（= MP） |
| `¬` | 假設 φ，推出 ⊥，則得 ¬φ | 從 φ、¬φ 得 ⊥ |

這套規則寫起來像數學家的證明，所以叫「自然」。**Fitch-style** 是常見的表現形式：

```
1.  p → q        premise
2.  q → r        premise
3.  | p          assumption
4.  | q          → elim  (1, 3)
5.  | r          → elim  (2, 4)
6.  p → r        → intro (卸掉 3 的假設)
```

左邊那條豎線代表「這個範圍內 `p` 是假設」，到 6 收回來。**這是所有 Lean、Coq、Agda 這類 proof assistant 的底層**。你用 `intro`、`apply` 這些 tactic，其實就是在按 Natural Deduction 規則走。

## 第三種：Sequent Calculus

還是 Gentzen，同年（1934）。他覺得 Natural Deduction 有個問題 — 規則不對稱（intro/elim 方向不一致）— 於是設計了 Sequent Calculus：**所有規則都在同一種形式** `Γ ⊢ Δ` 上操作。

```
Γ ⊢ Δ    讀作「由 Γ 中所有公式出發，至少能導出 Δ 中某一個公式」
```

典型規則（只示範幾條）：

```
       Γ, φ ⊢ Δ          Γ, ψ ⊢ Δ
       ─────────────────────────────   (∨-L)
           Γ, φ ∨ ψ ⊢ Δ

       Γ ⊢ φ, Δ
       ──────────   (∨-R 的一邊)
       Γ ⊢ φ ∨ ψ, Δ
```

每個連接詞有 **左規則** 和 **右規則**（而非 intro/elim）。漂亮在：**所有規則都 purely syntactic、完全對稱**。

Sequent Calculus 的分支裡有一條 **Cut rule**：

```
       Γ ⊢ φ, Δ     Γ', φ ⊢ Δ'
       ───────────────────────   (Cut)
           Γ, Γ' ⊢ Δ, Δ'
```

讀作「如果 Γ 能推出 φ，Γ' 加上 φ 能推出別的，那不借 φ 也行」。Gentzen 的著名結果 **cut-elimination theorem**：**任何用 Cut 的 proof 都能改成不用 Cut 的 proof**（但可能指數爆炸）。

**Cut-elimination 為什麼重要？** 它保證「只用純粹的 left/right rule」就是完備的。在 proof search 的世界，這把搜索空間變成可控 — **這就是自動化 prover 的理論根基**。

## 為什麼有三套？

| 系統 | 強項 | 弱項 | 誰用 |
|---|---|---|---|
| **Hilbert** | 元邏輯證明最乾淨 | 人類寫 proof 噁心 | 教科書的證明 |
| **Natural Deduction** | 貼近人類思維 | 規則不對稱，proof search 難 | proof assistant（Lean、Coq） |
| **Sequent Calculus** | 對稱、cut-elimination | 規則多、初學陡峭 | 自動化 theorem prover、邏輯學研究 |

**三個都 sound & complete**，能推的 theorem 一模一樣。差別只在 **proof 本身的形狀** 和 **搜索 proof 的難易度**。

## SAT solver 走的是第四條路：refutation + resolution

以上三種系統都是 **forward proof**：從 `Γ` 出發，套規則，一路推到 `φ`。對於找證明，這個方向極度困難 — **分支因子** 極大。

SAT solver 換個方向：

1. 要證 `Γ ⊨ φ`，等價於證 `Γ ∪ {¬φ}` **unsatisfiable**（`φ valid ⇔ ¬φ unsat`，Ch 2 講過）
2. Unsatisfiability 靠 **resolution**（只有一條規則）結合 **systematic search**
3. 產生 **空 clause** 就是證明結束

這條路 **搜索空間更小、規則更機械化**，適合電腦。Ch 6 把 resolution 講透。

## 你該記什麼

短期你不會手證 theorem，這章**不要求你會寫 Hilbert / Natural Deduction / Sequent proof**。但你要：

1. **聽到 `⊢` 和 `⊨` 能區分**（syntactic vs semantic）
2. **看到 Sequent 風格的 rule 表能讀懂**（SMT 論文會出現）
3. **理解為什麼 SAT 走 resolution / refutation 而不走這三種**

## 歷史插曲：Gentzen 跟 Gödel

Gentzen 1934 年同時發明 Natural Deduction 和 Sequent Calculus，23 歲。之後他用 Sequent Calculus 證明了 **Peano 算術的 consistency**（1936），繞開 Gödel 第二不完備性定理（靠超限歸納、不在 Peano 內部）。這是邏輯學的里程碑。

Gentzen 二戰末死於蘇聯戰俘營，34 歲。如果他活下來，這門學科可能不一樣。

這個人值得在你腦袋裡留個位置 — 下一章 resolution 也是從他的 cut-elimination 思想延伸出來的（Robinson 1965）。

## 動手練習

1. **Natural Deduction 一題**：證 `p → (q → p)`。提示：假設 `p`，假設 `q`，結論 `p` 就在 context 裡。用 `→ intro` 關兩層。
2. **Sequent 一題**：推 `⊢ p ∨ ¬p`（排中律）。提示：從 `p ⊢ p` 出發，用 `¬-R` 和 `∨-R`。
3. 說服自己：`p → q` 和 `¬p ∨ q` 是同一件事。用真值表驗證，然後想想 Tseitin clause 模板為什麼長那樣。

（這章的練習有點放水，因為真正重要的推理系統是下一章的 resolution。）

## 自我檢核

- [ ] 分得清楚 `⊢`（syntactic）和 `⊨`（semantic）
- [ ] 說得出三種 proof system 各自的風格（axiom-heavy、intro/elim、left/right rule）
- [ ] 知道 soundness 和 completeness 的定義與方向
- [ ] 聽得懂「cut-elimination」這個詞的大意
- [ ] 理解為什麼 SAT solver 走 refutation + resolution 而不是這三種

下一章是這個 Part 最硬的一章 — **Resolution**。它是 SAT solver 唯一用的 inference rule，但威力強到能證出命題邏輯的所有 UNSAT。理解 resolution = 理解 CDCL 的核心。

→ [Ch 6 — Resolution 與 refutation 完備性](./06-resolution.md)
