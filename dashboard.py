"""
Streamlit Dashboard v2 — AI News Accident Intelligence
========================================================
Enhanced dashboard with:
  - Live database statistics & charts
  - Daily crawl status & history
  - State/district breakdown with maps
  - Manual extraction (URL/text/batch)
  - Auto-refresh for crawl monitoring
  - Trigger crawl from UI
"""

import asyncio
import io
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from config import OLLAMA_MODEL, DB_PATH, SCHEDULE_HOUR, SCHEDULE_MINUTE, OUTPUT_DIR
from ollama_client import OllamaClient
from processor import process_url, process_text
from scraper import scrape_news

st.set_page_config(
    page_title="AI Accident Intelligence",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ──────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .main-header {
        background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
        padding: 1.5rem 2rem; border-radius: 12px; margin-bottom: 1.5rem;
        color: white; text-align: center;
    }
    .main-header h1 { margin: 0; font-size: 2rem; }
    .main-header p { margin: 0.3rem 0 0 0; opacity: 0.8; font-size: 0.9rem; }
    .metric-card {
        background: #fff; border: 1px solid #e8e8e8; border-radius: 10px;
        padding: 1rem; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    .metric-card .value { font-size: 1.8rem; font-weight: 700; line-height: 1.1; }
    .metric-card .label { font-size: 0.78rem; color: #666; margin-top: 0.2rem; }
    .badge-online { color:#0f5132; background:#d1e7dd; padding:0.2rem 0.6rem; border-radius:20px; font-size:0.78rem; font-weight:600; }
    .badge-offline { color:#842029; background:#f8d7da; padding:0.2rem 0.6rem; border-radius:20px; font-size:0.78rem; font-weight:600; }
    .accident-card {
        background:#fff; border-left:4px solid #e74c3c; border-radius:8px;
        padding:1rem 1.2rem; margin-bottom:0.8rem; box-shadow:0 1px 4px rgba(0,0,0,0.06);
    }
    .accident-card h4 { margin:0 0 0.4rem 0; color:#1a1a2e; font-size:0.95rem; }
    .accident-card .detail { font-size:0.84rem; margin:0.15rem 0; color:#333; }
</style>
""", unsafe_allow_html=True)


# ── Header ───────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>🚨 AI Accident Intelligence Dashboard</h1>
    <p>Multi-agent accident data extraction • Ollama LLM • Auto-scheduled daily crawler</p>
</div>
""", unsafe_allow_html=True)


# ── Helper: get DB connection ────────────────────────────────────
def get_db():
    if not os.path.exists(DB_PATH):
        return None
    return sqlite3.connect(DB_PATH)


def query_db(sql, params=()):
    con = get_db()
    if not con:
        return pd.DataFrame()
    try:
        df = pd.read_sql_query(sql, con, params=params)
        return df
    finally:
        con.close()


# ── Sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Control Panel")

    client = OllamaClient()
    ollama_ok = client.is_available()
    if ollama_ok:
        st.markdown('<span class="badge-online">● Ollama Online</span>', unsafe_allow_html=True)
        models = client.list_models()
        default_idx = next((i for i, m in enumerate(models) if m == OLLAMA_MODEL), 0)
        selected_model = st.selectbox("Model", models, index=default_idx)
        client = OllamaClient(model=selected_model)
    else:
        st.markdown('<span class="badge-offline">● Ollama Offline</span>', unsafe_allow_html=True)
        selected_model = OLLAMA_MODEL

    st.markdown("---")
    st.markdown("### 🗓️ Scheduler")
    st.info(f"Auto-crawl daily at **{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}**")

    if st.button("🔄 Trigger Crawl Now", type="primary", use_container_width=True):
        st.session_state["trigger_crawl"] = True

    st.markdown("---")
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)
    if auto_refresh:
        st.markdown("*Dashboard refreshes every 30 seconds*")

    st.markdown("---")
    st.caption(f"Model: **{selected_model}**")
    st.caption(f"DB: `{DB_PATH}`")


# ── Full column order for display ────────────────────────────────
ALL_COLUMNS = [
    "district", "headline", "source_url", "location", "city",
    "police_station", "vehicle_type", "vehicle_number", "persons",
    "fatalities", "injuries", "date", "time", "language_detected",
    "confidence_score", "raw_text", "crawl_timestamp", "state", "crawl_date",
]

# ── Main Tabs ────────────────────────────────────────────────────
tab_dash, tab_crawl, tab_data, tab_extract, tab_history, tab_search = st.tabs([
    "📊 Dashboard", "🚀 State Crawl", "📋 All Data", "🔍 Extract", "📜 Crawl History", "🔎 Search"
])


# ━━━━━━━━  TAB 1: DASHBOARD  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_dash:
    db_exists = os.path.exists(DB_PATH)
    if not db_exists:
        st.warning("No database yet. Run a crawl first: `python agents.py` or use 'Trigger Crawl Now'")
    else:
        # Overall metrics
        stats = query_db("""
            SELECT COUNT(*) as total,
                   COALESCE(SUM(fatalities),0) as fatalities,
                   COALESCE(SUM(injuries),0) as injuries,
                   COUNT(DISTINCT state) as states,
                   COUNT(DISTINCT district) as districts,
                   COUNT(DISTINCT crawl_date) as days
            FROM accidents
        """)

        if not stats.empty:
            s = stats.iloc[0]
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            for col, val, lbl, clr in [
                (c1, int(s["total"]), "Total Records", "#3498db"),
                (c2, int(s["fatalities"]), "Fatalities", "#e74c3c"),
                (c3, int(s["injuries"]), "Injuries", "#e67e22"),
                (c4, int(s["states"]), "States", "#9b59b6"),
                (c5, int(s["districts"]), "Districts", "#27ae60"),
                (c6, int(s["days"]), "Days Crawled", "#2c3e50"),
            ]:
                with col:
                    st.markdown(f"""<div class="metric-card">
                        <div class="value" style="color:{clr};">{val}</div>
                        <div class="label">{lbl}</div>
                    </div>""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Today's summary
        today = datetime.now().strftime("%Y-%m-%d")
        today_summary = query_db("""
            SELECT state, district, COUNT(*) as accidents,
                   SUM(fatalities) as fatalities, SUM(injuries) as injuries
            FROM accidents WHERE crawl_date = ?
            GROUP BY state, district ORDER BY accidents DESC
        """, (today,))

        col_left, col_right = st.columns(2)

        with col_left:
            st.subheader(f"📅 Today's Report ({today})")
            if today_summary.empty:
                st.info("No data crawled today yet.")
            else:
                st.dataframe(today_summary, use_container_width=True, hide_index=True)

        # Today's full records (all columns)
        today_full = query_db("""
            SELECT district, headline, source_url, location, city,
                   police_station, vehicle_type, vehicle_number, persons,
                   fatalities, injuries, date, time, language_detected,
                   confidence_score, raw_text, crawl_timestamp, state, crawl_date
            FROM accidents WHERE crawl_date = ?
            ORDER BY state, district
        """, (today,))
        if not today_full.empty:
            st.subheader(f"📄 Today's Full Records ({len(today_full)})")
            st.dataframe(today_full, use_container_width=True, hide_index=True)

        with col_right:
            # State-wise bar chart
            state_df = query_db("""
                SELECT state, COUNT(*) as accidents,
                       SUM(fatalities) as fatalities, SUM(injuries) as injuries
                FROM accidents GROUP BY state ORDER BY accidents DESC
            """)
            if not state_df.empty:
                st.subheader("🏛️ State-wise Accidents")
                st.bar_chart(state_df.set_index("state")[["accidents", "fatalities", "injuries"]])

        # Daily trend
        daily_df = query_db("""
            SELECT crawl_date as date, COUNT(*) as accidents,
                   SUM(fatalities) as fatalities, SUM(injuries) as injuries
            FROM accidents GROUP BY crawl_date ORDER BY crawl_date
        """)
        if not daily_df.empty and len(daily_df) > 1:
            st.subheader("📈 Daily Trend")
            st.line_chart(daily_df.set_index("date")[["accidents", "fatalities", "injuries"]])

        # Top districts
        dist_df = query_db("""
            SELECT state, district, COUNT(*) as accidents,
                   SUM(fatalities) as fatalities
            FROM accidents GROUP BY state, district
            ORDER BY accidents DESC LIMIT 15
        """)
        if not dist_df.empty:
            st.subheader("🔝 Top 15 Accident-Prone Districts")
            st.dataframe(dist_df, use_container_width=True, hide_index=True)


# ━━━━━━━━  TAB 2: STATE CRAWL  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_crawl:
    st.subheader("🚀 Crawl a State")
    st.caption("Select a state below to crawl all its districts for accident news.")

    # Load states from JSON
    _states_file = Path(__file__).parent / "states_districts_full.json"
    if not _states_file.exists():
        st.error("states_districts_full.json not found. Run discover_states.py first.")
    else:
        with open(_states_file, encoding="utf-8") as _f:
            _all_states = json.load(_f)

        # Build display labels
        _state_options = {
            slug: f"{info['name']}  ({slug})  —  {len(info.get('districts', {}))} districts"
            for slug, info in sorted(_all_states.items(), key=lambda x: x[0])
            if len(info.get("districts", {})) > 0
        }

        col_sel, col_art = st.columns([3, 1])
        with col_sel:
            _selected_label = st.selectbox(
                "Select State",
                list(_state_options.values()),
                index=None,
                placeholder="Choose a state...",
            )
        with col_art:
            _max_articles = st.number_input(
                "Max articles / district", min_value=1, max_value=20, value=3, step=1
            )

        # Resolve slug from label
        _selected_slug = None
        if _selected_label:
            for slug, label in _state_options.items():
                if label == _selected_label:
                    _selected_slug = slug
                    break

        if _selected_slug:
            _state_info = _all_states[_selected_slug]
            _districts = _state_info.get("districts", {})
            st.info(
                f"**{_state_info['name']}** — {len(_districts)} districts: "
                f"{', '.join(d['name'] for d in list(_districts.values())[:15])}"
                f"{'...' if len(_districts) > 15 else ''}"
            )

            if st.button("🚀 Start Crawl", type="primary", key="btn_state_crawl"):
                st.session_state["state_crawl_running"] = True
                st.session_state["state_crawl_slug"] = _selected_slug
                st.session_state["state_crawl_max"] = _max_articles

        # Run crawl if triggered
        if st.session_state.get("state_crawl_running"):
            _crawl_slug = st.session_state["state_crawl_slug"]
            _crawl_max = st.session_state["state_crawl_max"]
            _crawl_info = _all_states[_crawl_slug]
            st.session_state["state_crawl_running"] = False

            with st.spinner(
                f"Crawling **{_crawl_info['name']}** "
                f"({len(_crawl_info.get('districts', {}))} districts)... "
                f"This may take several minutes."
            ):
                try:
                    from agents import OrchestratorAgent
                    _orch = OrchestratorAgent(
                        states={_crawl_slug: _crawl_info},
                        max_articles=_crawl_max,
                    )
                    _crawl_stats = asyncio.run(_orch.run([_crawl_slug]))

                    st.success(
                        f"✅ Crawl complete — "
                        f"**{_crawl_stats.get('new', 0)}** new records, "
                        f"**{_crawl_stats.get('dup', 0)}** duplicates, "
                        f"**{_crawl_stats.get('districts', 0)}** districts, "
                        f"**{len(_crawl_stats.get('errors', []))}** errors"
                    )

                    # Show metrics
                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.metric("New Records", _crawl_stats.get('new', 0))
                    mc2.metric("Duplicates", _crawl_stats.get('dup', 0))
                    mc3.metric("Districts", _crawl_stats.get('districts', 0))
                    mc4.metric("Articles Found", _crawl_stats.get('articles', 0))

                    # Show errors if any
                    if _crawl_stats.get('errors'):
                        with st.expander("⚠️ Errors", expanded=False):
                            for _err in _crawl_stats['errors']:
                                st.warning(_err)

                    # Fetch the crawled data from DB
                    _crawl_date = datetime.now().strftime("%Y-%m-%d")
                    _crawl_df = query_db("""
                        SELECT district, headline, source_url, location, city,
                               police_station, vehicle_type, vehicle_number, persons,
                               fatalities, injuries, date, time, language_detected,
                               confidence_score, raw_text, crawl_timestamp, state, crawl_date
                        FROM accidents
                        WHERE state = ? AND crawl_date = ?
                        ORDER BY district
                    """, (_crawl_info['name'], _crawl_date))

                    # Also try slug-based state name (fallback)
                    if _crawl_df.empty:
                        _crawl_df = query_db("""
                            SELECT district, headline, source_url, location, city,
                                   police_station, vehicle_type, vehicle_number, persons,
                                   fatalities, injuries, date, time, language_detected,
                                   confidence_score, raw_text, crawl_timestamp, state, crawl_date
                            FROM accidents
                            WHERE state = ? AND crawl_date = ?
                            ORDER BY district
                        """, (_crawl_slug, _crawl_date))

                    if not _crawl_df.empty:
                        st.subheader(f"📊 Results: {_crawl_info['name']} ({len(_crawl_df)} records)")
                        st.dataframe(_crawl_df, use_container_width=True, hide_index=True)

                        # Excel download
                        _xl_buffer = io.BytesIO()
                        with pd.ExcelWriter(_xl_buffer, engine="openpyxl") as _writer:
                            # Summary sheet
                            _summary = pd.DataFrame({
                                "Metric": ["State", "Total Records", "Fatalities",
                                           "Injuries", "Districts", "Crawl Date"],
                                "Value": [
                                    _crawl_info['name'],
                                    len(_crawl_df),
                                    int(_crawl_df['fatalities'].sum()) if 'fatalities' in _crawl_df.columns else 0,
                                    int(_crawl_df['injuries'].sum()) if 'injuries' in _crawl_df.columns else 0,
                                    _crawl_df['district'].nunique(),
                                    _crawl_date,
                                ],
                            })
                            _summary.to_excel(_writer, sheet_name="Summary", index=False)

                            # All data sheet
                            _crawl_df.to_excel(_writer, sheet_name="All Data", index=False)

                            # Per-district sheets
                            for _dist_name, _dist_group in _crawl_df.groupby("district"):
                                _sheet = str(_dist_name)[:31]
                                _dist_group.to_excel(_writer, sheet_name=_sheet, index=False)

                        _xl_buffer.seek(0)
                        _fname = f"{_crawl_slug}_accidents_{_crawl_date}.xlsx"

                        dl_x1, dl_x2, _ = st.columns([1, 1, 3])
                        with dl_x1:
                            st.download_button(
                                "⬇️ Download Excel",
                                _xl_buffer.getvalue(),
                                _fname,
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="dl_state_xlsx",
                            )
                        with dl_x2:
                            st.download_button(
                                "⬇️ Download CSV",
                                _crawl_df.to_csv(index=False),
                                f"{_crawl_slug}_accidents_{_crawl_date}.csv",
                                "text/csv",
                                key="dl_state_csv",
                            )
                    else:
                        st.warning("Crawl finished but no accident records were found.")

                except Exception as e:
                    st.error(f"Crawl failed: {e}")
                    import traceback
                    with st.expander("Error details"):
                        st.code(traceback.format_exc())


# ━━━━━━━━  TAB 3: ALL DATA  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_data:
    st.subheader("📋 All Accident Records")
    db_exists_data = os.path.exists(DB_PATH)
    if not db_exists_data:
        st.warning("No database yet.")
    else:
        # Filters
        filter_col1, filter_col2, filter_col3 = st.columns(3)
        with filter_col1:
            states_list = query_db("SELECT DISTINCT state FROM accidents ORDER BY state")
            state_filter = st.multiselect("Filter by State", states_list["state"].tolist() if not states_list.empty else [])
        with filter_col2:
            if state_filter:
                placeholders = ",".join(["?"] * len(state_filter))
                dists = query_db(f"SELECT DISTINCT district FROM accidents WHERE state IN ({placeholders}) ORDER BY district", tuple(state_filter))
            else:
                dists = query_db("SELECT DISTINCT district FROM accidents ORDER BY district")
            dist_filter = st.multiselect("Filter by District", dists["district"].tolist() if not dists.empty else [])
        with filter_col3:
            dates_list = query_db("SELECT DISTINCT crawl_date FROM accidents ORDER BY crawl_date DESC")
            date_filter = st.multiselect("Filter by Crawl Date", dates_list["crawl_date"].tolist() if not dates_list.empty else [])

        # Build query
        where_clauses = []
        params = []
        if state_filter:
            where_clauses.append(f"state IN ({','.join(['?']*len(state_filter))})")
            params.extend(state_filter)
        if dist_filter:
            where_clauses.append(f"district IN ({','.join(['?']*len(dist_filter))})")
            params.extend(dist_filter)
        if date_filter:
            where_clauses.append(f"crawl_date IN ({','.join(['?']*len(date_filter))})")
            params.extend(date_filter)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        all_data = query_db(f"""
            SELECT district, headline, source_url, location, city,
                   police_station, vehicle_type, vehicle_number, persons,
                   fatalities, injuries, date, time, language_detected,
                   confidence_score, raw_text, crawl_timestamp, state, crawl_date
            FROM accidents{where_sql}
            ORDER BY crawl_date DESC, state, district
        """, tuple(params))

        if all_data.empty:
            st.info("No records match the selected filters.")
        else:
            st.caption(f"Showing {len(all_data)} records")
            st.dataframe(all_data, use_container_width=True, hide_index=True)

            # Downloads
            dl1, dl2, _ = st.columns([1, 1, 3])
            with dl1:
                st.download_button(
                    "⬇️ Download CSV", all_data.to_csv(index=False),
                    "all_accidents.csv", "text/csv", key="dl_all_csv"
                )
            with dl2:
                st.download_button(
                    "⬇️ Download JSON",
                    all_data.to_json(orient="records", force_ascii=False, indent=2),
                    "all_accidents.json", "application/json", key="dl_all_json"
                )


# ━━━━━━━━  TAB 3: EXTRACT  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_extract:
    if "results" not in st.session_state:
        st.session_state.results = []

    sub_tab_url, sub_tab_text, sub_tab_batch = st.tabs(["🔗 URL", "📝 Text", "📂 Batch"])

    with sub_tab_url:
        url = st.text_input("News URL", placeholder="https://hindi.news18.com/...")
        if st.button("🔍 Extract", key="btn_url", type="primary"):
            if not ollama_ok:
                st.error("Ollama is not running.")
            elif url:
                with st.spinner("Extracting..."):
                    t0 = time.time()
                    recs = process_url(url, client=client)
                    st.session_state.results = recs
                    st.session_state.elapsed = time.time() - t0

    with sub_tab_text:
        text = st.text_area("Paste news content", height=150,
                            placeholder="ट्रक और कार की टक्कर में 2 लोगों की मौत...")
        if st.button("🔍 Extract", key="btn_text", type="primary"):
            if not ollama_ok:
                st.error("Ollama is not running.")
            elif text:
                with st.spinner("Extracting..."):
                    t0 = time.time()
                    recs = process_text(text, client=client)
                    st.session_state.results = recs
                    st.session_state.elapsed = time.time() - t0

    with sub_tab_batch:
        batch = st.text_area("URLs (one per line)", height=120)
        if st.button("🔍 Extract All", key="btn_batch", type="primary"):
            if batch and ollama_ok:
                urls = [u.strip() for u in batch.splitlines() if u.strip()]
                all_recs = []
                prog = st.progress(0)
                t0 = time.time()
                for i, u in enumerate(urls):
                    prog.progress((i + 1) / len(urls))
                    try:
                        all_recs.extend(process_url(u, client=client))
                    except Exception as e:
                        st.warning(f"Failed: {u}")
                st.session_state.results = all_recs
                st.session_state.elapsed = time.time() - t0

    # Display results
    results = st.session_state.results
    if results:
        st.markdown("---")
        total = len(results)
        fatal = sum(r.get("fatalities", 0) for r in results)
        injured = sum(r.get("injuries", 0) for r in results)
        elapsed = st.session_state.get("elapsed", 0)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Accidents", total)
        m2.metric("Fatalities", fatal)
        m3.metric("Injuries", injured)
        m4.metric("Time", f"{elapsed:.1f}s")

        view = st.radio("View", ["Cards", "Table", "JSON"], horizontal=True, label_visibility="collapsed")

        if view == "Cards":
            for i, rec in enumerate(results, 1):
                loc = ", ".join(p for p in [rec.get("location"), rec.get("city"), rec.get("state")] if p)
                st.markdown(f"""<div class="accident-card">
                    <h4>#{i} — {loc or 'Unknown'}</h4>
                    <div class="detail">🚗 {', '.join(rec.get('vehicle_type',[])) or '—'} |
                        💀 {rec.get('fatalities',0)} | 🏥 {rec.get('injuries',0)} |
                        🎯 {rec.get('confidence_score',0):.0%}</div>
                </div>""", unsafe_allow_html=True)
        elif view == "Table":
            st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
        else:
            st.code(json.dumps(results, indent=2, ensure_ascii=False), language="json")

        c1, c2, _ = st.columns([1, 1, 3])
        with c1:
            st.download_button("⬇️ JSON", json.dumps(results, indent=2, ensure_ascii=False),
                               "accidents.json", "application/json")
        with c2:
            st.download_button("⬇️ CSV", pd.DataFrame(results).to_csv(index=False),
                               "accidents.csv", "text/csv")


# ━━━━━━━━  TAB 3: CRAWL HISTORY  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_history:
    st.subheader("📜 Crawl Run History")
    runs_df = query_db("SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 30")
    if runs_df.empty:
        st.info("No crawl runs yet. Trigger a crawl from the sidebar.")
    else:
        st.dataframe(runs_df, use_container_width=True, hide_index=True)


# ━━━━━━━━  TAB 4: SEARCH  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with tab_search:
    st.subheader("🔎 Search Accident Records")
    q = st.text_input("Search", placeholder="Type keyword (e.g., truck, jaipur, NH-48)")
    if q:
        results_df = query_db("""
            SELECT district, headline, source_url, location, city,
                   police_station, vehicle_type, vehicle_number, persons,
                   fatalities, injuries, date, time, language_detected,
                   confidence_score, raw_text, crawl_timestamp, state, crawl_date
            FROM accidents
            WHERE headline LIKE ? OR location LIKE ? OR district LIKE ?
                  OR city LIKE ? OR state LIKE ? OR vehicle_type LIKE ?
                  OR raw_text LIKE ? OR persons LIKE ?
            ORDER BY crawl_date DESC LIMIT 100
        """, (f"%{q}%",)*8)
        if results_df.empty:
            st.info("No matching records.")
        else:
            st.caption(f"{len(results_df)} result(s)")
            st.dataframe(results_df, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ Download Results CSV", results_df.to_csv(index=False),
                "search_results.csv", "text/csv", key="dl_search"
            )


# ── Trigger crawl handling ───────────────────────────────────────
if st.session_state.get("trigger_crawl"):
    st.session_state["trigger_crawl"] = False
    with st.spinner("Running multi-agent crawl... This may take several minutes."):
        try:
            from agents import OrchestratorAgent
            orch = OrchestratorAgent(max_articles=3)
            stats = asyncio.run(orch.run())
            st.success(f"✅ Crawl complete: {stats.get('new', 0)} new records!")
            st.rerun()
        except Exception as e:
            st.error(f"Crawl failed: {e}")


# ── Auto-refresh ────────────────────────────────────────────────
if auto_refresh:
    time.sleep(30)
    st.rerun()


# ── Footer ───────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    "<div style='text-align:center; color:#888; font-size:0.75rem;'>"
    "AI Accident Intelligence v2 • Multi-Agent • MCP • Ollama LLM • APScheduler"
    "</div>",
    unsafe_allow_html=True,
)
