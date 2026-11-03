# Ch 40 — Binary 相似度的圖比對

> **目標**：從真實 ELF 抽出每個函式的 CFG，親手實作 WL subtree kernel、結構特徵、graph edit distance 三種相似度，實測它們在「同一份 C、不同最佳化等級」與「同一最佳化等級、原始碼小改（patch diffing）」兩種情境下的表現，並理解每一種為什麼會失敗。
>
> **環境**：WSL Ubuntu 22.04 的 gcc 11.4.0（`gcc (Ubuntu 11.4.0-1ubuntu1~22.04.3)`）產生 x86-64 ELF；分析端是 Windows 11 上的 Python 3.13.12、NetworkX 3.7、capstone 5.0.9、pyelftools 0.33。不同版本的 gcc 會產生不同的機器碼，你重現時 block 數字可能不同，但本章的結論（哪種方法在哪種情境好用）應該不變。

## 為什麼需要這個？

Binary 相似度在資安裡有三個典型用途：

1. **Patch diffing**：廠商發了安全更新，只給 binary。比對新舊版找出「改了哪些函式」，就能定位被修的漏洞。
2. **函式庫識別**：一個 stripped 的韌體裡，哪些函式其實是 OpenSSL 1.0.1 的 `memcpy` 包裝？認出來就不用逆向它。
3. **漏洞搜尋**：已知某個函式有漏洞，在其他 binary 裡找長得像的函式。

最早的做法是**比 bytes**：雜湊每個函式的機器碼。問題是只要前面多一個函式，後面所有 `call` 和 RIP-relative 位址都會變，bytes 全部不同。下一步是**比指令序列**（去掉位址後的 opcode n-gram），但編譯器換個暫存器分配、重排兩條指令，序列就不同了。

2004–2005 年 Halvar Flake（Thomas Dullien）與 Rolf Rolles 提出用**圖結構**比對：一個執行檔是「call graph 的節點各自掛著一張 CFG」，暫存器和位址會變，但「這個函式有幾個 basic block、幾個迴圈、呼叫了誰」相對穩定。這就是 BinDiff 的起點。之後十幾年的研究（包括 2017 年以後用神經網路的方法，本課不涵蓋）都是在問同一個問題：**哪些圖的性質在編譯器改寫下是穩定的？**

這一章用一個可控的實驗回答它：同一份 C，用 gcc `-O0`、`-O1`、`-O2`、`-O3` 各編一次，看三種圖相似度能不能把同名函式配回來。

## 先建立直覺

把函式的 CFG 想成一棟建築的平面圖：房間是 basic block，門是控制流邊。

- `-O0` 的編譯器像照著設計圖一比一蓋：每個 `if` 一個房間、每個迴圈一個走廊，變數全放在記憶體裡。
- `-O2` 的編譯器像會改設計的建築師：把迴圈「轉一圈」（loop rotation，把條件判斷移到迴圈尾巴）、把小函式直接蓋進呼叫者（inlining）、把常數算好（constant folding），甚至把遞迴改成迴圈。

```text
-O0 gcd：                 -O2 gcd：
  [entry]──→[cond]──┐        [entry: test b]──┬──→[ret a]
              ↑  │  │                         ↓
           [body]←┘ └→[ret]               [loop body]──┐
              └──→↑                          ↑   │      │
                                             └───┘      └→[ret]
```

兩張圖都是 4 個 block、4 條邊，但形狀不同。問題變成：**「形狀像」要怎麼量化？**三個候選答案：

| 方法 | 比什麼 | 直覺 |
| --- | --- | --- |
| 結構特徵 | block 數、邊數、迴圈數、call 數…… | 比平面圖的「統計摘要」 |
| WL subtree kernel | 每個房間「往外看 h 步」的鄰域長相，數各種鄰域出現幾次 | 比「局部格局」的分布 |
| Graph edit distance | 最少要改幾個房間、幾扇門，才能把 A 改成 B | 比「改建成本」 |

> 如果你對 WL 測試還不熟，先回看 [Ch 37 — 圖同構與 Weisfeiler–Leman](./37-graph-isomorphism-wl.md)；本章用的 DFS 邊分類（back edge）在 [Ch 13](./13-directed-graphs-scc-dag.md)。

## 範例一：從 ELF 切出函式與 CFG

實驗對象是 11 個控制流形狀各不相同的小函式：

```c
/* simlab.c —— Ch 40 的實驗對象：幾個控制流形狀各不相同的小函式。 */
#include <stdio.h>
#include <string.h>

int checksum(const unsigned char *buf, int n) {
    unsigned int s = 0;
    for (int i = 0; i < n; i++)
        s = (s << 5) + s + buf[i];
    return (int)(s & 0x7fffffff);
}

int find_max(const int *a, int n) {
    if (n <= 0) return -1;
    int m = a[0];
    for (int i = 1; i < n; i++)
        if (a[i] > m) m = a[i];
    return m;
}

int classify(int x) {
    if (x < 0) return -1;
    if (x == 0) return 0;
    if (x < 10) return 1;
    if (x < 100) return 2;
    return 3;
}

int parse_digits(const char *s) {
    int v = 0, sign = 1;
    if (*s == '-') { sign = -1; s++; }
    while (*s) {
        if (*s < '0' || *s > '9') return -9999;
        v = v * 10 + (*s - '0');
        s++;
    }
    return sign * v;
}

int gcd(int a, int b) {
    while (b != 0) {
        int t = a % b;
        a = b;
        b = t;
    }
    return a;
}

int fib(int n) {
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}

void bubble_sort(int *a, int n) {
    for (int i = 0; i < n - 1; i++)
        for (int j = 0; j < n - 1 - i; j++)
            if (a[j] > a[j + 1]) {
                int t = a[j]; a[j] = a[j + 1]; a[j + 1] = t;
            }
}

int count_words(const char *s) {
    int in = 0, c = 0;
    for (; *s; s++) {
        if (*s == ' ' || *s == '\t' || *s == '\n') in = 0;
        else if (!in) { in = 1; c++; }
    }
    return c;
}

int binsearch(const int *a, int n, int key) {
    int lo = 0, hi = n - 1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        if (a[mid] == key) return mid;
        if (a[mid] < key) lo = mid + 1; else hi = mid - 1;
    }
    return -1;
}

int apply_op(int op, int a, int b) {
    switch (op) {
    case 0: return a + b;
    case 1: return a - b;
    case 2: return a * b;
    case 3: return b ? a / b : 0;
    case 4: return a & b;
    case 5: return a | b;
    case 6: return a ^ b;
    default: return 0;
    }
}

int main(int argc, char **argv) {
    int a[8] = {5, 3, 9, 1, 7, 2, 8, 6};
    bubble_sort(a, 8);
    printf("%d %d %d %d\n", checksum((const unsigned char *)"abc", 3), find_max(a, 8),
           classify(argc), parse_digits(argc > 1 ? argv[1] : "42"));
    printf("%d %d %d %d %d\n", gcd(84, 36), fib(10), count_words("a bb  ccc"),
           binsearch(a, 8, 7), apply_op(argc, 6, 3));
    return 0;
}
```

在 WSL 裡編四個版本（工作目錄放在 Windows 也可以，WSL 透過 `/mnt/c/...` 讀寫）：

```bash
wsl -e bash -lc 'for o in 0 1 2 3; do gcc -O$o -o simlab_O$o simlab.c; done; ./simlab_O0; ./simlab_O2'
```

```text
108966 9 1 42
12 55 3 5 3
108966 9 1 42
12 55 3 5 3
```

兩個版本輸出相同，語意一致。沒有加 `-s`，所以 `.symtab` 還在，我們用符號表找函式邊界——這是作弊，但能讓我們專注在圖的部分；stripped binary 的函式邊界辨識本身是另一個難題（見踩雷 5）。

CFG 的建法是教科書的 **leader 演算法**：

1. 函式入口是 leader；
2. 每個跳躍的**目標**是 leader；
3. 每個跳躍或 `ret` 的**下一條指令**是 leader；
4. 從一個 leader 到下一個 leader 之前是一個 basic block；
5. 連邊：條件跳躍有兩條出邊（目標、fall-through），無條件 `jmp` 一條，`ret` 沒有，其他 block 一條 fall-through。

`call` **不**結束 block：從函式內 CFG 的角度，call 之後控制流會回來（除非被呼叫者不返回，見踩雷 3）。

```python
# bincfg.py
"""Ch 40 範例一：用 pyelftools 找函式、capstone 反組譯、切 basic block、建 CFG。"""
import sys
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_GRP_JUMP, CS_GRP_RET, CS_GRP_CALL
from capstone.x86 import X86_OP_IMM

SKIP = {"_start", "_init", "_fini", "deregister_tm_clones", "register_tm_clones",
        "__do_global_dtors_aux", "frame_dummy"}          # 編譯器/C runtime 產生的函式


def functions(path):
    """回傳 {name: (addr, bytes)}：.symtab 裡在 .text 的 FUNC 符號。"""
    with open(path, "rb") as f:
        elf = ELFFile(f)
        text = elf.get_section_by_name(".text")
        base, data = text["sh_addr"], text.data()
        out = {}
        for sym in elf.get_section_by_name(".symtab").iter_symbols():
            if sym["st_info"]["type"] != "STT_FUNC" or sym["st_size"] == 0:
                continue
            a, n = sym["st_value"], sym["st_size"]
            if base <= a < base + len(data) and sym.name not in SKIP:
                out[sym.name] = (a, data[a - base: a - base + n])
        return out


def build_cfg(addr, code):
    """線性掃描反組譯 → 找 leader → 切 block → 連邊。回傳 (succ, blocks)。
    succ: dict[block_start, set[block_start]]；blocks: dict[block_start, list[insn]]。"""
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    insns = [i for i in md.disasm(code, addr) if not i.mnemonic.startswith("nop")]
    # nop 是對齊用的填充；拿掉之後，指向 nop 的跳躍目標要對到下一條真正的指令
    real = [i.address for i in insns]
    end = addr + len(code)

    def snap(t):
        for a in real:
            if a >= t:
                return a
        return None
    leaders = {addr}
    for i, ins in enumerate(insns):
        if ins.group(CS_GRP_JUMP) or ins.group(CS_GRP_RET):
            if i + 1 < len(insns):
                leaders.add(insns[i + 1].address)          # 跳躍/返回的下一條是新 block
            if ins.group(CS_GRP_JUMP) and ins.operands and ins.operands[0].type == X86_OP_IMM:
                t = ins.operands[0].imm
                if addr <= t < end and snap(t) is not None:
                    leaders.add(snap(t))                   # 函式內的跳躍目標
    blocks, cur = {}, None
    for ins in insns:
        if ins.address in leaders:
            cur = ins.address
            blocks[cur] = []
        blocks[cur].append(ins)
    starts = sorted(blocks)
    succ = {b: set() for b in starts}
    for k, b in enumerate(starts):
        last = blocks[b][-1]
        nxt = starts[k + 1] if k + 1 < len(starts) else None
        if last.group(CS_GRP_RET) or last.mnemonic == "hlt":
            continue
        if last.group(CS_GRP_JUMP):
            op = last.operands[0] if last.operands else None
            if op is not None and op.type == X86_OP_IMM and addr <= op.imm < end and snap(op.imm):
                succ[b].add(snap(op.imm))
            if last.mnemonic != "jmp" and nxt is not None:  # 條件跳躍：還有 fall-through
                succ[b].add(nxt)
            continue
        if nxt is not None:
            succ[b].add(nxt)                               # 一般 fall-through
    return succ, blocks


def reachable(succ, entry):
    seen, st = {entry}, [entry]
    while st:
        for w in succ[st.pop()] - seen:
            seen.add(w)
            st.append(w)
    return seen


def n_calls(blocks):
    return sum(1 for bl in blocks.values() for ins in bl if ins.group(CS_GRP_CALL))


if __name__ == "__main__":
    path = sys.argv[1]
    for name, (a, code) in sorted(functions(path).items()):
        succ, blocks = build_cfg(a, code)
        e = sum(map(len, succ.values()))
        print(f"{name:14} bytes={len(code):4} insns={sum(map(len, blocks.values())):3} "
              f"blocks={len(succ):3} edges={e:3} calls={n_calls(blocks)} "
              f"unreachable={len(succ) - len(reachable(succ, a))}")
```

```text
$ python bincfg.py simlab_O0
apply_op       bytes= 139 insns= 46 blocks= 13 edges= 14 calls=0 unreachable=8
binsearch      bytes= 155 insns= 52 blocks=  9 edges= 11 calls=0 unreachable=0
bubble_sort    bytes= 239 insns= 65 blocks=  9 edges= 11 calls=0 unreachable=0
checksum       bytes=  91 insns= 29 blocks=  4 edges=  4 calls=0 unreachable=0
classify       bytes=  70 insns= 23 blocks= 10 edges= 13 calls=0 unreachable=0
count_words    bytes= 108 insns= 33 blocks= 10 edges= 14 calls=0 unreachable=0
fib            bytes=  63 insns= 23 blocks=  4 edges=  4 calls=2 unreachable=0
find_max       bytes= 115 insns= 36 blocks=  9 edges= 11 calls=0 unreachable=0
gcd            bytes=  49 insns= 19 blocks=  4 edges=  4 calls=0 unreachable=0
main           bytes= 394 insns= 98 blocks=  6 edges=  7 calls=13 unreachable=0
parse_digits   bytes= 137 insns= 44 blocks=  9 edges= 12 calls=0 unreachable=0
$ python bincfg.py simlab_O2
apply_op       bytes= 105 insns= 34 blocks= 11 edges=  4 calls=0 unreachable=8
apply_op.cold  bytes=   3 insns=  2 blocks=  1 edges=  0 calls=0 unreachable=0
binsearch      bytes=  55 insns= 21 blocks=  8 edges= 12 calls=0 unreachable=0
bubble_sort    bytes=  64 insns= 22 blocks=  8 edges= 11 calls=0 unreachable=0
checksum       bytes=  59 insns= 18 blocks=  5 edges=  5 calls=0 unreachable=0
classify       bytes=  43 insns= 15 blocks=  6 edges=  7 calls=0 unreachable=0
count_words    bytes=  82 insns= 28 blocks= 10 edges= 16 calls=0 unreachable=0
fib            bytes= 632 insns=160 blocks= 35 edges= 52 calls=1 unreachable=0
find_max       bytes=  62 insns= 19 blocks=  6 edges=  8 calls=0 unreachable=0
gcd            bytes=  47 insns= 16 blocks=  4 edges=  4 calls=0 unreachable=0
main           bytes= 360 insns= 91 blocks=  9 edges= 11 calls=10 unreachable=0
parse_digits   bytes=  93 insns= 27 blocks=  9 edges= 10 calls=0 unreachable=0
```

這兩張表已經把本章的困難攤在眼前：

- **`fib` 從 4 個 block 長成 35 個**。gcc `-O2` 把 `fib(n-1) + fib(n-2)` 的其中一個遞迴改寫成迴圈並展開，只剩 1 個 `call`。原始碼裡沒有迴圈，`-O2` 版有 9 條 back edge（下一節的輸出）。
- **`main` 的 call 從 13 個變 10 個**。用 `objdump -d` 看 `-O2` 的 `main`，`find_max`、`checksum`、`gcd` 的呼叫不見了：`checksum("abc", 3)` 和 `gcd(84, 36)` 的參數都是常數，被整個算掉；`find_max` 被 inline。`printf` 也變成了 `__printf_chk`（Ubuntu 的 gcc 預設開啟 `_FORTIFY_SOURCE`）。
- **`apply_op` 有 8 個不可達 block**。`switch` 被編成跳躍表：`notrack jmp *%rcx`，目標位址在執行期從 `.rodata` 讀出。我們的 leader 演算法只認得立即數目標，看不到這 8 條邊。`-O2` 還把 `default` 分支搬到另一個符號 `apply_op.cold`（冷熱分離），於是 `ja` 跳到函式外，連那條邊也沒了。

## 底層機制：三種相似度的數學

### 結構特徵

最便宜的方法：把每個 CFG 摘要成一個向量 `[block 數, 邊數, cyclomatic number, back edge 數, call 數, ret 數]`。

**Cyclomatic number** `M = E − N + 2` 來自 McCabe 1976，圖論上它是連通圖的 **circuit rank**（cycle space 的維度）加 1：連通圖的生成樹有 `N − 1` 條邊，每多一條邊就多一個獨立的環，所以獨立環數是 `E − (N − 1)`。把 exit 連回 entry 之後就是 `E − N + 2`。注意這個推導**假設圖連通**——`apply_op` 的 `-O2` 版 `M = 4 − 11 + 2 = −5`，負數就是圖不連通（跳躍表的邊沒解析）的症狀。

**Back edge** 是 DFS 時指向仍在遞迴堆疊上的祖先的邊（[Ch 13](./13-directed-graphs-scc-dag.md) 的邊分類）。在可歸約（reducible）CFG 上，back edge 的目標就是迴圈 header；一個迴圈可以有多條 back edge（例如 `binsearch` 的 `lo = mid + 1` 和 `hi = mid - 1` 兩個分支都跳回條件判斷，`-O0` 版有 2 條 back edge 但只有 1 個迴圈）。

### WL subtree kernel

[Ch 37](./37-graph-isomorphism-wl.md) 的 WL 測試（1-WL，color refinement）反覆執行：

```text
label_{i+1}(v) = HASH( label_i(v), {{ label_i(w) : w ∈ N(v) }} )
```

`{{ }}` 是 multiset。Shervashidze 等人（JMLR 2011）的觀察是：WL 每一輪產生的標籤可以當成**特徵**。令 `φ(G)` 為「第 0…h 輪中，每種標籤在 G 裡出現幾次」的計數向量，WL subtree kernel 定義為

```text
k(G, H) = ⟨φ(G), φ(H)⟩
```

我們用正規化版本 `k(G,H) / sqrt(k(G,G) · k(H,H))`，也就是兩個計數向量的 cosine，值在 `[0, 1]`。第 i 輪的一個標籤，精確地編碼了「以 v 為根、深度 i 的展開樹（unfolding tree）」的形狀，所以叫 subtree kernel。

CFG 是有向圖，入邊和出邊意義不同（「誰跳到我」vs「我跳到誰」），所以我們的 WL 把後繼與前驅的 multiset 分開放進新標籤。

這裡有兩個性質值得完整證明，因為後面的實驗依賴它們。

**命題 1（是合法的 kernel）**。對任意圖 `G1, …, Gm`，Gram 矩陣 `K[i][j] = k(Gi, Gj)` 是半正定的。

**證明**。對任意實數 `c1, …, cm`，`Σ_i Σ_j ci cj K[i][j] = Σ_i Σ_j ci cj ⟨φ(Gi), φ(Gj)⟩ = ⟨Σ_i ci φ(Gi), Σ_j cj φ(Gj)⟩ = ‖Σ_i ci φ(Gi)‖² ≥ 0`。∎

這就是為什麼它能直接丟進 SVM 之類的方法；正規化版本是 `φ(G)/‖φ(G)‖` 的內積，同樣半正定。

**命題 2（同構 ⇒ 特徵相同）**。若 `G ≅ H`（保持初始標籤的同構，對有向圖保持邊方向），則對所有 h，`φ(G) = φ(H)`。

**證明**。設 `π: V(G) → V(H)` 是同構。對輪數 i 歸納證明 `label_i(v) = label_i(π(v))` 對所有 v 成立。`i = 0`：同構保持初始標籤。設對 i 成立。因為 π 是同構，`π` 把 v 的後繼集合雙射到 `π(v)` 的後繼集合，前驅亦同；由歸納假設，這兩個集合的標籤 multiset 相等。新標籤是（舊標籤，後繼 multiset，前驅 multiset）的函數，三個參數都相等，所以 `label_{i+1}(v) = label_{i+1}(π(v))`。於是每一輪的標籤 multiset 在 π 下相等，計數向量相同。∎

反方向不成立：Ch 37 已經看過 1-WL 分不開某些非同構的正則圖。對 CFG 而言這通常不是問題（CFG 很少是正則圖），真正的問題在下一節：**編譯器產生的兩張圖本來就不同構**。

### Graph edit distance

GED 是「把 G 改成 H 所需的最小編輯成本」，編輯操作是插入/刪除/替換節點與邊，每個操作有成本（本章：節點標籤相同的配對免費，其餘每個操作成本 1）。當成本函數滿足非負、對稱、三角不等式時，GED 是圖空間上的度量（metric）。

計算 GED 是 NP-hard：它把圖同構（GED = 0 ⟺ 同構）與最大共同子圖當成特例。NetworkX 的 `graph_edit_distance` 引用的是 Abu-Aisheh 等人（ICPRAM 2015）的精確演算法，屬於分支界限式的搜尋；最壞情況是指數時間。它的 `timeout` 參數到時間就回傳**目前找到的最佳值**——這只是上界，不保證是真正的 GED。

## 範例二：跨最佳化等級配對

實驗設計：對每個 `-O0` 函式，在 `-O2` 的所有函式中找相似度最高的，看是不是同名的那一個（top-1 準確率）。這模擬「我有一個 debug build 的函式，要在 release build 裡找到它」。

```python
# binsim.py（與 bincfg.py、四個 simlab_O* 執行檔同目錄）
"""Ch 40 範例二：跨最佳化等級的函式配對——WL subtree kernel、結構特徵、度數序列。
與 bincfg.py、四個 simlab_O* 執行檔同目錄。"""
import math
from collections import Counter
import networkx as nx
from capstone import CS_GRP_CALL, CS_GRP_JUMP, CS_GRP_RET
from bincfg import functions, build_cfg


def block_label(bl):
    """粗粒度 block 標籤：結尾類型 + 是否含 call。刻意不用指令數、暫存器，因為最佳化會改它們。"""
    last = bl[-1]
    if last.group(CS_GRP_RET):
        t = "ret"
    elif last.group(CS_GRP_JUMP):
        t = "jmp" if last.mnemonic == "jmp" else "jcc"
    else:
        t = "fall"
    return t + ("+call" if any(i.group(CS_GRP_CALL) for i in bl) else "")


def wl_features(succ, labels, h=3):
    """有向 WL：新標籤 = (舊標籤, 後繼標籤 multiset, 前驅標籤 multiset)。回傳所有輪的標籤計數。"""
    pred = {v: [] for v in succ}
    for u in succ:
        for w in succ[u]:
            pred[w].append(u)
    lab = dict(labels)
    feats = Counter((0, l) for l in lab.values())
    for it in range(1, h + 1):
        lab = {v: (lab[v], tuple(sorted(lab[w] for w in succ[v])), tuple(sorted(lab[w] for w in pred[v])))
               for v in succ}
        # 用 hash 壓縮巢狀 tuple；同一次執行內 hash 穩定，兩邊特徵可比較
        lab = {v: hash(x) for v, x in lab.items()}
        feats.update((it, l) for l in lab.values())
    return feats


def cosine(a, b):
    dot = sum(a[k] * b.get(k, 0) for k in a)
    return dot / math.sqrt(sum(x * x for x in a.values()) * sum(x * x for x in b.values()))


def back_edges(succ, entry):
    """迭代式 DFS：指向「仍在堆疊上」節點的邊是 back edge（Ch 13 的 DFS 邊分類）。"""
    on, done, cnt = {entry}, set(), 0
    stack = [(entry, iter(sorted(succ[entry])))]
    while stack:
        v, it = stack[-1]
        w = next(it, None)
        if w is None:
            stack.pop(); on.discard(v); done.add(v)
        elif w in on:
            cnt += 1
        elif w not in done:
            on.add(w); stack.append((w, iter(sorted(succ[w]))))
    return cnt


def struct_vec(succ, blocks, entry):
    """結構特徵：block 數、邊數、cyclomatic number、back edge 數、call 數、ret 數。"""
    n, e = len(succ), sum(map(len, succ.values()))
    calls = sum(1 for bl in blocks.values() for i in bl if i.group(CS_GRP_CALL))
    rets = sum(1 for bl in blocks.values() if bl[-1].group(CS_GRP_RET))
    return [n, e, e - n + 2, back_edges(succ, entry), calls, rets]


def struct_sim(a, b):
    return 1 - sum(abs(x - y) / max(x, y, 1) for x, y in zip(a, b)) / len(a)


def degseq_sim(sa, sb):
    """(in, out) 度數對的 multiset Jaccard。"""
    def ds(succ):
        indeg = Counter(w for u in succ for w in succ[u])
        return Counter((indeg[v], len(succ[v])) for v in succ)
    A, B = ds(sa), ds(sb)
    return sum((A & B).values()) / sum((A | B).values())


def load(path):
    out = {}
    for name, (a, code) in functions(path).items():
        if name.endswith(".cold"):
            continue
        succ, blocks = build_cfg(a, code)
        out[name] = dict(entry=a, succ=succ, blocks=blocks,
                         wl0=wl_features(succ, {v: "B" for v in succ}),
                         wl=wl_features(succ, {v: block_label(blocks[v]) for v in succ}),
                         sv=struct_vec(succ, blocks, a))
    return out


def evaluate(F0, F2, sim):
    names = sorted(F0)
    hit, ranks = 0, []
    for n in names:
        scores = sorted(((sim(F0[n], F2[m]), m) for m in F2), reverse=True)
        best = scores[0][1]
        hit += best == n
        ranks.append([m for _, m in scores].index(n) + 1)
    return hit, len(names), ranks


if __name__ == "__main__":
    methods = {
        "WL 無標籤 h=3": lambda a, b: cosine(a["wl0"], b["wl0"]),
        "WL block標籤 h=3": lambda a, b: cosine(a["wl"], b["wl"]),
        "結構特徵向量": lambda a, b: struct_sim(a["sv"], b["sv"]),
        "度數序列 Jaccard": lambda a, b: degseq_sim(a["succ"], b["succ"]),
    }
    pairs = [("simlab_O0", "simlab_O2"), ("simlab_O1", "simlab_O2"), ("simlab_O2", "simlab_O3")]
    F = {p: load(p) for p in ["simlab_O0", "simlab_O1", "simlab_O2", "simlab_O3"]}
    print("top-1 正確配對數        O0→O2   O1→O2   O2→O3")
    for k, f in methods.items():
        row = []
        for a, b in pairs:
            hit, tot, _ = evaluate(F[a], F[b], f)
            row.append(f"{hit:>2}/{tot}")
        print(f"{k:<18}" + "".join(f"{r:>8}" for r in row))
    F0, F2 = F["simlab_O0"], F["simlab_O2"]
    print("-O0 → -O2 結構特徵 [blocks, edges, cyclomatic, back, calls, rets]")
    for n in sorted(F0):
        print(f"  {n:12} {F0[n]['sv']} → {F2[n]['sv']}")

    # 對拍：圖同構 ⇒ WL 特徵相同（WL 是同構的必要條件，Ch 37）。用 NetworkX 的 VF2 檢查 -O2 vs -O3。
    F3 = F["simlab_O3"]

    def to_nx(f):
        G = nx.DiGraph((u, w) for u in f["succ"] for w in f["succ"][u])
        G.add_nodes_from(f["succ"])
        nx.set_node_attributes(G, {v: block_label(f["blocks"][v]) for v in f["succ"]}, "lab")
        return G
    iso = [n for n in F2 if nx.is_isomorphic(to_nx(F2[n]), to_nx(F3[n]), node_match=lambda a, b: a["lab"] == b["lab"])]
    print("-O2/-O3 同構的函式:", sorted(iso), "→ WL 特徵全部相同:", all(F2[n]["wl"] == F3[n]["wl"] for n in iso))
```

```text
top-1 正確配對數        O0→O2   O1→O2   O2→O3
WL 無標籤 h=3            0/11    5/11    9/11
WL block標籤 h=3        1/11    5/11    9/11
結構特徵向量                3/11    4/11    9/11
度數序列 Jaccard          2/11    5/11    9/11
-O0 → -O2 結構特徵 [blocks, edges, cyclomatic, back, calls, rets]
  apply_op     [13, 14, 3, 0, 0, 1] → [11, 4, -5, 0, 0, 8]
  binsearch    [9, 11, 4, 2, 0, 1] → [8, 12, 6, 2, 0, 1]
  bubble_sort  [9, 11, 4, 2, 0, 1] → [8, 11, 5, 2, 0, 1]
  checksum     [4, 4, 2, 1, 0, 1] → [5, 5, 2, 1, 0, 2]
  classify     [10, 13, 5, 0, 0, 1] → [6, 7, 3, 0, 0, 2]
  count_words  [10, 14, 6, 1, 0, 1] → [10, 16, 8, 2, 0, 1]
  fib          [4, 4, 2, 0, 2, 1] → [35, 52, 19, 9, 1, 1]
  find_max     [9, 11, 4, 1, 0, 1] → [6, 8, 4, 1, 0, 1]
  gcd          [4, 4, 2, 1, 0, 1] → [4, 4, 2, 1, 0, 2]
  main         [6, 7, 3, 0, 13, 1] → [9, 11, 4, 2, 10, 1]
  parse_digits [9, 12, 5, 1, 0, 1] → [9, 10, 3, 1, 0, 3]
-O2/-O3 同構的函式: ['apply_op', 'bubble_sort', 'checksum', 'classify', 'count_words', 'fib', 'gcd', 'parse_digits'] → WL 特徵全部相同: True
```

這張表是本章最重要的實驗結果，要仔細讀：

1. **準確率幾乎完全由「編譯器改了多少」決定，而不是由方法決定**。`-O2 → -O3` 四種方法都 9/11，因為 11 個函式裡有 8 個 CFG 完全同構（最後一行）；`-O0 → -O2` 四種方法都很差，最好的結構特徵也只有 3/11。
2. **WL 在跨最佳化時最脆弱**。WL 比的是局部格局的**精確**匹配：一個 block 的鄰域多一條邊，它在第 1 輪以後的所有標籤都變了。`-O0 → -O2` 時 gcc 把 `classify` 最後一個 `if` 改成無分支的 `setg`、把 `gcd` 的迴圈轉成「先測一次、尾端再測」的 do-while 形狀（`objdump -d` 可以看到），幾乎每個 block 的鄰域都變，WL 特徵幾乎沒有交集。
3. **結構特徵最「粗」，所以最耐改**。`gcd`、`binsearch`、`bubble_sort` 的 back edge 數在兩個版本一致，block 數也接近。但它在 `fib`（4 → 35 block）這種被重寫的函式上完全沒救。
4. **最後一行是 WL 實作的對拍**：NetworkX 的 VF2 判定為同構的 8 對函式，我們的 WL 特徵全部相同，符合命題 2。反過來 `-O2/-O3` 的 9/11 比 8 對同構多一個，那個函式是 WL 特徵不同但仍然是最相似的候選。

WL 在跨最佳化表現差，不代表它沒用——它要用在對的地方。

## 範例三：patch diffing——WL 的主場

換一個情境：同一個最佳化等級，原始碼小改。我們做三個改動：`checksum` 的位移從 5 改成 7（不改控制流）、`classify` 多一個 `if (x < 1000) return 3;`、`parse_digits` 迴圈裡多一個溢位檢查 `if (v > 100000) return -9998;`。用 `-O2` 編成 `simlab_v2_O2`。把 `simlab.c` 複製成 `simlab_v2.c`，只改這三處：

```diff
-        s = (s << 5) + s + buf[i];
+        s = (s << 7) + s + buf[i];
@@ classify
     if (x < 100) return 2;
-    return 3;
+    if (x < 1000) return 3;
+    return 4;
@@ parse_digits
         v = v * 10 + (*s - '0');
+        if (v > 100000) return -9998;
         s++;
```

```bash
wsl -e bash -lc 'gcc -O2 -o simlab_v2_O2 simlab_v2.c'
```

```python
# patchdiff.py（與 bincfg.py、binsim.py 同目錄）
"""Ch 40 範例四：patch diffing——同樣 -O2、原始碼小改，WL 能不能找出改了哪些函式？"""
import hashlib
from binsim import load, cosine
from bincfg import functions

A, B = load("simlab_O2"), load("simlab_v2_O2")
rawA, rawB = functions("simlab_O2"), functions("simlab_v2_O2")
print(f"{'函式':12} {'bytes 相同':>8} {'WL 相似度':>9} {'blocks':>8}")
for n in sorted(A):
    same_bytes = hashlib.sha256(rawA[n][1]).digest() == hashlib.sha256(rawB[n][1]).digest()
    sim = cosine(A[n]["wl"], B[n]["wl"])
    print(f"{n:12} {str(same_bytes):>8} {sim:9.3f} {len(A[n]['succ']):>4}→{len(B[n]['succ'])}")
```

```text
函式           bytes 相同    WL 相似度   blocks
apply_op        False     1.000   11→11
binsearch        True     1.000    8→8
bubble_sort      True     1.000    8→8
checksum        False     1.000    5→5
classify        False     0.666    6→7
count_words      True     1.000   10→10
fib              True     1.000   35→35
find_max         True     1.000    6→6
gcd              True     1.000    4→4
main            False     1.000    9→9
parse_digits    False     0.908    9→11
```

逐列看：

- **`apply_op`、`main`：bytes 不同、WL = 1**。原始碼沒改，但前面的函式變長了，`main` 裡的 `call` 相對位移、`apply_op` 的 RIP-relative 跳躍表位址都跟著變。byte 雜湊會誤報它們被改了，WL 正確地說「結構沒變」。
- **`classify`、`parse_digits`：WL < 1**。這兩個是真的被改了控制流的函式，WL 精準地把它們挑出來；`classify` 降得比較多（6 → 7 block，相對變化大）。
- **`checksum`：bytes 不同、WL = 1**。這是 WL 的盲點：`<< 5` 改成 `<< 7` 只改了一條指令的立即數，CFG 完全不變。如果漏洞修補是「把 `<` 改成 `<=`」或「改一個緩衝區大小常數」，純結構比對看不到。實務工具（BinDiff）會在結構配對之後，再對配上的 block 比指令內容，把這種「結構相同、內容不同」的函式標成 changed。

所以 WL 的定位是：**在大部分函式沒變的情況下，快速把「結構相同」的函式配起來，縮小人要看的範圍**。這正是 patch diffing 的需求。

## 範例四：Graph edit distance 的代價

GED 給的是最直觀的答案（「差幾步」），代價是計算時間。

```python
# binged.py（與 bincfg.py、binsim.py 同目錄）
"""Ch 40 範例三：graph edit distance——精確但昂貴。與 bincfg.py、binsim.py 同目錄。"""
import time
import networkx as nx
from binsim import load, block_label


def to_nx(F):
    G = nx.DiGraph()
    for v in F["succ"]:
        G.add_node(v, lab=block_label(F["blocks"][v]))
    G.add_edges_from((u, w) for u in F["succ"] for w in F["succ"][u])
    return G


F0, F2 = load("simlab_O0"), load("simlab_O2")
same = lambda a, b: a["lab"] == b["lab"]
print("同名函式 -O0 vs -O2 的 GED（節點標籤要相同才算免費配對，timeout=10s）")
for name in ["gcd", "checksum", "find_max", "classify", "parse_digits", "binsearch"]:
    G, H = to_nx(F0[name]), to_nx(F2[name])
    t0 = time.perf_counter()
    d = nx.graph_edit_distance(G, H, node_match=same, timeout=10)
    dt = time.perf_counter() - t0
    print(f"  {name:12} |V|={G.number_of_nodes():2}/{H.number_of_nodes():2} GED={d}  {dt:6.2f}s")

# 用 GED 做配對：只取兩個版本都 ≤ 10 blocks 的函式，每對 timeout=1s（逾時回傳的是目前找到的上界）
from binsim import cosine
names = sorted(n for n in F0 if len(F0[n]["succ"]) <= 10 and len(F2[n]["succ"]) <= 10)
print("候選函式:", names)
t0 = time.perf_counter()
hit_ged = hit_wl = 0
for n in names:
    G = to_nx(F0[n])
    ged = {m: nx.graph_edit_distance(G, to_nx(F2[m]), node_match=same, timeout=1) for m in names}
    wl = {m: cosine(F0[n]["wl"], F2[m]["wl"]) for m in names}
    hit_ged += min(ged, key=ged.get) == n
    hit_wl += max(wl, key=wl.get) == n
print(f"在這 {len(names)} 個函式上：GED top-1 = {hit_ged}/{len(names)}，WL top-1 = {hit_wl}/{len(names)}，"
      f"GED 總耗時 {time.perf_counter() - t0:.1f}s")
```

實測（AMD Ryzen 7 5700G、Windows 11、Python 3.13.12）：

```text
同名函式 -O0 vs -O2 的 GED（節點標籤要相同才算免費配對，timeout=10s）
  gcd          |V|= 4/ 4 GED=4.0    0.66s
  checksum     |V|= 4/ 5 GED=5.0    0.01s
  find_max     |V|= 9/ 6 GED=12.0    0.93s
  classify     |V|=10/ 6 GED=16.0    2.61s
  parse_digits |V|= 9/ 9 GED=8.0    1.79s
  binsearch    |V|= 9/ 8 GED=13.0   10.00s
候選函式: ['binsearch', 'bubble_sort', 'checksum', 'classify', 'count_words', 'find_max', 'gcd', 'main', 'parse_digits']
在這 9 個函式上：GED top-1 = 1/9，WL top-1 = 3/9，GED 總耗時 48.5s
```

三個觀察：

- **9 個節點對 8 個節點就撞上 10 秒的 timeout**。`binsearch` 的 13.0 是逾時時的最佳值，只是上界。同一個查詢在不同機器上可能回傳不同數字，這讓 GED 配對的結果**依賴機器速度**——寫報告時要記下 timeout 設定。
- **GED 的配對準確率沒有比較好**（1/9 vs WL 3/9），卻慢了好幾個數量級。原因和 WL 一樣：`-O0` 與 `-O2` 的 CFG 本來就差很多，「差最少的」不一定是同一個函式。精確計算一個沒有區辨力的距離沒有幫助。
- 這就是為什麼實務工具不用精確 GED：BinDiff 這類工具用的是先以穩定特徵（呼叫關係、字串參考、結構雜湊）找「錨點」配對，再沿 call graph 向外傳播的近似演算法。

## 對比與取捨

| 方法 | 每對時間 | 跨最佳化（O0→O2） | 同最佳化小改（patch diff） | 盲點 |
| --- | --- | --- | --- | --- |
| bytes 雜湊 | 線性 | 全錯 | 位址一動就誤報 | 位址、暫存器 |
| 結構特徵向量 | `O(V + E)` | 本實驗最好（3/11），仍很差 | 改動不影響計數時看不到 | 太粗，很多函式撞在一起 |
| 度數序列 | `O(V + E)` | 2/11 | 同上 | 丟掉了連接關係 |
| WL subtree kernel | `O(h · E)` | 1/11 | 精準挑出控制流改動 | 非結構改動、重寫型最佳化 |
| 精確 GED | 指數 | 1/9（且常逾時） | 能量化「改了多少」 | 規模，> 10 節點就不實際 |

**為什麼不直接用圖同構？** 因為同構是「全有或全無」，任何一條邊不同就是「不同構」，沒有程度之分；而且即使同構測試在實務上很快，它回答的不是我們要的問題。WL kernel 和 GED 都是把同構「軟化」成連續的相似度，前者從「局部結構的計數」軟化，後者從「編輯成本」軟化。

## 踩雷集錦

1. **以為 CFG 是 binary 的固有性質**：CFG 是**分析工具建出來的**，不同工具（甚至同一工具不同設定）對跳躍表、`noreturn` 函式、尾呼叫、冷熱分離的處理不同，建出的圖不同。比較兩個 binary 時，兩邊一定要用同一個 CFG 建構器，否則你量到的是工具差異。
2. **跳躍表沒解析，cyclomatic number 變負數**：`apply_op` 的 `-O2` 版 `M = −5`，8 個 block 不可達。看到負的 cyclomatic number 或大量不可達 block，先懷疑間接跳躍沒解析，而不是懷疑公式。修法是辨識 `cmp $N; ja default; lea table(%rip); movslq (table, idx, 4); add; jmp *reg` 這個樣式，從 `.rodata` 讀出 N + 1 個相對位移。
3. **`call` 之後不一定會回來**：`call __stack_chk_fail`、`call abort`、`call exit` 不返回，後面的指令可能是另一個函式或填充。把 call 當 fall-through 會連出一條假邊。成熟的工具維護一份 `noreturn` 函式清單。
4. **把 `-O0` 的直覺帶到 `-O2`**：`fib` 從 4 個 block 長成 35 個、`main` 裡的 `checksum` 呼叫消失。最佳化會 inline、常數折疊、改寫遞迴、展開迴圈；「原始碼的一個函式」和「binary 的一個函式」不是一對一。
5. **依賴符號表**：本章用 `.symtab` 找函式邊界。release binary 通常 `strip` 過，函式邊界要靠 `endbr64` 樣式、call 目標、`.eh_frame` 的 FDE 等線索推測，那是 binary 分析的另一個大題目，錯了會連帶讓 CFG 全錯。
6. **把 timeout 後的 GED 當成精確值**：`nx.graph_edit_distance(..., timeout=t)` 在逾時時回傳的是目前最佳值，只是上界；而且結果依賴機器速度。

## 進階：再往深一層

- **Call graph 上的傳播**：BinDiff 的核心不是單一函式的相似度，而是「如果 A 配 A'，那 A 呼叫的函式也應該配 A' 呼叫的函式」。這是在兩張 call graph 之間找一個近似的保結構映射，把單函式相似度當成初始分數，沿邊傳播。
- **語意特徵**：block 內的常數、字串參考、呼叫的外部函式名（例如 `__printf_chk`）在最佳化下比結構穩定。把它們放進 WL 的初始標籤，跨最佳化準確率會大幅提升——動手練習 3 讓你驗證。
- **動態方法**：對兩個函式餵相同的輸入、比較輸出與記憶體副作用（I/O 等價），完全繞過結構差異，代價是需要能執行、要處理副作用。
- **學習式方法**：2017 年之後大量研究用圖神經網路學 CFG 的嵌入向量（例如 Gemini，Xu 等，CCS 2017）。本課不涵蓋 GNN，但你現在知道它們想解決的問題：手工特徵在跨最佳化時失效。已知 message-passing GNN 區分圖的能力不超過 1-WL（Xu 等 “How Powerful are Graph Neural Networks?”，ICLR 2019；Morris 等，AAAI 2019），這是理解那些論文的切入點。

## 動手練習

1. 把 `wl_features` 的 h 從 3 改成 0、1、2，重跑 `binsim.py`。在 `O1→O2` 這一欄，h 越大準確率越高還是越低？用「標籤越來越精確」解釋你看到的趨勢。
2. 實作踩雷 2 的跳躍表解析，讓 `apply_op` 的 `-O0`、`-O2` 兩版都沒有不可達 block。驗證方式：`objdump -d -j .rodata` 對照你讀出的目標位址。
3. 把 `block_label` 改成「結尾類型 + block 內呼叫的外部符號名」（PLT 名稱要從 `.rela.plt` 解析，或先用 `objdump -d` 的註解對照），看 `O0→O2` 的準確率能不能突破 3/11。
4. 把 `simlab.c` 用 `gcc -O2 -fno-inline` 再編一次，和 `-O2` 比較 `main` 的結構特徵。哪些差異消失了？

## 本章重點整理

- 從 ELF 建 CFG：符號表找邊界、capstone 反組譯、leader 演算法切 block；跳躍表、`noreturn`、冷熱分離是三個主要的建圖陷阱。
- 結構特徵粗但耐改；WL subtree kernel 是合法的半正定 kernel，同構必給相同特徵，對「局部結構」精確，所以在 patch diffing 很準、在跨最佳化很脆；GED 直觀但 NP-hard，超過約 10 個節點就不實際。
- 實驗結論：配對難度主要由編譯器改寫的程度決定；同一最佳化等級的小改動，WL 能精準挑出控制流有變的函式，但看不到只改常數的修補。

## 自我檢核

- [ ] 不看筆記，說出 leader 演算法的三條規則，並解釋為什麼 `call` 不結束 basic block。
- [ ] 用自己的話證明「同構 ⇒ WL 特徵相同」，並解釋為什麼這個方向的定理對 patch diffing 有用、反方向不成立對它影響不大。
- [ ] 如果有人說「GED 最精確，所以應該都用 GED」，你會用本章哪兩個實測數字反駁？
- [ ] 看到 cyclomatic number 是負數時，你第一個懷疑什麼？
- [ ] 想一個你遇過的 patch：它是改控制流還是改常數？純結構比對抓不抓得到？

## 延伸閱讀

### 論文

- **[Weisfeiler-Lehman Graph Kernels](https://www.jmlr.org/papers/v12/shervashidze11a.html)** — Nino Shervashidze、Pascal Schweitzer、Erik Jan van Leeuwen、Kurt Mehlhorn、Karsten M. Borgwardt，JMLR 12（2011），pp. 2539–2561
  - **核心貢獻**：把 WL 測試的每輪標籤變成特徵，定義 WL subtree / edge / shortest-path kernel 家族，時間與邊數成線性。
  - **讀哪裡**：WL 測試回顧那節，以及 WL subtree kernel 的定義與複雜度分析；實驗那節可略。
  - **和本章的關聯**：`wl_features` 就是 subtree kernel 的實作，差別是我們把前驅與後繼分開處理有向圖。
- **[Graph-based Comparison of Executable Objects](https://www.zynamics.com/downloads/bindiffsstic05-1.pdf)** — Thomas Dullien、Rolf Rolles，SSTIC（2005）
  - **核心貢獻**：把執行檔視為「call graph 節點掛著 CFG」的圖中圖，用結構特徵找初始配對再沿 call graph 傳播，是 BinDiff 的設計基礎。
  - **讀哪裡**：整篇不長，重點在配對特徵的選擇與傳播策略。
  - **和本章的關聯**：本章的結構特徵向量就是它的單函式版本；「進階」那節的 call graph 傳播是它的核心。
- **[A Survey of Binary Code Similarity](https://arxiv.org/abs/1909.11424)** — Irfan Ul Haq、Juan Caballero，arXiv 1909.11424（2019）
  - **核心貢獻**：系統性整理 61 種 binary 相似度方法，依應用、特徵、比對方式、評估方法分類。
  - **讀哪裡**：分類維度那節（看你的方法落在哪一格），以及評估方法的討論（為什麼不同論文的準確率不能直接比較）。
  - **前提**：本章的三種方法就夠看懂大部分內容。

### 官方文件

- **[NetworkX：graph_edit_distance](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.similarity.graph_edit_distance.html)**
  - **讀哪裡**：`timeout`、`upper_bound`、`node_match` 參數說明；References 列出的 Abu-Aisheh 等（ICPRAM 2015）是演算法來源。
  - **注意**：`optimize_graph_edit_distance` 是會逐步產生更好上界的 generator，適合在時間預算內取「目前最好」。

### repo 內延伸

- **[security/reverse_engineering](../../security/reverse_engineering/README.md)**：工具端的對照。[Binary 相似度與指紋](../../security/reverse_engineering/28-binary-similarity-fingerprinting.md) 講 BinDiff/Diaphora 的實務流程，[Patch diffing](../../security/reverse_engineering/27-patch-diffing.md) 是範例三的完整版，[辨識控制流](../../security/reverse_engineering/05-recognizing-control-flow.md) 能幫你看懂 `-O2` 的迴圈轉換。

二進位相似度的實驗只有十幾個函式；真正的程式分析圖、AD 圖、網路圖動輒百萬節點，Python 字典會先被記憶體和 cache miss 打垮。下一章從資料結構與硬體的角度重新看「一張圖在記憶體裡長什麼樣」。

→ [Ch 41 大圖工程：CSR、cache 與平行 BFS](./41-large-graph-engineering.md)
