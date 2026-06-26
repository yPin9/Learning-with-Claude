---
name: shopee-threads-ops
description: 用 Threads × 蝦皮分潤的半自動營運循環來經營與迭代一個分潤帳號。當使用者想「跑蝦皮分潤工作流／Threads 分潤營運／每日發文循環／拆解爆文／A-B 測試／週復盤」，或輸入 /shopee-threads-ops 時啟動。它驅動 passive_income_with_ai/threads_shopee_affiliate 的腳本與流程（選題→產文→過閘→追蹤→迭代），但不會、也不能自動發文或自動讀 Threads 真實數據（無 API、登入限制、ToS）。
---

# shopee-threads-ops：Threads × 蝦皮分潤的半自動營運循環

把 [`passive_income_with_ai/threads_shopee_affiliate`](../../../passive_income_with_ai/threads_shopee_affiliate/) 的 playbook、腳本、案例庫，串成一個你可以反覆跑的營運循環。這個技能是上面那條 **hill-climb 迴圈**（[workflow.md](../../../passive_income_with_ai/threads_shopee_affiliate/workflow.md)）的「執行者」。

## 先講清楚這個技能能做什麼、不能做什麼（誠實邊界）

- ✅ **能**：幫你拆解公開貼文（WebFetch）、歸納可複製的鉤子、用 `hook_generator` 產草稿、用 `post_qa` 過閘、用 `tracker` 記錄與產出復盤報告、設計與評估 A/B、提醒每天/每週該做什麼。
- ❌ **不能**：自動登入 Threads 發文、自動抓你貼文的真實觸及/分潤數字。**發文與數據回填一定是你手動做**（沒有 Threads API、爬它違反 ToS、本 session 也沒有瀏覽器工具）。所以這是「**半自動**」——我做判斷與工具，你做實際發佈與回報數字。

## 啟動時

1. 先確認工作目錄：所有腳本與 CSV 都相對 `passive_income_with_ai/threads_shopee_affiliate/`，執行前 `cd` 過去。
2. 問使用者要跑哪個模式（或從他的話判斷）：`setup` / `daily` / `trend-mine` / `produce` / `ab` / `review`。沒講就列出來讓他選。
3. **每個模式都要守的合規鐵律**（任何時候都不可違反，違反就拒絕並說明）：
   - 內容只用使用者自己的素材；**複製結構可以，複製文字/圖片/影片不行**（侵權＝分潤蟑螂）。
   - 導購一定**揭露分潤**；不要求加 LINE 領好康、不要個資、不導可疑網站。
   - **不開分身互按、不買假互動**（Meta 操弄性不實行為，會被砍號）。
   - 追蹤實測**別自買**（self-referral 多數計畫禁止）；請真實需求的朋友測。
   - 任何「蝦皮費率/條款/歸因」都說「以官方當期為準」，不杜撰數字。

---

## 模式：`setup`（第一次用，一次性）

照 [分類 README「開始前」](../../../passive_income_with_ai/README.md) 與 [QUICKSTART Day 0](../../../passive_income_with_ai/threads_shopee_affiliate/QUICKSTART.md) 帶使用者走：
1. 確認已申請蝦皮分潤計畫（affiliate.shopee.tw）、能產官方連結。
2. 一起定**一個利基**（他懂、會買的）。
3. 設定 handle / bio（含一句揭露）/ Portaly 承接頁。
4. 提醒做一次**追蹤實測**（朋友真實購買，別自買）。
5. `cp templates/config.example.yaml config.yaml`、`cp templates/products.example.json products.json` 並協助填好。

## 模式：`trend-mine`（挖爆文、補鉤子靈感）

1. 請使用者貼幾個他在 Threads 滑到、想研究的**公開貼文 URL**（或給利基關鍵字，我用 WebSearch 找公開貼文）。
2. 對每個 URL 用 **WebFetch** 抓公開內容與可見互動數（抓不到就標「需登入確認」，不要編數字）。
3. 對每篇套 `post_teardown.py` 的判讀邏輯：鉤子類型、連結在內文還留言、走逼留言還是高分享路徑；有數字就算留言/讚比。
4. 歸納「可複製的結構」（**只有結構，不抄字**），整理成幾條可套用到他選品的鉤子。
5. 把每篇追加進 `templates/collection_sheet.csv`（或他的副本）。
6. 產出：「這批看到的 N 個可複製鉤子 + 建議拿哪個去試」。

## 模式：`produce`（產今天/這批要發的草稿）

0. （選品沒定好時）把候選商品填成 CSV 跑 `python scripts/product_scorer.py items.csv`，挑無硬門檻、高分的；需要鉤子靈感看 [`hooks.md`](../../../passive_income_with_ai/threads_shopee_affiliate/hooks.md)。
1. 確認主題與 `products.json`（沒則先 `produce` 前做 `setup` 選品）。
2. 跑 `python scripts/hook_generator.py --topic "<主題>" --product products.json --n 5 --dry-run` 給使用者看，OK 再去掉 `--dry-run` 產草稿（需要他的 `ANTHROPIC_API_KEY`；沒有就把 prompt 給他自己貼）。
3. 請他**挑一個變體、改成自己口吻、補真實心得**（提醒：罐頭 AI 文會被埋）。
4. 把最終內文存 `body.txt`、第一則留言（連結＋揭露）存 `comment.txt`，跑 `python scripts/post_qa.py body.txt --comment comment.txt`。**沒過就回到第 3 步改**，過了才放行。
5. 告訴他發佈步驟：發主貼文 → **立刻**用第一則留言貼連結＋揭露；發完回來跑 `daily` 的記錄步驟。

## 模式：`daily`（每天的循環）

引導跑完當天份（對照 [workflow.md 第 1 節](../../../passive_income_with_ai/threads_shopee_affiliate/workflow.md)）：
1. **回填昨天數據**：對昨天發的貼文 `python scripts/tracker.py update --post <代號> --reach .. --likes .. --comments .. --clicks .. --commission ..`（數字由使用者從 Threads 洞察＋分潤後台提供）。
2. **拆 1 篇**（走 `trend-mine` 的精簡版，至少存 1 篇進蒐集表）。
3. **產並過閘 1–3 篇**（走 `produce`），守住價值:導購 ≈ 3:1。
4. 每篇發完 `python scripts/tracker.py add --post <代號> --hook <鉤子> --topic <主題> --product <選品>`。
5. 收尾：提醒他發佈是手動的、連結放第一則留言、別洗版。

## 模式：`ab`（設計或評估 A/B）

1. **設計**：協助定**一個變數**（鉤子/連結位置/時段/選品角度其一）。預設用**單帳號序列 A/B**；若使用者要多帳號，先確認每個帳號是真實獨立不同利基的經營體，並警告「分身/互按＝違規」。定好每變體至少累積 5–10 篇、主指標用「連結點擊＋分潤」。
2. **評估**：跑 `python scripts/tracker.py ab --by hook`（或 `--by product` / `--by topic`），比較兩變體在「點擊＋分潤/篇」上的差。提醒：每組 <5 篇腳本會警告、先別下結論；贏家要**拿去新一批 held-out 再驗**才推廣（避免過擬合）。

## 模式：`review`（每週/每月復盤＋迭代）

1. `python scripts/tracker.py report`。
2. 分析並具體建議：**加碼**哪個鉤子/選品、**淘汰**哪些（連續高觸及零點擊→換鉤子；零轉換→換品）、有沒有鉤子疲乏。
3. 定下「**下週要測的那 1 個變數**」。
4. **失敗回灌**：若有貼文被埋/被檢舉/掉觸及，一起找原因，把它變成 `post_qa.py` 的新檢查或 `collection_sheet` 的反例。
5. （每月）順帶檢視選品換血、利基微調、活動檔期準備。

---

## 跑完後

- 把這次做了什麼、產出什麼（新增哪些蒐集、發了哪些、tracker 現況、下一步建議）簡短總結給使用者。
- 永遠提醒：**發佈與數字回填是你手動的**；這套是幫你想清楚、把關品質、留下數據，不是替你自動營運。
- 詳細原理見 [README](../../../passive_income_with_ai/threads_shopee_affiliate/README.md)、流程規格見 [workflow.md](../../../passive_income_with_ai/threads_shopee_affiliate/workflow.md)、真實案例見 [case_studies.md](../../../passive_income_with_ai/threads_shopee_affiliate/case_studies.md)。
