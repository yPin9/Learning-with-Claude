# Ch 17 — CTF 應用：用 angr 解 crackme 的正確姿勢

> 目標：過幾個遞進難度的 CTF 風格 target，看 angr 如何在實戰中破 crackme。最後一個會故意示範 **angr 走不動**，教你怎麼救。

## Level 1：簡單 if chain

最入門的 crackme：

```c
// level1.c
#include <stdio.h>
#include <string.h>
int main() {
    char s[16];
    fgets(s, 16, stdin);
    s[strcspn(s, "\n")] = 0;
    
    if (s[0] == 'a' && s[1] == 'n' && s[2] == 'g' &&
        s[3] == 'r' && s[4] == '!' && s[5] == 0) {
        puts("ok");
        return 0;
    }
    puts("nope");
    return 1;
}
```

```bash
gcc -O0 -no-pie level1.c -o level1
```

solver script：

```python
import angr

proj = angr.Project('./level1', auto_load_libs=False)
state = proj.factory.entry_state()
simgr = proj.factory.simulation_manager(state)

simgr.explore(
    find=lambda s: b'ok' in s.posix.dumps(1),
    avoid=lambda s: b'nope' in s.posix.dumps(1)
)

if simgr.found:
    print(repr(simgr.found[0].posix.dumps(0)))
# b'angr!\n...'
```

角度：這是 angr 最甜蜜的區 — 少 branch、無 loop、無 external complexity。幾秒出結果。

## Level 2：charset + checksum

再加條件：

```c
// level2.c
#include <stdio.h>
#include <string.h>

int main() {
    char s[9];
    fgets(s, 9, stdin);
    s[strcspn(s, "\n")] = 0;
    
    if (strlen(s) != 8) { puts("length"); return 1; }
    
    int sum = 0;
    for (int i = 0; i < 8; i++) {
        if (s[i] < 'a' || s[i] > 'z') { puts("charset"); return 2; }
        sum += s[i];
    }
    if (sum != 800) { puts("sum"); return 3; }
    
    if (s[0] != s[7]) { puts("mirror"); return 4; }
    
    puts("ok");
    return 0;
}
```

symbolic input + 明確 constraint：

```python
import angr
import claripy

proj = angr.Project('./level2', auto_load_libs=False)

flag_len = 8
flag = claripy.BVS('flag', 8 * flag_len)

state = proj.factory.entry_state(stdin=flag)

# charset constraint 預先加上
for byte in flag.chop(8):
    state.solver.add(byte >= ord('a'))
    state.solver.add(byte <= ord('z'))

simgr = proj.factory.simulation_manager(state)
simgr.explore(
    find=lambda s: b'ok' in s.posix.dumps(1),
    avoid=lambda s: any(err in s.posix.dumps(1)
                         for err in (b'length', b'charset', b'sum', b'mirror'))
)

if simgr.found:
    sol = simgr.found[0]
    print(sol.solver.eval(flag, cast_to=bytes))
# 比如 b'somxyzrs'  (sum=800，s[0]==s[7])
```

重點：**input 的 constraint 先加好**。不加的話 angr 要自己枚舉 8 個 byte 的 256^8 空間、每個 branch 都要 fork — path 爆炸。

## Level 3：function call chain

crackme 開始變複雜：

```c
// level3.c
int transform(char c, int i) {
    return (c ^ (i * 7)) + i;
}

int check(const char* s) {
    int target[] = { 0x67, 0x59, 0x5d, 0x5f, 0x61 };
    for (int i = 0; i < 5; i++) {
        if (transform(s[i], i) != target[i]) return 0;
    }
    return 1;
}

int main() {
    char s[6];
    fgets(s, 6, stdin);
    if (check(s)) puts("ok"); else puts("nope");
    return 0;
}
```

angr 對 function call 的 symex 天生順 — 你什麼都不用做：

```python
import angr, claripy

proj = angr.Project('./level3', auto_load_libs=False)
flag = claripy.BVS('flag', 8 * 5)
state = proj.factory.entry_state(stdin=flag)
simgr = proj.factory.simulation_manager(state)
simgr.explore(
    find=lambda s: b'ok' in s.posix.dumps(1),
    avoid=lambda s: b'nope' in s.posix.dumps(1)
)
if simgr.found:
    print(simgr.found[0].solver.eval(flag, cast_to=bytes))
```

秒解。transform 的 xor + add 是 SMT 小菜一碟。

## Level 4：hash-like 函式（angr 會掙扎）

故意做一個讓 symex 慢的：

```c
// level4.c
// sample: simple hash function
uint32_t weak_hash(const char* s) {
    uint32_t h = 0x811c9dc5;       // FNV offset basis
    for (int i = 0; s[i]; i++) {
        h = (h ^ s[i]) * 0x01000193;  // FNV prime multiplier
    }
    return h;
}

int main() {
    char s[16];
    fgets(s, 16, stdin);
    s[strcspn(s, "\n")] = 0;
    if (weak_hash(s) == 0xdeadbeef) puts("ok");
    else puts("nope");
    return 0;
}
```

天真 angr 跑：

```python
import angr, claripy
proj = angr.Project('./level4', auto_load_libs=False)
flag = claripy.BVS('flag', 8 * 16)
state = proj.factory.entry_state(stdin=flag)
simgr = proj.factory.simulation_manager(state)
simgr.explore(find=lambda s: b'ok' in s.posix.dumps(1))
```

結果：**卡 10+ 分鐘都不動**，或 SMT timeout。

原因：hash 的 multiplication + xor + loop iteration 讓 formula 指數成長。SMT 對 16-byte × 16-iteration 的乘法方程解起來極慢。

### 救法 1：固定 input 長度

你知道 flag 是 16 byte 就告訴 angr：

```python
flag = claripy.BVS('flag', 8 * 16)
state.add_constraints(claripy.And(*[b != 0 for b in flag.chop(8)[:-1]]))
# 前 15 byte 非 0，最後 byte 是 null
```

### 救法 2：charset constraint

```python
for byte in flag.chop(8):
    state.add_constraints(byte >= 0x20, byte < 0x7f)
```

降 input space 到 printable ASCII。

### 救法 3：用 concolic + 從已知長度起手

如果你 reverse 出大概的 flag 長度（例如 8），把 input 固定成 8-byte：

```python
flag = claripy.BVS('flag', 8 * 8)
state = proj.factory.entry_state(stdin=flag)
```

### 救法 4：放棄 angr，手寫 Z3 model

更激進 — 你已經 reverse 出 hash function，乾脆**只在 Z3 裡建 model**：

```python
import z3

s = [z3.BitVec(f's_{i}', 32) for i in range(8)]
h = z3.BitVecVal(0x811c9dc5, 32)
for c in s:
    h = (h ^ (c & 0xff)) * 0x01000193

solver = z3.Solver()
solver.add(h == 0xdeadbeef)
for ch in s:
    solver.add(ch >= 0x20, ch < 0x7f)

if solver.check() == z3.sat:
    m = solver.model()
    print(bytes([m[c].as_long() & 0xff for c in s]))
```

**bypass angr 整個 symex 層**，直接用 Z3。對 "已知 algorithm" 的 crackme 特別有效。

### 什麼時候 angr，什麼時候 Z3-only

Rule of thumb：

- **CFG 複雜、你不想 reverse**：讓 angr 自動展開 → angr
- **algorithm 你已經搞懂**：抄進 Z3 → 手寫 Z3
- **有 external call / 需要 POSIX model**：angr
- **Pure computation、input 長度知**：Z3

混用也可以 — 先用 angr 探出 CFG、確認 algorithm 等價，再用 Z3 跑 fast solve。

## Level 5：用 hook 跳過複雜函式

```c
// level5.c
int complex_libc_call(const char* s) {
    // 某個龐大的 libc call chain，讓 angr 走進去很慢
    // 為了模擬：這裡就用 sscanf 意思一下
    int x;
    if (sscanf(s, "%d", &x) != 1) return -1;
    return x;
}

int main() {
    char s[32];
    fgets(s, 32, stdin);
    int n = complex_libc_call(s);
    if (n == 42) puts("ok");
    else puts("nope");
    return 0;
}
```

sscanf 這種 libc function angr 有 SimProcedure 但效果不一定好。手動 hook：

```python
import angr, claripy

proj = angr.Project('./level5', auto_load_libs=False)

class parse_int(angr.SimProcedure):
    def run(self, s_ptr):
        # 假設輸入是個 symbolic int（8 byte ascii digit）
        result = claripy.BVS('parsed_int', 32)
        self.state.solver.add(result >= 0, result <= 999999)
        return result

proj.hook_symbol('complex_libc_call', parse_int())

flag = claripy.BVS('flag', 8 * 8)
state = proj.factory.entry_state(stdin=flag)
simgr = proj.factory.simulation_manager(state)
simgr.explore(
    find=lambda s: b'ok' in s.posix.dumps(1),
    avoid=lambda s: b'nope' in s.posix.dumps(1)
)
```

hook 之後 angr 不 step into complex_libc_call、直接用 `parse_int` 的 Python 邏輯。速度天壤之別。

但代價：**精度妥協** — 你的 hook 邏輯可能跟真實 behavior 不等價。bug-finding 場景要小心；CTF 場景通常夠用。

## 共通 tip：stdin vs stdin_file

input 餵給 binary 有兩種方式：

```python
# 方式 A：直接把 symbolic 當 stdin
flag = claripy.BVS('flag', 8 * 16)
state = proj.factory.entry_state(stdin=flag)

# 方式 B：stdin 當 symbolic file
flag = claripy.BVS('flag', 8 * 16)
stdin_file = angr.SimFile('stdin', content=flag, has_end=True)
state = proj.factory.entry_state(
    args=['./target'],
    stdin=stdin_file
)
```

有 `has_end=True` 的差別：angr 會在 symbolic input 結束後模擬 EOF（read 回 0）。某些 target 需要這個才正確停。

## 共通 tip：command line args

```c
int main(int argc, char** argv) {
    if (!strcmp(argv[1], "magic")) puts("ok");
    ...
}
```

```python
sym_arg = claripy.BVS('arg', 8 * 10)
state = proj.factory.entry_state(
    args=['./target', sym_arg],
    add_options=angr.options.unicorn
)
```

argv 放進 state 會走 `_start` 把它壓 stack，symex 在 `main` 看到的 `argv[1]` 就是 `sym_arg`。

## 共通 tip：看 flag 直接 eval

通常你拿到 found state 就這樣：

```python
sol = simgr.found[0]
print("stdin:", sol.posix.dumps(0))

# 或 eval 特定 symbolic var
print("flag:", sol.solver.eval(flag, cast_to=bytes))

# 多個解
print("alt flags:", sol.solver.eval_upto(flag, 5, cast_to=bytes))
```

看 constraint 也有用：

```python
for c in sol.solver.constraints:
    print(c)
```

PC 太長看不出名堂？取 variable 的 max / min：

```python
print("flag[0] in range:",
      sol.solver.min(flag.get_bytes(0, 1)),
      sol.solver.max(flag.get_bytes(0, 1)))
```

## 心法：angr 不是通用解題器

很多人以為寫 CTF RE 題「拿 angr 跑就行」。真相：

- **Level 1, 2**：幾乎任意寫都能解
- **Level 3**：懂怎麼 setup input 就能解
- **Level 4**：要 domain knowledge 或 Z3-only
- **Level 5**：要 hook + 手工介入

**angr 幫你不用 reverse 那些 boilerplate**，但**不幫你不用 reverse 核心算法**。遇到 hash / crypto / custom VM，先 reverse 清楚、再決定用 angr 還是 Z3。

## 一些 CTF 裡的 angr 陷阱

- **binary 是 stripped**：`proj.factory.entry_state()` 還是能用，但 `proj.symbol('...')` 拿不到 function。要從 CFG 自己找 target 的 address
- **binary 用 ptrace 反 debug**：angr 本來就不跑真的 process，ptrace check 看不到
- **binary 檢查 argc**：忘了給 argv，state 裡 argc 是 0，target 跑到 `argv[1]` 炸。用 `args=['./target', 'x']`
- **binary mmap 讀自己**：少見，但一些 CTF 會這樣。angr 的 mmap SimProcedure 會正確 model
- **binary 用 /dev/urandom**：angr 有 SimProcedure 回 symbolic，但 solver 可能推出奇怪結果。固定 random seed（`open_fds` 手動 setup）

## 自我檢核

- [ ] 跑完 level1-3 的 solver script
- [ ] 試 level4、確認 naive solve 卡住、用 Z3-only 解
- [ ] 用 hook 重寫 level5，跳過 libc call
- [ ] 知道 `stdin=`、`args=`、`stdin_file` 三種 input 傳法差別
- [ ] 能用 `solver.eval_upto` 拿多個解

下一章收尾 Part 4 — 明確講 angr 的極限、哪些題目該換別的工具。

→ [Ch 18 — angr 的極限：什麼時候該關掉 angr](./18-angr-limits.md)
