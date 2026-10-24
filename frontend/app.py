"""Chill at My Orchard — the app's entry point. Talks to the backend API only.

This file just lists the pages; each page lives in views/. st.navigation builds the
sidebar menu from this list, with the names a grower would ask.
"""
import streamlit as st

st.set_page_config(page_title="Chill at My Orchard", page_icon="🌳", layout="wide")

pages = [
    st.Page("views/my_orchard.py", title="My orchard", icon="🌳", default=True),
    st.Page("views/trust.py", title="Can I trust it?", icon="🔍"),
    st.Page("views/station_check.py", title="Is my station working?", icon="🛠️"),
    st.Page("views/compare.py", title="Best data near me", icon="📍"),
]
st.navigation(pages).run()
