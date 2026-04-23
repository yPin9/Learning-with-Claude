# Ch 5 — Struct / enum / type 還原情境

> 目標：看到一堆 `[rbx+0x18]` 能快速還原成 struct 欄位；用 Local Types editor 寫 C 宣告、套回 decompiler。

## 為什麼 type recovery 是逆向的核心

沒還原 struct 的偽代碼：

```c
v3 = *(_QWORD *)(a1 + 0x18);
if ( *(_DWORD *)(v3 + 0x8) > 0x10 )
  *(_BYTE *)(v3 + 0xC + *(_DWORD *)(v3 + 0x8)) = 0;
```

還原後：

```c
buf = self->buffer;
if ( buf->len > 16 )
  buf->data[buf->len] = 0;
```

**這不是美學差異，是能不能看懂的差異**。中等以上複雜度的 binary，不還原 struct 基本等於沒分析。

## IDA 9.x 的 type 系統：Local Types 為王

9.x 之前的舊教材會說：

- `Shift+F9` 開 Structures
- `Shift+F10` 開 Enums
- 各自有自己的 editor

**9.x 全廢了**，統一進 **Local Types**（`Shift+F1`）。所有 struct、union、enum、typedef 都在同一個 window。

```
┌────── Local Types (Shift+F1) ──────┐
│                                    │
│  struct Object {                   │
│    int ref_count;                  │
│    Array *arr;                     │
│    char name[32];                  │
│  };                                │
│                                    │
│  enum FileFlags { ... };           │
│                                    │
│  typedef uint64_t HandleID;        │
│                                    │
│  union Variant { ... };            │
│                                    │
└────────────────────────────────────┘
```

編輯方式：

- **新增**：`Insert` 鍵（或右鍵 → `Add new type`）。
- **編輯**：點一下，按 `F2`。
- **刪除**：`Delete`。
- **匯入 C header**：`File → Load file → Parse C header file`（整個 `.h` 吃進來）。

## 從 offset 推 struct 的流程

看到一個 function 裡大量 `[rbx+X]` 存取：

```asm
mov rax, [rbx+0x0]
mov ecx, [rbx+0x8]
mov rdx, [rbx+0x10]
movzx r8d, byte ptr [rbx+0x18]
lea rdi, [rbx+0x19]
```

人工推：

| offset | 大小 | 推測 type |
|---|---|---|
| 0x0  | 8 | pointer？（`mov rax`，可能 pointer / int64） |
| 0x8  | 4 | `int32_t`（`mov ecx`） |
| 0x10 | 8 | pointer / int64 |
| 0x18 | 1 | `char` / `bool`（movzx byte） |
| 0x19 | ? | `lea rdi`，像是 inline array 開頭 |

組出來：

```c
struct Thing {
  void *ptr_0;       // 0x00
  int32_t count;     // 0x08
  _QWORD field_10;   // 0x10  — 暫時叫這個，後面再細看
  bool flag;         // 0x18
  char inline_buf[]; // 0x19 — flexible array
};
```

按 `Shift+F1` 進 Local Types，`Insert` 新增、貼上去 save。回到 disasm / pseudocode，在 `rbx` 變數上按 `Y` 填 `Thing *`，上面那段會立刻變：

```c
self->ptr_0 = ...;
self->count = ...;
self->field_10 = ...;
self->flag = ...;
ptr = self->inline_buf;
```

一次打磨完。

## 套用 struct 的三種場景

### 1. Register / LVAR 是 struct pointer

游標在 pseudocode 的變數上 `Y` 填 `MyStruct *`。Ch 3 已示範。

### 2. Stack 上的 inline struct

一個 function 在 stack 上配置 struct：

```c
int v3;
int v4;
int v5;
int v6;
```

一看就是連續 4 個 int 欄位（`[rbp-0x10]`、`[rbp-0xC]`、...）。做法：

1. `Ctrl+K` 開 stack frame window。
2. 在第一個 local var 上 `Y` 填 `MyStruct` — 不是 `MyStruct *`，這是 inline 配置。
3. 後面重疊的 vars 會自動被吃進 struct。

### 3. disasm 某個 operand 要套 struct offset

游標停在 `[rbx+0x18]` 的 `0x18` 上，按 `T` — 彈出候選的 struct（所有在 Local Types 裡定義過、剛好有 0x18 offset field 的）— 選一個，`0x18` 立刻變成 `[rbx+Thing.flag]`。

**這招在 disasm 看組語時非常省腦**，比一直對 offset 表快。

## 從已知 til 撈型別

til（Type Information Library）是 IDA 預裝的型別資料庫：C 標準庫、Win32 API、POSIX、Boost、.NET、etc.

`Shift+F11` 開 Type Libraries，上面列已載入的 til。沒載到的：`Insert` 鍵新增，選 `mssdk_win7`、`gnulnx_x64` 等。

載入之後，你在 `Y` 輸入 `FILE *` / `HANDLE` / `stat` / `pthread_mutex_t` 都能吃。**別重造輪子自己定義標準 type**。

## Parse C header：外部 API 成批匯入

拿到一個閉源 library 的 `.h` 檔：

```
File → Load file → Parse C header file
```

整個 header 的所有 `struct` / `typedef` / `enum` 都會進 Local Types。之後就能直接套用。

**踩雷**：header 用了你沒有的 macro（`#define EXPORT __declspec(dllexport)`）會 parse 失敗。要先清掉或預先 `#define` 掉。

## 反向操作：從 struct 匯出 C

右鍵 Local Types 某個 type → `Dump to file`，匯出整個 type 成 C 宣告。可以貼進 PoC 程式碼直接用。

## Enum：把 magic number 變人話

flag 值、error code、state machine value — 都應該變成 enum，不然偽代碼會一堆 `if (x == 3)`。

流程：

1. `Shift+F1` 新增 enum：

```c
enum CmdType {
  CMD_HELLO    = 0,
  CMD_PING     = 1,
  CMD_UPLOAD   = 2,
  CMD_EXEC     = 3,
  CMD_EXIT     = 99,
};
```

2. 在 pseudocode 的數字 `3` 上按 `M`（或右鍵 → `Symbolic constant`）→ 選 `CmdType::CMD_EXEC`。
3. 之後那個位置顯示 `CMD_EXEC` 而不是 `3`。

**bitmask 組合**：如果是 `x = 0x5`（`READONLY | SYSTEM`），按 `M` 只能選單一值。要同時套多 bit：看當前 operand index（`Ctrl+1` 是 operand 1），或直接編輯當前行，IDA 會自動組合。

## 常見踩雷

- **struct 套上去偽代碼變得更亂**：size / alignment 錯了。檢查每個 field 的 type size 是否正確，packed 結構要用 `#pragma pack(1)` 或 Local Types 的 `__packed__` attribute。
- **union 忘了用**：遇到同一個 offset 在不同場景存不同 type（例如 `tagged union`），請用 `union`，不是各寫一個 struct。
- **nested struct 顯示成 `*(int*)((char*)&foo + 0x20)`**：`Ctrl+P` 打開 `Use recursive struct offset expressions`。
- **改了 struct 但偽代碼沒更新**：按 `F5` 重開 pseudocode window 強制重算。極少數需要 `Edit → Other → Force analysis`。

## 動手練習

找一個有用到某 object 的 function（典型：parser、protocol handler）：

1. 列出所有 `[reg+offset]` 存取，整理成表格。
2. 猜每個 offset 的 size 和 type。
3. 在 Local Types 寫出 struct。
4. 套用到 pseudocode，看打磨後結果。
5. 至少留一個 `field_XX` 的欄位（意義不明的），當成 TODO — 真實逆向你不會一次猜完所有 field。

## 自我檢核

- [ ] 知道 9.x 用 Local Types (`Shift+F1`) 取代舊的 Structures / Enums
- [ ] 能從 `[reg+offset]` 模式推出 struct layout
- [ ] 會用 `T` 把 operand offset 套成 struct member
- [ ] 會用 `M` 把 magic number 套成 enum
- [ ] 知道 til 有 Win32 / POSIX 標準型別可直接用
- [ ] 會 Parse C header file 匯入整批 type

下一章把前五章的所有招式，依 CTF / malware / vuln / firmware 四大題材重組成速查頁。

→ [Ch 6 四大題材速查：CTF / malware / vuln / firmware](./06-scenario-cheatsheet.md)
