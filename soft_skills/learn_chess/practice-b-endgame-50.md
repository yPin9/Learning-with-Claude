# 練習 B — 殘局 50 題

> 目標：把 Part 2 學的殘局技巧全部實打一次。50 題分成 5 個主題，每主題 10 題。重點不是比戰術 puzzle 快，而是**算準每一步**。

殘局和戰術不同：戰術是找一次性組合、10 秒內看出來；殘局是**每一步都要算準**、慢下來想。這個練習的節奏刻意比練習 A 慢。

## 做題規則

1. **每題給 5 分鐘**（戰術 3 分鐘、殘局長一點）
2. **算不出來看答案** — 但看完要**自己再走一次**直到收局
3. **錯題隔天重做**
4. **不要跳過 Lichess Practice 的互動教學** — 那些比死背 position 有效

## 50 題分配

### 第 1-10 題：基本將殺（K+Q、K+R vs K）

連結：
- **lichess.org/practice/basic-checkmates** → Queen + Rook

**Focus**：
- K+Q vs K 10 步內 mate 不 stalemate
- K+R vs K 16 步內 mate、利用 opposition
- 50 步規則意識

### 第 11-20 題：K+P vs K（Opposition）

連結：**lichess.org/practice/pawn-endgames → King and Pawn**

**Focus**：
- 判斷能不能贏（key square + opposition + 誰走）
- 邊兵 a/h 列的和棋邏輯
- Critical square 的判斷

### 第 21-30 題：通路兵與 Square Rule

連結：**lichess.org/practice/pawn-endgames → Passed Pawns**

**Focus**：
- 方形規則快速判斷
- 輪誰走會影響方形大小
- 外圍通路兵的戰略應用

### 第 31-40 題：車兵殘局（Lucena 與 Philidor）

連結：**lichess.org/practice/rook-endgames**

**Focus**：
- 識別 Lucena 位置並執行蓋橋
- 識別 Philidor 位置並守第 3 排
- 車永遠不在自己兵前

### 第 41-50 題：化簡判斷（局面判斷）

這部分 Lichess 沒有現成分類。改成：**分析自己過去 10 盤殘局**，每一盤的關鍵交換點問「該不該化簡」。

**Focus**：
- 子力清單預想「化簡後」
- bishop pair 的殘局放大
- 避免進入 Philidor 形狀

## 追蹤表

```
K+Q / K+R mate          [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
K+P vs K opposition     [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
Passed pawn / Sq rule   [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
Lucena / Philidor       [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
Simplification judgment [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
```

## 做完後的 debrief

回答這幾個問題：

1. **第幾題卡超過 5 分鐘？** — 那題是你的弱點類型，多做
2. **有沒有哪種殘局形狀你反覆走錯？** — 把那個 position 記下來，打印到牆上
3. **K+R vs K 你穩定 20 步內 mate 嗎？** — 不穩定就繼續做
4. **Lucena 的蓋橋你會嗎？** — 不會直接重讀 Ch 14 + Lichess Practice

## 評估標準（過關標準）

- 五個主題每個至少 7/10 一次到位
- K+Q 10 步、K+R 20 步、Lucena/Philidor 互動練習一次過
- 面對 K+P vs K 局面能**立刻**判斷贏 / 和

達到 → 進 Part 3。達不到 → 重刷一輪。

## 期待值

做完這 50 題後你會發現：**實戰殘局你的判斷開始超越許多 rating 相當的對手**。戰術的差別大家差不多，殘局才是真正分水嶺。中階業餘棋手的棋力差異有一半是殘局差異。

**殘局 pattern 的複利價值最高**：一次學會 Lucena，用一輩子。

## 自我檢核

- [ ] 完成 50 題並做過 debrief
- [ ] 錯題重做到錯誤率降到 10% 以下
- [ ] K+R vs K 能穩定 20 步內
- [ ] Lucena / Philidor 能識別並執行
- [ ] 能判斷 K+P vs K 任意局面的勝和

下一關進 Part 3：**局面要素與判斷**。Part 1 和 2 都是「具體招式」，Part 3 是「怎麼看盤」— 沒戰術可打、沒殘局可收時，你要能自己找計畫。

→ [Ch 16 活動度與子力協調](./16-activity-coordination.md)
