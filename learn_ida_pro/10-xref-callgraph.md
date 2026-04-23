# Ch 10 — Xref 與 call graph 分析

> 目標：從 sink（例如 `system`、`strcpy`）反推所有呼叫鏈、做 reachability 分析、把結果匯出成可讀報告。

## 為什麼要寫 script 而不是手按 X

手按 `X` 跳進一個 caller，再按 `X` 跳進它的 caller — 3 層之後你就迷路了，且瀏覽歷史沒辦法平行看到 10 條分支。

Script 做 **Reverse Call Graph BFS**：從 sink 開始廣度優先往外走，完整把「從 entry point 能不能到達這個 sink」的路徑列出來。這是 vuln research 的核心分析招式。

## 核心資料結構

兩個方向的 edge：

```
caller → callee     (CodeRefsFrom callee 收集不到，要看 XrefsTo(callee))
callee ← caller     (XrefsTo(callee) 給你所有 caller 的位址)
```

對一個 function 做 reverse BFS：

```
Start: sink_func
Level 0: sink_func
Level 1: 所有呼叫 sink_func 的 function
Level 2: 所有呼叫 Level 1 的 function
...
直到沒有新 function 加入
```

結果 = **所有能夠（直接或間接）呼叫到 sink 的 function**。

## 基礎版 reverse BFS

```python
import idautils
import ida_funcs, ida_name, ida_xref
import idaapi

def callers_of(func_ea):
    """回傳所有呼叫這個 function 的 caller function 起始位址"""
    out = set()
    for xref in idautils.XrefsTo(func_ea, 0):
        if xref.type not in (ida_xref.fl_CN, ida_xref.fl_CF):
            continue
        caller = ida_funcs.get_func(xref.frm)
        if caller:
            out.add(caller.start_ea)
    return out

def reverse_reachable(sink_ea, max_depth=10):
    """從 sink 往外 BFS，回傳 {func_ea: depth}"""
    visited = {sink_ea: 0}
    frontier = [sink_ea]
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        next_frontier = []
        for f in frontier:
            for c in callers_of(f):
                if c not in visited:
                    visited[c] = depth
                    next_frontier.append(c)
        frontier = next_frontier
    return visited

# 用法
target = ida_name.get_name_ea(idaapi.BADADDR, "strcpy")
reach = reverse_reachable(target)
for ea, depth in sorted(reach.items(), key=lambda kv: kv[1]):
    print(f"[depth={depth}] {ea:#x}  {ida_name.get_name(ea)}")
```

跑完你會看到：

```
[depth=0] 0x403000  strcpy
[depth=1] 0x401500  copy_arg
[depth=1] 0x401680  handle_setname
[depth=2] 0x401200  main
[depth=2] 0x401900  handle_config
...
```

Depth 是從 sink 往外算的跳數。愈大表示離 sink 愈遠。

## 加路徑資訊（不只名字，給完整 chain）

BFS 記 parent，最後就能回溯 path：

```python
def reverse_reachable_with_paths(sink_ea, max_depth=10):
    parent = {sink_ea: None}
    frontier = [sink_ea]
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        next_frontier = []
        for f in frontier:
            for c in callers_of(f):
                if c not in parent:
                    parent[c] = f
                    next_frontier.append(c)
        frontier = next_frontier
    return parent

def path_to_sink(parent, start_ea):
    path = []
    cur = start_ea
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    return path

# 用法
target = ida_name.get_name_ea(idaapi.BADADDR, "system")
parent = reverse_reachable_with_paths(target)

for caller_ea in parent:
    if caller_ea == target:
        continue
    path = path_to_sink(parent, caller_ea)
    names = " -> ".join(ida_name.get_name(ea) for ea in path)
    print(names)
```

輸出 like：

```
main -> handle_cmd -> exec_command -> system
daemon_loop -> process_request -> exec_command -> system
signal_handler -> exec_command -> system
```

每一行就是一條可達 sink 的完整呼叫鏈。

## 找真正外部可達的：過濾 entry points

有些 function 是內部 helper，不是任何人從外部能直接呼叫的。做 reachability 時要關心的是 **entry points** 到 sink 的路徑。

Entry point 通常指：
- `main` / `WinMain` / `DllMain` / `_start`
- Exported function（`.dynsym` 的 export）
- TLS callback
- Thread entry
- Signal handler / interrupt handler

```python
import ida_entry

def entry_functions():
    """所有 entry point function 的起始位址"""
    out = set()
    for i in range(ida_entry.get_entry_qty()):
        ord_ = ida_entry.get_entry_ordinal(i)
        ea = ida_entry.get_entry(ord_)
        f = ida_funcs.get_func(ea)
        if f:
            out.add(f.start_ea)
    return out

# 濾出 reachable 且是 entry point 的
entries = entry_functions()
target = ida_name.get_name_ea(idaapi.BADADDR, "system")
reach = reverse_reachable(target)

reachable_entries = entries & set(reach.keys())
print("External entry points that can reach system():")
for ea in reachable_entries:
    print(f"  {ea:#x}  {ida_name.get_name(ea)}")
```

這就是「哪些外部入口能觸發 command injection」的第一層答案。

## Indirect call 的盲區

`call [rax]` / `call qword ptr [rip + foo@GOT]` IDA 靜態看不出 target，`XrefsTo` 不會包含。這是 reverse BFS 的已知限制。

補救：

1. **已知 indirect call 的實際 target**：手動加 `ida_xref.add_cref(from_ea, to_ea, fl_CN)`。
2. **C++ vtable**：遇到 `call [reg+0x8]` 如果你已經把 vtable 當成 struct 還原了，可以看 struct 裡的 function pointer 反查。
3. **符號表還在（非 stripped）**：大部分 PLT 走 indirect call 但 IDA 會自己處理。

處理 indirect call 是獨立主題。先接受靜態分析的這個上限。

## 匯出成 DOT（給 Graphviz 畫圖）

```python
def export_callers_dot(sink_ea, out_path, max_depth=5):
    parent = reverse_reachable_with_paths(sink_ea, max_depth)
    with open(out_path, "w") as f:
        f.write("digraph callers {\n")
        f.write("  rankdir=LR;\n")
        f.write("  node [shape=box];\n")
        for caller_ea, callee_ea in parent.items():
            if callee_ea is None:
                continue
            src = ida_name.get_name(caller_ea) or f"sub_{caller_ea:X}"
            dst = ida_name.get_name(callee_ea) or f"sub_{callee_ea:X}"
            f.write(f'  "{src}" -> "{dst}";\n')
        f.write("}\n")

export_callers_dot(
    ida_name.get_name_ea(idaapi.BADADDR, "system"),
    "/tmp/callers.dot"
)
# 然後 shell: dot -Tpng /tmp/callers.dot -o callers.png
```

畫出來後截圖給別人一看就懂，比 text 報告更 pitch 力強。

## 多 sink 聯合分析

Real-world：你關心的不是單一 sink，是一組。

```python
SINK_NAMES = ["strcpy", "strcat", "sprintf", "gets", "system"]

all_reach = {}                                # ea -> set of sinks reachable

for sink_name in SINK_NAMES:
    ea = ida_name.get_name_ea(idaapi.BADADDR, sink_name)
    if ea == idaapi.BADADDR:
        continue
    reach = reverse_reachable(ea)
    for f_ea in reach:
        all_reach.setdefault(f_ea, set()).add(sink_name)

# 排序：可達 sink 最多的先列
by_danger = sorted(all_reach.items(), key=lambda kv: -len(kv[1]))
for ea, sinks in by_danger[:30]:
    name = ida_name.get_name(ea)
    sinks_str = ", ".join(sorted(sinks))
    print(f"{name:40s}  -> {sinks_str}")
```

一眼看出「哪些 function 一個就能碰到 5 個 sink」— 那些通常是 tainted data 的主要流通路徑。

## 去除 false positive：只算真的 call xref

`XrefsTo` 會包含各種 xref type：call、jump、data ref。只要 call 的話：

```python
for xref in idautils.XrefsTo(ea, 0):
    if xref.type in (ida_xref.fl_CN, ida_xref.fl_CF):
        ...
```

**jump 也算 caller 嗎？** 有時是，有時不是。例如 tail call 優化 `jmp target` 本質是呼叫，但 PLT 也用 `jmp`。實務上把 `fl_JN` / `fl_JF` 也包進來更完整，代價是某些 local control flow 會被誤算。

## 常見踩雷

- **BFS 無限**：如果有 recursion（function 呼叫自己），visited set 會終止它；但要注意 parent 會指向自己，path 回溯要 break。
- **`ida_entry` 拿不到你以為的 entry**：它只認 binary loader 標出來的 entry point。TLS callback 在 PE 要額外處理。
- **名字衝突**：`strcpy` 可能是 import，也可能 binary 自己有 static function 叫 `strcpy`。`get_name_ea` 回第一個找到的。
- **太深的 BFS**：某些 utility function 被整個 codebase 呼叫，reach set 會是幾百個。設 `max_depth` 保護。

## 動手練習

1. 對你手邊任何 binary，挑一個 `free` 的 xref chain 跑 reverse BFS，看結果合不合理。
2. 改腳本：排除 depth > 5 的節點，避免 hop 太遠的 function 影響。
3. 加 function size 的 metadata：`f.size()`，超過 2000 bytes 的標出來（通常是 main / dispatcher）。
4. 做一個「entry point 到 sink 的最短路徑」版：BFS 到 sink 時就停，每個 entry 只找最短的一條。

## 自我檢核

- [ ] 能寫 reverse BFS 的 callers_of 與主迴圈
- [ ] 能還原從 sink 到 caller 的完整 path
- [ ] 知道 `ida_entry` 可以拿 entry point
- [ ] 知道 indirect call 是靜態分析盲區
- [ ] 能匯出 DOT 檔

下一章把自動化能力拉到 struct 推斷 — 從存取 pattern 自動還原 struct layout。

→ [Ch 11 Struct 自動推斷腳本](./11-struct-auto-recovery.md)
