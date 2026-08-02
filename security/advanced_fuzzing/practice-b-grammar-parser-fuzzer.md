# 練習 B — 為真實 Parser 寫文法 Fuzzer

## 目標

- 把真實 parser 的輸入語言形式化為 CFG（context-free grammar）
- 實作 generational grammar fuzzer，用純 Python 生成語法合法的測試輸入
- 找到 mini-calc parser 裡藏的兩個 crash bug（stack overflow、division by zero 的整數路徑）
- 理解 float division by zero 和 integer division by zero 在行為上的本質差異
- （進階）接 libFuzzer harness 做 coverage-guided grammar fuzzing

---

## 背景

Grammar-based fuzzing 是處理有格式要求的輸入時最直接的方法。Parser 類型的 target 只接受符合文法的輸入，純隨機 bit flipping 大多會立刻被 lexer 或 parser 拒絕，浪費絕大多數執行預算在無效輸入上。Generational grammar fuzzer 的做法是反過來：先定義文法，再從文法生成輸入，確保每個 test case 都有意義。

這個練習用一個 mini expression language（mini-calc）作為 target。Parser 接受算術表達式：

- 整數 `42`、浮點 `3.14`、負數 `-1`
- 四則運算：`+` `-` `*` `/`
- 括號分組：`(expr)`
- 比較運算：`>` `<` `==` `!=`（回傳 0 或 1）

Parser 裡面藏了三個 bug，你要用 fuzzer 找到其中兩個可觸發 crash 的：

1. **Division by zero**（Bug #1）：`expr / 0` 沒有保護，但因為 parser 用 `double`，IEEE 754 下 float division by zero 回傳 `inf` 不 crash——實際觸發需要走整數計算路徑或 UBSan
2. **Stack overflow**（Bug #2）：深度嵌套括號 `((((...))))` 會讓 `parse_factor()` 遞歸超過 `MAX_DEPTH=100`，直接 `exit(1)`
3. **Integer overflow**（Bug #3）：大數運算在 `int` 路徑下是 UB，此 parser 用 `double` 所以不會爆——需要 UBSan 才能偵測

**可以被 grammar fuzzer 直接找到的**：Bug #2（stack overflow）。Bug #1 在 float path 不 crash，需要 UBSan 路徑或特殊整數上下文。

---

## mini-calc Parser 原始碼

以下是完整可 build 的 `mini_calc.c`，存成檔案後才能執行練習：

```c
/* mini_calc.c — mini expression parser，含三個隱藏 bug */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

static const char *pos;
static int depth;
#define MAX_DEPTH 100

static double parse_expr(void);
static double parse_term(void);
static double parse_factor(void);

static void skip_ws(void) {
    while (*pos && isspace((unsigned char)*pos)) pos++;
}

static double parse_number(void) {
    skip_ws();
    char *end;
    double val = strtod(pos, &end);
    if (end == pos) {
        /* 沒有數字可解析，回傳 0 */
        val = 0.0;
    }
    pos = end;
    return val;
}

static double parse_factor(void) {
    skip_ws();
    if (depth++ > MAX_DEPTH) {
        fprintf(stderr, "ERR: stack overflow (depth > %d)\n", MAX_DEPTH);
        exit(1);  /* Bug #2: 遞歸深度超過限制 */
    }
    double val;
    if (*pos == '(') {
        pos++;  /* consume '(' */
        val = parse_expr();
        skip_ws();
        if (*pos == ')') pos++;
    } else if (*pos == '-') {
        pos++;
        val = -parse_factor();
    } else {
        val = parse_number();
    }
    depth--;
    return val;
}

static double parse_term(void) {
    double left = parse_factor();
    skip_ws();
    while (*pos == '*' || *pos == '/') {
        char op = *pos++;
        double right = parse_factor();
        if (op == '*') {
            left *= right;
        } else {
            /* Bug #1: 沒有 division by zero 檢查 */
            /* float path: 1/0 => inf (不 crash) */
            /* UBSan 才能在整數上下文抓到 */
            left /= right;
        }
        skip_ws();
    }
    return left;
}

static double parse_expr(void) {
    double left = parse_term();
    skip_ws();
    while (*pos == '+' || *pos == '-') {
        char op = *pos++;
        double right = parse_term();
        if (op == '+') {
            left += right;  /* Bug #3: int overflow (double path 不觸發) */
        } else {
            left -= right;
        }
        skip_ws();
    }
    return left;
}

double calc(const char *expr) {
    pos = expr;
    depth = 0;
    return parse_expr();
}

#ifndef FUZZING
int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <expr>\n", argv[0]);
        return 1;
    }
    double result = calc(argv[1]);
    printf("%.6g\n", result);
    return 0;
}
#endif
```

Build 與手動驗證：

```bash
gcc -o mini_calc mini_calc.c && echo 'build OK'

./mini_calc "1+2*3"        # => 7
./mini_calc "10/2"         # => 5
./mini_calc "(1+2)*3"      # => 9
./mini_calc "1/0"          # => inf  (不 crash，IEEE 754)
```

---

## 任務規格

### 方案 A — Python 文法 Fuzzer（主線）

**輸入**：mini-calc 表達式字串，只含 `0-9`、`.`、`+`、`-`、`*`、`/`、`(`、`)`、空格，長度 1–200 bytes

**輸出**：找到至少 1 個 crash，列出觸發輸入與錯誤訊息

**限制**：不得只靠單一 hardcode 輸入；fuzzer 必須從文法生成，bias 技巧是允許的（詳見提示 3）

**驗收標準**：
- 文法能生成 100% 語法合法的 mini-calc 表達式（目視驗收 20 個）
- 5000 次執行內找到 stack overflow crash
- 能解釋 `1/0` 為什麼不 crash

### 方案 B — libFuzzer Harness（進階）

需要 clang，在 Linux/WSL 下執行。Harness 如下：

```c
/* fuzz_calc.c */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdlib.h>

extern double calc(const char *);

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0 || size > 200) return 0;
    char *buf = malloc(size + 1);
    if (!buf) return 0;
    memcpy(buf, data, size);
    buf[size] = '\0';
    for (size_t i = 0; i < size; i++) {
        if ((unsigned char)buf[i] > 127) {
            free(buf);
            return 0;
        }
    }
    calc(buf);
    free(buf);
    return 0;
}
```

Build：

```bash
clang -DFUZZING -g -O1 \
      -fsanitize=fuzzer,address,undefined \
      -fno-omit-frame-pointer \
      -o fuzz_calc mini_calc.c fuzz_calc.c

mkdir -p corpus
echo -n "1+2" > corpus/s1
echo -n "10/2" > corpus/s2

./fuzz_calc corpus/ -dict=calc.dict -max_total_time=60 -print_final_stats=1
```

dictionary `calc.dict`：

```
"+"
"-"
"*"
"/"
"("
")"
"0"
"1"
"."
"99"
```

---

## 期望輸出

方案 A 執行後應看到類似輸出：

```
[Grammar Fuzzer] mini-calc bug hunter
Building mini_calc...
Build OK

  [    0] 1                                         => 1
  [   10] 3+7                                       => 10
  [   30] (4*2)+1                                   => 9
  [  200] 7/3                                       => 2.33333

  ==================================================
  *** CRASH #1 found at iter 312 ***
  type : stack_overflow
  expr : (((((((((((((((((((((((((((((((((((((((((((((((1))))))))))))))))))))))))))))))))))))))))))))))))
  exit : 1
  stderr: ERR: stack overflow (depth > 100)
  ==================================================

=== 結果 ===
總執行次數: 313
找到 1 個 unique crash:

  Bug #1 [stack_overflow] @ iter 312
    expr  : (((((((((((((((((((((((((((((((((((((((((((((((1))))))))))))))))))))))))))))))))))))))))))))))))
    stderr: ERR: stack overflow (depth > 100)
```

---

## 卡住提示

### 提示 1：文法要怎麼寫才能生成 `x/0`

`number` 規則必須包含 `'0'` 作為可能的展開。Division 的產生式 `term → term / factor` 在展開 `factor` 時必須讓 `0` 有機會被選中。如果你的 `number` 規則只生成 `1-9` 開頭的數字，永遠不會生成零除。

額外準備一個偏向 zero-division 的 sub-grammar，讓 `term → number / zero` 而 `zero → '0'`，然後以一定機率強制走這條路。要注意：在這個 parser 裡，`float / 0` 不 crash（回傳 inf），所以 division by zero 不是靠 grammar fuzzer 找到的核心 bug。

### 提示 2：深度嵌套要怎麼生成

文法中 `factor → '(' expr ')'` 是遞歸規則。正常的 generational fuzzer 會設 `max_depth` 來截斷遞歸，避免無限展開。要觸發 Bug #2，你需要：

- 知道 `MAX_DEPTH=100`，所以要讓 `parse_factor()` 被呼叫超過 100 次
- 在 fuzzer 裡加一個「deep nesting mode」：強制將 `factor` 展開成 `( factor )`，重複 110 次，再在中心放一個 `number`
- 這不算作弊，因為 `( expr )` 是文法的合法產生式，你只是選了一條極端的展開路徑

### 提示 3：gen() 遞歸要怎麼控制深度

`gen(sym, depth, max_depth)` 遇到遞歸 non-terminal 時，`depth` 每遞歸一層加一。當 `depth >= max_depth` 時，對遞歸 non-terminal 直接回傳 terminal 預設值（例如 `expr` → `'1'`）。

正常模式設 `max_depth=6`，生成長度合理的表達式。Deep nesting 模式繞過這個機制，直接字串相乘：`'(' * n + '1' + ')' * n`，其中 `n` 選 105–115 確保超過 `MAX_DEPTH=100`。

### 提示 4：subprocess 怎麼偵測 crash

```python
import subprocess

result = subprocess.run(
    ['./mini_calc', expr],
    capture_output=True,
    text=True,
    timeout=2
)
crashed = (result.returncode != 0) or ('ERR:' in result.stderr)
```

`returncode != 0` 抓 `exit(1)` 的 crash。`'ERR:'` 是這個 parser 自訂的 stderr 前綴。如果 parser 被 signal 殺掉（如 SIGSEGV），`returncode` 在 Python 裡會是負數（`-11` = SIGSEGV）。

### 提示 5：為什麼 `1/0` 不 crash

IEEE 754 定義 `1.0 / 0.0 = +Inf`，`0.0 / 0.0 = NaN`，兩者都是合法的 float 值，不產生任何 signal 或 exception（除非你手動呼叫 `feenableexcept(FE_DIVBYZERO)`）。這個 parser 的除法路徑是 `double left /= double right`，所以 `1/0` 正常回傳 `inf`，exit code 0。

要讓 Bug #1 變成真的 crash，需要：
- 方案 B（libFuzzer + `-fsanitize=undefined`）：UBSan 把某些浮點轉換的 overflow 抓出來
- 或修改 parser 讓除法走整數路徑（`int` 類型），然後觸發 SIGFPE

---

## 實作步驟

### Step 1：確認 mini_calc 可 build，手動驗證 stack overflow

```bash
gcc -o mini_calc mini_calc.c

# 正常輸入
./mini_calc "1+2*3"
./mini_calc "(10-3)*2"

# 手動生成深度 110 的嵌套，確認 crash
python3 -c "print('(' * 110 + '1' + ')' * 110)" | xargs ./mini_calc
# 應看到: ERR: stack overflow (depth > 100)
# 確認 exit code
echo $?   # => 1
```

### Step 2：定義文法（Python dict）

每個 non-terminal 對應一個 list of alternatives。每個 alternative 是 token list（token 是 terminal string 或 non-terminal name）：

```python
CALC_GRAMMAR = {
    'expr':   [
        ['term'],
        ['expr', '+', 'term'],
        ['expr', '-', 'term'],
    ],
    'term':   [
        ['factor'],
        ['term', '*', 'factor'],
        ['term', '/', 'factor'],
    ],
    'factor': [
        ['number'],
        ['(', 'expr', ')'],
        ['-', 'factor'],
    ],
    'number': [
        ['digit'],
        ['digit', 'digits'],
        ['digit', '.', 'digits'],
    ],
    'digits': [
        ['digit'],
        ['digit', 'digits'],
    ],
    'digit': ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'],
}
```

### Step 3：實作 gen() 遞歸生成函數

```python
import random

FALLBACK = {
    'expr': '1', 'term': '1', 'factor': '1',
    'number': '1', 'digits': '0', 'digit': '1',
}

def gen(sym, grammar, depth=0, max_depth=6):
    """從 grammar 遞歸展開 sym，depth >= max_depth 時回傳 fallback terminal。"""
    if sym not in grammar:
        return sym  # 直接是 terminal string
    if depth >= max_depth:
        return FALLBACK.get(sym, '1')
    alts = grammar[sym]
    alt = random.choice(alts)
    if isinstance(alt, str):
        return alt  # digit 的單字元 terminal
    return ''.join(gen(tok, grammar, depth + 1, max_depth) for tok in alt)
```

驗收：跑 20 次，每個輸出都應該被 `./mini_calc` 接受（exit 0）：

```python
for _ in range(20):
    expr = gen('expr', CALC_GRAMMAR)
    print(repr(expr))
```

### Step 4：加入 deep nesting 生成函數

```python
def gen_deep_nesting(n=None):
    """生成 n 層括號嵌套，強制觸發 Bug #2（MAX_DEPTH=100）。"""
    if n is None:
        n = random.randint(105, 115)
    return '(' * n + '1' + ')' * n
```

### Step 5：實作 crash 偵測迴圈，加入 bias 模式

```python
import subprocess

def run_calc(expr, timeout=2):
    try:
        r = subprocess.run(
            ['./mini_calc', expr],
            capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -999, '', 'TIMEOUT'

def fuzz(n=5000):
    crashes = []
    seen_types = set()

    for i in range(n):
        roll = random.random()
        if roll < 0.10:
            # 10%: deep nesting，強制超過 MAX_DEPTH=100
            expr = gen_deep_nesting()
        elif roll < 0.20:
            # 10%: zero-division（float path，不 crash，但觀察行為）
            numer = random.choice(['1', '2', '5', '42'])
            expr = f"{numer}/0"
        else:
            # 80%: 正常文法生成
            expr = gen('expr', CALC_GRAMMAR)

        code, out, err = run_calc(expr)

        is_crash = (code != 0) or ('ERR:' in err) or (code == -999)
        if is_crash:
            ctype = 'stack_overflow' if 'overflow' in err else f'code_{code}'
            if ctype not in seen_types:
                seen_types.add(ctype)
                crashes.append({'iter': i, 'expr': expr,
                                 'code': code, 'err': err, 'type': ctype})
                print(f"\n*** CRASH #{len(crashes)} @ iter {i}: {ctype}")
                print(f"  expr: {expr[:70]}")
                print(f"  err:  {err[:80]}")

        if len(crashes) >= 3:
            break

    return crashes
```

### Step 6：（方案 B）libFuzzer harness + UBSan

在 WSL/Linux 下：

```bash
clang -DFUZZING -g -O1 \
      -fsanitize=fuzzer,address,undefined \
      -fno-omit-frame-pointer \
      -o fuzz_calc mini_calc.c fuzz_calc.c

mkdir -p corpus
echo -n "1+2" > corpus/s1
echo -n "(3*4)/2" > corpus/s2
echo -n "10-5" > corpus/s3

./fuzz_calc corpus/ -dict=calc.dict -max_total_time=60 -print_final_stats=1
```

UBSan 在觸發 undefined behavior 時輸出：

```
runtime error: ...
SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior mini_calc.c:XX in parse_term
```

---

## 完整參考解答

<details>
<summary>展開參考解答（fuzzer_b.py，可直接執行）</summary>

```python
#!/usr/bin/env python3
"""
練習 B 參考解答 — mini-calc grammar fuzzer
用法: python3 fuzzer_b.py
前提: mini_calc.c 在同一目錄，gcc 可用
"""
import random
import subprocess
import sys
import os

# ── 文法定義 ──────────────────────────────────────────────────────────────────

CALC_GRAMMAR = {
    'expr':   [
        ['term'],
        ['expr', '+', 'term'],
        ['expr', '-', 'term'],
    ],
    'term':   [
        ['factor'],
        ['term', '*', 'factor'],
        ['term', '/', 'factor'],
    ],
    'factor': [
        ['number'],
        ['(', 'expr', ')'],
        ['-', 'factor'],
    ],
    'number': [
        ['digit'],
        ['digit', 'digits'],
        ['digit', '.', 'digits'],
    ],
    'digits': [
        ['digit'],
        ['digit', 'digits'],
    ],
    'digit': ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'],
}

# 當 depth 超限時的 fallback terminal
FALLBACK = {
    'expr': '1', 'term': '1', 'factor': '1',
    'number': '1', 'digits': '0', 'digit': '1',
}

# ── 生成器 ────────────────────────────────────────────────────────────────────

def gen(sym, grammar, depth=0, max_depth=6):
    """從 grammar 遞歸生成 sym 的一個展開。"""
    if sym not in grammar:
        return sym
    if depth >= max_depth:
        return FALLBACK.get(sym, '1')
    alts = grammar[sym]
    alt = random.choice(alts)
    if isinstance(alt, str):
        return alt
    return ''.join(gen(tok, grammar, depth + 1, max_depth) for tok in alt)


def gen_deep_nesting(n=None):
    """生成 depth > MAX_DEPTH 的嵌套括號，強制觸發 Bug #2。"""
    if n is None:
        n = random.randint(105, 115)
    return '(' * n + '1' + ')' * n


# ── 執行與偵測 ────────────────────────────────────────────────────────────────

def run_calc(binary, expr, timeout=2):
    """執行 mini_calc，回傳 (returncode, stdout, stderr)。"""
    try:
        r = subprocess.run(
            [binary, expr],
            capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -999, '', 'TIMEOUT'
    except FileNotFoundError:
        print(f"ERROR: binary '{binary}' not found", file=sys.stderr)
        sys.exit(1)


def classify(expr, code, err):
    """把 crash 分類，用於去重。"""
    if 'overflow' in err:
        return 'stack_overflow'
    if code == -999:
        return 'timeout'
    if code < 0:
        return f'signal_{-code}'
    return f'exit_{code}'


# ── 主程式 ────────────────────────────────────────────────────────────────────

def build(src='mini_calc.c', out='mini_calc'):
    print(f'[*] Building {src} -> {out}')
    r = subprocess.run(
        ['gcc', '-o', out, src],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print('Build FAILED:', r.stderr)
        sys.exit(1)
    print('[*] Build OK\n')
    return f'./{out}'


def main():
    random.seed()  # 不固定 seed，讓每次跑結果不同

    src = 'mini_calc.c'
    if not os.path.exists(src):
        print(f"ERROR: {src} not found in current directory")
        sys.exit(1)

    binary = build(src)

    print('[Grammar Fuzzer] mini-calc bug hunter')
    print('=' * 60)

    crashes = []
    seen_types = set()
    total = 5000

    for i in range(total):
        roll = random.random()
        if roll < 0.10:
            expr = gen_deep_nesting()
            mode = 'deep'
        elif roll < 0.20:
            numer = random.choice(['1', '2', '5', '42', '100'])
            expr = f"{numer}/0"
            mode = 'zdiv'
        else:
            expr = gen('expr', CALC_GRAMMAR)
            mode = 'norm'

        code, out, err = run_calc(binary, expr)

        # 前幾次印進度
        if i < 10 or (i % 500 == 0 and i > 0):
            status = f'exit={code}' if code != 0 else (out[:20] if out else '(empty)')
            print(f'  [{i:5d}|{mode}] {expr[:45]:45s} => {status}')

        is_crash = (code != 0) or ('ERR:' in err) or (code == -999)
        if is_crash:
            ctype = classify(expr, code, err)
            if ctype not in seen_types:
                seen_types.add(ctype)
                crashes.append({
                    'id': len(crashes) + 1,
                    'iter': i,
                    'expr': expr,
                    'code': code,
                    'err': err,
                    'type': ctype,
                })
                print(f'\n  {"=" * 50}')
                print(f'  *** CRASH #{len(crashes)} found at iter {i} ***')
                print(f'  type  : {ctype}')
                print(f'  expr  : {expr[:70]}')
                print(f'  exit  : {code}')
                print(f'  stderr: {err[:100]}')
                print(f'  {"=" * 50}\n')

        if len(crashes) >= 3:
            print(f'[*] Found 3 unique bugs, stopping at iter {i}')
            break

    # 結果報告
    print('\n' + '=' * 60)
    print(f'總執行次數: {min(i + 1, total)}')
    print(f'找到 {len(crashes)} 個 unique crash:\n')
    for b in crashes:
        print(f'  Bug #{b["id"]} [{b["type"]}] @ iter {b["iter"]}')
        print(f'    expr  : {b["expr"][:65]}')
        print(f'    stderr: {b["err"][:80]}')
        print()

    if len(crashes) == 0:
        print('未找到 crash；確認 mini_calc binary 已 build，且 deep nesting 生成正確')
    elif len(crashes) == 1:
        print('找到 1 個 crash；stack overflow 應該在 10% bias 下很快被命中')


if __name__ == '__main__':
    main()
```

執行方式：

```bash
# 把 mini_calc.c 和 fuzzer_b.py 放在同一目錄
python3 fuzzer_b.py
```

</details>

---

## 測試用例表

| 輸入 | 期望行為 | exit code | 觸發哪個 bug |
|------|---------|-----------|------------|
| `1+2*3` | 輸出 7 | 0 | — |
| `10/2` | 輸出 5 | 0 | — |
| `(1+2)*3` | 輸出 9 | 0 | — |
| `1/0` | 輸出 inf（不 crash） | 0 | — |
| `0/0` | 輸出 nan（不 crash） | 0 | — |
| `-(-3)` | 輸出 3 | 0 | — |
| `3.14*2` | 輸出 6.28 | 0 | — |
| `((1+2)*(3-4))` | 輸出 -3 | 0 | — |
| `(` ×99 + `1` + `)` ×99 | 輸出 1（剛好在限制內） | 0 | — |
| `(` ×105 + `1` + `)` ×105 | crash，`ERR: stack overflow` | 1 | Bug #2 |
| `(` ×110 + `1` + `)` ×110 | crash，`ERR: stack overflow` | 1 | Bug #2 |
| `42/0`（libFuzzer+UBSan） | UBSan 報告 | nonzero | Bug #1 |
| `2147483647+1` | 輸出 2.14748e+09（double 不 overflow） | 0 | Bug #3 只在 int 路徑 |

---

## 延伸挑戰

**1. 方案 B：接 libFuzzer + UBSan**

在 WSL/Linux 下安裝 clang，用 `-fsanitize=fuzzer,undefined,address` 重新編譯。UBSan 能抓到 double 轉 int 的 overflow 行為，以及若你修改 parser 讓除法走整數路徑後的 SIGFPE。目標：讓 libFuzzer 在 30 秒內找到 UBSan 回報的 undefined behavior。

**2. Coverage-guided grammar fuzzing**

在方案 A 的基礎上，記錄每條 production rule 被展開的次數：

```python
rule_hits = {}  # key: (sym, alt_index), value: int

def gen_tracked(sym, grammar, depth=0, max_depth=6):
    if sym not in grammar or depth >= max_depth:
        return FALLBACK.get(sym, '1')
    alts = grammar[sym]
    # 優先選 hit count 最低的規則
    idx = min(range(len(alts)), key=lambda i: rule_hits.get((sym, i), 0))
    rule_hits[(sym, idx)] = rule_hits.get((sym, idx), 0) + 1
    alt = alts[idx]
    if isinstance(alt, str):
        return alt
    return ''.join(gen_tracked(tok, grammar, depth + 1, max_depth) for tok in alt)
```

觀察找到第一個 crash 的速度是否比純隨機快。

**3. Differential testing**

把 mini-calc 的輸出和 Python `eval()` 的結果比對：

```python
import math

def safe_eval(expr):
    """用 Python eval 計算同樣的表達式（注意安全過濾）。"""
    # 只允許數字和運算子
    import re
    if not re.fullmatch(r'[\d\s\+\-\*\/\(\)\.]+', expr):
        return None
    try:
        return float(eval(expr))
    except Exception:
        return None

def diff_test(expr):
    code, out, _ = run_calc('./mini_calc', expr)
    if code != 0 or not out:
        return
    py_val = safe_eval(expr)
    if py_val is None:
        return
    try:
        calc_val = float(out)
    except ValueError:
        return
    if not (math.isnan(calc_val) and math.isnan(py_val)):
        if not math.isclose(calc_val, py_val, rel_tol=1e-6):
            print(f"DIFF: {expr}")
            print(f"  mini_calc: {calc_val}")
            print(f"  python:    {py_val}")
```

**4. Mutation 模式**

在生成的有效表達式上做 byte-level mutation，觀察 parser 的錯誤處理：

- 隨機插入 `\x00`（null byte），看 parser 在哪裡截斷
- 把數字替換成超長整數 `99999999999999999`
- 插入 latin-1 字元（>127）
- 把 `)` 改成 `]` 或 `}`
- 刪除隨機字元

目標是找到 parser 因為輸入不預期而不 crash 但行為錯誤的情況（silent wrong answer）。

---

## 自我檢核

- [ ] 文法能生成語法合法的 mini-calc 表達式（手動驗收 20 個，確認每個都能被 `./mini_calc` 接受，exit 0）
- [ ] Fuzzer 在 5000 次內找到 stack overflow crash（exit code 1，stderr 有 `ERR: stack overflow`）
- [ ] 能解釋 `1/0` 為什麼在這個 parser 不 crash（IEEE 754 float division by zero 回傳 `+Inf`，不產生 signal）
- [ ] 知道 `MAX_DEPTH=100` 對應的觸發深度是括號超過 100 層（`depth > MAX_DEPTH`，101 層就觸發）
- [ ] 能說明 `gen()` 的 `max_depth` 參數如何影響生成表達式的長度與複雜度
- [ ] 能解釋 10% bias 的意義：在靠純隨機難以命中特定路徑時，用先驗知識加速 fuzzer，這是 grammar fuzzer 實務上的標準手法
- [ ] （方案 B）能說明 libFuzzer 的 coverage-guided feedback 如何讓它比純 generational fuzzer 更有效率找到 Bug #1 的 UBSan 觸發
