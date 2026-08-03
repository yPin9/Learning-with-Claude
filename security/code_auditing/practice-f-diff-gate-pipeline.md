# 練習 F — diff-gate pipeline

> **目標**：把 Ch 36-40 拼成一條能跑的小 pipeline——「只審改動 + 靜態掃 + SARIF 匯總 + 動態驗證 + gate 判定」，模擬真實 CI 安全閘。你要在一個 git repo 裡：PR diff → Semgrep 只掃新增 → 輸出 SARIF → 對高信命中生成 harness 跑 ASan 動態驗證 → 依結果 gate（pass/fail）。
> **環境**：WSL、git、semgrep 1.172.0、gcc（含 AddressSanitizer）、python3。靶在 `~/audit-lab/pf/`（本練習現建）。

前五章你各自學了零件：誤報治理（Ch 36 的排序/去重/baseline）、靜態+動態驗證（Ch 37 的 harness + ASan 閉環）、diff 審計（Ch 38 的 `--baseline-commit`）、SARIF 匯流（Ch 39 的結構解析）、AI 輔助的驗證紀律（Ch 40）。這個練習把它們串成一條線——這正是真實團隊的 CI 安全閘長的樣子：**PR 進來，只為它的改動負責，靜態撒網、動態收口，最後給一個 pass/fail 的判決**。

---

## 任務規格

在一個 git repo 裡實作一支 `gate.sh <baseline-commit>`，對「當前 HEAD 相對 baseline 的改動」做完整安全閘，精確定義如下：

**輸入**：
- 一個 git repo，兩個 commit：baseline（乾淨）+ PR（引入一個真實可觸發的漏洞）。
- 一組 Semgrep 規則（至少一條能抓那個漏洞）。
- baseline commit hash 當參數。

**輸出（五步，各要有可見產物）**：

1. **diff**：列出這次改動的 `.c` 檔。無 C 改動 → 直接 PASS（exit 0）。
2. **靜態掃（只報新增）**：用 `semgrep --baseline-commit` 掃，輸出 SARIF，印出**新增命中數**。新增為 0 → PASS（exit 0）。
3. **SARIF 匯總**：解析 SARIF，印出每個高信命中的 `ruleId file:line`。
4. **動態驗證**：對高信命中所在檔，**生成 harness**（因為原檔的 `main` 通常不呼叫可疑函式，sink 不在執行路徑上——Ch 37 的核心），`gcc -fsanitize=address` build，餵觸發輸入，看 ASan 有沒有真的 crash。
5. **gate 判定**：
   - 動態確認（ASan crash）→ **FAIL，擋 PR，exit 1**。
   - 有新增靜態命中但動態未確認 → **WARN，需人工複查，exit 2**（不自動擋，因為「沒觸發 ≠ 誤報」）。

**驗收標準**（缺一不可）：

- 跑 `gate.sh <baseline>`（baseline = PR 的前一個 commit），五步全部有輸出，最後 **GATE: FAIL、exit 1**，且步驟 4 貼得出**真實的 ASan `stack-buffer-overflow`** crash（含 `in handle` 與 crash 行號，且行號與步驟 3 SARIF 報的位置對得上）。
- 跑 `gate.sh <HEAD>`（baseline = HEAD，等於無 diff）→ 步驟 1 就 **PASS、exit 0**。
- 兩條路徑（FAIL、PASS）都要能重現。

---

## 分五步

1. **建靶 repo**：`~/audit-lab/pf/` git init。baseline commit：一個安全檔（`src/proto.c` 只有安全的 `strncpy` + `main`）。PR commit：往同檔加一個有 OOB 的 `handle(int fd)`（`memcpy(buf, data, len)`，`len` 外部可控、`buf` 64 bytes、無 bound check）。加一條 Semgrep 規則 `unbounded-memcpy`（`memcpy($D,$S,$N)` 且非 `sizeof`）。

2. **步驟 1+2（diff + baseline 掃）**：`git diff --name-only $BASE HEAD | grep '\.c$'` 取改動檔；`semgrep --config rules.yaml --baseline-commit $BASE . --sarif -o new.sarif` 只掃新增；用 python 讀 `new.sarif` 的 `results` 長度印新增數。0 就 PASS 早退。

3. **步驟 3（SARIF 匯總）**：python 解析 `new.sarif`，對每個 result 印 `ruleId + uri:startLine`。

4. **步驟 4（動態驗證，最關鍵）**：從 SARIF 取命中所在檔。**寫一個 harness** `main(){ handle(0); }`（直接驅動可疑函式），把原檔的 `main` 改名避衝突，和 harness 一起 `gcc -g -fsanitize=address` 編。造觸發輸入（`len=200` > 64 + payload）餵 stdin。抓 ASan 輸出。

5. **步驟 5（gate）**：ASan 輸出含 `AddressSanitizer` → FAIL/exit 1；否則 WARN/exit 2。無新增/無 diff 的早退路徑回 exit 0。

---

## 如果你卡住了

- **步驟 2 baseline 掃報 0 個，但你明明加了漏洞**：baseline commit 設錯了。要設成 **PR 的前一個 commit**（`git rev-parse HEAD~1`，或更嚴謹的 `git merge-base`），不是 HEAD 本身（設 HEAD → diff 為空 → 報 0）。這正是 Ch 38 踩雷「baseline 設錯」的實況。
- **步驟 4 ASan「未觸發」但命中是真的**：最常見的坑，也是這練習的教學重點——**原檔的 `main` 沒呼叫 `handle`**，所以 sink 根本不在 `main` 的執行路徑上，餵再多輸入也到不了（Ch 37：沒覆蓋到 ≠ 誤報）。解法是**自己寫 harness 直接呼叫 `handle`**，把 sink 暴露出來。沒有 harness 的動態驗證對「不被 main 觸及的函式」永遠是假陰性。
- **步驟 4 編譯報 `multiple definition of main`**：你的 harness 有 `main`，原檔也有 `main`，衝突。把原檔的 `main` 用 `sed` 改名（如 `__orig_main`）再一起編。
- **步驟 4 不開 ASan 就沒 crash**：忘了 `-fsanitize=address`。stack OOB 不開 ASan 多半默默踩壞相鄰記憶體不 crash，你會誤判沒 bug（Ch 37 邊界失敗一）。**必須**加 `-fsanitize=address -g`。
- **觸發輸入沒讓它 crash**：`len` 要 > `buf` 大小（64），且輸入格式對——前 4 byte 是 `len`（little-endian int），後面接至少 `len` bytes payload。餵太短會卡在 `read(...) != len` 的檢查（Ch 37 邊界失敗二：前置條件沒滿足）。
- **gate 該 FAIL 卻 exit 0**：檢查你的 exit code 邏輯——確認的 crash 要 `exit 1`，別讓 `set -e` 或某個 `|| true` 把它吞掉。

---

## 測試用例表

| 用例 | baseline 設定 | 預期步驟走向 | 預期 exit |
|------|--------------|-------------|-----------|
| PR 引入 OOB | `HEAD~1` | 1→2(1 hit)→3→4(ASan crash)→FAIL | 1 |
| 無改動 | `HEAD` | 步驟 1 無 C 改動，早退 PASS | 0 |
| 只改註解/非 C | `HEAD~1`（改 README） | 步驟 1 無 `.c` 改動，PASS | 0 |
| 改動但無新命中 | baseline 有同樣舊債 | 步驟 2 新增數 0，PASS | 0 |
| 命中但動態未觸發 | 命中在死碼/main 不呼叫且**不寫 harness** | 4 未確認 → WARN | 2 |

---

## 參考解答

真跑過（WSL Ubuntu、semgrep 1.172.0、gcc + ASan、python3）。

<details>
<summary>點開看靶 repo + gate.sh + 真實輸出</summary>

**建靶 repo**

```bash
rm -rf ~/audit-lab/pf && mkdir -p ~/audit-lab/pf && cd ~/audit-lab/pf
git init -q && git config user.email a@b.c && git config user.name t

# baseline commit：安全碼
mkdir -p src
cat > src/proto.c <<'EOF'
#include <string.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdio.h>
void log_msg(char *s){ char b[128]; strncpy(b, s, sizeof(b)-1); b[127]=0; }
int main(){ return 0; }
EOF
cat > rules.yaml <<'EOF'
rules:
  - id: unbounded-memcpy
    languages: [c]
    severity: ERROR
    message: memcpy with non-constant length into fixed buffer.
    patterns:
      - pattern: memcpy($D, $S, $N);
      - pattern-not: memcpy($D, $S, sizeof($X));
EOF
git add -A && git commit -qm "baseline: safe proto"

# PR commit：引入 OOB handler
cat >> src/proto.c <<'EOF'
void handle(int fd) {
    char buf[64];
    int len;
    if (read(fd, &len, sizeof(len)) != sizeof(len)) return;
    char *data = malloc(len);
    if (!data) return;
    if (read(fd, data, len) != len) { free(data); return; }
    memcpy(buf, data, len);
    free(data);
}
EOF
git add -A && git commit -qm "PR: add packet handler"
```

**`gate.sh`**

```bash
#!/usr/bin/env bash
# diff-gate pipeline：只審改動 → 靜態掃 → SARIF 匯總 → 動態驗證 → gate
set -u
BASE="${1:?need baseline commit}"
RULES="rules.yaml"

echo "### 步驟 1：diff — 這次改了哪些 .c 檔"
CHANGED=$(git diff --name-only "$BASE" HEAD | grep '\.c$' || true)
if [ -z "$CHANGED" ]; then echo "無 C 改動，gate PASS"; exit 0; fi
echo "$CHANGED"

echo; echo "### 步驟 2：靜態掃（baseline 只報新增）→ SARIF"
semgrep --config "$RULES" --baseline-commit "$BASE" . --sarif -o new.sarif -q 2>/dev/null
NEW=$(python3 -c 'import json;print(len(json.load(open("new.sarif"))["runs"][0]["results"]))')
echo "新增命中數：$NEW"
if [ "$NEW" -eq 0 ]; then echo "無新增命中，gate PASS"; exit 0; fi

echo; echo "### 步驟 3：SARIF 匯總 — 列出高信命中（file:line）"
python3 - <<'PY'
import json
d = json.load(open("new.sarif"))
for r in d["runs"][0]["results"]:
    loc = r["locations"][0]["physicalLocation"]
    print("  HIGH  {}  {}:{}".format(r["ruleId"],
        loc["artifactLocation"]["uri"], loc["region"]["startLine"]))
PY

echo; echo "### 步驟 4：動態驗證 — 生成 harness + ASan build + 觸發"
TARGET=$(python3 -c 'import json;d=json.load(open("new.sarif"));print(d["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"])')
echo "目標檔：$TARGET"
# 原檔 main 沒呼叫 handle()，sink 不在其執行路徑（Ch 37：沒覆蓋到 ≠ 誤報）
# 寫 harness 直接驅動可疑函式
cat > /tmp/harness.c <<'HEOF'
extern void handle(int fd);
int main(){ handle(0); return 0; }
HEOF
sed 's/int main(){ return 0; }/int __orig_main(){ return 0; }/' "$TARGET" > /tmp/pf_target.c
gcc -g -fsanitize=address -o /tmp/pf_asan /tmp/pf_target.c /tmp/harness.c 2>/dev/null
python3 -c 'import sys,struct; sys.stdout.buffer.write(struct.pack("<i",200)+b"A"*200)' > /tmp/pf_in.bin
ASAN_OUT=$(/tmp/pf_asan < /tmp/pf_in.bin 2>&1 || true)
if echo "$ASAN_OUT" | grep -q "AddressSanitizer"; then
    CONFIRMED=1
    echo "$ASAN_OUT" | grep -E "ERROR: AddressSanitizer|WRITE of size|in handle|overflows this variable" | head -4
else
    CONFIRMED=0
    echo "ASan 未觸發（覆蓋不足或誤報，需人工複查）"
fi

echo; echo "### 步驟 5：gate 判定"
if [ "${CONFIRMED:-0}" -eq 1 ]; then
    echo "GATE: FAIL — 新增且動態確認的漏洞，擋下 PR"; exit 1
else
    echo "GATE: WARN — 有新增靜態命中但動態未確認，需人工複查"; exit 2
fi
```

**真實執行（FAIL 路徑）**

```
$ BASE=$(git rev-parse HEAD~1); bash ./gate.sh "$BASE"; echo "exit: $?"
### 步驟 1：diff — 這次改了哪些 .c 檔
src/proto.c

### 步驟 2：靜態掃（baseline 只報新增）→ SARIF
新增命中數：1

### 步驟 3：SARIF 匯總 — 列出高信命中（file:line）
  HIGH  unbounded-memcpy  src/proto.c:14

### 步驟 4：動態驗證 — 生成 harness + ASan build + 觸發
目標檔：src/proto.c
==368452==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffe86b20b80 ...
WRITE of size 200 at 0x7ffe86b20b80 thread T0
    #1 0x587532a06638 in handle /tmp/pf_target.c:14
    #0 0x587532a06427 in handle /tmp/pf_target.c:7

### 步驟 5：gate 判定
GATE: FAIL — 新增且動態確認的漏洞，擋下 PR
exit: 1
```

閉環驗收成立：步驟 3 的 SARIF 命中 `proto.c:14`（memcpy 那行），步驟 4 的 ASan crash 也在 `handle` 的第 14 行 WRITE 200 bytes——**靜態指出的位置被動態確認**，gate 據此 FAIL。

**真實執行（PASS 路徑）**

```
$ bash ./gate.sh "$(git rev-parse HEAD)"; echo "exit: $?"
### 步驟 1：diff — 這次改了哪些 .c 檔
無 C 改動，gate PASS
exit: 0
```

baseline = HEAD → diff 為空 → 步驟 1 就 PASS。兩條路徑都重現。

</details>

---

## 延伸挑戰

- **接真實 GitHub Actions**（未實測，需真 repo）：把 `gate.sh` 包成一個 workflow——`on: pull_request`，baseline 用 `git merge-base origin/${{ github.base_ref }} HEAD`（不是 `HEAD~1`，多 commit PR 才對，Ch 38 進階延伸），gate FAIL 讓 job 失敗擋 PR。步驟：checkout 帶 `fetch-depth: 0`（要完整歷史算 merge-base）→ 裝 semgrep + gcc → 跑 `gate.sh` → 用 exit code 決定 job 成敗。SARIF 另可 `upload-sarif` 顯示在 PR（Ch 39）。
- **加 baseline 凍結舊債**：現在 gate 只看新增，但完全不管舊債。加一個「舊債清單」（baseline 掃全量存指紋），讓每週跑一次全量、追舊債下降趨勢（Ch 36 的 precision/趨勢度量），PR gate 仍只擋新增。
- **多工具去重**：步驟 2 除了 Semgrep，再跑 CodeQL（對改動檔建 db、跑 query、出 SARIF），用 Ch 39 的合併腳本去重、標跨工具佐證，只對「兩工具都命中」的高信命中做步驟 4 的動態驗證（省 ASan build 成本、優先驗最可疑的）。
- **directed fuzzing 取代手工輸入**：步驟 4 現在手工造 `len=200`。對觸發條件不明顯的深命中，改用 fuzzer（接 `../advanced_fuzzing/`）自動找觸發輸入，甚至用 directed fuzzing 拿 SARIF 命中的 `file:line` 當目標——把「靜態命中直接餵 directed fuzzer」這個 Ch 37 說的天作之合真的接起來。

---

## 本練習你證明了什麼

你親手把 Ch 36-40 的零件組成了一條**能跑、能判、能重現**的 CI 安全閘：diff 縮範圍（Ch 38）、baseline 只報新增（Ch 36/38）、SARIF 當匯流排（Ch 39）、生成 harness + ASan 把靜態懷疑打成動態確認（Ch 37）、據此 gate。最關鍵的教學點是步驟 4——**沒有 harness，sink 不在 main 路徑上，動態驗證就是假陰性**；有了 harness，靜態指出的 `proto.c:14` 被 ASan 在同一行確認。這條「靜態撒網 → diff 收斂 → 動態收口 → gate 判決」的線，就是把整個 Part 7 的方法論落成工程的縮影。

→ [Ch 41 審計反模式](./41-auditing-antipatterns.md)
