# Ch 13 — 索引

> **目標**：搞懂 Firestore 的索引——為什麼查詢需要索引、單欄位索引（自動）和複合索引（要建）的差別、看到「requires an index」時怎麼一鍵解決、以及一個超容易咬人的坑：**模擬器不強制索引，但正式環境會**。

> **環境**：Firebase JS SDK v12.18.0。「模擬器不要求複合索引」的行為以 Firestore 模擬器實跑驗證；「正式環境要求索引」為官方文件與正式環境的已知行為（本地無真實專案，標為官方確認行為）。

## 為什麼需要這個？

當你在 Ch 10 寫出「篩一個欄位 + 用另一個欄位排序」的查詢，正式環境會給你一個錯誤：`The query requires an index`。第一次遇到會慌——是我寫錯了嗎？不是。這是 Firestore 正常運作的一部分。這章讓你理解索引是什麼、為什麼要它、以及怎麼優雅地處理這個錯誤（其實只要點一下連結）。

## 先建立直覺：書的索引

```
   沒有索引                          有索引
   要找「所有提到 X 的頁」            翻到書末的索引頁
   得從第一頁翻到最後一頁            「X ....... p.42, p.88」
   （全書掃描，書越厚越慢）          直接翻到那幾頁（多厚的書都一樣快）
```

資料庫索引就是「書末的索引頁」——**一份預先排好序的對照表**，讓查詢不用掃全部資料，直接跳到符合的那幾筆。

Firestore 把這個概念推到極致：**它要求每個查詢都必須有對應的索引**，這樣才能保證 Ch 10 說的那個賣點——**查詢速度只和「回傳幾筆」有關，和「資料庫總共多少筆」無關**。沒有索引能支援的查詢，Firestore**直接拒絕執行**，而不是慢慢掃給你看。這就是為什麼會有「requires an index」錯誤：它在說「我沒有能快速執行這個查詢的索引，請先建一個」。

## 兩種索引：自動的 vs 要你建的

### 單欄位索引：自動，你不用管

Firestore **自動**為每個欄位建立單欄位索引。所以這些查詢**開箱即用**、不用建任何東西：

```js
where("authorId", "==", "uid_alice")           // 單欄位 ==，OK
where("price", ">", 100)                         // 單欄位範圍，OK
orderBy("createdAt", "desc")                     // 單欄位排序，OK
where("price", ">", 100), orderBy("price")       // 同一欄位篩+排，OK
```

實測（模擬器）：`where("score",">",10)` + `orderBy("score","desc")`（**同一欄位** score）直接可跑，回傳 p2,p3——這種同欄位的不需要複合索引。

### 複合索引：跨多欄位，要你建

當查詢**組合多個欄位**——例如篩一個欄位、用**另一個**欄位排序，或篩多個欄位——就需要**複合索引（composite index）**，這個 Firestore**不會**自動建，要你手動建：

```js
// 篩 cat + 用「另一個」欄位 price 排序 → 需要複合索引
query(collection(db,"prod"),
      where("cat", "==", "book"),
      orderBy("price", "desc"))

// 篩多個欄位 → 需要複合索引
query(collection(db,"posts"),
      where("authorId","==",uid),
      where("published","==",true),
      orderBy("createdAt","desc"))
```

**判斷法則**：查詢只碰**一個欄位**（或同一欄位篩+排）→ 自動索引，免建。查詢碰**多個不同欄位**（跨欄位篩+排、或多重篩選）→ 要建複合索引。

## 看到「requires an index」怎麼辦：點連結就好

這是 Firebase 最貼心的設計之一。當你在**正式環境**跑一個需要複合索引的查詢，錯誤訊息長這樣（官方確認的格式）：

```
FirebaseError: The query requires an index. You can create it here:
https://console.firebase.google.com/project/你的專案/firestore/indexes?create_composite=...
```

**那個連結會帶著「這個查詢需要的索引設定」直接開啟 Console 的建立索引頁**——你只要點連結、按「建立」，等它建好（幾秒到幾分鐘，看資料量），查詢就能跑了。你**完全不用自己想「該建什麼索引」**，Firebase 從你的查詢推導好、連結裡都填好了。

所以正確的工作流程是：

1. 寫查詢 → 跑 → 如果報「requires an index」
2. 點錯誤訊息裡的連結
3. 在開啟的頁面按「建立索引」
4. 等狀態從「建立中（Building）」變「已啟用（Enabled）」
5. 重跑查詢 → 成功

**這不是 bug、不是你寫錯，是正常開發流程。** 每個 Firestore 開發者都點過無數次這種連結。

## ⚠️ 大坑：模擬器不要求索引，正式環境要求

這是本章最重要、最容易害你上線爆炸的一點。**認識論誠實**：

實測——同一個需要複合索引的查詢（`where("cat","==","book")` + `orderBy("price","desc")`），在 **Firestore 模擬器**上：

```
[emulator] composite query WORKED without index, count: 2 ids: a,b
```

**它直接跑成功了，完全沒要求索引。** 但同樣的查詢在**正式環境**會回 `requires an index` 直到你建好索引（官方確認行為）。

這代表一個危險的陷阱：

```
   你在模擬器（Ch 21）開發              你部署到正式環境
   所有查詢都順暢無比                   使用者一用某個查詢
   （模擬器不檢查索引）                 → 炸掉！requires an index
   你以為沒問題                        → 你在生產環境才發現
```

**怎麼避免**：

1. **知道有這回事**（你現在知道了）——模擬器跑得過不代表正式環境跑得過。
2. **把索引定義寫進 `firestore.indexes.json`** 並用 CLI 部署（見進階），讓索引跟程式碼一起版控、一起上線，而不是靠「上線後點錯誤連結」臨時補。
3. **上線前在真實（測試用）專案跑一遍**所有查詢路徑，把需要的索引都建齊。

> 這個「本地能跑、線上炸掉」的落差，是 Firestore 新手最常見的上線意外之一。記住它，比記住索引的技術細節更能救你。

## 索引怎麼版控：firestore.indexes.json

與其每次靠錯誤連結手動建，專業做法是把索引定義成檔案、隨程式碼部署。當你 `firebase init firestore`（Ch 21）時會生成 `firestore.indexes.json`：

```json
{
  "indexes": [
    {
      "collectionGroup": "posts",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "authorId", "order": "ASCENDING" },
        { "fieldPath": "createdAt", "order": "DESCENDING" }
      ]
    }
  ],
  "fieldOverrides": []
}
```

用 `firebase deploy --only firestore:indexes` 部署，這些索引就建到正式環境。好處：索引和程式碼一起版控、code review 看得到、不同環境（測試/正式）能一致地建齊。**這是避免上面那個大坑的正解。**

> 小技巧：你在 Console 點錯誤連結建的索引，可以之後用 `firebase firestore:indexes` 匯出成 json 存進版控，補齊「手動建的」和「檔案定義的」之間的落差。

## 索引的成本：不是免費的

索引讓查詢快，但有代價，要知道取捨：

- **儲存成本**：每個索引都佔額外儲存空間（索引也算進你的儲存用量計費）。
- **寫入成本**：每次寫入一份文件，**所有相關索引都要更新**——所以欄位越多、索引越多，寫入越慢、越貴。
- **所以**：不要為「可能永遠用不到」的查詢建一堆索引。也可以對「肯定不會拿來查」的大欄位**關閉單欄位索引**（`fieldOverrides`），省儲存和寫入成本。例如存一大段 log 文字，你永遠不會 `where` 它，就沒必要索引它。

**取捨一句話**：索引用「寫入變慢 + 儲存變多」換「查詢變快且可行」。查詢需要的索引一定要建，用不到的別亂建。

## 踩雷集錦

1. **看到「requires an index」以為程式壞了**：那是正常流程。點錯誤訊息裡的連結、建立、等啟用、重跑。每個 Firestore 開發者的日常。
2. **模擬器測過就以為正式環境沒問題**（最危險）：模擬器**不強制**複合索引，正式環境**強制**。本地全綠不代表上線不炸。上線前務必在真專案驗證查詢或用 `firestore.indexes.json` 部署索引。
3. **索引還在「Building」就重跑並以為失敗**：大集合建索引要時間，狀態沒到「Enabled」前查詢還是會被擋。等它建好。
4. **為所有欄位建複合索引「以防萬一」**：索引不是免費的，每個都增加寫入成本和儲存。只建查詢真正需要的。
5. **不知道索引也算儲存計費**：大量索引會推高你的儲存帳單和寫入延遲。定期檢視有沒有沒用到的索引。
6. **以為改了查詢舊索引會自動消失**：不會，舊索引留著繼續佔空間/計費，要手動刪。查詢重構後回頭清理不再需要的索引。

## 進階：再往深一層

- **索引的方向（asc/desc）要匹配查詢**：複合索引的欄位有排序方向，你的 `orderBy` 方向要和索引一致（或建雙向）。錯誤連結生成的索引會自動匹配你當下的查詢方向，但如果你之後改成反向排序，可能需要另一個索引。
- **collection group 索引**：Ch 10 提的 collection group query（跨所有同名子集合查）需要特別的 collection-group scope 索引（`queryScope: "COLLECTION_GROUP"`），也是靠錯誤連結或 json 定義建。
- **exemption / 單欄位覆寫**：`fieldOverrides` 可以對特定欄位「豁免」自動索引（大文字欄位、大陣列欄位），或反過來為某欄位建特殊的單欄位索引（如陣列的 `array-contains`）。用來精細控制「哪些欄位值得索引」。
- **索引 vs 查詢設計的連動**：有時「需要很複雜的複合索引」是一個信號——你的資料結構可能該調整（Ch 12），例如多存一個「組合欄位」讓查詢變單欄位。索引問題常常能靠更好的建模繞開，而不是一直堆索引。
- **`array-contains` 和 `in` 的索引成本**：這類查詢的索引會「展開」，一個文件的陣列有 N 個元素就產生 N 個索引項。大陣列 + 這類查詢會顯著增加索引成本，設計時留意。

## 本章重點整理

- Firestore **要求每個查詢都有對應索引**，沒有就拒絕執行——這是為了保證「速度只和回傳筆數有關」。
- **單欄位查詢**（或同欄位篩+排）自動有索引；**跨多欄位**的查詢要建**複合索引**。
- 看到 **`requires an index`** 不是壞掉——**點錯誤訊息裡的連結、建立、等啟用、重跑**即可。
- **大坑：模擬器不強制索引，正式環境強制**——本地能跑不代表上線能跑。用 `firestore.indexes.json` 版控 + 部署索引來避免。
- 索引不免費：用「寫入變慢 + 儲存變多」換「查詢可行」，只建需要的。

## 自我檢核

- [ ] 我能解釋 Firestore 為什麼要索引，以及它和「查詢速度和資料量無關」的關係
- [ ] 我能判斷一個查詢需不需要複合索引（單欄位 vs 跨欄位）
- [ ] 看到「requires an index」我知道那是正常流程，也知道怎麼一鍵解決
- [ ] 我知道模擬器不強制索引、正式環境強制這個大坑，以及怎麼避免上線炸掉
- [ ] 我理解索引的成本（寫入變慢、儲存變多），不會亂建

## 延伸閱讀

### 官方文件

- **[Index types in Cloud Firestore](https://firebase.google.com/docs/firestore/query-data/index-overview)**
  - **讀哪裡**：整篇，單欄位 vs 複合索引、自動索引、索引與查詢的關係。
  - **能學到什麼**：本章的官方權威版，把索引和查詢限制（Ch 10）連起來講。
  - **前提**：本章讀完即可。

- **[Manage indexes / Deploy indexes with the CLI](https://firebase.google.com/docs/firestore/query-data/indexing)**
  - **讀哪裡**：`firestore.indexes.json` 格式與 `firebase deploy --only firestore:indexes` 那段。
  - **能學到什麼**：本章「版控索引」的完整做法，避免上線大坑的正解。

- **[Index best practices / 索引成本與限制](https://firebase.google.com/docs/firestore/best-practices#indexes)**
  - **讀哪裡**：索引的成本、單欄位豁免、避免熱點那幾段。
  - **能學到什麼**：本章「索引不免費」的完整取捨與最佳實踐。

### 文章

- **[Fireship — Firestore 查詢與索引](https://fireship.io/lessons/advanced-firestore-nosql-database-guide/)** — Jeff Delaney
  - **這篇說什麼**：把查詢限制、索引、建模串在一起。
  - **為什麼值得讀**：讓你理解「索引問題常能靠建模繞開」，而不是死堆索引。

讀、寫、即時、建模、索引都齊了。Part 3 最後一塊拼圖是「一致性」——當你需要「好幾筆資料要嘛一起成功、要嘛一起失敗」（例如轉帳、發文同時更新計數），怎麼保證不會做一半？下一章講交易與批次寫入。

→ [Ch 14 — 交易與批次寫入](./14-firestore-transactions.md)
