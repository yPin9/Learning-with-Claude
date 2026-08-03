# Ch 15 — Semgrep 規則工程

> **目標**：從「寫一條規則」跨到「維護一套規則」。前兩章你會寫 pattern（Ch 13）和 taint rule（Ch 14），這章講**工程化**：規則 ID 命名慣例、`message`／`metadata`／CWE 對應、`severity`、`fix:`（autofix，自動修補）、以及最關鍵的 **test 檔**（`.test.` 檔配 `// ruleid:` `// ok:` 註解）——用 `semgrep --test` 把規則的真陽性/假陽性標記變成可自動驗證的回歸測試。再真跑 `metavariable-comparison`（size 小於某常數才算安全）示範怎麼用數值條件降誤報。核心命題：**沒測過的規則不該上線；一條規則只該有一個意圖**。
>
> **環境**：Semgrep 1.172.0，WSL Ubuntu 22.04

一條規則寫出來能命中，不代表它能上線。上線意味著它要跑在別人的 codebase、被別人 triage、隨著程式碼演進不能悄悄壞掉。這章的每個機制都在回答同一個問題：**怎麼讓一條規則值得信任、可維護、好 triage**。

## 一條「上線級」規則長什麼樣

先看完整體，再拆。這是一條帶 metadata、CWE、autofix 的規則：

```yaml
rules:
  - id: insecure-strcpy
    languages: [c]
    severity: WARNING
    message: >
      strcpy() has no bounds check; prefer a bounded copy. (CWE-120)
    metadata:
      cwe: "CWE-120: Buffer Copy without Checking Size of Input"
      category: security
      references:
        - https://cwe.mitre.org/data/definitions/120.html
    pattern: strcpy($DST, $SRC);
    fix: strlcpy($DST, $SRC, sizeof($DST));
```

比 Ch 13 的最小骨架多了三塊，逐一講清為什麼每塊都不是裝飾。

### rule ID 命名：triage 的第一線索

`id` 不只是唯一鍵，它是**每條命中在報告/SARIF/抑制註解裡的臉**。命名慣例：`語言.類別.具體問題`（例如 `c.security.insecure-strcpy`）或至少「動詞/形容詞 + 對象」讓人掃一眼知道在講什麼。爛 ID（`rule1`、`check-stuff`）讓 triage 的人得回去翻 YAML 才知道規則意圖，一條爛 ID 乘以幾百條命中就是災難。**ID 是給人讀的，取到看名字就懂**。

### message + metadata + CWE：讓命中可被分流

`message` 是命中時印的話，寫給**看到報告但沒看過規則的人**——講清「這是什麼問題、為什麼危險、怎麼修」，不是複述 pattern。`metadata` 掛結構化資訊，其中 **`cwe`** 是 triage 的關鍵：

- 有 CWE，命中能自動歸類（「這批都是 CWE-120 buffer overflow」）、對接漏洞管理系統、統計趨勢。
- 沒 CWE，每條命中都得人工判斷屬於哪類，**triage 成本暴增**（見踩雷）。

`references` 放 CWE/CVE/文件連結，讓 triage 的人一鍵查證。**metadata 不影響是否命中，但決定命中出來之後好不好處理**——規則工程有一半是為了下游的人。

### severity：分流的優先級旋鈕

`INFO`／`WARNING`／`ERROR` 決定命中的默認嚴重度，直接影響 CI 是否 block（Ch 17）、triage 先看哪批。定 severity 的紀律是**對齊真實風險**：確定可利用的注入是 `ERROR`，值得看但未必是洞的模式是 `WARNING`，純資訊/風格是 `INFO`。全部標 `ERROR` 等於沒分級——CI 天天紅、大家開始無視，比不分級更糟。

## autofix：`fix:` 自動改寫

`fix:` 讓規則不只報還能改。上面的規則把 `strcpy($DST, $SRC);` 改寫成 `strlcpy($DST, $SRC, sizeof($DST));`——metavariable 綁定會帶進替換文字。真跑 `--autofix --dryrun`（只看 diff 不落地）：

```
    cwe-strcpy.test.c
    ❯❱ insecure-strcpy
          strcpy() has no bounds check; prefer a bounded copy. (CWE-120)
           ▶▶┆ Autofix ▶ strlcpy(dst, src, sizeof(dst));
            6┆ strlcpy(dst, src, sizeof(dst));
```

`$DST`→`dst`、`$SRC`→`src` 精準代入，`sizeof($DST)`→`sizeof(dst)`。autofix 在「機械式、語意等價」的修補上很值（大量把危險 API 換成安全版）。**但 autofix 是雙面刃**——它會真的改別人的程式碼，改錯就是引 bug（見踩雷）。所以 autofix 規則**尤其**需要測試證明它不改壞語意。

## test 檔：把「規則對不對」變成可自動驗證

這是規則工程的核心紀律。Semgrep 的 test 機制：寫一個測試檔，在該命中的行上一行標 `// ruleid: <rule-id>`（預期真陽性）、在**不該**命中的行上一行標 `// ok: <rule-id>`（預期真陰性），跑 `semgrep --test` 讓 Semgrep 自動核對「實際命中」是否符合「你標的預期」。

測試檔 `cwe-strcpy.test.c`：

```c
#include <string.h>

void f(char *src) {
    char dst[64];
    // ruleid: insecure-strcpy
    strcpy(dst, src);

    // ok: insecure-strcpy
    strncpy(dst, src, sizeof(dst));
}
```

`strcpy` 那行標 `ruleid`（該報）、`strncpy` 那行標 `ok`（不該報，因為它不是 `strcpy`）。真跑 `semgrep --test --config cwe-strcpy.yaml cwe-strcpy.test.c`：

```
1/1: ✓ All tests passed
No tests for fixes found.
```

`1/1 ✓` = 規則的命中位置和你標的完全一致（該報的報了、該放過的放過了）。**這條規則現在有回歸測試了**——以後改 pattern、升 Semgrep 版本，跑一次 `--test` 就知道有沒有把它改壞。「No tests for fixes found」是提示：你有 `fix:` 卻沒測 fix 的正確性。

### 連 autofix 也測：`.fixed` 檔

`fix:` 會改程式碼，改成什麼也該測。放一個 `cwe-strcpy.test.fixed.c`，內容是**測試檔套用 autofix 之後應該長的樣子**：

```c
void f(char *src) {
    char dst[64];
    // ruleid: insecure-strcpy
    strlcpy(dst, src, sizeof(dst));   // <- 預期 autofix 後的結果

    // ok: insecure-strcpy
    strncpy(dst, src, sizeof(dst));
}
```

再跑 `--test`：

```
1/1: ✓ All tests passed
1/1: ✓ All fix tests passed
```

多了 `1/1 ✓ All fix tests passed`——Semgrep 把規則 autofix 套在 test 檔上、跟 `.fixed` 逐字比對，一致才過。**現在 autofix 改壞語意也會被測試抓到**。這是讓 autofix 規則敢上線的前提。

## metavariable-comparison：用數值條件降誤報

syntactic + regex 收窄的是「結構/文字」，`metavariable-comparison` 收窄的是**數值**——對綁到常數的 metavariable 做算術比較，只有滿足條件才算命中。例如「memcpy 的 size 超過 buffer 大小（>64）才報，小於等於就是安全」：

```yaml
rules:
  - id: memcpy-oversized-literal
    languages: [c]
    severity: WARNING
    message: "memcpy size $N exceeds 64-byte buffer"
    patterns:
      - pattern: memcpy($DST, $SRC, $N);
      - metavariable-comparison:
          metavariable: $N
          comparison: $N > 64
```

測試檔含三種 size：

```c
void g(char *src) {
    char buf[64];
    memcpy(buf, src, 128);   // 128 > 64  -> flag
    memcpy(buf, src, 32);    // 32 <= 64  -> safe
    memcpy(buf, src, 64);    // 64 not > 64 -> boundary safe
}
```

真跑，只命中 `128`：

```
┌────────────────┐
│ 1 Code Finding │
└────────────────┘
    mvcmp.c
    ❯❱ memcpy-oversized-literal
          memcpy size 128 exceeds 64-byte buffer
            4┆ memcpy(buf, src, 128);
```

`32`（≤64）和邊界 `64`（不 >64）都被正確排除。**這是精準度調校的典型手段**：不再是「所有 memcpy 都報」（Ch 13 的寬命中），而是「只報數值上真的越界的」，誤報大幅下降。注意邊界——`> 64` 讓剛好 64 通過，若語意該含 64 得寫 `>= 64`；**比較運算子的邊界就是規則的邊界**，寫錯一格就漏報或誤報邊界值。

## 精準度 vs 覆蓋率：怎麼調

規則工程的永恆張力（呼應 Ch 8 三角）：**pattern 越寬覆蓋越高但誤報越多，越窄誤報越低但漏報風險升**。手上的三把降誤報刀，按「代價由低到高」排：

```
降誤報工具箱
├─ pattern-not              減去已知安全形（Ch 13：sizeof 版 memcpy）
├─ metavariable-comparison  數值條件（本章：size <= buffer 就放過）
├─ metavariable-regex/pattern 對綁定值加文字/結構條件（Ch 13）
├─ pattern-inside           限定危險上下文（迴圈裡/無鎖區）
└─ 升級 mode: taint         要「值真的是攻擊者控制」才報（Ch 14）
```

調校流程：**先寬抓看命中總量與真陽性比例，再逐條加約束把假陽性形減掉，每加一個約束就跑 `--test` 確認沒把真陽性也砍掉**。沒有 test 檔，你每次調 pattern 都是盲改——可能修掉一個誤報同時引入三個漏報而不自知。**test 檔是調校的安全網**，這也是為什麼它排在規則工程的核心。

## 對比演進：從 Ch 13/14 的「能命中」到本章的「可維護」

| 階段 | 關注 | 產物 |
|---|---|---|
| Ch 13 | 規則命不命中對的結構 | 一條 pattern |
| Ch 14 | 規則追不追得到資料流 | 一條 taint rule |
| **Ch 15（本章）** | 規則**可信、可維護、好 triage** | rule + metadata/CWE + `fix:` + **test 檔** |

Ch 13/14 讓規則「能用」，本章讓規則「敢上線、能長期維護」。差別就在 metadata（下游好分流）、autofix（能修不只報）、**test（改不壞、可回歸）**。一套沒測、沒 CWE、ID 亂取的規則，命中再準也是技術債。

## 踩雷集錦

**錯誤直覺：「規則命中對了就能上線，測試是多餘的。」**
正確認識：沒 test 檔的規則是定時炸彈。你之後改 pattern 降誤報、升 Semgrep 版本、加個 `pattern-not`——任何一步都可能悄悄把真陽性砍掉或引入新假陽性，而你**不會發現**。`--test` 把「該報的報、該放的放」變成可自動驗證的回歸，是規則能長期演進的前提。命中準只是第一天的事，測試管的是往後每一天。

**錯誤直覺：「autofix 很方便，開了就好，反正是改成更安全的寫法。」**
正確認識：autofix **真的會改別人的程式碼**，改錯就是引 bug。`strcpy($DST,$SRC)`→`strlcpy($DST,$SRC,sizeof($DST))` 看似安全，但若 `$DST` 是指標而非陣列，`sizeof($DST)` 是指標大小（8）不是 buffer 大小——autofix 反而製造更難查的截斷 bug。**autofix 規則必須配 `.fixed` 測試證明它語意等價**，沒測的 autofix 比沒 autofix 危險。

**錯誤直覺：「metadata、CWE 是行政表格，能省就省。」**
正確認識：CWE 缺失讓每條命中都得人工歸類——triage 一百條命中時，有 CWE 是「這批 CWE-120 一起處理」，沒 CWE 是「一條條讀 message 猜屬於哪類」。metadata 不影響命中與否，但**直接決定命中出來之後的處理成本**。規則工程一半是為下游 triage 的人服務，CWE 是那半的核心。

**錯誤直覺：「一條規則多塞幾個意圖比較省事，少寫幾條。」**
正確認識：一條規則塞「抓 strcpy + 抓 memcpy + 抓 sprintf」三個意圖，會讓 message 講不清、CWE 對不準（三個問題不同 CWE）、誤報難 triage（不知道是哪個意圖誤報）、autofix 沒法統一。**一條規則一個意圖**：ID 清楚、message 精準、CWE 對得上、測試好寫、triage 好分。要抓多個相關形用 `pattern-either` 在同一意圖內合併，不是把不同意圖硬塞一條。

**錯誤直覺：「metavariable-comparison 的邊界差一格沒差。」**
正確認識：`$N > 64` 和 `$N >= 64` 差的是「剛好 64」這個邊界值報不報。安全語意上 `memcpy(buf[64], src, 64)` 恰好填滿是安全的，該用 `> 64`；但若 buffer 是 63 或有 off-by-one 語境，邊界就得跟著改。**比較運算子的邊界就是規則的判定邊界**，寫錯一格不是小事——邊界值往往正是最容易出 off-by-one 漏洞的地方，規則在那裡判錯等於在最關鍵處失效。務必為邊界值寫 `ok:`/`ruleid:` 測試釘死。

## 進階延伸

- **Semgrep 官方 *Testing rules* 文件**——`--test` 的完整語意、`// ruleid:` `// ok:` `// todoruleid:` 標註、`.fixed` fix 測試、多命中同行怎麼標。把本章的測試紀律做全。前提：本章 test 一節。
- **Semgrep registry 規則的 metadata 慣例**——官方規則的 `metadata` 欄位（`cwe`/`owasp`/`references`/`confidence`/`technology`）是業界標準模板，照抄能讓你的規則直接對接主流漏洞管理與報告流程。挑幾條 registry 規則讀它的 metadata 塊。前提：本章 metadata 一節。
- **autofix 的進階：`fix-regex` 與多行 fix**——單純 `fix:` 表達不了的改寫（要對綁定值做正則替換、跨多行重構）用 `fix-regex`。理解 autofix 能做到多複雜、以及為什麼越複雜的 fix 越需要嚴格 `.fixed` 測試。前提：本章 autofix 一節。

## 本章重點整理

- 上線級規則 = pattern/taint **＋ 好 ID ＋ message/metadata/CWE ＋ 對齊風險的 severity ＋（可選）autofix ＋ test 檔**。ID 是 triage 第一線索、CWE 決定命中好不好分流、severity 全標 ERROR 等於沒分級。
- **`fix:` autofix** 用 metavariable 綁定改寫程式碼（`strcpy`→`strlcpy(...,sizeof($DST))`），對機械式安全替換很值——但會真的改別人的碼，**必須配 `.fixed` 測試**證明不改壞語意。
- **test 檔是規則工程核心**：`// ruleid:`（該報）`// ok:`（不該報）+ `semgrep --test` 把規則正確性變可自動驗證的回歸。`1/1 ✓ All tests passed`／`All fix tests passed` 是規則敢演進、敢上線的前提。沒測的規則是定時炸彈。
- **`metavariable-comparison`** 用數值條件降誤報（size ≤ buffer 就放過），只報真的越界的常數。**比較運算子的邊界就是規則判定邊界**，差一格就在最關鍵的邊界值處判錯。
- 精準度 vs 覆蓋率靠工具箱調（`pattern-not`／`metavariable-comparison`／`metavariable-regex`／`pattern-inside`／升 taint），流程是「寬抓→逐條加約束→每步跑 `--test` 確認沒砍到真陽性」。**一條規則一個意圖**。

## 自我檢核

- 一條規則命中很準但沒 test 檔、沒 CWE、ID 叫 `rule1`。分別說出這三個缺失各會在什麼時候咬你一口。
- `// ruleid:` 和 `// ok:` 各標什麼？`semgrep --test` 印 `1/1 ✓` 代表什麼被驗證了？它怎麼防止你之後改 pattern 把規則改壞？
- 你要為一條 autofix 規則加測試，光 `.test.c` 夠嗎？還要什麼檔、驗證什麼？
- 用 `metavariable-comparison` 寫「memcpy size 大於等於 buffer 大小才報」的條件。`> 64` 和 `>= 64` 對「剛好 64」的判定差在哪？為什麼邊界值特別重要？
- 你手上一條規則同時抓 `strcpy`、`memcpy`、`sprintf` 三種問題。從規則工程角度列出至少三個「一條塞三意圖」帶來的具體壞處，以及正確做法。

## 延伸閱讀

- **Semgrep 官方 *Testing rules* + *Rule syntax* 文件**——`--test`、metadata 欄位、`fix`/`fix-regex`、severity 語意的權威定義。本章每個工程機制的查詢手冊，寫真規則庫時常駐。前提：本章。
- **Semgrep registry（原始碼倉庫）**——上千條**帶完整 test 檔與 metadata** 的維護中規則，是「業界怎麼工程化一條規則」的最佳範本。挑一個漏洞類，讀它的 rule + `.test` + metadata 三件套怎麼配。前提：本章全部。
- **Sadowski et al., *Lessons from Building Static Analysis Tools at Google* (CACM 2018)**——工業界為什麼把「誤報率」與「開發者信任」當規則上線的首要指標、為什麼寧可漏也要控誤報。把本章的 severity/測試/精準度紀律放進真實工程脈絡。前提：本章 + Ch 8。銜接 Ch 36 false positive governance。

規則工程化之後，你有了一套可信、可測、好 triage 的 Semgrep 規則。但目前都在單一語言內打轉——真實 codebase 是多語言混雜的（前端 JS、後端 Python、底層 C），漏洞常跨語言邊界流動。下一章把 Semgrep 拉到跨語言場景。

→ [Ch 16 跨語言 Semgrep](./16-semgrep-cross-language.md)
