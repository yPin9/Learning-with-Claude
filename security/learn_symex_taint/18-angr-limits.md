# Ch 18 — angr 的極限：什麼時候該關掉 angr

> 目標：列出 angr 失效的情境，配對每個情境的替代工具或 workaround。看完不要再對卡住的 angr 敲 `simgr.step()` 直到天亮。

## 不要把 angr 當通用解

angr 不是 bug finder，不是 fuzzer，不是 decompiler。它是一個 **binary symex 框架**。對下列情況它是對的：

- binary 不大（< 10 MB）
- input 有界（< 1 KB）
- 你要解 path constraint、不是窮舉 memory corruption

對下列情況它是錯的：

- 網路 daemon / GUI 程式
- kernel / driver
- 大型 protocol parser（整本 OpenSSL）
- 需要 race condition 才能觸發的 bug
- 有 strong obfuscation（VM-based、control-flow flatten 嚴重）

## 極限 1：Path explosion 無法救

前面章節已經細講，但具體徵狀：

- active state 十萬 plus
- memory 用量幾十 GB
- 卡在同一個 basic block 十幾分鐘

**救法順序**：
1. 加 `LoopSeer`、`LengthLimiter`
2. 加 input constraint 縮 space
3. Veritesting technique
4. 放棄 explore，改 concolic：固定大部分 input、只讓少量 symbolic
5. 放棄 symex，改 fuzzing

## 極限 2：SMT 解不動

符號方程太複雜，SMT timeout：

- 大量 nonlinear arithmetic（multiply, division）
- 很深的 ite 巢狀
- QF_BV + QF_FP 混合
- memory 的 store-load chain 很長

**救法**：
1. `state.options.add(angr.options.LAZY_SOLVES)` — 把 SMT 推到必要時才做
2. 主動 concretize 部分 symbolic value（`state.solver.eval` 然後 add constraint）
3. 切換 solver backend（`state.solver._solver = ClaripyFrontend(solver=...)`）
4. 手 reverse 出 algorithm、改 Z3 直接 solve（Ch 17 level 4 的做法）

## 極限 3：Environment 太複雜

target 依賴：
- `epoll` / `kqueue` event loop
- 真實 filesystem metadata
- `/proc` / `/sys` 
- shared memory
- ioctl、mmap 特殊設備

angr 的 SimProcedure 涵蓋 libc、POSIX 的常用 API，但**一旦 target 走進 OS-specific 的 corner，就會撞牆**。

**救法**：
1. 找 SimProcedure 是否已有（grep `angr/procedures/`）
2. 自己寫 hook
3. 用 `@claripy.ModelCache` 讓 SimProcedure 能 "memoize"
4. 放棄 full-system symex，只做 target function unit test（call_state）

## 極限 4：閉源 library / blob

target binary 裡 link 了一大塊閉源 library，angr 沒 SimProcedure。

選擇：
- **強 hook**：寫一個 fake 版本
- **讓 angr step into**：配 unicorn 加速、期待它能跑完
- **用 Frida / Pin 在原生 process instrument**：不用 symex，看 concrete trace

對 malware、商業軟體 RE，後者常更實際。

## 極限 5：Binary 太大、CFG 太亂

LibreOffice、Chromium 這種數百 MB binary：

- CLE load 很慢
- CFG 建不起來（memory、時間）
- 單一 function 可能幾萬 instruction

**救法**：
1. 不建整體 CFG，只對感興趣的 function 做 `blank_state(addr=F)` + `call_state`
2. 用 IDA / Ghidra 先 analyze、只把 target function 的 info 餵 angr
3. 對 target function 單獨 extract、rebuild 成小 binary、重新 angr

這就是 **harness-based symex** — 跟 fuzzing harness 同個思路。

## 極限 6：Obfuscation

現代商業 pack（VMProtect、Themida）：

- VM-based code：一條 instruction 變幾百條 dispatch
- Control flow flattening：所有 branch 通過 state machine 中央 dispatcher
- Indirect jump 大量 — CFG recovery 無效

angr 理論上能 symex VMed code，但實務上太慢。

**救法**：
1. 先 deobfuscate：手動 reverse、把 VM 的 handler 解出、寫 script 把 VM bytecode 翻回原生
2. 用專門 tool：Triton（配合 symex 反推 VM）、Miasm
3. 動態 trace 後再 symex：tracer 跑一次拿 concrete trace，angr 在 trace 上做 symex（trace-based symex）

## 極限 7：非決定性 bug

race condition、timing attack、JIT spraying — 這些 bug 的觸發依賴 **時序**，symex 沒時序概念。

**救法**：換工具。
- 并發 bug → `tsan`（ThreadSanitizer）、syzkaller
- Timing → 專門的 side-channel 分析工具
- JIT spraying → 手動 reverse

## 替代 / 互補工具矩陣

給定 target 特徵，推薦對應工具組：

| 情境 | 首選 | 備選 |
|------|------|------|
| CTF RE 小 binary | angr | Z3-only |
| 有 source 的 vuln research | KLEE | AFL++ |
| 大型 binary 的 coverage fuzzing | AFL++ | libFuzzer |
| fuzzer 卡住的 specific branch | angr / SymCC | Driller |
| Taint propagation 問題 | Triton | libdft |
| Malware / packed | 手動 + Frida | Triton |
| Kernel / driver bug | syzkaller | — |
| Crypto 實作驗證 | KLEE | Cryptol |
| Exploit automation | angr + pwntools | Mayhem |

## 你什麼時候應該**不**用 symex

一個常見誤會：「既然 angr 這麼通用，我遇到 binary 問題都試 angr」。

**錯**。真實 workflow：

1. 先拿 target 手動執行一次（strings、ltrace、strace、看輸出）
2. 用 IDA / Ghidra 看反組譯，手動理解 main logic
3. **如果能手解就手解** — 很多 crackme 一眼看穿
4. 解不動再派 angr

跳過前三步直接 angr，叫「蠻幹」。有時會成功，但你浪費了了解 target 的機會。**工程成長來自對 target 的理解，不是 angr 解出的答案**。

## angr 使用進階建議

- **snapshot**：長跑的 symex 可 pickle state，下次不用從頭（`state = pickle.load(open('state.pkl', 'rb'))`）
- **angr-management GUI**：debug 時 visualize 有用，但別作為主力工具
- **升級 frequency**：angr 每 2–3 個月一版，大版本可能有 breaking change。鎖住版本（`pip install angr==9.x.y`）
- **社群資源**：angr-doc GitHub repo、angr Discord、CTF writeup 寫 angr 的很多
- **讀 CTF writeup**：學 angr 最好方式是看別人怎麼用
- **別 fork angr source**：它的 internal 大、不穩定、upstream 進展快。有改需求寫 hook 或 technique，別改 core

## 心法

angr 是**強力但挑剔**的工具。用對了省你幾天 reverse engineering，用錯了浪費你幾天 debug symex。

標準：能手解就手解；能 fuzz 就 fuzz；都不行再上 angr。

你用 angr 的技能成長，不是「調更多 technique」，而是「更準判斷該不該用 angr」。

## 自我檢核

- [ ] 列出 angr 常見失效的 7 種情境
- [ ] 對每種情境講出救法或替代工具
- [ ] 知道 "先手 reverse，再 angr" 的正確 workflow
- [ ] 理解 angr 跟 KLEE、Triton、AFL 的分工
- [ ] 能告訴別人「這題不要用 angr，去找 X」

Part 4 結束。下個是 **練習 C**，你要對一組真實 CTF crackme（自己從 pwnable.tw / rop.baby 下載）跑完整 solver 流程。

→ [練習 C：angr 解一整組 CTF crackme](./practice-c-angr-ctf-series.md)
