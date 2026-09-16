"""Reject private deployment files in the Git index, including forced additions."""

import subprocess
from pathlib import Path, PurePosixPath


def private_paths(paths: list[str]) -> list[str]:
    rejected = []
    for name in paths:
        path = PurePosixPath(name)
        private = (
            name.startswith("library/")
            or name in {"config/telegram.yaml", "config/telegram.yml"}
            or (
                path.parent == PurePosixPath("config")
                and path.name.startswith("world-")
                and path.suffix == ".json"
            )
            or (
                path.name != ".env.example"
                and (
                    path.name == ".env"
                    or path.name.startswith(".env.")
                    or path.name.endswith(".env")
                )
            )
        )
        if private:
            rejected.append(name)
    return rejected


def tracked_paths(directory: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(directory), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    return [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]


def main() -> int:
    rejected = private_paths(tracked_paths(Path.cwd()))
    if rejected:
        for name in rejected:
            print(f"Private deployment file is tracked: {name}")
        return 1
    print("Repository privacy check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
