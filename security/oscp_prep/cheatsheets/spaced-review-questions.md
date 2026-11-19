# 間隔複習題庫（Spaced Review）

> 用法：蓋住答案先自己答，答不出的回對應章節。建議每週挑一組重做；考前全部過一遍。答案在每題下方 `<details>`。

## 考試與方法論

**Q1. OSCP 總分、及格線、機器配分？**
<details><summary>答</summary>100 分，及格 70（2026-12-01 起改 100–500 分級分、400 及格）。3 台獨立機器各 20（10 初始 +10 提權）=60；AD 三機鏈 =40。</details>

**Q2. OSCP 與 OSCP+ 差在哪？**
<details><summary>答</summary>OSCP 終身有效；OSCP+（2024-11 起）三年效期、需續證。考試內容相同。</details>

**Q3. 考試對 Metasploit / AI 的限制？**
<details><summary>答</summary>MSF 模組（含 meterpreter）整場只能對「一台」機器用；明確禁用 AI/LLM 助手。</details>

**Q4. 「枚舉 > 一切」是什麼意思？**
<details><summary>答</summary>找不到入口幾乎都是枚舉不夠徹底，而不是機器太難。每拿到新憑證就重跑所有枚舉。</details>

## 枚舉

**Q5. nmap 開場標準三步？**
<details><summary>答</summary>`-p-` 全埠快掃 → 對開放埠 `-sCV` 深掃 → UDP top 100。擋 ping 加 `-Pn`。</details>

**Q6. SMB 無憑證能做什麼？**
<details><summary>答</summary>`smbclient -L //IP/ -N`、`nxc smb IP -u '' -p '' --shares --rid-brute`、enum4linux-ng。</details>

**Q7. 為什麼用 nxc 不用 crackmapexec？**
<details><summary>答</summary>CrackMapExec 自 2023 停止維護，NetExec（nxc）是現役社群分支，語法幾乎相容。</details>

## Web

**Q8. 確認 SQLi 存在的三種測試？**
<details><summary>答</summary>報錯（`'`）、boolean（`AND 1=1` vs `1=2`）、time-based（`SLEEP(5)`）。</details>

**Q9. LFI 如何升級成 RCE？**
<details><summary>答</summary>log poisoning（寫 PHP 進 access.log 再 include）、php://filter 讀原始碼找憑證、php://input / data 包裝器。</details>

**Q10. 檔案上傳常見繞過？**
<details><summary>答</summary>替代副檔名（.phtml/.php5/.phar）、雙副檔名、大小寫、改 Content-Type、加 GIF89a 魔術字元、.htaccess。</details>

## Linux 提權

**Q11. 到手一個 Linux shell，枚舉順序？**
<details><summary>答</summary>升 TTY → `sudo -l` → SUID → cron → 可寫檔/服務 → capabilities → NFS → 內核（最後）。</details>

**Q12. 看到可 sudo 的 binary 怎麼辦？**
<details><summary>答</summary>查 GTFOBins 的 sudo 區塊找 escape。</details>

**Q13. SUID 自訂 binary（不在 GTFOBins）？**
<details><summary>答</summary>`strings`/`ltrace` 看它呼叫什麼；若用相對路徑呼叫其他程式 → PATH 劫持。</details>

## Windows 提權

**Q14. `whoami /priv` 哪兩個權限是金礦？**
<details><summary>答</summary>SeImpersonatePrivilege（→ PrintSpoofer/GodPotato → SYSTEM）、SeBackupPrivilege（→ 抄 SAM/SYSTEM）。</details>

**Q15. 服務提權的三種漏洞？**
<details><summary>答</summary>unquoted service path、可寫服務執行檔、可改服務設定（sc config）。用 accesschk 確認權限。</details>

**Q16. Windows 提權常靠什麼而非漏洞？**
<details><summary>答</summary>撿到的密碼：cmdkey、registry autologon/PuTTY/VNC、unattend.xml、PowerShell history。</details>

## Active Directory

**Q17. Kerberos 操作報 clock skew 怎麼修？**
<details><summary>答</summary>`sudo ntpdate DC_IP` 把 Kali 時間對到 DC。</details>

**Q18. AS-REP Roasting vs Kerberoasting 前提？**
<details><summary>答</summary>AS-REP：帳號設了不要求預先驗證（免憑證）。Kerberoast：帳號有 SPN（需任一有效憑證）。</details>

**Q19. Pass-the-Hash 能用 Responder 抓到的 hash 嗎？**
<details><summary>答</summary>不行。Responder 抓的是 NetNTLMv2，只能離線破（-m 5600）或 relay，不能 PtH。</details>

**Q20. 接管 Domain 的典型終局？**
<details><summary>答</summary>取得 Replication 權限或 DA → DCSync 抽 krbtgt/administrator hash → psexec 進 DC（或 Golden Ticket 持久化）。</details>

## 進階

**Q21. Ligolo-ng 比 proxychains+Chisel 好在哪？**
<details><summary>答</summary>建 tun 介面把內網網段變本機路由，所有工具直連內網 IP 免加 proxychains，速度快、多段乾淨。</details>

**Q22. Buffer Overflow 在現行 OSCP 考試的地位？**
<details><summary>答</summary>自 ~2023 起已非計分項。仍建議學（PG/靶機會遇到、有助讀 exploit），但備考時間緊可後置。</details>

**Q23. 拿到 shell 第一件事？**
<details><summary>答</summary>升級 TTY（Linux：python pty → stty raw -echo → export TERM），否則操作易斷、誤判。</details>

**Q24. 截圖紀律最關鍵的兩張？**
<details><summary>答</summary>local.txt + ip 同畫面、proof.txt + whoami(root/SYSTEM) + ip 同畫面。沒截等於沒分。</details>

---

答對 20/24 以上 → 方法論已內化，重心放上機手感。答對 < 15 → 回對應 Part 重讀 + 上機。
