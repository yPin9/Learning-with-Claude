# Ch 8 — Firestore 是什麼：document/collection 模型

> **目標**：搞懂 Firestore 的資料是怎麼組織的——document（文件）與 collection（集合）的階層模型，它和 SQL 資料表、和其他 NoSQL 的差別，以及這個模型背後的設計取捨。這章是 Part 3 的地基，觀念為主。理解了資料長什麼樣，後面的讀寫、查詢、建模才有依附。

> **環境**：概念章。文中的資料結構對照以 Firestore v12.18.0 的實際行為為準。

## 為什麼需要這個？

你在 Ch 3 已經用 `addDoc` 寫過一筆資料了，但當時沒解釋那筆資料「住在哪、怎麼組織的」。要能設計出好用的資料結構，你得先在腦中有一張清楚的圖：Firestore 的世界是由什麼組成的。這張圖錯了，你的資料結構就會長歪，後面查詢和安全規則全部跟著痛。

而且——如果你有 SQL 背景，Firestore 會**反直覺**，因為它不是「表格 + 列」。如果你沒有資料庫背景，反而更容易接受它。這章幫兩種人都建立正確的心智模型。

## 先建立直覺：檔案總管的資料夾與檔案

Firestore 的結構，最好的類比是你電腦的**檔案總管**：

```
   資料夾（collection）
      └─ 檔案（document）
            └─ 檔案內容（fields，欄位）
            └─ 有時檔案裡還能再放子資料夾（subcollection）
```

具體一點，一個部落格的資料可能長這樣：

```
📁 users (collection 集合)
   ├─ 📄 uid_alice (document 文件)
   │     ├─ name: "Alice"           ┐
   │     ├─ age: 30                  │ fields（欄位）
   │     └─ createdAt: <Timestamp>   ┘
   │
   └─ 📄 uid_bob (document)
         ├─ name: "Bob"
         └─ age: 25

📁 posts (collection)
   ├─ 📄 post_001 (document)
   │     ├─ title: "我的第一篇"
   │     ├─ authorId: "uid_alice"
   │     ├─ likes: 42
   │     └─ 📁 comments (subcollection 子集合)   ← 文件底下可以再有集合
   │           ├─ 📄 comment_1 { text: "讚", by: "uid_bob" }
   │           └─ 📄 comment_2 { text: "推", by: "uid_alice" }
   └─ 📄 post_002 { ... }
```

三個層級，記住這個交替規律：

- **collection（集合）**：一堆同類文件的容器。像 `users`、`posts`。**collection 只能裝 document。**
- **document（文件）**：一筆資料，有唯一 ID，裡面裝欄位。像 `post_001`。**document 裝的是 fields，也可以再開 subcollection。**
- **field（欄位）**：文件裡的鍵值對。像 `title: "我的第一篇"`。

**關鍵規律：集合 → 文件 → 集合 → 文件……交替下去。** 集合裡不能直接放集合，文件裡不能直接放文件。這個「一層集合、一層文件」的交替是 Firestore 結構的鐵律。

## 路徑：每筆資料都有一個地址

因為是階層結構，每個文件和集合都有一個**路徑（path）**，像檔案路徑一樣：

```
users/uid_alice                          ← 一個 document
users/uid_alice （的父層）→ users        ← 一個 collection
posts/post_001/comments                  ← 一個 subcollection
posts/post_001/comments/comment_1        ← 一個 document
```

規律很清楚：**路徑段數是奇數 → 指向 collection；偶數 → 指向 document。**

```
posts                        1 段（奇）→ collection
posts/post_001               2 段（偶）→ document
posts/post_001/comments      3 段（奇）→ collection
posts/post_001/comments/c1   4 段（偶）→ document
```

你在程式碼裡就是用這些路徑定位資料：`collection(db, "posts")`（奇，指集合）、`doc(db, "posts", "post_001")`（偶，指文件）。這也是為什麼 `collection()` 和 `doc()` 要交替用——它們對應奇/偶路徑。Ch 9 開始你會大量用到。

## 一個 document 裡能放什麼：欄位型別

文件的欄位支援這些型別（你會常用到）：

| 型別 | 例子 | 說明 |
|---|---|---|
| 字串 string | `"Alice"` | |
| 數字 number | `42`、`3.14` | 整數與浮點都算 number |
| 布林 boolean | `true` | |
| 時間戳 timestamp | `serverTimestamp()` | Firestore 專用時間型別（Ch 3 用過） |
| 陣列 array | `["tag1", "tag2"]` | |
| map（巢狀物件） | `{ city: "台北", zip: "100" }` | 文件裡可以有巢狀結構 |
| null | `null` | |
| reference | 指向另一個文件的「指標」 | 進階，存另一文件的路徑 |

**限制**：單一 document 最大 **1 MiB**。這呼應 Ch 2 的踩雷——大檔（圖片影片）不能塞欄位，要放 Storage（Ch 19），文件裡只存那個檔案的網址字串。

> **map vs subcollection 怎麼選？** 兩者都能表達「文件底下的更多資料」。粗略原則：資料量小、固定、總是一起讀 → 用 **map 欄位**（例如地址）；資料量會成長、要獨立查詢/分頁 → 用 **subcollection**（例如一篇文章的留言，可能上千則）。Ch 12 資料建模會深入。

## 和 SQL 資料庫的關鍵差異

如果你熟 SQL，這張對照表幫你轉換，但重點在「差在哪」：

| SQL（關聯式） | Firestore | 差異的意義 |
|---|---|---|
| table（表） | collection | 大致對應 |
| row（列） | document | 大致對應 |
| column（欄，schema 固定） | field（**每個文件欄位可不同**） | Firestore **無強制 schema**，同一集合的兩個文件可以有不同欄位 |
| `JOIN` 多表關聯 | **沒有 JOIN** | 最大差異！要關聯資料得換思路 |
| `WHERE a AND b AND c` 任意組合 | 查詢受限，某些組合要建索引或做不到 | 查詢能力弱得多 |
| 先設計正規化 schema | 先想「我要怎麼查」再決定怎麼存 | 建模思維相反 |

**三個一定要內化的差異**：

1. **沒有 JOIN**：SQL 你可以「把 users 表和 posts 表 join 起來，一次查出每篇文章的作者名字」。Firestore **做不到**。你要嘛在讀到文章後再各別去讀作者（多次查詢），要嘛在文章文件裡**直接複製一份**作者名字（反正規化）。這是 NoSQL 建模的核心取捨，Ch 12 專講。

2. **無強制 schema**：同一個 `users` 集合，Alice 的文件有 `age`，Bob 的可以沒有。Firestore 不管。這給你彈性，也給你責任——沒有資料庫幫你保證資料一致，得靠**安全規則**（Part 4）驗證欄位，或自己在程式碼裡把關。

3. **查詢能力有限**：Firestore 的查詢是為了「大規模下仍然快」而故意設計得受限——它保證查詢速度只跟「回傳幾筆」有關，跟「資料庫裡總共多少筆」無關。代價是很多 SQL 輕鬆的查詢（例如 `OR` 跨欄位、模糊搜尋 `LIKE`）它做不到或要繞。Ch 10、Ch 13 會碰到這些牆。

## 和其他 NoSQL 的差異

Firestore 是「**文件型（document）** NoSQL」，和 MongoDB 同一大類。但相對於自架 MongoDB，Firestore 的特色是：

- **即時同步內建**（Ch 11）——這是它最值錢的地方，多數 NoSQL 沒有。
- **強一致性**——你寫進去，馬上讀得到最新值（不像某些 NoSQL 是最終一致）。
- **全託管**——不用自己架、擴容、備份。
- 代價是**查詢比 MongoDB 更受限**，且**綁定 Google**。

和 Firebase 自家較舊的 **Realtime Database（一棵大 JSON 樹）** 相比，Firestore 用「集合/文件」取代「巨大 JSON 樹」，換來更好的查詢和擴展性——這就是官方推新專案用 Firestore 的原因（Ch 2 講過）。

## 踩雷集錦

1. **把 Firestore 當 SQL 表用**：想著「這張表 join 那張表」「先正規化」去設計 Firestore，一定撞牆。NoSQL 是**先想查詢、再設計結構**，而且常常**故意重複資料**（反正規化）。這是最根本、最多人犯的心態錯誤。
2. **搞錯 collection/document 交替規律**：試圖 `doc(db, "posts")`（只有一段，其實是 collection）或 `collection(db, "posts", "post_001")`（兩段，其實是 document）會出錯。記住奇數段=集合、偶數段=文件。
3. **以為同集合文件必須同結構**：不必。Firestore 無強制 schema。但「不必」不代表「該亂來」——你仍應該讓同類文件結構一致，只是這個一致性要**你自己維護**（靠規則或程式碼），資料庫不幫你。
4. **把大東西塞進一個 document**：單文件 1 MiB 上限，而且「把一整個列表塞進一個文件的陣列欄位」會導致「改一個項目要重寫整份文件」。大量、會成長的資料用 subcollection 或獨立集合，別塞陣列。
5. **以為 subcollection 會跟著父文件一起被讀出來**：不會。讀 `posts/post_001` **不會**自動帶出它的 `comments` 子集合，要另外查。subcollection 是獨立的，這其實是好事（不會一次拉爆），但別誤會。
6. **刪除文件以為會刪掉它的子集合**：**不會！** 刪 `posts/post_001` 不會刪掉 `posts/post_001/comments` 底下的文件，它們會變成「孤兒」（路徑還在但父文件沒了）。子集合要另外遞迴刪。這是很隱蔽的坑。

## 進階：再往深一層

- **document ID 的選擇**：ID 可以讓 Firestore 自動產生（`addDoc`，隨機 20 字元），也可以自己指定（`setDoc(doc(db, "users", uid), ...)`）。用有意義的 ID（例如用 `uid` 當 user 文件 ID）能讓你「不用查就知道路徑」——想拿 Alice 的資料直接 `doc(db,"users","uid_alice")`，不用先查。Ch 9 會對比 `addDoc` vs `setDoc`。
- **reference 型別與「假 JOIN」**：欄位可以存另一個文件的 reference（指標）。但存了 reference 不代表能 JOIN——你拿到 reference 還是要再發一次查詢去讀那個文件。它只是「有型別的路徑字串」，比自己存字串路徑多一點便利。
- **根集合 vs 子集合的查詢差異**：你可以用 **collection group query** 一次查所有同名的子集合（例如所有文章底下的所有 `comments`），這在資料分散在多個子集合時很有用。Ch 10 會提到。這是 Firestore 階層模型的一個強大延伸。
- **為什麼查詢「和資料庫大小無關」**：Firestore 的每個查詢底層都靠索引（Ch 13），它掃的是索引而非全表，所以回傳 100 筆的查詢，不管你資料庫有 1000 筆還是 10 億筆，速度幾乎一樣。這個「可預測的效能」是它犧牲查詢彈性換來的核心賣點。

## 本章重點整理

- Firestore 是**階層文件模型**：**collection（集合）→ document（文件）→ field（欄位）**，集合與文件交替，文件底下可再開 subcollection。
- 每筆資料有**路徑**，奇數段指集合、偶數段指文件——對應程式碼裡 `collection()` / `doc()` 交替。
- 和 SQL 三大差異：**沒有 JOIN**、**無強制 schema**、**查詢能力受限**（換來大規模下的可預測效能）。
- NoSQL 建模是**先想查詢、再設計結構**，且常故意**反正規化**（重複資料）。
- 單文件上限 **1 MiB**；大檔進 Storage；刪文件**不會**自動刪子集合。

## 自我檢核

- [ ] 我能畫出 collection / document / field / subcollection 的階層，並說出交替規律
- [ ] 給一個路徑（如 `posts/p1/comments`），我能判斷它指向集合還是文件
- [ ] 我能說出 Firestore 和 SQL 的三個關鍵差異，特別是「沒有 JOIN」的意義
- [ ] 我理解「無強制 schema」的彈性與代價（一致性要自己維護）
- [ ] 我知道 map 欄位和 subcollection 各適合什麼情況
- [ ] 我知道刪文件不會刪它的子集合這個坑

## 延伸閱讀

### 官方文件

- **[Cloud Firestore Data Model](https://firebase.google.com/docs/firestore/data-model)**
  - **讀哪裡**：整篇，這就是本章的官方權威版，含 documents / collections / references / 階層資料的完整說明。
  - **能學到什麼**：本章每個概念的官方定義與更多例子，特別是 reference 型別和階層資料的細節。
  - **前提**：本章讀完即可。

- **[Choose a data structure](https://firebase.google.com/docs/firestore/manage-data/structure-data)**
  - **讀哪裡**：巢狀資料、subcollection、根層集合三種結構的取捨那幾段。
  - **能學到什麼**：map vs subcollection vs 獨立集合的官方建議，Ch 12 建模的前導。

### 文章

- **[Fireship — Firestore Data Modeling 系列](https://fireship.io/lessons/advanced-firestore-nosql-database-guide/)** — Jeff Delaney
  - **這篇說什麼**：從 NoSQL 思維出發講 Firestore 建模，含「沒有 JOIN 怎麼辦」的實際模式。
  - **為什麼值得讀**：把「先想查詢再建模」的心態講得很透，是 Ch 12 的絕佳預習。
  - **前提**：本章讀完即可。

- **[The Firebase Blog — Firestore is not a SQL database（觀念文）](https://firebase.blog/)** — Firebase 團隊
  - **這篇說什麼**：在部落格搜尋 Firestore 資料建模相關文章，理解為什麼查詢這樣設計。
  - **為什麼值得讀**：從「為什麼故意這樣限制」的角度理解 Firestore，比死記限制更有用。

心智模型建好了。下一章開始動手——我們來寫資料：新增、設定、更新、刪除。你會學到 `addDoc` 和 `setDoc` 的關鍵差別，以及怎麼用有意義的 ID。

→ [Ch 9 — 寫入資料](./09-firestore-write.md)
