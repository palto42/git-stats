#!/usr/bin/env python3
"""
git_author_char_stats_with_logging.py

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
- Grouping is case-insensitive using Unicode-aware casefold().
- When grouping by email, the `author` column lists all author names seen for that email
  (semicolon-separated) and the `email` column shows a canonical email (most frequent first seen).
- When grouping by name, the `author` column shows a canonical name form and the `email`
  column lists all emails seen for that name.
- The script validates dates and branch/ref before running and logs progress to stderr.
"""

from collections import defaultdict, Counter
import subprocess
import sys
import csv
import argparse
import logging

# ------------------ Utility functions ------------------


def run(cmd):
    """Run command and return stdout as text. Raises RuntimeError on non-zero exit."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = p.communicate()
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)} {err.strip()}")
    return out


def check_git_repo():
    """Validate if current folder is a git repository."""
    cmd = ["git", "status"]
    try:
        result = run(cmd)
        logging.debug(f"git status: {result}")
        return True
    except RuntimeError as err:
        logging.error(f"Folder is not a git repository:\n{err}")
        return False


def check_git_date(date_str, refspec=None):
    """Validate that git accepts a date string by running a trivial git log query.

    Returns True if git parsed the date (even if it returns no commits); False otherwise.
    If refspec is provided it is appended to the git command (e.g. a branch name) so
    validation uses the same refs that will be used during processing.
    """
    cmd = ["git", "log", "--pretty=format:%H", f"--since={date_str}", "-n", "1"]
    logging.debug(f"Check date using the command'{cmd}'")
    if refspec:
        cmd.append(refspec)
    try:
        # if git accepts the date but there are no commits in range, git returns 0 and empty output
        result = run(cmd)
        logging.debug(f"git date check result: {result}")
        return True
    except RuntimeError as err:
        logging.debug(f"git date check error: {err}")
        return False


def check_git_until_date(date_str, refspec=None):
    """Validate --until (to-date) similarly to check_git_date."""
    cmd = ["git", "log", "--pretty=format:%H", f"--until={date_str}", "-n", "1"]
    if refspec:
        cmd.append(refspec)
    try:
        _ = run(cmd)
        return True
    except RuntimeError:
        return False


def validate_branch(branch):
    """Validate that the provided branch/ref exists (git rev-parse --verify).

    Returns True if branch exists, False otherwise.
    """
    try:
        # `git rev-parse --verify --quiet <branch>` returns 0 if exists; but some versions need full ref
        run(["git", "rev-parse", "--verify", branch])
        return True
    except RuntimeError:
        # try resolving as a ref name (heads, remotes)
        try:
            run(["git", "show-ref", "--verify", f"refs/heads/{branch}"])
            return True
        except RuntimeError:
            try:
                run(["git", "show-ref", "--verify", f"refs/remotes/{branch}"])
                return True
            except RuntimeError:
                return False


def levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein distance (character-level) between a and b."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    # ensure a is the shorter string to use less memory
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    previous = list(range(la + 1))
    for i in range(1, lb + 1):
        c = b[i - 1]
        current = [i] + [0] * la
        for j in range(1, la + 1):
            insert_cost = previous[j] + 1
            delete_cost = current[j - 1] + 1
            replace_cost = previous[j - 1] + (0 if a[j - 1] == c else 1)
            current[j] = min(insert_cost, delete_cost, replace_cost)
        previous = current
    return previous[la]


# ------------------ Argument parsing ------------------


def parse_args():
    ap = argparse.ArgumentParser(
        description="Per-author git stats including character-level changes (logging)"
    )
    ap.add_argument(
        "--include-merges", action="store_true", help="Include merge commits"
    )
    ap.add_argument(
        "--group-by",
        "-G",
        choices=("name", "email"),
        default="name",
        help="Group authors by name or email",
    )
    ap.add_argument(
        "--limit", type=int, default=0, help="Limit to most recent N commits (0 = all)"
    )
    ap.add_argument(
        "--progress",
        "-P",
        type=int,
        default=0,
        help="Print a progress message every N commits (0 = disabled)",
    )
    ap.add_argument(
        "--verbose",
        "-V",
        action="store_true",
        help="Verbose debug output (sets log level to DEBUG)",
    )
    ap.add_argument(
        "--log-level",
        "-L",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Explicit log level (overrides --verbose and --progress default behavior)",
    )
    # date range options: aliases map to git's --since / --until
    ap.add_argument(
        "--from-date",
        "--since",
        dest="since",
        default=None,
        help="Start date (inclusive). Passed to `git log --since`. Accepts git date formats.",
    )
    ap.add_argument(
        "--to-date",
        "--until",
        dest="until",
        default=None,
        help="End date (inclusive). Passed to `git log --until`. Accepts git date formats.",
    )
    ap.add_argument(
        "--branch",
        dest="branch",
        default=None,
        help="Optional branch or ref to analyze (e.g. 'main' or 'origin/main'). If omitted, --all is used.",
    )
    ap.add_argument("output", help="Output CSV file path (required)")
    return ap.parse_args()


# ------------------ Main ------------------


def main():
    args = parse_args()
    include_merges = args.include_merges
    group_by = args.group_by
    limit = args.limit if args.limit and args.limit > 0 else None
    progress_every = args.progress if args.progress and args.progress > 0 else None
    output_path = args.output
    since = args.since
    until = args.until
    branch = args.branch

    # Configure logging
    # Default: WARNING. If --verbose -> DEBUG. If progress requested -> INFO. --log-level overrides.
    if args.log_level:
        level = getattr(logging, args.log_level)
    elif args.verbose:
        level = logging.DEBUG
    elif progress_every:
        level = logging.INFO
    else:
        level = logging.WARNING

    # Ensure logs go to stderr to keep CSV file clean
    logging.basicConfig(
        stream=sys.stderr, level=level, format="[%(levelname)s] %(message)s"
    )
    logger = logging.getLogger(__name__)

    logger.debug("Starting to collect git statistics")

    if not check_git_repo():
        sys.exit(1)

    # Validate branch if provided
    if branch:
        logger.info("Validating branch/ref '%s'...", branch)
        if not validate_branch(branch):
            logger.error("Branch/ref '%s' not found or not resolvable by git.", branch)
            sys.exit(1)
        logger.debug("Branch '%s' exists.", branch)

    # Validate date inputs (if provided) by asking git to parse them. Use the same ref selection as we'll use later.
    refspec_for_validation = branch if branch else "--all"
    if since:
        logger.info("Validating --from-date/--since value: %s", since)
        if not check_git_date(since, refspec_for_validation):
            logger.error("Invalid or unparseable --from-date/--since: %s", since)
            sys.exit(1)
        logger.debug("--from-date parsed OK: %s", since)
    if until:
        logger.info("Validating --to-date/--until value: %s", until)
        if not check_git_until_date(until, refspec_for_validation):
            logger.error("Invalid or unparseable --to-date/--until: %s", until)
            sys.exit(1)
        logger.debug("--to-date parsed OK: %s", until)

    # Prepare git log command
    sep = "\x01"
    fmt = f"%H{sep}%aN{sep}%aE"
    git_cmd = ["git", "log", f"--pretty=format:{fmt}"]
    if not include_merges:
        git_cmd.append("--no-merges")
    if limit:
        git_cmd.extend(["-n", str(limit)])
    if since:
        git_cmd.append(f"--since={since}")
    if until:
        git_cmd.append(f"--until={until}")
    # Refs selection: if branch provided, analyze that ref; otherwise include --all
    if branch:
        git_cmd.append(branch)
    else:
        git_cmd.append("--all")

    logger.info("Running git log to list commits...")
    try:
        out = run(git_cmd)
    except RuntimeError as e:
        logger.error("git log failed: %s", e)
        sys.exit(1)

    commits = []
    for line in out.splitlines():
        parts = line.split(sep)
        if len(parts) != 3:
            # skip unexpected lines
            continue
        chash, aname, aemail = parts
        commits.append((chash.strip(), aname.strip(), aemail.strip()))

    total_commits = len(commits)
    if total_commits == 0:
        logger.error("No commits found.")
        sys.exit(1)

    logger.info("Found %d commits to process.", total_commits)

    # Aggregation containers
    added_lines = defaultdict(int)
    deleted_lines = defaultdict(int)
    commits_count = defaultdict(int)

    added_chars = defaultdict(int)  # pure added line characters (surplus additions)
    deleted_chars = defaultdict(int)  # pure deleted line characters (surplus deletions)
    modified_chars = defaultdict(
        int
    )  # Levenshtein distance summed for paired del+add lines

    # For canonicalization and display
    # When grouping by email: map norm_email -> set(names), and count email variants
    author_names_by_email = defaultdict(Counter)
    canonical_email = {}  # norm_email -> canonical email string (most common seen)

    # When grouping by name: map norm_name -> set(emails), and count name variants
    emails_by_name = defaultdict(Counter)
    canonical_name = {}

    # Process each commit
    for i, (chash, aname, aemail) in enumerate(commits, start=1):
        norm_name = aname.casefold()
        norm_email = aemail.casefold()

        if group_by == "name":
            key = norm_name
            # record canonical (first-seen / or most common) name and emails
            canonical_name.setdefault(key, aname)
            emails_by_name[key][aemail] += 1
        else:  # group_by == "email"
            key = norm_email
            canonical_email.setdefault(key, aemail)
            author_names_by_email[key][aname] += 1

        commits_count[key] += 1

        logger.debug(
            "Processing commit %s (author=%r email=%r) [%d/%d]",
            chash,
            aname,
            aemail,
            i,
            total_commits,
        )

        # Get patch for this commit. use --unified=0 to reduce context lines
        try:
            patch = run(["git", "show", "--pretty=format:", "--unified=0", chash])
        except RuntimeError:
            # fallback to default unified
            try:
                patch = run(["git", "show", "--pretty=format:", chash])
            except RuntimeError as e:
                logger.error("Failed to get patch for commit %s: %s", chash, e)
                continue

        if not patch:
            logger.debug("Commit %s produced no patch.", chash)
            if progress_every and i % progress_every == 0:
                logger.info("Processed %d/%d commits...", i, total_commits)
            continue

        # parse patch: process hunks and pair '-' and '+' lines in order
        in_hunk = False
        del_buffer = []
        add_buffer = []

        def flush_buffers(local_key):
            """Process buffered del_buffer and add_buffer for author local_key."""
            nonlocal del_buffer, add_buffer
            if not del_buffer and not add_buffer:
                return
            pairs = min(len(del_buffer), len(add_buffer))
            paired_modified_total = 0
            # pair lines and compute Levenshtein
            for idx in range(pairs):
                dline = del_buffer[idx]
                aline = add_buffer[idx]
                dist = levenshtein(dline, aline)
                modified_chars[local_key] += dist
                paired_modified_total += dist
            # surplus added lines -> added_chars
            if len(add_buffer) > pairs:
                for line in add_buffer[pairs:]:
                    added_chars[local_key] += len(line)
            # surplus deleted lines -> deleted_chars
            if len(del_buffer) > pairs:
                for line in del_buffer[pairs:]:
                    deleted_chars[local_key] += len(line)
            logger.debug(
                "flush for %r: pairs=%d, paired_modified_sum=%d, surplus_added=%d, surplus_deleted=%d",
                local_key,
                pairs,
                paired_modified_total,
                max(0, len(add_buffer) - pairs),
                max(0, len(del_buffer) - pairs),
            )
            del_buffer = []
            add_buffer = []

        # Walk patch line by line
        for raw in patch.splitlines():
            if (
                raw.startswith("diff ")
                or raw.startswith("index ")
                or raw.startswith("--- ")
                or raw.startswith("+++ ")
            ):
                # file header lines - flush any buffered hunk changes
                if del_buffer or add_buffer:
                    flush_buffers(key)
                in_hunk = False
                continue
            if raw.startswith("@@"):
                # new hunk - flush previous segment first
                if del_buffer or add_buffer:
                    flush_buffers(key)
                in_hunk = True
                continue
            if not in_hunk:
                continue
            # in a hunk: inspect first char
            if len(raw) == 0:
                # blank line inside patch - treat as context and flush
                if del_buffer or add_buffer:
                    flush_buffers(key)
                continue
            first = raw[0]
            if first == "-":
                line_content = raw[1:]
                if line_content.startswith("\\ No newline"):
                    continue
                deleted_lines[key] += 1
                del_buffer.append(line_content)
            elif first == "+":
                line_content = raw[1:]
                if line_content.startswith("\\ No newline"):
                    continue
                added_lines[key] += 1
                add_buffer.append(line_content)
            else:
                # context line (space) - flush current buffers
                if del_buffer or add_buffer:
                    flush_buffers(key)
                # continue

        # end of patch: flush leftover buffers
        if del_buffer or add_buffer:
            flush_buffers(key)

        if progress_every and i % progress_every == 0:
            logger.info("Processed %d/%d commits...", i, total_commits)

    # Prepare output CSV and write to file
    fieldnames = [
        "author",
        "email",
        "commits",
        "added_lines",
        "deleted_lines",
        "added+deleted_lines",
        "net_lines",
        "added_chars",
        "deleted_chars",
        "modified_chars",
        "added_or_modified_chars",
        "net_chars",
    ]

    try:
        outf = open(output_path, "w", newline="", encoding="utf-8")
    except Exception as e:
        logger.error("Failed to open output file %s: %s", output_path, e)
        sys.exit(1)

    writer = csv.writer(outf, quoting=csv.QUOTE_STRINGS)
    writer.writerow(fieldnames)

    authors = set(
        list(commits_count.keys())
        + list(added_lines.keys())
        + list(added_chars.keys())
        + list(modified_chars.keys())
    )

    def sort_key(a):
        return modified_chars.get(a, 0) + added_chars.get(a, 0)

    for key in sorted(authors, key=sort_key, reverse=True):
        if group_by == "email":
            # email grouping: author column = list of names seen; email column = canonical email
            names_counter = author_names_by_email.get(key, Counter())
            # canonical email: prefer most common original email (we stored canonical_email as first seen)
            email_field = canonical_email.get(key, key)
            # produce a stable ordering: most common names first
            author_list = [name for name, _ in names_counter.most_common()]
            author_field = ";".join(author_list)
        else:
            # name grouping: author column = canonical name, email column = list of emails seen
            author_field = canonical_name.get(key, key)
            emails_counter = emails_by_name.get(key, Counter())
            email_list = [email for email, _ in emails_counter.most_common()]
            email_field = ";".join(email_list)

        added_l = added_lines.get(key, 0)
        deleted_l = deleted_lines.get(key, 0)
        added_plus_deleted_l = added_l + deleted_l
        net_l = added_l - deleted_l

        a_chars = added_chars.get(key, 0)
        d_chars = deleted_chars.get(key, 0)
        m_chars = modified_chars.get(key, 0)
        added_or_modified = m_chars + a_chars
        net_chars = a_chars - d_chars

        writer.writerow(
            [
                author_field,
                email_field,
                commits_count.get(key, 0),
                added_l,
                deleted_l,
                added_plus_deleted_l,
                net_l,
                a_chars,
                d_chars,
                m_chars,
                added_or_modified,
                net_chars,
            ]
        )

    outf.close()

    logger.info(
        "Finished processing %d commits; authors=%d; wrote %s",
        total_commits,
        len(authors),
        output_path,
    )

    # Totals (logged at INFO level)
    tot_added_l = sum(added_lines.values())
    tot_deleted_l = sum(deleted_lines.values())
    tot_added_chars = sum(added_chars.values())
    tot_deleted_chars = sum(deleted_chars.values())
    tot_modified_chars = sum(modified_chars.values())
    logger.info(
        "Totals: added_lines=%d, deleted_lines=%d, added_chars=%d, deleted_chars=%d, modified_chars=%d",
        tot_added_l,
        tot_deleted_l,
        tot_added_chars,
        tot_deleted_chars,
        tot_modified_chars,
    )


if __name__ == "__main__":
    main()
