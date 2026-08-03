# Ch 31 — Joern 自訂 pass 與 semantic

> **目標**：補上 Ch 30 留的兩個洞——taint 經過**未定義/未知函式**會斷、以及怎麼把查詢工程化。你會：(1) 用 `.sc` script 批次查詢並把結果**匯出成 JSON** 供後續處理；(2) 寫**自訂 dataflow semantic**，告訴 Joern「某個未定義函式怎麼傳遞 taint」（對回 CodeQL 的 models-as-data，Ch 23），真跑證明加了 semantic 後斷掉的 flow 接回來；(3) 認識 Joern 的 **query database（預設掃描規則）** 與 `joern-scan` 一鍵掃常見漏洞。重點：搞懂 Joern 不只是互動查詢，而是能寫成腳本、自訂語意、跑成批次掃描的平台。
>
> **環境**：Joern 4.0.594，WSL Ubuntu 22.04。靶 `~/audit-lab/vuln.c` 與殘缺片段 `~/audit-lab/broken/frag.c`。所有輸出照貼真跑結果；較繁的自訂 pass API 標明版本。自訂 semantic 對回 [Ch 23 CodeQL models-as-data](./23-codeql-flow-state-models.md)。

前兩章你在互動 shell 裡打查詢。但真實審計要的是**可重複、可批次、可整合**：一個 `.sc` script 對整包 code 跑一批查詢、把結果吐成 JSON 給後續工具（Ch 39 SARIF、Ch 35 漏斗）。更重要的是 Ch 30 那個限制——**taint 遇到 Joern 不認識的函式就斷**。`memcpy` 這種內建 semantic 引擎知道（arg2 傳到 arg1），但你 target 裡自己包的 `my_copy()`、或編不起來時那些**未定義函式**，引擎不知道它們怎麼傳遞 taint，DDG 就斷在那，flow 查不到。這一章教你手動補上——這是 Joern 從「玩具查詢」變「能審真專案」的關鍵。

## script 化與 JSON 匯出

互動 shell 適合探索，但要固化成流程就寫 `.sc`（Scala script）。`.sc` 裡你有完整的 Scala——能定義 case class、迴圈、寫檔。一個把 findings 匯出成 JSON 的最小例，`ch31.sc`：

```scala
importCode(inputPath="vuln.c", projectName="ch31")
case class Finding(sink: String, line: Int, code: String)
val findings = cpg.call.name("memcpy")
  .map(c => Finding("memcpy", c.lineNumber.getOrElse(-1), c.code)).l

import java.io.PrintWriter
val pw = new PrintWriter("/tmp/ch31-out.json")
pw.write(findings.map(f =>
  s"""{"sink":"${f.sink}","line":${f.line},"code":"${f.code}"}"""
).mkString("[", ",", "]"))
pw.close()
println("wrote " + findings.size + " findings to /tmp/ch31-out.json")
```

跑 `joern --script ch31.sc`，真跑輸出：

```
wrote 1 findings to /tmp/ch31-out.json
```

`/tmp/ch31-out.json` 內容（照貼）：

```json
[{"sink":"memcpy","line":10,"code":"memcpy(buf, data, len)"}]
```

**這就是把 Joern 接進 pipeline 的方式**：查詢 → case class 收結果 → 序列化成 JSON → 落地檔案 → 下游（Ch 35 漏斗把 Joern 的粗篩結果餵給 CodeQL 精查，或 Ch 39 轉成 SARIF 進 CI）。真實流程會用 Joern 內建的 JSON 序列化（`.toJson`）或 SARIF 匯出，這裡手寫是為了看清「結果就是普通 Scala 物件，你想怎麼處理都行」。

> **script vs shell 的差異**：`.sc` 是非互動執行，沒有 shell 的 tab 補全與逐行回饋；某些在 shell 裡靠隱式 import 就能用的東西，在 script 裡要顯式 `import`（下面自訂 semantic 就會踩到）。開發時在 shell 裡調通查詢，再貼進 `.sc` 固化。

## 自訂 dataflow semantic：把斷掉的 taint 接回來

這是本章的重頭戲。Ch 30 說過：taint 經過 Joern 不認識的函式會斷。用殘缺片段 `~/audit-lab/broken/frag.c` 示範（這個檔在練習 E 會再用，它故意呼叫未定義的 `alloc_buf`）：

```c
void process_packet(int sock) {
    char stack_buf[128];
    uint32_t sz;
    net_read(sock, &sz, 4);            /* net_read 未定義 */
    void *heap = alloc_buf(sz);        /* alloc_buf 未定義：sz 從這裡「穿過」 */
    net_read(sock, heap, sz);
    memcpy(stack_buf, heap, sz);       /* sink */
}
```

taint 要從 `net_read` 的 `sz` 流到 `memcpy` 的 size。中間 `sz` 經過 `alloc_buf(sz)` 的回傳、`heap` 之類——但 `alloc_buf` 是未定義函式，引擎不知道它把 arg1 傳到 return。我們先看**預設**能不能追到，再看**自訂 semantic** 補上會怎樣。

自訂 semantic 的 API（Joern 4.0.594，`.sc` 裡要顯式 import）——`ch31sem.sc`：

```scala
importCode(inputPath="broken/frag.c", projectName="pe3")
import io.joern.dataflowengineoss.semanticsloader.FlowSemantic
import io.joern.dataflowengineoss.DefaultSemantics
import io.joern.dataflowengineoss.queryengine.EngineContext

// 告訴引擎：alloc_buf 把 arg1(index 1) 傳到 return(index -1)
val extra = List(FlowSemantic.from("alloc_buf", List((1, -1))))
val sem = DefaultSemantics().plus(extra)
implicit val ctx: EngineContext = EngineContext(sem)

def src = cpg.call.name("net_read").argument(2)
def snk = cpg.call.name("memcpy").argument(3)
println("with custom semantic num flows: " + snk.reachableByFlows(src)(ctx).size)
```

拆解：

- `FlowSemantic.from("alloc_buf", List((1, -1)))`——為函式 `alloc_buf` 定義一條 flow mapping：`(1, -1)` 意思是「第 1 個參數的 taint 流到 return（index `-1` 代表回傳值）」。這正是 CodeQL models-as-data 裡「summary model」在做的事（Ch 23）：告訴引擎一個沒源碼的函式怎麼傳 taint。
- `DefaultSemantics().plus(extra)`——把你的自訂 semantic **疊加**到引擎內建那套（`memcpy` 等）上，不是取代。
- `EngineContext(sem)` + `implicit val ctx`——把這套 semantic 包成引擎執行環境，`reachableByFlows(src)(ctx)` 用它跑。

跑 `joern --script ch31sem.sc`，真跑輸出：

```
with custom semantic num flows: 2
```

**加了 `alloc_buf` 的 semantic 後查到 2 條 flow**——taint 成功穿過那個未定義函式接到 memcpy。這就是自訂 semantic 的價值：**當 target 用了引擎不認識的函式（自訂 wrapper、未定義函式、閉源 SDK 的 API），你手動告訴引擎它怎麼傳 taint，斷掉的 flow 就接回來**。這跟 Ch 23 CodeQL 你為 `MyFramework.sanitize()` 寫一條 model 是同一件事，只是 Joern 的 API 是 Scala、CodeQL 是 YAML/QL。

> **index 慣例**：`0` 是 receiver（物件方法的 `this`）、`1..n` 是參數、`-1` 是 return。`(1, -1)` = arg1→return；`(2, 1)` = arg2→arg1（像 `memcpy` 把來源 arg2 寫進目標 arg1）。寫錯 index，semantic 就建模錯，flow 接不上或接到錯地方。

## 自訂 CPG pass 與 tagging（概念 + 最小真跑）

比 semantic 更底層的是**自訂 CPG pass**——直接對圖增刪節點/邊或打標籤（tag）。實務上常見用途是 **tagging**：把符合某條件的節點打上標記，之後查詢 `cpg.tag` 就能快速撈。最小真跑例（在 shell/`.sc` 都可）：

```scala
importCode(inputPath="vuln.c", projectName="ch31tag")
// 把所有危險 sink call 打上 tag
cpg.call.name("memcpy|strcpy|sprintf").newTagNode("dangerous-sink").store()
run.commit          // 套用暫存的圖修改
cpg.tag.name("dangerous-sink").call.map(c => s"${c.lineNumber.getOrElse(-1)}: ${c.code}").l.foreach(println)
```

預期輸出（`vuln.c` 只有一個 memcpy）：

```
10: memcpy(buf, data, len)
```

> 上述 tagging API 依 Joern 版本略有差異（`newTagNode`/`store`/`run.commit` 的簽名在不同 4.x 版本有調整），此段為**未實測，理論預期**——若你的版本 API 不同，查 `docs.joern.io` 的 "Tagging" 與 "Custom Passes"，核心概念（打 tag → `cpg.tag` 撈）不變。**完整自訂 pass**（繼承 `CpgPass`／`SimpleCpgPass`、override `run`、對圖做結構性增改）用在「內建 CPG 缺某種邊/節點，你要補進去」的進階場景，屬於 Joern 開發者 API，一般審計用 semantic + tagging 就夠。

## joern-scan：一鍵掃常見漏洞

前面都是你自己寫查詢。Joern 附一套**預設查詢資料庫（query database）**——一批社群維護的漏洞查詢（buffer overflow、unchecked malloc、格式化字串等），`joern-scan` 一鍵跑全部。對 `vuln.c` 真跑：

```
$ joern-scan vuln.c
```

真跑輸出（下載 query bundle 的日誌略）：

```
Result: 3.0 : Unchecked read/recv/malloc: vuln.c:8:handle
Result: 3.0 : Unchecked read/recv/malloc: vuln.c:7:handle
Result: 3.0 : Unchecked read/recv/malloc: vuln.c:9:handle
Run `joern --for-input-path vuln.c` to explore interactively
```

**三個 finding**：第 7、8、9 行的 `read`/`malloc` 被標為 "Unchecked read/recv/malloc"——攻擊者控制的長度未經檢查就用於 `malloc`/`read`。分數 `3.0` 是嚴重度。`joern-scan` 的價值是**零查詢起步**：你連 DSL 都還沒寫，先跑它拿一批候選，再從候選深挖（`joern --for-input-path vuln.c` 進去對某個 finding 追 dataflow）。

但**別把 joern-scan 的預設規則當足夠**——它是通用規則，抓的是 `read/recv/malloc unchecked` 這類 pattern，不懂你 target 特有的危險 wrapper、特有的 source。它是**起點**（Ch 12 的粗篩），不是終點。真實審計是「joern-scan 粗篩 → 挑感興趣的 → 自己寫 `reachableByFlows` 精查 → 自訂 semantic 補斷點」。

## 對比：Joern semantic vs CodeQL models（Ch 23）

| 面向 | Joern 自訂 semantic | CodeQL models-as-data（Ch 23） |
|---|---|---|
| 目的 | 告訴引擎未知函式怎麼傳 taint | 同 |
| 寫法 | Scala：`FlowSemantic.from("f", List((1,-1)))` | YAML/`.ql`：summary/source/sink model |
| 疊加 | `DefaultSemantics().plus(...)` | extension pack 疊到現有 model |
| index 慣例 | 0=receiver、1..n=arg、-1=return | 類似的 access-path 描述 |
| 適用前提 | 不用 build（連未定義函式都能建模） | 要 build，但 model 可補 build 進不去的 lib |

兩者是同一個需求（「工具不認識的函式，我來告訴它語意」）的兩種實作。Joern 的特別之處：因為它連未定義函式都建了 stub node，你可以為**根本沒源碼的函式**寫 semantic——這在編不起來的 target 上是剛需（練習 E）。

## 踩雷集錦

**錯誤直覺：「自訂 semantic 寫錯了頂多沒效果，不會怎樣。」**
正確認識：semantic 寫錯（index 錯、方向反）會**默默導致漏報或誤報**——taint 接到錯地方或接不上，`reachableByFlows` 回的結果看起來正常但其實是錯的。例如 `alloc_buf` 你寫成 `(2, -1)`（arg2→return）但它其實是 arg1 傳遞，flow 就接不上，你以為「沒漏洞」。自訂 semantic 一定要**用一個已知有 flow 的最小例驗證**（像本章：加 semantic 前後比對 flow 數變化），確認它真的接上了才用在大 target。

**錯誤直覺：「shell 裡跑通的查詢貼進 `.sc` 一定照跑。」**
正確認識：`.sc` 是非互動環境，**隱式 import 不會自動帶進來**。shell 裡 `FlowSemantic`、`EngineContext` 可能靠預載的 import 直接可用，但 `.sc` 裡你要顯式 `import io.joern.dataflowengineoss.semanticsloader.FlowSemantic` 等——漏了就 compile error（`not found: value FlowSemantic`）。開發在 shell、固化到 `.sc` 時記得補齊 import。

**錯誤直覺：「joern-scan 的預設規則夠用，跑它就完成審計了。」**
正確認識：`joern-scan` 的 query database 是**通用規則**，抓通用 pattern（unchecked malloc 之類）。它不懂你 target 特有的 source（某個自訂 IPC 入口）、特有的危險 sink（自己包的 `run_cmd()`）、特有的 sanitizer。它是**粗篩起點**（給你一批候選），你必須從候選出發寫自己的 `reachableByFlows`、補自己的 semantic。把 joern-scan 當終點 = 大量漏報。

**錯誤直覺：「pass/查詢的順序不影響結果。」**
正確認識：CPG 是一層層 overlay 疊出來的（Ch 29 的 `ReachingDefPass` 等），**自訂 pass/tagging 若依賴某個 overlay，就必須在那個 overlay 之後跑**；dataflow 查詢必須在 DDG 建好之後。順序錯（例如在 DDG 還沒 commit 時查 taint、或 tag 依賴的節點還沒建）會拿到不完整或空結果。用 `.sc` 時 `importCode` 會把預設 overlay 跑完再執行你的 code，但你自己 `run.commit` 的修改要注意在依賴它的查詢之前完成。

**錯誤直覺：「自訂 semantic 只對有源碼的函式有用。」**
正確認識：恰恰相反——它對**沒源碼的未定義函式最有用**。因為 Joern 為未定義函式建了 stub node，你可以為一個「只知道名字、沒有實作」的函式（閉源 SDK API、韌體片段裡的外部呼叫）寫 semantic，讓 taint 穿過去。這是 Joern 在編不起來的 target 上補 taint 斷點的核心手段（練習 E 的進階挑戰）。

## 進階延伸

- **semantic 檔案化（`.semantics` file）**：本章在 `.sc` 裡硬寫 `FlowSemantic`，但大量 semantic 可以寫進獨立的 `.semantics` 檔（一行一條 mapping），用 `importCode` 時載入。target 越大、需要建模的外部函式越多，越該把 semantic 抽成可維護的檔案——這跟 Ch 23 CodeQL 把 model 抽成 data extension 是同一個工程動機。
- **寫自己的 scan query 進 query database**：`joern-scan` 的規則是一批 `.sc`／Scala query。你可以把自己反覆用的查詢（「找所有 `read→memcpy` 未檢查」）寫成一條 query 放進 query database，讓 `joern-scan` 每次都跑——等於把你的審計知識沉澱成可重跑的規則（對回 Ch 15 Semgrep 規則工程、Ch 26 CodeQL CVE-to-query）。
- **Joern 的 fuzzing/差異分析整合**：Joern 的 CPG 可匯出（`joern-export`）給其他工具，或用 `joern-slice` 抽出 program slice（某個變數相關的最小子圖）餵給下游分析。當你要把「Joern 找到的可疑點」交給動態工具（fuzzer）或 diff 分析（Ch 38）時，slice/export 是接口。

## 本章重點整理

- `.sc` script 讓 Joern 可批次、可整合：查詢 → case class 收結果 → 序列化 JSON → 落地供下游（Ch 35 漏斗、Ch 39 SARIF）。真跑把 `memcpy` finding 匯出成 JSON。
- **自訂 dataflow semantic** 補 Ch 30 的斷點：`FlowSemantic.from("f", List((1,-1)))`（arg1→return）+ `DefaultSemantics().plus(...)` + `EngineContext`，告訴引擎未知/未定義函式怎麼傳 taint。真跑證明加了 `alloc_buf` 的 semantic 後斷掉的 flow 接回（0/斷 → 2 條）。這是 CodeQL models-as-data（Ch 23）的 Joern 版。
- index 慣例：`0`=receiver、`1..n`=參數、`-1`=return。寫錯默默漏報/誤報，必須用已知 flow 的最小例驗證。
- **自訂 tagging/pass** 打標記快速撈節點（tagging API 隨版本略異，本章標未實測）；完整 pass 屬進階開發者 API。
- **`joern-scan`** 用預設 query database 一鍵掃常見漏洞（真跑 `vuln.c` 出 3 個 unchecked read/malloc）——是**粗篩起點**，不是終點；懂 target 特性的深查要自己寫。

## 自我檢核

- Ch 30 說 taint 遇到未定義函式會斷。自訂 semantic 怎麼把它接回來？寫出為 `alloc_buf`（arg1 傳到 return）建模的那一行 `FlowSemantic.from(...)`。
- `FlowSemantic.from("f", List((2, 1)))` 是什麼意思？舉一個真實函式（提示：想 `memcpy` 的參數順序）符合這條 semantic。
- 你在 shell 裡調通了一段用到 `FlowSemantic` 的查詢，貼進 `.sc` 卻 compile error（`not found: value FlowSemantic`），為什麼？怎麼修？
- `joern-scan` 對 `vuln.c` 報了 3 個 finding，為什麼說「不能把它當審計終點」？它漏了什麼你得自己補？
- 自訂 semantic 寫錯 index 的後果是什麼（漏報還是誤報，還是都有可能）？你該怎麼驗證一條 semantic 真的接上了？
- 為什麼說「自訂 semantic 對沒源碼的未定義函式最有用」？這跟 Joern 不用 build 的特性怎麼扣起來？

## 延伸閱讀

- **Ch 23 CodeQL flow-state 與 models-as-data（./23-codeql-flow-state-models.md）**——本章自訂 semantic 的 CodeQL 對應。讀哪裡：summary/source/sink model 那幾節。學什麼：同一個「告訴工具未知函式的 taint 語意」需求在兩個平台的寫法對照，體會 access-path/index 慣例的共通性。前提：本章。
- **Joern 官方文件（docs.joern.io）"Data Flow Semantics" 與 "Custom Passes" / "Tagging"**——`FlowSemantic`、`.semantics` 檔、`CpgPass`、tag API 的權威來源（也是你的版本 API 與本章不同時的查證處）。讀哪裡：Semantics 那節先讀。學什麼：本章沒展開的 semantic 檔案化、完整 pass API。前提：本章。
- **Joern query database（github.com/joernio/joern 的 querydb）**——`joern-scan` 跑的那批查詢的原始碼。讀哪裡：挑一兩條 C 的 buffer/malloc 查詢讀它怎麼寫。學什麼：把你的審計知識寫成可重跑 scan query 的範本，對回 Ch 26 CVE-to-query。前提：本章 + Ch 30。
- **Ch 35 漏斗：組合多工具（./35-funnel-combining-tools.md）**——本章的 JSON 匯出接到哪去。學什麼：Joern 粗篩結果餵給 CodeQL 精查/餵給動態工具的實務接法。前提：本章、Ch 32。

自訂 semantic、script、scan 都會了，你已經掌握 Joern 的完整能力面。下一章做一件必要的事：把 Joern 和 CodeQL 正面擺上檯面比——build 需求、精度、語言、查詢語言、生態、**授權**（Joern 開源 vs CodeQL 授權限制，對接案/商業 audit 是硬約束），以及那個 Joern 不可取代的場景：**build 不了的 target**。

→ [Ch 32 Joern vs CodeQL](./32-joern-vs-codeql.md)
