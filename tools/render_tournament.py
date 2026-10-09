"""Create editable local tournament scenes; never starts OBS or a server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from obs_director.tournament import TournamentValidationError, demo_snapshot, read_snapshot, render_tournament


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, help="Local obs.tournament.v1 JSON; timestamps are preserved")
    source.add_argument("--demo", action="store_true", help="Create explicit synthetic values with the current creation time")
    parser.add_argument("--output", type=Path, required=True, help="New output directory; parent must exist")
    args = parser.parse_args(argv)
    try:
        snapshot = demo_snapshot() if args.demo else read_snapshot(args.snapshot)
        portrait = ROOT / "examples" / "funded-desk" / "presenter.png" if args.demo else None
        receipt = render_tournament(snapshot, args.output, template_dir=ROOT / "examples" / "tournament", portrait_path=portrait)
    except (TournamentValidationError, OSError) as error:
        parser.exit(2, f"Tournament rendering failed: {type(error).__name__}\n")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
