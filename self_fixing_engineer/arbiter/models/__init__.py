# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Models package for the Arbiter system.

This package contains data models and client interfaces for:
- PostgreSQL database operations
- Redis cache operations
- Neo4j knowledge graph operations
- Audit ledger (DLT) operations
- Feature store management
- Meta-learning data storage
- Merkle tree implementations
- Multi-modal data schemas
"""

import logging

logger = logging.getLogger(__name__)

__all__ = []

# PostgreSQL Client - Critical for core functionality
try:
    from .postgres_client import (
        ConnectionError,
        PostgresClient,
        PostgresClientConnectionError,
        PostgresClientError,
        PostgresClientQueryError,
        PostgresClientSchemaError,
        PostgresClientTimeoutError,
        QueryError,
        SchemaValidationError,
    )

    __all__.extend(
        [
            "PostgresClient",
            "PostgresClientError",
            "PostgresClientConnectionError",
            "PostgresClientSchemaError",
            "PostgresClientQueryError",
            "PostgresClientTimeoutError",
        ]
    )
except ImportError as e:
    logger.warning(f"Could not import postgres_client: {e}")

# Redis Client
try:
    from .redis_client import RedisClient

    __all__.append("RedisClient")
except ImportError as e:
    logger.warning(f"Could not import redis_client: {e}")

# Neo4j Knowledge Graph
try:
    from .knowledge_graph_db import ConnectionError as KGConnectionError
    from .knowledge_graph_db import (
        KnowledgeGraphError,
        Neo4jKnowledgeGraph,
        NodeNotFoundError,
    )
    from .knowledge_graph_db import QueryError as KGQueryError
    from .knowledge_graph_db import SchemaValidationError as KGSchemaValidationError

    __all__.extend(
        [
            "Neo4jKnowledgeGraph",
            "KnowledgeGraphError",
            "NodeNotFoundError",
        ]
    )
except ImportError as e:
    logger.warning(f"Could not import knowledge_graph_db: {e}")

# Audit Ledger Client
try:
    from .audit_ledger_client import (
        AuditEvent,
        AuditLedgerClient,
        DLTConnectionError,
        DLTContractError,
        DLTError,
        DLTTransactionError,
        DLTUnsupportedError,
    )

    __all__.extend(
        [
            "AuditLedgerClient",
            "AuditEvent",
            "DLTError",
            "DLTConnectionError",
            "DLTContractError",
            "DLTTransactionError",
            "DLTUnsupportedError",
        ]
    )
except ImportError as e:
    logger.warning(f"Could not import audit_ledger_client: {e}")

# Feature Store Client
try:
    from .feature_store_client import ConnectionError as FSConnectionError
    from .feature_store_client import FeatureStoreClient
    from .feature_store_client import SchemaValidationError as FSSchemaValidationError

    __all__.extend(
        [
            "FeatureStoreClient",
        ]
    )
except ImportError as e:
    logger.warning(f"Could not import feature_store_client: {e}")

# Meta Learning Data Store
try:
    from .meta_learning_data_store import (
        BaseMetaLearningDataStore,
        InMemoryMetaLearningDataStore,
        MetaLearningBackendError,
        MetaLearningDataStoreConfig,
        MetaLearningDataStoreError,
        MetaLearningEncryptionError,
        MetaLearningRecord,
        MetaLearningRecordNotFound,
        MetaLearningRecordValidationError,
        RedisMetaLearningDataStore,
        get_meta_learning_data_store,
    )

    __all__.extend(
        [
            "MetaLearningRecord",
            "MetaLearningDataStoreConfig",
            "BaseMetaLearningDataStore",
            "InMemoryMetaLearningDataStore",
            "RedisMetaLearningDataStore",
            "get_meta_learning_data_store",
            "MetaLearningDataStoreError",
            "MetaLearningRecordNotFound",
            "MetaLearningRecordValidationError",
            "MetaLearningBackendError",
            "MetaLearningEncryptionError",
        ]
    )
except ImportError as e:
    logger.warning(f"Could not import meta_learning_data_store: {e}")

# Merkle Tree
try:
    from .merkle_tree import (
        MerkleProofError,
        MerkleTree,
        MerkleTreeEmptyError,
        MerkleTreeError,
    )

    __all__.extend(
        [
            "MerkleTree",
            "MerkleTreeError",
            "MerkleTreeEmptyError",
            "MerkleProofError",
        ]
    )
except ImportError as e:
    logger.warning(f"Could not import merkle_tree: {e}")

# Multi-Modal Schemas
try:
    from .multi_modal_schemas import (
        AudioAnalysisResult,
        AudioTranscriptionResult,
        ImageAnalysisResult,
        ImageCaptioningResult,
        ImageOCRResult,
        MultiModalAnalysisResult,
        Sentiment,
        Severity,
        VideoAnalysisResult,
        VideoSummaryResult,
    )

    __all__.extend(
        [
            "ImageAnalysisResult",
            "AudioAnalysisResult",
            "VideoAnalysisResult",
            "ImageOCRResult",
            "ImageCaptioningResult",
            "AudioTranscriptionResult",
            "VideoSummaryResult",
            "MultiModalAnalysisResult",
            "Sentiment",
            "Severity",
        ]
    )
except ImportError as e:
    logger.warning(f"Could not import multi_modal_schemas: {e}")

# Version info
__version__ = "1.0.0"

# Log summary of what's available
if logger.isEnabledFor(logging.DEBUG):
    logger.debug(
        f"Models package initialized. Available components: {', '.join(__all__)}"
    )
