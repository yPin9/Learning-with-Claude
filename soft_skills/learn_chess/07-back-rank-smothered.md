# Ch 7 — Back Rank 與 Smothered Mate

> 目標：認得 back rank mate 的核心 pattern（王被自己兵悶住 + 車/后沖底排）和 smothered mate 的核心 pattern（王被己方子完全包圍 + 馬跳進去將軍）。這兩個 pattern 在所有 rating 都會不斷重複出現。

這章講兩個**幾何形狀極度固定**的 mate pattern。固定到你看一次就能記住、看五次就能預判對手會不會中。Back rank 尤其高頻 — Lichess 1200 以下的棋，每四盤就有一盤有人 back rank mate 或被 back rank mate。

## Back Rank Mate（底排殺）

### 核心 pattern

棋局來到中局後期，雙方王已經易位。黑王在 g8，三個兵還在 f7 / g7 / h7 沒動（開局後就沒動過）。黑方底排（第 8 排）空曠。白方有一個車或后能沖到底排 → 黑王**想跑但 f7/g7/h7 被自己的兵擋住** → checkmate。

### 最乾淨例子

```
  a b c d e f g h
8 . . . . . . k .  8    <- 黑王 g8
7 . . . . . p p p  7    <- 黑兵在 f7、g7、h7
6 . . . . . . . .  6
5 . . . . . . . .  5
4 . . . . . . . .  4
3 . . . . . . . .  3
2 . . . . . . P P  2
1 . . . . . . . K  1    <- 白王 h1
      白車在哪邊準備好沖底排
```

假設白車在 d1 或 e1，黑方底排沒任何防守子。白走 **Rd8#** 或 **Re8#**：車到 d8/e8 將軍黑 g8 王，黑王的逃格：

- f8：被 d8/e8 的車沿第 8 排控制 → 不能去
- h8：同理被控制（如果車到 e8 或 h8 方向）
- f7：被自己兵擋
- g7：被自己兵擋
- h7：被自己兵擋

**王無處可逃、車也沒被保護者吃、沒其他子能擋** → checkmate。

### Luft（開窗）：防 back rank 的動作

預防 back rank mate 只要一步：**把王前的兵推一格**，創造一個逃口（德文 Luft = 空氣）。

```
  a b c d e f g h
8 . . . . . . k .  8
7 . . . . . p . p  7
6 . . . . . . p .  6    <- 黑方走了 g7-g6，王現在能逃 g7
```

走 `g7-g6` 或 `h7-h6` 都可以開 luft。**什麼時候該開？**

- 底排沒有自己的車 / 后在防守時
- 對方有能衝底排的子（車、后）
- 沒有其他更急迫的威脅

**新手常犯錯**：從頭到尾不推任何王翼兵 → 整盤 back rank 都漏。養成習慣：**看到中局自己底排沒車守 → 推一步 h3 / h6 開窗**。

### Back rank 的變形：不只是純 mate

很多局面不是直接 back rank mate，而是**「威脅 back rank mate」成為戰術槓桿**。例子：

對方要吃我的后，但我有一個車控制對方底排。我直接**放棄后**或**做其他事** — 因為對方的后或車不能離開防守 back rank 的位置，否則我 Rxe8#。**對方的防守子被 back rank pinned**。

這就是 Ch 6 overloaded piece 的變形：**對方底排守備子 overloaded**，無法同時兼顧底排和救其他東西。

## Smothered Mate（悶殺）

### 核心 pattern

**王被自己的子完全包圍**（可以是兵、車、象等），而**敵方馬**跳進去將軍。馬將軍的特點：**不能被擋**（馬跳過其他子）。王也跑不掉（被自己子包住）→ 唯一解是吃那個馬，吃不到 → mate。

### 最簡形狀（Philidor's Legacy 結尾）

```
  a b c d e f g h
8 . . . . . . r k  8    <- 黑車 g8、黑王 h8
7 . . . . . N p p  7    <- 白馬 f7 將軍
6 . . . . . . . .  6
...
```

白馬 f7 攻 h8 王（f7 到 h8 file 2、rank 1 ✓ 馬步）。黑王 h8 的逃法：

- `Kg8`：**自己車占著**，不能
- `Kh7`：**自己兵占著**，不能
- **吃馬 Kxf7**：h8 到 f7 距離 2 格、王只能走一格 → 不能
- **擋**：馬將不能擋
- **其他黑子吃 f7 馬**：
  - 黑車 g8：沿 rank 8 或 file g 動，**到不了 f7**（f7 在 rank 7、file f；g8 沿 rank 8 最近是 f8，沿 file g 最近是 g7 黑兵擋）
  - 黑兵 g7：斜攻 f6 和 h6，不攻 f7
  - 黑兵 h7：斜攻 g6，不攻 f7
  - 沒其他黑子

**mate**。王被自己兵 + 自己車完全包圍 → 馬跳進來一錘定音。這就是 smothered。

**關鍵幾何特點**：黑 g8 必須是**車或其他非后非象的子**（如果是后或象可能能沿對角吃 f7 馬），且 g7、h7 兵位置不能斜攻 f7（的確不攻）。

### Philidor's Legacy：最著名的 smothered mate 組合

1789 年的經典組合。**Q + N 配合完成 smothered mate**，前提是對方王翼被兵悶住。

步驟（白方打黑王）：

1. **Nf7+**（馬跳到 f7 將軍黑 g8 王）— 如果黑王已經在 h8 這步不用，直接 Qg8 線；如果王在 g8，被將軍只能 Kh8（h8 通常空的）
2. **Nh6+ +（雙重將軍）**（白馬從 f7 跳到 h6 閃擊將軍 — 雙重將因為馬將軍 + 某線性子也將）→ 黑王必須動，Kg8 唯一逃格
3. **Qg8+**（白后棄到 g8 將軍）— 黑方**唯一救法是 Rxg8**（黑車吃后；王吃不到，因為黑車原本就在 g8 後面排隊的關係，王還在 h8）
4. **Nf7#**（黑車 Rxg8 之後，g8 現在是黑車、黑王還在 h8、h7/g7 被兵擋。白馬從 h6 跳 f7 將軍 h8 王 → smothered mate）

整個 sequence 叫 Philidor's Legacy，名字來自 18 世紀棋手 Philidor。這是**新手必看一次**的 pattern — Lichess 中階 puzzle 常出。

### Smothered Mate 的核心條件 check

看到對方王在角落 + 己方子包圍 + 王翼兵沒動，就想「有馬能跳過去嗎？」

**典型形狀**：

- 王在 h8、自己的車/子在 g8、自己的兵在 g7/h7、我方馬能到 f7
- 或：王在 a8、車在 b8、兵在 a7/b7、我方馬能到 c7

這兩個角落 pattern 是最高頻版本。

## 兩個 mate 的防守共通點

兩個都是**王自己被擋死**造成。預防方式：

- **Back rank**：開 luft（推 g/h 兵一格）
- **Smothered**：不讓自己的車或子堵死 g8/h8 周圍；或在王角落時注意對方馬能不能跳進 f7/g6

**更根本的預防**：中局開始時對自己王的空氣保持敏感。不要把自己的後備兵全悶死、不要把車一直擺在 g8/h8 堵王。

## 動手練習

**題 1**：黑方要走。能用 `Rd1+` 強迫 back rank mate 嗎？

```
  a b c d e f g h
8 . . . . . . k .  8    <- 黑王 g8
7 . . . . . p p p  7
6 . . . . . . . .  6
5 . . . . . . . .  5
4 . . . . . . . .  4
3 . . . . . . . .  3
2 . . . r . . P P  2    <- 黑車 d2、白兵 g2/h2
1 . . . . . . . K  1    <- 白王 h1
  a b c d e f g h
```

<details>
<summary>答案</summary>

黑走 **`Rd1+`**：車從 d2 沿 d 列到 d1，沿 rank 1 將 h1 王。

白王 h1 逃法：
- g1：被 d1 車沿 rank 1 攻 → 不能
- h2：白兵 h2 擋 → 不能
- g2：白兵 g2 擋 → 不能
- Kxd1 吃車：h1 到 d1 距離 4 → 不能

**擋**：d1-h1 中間 e1/f1/g1 空，但沒白子能跳過來擋。
**吃**：沒白子能攻 d1。

**Checkmate**。白方被自己的 g2/h2 兵悶死。典型 back rank — **對方王前兵沒動 + 你有車沖底排 + 對方底排無守備**。

</details>

**題 2**：白方要走，找 smothered mate 的一步。

```
  a b c d e f g h
8 . . . . . . r k  8    <- 黑車 g8、黑王 h8
7 . . . . . . p p  7    <- 黑兵 g7、h7
6 . . . . . . . .  6
5 . . . . N . . .  5    <- 白馬 e5
4 . . . . . . . .  4
3 . . . . . . . .  3
2 . . . . . . . .  2
1 . . . . . . . K  1
  a b c d e f g h
```

<details>
<summary>答案</summary>

**`Nf7#`**。

驗證：e5→f7 file 1、rank 2 ✓ 馬步。f7 攻 h8（file 2、rank 1 ✓ 馬步）→ 將軍 h8 王。

黑王 h8 逃法：
- **Kg8**：自己車占 → 不能
- **Kh7**：自己兵占 → 不能
- **Kxf7 吃馬**：距離 2 格 → 不能

**其他黑子救**：
- g8 車沿 file g 被 g7 兵擋、沿 rank 8 到不了 f7
- g7 兵斜攻 f6/h6，不攻 f7
- h7 兵斜攻 g6，不攻 f7

**Smothered mate ✓**。這就是 Philidor's Legacy 結尾的 pattern。

</details>

## Lichess 訓練

做：
- **lichess.org/training/backRankMate** 30 題
- **lichess.org/training/smotheredMate** 15 題

Smothered 比較少見，puzzle 庫不那麼多。Back rank 無限做。

## 自我檢核

- [ ] Back rank mate 的 3 要素：車/后到底排、王被己方兵悶、底排無防守
- [ ] Luft（開窗）是標準預防動作，中局該推就推
- [ ] Smothered mate 的馬將不可擋，要靠王自己被己方子悶死
- [ ] Philidor's Legacy 的 4 步 sequence（Q+N）看過一次
- [ ] 完成 30 題 back rank + 15 題 smothered puzzle

下一章把前面 5 章講的戰術（fork / pin / skewer / discovered / deflection / back rank）**組合**成典型將殺圖案 — Anastasia、Arabian、Boden、Greek Gift 等 7-8 個 named mate pattern。這些是戰術識別的「完成狀態圖」。

→ [Ch 8 核心將殺圖案](./08-mating-patterns.md)
