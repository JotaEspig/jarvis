from jarvis.edge.segmenter import CHUNK_MS, Segmenter, SegmenterConfig
from jarvis.protocol import SpeechCancel, SpeechEnd, SpeechPause, SpeechStart

PCM = b"\x00\x00" * 512


def feed(seg, probs, playing=False):
    events, barges = [], []
    for p in probs:
        out, barge = seg.step(p, PCM, playing=playing)
        events += [o for o in out if not isinstance(o, bytes)]
        barges.append(barge)
    return events, any(barges)


def ms(n):
    return n // CHUNK_MS


def test_closed_window_ignores_speech():
    seg = Segmenter(SegmenterConfig())
    events, _ = feed(seg, [0.9] * 20)
    assert events == []


def test_open_window_segments_speech_with_pause_and_end():
    seg = Segmenter(SegmenterConfig())
    seg.set_open(True)
    events, _ = feed(seg, [0.9] * ms(640) + [0.1] * ms(320) + [0.9] * ms(320) + [0.1] * ms(700))
    kinds = [type(e) for e in events]
    # a pausa sempre precede o fim (o core adianta a decodificação do trecho)
    assert kinds == [SpeechStart, SpeechPause, SpeechPause, SpeechEnd]
    assert not events[0].after_wake


def test_short_blip_is_cancelled():
    seg = Segmenter(SegmenterConfig(min_speech_ms=300))
    seg.set_open(True)
    events, _ = feed(seg, [0.9] * ms(96) + [0.0] * ms(700))
    assert [type(e) for e in events] == [SpeechStart, SpeechPause, SpeechCancel]


def test_wake_marks_next_speech_and_drops_running_segment():
    seg = Segmenter(SegmenterConfig())
    seg.set_open(True)
    feed(seg, [0.9] * 5)
    assert seg.in_speech
    out = seg.wake()
    assert [type(o) for o in out] == [SpeechCancel]
    events, _ = feed(seg, [0.9] * 3)
    assert isinstance(events[0], SpeechStart) and events[0].after_wake


def test_barge_in_needs_stronger_and_longer_speech():
    seg = Segmenter(SegmenterConfig(barge_threshold=0.75, barge_in_ms=256))
    seg.set_open(True)
    events, barge = feed(seg, [0.6] * 20, playing=True)  # eco residual moderado
    assert events == [] and not barge
    events, barge = feed(seg, [0.9] * ms(256), playing=True)
    assert barge and events[0].barge_in


def test_closing_window_cancels_running_segment():
    seg = Segmenter(SegmenterConfig())
    seg.set_open(True)
    feed(seg, [0.9] * 5)
    assert [type(o) for o in seg.set_open(False)] == [SpeechCancel]
