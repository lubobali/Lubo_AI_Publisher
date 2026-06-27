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

import base64
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
    try:
        b = _LOGO_PATH.read_bytes()
        return "data:image/png;base64," + base64.b64encode(b).decode()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Design system (Phase 2.16 E) — brand palette + embedded fonts + texture.
# The universal frame is built on these. Everything deterministic: fonts embedded
# as base64, grain a FIXED-seed SVG -> a given post always renders identical pixels.
# ---------------------------------------------------------------------------

_FONTS_DIR = Path(__file__).parent.parent / "static" / "fonts"

# (css family, file, weight, style) — exactly what the locked design uses, nothing more.
_FONT_FACES = (
    ("Fraunces", "fraunces-400.woff2", 400, "normal"),  # serif headline (the "art")
    ("Grotesk", "spacegrotesk-500.woff2", 500, "normal"),  # body / secondary
    ("Grotesk", "spacegrotesk-700.woff2", 700, "normal"),  # kicker / signature / numbers
)

# Brand palette — matches the LuBot logo (blue 3D mark + steel key). Replaces the old gold.
BRAND = {
    "blue": "#4f8cf0",
    "blue_dk": "#1f4fae",
    "steel": "#c3c9d4",
    "accent": "linear-gradient(105deg,#1f4fae 0%,#4f8cf0 40%,#c3c9d4 70%,#4f8cf0 100%)",
    "bg": "radial-gradient(1300px 820px at 12% -20%,#101826 0%,#0a0e15 58%),#0a0e15",
    "text": "#eef2f8",
    "headline": "#f4f7fc",
    "footer": "#aab6cc",
    "hairline": "rgba(120,160,230,0.20)",
}

# Deterministic film grain (fixed seed -> identical every render). The SVG uses single
# quotes, so the url() is wrapped in &quot; to coexist with the double-quoted style attr.
_GRAIN_SVG = (
    "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='600' height='314'>"
    "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' "
    "seed='11' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter>"
    "<rect width='100%25' height='100%25' filter='url(%23n)'/></svg>"
)

_FONT_CSS_CACHE: str | None = None


def _font_css() -> str:
    """@font-face blocks with the fonts embedded as base64 (offline + deterministic).

    Cached after first read. Returns "" if the font files are missing (graceful — the
    card still renders in the system fallback rather than crashing).
    """
    global _FONT_CSS_CACHE
    if _FONT_CSS_CACHE is not None:
        return _FONT_CSS_CACHE
    parts = []
    for family, filename, weight, style in _FONT_FACES:
        try:
            b64 = base64.b64encode((_FONTS_DIR / filename).read_bytes()).decode()
        except Exception:
            continue
        parts.append(
            f"@font-face{{font-family:'{family}';font-weight:{weight};font-style:{style};"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2');font-display:block}}"
        )
    _FONT_CSS_CACHE = "".join(parts)
    return _FONT_CSS_CACHE


def _grain(opacity: float = 0.06) -> str:
    """A subtle film-grain overlay div (deterministic) — kills the flat-digital look."""
    return (
        f'<div style="position:absolute;inset:0;background:url(&quot;{_GRAIN_SVG}&quot;);'
        f'background-size:600px;opacity:{opacity};mix-blend-mode:overlay;pointer-events:none"></div>'
    )


def _vignette() -> str:
    """An inset vignette overlay div — depth, like a painting under gallery light."""
    return (
        '<div style="position:absolute;inset:0;box-shadow:inset 0 0 240px 60px '
        'rgba(0,0,0,.5);pointer-events:none"></div>'
    )


def _signature(brand: dict = BRAND) -> str:
    """The constant maker's mark: a small blue dash, then 'Lubo Bali' as a tech wordmark.
    Identical on every card (matches the logo's style — no handwriting)."""
    blue = brand["blue"]
    dash = (
        f"<svg width='46' height='12' style='vertical-align:middle'>"
        f"<line x1='2' y1='6' x2='44' y2='6' stroke='{blue}' stroke-width='2'/></svg>"
    )
    return (
        "<span style='display:inline-flex;align-items:center;gap:14px'>"
        f"{dash}<span style='font-family:Grotesk;font-weight:700;font-size:30px;"
        f"letter-spacing:.5px;color:{blue}'>Lubo Bali</span></span>"
    )


def _frame(
    *,
    kicker: str,
    body: str,
    disclaimer: str,
    folio: str = "",
    logo_uri: str = "",
    lib_js: str = "",
    script: str = "",
    signature: bool = True,
    brand: dict = BRAND,
) -> str:
    """The UNIVERSAL card frame (Phase 2.16 E2) — constant chrome on EVERY card:
    background + grain + vignette, an accent rail, the topic KICKER + LOGO, a hairline,
    a centered BODY slot (the topic-specific interior), the "— Lubo Bali" SIGNATURE, and a
    footer (FOLIO | disclaimer + accent dot). Flex column so any body size stays centered and
    the signature + footer always anchor at the bottom. Deterministic. The signature is on
    every card by design (it's Lubo's vision/voice; the tools just express it). `body` is
    whatever interior a builder composes; lib_js/script let chart interiors inject engine+setup.
    """
    b = brand
    logo = (
        f'<img src="{logo_uri}" style="height:70px;opacity:.97"/>'
        if logo_uri
        else f'<div style="font-family:Grotesk;font-weight:800;font-size:26px;color:{b["blue"]}">LuBot</div>'
    )
    sig = f'<div style="position:relative;margin-bottom:20px">{_signature(b)}</div>' if signature else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{_font_css()}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1200px;height:627px;font-family:'Grotesk','Inter','Segoe UI',sans-serif;background:{b["bg"]};color:{b["text"]}}}
</style></head><body><div style="position:relative;width:1200px;height:627px;display:flex;flex-direction:column;padding:52px 64px 40px 70px">
{_grain()}{_vignette()}
<div style="position:absolute;left:0;top:64px;bottom:64px;width:3px;background:{b["accent"]}"></div>
<div style="position:relative;display:flex;justify-content:space-between;align-items:flex-start">
  <div style="font-family:Grotesk;font-weight:700;font-size:17px;letter-spacing:4px;text-transform:uppercase;background:{b["accent"]};-webkit-background-clip:text;background-clip:text;color:transparent">{html_lib.escape(kicker)}</div>
  {logo}
</div>
<div style="position:relative;width:60px;height:2px;background:{b["accent"]};margin:24px 0 22px 0"></div>
<div style="position:relative;flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;max-width:1010px">{body}</div>
{sig}
<div style="position:relative;display:flex;justify-content:space-between;align-items:center;
  border-top:1px solid {b["hairline"]};padding-top:14px;font-family:Grotesk;font-size:16px;letter-spacing:1.5px;text-transform:uppercase;color:{b["footer"]}">
  <span>{html_lib.escape(folio)}</span>
  <span style="display:flex;align-items:center;gap:9px"><span style="width:7px;height:7px;border-radius:50%;background:{b["blue"]};box-shadow:0 0 12px {b["blue"]}"></span>{html_lib.escape(disclaimer)}</span>
</div>
</div><script>{lib_js}</script><script>{script}</script></body></html>"""


def _fmt_pct(p: float) -> str:
    return f"{'+' if p >= 0 else ''}{p:.1f}%"


def _shell(
    *,
    palette: dict,
    date_range: str,
    body: str,
    script: str,
    lib_js: str,
    logo_uri: str = "",
    kicker: str = "Market Pulse",
    foot: str = "Weekly close · real market data · LuBot Stock",
) -> str:
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
  <div class="head"><div><div class="kicker">{html_lib.escape(kicker)}</div>
    <div class="range">{html_lib.escape(date_range)}</div></div>{brand}</div>
  <div class="body">{body}</div>
  <div class="foot">{html_lib.escape(foot)}</div>
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
  timeScale:__TIMESCALE__, crosshair:{mode:0}, handleScroll:false, handleScale:false
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
    # Real per-day dates when available (Batch B) -> real time axis; else numeric + hidden axis.
    dates = list(s.get("dates") or [])
    real_dates = len(dates) == len(ohlc) and len(dates) > 0
    base = 1700000000

    def _t(i: int):
        return dates[i] if real_dates else base + i * 86400

    candles, vold = [], []
    for i, row in enumerate(ohlc):
        o, h, low_, c = row
        candles.append({"time": _t(i), "open": o, "high": h, "low": low_, "close": c})
        v = vols[i] if i < len(vols) and vols[i] else round(abs(c - o) * 1.5 + 8, 1)
        vold.append({"time": _t(i), "value": round(v, 2), "color": palette["up"] if c >= o else palette["down"]})
    # SMA aligned to the OHLC timeline (use ohlc closes so times line up with the candles)
    oc = [row[3] for row in ohlc]
    sma = [{"time": _t(i), "value": round(sum(oc[i - 4 : i + 1]) / 5, 2)} for i in range(len(oc)) if i >= 4]
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
        .replace(
            "__TIMESCALE__",
            "{borderColor:'rgba(255,255,255,0.08)', timeVisible:false}" if real_dates else "{visible:false}",
        )
    )
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


def _heading(name: str, pct: float, palette: dict) -> str:
    """Small in-panel heading: instrument name + signed % (matches summary formatting)."""
    col = palette["up"] if pct >= 0 else palette["down"]
    return (
        f'<div style="display:flex;align-items:baseline;gap:16px;padding:4px 4px 12px">'
        f'<span style="font-size:20px;font-weight:800;color:{palette["text"]}">{html_lib.escape(str(name))}</span>'
        f'<span style="font-size:19px;font-weight:800;color:{col}">{_fmt_pct(pct)}</span></div>'
    )


def build_waterfall(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: lead instrument's daily % steps building to the weekly total (ECharts)."""
    s = series[0]
    # Weekly window (last 5 closes) so the cumulative endpoint == the stated weekly % —
    # a waterfall makes the running total explicit, so it must match the header number.
    closes = (list(s["closes"]) or [0.0])[-5:]
    b = closes[0] or 1.0
    path = [(c - b) / b * 100 for c in closes]
    ph, bars, cols = [], [], []
    prev = 0.0
    for cur in path:
        lo, hi = min(prev, cur), max(prev, cur)
        ph.append(round(lo, 3))
        bars.append(round(hi - lo, 3))
        cols.append(palette["up"] if cur >= prev else palette["down"])
        prev = cur
    body = (
        f'<div class="panel" style="flex:1;display:flex;flex-direction:column;padding:18px 24px 12px">'
        f'{_heading(s["name"], s["pct"], palette)}<div id="c" style="flex:1;width:100%"></div></div>'
    )
    script = (
        (
            """
const P=__P__, ph=__PH__, bars=__BARS__, cols=__COLS__;
const ch=echarts.init(document.getElementById('c'),null,{renderer:'canvas',devicePixelRatio:2});
ch.setOption({animation:false,backgroundColor:'transparent',
 grid:{left:56,right:20,top:14,bottom:18},
 xAxis:{type:'category',data:bars.map((_,i)=>i),axisLine:{lineStyle:{color:P.grid}},axisTick:{show:false},axisLabel:{show:false}},
 yAxis:{type:'value',axisLabel:{formatter:'{value}%',color:P.muted},axisLine:{show:false},splitLine:{lineStyle:{color:P.grid}}},
 series:[
  {type:'bar',stack:'t',data:ph,itemStyle:{color:'transparent'},emphasis:{disabled:true}},
  {type:'bar',stack:'t',barWidth:'58%',data:bars.map((v,i)=>({value:v,itemStyle:{color:cols[i],borderRadius:3}}))}
 ]});
"""
        )
        .replace("__P__", json.dumps(palette))
        .replace("__PH__", json.dumps(ph))
        .replace("__BARS__", json.dumps(bars))
        .replace("__COLS__", json.dumps(cols))
    )
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


def build_slope(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: slope chart — each instrument from week open (0%) to its close (% move)."""
    body = '<div class="panel" style="flex:1;padding:20px 28px 14px"><div id="c" style="width:100%;height:100%"></div></div>'
    script = (
        (
            """
const S=__S__, P=__P__;
const COLORS=[P.accent,P.up,P.down,'#b794f6','#f6ad55'];
const ch=echarts.init(document.getElementById('c'),null,{renderer:'canvas',devicePixelRatio:2});
ch.setOption({animation:false,backgroundColor:'transparent',color:COLORS,
 grid:{left:30,right:150,top:24,bottom:30},
 xAxis:{type:'category',data:['Week open','Now'],boundaryGap:false,axisLine:{lineStyle:{color:P.grid}},axisTick:{show:false},axisLabel:{color:P.muted,fontWeight:700}},
 yAxis:{type:'value',axisLabel:{formatter:'{value}%',color:P.muted},axisLine:{show:false},splitLine:{lineStyle:{color:P.grid}}},
 series:S.map((s,i)=>({name:s.name,type:'line',data:[0,s.pct],symbolSize:11,lineStyle:{width:3,color:(s.pct>=0?P.up:P.down)},
   itemStyle:{color:(s.pct>=0?P.up:P.down)},
   endLabel:{show:true,color:P.text,fontWeight:800,formatter:s.name+'  '+(s.pct>=0?'+':'')+s.pct.toFixed(1)+'%'}}))
});
"""
        )
        .replace("__S__", _norm(series))
        .replace("__P__", json.dumps(palette))
    )
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


def build_scoreboard(series: list[dict], date_range: str, palette: dict, lib_js: str = "", logo_uri: str = "") -> str:
    """Layout: number-forward scoreboard — big % deltas + relative bars (pure HTML, no lib)."""
    ranked = sorted(series, key=lambda s: s["pct"], reverse=True)
    maxabs = max((abs(s["pct"]) for s in series), default=1) or 1
    rows = ""
    for s in ranked:
        col = palette["up"] if s["pct"] >= 0 else palette["down"]
        w = max(4, int(abs(s["pct"]) / maxabs * 100))
        rows += (
            f'<div style="display:flex;align-items:center;gap:26px;padding:20px 6px;border-bottom:1px solid {palette["stroke"]}">'
            f'<div style="flex:0 0 250px;font-size:22px;font-weight:800;color:{palette["text"]}">{html_lib.escape(str(s["name"]))}</div>'
            f'<div style="flex:0 0 150px;font-size:34px;font-weight:800;color:{col}">{_fmt_pct(s["pct"])}</div>'
            f'<div style="flex:0 0 150px;font-size:20px;color:{palette["muted"]}">{s["last_close"]:,.2f}</div>'
            f'<div style="flex:1;height:16px;background:rgba(255,255,255,0.05);border-radius:8px;overflow:hidden">'
            f'<div style="height:100%;width:{w}%;background:{col};border-radius:8px"></div></div></div>'
        )
    body = f'<div class="panel" style="flex:1;display:flex;flex-direction:column;justify-content:center;padding:8px 32px">{rows}</div>'
    return _shell(palette=palette, date_range=date_range, body=body, script="", lib_js=lib_js, logo_uri=logo_uri)


def build_hero(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: biggest mover as a big area chart + the others as stat chips (ECharts)."""
    hero = max(series, key=lambda s: abs(s["pct"]))
    others = [s for s in series if s["name"] != hero["name"]]
    chips = ""
    for s in others:
        col = palette["up"] if s["pct"] >= 0 else palette["down"]
        chips += (
            f'<div style="background:{palette["panel"]};border:1px solid {palette["stroke"]};border-radius:14px;padding:16px 18px;margin-bottom:14px">'
            f'<div style="font-size:14px;font-weight:700;color:{palette["muted"]};text-transform:uppercase;letter-spacing:1px">{html_lib.escape(str(s["name"]))}</div>'
            f'<div style="font-size:26px;font-weight:800;color:{palette["text"]};margin-top:6px">{s["last_close"]:,.2f}</div>'
            f'<div style="font-size:17px;font-weight:800;color:{col};margin-top:2px">{_fmt_pct(s["pct"])}</div></div>'
        )
    body = (
        f'<div style="flex:1;display:flex;gap:20px">'
        f'<div class="panel" style="flex:1.9;display:flex;flex-direction:column;padding:18px 22px 12px">'
        f'{_heading(hero["name"], hero["pct"], palette)}<div id="c" style="flex:1;width:100%"></div></div>'
        f'<div style="flex:1;display:flex;flex-direction:column;justify-content:center">{chips}</div></div>'
    )
    script = (
        (
            """
const H=__H__, P=__P__;
const up = H.pct>=0; const c = up?P.up:P.down;
const ch=echarts.init(document.getElementById('c'),null,{renderer:'canvas',devicePixelRatio:2});
ch.setOption({animation:false,backgroundColor:'transparent',
 grid:{left:56,right:18,top:14,bottom:18},
 xAxis:{type:'category',boundaryGap:false,data:H.closes.map((_,i)=>i),axisLine:{show:false},axisTick:{show:false},axisLabel:{show:false}},
 yAxis:{type:'value',scale:true,axisLabel:{color:P.muted},axisLine:{show:false},splitLine:{lineStyle:{color:P.grid}}},
 series:[{type:'line',data:H.closes,smooth:true,showSymbol:false,lineStyle:{width:3,color:c},
   areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:c+'66'},{offset:1,color:c+'05'}])}}]});
"""
        )
        .replace(
            "__H__", json.dumps({"name": hero["name"], "pct": round(hero["pct"], 2), "closes": list(hero["closes"])})
        )
        .replace("__P__", json.dumps(palette))
    )
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


def build_small_multiples(series: list[dict], date_range: str, palette: dict, lib_js: str, logo_uri: str = "") -> str:
    """Layout: a row of mini area sparklines, one per instrument, each labeled (ECharts)."""
    body = '<div class="panel" style="flex:1;padding:14px 18px"><div id="c" style="width:100%;height:100%"></div></div>'
    script = (
        (
            """
const S=__S__, P=__P__;
const n=S.length, gap=5, w=(100-gap*(n+1))/n;
const grids=[],xs=[],ys=[],sers=[],titles=[];
S.forEach((s,i)=>{
 const left=gap+i*(w+gap), cx=left+w/2, c=(s.pct>=0?P.up:P.down);
 grids.push({left:left+'%',top:84,width:w+'%',bottom:24});
 xs.push({type:'category',gridIndex:i,show:false,data:s.path.map((_,k)=>k)});
 ys.push({type:'value',gridIndex:i,show:false,scale:true});
 sers.push({type:'line',xAxisIndex:i,yAxisIndex:i,data:s.path,smooth:true,showSymbol:false,lineStyle:{width:2,color:c},
   areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:c+'55'},{offset:1,color:c+'04'}])}});
 titles.push({text:s.name,left:cx+'%',top:32,textAlign:'center',textStyle:{color:P.text,fontSize:15,fontWeight:700}});
 titles.push({text:(s.pct>=0?'+':'')+s.pct.toFixed(1)+'%',left:cx+'%',top:54,textAlign:'center',textStyle:{color:c,fontSize:17,fontWeight:800}});
});
const ch=echarts.init(document.getElementById('c'),null,{renderer:'canvas',devicePixelRatio:2});
ch.setOption({animation:false,backgroundColor:'transparent',grid:grids,xAxis:xs,yAxis:ys,series:sers,title:titles});
"""
        )
        .replace("__S__", _norm(series))
        .replace("__P__", json.dumps(palette))
    )
    return _shell(palette=palette, date_range=date_range, body=body, script=script, lib_js=lib_js, logo_uri=logo_uri)


# Layout registry — defined AFTER all builders. Rotates per post; engine = vendored lib.
LAYOUTS = [
    {"name": "candlestick", "builder": build_candlestick_pro, "engine": "lwc", "palette": 1},
    {"name": "bar-ranking", "builder": build_bar_ranking, "engine": "echarts", "palette": 0},
    {"name": "hero-standout", "builder": build_hero, "engine": "echarts", "palette": 1},
    {"name": "treemap", "builder": build_treemap, "engine": "echarts", "palette": 0},
    {"name": "combined-overlay", "builder": build_combined_overlay, "engine": "echarts", "palette": 2},
    {"name": "scoreboard", "builder": build_scoreboard, "engine": "html", "palette": 0},
    {"name": "radial", "builder": build_radial, "engine": "echarts", "palette": 1},
    {"name": "small-multiples", "builder": build_small_multiples, "engine": "echarts", "palette": 2},
    {"name": "waterfall", "builder": build_waterfall, "engine": "echarts", "palette": 0},
    {"name": "heatmap", "builder": build_heatmap, "engine": "echarts", "palette": 2},
    {"name": "slope", "builder": build_slope, "engine": "echarts", "palette": 1},
]


def select_card_layout(n: int) -> dict:
    """Pick the card layout for this post, round-robin (by market_pulse post count)."""
    return LAYOUTS[n % len(LAYOUTS)]


def _fmt_tokens(n: int) -> str:
    """25298310 -> '25.3M'; smaller -> comma form."""
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    return f"{n:,}"


def build_devtrack_card(m: dict, date_range: str, palette: dict, lib_js: str = "", logo_uri: str = "") -> str:
    """Luxury Building-in-Public stat-card (Phase 2.11) — big-number scoreboard of the
    real DevTrack weekly metrics. Pure HTML/CSS premium tiles (no chart lib)."""
    p = palette

    def tile(big: str, label: str, sub: str, accent: bool = False) -> str:
        col = p["accent"] if accent else p["text"]
        return (
            '<div class="panel" style="flex:1;display:flex;flex-direction:column;justify-content:center;padding:22px 28px">'
            f'<div style="font-size:13px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:{p["muted"]}">{html_lib.escape(label)}</div>'
            f'<div style="font-size:44px;font-weight:800;color:{col};line-height:1.05;margin-top:10px">{html_lib.escape(big)}</div>'
            f'<div style="font-size:15px;color:{p["muted"]};margin-top:8px">{html_lib.escape(sub)}</div></div>'
        )

    days = str(m.get("days_worked", "")).replace(" of ", "/")
    row1 = (
        tile(f"{m.get('total_hours', 0):g}h", "Total Working", f"{m.get('code_hours', 0):g}h code & build", accent=True)
        + tile(str(m.get("commits", 0)), "Git Commits", f"{m.get('files_changed', 0)} files touched")
        + tile(f"+{m.get('lines_added', 0):,}", "Lines Shipped", f"-{m.get('lines_deleted', 0):,} removed")
    )
    row2 = (
        tile(str(m.get("tests_added", 0)), "New Tests", "test-first (RECR)", accent=True)
        + tile(
            _fmt_tokens(int(m.get("ai_output_tokens", 0))),
            "AI Output Tokens",
            f"{m.get('ai_sessions', 0)} orchestration sessions",
        )
        + tile(days or "—", "Days Shipped", f"{m.get('momentum', '')} vs last week".strip())
    )
    body = (
        '<div style="flex:1;display:flex;flex-direction:column;gap:18px">'
        f'<div style="flex:1;display:flex;gap:18px">{row1}</div>'
        f'<div style="flex:1;display:flex;gap:18px">{row2}</div></div>'
    )
    return _shell(
        palette=p,
        date_range=date_range,
        body=body,
        script="",
        lib_js="",
        logo_uri=logo_uri,
        kicker="Building in Public",
        foot="Weekly build report · real data: WakaTime + Git · LuBot",
    )


def _folio(issue: int | None, date_range: str) -> str:
    """Magazine folio: 'No. 27 · June 27, 2026' (issue optional, date optional)."""
    parts = []
    if issue:
        parts.append(f"No. {issue}")
    if date_range:
        parts.append(date_range)
    return " · ".join(parts)


def build_headline_card(
    headline: str,
    source: str = "",
    date_range: str = "",
    dek: str = "",
    logo_uri: str = "",
    kicker: str = "AI News",
    issue: int | None = None,
    brand: dict = BRAND,
) -> str:
    """HEADLINE card (Phase 2.16 E) for ai_news — the article HEADLINE + source inside the
    universal frame, instead of screenshotting the source site. Headline size adapts to length."""
    h = " ".join(headline.split())
    size = 66 if len(h) <= 36 else 56 if len(h) <= 58 else 46 if len(h) <= 88 else 38
    dek_html = (
        f'<div style="font-family:Grotesk;font-weight:500;font-size:23px;line-height:1.45;'
        f'color:{brand["footer"]};margin-top:22px;max-width:92%">{html_lib.escape(" ".join(dek.split()))}</div>'
        if dek.strip()
        else ""
    )
    src_html = (
        '<div style="display:flex;align-items:center;gap:11px;margin-top:30px">'
        f'<span style="width:9px;height:9px;border-radius:50%;background:{brand["blue"]};box-shadow:0 0 14px {brand["blue"]}"></span>'
        f'<span style="font-family:Grotesk;font-weight:700;font-size:16px;letter-spacing:1.5px;color:{brand["blue"]}">'
        f"{html_lib.escape(source.lower())}</span></div>"
        if source.strip()
        else ""
    )
    body = (
        f'<div style="font-family:Fraunces;font-weight:400;font-size:{size}px;line-height:1.1;'
        f'letter-spacing:-0.5px;color:{brand["headline"]};text-shadow:0 2px 30px rgba(0,0,0,.4)">'
        f"{html_lib.escape(h)}</div>{dek_html}{src_html}"
    )
    return _frame(
        kicker=kicker,
        body=body,
        disclaimer="AI news, curated and explained · LuBot",
        folio=_folio(issue, date_range),
        logo_uri=logo_uri,
        brand=brand,
    )


def build_insight_card(
    headline: str,
    kicker: str = "Insight",
    date_range: str = "",
    logo_uri: str = "",
    disclaimer: str = "Field notes from building in AI · LuBot",
    issue: int | None = None,
    brand: dict = BRAND,
) -> str:
    """INSIGHT card (Phase 2.16 E) for opinion categories (tech_talk, biohacker, Investing
    Principle) — the post's core take as a big Fraunces serif pull-quote inside the universal
    frame, signed. Pull-quote font-size adapts to length so it always fits."""
    h = " ".join(headline.split())
    size = 80 if len(h) <= 40 else 66 if len(h) <= 64 else 54 if len(h) <= 95 else 44
    body = (
        f'<div style="font-family:Fraunces;font-weight:400;font-size:{size}px;line-height:1.06;'
        f'letter-spacing:-1px;color:{brand["headline"]};text-shadow:0 2px 30px rgba(0,0,0,.4)">'
        f"{html_lib.escape(h)}</div>"
    )
    return _frame(
        kicker=kicker,
        body=body,
        disclaimer=disclaimer,
        folio=_folio(issue, date_range),
        logo_uri=logo_uri,
        brand=brand,
    )
