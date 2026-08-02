# Ch 43 Directed Fuzzing：AFLGo

> **目標**: 理解 directed greybox fuzzing 的核心設計——distance metric、power schedule 與 simulated annealing，掌握 AFLGo 的完整工作流程，能把 patch diff 或 CVE 函式設為 target 讓 fuzzer 定向打進去。
> **環境**: WSL2 Ubuntu，LLVM/Clang（12+），Python 3，gold linker（LTO pass 必要）

---

## 為什麼需要 Directed Fuzzing

一般 coverage-guided fuzzer（AFL++、libFuzzer）的目標是最大化整個 binary 的覆蓋率。這個策略在通用測試上合理，但有三個場景它天生吃虧：

**場景一：Patch testing**
安全修補發佈後，你想確認修補有效、同時確認沒有引入新漏洞。你關心的只有幾個 changed lines，整支程式的 99% 跟你無關。讓 coverage-guided fuzzer 跑幾天，它會把能量均勻分散到整支程式，真正打到 patch 那幾行的 seed 比例極低。

**場景二：CVE 重現**
已知 CVE 的 crash site 在某個函式，你有 fuzzer seed corpus 但沒有 PoC。你需要把 fuzzer 的探索力量集中在那個函式的呼叫路徑上，而不是重新探索整支程式。

**場景三：Reach 特定 code path**
某個安全敏感的程式碼路徑（parser 深層、特定 protocol handler）只有在滿足複雜前提條件下才會進入。通用 fuzzer 靠運氣碰到的機率極低，你需要一個能主動往那條路徑逼近的機制。

Directed greybox fuzzing（DGF）正是為這三個場景設計的。AFLGo 是 2017 年 CCS 的開創性實作，也是現在最多人拿來用的 directed fuzzer。

---

## 建立直覺：廣度優先 vs 加權最短路徑

Coverage-guided fuzzing 的探索方式類似廣度優先：

```
coverage-guided（廣度優先）
─────────────────────────────
          entry
         / | \ \
        A  B  C  D     <- 全部探索，能量平均分散
       /|   \   |
      E F    G  H
         ...每條路徑都要碰到
```

Directed fuzzing 把 CFG 轉成有向加權圖，然後往目標節點（target BBs）走加權最短路徑：

```
directed（往目標的加權最短路徑）
────────────────────────────────
          entry
         / | \ \
        A  B  C  D
       /|       |
      E F        G <- TARGET

  距離 G 越遠的 BB -> power 低
  距離 G 越近的 BB -> power 高
  能量集中在 entry->D->G 這條路線上的 seed
```

問題在於：你事先不知道哪些 seed 會走哪些路徑。這就是為什麼 directed fuzzing 需要先探索（warmup），再逐漸把能量往目標收攏——simulated annealing 的作用就在這裡。

---

## 核心概念

### Distance Metric

AFLGo 在**編譯期**計算每個 basic block（BB）到 target BBs 的距離。距離定義在兩個層次上：

**BB 層次距離**：在 CFG（control flow graph）內，從 BB `b` 到 target BB `t` 的最短路徑長度，定義為 `d_b(b, t)`。

**Function 層次距離**：跨函式時，使用 call graph。如果 target 在函式 `f_t` 裡，caller 函式 `f_c` 到 `f_t` 的 call graph 距離記為 `d_f(f_c, f_t)`。

**種子的距離**：對執行過的 seed，它走過一組 BB 序列 `T`，對每個 target `t`，取 `T` 中所有 BB 到 `t` 的距離的調和平均數（harmonic mean）。調和平均數讓「有 BB 非常靠近 target」的 seed 得到低距離值（低距離 = 好）。

### Distance-Based Power Schedule

AFL 的 power schedule 決定每個 seed 被 mutate 幾次（energy）。AFLGo 把這個 energy 與距離掛鉤：

- **距離近的 seed**：分配高 energy，被 mutate 更多次
- **距離遠的 seed**：分配低 energy，只被輕度探索

這樣 fuzzer 的 mutation 資源會逐漸集中在那些「已經走近 target」的 seed 上。

### Simulated Annealing（SA）

SA 借用冶金學退火的比喻：高溫時粒子隨機跳動（廣泛探索），降溫後收斂到低能量狀態（聚焦目標）。

AFLGo 的 SA 控制一個「溫度」參數（不要跟 target BB 的符號混淆）：

- **高溫（warmup 階段）**：distance-based 的影響小，fuzzer 行為接近普通 coverage-guided，廣泛探索 code space
- **低溫（directed 階段）**：distance 的影響大，energy 極度集中在近 target 的 seed，犧牲廣度換取深度靠近

SA 的降溫是線性或指數的，由 `-z` 參數指定（`exp` = 指數降溫），`-c` 指定 warmup 時長（cooling 前的探索時間）。

### 為什麼不直接 Dijkstra 一路衝

直接把所有 energy 集中在最近 target 的 seed 有個致命問題：你的 seed 庫初始是空的（或很稀疏），在 fuzzer 真正發現走向 target 的路徑之前，「最近 target 的 seed」可能是完全沒用的。你需要先靠廣泛探索建立足夠的 seed 多樣性，才有材料讓 directed phase 發揮作用。SA 的 warmup 解決這個 chicken-and-egg 問題。

---

## 底層機制

### CFG/Call Graph 上的距離計算

```
                    ┌─────────────────────────────────┐
                    │         Call Graph               │
                    │                                  │
                    │  main -> parse_input -> process  │
                    │                ↓                 │
                    │           validate -> [TARGET_f] │
                    └─────────────────────────────────┘
                              ↓ 展開
                    ┌──────────────────────────────────┐
                    │    parse_input 的 CFG             │
                    │                                  │
                    │  BB_entry                        │
                    │     ↓                            │
                    │  BB_check ──> BB_error (exit)    │
                    │     ↓                            │
                    │  BB_call_validate  <- 到 TARGET 1 hop
                    └──────────────────────────────────┘

  d_f(parse_input, TARGET_f) = 1（call graph 距離）
  d_b(BB_call_validate, TARGET_BB) = 1（CFG 距離）
  d_b(BB_entry, TARGET_BB) = 2
```

### Seed 的 Power Schedule 權重

```
  Seed A：走過 [BB_entry, BB_check, BB_call_validate]
           距 TARGET 平均距離 = 調和平均(2, 2, 1) ≈ 1.5  <- 近

  Seed B：走過 [BB_entry, BB_error]
           距 TARGET 平均距離 = 調和平均(2, ∞) ≈ ∞       <- 遠

  SA temperature = 低（directed phase）時：
    energy(A) >> energy(B)
    A 被 mutate 數百次，B 幾乎不動
```

---

## AFLGo 工作流程

### 編譯期：插樁 + 距離計算

AFLGo 的插樁分兩個階段，都需要 LLVM gold plugin（LTO pass）：

1. **第一次編譯（CFG 生成）**：`afl-clang-fast` 把每個函式的 CFG dump 成 dot 文件
2. **距離計算**：Python 腳本讀入 dot 文件 + call graph，計算每個 BB 到 target BBs 的距離，輸出 `distance.cfg.txt`
3. **第二次編譯（插樁）**：把距離值嵌入 binary，每個 BB 執行時會把自己的距離值加進 shared memory

編譯後的 binary 帶有「距離感知」——執行完一個 input 後，AFLGo 可以從 shared memory 讀出這次執行的平均距離。

### 運行期：距離收集 + SA 調度

1. 對每個 input，執行 binary，從 shared memory 讀出 `avg_distance`
2. 把 `avg_distance` 存進 seed metadata
3. 按照當前 SA 溫度，計算這個 seed 的 energy：溫度越低，低距離 seed 的 energy 倍率越大
4. 對這個 seed 進行 `energy` 次 mutation，每次 mutation 生成的新 input 也跑一遍，如果距離更近則加入 seed queue

### Target BBs 的指定方式

Target 用 `source_file:line_number` 格式指定，一行一個 target，存成 `targets.txt`：

```
vuln.c:42
parser.c:137
heap.c:89
```

AFLGo 在編譯期把這些 source locations 對應到 BB（透過 debug info），計算距離。這要求你用 `-g` 編譯（保留 debug info）。

---

## 真跑驗證

**本段未實測，理論預期行為。** AFLGo 的 build 依賴 gold linker plugin，在不同 LLVM 版本間常有相容性問題，以下步驟基於 LLVM 12 + AFLGo master。

```bash
# 安裝依賴
sudo apt install llvm-12 clang-12 llvm-12-dev libllvm12 \
     binutils-gold llvm-12-linker-tools python3-networkx

# 建置 AFLGo
git clone https://github.com/aflgo/aflgo
cd aflgo && make

# 準備 target 列表
# 假設你要打 CVE 涉及的 vuln.c 第 42 行
echo "vuln.c:42" > /tmp/targets.txt

# 第一階段編譯：生成 CFG dot 文件
mkdir -p /tmp/aflgo-obj
export AFLGO=/path/to/aflgo
export TMP_DIR=/tmp/aflgo-obj
export CC="$AFLGO/afl-clang-fast"
export CXX="$AFLGO/afl-clang-fast++"
export CFLAGS="-targets /tmp/targets.txt -outdir $TMP_DIR -flto -fuse-ld=gold"
./configure --disable-shared
make clean && make

# 計算距離（Python 腳本）
$AFLGO/scripts/gen_distance_fast.py $TMP_DIR /tmp/distance.cfg.txt vuln

# 第二階段編譯：嵌入距離資訊
export CFLAGS="-distance /tmp/distance.cfg.txt -flto -fuse-ld=gold"
make clean && make

# 準備 seed corpus
mkdir seeds
echo "AAAA" > seeds/seed0

# 執行：-z exp 指數降溫，-c 45m = warmup 45 分鐘後切 directed
afl-fuzz -m none -z exp -c 45m -i seeds -o out ./target @@
```

`-z exp -c 45m` 的意義：
- `-z exp`：SA 使用指數降溫（cooling schedule = exponential），溫度下降速度相對平穩
- `-c 45m`：前 45 分鐘是 warmup（高溫），fuzzer 行為接近普通 AFL，廣泛探索建立 seed 多樣性；45 分鐘後切入 directed 模式，energy 開始往低距離 seed 集中

45 分鐘是 AFLGo 論文建議的 warmup 比例（約佔總 fuzzing 時間的 1/3），實際應根據程式複雜度調整——程式越大、target 越深，warmup 要越長。

---

## 實戰用途

### Patch-Diff 接 Directed Fuzzing

1. `git diff HEAD~1 HEAD --unified=0` 取得 changed lines
2. 解析 diff 輸出，抽取 `filename:line` 格式
3. 把這些 lines 寫進 `targets.txt`
4. 跑 AFLGo，讓 fuzzer 集中打 patch 改過的地方

這個流程可以自動化成 CI 的一步：每次 PR merge 後，對 changed lines 跑 24 小時 directed fuzzing，比通用 fuzzer 更早發現 patch 引入的 regression。

### CVE PoC 自動生成

已知 CVE 的 advisory 通常會提 affected function 或 source location。把那個 function 的入口 BB 設為 target，用現有的 fuzzing corpus 作為 seed，讓 AFLGo 嘗試重現。比從零開始找 PoC 快很多，特別是當 CVE 涉及複雜的輸入前置條件時。

### 接 Browser Pwn / Android 的 Patch Testing 場景

在 `security/browser_pwn` 的 patch diff 場景或 `security/android_reversing` 的 native library 漏洞驗證中，directed fuzzing 能解決「目標 code path 太深、coverage fuzzer 打不到」的問題。給定 Chromium 的某個安全修補 commit，把 changed lines 設為 AFLGo targets，用 ClusterFuzz 的 seed corpus 驅動，效果遠優於純 coverage-guided。

---

## 對比取捨

| fuzzer | 類型 | 目標 | 優點 | 缺點 |
|--------|------|------|------|------|
| **AFLGo** | directed greybox | target BBs | 實作成熟、論文扎實 | build 麻煩（gold LTO）、warmup 時間需調 |
| **AFL++** | coverage-guided | 最大覆蓋 | 通用、插件豐富 | 不適合深層 target |
| **Hawkeye** | directed greybox | target BBs | 加了 static analysis 輔助、比 AFLGo 更精確 | 實作較新、社群小 |
| **WindRanger** | directed greybox | target BBs | deviation BB 概念，繞過控制流障礙更有效 | ICSE 2022，研究原型為主 |
| **Beacon** | directed greybox | target BBs | precondition inference，更主動推算需要的輸入條件 | 實作複雜度高 |
| **libFuzzer** | coverage-guided | 最大覆蓋 | 快、易整合 Sanitizer | 無 directed 機制 |

AFLGo 的主要對手是 Hawkeye 和 WindRanger。Hawkeye 加入了 static analysis 來優化 target selection 和 seed prioritization，在同樣的 target 下通常比 AFLGo 更快到達；WindRanger 解決了 AFLGo 在有「控制流障礙」（必須滿足特定條件才能跳的 branch）時容易卡死的問題。但 AFLGo 是最容易部署的，文件最完整。

---

## 踩雷

**Target BB 設太少——只設一個 crash site**

只把 crash function 的那一行設為 target，fuzzer 看不到任何靠近路徑，距離計算退化成全部 seed 距離相近（都很遠），power schedule 失效。

正確做法：把整個 call chain 都設進去。如果 crash 在 `deep_parse()`，而它的呼叫路徑是 `main -> read_input -> tokenize -> deep_parse`，就把 `tokenize` 的入口和 `deep_parse` 的入口都設為 targets，讓 fuzzer 有「靠近的中繼站」可以跟蹤。

**Warmup 時間設太短**

`-c 5m` 在複雜程式上根本不夠建立 seed 多樣性。5 分鐘後 SA 進入 directed 模式，但 seed queue 裡全是差不多的 input，directed phase 只是在一堆爛 seed 上重複 mutate，等於退化成隨機打。

判斷 warmup 夠不夠的方法：看 AFL 的 `paths_found`。如果 warmup 結束時還在快速增加，warmup 太短了。

**忘記 gold linker**

AFLGo 的距離計算依賴 LTO（link-time optimization）pass，gold linker 是 LLVM LTO plugin 的必要條件。如果系統預設是 `ld.bfd`，編譯會成功但距離資訊根本沒有被計算嵌入，binary 跑起來是普通 AFL binary，不是 directed 的。症狀：`afl-fuzz` 顯示 `distance: N/A`。

修法：確認 `which ld` 指向 gold，或在 `CFLAGS` 加 `-fuse-ld=gold` 並確認 gold plugin 存在（`/usr/lib/llvm-12/lib/LLVMgold.so`）。

**Target source location 對不上 BB**

如果 target 的 line number 對應到一個宣告行（`int x = 0;`）或空白行，LLVM debug info 可能把它合併進鄰近 BB，AFLGo 找不到對應的 target BB，這個 target 會被靜默忽略。

修法：把 line number 設在真正有 computation 的行（函式呼叫、條件判斷），或設在函式入口的第一個可執行語句。

---

## 進階延伸

**Hawkeye**（CCS 2018）
比 AFLGo 更精確的 directed fuzzer，加入了以下改進：
- **Target selection 精煉**：用 static backward slicing 分析到達 target 需要哪些 tainted 輸入欄位
- **Seed prioritization**：不只看距離，還看 seed 「卡在哪個 branch condition 上」
- **Path exploration guidance**：主動生成能繞過 branch 的 input

在有複雜 branch condition 的程式上，Hawkeye 比 AFLGo 快 3–10 倍到達 target。

**WindRanger**（ICSE 2022）
核心概念是「deviation basic blocks」：到 target 路徑上那些「執行到就等於在走向 target、沒執行到就走岔了」的 critical BB。WindRanger 優先讓 fuzzer 覆蓋這些 deviation BB，解決 AFLGo 在 branch-heavy 程式上的卡死問題。

**Beacon**（S&P 2022）
更進一步：用 precondition inference 在 fuzzing 前靜態推算「要進入 target 需要滿足什麼輸入條件」，然後把 fuzzer 的 mutation 集中在滿足這些條件上。在高度路徑依賴的程式（如協議解析器）上效果顯著。

**SelectFuzz**（S&P 2023）
解決「設的 target 太多時 AFLGo 被分散」的問題，用靜態分析選出最關鍵的 target 子集。

---

## 動手練習

1. 找一個你熟悉的開源專案，用 `git log --oneline -5` 取最近的安全修補 commit，提取 changed lines，設成 AFLGo targets，對比普通 AFL++ 跑 30 分鐘後誰先觸碰到 target BB。

2. 下載一個已知 CVE 的修補 commit，把修補前後的 changed function 設為 target，用 AFLGo 嘗試生成觸碰該路徑的 input。觀察 `afl-fuzz` UI 中的 `avg_distance` 是否隨時間下降。

3. 用同一個 target binary，分別跑 `-c 5m`、`-c 30m`、`-c 60m`，記錄每個設定在 2 小時後的 `avg_distance` 和 `total_paths`，感受 warmup 時間對結果的影響。

---

## 本章重點

- Directed greybox fuzzing 把 fuzzer 的探索能量集中在指定 target BBs，解決 coverage-guided fuzzer 在 patch testing、CVE 重現、深層 code path 上的天生弱點。
- AFLGo 的核心是三件事：編譯期計算 BB 距離（CFG + call graph）、運行期距離感知 power schedule、SA 控制 warmup 到 directed 的過渡。
- Target 指定為 `source_file:line` 格式，設整個 call chain 而非只設 crash site。
- `-z exp -c 45m` = 指數降溫 + 45 分鐘 warmup，複雜程式需拉長 warmup。
- Gold linker 是 build AFLGo 的非可選依賴，缺了它距離資訊無法嵌入 binary。
- Hawkeye / WindRanger / Beacon 各解決 AFLGo 的一個弱點，但 AFLGo 仍是最易部署的起點。

---

## 自我檢核

- [ ] 能解釋 distance metric 為什麼用調和平均數而不是算術平均數
- [ ] 能說明 SA warmup 解決的 chicken-and-egg 問題是什麼
- [ ] 知道 AFLGo 編譯需要兩個階段（CFG dump + 距離嵌入），而不是一次完成
- [ ] 能把 `git diff` 輸出轉成 AFLGo 的 `targets.txt` 格式
- [ ] 知道 `-z exp -c 45m` 各參數的意義
- [ ] 能解釋為什麼只設 crash site 當 target 會讓 directed 失效
- [ ] 知道 Hawkeye 和 WindRanger 各解決 AFLGo 的哪個弱點

---

## 延伸閱讀

1. **Directed Greybox Fuzzing**（CCS 2017，Böhme, Pham, Nguyen, Roychoudhury）——AFLGo 原始論文，distance metric 和 SA power schedule 的完整推導，必讀。https://dl.acm.org/doi/10.1145/3133956.3134020

2. **Hawkeye: Towards a Desired Directed Grey-box Fuzzer**（CCS 2018，Chen, Li, et al.）——靜態分析輔助 directed fuzzing，對 AFLGo 在 branch-heavy 場景的改進有完整實驗對比。https://dl.acm.org/doi/10.1145/3243734.3243849

3. **WindRanger: A Directed Greybox Fuzzer driven by deviation basic blocks**（ICSE 2022，Zong, Lv, et al.）——deviation BB 概念解決控制流障礙，實驗涵蓋多個真實 CVE 的重現速度對比。https://dl.acm.org/doi/10.1145/3510003.3510197

4. **AFLGo GitHub Repository**——包含完整 build 說明、範例 target 和距離計算腳本。https://github.com/aflgo/aflgo

---

本章介紹了 directed fuzzing 的完整機制。距離計算是編譯期的靜態工作，SA 調度是運行期的動態控制，兩者合力讓 fuzzer 能從廣泛探索平滑過渡到定向打擊。下一個練習把這個技術用在實際的 hybrid 場景——結合符號執行和 directed fuzzing，讓符號執行負責突破路徑條件，AFLGo 負責收斂到 target。

→ [練習 F](./practice-f-hybrid-directed.md)
