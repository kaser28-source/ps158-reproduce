#!/usr/bin/env python3
"""Download the two official CRDC public-use releases used by plot.py.

Usage: python3 get_data.py [--cache-dir DIRECTORY] [--refresh]
Requires Python 3.10+ and only the standard library. Downloads are cached outside
the repository by default. No data are extracted or edited. Source landing page:
https://civilrightsdata.ed.gov/data (accessed 2026-10-21).

Official documentation:
2017-18: https://civilrightsdata.ed.gov/assets/for-researchers/user-manual/2017-18%20CRDC%20Public-Use%20Data%20File%20Manual.pdf
2023-24: https://civilrightsdata.ed.gov/assets/downloads/2023-24%20User%27s%20Manual.pdf
2023-24 dictionary: https://civilrightsdata.ed.gov/assets/downloads/2023-24%20Appendix%20Workbook.xlsx

SHA-256 values pin the bytes inspected for this analysis, rather than a checksum
published by ED. An upstream revision must be reviewed before updating the pins.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

sys.dont_write_bytecode = True

DEFAULT_CACHE = Path(tempfile.gettempdir()) / "ps158-crdc-public-use"
SOURCES = {
    "2017-18": {
        "url": "https://civilrightsdata.ed.gov/assets/ocr/docs/2017-18-crdc-data.zip",
        "sha256": "422d5cef8733c0d3084cb16e5ad4f459e38de943d3cd5118bc60c93d1e27511a",
    },
    "2023-24": {
        "url": "https://civilrightsdata.ed.gov/assets/ocr/docs/2023-24-crdc-data.zip",
        "sha256": "7471ce010c73804cfeddb50db28d0cf29d6d5a4fb903d4989cbb7a308f45475e",
    },
}
REQUIRED_FILES = ("Enrollment.csv", "Suspensions.csv", "School Characteristics.csv")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_path(year: str, cache_dir: Path = DEFAULT_CACHE) -> Path:
    return cache_dir / f"{year}-crdc-data.zip"


def find_member(archive: zipfile.ZipFile, basename: str) -> str:
    """Require one unambiguous topical CSV, ignoring macOS metadata."""
    matches = [
        name for name in archive.namelist()
        if Path(name.replace("\\", "/")).name.casefold() == basename.casefold()
        and "__MACOSX" not in name
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one {basename!r}; found {matches!r}")
    return matches[0]


def validate_archive(path: Path, year: str) -> str:
    digest = sha256_file(path)
    expected = SOURCES[year]["sha256"]
    if expected and digest != expected:
        raise ValueError(
            f"{year}: SHA-256 mismatch for {path}. Expected {expected}; got "
            f"{digest}. Do not silently use an unreviewed upstream revision."
        )
    with zipfile.ZipFile(path) as archive:
        for basename in REQUIRED_FILES:
            member = find_member(archive, basename)
            # Reading to EOF verifies each selected CSV's ZIP CRC without extraction.
            with archive.open(member) as stream:
                while stream.read(1024 * 1024):
                    pass
    return digest


def download(year: str, cache_dir: Path, refresh: bool = False) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_path(year, cache_dir)
    if destination.exists() and not refresh:
        digest = validate_archive(destination, year)
        print(f"{year}: verified cached archive {destination}\n  SHA-256 {digest}", flush=True)
        return destination

    url = SOURCES[year]["url"]
    for attempt in range(1, 4):
        temporary = None
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "PS158-CRDC-reproduction/1.0"}
            )
            print(f"{year}: downloading {url} (attempt {attempt}/3)", flush=True)
            with urllib.request.urlopen(request, timeout=120) as response:
                expected_length = response.headers.get("Content-Length")
                with tempfile.NamedTemporaryFile(
                    dir=cache_dir, prefix=f".{year}-", suffix=".part", delete=False
                ) as output:
                    temporary = Path(output.name)
                    size = 0
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        output.write(block)
                        size += len(block)
                if expected_length is not None and size != int(expected_length):
                    raise OSError(f"Incomplete download: {size}/{expected_length} bytes")
            digest = validate_archive(temporary, year)
            os.replace(temporary, destination)
            print(f"{year}: verified {size:,} bytes, saved {destination}\n  SHA-256 {digest}", flush=True)
            return destination
        except (OSError, ValueError, zipfile.BadZipFile, urllib.error.URLError) as exc:
            if attempt == 3 or isinstance(exc, (ValueError, zipfile.BadZipFile)):
                raise
            print(f"  Download failed: {exc}; retrying.", file=sys.stderr, flush=True)
            time.sleep(attempt * 2)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise RuntimeError("Unreachable download state")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--refresh", action="store_true", help="Re-download and verify the pinned releases")
    args = parser.parse_args()
    try:
        for year in SOURCES:
            download(year, args.cache_dir, args.refresh)
    except (OSError, ValueError, zipfile.BadZipFile, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Inputs ready. Run python3 plot.py with the same --cache-dir if customized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
