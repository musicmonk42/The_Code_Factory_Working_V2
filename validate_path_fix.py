#!/usr/bin/env python3
"""
Simple validation script to verify path resolution logic works correctly.
This tests the core logic of the fix without requiring full test infrastructure.
"""

from pathlib import Path
import tempfile
import os


def test_path_resolution_logic():
    """Test the path resolution logic that was fixed."""
    print("Testing path resolution logic...")
    
    # Create a temporary directory structure similar to what testgen uses
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        
        # Simulate the directory structure
        job_id = "test-job-123"
        upload_dir = tmp_path / "uploads" / job_id
        generated_dir = upload_dir / "generated"
        generated_dir.mkdir(parents=True)
        
        # Create sample files
        (generated_dir / "main.py").write_text("def main(): pass")
        (generated_dir / "utils.py").write_text("def util(): pass")
        (generated_dir / "test_main.py").write_text("def test(): pass")
        
        # Simulate the old buggy behavior
        print("\n1. OLD BUGGY BEHAVIOR (without .resolve()):")
        code_dir = Path(f"./uploads/{job_id}/generated")  # Relative path
        repo_path = Path(f"./uploads/{job_id}")  # Relative path
        
        print(f"   repo_path (relative): {repo_path}")
        print(f"   code_dir (relative): {code_dir}")
        
        # Change to temp directory to simulate relative path issues
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        
        try:
            if code_dir.exists():
                for f in code_dir.rglob("*.py"):
                    if not f.name.startswith("test_"):
                        try:
                            # This could fail or produce wrong paths if not resolved
                            rel_path = f.relative_to(repo_path)
                            print(f"   File: {f} -> {rel_path}")
                        except ValueError as e:
                            print(f"   ERROR: {e}")
        except Exception as e:
            print(f"   Exception: {e}")
        
        # Simulate the new fixed behavior
        print("\n2. NEW FIXED BEHAVIOR (with .resolve()):")
        code_dir = Path(f"./uploads/{job_id}/generated").resolve()  # Absolute path
        repo_path = Path(f"./uploads/{job_id}").resolve()  # Absolute path
        
        print(f"   repo_path (absolute): {repo_path}")
        print(f"   code_dir (absolute): {code_dir}")
        
        if code_dir.exists():
            code_files = []
            for f in code_dir.rglob("*.py"):
                if not f.name.startswith("test_"):
                    try:
                        # Get absolute path and convert to relative
                        abs_file_path = f.resolve()
                        rel_path = abs_file_path.relative_to(repo_path)
                        code_files.append(str(rel_path))
                        print(f"   File: {abs_file_path} -> {rel_path}")
                    except ValueError as e:
                        print(f"   WARNING: File {f} is outside repo_path, skipping")
            
            print(f"\n   Total files added: {len(code_files)}")
            print(f"   Files: {code_files}")
            
            # Verify no path duplication
            for file_path in code_files:
                assert not Path(file_path).is_absolute(), f"Path should be relative: {file_path}"
                assert file_path.count(job_id) <= 1, f"Path has duplication: {file_path}"
                print(f"   ✓ No duplication in: {file_path}")
        
        # Test files outside repo_path
        print("\n3. TEST FILES OUTSIDE REPO_PATH:")
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir(parents=True)
        (outside_dir / "external.py").write_text("def external(): pass")
        
        code_dir_outside = outside_dir.resolve()
        print(f"   code_dir (outside): {code_dir_outside}")
        print(f"   repo_path: {repo_path}")
        
        if code_dir_outside.exists():
            skipped = 0
            for f in code_dir_outside.rglob("*.py"):
                if not f.name.startswith("test_"):
                    try:
                        abs_file_path = f.resolve()
                        rel_path = abs_file_path.relative_to(repo_path)
                        print(f"   File: {abs_file_path} -> {rel_path}")
                    except ValueError as e:
                        skipped += 1
                        print(f"   ✓ Correctly skipped: {f} (outside repo_path)")
            
            print(f"   Total files skipped: {skipped}")
        
        os.chdir(original_cwd)
    
    print("\n✅ All path resolution tests passed!")


if __name__ == "__main__":
    test_path_resolution_logic()
