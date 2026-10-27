"""Try the same adopted extraction profile used by the documented CLI."""
import streamlit as st

from adopted_demo import render_live, render_recorded
from routewise.deployment import adopted_evidence

st.title('Live router')
st.markdown('Try the adopted refund-extraction profile: grounded Nano → Super with deterministic issue-date '
            'normalization. Ambiguous dates and final validation failures go to review.')
try:
    evidence = adopted_evidence()
except (FileNotFoundError, ValueError, KeyError):
    st.info('The adopted-profile evidence bundle is unavailable in this deployment.')
    st.stop()

live, recorded = st.tabs(['Run a fresh request', 'Inspect recorded examples'])
with live:
    render_live(evidence, key='adopted-live')
with recorded:
    render_recorded(evidence, key='adopted-recorded')
st.page_link('views/historical_router.py', label='Inspect the historical v1 router')
