from jarvis.core.addressing import AddressContext, decide, mentions_name


def test_after_wake_and_name_accept():
    assert decide("cria um arquivo", AddressContext(after_wake=True)) == "accept"
    assert decide("ô já vis, abre o spotify", AddressContext()) == "accept"
    assert mentions_name("Jarvas faz isso", "Jarvis")


def test_reply_window_accepts_short_answers():
    assert decide("sim", AddressContext(since_assistant_s=2.0, assistant_asked=True)) == "accept"
    assert decide("sim", AddressContext(since_assistant_s=2.0, pending_request=True)) == "accept"
    assert decide("pode", AddressContext(since_assistant_s=6.0, assistant_asked=True)) == "accept"


def test_side_talk_right_after_assistant_is_only_ambiguous():
    ctx = AddressContext(since_assistant_s=2.0)
    assert decide("ele disse que vai chegar atrasado amanhã", ctx) == "ambiguous"


def test_fillers_rejected():
    assert decide("hum é", AddressContext(since_assistant_s=1.0)) == "reject"


def test_side_conversation_is_not_accepted():
    ctx = AddressContext(since_assistant_s=60)
    assert decide("ele falou que ia chegar tarde hoje", ctx) in ("reject", "ambiguous")
    assert decide("ok", ctx) != "accept"


def test_direct_command_after_a_while():
    assert decide("abre o navegador no github", AddressContext(since_assistant_s=12)) == "accept"
    assert decide("abre o navegador no github", AddressContext(since_assistant_s=120)) == "ambiguous"
