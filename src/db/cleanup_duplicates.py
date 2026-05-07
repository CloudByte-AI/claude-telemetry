"""
Cleanup Duplicate Projects

Fixes duplicate project entries caused by case/slash differences in paths.
Consolidates sessions from duplicate projects into a single canonical project.
"""

import hashlib
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.manager import get_db_connection, get_db_manager
from src.common.logging import get_logger, setup_logging

logger = get_logger(__name__)


def normalize_path_for_id(project_path: str) -> str:
    """Normalize path the same way generate_project_id now does."""
    normalized = project_path.strip().lower().replace("\\", "/").rstrip("/")
    return normalized


def generate_new_project_id(project_path: str) -> str:
    """Generate project ID using the new normalization logic."""
    normalized = normalize_path_for_id(project_path)
    return hashlib.md5(normalized.encode()).hexdigest()


def find_duplicate_projects(conn) -> list:
    """
    Find projects that would have the same ID under new normalization.

    Returns:
        List of groups, where each group is a list of (project_id, path) tuples
        that would collapse to the same new project_id.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT project_id, path FROM PROJECT")
    all_projects = cursor.fetchall()

    # Group by new normalized ID
    groups = {}
    for old_id, path in all_projects:
        if not path:
            continue
        new_id = generate_new_project_id(path)
        if new_id not in groups:
            groups[new_id] = []
        groups[new_id].append((old_id, path))

    # Return only groups with duplicates (more than 1)
    duplicates = [g for g in groups.values() if len(g) > 1]
    return duplicates


def cleanup_duplicates(dry_run: bool = True) -> dict:
    """
    Cleanup duplicate projects.

    Args:
        dry_run: If True, only report what would be done without executing

    Returns:
        dict with stats about the cleanup
    """
    conn = get_db_connection()
    stats = {
        "duplicate_groups": 0,
        "projects_deleted": 0,
        "sessions_migrated": 0,
        "errors": []
    }

    try:
        duplicates = find_duplicate_projects(conn)

        if not duplicates:
            logger.info("No duplicate projects found!")
            return stats

        stats["duplicate_groups"] = len(duplicates)
        logger.info(f"Found {len(duplicates)} groups of duplicate projects")

        for group in duplicates:
            # Sort by path length - prefer shorter paths (usually the "real" path)
            group.sort(key=lambda x: len(x[1]))

            # Keep the first one (shortest path), migrate others to it
            keep_id, keep_path = group[0]
            new_canonical_id = generate_new_project_id(keep_path)

            logger.info(f"\n--- Duplicate Group ---")
            logger.info(f"Keeping: {keep_path} (id: {keep_id})")

            for old_id, old_path in group[1:]:
                logger.info(f"  Merging: {old_path} (id: {old_id})")

                # Count sessions to migrate
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM SESSION WHERE project_id = ?",
                    (old_id,)
                )
                session_count = cursor.fetchone()[0]
                stats["sessions_migrated"] += session_count

                if dry_run:
                    logger.info(f"    [DRY RUN] Would migrate {session_count} sessions")
                else:
                    # Migrate sessions to the kept project
                    cursor.execute(
                        "UPDATE SESSION SET project_id = ? WHERE project_id = ?",
                        (keep_id, old_id)
                    )
                    # Delete the duplicate project
                    cursor.execute(
                        "DELETE FROM PROJECT WHERE project_id = ?",
                        (old_id,)
                    )
                    conn.commit()
                    logger.info(f"    Migrated {session_count} sessions, deleted duplicate")

                stats["projects_deleted"] += 1

        # Finally, update the kept project to use the new normalized ID
        if not dry_run:
            logger.info("\n--- Updating canonical project IDs ---")
            cursor = conn.cursor()

            # Temporarily disable foreign key constraints for the update
            cursor.execute("PRAGMA foreign_keys = OFF;")

            for group in duplicates:
                keep_id, keep_path = group[0]
                new_id = generate_new_project_id(keep_path)

                if keep_id != new_id:
                    # Update sessions first (child records)
                    cursor.execute(
                        "UPDATE SESSION SET project_id = ? WHERE project_id = ?",
                        (new_id, keep_id)
                    )
                    # Then update project (parent record)
                    cursor.execute(
                        "UPDATE PROJECT SET project_id = ? WHERE project_id = ?",
                        (new_id, keep_id)
                    )
                    conn.commit()
                    logger.info(f"Updated {keep_path} -> new_id: {new_id}")

            # Re-enable foreign key constraints
            cursor.execute("PRAGMA foreign_keys = ON;")

        logger.info(f"\n=== Cleanup Summary ===")
        logger.info(f"Duplicate groups: {stats['duplicate_groups']}")
        logger.info(f"Projects deleted: {stats['projects_deleted']}")
        logger.info(f"Sessions migrated: {stats['sessions_migrated']}")

        if dry_run:
            logger.info("\n[DRY RUN] No changes made. Run with dry_run=False to apply.")

    except Exception as e:
        stats["errors"].append(str(e))
        logger.error(f"Error during cleanup: {e}", exc_info=True)

    return stats


def main():
    """Main entry point."""
    setup_logging(log_to_file=True, log_to_console=True)

    import argparse
    parser = argparse.ArgumentParser(description="Cleanup duplicate projects")
    parser.add_argument("--apply", action="store_true",
                        help="Actually apply changes (default is dry-run)")
    args = parser.parse_args()

    if args.apply:
        logger.info("=== Running Duplicate Project Cleanup (APPLY MODE) ===")
        cleanup_duplicates(dry_run=False)
    else:
        logger.info("=== Running Duplicate Project Cleanup (DRY RUN) ===")
        cleanup_duplicates(dry_run=True)


if __name__ == "__main__":
    main()
