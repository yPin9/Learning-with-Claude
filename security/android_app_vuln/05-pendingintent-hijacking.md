# Ch 5 — PendingIntent 劫持

> **目標**：把 PendingIntent 從「通知會用到的那個東西」升級成你能審、能打的攻擊面。你會理解 PendingIntent 本質是「一張借 victim 身份的憑證」，看懂「可變（mutable）的 PendingIntent + 空白 base Intent」為什麼是致命組合、攻擊者怎麼把它填充成任意內部呼叫，並掌握 `FLAG_MUTABLE`/`FLAG_IMMUTABLE`（API 31 起強制指定）這條防線。這章直接承接 Ch 4 的 confused deputy——PendingIntent 劫持就是把「借身份轉發」憑證化。

> **環境**：AVD（Android 13 / API 33），drozer、`adb`、`jadx`。案例以通知（Notification）與 AlarmManager 為主。PendingIntent 的觸發需要 victim App 跑起來，drozer/Frida/adb 執行輸出標「**未實測，理論預期行為**」+驗證步驟；「可變 base Intent 被填充」的合併邏輯用文字/程式碼推導說明。**特別注意 API 版本行為**：`FLAG_MUTABLE`/`FLAG_IMMUTABLE` 在 targetSdk≥31 起**強制二選一**，這條全章反覆標注。

## 為什麼需要這個？

Ch 4 的 Intent redirection 需要一個「當下會轉發 Intent 的 exported proxy」。PendingIntent 把這件事推到更遠、更陰險的地方：它是一張**可以交給別人、別人之後拿去觸發的憑證**，而觸發時用的是**發卡人（victim）的身份與權限**，不是持卡人（攻擊者）的。

想像 victim App 建了一個 PendingIntent 塞進通知，或交給系統的 AlarmManager，或透過某個 exported 元件遞出去。這張憑證一旦落到攻擊者手上，攻擊者若能**改寫它要送的 Intent 內容**，就能讓系統「用 victim 的身份」去做攻擊者指定的事——啟動 victim 的內部元件、以 victim 的權限發廣播、存取 victim 的私有資料。攻擊者自己完全沒有這些權限，全靠這張借來的憑證。

這是 Oversecured 等研究者挖出無數高賞金洞的一類，Google 認真到直接在 Android 12（API 31）改了平台規則：**你建 PendingIntent 時，必須明確表態它可不可變**——因為太多開發者建了「可變的空白憑證」，等著被填充劫持。這章教你認出這張憑證、看懂它為什麼危險、以及那條平台防線到底防住了什麼。

## 先建立直覺：PendingIntent 是一張「代領支票」

把 PendingIntent 想成一張**代領支票**：

```
   victim App（發卡人，有身份/權限）
        │  建立 PendingIntent（開一張支票）：
        │    「持這張的人，可以用【我的名義】去做【這個 Intent】的事」
        ▼
   ┌─────────────────────────────────────────┐
   │  PendingIntent = 一個 token（憑證）      │
   │   內含：要送的 base Intent + 觸發方式     │
   │   關鍵：觸發時用【victim 的 UID/權限】    │
   └─────────────────────────────────────────┘
        │  交出去（塞進通知 / 給 AlarmManager / 透過元件遞給別的 App）
        ▼
   持卡人（可能是系統、可能是攻擊者）
        │  在某個時機觸發（send / 系統代觸發）
        ▼
   系統用【victim 的身份】執行 base Intent 描述的動作
```

兩個關鍵性質，理解了整章就通了：

1. **觸發時用的是發卡人的身份**。這是設計本意——通知被點擊時，要能以 App 自己的身份啟動 App 的 Activity，即使點擊的是系統 UI（別的進程）。這個「借身份」是 feature，也是攻擊面。
2. **PendingIntent 是「支票」而非「當下的動作」**。它可以被儲存、傳遞、延後觸發。攻擊者不需要在場，只要拿到憑證、且能影響它最終送出的 Intent。

漏洞的核心問句：**這張支票的「收款人/金額」欄位（base Intent 的內容），持卡人能不能事後填？** 如果能，攻擊者就把一張「送給空白目標」的支票，填成「送給我想打的內部元件」。

## 底層機制：mutable + 空白 base Intent = 可被填充

PendingIntent 建立時給一個 **base Intent** 和一組 **flags**。決定它可不可被劫持的，是兩件事：**base Intent 留了多少空白**，以及**它可不可變（mutable）**。

先看「填充」怎麼發生。當持卡人觸發 PendingIntent 時，可以提供一個 **fillInIntent**，系統會把 fillInIntent 的欄位**填進 base Intent 裡「當初沒指定」的空白欄位**。規則大致是：

```
最終送出的 Intent = base Intent（已指定的欄位鎖死）
                  + fillInIntent 填進 base 沒指定的空白欄位

  base 指定了 component → fillInIntent 改不動 component（安全）
  base 沒指定 component → fillInIntent 可以填 component！（危險）
  extras / data / action 同理：base 留白的，持卡人能填
```

所以**致命組合是：base Intent 幾乎空白（尤其沒設 component/action）+ PendingIntent 可變**。攻擊者拿到這種 PendingIntent，提供一個 fillInIntent 把 component 填成 victim 的內部元件，觸發時系統就用 victim 身份啟動那個內部元件。

我用 Python 模擬這個「base + fillIn 合併」的欄位邏輯，把「base 留白 → 被填充」看清楚（**實際輸出**，這是欄位合併規則的邏輯示範，非 Android 執行）：

```python
def resolve_intent(base, fill_in, mutable):
    """模擬 PendingIntent 觸發時 base 與 fillInIntent 的合併。
    規則：base 已指定的欄位鎖死；base 為 None 的欄位由 fill_in 填。
    immutable（mutable=False）：忽略 fill_in（API31+ 的 IMMUTABLE 語意）。"""
    final = {}
    for field in ("component", "action", "data", "extras"):
        b = base.get(field)
        if b is not None:
            final[field] = b + "  (來自 base，鎖死)"
        elif mutable and fill_in.get(field) is not None:
            final[field] = fill_in[field] + "  (← 攻擊者填充！)"
        else:
            final[field] = None
    return final

# 危險：base 幾乎空白 + mutable
base_blank = {"component": None, "action": None, "data": None, "extras": None}
attacker_fill = {"component": "com.victim/.SecretAdminActivity",
                 "action": "android.intent.action.VIEW", "data": None, "extras": None}
print("== mutable + 空白 base ==")
for k, v in resolve_intent(base_blank, attacker_fill, mutable=True).items():
    print(f"  {k:10s}: {v}")

# 安全：base 鎖死 component
base_locked = {"component": "com.victim/.SafeActivity", "action": None, "data": None, "extras": None}
print("== base 已鎖 component（即使 mutable，component 也改不動）==")
for k, v in resolve_intent(base_locked, attacker_fill, mutable=True).items():
    print(f"  {k:10s}: {v}")

# 最安全：immutable
print("== immutable（fillInIntent 全被忽略）==")
for k, v in resolve_intent(base_blank, attacker_fill, mutable=False).items():
    print(f"  {k:10s}: {v}")
```

```
== mutable + 空白 base ==
  component : com.victim/.SecretAdminActivity  (← 攻擊者填充！)
  action    : android.intent.action.VIEW  (← 攻擊者填充！)
  data      : None
  extras    : None
== base 已鎖 component（即使 mutable，component 也改不動）==
  component : com.victim/.SafeActivity  (來自 base，鎖死)
  action    : None
  data      : None
  extras    : None
== immutable（fillInIntent 全被忽略）==
  component : None
  action    : None
  data      : None
  extras    : None
```

三種情況一目了然：**只有「mutable + 空白 base」讓攻擊者填進了 component**，指向內部元件。base 鎖死 component 的，component 改不動（但 extras 若留白仍可能被填，見踩雷 3）。immutable 的，fillInIntent 整個被忽略。

## API 31 的強制：FLAG_MUTABLE vs FLAG_IMMUTABLE

在 Android 12（API 31）之前，PendingIntent **預設是可變的**——開發者不寫 flag，就得到一張可被填充的支票，多數人根本沒意識到。API 31 改了規則：

- **targetSdk ≥ 31**：建立 PendingIntent 時**必須**在 flags 裡明確給 `FLAG_MUTABLE` 或 `FLAG_IMMUTABLE`，二選一。漏了會直接 `IllegalArgumentException` crash。
- Google 的意圖：逼開發者面對「這張支票該不該可變」這個決定，把「預設可變」這個坑從默默存在變成必須表態。

| flag | 語意 | 什麼時候該用 |
|---|---|---|
| **`FLAG_IMMUTABLE`** | 支票內容鎖死，fillInIntent 全被忽略 | **絕大多數情況的正解**——通知點擊、AlarmManager 觸發自己的元件，都不需要別人填 |
| **`FLAG_MUTABLE`** | 允許 fillInIntent 填充空白欄位 | 少數真的需要（如 `RemoteInput` 直接回覆、Bubble、某些系統整合）；用時 base **必須**鎖死 component/action |

**判定紅旗**（審 App 時）：
- targetSdk < 31 且建 PendingIntent 沒給 IMMUTABLE → 預設可變，查它 base 留白沒。
- 用了 `FLAG_MUTABLE` → 立刻查 base Intent 有沒有鎖死 component/action；沒鎖 = 可劫持。
- 用了 `FLAG_MUTABLE` 又把 PendingIntent 遞給不可信的對象（別的 App、透過 exported 元件遞出）→ 高危。

> **一個關鍵細節**：`FLAG_IMMUTABLE` 鎖的是「內容不可被 fillInIntent 改」，不是「這張支票不可被別人觸發」。immutable 的 PendingIntent 別人拿到照樣能觸發它、以 victim 身份執行它**原本就要做的事**。所以 immutable 解決的是「填充劫持」，不解決「憑證外洩本身」——base Intent 該做的事若本身敏感，外洩仍有害。

## 範例一：可變 PendingIntent 被填充（通知場景）

漏洞版：victim 建了一個空白 base、mutable 的 PendingIntent，塞進通知（Java，**漏洞點已標**）：

```java
// 漏洞版（targetSdk 30，或 31+ 顯式寫了 FLAG_MUTABLE）
Intent base = new Intent();   // ← 漏洞點 1：base 幾乎空白，沒設 component/action
PendingIntent pi = PendingIntent.getActivity(
        this, 0, base,
        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);  // ← 漏洞點 2：mutable

// 若這個 pi 又被透過某個 exported 元件、或 RemoteViews 遞給不可信方 → 可被填充
```

攻擊者拿到這個 PendingIntent 後，用 fillInIntent 填充並觸發（概念，攻擊者這端）：

```java
// 假設攻擊者透過某管道取得 victim 的 PendingIntent pi
Intent fillIn = new Intent();
fillIn.setComponent(new ComponentName("com.victim", "com.victim.SecretAdminActivity"));
// base 沒設 component，這裡填進去 → 觸發時以 victim 身份啟動 SecretAdminActivity
pi.send(context, 0, fillIn);   // 用 victim 的身份執行填充後的 Intent
```

**為什麼成功**：`pi.send(..., fillIn)` 觸發時，系統以 victim 的 UID 執行合併後的 Intent。`SecretAdminActivity` 就算不 exported 也會被啟動——因為發起者是 victim 自己（透過它開的支票）。這是 Ch 4 confused deputy 的憑證化版本：支票就是 confused deputy，攻擊者填充收款欄。

**怎麼拿到 pi？** 常見管道：victim 透過一個 exported 元件把 PendingIntent 放進回傳的 Intent extra 遞出來（`getParcelableExtra` 取 PendingIntent）；或放進一個攻擊者能讀到的 RemoteViews / widget；或某些 IPC 介面直接回傳。**枚舉這些遞出點**是實戰的第一步。

**驗證步驟**（**未實測，理論預期行為**）：在 AVD 寫一個自製靶，exported 元件回傳一個 mutable+空白 base 的 PendingIntent；寫攻擊 App 取得它、`send` 帶 fillIn 指向內部 Activity，觀察 logcat 該 Activity 的 `onCreate` 是否被觸發、以及發起 UID 是否為 victim。對照組：把 flag 改成 `FLAG_IMMUTABLE` 重試，fillIn 應被忽略，內部 Activity 不被啟動。

## 範例二：AlarmManager 場景與 base 鎖死的邊界

AlarmManager 是另一個常見遞出點——你把 PendingIntent 交給系統，讓它到時間替你觸發：

```java
// 相對安全版：base 鎖死 component + IMMUTABLE
Intent base = new Intent(this, MyAlarmReceiver.class);   // ← base 設了 component
PendingIntent pi = PendingIntent.getBroadcast(
        this, 0, base, PendingIntent.FLAG_IMMUTABLE);      // ← immutable
alarmManager.setExact(AlarmManager.RTC_WAKEUP, triggerAt, pi);
```

這版**擋得住填充**：component 鎖死 + immutable。即使攻擊者能拿到 pi，也改不了它要送去的 `MyAlarmReceiver`，且 fillIn 被忽略。

**邊界 / 失敗案例**：如果只做了「base 鎖死 component」但**忘了 IMMUTABLE 且 base 的 extras 留白**——攻擊者雖然改不了 component，卻可能用 fillIn 填 extras，若 `MyAlarmReceiver` 盲信 extras 裡的資料（例如金額、userId），仍能被操縱。**鎖 component 不等於安全，extras 也要管**。最穩的組合是 `FLAG_IMMUTABLE`（一次擋掉所有填充），只有真的需要 mutable 時才退而求其次去鎖每個欄位。

## 範例三：隱式 base Intent 的外洩放大

一個更陰險的變種：base Intent 是**隱式的**（只有 action，沒設 package/component）。這種 PendingIntent 觸發時，系統按 action 去 resolve 目標——如果攻擊者能註冊一個匹配該 action 的元件，就可能**攔截**這個以 victim 身份發出的 Intent，連同 extras（可能含敏感資料）一起收走。

```java
// 危險：base 是隱式 Intent（無 package/component）
Intent base = new Intent("com.victim.SOME_ACTION");   // 沒 setPackage！
base.putExtra("session_token", token);                // extras 帶敏感資料
PendingIntent pi = PendingIntent.getBroadcast(this, 0, base,
        PendingIntent.FLAG_IMMUTABLE);   // 即使 immutable 也救不了：問題在 base 本身是隱式
```

即使 `FLAG_IMMUTABLE`，這裡的問題不是「被填充」而是「隱式 base 被別人接走」——攻擊者註冊 `com.victim.SOME_ACTION` 的 Receiver，觸發時就收到 victim 發的廣播和裡面的 token。**修法**：base Intent 一律**設 explicit target**（`setPackage` / `setComponent`），別讓 PendingIntent 承載隱式 Intent。這也呼應 Ch 4 的「隱式 Intent resolve 歧異」。

## 對比與取捨

| PendingIntent 配置 | 可被填充劫持？ | 可被隱式攔截？ | 建議 |
|---|---|---|---|
| mutable + 空白 base | ✅ 高危 | 視情況 | ❌ 幾乎沒有正當理由 |
| mutable + base 鎖 component（extras 留白） | 部分（extras） | 否 | ⚠️ extras 仍要管 |
| **immutable + explicit base** | 否 | 否 | ✅ 絕大多數情況的正解 |
| immutable + 隱式 base（無 package） | 否 | ✅ 可攔截 | ❌ base 一定要 explicit |

| | Intent redirection（Ch 4） | PendingIntent 劫持（Ch 5） |
|---|---|---|
| 攻擊者控制的東西 | 被轉發的 Intent | 支票的空白欄位（fillInIntent） |
| 觸發時機 | 當下（proxy 立即 startActivity） | 延後（憑證可儲存、系統代觸發） |
| 借誰的身份 | proxy 元件所在 App | 建 PendingIntent 的 App |
| 平台防線 | 開發者自己白名單/剝 flag | **API 31 強制 MUTABLE/IMMUTABLE 表態** |

## 踩雷集錦

1. **以為 API 31+ 就自動安全了**：強制的是「表態」，不是「一定 immutable」。開發者照樣能寫 `FLAG_MUTABLE`。看到 mutable 一定要往下查 base 鎖死沒。強制宣告堵的是「忘了想」，不是「想錯了」。
2. **把 IMMUTABLE 當「別人不能觸發」**：immutable 只鎖「內容不被 fillIn 改」，別人拿到照樣能觸發它、以 victim 身份做它原本要做的事。憑證外洩本身（尤其 base 帶敏感 extras 或是隱式）仍有害。
3. **鎖了 component 就以為完事**：mutable 且 base 只鎖 component、extras/data 留白，攻擊者仍能填 extras/data。要嘛全鎖，要嘛直接 IMMUTABLE 一次解決。
4. **忽略隱式 base 的攔截風險**：base 沒 `setPackage`/`setComponent` 的 PendingIntent，觸發的 Intent 可能被別的 App 接走，連 extras 一起洩。base 一律 explicit。
5. **只看建立點不看遞出點**：一個 mutable PendingIntent 若只在 App 內自用、從不遞給外部，風險有限。真正危險的是它**經由 exported 元件/回傳 Intent/RemoteViews 遞給了不可信方**。審計要同時追「怎麼建的」和「遞給了誰」。

## 進階：再往深一層

- **`FLAG_UPDATE_CURRENT` 與 requestCode 撞用**：兩個 PendingIntent 若 requestCode、Intent 的「filterEquals 欄位」都相同，系統視為同一個，`FLAG_UPDATE_CURRENT` 會更新既有那個的 extras。這曾被用來跨 App 干擾/覆寫彼此的 PendingIntent，是個微妙的攻擊面。
- **PendingIntent 的 `getIntentSender()` 與 IntentSender 傳遞**：PendingIntent 底層是 `IIntentSender` Binder。它作為 IPC token 在進程間傳遞時，接收方能查發起 App（`getCreatorPackage()`），但這只用於防禦判斷，攻擊者拿到 token 本身才是問題。
- **RemoteViews 與 widget 的填充面**：桌面 widget、通知的 `RemoteViews` 常用 mutable PendingIntent 搭配 `setOnClickFillInIntent`，這是 mutable 的正當用途之一——但也正因如此成為填充劫持的溫床。審 widget/通知的點擊處理要特別看 fillInIntent 的來源可信度。
- **與 Ch 4 的合流**：實戰中 PendingIntent 劫持常和 Intent redirection 串成鏈——用 redirection 取得 victim 遞出的 mutable PendingIntent，再填充觸發。兩章的模型是同一個 confused deputy，換了外衣。

## 動手練習

1. 自寫一個靶：exported 元件回傳一個 mutable + 空白 base 的 PendingIntent。寫攻擊 App 取得它、用 fillInIntent 填 component 指向一個不 exported 的 Activity，`send` 觸發，用 logcat 確認 (a) 該 Activity 被啟動、(b) 發起 UID 是 victim。這是本章的核心 PoC。
2. 把靶的 flag 從 `FLAG_MUTABLE` 改成 `FLAG_IMMUTABLE`，其餘不動，重跑步驟 1 的攻擊。觀察填充失效、內部 Activity 不再被啟動——親手驗證那條平台防線擋住了什麼。
3. 把 base 改成隱式（只給 action、不 setPackage）並帶一個假 token 在 extras，在攻擊 App 註冊匹配該 action 的 Receiver，觸發後看你的 Receiver 有沒有收到 victim 發的 token。體會「隱式 base 即使 immutable 也會外洩」。

## 本章重點整理

- **PendingIntent = 一張借 victim 身份的支票**：觸發時用建立者的 UID/權限，可儲存、可延後、可傳遞。
- 致命組合 = **mutable + 空白 base（無 component/action）**：攻擊者用 fillInIntent 填 component 指向內部元件，以 victim 身份啟動。
- **API 31 起 targetSdk≥31 必須明確給 `FLAG_MUTABLE`/`FLAG_IMMUTABLE`**（漏了直接 crash）；正解幾乎總是 **IMMUTABLE + explicit base**。
- IMMUTABLE 只擋「填充」，不擋「憑證被觸發/外洩」；隱式 base 即使 immutable 也可能被別的 App 攔截，連 extras 一起洩。
- 審計要同時追**建立點（flag/base 留白）**與**遞出點（遞給了誰）**；這是 Ch 4 confused deputy 的憑證化變種。

## 自我檢核

- [ ] 不看筆記，能用「代領支票」解釋 PendingIntent 為什麼觸發時用的是 victim 的身份
- [ ] 能說出「可被填充劫持」的兩個必要條件，以及各自對應的修法
- [ ] 能講清楚 API 31 對 `FLAG_MUTABLE`/`FLAG_IMMUTABLE` 的強制規則，以及它「只堵忘了想、不堵想錯了」
- [ ] 能解釋為什麼 IMMUTABLE 不等於「別人不能觸發」，以及隱式 base 為什麼即使 immutable 也危險
- [ ] 給你一段建 PendingIntent 的程式碼，能判斷它可不可被劫持、缺哪條防線

## 延伸閱讀

- **[Oversecured — Android: PendingIntent hijacking / arbitrary intents](https://blog.oversecured.com/)**
  - **讀哪裡**：搜他們關於 PendingIntent 與 mutable Intent 的深度文章（含大廠 App 真實案例與利用鏈）
  - **為什麼是一手參考**：這一類洞最系統、最深入的研究來源；本章的填充模型與遞出點分析直接對應他們的案例
- **[Android 官方 — PendingIntent 與 mutability（API 31 行為變更）](https://developer.android.com/reference/android/app/PendingIntent)**
  - **讀哪裡**：`FLAG_MUTABLE`/`FLAG_IMMUTABLE` 常數說明，以及 Android 12 行為變更頁對「必須指定 mutability」的規定
  - **為什麼權威**：API 版本行為（強制表態、預設值變更）以此為準，別靠記憶
- **[Android Developers Blog — Making PendingIntents safe(r)](https://android-developers.googleblog.com/)**
  - **讀哪裡**：官方解釋為什麼引入強制 mutability、開發者該怎麼選 IMMUTABLE 的那篇
  - **和本章的關聯**：從防禦者/平台角度看這條防線的設計動機，補齊攻擊視角
- **[OWASP MASTG — Testing PendingIntent 使用](https://mas.owasp.org/MASTG/tests/android/MASVS-PLATFORM/)**
  - **讀哪裡**：PendingIntent 與 IPC 相關 test case
  - **前提知識**：讀過本章，這裡給你把 PendingIntent 劫持寫進評估報告的標準流程與嚴重度依據

到這裡，Ch 3–5 走完了「元件與 Intent」這條線：直接打 exported、借道打內部、劫持憑證。下一章換一種元件——**ContentProvider**，它是四大元件裡唯一天生跟「資料」綁死的，於是它的洞也最像傳統 Web：SQL injection、path traversal、任意檔案讀取。我們從 query 未過濾一路打到 `openFile` 的 `../`。

→ [Ch 6 ContentProvider 漏洞：SQLi、path traversal、openFile](./06-contentprovider-vulns.md)
