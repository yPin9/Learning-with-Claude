# Ch 30 — C++ 物件導向利用：vtable 劫持 / 物件再用

> **目標**：徹底理解 C++ 物件的記憶體佈局（vptr 在物件開頭、vtable 的結構）、虛擬呼叫的機器碼層面執行流程；掌握 vtable 劫持的完整攻擊路徑（UAF/overflow 蓋 vptr → 偽造 vtable → 虛擬呼叫觸發控制流劫持）；理解物件再用（COOP）的概念；知道 CFG 為何針對性地對付這個技法（引到 Ch 32）。用 mingw 編有虛擬函式的 C++ 程式、反組譯看 vptr/vtable 並貼真實輸出。

> **環境**：C++ 程式用 `C:\msys64\ucrt64\bin\g++` 編譯，反組譯用 `objdump -d`，兩者本機已確認可執行。需要 WinDbg/cdb 的步驟標 `> **未實測，理論預期**`。

## 為什麼需要這個？

你在 Ch 27 學 UAF，結尾說「attacker reclaim slot，填 fake_vptr，觸發虛擬呼叫，控制流劫持」——但那個說明跳過了細節：

- vptr 在物件的哪裡？vtable 長什麼樣？
- 「虛擬呼叫」在 x64 上的機器碼是哪幾條指令？
- fake vtable 要放什麼內容？偽造的記憶體要在哪裡？
- 為什麼 vtable 劫持需要和 CFG 對抗？

這章把這些全部打通。vtable 劫持是 Windows userland 和 kernel exploit 裡**最常見的控制流劫持原語**，也是你在 browser_pwn 課（V8 物件型別混淆、JIT spray 前的控制流取得）做過的技法在 C++ native 世界的對應。理解透徹了，Ch 31 的 info leak + Ch 32 的 CFG 才能串起來。

> 你在 browser_pwn 的 V8 物件做過 map confusion（讓 JS 引擎誤認物件型別），本章是完全類比的 C++ native 版本。對照著看效果最好。

## 先建立直覺：vptr 是什麼？

C++ 的虛擬函式（virtual function）讓「用基底類別指標呼叫衍生類別的方法」成為可能。這個能力在執行期才決定「實際呼叫哪個函式」——編譯器用 vptr + vtable 實現這件事：

```
  vptr（virtual pointer）：每個多型物件開頭的 8 bytes（x64）
  → 指向這個物件所屬類別的 vtable

  vtable（virtual function table）：一個函式指標陣列，在 .rdata（唯讀資料段）
  → 陣列的每個 slot 是這個類別對應虛擬方法的地址

  虛擬呼叫流程：
  p->method()
  ↓ 編譯成
  rax = *p         // 讀 p[0]，取出 vptr
  rdx = *(rax + N) // 讀 vtable[N/8]，取出函式指標
  call rdx         // 間接呼叫
```

這個三步驟是整個 vtable 劫持的攻擊面——改掉 vptr（指向 fake vtable），或改掉 vtable 的某個 slot（在 .rdata 裡），或兩者都改。

## C++ 物件的記憶體佈局：從真實輸出看

用 mingw 編一個有虛擬函式的 C++ 程式，觀察物件在記憶體裡的實際佈局：

```cpp
// vtable_demo.cpp
#include <cstdio>

class Animal {
public:
    int age;                                        // 4 bytes
    virtual void speak() { printf("Animal\n"); }   // 虛擬函式 0
    virtual void move()  { printf("move\n");  }    // 虛擬函式 1
    virtual ~Animal()    {}                         // 虛擬解構 2
};

class Dog : public Animal {
public:
    int breed;                                      // 4 bytes
    virtual void speak() override { printf("Woof\n"); }  // override slot 0
    virtual void fetch()          { printf("Fetch\n"); } // 新增 slot 3
};

int main() {
    Animal* a = new Animal();
    Dog*    d = new Dog();

    printf("Animal obj addr: %p\n", (void*)a);
    printf("Animal vptr    : %p  (at obj+0)\n", *(void**)a);
    printf("Dog    obj addr: %p\n", (void*)d);
    printf("Dog    vptr    : %p  (at obj+0)\n", *(void**)d);

    void** a_vt = *(void***)a;
    void** d_vt = *(void***)d;

    printf("\nAnimal vtable:\n");
    printf("  [0] speak  = %p\n", a_vt[0]);
    printf("  [1] move   = %p\n", a_vt[1]);
    printf("  [2] dtor   = %p\n", a_vt[2]);

    printf("\nDog vtable:\n");
    printf("  [0] speak  = %p  (overridden)\n", d_vt[0]);
    printf("  [1] move   = %p  (inherited)\n",  d_vt[1]);
    printf("  [2] dtor   = %p\n", d_vt[2]);
    printf("  [3] fetch  = %p\n", d_vt[3]);

    printf("\nsizeof(Animal) = %zu\n", sizeof(Animal));
    printf("sizeof(Dog)    = %zu\n",   sizeof(Dog));
    delete a; delete d;
}
```

編譯並執行（本機實測，mingw g++ 14.2，Windows 11 x64）：

```
$ g++ -O0 -g vtable_demo.cpp -o vtable_demo.exe
$ ./vtable_demo.exe
Animal obj addr: 0000024fdbeb1290
Animal vptr    : 00007ff6a1f3aab0  (at obj+0)
Dog    obj addr: 0000024fdbeb1210
Dog    vptr    : 00007ff6a1f3aa70  (at obj+0)

Animal vtable:
  [0] speak  = 00007ff6a1f380e0
  [1] move   = 00007ff6a1f380b0
  [2] dtor   = 00007ff6a1f38180

Dog vtable:
  [0] speak  = 00007ff6a1f37ff0  (overridden)
  [1] move   = 00007ff6a1f380b0  (inherited)
  [2] dtor   = 00007ff6a1f38080
  [3] fetch  = 00007ff6a1f38050

sizeof(Animal) = 16
sizeof(Dog)    = 16
```

幾個關鍵觀察：

1. **vptr 在物件的 offset 0**（x64 GCC 慣例，MSVC 也相同）：`Animal obj addr` 的 `+0x00` 就是 vptr，指向 Animal 的 vtable。
2. **vtable 在 .rdata**（唯讀資料段）：vtable 位址（`00007ff6a1f3aab0`）在 module 的 image 範圍內，不在 heap 也不在 stack，是唯讀的。
3. **Dog 的 vtable[0]（speak）和 Animal 的 vtable[0] 不同**：override 體現在 vtable slot 替換，不是新增。
4. **Dog.vtable[1]（move）和 Animal.vtable[1] 相同**：繼承但未 override 的方法指標直接複製。
5. **`sizeof(Animal) = sizeof(Dog) = 16`**：兩個類別大小相同——vptr（8 bytes）+ age（4 bytes）+ breed（4 bytes），Dog 的 `breed` 佔了 Animal 的 padding 空間。這對 UAF reclaim 的 bucket 匹配很重要。

## 底層機制：虛擬呼叫的機器碼

虛擬呼叫和非虛擬呼叫的機器碼完全不同。用反組譯看：

```cpp
// vcall.cpp
class Base {
public:
    virtual void foo() { printf("foo\n"); }
    virtual void bar() { printf("bar\n"); }
};

void do_call(Base* p) {
    p->foo();  // 虛擬呼叫：slot 0
    p->bar();  // 虛擬呼叫：slot 1
}
```

`do_call` 函式的實際反組譯（本機實測，mingw g++ -O0）：

```
0000000140001430 <_Z7do_callP4Base>:
   140001430:   push   %rbp
   140001431:   mov    %rsp,%rbp
   140001434:   sub    $0x20,%rsp
   140001438:   mov    %rcx,0x10(%rbp)   ; 存 p 到 local（Windows x64 calling conv）

   ; p->foo()  ← 虛擬呼叫 vtable[0]
   14000143c:   mov    0x10(%rbp),%rax   ; rax = p
   140001440:   mov    (%rax),%rax       ; rax = *p = vptr（vtable 位址）
   140001443:   mov    (%rax),%rdx       ; rdx = vtable[0]（foo 的函式指標）
   140001446:   mov    0x10(%rbp),%rax   ; rax = p（this 指標）
   14000144a:   mov    %rax,%rcx         ; rcx = this（Windows x64: arg1 in rcx）
   14000144d:   call   *%rdx             ; 間接呼叫 vtable[0]

   ; p->bar()  ← 虛擬呼叫 vtable[1]
   14000144f:   mov    0x10(%rbp),%rax   ; rax = p
   140001453:   mov    (%rax),%rax       ; rax = *p = vptr
   140001456:   add    $0x8,%rax         ; rax += 8（跳到 vtable[1]）
   14000145a:   mov    (%rax),%rdx       ; rdx = vtable[1]（bar 的函式指標）
   14000145d:   mov    0x10(%rbp),%rax   ; rax = p（this 指標）
   140001461:   mov    %rax,%rcx         ; rcx = this
   140001464:   call   *%rdx             ; 間接呼叫 vtable[1]

   140001466:   nop
   140001467:   add    $0x20,%rsp
   14000146b:   pop    %rbp
   14000146c:   ret
```

三條核心指令（每個虛擬呼叫都是這個模式）：

```
  mov rax, [p]            ; ①  讀 vptr：p 的 offset 0 是 vptr
  mov rdx, [rax + N]      ; ②  讀 vtable[N/8]：vtable 的第 N/8 個 slot
  call rdx                ; ③  間接呼叫
```

vtable[N] 的 N 值規律：slot 0 → `[rax+0]`、slot 1 → `[rax+8]`、slot K → `[rax + K*8]`（x64，每個函式指標 8 bytes）。

## vtable 物件記憶體佈局圖

```
  heap（free store）                    .rdata（唯讀資料段，在 image 裡）
  ┌──────────────────────────────────┐   ┌─────────────────────────────────┐
  │ Animal obj（16 bytes）           │   │ Animal::vtable                  │
  │ +0x00 vptr ─────────────────────┼──►│ [0] 0x...e0 → speak()           │
  │ +0x08 age  (int, 4 bytes)        │   │ [1] 0x...b0 → move()            │
  │ +0x0c padding (4 bytes)          │   │ [2] 0x...80 → ~Animal()         │
  └──────────────────────────────────┘   └─────────────────────────────────┘

  ┌──────────────────────────────────┐   ┌─────────────────────────────────┐
  │ Dog obj（16 bytes）              │   │ Dog::vtable                     │
  │ +0x00 vptr ─────────────────────┼──►│ [0] 0x...f0 → Dog::speak()     │
  │ +0x08 age  (int, 4 bytes)        │   │ [1] 0x...b0 → Animal::move()   │
  │ +0x0c breed (int, 4 bytes)       │   │ [2] 0x...80 → Dog::~Dog()      │
  └──────────────────────────────────┘   │ [3] 0x...50 → Dog::fetch()     │
                                         └─────────────────────────────────┘
```

vtable 在 .rdata（唯讀）——不能直接改 vtable 的 slot（沒有寫入權限）。

攻擊者能改的是**物件的 vptr**（在 heap，可讀寫），讓它指向一個**攻擊者控制的 fake vtable**（在可讀寫的記憶體裡）。

## vtable 劫持：完整攻擊路徑

### 前提條件

1. **可控的記憶體**（用來放 fake vtable）：heap 的某個 chunk、stack 的某個位置、BSS segment——任何可讀寫且位址已知的記憶體都行。在 ASLR 時代，「位址已知」要靠 info leak（Ch 31）。
2. **修改 vptr 的能力**：UAF（reclaim 後填 fake vptr）或 heap overflow（overflow 蓋相鄰物件的 vptr）。
3. **可觸發的虛擬呼叫**：程式在攻擊者能控制的時機，對目標物件做虛擬呼叫。

### 攻擊者要構造的 fake vtable

fake vtable 就是一個「函式指標陣列」，放在攻擊者控制的記憶體裡：

```
  攻擊者控制的記憶體（例如 heap chunk 的 user data）：

  +0x00 gadget_0  ← fake vtable[0]（程式呼叫的是哪個 slot，就要對應那個 offset）
  +0x08 gadget_1  ← fake vtable[1]
  +0x10 gadget_2  ← fake vtable[2]
  ...

  gadget_0 / gadget_1 的值：
  - 可以是 ROP gadget 的位址（搭配 ROP chain 使用）
  - 可以是 shellcode 位址（如果 NX 沒開或有 JIT spray）
  - 可以是一個 trampoline，把 rsp 指向 attacker 控制的 ROP chain
```

### 完整攻擊時序：UAF → vtable 劫持

```
  t=0: victim = new VictimClass()
       ┌──────────────────────────────────────────────────────────┐
       │ heap slot：[vptr: &VictimVtable][other fields...]        │
       │ heap 外的 .rdata：VictimVtable[0]=real_speak,  ...     │
       └──────────────────────────────────────────────────────────┘
       dangling_ptr = victim  (stored somewhere in program state)

  t=1: delete victim  (或 victim->Release())
       ┌──────────────────────────────────────────────────────────┐
       │ heap slot：BusyBitmap bit 清 0（記憶體內容可能還在）    │
       │ dangling_ptr 還指向這個 slot（懸空）                     │
       └──────────────────────────────────────────────────────────┘

  t=2: [攻擊者動作] — 準備 fake vtable，reclaim slot
       步驟 a: 在攻擊者控制的記憶體（例如另一個 heap chunk）放置：
               fake_vtable[0] = addr_of_ROP_gadget_or_shellcode
               fake_vtable[1] = ...（如果需要）

       步驟 b: 分配 sprite object（size = VictimClass 的 size，同 LFH bucket）
               把 sprite 的 +0x00 填成 &fake_vtable

       步驟 c: sprite 佔回 victim 的 slot（或直接 overflow 蓋 vptr）
       ┌──────────────────────────────────────────────────────────┐
       │ heap slot（原 victim）：[vptr: &fake_vtable][...]        │
       └──────────────────────────────────────────────────────────┘

  t=3: [程式繼續執行] — 觸發 Use：dangling_ptr->virtual_speak()
       機器碼：
         mov rax, [dangling_ptr]        ; rax = &fake_vtable  ← 攻擊者填的
         mov rdx, [rax + 0]             ; rdx = fake_vtable[0] = ROP gadget
         mov rcx, dangling_ptr          ; this = dangling_ptr（不重要了）
         call rdx                       ; → 跳到 ROP gadget
       → 控制流劫持成功！
```

### 偽造 vtable 的精確要求

攻擊者需要在 fake vtable 裡放的 slot 數量，取決於程式觸發的是 vtable 的哪個 slot：

```
  如果程式呼叫 p->method_K()，機器碼是：
  mov rdx, [vptr + K*8]
  call rdx

  → fake_vtable 只需要 offset K*8 的位置有有效的跳轉目標
  → offset 0 到 (K-1)*8 的內容可以是任意值（程式不會讀它們）
  → 但如果程式先呼叫 method_0 再呼叫 method_K，你要兩個 slot 都準備好

  常見的「懶惰 fake vtable」：
  fake_vtable 的所有 slot 都填成同一個 gadget 位址
  → 無論程式呼叫哪個 virtual method，都跳到你的 gadget
```

### heap overflow 蓋 vptr：另一條路徑

如果不是 UAF，而是 heap overflow，可以直接蓋到相鄰物件的 vptr：

```
  layout（heap 上相鄰的兩個物件）：

  ┌──────────────────────────────────────────────────────┐
  │ attacker_obj（你控制的物件，允許 overflow）          │
  │ [vptr:xxx][data .........overflow-data...........]   │
  └──────────────────────────────────────────────────────┘
  ┌──────────────────────────────────────────────────────┐
  │ victim_obj（你想劫持的物件）                         │
  │ [vptr: 原始值 → 被 overflow 蓋成 &fake_vtable]       │
  └──────────────────────────────────────────────────────┘

  overflow 的精度要求：
  - 要蓋到 victim_obj.vptr（offset 0），但不能蓋到其他敏感欄位（除非設計好）
  - 在 NT Heap：chunk 之間有 16 bytes header → overflow 要蓋過 header 才到下一個 chunk 的 user data
  - 在 Segment Heap LFH：slot 之間緊密排列（沒有 per-slot header，slot 大小由 bucket BlockSize 決定）
```

## 物件再用（Object Reuse）

物件再用是 UAF 劫持的進階版本：不只是「佔回 slot 填 fake_vptr」，而是用**完全另一個型別的物件**佔回 victim 的 slot，讓型別混淆產生更豐富的原語。

```
  範例：
  victim type:  FileObject（有 vptr，vptr 在 +0x00；handle 欄位在 +0x08）
  sprite type:  StringBuffer（沒有 vptr；+0x00 是 buffer_ptr，+0x08 是 length）

  sprite 佔回 victim slot 後：
  dangling FileObject* ptr 的 +0x00 讀到的是「buffer_ptr」（StringBuffer 的欄位）
  → 把 buffer_ptr 設成 fake_vtable 的位址
  → 程式用 FileObject* 做虛擬呼叫時，讀 +0x00 拿到 fake_vtable 的位址，跳到劫持目標

  攻擊者能控制 StringBuffer 的 buffer_ptr（透過字串內容的分配）
  → 對 fake_vtable 位址有完整的控制
```

這就是 type confusion 的利用形式（Ch 27 提過）。V8 的 Map confusion（browser_pwn）是完全相同的原理：讓引擎誤認物件型別，讓欄位被「以錯誤型別解釋」。

## COOP（Counterfeit Object-Oriented Programming）概念

COOP 是 Schuster 等人 2015 年提出的技法（S&P 2015）：在 CFG 開啟的環境下，直接跳到 shellcode 或任意 ROP gadget 可能被 CFG 擋下（Ch 32 詳述）。COOP 的繞過思路：

```
  不跳到「非法的呼叫目標」，而是把虛擬呼叫重定向到
  一個「合法的（在 CFG 白名單裡的）C++ 虛擬函式」
  → 但選的是一個「功能性的 gadget」：這個合法虛擬函式做的事情
    恰好是攻擊者需要的原語（例如：呼叫某個函式指標、讀寫記憶體）

  COOP 把「虛擬呼叫鏈」變成 ROP chain 的高層類比：
  - 每個「COOP gadget」是一個合法的虛擬函式
  - 攻擊者把多個偽造物件串成一條鏈（利用 C++ 迴圈 / virtual dispatch 邏輯）
  - 每個 gadget 在呼叫完成後，把控制流傳遞給下一個 gadget
  - 最終達到任意操作
```

COOP 是 CFG 環境下 vtable 劫持的「後繼技術」。如果你的目標開了 CFG（Win 10+，Edge、Windows Store app 等），vtable 劫持的目標不能是任意地址，必須選 CFG-valid 的目標——COOP 就是在這個約束下找出「有效攻擊路徑」的方法論。

## vtable 劫持與 CFG 的關係（引到 Ch 32）

CFG（Control Flow Guard）的設計動機**就是針對 vtable 劫持**：

```
  CFG 的保護邏輯（Ch 32 詳述）：
  每個間接呼叫（call rdx / call [rax+N]）執行前，
  先查「CFG bitmap」確認目標位址是否是合法的間接呼叫目標
  → 只有在 binary 裡有「函式定義或明確標記」的函式才在白名單
  → 跳到 heap / stack / 任意 gadget → CFG 發現不合法 → 行程終止

  vtable 劫持的攻擊路徑：
  call *%rdx（虛擬呼叫）→ rdx = fake_vtable[N] = shellcode_addr
  → CFG check：shellcode_addr 不在白名單 → 擋下

  CFG 把「間接呼叫只能跳到白名單地址」的要求落地，
  直接破壞了「fake vtable 指向任意地址」的假設
```

**CFG 不是萬能的**（Ch 32 會分析哪些 bypass 路徑存在），但它確實大幅提高了 vtable 劫持的門檻——你不能再直接跳到 heap 上的 shellcode，必須找合法目標或繞過 CFG 本身。

## Linux / glibc 的對比

| 維度 | Windows C++ vtable 劫持 | Linux C++ vtable 劫持 |
|---|---|---|
| vptr 位置 | 物件開頭（offset 0） | 物件開頭（offset 0，相同） |
| vtable 位置 | .rdata（唯讀） | .rodata（唯讀） |
| 攻擊 vptr 的路徑 | UAF reclaim / heap overflow | UAF reclaim / heap overflow（相同） |
| 額外保護 | CFG（間接呼叫目標 whitelist） | CET (IBT, Linux 5.18+)；CFG 前幾乎無保護 |
| 典型利用目標 | svchost / Edge / COM 物件 | libc++ / libstdc++ 任意 C++ 程式 |
| fake vtable 放哪 | heap（ASLR bypass 後已知位址） | heap（同） |
| browser_pwn 的對應 | N/A（browser 自己管記憶體） | V8 HeapObject 的 Map confusion |

**最大差異**：Windows 開了 CFG 的環境，fake vtable 的目標必須是 CFG-valid 的地址——這讓 vtable 劫持從「任意地址」降級為「受限地址集合」，需要配合 COOP 或 CFG bypass（Ch 32）才能完整利用。Linux 上 CFI（Control Flow Integrity）普及率遠低於 Windows，vtable 劫持對大多數 Linux C++ 程式仍然可以直接跳到任意地址。

## 踩雷集錦

1. **「vptr 在物件的末尾（為了不破壞資料佈局）」**：錯。主流 ABI（Itanium C++ ABI，GCC/Clang 使用；MSVC 也是）規定 vptr 在物件最開頭（offset 0）。這是有意的設計：任何多型物件指標都可以直接把前 8 bytes 解釋成 vptr，不需要知道確切型別。

2. **「vtable 在 heap 上，所以可以直接修改 vtable 的 slot」**：錯。vtable 在 **`.rdata`（唯讀資料段）**，link time 就被放進 image，執行期通常是唯讀的（除非有 mprotect 漏洞）。攻擊者修改的是物件的 **vptr**（在 heap，可讀寫），讓它指向 fake vtable，而不是修改真正的 vtable。

3. **「fake vtable 的所有 slot 都要填，否則程式會 crash」**：不完全對。程式只讀用到的那些 slot（依據虛擬呼叫的 slot index）。如果程式只呼叫 `p->method_0()`，fake vtable 只有 slot 0 需要合法跳轉目標，其他 slot 是什麼值無所謂（除非程式之後又呼叫了其他 slot）。

4. **「heap overflow 蓋 vptr 很容易，overflow 一個 chunk 剛好蓋到下一個的 vptr」**：不一定。NT Heap 的 chunk 之間有 16 bytes 的 `_HEAP_ENTRY` header，overflow 要先穿越 header（並且不能讓 heap check 在穿越時 AV）才能到下一個 chunk 的 vptr。Segment Heap LFH 的 slot 之間沒有 per-slot header（slot 緊密排列），但同樣需要精確計算 overflow 長度。

5. **「有了 vtable 劫持就夠了，ROP 不需要」**：現代 Windows 的 NX（DEP）+ CFG 環境，vtable 劫持只是「獲得一次間接呼叫控制流」的入口點——`call rdx` 跳到的地方必須是 CFG-valid 的地址。在開了 CFG 的 binary 裡，跳到 heap 上的 shellcode 會被 CFG 攔截。需要配合 ROP（跳到合法 gadget）或 COOP（跳到合法虛擬函式）才能完整利用。

## 進階：再往深一層

### 多重繼承的 vptr 佈局

單繼承：物件只有一個 vptr（在 offset 0）。多重繼承：物件有**多個 vptr**，每個繼承鏈一個：

```
  class C : public A, public B { ... };

  C 物件的記憶體佈局：
  ┌──────────────────────────────────────────────────────────┐
  │ +0x00 vptr_A  ← 指向 C-as-A 的 vtable（A 子物件的 vptr）│
  │ +0x08 A 的欄位...                                        │
  │ ...                                                      │
  │ +0xXX vptr_B  ← 指向 C-as-B 的 vtable（B 子物件的 vptr）│
  │ +0xXX+8 B 的欄位...                                     │
  └──────────────────────────────────────────────────────────┘
```

對 exploit 的影響：多重繼承的物件有多個 vptr，可以選擇攻擊哪個 vptr（哪個虛擬呼叫鏈先被觸發、哪個 vptr 相對位置更容易 overflow 到）。在 COM 物件（大量使用多重繼承）的 UAF 利用中，vptr 的選擇是關鍵決策。

### 虛擬解構子（virtual destructor）的 vtable slot

注意：`virtual ~Animal()` 在 GCC 的 Itanium ABI 裡，在 vtable 裡佔**兩個** slot（完整解構和刪除解構，d0 和 d1），而不是一個。MSVC ABI 也類似（scalar deleting destructor + vector deleting destructor）。

實際 slot 的安排：

```
  GCC/Clang 的虛擬解構子在 vtable：
  [0] speak
  [1] move
  [2] complete destructor (Animal::~Animal, 析構但不 delete)
  [3] deleting destructor  (call [2] 然後 operator delete)

  （這解釋了為什麼 vtable_demo 的輸出裡 vtable 看起來只有 3 個 slot
    但實際上解構子佔的是什麼：需要看反組譯的確切 offset）
```

> **未實測（需 WinDbg 或 dumpbin 查 vtable 詳細 layout）**：上面的描述基於 Itanium ABI，MSVC ABI 的 vtable layout 不完全相同（destructor slots 的排列有差異）。以 MSVC dumpbin /RELOCATIONS 或 WinDbg dt 輸出為準。

### `__vfptr` 和 `__vbtable`（MSVC 的命名）

MSVC 把 vptr 命名為 `__vfptr`（virtual function pointer），虛擬基底類別的指標叫 `__vbtable`（virtual base class table pointer）。概念完全相同，只是名稱不同。在 WinDbg 看 C++ 物件時會看到這些欄位名。

## 動手練習

**練習 1（本機可執行）**：編譯本章的 `vtable_demo.cpp`，觀察輸出。然後修改 `main()`，用 `Dog* d = new Dog()` 分配 Dog 物件，接著用 `delete d` free 它，再立刻做：

```cpp
void** dangling = (void**)d;  // dangling pointer，d 已被 free
printf("dangling[0] = %p\n", dangling[0]);  // 讀 vptr slot（可能還在）
```

觀察：free 後，dangling pointer 讀到的 vptr 是原始值還是被 heap manager 改過？在 release 環境（無 Page Heap）下，預期還是原始 vptr——這就是「UAF info leak 讀 vptr → leak module base」的基礎。

**練習 2（本機可執行）**：在 `vtable_demo.cpp` 中，把 `Animal` 物件的 vptr 手動改掉，讓虛擬呼叫跳到另一個函式：

```cpp
Animal* a = new Animal();

// 把 a 的 vptr 改成 Dog 的 vtable（或者 fake vtable）
void** fake_vt = *(void***)new Dog();  // Dog 的 vtable 位址
*(void**)a = fake_vt;                  // 蓋 a 的 vptr

a->speak();  // 用 Animal* 呼叫，但 vptr 已是 Dog 的 → 呼叫 Dog::speak
```

觀察輸出，確認 vptr 替換讓 `a->speak()` 走了 Dog 的實作而不是 Animal 的。這是 vtable 劫持的最小 demo。

## 本章重點整理

- vptr 在物件的 offset 0（x64，GCC 和 MSVC 都相同）；vtable 在 .rdata（唯讀）；虛擬呼叫的機器碼是 `mov rax, [p]` → `mov rdx, [rax+N*8]` → `call rdx` 三步
- vtable 劫持的本質：改掉物件的 **vptr**（heap 上可寫）讓它指向 fake vtable；程式觸發虛擬呼叫時，間接跳到攻擊者設定的地址
- 兩條進入路徑：UAF（reclaim 後 sprite 在 slot 的 offset 0 填 fake vptr）、heap overflow（蓋到相鄰物件的 vptr）
- COOP：CFG 環境下的 vtable 劫持變體，把跳轉目標限制在 CFG-valid 的合法虛擬函式，透過鏈式呼叫完成複雜攻擊
- CFG 的設計動機就是針對 vtable 劫持——它把間接呼叫目標限制在白名單，讓「fake vtable → 任意地址」失效

## 自我檢核

- [ ] 不看筆記，能在紙上畫出 Animal/Dog 物件在 heap 和 vtable 在 .rdata 的完整佈局圖（含 vptr 指向關係）
- [ ] 能把虛擬呼叫 `p->foo()` 展開成三條機器碼指令，說清楚每條指令讀了什麼記憶體
- [ ] 面試被問「vtable 劫持的攻擊步驟」，能說出前提（可控記憶體、修改 vptr 的能力、可觸發的虛擬呼叫）+ 攻擊時序（準備 fake vtable → 蓋 vptr → 觸發 virtual call）
- [ ] 能解釋 COOP 和直接 vtable 劫持的差異，以及為什麼 CFG 讓 COOP 成為必要
- [ ] 知道 heap overflow 蓋 vptr 和 UAF reclaim 蓋 vptr 的差別：timing（存活 vs. 已 free）和 precision（overflow 距離 vs. sprite 大小匹配）

## 延伸閱讀

### 論文

- **[Out of Control: Overcoming Control-Flow Integrity](https://ieeexplore.ieee.org/document/6956567)** — Enes Göktaş et al., IEEE S&P 2014
  - **讀哪裡**：全文，特別是 Section 4（攻擊 C++ 虛擬呼叫的細節）
  - **學什麼**：CFG/CFI 方案為什麼不夠用、vtable 劫持的攻擊面的系統性分析；本章技法的學術一手來源
  - **前提知識**：本章全部

- **[Counterfeit Object-Oriented Programming](https://ieeexplore.ieee.org/document/7163058)** — Felix Schuster et al., IEEE S&P 2015
  - **讀哪裡**：全文（特別是 Section 3–4，COOP gadget 的分類和 exploit 構造）
  - **學什麼**：COOP 的原始論文；CFG 環境下 vtable 劫持的完整攻擊方法論
  - **前提知識**：本章 + Ch 32（CFG）基礎

### 部落格

- **[j00ru — Exploiting Windows Kernel: Virtual Dispatch](https://j00ru.vexillium.org/)** — Mateusz Jurczyk（j00ru）
  - **讀哪裡**：搜尋 j00ru 部落格的 virtual dispatch / UAF 相關文章
  - **學什麼**：真實 CVE 中 C++ 物件 UAF 到 vtable 劫持的完整流程，kernel 和 userland 的比較
  - **前提知識**：Ch 27 + 本章

- **[Connor McGarr — C++ vtable Exploitation](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：vtable 和 C++ 物件利用系列
  - **學什麼**：Windows 現代環境（Win 10/11）下 vtable 劫持的實際操作步驟，包含 info leak 到 vtable 劫持的完整鏈
  - **前提知識**：本章 + Ch 31（info leak）

### 官方文件

- **[Microsoft — Control Flow Guard](https://learn.microsoft.com/en-us/windows/win32/secbp/control-flow-guard)** — Microsoft Learn
  - **讀哪裡**：全文（約 10 分鐘），了解 CFG 從 Microsoft 視角對 vtable 劫持的防禦定位
  - **學什麼**：CFG 保護的具體機制，為 Ch 32 做準備
  - **前提知識**：本章全部

vtable 劫持給了「一次虛擬呼叫的控制」，但要讓它轉化為穩定的 code execution，還需要知道跳到哪裡——也就是說你必須有合法的 ROP gadget 位址、stack 位址、或 CFG-valid 的 trampoline 位址。這些位址全部來自 info leak。

→ [Ch 31 — info leak 原語大全](./31-info-leak-primitives.md)
