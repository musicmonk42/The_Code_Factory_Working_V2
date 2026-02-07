# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import os


def test_decision_optimizer_settings_env_pollution(monkeypatch):
    # Print the status and value before any config import
    print(
        "DECISION_OPTIMIZER_SETTINGS in env at test start:",
        "DECISION_OPTIMIZER_SETTINGS" in os.environ,
    )
    print(
        "DECISION_OPTIMIZER_SETTINGS value:",
        os.environ.get("DECISION_OPTIMIZER_SETTINGS"),
    )

    # Remove it for this test and print again
    os.environ.pop("DECISION_OPTIMIZER_SETTINGS", None)
    print(
        "After pop, DECISION_OPTIMIZER_SETTINGS in env:",
        "DECISION_OPTIMIZER_SETTINGS" in os.environ,
    )

    # Now import config and try to instantiate
    from self_fixing_engineer.arbiter.policy.config import ArbiterConfig

    # Try to instantiate and print result
    try:
        config = ArbiterConfig()
        print(
            "ArbiterConfig.DECISION_OPTIMIZER_SETTINGS keys:",
            list(config.DECISION_OPTIMIZER_SETTINGS.keys()),
        )
    except Exception as e:
        print("ArbiterConfig failed to initialize:", e)
        raise

    # Check for pollution
    pollution_keys = [
        "REDIS_URL",
        "NEO4J_URL",
        "APP_ENV",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "LLM_API_KEY",
    ]
    polluted = [
        k for k in config.DECISION_OPTIMIZER_SETTINGS.keys() if k in pollution_keys
    ]
    assert not polluted, f"DECISION_OPTIMIZER_SETTINGS polluted with: {polluted}"
