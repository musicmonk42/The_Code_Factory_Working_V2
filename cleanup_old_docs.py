#!/usr/bin/env python3
"""
Cleanup Old Audit and Testing Documentation
============================================
This script removes audit and testing documentation files that are older than
a specified number of days (default: 2 days).

Usage:
    python cleanup_old_docs.py [--days DAYS] [--dry-run]

Options:
    --days DAYS    Number of days to keep files (default: 2)
    --dry-run      Show what would be deleted without actually deleting
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple


# Patterns for files to clean up
# Note: glob patterns are case-sensitive on Unix-like systems
FILE_PATTERNS = [
    "*AUDIT*.md",
    "*audit*.md",
    "*TEST*.md", 
    "*test*.md",
    "*_REPORT.md",
    "*_report.md",
    "*.log.json",
    "dlt_audit*",
    "*audit*.json*",
]

# Files to exclude from cleanup (important documentation)
EXCLUDE_FILES = [
    "README.md",
    "DEPLOYMENT.md",
    "QUICKSTART.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "SECURITY.md",
    ".env.example",
    ".env.production.template",
]

# Directories to exclude from cleanup
EXCLUDE_DIRS = [
    ".git",
    ".github",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "venv",
    ".venv",
    "dist",
    "build",
    "htmlcov",
]


def get_file_age_days(file_path: Path) -> float:
    """Get the age of a file in days based on modification time."""
    mtime = file_path.stat().st_mtime
    file_datetime = datetime.fromtimestamp(mtime, tz=timezone.utc)
    age = datetime.now(timezone.utc) - file_datetime
    return age.total_seconds() / 86400  # Convert to days


def should_exclude_path(file_path: Path, base_path: Path) -> bool:
    """Check if a path should be excluded from cleanup."""
    # Check if file is in exclude list
    if file_path.name in EXCLUDE_FILES:
        return True
    
    # Check if file is in an excluded directory
    try:
        relative_path = file_path.relative_to(base_path)
        for part in relative_path.parts:
            if part in EXCLUDE_DIRS:
                return True
    except ValueError:
        pass
    
    return False


def find_old_files(base_path: Path, days: int = 2) -> List[Tuple[Path, float]]:
    """
    Find all audit/test files older than specified days.
    
    Returns:
        List of tuples (file_path, age_in_days)
    """
    old_files = []
    
    for pattern in FILE_PATTERNS:
        # Search only in the root directory to avoid deep traversal
        for file_path in base_path.glob(pattern):
            if not file_path.is_file():
                continue
                
            if should_exclude_path(file_path, base_path):
                continue
            
            age_days = get_file_age_days(file_path)
            if age_days > days:
                old_files.append((file_path, age_days))
    
    return sorted(old_files, key=lambda x: x[1], reverse=True)


def format_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def cleanup_old_files(base_path: Path, days: int = 2, dry_run: bool = False) -> None:
    """
    Remove old audit and testing documentation files.
    
    Args:
        base_path: Base directory to search
        days: Files older than this many days will be removed
        dry_run: If True, only show what would be deleted
    """
    print(f"Scanning for audit/test documents older than {days} days...")
    print(f"Base path: {base_path}")
    print()
    
    old_files = find_old_files(base_path, days)
    
    if not old_files:
        print("✓ No old audit/test documents found.")
        return
    
    print(f"Found {len(old_files)} file(s) to clean up:")
    print()
    
    total_size = 0
    for file_path, age_days in old_files:
        file_size = file_path.stat().st_size
        total_size += file_size
        
        relative_path = file_path.relative_to(base_path)
        print(f"  • {relative_path}")
        print(f"    Age: {age_days:.1f} days, Size: {format_size(file_size)}")
    
    print()
    print(f"Total size to free: {format_size(total_size)}")
    print()
    
    if dry_run:
        print("DRY RUN: No files were deleted.")
        print("Run without --dry-run to actually delete these files.")
    else:
        confirmation = input("Delete these files? [y/N]: ")
        if confirmation.lower() in ['y', 'yes']:
            deleted_count = 0
            for file_path, _ in old_files:
                try:
                    file_path.unlink()
                    deleted_count += 1
                    print(f"  ✓ Deleted: {file_path.relative_to(base_path)}")
                except Exception as e:
                    print(f"  ✗ Error deleting {file_path.relative_to(base_path)}: {e}")
            
            print()
            print(f"✓ Successfully deleted {deleted_count} file(s).")
        else:
            print("Cancelled. No files were deleted.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Clean up old audit and testing documentation files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--days",
        type=int,
        default=2,
        help="Number of days to keep files (default: 2)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt"
    )
    
    args = parser.parse_args()
    
    # Get the repository root (where this script is located)
    base_path = Path(__file__).parent.resolve()
    
    if args.yes and not args.dry_run:
        # Auto-confirm deletion
        print(f"Scanning for audit/test documents older than {args.days} days...")
        print(f"Base path: {base_path}")
        print()
        
        old_files = find_old_files(base_path, args.days)
        
        if not old_files:
            print("✓ No old audit/test documents found.")
            return
        
        deleted_count = 0
        for file_path, age_days in old_files:
            try:
                file_path.unlink()
                deleted_count += 1
                print(f"  ✓ Deleted: {file_path.relative_to(base_path)} (age: {age_days:.1f} days)")
            except Exception as e:
                print(f"  ✗ Error deleting {file_path.relative_to(base_path)}: {e}")
        
        print()
        print(f"✓ Successfully deleted {deleted_count} file(s).")
    else:
        cleanup_old_files(base_path, args.days, args.dry_run)


if __name__ == "__main__":
    main()
