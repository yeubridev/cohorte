"""Regenerate the Cohorte brand SVGs: python3 assets/brand/generate.py assets/brand"""
import math, random, sys
OUT = sys.argv[1]
THEMES = {
    "light": dict(bg="#FAF7F2", ink="#1B1F2A", sub="#5B6170", dot="#C9CDD6", pos="#1F9E89", neg="#E4572E", line="#1B1F2A"),
    "dark":  dict(bg="#11141B", ink="#F4F1EA", sub="#A3A9B7", dot="#3A4050", pos="#36C5A8", neg="#FF6B45", line="#F4F1EA"),
}
FONT = "font-family=\"'Inter','Segoe UI','Helvetica Neue',Arial,sans-serif\""

def mark(cx, cy, r, t, scale=1.0):
    """Concentric rings of agents around one highlighted voice."""
    rnd = random.Random(7)
    parts = []
    rings = [(0.38, 8), (0.62, 14), (0.86, 20)]
    for i, (rf, n) in enumerate(rings):
        for k in range(n):
            a = 2 * math.pi * k / n + i * 0.35
            x, y = cx + math.cos(a) * r * rf, cy + math.sin(a) * r * rf
            roll = rnd.random()
            col = t["pos"] if roll < 0.22 else t["neg"] if roll < 0.36 else t["ink"]
            op = 1 if col != t["ink"] else 0.85 - i * 0.2
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r*0.055*scale*(1-i*0.12):.1f}" fill="{col}" opacity="{op:.2f}"/>')
    for a in (0.3, 2.4, 4.3):  # ties from the voice to the crowd
        x, y = cx + math.cos(a) * r * 0.62, cy + math.sin(a) * r * 0.62
        parts.insert(0, f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="{t["neg"]}" stroke-width="{r*0.02:.1f}" opacity="0.55"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r*0.15:.1f}" fill="{t["neg"]}"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r*0.24:.1f}" fill="none" stroke="{t["neg"]}" stroke-width="{r*0.025:.1f}" opacity="0.5"/>')
    return "\n".join(parts)

def crowd(x0, y0, w, h, t, seed=3, step=18):
    """Dot field: a crowd whose mood drifts from negative (left) to positive (right), with clusters."""
    rnd = random.Random(seed)
    parts = []
    nodes = []
    for gx in range(int(w // step)):
        for gy in range(int(h // step)):
            x = x0 + gx * step + rnd.uniform(-4, 4)
            y = y0 + gy * step + rnd.uniform(-4, 4)
            fx = gx / (w / step)
            fade = min(1, 0.25 + fx * 2.5)  # fade in from the text side
            roll = rnd.random()
            p_neg = 0.30 * (1 - fx); p_pos = 0.32 * fx
            if roll < p_neg: col, op = t["neg"], 0.9
            elif roll < p_neg + p_pos: col, op = t["pos"], 0.9
            else: col, op = t["dot"], 0.9
            rr = rnd.choice([2.2, 2.6, 3.0, 3.4])
            if fade < 0.05: continue
            nodes.append((x, y, col))
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rr}" fill="{col}" opacity="{op*fade:.2f}"/>')
    # a few conversation threads between colored agents
    colored = [n for n in nodes if n[2] != t["dot"]]
    lines = []
    for _ in range(26):
        a, b = rnd.sample(colored, 2)
        if math.dist(a[:2], b[:2]) < 120:
            lines.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="{a[2]}" stroke-width="1.2" opacity="0.35"/>')
    return "\n".join(lines + parts)

def banner(theme, w=1280, h=360):
    t = THEMES[theme]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="Cohorte: rehearse your launch with a cohort of AI robots">
<rect width="{w}" height="{h}" rx="18" fill="{t['bg']}"/>
{crowd(800, 34, 450, h - 50, t)}
{mark(150, h/2, 92, t)}
<text x="272" y="{h/2 + 4}" {FONT} font-size="76" font-weight="800" letter-spacing="-2" fill="{t['ink']}">cohorte</text>
<text x="276" y="{h/2 + 50}" {FONT} font-size="23" font-weight="500" fill="{t['sub']}">Rehearse your launch with 10,000 robots.</text>
<text x="276" y="{h/2 + 82}" {FONT} font-size="16" font-weight="600" letter-spacing="2.5" fill="{t['neg']}">SWARM SIMULATION · CLAUDE SKILL</text>
</svg>'''

def logo(theme, s=256):
    t = THEMES[theme]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" viewBox="0 0 {s} {s}" role="img" aria-label="Cohorte logo">
<rect width="{s}" height="{s}" rx="{s*0.22:.0f}" fill="{t['bg']}"/>
{mark(s/2, s/2, s*0.42, t)}
</svg>'''

def social(theme="dark", w=1280, h=640):
    t = THEMES[theme]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<rect width="{w}" height="{h}" fill="{t['bg']}"/>
{crowd(720, 40, 520, h - 70, t, seed=11, step=20)}
{mark(190, 250, 120, t)}
<text x="80" y="470" {FONT} font-size="96" font-weight="800" letter-spacing="-3" fill="{t['ink']}">cohorte</text>
<text x="84" y="525" {FONT} font-size="28" font-weight="500" fill="{t['sub']}">Rehearse your launch with 10,000 robots.</text>
<text x="84" y="575" {FONT} font-size="18" font-weight="600" letter-spacing="3" fill="{t['neg']}">SWARM SIMULATION · CLAUDE SKILL</text>
</svg>'''

for th in THEMES:
    open(f"{OUT}/banner-{th}.svg", "w").write(banner(th))
    open(f"{OUT}/logo-{th}.svg", "w").write(logo(th))
open(f"{OUT}/social-preview.svg", "w").write(social())
print("ok")
