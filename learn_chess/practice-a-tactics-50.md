# 練習 A — 戰術 50 題

> 目標：把 Part 1 講的所有戰術 pattern **實打**一輪。50 題分 5 個主題，每主題 10 題。做完這一輪你的戰術 rating 會明顯跳。

這個練習**不直接給 50 道棋題**（寫死會變考古題，無限重複做反而有反效果）。改成**指定 Lichess puzzle theme 連結 + 做題規則 + 追蹤表**。Lichess puzzle DB 有 400 萬題以上，每次做都是新題。

## 做題規則（**不守規則這練習沒用**）

1. **每題限時 3 分鐘**。想不出來點提示 → 點完立刻記錄「需要提示」
2. **提示用完還想不出** → 看答案 → 把答案的 pattern 重畫一次（心裡或 Lichess Study）
3. **錯了 / 需提示的題目**標記下來，**隔天重做一次**
4. **每 5 題暫停** 30 秒，回想剛才的 pattern
5. **不要連續做超過 30 題** — pattern recognition 疲勞比體力疲勞更早來

## 50 題分配

### 第 1-10 題：Fork（雙擊）

連結：**lichess.org/training/fork**

Lichess 會依你 rating 給合適難度。做第一輪時選「Easier」（難度往下調），打 1-2 輪後調「Normal」。

**Focus**：
- 馬叉 Royal Fork 的幾何 pattern
- 兵叉的「推兵那一步」是否同時雙攻
- 后叉常見的「吃一個子 + 將軍順便」雙重威脅

### 第 11-20 題：Pin（牽制）

連結：**lichess.org/training/pin**

**Focus**：
- 絕對 pin 下「pinned piece 不保護任何東西」的利用
- 主教沿對角線 pin 的典型幾何（特別是 Bg5 pin f6 馬）
- 車沿 file 或 rank 的 pin

### 第 21-30 題：Skewer + Discovered Attack

連結：
- **lichess.org/training/skewer**（5 題）
- **lichess.org/training/discoveredAttack**（5 題）

**Focus**：
- Skewer 的「前重後輕」識別
- Discovered attack 的「擋子挪開那步順便做事」
- Double check（雙重將軍）pattern — 王必須跑、別的應法都不行

### 第 31-40 題：Deflection + Overloaded + Back Rank

連結：
- **lichess.org/training/deflection**（4 題）
- **lichess.org/training/overloadedPiece**（3 題，主題名可能叫 `overloading`）
- **lichess.org/training/backRankMate**（3 題）

**Focus**：
- Deflection 識別「哪個子必須離開關鍵格」
- Overloaded 識別「一個子同時守兩地」的瞬間
- Back rank 識別「對方王前的兵全沒動」立刻尋找沖底排可能

### 第 41-50 題：多步 Combination

連結：**lichess.org/training** → 設定 `Mate in 2` / `Mate in 3` / Medium 難度

**Focus**：
- CCT 紀律（每步先列 Checks/Captures/Threats）
- 強制走法的串接
- 評估終點局面（不是中途）

## 追蹤表（拿去填，真的）

複製下面這張表到 Lichess Study 或自己的筆記，每做完一題打勾：

```
Fork        [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
Pin         [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
Skewer/DA   [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
Defl/Over/B [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
Combo       [ ][ ][ ][ ][ ][ ][ ][ ][ ][ ]
```

**需要提示 / 錯誤的題**在格子裡畫 X，隔天一定重做。

## 做完 50 題後的 debrief（重要）

做完 50 題拿出紙筆（或文字檔），回答三個問題：

1. **哪個主題你錯最多？** — 這是你的弱點區，多做 30 題那個主題的補強
2. **你看錯過哪個 pattern 超過 3 次？** — 把這個 pattern 畫下來，貼在螢幕旁邊
3. **你算錯的原因是「沒看到 pattern」還是「算度跑完某步忘了反應」？**
   - 前者 → 繼續做 puzzle 量
   - 後者 → 放慢速度、每步做 CCT

## 什麼時候算「這輪 OK」

不是 50 題全對就 OK，那不現實。評估標準：

- **5 個主題各至少 7/10 一次到位（不用提示）**
- **重做隔天錯題後，錯題從超過 10 題降到 5 題以下**
- **第二輪同主題 puzzle 的時間從 3 分鐘降到 1-2 分鐘**

達到這標準 → 進 Part 2。達不到 → 再刷一輪 50 題。

## Rating 的期待值

Part 0 前（從零開始）你的 puzzle rating 大約 800-1000。做完這 50 題**一輪**，rating 會到 1100-1300。做完**第二輪**（重複題做熟），能摸到 1400-1500。

**到 1500 puzzle rating 是 Part 1 的畢業線**。1500 以下不建議直接進 Part 2 殘局，因為戰術沒內化讀殘局也抓不到關鍵子。

## 自我檢核

- [ ] 完成 50 題第一輪，記錄錯題
- [ ] 隔天重做錯題，錯題降到 5 以下
- [ ] 做過 debrief，識別出自己最弱的主題
- [ ] Puzzle rating 至少達 1400
- [ ] 每個戰術 pattern 能在 10 秒內識別類型（不需精確算出來，但知道「這題是 fork 系」）

戰術 pattern 打磨到這個程度後，Part 2 正式進殘局。殘局不像戰術需要大量 pattern，是**少量硬知識**（opposition、square rule、Lucena 等）— 但沒學就不會走，知道了就贏大半。

→ [Ch 10 K+Q vs K](./10-kq-vs-k.md)
