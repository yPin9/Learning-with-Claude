# Ch 24 — 查詢處理全景

> **目標**：在動手寫 parser、planner、executor 之前，先看清整條查詢處理管線（pipeline）的全景——每一層的輸入、輸出、職責邊界，以及為什麼要切這麼多層。Part 4 的所有章節都是這張地圖的局部放大。

## 宣告式 vs 命令式：鴻溝在哪裡

SQL 是宣告式（declarative）語言——你說「我要什麼」，而不是「怎麼拿」：

```sql
SELECT e.name, d.dept_name
FROM employees e
JOIN departments d ON e.dept_id = d.id
WHERE e.salary > 100000
ORDER BY e.name;
```

這一句話完全沒告訴資料庫：
- 先掃 employees 還是 departments？
- 用 hash join 還是 nested loop join？
- 先過濾薪水再 join，還是 join 之後再過濾？
- salary 上有索引嗎？要不要用？

把這個宣告式意圖轉換成具體執行步驟，就是查詢處理（query processing）要做的事。這個轉換過程至少六個階段，每一層都有清晰的輸入輸出邊界。

## 完整管線圖

```
SQL 字串 (text)
   │  "SELECT e.name, d.dept_name FROM employees e JOIN ..."
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  Ch 25  SQL Parser (詞法 + 語法分析)                     │
│                                                         │
│  輸入：SQL 字串                                          │
│  輸出：AST（抽象語法樹）                                  │
│                                                         │
│  工作：切 token → 依文法建樹                              │
│  不做：檢查 table 存不存在、型別對不對                     │
└─────────────────────────────────────────────────────────┘
   │  SelectStmt { from: [...], where: Expr, ... }
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  Ch 26  Binder / Analyzer（綁定 / 語義分析）              │
│                                                         │
│  輸入：AST + Catalog（資料庫的 metadata）                 │
│  輸出：帶型別的 AST（每個節點知道自己的型別）               │
│                                                         │
│  工作：name resolution（把 "employees" 綁到 Catalog 的    │
│        實體）、型別推斷、ambiguity 消除                   │
│  不做：選執行策略                                         │
└─────────────────────────────────────────────────────────┘
   │  Typed AST（Column 節點帶 ColumnDef、型別）
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  Ch 27  Logical Planner（邏輯計畫）                      │
│                                                         │
│  輸入：Typed AST                                         │
│  輸出：Logical Plan Tree（關聯代數運算子樹）               │
│                                                         │
│  工作：把 SQL 翻成 σ（選擇）π（投影）⋈（join）等          │
│        關聯代數（relational algebra）運算子樹             │
│  不做：選 join 演算法、決定索引掃描                        │
└─────────────────────────────────────────────────────────┘
   │  LogicalPlan::Join(LogicalPlan::Filter(...), ...)
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  Ch 32–33  Optimizer（查詢優化）                         │
│                                                         │
│  輸入：Logical Plan Tree                                 │
│  輸出：Optimized Logical Plan Tree                       │
│                                                         │
│  Rule-based：謂詞下推（predicate pushdown）、projection  │
│              pruning 等等效變換                          │
│  Cost-based：估計基數（cardinality estimation）、         │
│              選 join 順序、決定是否用索引                  │
└─────────────────────────────────────────────────────────┘
   │  Optimized Logical Plan（謂詞已下推到 leaf）
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  Ch 28  Physical Planner（物理計畫）                     │
│                                                         │
│  輸入：Optimized Logical Plan                            │
│  輸出：Physical Plan Tree                                │
│                                                         │
│  工作：把每個 logical operator 選一個具體實作             │
│        ⋈ → HashJoin 或 MergeJoin 或 NestedLoopJoin      │
│        σ on indexed col → IndexScan 而非 SeqScan        │
└─────────────────────────────────────────────────────────┘
   │  PhysicalPlan::HashJoin(IndexScan("employees"), SeqScan("departments"))
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  Ch 29–31  Executor（執行）                              │
│                                                         │
│  輸入：Physical Plan Tree                                │
│  輸出：Result rows（最終結果）                            │
│                                                         │
│  Volcano model：拉模型，每個算子呼叫 next() 從子節點拉資料│
│  Vectorized：批量拉，每次拉一個 batch（列向量）            │
└─────────────────────────────────────────────────────────┘
   │
   ▼
Result rows → 回傳給客戶端
```

## 各層職責對照表

| 階段 | 別名 | 輸入 | 輸出 | 失敗原因 |
|------|------|------|------|----------|
| Parser | 語法分析 | SQL 字串 | AST | 語法錯誤 |
| Binder | Analyzer / 語義分析 | AST + Catalog | Typed AST | table 不存在、型別不符 |
| Logical Planner | Algebraizer | Typed AST | Logical Plan | （理論上不失敗） |
| Optimizer | Rewriter | Logical Plan | Optimized Plan | （通常不失敗，最壞回原計畫） |
| Physical Planner | Code selector | Optimized Plan | Physical Plan | 沒有可用實作（少見） |
| Executor | — | Physical Plan | Rows | runtime error（除零、型別溢位） |

## 為什麼要切這麼多層？

**同一份 SQL，不同的物理執行策略**。Logical Plan 是「要做什麼」的抽象描述，Physical Plan 才是「怎麼做」。這個分離讓 optimizer 可以在 logical 層做等效變換（謂詞下推不影響結果的正確性），再在 physical 層另外選演算法。

沒有這個分離，優化邏輯就只能直接改執行步驟，每加一種優化規則都得同時考慮執行實作，複雜度爆炸。PostgreSQL 的 planner/optimizer 至今約有 80,000 行 C，如果沒有清晰的層次分離，那個複雜度根本不可能管理。

**各層可以獨立測試**。Parser 的 unit test 只管 AST 是否正確，不需要 Catalog 存在。Optimizer 的測試只管 logical plan 是否等效，不需要真正執行。

**不同的 SQL 方言可以共用後端**。多數資料庫的 SQL dialect 主要差異在 parser 層，logical/physical/executor 層可以共用。

## Postgres 的對應結構

你之後讀 PostgreSQL 原始碼時，這些層的對應是：

```
pg/src/backend/
├── parser/       ← Parser：raw_parser() 產出 raw parse tree
├── analyze/      ← Binder：parse_analyze() 產出 Query（帶型別）
├── optimizer/
│   ├── plan/     ← Logical → Physical Planner：planner() → PlannedStmt
│   └── path/     ← Cost-based Optimizer：選 access path
└── executor/     ← Executor：ExecutorRun()
```

PostgreSQL 的術語裡沒有顯式的「logical plan tree」作為中間資料結構——Query 節點兼任了 typed AST 與部分 logical plan 的角色，但概念層次是一樣的。

## Part 4 章節導覽

以下章節各是這張地圖的局部放大：

- **Ch 25 SQL Parser**：parser 層，手寫遞迴下降 lexer + parser
- **Ch 26 Catalog / Schema**：binder 的依賴——catalog 怎麼組織、name resolution 怎麼做
- **Ch 27 Logical Plan**：logical planner 層，AST → 關聯代數樹
- **Ch 28 Physical Plan**：physical planner，logical → physical operator 選擇
- **Ch 29 執行模型**：Volcano vs vectorized，executor 框架
- **Ch 30–31 Join / 排序聚合**：executor 的核心演算法
- **Ch 32–33 查詢優化**：rule-based 與 cost-based optimizer

## 踩雷

1. **Parser 不做語義驗證**。很多人第一次寫資料庫時，把「table 不存在」的錯誤放在 parser 裡報——這是錯的。Parser 只管語法，它甚至不知道 Catalog 的存在。語義驗證屬於 binder 層。

2. **Logical Plan 不是 AST 的同義詞**。AST 忠實反映 SQL 語法結構（`SELECT ... FROM ... WHERE ...`），Logical Plan 是語義等效的關聯代數樹。兩者形狀完全不同——subquery 在 AST 裡是嵌套的，在 logical plan 裡可能已被拉平成 join。

3. **優化只能在 logical 層做等效變換**。謂詞下推（把 WHERE 條件推到離資料掃描更近的地方）在 logical 層是純代數等效，不影響正確性；但如果你在 physical 層才想做，就得同時考慮算子的執行語義（例如 HashJoin 的 probe 順序），難度大增。

4. **Physical plan 不是唯一決定的**。同一個 optimized logical plan 可以有多個合法的 physical plan，optimizer 的 cost model 負責挑最便宜的那個。Cost model 估得準不準決定了資料庫快不快。

5. **Executor 的錯誤是 runtime error，不是 plan error**。除以零、型別溢位、FK 違反——這些在 physical plan 建好之後才會在執行時爆出來，plan 那一層看不到。

## 進階延伸

**Cascades 框架**：現代優化器（SQL Server、CockroachDB 的 Optigen）使用 Cascades/Volcano 框架，把 logical→physical 的對應做成規則集，exploration 過程用備忘錄（memo）去重。比 PostgreSQL 那種 top-down 兩遍式更系統化。

**Query compilation**：Snowflake、HyPer 等系統把 physical plan 編譯成機器碼（LLVM IR），消除 Volcano 模型的虛函式呼叫 overhead。這是純解釋執行和編譯執行的根本差異。

**Incremental view maintenance**：物化視圖（materialized view）在有資料更新時要增量維護——這需要 planner 能推導出「delta query」，是查詢處理的一個進階主題。

## 本章重點整理

- SQL 是宣告式的，查詢處理負責把它轉成命令式執行步驟
- 管線六層：Parser → Binder → Logical Planner → Optimizer → Physical Planner → Executor
- Logical Plan 與 Physical Plan 分離是優化器複雜度可控的核心設計決策
- 每層有清晰的輸入輸出邊界，可以獨立測試

## 自我檢核

- [ ] 我能說出查詢處理管線六層各自的輸入輸出
- [ ] 我能解釋 Logical Plan 和 Physical Plan 的差異，以及為什麼要分開
- [ ] 我能說出 Parser 和 Binder 的職責邊界——「table 不存在」的錯誤在哪層報？
- [ ] 我能在 Postgres 原始碼目錄裡找到各層的對應位置

## 延伸閱讀

1. **CMU 15-445 Lecture 13–15（Query Planning & Optimization）**
   讀什麼：query processing pipeline 的整體架構、logical vs physical plan 的設計哲學
   關聯：本章地圖的學術版本，Andy Pavlo 解釋得非常清楚，特別是 cost model 的動機

2. **《Database Internals》Ch 4（B-Tree Lookups）序言部分 + Part II Overview**
   讀什麼：Alex Petrov 對「查詢如何找到資料」的描述，連結 Part 4 與我們在 Part 1–2 做的儲存引擎
   關聯：讓你看清 executor 的 scan 操作最終怎麼打到 B-tree 的 range scan

3. **PostgreSQL Documentation：[The Query Planner](https://www.postgresql.org/docs/current/planner-optimizer.html)**
   讀什麼：pg 官方對 planner 架構的白話說明，特別是 path/plan 的區分（path = 候選計畫，plan = 選定的計畫）
   關聯：把本章的抽象管線和工業級實作對應起來

4. **《Readings in Database Systems》（Red Book）— [Architecture of a Database System](https://dsf.berkeley.edu/papers/fntdb07-architecture.pdf)**（Hellerstein et al.）
   讀什麼：Section 4（Relational Query Processor）——30 頁，把 parse/rewrite/optimize/execute 每層都解釋清楚
   關聯：本章的學術文獻對應，寫資料庫系統的工程師應該讀過一遍

→ [Ch 25 SQL Parser](./25-sql-parser.md)
