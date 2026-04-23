# Ch 17 — Crash triage：uniqueness、tmin、cmin 的演算法

> 目標：解釋 AFL 的 unique crash 是用 bitmap hash 判定 — 為什麼會過度估計；拆 `afl-tmin` 的 delta debugging style 化簡；拆 `afl-cmin` 怎麼挑出最小覆蓋集。

## Fuzzer 跑完之後呢

fuzzer 跑一週，`crashes/` 目錄長這樣：

```
crashes/
├── id:000000,sig:11,src:000042,op:havoc,rep:16
├── id:000001,sig:06,src:000042,op:havoc,rep:32
├── id:000002,sig:11,src:000042,op:havoc,rep:8
├── id:000003,sig:11,src:000089,op:splice,rep:64
├── id:000004,sig:11,src:000089,op:splice,rep:16
...
├── id:005432,sig:11,src:012345,op:havoc,rep:8
```

幾千甚至幾萬個檔案。但 **實際獨立的 bug 通常只有幾十個** — 一個 bug 會被 fuzzer 從不同路徑觸發幾百次。Triage 的第一步是把這些 crash 合併到 root cause。

## Unique crash 的判定機制

AFL 判 crash uniqueness 的方法很簡單：**比對 crash 時的 bitmap**。

流程：

1. Fuzzer 偵測到 SIGSEGV / SIGABRT / timeout → 認定 crash。
2. 把 crash 發生時的 trace_bits hash 一下，得到一個 signature。
3. 如果 signature 沒看過 → unique，存到 `crashes/`。
4. 如果看過 → 標為 duplicate，丟棄（或放到 `hangs/` 區 depending on 原因）。

「沒看過」的判斷用一個 `virgin_crash` bitmap（類似 virgin_bits 但專給 crash 用）。

## 為什麼會過度估計

這個 bitmap-based 判定有一個根本問題：**兩個 bug 的 crash bitmap 可能不同，但 root cause 可能一樣**。

例子：同一個 heap overflow，兩個不同 input：

- Input A：overflow 8 byte，crash in `memcpy` at line 42
- Input B：overflow 24 byte，crash in `strlen` at line 56（之前 memcpy 沒 crash，但後面 strlen 讀到壞 memory）

兩個 crash 的 execution path 不同 → bitmap 不同 → AFL 判為 unique。但 root cause 是同一個 overflow。

實測過度估計率可達 **10–100 倍**。一個「2000 unique crashes」的 report，去重後可能只剩 20 個 bug。

## 正確的去重：stack trace hashing

業界實務常用的方法：**用 crash 時的 stack trace 做 hash**，而非 coverage bitmap。流程：

1. 對每個 crash input，用 debugger（gdb / lldb）跑一次 target。
2. 拿 stack trace，取頂端 N 個 frame（通常 N=3 或 5）。
3. 把這 N 個 frame 的函式名或 file:line hash 起來。
4. 相同 hash 的視為同一 bug。

AFL++ 內建 `casr`-integration 和幾個 triage 腳本，但主流工具是：

- **CERT BFF**（Basic Fuzzing Framework）的 crash triager
- **Crashwalk**
- **casr**（Rust 寫的 crash analysis）

實際命令（用 gdb 抓 backtrace）：

```bash
gdb -batch -ex run -ex bt ./target < crashes/id:000042,sig:11,...
```

取出 top frames，hash 比對。

## afl-tmin：crash input 最小化

`afl-tmin` 解的問題：fuzzer 給你一個 10KB 的 crash input，但其中可能只有幾個 byte 是觸發 bug 的關鍵。**你要給 dev 看最短能重現的 input**，不然他們不會看。

### Delta debugging 風格演算法

`afl-tmin` 用 **delta debugging**（Andreas Zeller 的 ddmin）的變形。核心 idea：

```
目標：找到保持相同 crash 的最小子集
```

簡化流程：

```
function trim(input):
    step = len(input) / 2
    while step >= MIN_CHUNK:
        start = 0
        while start + step <= len(input):
            candidate = input[:start] + input[start + step:]
            if still_crashes(candidate):
                input = candidate   # 拿掉這塊還 crash → 砍掉
            else:
                start += step       # 保留這塊，前進
        step = step / 2
    return input
```

二分 chunk size：先嘗試大塊刪，失敗縮小，直到 chunk 小到 1 byte 也刪不動就停。

實際 `afl-tmin` 還多一步 — 嘗試把每個 byte **替換成 `0x00`**：

```
for each byte in input:
    tmp = input with this byte = 0x00
    if still_crashes(tmp):
        input = tmp
```

因為 0x00 一定比原值「更無資訊」— 能保持 crash 表示這個位置內容不重要。

### 怎麼判斷「still_crashes」

這裡有個微妙問題：是「crash」還是「相同 crash」？

`afl-tmin` 預設只看 SIGSEGV / SIGABRT 有沒有發生。但嚴格來說「同類 crash」該看 stack trace。`afl-tmin -e` 可以讓它比對完整 bitmap（edge coverage）— 這樣化簡後會保持相同 execution path。

```bash
afl-tmin -i crash_input -o minimized -- ./target @@
```

化簡時 target 會被重複執行數百到數千次。對跑快的 target 幾秒到幾分鐘，慢的可能要小時。

### 例子

假設 crash input 10KB，內容是某個 corrupted PNG：

```
[magic][IHDR chunk][nnn bytes of garbage][IDAT chunk][nnn bytes][...]
```

跑 `afl-tmin` 後可能留下：

```
[magic][IHDR chunk][IDAT with 1 overflow byte]
```

從 10KB 降到 30 byte。Dev 看這個能立刻診斷。

## afl-cmin：corpus 最小化

不同問題：fuzzer 跑完 queue 有 5000 個 entry。如果要 **把這些 entry 拿去別的 session、放進 CI、或發布給其他人**，5000 個太多 — 多數是冗餘。我們要的是「覆蓋同樣 edge 集合的最小子集」。

這就是 **corpus minimization（cmin）**，本質上也是 set cover 問題。

### Greedy set cover

`afl-cmin` 的演算法：

```
1. 對每個 corpus 的 input，跑 target 拿 bitmap（edge 集合 E_i）。
2. 依 |E_i|（edge 覆蓋數）由多到少排序 — 先挑覆蓋多的。
   或依 exec_us / size 加權。
3. Greedy：
      covered = empty set
      result = []
      for each input i in sorted order:
          if E_i \ covered is not empty:
              result.append(i)
              covered = covered ∪ E_i
      return result
```

這是 set cover 的標準 greedy 近似解。近似比 O(log n)，對 fuzzing 足夠好。

跑法：

```bash
afl-cmin -i large_corpus/ -o min_corpus/ -- ./target @@
```

5000 個 input 可能縮到 300 個左右。縮後再放 CI 或發布。

### 為什麼不在 fuzzing 過程中 online 做

`cull_queue`（Ch 8）就是 online 版本，fuzzer 每次新 entry 進來都會更新 `top_rated[]` 和 favored。但：

- online 版用 `exec_us × len` 做 score，`cmin` 可以更講究。
- fuzzing 時 queue 會持續長，`cull_queue` 只管當下；`cmin` 是一次性精確計算。

兩者各有用途。

## afl-showmap：看單一 input 的 coverage

輔助工具。給一個 input，跑一次 target，把 bitmap 印出來或寫到檔：

```bash
afl-showmap -o trace.txt -- ./target < some_input
# trace.txt 每行：edge_id: hit_count
```

用途：

- 手動分析兩個 input 的 coverage diff。
- 給自己寫的 triage script 當 building block。
- debug「為什麼 fuzzer 認為這個 input 有新 coverage」。

## afl-analyze：每 byte 的影響

另一個輔助工具，逐 byte 修改 input，觀察哪些 byte 變了會影響 coverage / crash：

```bash
afl-analyze -i crash_input -- ./target @@
```

輸出會標示每個 byte 的角色：

```
000-003: no change (magic bytes?)
004-007: changes trigger different coverage (important)
008-015: no effect on coverage (padding?)
...
```

對 binary format reverse engineering 有幫助 — 還沒讀規格前就能先看出哪些 byte 重要。

## 完整 triage 流程

實際用 AFL++ 做 triage 的 workflow：

```
1. 收集 crashes/ 和 hangs/
2. 跑 afl-cmin 降冗餘（對 queue 用；crashes 可跳過）
3. 用 casr / 自製 script 以 stack trace hash 去重
4. 每個 unique bug 跑 afl-tmin 化簡 input
5. 跑 ASan / UBSan 版 target 重現，拿詳細報告
6. 手動看哪些是 exploitable、哪些是 low-severity
```

沒一個工具一條龍，是一連串 filter。

## 常見誤解

- **「AFL 說 2000 unique crashes 就是 2000 個 bug」**：典型新手錯誤。實際可能只有 20。先 stack trace dedupe 再下結論。
- **「afl-tmin 會把 input 縮到本質」**：它會縮到「最小保持 crash 的 input」，但那不一定等於「本質」— 可能還有冗餘的 wrapper byte。手動再審一次。
- **「afl-cmin 的結果是最優的」**：greedy set cover 是近似解，不是最優。通常夠好，但如果你拿它當 benchmark suite 要小心。

## 自我檢核

- [ ] 能說出 AFL unique crash 判定機制以及為什麼會過度估計
- [ ] 能用自己的話描述 delta debugging 的迴圈（二分 chunk size + 嘗試刪除）
- [ ] 能說明 corpus minimization 是 set cover、用 greedy 近似
- [ ] 能說出 stack trace hashing 在 triage 中的角色
- [ ] 知道 `afl-showmap` / `afl-analyze` 的用途

下一章是本系列最後一章 — 把 AFL++ 和它的兩個主要對手放在一起比。

→ [Ch 18 AFL++ vs libFuzzer vs Honggfuzz：設計哲學對比](./18-fuzzer-comparison.md)
