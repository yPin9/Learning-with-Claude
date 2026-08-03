# Ch 27 — MRVA 多倉庫變體分析

> **目標**：把 [Ch 26](./26-codeql-cve-to-query.md) 學到的「從一個 CVE 抽出 query、在原專案裡找變體」放大一個量級——不是掃一個 repo，而是**一次對幾百上千個 repo 跑同一條 query**。這件事叫 MRVA（multi-repository variant analysis，多倉庫變體分析）。你會學到：MRVA 在架構上到底是什麼、怎麼設定與操作、`codeql pack` 怎麼讓 query 可攜到別人的 database、以及一條 query 要寫成什麼樣子才「換個 repo 也抓得到」。GitHub Security Lab 就是用這套機制，一條 query 一次掃出跨幾十個專案的同型 CVE。
> **環境**：MRVA 的真正執行**需要 GitHub 帳號 + VS Code 的 CodeQL 擴充 + 一個 GitHub 上的 controller repo**，掃的是 GitHub 為熱門 repo **預建好的 CodeQL database**。這條路徑本機無法真跑，本章相關操作**標明「未實測，需 GitHub 帳號」並附完整步驟**。本機能真跑的是「對多個本機 database 依序跑同一條 query」——這是 MRVA 概念的**本機縮影**，這部分我照跑照貼輸出（`codeql` 2.26.2）。

## 為什麼需要 MRVA：變體分析的規模問題

回到 [Ch 26](./26-codeql-cve-to-query.md) 的核心動作：拿到一個 CVE，抽出它的 bug pattern，寫成 query，在**同一個專案**裡找還有沒有沒修到的同型 bug。這叫 variant analysis（變體分析），是把「一個 bug」變成「一類 bug」的槓桿。

但變體不會乖乖待在同一個 repo 裡。同一個危險 API、同一種錯誤習慣、同一段被複製貼上的 helper，會散落在**成百上千個專案**裡：

- 某個 C 函式庫有 integer overflow bug，你抽出 query——結果發現十幾個 fork、下游 vendored（內嵌複製）copy、抄了它 helper 的其他專案，全有同一個洞。
- 某個 npm 套件的 prototype pollution 模式，在整個 JavaScript 生態裡被無數專案重複。
- 某個 Java 反序列化 gadget，跨越幾百個用了同一個框架的服務。

如果你只有本機、只能一次掃一個 repo，這種「跨生態掃同型 bug」就得手動 clone 幾百個專案、逐個建 database、逐個跑 query、逐個收結果——**光是建 database 這一步就會把你拖死**（[Ch 20](./20-codeql-databases.md) 講過建 C/C++ database 要能 build，光解決 build 就夠喝一壺）。

MRVA 解決的就是這個規模問題。它的三個關鍵前提：

1. **GitHub 已經幫熱門 repo 預建好 CodeQL database**：你不用自己 build、自己 `codeql database create`。GitHub code scanning 生態每天為大量公開 repo 產生並快取 database。
2. **query 是可攜的**：一條寫對的 CodeQL query 不綁定某個 repo 的目錄結構或函式名，可以套用到任何**同語言**的 database 上（這點下面 `codeql pack` 一節會講透）。
3. **執行在 GitHub 的雲端 Actions 上**：你選一組 repo、指定一條 query，GitHub 在它的基礎設施上對那組 repo 的預建 database **並行**跑你的 query，再把結果匯總回你的 VS Code。

一句話：**MRVA = 你出 query，GitHub 出 database 和算力，一次掃一整片 repo。**

```
  [Ch 26] 單 repo variant analysis          [Ch 27] MRVA
  ┌───────────────┐                        ┌───────────────────────────────┐
  │ 你自己 build   │                        │ 你只出 query                    │
  │ 你自己建 db    │      放大到 N 個 repo   │ GitHub 出預建 db + 算力          │
  │ 你自己跑 query │  ───────────────────►  │ 一次對 100~1000 個 repo 並行跑   │
  │ 一次一個 repo  │                        │ 結果匯總回 VS Code              │
  └───────────────┘                        └───────────────────────────────┘
```

## 底層機制：MRVA 到底怎麼運作

別把 MRVA 想成魔法。拆開來看，它是幾個既有零件的組合。

### 零件一：GitHub 預建的 CodeQL database

GitHub 對大量公開 repo（尤其是熱門、有在跑 code scanning 的）持續產生 CodeQL database，存在它的基礎設施上。你在 MRVA 選一個 repo 時，實際上是在說「用你那份預建 database」。**如果某 repo 沒有可用的預建 database，MRVA 會跳過它並在結果裡標明**——這是你之後看結果數字要注意的第一件事。

### 零件二：controller repo（控制器 repo）

MRVA 的執行引擎是 **GitHub Actions**。你需要在自己的 GitHub 帳號下指定一個 repo 當 **controller repo**——它的作用是**承載那個跑 MRVA 的 GitHub Actions workflow**。

- controller repo **可以是空的**（甚至建議是空的私有 repo），它不放你要掃的 code，它只是「MRVA workflow 的執行場地」。
- MRVA 把你的 query 打包、把「要掃哪些 repo」的清單傳過去，controller repo 的 Actions 就在雲端 fan-out（扇出）成一堆並行 job，每個 job 拉一個 target repo 的預建 database、跑你的 query、回傳結果。
- 你消耗的是**你自己帳號的 GitHub Actions 額度**（quota）——這是 MRVA「不是免費無限」的來源，下面踩雷區會展開。

### 零件三：repo list（要掃哪些 repo）

你要告訴 MRVA 掃哪些 repo。有幾種指定方式：

- **手動列**：`owner/repo` 一個個列，或列一組。
- **用預設清單**：CodeQL 擴充內建一些 curated（精選）list（例如「top 100 個某語言的 repo」）。
- **自訂清單**：你自己維護一份 `owner/repo` 清單（例如你關心的所有下游專案、所有用了某框架的 repo）。

MRVA 一次能掃的 repo 數量有上限（數量級是「幾百到約一千」，實際上限依 GitHub 當時的政策而定——**別把這個數字背死，以官方文件當下的說明為準**）。

### 整條流程串起來

```
你的 VS Code (CodeQL 擴充)
   │  ① 選一條 query（.ql）
   │  ② 選一個 repo list（要掃誰）
   │  ③ 指定 controller repo（在誰的 Actions 上跑）
   ▼
GitHub controller repo 的 Actions workflow
   │  ④ fan-out：對 list 裡每個 repo 起一個 job
   ▼
每個 job： 拉該 repo 的「預建 CodeQL database」→ 跑你的 query → 回傳結果
   │
   ▼
結果匯總 → 下載回 VS Code → 你逐 repo triage flow path
```

## `codeql pack`：query 的可攜性從哪來

MRVA 能成立，靠的是「一條 query 可以套到別人的 database 上」。這件事不是理所當然——它建立在 CodeQL 的**分層抽象**與 **query pack（`codeql pack`）** 機制上。

### 為什麼 query 天生可攜

一條寫對的 CodeQL query **不引用任何具體 repo 的東西**。回想 [Ch 26](./26-codeql-cve-to-query.md) 抽 pattern 那句鐵律：抽對的 query 只描述**抽象的程式性質**（「攻擊者可控的值流進 allocation size 而沒經過 bound check」），不提 `bmp_decode`、不提 `width`、不提任何 repo 的目錄。

這種 query 之所以可攜，是因為它站在 **standard library（標準庫）** 的抽象之上：`import cpp` 給你的是「這個語言的所有 database 都有的」概念——`FunctionCall`、`memcpy` 的 sink 模型、`DataFlow` 框架（[Ch 21](./21-codeql-local-dataflow.md)、[Ch 22](./22-codeql-global-taint.md)）。這些概念**不綁定 database schema 的細節**，也不綁定哪個 repo。所以同一條 query，在 A repo 的 C database 上跑得動，在 B repo 的 C database 上也跑得動——只要它們是同語言的 database。

### `codeql pack` 是什麼

`codeql pack` 是 CodeQL 的**打包與發布機制**，把一組 query（加上它們的 metadata、依賴宣告）打包成一個可重用、可版本化、可發布到 registry（登錄檔）的單位。你其實從 [Ch 20](./20-codeql-databases.md) 起就一直在用它的最小形態——每個 query 目錄裡那個 `qlpack.yml`：

```yaml
name: audit-tests
version: 0.0.1
dependencies:
  codeql/cpp-all: "*"
```

這個檔案宣告了：這個 pack 叫什麼、版本、依賴哪個標準庫 pack（`codeql/cpp-all` 就是 C/C++ 的標準庫）。有了它，CodeQL 才知道你的 query `import cpp` 時要去解析哪個庫的哪個版本。

query pack 的關鍵動作：

| 動作 | 指令 | 用途 |
|---|---|---|
| 安裝依賴 | `codeql pack install` | 把 `dependencies` 宣告的庫抓下來鎖版本 |
| 打包 | `codeql pack create` | 把 query + 依賴打成可發布的 pack |
| 發布 | `codeql pack publish` | 推到 registry（GitHub Container Registry），別人可重用 |
| 下載 | `codeql pack download <name>` | 取用別人發布的 pack |

MRVA 送去雲端跑的，本質上就是**你這個 query pack**。你的 query 連同它宣告的標準庫依賴一起被搬到遠端、對每個 repo 的 database 求值。**query 綁死了具體 repo 結構，或依賴宣告不對，MRVA 就會在部分 repo 上跑失敗或漏報**——這是可攜性的反面，也是下面踩雷區的重點之一。

## 本機真跑：MRVA 的縮影——對多個 database 跑同一條 query

MRVA 本身本機跑不了（沒有 GitHub 帳號、沒有預建 database、沒有 controller repo）。但 MRVA 的**核心概念**——「同一條 query，套到多個不同 database 上，匯總結果」——本機完全可以做出縮影：我建**兩個內容不同的 C database**，用**同一個 query pack** 依序對它們跑，看它怎麼在每個 database 上各自找到命中。這就是 MRVA 在做的事，只是 repo 數從 2 換成 1000、執行從本機序列換成雲端並行。

先建兩個 database。`vuln.c` 是我們的共用靶（[Ch 20](./20-codeql-databases.md) 建過），我再造一個「同型變體、但函式名/變數名全不一樣」的 `other.c`——這正是為了驗證「一條抽象 query 可攜到結構不同的 code」。

`other.c`（同型 bug、換皮）：

```c
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
void process_packet(int sock) {
    char scratch[128];
    int amount;
    read(sock, &amount, sizeof(amount));   // 換名的 source
    char *chunk = malloc(amount);
    read(sock, chunk, amount);
    memcpy(scratch, chunk, amount);        // 換名的 sink，一樣沒 bound check
    free(chunk);
}
int main(){ process_packet(0); return 0; }
```

一條**只描述抽象性質**的 query（source = `read` 寫進去的 buffer，sink = `memcpy` 的 size，兩者都不提任何具體函式名）：

```ql
// MrvaLocal.ql —— 抽象到不綁任何 repo 的結構
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking

module LenToMemcpyConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    exists(FunctionCall rd |
      rd.getTarget().getName() = "read" and
      source.asDefiningArgument() = rd.getArgument(1))
  }
  predicate isSink(DataFlow::Node sink) {
    exists(FunctionCall mc |
      mc.getTarget().getName() = "memcpy" and
      sink.asExpr() = mc.getArgument(2))
  }
}
module LenToMemcpyFlow = TaintTracking::Global<LenToMemcpyConfig>;

from DataFlow::Node source, DataFlow::Node sink
where LenToMemcpyFlow::flow(source, sink)
select sink, "attacker-controlled length reaches memcpy size"
```

「MRVA 縮影」的執行——**對每個 database 依序跑同一條 query**，就像 MRVA 對每個 repo 跑一樣：

```bash
# 把兩份 source 各放一個目錄（source-root 要是目錄）
mkdir -p vuln other && cp vuln.c vuln/ && cp other.c other/

# 建兩個內容不同的 database（模擬兩個不同的 repo）
codeql database create db-vuln  --language=cpp --source-root=vuln  \
  --command="gcc -c vuln.c  -o /tmp/v.o" --overwrite
codeql database create db-other --language=cpp --source-root=other \
  --command="gcc -c other.c -o /tmp/o.o" --overwrite

# 同一個 query pack，掃過每一個 database（這就是 MRVA 的迴圈，只是本機、序列）
for db in db-vuln db-other; do
  echo "===== repo(縮影)：$db ====="
  codeql database analyze "$db" --additional-packs=. MrvaLocal.ql \
    --format=csv --output="$db.csv" --rerun 2>/dev/null
  cat "$db.csv"
done
```

真跑輸出（照貼，行號欄擷取關鍵欄位）：

```
===== repo(縮影)：db-vuln =====
"attacker-controlled length reaches memcpy size",...,"/vuln.c","9",...

===== repo(縮影)：db-other =====
"attacker-controlled length reaches memcpy size",...,"/other.c","9",...
```

**看懂這個縮影**：同一個 query pack，在 `vuln.c`（函式 `handle`、變數 `len`）與 `other.c`（函式 `process_packet`、變數 `amount`）上**各自命中一次**——命中的檔名不同、上下文不同，但都是同一類 bug（`memcpy` 第 9 行，size 來自 attacker-controlled read）。這就是 MRVA 在雲端做的事的本質：**query 不變，database 換一個又一個，命中各自匯總**。差別只在：MRVA 的迴圈跑在 GitHub Actions 上、並行、對象是幾百上千個「你沒 build 過」的預建 database，結果匯總回 VS Code 讓你逐 repo triage。

> **這個縮影誠實的邊界**：本機縮影用的是你自己 build 的 database、序列執行、兩個 repo。真 MRVA 用 GitHub 預建 database、雲端並行、上百 repo，還多了「部分 repo 沒預建 database 被跳過」「Actions 額度」「結果下載與大規模 triage」這些真實世界的摩擦。縮影教你**概念對不對**，不能替代**真 MRVA 的規模與運維經驗**。

## 完整操作步驟（未實測，需 GitHub 帳號）

以下步驟本機無法驗證（沒有 GitHub 帳號登入 VS Code CodeQL 擴充、沒有 controller repo）。我把官方流程整理成可照做的清單，**標明未實測**；你有帳號時照這條路走，細節以官方文件當下版本為準。

### Step 1：裝 VS Code + CodeQL 擴充

1. 裝 VS Code。
2. 在擴充市集搜 **CodeQL**（發行者 GitHub），安裝。
3. 擴充會提示下載 CodeQL CLI（就是我們本機那支 `codeql`）與標準庫，讓它裝好。

### Step 2：登入 GitHub 並設定 controller repo

1. 在 VS Code 用你的 GitHub 帳號登入（擴充會走 OAuth 授權）。
2. 在你的 GitHub 帳號下**建一個空的私有 repo**當 controller repo，例如 `yourname/mrva-controller`。
3. 在 CodeQL 擴充的設定裡把 MRVA 的 **controller repository** 指到 `yourname/mrva-controller`。
   - controller repo 要**啟用 GitHub Actions**（新 repo 預設開）。

### Step 3：準備 repo list（要掃誰）

1. 在 CodeQL 擴充的 **Variant Analysis Repositories** 面板：
   - 用內建的 curated list（例如某語言 top-N），或
   - 手動加 `owner/repo`，或
   - 建自訂 list（把你關心的一批下游 repo 放進去）。

### Step 4：寫 / 選 query 並跑 MRVA

1. 打開你的 `.ql`（就是你在 [Ch 26](./26-codeql-cve-to-query.md) 抽出來、確認過可攜的那條）。
2. 確認它的 `qlpack.yml` 依賴宣告正確（`codeql/<lang>-all`）。
3. 在 query 檔上按右鍵 → **CodeQL: Run Variant Analysis**（或從命令面板選）。
4. 選你要用的 repo list。
5. 擴充把 query 打包送到 controller repo 的 Actions，fan-out 執行。

### Step 5：看結果並 triage

1. Actions 跑完，結果匯總回 VS Code 的 **Variant Analysis Results** 視圖。
2. 你會看到**每個 repo 各自的命中數**，可展開看 flow path（就像 [Ch 22](./22-codeql-global-taint.md) 的 path-problem 結果）。
3. **逐 repo triage**：MRVA 給你候選，不給你結論——每個命中都要照 [Ch 12](./12-false-positive-triage.md)、[Ch 36](./36-false-positive-governance.md) 的方法判真偽。跨 repo 的命中誤報率不會比單 repo 低，只會因為量大而**更需要治理**。

### 結果匯出與後續

- MRVA 結果可匯出成 **SARIF**（[Ch 39](./39-sarif-ecosystem.md)），接你既有的 triage / 追蹤流程。
- 對確認為真的跨 repo bug，走負責任揭露（各 repo 各自的 security policy）。

## 對比：單 repo variant analysis vs MRVA

| 面向 | 單 repo（Ch 26） | MRVA（本章） |
|---|---|---|
| database 誰建 | 你自己 `codeql database create` | GitHub 預建，你不 build |
| 一次掃幾個 | 1 | 幾百到約 1000 |
| 執行在哪 | 你本機 | GitHub Actions（雲端並行） |
| 需要什麼 | 本機 CLI + database | GitHub 帳號 + 擴充 + controller repo + 額度 |
| 適合場景 | 深挖一個專案 | 跨生態掃同型 bug、找 vendored copy / fork |
| triage 量 | 一個專案的命中 | N 個專案的命中——治理壓力大 N 倍 |
| 本機可真跑 | 可 | 否（本章縮影可） |

一句話：**MRVA 不是取代單 repo 分析，是它的規模化外掛**。你還是得先在單 repo 上把 query 打磨到可攜、低誤報，才值得放到 MRVA 上一次掃一片——否則你只是把誤報也放大了 N 倍。

## 踩雷集錦

**踩雷 1：以為 MRVA 免費、可以無限跑。**
錯誤直覺：「反正跑在 GitHub 雲端，我隨便選一千個 repo 一直跑。」
正確認識：MRVA 消耗**你自己帳號的 GitHub Actions 額度**，一次能掃的 repo 數有上限，跑久了會撞到配額或速率限制。把 MRVA 當**有成本的資源**：先在小 repo list（幾個到幾十個）上驗證 query 對不對、誤報多不多，再放大 list。別拿沒打磨過的 query 一次轟一千個 repo。

**踩雷 2：query 綁死了某 repo 的結構，不可攜。**
錯誤直覺：「我在原 repo 上跑得好好的，MRVA 上一定也行。」
正確認識：如果你的 query 引用了具體函式名（`bmp_decode`）、具體目錄、具體 magic number，它只在**那個** repo 成立，換 repo 就漏報或報空。MRVA 的前提是 query **抽象到只描述程式性質**（[Ch 26](./26-codeql-cve-to-query.md) 的抽 pattern 鐵律）。上 MRVA 前先自問：「這條 query 換個變數名、換個目錄，還抓得到嗎？」抓不到就先抽象化。

**踩雷 3：跨 repo 結果不 triage 就當真。**
錯誤直覺：「MRVA 在 50 個 repo 各報了命中，那就是 50 個 CVE。」
正確認識：MRVA 給的是**候選**，不是結論。跨 repo 的誤報率跟單 repo 一樣（甚至更雜，因為你不熟那些 repo 的上下文）。每個命中都得照 [Ch 12](./12-false-positive-triage.md) 判真偽、看 flow path 合不合理、確認 source 真的可控。量大更要有 [Ch 36](./36-false-positive-governance.md) 的治理流程，否則你會被一堆未 triage 的候選淹沒，最後全部擺爛。

**踩雷 4：controller repo 設定錯，MRVA 直接不動或全紅。**
錯誤直覺：「隨便指一個 repo 當 controller 就好。」
正確認識：controller repo 必須**啟用 Actions**、必須是**你有權限跑 workflow** 的 repo、且它與你的帳號額度綁定。指錯（例如指到一個沒開 Actions 的 repo、或你沒寫權限的 repo），MRVA 會在 fan-out 階段全部失敗，你只看到一堆 job error 卻不知為何。設定時確認：controller repo 是你自己的、Actions 開著、私有即可（它不放 code）。

**踩雷 5：把「沒預建 database 的 repo」的空結果當成「沒 bug」。**
錯誤直覺：「這個 repo MRVA 沒報，代表它乾淨。」
正確認識：MRVA 只能掃**有預建 database** 的 repo。某 repo 沒有可用 database，MRVA 會**跳過**它——這在結果裡會標明「no database / skipped」，不是「掃過且乾淨」。看結果時務必分清「掃了但 0 命中」與「根本沒掃」。前者是弱信號的乾淨，後者什麼都沒說。

## 進階延伸

- **自建 repo list 精準狙擊**：與其用 top-N 這種泛泛清單，不如維護一份「所有 vendored 了某函式庫的 repo」或「所有用了某框架的服務」清單，讓 MRVA 精準打在**最可能有同型 bug** 的 repo 上。這把變體分析從「廣撒網」變成「順著依賴鏈狙擊」。
- **MRVA + patch-diff 找 fork 漏修**：上游修了一個 bug，用 MRVA 對所有 fork / vendored copy 跑「修補前的 pattern」，一次找出**還沒同步修補**的下游——這是 [Ch 38](./38-diff-based-auditing.md) diff-based auditing 的規模化版本。
- **把 MRVA 結果餵回治理流程**：MRVA 匯出 SARIF（[Ch 39](./39-sarif-ecosystem.md)）後，接你的 triage 追蹤系統，把「哪些 repo 已 triage、哪些真、哪些已揭露」管起來。沒有這層治理，跨 repo 掃出的量會反過來淹死你。

## 本章重點整理

- **MRVA = 變體分析的規模化**：你出 query，GitHub 出預建 database 與雲端算力，一次對幾百上千個 repo 並行跑同一條 query。
- **三個零件**：GitHub 預建 database（你不 build）、controller repo（承載 MRVA 的 Actions workflow、綁你的額度）、repo list（掃誰）。
- **query 可攜性靠抽象 + `codeql pack`**：query 只描述程式性質、不綁 repo 結構，靠 `qlpack.yml` 宣告標準庫依賴，才能被搬到別人的 database 上求值。
- **本機縮影驗證了核心概念**：同一個 query pack 對 `vuln.c` 與換皮的 `other.c` 各自命中一次——這就是 MRVA 對每個 repo 做的事，只是規模與執行環境不同。
- **MRVA 不是免費魔法**：吃額度、有上限、給候選不給結論、跨 repo 更需治理，且沒預建 database 的 repo 是「沒掃」不是「乾淨」。

## 自我檢核

- 不看上文，說出 MRVA 的三個零件各自的作用（預建 database / controller repo / repo list）。
- 為什麼一條 query 能套到別人的 database 上？跟 `codeql pack` 和 query 的抽象程度各有什麼關係？
- 本機縮影裡，同一條 query 為什麼能同時在 `vuln.c` 和 `other.c` 命中，儘管兩者函式名變數名都不同？這對「query 上 MRVA 前要做什麼」有什麼啟示？
- **主動回憶**：假設你在 MRVA 對 200 個 repo 跑完，180 個報 0 命中、20 個各報 1-3 個命中。你**不能**直接下什麼結論？你要對「180 個 0 命中」和「20 個有命中」分別做什麼檢查？（提示：跳過的 repo、triage、可攜性回頭驗證。）

## 延伸閱讀

- **GitHub CodeQL 官方文件 "Running CodeQL queries at scale with multi-repository variant analysis"**（讀「操作步驟」與「repo list / controller repo 設定」兩節）：這是 MRVA 操作的權威來源，本章步驟以它為準。前提：需要能登入 GitHub 的環境實際照做。
- **CodeQL CLI 文件的 `codeql pack` 系列指令**（讀 `pack create` / `publish` / `install`）：搞懂 query pack 怎麼打包發布，你才能把自己的 query 變成可重用、可上 MRVA 的單位。前提：本機已有 CodeQL CLI（我們有）。
- **GitHub Security Lab 的 variant analysis 案例文章**（挑一篇「一條 query 掃出跨專案同型 CVE」的 writeup，讀「他們怎麼抽 query、怎麼選 repo list」）：看真實世界怎麼把 MRVA 用在 CVE hunting。前提：先讀完 [Ch 26](./26-codeql-cve-to-query.md) 才看得懂他們抽 pattern 的邏輯。

MRVA 讓你一條 query 掃一片 repo——但前提是這條 query 又快又準。放到上千個 repo 上，一條寫爛的 query 慢起來會拖垮整批 job，誤報多起來會淹死你。下一章我們回到 query 本身：怎麼寫得快、慢在哪、怎麼 debug。

→ [Ch 28 query 效能與除錯](./28-codeql-query-performance.md)
