# self_fixing_engineer/exceptions.py
"""
Centralized exception definitions for the self-fixing engineer system.
These exceptions are used across multiple components to ensure consistent error handling.
"""


class AnalyzerCriticalError(RuntimeError):
    """
    Raised for unrecoverable analyzer / plugin errors.
    
    This exception indicates a critical failure that cannot be recovered from
    and requires operator intervention or system restart.
    """
    pass


class NonCriticalError(Exception):
    """
    Raised for recoverable/non-fatal errors handled by caller.
    
    This exception indicates an error that can be handled gracefully
    without causing system-wide failure.
    """
    pass
