"""Build Phase 3 v3 static prompt corpora from already-local exact tokenizers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_plan  # noqa: E402
from rejudge import phase3_v3_tokenizer_materialization as materialization  # noqa: E402


def _load_json_object(path: Path, label: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise materialization.TokenizerMaterializationError(
            f"{label} must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--tokenizer-spec", type=Path, required=True)
    parser.add_argument("--main-transcript-bundle", type=Path, required=True)
    parser.add_argument("--canary-transcript-bundle", type=Path, required=True)
    parser.add_argument(
        "--prompt-bundle", type=Path,
        default=REPO_ROOT / "rejudge/phase2_prompt_bundle.json")
    parser.add_argument(
        "--world-documents", type=Path, default=REPO_ROOT / "world_specs")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    protocol = phase3_plan.load_protocol(args.protocol.resolve())
    spec = _load_json_object(args.tokenizer_spec.resolve(), "tokenizer spec")
    result = materialization.build_exact_tokenizer_artifacts(
        protocol=protocol,
        tokenizer_spec=spec,
        transcript_bundle_paths={
            "main": args.main_transcript_bundle.resolve(),
            "canary": args.canary_transcript_bundle.resolve(),
        },
        prompt_bundle_path=args.prompt_bundle.resolve(),
        world_documents_directory=args.world_documents.resolve(),
        output_directory=args.out_dir.resolve(),
        project_root=REPO_ROOT,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "manifest"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
