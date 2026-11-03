# 圖論與離散數學學習筆記：從證明到真實程式分析

> 給寫過 BFS/DFS、會刷題，但從沒認真證明過一個圖論定理的系統/資安工程師。

這門課把圖論當主軸、離散數學當地基：每個核心定理都親手證明，每個演算法都自己實作並用 NetworkX 對答案，最後把它們接回你已經在用的東西——編譯器的 dominator tree 與 register allocation、binary 的 CFG 與函式相似度、AD 的攻擊路徑。學完後，你看到一張圖，會先問「它有什麼結構」，而不是「該套哪個模板」。

## 為什麼學這個？

- **你的系統課到處都是圖，但沒人教過圖本身**：SSA 的 dominator、register allocation 的著色、fuzzing 的 CFG 距離、CodeQL 的 dataflow、BloodHound 的攻擊路徑——底層都是圖論，這門課把它們背後的定理補齊。
- **從「會用」到「知道為什麼對」**：貪婪 MST 為什麼正確？SSA 為什麼讓著色變簡單？max-flow 為什麼等於 min-cut？證明是讓你在演算法失效時知道原因的唯一方法。
- **CS 數學主幹的第一塊**：證明技巧、計數、機率方法、譜方法會在計算理論、密碼學、ML 反覆出現。

## 先修知識

- Python（程度：能寫類別、讀標準函式庫文件）
- 基本演算法（程度：寫過 BFS/DFS、懂 Big-O；`algorithms/leetcode_patterns` 程度即可）
- 線性代數（程度：知道矩陣乘法與特徵值的定義；Part 5 會補需要的部分）
- 沒有也沒關係的：任何證明經驗——Ch 2 從零開始

## 課程地圖

### Part 1 — 證明與計數地基（Ch 0–6）

- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 圖論全貌：從柯尼斯堡七橋出發](./01-graph-theory-overview.md)
- [Ch 2 證明工具箱：歸納、反證、鴿籠、極端原理](./02-proof-toolbox.md)
- [Ch 3 計數基礎：排列組合、二項式、雙重計數](./03-counting-basics.md)
- [Ch 4 排容原理與錯排](./04-inclusion-exclusion.md)
- [Ch 5 遞迴關係與生成函數](./05-recurrences-generating-functions.md)
- [Ch 6 對稱計數：Burnside 與 Pólya](./06-burnside-polya.md)
- [練習 A：組合恆等式的暴力驗證器](./practice-a-combinatorial-identities.md)

### Part 2 — 圖的結構（Ch 7–14）

- [Ch 7 圖的定義、表示與同構](./07-graph-definitions.md)
- [Ch 8 Euler 與 Hamilton：容易與困難的分水嶺](./08-euler-hamilton.md)
- [Ch 9 樹：等價定義、Cayley 公式、Prüfer 編碼](./09-trees-cayley-prufer.md)
- [Ch 10 最小生成樹與 cut/cycle property](./10-minimum-spanning-trees.md)
- [Ch 11 連通度：割點、橋與 Tarjan](./11-connectivity-tarjan.md)
- [Ch 12 Menger 定理](./12-menger-theorem.md)
- [Ch 13 有向圖：SCC 與 DAG](./13-directed-graphs-scc-dag.md)
- [Ch 14 Dominator tree 與 Lengauer–Tarjan](./14-dominator-tree.md)
- [練習 B：CFG 分析器](./practice-b-cfg-analyzer.md)

### Part 3 — 著色與平面性（Ch 15–19）

- [Ch 15 著色：貪婪、Brooks、Vizing](./15-graph-coloring.md)
- [Ch 16 Chordal graph 與完美消去序](./16-chordal-graphs.md)
- [Ch 17 Register allocation：Chaitin–Briggs](./17-register-allocation.md)
- [Ch 18 平面圖：Euler 公式與 Kuratowski](./18-planar-graphs.md)
- [Ch 19 五色定理與四色定理](./19-five-and-four-color.md)
- [練習 C：Toy register allocator](./practice-c-register-allocator.md)

### Part 4 — Flow、Matching、Matroid（Ch 20–26）

- [Ch 20 最大流最小割與 Edmonds–Karp](./20-max-flow-min-cut.md)
- [Ch 21 Dinic 與 push-relabel](./21-dinic-push-relabel.md)
- [Ch 22 二分圖匹配：Hall、König、Hopcroft–Karp](./22-bipartite-matching.md)
- [Ch 23 一般圖匹配：Tutte 定理與 Blossom](./23-general-matching-blossom.md)
- [Ch 24 最小費用流與指派問題](./24-min-cost-flow-assignment.md)
- [Ch 25 Matroid：貪婪法為什麼有效](./25-matroids.md)
- [Ch 26 LP 對偶與全單模矩陣](./26-lp-duality.md)
- [練習 D：Flow 函式庫與問題建模](./practice-d-flow-modeling.md)

### Part 5 — 代數、機率與極值（Ch 27–33）

- [Ch 27 譜圖論入門](./27-spectral-graph-theory.md)
- [Ch 28 Laplacian、矩陣樹定理與 Cheeger 不等式](./28-laplacian-cheeger.md)
- [Ch 29 隨機遊走與 PageRank](./29-random-walks-pagerank.md)
- [Ch 30 Expander graph](./30-expander-graphs.md)
- [Ch 31 機率方法](./31-probabilistic-method.md)
- [Ch 32 Ramsey 理論與極值圖論](./32-ramsey-extremal.md)
- [Ch 33 隨機圖與相變](./33-random-graphs.md)
- [練習 E：譜分群與相變實驗](./practice-e-spectral-clustering.md)

### Part 6 — 計算複雜度（Ch 34–37）

- [Ch 34 NP-complete 圖問題與歸約](./34-np-complete-reductions.md)
- [Ch 35 把圖問題歸約到 SAT](./35-reductions-to-sat.md)
- [Ch 36 Treewidth 與樹分解](./36-treewidth.md)
- [Ch 37 圖同構與 Weisfeiler–Leman](./37-graph-isomorphism-wl.md)

### Part 7 — 系統與資安應用（Ch 38–41）

- [Ch 38 程式分析的圖：CPG 與 reachability](./38-program-analysis-graphs.md)
- [Ch 39 攻擊圖：路徑與最小切斷集](./39-attack-graphs.md)
- [Ch 40 Binary 相似度的圖比對](./40-binary-similarity.md)
- [Ch 41 大圖工程：CSR、cache 與平行 BFS](./41-large-graph-engineering.md)
- [練習 F：攻擊圖的 chokepoint 分析](./practice-f-attack-graph.md)
- [Final Project：graphlab 分析工具鏈](./final-project-graphlab.md)

## 學習方式建議

1. **先自己證一次再看證明**：每個定理先蓋住證明想五分鐘，卡住的地方就是這章真正要學的東西。
2. **小圖暴力對答案**：每個定理都能在 n ≤ 7 的所有圖上窮舉驗證（NetworkX 的 graph atlas 收錄到 7 點）；每個演算法都跟 NetworkX 對拍。程式抓不到的反例，證明裡一定有漏洞。
3. **把圖畫出來**：看不懂的結構，用 NetworkX + matplotlib 畫出來，比讀十遍定義有用。

## 精選資料庫

### 必讀基礎

- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - 本課的主教科書；習題量大、證明寫得完整，Part 2–4 大致對應其第 1–4、5–6、7 章
- **《Graph Theory》** — Reinhard Diestel（Springer GTM 173）
  - 比 West 精煉也更深；Menger、Ramsey、圖子式的章節是標準參考
- **《Concrete Mathematics》** — Graham、Knuth、Patashnik（Addison-Wesley，第 2 版，1994）
  - Part 1 計數、遞迴、生成函數的主要參考；年代久但內容不會過時
- **[NetworkX 官方文件](https://networkx.org/documentation/stable/)**
  - 本課拿來對答案的工具；演算法頁面都列了對應的原始論文

### 推薦論文

- **A Fast Algorithm for Finding Dominators in a Flowgraph** — Lengauer、Tarjan，ACM TOPLAS（1979）
  - 近線性時間的 dominator 演算法；Ch 14 的主角，LLVM、GCC 都有它的變體
- **Register Allocation & Spilling via Graph Coloring** — G. J. Chaitin，SIGPLAN Symposium on Compiler Construction（1982）
  - 把暫存器分配建模成圖著色的開山論文；Ch 17 的起點
- **Paths, Trees, and Flowers** — Jack Edmonds，Canadian Journal of Mathematics（1965）
  - Blossom 演算法原始論文，也是「多項式時間 = 有效率」這個觀念的源頭之一；Ch 23 的主角
- **Modeling and Discovering Vulnerabilities with Code Property Graphs** — Yamaguchi 等，IEEE S&P（2014）
  - 把 AST、CFG、PDG 合成一張圖來找漏洞；Ch 38 的核心，連到 `code_auditing` 的 Joern

### 讀完本課之後

- **《The Probabilistic Method》** — Alon、Spencer（把 Ch 31 的機率方法推到研究前沿）
- **《Combinatorial Optimization: Polyhedra and Efficiency》** — Alexander Schrijver（flow、matching、matroid 的百科全書，Part 4 的深水區）
- **Spectral and Algebraic Graph Theory** — Daniel Spielman 的教科書草稿（把 Part 5 的譜方法系統化）
