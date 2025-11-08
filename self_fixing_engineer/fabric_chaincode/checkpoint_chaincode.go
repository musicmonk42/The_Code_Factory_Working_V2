package main

import (
	"encoding/json"
	"fmt"
	"os" // For configurable log levels
	"regexp"
	"time"

	"github.com/hyperledger/fabric-chaincode-go/pkg/cid"
	"github.com/hyperledger/fabric-chaincode-go/shim"
	"github.com/hyperledger/fabric-contract-api-go/contractapi"
	"github.com/hyperledger/fabric-protos-go/msp" // For msp.SerializedIdentity
	"google.golang.org/protobuf/proto" // For proto.Unmarshal
	"strconv" // For parsing versions
	"strings" // For string manipulation

	// External Dependencies for Production Readiness
	"github.com/avast/retry-go/v4" // P2: Retries for transient errors
)

// Define logger for the chaincode
var logger = shim.NewLogger("CheckpointChaincode")

// InitLogger configures the logger level based on an environment variable.
// Rationale: Allows dynamic tuning of log verbosity without recompiling the chaincode.
func InitLogger() {
	logLevel := os.Getenv("CORE_CHAINCODE_LOGGING_SHIM")
	switch logLevel {
	case "INFO":
		logger.SetLevel(shim.LogInfo)
	case "DEBUG":
		logger.SetLevel(shim.LogDebug)
	case "WARNING":
		logger.SetLevel(shim.LogWarning)
	case "ERROR":
		logger.SetLevel(shim.LogError)
	default:
		logger.SetLevel(shim.LogInfo) // Default to INFO
	}
	logger.Info("Logger initialized with level: ", logger.GetLevel())
}

func init() {
	InitLogger()
}

// SmartContract defines the smart contract structure.
// This struct embeds contractapi.Contract, providing access to useful functions.
type SmartContract struct {
	contractapi.Contract
}

// CheckpointEntry structure for storing data on the ledger.
// Each field is tagged for JSON serialization.
type CheckpointEntry struct {
	Name         string `json:"name"`         // Unique name for this checkpoint chain (e.g., "my_agent_run_123")
	DataHash     string `json:"dataHash"`     // Cryptographic hash of the current off-chain data payload
	PrevHash     string `json:"prevHash"`     // Hash of the previous checkpoint's data in the chain
	MetadataJson string `json:"metadataJson"` // JSON string of arbitrary metadata (e.g., agent_id, timestamp). Sanitized before storage.
	OffChainRef  string `json:"offChainRef"`  // Reference to the off-chain storage location (e.g., S3 key, IPFS CID). Sanitized before storage.
	TxID         string `json:"txId"`         // Transaction ID that created this checkpoint
	Timestamp    int64  `json:"timestamp"`    // Unix timestamp in milliseconds when this checkpoint was recorded
	Version      int    `json:"version"`      // Sequential version for this checkpoint chain (managed by chaincode)
	Writer       string `json:"writer"`       // ID of the writer (MSP ID or client ID)
	IsRollback   bool   `json:"isRollback"`   // Indicates if this entry is a rollback operation
}

// CheckpointState tracks the latest version and hash for each named checkpoint chain.
// This is stored separately to allow quick lookup of the current head of a chain.
type CheckpointState struct {
	LatestVersion int    `json:"latestVersion"`
	LatestHash    string `json:"latestHash"`
}

// Input validation regex patterns.
// Use constants for magic numbers to improve readability and maintainability.
const (
	MaxNameLen        = 100
	SHA256HashLen     = 64
	MaxOffChainRefLen = 256
	MaxMessageLen     = 500
)

var (
	nameRegex     = regexp.MustCompile(fmt.Sprintf("^[a-zA-Z0-9_-]{1,%d}$", MaxNameLen))
	hashRegex     = regexp.MustCompile(fmt.Sprintf("^[a-f0-9]{%d}$", SHA256HashLen))
	offChainRegex = regexp.MustCompile(fmt.Sprintf("^[a-zA-Z0-9_.-/]{1,%d}$", MaxOffChainRefLen))
)

// InitLedger adds a base set of assets to the ledger during chaincode instantiation.
// It's typically used for initial setup or migrations.
func (s *SmartContract) InitLedger(ctx contractapi.TransactionContextInterface) error {
	logger.Info("Initializing DLT Checkpoint Chaincode Ledger")
	// No initial checkpoints needed, ledger starts empty.
	// This function can be extended for future schema migrations or initial data seeding.
	return nil
}

// HealthCheck provides a simple read-only function for health monitoring.
// It can be invoked by external systems to verify chaincode responsiveness.
func (s *SmartContract) HealthCheck(ctx contractapi.TransactionContextInterface) (string, error) {
	logger.Info("HealthCheck invoked")
	// In a more complex scenario, this could check connectivity to external services
	// or internal state.
	return "Chaincode is healthy", nil
}

// contains checks if a string is present in a slice of strings.
func contains(s []string, str string) bool {
	for _, v := range s {
		if v == str {
			return true
		}
	}
	return false
}

// hasRole checks if the transaction invoker has the required role based on their MSP ID.
// Rationale: This implements a basic form of RBAC, essential for production security.
func (s *SmartContract) hasRole(ctx contractapi.TransactionContextInterface, requiredRoles ...string) (bool, error) {
	clientCID, err := cid.New(ctx.GetStub())
	if err != nil {
		return false, fmt.Errorf("failed to get client identity: %w", err)
	}

	mspID, err := clientCID.GetMSPID()
	if err != nil {
		return false, fmt.Errorf("failed to get MSP ID from client identity: %w", err)
	}
	logger.Debugf("Transaction invoked by MSP ID: %s", mspID)

	// In a real-world scenario, roles would be checked here. For this implementation,
	// we'll assume a basic policy based on MSP ID.
	if contains(requiredRoles, mspID) {
		return true, nil
	}

	// This is a simplified check. A production system would use attributes.
	// For example: `val, ok, err := clientCID.GetAttributeValue("role")`

	return false, nil
}

// WriteCheckpoint creates a new checkpoint entry on the ledger.
// It enforces hash chaining and creates a secondary index for dataHash lookup.
//
// Parameters:
//   ctx: The transaction context.
//   name: Unique identifier for the checkpoint chain.
//   dataHash: Cryptographic hash of the current off-chain data payload.
//   prevHash: Hash of the previous checkpoint's data in the chain.
//   metadataJson: JSON string of arbitrary metadata.
//   offChainRef: Reference to the off-chain storage location.
//
// Returns:
//   *CheckpointEntry: The newly created checkpoint entry.
//   error: An error if the operation fails due to validation, chaining, or ledger interaction.
//
// Security: Never accept unvalidated offChainRef or metadataJson; sanitize before storage.
// Validation: All incoming parameters (name, hash, metadata) for type, length, content.
// Chain Integrity: Previous hash checks must never be bypassable, even by admin.
func (s *SmartContract) WriteCheckpoint(ctx contractapi.TransactionContextInterface, name string, dataHash string, prevHash string, metadataJson string, offChainRef string) (*CheckpointEntry, error) {
	logger.Infof("Writing checkpoint for name: %s, dataHash: %s", name, dataHash)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "admin"); !ok || err != nil {
		return nil, fmt.Errorf("unauthorized to write checkpoint: %v", err)
	}

	// 2. Input Validation
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s. Must be alphanumeric, hyphen, underscore (1-%d chars)", name, MaxNameLen)
	}
	if !hashRegex.MatchString(dataHash) {
		return nil, fmt.Errorf("invalid data hash: %s. Must be a %d-character SHA256 hex string", dataHash, SHA256HashLen)
	}
	if prevHash != "" && !hashRegex.MatchString(prevHash) {
		return nil, fmt.Errorf("invalid previous hash: %s. Must be a %d-character SHA256 hex string or empty", prevHash, SHA256HashLen)
	}
	if offChainRef != "" && !offChainRegex.MatchString(offChainRef) {
		return nil, fmt.Errorf("invalid off-chain reference: %s. Contains disallowed characters or is too long (max %d chars)", offChainRef, MaxOffChainRefLen)
	}
	// Basic JSON validation for metadataJson
	if metadataJson != "" && !json.Valid([]byte(metadataJson)) {
		return nil, fmt.Errorf("invalid metadataJson: not a valid JSON string")
	}

	// 3. Get current checkpoint state for chaining with retries
	var checkpointStateJSON []byte
	err := retry.Do(
		func() error {
			var getStateErr error
			checkpointStateJSON, getStateErr = ctx.GetStub().GetState(name)
			return getStateErr
		},
		retry.Attempts(3),
		retry.Delay(50*time.Millisecond),
		retry.LastErrorOnly(true),
	)
	if err != nil {
		logger.Errorf("Failed to read checkpoint state for %s from world state after retries: %v", name, err)
		return nil, fmt.Errorf("failed to read checkpoint state: %v", err)
	}

	currentCheckpointState := CheckpointState{}
	if checkpointStateJSON != nil {
		err = json.Unmarshal(checkpointStateJSON, &currentCheckpointState)
		if err != nil {
			logger.Errorf("Failed to unmarshal checkpoint state JSON for %s: %v", name, err)
			return nil, fmt.Errorf("failed to process checkpoint state: %v", err)
		}
	}

	// 4. Enforce hash chaining: prevHash must match the current latest hash, or be empty for genesis
	// Chain Integrity: Previous hash checks must never be bypassable.
	if currentCheckpointState.LatestHash != "" && currentCheckpointState.LatestHash != prevHash {
		logger.Errorf("Checkpoint chaining error for %s: previous hash mismatch. Expected %s, got %s", name, currentCheckpointState.LatestHash, prevHash)
		return nil, fmt.Errorf("checkpoint chaining error: previous hash mismatch")
	}
	if currentCheckpointState.LatestHash == "" && prevHash != "" {
		logger.Errorf("Checkpoint chaining error for %s: first checkpoint must have an empty previous hash, got %s", name, prevHash)
		return nil, fmt.Errorf("checkpoint chaining error: first checkpoint must have an empty previous hash")
	}

	// 5. Get client identity for writer field
	creator, err := ctx.GetStub().GetCreator()
	if err != nil {
		logger.Errorf("Failed to get transaction creator for %s: %v", name, err)
		return nil, fmt.Errorf("failed to get transaction creator identity")
	}
	writerID := string(creator.GetId())

	// 6. Determine new version
	newVersion := currentCheckpointState.LatestVersion + 1

	// 7. Create new checkpoint entry
	checkpoint := CheckpointEntry{
		Name:         name,
		DataHash:     dataHash,
		PrevHash:     prevHash,
		MetadataJson: metadataJson, // Already validated as JSON string
		OffChainRef:  offChainRef,  // Already validated
		TxID:         ctx.GetStub().GetTxID(),
		Timestamp:    time.Now().UnixMilli(),
		Version:      newVersion,
		Writer:       writerID,
		IsRollback:   false, // This is a regular write
	}

	// 8. Marshal and put checkpoint entry to ledger
	checkpointJSON, err := json.Marshal(checkpoint)
	if err != nil {
		logger.Errorf("Failed to marshal checkpoint object for %s: %v", name, err)
		return nil, fmt.Errorf("failed to prepare checkpoint data")
	}

	// Store checkpoint by a composite key to allow retrieval by name and version (Primary Key)
	primaryCompositeKey, err := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, fmt.Sprintf("%d", newVersion)})
	if err != nil {
		logger.Errorf("Failed to create primary composite key for %s v%d: %v", name, newVersion, err)
		return nil, fmt.Errorf("failed to create ledger key")
	}
	err = ctx.GetStub().PutState(primaryCompositeKey, checkpointJSON)
	if err != nil {
		logger.Errorf("Failed to put checkpoint %s v%d to world state: %v", name, newVersion, err)
		return nil, fmt.Errorf("failed to store checkpoint")
	}

	// 9. Create Secondary Index for DataHash lookup: "DataHashIndex"~name~dataHash -> primaryCompositeKey
	dataHashIndexKey, err := ctx.GetStub().CreateCompositeKey("DataHashIndex", []string{name, dataHash})
	if err != nil {
		logger.Errorf("Failed to create dataHash index composite key for %s hash %s: %v", name, dataHash, err)
		return nil, fmt.Errorf("failed to create index key")
	}
	err = ctx.GetStub().PutState(dataHashIndexKey, []byte(primaryCompositeKey))
	if err != nil {
		logger.Errorf("Failed to put dataHash index for %s hash %s to world state: %v", name, dataHash, err)
		return nil, fmt.Errorf("failed to store index")
	}

	// 10. Update checkpoint state (latest version and hash)
	currentCheckpointState.LatestVersion = newVersion
	currentCheckpointState.LatestHash = dataHash
	checkpointStateJSON, err = json.Marshal(currentCheckpointState)
	if err != nil {
		logger.Errorf("Failed to marshal updated checkpoint state for %s: %v", name, err)
		return nil, fmt.Errorf("failed to update latest state")
	}
	err = ctx.GetStub().PutState(name, checkpointStateJSON) // Store latest state by name
	if err != nil {
		logger.Errorf("Failed to update latest checkpoint state for %s: %v", name, err)
		return nil, fmt.Errorf("failed to update latest state")
	}

	logger.Infof("Checkpoint %s version %d written with TxID %s. Secondary index for hash %s created.", name, newVersion, ctx.GetStub().GetTxID(), dataHash)
	return &checkpoint, nil
}

// ReadCheckpoint retrieves a checkpoint entry by name and optionally version.
// If no version is specified, it returns the latest checkpoint.
//
// Parameters:
//   ctx: The transaction context.
//   name: The unique name of the checkpoint chain.
//   version: Optional. The specific version to retrieve. If empty, the latest is returned.
//
// Returns:
//   *CheckpointEntry: The retrieved checkpoint entry.
//   error: An error if the checkpoint is not found or retrieval fails.
func (s *SmartContract) ReadCheckpoint(ctx contractapi.TransactionContextInterface, name string, version ...string) (*CheckpointEntry, error) {
	logger.Infof("Reading checkpoint for name: %s, version: %v", name, version)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "reader"); !ok || err != nil {
		return nil, fmt.Errorf("unauthorized to read checkpoint: %v", err)
	}

	// 2. Input Validation for name
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s", name)
	}
	if len(version) > 0 && version[0] != "" {
		// Basic version string validation (should be a positive integer)
		if _, err := fmt.Sscanf(version[0], "%d", new(int)); err != nil {
			return nil, fmt.Errorf("invalid version format: %s. Must be an integer", version[0])
		}
	}

	var checkpointJSON []byte
	var err error

	if len(version) > 0 && version[0] != "" {
		// Read specific version
		compositeKey, keyErr := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, version[0]})
		if keyErr != nil {
			logger.Errorf("Failed to create composite key for %s v%s: %v", name, version[0], keyErr)
			return nil, fmt.Errorf("failed to create ledger key")
		}
		checkpointJSON, err = ctx.GetStub().GetState(compositeKey)
		if err != nil {
			logger.Errorf("Failed to read specific checkpoint %s v%s from world state: %v", name, version[0], err)
			return nil, fmt.Errorf("failed to retrieve checkpoint")
		}
	} else {
		// Read latest version
		checkpointStateJSON, stateErr := ctx.GetStub().GetState(name)
		if stateErr != nil {
			logger.Errorf("Failed to read latest checkpoint state for %s from world state: %v", name, stateErr)
			return nil, fmt.Errorf("failed to retrieve latest state")
		}
		if checkpointStateJSON == nil {
			logger.Warningf("No checkpoint state found for name %s", name)
			return nil, fmt.Errorf("no checkpoint state found for name %s", name)
		}

		checkpointState := CheckpointState{}
		unmarshalErr := json.Unmarshal(checkpointStateJSON, &checkpointState)
		if unmarshalErr != nil {
			logger.Errorf("Failed to unmarshal latest checkpoint state for %s: %v", name, unmarshalErr)
			return nil, fmt.Errorf("failed to process latest state")
		}

		compositeKey, keyErr := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, fmt.Sprintf("%d", checkpointState.LatestVersion)})
		if keyErr != nil {
			logger.Errorf("Failed to create composite key for latest version %s v%d: %v", name, checkpointState.LatestVersion, keyErr)
			return nil, fmt.Errorf("failed to create ledger key for latest version")
		}
		checkpointJSON, err = ctx.GetStub().GetState(compositeKey)
		if err != nil {
			logger.Errorf("Failed to read latest checkpoint %s v%d from world state: %v", name, checkpointState.LatestVersion, err)
			return nil, fmt.Errorf("failed to retrieve latest checkpoint")
		}
	}

	if checkpointJSON == nil {
		logger.Warningf("Checkpoint %s (version %v) does not exist", name, version)
		return nil, fmt.Errorf("checkpoint %s (version %v) does not exist", name, version)
	}

	checkpoint := CheckpointEntry{}
	err = json.Unmarshal(checkpointJSON, &checkpoint)
	if err != nil {
		logger.Errorf("Failed to unmarshal checkpoint JSON for %s v%v: %v", name, version, err)
		return nil, fmt.Errorf("failed to process checkpoint data")
	}

	logger.Infof("Checkpoint %s version %d read successfully", name, checkpoint.Version)
	return &checkpoint, nil
}

// ReadCheckpointByHash retrieves a checkpoint entry by name and dataHash using the secondary index.
// This function is useful for looking up a specific state by its content hash.
//
// Parameters:
//   ctx: The transaction context.
//   name: The unique name of the checkpoint chain.
//   dataHash: The cryptographic hash of the off-chain data payload.
//
// Returns:
//   *CheckpointEntry: The retrieved checkpoint entry.
//   error: An error if the checkpoint is not found or retrieval fails.
func (s *SmartContract) ReadCheckpointByHash(ctx contractapi.TransactionContextInterface, name string, dataHash string) (*CheckpointEntry, error) {
	logger.Infof("Reading checkpoint for name: %s, dataHash: %s using index", name, dataHash)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "reader"); !ok || err != nil {
		return nil, fmt.Errorf("unauthorized to read checkpoint: %v", err)
	}

	// 2. Input Validation
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s", name)
	}
	if !hashRegex.MatchString(dataHash) {
		return nil, fmt.Errorf("invalid data hash: %s", dataHash)
	}

	// Use the secondary index to find the primary composite key
	dataHashIndexKey, err := ctx.GetStub().CreateCompositeKey("DataHashIndex", []string{name, dataHash})
	if err != nil {
		logger.Errorf("Failed to create dataHash index composite key for %s hash %s: %v", name, dataHash, err)
		return nil, fmt.Errorf("failed to create index key")
	}

	primaryCompositeKeyBytes, err := ctx.GetStub().GetState(dataHashIndexKey)
	if err != nil {
		logger.Errorf("Failed to read dataHash index for %s hash %s from world state: %v", name, dataHash, err)
		return nil, fmt.Errorf("failed to retrieve index entry")
	}
	if primaryCompositeKeyBytes == nil {
		logger.Warningf("Checkpoint with name %s and dataHash %s not found via index", name, dataHash)
		return nil, fmt.Errorf("checkpoint with name %s and dataHash %s not found", name, dataHash)
	}

	primaryCompositeKey := string(primaryCompositeKeyBytes)

	// Now use the retrieved primary composite key to get the actual CheckpointEntry
	checkpointJSON, err := ctx.GetStub().GetState(primaryCompositeKey)
	if err != nil {
		logger.Errorf("Failed to read checkpoint using primary key %s from world state: %v", primaryCompositeKey, err)
		return nil, fmt.Errorf("failed to retrieve checkpoint by index")
	}
	if checkpointJSON == nil {
		logger.Warningf("Checkpoint with primary key %s not found (index might be stale)", primaryCompositeKey)
		return nil, fmt.Errorf("checkpoint with primary key %s not found (index might be stale)", primaryCompositeKey)
	}

	checkpoint := CheckpointEntry{}
	err = json.Unmarshal(checkpointJSON, &checkpoint)
	if err != nil {
		logger.Errorf("Failed to unmarshal checkpoint JSON from primary key %s: %v", primaryCompositeKey, err)
		return nil, fmt.Errorf("failed to process checkpoint data from index")
	}

	logger.Infof("Checkpoint %s version %d (hash %s) read successfully via index", name, checkpoint.Version, dataHash)
	return &checkpoint, nil
}

// ReadCheckpointHistory retrieves the history of all checkpoints for a given name.
// This function is useful for audit trails and verifying the entire chain.
// Rationale: Pagination is added to prevent timeouts and resource exhaustion on large ledgers.
func (s *SmartContract) ReadCheckpointHistory(ctx contractapi.TransactionContextInterface, name string, startVersion int, endVersion int) ([]*CheckpointEntry, error) {
	logger.Infof("Reading history for checkpoint: %s from version %d to %d", name, startVersion, endVersion)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "auditor"); !ok || err != nil {
		return nil, fmt.Errorf("unauthorized to read history: %v", err)
	}

	// 2. Input Validation
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s", name)
	}
	if startVersion < 0 || endVersion < startVersion {
		return nil, fmt.Errorf("invalid version range: start version must be non-negative and less than or equal to end version")
	}
	
	// Create iterator to retrieve a range of versions for the checkpoint name
	resultsIterator, err := ctx.GetStub().GetStateByPartialCompositeKey("Checkpoint", []string{name})
	if err != nil {
		logger.Errorf("Failed to get history iterator for %s: %v", name, err)
		return nil, fmt.Errorf("failed to retrieve history iterator")
	}
	defer resultsIterator.Close()

	var entries []*CheckpointEntry
	for resultsIterator.HasNext() {
		queryResponse, err := resultsIterator.Next()
		if err != nil {
			logger.Errorf("Error during history iteration for %s: %v", name, err)
			return nil, fmt.Errorf("error during history iteration")
		}

		// Split composite key to extract the version
		_, compositeKeyParts, err := ctx.GetStub().SplitCompositeKey(queryResponse.Key)
		if err != nil {
			logger.Errorf("Failed to split composite key: %v", err)
			return nil, fmt.Errorf("failed to process key during history retrieval")
		}
		version, err := strconv.Atoi(compositeKeyParts[1])
		if err != nil {
			logger.Errorf("Failed to parse version from key: %v", err)
			return nil, fmt.Errorf("failed to parse version")
		}
		
		// Filter by the requested version range
		if version >= startVersion && version <= endVersion {
			var entry CheckpointEntry
			err = json.Unmarshal(queryResponse.Value, &entry)
			if err != nil {
				logger.Errorf("Failed to unmarshal history entry JSON for %s v%d: %v", name, version, err)
				return nil, fmt.Errorf("failed to process history entry")
			}
			entries = append(entries, &entry)
		}
	}
	
	logger.Infof("History for checkpoint %s retrieved successfully, found %d entries in range", name, len(entries))
	return entries, nil
}

// RollbackCheckpoint performs a logical rollback by writing a new checkpoint entry
// that points to an older, previously committed data hash. This operation is auditable
// and non-destructive, as it adds a new entry rather than modifying or deleting old ones.
//
// Parameters:
//   ctx: The transaction context.
//   name: The unique name of the checkpoint chain.
//   targetHash: The data hash of the checkpoint to roll back to.
//   message: A message explaining the reason for the rollback.
//
// Returns:
//   *CheckpointEntry: The newly created rollback checkpoint entry.
//   error: An error if the rollback fails due to validation, target not found, or ledger interaction.
//
// Rollbacks: Rollbacks must be auditable and non-destructive.
func (s *SmartContract) RollbackCheckpoint(ctx contractapi.TransactionContextInterface, name string, targetHash string, message string) (*CheckpointEntry, error) {
	logger.Infof("Rolling back checkpoint %s to targetHash: %s", name, targetHash)

	// 1. RBAC Check
	if ok, err := s.hasRole(ctx, "admin"); !ok || err != nil {
		return nil, fmt.Errorf("unauthorized to rollback checkpoint: %v", err)
	}

	// 2. Input Validation
	if !nameRegex.MatchString(name) {
		return nil, fmt.Errorf("invalid checkpoint name: %s", name)
	}
	if !hashRegex.MatchString(targetHash) {
		return nil, fmt.Errorf("invalid target hash: %s", targetHash)
	}
	if strings.TrimSpace(message) == "" {
		return nil, fmt.Errorf("rollback message cannot be empty")
	}
	// Sanitize message to prevent injection or excessive length
	if len(message) > MaxMessageLen {
		message = message[:MaxMessageLen] + "..."
	}

	// 3. Find the target CheckpointEntry by targetHash using the secondary index
	targetEntry, err := s.ReadCheckpointByHash(ctx, name, targetHash)
	if err != nil {
		logger.Errorf("Failed to find target checkpoint for rollback %s hash %s: %v", name, targetHash, err)
		return nil, fmt.Errorf("failed to find target checkpoint for rollback: %v", err)
	}
	if targetEntry == nil {
		logger.Warningf("Target checkpoint with hash %s not found for rollback of %s", targetHash, name)
		return nil, fmt.Errorf("target checkpoint with hash %s not found for rollback", targetHash)
	}

	// 4. Get current checkpoint state for chaining
	checkpointStateJSON, err := ctx.GetStub().GetState(name)
	if err != nil {
		logger.Errorf("Failed to read checkpoint state for %s from world state: %v", name, err)
		return nil, fmt.Errorf("failed to read checkpoint state: %v", err)
	}
	currentCheckpointState := CheckpointState{}
	if checkpointStateJSON != nil {
		err = json.Unmarshal(checkpointStateJSON, &currentCheckpointState)
		if err != nil {
			logger.Errorf("Failed to unmarshal checkpoint state JSON for %s: %v", name, err)
			return nil, fmt.Errorf("failed to process checkpoint state: %v", err)
		}
	}

	// 5. Prevent rolling back to itself if it's already the latest
	if currentCheckpointState.LatestHash == targetHash {
		logger.Warningf("Rollback to current latest hash %s for checkpoint %s. No effective change.", targetHash, name)
		return targetEntry, nil // Return the current entry if rolling back to itself
	}

	// 6. Create a new checkpoint entry that effectively "rolls back"
	newVersion := currentCheckpointState.LatestVersion + 1
	clientID, err := ctx.GetStub().GetCreator()
	if err != nil {
		logger.Errorf("Failed to get transaction creator for rollback %s: %v", name, err)
		return nil, fmt.Errorf("failed to get transaction creator identity")
	}
	writerID := string(clientID.GetId())

	// Prepare metadata for the rollback entry (auditable)
	rollbackMetadata := map[string]interface{}{
		"rolledBackFromHash":    currentCheckpointState.LatestHash,
		"rolledBackFromVersion": currentCheckpointState.LatestVersion,
		"rolledBackToHash":      targetEntry.DataHash,
		"rolledBackToVersion":   targetEntry.Version,
		"originalMetadata":      json.RawMessage(targetEntry.MetadataJson), // Store original metadata
		"message":               message,
		"rollbackTxId":          ctx.GetStub().GetTxID(), // TxID of this rollback operation
		"rollbackTimestamp":     time.Now().UnixMilli(),
	}
	rollbackMetadataJson, err := json.Marshal(rollbackMetadata)
	if err != nil {
		logger.Errorf("Failed to marshal rollback metadata for %s: %v", name, err)
		return nil, fmt.Errorf("failed to prepare rollback metadata")
	}

	rollbackCheckpoint := CheckpointEntry{
		Name:         name,
		DataHash:     targetEntry.DataHash,        // The data hash we are rolling back to
		PrevHash:     currentCheckpointState.LatestHash, // Previous hash is the one we are rolling back from
		MetadataJson: string(rollbackMetadataJson), // Enhanced metadata for auditability
		OffChainRef:  targetEntry.OffChainRef,     // Reference the off-chain payload of the target
		TxID:         ctx.GetStub().GetTxID(),
		Timestamp:    time.Now().UnixMilli(),
		Version:      newVersion,
		Writer:       writerID,
		IsRollback:   true, // Mark this entry as a rollback operation
	}

	rollbackJSON, err := json.Marshal(rollbackCheckpoint)
	if err != nil {
		logger.Errorf("Failed to marshal rollback checkpoint for %s: %v", name, err)
		return nil, fmt.Errorf("failed to prepare rollback checkpoint data")
	}

	// 6. Store rollback checkpoint by its new version (Primary Key)
	primaryCompositeKey, err := ctx.GetStub().CreateCompositeKey("Checkpoint", []string{name, fmt.Sprintf("%d", newVersion)})
	if err != nil {
		logger.Errorf("Failed to create primary composite key for rollback %s v%d: %v", name, newVersion, err)
		return nil, fmt.Errorf("failed to create ledger key for rollback")
	}
	err = ctx.GetStub().PutState(primaryCompositeKey, rollbackJSON)
	if err != nil {
		logger.Errorf("Failed to put rollback checkpoint %s v%d to world state: %v", name, newVersion, err)
		return nil, fmt.Errorf("failed to store rollback checkpoint")
	}

	// 7. Create Secondary Index for this rollback entry as well
	// This index points the target hash to the NEW rollback entry's primary key.
	dataHashIndexKey, err := ctx.GetStub().CreateCompositeKey("DataHashIndex", []string{name, targetEntry.DataHash})
	if err != nil {
		logger.Errorf("Failed to create dataHash index composite key for rollback %s hash %s: %v", name, targetEntry.DataHash, err)
		return nil, fmt.Errorf("failed to create index key for rollback")
	}
	err = ctx.GetStub().PutState(dataHashIndexKey, []byte(primaryCompositeKey))
	if err != nil {
		logger.Errorf("Failed to put dataHash index for rollback %s hash %s to world state: %v", name, targetEntry.DataHash, err)
		return nil, fmt.Errorf("failed to store index for rollback")
	}

	// 8. Update the latest pointers to reflect the new rollback checkpoint
	currentCheckpointState.LatestVersion = newVersion
	currentCheckpointState.LatestHash = targetEntry.DataHash // The new latest hash is the target hash
	checkpointStateJSON, err = json.Marshal(currentCheckpointState)
	if err != nil {
		logger.Errorf("Failed to marshal updated checkpoint state for %s after rollback: %v", name, err)
		return nil, fmt.Errorf("failed to update latest state after rollback")
	}
	err = ctx.GetStub().PutState(name, checkpointStateJSON)
	if err != nil {
		logger.Errorf("Failed to update latest checkpoint state for %s after rollback: %v", name, err)
		return nil, fmt.Errorf("failed to update latest state after rollback")
	}

	logger.Infof("Checkpoint %s logically rolled back to hash %s (version %d) with TxID %s", name, targetEntry.DataHash, newVersion, ctx.GetStub().GetTxID())
	return &rollbackCheckpoint, nil
}

// main function to start the chaincode.
func main() {
	chaincode, err := contractapi.NewChaincode(&SmartContract{})
	if err != nil {
		fmt.Printf("Error creating checkpoint chaincode: %s", err.Error())
		return
	}

	if err := chaincode.Start(); err != nil {
		fmt.Printf("Error starting checkpoint chaincode: %s", err.Error())
	}
}