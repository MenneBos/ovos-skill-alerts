"""Dutch (nl-NL) alarm and timer requests: intent routing and kind.

The nl-NL intent files must route "zet een wekker om 7 uur" to create_alarm
(and timer sentences to create_timer), and the vocab must read the kind
(wekker, timer, eierwekker, ...). The intent test loads the shipped intent and
entity files into a padacioso IntentContainer (no skill boot).
"""
import re
from pathlib import Path

import pytest
from ovos_bus_client.message import Message
from padacioso import IntentContainer

from ovos_skill_alerts.util import AlertType
from ovos_skill_alerts.util.parse_utils import build_alert_from_intent

LOCALE = Path(__file__).parent.parent / "locale" / "nl-NL"
TYPED_SLOT = re.compile(r"\{[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)\}")


def _lines(path):
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


@pytest.fixture(scope="module")
def container():
    c = IntentContainer()
    for entity in sorted((LOCALE / "entity").glob("*.entity")):
        c.add_entity(entity.stem, _lines(entity))
    for intent in sorted((LOCALE / "intent").glob("*.intent")):
        # simplematch knows few slot types and rejects repeated names, so keep
        # the part after the colon as a plain wildcard slot
        c.add_intent(intent.stem, [TYPED_SLOT.sub(r"{\1}", line) for line in _lines(intent)])
    return c


ALARM_SENTENCES = [
    "zet een wekker voor 7 uur",
    "zet een wekker op 7 uur",
    "zet een wekker om 7 uur",
    "zet de wekker op half 8",
    "zet een wekker voor morgen om 7 uur",
    "wekker om 7 uur",
    "alarm om 7 uur",
    "stel een wekker in voor 7 uur",
    "kun je een wekker zetten voor 8 uur",
    "ik wil een wekker voor morgenochtend 7 uur",
    "wek me om 7 uur",
    "wek me morgen om half 7",
    "maak me wakker om 6 uur",
    "maak me om 6 uur wakker",
    "wek me over een uur",
    "zet een wekker over een uur",
]

TIMER_SENTENCES = [
    "zet een timer voor 5 minuten",
    "zet een timer op 5 minuten",
    "zet een timer van 5 minuten",
    "zet een timer van tien minuten",
    "start een timer van 10 minuten",
    "stel een timer in voor 20 minuten",
    "timer van 5 minuten",
    "zet een eierwekker op 8 minuten",
    "zet een kookwekker van 10 minuten",
    "kun je een timer zetten voor 3 minuten",
    "start een eierwekker van een kwartier",
]

TIMER_STATUS_SENTENCES = [
    "hoe lang duurt de timer nog",
    "hoeveel tijd is er nog over op de timer",
    "wat is de status van de timer",
]


@pytest.mark.parametrize("utterance", ALARM_SENTENCES)
def test_alarm_sentence_reaches_create_alarm(container, utterance):
    assert container.calc_intent(utterance).get("name") == "create_alarm"


@pytest.mark.parametrize("utterance", TIMER_SENTENCES)
def test_timer_sentence_reaches_create_timer(container, utterance):
    assert container.calc_intent(utterance).get("name") == "create_timer"


@pytest.mark.parametrize("utterance", TIMER_STATUS_SENTENCES)
def test_timer_status_sentence_reaches_timer_status(container, utterance):
    assert container.calc_intent(utterance).get("name") == "timer_status"


def _alert(utterance):
    message = Message("intent", {"utterance": utterance, "lang": "nl-NL"},
                      {"lang": "nl-NL"})
    return build_alert_from_intent(message)


@pytest.mark.parametrize("utterance", [
    "zet een eierwekker op 8 minuten",
    "zet een kookwekker van 10 minuten",
    "start een timer van 5 minuten",
])
def test_timer_words_make_a_timer(utterance):
    assert _alert(utterance).alert_type == AlertType.TIMER


@pytest.mark.parametrize("utterance", [
    "zet een wekker om 7 uur",
    "maak me om 6 uur wakker",
    "kun je me morgen om 7 uur wekken",
    "wek me over een uur",
])
def test_alarm_words_make_an_alarm(utterance):
    assert _alert(utterance).alert_type == AlertType.ALARM


# the verb must not end up in the name; "kookwekker" and "eiertimer" are the name
@pytest.mark.parametrize("utterance,name", [
    ("zet een timer voor 10 minuten voor de pasta", "pasta"),
    ("zet kookwekker op 1 minuut", "kookwekker"),
    ("zet een eiertimer op 1 minuut", "eiertimer"),
    ("zet een eierwekker van 10 minuten", "eierwekker"),
])
def test_alert_name_has_no_verb_or_kind_word(utterance, name):
    assert _alert(utterance).alert_name == name


@pytest.mark.parametrize("utterance", [
    "wek me om 7 uur",
    "maak me wakker om 6 uur",
    "kun je een wekker zetten voor 8 uur",
])
def test_alarm_name_is_the_default_name(utterance):
    # no leftover verb or pronoun: the default name is "<time> wekker"
    assert _alert(utterance).alert_name.endswith("wekker")


# two timers must be told apart by name: "stop de eiertimer" names the egg timer
@pytest.mark.parametrize("utterance,name", [
    ("stop de eiertimer", "eiertimer"),
    ("annuleer de kookwekker", "kookwekker"),
    ("zet de eiertimer uit", "eiertimer"),
    ("stop de timer", ""),
])
def test_cancel_request_names_the_timer(utterance, name):
    from ovos_skill_alerts.util.parse_utils import parse_alert_name_from_message
    message = Message("intent", {"utterance": utterance, "lang": "nl-NL"}, {"lang": "nl-NL"})
    assert parse_alert_name_from_message(message) == name
