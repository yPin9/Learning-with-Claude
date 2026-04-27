# Ch 3 — Decompiler (F5) 情境快捷鍵

> 目標：在 Pseudocode window 裡用鍵盤改 function prototype、改 LVAR、折疊 cast、把偽代碼打磨成像 C 原始碼。

## F5 不是魔法

F5 產出的偽代碼長這樣：

```c
__int64 __fastcall sub_401200(__int64 a1, int a2)
{
  __int64 v3;
  int v4;
  _BYTE *v5;

  v3 = *(_QWORD *)(a1 + 0x18);
  v4 = *(_DWORD *)v3;
  if ( v4 <= a2 )
    return 0LL;
  v5 = (char *)v3 + 4 * a2 + 8;
  *v5 = 0x41;
  return 1LL;
}
```

看起來有型但沒意義 — 因為 Hex-Rays 不知道 `a1` 是什麼、`v3` 是什麼。**你的工作就是告訴它**，它會立刻把偽代碼重寫得像樣。

這一章就是教你「告訴它」的所有鍵。

## 打磨 pseudocode 的動作分類

```
   原始 pseudocode
         │
         ▼
  ┌──────────────────────────────┐
  │  1. 改 function prototype    │  ← Y
  │  2. 改 local variable 名稱   │  ← N
  │  3. 改 local variable 型別   │  ← Y（在 v3 上）或 Shift+L
  │  4. 套 struct 給 pointer     │  ← 右鍵 or Y 填 MyStruct *
  │  5. 切割 / 摺疊顯示           │  ← Numpad +/-、Ctrl+P
  │  6. 強制展開 cast / 隱藏      │  ← Ctrl+P → hide casts
  └──────────────────────────────┘
         │
         ▼
   打磨過的 pseudocode（像 C 原始碼）
```

## 核心鍵速查

| 鍵 | 在什麼上按 | 做什麼 |
|---|---|---|
| `F5` | 任何位置 | 開 / 切換當前 function 的 pseudocode |
| `Fn + F5` (Mac) | 同上 | Mac 要加 Fn |
| `N` | variable / function 名 | 改名（這和 disasm 一樣） |
| `Y` | variable / function 名 | 改型別 / prototype |
| `Shift+L` | variable | map 到另一個 LVAR（合併兩個其實是同一個的 var） |
| `Ctrl+數字` | variable | 把立即數改成 enum |
| `K` | 數字常數 | 把數字改成顯示成 char / offset / enum |
| `H` | 數字常數 | hex / decimal 切換 |
| `/` | 任何行 | 加 C 風格註解（只在 pseudocode 顯示） |
| `Tab` | 任何位置 | 跳回 disasm 對應行（反向也可） |
| `Esc` | 任何位置 | 回上一個位置 |
| `Numpad +` | function 名 | 展開 collapsed 段 |
| `Numpad -` | function 名 | 摺起 function 末段 |
| `Ctrl+P` | 任何位置 | 打開 Hex-Rays options（hide casts、auto rename 等） |
| `Alt+=` | 任何位置 | 顯示 microcode（9.x，進階用，Ch 13 會講） |

## 一條打磨動線實戰

還是上面那段 code：

```c
__int64 __fastcall sub_401200(__int64 a1, int a2) {
  __int64 v3;
  int v4;
  _BYTE *v5;
  v3 = *(_QWORD *)(a1 + 0x18);
  v4 = *(_DWORD *)v3;
  if ( v4 <= a2 ) return 0LL;
  v5 = (char *)v3 + 4 * a2 + 8;
  *v5 = 0x41;
  return 1LL;
}
```

從 disasm 線索我判斷 `a1` 是某個 object，`a1+0x18` 是一個 array header（`len; data[]`）。`a2` 是 index。

動線：

```
1. 游標在 a1 上，按 Y  →  填 Object *a1
2. 游標在 a2 上，按 N  →  改名 index
3. 游標在 v3 上，按 Y  →  填 Array *v3
4. Shift+F1 開 Local Types，新增：
     struct Array { int32_t len; char data[1]; };
5. 回 pseudocode，游標在 v3 上按 Y  →  填 Array *
6. 現在 v3->len 和 v3->data[...] 會自動出現
```

打磨後：

```c
bool __fastcall Object_set_char(Object *self, int index) {
  Array *arr = self->arr;
  if ( arr->len <= index ) return false;
  arr->data[index] = 'A';
  return true;
}
```

差很多。整個流程全鍵盤。

## 改 function prototype 的幾種寫法

`Y` 之後彈出輸入框，填什麼都吃：

```c
void foo(int x)
int __fastcall foo(int x, char *y)
ssize_t __fastcall read(int fd, void *buf, size_t count)
BOOL __stdcall MyCallback(HWND hwnd, LPARAM lParam)      // Windows
_BYTE *__cdecl parse(const char *input, int *out_len)
```

三個提醒：

1. **calling convention 要寫對**：x86 上 `__fastcall` / `__cdecl` / `__stdcall` 會影響參數取哪個暫存器。寫錯的話偽代碼會整個錯位。
2. **用 til 裡有的型別**：`HWND` / `LPARAM` / `FILE *` 這些標準型別 til 都有，直接打字就行，不用自己定義。
3. **遇到多 return 值（x86-64 回傳 struct）**：把 return type 寫成 struct，Hex-Rays 會自動還原 `rax` + `rdx`。

## Shift+L 合併「其實同一個」的變數

常見場景：Hex-Rays 把同一個邏輯變數拆成兩個（`v3` 和 `v7`）。你在 `v7` 上按 `Shift+L`，輸入 `v3`，之後兩個都變成 `v3`。反向也一樣。

這個鍵救場次數比想像多。

## 把 flag 常數改成 enum

遇到 `flags & 0x4` 這種 code，先開 Local Types 定義 enum：

```c
enum FileFlags {
  FLAG_READONLY = 0x1,
  FLAG_HIDDEN   = 0x2,
  FLAG_SYSTEM   = 0x4,
  FLAG_ARCHIVE  = 0x8,
};
```

回 pseudocode，游標在 `0x4` 上按 `M`（或右鍵 → Symbolic constant），選 `FileFlags::FLAG_SYSTEM`。以後所有看到 `0x4` 的 flag 比較都會變成可讀名稱。

如果是多位元 flag 組合（`0x5` = `FLAG_READONLY | FLAG_SYSTEM`），要用 `Ctrl+數字`（`Ctrl+1` 表示 operand 1），IDA 會同時顯示所有命中的 flag。

## Hide casts / unsigned / 其他顯示選項

`Ctrl+P` 打開 Hex-Rays options，這幾個建議打開：

- **Hide casts**：`(_QWORD *)(a1 + 0x18)` 變成 `a1->field18`，大量減少視覺噪音。
- **Auto rename all numbers to hex**：全 hex 顯示，位址 / 常數一致。
- **Use recursive struct offset expressions**：遇到 nested struct 顯示成 `a->b->c` 而不是 `*(&a + 0x20)`。

這些設定存在 `hexrays.cfg`，所以換 IDB 也會帶過去。

## Tab 的雙向跳：其實很好用

`Tab` 在 disasm ↔ pseudocode 間切換，而且 **維持同一個 statement 的位址**。用途：

- 看 pseudocode 看到可疑計算，懷疑 Hex-Rays 推錯 → `Tab` 去 disasm 對 raw 指令。
- 在 disasm 找到某段 code 但看不出在做什麼 → `Tab` 切回 pseudocode 看 Hex-Rays 怎麼解讀。

## 常見踩雷

- **F5 按了沒反應**：可能你的 IDA 授權沒包 decompiler，或你在看的架構 Hex-Rays 不支援（例如某些 MIPS 變體、早期 RISC-V 僅 9.x 後期才支援）。
- **改型別後偽代碼變得更怪**：大概是 prototype 寫錯或 calling convention 錯。按 `Y` 重填，或 undo（`Ctrl+Z` 在 pseudocode 是一層一層回退）。
- **某個變數一直改不了名**：那可能不是 LVAR 而是 global — 要跳到 disasm 改，或用 Names window (`Shift+F4`)。
- **pseudocode 顯示 `; low-level reason ...`**：Hex-Rays 放棄的地方，通常是 obfuscation 或奇怪的 stack pivoting。要進 microcode（Ch 13）或改手分析。

## 動手練習

拿 Ch 2 練過的那個 binary：

1. 在主要 function 按 F5，開 pseudocode。
2. 選 **3 個** 你覺得意義不明的 LVAR，試著從上下文推測它們是什麼，按 `Y` 給它們正確型別。
3. 選 **1 個** 看起來像 struct pointer 的 LVAR，在 Local Types 裡寫一個可能的 struct 定義，套上去看偽代碼變化。
4. 開 `Ctrl+P`，勾選 hide casts，看同一個 function 乾淨多少。

## 自我檢核

- [ ] 能用 `Y` 改 function prototype 和 LVAR 型別
- [ ] 能用 `N` 改 LVAR 名字
- [ ] 知道 `Shift+L` 合併同源變數
- [ ] 知道 `Tab` 在 disasm 和 pseudocode 間跳
- [ ] 知道 `Ctrl+P` hide casts

下一章從靜態跳到動態 — 讓 IDA 跑起來，當 debugger 用。

→ [Ch 4 動態 debug 情境快捷鍵](./04-debugger-hotkeys.md)
