"""Look and feel. Pure presentation: nothing here touches core/.

Made for people who don't want to learn software: one typeface built for
easy reading (Atkinson Hyperlegible Next, designed for low-vision
readers), comfortable text (15px), strong contrast, clear buttons, one step on
screen at a time, and color used only to say "done", "needs you", or
"problem". A single light theme on purpose: the colors are painted
explicitly so the page looks the same on any computer.

app.py injects CSS once and uses the small HTML helpers below.
"""
import html

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Next:wght@400;600;700;800&display=swap');

:root {
  --ts-ground: #F3F6F3;
  --ts-surface: #FFFFFF;
  --ts-surface-soft: #EAF0EC;
  --ts-ink: #12352E;            /* sidebar, reading panel */
  --ts-ink-soft: #1C4A40;
  --ts-primary: #1D6F52;        /* buttons, "done" */
  --ts-primary-hover: #165A42;
  --ts-primary-tint: #E2F0E9;
  --ts-text: #17211E;
  --ts-text-muted: #45534D;
  --ts-border: #D3DDD7;
  --ts-border-strong: #A9B8B0;
  --ts-done: #1D6F52;     --ts-done-bg: #E2F0E9;
  --ts-attention: #8A4B00; --ts-attention-bg: #FFF1D6; --ts-attention-line: #E19A2B;
  --ts-problem: #A3241A;  --ts-problem-bg: #FCEBE8;  --ts-problem-line: #D9534A;
  --ts-r-sm: 8px; --ts-r-md: 12px; --ts-r-lg: 18px;
  --ts-shadow: 0 1px 2px rgba(18,53,46,.06), 0 6px 20px rgba(18,53,46,.06);
}

/* base: one readable face, compact but comfortable text */
html, body, .stApp, [class*="css"], button, input, textarea, select {
  font-family: 'Atkinson Hyperlegible Next', 'Segoe UI', system-ui, sans-serif;
}
.stApp { background: var(--ts-ground); color: var(--ts-text); }
.stApp p, .stApp li, .stApp label, .stApp input { font-size: 15px; line-height: 1.5; }
.block-container { padding-top: 56px; max-width: 1120px; }
h1, h2, h3, h4 { font-family: 'Atkinson Hyperlegible Next', system-ui, sans-serif; font-weight: 800;
  letter-spacing: -.01em; color: var(--ts-text); text-wrap: balance; }
/* Streamlit sets its own heading sizes with stronger selectors; override them. */
.stApp h1 { font-size: 26px !important; padding: 4px 0 2px !important; }
.stApp h2 { font-size: 21px !important; padding: 4px 0 2px !important; }
.stApp h3 { font-size: 18px !important; padding: 4px 0 2px !important; }
[data-testid="stCaptionContainer"] p { font-size: 13px; color: var(--ts-text-muted); }

/* sidebar */
[data-testid="stSidebar"] { background: var(--ts-ink); }
[data-testid="stSidebar"] * { color: #F1F6F3; }
.stApp [data-testid="stSidebar"] h2 { font-size: 17px !important; margin-top: 6px; }
[data-testid="stSidebar"] .stTextInput input,
[data-testid="stSidebar"] .stNumberInput input {
  background: var(--ts-ink-soft); border: 1px solid #3C6B5F; color: #F1F6F3;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div { background: var(--ts-ink-soft); border-color: #3C6B5F; }
[data-testid="stSidebar"] .stButton > button[kind="secondary"],
[data-testid="stSidebar"] .stButton > button[kind="secondary"] *,
[data-testid="stSidebar"] [data-testid="stPopover"] button,
[data-testid="stSidebar"] [data-testid="stPopover"] button * {
  color: var(--ts-text); background: #F1F6F3;
}
[data-testid="stSidebar"] [data-testid="stExpander"] { background: var(--ts-ink-soft); border-color: #3C6B5F; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary p { color: #F1F6F3; }
[data-testid="stPopoverBody"] *, [data-testid="stPopoverBody"] p { color: var(--ts-text); }

/* buttons: clear, plain */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
[data-testid="stPopover"] > div > button {
  border-radius: var(--ts-r-sm); font-weight: 700; min-height: 38px; padding: 6px 16px;
  transition: background .15s ease, transform .1s ease;
}
.stButton > button p, .stDownloadButton > button p, [data-testid="stPopover"] button p { font-size: 15px !important; font-weight: 700 !important; }
.stButton > button[kind="primary"], .stDownloadButton > button {
  background: var(--ts-primary); border: 1px solid var(--ts-primary); color: #FFFFFF;
}
.stButton > button[kind="primary"] *, .stDownloadButton > button * { color: #FFFFFF; }
.stButton > button[kind="primary"]:hover, .stDownloadButton > button:hover { background: var(--ts-primary-hover); }
.stButton > button:active, .stDownloadButton > button:active { transform: translateY(1px); }
.stButton > button[kind="secondary"] { background: var(--ts-surface); border: 1.5px solid var(--ts-border-strong); color: var(--ts-text); }
.stButton > button[kind="secondary"]:hover { background: var(--ts-surface-soft); border-color: var(--ts-primary); }
.stButton > button:disabled { opacity: .45; }
:focus-visible { outline: 3px solid #E19A2B !important; outline-offset: 2px; }

/* the step picker: four buttons in a row */
.st-key-ts_steps [data-testid="stButtonGroup"] { width: 100%; }
.st-key-ts_steps [data-testid="stButtonGroup"] > div { display: grid !important;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; width: 100%; }
.st-key-ts_steps button {
  min-height: 46px; width: 100%; justify-content: flex-start; text-align: left;
  border-radius: var(--ts-r-md) !important; border: 1.5px solid var(--ts-border) !important;
  background: var(--ts-surface) !important; box-shadow: var(--ts-shadow); padding: 6px 12px !important;
}
.st-key-ts_steps button p { font-size: 15px !important; font-weight: 700; color: var(--ts-text) !important;
  white-space: normal !important; overflow: visible !important; text-overflow: clip !important; text-align: center; }
.st-key-ts_steps button[kind="segmented_controlActive"], .st-key-ts_steps button[aria-checked="true"] {
  background: var(--ts-ink) !important; border-color: var(--ts-ink) !important;
}
.st-key-ts_steps button[kind="segmented_controlActive"] p, .st-key-ts_steps button[aria-checked="true"] p { color: #FFFFFF !important; }

/* "What to do next" card */
.ts-next { display: flex; gap: 14px; align-items: flex-start; background: var(--ts-surface);
  border: 1.5px solid var(--ts-border); border-radius: var(--ts-r-lg); padding: 14px 18px;
  box-shadow: var(--ts-shadow); }
.ts-next-icon { flex: none; width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center;
  font-size: 18px; font-weight: 800; }
.ts-next.done .ts-next-icon { background: var(--ts-done-bg); color: var(--ts-done); }
.ts-next.attention .ts-next-icon { background: var(--ts-attention-bg); color: var(--ts-attention); }
.ts-next.problem .ts-next-icon { background: var(--ts-problem-bg); color: var(--ts-problem); }
.ts-next.attention { border-color: var(--ts-attention-line); }
.ts-next.problem { border-color: var(--ts-problem-line); }
.ts-next-label { font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--ts-text-muted); }
.ts-next-title { font-size: 17px; font-weight: 800; line-height: 1.3; margin-top: 2px; }
.ts-next-text { font-size: 15px; color: var(--ts-text-muted); margin-top: 4px; }

/* number cards (replace st.metric so big amounts never get cut off) */
.ts-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin: 4px 0 14px; }
.ts-card { background: var(--ts-surface); border: 1.5px solid var(--ts-border); border-radius: var(--ts-r-md);
  padding: 12px 16px; box-shadow: var(--ts-shadow); }
.ts-card-label { font-size: 13px; font-weight: 700; color: var(--ts-text-muted); }
.ts-card-value { font-size: clamp(20px, 2.4vw, 26px); font-weight: 800; font-variant-numeric: tabular-nums;
  line-height: 1.15; margin-top: 6px; overflow-wrap: anywhere; }
.ts-card-note { font-size: 12px; color: var(--ts-text-muted); margin-top: 4px; }
.ts-card.highlight { background: var(--ts-ink); border-color: var(--ts-ink); }
.ts-card.highlight .ts-card-label, .ts-card.highlight .ts-card-note { color: #BFD6CC; }
.ts-card.highlight .ts-card-value { color: #FFFFFF; }

/* status pills inside text */
.ts-pill { display: inline-block; padding: 1px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }
.ts-pill.done { background: var(--ts-done-bg); color: var(--ts-done); }
.ts-pill.attention { background: var(--ts-attention-bg); color: var(--ts-attention); }
.ts-pill.problem { background: var(--ts-problem-bg); color: var(--ts-problem); }

/* banners: Streamlit's own alert colors, just rounder */
[data-testid="stAlert"] { border-radius: var(--ts-r-md); }

/* tables */
[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"] {
  border: 1.5px solid var(--ts-border); border-radius: var(--ts-r-md); overflow: hidden;
}

/* uploader: a friendly drop area */
[data-testid="stFileUploaderDropzone"] {
  background: var(--ts-surface); border: 2px dashed var(--ts-border-strong);
  border-radius: var(--ts-r-lg); padding: 22px 20px; transition: background .15s, border-color .15s;
}
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--ts-primary); background: var(--ts-primary-tint); }

/* expanders: quiet "more detail" boxes */
[data-testid="stExpander"] { background: var(--ts-surface); border: 1.5px solid var(--ts-border); border-radius: var(--ts-r-md); }
[data-testid="stExpander"] summary p { font-size: 15px; font-weight: 700; }

/* tabs (only used inside the experts' detail box) */
.stTabs [data-baseweb="tab"] p { font-size: 14px; font-weight: 700; }

/* reading panel */
.st-key-ts_progress_panel { background: var(--ts-ink); border-radius: var(--ts-r-lg); padding: 20px 22px; }
.st-key-ts_progress_panel * { color: #F1F6F3; }
.st-key-ts_progress_panel .stProgress > div > div { background: #0B2621; height: 12px; border-radius: 6px; }
.st-key-ts_progress_panel .stProgress > div > div > div { background: #6FD3A8; border-radius: 6px; }
.st-key-ts_progress_panel .stButton > button { background: #F1F6F3; color: var(--ts-text); border: none; }
.st-key-ts_progress_panel .stButton > button * { color: var(--ts-text); }
.ts-reading { display: flex; gap: 16px; align-items: center; }
.ts-reading-dot { flex: none; width: 14px; height: 14px; border-radius: 50%; background: #6FD3A8;
  animation: ts-pulse 1.4s ease-in-out infinite; }
.ts-reading-title { font-size: 20px; font-weight: 800; }
.ts-reading-meta { font-size: 15px; color: #BFD6CC; margin-top: 2px; }

/* While reading, Streamlit greys out the previous page's elements. Keep the
   reading panel (and its Stop button) fully visible and hide the rest. */
[data-testid="stMain"]:has(.st-key-ts_progress_panel) [data-stale="true"]:not(:has(.st-key-ts_progress_panel)):not(.st-key-ts_progress_panel *) { display: none; }
.st-key-ts_progress_panel [data-stale="true"], [data-stale="true"]:has(.st-key-ts_progress_panel) { opacity: 1 !important; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color: #BFD6CC; }

/* The only animation: a gently pulsing dot while files are being read.
   (Cards are redrawn on every click, so animating them would flicker.) */
@keyframes ts-pulse { 0%, 100% { transform: scale(1); opacity: 1; } 50% { transform: scale(.65); opacity: .55; } }
@media (prefers-reduced-motion: reduce) { .ts-reading-dot { animation: none !important; } }

/* password eye: "Show" / "Hide" on hover (its aria-label flips) */
[data-testid="stTextInputRootElement"] button[aria-label$="password"] { position: relative; }
[data-testid="stTextInputRootElement"] button[aria-label$="password"]:hover::after,
[data-testid="stTextInputRootElement"] button[aria-label$="password"]:focus-visible::after {
  position: absolute; top: 50%; right: calc(100% + 4px); transform: translateY(-50%);
  padding: 3px 8px; border-radius: 6px; background: var(--ts-ink); color: #FFFFFF;
  font-size: 13px; font-weight: 700; line-height: 1.3; white-space: nowrap; pointer-events: none; z-index: 1000;
}
[data-testid="stTextInputRootElement"] button[aria-label="Show password"]:hover::after,
[data-testid="stTextInputRootElement"] button[aria-label="Show password"]:focus-visible::after { content: "Show"; }
[data-testid="stTextInputRootElement"] button[aria-label="Hide password"]:hover::after,
[data-testid="stTextInputRootElement"] button[aria-label="Hide password"]:focus-visible::after { content: "Hide"; }
</style>
"""

ICONS = {"done": "✓", "attention": "!", "problem": "!"}


def _e(text) -> str:
    return html.escape(str(text))


def next_step_html(tone: str, title: str, text: str = "") -> str:
    """The "What to do next" card. tone: done | attention | problem."""
    body = f'<div class="ts-next-text">{_e(text)}</div>' if text else ""
    return (f'<div class="ts-next {tone}" role="status"><div class="ts-next-icon">{ICONS.get(tone, "•")}</div>'
            f'<div><div class="ts-next-label">What to do next</div>'
            f'<div class="ts-next-title">{_e(title)}</div>{body}</div></div>')


def cards_html(cards) -> str:
    """cards: [(label, value, note, highlight)]"""
    parts = []
    for label, value, note, highlight in cards:
        note_html = f'<div class="ts-card-note">{_e(note)}</div>' if note else ""
        parts.append(f'<div class="ts-card{" highlight" if highlight else ""}"><div class="ts-card-label">{_e(label)}</div>'
                     f'<div class="ts-card-value">{_e(value)}</div>{note_html}</div>')
    return f'<div class="ts-cards">{"".join(parts)}</div>'


def reading_html(done: int, total: int) -> str:
    return ('<div class="ts-reading"><div class="ts-reading-dot"></div><div>'
            '<div class="ts-reading-title">Reading your files…</div>'
            f'<div class="ts-reading-meta">{done} of {total} parts read and saved. '
            'You can stop any time; nothing read so far is lost.</div>'
            '</div></div>')
