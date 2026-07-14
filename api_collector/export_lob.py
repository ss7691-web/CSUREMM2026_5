import argparse
import glob
import os
import duckdb

# Series to export. Filtering is on the market_ticker prefix f"{SERIES}-".
SERIES = ["KXGOLDD", "KXBTCD", "KXETHD", "KXBRENTD"]

# Tables to pull for each series (raw, for later reconstruction).
TABLES = ["lob", "snapshots"]


def export_chunk(chunk_path, out_dir, series_list, tables):
    chunk_name = os.path.splitext(os.path.basename(chunk_path))[0]
    try:
        con = duckdb.connect(chunk_path, read_only=True)
    except Exception as e:
        print(f"  !! could not open {chunk_name}: {e}")
        return

    # Which tables actually exist in this chunk (older chunks might differ).
    present = {r[0] for r in con.execute("SHOW TABLES").fetchall()}

    for series in series_list:
        prefix = f"{series}-"
        series_dir = os.path.join(out_dir, series)
        os.makedirs(series_dir, exist_ok=True)

        for table in tables:
            if table not in present:
                continue
            # count first so we don't litter the output with empty parquet files
            n = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE market_ticker LIKE ?",
                [prefix + "%"],
            ).fetchone()[0]
            if n == 0:
                continue

            out_path = os.path.join(series_dir, f"{chunk_name}__{table}.parquet")
            # stamp source_chunk on every row for provenance / dedup when stitching.
            con.execute(
                f"""
                COPY (
                    SELECT *, ? AS source_chunk
                    FROM {table}
                    WHERE market_ticker LIKE ?
                ) TO '{out_path}' (FORMAT PARQUET)
                """,
                [chunk_name, prefix + "%"],
            )
            print(f"  {series:9s} {table:9s} {n:>9,} rows -> {os.path.relpath(out_path, out_dir)}")

    con.close()


def main():
    ap = argparse.ArgumentParser(description="Export lob+snapshots per series from DuckDB chunks.")
    ap.add_argument("--chunks", required=True,
                    help="Glob for the chunk .duckdb files, e.g. '/path/to/*.duckdb' "
                         "(quote it so the shell doesn't expand it).")
    ap.add_argument("--out", default="./export_lob_out",
                    help="Output directory (default: ./export_lob_out).")
    ap.add_argument("--series", nargs="*", default=SERIES,
                    help=f"Series to export (default: {SERIES}).")
    args = ap.parse_args()

    chunk_files = sorted(glob.glob(os.path.expanduser(args.chunks)))
    if not chunk_files:
        raise SystemExit(f"No chunk files matched: {args.chunks}")

    os.makedirs(args.out, exist_ok=True)
    print(f"Found {len(chunk_files)} chunk(s). Exporting series {args.series} -> {args.out}\n")

    for i, chunk in enumerate(chunk_files, 1):
        print(f"[{i}/{len(chunk_files)}] {os.path.basename(chunk)}")
        export_chunk(chunk, args.out, args.series, TABLES)
        print()

    print("Done. Layout: <out>/<SERIES>/<chunk>__lob.parquet and __snapshots.parquet")


if __name__ == "__main__":
    main()
