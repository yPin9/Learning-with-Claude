# Ch 28 二進位相似度與函式指紋

> **目標**：學會用函式指紋技術快速從陌生 stripped binary 中剔除已知庫函式，把注意力集中在真正要逆的業務邏輯；理解各種相似度算法的本質與侷限，能在工作流中選對工具。

> **環境**：Linux / WSL，需要 `gcc`、`objdump`、`python3`、`radare2`（`r2`）。`objdump` 已裝在任何正常 Linux 系統，r2 用 `pip install r2pipe` 或直接裝 radare2 binary 即可。不需要 IDA Pro（FLIRT 只作概念說明）。

---

## 為什麼需要？

你拿到一個 stripped binary，`nm` 什麼都不給，gdb 進去看到的全是 `sub_4015a0`。裡面可能有三百個函式，其中兩百八十個是 `libc`、`zlib`、`openssl` 靜態連結進來的。你要花時間逆這兩百八十個嗎？不。

相似度指紋解決的問題是：**「這個函式，我在別的地方見過。」**

應用場景直接列：

- **庫函式辨識（library matching）**：把 `memcpy`、`strcmp`、`inflate` 認出來，打上名字，剩下的才是你要的業務邏輯。這是 FLIRT / r2 zignature 最主要的用途。
- **漏洞 variant hunting**：CVE-XXXX-YYYY 的漏洞函式是 `foo_parse()`，你懷疑同廠商另一個產品的 binary 也有類似實作，跨 binary 搜相似函式。
- **malware 家族分類**：同一個 malware builder 吐出來的樣本結構高度相似，用函式相似度可以分群，不用逆每一個。
- **抄襲偵測 / 合規掃描**：某 embedded firmware 是否夾帶了 GPL 授權的 OpenSSL？

這四種場景對「相似度」的精度要求差很多，所以方法也不同。

---

## 先建立直覺

先搞清楚為什麼直接 md5 整個函式的 byte 序列是錯誤的起點。

寫一個最簡單的 C 函式：

```c
// target.c
#include <stddef.h>

int sum_array(int *arr, size_t n) {
    int total = 0;
    for (size_t i = 0; i < n; i++) {
        total += arr[i];
    }
    return total;
}

int main(void) {
    int a[] = {1, 2, 3, 4, 5};
    return sum_array(a, 5);
}
```

分別用 `-O0` 和 `-O2` 編：

```bash
gcc -O0 -o target_O0 target.c
gcc -O2 -o target_O2 target.c
objdump -d target_O0 | grep -A 40 '<sum_array>'
objdump -d target_O2 | grep -A 40 '<sum_array>'
```

`-O0` 的 `sum_array` 大約 25-30 行組語，用 stack frame 乖乖存每個變數。`-O2` 可能壓縮到 10 行以下，用暫存器做循環計數，甚至被 inline 掉。byte 序列完全不同，md5 當然也不同。

---

## 函式雜湊：最粗的方法

### Bytewise hash

拿函式的機器碼 bytes 直接算 md5/sha1。

優點：算法簡單，速度極快。
缺點：任何改變都讓指紋失效：

- 不同編譯器版本
- 不同最佳化等級
- 地址相關的 relocation（call target 地址不同 → bytes 不同）
- 不同的 PIE / ASLR 基址

只有在「完全一樣的 build pipeline 下產生的同一個版本」這種情況才有用，比如 malware 的不同副本（但只要 recompile 一次就廢了）。

實際上可以稍微改良：把 call/jmp 指令的 target offset 填成 0 再算 hash（normalize relocation）。這讓不同 load address 的同一份 binary 有相同指紋，但跨編譯器版本還是廢。

---

## Mnemonic 序列指紋

把機器碼解成助記符（mnemonic）序列，丟掉運算元，只保留指令種類，然後算 hash。

這是比 bytewise 稍微好一點的方法。`mov rax, rbx` 和 `mov rcx, rdx` 都變成 `mov`，不同暫存器分配不影響指紋。

### 真跑：用 objdump + Python 算 mnemonic hash

```python
#!/usr/bin/env python3
# mnemonic_hash.py
import subprocess
import hashlib
import sys
import re

def get_mnemonic_seq(binary, func_name):
    out = subprocess.check_output(
        ['objdump', '-d', '--no-show-raw-insn', binary],
        text=True
    )
    in_func = False
    mnemonics = []
    for line in out.splitlines():
        if f'<{func_name}>:' in line:
            in_func = True
            continue
        if in_func:
            # 空行或下一個函式開頭 = 結束
            if line.strip() == '' or (line.strip() and line[0] != ' ' and ':' in line and '<' in line):
                if mnemonics:
                    break
                continue
            # 格式: "  addr:  mnemonic  operands"
            m = re.match(r'\s+[0-9a-f]+:\s+(\w+)', line)
            if m:
                mnemonics.append(m.group(1))
    return mnemonics

if len(sys.argv) < 3:
    print(f"Usage: {sys.argv[0]} binary1 binary2 [func_name]")
    sys.exit(1)

bin1, bin2 = sys.argv[1], sys.argv[2]
func = sys.argv[3] if len(sys.argv) > 3 else 'sum_array'

seq1 = get_mnemonic_seq(bin1, func)
seq2 = get_mnemonic_seq(bin2, func)

h1 = hashlib.md5(' '.join(seq1).encode()).hexdigest()
h2 = hashlib.md5(' '.join(seq2).encode()).hexdigest()

print(f"[{bin1}] {func}: {len(seq1)} mnemonics, hash={h1[:12]}")
print(f"[{bin2}] {func}: {len(seq2)} mnemonics, hash={h2[:12]}")
print(f"Sequence match: {seq1 == seq2}")
print(f"Hash match: {h1 == h2}")

# 簡單相似度：longest common subsequence / max length
def lcs_len(a, b):
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    dp = [[0]*(n+1) for _ in range(2)]
    for i in range(1, m+1):
        for j in range(1, n+1):
            if a[i-1] == b[j-1]:
                dp[i%2][j] = dp[(i-1)%2][j-1] + 1
            else:
                dp[i%2][j] = max(dp[(i-1)%2][j], dp[i%2][j-1])
    return dp[m%2][n]

lcs = lcs_len(seq1, seq2)
sim = lcs / max(len(seq1), len(seq2)) if max(len(seq1), len(seq2)) > 0 else 0
print(f"LCS similarity: {sim:.2%} ({lcs}/{max(len(seq1), len(seq2))})")
```

```bash
python3 mnemonic_hash.py target_O0 target_O2 sum_array
```

典型輸出（你的結果會因 gcc 版本有出入）：

```
[target_O0] sum_array: 28 mnemonics, hash=a3f8c1d92b44
[target_O2] sum_array: 9 mnemonics, hash=7e2b90a1f338
Sequence match: False
Hash match: False
LCS similarity: 64.29% (9/14)
```

hash 不同，但 LCS 相似度還有 60% 左右。這說明：**序列 hash 對最佳化同樣脆弱**，但序列本身還是有結構性相似度可以挖掘。

---

## CFG 相似度：對混淆更有抵抗力

把函式的控制流圖（Control Flow Graph）當作比較單位，而不是 flat 的指令序列。

### CFG 是什麼

CFG 的節點是基本塊（basic block）：直線執行、無分支的指令序列。邊是控制流轉移（條件跳、無條件跳、fallthrough）。

`-O0` 的 `sum_array` 可能有：entry → loop_header → loop_body → loop_exit → return，五個基本塊。`-O2` 可能被展開成 entry → exit 兩個塊，但整體拓樸結構（幾個節點、幾條邊、是否有 back-edge）還是保留了「有一個迴圈」的資訊。

### BinDiff 的核心概念

BinDiff（Google 出品，現已開源）是最廣泛使用的二進位差分工具，底層的函式匹配演算法大致分兩階段：

1. **Call graph matching**：先從整個二進位的函式呼叫關係圖出發，找「同樣呼叫 A 又被 B 呼叫」的函式對，從已知錨點（庫函式名字）向外擴散。
2. **CFG matching**：對配對好的函式，做基本塊級別的 CFG isomorphism 搜尋，找最佳匹配的基本塊對應關係。

BinDiff 的設計前提是：你已經有兩個 IDB（IDA database），讓 IDA 先做好初步分析，BinDiff 再來做差分。它不是獨立工具，是 IDA 的 plugin（不過現在也有 BinExport 可以拿出 protobuf 在外面跑）。

### 基本塊指紋

一個粗但實用的中間方案：對每個基本塊取 mnemonic hash，再用 set similarity（Jaccard / cosine）比較兩個函式的基本塊集合。

這比全序列 hash 穩定，因為最佳化可能重排基本塊，但基本塊本身的指令組成變化相對小。

---

## FLIRT 與 r2 zignatures

### FLIRT（IDA 用）

FLIRT 是 IDA 自帶的靜態庫識別系統。Hex-Rays 維護了大量常見庫的 `.sig` 簽名文件，IDA 開啟 binary 時自動比對。

FLIRT 簽名的核心是：取函式開頭若干 bytes（通常 32 bytes），加上 CRC16 校驗一段後續 bytes，形成一個多欄位指紋。設計上允許 wildcard（call target 等 relocation 部分填 `??`）。它不是 CFG 方法，還是 byte 序列，但因為 wildcard 機制和多欄位設計，對 relocation 問題有一定抵抗力。

實際限制：只在函式開頭有效。如果函式 prologue 被混淆（比如 VM 保護把每個函式 prologue 換成 VM entry），FLIRT 全廢。

FLIRT `.sig` 文件不開放，但有第三方如 `IDAscope`、`flirt-on-demand` 等，社群也有從 libc/openssl 生成 sig 的工具鏈（`plb` / `pcf` / `sigmake`）。

### r2 zignatures

radare2 的對應機制，可以在不買 IDA 的情況下做類似的事。

```bash
# 建立 zignature：對 libc 本身分析，生成 .sdb
r2 -A /lib/x86_64-linux-gnu/libc.so.6
# 在 r2 裡：
# za  => 列出所有 zignature 命令
# zaf => 對每個函式生成 zignature（基於 bytes + 圖結構）
# zos libc.sdb  => 存到檔案

# 對 target binary 套用：
r2 -A target_O0
# zls libc.sdb  => 載入 sdb
# z/  => 對所有未命名函式執行比對
```

r2 zignature 支援多種匹配模式：byte 模式（`zb`）、graph 模式（`zg`，基於 CFG 統計特徵）、offset 模式（`zo`）。graph 模式對最佳化的抵抗力比 byte 模式好。

實際效果：在同版本 libc 上，graph 模式的命中率很高；跨版本（比如 glibc 2.35 vs 2.39）會有漏識，因為函式結構確實有改變。

---

## 機器學習方法（概念）

近年學術界和工業界都在把 ML 引入函式相似度：

- **Gemini（2018, Xu et al.）**：把基本塊的特徵向量（指令數、call 數、運算類型分佈等）跑 graph neural network，學出函式的 embedding，用 cosine similarity 比較。
- **BinBert / SAFE / PalmTree**：把 assembly 指令序列當 token，用 BERT 類模型學 embedding。
- **jTrans（2022）**：專門針對 transformer 處理 jump-aware assembly。

這些方法的優點是對混淆和跨架構（x86 vs ARM 相同邏輯函式）有更強的抵抗力。缺點是：需要 GPU 推論才夠快、需要訓練資料、black-box 難以解釋為什麼「像」。

工作流上，ML 方法通常用在「已經縮小範圍，要在幾百個候選函式裡找最像的那個」，而不是第一步掃全圖。

---

## 實際工作流：拿到陌生 stripped binary 怎麼做

1. **先跑 FLIRT / zignature**：載入對應版本的 libc、常見庫簽名，讓工具自動命名能認出的函式。命中 50-80% 的函式是正常的（取決於靜態連結比例）。

2. **看未命名函式的數量**：如果只剩 20-30 個 `sub_XXXX`，那才是你真正的戰場。

3. **用 xref 和 call graph 縮小範圍**：`main` 呼叫誰？哪個函式被最多地方呼叫（可能是工具函式或關鍵業務邏輯）？

4. **對可疑函式做相似度搜尋**（可選）：懷疑某函式是已知 CVE 的漏洞版本，用 BinDiff 或 bindiff-like 工具跟已知有漏洞的 binary 比對。

5. **跨 binary variant 搜尋**（可選）：懷疑同廠商多個產品共用同一段程式碼，批次提取所有 binary 的函式指紋，建索引，查詢相似函式。

第 1 步沒做就跳進去逆，是在浪費自己的時間。

---

## 對比與取捨

| 方法 | 跨最佳化等級 | 跨編譯器 | 跨架構 | 速度 | 對混淆抵抗力 |
|------|-------------|---------|--------|------|------------|
| Bytewise hash | 廢 | 廢 | 廢 | 極快 | 無 |
| Normalized bytewise（wildcard reloc） | 部分 | 廢 | 廢 | 快 | 低 |
| Mnemonic sequence hash | 部分 | 部分 | 廢 | 快 | 低 |
| FLIRT / zignature（byte 模式） | 低 | 低 | 無 | 快 | 低 |
| CFG 統計特徵（基本塊數/邊數/back-edge） | 中 | 中 | 中 | 中 | 中 |
| BinDiff CFG isomorphism | 高 | 中 | 低 | 慢 | 中 |
| ML embedding（Gemini/BinBert 類） | 高 | 高 | 高 | 慢（需 GPU） | 高 |

「跨架構」是指 x86 上的 `sum_array` 和 ARM64 上的 `sum_array` 是否能配對。只有 ML 方法能做到，因為它學的是語義而不是語法。

---

## 踩雷集錦

### 1. 以為 zignature 命中就是確定答案

zignature 的 graph 模式比對的是統計特徵（基本塊數量、edge 數、指令類型分佈），不是精確匹配。有一定的 false positive rate，尤其是短函式（基本塊少於 3 個的函式，特徵向量幾乎都一樣）。命中之後還是要看一眼確認邏輯是否合理，特別是被命名為 `memcpy` 但大小看起來不像的情況。

### 2. 把 inline 函式誤認為業務邏輯

`-O2` 以上，`strlen`、`memset` 等小函式會被 inline。你在 binary 裡看到一段「長得很像 strlen 的 mnemonic 序列」卻不是獨立函式，FLIRT 識別不到（因為 FLIRT 需要函式邊界），你就會把它當成業務邏輯去逆。

判斷方式：看是否有標準的函式 prologue/epilogue，以及 call graph 是否有入邊。沒有入邊的「函式」可能只是 IDA/r2 錯誤識別的片段。

### 3. 跨版本 libc 的 false negative

你拿的 binary 靜態連結了 glibc 2.31，但你的 zignature 資料庫是從 glibc 2.39 生成的。`printf` 的內部實作在這兩個版本之間差異夠大，graph 模式也識別不到。結果你去逆一個完整的 `printf` 實作，花了兩個小時才發現。

解法：多準備幾個版本的 signature 資料庫，或先用 strings 找線索估計 libc 版本。

### 4. BinDiff 在嚴重混淆過的 binary 上失效

BinDiff 的錨點擴散邏輯依賴正確的 call graph。如果混淆器做了間接呼叫（`call [rax]`、`jmp [table+rbx*8]`）把所有直接 call 換掉，call graph 幾乎是空的，BinDiff 找不到錨點就無法擴散，命中率趨近於零。這種情況只能靠 ML embedding 或人工分析。

### 5. 在 stripped binary 上用 bytewise hash 建「已知惡意函式庫」

某些威脅情資 feed 提供已知惡意函式的 md5。對手只要換一個 compiler flag 重新 build，所有 hash 全失效。這種情報的實際用途非常有限，不要過度依賴。

---

## 進階：再往深一層

### MinHash / LSH 加速大規模搜尋

你有 10,000 個 binary 各有幾百個函式，要兩兩比對，暴力算相似度是 O(N²)。LSH（Locality Sensitive Hashing）把相似的函式投影到相同的 bucket，把搜尋複雜度降到亞線性。`bindiff-databas` 和 `BinaryAI`（騰訊）等工業工具都在底層用這類加速。

### 語義等價 vs 結構相似

CFG 相似度量的是「結構像不像」，但兩個函式可能：
- 結構完全不同，但語義等價（一個用迴圈，一個被展開成直線 + SIMD）
- 結構非常像，但語義不同（同一個 loop skeleton，但計算不同）

真正的語義等價需要 symbolic execution 或 theorem prover，計算成本極高。Gemini / BinBert 類 ML 方法嘗試近似語義，但仍然是啟發式的。

### r2 批次簽名流程

對一批 firmware binary 做批次指紋掃描：

```bash
# 對每個 binary 提取函式 graph hash，存到 sqlite
for f in firmware/*.bin; do
    r2 -A -q -c 'afl~?; zaf; zos /tmp/sigs/$(basename $f).sdb; q' "$f" 2>/dev/null
done
# 然後用 r2 或自己的 Python 做 cross-binary 搜尋
```

這是在沒有 IDA Pro 授權時做 malware 家族分析的實用替代方案。

---

## 本章重點整理

- 函式指紋的第一用途是「剔除噪音」：讓工具認出 libc/zlib，你才能專心逆業務邏輯。
- Bytewise hash 在跨編譯選項時直接廢掉，任何工作流都不應該只靠這個。
- Mnemonic sequence hash 稍好，但對大幅最佳化（inline、loop unroll）仍然失效。
- CFG 相似度（BinDiff、r2 graph zignature）是目前實用和精度的最佳平衡點。
- ML embedding 方法（Gemini/BinBert）在跨架構和重度混淆場景有明顯優勢，但需要推論基礎設施。
- FLIRT 簽名（IDA）和 r2 zignature 是逆向工作流第一步必用的工具，但要理解其侷限（版本敏感、inline 識別不到、短函式 false positive）。
- 指紋命中不等於確定，短函式尤其要二次確認。

---

## 自我檢核

1. 說明為什麼對同一個 C 函式用 `-O0` 和 `-O2` 編譯後，bytewise md5 一定不同，mnemonic sequence hash 大概率也不同，但 CFG 結構可能保留部分相似性。
2. r2 zignature 的 graph 模式比對的是什麼？它的 false positive 在什麼情況下最嚴重？
3. BinDiff 的錨點擴散邏輯依賴什麼資訊？哪種混淆技術最容易讓它失效？
4. 你拿到一個懷疑靜態連結了 zlib 1.2.11 的 stripped binary，描述你的第一步操作流程（工具、指令、判斷標準）。
5. 解釋「結構相似但語義不同」和「結構不同但語義等價」各自用什麼方法才能正確處理。

---

## 延伸閱讀

1. **BinDiff 論文與開源碼**：Halvar Flake 的原始論文「Structural Comparison of Executable Objects」（2004）定義了 CFG isomorphism 搜尋的框架，Google 開源版在 github.com/google/bindiff，值得直接看 diffing 演算法實作。

2. **Gemini 論文**：Xu et al., "Neural Network-based Graph Embedding for Cross-Platform Binary Code Similarity Detection"（CCS 2017），是把 GNN 引入 binary 相似度的奠基工作，讀這篇之前先確定你理解 CFG 和基本塊概念。

3. **radare2 文件：Zignatures**：`r2` 官方 book（book.rada.re）的 Signatures 章節，含完整的 `za`/`zb`/`zg`/`zo` 命令說明和從標準庫生成 sdb 的步驟，是不用 IDA 的人的必讀。

4. **FLIRT 技術細節**：Ilfak Guilfanov 的 "Fast Library Identification and Recognition Technology"（1990s，IDA 內建文件），現在 Hex-Rays 官網有 `.pat`/`.sig` 格式的詳細說明，配合 `flair` 工具集（`pcf`、`sigmake`）可以自己建 signature。

---

本章把函式指紋的技術層次從最粗（bytewise）到最精（ML embedding）都走過一遍，重點是在逆向工作流裡，它是「縮小戰場」的前置步驟，而不是目的本身。真正要分析的業務邏輯，要等雜訊被清掉之後才看得清楚。

→ [Ch 29 反編譯到可編譯：lifting 與重建](./29-decompile-to-recompilable-lifting.md)
