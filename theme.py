"""Design tokens and injected CSS for the TaxSavers visual identity.

Pure presentation. Nothing here touches parsing, categorization, or any
business logic in core/ -- app.py imports CSS and injects it once, plus
calls render_progress() for the processing-status panel's markup.

Brand: olive green from the TaxSavers (Wilson Tax & Accounting LLC) logo
over cream, warm neutrals, dark green ink for hierarchy surfaces. Boutique
accounting register, not generic SaaS.
"""

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@400;600;700&family=Source+Sans+3:wght@400;500;600;700&display=swap');

:root {
  --ts-primary: #5C8727;
  --ts-primary-hover: #4B6F1F;
  --ts-primary-active: #3D5A18;
  --ts-primary-tint: #EEF3E4;
  --ts-ink: #2E4230;
  --ts-accent: #8A6A2F;
  --ts-bg: #F7F5EF;
  --ts-surface: #FFFFFF;
  --ts-surface-alt: #F1EEE4;
  --ts-text: #23261F;
  --ts-text-muted: #6B7065;
  --ts-border: #DCD8CB;
  --ts-border-strong: #C3BEAD;
  --ts-success: #3F7D33;  --ts-success-bg: #EAF2E6;
  --ts-warning: #B07A16;  --ts-warning-bg: #FAF2DF;
  --ts-error:   #A6382F;  --ts-error-bg:   #F8EAE7;
  --ts-r-sm: 4px; --ts-r-md: 6px; --ts-r-lg: 10px;
  --ts-s1: 4px; --ts-s2: 8px; --ts-s3: 12px; --ts-s4: 16px;
  --ts-s5: 24px; --ts-s6: 32px; --ts-s7: 48px;
  --ts-row-h: 40px;
  --ts-shadow-1: 0 1px 2px rgba(35,38,31,.06);
  --ts-shadow-2: 0 4px 12px rgba(35,38,31,.10);
}

@media (prefers-color-scheme: dark) {
  :root {
    --ts-primary: #8FB94F;
    --ts-primary-hover: #A3C868;
    --ts-primary-active: #7BA43D;
    --ts-primary-tint: #2A3320;
    --ts-ink: #22331F;
    --ts-accent: #C09A55;
    --ts-bg: #161814;
    --ts-surface: #1E211B;
    --ts-surface-alt: #262A22;
    --ts-text: #EDEBE2;
    --ts-text-muted: #9CA192;
    --ts-border: #343A2E;
    --ts-border-strong: #47503C;
    --ts-success: #6FA85C;  --ts-success-bg: #1F2A1B;
    --ts-warning: #D9A93F;  --ts-warning-bg: #2B2415;
    --ts-error:   #D9705F;  --ts-error-bg:   #2C1A17;
  }
}

/* base */
html, body, [class*="css"] { font-family: 'Source Sans 3', system-ui, sans-serif; }
.stApp { background: var(--ts-bg); color: var(--ts-text); }
.block-container { padding-top: var(--ts-s7); max-width: 1240px; }
h1, h2, h3 { font-family: 'Source Serif 4', Georgia, serif; font-weight: 600; letter-spacing: -.01em; }
h1 { font-size: 32px; }
h2 { font-size: 20px; margin-top: var(--ts-s6); }

/* sidebar */
[data-testid="stSidebar"] { background: var(--ts-ink); border-right: 1px solid var(--ts-border); }
[data-testid="stSidebar"] * { color: #F3F1E9; }
[data-testid="stSidebar"] .stTextInput input,
[data-testid="stSidebar"] .stNumberInput input {
  background: #354B36; border: 1px solid #4A6049; color: #F3F1E9; border-radius: var(--ts-r-sm);
}

/* metricas */
[data-testid="stMetric"] {
  background: var(--ts-surface);
  border: 1px solid var(--ts-border);
  border-top: 3px solid var(--ts-border-strong);
  border-radius: var(--ts-r-md);
  padding: 18px 20px;
  box-shadow: var(--ts-shadow-1);
}
[data-testid="stMetricLabel"] p {
  font-size: 12px; font-weight: 700; letter-spacing: .09em;
  text-transform: uppercase; color: var(--ts-text-muted);
}
[data-testid="stMetricValue"] {
  font-family: 'Source Serif 4', Georgia, serif;
  font-size: 36px; font-weight: 600; font-variant-numeric: tabular-nums;
  color: var(--ts-text); line-height: 1.05;
}
/* la 4a metrica (Client Questions) es la que dispara la accion */
div[data-testid="stColumn"]:nth-of-type(4) [data-testid="stMetric"] {
  background: var(--ts-primary-tint);
  border-color: #C7D6AE;
  border-top-color: var(--ts-primary);
}

/* tabs */
.stTabs [data-baseweb="tab-list"] { gap: var(--ts-s1); border-bottom: 2px solid var(--ts-border); }
.stTabs [data-baseweb="tab"] {
  height: 44px; padding: 0 18px; color: var(--ts-text-muted);
  border-radius: var(--ts-r-md) var(--ts-r-md) 0 0; font-weight: 600;
}
.stTabs [aria-selected="true"] {
  background: var(--ts-primary-tint); color: #3D5A18;
  box-shadow: inset 0 -2px 0 var(--ts-primary);
}

/* tablas y data editor */
[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"] {
  border: 1px solid var(--ts-border); border-radius: var(--ts-r-md);
  overflow: hidden; box-shadow: var(--ts-shadow-1);
}
[data-testid="stDataFrame"] [role="columnheader"] {
  background: var(--ts-ink) !important; color: #F3F1E9 !important;
  font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
}
[data-testid="stDataFrame"] [role="gridcell"] {
  font-size: 14px; font-variant-numeric: tabular-nums;
  border-bottom: 1px solid #E8E4D8;
}
[data-testid="stDataFrame"] [role="row"]:hover [role="gridcell"] { background: var(--ts-primary-tint); }

/* botones */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
  border-radius: var(--ts-r-sm); font-weight: 600; font-size: 15px;
  padding: 9px 20px; min-height: 40px; border: 1px solid transparent; transition: background .12s;
}
.stButton > button[kind="primary"], .stFormSubmitButton > button {
  background: var(--ts-primary); color: #FFFFFF;
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover {
  background: var(--ts-primary-hover);
}
.stButton > button[kind="primary"]:active { background: var(--ts-primary-active); }
.stDownloadButton > button { background: var(--ts-ink); color: #F3F1E9; }
.stDownloadButton > button:hover { background: #23331F; }
.stButton > button[kind="secondary"] {
  background: var(--ts-surface); color: var(--ts-text); border-color: var(--ts-border-strong);
}
.stButton > button[kind="secondary"]:hover { background: var(--ts-surface-alt); }
:focus-visible { outline: 2px solid var(--ts-primary); outline-offset: 2px; }

/* banners */
[data-testid="stAlert"] { border-radius: var(--ts-r-md); border: 1px solid var(--ts-border); padding: 14px 18px; }
[data-testid="stAlert"][data-baseweb="notification"] { box-shadow: none; }
div[data-testid="stAlertContentSuccess"] { background: var(--ts-success-bg); border-left: 4px solid var(--ts-success); }
div[data-testid="stAlertContentWarning"] { background: var(--ts-warning-bg); border-left: 4px solid var(--ts-warning); }
div[data-testid="stAlertContentError"]   { background: var(--ts-error-bg);   border-left: 4px solid var(--ts-error); }
div[data-testid="stAlertContentInfo"]    { background: var(--ts-surface-alt); border-left: 4px solid var(--ts-text-muted); }

/* uploader */
[data-testid="stFileUploaderDropzone"] {
  background: var(--ts-surface); border: 1.5px dashed var(--ts-border-strong);
  border-radius: var(--ts-r-lg); padding: var(--ts-s6);
}
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--ts-primary); background: var(--ts-primary-tint); }

/* panel de progreso: st.container(key="ts_progress_panel") en app.py genera
   la clase .st-key-ts_progress_panel en el div real que envuelve a los
   widgets hijos (barra de progreso incluida) -- a diferencia de un div
   suelto inyectado por st.markdown, que queda como hermano, no padre. */
.st-key-ts_progress_panel {
  background: var(--ts-ink); color: #F3F1E9;
  border-radius: var(--ts-r-lg); padding: 28px 30px;
}
.st-key-ts_progress_panel .stProgress > div > div > div { background: #8FB94F; }
.st-key-ts_progress_panel .stProgress > div > div { background: #22331F; height: 6px; border-radius: 3px; }
.ts-step { display: flex; gap: 14px; align-items: flex-start; padding-bottom: 18px; }
.ts-step-dot { width: 22px; height: 22px; border-radius: 50%; border: 2px solid #4A6049; flex: none; }
.ts-step-dot.done { background: #8FB94F; border-color: #8FB94F; color: #1D2A16; text-align: center; font-weight: 700; font-size: 13px; line-height: 18px; }
.ts-step-dot.active { border-color: #8FB94F; }
.ts-step-title { font-weight: 600; }
.ts-step-meta { font-size: 13.5px; color: #A8B8A2; }

/* expander de auditoria: secundario, sin sombra */
[data-testid="stExpander"] {
  background: var(--ts-surface-alt); border: 1px solid var(--ts-border);
  border-radius: var(--ts-r-md); box-shadow: none;
}
[data-testid="stExpander"] summary p { color: var(--ts-text-muted); font-size: 15px; font-weight: 400; }
</style>
"""

# Ordered steps shown in the processing-status panel. Key is an internal
# handle app.py uses to mark a step done/active; label is what's shown.
PROGRESS_STEPS = [
    ("extract", "Extracción de transacciones"),
    ("coverage", "Cobertura de 12 meses"),
    ("reconcile", "Reconciliación"),
    ("questions", "Generación de preguntas"),
]


def render_progress_steps(container, step_state, active_meta=""):
    """Renders the 4-step progress list inside the dark ink panel.

    step_state: {step_key: "done" | "active" | "pending"}. Any key not
    present defaults to "pending". active_meta: a short status line shown
    under whichever step is currently "active" (e.g. "Procesando 12 de 34 --
    filename.pdf") -- purely cosmetic, callers keep driving their own state.
    """
    rows = []
    for key, label in PROGRESS_STEPS:
        state = step_state.get(key, "pending")
        if state == "done":
            dot = '<div class="ts-step-dot done">✓</div>'
        elif state == "active":
            dot = '<div class="ts-step-dot active"></div>'
        else:
            dot = '<div class="ts-step-dot"></div>'
        meta = f'<div class="ts-step-meta">{active_meta}</div>' if (state == "active" and active_meta) else ""
        rows.append(f'<div class="ts-step">{dot}<div><div class="ts-step-title">{label}</div>{meta}</div></div>')
    container.markdown(
        f'<div class="ts-steps">{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )
