import importlib
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

import ui  # noqa: E402
import adopted_demo  # noqa: E402

# Streamlit Cloud pulls new commits without restarting the process; reload the shared
# helpers so pages never run against a stale copy.
importlib.reload(ui)
importlib.reload(adopted_demo)

st.set_page_config(page_title="RouteWise", page_icon=":material/alt_route:", layout="wide")

pages = [
    st.Page("views/start.py", title="Start here", icon=":material/home:", default=True),
    st.Page("views/workload.py", title="Workload optimizer · M5", icon=":material/tune:"),
    st.Page("views/live_router.py", title="Live router", icon=":material/alt_route:"),
    st.Page("views/overview.py", title="Evidence archive", icon=":material/folder_open:"),
    st.Page("views/benchmark.py", title="Benchmark", icon=":material/leaderboard:"),
    st.Page("views/historical_router.py", title="Historical router · v1", icon=":material/history:"),
    st.Page("views/evaluation.py", title="Held-out evaluation", icon=":material/fact_check:"),
    st.Page("views/experiments.py", title="Experiments", icon=":material/science:"),
]
st.navigation(pages).run()
