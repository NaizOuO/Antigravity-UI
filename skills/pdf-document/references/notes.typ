// 講義／筆記範例：照這個開頭設定寫，內容換成使用者要的。
#import "@preview/physica:0.9.8": *
#import "@preview/cetz:0.5.2"

// ---------- 版面與字型（中文優先思源宋體 Noto Serif TC，沒有就用 Windows 內建的微軟正黑體） ----------
#set page(paper: "a4", margin: (x: 2.2cm, y: 2.4cm), numbering: "1 / 1")
#set text(font: ("New Computer Modern", "Noto Serif TC", "微軟正黑體", "Microsoft JhengHei UI"),
          lang: "zh", region: "tw", size: 11pt)
#set par(justify: true, leading: 0.8em)
#set heading(numbering: "1.1")
#set math.equation(numbering: "(1)")
#show heading.where(level: 1): it => block(above: 1.4em, below: 0.8em, text(fill: rgb("#2f4f7f"), it))

// ---------- 重點框（定義、定理、例題、注意） ----------
#let callout(title, color, body) = block(
  width: 100%, inset: (x: 12pt, y: 10pt), radius: 6pt, above: 1em, below: 1em,
  fill: color.lighten(88%), stroke: (left: 3pt + color),
  [#text(weight: "bold", fill: color, title) \ #body],
)
#let definition(title: "定義", body) = callout(title, rgb("#2f6fde"), body)
#let theorem(title: "定理", body) = callout(title, rgb("#7a4fd6"), body)
#let example(title: "例題", body) = callout(title, rgb("#2e8b57"), body)
#let note(title: "重點", body) = callout(title, rgb("#c27c0e"), body)
#let warning(title: "注意", body) = callout(title, rgb("#c43c3c"), body)

// ---------- 標題 ----------
#align(center)[
  #text(size: 20pt, weight: "bold")[Transmon 量子位元入門]
  #v(2pt)
  #text(fill: gray)[量子力學導論 · 課堂筆記]
]

#note(title: "重點摘要")[
  - Transmon 是在 Cooper-pair box 並聯大電容，讓 $E_J slash E_C >> 1$。
  - 能階間距不相等（非簡諧），才能只操作最低的兩個能階。
  - 非簡諧性約為 $alpha approx -E_C$。
]

= 哈密頓量

#definition[
  Transmon 的哈密頓量為
  $ H = 4 E_C (n - n_g)^2 - E_J cos phi $ <eq:hamiltonian>
  - $E_C$：充電能（單位 GHz）
  - $E_J$：約瑟夫森能
  - $n$：穿越接面的 Cooper 對數，$phi$：超導相位差
]

由 @eq:hamiltonian 可知，當 $E_J >> E_C$ 時，相位 $phi$ 被侷限在位能井底部。

= 能階與非簡諧性

#theorem(title: "非簡諧性")[
  相鄰躍遷頻率的差為 $ alpha = omega_(12) - omega_(01) approx -E_C / hbar $ <eq:alpha>
]

#figure(
  cetz.canvas({
    import cetz.draw: *
    let levels = (0, 1.0, 1.8, 2.45)
    for (i, e) in levels.enumerate() {
      line((0, e), (3, e), stroke: 1.2pt)
      content((3.4, e), $ket(#i)$)
    }
    line((1.5, 0), (1.5, 1.0), mark: (end: ">"), stroke: blue)
    content((0.9, 0.5), text(fill: blue)[$omega_(01)$])
  }),
  caption: [Transmon 最低的四個能階：間距越往上越窄。],
)

#example[
  若 $E_C slash h = 250 "MHz"$，則 $alpha slash 2 pi approx -250 "MHz"$。
]

#warning[
  非簡諧性太小時，驅動 $ket(0) -> ket(1)$ 的脈衝會漏到 $ket(2)$（leakage）。
]

#note(title: "本節重點")[
  非簡諧性讓 transmon 可以被當成二能階系統（qubit）。
]

= 參考資料
- 量導1005.pdf，第 3 頁
- J. Koch et al., Phys. Rev. A 76, 042319 (2007)
