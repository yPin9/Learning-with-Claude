# Ch 10 — 讀取與查詢

> **目標**：學會從 Firestore 讀資料——`getDoc` 讀單筆、`getDocs` 讀一批、用 `query` 搭 `where`/`orderBy`/`limit` 做查詢，並搞懂 Firestore 查詢那些「SQL 覺得理所當然、這裡卻做不到」的限制與原因。

> **環境**：Firebase JS SDK v12.18.0。本章所有讀取/查詢與輸出都用 Firestore 模擬器實跑驗證。

## 為什麼需要這個？

寫進去的資料要拿得出來才有用。但 Firestore 的「讀」分兩種層次：**用路徑直接拿一筆**（你知道 ID）和**用條件查一批**（你不知道 ID、要篩選）。而查詢正是 Firestore 和 SQL 差最多的地方——很多 SQL 一行的查詢，這裡要嘛要建索引、要嘛得換寫法、要嘛根本做不到。這章讓你知道哪些查得到、哪些查不到、以及為什麼。

> 本章講的是**一次性讀取**（讀當下的值）。Firestore 更強大的**即時監聽**（值一變就自動通知）留到 Ch 11。先掌握一次性讀取，即時只是把 `getDoc` 換成 `onSnapshot`。

## 先建立直覺：兩種「讀」

```
   我知道 ID（路徑）嗎？
     ├─ 知道 → 直接「讀」這一筆        getDoc(doc(db, "users", uid))
     │         像用檔案路徑打開檔案
     │
     └─ 不知道 → 用條件「查」一批      getDocs(query(collection..., where...))
               像用搜尋條件找檔案
```

**能用路徑直接讀，就別查詢**——直接讀更快、更便宜、不用索引。這也是 Ch 9 說「用 uid 當 ID」的好處：你天生知道路徑，永遠走左邊那條。

## 讀單筆：getDoc

```js
import { getFirestore, doc, getDoc } from "firebase/firestore";
const db = getFirestore(app);

const snap = await getDoc(doc(db, "posts", "fixed-id"));
if (snap.exists()) {
  console.log("標題:", snap.data().title);
} else {
  console.log("這筆不存在");
}
```

實測：對一筆存在、含 `serverTimestamp` 的文件：

```
[getDoc] exists: true | title: Fixed | createdAt is Timestamp: true
```

對不存在的文件：

```
[getDoc-miss] exists: false | data(): undefined
```

關鍵：

- `getDoc` 回傳一個 **DocumentSnapshot**（快照），不是資料本身。
- **一定要先 `snap.exists()`** 判斷存不存在，再 `snap.data()` 取資料。
- **文件不存在時 `getDoc` 不會報錯**，而是回一個 `exists()` 為 `false` 的快照，`data()` 是 `undefined`。不判斷就直接 `snap.data().title` 會噴 `Cannot read properties of undefined`。這是新手常見錯誤。

## 讀一批 + 查詢：query + getDocs

不知道 ID、要按條件篩，用 `query` 組條件、`getDocs` 執行：

```js
import { collection, query, where, orderBy, limit, getDocs } from "firebase/firestore";

const q = query(
  collection(db, "items"),
  where("score", ">", 10),      // 篩選：score 大於 10
  orderBy("score", "desc"),     // 排序：score 由大到小
  limit(2)                       // 限量：只要前 2 筆
);
const qs = await getDocs(q);
console.log("筆數:", qs.size);
qs.forEach(d => console.log(d.id, d.data().name, d.data().score));
```

實測（資料為 p1=10, p2=30, p3=20）：

```
[query] count: 2 | names: p2,p3
```

`score > 10` 篩掉 p1(=10，不含)，剩 p2(30)、p3(20)，依 desc 排序後 limit 2 → **p2, p3**。逐項解讀：

- `collection(db, "items")`：要查的集合。
- `where(欄位, 運算子, 值)`：篩選條件。運算子有 `==`、`!=`、`<`、`<=`、`>`、`>=`、`in`、`not-in`、`array-contains`、`array-contains-any`。
- `orderBy(欄位, "asc"|"desc")`：排序。
- `limit(n)`：只取前 n 筆。
- `getDocs(q)` 回傳 **QuerySnapshot**，`.size` 是筆數，`.forEach` 或 `.docs` 走訪，每個是 DocumentSnapshot。

### 走訪查詢結果

```js
const qs = await getDocs(q);
// 方法一：forEach
qs.forEach(d => console.log(d.id, d.data()));
// 方法二：map 成陣列（常用，方便 render）
const items = qs.docs.map(d => ({ id: d.id, ...d.data() }));
// 空結果不會報錯，qs.empty 為 true、qs.size 為 0
if (qs.empty) console.log("沒有符合的資料");
```

`qs.docs.map(d => ({ id: d.id, ...d.data() }))` 這個慣用法很常見——把每筆文件攤平成一個帶 `id` 的物件陣列，方便丟給 UI 渲染。

## Firestore 查詢的限制（重點！和 SQL 差最多的地方）

這節是本章的核心。Firestore 為了「大規模下查詢仍然快」，故意犧牲了查詢彈性。以下限制務必記住：

### 1. 沒有「模糊搜尋」（沒有 LIKE）

Firestore **不能**做 `WHERE title LIKE '%關鍵字%'`。它只能做前綴比對的變通（`>=` + `<=` 一段範圍），全文搜尋要靠外部服務（Algolia、Typesense、或 Firebase Extension）。**「搜尋框」這種需求，Firestore 原生做不到**，這是最常見的撞牆。

### 2. 範圍查詢（`<`、`>`、`!=`）有嚴格規則

- 同一個查詢裡，**範圍/不等比較過去限定只能用在單一欄位**（新版放寬了跨欄位範圍，但仍有代價與索引要求）。
- **如果你用 `orderBy` 的欄位，和範圍 `where` 的欄位不同，需要複合索引**，且第一個 `orderBy` 必須是那個範圍欄位。實務上：`where("score",">",10)` 就常需要 `orderBy("score")`。

### 3. 複合查詢要建「複合索引」

單欄位查詢 Firestore 自動有索引。但**多個條件組合**（例如 `where("cat","==","book")` + `orderBy("price")`）需要**複合索引**——在**正式環境**第一次跑這種查詢會**報錯**，錯誤訊息裡**附一個直接建索引的連結**，點下去建好就能跑。（注意：**本機模擬器不會報這個錯**、會直接讓查詢跑過——這個「本機能跑、上線才炸」的落差是 Ch 13 的重點，先記著。）看到「query requires an index」別慌，那是正常流程。

### 4. `OR` 能力有限

跨欄位的 `OR`（`a==1 OR b==2`）以前完全不行，現在可用 `or()` 組合，但有筆數/索引限制。單欄位的「多選一」用 `in`：`where("status", "in", ["active", "pending"])`（`in` 最多 30 個值）。

### 5. 不能跨集合任意查（沒有 JOIN）

Ch 8 講過——查 `posts` 就只查 `posts`，不能一個查詢把 `posts` 和 `users` join 起來。要作者名字就得反正規化（存進 post）或多查一次（Ch 12）。

> **這些限制不是 bug，是設計**：Firestore 保證每個查詢的速度只和「回傳幾筆」有關，和「集合總共多少筆」無關。要做到這點，它只允許「能靠索引直接定位」的查詢。SQL 的彈性來自「可以掃全表算」，代價是大表會慢。Firestore 選了另一邊：**限制查詢種類，換取可預測的效能**。理解這個取捨，你就不會覺得這些限制莫名其妙。

## 分頁：startAfter

要「載入更多」，用 `limit` + `startAfter`（游標分頁），而不是 SQL 的 `OFFSET`（Firestore 沒有高效的 offset）：

```js
import { startAfter, getDocs } from "firebase/firestore";

// 第一頁
const first = query(collection(db, "posts"), orderBy("createdAt", "desc"), limit(10));
const firstSnap = await getDocs(first);
const lastDoc = firstSnap.docs[firstSnap.docs.length - 1];

// 下一頁：從上一頁最後一筆之後接續
const next = query(collection(db, "posts"), orderBy("createdAt", "desc"),
                   startAfter(lastDoc), limit(10));
const nextSnap = await getDocs(next);
```

`startAfter(lastDoc)` 傳「上一頁最後一個文件快照」，Firestore 從它之後接續。這種**游標分頁**在大資料集下遠比 offset 高效（不用掃過前面所有筆）。

## 完整範例

```js
import { getFirestore, collection, doc, getDoc, getDocs,
         query, where, orderBy, limit } from "firebase/firestore";
const db = getFirestore(app);

// A. 知道 ID → 直接讀
const userSnap = await getDoc(doc(db, "users", "uid_alice"));
const user = userSnap.exists() ? userSnap.data() : null;

// B. 不知道 ID → 查詢：Alice 最新的 5 篇文
const q = query(
  collection(db, "posts"),
  where("authorId", "==", "uid_alice"),
  orderBy("createdAt", "desc"),
  limit(5)
);
const snap = await getDocs(q);
const posts = snap.docs.map(d => ({ id: d.id, ...d.data() }));
console.log(`Alice 有 ${posts.length} 篇文`);
```

（B 這個查詢——`where` 一個欄位 + `orderBy` 另一個欄位——正是會需要複合索引的典型，正式環境第一次跑會叫你建索引（模擬器不會），Ch 13 詳解。）

## 踩雷集錦

1. **不判斷 `exists()` 就 `.data()`**：文件不存在時 `data()` 是 `undefined`，直接取欄位會噴錯。永遠先 `if (snap.exists())`。
2. **期待 `LIKE` 模糊搜尋**：Firestore 沒有。搜尋框要接外部全文搜尋服務。這是規劃功能時就要知道的硬限制，別做到一半才發現。
3. **看到 "query requires an index" 以為壞了**：那是正常的。複合查詢要複合索引，錯誤訊息附建立連結，點一下建好即可（Ch 13）。
4. **`orderBy` 欄位和範圍 `where` 欄位不搭**：`where("a",">",1)` 配 `orderBy("b")` 會出問題——有範圍過濾時，`orderBy` 得先排那個範圍欄位。記住這個配對規則。
5. **用 offset 分頁**：Firestore 的 offset 效率差（要掃過前面所有筆且照樣計費）。用 `startAfter` 游標分頁。
6. **以為查詢會回傳整個集合**：沒加 `limit` 的查詢會把**所有符合的文件**讀出來，筆數多會慢又貴（每筆算一次讀取計費）。列表一律加 `limit` + 分頁。
7. **把「讀不到」和「不存在」搞混**：`getDoc` 回 `exists():false` 是「文件不存在」；若是**權限**問題（安全規則擋），會是 `permission-denied` **錯誤**（走 catch），不是 `exists():false`。兩者處理不同。

## 進階：再往深一層

- **只讀快取 / 來源控制**：`getDoc` 預設先看本機快取再確認伺服器。你可以用 `getDocFromCache` / `getDocFromServer` 明確指定來源。一般不用管，但需要「一定要最新」或「省流量只讀快取」時有用。
- **collection group query**：`collectionGroup(db, "comments")` 一次查**所有**叫 `comments` 的子集合（不管在哪個 post 底下），例如「這個使用者在全站的所有留言」。它需要對應的 collection group 索引。這是 Firestore 階層模型的強大延伸（Ch 8 進階提過）。
- **`count()` 聚合查詢**：要「有幾筆」不想把整批讀出來（省錢），用 `getCountFromServer(query(...))` 只拿數量。Firestore 也支援 `sum()`、`average()` 聚合。這比讀全部再 `.length` 便宜太多。
- **查詢也計費、按讀取筆數算**：查詢回傳 N 筆 = N 次讀取計費（`count()` 聚合另有較便宜的計價）。所以「查詢設計」直接影響帳單，這是 Firestore 建模（Ch 12）和成本（Ch 22）的連結點。
- **`!=` 和 `not-in` 的隱藏行為**：這類「否定」查詢**不會**回傳「該欄位不存在」的文件。`where("status","!=","done")` 不會包含沒有 `status` 欄位的文件。無 schema 的世界裡這很容易咬到你。

## 本章重點整理

- 兩種讀：**知道 ID 用 `getDoc`（直接讀）**，不知道 ID 用 **`query` + `getDocs`（條件查）**。能直接讀就別查。
- `getDoc` 回快照，**先 `exists()` 再 `data()`**；不存在不報錯而是 `exists():false`。
- 查詢用 `where`/`orderBy`/`limit` 組合；結果常用 `qs.docs.map(d => ({id:d.id, ...d.data()}))` 攤平。
- Firestore 查詢**故意受限**（換可預測效能）：**沒有 LIKE 模糊搜尋**、範圍查詢有規則、複合查詢**要建複合索引**、`OR` 有限、**沒有 JOIN**。
- 分頁用 **`startAfter` 游標**，不用 offset；列表一律加 `limit`。

## 自我檢核

- [ ] 我知道什麼時候該用 `getDoc`、什麼時候該用查詢，以及為什麼「能直接讀就別查」
- [ ] 我每次 `getDoc` 後都先判斷 `exists()`
- [ ] 我能說出 Firestore 查詢至少三個 SQL 沒有的限制（LIKE、複合索引、JOIN…）
- [ ] 看到「query requires an index」我知道那是正常流程、該怎麼辦
- [ ] 我知道要做分頁用 `startAfter` 而不是 offset
- [ ] 我理解「查詢受限」是為了換「和資料庫大小無關的可預測效能」

## 延伸閱讀

### 官方文件

- **[Get data with Cloud Firestore](https://firebase.google.com/docs/firestore/query-data/get-data)**
  - **讀哪裡**：「Get a document」和「Get multiple documents from a collection」，對照本章的 `getDoc`/`getDocs`。
  - **能學到什麼**：官方讀取範例，含 source（cache/server）選項。

- **[Perform simple and compound queries](https://firebase.google.com/docs/firestore/query-data/queries)**
  - **讀哪裡**：整篇，特別是「Query limitations」那節——本章限制清單的官方權威版。
  - **能學到什麼**：所有查詢運算子、`in`/`array-contains`/`or()` 的用法與上限、以及每個限制的官方說明。
  - **前提**：本章讀完即可。

- **[Paginate data with query cursors](https://firebase.google.com/docs/firestore/query-data/query-cursors)**
  - **讀哪裡**：`startAfter`/`startAt`/`endBefore` 那幾段。
  - **能學到什麼**：游標分頁的完整做法，做「載入更多」必讀。

### 文章

- **[Fireship — Firestore 查詢與資料建模](https://fireship.io/lessons/advanced-firestore-nosql-database-guide/)** — Jeff Delaney
  - **這篇說什麼**：從查詢限制反推「該怎麼設計資料」，是 Ch 12 的前導。
  - **為什麼值得讀**：把「查詢限制」和「資料建模」連起來，讓你理解限制其實在引導你怎麼存資料。

一次性讀取會了。但 Firestore 真正的魔法在下一章——**即時同步**：不是「讀當下的值」，而是「訂閱這份資料，只要它一變（不管誰改的、從哪台裝置），你的畫面自動更新」。這是讓你的 App 有「即時感」的核心，也是 Firestore 最值錢的功能。

→ [Ch 11 — 即時同步：onSnapshot 與底層機制](./11-firestore-realtime.md)
