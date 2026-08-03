# 練習 A — 手刻 mini taint tracker

> **目標**：把 Ch 4-7 拼成一個能跑的東西。你用 **Python** 在一個小 CFG 上實作一個 **intra-procedural taint tracker**，跑 fixpoint、處理 source/sink/sanitizer、報告哪些 sink 收到 tainted 值與 taint 路徑。做完你會真的懂：taint 就是換了 lattice/transfer 的 dataflow（Ch 4）、may-join 為什麼決定漏報（Ch 4）、sanitizer 怎麼切斷邊（Ch 7）——這些不再是讀來的，是你寫出來、跑過、看它報對報錯的。

這是 Part 1 的收尾練習。不碰 inter-procedural（那要 IFDS summary，Ch 5 的完整版留給進階挑戰）、不碰 alias（Ch 6，留給進階）。**先把單函式內的 taint 骨架刻對**，這副骨架 Semgrep（Ch 14）、CodeQL（Ch 22）底層是一樣的，只是它們跨函式、有 alias、有更豐富的 propagation。

## 任務規格

### 輸入格式

一個 CFG，用 list of dict 表示。每個節點一條 statement：

```python
{"id": "n0", "stmt": (...), "succ": ["n1", ...]}
```

`stmt` 是一個 tuple，種類如下（這是你的迷你 IR）：

| stmt | 語意 | taint 行為 |
|---|---|---|
| `("source", "x")` | `x = source()` | `x` 變 tainted |
| `("assign", "y", "x")` | `y = x` | `x` tainted 則 `y` tainted，否則 `y` clean |
| `("binop", "z", ["a","b"])` | `z = a op b` | 任一運算元 tainted 則 `z` tainted |
| `("sanitize", "w", "v")` | `w = sanitize(v)` | `w` 一律 clean（切斷 taint） |
| `("const", "c")` | `c = 0` | `c` clean |
| `("sink", "system", "x")` | `system(x)` | 檢查 `x` 是否 tainted |
| `("nop",)` | 分支點/占位 | 不改 taint |

### 輸出格式與驗收標準

程式要輸出兩樣東西：

1. **每個節點的 `IN` / `OUT` tainted 變數集合**（讓你檢查 fixpoint 算對了）。
2. **findings**：每個「收到 tainted 值」的 sink，報出 `(節點 id, sink 名, 變數名)`，並附**一條** source→sink 的 taint 路徑。沒有任何 tainted 到 sink 就報「安全」。

**驗收標準**（下面四組測試各自的預期）：

| 測試 | 情境 | 預期 |
|---|---|---|
| 1 直接流 | `x=source(); y=x; system(y)` | **報** `system` 收到 `y` |
| 2 sanitizer 阻斷 | `x=source(); y=sanitize(x); system(y)` | **不報**（安全） |
| 3 分支 join | 一條 path `y=x`（髒）、另一條 `y=0`（乾淨），匯流後 `system(y)` | **報**（may-join：有一條髒就報） |
| 4 假 sanitizer | 一條 path sanitize、另一條**沒有**，匯流後 `system(y)` | **報**（沒 sanitize 的 path 讓 `y` 仍髒） |

測試 3、4 是**邊界**：它們考的是你有沒有用 **may / union-join**（Ch 4）。如果你不小心用了 intersection，測試 3 會漏報——這正是 Ch 4 說「一個字決定漏報」的地方。測試 4 更狠：它示範「sanitizer 只擋部分 path 等於沒擋」，這是 Ch 7 sanitizer 建模不當導致漏報的具體長相。

## 分五步實作

1. **建 preds**：從每個節點的 `succ` 反推 `preds`（誰是我的前驅）。join 要靠它。
2. **寫 transfer function**：吃 `(stmt, IN 集合)`，回傳 `OUT` 集合。逐 stmt 種類實作上表的 taint 行為。**關鍵**：`assign` 要處理「src 不髒時 dst 要被 clear」（否則 dst 舊的 taint 殘留 → 錯）；`sanitize` 無論 src 髒不髒，dst 都 clear。
3. **fixpoint（worklist）**：抄 Ch 4 的 worklist 骨架。`IN[n] = ∪ OUT[preds]`（**union-join = may**），`OUT[n] = transfer(stmt, IN[n])`，只在 `OUT` 有變時把後繼推回 worklist。
4. **收 findings**：跑完後掃每個 `sink` 節點，看它用的變數在不在該節點的 `IN` 裡。
5. **報 taint 路徑**：從 sink 反向走，追「哪個變數把 taint 帶進來」，經 `assign`/`binop` 換追蹤的變數名，直到碰到 `source`。取一條即可（不必列全部）。

## 如果你卡住了

- **測試 3 漏報**：你的 join 用成 intersection 了。`IN[n] = ∪ OUT[preds]`，是 union，不是 `&`。這正是練習要你踩的坑。
- **`assign` 後 dst 一直髒不掉**：transfer 裡 `assign` 要在 src 不髒時 `discard(dst)`，不能只加不減。想想 `y=x`（x 乾淨）之後 y 應該乾淨。
- **fixpoint 不收斂/漏算節點**：確認「只有 `OUT` 真的變了才把後繼推回 worklist」，且初始 worklist 放了所有節點。分支節點（`nop`）別忘了它有兩個 succ。
- **路徑追到一半斷掉**：反向走時，遇 `assign y x` 要把「追蹤目標」從 `y` 換成 `x`（taint 的真正來源），否則你在找 `y` 的 source 會找不到。
- **sanitizer 沒切斷**：`sanitize` 的 transfer 是無條件 `discard(dst)`，別寫成「src 乾淨才 clean」——sanitize 的意義就是不管輸入多髒，輸出都乾淨。

## 參考解答

真跑過（Python 3.12，無外部依賴）。

<details>
<summary>點開看完整參考解答 + 真實輸出</summary>

```python
from collections import defaultdict

def build_preds(cfg):
    preds = defaultdict(list)
    for n in cfg:
        for s in n["succ"]:
            preds[s].append(n["id"])
    return preds

def transfer(stmt, tin):
    t = set(tin); k = stmt[0]
    if k == "source":            t.add(stmt[1])
    elif k == "assign":
        _, dst, src = stmt
        (t.add if src in t else t.discard)(dst)      # src 乾淨 -> dst 也清掉
    elif k == "binop":
        _, dst, srcs = stmt
        (t.add if any(s in t for s in srcs) else t.discard)(dst)
    elif k == "sanitize":        t.discard(stmt[1])  # 無條件 clean（切斷 taint）
    elif k == "const":           t.discard(stmt[1])
    return t                     # sink / nop 不改

def analyze(cfg):
    node  = {n["id"]: n for n in cfg}
    preds = build_preds(cfg)
    IN  = {n["id"]: set() for n in cfg}
    OUT = {n["id"]: set() for n in cfg}
    worklist = [n["id"] for n in cfg]
    while worklist:
        nid = worklist.pop(0)
        # may / union join —— 有一條前驅髒，這裡就髒
        IN[nid] = set().union(*[OUT[p] for p in preds[nid]]) if preds[nid] else set()
        newout = transfer(node[nid]["stmt"], IN[nid])
        if newout != OUT[nid]:                        # 只在變了才推後繼
            OUT[nid] = newout
            for s in node[nid]["succ"]:
                if s not in worklist: worklist.append(s)
    findings = []
    for n in cfg:
        st = n["stmt"]
        if st[0] == "sink" and st[2] in IN[n["id"]]:
            findings.append((n["id"], st[1], st[2]))
    return IN, OUT, findings

def taint_path(cfg, sink_id, var):
    """反向走一條把 var 帶髒的 source->sink 路徑（取其中一條）。"""
    node = {n["id"]: n for n in cfg}
    preds = build_preds(cfg)
    IN, OUT, _ = analyze(cfg)
    path = [sink_id]; cur = sink_id; watch = var; guard = 0
    while guard < len(cfg) + 1:
        guard += 1
        st = node[cur]["stmt"]
        if st[0] == "source" and st[1] == watch:
            break
        if st[0] == "assign" and st[1] == watch:
            watch = st[2]                    # 追回真正的 taint 來源變數
        elif st[0] == "binop" and st[1] == watch:
            for s in st[2]:
                if s in IN[cur]: watch = s; break
        nxt = next((p for p in preds[cur]
                    if watch in OUT[p] or
                    (node[p]["stmt"][0]=="source" and node[p]["stmt"][1]==watch)), None)
        if nxt is None: break
        path.append(nxt); cur = nxt
    return path

def run(name, cfg):
    print(f"\n===== 測試：{name} =====")
    IN, OUT, findings = analyze(cfg)
    for n in cfg:
        print(f"  {n['id']:4s} {str(n['stmt']):32s} IN={sorted(IN[n['id']])} OUT={sorted(OUT[n['id']])}")
    for sid, sname, var in findings:
        print(f"  >> 漏洞：{sname} @ {sid} 收到 tainted '{var}'，路徑 {list(reversed(taint_path(cfg,sid,var)))}")
    if not findings:
        print("  >> 無 tainted 抵達任何 sink（安全）")

t1 = [{"id":"n0","stmt":("source","x"),"succ":["n1"]},
      {"id":"n1","stmt":("assign","y","x"),"succ":["n2"]},
      {"id":"n2","stmt":("sink","system","y"),"succ":[]}]
t2 = [{"id":"n0","stmt":("source","x"),"succ":["n1"]},
      {"id":"n1","stmt":("sanitize","y","x"),"succ":["n2"]},
      {"id":"n2","stmt":("sink","system","y"),"succ":[]}]
t3 = [{"id":"n0","stmt":("source","x"),"succ":["n1"]},
      {"id":"n1","stmt":("nop",),"succ":["n2","n3"]},
      {"id":"n2","stmt":("assign","y","x"),"succ":["n4"]},
      {"id":"n3","stmt":("const","y"),"succ":["n4"]},
      {"id":"n4","stmt":("sink","system","y"),"succ":[]}]
t4 = [{"id":"n0","stmt":("source","x"),"succ":["n1"]},
      {"id":"n1","stmt":("nop",),"succ":["n2","n3"]},
      {"id":"n2","stmt":("sanitize","y","x"),"succ":["n4"]},
      {"id":"n3","stmt":("assign","y","x"),"succ":["n4"]},
      {"id":"n4","stmt":("sink","system","y"),"succ":[]}]

run("直接流", t1)
run("經 sanitizer 阻斷", t2)
run("分支 join（may 應報）", t3)
run("假 sanitizer（只擋一條 path，另一條漏）", t4)
```

真跑輸出（照貼）：

```
===== 測試：直接流 =====
  n0   ('source', 'x')                  IN=[] OUT=['x']
  n1   ('assign', 'y', 'x')             IN=['x'] OUT=['x', 'y']
  n2   ('sink', 'system', 'y')          IN=['x', 'y'] OUT=['x', 'y']
  >> 漏洞：system @ n2 收到 tainted 'y'，路徑 ['n0', 'n1', 'n2']

===== 測試：經 sanitizer 阻斷 =====
  n0   ('source', 'x')                  IN=[] OUT=['x']
  n1   ('sanitize', 'y', 'x')           IN=['x'] OUT=['x']
  n2   ('sink', 'system', 'y')          IN=['x'] OUT=['x']
  >> 無 tainted 抵達任何 sink（安全）

===== 測試：分支 join（may 應報） =====
  n0   ('source', 'x')                  IN=[] OUT=['x']
  n1   ('nop',)                         IN=['x'] OUT=['x']
  n2   ('assign', 'y', 'x')             IN=['x'] OUT=['x', 'y']
  n3   ('const', 'y')                   IN=['x'] OUT=['x']
  n4   ('sink', 'system', 'y')          IN=['x', 'y'] OUT=['x', 'y']
  >> 漏洞：system @ n4 收到 tainted 'y'，路徑 ['n0', 'n1', 'n2', 'n4']

===== 測試：假 sanitizer（只擋一條 path，另一條漏） =====
  n0   ('source', 'x')                  IN=[] OUT=['x']
  n1   ('nop',)                         IN=['x'] OUT=['x']
  n2   ('sanitize', 'y', 'x')           IN=['x'] OUT=['x']
  n3   ('assign', 'y', 'x')             IN=['x'] OUT=['x', 'y']
  n4   ('sink', 'system', 'y')          IN=['x', 'y'] OUT=['x', 'y']
  >> 漏洞：system @ n4 收到 tainted 'y'，路徑 ['n0', 'n1', 'n3', 'n4']
```

盯著兩個邊界測試：

- **測試 3**：`n4` 的 `IN` 用 union-join 收 `n2`（有 `y`）與 `n3`（無 `y`），於是 `y` 進來 → 報。路徑 `n0→n1→n2→n4` 走的正是那條髒 path。**這就是 may 分析的態度：有一條 path 髒就報**。改成 intersection 這裡會漏。
- **測試 4**：`n3` 那條沒 sanitize，`y` 保持髒，union-join 讓它到 `n4` → 報。路徑 `n0→n1→n3→n4` 精確指出「繞過 sanitizer 的那條 path」。**這示範了 sanitizer 只擋部分 path 等於沒擋**（Ch 7）——一個真實審計裡極常見的漏洞成因。

</details>

## 延伸挑戰

做完基礎版，任選一個往 Part 2/3 的方向延伸：

- **field-sensitivity（接 Ch 6）**：把變數換成 `("var", "field")` 這種 access path，讓 `s.tainted` 髒不會污染 `s.safe`。你會親手體會 field-insensitive 的誤報從哪來。
- **簡單 inter-procedural summary（接 Ch 5）**：加一種 `("call", dst, fn, [args])` stmt，先對被呼叫函式算一條 summary（「哪個參數髒 ⟹ 回傳髒」），再讓所有 caller 複用。這就是你手刻一個迷你 summary edge。
- **implicit flow（接 Ch 7）**：加一個 tainted 的 program-counter，進入「條件是 tainted 變數」的 branch 時把 pc 染色，branch 內的賦值繼承 pc 的 taint。跑一段 code 感受它為什麼會誤報爆炸——這是你親眼看到「為什麼工具刻意忽略 implicit flow」。

這三個延伸各對應 Part 1 的一章，做完等於把 Ch 5/6/7 的理論用手驗證了一遍。

## 本練習你該帶走的

- taint 就是 dataflow 的一個 instance——你用了跟 reaching definitions **同一個 worklist 核心**（Ch 4），只換了 lattice（tainted 變數集合）與 transfer。
- **may / union-join 是漏報的分水嶺**：測試 3/4 用手驗證了「有一條 path 髒就報」，換 intersection 就漏。
- **sanitizer 只擋部分 path = 沒擋**（測試 4），這是真實審計最常見的漏洞成因之一，也是 Ch 7「假 sanitizer」的動手版。
- 你刻的是 intra-procedural；真實工具的差別在跨函式（IFDS summary，Ch 5）、間接邊（points-to，Ch 6）、更豐富的 policy——但骨架你已經有了。

理論與骨架備齊。Part 2 開始把「找漏洞」形式化：不再問「怎麼算 flow」，而是問「我要找的 flow 的 source/sink/sanitizer 到底是什麼」——攻擊面建模的思維。

→ [Ch 9 source/sink/sanitizer 思維](./09-source-sink-sanitizer.md)
