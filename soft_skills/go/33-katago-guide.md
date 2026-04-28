# Ch 33 — KataGo 完整指南

> 目標：完整掌握 KataGo + 三大 GUI（KaTrain / Lizzie / Sabaki），讓 AI 復盤變成你日常工作流。

## KataGo 是什麼

開源 + 免費的圍棋 AI，**強過所有人類冠軍**。基於 AlphaGo 演算法 + 額外改進：

- **更快**（單機 GPU 可達職業 9p+ 強度）
- **可調 playout**（電腦弱 = 業餘 5d、強 = 比 AlphaGo Master 強）
- **目數估算**（不只勝率，還顯示「領先幾目」）
- **score-aware**（會考慮目數差異，不只贏輸）

## KataGo Network Weights

KataGo 自身是「框架」，需要 **network weights** 才能下棋。weights 越大越強：

| Weight | 大小 | 強度 |
|---|---|---|
| 6-block | ~5 MB | 業餘 1-3 段 |
| 18-block | ~50 MB | 業餘高段 |
| 28-block | ~150 MB | 職業 |
| 40-block | ~300 MB | 超職業 |

新手用 18-block 已經足夠（強過業餘 5d）。28-block 復盤更精準但需要 GPU。

下載：https://katagotraining.org/networks/

## 3 大 GUI 對比

### KaTrain（推 #1）

**對學棋者最友善**。

特色：

- 「**Mistakes**」按鈕標出每手失誤（紅 / 橘 / 黃）
- 「**Equity**」曲線顯示勝率變化
- 「**AI Settings**」可調 playout 數
- 介面直觀，新手第一選擇

下載：https://github.com/sanderland/katrain/releases

### Lizzie（推 #2）

**棋手最愛的「即時分析」工具**。

特色：

- **半透明候選手**：滑鼠懸停看 AI 推薦
- **變化樹**：1 鍵展開所有變化
- **學棋棋手最愛** — 因為視覺化清楚
- 設定 KataGo 麻煩

下載：https://github.com/featurecat/lizzie/releases

### Sabaki（純 SGF Editor）

**輕量、適合單純看棋譜**。

特色：

- 純粹 SGF 編輯器
- 整合 AI 麻煩
- 適合不需要 AI 分析時

## KaTrain 完整工作流

### 1. 載入棋譜

```
File → Open SGF → 選你的對局
```

或從 OGS / 野狐 export SGF 後拖進 KaTrain。

### 2. 自動分析

KaTrain 自動跑 AI 對每手評估。等 1-3 分鐘（按 weight 大小）。

### 3. 看 Mistakes

**點 "Mistakes" 標籤**。AI 列出所有 score loss > 1 目的手：

```
 手 23：score loss -8.5（紅）
 手 47：score loss -4.2（橘）
 手 89：score loss -2.1（黃）
```

點任何手 → 跳到那一手 + 顯示 AI 推薦。

### 4. 看 AI 推薦變化

每手在棋盤上顯示：

- **紅圈 + 數字**：AI 推薦手 + 推薦變化的勝率
- **半透明**：其他候選手

點任意推薦手 → 看 5 步續推 → 理解 AI 的「為什麼」。

### 5. 寫筆記

KaTrain 內建 SGF comments：

```
 Right-click 棋盤位置 → Add Comment
```

寫下「我為什麼下錯」「AI 為什麼這樣推」。**這是內化的關鍵**。

## KataGo 設置技巧

### Playout 數

控制 AI 算多深：

- **playout 100**：快但弱（業餘 1d）
- **playout 1000**：中（業餘 5d）
- **playout 10000**：強（職業）
- **playout 100000+**：超強（KataGo 全力）

KaTrain 預設 playout 通常 800-1500。對局時用低 playout 快、復盤時用高 playout。

### GPU vs CPU

**有 NVIDIA GPU**：playout 5000 = 1-2 秒/手  
**沒 GPU**：playout 100 = 1-2 秒/手

GPU 是 KataGo 的「**性能 boost**」。沒 GPU 也能用，但慢。

### Hardware 配置

KaTrain → Settings → Engine → 選擇 KataGo binary 跟 weight。

如果你的硬體不夠：

- 下載 6-block weight（最弱但最快）
- playout 200-500
- 還是夠用

## 進階：KataGo command line

如果你想 hack：

```bash
katago analysis -model <weight.bin.gz> -config <config.cfg>
```

接 STDIN/STDOUT 的 JSON API。對棋譜批次分析、自寫工具用。

實務上 KaTrain 已經幫你包好，**新手不需要碰 command line**。

## Lizzie 簡介

如果你想試 Lizzie：

```bash
# 下載 Lizzie + 配 KataGo binary
# Lizzie → Engine Settings → 路徑指向 katago analysis
```

**Lizzie 強項**：

- **變化樹**：每個位置 1 鍵展開
- **候選手懸浮**：滑鼠 over 看勝率
- **playout 可手調**：右側 slider

新手不必馬上學 Lizzie，KaTrain 夠用。但業餘 1d+ 後可以試。

## 一個常見誤解：「強的 weight 復盤一定比弱的好」

**部分對**。28-block 比 18-block 算得深，但業餘看不出細微差別。

新手用 18-block 就夠。**省 GPU 時間給其他事**。

## 一個常見誤解：「KaTrain 自動分析的勝率 100% 準確」

**錯**。AI 給的是「**統計勝率**」 — 假設後續雙方下最佳手。

業餘對局後續不會下最佳手 → 實際勝率跟 AI 顯示有差距。

當參考用，**別當聖經**。

## 一個常見誤解：「playout 越高越好」

**部分對**。playout 高 = AI 算更深，但**邊際收益遞減**。

playout 1000 → 5000 變化不大；playout 10000 → 100000 變化更小。

復盤用 5000 已經很強，不必 100000。

## 一個常見誤解：「KaTrain 慢 = 我電腦爛」

**部分對**。但也可能：

- 沒裝 GPU 加速
- weight 太大（28+ block + 沒 GPU）
- playout 設太高

調整：weight 換 18-block + playout 1500。

## 動手練習

**1. KaTrain 完整流程**

挑你最近 1 盤對局，完整跑：

- 載入 SGF
- 等分析完成
- 看 Mistakes
- 點 3 個紅手看 AI 推薦
- 寫筆記

**2. 比較 weight 強度**

對 KaTrain 設不同 weight 各下 1 盤：

- 6-block
- 18-block
- 28-block

哪個最舒服？哪個最強？

**3. 試 Lizzie**

下載 Lizzie，配對 KataGo。打開一盤棋，玩玩**變化樹** + **候選手懸浮**功能。

對比 KaTrain，找你的偏好。

**4. SGF 註解練習**

打開最近 1 盤對局，**對 5 個關鍵手寫 SGF comment**：

- 我當時想什麼
- AI 推薦什麼
- 我學到什麼

存檔後過 1 週再看 — 你還記得當時的思考嗎？

## 自我檢核

- [ ] 知道 KataGo 是什麼、怎麼下載
- [ ] KaTrain 能完整跑復盤流程
- [ ] 認識 weight 大小跟強度的關係
- [ ] 試過 Lizzie 至少 1 次
- [ ] 為自己的對局寫過 SGF comment

下一章看「用 AI 復盤的正確方法」 — 不是看曲線就好，要怎麼真正學到。

→ [Ch 34 用 AI 復盤的正確方法](./34-ai-review-method.md)
