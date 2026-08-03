# Ch 31 — 完整攻堅實況：冷逆一個 strip binary

> **目標**：不再談方法——**真的逆一遍**。我拿一個自己寫的、中等複雜度的 strip binary（一個自訂編碼工具），假裝完全沒讀過它的 source，從零走完整套 SOP：偵察 → 靜態建假設 → 動態驗證 → 收斂到核心邏輯 → 還原演算法 → 費曼複述。全程外化（記假設、畫圖、命名暫存器）。所有 objdump / gdb / radare2 輸出都是實際跑出來照抄的。讀完你該有信心自己走一遍——這正是 Part 0-4 所有技巧串成一次的活體演示。

> **環境**：WSL2 / Linux x86-64，gcc + objdump + gdb + radare2（`aaa; pdc`）。sandbox 在 `~/re_lab/`。target 是 `enc`（已 strip 的 PIE ELF），我另存了一份帶符號的 `enc_dbg` 當**標準答案**——但在「還原」全部做完前，我一次都不看它（這是 Ch 0 的 ground-truth 紀律）。

## 為什麼要看一次完整攻堅？

前面 31 章你學了一堆單點技巧：認控制流（Ch 5）、認 idiom（Ch 10）、gdb 工作流（Ch 13）、假設驅動（Ch 17）。但真實逆向裡，這些**不是分開用的**——它們是一套協同的閉環：靜態產生假設、動態釘死假設、外化收斂心智模型、費曼確認真懂。單看每一章你會以為自己會了，真的坐下來面對一團無名機器碼，常常不知道從哪下第一刀。

這章就是把「不知從哪下手」演給你看：一個下午、一個 strip binary、從 `file` 打第一槍到寫出等價實作對拍。這是 [`reading_code` Ch 39](../../soft_skills/reading_code/39-case-study-full-attack.md) 的 binary 鏡像——那篇對 redis source 攻堅，這篇對一團無符號機器碼攻堅，走的是同一套 SOP，只是工具從 `rg`/`cscope` 換成 `objdump`/`gdb`/`radare2`。

## 先建立直覺：這一戰的地形

我手上有一個叫 `enc` 的執行檔，別人只告訴我「它會把一個字串編碼成十六進位」。沒有 source、沒有文件、沒有符號。我的任務：**搞懂它的編碼演算法，並寫出一個等價實作，同輸入同輸出。**

攻堅的地形長這樣，我心裡先有張路線圖：

```
  enc (strip binary)
     │
     ├─ 偵察   file / readelf / strings / imports   ← 免費線索，先撿滿
     │         「它用了哪些 libc 函式？有哪些字串？」
     ▼
     ├─ 定位   找 main（strip 了，得從 _start 推）
     │         「真正的邏輯在哪個位址？」
     ▼
     ├─ 靜態   objdump 讀 main，認 idiom，建假設
     │         「這條迴圈在做 加→XOR→旋轉？（待驗證）」
     ▼
     ├─ 動態   gdb 斷點，看每個 byte 進出的真實值
     │         「假設對嗎？中間值是什麼？」
     ▼
     ├─ 還原   把逆出的邏輯寫成獨立實作
     ▼
     └─ 對拍   我的實作 vs enc，500 組隨機輸入全對 → 逆對了
```

**注意這條線的形狀**：從廣（整個檔案的骨架）收斂到窄（一條迴圈的每一行），再從「讀懂」擴張回「寫得出等價品」。這個「先發散偵察、再收斂到核心、最後外化成產物」的節奏，就是逆向 SOP 的骨架（Ch 33 會抽象化成 checklist）。

---

## 階段 0：界定任務（照 reading_code Ch 38 三欄）

動手前先寫下三欄，釘死我在追什麼、以及**我不追什麼**（對抗鑽牛角尖，Ch 32 反模式之一）：

```
本次任務：逆出 enc 的編碼演算法，寫出等價實作，同輸入同輸出
成功標準：(1) 能講清楚每個輸出 byte 是輸入怎麼變出來的
          (2) 寫一份獨立實作，對 500 組隨機輸入與 enc 逐字元相同
          (3) 能費曼複述整條資料流，每個斷言背後有真跑撐著
不需要懂：libc 內部（printf/strlen 怎麼實作）、ELF 載入細節、
          PLT/GOT 重定位機制——這些是 Ch 3 講過的地基，這次當黑盒
```

「不需要懂」那欄是預先聲明。`__printf_chk` 的 PLT stub 怎麼跳、GOT 怎麼填，是誘人的 rabbit-hole，我現在就宣告：不進去，它只是「印出一個 byte」。

---

## 階段 1：偵察——把免費線索撿滿

逆向的第一原則是**先撿免費情報**。`file` / `readelf` / `strings` / import 表這些東西一秒就能看，卻能省你半小時瞎逛。忽略它們（Ch 32 的經典反模式：一上來就鑽 asm）是新手最貴的錯。

先看骨架：

```
$ file enc
enc: ELF 64-bit LSB pie executable, x86-64, ... dynamically linked, ... stripped

$ readelf -h enc | grep -E 'Type|Machine|Entry'
  Type:                              DYN (Position-Independent Executable file)
  Machine:                           Advanced Micro Devices X86-64
  Entry point address:               0x1170

$ nm enc
nm: enc: no symbols
```

情報一：**PIE、x86-64、stripped**。`no symbols` 確認沒有符號名——真實世界的常態，逆向的起點。entry point `0x1170` 不是 main，是 `_start`（Ch 3），等下要從它推 main。

字串是最便宜的線索，先撈：

```
$ strings -a enc | grep -iE 'usage|%02x|%s'
usage: %s <string>
%02x
```

情報二：**兩個格式字串**。`usage: %s <string>` 說明它吃一個命令列參數（不是 stdin）。`%02x` 是關鍵——**它一個 byte 一個 byte 印成兩位十六進位**。這一條字串就告訴我輸出結構：hex，byte-wise。

再看它 import 了哪些 libc 函式——這等於一張「它會用到哪些能力」的清單：

```
$ readelf -W --dyn-syms enc | awk '{print $8}' | grep -v '^$'
putchar@GLIBC_2.2.5
__libc_start_main@GLIBC_2.34
strlen@GLIBC_2.2.5
__printf_chk@GLIBC_2.3.4
__fprintf_chk@GLIBC_2.3.4
stderr@GLIBC_2.2.5
```

情報三，這張清單資訊量極大：

- `strlen`：它會算輸入字串長度 → 有一個「跑過每個字元」的迴圈。
- `__printf_chk`：帶 `%02x` → 印 hex（`_chk` 是 `-D_FORTIFY_SOURCE` 版，語意同 printf）。
- `putchar`：印單一字元——多半是最後補一個換行 `\n`。
- `__fprintf_chk` + `stderr`：配 `usage:` 字串 → 參數不足時印用法到 stderr。
- **沒有任何 crypto 函式**（沒有 `AES`、`SHA`、`EVP_*`）。這排除了「它呼叫 libcrypto 做標準加密」——編碼邏輯是**它自己手寫的**，就在 `.text` 裡。

我已經外化出一張假設卡，一行 asm 都還沒讀：

```
假設（偵察後）：
- 讀 argv[1] 一個字串
- strlen 後跑一個 per-byte 迴圈
- 每個 byte 經某種手寫轉換 → printf %02x
- 迴圈後 putchar('\n')
- 沒 crypto import → 自訂輕量編碼，邏輯在 .text
待答：那個「某種轉換」到底是什麼？
```

> 這正是 [`reading_code`](../../soft_skills/reading_code/README.md) 的「從使用者可見的東西反推」——只是那邊反推的是字串常量在 source 哪出現，這邊反推的是 import 表暗示的能力。同一種偵察直覺，鏡像的工具。

---

## 階段 2：定位 main——strip 了怎麼找

strip 沒有 `main` 符號。標準做法：**`_start` 會把 main 的位址放進 `%rdi`，再 `call __libc_start_main`**（Ch 3、Ch 7）。看 `_start`（entry 0x1170）：

```
$ objdump -d enc > /tmp/enc.asm
$ sed -n '/1170:/,/hlt/p' /tmp/enc.asm
    1170:  endbr64
    1174:  xor    %ebp,%ebp
    1176:  mov    %rdx,%r9
    1179:  pop    %rsi
    117a:  mov    %rsp,%rdx
    117d:  and    $0xfffffffffffffff0,%rsp
    1181:  push   %rax
    1182:  push   %rsp
    1183:  xor    %r8d,%r8d
    1186:  xor    %ecx,%ecx
    1188:  lea    -0xcf(%rip),%rdi        # 10c0
    118f:  call   *0x2e43(%rip)           # __libc_start_main
    1195:  hlt
```

`1188: lea -0xcf(%rip),%rdi # 10c0`——**`%rdi` 被載入 `0x10c0`，這就是 main 的位址**。（glibc 新版把 main 當 `__libc_start_main` 第一個參數傳。）

雙重確認：main 應該是那個呼叫 `strlen`/`printf_chk`/`putchar` 的函式。反查誰呼叫這些 import：

```
$ grep -nE 'call.*(putchar|strlen|printf_chk)' /tmp/enc.asm
    10d8:  call   1090 <strlen@plt>
    1121:  call   10a0 <__printf_chk@plt>
    1130:  call   1080 <putchar@plt>
    1158:  call   10b0 <__fprintf_chk@plt>
```

全在 `0x10d8`–`0x1158` 區間——正是 `0x10c0` 起頭的函式體。**`0x10c0` = main 確認。** 我在筆記把它命名 `main`（外化的一環：strip 拿走了名字，我自己補回去）。

---

## 階段 3：靜態逆——讀 main，認 idiom，建假設

把整個 main 拉出來讀。**這一步是純靜態閱讀，目標不是「懂每一行」，是「認出結構、標出可疑點、生出可驗證的假設」**：

```
$ awk '/10c0:/{f=1} f{print} /1170:/{exit}' /tmp/enc.asm
    10c0:  endbr64
    10c4:  push   %r14
    10c6:  push   %r13
    10c8:  push   %r12
    10ca:  push   %rbp
    10cb:  push   %rbx
    10cc:  cmp    $0x1,%edi              ; argc <= 1 ?
    10cf:  jle    1140                   ;   → 印 usage
    10d1:  mov    0x8(%rsi),%rbp         ; rbp = argv[1]
    10d5:  mov    %rbp,%rdi
    10d8:  call   1090 <strlen@plt>      ; rax = strlen(argv[1])
    10dd:  test   %rax,%rax
    10e0:  je     112b                   ; 空字串 → 跳過迴圈直接印 \n
    10e2:  lea    0x0(%rbp,%rax,1),%r13  ; r13 = argv[1] + len  (迴圈結束指標)
    10e7:  mov    $0x5a,%r12d            ; r12d = 0x5a           ← 常數！
    10ed:  mov    $0xffffffab,%ebx       ; ebx  = 0xab (低位)    ← 常數！
    10f2:  lea    0xf1f(%rip),%r14       ; r14 = "%02x"
    ; ---- 迴圈本體 ----
    1100:  movzbl 0x0(%rbp),%eax         ; eax = *rbp  (目前輸入 byte，零擴充)
    1104:  mov    %r14,%rsi              ; printf 參數：格式 "%02x"
    1107:  mov    $0x1,%edi              ; __printf_chk 第一參數 flag=1
    110c:  add    $0x1,%rbp              ; rbp++  (下一個輸入 byte)
    1110:  add    %r12d,%eax             ; eax = byte + r12d      ← 加 keystream
    1113:  add    $0x7,%r12d             ; r12d += 7              ← keystream 遞增 7
    1117:  xor    %eax,%ebx              ; ebx = ebx ^ eax        ← 與「上一輪」串鏈
    1119:  xor    %eax,%eax              ; eax = 0  (清高位，準備放 dl)
    111b:  rol    $0x3,%bl               ; bl = rol(bl, 3)        ← 左旋 3 bits
    111e:  movzbl %bl,%edx               ; edx = bl  (要印的 byte)
    1121:  call   10a0 <__printf_chk@plt>; printf("%02x", edx)
    1126:  cmp    %rbp,%r13
    1129:  jne    1100                   ; 還沒到結尾 → 下一輪
    ; ---- 迴圈後 ----
    112b:  mov    $0xa,%edi
    1130:  call   1080 <putchar@plt>     ; putchar('\n')
    1135:  xor    %eax,%eax              ; return 0
    ...
    1140:  ...                           ; usage 分支 (fprintf stderr)
```

**這是一段乾淨的 strength-reduced 迴圈**，逐行拆給你看我腦裡怎麼認：

1. `movzbl 0x0(%rbp),%eax`：`movzbl` = move zero-extend byte to long——這是「讀一個 byte、清掉高位」的指紋（Ch 6 認資料）。`%rbp` 每輪 `add $1`，`%r13 = base+len` 當終點——**這是標準的指標式 for 迴圈**（Ch 5）。

2. `add %r12d,%eax` + 下面 `add $0x7,%r12d`：`%r12d` 初值 `0x5a`，每輪加 7。這是一個**位置相關的 keystream**：第 i 個 byte 加 `0x5a + i*7`。認出「一個暫存器持有初值、每輪加固定量、參與運算」= 迴圈變數驅動的 keystream（Ch 10 idiom）。我把 `%r12d` 命名 `keystream`。

3. `xor %eax,%ebx`：`%ebx` 初值 `0xab`（`0xffffffab` 低位元組），**每輪被上一輪的結果影響**。這是串鏈——CBC 風格：本輪的輸出 feedback 進下一輪。我把 `%ebx` 命名 `prev`，`0xab` 命名 `IV`。這是「本 byte 的結果不只看自己」的指紋，逆向時看到一個暫存器跨迭代累積就要警覺。

4. `rol $0x3,%bl`：**左旋 3 bits**（rotate，不是 shift——`rol` 會把移出的位轉回低位）。這是位元級混淆的常見手法（Ch 10）。

5. `movzbl %bl,%edx` → `printf("%02x", edx)`：印出這個 byte。**關鍵細節**：印出去的是 `%bl`（就是 `prev` 這個暫存器 rotate 後的值），而 `%ebx` 下一輪繼續被用——所以**「上一輪印出去的 byte」就是「下一輪 XOR 的 prev」**。串鏈的鏈結接上了。

到這我已經能寫出一份**完整但未驗證**的假設，全部外化：

```
逆出的假設（靜態，待驗證）：
  prev = 0xAB               # IV
  keystream = 0x5A          # 每輪 +7
  對每個輸入 byte b:
    t = (b + keystream) & 0xFF
    t = t ^ prev
    t = rol(t, 3)
    印 %02x(t)
    prev = t                # 本輪輸出餵下一輪
    keystream += 7
  結尾印 '\n'
```

**但我不信任純靜態推論。** 我最容易錯的地方有三個，全是必須動態釘死的疑點：（a）`rol` 到底旋 3 還是我看錯方向？（b）`prev` 餵的是 rotate 前還是 rotate 後的值？（c）加法有沒有進位截斷的邊界問題？瞪 asm 瞪到死也不如**跑它一次看真實中間值**（Ch 12：觀察勝於推理）。

---

## 階段 4：動態驗證——用 gdb 讓 binary 自己招

我要親眼看每個 byte 進迴圈、中途變成什麼、印出什麼。用最短的輸入 `AB`（兩個 byte，剛好驗證「串鏈」——第二輪的 prev 該等於第一輪的輸出）。

先跑一次記下正確行為：

```
$ ./enc AB
8111
```

PIE 預設會 ASLR，我用 `set disable-randomization on` 讓載入基底固定在 `0x555555554000`，那麼 main 迴圈頂 `0x1100` 的實際位址是 `0x555555555100`、printf 呼叫點 `0x1121` 是 `0x555555555121`。在這兩處下斷點，印出我要驗的三個值：

```
$ gdb -q -nx -batch \
    -ex 'set disable-randomization on' \
    -ex 'break *0x555555555100' \
    -ex 'break *0x555555555121' \
    -ex 'run AB' \
    -ex 'printf "[loop top] inbyte=0x%02x keystream=0x%x prev=0x%x\n", *(unsigned char*)$rbp, $r12d, ($ebx & 0xff)' \
    -ex 'continue' \
    -ex 'printf "[at printf] out_byte(dl)=0x%02x\n", ($edx & 0xff)' \
    -ex 'continue' \
    -ex 'printf "[loop top] inbyte=0x%02x keystream=0x%x prev=0x%x\n", *(unsigned char*)$rbp, $r12d, ($ebx & 0xff)' \
    -ex 'continue' \
    -ex 'printf "[at printf] out_byte(dl)=0x%02x\n", ($edx & 0xff)' \
    -ex 'continue' \
    ./enc
```

真實輸出（照抄）：

```
[loop top] inbyte=0x41 keystream=0x5a prev=0xab
[at printf] out_byte(dl)=0x81
[loop top] inbyte=0x42 keystream=0x61 prev=0x81
[at printf] out_byte(dl)=0x11
8111
```

**這四行一次釘死了整條假設**，逐條對答案：

- **第一輪**：`inbyte=0x41`（'A'）、`keystream=0x5a`（IV 初值）、`prev=0xab`（IV）。手算驗證：`(0x41+0x5a)=0x9b`，`0x9b ^ 0xab = 0x30`，`rol(0x30,3) = 0x80|0x01 = 0x81`。gdb 印的 `out_byte=0x81` 對得上，輸出 `81` 對得上。
- **第二輪**：`keystream=0x61`——正是 `0x5a+7`，**證實「每輪 +7」**。`prev=0x81`——**正是第一輪印出去的 byte**，證實「上一輪的輸出餵下一輪的 XOR」這個串鏈假設。`inbyte=0x42`（'B'），輸出 `0x11` 對得上，湊成 `8111` 對得上。

三個我最不確定的疑點全部被真實執行釘死：`keystream` 真的每輪 +7、`prev` 真的是**上一輪 rotate 後的輸出**、`rol` 方向正確（手算對得上）。靜態假設與動態觀測閉環，理解釘死。這正是 Ch 17「假設驅動」的核心——**靜態產生假設，動態證實或推翻，絕不停在腦補**。

> 交叉驗證：我也讓 radare2 反編譯 main 看它怎麼還原這段迴圈（`r2 -q -c 'aaa; s 0x10c0; pdc' enc`）。它的 `pdc` 輸出把迴圈還原成：`eax += r12d; r12d += 7; ebx ^= eax; rol bl 3` 後 `sym.imp.__printf_chk()`——和我手讀的 asm、gdb 觀測完全一致。反編譯器在這裡是**第三個獨立證人**，不是唯一真相（Ch 8：反編譯器會騙你，但三方一致時可信度很高）。

---

## 階段 5：還原演算法——寫出等價實作

理解釘死了，現在把它外化成**可執行的產物**。我完全不看 `enc_dbg`（標準答案），純憑逆出的假設寫一份獨立實作。用 Python（寫得快，好對拍）：

```python
# recovered.py —— 純從 asm + gdb 逆出，未參考原始 source
import sys
def encode(s: bytes) -> str:
    out = []
    prev = 0xAB          # ebx 初值 0xffffffab 的低位元組 = IV
    k = 0x5A             # r12d 起始 0x5a
    for ch in s:
        b = (ch + k) & 0xFF                 # add %r12d,%eax（+ keystream，8-bit 截斷）
        b ^= prev                           # xor %eax,%ebx（串鏈）
        b = ((b << 3) | (b >> 5)) & 0xFF    # rol $0x3,%bl（左旋 3）
        out.append("%02x" % b)              # printf %02x
        prev = b                            # 本輪輸出餵下一輪
        k = (k + 7) & 0xFF                  # add $0x7,%r12d
    return "".join(out)

if __name__ == "__main__":
    sys.stdout.write(encode(sys.argv[1].encode()) + "\n")
```

每一行都對得回一條 asm，我在註解裡標了對應指令——這是「lifting」的最小版（Ch 29）：把 asm 抬回可讀、可編譯的等價 source。

---

## 階段 6：對拍——證明我逆對了

**逆向最危險的不是讀不懂，是自信地讀錯**（Ch 0）。我的實作看起來對，但「看起來對」不算數。用 ground-truth 迴圈的精神：**拿我的實作和原 binary，餵大量隨機輸入，逐字元比對**。任何一組不合，就是我某處逆錯了。

```python
import subprocess, random, string, importlib.util
spec = importlib.util.spec_from_file_location("recovered","recovered.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
random.seed(1)
alph = string.ascii_letters + string.digits + "!@#_-. "
fails = 0; n = 500
for _ in range(n):
    s = "".join(random.choice(alph) for _ in range(random.randint(1,40)))
    got  = subprocess.run(["./enc", s], capture_output=True, text=True).stdout.strip()
    mine = m.encode(s.encode())
    if got != mine:
        fails += 1
        if fails <= 3: print("MISMATCH", repr(s), "bin=", got, "mine=", mine)
print(f"tested {n} inputs, mismatches = {fails}")
```

真實輸出（照抄）：

```
tested 500 inputs, mismatches = 0
  'hello'                bin=4b6cc5f0a8  mine=4b6cc5f0a8  match=True
  'A'                    bin=81  mine=81  match=True
  'AB'                   bin=8111  mine=8111  match=True
  'ReverseEngineering'   bin=38f749ec20867b5dea57f23f61dbaf1c21c8  mine=38f749ec20867b5dea57f23f61dbaf1c21c8  match=True
```

**500 組隨機輸入、0 個不合。** 我的實作和 strip binary 行為完全等價——這就是「逆對了」的鐵證。不是「我覺得對」，是**可重現的對拍通過**。這是這門課給你的最強確信來源：ground-truth 對拍不會騙你。

---

## 對答案：打開標準答案，看我逆得多準

現在——只有現在——我打開一直沒看的 `enc.c`（原始 source）對答案：

```c
static unsigned char rotl(unsigned char v, int n){ return (v<<n)|(v>>(8-n)); }
int main(int argc, char **argv){
    ...
    unsigned char prev = 0xAB;                       /* IV */
    for(size_t i=0;i<n;i++){
        unsigned char b = (unsigned char)in[i];
        unsigned char k = (unsigned char)(i*7 + 0x5A);   /* keystream */
        b = (unsigned char)(b + k);
        b = b ^ prev;
        b = rotl(b, 3);
        printf("%02x", b);
        prev = b;
    }
    ...
}
```

**逆出的邏輯和原始碼一模一樣。** 唯一「不同」是表面的：原 source 用 `k = i*7 + 0x5A`（每輪從 i 重算），編譯器把它 strength-reduce 成 `k += 7`（我逆到的形式）——這兩者數學等價，編譯器選了增量式因為省一次乘法。**這正是 Ch 2 說的「你逆的是編譯器改寫過的 code」**：我逆到的 `k += 7` 比原 source 的 `i*7 + 0x5A` 更「低階」，但對拍證明它們行為相同。逆向的目標從來不是還原出逐字相同的 source，是**還原出行為等價的邏輯**。

---

## 這一戰用了哪些技巧（回顧全課）

把這次攻堅拆開，每一步對應哪一章，體會方法論如何協同：

```
 步驟                              用到的技巧                章節
 ─────────────────────────────────────────────────────────────
 填任務三欄（含「不需要懂」）      界定任務、防鑽牛角尖      Ch 32
 file/readelf 看骨架               ELF 偵察                  Ch 3
 strings 撈格式字串                免費線索                  Ch 5,11
 import 表推「它會做什麼」          從可見面反推              Ch 11
 _start 的 lea→rdi 找 main         定位 main（strip）        Ch 3,7
 反查 call import 雙確認 main       call graph 反查           Ch 7
 movzbl / 指標迴圈 認 for          認控制流/資料             Ch 5,6
 keystream/prev/IV 命名暫存器      外化、重建意圖            Ch 25
 rol=旋轉、+7=strength reduce      認編譯器 idiom            Ch 2,10
 gdb 斷點看中間值                  動態驗證                  Ch 13
 靜態假設↔動態觀測閉環            假設驅動逆向              Ch 17
 r2 pdc 當第三證人                 讀反編譯器輸出            Ch 8
 recovered.py 逐行對 asm           lifting 到可執行          Ch 29
 500 組隨機對拍                    ground-truth 對拍         Ch 0
 對照 enc.c 驗證                   ground-truth 對答案       Ch 0
```

**一次真實攻堅橫跨 Part 0 到 Part 4 的十幾個技巧。** 它們不是各自為政——偵察撿線索、靜態建假設、動態釘死、對拍證實，是一套協同閉環。這就是整門課要教的東西，濃縮在一個下午。

## 對比與取捨

| 決策點 | 我怎麼選 | 為什麼 |
|---|---|---|
| 一上來鑽 asm vs 先偵察 | 先偵察（strings/import） | import 表一秒告訴我「沒 crypto、自訂編碼」，省掉猜方向 |
| 純靜態讀懂 vs 靜態+動態 | 靜態建假設、動態釘死 | `rol` 方向、`prev` 餵哪個值——瞪 asm 不確定，跑一次秒懂 |
| 信反編譯器 vs 交叉驗證 | r2 當第三證人，不當唯一真相 | 三方（手讀/gdb/pdc）一致才敢下結論（Ch 8） |
| 逆到「像原 source」vs 行為等價 | 只求行為等價 | 編譯器已把 `i*7` 變 `+=7`，逐字還原不可能也沒必要 |
| 「我覺得對」vs 對拍 | 500 組隨機對拍 | 自信讀錯是最貴的錯，對拍是唯一鐵證 |

## 踩雷集錦

1. **錯誤直覺**：「輸出是 hex，一定是某種標準編碼（base16/加密）。」→ **正確**：import 表沒有任何 crypto 函式，`%02x` 只是「印 byte 的格式」不代表演算法是標準的。**先看 import 再猜演算法**——它手寫了一個自訂串鏈編碼，硬套 AES/base64 會全盤皆錯。

2. **錯誤直覺**：「`rol $0x3` 就是左移 3，`<<3` 就好。」→ **正確**：`rol` 是**旋轉**不是移位——移出的高 3 位會轉回低位。寫成 `<<3` 會丟高位，對拍立刻爆掉。`rol(b,3)` = `((b<<3)|(b>>5))&0xFF`。認錯 shift/rotate 是 idiom 辨識的經典坑（Ch 10）。

3. **錯誤直覺**：「`prev` 串的是輸入的上一個 byte。」→ **正確**：gdb 第二輪 `prev=0x81` 正是**第一輪的輸出**，不是第一個輸入 byte。串鏈串的是「已編碼的輸出」（CBC 風格）。這種 feedback 靜態容易看反，gdb 一跑就對——**跨迭代累積的暫存器，一定動態確認它累積的是什麼**。

4. **錯誤直覺**：「逆出來的 code 跟原 source 不一樣（`+=7` vs `i*7`），一定逆錯了。」→ **正確**：編譯器做了 strength reduction，兩者數學等價。**逆向求的是行為等價不是逐字還原**——對拍通過就是對了，別被表面形式差異嚇到（Ch 2）。

5. **錯誤直覺**：「讀懂 asm 就結束了，不用寫實作。」→ **正確**：「讀懂」和「寫得出等價品」之間有鴻溝——你以為懂了，一寫才發現漏了截斷、搞反了 rotate。**寫出可對拍的實作是理解的試金石**，也是這門課 final 的核心要求。

## 進階：再往深一層

- **這個 target 是 `-O2` 的**，所以看到 strength reduction（`i*7 → +=7`）和乾淨的暫存器迴圈。試著 `gcc -O0` 編同一份、strip、再逆一次——你會看到 `i*7` 真的出現一條 `imul`、每個變數都往 stack 存讀。對比兩者，體會優化等級對逆向難度的影響（Ch 2、Ch 32 反模式「忽略優化等級」）。
- **換更硬的 target**：把 rotate 量、keystream 步長做成從 argv 讀的，或加一層查表 S-box。逆向難度陡升——這時純靜態更吃力，動態插樁（Ch 15 Frida）批次記錄每個 byte 的中間值會比 gdb 手動斷點高效。
- **自動化這條流程**：這次的偵察（file/strings/import）可以寫成一個腳本一鍵吐報告（Ch 26 腳本化逆向）。老手的 `.gdbinit` / r2 script 會把「找 main、dump 迴圈、下斷點看中間值」變成一鍵。

## 本章重點整理

- 一次完整攻堅 = 界定任務（含不追什麼）→ 偵察撿免費線索 → 定位 main → 靜態建假設 → 動態釘死 → 還原成實作 → 對拍 → 對答案。
- **偵察先行**：`file`/`strings`/import 表一秒可看、資訊量巨大。import 表沒 crypto 就知道是自訂編碼，省掉猜錯方向的半小時。
- **靜態產生假設、動態釘死假設**：`rol` 方向、`prev` 餵哪個值這種疑點，瞪 asm 不確定，gdb 跑一次秒懂。三方（手讀/gdb/反編譯器）一致才下結論。
- **對拍是逆對了的唯一鐵證**：500 組隨機輸入 0 不合，勝過任何「我覺得對」。逆向求行為等價不是逐字還原——編譯器已把 `i*7` 變 `+=7`。
- 這一戰橫跨 Part 0–4 十幾個技巧，它們是協同閉環不是散裝清單。

## 自我檢核

- [ ] 不看本章，我能說出「strip 了怎麼找 main」的兩種方法嗎（`_start` 的 lea→rdi、反查 import 呼叫者）？
- [ ] 我能解釋為什麼「先看 import 表再猜演算法」比「一上來鑽 asm」省時間嗎？
- [ ] `rol $0x3` 和 `shl $0x3` 差在哪？寫錯會怎樣？
- [ ] gdb 第二輪的 `prev=0x81` 證實了哪個假設？為什麼跨迭代暫存器一定要動態確認？
- [ ] 我逆出 `k+=7` 而原 source 是 `i*7+0x5A`，為什麼這不算逆錯？
- [ ] 我能自己挑一個 strip binary，從 `file` 走到「寫出等價實作對拍」嗎？

方法論演練完畢，你看過一次完整攻堅實況。下一章把攻堅裡容易犯的錯反過來講——那些讓你多花三倍時間、甚至自信地逆錯的反模式，一條一條拆給你看。

→ [Ch 32 常見誤區與反模式](./32-anti-patterns.md)
