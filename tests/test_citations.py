from app.answering.citations import Citation, render_citations
from tests.factories import scored


def test_citations_come_from_metadata_in_cited_order():
    by_label = {"S1": scored(section_id="4.1", title="Night window"), "S2": scored(section_id="4.2")}
    citations = render_citations(("S2", "S1"), by_label)
    assert [c.section for c in citations] == ["4.2", "4.1"]
    assert citations[0] == Citation(
        document="Arcadia Transport Policy",
        version=2,
        section="4.2",
        title="Security escort",
        excerpt="A security escort travels on every drop after 7:30 PM with a woman employee.",
    )


def test_parts_of_one_section_are_cited_once():
    by_label = {"S1": scored(section_id="8"), "S2": scored(section_id="8")}
    assert len(render_citations(("S1", "S2"), by_label)) == 1


def test_long_excerpts_are_shortened_on_a_word_boundary():
    [citation] = render_citations(("S1",), {"S1": scored(text="word " * 200)})
    assert len(citation.excerpt) <= 281 and citation.excerpt.endswith("word…")
