# Ch 17 — 常見規則模式

> **目標**：把 Ch 16 的語法變成實戰武器——學會最常用的幾個規則模式：「只能讀寫自己的資料」「欄位驗證（型別、必填、範圍）」「擁有者才能改/刪」「角色權限（管理員）」，並用 `function` 把重複邏輯抽出來。學完你能為留言板寫出真正安全的規則。

> **環境**：規則語言 v2。本章所有規則模式以 `@firebase/rules-unit-testing` v5.0.2 + Firestore 模擬器實跑驗證。

## 為什麼需要這個？

Ch 16 教了零件，這章教怎麼組成真實的鎖。90% 的 App 需要的規則，就是這幾個模式的組合。把它們變成你的肌肉記憶，寫規則就從「每次查半天」變成「套模式」。

## 模式 1：只能讀寫自己的資料

最基礎、最常用。使用者的個人資料 `users/{uid}`，只有本人能碰：

```
match /users/{userId} {
  allow read, write: if request.auth != null
                     && request.auth.uid == userId;
}
```

拆解：`request.auth != null`（登入了）**且** `request.auth.uid == userId`（路徑上的 userId 就是他自己）。實測：

```
[PASS] alice 能寫自己的 /users/alice
[PASS] alice 被拒絕寫 /users/bob
[PASS] 未登入被拒絕寫 /users/alice
```

> **為什麼要先檢查 `request.auth != null`？** 因為未登入時 `request.auth` 是 null，直接讀 `request.auth.uid` 會出錯（整條規則變成拒絕，雖然結果一樣是擋掉，但先明確判斷 null 更清楚、也避免某些邊界問題）。習慣把 `request.auth != null` 放最前面當「門檻」。

## 模式 2：公開讀、擁有者才能寫

貼文、留言這類「大家都能看，但只有作者能改/刪」的資料。這需要 Ch 16 的**操作細分**：

```
match /posts/{postId} {
  allow read: if true;                                    // 誰都能讀

  allow create: if request.auth != null
                && request.resource.data.owner == request.auth.uid;  // 只能以自己為 owner 建立

  allow update, delete: if request.auth != null
                        && resource.data.owner == request.auth.uid;  // 只能改/刪自己的
}
```

注意 create 和 update/delete 用的物件不同（Ch 16 的重點）：

- **create**：文件還不存在，沒有「舊資料」，用 `request.resource.data.owner`（要寫入的新值），要求「你只能建立 owner 是自己的貼文」——防止有人建立一篇冒名別人的貼文。
- **update/delete**：文件已存在，用 `resource.data.owner`（現有的值），要求「你只能動 owner 是自己的貼文」。

實測：

```
[PASS] alice 建立 owner=alice 的貼文（允許）
[PASS] alice 被拒絕建立 owner=bob 的貼文（想冒名，擋下）
[PASS] bob 被拒絕刪除 alice 的貼文（不是他的，擋下）
[PASS] 匿名能讀貼文（read: if true）
```

**這就是練習 B 留言板缺的那道鎖**——前端藏刪除鈕擋不住攻擊者（Ch 15），但這條 `allow delete: if resource.data.owner == request.auth.uid` 在資料庫層真正擋住「刪別人的」。

## 模式 3：欄位驗證（型別、必填、範圍）

因為 Firestore 沒有強制 schema（Ch 8），規則要兼任「資料驗證」——確保寫入的資料**長得對**，擋掉垃圾/惡意資料：

```
match /posts/{postId} {
  allow create: if request.auth != null
    && request.resource.data.owner == request.auth.uid
    && request.resource.data.title is string                    // title 必須是字串
    && request.resource.data.title.size() > 0                   // 不能空
    && request.resource.data.title.size() <= 100                // 長度上限
    && request.resource.data.likes == 0                         // 新貼文按讚數必須從 0 開始
    && request.resource.data.keys().hasOnly(['owner','title','likes','createdAt']);
    //   ▲ 只允許這些欄位，擋掉塞奇怪欄位
}
```

常用驗證工具：

| 需求 | 寫法 |
|---|---|
| 型別檢查 | `x is string` / `is int` / `is number` / `is bool` / `is timestamp` / `is list` / `is map` |
| 字串長度 | `x.size() > 0 && x.size() <= 100` |
| 數值範圍 | `x >= 0 && x <= 5` |
| 必填欄位存在 | `'field' in request.resource.data` 或 `request.resource.data.keys().hasAll(['a','b'])` |
| 只允許特定欄位 | `request.resource.data.keys().hasOnly(['a','b','c'])` |
| 值必須是某幾個之一 | `x in ['active','pending','done']` |

實測型別驗證有效：

```
[PASS] alice 被拒絕建立 title 為數字（123）的貼文  ← title is string 擋下
```

> **為什麼欄位驗證在 Firebase 特別重要？** 傳統架構有後端幫你驗證資料。Firebase 前端直連、前端驗證可繞過（Ch 15），所以「確保存進資料庫的資料是乾淨的」這件事，**唯一可靠的地方就是安全規則**。不驗證 = 攻擊者能塞任何形狀的垃圾進你的資料庫。這是規則的第二大職責（第一是存取控制）。

## 模式 4：用 function 抽出重複邏輯

規則會重複（每個 match 都要 `request.auth != null && ...`）。用 `function` 抽出來，可讀又好維護：

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    function isSignedIn() {
      return request.auth != null;
    }
    function isOwner(userId) {
      return isSignedIn() && request.auth.uid == userId;
    }
    function isValidPost() {
      let data = request.resource.data;
      return data.title is string && data.title.size() > 0 && data.title.size() <= 100;
    }

    match /users/{userId} {
      allow read, write: if isOwner(userId);
    }

    match /posts/{postId} {
      allow read: if true;
      allow create: if isSignedIn()
                    && request.resource.data.owner == request.auth.uid
                    && isValidPost();
      allow update, delete: if isOwner(resource.data.owner);
    }
  }
}
```

`function` 可以帶參數、可以呼叫其他 function、可以用 `let` 定義區域值（規則裡唯一能用 `let` 的地方是 function 內）。把 `isSignedIn()`、`isOwner()` 這種抽出來，整份規則立刻清爽，也不容易漏改。**任何重複出現兩次以上的條件，抽成 function。**

## 模式 5：角色權限（管理員）

「只有管理員能刪任何貼文」——有兩種做法：

### 做法 A：custom claims（推薦，快）

在 token 裡放一個 `admin` 標記（Ch 4 提過），規則直接讀：

```
function isAdmin() {
  return request.auth != null && request.auth.token.admin == true;
}
match /posts/{postId} {
  allow delete: if isAdmin() || isOwner(resource.data.owner);  // 管理員或本人可刪
}
```

`request.auth.token.admin` 就是 token payload 裡的自訂 claim。**這個 claim 只能用 Admin SDK 在後端設定**（`admin.auth().setCustomUserClaims(uid, {admin: true})`），使用者**無法自己塞**——這是它安全的關鍵。優點：判斷不用額外讀資料庫、快。缺點：改角色要等使用者 token 刷新（最多 1 小時）才生效。

### 做法 B：讀角色文件（靈活，較慢）

在 Firestore 存一個 `roles/{uid}` 文件記角色，規則用 `get()` 去讀：

```
function isAdmin() {
  return request.auth != null
    && get(/databases/$(database)/documents/roles/$(request.auth.uid)).data.role == 'admin';
}
```

優點：改角色立即生效、角色資訊靈活。缺點：**每次判斷都 `get()` 一次，算一次讀取計費、也增加延遲**（Ch 16 進階提過）。適合角色常變、或需要複雜角色資料的情況。

> **怎麼選？** 簡單的 admin 布林、不常變 → **custom claims**（做法 A，省錢快）。角色複雜、常變、要立即生效 → **讀文件**（做法 B，但注意每次 get 的成本）。多數 App 用 custom claims 就夠。

## 完整範例：留言板的安全規則

把模式組合起來，為練習 B 的留言板寫一套真正的規則：

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    function isSignedIn() { return request.auth != null; }

    match /messages/{messageId} {
      // 登入者能讀（列出）留言
      allow read: if isSignedIn();

      // 發言：必須登入、authorId 是自己、內容合法
      allow create: if isSignedIn()
        && request.resource.data.authorId == request.auth.uid
        && request.resource.data.text is string
        && request.resource.data.text.size() > 0
        && request.resource.data.text.size() <= 500;

      // 只能刪自己的留言（前端藏鈕擋不住的，這裡真正擋住）
      allow delete: if isSignedIn()
        && resource.data.authorId == request.auth.uid;

      // 不給改（留言不可編輯）—— 沒寫 allow update，預設拒絕
    }
  }
}
```

這份規則保證：只有登入者能發言、發言者不能冒用別人的 `authorId`、內容必須是 1–500 字的字串、只能刪自己的、不能改任何留言。**現在攻擊者就算繞過前端直接 `deleteDoc` 別人的留言，也會被 `permission-denied` 擋下。**

## 踩雷集錦

1. **create 用 `resource.data`、update 用 `request.resource.data` 用反**：create 沒有舊資料（`resource` 是 null），要用 `request.resource.data`；判斷「現有擁有者」要用 `resource.data`。用反不是報錯就是開後門（Ch 16 踩雷第 1、2 條，實戰最常犯）。
2. **只做存取控制、不做欄位驗證**：擋住了「誰能寫」，但沒驗「寫什麼」——攻擊者能以合法身分塞垃圾資料（超長字串、錯型別、多餘欄位）。存取控制 + 欄位驗證**兩者都要**。
3. **`authorId`/`owner` 不驗證來源**：create 時若不寫 `request.resource.data.authorId == request.auth.uid`，使用者能建立一筆 `authorId` 是別人的資料，之後「只能改自己的」規則就形同虛設（因為他能一開始就冒名）。**寫入時就要釘死 owner 是自己**。
4. **custom claims 以為前端能設**：`admin` claim 只能後端 Admin SDK 設，前端設不了（不然人人自封管理員）。規則能**讀** claim，但 claim 的**寫**在後端。
5. **`get()` 濫用**：每個 `get()` 都計費 + 延遲。在高頻路徑用 `get()` 判斷角色，帳單和效能都會痛。能用 custom claims 就別用 get。
6. **忘記「沒寫 allow = 拒絕」**：想禁止某操作（如禁止改留言），**不用**寫任何東西——不寫 `allow update` 就自動拒絕。別去找「怎麼寫 deny」（沒有 deny）。
7. **function 裡想用迴圈或複雜邏輯**：規則語言受限，function 只能是「回傳布林的表達式 + `let`」，不能迴圈。複雜驗證要拆成多個條件用 `&&` 串。

## 進階：再往深一層

- **驗證「只改了允許的欄位」**：update 時可以用 `request.resource.data.diff(resource.data).affectedKeys().hasOnly(['likes'])` 來要求「這次更新只動了 likes 欄位」——防止使用者在「按讚」的同時偷改別的欄位。`diff()` 比對新舊資料的差異，是進階但很有用的驗證。
- **時間戳驗證**：`request.resource.data.createdAt == request.time` 可以要求「使用者寫入的 createdAt 必須等於伺服器時間」，防止偽造時間。搭配 `serverTimestamp()`（Ch 9），規則和寫入端一致。
- **巢狀資料與子集合的規則**：subcollection 要另外寫 match（`match /posts/{postId}/comments/{commentId}`）——父文件的規則**不會**自動套到子集合。這是常見疏漏：以為保護了 posts 就保護了它的 comments，其實沒有。
- **規則的可測試性驅動設計**：好的規則應該能被單元測試覆蓋（Ch 18）。如果你的規則複雜到難以測試，通常代表授權模型太複雜，該考慮簡化資料結構（把授權需要的資訊，如 owner，直接存進文件，Ch 12）。**規則好不好寫，是資料建模好不好的一面鏡子。**
- **read 規則與查詢的配合**：`allow read: if resource.data.owner == request.auth.uid`（只能讀自己的）配合查詢時，你的查詢**必須**加 `where("owner","==",myUid)`，否則因為「規則不過濾查詢」（Ch 16），查整個集合會被整個拒絕。規則和查詢要成對設計。

## 本章重點整理

- **模式 1**：`request.auth.uid == userId` → 只能讀寫自己的資料。
- **模式 2**：`read: if true` + `create/update/delete` 用 owner 比對 → 公開讀、擁有者才能寫；create 用 `request.resource.data.owner`、update/delete 用 `resource.data.owner`。
- **模式 3 欄位驗證**：`is string`、`.size()`、`hasOnly()` 等——因為沒有強制 schema，規則兼任資料驗證，擋垃圾/惡意資料。
- **模式 4**：用 `function`（可帶參數、可 `let`）抽出重複邏輯（`isSignedIn()`、`isOwner()`）。
- **模式 5 角色**：custom claims（`request.auth.token.admin`，後端設、快）或讀角色文件（`get()`，靈活但計費）。
- **create 時就要釘死 owner/authorId 是自己**，否則「只能改自己的」會被冒名繞過。

## 自我檢核

- [ ] 我能寫出「只能讀寫自己 `/users/{uid}`」的規則
- [ ] 我能寫出「公開讀、只有作者能改刪」的規則，且 create 和 update 用對了 request.resource / resource
- [ ] 我能加上欄位驗證（型別、長度、只允許特定欄位）
- [ ] 我知道為什麼 create 時要驗證 `owner == request.auth.uid`（防冒名）
- [ ] 我能用 `function` 抽出重複條件
- [ ] 我知道角色權限兩種做法（claims vs get 文件）的取捨
- [ ] 我記得子集合要另外寫規則、父規則不會自動套

## 延伸閱讀

### 官方文件

- **[Writing conditions for Security Rules](https://firebase.google.com/docs/firestore/security/rules-conditions)**
  - **讀哪裡**：「Data validation」和「Access other documents（get/exists）」兩節，對應本章模式 3、5。
  - **能學到什麼**：欄位驗證和跨文件判斷的完整官方寫法。
  - **前提**：本章讀完即可。

- **[Firestore security rules 最佳實踐與範例](https://firebase.google.com/docs/rules/basics)**
  - **讀哪裡**：常見情境（owner-based、role-based、attribute-based）的官方範例。
  - **能學到什麼**：本章模式的官方版與更多變體，寫規則時的範本庫。

- **[Rules 語言 — diff、affectedKeys 等進階函式](https://firebase.google.com/docs/reference/rules/rules.firestore.Resource)**
  - **讀哪裡**：`data.diff()` 與相關方法。
  - **能學到什麼**：本章進階提到的「只允許改特定欄位」驗證。

### 影片 / 文章

- **[Fireship — Firestore Security Rules 實戰](https://fireship.io/lessons/firestore-security-rules-recipes/)** — Jeff Delaney
  - **這篇說什麼**：一份「規則食譜」，把常見情境的規則直接給你抄。
  - **為什麼值得讀**：本章模式的擴充版，遇到新情境時的查閱清單。
  - **前提**：本章讀完即可。

規則會寫了，但你怎麼**確定**它真的對？手動一條條試又慢又容易漏。下一章教你用官方測試工具，把規則當程式碼一樣寫單元測試——自動驗證「該擋的擋、該放的放」，這也是本課驗證所有規則的方法。

→ [Ch 18 — 測試規則](./18-testing-rules.md)
