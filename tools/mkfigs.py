"""Render the bus-reference figures from real captures (tpu-autofeeder bench, 2026-11-23)."""
import json, re, sys, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

D = sys.argv[1]; OUT = sys.argv[2]; SP = sys.argv[3]
os.makedirs(OUT, exist_ok=True)
SR = 8e6
SRC = "Source: tpu-autofeeder bench capture, X2D 01.01.00.00 + AMS 2 Pro, 2026-11-23"
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 140, "savefig.bbox": "tight", "axes.titleweight": "bold"})
BLUE, ORANGE, AQUA, VIOLET, GRAY = "#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#898781"

def foot(fig, y=-0.01):
    fig.text(0.01, y, SRC, fontsize=7, color=GRAY, ha="left", va="top")

def load_bits(path):
    b = np.frombuffer(open(path, "rb").read(), dtype=np.uint8)
    return {k: ((b >> k) & 1).astype(np.uint8) for k in range(8)}

def load_dec(path):
    out = []
    for l in open(path):
        m = re.match(r"(\d+)-(\d+) uart-1: ([0-9A-F]{2})", l.strip())
        if m: out.append((int(m.group(1)), int(m.group(2)), int(m.group(3), 16)))
    return out

def frames(dec, gap_us=30):
    """Split a decoded byte stream into frames using 0x3D starts, header length and gaps."""
    fr = []; i = 0
    while i < len(dec):
        if dec[i][2] != 0x3D: i += 1; continue
        j = i + 1
        while j < len(dec) and (dec[j][0] - dec[j-1][1]) / SR * 1e6 < gap_us: j += 1
        bs = [x[2] for x in dec[i:j]]
        if len(bs) >= 5:
            if bs[1] & 0x80: typ = "s%02X" % bs[4]; ln = bs[2]
            elif len(bs) >= 13: typ = "L%04X" % (bs[11] | bs[12] << 8); ln = bs[4] | bs[5] << 8
            else: typ = None; ln = 0
            if typ: fr.append({"start": dec[i][0], "end": dec[j-1][1], "bytes": bs, "type": typ, "len": ln})
        i = j
    return fr

R = load_bits(f"{D}/right.bin"); L = load_bits(f"{D}/left.bin")
dP = load_dec(f"{D}/dec_D7.txt")   # D0 RX printer side  (from printer)
dPf = load_dec(f"{D}/dec_D3.txt")  # D4 TX AMS side      (forwarded to AMS)
dA = load_dec(f"{D}/dec_D4.txt")   # D3 RX AMS side      (from AMS)
dAf = load_dec(f"{D}/dec_D6.txt")  # D1 TX printer side  (forwarded to printer)
fP, fPf, fA, fAf = frames(dP), frames(dPf), frames(dA), frames(dAf)

# ---------- 1. bridge timing: one printer frame and one AMS reply ----------
def trace(ax, y, sig, s0, s1, label, color):
    seg = sig[s0:s1].astype(float)
    t = (np.arange(s0, s1) - s0) / SR * 1e6
    ax.step(t, y + 0.7 * seg, where="post", color=color, lw=1)
    ax.text(-2, y + 0.35, label, ha="right", va="center", fontsize=8)

def annotate_bytes(ax, y, dec, s0, s1, color):
    for a, b, v in dec:
        if s0 <= a and b <= s1:
            ax.text(((a + b) / 2 - s0) / SR * 1e6, y + 0.95, "%02X" % v, ha="center", fontsize=6.5, color=color, family="monospace")

reply = next(f for f in fA if f["start"] > 0)
prev = [f for f in fP if f["end"] < reply["start"]][-1]
fig, axes = plt.subplots(2, 1, figsize=(12, 6.2))
for ax, (fin, sig_in, sig_out, sig_en, dec_in, dec_out, title, lab) in zip(axes, [
        (prev, R[7], R[3], R[2], dP, dPf, "Printer -> AMS: frame in on the printer side, out on the AMS side",
         ["printer-side RX (D0)", "AMS-side TX (D4)", "AMS-side DE (D5)"]),
        (reply, R[4], R[6], R[5], dA, dAf, "AMS -> printer: reply in on the AMS side, out on the printer side",
         ["AMS-side RX (D3)", "printer-side TX (D1)", "printer-side DE (D2)"])]):
    s0 = fin["start"] - int(30e-6 * SR); s1 = fin["end"] + int(160e-6 * SR)
    trace(ax, 4, sig_in, s0, s1, lab[0], BLUE); annotate_bytes(ax, 4, dec_in, s0, s1, BLUE)
    trace(ax, 2, sig_out, s0, s1, lab[1], ORANGE); annotate_bytes(ax, 2, dec_out, s0, s1, ORANGE)
    trace(ax, 0, sig_en, s0, s1, lab[2], AQUA)
    out = next((f for f in (fPf if sig_out is R[3] else fAf) if f["start"] >= fin["start"]), None)
    if out:
        lat = (out["start"] - fin["start"]) / SR * 1e6
        x0 = (fin["start"] - s0) / SR * 1e6; x1 = (out["start"] - s0) / SR * 1e6
        ax.annotate("", xy=(x1, 3.2), xytext=(x0, 3.2), arrowprops=dict(arrowstyle="->", color=GRAY))
        ax.text((x0 + x1) / 2, 3.3, f"{lat:.0f} us", ha="center", fontsize=8, color=GRAY)
    ax.set_yticks([]); ax.set_ylim(-0.3, 5.2); ax.set_xlim(0, (s1 - s0) / SR * 1e6)
    ax.set_title(f"{title}  ({fin['type']}, {len(fin['bytes'])} bytes)", fontsize=10, loc="left")
    ax.set_xlabel("microseconds")
fig.suptitle("Bridge forwarding on the analyzer: bytes in, bytes out, driver enable", y=1.0)
fig.tight_layout(); foot(fig); fig.savefig(f"{OUT}/bridge_frame_timing.png"); plt.close(fig)

# ---------- 2. forwarding latency histogram ----------
def lat(fin, fout):
    o = sorted(f["start"] for f in fout); res = []; k = 0
    for f in fin:
        while k < len(o) and o[k] < f["start"]: k += 1
        if k < len(o): res.append((o[k] - f["start"]) / SR * 1e6)
    return [x for x in res if x < 2000]
lp, la = lat(fP, fPf), lat(fA, fAf)
fig, ax = plt.subplots(figsize=(8, 3.6))
bins = np.arange(80, 140, 1)
ax.hist(lp, bins=bins, color=BLUE, alpha=0.85, label=f"printer -> AMS (n={len(lp)}, median {np.median(lp):.0f} us)")
ax.hist(la, bins=bins, color=ORANGE, alpha=0.85, label=f"AMS -> printer (n={len(la)}, median {np.median(la):.0f} us)")
ax.set_xlabel("first byte in -> first byte out (microseconds)"); ax.set_ylabel("frames")
ax.set_title("Bridge forwarding latency per frame (2 s capture)", loc="left"); ax.legend(frameon=False, fontsize=8)
foot(fig); fig.savefig(f"{OUT}/bridge_latency.png"); plt.close(fig)

# ---------- 3. traffic mix ----------
from collections import Counter
cp, ca = Counter(f["type"] for f in fP), Counter(f["type"] for f in fA)
types = sorted(set(cp) | set(ca), key=lambda t: -(cp[t] + ca[t]))
fig, ax = plt.subplots(figsize=(10, 4))
x = np.arange(len(types)); w = 0.4
ax.bar(x - w / 2, [cp[t] for t in types], w, color=BLUE, label=f"from printer ({sum(cp.values())} frames)")
ax.bar(x + w / 2, [ca[t] for t in types], w, color=ORANGE, label=f"from AMS ({sum(ca.values())} frames)")
lab = [("short 0x%s" % t[1:]) if t[0] == "s" else ("long 0x%s" % t[1:]) for t in types]
ax.set_xticks(x); ax.set_xticklabels(lab, rotation=45, ha="right", fontsize=8)
ax.set_ylabel("frames in 2 s"); ax.set_title("Message types on the bus, idle with filament loaded (2 s)", loc="left")
ax.legend(frameon=False, fontsize=8)
fig.tight_layout(); foot(fig, y=-0.04); fig.savefig(f"{OUT}/traffic_mix.png"); plt.close(fig)

# ---------- 4. frame rate over time ----------
fig, ax = plt.subplots(figsize=(10, 3))
edges = np.arange(0, 2.0001, 0.02)
for fr, c, n in ((fP, BLUE, "from printer"), (fA, ORANGE, "from AMS")):
    ax.hist([f["start"] / SR for f in fr], bins=edges, color=c, alpha=0.8, label=n)
ax.set_xlabel("seconds"); ax.set_ylabel("frames per 20 ms"); ax.legend(frameon=False, fontsize=8)
ax.set_title("Bus activity: frames per 20 ms window (polls and replies)", loc="left")
foot(fig); fig.savefig(f"{OUT}/frame_rate.png"); plt.close(fig)

# ---------- 5. pins 5/6 PWM ----------
s0 = int(np.argmax(np.diff(L[6].astype(int)) == 1)); s1 = s0 + int(350e-6 * SR)
t = (np.arange(s0, s1) - s0) / SR * 1e6
fig, ax = plt.subplots(figsize=(10, 3.6))
for y, k, lab, c in ((6, 6, "wire 5 (pre-10k)", BLUE), (4.5, 4, "wire 5 (post-10k)", BLUE),
                     (3, 7, "wire 6 (pre-10k)", VIOLET), (1.5, 5, "wire 6 (post-10k)", VIOLET)):
    ax.step(t, y + L[k][s0:s1], where="post", color=c, lw=1.2); ax.text(-4, y + 0.5, lab, ha="right", va="center", fontsize=8)
d5 = L[6].mean() * 100; d6 = L[7].mean() * 100
ax.set_yticks([]); ax.set_xlabel("microseconds"); ax.set_xlim(0, t[-1])
ax.set_title(f"Cable pins 5/6: complementary 10 kHz PWM (period 100.0 us); duty now {d5:.1f} % / {d6:.1f} %", loc="left")
foot(fig); fig.savefig(f"{OUT}/pins56_pwm.png"); plt.close(fig)

# ---------- 6/7/8. buffer duty timelines ----------
def series_unload():
    a = json.load(open(f"{SP}/unload/seg.log.json"))["buf"]; ev = json.load(open(f"{SP}/unload/seg.log.ev.json"))
    return sorted([[t, d] for t, d in a] + [[h, d] for h, b, d in ev])
def series_load():
    return json.load(open(f"{SP}/load2/seg.log.json"))["pts"]
UC = [(24.29, "stop_on_use 07 00"), (29.32, "before_pull_back 09 3F"), (31.74, "unload 03, slot FF"), (38.09, "idle")]
LC = [(14.32, "load 03, slot 1"), (24.29, "on_use 07 7F"), (45.74, "09 7F"), (48.05, "07 00")]
def duty_plot(pts, cmds, xmax, title, fname, bands=True, ly=96):
    pts = [p for p in pts if p[0] <= xmax]
    fig, ax = plt.subplots(figsize=(11, 4))
    if bands:
        ax.axhspan(36, 66, color=AQUA, alpha=0.08); ax.text(xmax * 0.995, 66.5, "AMS feed band ~37-65 %", ha="right", fontsize=8, color=AQUA)
    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=BLUE, lw=1.3)
    for i, (tc, lab) in enumerate(cmds):
        if tc <= xmax:
            ax.axvline(tc, color=GRAY, ls="--", lw=0.9); ax.text(tc + xmax * 0.004, ly - 6 * (i % 4), lab, fontsize=8, color="#52514e")
    ax.set_ylim(0, 100); ax.set_xlim(0, xmax); ax.set_xlabel("seconds after marker"); ax.set_ylabel("pin 5 duty (%)")
    ax.set_title(title, loc="left"); foot(fig); fig.savefig(f"{OUT}/{fname}"); plt.close(fig)
duty_plot(series_unload(), UC, 61, "Buffer duty during an unload (AMS B, slot 1) vs printer motion commands", "buffer_unload.png", bands=False, ly=34)
duty_plot(series_load(), LC, 200, "Buffer duty during a load + purge (AMS B, slot 1) vs printer motion commands", "buffer_load.png")
lp_ = [p for p in series_load() if 54 <= p[0] <= 112]
fig, ax = plt.subplots(figsize=(11, 3.6))
ax.axhline(65, color=ORANGE, ls=":", lw=1); ax.text(112, 65.8, "feed starts ~65 %", ha="right", fontsize=8, color=ORANGE)
ax.axhline(37, color=AQUA, ls=":", lw=1); ax.text(112, 32.5, "feed stops ~37 %", ha="right", fontsize=8, color=AQUA)
ax.plot([p[0] for p in lp_], [p[1] for p in lp_], color=BLUE, lw=1.3)
ax.set_xlim(54, 112); ax.set_ylim(25, 75); ax.set_xlabel("seconds after marker"); ax.set_ylabel("pin 5 duty (%)")
ax.set_title("The AMS's own feed loop during purge: ~7.1 s cycle, no new bus commands", loc="left")
foot(fig); fig.savefig(f"{OUT}/feed_loop_zoom.png"); plt.close(fig)

# ---------- 9. frame anatomy ----------
fig, axes = plt.subplots(2, 1, figsize=(12, 3.6))
LONG = [("3D", "start"), ("flag\n<0x80", ""), ("seq", ""), ("seq", ""), ("len\nlo", ""), ("len\nhi", ""), ("CRC8", ""),
        ("target\nlo", ""), ("target\nhi", ""), ("source\nlo", ""), ("source\nhi", ""), ("type\nlo", ""), ("type\nhi", ""),
        ("payload ...", "wide"), ("CRC16\nlo", ""), ("CRC16\nhi", "")]
SHORT = [("3D", "start"), ("flag\n>=0x80", ""), ("len", ""), ("CRC8", ""), ("type", ""), ("payload ...", "wide"),
         ("CRC16\nlo", ""), ("CRC16\nhi", "")]
for ax, fields, title in ((axes[0], LONG, "Long header (flag < 0x80): length is 16-bit little-endian at bytes 4-5; frames reach ~1,069 bytes"),
                          (axes[1], SHORT, "Short header (flag >= 0x80): 1-byte length at byte 2, 1-byte type at byte 4")):
    x = 0
    for i, (lab, kind) in enumerate(fields):
        w = 3.2 if kind == "wide" else 1
        col = {"start": "#FAEEDA", "wide": "#F1EFE8"}.get(kind, "#E6F1FB")
        if "CRC" in lab: col = "#FAECE7"
        if "type" in lab: col = "#EEEDFE"
        ax.add_patch(Rectangle((x, 0), w, 1, facecolor=col, edgecolor="#5F5E5A", lw=0.6))
        ax.text(x + w / 2, 0.5, lab, ha="center", va="center", fontsize=7.5)
        if kind != "wide":
            idx = ("n-2" if "lo" in lab else "n-1") if "CRC16" in lab else str(i)
            ax.text(x + w / 2, 1.08, idx, ha="center", fontsize=7, color=GRAY)
        x += w
    ax.set_xlim(0, x); ax.set_ylim(0, 1.25); ax.axis("off"); ax.set_title(title, fontsize=9, loc="left")
fig.text(0.01, 0.02, "CRC8: poly 0x39, init 0x66 over the header.  CRC16: poly 0x1021, init 0x913D, low byte first, over the frame.", fontsize=8)
foot(fig); fig.savefig(f"{OUT}/frame_anatomy.png"); plt.close(fig)

# ---------- 10. boot sequence (first appearance of each type after power-on) ----------
ev = []
for l in open(f"{D}/boot.log", errors="replace"):
    m = re.match(r"(\d\d):(\d\d):([\d.]+) \[A\]\s+\d+ ms \[(A->P|P->A)\] NEW ([0-9A-F]{6})", l)
    if m:
        ts = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        ev.append((ts, "from printer" if m.group(4) == "A->P" else "from AMS", m.group(5)))
if ev:
    t0 = ev[0][0]
    first = {}
    for ts, d, k in ev:
        first.setdefault(k, {}).setdefault(d, ts - t0)
    keys = sorted(first, key=lambda k: min(first[k].values()))
    fig, ax = plt.subplots(figsize=(10, 0.32 * len(keys) + 1.4))
    for r, k in enumerate(keys):
        y = len(keys) - 1 - r
        ax.axhline(y, color="#e1e0d9", lw=0.6, zorder=0)
        for d, c, m in (("from printer", BLUE, "o"), ("from AMS", ORANGE, "s")):
            if d in first[k]: ax.plot(first[k][d], y, m, color=c, ms=6)
    names = [("short 0x" + k[4:]) if k.startswith("01") else ("long 0x" + k[2:]) for k in keys]
    ax.set_yticks(range(len(keys))); ax.set_yticklabels(names[::-1], fontsize=8, family="monospace")
    ax.plot([], [], "o", color=BLUE, label="first seen from printer"); ax.plot([], [], "s", color=ORANGE, label="first seen from AMS")
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    ax.set_xlabel("seconds after the first logged frame (power-on)")
    ax.set_title("Power-on: when each message type first crossed the bridge", loc="left")
    fig.tight_layout(); foot(fig, y=-0.02); fig.savefig(f"{OUT}/boot_first_seen.png"); plt.close(fig)

print("lat printer->AMS median %.1f us  AMS->printer median %.1f us" % (np.median(lp), np.median(la)))
print("frames: from printer %d, from AMS %d in 2 s" % (len(fP), len(fA)))
print("types:", {t: (cp[t], ca[t]) for t in types})
print("boot types:", len(ev))
print(sorted(os.listdir(OUT)))
