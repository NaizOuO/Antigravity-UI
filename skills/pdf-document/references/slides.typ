// 簡報範例（touying）：照這個開頭設定寫，內容換成使用者要的。每個 == 標題是一頁。
#import "@preview/touying:0.8.0": *
#import themes.metropolis: *
#import "@preview/physica:0.9.8": *

#show: metropolis-theme.with(
  aspect-ratio: "16-9",
  config-info(title: [Transmon 量子位元], subtitle: [實驗室組會報告], author: [王小明], date: [2026/10/13]),
)
#set text(font: ("Noto Sans TC", "微軟正黑體", "Microsoft JhengHei UI"), lang: "zh", region: "tw")

#title-slide()

== 大綱
+ 為什麼需要 transmon
+ 哈密頓量與能階
+ 量測結果

== 哈密頓量
$ H = 4 E_C (n - n_g)^2 - E_J cos phi $

- $E_J slash E_C >> 1$：對電荷雜訊不敏感
- 非簡諧性 $alpha approx -E_C$

== 量測結果
#table(columns: 3, align: center,
  [樣品], [$T_1$], [$T_2^*$],
  [Q7], [48 μs], [31 μs],
  [Q9], [55 μs], [40 μs],
)

== 結論
- Q9 的 $T_1$ 較長
- 下一步：量測 $ket(1) -> ket(2)$ 的漏失
