"""Build the Sentinel pitch deck as a clean, custom PPTX.

This is a from-scratch deck (no Accenture template, no Accenture logo): white
canvas, one purple accent, and an original shield-pulse mark drawn in shapes.
The slide text and the speaker notes are lifted straight from docs/pitch.md, so
the deck and the script stay in sync. Regenerate any time with:

    python tools/build_pitch_deck.py

Output: docs/Sentinel_Pitch.pptx
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

# --- brand ---
PURPLE = RGBColor(0xA1, 0x00, 0xFF)
PURPLE_DEEP = RGBColor(0x75, 0x00, 0xC0)
PURPLE_INK = RGBColor(0x3D, 0x00, 0x66)
TINT = RGBColor(0xF6, 0xEF, 0xFE)
INK = RGBColor(0x14, 0x14, 0x1D)
INK2 = RGBColor(0x40, 0x40, 0x4F)
MUTED = RGBColor(0x70, 0x70, 0x7F)
LINE = RGBColor(0xE7, 0xE7, 0xEF)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
RED = RGBColor(0xD9, 0x00, 0x2B)

FONT = "Segoe UI"
FONT_LIGHT = "Segoe UI Light"

EMU_PER_IN = 914400


def _pt_color(run, color):
    run.font.color.rgb = color


class Deck:
    def __init__(self) -> None:
        self.prs = Presentation()
        self.prs.slide_width = Inches(13.333)
        self.prs.slide_height = Inches(7.5)
        self.W = 13.333
        self.H = 7.5

    def save(self, path: Path) -> None:
        self.prs.save(str(path))

    # --- primitives ---

    def _blank(self):
        return self.prs.slides.add_slide(self.prs.slide_layouts[6])

    def _rect(self, slide, x, y, w, h, fill=None, line=None, line_w=1.0, shape=MSO_SHAPE.RECTANGLE):
        sp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
        if fill is None:
            sp.fill.background()
        else:
            sp.fill.solid()
            sp.fill.fore_color.rgb = fill
        if line is None:
            sp.line.fill.background()
        else:
            sp.line.color.rgb = line
            sp.line.width = Pt(line_w)
        sp.shadow.inherit = False
        return sp

    def _text(self, slide, x, y, w, h, runs, *, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
              space_after=6, line_spacing=1.0):
        """runs: list of paragraphs; each paragraph is a list of (text, size, color, bold, font)."""
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        for i, para in enumerate(runs):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            p.space_after = Pt(space_after)
            p.space_before = Pt(0)
            p.line_spacing = line_spacing
            for (txt, size, color, bold, fnt) in para:
                r = p.add_run()
                r.text = txt
                r.font.size = Pt(size)
                r.font.bold = bold
                r.font.name = fnt or FONT
                _pt_color(r, color)
        return tb

    def _accent_top(self, slide):
        # thin purple accent strip at the very top
        self._rect(slide, 0, 0, self.W, 0.09, fill=PURPLE)

    def _pageno(self, slide, n):
        self._text(slide, self.W - 1.2, self.H - 0.55, 0.9, 0.35,
                   [[(f"{n:02d}", 10, MUTED, False, FONT)]], align=PP_ALIGN.RIGHT)

    def _footer(self, slide, text):
        self._text(slide, 0.9, self.H - 0.55, 9.0, 0.35,
                   [[(text, 9.5, MUTED, False, FONT)]])

    def _mark(self, slide, x, y, size, *, on_dark=False):
        """Original Sentinel mark: a purple rounded square with a white heartbeat."""
        box = self._rect(slide, x, y, size, size, fill=PURPLE, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
        try:
            box.adjustments[0] = 0.28
        except Exception:
            pass
        # heartbeat polyline across the box
        base = y + size * 0.56
        pts_in = [
            (x + size * 0.16, base),
            (x + size * 0.34, base),
            (x + size * 0.46, base - size * 0.22),
            (x + size * 0.60, base + size * 0.26),
            (x + size * 0.72, base - size * 0.10),
            (x + size * 0.82, base),
            (x + size * 0.86, base),
        ]
        try:
            emu = [(int(px * EMU_PER_IN), int(py * EMU_PER_IN)) for (px, py) in pts_in]
            fb = slide.shapes.build_freeform(emu[0][0], emu[0][1], scale=1)
            fb.add_line_segments(emu[1:], close=False)
            hb = fb.convert_to_shape()
            hb.fill.background()
            hb.line.color.rgb = WHITE
            hb.line.width = Pt(2.4)
            hb.shadow.inherit = False
        except Exception:
            # fallback: a simple white cross so the mark still renders
            t = size * 0.12
            self._rect(slide, x + size * 0.5 - t / 2, y + size * 0.24, t, size * 0.52, fill=WHITE)
            self._rect(slide, x + size * 0.24, y + size * 0.5 - t / 2, size * 0.52, t, fill=WHITE)
        return box

    def _wordmark(self, slide, x, y):
        self._mark(slide, x, y, 0.62)
        self._text(slide, x + 0.78, y - 0.06, 4.0, 0.8,
                   [[("Sentinel", 26, INK, True, FONT)]], anchor=MSO_ANCHOR.MIDDLE)

    # --- slide templates ---

    def title_slide(self):
        s = self._blank()
        self._rect(s, 0, 0, self.W, self.H, fill=WHITE)
        # left purple band
        self._rect(s, 0, 0, 0.28, self.H, fill=PURPLE)
        self._mark(s, 0.95, 1.5, 1.5)
        self._text(s, 0.95, 3.25, 11.0, 1.4,
                   [[("Sentinel", 66, INK, True, FONT)]])
        self._text(s, 0.98, 4.5, 11.2, 1.0,
                   [[("A safety-first triage assistant for the emergency department", 22, INK2, False, FONT)]])
        self._text(s, 0.98, 5.5, 11.2, 0.5,
                   [[("Team Sentinel   ", 14, PURPLE_DEEP, True, FONT),
                     ("Track 2  ·  PatientTriage.ai", 14, MUTED, False, FONT)]])
        self._text(s, 0.98, self.H - 0.7, 11.2, 0.4,
                   [[("Accenture Innovation Challenge 2026  ·  Round 2", 11, MUTED, False, FONT)]])
        return s

    def statement_slide(self, n, kicker, big, sub, *, notes=""):
        s = self._blank()
        self._accent_top(s)
        self._text(s, 0.95, 0.75, 11.4, 0.4,
                   [[(kicker.upper(), 13, PURPLE_DEEP, True, FONT)]])
        self._text(s, 0.9, 1.7, 11.5, 3.2,
                   [[(big, 40, INK, True, FONT)]], line_spacing=1.05)
        if sub:
            self._text(s, 0.95, 4.9, 11.3, 1.6,
                       [[(sub, 18, INK2, False, FONT)]], line_spacing=1.2)
        self._pageno(s, n)
        if notes:
            s.notes_slide.notes_text_frame.text = notes
        return s

    def points_slide(self, n, title, points, *, notes="", kicker=None):
        """points: list of (heading, body)."""
        s = self._blank()
        self._accent_top(s)
        if kicker:
            self._text(s, 0.95, 0.7, 11.4, 0.35, [[(kicker.upper(), 12, PURPLE_DEEP, True, FONT)]])
        self._text(s, 0.9, 1.05 if kicker else 0.85, 11.5, 0.9, [[(title, 32, INK, True, FONT)]])
        # cards
        top = 2.35
        gap = 0.28
        n_pts = len(points)
        card_w = (self.W - 1.8 - gap * (n_pts - 1)) / n_pts
        for i, (head, body) in enumerate(points):
            x = 0.9 + i * (card_w + gap)
            self._rect(s, x, top, card_w, 3.9, fill=WHITE, line=LINE, line_w=1.0,
                       shape=MSO_SHAPE.ROUNDED_RECTANGLE)
            self._rect(s, x, top, card_w, 0.12, fill=PURPLE)
            self._text(s, x + 0.28, top + 0.4, card_w - 0.56, 1.0,
                       [[(head, 18, INK, True, FONT)]], line_spacing=1.05)
            self._text(s, x + 0.28, top + 1.35, card_w - 0.56, 2.4,
                       [[(body, 14, INK2, False, FONT)]], line_spacing=1.2)
        self._pageno(s, n)
        if notes:
            s.notes_slide.notes_text_frame.text = notes
        return s

    def agents_slide(self, n, notes=""):
        s = self._blank()
        self._accent_top(s)
        self._text(s, 0.9, 0.85, 11.5, 0.9, [[("Three agents, one firm rule", 32, INK, True, FONT)]])
        self._text(s, 0.95, 1.7, 11.3, 0.6,
                   [[("The engine sets the score. The model only reads text and explains. It never sets the number.",
                      16, INK2, False, FONT)]])
        agents = [
            ("Interpreter", "Structures messy intake and reads every vital against the patient's own age band. Computes the shock index, notes what is missing, scores confidence."),
            ("Adjudicator", "Assigns the five-level acuity, runs the stroke, heart-attack and sepsis clocks, and writes why. Danger-zone vitals only push up. Uncertainty escalates."),
            ("Watcher", "Keeps watching everyone still waiting. Re-checks the sick often, ratchets a patient up the moment they worsen or wait too long, and never lowers an acuity."),
        ]
        top = 2.7
        card_w = 3.7
        gap = 0.35
        total = card_w * 3 + gap * 2
        start = (self.W - total) / 2
        for i, (name, body) in enumerate(agents):
            x = start + i * (card_w + gap)
            self._rect(s, x, top, card_w, 3.5, fill=WHITE, line=LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
            self._rect(s, x, top, card_w, 0.7, fill=TINT)
            self._text(s, x, top + 0.14, card_w, 0.5, [[(f"Agent {i+1}", 11, PURPLE_DEEP, True, FONT)]],
                       align=PP_ALIGN.CENTER)
            self._text(s, x + 0.28, top + 0.85, card_w - 0.56, 0.6, [[(name, 20, INK, True, FONT)]])
            self._text(s, x + 0.28, top + 1.5, card_w - 0.56, 1.9, [[(body, 13, INK2, False, FONT)]],
                       line_spacing=1.2)
            if i < 2:
                self._text(s, x + card_w - 0.02, top + 1.4, gap + 0.1, 0.6,
                           [[(">", 26, PURPLE, True, FONT)]], align=PP_ALIGN.CENTER)
        self._pageno(s, n)
        if notes:
            s.notes_slide.notes_text_frame.text = notes
        return s

    def demo_slide(self, n, title, bullets, *, notes=""):
        s = self._blank()
        # purple hero band on the left, content on white
        self._rect(s, 0, 0, self.W, self.H, fill=WHITE)
        self._rect(s, 0, 0, 4.3, self.H, fill=PURPLE_INK)
        self._mark(s, 0.7, 0.7, 0.7, on_dark=True)
        self._text(s, 0.7, 2.4, 3.2, 1.4, [[("LIVE", 40, WHITE, True, FONT)], [("DEMO", 40, PURPLE, True, FONT)]],
                   line_spacing=1.0)
        self._text(s, 0.7, 4.3, 3.1, 2.0, [[("Shown on the running board. Slides pause here.", 13,
                                             RGBColor(0xD9, 0xC7, 0xF2), False, FONT)]], line_spacing=1.3)
        self._text(s, 4.85, 0.9, 7.7, 1.0, [[(title, 30, INK, True, FONT)]], line_spacing=1.05)
        rows = []
        for b in bullets:
            rows.append([(">  ", 15, PURPLE, True, FONT), (b, 15.5, INK2, False, FONT)])
        self._text(s, 4.85, 2.2, 7.8, 4.6, rows, space_after=12, line_spacing=1.15)
        self._pageno(s, n)
        if notes:
            s.notes_slide.notes_text_frame.text = notes
        return s

    def roadmap_slide(self, n, notes=""):
        s = self._blank()
        self._accent_top(s)
        self._text(s, 0.9, 0.85, 11.5, 0.9, [[("Roadmap: safe and small, outward", 32, INK, True, FONT)]])
        stages = [
            ("Now", "Prototype", "All three agents, live board, surge, override and audit, optional model. On simulated data. Done."),
            ("Next", "Shadow pilot", "One department, read-only, changes nothing. Measures agreement, mistriage, and the catches it would have made."),
            ("Then", "Supervised live", "Recommendation on, clinician in control. Deterioration monitoring in the live queue. Tuned to the site."),
            ("After", "Multi-site + learning", "Same configurable engine across sizes. Feedback loop improves thresholds. Autonomy rises only as accuracy earns it."),
        ]
        top = 2.4
        card_w = 2.86
        gap = 0.3
        for i, (when, name, body) in enumerate(stages):
            x = 0.9 + i * (card_w + gap)
            done = i == 0
            self._rect(s, x, top, card_w, 3.7, fill=(TINT if done else WHITE), line=(PURPLE if done else LINE),
                       line_w=(1.5 if done else 1.0), shape=MSO_SHAPE.ROUNDED_RECTANGLE)
            self._text(s, x + 0.24, top + 0.28, card_w - 0.48, 0.4, [[(when.upper(), 11, PURPLE_DEEP, True, FONT)]])
            self._text(s, x + 0.24, top + 0.7, card_w - 0.48, 0.7, [[(name, 18, INK, True, FONT)]])
            self._text(s, x + 0.24, top + 1.5, card_w - 0.48, 2.1, [[(body, 12.5, INK2, False, FONT)]],
                       line_spacing=1.2)
            if i < 3:
                self._text(s, x + card_w - 0.02, top + 1.5, gap + 0.1, 0.6, [[(">", 22, PURPLE, True, FONT)]],
                           align=PP_ALIGN.CENTER)
        self._pageno(s, n)
        if notes:
            s.notes_slide.notes_text_frame.text = notes
        return s

    def ask_slide(self, n, notes=""):
        s = self._blank()
        self._rect(s, 0, 0, self.W, self.H, fill=PURPLE_INK)
        self._mark(s, 0.95, 0.9, 0.9)
        self._text(s, 0.95, 2.1, 11.0, 1.0, [[("What we're asking for", 40, WHITE, True, FONT)]])
        asks = [
            "One partner emergency department willing to run Sentinel in shadow mode",
            "Read-only access to its records and flow data over standard interfaces",
            "A clinical champion to help tune thresholds and read the results",
        ]
        rows = [[(">  ", 18, PURPLE, True, FONT), (a, 18, WHITE, False, FONT)] for a in asks]
        self._text(s, 1.0, 3.4, 11.0, 2.2, rows, space_after=14, line_spacing=1.15)
        self._text(s, 0.98, 5.9, 11.2, 1.0,
                   [[("Safe when it's unsure. Always watching the people who wait. The decision always stays with the clinician.",
                      16, RGBColor(0xD9, 0xC7, 0xF2), False, FONT)]], line_spacing=1.25)
        self._pageno(s, n)
        if notes:
            s.notes_slide.notes_text_frame.text = notes
        return s


def build() -> Path:
    d = Deck()

    d.title_slide()

    d.statement_slide(
        2, "The stakes",
        "Missing a critical patient is far worse than being cautious with a minor one.",
        "The department has seconds, not minutes. So the goal is not accuracy on average. It is being safe when the tool is unsure.",
        notes=("A triage nurse assesses one patient while three more arrive. Vague complaint, incomplete "
               "vitals, sometimes no history, decision in under two minutes. The cost is lopsided: over-triage "
               "wastes a little capacity, under-triage can kill someone. That asymmetry is the whole product."),
    )

    d.points_slide(
        3, "Why triage is genuinely hard",
        [
            ("Age changes the number", "A heart rate of 150 is an emergency in a calm adult and normal in an upset toddler. A flat adult rule set hides that, and the danger is silent."),
            ("Data is half-missing", "About half of arrivals have a history on file and half are strangers. A tool that assumes rich data fails worst on the patients it knows least."),
            ("The waiting room", "Triage is not a one-time score. People deteriorate while waiting, and that happens most when the department is busiest."),
        ],
        notes="Three forces, none of which go away with more staff or a better paper form.",
    )

    d.points_slide(
        4, "Our stance",
        [
            ("The engine decides", "Deterministic clinical logic sets the score, so a clinician can audit any decision in seconds."),
            ("The model explains", "A language model is used only at the edges: reading free text and writing a plain explanation. It never sets the acuity."),
            ("Uncertainty is a feature", "Every score ships a confidence band. Low confidence routes to a nurse instead of clearing the patient."),
        ],
        notes="A triage tool that is quietly wrong is more dangerous than one that admits it does not know.",
        kicker="Three deliberate choices",
    )

    d.agents_slide(
        5,
        notes=("Interpreter structures intake and reads vitals against age band. Adjudicator assigns the "
               "five-level acuity, runs the time-critical clocks, and explains. Watcher keeps watching the "
               "queue and ratchets up. Then switch to the running board and stay there through slide 9."),
    )

    d.demo_slide(
        6, "The board, and one patient",
        [
            "A full department, most urgent at the top. Every row has an acuity, a confidence bar, flags, and any running clock.",
            "Open a high-acuity patient: the drivers, the vitals read against this patient's age band, the clock counting down.",
            "A plain-language read written by the model but checked against the engine's level. Nothing here is a black box.",
        ],
        notes="Point at the confidence band: it tells you how sure it is.",
    )

    d.demo_slide(
        7, "The cases that break flat rules",
        [
            "Geriatric heart attack with no classic chest pain: an adult-calibrated tool can miss it. We catch it and start the clock.",
            "Febrile toddler: adult thresholds would panic or under-call. Age-banding lands it correctly.",
            "Near-zero-data walk-in: confidence drops and it routes to a nurse. It does not guess and move on.",
        ],
        notes="These are exactly the cases the brief asks for, and where age-awareness and honesty about uncertainty save someone.",
    )

    d.demo_slide(
        8, "The surge, and the catch",
        [
            "Flip to three-times arrivals: the board fills and the Watcher reallocates its attention to the highest risk.",
            "One patient arrived looking like a stable infection. While waiting, vitals drift, the Watcher re-checks and escalates them.",
            "That catch, before a human would have looked again, is the single most persuasive thing here. The Watcher never lowers an acuity.",
        ],
        notes="Watch the longest unsafe wait climb under surge. The no-de-escalation rule is enforced and tested.",
    )

    d.demo_slide(
        9, "The override, and the audit trail",
        [
            "The clinician always has the final say, in both directions. Change the level and a reason is required.",
            "It writes to an append-only trail: the engine's original score, the new one, who, why, when, and the engine version.",
            "It cannot be edited, deleted, or saved without a reason. That is what an override must record under HIPAA.",
        ],
        notes="This plus the waiting-room catch are the two moments you never cut for time.",
    )

    d.points_slide(
        10, "The model, used on purpose",
        [
            ("Two narrow jobs", "It reads a free-text note into fields the engine can score, and it writes the two-sentence explanation. Nothing else."),
            ("Guarded", "Temperature zero, cached, hard timeout. Any explanation that names the wrong level is thrown out."),
            ("Optional and cheap", "Switch it off and a rule-based parser and template take over. Fractions of a cent per patient, or zero."),
        ],
        notes="Optionally paste a free-text note live, show it parse, score and explain, then show the telemetry tick up.",
    )

    d.points_slide(
        11, "Safe by construction, compliant by design",
        [
            ("Safety is the architecture", "Escalates when unsure, clinician always in charge, every decision auditable. Shadow mode proves it before it is in the critical path."),
            ("Non-device CDS", "Designed to fit the FDA's Non-Device Clinical Decision Support category: it recommends, and it lets a clinician review the basis."),
            ("Privacy built in", "The model never sees identifiers, runs under a business agreement or on-premises, and a strict site can turn it off entirely."),
        ],
        notes="Transparency is not just a trust feature, it is the regulatory argument.",
        kicker="HIPAA (US) assumed",
    )

    d.points_slide(
        12, "The business case",
        [
            ("Fewer missed critical cases", "Under-triage is a known, expensive failure. Even a small reduction is worth more than the whole system. This is the pitch to a CMO."),
            ("Fewer walk-aways", "For a mid-size department, cutting the leave-without-being-seen rate by one point is hundreds of patients a year who stay and get care."),
            ("Cheap to run, simple to sell", "Deterministic core, optional model. Per-department SaaS tiered by size. Pilot priced low for the first outcomes study."),
        ],
        notes="Value is safety first, throughput second. Every figure is illustrative and tied to a stated assumption.",
        kicker="Illustrative, assumption-based",
    )

    d.roadmap_slide(
        13,
        notes="We go outward from safe and small. One strong outcomes study unlocks the rest of the roadmap.",
    )

    d.ask_slide(
        14,
        notes="Close on the one-liner: safe when unsure, always watching those who wait, decision stays with the clinician.",
    )

    out = Path(__file__).resolve().parents[1] / "docs" / "Sentinel_Pitch.pptx"
    d.save(out)
    return out


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}")
