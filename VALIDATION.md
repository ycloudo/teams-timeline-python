# Python 版驗證紀錄

2026-11-15（Asia/Taipei）。

- 本機隔離測試 4 項通過：stdio MCP 工具、日期與 SQLite、Chrome DOM fixture、背景工作取消與恢復。
- 使用新版獨立 Chrome profile 登入 Teams，以 MCP 呼叫收集 2026-11-09 至 2026-11-15，限制最多兩個聊天及每聊天 10 頁。
- 個人聊天與群組聊天均看到起點之前的訊息，期間內共 17 則，沒有日期不明紀錄。第一頁讀取 limit=1 時正確提供下一頁 offset。
- 狀態為 partial：探索刻意設置兩個聊天上限；兩個聊天分別有單頁最多 9 和 6 個未支援節點，因此狀態為 boundary_needs_review，不能宣稱訊息完整。
- 結束 MCP 程序並重新啟動後，使用相同 job ID 恢復重掃；再次得到 17 個不同訊息 key，未觀察到重複。這不代表所有訊息皆已擷取。
- 後續依使用者要求切換 MCP 並移除舊版程式；舊版資料及 Chrome profile 保存在 `data/legacy-node/`，尚未合併至新版資料庫。尚未完成全聊天及長期運行驗證。
