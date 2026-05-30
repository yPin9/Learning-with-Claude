# Ch 5 — dpkg 的 maintainer scripts

> **目標**：理解四個 maintainer scripts（preinst/postinst/prerm/postrm）的執行時機與傳入參數、安裝/升級/移除的完整呼叫序列、為什麼 scripts 必須可重入（idempotent），以及如何安全地寫它們。

> **環境**：dpkg 1.21.x。maintainer scripts 的呼叫約定由 Debian Policy §6 定義，跨版本穩定。

## 為什麼需要 maintainer scripts？

大部分套件只是把檔案灑進系統，dpkg 解開 data.tar 就完事了。但有些套件需要在安裝/移除時做額外動作：

- 建立系統使用者（如 `postgres` 套件要建 `postgres` user）
- 啟動/重啟 daemon（裝了 nginx 要啟動服務）
- 編譯設定（如更新 font cache、ldconfig）
- 遷移舊版本的設定檔
- 移除時清理產生的資料

這些動作寫在四個 shell script 裡，dpkg 在安裝/移除的特定時機呼叫它們。寫錯了——尤其是時機和可重入性——會造成升級失敗、套件卡在 half-configured，甚至系統服務起不來。

## 先建立直覺：四個 script 的時機

```
            檔案解開前        檔案解開後
            ─────────        ─────────
  安裝/升級   preinst    →    [data.tar]    →    postinst
            (檔案還沒到)      (檔案到位了)      (設定/啟動)

            移除前            檔案刪除後
            ─────            ─────────
  移除       prerm      →    [刪檔案]      →    postrm
            (停服務)         (清理)
```

四個 script 的記憶法：
- **pre**inst / **pre**rm：在主要動作（解開檔案 / 刪檔案）**之前**
- **post**inst / **post**rm：在主要動作**之後**

最常用的是 **postinst**（檔案都到位了，可以設定/啟動服務）和 **prerm**（移除前要先停服務，否則檔案被刪了服務還在跑）。

## 每個 script 收到的參數

dpkg 呼叫每個 script 時傳入第一個參數說明「現在是什麼情況」。這是寫對 script 的關鍵——同一個 script 在安裝、升級、移除時被呼叫，要靠參數判斷。

```bash
#!/bin/sh
set -e   # 任何指令失敗就中止（後面詳談為什麼必須）

case "$1" in
    configure)
        # postinst 在安裝/升級時收到 "configure"
        # $2 = 舊版本號（升級時）或空（首次安裝）
        ;;
    abort-upgrade|abort-remove|abort-deconfigure)
        # 出錯回滾時收到這些
        ;;
esac
```

**postinst** 的第一參數：

| `$1` | 何時 | `$2` |
|---|---|---|
| `configure` | 安裝或升級完成、設定階段 | 舊版本號（升級）或空（首裝）|
| `abort-upgrade` | 升級失敗回滾 | 失敗的版本 |

**prerm** 的第一參數：

| `$1` | 何時 |
|---|---|
| `remove` | 即將移除這個套件 |
| `upgrade` | 即將被新版本取代（升級）|
| `deconfigure` | 因依賴衝突被暫時取消設定 |

**postrm** 的第一參數：

| `$1` | 何時 |
|---|---|
| `remove` | 套件已移除（檔案已刪）|
| `purge` | 連設定檔一起清除（`apt purge`）|
| `upgrade` | 升級中，舊版的 postrm |

## 安裝的完整呼叫序列

首次安裝 `foo`：

```
dpkg -i foo.deb
    │
    ▼
  foo.preinst install          ← $1=install, 檔案還沒解開
    │
    ▼
  [解開 data.tar，檔案到位]
    │
    ▼
  foo.postinst configure       ← $1=configure, $2=空（首裝）
    │
    ▼
  installed ✓
```

升級 `foo`（1.0 → 2.0）更複雜，涉及新舊兩個套件的 scripts：

```
dpkg -i foo_2.0.deb （已裝 foo_1.0）
    │
    ▼
  foo_1.0.prerm upgrade 2.0      ← 舊版 prerm，$1=upgrade $2=新版本
    │
    ▼
  foo_2.0.preinst upgrade 1.0    ← 新版 preinst，$1=upgrade $2=舊版本
    │
    ▼
  [解開新版 data.tar，覆蓋舊檔案]
    │
    ▼
  foo_1.0.postrm upgrade 2.0     ← 舊版 postrm 清理
    │
    ▼
  foo_2.0.postinst configure 1.0 ← 新版 postinst，$2=舊版本（可據此遷移）
    │
    ▼
  installed ✓
```

> 注意升級時 `postinst configure` 的 `$2` 是**舊版本號**。這讓你能寫「如果從 1.x 升上來，遷移舊設定格式」這種版本相關的邏輯。

## 為什麼必須可重入（idempotent）

maintainer script 可能被跑不只一次：

- postinst 跑到一半失敗（套件卡在 half-configured），下次 `dpkg --configure -a` 會**重跑** postinst configure
- 同一個 script 在安裝和升級時都會跑

所以 script 不能假設「我只會被跑一次」。範例：建立使用者要先檢查存不存在。

```bash
# 錯誤：重跑會失敗
adduser --system foo    # 第二次跑：useradd: user 'foo' already exists → set -e 中止！

# 正確：先檢查
if ! getent passwd foo >/dev/null; then
    adduser --system --group foo
fi
```

```bash
# 錯誤：建目錄重跑會失敗（如果沒有 -p）
mkdir /var/lib/foo

# 正確
mkdir -p /var/lib/foo    # -p 讓已存在不報錯
```

## set -e 與錯誤處理

Policy 要求 maintainer scripts 開頭 `set -e`——任何指令失敗（非零退出）就立刻中止 script。為什麼？

如果 postinst 中間某步失敗了卻繼續跑，套件會被標記成「設定成功」，但實際上系統處於半完成狀態，比直接失敗更難 debug。`set -e` 確保失敗立刻可見，套件卡在 half-configured，逼你修復根因。

```bash
#!/bin/sh
set -e   # 必須

# 危險：如果這個指令「正常」會回傳非零（如 grep 沒找到），set -e 會誤殺
if grep foo /etc/something; then ... ; fi   # OK，在 if 裡的非零不觸發 set -e

grep foo /etc/something || true   # 如果你要忽略失敗，明確 || true
```

> `set -e` 的陷阱：在 `if`、`while`、`||`、`&&` 裡的指令失敗**不會**觸發 set -e。但獨立一行的指令失敗會。這個 shell 行為要熟悉，否則會寫出在某些情況下莫名中止的 script。

## debhelper 自動生成大部分 scripts

好消息：你很少需要手寫完整的 maintainer script。debhelper 的 `dh_*` 工具會自動生成常見邏輯，透過 `#DEBHELPER#` 佔位符注入：

```bash
# debian/postinst（你寫的部分）
#!/bin/sh
set -e

case "$1" in
    configure)
        # 你的自訂邏輯（如建立 config）
        ;;
esac

#DEBHELPER#   ← debhelper 在這裡注入它生成的程式碼
                # 例如 dh_installsystemd 注入的服務啟動邏輯

exit 0
```

常見的自動化：
- `dh_installsystemd`：注入 systemd unit 的 enable/start（Ch 29）
- `dh_installinit`：注入 SysV init script 處理
- `dh_makeshlibs` → ldconfig trigger（Ch 26）
- `dh_installdebconf`：debconf 設定整合

所以實務上你的 postinst 常常只有「自訂部分 + `#DEBHELPER#`」，大部分樣板交給 debhelper。

## 完整範例：建立服務使用者的 postinst

```bash
#!/bin/sh
# debian/postinst — 為 myservice 套件建立系統使用者並設定資料目錄
set -e

case "$1" in
    configure)
        # 1. 建立系統使用者（可重入：先檢查）
        if ! getent group myservice >/dev/null; then
            addgroup --system myservice
        fi
        if ! getent passwd myservice >/dev/null; then
            adduser --system --ingroup myservice \
                    --home /var/lib/myservice \
                    --no-create-home \
                    --shell /usr/sbin/nologin \
                    myservice
        fi

        # 2. 建立資料目錄並設權限（可重入：-p + 無條件 chown）
        mkdir -p /var/lib/myservice
        chown myservice:myservice /var/lib/myservice
        chmod 750 /var/lib/myservice

        # 3. 版本相關遷移（$2 是舊版本）
        if [ -n "$2" ] && dpkg --compare-versions "$2" lt "2.0"; then
            # 從 1.x 升上來，遷移舊設定格式
            echo "Migrating config from pre-2.0..."
            # ... 遷移邏輯 ...
        fi
        ;;

    abort-upgrade|abort-remove|abort-deconfigure)
        ;;

    *)
        echo "postinst called with unknown argument \`$1'" >&2
        exit 1
        ;;
esac

#DEBHELPER#

exit 0
```

對應的 postrm（purge 時清理資料）：

```bash
#!/bin/sh
# debian/postrm
set -e

case "$1" in
    purge)
        # 只在 purge（完全清除）時刪資料和使用者
        rm -rf /var/lib/myservice
        if getent passwd myservice >/dev/null; then
            deluser --system myservice || true
        fi
        ;;
    remove|upgrade|failed-upgrade|abort-install|abort-upgrade|disappear)
        ;;
esac

#DEBHELPER#

exit 0
```

> 注意 postrm 的 `purge` 才刪資料。`remove` 不刪——使用者可能只是暫時移除套件，之後重裝想保留資料。這個區分尊重使用者意圖，是 Policy 的要求。

## 踩雷集錦

1. **忘記 `set -e`**：script 中間失敗卻繼續，套件被誤標成設定成功，留下半完成狀態。Policy 要求 set -e

2. **不可重入**：`adduser foo` 沒先檢查存在，重跑時 set -e 中止，套件卡 half-configured。所有「建立」操作都要先檢查或用冪等選項（`mkdir -p`、`getent` 檢查）

3. **在 remove 就刪資料**：使用者 `apt remove`（非 purge）只想暫時移除，你卻在 postrm remove 刪了他的資料庫。資料清理只能在 `purge`

4. **沒處理 `$1` 的所有情況**：script 被傳入沒料到的參數（如 `abort-upgrade`）卻沒對應 case，行為未定義。至少要有 `case` 涵蓋已知情況，未知的 `*)` 處理

5. **prerm 沒停服務就讓檔案被刪**：升級時舊檔案被新版覆蓋，但服務還用著舊的 `.so`，可能崩潰。服務的停止/重啟交給 `dh_installsystemd` 自動處理通常比手寫安全

6. **script 裡有互動式提示**：maintainer script 在非互動環境（自動更新、CI）跑時不能卡在 `read`。要使用者輸入用 debconf，不要直接 prompt

## 進階：debconf 與非互動設定

當套件需要使用者輸入（如設定資料庫密碼），不能在 maintainer script 裡直接 `read`——那會在自動化環境卡死。Debian 的解法是 **debconf**：一個預先收集設定的框架。

```bash
# postinst 用 debconf 取得設定（已在更早的 config script 收集）
. /usr/share/debconf/confmodule
db_get myservice/admin_password
ADMIN_PW="$RET"
```

debconf 的好處：設定在安裝**前**統一收集（`debconf-set-selections` 可預先餵入），安裝過程完全非互動。這讓無人值守部署成為可能。這是個大主題，這裡先讓你知道「需要使用者輸入時用 debconf，不要在 script 裡 read」。

## 動手練習

1. 抓一個有 maintainer scripts 的套件，看它們：`dpkg-deb --raw-extract <(apt-get download --print-uris ...) ...`，或更簡單——找已裝套件的 scripts：`ls /var/lib/dpkg/info/openssh-server.*` 看 `.postinst`、`.prerm` 內容

2. 讀 `cat /var/lib/dpkg/info/dpkg.postinst`（或任何裝了的套件），找出它的 `case "$1"` 結構和 `#DEBHELPER#`（如果有）

3. 寫一個故意不可重入的 postinst（`adduser foo` 不檢查），在 VM 裡裝兩次（或 reconfigure），觀察第二次失敗

4. 用 `dpkg-reconfigure <package>`（對有 debconf 的套件如 `tzdata`）觀察 debconf 互動，理解設定和安裝分離

## 本章重點整理

- 四個 script：preinst/postinst（安裝前後）、prerm/postrm（移除前後）；postinst 和 prerm 最常用
- 每個 script 靠 `$1` 參數判斷情境（configure/remove/upgrade/purge...），`$2` 常是舊版本號
- script 必須可重入（idempotent）——可能被重跑，所有操作要先檢查或用冪等選項
- `set -e` 是 Policy 要求；資料清理只能在 `purge`，不能在 `remove`
- debhelper 透過 `#DEBHELPER#` 注入大部分樣板；需要使用者輸入用 debconf 不要 read

## 自我檢核

- [ ] 不看筆記，能畫出升級套件時新舊版 scripts 的呼叫順序
- [ ] 知道 postinst 的 `$2` 在升級時是什麼（舊版本號），能用它做什麼
- [ ] 能解釋為什麼 script 必須可重入，舉一個不可重入會出事的例子
- [ ] 知道為什麼資料清理要在 `purge` 而非 `remove`
- [ ] 知道需要使用者輸入時為什麼不能在 script 裡 `read`（用 debconf）

## 延伸閱讀

### 官方文件

- **[Debian Policy §6 (Maintainer Scripts)](https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html)**
  - **讀哪裡**：§6.4（summary of ways maintainer scripts are called）那張完整的呼叫序列表
  - **學什麼**：所有情境的精確參數；本章是它的教學版，這是權威版
  - **前提**：讀完本章

- **[debconf-devel(7) man page](https://manpages.debian.org/bookworm/debconf-doc/debconf-devel.7.html)**
  - **讀哪裡**：開頭的 overview 和 confmodule 用法
  - **學什麼**：debconf 的完整開發者介面
  - **前提**：本章的 debconf 進階段落

### 部落格 / 文章

- **[Maintainer scripts flowcharts](https://people.debian.org/~srivasta/MaintainerScripts.html)** — Manoj Srivastava
  - **這篇說什麼**：用流程圖完整畫出所有安裝/移除/升級/失敗情境的 script 呼叫順序
  - **讀哪裡**：所有流程圖，尤其 upgrade 和 failed-upgrade 的
  - **為什麼值得讀**：Policy §6.4 的文字很難記，這些圖是社群公認最清楚的視覺化參考

→ [練習 A：手工組裝一個 .deb](./practice-a-handcraft-deb.md)
