# Ch 5 — Email/密碼登入實作

> **目標**：用 Firebase Authentication 做出最基礎的 email/密碼註冊與登入，親眼在 Console 的使用者清單看到帳號冒出來，並學會正確處理登入失敗（錯密碼、重複註冊、弱密碼）的錯誤。

> **環境**：Firebase JS SDK v12.18.0。本章所有登入程式碼與錯誤碼都用 Auth 模擬器實跑驗證；「Console 看到使用者」由你在真實專案操作。錯誤碼有 emulator 與正式環境的差異，文中會標注。

## 為什麼需要這個？

Ch 4 講了認證的原理，現在把它變成能跑的程式碼。email/密碼是最基礎、最好理解的登入方式——沒有 OAuth 彈窗、沒有第三方，就是「輸入 email 和密碼」。先把這個做扎實，Ch 6 的 Google 登入只是換一個函式。

## Step 0：先在 Console 啟用 Email 登入

> 這步在 Console 操作。**不做這步，程式碼一定報錯。**

回想 Ch 2 的踩雷：服務要先在 Console 啟用。到 **Build → Authentication → 開始使用（Get started）**，在 **Sign-in method** 分頁找到 **「電子郵件/密碼 / Email/Password」**，點開、**啟用（Enable）**、儲存。

沒開這個開關，`createUserWithEmailAndPassword` 會回 `auth/operation-not-allowed`。

## 先建立直覺：註冊與登入是兩個不同動作

新手常把「註冊」和「登入」混在一起，但它們是兩個 API：

```
   註冊 (Sign Up)                     登入 (Sign In)
   createUserWith...                  signInWith...
   「建立一個新帳號」                  「用已存在的帳號進來」
        │                                  │
        ▼                                  ▼
   在 Google 建一個新使用者            核對已存在使用者的密碼
   記錄 email + 密碼雜湊               正確 → 發 token
   順便直接登入                        錯誤 → 報錯
```

兩者成功後都會讓你「登入狀態」，差別是：註冊**建立**新帳號，登入**進入**既有帳號。用錯 API 的典型症狀：拿 `createUser...` 給老用戶用，會報「email 已存在」。

## 註冊：createUserWithEmailAndPassword

```js
import { getAuth, createUserWithEmailAndPassword } from "firebase/auth";

const auth = getAuth(app);

async function signUp(email, password) {
  const cred = await createUserWithEmailAndPassword(auth, email, password);
  // cred.user 就是剛建立、且已自動登入的使用者
  console.log("註冊成功 uid:", cred.user.uid);
  console.log("email:", cred.user.email);
  console.log("isAnonymous:", cred.user.isAnonymous);
  return cred.user;
}
```

實測 `signUp("alice@example.com", "pw123456")` 的輸出：

```
[signup] uid: 28 chars | email: alice@example.com | isAnonymous: false
```

幾個重點：

- 回傳的是一個 **`UserCredential`** 物件，真正的使用者在 `cred.user`。
- `uid` 是那串 28 字元的唯一 ID（Ch 4 講過）。
- **註冊即登入**：`createUser...` 成功後，這個使用者就是當前登入者（`auth.currentUser` 立刻有值），不用再呼叫一次登入。
- 密碼**至少 6 個字元**，這是 Firebase 的硬性最低要求。

跑完回 Console 的 **Authentication → Users** 分頁，你會看到 `alice@example.com` 出現在使用者清單裡，帶著它的 uid 和建立時間。**這就是「帳號真的建到雲端了」的實感。**

> **`isAnonymous` 是什麼？** Firebase 也支援「匿名登入」（Ch 6），那種使用者 `isAnonymous` 為 `true`。email 註冊的是正式帳號，所以 `false`。先知道這欄存在。

## 登入：signInWithEmailAndPassword

```js
import { signInWithEmailAndPassword } from "firebase/auth";

async function signIn(email, password) {
  const cred = await signInWithEmailAndPassword(auth, email, password);
  console.log("登入成功，uid:", cred.user.uid);
  return cred.user;
}
```

實測「登出後再用同帳密登入」，uid 和註冊時**完全一樣**：

```
[signin] ok, uid matches: true
```

這印證 Ch 4 說的：`uid` 對同一個帳號是**穩定不變**的。今天註冊、明天登入，同一個 uid，所以你可以放心用它當「這是誰的資料」的關聯鍵。

## 關鍵：處理登入失敗（別讓 App 直接崩）

真實世界使用者會打錯密碼、會重複註冊。這些情況 Firebase 用**丟出例外（throw）**表示，你必須用 `try/catch` 接住，並根據 `err.code` 給出對的提示。

```js
async function signInSafe(email, password) {
  try {
    const cred = await signInWithEmailAndPassword(auth, email, password);
    return { ok: true, user: cred.user };
  } catch (err) {
    // err.code 是機器可判斷的錯誤碼，err.message 是給開發者看的長訊息
    return { ok: false, code: err.code, message: humanize(err.code) };
  }
}

function humanize(code) {
  switch (code) {
    case "auth/invalid-credential":  // 正式環境：帳號或密碼錯（不告訴你是哪個，防列舉）
    case "auth/wrong-password":      // 舊版/模擬器：密碼錯
    case "auth/user-not-found":      // 舊版：查無此帳號
      return "帳號或密碼錯誤";
    case "auth/invalid-email":
      return "email 格式不正確";
    case "auth/too-many-requests":
      return "嘗試次數過多，請稍後再試";
    default:
      return "登入失敗：" + code;
  }
}
```

實測各種失敗情況的 `err.code`（Auth 模擬器）：

```
[wrong-pw]   code: auth/wrong-password
[dup-email]  code: auth/email-already-in-use
[weak-pw]    code: auth/weak-password
```

> ⚠️ **重要的版本差異（認識論誠實）**：上面 `auth/wrong-password` 是**模擬器**的回傳。但在**正式環境**，Google 從 2023 年起預設開啟「email 列舉保護（email enumeration protection）」，密碼錯或帳號不存在都會統一回傳 **`auth/invalid-credential`**——**故意不告訴你是「帳號不存在」還是「密碼錯」**，避免壞人用錯誤訊息的差異去試探「哪些 email 有註冊」。所以你的 `humanize` 一定要同時處理 `auth/invalid-credential`（正式）和 `auth/wrong-password`（舊/模擬器），而且 UI 提示統一寫「帳號或密碼錯誤」——**不要**寫「密碼錯誤」，那反而洩漏了「帳號存在」。這是安全性與使用者體驗的真實取捨。

常見錯誤碼一覽：

| 錯誤碼 | 意思 | 常見場景 |
|---|---|---|
| `auth/invalid-credential` | 帳密不符（正式環境統一碼） | 登入打錯 |
| `auth/wrong-password` | 密碼錯（舊版/模擬器） | 登入打錯 |
| `auth/email-already-in-use` | email 已被註冊 | 註冊時 email 重複 |
| `auth/weak-password` | 密碼太弱（少於 6 字元） | 註冊時密碼太短 |
| `auth/invalid-email` | email 格式錯 | 沒有 `@` 之類 |
| `auth/operation-not-allowed` | 這種登入方式沒啟用 | 忘了 Step 0 |
| `auth/too-many-requests` | 太多次失敗，暫時鎖 | 被當成攻擊 |

## 完整可跑範例：一個最小登入頁

把上面拼成一個能在瀏覽器跑的頁面（延續 Ch 3 的 CDN 寫法）：

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head><meta charset="UTF-8"><title>Email 登入</title></head>
<body>
  <input id="email" placeholder="email" value="alice@example.com">
  <input id="password" type="password" placeholder="密碼（至少6字）">
  <button id="signup">註冊</button>
  <button id="signin">登入</button>
  <p id="status">未登入</p>

  <script type="module">
    import { initializeApp } from "https://www.gstatic.com/firebasejs/12.18.0/firebase-app.js";
    import { getAuth, createUserWithEmailAndPassword, signInWithEmailAndPassword }
      from "https://www.gstatic.com/firebasejs/12.18.0/firebase-auth.js";

    const firebaseConfig = { /* 換成你的 config */ };
    const auth = getAuth(initializeApp(firebaseConfig));

    const $ = id => document.getElementById(id);
    const status = $("status");

    async function run(fn, label) {
      try {
        const cred = await fn(auth, $("email").value, $("password").value);
        status.textContent = `${label}成功！uid: ${cred.user.uid}`;
      } catch (err) {
        status.textContent = `${label}失敗：${err.code}`;
      }
    }
    $("signup").onclick = () => run(createUserWithEmailAndPassword, "註冊");
    $("signin").onclick = () => run(signInWithEmailAndPassword, "登入");
  </script>
</body>
</html>
```

換上你的 config，打開，按「註冊」→ 看 status 顯示 uid、看 Console 使用者清單多一個人。故意輸入短密碼按註冊，看它顯示 `auth/weak-password`。**故意弄錯，看它怎麼罵你**，這比只跑成功路徑學到更多。

## 踩雷集錦

1. **忘記 Console 啟用 Email 登入**：報 `auth/operation-not-allowed`。回 Step 0。這是最常見的第一個卡點。
2. **UI 提示寫太細洩漏帳號存在**：把錯誤訊息寫成「查無此帳號」或「密碼錯誤」，等於告訴攻擊者「這個 email 有沒有註冊」。正式環境本來就用 `auth/invalid-credential` 幫你統一了，你的 UI 也要配合寫「帳號或密碼錯誤」，別自作聰明分開。
3. **不接 `try/catch`**：登入失敗是**丟例外**，不是回傳 `{ok: false}`。不接就會變成未處理的 Promise rejection，畫面沒反應、使用者一頭霧水。所有 auth 呼叫都要包 try/catch。
4. **以為註冊要自己存密碼**：`createUserWithEmailAndPassword` 之後，密碼由 Google 雜湊儲存，你的資料庫**不該、也不會**有密碼。想著「我要把密碼存進 Firestore」是嚴重的安全錯誤——不要。
5. **密碼驗證只做前端**：前端可以檢查「至少 6 字」給即時提示，但真正的規則是 Firebase 在後端強制的。別以為前端擋掉就安全——Firebase 那關才算數。
6. **拿 email 當文件 ID 或關聯鍵**：又是這個。email 可變，用 `uid`。（Ch 4 踩雷第 4 條，重要到再講一次。）

## 進階：再往深一層

- **Email 驗證（verify email）**：`createUser` 後使用者的 `emailVerified` 預設是 `false`（實測確認）。你可以呼叫 `sendEmailVerification(auth.currentUser)` 寄一封驗證信，使用者點連結後 `emailVerified` 變 `true`。之後安全規則可以要求「email 驗證過才能發文」（`request.auth.token.email_verified == true`）。這是擋機器人註冊的常見手段。
- **忘記密碼**：`sendPasswordResetEmail(auth, email)` 一行就寄出重設密碼信，整個「忘記密碼」流程 Firebase 包好了——這又是 BaaS 省掉的一大塊苦工，自己刻這個安全又麻煩。
- **更新個人資料**：`updateProfile(auth.currentUser, { displayName: "Alice", photoURL: "..." })` 可以設定顯示名稱和頭像網址。實測 email 註冊的使用者 `displayName` 預設是 `null`，要自己設。
- **密碼政策**：正式環境可以在 Console 設定更嚴的密碼要求（要有大小寫、數字、長度下限）。預設只要 6 字元，正式產品通常會加嚴。

## 本章重點整理

- **註冊**（`createUserWithEmailAndPassword`）建新帳號並自動登入；**登入**（`signInWithEmailAndPassword`）進既有帳號。是兩個 API。
- 使用前**先在 Console 啟用 Email 登入**，否則 `auth/operation-not-allowed`。
- 失敗用**丟例外**表示，一律 `try/catch` 接 `err.code`。
- 正式環境密碼錯/帳號不存在**統一回 `auth/invalid-credential`**（防列舉），UI 要寫「帳號或密碼錯誤」。
- 密碼由 Google 雜湊保管，**你的資料庫永遠不該有密碼**。

## 自我檢核

- [ ] 我親手註冊過一個帳號，並在 Console 的 Users 清單看到它
- [ ] 我能說出「註冊」和「登入」兩個 API 的差別，以及各自成功後的狀態
- [ ] 我知道為什麼正式環境要用 `auth/invalid-credential` 統一錯誤，以及 UI 該怎麼寫
- [ ] 我的每個 auth 呼叫都有 try/catch，並根據 err.code 給對的提示
- [ ] 我確定我的程式碼裡沒有任何地方儲存使用者密碼

## 延伸閱讀

### 官方文件

- **[Authenticate with Firebase using Password-Based Accounts (Web)](https://firebase.google.com/docs/auth/web/password-auth)**
  - **讀哪裡**：「Create a password-based account」和「Sign in a user」兩節，就是本章的官方對應。
  - **能學到什麼**：官方版的註冊/登入寫法，以及 email 驗證、重設密碼的完整 API。
  - **前提**：本章讀完即可。

- **[Email Enumeration Protection](https://cloud.google.com/identity-platform/docs/admin/email-enumeration-protection)**
  - **讀哪裡**：開頭解釋「為什麼要保護」那段。
  - **能學到什麼**：本章那個 `auth/invalid-credential` 統一碼背後的安全原理，講到底。理解這個，你對「錯誤訊息也可能是資安漏洞」會有感。

- **[Firebase Auth 錯誤碼清單](https://firebase.google.com/docs/reference/js/auth#autherrorcodes)**
  - **讀哪裡**：當你遇到本章表格沒列的錯誤碼，來這查。
  - **能學到什麼**：所有 auth 錯誤碼的權威清單。

### 影片

- **[Fireship — Firebase Authentication 教學](https://www.youtube.com/c/Fireship/search?query=authentication)** — Jeff Delaney
  - **這支說什麼**：從啟用登入方式到寫登入邏輯的實作版，和本章同路徑。
  - **為什麼值得看**：畫面對照，卡關時很有用。

email/密碼會了。但現代 App 很少只有這個——使用者更想「用 Google 一鍵登入」，或先「匿名試用」不註冊。下一章我們加上 Google 登入和匿名登入，你會發現換一種登入方式其實只是換一個函式。

→ [Ch 6 — 第三方登入（Google）與匿名登入](./06-oauth-anonymous.md)
