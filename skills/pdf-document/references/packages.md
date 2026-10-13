# 可以用的 Typst 套件（只能用這些版本）

這些套件已經下載在本機，編譯時不連網。用其他套件或其他版本會編譯失敗。

| 套件 | import 寫法 | 用途 |
|---|---|---|
| physica | `#import "@preview/physica:0.9.8": *` | Dirac 符號 `ket`、`bra`、`braket`，偏微分 `pdv`、向量等 |
| cetz | `#import "@preview/cetz:0.5.2"` | 畫能階圖、座標圖、幾何圖（`cetz.canvas({ import cetz.draw: * ... })`） |
| fletcher | `#import "@preview/fletcher:0.5.8" as fletcher: diagram, node, edge` | 流程圖、方塊圖、狀態圖 |
| zap | `#import "@preview/zap:0.6.0"` | 電路圖（電阻、電容、電感、電源…；沒有約瑟夫森接面，接面請用 cetz 畫一個打叉的方框） |
| touying | `#import "@preview/touying:0.8.0": *` 搭配 `#import themes.metropolis: *` | 簡報 |

講義、報告、信件的重點框和版面都已經寫在範例檔裡（不需要額外套件），直接沿用。
