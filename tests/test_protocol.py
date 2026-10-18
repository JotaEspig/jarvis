from jarvis.protocol import Listen, SpeechStart, State, TaskInfo, dump, parse_core, parse_edge


def test_roundtrip():
    m = parse_edge(dump(SpeechStart(after_wake=True)))
    assert isinstance(m, SpeechStart) and m.after_wake
    s = parse_core(dump(State(state="thinking", conversation_open=True, tasks=[TaskInfo(id="1", title="x", status="y")])))
    assert s.tasks[0].id == "1"
    assert parse_core(dump(Listen(open=False))).open is False
