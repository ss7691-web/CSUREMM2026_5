import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import pandas as pd 

DATA_DIR = Path(__file__).parent / "data_tech_all"
BLENDED_DIR = Path(__file__).parent / "hourly_kalshi" / "data" / "blended"
BLENDED_KXBRENTD_CSV = BLENDED_DIR / "BRENT_combined.csv"
WEEKDAY_KXBRENTD_CSV = BLENDED_DIR / "weekday_KXBRENTD_blended.csv"


def find_unique_open_days(data_dir: Path = DATA_DIR) -> set[str]:
    """Scan every '*_markets_live.csv' and '*_markets_historical.csv' under
    data_dir and return the set of unique open_time dates (YYYY-MM-DD)."""
    unique_days: set[str] = set()

    for csv_path in data_dir.glob("*/*_markets_live.csv"):
        unique_days |= _open_days_from_csv(csv_path)

    for csv_path in data_dir.glob("*/*_markets_historical.csv"):
        unique_days |= _open_days_from_csv(csv_path)

    return unique_days


def _open_days_from_csv(csv_path: Path) -> set[str]:
    days: set[str] = set()
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            open_time = row.get("open_time")
            if not open_time:
                continue
            date = datetime.fromisoformat(open_time.replace("Z", "+00:00")).date()
            days.add(date.isoformat())
    return days


def _days_from_csv(csv_path: Path, day_col: str = "converted_time") -> set[str]:
    """Return the unique calendar days (YYYY-MM-DD) found in day_col of csv_path."""
    days: set[str] = set()
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            value = row.get(day_col)
            if value:
                days.add(value.split(" ")[0])
    return days


def compare_kxbrentd_unique_days(
    blended_path: Path = BLENDED_KXBRENTD_CSV,
    weekday_path: Path = WEEKDAY_KXBRENTD_CSV,
) -> tuple[set[str], set[str]]:
    """Compare the unique days in blended_KXBRENTD.csv vs
    weekday_KXBRENTD_blended.csv and print the days that only appear in one
    of the two files (no overlap)."""
    blended_days = _days_from_csv(blended_path)
    weekday_days = _days_from_csv(weekday_path)

    only_in_blended = blended_days - weekday_days
    only_in_weekday = weekday_days - blended_days

    print(f"Days only in {blended_path.name} ({len(only_in_blended)}):")
    for day in sorted(only_in_blended):
        print(f"  {day}")

    print(f"Days only in {weekday_path.name} ({len(only_in_weekday)}):")
    for day in sorted(only_in_weekday):
        print(f"  {day}")

    return only_in_blended, only_in_weekday


def _rows_by_day(
    csv_path: Path,
    day_col: str = "converted_time",
    ignore_cols: tuple[str, ...] = ("date_only", "weekday"),
) -> tuple[dict[str, list[tuple]], list[str]]:
    """Group each row (as a tuple of its shared-column values) by calendar day."""
    rows_by_day: dict[str, list[tuple]] = defaultdict(list)
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        shared_cols = [c for c in reader.fieldnames if c not in ignore_cols]
        for row in reader:
            day = row[day_col].split(" ")[0]
            rows_by_day[day].append(tuple(row[c] for c in shared_cols))
    return rows_by_day, shared_cols


def combine_kxbrentd_days(
    blended_path: Path = BLENDED_KXBRENTD_CSV,
    weekday_path: Path = WEEKDAY_KXBRENTD_CSV,
) -> dict[str, list[tuple]]:
    """Combine the unique days of blended_KXBRENTD.csv and
    weekday_KXBRENTD_blended.csv into one day -> rows mapping covering the
    union of both files. For days present in both files, the rows are
    expected to match exactly; any day where they don't is flagged."""
    blended_by_day, _ = _rows_by_day(blended_path)
    weekday_by_day, _ = _rows_by_day(weekday_path)

    all_days = set(blended_by_day) | set(weekday_by_day)
    combined: dict[str, list[tuple]] = {}
    flagged_days: list[str] = []

    for day in sorted(all_days):
        b_rows = blended_by_day.get(day, [])
        w_rows = weekday_by_day.get(day, [])

        if b_rows and w_rows and Counter(b_rows) != Counter(w_rows):
            flagged_days.append(day)

        combined[day] = b_rows or w_rows

    if flagged_days:
        print(f"Flagged {len(flagged_days)} overlapping day(s) with mismatched values:")
        for day in flagged_days:
            print(f"  {day}")
    else:
        print("All overlapping days match between the two files.")

    print(f"Combined dataset covers {len(combined)} unique days.")

    return combined


def ticker_to_threshold(ticker):
    parts = ticker.split("-")
    return parts[2].lstrip("T")  


if __name__ == "__main__":
    '''
    df = pd.read_csv("hourly_kalshi/data/blended/combined_KXBRENTD.csv")
    
    df["threshold"] = df["ticker"].apply(ticker_to_threshold)
    print(df["threshold"])
    df.to_csv('hourly_kalshi/data/blended/combined_KXBRENTD.csv', index=False)
    '''
    days = find_unique_open_days()
    for day in sorted(days):
        print(day)
    print(f"\n{len(days)} unique open days found")

    compare_kxbrentd_unique_days()
    combine_kxbrentd_days()



    #start time 2021-10-18

