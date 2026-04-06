#!/usr/bin/env python3
"""Download raw benchmark datasets into datasets/<name>/ subdirectories.

Usage:
    python datasets/download.py               # download all
    python datasets/download.py tau_bench     # download one
    python datasets/download.py abcd multiwoz # download several
"""
import argparse
import gzip
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent

DATASETS = {
    "tau_bench": {
        "dir": ROOT / "tau_bench",
        "files": [
            # Few-shot tasks (JSONL) — retail + airline
            (
                "https://raw.githubusercontent.com/sierra-research/tau-bench/main/few_shot_data/MockRetailDomainEnv-few_shot.jsonl",
                "retail_tasks.jsonl",
            ),
            (
                "https://raw.githubusercontent.com/sierra-research/tau-bench/main/few_shot_data/MockAirlineDomainEnv-few_shot.jsonl",
                "airline_tasks.jsonl",
            ),
            # Retail database
            (
                "https://raw.githubusercontent.com/sierra-research/tau-bench/main/tau_bench/envs/retail/data/users.json",
                "retail_users.json",
            ),
            (
                "https://raw.githubusercontent.com/sierra-research/tau-bench/main/tau_bench/envs/retail/data/orders.json",
                "retail_orders.json",
            ),
            (
                "https://raw.githubusercontent.com/sierra-research/tau-bench/main/tau_bench/envs/retail/data/products.json",
                "retail_products.json",
            ),
            # Airline database
            (
                "https://raw.githubusercontent.com/sierra-research/tau-bench/main/tau_bench/envs/airline/data/users.json",
                "airline_users.json",
            ),
            (
                "https://raw.githubusercontent.com/sierra-research/tau-bench/main/tau_bench/envs/airline/data/reservations.json",
                "airline_reservations.json",
            ),
            (
                "https://raw.githubusercontent.com/sierra-research/tau-bench/main/tau_bench/envs/airline/data/flights.json",
                "airline_flights.json",
            ),
            # Policy documents
            (
                "https://raw.githubusercontent.com/sierra-research/tau-bench/main/tau_bench/envs/retail/wiki.md",
                "retail_policy.md",
            ),
        ],
    },
    "abcd": {
        "dir": ROOT / "abcd",
        "files": [
            (
                "https://raw.githubusercontent.com/asappresearch/abcd/master/data/abcd_sample.json",
                "abcd_sample.json",
            ),
            (
                "https://raw.githubusercontent.com/asappresearch/abcd/master/data/guidelines.json",
                "guidelines.json",
            ),
            (
                "https://raw.githubusercontent.com/asappresearch/abcd/master/data/kb.json",
                "kb.json",
            ),
        ],
        # Full dataset (gzipped) — downloaded separately and decompressed
        "gz_files": [
            (
                "https://raw.githubusercontent.com/asappresearch/abcd/master/data/abcd_v1.1.json.gz",
                "abcd_v1.1.json",
            ),
        ],
    },
    "multiwoz": {
        "dir": ROOT / "multiwoz",
        "files": [
            # Schema definition
            (
                "https://raw.githubusercontent.com/budzianowski/multiwoz/master/data/MultiWOZ_2.2/schema.json",
                "schema.json",
            ),
            # Test dialogues (first file of test split)
            (
                "https://raw.githubusercontent.com/budzianowski/multiwoz/master/data/MultiWOZ_2.2/test/dialogues_001.json",
                "test_dialogues_001.json",
            ),
            (
                "https://raw.githubusercontent.com/budzianowski/multiwoz/master/data/MultiWOZ_2.2/test/dialogues_002.json",
                "test_dialogues_002.json",
            ),
        ],
    },
}


def _download(url: str, dest: Path) -> None:
    print(f"  Downloading {dest.name} ...", end=" ", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:
            shutil.copyfileobj(resp, out)
        print(f"OK ({dest.stat().st_size // 1024} KB)")
    except Exception as exc:
        print(f"FAILED: {exc}")
        raise


def _download_gz(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(".json.gz")
    print(f"  Downloading {tmp.name} (gzip) ...", end=" ", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out)
        with gzip.open(tmp, "rb") as gz, open(dest, "wb") as out:
            shutil.copyfileobj(gz, out)
        tmp.unlink()
        print(f"OK ({dest.stat().st_size // 1024} KB)")
    except Exception as exc:
        print(f"FAILED: {exc}")
        raise


def download_dataset(name: str) -> None:
    cfg = DATASETS[name]
    cfg["dir"].mkdir(parents=True, exist_ok=True)
    print(f"\n[{name}]")

    for url, filename in cfg.get("files", []):
        dest = cfg["dir"] / filename
        if dest.exists():
            print(f"  {filename} already exists, skipping")
            continue
        _download(url, dest)

    for url, filename in cfg.get("gz_files", []):
        dest = cfg["dir"] / filename
        if dest.exists():
            print(f"  {filename} already exists, skipping")
            continue
        _download_gz(url, dest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download benchmark datasets")
    parser.add_argument(
        "datasets",
        nargs="*",
        choices=list(DATASETS) + ["all"],
        default=["all"],
        help="Which datasets to download (default: all)",
    )
    args = parser.parse_args()

    targets = list(DATASETS) if "all" in args.datasets else args.datasets
    for name in targets:
        try:
            download_dataset(name)
        except Exception:
            print(f"  Error downloading {name}, continuing...", file=sys.stderr)

    print("\nDone. Run adapters to convert to bench YAML:")
    print("  python datasets/adapters/tau_bench_adapter.py")
    print("  python datasets/adapters/abcd_adapter.py")
    print("  python datasets/adapters/multiwoz_adapter.py")


if __name__ == "__main__":
    main()
