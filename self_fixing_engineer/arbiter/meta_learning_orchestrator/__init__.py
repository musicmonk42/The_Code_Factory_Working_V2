# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Meta-Learning Orchestrator — distributed training, ingestion, and deployment
of ML models that power the Arbiter's adaptive decision-making.
"""

try:
    from .orchestrator import (
        MetaLearningOrchestrator,
        Ingestor,
        Trainer,
        create_task_with_supervision,
        setup_signal_handlers,
    )
    from .clients import AgentConfigurationService, MLPlatformClient
    from .models import (
        EventType,
        LearningRecord,
        ModelVersion,
        DataIngestionError,
        ModelDeploymentError,
        LeaderElectionError,
    )

    __all__ = [
        "MetaLearningOrchestrator",
        "Ingestor",
        "Trainer",
        "create_task_with_supervision",
        "setup_signal_handlers",
        "MLPlatformClient",
        "AgentConfigurationService",
        "LearningRecord",
        "ModelVersion",
        "EventType",
        "DataIngestionError",
        "ModelDeploymentError",
        "LeaderElectionError",
    ]
except ImportError as e:
    import warnings
    warnings.warn(f"meta_learning_orchestrator not fully available: {e}")
    __all__ = []

