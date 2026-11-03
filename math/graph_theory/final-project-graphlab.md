# Final Project — graphlab 分析工具鏈

> **目標**：從零寫出一條可以對真實 ELF 執行檔跑完的圖分析工具鏈 graphlab：自刻圖論函式庫（鄰接字典）→ 從 WSL gcc 編出的 demo 程式抽出每個函式的 CFG 與整個程式的 call graph → dominator tree 與自然迴圈 → SCC 找遞迴 → WL 與指派問題做 `-O0` 對 `-O2` 的函式配對 → max-flow/min-cut 找通往危險函式的 chokepoint。每一步都要和 NetworkX 對拍，或在小圖上窮舉驗證。
>
> **環境**：WSL Ubuntu 22.04 的 gcc 11.4.0 產生 ELF；分析端是 Windows 11 上的 Python 3.13.12、NetworkX 3.7、SciPy 1.18.1、capstone 5.0.9、pyelftools 0.33、pytest 9.2.0。安裝：`pip install networkx==3.7 scipy==1.18.1 capstone==5.0.9 pyelftools==0.33 pytest==9.2.0`。執行前設 `PYTHONUTF8=1`。分析對象是你自己編譯的 demo 程式，不需要執行它。

## 這個專案整合了哪些章

graphlab 的每個階段都在用前面某幾章證明過的定理。把全課的核心概念粗分成 24 個主題（證明技巧、計數、圖的表示與同構、樹、連通度與 Menger、SCC 與 DAG、dominator、著色、chordal 與消去序、平面性、flow 與割、匹配與指派、matroid、LP 對偶、譜方法、隨機遊走、機率方法與 Ramsey、NP-complete 與歸約、treewidth、WL、程式分析圖、攻擊圖、binary 相似度、大圖工程），graphlab 直接用到其中 18 個（75%），分布在下表的 21 章；沒用到的 6 個是著色、平面性、matroid、LP 對偶、譜方法、機率方法與 Ramsey，見表後說明。

| Part | 章 | 在 graphlab 裡的角色 |
| --- | --- | --- |
| 1 證明與計數 | [Ch 2](./02-proof-toolbox.md) 證明工具箱、[Ch 3](./03-counting-basics.md) 雙重計數 | 每個演算法的正確性論證；測試用的不變量（森林邊數、路徑經過次數總和） |
| 2 圖的結構 | [Ch 7](./07-graph-definitions.md) 表示與同構、[Ch 9](./09-trees-cayley-prufer.md) 樹、[Ch 11](./11-connectivity-tarjan.md) Tarjan、[Ch 12](./12-menger-theorem.md) Menger、[Ch 13](./13-directed-graphs-scc-dag.md) SCC 與 DAG、[Ch 14](./14-dominator-tree.md) Dominator tree | 鄰接字典、DFS 邊分類、Tarjan SCC 與 condensation、dominator tree、「dominator = 大小 1 的點割」 |
| 3 著色與平面性 | [Ch 16](./16-chordal-graphs.md) Chordal 與消去序、[Ch 17](./17-register-allocation.md) Register allocation | min-degree 消去 = chordal 補全，給出 CFG 的 treewidth 上界；Thorup 的「結構化程式 treewidth ≤ 6」是 register allocation 的背景 |
| 4 Flow 與 matching | [Ch 20](./20-max-flow-min-cut.md) 最大流最小割、[Ch 22](./22-bipartite-matching.md) 二分匹配、[Ch 24](./24-min-cost-flow-assignment.md) 指派問題 | Edmonds–Karp、node splitting 點割；Hungarian 演算法做一對一函式配對 |
| 5 代數與機率 | [Ch 29](./29-random-walks-pagerank.md) 隨機遊走與 PageRank | call graph 上的 PageRank，找「流量匯集」的函式 |
| 6 計算複雜度 | [Ch 34](./34-np-complete-reductions.md) NP-complete、[Ch 36](./36-treewidth.md) Treewidth、[Ch 37](./37-graph-isomorphism-wl.md) WL | 為什麼不用精確 GED／圖同構；treewidth 上界；WL subtree 特徵 |
| 7 系統與資安 | [Ch 38](./38-program-analysis-graphs.md)、[Ch 39](./39-attack-graphs.md)、[Ch 40](./40-binary-similarity.md)、[Ch 41](./41-large-graph-engineering.md) | 程式圖、chokepoint 割分析、binary CFG 抽取與相似度、工程取捨（迭代式 DFS、避免遞迴深度） |

沒有直接用到的是著色（[Ch 15](./15-graph-coloring.md)）、平面性（Ch 18–19）、matroid（Ch 25）、LP 對偶（Ch 26）、譜方法（Ch 27–28）、機率方法與 Ramsey（Ch 31–33），以及 Part 1 的排容、生成函數與 Burnside——它們很重要，但不是 binary 分析工具鏈的自然組成。延伸挑戰給了接上著色、chordal 干涉圖與譜分群的方向。

## 背景與動機

逆向工程師拿到一個陌生的 binary，第一個小時做的事情大致固定：列出函式、看 call graph、找出「從入口能走到 `system`/`exec`/`memcpy` 的路」、看哪些函式有迴圈或遞迴（parser 和解碼器通常長這樣）、如果有舊版就做 patch diff。IDA、Ghidra、BinDiff 把這些都包好了，但它們底層做的就是這門課的演算法。graphlab 是把這些演算法自己串一次，好處是你會知道每個數字是怎麼來的、什麼時候會錯：

- 你會親眼看到 `-O2` 把 `run_cmd` 改成**尾呼叫**之後，天真的 call graph 少了「`run_cmd → system`」這條邊，chokepoint 分析因此給出錯誤答案。
- 你會看到只用 CFG 結構配對函式只有 4/16 正確，加上外部呼叫名稱變 11/16，再沿 call graph 傳播配對變 16/16——每一步加的是哪一種圖的資訊。
- 你會看到 dominator tree 回答「哪些函式是必經之路」，而 min-cut 回答「如果沒有單一必經之路，最少要看幾個」，兩者在 Menger 定理下是同一件事的兩個尺寸。

## 分析對象：demo 程式

一個小型「指令處理器」，刻意包含巢狀迴圈、直接遞迴、互相遞迴（遞迴下降運算式解析），以及兩條通往 `system` 的路：

```c
/* graphlab 的分析對象：一個小型「指令處理器」。
 * 刻意包含：迴圈（含巢狀）、直接遞迴、互相遞迴（遞迴下降運算式解析）、
 * 以及兩條通往危險 sink（system）的呼叫路徑。只用來編譯與分析，不需要執行。 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char g_log[256];

void log_msg(const char *m) {
    strncat(g_log, m, sizeof g_log - strlen(g_log) - 1);
}

/* ---------- 運算式：expr → term → factor → expr（互相遞迴） ---------- */
const char *p;
int parse_expr(void);

int parse_factor(void) {
    while (*p == ' ') p++;
    if (*p == '(') {
        p++;
        int v = parse_expr();
        if (*p == ')') p++;
        return v;
    }
    int v = 0;
    while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
    return v;
}

int parse_term(void) {
    int v = parse_factor();
    for (;;) {
        while (*p == ' ') p++;
        if (*p == '*') { p++; v *= parse_factor(); }
        else if (*p == '/') { p++; int d = parse_factor(); v = d ? v / d : 0; }
        else return v;
    }
}

int parse_expr(void) {
    int v = parse_term();
    for (;;) {
        while (*p == ' ') p++;
        if (*p == '+') { p++; v += parse_term(); }
        else if (*p == '-') { p++; v -= parse_term(); }
        else return v;
    }
}

/* ---------- 直接遞迴 ---------- */
long fact(int n) { return n <= 1 ? 1 : n * fact(n - 1); }

/* ---------- 巢狀迴圈 ---------- */
int checksum2d(const char *s, int rows) {
    int h = 0, n = (int)strlen(s);
    for (int r = 0; r < rows; r++)
        for (int i = 0; i < n; i++)
            h = h * 31 + s[i] + r;
    return h;
}

/* ---------- 危險 sink：兩條路都會走到 system ---------- */
int sanitize(char *s) {
    for (char *q = s; *q; q++)
        if (strchr(";|&`$", *q)) return 0;
    return 1;
}

int run_cmd(const char *cmd) {
    log_msg("exec ");
    return system(cmd);
}

int run_hook(const char *name) {
    char buf[160];
    snprintf(buf, sizeof buf, "/opt/hooks/%s", name);
    return run_cmd(buf);
}

int cmd_exec(char *arg) {
    if (!sanitize(arg)) { log_msg("rejected "); return -1; }
    return run_cmd(arg);
}

int cmd_calc(char *arg) {
    p = arg;
    int v = parse_expr();
    printf("%d\n", v);
    return v;
}

int cmd_fact(char *arg) {
    printf("%ld\n", fact(atoi(arg)));
    return 0;
}

int cmd_hash(char *arg) {
    printf("%d\n", checksum2d(arg, 3));
    return 0;
}

int dispatch(char *line) {
    char *sp = strchr(line, ' ');
    char *arg = sp ? sp + 1 : "";
    if (sp) *sp = 0;
    if (!strcmp(line, "calc")) return cmd_calc(arg);
    if (!strcmp(line, "fact")) return cmd_fact(arg);
    if (!strcmp(line, "hash")) return cmd_hash(arg);
    if (!strcmp(line, "exec")) return cmd_exec(arg);
    log_msg("unknown ");
    return -1;
}

int load_config(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    char line[128];
    int n = 0;
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\n")] = 0;
        if (!strncmp(line, "hook=", 5)) run_hook(line + 5);
        n++;
    }
    fclose(f);
    return n;
}

int main(int argc, char **argv) {
    char line[256];
    if (argc > 1) load_config(argv[1]);
    while (fgets(line, sizeof line, stdin)) {
        line[strcspn(line, "\n")] = 0;
        if (!*line) continue;
        dispatch(line);
    }
    puts(g_log);
    return 0;
}
```

編譯兩個版本（不加 `-s`，保留 `.symtab`）：

```bash
wsl -e bash -lc 'gcc -O0 -Wall -o demo_O0 demo.c && gcc -O2 -Wall -o demo_O2 demo.c'
```

`-Wall` 下沒有警告。第一版的 `run_hook` 用了 `char buf[128]`，gcc 警告 `snprintf` 可能截斷（`-Wformat-truncation`，因為 `load_config` 傳進來的字串最長 122 bytes，加上 `/opt/hooks/` 會超過 128）；改成 160 之後警告消失。

## 分階段規格

### Stage 0：圖論函式庫 `graphlab.py`

全部用 `succ: dict[node, set[node]]`，不得 import NetworkX（NetworkX 只能出現在測試與對拍）。必須提供：

| 函式 | 規格 | 對應章 |
| --- | --- | --- |
| `reverse`、`reachable`、`reverse_postorder` | 反向圖、可達集合、迭代式 DFS 逆後序 | Ch 7、13 |
| `tarjan_scc`、`condensation` | 迭代式 Tarjan（不得用遞迴，CFG 可能很深）；縮點 DAG | Ch 13 |
| `dominators(succ, entry)` | 回傳 `idom`，只含可達點，`idom[entry] = entry` | Ch 14 |
| `dom_tree`、`dominates` | 由 idom 建樹；判斷 a 是否支配 b | Ch 14 |
| `retreating_edges`、`natural_loops` | DFS retreating edge；h 支配 t 時是 back edge，定義自然迴圈（同 header 合併）；其餘回報為不可歸約 | Ch 13、14 |
| `wl_features`、`cosine` | 有向 WL subtree 特徵（前驅與後繼分開） | Ch 37、40 |
| `max_flow`、`min_vertex_cut` | Edmonds–Karp；node splitting 點割，可指定受保護節點 | Ch 12、20、39 |
| `pagerank` | 冪迭代，處理 dangling 節點 | Ch 29 |
| `treewidth_min_degree` | min-degree 消去，回傳最大消去度數（treewidth 上界） | Ch 16、36 |
| `hungarian(cost)` | `O(n³)` 位勢版 Kuhn–Munkres，n ≤ m | Ch 24 |

**驗收**：`test_graphlab.py` 的函式庫測試全部通過（下面列出）。

### Stage 1：ELF 前端 `elfx.py`

- 用 `.symtab` 找 `.text` 裡的 `STT_FUNC` 符號（排除 `_start` 等 C runtime 函式）。
- 解析 PLT：`.rela.plt` 給「GOT 槽位 → 符號名」，`.plt.sec` 的每個 16-byte stub 是 `bnd jmp *disp(%rip)`，算出它讀的槽位，得到「stub 位址 → `name@plt`」。
- 每個函式的 CFG：沿用 [Ch 40](./40-binary-similarity.md) 的 leader 演算法，另外記錄每個 block 呼叫了誰；呼叫 `noreturn` 函式（`__stack_chk_fail` 等）的 block 不連 fall-through。
- **尾呼叫**：`jmp` 的目標是另一個函式的入口（或 PLT stub）時，視為一次呼叫。
- call graph：節點是本地函式與被呼叫的 PLT 函式。

**驗收**：`demo_O0` 找到 16 個本地函式、15 個 PLT stub，PLT 名稱和 `readelf -r` 的 `.rela.plt` 一致。

### Stage 2：CFG 分析

對每個函式算 dominator tree、自然迴圈、不可歸約邊、dominator tree 深度、treewidth 上界。

**驗收**：所有函式的 idom 與 `nx.immediate_dominators` 一致（注意：NetworkX 3.7 的結果**不含** entry 本身，舊版含 `entry: entry`，對拍前要補上）；`checksum2d` 有 2 個自然迴圈（原始碼的兩層 `for`）；沒有不可歸約邊（demo 沒有 `goto`）。

### Stage 3：遞迴偵測

call graph 上跑 Tarjan；大小 > 1 的 SCC 或有自環的單點 SCC 就是遞迴。

**驗收**：找到 `[fact]` 與 `[parse_expr, parse_factor, parse_term]`，和 `nx.strongly_connected_components` 一致；condensation 是 DAG。

### Stage 4：`-O0` 對 `-O2` 的函式配對

三個遞進的版本：

1. 只用 CFG 結構的 WL 特徵（h = 2，block 標籤為結尾類型），cosine 相似度；
2. 再加上「外部呼叫名稱 multiset」的 Jaccard（`__printf_chk` 要正規化成 `printf`，見踩雷）；
3. 再沿 call graph 傳播 3 輪：若 a 的鄰居（呼叫者與被呼叫者）已經被配到 c 的鄰居，(a, c) 加分。

每一版都用 Hungarian 做一對一指派（不是各自取 top-1），並和 `scipy.optimize.linear_sum_assignment` 比對總成本。

**驗收**：三版的正確配對數嚴格遞增；最後一版 16/16；Hungarian 總成本與 SciPy 一致。

### Stage 5：chokepoint

在 call graph 上：`system@plt` 的支配者鏈、`main → system@plt` 與 `main → run_cmd` 的最小點割（和 `nx.minimum_node_cut` 的大小比對，並驗證刪掉後不可達）、PageRank 前 5 名（和 `nx.pagerank` 的最大差 < 1e-8）。`-O0`、`-O2` 兩個版本都要跑。

**驗收**：`-O0` 上 `main → system@plt` 的最小點割是 `[run_cmd]`（它也是 `system@plt` 唯一的非 main 支配者），`main → run_cmd` 的最小點割大小是 2。

## 期望輸出

```text
$ python pipeline.py demo_O0 demo_O2
== Stage 1：函式與 PLT ==
本地函式 16 個： checksum2d cmd_calc cmd_exec cmd_fact cmd_hash dispatch fact load_config log_msg main parse_expr parse_factor parse_term run_cmd run_hook sanitize
PLT stub 15 個： __stack_chk_fail@plt atoi@plt fclose@plt fgets@plt fopen@plt printf@plt puts@plt snprintf@plt strchr@plt strcmp@plt strcspn@plt strlen@plt strncat@plt strncmp@plt system@plt
== Stage 2：每個函式的 CFG、dominator tree、自然迴圈 ==
  checksum2d    blocks=  7 loops=2 loop_sizes=[2, 5] irreducible=0 domtree_depth=4 tw<=2
  cmd_calc      blocks=  1 loops=0 loop_sizes=[] irreducible=0 domtree_depth=0 tw<=0
  cmd_exec      blocks=  4 loops=0 loop_sizes=[] irreducible=0 domtree_depth=1 tw<=2
  cmd_fact      blocks=  1 loops=0 loop_sizes=[] irreducible=0 domtree_depth=0 tw<=0
  cmd_hash      blocks=  1 loops=0 loop_sizes=[] irreducible=0 domtree_depth=0 tw<=0
  dispatch      blocks= 15 loops=0 loop_sizes=[] irreducible=0 domtree_depth=6 tw<=2
  fact          blocks=  4 loops=0 loop_sizes=[] irreducible=0 domtree_depth=1 tw<=2
  load_config   blocks= 11 loops=1 loop_sizes=[4] irreducible=0 domtree_depth=4 tw<=2
  log_msg       blocks=  1 loops=0 loop_sizes=[] irreducible=0 domtree_depth=0 tw<=0
  main          blocks=  9 loops=1 loop_sizes=[4] irreducible=0 domtree_depth=3 tw<=2
  parse_expr    blocks=  8 loops=1 loop_sizes=[6] irreducible=0 domtree_depth=4 tw<=2
  parse_factor  blocks= 13 loops=2 loop_sizes=[2, 3] irreducible=0 domtree_depth=6 tw<=2
  parse_term    blocks= 11 loops=1 loop_sizes=[9] irreducible=0 domtree_depth=5 tw<=2
  run_cmd       blocks=  1 loops=0 loop_sizes=[] irreducible=0 domtree_depth=0 tw<=0
  run_hook      blocks=  3 loops=0 loop_sizes=[] irreducible=0 domtree_depth=1 tw<=1
  sanitize      blocks=  7 loops=1 loop_sizes=[3] irreducible=0 domtree_depth=3 tw<=2
  dominator 與 nx.immediate_dominators 全部一致: True
== Stage 3：call graph 與遞迴（SCC） ==
  call graph: 31 節點, 43 邊
  遞迴（SCC 大小 > 1 或自環）: [['fact'], ['parse_expr', 'parse_factor', 'parse_term']]
  SCC 與 NetworkX 一致: True
  condensation DAG: 29 個分量，是 DAG: True
== Stage 4：-O0 vs -O2 函式配對 ==
  結構 WL            正確配對 4/16
  WL + 外部呼叫        正確配對 11/16
  + call graph 傳播  正確配對 16/16
  Hungarian 與 scipy.linear_sum_assignment 總成本一致: True
  仍配錯的: []
== Stage 5（-O0）：call graph 上的 chokepoint ==
  main 能到 system: True
  system@plt 的支配者鏈（main 以外）: ['run_cmd']
  main → system@plt: 最小點割 = 1 ['run_cmd']；刪掉後仍可達: False；nx.minimum_node_cut 大小 = 1
  main → run_cmd: 最小點割 = 2 ['dispatch', 'load_config']；刪掉後仍可達: False；nx.minimum_node_cut 大小 = 2
  PageRank 前 5: [('fact', 0.123), ('parse_expr', 0.111), ('parse_term', 0.108), ('parse_factor', 0.106), ('strlen@plt', 0.044)] 與 NetworkX 最大差: 5.7e-10
== Stage 5（-O2）：call graph 上的 chokepoint ==
  main 能到 system: True
  system@plt 的支配者鏈（main 以外）: []
  main → system@plt: 最小點割 = 2 ['dispatch', 'load_config']；刪掉後仍可達: False；nx.minimum_node_cut 大小 = 2
  main → run_cmd: 不可達（被 inline 或改成尾呼叫後消失）
  PageRank 前 5: [('parse_expr', 0.133), ('parse_term', 0.129), ('parse_factor', 0.126), ('strlen@plt', 0.06), ('__printf_chk@plt', 0.04)] 與 NetworkX 最大差: 7.5e-10
```

逐段讀這份輸出：

- **Stage 2**：`parse_term` 原始碼有一個 `for (;;)` 和裡面的 `while (*p == ' ')`，卻只回報 1 個迴圈、大小 9。因為 `for (;;)` 沒有條件，它的第一個 block 就是內層 `while` 的條件判斷——兩個迴圈的 header 是**同一個 block**，自然迴圈的定義把同 header 的迴圈合併。`checksum2d` 的兩層 `for` header 不同，所以是 2 個。所有 CFG 的 treewidth 上界 ≤ 2，遠低於 Thorup 對無 goto 程式證明的上界 6。
- **Stage 3**：31 個節點裡有 15 個是 PLT 函式；29 個 SCC 是因為 3 個互相遞迴的函式縮成 1 個（`31 − 3 + 1 = 29`）。
- **Stage 4**：純結構只有 4/16——`-O0` 和 `-O2` 的 CFG 差太多，和 Ch 40 的結論一致。加上外部呼叫名稱跳到 11/16：呼叫 `fopen` 與 `fclose` 的只有 `load_config`，這種「語意錨點」比結構穩定得多。最後的傳播把剩下 5 個結構相似、外部呼叫也相似的小函式（例如 `cmd_calc`、`cmd_fact`、`cmd_hash` 的外部呼叫幾乎只有 `printf`）靠「誰呼叫它、它呼叫誰」分開。
- **Stage 5**：`-O0` 的 dominator 說 `run_cmd` 是 `system` 的必經之路——這正是 min-cut 大小 1 的那個點。`run_cmd` 自己沒有單一必經之路（idom 是 main），min-cut 告訴你最少要看 2 個：`dispatch` 和 `load_config`。`-O2` 上 `run_cmd` 變得**不可達**：它被 inline 進 `cmd_exec` 與 `run_hook`（`run_hook` 又被 inline 進 `load_config`），而 `run_cmd`、`cmd_exec` 結尾的 `system` 呼叫被編成尾呼叫 `jmp system@plt`；`-O2` 的 chokepoint 因此直接是 `dispatch` 與 `load_config`。如果你的 call graph 沒處理尾呼叫，`-O2` 上 `cmd_exec → system` 這條邊會消失，割的答案就錯了。PageRank 前幾名被遞迴函式佔據，因為隨機遊走會在 SCC 裡繞圈——它衡量的是「流量匯集」，不是「危險程度」。

## 如果你卡住了

1. **PLT 名稱全是空的**：capstone 對 `.plt.sec` 的 `bnd jmp` 回報的 mnemonic 是 `"bnd jmp"`，不是 `"jmp"`。用 `endswith("jmp")` 判斷。
2. **dominator 和 NetworkX 對不上，差一個 key**：NetworkX 3.7 的 `immediate_dominators` 不含 entry；先 `ref.setdefault(entry, entry)` 再比。如果還對不上，檢查你的 `intersect` 是否用**逆後序編號**比較「誰比較靠近 entry」。
3. **`-O2` 的 call graph 上找不到 `run_cmd → system`**：看 `objdump -d demo_O2` 的 `run_cmd`，結尾是 `jmp ... <system@plt>` 而不是 `call`。尾呼叫要算成呼叫邊。
4. **配對時好幾個函式分數完全相同**：1 個 block 的小函式在結構上無法區分。需要結構以外的資訊——外部呼叫名稱、call graph 鄰居。
5. **Hungarian 寫不出來**：先寫 `O(n!)` 窮舉當對照組（n ≤ 7），再照 [Ch 24](./24-min-cost-flow-assignment.md) 的位勢（potential）版本寫；關鍵是每次為新的一列找增廣路徑時維護 `minv` 與 `way`，用 `delta` 更新位勢讓 reduced cost 保持非負。

## 實作步驟建議

### Step 1：函式庫骨架與測試

先寫 `reverse`、`reachable`、`reverse_postorder`、`tarjan_scc`，對 40 張隨機有向圖和 NetworkX 對拍。全部用迭代式 DFS（Ch 41 的教訓：真實 CFG 可以很深，Python 預設遞迴深度是 1000）。

### Step 2：dominator、迴圈、割

寫 Cooper–Harvey–Kennedy 迭代 dominator，對拍 NetworkX；寫 `natural_loops` 並用「while 裡有 if」和「兩個入口的環」兩張手工圖測試；寫 `min_vertex_cut` 並驗證「d 嚴格支配 t ⟺ 刪掉 d 後 t 不可達」這個 Menger 特例。

### Step 3：ELF 前端

先只做函式列表與 PLT 名稱，用 `readelf -r`、`objdump -d -j .plt.sec` 對照；再做 CFG，和 Ch 40 的數字比較；最後做 call graph 與尾呼叫。

### Step 4：配對

寫 WL 特徵與 Hungarian，先跑純結構版本看到 4/16；逐步加外部呼叫與傳播，每加一層記錄正確數。

### Step 5：整合與報告

寫 `pipeline.py` 串起 Stage 1–5，兩個最佳化等級都跑 Stage 5，寫一段「如果這是一份真實的審計報告，你會怎麼描述 chokepoint」。

## 完整參考實作

**寫完再看！不要偷看**，否則學不到東西。四個檔案放在同一個目錄，和 `demo_O0`、`demo_O2` 一起。

<!-- markdownlint-disable MD033 -->

<details>
<summary>點開 graphlab.py（圖論函式庫）</summary>

```python
"""graphlab：本課的迷你圖論函式庫。有向圖一律是 succ: dict[node, set[node]]。"""
from collections import Counter, deque
import math

INF = float("inf")


# ---------- 基本工具（Ch 7、Ch 13） ----------
def reverse(succ):
    pred = {v: set() for v in succ}
    for u, ws in succ.items():
        for w in ws:
            pred[w].add(u)
    return pred


def reachable(succ, s):
    seen, st = {s}, [s]
    while st:
        for w in succ[st.pop()] - seen:
            seen.add(w)
            st.append(w)
    return seen


def reverse_postorder(succ, s):
    """迭代式 DFS 的逆後序（Ch 13）；dominator 與資料流分析都靠這個順序。"""
    seen, post = {s}, []
    stack = [(s, iter(sorted(succ[s], key=str)))]
    while stack:
        v, it = stack[-1]
        w = next(it, None)
        if w is None:
            stack.pop()
            post.append(v)
        elif w not in seen:
            seen.add(w)
            stack.append((w, iter(sorted(succ[w], key=str))))
    return post[::-1]


# ---------- SCC（Ch 13，Tarjan，迭代版避免遞迴深度限制） ----------
def tarjan_scc(succ):
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]
    for root in sorted(succ, key=str):
        if root in index:
            continue
        work = [(root, iter(sorted(succ[root], key=str)))]
        index[root] = low[root] = counter[0]; counter[0] += 1
        stack.append(root); on.add(root)
        while work:
            v, it = work[-1]
            w = next(it, None)
            if w is not None:
                if w not in index:
                    index[w] = low[w] = counter[0]; counter[0] += 1
                    stack.append(w); on.add(w)
                    work.append((w, iter(sorted(succ[w], key=str))))
                elif w in on:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = set()
                while True:
                    x = stack.pop(); on.discard(x); comp.add(x)
                    if x == v:
                        break
                out.append(comp)
    return out


def condensation(succ, sccs):
    comp_of = {v: i for i, c in enumerate(sccs) for v in c}
    dag = {i: set() for i in range(len(sccs))}
    for u, ws in succ.items():
        for w in ws:
            if comp_of[u] != comp_of[w]:
                dag[comp_of[u]].add(comp_of[w])
    return dag, comp_of


# ---------- Dominator（Ch 14：Cooper–Harvey–Kennedy 迭代法） ----------
def dominators(succ, entry):
    """回傳 idom: dict[node, node]，只含從 entry 可達的節點；idom[entry] = entry。"""
    order = reverse_postorder(succ, entry)
    po = {v: i for i, v in enumerate(order)}            # 逆後序編號：越小越靠近 entry
    pred = reverse(succ)
    idom = {entry: entry}

    def intersect(a, b):
        while a != b:
            while po[a] > po[b]:
                a = idom[a]
            while po[b] > po[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for v in order[1:]:
            ps = [p for p in pred[v] if p in idom]
            new = ps[0]
            for p in ps[1:]:
                new = intersect(p, new)
            if idom.get(v) != new:
                idom[v] = new
                changed = True
    return idom


def dom_tree(idom):
    tree = {v: set() for v in idom}
    for v, d in idom.items():
        if v != d:
            tree[d].add(v)
    return tree


def dominates(idom, a, b):
    """a 是否支配 b：沿 idom 鏈從 b 往上走。"""
    while True:
        if b == a:
            return True
        if idom[b] == b:
            return False
        b = idom[b]


def retreating_edges(succ, entry):
    """DFS 中指向「仍在堆疊上」祖先的邊（Ch 13 的 back edge 分類）。"""
    on, done, out = {entry}, set(), []
    stack = [(entry, iter(sorted(succ[entry], key=str)))]
    while stack:
        v, it = stack[-1]
        w = next(it, None)
        if w is None:
            stack.pop(); on.discard(v); done.add(v)
        elif w in on:
            out.append((v, w))
        elif w not in done:
            on.add(w); stack.append((w, iter(sorted(succ[w], key=str))))
    return out


def natural_loops(succ, entry, idom=None):
    """retreating edge t→h 若 h 支配 t 就是 back edge，定義自然迴圈：h 加上「不經過 h 能倒著走到 t」的點。
    回傳 (loops: {header: body}, irreducible: 不是 back edge 的 retreating edge)。同 header 的迴圈合併。"""
    idom = idom or dominators(succ, entry)
    pred = reverse(succ)
    loops, irreducible = {}, []
    for t, h in retreating_edges(succ, entry):
        if not dominates(idom, h, t):
            irreducible.append((t, h))
            continue
        body, st = {h, t}, [t]
        while st:
            x = st.pop()
            for y in pred[x]:
                if y not in body and y in idom:
                    body.add(y)
                    st.append(y)
        loops.setdefault(h, set()).update(body)
    return loops, irreducible


# ---------- WL subtree 特徵（Ch 37、Ch 40） ----------
def wl_features(succ, labels, h=2):
    pred = reverse(succ)
    lab = dict(labels)
    feats = Counter((0, l) for l in lab.values())
    for it in range(1, h + 1):
        lab = {v: repr((lab[v], sorted(map(repr, (lab[w] for w in succ[v]))),
                        sorted(map(repr, (lab[w] for w in pred[v]))))) for v in succ}
        feats.update((it, l) for l in lab.values())
    return feats


def cosine(a, b):
    na = math.sqrt(sum(x * x for x in a.values()))
    nb = math.sqrt(sum(x * x for x in b.values()))
    return sum(a[k] * b.get(k, 0) for k in a) / (na * nb) if na and nb else 0.0


# ---------- Max-flow / min-cut（Ch 20、Ch 12） ----------
def max_flow(cap, s, t):
    """Edmonds–Karp。cap: dict[u][v]。回傳 (流量值, 殘餘圖中 s 可達的點集合)。"""
    res = {u: dict(vs) for u, vs in cap.items()}
    for u in cap:
        for v in cap[u]:
            res.setdefault(v, {}).setdefault(u, 0)
    res.setdefault(s, {}); res.setdefault(t, {})
    flow = 0
    while True:
        par = {s: None}
        q = deque([s])
        while q and t not in par:
            u = q.popleft()
            for v, c in res[u].items():
                if c > 0 and v not in par:
                    par[v] = u
                    q.append(v)
        if t not in par:
            return flow, set(par)
        b, v = INF, t
        while par[v] is not None:
            b = min(b, res[par[v]][v]); v = par[v]
        if b == INF:
            return INF, set(par)
        v = t
        while par[v] is not None:
            res[par[v]][v] -= b; res[v][par[v]] += b; v = par[v]
        flow += b


def min_vertex_cut(succ, s, t, protected=()):
    """node splitting：v → (v,'in') → (v,'out')，容量 1（protected 與 s、t 為 ∞），原邊容量 ∞。"""
    prot = set(protected) | {s, t}
    cap = {}
    for v in succ:
        cap[(v, "in")] = {(v, "out"): INF if v in prot else 1}
        cap.setdefault((v, "out"), {})
        for w in succ[v]:
            cap[(v, "out")][(w, "in")] = INF
    val, S = max_flow(cap, (s, "out"), (t, "in"))
    cut = sorted((v for v in succ if (v, "in") in S and (v, "out") not in S), key=str)
    return val, cut


# ---------- PageRank（Ch 29）：找「被很多路徑流經」的函式 ----------
def pagerank(succ, d=0.85, iters=100):
    n = len(succ)
    r = {v: 1 / n for v in succ}
    for _ in range(iters):
        dangling = sum(r[v] for v in succ if not succ[v])
        new = {v: (1 - d) / n + d * dangling / n for v in succ}
        for u, ws in succ.items():
            for w in ws:
                new[w] += d * r[u] / len(ws)
        r = new
    return r


# ---------- Treewidth 上界（Ch 36、Ch 16：min-degree 消去 = chordal 補全） ----------
def treewidth_min_degree(adj):
    """無向圖 adj: dict[node, set]。反覆消去度數最小的點並把它的鄰居補成團；回傳最大的消去度數。"""
    g = {v: set(ws) for v, ws in adj.items()}
    width = 0
    while g:
        v = min(g, key=lambda x: (len(g[x]), str(x)))
        nb = g.pop(v)
        width = max(width, len(nb))
        for a in nb:
            g[a].discard(v)
            g[a] |= nb - {a}
    return width


# ---------- 指派問題（Ch 24：Hungarian / Kuhn–Munkres，O(n^3) 位勢版） ----------
def hungarian(cost):
    """cost: n×m（n ≤ m）的 list of lists，求最小成本的列→欄一對一指派。回傳 assign[i] = j。"""
    n, m = len(cost), len(cost[0])
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)          # p[j] = 指派到欄 j 的列（1-indexed，0 = 無）
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [INF] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta; v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:                                    # 沿增廣路徑翻轉
            j1 = way[j0]; p[j0] = p[j1]; j0 = j1
    assign = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            assign[p[j] - 1] = j - 1
    return assign
```

</details>

<details>
<summary>點開 elfx.py（ELF 前端）</summary>

```python
"""graphlab 的 ELF 前端：函式邊界（.symtab）、PLT 名稱、每個函式的 CFG、整個程式的 call graph。"""
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_GRP_JUMP, CS_GRP_RET, CS_GRP_CALL
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_RIP

SKIP = {"_start", "_init", "_fini", "deregister_tm_clones", "register_tm_clones",
        "__do_global_dtors_aux", "frame_dummy"}
NORETURN = {"exit@plt", "abort@plt", "__stack_chk_fail@plt", "__assert_fail@plt"}


def _md():
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    return md


class Binary:
    def __init__(self, path):
        with open(path, "rb") as f:
            elf = ELFFile(f)
            text = elf.get_section_by_name(".text")
            self.text_base, self.text = text["sh_addr"], text.data()
            self.funcs = {}                                   # name -> (addr, size)
            for sym in elf.get_section_by_name(".symtab").iter_symbols():
                if sym["st_info"]["type"] == "STT_FUNC" and sym["st_size"] > 0 and sym.name not in SKIP:
                    a = sym["st_value"]
                    if self.text_base <= a < self.text_base + len(self.text):
                        self.funcs[sym.name] = (a, sym["st_size"])
            self.plt = self._plt_names(elf)                   # stub 位址 -> "name@plt"
        self.addr2name = {a: n for n, (a, _) in self.funcs.items()}
        self.addr2name.update(self.plt)

    def _plt_names(self, elf):
        """.rela.plt 給「GOT 槽位 → 符號名」；.plt.sec（或 .plt）的每個 stub 是 jmp *[rip+disp]，算出它讀的 GOT 槽位。"""
        rela = elf.get_section_by_name(".rela.plt")
        symtab = elf.get_section(rela["sh_link"])
        got2name = {r["r_offset"]: symtab.get_symbol(r["r_info_sym"]).name for r in rela.iter_relocations()}
        out = {}
        for secname in (".plt.sec", ".plt"):
            sec = elf.get_section_by_name(secname)
            if sec is None:
                continue
            base = sec["sh_addr"]
            for ins in _md().disasm(sec.data(), base):
                if ins.mnemonic.endswith("jmp") and ins.operands and ins.operands[0].type == X86_OP_MEM \
                        and ins.operands[0].mem.base == X86_REG_RIP:
                    slot = ins.address + ins.size + ins.operands[0].mem.disp
                    if slot in got2name:
                        stub = ins.address - (ins.address - base) % 16   # 每個 stub 16 bytes
                        out.setdefault(stub, got2name[slot] + "@plt")
            if out:
                break
        return out

    def code(self, name):
        a, n = self.funcs[name]
        return a, self.text[a - self.text_base: a - self.text_base + n]

    def cfg(self, name):
        """leader 演算法建 CFG（Ch 40）；另外記錄每個 block 的 call 目標名稱，呼叫 noreturn 函式的 block 不連 fall-through。"""
        addr, code = self.code(name)
        insns = [i for i in _md().disasm(code, addr) if not i.mnemonic.startswith("nop")]
        real = [i.address for i in insns]
        end = addr + len(code)

        def snap(t):
            return next((a for a in real if a >= t), None)

        def call_target(ins):
            op = ins.operands[0] if ins.operands else None
            if op is not None and op.type == X86_OP_IMM:
                return self.addr2name.get(op.imm, hex(op.imm))
            return "<indirect>"

        leaders = {addr}
        for k, ins in enumerate(insns):
            ends = ins.group(CS_GRP_JUMP) or ins.group(CS_GRP_RET) or \
                (ins.group(CS_GRP_CALL) and call_target(ins) in NORETURN)
            if ends and k + 1 < len(insns):
                leaders.add(insns[k + 1].address)
            if ins.group(CS_GRP_JUMP) and ins.operands and ins.operands[0].type == X86_OP_IMM:
                t = ins.operands[0].imm
                if addr <= t < end and snap(t) is not None:
                    leaders.add(snap(t))
        blocks, cur = {}, None
        for ins in insns:
            if ins.address in leaders:
                cur = ins.address
                blocks[cur] = []
            blocks[cur].append(ins)
        starts = sorted(blocks)
        succ = {b: set() for b in starts}
        calls = {b: [call_target(i) for i in blocks[b] if i.group(CS_GRP_CALL)] for b in starts}
        for b in starts:                       # 尾呼叫：jmp 到另一個函式的入口（-O2 常見），在 call graph 上也是一條呼叫邊
            last = blocks[b][-1]
            if last.mnemonic == "jmp" and last.operands and last.operands[0].type == X86_OP_IMM:
                t = last.operands[0].imm
                if not addr <= t < end and t in self.addr2name:
                    calls[b].append(self.addr2name[t])
        for k, b in enumerate(starts):
            last = blocks[b][-1]
            nxt = starts[k + 1] if k + 1 < len(starts) else None
            if last.group(CS_GRP_RET) or last.mnemonic == "hlt":
                continue
            if last.group(CS_GRP_CALL) and call_target(last) in NORETURN:
                continue
            if last.group(CS_GRP_JUMP):
                op = last.operands[0] if last.operands else None
                if op is not None and op.type == X86_OP_IMM and addr <= op.imm < end and snap(op.imm):
                    succ[b].add(snap(op.imm))
                if last.mnemonic != "jmp" and nxt is not None:
                    succ[b].add(nxt)
                continue
            if nxt is not None:
                succ[b].add(nxt)
        return succ, blocks, calls

    def call_graph(self):
        """節點 = 本地函式 + 被呼叫的 PLT 函式；u→v 表示 u 的程式碼裡有一條直接 call v。"""
        cg = {}
        for name in self.funcs:
            _, _, calls = self.cfg(name)
            cg[name] = {c for cs in calls.values() for c in cs}
        for ws in list(cg.values()):
            for w in ws:
                cg.setdefault(w, set())
        return cg
```

</details>

<details>
<summary>點開 pipeline.py（分析流水線）</summary>

```python
"""graphlab 分析流水線：python pipeline.py demo_O0 demo_O2"""
import sys
from collections import Counter
from capstone import CS_GRP_JUMP, CS_GRP_RET
import networkx as nx
import graphlab as gl
from elfx import Binary


def to_nx(succ):
    G = nx.DiGraph()
    G.add_nodes_from(succ)
    G.add_edges_from((u, w) for u in succ for w in succ[u])
    return G


def stage1(b):
    print("== Stage 1：函式與 PLT ==")
    print(f"本地函式 {len(b.funcs)} 個：", " ".join(sorted(b.funcs)))
    print(f"PLT stub {len(b.plt)} 個：", " ".join(sorted(b.plt.values())))


def stage2(b):
    print("== Stage 2：每個函式的 CFG、dominator tree、自然迴圈 ==")
    ok_dom = True
    for name in sorted(b.funcs):
        succ, blocks, _ = b.cfg(name)
        entry = b.funcs[name][0]
        idom = gl.dominators(succ, entry)
        ref = nx.immediate_dominators(to_nx(succ), entry)
        ref.setdefault(entry, entry)                    # NetworkX 3.7 的結果不含 entry 自己
        ok_dom &= ref == idom
        loops, irr = gl.natural_loops(succ, entry, idom)
        depth = _tree_depth(gl.dom_tree(idom), entry)
        adj = {v: set() for v in succ}
        for u in succ:
            for w in succ[u]:
                if u != w:
                    adj[u].add(w); adj[w].add(u)
        tw = gl.treewidth_min_degree(adj)
        tw_nx, _ = nx.approximation.treewidth_min_degree(nx.Graph([(u, w) for u in adj for w in adj[u]]) if any(adj.values()) else nx.empty_graph(1))
        print(f"  {name:13} blocks={len(succ):3} loops={len(loops)} "
              f"loop_sizes={sorted(len(x) for x in loops.values())} irreducible={len(irr)} "
              f"domtree_depth={depth} tw<={tw}{'' if tw == tw_nx else ' (nx=' + str(tw_nx) + ')'}")
    print("  dominator 與 nx.immediate_dominators 全部一致:", ok_dom)


def _tree_depth(tree, root):
    best, st = 0, [(root, 0)]
    while st:
        v, d = st.pop()
        best = max(best, d)
        st.extend((c, d + 1) for c in tree[v])
    return best


def stage3(b):
    print("== Stage 3：call graph 與遞迴（SCC） ==")
    cg = b.call_graph()
    print(f"  call graph: {len(cg)} 節點, {sum(map(len, cg.values()))} 邊")
    sccs = gl.tarjan_scc(cg)
    rec = [sorted(c) for c in sccs if len(c) > 1 or next(iter(c)) in cg[next(iter(c))]]
    print("  遞迴（SCC 大小 > 1 或自環）:", sorted(rec))
    ref = sorted(sorted(c) for c in nx.strongly_connected_components(to_nx(cg)))
    print("  SCC 與 NetworkX 一致:", sorted(sorted(c) for c in sccs) == ref)
    dag, comp_of = gl.condensation(cg, sccs)
    print(f"  condensation DAG: {len(dag)} 個分量，是 DAG:", nx.is_directed_acyclic_graph(to_nx(dag)))
    return cg


def norm(name):
    """把 _FORTIFY_SOURCE 的變體名稱對回原名：__printf_chk@plt → printf。"""
    n = name.removesuffix("@plt")
    if n.startswith("__") and n.endswith("_chk"):
        n = n[2:-4]
    return n


def stage4(b0, b2, cg0, cg2):
    print("== Stage 4：-O0 vs -O2 函式配對 ==")

    def feats(b):
        out = {}
        for name in b.funcs:
            if name.endswith(".cold"):
                continue
            succ, blocks, calls = b.cfg(name)
            lab = {}
            for v in succ:
                last = blocks[v][-1]
                lab[v] = "ret" if last.group(CS_GRP_RET) else ("jmp" if last.mnemonic == "jmp" else
                                                             ("jcc" if last.group(CS_GRP_JUMP) else "fall"))
            ext = Counter(norm(c) for cs in calls.values() for c in cs if c.endswith("@plt"))
            out[name] = (gl.wl_features(succ, lab, h=2), ext)
        return out

    def jaccard(A, B):
        u = sum((A | B).values())
        return sum((A & B).values()) / u if u else 1.0      # 兩邊都不呼叫外部函式也算一致

    def assign(S):
        cost = [[-S[a, c] for c in n2] for a in n0]
        return {n0[i]: n2[j] for i, j in enumerate(gl.hungarian(cost))}

    F0, F2 = feats(b0), feats(b2)
    n0, n2 = sorted(F0), sorted(F2)
    wl = {(a, c): gl.cosine(F0[a][0], F2[c][0]) for a in n0 for c in n2}
    ext = {(a, c): jaccard(F0[a][1], F2[c][1]) for a in n0 for c in n2}
    nb0 = {a: (cg0[a] | gl.reverse(cg0)[a]) & set(n0) for a in n0}
    nb2 = {c: (cg2[c] | gl.reverse(cg2)[c]) & set(n2) for c in n2}
    S = {k: 0.5 * wl[k] + 0.5 * ext[k] for k in wl}
    report = [("結構 WL", assign(wl)), ("WL + 外部呼叫", assign(S))]
    M = report[-1][1]
    for _ in range(3):                                       # 沿 call graph 傳播：鄰居已配對者加分
        agree = {(a, c): len({M[x] for x in nb0[a]} & nb2[c]) / max(len(nb0[a]), len(nb2[c]), 1)
                 for a in n0 for c in n2}
        M = assign({k: S[k] + 0.5 * agree[k] for k in S})
    report.append(("+ call graph 傳播", M))
    import scipy.optimize
    for name, M in report:
        print(f"  {name:16} 正確配對 {sum(M[a] == a for a in n0)}/{len(n0)}")
    import numpy as np
    C = np.array([[-S[a, c] for c in n2] for a in n0])
    r, cidx = scipy.optimize.linear_sum_assignment(C)
    mine = assign(S)
    print("  Hungarian 與 scipy.linear_sum_assignment 總成本一致:",
          abs(C[r, cidx].sum() - sum(-S[a, mine[a]] for a in n0)) < 1e-9)
    print("  仍配錯的:", sorted((a, M[a]) for a in n0 if M[a] != a))


def stage5(cg, tag):
    print(f"== Stage 5（{tag}）：call graph 上的 chokepoint ==")
    sink = "system@plt"
    print("  main 能到 system:", sink in gl.reachable(cg, "main"))
    idom = gl.dominators(cg, "main")
    chain, v = [], idom[sink]
    while v != "main":
        chain.append(v); v = idom[v]
    print(f"  {sink} 的支配者鏈（main 以外）:", chain)
    for t, prot in [(sink, ()), ("run_cmd", ())]:
        if t not in gl.reachable(cg, "main"):
            print(f"  main → {t}: 不可達（被 inline 或改成尾呼叫後消失）")
            continue
        val, cut = gl.min_vertex_cut(cg, "main", t, prot)
        H = to_nx(cg); H.remove_nodes_from(cut)
        print(f"  main → {t}: 最小點割 = {val} {cut}；刪掉後仍可達: {nx.has_path(H, 'main', t)}；"
              f"nx.minimum_node_cut 大小 = {len(nx.minimum_node_cut(to_nx(cg), 'main', t))}")
    pr = gl.pagerank(cg)
    ref = nx.pagerank(to_nx(cg), alpha=0.85, tol=1e-12, max_iter=1000)
    top = sorted(pr, key=pr.get, reverse=True)[:5]
    print("  PageRank 前 5:", [(v, round(pr[v], 3)) for v in top],
          "與 NetworkX 最大差:", f"{max(abs(pr[v] - ref[v]) for v in pr):.1e}")


if __name__ == "__main__":
    b0, b2 = Binary(sys.argv[1]), Binary(sys.argv[2])
    stage1(b0)
    stage2(b0)
    cg = stage3(b0)
    cg2 = b2.call_graph()
    stage4(b0, b2, cg, cg2)
    stage5(cg, "-O0")
    stage5(cg2, "-O2")
```

</details>

<details>
<summary>點開 test_graphlab.py（驗收測試）</summary>

```python
"""graphlab 驗收測試：pytest -q test_graphlab.py（需要 demo_O0、demo_O2 在同目錄）"""
import itertools
import random
import networkx as nx
import pytest
import graphlab as gl


def rand_digraph(seed, n=9, p=0.25):
    rng = random.Random(seed)
    succ = {v: set() for v in range(n)}
    for u in range(n):
        for w in range(n):
            if u != w and rng.random() < p:
                succ[u].add(w)
    return succ


def to_nx(succ):
    G = nx.DiGraph()
    G.add_nodes_from(succ)
    G.add_edges_from((u, w) for u in succ for w in succ[u])
    return G


@pytest.mark.parametrize("seed", range(40))
def test_dominators_match_networkx(seed):
    succ = rand_digraph(seed)
    idom = gl.dominators(succ, 0)
    ref = nx.immediate_dominators(to_nx(succ), 0)
    ref.setdefault(0, 0)              # NetworkX 3.7 不把 start 放進結果
    assert idom == ref


@pytest.mark.parametrize("seed", range(40))
def test_dominator_iff_single_vertex_cut(seed):
    # d 嚴格支配 t（d ≠ entry, t）⟺ 刪掉 d 之後 entry 到不了 t：dominator 就是大小 1 的點割（Menger 的特例）
    succ = rand_digraph(seed)
    idom = gl.dominators(succ, 0)
    for t in idom:
        for d in idom:
            if d in (0, t):
                continue
            H = {v: ws - {d} for v, ws in succ.items() if v != d}
            assert gl.dominates(idom, d, t) == (t not in gl.reachable(H, 0))


@pytest.mark.parametrize("seed", range(40))
def test_scc_matches_networkx(seed):
    succ = rand_digraph(seed, n=12, p=0.15)
    mine = sorted(sorted(c) for c in gl.tarjan_scc(succ))
    assert mine == sorted(sorted(c) for c in nx.strongly_connected_components(to_nx(succ)))
    dag, _ = gl.condensation(succ, gl.tarjan_scc(succ))
    assert nx.is_directed_acyclic_graph(to_nx(dag))


def test_natural_loops_and_irreducible():
    # while 迴圈裡有 if：一個 header、迴圈體 {1,2,3,4}
    succ = {0: {1}, 1: {2, 5}, 2: {3, 4}, 3: {1}, 4: {1}, 5: set()}
    loops, irr = gl.natural_loops(succ, 0)
    assert loops == {1: {1, 2, 3, 4}} and irr == []
    # 經典不可歸約圖：兩個入口跳進同一個環
    succ = {0: {1, 2}, 1: {2}, 2: {1}}
    loops, irr = gl.natural_loops(succ, 0)
    assert loops == {} and len(irr) == 1


@pytest.mark.parametrize("seed", range(30))
def test_min_vertex_cut_matches_networkx(seed):
    succ = rand_digraph(seed, n=10, p=0.3)
    s, t = 0, 9
    if t not in gl.reachable(succ, s) or t in succ[s]:
        return                          # 不可達，或 s→t 直接相連（點割不存在）
    val, cut = gl.min_vertex_cut(succ, s, t)
    assert val == len(nx.minimum_node_cut(to_nx(succ), s, t))
    H = {v: ws - set(cut) for v, ws in succ.items() if v not in cut}
    assert t not in gl.reachable(H, s)


@pytest.mark.parametrize("seed", range(20))
def test_hungarian_matches_bruteforce(seed):
    rng = random.Random(seed)
    n = 5
    cost = [[rng.randint(0, 20) for _ in range(n)] for _ in range(n)]
    a = gl.hungarian(cost)
    best = min(sum(cost[i][p[i]] for i in range(n)) for p in itertools.permutations(range(n)))
    assert sorted(a) == list(range(n)) and sum(cost[i][a[i]] for i in range(n)) == best


def test_pagerank_matches_networkx():
    succ = rand_digraph(3, n=15, p=0.2)
    pr = gl.pagerank(succ, iters=200)
    ref = nx.pagerank(to_nx(succ), alpha=0.85, tol=1e-12, max_iter=1000)
    assert max(abs(pr[v] - ref[v]) for v in succ) < 1e-8
    assert abs(sum(pr.values()) - 1) < 1e-9


def exact_treewidth(adj):
    """窮舉所有消去順序（n ≤ 7）：treewidth = min over orderings of 最大消去度數。"""
    best = len(adj)
    for order in itertools.permutations(adj):
        g = {v: set(ws) for v, ws in adj.items()}
        w = 0
        for v in order:
            nb = g.pop(v)
            w = max(w, len(nb))
            for a in nb:
                g[a].discard(v)
                g[a] |= nb - {a}
        best = min(best, w)
    return best


@pytest.mark.parametrize("seed", range(15))
def test_treewidth_upper_bound(seed):
    rng = random.Random(seed)
    n = 7
    adj = {v: set() for v in range(n)}
    for u, w in itertools.combinations(range(n), 2):
        if rng.random() < 0.4:
            adj[u].add(w); adj[w].add(u)
    assert gl.treewidth_min_degree(adj) >= exact_treewidth(adj)
    cyc = {v: {(v - 1) % n, (v + 1) % n} for v in range(n)}
    assert gl.treewidth_min_degree(cyc) == 2


def test_wl_isomorphism_invariance():
    succ = rand_digraph(5)
    perm = {v: (v * 7 + 3) % 9 for v in succ}
    succ2 = {perm[v]: {perm[w] for w in ws} for v, ws in succ.items()}
    f1 = gl.wl_features(succ, {v: "B" for v in succ}, h=3)
    f2 = gl.wl_features(succ2, {v: "B" for v in succ2}, h=3)
    assert f1 == f2 and abs(gl.cosine(f1, f2) - 1) < 1e-12


def test_demo_binary_end_to_end():
    from elfx import Binary
    b = Binary("demo_O0")
    cg = b.call_graph()
    rec = sorted(sorted(c) for c in gl.tarjan_scc(cg) if len(c) > 1 or next(iter(c)) in cg[next(iter(c))])
    assert rec == [["fact"], ["parse_expr", "parse_factor", "parse_term"]]
    assert gl.min_vertex_cut(cg, "main", "system@plt") == (1, ["run_cmd"])
    assert gl.min_vertex_cut(cg, "main", "run_cmd")[0] == 2
    succ, _, _ = b.cfg("checksum2d")
    loops, irr = gl.natural_loops(succ, b.funcs["checksum2d"][0])
    assert len(loops) == 2 and irr == []                    # 原始碼的兩層 for
```

```text
$ python -m pytest -q test_graphlab.py
........................................................................ [ 38%]
........................................................................ [ 76%]
.............................................                            [100%]
189 passed in 1.06s
```

</details>

**解答說明**：

1. **Dominator 用迭代法而不是 Lengauer–Tarjan**。Cooper、Harvey、Kennedy 的論文論證：在 CFG 這種規模（幾十到幾千個 block）上，依逆後序反覆套用 `idom(v) = intersect(所有已處理前驅)`，實務上比 Lengauer–Tarjan 快而且好寫。正確性來自 dominator 是資料流方程式 `Dom(v) = {v} ∪ ⋂ Dom(p)` 的最大不動點（[Ch 14](./14-dominator-tree.md)），`intersect` 是在 dominator tree 上求兩點的最近共同祖先。
2. **「dominator = 大小 1 的點割」**。d 嚴格支配 t（d 不是 entry 也不是 t）⟺ 每條 entry–t 路徑都經過 d ⟺ `{d}` 是一個分開 entry 與 t 的點割。由 Menger 定理（[Ch 12](./12-menger-theorem.md)），t 沒有非平凡 dominator ⟺ entry 到 t 至少有 2 條內部點不相交的路徑 ⟺ 最小點割 ≥ 2。`test_dominator_iff_single_vertex_cut` 在 40 張隨機圖上逐對驗證這個等價。dominator tree 一次給出所有大小 1 的 chokepoint，max-flow 處理大小 ≥ 2 的。
3. **為什麼 Stage 4 要一對一指派**。各自取 top-1 會讓好幾個 `-O0` 函式都配到同一個 `-O2` 函式；patch diff 的語意是「每個函式最多一個對應」，那正是二分圖的最大權完美匹配，即 [Ch 24](./24-min-cost-flow-assignment.md) 的指派問題（把相似度取負當成本）。
4. **傳播為什麼有效**。BinDiff 一類工具的核心想法（Ch 40 的延伸閱讀）：單一函式的特徵不夠時，用 call graph 的結構當約束。`load_config` 被錨定之後，它呼叫的 `run_hook` 就有了「鄰居已配對」的加分；這種加分沿 call graph 擴散，把結構上無法區分的小函式拉開。
5. **不可歸約邊的判定**。對固定的一次 DFS，CFG 可歸約 ⟺ 每條 retreating edge 都是 back edge（head 支配 tail）——這是可歸約圖的一個標準等價刻畫，本專案只用它、不證明。demo 沒有 `goto`，所以沒有不可歸約邊；測試用「兩個入口的環」確認判定能抓到反例。
6. **複雜度**。Stage 2 每個函式是 `O(迭代輪數 × E)`；Stage 4 的相似度矩陣是 `O(n²)` 對、Hungarian `O(n³)`；Stage 5 的 Edmonds–Karp 在單位容量點割上增廣次數不超過割值。對真實的大 binary（上萬個函式），瓶頸會是 Stage 4 的 `O(n²)` 相似度——實務上先用外部呼叫名稱這類錨點分桶，只在桶內比。

## 測試用例

| 測試 | 預期 | 驗證的是 |
| --- | --- | --- |
| 40 張隨機有向圖 | idom 與 NetworkX 一致 | dominator 實作 |
| 40 張隨機有向圖，所有 (d, t) | d 嚴格支配 t ⟺ 刪 d 後 t 不可達 | Menger 特例 |
| 40 張隨機有向圖 | SCC 與 NetworkX 一致，condensation 無環 | Tarjan |
| while 內有 if | `{1: {1, 2, 3, 4}}`，無不可歸約邊 | 自然迴圈 |
| 兩個入口的環 `0→1, 0→2, 1↔2` | 沒有自然迴圈、1 條不可歸約邊 | 不可歸約判定 |
| 30 張隨機有向圖 | 點割大小 = `nx.minimum_node_cut`，刪掉後不可達 | node splitting |
| 20 個 5×5 隨機成本矩陣 | Hungarian = 窮舉 120 種排列的最小值 | 指派問題 |
| 15 張隨機 7 點圖 | min-degree 上界 ≥ 窮舉所有消去序的精確 treewidth；7-環 = 2 | treewidth 上界 |
| 隨機圖與它的重新編號 | WL 特徵完全相同 | 同構不變性 |
| `demo_O0` 端到端 | 遞迴 SCC、`[run_cmd]` 割、`checksum2d` 2 個迴圈 | 整條流水線 |

## 延伸挑戰（加分）

- **跳躍表**：在 demo 裡加一個 7 個 case 的 `switch`，實作 Ch 40 踩雷 2 的跳躍表解析，讓 Stage 2 沒有不可達 block。
- **Stripped binary**：用 `gcc -O2 -s` 編譯，不靠 `.symtab` 找函式邊界（`endbr64` 樣式、所有 `call` 的目標、`.eh_frame` 的 FDE），Stage 4 的配對還剩幾個對？
- **控制相依與 taint**：把 [Ch 38](./38-program-analysis-graphs.md) 的 reaching definitions 搬到 binary 上（以暫存器與堆疊槽為變數），在 `cmd_exec` 裡追「`arg` 有沒有不經過 `sanitize` 就流到 `system`」。
- **Register interference 圖**：對每個函式的 `-O0` 版本，以「同時存活的堆疊槽」建干涉圖，用 [Ch 16](./16-chordal-graphs.md) 的完美消去序檢查它是不是 chordal，並用 [Ch 17](./17-register-allocation.md) 的 Chaitin–Briggs 著色估計最少需要幾個暫存器。
- **譜分群**：在 call graph（無向化）上做 [Ch 27](./27-spectral-graph-theory.md)–[Ch 28](./28-laplacian-cheeger.md) 的 Fiedler 向量二分，看它是否把「運算式解析」與「指令分派」兩群函式分開。
- **大規模**：把 Stage 3 的 call graph 改成 [Ch 41](./41-large-graph-engineering.md) 的 CSR，對一個真實的大型開源 binary（例如你自己編的 `busybox`）跑 SCC 與 dominator，量測時間與記憶體。

## 自我檢核

- [ ] 不看筆記，畫出 graphlab 的五個階段，並說出每一階段用到哪一個定理、哪一個演算法。
- [ ] 解釋為什麼 dominator tree 與 min-cut 回答的是同一類問題的不同尺寸，並用 Menger 定理證明「沒有非平凡 dominator ⟺ 最小點割 ≥ 2」。
- [ ] 如果主管問「為什麼 release build 的 chokepoint 和 debug build 不一樣」，你能用 inline 與尾呼叫解釋，並說明報告該以哪個 build 為準嗎？
- [ ] Stage 4 從 4/16 到 16/16，每一步加進來的是哪一種圖的資訊？為什麼結構資訊在跨最佳化時最先失效？
- [ ] 能說出自己的實作和參考實作的差異（例如你怎麼處理尾呼叫、怎麼定義迴圈），以及各自的取捨。

## 延伸閱讀

- **[A Simple, Fast Dominance Algorithm](https://www.cs.tufts.edu/comp/150FP/archive/keith-cooper/dom14.pdf)** — Keith D. Cooper、Timothy J. Harvey、Ken Kennedy（Rice University；常被引用為 Software Practice & Experience，2001）
  - **核心貢獻**：論證以逆後序迭代、用 `intersect` 在 dominator tree 上求共同祖先的簡單演算法，在實際 CFG 上比 Lengauer–Tarjan 快；也給出 dominance frontier 的簡單算法。
  - **讀哪裡**：演算法描述與 `intersect` 的圖解；實驗章節可略讀。
  - **和本專案的關聯**：`graphlab.dominators` 就是這篇的演算法。
- **[All Structured Programs Have Small Tree Width and Good Register Allocation](https://doi.org/10.1006/inco.1997.2697)** — Mikkel Thorup，Information and Computation 142(2)（1998），pp. 159–181
  - **核心貢獻**：證明無 `goto` 程式的 CFG treewidth 有小常數上界（C 程式 ≤ 6），並利用它在 register allocation 上得到好的近似。
  - **讀哪裡**：主定理的敘述與「結構化語句對應到樹分解」的構造；需要先懂 [Ch 36](./36-treewidth.md) 的樹分解。
  - **和本專案的關聯**：Stage 2 的 `tw<=2` 都在這個上界之下；Gustedt 等人對 Java 程式的實測平均約 2.5，和我們的觀察一致。
- **[Graph-based Comparison of Executable Objects](https://www.zynamics.com/downloads/bindiffsstic05-1.pdf)** — Thomas Dullien、Rolf Rolles，SSTIC（2005）
  - **核心貢獻**：BinDiff 的設計基礎——以穩定特徵找初始配對、再沿 call graph 傳播。
  - **讀哪裡**：配對傳播的策略那節，對照 Stage 4 的第三版。
- **[security/reverse_engineering](../../security/reverse_engineering/README.md)** 的 [Patch diffing](../../security/reverse_engineering/27-patch-diffing.md) 與 [辨識函式](../../security/reverse_engineering/07-recognizing-functions.md)：graphlab 每一步在真實工具裡的對應操作，以及 stripped binary 的函式邊界辨識。
- **[compilers/ssa_optimizations](../../compilers/ssa_optimizations/README.md)** 的 [dominator tree 演算法](../../compilers/ssa_optimizations/03-dominator-tree-algorithm.md)、[迴圈辨識](../../compilers/ssa_optimizations/20-loop-identification.md)、[call graph](../../compilers/ssa_optimizations/27-call-graph.md)：編譯器端如何使用 Stage 2–3 的同一組演算法。

從柯尼斯堡的七座橋走到 ELF 裡的 call graph，這門課的每一個定理都在 graphlab 的某一行程式裡。回到課程首頁，挑一個延伸挑戰繼續做下去。

→ [回到課程首頁](./README.md)
