# Ch 22 — 白方：Italian Game

> 目標：把 Italian Game 當白方主力開局。懂最初 10 步的走法和邏輯、知道對方幾種常見應法的對策。**不是背主變，是懂「為什麼」**。

本章給你一個**實戰白方武器**。選 Italian Game 不是因為它最強（它溫和但可靠），而是因為：

1. 結構清楚、容易理解
2. 戰術機會豐富（常變成 Evans Gambit、Two Knights 等攻擊變化）
3. 不需背深主變 — 到第 8-10 步對手多半脫譜，你靠原則繼續走
4. 覆蓋白方 `1.e4 e5` 系的最常見對手應法

## Italian Game 的最初 3 步

```
1. e4 e5 2. Nf3 Nc6 3. Bc4
```

走完後：

```
  a b c d e f g h
8 r . b q k b n r  8
7 p p p p . p p p  7
6 . . n . . . . .  6
5 . . . . p . . .  5
4 . . B . P . . .  4
3 . . . . . N . .  3
2 P P P P . P P P  2
1 R N B Q K . . R  1
  a b c d e f g h
```

## 為什麼選這三步

- **1. e4** — 占中心、打開 Bf1 和 Qd1 對角線
- **2. Nf3** — 發展馬、威脅黑 e5、準備易位
- **3. Bc4** — 發展象到最活躍格、威脅 f7 弱點（黑方 f7 只有黑王守著）

`Bc4` 的象瞄準 f7 — 這是黑方最脆弱的兵（開局只有黑王守 f7）。很多 Italian 戰術都圍繞 **f7 攻擊**。

## 黑方常見應法 + 你的對策

### A. 黑方走 `3...Bc5`（Giuoco Piano，義大利慢開局）

黑方 symmetric 回應。這是 Italian 的主線。

**你（白方）接續**：

```
4. c3    (準備 d4 推中央)
4...Nf6
5. d4    exd4
6. cxd4  (中央兩個兵開局面)
```

這樣走白方取得 **中央兩個兵** + **活躍子位**。

### B. 黑方走 `3...Nf6`（Two Knights Defense）

黑方反攻 e4。這是非常常見的激進回應。

**你的兩個選擇**：

**選擇 1（穩健）**：`4. d3`（保中央 + 慢發展）
**選擇 2（激進）**：`4. Ng5!?`（威脅 Nxf7 — Fried Liver Attack，如果黑方 4...d5 那麼 5.exd5 Nxd5? 6.Nxf7! 白方大優）

**建議**：學階段先走 `4. d3` 穩健路線。Fried Liver 需要精確算度，失誤就虧。

### C. 黑方走 `3...Bc5` 但之後亂走

黑方不按譜走（業餘常見）。**你就照原則推**：

- 把 Nc3 發展出來
- Bc4 或 Be2 配合
- O-O 易位
- 看對方弱點下手

## Italian Game 的關鍵計畫（中局想做什麼）

Italian 開局發展完後（約第 8-10 步），中局計畫選項：

1. **推 d4 開中央**（如果沒做過）
2. **取得半開放 d 列 或 c 列**
3. **攻 f7 / f6 方向**（白方傳統攻擊目標）
4. **如果黑方王易位到 g8 → 準備 Greek Gift 或 h4-h5 王翼攻擊**

## 常見錯誤（白方）

### 錯誤 1：過早 Qh5（Scholar's Mate 企圖）

第 3 步 `3.Qh5` 試 Scholar's Mate。業餘對手可能中招，但稍懂的對手 `3...Nf6 4.Qxf7+?? Kxf7` 或 `3...g6` 後你的后被攻一整盤。**別這樣下**。

### 錯誤 2：第 5 步才推 d4 卻沒準備

`4.d4` 直接推（沒先 c3 準備），黑方 `4...exd4 5.Nxd4 Nxd4 6.Qxd4` 白后早出被擾。

**正確**：先 `4.c3` 準備，然後 `5.d4` 推。

### 錯誤 3：亂發 Bxf7+

看到 f7 就想吃。沒確認**後續 sequence**導致子力連鎖（Nxg5 防守反擊等）就衝 → 送子。

## Italian Game 的代表大師

Italian Game 歷史悠久（15 世紀），義大利棋手發明，一度是主流。後期被更積極的 Ruy Lopez（`3. Bb5`）和 Sicilian 逐漸取代，但現代 top-level（如 Carlsen、Caruana）又重新使用。

**學 Italian 不代表過時** — 你的對手 rating 1000-1800 用 Italian 完全夠用。

## 建議的學習方式

1. **到 Lichess → Opening Explorer** 搜尋「Italian Game」
2. **看 Master Games 最頂端 10 盤**（Bc4 系大師對局），看到第 15 步
3. **每盤問自己**：
   - 為什麼白方這樣發展？
   - 白方什麼時候打開中央？
   - 白方怎麼攻王？

4. **自己下 Italian 10 盤**（對 Lichess 電腦或真人），每盤用完整 Italian 開局走法

## 動手練習

**題**：Italian Game 第 4 步後，黑方走 `4...Bg4`（pin 白馬）。白方對策？

```
  a b c d e f g h
8 r . . q k . n r  8
7 p p p . . p p p  7
6 . . n . . . . .  6
5 . . b . p . . .  5    <- 黑象 c5（假設 Giuoco Piano 的 3...Bc5）
4 . . B . P . b .  4    <- 黑象 g4 pin Nf3 到 Qd1
3 . . . . . N . .  3
2 P P P P . P P P  2
1 R N B Q K . . R  1
```

<details>
<summary>答案</summary>

黑方 `Bg4` pin f3 馬到 d1 后。應對：

**選項 1**：`5. h3` — 威脅 h2 兵踢象。黑方要麼 `5...Bxf3 6.Qxf3`（黑放棄雙象換子發展 — 白有 bishop pair）要麼 `5...Bh5` 退象（然後白方推 `g4` 進一步擾、但這弱化王翼）。

**選項 2**：`5. c3` + 準備 d4 破中央 — 不直接處理 pin，繼續計畫。

**簡單建議**：走 `5. h3` 強制黑方決定。多數業餘黑方會 `5...Bxf3` 自己放棄 pin → 白賺 bishop pair。

</details>

## 自我檢核

- [ ] Italian Game 前 3 步背下來 + 懂每步為什麼
- [ ] 黑方 Giuoco Piano（3...Bc5）和 Two Knights（3...Nf6）的應對
- [ ] Italian 的中局計畫（推 d4 / 攻 f7 / 王翼攻擊）
- [ ] 常見錯誤（亂 Qh5、無準備推 d4、亂 Bxf7+）不再犯
- [ ] 實戰下過 10 盤 Italian

下一章是黑方對 `1.e4` 和 `1.d4` 的應對。黑方防禦有更多選擇，我們選**古典路線**：對 e4 用 `1...e5`（黑方 Italian）、對 d4 用 Queen's Gambit Declined（QGD）。

→ [Ch 23 黑方：對 1.e4 / 1.d4 的應對](./23-black-defenses.md)
