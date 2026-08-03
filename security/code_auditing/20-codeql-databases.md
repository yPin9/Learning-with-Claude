# Ch 20 — 建 database：多語言抽取

> **目標**：把「建 db」從能跑一次的儀式變成能診斷、能信任的工程能力。分語言講建 db 的實務——C/C++（追 build：autobuild vs 手動 build command、CMake/make 情境、漏抽的坑）、Java、Python/JS/Go（免 build）；看 `codeql database create` 的產物結構與 `codeql resolve database`；學會**驗證 db 完整性**（該進來的 TU/行數進來了沒）；並親手建一個 python db 證明「免 build 直接 parse」。
> **環境**：CodeQL 2.26.2，WSL Ubuntu 22.04

Ch 18 你對 `vuln.c` 建過一次 cpp db，Ch 19 在上面寫查詢。那是最順的情況：單檔、`gcc -c` 一行搞定。真實專案不是——CMake 大專案、conditional compilation、多語言 mono-repo、CI 裡建 db。這章把「建 db」這件事的實務坑一次講清楚，核心命題是：**db 的完整性 = 你掃到多少東西的上限，而 C/C++ 的 db 完整性完全等於 build command 的覆蓋度**。建錯 db，後面 query 寫得再好都是掃半個專案還以為乾淨。

## 兩類語言，兩種抽取模型

CodeQL 支援的語言分成兩大類，建 db 的方式本質不同（Ch 18 提過，這裡展開）：

| 類別 | 語言 | 抽取方式 | 要 build command 嗎 |
|---|---|---|---|
| **編譯型（trace-based）** | C/C++、Java（javac）、C#、Go、Swift、Kotlin | extractor **攔截編譯器呼叫** | **要**（或 autobuild 猜） |
| **直譯/腳本（parse-based）** | Python、JavaScript/TypeScript、Ruby | extractor **直接 parse 原始檔** | **不要** |

Go 是個混合案例：它「需要編譯」但 CodeQL 的 Go extractor 有很好的 autobuild，多數情況 `--language=go` 不用手動給 command 也能建。

**為什麼分這兩類？** 回到 Ch 18：編譯型語言的「一份原始碼」≠「一份實際編譯的程式」——`#ifdef`、include path、build 條件決定了哪些 code 真的被編。extractor 要看到**編譯器實際看到的東西**，只能攔截編譯。而 Python/JS 沒有這層「編譯期組態決定原始碼形狀」的問題（沒有 C 前處理器那種展開），extractor 直接 parse `.py`/`.js` 就拿到完整資訊，不需要跑任何 build。

## C/C++：追 build 的三條路

C/C++ 是最需要小心的。有三種給 build command 的方式，由鬆到嚴：

**1. autobuild（自動猜 build 系統）**——不給 `--command`，加 `--language=cpp` 讓 CodeQL 自己猜：

```bash
codeql database create db --language=cpp --source-root=.
```

autobuild 會找 `Makefile`/`CMakeLists.txt`/`configure` 之類，猜一套 build 跑。**方便但脆弱**：專案有多套 build 系統、或需要特殊環境變數/前置步驟時，它常猜錯（下面踩雷）。

**2. 手動 build command（`--command`）**——最可靠，你明確告訴它怎麼 build：

```bash
# 單檔
codeql database create db --language=cpp --command="gcc -c vuln.c" --source-root=.
# make 專案
codeql database create db --language=cpp --command="make -j4" --source-root=.
# CMake 專案：先 configure，再讓 codeql 攔截 build 那步
codeql database create db --language=cpp \
  --command="cmake -S . -B build && cmake --build build -j4" --source-root=.
```

關鍵：`--command` 給的是**會實際觸發編譯的指令**。對 CMake，光 `cmake -S . -B build`（configure）不會編任何 TU——真正編譯在 `cmake --build`，extractor 要攔的是後者。configure 與 build 都放進 `--command` 才穩。

**3. indirect tracing / 手動 wrapper**——CI 或複雜 build 系統裡，用 `codeql database init` + `codeql database trace-command` 把 build 拆成多步攔截。這是 Ch 17/CI 場景的做法，本章知道它存在即可。

**乾淨重建的鐵律**：build 有快取（`make` 看到 `.o` 已存在就不重編），extractor 就攔不到那些 TU（沒有編譯動作可攔）。**建 db 前一定先 `make clean` / 刪 build 目錄**，強迫全量重編，否則靜默漏抽（踩雷會展開）。

## 真跑：對 vuln.c 建 cpp db（看產物結構）

重跑 Ch 18 的建庫，這次盯著產物。

```bash
cd ~/audit-lab
codeql database create vuln-db --language=cpp --command="gcc -c vuln.c" --source-root=.
```

輸出尾段（照貼）：

```
Running build command: [gcc, -c, vuln.c]
Finalizing database at /home/ypp/audit-lab/vuln-db.
Running TRAP import for CodeQL database at /home/ypp/audit-lab/vuln-db...
Importing TRAP files
Merging relations
Finished writing database (relations: 103.11 KiB; string pool: 2.13 MiB).
Successfully created database at /home/ypp/audit-lab/vuln-db.
```

**db 產物結構**（`ls ~/audit-lab/vuln-db/`，照貼）：

```
baseline-info.json      ← 抽取統計（LoC 等基線資訊）
codeql-database.yml     ← db 的 metadata（語言、建立時間、source 前綴）
db-cpp                  ← 真正的關係表 + string pool（QL 查的就是這裡）
diagnostic              ← 抽取診斷（哪些檔沒抽到、警告）
log                     ← extractor 完整 log
src.zip                 ← 原始碼快照（結果指回行號、給人看）
```

**`codeql-database.yml`**（照貼，關鍵欄位）：

```yaml
sourceLocationPrefix: /home/ypp/audit-lab
baselineLinesOfCode: 57          ← 抽取到的程式碼行數（含展開的 header）
primaryLanguage: cpp
creationMetadata:
  cliVersion: 2.26.2
  creationTime: 2026-08-02T15:00:57Z
finalised: true                  ← db 已封盤可查
```

`baselineLinesOfCode: 57` 就是**驗證完整性的第一個抓手**：`vuln.c` 只有 12 行，但 db 記了 57 行——因為 `#include` 展開的 header 內容也算進去了（對回 Ch 18 冒出的 `__builtin_bswap`）。真實專案裡，**這個數字若遠小於你預期的專案規模，八成是 build command 沒覆蓋全**。

**`codeql resolve database`**——用機器可讀的方式確認 db 的組態：

```bash
codeql resolve database ~/audit-lab/vuln-db
```

輸出（照貼，節錄）：

```json
{
  "sourceLocationPrefix" : "/home/ypp/audit-lab",
  "sourceArchiveZip" : "/home/ypp/audit-lab/vuln-db/src.zip",
  "datasetFolder" : "/home/ypp/audit-lab/vuln-db/db-cpp",
  "languages" : [ "cpp" ]
}
```

`languages: [cpp]` 確認語言、`datasetFolder` 指向真正被查的關係表目錄。`codeql resolve` 系列（還有 `resolve queries`、`resolve extractor`）是排查「query 找不到 db / 語言不符」時的第一站。

db 大小參考：`vuln-db` 約 11 MB、後面建的 `py-db` 約 4.9 MB——小檔就這量級。db 大小主要由 TU 數量與程式規模決定，直接關係到 query 的 evaluation 時間（Ch 28）。

## 真跑：自建 python 檔建 python db（免 build）

證明「parse-based 語言免 build」。建一個小 python 檔：

```python
# ~/audit-lab/pysrc/app.py
import os

def handle(req):
    cmd = req.get("cmd")        # source：attacker-controlled
    os.system("echo " + cmd)    # sink：command injection

def safe():
    os.system("echo hello")     # constant，非 tainted
```

建 db——**注意：沒有 `--command`**：

```bash
cd ~/audit-lab/pysrc
codeql database create ~/audit-lab/py-db --language=python --source-root=.
```

輸出尾段（照貼）：

```
[10] Extracted file /home/ypp/audit-lab/pysrc/app.py in 30ms
[INFO] Processed 2 modules in 0.22s
Finalizing database at /home/ypp/audit-lab/py-db.
Finished writing database (relations: 40.51 KiB; string pool: 2.06 MiB).
Successfully created database at /home/ypp/audit-lab/py-db.
```

`Extracted file .../app.py`——extractor **直接讀檔 parse**，全程沒有跑任何 build command，約 6 秒建完。`Processed 2 modules`：它連 `import os` 也一併處理了（`os.py` 被跳過，log 裡有 `Skipped built-in file .../os.py`——標準庫不進 db 但 import 關係有記）。

**跑一條 python query 證明多語言都通**。`os-system.ql`（配 `qlpack.yml` 依賴 `codeql/python-all`）：

```ql
import python

from Call c
where c.getFunc().(Attribute).getName() = "system"
select c, "os.system call at line " + c.getLocation().getStartLine()
```

輸出（照貼）：

```
Evaluation completed (2.4s).
|      c      |           col1           |
+-------------+--------------------------+
| Attribute() | os.system call at line 5 |
| Attribute() | os.system call at line 8 |
```

兩處 `os.system`（第 5 行的 tainted、第 8 行的常數）都抓到。**同一套 CodeQL、同一套 `from/where/select` 語言，換 `import python`、換 db，Python 就通了**——只是這裡的 db 是 parse-based、零 build command 建出來的。注意 `c.getFunc().(Attribute).getName()`：`os.system` 在 Python AST 裡 `system` 是 `os` 這個物件的 **attribute**，所以取 func 之後 cast 成 `Attribute` 再取名——各語言的標準庫 class 不同，但 QL 骨架一致。

## 驗證 db 完整性：建完別急著查

這是本章最實戰的一節。db 建成功 ≠ db 完整。**「db 建成功、query 跑得動、就是掃不到某些檔」是最陰的漏報**。建完 db 三個抽查動作：

1. **看 LoC 對不對**：`codeql database create` 結尾與 `codeql-database.yml` 的 `baselineLinesOfCode`。跟你對專案規模的預期比——差一個量級就是 build 沒覆蓋全。也可 `codeql database print-baseline <db>` 看逐語言/逐檔基線。
2. **看 diagnostic**：`<db>/diagnostic/` 與建庫 log 裡，extractor 會報「某檔 parse 失敗」「某編譯呼叫沒攔到」。C/C++ 尤其要看有沒有「no build command traced / extractor saw 0 compilations」這類警告——那代表 **db 幾乎是空的**（build command 根本沒觸發編譯）。
3. **抽查關鍵檔進來了沒**：對一個你**確定存在**的函式/檔跑一條最簡 query（像 Ch 18 的 `all-calls.ql`）。查不到你明知存在的東西 = 那部分沒進 db。

養成「建完先驗，再開始寫 query」的習慣，能省掉「花三天寫 query 掃不到 bug，最後發現半個專案沒抽進來」這種災難。

## 多專案 / 大 repo / CI 建 db 策略

- **mono-repo 多語言**：一次建一個語言的 db（`--language=cpp` 一個、`--language=python` 一個），或用 `--db-cluster` 一次建多語言到一個 cluster 目錄。查詢時各查各的 db。
- **大 C/C++ 專案**：`make clean` 後全量 build，`--command="make -jN"`。build 慢的話 db 建庫時間主要花在跑 build 本身（extractor 攔截的 overhead 相對小）。
- **CI 裡建 db**：GitHub Actions 用 `github/codeql-action`，它幫你 init/autobuild/analyze。自架 CI 用 `codeql database create` + `codeql database analyze` 產 SARIF（Ch 39）。**CI 場景常搭 diff-based 只分析變更**（Ch 38），但 db 本身通常還是全量建（增量建 db 有 overlay 機制但較進階）。
- **db 快取/複用**：db 可攜（Ch 18），CI 可以把建好的 db 存成 artifact 給多條 query job 復用——建一次、查多次，攤平建庫成本。這也是 Ch 27 MRVA「先各自建 db、再對一堆 db 跑同一 query」的基礎。

## 踩雷集錦

**錯誤直覺：「autobuild 會自己搞定，不用管 build command。」**
正確認識：autobuild **猜** build 系統，猜錯很常見——專案有多套 build（`Makefile` + `CMakeLists.txt`）、需要先設環境變數、需要 `./configure --with-...` 特定選項、或 build 依賴某個前置產生步驟時，autobuild 常跑出一個**只編了一部分或完全沒編**的結果。db 照樣建「成功」，但只有半個或空的專案進去。**能明確給 `--command` 就別靠 autobuild**；用了 autobuild 一定驗 LoC 與 diagnostic。

**錯誤直覺：「改了 code 不用重建 db，反正 query 會重跑。」**
正確認識：query 查的是 **db 快照**（Ch 18）。改了原始碼、db 沒重建，你查的是舊碼——「怎麼改結果都不變」。這在 debug query 對照 code 行為時最坑。**改被分析的原始碼 → 重建 db；改 query → 不用**。

**錯誤直覺：「make 一次就建好 db 了，重建直接再 make。」**
正確認識：`make` 有 incremental 快取——`.o` 已存在就不重編，extractor 就**攔不到那些 TU 的編譯**（沒有編譯動作可攔），那些檔靜默漏抽。**建 db 前必 `make clean` / 刪 build 目錄**強迫全量重編。這條與上一條合起來：C/C++ 建 db 的黃金流程是「clean → 全量 build 包在 `--command` 裡 → 驗 LoC/diagnostic」。

**錯誤直覺：「Python 建 db 一定成功，反正只是 parse。」**
正確認識：parse-based 也會失敗。**Python 版本不符**（extractor 預設用某版 Python 語法，你的 code 用了更新語法特性）會導致某些檔 parse 失敗、靜默不進 db；用了 CodeQL 沒裝對的 Python 環境也可能抽不到第三方套件的型別資訊。建完一樣要看 diagnostic 有沒有 parse error、`Extracted file` 的檔數對不對。

**錯誤直覺：「db 建成功就代表整個專案都掃進來了。」**
正確認識：「成功」只代表流程沒崩，不代表覆蓋完整。C/C++ 只編到一半、Python 有檔 parse 失敗、被 `.gitignore`/`--source-root` 排除的目錄——都能讓 db「成功但殘缺」。**建成功與掃完整是兩件事**，永遠用 LoC + diagnostic + 抽查 query 這三招驗一遍再開工。

## 進階延伸

- **overlay / 增量 db**：`codeql-database.yml` 裡有 `overlayBaseDatabase` / `overlayDatabase` 欄位——CodeQL 支援在一個 base db 上疊 overlay 只重抽變更的部分，給大 repo 的增量分析用。多數人手動建 db 用不到，但 CI 加速會碰到；它 sound 與否回到 Ch 5 的 summary 複用。
- **build tracing 內部機制**：C/C++ 的「攔截編譯」靠的是把 extractor 塞進 build 的執行路徑（Linux 上用 `LD_PRELOAD` / 或 wrapper 攔 `exec`）。理解這點就懂為什麼「build 有快取沒重編 = 攔不到 = 漏抽」——沒有 `exec(gcc ...)` 發生，就沒東西可攔。
- **extractor 診斷與 `database print-baseline`**：`codeql database print-baseline <db>` 給逐語言的基線 LoC，`<db>/baseline-info.json` 是機器可讀版。把「建完驗 LoC」自動化進你的建 db 腳本（建完自動印基線 + grep diagnostic 的 error），是把「別忘了驗」變成流程的實務做法。

## 本章重點整理

- **兩類語言兩種抽取**：編譯型（C/C++/Java/Go/C#，extractor 攔編譯，要 build command 或 autobuild）vs parse-based（Python/JS/Ruby，直接 parse 原始檔，**免 build**）。
- **C/C++ 三條 build 路**：autobuild（方便脆弱，易猜錯）、手動 `--command`（最可靠，CMake 要含 configure+build）、indirect tracing（CI/複雜 build）。**建前必 clean 強迫全量重編**，否則快取導致靜默漏抽。
- **db 產物**：`db-cpp`（真正被查的關係表）、`codeql-database.yml`（metadata，含 `baselineLinesOfCode`）、`src.zip`、`diagnostic`、`log`。`codeql resolve database` 確認組態。
- **建成功 ≠ 完整**。建完必驗三招：LoC 對不對、diagnostic 有無 error、抽查 query 找得到已知存在的東西。這是防「掃半個專案還以為乾淨」的關卡。
- **db 可攜、可複用**：CI 建一次存 artifact 給多 query job 用，是 Ch 27 MRVA 的基礎。

## 自我檢核

- 不看上文，說出「編譯型」與「parse-based」兩類語言建 db 的差別，各舉兩個語言，並解釋 C/C++ 為何非 build 不可、Python 為何不用。
- CMake 專案的 `--command` 只寫 `cmake -S . -B build` 為什麼會建出幾乎空的 db？該怎麼寫？
- 你 `make` 完直接 `codeql database create`，結果 db 的 LoC 遠低於預期。最可能的原因是什麼？怎麼修？
- 列出「建完 db 驗證完整性」的三個抽查動作，各自能抓到哪種殘缺。
- 為什麼「db 建成功」不等於「整個專案都掃進來了」？舉 C/C++ 與 Python 各一個「成功但殘缺」的情境。

## 延伸閱讀

- **CodeQL 官方文件 *Creating CodeQL databases*（codeql.github.com/docs → “Creating CodeQL databases”）整章**——各語言建 db 的權威做法，尤其 C/C++ 的 build command / autobuild / indirect build tracing 三種模式，與 `--db-cluster` 多語言。前提：本章。這是你建任何真實專案 db 的手冊。
- **CodeQL 官方文件 *Preparing your code for CodeQL analysis* 裡的 C/C++ 與 Python 各語言小節**——每語言的特有坑（C/C++ 的 compiler 相容、Python 的版本設定），對應本章的「Python 版本不符」「build 沒覆蓋」踩雷。前提：本章。
- **CodeQL 官方文件 *codeql database* CLI 參考（`create` / `print-baseline` / `resolve database` / `analyze`）**——本章用到的每個 CLI 指令的完整旗標，尤其 `print-baseline` 拿來驗完整性、`analyze` 產 SARIF（銜接 Ch 39）。前提：本章。
- **GitHub *codeql-action* 的 README / workflow 範例（github.com/github/codeql-action）**——CI 裡怎麼 init/autobuild/analyze，本章「CI 建 db」的實作參照。前提：本章 + 基本 GitHub Actions 概念。銜接 Ch 17、Ch 38。

到這裡，CodeQL 的地基三章齊了：心智模型（Ch 18）、QL 語言（Ch 19）、建 db 的工程與驗證（本章）。你能建出可信的 db、能寫 `from/where/select` + class + 遞迴查它。但目前查的都還是「語法層」的東西（哪裡呼叫了 memcpy）。下一章進入 CodeQL 的真正主戰場——dataflow：先從 **local dataflow**（函式內的值怎麼流）開始，把 Ch 4 的 def-use 變成 QL 能查的 `DataFlow::Node` 與 `flowsTo`。

→ [Ch 21 CodeQL local dataflow](./21-codeql-local-dataflow.md)
