# Ch 3 — 陣列與指標的真正關係

> 目標：破除「陣列就是指標」這個最流行的錯誤認知，精確說出兩者在哪裡相同、哪裡完全不同。

## 核心：陣列不是指標

**陣列是陣列，指標是指標。** 但陣列在大多數表達式中會自動「衰退」（decay）成指向第一個元素的指標。

```c
int arr[5] = {1,2,3,4,5};
int *p = arr;   // arr 衰退為 &arr[0]，隱式轉換為 int*

sizeof(arr) == 20   // 5 * sizeof(int)：陣列的完整大小
sizeof(p)   == 8    // 64-bit 指標大小，與陣列無關
```

這個 `sizeof` 差異是面試最常考的考點之一。

---

## 衰退（Array Decay）發生的時機

陣列在以下情況**會**衰退成 `T*`：
- 出現在表達式中（賦值、算術、函式呼叫...）
- 作為函式引數

陣列在以下情況**不衰退**：
- `sizeof(arr)` → 回傳整個陣列大小
- `&arr` → 型別是 `T(*)[N]`，不是 `T**`
- `_Alignof(arr)` → 回傳對齊需求

---

## 函式參數的陷阱

```c
// 下面三個宣告完全等價，都是 int*：
void foo(int arr[100]);
void foo(int arr[]);
void foo(int *arr);

// 驗證：
void foo(int arr[100]) {
    printf("%zu\n", sizeof(arr));  // 輸出 8，不是 400！
    // arr 在函式內已是 int*，資訊丟失了
}
```

**結論：傳陣列給函式時，必須另外傳長度**：

```c
void process(int *arr, size_t n);
```

---

## `arr` vs `&arr`：型別不同，算術行為不同

```c
int arr[5];
int *p1 = arr;      // int*，等同 &arr[0]
int (*p2)[5] = &arr; // int(*)[5]，指向整個陣列

// 值相同（都是陣列起始地址）：
printf("%p %p\n", (void*)arr, (void*)&arr);  // 同一個地址

// 但算術行為完全不同：
arr + 1              // 移動 sizeof(int) = 4 bytes
&arr + 1             // 移動 sizeof(int[5]) = 20 bytes！
```

經典考題：

```c
int a[5] = {1,2,3,4,5};
int *p = (int *)(&a + 1);
printf("%d\n", *(p - 1));   // 答：5
// &a+1 越過整個陣列（+20 bytes），轉成 int* 後 -1 = a[4] = 5
```

---

## 多維陣列

```c
int mat[3][4];
// mat 的型別：int (*)[4]（指向一個含 4 int 的陣列）
// mat[0] 的型別：int*（第 0 列）

// 傳多維陣列：必須指定第一維以外的所有維度
void foo(int mat[][4], int rows);   // OK
void foo(int (*mat)[4], int rows);  // 等價，更清楚
// void foo(int mat[][], int rows); // 錯誤：列大小未知，無法算 mat[i] 的偏移
```

記憶佈局（row-major）：

```
mat[0][0] mat[0][1] mat[0][2] mat[0][3]
mat[1][0] mat[1][1] mat[1][2] mat[1][3]
mat[2][0] mat[2][1] mat[2][2] mat[2][3]
→ 在記憶體裡是連續的 12 個 int
```

`mat[i][j]` 的位址 = `(char*)mat + (i*4 + j) * sizeof(int)`。

---

## 字元陣列 vs 字元指標

```c
char arr[] = "hello";   // 陣列，"hello" 複製到 stack/data
char *p    = "hello";   // 指標，指向 text segment 的常數

arr[0] = 'H';    // OK，arr 是可改的記憶體
p[0]   = 'H';    // UB！p 指向唯讀的 text segment

sizeof(arr) == 6   // 含 '\0'
sizeof(p)   == 8   // 指標大小
```

**面試記憶點**：`char *p = "..."` 是古老的寫法，現代 C 應寫 `const char *p = "..."`，讓編譯器幫你抓寫入錯誤。

---

## VLA（Variable Length Array，C99）的陷阱

```c
void foo(int n) {
    int arr[n];   // VLA：大小在執行期決定，分配在 stack
}
```

VLA 的問題：
1. n 太大直接 stack overflow，沒有任何錯誤訊息
2. 無法靜態分析 stack 使用量（嵌入式禁用原因）
3. C11 起成為可選功能，嵌入式工具鏈常不支援
4. `sizeof(arr)` 變成執行期操作（不是編譯期常數）

嵌入式和系統職位面試常問：「為什麼不用 VLA？」回答要點在上面。

---

## 自我檢核

- [ ] `sizeof(陣列)` 和 `sizeof(指標)` 的差異
- [ ] 陣列衰退的三個「不衰退」例外（sizeof、&、_Alignof）
- [ ] 函式接收陣列參數為什麼要另傳長度
- [ ] `arr + 1` 和 `&arr + 1` 移動距離的差異
- [ ] `char arr[] = "hello"` 和 `char *p = "hello"` 的本質差異

→ [Ch 4 struct / union / bitfield 記憶體佈局](./04-struct-union-bitfield.md)
