# Ch 16 — 規則語法與運作

> **目標**：讀懂也寫得出 Firestore 安全規則——`service`/`match`/`allow` 的結構、`read`/`write` 的細分、`request` 和 `resource` 這兩個關鍵物件、路徑萬用字元與變數，以及規則到底是怎麼被評估的（哪條規則對哪個請求生效）。

> **環境**：規則語言 `rules_version = '2'`，Firestore。本章的規則行為以 `@firebase/rules-unit-testing` v5.0.2 + Firestore 模擬器實跑驗證。

## 為什麼需要這個？

Ch 15 讓你知道規則是唯一防線。這章教你這道防線的「語言」。安全規則不是 JavaScript，是一種**專用的、受限的語言**——語法看起來有點像，但規則不同。搞懂結構和那幾個核心物件，你就能讀懂任何規則、也能寫出保護自己資料的規則。

## 先建立直覺：規則是一串「守衛」

把規則想成資料庫路徑上的一排守衛，每個守衛負責一段路徑，決定「對這段路徑的這種操作，放行還是擋下」：

```
   請求：要 delete /messages/msg_123
        │
        ▼
   規則檔裡找「哪個 match 段負責 /messages/{id}」
        │
        ▼
   那段的 allow delete: if <條件>
        │
   條件成立 → 放行      條件不成立 → 擋下（permission-denied）
```

規則檔就是「一堆 match 段，每段對某路徑寫 allow 條件」。

## 骨架：service → match → allow

最小的規則檔長這樣，逐層拆解：

```
rules_version = '2';                          // ① 用第 2 版規則語言

service cloud.firestore {                     // ② 這是給 Firestore 的規則
  match /databases/{database}/documents {     // ③ 固定開頭，指向你的資料庫根
  
    match /messages/{messageId} {             // ④ 對「messages 集合下的任一文件」
      allow read: if true;                    // ⑤ 允許讀（任何人）
      allow write: if request.auth != null;   // ⑥ 允許寫（限已登入）
    }
    
  }
}
```

| 部分 | 作用 |
|---|---|
| ① `rules_version = '2'` | 宣告版本，一律用 `'2'`（第 2 版，現行標準，行為和 v1 有差異） |
| ② `service cloud.firestore` | 這份規則是給 Firestore 的（Storage 是 `firebase.storage`，Ch 19） |
| ③ `match /databases/{database}/documents` | 固定樣板，代表資料庫根，照抄即可 |
| ④ `match /messages/{messageId}` | **重點**：對哪段路徑套規則。`{messageId}` 是萬用變數，比對任一文件 ID |
| ⑤⑥ `allow <操作>: if <條件>` | 對某種操作，條件成立才允許 |

## match：對「哪段路徑」套規則

`match` 的路徑對應 Ch 8 的資料路徑，`{變數}` 會**捕獲**那一段的實際值，供條件使用：

```
match /users/{userId} {
  // {userId} 會是實際被存取的文件 ID
  // 例如請求 /users/uid_alice 時，userId == "uid_alice"
  allow read, write: if request.auth.uid == userId;
  //                                        ▲ 用捕獲的變數判斷
}
```

這就是「使用者只能存取自己的文件」的核心——把「路徑裡的 userId」和「請求者的 uid」比對。實測這條規則：

```
[PASS] alice 能寫自己的 /users/alice
[PASS] alice 被拒絕寫 /users/bob      ← alice.uid != "bob"
[PASS] 匿名（未登入）被拒絕寫 /users/alice
```

### 萬用路徑 `{name=**}`

`{document=**}` 這種 `=**` 語法匹配**任意深度**的路徑（不只一段），常用於「對整個資料庫套一條總規則」：

```
match /{document=**} {
  allow read, write: if false;   // 對所有路徑、所有深度，全部拒絕（Production mode 預設）
}
```

普通 `{id}` 只比對一段；`{id=**}` 比對這之後的任意多段。前者對「一層文件」，後者對「這底下全部」。

## 操作細分：read/write 可以拆更細

`read` 和 `write` 是粗分類，各自能拆成更細的操作，讓你精準控制：

```
   read  ┬─ get     （讀單一文件 getDoc）
         └─ list    （查詢/列出多筆 getDocs、onSnapshot 集合）

   write ┬─ create  （新增：原本不存在 → 存在）
         ├─ update  （修改：原本存在 → 改內容）
         └─ delete  （刪除：原本存在 → 不存在）
```

所以你可以寫：

```
match /posts/{postId} {
  allow get: if true;                              // 誰都能讀單篇
  allow list: if request.auth != null;             // 只有登入者能列出
  allow create: if request.auth != null;           // 登入才能發文
  allow update, delete: if request.auth.uid == resource.data.authorId;  // 只能改/刪自己的
}
```

`allow read` = `get` + `list`；`allow write` = `create` + `update` + `delete`。你可以用粗的，也可以拆細。**「只能刪自己的、但誰都能讀」這種需求，就要拆細。**

## 兩個核心物件：request 與 resource

規則的條件幾乎都圍繞這兩個物件。**搞懂它們的差別，就懂了規則的一大半。**

```
   request = 「這次請求本身」的資訊         resource = 「資料庫裡現有的資料」
   ─────────────────────────           ─────────────────────────
   request.auth       誰在請求          resource.data    這筆文件「目前」的內容
     .uid             他的 uid          （寫入前資料庫裡已經有的值）
     .token           他的 token 內容
   request.resource.data                 
     要寫入的「新」資料（僅 write 時）
   request.time       請求時間
```

**最容易混淆、最重要的對照**：

| 你想要的 | 用 | 意思 |
|---|---|---|
| 這筆文件**目前**（改之前）的擁有者 | `resource.data.owner` | 資料庫現有的值 |
| 使用者**正要寫進去**的新資料 | `request.resource.data` | 這次請求帶來的新值 |
| 誰在發這個請求 | `request.auth.uid` | 請求者身分 |

一個具體例子把三者串起來——「只有文章現有的擁有者，才能更新，且不能偷改擁有者欄位」：

```
allow update: if request.auth.uid == resource.data.owner            // 你是現有擁有者
              && request.resource.data.owner == resource.data.owner; // 且新資料沒改 owner
```

- `resource.data.owner`：這篇文**現在**的擁有者（改之前）。
- `request.auth.uid`：**誰**要改。
- `request.resource.data.owner`：這次要寫入的**新** owner 值——我們要求它和舊的相同，防止有人把 owner 改成別人再亂搞。

實測驗證這類規則有效：

```
[PASS] alice 建立 owner=alice 的貼文（request.resource.data.owner == uid，允許）
[PASS] alice 被拒絕建立 owner=bob 的貼文（想冒名，擋下）
[PASS] bob 被拒絕刪除 alice 的貼文（resource.data.owner != bob，擋下）
```

> **記憶法**：`resource`（沒有 request）= 資料庫**現有**的（舊）；`request.resource` = 這次**要寫入**的（新）。`request.auth` = **誰**。create 時沒有「舊資料」所以 `resource` 是 null（別在 create 規則用 `resource.data`，會出錯）；delete 時沒有「新資料」所以沒有 `request.resource.data`。

## 條件裡能寫什麼

`if` 後面的條件是一個布林表達式，能用：

- **比較**：`==`、`!=`、`<`、`>`、`>=`、`<=`
- **邏輯**：`&&`、`||`、`!`
- **存取欄位**：`resource.data.欄位`、`request.resource.data.欄位`
- **型別檢查**：`request.resource.data.title is string`（Ch 17 驗證用）
- **字串/清單方法**：`.size()`、`in`、`.hasAll([...])` 等
- **呼叫其他文件**：`get()`、`exists()`（進階，跨文件判斷）

但**不能**：跑迴圈、宣告變數（只能用 `function` 定義可重用條件，Ch 17）、呼叫外部 API。這是 Ch 15 說的「故意受限，保證快」。

## 規則怎麼被評估：allow 是「或」的關係

幾個關鍵評估規則，搞錯會寫出漏洞或困惑：

1. **預設拒絕**：沒有任何 `allow` 匹配到的操作，一律拒絕。你只寫「允許什麼」，不用寫「拒絕什麼」。
2. **allow 之間是 OR**：一個操作只要**任何一條** `allow` 條件成立就放行。規則裡**沒有 deny**——你不能用一條 allow 去「否決」另一條。所以別想著「先全開再擋幾個」，那做不到，要「預設關、逐條開」。
3. **巢狀 match 疊加**：外層和內層的 match 一起作用。要小心別在外層開了一個 `{document=**}: allow read` 卻在內層以為擋住了——外層的 allow 已經放行了（因為 OR）。
4. **規則不是過濾器**：這點超重要——`allow list: if <條件>` **不會**「自動只回傳符合條件的文件」。如果你的查詢可能讀到不符合規則的文件，整個查詢會被**拒絕**，而不是「過濾掉那些」。你的查詢必須「只查你有權讀的範圍」，規則才放行。這是新手大坑（下面踩雷第 5 條詳述）。

## 踩雷集錦

1. **搞混 `resource` 和 `request.resource`**：`resource.data` 是資料庫**現有**的（舊值），`request.resource.data` 是這次**要寫的**（新值）。要判斷「現有擁有者」用前者，要驗證「寫入的內容」用後者。混用是最常見的規則 bug。
2. **在 `create` 規則用 `resource.data`**：create 時文件還不存在，`resource` 是 null，讀 `resource.data.x` 會出錯。create 只能用 `request.resource.data`（新資料）和 `request.auth`。
3. **以為規則會過濾查詢結果**：不會。`allow list` 是「這個查詢整體准不准」，不是「回傳符合的那些」。查詢範圍超出你有權讀的，整個被拒。要自己在查詢加 `where` 限制到有權範圍。
4. **想用 deny 否決**：規則裡沒有 deny，allow 之間是 OR，一條放行就放行。想「限制」只能靠「不寫那條 allow」或「把 allow 條件收緊」，不能加一條 deny 去蓋掉。
5. **`allow read, write: if request.auth != null` 當安全規則**：只擋未登入，任何登入者（含匿名）能動任何資料。這幾乎等於沒設防（Ch 15 踩雷第 5 條）。要細到 uid 對應。
6. **忘記 `rules_version = '2'`**：不寫或寫 '1'，某些行為（尤其 `list` 和 `{=**}` 的語意）不同。一律用 '2'。
7. **規則語法當 JS 寫**：不能宣告變數（`let x = ...`）、不能迴圈。要重用邏輯用 `function`（Ch 17）。

## 進階：再往深一層

- **`request.auth.token` 的內容**：`request.auth.token` 是 Ch 4 那張 JWT 解出的 payload，裡面有 `email`、`email_verified`、以及自訂的 **custom claims**（如 `admin`）。所以你能寫 `allow delete: if request.auth.token.admin == true`——但 custom claims 要在後端（Admin SDK）設定，不是使用者能自己塞的（Ch 17 會提）。
- **`get()` 和 `exists()` 跨文件判斷**：規則裡可以 `get(/databases/$(database)/documents/roles/$(request.auth.uid)).data.role == 'admin'`——去**讀另一份文件**來做判斷（例如查一個「角色表」）。功能強大，但**每個 `get()` 都算一次讀取計費、也增加延遲**，且有次數上限。用於複雜授權，但別濫用。
- **v2 的 `list` 語意**：v2 裡 `get`（讀單篇）和 `list`（查詢）分開，能分別控制——例如「能讀單篇但不能列出整個集合」。這對「知道 ID 才能看、不能瀏覽全部」的隱私設計有用。
- **規則的評估順序不影響結果**：因為是 OR、預設拒絕，多條 allow 誰先誰後不影響最終「准或不准」。但可讀性上，通常把通用的放外層、特殊的放內層。
- **模擬器 vs 正式的規則一致性**：好消息——規則的評估在模擬器（`rules-unit-testing`）和正式環境**行為一致**（不像索引那樣有落差，Ch 13）。所以規則能在本地放心測（Ch 18），這是安全規則能被可靠開發的關鍵。

## 本章重點整理

- 規則結構：`rules_version='2'` → `service cloud.firestore` → `match /databases/{database}/documents` → 一堆 `match /路徑 { allow 操作: if 條件 }`。
- 操作可細分：`read`=`get`+`list`，`write`=`create`+`update`+`delete`。要「誰都能讀、只能改自己的」就拆細。
- **兩個核心物件**：`request`（這次請求：`.auth.uid` 誰、`request.resource.data` 要寫的新值）、`resource`（資料庫現有的舊值 `resource.data`）。**別混用新舊。**
- 評估規則：**預設拒絕**、**allow 之間是 OR、沒有 deny**、**規則不過濾查詢**（範圍超出權限整個查詢被拒）。
- 用 `match` 的 `{變數}` 捕獲路徑段，和 `request.auth.uid` 比對，是「只能存取自己資料」的核心。

## 自我檢核

- [ ] 我能看懂一份規則檔的 service/match/allow 結構
- [ ] 我能說出 `resource.data` 和 `request.resource.data` 的差別，以及各在什麼操作能用
- [ ] 我能寫出「使用者只能讀寫 `/users/{自己uid}`」的規則
- [ ] 我知道 `read`/`write` 怎麼細分，以及「誰都能讀、只能刪自己的」怎麼拆
- [ ] 我理解「規則不過濾查詢」——查詢範圍超出權限會整個被拒
- [ ] 我知道規則裡沒有 deny、allow 是 OR、預設拒絕

## 延伸閱讀

### 官方文件

- **[Firestore Security Rules structure](https://firebase.google.com/docs/firestore/security/rules-structure)**
  - **讀哪裡**：整篇，match/allow、路徑萬用字元、read/write 細分的官方說明。
  - **能學到什麼**：本章結構部分的權威版，含巢狀 match 和 `{=**}` 的完整語意。
  - **前提**：本章讀完即可。

- **[Rules conditions（request/resource）](https://firebase.google.com/docs/firestore/security/rules-conditions)**
  - **讀哪裡**：`request` 和 `resource` 物件的完整欄位、以及各種條件寫法。
  - **能學到什麼**：本章兩個核心物件的所有可用屬性，寫規則時的查閱手冊。

- **[Rules language reference](https://firebase.google.com/docs/reference/rules)**
  - **讀哪裡**：需要某個內建函式（`.size()`、`.hasAll()`、型別檢查…）時來查。
  - **能學到什麼**：規則語言的完整內建函式與型別，Ch 17 寫驗證時的參考。

### 影片

- **[Firebase — Security Rules 官方系列](https://www.youtube.com/results?search_query=firebase+security+rules)** — Firebase 團隊
  - **這系列說什麼**：官方用實例講 match/allow、request/resource。
  - **為什麼值得看**：把 `resource` vs `request.resource` 用動畫講清楚，比純文字好懂。

會讀會寫基本規則了。下一章進入實戰——常見的規則模式：「只能讀寫自己的資料」「驗證寫入的欄位型別和內容」「角色權限（管理員）」，把留言板從 Test mode 升級成真正安全。

→ [Ch 17 — 常見規則模式](./17-rules-patterns.md)
