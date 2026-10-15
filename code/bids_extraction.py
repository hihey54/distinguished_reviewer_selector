
"""Add HotCRP paper bids to the PC roster."""

import argparse
import csv
import json
from pathlib import Path


def extract_bids(
    pc_filename: str | Path,
    prefs_filename: str | Path,
    output_folder: str | Path | None = None,
) -> Path:
    """
    Add a 'bids' column to the PC CSV.

    Each value is a JSON list of submission IDs.
    Returns the path of the generated CSV.
    """
    pc_path = Path(pc_filename).expanduser()
    prefs_path = Path(prefs_filename).expanduser()

    for path in (pc_path, prefs_path):
        if not path.is_file():
            raise FileNotFoundError(f"CSV not found: {path}")

    # Read the eligible PC members.
    with pc_path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as src:
        reader = csv.DictReader(src)

        if not reader.fieldnames or "email" not in reader.fieldnames:
            raise ValueError("PC CSV requires an 'email' column.")

        fieldnames = list(reader.fieldnames)
        pc_rows = list(reader)

    bids_by_email = {}

    for row in pc_rows:
        email = (row.get("email") or "").strip().casefold()

        if not email:
            raise ValueError("PC member without email.")

        if email in bids_by_email:
            raise ValueError(f"Duplicate PC email: {email}")

        bids_by_email[email] = set()

    # Read HotCRP preferences.
    with prefs_path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as src:
        reader = csv.DictReader(src)

        required = {"paper", "email", "preference"}

        if not reader.fieldnames or not required.issubset(
            reader.fieldnames
        ):
            raise ValueError(
                "Preferences CSV requires paper, email, preference."
            )

        for row in reader:
            email = (row.get("email") or "").strip().casefold()

            # Ignore people outside the eligible PC roster.
            if email not in bids_by_email:
                continue

            preference = (row.get("preference") or "").strip()

            # Empty values mean no explicit bid.
            # Importantly, '0' is still an explicit bid.
            if preference.casefold() in {"", "none", "null"}:
                continue

            paper_id = int(row["paper"])
            bids_by_email[email].add(paper_id)

    # Prepare output.
    output_dir = (
        Path(output_folder).expanduser()
        if output_folder is not None
        else pc_path.parent
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{pc_path.stem}_bids.csv"

    if "bids" not in fieldnames:
        fieldnames.append("bids")

    # Write the enriched PC CSV.
    with output_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as dst:
        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()

        for row in pc_rows:
            email = row["email"].strip().casefold()
            row["bids"] = json.dumps(sorted(bids_by_email[email]))
            writer.writerow(row)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Extract PC paper bids from HotCRP."
    )

    parser.add_argument("pc_filename")
    parser.add_argument("prefs_filename")

    parser.add_argument(
        "--output-folder", "-o",
        default=None,
        help="Output folder (defaults to PC file's folder)."
    )

    args = parser.parse_args()

    output = extract_bids(
        args.pc_filename,
        args.prefs_filename,
        args.output_folder
    )

    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
