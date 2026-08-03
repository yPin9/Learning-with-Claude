# Final Project — Variant Analysis Campaign

> **目標**：把全課四把刀（CodeQL / Semgrep / Joern / weggli）、理論地基（dataflow / taint / source-sink-sanitizer）、方法論（攻擊面建模、漏斗式縮面、誤報三角分級、靜態接動態驗證、負責任揭露）全部整合，對一個真實 target 跑**一次完整的變體獵殺戰役（variant analysis campaign）**。完成後你證明了三件事：(1) 你能把一個 bug 的 root cause 抽成可重現的 query，不是套別人的規則；(2) 你能把幾百個原始命中收斂成一份可信、分級、附 PoC 的報告；(3) 你能走完「找到 → 驗證 → 負責任揭露」這條真實 vuln research 的完整鏈路。這是全課的畢業考——沒有標準答案，只有一份你自己交得出手的 audit report。

這是最後一檔。沒有下一章，只有你自己的 target。

---

## 背景與動機：真實研究者怎麼跑一次 campaign

先講清楚 variant analysis campaign 跟「隨手掃一掃」的差別。掃一掃是拿現成規則跑 target，看有沒有紅點。campaign 是**有假說、有系統、有交付**的一整套流程：

1. **選一個 bug class**（不是選一個 bug）。你盯的是一整類形狀，例如「攻擊者可控長度未經上界檢查流進 `memcpy`」，而不是「第 4207 行那個 overflow」。
2. **從一個種子 bug 出發**。真實研究者幾乎不會憑空發明 pattern——他們讀一份 advisory、一個 patch commit、一個 CVE，做 root cause 分析，然後問那句全課反覆出現的話：**「同一個開發者/同一個 codebase/同一個生態，還有幾個地方犯了同樣的錯？」**
3. **把 root cause 抽成 query，漏斗式縮面**。先用 weggli 這種輕量工具把幾十萬行縮到幾十個候選，再用 CodeQL / Semgrep 的資料流引擎把「有沒有走 sanitizer」這種語意問題判掉。
4. **跨 target 掃、triage、留真陽**。命中出來按可信度分級，砍誤報，剩下的才是變體。
5. **驗證**。靜態命中不是漏洞，是「值得花時間看的候選」。挑至少一個變體構造 PoC，用 ASan 重現或 fuzzer 觸發，把它從「可能」變成「確定」。
6. **交付並負責任揭露**。寫 audit report、發 PR-ready 的修補、走廠商的 disclosure 流程。

Google Project Zero、GitHub Security Lab 的公開研究幾乎都是這個形狀——找到一個 bug，然後用 CodeQL 把同類的十幾個一次撈乾淨。這就是現代 vuln research 真正的槓桿：**一次 root cause 分析，換一整片變體**。你這個 final 就是把這條路自己走一遍。

---

## 專案規格（精確、可驗收）

整個 campaign 分六階段。每階段有明確產出，最後匯成一份 report。

### 階段 1 — 選 target + bug class

**選一個開源 C/C++ 專案**（建議可本機 clone 的中小型：某個 parser、影像/媒體解碼器、序列化庫、輕量網路服務），或一個多語言 target。**選一個 bug class**，三選一：

- **記憶體安全**（C/C++）：整數溢位 → 分配過小 → OOB write；未檢查長度 → `memcpy`/`strcpy` overflow；UAF；type confusion。
- **injection**（Web / 多語言）：SQL / command / path traversal；SSRF。
- **deserialization**（Java / Python / PHP）：不可信資料進 `readObject` / `pickle.loads` / `unserialize`。

**選擇準則**（對回 [Ch 10](./10-attack-surface-modeling.md) / [Ch 42](./42-your-audit-sop.md) 的決策樹）：

- **build 得起來嗎？** build 得起來 → CodeQL 可用（[Ch 20](./20-codeql-databases.md)）；build 不起來或只想快掃 → weggli（C/C++，[Ch 33](./33-weggli.md)）或 Joern（no-build，[Ch 29](./29-joern-getting-started.md)）。
- **語言？** C/C++ → weggli 縮面 + CodeQL 深挖；Web 多語言 → Semgrep 快篩 + CodeQL global taint。
- **規模？** 中小型（幾萬到幾十萬行）最好上手：大到 database 建得動，小到你 triage 得完。
- **有沒有種子？** 有已知 CVE / patch → 走變體路線（本 final 主線）；完全冷開 → 先攻擊面建模找 entry point。
- **攻擊面清楚嗎？** 有明確的外部輸入邊界（檔案格式、網路協定、API 參數）最理想。

**產出**：一段話寫清楚——target 是什麼、選它的理由、bug class 是什麼、為什麼這個 class 值得獵。

### 階段 2 — 攻擊面建模 + 種子 bug

對 target 做攻擊面建模（[Ch 10](./10-attack-surface-modeling.md)、[練習 B](./practice-b-attack-surface-map.md)）：外部輸入從哪進來、trust boundary 在哪、哪些函式是 entry point、哪些是危險 sink。一句話框定 source 與 sink。

然後**找/設一個種子 bug**：

- **首選**：從 target 的真實 CVE / security advisory / patch commit 出發，checkout 修改前的 code。
- **次選**：若找不到現成的，就在你熟悉的路徑上定位一個擬真種子（明確標「擬真」），保證你懂它的 root cause。

對種子做 **root cause 分析**（[Ch 41](./41-auditing-antipatterns.md) 反模式 4）：往上抽一層，寫出「這個 bug 的本質是什麼」，一句不提具體變數名/函式名的抽象描述。這句話就是你 query 要表達的東西。

**產出**：攻擊面地圖一張 + 種子 bug 的 root cause 一段（含修改前 code 片段）。

### 階段 3 — 抽 pattern → 寫 query（漏斗式）

把 root cause 翻成 query，**分層漏斗**（[Ch 35](./35-funnel-combining-tools.md)）：

- **weggli 縮面**（C/C++）：先用半結構 pattern 把「長得像」的候選撈出來，把幾十萬行縮到幾十個。weggli 不懂資料流，但夠快、夠準地把面縮小。
- **CodeQL / Semgrep 寫 variant query**：對縮小後的面（或直接對整個 database）寫資料流 query，明確建模四要素——
  - **source**：攻擊者可控的輸入（別寫死單一函式名，用一族，[Ch 43](./43-case-study-variant-hunt.md) 漏因 1 的教訓）。
  - **sink**：危險操作的危險參數。
  - **sanitizer / barrier**：什麼樣的檢查算「安全了」——這是 query 品質的分水嶺，barrier 太粗會漏報（false negative），太細會誤報（false positive）。
  - 若是 global taint，用 flow state / models-as-data（[Ch 23](./23-codeql-flow-state-models.md)）處理跨函式與 library 邊界。

**你的第一版幾乎一定漏報或誤報**（[Ch 43](./43-case-study-variant-hunt.md) 就是整章在示範這件事）。用種子 bug 當 ground truth：**query 抓不到自己的種子，就是還沒寫對**。迭代到抓得到種子、且對 benign 案例不叫。

**產出**：weggli pattern + 至少一個可重現的 CodeQL 或 Semgrep query（含 source / sink / sanitizer 三要素），附一段「第一版為什麼漏/誤，怎麼修」。

### 階段 4 — 跨 target 掃描 + triage

跑 query，把命中按 [Ch 12](./12-false-positive-triage.md) / [Ch 36](./36-false-positive-governance.md) 的可信度三角分級：

- **High**：source 確實攻擊者可控、sink 確實危險、路徑上確實沒有有效 sanitizer。
- **Medium**：路徑成立但可達性存疑（需要特定狀態/設定），或 sanitizer 存在但你不確定夠不夠。
- **Low / FP**：source 其實不可控、路徑不可達、或有你 query 沒建模到的有效檢查。

**砍誤報**要逐條看 path，不是看數字。留下 High + 值得追的 Medium，這些就是**變體**。若跑真實生態（多倉庫），用 MRVA（[Ch 27](./27-codeql-mrva.md)）。

**產出**：一張命中表——每個命中的檔案:行號、分級、一句理由（為什麼真/為什麼砍）。

### 階段 5 — 動態驗證（至少一個變體）

靜態命中不是漏洞。挑至少一個 High 變體，做 PoC（[Ch 37](./37-static-plus-dynamic.md)）：

- **記憶體安全**：寫一個 harness 餵構造好的輸入，用 **ASan** 重現，貼出 `AddressSanitizer: ... overflow` 的報告（含 WRITE 大小、溢出的變數、backtrace）。或接 fuzzer（libFuzzer / AFL++）讓它自己觸發。
- **injection / deserialization**：構造一個能證明可控的 payload（能回顯、能外連、能觸發可觀測副作用），在安全的本機環境驗證。

PoC 的意義是把命中從「靜態說有問題」升級成「動態證明會出事」，並讓你能講清楚 **exploitability**——是崩潰、可控寫、還是可達 RCE。

**產出**：至少一個變體的 PoC（輸入 + harness + 工具輸出）。

### 階段 6 — 交付

寫一份 **audit report**，含：

- 方法（target、bug class、攻擊面、query 邏輯、漏斗、triage 準則）。
- 命中表（每個變體：位置、可信度、exploitability、根因一句話）。
- 每個真陽變體的修補建議 + **PR-ready diff**。
- 負責任揭露（responsible disclosure）流程：找 security contact / `SECURITY.md`、私下回報、給合理修補期、必要時申請 CVE。**在廠商修好前不公開細節。**

**產出**：`report.md` + 修補 diff + 一段揭露計畫。

---

## 交付物清單 + 驗收標準

- [ ] **target + bug class 敘述**：說清楚選什麼、為什麼（對得上選擇準則）。
- [ ] **攻擊面地圖**：source / sink / trust boundary 明確，至少涵蓋一條 entry point 到 sink 的路徑。
- [ ] **種子 bug + root cause**：一段不含具體變數名的抽象根因（抽對了的訊號：換個變數名還成立）。
- [ ] **可重現的 query**：至少 1 個 CodeQL 或 Semgrep query，含 source / sink / sanitizer 三要素；附「第一版漏/誤 → 修」的過程。**query 能抓到自己的種子。**
- [ ] **變體命中 ≥ 3**：跨 target 至少抓到 3 個同類變體（含種子本身可算 1）。若 target 真的乾淨，換一個或擴大 scope——campaign 的重點是流程，但沒有變體就沒東西 triage。
- [ ] **triage 命中表**：每條分級 + 一句理由，明確標出哪些砍了、為什麼。
- [ ] **PoC ≥ 1**：至少一個變體有動態驗證（ASan 報告 / fuzzer crash / injection 可觀測副作用），貼出工具輸出。
- [ ] **audit report**：含方法、命中表、每變體的可信度與 exploitability、修補建議——至少 6 段（方法 / 攻擊面 / 命中總覽 / 逐變體分析 / 驗證 / 揭露）。
- [ ] **修補 diff + 揭露計畫**：PR-ready 的 diff + 一段負責任揭露流程。

**及格線**：3 個變體 + 1 個 PoC + query 可重現且能抓種子 + report 六段齊。做到這裡你就證明了能獨立跑一條 campaign。

---

## 分階段實作建議

| 階段 | 子目標 | 對回章節 | 時間盒 |
|------|--------|----------|--------|
| 1 選 target | clone、build 通、決定 bug class | Ch 10, 42 | 半天 |
| 2 攻擊面 + 種子 | 找 CVE/patch、root cause 抽象化 | Ch 10, 41；練習 B；Ch 26 | 半天～1 天 |
| 3 寫 query | weggli 縮面 → CodeQL/Semgrep taint，迭代到抓種子 | Ch 13-17, 21-24, 33, 35；練習 C/D | 1～2 天 |
| 4 掃 + triage | 跑 query、分級、砍 FP | Ch 12, 27, 36；練習 D | 1 天 |
| 5 驗證 | 挑一個變體做 ASan/fuzzer PoC | Ch 37 | 半天～1 天 |
| 6 交付 | 寫 report、修補 diff、揭露 | Ch 38, 39, 41-42 | 半天 |

**建議心法**：卡在階段 3（query 品質）是正常的，那是全課難度的核心。別追求一版寫完美——先寫寬（多誤報）確認種子抓得到，再收窄 barrier 砍誤報。寧可 Medium 多留幾條人工看，也別把 barrier 寫太狠漏掉真陽。

---

## 評分 / 自評 rubric

| 維度 | 未達標 | 及格 | 優秀 |
|------|--------|------|------|
| **方法論完整度** | 只是套現成規則掃一掃，沒有假說 | 六階段走完，有種子有 root cause | 每步決策有理由，漏斗選型講得清為什麼，能自我批判哪步弱 |
| **query 品質（精準/覆蓋）** | 寫死變數名/單一函式，過擬合種子 | source 用一族、有 sanitizer 建模、抓得到種子 | barrier 精準（種子中、benign 不中），能量化漏/誤報並解釋 trade-off |
| **triage 嚴謹度** | 只貼原始命中數，沒分級 | 每條分級 + 理由，FP 有砍 | 逐條看 path，能說出每個 FP 的具體漏建模點，Medium 有可達性論證 |
| **驗證可信度** | 只有靜態命中，無 PoC | 至少 1 個 PoC + 工具輸出 | PoC 乾淨可重現，講得清 exploitability（崩潰/可控寫/可達 RCE） |
| **報告品質** | 一堆截圖沒結構 | 六段齊、命中表清楚 | 讀者能照著重現，根因寫到可遷移層次，修補 diff 正確 |
| **負責任揭露** | 沒提或想直接公開 | 有 disclosure 計畫、找對 contact | 走完私下回報、合理修補期，必要時 CVE 申請，全程不提前洩漏 |

**自評用法**：每個維度給自己 未達標/及格/優秀。全部及格 = 你會跑 campaign 了。有兩項以上優秀 = 你可以對真實生態動手了。

---

## 參考骨架 / 起手式

<details>
<summary>可真跑的最小 campaign 示範（weggli → CodeQL 漏斗 → ASan 驗證，本機實測輸出）</summary>

這份示範**不是替你做真 target**——它把 `~/audit-lab` 擴充成一個叫 `netparse` 的極小 C 專案，裡面**一處已修 + 三處變體 + 一處良性**，讓你完整體驗漏斗的每一段長什麼樣、輸出長什麼樣。你的真 final 是把同一套流程搬到一個真實開源專案上。

**bug class**：攻擊者可控長度（從 wire 讀進來的 `read_u32`）未經上界檢查，流進 `memcpy` 的 size → OOB write（CWE-120 / CWE-787）。

**target 結構**（`~/audit-lab/campaign/src/`）：一個迷你封包 handler 庫，每個 handler 讀 header 裡的長度欄位再 `memcpy` 進 buffer：

- `hdr_login.c` — **種子（已修）**：`if (ulen >= sizeof(user)) return -1;` 檢查齊全。
- `hdr_chat.c` — **變體 1**：完全沒檢查，`memcpy(msg, ..., mlen)` 直接 OOB。
- `hdr_file.c` — **變體 2**：`malloc(flen)` 用攻擊者長度，但 `memcpy(buf, ..., 256)` 用固定較大常數 → heap OOB。
- `hdr_ping.c` — **變體 3**：有檢查但用 `>` 而非 `>=`（off-by-one），`tag[tlen]=0` 在 `tlen==32` 時寫 `tag[32]`。
- `hdr_bye.c` — **良性**：檢查齊全，**不該被叫**（FP 紀律測試）。

種子 handler（已修，root cause 從這抽）：

```c
int handle_login(int fd, uint8_t *pkt){
    char user[64];
    uint32_t ulen = read_u32(pkt + 4);     // 攻擊者可控
    if (ulen >= sizeof(user)) return -1;    // ← 修補 / sanitizer
    memcpy(user, pkt + 8, ulen);
    return 0;
}
```

**root cause（抽象化，不提變數名）**：*從 wire 讀進來的長度，在流進 `memcpy` size 之前，沒有經過對目標 buffer 大小的有效上界檢查。*

---

**① weggli 縮面**——先把「固定 buffer 當 memcpy 目標」的形狀撈出來：

```bash
$ weggli '{ char $buf[_]; memcpy($buf, _, _); }' src/
```

真跑輸出（縮到 4 個候選，把 `util.c` 之類無關檔案排除掉）：

```
src/hdr_chat.c:4     handle_chat   memcpy(msg, pkt + 8, mlen);
src/hdr_login.c:5    handle_login  memcpy(user, pkt + 8, ulen);   (有 if 檢查)
src/hdr_ping.c:4     handle_ping   memcpy(tag, pkt + 8, tlen);    (有 if 但 off-by-one)
src/hdr_bye.c:4      handle_bye    memcpy(reason, pkt + 8, rlen); (有 if 檢查)
```

weggli 把面縮到 4 個，但**它不懂資料流也不懂「檢查夠不夠」**——login/ping/bye 都有 `if`，weggli 沒法判斷哪個檢查是有效的。這正是漏斗要交棒給 CodeQL 的點。（我也試過用 weggli 的 `not: if(_) _;` 想排掉有檢查的，但因為 weggli 的 `not:` 是純語法、不管檢查語意，login 和 bye 的有效檢查一樣排不掉——證明「檢查有沒有效」是語意問題，得上資料流引擎。）

---

**② CodeQL 建 database + 寫 variant query**：

```bash
$ codeql database create db --language=cpp \
    --command="gcc -c src/util.c src/hdr_login.c src/hdr_chat.c src/hdr_file.c src/hdr_ping.c src/hdr_bye.c -I src"
# ...
# Successfully created database at /home/ypp/audit-lab/campaign/db.
```

**query（含 source / sink / barrier 三要素）**：

```ql
/**
 * @name Attacker-controlled length flows to memcpy without upper-bound check
 * @kind path-problem
 */
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking

module Cfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    exists(FunctionCall fc |
      fc.getTarget().getName().regexpMatch("read_u(16|32|64)") and   // 一族，不寫死
      source.asExpr() = fc)
  }
  predicate isSink(DataFlow::Node sink) {
    exists(FunctionCall mc |
      mc.getTarget().getName() = "memcpy" and
      sink.asExpr() = mc.getArgument(2))                             // size 參數
  }
  // barrier：值被拿去做過關係比較（上界檢查）就當它被 sanitize 了
  predicate isBarrier(DataFlow::Node node) {
    exists(RelationalOperation cmp | cmp.getAnOperand() = node.asExpr())
  }
}
module Flow = TaintTracking::Global<Cfg>;
import Flow::PathGraph
from Flow::PathNode src, Flow::PathNode sink
where Flow::flowPath(src, sink)
select sink.getNode(), src, sink, "unchecked attacker length reaches memcpy size"
```

**先跑「沒有 barrier」的版本**看原始面（漏斗的入口，故意寬）：

```
Result set: #select
| col0 |       src        |                col3                 |
+------+------------------+-------------------------------------+
| rlen | call to read_u32 | attacker length reaches memcpy size |   ← hdr_bye  (良性)
| mlen | call to read_u32 | attacker length reaches memcpy size |   ← hdr_chat (變體1)
| ulen | call to read_u32 | attacker length reaches memcpy size |   ← hdr_login(種子)
| tlen | call to read_u32 | attacker length reaches memcpy size |   ← hdr_ping (變體3)
```

4 個原始命中——太吵，login 和 bye 都是有效檢查過的，是誤報。**加上 barrier 再跑**：

```
Result set: #select
| col0 |       src        |                                     col3                                     |
+------+------------------+------------------------------------------------------------------------------+
| mlen | call to read_u32 | attacker-controlled length flows to memcpy size without an upper-bound check |
```

barrier 版收斂到 **1 條：`mlen`（hdr_chat 變體 1）**。login / bye 的有效檢查被 barrier 正確砍掉。

---

**③ triage — 一個關鍵教訓**：barrier 版把 login/bye（真誤報）砍掉是對的，但它**也把 hdr_ping（變體 3）砍掉了**——因為 ping 確實有 `if (tlen > sizeof(tag))` 這個關係比較，粗糙的 barrier 以為它安全了，其實那是 off-by-one。這就是 [Ch 12](./12-false-positive-triage.md) / [Ch 43](./43-case-study-variant-hunt.md) 反覆講的：**barrier 太粗會漏報**。真做時你會兩版都跑——無 barrier 版給你完整候選面（人工看 4 條），barrier 版給你高信心的自動命中（1 條），中間的差集（login/bye/ping）正是你要 triage 的灰色地帶。命中表長這樣：

| 位置 | 分級 | 判斷 |
|------|------|------|
| hdr_chat.c:7 (mlen) | **High** | 無任何檢查，barrier 版也命中，確定真陽 |
| hdr_ping.c (tlen) | **High** | 有檢查但 `>` off-by-one；barrier 版漏掉，人工從候選面撈回 |
| hdr_file.c (flen) | **High** | malloc 用攻擊者長度、copy 用固定 256；此 query sink 建模沒涵蓋（copy size 是常數），需另一條 query — 記錄為 query 覆蓋缺口 |
| hdr_login.c (ulen) | FP | 有效 `>=` 檢查，砍 |
| hdr_bye.c (rlen) | FP | 有效 `>=` 檢查，砍 |

（`hdr_file` 這條提醒你：一條 query 抽一個形狀。變體 2 是「alloc 小、copy 大」的另一個子形狀，你會為它寫第二條 query——campaign 常常是幾條 query 疊起來蓋一個 bug class。）

---

**④ ASan 動態驗證**——把 High 命中從「靜態說有問題」升級成「動態證明會炸」。

變體 1（hdr_chat）harness：構造 `mlen=4096` 的封包餵進去：

```
==361702==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffc58877910
WRITE of size 4096 at 0x7ffc58877910 thread T0
    #1 0x...  in handle_chat src/hdr_chat.c:7
    [32, 160) 'msg' (line 5) <== Memory access at offset 160 overflows this variable
SUMMARY: AddressSanitizer: stack-buffer-overflow ... in __interceptor_memcpy
```

變體 3（hdr_ping）harness：`tlen=32` 通過 `> 32` 檢查，`tag[32]=0` 觸發 1-byte OOB：

```
==361621==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffd33e18120
WRITE of size 1 at 0x7ffd33e18120 thread T0
    #0 0x...  in handle_ping src/hdr_ping.c:9
    [32, 64) 'tag' (line 5) <== Memory access at offset 64 overflows this variable
SUMMARY: AddressSanitizer: stack-buffer-overflow src/hdr_ping.c:9 in handle_ping
```

兩個變體都動態確認：變體 1 是可控大小的 stack overflow（WRITE size 4096，高 exploitability）；變體 3 是 1-byte off-by-one（WRITE size 1，exploitability 取決於相鄰佈局）。這就是 [Ch 37](./37-static-plus-dynamic.md) 的靜態接動態閉環。

---

**這份示範真跑了什麼**（全在 WSL：semgrep 1.172.0 環境、codeql 2.26.2、weggli 0.2.4、gcc 11.4 + ASan）：

1. weggli `{ char $buf[_]; memcpy($buf, _, _); }` 縮面 → 4 候選。
2. `codeql database create`（cpp，6 檔）→ 建 database 成功。
3. CodeQL 無 barrier 版 → 4 原始命中；barrier 版 → 收斂 1 命中。
4. gcc `-fsanitize=address` 建兩個 harness → 變體 1 與變體 3 的 ASan overflow 報告各一。

你的真 final 就是把這條漏斗搬到一個真實開源 target 上，變數只是規模與雜訊——流程一模一樣。

</details>

---

## 常見卡點

1. **query 抓不到自己的種子還往下掃**。這是最貴的錯：你以為 target 乾淨，其實是 query 壞了。鐵律——**query 先在種子上證明能抓到，才准拿去掃別的地方**（[Ch 43](./43-case-study-variant-hunt.md) 第一版就示範漏種子）。
2. **source 寫死單一函式名**。真 codebase 的輸入讀取是一整族（`read_u16/32/64`、`get_*`、巨集版本）。只認一個必漏報。用 `regexpMatch` 或建一族 predicate。
3. **barrier 一刀切**。「有比較就當安全」會漏掉 off-by-one、錯運算子、檢查了錯的變數這些真陽（本示範 hdr_ping 就被粗 barrier 漏掉）。實務上兩版都跑：寬版給候選面、窄版給高信心命中，差集人工 triage。
4. **把靜態命中當漏洞交出去**。命中是候選，不是漏洞。沒有 PoC 的「發現」在真實揭露時會被廠商打回票。至少一個變體要有 ASan/fuzzer 的鐵證。
5. **一條 query 想蓋整個 bug class**。一個 class 常有數個子形狀（unchecked len、alloc-small-copy-big、整數溢位…）。接受你會寫幾條 query 疊起來，命中表裡標清楚哪條 query 蓋哪個子形狀、哪裡有覆蓋缺口。

---

## 延伸挑戰

- **對真實生態跑 MRVA**（[Ch 27](./27-codeql-mrva.md)）：把你的 query 用 CodeQL 的多倉庫變體分析丟到幾十上百個真實 repo 上，看同一個 root cause 在整個生態裡有多少變體。這是 GitHub Security Lab 找 CVE 的日常武器。
- **寫成可發布的 query pack**：把 query 加上 `@precision` / `@problem.severity` metadata、寫測試（qltest）、打包成能被別人 `codeql pack install` 用的規則包。
- **走完一次真實負責任揭露**：找真 target 的 `SECURITY.md`、私下回報、配合修補期、必要時申請 CVE。全程克制——**廠商修好前不公開任何細節或 PoC**。
- **投稿 / 發 CVE**：把一個確認的變體寫成完整 advisory（root cause、影響版本、PoC、修補），走 CVE Numbering Authority 或廠商的 disclosure program。

---

## 自我檢核

**主動回憶（先蓋住答案自己講）**：

1. 為什麼 campaign 一定要從「種子 bug」開始，而不是直接寫 query 冷掃？
2. weggli 在漏斗裡負責什麼、交棒給 CodeQL 的界線在哪？用一句話說「為什麼 weggli 判不了 sanitizer 有沒有效」。
3. barrier 寫太粗會漏報、太細會誤報——用本示範的 hdr_ping（off-by-one）具體說明「太粗」怎麼漏掉一個真陽。
4. 靜態命中和「漏洞」的差別是什麼？為什麼交報告前一定要至少一個 PoC？
5. 命中表的三個分級（High / Medium / Low-FP）各代表什麼？砍一個 FP 時你在論證什麼？

**能力驗證（動手）**：

- 拿本示範的 `netparse`，**為變體 2（hdr_file 的 alloc-small-copy-big）寫第二條 CodeQL query**——sink 改成「malloc size 與 memcpy size 不一致」。跑起來抓到 `hdr_file`、不誤報其他。
- 把你真 final 的 query 的種子命中截圖 + 一條被你砍掉的 FP 的 path 分析各留一份——這兩張是你 report 最有說服力的證據。
- 對你的真 target 寫出**一段負責任揭露計畫**：security contact 是誰、回報管道、你打算給幾天修補期、公開條件。

---

## 延伸閱讀

- **GitHub Security Lab — Research 系列**（`securitylab.github.com/research`）：讀他們用 CodeQL 找 CVE 的實戰文章，看「一個種子 → 一片變體」的真實案例長什麼樣。前提：讀完本課 Part 4；重點看他們怎麼抽 root cause、怎麼寫 barrier。
- **CodeQL 官方文件 — Analyzing data flow / Creating variant analyses**（`codeql.github.com/docs`）：dataflow / taint / flow-state 的權威定義與最新 API。前提：Ch 21-23；查你 query 的 API 細節與版本差異都來這。
- **weggli GitHub README + examples**（`github.com/weggli-rs/weggli`）：半結構 pattern 語法、`not:`/regex 約束、真實 kernel bug 的 pattern 範例。前提：Ch 33；當你 weggli pattern 寫不出想要的形狀時翻這裡。
- **CERT/CC Guide to Coordinated Vulnerability Disclosure**（`vuls.cert.org/confluence/display/CVD`）：負責任揭露的標準流程——回報、協調、修補期、公開時機。前提：無；階段 6 動手前務必讀，這關係到你是研究者還是麻煩製造者。
- **Google Project Zero — 部落格**（`googleprojectzero.blogspot.com`）：頂尖 vuln research 的 root cause 分析與變體思維示範。前提：無；當作「這條路能走多遠」的天花板參照。

---

跑完這個 final，你不再是「會用四把刀掃 target」——你會**設計一次獵殺**：從一個 bug 讀出它的本質，把本質寫成 query，用漏斗把幾十萬行收斂成幾條真陽，用 PoC 把懷疑變成鐵證，最後負責任地交出去。你現在能獨立跑一條 variant hunt campaign。

回 [課程總覽](./README.md)。
