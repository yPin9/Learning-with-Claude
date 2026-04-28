# Ch 27 — 紅藍隊演習方法論

> 目標：認識紅 / 藍 / 紫隊概念、MITRE ATT&CK、purple team 演習怎麼跑。

## 紅 / 藍 / 紫 / 黃 隊

| 隊 | 角色 |
|---|---|
| **紅隊** (Red Team) | 模擬攻擊者，找漏洞 |
| **藍隊** (Blue Team) | 防禦 + 偵測 + 響應 |
| **紫隊** (Purple Team) | 紅 + 藍協作（不對抗） |
| **黃隊** (Yellow Team) | dev (寫 code 的人) |
| **綠隊** (Green Team) | yellow + blue (DevOps + Sec) |
| **橘隊** (Orange Team) | yellow + red (dev + offensive) |
| **白隊** (White Team) | 監督演習 |

紅藍紫是主流。其他衍生概念。

## Red Team vs Pentest

兩者不同：

| 維度 | Pentest | Red Team |
|---|---|---|
| 範圍 | 明確（某 app / 某 endpoint） | 廣（整個公司） |
| 時間 | 短（幾天-幾週） | 長（幾週-幾個月） |
| 目的 | 找盡可能多漏洞 | 模擬真實 APT 攻擊鏈 |
| 知會 | 全部知道 | 只 stakeholders 知 |
| 對抗 | 沒 | 跟 blue team 對抗 |
| 終點 | report 漏洞 | 達成 objective (如 拿 admin / steal X 資料) |

「**Pentest 找漏洞，Red team 試達成攻擊目標**」。

## MITRE ATT&CK Framework

「**攻擊技術 + 戰術的 knowledge base**」。

https://attack.mitre.org/

12 戰術 (Tactics)：

1. Reconnaissance
2. Resource Development
3. Initial Access
4. Execution
5. Persistence
6. Privilege Escalation
7. Defense Evasion
8. Credential Access
9. Discovery
10. Lateral Movement
11. Collection
12. Command and Control + Exfiltration + Impact

每戰術下有多 techniques（總共 200+）。每 technique 含：

- 詳細描述
- 真實 APT 用過的例
- detection 建議
- mitigation

「**ATT&CK 是現代 SOC 共通語言**」。

例：

- Tactic: Initial Access
- Technique: T1190 Exploit Public-Facing Application
- Sub-technique: T1190.001 ...

## Cyber Kill Chain (Lockheed Martin)

老 framework，但仍常用：

```
1. Reconnaissance
2. Weaponization
3. Delivery
4. Exploitation
5. Installation
6. Command and Control
7. Actions on Objectives
```

「**break the kill chain**」 — 任何階段擋下都阻止 breach。

## Diamond Model

1 個 attack = 4 個維度：

- Adversary (誰攻)
- Capability (用什麼工具 / 技術)
- Infrastructure (從哪攻)
- Victim (攻誰)

threat intel 用此 model 描述 actor。

## Purple Team 演習

紅 + 藍**協作**，不是對抗：

```
1. 紅隊執行 1 個 ATT&CK technique
2. 藍隊看 SIEM / log 是否 detect
3. 一起討論 detection gap
4. 藍隊改 detection rule
5. 紅隊驗證新 rule 有效
6. 文件化
7. 下個 technique
```

「**漸進改善 detection**」。

## Atomic Red Team

開源 ATT&CK technique 模擬庫：

```bash
git clone https://github.com/redcanaryco/atomic-red-team.git
```

每 technique 有可執行 atomic test：

```yaml
# T1003.001 - LSASS Memory Dump
- name: Dump LSASS via comsvcs.dll
  description: ...
  executor:
    command: |
      C:\Windows\system32\rundll32.exe C:\windows\system32\comsvcs.dll, MiniDump <PID> dump.bin full
```

跑 → 看 SIEM 抓不抓到。**最簡 purple team 工具**。

## Caldera (MITRE)

自動化 red team agent：

- 部署 agent 到 host
- agent 執行 ATT&CK techniques
- 記錄結果
- 自動化整 attack chain

「**adversary simulation**」工具。

## Breach and Attack Simulation (BAS)

商業 BAS 平台：

- **AttackIQ**
- **SafeBreach**
- **Picus Security**

跑 attack technique → 評估 control coverage → dashboard。

「**24/7 自動化驗證 detection**」。

## 演習類型

### 1. Tabletop exercise

純桌上討論。沒實際攻擊：

- 「**假設 ransomware 進來，我們會怎麼做**」
- discuss IR plan
- 找 gap

低成本 / 低風險，**第一步**。

### 2. Capture the Flag (CTF)

短期 / 帶有遊戲性：

- HackTheBox / TryHackMe / VulnHub
- 解題 / 拿 flag

訓練個人 skill。

### 3. Pentest

正式 engagement：

- scope 明確
- 簽合約
- 報告

### 4. Red Team

長期、stealthy、模擬 APT。少數公司有資源。

### 5. Bug Bounty

continuous external testing。

### 6. Purple Team

紅藍協作。

選哪個看：成熟度 + 預算 + 目的。

## SOAR (Security Orchestration, Automation, Response)

藍隊自動化平台：

- 接 SIEM alert
- 自動 enrichment（whois / virustotal / threat intel）
- 自動 containment（isolate host / block IP）
- 工單 / notification

工具：

- **Splunk SOAR**（前 Phantom）
- **Palo Alto XSOAR**（前 Demisto）
- **TheHive**（開源）

「**alert 太多 → 人工處理不來 → 自動化**」。

## SIEM 設計

選 SIEM：

- **Splunk** (商業, 強大)
- **Elastic Security** (開源 ELK + 安全)
- **Microsoft Sentinel** (Azure)
- **Wazuh** (開源)
- **Sumo Logic / Datadog SIEM** (SaaS)

關鍵：

- log 來源廣（network / endpoint / app / cloud）
- correlation rule
- detection content（自寫 + 商業 feed）
- 響應 workflow

## Threat Intelligence

「**知道誰在攻、用什麼 TTPs**」：

- **MISP** (open source threat sharing)
- 商業 feed (CrowdStrike / Recorded Future)
- 政府 (CISA / 國家 CERT)

整合到 SIEM → 看到 known bad IP / domain → alert。

## Detection Engineering

「**寫 detection rule**」是現代藍隊核心 skill。

工具：

- **Sigma**（vendor-neutral detection rule format）
- **YARA**（malware detection）

```yaml
# Sigma rule 範例：偵測 LSASS dump
title: LSASS Memory Dump
description: Detects MiniDump of LSASS
logsource:
  product: windows
  service: sysmon
detection:
  selection:
    EventID: 10
    TargetImage: '*lsass.exe'
  filter:
    SourceImage:
      - '*\windowsdefender.exe'
  condition: selection and not filter
falsepositives:
  - Defender activities
level: high
```

寫 rule → SIEM 編譯 → alert。

## 紅藍對抗心理

紅隊心法：

- **stealth**：避免被偵測
- **persistence**：保持 access
- **escalation**：privilege + lateral
- **achieve objective**

藍隊心法：

- **assume breach**：假設已經被攻
- **defense in depth**
- **detection > prevention**（總有漏，重要的是看到）
- **fast response**

## 一個常見誤解：「藍隊只防、不攻」

**錯**。藍隊也要會「**攻擊者思維**」 — 才知道對手會怎麼行動。

「**有 attacker mindset 的 defender**」是頂級藍隊。

## 一個常見誤解：「紫隊比紅 / 藍好」

**部分對**。紫隊適合「**改善 detection**」。但：

- 真實 APT 不會跟你協作
- 紅隊壓力測試 detection 能力
- 兩者互補

## 一個常見誤解：「中小公司不需要紅藍隊」

**錯**。中小公司：

- 沒資源養紅隊 → 用 bug bounty / pentest
- 沒專業藍隊 → 用 MDR (Managed Detection and Response)
- 仍要 monitoring + IR plan

「**安全程度 ≠ 隊伍規模**」。

## 動手練習

**1. 學 ATT&CK**

https://attack.mitre.org/

挑 5 個 technique 詳讀（如 T1190 / T1078 / T1059）。

**2. Atomic Red Team 跑**

```bash
git clone https://github.com/redcanaryco/atomic-red-team.git

# 跑 1 個 atomic
# T1486: Data Encrypted for Impact (但別在重要系統跑！)
```

**3. 寫 Sigma rule**

對 1 個 attack scenario 寫 detection rule。

**4. 對自己 app pentest**

把自己當 red team 攻自己 app（已做過 in 練習 A）。

**5. 看 incident report**

讀真實 incident report：

- US-CERT / CISA advisories
- DFIR (Digital Forensics & IR) writeups
- vendor breach disclosure

了解 attack chain + defense response。

## 自我檢核

- [ ] 紅 / 藍 / 紫 / 黃 隊概念
- [ ] Pentest vs Red Team 差別
- [ ] MITRE ATT&CK 12 戰術知道
- [ ] 演習類型 6 種知道
- [ ] SIEM / SOAR 概念
- [ ] 寫過 Sigma rule 或讀懂

Part 5 結束。Final Project 整合所有 part 寫完整 pentest report。

→ [Final Project：完整 web app pentest report](./final-project-pentest-report.md)
