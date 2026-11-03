# Ch 6 — 對稱計數：Burnside 與 Pólya

> **目標**：學會用群作用描述「哪些東西算同一個」，證明軌道—穩定子定理與 Burnside 引理，用它們算項鍊、手環與 n 點無標號圖的數量，並和暴力窮舉、Ch 0 的 graph atlas、OEIS 三方核對。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。

## 為什麼需要這個？

[Ch 0](./00-environment-setup.md) 留下一個問題：graph atlas 告訴我們 7 點無標號圖有 1044 種，但這個數字是**把圖一張張畫出來**數的。8 點以上 atlas 就沒有了。有沒有辦法不列舉就算出來？

直接的想法是「標號圖數除以重新標號的方法數」：`2^C(n,2) / n!`。n = 4 時是 `64 / 24 ≈ 2.67`，連整數都不是，真值是 11。錯在哪？因為**不同的圖有不同的對稱性**。空圖不論怎麼重新標號都是自己，只對應 1 張標號圖；一條路徑 `0-1-2-3` 對應 12 張標號圖；沒有任何對稱性的圖才對應完整的 `n!` 張。除以同一個數必然錯。

這個問題在系統與資安工作裡也會遇到，只是換了名字：

- 去重：兩個 CFG 只差在基本塊編號，算不算同一個函式？（[逆向工程的 binary 相似度](../../security/reverse_engineering/28-binary-similarity-fingerprinting.md)要先回答這個。）
- 搜尋空間縮減：SAT 編碼裡有對稱性時（例如鴿籠問題的鴿子可互換），solver 會反覆探索等價的分支。symmetry breaking 的第一步是知道「到底有幾個本質不同的情況」。
- 化學與材料：同分異構物計數，正是 Pólya 在 1937 年發展這套理論的動機。

Burnside 引理給出正確的做法：**不是除以群的大小，而是對群的每個元素數「它固定了幾個東西」，再取平均。**

## 先建立直覺

用 2 種顏色（黑 ●、白 ○）塗 4 顆珠子的項鍊，旋轉後相同的算同一條。16 種著色會分成幾組？

```text
組 1：○○○○                          （1 種著色）
組 2：●○○○ ○●○○ ○○●○ ○○○●            （4 種）
組 3：●●○○ ○●●○ ○○●● ●○○●            （4 種）
組 4：●○●○ ○●○●                      （2 種）
組 5：●●●○ ●●○● ●○●● ○●●●            （4 種）
組 6：●●●●                          （1 種）
                                     共 16 種，6 組
```

答案是 6，而 `16 / 4 = 4` 是錯的。組的大小是 1、4、4、2、4、1，不一樣大。大小比 4 小的組，正是**有對稱性**的著色：`●○●○` 轉 2 格回到自己，`○○○○` 轉幾格都是自己。

Burnside 的想法：與其數組，不如數「(旋轉, 被它固定的著色)」這種配對。

```text
旋轉 0 格（不動）：16 種著色全被固定
旋轉 1 格        ：只有 ○○○○、●●●● 被固定        → 2
旋轉 2 格        ：○○○○ ●●●● ●○●○ ○●○● 被固定    → 4
旋轉 3 格        ：只有 ○○○○、●●●●                → 2
                                       平均 = (16 + 2 + 4 + 2) / 4 = 6
```

平均值剛好是組數。下面要證明這不是巧合。

## 群作用的最小語彙

**群（group）**：這裡用到的只有一種具體的群——一個集合 X 上的**置換群**：一組 X 到自己的雙射，包含恆等映射、對合成封閉、每個元素有反函數。例子：

- `C_n`：n 顆珠子的旋轉，n 個元素（轉 0、1、...、n − 1 格）。
- `D_n`：旋轉加上翻轉，2n 個元素（手環可以翻面）。
- `S_n`：n 個頂點的所有重新標號，`n!` 個元素。

**群作用（group action）**：群 G 作用在一個「物件集合」Ω 上（例如所有著色、所有標號圖），每個 `g ∈ G` 把物件 x 送到 `g·x`，滿足恆等元不動、`(gh)·x = g·(h·x)`。

三個核心定義：

| 名詞 | 定義 | 項鍊的例子 |
| --- | --- | --- |
| 軌道（orbit） | `Orb(x) = {g·x : g ∈ G}`，所有和 x「算同一個」的物件 | 上面的每一組 |
| 穩定子（stabilizer） | `Stab(x) = {g ∈ G : g·x = x}`，讓 x 不動的群元素 | `●○●○` 的穩定子是 {轉 0, 轉 2} |
| 不動點集（fixed set） | `Fix(g) = {x ∈ Ω : g·x = x}`，被 g 固定的物件 | 轉 2 格的不動點集有 4 個 |

我們要數的「本質不同的物件」，就是**軌道的個數**。

## 軌道—穩定子定理

**定理 6.1**：對任何有限群 G 作用在 Ω 上，以及任何 `x ∈ Ω`，`|Orb(x)| · |Stab(x)| = |G|`。

**證明**：令 `H = Stab(x)`。對 `g, h ∈ G`：

```text
g·x = h·x  ⟺  (h⁻¹g)·x = x  ⟺  h⁻¹g ∈ H
```

固定一個 g，滿足 `h·x = g·x` 的 h 恰好是 `{g k⁻¹ : k ∈ H}`——由上式，`h⁻¹g = k ∈ H` 等價於 `h = g k⁻¹`，而 `k ↦ g k⁻¹` 是單射，所以這個集合恰有 `|H|` 個元素。也就是說，「把 g 送到 `g·x`」這個從 G 到 `Orb(x)` 的映射是滿射，而且每個軌道元素恰有 `|H|` 個原像。由除法原理（[Ch 3](./03-counting-basics.md)），`|Orb(x)| = |G| / |H|`。證畢。

直覺：對稱性越高（穩定子越大），軌道越小。`○○○○` 的穩定子是整個 `C_4`，軌道只有 1 個元素；`●●○○` 沒有非平凡的旋轉對稱，軌道有 4 個元素。

```python
"""軌道—穩定子定理：在 C_6 作用於 2 色 6 珠項鍊上逐一檢查。"""
from itertools import product

n = 6


def rot(s, k):
    return s[k:] + s[:k]


for s in [(0,) * 6, (0, 1) * 3, (0, 0, 1) * 2, (0, 0, 0, 1, 1, 1), (0, 0, 0, 0, 0, 1)]:
    orbit = {rot(s, k) for k in range(n)}
    stab = [k for k in range(n) if rot(s, k) == s]
    print(f"{''.join(map(str, s))}: |軌道|={len(orbit)}  穩定子={stab}  乘積={len(orbit) * len(stab)}")
assert all(len({rot(s, k) for k in range(n)}) * sum(rot(s, k) == s for k in range(n)) == n
           for s in product((0, 1), repeat=n))
print("全部 64 種著色：|軌道| × |穩定子| = 6")
```

輸出：

```text
000000: |軌道|=1  穩定子=[0, 1, 2, 3, 4, 5]  乘積=6
010101: |軌道|=2  穩定子=[0, 2, 4]  乘積=6
001001: |軌道|=3  穩定子=[0, 3]  乘積=6
000111: |軌道|=6  穩定子=[0]  乘積=6
000001: |軌道|=6  穩定子=[0]  乘積=6
全部 64 種著色：|軌道| × |穩定子| = 6
```

## Burnside 引理

**定理 6.2（Burnside 引理）**：有限群 G 作用在有限集合 Ω 上，軌道數

```text
|Ω / G| = (1 / |G|) Σ_{g∈G} |Fix(g)|
```

**證明**：雙重計數（[Ch 2](./02-proof-toolbox.md) 技巧一）集合 `P = {(g, x) ∈ G × Ω : g·x = x}`。

- 按 g 分組：`|P| = Σ_g |Fix(g)|`。
- 按 x 分組：`|P| = Σ_x |Stab(x)| = Σ_x |G| / |Orb(x)|`（定理 6.1）。

把第二種寫法按軌道分組。一個軌道 O 裡有 `|O|` 個 x，每個 x 貢獻 `|G| / |O|`（同一軌道的元素軌道相同），所以整個軌道貢獻 `|G|`。因此 `|P| = |G| × (軌道數)`。兩式相等，除以 `|G|`。證畢。

證明裡的關鍵一步：每個軌道對總和的貢獻**剛好是 |G|**，不論它多大多小。對稱性高的軌道元素少但每個 `|Stab|` 大，對稱性低的元素多但每個 `|Stab|` 小，乘起來扯平。這就是為什麼取平均會給出正確的軌道數，而直接除以 `|G|` 不會。

> 歷史註記：這個引理以 Burnside 命名，因為他 1897 年的群論教科書寫了它；但它更早出現在 Cauchy 與 Frobenius 的工作裡，所以也有人叫它「不是 Burnside 的引理」（the lemma that is not Burnside's）。

## 應用一：項鍊與手環

**項鍊**：q 種顏色、n 顆珠子，`C_n` 作用。旋轉 k 格把位置 i 送到 `i + k (mod n)`。一個著色被它固定，若且唯若沿著 `i → i + k → i + 2k → ...` 的每個循環都同色。

**引理 6.3**：旋轉 k 格把 n 個位置分成 `gcd(n, k)` 個循環，每個長度 `n / gcd(n, k)`。

**證明**：位置 i 的循環長度是最小的 `t ≥ 1` 使 `tk ≡ 0 (mod n)`，也就是 `n | tk`。令 `d = gcd(n, k)`，`n | tk` 等價於 `(n/d) | t (k/d)`，而 `n/d` 和 `k/d` 互質，所以等價於 `(n/d) | t`，最小的 t 是 `n/d`。每個循環長度都是 `n/d`，總共 `n / (n/d) = d` 個循環。證畢。

所以 `|Fix(轉 k 格)| = q^gcd(n,k)`，代入 Burnside：

```text
項鍊數 N(n, q) = (1/n) Σ_{k=0}^{n−1} q^gcd(n, k)
```

**手環**：再加上 n 個翻轉，群變成 `D_n`（2n 個元素）。翻轉的循環結構：

- n 奇數：每條對稱軸穿過一顆珠子和對面兩顆之間，固定 1 顆、其餘配成 `(n−1)/2` 對，共 `(n+1)/2` 個循環。n 條軸都是這樣。
- n 偶數：n/2 條軸穿過兩顆珠子（`n/2 + 1` 個循環），n/2 條軸穿過兩個間隙（`n/2` 個循環）。

```python
"""項鍊與手環：Burnside 公式 vs 暴力標準形。"""
from itertools import product
from math import gcd


def rotations(s):
    return [s[k:] + s[:k] for k in range(len(s))]


def brute(n, q, flips):
    seen = set()
    for s in product(range(q), repeat=n):
        group = rotations(s)
        if flips:
            group += rotations(s[::-1])
        seen.add(min(group))          # 標準形：整個軌道裡字典序最小的那個
    return len(seen)


def necklace(n, q):
    return sum(q ** gcd(n, k) for k in range(n)) // n


def bracelet(n, q):
    rot = sum(q ** gcd(n, k) for k in range(n))
    if n % 2:
        ref = n * q ** ((n + 1) // 2)
    else:
        ref = (n // 2) * q ** (n // 2 + 1) + (n // 2) * q ** (n // 2)
    return (rot + ref) // (2 * n)


print(" n | 2色項鍊 暴力/公式 | 2色手環 暴力/公式 | 3色項鍊 暴力/公式")
for n in range(1, 11):
    a, b = brute(n, 2, False), necklace(n, 2)
    c, d = brute(n, 2, True), bracelet(n, 2)
    e, f = (brute(n, 3, False), necklace(n, 3)) if n <= 8 else ("-", necklace(n, 3))
    print(f"{n:>2} | {a:>6} {b:>6}    | {c:>6} {d:>6}    | {e!s:>6} {f:>6}")
print("錯誤做法 q^n / n （n=4, q=2）:", 2 ** 4 / 4, "  真值:", necklace(4, 2))
```

輸出：

```text
 n | 2色項鍊 暴力/公式 | 2色手環 暴力/公式 | 3色項鍊 暴力/公式
 1 |      2      2    |      2      2    |      3      3
 2 |      3      3    |      3      3    |      6      6
 3 |      4      4    |      4      4    |     11     11
 4 |      6      6    |      6      6    |     24     24
 5 |      8      8    |      8      8    |     51     51
 6 |     14     14    |     13     13    |    130    130
 7 |     20     20    |     18     18    |    315    315
 8 |     36     36    |     30     30    |    834    834
 9 |     60     60    |     46     46    |      -   2195
10 |    108    108    |     78     78    |      -   5934
錯誤做法 q^n / n （n=4, q=2）: 4.0   真值: 6
```

三欄都和 OEIS 一致：2 色項鍊 A000031（`2, 3, 4, 6, 8, 14, 20, 36, 60, 108`）、2 色手環 A000029（`2, 3, 4, 6, 8, 13, 18, 30, 46, 78`）、3 色項鍊 A001867（`3, 6, 11, 24, 51, 130, 315, 834, 2195, 5934`）。項鍊和手環在 n ≤ 5 時相同，n = 6 開始分岔：`001011` 和它的鏡像 `001101` 旋轉後不同、翻轉後相同。

注意暴力程式的「標準形」寫法：每個著色算出它整個軌道裡字典序最小的代表，丟進 set。這是**不依賴 Burnside** 的獨立計數，所以兩者一致才有意義。暴力的成本是 `q^n × |G|`，隨 n 指數成長；表格在 3 色 n ≥ 9 只列公式，讓輸出保持精簡，你可以把上限改大，看暴力時間怎麼成長。

## 應用二：n 點無標號圖

現在回到 Ch 0 的問題。

- **物件集合 Ω**：頂點集 `{0..n−1}` 上的所有標號圖，`2^C(n,2)` 張。等價地，每個頂點對 `{u, v}` 塗「有邊」或「沒邊」兩色。
- **群 G** = `S_n`，重新標號。置換 σ 把圖 H 送到 `σ(H)`，`{u, v}` 是 H 的邊若且唯若 `{σ(u), σ(v)}` 是 `σ(H)` 的邊。
- **軌道** = 同構類：H 和 H' 同構，按定義就是存在 σ 使 `σ(H) = H'`。

σ 作用在頂點上，同時誘導出一個作用在 `C(n,2)` 個**頂點對**上的置換。一張圖被 σ 固定，若且唯若它的邊集在這個誘導置換下不變，也就是**每個頂點對循環要嘛全是邊、要嘛全不是邊**。所以：

```text
|Fix(σ)| = 2^c(σ)，  c(σ) = σ 在頂點對上誘導的循環個數

無標號圖數 g(n) = (1 / n!) Σ_{σ∈S_n} 2^c(σ)
```

先用最直接的方式：列舉 `S_n` 的每個置換，追蹤它在頂點對上的循環。

```python
"""無標號圖數 = (1/n!) Σ_σ 2^{c(σ 作用在頂點對上的循環數)}，直接列舉 S_n。"""
from itertools import permutations, combinations
from math import factorial
import time


def cycles_on_pairs(perm):
    n = len(perm)
    seen = set()
    c = 0
    for p in combinations(range(n), 2):
        if p in seen:
            continue
        c += 1
        q = p
        while q not in seen:
            seen.add(q)
            a, b = perm[q[0]], perm[q[1]]
            q = (a, b) if a < b else (b, a)
    return c


for n in range(1, 9):
    t = time.perf_counter()
    s = sum(2 ** cycles_on_pairs(p) for p in permutations(range(n)))
    print(f"n={n}: {s // factorial(n):>6}   (列舉 {factorial(n)} 個置換, {time.perf_counter() - t:.2f}s)")
```

輸出（耗時在 Ryzen 7 5700G 上單次執行；你的機器會不同）：

```text
n=1:      1   (列舉 1 個置換, 0.00s)
n=2:      2   (列舉 2 個置換, 0.00s)
n=3:      4   (列舉 6 個置換, 0.00s)
n=4:     11   (列舉 24 個置換, 0.00s)
n=5:     34   (列舉 120 個置換, 0.00s)
n=6:    156   (列舉 720 個置換, 0.00s)
n=7:   1044   (列舉 5040 個置換, 0.02s)
n=8:  12346   (列舉 40320 個置換, 0.22s)
```

n ≤ 7 和 Ch 0 的 atlas 計數 `1, 1, 2, 4, 11, 34, 156, 1044` 完全一致；n = 8 的 12346 是 atlas 沒有的，和 OEIS A000088 一致。0.22 秒算出 12346 種圖，對比之下，用標號窮舉 + 同構判定分類 n = 8 要處理 `2^28` 張圖。

### 獨立驗證：不靠 atlas 的同構分類

atlas 和 Burnside 都可能「剛好對」。再用第三種完全獨立的方法：把所有標號圖按同構分類，標準形取「所有重新標號中字典序最小的邊集」。順便驗證軌道—穩定子定理在圖上的形式：一個同構類的大小是 `n! / |Aut(H)|`，其中 `Aut(H)`（自同構群）正是 H 的穩定子。

```python
"""獨立於 atlas 的暴力：把所有標號圖按同構分類（標準形 = 所有重新標號中最小的邊集）。"""
from itertools import combinations, permutations
from math import factorial

for n in range(1, 6):
    pairs = list(combinations(range(n), 2))
    perms = list(permutations(range(n)))
    classes = {}
    for mask in range(1 << len(pairs)):
        E = [p for i, p in enumerate(pairs) if mask >> i & 1]
        images = [tuple(sorted(tuple(sorted((s[u], s[v]))) for u, v in E)) for s in perms]
        canon = min(images)
        aut = sum(1 for img in images if img == tuple(sorted(E)))   # 穩定子 = 自同構群
        classes.setdefault(canon, aut)
    orbit_sum = sum(factorial(n) // a for a in classes.values())
    print(f"n={n}: 同構類 {len(classes):>3}；Σ n!/|Aut| = {orbit_sum:>5} = 2^C(n,2) = {2 ** len(pairs)}")
```

輸出：

```text
n=1: 同構類   1；Σ n!/|Aut| =     1 = 2^C(n,2) = 1
n=2: 同構類   2；Σ n!/|Aut| =     2 = 2^C(n,2) = 2
n=3: 同構類   4；Σ n!/|Aut| =     8 = 2^C(n,2) = 8
n=4: 同構類  11；Σ n!/|Aut| =    64 = 2^C(n,2) = 64
n=5: 同構類  34；Σ n!/|Aut| =  1024 = 2^C(n,2) = 1024
```

`Σ n!/|Aut| = 2^C(n,2)` 說的是：所有軌道的大小加起來等於物件總數。這把開頭那個錯誤直覺修正成正確的形式：每張無標號圖 H 對應 `n! / |Aut(H)|` 張標號圖，而不是 `n!` 張。

### 按循環型加總：算到 n = 15

`n!` 個置換在 n = 12 就是 4.8 億個，直接列舉不可行。但 `c(σ)` 只依賴 σ 的**循環型**——長度 1 的循環有 `m_1` 個、長度 2 的有 `m_2` 個……——而循環型就是 n 的整數分割（[Ch 5](./05-recurrences-generating-functions.md)），p(15) 只有 176 種。

需要兩個事實：

**(a) 循環型為 `(m_1, m_2, ...)` 的置換有 `n! / Π_k (k^(m_k) m_k!)` 個。**理由：把 n 個元素排成一列（`n!` 種），依序切成循環。每個長度 k 的循環有 k 種寫法（從哪個元素開始寫），同長度的 `m_k` 個循環之間有 `m_k!` 種排列順序，這些都對應同一個置換，除法原理。

**(b) 頂點對上的循環數 `c(σ)`**，按頂點對的兩端落在哪些頂點循環分三類：

- 兩端在**同一個**長度 k 的循環：`C(k, 2)` 對。k 奇數時全部組成長度 k 的循環，共 `(k−1)/2` 個；k 偶數時，「相距 k/2」的 k/2 對組成一個長度 k/2 的循環，其餘組成 `(k−2)/2` 個長度 k 的循環，共 `k/2` 個。兩種情況都是 `⌊k/2⌋`。
- 兩端在**兩個不同但等長**的 k-循環：`k^2` 對，每個循環長度 k，共 k 個。這樣的循環對有 `C(m_k, 2)` 組。
- 兩端在長度 i 和 j（i ≠ j）的循環：`ij` 對，每個循環長度 `lcm(i, j)`，共 `ij / lcm(i, j) = gcd(i, j)` 個。

```python
"""同一個公式，改成按循環型（整數分割）加總，n 可以到 15 以上。"""
from math import factorial, gcd, comb
from fractions import Fraction


def partitions(n, max_part=None):
    if max_part is None:
        max_part = n
    if n == 0:
        yield {}
        return
    for k in range(min(n, max_part), 0, -1):
        for rest in partitions(n - k, k):
            d = dict(rest)
            d[k] = d.get(k, 0) + 1
            yield d


def num_perms(m):
    """循環型為 m（m[k] = 長度 k 的循環個數）的置換有 n!/Π(k^{m_k} m_k!) 個。"""
    n = sum(k * c for k, c in m.items())
    denom = 1
    for k, c in m.items():
        denom *= k ** c * factorial(c)
    return factorial(n) // denom


def pair_cycles(m):
    ks = sorted(m)
    c = sum(m[k] * (k // 2) for k in ks)                  # 同一個循環內部的頂點對
    c += sum(k * m[k] * (m[k] - 1) // 2 for k in ks)      # 兩個等長循環之間
    for i in range(len(ks)):                              # 兩個不等長循環之間
        for j in range(i + 1, len(ks)):
            c += m[ks[i]] * m[ks[j]] * gcd(ks[i], ks[j])
    return c


def unlabeled_graphs(n):
    total = sum(num_perms(m) * 2 ** pair_cycles(m) for m in partitions(n))
    return Fraction(total, factorial(n))


vals = [unlabeled_graphs(n) for n in range(0, 16)]
assert all(v.denominator == 1 for v in vals)
print([int(v) for v in vals])
for n in (4, 8, 12, 15):
    print(f"n={n:>2}: g(n) / (2^C(n,2)/n!) = {float(vals[n] / Fraction(2 ** comb(n, 2), factorial(n))):.6f}")
```

輸出：

```text
[1, 1, 2, 4, 11, 34, 156, 1044, 12346, 274668, 12005168, 1018997864, 165091172592, 50502031367952, 29054155657235488, 31426485969804308768]
n= 4: g(n) / (2^C(n,2)/n!) = 4.125000
n= 8: g(n) / (2^C(n,2)/n!) = 1.854415
n=12: g(n) / (2^C(n,2)/n!) = 1.071719
n=15: g(n) / (2^C(n,2)/n!) = 1.013085
```

16 個數全部和 OEIS A000088 的 DATA 列一致（n = 15 時約 `3.1 × 10^19`）。`assert v.denominator == 1` 是一個便宜但有效的檢查：Burnside 的平均值必須是整數，若 `pair_cycles` 寫錯，分母通常不會剛好消掉。

第二部分回答了開頭的錯誤直覺：`2^C(n,2) / n!` 在小 n 時差很多（n = 4 差 4 倍），但比值趨近 1。這反映了一個定理：**幾乎所有大圖都沒有非平凡的自同構**（Erdős 與 Rényi，1963），所以絕大多數軌道的大小都是完整的 `n!`。這裡**不證**這個定理，數值只是一致的佐證。

## Pólya 計數：按「顏色內容」分類

Burnside 回答「總共幾個」。如果想知道「恰有 m 條邊的無標號圖有幾個」或「恰有 3 顆紅珠的項鍊有幾條」，需要 Pólya 的加權版本。

**循環指標（cycle index）**：對 G 中每個元素 g，記它的循環型為 `(c_1, c_2, ...)`（長度 i 的循環有 `c_i` 個），定義

```text
Z(G; x_1, x_2, ...) = (1/|G|) Σ_{g∈G} Π_i x_i^(c_i(g))
```

**Pólya 計數定理**：用顏色集合 `{1..q}` 著色，每種顏色有權重 `w_1, ..., w_q`，則按權重分類的軌道生成函數是

```text
Z(G; x_i = w_1^i + w_2^i + ... + w_q^i)
```

**證明（從 Burnside 推出）**：群作用不改變一個著色裡每種顏色用了幾次（重新排列位置不改變顏色的多重集合），所以對任一固定的「顏色內容」，那些著色構成一個 G 不變的子集，可以單獨套 Burnside。g 固定一個著色若且唯若 g 的每個循環整圈同色；長度 i 的循環塗顏色 c 貢獻權重 `w_c^i`，對所有顏色求和得 `Σ_c w_c^i`，各循環獨立相乘就是 `Π_i (Σ_c w_c^i)^(c_i(g))`，它是 g 的不動著色按顏色內容展開的生成函數。對 g 取平均，由 Burnside，每個顏色內容的係數就是該內容的軌道數。證畢。

代入所有 `w_c = 1` 得 `Z(G; x_i = q)`，就回到 Burnside。

```python
"""Pólya：6 珠項鍊恰有 r 顆紅珠的數量，用循環指標代入 (1 + y^i) 展開。"""
from itertools import product
from math import gcd

n = 6


def poly_mul(a, b):
    out = [0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            out[i + j] += x * y
    return out


total = [0] * (n + 1)
for k in range(n):
    c = gcd(n, k)                 # 旋轉 k 格有 c 個長度 n/c 的循環
    L = n // c
    term = [1]
    for _ in range(c):            # 每個循環貢獻 (1 + y^L)：整圈白或整圈紅
        factor = [0] * (L + 1)
        factor[0] = factor[L] = 1
        term = poly_mul(term, factor)
    total = [t + u for t, u in zip(total, term)]
polya = [t // n for t in total]

brute = [0] * (n + 1)
seen = set()
for s in product((0, 1), repeat=n):
    canon = min(s[k:] + s[:k] for k in range(n))
    if canon not in seen:
        seen.add(canon)
        brute[sum(canon)] += 1
print("紅珠數 r   :", list(range(n + 1)))
print("Pólya      :", polya)
print("暴力       :", brute)
```

輸出：

```text
紅珠數 r   : [0, 1, 2, 3, 4, 5, 6]
Pólya      : [1, 1, 3, 4, 3, 1, 1]
暴力       : [1, 1, 3, 4, 3, 1, 1]
```

這裡把白色權重設為 1、紅色設為 y，`x_i = 1 + y^i`，展開後 `y^r` 的係數就是「恰有 r 顆紅珠」的項鍊數。總和 `1+1+3+4+3+1+1 = 14`，回到上一節的 2 色 6 珠項鍊數。

同樣的代入用在圖上（「有邊」權重 y、「沒邊」權重 1），就得到按邊數分類的無標號圖。和 atlas 對照：

```python
"""Pólya 加權：按邊數分類的無標號圖，對照 atlas。"""
from itertools import permutations, combinations
from math import factorial
from collections import Counter
import networkx as nx


def pair_cycle_lengths(perm):
    n = len(perm)
    seen, lens = set(), []
    for p in combinations(range(n), 2):
        if p in seen:
            continue
        L, q = 0, p
        while q not in seen:
            seen.add(q)
            L += 1
            a, b = perm[q[0]], perm[q[1]]
            q = (a, b) if a < b else (b, a)
        lens.append(L)
    return lens


def by_edges(n):
    N = n * (n - 1) // 2
    total = [0] * (N + 1)
    for perm in permutations(range(n)):
        poly = [1]
        for L in pair_cycle_lengths(perm):   # 每個長度 L 的循環貢獻 (1 + y^L)
            new = [0] * (len(poly) + L)
            for i, c in enumerate(poly):
                new[i] += c
                new[i + L] += c
            poly = new
        total = [t + c for t, c in zip(total, poly)]
    return [t // factorial(n) for t in total]


atlas = nx.graph_atlas_g()
for n in (4, 5, 6):
    cnt = Counter(G.number_of_edges() for G in atlas if G.number_of_nodes() == n)
    ref = [cnt[m] for m in range(n * (n - 1) // 2 + 1)]
    print(f"n={n} Pólya: {by_edges(n)}")
    print(f"n={n} atlas: {ref}")
```

輸出：

```text
n=4 Pólya: [1, 1, 2, 3, 2, 1, 1]
n=4 atlas: [1, 1, 2, 3, 2, 1, 1]
n=5 Pólya: [1, 1, 2, 4, 6, 6, 6, 4, 2, 1, 1]
n=5 atlas: [1, 1, 2, 4, 6, 6, 6, 4, 2, 1, 1]
n=6 Pólya: [1, 1, 2, 5, 9, 15, 21, 24, 24, 21, 15, 9, 5, 2, 1, 1]
n=6 atlas: [1, 1, 2, 5, 9, 15, 21, 24, 24, 21, 15, 9, 5, 2, 1, 1]
```

分布左右對稱，因為取補圖是無標號圖之間「m 條邊 ↔ `C(n,2) − m` 條邊」的雙射（Ch 2 例 2 的無標號版本：補圖運算和重新標號可交換，所以它把軌道送到軌道）。

## 對比與取捨

| 方法 | 能回答 | 成本 | 本章用在 |
| --- | --- | --- | --- |
| 暴力標準形 | 軌道數、每個軌道的代表 | 物件數 × 群大小 | 驗證所有公式 |
| 天真除法：物件數 / 群大小 | 只在沒有任何物件有對稱性時正確 | O(1) | 反例 |
| Burnside | 軌道數 | 對每個群元素 g 算不動點數 | 項鍊、手環、無標號圖 |
| Burnside + 共軛類 | 同上，群元素按循環型分組 | 分割數 p(n) | n = 15 的無標號圖 |
| Pólya | 按顏色內容（權重）分類的軌道數 | 多項式展開 | 按紅珠數、按邊數分類 |

計數和**列舉**是兩件事。Burnside 告訴你 9 點無標號圖有 274668 種，但不會給你這 274668 張圖。要真的產生每個同構類的代表，需要 canonical labeling 加 orderly generation（McKay 的 nauty/geng，見 Ch 0 進階）。

## 踩雷集錦

1. **直接除以群的大小**：`2^4 / 4 = 4` 條項鍊、`2^C(n,2) / n!` 張無標號圖，都錯。糾正：只有在「沒有任何物件有非平凡對稱」時才成立；一般情況要用 Burnside 平均不動點數。
2. **群選錯**：手環問題只用旋轉群，n = 6 時會得到 14 而不是 13。糾正：先問「哪些操作之後算同一個」，把那些操作（含恆等元）全部列進群，並確認它們真的構成群（合成封閉）。
3. **在頂點上算不動點，而不是在頂點對上**：計算 `|Fix(σ)|` 時用了「σ 在頂點上的循環數」，得到 `2^(頂點循環數)`。但著色的對象是頂點對，必須用 σ **誘導在頂點對上**的循環。糾正：先確定 Ω 的每個物件是「什麼東西的著色」，群作用的循環就在那個東西上算。
4. **以為 Burnside 的和一定要列舉所有群元素**：n = 15 時 `15! ≈ 1.3 × 10^12`，不可能列舉。糾正：`|Fix(g)|` 只依賴 g 的共軛類（對 `S_n` 就是循環型），按類加總，乘上每類的大小。
5. **把軌道數和「沒有對稱性的物件數」混為一談**：Burnside 數的是**所有**軌道，包括高度對稱的（空圖、完全圖）。想數「自同構群平凡的圖」需要另外的方法。

## 進階：再往深一層

**歷史**：J. Howard Redfield 在 1927 年就發表了等價於 Pólya 定理的結果，但論文寫得艱澀、長期被忽略；Pólya 在 1937 年獨立發表並大量應用在化學異構物計數上，這套理論因此以他命名。Pólya 的原始論文後來由 R. C. Read 譯成英文並加上半世紀的發展回顧（見延伸閱讀）。

**和 Ch 37 的連結**：Burnside 只要求知道「每個置換固定了幾張圖」，不需要判定兩張圖是否同構。真正判定同構（graph isomorphism）是另一個問題：Babai 在 2015 年給出 quasipolynomial 時間演算法，但是否有多項式時間演算法仍未知。實務上 nauty、Traces、bliss 這些工具在絕大多數圖上很快。[Ch 37](./37-graph-isomorphism-wl.md) 會講 Weisfeiler–Leman 演算法和它的極限。

**在 SAT 的應用**：對稱的 CNF 公式（例如鴿籠公式）會讓 CDCL solver 反覆搜尋等價分支。symmetry breaking 會加入「只接受字典序最小代表」的限制式——正是本章暴力標準形的想法。見 [SAT/SMT 課程](../../programming/sat_smt/README.md) 的前處理章節與本課 [Ch 35](./35-reductions-to-sat.md)。

**群論的下一步**：本章只用到置換群和軌道—穩定子。若要處理更大的對稱群（例如 Petersen 圖的自同構群有 120 個元素），需要 Sylow 定理、共軛類等工具；Pólya 理論的完整推廣（de Bruijn 的加權版本、帶顏色置換的情況）見 Harary–Palmer。

## 動手練習

1. 用 Burnside 計算：用 3 種顏色塗立方體的 6 個面，旋轉後相同算同一個，有幾種？（立方體的旋轉群有 24 個元素，先把它們按「繞面軸、繞頂點軸、繞邊軸」分類，再算每類的循環型。）寫暴力程式產生 24 個旋轉並驗證答案是 57。
2. 修改 `unlabeled_graphs`，改成數 n 點無標號**有向圖**（每對有序頂點 `(u, v)`、`u ≠ v` 可以有或沒有邊）。推導 `c(σ)` 的公式，和 n ≤ 4 的暴力標準形比對。
3. 用 `by_edges` 的想法，數 n = 7 時恰有 10 條邊的無標號圖，和 atlas 比對。然後用 `partitions` 版本改寫 `by_edges`，算到 n = 10。
4. 在 `c6_canon` 的暴力程式中印出每個同構類的 `|Aut|`，找出 n = 5 時自同構群最大和最小的圖，說出它們長什麼樣子。

## 本章重點整理

- 「哪些算同一個」用群作用描述；本質不同的物件數 = 軌道數。
- 軌道—穩定子：`|Orb(x)| · |Stab(x)| = |G|`，對稱越高軌道越小。
- Burnside：軌道數 = 平均不動點數。證明是雙重計數 `(g, x)` 配對，每個軌道剛好貢獻 `|G|`。
- 無標號圖數 `= (1/n!) Σ_σ 2^c(σ)`，`c(σ)` 是誘導在頂點對上的循環數；按循環型加總可算到 n = 15 以上。
- Pólya 把 `x_i` 代成 `Σ_c w_c^i`，得到按顏色內容分類的計數。

## 自我檢核

- [ ] 不看書，寫出 Burnside 引理的雙重計數證明，並說明「每個軌道剛好貢獻 |G|」這一步用到了什麼。
- [ ] 解釋為什麼 `2^C(n,2) / n!` 不是無標號圖數，但 n 很大時比值趨近 1。
- [ ] 推導置換的一個長度 k 循環內部的頂點對會形成幾個循環，並說明 k 為偶數時的特殊情況。
- [ ] 想一個你工作中的去重問題（例如「只差變數命名的兩段程式碼」「只差主機名稱的兩條攻擊路徑」），說出它的 Ω、群 G 和軌道分別是什麼。

## 延伸閱讀

### 原始文獻

- **[Kombinatorische Anzahlbestimmungen für Gruppen, Graphen und chemische Verbindungen](https://doi.org/10.1007/BF02546665)** — George Pólya，*Acta Mathematica* 68: 145–254（1937）
  - **核心貢獻**：Pólya 計數定理的原始論文（第 4 節的 Hauptsatz），並用它統一處理樹與化學異構物的計數。
  - **讀哪裡**：原文是德文。建議改讀英譯本《Combinatorial Enumeration of Groups, Graphs, and Chemical Compounds》（Pólya、R. C. Read，Springer，1987），其中 Read 撰寫的「The Legacy of Pólya's Paper: Fifty Years of Pólya Theory」一章回顧了後續發展，包括 Redfield 的優先權問題。
  - **和本章的關聯**：本章 Pólya 計數定理的出處；論文的化學應用正是「無標號樹」計數，和 Ch 9 呼應。
  - **前提知識**：本章內容與基本的冪級數操作。

### 書籍

- **《Graphical Enumeration》** — Frank Harary、Edgar M. Palmer（Academic Press，1973）
  - **這本書的定位**：圖計數的經典專著。
  - **讀哪幾章**：Chapter 2（Pólya's Theorem：cycle index、Burnside's lemma、Pólya's theorem）是本章的完整版；Chapter 4（Graphs）用頂點對上的循環指標推導無標號圖的計數，正是本章 `pair_cycles` 的出處。年代久，但這部分理論已定型，仍然適用。
  - **前提知識**：本章內容與 Ch 5 的生成函數。
- **《Introductory Combinatorics》** — Richard A. Brualdi（Pearson，第 5 版，2010）
  - **讀哪幾章**：Chapter 14（Pólya Counting）：從置換群、Burnside 定理到 Pólya 公式，步調比 Harary–Palmer 慢，例子以項鍊和立方體著色為主，適合當本章的第二個講解版本。
  - **前提知識**：Ch 3 的排列組合；不需要群論背景。

### 資料庫

- **[OEIS A000088](https://oeis.org/A000088)**、**[A000031](https://oeis.org/A000031)**、**[A000029](https://oeis.org/A000029)**
  - **讀哪裡**：A000088 的 FORMULA 段列出了本章按循環型加總的公式與漸近式 `2^C(n,2)/n!`；A000031 的 COMMENTS 註明 2 色項鍊數也等於 GF(2) 上某些不可約多項式的計數，這是項鍊和有限體之間的連結。
  - **能學到什麼**：本章每一個數字的獨立參照，以及更多項（A000088 列到 n = 19）。

Part 1 的工具到這裡備齊了：證明技巧、排列組合、排容、生成函數、對稱計數。在進入圖的結構之前，用一個練習把它們綁在一起：寫一個自動用暴力計數去檢查組合公式的工具。

→ [練習 A：組合恆等式的暴力驗證器](./practice-a-combinatorial-identities.md)
