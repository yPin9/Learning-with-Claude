# Ch 43 — 案例實況：完整 variant hunt

> **目標**：把 [Ch 42](./42-your-audit-sop.md) 那套 SOP 完整走一遍——從一個已知 bug 出發，做 root cause 分析、抽 pattern、寫 query、掃原專案與相似專案找變體、triage 命中、對一個變體構造 PoC、寫報告發 PR。重點不是給你一個乾淨的成功故事，而是把**過程中的決策與踩坑**攤開來：第一版 query 為什麼漏報、怎麼發現漏了、怎麼修；誤報從哪來、怎麼砍。失敗過程才是這章的教學核心。讀完你該有的感覺是：這條路我自己走得下來。

## 先講清楚：什麼是真做過、什麼是敘事示意

這章要誠實。全課教你別杜撰 CVE（[Ch 26](./26-codeql-cve-to-query.md) 就在講「從真 CVE 抽 query」），這章我也不會給你一個編造的 CVE 編號當真。所以我採取的做法是：

- **bug class 是真的**：我們用的漏洞形狀——**多個 user 可控的整數相乘，結果未經溢位檢查就當成 allocation size 傳給 `malloc`，隨後照較大的原始尺寸寫入**——是 C 影像 / 媒體解析庫裡最經典、被公開記錄過無數次的一類 heap overflow（CWE-190 整數溢位 → CWE-787 越界寫入）。這個形狀你在 libpng、各種 codec、圖片解析器的 advisory 裡反覆見過。
- **具體的專案、函式名、行號、命中數字是擬真的**：我用一個叫 `imgparse` 的擬真 target 把流程串起來，因為我要示範的是**方法**，不是某個特定 CVE 的考據。凡是擬真的地方我會標「（擬真）」。
- **CodeQL / Semgrep 的 query 語法、taint 建模概念、triage 的判斷邏輯是真的**：這些是可重現的，你換到任何真 target 上邏輯一樣成立。query 我寫的是**能表達正確語意的骨架**，你在真 database 上跑時 API 細節可能要按版本微調——這點我也標明。

一句話：**方法真、邏輯真、語法真；專案與數字擬真**。這樣你既學到可遷移的東西，我又沒對你撒謊說某個 CVE 是怎樣。

## Step ①②③：target、攻擊面、種子 bug

**target（擬真）**：`imgparse`，一個約 4 萬行的 C 影像解析庫，會被上層應用拿去解使用者上傳的圖片。它 build 得起來（有 CMake），語言是 C，規模中等——照 [Ch 42](./42-your-audit-sop.md) 決策樹：**build 得起來 → CodeQL 可用**；**C → weggli 縮面 + CodeQL 深挖**；**規模中等 → 值得建 database**；而且我們**有一個已知 bug 當種子 → 走變體路線**。

**攻擊面建模（[Ch 10](./10-attack-surface-modeling.md)）一句話**：外部輸入是「使用者上傳的圖片檔 bytes」，entry point 是各格式的 `*_decode()` 函式，trust boundary 是「檔案 header 裡宣告的尺寸欄位」——這些欄位是**攻擊者完全可控**的，卻常被 code 當成可信的來算 buffer 大小。這一句就框定了：**source = 從檔案 header 讀進來的尺寸欄位；危險 sink = allocation size 與 memory copy 的 size 參數**。

**種子 bug**：假設已有一份 advisory 指出 `imgparse` 的 BMP 解碼路徑有 heap overflow。我們讀 patch（root cause 分析是下一步），拿到修改前的 code：

```c
// bmp.c ── 修改前（種子 bug）
static uint8_t *bmp_decode(const uint8_t *file, size_t file_len) {
    uint32_t width  = read_le32(file + 0x12);   // header 宣告的寬，攻擊者可控
    uint32_t height = read_le32(file + 0x16);   // header 宣告的高，攻擊者可控
    uint32_t bpp    = read_le16(file + 0x1C);   // bits per pixel，可控

    size_t   pixels = width * height;            // ← 溢位點 1：width*height 溢 uint32
    uint8_t *buf    = malloc(pixels * (bpp / 8));// ← 溢位點 2：再乘 bytes/pixel，溢 size_t
    // ... 之後照「宣告的 width*height*bpp」把 pixel data 寫進 buf ...
    return buf;
}
```

## Step ④(root cause)：這個 bug 的本質是什麼

抽 pattern 前，必須先問 [Ch 41](./41-auditing-antipatterns.md) 反模式 4 教你的那句話：**這個 bug 的本質是什麼——是表面長相，還是 root cause？**

表面長相是「`width * height` 這兩個變數相乘傳給 `malloc`」。如果我照這個抽 query，就過擬合了——換個變數名、多一個維度、拆成兩步，就抓不到。

root cause 往上抽一層是這句話：

> **多個攻擊者可控的整數相乘，乘積在傳給 allocation（或 copy size）之前，沒有經過溢位檢查；而後續寫入用的是「未溢位的邏輯尺寸」，導致 alloc 小、寫入大，heap overflow。**

拆成 query 要表達的三個抽象性質：

1. **source**：≥ 2 個值，都來自「檔案 header 讀取」（`read_le32` / `read_le16` 這類），即攻擊者可控。
2. **缺失的 sanitizer**：相乘到 alloc 之間，**沒有** overflow check（沒有 `if (a > SIZE_MAX / b)` 這類、也沒有用 `__builtin_mul_overflow` / `reallocarray` 這類安全乘法）。
3. **sink**：乘積流進 `malloc` / `calloc` 的 size，或 `memcpy` 的 length。

這三條就是我們要翻成 CodeQL 的東西。注意它**完全不提** `width`、`height`、`bmp_decode` 這些字面——這是抽對了的訊號。

## Step ⑤：寫 query，第一版就漏報

建好 CodeQL database（`codeql database create`，見 [Ch 20](./20-codeql-databases.md)）。先寫 taint 的第一版——**故意把第一版寫成大多數人會寫的樣子，然後看它怎麼漏**。

```ql
// v1 ── 天真第一版（會漏報，示範用）
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking

module MulOverflowConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    // source：header 讀取函式的回傳值
    exists(FunctionCall fc |
      fc.getTarget().getName() = "read_le32" and
      source.asExpr() = fc)
  }
  predicate isSink(DataFlow::Node sink) {
    // sink：malloc 的 size 參數
    exists(FunctionCall mc |
      mc.getTarget().getName() = "malloc" and
      sink.asExpr() = mc.getArgument(0))
  }
}
module Flow = TaintTracking::Global<MulOverflowConfig>;

from DataFlow::Node src, DataFlow::Node sink
where Flow::flow(src, sink)
select sink, "tainted header value flows to malloc size: $@", src, src.toString()
```

跑原專案。**命中 BMP 那條種子？沒有。0 命中。** 天真第一版就漏了種子 bug 本身——這是最好的老師。

**為什麼漏？** 三個 bug 疊在一起，逐一拆：

- **漏因 1：source 只認 `read_le32`，種子的 `bpp` 走 `read_le16`。** 我只建模了一個讀取函式。真實 codebase 的 header 讀取有一整族（`read_le16/32`、`read_be32`、`get_u32`、巨集版本…），只認一個必漏。
- **漏因 2：`malloc(pixels * (bpp/8))` 這個 sink，taint 是否傳過乘法與除法？** CodeQL 的 `TaintTracking` 預設把算術運算當 taint 傳播（`a*b` tainted 若 `a` 或 `b` tainted），這條**理論上**該通。但——
- **漏因 3（真正的殺手）：v1 只查「單一 tainted 值到 malloc」，這太寬又太窄。** 寬在會撈到一堆單值到 malloc 的良性命中；窄在它沒表達「≥2 個可控值**相乘** + 缺溢位檢查」這個本質。而因為漏因 1 漏了 `bpp` 那條 `read_le16`，「兩個可控值相乘」這個核心條件我的 query 根本沒在檢查——中間隔一個 local 變數 `pixels`、乘法拆兩步這些對 taint 傳播其實不卡（SSA 會接上），真正的病在建模沒對齊 root cause。

**這就是第一版漏報的典型形狀**：不是 CodeQL 壞了，是我的**建模沒對齊 root cause**——source 建模不全 + 沒把「相乘」這個關鍵語意寫進 query。

## Step ⑤(迭代)：修 query 到抓得到

第二版，對著三個漏因逐一修：

```ql
// v2 ── 對齊 root cause
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking

// (修漏因1) source：一族 header 讀取函式，不寫死單一名字
predicate isHeaderRead(FunctionCall fc) {
  fc.getTarget().getName().regexpMatch("(read|get)_(le|be)?(16|32|64)")
}

// (修漏因3核心) 只關心「兩個 tainted 值相乘」的乘法運算
class TaintedMul extends MulExpr {
  TaintedMul() {
    // 左右運算元都能追溯到 header 讀取（近似：兩側都 tainted）
    isTaintedByHeader(this.getLeftOperand()) and
    isTaintedByHeader(this.getRightOperand())
  }
}

module Cfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node s) {
    exists(FunctionCall fc | isHeaderRead(fc) and s.asExpr() = fc)
  }
  predicate isSink(DataFlow::Node k) {
    // sink：alloc size 或 memcpy length
    exists(FunctionCall c |
      c.getTarget().getName() = ["malloc", "calloc", "memcpy"] and
      k.asExpr() = c.getArgument([0, 2]))   // malloc arg0 / memcpy arg2
  }
  // (修漏因2) barrier：經過安全乘法 / 溢位檢查的就切斷 taint
  predicate isBarrier(DataFlow::Node n) {
    // 用了 __builtin_mul_overflow / reallocarray / 明確的 SIZE_MAX 檢查
    exists(FunctionCall safe |
      safe.getTarget().getName() = ["__builtin_mul_overflow", "reallocarray"] and
      n.asExpr() = safe.getAnArgument())
    or
    n.asExpr() = any(GuardCondition g | g.toString().matches("%SIZE_MAX%")).getAChild*()
  }
}
module Flow = TaintTracking::Global<Cfg>;

// 最終：tainted 值 → 經過「兩 tainted 值相乘」→ 到 alloc/copy sink，且沒被 barrier 切斷
from DataFlow::Node src, DataFlow::Node sink, TaintedMul m
where Flow::flow(src, sink)
  and m.getEnclosingFunction() = sink.asExpr().getEnclosingFunction()   // 近似關聯
select sink, "可控整數相乘未檢查溢位流入 alloc/copy: $@", src, "header 值"
```

我要誠實標一件事：上面 `isTaintedByHeader` 和「乘法與 sink 在同函式」的關聯寫法，是**簡化的近似**——真正嚴謹的做法是用 flow state（見 [Ch 23](./23-codeql-flow-state-models.md)）把「已經過 tainted 乘法」記成一個狀態，讓 sink 只接受帶這個狀態的 flow，而不是靠「同函式」這種粗關聯。我這裡用近似是為了讓骨架讀得懂；上真專案時該升級成 flow state 版，精度會高很多。

v2 跑原專案：**命中種子那條 BMP。** 現在驗證抽對了沒——照 [Ch 41](./41-auditing-antipatterns.md) 反模式 4 的紀律，**故意用不同寫法重寫 bug 測 query**：把 `width*height` 改名成 `w*h`、拆成 `t = w*h; buf = malloc(t*ch)`、把讀取換成 `get_u32`。v2 三種變寫都抓得到。**抽對了。**

## Step ⑤(掃全場)：原專案 + 相似專案

v2 跑**整個 `imgparse`**（不只 BMP 路徑）。命中（擬真）：

```
bmp.c:41   （種子，已知）
tga.c:88   ← 新！TGA 解碼有一模一樣的 width*height*bpp
pcx.c:132  ← 新！PCX 用 planes*bytes_per_line*height，三值相乘
gif.c:205  （命中，但下面 triage 會發現是誤報）
ico.c:66   ← 新！
```

一個種子滾出三到四個原專案內變體——這就是變體分析的複利（[Ch 1](./01-reading-to-auditing.md)）。同一個團隊的同一個盲點（「header 尺寸可信、相乘不檢查」）散落在每個格式的解碼器裡。

接著跨專案掃——用 MRVA（[Ch 27](./27-codeql-mrva.md)）把同一條 query 送去掃**其他影像庫**（擬真：另外兩個小型 codec 專案）。這一步的假設是：影像解析的這個盲點是**跨專案共通**的文化，不是 `imgparse` 獨有。（擬真結果：另一專案的 WebP-lite 解碼命中一條相同形狀。）

## Step ⑥：triage——砍誤報，記理由

按 [Ch 42](./42-your-audit-sop.md) 的紀律，**按 exploitability 排序**（不是照工具給的順序）逐條看。逐條判、逐條記理由（[Ch 36](./36-false-positive-governance.md)）：

| 命中 | 判定 | 理由（記錄下來） |
|---|---|---|
| `tga.c:88` | **真陽** | width/height/bpp 全來自 header，相乘無檢查，寫入用原尺寸。可達（TGA 是預設支援格式）。 |
| `pcx.c:132` | **真陽** | 三值相乘 `planes*bpl*height`，無檢查。可達。 |
| `ico.c:66` | **真陽（次要）** | 相乘無檢查，但 ICO 尺寸欄位是 uint8/uint16，溢位需極端值，可達性待驗。 |
| `gif.c:205` | **誤報** | 乘法上游 15 行有 `if (w > MAX_DIM \|\| h > MAX_DIM) return NULL;`——這是個 **barrier，我的 v2 沒建模到**（它不是 `SIZE_MAX` 字樣，是 `MAX_DIM` 常數上限）。 |

`gif.c:205` 這條誤報很有教學價值：它暴露 v2 的 barrier 建模不全——我只認 `SIZE_MAX` 字樣的檢查，沒認「用一個 `MAX_DIM` 常數 clamp 上限」這種同樣有效的防護。**照反模式 2 的紀律，這時該做的不是把它一鍵 dismiss 了事，是把這種 barrier 加進 query**（把 `MAX_DIM` 這類維度上限檢查也建模成 isBarrier），這樣下次掃別的專案，同類的良性 clamp 就不會再冒出來——triage 勞動沉澱回 query。改完重跑，`gif.c:205` 乾淨消失，且沒誤傷真陽。

triage 收束：**4 條真陽（tga/pcx/ico + 跨專案那條）**，1 條誤報已通過修 query 消除並記錄。

## Step ⑦：對一個變體構造 PoC

挑 exploitability 最高的 `tga.c:88` 驗證（[Ch 37](./37-static-plus-dynamic.md) 靜態接動態）。靜態只告訴我「這條 flow 存在」，**可達性與可控性要動態證**：

構造一個惡意 TGA（擬真 PoC 步驟，形狀真實）：header 把 `width = 0x10000`、`height = 0x10000`、`bpp = 32`。則 `width*height = 0x1_0000_0000`，在 32-bit 運算裡**溢位回 0**；`malloc(0 * 4)` → `malloc(0)`（回傳一個極小或可用的 chunk）。但後續寫入迴圈用的是「宣告的 width×height×4」這個**邏輯上的大尺寸**去填 pixel data → 往一個近乎 0 大小的 heap chunk 寫入 4 GB 級的資料 → **heap overflow**，程式在寫穿幾個 page 後 crash（ASAN 會立刻報 heap-buffer-overflow）。

**這一步我明確標示為敘事示意**——我沒有一個真的 `imgparse` 讓你 `./imgparse poc.tga` 看它 crash。但 PoC 的**邏輯是可重現的**：這正是整數溢位型 heap overflow 的標準構造，你在任何有此 bug 的真 target 上，用 ASAN build + 這個 header 構造，就會看到同樣的 crash。要把它變成「真做過」，你需要：真 target、ASAN build（`-fsanitize=address`）、一個能觸發 TGA 解碼路徑的 harness（可以直接 libFuzzer 化，見 [`advanced_fuzzing`](../advanced_fuzzing/README.md)）。靜態給候選、fuzzer/ASAN 給 crash——這就是 [Ch 37](./37-static-plus-dynamic.md) 說的靜態動態合流。

## Step ⑧：報告 / PR

每個確認的 bug，報告帶三樣東西（[Ch 39](./39-sarif-ecosystem.md)）：**PoC（觸發輸入 + ASAN 輸出）、root cause（可控維度相乘無溢位檢查）、建議修法**。修法對這類 bug 是標準的——用 checked multiplication：

```c
// 修法：安全乘法，溢位就拒絕
size_t alloc;
if (__builtin_mul_overflow(width, height, &alloc) ||
    __builtin_mul_overflow(alloc, bpp / 8, &alloc)) {
    return NULL;   // 溢位 → 拒絕這個檔案
}
uint8_t *buf = malloc(alloc);
```

發 PR 時，把**同型的四處一起修**（tga/pcx/ico + 跨專案那條各自發），並在 PR 描述裡點明「這是 BMP 那個已知 bug 的變體，同一個 root cause 在這幾處重複」——這讓維護者理解這不是零散 bug，是一個**系統性的 pattern**，順帶提示他們日後新增格式時要用安全乘法。報告誠實標明覆蓋率邊界：這條 query 覆蓋「整數溢位 → alloc/copy」這一類，**不覆蓋**其他 CWE；`ico.c:66` 的可達性標為「待進一步驗證」而非誇大成確認 RCE。

最後——確認的 bug 回填成新種子（[Ch 42](./42-your-audit-sop.md) 的迴圈）：既然「header 尺寸可信」是這類庫的共通盲點，下一輪可以抽更廣的 pattern（不只相乘，也包括 `width * stride` 型的 `memcpy` 尺寸計算），再掃一圈。一場 hunt 結束，下一場的種子已經在手上。

## 踩雷集錦

**錯誤直覺：「第一版 query 沒命中種子，八成是 CodeQL 不行 / database 建壞了。」**
正確認識：v1 漏掉種子，99% 是**你的建模沒對齊 root cause**，不是工具壞。本章 v1 漏報的三個原因（source 只認一個函式、沒把「相乘」寫進 query、sink 關聯太粗）全是建模問題。遇到「該中沒中」，先懷疑自己的 source/sink/barrier 建模，用「拿種子 bug 當 ground truth，一路 debug query 為什麼追不到它」的方式修——這是寫 query 最核心的迭代技能，比從零寫對更常用。

**錯誤直覺：「query 命中種子了，收工，去掃全場。」**
正確認識：命中種子只證明「沒過度漏報」，還沒證明「沒過擬合」。中間那步——**故意用不同寫法重寫 bug 測 query**——不能省。v2 我特意測了改名 / 拆步 / 換讀取函式三種變寫都抓得到，才敢往下掃。跳過這步就去掃全場，你的 0 額外命中可能只是因為 query 綁死了種子的字面形狀（反模式 4）。

**錯誤直覺：「誤報就 dismiss 掉，反正是誤報。」**
正確認識：`gif.c:205` 那條誤報教的是——誤報常常在告訴你 **query 少建模了一個 barrier**。把它一鍵 dismiss，你只解決了這一條；把 `MAX_DIM` 型的 clamp 加進 isBarrier，你解決了所有同型誤報，且沉澱回 query 供跨專案復用。誤報是 query 的 bug report，不是垃圾。（反模式 2、6。）

**錯誤直覺：「靜態命中 = 漏洞，寫進報告就行。」**
正確認識：靜態只給「flow 存在」，可達性與可控性要動態證（本章 Step ⑦）。`ico.c:66` 我標「可達性待驗」正是因為它的尺寸欄位窄、溢位需要極端值，靜態看不出真實輸入搆不搆得到。報告裡把「確認 PoC」和「疑似待驗」分清楚，是誠實也是專業（反模式 7、8）。

## 進階延伸

- **把這條 hunt 全自動化到 CI**：v2 這條 query 一旦穩定，可以放進 CI（[Ch 17](./17-semgrep-ci.md) 的 Semgrep 版 / CodeQL Actions），每次 PR 自動掃「新加的解碼器有沒有再犯同樣的整數溢位」。變體分析的終局不只是找出現存變體，是**建立一道防止未來變體被引入的閘**——把你這次的洞見固化成常駐守衛。
- **flow state 精修這條 query**：本章 v2 用「同函式」近似關聯乘法與 sink，是為了好懂而犧牲精度。真正的做法是用 flow state（[Ch 23](./23-codeql-flow-state-models.md)）把「flow 已經過一次 tainted 乘法」記成狀態，sink 只接受帶此狀態的 flow。把 v2 升級成 flow state 版，是把這章當練習往下做的最好方向——精度會顯著提升、跨函式的乘法也能正確關聯。
- **同一 root cause 的鄰近 pattern**：整數溢位進 alloc 只是「尺寸計算不可信」這個大家族的一支。鄰近的還有：`alloc(n)` 但 `memcpy(n + header_len)`（off-by-header）、`stride * height` 型 stride 溢位、`realloc` 迴圈裡的累加溢位。把種子 bug 的 root cause 再往外擴一圈，一個 hunt 能延伸成一個 campaign——這正是 [final project](./final-project-variant-analysis-campaign.md) 要你做的事。

## 本章重點整理

- 一條完整 variant hunt 走完 SOP 八步：target/建模 → root cause → 抽 pattern → 寫 query（v1 漏報 → v2 修好）→ 掃原專案 + MRVA 跨專案 → triage 砍誤報記理由 → 對一個變體構造 PoC → 報告/PR + 回填種子。
- **第一版 query 漏掉種子是常態，不是失敗**：99% 是建模沒對齊 root cause（source 不全 / 沒把關鍵語意「相乘」寫進 query / sink 關聯太粗）。用「拿種子當 ground truth 反覆 debug query」修。
- **命中種子後、掃全場前，必做「換寫法重寫測 query」**驗證沒過擬合。跳過這步的 0 命中可能是假安全。
- **誤報是 query 的 bug report**：`gif.c:205` 暴露 barrier 少建模了 `MAX_DIM` clamp，該做的是補建模 + 沉澱回 query，不是一鍵 dismiss。
- **靜態給候選、動態給 crash**：可達性 / 可控性要用 ASAN + 構造輸入證，報告裡「確認 PoC」與「疑似待驗」要分清。
- 一個種子滾出一串同型變體（原專案 3-4 處 + 跨專案），反映團隊/生態的共通盲點——這就是變體分析的複利，也是全課的核心槓桿。

## 自我檢核

- [ ]（主動回憶）不看內文，把這條 hunt 的八個步驟按順序講出來，並指出哪一步是「迴圈回填」。
- [ ]（理解）v1 漏掉種子的三個原因各是什麼？為什麼說「該中沒中」該先懷疑自己的建模而非工具？
- [ ]（理解）「命中種子」和「沒過擬合」是兩件不同的事——中間那步驗證具體怎麼做？
- [ ]（理解）`gif.c:205` 為什麼是誤報？正確的處理為什麼是「修 query」而非「dismiss」？
- [ ]（應用）本章哪些步驟我標為「真做過/可重現」、哪些標為「敘事示意」？你要把 Step ⑦ 變成真做過，需要哪三樣東西？
- [ ]（綜合）挑一個你熟的 bug class（UAF / 命令注入 / path traversal 皆可），照本章結構，在腦中走一遍：root cause 怎麼抽、query 第一版可能怎麼漏、誤報可能從哪來。

## 延伸閱讀

- **[GitHub Security Lab — 整數溢位 / 記憶體安全 variant analysis write-up](https://securitylab.github.com/research/)**：找他們用 CodeQL 追整數溢位或 buffer overflow 變體的真實案例，對照本章的 v1→v2 迭代，看專業研究者的 query 怎麼從天真版修到精準版。前提：本課 Part 4（CodeQL）。
- **CWE-190（Integer Overflow）與 CWE-787（Out-of-bounds Write）官方條目**（[cwe.mitre.org](https://cwe.mitre.org/)）：把本章 bug class 的標準分類與更多真實 example 讀一遍，建立「這類 bug 的詞彙庫」，之後抽 pattern 更快。前提：無。
- **你自己這門課的 [Ch 26 CVE→query](./26-codeql-cve-to-query.md) 與 [Ch 23 flow state](./23-codeql-flow-state-models.md)**：本章 Step ④⑤ 的方法論母章。回頭讀 Ch 26 的「抽多抽象」與 Ch 23 的 flow state，把本章 v2 那個「同函式近似」升級成嚴謹版。前提：無。
- **[`advanced_fuzzing`](../advanced_fuzzing/README.md) 的 libFuzzer / ASAN 入門章**：把 Step ⑦ 從「敘事示意」變「真做過」的工具。學怎麼給解碼函式包一個 harness、用 ASAN build 讓 heap overflow 當場現形。前提：本課 [Ch 37](./37-static-plus-dynamic.md)。

你現在完整看過一條 variant hunt 從已知 bug 到 PR 的全貌——包括第一版漏報、誤報怎麼砍這些真實的摩擦。**你現在能獨立跑一條 variant hunt 了。** 最後一步，是拿一個你自己選的真 target，把整套從頭到尾自己走一遍——那就是 final project。

→ [Final Project：Variant Analysis Campaign](./final-project-variant-analysis-campaign.md)
