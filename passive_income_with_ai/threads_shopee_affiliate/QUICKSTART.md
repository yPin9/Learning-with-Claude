# QUICKSTART：從 0 到發出前 10 篇（第一週行動清單）

> **這份幫你今天就開工。** 把 [README.md](./README.md)（原理）、[case_studies.md](./case_studies.md)（範例）、`scripts/`（工具）串成一條「照著做」的流程。
>
> **誠實的期待**：第一週**大概率還賺不到錢**。第一週的目標不是分潤，是「**把系統建起來、發出前 10 篇、開始有真實數據**」。賺錢是後面持續迭代的結果，不是第一週的事（見最後的里程碑）。

---

## Day 0（約 30–60 分鐘）：註冊與設定

1. **註冊蝦皮分潤計畫**：到 [affiliate.shopee.tw](https://affiliate.shopee.tw) 或蝦皮 App【我的 → 蝦皮分潤計畫】申請，合作類型選「部落客／社交媒體」。審核過了才有官方連結可產。**完整步驟與條款見 [分類 README 的「開始前」一節](../README.md)，並以蝦皮官方當期說明為準。**
2. **選一個利基**（只選一個）：挑你**自己真的懂、真的會買**的領域（3C／居家／美妝／寵物／省錢情報…）。利基越聚焦，演算法越知道把你推給誰（見 case_studies 案例 4）。
3. **設定帳號**：
   - handle 與 bio 講清楚定位（例：「通勤族的平價 3C 好物｜實際用過才推」）。
   - bio 或置頂放一句**分潤揭露**（例：「文中／留言含蝦皮分潤連結」）。
   - 開一個 **Portaly（傳送門）** 之類的 link-in-bio 承接頁，之後多個連結集中放這。
4. **做一次追蹤實測（重要）**：產一條你的分潤連結，先看後台「點擊數」會不會在你點它之後增加（測點擊歸因）；要測「成單」歸因，**請有真實購買需求的朋友（非同住、無利益交換）透過你的連結下單**——**別自己買來測**（self-referral / 自買多數計畫禁止，以蝦皮條款為準）。目的是排除 playbook 第五節說的「導購連結覆蓋追蹤」陷阱——確認連結真的算得到，再大量發。

> **跑指令前**：先 `cd` 到本資料夾（`cd passive_income_with_ai/threads_shopee_affiliate`），下面的 `scripts/...`、`templates/...` 與產生的 CSV 都是相對這裡。

## Day 1–2：拆解別人（用法 A）

5. 讀完 [case_studies.md](./case_studies.md)，建立「什麼樣的貼文會起來」的直覺。
6. 滑你的利基，用 [`templates/collection_sheet.csv`](./templates/collection_sheet.csv) 填 **15–20 篇**（帳號／URL／形態／鉤子／連結在內文還留言／體感互動）。
7. 挑 3–5 篇高互動的，**把貼文內文存成 `post.txt`**，跑 `python scripts/post_teardown.py post.txt --likes <讚> --comments <留言>`（不帶檔案會跑內建示範）。看它打中哪些鉤子、走逼留言還是高分享路徑。
8. **歸納**：在你的利基裡，哪種「鉤子 × 形態 × 選品」最常起來？那就是你要照抄**結構**（不是抄字）的模板。

## Day 3：選品 + 產第一批草稿（用法 B）

9. 到蝦皮分潤後台挑 **5–8 個你真的會推**的商品（有分潤、和利基相關）。不確定先推哪個？把候選填成 CSV 跑 `python scripts/product_scorer.py items.csv`（分潤×需求×可內容化×競爭 + 硬門檻）挑高分的。把選定商品的事實填進 `products.json`（`cp templates/products.example.json products.json` 再改）。鉤子靈感看 [`hooks.md`](./hooks.md)。
10. `python scripts/hook_generator.py --topic "你的主題" --product products.json --n 5 --dry-run` 先看 prompt，OK 再拿掉 `--dry-run` 產草稿。
11. **挑一個、改成你自己的口吻、補真實心得**（`[請補：...]` 一定要補）——千篇一律的 AI 貼文會被埋。

## Day 4–7：發前 10 篇 + 過閘 + 追蹤

12. 每一篇都這樣走：
    - 內文存 `body.txt`、第一則留言（連結＋揭露）存 `comment.txt`。
    - `python scripts/post_qa.py body.txt --comment comment.txt` **過閘**，沒過先改。
    - 發主貼文 → **立刻**用第一則留言回自己、放連結＋揭露。
    - `python scripts/tracker.py add --post <代號> --hook <鉤子> --topic <主題> --product <選品>`。
13. **節奏**：一天 1–2 篇，**換不同鉤子／選品**測。一週累積約 10 篇。
14. 觀察哪幾篇觸及起來——起來的那種鉤子，下週多做。

## 第 2–4 週：用數據迭代

15. 回填數據：`python scripts/tracker.py update --post <代號> --reach .. --likes .. --comments .. --clicks .. --commission ..`
16. `python scripts/tracker.py report` 看每篇與各鉤子累計；要比**哪種鉤子／選品／主題的「留言連結點擊 + 分潤」最好**（不是看讚），用 `python scripts/tracker.py ab --by hook`（或 `--by product` / `--by topic`）。
17. **砍**沒人點連結的，**複製**會轉換的；持續從 collection_sheet 補新鉤子。

---

## 固定下來的「每日最小循環」（穩定後你只需要這個）

- **拆 1 篇**別人的（填 collection_sheet）——持續補鉤子靈感。
- **發 1–2 篇**自己的（一定過 `post_qa`）。
- **回填昨天的數據**（tracker）。

10 分鐘能跑完的循環，重點是**天天做、用數據轉向**，而不是某天爆肝發 20 篇。

## 務實里程碑（別騙自己）

| 時間 | 合理的目標 | 不切實際的幻想 |
|---|---|---|
| 第 1 週 | 系統建好、10 篇上線、開始有觸及數據 | 「第一週就有分潤」 |
| 第 1 個月 | 找到 1–2 個會起來的「鉤子 × 選品」組合，可能有零星分潤 | 「月入幾萬」 |
| 第 3 個月 | 若持續發＋選品準，**可能**開始接近「小副業級分潤」——但成效因人/利基而異，**以你自己的 tracker 數據為準** | 「躺著被動收入」「保證月入 X」 |
| 半年＋ | 少數人靠利基準＋高頻＋長期，做到較可觀；但這是少數，不是常態 | 「辭職全靠這個」 |

把它當「**用 AI 加速的內容副業**」，不是「印鈔機」。能接受這個期待值，再開始。

> 想把期待值算成數字？用 `python scripts/funnel_calc.py --target <你的月分潤目標>`，它會用你填的觸及/轉換率反推「要多少觸及/篇數」——多數人第一次跑會發現目標訂太高，這正是校準期待的用意。

→ 原理回 [README.md](./README.md)；範例看 [case_studies.md](./case_studies.md)。
