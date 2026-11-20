import pytest

from app.answering.companies import CompanyName

GOMTI = CompanyName("gomti", "Gomti Logistics")
YAMUNA = CompanyName("yamuna", "Yamuna Software Services")


@pytest.mark.parametrize(
    "text",
    [
        "What is Gomti's no-show penalty?",
        "How long does a Gomti cab wait at the pickup point?",
        "Compare my policy with GOMTI LOGISTICS",
        "what does gomti do about weekends",
        "Tell me about Gomti.",
        "Gomti Logistics Park is where I work",  # the full name always counts
    ],
)
def test_the_company_is_named(text):
    assert GOMTI.named_in(text)


@pytest.mark.parametrize(
    "text",
    [
        "I live in Gomti Nagar, am I eligible for pickup?",
        "i live in gomti nagar, do i get a cab",
        "My pickup point is near the Gomti riverfront.",
        "Is the Gomti Bridge route allowed at night?",
        "Gomtinagar is 30 km from the office",  # not a whole word
    ],
)
def test_a_place_with_the_same_name_is_not_the_company(text):
    assert not GOMTI.named_in(text)


def test_an_expressway_is_not_the_company():
    assert not YAMUNA.named_in("Does the cab take the Yamuna Expressway after 10 PM?")
    assert YAMUNA.named_in("Does Yamuna allow weekend cabs?")
