"""Turning a Dutch alert off by name, the catch-all intent and the alert sound.

- "zet de kookwekker uit" routes to cancel_alert and only dismisses the alert
  named kookwekker; a timer started as "een kookwekker" carries that name.
- A name that matches nothing dismisses nothing and reads out every running
  alert, so the follow-up request can use the right name.
- Any other sentence with wekker/timer/alarm/alert lands on alert_catch_all,
  not on the persona, while the specific intents still win their sentences.
- The alert sound goes over the bus with the file attached and the creating
  satellite as destination, and falls back to local playback.
"""
import datetime as dt
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from ovos_bus_client.message import Message
from padacioso import IntentContainer

from ovos_skill_alerts import AlertSkill
from ovos_skill_alerts.util import AlertType
from ovos_skill_alerts.util.alert import Alert
from ovos_skill_alerts.util.parse_utils import build_alert_from_intent

LOCALE = Path(__file__).parent.parent / "locale" / "nl-NL"
TYPED_SLOT = re.compile(r"\{[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)\}")
SOUND = Path(__file__).parent.parent / "res" / "snd" / "default-timer.wav"


def _lines(path):
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


@pytest.fixture(scope="module")
def container():
    c = IntentContainer()
    for entity in sorted((LOCALE / "entity").glob("*.entity")):
        c.add_entity(entity.stem, _lines(entity))
    for intent in sorted((LOCALE / "intent").glob("*.intent")):
        c.add_intent(intent.stem, [TYPED_SLOT.sub(r"{\1}", line) for line in _lines(intent)])
    return c


@pytest.mark.parametrize("utterance", [
    "zet de kookwekker uit",
    "zet kookwekker uit",
    "stop de eierwekker",
    "stop de kooktimer",
    "zet de wekker uit",
    "zet de timer genaamd pasta uit",
    "zet de pasta timer uit",
    "annuleer de timer voor pasta",
    "kun je de kookwekker uitzetten",
])
def test_turning_off_reaches_cancel_alert(container, utterance):
    assert container.calc_intent(utterance).get("name") == "cancel_alert"


@pytest.mark.parametrize("utterance", [
    "doe iets met de wekker",
    "wekker",
    "hoe zit het met mijn timers vandaag eigenlijk",
    "ik heb een vraag over het alarm",
])
def test_other_alert_sentences_reach_the_catch_all(container, utterance):
    assert container.calc_intent(utterance).get("name") == "alert_catch_all"


@pytest.mark.parametrize("utterance, intent", [
    ("zet een timer van 5 minuten", "create_timer"),
    ("zet een kookwekker van 10 minuten", "create_timer"),
    ("zet een wekker om 7 uur", "create_alarm"),
    ("hoe lang duurt de timer nog", "timer_status"),
])
def test_specific_intents_beat_the_catch_all(container, utterance, intent):
    assert container.calc_intent(utterance).get("name") == intent


def test_unrelated_sentence_does_not_reach_the_catch_all(container):
    assert container.calc_intent("wat is de hoofdstad van frankrijk").get("name") \
        != "alert_catch_all"


@pytest.mark.parametrize("utterance, name", [
    ("zet een kookwekker van 10 minuten", "kookwekker"),
    ("start een eierwekker van 8 minuten", "eierwekker"),
])
def test_compound_kind_word_names_the_timer(utterance, name):
    alert = build_alert_from_intent(Message(
        "create_timer", {"utterance": utterance, "lang": "nl-NL"}, {"lang": "nl-NL"}))
    assert alert.alert_type == AlertType.TIMER
    assert alert.alert_name == name


@pytest.mark.parametrize("utterance", [
    "zet mijn wekker om 7 uur",
    "Zet Mijn wekker om 7 uur",
])
def test_capitalised_noise_word_does_not_name_the_alarm(utterance):
    alert = build_alert_from_intent(Message(
        "create_alarm", {"utterance": utterance, "lang": "nl-NL"}, {"lang": "nl-NL"}))
    assert "mijn" not in alert.alert_name.lower()


def _timer(name, minutes):
    return Alert.create(
        expiration=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes),
        alert_name=name, alert_type=AlertType.TIMER)


def _cancel(utterance, alerts, **slots):
    """handle_cancel_alert on a stand-in skill: pending alerts only"""
    def get_list(alert_type, disposition=None, **_):
        return list(alerts) if disposition.name == "PENDING" else []

    skill = SimpleNamespace(
        lang="nl-NL", alert_manager=SimpleNamespace(),
        _get_alerts_list=get_list, _pick_one_by_name=Mock(return_value=None),
        _dismiss_alert=Mock(), speak_dialog=Mock(), _speak_running_alerts=Mock())
    skill._resolve_requested_alert = \
        lambda message, alert_type: AlertSkill._resolve_requested_alert(
            skill, message, alert_type)
    AlertSkill.handle_cancel_alert(skill, Message(
        "cancel_alert.intent",
        dict(utterance=utterance, lang="nl-NL", **slots), {"lang": "nl-NL"}))
    return skill


def test_named_timer_is_dismissed_and_the_other_kept():
    kook, ei = _timer("kookwekker", 10), _timer("eierwekker", 5)
    skill = _cancel("zet de kookwekker uit", [ei, kook], alertkind="kookwekker")
    skill._dismiss_alert.assert_called_once_with(kook.ident, drop_dav=True)
    skill._speak_running_alerts.assert_not_called()


def test_name_slot_dismisses_the_matching_timer():
    pasta, ei = _timer("pasta", 10), _timer("eierwekker", 5)
    skill = _cancel("zet de pasta timer uit", [ei, pasta], name="pasta")
    skill._dismiss_alert.assert_called_once_with(pasta.ident, drop_dav=True)


def test_single_timer_with_another_name_is_not_dismissed():
    """'zet de kookwekker uit' must not switch off the only, other, timer"""
    skill = _cancel("zet de kookwekker uit", [_timer("eierwekker", 5)],
                    alertkind="kookwekker")
    skill._dismiss_alert.assert_not_called()
    assert skill.speak_dialog.call_args[0][0] == "error_nothing_to_cancel"
    skill._speak_running_alerts.assert_called_once()


def test_unnamed_request_with_one_timer_still_dismisses_it():
    timer = _timer("timer", 5)
    skill = _cancel("zet de timer uit", [timer], alertkind="timer")
    skill._dismiss_alert.assert_called_once_with(timer.ident, drop_dav=True)


def test_running_alerts_are_read_out_with_their_names():
    alerts = [_timer("kookwekker", 10), _timer("eierwekker", 5)]
    spoken = []
    skill = SimpleNamespace(
        lang="nl-NL",
        _get_alerts_list=lambda alert_type, disposition=None, **_:
            list(alerts) if disposition.name == "PENDING" else [],
        speak_dialog=Mock(), speak=lambda text, wait=False: spoken.append(text),
        resources=SimpleNamespace(render_dialog=lambda name, data: name))
    AlertSkill._speak_running_alerts(skill)
    assert skill.speak_dialog.call_args[0][0] == "list_alert_intro"
    assert any("kookwekker" in s for s in spoken)
    assert any("eierwekker" in s for s in spoken)


def _sound_skill(response):
    bus = SimpleNamespace(wait_for_response=Mock(return_value=response))
    return SimpleNamespace(bus=bus)


def _alert_from_satellite():
    alert = _timer("kookwekker", 0)
    alert._data["message_context"] = {"source": "satellite-keuken",
                                      "destination": "skills",
                                      "session": {"session_id": "keuken"}}
    return alert


def test_sound_goes_to_the_creating_satellite_with_the_file_attached():
    skill = _sound_skill(Message("mycroft.audio.play_sound.response"))
    assert AlertSkill._play_sound(skill, str(SOUND), _alert_from_satellite())
    request = skill.bus.wait_for_response.call_args[0][0]
    assert request.msg_type == "mycroft.audio.play_sound"
    assert request.context["destination"] == "satellite-keuken"
    assert bytes.fromhex(request.data["binary_data"]) == SOUND.read_bytes()
    assert request.data["audio_ext"] == "wav"


def test_server_rings_when_the_satellite_does_not_answer():
    """ovos-audio ignores sounds of a satellite session, the hub then rings"""
    skill = _sound_skill(None)
    skill.bus.wait_for_response.side_effect = [
        None, Message("mycroft.audio.play_sound.response")]
    assert AlertSkill._play_sound(skill, str(SOUND), _alert_from_satellite())
    first, second = [c[0][0] for c in skill.bus.wait_for_response.call_args_list]
    assert first.context["session"]["session_id"] == "keuken"
    assert second.context["session"]["session_id"] == "default"
    assert second.data["binary_data"] == first.data["binary_data"]


def test_alert_from_the_server_goes_straight_to_the_default_session():
    skill = _sound_skill(Message("mycroft.audio.play_sound.response"))
    assert AlertSkill._play_sound(skill, str(SOUND), _timer("kookwekker", 0))
    skill.bus.wait_for_response.assert_called_once()
    request = skill.bus.wait_for_response.call_args[0][0]
    assert request.context["session"]["session_id"] == "default"


def test_wait_is_bounded_by_the_sound_length():
    skill = _sound_skill(Message("mycroft.audio.play_sound.response"))
    AlertSkill._play_sound(skill, str(SOUND), _timer("kookwekker", 0))
    assert skill.bus.wait_for_response.call_args[1]["timeout"] < 30


def test_sound_falls_back_to_local_playback():
    skill = _sound_skill(None)
    process = Mock()
    with patch("ovos_skill_alerts.play_audio", return_value=process) as play:
        assert AlertSkill._play_sound(skill, str(SOUND), _alert_from_satellite())
    play.assert_called_once_with(str(SOUND))
    process.wait.assert_called_once()


def test_no_player_at_all_reports_failure_instead_of_crashing():
    skill = _sound_skill(None)
    with patch("ovos_skill_alerts.play_audio", return_value=None):
        assert AlertSkill._play_sound(skill, str(SOUND), _alert_from_satellite()) is False
