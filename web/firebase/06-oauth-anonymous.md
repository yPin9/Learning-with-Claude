# Ch 6 — 第三方登入（Google）與匿名登入

> **目標**：加上「用 Google 一鍵登入」和「匿名登入（先試用不註冊）」兩種方式，理解 OAuth 彈窗登入背後的流程，並學會把匿名帳號「升級」成正式帳號而不丟失資料。

> **環境**：Firebase JS SDK v12.18.0。匿名登入以 Auth 模擬器實跑驗證；Google OAuth 需真實彈窗與 Google 帳號互動，**無法在無頭環境模擬**，本章的 Google 登入程式碼標為「理論預期行為 + 官方標準寫法」，由你在真實專案與瀏覽器操作。

## 為什麼需要這個？

現實中，要使用者「想一組新密碼、記住它」是很高的門檻——很多人看到註冊表單就關掉了。兩個降低門檻的做法：

1. **社群登入（Google/Facebook…）**：使用者按一下、選個已登入的 Google 帳號就進來了，不用想新密碼。
2. **匿名登入**：連帳號都不用，先讓使用者「以訪客身分」用起來，等他真的想保存資料時再引導註冊。

這章教這兩種，並解決一個實務關鍵問題：**訪客用一用想註冊了，他匿名時產生的資料怎麼保留？**

## 先建立直覺：所有登入方式，回來的都是同一種 user

這是 Firebase Auth 最舒服的設計：**不管用哪種方式登入，你最後拿到的都是同一種 `user` 物件、同一套 `auth.currentUser`。** 差別只在「怎麼登入」，登入後的世界一模一樣。

```
   email/密碼 ──┐
   Google     ──┤
   Facebook   ──┼──▶  同一個 auth.currentUser（有 uid、可讀寫資料）
   GitHub     ──┤
   匿名        ──┘

   換登入方式 = 換一個 signInWith... 函式，後面全部一樣
```

所以你 Ch 5 學的「登入後看 currentUser、用 uid 綁資料」的一切，換成 Google 登入後**完全不用改**。這章只是多教幾個「入口函式」。

## Google 登入：signInWithPopup

### Step 0：Console 啟用 Google 登入

> Console 操作。到 **Authentication → Sign-in method**，啟用 **Google**，會要你設定「專案對外顯示名稱」和「支援 email」，填一下儲存。

### 程式碼

Google 登入用一個 **provider（提供者）** 物件搭配 `signInWithPopup`：

```js
import { getAuth, GoogleAuthProvider, signInWithPopup } from "firebase/auth";

const auth = getAuth(app);
const provider = new GoogleAuthProvider();

async function signInWithGoogle() {
  try {
    const result = await signInWithPopup(auth, provider);
    // 和 email 登入一樣，使用者在 result.user
    console.log("Google 登入成功:", result.user.displayName, result.user.email);
    console.log("uid:", result.user.uid);
    return result.user;
  } catch (err) {
    console.log("登入失敗:", err.code);
  }
}
```

> **以下為理論預期行為（需真實 Google 帳號 + 瀏覽器彈窗，無法在無頭環境驗證）**：按下觸發 `signInWithGoogle()` 的按鈕後，瀏覽器會**彈出一個 Google 帳號選擇視窗**，使用者選帳號、（第一次）授權你的 App 讀取基本資料，彈窗關閉，`result.user` 就填好了——而且和 email 登入不同，Google 登入的 `displayName` 和 `photoURL` **會自動帶入**（來自 Google 帳號），不用自己設。

### 彈窗背後：OAuth 流程

`signInWithPopup` 那一下，背後跑的是 **OAuth 2.0** 授權流程：

```
1. 你的 App 開一個彈窗，導向 Google 的登入頁
2. 使用者在 Google（不是在你的網站）輸入/確認身分
   ── 關鍵：使用者的 Google 密碼從不經過你的網站 ──
3. 使用者授權「允許這個 App 讀取我的 email、名字、頭像」
4. Google 把一組憑證送回你的 App
5. Firebase 用它換成你熟悉的 ID token → auth.currentUser 就緒
```

**為什麼這樣設計很安全**：使用者是在 **Google 自己的頁面**輸入密碼，你的網站從頭到尾碰不到他的 Google 密碼——你只拿到「Google 幫你證明的身分 + 使用者授權你看的那些欄位」。這就是「用 Google 登入」能被信任的原因。

> **`signInWithPopup` vs `signInWithRedirect`**：popup 開彈窗（體驗好，但可能被瀏覽器擋彈窗）；redirect 是整頁跳去 Google 再跳回來（相容性好，尤其行動裝置和嚴格的瀏覽器隱私設定下）。近年因瀏覽器封鎖第三方 cookie，redirect 在某些情境更可靠。先用 popup，遇到彈窗被擋或行動端問題時知道有 redirect 這個備案。

### 本地測試 Google 登入要注意

Google 登入要求網頁跑在**授權過的網域**上。`localhost` 預設就在白名單，所以你要用**本地伺服器**開頁面（例如 `npx serve` 或 VS Code 的 Live Server），**不能**像 Ch 3 那樣直接雙擊 `file://` 打開——`file://` 不被 OAuth 接受。部署到 Firebase Hosting 後（Ch 20），你的正式網域也會自動在白名單。

## 匿名登入：signInAnonymously

匿名登入讓使用者「不註冊就有一個真的 uid」，可以讀寫資料，只是這個帳號沒有 email、關掉瀏覽器清掉資料後就找不回來。

### Step 0：Console 啟用匿名登入

> **Authentication → Sign-in method → 匿名（Anonymous）→ 啟用**。

### 程式碼

```js
import { getAuth, signInAnonymously } from "firebase/auth";

const auth = getAuth(app);

async function signInAsGuest() {
  const cred = await signInAnonymously(auth);
  console.log("匿名登入，isAnonymous:", cred.user.isAnonymous);
  console.log("有真的 uid:", cred.user.uid.length > 0);
  return cred.user;
}
```

實測輸出（Auth 模擬器）：

```
[anon] isAnonymous: true | has uid: true
```

重點：匿名使用者**有一個真正的 uid**，`isAnonymous` 為 `true`。這代表你的安全規則、資料關聯全都能照常運作——訪客也是「有身分的」，只是那個身分是臨時的。

適合匿名登入的場景：購物車（還沒註冊就能加東西）、試玩、問卷、「先讓你用，喜歡再註冊」的產品。

## 關鍵實務：把匿名帳號「升級」成正式帳號

最重要的一招。訪客匿名用了一陣子、產生了資料（購物車、草稿），現在他想正式註冊——**你希望他匿名時的資料原封不動地變成他新帳號的資料，而不是重開一個空帳號。**

秘訣：用 **`linkWithCredential`（或 `linkWithPopup`）** 把新的登入方式**綁到現有的匿名帳號上**，這樣 **uid 不變**，資料自然全保留。

```js
import { getAuth, EmailAuthProvider, linkWithCredential,
         GoogleAuthProvider, linkWithPopup } from "firebase/auth";

const auth = getAuth(app);

// 把 email/密碼 綁到目前的匿名帳號（uid 不變）
async function upgradeAnonToEmail(email, password) {
  const credential = EmailAuthProvider.credential(email, password);
  const result = await linkWithCredential(auth.currentUser, credential);
  console.log("升級後 isAnonymous:", result.user.isAnonymous); // 變成 false
  console.log("uid 有沒有變:", result.user.uid); // 和升級前一樣！
  return result.user;
}

// 或綁 Google（會彈窗）
async function upgradeAnonToGoogle() {
  return await linkWithPopup(auth.currentUser, new GoogleAuthProvider());
}
```

> **為什麼「uid 不變」是整個技巧的靈魂**：因為你所有資料都是用 uid 綁的（`users/{uid}`、文件的 `owner: uid`），只要 uid 不變，那些資料自動就是新帳號的了——**你一行資料搬移都不用寫**。如果你用「新註冊一個帳號再手動搬資料」的笨辦法，不但麻煩還容易出錯。這再次證明 Ch 4「一切用 uid 綁」的價值。

> **升級可能失敗的情況**：如果要綁的 email 已經是**另一個既有帳號**，`linkWithCredential` 會報 `auth/email-already-in-use` 或 `auth/credential-already-in-use`。這時的正解通常是引導使用者「登入那個既有帳號」，並決定匿名資料要不要合併過去（合併邏輯要你自己寫）。這是升級流程要處理的邊界。

## 對比與取捨：三種登入方式

| 方式 | 使用者門檻 | 帳號能否找回 | 適合 | API |
|---|---|---|---|---|
| **匿名** | 最低（零操作） | 不能（清資料就沒了） | 試用、購物車、先玩再說 | `signInAnonymously` |
| **Email/密碼** | 中（要想密碼） | 能（用 email） | 需要長期帳號、無社群依賴 | `signInWithEmailAndPassword` |
| **Google** | 低（按一下選帳號） | 能（綁 Google） | 大多數消費級 App | `signInWithPopup` |

好的產品常「三個都給」：進來先匿名 → 想保存時給 Google 一鍵註冊（升級）→ 也留 email 選項給沒有/不想用 Google 的人。

## 踩雷集錦

1. **用 `file://` 直接開頁面測 Google 登入**：OAuth 不接受 `file://`，會失敗。Google 登入一定要跑在 `localhost`（本地伺服器）或授權網域上。匿名和 email 登入則沒這限制。
2. **忘記在 Console 啟用對應登入方式**：每種方式都要各自啟用。啟用了 Email 不代表 Google 也開了。報 `auth/operation-not-allowed` 就是這個。
3. **匿名升級時用「新建帳號」而非「link」**：`createUserWithEmailAndPassword` 會產生**新 uid**，匿名時的資料就跟丟了。要保留資料必須用 `linkWithCredential`/`linkWithPopup` 綁到現有 uid。
4. **以為匿名使用者沒有 uid、不能存資料**：錯，匿名使用者有真 uid、能讀寫、能被安全規則管。它只是「找不回」，不是「沒身分」。
5. **彈窗被瀏覽器擋掉當成程式壞了**：`signInWithPopup` 若不是由使用者點擊直接觸發（例如放在 `setTimeout` 或頁面載入時自動呼叫），會被瀏覽器當彈窗廣告擋掉。一定要綁在按鈕的 click 事件上。擋掉時考慮改用 `signInWithRedirect`。
6. **忘記處理「同一人用不同方式登入」**：預設 Firebase 會把「相同 email 的不同登入方式」視情況處理。若你在 Console 開了「一個 email 只能一個帳號」，使用者先用 Google（alice@gmail.com）、後又想用 email 密碼註冊同個 email，會衝突。這類帳號連結策略在 Console 的 Authentication 設定裡。

## 進階：再往深一層

- **其他 provider 幾乎一樣**：`FacebookAuthProvider`、`GithubAuthProvider`、`OAuthProvider`（Apple、Microsoft…）用法和 Google 同一個模子——`new XxxProvider()` + `signInWithPopup`。學會 Google 就等於學會全部社群登入，差別只在各家 Console 設定要填的東西（App ID/Secret）。
- **索取額外權限（scopes）**：`provider.addScope('https://www.googleapis.com/auth/contacts.readonly')` 可以要求存取使用者的 Google 通訊錄之類。拿到的 OAuth access token（`GoogleAuthProvider.credentialFromResult(result)`）能去打 Google 的其他 API。這超出「登入」進入「整合 Google 服務」，但知道這條路存在。
- **匿名帳號會累積**：每次 `signInAnonymously` 都可能產生一個新匿名帳號。使用者一直沒升級的匿名帳號會在 Console 的 Users 裡累積（可設定自動清理）。設計時注意別無限制地製造孤兒匿名帳號。
- **`onAuthStateChanged` 對所有方式一致**：不管哪種登入，狀態變化都透過同一個監聽器通知（下一章 Ch 7 的主題）。這就是「回來的都是同一種 user」在事件層面的體現。

## 本章重點整理

- 不論哪種登入方式，登入後拿到的都是**同一種 `user` / `auth.currentUser`**，換方式只是換入口函式。
- **Google 登入**用 `new GoogleAuthProvider()` + `signInWithPopup`；背後是 OAuth，使用者密碼在 Google 頁面輸入，你的網站碰不到。
- Google 登入要跑在 `localhost` 或授權網域，**不能用 `file://`**。
- **匿名登入**（`signInAnonymously`）給訪客一個真 uid，能讀寫、受規則管，但找不回。
- 匿名升級用 **`linkWithCredential`/`linkWithPopup`** 綁新方式到現有 uid，**uid 不變 = 資料全保留**。

## 自我檢核

- [ ] 我能解釋「不管哪種登入，登入後的程式碼都一樣」這句話的意思
- [ ] 我能描述 Google 彈窗登入背後的 OAuth 流程，以及為什麼我的網站碰不到使用者的 Google 密碼
- [ ] 我知道測 Google 登入為什麼不能用 `file://`
- [ ] 有人問我「訪客註冊後資料怎麼保留」，我能說出用 `link...` 保持 uid 不變的做法
- [ ] 我知道匿名使用者也有 uid、也受安全規則管

## 延伸閱讀

### 官方文件

- **[Authenticate Using Google with JavaScript](https://firebase.google.com/docs/auth/web/google-signin)**
  - **讀哪裡**：「Handle the sign-in flow with the Firebase SDK」的 popup 與 redirect 兩種寫法。
  - **能學到什麼**：本章 Google 登入的官方完整版，含 redirect 的處理與取 access token。

- **[Authenticate with Firebase Anonymously](https://firebase.google.com/docs/auth/web/anonymous-auth)**
  - **讀哪裡**：整篇不長，特別看「Convert an anonymous account to a permanent account」——就是本章的升級技巧。
  - **能學到什麼**：官方版的匿名登入與帳號升級，含各種 provider 的 link 寫法。

- **[Account Linking](https://firebase.google.com/docs/auth/web/account-linking)**
  - **讀哪裡**：處理 `credential-already-in-use` 那段。
  - **能學到什麼**：本章踩雷第 6 條「同一人多登入方式衝突」的完整處理方案。

### 概念

- **[OAuth 2.0 Simplified](https://aaronparecki.com/oauth-2-simplified/)** — Aaron Parecki
  - **這篇說什麼**：OAuth 2.0 流程的白話解釋，作者是 OAuth 規格的參與者之一。
  - **為什麼值得讀**：把本章「彈窗背後的 OAuth 流程」講到通透。理解 OAuth 是超越 Firebase 的通用技能。
  - **前提**：本章讀完即可，這篇補足協議層細節。

登入方式齊了，但還缺一塊：使用者登入或登出後，**你的畫面怎麼知道、怎麼跟著變**？（例如登入後把「登入按鈕」換成「登出按鈕」和使用者名字。）下一章講登入狀態的管理——這是把認證接進真實 UI 的最後一哩。

→ [Ch 7 — 登入狀態管理](./07-auth-state.md)
