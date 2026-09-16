"""File-backed session instructions with mode-specific data boundaries."""

import json
import re
from pathlib import Path

from ruamel.yaml import YAML

from src.core.context import ContextIsolationError, Request
from src.core.pad import Mood
from src.core.settings import SettingsRegistry
from src.core.world import DayContext

_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_FIELD = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class ChatContext:
    def __init__(
        self, prompt_dir, mood_model, *, config_dir=Path("config"), settings=None
    ):
        self.prompt_dir, self.config_dir = Path(prompt_dir), Path(config_dir)
        self.mood_model = mood_model
        self.settings = settings or SettingsRegistry.from_file(
            self.config_dir / "settings.yaml"
        )

    def build(
        self,
        mode,
        *,
        question,
        nodes,
        history,
        summary,
        people_facts,
        day,
        mood,
        wake_reason,
        budget=16000,
        narrative=(),
    ):
        if mode not in {"topical", "unknown", "personal"}:
            raise ValueError("Unknown conversation mode")
        if (mode == "topical") != bool(nodes):
            raise ContextIsolationError("Only topical chat may receive graph nodes")
        if not isinstance(day, DayContext) or not isinstance(mood, Mood):
            raise TypeError("Chat requires validated day and mood values")
        yaml = YAML(typ="safe")
        emoji_max = self.settings.get("persona.emoji_max")
        persona = _COMMENTS.sub(
            "", (self.prompt_dir / "_base_core.md").read_text()
        ).strip()
        if mode == "topical":
            persona += "\n\n" + (self.prompt_dir / "_base_study.md").read_text().strip()
        values = {"mood": self.mood_model.mood_block(mood), "emoji_max": str(emoji_max)}
        if set(_FIELD.findall(persona)) != values.keys():
            raise ValueError("Unexpected persona fields")
        persona = _FIELD.sub(lambda match: values[match[1]], persona)
        raw = (self.prompt_dir / "chat_session.md").read_text()
        temperature = re.search(r"\btemp\s+([0-9.]+)", raw)
        length = re.search(r"Длина\s+(\d+)[–-](\d+)", raw)
        if temperature is None or length is None:
            raise ValueError("Missing session sampling or length metadata")
        session = _COMMENTS.sub("", raw).strip()
        session_rules = session.split("{mode_block}", 1)[0].replace(
            "{persona}", persona
        )
        template = (
            _COMMENTS.sub("", (self.prompt_dir / f"chat_{mode}.md").read_text())
            .replace("{persona}", "")
            .replace("{identity}", "")
            .strip()
        )
        first = _FIELD.search(template)
        boundary = template.rfind("\n", 0, first.start()) + 1
        data = {
            "question": question,
            "dialog_tail": history,
            "session_summary": summary,
            "people_facts": people_facts,
            "day_context": {
                "when": day.at.isoformat(),
                "daypart": day.daypart,
                "location": day.location,
                "bedtime": day.bedtime.isoformat(),
                "wake_time": day.wake_time.isoformat(),
                "wake_reason": wake_reason,
                "sleep_debt": round(day.sleep_debt, 4),
            },
        }
        if mode == "topical":
            data["retrieved_nodes"] = nodes
        elif mode == "personal":
            life = yaml.load((self.config_dir / "life.yaml").read_text())
            data.update(
                identity=life["identity"],
                life_state=life["progress"],
                narrative_offtop=list(narrative),
            )
        else:
            data["nearest_nodes"] = []
        return Request(
            f"chat_{mode}",
            session_rules + "\n\n" + template[:boundary].strip(),
            json.dumps(data, ensure_ascii=False),
            budget,
            float(temperature[1]),
            min_chars=1,
            max_chars=int(length[2]),
        )
