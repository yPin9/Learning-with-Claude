# Ch 12 — 王兵殘局：Opposition

> 目標：搞懂 opposition（王對王）和 key squares（關鍵格）兩個概念。用這兩個工具判斷 K+P vs K 殘局能不能升變 — 同樣的局面，**輪誰走**會決定贏還是和。

K+P vs K（白方有王和一個兵、黑方只剩王）是**殘局最基本的學習標的**。看起來簡單：白方多一個兵、推到底升變成后就贏了。**實際上 50% 的 K+P vs K 局面是和棋**，因為黑王能卡住白兵升變路。會不會判斷這 50%，是會走殘局和不會走殘局的分界。

## Opposition 的嚴格定義

**兩個王在同一直線（file / rank / diagonal）上、中間隔一個空格，且現在要走的是對方 → 你有 opposition**。

輪對方走的那方必須退讓（規則上王不能相鄰，對方只能走到不是「雙王距離 1 格以下」的格子）。

### Direct Opposition（直接對應）

兩王中間隔 **1 個空格**（總距離 2 格）。

```
  a b c d e f g h
6 . . . k . . . .  6    <- 黑王 d6
5 . . . . . . . .  5    <- 中間 d5 空
4 . . . K . . . .  4    <- 白王 d4
```

同 file、中間一格 → direct opposition。**輪對方走的那方被迫退**。

### Distant Opposition（遠距對應）

兩王中間隔 **3 個空格**（總距離 4 格）或 **5 個空格**（6 格），依然算 opposition。**關鍵是距離為偶數**（你跟對方間奇數格的對應永遠是你握 opposition — 因為對方要走但不能直接進 opposition 得花兩步，你第二步可以轉為直接 opposition）。

實戰白方想推兵上去升變時，「先用 distant opposition 逼退黑王」→ 推兵。

### 誰「擁有」opposition

**要走的那方「失去」opposition**。也就是說，局面對稱但**對方輪**時，你有 opposition。

這也是 opposition 的奧妙：**誰該走決定局面**，**不是誰走先贏**。殘局「輪誰走」（常被記為 *to move*）是頭等大事。

## Key Squares（關鍵格）

K+P vs K 能不能贏看一件事：**白王能不能先占到一個 key square**。

### 兵在 c 到 f 列（中央兵）的 key squares

對於中央兵，key squares 是 **兵前方三格**（兵在第 5 排時 key squares 在第 6 排；兵在第 6 排時 key squares 在第 7 排）。

```
  a b c d e f g h
8 . . . . . . . .  8
7 . . . x x x . .  7    <- 兵在 e6 時，key squares 是 d7/e7/f7
6 . . . . P . . .  6    <- 白兵 e6
5 . . . . . . . .  5
```

**如果白王能在黑王能阻擋之前占到 e7 或 d7 或 f7 → 白贏**。黑王無論怎麼下都會被逼讓開 → 白兵升變。

### 兵在其他排的 key squares

- **兵在第 4 排或更前**：key squares 是兵前**兩排**的三格 + 兵兩側的兩格。範圍更大。
- **兵在第 7 排（就要升變）**：key squares 已經不需要，只要王守住兵不被吃就贏。

### 邊路兵（a 和 h）的例外 — **邊兵多數和棋**

a 列和 h 列的兵特別難贏。因為邊兵沒有左邊或右邊的 key square（一邊是棋盤外），黑王只要跑到角落就幾乎永遠逃得掉。

**結論**：邊兵對光王 **除非白王已經在 key square 或黑王太遠**，否則多是和棋。

## 實戰判斷：能不能贏

面對 K+P vs K 局面，你的判斷流程：

1. **找到 key squares**
2. **問：現在我的王能在黑王到前面之前占到 key square 嗎？**
3. **問：輪誰走？**
4. 推演 3-5 步後會不會落到雙方 opposition、我方兵是否能安全推到底

這個判斷是殘局的**硬功夫**，沒辦法靠戰術感覺。要做 Lichess Practice 下至少 20 盤 K+P vs K 手感才會出來。

## 經典例子：贏 / 和棋的分界

### 例 A：白贏（白王在 key square）

```
  a b c d e f g h
8 . . . . . . . .  8
7 . . . . K . . .  7    <- 白王 e7（key square！）
6 . . . . P . . .  6    <- 白兵 e6
5 . . . . k . . .  5    <- 黑王 e5
4 . . . . . . . .  4
```

輪誰走都白贏。白王守 e6 兵 + 占 e7 → 黑王衝不進來 → 白兵安全推到 e8 升變。

### 例 B：和棋（黑王在兵前面、白王沒到 key square）

```
  a b c d e f g h
8 . . . . k . . .  8    <- 黑王 e8（擋在兵前）
7 . . . . . . . .  7
6 . . . . . . . .  6
5 . . . . K . . .  5    <- 白王 e5
4 . . . . P . . .  4    <- 白兵 e4
```

輪白走：推兵？`e4-e5`？但 e5 有白王。先走王，白王走 Kf5 或 Kd5 → 黑王對應走 Kd7 或 Kf7（保持 opposition）。

這個局面**黑方可以維持 opposition 永遠** → 白兵推不過去 → 和棋。

**關鍵判斷**：開始時如果黑王已在兵前且會 opposition → 白無法占 key square → 和棋。

### 例 C：誰先走決定勝負（微妙）

規則上兩王不能相鄰，所以 opposition 至少隔 1 空格。很多 K+P vs K 局面**白先走贏、黑先走和**（或反過來）— 這就是 opposition 的精髓：走錯一步（或「不得不走」一步）就從贏變和、從和變輸。

**實戰例**：白王 d5、黑王 e7、白兵 e4。輪誰走？

- 輪白走：白方可 Kd6 或 Ke5 推進，多數能守住 e 兵並推到 e5 → 贏
- 輪黑走：黑方 Ke6 取得 opposition，白王被迫退 → 多數和棋

這種**一步之差決定結果**的局面在 K+P vs K 裡非常普遍。

## Lichess Practice

**lichess.org/practice/pawn-endgames → King and Pawn**

互動 20 題從簡到難，涵蓋 key squares 判斷、opposition 技巧。**每一題都要下到結束**，不要放棄。

## 動手練習

**題 1**：白方先走，贏還是和？

```
  a b c d e f g h
6 . . . . k . . .  6    <- 黑王 e6
5 . . . . . . . .  5
4 . . . . K . . .  4    <- 白王 e4
3 . . . . P . . .  3    <- 白兵 e3
```

<details>
<summary>答案</summary>

白 e3 兵 → key squares 是 d5/e5/f5（兵前方三格）。白王在 e4，距離 e5 一步。黑王 e6 在 e5 一步外。

**輪白走**：
- `Ke4-e5`：e5 被黑王 e6 攻（相鄰）→ 違法
- `Kd4` 或 `Kf4`：遠離 key square，黑王 Kd5 或 Kf5 搶 key square

**如果黑方先走**，`Kd5` 或 `Kf5` 阻擋。但題目是白先走，白方推王要迂迴。

這題**精確結果要算 5-6 步**。大致結果：白方白走可能取得 opposition，然後推 e4→e5 → 贏。但要走對。詳細結果**在 Lichess Practice 下一次就見真章**。

</details>

## 自我檢核

- [ ] Opposition 定義：王同線、中間隔偶數空格、對方要走
- [ ] Key squares：兵前方 3 格（中央兵）
- [ ] 邊兵通常和棋（a、h 列）
- [ ] K+P vs K 的勝負判斷流程：key square + 誰走 + opposition
- [ ] Lichess Practice K+P endgame 20 題全做一遍

下一章講通路兵（passed pawn）和 square rule — 兵沒人擋時能不能靠自己升變的幾何判斷。

→ [Ch 13 Square Rule 與通路兵](./13-square-rule-promotion.md)
