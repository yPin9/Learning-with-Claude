# Ch 16 — Simulation manager 與 exploration techniques

> 目標：把 `.explore()` 拆開看。講完你要能自己組 custom exploration 而不是盲調 `find=` 跟 `avoid=`。

## SimulationManager (simgr) 的 stash model

simgr 是 angr 的 path scheduler。內部用 **stash** 管理 state：

```
simgr.active       # 正在跑的 states（default stash）
simgr.deadended    # 跑完了的 states（halt / exited）
simgr.errored      # 出錯的 states
simgr.unconstrained # PC 變 symbolic 的 states（走飛了）
simgr.unsat        # 發現 PC unsat 被丟的 states
simgr.found        # explore() 找到 target 的 states
simgr.avoid        # 被 avoid 命中的 states
simgr.<custom>     # 你自己命名的 stash
```

所有 API 圍繞 stash：

```python
simgr.step()                    # active stash 各推一步
simgr.step(num_inst=10)         # 推 10 個 instruction
simgr.move(from='active', to='found', filter_func=lambda s: condition)
simgr.drop(stash='errored')     # 丟掉
simgr.explore(find=addr)        # 自動 step + move
```

`.explore(find=X, avoid=Y)` 其實是個 shortcut：while active 非空 && 沒找到足夠的 found，`step()` + 檢查每個 active state 是否命中 find/avoid。

## .explore 的語意

```python
simgr.explore(
    find=0x400800,                     # target PC
    avoid=[0x400900, 0x400a00],        # 不要碰
    find_stash='found',
    avoid_stash='avoid',
    num_find=1,                        # 找到幾條就停
    n=None,                            # 最多 step 幾次（None = 無限）
)
```

find / avoid 可以是：
- 一個 address
- 一個 list of addresses
- 一個 lambda：`lambda state: condition`

最常見 patterns：

```python
# "最後印出 Good job"
simgr.explore(find=lambda s: b"Good job" in s.posix.dumps(1))

# "執行到 main 的 ret"
simgr.explore(find=proj.symbol('main').rebased_addr + func_size - 1)

# avoid 一個 error log
simgr.explore(find=..., avoid=lambda s: b"wrong" in s.posix.dumps(1))
```

## ExplorationTechnique：可插拔的策略

explore 預設是 BFS（對 active stash 所有 state 同時 step）。換策略用 `use_technique`：

```python
simgr.use_technique(angr.exploration_techniques.DFS())
simgr.use_technique(angr.exploration_techniques.LengthLimiter(max_length=100))
```

內建一些常用的：

### DFS

```python
simgr.use_technique(angr.exploration_techniques.DFS())
```

每次只 step 一個 state（其他放 `deferred` stash），走到 deadend 才回來。適合 path 長但分岔不多的題目。

### LengthLimiter

限制每條 state 最大 step 數，避免 infinite loop：

```python
simgr.use_technique(angr.exploration_techniques.LengthLimiter(max_length=1000))
```

### LoopSeer

偵測 loop、判斷迭代次數：

```python
simgr.use_technique(angr.exploration_techniques.LoopSeer(
    cfg=cfg, bound=10, limit_concrete_loops=False))
```

當某個 loop 被某條 state 跑超過 10 次，這條 state 被丟到 `spinning` stash。

### Veritesting

Ch 8 提過 CMU 的 Veritesting（靜態 formula encode）：

```python
simgr.use_technique(angr.exploration_techniques.Veritesting())
```

對 loop-free diamond region 的 path merging。某些題目速度飆升。

### Driller

Ch 25 會細講。這是 hybrid fuzzing 的 technique：

```python
# Driller 自己做了個獨立的 AFL 互動，這裡不貼完整 setup
```

簡單說：整合 AFL 的 corpus，當 AFL stuck 時用 symex 產生新 seed 再餵回去。

### Explorer (v.s. explore)

`explore()` 的 find/avoid 其實就是 `Explorer` technique 的封裝：

```python
simgr.use_technique(angr.exploration_techniques.Explorer(
    find=0x400800, avoid=[0x400900],
    find_stash='found', avoid_stash='avoid'))
# 之後 simgr.run() 會把 target state 收到 found
```

這讓你可以跟其他 technique 組合。

### Threading

```python
simgr.use_technique(angr.exploration_techniques.Threading(threads=4))
```

多執行緒 symex。**效果有限** — Python GIL 限制、SMT call 是 native 但狀態是 Python，同步成本高。**通常不推薦**。

### Memory limit

```python
simgr.use_technique(angr.exploration_techniques.MemoryWatcher(min_memory=1024))
```

每次 step 檢查 memory，低於 1024 MB 可用時 drop 一部分 state。

## 自己寫 ExplorationTechnique

想 customize scheduler？實作 technique：

```python
class MyTechnique(angr.ExplorationTechnique):
    def setup(self, simgr):
        simgr.stashes['interesting'] = []
    
    def step_state(self, simgr, state, **kwargs):
        # 對單一 state 推一步
        successors = simgr.step_state(state, **kwargs)
        # 修改 successors 的分流邏輯
        for active in successors.get('active', []):
            if is_interesting(active):
                successors.setdefault('interesting', []).append(active)
        return successors
    
    def step(self, simgr, stash='active', **kwargs):
        # 整個 stash 推一步
        return simgr.step(stash=stash, **kwargs)
```

你需要這個時，通常是要做 coverage-guided symex、或整合外部 fuzzer。

## 實戰：抓 state 探索過程

Debug 時常要看 state 走到哪。打 step log：

```python
while simgr.active:
    simgr.step()
    print(f"active={len(simgr.active)}, "
          f"found={len(simgr.found)}, "
          f"deadended={len(simgr.deadended)}, "
          f"errored={len(simgr.errored)}")
    for s in simgr.active:
        print(f"  PC={hex(s.addr)}, constraints={len(s.solver.constraints)}")
```

輸出類似：

```
active=2, found=0, deadended=0, errored=0
  PC=0x400550, constraints=1
  PC=0x400580, constraints=1
active=3, found=0, deadended=0, errored=0
  PC=0x4005a0, constraints=2
  ...
```

你可以**看到 path tree 實際怎麼分岔**。

## 階段控制：pre + explore + post

典型 angr solver script 三段：

```python
# 1. Pre: 建 state、設定 symbolic input、加 constraint
state = proj.factory.entry_state()
flag = claripy.BVS('flag', 8 * 20)
state.memory.store(flag_addr, flag)
for byte in flag.chop(8):
    state.solver.add(claripy.And(byte >= 0x20, byte < 0x7f))  # printable

# 2. Explore
simgr = proj.factory.simulation_manager(state)
simgr.use_technique(angr.exploration_techniques.LengthLimiter(500))
simgr.explore(find=0x400800, avoid=0x400700)

# 3. Post: 從 found state 拿 solution
if simgr.found:
    sol = simgr.found[0]
    print(sol.solver.eval(flag, cast_to=bytes))
```

每一段都有陷阱：
- **Pre**：constraint 加得不夠 → input space 太大；加太緊 → 根本解不出來
- **Explore**：選錯 technique → 爆炸或走不到
- **Post**：只 eval 一個解可能不是你要的（另一個解也 satisfies、但沒意義）。用 `eval_upto(x, 10)` 看多個可能

## 觀察點：inspect breakpoint

angr 提供 SimInspect — 在 symex 過程中 hook 事件：

```python
def on_mem_read(state):
    addr = state.inspect.mem_read_address
    print(f"read at {state.addr}: addr={addr}")

state.inspect.b('mem_read', when=angr.BP_BEFORE, action=on_mem_read)
```

事件類型：
- `instruction` — 每條 instruction
- `mem_read`、`mem_write`
- `call`、`return`
- `constraints` — 每次加 constraint
- `symbolic_variable` — 新 symbolic 變數
- `exit` — basic block 結束
- `fork` — state fork

這是 debug symex 卡住的 power tool。看 constraint 怎麼加、看 memory access pattern 異常在哪，全靠它。

## Unicorn 加速

angr 可以在 concrete-heavy 的區塊切到 Unicorn engine，繞過 VEX：

```python
state.options.add(angr.options.UNICORN)
state.options.add(angr.options.UNICORN_SYM_REGS_SUPPORT)
```

加速有時 **10–100×**。但：
- 當 state 有 symbolic 時自動 fallback 回 VEX
- Unicorn 的 CPU model 有邊緣 bug（不同於真實 CPU）
- 不支援所有 option（SimProcedure 在 Unicorn 中不跑）

**建議**：想加速就試試開 UNICORN；結果對不上或 symex 走偏就關掉。

## 常見誤用

### 誤用 1：`explore()` 沒限制就 run

```python
simgr.explore(find=0x400800)    # 跑到天荒地老
```

你以為它會很快，但如果 target 路徑深 / 分岔多，可能卡幾小時。**永遠加 LengthLimiter / LoopSeer**。

### 誤用 2：`find=lambda s: condition` 裡呼叫 slow 函式

```python
simgr.explore(find=lambda s: some_slow_check(s))
```

這個 lambda 每次 step 對所有 active state 呼叫。slow 函式會把 explore 拖到極慢。

對策：把 check 做在 stash move 時、或 hook 在特定 address。

### 誤用 3：忘記 `num_find`

```python
simgr.explore(find=0x400800)    # 預設 num_find=1，找一條就停
```

你以為會全部探索完。其實找到一條就停、其他 potential 解都漏。加 `num_find=10` 或 `avoid=` 清楚列。

### 誤用 4：忽略 `unconstrained` stash

某些情況 symex 推到 `pc` 變 symbolic（典型：函式 return 時 rsp 被污染）。這些 state 進 `unconstrained` stash、你不理他就永遠拿不到。

對 PWN 這類題，unconstrained 反而是**好消息**（你能控 PC → exploit 機會）。`state.options.add(angr.options.UNCONSTRAINED_STATE_DONT_DISCARD)` 保留它。

## 心法

SimulationManager 是 angr 的靈魂。多數 angr script 的 bug 不在 symex 本身，而在 scheduler 沒調好。

調 scheduler 的順序：
1. 先 default `explore()` 看能不能跑通
2. 不行就加 LengthLimiter + LoopSeer
3. 還不行就看 active state 在哪卡、針對 pattern 寫 custom technique
4. 大量 concrete-heavy region 就開 UNICORN

**不要一開始就組五個 technique**。先看 symex 怎麼走、再對症下藥。

## 自我檢核

- [ ] 解釋 simgr 的 stash 是什麼、列出至少 5 個 default stash
- [ ] 能用 `.move()` 把 state 從一個 stash 搬到另一個
- [ ] 知道 DFS、LengthLimiter、LoopSeer、Veritesting 各自解決什麼問題
- [ ] 會用 `state.inspect.b()` 觀察 symex 過程
- [ ] 解釋 UNICORN option 的加速效果與 fallback 條件

下一章是最好玩的 — 用 angr 解一組實際 CTF crackme，把 Ch 14-16 學的東西全部串起來跑。

→ [Ch 17 — CTF 應用：用 angr 解 crackme 的正確姿勢](./17-angr-ctf.md)
