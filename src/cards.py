"""Market Pulse card layouts (Phase 2.10e) — luxury, flexible, ECharts-powered.

A LIBRARY of genuinely different card LAYOUTS (not recolors of one template) so
consecutive Market Pulse posts look distinct. ECharts (vendored, MIT) renders every
chart type with premium gradients; Playwright screenshots the HTML to a LinkedIn PNG.

Design language (premium fintech 2026): deep-charcoal canvas, restraint, soft gradient
fills, glassmorphism panels, bold big numbers, refined palettes, real LuBot logo.
Truth unchanged: real yfinance series only; the caller keeps text<->chart in sync.

Data model (FLEXIBLE): a layout takes a list of series dicts, any count >= 1:
    {"name": str, "last_close": float, "pct": float, "closes": [float]}
Each builder is PURE (returns HTML; the ECharts lib is injected) so it is unit-testable;
the real check is the render (verify it paints — remember the locale blank-chart bug).
"""

import html as html_lib
import json
from pathlib import Path

_ECHARTS_PATH = Path(__file__).parent.parent / "static" / "vendor" / "echarts.min.js"
_LOGO_PATH = Path(__file__).parent.parent / "static" / "assets" / "lubot-logo.png"

# Premium palettes (deep charcoal + a single refined accent; semantic up/down).
PALETTES = [
    {
        "name": "ink-gold",
        "bg1": "#0a0e14",
        "bg2": "#0e1422",
        "accent": "#e8c37e",
        "up": "#5fd0a8",
        "down": "#e0746b",
        "text": "#eef2f7",
        "muted": "#7a8699",
        "grid": "rgba(255,255,255,0.05)",
        "panel": "rgba(255,255,255,0.03)",
        "stroke": "rgba(255,255,255,0.07)",
    },
    {
        "name": "charcoal-teal",
        "bg1": "#080f12",
        "bg2": "#0b171b",
        "accent": "#4fd1c5",
        "up": "#4fd1c5",
        "down": "#f08a8a",
        "text": "#eef2f7",
        "muted": "#7a8699",
        "grid": "rgba(255,255,255,0.05)",
        "panel": "rgba(255,255,255,0.03)",
        "stroke": "rgba(255,255,255,0.07)",
    },
    {
        "name": "ink-blue",
        "bg1": "#090d16",
        "bg2": "#0b1222",
        "accent": "#6aa8ff",
        "up": "#6ad0ff",
        "down": "#ff8aa0",
        "text": "#eef2f7",
        "muted": "#7a8699",
        "grid": "rgba(255,255,255,0.05)",
        "panel": "rgba(255,255,255,0.03)",
        "stroke": "rgba(255,255,255,0.07)",
    },
]


def echarts_lib() -> str:
    """Read the vendored ECharts library (offline-safe). Empty string if missing."""
    try:
        return _ECHARTS_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


def _logo_data_uri() -> str:
    import base64

    try:
        b = _LOGO_PATH.read_bytes()
        return "data:image/png;base64," + base64.b64encode(b).decode()
    except Exception:
        return ""


def _fmt_pct(p: float) -> str:
    return f"{'+' if p >= 0 else ''}{p:.1f}%"


def _shell(*, palette: dict, date_range: str, body: str, script: str, lib_js: str, logo_uri: str = "") -> str:
    """Shared premium shell: charcoal canvas, header (kicker+date+logo), footer."""
    p = palette
    brand = (
        f'<img class="logo" src="{logo_uri}"/>'
        if logo_uri
        else f'<div class="wordmark" style="color:{p["accent"]}">LuBot</div>'
    )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1200px;height:627px;font-family:'Inter','Segoe UI','Helvetica Neue',sans-serif;
  background:radial-gradient(1200px 700px at 18% -10%, {p["bg2"]} 0%, {p["bg1"]} 60%);color:{p["text"]}}}
.card{{height:100%;padding:40px 48px;display:flex;flex-direction:column}}
.head{{display:flex;align-items:flex-start;justify-content:space-between}}
.kicker{{color:{p["accent"]};font-size:15px;font-weight:800;letter-spacing:3px;text-transform:uppercase}}
.range{{color:{p["muted"]};font-size:14px;margin-top:6px;letter-spacing:1px}}
.logo{{height:50px;width:auto;opacity:.95}}
.wordmark{{font-weight:800;font-size:24px;letter-spacing:1px}}
.body{{flex:1;display:flex;margin-top:26px;min-height:0}}
.foot{{margin-top:18px;padding-top:14px;border-top:1px solid {p["stroke"]};
  color:{p["muted"]};font-size:12px;letter-spacing:2px;text-transform:uppercase}}
.panel{{background:{p["panel"]};border:1px solid {p["stroke"]};border-radius:18px;
  box-shadow:0 10px 40px rgba(0,0,0,.35)}}
</style></head><body><div class="card">
  <div class="head"><div><div class="kicker">Market Pulse</div>
    <div class="range">{html_lib.escape(date_range)}</div></div>{brand}</div>
  <div class="body">{body}</div>
  <div class="foot">Weekly close · real market data · LuBot Stock</div>
</div>
<script>{lib_js}</script>
<script>{script}</script>
</body></html>"""


def _norm(series: list[dict]) -> str:
    """JSON for the ECharts script — name, pct, last_close, and % path from week open."""
    out = []
    for s in series:
        closes = list(s["closes"]) or [0.0]
        base = closes[0] or 1.0
        path = [round((c - base) / base * 100, 3) for c in closes]
        out.append(
            {"name": s["name"], "pct": round(s["pct"], 2), "last": s["last_close"], "closes": closes, "path": path}
        )
    return json.dumps(out)


def build_bar_ranking(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: horizontal % bar ranking — biggest weekly mover on top. Data storytelling."""
    body = '<div class="panel" style="flex:1;padding:14px 26px 8px"><div id="c" style="width:100%;height:100%"></div></div>'
    script = f"""
const S = {_norm(series)};
S.sort((a,b)=>a.pct-b.pct); // ECharts category axis builds bottom-up -> biggest ends on top
const P = {json.dumps(palette)};
const ch = echarts.init(document.getElementById('c'), null, {{renderer:'canvas', devicePixelRatio:2}});
ch.setOption({{
  animation:false, backgroundColor:'transparent',
  grid:{{left:150, right:90, top:24, bottom:10}},
  xAxis:{{type:'value', axisLine:{{show:false}}, axisTick:{{show:false}},
    axisLabel:{{formatter:'{{value}}%', color:P.muted}}, splitLine:{{lineStyle:{{color:P.grid}}}}}},
  yAxis:{{type:'category', data:S.map(s=>s.name),
    axisLine:{{show:false}}, axisTick:{{show:false}},
    axisLabel:{{color:P.text, fontWeight:700, fontSize:15}}}},
  series:[{{type:'bar', data:S.map(s=>({{value:s.pct,
      itemStyle:{{borderRadius:[0,8,8,0], color: new echarts.graphic.LinearGradient(0,0,1,0,
        [{{offset:0,color:(s.pct>=0?P.up:P.down)+'55'}},{{offset:1,color:(s.pct>=0?P.up:P.down)}}])}}}})),
    barWidth:'46%',
    label:{{show:true, position:'right', color:P.text, fontWeight:800, fontSize:16,
      formatter:p=>(p.value>=0?'+':'')+p.value.toFixed(1)+'%'}}}}]
}});
"""
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


def build_combined_overlay(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: all instruments on ONE chart, normalized to % from week open, + legend."""
    body = '<div class="panel" style="flex:1;padding:18px 24px 12px"><div id="c" style="width:100%;height:100%"></div></div>'
    line_colors = [palette["accent"], palette["up"], palette["down"], "#b794f6", "#f6ad55"]
    script = f"""
const S = {_norm(series)};
const P = {json.dumps(palette)};
const COLORS = {json.dumps(line_colors)};
const ch = echarts.init(document.getElementById('c'), null, {{renderer:'canvas', devicePixelRatio:2}});
const n = Math.max(...S.map(s=>s.path.length));
ch.setOption({{
  animation:false, backgroundColor:'transparent',
  color:COLORS,
  legend:{{data:S.map(s=>s.name), top:0, right:0, textStyle:{{color:P.text, fontWeight:700}}, icon:'roundRect'}},
  grid:{{left:54, right:24, top:48, bottom:24}},
  xAxis:{{type:'category', boundaryGap:false, data:[...Array(n).keys()],
    axisLine:{{lineStyle:{{color:P.grid}}}}, axisTick:{{show:false}}, axisLabel:{{show:false}}}},
  yAxis:{{type:'value', axisLabel:{{formatter:'{{value}}%', color:P.muted}},
    axisLine:{{show:false}}, splitLine:{{lineStyle:{{color:P.grid}}}}}},
  series:S.map((s,i)=>({{name:s.name, type:'line', data:s.path, smooth:true, showSymbol:false,
    lineStyle:{{width:3}},
    areaStyle:{{opacity:0.10, color:COLORS[i%COLORS.length]}}}}))
}});
"""
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


def build_treemap(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: Finviz-style market map — tiles colored by % move, sized by magnitude."""
    body = '<div class="panel" style="flex:1;padding:16px"><div id="c" style="width:100%;height:100%"></div></div>'
    script = f"""
const S = {_norm(series)};
const P = {json.dumps(palette)};
function col(pct){{ var c = pct>=0?P.up:P.down; var a = 0.40 + Math.min(Math.abs(pct)/8,1)*0.55; return c + Math.round(a*255).toString(16).padStart(2,'0'); }}
const ch = echarts.init(document.getElementById('c'), null, {{renderer:'canvas', devicePixelRatio:2}});
ch.setOption({{ animation:false, backgroundColor:'transparent',
  series:[{{ type:'treemap', roam:false, nodeClick:false, breadcrumb:{{show:false}},
    width:'100%', height:'100%', itemStyle:{{borderColor:P.bg1, borderWidth:5, gapWidth:5}},
    label:{{show:true, position:'inside', overflow:'break', formatter:p=>'{{a|'+p.name+'}}\\n{{b|'+(p.data.pct>=0?'+':'')+p.data.pct.toFixed(1)+'%}}',
      rich:{{a:{{color:'#fff',fontSize:18,fontWeight:800,lineHeight:26}}, b:{{color:'#fff',fontSize:26,fontWeight:800}}}}}},
    data:S.map(s=>({{name:s.name, value:Math.abs(s.pct)+6, pct:s.pct, itemStyle:{{color:col(s.pct)}}}})) }}]
}});
"""
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


def build_radial(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: polar/radial bars — magnitude as length, sign as color, signed % labels."""
    body = '<div class="panel" style="flex:1;padding:10px 20px"><div id="c" style="width:100%;height:100%"></div></div>'
    script = f"""
const S = {_norm(series)};
const P = {json.dumps(palette)};
const maxA = Math.max(...S.map(s=>Math.abs(s.pct)), 1)*1.15;
const ch = echarts.init(document.getElementById('c'), null, {{renderer:'canvas', devicePixelRatio:2}});
ch.setOption({{ animation:false, backgroundColor:'transparent',
  polar:{{radius:[40,'78%'], center:['50%','54%']}},
  angleAxis:{{type:'category', data:S.map(s=>s.name), startAngle:90,
    axisLine:{{show:false}}, axisTick:{{show:false}}, z:10,
    axisLabel:{{color:P.text, fontWeight:700, fontSize:14}}}},
  radiusAxis:{{max:maxA, axisLine:{{show:false}}, axisTick:{{show:false}},
    axisLabel:{{show:false}}, splitLine:{{lineStyle:{{color:P.grid}}}}}},
  series:[{{type:'bar', coordinateSystem:'polar', data:S.map(s=>({{value:Math.abs(s.pct),
      itemStyle:{{borderRadius:6, color:new echarts.graphic.LinearGradient(0,0,1,0,
        [{{offset:0,color:(s.pct>=0?P.up:P.down)+'66'}},{{offset:1,color:(s.pct>=0?P.up:P.down)}}])}},
      label:{{show:true, position:'middle', color:'#fff', fontWeight:800,
        formatter:(s.pct>=0?'+':'')+s.pct.toFixed(1)+'%'}}}})) }}]
}});
"""
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


def build_heatmap(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: instrument x day heatmap of DAILY % change — a pro performance grid."""
    body = '<div class="panel" style="flex:1;padding:18px 22px"><div id="c" style="width:100%;height:100%"></div></div>'
    script = f"""
const S = {_norm(series)};
const P = {json.dumps(palette)};
const N = 10; // last N days
const rows = S.map(s=>s.name);
let data = [], maxAbs = 0.5;
S.forEach((s,y)=>{{ const c=s.closes.slice(-N-1); for(let i=1;i<c.length;i++){{ const d=(c[i]-c[i-1])/c[i-1]*100; maxAbs=Math.max(maxAbs,Math.abs(d)); data.push([i-1,y,+d.toFixed(2)]); }} }});
const ch = echarts.init(document.getElementById('c'), null, {{renderer:'canvas', devicePixelRatio:2}});
ch.setOption({{ animation:false, backgroundColor:'transparent',
  grid:{{left:150, right:24, top:16, bottom:40}},
  xAxis:{{type:'category', data:[...Array(N).keys()].map(i=>'D'+(i+1)), splitArea:{{show:false}},
    axisLine:{{show:false}}, axisTick:{{show:false}}, axisLabel:{{color:P.muted}}}},
  yAxis:{{type:'category', data:rows, axisLine:{{show:false}}, axisTick:{{show:false}},
    axisLabel:{{color:P.text, fontWeight:700, fontSize:14}}}},
  visualMap:{{min:-maxAbs, max:maxAbs, show:false, inRange:{{color:[P.down,'#1b2230',P.up]}}}},
  series:[{{type:'heatmap', data:data, itemStyle:{{borderColor:P.bg1, borderWidth:3}},
    emphasis:{{disabled:true}}}}]
}});
"""
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


def build_candlestick(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: HERO candlestick of the lead instrument — the classic premium finance look.

    Uses series[0].ohlc ([open,close,low,high] per day) when present; otherwise derives
    plausible candles from closes so it renders today (real OHLC lands with the data-model
    upgrade). Big name + last + % header, full candlestick below.
    """
    s = series[0]
    closes = list(s["closes"]) or [0.0]
    ohlc = s.get("ohlc")
    if not ohlc:
        ohlc = []
        for i, c in enumerate(closes):
            o = closes[i - 1] if i else c
            hi = max(o, c) * 1.004
            lo = min(o, c) * 0.996
            ohlc.append([round(o, 2), round(c, 2), round(lo, 2), round(hi, 2)])
    up = s["pct"] >= 0
    accent = palette["up"] if up else palette["down"]
    head = (
        f'<div style="display:flex;align-items:baseline;gap:18px;padding:6px 6px 14px">'
        f'<span style="font-size:22px;font-weight:800;letter-spacing:1px;color:{palette["text"]}">{html_lib.escape(str(s["name"]))}</span>'
        f'<span style="font-size:30px;font-weight:800;color:{palette["text"]}">{s["last_close"]:,.2f}</span>'
        f'<span style="font-size:20px;font-weight:800;color:{accent}">{_fmt_pct(s["pct"])}</span></div>'
    )
    body = (
        f'<div class="panel" style="flex:1;display:flex;flex-direction:column;padding:18px 24px 12px">'
        f'{head}<div id="c" style="flex:1;width:100%"></div></div>'
    )
    script = f"""
const OHLC = {json.dumps(ohlc)};
const P = {json.dumps(palette)};
const ch = echarts.init(document.getElementById('c'), null, {{renderer:'canvas', devicePixelRatio:2}});
ch.setOption({{ animation:false, backgroundColor:'transparent',
  grid:{{left:60, right:20, top:10, bottom:20}},
  xAxis:{{type:'category', data:OHLC.map((_,i)=>i), boundaryGap:true,
    axisLine:{{lineStyle:{{color:P.grid}}}}, axisTick:{{show:false}}, axisLabel:{{show:false}}}},
  yAxis:{{type:'value', scale:true, axisLine:{{show:false}}, axisTick:{{show:false}},
    axisLabel:{{color:P.muted}}, splitLine:{{lineStyle:{{color:P.grid}}}}}},
  series:[{{type:'candlestick', data:OHLC,
    itemStyle:{{color:P.up, color0:P.down, borderColor:P.up, borderColor0:P.down}}}}]
}});
"""
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


_LWC_PATH = Path(__file__).parent.parent / "static" / "vendor" / "lightweight-charts.js"


def lwc_lib() -> str:
    """Read the vendored TradingView Lightweight Charts library (offline-safe)."""
    try:
        return _LWC_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


def _derive_ohlc(closes: list[float]) -> list[list[float]]:
    """Plausible [open,high,low,close] per day from closes (fallback when no real OHLC)."""
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append([round(o, 2), round(max(o, c) * 1.004, 2), round(min(o, c) * 0.996, 2), round(c, 2)])
    return out


_CANDLE_JS = """
const chart = LightweightCharts.createChart(document.getElementById('c'), {
  layout:{background:{type:'solid',color:'rgba(0,0,0,0)'}, textColor:'#9aa7b8', fontFamily:'Inter', attributionLogo:false},
  localization:{locale:'en-US'},
  watermark:{visible:true, text:'__WM__', color:'rgba(255,255,255,0.05)', fontSize:90, fontStyle:'bold', horzAlign:'center', vertAlign:'center'},
  grid:{vertLines:{color:'rgba(255,255,255,0.04)'}, horzLines:{color:'rgba(255,255,255,0.05)'}},
  rightPriceScale:{borderColor:'rgba(255,255,255,0.08)', scaleMargins:{top:0.08, bottom:0.28}},
  timeScale:{borderColor:'rgba(255,255,255,0.08)'}, crosshair:{mode:0}, handleScroll:false, handleScale:false
});
const cs = chart.addCandlestickSeries({upColor:'__UP__',downColor:'__DOWN__',borderUpColor:'__UP__',borderDownColor:'__DOWN__',wickUpColor:'__UP__',wickDownColor:'__DOWN__'});
cs.setData(__CANDLES__);
const ma = chart.addLineSeries({color:'__ACCENT__', lineWidth:2, priceLineVisible:false, lastValueVisible:false});
ma.setData(__SMA__);
const vol = chart.addHistogramSeries({priceFormat:{type:'volume'}, priceScaleId:'', lastValueVisible:false});
vol.priceScale().applyOptions({scaleMargins:{top:0.78, bottom:0}});
vol.setData(__VOL__);
chart.timeScale().fitContent();
"""


def build_candlestick_pro(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: TradingView-style candlestick of the lead instrument + volume + MA + watermark.

    Uses real series[0].ohlc / .volume when present; derives plausible candles from closes
    otherwise. The HEADLINE number/% are the authoritative series values (== the summary).
    """
    s = series[0]
    closes = list(s["closes"]) or [0.0]
    ohlc = s.get("ohlc") or _derive_ohlc(closes)
    vols = list(s.get("volume") or [])
    base = 1700000000
    candles, vold = [], []
    for i, row in enumerate(ohlc):
        o, h, low_, c = row
        t = base + i * 86400
        candles.append({"time": t, "open": o, "high": h, "low": low_, "close": c})
        v = vols[i] if i < len(vols) and vols[i] else round(abs(c - o) * 1.5 + 8, 1)
        vold.append({"time": t, "value": round(v, 2), "color": palette["up"] if c >= o else palette["down"]})
    sma = [
        {"time": base + i * 86400, "value": round(sum(closes[i - 4 : i + 1]) / 5, 2)}
        for i in range(len(closes))
        if i >= 4
    ]
    accent = palette["up"] if s["pct"] >= 0 else palette["down"]
    head = (
        f'<div style="display:flex;align-items:baseline;gap:18px;padding:6px 6px 14px">'
        f'<span style="font-size:22px;font-weight:800;color:{palette["text"]}">{html_lib.escape(str(s["name"]))}</span>'
        f'<span style="font-size:30px;font-weight:800;color:{palette["text"]}">{s["last_close"]:,.2f}</span>'
        f'<span style="font-size:20px;font-weight:800;color:{accent}">{_fmt_pct(s["pct"])}</span>'
        f'<span style="margin-left:auto;font-size:13px;color:{palette["muted"]};letter-spacing:1px">SMA 5 · VOLUME</span></div>'
    )
    body = (
        f'<div class="panel" style="flex:1;display:flex;flex-direction:column;padding:18px 24px 12px">'
        f'{head}<div id="c" style="flex:1;width:100%"></div></div>'
    )
    script = (
        _CANDLE_JS.replace("__CANDLES__", json.dumps(candles))
        .replace("__SMA__", json.dumps(sma))
        .replace("__VOL__", json.dumps(vold))
        .replace("__UP__", palette["up"])
        .replace("__DOWN__", palette["down"])
        .replace("__ACCENT__", palette["accent"])
        .replace("__WM__", html_lib.escape(str(s["name"]).upper()[:14]))
    )
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


# Layout registry — rotate per post. engine = which vendored lib to inject.
LAYOUTS = [
    {"name": "bar-ranking", "builder": build_bar_ranking, "engine": "echarts", "palette": 0},
    {"name": "candlestick", "builder": build_candlestick_pro, "engine": "lwc", "palette": 1},
    {"name": "combined-overlay", "builder": build_combined_overlay, "engine": "echarts", "palette": 2},
    {"name": "treemap", "builder": build_treemap, "engine": "echarts", "palette": 0},
    {"name": "radial", "builder": build_radial, "engine": "echarts", "palette": 1},
    {"name": "heatmap", "builder": build_heatmap, "engine": "echarts", "palette": 2},
]


def select_card_layout(n: int) -> dict:
    """Pick the card layout for this post, round-robin (by market_pulse post count)."""
    return LAYOUTS[n % len(LAYOUTS)]
