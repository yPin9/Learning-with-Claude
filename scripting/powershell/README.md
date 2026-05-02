# PowerShell 全面學習筆記：從零到系統維運自動化

> 給完全沒碰過 PowerShell 的工程師，目標是寫出能在真實 Windows 環境部署的系統維運腳本。

這系列從 Shell 的思維方式講起，覆蓋語言核心、腳本工程、系統管理、遠端管理、Active Directory，最後整合成一個可部署的維運自動化模組。全程 Windows 11 + PowerShell 7，每章都有能直接跑的範例。

## 為什麼學 PowerShell？

- **Windows 生態的第一語言**：AD、GPO、WMI、WinRM、Task Scheduler 全都有官方 cmdlet，不用 PS 就只能點 GUI
- **物件 Pipeline 比 bash 強大**：傳的是 .NET 物件不是文字，不需要 awk/sed 就能做複雜篩選
- **模組化與可重用性**：自訂模組可以發到 PSGallery，整個團隊共享

## 課程地圖

### Part 1 — 起點：環境與 Shell 思維
- [Ch 0 環境建置](./00-environment-setup.md)
- [Ch 1 Shell 的思維方式](./01-shell-mindset.md)
- [Ch 2 Pipeline 初探](./02-pipeline-intro.md)
- [Ch 3 變數與資料型別](./03-variables-and-types.md)
- [Ch 4 運算子全覽](./04-operators.md)

### Part 2 — 語言核心
- [Ch 5 流程控制](./05-control-flow.md)
- [Ch 6 函式與作用域](./06-functions-and-scope.md)
- [Ch 7 物件深入](./07-objects-deep-dive.md)
- [Ch 8 Pipeline 深入](./08-pipeline-deep-dive.md)
- [Ch 9 字串與正規表示式](./09-strings-and-regex.md)
- [Ch 10 陣列與 Hashtable 進階](./10-arrays-and-hashtables.md)
- [Ch 11 格式化與輸出](./11-formatting-and-output.md)

### Part 3 — 腳本工程
- [Ch 12 腳本參數化](./12-script-parameters.md)
- [Ch 13 錯誤處理](./13-error-handling.md)
- [Ch 14 檔案系統操作](./14-filesystem-operations.md)
- [Ch 15 文字與結構化資料](./15-structured-data.md)
- [Ch 16 偵錯技巧](./16-debugging.md)
- [Ch 17 排程工作自動化](./17-scheduled-tasks.md)
- [練習 A：日誌分析腳本](./practice-a-log-analysis.md)

### Part 4 — 系統管理
- [Ch 18 行程與服務](./18-process-and-service.md)
- [Ch 19 本機使用者與群組](./19-local-users-and-groups.md)
- [Ch 20 網路管理工具](./20-network-tools.md)
- [Ch 21 登錄檔操作](./21-registry.md)
- [Ch 22 CIM / WMI 查詢](./22-cim-wmi.md)
- [Ch 23 事件日誌](./23-event-log.md)
- [練習 B：系統健康報告腳本](./practice-b-health-report.md)

### Part 5 — 遠端管理與 Active Directory
- [Ch 24 PSRemoting 基礎](./24-psremoting-basics.md)
- [Ch 25 PSRemoting 進階與 Jobs](./25-psremoting-advanced-jobs.md)
- [Ch 26 Active Directory 模組](./26-active-directory.md)
- [Ch 27 GPO、DNS、DHCP 腳本化](./27-gpo-dns-dhcp.md)
- [練習 C：AD 批次建立使用者腳本](./practice-c-ad-bulk-users.md)

### Part 6 — 模組化與進階整合
- [Ch 28 自訂模組開發](./28-custom-modules.md)
- [Ch 29 REST API 整合與安全實踐](./29-rest-api-and-security.md)
- [Final Project：系統維運自動化套件](./final-project-sysops-toolkit.md)

## 學習方式建議

1. **每章都要跟著敲**：複製貼上不算學，跟著打一遍才有肌肉記憶
2. **故意改壞再看錯誤**：PowerShell 的錯誤訊息比多數語言友善，敢犯錯才學得快
3. **查文件要用 `Get-Help`**：不要每次都 Google，培養在 shell 裡查的習慣

## 參考資料

- 官方文件：https://learn.microsoft.com/powershell/
- PowerShell Gallery：https://www.powershellgallery.com/
- about_* 主題：在 shell 輸入 `Get-Help about_*` 列出所有概念文件
