import json
from pathlib import Path


def load_history_document(
    path: Path,
    *,
    entries_key: str = "entries",
) -> dict:
    if not path.exists():
        return {entries_key: []}

    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{path} contains invalid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            f"{path} must contain a JSON object"
        )

    entries = data.get(entries_key, [])

    if not isinstance(entries, list):
        raise RuntimeError(
            f"{path} field '{entries_key}' must be a list"
        )

    data[entries_key] = entries
    return data


def write_history_document(
    path: Path,
    data: dict,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = path.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(path)
