# 練習 A — 靜態逆一個 strip crackme

> **目標**：把 Part 1（Ch 4–11）學的全部串起來，做一次完整的靜態逆向——**不准跑目標程式**，純靠 `objdump` + 反編譯器，逆出一個 strip 過的 crackme 的正確密碼。這是 ground-truth 練習：解答區附出題 source 當標準答案，逆完對照驗證你逆對了，最後跑一次確認密碼正確。

> **環境**：WSL2 / Linux x86-64，gcc（出題用）+ objdump + radare2（或 Ghidra/IDA）。**規則：分析階段不准執行 target**，逼你純靜態重建邏輯——這正是逆向真實 malware/保護時的常態（不能隨便跑）。

## 情境

你拿到一個 stripped ELF `crackme`。它讀一個密碼（命令列引數或 stdin），檢查對了印 `Access granted.`、錯了印 `Access denied.`。你的任務：**純靜態**逆出那個能讓它印 `Access granted.` 的密碼。

先自己產生題目 binary（出題 source 在最後解答區，**現在別看**）。這裡給你編譯好的等價指令——但把 source 檔當作看不到：

```bash
# 助教已幫你編好 crackme（你只拿到這個 stripped binary）
$ file crackme
crackme: ELF 64-bit LSB pie executable, x86-64, ... dynamically linked, ... stripped
```

## 任務規格

1. **只用靜態工具**：`objdump -d`、`readelf`、`nm -D`、`strings`、`r2`（`pdc`/`pdf`）或 Ghidra/IDA。**分析階段不執行 `./crackme`**。
2. **找到密碼檢查函式**，讀懂它的驗證邏輯。
3. **逆推出正確密碼**（一個字串），寫在紙上。
4. **最後**（且僅在你寫下答案後）跑一次 `./crackme "你的答案"` 確認 `Access granted.`——這是驗證，不是分析手段。
5. 對照解答區的出題 source，檢查你逆對了檢查邏輯（不只是密碼碰巧對）。

**時限**：60 分鐘。逆到「看懂檢查邏輯」算及格，逆出密碼算滿分。

## 如果你卡住了（5 個方向提示，逐條掀）

<details>
<summary>提示 1：從哪裡下手？先做偵察</summary>

跑 `strings crackme`、`nm -D crackme`、`readelf -r crackme`。你會看到 `Access granted.`/`Access denied.` 兩個字串，和 libc import（`strlen`、`puts`、`strncpy`、`fgets`…）。**那兩個字串是路標**——找到「引用 `Access granted.` 的地方」，往回看是什麼條件跳到那裡，就是檢查通過的分支（Ch 11：字串是免費線索）。
</details>

<details>
<summary>提示 2：定位檢查函式</summary>

用 r2：`r2 -qc "aaa; izz~granted" crackme` 找到字串位址，`axt @ 字串位址` 看誰引用它。或直接 `aaa` 後看 `afl`（函式列表），`main` 會呼叫一個回傳布林的函式——那個就是 `check`。stripped 後它叫 `fcn.0000xxxx`（Ch 8：反編譯器連函式名都是編的）。
</details>

<details>
<summary>提示 3：讀檢查函式的骨架</summary>

`check` 開頭多半有 `call strlen@plt` + 一個 `cmp $常數,長度`——這是**長度檢查**（密碼必須是某個固定長度）。那個常數就是密碼長度。接著通常是一個**逐字元迴圈**：讀輸入第 i 個 byte、做某種變換、和一個目標比對，不符就 return 0。
</details>

<details>
<summary>提示 4：認出迴圈裡的變換（用 Ch 10 idiom）</summary>

迴圈體裡找這幾個 pattern：`movzbl (輸入+i),%eax`（讀第 i 個輸入 byte）、`xor`（和某個 key 互斥或）、`cmp` 和一個從 `.rodata` 讀出來的 byte（目標值）。留意有沒有一個 key **每輪變化**（如 `add $0x7,key`）——那是 rolling key。把「輸入[i] ⊕ key[i] == target[i]」這條式子還原出來。
</details>

<details>
<summary>提示 5：反推密碼</summary>

檢查是 `input[i] ^ key == target[i]`，所以 `input[i] = target[i] ^ key`。你需要兩份資料：(1) `target[]` 陣列——在 `.rodata`，用 `objdump -s -j .rodata` 或 r2 `px @ 位址` 讀出那串 bytes；(2) key 的初始值和每輪怎麼變（從 asm 讀 `mov $0x37,...` 和 `add $0x7,...`）。有了這兩個，用 Python 算 `chr(target[i] ^ key_i)` 拼出密碼。
</details>

## 分段步驟

### Step 1：偵察（5 分鐘）

```bash
$ file crackme                    # 確認架構、動態連結、stripped
$ strings crackme | grep -iE 'access|granted|denied'
$ nm -D crackme                   # 看 libc import（strlen/puts/strncpy/fgets…）
```

libc import 洩露行為：有 `strlen` = 會量長度、有 `strncpy`/`fgets` = 讀輸入。

### Step 2：定位檢查函式（10 分鐘）

```bash
$ r2 -e scr.color=0 -qc "aaa; afl~fcn" crackme    # 列出函式
$ r2 -e scr.color=0 -qc "aaa; pdc @ main" crackme  # 看 main 呼叫誰
```

`main` 會呼叫一個函式、拿它的布林回傳決定印 granted/denied——那就是 `check`（stripped 名 `fcn.00001xxx`）。

### Step 3：讀檢查邏輯（25 分鐘）

`objdump -d` 或 `r2 pdf @ 那個函式`，逐段讀：長度檢查 → 逐字元迴圈 → 變換 → 比對。用 Ch 9/10/11 的技巧認 idiom 和資料存取。

### Step 4：抽出 target[] 與 key（10 分鐘）

從 asm 找到 `lea 常數(%rip),%rcx` 指向的 `.rodata` 位址，讀出 target bytes；讀出 key 初值與更新規則。

### Step 5：反推密碼並驗證（10 分鐘）

用 Python 算出密碼，寫下來，**才**跑 `./crackme "答案"` 驗證。

## 完整參考解答

**先自己逆完再看！** 偷看就練不到東西。

<details>
<summary>點開完整逆向過程與答案</summary>

### 出題 source（ground truth——逆完拿它對答案）

```c
#include <stdio.h>
#include <string.h>

/* 逐字元 xor rolling key 後和 target 比對 */
static const unsigned char target[] = {
    0x53, 0x40, 0x5e, 0x5b, 0x0a, 0x69, 0x2f, 0x22, 0x3d
};

int check(const char *in){
    size_t n = strlen(in);
    if (n != sizeof(target)) return 0;       // 長度必須是 9
    unsigned char key = 0x37;                 // key 初值 0x37
    for (size_t i = 0; i < n; i++){
        if (((unsigned char)in[i] ^ key) != target[i]) return 0;  // in[i]^key == target[i]
        key = (unsigned char)(key + 7);       // rolling：每輪 +7
    }
    return 1;
}

int main(int argc, char **argv){
    char buf[64];
    if (argc >= 2){ strncpy(buf, argv[1], sizeof(buf)-1); buf[63]=0; }
    else { if(!fgets(buf,sizeof(buf),stdin)) return 1; buf[strcspn(buf,"\n")]=0; }
    if (check(buf)) puts("Access granted.");
    else puts("Access denied.");
    return 0;
}
```

### 逆向實況

**偵察**確認 stripped、動態連結，import 有 `strlen`/`strncpy`/`fgets`/`puts`。字串 `Access granted.`/`Access denied.` 在 `.rodata`。

**定位**：r2 `aaa` 後 `pdc @ main` 顯示 main 呼叫 `fcn.000011e9`，用它的回傳決定印哪個字串——`check` 就是 `fcn.000011e9`。

**讀檢查函式**（真跑 `objdump -d -M att --no-show-raw-insn crackme`）：

```asm
00000000000011e9 <check（stripped: fcn.000011e9）>:
    11e9:  endbr64
    11ed:  push   %rbp
    11ee:  mov    %rsp,%rbp
    11f1:  sub    $0x30,%rsp
    11f5:  mov    %rdi,-0x28(%rbp)      ; 參數 in
    11f9:  mov    -0x28(%rbp),%rax
    11fd:  mov    %rax,%rdi
    1200:  call   10c0 <strlen@plt>     ; n = strlen(in)
    1205:  mov    %rax,-0x8(%rbp)
    1209:  cmpq   $0x9,-0x8(%rbp)       ; ← 長度檢查：n == 9 ?
    120e:  je     1217
    1210:  mov    $0x0,%eax             ; 長度不符 → return 0
    1215:  jmp    126c
    1217:  movb   $0x37,-0x11(%rbp)     ; ← key = 0x37（初值）
    121b:  movq   $0x0,-0x10(%rbp)      ; i = 0
    1223:  jmp    125d
    ; ---- 迴圈體 ----
    1225:  mov    -0x28(%rbp),%rdx      ; ┐
    1229:  mov    -0x10(%rbp),%rax      ; ┤ 讀 in[i]
    122d:  add    %rdx,%rax             ; ┤
    1230:  movzbl (%rax),%eax           ; ┘ eax = in[i]（1-byte，movzbl）
    1233:  xor    -0x11(%rbp),%al       ; ← in[i] ^ key
    1236:  mov    %eax,%edx
    1238:  lea    0xdc9(%rip),%rcx      ; ← rcx = &target[]（.rodata @ 0x2008）
    123f:  mov    -0x10(%rbp),%rax      ; i
    1243:  add    %rcx,%rax             ; &target[i]
    1246:  movzbl (%rax),%eax           ; target[i]
    1249:  cmp    %al,%dl               ; ← (in[i]^key) == target[i] ?
    124b:  je     1254
    124d:  mov    $0x0,%eax             ; 不符 → return 0
    1252:  jmp    126c
    1254:  addb   $0x7,-0x11(%rbp)      ; ← key += 7（rolling key）
    1258:  addq   $0x1,-0x10(%rbp)      ; i++
    125d:  mov    -0x10(%rbp),%rax
    1261:  cmp    -0x8(%rbp),%rax       ; i < n ?
    1265:  jb     1225
    1267:  mov    $0x1,%eax             ; 全部通過 → return 1
    126c:  leave
    126d:  ret
```

還原的邏輯（純從 asm 讀出，未看 source）：

1. **長度必須是 9**（`cmpq $0x9`）。
2. **key 初值 0x37**（`movb $0x37`）。
3. 迴圈：`in[i] ^ key == target[i]`（`movzbl (in+i)` → `xor key` → `cmp target[i]`）。
4. **每輪 `key += 7`**（`addb $0x7`）——rolling key。

**抽 target[]**：`lea 0xdc9(%rip),%rcx` 在 0x1238，指向 `.rodata` 0x2008。讀出來（真跑 `r2 px 16 @ 0x2008`）：

```
0x00002008  5340 5e5b 0a69 2f22 3d0a ...
            └─ target[0..8] = 53 40 5e 5b 0a 69 2f 22 3d
```

（r2 甚至把它當字串秀成 `"S@^[\ni/"=\n"`——就是這 9 個 bytes。和出題 source 的 `target[]` **完全一致**，逆對了。）

**反推密碼**：`in[i] = target[i] ^ key_i`，`key_i = 0x37 + 7*i`：

```python
target = [0x53,0x40,0x5e,0x5b,0x0a,0x69,0x2f,0x22,0x3d]
key = 0x37
pw = ""
for t in target:
    pw += chr(t ^ key)
    key = (key + 7) & 0xff
print(pw)
```

**算出密碼**（真跑）：

```
password bytes: 0x64 0x7e 0x1b 0x17 0x59 0x33 0x4e 0x4a 0x52
password ascii: d~Y3NJR
```

（bytes：`64 7e 1b 17 59 33 4e 4a 52` = `d ~ \x1b \x17 Y 3 N J R`——含兩個不可印字元 0x1b、0x17，所以要用引號直接傳。）

### 驗證（寫下答案後才跑）

```bash
$ ./crackme "$(python3 -c 'target=[0x53,0x40,0x5e,0x5b,0x0a,0x69,0x2f,0x22,0x3d];k=0x37;o=""
for t in target:
 o+=chr(t^k);k=(k+7)&0xff
print(o)')"
Access granted.          ← 逆對了！

$ ./crackme "wrongpass"
Access denied.
```

`Access granted.` — 密碼正確，且我們是**先靜態逆出邏輯、算出密碼，再跑驗證**，符合規則。對照出題 source 的 `target[]`、`key=0x37`、`+7`、`^`、長度 9——每一項都和逆出的一致，**不是碰運氣猜對，是真的看懂了檢查邏輯**。

</details>

## 驗證方式

- 你逆出的檢查邏輯（長度 9、key 0x37、每輪 +7、xor 比 target）要和解答區出題 source 逐項對得上。
- `./crackme "你的密碼"` 印 `Access granted.`。
- 能解釋密碼裡為什麼有不可印字元（因為是 `target ^ rolling_key` 算出來的任意 byte，不保證落在可印範圍）。

## 延伸挑戰

1. **改成 `-O2` 再逆**（本課核心訓練）：把出題 source 用 `gcc -O2` 編、strip，再逆一次。你會發現檢查邏輯**大不同**——`check` 被優化成（真跑 `crackme_O2`）：

   ```asm
   00000000000012b0 <check>:
       12b8:  call   10c0 <strlen@plt>
       12c2:  cmp    $0x9,%r8               ; 長度 9（同前，但用 r8）
       12c8:  mov    $0x53,%esi             ; ← target[0]=0x53 直接 mov 進暫存器！
       12cd:  mov    $0x37,%edx             ; key=0x37
       12e0:  movzbl (%rdi,%rax,1),%esi     ; target[i]（用 index 定址，Ch 9 陣列 idiom）
       12e4:  movzbl (%rbx,%rax,1),%ecx     ; in[i]
       12e8:  xor    %edx,%ecx              ; in[i] ^ key
       12ea:  cmp    %sil,%cl               ; == target[i] ?
       12f3:  add    $0x7,%edx              ; key += 7
       12f6:  cmp    $0x9,%rax              ; i < 9
   ```

   注意 O2 的差異：迴圈用 `(%rbx,%rax,1)` **index 定址**（Ch 9 陣列 pattern）、暫存器命名不同、第一次迭代 target[0] 被 `mov $0x53` 直接餵進去（迴圈剝離 loop peeling）。**邏輯相同、形狀不同**——這就是為什麼 Ch 0 強調「兩種優化等級都要看」。用同一個 Python 反推，密碼一樣是 `d~Y3NJR`（真跑 `./crackme_O2_strip` 驗證：`Access granted.`）。

2. **不看 target[] 硬解**：如果 crackme 把 target 也藏起來（如執行時解密），純靜態就不夠了——這時要動態逆向（Part 2），在 `cmp` 那行下斷點、直接讀暫存器裡的期望值。這是 Ch 12–17 的預告。

3. **寫個 solver 腳本**：用 r2pipe 或 angr（Ch 26）自動抽出 target、key、算密碼——把這次手工過程腳本化，遇到同類 crackme 一鍵解。

## 自我檢核

- [ ] 我全程沒在分析階段跑 target，純靜態逆出邏輯
- [ ] 我從字串路標（`Access granted.`）反查定位到檢查函式
- [ ] 我認出長度檢查、rolling key、xor 變換、target 比對（用 Ch 9/10/11 技巧）
- [ ] 我從 `.rodata` 正確抽出 target[] bytes
- [ ] 我反推出密碼 `d~Y3NJR` 並在**寫下答案後**跑驗證得到 `Access granted.`
- [ ] 我對照出題 source 確認逆對的是邏輯、不是碰運氣
- [ ] （挑戰）我逆了 `-O2` 版，看到 index 定址/loop peeling 等差異但邏輯相同

做完這個練習，你已經能獨立完成一次完整的靜態逆向攻堅。Part 2 我們換武器——當靜態推不動時，讓 binary 自己招：用 gdb 動態觀察它到底在做什麼。

→ [Ch 12 動態逆向心法：觀察勝於推理](./12-dynamic-reversing-mindset.md)
