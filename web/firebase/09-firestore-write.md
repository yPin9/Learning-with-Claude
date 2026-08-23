# Ch 9 — 寫入資料

> **目標**：學會 Firestore 的四種寫入操作——`addDoc`（新增自動 ID）、`setDoc`（指定 ID 建立/覆蓋）、`updateDoc`（局部更新）、`deleteDoc`（刪除），搞清楚 `addDoc` vs `setDoc` 的關鍵差別，並用上 `serverTimestamp`、`increment` 這些特殊值。

> **環境**：Firebase JS SDK v12.18.0。本章所有寫入操作與輸出都用 Firestore 模擬器實跑驗證。

## 為什麼需要這個？

Ch 3 你用過 `addDoc` 寫了一筆，但寫入其實有四種操作，各有適用場景。用錯了會出現「我只想改一個欄位，結果整份資料被蓋掉」這種災難。這章把四種寫入講清楚，讓你每次都選對工具。

## 先建立直覺：四種寫入 = 兩個問題的組合

四種寫入操作，其實是回答兩個問題的組合：

```
   問題一：ID 誰決定？
     ├─ Firestore 自動給 → addDoc
     └─ 我自己指定       → setDoc / updateDoc

   問題二：是整份覆蓋，還是只改部分？
     ├─ 整份建立/覆蓋 → setDoc
     └─ 只改指定欄位   → updateDoc
```

| 操作 | ID | 行為 | 文件不存在時 |
|---|---|---|---|
| `addDoc` | 自動產生 | 新增一筆 | — |
| `setDoc` | 你指定 | **整份**寫入/覆蓋 | 建立 |
| `setDoc(..., {merge:true})` | 你指定 | 合併（只動有給的欄位） | 建立 |
| `updateDoc` | 你指定 | **局部**更新指定欄位 | **報錯** |
| `deleteDoc` | 你指定 | 刪除整份文件 | 無事發生 |

## addDoc：新增，讓 Firestore 給 ID

適合「一直往裡面加新東西、每筆各自獨立」的場景——留言、貼文、log。

```js
import { getFirestore, collection, addDoc, serverTimestamp } from "firebase/firestore";
const db = getFirestore(app);

const docRef = await addDoc(collection(db, "posts"), {
  title: "Hello",
  likes: 0,
  tags: ["a", "b"]
});
console.log("自動產生的 ID 長度:", docRef.id.length);
```

實測輸出：

```
[addDoc] auto id length: 20
```

`addDoc` 回傳一個 **DocumentReference**，`docRef.id` 是 Firestore 自動配的 **20 字元**隨機 ID。這種 ID 的好處是**天生分散**、不會撞、適合高頻寫入。

## setDoc：用「我指定的 ID」建立或覆蓋

適合「這筆資料的 ID 我心裡有數」的場景——最經典的是**用 `uid` 當 user 文件的 ID**（Ch 4 說的「用 uid 綁」）。

```js
import { doc, setDoc } from "firebase/firestore";

await setDoc(doc(db, "posts", "fixed-id"), {
  title: "Fixed",
  likes: 5,
  createdAt: serverTimestamp()
});
```

實測：這行成功在 `posts/fixed-id` 建立文件。注意兩點：

1. **`doc(db, "posts", "fixed-id")`**：偶數段路徑，指向一個**特定文件**（Ch 8 的路徑規律）。ID 由你決定。
2. **`setDoc` 是「整份覆蓋」**：如果 `fixed-id` 已存在，這行會用新內容**完全取代**舊的——舊文件有、新資料沒給的欄位會**消失**。這是最常見的災難來源（踩雷第 1 條）。

**為什麼用 uid 當 ID 很爽**：

```js
// 用 uid 當 user 文件 ID
await setDoc(doc(db, "users", user.uid), { name: "Alice", bio: "..." });

// 之後要拿 Alice 的資料，不用「查詢」，直接用路徑「讀」：
// doc(db, "users", user.uid) —— 因為你知道 ID 就是 uid
```

這比「用 addDoc 隨機 ID，之後還要用 `where("uid","==",...)` 去查」省事太多。**只要你「天生就知道 ID 該是什麼」（uid、email、slug…），就用 `setDoc` 指定 ID。**

### setDoc 的 merge：只想補欄位、不想覆蓋

如果你想「這筆存在就補欄位、不存在就建立」，加 `{ merge: true }`：

```js
await setDoc(doc(db, "users", uid), { lastLogin: serverTimestamp() }, { merge: true });
// 只更新/新增 lastLogin，其他欄位原封不動；文件不存在就建一份只有 lastLogin 的
```

merge 版的 `setDoc` 很安全——它不會像預設 `setDoc` 那樣把整份蓋掉。

## updateDoc：只改指定欄位（文件必須已存在）

適合「這筆已經存在，我只想動其中幾個欄位」。

```js
import { updateDoc, increment } from "firebase/firestore";

await updateDoc(doc(db, "posts", "fixed-id"), {
  likes: increment(3)   // 特殊值：在現有值上 +3，不用先讀
});
```

實測：`fixed-id` 原本 `likes: 5`，執行後：

```
[updateDoc+increment] likes now: 8
```

兩個重點：

1. **`updateDoc` 只動你給的欄位**，其他不變——這是它和預設 `setDoc` 的關鍵差別。「只改一個欄位」用 `updateDoc`（或 merge `setDoc`），**別用**預設 `setDoc`。
2. **`updateDoc` 要求文件已存在**，不存在會報錯（`No document to update`）。不確定存不存在就用 `setDoc(..., {merge:true})`。

### increment：不用先讀就能加減

`increment(3)` 是一個**特殊值**，意思是「把這個欄位的現值加 3」。為什麼不自己 `讀出來 + 3 再寫回`？因為那樣有**競態條件**——兩個人同時按讚，各自讀到 5、各自寫回 6，結果少算一次（應該是 7）。`increment` 是在**伺服器端原子地**加，不會漏算。並發計數（讚數、瀏覽數）一定要用它。

> 同類的原子操作還有 `arrayUnion(x)`（往陣列加元素、去重）、`arrayRemove(x)`（移除元素）、`deleteField()`（刪掉某個欄位）。它們都是「不用先讀就能安全改」的伺服器端操作。

## deleteDoc：刪除文件

```js
import { deleteDoc } from "firebase/firestore";
await deleteDoc(doc(db, "posts", "fixed-id"));
```

實測（在批次操作裡驗證）：刪除後該文件 `exists()` 為 `false`。

⚠️ **重申 Ch 8 的坑**：刪一個文件**不會**刪掉它的 subcollection。刪 `posts/p1` 不會刪 `posts/p1/comments` 底下的留言，它們變孤兒。要清乾淨得**先遞迴刪子集合**再刪父文件（前端沒有內建遞迴刪除，通常靠 Cloud Functions 或 CLI 工具做）。

## serverTimestamp：用伺服器的時間，不是使用者的

```js
await addDoc(collection(db, "posts"), {
  text: "hi",
  createdAt: serverTimestamp()   // 不是 new Date()！
});
```

實測：存進去後這個欄位變成一個 Firestore 的 **`Timestamp`** 物件（`createdAt instanceof Timestamp` 為 `true`）。

**為什麼不用 `new Date()`？** `new Date()` 是**使用者電腦**的時間——使用者的時鐘可能不準、可能被改、時區不同。`serverTimestamp()` 是一個指令，叫 **Firebase 伺服器在寫入當下填上它的時間**，全世界所有寫入都用同一個權威時鐘。任何「排序、記錄發生時間」的欄位都該用它。

## 完整範例：寫入的四種操作一次看

```js
import { getFirestore, collection, addDoc, doc, setDoc, updateDoc,
         deleteDoc, serverTimestamp, increment } from "firebase/firestore";
const db = getFirestore(app);

// 1. addDoc：新增留言，ID 自動
const ref = await addDoc(collection(db, "comments"), {
  text: "第一則留言", createdAt: serverTimestamp()
});
console.log("新留言 ID:", ref.id);

// 2. setDoc：用 uid 當 ID 建立使用者資料
await setDoc(doc(db, "users", "uid_alice"), {
  name: "Alice", postCount: 0
});

// 3. updateDoc：Alice 發了一篇文，postCount +1
await updateDoc(doc(db, "users", "uid_alice"), {
  postCount: increment(1)
});

// 4. setDoc merge：更新最後登入時間，不動其他欄位
await setDoc(doc(db, "users", "uid_alice"), {
  lastLogin: serverTimestamp()
}, { merge: true });

// 5. deleteDoc：刪掉那則留言
await deleteDoc(ref);
```

## 對比與取捨：我到底該用哪個？

| 我想做的事 | 用 | 為什麼 |
|---|---|---|
| 加一筆新的、ID 隨便 | `addDoc` | 高頻新增、ID 天生分散 |
| 建一筆、ID 我知道（uid…） | `setDoc` | 之後可直接用路徑讀，不用查 |
| 改幾個欄位、文件已存在 | `updateDoc` | 只動指定欄位，其他不變 |
| 改幾個欄位、不確定存不存在 | `setDoc(...,{merge:true})` | 存在就合併、不存在就建立 |
| 整份取代 | `setDoc`（不加 merge） | 明確要覆蓋全部 |
| 計數 +1 / 陣列增減 | `increment`/`arrayUnion` 等 | 原子操作，避免競態 |
| 刪 | `deleteDoc` | 記得子集合要另外處理 |

## 踩雷集錦

1. **用預設 `setDoc` 只想改一個欄位，結果整份被蓋掉**：`setDoc(doc, { likes: 5 })` 會把這份文件變成**只有** `likes` 一個欄位，其他全消失。只改部分用 `updateDoc` 或 `setDoc(..., {merge:true})`。這是新手最痛的一課。
2. **`updateDoc` 一個不存在的文件**：報 `No document to update`。不確定存不存在，用 merge 版 `setDoc`。
3. **自己讀值 +1 再寫回**：並發時會漏算。計數用 `increment()`，它在伺服器端原子執行。
4. **用 `new Date()` 存時間**：那是使用者本機時間，不可靠。用 `serverTimestamp()`。
5. **以為 `deleteDoc` 會連子集合一起刪**：不會，子集合會變孤兒。要遞迴清。
6. **忘記寫入是非同步、沒 await**：`addDoc(...)` 不 await，後面立刻用 `docRef` 會拿到 pending 的 Promise。所有寫入都要 `await`（或 `.then`）。
7. **把 `doc()` 和 `collection()` 用反**：`addDoc` 吃 collection（`collection(db,"posts")`），`setDoc`/`updateDoc` 吃 document（`doc(db,"posts","id")`）。用反了路徑段數就錯（Ch 8 的奇偶規律）。

## 進階：再往深一層

- **寫入的「離線」行為**：SDK 有離線快取——你 `await addDoc` 在**沒網路**時，Promise 其實會**先在本機記下**、等連線再送出，本機的即時監聽（Ch 11）會立刻反映這個「還沒真的上傳」的變更（樂觀更新）。這對行動 App 體驗極好，但要注意：`await` 回來不代表「一定寫進雲端了」，只代表「已排入」。要確認真的落地，得看伺服器確認或監聽器的 `metadata.hasPendingWrites`。
- **document ID 的限制**：自訂 ID 不能是空字串、不能包含 `/`（會被當路徑分隔）、不能是 `.` 或 `..`、長度有上限、且不建議用會讓寫入熱點集中的遞增 ID（像 `0001,0002...` 會集中寫在同一個索引範圍，大規模下變慢）。隨機 ID（`addDoc`）或雜湊過的 ID 才分散。
- **批次與交易**：要「一次寫多筆、全成或全敗」，用 `writeBatch`（批次）或 `runTransaction`（交易）——例如「發文的同時把使用者的 postCount +1」這種要一致的操作。這是 Ch 14 的主題，先知道有這回事，別用多個獨立 `await` 拼湊（那不保證原子性）。
- **寫入計費**：每次 `addDoc`/`setDoc`/`updateDoc`/`deleteDoc` 算**一次寫入**計費（`increment` 等特殊值不額外算）。大量寫入要留意成本，Ch 22 會談。批次寫 N 筆算 N 次，不是一次。

## 本章重點整理

- 四種寫入是「ID 誰決定 × 覆蓋還是局部」的組合：**`addDoc`**（自動 ID 新增）、**`setDoc`**（指定 ID，**整份覆蓋**）、**`updateDoc`**（局部更新，須已存在）、**`deleteDoc`**（刪除）。
- 只改部分欄位用 `updateDoc` 或 `setDoc(...,{merge:true})`——**別用預設 `setDoc`**，它會整份蓋掉。
- 「天生知道 ID」（uid 等）就用 `setDoc` 指定，之後能直接用路徑讀、不用查。
- 計數用 **`increment()`**（原子、防競態）；時間用 **`serverTimestamp()`**（伺服器權威時鐘）。
- 刪文件**不刪子集合**；所有寫入都要 `await`。

## 自我檢核

- [ ] 我能說出 `addDoc` 和 `setDoc` 的兩個關鍵差別（ID 誰決定、以及覆蓋行為）
- [ ] 我知道「只改一個欄位」為什麼不能用預設 `setDoc`，該用什麼
- [ ] 我能解釋為什麼計數要用 `increment` 而不是自己讀值 +1
- [ ] 我知道為什麼時間要用 `serverTimestamp` 而不是 `new Date()`
- [ ] 我記得刪文件不會刪子集合這個坑
- [ ] 給一個寫入需求，我能選對四種操作中的哪一個

## 延伸閱讀

### 官方文件

- **[Add data to Cloud Firestore](https://firebase.google.com/docs/firestore/manage-data/add-data)**
  - **讀哪裡**：整篇，涵蓋 `setDoc`、`addDoc`、`updateDoc`、merge、巢狀欄位、`serverTimestamp`、`increment`——正是本章全部內容的官方版。
  - **能學到什麼**：每個操作的完整選項，特別是巢狀欄位的點記法（`"address.city"`）更新。
  - **前提**：本章讀完即可。

- **[Delete data from Cloud Firestore](https://firebase.google.com/docs/firestore/manage-data/delete-data)**
  - **讀哪裡**：「Delete collections」那段——官方明說前端刪子集合的注意事項與建議做法。
  - **能學到什麼**：本章踩雷第 5 條「子集合孤兒」的官方解法。

### 文章

- **[The Firebase Blog — 原子操作（increment / arrayUnion）介紹](https://firebase.blog/)** — Firebase 團隊
  - **這篇說什麼**：在部落格搜 "increment" 或 "atomic"，解釋這些伺服器端操作為什麼能避免競態。
  - **為什麼值得讀**：把本章「為什麼不自己讀值 +1」講到底層，並帶到 Ch 14 交易的動機。

會寫了，接下來當然要會讀。下一章講讀取與查詢——`getDoc` 讀單筆、`getDocs` 配 `where`/`orderBy`/`limit` 做查詢，以及 Firestore 查詢那些「SQL 覺得理所當然、但這裡做不到」的限制。

→ [Ch 10 — 讀取與查詢](./10-firestore-read-query.md)
