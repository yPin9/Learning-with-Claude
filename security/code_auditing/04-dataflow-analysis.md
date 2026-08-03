# Ch 4 — 資料流分析：lattice、transfer function、fixpoint

> **目標**：把 Ch 3 說的「data-dependence 邊怎麼算出來」拆開。你在 `ssa_optimizations` 學過 dataflow 的數學骨架；這章重點在**審計視角的取捨**——forward/backward、may/must 怎麼直接對應到「可能被污染」與「一定被 sanitize」，以及一個能跑的迷你 dataflow 引擎，讓你看清 taint 就是 dataflow 的一個 instance。

審計最核心的問題只有一句：**「這個值從哪來、流到哪去？」** 這句話的形式化就是 dataflow analysis。你已經懂 lattice/monotone framework/fixpoint（重疊處連 [`../../compilers/ssa_optimizations/07-dataflow-framework.md`](../../compilers/ssa_optimizations/07-dataflow-framework.md)），所以這裡不重推 Tarski 定理，而是把每個零件對回「它決定我的工具會漏報還是誤報」。

## 一分鐘對齊零件

dataflow analysis 的四個零件，每個都對應審計裡一個具體決定：

```
CFG          ──►  在哪裡傳（程式的骨架）
lattice L    ──►  「事實」用什麼集合表示，怎麼合併
transfer f_B ──►  一個 block 怎麼改變事實
fixpoint     ──►  反覆迭代到穩定 = 答案
```

- **lattice（格）**：分析追蹤的「事實」所形成的偏序集合，配 join（`⊔`，最小上界）與 meet（`⊓`，最大下界）。taint 的 lattice 很粗：每個變數「tainted 或 not」，事實是「目前 tainted 的變數集合」，格是這個集合的冪集，`⊑` 是 `⊆`。
- **transfer function `f_B`**：`OUT[B] = f_B(IN[B])`，描述一個 block 執行後事實怎麼變。必須 **monotone（單調）**：`x ⊑ y ⇒ f(x) ⊑ f(y)`——這保證 fixpoint 存在且迭代會停。
- **fixpoint（不動點）**：反覆套 transfer 直到不再變。lattice 高度有限 + monotone ⇒ 一定收斂（本章 termination 的保證全靠這兩點）。

審計視角的一句話總結：**lattice 決定精度上限、transfer 決定 sound/unsound、fixpoint 的收斂性決定會不會跑不完**。工具漏報/誤報/超時，全能回到這三點。

## join 的方向：may vs must，直接決定漏報還是誤報

這是全章最該記牢的一件事。CFG 有 merge point（多條 path 匯流），此時 `IN[B]` 要把各前驅的 `OUT` **合併**。合併用 `⊔` 還是 `⊓`，就是 may 與 must 的差別：

| | join 用什麼 | 直覺 | taint 語意 | 錯用的後果 |
|---|---|---|---|---|
| **may**（可能） | **union（∪）** | 「有任一條 path 成立就算」 | **可能被污染** | 該用 may 卻用 must → **漏報** |
| **must**（一定） | **intersection（∩）** | 「所有 path 都成立才算」 | **一定被 sanitize** | 該用 must 卻用 may → **誤報** |

taint 追污染要用 **may / union**：只要**有一條** path 讓值被污染，這個值就是「可能污染」，該報。反過來，「這個值一定被 sanitize 了嗎」是 **must / intersection**：只有**每一條** path 都 sanitize，才敢說安全。

**踩雷核心**：把 taint 建成 must 分析，你會在「只有部分 path 污染」的匯流點把污染事實 `∩` 掉，於是漏報真 bug。把 sanitizer 檢查建成 may，你會以為「只要有一條 path sanitize 就安全」，於是漏掉沒被 sanitize 的 path——同樣漏報，方向相反。**審計工具寧可誤報不可漏報**，所以 taint 傳播用 may、sanitizer 判定要求 must，這不是隨便選的。

## forward vs backward：污染往前追，活性往後追

- **forward（前向）**：`IN[B] = ⊔ OUT[preds]`，從 entry 往 exit 算。**reaching definitions、taint 都是 forward**——值從 source 出發，順著執行方向流向 sink。
- **backward（後向）**：`OUT[B] = ⊔ IN[succs]`，從 exit 往 entry 算。**liveness（活變數）是 backward**——「這個值之後還會不會被用到」得往後看。

審計最常用的是 forward taint（source 在前、sink 在後）。但 backward 也有用武之地：想問「這個危險 sink 的參數，往回追能不能碰到 source」時，backward slicing 更直接。CodeQL/Joern 的 dataflow API 兩個方向都給你（`reachableBy` 是 backward 味道的 API，Ch 3 見過）。

## 手動跑一輪 fixpoint：reaching definitions

先用最經典的 reaching definitions 手算，讓 worklist 演算法在你腦裡跑一遍。程式：

```
B0: d1: x = read()      // x 的定義 d1
B1: if (...)            // 分支
B2:   d2: y = x + 1     // y 的定義 d2
B3:   d3: y = 0         // y 的定義 d3（另一條 path）
B4: d4: z = y           // merge 後用 y
```

CFG：`B0→B1`，`B1→B2`，`B1→B3`，`B2→B4`，`B3→B4`。

transfer（forward，may）：`OUT[B] = gen[B] ∪ (IN[B] − kill[B])`。`gen` 是本 block 產生的 def，`kill` 是同名變數被覆蓋的舊 def。

`IN[B] = ∪ OUT[preds]`（union-join，因為 reaching definitions 是 may）。

手算收斂後：

| block | IN | OUT |
|---|---|---|
| B0 | {} | {x:d1} |
| B1 | {x:d1} | {x:d1} |
| B2 | {x:d1} | {x:d1, y:d2} |
| B3 | {x:d1} | {x:d1, y:d3} |
| B4 | {x:d1, **y:d2, y:d3**} | {x:d1, y:d2, y:d3, z:d4} |

**看 B4 的 IN**：`y:d2` 和 `y:d3` **同時**到達——因為 B4 有兩條前驅（B2、B3），union-join 把兩條 path 的 def 都收進來。這正是 may 的行為：B4 用的 `y`，可能是 d2 也可能是 d3。這也是為什麼 SSA 要在 B4 前插一個 `y₃ = φ(y₁, y₂)`——φ 就是把「兩條 def 都可能到」這件事顯式化（見 Ch 3）。

## 可跑的迷你 dataflow 引擎

下面這支我**真的跑過**（Python 3，無外部依賴）。它用同一個 worklist fixpoint 核心跑兩個分析：reaching definitions，再把 transfer 換掉就變 taint。重點是讓你看清 **「taint 只是換了 lattice 與 transfer 的 dataflow」**。

```python
from collections import defaultdict

class CFG:
    def __init__(self):
        self.succs = defaultdict(list); self.preds = defaultdict(list)
        self.gen = defaultdict(set); self.kill = defaultdict(set)
        self.nodes = []
    def add_edge(self, a, b):
        self.succs[a].append(b); self.preds[b].append(a)

# 建前面那張 CFG
g = CFG()
for n in ["B0","B1","B2","B3","B4"]: g.nodes.append(n)
for a,b in [("B0","B1"),("B1","B2"),("B1","B3"),("B2","B4"),("B3","B4")]:
    g.add_edge(a,b)

d1=("x","d1"); d2=("y","d2"); d3=("y","d3"); d4=("z","d4")
defs_of=defaultdict(set)
for v,l in [d1,d2,d3,d4]: defs_of[v].add((v,l))
g.gen["B0"]={d1}; g.kill["B0"]=defs_of["x"]-{d1}
g.gen["B2"]={d2}; g.kill["B2"]=defs_of["y"]-{d2}
g.gen["B3"]={d3}; g.kill["B3"]=defs_of["y"]-{d3}
g.gen["B4"]={d4}; g.kill["B4"]=defs_of["z"]-{d4}

def reaching_definitions(g):
    IN={n:set() for n in g.nodes}; OUT={n:set() for n in g.nodes}
    worklist=list(g.nodes); iters=0
    while worklist:
        iters+=1
        b=worklist.pop(0)
        IN[b]=set().union(*[OUT[p] for p in g.preds[b]]) if g.preds[b] else set()
        newOUT = g.gen[b] | (IN[b]-g.kill[b])       # transfer
        if newOUT!=OUT[b]:                           # 只有變了才推後繼
            OUT[b]=newOUT
            for s in g.succs[b]:
                if s not in worklist: worklist.append(s)
    return IN,OUT,iters

IN,OUT,iters=reaching_definitions(g)
print(f"=== Reaching Definitions（{iters} 次 worklist pop 收斂）===")
for n in g.nodes:
    fmt=lambda s:"{"+", ".join(sorted(v+":"+l for v,l in s))+"}" if s else "{}"
    print(f"{n}: IN={fmt(IN[n])}  OUT={fmt(OUT[n])}")

# 同一 worklist 核心，換 transfer 就變 taint（may / union-join）
def taint(g):
    IN={n:set() for n in g.nodes}; OUT={n:set() for n in g.nodes}
    def transfer(b, tin):
        t=set(tin)
        if   b=="B0": t|={"x"}              # source：x = read()
        elif b=="B2":
            if "x" in t: t|={"y"}           # y = x+1，污染傳播
        elif b=="B3": t.discard("y")        # y = 0，sanitize
        elif b=="B4":
            if "y" in t: t|={"z"}           # z = y
        return t
    worklist=list(g.nodes)
    while worklist:
        b=worklist.pop(0)
        IN[b]=set().union(*[OUT[p] for p in g.preds[b]]) if g.preds[b] else set()
        nout=transfer(b,IN[b])
        if nout!=OUT[b]:
            OUT[b]=nout
            for s in g.succs[b]:
                if s not in worklist: worklist.append(s)
    return IN,OUT

tIN,tOUT=taint(g)
print("\n=== Taint（may：union-join）===")
for n in g.nodes:
    print(f"{n}: IN={sorted(tIN[n])}  OUT={sorted(tOUT[n])}")
print("\nz 在 B4 出口 tainted？", "z" in tOUT["B4"])
```

真跑輸出（照貼）：

```
=== Reaching Definitions（5 次 worklist pop 收斂）===
B0: IN={}  OUT={x:d1}
B1: IN={x:d1}  OUT={x:d1}
B2: IN={x:d1}  OUT={x:d1, y:d2}
B3: IN={x:d1}  OUT={x:d1, y:d3}
B4: IN={x:d1, y:d2, y:d3}  OUT={x:d1, y:d2, y:d3, z:d4}

=== Taint（may：union-join）===
B0: IN=[]  OUT=['x']
B1: IN=['x']  OUT=['x']
B2: IN=['x']  OUT=['x', 'y']
B3: IN=['x']  OUT=['x']
B4: IN=['x', 'y']  OUT=['x', 'y', 'z']

z 在 B4 出口 tainted？ True
```

看 taint 的結果：B3 那條 path 把 `y` sanitize 掉了（`y=0`），但 B2 那條 path 讓 `y` tainted。B4 的 `IN` 用 **union-join** 收兩條前驅，於是 `y` 還在裡面 → `z=y` 讓 `z` 也 tainted → **報**。

這正是 may 分析的態度：**只要有一條 path 讓值可能被污染，就當它污染**。如果你把 join 改成 intersection（must），B4 的 IN 會是 `{'x'}`（B3 沒有 y），`z` 就不會 tainted → **漏掉**經由 B2 的真實污染。這一個字（union vs intersection）就是漏報的分水嶺。

## termination：為什麼一定會停，什麼時候不停

worklist 迭代會停，靠兩件事：

1. **lattice 高度有限**：事實只能單調往上（union 只加不減），且集合有上界（所有變數/def 是有限的）。
2. **monotone transfer**：transfer 不會讓事實往回跳。

兩者合起來 ⇒ 每個節點的 OUT 只能變有限次 ⇒ worklist 終究清空。上面 reaching definitions **5 次 pop 就收斂**，因為圖小、格矮。

**什麼時候不停？** 當 lattice **無限高**。經典例子：常數傳播想追蹤「變數的具體整數值」時，如果格是「所有整數的集合」而你在 loop 裡讓值一路加一，事實可以無限往上爬，永遠不收斂：

```
i = 0
while (...) { i = i + 1; }   // {0} → {0,1} → {0,1,2} → ... 不收斂
```

解法是 **widening（加寬）**：偵測到某個位置的事實一直在長，就一步跳到 `⊤`（「任何值」），犧牲精度換 termination。這是抽象解釋（abstract interpretation，見 `symex_taint`）的核心手法，也是為什麼很多工具對整數值域分析要嘛限制格的高度、要嘛上 widening。**審計工具超時或吃爆記憶體，十有八九是某個分析的格太高或沒 widening**。

## 踩雷集錦

**錯誤直覺：「dataflow 一定會收斂，不用管格的高度。」**
正確認識：收斂只在 lattice 高度有限時保證。追蹤具體整數值、無界字串、無界資料結構深度時，格可以無限高，迭代永不停。這時要 widening 或人為封頂（限制追蹤深度）。工具跑不完，先懷疑某個分析的格。

**錯誤直覺：「taint 用 must 分析比較精確，誤報少。」**
正確認識：方向反了。taint 追污染要 **may/union**——漏掉任一污染 path 就是漏報，而漏報在攻擊視角是致命的（你以為安全其實有洞）。must/intersection 是拿來判「一定被 sanitize」的，用在 sanitizer 判定，不是污染傳播。把兩者搞反，不是誤報變多就是真 bug 被吃掉。

**錯誤直覺：「sanitizer 只要有一條 path 做了就安全。」**
正確認識：那是 may 的判定，會漏掉沒 sanitize 的 path。「安全」是 must 命題——**每一條**到達 sink 的 path 都要 sanitize 才算。實務上工具把 sanitizer 建模成「切斷 taint 邊」，只有真的每條 path 都切斷，sink 才乾淨。只切一條 path 的 sanitizer 擋不住經由另一條 path 的污染。

**錯誤直覺：「worklist 每輪要把所有節點都重算一遍。」**
正確認識：worklist 的整個重點就是**只重算 OUT 有變的節點的後繼**（上面 code 的 `if newOUT!=OUT[b]` 才推後繼）。無腦每輪全掃是 round-robin，正確但慢。真實工具靠 worklist + 好的節點處理順序（reverse postorder）把迭代次數壓到接近線性。

**錯誤直覺：「fixpoint 迭代次數 = CFG 深度。」**
正確認識：迭代次數上界跟 **lattice 高度 × CFG 邊數** 有關，不是單純深度。格越高（追蹤的事實越豐富），越可能多迭代幾輪。這也是「精度越高越慢」的一個具體來源。

## 進階延伸

- **MFP vs MOP**：worklist 算的是 **MFP（Maximal Fixed Point，最大不動點解）**——在每個 merge 點先 join 再往下算。理論上更精確的是 **MOP（Meet Over all Paths，所有路徑之交/並）**——分別沿每條 path 算到底再合併。當 transfer 是 **distributive（可分配）** 時 MFP = MOP；不 distributive 時 MFP 是 MOP 的 sound 近似（可能較不精確）。**這個「distributive」條件正是 Ch 5 IFDS 能成立的關鍵**，記住它。
- **格的設計就是精度旋鈕**：taint 用 `{tainted, not}` 兩點格最粗；想區分「哪個 source 污染的」就把格換成「source 集合」；想追值域就上區間格（interval lattice）。格越豐富越精確，也越可能無限高、越慢。
- **flow-sensitive vs insensitive**：上面每個 program point 一個事實，是 flow-sensitive（考慮語句順序）。有些分析為了規模化做 flow-insensitive（整個函式一個事實），快但粗。points-to 分析（Ch 6）最常在這條軸上取捨。

## 本章重點整理

- dataflow 四零件：**CFG（在哪傳）、lattice（事實與合併）、transfer（怎麼變）、fixpoint（算到穩定）**。工具漏報/誤報/超時全能回到這裡。
- **may = union-join = 「可能」；must = intersection-join = 「一定」**。taint 傳播用 may（漏一條污染 path 就漏報），sanitizer 判定要 must（每條 path 都要 sanitize）。搞反 = 漏報或誤報。
- **forward 追污染（source→sink）、backward 追活性**。
- **taint 就是換了 lattice/transfer 的 dataflow**——迷你引擎同一個 worklist 核心，換 transfer 就從 reaching definitions 變 taint。
- termination 靠**格高度有限 + monotone**；格無限高（追具體整數/無界結構）要 **widening**，否則不收斂。

## 自我檢核

- 不看表，說出 may 與 must 各對應 union 還是 intersection、各對應「可能污染」還是「一定 sanitize」，以及把 taint 建成 must 會漏報還是誤報。
- 迷你引擎裡，把 taint 的 join 從 union 改成 intersection，B4 出口的 `z` 還會 tainted 嗎？為什麼這示範了「一個字決定漏報」？
- reaching definitions 的 B4 為什麼 IN 同時含 `y:d2` 和 `y:d3`？這跟 SSA 在 B4 插 φ 有什麼關係？
- 給一個會讓 dataflow 不收斂的程式片段，說出格為什麼無限高，以及 widening 怎麼救。
- 為什麼 worklist 只推「OUT 有變的節點的後繼」，這比 round-robin 好在哪？
- MFP 與 MOP 何時相等？這個條件的名字為什麼跟下一章有關？

## 延伸閱讀

- **Nielson, Nielson, Hankin, *Principles of Program Analysis*, ch. 1–2**——dataflow framework 與 lattice 的權威教科書。讀 monotone framework 與 MFP/MOP 那節，把本章直覺補成嚴謹推導。前提：本章 + 基本序理論。
- **`ssa_optimizations` Ch 7 資料流分析框架**（[連結](../../compilers/ssa_optimizations/07-dataflow-framework.md)）——你自己這門課的姊妹章，從編譯器優化角度推 Tarski 不動點與 worklist 正確性/終止性。對照著讀，兩個視角互補。前提：無。
- **Cousot & Cousot, *Abstract Interpretation: A Unified Lattice Model...*, POPL 1977**——widening/narrowing 與抽象解釋的原始論文，解釋「無限高的格怎麼還能收斂」。讀 widening operator 的定義那段。前提：本章 termination 一節。難但值得。
- **Møller & Schwartzbach, *Static Program Analysis* 講義（免費 PDF）**——線上免費、極好讀，dataflow 到 pointer analysis 一條龍，很多可跑的小例子。讀 monotone frameworks 與 worklist 章。前提：無。

我們現在會在**單一函式**內傳 dataflow。但真實漏洞幾乎都跨函式：source 在一個函式、sink 在另一個。暴力做會爆炸——下一章看 IFDS 怎麼把程序間 taint 化約成一張大圖上的可達性問題。

→ [Ch 5 IFDS/IDE](./05-ifds-ide.md)
