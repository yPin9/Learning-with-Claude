# Ch 34 — Simulation：辨識與實作框架

> 目標：建立 Simulation 題型的辨識能力，能把「一步一步執行規則」的問題轉成穩定的 code。

## 什麼是 Simulation？

Simulation（模擬）類題：題目本身就描述了一套規則，你的任務是**忠實地實作這套規則**，不需要特別的演算法技巧。

難點不在演算法，在：
- 邊界條件和特殊情況
- 索引計算（螺旋矩陣、對角線）
- 多步驟狀態更新的順序

## 辨識 Simulation 題

這些關鍵字暗示你需要 Simulation：

- 「按規則執行 N 步」
- 「螺旋 / 對角線 / 之字形」遍歷
- 「模擬遊戲 / 生命遊戲」
- 「旋轉矩陣 / 翻轉字串」

沒有特別的資料結構技巧，就是把題目說的做一遍。

## 題目 1：Spiral Matrix（LeetCode 54）

**題目**：按螺旋順序輸出矩陣所有元素。

**方法**：維護四個邊界（top, bottom, left, right），每走完一圈就縮小邊界。

```cpp
vector<int> spiralOrder(vector<vector<int>>& matrix) {
    vector<int> result;
    int top = 0, bottom = matrix.size() - 1;
    int left = 0, right = matrix[0].size() - 1;

    while (top <= bottom && left <= right) {
        // 左 → 右
        for (int j = left; j <= right; j++) result.push_back(matrix[top][j]);
        top++;
        // 上 → 下
        for (int i = top; i <= bottom; i++) result.push_back(matrix[i][right]);
        right--;
        // 右 → 左（要確認 top <= bottom）
        if (top <= bottom) {
            for (int j = right; j >= left; j--) result.push_back(matrix[bottom][j]);
            bottom--;
        }
        // 下 → 上（要確認 left <= right）
        if (left <= right) {
            for (int i = bottom; i >= top; i--) result.push_back(matrix[i][left]);
            left++;
        }
    }
    return result;
}
```

注意：第三和第四步要加邊界檢查，否則在奇數行 / 列時會重複輸出。

## 題目 2：Game of Life（LeetCode 289）

**題目**：Conway's Game of Life，根據規則更新細胞狀態（活 / 死）。必須同時更新（不能用第一個細胞的新狀態來算第二個）。

**技巧**：用額外的狀態值記錄「舊狀態」：
- `2`：原來活，現在要死
- `-1`：原來死，現在要活

這樣就能在原地更新，不用額外陣列。

```cpp
void gameOfLife(vector<vector<int>>& board) {
    int m = board.size(), n = board[0].size();
    vector<vector<int>> dirs = {{-1,-1},{-1,0},{-1,1},{0,-1},{0,1},{1,-1},{1,0},{1,1}};

    auto countLiveNeighbors = [&](int r, int c) {
        int count = 0;
        for (auto& d : dirs) {
            int nr = r + d[0], nc = c + d[1];
            if (nr >= 0 && nr < m && nc >= 0 && nc < n)
                if (abs(board[nr][nc]) == 1) count++;  // 1 或 -1 都代表原來活
        }
        return count;
    };

    for (int i = 0; i < m; i++) {
        for (int j = 0; j < n; j++) {
            int live = countLiveNeighbors(i, j);
            if (board[i][j] == 1 && (live < 2 || live > 3))
                board[i][j] = -1;  // 活 → 死（暫時標記）
            else if (board[i][j] == 0 && live == 3)
                board[i][j] = 2;   // 死 → 活（暫時標記）
        }
    }

    for (int i = 0; i < m; i++)
        for (int j = 0; j < n; j++)
            board[i][j] = board[i][j] > 0 ? 1 : 0;  // 最終化
}
```

## 題目 3：Rotate Image（LeetCode 48）

**題目**：原地旋轉 n×n 矩陣 90 度（順時針）。

**技巧**：先上下翻轉，再沿主對角線轉置。

```cpp
void rotate(vector<vector<int>>& matrix) {
    int n = matrix.size();
    // Step 1: 上下翻轉
    for (int i = 0; i < n/2; i++)
        swap(matrix[i], matrix[n-1-i]);
    // Step 2: 對角線轉置
    for (int i = 0; i < n; i++)
        for (int j = i+1; j < n; j++)
            swap(matrix[i][j], matrix[j][i]);
}
```

為什麼這等於旋轉 90 度？可以用小矩陣手動驗證。

## Simulation 的通用技巧

1. **先在紙上走一遍**：Simulation 最怕邊界條件，先手動跑幾個例子
2. **加 helper function**：把複雜的操作（如計算鄰居、檢查邊界）抽成函式
3. **狀態編碼**：需要同時更新時，用額外狀態值（-1, 2 等）標記「將要改變」
4. **四個邊界法**：螺旋 / 方向移動問題，維護上下左右邊界

## 自我檢核

- [ ] 能說出 Spiral Matrix 為什麼第三四步要加額外邊界檢查
- [ ] 能解釋 Game of Life 為什麼用 `-1` 和 `2` 而不直接改值
- [ ] 能說出旋轉矩陣的兩步法（上下翻轉 + 對角線轉置）
- [ ] 知道 Simulation 的難點在邊界條件，不在演算法

→ [Ch 35 State Machine：字串解析、遊戲邏輯](./35-state-machine.md)
