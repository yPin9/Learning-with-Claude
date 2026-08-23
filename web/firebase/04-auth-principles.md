# Ch 4 — 認證原理：身分、授權、ID token / JWT

> **目標**：搞懂「登入」在背後到底發生了什麼——認證（authentication）和授權（authorization）的差別、Firebase 怎麼在「前端直連」的架構下安全地確認使用者是誰、以及那張叫 ID token（JWT）的通行證長什麼樣、怎麼運作。這章是理解 Part 2、Part 4 的地基，觀念為主、少量可驗證的程式碼。

> **環境**：本章的 token 觀察以 Firebase JS SDK v12.18.0 對 Auth 模擬器實跑，token 結構為真實輸出。

## 為什麼需要這個？

Ch 3 你做出了能寫資料的網頁，但它有個致命問題：**它不知道「誰」在寫資料，任何人都能寫任何東西。** 一個真實的 App 必須能回答兩個問題：

1. **你是誰？**（認證 / Authentication）——確認來的人是不是他宣稱的那個人。
2. **你能做什麼？**（授權 / Authorization）——確認這個人有沒有權限做這件事。

這兩件事常被混為一談，但它們是**不同層次**的問題，而且 Firebase 把它們交給**不同機制**處理。搞清楚這個分工，你才會理解為什麼「登入」（Part 2）和「安全規則」（Part 4）是分開的兩件事。

## 先建立直覺：夜店的保全與識別證

用一個場景把整個認證流程講清楚：

```
   你要進一家夜店（＝你的網頁 App 的受保護區域）

   1. 門口保全查你的證件           ← 認證：你是誰？
      「請出示身分證」
      你給身分證，保全核對照片本人
              │
              ▼
   2. 保全確認無誤，發給你一條手環   ← ID token：一張「已驗明正身」的通行證
      手環上寫著你的資訊、有防偽、有時效（今晚有效）
              │
              ▼
   3. 你到吧台點酒，出示手環         ← 之後每次行動都亮手環
      吧台不用再查一次身分證，
      看手環就知道你是誰、幾歲
              │
              ▼
   4. 但「能不能進 VIP 室」是另一回事 ← 授權：你能做什麼？
      那要看你的手環是不是 VIP 級別
      （這是另一套規則在管，不是保全發手環時決定的）
```

對照到 Firebase：

| 夜店 | Firebase |
|---|---|
| 門口保全查證件 | **Authentication 服務**驗證你的帳密 / Google 登入 |
| 發的手環 | **ID token（JWT）**——一張有時效、防偽的通行證 |
| 之後亮手環點酒 | 每次讀寫資料庫，SDK 自動附上這張 token |
| 吧台看手環知道你是誰 | 安全規則透過 token 拿到你的 `uid` |
| 能不能進 VIP 室 | **安全規則（授權）**根據 `uid` 判斷你能不能做這件事 |

**關鍵洞察**：認證（發手環）和授權（能不能進 VIP）是**兩個獨立步驟**。Firebase Auth 只負責「發手環、證明你是誰」；「你能不能動這筆資料」是**安全規則**的工作（Part 4）。很多新手以為「登入了就有權限」，錯——登入只證明身分，權限是另一關。

## 認證 vs 授權：一張表講清

| | 認證 Authentication | 授權 Authorization |
|---|---|---|
| 回答的問題 | **你是誰？** | 你**能做什麼**？ |
| 發生時機 | 登入時 | 每次存取資源時 |
| Firebase 由誰做 | **Authentication 服務** | **Security Rules**（安全規則） |
| 產物 | 一個已驗證的 `user`（含 `uid`） | 允許 / 拒絕 |
| 本課章節 | Part 2（Ch 4–7） | Part 4（Ch 15–18） |

記一句話：**「認證證明你是你，授權決定你能幹嘛。」** Part 2 學前者，Part 4 學後者，中間靠 ID token 這張通行證串起來。

## 核心難題：前端直連，怎麼安全地登入？

回到 Ch 1 那個反直覺的架構——前端**直接**連資料庫，中間沒有你的伺服器。那問題來了：

> 沒有我自己的後端，是誰在驗證帳號密碼？密碼難道從瀏覽器直接比對？那不是很危險？

答案是：**有一個後端在做這件事，只是那個後端是 Google 的，不是你的。**

```
   你的瀏覽器                    Google 的 Identity 伺服器
 ┌──────────────┐             ┌─────────────────────────┐
 │              │  帳號+密碼    │                         │
 │  你的網頁     │ ──────────▶ │  1. 核對密碼             │
 │  (JS SDK)    │             │     (密碼雜湊存在 Google) │
 │              │  ◀────────── │  2. 正確 → 簽發 ID token │
 │              │  ID token   │     (用 Google 的私鑰簽名) │
 └──────────────┘             └─────────────────────────┘
        │
        │ 之後每次讀寫 Firestore，SDK 自動夾帶這張 token
        ▼
   Firestore（收到請求，驗證 token 上 Google 的簽名，
              確認沒被偽造、沒過期，取出 uid 交給安全規則）
```

所以「前端直連」不代表「沒有後端驗證」。**驗證帳密、簽發 token 這種敏感事，是 Google 的認證伺服器做的**，你的前端只是負責把帳密送過去、把 token 收回來。密碼**從不**存在你的資料庫、也**不會**由你的前端比對——這部分 Google 全包了，且做得比你自己刻安全得多（密碼雜湊、防撞庫、洩漏偵測…）。這正是 BaaS 幫你省掉的最有價值的苦工之一。

## ID token 到底是什麼：一張 JWT

保全發的「手環」，在技術上是一個 **ID token**，格式是 **JWT（JSON Web Token，發音 "jot"）**。它是現代網路認證的通用格式，不是 Firebase 專有的。

我們實際觀察一張 Firebase 簽發的 ID token（用 Auth 模擬器實跑，取 `auth.currentUser.getIdToken()`）：

```
實測結果：
  token 開頭三個字元： eyJ
  token 裡的「.」數量： 2   （代表分成 3 段）
  token 總長度： 493 字元（模擬器；正式環境的 token 更長，約 800–1000+ 字元）
```

一張 JWT 由 **三段**用 `.` 分隔的字串組成，`eyJ` 是 Base64 編碼 `{"` 的開頭（所以幾乎所有 JWT 都以 `eyJ` 開頭）：

```
  eyJhbGci...  .  eyJpc3Mi...  .  SflKxwRJ...
  └─ Header ─┘    └─ Payload ─┘   └─ Signature ─┘
   演算法資訊       實際內容         簽名（防偽）
```

| 段 | 內容 | 白話 |
|---|---|---|
| **Header** | 用什麼演算法簽名（如 RS256） | 「這張手環用哪種防偽技術」 |
| **Payload** | 你的 `uid`、email、簽發時間、**過期時間**等 | 「手環上寫的你的資訊」 |
| **Signature** | 用 Google **私鑰**對前兩段簽的名 | 「防偽雷射標籤，只有 Google 簽得出來」 |

三個關鍵性質：

1. **內容是公開可讀的**：前兩段只是 Base64 編碼（不是加密）。任何人拿到 token 都能解出你的 uid、email。所以 **token 裡不放祕密**。你可以把一張 token 貼到 [jwt.io](https://jwt.io/) 看它解出來的內容。
2. **不可偽造**：第三段簽名是用 Google 的**私鑰**簽的。別人可以讀內容，但改了內容就無法重簽出正確簽名（沒有私鑰）。Firestore 收到 token 時用 Google 的**公鑰**驗簽，任何竄改都會被抓到。
3. **有時效**：Payload 裡有過期時間，Firebase ID token **一小時**後過期。過期後 SDK 會用另一個長效的 refresh token 自動換一張新的——這你不用管，SDK 都處理好了。

> **為什麼 token 要有時效？** 萬一 token 被偷了，一小時後它自動失效，把損害控制在最小。這就是為什麼手環「今晚有效」而不是「永久有效」。

## 這一切在你的程式碼裡長什麼樣

原理講完，看它怎麼對應到你之後會寫的 API（Ch 5–7 會展開）：

```js
import { getAuth, signInWithEmailAndPassword, onAuthStateChanged } from "firebase/auth";

const auth = getAuth(app);

// 「保全查證件」——送帳密去 Google 認證伺服器
await signInWithEmailAndPassword(auth, "alice@example.com", "pw123456");

// 登入後，auth.currentUser 就是「已驗明正身的你」
console.log(auth.currentUser.uid);    // 你的唯一 ID（實測 28 字元）
console.log(auth.currentUser.email);  // alice@example.com

// SDK 已經幫你把「手環」（token）收好了，
// 之後你呼叫 Firestore，它會自動夾帶，你不用手動處理 token
```

你**幾乎不會直接碰 token**——SDK 把「登入拿 token、附在請求上、過期自動換發」全包了。你只需要：呼叫登入函式、之後看 `auth.currentUser` 知道現在是誰。token 是底層機制，理解它是為了讓你懂「為什麼登入後 Firestore 就知道你是誰」以及 Part 4 安全規則裡的 `request.auth` 是哪來的。

## uid：使用者的唯一身分證字號

登入成功後，每個使用者有一個 **`uid`（user ID）**——Firebase 配給他的**唯一、不變**的識別碼。實測是一串 28 字元的字串，像 `XMl5ymc6...`。

`uid` 是整個 Firebase 世界裡「這個人」的代表：

- 你會用它當 Firestore 文件的 ID（例如 `users/{uid}` 存這個人的個人資料）
- 安全規則靠 `request.auth.uid` 判斷「這筆資料是不是這個人的」
- 同一個人不管用 email 還是 Google 登入到同一帳號，`uid` 都一樣

記住：**email 會變、名字會變，但 `uid` 不變**。任何「這是誰的東西」的關聯，都用 `uid` 綁，不要用 email 綁。

## 踩雷集錦

1. **把認證當成授權**：「我登入了，為什麼還是 `permission-denied`？」——因為登入只證明你是誰（認證），能不能動這筆資料是安全規則（授權）決定的。這兩件事分開。登入成功 ≠ 有權限。
2. **以為 token 裡的東西是加密的**：JWT 的內容只是 **Base64 編碼，不是加密**，任何人都能讀。所以**絕不要**在自訂 token claim 裡放密碼、信用卡之類的祕密。token 的安全來自「不可偽造」和「有時效」，不是「內容看不到」。
3. **想手動管理 token**：新手常想「我要把 token 存起來、下次帶上」。**不用**。SDK 幫你管到好，包括過期自動換發。你手動存反而會弄出過期 token 的 bug。要用時才 `getIdToken()`（例如你有自己的後端要驗證使用者身分）。
4. **用 email 當使用者的主鍵**：email 可以改（甚至換帳號），拿它當「這是誰的資料」的關聯鍵，使用者改 email 後資料就對不上了。**永遠用 `uid`**。
5. **以為前端直連 = 密碼在前端比對**：密碼從不碰你的前端邏輯，是送到 Google 認證伺服器比對的。前端只負責傳帳密、收 token。

## 進階：再往深一層

- **Custom Claims（自訂宣告）**：你可以在 token 的 payload 裡加自訂資訊，最常見的是「角色」，例如 `admin: true`。這要用 **Admin SDK 在後端（如 Cloud Functions）** 設定，之後安全規則就能寫 `request.auth.token.admin == true` 來做「只有管理員能刪文」這種授權。這是把認證資訊餵給授權系統的橋樑，本課 Ch 17 會提到概念。
- **ID token vs refresh token**：ID token 短命（1 小時），用來證明身分；refresh token 長命，存在客戶端，專門用來「換發新的 ID token」。SDK 自動用後者續前者。理解這個雙 token 機制，你就懂為什麼登入狀態能「一直保持」但每張 token 又都很快過期——安全性和便利性兼顧。
- **驗證 token 的另一半：後端**：如果你有自己的後端 API（例如某些邏輯不適合放前端），前端可以 `getIdToken()` 拿到 token 傳給你的後端，後端用 **Firebase Admin SDK** 的 `verifyIdToken()` 驗證它、取出 uid，就能安全地知道「是哪個使用者在呼叫我的 API」。這是 Firebase Auth 和你自己後端整合的標準做法。
- **OpenID Connect / OAuth 2.0**：Firebase 的登入流程其實是這兩個業界標準的實作。JWT、ID token 這些概念不是 Firebase 發明的，是整個現代身分認證生態的通用語言。學會 Firebase 的認證，你也就摸到了 OAuth/OIDC 的門。

## 本章重點整理

- **認證**（你是誰）和**授權**（你能做什麼）是兩件事：前者是 Auth 服務、後者是安全規則，中間靠 ID token 串起來。
- 「前端直連」不代表沒有後端驗證——**驗帳密、簽 token 是 Google 的認證伺服器做的**，密碼從不碰你的前端或資料庫。
- **ID token 是一張 JWT**：三段（Header/Payload/Signature）、內容公開可讀、靠簽名防偽、一小時過期。
- **`uid`** 是使用者唯一不變的身分證字號；一切「誰的東西」都用 uid 綁，不要用 email。
- token 由 SDK 全自動管理，你幾乎不用手動碰。

## 自我檢核

- [ ] 不看筆記，我能解釋「認證」和「授權」的差別，並說出 Firebase 各由什麼機制負責
- [ ] 有人問「前端直連資料庫，密碼在哪裡比對」，我能正確回答
- [ ] 我能說出 JWT 的三段各是什麼，以及「內容可讀但不可偽造」是什麼意思
- [ ] 我知道為什麼要用 `uid` 而不是 email 當使用者的關聯鍵
- [ ] 我理解「登入成功但被 permission-denied」是可能且合理的，並知道原因

## 延伸閱讀

### 官方文件 / 標準

- **[Firebase Authentication 概念總覽](https://firebase.google.com/docs/auth)**
  - **讀哪裡**：首頁的「How it works」那段，對照本章的認證流程圖。
  - **能學到什麼**：官方對認證流程的描述，補上各種登入方式的全景。
  - **前提**：本章讀完即可。

- **[jwt.io — JWT 互動解碼器與介紹](https://jwt.io/)**
  - **讀哪裡**：把你自己的 Firebase ID token（`getIdToken()` 印出來）貼進去，親眼看它解出的 Header/Payload。下面的「JWT Debugger」和介紹文都值得看。
  - **能學到什麼**：把本章「三段結構」從抽象變成親眼所見。強烈建議動手貼一次。
  - **前提**：本章讀完即可；注意別貼別人的真 token（雖然內容公開，但那是別人的身分憑證）。

### 部落格 / 文章

- **[Auth0 — The Difference Between Authentication and Authorization](https://auth0.com/docs/get-started/identity-fundamentals/authentication-and-authorization)** — Auth0
  - **這篇說什麼**：認證 vs 授權的權威解釋，不限 Firebase，是整個身分領域的通識。
  - **為什麼值得讀**：Auth0 是身分認證領域的專業廠商，講這個概念比誰都清楚。讀完你對本章的核心分野會更牢。
  - **前提**：本章讀完即可。

- **[The Firebase Blog — 理解 Firebase Auth token 生命週期相關文章](https://firebase.blog/)** — Firebase 團隊
  - **這些說什麼**：ID token / refresh token 的續期機制、安全考量。在部落格搜 "auth token"。
  - **為什麼值得讀**：把本章「進階」提到的雙 token 機制講到實作層。

原理清楚了，該動手了。下一章我們用最基礎的 email/密碼登入，實際做出「註冊、登入、看到 `auth.currentUser`」——你會親眼在 Console 的使用者清單看到新帳號冒出來。

→ [Ch 5 — Email/密碼登入實作](./05-email-password-auth.md)
