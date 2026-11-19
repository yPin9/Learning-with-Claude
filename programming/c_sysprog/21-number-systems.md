# Ch 21 — 數值系統：進位制、二補數與同餘

> **目標**：說得出「為什麼電腦用二進位、為什麼負數用二補數、為什麼 `-x == ~x + 1`」，並能用同餘（modular arithmetic）解釋 C 的無號回繞、有號轉換、整數提升與編譯器的代數化簡。

> **對應原文**：[你所不知道的 C 語言：數值系統篇](https://hackmd.io/@sysprog/c-numerics) 的「從 0.1 談起」「電腦不是只有二進位」「二進位的起源」「數值表達方式和阿貝爾群」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14、glibc 2.35。C23 段落（`0b` 常數的分隔符號、`%b`、`_BitInt`）在 Docker `gcc:14` 映像（GCC 14.4、glibc 2.41）執行，會在段落中標明。

## 為什麼需要這個？

寫 C 的人幾乎都背過「`int` 是 32 位元、範圍 −2147483648 到 2147483647」「負數用二補數」。可是一追問就卡住：

- 為什麼 `0.1 + 0.2 != 0.3`？
- 為什麼負數比正數多一個？
- 為什麼 `unsigned` 加過頭會繞回 0，`int` 加過頭卻是「未定義行為」？
- 為什麼 `(uint8_t)200 + (uint8_t)100` 等於 300 而不是 44？
- 為什麼 gcc 會把 `x * 3 == y * 3` 化簡成 `x == y`，卻不敢把 `x * 2 == y * 2` 化簡？

這些問題的答案都在同一個地方：**固定寬度的整數不是數學上的整數 ℤ，而是「模 2^w 的剩餘類」**。只要接受這個模型，上面每個現象都能用一兩行代數推出來，而不是死背規則。這一章先從「進位制本身是一種選擇」講起，再走到二補數與同餘，最後預告下一章要用到的整數提升與一般算術轉換。

歷史上，「負數怎麼表示」是吵過的：早期機器有 sign-magnitude（符號加大小，IBM 7090）、一補數（ones' complement，CDC 6600、UNIVAC 1100）、二補數（PDP-11 之後的主流），蘇聯甚至造過平衡三進位的電腦。C 語言誕生時這幾種都還在，所以 C11 規格書允許三種表示法；直到 C23 才只剩二補數。理解它們各自的代價，才知道為什麼最後是二補數勝出。

## 先建立直覺

想像汽車的里程表：四個滾輪，每個滾輪 0–9。開到 9999 再走一公里，它顯示 0000——第五位的進位**沒地方放，被丟掉了**。這台里程表做的其實是「模 10000 的加法」。

二進位的 4 位元暫存器也一樣，只是每個滾輪只有 0 和 1。把 16 個樣式排成一個圓：

```
                 0000
          1111         0001
      1110                 0010
    1101    無號讀法 / 有號讀法  0011
    1100     12/-4      3/3     0100
      1011                 0101
          1010         0110
                 1000  0111
           8/-8 ←剪開處→ 7/7

  順時針走一格 = 加 1。走到 1111 再加 1 回到 0000（進位被丟掉）。
  無號讀法：從 0000 剪開，讀成 0..15
  有號讀法：從 1000 剪開，讀成 -8..7
```

同一個圓，**剪開的地方不同，就得到不同的讀法**。這張圖是整章的心智模型：

- 加法器只是在圓上轉圈，它不知道你要怎麼讀。
- 無號與有號的差別，只在「最後怎麼把圓攤平成一條線」。
- 任何「比大小」都要先把圓剪開；跨過剪開處，大小關係就會翻轉（下一章的溢位漏洞全部從這裡來）。

## 從 0.1 談起

先看一個最常見的「數值系統」驚嚇，它和整數無關，但說明了一件事：**有限位數的二進位，表示不了所有十進位小數**。

```c
#include <stdio.h>

int main(void)
{
    double a = 0.1, b = 0.2, c = 0.01;
    printf("0.1 + 0.2        = %.17g\n", a + b);
    printf("1 - 0.1          = %.17g\n", 1 - a);
    printf("0.1 - 0.01       = %.17g\n", a - c);
    printf("0.1 - 0.01 - 0.1 = %.17g\n", a - c - a);
    printf("0.1 實際存的是   %.55f\n", a);
    printf("0.1 + 0.2 == 0.3 ? %d\n", a + b == 0.3);
    return 0;
}
```

```
0.1 + 0.2        = 0.30000000000000004
1 - 0.1          = 0.90000000000000002
0.1 - 0.01       = 0.090000000000000011
0.1 - 0.01 - 0.1 = -0.009999999999999995
0.1 實際存的是   0.1000000000000000055511151231257827021181583404541015625
0.1 + 0.2 == 0.3 ? 0
```

`0.1 = 1/10`，分母含有因數 5，而二進位只能精確表示分母是 2 的冪的分數，所以 0.1 在二進位是無限循環小數 `0.000110011001100…`，存進 64 位元的 `double`（IEEE 754 binary64）時必須捨入。第五行印出的就是「離 0.1 最近的那個 double」的精確十進位值。

注意第二行：原文用 Python 3.12 顯示 `1 - 0.1` 是 `0.9`，我們用 `%.17g` 卻看到 `0.90000000000000002`。兩者不矛盾：Python 的 `repr` 印出「能唯一還原這個 double 的最短十進位字串」，`1 - 0.1` 的結果碰巧就是離 0.9 最近的 double，所以最短表示是 `0.9`；`%.17g` 則硬印 17 位有效數字，把它和數學上 0.9 的差距暴露出來。**印出 0.9 不代表它等於 0.9**。

第四行是誤差的累積：第一次減法的捨入誤差留在中間結果，兩個 0.1 的表示誤差雖然相消，0.01 的表示誤差加上第一次捨入誤差卻留了下來，於是結果比 −0.01 略大。浮點數的格式、捨入與誤差分析是 [Ch 25](./25-ieee754.md) 與 [Ch 26](./26-floating-point-errors.md) 的主題；這一章留在整數。

## 電腦不是只有二進位

### 位置記數法

**位置記數法（positional notation）**：選一個**基底（radix / base）** b，每一位數 aᵢ 乘上 bⁱ 再加總，`n = Σ aᵢ·bⁱ`。十進位是 b = 10、aᵢ ∈ {0..9}；二進位是 b = 2、aᵢ ∈ {0, 1}。二進位是工程上的選擇，不是唯一的記數法。

Knuth 在《The Art of Computer Programming》第 2 卷 4.1 節寫：「Perhaps the prettiest number system of all is the balanced ternary notation」。**平衡三進位（balanced ternary）** 的基底是 3，但每一位數落在以 0 為中心的 {−1, 0, +1}，書寫時記為 `T`（或 `-`）、`0`、`1`（或 `+`）。

### radix economy：哪個基底最省？

用基底 b 寫出 N 需要 ⌊log_b N⌋ + 1 位數，每位數有 b 種狀態。如果把「位數 × 每位狀態數」當成硬體成本（每一位要能分辨 b 種電位），就得到 **radix economy**：

```
E(b, N) = b · (⌊log_b N⌋ + 1)
```

直接算給你看（`digits` 用反覆除法計算位數，避開 `log` 的浮點誤差）：

```c
#include <math.h>
#include <stdio.h>

/* 以 b 為基底寫 N 需要幾位數：反覆除以 b，避開 log 的浮點誤差 */
static int digits(int b, long n)
{
    int d = 0;
    do { n /= b; d++; } while (n);
    return d;
}

int main(void)
{
    long ranges[] = {6, 43, 182, 5329};
    printf("%-10s %8s %8s %8s\n", "N range", "b=2", "b=3", "b=4");
    for (int i = 0; i < 4; i++) {
        printf("1..%-7ld", ranges[i]);
        for (int b = 2; b <= 4; b++) {
            double sum = 0;
            for (long n = 1; n <= ranges[i]; n++)
                sum += b * digits(b, n);
            printf(" %8.2f", sum / ranges[i]);
        }
        printf("\n");
    }
    printf("\nb / (e ln b):\n");
    for (int b = 2; b <= 10; b++)
        printf("  b=%2d  %.4f\n", b, b / (M_E * log(b)));
    return 0;
}
```

```
N range         b=2      b=3      b=4
1..6           4.67     5.00     6.00
1..43          9.35     9.49    10.33
1..182        13.29    13.09    14.22
1..5329       22.93    22.16    23.91

b / (e ln b):
  b= 2  1.0615
  b= 3  1.0046
  b= 4  1.0615
  b= 5  1.1429
...（中略）
  b=10  1.5977
```

前半是原文表格的重現：N 小的時候二進位反而省，範圍擴大到 1–182 之後三進位勝出。N 夠大時取整的影響可忽略，`E(b, N) ≈ b · ln N / ln b`，與 N 無關的部分是 `b / ln b`。對 b 微分，`(ln b − 1) / (ln b)² = 0` 得 b = e ≈ 2.718。以 e 為基準正規化得到後半的表：整數基底中 3 最接近理論最佳（1.0046），2 與 4 打平（因為 `4/ln 4 = 4/(2 ln 2) = 2/ln 2`），10 差了快 60%。

這是三進位電腦的立論基礎。實際建成的是 Sergei Sobolev 與 Nikolay Brusentsov 1958 年在莫斯科國立大學主持的 **Setun**，以平衡三進位運作，1962 年起在喀山量產，連同原型機共 50 部。

但請注意這個模型的前提：「每一位分辨 b 種狀態的成本正比於 b」。真實的電晶體不是這樣——分辨「有電／沒電」兩種狀態遠比分辨三種電位穩定、抗雜訊、好製造。**二進位勝出的理由不在 radix economy，而在元件只要區分兩種狀態**。radix economy 是一個漂亮但簡化的模型，套用到硬體時要記得它的假設。

### 平衡三進位的負數：不需要符號位元

平衡三進位最迷人的地方是負數。下面這支程式把整數轉成平衡三進位，並示範「取負」：

```c
#include <stdio.h>
#include <string.h>

/* 整數轉平衡三進位字串：每位數取 {-1, 0, +1}，寫成 T、0、1 */
static void to_bal3(long n, char *out)
{
    char buf[64];
    int len = 0;
    if (n == 0) { strcpy(out, "0"); return; }
    while (n != 0) {
        long r = n % 3;          /* C 的 % 與被除數同號：r ∈ {-2..2} */
        n /= 3;
        if (r == 2)  { r = -1; n += 1; }   /* 2 = 3 - 1：本位寫 T，向上進 1 */
        if (r == -2) { r = 1;  n -= 1; }   /* -2 = -3 + 1 */
        buf[len++] = r == 0 ? '0' : (r == 1 ? '1' : 'T');
    }
    for (int i = 0; i < len; i++)
        out[i] = buf[len - 1 - i];
    out[len] = '\0';
}

static long from_bal3(const char *s)
{
    long v = 0;
    for (; *s; s++)
        v = v * 3 + (*s == '1') - (*s == 'T');
    return v;
}

/* 取負：1 與 T 互換 */
static void negate(char *s)
{
    for (; *s; s++)
        *s = *s == '1' ? 'T' : (*s == 'T' ? '1' : '0');
}

int main(void)
{
    long tests[] = {6, -6, 114, 123, -123, 1, -1, 13, 40};
    char s[64];
    for (size_t i = 0; i < sizeof tests / sizeof tests[0]; i++) {
        to_bal3(tests[i], s);
        printf("%5ld = %-8s", tests[i], s);
        negate(s);
        printf(" 取負 -> %-8s = %ld\n", s, from_bal3(s));
    }
    long bad = 0;
    for (long n = -100000; n <= 100000; n++) {
        to_bal3(n, s);
        bad += from_bal3(s) != n;
        negate(s);
        bad += from_bal3(s) != -n;
    }
    printf("round-trip mismatches in [-1e5, 1e5]: %ld\n", bad);
    return 0;
}
```

```
    6 = 1T0      取負 -> T10      = -6
   -6 = T10      取負 -> 1T0      = 6
  114 = 111T0    取負 -> TTT10    = -114
  123 = 1TTTT0   取負 -> T11110   = -123
 -123 = T11110   取負 -> 1TTTT0   = 123
    1 = 1        取負 -> T        = -1
   -1 = T        取負 -> 1        = 1
   13 = 111      取負 -> TTT      = -13
   40 = 1111     取負 -> TTTT     = -40
round-trip mismatches in [-1e5, 1e5]: 0
```

`6 = 1·9 + (−1)·3 + 0 = 1T0`；取負時每一位乘以 −1，`1` 與 `T` 互換即可。**正負號不需要額外的位元，也沒有 signed 與 unsigned 之分**；正負由最高的非零位決定。程式裡的轉換技巧值得多看一眼：C 的 `%` 結果與被除數同號（C11 6.5.5 §6），所以餘數 r 可能是 −2..2；遇到 2 就寫成「本位 −1、往上進 1」（因為 2 = 3 − 1）。

小數也適用，但 1/5 的分母不是 3 的冪，展開不會終止：`0.2 = 0.(1TT1)` 循環，`1/3 − 1/9 − 1/27 + 1/81 = 16/81`，以公比 1/81 重複，總和 `(16/81)/(1 − 1/81) = 16/80 = 0.2`。

### 取負數的成本：「加一」到底有多貴？

同樣是 123，8 位元二補數是 `01111011`，平衡三進位是 `001TTTT0`。平衡三進位取負只要逐位反轉；二補數則是**反轉全部位元再加一**，得 `10000101`。差別在那個「加一」。

`~x + 1` 的進位次數，等於 `~x` 尾端連續 1 的個數，也就是 x 尾端連續 0 的個數。對均勻分布的 n 位元 x，恰有 k 個尾端 0（k < n）的機率是 (1/2)^(k+1)，x = 0 時進位 n 次、機率 (1/2)^n。期望值：

```
E(n) = Σ_{k=0}^{n-1} k·(1/2)^(k+1) + n·(1/2)^n = 1 − (1/2)^n
```

平均不到一次進位，但最壞情況要走完整條進位鏈。實務上這個加一幾乎免費：加法器本來就有一個「進位輸入」，`a − b` 在硬體裡就是 `a + ~b + 1`，把那個 1 灌進進位輸入即可。三進位近年再受注意的原因在別處：[BitNet b1.58](https://arxiv.org/abs/2402.17764) 把大型語言模型線性層的權重限制在 {−1, 0, +1}（log₂3 ≈ 1.58 位元），矩陣乘法因而主要化為加減法，省下的是乘法器與記憶體頻寬，不是取負數的成本。

## 二進位的起源

Leibniz 走向二進位的起點是數論。從 a = 2 研究 Fermat 小定理時會遇到 2ⁿ − 1 這類 **Mersenne 數**，以二進位書寫時是 `1`、`11`、`111`、`1111`，不必計算就能寫出。**完美數（perfect number）** 是真因數總和等於自身的數（6 = 1+2+3），Euclid《幾何原本》第 9 卷命題 36 證明：若 2ⁿ − 1 是質數，則 2ⁿ⁻¹(2ⁿ − 1) 是完美數。換成二進位，定理變成字串拼接——n 個 1 後面接 n − 1 個 0：

```c
#include <stdio.h>

static void bin(unsigned long v, int width)
{
    char b[65]; int n = 0;
    do { b[n++] = '0' + (v & 1); v >>= 1; } while (v);
    for (int i = n; i < width; i++) putchar(' ');
    while (n) putchar(b[--n]);
}

static int is_prime(unsigned long n)
{
    if (n < 2) return 0;
    for (unsigned long d = 2; d * d <= n; d++)
        if (n % d == 0) return 0;
    return 1;
}

int main(void)
{
    for (int n = 2; n <= 7; n++) {
        unsigned long m = (1UL << n) - 1, p = (1UL << (n - 1)) * m;
        printf("n=%d  %3lu = ", n, m); bin(m, 7);
        printf(" %-4s  %4lu = ", is_prime(m) ? "質數" : "合成", p); bin(p, 13);
        printf("\n");
    }
    return 0;
}
```

```
n=2    3 =      11 質數     6 =           110
n=3    7 =     111 質數    28 =         11100
n=4   15 =    1111 合成   120 =       1111000
n=5   31 =   11111 質數   496 =     111110000
n=6   63 =  111111 合成  2016 =   11111100000
n=7  127 = 1111111 質數  8128 = 1111111000000
```

（6、28、496、8128 是完美數；120 與 2016 不是，因為 15 與 63 不是質數。）

Leibniz 不是第一個用二進位的人（Thomas Harriot 17 世紀初的手稿已有，未發表），但他是第一位有系統地建構二進位算術並看出其價值的人：1679 年的手稿 "De Progressione Dyadica" 寫下「反覆除以 2 取餘數」的轉換法與四則運算法則。他清楚二進位位數多、人算反而慢，但對機器不是問題。1701 年他把論文投給巴黎皇家科學院，秘書 Fontenelle 要求先看到用處而未刊登；同年他寫信給在中國傳教的耶穌會士白晉（Joachim Bouvet），白晉看出伏羲六十四卦的排列與二進位計數一致，回信附上卦圖。1703 年改寫的 "Explication de l'Arithmétique Binaire" 才得以刊出，副標就提到「古代中國伏羲的圖形」。

之後兩步把二進位接上電路：George Boole 在 1847 與 1854 年以代數處理邏輯（布林代數），Claude Shannon 1938 年的 MIT 碩士論文 *A Symbolic Analysis of Relay and Switching Circuits* 證明布林代數可以描述並設計繼電器開關電路。從此「0 與 1」同時是數字、邏輯值與電壓。

### C 語言很晚才能寫二進位常數

C11 6.4.4.1 的整數常數只有十進位、八進位（`0` 開頭）、十六進位（`0x` 開頭）。`0b` 一直是 GCC 與 Clang 的擴充：

```c
#include <stdio.h>
int main(void)
{
    unsigned mask = 0b11110000;
    printf("%u\n", mask);
    return 0;
}
```

```
$ gcc -std=c11 -Wpedantic bin11.c -o b && ./b
bin11.c: In function ‘main’:
bin11.c:4:21: warning: binary constants are a C2X feature or GCC extension
    4 |     unsigned mask = 0b11110000;
      |                     ^~~~~~~~~~
240
$ clang -std=c11 -Wpedantic bin11.c -o b
bin11.c:4:21: warning: binary integer literals are a GNU extension [-Wgnu-binary-literal]
```

（gcc 11 還稱 C23 為「C2X」；原文 GCC 14 的訊息改為 "C23 feature"。）C23 把整數常數移到 6.4.4.2，§4 正式加入 `0b`／`0B`，並引入**數字分隔符號（digit separator）** `'`。分隔符號在 C11 模式下會撞到字元常數語法：

```
$ gcc -std=c11 -c sep11.c          # 內容：int x = 1'000;
sep11.c:1:10: warning: missing terminating ' character
sep11.c:1:10: error: missing terminating ' character
```

輸出端，C23 7.23.6.1 讓 `printf` 支援 `%b`（`#` 旗標加上 `0b` 前綴），glibc 2.35 起支援。以下在 Docker `gcc:14`（glibc 2.41）以 `-std=c23` 執行：

```c
unsigned mask = 0b1111'0000;          /* 0xF0 */
unsigned long long n = 4'294'967'296; /* 2^32 */
printf("%u %#x %#b\n", mask, mask, mask);
printf("%llu %#llb\n", n, n);
```

```
240 0xf0 0b11110000
4294967296 0b100000000000000000000000000000000
```

## 底層機制：固定寬度整數是一個「環」

這一節是本章的核心。我們要用一點抽象代數，但每個定義都會立刻接回 C 程式。

### 群：最少的幾條規則

集合 G 配上二元運算 + 若滿足下列四條，稱為**群（group）**：

1. **封閉性**：a, b ∈ G ⇒ a + b ∈ G
2. **結合律**：(a + b) + c = a + (b + c)
3. **單位元素**：存在 0 使 a + 0 = 0 + a = a
4. **反元素**：每個 a 都有 b 使 a + b = 0（記 b 為 −a）

再加上交換律 a + b = b + a，稱為**阿貝爾群（Abelian group）**。整數 ℤ 對加法是阿貝爾群；自然數 ℕ 缺反元素，不是群。

這四條為什麼重要？因為**編譯器的每一個代數化簡都要以某條公理成立為前提**。結合律成立，才能把 `(a+b)+c` 改成 `a+(b+c)` 來向量化；反元素存在，才能把 `x + y - y` 化簡成 `x`。哪條公理在哪個 C 型別上成立，決定了哪些最佳化合法。

### 同餘與剩餘類：把「繞回」寫成數學

固定正整數 m，定義 **a ≡ b (mod m)**（同餘，congruence）若且唯若 m 整除 a − b。這是一個等價關係，把整數切成 m 堆，a 所在的那堆 `[a] = {a + km | k ∈ ℤ}` 稱為**剩餘類（residue class）**，m 個剩餘類構成的集合記為 ℤ/mℤ。

同餘最重要的性質是**與加法、乘法相容**：若 a ≡ a′ 且 b ≡ b′ (mod m)，則

```
(a + b) − (a′ + b′) = (a − a′) + (b − b′)
ab − a′b′          = a(b − b′) + b′(a − a′)
```

右邊都是 m 的倍數。所以 `[a] + [b] = [a + b]`、`[a]·[b] = [ab]` 不管你挑哪個代表元都得到同一堆——運算是**定義良好（well-defined）** 的。

取 m = 2^w，這就是**硬體可以丟掉進位的理由**：第 w 位元以上的部分是 2^w 的倍數，丟掉它不改變剩餘類。里程表丟掉第五位，答案在「模 10000」的意義下仍然正確。ℤ 的群公理也因此逐條傳到 ℤ/2^wℤ：結合律 `[a]+([b]+[c]) = [a+(b+c)] = [(a+b)+c]`，單位元素 [0]，[a] 的反元素 [−a]。

### 無號與有號：同一剩餘類的兩個代表元

w 位元的樣式 `x_{w−1}…x₁x₀` 有兩種讀法：

```
U(x) = Σ_{i=0}^{w−1} xᵢ·2ⁱ                         （無號，unsigned）
S(x) = −x_{w−1}·2^{w−1} + Σ_{i=0}^{w−2} xᵢ·2ⁱ = U(x) − x_{w−1}·2^w   （二補數，two's complement）
```

兩式只差最高位元的權重：無號是 +2^{w−1}，二補數是 −2^{w−1}。所以 **U(x) 與 S(x) 只差 2^w 的倍數，屬於同一個剩餘類**。把 4 位元的 16 個樣式全列出來：

```c
#include <stdint.h>
#include <stdio.h>

int main(void)
{
    /* 4 位元的 16 個樣式：U(x) 與 S(x) */
    printf("bits  U   S\n");
    for (unsigned x = 0; x < 16; x++) {
        int s = (x & 8) ? (int) x - 16 : (int) x;   /* S(x) = U(x) - x3 * 2^4 */
        printf("%u%u%u%u %3u %3d\n", (x >> 3) & 1, (x >> 2) & 1, (x >> 1) & 1, x & 1, x, s);
    }

    /* 窮舉 16 位元：-x == ~x + 1，x + ~x == 全 1 */
    unsigned long bad = 0;
    for (uint32_t i = 0; i <= 0xFFFF; i++) {
        uint16_t x = (uint16_t) i;
        bad += (uint16_t) -x != (uint16_t) (~x + 1);
        bad += (uint16_t) (x + (uint16_t) ~x) != 0xFFFF;
    }
    printf("16-bit mismatches: %lu\n", bad);

    /* INT_MIN 的反元素是自己（以 8 位元樣式示範，運算在無號中進行） */
    uint8_t m = 0x80;
    printf("~0x80 + 1 = 0x%02X\n", (uint8_t) (~m + 1));
    return 0;
}
```

```
twos.c:17:30: warning: self-comparison always evaluates to false [-Wtautological-compare]
   17 |         bad += (uint16_t) -x != (uint16_t) (~x + 1);
      |                              ^~
bits  U   S
0000   0   0
0001   1   1
...（中略）
0111   7   7
1000   8  -8
1001   9  -7
...（中略）
1110  14  -2
1111  15  -1
16-bit mismatches: 0
~0x80 + 1 = 0x80
```

先看那個警告：gcc 說 `(uint16_t)-x != (uint16_t)(~x + 1)` 是「自己跟自己比」，恆為假。**編譯器在內部表示中就把 `-x` 與 `~x + 1` 當成同一個運算式**——這正是我們要證明的事，gcc 先替我們確認了。

U 的值域是 [0, 2^w)，S 的值域是 [−2^{w−1}, 2^{w−1})，兩者都是 2^w 個連續整數，在每個剩餘類中恰好各取一個代表元，稱為**完全剩餘系（complete residue system）**。所以：

- **無號與二補數的加法器是同一個電路**。電路算的是剩餘類，有號或無號只決定讀出時取哪個代表元。
- 乘積的低 w 位元也一樣，所以 x86-64 的兩運算元 `imul` 同時服務有號與無號乘法；只有 2w 位元乘積的**高半部**依讀法而不同（arm64 因此分成 `smulh` 與 `umulh`）。
- 4 位元時 S 的範圍是 −8..7，**負數比正數多一個**，因為 0 佔掉了「非負」那一半的一個位置；`1000`（−8）的反元素是自己（−8 + −8 = −16 ≡ 0 mod 16）。最後一行輸出 `~0x80 + 1 = 0x80` 就是這件事的 8 位元版本，它也是 `-INT_MIN` 會溢位的代數根源（[Ch 22](./22-integer-overflow.md) 的 libsndfile 案例）。

### 為什麼 `-x == ~x + 1`：三種推導

**推導一（直接從 U 算）**：`~x` 把每個位元翻轉，`xᵢ + (1 − xᵢ) = 1`，所以 `U(x) + U(~x) = Σ 2ⁱ = 2^w − 1`。在模 2^w 下 `2^w − 1 ≡ −1`，得 `x + ~x ≡ −1`，移項 `−x ≡ ~x + 1`。上面窮舉的第二條 `x + ~x == 0xFFFF` 就是這一步。

**推導二（從加法與位元運算的橋樑）**：逐位元看，`xᵢ + yᵢ = (xᵢ ⊕ yᵢ) + 2(xᵢ ∧ yᵢ)` 在整數中成立（四種情形逐一驗算），兩邊乘 2ⁱ 再求和、取模 2^w：

```
x + y = (x ^ y) + 2·(x & y)
```

XOR 是「去掉進位的加法」，AND 標出產生進位的位置。令 y = ~x，`x ^ ~x` 是全 1、`x & ~x` 是 0，得 `x + ~x = 全 1 ≡ −1`。這條等式我們用 16 位元窮舉了全部 2³² 組 (x, y)，連同 `x + y = (x | y) + (x & y)` 與 De Morgan 定律 `~(x & y) == (~x | ~y)`：

```
identity mismatches over 2^32 pairs: 0
```

**推導三（從進位鏈看一補數與二補數的關係）**：「一補數」就是 `~x`，它用 `2^w − 1 − x` 表示 −x，所以會有兩個零（`0000` 與 `1111`）。二補數把整個負半邊往下挪一格，讓 `1111` 代表 −1，零就只剩一個。從時鐘圖看，一補數的對稱軸在 0 與 −0 之間，二補數的對稱軸在 0 本身，兩條軸差半格，所以「二補數 = 一補數 + 1」。三種推導講的是同一件事，只是語言不同。

### C 只把群結構交給無號型別

規格書怎麼說？C11 6.2.5 §9：

> A computation involving unsigned operands can never overflow, because a result that cannot be represented by the resulting unsigned integer type is reduced modulo the number that is one greater than the largest value that can be represented by the resulting type.

所以 `unsigned` 的 `+`、`-`、`*` 就是 ℤ/2^wℤ 的運算，是完整的群（乘法則構成環，見下文）。有號整數沒有這層保證，C11 6.5 §5：

> If an exceptional condition occurs during the evaluation of an expression (that is, if the result is not mathematically defined or not in the range of representable values for its type), the behavior is undefined.

C11 6.2.6.2 §2 允許有號整數用 sign-magnitude、二補數或一補數三種表示；C23 6.2.6.2 §2 統一為二補數（"If the sign bit is one, it has value −(2^{N−1})."）。**但表示法統一了，溢位仍是未定義行為**：`INT_MAX + 1` 在 C 語言層級不會「繞回」，編譯器可以假設它不發生。換句話說，**`int` 的語意是 ℤ（溢位了就是你的錯），`unsigned` 的語意是 ℤ/2^wℤ**。這個分野是下一章所有內容的起點。

### 轉換就是換代表元

C11 6.3.1.3（C23 同條同段）規定整數轉換：

> 1 When a value with integer type is converted to another integer type other than _Bool, if the value can be represented by the new type, it is unchanged.
>
> 2 Otherwise, if the new type is unsigned, the value is converted by repeatedly adding or subtracting one more than the maximum value that can be represented in the new type until the value is in the range of the new type.
>
> 3 Otherwise, the new type is signed and the value cannot be represented in it; either the result is implementation-defined or an implementation-defined signal is raised.

§2 的「反覆加減 2^N」不改變剩餘類，只是在 [0, 2^N) 找出代表元。§3 由實作定義；GCC 手冊 [Integers implementation](https://gcc.gnu.org/onlinedocs/gcc/Integers-implementation.html) 寫明 "the value is reduced modulo 2^N to be within range of the type; no signal is raised"，Clang 相同。所以在這兩個編譯器上，**同寬度的有號與無號互轉不改變位元樣式，只改變讀法**。

轉到**較寬**的型別時，問題反過來：窄型別的一個剩餘類對應寬型別的好幾個，必須先選定一個整數值。**零擴充（zero extension）** 選 U(x)，高位補 0；**符號擴充（sign extension）** 選 S(x)，高位複製最高位元。§1 要求「可表示的值不變」，所以 `int8_t` 變寬時編譯器做符號擴充（x86-64 的 `movsx`），`uint8_t` 做零擴充（`movzx`）。

為什麼複製符號位元能保持 S 的值？設 k 位元擴成 w 位元、最高位 x_{k−1} = 1：原本的權重 −2^{k−1} 變成 +2^{k−1}，增加 2^k；新增的第 k 到 w−2 位元貢獻 2^{w−1} − 2^k；新的最高位貢獻 −2^{w−1}；合計 2^k + 2^{w−1} − 2^k − 2^{w−1} = 0。值不變。

```c
#include <stdint.h>
#include <stdio.h>

#define TYPE(x) _Generic((x), int: "int", unsigned: "unsigned int", \
                         long: "long", unsigned long: "unsigned long", default: "other")

int main(void)
{
    uint8_t x = 200, y = 100;
    uint8_t z = x + y;                  /* 寫回時才取模 2^8 */
    printf("x + y = %d (%s), z = %u\n", x + y, TYPE(x + y), z);

    int8_t s = -3;
    printf("(uint8_t) s            = %u\n", (uint8_t) s);
    printf("(int8_t) (uint8_t) s   = %d\n", (int8_t) (uint8_t) s);
    printf("(uint16_t) s           = 0x%04x\n", (uint16_t) s);
    printf("(uint16_t) (uint8_t) s = 0x%04x\n", (uint16_t) (uint8_t) s);

    uint16_t a = 65535, b = 65535;
    printf("TYPE(a * b) = %s\n", TYPE(a * b));
    printf("TYPE(-1 + 0u) = %s, value = %u\n", TYPE(-1 + 0u), -1 + 0u);
    printf("TYPE(-1 + 0ul) = %s\n", TYPE(-1 + 0ul));
    printf("TYPE(1u + 1L) = %s\n", TYPE(1u + 1L));
    return 0;
}
```

```
x + y = 300 (int), z = 44
(uint8_t) s            = 253
(int8_t) (uint8_t) s   = -3
(uint16_t) s           = 0xfffd
(uint16_t) (uint8_t) s = 0x00fd
TYPE(a * b) = int
TYPE(-1 + 0u) = unsigned int, value = 4294967295
TYPE(-1 + 0ul) = unsigned long
TYPE(1u + 1L) = long
```

逐行解讀：

- `(uint8_t)s`：−3 + 256 = 253（§2）。轉回 `int8_t` 依 GCC 的實作定義取模，回到 −3。
- `(uint16_t)s`：−3 先以數學值轉換，−3 + 65536 = 65533 = `0xfffd`，等同符號擴充。
- `(uint16_t)(uint8_t)s`：先變成 253，再零擴充成 `0x00fd`。**轉換順序不同，結果不同**——這是寫 parser 時最常見的 bug 來源之一。
- 第一行的 300：兩個 `uint8_t` 相加時，先被**提升**為 `int` 再相加，所以得到 300 而不是 44；寫回 `uint8_t` 時才截成 44。下面的「整數提升」小節會說明。

### 環：零因子與消去律

集合 R 帶有加法與乘法，若 (R, +) 是阿貝爾群、乘法可結合並有單位元素 1、乘法對加法分配，稱為**環（ring）**。ℤ/2^wℤ 是交換環，所以 `unsigned` 的 `+ - *` 滿足分配律，編譯器可以把 `a*b + a*c` 改寫成 `a*(b+c)`。

環的乘法不必有反元素。ℤ/2^wℤ（w ≥ 2）甚至有**零因子（zero divisor）**——兩個非零元素相乘得 0：`0x80000000u * 2 == 0`、`0x10000u * 0x10000u == 0`。零因子破壞**消去律**：`x·c == y·c` 推不出 `x == y`。c = 2 時，x 與 x + 2^{31} 乘以 2 得到同一個值；c 為奇數時則可以消去（下一小節解釋為什麼）。編譯器清楚這個差別：

```c
#include <stdint.h>
int cancel3(uint32_t x, uint32_t y) { return x * 3 == y * 3; }
int cancel2(uint32_t x, uint32_t y) { return x * 2 == y * 2; }
int div3(uint32_t n) { return n % 3 == 0; }
unsigned dist(unsigned a, unsigned b, unsigned c) { return a * b + a * c; }
uint32_t mul_lo(uint64_t a, uint64_t b) { return a * b; }
```

`gcc -O2 -S -masm=intel` 的輸出（刪去 `endbr64` 等指示詞）：

```
cancel3:                 cancel2:                 dist:
  xor   eax, eax           add   edi, edi           lea   eax, [rsi+rdx]
  cmp   edi, esi           add   esi, esi           imul  eax, edi
  sete  al                 xor   eax, eax           ret
  ret                      cmp   edi, esi
                           sete  al               mul_lo:
                           ret                      mov   eax, edi
                                                    imul  eax, esi
                                                    ret
```

clang 14 對 `cancel2` 做得更聰明：

```
cancel2:
  xor   edi, esi
  xor   eax, eax
  test  edi, 2147483647      ; 0x7FFFFFFF
  sete  al
  ret
```

因為 `2x ≡ 2y (mod 2³²)` 若且唯若 `x ≡ y (mod 2³¹)`，只比較低 31 位元。`dist` 被化簡成一次加法一次乘法（分配律）；`mul_lo` 雖然運算元是 64 位元，只取低 32 位元的積，所以只用 32 位元的 `imul`——這是下一小節「同態」的直接結果。

### 同態：低位元只取決於低位元

截斷 π：ℤ/2^wℤ → ℤ/2^kℤ（k ≤ w）保持加法與乘法，是**環同態（ring homomorphism）**。換成位元的說法：**和、差、積的低 k 位元，只取決於運算元的低 k 位元**。加法器的進位只往高位傳，在這裡成為一條代數定理，而且連乘法都受它保障。

實務推論：

1. 截斷可以提早做：`(uint8_t)(a * b) == (uint8_t)((uint8_t)a * (uint8_t)b)`。
2. 編譯器可以把運算縮窄：`mul_lo` 只用 32 位元乘法。
3. `x % (1u << k)` 等於 `x & ((1u << k) - 1)`，這個遮罩本身就是 π。

**右移與除法不是同態**，它們把高位元搬進低位元：`0x100` 與 `0x000` 的低 8 位元相同，右移一位後卻是 `0x80` 與 `0x00`。所以含 `/`、`%`（除數非 2 的冪）、`>>` 的運算式不能先截斷再算——`(x + y) / 2` 在寬度不足時出錯，就屬於這一類。

### 序關係與加法不相容：溢位檢查失效的代數根源

整數的 `<` 滿足「兩邊同加一數不改變大小」。ℤ/2^wℤ 上**不存在任何與加法相容的全序**：若存在，0 < 1 與 1 < 0 必有一者成立；前者逐次加 1 得 0 < 1 < 2 < … < 2^w = 0，矛盾；後者同理。無號比較與有號比較，都只是把圓環在某處剪開所得的線性順序，**跨過剪開處就會翻轉**：

```c
uint8_t a = 1, b = 2, c = 254;
printf("a < b: %d\n", a < b);
printf("(uint8_t)(a + c) = %u, (uint8_t)(b + c) = %u, 比較結果: %d\n",
       (uint8_t) (a + c), (uint8_t) (b + c), (uint8_t) (a + c) < (uint8_t) (b + c));
```

```
a < b: 1
(uint8_t)(a + c) = 255, (uint8_t)(b + c) = 0, 比較結果: 0
```

1 < 2，兩邊加 254 後變成 255 > 0。在 ℤ 中移項、兩邊同加一數都保持不等式，在 ℤ/2^wℤ 中則不保持。程式設計者若以 ℤ 的直覺寫出 `n * size > limit` 的檢查，回繞後的小乘積就通過檢查——這是 [Ch 22](./22-integer-overflow.md) 每個配置大小漏洞的共同根源。

### 奇數的乘法反元素

ℤ/2^wℤ 中有乘法反元素的元素，恰好是全部奇數。偶數 a 沒有：ax 永遠是偶數，ax − 1 是奇數，不可能是 2^w 的倍數。奇數 a 與 2^w 互質，由 Bézout 等式存在 x 使 ax ≡ 1。這就是 `cancel3` 可以化簡的原因：兩邊同乘 3 的反元素，3 就消掉了。

反元素可以用牛頓法求：迭代 `x ← x·(2 − a·x)`，若 `a·x = 1 − e` 且 e ≡ 0 (mod 2^k)，則下一步 `a·x′ = (1 − e)(1 + e) = 1 − e²`，正確位元數加倍。起點 `x₀ = (3a) ^ 2` 已有 5 個正確位元（依同態，逐一檢查 16 個奇數剩餘類即可證明），32 位元三步就夠（5 → 10 → 20 → 40）：

```c
#include <stdint.h>
#include <stdio.h>

/* a 為奇數；回傳 x 使 a * x ≡ 1 (mod 2^32) */
static uint32_t inv32(uint32_t a)
{
    uint32_t x = (a * 3) ^ 2;   /* 低 5 位元正確 */
    for (int i = 0; i < 3; i++) /* 5 → 10 → 20 → 40 位元 */
        x *= 2 - a * x;
    return x;
}

int main(void)
{
    printf("inv32(3) = 0x%08x, inv32(5) = 0x%08x\n", inv32(3), inv32(5));
    printf("3 * 0xaaaaaaab = 0x%08x\n", 3u * 0xaaaaaaabu);
    printf("21 * inv32(3) = %u  (21 / 3 = 7)\n", 21u * inv32(3));
    printf("22 * inv32(3) = %u  (22 不是 3 的倍數)\n", 22u * inv32(3));

    unsigned long bad = 0;
    for (uint64_t a = 1; a < (1ull << 32); a += 2)
        bad += (uint32_t) a * inv32((uint32_t) a) != 1;
    printf("odd a with a*inv32(a) != 1: %lu\n", bad);

    /* 整除測試：n % 3 == 0  <=>  n * inv(3) <= (2^32-1)/3 */
    bad = 0;
    for (uint64_t n = 0; n < (1ull << 32); n++)
        bad += ((uint32_t) n * 0xaaaaaaabu <= 0x55555555u) != ((uint32_t) n % 3 == 0);
    printf("divisibility mismatches: %lu\n", bad);
    return 0;
}
```

```
inv32(3) = 0xaaaaaaab, inv32(5) = 0xcccccccd
3 * 0xaaaaaaab = 0x00000001
21 * inv32(3) = 7  (21 / 3 = 7)
22 * inv32(3) = 2863311538  (22 不是 3 的倍數)
odd a with a*inv32(a) != 1: 0
divisibility mismatches: 0
```

（兩個窮舉迴圈共 3×2³¹ 次，在 Ryzen 7 5700G 上約 8.7 秒。）

反元素讓**整除變成乘法**：若 3 整除 n，`n × 0xaaaaaaab` 就是商；若不整除，乘積會落在 `0x55555555`（= ⌊(2³² − 1)/3⌋）以上。回頭看前面 `div3` 的組合語言：`imul edi, edi, -1431655765` 的常數就是 `0xaaaaaaab`，`cmp edi, 1431655765` 就是 `0x55555555`。clang 寫成 `cmp ecx, 1431655766` 加 `setb`（小於 0x55555556），等價。這套手法出自 Granlund 與 Montgomery 的 PLDI 1994 論文 *Division by Invariant Integers using Multiplication*；[Ch 24](./24-bitwise-tricks.md) 的「數值語意與編譯器最佳化」會再看到指標相減如何用同一招。

## 整數提升與一般算術轉換（預告）

前面 `x + y = 300` 的現象叫**整數提升（integer promotions）**，C11 6.3.1.1 §2：

> If an int can represent all values of the original type (as restricted by the width, for a bit-field), the value is converted to an int; otherwise, it is converted to an unsigned int. These are called the integer promotions.

意思是：**比 `int` 窄的型別（`char`、`short`、`uint8_t`、`uint16_t`、位元欄位）在算術之前一律先變成 `int`**（`int` 裝不下才變 `unsigned int`）。所以我們前面說「w 是提升之後的寬度，不是宣告的寬度」。

兩個運算元型別不同時，接著做**一般算術轉換（usual arithmetic conversions，C11 6.3.1.8）**，粗略的規則：

```
1. 先對兩邊各自做整數提升
2. 型別相同 → 結束
3. 同為有號或同為無號 → 轉成「等級（rank）」高的那個
4. 無號的等級 ≥ 有號的等級 → 有號的一方轉成無號        ← -1 < 0u 為假的元兇
5. 有號的型別能表示無號型別的所有值 → 無號的一方轉成有號  ← 1u + 1L 是 long（LP64）
6. 否則 → 兩邊都轉成「有號型別對應的無號型別」

等級由低到高：_Bool < char < short < int < long < long long
```

`TYPE(-1 + 0u)` 是 `unsigned int`（規則 4），−1 變成 4294967295；`TYPE(1u + 1L)` 是 `long`（規則 5，LP64 上 `long` 是 64 位元，裝得下所有 `unsigned`）。

提升最陰險的後果是：**兩個無號的 16 位元相乘，可能變成有號溢位**。

```c
#include <stdint.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    (void) argv;
    uint16_t a = 65535, b = (uint16_t) (65534 + argc);  /* 執行時才知道：65535 */
    uint32_t r = a * b;            /* a、b 先提升為 int，int 乘法溢位 */
    uint32_t ok = (uint32_t) a * b;
    printf("%u %u\n", r, ok);
    return 0;
}
```

```
$ gcc -O0 -fsanitize=undefined mul16.c -o mul16 && ./mul16
4294836225 4294836225
mul16.c:8:20: runtime error: signed integer overflow: 65535 * 65535 cannot be represented in type 'int'
```

印出來的數字看起來都對，但 UBSan 抓到第 8 行是未定義行為：65535 × 65535 = 4294836225 超過 `INT_MAX`。寫成 `(uint32_t)a * b` 讓乘法在 `unsigned int` 中進行才安全。下一章會把這些規則接上真實漏洞，[Ch 23](./23-bitwise-basics.md) 的「有號與無號混用的比較」會再展開比較運算子的陷阱。

### C23 的 `_BitInt`：不提升的整數

C23 加入 **bit-precise 整數** `_BitInt(N)` 與 `unsigned _BitInt(N)`（6.2.5 §5），並在 6.3.1.1 §2 加上前提「If the original type is not a bit-precise integer type」——它們**不做整數提升**。以下在 Docker `gcc:14` 以 `-std=c23` 執行：

```c
#include <limits.h>
#include <stdint.h>
#include <stdio.h>

#define TYPE(x) _Generic((x), int: "int", unsigned _BitInt(8): "unsigned _BitInt(8)", \
                         default: "other")

int main(void)
{
    uint8_t x = 200, y = 100;
    unsigned _BitInt(8) a = 200, b = 100;
    printf("uint8_t:             x + y = %d (%s)\n", x + y, TYPE(x + y));
    printf("unsigned _BitInt(8): a + b = %u (%s)\n", (unsigned) (a + b), TYPE(a + b));
    printf("unsigned _BitInt(8): a + 1 -> %s\n", TYPE(a + 1));
    unsigned _BitInt(4) u = 15;
    u++;
    printf("unsigned _BitInt(4): 15 + 1 = %u\n", (unsigned) u);
    printf("BITINT_MAXWIDTH = %llu\n", (unsigned long long) BITINT_MAXWIDTH);
    return 0;
}
```

```
uint8_t:             x + y = 300 (int)
unsigned _BitInt(8): a + b = 44 (unsigned _BitInt(8))
unsigned _BitInt(8): a + 1 -> int
unsigned _BitInt(4): 15 + 1 = 0
BITINT_MAXWIDTH = 65535
```

兩個 `unsigned _BitInt(8)` 相加直接在 8 位元中取模，得 44；但 `a + 1` 的 `1` 是 `int`，一般算術轉換仍然適用，結果是 `int`。300 與 44 在低 8 位元一致（300 = 256 + 44），這正是前面「同態」的保證。`BITINT_MAXWIDTH` 依實作而異：GCC 14（x86-64）為 65535，原文記錄 Clang 18 為 8388608、arm64 上的 Clang 18 只有 128。gcc 11 不支援 `_BitInt`。

## 對比與取捨

| 表示法 | 範圍（4 位元） | 零的個數 | 取負 | 加法器 | 現況 |
|---|---|---|---|---|---|
| sign-magnitude | −7..7 | 2（`0000`、`1000`） | 翻最高位 | 要先比大小再決定加或減 | IEEE 754 浮點數的符號仍是這種 |
| 一補數（ones' complement） | −7..7 | 2（`0000`、`1111`） | 全部翻轉 | 需要 end-around carry（進位要繞回加到最低位） | 網路的 Internet checksum（RFC 1071）仍用一補數加法 |
| 二補數（two's complement） | −8..7 | 1 | 翻轉再加一 | 與無號共用同一個加法器 | C23 起唯一合法的有號表示 |
| 偏移碼（biased / excess-K） | 依 K | 1 | 2K − x | 需要修正 | IEEE 754 的指數欄位（[Ch 25](./25-ieee754.md)） |
| 平衡三進位 | −40..40（4 trit） | 1 | 逐位反轉 | 三態元件 | 研究與教學；Setun（1958） |

二補數贏在**兩件事**：只有一個零（比較相等不用特判），以及加法器與無號共用（硬體最省）。代價是範圍不對稱，`-INT_MIN` 無法表示。

## 踩雷集錦

1. **「printf 印出 0.9，所以它就是 0.9」**：Python 的 `repr` 與 `%g` 都會挑「夠短」的十進位字串。要看真值，用 `%.17g` 或 `%a`（十六進位浮點）。
2. **「`int` 溢位會繞回，跟 `unsigned` 一樣」**：在硬體上是，在 C 語言上不是。`int` 的語意是 ℤ，溢位是未定義行為，編譯器可以據此刪掉你的檢查（[Ch 22](./22-integer-overflow.md)）。需要模 2^w 的語意，請用無號型別。
3. **「`uint8_t` 相加會在 8 位元中繞回」**：不會，它先被提升成 `int`，`200 + 100` 是 300，寫回 `uint8_t` 才變 44。兩個 `uint16_t` 相乘甚至會變成有號溢位。
4. **「`(uint16_t)(int8_t)-3` 與 `(uint16_t)(uint8_t)(int8_t)-3` 一樣」**：前者符號擴充得 `0xfffd`，後者零擴充得 `0x00fd`。轉換的**順序**決定了取哪個代表元。
5. **「`abs(x)` 一定是非負數」**：`abs(INT_MIN)` 是未定義行為，因為 −INT_MIN 不在 `int` 的範圍內；在 GCC/Clang 的實際行為通常是回傳 `INT_MIN` 本身（負數）。要嘛先排除 `INT_MIN`，要嘛改用無號運算 `0u - (unsigned)x`。
6. **「`0x80000000` 和 `-2147483648` 都是 `int`」**：都不是。`-2147483648` 是一元負號作用在 `2147483648` 上，後者超出 `int`，C99 起變成 `long`（LP64 上 `sizeof` 為 8）；`0x80000000` 是 `unsigned int`。這就是 `<limits.h>` 把 `INT_MIN` 定義成 `(-INT_MAX - 1)` 的原因（[Ch 23](./23-bitwise-basics.md) 有實測）。

## 進階：再往深一層

**同一組位元上的其他結構。** 換一組運算，同樣 2^w 個位元樣式就是另一個代數結構：

- 以 XOR 為加法，`({0,1}^w, ⊕)` 也是阿貝爾群，但每個元素都是自己的反元素（`x ^ x == 0`），和 ℤ/2^wℤ 不同構。把位元看成二元素的域 GF(2)（加法 XOR、乘法 AND），`{0,1}^w` 就是 GF(2) 上的 w 維向量空間。邏輯位移、循環位移、Gray code 的 `x ^ (x >> 1)`、xorshift 的每一步，都是這個空間上的線性映射。右移對 XOR 是同態，對 `+` 不是——兩種結構的差別在這裡具體可見。這是 [Ch 24](./24-bitwise-tricks.md)「XOR 加解密」的數學基礎。
- AND、OR、NOT 構成**布林代數**；`&` 與 `|` 有單位元素卻沒有反元素，不構成群。

**飽和運算：用反元素換取單調。** 飽和加法 `min(a + b, MAX)` 可交換、可結合（無號版本），但非零元素沒有反元素，只是**么半群（monoid）**；有號版本連結合律都失去：`(100 ⊞ 100) ⊞ (−100) = 127 − 100 = 27`，`100 ⊞ (100 ⊞ (−100)) = 100`。失去反元素換來的是單調性：大的永遠不會「繞」成小的。原文的結論很漂亮：**有限集合上，群結構與相容的全序只能擇一**。Linux 核心的 `refcount_t` 與 `size_mul()` 都選了後者（[Ch 22](./22-integer-overflow.md)）。

**浮點數：連半群都不是。** 浮點加法可交換，但捨入讓結合律失效，`(3.14 + 1e10) − 1e10` 在 `float` 中是 0。單位元素甚至是 −0 而不是 +0（`(−0) + (+0) = +0`）。C11 5.1.2.3 §14 EXAMPLE 5 因此禁止編譯器自行重新結合浮點運算。細節在 [Ch 25](./25-ieee754.md)、[Ch 26](./26-floating-point-errors.md)。

**奇數乘法群的結構。** 奇數在乘法下構成 2^{w−1} 個元素的阿貝爾群，w ≥ 3 時同構於 C₂ × C_{2^{w−2}}，可由 −1 與 5 生成；`x² = 1` 有四個解（±1、2^{w−1} ± 1）。一個實務後果：乘法同餘產生器 `x ← a·x mod 2^w` 的週期至多 2^{w−2}（Knuth TAOCP 第 2 卷 3.2.1.2 節）。

## 動手練習

1. 把 `to_bal3` 改成支援 `long long`，找出 64 位元 `long long` 的最大值寫成平衡三進位要幾個 trit，並與 ⌈log₃(2⁶³)⌉ 對照。
2. 寫一個 `uint8_t sat_add_u8(uint8_t a, uint8_t b)`，不用 `if`（提示：先在 `unsigned` 中相加，比較結果與 255），用 2¹⁶ 組窮舉驗證可交換、可結合；再寫有號版本，數出不可結合的三元組比例（原文給的答案約 24.9%，你可以只抽樣）。
3. 用 `inv32` 寫一個「已知整除」的除法 `exact_div(n, d)`（d 為奇數），與 `n / d` 比對 10⁶ 組亂數（記得只挑 d 的倍數）。再把 d 改成偶數，想想要先做什麼。
4. 不看本章，用 `U(x)` 與 `S(x)` 的定義寫出「符號擴充保持值不變」的證明，再用 8 → 16 位元的 256 個樣式窮舉驗證。
5. 故意弄壞：把 `mul16.c` 的 `uint16_t` 改成 `uint8_t`，還會溢位嗎？為什麼？再把 `int` 想成 16 位元（嵌入式平台），`uint16_t` 會被提升成什麼？

## 本章重點整理

- 進位制是選擇：radix economy 理論上偏好 3，但二進位贏在元件只要分辨兩種狀態。
- w 位元整數是 ℤ/2^wℤ 的剩餘類；無號與二補數是同一剩餘類的兩個代表元，所以共用同一個加法器與乘法器（低半部）。
- `-x == ~x + 1` 來自 `x + ~x == 2^w − 1 ≡ −1`；也可由 `x + y = (x ^ y) + 2(x & y)` 推出。
- C 只把模 2^w 的保證給無號型別；有號溢位是未定義行為，即使 C23 統一了二補數。
- 整數轉換就是換代表元；變寬時有號做符號擴充、無號做零擴充；轉換順序會改變結果。
- 截斷是環同態（低位元只看低位元），右移與除法不是；ℤ/2^wℤ 上沒有與加法相容的全序——這是溢位檢查失效的根源。
- 奇數有乘法反元素，編譯器用它把 `n % 3 == 0` 變成一次乘法一次比較。
- 比 `int` 窄的型別在運算前先被提升成 `int`；一般算術轉換讓 `-1 < 0u` 為假。

## 自我檢核

- [ ] 不看筆記，能用「里程表」與「剪開的圓」解釋為什麼無號與有號共用同一個加法器嗎？
- [ ] 有人問「為什麼負數比正數多一個」，你能用一句話從代表元的角度回答嗎？
- [ ] 能寫出 `-x == ~x + 1` 的至少兩種推導嗎？
- [ ] 能說出 C 標準在哪一條規定無號運算不會溢位、哪一條讓有號溢位成為未定義行為嗎？
- [ ] 給你 `int8_t s = -3`，能不跑程式就說出 `(uint16_t)s` 與 `(uint16_t)(uint8_t)s` 的值並解釋原因嗎？
- [ ] 能解釋為什麼 gcc 可以把 `x*3 == y*3` 化簡成 `x == y`，卻不能對 `x*2 == y*2` 做同樣的事嗎？
- [ ] 想一個你寫過的「先加再檢查有沒有超過上限」的程式，它在 ℤ/2^wℤ 中還成立嗎？

## 延伸閱讀

### 論文

- **[Division by Invariant Integers using Multiplication](https://doi.org/10.1145/178243.178249)** — Torbjörn Granlund, Peter L. Montgomery，PLDI（1994）
  - **核心貢獻**：系統化地說明編譯器如何把除以常數變成乘法與位移，包含本章的「奇數乘法反元素做整除測試」與「已知整除時的精確除法」。
  - **讀哪裡**：Section 9 "Exact division by constants" 與整除測試的部分最貼近本章；前半的 magic number 推導可以留到 [Ch 24](./24-bitwise-tricks.md) 再讀。
  - **前提知識**：本章的同餘與反元素；會看 x86 組合語言更好。

- **[A Symbolic Analysis of Relay and Switching Circuits](https://dspace.mit.edu/handle/1721.1/11173)** — Claude E. Shannon，MIT 碩士論文（1938，刊於 Transactions of the AIEE）
  - **核心貢獻**：證明布林代數可以描述與設計開關電路，把「0 與 1」從數學帶進工程。
  - **讀哪裡**：Part II 的基本定理與 Part III 的電路例子；不必細讀每個電路圖。
  - **和本章的關聯**：二進位從 Leibniz 的數論走到電路的那一步。

### 部落格 / 技術文章

- **[解讀計算機編碼](https://hackmd.io/@sysprog/binary-representation)** — 黃敬群（jserv）
  - **這篇說什麼**：以時鐘模型圖解有限位元的加法、一補數與二補數的對稱軸關係，以及 C 標準中三種表示法的由來（含促成 C23 修改的 WG14 N2412）。
  - **讀哪裡**：「第二部分：圖解有限長度二進位運算與二補數編碼」與「C 語言標準中的三種表示法」。
  - **為什麼值得讀**：本章「推導三」的完整圖解版；原文作者的配套文章。

- **[The Balanced Ternary Machines of Soviet Russia](https://dev.to/buntine/the-balanced-ternary-machines-of-soviet-russia)** — Andrew Buntine
  - **這篇說什麼**：Setun 與 Setun-70 的歷史、平衡三進位的運算規則。
  - **讀哪裡**：整篇，十分鐘可讀完。
  - **前提知識**：本章平衡三進位一節。

### 官方文件 / Spec

- **[ISO/IEC 9899 草案 N1570（C11）](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.2.5 §9（無號不溢位）、6.2.6.2（整數表示）、6.3.1.1（整數提升）、6.3.1.3（整數轉換）、6.3.1.8（一般算術轉換）、6.5 §5（例外狀況是 UB）。
  - **注意**：C23 對應條款在 [N3220](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3220.pdf)；6.2.6.2 §2 改為只允許二補數，6.3.1.1 §2 加上 `_BitInt` 例外。

- **[GCC Manual: Integers implementation](https://gcc.gnu.org/onlinedocs/gcc/Integers-implementation.html)**
  - **讀哪裡**：整頁很短；記下「轉換為有號型別時取模 2^N」與「有號 `>>` 是符號擴充」兩條，它們是本章與 [Ch 23](./23-bitwise-basics.md) 許多寫法成立的前提。

### 書籍

- **《Computer Systems: A Programmer's Perspective》3rd ed.** — Bryant & O'Hallaron（Pearson，2015）
  - **這本書的定位**：本章內容的教科書版本，例子多、練習題多。
  - **讀哪幾章**：2.2（整數表示，含 Figure 2.19 的有號無號比較表）、2.3（整數算術，2.3.1–2.3.3 的模運算與二補數加法）。
  - **前提知識**：無；可以和本章交替讀。

- **《The Art of Computer Programming, Vol. 2: Seminumerical Algorithms》3rd ed.** — Donald E. Knuth（Addison-Wesley，1997）
  - **這本書的定位**：進位制與整數算術的權威參考，出版雖早但內容不過時。
  - **讀哪幾章**：4.1（Positional Number Systems，含平衡三進位的讚美）；3.2.1.2（乘法同餘產生器的週期，對應本章「進階」）。

我們已經有了「整數是一個環」的模型，也看到有號與無號在規格書裡被分成兩個世界。下一章把這個模型放到真實世界：計數器回繞、配置大小回繞、截斷、編譯器刪掉溢位檢查——以及怎麼防。

→ [Ch 22 integer overflow：語意、資安案例與防禦](./22-integer-overflow.md)
