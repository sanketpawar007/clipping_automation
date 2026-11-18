from app.live import chat, detector


def test_channel_parsing():
    assert chat.channel_of("https://www.twitch.tv/Lacy") == ("twitch", "lacy")
    assert chat.channel_of("https://kick.com/dasgasd3m") == ("kick", "dasgasd3m")
    assert chat.channel_of("https://www.youtube.com/@Tomovids/live") is None


def test_chat_spike_against_own_baseline():
    now = 10_000.0
    calm = [(now - 600 + i * 6, f"u{i % 40}", "hello") for i in range(97)]        # ~1 msg / 6s
    burst = [(now - 10 + i * 0.2, f"v{i}", "KEKW 💀") for i in range(50)]          # 50 users in 10s
    quiet = detector.chat_spike(calm, now)
    loud = detector.chat_spike(calm + burst, now)
    assert quiet["z"] < 2 and loud["z"] > 6 and loud["users"] == 50 and "kekw" in loud["top"]


def test_audio_spike():
    base = [-30.0] * 300
    assert detector.audio_spike(base) == 0
    assert detector.audio_spike(base + [-10.0] * 8) >= 19
