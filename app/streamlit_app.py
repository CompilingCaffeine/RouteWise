import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

st.set_page_config(page_title="RouteWise", page_icon=":material/alt_route:", layout="wide")

pages = [
    st.Page("views/overview.py", title="Overview", icon=":material/home:", default=True),
    st.Page("views/benchmark.py", title="Benchmark", icon=":material/leaderboard:"),
    st.Page("views/live_router.py", title="Live router", icon=":material/alt_route:"),
    st.Page("views/evaluation.py", title="Held-out evaluation", icon=":material/fact_check:"),
    st.Page("views/experiments.py", title="Experiments", icon=":material/science:"),
]
st.navigation(pages).run()
