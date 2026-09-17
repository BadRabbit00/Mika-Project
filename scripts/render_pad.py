"""Render a standalone operator PAD image without a database, models or Telegram."""

import argparse
from pathlib import Path

from src.core.pad_plot import PadPlot, render_png


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p", type=float, default=0.45)
    parser.add_argument("--a", type=float, default=-0.3)
    parser.add_argument("--d", type=float, default=0.65)
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = PadPlot.from_config(args.config).snapshot(
        {"P": args.p, "A": args.a, "D": args.d}
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(render_png(snapshot))
    print(f"PAD image saved: {args.output}")


if __name__ == "__main__":
    main()
