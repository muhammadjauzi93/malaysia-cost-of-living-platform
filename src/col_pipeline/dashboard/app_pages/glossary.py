"""Glossary: the acronyms on the dashboard, and what each number means and why it matters.

The text lives in glossary.py. This page reads no tables.
"""

import streamlit as st

from col_pipeline.dashboard import glossary
from col_pipeline.dashboard.glossary import ACRONYM


def card(term: glossary.Term) -> None:
    with st.container(border=True, height="stretch"):
        heading = f"**{term.term}**"
        if term.stands_for:
            heading += f" · {term.stands_for}"
        st.markdown(heading)
        st.markdown(term.meaning)
        st.markdown(f"*Why it matters:* {term.why_it_matters}")
        st.caption("Seen on: " + ", ".join(term.pages))


def grid(terms: list[glossary.Term]) -> None:
    for start in range(0, len(terms), 2):
        columns = st.columns(2, gap="medium")
        for column, term in zip(columns, terms[start : start + 2], strict=False):
            with column:
                card(term)


st.title("Glossary")
st.caption(
    "The acronyms used on these pages, and what each number means and why it matters to the "
    "question: does a pump price change reach transport CPI, and which states feel it most?"
)

query = st.text_input(
    "Search the glossary",
    placeholder="For example: pp, awaiting, diesel",
    key="glossary_query",
    icon=":material/search:",
    width=360,
)
found = glossary.search(query)

if not found:
    st.info(f"No glossary entry mentions “{query}”.")
    st.stop()

acronyms = [t for t in found if t.kind == ACRONYM]
if acronyms:
    st.header("Acronyms, sources and units")
    grid(acronyms)

groups = glossary.by_page(found)
if groups:
    st.header("The numbers, page by page")
    for page, terms in groups.items():
        st.subheader(page)
        grid(terms)
