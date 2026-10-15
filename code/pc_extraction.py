"""Annotate a HotCRP PC roster with topic-completion status.

This module can be imported by a future main script or run independently:

    python pc_extraction.py source/pcinfo.csv
    python pc_extraction.py source/pcinfo.csv --output-folder output

Only the Python standard library is required. No data is sent anywhere.
"""

import argparse
import csv
from pathlib import Path


def _topic_is_set(value: str | None) -> bool:
    """True if a HotCRP topic cell contains an explicit value."""
    return value is not None and value.strip().casefold() not in {"", "none"}


def extract_pc_topics(
    filename: str | Path, output_folder: str | Path | None = None
) -> Path:
    """Write a copy of a PC-info CSV with an added `topics?` column.

    `topics?` is True only if every `topic:` column has a nonempty value.
    Missing cells, empty cells, and literal `None` count as unset.
    If the CSV contains no topic columns, `topics?` is N/A.

    Returns the output file's path for use by a future main script.
    """
    input_path = Path(filename).expanduser()
    if not input_path.is_file():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    output_dir = (
        Path(output_folder).expanduser()
        if output_folder is not None
        else input_path.parent
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{input_path.stem}_topics.csv"

    with input_path.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        if not reader.fieldnames:
            raise ValueError("Input CSV is empty or has no header.")

        fieldnames = list(reader.fieldnames)
        topic_columns = [
            column for column in fieldnames
            if column.strip().casefold().startswith("topic:")
        ]
        if "topics?" not in fieldnames:
            fieldnames.append("topics?")

        with output_path.open("w", encoding="utf-8-sig", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=fieldnames)
            writer.writeheader()

            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise ValueError(
                        f"CSV row {row_number} contains more fields than its header."
                    )

                if not topic_columns:
                    row["topics?"] = "N/A"
                else:
                    row["topics?"] = all(
                        _topic_is_set(row.get(column)) for column in topic_columns
                    )

                writer.writerow(row)

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add a topics? completion column to a HotCRP PC CSV."
    )
    parser.add_argument("filename", help="Path to the HotCRP PC-info CSV.")
    parser.add_argument(
        "--output-folder", "-o", default=None,
        help="Destination folder (defaults to the input CSV's folder).",
    )
    args = parser.parse_args()

    output_path = extract_pc_topics(args.filename, args.output_folder)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
