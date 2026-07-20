import agent


def test_entrypoint_defaults_to_tui(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "run_tui", lambda: calls.append("tui"))
    monkeypatch.setattr(agent, "run_classic", lambda: calls.append("classic"))

    agent.main([])

    assert calls == ["tui"]


def test_entrypoint_classic_flag_preserves_old_cli(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "run_tui", lambda: calls.append("tui"))
    monkeypatch.setattr(agent, "run_classic", lambda: calls.append("classic"))

    agent.main(["--classic"])

    assert calls == ["classic"]
