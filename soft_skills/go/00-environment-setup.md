# Ch 0 — 環境搭建

> 目標：把對局平台、KataGo 引擎、棋譜 GUI、死活題網站全部備齊。後面每章都靠這套。

## 你會用到的東西

| 用途 | 工具 |
|---|---|
| 對局平台（線上跟人下） | OGS / 野狐 / KGS / Tygem / Fox |
| AI 引擎（復盤、分析） | **KataGo**（強過所有人類冠軍） |
| GUI 前端（看棋譜、跑 KataGo） | KaTrain（推）/ Lizzie / Sabaki |
| 死活題網站 | Goproblems / blacktoplay / BadukPop |
| 棋譜資料庫 | GoKifu / Waltheri Pattern Search |
| 桌上實體棋具 | 一副便宜木棋 + 玻璃子（可選但強烈推） |

別省工具步驟。後面每章都假設你能 (1) 上線下棋 (2) 用 KataGo 復盤 (3) 做死活題。三件事缺一個，課就讀不下去。

## Step 1：對局平台帳號

註冊一個就好，建議：

- **OGS** (https://online-go.com)：免費、開源、英文介面、社群活躍。**新手最推這個**
- **野狐圍棋**：中國大陸主流，棋手多、節奏快，但介面廣告多
- **KGS** (https://www.gokgs.com)：歐美傳統大平台，AI 跟老手多，介面老
- **Tygem / Fox**：韓國強手集中地，到中段位再去

**我的建議：先註冊 OGS**。新手友善、有 9x9 對局選項、能直接 export SGF 給 KataGo 看。

註冊後跑這幾件事熟悉介面：

1. 開一個 9x9 vs Computer (low rank)
2. 下完，下載 SGF 檔
3. 看自己的 profile：rank 顯示「?」表示沒被定段，需要下幾盤定段

## Step 2：KataGo 引擎

**這是整套課的核心工具**，免錢的世界第一強圍棋 AI。

最簡單的安裝 = 直接裝 **KaTrain**（GUI 已內建 KataGo）：

- 下載：https://github.com/sanderland/katrain/releases
- 選對應 OS：Windows / Mac / Linux
- 第一次啟動會問下載哪個 weight，選 **18-block** 或 **28-block** 大型網路（強且不慢）

跑起來後：

1. 開 → New Game → vs AI
2. 設 AI rank = 7d（強到爆，會輸到家門口找你）
3. 改設 rank = 5k 或 10k 來練手感

如果你想單獨用 KataGo（不要 GUI），裝二進制：

```bash
# Linux 範例
wget https://github.com/lightvector/KataGo/releases/download/<latest>/katago-linux.zip
unzip katago-linux.zip
./katago analysis -model <weight.bin.gz> -config analysis_example.cfg
```

GPU vs CPU：有 NVIDIA GPU 速度快 10-100x。沒 GPU 也能跑，但分析一手要等 1-3 秒。

## Step 3：GUI 前端

KaTrain 已經是 GUI，但有幾個替代：

| GUI | 強項 | 弱點 |
|---|---|---|
| **KaTrain** | 對學棋者最友善：標 mistakes、equity、推薦變化都直觀 | 介面有點重 |
| **Lizzie** | 棋手最愛，視覺化候選手清楚 | 設定 KataGo 要手動 |
| **Sabaki** | 純 SGF editor，輕量 | 分析功能弱 |
| **Goban** (Mac) | macOS 原生 | Mac only |

**新手選 KaTrain，不會錯**。後面 Ch 33 會詳細講三家差別。

## Step 4：死活題資源

死活訓練是圍棋上手的命脈。每天 30-50 題能撐半年，你已經贏 80% 學棋人。

| 資源 | 程度 | 特色 |
|---|---|---|
| **Goproblems** (goproblems.com) | 全段位 | 免費、量大、有 rating |
| **BadukPop** (App Store / Play) | 入門 - 5 段 | 手機 app，killer feature 是「題目分難度」 |
| **101weiqi.com** | 全段位 | 中文，海量題目，可建自己的題集 |
| **Cho Chikun's Encyclopedia of Life and Death** | 入門 - 高段 | 經典書，紙本 + 電子版皆有 |
| **《玄玄棋經》《發陽論》《官子譜》** | 中高段 | 古典難題，業餘高段必啃 |

**我的建議**：手機裝 BadukPop 在通勤練、桌前用 Goproblems 累計 rating、書本啃 Cho Chikun。

## Step 5：棋譜資料庫

學名局、研究 AI 推薦的標準變化，要有棋譜來源：

- **GoKifu** (gokifu.com)：免費、新棋譜更新快
- **Waltheri Pattern Search** (ps.waltheri.net)：搜尋特定佈局後職業棋手怎麼下
- **gobase.org**：老站但棋譜全
- **AlphaGo / DeepMind 公開棋譜**：找「AlphaGo Master 60-game series」、「AlphaGo vs Lee Sedol」自己對戰

下載 SGF 後，KaTrain 直接 open 就能看 + 跑 KataGo 分析。

## Step 6：（可選）實體棋具

線上能練到所有東西，但實體下對「全局視野」訓練不可取代：

- **入門棋盤**：木製或塑膠 19x19，便宜的台幣 500-1000 有
- **棋子**：玻璃子（不是石材）夠用
- **棋鐘**：可選，比賽才需要

中文圈推：誠品、博客來、淘寶都有。日本棋具（蛤碁石 + 那智黑）動輒 5-10 萬，新手別碰。

## 一個常見踩雷：跑去買「圍棋入門書」

書店圍棋區 80% 是「20 課速成」「3 天上手圍棋」這種書 — **大多教完規則就結束**，跟你練不到死活。買書要買：

- 死活題集（玄玄棋經 / Cho Chikun）
- 手筋題集（Maeda Tesuji 系列）
- 名手對局集（吳清源 / 李昌鎬 / 李世乭）

**理論書一本 KataGo 能取代十本**。錢花在買題集跟印題目，不要花在花俏的入門書。

## 一個常見踩雷：「我先看完規則章再開始下」

別。看完 Ch 2 規則就**馬上**去下 9x9（OGS vs Computer rank 25k），會輸但能形成體感。**圍棋是體感遊戲，純讀只會卡住**。

## Sanity check

跑下面這串，每個都該成功：

```
[ ] OGS 帳號註冊好，能進對局大廳
[ ] KaTrain 啟動，能 vs Computer 下完 9x9 一盤
[ ] KataGo 在 KaTrain 內能跑 analysis（每手 < 5 秒）
[ ] Goproblems 開得了，能做一題 30 級難度
[ ] 能在 KaTrain 開啟 SGF 檔案
```

全部過再進 Ch 1。

## 自我檢核

- [ ] OGS（或其他平台）帳號開好
- [ ] KaTrain 跟 KataGo 跑得起來
- [ ] 知道 KaTrain / Lizzie / Sabaki 各自定位
- [ ] 死活題網站至少做過一題
- [ ] 知道為什麼「先看書再下」是錯的學法

下一章看圍棋的全貌：段級制度、學棋路線圖、AI 時代怎麼學棋。

→ [Ch 1 圍棋全貌與段級制度](./01-go-overview.md)
