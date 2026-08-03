# Ch 8 — 理論怎麼落到工具：近似與取捨

> **目標**：收束 Part 1。前四章我們建了理論零件——lattice/dataflow（Ch 4）、IFDS 圖可達性（Ch 5）、points-to（Ch 6）、taint 四要素（Ch 7）。這章把它們**對映到本課四個主力工具實際怎麼實作與近似**：Semgrep、CodeQL、Joern、weggli。核心命題是——**每個工具都在精度軸上砍掉某些東西換擴展性，砍哪根軸決定它在哪類漏洞上漏報/誤報**。理解這點，你才能在 query 漏報時知道去開哪個 sensitivity、在 query 爆炸時知道哪個近似太精。這章不重教理論，只講「理論落地時的取捨」。

先立一個貫穿全課的判準：**沒有 sound 又 complete 又能規模化的工具**。這是理論界線（Rice 定理的實務後果），不是工具不夠好。於是每個工具都在三角形裡選位置：**精度（少誤報）、覆蓋（少漏報）、擴展性（跑得完）**——選兩個，第三個讓步。你要做的不是找「最好的工具」，是知道**手上工具讓步了哪個**。

## 四工具在精度軸上各站哪

把 Ch 4-7 的每根精度旋鈕當成一欄，四工具各自的預設立場：

| 軸 | weggli | Semgrep (taint mode) | Joern | CodeQL |
|---|---|---|---|---|
| **底層表示** | AST（tree-sitter） | AST + 輕量 dataflow | CPG（AST+CFG+PDG，Ch 3） | 關聯式 DB + QL（Datalog 風） |
| **dataflow** | 無（純結構匹配） | intra-proc 為主 | inter-proc（CPG 上） | **global（IFDS 家族）** |
| **flow-sensitive** | 無 | 部分 | 是 | 是 |
| **inter-procedural** | 無 | 有限（跨檔弱） | 有 | **有（models-as-data 補 summary）** |
| **alias / points-to** | 無 | 很粗 | 粗 | 局部 / demand-driven（Ch 6） |
| **field-sensitive** | N/A | 有限 | 視情況 | 較完整 |
| **implicit flow** | 無 | 無 | 預設無 | 預設無 |
| **主要犧牲** | dataflow 全砍 | 跨函式/alias 精度 | alias 精度換 no-build 規模 | **速度/記憶體 + 要建 DB** |
| **換來** | 極快、no-build、好寫 | 快、好寫、CI 友善 | no-build、圖查詢彈性 | 最全的 flow、最少漏報 |

一句話對照：**weggli 用結構匹配換極致速度（Ch 33）、Semgrep 用輕量 dataflow 換易用與 CI（Ch 13-17）、Joern 用粗 alias 換 no-build 的 inter-proc（Ch 29-32）、CodeQL 用建 DB 的成本換最全的 global taint（Ch 18-28）**。這張表是後面每個工具章的地圖。

## 同一個 taint 問題，四工具各自漏/誤在哪

抽象講沒感覺，給一個具體漏洞看四工具的近似怎麼分岔。情境是一條**同時需要跨函式 + alias + field-sensitive** 才追得到的真漏洞：

```c
struct req { char *data; char *meta; };
void handle(struct req *r) {
    char *p = r->data;          // r->data 是 tainted（來自 recv）
    copy_it(dst, p, n);         // 跨函式，經指標
}
void copy_it(char *d, char *s, size_t n) {
    memcpy(d, s, n);            // sink：tainted s 進 memcpy
}
```

要抓到它，分析得同時：跨進 `copy_it`（inter-proc）、把 `p` 接回 `r->data`（alias/field）、把 `s` 接回 `p`（inter-proc 參數綁定）。我用旗標模擬「開/不開某個 sensitivity」，看同一段 code 在四工具的近似下報不報（Python 3，真跑）：

```python
def analyze(intraproc_only, field_insensitive, no_alias):
    # 真相：這是真漏洞，需要 inter-proc + alias + field-sensitive 才追得到
    if intraproc_only:
        return (False, "只做 intra-procedural：taint 過 copy_it 邊界斷掉 -> 漏報")
    if no_alias:
        return (False, "不追 alias：p 與 r->data 沒接起來 -> 漏報")
    if field_insensitive:
        return (True, "field-insensitive：抓到，但整個 struct 髒 -> 對 r->meta 誤報")
    return (True, "全開：精確抓到，無此類誤報")

tools = [
    ("weggli (純語法/AST)",                    dict(intraproc_only=True,  field_insensitive=False, no_alias=True)),
    ("Semgrep taint (intra 為主)",             dict(intraproc_only=True,  field_insensitive=False, no_alias=True)),
    ("Joern (CPG, inter-proc, alias 粗)",      dict(intraproc_only=False, field_insensitive=True,  no_alias=False)),
    ("CodeQL (global taint, models-as-data)",  dict(intraproc_only=False, field_insensitive=False, no_alias=False)),
]
for name, cfg in tools:
    hit, why = analyze(**cfg)
    print(f"  [{'報' if hit else '漏'}] {name}\n        {why}")
```

真跑輸出（照貼）：

```
  [漏] weggli (純語法/AST)
        只做 intra-procedural：taint 過 copy_it 邊界斷掉 -> 漏報
  [漏] Semgrep taint (intra 為主)
        只做 intra-procedural：taint 過 copy_it 邊界斷掉 -> 漏報
  [報] Joern (CPG, inter-proc, alias 粗)
        field-insensitive：抓到，但整個 struct 髒 -> 對 r->meta 誤報
  [報] CodeQL (global taint, models-as-data)
        全開：精確抓到，無此類誤報
```

讀這張結果的方式**不是**「CodeQL 最好，用它就對」。而是：

- **weggli/Semgrep 漏** 這條，是因為它們沒做（足夠的）inter-proc taint——**但它們對「單函式內、語法特徵明顯」的 bug 又快又準**（例如「`strcpy` 到固定大小 buffer」weggli 一行 pattern 秒殺，CodeQL 要建 DB 寫 query）。工具是互補不是替代。
- **Joern 報但誤報 `r->meta`**，是它 alias/field 較粗的直接後果——你會得到結果，但要花時間 triage 掉那條假的。
- **CodeQL 精確**，代價是你得先花時間建 DB、寫 QL、忍受它慢。**天下沒有白吃的午餐在這張表上具體化了**。

這正是 Ch 35「funnel（漏斗）：組合工具」的動機——用 weggli/Semgrep 快掃粗篩，對高價值目標再上 Joern/CodeQL 深挖。單一工具的近似必然在某處漏或誤，**組合是對抗近似的唯一實戰解**。

## 為什麼懂理論能讓你 debug query

這是本章對審計實戰最直接的 payoff。你寫的 query 兩種病，各自的診斷路徑都回到理論：

**query 漏報（真 bug 沒報出來）→ 回頭問「哪個 sensitivity 沒開 / 哪條 summary 斷了」：**

```
漏報診斷樹
├─ flow 在函式邊界斷？        -> inter-proc 沒開，或某函式沒 model（Ch 5 summary edge）
│                               → CodeQL 補 models-as-data（Ch 23）；Semgrep 加 pattern
├─ flow 經過指標/struct 斷？   -> alias/field 不夠（Ch 6）
│                               → 加 isAdditionalTaintStep（Ch 7 propagation）
├─ source/sink 沒涵蓋？        -> policy 不完整（Ch 7）→ 補宣告
└─ sanitizer 誤切？            -> 假的 isSanitizer 把真 flow 切了 → 檢查 sanitizer 建模
```

**query 爆炸（誤報淹沒 / 跑不完）→ 回頭問「哪個近似太精 / 哪個集合太寬」：**

```
爆炸診斷樹
├─ source/sink 定太寬？        -> over-taint（Ch 7）→ 收窄到真攻擊面（Ch 9-10）
├─ propagation 太鬆？          -> 加 sanitizer / barrier 切掉不該傳的邊
├─ 追了不必要的 sensitivity？   -> 關掉用不到的精度（Ch 28 query performance）
└─ 格太高 / 沒收斂？           -> 分析吃爆記憶體（Ch 4 widening）→ 限制深度
```

**沒有理論，你只能瞎調 query、加 pattern 試運氣。有理論，漏報你直接定位到「summary edge 斷在 `copy_it`」，爆炸你直接定位到「source 把整個 `char*` 參數當入口所以 over-taint」**。這就是為什麼 Part 1 要先啃理論——它是你 debug 工具的座標系。Ch 12（false positive triage）、Ch 28（query performance）會反覆用這兩棵診斷樹。

## 學術 sound ≠ 工具 sound（關鍵區分）

一個容易踩死的概念坑，收在這裡當 Part 1 的封頂：

- **學術/理論 sound**：分析**不漏報**（over-approximation，寧可多報）。理論論文說某分析 sound，指的是它涵蓋所有真實 flow。
- **工具「sound」（實務用法）**：幾乎所有實用 SAST **都不是理論 sound 的**——它們忽略 implicit flow（Ch 7）、用近似的 alias（Ch 6）、對某些 construct（反射、`dlopen`、函式指標、`eval`）直接放棄。**它們是 unsound 的，這是刻意的工程選擇**（換可用性）。

於是「CodeQL 沒報 = 沒有這個 bug」是**錯的**——CodeQL 在它 model 的範圍內盡量不漏，但它 model 不到的東西（沒 model 的 API、函式指標、複雜 alias）就漏。**把工具的 unsound 當成 sound，是審計者最危險的誤判**：你以為掃乾淨了，其實只是掃過了工具的能力邊界。Ch 41（auditing antipatterns）會把這條列為頭號反模式。

## 橋接 Part 2

理論齊了，工具的近似座標也清楚了。但一切的前提是**你得先知道要找什麼 flow**——source 是哪、sink 是哪、什麼算 sanitizer。這不是理論問題，是**攻擊面建模**問題：把「找漏洞」形式化成 source/sink/sanitizer 的規格。Part 2（Ch 9 起）就從這裡開始——source/sink/sanitizer 思維（Ch 9）、攻擊面建模（Ch 10）、跨語言 sink catalog（Ch 11）、誤報 triage（Ch 12）。**Ch 7 給了 taint 的骨架，Part 2 教你怎麼把真實目標填進這副骨架**。

## 踩雷集錦

**錯誤直覺：「換更精的工具（CodeQL）就沒漏報了。」**
正確認識：更精的工具漏得少，不是不漏。CodeQL 一樣忽略 implicit flow、一樣 model 不到函式指標/反射/未宣告的 API。它把某些軸開得更全，但仍在同一個「unsound 換可用」的框架裡。換工具是換「漏在哪」，不是「不漏」。真正減漏靠**組合工具 + 人工**（Ch 35）。

**錯誤直覺：「不懂工具內部近似，只要會寫 query 就能審計。」**
正確認識：不懂近似，你 debug query 只能瞎試。漏報時你不知道是 inter-proc 沒開、alias 斷了、還是 sanitizer 誤切；爆炸時你不知道是 source 太寬還是 sensitivity 太高。**理論是 debug 的座標系**——沒有它，你在黑箱前面亂按鈕。

**錯誤直覺：「工具 sound = 學術 sound = 不漏報。」**
正確認識：實用 SAST 幾乎都刻意 unsound。「工具沒報」只代表「在它的 model 範圍與近似下沒找到」，不代表 bug 不存在。把工具的乾淨當成安全保證，是最危險的誤判。工具是把你的注意力引到可疑處的放大鏡，不是「證明無 bug」的判定器。

**錯誤直覺：「Joern/CodeQL 能跑 inter-proc，就一定比 weggli/Semgrep 強。」**
正確認識：inter-proc 貴且對某類 bug 沒必要。「單函式內語法特徵明顯」的 bug（危險 API 用法、缺長度檢查的固定模式），weggli 一行 pattern 又快又準，殺雞不用 CodeQL 的牛刀。工具強弱看 bug 類型，不是看誰的 dataflow 更全。組合用才對。

**錯誤直覺：「query 漏報就是我 query 寫錯了。」**
正確認識：可能是 query 錯，但同樣可能是**工具的近似本來就追不到那條 flow**（alias 太粗、某函式沒 model）。分清這兩者才不會白改 query——如果是工具能力邊界，再怎麼改 query 都補不回來，得換手段（補 model、換工具、人工）。診斷樹的第一步就是判斷「這是 query 問題還是工具能力問題」。

## 進階延伸

- **soundness 光譜與 "soundiness"**：學術界承認幾乎沒有真 sound 的實用工具，提出 **soundiness**——「對核心語意 sound，對某些難處（反射、`eval`、原生程式碼）明確標註放棄」。讀 *In Defense of Soundiness*（CACM 2015），它精確描述了本章「工具 sound ≠ 學術 sound」這條界線，是理解所有 SAST 誠實邊界的必讀。
- **精度 vs 擴展性的量化**：points-to 的 context-sensitivity（k-CFA 的 k）是這個取捨的實驗場——k 越大越精越慢，實務多半 k=1 或 2。看 Doop 框架的 benchmark，能直觀感受「多一層精度、慢幾倍」。
- **incremental / differential 分析**：CI 場景（Ch 17、Ch 38）只想分析 diff 動到的部分。這是另一種「近似」——不重算全程式，只算受影響的 flow。理解它為什麼 sound（或哪裡不 sound）也回到本章的 summary edge 複用（Ch 5）。

## 本章重點整理

- **沒有 sound + complete + 可規模化的工具**（理論界線）。每個工具在**精度 / 覆蓋 / 擴展性**三角選兩個，讓步第三個。你的工作是知道手上工具讓步了哪個。
- 四工具座標：**weggli**（AST，砍全部 dataflow，換極速 no-build）、**Semgrep**（輕量 intra dataflow，換易用 CI）、**Joern**（CPG inter-proc，粗 alias 換 no-build 規模）、**CodeQL**（global IFDS taint + models-as-data，建 DB 成本換最全 flow）。
- 同一條「跨函式+alias+field」漏洞：weggli/Semgrep 漏（無 inter-proc）、Joern 報但誤報（alias 粗）、CodeQL 精確（代價是慢+建 DB）。**工具互補，組合才是對抗近似的實戰解（Ch 35）**。
- **懂理論 = 有 debug query 的座標系**：漏報回到「哪個 sensitivity 沒開 / summary 斷哪」，爆炸回到「哪個近似太精 / 集合太寬」。
- **學術 sound（不漏報）≠ 工具「sound」**。實用 SAST 刻意 unsound（忽略 implicit flow、近似 alias、放棄某些 construct）。**「工具沒報」≠「安全」**，這是頭號誤判。

## 自我檢核

- 說出四工具各自砍了哪根精度軸、換來什麼。為什麼「殺雞（單函式語法 bug）」用 weggli 比 CodeQL 好？
- 對那條「跨函式+alias+field」漏洞，為什麼 weggli/Semgrep 漏、Joern 誤報、CodeQL 精確？各自的根因對到哪根軸？
- 你的 CodeQL query 漏報一條你手動確認的真 flow。用漏報診斷樹，列出你會依序檢查的三件事。
- query 誤報淹沒，跑不完。用爆炸診斷樹，說出兩個可能的過精/過寬來源，以及各怎麼收。
- 「學術 sound」與「工具 sound」差在哪？「CodeQL 掃乾淨了 = 沒有這個 bug」錯在哪？soundiness 是什麼意思？
- 為什麼「query 漏報一定是我 query 寫錯」是錯的？怎麼分辨「query 問題」與「工具能力邊界」？

## 延伸閱讀

- **Livshits et al., *In Defense of Soundiness: A Manifesto*, CACM 2015**——本章「工具 sound ≠ 學術 sound」的權威來源，定義 soundiness、列出實用工具普遍放棄哪些難處（反射、`eval`、原生碼）。全課理解工具誠實邊界的必讀，優先讀。前提：本章 + Ch 7。
- **Guarnieri et al. / Smaragdakis, Doop 框架相關論文**——points-to 精度 vs 擴展性的量化實驗場，看 context-sensitivity 的 k 怎麼換精度與速度。想把本章的取捨從定性變定量讀這個。前提：Ch 6。
- **GitHub CodeQL 官方 *Analyzing data flow* 文件 + Semgrep *taint mode* 文件**——兩個主力工具怎麼把本章的近似落成 API（local vs global dataflow、models-as-data、pattern-propagators）。當作 Part 2/3 的預習地圖。前提：Ch 5、Ch 7。
- **Sadowski et al., *Lessons from Building Static Analysis Tools at Google* (Tricorder), CACM 2018**——工業界怎麼在「誤報 vs 開發者信任」間取捨、為什麼寧可漏也要控誤報率。把本章的三角取捨放到真實工程脈絡。前提：本章。銜接 Ch 36（false positive governance）。

Part 1 到此收束：理論零件齊了、工具近似座標清楚了、debug query 的方法建立了。在把這些理論用到真實工具之前，先用練習 A 把它焊死——親手刻一個 mini taint tracker，你才真的懂 source/sink/sanitizer 在 fixpoint 上是怎麼跑的。做完再進 Part 2 的攻擊面建模。

→ [練習 A：手刻 mini taint tracker](./practice-a-mini-taint-tracker.md)
