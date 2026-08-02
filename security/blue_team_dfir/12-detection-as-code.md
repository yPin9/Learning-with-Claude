# Ch 12 — Detection-as-Code

> 目標：把偵測規則用軟體工程的方式管理——版控、單元測試、CI/CD 部署、指標追蹤——讓規則品質可量測、可回滾、可持續改善，而不是靠人的記憶和勇氣。

## 為什麼偵測規則需要工程化？

你現在有 Sigma 規則、YARA 規則、correlation rule，分散在 SIEM 的 GUI 裡、某個人的 desktop、某個 Google Doc——修改沒有記錄，誰改了什麼、什麼時候改的、為什麼改，沒有人知道。

這不是資安問題，這是**工程問題**。軟體工程花了 30 年解決這個問題，工具和實踐都已經成熟，我們只需要把它們搬到偵測規則上。

Detection-as-Code（DaC）的核心主張：**偵測規則是程式碼，用管理程式碼的方式管理它**。

對照軟體工程：

| 軟體工程 | Detection-as-Code |
|---|---|
| 程式碼放 git | 規則放 git |
| 單元測試（unit test） | 用已知攻擊 telemetry 測試規則是否命中 |
| CI pipeline（lint + test + deploy） | 規則語法驗證 + atomic test + 自動部署到 SIEM |
| Code review（PR） | 規則 PR review（至少一人 approve） |
| Changelog / release note | 規則版本說明（為何修改、影響） |
| Rollback（git revert） | 規則回滾（SIEM 可以載回舊版） |
| 效能 benchmark | 規則假陽性率、命中率 metrics |
| 技術債管理 | 廢棄規則退役 |

## 規則庫的 Git 結構

一個合理的 Detection-as-Code repo 結構：

```
detections/
├── rules/
│   ├── windows/
│   │   ├── credential_dumping/
│   │   │   ├── sigma_lsass_memory_access.yml
│   │   │   └── sigma_procdump_lsass.yml
│   │   ├── execution/
│   │   │   └── sigma_powershell_encoded_command.yml
│   │   └── persistence/
│   │       └── sigma_registry_run_key.yml
│   ├── linux/
│   └── network/
├── yara/
│   ├── malware_families/
│   │   └── cobalt_strike_beacon.yar
│   └── packers/
│       └── upx_packed.yar
├── tests/
│   ├── fixtures/           # 錄製的真實或模擬 telemetry
│   │   └── t1003.001_lsass_procdump_event.json
│   └── test_rules.py       # 測試腳本
├── pipelines/
│   └── deploy.yml          # CI/CD 設定
└── docs/
    └── rule_lifecycle.md
```

每條 Sigma 規則一個檔案，目錄對應 ATT&CK tactic。這讓搜尋和 PR review 都有明確邊界。

## 規則的單元測試

測試偵測規則的邏輯和測試程式碼一樣：給定**已知輸入**，驗證**是否有預期輸出**。

已知輸入有兩種來源：

**來源 1：Atomic Red Team 的真實 telemetry**

Atomic Red Team（[https://github.com/redcanaryco/atomic-red-team](https://github.com/redcanaryco/atomic-red-team)）提供每個 ATT&CK technique 的攻擊模擬腳本。你在測試環境跑一次，把 SIEM 收到的 event 匯出成 JSON，存到 `tests/fixtures/`。

```bash
# 在 Windows 測試 VM 執行 Atomic（PowerShell）
Invoke-AtomicTest T1003.001 -TestNumbers 1

# 從 Sysmon / Event Log 匯出 event
Get-WinEvent -LogName "Microsoft-Windows-Sysmon/Operational" |
  Where-Object { $_.TimeCreated -gt (Get-Date).AddMinutes(-5) } |
  ConvertTo-Json | Out-File t1003.001_lsass_procdump_event.json
```

**來源 2：手工建立的最小測試 fixture**

如果你不想跑真實攻擊，可以手動造一個符合規則觸發條件的最小 JSON event：

```json
{
  "@timestamp": "2025-01-15T03:24:00Z",
  "event.code": "10",
  "process.name": "procdump.exe",
  "process.command_line": "procdump64 -accepteula -ma lsass.exe lsass.dmp",
  "target.process.name": "lsass.exe",
  "host.name": "VICTIM-PC",
  "user.name": "DOMAIN\\attacker"
}
```

**測試腳本範例（Python + sigma-cli）**：

```python
import subprocess
import json
import pytest

def test_lsass_procdump_detection():
    """T1003.001 — procdump 針對 lsass.exe 應該觸發告警"""
    fixture = "tests/fixtures/t1003.001_lsass_procdump_event.json"
    rule    = "rules/windows/credential_dumping/sigma_procdump_lsass.yml"

    # sigma-cli 的 check 模式：輸入 event，輸出是否命中
    result = subprocess.run(
        ["sigma", "check", "--rule", rule, "--event", fixture],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"規則命中失敗：{result.stderr}"

def test_lsass_procdump_no_false_positive():
    """正常 IT 操作不應觸發 lsass procdump 規則"""
    fixture = "tests/fixtures/normal_task_manager_event.json"
    rule    = "rules/windows/credential_dumping/sigma_procdump_lsass.yml"

    result = subprocess.run(
        ["sigma", "check", "--rule", rule, "--event", fixture],
        capture_output=True, text=True
    )
    # 預期不命中（returncode != 0 或 output 為空）
    assert "no match" in result.stdout or result.returncode != 0
```

同時測試**應該命中**和**不應該命中**，才能確保規則在調校後沒有造成假陽性。

## CI/CD Pipeline

CI/CD 在每次 PR 合入前自動跑：

```yaml
# .github/workflows/detection-ci.yml
name: Detection Rules CI

on:
  pull_request:
    paths:
      - 'rules/**'
      - 'yara/**'

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install tools
        run: |
          pip install sigma-cli pySigma-backend-splunk
          apt-get install -y yara

      - name: Sigma syntax validation
        run: |
          sigma check rules/**/*.yml
        # sigma check 驗語法，格式錯誤直接 fail

      - name: YARA syntax validation
        run: |
          find yara/ -name "*.yar" -exec yara {} /dev/null \;
        # yara 對空輸入跑規則，語法錯誤會 exit nonzero

      - name: Run unit tests
        run: |
          pytest tests/ -v

      - name: Convert Sigma to SIEM backends
        run: |
          sigma convert -t splunk rules/**/*.yml -o dist/splunk/
          sigma convert -t es-ql  rules/**/*.yml -o dist/elastic/
        # 確保轉換不報錯

  deploy:
    needs: validate
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to SIEM (Splunk)
        env:
          SPLUNK_TOKEN: ${{ secrets.SPLUNK_TOKEN }}
        run: |
          python scripts/deploy_to_splunk.py --dir dist/splunk/
        # 自動把轉換好的規則推進 Splunk saved searches
```

這個 pipeline 做三件事：
1. **Validate**：語法正確、測試通過
2. **Convert**：把 Sigma 轉成所有目標 SIEM 的語言
3. **Deploy**：只有 main branch 的提交才部署到 production SIEM

PR 要通過這三關才能 merge，不讓壞規則進到 production。

## 規則生命週期

Detection-as-Code 不只是技術實踐，也是流程管理。規則有完整的生命週期：

```
提出（Propose）
    ↓
  • 攻擊研究或事件後發現 detection gap
  • 新開 GitHub Issue，描述 technique / 攻擊場景
  • 建立 rule branch：feature/detect-t1003-001-comsvcs

開發（Develop）
    ↓
  • 寫 Sigma / YARA 規則
  • 建立測試 fixture（Atomic Red Team 或手工造）
  • 本地驗證

審查（Review）
    ↓
  • 開 PR，至少一名其他 analyst review
  • CI pipeline 自動驗語法與測試
  • Review 重點：條件是否夠精確、有沒有漏掉常見變體、
    假陽性來源分析

部署（Deploy）
    ↓
  • Merge 進 main → CI 自動部署到 SIEM
  • 先推到 staging（shadow mode：偵測但不告警）
  • 觀察 7 天假陽性率，低於 threshold 再推 production

調校（Tune）
    ↓
  • 收集假陽性案例 → 在規則加例外條件
  • 每次調校都是 PR + review，保有完整歷史

退役（Retire）
    ↓
  • 攻擊技術過時、規則被更好的規則取代、
    data source 改變導致規則永遠不命中
  • 開 PR 刪除規則，在 CHANGELOG 記錄退役原因
```

Shadow mode 是關鍵實踐：新規則先在 SIEM 裡跑、記 log，但不產出告警。觀察一週的假陽性數量，如果每天 < 5 條，才開告警。這比直接上 production 造成告警爆炸好太多。

## Metrics：讓規則品質可量測

Detection-as-Code 的最終目的是讓規則品質可被量測、可被改善。追蹤的 metrics：

**每條規則的 metrics**：

| Metric | 計算方式 | 目標值 |
|---|---|---|
| True Positive Rate（TPR） | 規則命中且確認惡意 / 全部命中 | 越高越好（> 80%） |
| False Positive Rate（FPR） | 規則命中但確認無害 / 全部命中 | 越低越好（< 10%） |
| 平均每天告警數 | 過去 30 天告警 / 30 | 太高 = 調校；太低 = 可能死規則 |
| 最後一次真實命中 | 規則最後一次產出 TP 的時間 | > 90 天沒命中 = 考慮退役 |

**規則庫整體 metrics**：

| Metric | 意義 |
|---|---|
| ATT&CK 技術涵蓋率 | 有規則覆蓋的 technique 比例（Ch 10 的涵蓋度分析） |
| 平均規則年齡 | 太老 = 技術和 data source 可能已改變 |
| 測試覆蓋率 | 有單元測試的規則 / 全部規則 |
| 部署頻率 | 每月新增/修改規則數量（太低 = 規則庫在老化） |

這些 metrics 可以輸出到 dashboard（Grafana / Splunk dashboard），讓管理層看到偵測工程的健康狀態。

## 實戰：一次完整的規則提出到部署流程

場景：IR 發現攻擊者用 `comsvcs.dll` 的 MiniDump export 轉儲 lsass，現有的 procdump 規則沒有覆蓋這個變體。

```bash
# Step 1：開 branch
git checkout -b feature/detect-t1003-001-comsvcs

# Step 2：寫規則
cat > rules/windows/credential_dumping/sigma_comsvcs_lsass_dump.yml << 'EOF'
title: LSASS Memory Dump via comsvcs.dll MiniDump
id: b7b4a37f-8b4f-4c6d-a891-3e3f2d3d9c11
status: experimental
description: |
    攻擊者透過 rundll32 呼叫 comsvcs.dll 的 MiniDump export 轉儲 LSASS 記憶體，
    規避需要 procdump.exe 的偵測規則。
references:
    - https://attack.mitre.org/techniques/T1003/001/
    - https://lolbas-project.github.io/lolbas/Libraries/Comsvcs/
author: blue-team-analyst
date: 2025/01/15
tags:
    - attack.credential_access
    - attack.t1003.001
logsource:
    category: process_creation
    product: windows
detection:
    selection:
        CommandLine|contains|all:
            - 'comsvcs'
            - 'MiniDump'
            - 'lsass'
    condition: selection
falsepositives:
    - 合法的 crash dump 工具（需結合 parent process 排除）
level: high
EOF

# Step 3：建立 fixture（手工造最小 event）
cat > tests/fixtures/t1003.001_comsvcs_minidump.json << 'EOF'
{
  "@timestamp": "2025-01-15T02:00:00Z",
  "event.code": "1",
  "process.name": "rundll32.exe",
  "process.command_line": "rundll32.exe C:\\Windows\\System32\\comsvcs.dll MiniDump 672 lsass.dmp full",
  "process.parent.name": "cmd.exe",
  "host.name": "CORP-PC-01",
  "user.name": "DOMAIN\\attacker"
}
EOF

# Step 4：本地測試
sigma check rules/windows/credential_dumping/sigma_comsvcs_lsass_dump.yml \
           tests/fixtures/t1003.001_comsvcs_minidump.json

# Step 5：開 PR，CI 自動跑驗證
git add rules/ tests/
git commit -m "feat: detect lsass dump via comsvcs.dll MiniDump (T1003.001)"
git push origin feature/detect-t1003-001-comsvcs
gh pr create --title "Detection: LSASS dump via comsvcs.dll" \
             --body "覆蓋 T1003.001 comsvcs 變體，已通過 atomic test fixture 驗證"
```

PR 合入 → CI 驗證 → 自動部署到 staging → 7 天後推 production。整個流程有 git 記錄，有 review 歷史，可以隨時回滾。

## 踩雷

1. **測試 fixture 太完美**：手工造的 fixture 完全符合規則，但真實攻擊的 event 欄位可能有大小寫差異、路徑帶反斜線或正斜線、PID 格式不一致。盡可能用真實的 Atomic Red Team telemetry 而不是手工造。

2. **CI 部署到 SIEM 需要 credential 管理**：SIEM API token 放在 GitHub Actions secret 是標準做法，但要定期 rotate，且最小權限（只允許 create/update saved searches，不能 delete 資料）。

3. **規則 ID（`id` 欄位）沒有全域唯一**：Sigma 規則的 `id` 應該是 UUID v4，不能重複。如果兩條規則 id 相同，SIEM 部署腳本可能覆蓋掉舊規則。CI 裡加一個 id uniqueness check。

4. **Shadow mode 觀察期太短**：某些攻擊手法本來就不常見，7 天沒看到假陽性不代表沒有，只代表合法使用者這週剛好沒觸發。觀察期搭配業務日曆（月底/季末通常有異常操作）。

5. **退役規則沒有通知**：靜靜地刪掉一條規則，下個月沒人記得為什麼那個 technique 沒有偵測了。退役要寫 CHANGELOG，說明退役原因（「被更精確的 rule X 取代」「data source 已停收」）。

## 進階延伸

- **Panther**（[https://panther.com/](https://panther.com/)）：雲端原生 SIEM，規則用 Python 寫，天生就是 code-first 設計，適合已經有 DevOps 文化的團隊。
- **Tines / Torq**：SOAR 平台，可以把 DaC 的 CI/CD 延伸到自動化回應（alert → 自動隔離 host → Slack 通知 → 開 ticket）。
- **OpenRelik**（前身 Timesketch Team）：把 timeline 分析整合進 DaC 工作流，規則命中後自動建 timeline 供分析師查核。
- **規則品質 scoring**：Red Canary 的 blog 有一套規則品質評分方法（specificity × coverage × testability），參考他們的 Detection Engineering framework。

## 本章重點整理

- Detection-as-Code = 把規則用程式碼管理方式（git + CI/CD + review + metrics）管理
- 測試 fixture 來源：Atomic Red Team 真實 telemetry 或手工最小 event；要同時測 TP 和 FP
- CI pipeline 三關：語法驗證 → 單元測試 → SIEM backend 轉換；合入後自動部署
- 規則生命週期六階段：提出 → 開發 → 審查 → 部署（shadow mode 先） → 調校 → 退役
- 關鍵 metrics：TPR、FPR、每日告警數、最後命中時間、測試覆蓋率
- 規則 ID 要全域唯一（UUID v4）；退役規則要寫 CHANGELOG

## 自我檢核

- [ ] 能說出 Detection-as-Code 對應軟體工程的五個類比
- [ ] 知道測試 fixture 的兩種來源，以及為什麼要同時測 TP 和 FP case
- [ ] 能描述 CI pipeline 的三個主要 job 做什麼
- [ ] 能說出規則生命週期的六個階段
- [ ] 知道 shadow mode 的目的以及觀察期的意義
- [ ] 能說出追蹤規則品質的 4 個 metrics

## 延伸閱讀

1. **"Detection Engineering using Sigma" — Florian Roth, 2021**（YouTube 演講，可搜尋）
   — Sigma 作者本人講 DaC 的設計理念與工作流；30 分鐘，資訊密度高。

2. **Red Canary Detection Engineering Blog** [https://redcanary.com/blog/engineering/](https://redcanary.com/blog/engineering/)
   — 業界最透明公開 DaC 實踐的部落格；規則品質指標與 pipeline 設計是他們的強項。

3. **Atomic Red Team** [https://github.com/redcanaryco/atomic-red-team](https://github.com/redcanaryco/atomic-red-team)
   — 每個 ATT&CK technique 的標準化攻擊模擬；你的測試 fixture 主要來源，必須熟悉。

4. **sigma-cli 文件** [https://github.com/SigmaHQ/sigma-cli](https://github.com/SigmaHQ/sigma-cli)
   — CI pipeline 裡跑 `sigma check` 和 `sigma convert` 的工具；支援的 backend 和 pipeline 設定在此。

5. **"Measuring Detection Coverage"** — SpecterOps, 2021（可搜尋部落格文章）
   — 量化偵測有效性的方法論，補充 Ch 10 涵蓋度分析的 quality 那一層；TPR/FPR 追蹤的最佳實踐。

---

Part 1 的偵測工程基礎到這裡結束。接下來用一個完整的 purple team 練習把 Sigma + YARA + ATT&CK 對映全部串起來。

→ [練習 A：對已知攻擊技術寫 Sigma + YARA 偵測](./practice-a-write-detections.md)
