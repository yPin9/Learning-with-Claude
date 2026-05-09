# Ch 16 — x86-64 呼叫慣例與 ABI

> 目標：理解 System V AMD64 ABI 的暫存器分配、stack frame 結構，能解釋函式呼叫時發生什麼事，以及結構體傳遞規則。

## 為什麼要懂 ABI？

1. GDB 看 disassembly 時，知道哪個暫存器放了哪個引數
2. 解釋某些 C 行為（大結構體 return 為什麼比小結構體慢）
3. 嵌入式 / 核心開發需要寫 inline assembly
4. 面試常考「函式呼叫時 stack 長什麼樣子」

---

## System V AMD64 ABI（Linux / macOS）

### 整數與指標引數暫存器（依序）

```
第 1 個：rdi
第 2 個：rsi
第 3 個：rdx
第 4 個：rcx
第 5 個：r8
第 6 個：r9
第 7 個之後：stack（從右到左 push，caller 在 call 前放好）
```

**記憶法**：**D**ogs **S**it **D**own **C**almly, **R8 R9** come later.

### 浮點引數暫存器

```
xmm0 ~ xmm7（最多 8 個）
```

### 返回值暫存器

```
整數/指標 ≤ 64 bits：rax
整數/指標 128 bits：rdx:rax（rdx 放高位）
浮點：xmm0
結構體：視大小（見後面規則）
```

---

## Stack Frame 結構

```c
int foo(int a, int b) {    // a → rdi, b → rsi
    int local = a + b;
    return local;
}
```

對應的 stack layout（進入 foo 時）：

```
高地址
┌──────────────────────────┐
│  caller 的 stack frame   │
├──────────────────────────┤
│  return address（8 bytes）│  ← call 指令 push 的
├──────────────────────────┤  ← rsp（剛進入 foo）
│  saved rbp（8 bytes）    │  ← push rbp
├──────────────────────────┤  ← rbp = rsp
│  local（4 bytes）        │
│  padding（4 bytes）      │  ← 維持 16-byte 對齊
├──────────────────────────┤  ← rsp（sub rsp, 16 後）
低地址
```

`a`（rdi）、`b`（rsi）在暫存器，不在 stack。只有對 `a` 取地址（`&a`）時，編譯器才把它 spill 到 stack。

---

## Caller-Saved vs Callee-Saved

| 類型 | 暫存器 | 誰負責保存 |
|------|--------|-----------|
| **Caller-saved** | rax, rcx, rdx, rsi, rdi, r8–r11, xmm0–xmm15 | 呼叫方：call 前若需要這些值，自己 push |
| **Callee-saved** | rbx, rbp, r12–r15 | 被呼叫方：若函式內用到這些，必須先 push，返回前 pop |

```asm
; callee 若用到 rbx 的標準序言/後記：
foo:
    push rbp
    mov  rbp, rsp
    push rbx          ; rbx 是 callee-saved，用前要存
    sub  rsp, 8       ; 維持 16-byte 對齊（push rbx 減了 8，再減 8）
    ; ... 使用 rbx ...
    pop  rbx
    pop  rbp
    ret
```

---

## 結構體傳遞規則

```c
typedef struct { int x, y; }     Point2;    // 8 bytes
typedef struct { int x, y, z; }  Vec3;      // 12 bytes
typedef struct { double d[3]; }  Mat3x1;    // 24 bytes
```

規則（System V AMD64 §3.2.3）：

1. ≤ 16 bytes，且僅含整數/指標：拆成最多兩個 8-byte chunks，放 rax + rdx
2. ≤ 16 bytes，含浮點：視欄位分配 rax/xmm0 等
3. > 16 bytes：**hidden pointer**（呼叫者在 stack 分配空間，rdi 傳隱藏指標）

```c
Point2 get_point(void) {
    return (Point2){3, 4};
    // 8 bytes，走 rax：低 32 bits = x=3，高 32 bits = y=4
}

Mat3x1 get_mat(void) {
    return (Mat3x1){{1.0, 2.0, 3.0}};
    // 24 bytes > 16：caller 在 stack 準備 24 bytes
    //   → rdi 傳隱藏指標（第一個「隱藏引數」）
    //   → get_mat 把結果寫入 rdi 指向的記憶體
    //   → 返回 rax = rdi
}
```

這就是為什麼大結構體 return 比 int 慢，以及 C99 compound literal + NRVO 的優化意義。

---

## 查看實際 ABI 的方法

```bash
# 看呼叫慣例的 assembly：
gcc -O1 -S prog.c -o prog.s   # 輸出 AT&T 語法 assembly
gcc -O1 -S -masm=intel prog.c # Intel 語法（更易讀）

# GDB 看暫存器：
gdb ./prog
(gdb) break main
(gdb) run
(gdb) info registers rdi rsi rdx rax
```

---

## Windows x64 ABI 的差異

```
前 4 整數引數：rcx, rdx, r8, r9（不是 rdi/rsi/rdx/rcx）
shadow space：32 bytes（call 前 rsp 必須減 32，無論有沒有用到）
callee-saved 多了：rdi, rsi, xmm6–xmm15
```

跨平台代碼寫 inline assembly 或 OS-specific 代碼時一定要注意這個差異。

---

## 面試常考

**Q：C 函式最多幾個引數可以走暫存器？**
System V：6 個整數/指標 + 8 個浮點。超過走 stack。

**Q：main 怎麼拿到 argc 和 argv？**
啟動 code（`_start`）在 call main 前設定 rdi = argc、rsi = argv、rdx = envp。

**Q：rsp 為什麼在 call 前必須對齊 16 bytes？**
SSE/AVX 指令要求 16-byte 對齊。`call` push 8 bytes 返回地址，使 rsp mod 16 == 8，進入函式後序言的 `sub rsp, N`（N 是 16 的倍數）再讓 rsp 回到 16-byte 對齊。

**Q：leaf function（無 call）的 frame 長什麼樣？**
編譯器可以省略 `push rbp`（`-fomit-frame-pointer`），直接 `sub rsp, N` 管理 local 空間。

---

## 自我檢核

- [ ] 能背出前 6 個整數引數的暫存器（rdi/rsi/rdx/rcx/r8/r9）
- [ ] 知道 caller-saved vs callee-saved 的區別
- [ ] 知道 >16 bytes 結構體用 hidden pointer 傳，解釋它的 overhead
- [ ] 知道 Windows x64 的前 4 個引數是 rcx/rdx/r8/r9（不是 rdi/rsi）

→ [Ch 17 可變引數與 printf 內部實作](./17-variadic-printf.md)
