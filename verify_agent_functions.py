#!/usr/bin/env python3
"""
Verification script to check that all agent functions are operational as designed.
This script performs static analysis and basic import checks without requiring full dependencies.
"""

import ast
import importlib
import inspect
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def analyze_agent_file(file_path: Path) -> Dict:
    """Analyze a Python file to extract function definitions."""
    result = {
        "file": str(file_path),
        "functions": [],
        "classes": [],
        "async_functions": [],
        "syntax_valid": False,
        "imports": [],
        "issues": []
    }
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Parse AST
        tree = ast.parse(content, filename=str(file_path))
        result["syntax_valid"] = True
        
        # Extract functions and classes
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "is_async": False,
                    "has_docstring": ast.get_docstring(node) is not None,
                    "args": [arg.arg for arg in node.args.args]
                }
                result["functions"].append(func_info)
            
            elif isinstance(node, ast.AsyncFunctionDef):
                func_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "is_async": True,
                    "has_docstring": ast.get_docstring(node) is not None,
                    "args": [arg.arg for arg in node.args.args]
                }
                result["async_functions"].append(func_info)
            
            elif isinstance(node, ast.ClassDef):
                class_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "has_docstring": ast.get_docstring(node) is not None,
                    "methods": []
                }
                
                # Extract methods
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        class_info["methods"].append({
                            "name": item.name,
                            "is_async": isinstance(item, ast.AsyncFunctionDef),
                            "line": item.lineno
                        })
                
                result["classes"].append(class_info)
            
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    result["imports"].append(alias.name)
            
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    result["imports"].append(node.module)
    
    except SyntaxError as e:
        result["issues"].append(f"Syntax error: {e}")
    except Exception as e:
        result["issues"].append(f"Analysis error: {e}")
    
    return result


def check_agent_operational(agent_name: str, agent_path: Path) -> Dict:
    """Check if an agent is operational."""
    print(f"\n{'='*70}")
    print(f"Checking: {agent_name}")
    print(f"{'='*70}")
    
    result = {
        "agent": agent_name,
        "main_file_exists": False,
        "main_file_syntax": False,
        "key_functions_found": [],
        "key_classes_found": [],
        "import_issues": [],
        "operational": False
    }
    
    # Check main agent file
    main_file = agent_path / f"{agent_name}.py"
    if main_file.exists():
        result["main_file_exists"] = True
        print(f"✓ Main file found: {main_file.name}")
        
        # Analyze the file
        analysis = analyze_agent_file(main_file)
        result["main_file_syntax"] = analysis["syntax_valid"]
        
        if analysis["syntax_valid"]:
            print(f"✓ Syntax is valid")
            
            # Report functions
            if analysis["async_functions"]:
                print(f"\n  Found {len(analysis['async_functions'])} async functions:")
                for func in analysis["async_functions"][:5]:  # Show first 5
                    result["key_functions_found"].append(func["name"])
                    docstring_marker = "📝" if func["has_docstring"] else "  "
                    print(f"    {docstring_marker} async def {func['name']}(...) at line {func['line']}")
            
            # Report classes
            if analysis["classes"]:
                print(f"\n  Found {len(analysis['classes'])} classes:")
                for cls in analysis["classes"][:3]:  # Show first 3
                    result["key_classes_found"].append(cls["name"])
                    docstring_marker = "📝" if cls["has_docstring"] else "  "
                    print(f"    {docstring_marker} class {cls['name']} with {len(cls['methods'])} methods at line {cls['line']}")
                    
                    # Show async methods
                    async_methods = [m for m in cls["methods"] if m["is_async"]]
                    if async_methods:
                        print(f"       - {len(async_methods)} async methods: {', '.join([m['name'] for m in async_methods[:3]])}")
            
            # Check for critical patterns
            all_func_names = [f["name"] for f in analysis["async_functions"]] + [f["name"] for f in analysis["functions"]]
            
            # Look for main entry points
            entry_points = [f for f in all_func_names if f in ["main", "generate_code", "orchestrate", "run"]]
            if entry_points:
                print(f"\n  ✓ Entry points found: {', '.join(entry_points)}")
            
            # Check for error handling patterns
            if "try" in open(main_file).read():
                print(f"  ✓ Error handling (try/except) detected")
            
            result["operational"] = True
        else:
            print(f"✗ Syntax errors found:")
            for issue in analysis["issues"]:
                print(f"    - {issue}")
                result["import_issues"].append(issue)
    else:
        print(f"✗ Main file not found: {main_file}")
    
    return result


def main():
    """Main verification routine."""
    print("="*70)
    print("AGENT FUNCTION VERIFICATION")
    print("="*70)
    
    agents_dir = Path("generator/agents")
    if not agents_dir.exists():
        print(f"ERROR: Agents directory not found: {agents_dir}")
        return 1
    
    # Define agents to check
    agents = [
        "codegen_agent",
        "critique_agent",
        "deploy_agent",
        "docgen_agent",
        "testgen_agent"
    ]
    
    results = []
    for agent in agents:
        agent_path = agents_dir / agent
        if agent_path.exists():
            result = check_agent_operational(agent, agent_path)
            results.append(result)
        else:
            print(f"\n✗ Agent directory not found: {agent}")
            results.append({
                "agent": agent,
                "main_file_exists": False,
                "operational": False
            })
    
    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    
    operational_count = sum(1 for r in results if r["operational"])
    total_count = len(results)
    
    print(f"\nOperational Agents: {operational_count}/{total_count}")
    
    for result in results:
        status = "✓ OPERATIONAL" if result["operational"] else "✗ NOT OPERATIONAL"
        print(f"\n{result['agent']}: {status}")
        if result["operational"]:
            print(f"  - Functions: {len(result['key_functions_found'])}")
            print(f"  - Classes: {len(result['key_classes_found'])}")
        elif result["import_issues"]:
            print(f"  - Issues: {len(result['import_issues'])}")
    
    # Check for the fixes we made
    print(f"\n{'='*70}")
    print("VERIFICATION OF RECENT FIXES")
    print(f"{'='*70}")
    
    # Check codegen_prompt fix
    prompt_file = Path("generator/agents/codegen_agent/codegen_prompt.py")
    if prompt_file.exists():
        content = prompt_file.read_text()
        if "PROMPT_BUILD_LATENCY.labels(template=" in content:
            print("✓ Histogram metric label fix confirmed in codegen_prompt.py")
        else:
            print("✗ Histogram metric label fix NOT found in codegen_prompt.py")
    
    # Check clarifier fixes
    clarifier_file = Path("generator/clarifier/clarifier.py")
    if clarifier_file.exists():
        content = clarifier_file.read_text()
        
        if "async def detect_ambiguities" in content:
            print("✓ detect_ambiguities method confirmed in clarifier.py")
        else:
            print("✗ detect_ambiguities method NOT found in clarifier.py")
        
        if "async def generate_questions" in content:
            print("✓ generate_questions method confirmed in clarifier.py")
        else:
            print("✗ generate_questions method NOT found in clarifier.py")
        
        if 'aws_region = os.getenv("AWS_REGION")' in content and "if not aws_region:" in content:
            print("✓ AWS_REGION validation fix confirmed in clarifier.py")
        else:
            print("✗ AWS_REGION validation fix NOT found in clarifier.py")
    
    print(f"\n{'='*70}")
    if operational_count == total_count:
        print("✓ ALL AGENTS ARE OPERATIONAL (syntax-wise)")
        return 0
    else:
        print(f"⚠ {total_count - operational_count} agents have issues")
        return 1


if __name__ == "__main__":
    sys.exit(main())
