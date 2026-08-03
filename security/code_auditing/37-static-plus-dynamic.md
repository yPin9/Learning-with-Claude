# Ch 37 — 靜態 + 動態驗證

> **目標**：把靜態命中從「工具說它可疑」升級成「我讓它真的 crash 了一次」。你會學到怎麼從一個 SAST 命中提取可疑函式與路徑、寫最小 harness 給 fuzzer 或符號執行、以及本章的核心——對 `~/audit-lab` 的 memcpy OOB 命中親手跑出一個真正的 ASan crash，閉合「靜態懷疑 → 動態確認」的環。
> **環境**：WSL、gcc（含 AddressSanitizer）、semgrep/codeql（產命中）、python3（造輸入）。靶在 `~/audit-lab/ch37/`。fuzzer/符號執行深入見 `../advanced_fuzzing/`、`../symex_taint/`。

回到 Ch 2 的老問題：靜態分析是 **sound but not complete** 或反之，但它**永遠不是「確認」**。一個 SAST 命中的真正語意是：「在我的抽象模型下，存在一條從 source 到 sink、看起來沒被 sanitize 的路徑。」這句話裡每個詞都可能出錯——抽象模型過近似、path 其實 infeasible、sanitizer 沒被建模。所以哪怕排序後排第一、三個工具都同意的命中，它仍只是**可疑**。

要從「可疑」變「確認」，只有一條路：**讓程式真的執行那條路徑並觀察到壞事發生**。這就是動態驗證。靜態給你候選與覆蓋，動態給你可信的 PoC 與可達性證明。本章教你把兩者接起來。

---

## 為什麼靜態與動態互補（對回 Ch 2 sound/complete）

用 sound/complete 的框架看得最清楚：

```
             找出所有真 bug（complete）   不誤報（sound）    產物
靜態分析      追求（掃全部路徑）           很難              「可疑」候選集，覆蓋廣
動態驗證      不追求（只看跑到的路徑）      是（跑到就是真的）  「確認」的 PoC + 觸發輸入
```

- **靜態的強項是覆蓋**：它不需要你提供輸入，就能掃過所有函式、所有分支，指出「這裡可能有問題」。弱項是它不知道那條路徑實際上能不能被觸發、觸發要什麼輸入。
- **動態的強項是可信**：程式真的跑到那行、sanitizer 真的報 stack-buffer-overflow，那就是**鐵證**，不是「模型推測」。弱項是它只能看到「跑到的路徑」——沒觸發不代表沒 bug（可能是你的輸入/harness 沒覆蓋到）。

兩者接起來就補上了彼此的洞：**靜態縮小搜尋空間（幾萬行 → 幾十個候選），動態把候選一個個變成確認或存疑**。這正是為什麼現代審計流程是「靜態撒網、動態收口」，而不是二選一。

---

## 底層機制：從靜態命中到動態 PoC 的四步

### 步驟 1：從命中提取「可疑函式 + 可疑路徑」

一個好的命中（尤其有 codeFlow 的 taint 命中）已經告訴你：

- **sink 在哪**：`vuln.c:12` 的 `memcpy(buf, data, len)`。
- **source 在哪**：`read(fd, &len, ...)`，`len` 是外部可控。
- **path**：`len` 從 `read` 流到 `malloc` 再流到 `memcpy` 的第三參數，中間沒有 bound check。

你要從這裡萃取出**驅動這條路徑所需的入口**：哪個函式是頂層可觸發的？它吃什麼輸入？以本例是 `handle(int fd)`，吃一個 fd 上的 byte stream。這決定了 harness 長什麼樣。

### 步驟 2：寫最小 harness

Harness 是「把外部輸入餵到可疑函式」的最小驅動程式。目標是**用最少的碼把那條路徑暴露給 fuzzer 或直接的輸入**。對本例，靶已經自帶 `main(){ handle(0); }`——它從 fd 0（stdin）讀，所以「harness」就是「餵 stdin」。對一個更深的函式（例如 `parse_packet(char *buf, int len)`），你得自己寫：

```c
// libFuzzer 風格 harness：把 fuzzer 的 bytes 直接餵給目標函式
#include <stdint.h>
#include <stddef.h>
extern void parse_packet(const uint8_t *buf, size_t len);
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    parse_packet(data, size);   // 把 fuzz 輸入接到可疑路徑入口
    return 0;
}
```

Harness 寫得好不好，決定 fuzzer 到不到得了那條路徑（見踩雷）。

### 步驟 3：用 sanitizer build

**這一步最常被忘、卻最關鍵**。不開 sanitizer，一個 stack OOB write 可能只是默默踩壞相鄰變數、程式照跑不 crash，你就以為「沒事」。AddressSanitizer（ASan）在每個記憶體存取插入 shadow memory 檢查，讓越界**當場 abort 並印出精確位置**。編譯時加 `-fsanitize=address -g` 是動態驗證記憶體安全 bug 的前提。

### 步驟 4：驅動——給輸入或給 fuzzer

有了 sanitizer build，兩條路：

- **手工造輸入**（若你從命中就看得出觸發條件）：像本例，`len` 只要 > 64（buf 大小）就 OOB，你可以直接手刻一個 `len=200` 的輸入。快、精確、當 PoC。
- **交給 fuzzer**（若觸發條件不明顯）：把 harness 丟給 AFL++/libFuzzer，讓它自動找觸發輸入。適合路徑深、條件複雜、你看不出該餵什麼的情況。directed fuzzing（如 AFLGo）更進一步——你告訴它「目標是 `vuln.c:12`」，它會**優先探索能接近那行的路徑**，比盲 fuzz 快得多命中特定 sink。深入見 `../advanced_fuzzing/`。

若要的不是「找觸發輸入」而是「證明可不可達 + 求出觸發條件」，用符號執行：把路徑約束交給 solver，它回一組滿足約束的輸入，或證明無解（infeasible → 靜態命中是誤報）。深入見 `../symex_taint/`。

---

## 範例一：memcpy OOB 從靜態命中到 ASan crash（真跑，核心閉環）

這是本章要你親手做完的閉環。靶 `~/audit-lab/ch37/vuln.c`（加了 `read` 回傳值檢查讓它更像真碼，但 `len` 仍沒對 `buf` 大小做 bound check）：

```c
void handle(int fd) {
    char buf[64];
    int len;
    if (read(fd, &len, sizeof(len)) != sizeof(len)) return;
    char *data = malloc(len);
    if (!data) return;
    if (read(fd, data, len) != len) { free(data); return; }
    memcpy(buf, data, len);           // sink: OOB write, len unchecked
    printf("copied %d bytes\n", len);
    free(data);
}
int main(){ handle(0); return 0; }
```

**先靜態指出可疑點**（Semgrep 規則 `unbounded-memcpy`：memcpy 的長度不是 `sizeof(...)`）：

```bash
cd ~/audit-lab/ch37
semgrep --config memcpy-oob.yaml vuln.c --sarif -o out.sarif -q
jq -r '.runs[0].results[] | "\(.ruleId)  \(.locations[0].physicalLocation.artifactLocation.uri):\(.locations[0].physicalLocation.region.startLine)"' out.sarif
# unbounded-memcpy  vuln.c:12
```

靜態說：`vuln.c:12` 可疑，`len` 沒被 bound。但**這還只是懷疑**——也許上游別處其實 clamp 了 `len`？也許這函式根本不可達？我們要動態證。

**步驟 3：sanitizer build。**

```bash
gcc -g -fsanitize=address -o vuln_asan vuln.c
```

**步驟 4：手工造觸發輸入。** 命中告訴我們 `len` 是前 4 個 byte（`sizeof(int)`），`buf` 是 64 bytes。所以餵一個 `len=200`（> 64）再接 200 bytes payload，就會讓 `memcpy` 越界寫爆 `buf`：

```bash
python3 -c 'import sys,struct; sys.stdout.buffer.write(struct.pack("<i",200)+b"A"*200)' > input.bin
./vuln_asan < input.bin
```

**真實 ASan 輸出**（節錄關鍵段）：

```
=================================================================
==354258==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffd3aade9c0 ...
WRITE of size 200 at 0x7ffd3aade9c0 thread T0
    #0 ... in __interceptor_memcpy ...
    #1 0x63e3e81574cc in handle /home/ypp/audit-lab/ch37/vuln.c:12
    #2 0x63e3e8157590 in main /home/ypp/audit-lab/ch37/vuln.c:16
    ...
Address 0x7ffd3aade9c0 is located in stack of thread T0 at offset 128 in frame
    #0 ... in handle /home/ypp/audit-lab/ch37/vuln.c:5
  This frame has 2 object(s):
    [48, 52) 'len' (line 7)
    [64, 128) 'buf' (line 6) <== Memory access at offset 128 overflows this variable
SUMMARY: AddressSanitizer: stack-buffer-overflow ... in __interceptor_memcpy
```

**這就是閉環完成。** 讀懂這份輸出，靜態的「可疑」變成了鐵證：

- `WRITE of size 200`：確實寫了 200 bytes。
- `#1 ... in handle .../vuln.c:12`：確實是那行 `memcpy`，跟靜態命中的位置**完全對上**。
- `'buf' (line 6) <== ... overflows this variable`：確實爆的是那個 64-byte stack buffer，越界寫入到 offset 128（buf 從 64 到 128，我們寫到了 128+）。
- source 到 sink 的因果被程式的真實執行走了一遍。

現在你手上有：確認的 bug + 可重現的 PoC 輸入（`input.bin`）+ 精確的 crash 位置與型別（CWE-787 stack-buffer-overflow）。這份東西才是能寫進報告、能發 CVE、能讓維護者立刻信服的證據。純靜態命中做不到這點。

### 邊界失敗一：不開 sanitizer 就以為沒事

如果你 build 時忘了 `-fsanitize=address`：

```bash
gcc -g -o vuln_plain vuln.c
./vuln_plain < input.bin
```

程式很可能印出 `copied 200 bytes` 然後**正常結束或在別處才崩**——因為 stack overflow 踩壞的相鄰記憶體不一定立刻致命。你會誤判「動態沒觸發 → 這是誤報」。**這是本章最致命的陷阱**：沒 sanitizer 的動態驗證對記憶體安全 bug 基本無效。ASan 才讓越界「當場現形」。

### 邊界失敗二：輸入沒滿足前置條件，路徑沒走到

若你只餵 3 個 byte（`len` 讀取失敗，`read(...) != sizeof(len)` 成立）：

```bash
python3 -c 'import sys; sys.stdout.buffer.write(b"AAA")' | ./vuln_asan
# 無 crash：第一個 if 就 return 了，根本沒到 memcpy
```

沒 crash **不代表沒 bug**——是你的輸入沒滿足到達 sink 的前置條件（這裡是「前 4 byte 完整讀到 + 後續 payload 長度對得上」）。這正是動態的弱項：**沒觸發 ≠ 誤報，可能只是覆蓋不足**。要嘛更仔細構造輸入，要嘛交給 fuzzer 讓它自己找。

---

## 範例二：條件不明顯時交給 fuzzer（概念 + harness）

本例的觸發條件簡單到可以手刻。但真實的 bug 常藏在「先過 magic number 檢查、再過長度欄位、再到某個 state 才 OOB」的深路徑，你看不出該餵什麼。這時 harness + fuzzer 上場。假設可疑函式是 `handle`，但它從 fd 讀，我們改寫成吃 buffer 的版本並包 libFuzzer harness：

```c
// harness.c — 把 fuzz bytes 當成「原本從 fd 讀到的內容」
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 4) return 0;
    int len; memcpy(&len, data, 4);      // 模擬第一個 read 讀 len
    char buf[64];
    if (len < 0 || (size_t)len > size - 4) return 0;  // 模擬第二個 read 的長度檢查
    memcpy(buf, data + 4, len);          // 同一個未 bound 的 sink
    return 0;
}
```

```bash
# clang -g -O1 -fsanitize=address,fuzzer harness.c -o fuzz_harness
# ./fuzz_harness -runs=100000
# 預期：libFuzzer 幾秒內找到 len > 64 的輸入，ASan 報 stack-buffer-overflow，
#       並把觸發輸入存成 crash- 檔案（可當 PoC）。
```

> 上面的 clang/libFuzzer 執行標「未實測」——此環境的 gcc 版 ASan 已在範例一真跑驗證了同一個 sink 的 crash；libFuzzer 需要 clang 且行為與範例一等價（同一 `memcpy` OOB），故此處只給 harness 與命令，實跑請在裝有 clang 的環境執行。**重點在 harness 怎麼把 fuzz 輸入接到可疑路徑入口**，這是 `../advanced_fuzzing/` 會深挖的核心技能。

harness 的關鍵設計：**把「原本從外部來的輸入」對映到 fuzzer 給的 bytes**，並複製原碼裡到達 sink 前的所有前置檢查（這裡是 `size >= 4`、長度欄位解析）。少複製一個檢查，fuzzer 可能永遠到不了 sink（或觸發的是 harness 自己的 bug 而非真 bug）。

---

## 對比：手工輸入 vs fuzzer vs 符號執行

```
              什麼時候用                    給你什麼                 成本
手工造輸入    命中已看出觸發條件（如本例）    最快的 PoC              低，但靠人腦
fuzzer        觸發條件不明顯、路徑深          自動找到觸發輸入        中，要寫 harness
directed fuzz 想針對特定 sink 行（AFLGo）     偏向該 sink 的探索       中高，要標目標
符號執行      要證可達性 / 求精確約束         觸發輸入 or「infeasible」 高，路徑爆炸風險
```

三者不互斥，是漏斗的不同深度。多數命中手工幾分鐘就能證或否證；證不了的丟 fuzzer；fuzzer 也啃不動的深條件路徑，用符號執行求約束或證 infeasible。

---

## 踩雷集錦

**錯誤直覺一：靜態工具三個都同意，那就是真 bug 了，不用動態驗證。**
→ 正確認識：三個工具同意只提升「可疑度」，不等於「確認」——它們可能共享同一個錯誤假設（都沒建模你的 sanitizer、都認為某 path 可達其實不然）。只有讓程式真的走那條路並觀察到壞事（ASan crash / solver 給出觸發輸入）才算確認。工具共識是排序訊號，不是判決。

**錯誤直覺二：fuzzer 跑了一整天沒 crash，所以那個命中是誤報。**
→ 正確認識：沒 crash 有兩種可能——真的無 bug，或**你的 harness 根本沒 fuzz 到那條路徑**（入口沒接對、前置檢查沒複製、覆蓋率沒到那個函式）。先用覆蓋率工具確認 fuzzer 真的執行到了 sink 所在的 basic block，沒覆蓋到就別下「誤報」結論。動態的「沒觸發」永遠是弱證據。

**錯誤直覺三：動態驗證嘛，編譯跑起來看有沒有 crash 就好。**
→ 正確認識：不開 sanitizer 的話，記憶體安全 bug（OOB、UAF、越界讀）多半**不會當場 crash**——踩壞的記憶體要嘛沒被立刻用到、要嘛剛好踩在無關資料上。你會得到「跑起來好好的」的假象。記憶體安全 bug 的動態驗證**必須**開 ASan（或 MSan/UBSan 視 bug 類型），這是前提不是選項（見範例一邊界失敗一）。

**錯誤直覺四：harness 隨便把輸入丟給函式就行。**
→ 正確認識：harness 要**複製從入口到 sink 的所有前置條件**，並正確對映輸入格式。少一個 magic number 檢查、長度欄位解析錯位，fuzzer 要嘛到不了 sink（永遠沒 crash），要嘛觸發的是 harness 自己引入的 bug（假 crash）。harness 品質直接決定動態驗證有沒有意義——這是 `../advanced_fuzzing/` 的重頭戲。

**錯誤直覺五：動態確認了一次 crash，就等於利用完成。**
→ 正確認識：ASan crash 證明的是「存在可觸發的記憶體錯誤」（bug 確認），不等於「可穩定利用成 RCE」（exploit）。從 crash 到可控利用還有一大段（控制 crash 落點、繞過緩解），那是 exploitation 課的範疇。審計階段，「確認的可觸發 bug + PoC」已經是交付物；別把「證明 bug 存在」和「證明可利用」混為一談。

---

## 進階延伸

- **coverage-guided 反饋到靜態**：fuzzer 跑出的覆蓋率資訊可以反饋回 triage——若某個高排序的靜態命中所在 basic block，fuzzer 跑了很久都**覆蓋不到**，這本身是「這條路徑可能很難到達甚至 infeasible」的訊號，可以據此調整該命中的排序或標「需符號執行進一步證」。靜態、動態、triage 三者形成回圈，而非單向管線。
- **sanitizer 的分工**：ASan 抓 OOB/UAF/double-free；MemorySanitizer（MSan）抓未初始化記憶體讀取；UndefinedBehaviorSanitizer（UBSan）抓整數溢位、對齊、型別混淆等 UB。靜態命中的 CWE 類型決定你該開哪個——一個「整數溢位導致的分配過小」命中，光開 ASan 可能抓到後果（OOB），但配 UBSan 能直接抓到根因（溢位那一刻）。選對 sanitizer 讓動態證據更貼近命中的因果。
- **directed fuzzing 與靜態命中的天作之合**：AFLGo 這類 directed fuzzer 需要「目標位置」當輸入——而靜態命中**恰好就是**一組精確的目標位置（`vuln.c:12`）。把 SAST 命中直接餵給 directed fuzzer 當目標，是「靜態撒網、動態精準收口」最工程化的形態，比盲 fuzz 命中特定候選快一個數量級。深入見 `../advanced_fuzzing/` 的 directed fuzzing 章節。

---

## 本章重點整理

- 靜態命中的真正語意是「在抽象模型下存在可疑 source→sink 路徑」，永遠是**可疑**不是**確認**。抽象過近似、path infeasible、sanitizer 未建模都會讓它出錯。
- 動態驗證把「可疑」變「確認」：讓程式真的走那條路並觀察到壞事。靜態給覆蓋與候選，動態給可信 PoC 與可達性證明——對回 Ch 2 的 sound/complete，兩者互補。
- 四步閉環：**從命中提取可疑函式+路徑 → 寫最小 harness → sanitizer build → 給輸入或給 fuzzer**。
- 本章核心真跑：對 `~/audit-lab/ch37` 的 memcpy 命中，`gcc -fsanitize=address` build + 手工造 `len=200` 輸入，跑出 ASan `stack-buffer-overflow`，crash 位置 `vuln.c:12` 與靜態命中**完全對上**——這就是閉環。
- 兩個弱證據要牢記：沒開 sanitizer 的「沒 crash」對記憶體 bug 無效；沒覆蓋到 sink 的「fuzzer 沒 crash」不等於誤報。

## 自我檢核

- 用 sound/complete 的框架說明：為什麼靜態的「可疑」和動態的「確認」是互補而非重複？各自的強項與弱項是什麼？
- 給範例一的 ASan 輸出，指出哪三行分別證明了「寫了多少」「在哪一行」「爆的是哪個變數」。這份輸出比純靜態命中多給了你什麼？
- 為什麼「記憶體安全 bug 的動態驗證必須開 sanitizer」？不開會出現什麼假象？舉一個「不開 ASan 就以為沒事」的具體情境。
- fuzzer 跑一天沒 crash，你在下「誤報」結論前必須先確認什麼？用什麼工具確認？
- 寫一個吃 `parse(char *buf, int len)` 的 libFuzzer harness，需要複製哪些從入口到 sink 的前置條件？漏掉會怎樣（兩種失敗）？
- directed fuzzing（AFLGo）和一般 fuzzing 差在哪？為什麼靜態命中天生就是 directed fuzzing 的好目標？

## 延伸閱讀

- **AddressSanitizer 官方文件（Clang/GCC 的 `-fsanitize=address` 頁面）與原始論文**——搞懂 shadow memory 怎麼在每次記憶體存取插檢查、為什麼它能當場抓越界。用法：本章範例一的 crash 輸出你要能逐行讀懂，先讀它了解 shadow byte、redzone、frame layout 的意義。前提：C 記憶體模型（Ch 24 有鋪墊）。
- **`../advanced_fuzzing/` 課程的 harness 設計與 directed fuzzing 章節**——本章的 fuzzer 部分只點到，那門課把「怎麼寫好 harness」「AFL++/libFuzzer 怎麼用」「AFLGo 怎麼 directed」講透。用法：手工輸入證不了的深路徑命中，接那門課學自動化。前提：本章的四步閉環。
- **`../symex_taint/` 課程的可達性與約束求解章節**——「這條 path 真的可達嗎」的硬核解法：用 solver 證觸發輸入或證 infeasible。用法：fuzzer 也啃不動、你又想要「數學上證明可不可達」時讀。前提：符號執行基礎；接 Ch 12 的 infeasible path 討論。
- **libFuzzer 官方 tutorial**——最小 harness（`LLVMFuzzerTestOneInput`）怎麼寫、怎麼把命中函式接進去、crash 檔案怎麼當 PoC。用法：照著把本章範例二的 harness 在裝 clang 的環境真跑一次，補上本章標「未實測」的那段。前提：本章範例二。

你已經能把一個靜態命中打成確認的 crash 了。但到目前為止我們都在審**整個** codebase。真實工程場景裡，你更常面對的是「這個 PR 改了 30 行，幫我看有沒有引入漏洞」——只審**改動**，而非每次重掃全世界。下一章我們做 diff-based 審計：git diff 範圍限定、Semgrep baseline、以及從 security patch 反推漏洞的 variant hunting。

→ [Ch 38 diff-based 審計](./38-diff-based-auditing.md)
