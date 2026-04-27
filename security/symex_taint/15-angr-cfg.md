# Ch 15 — CFGFast vs CFGEmulated

> 目標：解釋 angr 兩種 CFG recovery algorithm 的原理與取捨。講完你應該知道，對一個不熟悉的 binary，先上哪個、發現 CFG 不完整時怎麼救。

## 為什麼 binary CFG 很難

C source 你有 AST，哪裡是 function、哪裡是 branch、哪個 indirect call 到哪，全寫在那。

Binary 呢？你有一堆 machine instruction。以下這些需要 CFG 才答得出來：

- 這個 binary 有幾個 function？
- 這個 function 的 entry、exit 在哪？
- `call rax`（indirect call）這個點可能跳到哪些 function？
- `jmp [rax*8 + 0x400200]`（jump table）展開是什麼？

Binary CFG recovery 是 reverse engineering 的基礎難題。有整本書 `SoK: Binary Disassembly` 在討論。angr 的 CFG 兩種實作代表光譜兩端：

- **CFGFast**：靜態分析，快但不完整
- **CFGEmulated**：symbolic execution，慢但精確

## CFGFast：linear sweep + recursive descent 混合

基本思路：
1. 從 entry point 開始，沿 instruction sequence 往下走（recursive descent）
2. 遇到 `call`：記下 target、分出新 function
3. 遇到 `jmp`：如果是 direct jmp，就跟去；indirect jmp 就**猜**
4. 遇到 `ret`：function 結束
5. 掃完主 path 後，linear sweep 剩下的 section（掃 byte sequence 當 instruction）

```python
cfg = proj.analyses.CFGFast()
print(len(cfg.functions))    # 有幾個 function
print(cfg.graph.nodes())     # CFG node
```

跑 `/bin/ls`（約 100 KB binary）大概 **3–10 秒**。

### 弱點

indirect call / jump 幾乎處理不了。**jump table**（switch-case 編譯後的結構）也經常搞錯。

```
jmp [rax*8 + table]
```

CFGFast 可能：
- 完全略過（把這當 unknown destination）
- 用簡單啟發法猜 table 大小（常見是 `mov eax, [rbx+off]; jmp [.L+eax*8]` 這種 pattern match）

對有 heavy C++ 的 binary（一堆 virtual call）、或 obfuscated binary，CFGFast 的 CFG 會殘缺。

### 優點

- **快**：上面提過，幾秒
- **不會爆**：不 fork、不 symex，memory 可控
- **對一般 target 夠用**：GCC 編的 C binary、簡單的 C++ 無 virtual 的，CFGFast 拿 80–95% coverage

**預設先用 CFGFast**。

## CFGEmulated：symex 驅動的 CFG recovery

基本思路：**跑 symex，每到達新 basic block 就記下**。

```python
cfg = proj.analyses.CFGEmulated(
    starts=[proj.entry],
    context_sensitivity_level=2,
    call_depth=5,
)
```

它會：
1. 從 `starts` 開始 symex
2. 每個 basic block 的 successor 都記錄（包括 indirect jmp 的 symbolic target 經過 SMT 解出來的可能值）
3. 遇到 function call 就 symex 進去
4. 遇到 `ret` 時回到 caller 的下一個 instruction

### 優點

- **精確**：indirect call 真的 solve 出 target，jump table 自動展開
- **context-sensitive**：知道「這個 function 是從 caller A 被呼叫」跟「從 caller B 被呼叫」時，callee 的 state 可能不同
- 對 obfuscated / heavy indirect 的 binary 比 CFGFast 強很多

### 弱點

- **慢**：真的跑 symex，同樣 100 KB binary 要幾分鐘到幾小時
- **容易 OOM**：symex 的 path explosion 也影響 CFG recovery
- **Loop 要設 bound**：跟 symex 的 loop 問題一樣
- **對 imports 處理複雜**：libc 的 call 要 hook 掉（不然會 symex 進整個 libc）

### 參數調教

- `context_sensitivity_level`：function 的 context 區分度。0 = insensitive、2 = 兩層 caller。越高越精確、越慢
- `call_depth`：最多 step into 幾層 function。5 是平衡
- `keep_state`：是否保留 symex state（會吃爆 memory）

## 什麼時候要 CFGEmulated

Default workflow：先 CFGFast 看看覆蓋率，如果：

- function 數明顯少於預期（你看 `nm` 或 `readelf` 有 200 個 symbol、CFGFast 只找到 30 個）
- 已知有 virtual call / jump table 沒展開
- 打算做 static analysis（VSA、DDG）需要完整 CFG

再上 CFGEmulated。

對一個 **1 MB 以上的 binary**，CFGEmulated 通常不 practical — 卡在 symex 的爆炸。此時要：
- 限制 `starts` 只從你關心的 function 開始
- 限 `call_depth` 淺一點
- 或配合手動 hook 掉無關 function

## API 差異的一些陷阱

```python
# CFGFast
cfg = proj.analyses.CFGFast()
cfg.functions         # function dict
cfg.graph             # nx DiGraph
cfg.get_any_node(addr)  # 給 address 找 CFGNode

# CFGEmulated
cfg = proj.analyses.CFGEmulated()
cfg.functions         # 同
cfg.graph             # 同
cfg.get_any_node(addr)  # 有 simprocs=True 參數，處理 hook
cfg.get_all_nodes(addr) # 同 addr 可能有多個 node（context-sensitive）
```

CFGEmulated 的 node 可能**同一個 address 有多個**（不同 caller context），寫 code 要注意。

## 實例：對一個 crackme 跑 CFG

```python
import angr, logging
logging.getLogger('angr').setLevel('ERROR')

proj = angr.Project('./target', auto_load_libs=False)

# CFGFast
cfg_fast = proj.analyses.CFGFast()
print(f"CFGFast: {len(cfg_fast.functions)} functions, "
      f"{len(cfg_fast.graph.nodes())} blocks")

# CFGEmulated
cfg_emu = proj.analyses.CFGEmulated(call_depth=5)
print(f"CFGEmulated: {len(cfg_emu.functions)} functions, "
      f"{len(cfg_emu.graph.nodes())} blocks")
```

典型對照：

```
CFGFast: 134 functions, 2891 blocks     (2.3 秒)
CFGEmulated: 142 functions, 3020 blocks (87 秒)
```

CFGEmulated 多找到 8 個 function — 通常是 indirect call 才看得到的。差不多就這個量級。

## 實用技巧：CFGFast + 手動補

通常生產用法：

```python
cfg = proj.analyses.CFGFast(
    normalize=True,
    force_complete_scan=True,   # 對未掃到的 section linear sweep
    resolve_indirect_jumps=True,  # 嘗試解 jmp table
    indirect_jump_target_limit=200,
)
```

幾個關鍵 flag：

- `force_complete_scan`：對沒走到的 code section 做 linear sweep。會找到更多 function、但有 false positive
- `resolve_indirect_jumps`：用 VSA / pattern match 嘗試解 indirect jmp
- `data_references`：順便建 cross-reference table
- `cross_references`：同上

調完大部分 target 夠用，不用 CFGEmulated。

## 看單一 function 細節

```python
func = cfg.functions['main']
# 或
func = cfg.functions[0x401000]

func.graph        # function 自己的 CFG (子圖)
func.blocks       # basic blocks
func.callees      # 它呼叫的 function
func.callers      # 呼叫它的 function

# 反組譯一個 block
block = proj.factory.block(func.addr)
block.pp()
# 0x401000  push rbp
# 0x401001  mov rbp, rsp
# ...
```

angr-management（GUI）基本上是把這些 API 用 Qt 畫出來。

## 可選：用 Ghidra / IDA 的 CFG 餵 angr

angr 自帶 CFG 不夠用時，可以**用別的工具的 CFG 結果**。

- **Ghidra**：export CFG 為 Python script，feed 進 angr
- **IDA Pro**：用 IDA 的 API 匯出，用 angr 讀
- **BinaryNinja**：`angr-binaryninja` bridge

這在做大型 binary 的 advanced RE 時是常見做法 — 人手審過的 CFG 比 angr 自動recovered 準多了。

## 心法

CFG 是 binary symex 的前置作業。**CFG 錯，symex 跑的路徑就錯**。

實務順序：
1. 先 CFGFast，看 coverage
2. 不夠 → CFGEmulated，對主要 function
3. 還不夠 → 手動 hook symbol、指定 entry
4. 仍不夠 → 用 Ghidra CFG 取代 angr 的

對 obfuscated binary（Malware）：**幾乎永遠要 CFGEmulated + 手動**。自動 CFG 會被 obfuscation 騙到。

## 自我檢核

- [ ] 解釋 CFGFast 跟 CFGEmulated 的演算法本質差別
- [ ] 知道兩者各自的優勢情境
- [ ] 會用 `context_sensitivity_level`、`call_depth` 調 CFGEmulated
- [ ] 知道哪些 flag 讓 CFGFast 變更準（`force_complete_scan`, `resolve_indirect_jumps`）
- [ ] 能在 `cfg.functions` 與 `cfg.graph` 之間切換

下一章切到 angr 的 **simulation manager** — `.explore()` 背後做了什麼、各種 ExplorationTechnique 什麼時候該用。

→ [Ch 16 — Simulation manager 與 exploration techniques](./16-angr-simmanager.md)
