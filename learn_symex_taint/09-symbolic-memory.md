# Ch 9 — Symbolic memory：address concretization vs fully symbolic

> 目標：把 symex 最髒的部分攤開。看完這章你會知道為什麼 "symex 處理 memory 很慢" 不是 "實作很爛" 而是物理定律。

## 哪裡髒

Ch 4 已經介紹了三種 memory model。這章深入 **address 是 symbolic** 時怎麼辦。這是整個 symex 領域最難的工程問題之一。

考慮這段 code：

```c
char s[256];
fgets(s, 256, stdin);
int i = s[0];          // i 是 symbolic（stdin-derived）
char c = s[i];         // 取第 i 個 byte — i 是 symbolic！
if (c == 'X') bug();
```

symex 走到 `s[i]` 時：
- address = `s_base + i`（symbolic）
- 你要回傳什麼值？

這就是 **symbolic address access** 的核心。三種主流解法，各有其罪。

## 解法 1：Address concretization

最粗暴：symex 看到 symbolic address 就**枚舉**它可能的 concrete value。

```
addr = s_base + i
solver.enumerate(addr) → [s_base, s_base+1, s_base+2, ..., s_base+255]
```

對每個可能的 address，fork 一條 state：

```
state_0: i = 0, load 回傳 mem[s_base]
state_1: i = 1, load 回傳 mem[s_base+1]
...
state_255: i = 255, load 回傳 mem[s_base+255]
```

**你剛 fork 了 256 條 state**。這個就是 path explosion 的主因之一 — 一次 symbolic memory access 可能產生上百條 state。

### 工程妥協

實務上不會讓你 enumerate 無限多個。工具設 threshold：

- KLEE：`MaxSymArraySize`、如果 enumerate 超過會 fail
- angr：`state.options.add(angr.options.CONSERVATIVE_READ_STRATEGY)`，預設 fork 上限
- 超過 threshold → 放棄這條 state、或 fallback 到其他 strategy

### 什麼時候有效

- i 的範圍**很窄**（幾個可能值）
- memory array 是 concrete 內容（symbolic index 但 data concrete，典型 lookup table）
- 你在乎精度、path explosion 可接受

### 什麼時候爆掉

- i 範圍大（幾百以上）
- memory 也是 symbolic 內容 — fork 出的 state 不只 address 不同、值也不同，state 數不會收斂

## 解法 2：Fully symbolic memory (array theory)

用 SMT 的 **array theory**：memory 當作 `Array(BV64, BV8)`。

```
initial: M_0 : Array(Addr, Byte)
store:   M_1 = store(M_0, p, v)
load:    r = select(M_1, q)
```

load 的 semantic 由 array axiom 定：

```
select(store(a, i, v), j) = ite(i = j, v, select(a, j))
```

不管 i、j、v 是 concrete 還 symbolic，SMT 都能推。不 fork。

### 看起來完美，哪裡不對？

**formula 爆炸速度恐怖**。

考慮 1000 次 store 之後，一次 load：

```
v = select(store(store(... store(M_0, p_0, v_0) ..., p_998, v_998), p_999, v_999), q)
```

根據 array axiom：

```
v = ite(p_999 == q, v_999,
    ite(p_998 == q, v_998,
      ite(p_997 == q, v_997,
        ...
        ite(p_0 == q, v_0, select(M_0, q))
      )))
```

**1000 層巢狀 ite**。每次查 `v` 的 formula 都要走過整串。SMT 能 reason 但慢到哭。

### 實作細節

- **z3 QF_ABV**：quantifier-free bit-vector + array。z3 對這個 theory 有優化但 formula 結構仍然貴。
- **Array decision procedure**：Stump et al. 2001 的 array axiom 實作。現代 solver 都會做 array rewriting 降巢狀深度。
- **Lemma-on-demand**：只在 load 時展開必要的 ite，不是全展開。

### 誰用這個

- **S2E**：默認 fully symbolic memory、代價是慢，但能處理 full-system 的 weird case
- **angr with `SymbolicMemory`**：可開，多數人不開
- **KLEE 沒有這個 mode**：KLEE 堅持 concretization 路線

## 解法 3：Hybrid（partial concretization）

現代工具的做法 — **智能混合**。

```python
def symbolic_load(addr):
    if addr.concrete:
        return mem[addr.value]         # 最快路線
    
    possible = solver.eval_upto(addr, n=FORK_THRESHOLD)
    
    if len(possible) == 1:
        return mem[possible[0]]         # 只有一個 value，直接讀
    
    if len(possible) <= FORK_THRESHOLD:
        # 少量 possible value → fork state
        for a in possible:
            fork_state(addr_fixed_to=a)
    else:
        # 太多 possible value → bail out, 用 fully symbolic
        return select(mem_array, addr)
```

angr 就是這個 pattern。你設 `FORK_THRESHOLD`（預設 256 左右），可以 case by case 調。

**這是實務上最常用的 strategy**。它承認 symbolic memory 沒有 silver bullet，只能選合適的武器。

## 特例：symbolic 寫入到 concrete 地址

比 symbolic load 好一些：你知道寫到哪了，值是 symbolic。

```c
mem[5] = symbolic_value;    // address = 5 (concrete), value = symbolic
```

這個情況：map 裡 `mem[5] = α`。沒有 fork、沒有 ite。後續 `mem[5]` 的 load 回傳 α。**乾淨**。

所以：**symex 對「值 symbolic、address concrete」是友善的；只要 address 變 symbolic 就開始難**。

## 特例：symbolic 寫入到 symbolic 地址

最慘的組合：

```c
mem[i] = v;   // i symbolic, v symbolic
```

concretization 路線：
- enumerate i 的可能值，對每個 fork 一條 state，那個 address 寫 v

array theory 路線：
- M_new = store(M, i, v) — SMT 處理

Hybrid：
- 如果 i 的 possible value 少，枚舉；多，用 store — 但這意味著後面的 load 對 store 的處理要用 ite 展開

這種 case 在真實 code 非常常見（`hashtable[hash(key)] = value`），所以 **symbolic memory 的效能很直接決定 symex 的 scalability**。

## Memory 版本（versioning）

實作細節：SMT 的 array theory 要求 memory 有 version。

```
M_0: initial state
M_1 = store(M_0, p_0, v_0)
M_2 = store(M_1, p_1, v_1)
...
```

每次 write 都產生新 version。SMT 內部 reason 的對象是 version chain。

Symex engine 維護 `state.memory` 這個物件，其實就是當下 version（M_k）的 reference + 一個 write log。fork 時 shallow copy：兩個 state 共享前面的 version chain、各自 append。

這是 angr / KLEE 內部的關鍵 data structure。fork 便宜是因為 memory copy 是 copy-on-write，不是 deep copy。

## 為什麼 C 比 binary 難

Ch 4 已經提過：binary 沒有 struct field、沒有 pointer type。symbolic memory 在 binary 上更難。

例子：

```asm
mov rax, [rbx + 0x10]
```

rbx 如果是 symbolic：
- 在 C 可能是 `p->field_at_16`（只有一個 field）
- 在 binary 不知道，可能是「某個 struct 的第 17 byte」、也可能是 `&array[4]`（如果 array 是 int64）

**symex engine 必須處理所有可能的解讀**。angr 的對策：對 symbolic pointer 的 load，保守地認為它可能 alias 所有之前 symbolic 寫過的地址。Formula 爆炸。

KLEE 因為在 LLVM IR 上跑、struct/pointer type 還在，可以精準 reason — 每個 load 只跟**相同 type 的 store** 可能 alias。這是 KLEE 精度比 angr 高的主因之一。

## 陷阱：沒 initialized 的 memory

```c
char buf[256];   // uninitialized!
int x = buf[0];  // 讀 undefined
```

symex 怎麼處理？兩種選擇：

1. **初始化為 0**（大多數 engine 預設）：你會漏掉「未初始化讀取」這類 bug
2. **初始化為 symbolic**（KLEE 有這個 option）：每個 byte 都是獨立的 symbolic var，會抓到讀 undefined 的問題，但 formula 變大

精度 vs 效能 的永恆取捨。

## OOB 的處理

symex engine 可以順手幫你找 **out-of-bound access**：

```c
char buf[10];
buf[i] = 0;   // 如果 i ≥ 10 就是 OOB
```

走到 `buf[i]` 時，engine 檢查 `i < 10 ∧ i ≥ 0`。如果 SMT 能找到 `i = 10` 或 `i = -1` 的 model，就報 OOB。

這是 KLEE 的殺手級功能之一。它為每個 memory object 記錄 bound，每次 access 都 assert。很多 CVE 就是這樣被 KLEE 發現的。

angr 沒有 built-in 做這個（binary 層面沒 bound 資訊），需要你自己把 object 的 range 加進 state。

## 心法

**symbolic memory 是 symex 的物理天花板**。

- 沒有一個方法對所有 case 都好
- 所有工程 trick 都是在精度與速度間取捨
- 你寫 symex script 時永遠要問：**我的 memory access pattern 有多少 symbolic address？**
- 如果超過幾個，考慮 concretize、或是根本換方法（taint、fuzzing）

這也是為什麼很多 CTF / vuln research 的 symex 工程師，**會把 target 的 memory 先 concretize 到幾乎全部具體**，只留 input buffer 是 symbolic。這樣 symex 的路徑才 tractable。

## 自我檢核

- [ ] 解釋 address concretization、fully symbolic、hybrid 三種 strategy
- [ ] 知道 array theory 的 formula 深度如何隨 store 次數增長
- [ ] 區分「address concrete, value symbolic」跟「address symbolic」的難度差
- [ ] 能解釋 KLEE 為什麼精度比 angr 高（在有 C source 的前提下）
- [ ] 理解 memory versioning 在 state fork 時的 copy-on-write 意義

下一章補完 symex 的最後一塊拼圖 — 外部世界。syscall、libc、filesystem 怎麼 model，哪些是大家公認的 dirty hack。

→ [Ch 10 — Environment modeling：syscall、libc、外部世界](./10-environment-modeling.md)
