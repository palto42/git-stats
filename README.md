# git statistics

Per-author git stats including character-level changes using the logging module
for progress and debug output.

Columns:
author,email,commits,
added_lines,deleted_lines,added+deleted_lines,net_lines,
added_chars,deleted_chars,modified_chars,added_or_modified_chars,net_chars

Usage: run from a git repository root:
    python3 git_author_char_stats_with_logging.py [--include-merges] [--group-by name|email]
                                                  [--limit N] [--progress N] [--verbose]
                                                  [--from-date SINCE] [--to-date UNTIL]
                                                  [--branch BRANCH]
                                                  <output.csv>

Notes:
- The script writes CSV output to the required positional <output.csv> file.
- Use --from-date (alias --since) and --to-date (alias --until) to limit the commit range.
  These values are passed to `git log` and accept any date formats that Git understands
  (e.g. "2023-01-01", "2 weeks ago", "2023-01-01 12:00"). The script validates the
  date strings by asking `git` to parse them. If parsing fails the script exits with an error.
- Use --branch to restrict analysis to a single branch/ref (e.g. `main` or `origin/main`). If
  not provided the script uses `--all` to include all refs.
- Logging uses the standard `logging` module and goes to stderr so the CSV file/stdout
  stays clean.
- `--verbose` enables DEBUG logs (detailed per-commit and flush info).
- `--progress N` prints INFO-level progress messages every N commits.
- `--log-level` explicitly sets the logging level and overrides `--verbose` / `--progress`.

Common examples:
    python3 git_author_char_stats_with_logging.py --group-by name --from-date "2024-01-01" --to-date "2024-12-31" out.csv
    python3 git_author_char_stats_with_logging.py --since "2 months ago" --branch main --progress 200 --verbose stats.csv
