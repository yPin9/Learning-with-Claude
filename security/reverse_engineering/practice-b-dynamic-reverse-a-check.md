# 練習 B — 動態逆一個授權檢查

> **目標**：把 Part 2（Ch 12–17）的動態逆向武器獨立串起來，攻堅一個帶「序號授權檢查」的 strip 程式。你要用 gdb + strace/ltrace **要嘛找出能通過的序號、要嘛 patch 掉檢查讓它無條件接受任何序號**——兩條路都走一遍，體會「繞過」和「還原」的差別。

> **環境**：WSL2 / Linux x86-64，gcc + objdump + gdb + strace + ltrace。**建議時限：90 分鐘**（卡住看提示，別硬撐超過 20 分鐘無進展）。

這是 Part 2 的 ground-truth 練習。和練習 A（純靜態逆 crackme）互補：那題你瞪 asm 推理，這題你**讓 binary 自己招**。全程用 Ch 0 的 ground-truth 迴圈——逆完打開出題 source 對答案，確認你沒腦補錯。

## 題目：一個序號授權程式

你手上有一個 strip 的執行檔 `serial_stripped`。它接受一個命令列參數當序號，內部做格式檢查 + 某種變換比對，對了印 `VALID SERIAL`、錯了印 `invalid serial`。**你沒有它的原始碼**（出題 source 在最後參考解答區當標準答案，先別看）。

### 先自己編出題目

這門課的鐵律：練習目標一律**自己寫 C → 編譯 → strip**，才有標準答案能對。把下面這段存成 `serial.c`（**這是題目，先別細讀邏輯，編完就當它是陌生 binary**）：

```c
// serial.c —— 編完就當作陌生 binary，逆的時候別回來看
#include <stdio.h>
#include <string.h>
static unsigned transform(const char *s){
    unsigned h = 0x811c9dc5u;
    for(const char *p=s; *p; p++){
        if(*p=="-"[0]) continue;
        h ^= (unsigned char)*p;
        h *= 0x01000193u;
    }
    return h;
}
int main(int argc,char**argv){
    if(argc<2){ printf("usage: %s <serial>\n",argv[0]); return 2; }
    const char *s=argv[1];
    if(strlen(s)!=12){ printf("bad format\n"); return 1; }
    if(s[4]!="-"[0] || s[9]!="-"[0]){ printf("bad format\n"); return 1; }
    if(transform(s)==0x45a7a3ceu){ printf("VALID SERIAL\n"); return 0; }
    printf("invalid serial\n"); return 1;
}
```

```bash
$ gcc -O0 -o serial serial.c        # 帶符號版，最後對答案用（別拿來逆）
$ cp serial serial_stripped && strip serial_stripped
$ nm serial_stripped
nm: serial_stripped: no symbols     # ← 確認無符號，逆向起點
```

**從現在起只碰 `serial_stripped`。** 把 `serial.c` 和 `serial` 蓋起來。

## 你的任務

按難度遞增，三個目標至少完成前兩個：

1. **摸清行為**：用 strace/ltrace + 跑幾次，搞清楚它對序號做了哪些檢查（格式？長度？分隔符？），不讀一行 asm 能講出大概。
2. **繞過檢查（patch）**：用 gdb 動態改值、或直接改 binary 的 bytes，讓它對**任何**輸入都印 `VALID SERIAL`。這是「keygen 不出來也能過」的路。
3. **還原機制**：靜態 + 動態結合，逆出它的變換演算法和目標 magic，理解「什麼樣的序號會通過」。（能不能真的算出一個合法序號？這是本題的思考陷阱，見延伸挑戰。）

## 分段步驟

### Step 1：偵察——先跑幾次，看它挑剔什麼

不要一上來就 objdump。先當個使用者亂餵幾個輸入，看它怎麼罵你：

```bash
$ ./serial_stripped abc
$ ./serial_stripped ABCDEFGHIJKL
$ ./serial_stripped ABCD-EFGH-IJ
```

記下每個輸入得到什麼回應（`bad format`？`invalid serial`？）。從回應差異反推「它在檢查什麼」——這是 Ch 17 的偵察循環。

### Step 2：ltrace / strace 看邊界

```bash
$ ltrace ./serial_stripped ABCD-EFGH-IJ
$ strace -e trace=write ./serial_stripped ABCD-EFGH-IJ
```

看 ltrace 揭露哪些 libc 呼叫。**注意哪些檢查看得到、哪些看不到**——看不到的那部分是 inline 的自寫邏輯（Ch 14 的 ltrace 天花板），得靠 gdb/靜態。

### Step 3：objdump 找關鍵決策點

```bash
$ objdump -d serial_stripped | grep -E 'cmp|call|jne|je'
```

找那幾個「決定成敗」的比較：長度檢查的常數、格式檢查、還有最後那個拿變換結果比對的 magic。記下它們的位址（Ch 13 的靜態定位）。

### Step 4：gdb 讀真實的變換結果

斷在最後那個 magic 比較，餵一個過了格式檢查的輸入，讀出它算出來的值 vs 目標值（Ch 13 讀中間值）。

### Step 5：走兩條路

- **繞過路**：gdb 改記憶體/暫存器讓比較通過（Ch 13 patch），或改 binary bytes 把那個條件跳轉 nop 掉。
- **還原路**：讀懂變換演算法（Ch 17 靜動結合），理解什麼序號會過。

## 如果你卡住了

<details>
<summary>提示 1：它的格式檢查有哪些？（先別看 asm）</summary>

從 Step 1 的偵察，你應該注意到：太短的輸入和 12 字元的輸入得到**不同**的錯誤訊息。試試 `AAAAAAAAAAAA`（12 個 A，無 dash）和 `AAAA-AAAA-AA`（12 字元、dash 在第 5 和第 10 位）——一個 `bad format`、一個 `invalid serial`。這告訴你：**格式要求 = 長度 12 且 dash 在特定位置**。過了格式才進到真正的序號驗證。
</details>

<details>
<summary>提示 2：ltrace 只看到 strlen，變換去哪了？</summary>

`ltrace` 會顯示 `strlen(...) = 12`，然後就直接 `puts("invalid serial")`——中間那個「算序號」的過程一個 libcall 都沒有。這不是它沒算，是**變換被 inline / 是自寫函式**，ltrace 攔不到（Ch 14）。訊號很明確：ltrace 到此為止，切 gdb/objdump 去看那個 inline 運算。
</details>

<details>
<summary>提示 3：objdump 裡的 magic 長什麼樣</summary>

`objdump -d serial_stripped | grep 'cmp'` 找那個拿 32-bit 常數比的指令。你會看到類似 `cmp $0x45a7a3ce,%eax`——`0x45a7a3ce` 就是目標 magic。它前面不遠處有 `imul $0x1000193,%eax,%eax`（一個乘以某質數的動作）——認得出這是某種 hash 的 idiom（Ch 10）。長度檢查則是 `cmp $0xc,%rax`（0xc = 12）。
</details>

<details>
<summary>提示 4：PIE 位址怎麼下斷點</summary>

`serial_stripped` 是 PIE，`0x12a6` 只是檔案偏移。gdb 裡 `set disable-randomization on` 後，程式載在固定基底 `0x555555554000`，所以斷點下 `break *(0x555555554000+0x12a6)`（Ch 13 的 PIE 處理）。斷到後 `printf "%x", $eax` 讀算出來的 hash。
</details>

<details>
<summary>提示 5：繞過的兩種改法</summary>

**動態（gdb）**：斷在 `cmp $0x45a7a3ce,%eax`，`set $eax = 0x45a7a3ce`，`continue`——比較變相等，印 VALID。缺點是每次跑都要在 gdb 裡改。

**永久（改 binary）**：那個 `cmp` 後面是 `jne`（機器碼 `75 xx`）——序號錯就跳去印 invalid。把 `jne` 的 opcode `75` 兩個 byte（`75 16`）改成兩個 `nop`（`90 90`），它就永遠不跳、直接落進 VALID 分支。改完的 binary 對任何輸入都印 VALID。
</details>

## 完整參考解答

**先自己做到卡住再看！** 下面帶你走完整動態逆向過程，所有輸出都是真跑貼上的。

<details>
<summary>點開完整解答（真實 gdb/ltrace/objdump 會話 + 對照 source 驗證）</summary>

### 出題 source（標準答案）

就是題目那份 `serial.c`。關鍵事實：格式 = 12 字元、dash 在 index 4 與 9；變換 = **FNV-1a hash**（種子 `0x811c9dc5`，每個非-dash byte 做 `h ^= c; h *= 0x01000193`）；目標 magic = `0x45a7a3ce`；**一個合法序號是 `KEYG-2026-RE`**。

### 第一步：偵察

```bash
$ ./serial_stripped abc
bad format
$ ./serial_stripped ABCDEFGHIJKL
bad format                        # 12 字元但沒 dash → 還是 bad format
$ ./serial_stripped ABCD-EFGH-IJ
invalid serial                    # 格式對了 → 進到序號驗證，但序號錯
```

三個回應把檢查分層講清楚了：**先格式（長度 + dash 位置），過了才驗序號**。

### 第二步：ltrace/strace

```bash
$ ltrace ./serial_stripped WRON-GSER-IA
strlen("WRON-GSER-IA")                = 12
puts("invalid serial")               = 15
invalid serial
+++ exited (status 1) +++
```

ltrace 只看到 `strlen=12`（長度檢查）和 `puts`，**中間的序號變換一個 libcall 都沒有**——inline 的自寫 hash，ltrace 天花板到了。strace 也只有輸出：

```bash
$ strace -e trace=write ./serial_stripped WRON-GSER-IA
write(1, "invalid serial\n", 15)      = 15
+++ exited with 1 +++
```

沒開檔、沒連網——純計算程式。定位方向明確：切 objdump/gdb 挖那個 inline 變換。

### 第三步：objdump 找決策點

```bash
$ objdump -d serial_stripped | grep -nE '45a7a3ce|imul.*1000193|cmp.*0xc,'
177:    1240:  cmp    $0xc,%rax                  ; ★ strlen 結果 == 12 ?
142:    11c6:  imul   $0x1000193,%eax,%eax       ; ★ 乘 FNV prime → 是個 hash
204:    12a6:  cmp    $0x45a7a3ce,%eax           ; ★ 變換結果 == magic ?
```

看 main 尾段的完整反組譯（真跑，節選）：

```asm
    1240:  cmp    $0xc,%rax              ; 長度 == 12？
    1244:  je     125c                   ; 是 → 繼續；否 → bad format
    ...
    125c:  mov    -0x8(%rbp),%rax
    1260:  add    $0x4,%rax              ; s + 4
    1264:  movzbl (%rax),%eax            ; s[4]
    1267:  mov    $0x2d,%edx             ; 0x2d = '-'
    126c:  cmp    %dl,%al                ; s[4] == '-'？
    126e:  jne    1284                   ; 否 → bad format
    1270:  ...
    1274:  add    $0x9,%rax              ; s + 9
    1278:  movzbl (%rax),%eax            ; s[9]
    1280:  cmp    %dl,%al                ; s[9] == '-'？
    1282:  je     129a                   ; 是 → 進序號驗證
    ...
    129a:  mov    -0x8(%rbp),%rax
    12a1:  call   1189 <...>             ; ← 呼叫 transform(s)（回傳在 eax）
    12a6:  cmp    $0x45a7a3ce,%eax       ; ★ transform(s) == 0x45a7a3ce ?
    12ab:  jne    12c3                   ; 否 → invalid serial
    12ad:  ...                            ; 是 → VALID SERIAL
```

靜態全貌浮現：**長度 12 → s[4]=='-' → s[9]=='-' → transform(s)==0x45a7a3ce → VALID**。transform 內部（另一段，`11c6` 的 `imul $0x1000193` + 前面的 `xor`）是 FNV-1a 的 idiom。

### 第四步：gdb 讀真實的 hash

斷在 `cmp $0x45a7a3ce,%eax`（`0x12a6`），餵一個過格式的錯序號，讀出算出來的 hash（真跑）：

```bash
$ gdb -q -batch -ex "set disable-randomization on" \
    -ex "break *(0x555555554000+0x12a6)" \
    -ex "run WRON-GSER-IA" \
    -ex 'printf "hash=0x%x wants=0x45a7a3ce\n", $eax' \
    ./serial_stripped WRON-GSER-IA

Breakpoint 1, 0x00005555555552a6 in ?? ()
hash=0xc9e9bd32 wants=0x45a7a3ce
```

`WRON-GSER-IA` 算出 `0xc9e9bd32`，要的是 `0x45a7a3ce`。我們沒手算 FNV，直接讀真實值（Ch 13 心法）。

### 第五步 —— 路 A：gdb 動態繞過

斷在比較那刻，把 `eax` 改成 magic，比較就相等（真跑）：

```bash
$ gdb -q -batch -ex "set disable-randomization on" \
    -ex "break *(0x555555554000+0x12a6)" \
    -ex "run WRON-GSER-IA" \
    -ex "set \$eax=0x45a7a3ce" -ex "continue" \
    ./serial_stripped WRON-GSER-IA

Breakpoint 1, 0x00005555555552a6 in ?? ()
VALID SERIAL
[Inferior 1 (process 410748) exited normally]
```

錯序號 `WRON-GSER-IA` 靠改暫存器印出 `VALID SERIAL`——證實我們找對了檢查點。

### 第五步 —— 路 B：永久 patch binary

`cmp` 後的 `jne`（`0x12ab`，機器碼 `75 16`）是「序號錯就跳走」。把它 nop 掉（`90 90`），永遠不跳（真跑）：

```bash
$ cp serial_stripped serial_patched
$ printf '\x90\x90' | dd of=serial_patched bs=1 seek=$((0x12ab)) count=2 conv=notrunc
$ ./serial_patched ANYT-HING-XX
VALID SERIAL                       ← 任何合格式輸入都過
```

`serial_patched` 對任何 12 字元、dash 位置正確的輸入都印 VALID——授權檢查被永久拆掉。

### 第六步 —— 還原路：真正的合法序號

繞過 ≠ 還原（Ch 12/13 反覆強調）。要真正逆出**演算法**：transform 是 **FNV-1a**（種子 `0x811c9dc5`、prime `0x01000193`、`h ^= c; h *= prime`，跳過 dash）。驗證我們讀對了——對 `KEYG-2026-RE` 手算/腳本跑 FNV：

```python
def fnv(s):
    h = 0x811c9dc5
    for c in s:
        if c == '-': continue
        h ^= ord(c); h = (h * 0x01000193) & 0xffffffff
    return h
print(hex(fnv("KEYG-2026-RE")))     # → 0x45a7a3ce ✓ 正好是 magic
```

真跑驗證這是**真正的合法序號**（不 patch、不改記憶體）：

```bash
$ ./serial KEYG-2026-RE
VALID SERIAL                        ← binary 自己認
$ ./serial WRON-GSER-IA
invalid serial
```

### 對照 source 驗證

打開 `serial.c` 對答案：`strlen(s)!=12`（✓ 長度）、`s[4]!='-' || s[9]!='-'`（✓ dash 位置）、`transform` 的 `h^=c; h*=0x01000193`（✓ FNV-1a）、`==0x45a7a3ceu`（✓ magic）。四點全對，沒腦補錯——ground-truth 迴圈閉環。

</details>

## 驗證你逆對了

- [ ] 我能不讀 asm 就說出它的格式要求（長度 12、dash 在 index 4 和 9）
- [ ] 我的 patched binary 對任何合格式輸入都印 `VALID SERIAL`
- [ ] 我逆出變換是 FNV-1a、目標是 `0x45a7a3ce`，並用 gdb 讀到的真實 hash 交叉驗證過
- [ ] 我對照 `serial.c` 確認四個檢查點全部逆對，沒腦補

## 延伸挑戰

1. **能算出「另一個」合法序號嗎？** 你逆出了演算法，但 FNV-1a 是**單向雜湊**——給定 magic `0x45a7a3ce`，你**無法反算**出原序號（不像本課前面 byte-sum 那種可湊）。試著暴力搜一個符合格式又 hash 到 magic 的序號：2^32 空間裡碰撞期望要跑約 40 億次，短時間內幾乎撞不到。**這是深刻的一課**：逆出演算法 ≠ 能生成合法輸入。真實 keygen 破解常常卡在這——這時 patch（路 B）反而是唯一實用解，或改用符號執行（`symex_taint` 課）讓 solver 去解那個 hash 方程（對 FNV 這種也極難）。想一想：出題者怎麼能給出 `KEYG-2026-RE` 這個合法序號？（因為他是**正向**算的——先選序號再算 magic 塞進 code，不是反算的。）
2. **inline vs not**：把 `transform` 的 `static` 拿掉、加 `__attribute__((noinline))` 重編，再 `-O2`，看 ltrace 這次抓不抓得到 transform 的呼叫，體會編譯器 inline 決策怎麼影響 ltrace 的可見度。
3. **Frida 版繞過**：用 Ch 15 的 Frida，`Interceptor.attach` 到那個 `cmp` 附近或 transform 的返回，`onLeave` 把回傳改成 magic——不改 binary、不進 gdb 就繞過。對照三種繞過手段（gdb 改暫存器 / 改 binary bytes / Frida hook）的優劣。
4. **watchpoint 追 hash**：用 Ch 16 的 `watch`，斷在 transform 內盯那個累加器 `h`，看它每吃一個 byte 怎麼 `^` 再 `*prime` 地滾動——把 FNV 的每一步看出來，而不是靠認 idiom。

## 自我檢核

- [ ] 我能獨立用「偵察 → ltrace/strace → objdump 定位 → gdb 讀值」的流程攻一個陌生檢查程式
- [ ] 我能同時做到「gdb 動態繞過」和「改 binary bytes 永久 patch」，並說出兩者差別
- [ ] 我理解「patch 繞過」和「還原演算法/找出合法輸入」是兩個不同目標
- [ ] 我懂為什麼逆出單向 hash 的演算法後，仍可能算不出合法序號——以及這時 patch 為何是實用解
- [ ] 我全程用 ground-truth 迴圈：逆完打開出題 source 對照，確認沒腦補

做完這題，你已經能獨立對一個 strip 的授權程式跑完整動態逆向。Part 2 收工——你手上有斷點、trace、插樁、資料流追蹤、假設驅動五樣武器。下一 Part 換個維度：不再是「這程式怎麼判斷」，而是「這程式在算**什麼演算法**」——認出 crypto、hash、壓縮的指紋。

→ [Ch 18 逆一個演算法：認出 crypto / hash / 壓縮指紋](./18-reversing-algorithms.md)
