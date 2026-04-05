"""Prompt construction helpers for rule-based clarification question generation.

This module contains the keyword-to-question mapping logic extracted from
``OmniCoreService._generate_clarification_questions``.  Each builder function
checks the lowered requirements text for domain-specific keywords and returns
a question dict (or ``None`` if the domain is already specified).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _build_database_question(req_lower: str, counter: int) -> Optional[Dict[str, str]]:
    """Return a database clarification question if the domain is ambiguous."""
    triggers = ["database", "data", "store", "save", "persist", "storage", "db"]
    specifics = [
        "mysql", "postgres", "postgresql", "mongodb", "sqlite", "redis",
        "dynamodb", "firestore", "cassandra", "mariadb",
    ]
    if any(w in req_lower for w in triggers):
        if not any(db in req_lower for db in specifics):
            return {
                "id": f"q{counter}",
                "question": (
                    "What type of database would you like to use? "
                    "(e.g., PostgreSQL, MongoDB, MySQL)"
                ),
                "category": "database",
            }
    return None


def _build_auth_question(req_lower: str, counter: int) -> Optional[Dict[str, str]]:
    """Return an authentication clarification question if the domain is ambiguous."""
    triggers = ["user", "login", "auth", "account", "sign", "authentication", "credential"]
    specifics = ["jwt", "oauth", "session", "token", "saml", "auth0", "cognito", "firebase auth"]
    if any(w in req_lower for w in triggers):
        if not any(a in req_lower for a in specifics):
            return {
                "id": f"q{counter}",
                "question": (
                    "What authentication method should be used? "
                    "(e.g., JWT, OAuth 2.0, session-based)"
                ),
                "category": "authentication",
            }
    return None


def _build_api_question(req_lower: str, counter: int) -> Optional[Dict[str, str]]:
    """Return an API type clarification question if the domain is ambiguous."""
    triggers = ["api", "endpoint", "rest", "graphql", "service"]
    if any(w in req_lower for w in triggers):
        if "rest" not in req_lower and "graphql" not in req_lower and "grpc" not in req_lower:
            return {
                "id": f"q{counter}",
                "question": "Should the API be RESTful or GraphQL?",
                "category": "api",
            }
    return None


def _build_frontend_question(req_lower: str, counter: int) -> Optional[Dict[str, str]]:
    """Return a frontend framework clarification question if ambiguous."""
    triggers = ["web", "frontend", "ui", "interface", "dashboard", "client", "browser"]
    specifics = ["react", "vue", "angular", "svelte", "next", "nextjs", "nuxt", "gatsby"]
    if any(w in req_lower for w in triggers):
        if not any(fw in req_lower for fw in specifics):
            return {
                "id": f"q{counter}",
                "question": (
                    "What frontend framework would you prefer? "
                    "(e.g., React, Vue.js, Angular)"
                ),
                "category": "frontend",
            }
    return None


def _build_deployment_question(req_lower: str, counter: int) -> Optional[Dict[str, str]]:
    """Return a deployment platform clarification question if ambiguous."""
    triggers = ["deploy", "host", "production", "server", "cloud", "infrastructure"]
    specifics = [
        "docker", "kubernetes", "k8s", "aws", "azure", "gcp",
        "heroku", "vercel", "netlify",
    ]
    if any(w in req_lower for w in triggers):
        if not any(p in req_lower for p in specifics):
            return {
                "id": f"q{counter}",
                "question": (
                    "What deployment platform will you use? "
                    "(e.g., Docker, Kubernetes, AWS, Heroku)"
                ),
                "category": "deployment",
            }
    return None


def _build_testing_question(req_lower: str, counter: int) -> Optional[Dict[str, str]]:
    """Return a testing strategy clarification question if ambiguous."""
    if "test" in req_lower:
        specifics = ["unit", "integration", "e2e", "end-to-end", "pytest", "jest", "mocha"]
        if not any(t in req_lower for t in specifics):
            return {
                "id": f"q{counter}",
                "question": (
                    "What types of tests should be included? "
                    "(e.g., unit tests, integration tests, e2e tests)"
                ),
                "category": "testing",
            }
    return None


def _build_performance_question(req_lower: str, counter: int) -> Optional[Dict[str, str]]:
    """Return a performance requirements clarification question."""
    triggers = ["performance", "scale", "load", "concurrent"]
    if any(w in req_lower for w in triggers):
        return {
            "id": f"q{counter}",
            "question": (
                "What are your expected performance requirements? "
                "(e.g., number of concurrent users, response time SLAs)"
            ),
            "category": "performance",
        }
    return None


def _build_security_question(req_lower: str, counter: int) -> Optional[Dict[str, str]]:
    """Return a security requirements clarification question if ambiguous."""
    triggers = ["secure", "security", "encrypt", "protect"]
    if any(w in req_lower for w in triggers):
        if "encrypt" not in req_lower:
            return {
                "id": f"q{counter}",
                "question": (
                    "What security measures are required? "
                    "(e.g., data encryption at rest/in transit, HTTPS, rate limiting)"
                ),
                "category": "security",
            }
    return None


# Ordered list of all builders -- iteration order determines question order.
QUESTION_BUILDERS = [
    _build_database_question,
    _build_auth_question,
    _build_api_question,
    _build_frontend_question,
    _build_deployment_question,
    _build_testing_question,
    _build_performance_question,
    _build_security_question,
]

MAX_QUESTIONS = 5


def build_rule_based_questions(requirements: str) -> List[Dict[str, str]]:
    """Generate rule-based clarification questions from raw requirements text.

    Args:
        requirements: The raw requirements / README content.

    Returns:
        A list of question dicts (max ``MAX_QUESTIONS``) each containing
        ``id``, ``question``, and ``category`` keys.
    """
    req_lower = requirements.lower()
    questions: List[Dict[str, str]] = []
    counter = 1

    for builder in QUESTION_BUILDERS:
        q = builder(req_lower, counter)
        if q is not None:
            questions.append(q)
            counter += 1

    # Bug 2 Fix: Return empty list if no ambiguities detected (no generic fallback)
    return questions[:MAX_QUESTIONS]
