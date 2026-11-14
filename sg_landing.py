"""Public landing page for StudyGenie (white and blue): the first thing a visitor sees, before login.

It explains the project like a normal website (hero, features, how the planner works, catch-up rule,
catch-up rule) and has buttons that open the login / sign-up page. Shown by sg_auth._login_page().
light_theme() is also used by the login page so both look the same. The dashboard keeps its own theme.
"""
from __future__ import annotations

import html

LIGHT_CSS = """<style>
:root{--b9:#0B2A6F;--b7:#1546B8;--b6:#1D5FE0;--b5:#2F7BFF;--b3:#8DB8FF;--b1:#E6F0FF;--b0:#F4F8FF;--ink:#0F1B3D;--mut:#52627F;--line:#DCE6F7}
.stApp,[data-testid="stAppViewContainer"],[data-testid="stHeader"]{background:#fff!important;color:var(--ink)!important}
[data-testid="stSidebar"]{display:none}
.stApp h1,.stApp h2,.stApp h3,.stApp p,.stApp label,.stApp span,.stApp li,.stApp small{color:var(--ink)}
.block-container{max-width:1120px;padding-top:1.2rem}
/* animated background blobs */
.bg-blob{position:fixed;border-radius:50%;filter:blur(70px);opacity:.5;z-index:0;pointer-events:none;animation:sgfloat 14s ease-in-out infinite}
.bg1{width:380px;height:380px;background:#BFD8FF;top:-100px;left:-90px}
.bg2{width:320px;height:320px;background:#9CC3FF;right:-70px;top:120px;animation-delay:-5s}
.bg3{width:260px;height:260px;background:#DCEAFF;bottom:-80px;left:40%;animation-delay:-9s}
@keyframes sgfloat{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(30px,-26px) scale(1.08)}}
@keyframes sgbob{0%,100%{transform:translateY(0)}50%{transform:translateY(-12px)}}
@keyframes sgshine{to{background-position:200% center}}
@keyframes sgrise{from{opacity:0;transform:translateY(30px)}to{opacity:1;transform:none}}
@keyframes sgpulse{0%{box-shadow:0 0 0 0 rgba(24,178,107,.6)}100%{box-shadow:0 0 0 10px rgba(24,178,107,0)}}
@keyframes sgfill{from{width:0}to{width:68%}}
/* buttons */
.stButton>button,.stFormSubmitButton>button{border-radius:12px;font-weight:700;border:2px solid var(--b6);background:#fff;color:var(--b7)!important;transition:.25s}
.stButton>button:hover,.stFormSubmitButton>button:hover{background:var(--b0);transform:translateY(-3px);border-color:var(--b6);color:var(--b7)!important}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"],button[data-testid="stBaseButton-primary"]{background:linear-gradient(135deg,var(--b5),var(--b7))!important;color:#fff!important;border-color:transparent!important;box-shadow:0 8px 22px rgba(29,95,224,.35)}
.stButton>button[kind="primary"]:hover,button[data-testid="stBaseButton-primary"]:hover{transform:translateY(-3px);box-shadow:0 14px 30px rgba(29,95,224,.45)}
.stButton>button[kind="primary"] *,button[data-testid="stBaseButton-primary"] *{color:#fff!important}
/* login / sign-up forms and inputs */
div[data-testid="stForm"]{border:1px solid var(--line);border-radius:18px;padding:1.4rem;background:#fff;box-shadow:0 10px 30px rgba(29,95,224,.14)}
.stTextInput input{background:#fff!important;color:var(--ink)!important;border:1px solid var(--line)!important;border-radius:10px}
.stTextInput input:focus{border-color:var(--b6)!important;box-shadow:0 0 0 3px rgba(47,123,255,.2)!important}
.stTabs [data-baseweb="tab"]{color:var(--mut)}.stTabs [aria-selected="true"]{color:var(--b7)!important}
.stTabs [data-baseweb="tab-highlight"]{background:var(--b6)}
/* page blocks */
.lp-brand{font-size:1.5rem;font-weight:800;color:var(--b7)}
.lp-hero{text-align:center;padding:2.6rem 1rem 1rem;animation:sgrise .8s both}
.lp-badge{display:inline-flex;align-items:center;gap:8px;padding:5px 16px;border-radius:999px;background:#fff;border:1px solid var(--line);color:var(--b7);font-size:.85rem;font-weight:700;box-shadow:0 6px 18px rgba(29,95,224,.12)}
.lp-dot{width:8px;height:8px;border-radius:50%;background:#18B26B;animation:sgpulse 1.6s infinite}
.lp-h1{font-size:3.3rem;font-weight:800;line-height:1.1;margin:.9rem 0 .8rem;letter-spacing:-.02em;color:var(--ink)}
.lp-h1 span{background:linear-gradient(90deg,var(--b5),var(--b9),var(--b5));background-size:200% auto;-webkit-background-clip:text;background-clip:text;color:transparent!important;animation:sgshine 5s linear infinite}
.lp-lead{font-size:1.15rem;color:var(--mut)!important;max-width:700px;margin:0 auto 1.2rem}
.lp-strip{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin:1.1rem 0 .6rem;animation:sgrise 1s both}
.lp-pill{padding:7px 16px;border-radius:999px;background:var(--b0);border:1px solid var(--line);font-size:.88rem;font-weight:600;color:var(--b7)}
.lp-mock{max-width:520px;margin:3rem auto 0;background:#fff;border:1px solid var(--line);border-radius:22px;padding:18px 20px;box-shadow:0 22px 50px rgba(29,95,224,.22);animation:sgbob 6s ease-in-out infinite;position:relative}
.lp-mock h5{margin:.2rem 0 .6rem;color:var(--mut);font-size:.9rem}
.lp-sess{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-radius:12px;background:var(--b0);border:1px solid var(--line);margin-bottom:8px;font-size:.93rem;transition:.25s}
.lp-sess:hover{transform:translateX(6px);border-color:var(--b3)}
.lp-tag{padding:3px 11px;border-radius:999px;font-size:.75rem;font-weight:700;background:var(--b1);color:var(--b7)}
.lp-tag.skip{background:#FFF0D6;color:#A86400}.lp-tag.done{background:#DDF6EA;color:#0E8A52}
.lp-bar{height:10px;border-radius:99px;background:var(--b1);overflow:hidden;margin-top:10px}
.lp-bar i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--b5),var(--b7));animation:sgfill 1.8s .6s cubic-bezier(.2,.8,.2,1) both}
.lp-float{position:absolute;background:#fff;border:1px solid var(--line);border-radius:14px;padding:8px 14px;font-weight:700;font-size:.85rem;box-shadow:0 10px 30px rgba(29,95,224,.14);animation:sgbob 5s ease-in-out infinite}
.lp-eye{display:block;text-align:center;color:var(--b6);font-weight:800;letter-spacing:.12em;font-size:.78rem;text-transform:uppercase;margin-top:3.2rem}
.lp-h2{font-size:2.1rem;font-weight:800;text-align:center;margin:.3rem 0 .4rem;color:var(--ink)}
.lp-sub{text-align:center;color:var(--mut)!important;max-width:680px;margin:0 auto 1.6rem}
.lp-card{height:100%;padding:1.3rem 1.4rem;border-radius:20px;border:1px solid var(--line);background:#fff;margin-bottom:14px;transition:.35s;position:relative;overflow:hidden;animation:sgrise .8s both}
.lp-card::before{content:"";position:absolute;inset:0 0 auto 0;height:4px;background:linear-gradient(90deg,var(--b5),var(--b9));transform:scaleX(0);transform-origin:left;transition:.4s}
.lp-card:hover{transform:translateY(-8px);box-shadow:0 22px 50px rgba(29,95,224,.22);border-color:var(--b3)}
.lp-card:hover::before{transform:scaleX(1)}
.lp-card .ic{width:52px;height:52px;border-radius:15px;background:var(--b1);display:grid;place-items:center;font-size:1.6rem;margin-bottom:.8rem;transition:.35s}
.lp-card:hover .ic{background:var(--b6);transform:rotate(-8deg) scale(1.08)}
.lp-card b{display:block;font-size:1.08rem;margin-bottom:.25rem;color:var(--ink)}
.lp-card p{margin:0;color:var(--mut)!important;font-size:.94rem;line-height:1.5}
.lp-formula{max-width:860px;margin:0 auto 1.4rem;text-align:center;padding:1.4rem;border-radius:20px;background:linear-gradient(135deg,var(--b9),var(--b7));box-shadow:0 22px 50px rgba(29,95,224,.22)}
.lp-formula code{font-family:Consolas,monospace;font-size:1.2rem;color:#fff!important;background:none}
.lp-formula small{display:block;color:#CFE0FF!important;margin-top:6px}
.lp-step{display:flex;gap:18px;align-items:flex-start;padding:.8rem 0;animation:sgrise .8s both}
.lp-num{flex:0 0 48px;height:48px;border-radius:50%;background:linear-gradient(135deg,var(--b5),var(--b7));color:#fff!important;font-weight:800;display:flex;align-items:center;justify-content:center;box-shadow:0 6px 18px rgba(29,95,224,.35);transition:.3s}
.lp-step:hover .lp-num{transform:scale(1.15)}
.lp-step p{margin:0;color:var(--mut)!important}
.lp-cmp{display:grid;grid-template-columns:1fr 1fr;gap:14px;max-width:640px;margin:0 auto}
.lp-col{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 10px 30px rgba(29,95,224,.14)}
.lp-col.good{border-color:var(--b5);background:var(--b0)}
.lp-col h6{margin:0 0 10px;font-size:.8rem;color:var(--mut)}
.lp-row{padding:8px 12px;border-radius:10px;background:var(--b1);margin-bottom:7px;font-size:.88rem;font-weight:600}
.lp-row.hl{background:var(--b6);color:#fff!important}
.lp-tags{text-align:center}
.lp-tag2{display:inline-block;margin:5px 6px;padding:9px 18px;border-radius:999px;background:#fff;border:1px solid var(--line);color:var(--b7);font-weight:700;font-size:.9rem;transition:.25s;box-shadow:0 4px 12px rgba(29,95,224,.08)}
.lp-tag2:hover{background:var(--b6);color:#fff;transform:translateY(-4px)}
.lp-final{margin-top:3rem;background:linear-gradient(135deg,var(--b9),var(--b6));border-radius:26px;padding:2.6rem 1rem 1.2rem;text-align:center;box-shadow:0 22px 50px rgba(29,95,224,.22)}
.lp-final h2,.lp-final p{color:#fff!important}.lp-final p{opacity:.9}
.lp-foot{text-align:center;color:var(--mut)!important;font-size:.85rem;margin:2.4rem 0 1rem;padding-top:1rem;border-top:1px solid var(--line)}
@media(max-width:800px){.lp-h1{font-size:2.3rem}.lp-cmp{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>"""

BLOBS = '<div class="bg-blob bg1"></div><div class="bg-blob bg2"></div><div class="bg-blob bg3"></div>'

FEATURES = [
    ("🧠", "Rule-based planner", "A fixed, explainable formula scores every topic. The same input always gives the same plan, with no training data."),
    ("📅", "Day-by-day schedule", "Study blocks of 45 minutes fill your daily hours, mixed across subjects, and never after the topic's exam."),
    ("🔁", "Catch-up first", "Skip a topic and the next plan finishes it before any new topic starts. Nothing gets lost."),
    ("📈", "Progress tracking", "Tick off or skip sessions, see streaks, exam countdowns and whether you are on track."),
    ("🔒", "Private for every student", "Each student has their own planner file. Students from different colleges never see each other's data."),
    ("🤖", "Ask StudyGenie (optional)", "An AI coach answers questions about your own plan. It only explains; the plan itself is never made by AI."),
]

FACTORS = [
    ("⚖️", "Weight", "Average of subject and topic difficulty. Harder topics get more time."),
    ("⏰", "Urgency", "Rises sharply as the subject's exam gets closer."),
    ("📉", "Remaining", "Topics you have already studied get lower priority."),
    ("🎨", "Variety", "A topic already planned that day is halved, so days stay mixed."),
]

STEPS = [
    ("Add your data", "Subjects, topics with difficulty, exam dates, and how many hours you can study each day."),
    ("Generate the plan", "The engine scores each pending topic and fills every day until the time is used. If time is short, it warns you."),
    ("Study and tick off", "Mark each block done or skipped. Skipped topics become a priority for the next plan."),
    ("Watch your progress", "The dashboard shows progress per subject, your streak and the next exam countdown."),
]



def light_theme(st) -> None:
    """White and blue look with floating background blobs (landing and login pages only)."""
    st.markdown(LIGHT_CSS + BLOBS, unsafe_allow_html=True)


def _card(icon: str, title: str, text: str) -> str:
    return (f'<div class="lp-card"><div class="ic">{icon}</div><b>{html.escape(title)}</b>'
            f"<p>{html.escape(text)}</p></div>")


def _enter(st) -> None:
    st.session_state["sg_enter"] = True
    st.rerun()


def render(st, first_time: bool = False) -> None:
    """Draw the landing page. first_time=True when no account exists yet (the first sign-up becomes admin)."""
    light_theme(st)
    top_l, top_r1, top_r2 = st.columns([5, 1.2, 1.2])
    top_l.markdown('<span class="lp-brand">🎓 StudyGenie</span>', unsafe_allow_html=True)
    if top_r1.button("Log in", key="lp_login_top", width="stretch", disabled=first_time):
        _enter(st)
    if top_r2.button("Sign up", key="lp_signup_top", type="primary", width="stretch"):
        _enter(st)

    st.markdown(
        '<div class="lp-hero"><span class="lp-badge"><span class="lp-dot"></span>Rule-based study planner</span>'
        '<div class="lp-h1">Know <span>what to study,</span> when and for how long</div>'
        '<div class="lp-lead">StudyGenie turns your subjects, topics and exam dates into a clear day-by-day study plan, '
        "using a transparent formula you can explain. No black box, no guessing.</div></div>",
        unsafe_allow_html=True,
    )
    _, c1, c2, _ = st.columns([2, 1.4, 1.4, 2])
    if c1.button("Get started free →", key="lp_start", type="primary", width="stretch"):
        _enter(st)
    if c2.button("I have an account", key="lp_have", width="stretch", disabled=first_time):
        _enter(st)
    if first_time:
        st.caption("First-time setup: the first account created becomes the administrator.")

    pills = ["No AI in the planning", "Same input, same plan", "Private planner per student", "Works offline with SQLite"]
    st.markdown('<div class="lp-strip">' + "".join(f'<span class="lp-pill">✓ {p}</span>' for p in pills) + "</div>",
                unsafe_allow_html=True)
    st.markdown(
        '<div class="lp-mock"><span class="lp-float" style="top:-16px;right:-16px;color:#1546B8">🔥 5-day streak</span>'
        '<span class="lp-float" style="bottom:-16px;left:-16px;color:#0E8A52;animation-delay:-2.5s">✅ On track</span>'
        "<h5>Today · Study plan</h5>"
        '<div class="lp-sess"><span><b>Maths · Calculus</b> · 45 min</span><span class="lp-tag skip">Catch-up first</span></div>'
        '<div class="lp-sess"><span><b>Physics · Optics</b> · 45 min</span><span class="lp-tag">Planned</span></div>'
        '<div class="lp-sess"><span><b>History · World War 2</b> · 30 min</span><span class="lp-tag done">Completed</span></div>'
        '<h5 style="margin-top:12px">Exam readiness</h5><div class="lp-bar"><i></i></div></div>',
        unsafe_allow_html=True,
    )

    st.markdown('<span class="lp-eye">Features</span><div class="lp-h2">Everything a student needs to plan and stick to it</div>'
                '<div class="lp-sub">Students with many subjects and one exam season struggle to decide what to study first. '
                "StudyGenie answers three questions: <b>what, when and how long</b>.</div>", unsafe_allow_html=True)
    for row in range(0, len(FEATURES), 3):
        for col, feat in zip(st.columns(3), FEATURES[row:row + 3], strict=False):
            col.markdown(_card(*feat), unsafe_allow_html=True)

    st.markdown('<span class="lp-eye">The engine</span><div class="lp-h2">How the planner thinks</div>'
                '<div class="lp-sub">Every pending topic gets a score for each study block. The highest score wins the block, '
                "until the day's time is full.</div>"
                '<div class="lp-formula"><code>score = weight × urgency × (0.3 + remaining ÷ estimated) × variety</code>'
                "<small>Fixed rules, so the result is always explainable</small></div>", unsafe_allow_html=True)
    for col, f in zip(st.columns(4), FACTORS, strict=True):
        col.markdown(_card(*f), unsafe_allow_html=True)

    st.markdown('<span class="lp-eye">How it works</span><div class="lp-h2">From data to plan in four steps</div>',
                unsafe_allow_html=True)
    steps = "".join(f'<div class="lp-step"><div class="lp-num">{i}</div><div><b>{html.escape(t)}</b>'
                    f"<p>{html.escape(d)}</p></div></div>" for i, (t, d) in enumerate(STEPS, 1))
    st.markdown(f'<div style="max-width:760px;margin:0 auto">{steps}</div>', unsafe_allow_html=True)

    st.markdown('<span class="lp-eye">Smart recovery</span><div class="lp-h2">Skipped a topic? It comes first.</div>'
                '<div class="lp-sub">StudyGenie remembers how many minutes are still owed and finishes those topics '
                "before starting anything new, so no topic is quietly dropped.</div>"
                '<div class="lp-cmp"><div class="lp-col"><h6>WITHOUT CATCH-UP</h6><div class="lp-row">History · 45 min</div>'
                '<div class="lp-row">Algebra · 15 min</div><small>Skipped Algebra waits</small></div>'
                '<div class="lp-col good"><h6>WITH CATCH-UP</h6><div class="lp-row hl">Algebra · 45 min</div>'
                '<div class="lp-row">History · 15 min</div><small>Skipped Algebra goes first</small></div></div>',
                unsafe_allow_html=True)

    st.markdown('<div class="lp-final"><h2 style="color:#fff">Ready to plan smarter?</h2>'
                "<p>Create your account and get your first study plan in minutes.</p></div>", unsafe_allow_html=True)
    _, c3, _ = st.columns([2, 1.4, 2])
    if c3.button("Create your account →", key="lp_start_bottom", type="primary", width="stretch"):
        _enter(st)
    st.markdown('<div class="lp-foot">StudyGenie · Python, SQLite and Streamlit · Rule-based planning, no AI in the schedule</div>',
                unsafe_allow_html=True)