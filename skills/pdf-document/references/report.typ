// 報告／實驗報告／正式文件範例：照這個開頭設定寫，內容換成使用者要的。
#import "@preview/zap:0.6.0"
#import "@preview/fletcher:0.5.8" as fletcher: diagram, node, edge

#set page(paper: "a4", margin: (x: 2.5cm, y: 2.5cm), numbering: "1",
  header: context if counter(page).get().first() > 1 [#h(1fr) #text(size: 9pt, fill: gray)[實驗報告 · Transmon 量測]])
#set text(font: ("New Computer Modern", "Noto Serif TC", "微軟正黑體", "Microsoft JhengHei UI"),
          lang: "zh", region: "tw", size: 11pt)
#set par(justify: true, leading: 0.8em, first-line-indent: (amount: 2em, all: true))
#set heading(numbering: "1.1")
#set math.equation(numbering: "(1)")
#set figure(gap: 0.8em)
#show table.cell.where(y: 0): set text(weight: "bold")

// ---------- 封面資訊 ----------
#align(center)[
  #text(size: 18pt, weight: "bold")[超導量子位元 $T_1$ 量測報告] \
  #v(4pt)
  王小明（電物系三年級） · 指導教授：陳教授 · 2026 年 10 月
]
#v(6pt)
#block(inset: 10pt, radius: 4pt, fill: luma(245), width: 100%)[
  *摘要* #h(0.5em) 本報告量測 transmon 量子位元的能量弛豫時間 $T_1$，
  得到 $T_1 = 48 plus.minus 3 thin mu"s"$，並討論 Purcell 效應的影響。
]

= 實驗架構

#figure(
  zap.circuit({
    import zap: *
    vsource("v1", (0, 0), (0, 3), label: "drive")
    capacitor("c1", (0, 3), (3, 3), label: $C_c$)
    inductor("l1", (3, 3), (3, 0), label: $L_r$)
    wire((3, 0), (0, 0))
  }),
  caption: [讀取共振腔的等效電路（示意）。],
)

#figure(
  diagram(spacing: 2.2em, node-stroke: 0.6pt, node-corner-radius: 3pt,
    node((0, 0), [AWG]), edge("-|>"),
    node((1, 0), [IQ 混頻]), edge("-|>"),
    node((2, 0), [稀釋冷凍機]), edge("-|>"),
    node((3, 0), [HEMT 放大]), edge("-|>"),
    node((4, 0), [數位化]),
  ),
  caption: [量測訊號流程。],
)

= 結果

#figure(
  table(columns: 3, align: center, stroke: 0.5pt + gray,
    [樣品], [$T_1$ ($mu"s"$)], [$T_2^*$ ($mu"s"$)],
    [Q7], [48], [31],
    [Q9], [55], [40],
  ),
  caption: [各樣品的相干時間。],
)

能量弛豫遵循 $ P_1(t) = e^(-t slash T_1) $ <eq:decay>
由 @eq:decay 擬合得到上表結果。

= 結論
Q9 的 $T_1$ 較長，推測與較小的 Purcell 損耗有關。

= 參考資料
+ J. Koch et al., Phys. Rev. A 76, 042319 (2007).
+ 實驗室量測紀錄，2026/10/05。
