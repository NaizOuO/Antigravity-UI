---
name: pdf-document
description: >-
  Use this skill whenever the user asks for a PDF, a printable document, lecture notes (講義／筆記),
  a report (報告), a formal document or letter (正式文件、信件、履歷), slides (簡報), or asks to
  "輸出成 PDF". Write the document as a Typst (.typ) source file; the interface compiles it to PDF.
---

# PDF 文件（Typst）

這個介面會自動把你寫的 `.typ` 檔編譯成 PDF。**你只要寫 `.typ` 檔，不要執行任何指令**（不要 run_command、不要呼叫 typst）。

## 步驟
1. 依需求選一個模板（見下表），讀對應的範例檔，照它的開頭設定寫。
2. 用 write_to_file 把完整的 `.typ` 存到指示裡指定的 `.原始檔` 資料夾（PDF 會自動編譯到它的上一層），檔名有意義（可以中文），副檔名 `.typ`。
3. 回覆使用者：用幾句話說明文件涵蓋了什麼。**不要提 `.typ` 原始檔、它的路徑、Typst 或編譯**——使用者只關心 PDF，
   介面會自動附上 PDF 連結。不要保證頁數或版面（你還沒看到成品），介面編譯後會把每一頁的圖片給你檢查。
4. 如果之後收到「Typst 編譯錯誤」的訊息，依照錯誤的行號修正同一個檔案（用 replace_file_content 或重新 write_to_file），一樣不要執行指令。

| 用途 | 範例檔 |
|---|---|
| 講義、課堂筆記、讀書整理 | [references/notes.typ](references/notes.typ) |
| 報告、實驗報告、正式文件 | [references/report.typ](references/report.typ) |
| 簡報 | [references/slides.typ](references/slides.typ) |
| 信件、申請書、履歷等短文件 | [references/letter.typ](references/letter.typ) |

## 寫作規範（讓重點清楚）
- **只寫使用者要的內容**：不要自己加題外的延伸章節、「與你的研究／實驗室的連結」、「物理直覺拓展」之類的段落；
  使用者的背景只用來決定講解的深淺。整理例題就是題目＋完整解答（＋課堂上的勘誤），不要加料。
- 開頭先放 3～5 點「重點摘要」。
- 框用在定義、定理、例題、注意事項（`#definition`、`#theorem`、`#example`、`#note`、`#warning`）；
  **一個主題最多兩個框**（例如題目一個、解答一個），不要每段都套框、不要框裡再疊不同顏色的框，版面要乾淨。
- 重要公式獨立一行並編號（`$ ... $ <eq:名稱>`），公式下方條列每個變數的意義與單位；用 `@eq:名稱` 引用。
- 每節結尾一段「本節重點」。
- 圖：電路用 zap、流程圖用 fletcher、能階圖／座標圖／幾何用 cetz（範例檔裡有用法）。不要嵌入網路圖片。
- 最後列出參考資料（使用者給的檔名、頁碼、網址）。

## Typst 語法提醒（最常寫錯的地方）
- 數學：`$x^2$` 行內；`$ x^2 $`（前後有空格）獨立一行。分數 `a/b` 或 `frac(a, b)`，希臘字母直接寫 `alpha`、`hbar`、`omega_r`。
- 微分（最常寫錯）：Typst **沒有** `diff`、`d/dt` 這種寫法。請用 physica 的 `dv(f, t)`（全微分）、`dv(f, x, 2)`（二階）、
  `pdv(psi(x, t), t)`（偏微分）、`pdv(psi, x, 2)`；或內建的 `partial`（∂）、`dif x`（積分裡的 dx：`integral_0^1 f(x) dif x`）。
  例：薛丁格方程式 `$ i hbar pdv(psi(x, t), t) = - hbar^2/(2m) pdv(psi(x, t), x, 2) + V(x) psi(x, t) $`
- Dirac 符號用 physica：`#import "@preview/physica:VERSION": *` 後寫 `$ket(psi)$`、`$bra(phi)$`、`$braket(phi, psi)$`。
- 多個字母的變數要加引號或空格：`$E_"C"$`、`$E_C$`（單字母可以）。
- 中文直接寫，不需要特別處理；不要用 LaTeX 指令（`\frac`、`\section`）。
- 套件版本：只能用 [references/packages.md](references/packages.md) 列出的版本，不能自己猜。
