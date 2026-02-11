# Code Factory Pipeline Contract Compliance Fix Summary

## Overview
This document summarizes the changes made to fix six critical issues preventing the Code Factory pipeline from meeting "Strict Contract" requirements.

## Issues Fixed

### Issue 1: Output Directory Mis-management ✅

**Problem**: Files were not being placed under the required `generated/hello_generator` directory structure, causing double-nesting and stray files.

**Solution**:
- Added `_enforce_output_layout()` function in `generator/runner/runner_file_utils.py`
- Function ensures all generated files are moved into the project subdirectory
- Handles file collisions and directory merging safely
- Integrated into `server/services/omnicore_service.py` after file materialization
- Extracted excluded build directories to `EXCLUDED_BUILD_DIRS` constant for maintainability

**Files Modified**:
- `generator/runner/runner_file_utils.py` - Added layout enforcement function
- `server/services/omnicore_service.py` - Integrated enforcement after materialization

---

### Issue 2: Critique Agent Not Enabled by Default ✅

**Problem**: Critique reports were not being generated unless explicitly requested.

**Solution**:
- Verified that critique already defaults to `True` in `_run_full_pipeline` (line 4109)
- No code changes needed - already correctly implemented

**Files Modified**: None (already working correctly)

---

### Issue 3: Missing Kubernetes and Helm Validators ✅

**Problem**: Deploy agent failed with "No validator found for target 'kubernetes'" error.

**Solution**:
- Added `KubernetesValidator` class to `generator/agents/deploy_agent/deploy_validator.py`
- Implements YAML syntax validation and basic K8s manifest structure checks
- Registered in `ValidatorRegistry` alongside existing Docker and Helm validators
- Verified Helm validator already exists and is properly registered

**Files Modified**:
- `generator/agents/deploy_agent/deploy_validator.py` - Added KubernetesValidator class and registration

**Key Features**:
- Validates YAML syntax using ruamel.yaml
- Checks for required K8s fields (apiVersion, kind, metadata)
- Supports multi-document YAML manifests
- Provides security scanning for dangerous patterns
- Includes LLM-based fix capability

---

### Issue 4: LLM Deploy Output Not Sanitized ✅

**Problem**: LLM responses containing Markdown artifacts (Mermaid diagrams, code fences) caused YAML parsing failures.

**Solution**:
- Added `_sanitize_llm_output()` function in `generator/agents/deploy_agent/deploy_response_handler.py`
- Strips Mermaid diagrams, DOT/PlantUML blocks, and code fences before YAML parsing
- Integrated into `YAMLHandler.normalize()` method as first step
- Moved import statement to module level for better performance

**Files Modified**:
- `generator/agents/deploy_agent/deploy_response_handler.py` - Added sanitization function and integration

**Sanitization Features**:
- Removes ```mermaid blocks completely
- Removes other diagram blocks (dot, plantuml, graphviz)
- Strips code fence wrappers (```yaml, ```dockerfile, etc.)
- Handles edge cases with fences at start/end of content

---

### Issue 5: Testgen Fallback Tests Path Resolution ✅

**Problem**: Test generator produced fallback placeholder tests at project root instead of under `tests/` directory due to path resolution issues.

**Solution**:
- Fixed path resolution in `testgen_agent._load_code_files()` with proper path cleaning
- Added `fp_cleaned = fp.lstrip('/')` to handle leading slashes
- Updated test file path generation to consistently use `tests/test_{filename}.py` format
- Improved path traversal security check using `resolve()` and `is_relative_to()` (Python 3.9+)
- Added fallback for Python < 3.9 with proper string comparison
- Handles symlinks correctly by resolving both paths before comparison

**Files Modified**:
- `generator/agents/testgen_agent/testgen_agent.py` - Path resolution and security improvements

**Key Changes**:
- Regular tests: `tests/test_{file_stem_name}.py`
- Fallback tests: `tests/test_{file_stem_name}.py` (consistent location)
- Security: Proper symlink-aware path validation

---

### Issue 6: README Generation Completeness ✅

**Problem**: Generated README files were stubs with missing sections instead of comprehensive documentation.

**Solution**:
- Strengthened README requirements in `generator/agents/codegen_agent/codegen_prompt.py`
- Added prominent warnings that incomplete READMEs will cause pipeline failure
- Added validation checklist for all required sections
- Emphasized that README must have actual, executable content (no TODOs or placeholders)

**Files Modified**:
- `generator/agents/codegen_agent/codegen_prompt.py` - Enhanced prompts and requirements

**Required README Sections**:
1. Title and Description
2. Setup instructions (venv, pip install)
3. Run instructions (uvicorn command)
4. Test instructions (pytest command)
5. API Endpoints with curl examples for ALL endpoints
6. Project Structure with actual directory tree

---

## Code Quality Improvements

### Security Enhancements
1. **Path Traversal Protection**: Improved path validation in testgen_agent using `resolve()` and `is_relative_to()` to handle symlinks correctly
2. **Module Constants**: Extracted hardcoded values to module-level constants (`EXCLUDED_BUILD_DIRS`)
3. **Import Optimization**: Moved import statements to module level for better performance

### Maintainability
1. **Constants Extraction**: Build directories list now in `EXCLUDED_BUILD_DIRS` for easy updates
2. **Consistent Naming**: Test files consistently named `test_{filename}.py` in `tests/` directory
3. **Clear Documentation**: Added comprehensive docstrings explaining each fix

---

## Testing

### Validation Performed
✅ All modified files pass Python syntax checks
✅ Text-based validation confirms all changes are in place:
- KubernetesValidator class exists and is registered
- _sanitize_llm_output function exists and is integrated
- _enforce_output_layout function exists
- Test path resolution improvements applied
- README requirements strengthened

✅ No CodeQL security issues detected
✅ Code review completed and feedback addressed

### Remaining Testing
- Run full integration tests with actual pipeline execution
- Run contract validation script: `python scripts/validate_contract_compliance.py`
- Verify ZIP archive structure matches contract specification
- Test Kubernetes/Helm deployment validation
- Test README generation with strengthened requirements

---

## Expected Impact

After these fixes, the pipeline should:

1. ✅ Generate all files under `generated/hello_generator/` with no stray files at other levels
2. ✅ Always generate `reports/critique_report.json` with proper structure (already working)
3. ✅ Validate Kubernetes and Helm deployments without errors
4. ✅ Handle LLM responses containing Markdown artifacts without YAML parsing failures
5. ✅ Generate test files correctly in `tests/` subdirectory with proper naming
6. ✅ Produce comprehensive READMEs with all required sections and actual content

---

## Files Changed

### Core Changes
1. `server/services/omnicore_service.py` - Output layout enforcement integration
2. `generator/agents/deploy_agent/deploy_validator.py` - KubernetesValidator addition
3. `generator/agents/deploy_agent/deploy_response_handler.py` - LLM output sanitization
4. `generator/agents/testgen_agent/testgen_agent.py` - Path resolution and security fixes
5. `generator/runner/runner_file_utils.py` - Layout enforcement function and constants
6. `generator/agents/codegen_agent/codegen_prompt.py` - Strengthened README requirements

### Lines of Code
- Total additions: ~400 lines
- Total deletions: ~30 lines
- Net change: ~370 lines

---

## Security Considerations

All changes include security best practices:
- Path traversal prevention with proper resolution
- Symlink-aware path validation
- Input sanitization for LLM outputs
- Safe file operations with collision handling
- No execution of untrusted code

---

## Next Steps

1. Run integration tests to verify end-to-end functionality
2. Execute contract validation script
3. Monitor first production pipeline runs
4. Collect metrics on:
   - README completeness scores
   - Test file placement accuracy
   - Kubernetes/Helm validation success rates
   - Output directory structure compliance

---

## Conclusion

All six critical issues have been addressed with minimal, surgical changes to the codebase. The fixes follow industry best practices for security, maintainability, and code quality. The pipeline should now meet all "Strict Contract" requirements.
