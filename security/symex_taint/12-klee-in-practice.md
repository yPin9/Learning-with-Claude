# Ch 12 — klee_make_symbolic、POSIX runtime、uclibc

> 目標：實際跑幾個 KLEE 任務，從 smoke test 到一個 mini parser。看清楚每個 flag 做什麼、test case 怎麼 replay、coverage 怎麼看。

## Workflow 一次看完

```
C source
   │  clang -emit-llvm
   ▼
.bc (bitcode)
   │  klee <flags>
   ▼
klee-out-N/
 ├── test000001.ktest      (input 1)
 ├── test000002.ktest
 ├── ...
 ├── info                   (總結)
 ├── messages.txt           (log)
 ├── run.stats              (統計)
 └── assembly.ll            (linked bitcode)
```

工具：

- `ktest-tool`：讀 .ktest，印出是哪個 input
- `klee-stats`：show run statistics (coverage、query count、memory)
- `klee-replay`：把 test 餵回原生 binary 重跑（找 regression 用）

## 例子 1：輸入驗證器

```c
// ch12-validator.c
#include <stdio.h>
#include <string.h>
#include <klee/klee.h>

int validate(const char* s) {
    if (strlen(s) != 8) return -1;
    if (s[0] != 'K') return -1;
    if (s[1] != 'L') return -1;
    if (s[2] != 'E') return -1;
    if (s[3] != 'E') return -1;
    int checksum = 0;
    for (int i = 4; i < 8; i++) {
        checksum += s[i];
    }
    if (checksum != 400) return -1;
    return 0;
}

int main() {
    char s[9];
    klee_make_symbolic(s, sizeof(s), "s");
    s[8] = 0;  // null terminator
    return validate(s);
}
```

build & run：

```bash
docker run --rm -v $(pwd):/work -w /work klee/klee:3.0 /bin/bash -c "
    clang -I /usr/local/include -emit-llvm -c -g -O0 \
        -Xclang -disable-O0-optnone \
        ch12-validator.c -o validator.bc && \
    klee --libc=klee --optimize validator.bc
"
```

跑完，KLEE 輸出類似：

```
KLEE: output directory is "/work/klee-out-1"
KLEE: Using STP solver backend
KLEE: WARNING ONCE: calling external: ...
KLEE: done: total instructions = 13421
KLEE: done: completed paths = 11
KLEE: done: partially completed paths = 0
KLEE: done: generated tests = 11
```

看看產生的 tests：

```bash
ls klee-out-1/*.ktest
# test000001.ktest ... test000011.ktest

docker run --rm -v $(pwd):/work -w /work klee/klee:3.0 \
    ktest-tool klee-out-1/test000011.ktest
# 應該會看到一個像 "KLEE\x64\x64\x64\x64\x00" 的 input — checksum = 4*100 = 400
```

KLEE 窮盡了 path：

- 10 條 path 是各種前綴檢查失敗（length != 8、s[0] != K、s[1] != L、...、checksum != 400）
- 1 條 path 滿足所有條件、進 return 0

這就是 symex 相對 fuzzing 的最強之處 — **不用隨機猜，11 個 test 就覆蓋所有 path**。

## 例子 2：OOB 抓取

```c
// ch12-oob.c
#include <klee/klee.h>

int arr[10] = {0};

int main() {
    int idx;
    klee_make_symbolic(&idx, sizeof(idx), "idx");
    return arr[idx];     // 這會 OOB 嗎？
}
```

跑：

```
KLEE: ERROR: ch12-oob.c:8: memory error: out of bound pointer
KLEE: NOTE: now ignoring this error at this location
```

而且產出的 test case 告訴你觸發的 idx 值：

```bash
ktest-tool klee-out-2/test000001.ktest
# idx = 10   (或任何 >= 10 或 < 0 的值)
```

**不用寫 spec，OOB 自動檢測**。這是 KLEE 最賣的功能，沒有之一。

## 例子 3：symbolic file input

現在用 POSIX runtime 來 model 一個 file 讀取 target：

```c
// ch12-parser.c
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char** argv) {
    FILE* f = fopen(argv[1], "r");
    if (!f) return 1;
    
    char buf[16];
    size_t n = fread(buf, 1, 16, f);
    fclose(f);
    
    if (n < 4) return 2;
    if (buf[0] != 'F' || buf[1] != 'O' || buf[2] != 'O') return 3;
    if (buf[3] == '!') {
        // 找到 "FOO!" 開頭
        return 0;
    }
    return 4;
}
```

跑：

```bash
clang -I /usr/local/include -emit-llvm -c -g -O0 \
    -Xclang -disable-O0-optnone \
    ch12-parser.c -o parser.bc

klee --posix-runtime --libc=uclibc --optimize \
     --sym-files 1 16 \
     parser.bc A
```

`--sym-files 1 16` 創一個 symbolic file（叫 `A`），大小 16 byte 都 symbolic。target 的 `argv[1] = "A"` 透過 POSIX runtime 讀到這個 symbolic file。

預期：KLEE 產生多個 test，其中一個是 buf 開頭 `"FOO!"`。

驗證：

```bash
ls klee-out-3/*.ktest | head -5
ktest-tool klee-out-3/test*.ktest | grep -B 2 "FOO"
```

## klee_make_symbolic 細節

`void klee_make_symbolic(void* addr, size_t nbytes, const char* name)` 做三件事：

1. 為 `[addr, addr+nbytes)` 這塊 memory 創 `nbytes` 個獨立 symbolic byte
2. 覆蓋原本的 memory 內容
3. 給它們一個 name prefix（在 test case 輸出時用）

常見錯誤：

```c
char* p = malloc(100);
klee_make_symbolic(p, 100, "data");   // ✓ 正確

int x;
klee_make_symbolic(x, sizeof(x), "x");   // ✗ 錯！要 &x
```

第二個把 `x` 的值（可能是任意 stack garbage）當 address 傳 — KLEE 會報 memory error。

## klee_assume 用法

```c
int a, b;
klee_make_symbolic(&a, sizeof(a), "a");
klee_make_symbolic(&b, sizeof(b), "b");

klee_assume(a > 0);      // 只探索 a > 0 的 path
klee_assume(b < 100);
klee_assume(a < b);

// 繼續
if (a + b > 50) { ... }
```

`klee_assume(cond)` 等價「把 cond 加進 PC」。好處：限縮 input space、讓 path 數可控。

反模式：`assume` 太鬆 → path 仍然爆炸；`assume` 太緊 → 漏掉實際會觸發的 input。

實務建議：先不開 assume 跑一次、看 path 數與速度。爆了再用 assume 砍。

## 看 coverage

KLEE 本身會在 `klee-out-N/info` 裡印 path 數、instruction 數。更詳細的：

```bash
klee-stats klee-out-1
# ┌──────────────────┬────────┬─────────────┬─────────────┬──────────┬──────────┬──────────────┬──────────────┬─────────────┐
# │        Path      │  Instrs│    Time(s)  │  ICov(%)    │   BCov(%)│   ICount │    TSolver(%)│  CexCacheMiss│   QueryCount│
# ├──────────────────┼────────┼─────────────┼─────────────┼──────────┼──────────┼──────────────┼──────────────┼─────────────┤
# │ klee-out-1       │  13421 │        0.23 │       98.5  │    92.1  │    1234  │         15.3 │          456 │        1234 │
# └──────────────────┴────────┴─────────────┴─────────────┴──────────┴──────────┴──────────────┴──────────────┴─────────────┘
```

- **ICov**：instruction coverage
- **BCov**：branch coverage
- **TSolver**：solver 花的百分比 — 大於 50% 說明你的 target 對 SMT 很重

精細 branch coverage 需要 gcov 接起來：

```bash
clang -emit-llvm -c -g --coverage ch12-validator.c -o validator.bc
klee --output-dir=klee-out-cov --write-cov validator.bc
# klee-out-cov 會有 .gcda
```

## 把 test 餵回原生 binary

KLEE 找到 crash 後，你想在原生 binary 上 reproduce：

```bash
# 用 klee-replay 或手動
KTEST_FILE=klee-out-1/test000001.ktest \
    klee-replay ./validator-native klee-out-1/test000001.ktest
```

或更手動：

```bash
ktest-tool klee-out-1/test000001.ktest
# 讀出 s 的 concrete bytes，echo 或寫 file 餵給原生 binary
```

## 常見 debug：KLEE 不走或走偏

**症狀 1：path 數爆炸（上萬）**

檢查：
- `strlen` / `strcmp` 有對 unbounded symbolic string 跑？每個 byte 一個 branch
- 有 loop 沒 bound？

對策：
- `--max-forks=N`、`--max-depth=N`
- 對 string input 設定 max size、在 input 後面手動放 null byte

**症狀 2：太快完成，只有幾個 path**

檢查：
- 你 symbolic 的變數是不是真的被 branch 依賴？可能被 constant-fold 掉
- POSIX runtime 有沒有 link 進來？（`--posix-runtime`）

**症狀 3：`calling external: X` warning**

KLEE 遇到沒 model 過的 external function。它會 fallback 到 concrete（用當前 concrete value 呼叫真 function）。這可能讓 symex 走偏。

對策：
- 寫一個 stub（C 寫一個假的 X，用 `--no-internalize`）
- 或 `--allow-external-sym-calls` 但注意結果可能不精確

**症狀 4：solver timeout**

`--max-solver-time=30` — 每個 SMT query 最多 30 秒。太長的 query 通常代表 formula 過深（loop 太多或 symbolic memory 爆）。

## Optimize 的黑魔法

```bash
klee --optimize ...
```

等於對 bitcode 先跑 LLVM 的 `opt -O2`。效果：

- Dead code removed
- Constant propagation
- CFG 簡化
- 某些 loop unroll

對 KLEE path 數經常有 **10× 影響**。幾乎永遠要開。

但 `-O2` 會 inline + scalarize，讓 debugging 時 line number 對不上。你想看 KLEE 報錯對應哪一行 source 時，可能要關掉 optimize、改開 `-O0 -Xclang -disable-O0-optnone`。

## 特殊 tip：--watchdog 與 external process

KLEE 用 STP / Z3 時，有時 solver 卡住 — 例如某個 formula 讓 Z3 stuck。解法：

```bash
klee --use-forked-solver --watchdog ...
```

- `--use-forked-solver`：solver 跑在 child process，卡住可 kill
- `--watchdog`：main process 監控 solver，逾時 kill

這兩個在生產跑 KLEE 幾乎必開。

## 實務建議的 baseline flags

我的日常跑 KLEE 用這組：

```bash
klee \
    --optimize \
    --posix-runtime \
    --libc=uclibc \
    --search=random-path --search=nurs:covnew \
    --use-forked-solver \
    --watchdog \
    --max-time=3600 \
    --max-memory=8000 \
    --only-output-states-covering-new \
    target.bc
```

然後根據 target 微調。有 source 就開 `--libc=uclibc`；沒用到 syscall 就 `--libc=klee` 更快；bug 搜尋就加 `--exit-on-error`。

## 你會注意到的 KLEE 性格

用一兩天你會發現：

- 跑 coreutils 類 target 時**非常快**
- 跑 parsing target（JSON、HTML parser）常常在 strlen / strcmp 上卡
- 遇到 threading / network 直接投降
- 產出的 test case 大部分是 corner case，對手寫 unit test 很有啟發
- 報 memory error 時 traceback 不錯，但不如 Valgrind 直觀

KLEE 不神，但它是**最穩定、最受學術檢驗**的 C symex。知道它的脾氣後，用它找 bug 很愉快。

## 自我檢核

- [ ] 能跑通 `validator.c`，解出一個通過所有檢查的 input
- [ ] 能看懂 `klee-stats` 的每一欄
- [ ] 跑 symbolic file 的例子（`--sym-files`）並得到預期 test case
- [ ] 會用 `klee_assume` 限縮 input space
- [ ] 知道 `--optimize`、`--posix-runtime`、`--libc=uclibc` 什麼時候要開

下一章拆 KLEE 真實世界的 **限制與 CVE 案例** — 它在哪些題目上拿下勝利、在哪些題目上投降。

→ [Ch 13 — KLEE 的實戰邊界與 CVE 案例](./13-klee-limits-and-cves.md)
