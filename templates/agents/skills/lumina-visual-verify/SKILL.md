---
name: lumina-visual-verify
description: Verifies programs that have a screen — games, desktop windows, console drawing, and HTML pages — by actually operating them with lumina's run_capture tool (key presses, waits, screenshots, bursts) and comparing frames. Use before claiming any visual program works.
---

# 有畫面的程式怎麼驗證

只截開場畫面不算驗證過。一定要實際操作，逐張比較截圖確認有反應。

1. 用 lumina 的 run_capture 執行，actions 裡排一段完整流程：
   - `wait` 等程式開好
   - `press` / `hold` 按鍵（方向鍵、跳、攻擊）
   - `shot` 截圖；動作或動畫用 `rec` 連拍，先看「連拍總覽」那張
   - 網頁（.html）還能用 `click`、`drag`
2. 逐張檢查：
   - 角色有沒有往按的方向動？方向、座標有沒有反？
   - 敵人、計時器有沒有在動？會不會卡住？
   - 動畫是整個角色一起動，還是只有一部分（例如只有身體在轉）？
   - 有沒有錯誤訊息、主控台錯誤？
3. 驗收測試裡也要模擬一段真實流程（例如按方向鍵幾秒、用真實的 dt 跑一長段模擬），不只測個別函式。
4. 3D 程式在隱藏桌面可能截到黑畫面，run_capture 會改用引擎自己截圖；照工具說明處理。
5. 在回答裡寫出你按了什麼、在第幾張圖看到什麼，標【已驗證】。
