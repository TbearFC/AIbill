"""Explicitly synthetic contract fixture, no real user identifiers."""

import json, pathlib


def make_demo(folder):
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    events = []
    for i in range(60):
        agent = "codex" if i % 2 == 0 else "claude"
        model = "example-openai-model" if agent == "codex" else "example-anthropic-model"
        day = 20 + i // 6
        hour = i % 6 + 8
        root = "demo-run-" + str(i)
        session = "demo-session-" + str(i)
        events.append(
            dict(
                type="task_started",
                timestamp=f"2026-09-{day:02}T{hour:02}:00:00Z",
                agent=agent,
                run_id=root,
            )
        )
        for j in range(8 + (i % 5)):
            events.append(
                dict(
                    type="usage",
                    timestamp=f"2026-09-{day:02}T{hour:02}:{j+1:02}:00Z",
                    agent=agent,
                    session_id=session,
                    run_id=root,
                    response_id=root + "-" + str(j),
                    model=model,
                    usage_schema="exclusive",
                    usage={
                        "input_uncached": 1000 + i * 50,
                        "cache_read": 5000 + j * 200,
                        "cache_write_5m": 200,
                        "cache_write_1h": 0,
                        "output": 300 + i * 5,
                    },
                )
            )
        events.append(
            dict(
                type="task_complete",
                timestamp=f"2026-09-{day:02}T{hour:02}:40:00Z",
                agent=agent,
                run_id=root,
            )
        )
    for agent, model in [("codex", "example-openai-model"), ("claude", "example-anthropic-model")]:
        root = "demo-open-" + agent
        events.append(
            dict(type="task_started", timestamp="2026-10-03T08:00:00Z", agent=agent, run_id=root)
        )
        for j in range(5):
            events.append(
                dict(
                    type="usage",
                    timestamp=f"2026-10-03T08:0{j+1}:00Z",
                    agent=agent,
                    session_id=root,
                    run_id=root,
                    response_id=root + str(j),
                    model=model,
                    usage_schema="exclusive",
                    usage={"input_uncached": 1500, "cache_read": 6000, "output": 400},
                )
            )
    (folder / "demo.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
