# 練習 E — Joern 無 build dataflow 查詢

> **目標**：親手驗證 Joern 的殺手級場景（Ch 32）——對一個**故意編不起來、殘缺**的 C 檔跑 dataflow 查詢，證明 CodeQL（以及任何要 build 的工具）在此**卡死**，而 Joern 的 fuzzy parser 照樣 parse 出近似 CPG、`reachableByFlows` 照樣抓到 taint flow。做完你會用手驗證 Ch 29-32 那句核心論斷：「build 不了的 target 上，Joern 不可取代」——這不再是讀來的，是你看著 gcc 報 error 退出、Joern 卻吐出完整 flow path 的親眼所見。
>
> **環境**：Joern 4.0.594，WSL Ubuntu 22.04，gcc（模擬 CodeQL 的 build 前置——CodeQL 建 database 前底層要能編譯，gcc 編不過就是 CodeQL 建不出 database 的代理指標）。工作目錄 `~/audit-lab/broken/`。所有輸出照貼真跑結果。對回 [Ch 32 Joern vs CodeQL](./32-joern-vs-codeql.md)、CodeQL 版比較見 [練習 D](./practice-d-codeql-variant-analysis.md)。

前面練習 D 你在能 build 的 target 上用 CodeQL 做變體分析——那是 CodeQL 的主場。這個練習反過來：把 target 換成**編不起來的殘缺 code**，看兩個平台的命運分岔。這是 Joern 存在的理由，也是你以後面對韌體片段、閉源 SDK、漏洞回報只附幾個檔時的標準操作。

## 任務規格

### 你要構造的東西

一個**殘缺但仍有完整 taint flow 的 C 片段**。「殘缺」要同時踩到幾種讓編譯器死掉的情況，但 taint 路徑（攻擊者控制的長度 → 危險 copy）必須還在：

- **缺 include**：用了 `uint32_t` 卻不 `#include <stdint.h>`（gcc 直接 `unknown type name` 報 error 退出）。
- **呼叫未定義函式**：`net_read()`、`alloc_buf()`、`log_event()` 都沒宣告沒定義（gcc 至少 warning，嚴格模式 error）。
- **語法不全**：故意漏一個分號（真正的 syntax error）。
- **沒有 `main`、沒有 build 系統**：湊不齊可編譯單元。

但**保留 taint flow**：攻擊者控制的 `sz`（從 `net_read` 讀進來）一路流到 `memcpy` 的 size 參數（`stack_buf` 只有 128 byte → OOB write）。

### 驗收標準

明確的三條，缺一不可：

1. **證明 build 會失敗**：`gcc -c frag.c` 回**非 0** exit code（代表 CodeQL 建 database 也會卡死）。貼出 gcc 的 error 訊息。
2. **證明 Joern 能 parse**：`importCode` 成功，`cpg.method.name.l` 列得出 `process_packet`（以及未定義函式的 stub）。
3. **證明 Joern 抓到 flow**：`snk.reachableByFlows(src)` 對「`net_read` 的 `sz` → `memcpy` 的 size」回**至少一條** flow path，且 path 逐節點印得出來（從 `net_read` 那行到 `memcpy` 那行）。

三條都達成 = 你用手證明了「CodeQL 卡死、Joern 照跑」。

## 分五步

1. **寫殘缺 C**：在 `~/audit-lab/broken/frag.c` 構造上面規格的片段。確認它同時有「編不過的原因」和「完整的 taint flow」兩者。
2. **跑 gcc 證明編不過**：`gcc -c ~/audit-lab/broken/frag.c -o /tmp/frag.o`，看 exit code（`echo $?` 要非 0）與 error 訊息。這一步代理「CodeQL database create 會失敗」。
3. **Joern importCode**：`importCode(inputPath="broken/frag.c", ...)`，確認 `Code successfully imported`、列出 methods（含未定義函式 stub）。
4. **寫 dataflow 查詢**：source = `cpg.call.name("net_read").argument(2)`（讀進 `sz` 的那個 arg）、sink = `cpg.call.name("memcpy").argument(3)`（size 參數），跑 `snk.reachableByFlows(src)`。
5. **驗收 + 印 path**：確認 flow 數 ≥ 1，用 `.p.foreach(println)` 把 path 印出來，肉眼核對它從 `net_read` 走到 `memcpy`。

## 如果你卡住了

- **gcc 只 warning 沒 error、exit code 是 0**：你的殘缺不夠「硬」。`uint32_t` 沒 include `<stdint.h>` 會是 **error（unknown type name）**讓 gcc 退出（未定義函式只是 warning）。加上那個缺分號的 syntax error 更保險。目標是 `echo $?` 非 0。
- **Joern importCode 後 `cpg.method` 是空的**：確認 `inputPath` 路徑對（相對於你啟動 Joern 的目錄，或用絕對路徑）、確認檔案真的存在。Joern 對殘缺 code 也該 parse 出東西，空多半是路徑錯。
- **flow 數是 0**：最可能是 source/sink 選錯節點——要選 `.argument`（值所在），不是 call 節點（Ch 30 的核心坑）。其次確認 `sz` 真的從 `net_read` 流到 `memcpy`（中間別不小心把變數名寫斷了）。
- **flow 斷在未定義函式**：如果你的 flow 中間**必須**穿過某個未定義函式（如 `sz` 經過 `alloc_buf(sz)` 的回傳才到 memcpy），預設 semantic 不認識 `alloc_buf`，flow 可能斷——把 flow 設計成「`sz` 本身直接到 memcpy」（不經過未定義函式的回傳）就不會斷。想挑戰穿過未定義函式的，用 Ch 31 的自訂 semantic（延伸挑戰）。
- **`reachableByFlows` 方向寫反**：是 `snk.reachableByFlows(src)`（接收者是 sink），不是反過來（Ch 30）。

## 參考解答

真跑過（Joern 4.0.594、gcc、WSL Ubuntu 22.04）。

<details>
<summary>點開看殘缺 C + gcc 失敗 + Joern 查詢 + 真實輸出</summary>

**殘缺 C：`~/audit-lab/broken/frag.c`**

```c
/* 殘缺片段：缺 include、呼叫未定義函式、無 main、故意缺分號 */
void process_packet(int sock) {
    char stack_buf[128];
    uint32_t sz;                       /* uint32_t 未定義（缺 stdint.h）→ gcc error */
    net_read(sock, &sz, 4);            /* net_read 未定義；sz 是攻擊者控制的長度 */
    void *heap = alloc_buf(sz);        /* alloc_buf 未定義 */
    net_read(sock, heap, sz);
    memcpy(stack_buf, heap, sz);       /* sink：sz 未檢查 → stack OOB write */
    log_event("copied", sz)            /* 故意缺分號 → syntax error */
}
```

這個片段：`uint32_t` 沒 include（error）、`net_read`/`alloc_buf`/`log_event` 全未定義、最後一行缺分號（syntax error）、沒 `main`。但 taint flow 完整——`sz` 從 `net_read(sock, &sz, 4)` 進來，直接當 `memcpy(stack_buf, heap, sz)` 的 size（`sz` 這個變數本身直接流到 memcpy，不強制穿過未定義函式的回傳，避免斷點）。

**步驟 2：gcc 證明編不過**

```
$ gcc -c ~/audit-lab/broken/frag.c -o /tmp/frag.o
/home/ypp/audit-lab/broken/frag.c: In function ‘process_packet’:
/home/ypp/audit-lab/broken/frag.c:4:5: error: unknown type name ‘uint32_t’
    4 |     uint32_t sz;
      |     ^~~~~~~~
/home/ypp/audit-lab/broken/frag.c:4:5: note: ‘uint32_t’ is defined in header ‘<stdint.h>’; did you forget to ‘#include <stdint.h>’?
/home/ypp/audit-lab/broken/frag.c:5:5: warning: implicit declaration of function ‘net_read’ [-Wimplicit-function-declaration]
    5 |     net_read(sock, &sz, 4);
      |     ^~~~~~~~
...（更多 warning/error）...

$ echo $?
1
```

**exit code 1（非 0）= 編不過**。`error: unknown type name 'uint32_t'` 是硬 error（不是 warning），加上缺分號的 syntax error，gcc 直接失敗。**CodeQL 的 `database create` 需要 gcc 成功編過每個 translation unit 才建得出 database——這裡 gcc 退出非 0，CodeQL 建 database 同樣卡死，後面的查詢/taint 全部無從談起**。這就是驗收標準第 1 條。

**步驟 3-5：Joern script `pe.sc`**

```scala
importCode(inputPath="broken/frag.c", projectName="pe")
println("=== methods parsed ===")
cpg.method.name.l.foreach(println)
println("=== flow: net_read sz -> memcpy size ===")
def src = cpg.call.name("net_read").argument(2)   // &sz
def snk = cpg.call.name("memcpy").argument(3)      // size 參數
val flows = snk.reachableByFlows(src)
println("num flows: " + flows.size)
flows.p.foreach(println)
```

跑 `cd ~/audit-lab && joern --script pe.sc`。真跑輸出（節錄）：

```
Code successfully imported. You can now query it using `cpg`.

=== methods parsed ===
process_packet
<global>
net_read
memcpy
log_event
<operator>.assignment
alloc_buf

=== flow: net_read sz -> memcpy size ===
num flows: 2
```

**驗收第 2 條達成**：`Code successfully imported`，`process_packet` 列得出來，未定義函式 `net_read`/`alloc_buf`/`log_event` 都有 stub method——**Joern 對一個 gcc 編不過的檔照樣 parse 出 CPG**。

flow path（`.p` 印出的其中一條，照貼）：

```
┌──────────┬───────────────────────────┬────┬──────────────┬──────┐
│nodeType  │tracked                    │line│method        │file  │
├──────────┼───────────────────────────┼────┼──────────────┼──────┤
│Call      │net_read(sock, &sz, 4)     │5   │process_packet│frag.c│
│Identifier│alloc_buf(sz)              │6   │process_packet│frag.c│
│Call      │alloc_buf(sz)              │6   │process_packet│frag.c│
│Identifier│heap = alloc_buf(sz)       │6   │process_packet│frag.c│
│Identifier│net_read(sock, heap, sz)   │7   │process_packet│frag.c│
│Identifier│net_read(sock, heap, sz)   │7   │process_packet│frag.c│
│Identifier│net_read(sock, heap, sz)   │7   │process_packet│frag.c│
│Identifier│memcpy(stack_buf, heap, sz)│8   │process_packet│frag.c│
└──────────┴───────────────────────────┴────┴──────────────┴──────┘
```

**驗收第 3 條達成**：`num flows: 2`（≥ 1），path 從第 5 行 `net_read(sock, &sz, 4)`（`sz` 的來源）一路走到第 8 行 `memcpy(stack_buf, heap, sz)`（sink）。攻擊者控制的 `sz` 未經檢查流到 `memcpy` 的 size，`stack_buf` 只有 128 byte → stack OOB write。**這條 flow path 就是漏洞的完整資料流證據，Joern 在一個 gcc 編不過的殘缺檔上把它抓了出來。**

**三條驗收全達成的意義**：同一個檔，gcc/CodeQL 因為型別未定義、語法殘缺、缺 build 環境而**完全起不了步**（第 1 條），Joern 卻 parse 出 CPG（第 2 條）並追出完整 taint flow（第 3 條）。這就是 Ch 32 那句「build 不了的 target 上 Joern 不可取代」的手驗版——不是理論，是你眼前的輸出。

</details>

## 測試用例

自檢你的解答是否合格，逐項核對：

| 測試 | 做法 | 通過標準 |
|---|---|---|
| build 真的失敗 | `gcc -c frag.c; echo $?` | 非 0 exit code + 至少一個 `error:` |
| Joern 真的 parse | `cpg.method.name.l` | 列得出你的函式 + 未定義函式 stub |
| flow 真的抓到 | `snk.reachableByFlows(src).size` | ≥ 1 |
| path 走對 | `.p` 印出的 path | 從 source 行（net_read）到 sink 行（memcpy） |
| 對照組（負向） | 把 `sz` 換成常數 `memcpy(stack_buf, heap, 16)` | flow 數變 0（證明你抓的是真 taint 不是假陽性） |

最後那條負向對照很重要：把 size 改成常數後 flow 應該消失——如果還有 flow，代表你的 source/sink 選太寬抓到雜訊，收窄它（Ch 30）。

## 延伸挑戰

做完基礎版，任選一個往真實場景/進階走：

- **穿過未定義函式的 taint（接 Ch 31 自訂 semantic）**：把 flow 設計成**必須**穿過未定義函式的回傳——例如 `sz` 經過 `uint32_t real = decode_len(sz);` 再用 `real` 當 memcpy size。預設 semantic 不認識 `decode_len`，flow 會斷（先跑一次確認斷）。然後用 Ch 31 的 `FlowSemantic.from("decode_len", List((1, -1)))` 建模，證明 flow 接回來。這是把「不 build」和「自訂 semantic」兩個 Joern 殺手鐧疊起來。
- **真實韌體/SDK 片段**：找一段真實的韌體反編 C、或閉源 SDK 只給的幾個 `.c`（缺 header 那種），對它跑同樣的 `reachableByFlows`。體會「真實的殘缺」比你手造的更亂（macro 地獄、奇怪 type），Joern 的 fuzzy 怎麼應對、哪裡會漏。
- **對回 CodeQL 版（練習 D）比較**：把練習 D 那個能 build 的 target 和這裡編不起來的 target 並排——同一類漏洞（未檢查長度 → OOB copy），一個 CodeQL 精查、一個只能 Joern。寫一段對照：CodeQL 在能 build 時精度贏在哪、Joern 在編不起來時覆蓋贏在哪。這就是 Ch 32 決策表的手驗。

## 本練習你該帶走的

- **build 不了的 target 上，Joern 不可取代**——你親手看到 gcc exit 非 0（CodeQL 建 database 同樣卡死），Joern 卻 parse 出 CPG 並抓到完整 taint flow。這是整個 Joern Part 的核心論斷的手驗。
- **fuzzy parser 的具體長相**：未定義型別（`uint32_t`）、未定義函式（`net_read`/`alloc_buf`）、語法殘缺（缺分號）全都吃，為未定義函式建 stub，照樣有 DDG 可追。
- **source/sink 選 `.argument`、`reachableByFlows` 接收者是 sink**（Ch 30 的坑，這裡再驗一次）；負向對照（size 換常數 → flow 消失）證明抓的是真 taint。
- 這正是**韌體、閉源 SDK、漏洞回報片段、逆向 workflow** 的標準操作——你以後面對「只給幾個編不起來的檔」時，掏 Joern。

理論（Ch 3 CPG）→ CodeQL Part（Ch 18-28，能 build 的精查）→ Joern Part（Ch 29-32，不 build 的覆蓋）→ 這個練習把 Joern 的殺手級場景釘死。接下來離開 CPG 平台，進入更輕量的一族——**結構化搜尋**：weggli 不建完整 CPG、不追 dataflow，卻在「快速結構匹配」上比 CPG 平台更輕更快，是 C/C++ 審計的另一把利器。

→ [Ch 33 weggli](./33-weggli.md)
