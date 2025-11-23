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
        PostgresClient,
        PostgresClientError,
        PostgresClientConnectionError,
        PostgresClientSchemaError,
        PostgresClientQueryError,
        PostgresClientTimeoutError,
        ConnectionError,
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
    from .knowledge_graph_db import (
        Neo4jKnowledgeGraph,
        KnowledgeGraphError,
        ConnectionError as KGConnectionError,
        QueryError as KGQueryError,
        SchemaValidationError as KGSchemaValidationError,
        NodeNotFoundError,
    )

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
        AuditLedgerClient,
        AuditEvent,
        DLTError,
        DLTConnectionError,
        DLTContractError,
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
    from .feature_store_client import (
        FeatureStoreClient,
        ConnectionError as FSConnectionError,
        SchemaValidationError as FSSchemaValidationError,
    )

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
        MetaLearningRecord,
        MetaLearningDataStoreConfig,
        BaseMetaLearningDataStore,
        InMemoryMetaLearningDataStore,
        RedisMetaLearningDataStore,
        get_meta_learning_data_store,
        MetaLearningDataStoreError,
        MetaLearningRecordNotFound,
        MetaLearningRecordValidationError,
        MetaLearningBackendError,
        MetaLearningEncryptionError,
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
        MerkleTree,
        MerkleTreeError,
        MerkleTreeEmptyError,
        MerkleProofError,
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
        ImageAnalysisResult,
        AudioAnalysisResult,
        VideoAnalysisResult,
        ImageOCRResult,
        ImageCaptioningResult,
        AudioTranscriptionResult,
        VideoSummaryResult,
        MultiModalAnalysisResult,
        Sentiment,
        Severity,
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
    logger.debug(f"Models package initialized. Available components: {', '.join(__all__)}")
