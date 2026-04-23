# 練習 C — angr 解一整組 CTF crackme

> 目標：面對一組由易到難的真實 binary，練習 angr 解題的各種姿勢。每題記錄你用的 technique、遇到的問題、解法。

## 題庫建議

以下是免費 / 開源的 CTF crackme 題庫：

- **angr_ctf**（<https://github.com/jakespringer/angr_ctf>）：官方 tutorial 系列，21 題，從 00 到 16。最建議起手
- **rop.emporium**：focus pwn 而非 reverse，但有幾題適合
- **pwnable.kr** / **pwnable.tw**：有 reverse 題
- **crackmes.one**：大量社群上傳的題目
- **picoCTF** past years：有 reverse 題

對這練習：**跑完 angr_ctf 00-16**。那就是 angr 的 hello world 到中階的完整教學集合。

## angr_ctf 題目分類

<https://github.com/jakespringer/angr_ctf> 的 21 題涵蓋：

| 題號 | 主題 | 關鍵 technique |
|------|------|---------------|
| 00 | Basic | `entry_state` + `explore(find=)` |
| 01 | Avoid | `avoid=` 用法 |
| 02 | Finding output | `posix.dumps` |
| 03 | Symbolic registers | `state.regs.reg = claripy.BVS(...)` |
| 04 | Symbolic stack | 手動設 stack var |
| 05 | Symbolic memory | 任意 memory 設 symbolic |
| 06 | Dynamically allocated | symbolic heap |
| 07 | Global vars | BSS 的 symbolic 變數 |
| 08 | Constraints | 手動 `state.solver.add` |
| 09 | Hooks | `proj.hook(addr, ...)` 跳過難模擬的 function |
| 10 | Scanf formats | symbolic scanf 輸入 |
| 11 | Libc functions | replace libc function with SimProcedure |
| 12 | Static libs | 跨函式 hook |
| 13 | Arbitrary read/write | exploit primitive 場景 |
| 14 | Arbitrary jump | unconstrained state 的應用 |
| 15 | Prevention | anti-angr techniques 及其繞過 |
| 16 | Veritesting | state merging 的實用 |

## 工作流程

對每題：

### Step 1 — 手動理解 binary

```bash
file chall            # 架構、是否 stripped
strings chall         # 找線索（flag format、magic string）
objdump -d chall | less   # 看 main
# 或用 IDA / Ghidra / radare2
```

**重點**：找到 main、找到 input 入口、找到成功/失敗的 print。

### Step 2 — 擬定 angr 策略

問自己：

- Input 是 stdin、argv、file？
- Success 是 print 還是 return value？
- 有 libc function 要 hook 嗎？
- 有 loop 嗎？LoopSeer 要嗎？
- 大概需要多少 input byte？

### Step 3 — 寫 solver script

template：

```python
#!/usr/bin/env python3
import angr, claripy, sys

def main():
    proj = angr.Project('./chall', auto_load_libs=False)
    
    # setup input
    flag = claripy.BVS('flag', 8 * N)
    for byte in flag.chop(8):
        # constraint: printable
        state.solver.add(byte >= 0x20, byte < 0x7f)
    
    state = proj.factory.entry_state(stdin=flag)
    simgr = proj.factory.simulation_manager(state)
    
    # optional: techniques
    simgr.use_technique(angr.exploration_techniques.LengthLimiter(500))
    
    # explore
    simgr.explore(
        find=lambda s: b"success_msg" in s.posix.dumps(1),
        avoid=lambda s: b"fail_msg" in s.posix.dumps(1)
    )
    
    if simgr.found:
        sol = simgr.found[0]
        print('flag:', sol.solver.eval(flag, cast_to=bytes))
    else:
        print('no solution')

if __name__ == '__main__':
    main()
```

### Step 4 — 跑、debug

跑的時候可能發生：
- state 爆炸、卡住 → Ch 16 的 technique
- SMT unsat → constraint 太緊
- 解不出正確 flag → hook 錯、input 設錯

診斷用 `state.history.descriptions` 跟 `state.solver.constraints`。

### Step 5 — 記錄心得

每題寫個 **5 行 note**：

```
題目：05_symbolic_memory
策略：把 global variable 在 main 執行前先 symbolic
花費時間：30 min
卡在哪：忘記 address 要 proj.loader.main_object.addr 加 rva
學到什麼：angr 的 memory.store 跟 state.memory.load 的 endian
```

## 重點題目 walkthrough

給你三題的示範 solver。

### 00_angr_find

```
main:
    check = check_password(stdin())
    if check == "Good Job."
        print("Good Job")
    else:
        print("Try again")
```

```python
import angr

proj = angr.Project('./00_angr_find', auto_load_libs=False)
state = proj.factory.entry_state()
simgr = proj.factory.simulation_manager(state)
simgr.explore(find=lambda s: b"Good Job." in s.posix.dumps(1))

if simgr.found:
    print(simgr.found[0].posix.dumps(0))
```

秒解。

### 09_angr_hooks

target 有一個 `check_equals_XXX` function angr 走進去很慢。Hook 它：

```python
import angr, claripy

proj = angr.Project('./09_angr_hooks', auto_load_libs=False)

class check_equals(angr.SimProcedure):
    def run(self, addr, length):
        to_check = self.state.memory.load(addr, length)
        return claripy.If(to_check == b"MAGIC_STRING_HERE",
                          claripy.BVV(1, 32), claripy.BVV(0, 32))

proj.hook(0x<check_equals_addr>, check_equals(), length=5)
state = proj.factory.entry_state()
simgr = proj.factory.simulation_manager(state)
simgr.explore(find=lambda s: b"Good Job" in s.posix.dumps(1))

if simgr.found:
    print(simgr.found[0].posix.dumps(0))
```

### 14_angr_shared_library

target 呼叫一個 shared library，angr 預設不 auto-load。處理：

```python
import angr

proj = angr.Project('./14_angr_shared_library', auto_load_libs=True,
                     ld_path=['./'],
                     use_system_libs=False)

# 找到 library 裡目標 function 的 address
libname = 'lib14_angr_shared_library.so'
lib = proj.loader.shared_objects[libname]
validate = lib.get_symbol('validate').rebased_addr

# 用 call_state 直接呼叫 validate
buf_addr = 0x10000000    # 任意
state = proj.factory.call_state(validate,
    buf_addr, 8,       # args: buf, size
    add_options={angr.options.LAZY_SOLVES})
# fill buf with symbolic
flag = claripy.BVS('flag', 8 * 8)
state.memory.store(buf_addr, flag)

simgr = proj.factory.simulation_manager(state)
simgr.explore(find=lambda s: s.regs.eax == 1)    # success return

if simgr.found:
    print(simgr.found[0].solver.eval(flag, cast_to=bytes))
```

這題的價值：**call_state 的用法**。真實 library 分析都這樣跑。

## 進階挑戰

### 挑戰 1：寫一題自己的 crackme

設計一個 binary，讓 angr 解，計算需要多少時間。你開始理解「什麼樣的題目 angr 容易 / 困難」。

### 挑戰 2：對 obfuscated binary

抓一個有 control-flow flatten 的 crackme（crackmes.one 上有）。你會發現 angr 的 CFG 建不起來、explore 卡住。練習如何 manual 突破。

### 挑戰 3：AMD64 → ARM

pick 一個 ARM binary（Raspberry Pi 或 Android），跑 angr。跨 arch 實際上跟 x86 差不多，但你會碰到 CLE 的 ARM-specific 問題。

### 挑戰 4：Writeup 練習

選一題你自己解過的（不是 angr_ctf 的 tutorial 題），寫成一篇 blog-post style writeup。**Writeup 是 RE 社群的標準 output**，練一次很有用。

## 常見症狀速查

| 症狀 | 可能原因 | 解法 |
|------|----------|------|
| explore() 一直不返回 | path explosion | LengthLimiter、LoopSeer |
| found state 的 input 不是 flag | constraint 太鬆 | 加 charset constraint |
| angr hang 在某個 function | libc 被 step into | 加 SimProcedure hook |
| RecursionError | binary load 時 relocations 問題 | auto_load_libs=False |
| Unsupported instruction | rare x86 ext | 切 unicorn 或 skip block |
| OOM 10+ GB | state 爆 | memory limit + drop technique |

## 必做 vs 可選

**必做**：angr_ctf 的 00, 02, 03, 07, 08, 09, 12

**可選**：全部 21 題、真實 CTF 題、寫 writeup

全部做完大概 **一到兩週**（每天 2 小時）。**做完你對 angr 的掌握會上一階**。

## 提交

跟練習 A 一樣，建議 GitHub repo：

```
angr-ctf-solutions/
├── README.md           # 你的心得總結
├── 00_angr_find/
│   ├── solve.py
│   └── note.md         # 你的 5 行 note
├── 01_angr_avoid/
│   └── ...
└── ...
```

有 GitHub 就順便啟用 Action — angr 每題跑一次 as regression test。這是極好的 portfolio。

## 自我檢核

- [ ] 至少解完 angr_ctf 00-12
- [ ] 每題有 5 行 note
- [ ] 對「何時要 hook、何時 LengthLimiter、何時 call_state」有直覺
- [ ] 能 explain 自己的 solver script 每一行在做什麼
- [ ] 對 path explosion 跟 SMT timeout 有實戰感

→ [Ch 19 — Taint 語意：source / sink / propagation rule](./19-taint-semantics.md)
